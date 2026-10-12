#!/usr/bin/env python3
"""Offline terminal fault controls; never launch Chromium, auth or a service.

Each case invokes the real gate entry point in a child CLI, replacing only the
workload and the named I/O fault. These are terminal controls, not browser tests.
Run with fresh external --evidence-dir and local --runtime-root.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stdout
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py"
CASES = (
    "acceptance-late-source", "acceptance-first-late-source",
    "acceptance-log-close", "acceptance-first-log-close",
    "acceptance-artifact-read", "acceptance-first-artifact-read",
    "acceptance-stop", "acceptance-first-stop",
    "selftest-late-source", "selftest-first-late-source", "selftest-log-close",
    "verify-late-source", "verify-first-late-source", "verify-artifact-read",
    "verify-completed-late-source", "acceptance-evidence-scan",
    "acceptance-first-evidence-scan", "acceptance-source-walk",
    "acceptance-valid-candidate",
)


def gate():
    spec = importlib.util.spec_from_file_location("parametric_terminal_gate", TARGET)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def worker(args):
    module = gate()
    out, runtime = args.evidence_dir, args.runtime_root
    case = args.worker
    first = "-first-" in case
    positive = case == "acceptance-valid-candidate"
    fault = "none" if positive else next(
        name for name in ("late-source", "log-close", "artifact-read", "stop",
                          "evidence-scan", "source-walk") if case.endswith(name))
    original_source = module.source
    original_read = Path.read_bytes
    original_open = Path.open
    original_walk = os.walk
    calls = 0

    def source(root):
        nonlocal calls
        calls += 1
        if fault == "late-source" and calls > 1:
            raise OSError("injected-late-source")
        return original_source(root)

    def walk(path, *values, **kwargs):
        if ((fault == "source-walk" and calls > 1 and Path(path) == ROOT) or
                (fault == "evidence-scan" and Path(path) == out.resolve())):
            if kwargs.get("onerror") is not None:
                kwargs["onerror"](OSError("injected-" + fault))
            return iter(())
        return original_walk(path, *values, **kwargs)

    class Log:
        def __init__(self, handle):
            self.handle = handle

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def close(self):
            self.handle.close()
            if fault == "log-close":
                raise OSError("injected-log-close")

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            self.close()

    class Workload:
        def __init__(self, root, evidence, local, binary):
            self.checks, self.cli, self.cleanup_errors, self.secrets = [], [], [], []
            self.log = Log(original_open(evidence / "checks.log", "x"))
            module.write(evidence / "artifact.bin", b"offline-terminal-fixture")
            if positive:
                control = module.Controls("test_terminal_positive")
                control.setUp()
                self.cli = control.valid["cli"]

        def register(self):
            pass

        def start(self):
            pass

        def scenario(self, width, height):
            if positive:
                self.checks.extend({"name": str(width) + "-" + name, "pass": True}
                                   for name in sorted(module.WORKFLOWS))
            else:
                self.checks.append({"name": str(width) + "-offline-terminal", "pass": True})
            if first:
                raise RuntimeError("original-workload-failure")

        def stop(self):
            if fault == "stop":
                raise OSError("injected-stop")

    class Control(unittest.TestCase):
        def test_one(self):
            if first:
                raise RuntimeError("original-control-failure")

        def test_two(self):
            self.assertTrue(True)

    def read_bytes(path):
        if fault == "artifact-read" and path.name == "artifact.bin":
            raise OSError("injected-artifact-read")
        return original_read(path)

    def open_file(path, *values, **kwargs):
        handle = original_open(path, *values, **kwargs)
        if fault == "log-close" and path.name == "tests.log":
            return Log(handle)
        return handle

    def verify(report, root, evidence):
        if first:
            raise RuntimeError("original-verify-failure")
        if fault == "artifact-read":
            (evidence / "artifact.bin").read_bytes()
        if case != "verify-completed-late-source":
            module.source(root)
        return len(report["checks"])

    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "source", source))
        stack.enter_context(patch.object(Path, "read_bytes", read_bytes))
        stack.enter_context(patch.object(os, "walk", walk))
        if case.startswith("acceptance-"):
            stack.enter_context(patch.object(module, "Review", Workload))
            if positive:
                # Only the browser/artifact workload is synthetic; finalization,
                # source capture, candidate equality and terminal I/O are real.
                stack.enter_context(patch.object(
                    module, "validate_bound_report",
                    side_effect=lambda report, *unused, **kwargs: module.terminal(report)))
            rc = module.main(["--evidence-dir", str(out), "--runtime-root", str(runtime)])
            module.write(out.parent / (out.name + "-source-calls.json"), {"calls": calls})
            return rc
        if case.startswith("selftest-"):
            stack.enter_context(patch.object(Path, "open", open_file))
            stack.enter_context(patch.object(
                module.unittest.defaultTestLoader, "loadTestsFromTestCase",
                return_value=unittest.defaultTestLoader.loadTestsFromTestCase(Control)))
            return module.main(["--selftest", "--evidence-dir", str(out),
                                "--runtime-root", str(runtime)])
        original = out.parent / (out.name + "-input")
        original.mkdir()
        module.write(original / "artifact.bin", b"offline-terminal-fixture")
        module.write(original / "result.json", {
            "checks": [{"name": "completed-offline", "pass": True}] * 3,
            "first_cause": "original-verify-failure" if first else None,
            "verdict": "FAIL" if first else "PASS"})
        stack.enter_context(patch.object(module, "verify", verify))
        return module.main(["--verify-evidence", str(original), "--evidence-dir", str(out)])


class TerminalControls(unittest.TestCase):
    def setUp(self):
        if hasattr(self, "evidence_root"):
            return
        declared = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
        self.assertIsNotNone(declared, "discovery_local_runtime_required")
        local = Path(declared)
        self.assertTrue(local.is_dir() and local.absolute().is_relative_to(Path("/data/tmp")))
        self.gate = gate()
        self.runtime_root = Path(tempfile.mkdtemp(prefix="config-terminal-", dir=local))
        self.evidence_root = Path(tempfile.mkdtemp(
            prefix="opencad-config-terminal-discovery-", dir="/hf3fs-jd/data/tmp"))

    def run_case(self, case):
        out = self.evidence_root / case
        command = [sys.executable, "-B", str(Path(__file__).resolve()),
                   "--worker", case, "--evidence-dir", str(out),
                   "--runtime-root", str(self.runtime_root / case)]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        self.gate.write(self.evidence_root / (case + "-command.json"),
                        {"argv": command, "rc": process.returncode})
        self.gate.write(self.evidence_root / (case + "-stdout.log"), process.stdout.encode())
        self.gate.write(self.evidence_root / (case + "-stderr.log"), process.stderr.encode())
        if case == "acceptance-valid-candidate":
            self.assertEqual(process.returncode, 0, process.stderr)
            candidate = (out / "terminal-candidate.json").read_bytes()
            self.assertEqual(candidate, (out / "result.json").read_bytes())
            report = json.loads(candidate)
            self.assertEqual((report["verdict"], len(report["checks"])), ("PASS", 42))
            self.assertIsNone(report["first_cause"])
            self.assertEqual(report["source_before"], report["source_after"])
            self.assertEqual(report["secondary_failures"], [])
            self.assertIn("finished_at", report)
            self.assertNotEqual(report["started_at"], report["finished_at"])
            self.assertEqual(json.loads((self.evidence_root /
                             (case + "-source-calls.json")).read_bytes())["calls"], 3)
            self.assertIn("verdict=PASS", (out / "completion.log").read_text())
            return
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertTrue((out / "result.json").is_file(), "structured FAIL terminal missing")
        report = json.loads((out / "result.json").read_bytes())
        self.assertEqual((report["verdict"], report["intended_rc"]), ("FAIL", 1))
        fault = next(name for name in ("late-source", "log-close", "artifact-read", "stop",
                                      "evidence-scan", "source-walk")
                     if case.endswith(name))
        first = "-first-" in case
        expected = ("original-control-failure" if case.startswith("selftest-") else
                    "original-verify-failure" if case.startswith("verify-") else
                    "original-workload-failure") if first else "injected-" + fault
        self.assertIn(expected, report["first_cause"])
        failures = report["secondary_failures"]
        if first:
            self.assertTrue(any("injected-" + fault in item["cause"] for item in failures))
            self.assertNotIn("injected-" + fault, report["first_cause"])
        if case.startswith("selftest-"):
            self.assertEqual(report["tests"], 2)
            self.assertEqual(report["errors"], int(first))
            self.assertEqual(report["failures"], 0)
        elif case.startswith("verify-"):
            self.assertEqual(report["checks"], 3)
            if case == "verify-completed-late-source":
                self.assertEqual(report["verified_checks"], 3)
            else:
                self.assertIsNone(report["verified_checks"])
        else:
            self.assertEqual(len(report["checks"]), 1 if first else 2)
        if fault in ("late-source", "source-walk"):
            self.assertIsNone(report["source_after"])
            self.assertIsNone(report["source_drift"])
        if fault == "artifact-read" and case.startswith("acceptance-"):
            self.assertFalse(report["artifact_scan_complete"])
            self.assertNotIn("artifact.bin", report["artifacts"])
            self.assertIn("checks.log", report["artifacts"])
        if fault == "evidence-scan":
            self.assertFalse(report["artifact_scan_complete"])
        self.assertIn("verdict=FAIL", (out / "completion.log").read_text())
        self.assertIn(expected, (out / "failure.log").read_text())
        self.assertIn("injected-" + fault, process.stderr)

    def test_uncollected_source_cannot_pass(self):
        report = self.gate.Controls("test_terminal_positive")
        report.setUp()
        report.valid["source_before"] = report.valid["source_after"] = None
        with self.assertRaisesRegex(AssertionError, "source_not_collected"):
            self.gate.terminal(report.valid)

    def test_fault_cases(self):
        for case in CASES:
            with self.subTest(case=case):
                self.run_case(case)

    def test_discovery_collects_this_suite(self):
        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "tools"), pattern=Path(__file__).name,
            top_level_dir=str(ROOT / "tools"))
        self.assertEqual(suite.countTestCases(), 3)


def bind(suite, module, out, runtime):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            bind(test, module, out, runtime)
        else:
            test.gate = module
            test.evidence_root = out
            test.runtime_root = runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--discovery-check", action="store_true")
    parser.add_argument("--expect-head")
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    module = gate()
    out, runtime = args.evidence_dir.resolve(), args.runtime_root.absolute()
    module.require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()),
                   "evidence_not_external_3fs")
    module.require(runtime.is_relative_to(Path("/data/tmp")) and
                   all(not part.is_symlink() for part in (runtime, *runtime.parents)),
                   "runtime_scope_invalid")
    out.mkdir(parents=True, exist_ok=False)
    runtime.mkdir(parents=True, exist_ok=False)
    report = module.report_start(tests=0, cases=len(CASES), fault_cases=len(CASES)-1,
                                 positive_cases=1, failures=0, errors=0,
                                 skipped=0, discovery=args.discovery_check,
                                 browser_launched=False)
    module.capture_source(report, "source_before", ROOT)
    result, log = None, None
    try:
        module.require(report["source_before"] is not None, "source_before_not_collected")
        if args.expect_head:
            module.require(report["source_before"]["head"] == args.expect_head,
                           "expected_head_mismatch")
        suite = (unittest.defaultTestLoader.discover(
            str(ROOT / "tools"), pattern=Path(__file__).name, top_level_dir=str(ROOT / "tools"))
            if args.discovery_check else
            unittest.defaultTestLoader.loadTestsFromTestCase(TerminalControls))
        bind(suite, module, out, runtime)
        log = (out / "tests.log").open("x")
        with redirect_stdout(log):
            result = unittest.TextTestRunner(
                stream=log, verbosity=2, resultclass=module.EvidenceResult).run(suite)
        report.update(tests=result.testsRun, failures=len(result.failures),
                      errors=len(result.errors), skipped=len(result.skipped))
        if result.first_cause:
            module.failure(report, "terminal_controls", result.first_cause)
        elif result.wasSuccessful() and result.testsRun == 3 and not result.skipped:
            report.update(verdict="PASS", intended_rc=0)
        else:
            module.failure(report, "terminal_controls", "incomplete_terminal_controls")
    except Exception:
        module.failure(report, "terminal_control_runner")
    finally:
        if log is not None:
            try:
                log.close()
            except Exception:
                module.failure(report, "log_close")
        module.scan_artifacts(report, out, [])
        module.capture_source(report, "source_after", ROOT)
        module.compare_source(report)
    return module.publish_terminal(report, out, lambda value:
        f"OPENCAD_CONFIG_TERMINAL_CONTROLS verdict={value['verdict']} "
        f"tests={value['tests']} cases={value['cases']} rc={value['intended_rc']}\n")


if __name__ == "__main__":
    raise SystemExit(main())
