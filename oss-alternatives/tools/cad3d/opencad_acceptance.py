#!/usr/bin/env python3
"""Run OpenCAD regressions with complete, timestamped evidence outside the tree.

Failures stop the selected stage; logs retain the first cause, not just a tail.
Use --stage focused before --stage packages and --stage gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "oss-alternatives/packages/cadcore"
TOOLS = ROOT / "oss-alternatives/tools"


def is_product_source(path: Path, tree: Path) -> bool:
    """Exclude only generated build/cache artifacts, not new product assets."""
    relative = path.relative_to(tree)
    return (path.is_file() and not {"build", "__pycache__"}.intersection(relative.parts)
            and path.suffix not in (".pyc", ".pyo"))


def source_fingerprint():
    """Bind evidence to the exact product tree, including uncommitted new assets."""
    files = {}
    for tree in (CORE, ROOT / "oss-alternatives/packages/dxf",
                 ROOT / "oss-alternatives/apps/opencad",
                 ROOT / "oss-alternatives/tools/cad3d"):
        for path in sorted(tree.rglob("*")):
            if not is_product_source(path, tree):
                continue
            files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": files}


def commands(stage):
    python = [sys.executable, "-B"]
    if stage == "focused":
        return [("drawing-regressions", CORE, python + [
            "-m", "unittest", "-v", "tests.test_output_registered_ops",
            "tests.test_sw_drawings_ops", "tests.test_store", "tests.test_subproc_geometry",
            "tests.test_subproc_step", "tests.test_output_ops",
        ])]
    if stage == "packages":
        return [(name, ROOT / "oss-alternatives" / path, python + [
            "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v",
        ]) for name, path in [
            ("cadcore", "packages/cadcore"), ("dxf", "packages/dxf"),
            ("opencad-web", "apps/opencad"),
        ]]
    if stage == "web3d":
        return [("web3d-regressions", ROOT / "oss-alternatives/apps/opencad",
                 python + ["-m", "unittest", "-v", "tests.test_web3d"])]
    if stage == "advanced":
        return [("advanced-solid-http", ROOT / "oss-alternatives/apps/opencad",
                 python + ["-m", "unittest", "-v", "tests.test_advanced_solids"])]
    if stage == "release":
        return [("atomic-release-state", ROOT / "oss-alternatives/apps/opencad",
                 python + ["-m", "unittest", "-v", "tests.test_release_state",
                           "tests.test_release_ack_review",
                           "tests.test_release_output_destinations"]),
                ("release-driver-review", TOOLS / "cad3d",
                 python + ["-m", "unittest", "-v", "test_release_workspace"])]
    if stage == "reviews":
        return [("feature-replay-review", CORE, python + [
                    "-m", "unittest", "-v", "tests.test_feature_replay_review"]),
                ("solid-atomic-review", ROOT / "oss-alternatives/apps/opencad", python + [
                    "-m", "unittest", "-v", "tests.test_solid_atomic_review"]),
                ("clearance-http", CORE, python + [
                    "-m", "unittest", "-v", "tests.test_clearance_http"]),
                ("advanced-ui-review", ROOT / "oss-alternatives/apps/opencad", python + [
                    "-m", "unittest", "-v", "tests.test_advanced_ui_review"])]
    if stage == "features":
        return [("advanced-real-geometry", CORE, python + [
            "-m", "unittest", "-v", *("tests.test_features_" + name for name in
            ("sweep", "loft", "edges", "shell", "pattern", "step", "clearance"))])]
    if stage == "advanced-browser":
        return [("advanced-browser", ROOT, python + [
            str(TOOLS / "cad3d/advanced_browser_check.py")])]
    if stage == "views":
        return [("viewer-controls", ROOT, python + [
            str(TOOLS / "cad3d/viewer_controls_check.py")])]
    if stage == "server":
        return [("server-regressions", ROOT / "oss-alternatives/apps/opencad",
                 python + ["-m", "unittest", "-v", "tests.test_server",
                           "tests.test_ui_gate_contract", "tests.test_g28_dxf_scope_real_paths"])]
    if stage == "assembly":
        return [("assembly-and-layers", CORE, python + [
            "-m", "unittest", "-v", "tests.test_sw_assembly_ops",
            "tests.test_b6_assembly_atomic_store", "tests.test_organization_ops",
            "tests.test_assembly_pattern_measure"])]
    if stage == "contracts":
        return [("acceptance-contracts", TOOLS / "cad3d", python + [
            "-m", "unittest", "-v", "test_acceptance_contract", "test_capability_catalog"]),
                ("package-source-closure", TOOLS / "cad", python + [
                    "-m", "unittest", "-v", "test_opencad_package_sources"])]
    if stage == "geometry":
        return [("real-geometry", CORE, python + ["-m", "unittest", "-v", "tests.test_subproc_geometry"])]
    if stage in ("hardware", "browser"):
        return [("web3d-browser", ROOT, python + [str(TOOLS / "cad3d/web3d_browser_check.py")] +
                (["--hardware"] if stage == "hardware" else []))]
    if stage == "gates":
        return [("gate-registration", ROOT, python + [str(TOOLS / "cad/run-gates.py"), "--selftest"]),
                ("cad-gates", ROOT, python + [str(TOOLS / "cad/run-gates.py"),
                                             "--json-out", "{out}/cad-gates.json"])]
    if stage == "gates-repair":
        return [("repaired-gates", ROOT, python + [str(TOOLS / "cad/run-gates.py"),
                 "--only", "G9,G41,G42", "--json-out", "{out}/cad-gates.json"])]
    if stage == "winbuild":
        return [("windows-build", ROOT, python + [
            str(ROOT / "oss-alternatives/apps/opencad/packaging/windows/build-windows-package.py"),
            "--build", "--timing"]),
            ("windows-extract", ROOT, python + [str(TOOLS / "cad/windows_package_gate.py"),
                                               "--extract-installer"]),
            ("windows-package", ROOT, python + [str(TOOLS / "cad/windows_package_gate.py"), "--check"])]
    if stage == "winverify":
        return [("windows-package", ROOT, python + [
            str(TOOLS / "cad/windows_package_gate.py"), "--check"])]
    if stage == "wine-diagnose":
        script = str(TOOLS / "cad/wine_csprng_shim/wine_csprng_shim.py")
        scope = ["--stage", "{build}/stage/OpenCAD", "--out", "{out}/wine-diagnostic"]
        return [("wine-path-contracts", TOOLS / "cad/wine_csprng_shim",
                 python + ["-m", "unittest", "-v", "test_paths"]),
                ("wine-before-after", ROOT, python + [script, "--all"] + scope),
                ("wine-negative-controls", ROOT, python + [script, "--selftest"] + scope),
                ("wine-payload-check", ROOT, python + [script, "--payload-check"] + scope),
                ("wine-revert", ROOT, python + [script, "--revert"] + scope)]
    return [(name, ROOT, python + [str(TOOLS / path)] + args)
            for name, path, args in [
                ("kernel-numeric", "cad3d/numeric-gate-kernel.py", []),
                ("3d-capability", "cad3d/gate-3d-capability.py",
                 ["--check", "--kernels", "nano,freecad", "--artifact", "{out}/i18n-unwired.json"]),
                ("fea-static", "fea/fea-cantilever-gate.py", ["--check"]),
                ("fea-contour", "fea/fea-contour.py", ["--check"]),
                ("sim", "sim/sim-msd-gate.py", ["--check"]),
                ("datapath", "cad/datapath-gate.py", ["--check"]),
            ]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("focused", "packages", "gates", "numeric", "web3d",
                                            "gates-repair", "winbuild", "winverify", "geometry",
                                            "hardware", "browser", "server", "assembly",
                                            "contracts", "wine-diagnose", "advanced", "release",
                                            "reviews", "features", "advanced-browser", "views"), required=True)
    parser.add_argument("--win-build-dir", type=Path,
                        help="Verified independent Windows build, used by gates/winverify.")
    parser.add_argument("--out", default="/hf3fs-jd/data/tmp/opencad-3d-green-20261010")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = Path(args.out) / (stamp + "-" + args.stage)
    out.mkdir(parents=True, exist_ok=False)
    probe = out / ".write-probe"
    probe.touch(exist_ok=False)
    probe.unlink()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(out))
    # Child web gates inherit this explicit root instead of exporting into APP_DIR.
    workspace = out / "workspace"
    workspace.mkdir()
    env["OPENCAD_OUT_ROOT"] = str(workspace)
    source_before = source_fingerprint()
    if args.stage == "winbuild":
        build = out / "win"
        build.mkdir()
        shutil.copyfile(ROOT / "build/win/python-3.10.11-embed-amd64.zip",
                        build / "python-3.10.11-embed-amd64.zip")
        env.update(OPENCAD_WIN_BUILD_DIR=str(build), OPENCAD_WIN_PUBLISH_DIR=str(out / "artifacts"),
                   OPENCAD_WINE_SMOKE_DIR=str(out / "wine"),
                   OPENCAD_INSTALLER_EXTRACT=str(out / "installer-extract"))
    elif args.win_build_dir is not None:
        build = args.win_build_dir.resolve()
        env.update(OPENCAD_WIN_BUILD_DIR=str(build),
                   OPENCAD_WINE_SMOKE_DIR=str(build.parent / "wine"),
                   OPENCAD_INSTALLER_EXTRACT=str(build.parent / "installer-extract"))
    elif args.stage in ("winverify", "wine-diagnose"):
        parser.error("--stage %s requires --win-build-dir" % args.stage)
    results = []
    rc = 0
    for name, cwd, command in commands(args.stage):
        command = [arg.replace("{out}", str(out)).replace(
            "{build}", str(args.win_build_dir.resolve()) if args.win_build_dir else "")
                   for arg in command]
        log = out / (name + ".log")
        start = time.monotonic()
        print(f"OPENCAD_CHECK name={name} state=RUNNING log={log}", flush=True)
        try:
            with log.open("w", encoding="utf-8") as stream:
                proc = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                      stderr=subprocess.STDOUT, check=False)
            text = log.read_text(encoding="utf-8")
            count = re.findall(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
            skipped = re.findall(r"^OK \(skipped=(\d+)\)", text, re.MULTILINE)
            if "-m" in command:
                valid = (any(int(n) > 0 for n in count) and
                         bool(re.search(r"^OK(?: \(|$)", text, re.MULTILINE)))
            elif name == "wine-revert":
                valid = all("REVERT removed=" in line for line in
                            text.splitlines() if line.startswith("REVERT "))
                valid = valid and text.count("REVERT removed=") == 2
            else:
                valid = bool(re.search(r"(?:verdict[=:]PASS|verdict[=:] PASS)", text))
            status = proc.returncode if proc.returncode else (0 if valid else 1)
            item = {"name": name, "rc": status, "process_rc": proc.returncode,
                    "tests": [int(n) for n in count], "wall_s": time.monotonic() - start,
                    "skipped": [int(n) for n in skipped],
                    "command": command, "cwd": str(cwd), "log": str(log)}
        except Exception as exc:
            status = 1
            item = {"name": name, "rc": status, "error": f"{type(exc).__name__}: {exc}",
                    "command": command, "cwd": str(cwd), "log": str(log)}
        results.append(item)
        print(f"OPENCAD_CHECK name={name} rc={status} tests={item.get('tests')} log={log}", flush=True)
        if status:
            rc = 1
            break
    source_after = source_fingerprint()
    if source_before != source_after:
        rc = 1
    source_changes = [key for key in sorted(source_before["files"].keys() | source_after["files"].keys())
                      if source_before["files"].get(key) != source_after["files"].get(key)]
    report = {"verdict": "FAIL" if rc else "PASS", "stage": args.stage, "time": stamp,
              "source": source_before, "source_unchanged": source_before == source_after,
              "source_changes": source_changes, "workspace": str(workspace),
              "skipped_tests": sum(sum(item.get("skipped", [])) for item in results),
              "results": results, "expected": len(commands(args.stage))}
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"OPENCAD_ACCEPTANCE verdict={report['verdict']} stage={args.stage} "
          f"completed={len(results)}/{report['expected']} time={stamp} evidence={out}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
