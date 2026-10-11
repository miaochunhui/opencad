#!/usr/bin/env python3
"""Freeze and copy the CAD-only source closure without replacing any file.

Run as infra. ``freeze`` records immutable source bytes in an external plan;
``apply`` rechecks them, creates only absent destinations with O_EXCL, and
checks every copied byte again. Unrelated destination files are allowed.
No cleanup, retries, source edits, permission changes or service operations.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

OSS = "oss-alternatives/"
TREES = (
    OSS + "apps/opencad",
    OSS + "packages/cadcore",
    OSS + "packages/dxf",
)
EXACT = (
    OSS + "packages/vault/freechat_vault/__init__.py",
    OSS + "packages/vault/freechat_vault/store.py",
    OSS + "packages/vault/freechat_vault/fcx.py",
    OSS + "tools/cad/ui_gate.py",
    OSS + "tools/cad/i18n_gate.py",
    OSS + "tools/cad/i18n_newlang_gate.py",
    OSS + "tools/cad/parity_output_gate.py",
    OSS + "tools/cad/param_boundary_gate.py",
    OSS + "tools/cad3d/browser_render.py",
    OSS + "tools/cad3d/web3d_browser_check.py",
    OSS + "tools/cad3d/viewer_controls_check.py",
    OSS + "tools/cad3d/advanced_browser_check.py",
    OSS + "tools/cad3d/opencad_acceptance.py",
    OSS + "tools/cad3d/numeric-gate-kernel.py",
    OSS + "docs/design/opencad-web-product-scope.md",
    OSS + "docs/research/cad-parity/features/autocad-output.json",
    OSS + "license-manifest.json",
)
DENIED_PARTS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", "build", "dist",
    "target", "packaging", "docs", "artifacts", "results", "evidence",
}
ALLOWED_SUFFIXES = {
    ".py", ".json", ".html", ".css", ".js", ".dxf", ".step", ".stp",
    ".txt", ".l1", ".csv", ".svg",
}
PROVENANCE = "tools/bootstrap-source-manifest.json"
SUPPLEMENT_PROVENANCE = "tools/bootstrap-supplement-manifest.json"
SUPPLEMENT_FILES = (OSS + "docs/research/cad-parity/features/autocad-output.json",)
OWNED_FILES = (
    ".gitignore", "AGENTS.md", "README.md", "LICENSE", "NOTICE",
    "tools/bootstrap_from_infra.py", "tools/acceptance.py",
    "tools/test_bootstrap.py", PROVENANCE,
)
CHANGE_FILES = {
    "README.md", "tools/bootstrap_from_infra.py", "tools/acceptance.py",
    "tools/test_bootstrap.py", SUPPLEMENT_PROVENANCE, *SUPPLEMENT_FILES,
    OSS + "apps/opencad/server.py",
    OSS + "apps/opencad/tests/test_advanced_ui_review.py",
    OSS + "packages/cadcore/tests/test_edit_reach_contract.py",
    OSS + "packages/cadcore/tests/test_partials_batch3_ops.py",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:password|passwd|api[_-]?key|client[_-]?secret)"
               rb"""["']?\s*[:=]\s*["'][^"'\r\n]{6,}["']"""),
    re.compile(rb"(?i)IDENTIFIED\s+BY\s+['\"]"),
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: object) -> str:
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checked_relative(name: str) -> Path:
    value = PurePosixPath(name)
    if value.is_absolute() or not value.parts or ".." in value.parts or str(value) != name:
        raise ValueError(f"unsafe relative path: {name!r}")
    return Path(name)


def regular_bytes(path: Path) -> bytes:
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"not a regular source file: {path}")
    return path.read_bytes()


def exclusive_bytes(path: Path, value: bytes) -> None:
    """Do not replace even a dangling link, or traverse a linked parent."""
    for parent in (path.parent, *path.parents):
        if parent.is_symlink():
            raise ValueError(f"symlink destination parent: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"non-directory destination parent: {parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def exclusive_json(path: Path, value: object) -> None:
    exclusive_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def scan_bytes(name: str, value: bytes) -> None:
    # Report coordinates only; never echo the matching secret.
    if b"\x00" in value:
        raise ValueError(f"binary input not admitted: {name}")
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        raise ValueError(f"credential-shaped literal: {name}")


def selected(source: Path) -> tuple[list[str], list[dict]]:
    names = set(EXACT)
    excluded = []
    for relative in TREES:
        tree = source / relative
        if not tree.is_dir() or tree.is_symlink():
            raise ValueError(f"required source tree absent/linked: {tree}")
        for directory, dirs, files in os.walk(tree, followlinks=False):
            parent = Path(directory)
            for item in list(dirs):
                path = parent / item
                if path.is_symlink():
                    raise ValueError(f"source directory symlink: {path}")
                if item in DENIED_PARTS or item.startswith("."):
                    dirs.remove(item)
                    excluded.append({"path": path.relative_to(source).as_posix(),
                                     "reason": "generated/desktop/evidence directory"})
            for item in files:
                path = parent / item
                name = path.relative_to(source).as_posix()
                if (path.suffix in ALLOWED_SUFFIXES or item == "README.md") and not item.startswith("."):
                    names.add(name)
                else:
                    excluded.append({"path": name, "reason": "not source/fixture allowlist"})
    return sorted(names), excluded


def import_inventory(source: Path, names: list[str]) -> dict:
    """Audit syntax and record imports, including literal dynamic imports.

    Relative imports and CAD sibling package names are recorded without
    importing code during migration. Runtime origin binding is an acceptance
    step. Nonliteral imports remain explicitly dynamic, not claimed resolved.
    """
    imports = {}
    dynamic = {}
    for name in names:
        if not name.endswith(".py"):
            continue
        tree = ast.parse(regular_bytes(source / name), filename=name)
        found = set()
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Call):
                function = node.func
                label = (function.attr if isinstance(function, ast.Attribute)
                         else function.id if isinstance(function, ast.Name) else "")
                if label in {"import_module", "__import__", "spec_from_file_location"}:
                    calls.append({"line": node.lineno, "call": ast.unparse(node)})
        imports[name] = sorted(found)
        if calls:
            dynamic[name] = calls
    return {"static": imports, "dynamic": dynamic,
            "mathcore": "No CAD runtime import; not migrated.",
            "vault": "cadcore.store -> freechat_vault.store/fcx; three source files only.",
            "legacy_debt": [
                "test_edit_reach_contract and test_partials_batch3_ops read historical cad-parity/status evidence; not migrated.",
                "test_no_source_tree_pollution/test_b6_round2_secondary_writers read excluded ops/oss-alt-web or desktop build tools.",
                "Old cad3d acceptance gates/numeric orchestration reference wider infra tools; use tools/acceptance.py.",
                "Desktop packaging and full historical Web profile are not an independent repository release gate.",
            ]}


def freeze(source: Path, plan_path: Path, *, supplement: bool = False) -> dict:
    names, excluded = (list(SUPPLEMENT_FILES), []) if supplement else selected(source)
    manifest = {}
    for name in names:
        value = regular_bytes(source / name)
        scan_bytes(name, value)
        manifest[name] = {"sha256": sha_bytes(value), "bytes": len(value)}
    license_source = Path("/usr/share/common-licenses/Apache-2.0")
    value = regular_bytes(license_source)
    result = {
        "schema": 1, "kind": "supplement" if supplement else "baseline",
        "created_at": now(), "source_root": str(source.resolve()),
        "source_head": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "files": manifest, "files_sha256": digest(manifest), "excluded": excluded,
        "imports": import_inventory(source, names),
        "license": {"source": str(license_source), "sha256": sha_bytes(value),
                    "bytes": len(value)},
        "source_state": "Current working-tree bytes, not an infra HEAD-only snapshot.",
    }
    exclusive_json(plan_path.resolve(), result)
    return result


def validate_plan(plan: dict) -> None:
    if plan.get("schema") != 1 or not plan.get("files"):
        raise ValueError("empty/unsupported source plan")
    if digest(plan["files"]) != plan.get("files_sha256"):
        raise ValueError("source plan digest mismatch")
    for name, record in plan["files"].items():
        checked_relative(name)
        if plan.get("kind") == "supplement" and name not in SUPPLEMENT_FILES:
            raise ValueError(f"unapproved supplement path: {name}")
        if not (name in EXACT or any(name.startswith(tree + "/") for tree in TREES)):
            raise ValueError(f"plan path outside CAD allowlist: {name}")
        parts = checked_relative(name).parts[3:]
        if name not in EXACT and any(part in DENIED_PARTS or part.startswith(".") for part in parts):
            raise ValueError(f"plan path in excluded directory: {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", "")):
            raise ValueError(f"invalid file SHA: {name}")


def verify_files(root: Path, files: dict) -> list[str]:
    changed = []
    for name, record in files.items():
        path = root / checked_relative(name)
        if not path.is_file() or path.is_symlink():
            changed.append(name)
        else:
            value = regular_bytes(path)
            if sha_bytes(value) != record["sha256"] or len(value) != record["bytes"]:
                changed.append(name)
    return changed


def apply(plan: dict, destination: Path, evidence: Path) -> dict:
    validate_plan(plan)
    source = Path(plan["source_root"])
    if source.resolve() == destination.resolve() or source.resolve() in destination.resolve().parents:
        raise ValueError("destination overlaps source")
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("destination must be an existing non-linked directory")
    supplement = plan.get("kind") == "supplement"
    provenance = SUPPLEMENT_PROVENANCE if supplement else PROVENANCE
    targets = [destination / name for name in plan["files"]]
    targets += [destination / provenance]
    if not supplement:
        targets.append(destination / "LICENSE")
    collisions = [str(path.relative_to(destination)) for path in targets
                  if os.path.lexists(path)]
    if collisions:
        raise FileExistsError("existing migration targets: " + ", ".join(collisions))
    drift = verify_files(source, plan["files"])
    if drift:
        raise ValueError("source changed after freeze: " + ", ".join(drift))
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    copied = []
    try:
        exclusive_json(evidence / "frozen-plan.json", plan)
        for name, record in plan["files"].items():
            value = regular_bytes(source / name)
            scan_bytes(name, value)
            if sha_bytes(value) != record["sha256"]:
                raise ValueError(f"source changed while copying: {name}")
            exclusive_bytes(destination / name, value)
            copied.append(name)
        if not supplement:
            license_value = regular_bytes(Path(plan["license"]["source"]))
            if sha_bytes(license_value) != plan["license"]["sha256"]:
                raise ValueError("Apache license source changed")
            exclusive_bytes(destination / "LICENSE", license_value)
        source_drift = verify_files(source, plan["files"])
        destination_drift = verify_files(destination, plan["files"])
        if source_drift or destination_drift:
            raise ValueError(f"post-copy drift source={source_drift} target={destination_drift}")
        exclusive_json(destination / provenance, plan)
        result = {"verdict": "PASS", "at": now(), "copied": len(copied),
                  "files_sha256": plan["files_sha256"],
                  "source_drift": source_drift, "destination_drift": destination_drift}
        exclusive_json(evidence / "result.json", result)
        return result
    except BaseException as exc:
        try:
            exclusive_json(evidence / "failure.json", {
                "verdict": "FAIL", "at": now(), "error_type": type(exc).__name__,
                "detail": str(exc), "copied": copied,
                "recovery": "Keep partial files; no automatic overwrite or cleanup."})
        except Exception as evidence_error:
            print(f"BOOTSTRAP failure evidence unavailable: {evidence_error}", file=sys.stderr)
        raise


def commit_baseline(plan: dict, target: Path, evidence: Path) -> str:
    """Point-name the frozen migration and owned tools; never stage chair files."""
    validate_plan(plan)
    drift = verify_files(target, plan["files"])
    if drift:
        raise ValueError("cannot commit changed migration files: " + ", ".join(drift))
    paths = sorted(set(plan["files"]) | set(OWNED_FILES))
    snapshot = {}
    for name in paths:
        value = regular_bytes(target / name)
        scan_bytes(name, value)
        snapshot[name] = sha_bytes(value)
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    exclusive_json(evidence / "owned-paths.json", snapshot)
    subprocess.run(["git", "add", "--", *paths], cwd=target, check=True)
    whitespace = subprocess.run(["git", "diff", "--cached", "--check", "--", *paths],
                                cwd=target, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exclusive_bytes(evidence / "original-whitespace-check.txt", whitespace.stdout)
    if whitespace.returncode not in (0, 2):
        raise RuntimeError(f"git whitespace inspection failed rc={whitespace.returncode}")
    # Preserve frozen origin bytes, including legacy whitespace. New tools
    # still must pass strictly; this is not a permission to bypass Git hooks.
    subprocess.run(["git", "diff", "--cached", "--check", "--", *OWNED_FILES],
                   cwd=target, check=True)
    summary = subprocess.check_output(
        ["git", "diff", "--cached", "--stat", "--", *paths], cwd=target)
    exclusive_bytes(evidence / "staged-stat.txt", summary)
    # Explicit pathspec commit leaves other seats' index entries untouched.
    process = subprocess.run(
        ["git", "commit", "-s", "-m", "Bootstrap independent Web-only OpenCAD source and regression entry", "--", *paths],
        cwd=target, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exclusive_bytes(evidence / "commit.log", process.stdout)
    if process.returncode:
        raise RuntimeError(f"git commit failed rc={process.returncode}; see {evidence / 'commit.log'}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    committed = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=target, text=True).splitlines()
    if set(committed) != set(paths):
        raise ValueError("initial commit file set differs from owned pathspec")
    for name, expected in snapshot.items():
        value = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=target)
        if sha_bytes(value) != expected:
            raise ValueError(f"committed source binding mismatch: {name}")
    message = subprocess.check_output(["git", "log", "-1", "--format=%B", commit], cwd=target, text=True)
    if "Signed-off-by:" not in message:
        raise ValueError("DCO sign-off missing")
    exclusive_json(evidence / "result.json", {
        "verdict": "PASS", "at": now(), "commit": commit, "paths": len(paths),
        "committed_bytes_verified": True, "push": "NOT_PERFORMED"})
    return commit


def commit_changes(target: Path, evidence: Path, acceptance: Path) -> str:
    """DCO commit only this seat's explicit paths after real all acceptance."""
    report = json.loads(regular_bytes(acceptance / "result.json"))
    if (report["verdict"] != "PASS" or report["stage"] != "all"
            or report.get("source_drift") != []
            or [step["label"] for step in report["steps"]] != [
                "bootstrap-negative-controls", "http-selftest", "cadcore", "web", "dxf"]):
        raise ValueError("complete all acceptance with zero drift required")
    for step in report["steps"]:
        log = regular_bytes(acceptance / (step["label"] + ".log"))
        if step["rc"] != 0 or sha_bytes(log) != step["log_sha256"]:
            raise ValueError(f"unverified acceptance log: {step['label']}")
        if step["label"] != "http-selftest" and not step["counts"].get("counts_reconciled"):
            raise ValueError(f"unreconciled acceptance counts: {step['label']}")
    # Import here only: acceptance imports bootstrap for the shared byte scanner.
    from acceptance import source_manifest
    if digest(source_manifest(target)) != report["source_sha256"]:
        raise ValueError("source changed since accepted all run")
    paths = sorted(CHANGE_FILES)
    snapshot = {name: sha_bytes(regular_bytes(target / name)) for name in paths}
    for name in paths:
        scan_bytes(name, regular_bytes(target / name))
    evidence.mkdir(parents=True, exist_ok=False)
    exclusive_json(evidence / "owned-paths.json", snapshot)
    subprocess.run(["git", "add", "--", *paths], cwd=target, check=True)
    check = subprocess.run(["git", "diff", "--cached", "--check", "--", *paths],
                           cwd=target, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exclusive_bytes(evidence / "whitespace-check.log", check.stdout)
    if check.returncode:
        raise RuntimeError(f"staged whitespace check failed rc={check.returncode}")
    diff = subprocess.check_output(["git", "diff", "--cached", "--", *paths], cwd=target)
    exclusive_bytes(evidence / "staged.diff", diff)
    process = subprocess.run(["git", "commit", "-s", "-m",
        "Decouple independent CAD regression and preserve local SQLite test runtime",
        "--", *paths], cwd=target, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    exclusive_bytes(evidence / "commit.log", process.stdout)
    if process.returncode:
        raise RuntimeError(f"DCO commit failed rc={process.returncode}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    changed = subprocess.check_output(["git", "diff-tree", "--no-commit-id", "--name-only",
                                      "-r", commit], cwd=target, text=True).splitlines()
    if set(changed) != set(paths):
        raise ValueError("committed change set differs from named owned paths")
    for name, sha in snapshot.items():
        if sha_bytes(subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=target)) != sha:
            raise ValueError(f"committed bytes mismatch: {name}")
    message = subprocess.check_output(["git", "log", "-1", "--format=%B", commit],
                                     cwd=target, text=True)
    if "Signed-off-by: infra <infra@ai4everyone.top>" not in message:
        raise ValueError("expected DCO identity missing")
    exclusive_json(evidence / "result.json", {
        "verdict": "PASS", "at": now(), "commit": commit, "paths": len(paths),
        "credential_matches": 0, "committed_bytes_verified": True,
        "acceptance": str(acceptance), "push": "NOT_PERFORMED"})
    return commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True,
                        choices=("freeze", "apply", "verify", "commit", "commit-changes"))
    parser.add_argument("--source", type=Path, default=Path("/data/dev/infra"))
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--supplement", action="store_true",
                        help="freeze only the named output specification; keep initial provenance")
    args = parser.parse_args()
    try:
        if args.phase == "commit-changes":
            if args.out is None or args.acceptance is None:
                parser.error("commit-changes requires --out and --acceptance")
            commit = commit_changes(args.target, args.out, args.acceptance)
            print(f"BOOTSTRAP_CHANGE_COMMIT verdict=PASS sha={commit} push=NOT_PERFORMED")
            return 0
        if args.plan is None:
            parser.error("this phase requires --plan")
        if args.phase == "freeze":
            result = freeze(args.source, args.plan, supplement=args.supplement)
            print(f"BOOTSTRAP_FREEZE verdict=PASS files={len(result['files'])} sha256={result['files_sha256']}")
        else:
            plan = json.loads(args.plan.read_text())
            validate_plan(plan)
            if args.phase == "commit":
                if args.out is None:
                    parser.error("commit requires --out")
                commit = commit_baseline(plan, args.target, args.out)
                print(f"BOOTSTRAP_COMMIT verdict=PASS sha={commit} push=NOT_PERFORMED")
            elif args.phase == "apply":
                if args.out is None:
                    parser.error("apply requires --out (new external evidence directory)")
                result = apply(plan, args.target, args.out)
                print(f"BOOTSTRAP_APPLY verdict=PASS files={result['copied']} sha256={result['files_sha256']}")
            else:
                source_drift = verify_files(Path(plan["source_root"]), plan["files"])
                target_drift = verify_files(args.target, plan["files"])
                if source_drift or target_drift:
                    raise ValueError(f"binding drift source={source_drift} target={target_drift}")
                print(f"BOOTSTRAP_BINDING verdict=PASS files={len(plan['files'])} sha256={plan['files_sha256']}")
        return 0
    except Exception as exc:
        print(f"BOOTSTRAP verdict=FAIL type={type(exc).__name__} detail={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
