#!/usr/bin/env python3
"""Independent CAD regression entry point, with exact counts and source binding.

Stages: scan (source hygiene and in-repository runtime imports), smoke (scan,
real HTTP selftest, document/store/DXF tests), core (all cadcore tests), web
(all OpenCAD tests), all (negative controls + HTTP + core + Web + DXF).
Coupling is a targeted regression for the migrated infra dependencies.
No test exclusions or new skips.
Evidence is external on 3FS; child temporary files and SQLite use /data/tmp.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import faulthandler
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import traceback
import unittest
from urllib.parse import unquote, urlsplit

import bootstrap_from_infra as bootstrap

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "oss-alternatives/packages"
APP = ROOT / "oss-alternatives/apps/opencad"
RUNTIME_ORIGINS = {
    "cadcore.docmodel": PACKAGES / "cadcore/cadcore/docmodel.py",
    "cadcore.store": PACKAGES / "cadcore/cadcore/store.py",
    "cadcore.kernel.features_registry": PACKAGES / "cadcore/cadcore/kernel/features_registry.py",
    "dxf.reader": PACKAGES / "dxf/dxf/reader.py",
    "freechat_vault.store": PACKAGES / "vault/freechat_vault/store.py",
    "freechat_vault.fcx": PACKAGES / "vault/freechat_vault/fcx.py",
    "server": APP / "server.py",
}
LOCAL_RUNTIME_PARENT = Path("/data/tmp")
PERSISTENT_FILESYSTEMS = {"ext2/ext3", "ext4", "xfs", "btrfs", "zfs"}


def local_runtime_path(value: Path, *, fresh: bool = False, short: bool = False) -> Path:
    """Validate actual ancestors, not a declaration or a resolved symlink alias."""
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            not path.is_relative_to(LOCAL_RUNTIME_PARENT) or path == LOCAL_RUNTIME_PARENT):
        raise ValueError("SQLite runtime must be an absolute /data/tmp child")
    if short and len(os.fsencode(path)) > 80:
        raise ValueError("SQLite runtime root exceeds the short-path limit of 80 bytes")
    existing = LOCAL_RUNTIME_PARENT
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError(f"symlink SQLite runtime path: {parent}")
        if parent.exists():
            if parent != path and not parent.is_dir():
                raise ValueError(f"non-directory SQLite runtime ancestor: {parent}")
            if parent.is_dir():
                existing = parent
                if (parent / ".git").exists():
                    raise ValueError("SQLite runtime must not be inside a repository")
    filesystem = subprocess.check_output(
        ["stat", "-f", "-c", "%T", str(existing)], text=True).strip()
    if filesystem not in PERSISTENT_FILESYSTEMS:
        raise ValueError(f"SQLite runtime is not local persistent storage: {filesystem}")
    if fresh and path.exists():
        raise FileExistsError(f"SQLite runtime already exists: {path}")
    return path


class SQLiteRuntimeGuard:
    def __init__(self, root: Path):
        self.root = local_runtime_path(root, short=True)
        self.opens = 0
        self.paths = {}
        self.rejected = []

    def audit(self, event, args):
        if event != "sqlite3.connect":
            return
        value = os.fsdecode(args[0])
        if value == ":memory:" or value == "file::memory:" or value.startswith(
                "file::memory:?"):
            return
        if value.startswith("file:"):
            uri = urlsplit(value)
            if uri.netloc or uri.fragment:
                self.reject("SQLite URI authority/fragment rejected")
            value = unquote(uri.path)
        try:
            if not value:
                raise ValueError("empty SQLite filename is not an isolated database")
            path = local_runtime_path(Path(value).absolute())
            if not path.is_relative_to(self.root):
                raise ValueError("actual SQLite path is outside the declared local runtime")
            if not path.parent.is_dir():
                raise ValueError("SQLite database parent is absent")
            for suffix in ("-wal", "-shm", "-journal"):
                local_runtime_path(Path(str(path) + suffix))
            if (os.environ.get("OPENCAD_TEST_RUNTIME_ROOT") != str(self.root) or
                    tempfile.gettempdir() != str(self.root)):
                raise ValueError("SQLite runtime declaration/tempfile drift")
            filesystem = subprocess.check_output(
                ["stat", "-f", "-c", "%T", str(path.parent)], text=True).strip()
            self.paths[str(path)] = filesystem
            self.opens += 1
        except Exception as exc:
            self.reject(str(exc))

    def reject(self, reason):
        self.rejected.append(reason)
        print(f"INDEPENDENT_SQLITE_REJECT reason={reason}", file=sys.stderr, flush=True)
        raise ValueError(reason)

    def summary(self) -> dict:
        return {"root": str(self.root), "TMPDIR": os.environ.get("TMPDIR"),
                "tempfile": tempfile.gettempdir(),
                "filesystem": subprocess.check_output(
                    ["stat", "-f", "-c", "%T", str(self.root)], text=True).strip(),
                "sqlite_opens": self.opens, "sqlite_paths": dict(sorted(self.paths.items())),
                "rejected": list(self.rejected)}


def configure_runtime(runtime: Path | None = None) -> SQLiteRuntimeGuard:
    active = getattr(sys, "_opencad_acceptance_sqlite_guard", None)
    if active is not None:
        if runtime is not None and active.root != runtime:
            raise ValueError("cannot replace the active SQLite runtime")
        return active
    declared = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
    if runtime is None:
        if declared is not None:
            if not declared:
                raise ValueError("empty explicit SQLite runtime declaration")
            runtime = local_runtime_path(Path(declared), short=True)
        else:
            runtime = Path(tempfile.mkdtemp(prefix="oc-a-", dir=LOCAL_RUNTIME_PARENT))
    runtime = local_runtime_path(runtime, short=True)
    if not runtime.is_dir():
        raise ValueError("SQLite runtime directory must exist")
    for name in ("TMPDIR", "TMP", "TEMP", "OPENCAD_TEST_RUNTIME_ROOT"):
        os.environ[name] = str(runtime)
    tempfile.tempdir = str(runtime)
    guard = SQLiteRuntimeGuard(runtime)
    sys.addaudithook(guard.audit)
    sys._opencad_acceptance_sqlite_guard = guard
    faulthandler.enable(all_threads=True)
    return guard


def runtime_marker(guard: SQLiteRuntimeGuard) -> dict:
    summary = guard.summary()
    print("INDEPENDENT_RUNTIME_BINDING " + json.dumps(summary, sort_keys=True), flush=True)
    return summary


def source_manifest(root: Path) -> dict:
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in {
            ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}]
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f"linked source directory: {Path(directory) / name}")
        for name in names:
            if name == ".git":
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            value = bootstrap.regular_bytes(path)
            files[relative] = {"sha256": bootstrap.sha_bytes(value), "bytes": len(value)}
    if not files or "oss-alternatives/apps/opencad/server.py" not in files:
        raise ValueError("empty/incomplete independent source")
    return dict(sorted(files.items()))


def runtime_bindings(root: Path) -> dict:
    """Bind every loaded CAD namespace, including dynamically loaded pieces."""
    allowed = {
        "cadcore": root / "oss-alternatives/packages/cadcore/cadcore",
        "dxf": root / "oss-alternatives/packages/dxf/dxf",
        "freechat_vault": root / "oss-alternatives/packages/vault/freechat_vault",
    }
    bound = {}
    for name, module in list(sys.modules.items()):
        namespace = name.split(".")[0]
        expected = allowed.get(namespace)
        if expected is None:
            continue
        filename = getattr(module, "__file__", None)
        if not filename:
            locations = [Path(path).resolve() for path in getattr(module, "__path__", [])]
            if not locations or any(not path.is_relative_to(expected) or not path.is_dir()
                                    for path in locations):
                raise ValueError(f"unbound/foreign CAD namespace import: {name}")
            bound[name] = [path.relative_to(root).as_posix() for path in locations]
            continue
        actual = Path(filename).resolve()
        if not actual.is_relative_to(expected) or not actual.is_file():
            raise ValueError(f"foreign CAD runtime import: {name}: {actual}")
        bound[name] = actual.relative_to(root).as_posix()
    if not bound:
        raise ValueError("empty CAD runtime binding set")
    return dict(sorted(bound.items()))


def scan(root: Path) -> dict:
    manifest = source_manifest(root)
    for name in manifest:
        bootstrap.scan_bytes(name, bootstrap.regular_bytes(root / name))
        if name.endswith((".pyc", ".exe", ".dll", ".png", ".sqlite", ".log")):
            raise ValueError(f"generated/desktop/evidence file in source: {name}")
    for path in (PACKAGES / "cadcore", PACKAGES / "dxf", PACKAGES / "vault", APP):
        sys.path.insert(0, str(path))
    bound = {}
    for name, expected in RUNTIME_ORIGINS.items():
        module = importlib.import_module(name)
        actual = Path(module.__file__).resolve()
        if actual != expected.resolve():
            raise ValueError(f"foreign runtime import {name}: {actual}")
        bound[name] = actual.relative_to(root).as_posix()
    # The CAD bridge's dynamic vault loader must resolve locally too.
    store = importlib.import_module("cadcore.store")
    if Path(store._load_vault_module().__file__).resolve() != RUNTIME_ORIGINS["freechat_vault.store"]:
        raise ValueError("cadcore dynamic vault binding mismatch")
    return {"files": len(manifest), "source_sha256": bootstrap.digest(manifest),
            "runtime_origins": bound, "loaded_cad_origins": runtime_bindings(root),
            "credential_matches": 0}


class CountedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed += 1


def counts_reconcile(summary: dict) -> bool:
    return summary["tests"] == (
        summary["passed"] + summary["failures"] + summary["errors"] +
        len(summary["skipped"]) + len(summary["expected_failures"]) +
        len(summary["unexpected_successes"]))


def run_suite(start: Path, pattern: str, top: Path, case: str | None = None) -> dict:
    guard = configure_runtime()
    scan(ROOT)
    # Sibling packages all use "tests"; prioritize this child process's root.
    sys.path[:] = [str(top)] + [path for path in sys.path if path != str(top)]
    suite = (unittest.defaultTestLoader.loadTestsFromName(case) if case else
             unittest.defaultTestLoader.discover(str(start), pattern=pattern, top_level_dir=str(top)))
    count = suite.countTestCases()
    if not count:
        raise ValueError(f"empty unittest discovery: {start} {pattern}")
    result = unittest.TextTestRunner(verbosity=2, resultclass=CountedResult).run(suite)
    summary = {
        "discovered": count, "tests": result.testsRun, "passed": result.passed,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "expected_failures": [str(test) for test, _ in result.expectedFailures],
        "unexpected_successes": [str(test) for test in result.unexpectedSuccesses],
        "successful": result.wasSuccessful(),
        "loaded_cad_origins": runtime_bindings(ROOT),
    }
    summary["counts_reconciled"] = counts_reconcile(summary)
    summary["sqlite_runtime"] = guard.summary()
    if not summary["counts_reconciled"] or guard.rejected:
        summary["successful"] = False
    print("INDEPENDENT_TEST_COUNTS " + json.dumps(summary, sort_keys=True))
    runtime_marker(guard)
    return summary


def http_selftest(workspace: Path) -> int:
    """Run the original 15 HTTP checks using the server's external temp root."""
    workspace = workspace.resolve()
    if workspace == ROOT or ROOT in workspace.parents:
        raise ValueError("HTTP selftest workspace must be outside source tree")
    guard = configure_runtime()
    workspace = local_runtime_path(workspace)
    if not workspace.is_relative_to(guard.root):
        raise ValueError("HTTP workspace outside declared SQLite runtime")
    workspace.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(APP))
    server = importlib.import_module("server")
    if Path(server.__file__).resolve() != APP / "server.py":
        raise ValueError("foreign HTTP selftest server")
    rc = server.selftest(temp_root=str(workspace))
    origins = runtime_bindings(ROOT)
    print("INDEPENDENT_HTTP_BINDINGS " + json.dumps(origins, sort_keys=True))
    runtime_marker(guard)
    if guard.rejected:
        return 1
    return rc


def child(args: list[str], cwd: Path, out: Path, label: str, runtime: Path) -> dict:
    runtime = local_runtime_path(runtime, short=True)
    log = out / (label + ".log")
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONFAULTHANDLER="1",
               TMPDIR=str(runtime), TMP=str(runtime), TEMP=str(runtime),
               OPENCAD_OUT_ROOT=str(runtime / "workspace"),
               OPENCAD_EVIDENCE_ROOT=str(out),
               OPENCAD_CADCORE_ROOT=str(PACKAGES / "cadcore"),
               OPENCAD_TEST_RUNTIME_ROOT=str(runtime),
               PYTHONPATH=os.pathsep.join(str(path) for path in
                                         (PACKAGES / "cadcore", PACKAGES / "dxf", PACKAGES / "vault", APP)))
    with log.open("xb") as handle:
        handle.write(f"OPENCAD_TEST_RUNTIME_ROOT={runtime}\n".encode())
        handle.flush()
        process = subprocess.run([sys.executable, "-B", *args], cwd=cwd, env=env,
                                 stdout=handle, stderr=subprocess.STDOUT)
        handle.write(f"\nINDEPENDENT_CHILD_RC rc={process.returncode}\n".encode())
    text = log.read_text()
    counts = [line.removeprefix("INDEPENDENT_TEST_COUNTS ") for line in text.splitlines()
              if line.startswith("INDEPENDENT_TEST_COUNTS ")]
    summary = json.loads(counts[0]) if len(counts) == 1 else None
    result = {"label": label, "argv": args, "cwd": str(cwd), "rc": process.returncode,
              "log_sha256": bootstrap.sha_bytes(log.read_bytes()), "counts": summary,
              "test_runtime_root": str(runtime)}
    bindings = [json.loads(line.removeprefix("INDEPENDENT_RUNTIME_BINDING "))
                for line in text.splitlines()
                if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
    if len(bindings) == 1:
        result["sqlite_runtime"] = bindings[0]
    compact = ({key: value for key, value in summary.items()
                if key not in {"loaded_cad_origins", "sqlite_runtime"}}
               if summary else None)
    print(f"INDEPENDENT_STEP label={label} rc={process.returncode} counts={compact}")
    if process.returncode:
        print(text[-6000:], file=sys.stderr)
    if label == "http-selftest":
        markers = re.findall(r"^OPENCAD_SERVER selftest verdict=PASS checks=(\d+) failed=0$", text, re.M)
        if len(markers) != 1 or int(markers[0]) != 15 or "INDEPENDENT_HTTP_BINDINGS " not in text:
            result["marker_error"] = "real HTTP selftest exact 15-check/binding marker absent"
        else:
            result["http_checks"] = int(markers[0])
    if label != "http-selftest" and summary is None:
        result["marker_error"] = "unittest count marker absent or duplicated"
    elif summary is not None and not summary["successful"]:
        result["marker_error"] = "unittest result is unsuccessful"
    if (len(bindings) != 1 or bindings[0]["root"] != str(runtime) or
            bindings[0]["TMPDIR"] != str(runtime) or
            bindings[0]["tempfile"] != str(runtime) or bindings[0]["rejected"]):
        result["runtime_error"] = "actual local SQLite runtime binding absent/invalid/rejected"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("scan", "smoke", "coupling", "core", "web", "all"), default="smoke")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path,
                        help="new local-disk external directory for SQLite WAL tests")
    parser.add_argument("--suite", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--pattern", default="test*.py", help=argparse.SUPPRESS)
    parser.add_argument("--top", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--http-selftest", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.http_selftest:
        return http_selftest(args.http_selftest)
    if args.suite:
        if args.top is None:
            parser.error("--suite requires --top")
        return 0 if run_suite(args.suite, args.pattern, args.top, args.case)["successful"] else 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or Path("/hf3fs-jd/data/tmp/opencad-bootstrap-s7") / (stamp + "-" + args.stage)).resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("evidence must be outside the source tree")
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "stage": args.stage, "at": bootstrap.now(), "steps": [],
              "production_deployment": "NOT_ASSESSED",
              "industrial_parity": "NOT_ASSESSED", "multi_tenant_readiness": "NOT_ASSESSED"}
    before = None
    try:
        declared = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
        if args.runtime_root is not None:
            runtime = local_runtime_path(args.runtime_root, fresh=True, short=True)
            runtime.mkdir()
        elif declared is not None:
            if not declared:
                raise ValueError("empty explicit SQLite runtime declaration")
            runtime = local_runtime_path(Path(declared) / stamp, fresh=True, short=True)
            runtime.mkdir()
        else:
            runtime = Path(tempfile.mkdtemp(prefix="oc-a-", dir=LOCAL_RUNTIME_PARENT))
        if runtime == out or runtime in out.parents or out in runtime.parents:
            raise ValueError("SQLite test runtime overlaps evidence")
        (runtime / "workspace").mkdir()
        guard = configure_runtime(runtime)
        filesystem = guard.summary()["filesystem"]
        report["test_runtime"] = {"root": str(runtime), "filesystem": filesystem,
                                  "env": "OPENCAD_TEST_RUNTIME_ROOT", "TMPDIR": str(runtime)}
        print(f"INDEPENDENT_RUNTIME root={runtime} filesystem={filesystem}")
        report["source_head_before"] = subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True).strip()
        report["source_status_before"] = subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(ROOT), "status", "--porcelain"],
            text=True)
        before = source_manifest(ROOT)
        report["source_before"] = before
        report["scan"] = scan(ROOT)
        commands = []
        if args.stage in ("smoke", "coupling", "all"):
            commands.append(("sqlite-runtime-negative-controls", [
                str(Path(__file__).resolve()), "--suite", str(ROOT / "tools"),
                "--top", str(ROOT / "tools"), "--pattern",
                "test_sqlite_runtime_isolation.py"], ROOT))
            commands.append(("bootstrap-negative-controls", [
                str(Path(__file__).resolve()), "--suite", str(ROOT / "tools"),
                "--top", str(ROOT / "tools"), "--pattern", "test_bootstrap.py"], ROOT))
            commands.append(("http-selftest", [str(Path(__file__).resolve()),
                             "--http-selftest", str(runtime / "http-selftest-workspace")], ROOT))
        if args.stage == "smoke":
            for pattern in ("test_docmodel.py", "test_store.py"):
                commands.append((pattern, [str(Path(__file__).resolve()), "--suite",
                    str(PACKAGES / "cadcore/tests"), "--top", str(PACKAGES / "cadcore"),
                    "--pattern", pattern], ROOT))
        if args.stage == "coupling":
            for pattern in ("test_edit_reach_contract.py", "test_partials_batch3_ops.py",
                            "test_no_source_tree_pollution.py", "test_output_registered_ops.py"):
                commands.append((pattern, [str(Path(__file__).resolve()), "--suite",
                    str(PACKAGES / "cadcore/tests"), "--top", str(PACKAGES / "cadcore"),
                    "--pattern", pattern], ROOT))
            commands.append(("web-selftest-cli", [str(Path(__file__).resolve()), "--suite",
                str(APP / "tests"), "--top", str(APP), "--case",
                "tests.test_server.TestCliVerdictLines.test_selftest_judgement_line_passes"], ROOT))
        if args.stage in ("core", "all"):
            commands.append(("cadcore", [str(Path(__file__).resolve()), "--suite",
                str(PACKAGES / "cadcore/tests"), "--top", str(PACKAGES / "cadcore")], ROOT))
        if args.stage in ("web", "all"):
            commands.append(("web", [str(Path(__file__).resolve()), "--suite",
                str(APP / "tests"), "--top", str(APP)], ROOT))
        if args.stage in ("smoke", "all"):
            commands.append(("dxf", [str(Path(__file__).resolve()), "--suite",
                str(PACKAGES / "dxf/tests"), "--top", str(PACKAGES / "dxf")], ROOT))
        for label, argv, cwd in commands:
            result = child(argv, cwd, out, label, runtime)
            report["steps"].append(result)
            if result["rc"] or result.get("marker_error") or result.get("runtime_error"):
                report["first_cause"] = dict(result)
                raise RuntimeError(f"failed independent step: {label}")
        report["verdict"] = "PASS"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc()}
        report.setdefault("first_cause", report["error"])
        print(f"INDEPENDENT_ACCEPTANCE error={type(exc).__name__} detail={exc}", file=sys.stderr)
    finally:
        if before is not None:
            try:
                after = source_manifest(ROOT)
                drift = sorted(name for name in before.keys() | after.keys()
                               if before.get(name) != after.get(name))
                report["source_drift"] = drift
                report["source_after"] = after
                report["source_sha256"] = bootstrap.digest(before)
                report["source_after_sha256"] = bootstrap.digest(after)
                report["source_head_after"] = subprocess.check_output(
                    ["git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
                    text=True).strip()
                report["source_status_after"] = subprocess.check_output(
                    ["git", "--no-replace-objects", "-C", str(ROOT), "status", "--porcelain"],
                    text=True)
                if (drift or report["source_head_before"] != report["source_head_after"] or
                        report["source_status_before"] != report["source_status_after"]):
                    report["verdict"] = "FAIL"
                    report.setdefault("first_cause", {"detail": "independent source drift"})
            except Exception as exc:
                report["verdict"] = "FAIL"
                report["source_recheck_error"] = {"type": type(exc).__name__, "detail": str(exc)}
                report.setdefault("first_cause", report["source_recheck_error"])
        summaries = [step["counts"] for step in report["steps"] if step.get("counts")]
        report["totals"] = {
            "tests": sum(value["tests"] for value in summaries),
            "passed": sum(value["passed"] for value in summaries),
            "failures": sum(value["failures"] for value in summaries),
            "errors": sum(value["errors"] for value in summaries),
            "skipped": sum(len(value["skipped"]) for value in summaries),
            "http_checks": sum(step.get("http_checks", 0) for step in report["steps"]),
        }
        report["finished_at"] = bootstrap.now()
        report["actual_rc"] = 0 if report["verdict"] == "PASS" else 1
        bootstrap.exclusive_json(out / "result.json", report)
        line = f"INDEPENDENT_ACCEPTANCE verdict={report['verdict']} stage={args.stage} evidence={out}"
        bootstrap.exclusive_bytes(out / "acceptance.log", (line + "\n").encode())
        print(line)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
