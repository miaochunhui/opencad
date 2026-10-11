#!/usr/bin/env python3
"""Independent CAD regression entry point, with exact counts and source binding.

Stages: scan (source hygiene and in-repository runtime imports), smoke (scan,
real HTTP selftest, document/store/DXF tests), core (all cadcore tests), web
(all OpenCAD tests), all (core + DXF + Web). No test exclusions or new skips.
Full discovery currently exposes the documented legacy infra coupling.
Evidence and child temporary files are kept outside the worktree.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import unittest

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


def source_manifest(root: Path) -> dict:
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in {
            ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}]
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            value = bootstrap.regular_bytes(path)
            files[relative] = {"sha256": bootstrap.sha_bytes(value), "bytes": len(value)}
    if not files or "oss-alternatives/apps/opencad/server.py" not in files:
        raise ValueError("empty/incomplete independent source")
    return dict(sorted(files.items()))


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
            "runtime_origins": bound, "credential_matches": 0}


def run_suite(start: Path, pattern: str, top: Path) -> dict:
    # Sibling packages all use "tests"; prioritize this child process's root.
    sys.path[:] = [str(top)] + [path for path in sys.path if path != str(top)]
    suite = unittest.defaultTestLoader.discover(str(start), pattern=pattern, top_level_dir=str(top))
    count = suite.countTestCases()
    if not count:
        raise ValueError(f"empty unittest discovery: {start} {pattern}")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        "tests": result.testsRun, "passed": result.testsRun - len(result.failures)
        - len(result.errors) - len(result.skipped),
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "successful": result.wasSuccessful(),
    }
    print("INDEPENDENT_TEST_COUNTS " + json.dumps(summary, sort_keys=True))
    return summary


def child(args: list[str], cwd: Path, out: Path, label: str) -> dict:
    log = out / (label + ".log")
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(out / "tmp"),
               OPENCAD_OUT_ROOT=str(out / "workspace"),
               OPENCAD_CADCORE_ROOT=str(PACKAGES / "cadcore"),
               PYTHONPATH=os.pathsep.join(str(path) for path in
                                         (PACKAGES / "cadcore", PACKAGES / "dxf", PACKAGES / "vault", APP)))
    with log.open("xb") as handle:
        process = subprocess.run([sys.executable, "-B", *args], cwd=cwd, env=env,
                                 stdout=handle, stderr=subprocess.STDOUT)
    text = log.read_text()
    counts = [line.removeprefix("INDEPENDENT_TEST_COUNTS ") for line in text.splitlines()
              if line.startswith("INDEPENDENT_TEST_COUNTS ")]
    summary = json.loads(counts[-1]) if counts else None
    result = {"label": label, "argv": args, "cwd": str(cwd), "rc": process.returncode,
              "log_sha256": bootstrap.sha_bytes(log.read_bytes()), "counts": summary}
    print(f"INDEPENDENT_STEP label={label} rc={process.returncode} counts={summary}")
    if process.returncode:
        print(text[-6000:], file=sys.stderr)
    if label == "http-selftest" and "OPENCAD_SERVER selftest verdict=PASS" not in text:
        result["marker_error"] = "real HTTP selftest PASS marker absent"
    if label != "http-selftest" and summary is None:
        result["marker_error"] = "unittest count marker absent"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("scan", "smoke", "core", "web", "all"), default="smoke")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--suite", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--pattern", default="test*.py", help=argparse.SUPPRESS)
    parser.add_argument("--top", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.suite:
        return 0 if run_suite(args.suite, args.pattern, args.top)["successful"] else 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or Path("/hf3fs-jd/data/tmp/opencad-bootstrap-s7") / (stamp + "-" + args.stage)).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    (out / "workspace").mkdir()
    report = {"verdict": "FAIL", "stage": args.stage, "at": bootstrap.now(), "steps": [],
              "production_deployment": "NOT_ASSESSED",
              "industrial_parity": "NOT_ASSESSED", "multi_tenant_readiness": "NOT_ASSESSED"}
    before = None
    try:
        before = source_manifest(ROOT)
        report["source_before"] = before
        report["scan"] = scan(ROOT)
        commands = []
        if args.stage == "smoke":
            commands.append(("bootstrap-negative-controls", [
                str(Path(__file__).resolve()), "--suite", str(ROOT / "tools"),
                "--top", str(ROOT / "tools"), "--pattern", "test_bootstrap.py"], ROOT))
            commands.append(("http-selftest", [str(APP / "server.py"), "--selftest"], ROOT))
            for pattern in ("test_docmodel.py", "test_store.py"):
                commands.append((pattern, [str(Path(__file__).resolve()), "--suite",
                    str(PACKAGES / "cadcore/tests"), "--top", str(PACKAGES / "cadcore"),
                    "--pattern", pattern], ROOT))
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
            result = child(argv, cwd, out, label)
            report["steps"].append(result)
            if result["rc"] or result.get("marker_error"):
                raise RuntimeError(f"failed independent step: {label}")
        report["verdict"] = "PASS"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc()}
        print(f"INDEPENDENT_ACCEPTANCE error={type(exc).__name__} detail={exc}", file=sys.stderr)
    finally:
        if before is not None:
            after = source_manifest(ROOT)
            drift = sorted(name for name in before.keys() | after.keys()
                           if before.get(name) != after.get(name))
            report["source_drift"] = drift
            report["source_sha256"] = bootstrap.digest(before)
            if drift:
                report["verdict"] = "FAIL"
        report["finished_at"] = bootstrap.now()
        bootstrap.exclusive_json(out / "result.json", report)
        line = f"INDEPENDENT_ACCEPTANCE verdict={report['verdict']} stage={args.stage} evidence={out}"
        bootstrap.exclusive_bytes(out / "acceptance.log", (line + "\n").encode())
        print(line)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
