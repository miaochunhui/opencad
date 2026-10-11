"""Real HTTP regression for final output paths that collide with CURRENT data.

Checkpoint names deliberately use output extensions: L1 save accepts them.
Workspace, HTTP, IO and restoration are real. Controlled PDF backends inject
returned paths or a real output-guard rejection, including a normal path control.
Each test retains HTTP observations and startup exceptions externally.
The selected source is read-only; this is not a production or power-loss test.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.request
import uuid

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path(
    "/hf3fs-jd/data/tmp/opencad-features-20261010/seat-atomic-review/output-destinations"
)
RUN_DIR = None
SERVER = None
RELEASE = None


def new_run_directory(root):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / (stamp + "-" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "write-probe").touch(exist_ok=False)
    return directory


def source_hashes():
    relative = (
        "apps/opencad/server.py",
        "apps/opencad/release_state.py",
        "packages/cadcore/cadcore/docmodel.py",
        "packages/cadcore/cadcore/ops/output.py",
        "packages/cadcore/cadcore/draw/sheet.py",
    )
    paths = [SOURCE_ROOT / "oss-alternatives" / item for item in relative]
    guard = SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/io/write_guard.py"
    if guard.exists():
        paths.append(guard)
    paths.append(Path(__file__).resolve())
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def load_source():
    global SERVER, RELEASE
    app = SOURCE_ROOT / "oss-alternatives/apps/opencad"
    core = SOURCE_ROOT / "oss-alternatives/packages/cadcore"
    for directory in (app, core):
        sys.path.insert(0, str(directory))
    SERVER = importlib.import_module("server")
    RELEASE = importlib.import_module("release_state")
    for module, expected in ((SERVER, app / "server.py"),
                             (RELEASE, app / "release_state.py")):
        if Path(module.__file__).resolve() != expected.resolve():
            raise AssertionError("foreign regression source: " + module.__file__)


class ReleaseOutputDestinationTests(unittest.TestCase):
    CHECKPOINT_NAMES = {
        "test_publish_merged_child_cannot_overwrite_current_saved_l1": "publish.pdf",
        "test_publish_page_child_cannot_overwrite_current_saved_l1": "page_001.pdf",
        "test_plot_appended_extension_cannot_overwrite_current_saved_l1": "drawing.svg",
        "test_plot_pdf_appended_extension_cannot_overwrite_current_saved_l1": "drawing.pdf",
    }

    @classmethod
    def setUpClass(cls):
        global RUN_DIR
        if RUN_DIR is None:
            RUN_DIR = new_run_directory(EVIDENCE_ROOT)
        cls.before = source_hashes()
        if SERVER is None:
            load_source()

    @classmethod
    def tearDownClass(cls):
        after = source_hashes()
        (RUN_DIR / "source-hashes.json").write_text(
            json.dumps({"before": cls.before, "after": after}, indent=2), encoding="utf-8",
        )
        if cls.before != after:
            raise AssertionError("selected source changed during output destination tests")

    def setUp(self):
        self.directory = RUN_DIR / self._testMethodName
        self.directory.mkdir(parents=True, exist_ok=False)
        self.state_root = (self.directory / "state").resolve()
        self.state_root.mkdir()
        self.core = SERVER._load_cadcore()
        original = SERVER.Workspace(
            self.core, locale="en", standard="iso", out_root=str(self.state_root),
        )
        original.command("add_line", {"p1": [0, 0], "p2": [10_000_000, 5_000_000]})
        self.original = original.state()
        self.saved = self.state_root / self.CHECKPOINT_NAMES.get(
            self._testMethodName, "drawing.json",
        )
        original.command("prepare_release", {
            "path": str(self.saved),
            "expected_document_sha256": RELEASE.digest(self.original["doc"]),
            "expected_locale": self.original["locale"],
            "expected_standard": self.original["standard"],
        })
        self.saved_bytes = self.saved.read_bytes()
        self.assertEqual(self.saved_bytes, self.original["doc"].encode("utf-8"))
        self.httpd = self.build()
        workspace = self.httpd.workspace
        self.assertEqual(workspace.l1_text(), self.original["doc"])
        self.assertFalse(workspace._release_pending)
        self.assertFalse((self.state_root / RELEASE.MARKER).exists())
        self.current = self.state_root / RELEASE.CURRENT
        self.current_bytes = self.current.read_bytes()
        self.assertEqual(json.loads(self.current_bytes)["path"], str(self.saved))
        self.archives = {
            path: path.read_bytes()
            for path in self.state_root.glob(".opencad-release-restored-*.json")
        }
        self.assertEqual(len(self.archives), 1)
        thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        thread.start()

        def stop():
            self.httpd.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "isolated HTTP server did not stop")

        self.addCleanup(stop)
        host, port = self.httpd.server_address
        self.base = "http://%s:%s" % (host, port)
        status, layout = self.command("create_layout", {
            "name": "output-regression", "standard": "iso",
            "language": "en", "size": "A3", "scale": "1:1",
        })
        self.assertEqual(status, 200, layout)
        self.assertTrue(layout.get("ok"), layout)
        self.sheet_uuid = layout["uuid"]
        self.assertTrue(self.sheet_uuid)
        self.canonicalize_fixture()
        self.record("fixture_ready", saved_path=str(self.saved),
                    saved_sha256=hashlib.sha256(self.saved_bytes).hexdigest(),
                    current_sha256=hashlib.sha256(self.current_bytes).hexdigest(),
                    sheet_uuid=self.sheet_uuid)

    def canonicalize_fixture(self):
        """Prime real read diagnostics, then retain exact transaction assertions."""
        workspace = self.httpd.workspace
        before = workspace.state()
        histories = (list(workspace.undo_stack), list(workspace.redo_stack))
        cache = workspace._mesh_cache
        with workspace._lock:
            workspace._restore(before["doc"])
        after = workspace.state()
        original = json.loads(before["doc"])
        restored = json.loads(after["doc"])
        self.assertEqual(
            {key: value for key, value in original.items() if key != "diagnostics"},
            {key: value for key, value in restored.items() if key != "diagnostics"},
        )
        self.assertEqual(
            {key: value for key, value in before.items() if key != "doc"},
            {key: value for key, value in after.items() if key != "doc"},
        )
        previous = original.get("diagnostics", [])
        diagnostics = restored.get("diagnostics", [])
        for diagnostic in previous:
            self.assertIn(diagnostic, diagnostics)
        added = [diagnostic for diagnostic in diagnostics if diagnostic not in previous]
        for diagnostic in added:
            self.assertEqual(diagnostic.get("code"), "unknown_field_preserved", diagnostic)
            self.assertEqual(diagnostic.get("severity"), "warning", diagnostic)
            self.assertTrue(diagnostic.get("path"), diagnostic)
        self.assertEqual((workspace.undo_stack, workspace.redo_stack), histories)
        self.assertIs(workspace._mesh_cache, cache)
        self.assert_checkpoint_unchanged()
        (self.directory / "fixture-read-roundtrip.json").write_text(
            json.dumps({"before": before, "after": after}, indent=2), encoding="utf-8",
        )
        self.record("fixture_read_roundtrip_verified",
                    document_fields_unchanged=True, added_diagnostics=added)

    def build(self):
        server = SERVER.build_server(
            self.core, host="127.0.0.1", port=0, locale="en", standard="iso",
            out_root=str(self.state_root),
        )
        self.addCleanup(server.server_close)
        return server

    def record(self, phase, **fields):
        event = {
            "phase": phase,
            "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **fields,
        }
        with (self.directory / "observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

    def command(self, name, arguments):
        request = urllib.request.Request(
            self.base + "/api/command",
            data=json.dumps({"command": name, "args": arguments}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
        self.record("http_command", command=name, arguments=arguments,
                    status=status, response=payload)
        return status, payload

    def assert_checkpoint_unchanged(self):
        self.assertEqual(self.saved.read_bytes(), self.saved_bytes)
        self.assertEqual(self.current.read_bytes(), self.current_bytes)
        for path, data in self.archives.items():
            self.assertEqual(path.read_bytes(), data)

    def register_controlled_pdf_backend(self, backend):
        """Use the official backend hook and restore only this test registration."""
        sheet = importlib.import_module("cadcore.draw.sheet")
        expected = SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/draw/sheet.py"
        self.assertEqual(Path(sheet.__file__).resolve(), expected.resolve())
        previous = tuple(sheet._PDF_BACKENDS)
        unregister = sheet.register_pdf_backend(backend)

        def restore():
            unregister()
            self.assertEqual(tuple(sheet._PDF_BACKENDS), previous)

        self.addCleanup(restore)
        self.assertIs(sheet._pdf_backend(), backend)
        self.record("controlled_backend_registered", backend=backend.__name__,
                    injection_only=True, backend_writes=False)

    def assert_final_destination_rejected(self, name, arguments):
        workspace = self.httpd.workspace
        before = workspace.state()
        histories = (list(workspace.undo_stack), list(workspace.redo_stack))
        cache = workspace._mesh_cache
        before_names = sorted(path.name for path in self.state_root.iterdir())
        status, response = self.command(name, arguments)
        after = workspace.state()
        (self.directory / "transaction-state.json").write_text(
            json.dumps({"before": before, "after": after}, indent=2), encoding="utf-8",
        )
        observed = self.saved.read_bytes() if self.saved.is_file() else None
        changed = observed != self.saved_bytes
        recovery_error = None
        try:
            recovered = self.build()
        except Exception as exc:
            recovery_error = exc
            self.record(
                "subsequent_real_startup_failed", error_type=type(exc).__name__,
                error=str(exc), context=str(exc.__context__),
                traceback="".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)),
            )
        else:
            self.record("subsequent_real_startup_restored",
                        document_sha256=RELEASE.digest(recovered.workspace.l1_text()))
        self.record(
            "final_destination_observed", command=name, status=status,
            checkpoint_changed=changed,
            checkpoint_exists=observed is not None,
            before_sha256=hashlib.sha256(self.saved_bytes).hexdigest(),
            after_sha256=(hashlib.sha256(observed).hexdigest()
                          if observed is not None else None),
            saved_magic=(observed[:16].decode("latin-1") if observed is not None else None),
            recovery_error_type=(type(recovery_error).__name__
                                 if recovery_error is not None else None),
            document_unchanged=after == before,
            histories_unchanged=(workspace.undo_stack, workspace.redo_stack) == histories,
        )
        self.assertEqual(status, 409, "final output path must be rejected before writing L1")
        self.assertEqual(response.get("error"), "protected_checkpoint")
        self.assertFalse(response.get("ok", True))
        self.assertTrue(response.get("detail"), "rejection must retain a diagnostic")
        self.assert_checkpoint_unchanged()
        self.assertEqual(after, before)
        self.assertEqual((workspace.undo_stack, workspace.redo_stack), histories)
        self.assertIs(workspace._mesh_cache, cache)
        self.assertEqual(sorted(path.name for path in self.state_root.iterdir()), before_names)
        self.assertIsNone(recovery_error, "rejected output must preserve startup recovery")
        self.assertEqual(recovered.workspace.l1_text(), self.original["doc"])

    def test_publish_merged_child_cannot_overwrite_current_saved_l1(self):
        self.assert_final_destination_rejected("publish", {
            "sheet_uuids": [self.sheet_uuid], "fmt": "pdf",
            "out_dir": str(self.state_root), "single_file": True,
        })

    def test_publish_page_child_cannot_overwrite_current_saved_l1(self):
        self.assert_final_destination_rejected("publish", {
            "sheet_uuids": [self.sheet_uuid], "fmt": "pdf",
            "out_dir": str(self.state_root),
        })

    def test_plot_appended_extension_cannot_overwrite_current_saved_l1(self):
        self.assert_final_destination_rejected("plot", {
            "sheet_uuid": self.sheet_uuid, "fmt": "svg",
            "out": str(self.state_root / "drawing"),
        })

    def test_plot_pdf_appended_extension_cannot_overwrite_current_saved_l1(self):
        self.assert_final_destination_rejected("plot_pdf", {
            "sheet_uuid": self.sheet_uuid, "out": str(self.state_root / "drawing"),
        })

    def test_pdf_backend_returned_protected_source_cannot_be_moved(self):
        target = self.state_root / "ordinary-backend.pdf"
        calls = []

        def return_protected_source(svg_text, out_path):
            self.assertIn("<svg", svg_text)
            self.assertEqual(Path(out_path), target)
            self.assert_checkpoint_unchanged()
            calls.append(out_path)
            self.record("backend_returned_protected_source", source=str(self.saved),
                        target=out_path, source_exists=self.saved.is_file(),
                        source_sha256=hashlib.sha256(self.saved.read_bytes()).hexdigest())
            return str(self.saved)

        self.register_controlled_pdf_backend(return_protected_source)
        self.addCleanup(lambda: self.assertEqual(len(calls), 1, "backend was not invoked once"))
        try:
            self.assert_final_destination_rejected("plot_pdf", {
                "sheet_uuid": self.sheet_uuid, "out": str(target),
            })
        finally:
            self.record("backend_move_observed", source_exists=self.saved.is_file(),
                        destination_exists=target.is_file(),
                        destination_contains_checkpoint=(
                            target.is_file() and target.read_bytes() == self.saved_bytes))
        self.assertFalse(target.exists(), "a rejected move must not create its destination")

    def test_pdf_backend_guard_rejection_retains_protected_checkpoint_api_error(self):
        guard = importlib.import_module("cadcore.io.write_guard")
        expected = SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/io/write_guard.py"
        self.assertEqual(Path(guard.__file__).resolve(), expected.resolve())
        target = self.state_root / "ordinary-backend.pdf"
        calls = []

        def reject_protected_output(svg_text, out_path):
            self.assertIn("<svg", svg_text)
            self.assertEqual(Path(out_path), target)
            self.assert_checkpoint_unchanged()
            calls.append(out_path)
            try:
                guard.guard_output(str(self.saved))
            except Exception as exc:
                self.record(
                    "backend_real_guard_rejected", error_type=type(exc).__name__,
                    error=str(exc), traceback="".join(traceback.format_exception(
                        type(exc), exc, exc.__traceback__)),
                )
                raise
            self.fail("real request-local guard allowed a protected checkpoint")

        self.register_controlled_pdf_backend(reject_protected_output)
        self.addCleanup(lambda: self.assertEqual(len(calls), 1, "backend was not invoked once"))
        self.assert_final_destination_rejected("plot_pdf", {
            "sheet_uuid": self.sheet_uuid, "out": str(target),
        })
        self.assertFalse(target.exists(), "a rejected backend must not write output")

    def test_ordinary_independent_output_directory_remains_writable(self):
        directory = self.state_root / "ordinary-output"
        cases = [
            ("plot", {"sheet_uuid": self.sheet_uuid, "fmt": "svg",
                      "out": str(directory / "plot")}, directory / "plot.svg", b"<svg"),
            ("plot_pdf", {"sheet_uuid": self.sheet_uuid,
                          "out": str(directory / "plot-pdf")},
             directory / "plot-pdf.pdf", b"%PDF-"),
            ("publish", {"sheet_uuids": [self.sheet_uuid], "fmt": "pdf",
                         "out_dir": str(directory / "merged"), "single_file": True},
             directory / "merged/publish.pdf", b"%PDF-"),
            ("publish", {"sheet_uuids": [self.sheet_uuid], "fmt": "pdf",
                         "out_dir": str(directory / "pages")},
             directory / "pages/page_001.pdf", b"%PDF-"),
        ]
        for name, arguments, output, magic in cases:
            with self.subTest(command=name, output=output.name):
                status, response = self.command(name, arguments)
                self.assertEqual(status, 200, response)
                self.assertTrue(response.get("ok"), response)
                self.assertTrue(output.is_file(), "positive control did not write real output")
                data = output.read_bytes()
                self.assertGreater(len(data), 100)
                self.assertIn(magic, data[:256])
                self.record("ordinary_output_verified", path=str(output), bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                            magic=data[:16].decode("latin-1"))
                self.assert_checkpoint_unchanged()
        produced = directory / "backend-produced.pdf"
        destination = directory / "backend-normal.pdf"
        pdf_bytes = (directory / "plot-pdf.pdf").read_bytes()
        produced.write_bytes(pdf_bytes)
        self.assertEqual(produced.read_bytes(), pdf_bytes)
        self.assertFalse(destination.exists())
        calls = []

        def return_ordinary_pdf(svg_text, out_path):
            self.assertIn("<svg", svg_text)
            self.assertEqual(Path(out_path), destination)
            self.assertEqual(produced.read_bytes(), pdf_bytes)
            self.assert_checkpoint_unchanged()
            calls.append(out_path)
            self.record("backend_returned_ordinary_pdf", source=str(produced),
                        target=out_path, bytes=len(pdf_bytes))
            return str(produced)

        self.register_controlled_pdf_backend(return_ordinary_pdf)
        status, response = self.command("plot_pdf", {
            "sheet_uuid": self.sheet_uuid, "out": str(destination),
        })
        self.assertEqual(len(calls), 1, "normal backend was not invoked once")
        self.assertEqual(status, 200, response)
        self.assertTrue(response.get("ok"), response)
        self.assertFalse(produced.exists(), "normal backend result was not moved")
        self.assertEqual(destination.read_bytes(), pdf_bytes)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assert_checkpoint_unchanged()
        self.record("ordinary_backend_move_verified", source=str(produced),
                    target=str(destination), source_consumed=True, bytes=len(pdf_bytes),
                    sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
        restarted = self.build()
        self.assertEqual(restarted.workspace.l1_text(), self.original["doc"])
        self.assertFalse(restarted.workspace._release_pending)
        self.assert_checkpoint_unchanged()


def main():
    global SOURCE_ROOT, RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    parser.add_argument(
        "--test", action="append",
        choices=unittest.defaultTestLoader.getTestCaseNames(ReleaseOutputDestinationTests),
    )
    arguments = parser.parse_args()
    SOURCE_ROOT = arguments.source_root.resolve()
    RUN_DIR = new_run_directory(arguments.evidence_dir)
    before = source_hashes()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    clock = time.perf_counter()
    report = {
        "verdict": "RUNNING", "started_utc": started, "source_root": str(SOURCE_ROOT),
        "evidence": str(RUN_DIR), "production_operations": False, "io_mocks": False,
        "controlled_pdf_backend_injections": [
            "returned protected source path", "real guard_output rejection in backend",
            "returned ordinary path containing a real generated PDF",
        ],
    }
    (RUN_DIR / "START.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    suite = (unittest.TestSuite(ReleaseOutputDestinationTests(name) for name in arguments.test)
             if arguments.test else
             unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseOutputDestinationTests))
    with (RUN_DIR / "tests.log").open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    after = source_hashes()
    passed = result.wasSuccessful() and result.testsRun and not result.skipped and before == after
    report.update(
        verdict="PASS" if passed else "FAIL", tests=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        source_before=before, source_after=after, source_unchanged=before == after,
        finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        elapsed_s=time.perf_counter() - clock,
        findings=[{"test": str(test), "traceback": detail}
                  for test, detail in result.failures + result.errors],
    )
    (RUN_DIR / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (RUN_DIR / "time.json").write_text(json.dumps({
        key: report[key] for key in ("started_utc", "finished_utc", "elapsed_s")
    }, indent=2), encoding="utf-8")
    print("RELEASE_OUTPUT_DESTINATIONS verdict=%s tests=%s failures=%s errors=%s evidence=%s" %
          (report["verdict"], report["tests"], report["failures"], report["errors"], RUN_DIR))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
