#!/usr/bin/env python3
"""Run private-service component tests with immutable external logs and hashes.

This is not the repository-wide CAD regression. It explicitly names the tests
it runs and records all failures, skips and source drift without automatic retry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
TESTS = {
    "http": "test_secure_web.py",
    "gateway": "test_project_gateway.py",
}
SOURCES = {
    "http": ("secure_web.py", "tests/test_secure_web.py"),
    "gateway": ("secure_web.py", "project_gateway.py", "project_store.py",
                "access_control.py", "request_context.py", "tests/test_project_gateway.py",
                "tests/auth_boundary_actor.py"),
}


def fingerprints(names):
    values = {}
    for name in names:
        path = APP / name
        values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def write_exclusive(path, content):
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=TESTS, default="http")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path,
                        default=Path("/data/dev/opencad-private-fixtures"))
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = args.out or Path("/hf3fs-jd/data/tmp/opencad-private-web") / (stamp + "-" + args.stage)
    out = out.resolve()
    if out.is_relative_to(ROOT):
        raise ValueError("Test evidence must be outside the source repository")
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = {"stage": args.stage, "started_at": datetime.now(timezone.utc).isoformat(),
              "scope": list(SOURCES[args.stage]), "verdict": "FAIL"}
    try:
        before = fingerprints(SOURCES[args.stage])
        runtime = args.runtime_root.absolute()
        if runtime.resolve().is_relative_to(ROOT) or runtime.resolve().is_relative_to(out):
            raise ValueError("Local database fixtures must be external and separate from evidence")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(out),
                   OPENCAD_TEST_RUNTIME_ROOT=str(runtime))
        result["runtime_root"] = str(runtime)
        command = [sys.executable, "-B", str(APP / "tests" / TESTS[args.stage]), "-v"]
        result["argv"] = command
        with (out / "tests.log").open("xb") as log:
            process = subprocess.run(command, cwd=APP, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, check=False)
            log.flush()
            os.fsync(log.fileno())
        raw = (out / "tests.log").read_bytes()
        text = raw.decode("utf-8", errors="replace")
        match = re.search(r"Ran (\d+) tests? in", text)
        count = int(match.group(1)) if match else None
        after = fingerprints(SOURCES[args.stage])
        drift = sorted(name for name in before if before[name] != after[name])
        passed = (process.returncode == 0 and count is not None and count > 0
                  and re.search(r"^OK(?: \([^)]*\))?$", text, re.MULTILINE) is not None
                  and not drift)
        result.update(process_rc=process.returncode, tests=count,
                      skipped=len(re.findall(r"\.\.\. skipped ", text)),
                      source_sha256=before, source_drift=drift,
                      log_sha256=hashlib.sha256(raw).hexdigest(),
                      verdict="PASS" if passed else "FAIL")
    except Exception as exc:
        result["cause"] = f"{type(exc).__name__}: {exc}"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["wall_s"] = time.monotonic() - started
    line = (f"OPENCAD_PRIVATE_ACCEPTANCE verdict={result['verdict']} stage={args.stage} "
            f"tests={result.get('tests', 'NOT_CAPTURED')} rc={result.get('process_rc', 'NOT_CAPTURED')} "
            f"collected_at={result['finished_at']}")
    write_exclusive(out / "verdict.log", (line + "\n").encode())
    write_exclusive(out / "result.json", (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    print(line)
    print(f"evidence={out}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
