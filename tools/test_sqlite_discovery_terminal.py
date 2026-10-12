#!/usr/bin/env python3
"""Independent CLI fault controls for discovery terminal evidence, not product tests."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import unittest
from unittest.mock import patch

import acceptance as A
import test_sqlite_discovery_fixtures as D

ROOT = Path(__file__).resolve().parents[1]
OLD_TOOL = Path("/data/dev/wt-opencad-sqlite-discovery-fixture-fix-20261012/"
                "tools/test_sqlite_discovery_fixtures.py")
FAULTS = ("first-and-after", "after-source", "before-source", "log-open",
          "log-close", "log-read", "completion-log", "result-publication",
          "first-and-publication", "stdout", "partial-counts", "test-errors-and-close", "positive")
OUT = None
LOCAL = None


def actor(args):
    class TwoControls(unittest.TestCase):
        def test_1(self):
            self.assertEqual(17 + 1, 18)

        def test_2(self):
            self.assertEqual(b"unchanged", b"unchanged")

    class ErrorControls(unittest.TestCase):
        def test_1(self):
            raise RuntimeError("injected-first-test-error")

        def test_2(self):
            self.fail("injected-later-test-failure")

    tool = args.tool.resolve()
    if tool not in (Path(D.__file__).resolve(), OLD_TOOL.resolve()):
        raise ValueError("fault actor tool outside the two authorized source trees")
    spec = importlib.util.spec_from_file_location("discovery_fault_subject", tool)
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    original_binding = subject.binding
    original_evidence = subject.original_failure
    original_open = Path.open
    original_read = subject.A.bootstrap.regular_bytes
    original_print = print
    original_run = unittest.TextTestRunner.run
    calls = 0
    selected_out = args.out.resolve()
    failed_close = False

    def read_source():
        nonlocal calls
        calls += 1
        if args.actor == "before-source" and calls == 1:
            raise OSError("injected-before-source")
        if args.actor in ("first-and-after", "after-source") and calls == 2:
            raise OSError("injected-after-source")
        return original_binding()

    def evidence():
        if args.actor in ("result-publication", "first-and-publication"):
            with original_open(selected_out / "result.json", "x") as handle:
                handle.write("immutable-sentinel")
        if args.actor == "completion-log":
            (selected_out / "completion.log").mkdir()
        if args.actor in ("first-and-after", "first-and-publication"):
            raise ValueError("injected-primary")
        return original_evidence()

    class CloseFault:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            nonlocal failed_close
            self.handle.close()
            if not failed_close:
                failed_close = True
                raise OSError("injected-log-close")

    def open_log(path, *values, **kwargs):
        if path == selected_out / "selftest.log":
            if args.actor == "log-open":
                raise OSError("injected-log-open")
            if args.actor in ("log-close", "test-errors-and-close"):
                return CloseFault(original_open(path, *values, **kwargs))
        return original_open(path, *values, **kwargs)

    def read_log(path):
        if args.actor == "log-read" and path == selected_out / "selftest.log":
            raise OSError("injected-log-read")
        return original_read(path)

    def terminal(*values, **kwargs):
        if args.actor == "stdout" and kwargs.get("file") is not sys.stderr:
            raise BrokenPipeError("injected-stdout")
        return original_print(*values, **kwargs)

    def interrupted(runner, suite):
        if args.actor != "partial-counts":
            return original_run(runner, suite)
        result = runner._makeResult()
        suite._tests[0](result)
        raise OSError("injected-after-one-control")

    with patch.object(subject, "binding", read_source), \
            patch.object(subject, "original_failure", evidence), \
            patch.object(subject, "FixtureControls",
                         ErrorControls if args.actor == "test-errors-and-close" else TwoControls), \
            patch.object(Path, "open", open_log), \
            patch.object(subject.A.bootstrap, "regular_bytes", read_log), \
            patch("builtins.print", terminal), \
            patch.object(unittest.TextTestRunner, "run", interrupted):
        return subject.run(argparse.Namespace(
            mode="selftest", out=args.out, runtime_root=args.runtime_root))


class TerminalControls(unittest.TestCase):
    def check_actor(self, fault, *, old=False):
        out = OUT / fault
        runtime = LOCAL / fault
        log = OUT / (fault + ".log")
        argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                "--actor", fault, "--out", str(out), "--runtime-root", str(runtime),
                "--tool", str(OLD_TOOL if old else Path(D.__file__).resolve())]
        with log.open("xb") as handle:
            process = subprocess.run(argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            handle.write(f"\nACTOR_ORIGINAL_RC rc={process.returncode}\n".encode())
        text = log.read_text()
        if old:
            self.assertEqual(process.returncode, 1, text)
            self.assertIn("injected-after-source", text)
            self.assertFalse((out / "result.json").exists())
            self.assertNotIn("injected-primary", text)
            return None
        self.assertEqual(process.returncode, 0 if fault == "positive" else 1, text)
        result = out / ("terminal-failure.json" if "publication" in fault else "result.json")
        report = json.loads(result.read_text())
        self.assertEqual(report["actual_rc"], process.returncode)
        self.assertEqual(report["verdict"], "PASS" if fault == "positive" else "FAIL")
        if fault == "positive":
            self.assertIsNone(report["first_cause"])
            self.assertEqual(report["secondary_errors"], [])
        else:
            self.assertIsNotNone(report["first_cause"])
        return report

    def test_first_and_later_source_keep_primary(self):
        report = self.check_actor("first-and-after")
        self.assertEqual(report["first_cause"]["detail"], "injected-primary")
        self.assertEqual(report["secondary_errors"][0]["detail"], "injected-after-source")
        self.assertIsNone(report["source_after"])
        self.assertIsNone(report["source_drift"])
        self.assertIsNone(report["counts"])

    def test_completed_counts_survive_after_source_failure(self):
        report = self.check_actor("after-source")
        self.assertEqual(report["first_cause"]["phase"], "source-after")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue(report["counts_complete"])
        self.assertIsNone(report["source_after"])
        self.assertIsNone(report["source_drift"])

    def test_before_source_failure_is_explicit_unverified(self):
        report = self.check_actor("before-source")
        self.assertEqual(report["first_cause"]["detail"], "injected-before-source")
        self.assertIsNone(report["source_before"])
        self.assertIsNone(report["source_drift"])
        self.assertIsNone(report["source_sha256"])
        self.assertIsNone(report["counts"])

    def test_log_open_failure_is_not_empty_pass(self):
        report = self.check_actor("log-open")
        self.assertEqual(report["first_cause"]["detail"], "injected-log-open")
        self.assertIsNone(report["counts"])
        self.assertFalse(report["counts_complete"])
        self.assertEqual(report["secondary_errors"][0]["phase"], "selftest-log-read")

    def test_log_close_failure_preserves_completed_counts(self):
        report = self.check_actor("log-close")
        self.assertEqual(report["first_cause"]["detail"], "injected-log-close")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue(report["counts_complete"])
        self.assertIsNotNone(report["log_sha256"])

    def test_log_read_failure_preserves_completed_counts(self):
        report = self.check_actor("log-read")
        self.assertEqual(report["first_cause"]["detail"], "injected-log-read")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertIsNone(report["log_sha256"])

    def test_completion_log_os_failure_refuses_pass(self):
        report = self.check_actor("completion-log")
        self.assertEqual(report["first_cause"]["phase"], "completion-log")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue((OUT / "completion-log/completion.log").is_dir())

    def test_exclusive_result_failure_preserves_old_bytes(self):
        report = self.check_actor("result-publication")
        self.assertEqual(report["first_cause"]["phase"], "result-publication")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertEqual((OUT / "result-publication/result.json").read_text(),
                         "immutable-sentinel")

    def test_first_and_publication_keep_primary(self):
        report = self.check_actor("first-and-publication")
        self.assertEqual(report["first_cause"]["detail"], "injected-primary")
        self.assertEqual(report["secondary_errors"][0]["phase"], "result-publication")
        self.assertEqual((OUT / "first-and-publication/result.json").read_text(),
                         "immutable-sentinel")

    def test_stdout_failure_remains_machine_readable(self):
        report = self.check_actor("stdout")
        self.assertEqual(report["first_cause"]["detail"], "injected-stdout")
        self.assertEqual(report["counts"]["passed"], 2)

    def test_partial_completed_counts_are_not_total_success(self):
        report = self.check_actor("partial-counts")
        self.assertEqual(report["first_cause"]["detail"], "injected-after-one-control")
        self.assertEqual(report["counts"]["tests"], 1)
        self.assertEqual(report["counts"]["passed"], 1)
        self.assertFalse(report["counts_complete"])

    def test_test_error_precedes_later_failure_and_log_close(self):
        report = self.check_actor("test-errors-and-close")
        self.assertTrue(report["first_cause"]["test"].endswith("ErrorControls.test_1"))
        self.assertIn("injected-first-test-error", report["first_cause"]["detail"])
        self.assertEqual(report["counts"]["errors"], 1)
        self.assertEqual(report["counts"]["failures"], 1)
        self.assertEqual(report["counts"]["passed"], 0)
        self.assertIn("injected-log-close",
                      [error["detail"] for error in report["secondary_errors"]])

    def test_terminal_success_positive(self):
        report = self.check_actor("positive")
        self.assertEqual(report["source_before"], report["source_after"])
        self.assertEqual(report["source_drift"], [])
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue(report["counts_complete"])


def selftest(args):
    global OUT, LOCAL
    OUT = args.out.resolve()
    LOCAL = D.A.local_runtime_path(args.runtime_root, fresh=True, short=True)
    if not OUT.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()):
        raise ValueError("external 3FS evidence required")
    OUT.mkdir(parents=True, exist_ok=False)
    LOCAL.mkdir()
    before = D.binding()
    report = {"verdict": "FAIL", "actual_rc": 1, "started_at": D.now(),
              "source_before": before, "first_cause": None}
    with (OUT / "selftest.log").open("x") as handle, \
            redirect_stdout(handle), redirect_stderr(handle):
        if args.before_red:
            suite = unittest.TestSuite()
            control = TerminalControls("test_first_and_later_source_keep_primary")
            suite.addTest(unittest.FunctionTestCase(
                lambda: control.check_actor("first-and-after", old=True)))
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(TerminalControls)
        result = unittest.TextTestRunner(verbosity=2, resultclass=A.CountedResult).run(suite)
    report.update(tests=result.testsRun, passed=result.passed,
                  failures=len(result.failures), errors=len(result.errors),
                  skipped=len(result.skipped))
    failed = result.failures + result.errors
    if failed:
        report["first_cause"] = {"test": str(failed[0][0]), "detail": failed[0][1]}
    after = D.binding()
    report.update(source_after=after,
                  source_drift=[key for key in before if before[key] != after[key]],
                  source_sha256=A.bootstrap.digest(before["files"]),
                  log_sha256=A.bootstrap.sha_bytes((OUT / "selftest.log").read_bytes()),
                  actor_logs={path.name: A.bootstrap.sha_bytes(path.read_bytes())
                              for path in sorted(OUT.glob("*.log"))},
                  finished_at=D.now())
    if result.wasSuccessful() and result.testsRun > 0 and not result.skipped \
            and not report["source_drift"]:
        report.update(verdict="PASS", actual_rc=0)
    A.bootstrap.exclusive_json(OUT / "result.json", report)
    print(f"SQLITE_DISCOVERY_TERMINAL verdict={report['verdict']} "
          f"tests={report['tests']} passed={report['passed']} rc={report['actual_rc']} "
          f"evidence={OUT}", flush=True)
    return report["actual_rc"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selftest", action="store_true")
    group.add_argument("--before-red", action="store_true")
    group.add_argument("--actor", choices=FAULTS)
    parser.add_argument("--tool", type=Path, default=Path(D.__file__))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        return actor(args) if args.actor else selftest(args)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
