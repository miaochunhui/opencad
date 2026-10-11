#!/usr/bin/env python3
"""Source-bound fail-fast acceptance for rigid subassemblies, with retained raw exits."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import acceptance


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def fingerprint(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(out):
    report = json.loads((out / "result.json").read_text())
    bad = [name for name, item in report["artifacts"].items()
           if not (out / name).is_file() or digest(out / name) != item]
    raw = [out / name for name in report["artifacts"] if Path(name).name == "process.json"]
    raw_valid = bool(raw) and all(
        (value := json.loads(path.read_text()))["rc"] == 0 and value["rc_measured"]
        and not value["timed_out"] for path in raw)
    children = report["children"]
    checks = {
        "result_pass": report["verdict"] == "PASS" and report["rc"] == 0,
        "source_unchanged": report["source_before"] == report["source_after"],
        "current_source": acceptance.source_manifest(ROOT) == report["source_after"],
        "source_fingerprint": fingerprint(report["source_after"]) == report["source_fingerprint"],
        "artifacts": not bad, "raw_jobs": raw_valid,
        "counts": all(c["rc"] == 0 and c["summary"] is not None and
                      c["summary"]["tests"] == len(c["summary"]["selected"]) and
                      (c["summary"]["failures"], c["summary"]["errors"], c["summary"]["skips"]) ==
                      (0, 0, 0) for c in children),
        "total": report["tests"] == sum(c["summary"]["tests"] for c in children),
        "bounded_claim": not report["full_industrial_assembly"] and report["production_requests"] == 0,
    }
    print("ASSEMBLY_SCALE_READBACK verdict=%s checks=%s raw_jobs=%d result=%s" % (
        "PASS" if all(checks.values()) else "FAIL", json.dumps(checks, sort_keys=True),
        len(raw), out / "result.json"))
    return 0 if all(checks.values()) else 1


def child(args):
    path = (ROOT / "oss-alternatives/packages/cadcore/tests/test_real_assembly.py"
            if args.child == "kernel" else
            ROOT / "oss-alternatives/apps/opencad/tests/test_industrial_assembly.py")
    module = load("scale_" + args.child, path)
    module.OUT = args.out / args.child
    module.OUT.mkdir(exist_ok=False)
    cls = module.RealAssemblyTests if args.child == "kernel" else module.IndustrialAssemblyTests
    names = unittest.defaultTestLoader.getTestCaseNames(cls)
    if args.case:
        missing = set(args.case) - set(names)
        if missing:
            raise ValueError("unknown case: %s" % sorted(missing))
        names = list(args.case)
    elif args.stage == "scale":
        names = [name for name in names if int(name.split("_")[1]) >= (
            31 if args.child == "kernel" else 30)]
    if not names:
        raise ValueError("empty acceptance selection")
    started = now()
    with (module.OUT / "tests.log").open("x") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, failfast=True).run(
            unittest.TestSuite(cls(name) for name in names))
    jobs = list(module.OUT.rglob("process.json"))
    report = {"started_at": started, "finished_at": now(), "selected": names,
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skips": len(result.skipped),
              "failure_details": [{"test": test.id(), "traceback": detail}
                                  for test, detail in result.failures + result.errors],
              "observations": module.OBSERVATIONS, "raw_jobs": len(jobs),
              "runtime_bindings": acceptance.runtime_bindings(ROOT)}
    valid_jobs = bool(jobs) and all(
        (value := json.loads(p.read_text()))["rc"] == 0 and value["rc_measured"]
        and not value["timed_out"] for p in jobs)
    complete = (result.wasSuccessful() and not result.skipped
                and result.testsRun == len(names) and valid_jobs)
    report.update(verdict="PASS" if complete else "FAIL", raw_jobs_valid=valid_jobs)
    (module.OUT / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    return 0 if complete else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--expect-head", default="941005d7c45a35f8cbbe8d2dc65c68093a790a3b")
    parser.add_argument("--stage", choices=("scale", "regression"), default="scale")
    parser.add_argument("--scope", choices=("kernel", "web", "all"), default="all")
    parser.add_argument("--case", action="append")
    parser.add_argument("--child", choices=("kernel", "web"))
    parser.add_argument("--verify-only", action="store_true",
                        help="read-only source/count/artifact/raw-process verification")
    args = parser.parse_args()
    if args.verify_only:
        return verify(args.out.resolve())
    if args.child:
        return child(args)
    args.out = args.out.resolve()
    runtime = (args.runtime_root or Path("/data/tmp") / args.out.name).resolve()
    for target in (args.out, runtime):
        if target == ROOT or ROOT in target.parents or target.is_symlink() or target.exists():
            raise ValueError("requires new external evidence/runtime child: %s" % target)
    if args.out == runtime or args.out in runtime.parents or runtime in args.out.parents:
        raise ValueError("evidence and local runtime must be separate")
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    if head != args.expect_head:
        raise ValueError("HEAD mismatch: %s" % head)
    args.out.mkdir(parents=True, exist_ok=False)
    runtime.mkdir(parents=True, exist_ok=False)
    filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(runtime)], text=True).strip()
    if filesystem not in ("ext2/ext3", "xfs", "btrfs", "zfs"):
        raise ValueError("requires local persistent fixture filesystem: %s" % filesystem)
    before = acceptance.source_manifest(ROOT)
    started, clock = now(), time.monotonic()
    records = []
    for scope in (("kernel", "web") if args.scope == "all" else (args.scope,)):
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--child", scope,
                "--out", str(args.out), "--stage", args.stage]
        for case in args.case or ():
            argv.extend(("--case", case))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   OPENCAD_TEST_RUNTIME_ROOT=str(runtime), TMPDIR=str(runtime))
        launched = now()
        with (args.out / (scope + ".stdout.log")).open("xb") as stdout, (
                args.out / (scope + ".stderr.log")).open("xb") as stderr:
            process = subprocess.run(argv, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
        summary_path = args.out / scope / "summary.json"
        record = {"scope": scope, "argv": argv, "rc": process.returncode,
                  "started_at": launched, "finished_at": now(),
                  "summary": json.loads(summary_path.read_text()) if summary_path.is_file() else None}
        records.append(record)
        (args.out / (scope + ".process.json")).write_text(json.dumps(record, indent=2, sort_keys=True))
        print("ASSEMBLY_SCALE_CHILD scope=%s rc=%d evidence=%s" % (scope, process.returncode, args.out),
              flush=True)
        if process.returncode:
            break
    after = acceptance.source_manifest(ROOT)
    artifacts = {str(p.relative_to(args.out)): digest(p) for p in args.out.rglob("*") if p.is_file()}
    complete = bool(records) and all(r["rc"] == 0 for r in records) and before == after
    report = {"verdict": "PASS" if complete else "FAIL", "rc": 0 if complete else 1,
              "head": head, "source_root": str(ROOT), "source_before": before, "source_after": after,
              "source_fingerprint": fingerprint(after),
              "source_unchanged": before == after, "started_at": started, "finished_at": now(),
              "duration_s": time.monotonic()-clock, "children": records, "artifacts": artifacts,
              "runtime_root": str(runtime), "runtime_filesystem": filesystem,
              "tests": sum(r["summary"]["tests"] for r in records if r["summary"]),
              "full_industrial_assembly": False, "production_requests": 0,
              "scope": args.stage + "/" + args.scope}
    (args.out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print("ASSEMBLY_SCALE verdict=%s tests=%d evidence=%s" % (
        report["verdict"], report["tests"], args.out), flush=True)
    return report["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
