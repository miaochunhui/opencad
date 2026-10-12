#!/usr/bin/env python3
"""Source-bound discovery fixture regression; evidence on 3FS, SQLite local.

Run --before-red before changing the three fixture modules. Then --selftest,
--targeted and --web against one frozen source. No original evidence is edited.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout

import acceptance as A

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
ORIGINAL = Path("/hf3fs-jd/data/tmp/opencad-config-integration-all-20261012-v1")
MARKER = "SQLITE_DISCOVERY_FIXTURES"
CASES = (
    ("drawing", "tests.test_industrial_drawing.IndustrialDrawingTests."
     "test_empty_read_does_not_create_drawing"),
    ("store", "tests.test_project_store.StoreTests.test_local_module_and_real_sqlite_binding"),
    ("socket", "tests.test_project_session_browser.BrowserSocketPreflightTests."
     "test_long_runtime_terminal_fail_does_not_launch_cli_or_browser"),
)


def fixture_runtime(value: Path | None = None) -> Path:
    """Use the existing guard; explicit roots cannot bypass an active declaration."""
    if value is None:
        return A.configure_runtime().root
    candidate = A.local_runtime_path(value)
    if (getattr(sys, "_opencad_acceptance_sqlite_guard", None) is not None
            or "OPENCAD_TEST_RUNTIME_ROOT" in os.environ):
        guard = A.configure_runtime()
        if not candidate.is_relative_to(guard.root):
            raise ValueError("fixture runtime outside the declared local runtime")
        if not candidate.exists():
            candidate.mkdir()
        return candidate
    candidate = A.local_runtime_path(candidate, short=True)
    if not candidate.exists():
        candidate.mkdir()
    return A.configure_runtime(candidate).root


def now():
    return datetime.now(timezone.utc).isoformat()


def binding():
    return {
        "head": subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True).strip(),
        "status": subprocess.check_output(
            ["git", "--no-replace-objects", "-C", str(ROOT), "status", "--porcelain"],
            text=True),
        "files": A.source_manifest(ROOT),
    }


def require(condition, cause):
    if not condition:
        raise AssertionError(cause)


def failure(report, phase, exc):
    cause = {"phase": phase, "type": type(exc).__name__, "detail": str(exc),
             "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    report.update(verdict="FAIL", actual_rc=1)
    if report["first_cause"] is None:
        report["first_cause"] = cause
    else:
        report["secondary_errors"].append(cause)


class ObservedResult(A.CountedResult):
    first_problem = None

    def problem(self, test, err):
        if self.first_problem is None:
            self.first_problem = {"phase": "selftest", "test": test.id(),
                                  "detail": self._exc_info_to_string(err, test)}

    def addFailure(self, test, err):
        self.problem(test, err)
        super().addFailure(test, err)

    def addError(self, test, err):
        self.problem(test, err)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.problem(subtest, err)
        super().addSubTest(test, subtest, err)


class ObservedRunner(unittest.TextTestRunner):
    observed_result = None

    def _makeResult(self):
        self.observed_result = super()._makeResult()
        return self.observed_result


def controls(report, out, runtime):
    global CONTROL_OUT, CONTROL_RUNTIME
    CONTROL_OUT, CONTROL_RUNTIME = out, runtime
    runner = None
    handle = None
    try:
        handle = (out / "selftest.log").open("x")
        with redirect_stdout(handle), redirect_stderr(handle):
            runner = ObservedRunner(verbosity=2, resultclass=ObservedResult)
            try:
                result = runner.run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(FixtureControls))
                report["counts_complete"] = True
            except Exception as exc:
                result = runner.observed_result
                if result is not None and result.first_problem is not None:
                    report["first_cause"] = result.first_problem
                failure(report, "selftest-run", exc)
            finally:
                result = runner.observed_result
                if result is not None:
                    report["counts"] = {
                        "tests": result.testsRun, "passed": result.passed,
                        "failures": len(result.failures), "errors": len(result.errors),
                        "skipped": len(result.skipped)}
                    if result.first_problem is not None and report["first_cause"] is None:
                        report["first_cause"] = result.first_problem
            if report["counts_complete"]:
                require(result.wasSuccessful() and result.testsRun > 0 and not result.skipped,
                        "fixture_selftest_failed")
    except Exception as exc:
        failure(report, "selftest-log/run", exc)
    finally:
        if handle is not None:
            for phase, action in (("selftest-log-flush", handle.flush),
                                  ("selftest-log-close", handle.close)):
                try:
                    action()
                except Exception as exc:
                    failure(report, phase, exc)
        try:
            report["log_sha256"] = A.bootstrap.sha_bytes(
                A.bootstrap.regular_bytes(out / "selftest.log"))
        except Exception as exc:
            failure(report, "selftest-log-read", exc)


def completion(report, out):
    handle = None
    try:
        handle = (out / "completion.log").open("x")
        handle.write(f"{MARKER} verdict={report['verdict']} mode={report['mode']} "
                     f"rc={report['actual_rc']} evidence={out}\n")
        handle.flush()
    except Exception as exc:
        failure(report, "completion-log", exc)
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception as exc:
                failure(report, "completion-log-close", exc)


def finalize(report, out):
    before = report["source_before"]
    try:
        after = binding()
        report["source_after"] = after
        if before is not None:
            report["source_drift"] = [key for key in before if before[key] != after[key]]
            if report["source_drift"]:
                failure(report, "source-drift", ValueError("source_drift"))
    except Exception as exc:
        failure(report, "source-after", exc)
    if before is not None:
        try:
            report["source_sha256"] = A.bootstrap.digest(before["files"])
        except Exception as exc:
            failure(report, "source-digest", exc)
    # Stdout cannot advertise PASS before immutable result publication succeeds.
    try:
        print(f"{MARKER} publication_pending mode={report['mode']} evidence={out}", flush=True)
    except Exception as exc:
        failure(report, "terminal-output", exc)
    completion(report, out)
    report["finished_at"] = now()
    try:
        A.bootstrap.exclusive_json(out / "result.json", report)
    except Exception as exc:
        failure(report, "result-publication", exc)
        try:
            print(f"{MARKER} verdict=FAIL rc=1 report=" + json.dumps(report),
                  file=sys.stderr, flush=True)
        except Exception as output_error:
            failure(report, "failure-output", output_error)
        try:
            A.bootstrap.exclusive_json(out / "terminal-failure.json", report)
        except Exception as evidence_error:
            failure(report, "failure-publication", evidence_error)
            try:
                print(json.dumps(report), file=sys.stderr, flush=True)
            except Exception:
                # Every authorized evidence/output channel has already failed.
                return 1
    return report["actual_rc"]


def original_failure():
    raw = A.bootstrap.regular_bytes(ORIGINAL / "result.json")
    report = json.loads(raw)
    web = next(step for step in report["steps"] if step["label"] == "web")
    core = next(step for step in report["steps"] if step["label"] == "cadcore")
    log = A.bootstrap.regular_bytes(ORIGINAL / "web.log")
    counts = web["counts"]
    require(report["verdict"] == "FAIL" and report["actual_rc"] == 1
            and not report["source_drift"], "original_failure_binding")
    require(web["rc"] == 1 and counts["tests"] == 589 and counts["passed"] == 520
            and counts["failures"] == 1 and counts["errors"] == 68,
            "original_web_counts_changed")
    require(core["rc"] == 0 and core["counts"]["tests"] == 1761
            and core["counts"]["passed"] == 1748
            and len(core["counts"]["skipped"]) == 13, "original_core_counts_changed")
    require(A.bootstrap.sha_bytes(log) == web["log_sha256"], "original_web_log_sha")
    text = log.decode()
    require(text.count("ERROR: ") == 68
            and text.count("ERROR: test_") == 68
            and "external_fixture_roots_required" in text, "original_first_cause_missing")
    return {"path": str(ORIGINAL / "result.json"), "sha256": A.bootstrap.sha_bytes(raw),
            "web_log_sha256": A.bootstrap.sha_bytes(log),
            "actual_rc": report["actual_rc"], "web_actual_rc": web["rc"],
            "web": {key: counts[key] for key in ("tests", "passed", "failures", "errors")},
            "core": {"tests": 1761, "passed": 1748, "original_skips": 13}}


def probe(args):
    try:
        parent = fixture_runtime(args.fixture_root)
        if args.probe == "tempfile-drift":
            tempfile.tempdir = "/hf3fs-jd/data/tmp"
        directory = Path(tempfile.mkdtemp(prefix="probe-", dir=parent))
        import sqlite3
        with sqlite3.connect(directory / "probe.sqlite3") as conn:
            conn.execute("CREATE TABLE witness(value INTEGER)")
            conn.execute("INSERT INTO witness VALUES (17)")
            require(conn.execute("SELECT value FROM witness").fetchone() == (17,),
                    "real_sqlite_witness")
        print("FIXTURE_PROBE verdict=PASS root=" + str(parent), flush=True)
        A.runtime_marker(sys._opencad_acceptance_sqlite_guard)
        return 0
    except Exception:
        traceback.print_exc()
        print("FIXTURE_PROBE verdict=FAIL", flush=True)
        return 1


CONTROL_OUT = None
CONTROL_RUNTIME = None


class FixtureControls(unittest.TestCase):
    def actor(self, name, *, explicit=None, declaration=None, cause=None, drift=False):
        local = CONTROL_RUNTIME / name
        local.mkdir()
        env = dict(os.environ, OPENCAD_TEST_RUNTIME_ROOT=str(local), TMPDIR=str(local),
                   TMP=str(local), TEMP=str(local), PYTHONDONTWRITEBYTECODE="1")
        if declaration is not None:
            env["OPENCAD_TEST_RUNTIME_ROOT"] = declaration
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--probe",
                "tempfile-drift" if drift else "normal"]
        if explicit is not None:
            argv += ["--fixture-root", str(explicit)]
        log = CONTROL_OUT / (name + ".log")
        with log.open("xb") as handle:
            process = subprocess.run(argv, cwd=ROOT, env=env, stdout=handle,
                                     stderr=subprocess.STDOUT)
            handle.write(f"\nPROBE_ACTUAL_RC rc={process.returncode}\n".encode())
        text = log.read_text()
        self.assertEqual(process.returncode, 1 if cause else 0, text)
        self.assertIn("FIXTURE_PROBE verdict=" + ("FAIL" if cause else "PASS"), text)
        if cause:
            self.assertIn(cause, text)
        else:
            records = [json.loads(line.removeprefix("INDEPENDENT_RUNTIME_BINDING "))
                       for line in text.splitlines()
                       if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["root"], str(local))
            self.assertEqual(records[0]["sqlite_opens"], 1)
            self.assertEqual(records[0]["rejected"], [])
        return local

    def test_real_declared_sqlite_positive(self):
        self.actor("declared-positive")

    def test_explicit_child_stays_within_declaration(self):
        name = "explicit-child"
        self.actor(name, explicit=CONTROL_RUNTIME / name / "child")

    def test_hf3fs_explicit_root_rejected_without_database(self):
        path = CONTROL_OUT / "must-not-create"
        self.actor("hf3fs-negative", explicit=path, cause="absolute /data/tmp child")
        self.assertFalse(path.exists())

    def test_empty_declaration_is_not_defaulted(self):
        self.actor("empty-negative", declaration="", cause="empty explicit SQLite")

    def test_other_local_root_not_authorized(self):
        other = CONTROL_RUNTIME / "other"
        other.mkdir()
        self.actor("outside-negative", explicit=other,
                   cause="outside the declared local runtime")
        self.assertEqual(list(other.iterdir()), [])

    def test_symlink_alias_rejected_without_database(self):
        target = CONTROL_RUNTIME / "link-target"
        target.mkdir()
        alias = CONTROL_RUNTIME / "link-alias"
        alias.symlink_to(target, target_is_directory=True)
        self.actor("symlink-negative", explicit=alias, cause="symlink SQLite runtime")
        self.assertEqual(list(target.iterdir()), [])

    def test_dotdot_is_not_resolved_away(self):
        self.actor("dotdot-negative", explicit=CONTROL_RUNTIME / "absent/../other",
                   cause="absolute /data/tmp child")

    def test_real_tempfile_drift_remains_red(self):
        self.actor("tempfile-negative", drift=True,
                   cause="SQLite runtime declaration/tempfile drift")


def run(args):
    out = args.out.resolve()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()), "external_3fs_required")
    runtime = A.local_runtime_path(args.runtime_root, fresh=True, short=True)
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "actual_rc": 1, "started_at": now(),
              "mode": args.mode, "source_before": None, "source_after": None,
              "source_drift": None, "source_sha256": None,
              "counts": None, "counts_complete": False, "log_sha256": None,
              "steps": [], "first_cause": None, "secondary_errors": [],
              "runtime_root": str(runtime), "production": "NOT_ASSESSED"}
    try:
        runtime.mkdir()
        report["source_before"] = binding()
        report["retained_original_failure"] = original_failure()
        if args.mode == "selftest":
            controls(report, out, runtime)
        else:
            os.environ["OPENCAD_DRAWING_DEPENDENCY_ROOT"] = str(ROOT)
            os.environ["OPENCAD_DRAWING_TEST_OUT"] = str(out / "drawing-artifacts")
            cases = CASES if args.mode == "before-red" else (
                (("web", None),) if args.mode == "web" else
                tuple((name, None) for name in
                      ("industrial_drawing", "project_store", "project_session_browser")))
            for label, case in cases:
                argv = [str(ROOT / "tools/acceptance.py"), "--suite", str(APP / "tests"),
                        "--top", str(APP)]
                if case:
                    argv += ["--case", case]
                elif args.mode != "web":
                    argv += ["--pattern", "test_" + label + ".py"]
                step = A.child(argv, ROOT, out, label, runtime)
                report["steps"].append(step)
                counts = step["counts"]
                if args.mode == "before-red":
                    log = (out / (label + ".log")).read_text()
                    require(step["rc"] == 1 and counts is not None and counts["tests"] == 1
                            and counts["passed"] == 0
                            and counts["errors"] == (label != "socket")
                            and counts["failures"] == (label == "socket"),
                            "repair_before_negative_not_red:" + label)
                    require(("external_fixture_roots_required" if label == "socket" else
                             "SQLite runtime must be an absolute /data/tmp child") in log,
                            "repair_before_first_cause_changed:" + label)
                else:
                    require(step["rc"] == 0 and not step.get("marker_error")
                            and not step.get("runtime_error") and counts
                            and counts["successful"] and counts["tests"] > 0
                            and not counts["skipped"], "failed_discovery_step:" + label)
        if report["first_cause"] is None:
            report.update(verdict="PASS", actual_rc=0)
    except Exception as exc:
        failure(report, "run", exc)
    return finalize(report, out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ("before-red", "selftest", "targeted", "web"):
        group.add_argument("--" + mode, action="store_const", const=mode, dest="mode")
    group.add_argument("--probe", choices=("normal", "tempfile-drift"))
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args()
    if args.probe:
        return probe(args)
    if args.out is None or args.runtime_root is None:
        parser.error("regression requires --out and --runtime-root")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
