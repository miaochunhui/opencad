#!/usr/bin/env python3
"""Real SQLite auth/revocation acceptance; only fresh external evidence/fixtures.

Run as infra with Python -B, --out NEW_3FS_DIRECTORY and --runtime-root
NEW_LOCAL_DIRECTORY. --case selects an explicit unittest control. No production,
gateway edits, credential provisioning or cleanup is performed by this runner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import sys

import acceptance
import bootstrap_from_infra as bootstrap

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--case")
    args = parser.parse_args()
    if os.geteuid() != pwd.getpwnam("infra").pw_uid:
        parser.error("infra identity required")
    out, runtime = args.out.resolve(), args.runtime_root.resolve()
    if (not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve())
            or out == Path("/hf3fs-jd/data/tmp").resolve()
            or not runtime.is_relative_to(Path("/data/tmp"))
            or runtime == Path("/data/tmp")):
        parser.error("fresh 3FS evidence and local /data/tmp fixture child required")
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    (out / "workspace").mkdir()
    report = {"verdict": "FAIL", "started_at": now(), "source_root": str(ROOT),
              "scope": "LOCAL_AUTH_PROJECT_SQLITE_NOT_GATEWAY_OR_PRODUCTION",
              "steps": [], "runtime_root": str(runtime)}
    before = acceptance.source_manifest(ROOT)
    report["source_before"] = before
    try:
        runtime.mkdir(parents=True, exist_ok=False)
        from project_store import validate_root
        validate_root(runtime)
        report["scan"] = acceptance.scan(ROOT)
        argv = [str(ROOT / "tools/acceptance.py"), "--suite", str(APP / "tests"),
                "--top", str(APP), "--pattern", "test_access_control.py"]
        if args.case:
            argv.extend(["--case", args.case])
        step = acceptance.child(argv, ROOT, out, "auth-revocation", runtime)
        report["steps"].append(step)
        counts = step.get("counts")
        if (step["rc"] != 0 or step.get("marker_error") or not counts
                or not counts["successful"] or not counts["counts_reconciled"]
                or counts["tests"] < 1 or counts["skipped"]):
            raise RuntimeError("auth_revocation_test_failed")
        report["verdict"] = "PASS"
    except Exception as exc:
        report["first_cause"] = {"type": type(exc).__name__, "code": str(exc)}
    finally:
        after = acceptance.source_manifest(ROOT)
        report["source_drift"] = sorted(
            name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
        report["source_sha256"] = bootstrap.digest(before)
        report["source_after_sha256"] = bootstrap.digest(after)
        if report["source_drift"]:
            report["verdict"] = "FAIL"
        counts = report["steps"][0].get("counts") if report["steps"] else None
        report["totals"] = ({key: counts[key] for key in
                             ("tests", "passed", "failures", "errors")} | {
                                 "skipped": len(counts["skipped"])} if counts else None)
        report["finished_at"] = now()
        bootstrap.exclusive_json(out / "result.json", report)
        line = (f"AUTH_REVOCATION_ACCEPTANCE verdict={report['verdict']} "
                f"counts={json.dumps(report['totals'], sort_keys=True)} "
                f"finished_at={report['finished_at']} evidence={out}")
        bootstrap.exclusive_bytes(out / "acceptance.log", (line + "\n").encode())
        print(line, flush=True)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(APP))
    raise SystemExit(main())
