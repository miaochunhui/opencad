"""Offline negative controls for the real browser identity evidence gate."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "identity_acceptance_tests", ROOT / "tools/browser_identity_acceptance.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class IdentityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="identity-gate-")
        self.out = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        checks = [{"name": f"{width}-{name}", "pass": True}
                  for width in (1440, 390) for name in sorted(REVIEW.WORKFLOWS)]
        source = {"head": "synthetic", "files": {"session": "sha"}, "status": []}
        ready_marker = "OPENCAD_PRIVATE_WEB verdict=READY port=12345"
        artifacts = {"cli.log": (ready_marker + "\n").encode(),
                     "checks.log": b"".join((json.dumps(item) + "\n").encode() for item in checks)}
        for width in (1440, 390):
            for name in ("alice-3d.png", "bob-3d.png", "offline-shield.png",
                         "revoked-shield.png", "restored-bob.png"):
                artifacts[f"{width}/{name}"] = b"\x89PNG\r\n\x1a\n" + b"x" * 1100
            for name in ("cdp-events.json", "browser.log", "downloads/opencad.step",
                         "concurrent-cas.json", "version-witness.json",
                         "download-response-witness.json", "independent.step"):
                artifacts[f"{width}/{name}"] = b"synthetic artifact\n"
            step = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nsynthetic;\nENDSEC;\n"
            artifacts[f"{width}/downloads/opencad.step"] = step
            artifacts[f"{width}/independent.step"] = step
            artifacts[f"{width}/download-response-witness.json"] = json.dumps({
                "sha256": REVIEW.hashlib.sha256(step).hexdigest(),
                "bytes": len(step)}).encode()
        for name, data in artifacts.items():
            path = self.out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.out / "completion.log").write_text(
            f"{REVIEW.MARKER} verdict=PASS checks={len(checks)} rc=0\n")
        self.report = {"verdict": "PASS", "original_rc": 0, "first_cause": None,
                       "cleanup_errors": [], "source_before": source,
                       "source_after": copy.deepcopy(source), "source_drift": [],
                       "runtime_root": "/data/tmp/synthetic",
                       "checks": checks, "cli": {"ready": True, "exit_rc": 0,
                           "argv": [REVIEW.sys.executable, "-B", str(ROOT /
                               "oss-alternatives/apps/opencad/secure_web.py"),
                               "--data-root", "/data/tmp/synthetic", "--host", "127.0.0.1",
                               "--port", "12345", "--public-origin", "http://127.0.0.1:12345"],
                           "startticks": 123, "stop_startticks": 123,
                           "ready_marker": ready_marker, "port": 12345,
                           "origin": "http://127.0.0.1:12345"},
                       "artifacts": {name: {
                           "sha256": REVIEW.hashlib.sha256(data).hexdigest(),
                           "bytes": len(data)} for name, data in artifacts.items()}}
        self.report["cli"]["observed_argv"] = list(self.report["cli"]["argv"])
        self.source_patch = patch.object(REVIEW, "source", return_value=source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def verify(self, report=None):
        return REVIEW.validate_evidence(report or self.report, ROOT, self.out)

    def reject(self, mutate, cause):
        changed = copy.deepcopy(self.report)
        mutate(changed)
        with self.assertRaisesRegex(AssertionError, cause):
            self.verify(changed)

    def test_valid_complete_synthetic_gate(self):
        self.assertEqual(self.verify(), 2 * len(REVIEW.WORKFLOWS))

    def test_original_failure_not_promoted(self):
        self.reject(lambda r: r.update(original_rc=1), "producer_not_pass")

    def test_first_cause_retained(self):
        self.reject(lambda r: r.update(first_cause="original failure"), "producer_failure_present")

    def test_cleanup_failure_not_hidden(self):
        self.reject(lambda r: r.update(cleanup_errors=["stop failure"]), "producer_failure_present")

    def test_source_head_only_drift_rejected(self):
        self.reject(lambda r: r["source_after"].update(head="changed"), "producer_source_drift")

    def test_current_source_drift_rejected(self):
        REVIEW.source.return_value = {"head": "changed"}
        with self.assertRaisesRegex(AssertionError, "current_source_drift"):
            self.verify()

    def test_missing_workflow_rejected(self):
        self.reject(lambda r: r["checks"].pop(), "missing_workflow_checks")

    def test_duplicate_check_rejected(self):
        self.reject(lambda r: r["checks"].append(r["checks"][0]), "invalid_check_ledger")

    def test_failed_check_rejected(self):
        self.reject(lambda r: r["checks"][0].update({"pass": False}), "invalid_check_ledger")

    def test_cli_argv_mismatch_rejected(self):
        self.reject(lambda r: r["cli"].update(observed_argv=["other"]), "invalid_original_cli")

    def test_cli_exit_mismatch_rejected(self):
        self.reject(lambda r: r["cli"].update(exit_rc=-9), "invalid_original_cli")

    def test_artifact_corruption_rejected(self):
        (self.out / "cli.log").write_bytes(b"corrupt")
        with self.assertRaisesRegex(AssertionError, "artifact_hash_mismatch"):
            self.verify()

    def test_empty_artifact_manifest_rejected(self):
        self.reject(lambda r: r.update(artifacts={}), "empty_artifacts")

    def test_artifact_escape_rejected(self):
        self.reject(lambda r: r["artifacts"].update({"../outside": {}}),
                    "artifact_manifest_mismatch")

    def test_completion_marker_mismatch_rejected(self):
        (self.out / "completion.log").write_text("PASS\n")
        with self.assertRaisesRegex(AssertionError, "completion_marker_mismatch"):
            self.verify()

    def test_json_roundtrip(self):
        self.assertEqual(self.verify(json.loads(json.dumps(self.report))), 2 * len(REVIEW.WORKFLOWS))

    def test_missing_screenshot_rejected(self):
        self.reject(lambda r: r["artifacts"].pop("1440/alice-3d.png"), "missing_artifacts")

    def test_unknown_artifact_rejected(self):
        (self.out / "extra.log").write_bytes(b"unbound")
        with self.assertRaisesRegex(AssertionError, "artifact_manifest_mismatch"):
            self.verify()

    def test_arbitrary_cli_not_accepted(self):
        def mutate(report):
            report["cli"]["argv"][2] = "/outside/arbitrary.py"
            report["cli"]["observed_argv"] = list(report["cli"]["argv"])
        self.reject(mutate, "cli_scope_mismatch")

    def test_unlogged_check_facts_rejected(self):
        self.reject(lambda r: r["checks"][0].update(at="fictional"), "check_log_mismatch")

    def test_cli_replacement_rejected(self):
        self.reject(lambda r: r["cli"].update(stop_startticks=456), "cli_identity_changed")

    def test_download_response_witness_rejected(self):
        path = self.out / "1440/download-response-witness.json"
        path.write_text(json.dumps({"sha256": "incorrect", "bytes": 1}))
        self.report["artifacts"]["1440/download-response-witness.json"] = {
            "sha256": REVIEW.hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size}
        with self.assertRaisesRegex(AssertionError, "download_witness_mismatch"):
            self.verify()

class BrowserSocketPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="identity-preflight-")
        self.out = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_actual_failed_runtime_rejected_with_coordinate(self):
        runtime = Path("/data/tmp/opencad-surface-private-release-browser-20261012-v1")
        with self.assertRaisesRegex(AssertionError, "owned_browser_socket_path_too_long"):
            REVIEW.browser_socket_preflight(runtime / "profiles/b-XXXXXXXX", self.out)
        record = json.loads((self.out / "browser-preflight.json").read_text())
        self.assertFalse(record["pass"])
        self.assertGreater(record["path_bytes"], 107)
        self.assertIn(str(runtime), record["socket_path_template"])

    def test_requested_short_runtime_passes(self):
        record = REVIEW.browser_socket_preflight(
            Path("/data/tmp/oc-spr-bi-1012-v2/profiles/b-XXXXXXXX"), self.out)
        self.assertTrue(record["pass"])
        self.assertLessEqual(record["path_bytes"], 107)

    def test_long_runtime_terminal_fail_does_not_launch_cli_or_browser(self):
        with tempfile.TemporaryDirectory(prefix="oc-bi-", dir="/data/tmp") as directory:
            runtime = Path(directory) / ("long-runtime-" * 9)
            evidence_parent = Path(os.environ.get(
                "OPENCAD_EVIDENCE_ROOT", "/hf3fs-jd/data/tmp"))
            self.assertTrue(evidence_parent.resolve().is_relative_to(
                Path("/hf3fs-jd/data/tmp").resolve()))
            out = Path(tempfile.mkdtemp(prefix="identity-long-", dir=evidence_parent)) / "producer"
            args = REVIEW.argparse.Namespace(source_root=ROOT, out=out,
                                             runtime_root=runtime, browser="must-not-launch")
            frozen = {"head": "synthetic", "files": {}, "status": []}
            with patch.object(REVIEW, "source", return_value=frozen), \
                    patch.object(REVIEW.Review, "run") as run:
                self.assertEqual(REVIEW.acceptance(args), 1)
                run.assert_not_called()
            report = json.loads((out / "result.json").read_text())
            self.assertTrue(out.resolve().is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()))
            self.assertEqual(report["runtime_root"], str(runtime))
            self.assertEqual(report["verdict"], "FAIL")
            self.assertEqual(report["original_rc"], 1)
            self.assertEqual(report["checks"], [])
            self.assertEqual(report["cli"], {})
            self.assertTrue(report["first_cause"].startswith("owned_browser_socket_path_too_long"))
            self.assertIn("owned_browser_socket_path_too_long", (out / "failure.log").read_text())
            self.assertEqual((out / "completion.log").read_text(),
                             f"{REVIEW.MARKER} verdict=FAIL checks=0 rc=1\n")
            self.assertIn("browser-preflight.json", report["artifacts"])

    def test_exact_boundary_and_encoded_bytes(self):
        suffix = "/org.chromium.Chromium.XXXXXX/SingletonSocket"
        profile = "/" + "x" * (107 - len(suffix) - 1)
        self.assertTrue(REVIEW.browser_socket_preflight(Path(profile), self.out)["pass"])
        other = self.out / "overflow"
        other.mkdir()
        with self.assertRaisesRegex(AssertionError, "108>107"):
            REVIEW.browser_socket_preflight(Path(profile + "x"), other)
        encoded = self.out / "encoded"
        encoded.mkdir()
        with self.assertRaisesRegex(AssertionError, "owned_browser_socket_path_too_long"):
            REVIEW.browser_socket_preflight(Path("/" + "\u4e2d" * 25), encoded)

    def test_real_unix_socket_accepts_short_and_rejects_long(self):
        with tempfile.TemporaryDirectory(prefix="oc-bi-", dir="/data/tmp") as directory:
            path = Path(directory) / "org.chromium.Chromium.XXXXXX/SingletonSocket"
            path.parent.mkdir()
            with socket.socket(socket.AF_UNIX) as actor:
                actor.bind(str(path))
                self.assertTrue(path.exists())
            long_path = Path(directory) / ("long-runtime-" * 9) / "SingletonSocket"
            long_path.parent.mkdir()
            self.assertGreaterEqual(len(os.fsencode(long_path)), 108)
            with socket.socket(socket.AF_UNIX) as actor:
                with self.assertRaises(OSError):
                    actor.bind(str(long_path))


if __name__ == "__main__":
    unittest.main()
