#!/usr/bin/env python3
"""Independent CLI fault controls for discovery terminal evidence, not product tests."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import errno
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
OLD_TERMINAL = Path("/data/dev/wt-opencad-sqlite-discovery-terminal-fix-20261012/"
                    "tools/test_sqlite_discovery_terminal.py")
FAULTS = ("first-and-after", "after-source", "before-source", "log-open",
          "log-close", "log-read", "completion-log", "result-publication",
          "first-and-publication", "stdout", "partial-counts", "test-errors-and-close", "positive")
SUMMARY_FAULTS = ("before-source", "after-source", "first-and-after", "result-write",
                  "first-and-write", "log-read", "partial-counts", "positive")
OUT = None
LOCAL = None


def summary_actor(args):
    """Fault the real selftest entry, with a tiny suite to avoid recursive actors."""
    class SummaryControls(unittest.TestCase):
        def test_1(self):
            if args.summary_actor in ("first-and-after", "first-and-write"):
                raise ValueError("injected-summary-primary")
            self.assertEqual(2 + 2, 4)

        def test_2(self):
            self.assertEqual(b"retained", b"retained")

    tool = args.tool.resolve()
    if tool not in (Path(__file__).resolve(), OLD_TERMINAL.resolve()):
        raise ValueError("summary actor outside authorized terminal source trees")
    spec = importlib.util.spec_from_file_location("summary_fault_subject", tool)
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)
    original_binding = subject.D.binding
    original_write = subject.A.bootstrap.exclusive_json
    original_read = subject.A.bootstrap.regular_bytes
    original_run = unittest.TextTestRunner.run
    out = args.out.resolve()
    calls = 0

    def read_source():
        nonlocal calls
        calls += 1
        if (args.summary_actor == "before-source" and calls == 1
                or args.summary_actor in ("after-source", "first-and-after") and calls == 2):
            raise OSError(errno.EIO, "injected-summary-source-" + str(calls))
        return original_binding()

    def write_result(path, report):
        if path == out / "result.json" and args.summary_actor in (
                "result-write", "first-and-write"):
            with path.open("xb") as handle:
                handle.write(b'{"partial":')
            raise OSError(errno.EIO, "injected-summary-result-write")
        return original_write(path, report)

    def read_log(path):
        if args.summary_actor == "log-read" and path == out / "selftest.log":
            raise OSError(errno.EIO, "injected-summary-log-read")
        return original_read(path)

    def interrupted(runner, suite):
        if args.summary_actor != "partial-counts":
            return original_run(runner, suite)
        result = runner._makeResult()
        suite._tests[0](result)
        raise OSError(errno.EIO, "injected-summary-partial")

    with patch.object(subject, "TerminalControls", SummaryControls), \
            patch.object(subject.D, "binding", read_source), \
            patch.object(subject.A.bootstrap, "exclusive_json", write_result), \
            patch.object(subject.A.bootstrap, "regular_bytes", read_log), \
            patch.object(unittest.TextTestRunner, "run", interrupted):
        return subject.selftest(argparse.Namespace(
            out=args.out, runtime_root=args.runtime_root, before_red=False,
            summary_before_red=False))


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
    def check_summary(self, fault, *, old=False):
        out = OUT / ("summary-" + fault)
        runtime = LOCAL / ("summary-" + fault)
        log = OUT / ("summary-" + fault + ".log")
        argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                "--summary-actor", fault, "--out", str(out),
                "--runtime-root", str(runtime), "--tool",
                str(OLD_TERMINAL if old else Path(__file__).resolve())]
        with log.open("xb") as handle:
            process = subprocess.run(argv, cwd=ROOT, stdout=handle,
                                     stderr=subprocess.STDOUT,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            handle.write(f"\nSUMMARY_ORIGINAL_RC rc={process.returncode}\n".encode())
        text = log.read_text()
        self.assertEqual(process.returncode, 0 if fault == "positive" else 1, text)
        if old:
            self.assertIn("injected-summary-source-2", text)
            self.assertFalse((out / "result.json").exists())
            self.assertIn("Ran 2 tests", (out / "selftest.log").read_text())
            return None
        result = out / ("terminal-failure.json" if "write" in fault else "result.json")
        report = json.loads(result.read_text())
        self.assertEqual(report["actual_rc"], process.returncode)
        self.assertEqual(report["verdict"], "PASS" if fault == "positive" else "FAIL")
        if "write" in fault:
            self.assertEqual((out / "result.json").read_bytes(), b'{"partial":')
        return report

    def test_summary_source_eio_retains_complete_counts(self):
        report = self.check_summary("after-source")
        self.assertEqual(report["first_cause"]["phase"], "source-after")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue(report["counts_complete"])
        self.assertIsNone(report["source_after"])
        self.assertIsNone(report["source_drift"])

    def test_summary_first_and_source_eio_keep_chronology(self):
        report = self.check_summary("first-and-after")
        self.assertIn("injected-summary-primary", report["first_cause"]["detail"])
        self.assertIn("source-after", [e["phase"] for e in report["secondary_errors"]])
        self.assertEqual(report["counts"]["errors"], 1)
        self.assertEqual(report["counts"]["passed"], 1)

    def test_summary_initial_source_eio_is_null_not_empty(self):
        report = self.check_summary("before-source")
        self.assertIn("injected-summary-source-1", report["first_cause"]["detail"])
        for key in ("source_before", "source_drift", "source_sha256", "counts", "tests"):
            self.assertIsNone(report[key])
        self.assertFalse(report["counts_complete"])

    def test_summary_write_eio_retains_counts_and_partial_bytes(self):
        report = self.check_summary("result-write")
        self.assertEqual(report["first_cause"]["phase"], "result-publication")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertTrue(report["counts_complete"])

    def test_summary_first_and_write_eio_keep_primary(self):
        report = self.check_summary("first-and-write")
        self.assertIn("injected-summary-primary", report["first_cause"]["detail"])
        self.assertIn("result-publication", [e["phase"] for e in report["secondary_errors"]])
        self.assertEqual(report["counts"]["errors"], 1)

    def test_summary_log_read_eio_retains_counts_with_null_hash(self):
        report = self.check_summary("log-read")
        self.assertEqual(report["first_cause"]["phase"], "selftest-log-read")
        self.assertEqual(report["counts"]["passed"], 2)
        self.assertIsNone(report["log_sha256"])
        self.assertIsNone(report["actor_logs"])

    def test_summary_partial_counts_are_not_complete(self):
        report = self.check_summary("partial-counts")
        self.assertIn("injected-summary-partial", report["first_cause"]["detail"])
        self.assertEqual(report["counts"]["passed"], 1)
        self.assertFalse(report["counts_complete"])

    def test_summary_complete_positive(self):
        report = self.check_summary("positive")
        self.assertIsNone(report["first_cause"])
        self.assertEqual(report["secondary_errors"], [])
        self.assertEqual(report["source_before"], report["source_after"])
        self.assertEqual(report["source_drift"], [])
        self.assertEqual(report["counts"]["passed"], 2)

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
    report = {"verdict": "FAIL", "actual_rc": 1, "started_at": D.now(),
              "mode": "terminal-selftest", "source_before": None, "source_after": None,
              "source_drift": None, "source_sha256": None, "first_cause": None,
              "secondary_errors": [], "counts": None, "counts_complete": False,
              "log_sha256": None, "actor_logs": None,
              "tests": None, "passed": None, "failures": None, "errors": None,
              "skipped": None, "runtime_root": str(LOCAL), "production": "NOT_ASSESSED"}
    try:
        LOCAL.mkdir()
        report["source_before"] = D.binding()
        control_class = TerminalControls
        if args.before_red:
            class BeforeRed(unittest.TestCase):
                def test_old_runner_terminal(self):
                    TerminalControls().check_actor("first-and-after", old=True)
            control_class = BeforeRed
        elif args.summary_before_red:
            class SummaryBeforeRed(unittest.TestCase):
                def test_old_selftest_terminal(self):
                    TerminalControls().check_summary("first-and-after", old=True)
            control_class = SummaryBeforeRed
        # Reuse the reviewed runner's chronological counts and terminal protection.
        with patch.object(D, "FixtureControls", control_class):
            D.controls(report, OUT, LOCAL)
        if report["counts"] is not None:
            report.update(report["counts"])
        try:
            report["actor_logs"] = {
                path.name: A.bootstrap.sha_bytes(A.bootstrap.regular_bytes(path))
                for path in sorted(OUT.glob("*.log"))}
        except Exception as exc:
            D.failure(report, "actor-log-read", exc)
        if report["first_cause"] is None:
            report.update(verdict="PASS", actual_rc=0)
    except Exception as exc:
        D.failure(report, "terminal-selftest", exc)
    with patch.object(D, "MARKER", "SQLITE_DISCOVERY_TERMINAL"):
        return D.finalize(report, OUT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selftest", action="store_true")
    group.add_argument("--before-red", action="store_true")
    group.add_argument("--actor", choices=FAULTS)
    group.add_argument("--summary-before-red", action="store_true")
    group.add_argument("--summary-actor", choices=SUMMARY_FAULTS)
    parser.add_argument("--tool", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    if args.tool is None:
        args.tool = Path(__file__) if args.summary_actor else Path(D.__file__)
    try:
        if args.summary_actor:
            return summary_actor(args)
        return actor(args) if args.actor else selftest(args)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
