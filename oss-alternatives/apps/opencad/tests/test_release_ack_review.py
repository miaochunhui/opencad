"""Real Workspace recovery after faults inside startup acknowledgement.

Only fsync/link failures are injected; acknowledgement, socket binding, L1 IO
and subsequent Workspace startup use the selected read-only application source.
This is deterministic IO-fault testing, not a physical power-loss experiment.
Evidence and unique test state directories are retained outside the repository.
"""

from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path(
    "/hf3fs-jd/data/tmp/opencad-features-20261010/seat-atomic-review/ack-review"
)
RUN_DIR = None
SERVER = None
RELEASE = None


def source_hashes():
    paths = [
        SOURCE_ROOT / "oss-alternatives/apps/opencad/server.py",
        SOURCE_ROOT / "oss-alternatives/apps/opencad/release_state.py",
        SOURCE_ROOT / "oss-alternatives/apps/opencad/solid_commands.py",
        SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/docmodel.py",
        SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/ops/output.py",
        Path(__file__).resolve(),
    ]
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
            raise AssertionError("foreign review source: " + module.__file__)


class ReleaseAckReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUN_DIR
        if RUN_DIR is None:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
            RUN_DIR = EVIDENCE_ROOT / (stamp + "-discovery-" + uuid.uuid4().hex)
            RUN_DIR.mkdir(parents=True, exist_ok=False)
        cls.before = source_hashes()
        if SERVER is None:
            load_source()

    @classmethod
    def tearDownClass(cls):
        after = source_hashes()
        (RUN_DIR / "source-hashes.json").write_text(
            json.dumps({"before": cls.before, "after": after}, indent=2),
            encoding="utf-8",
        )
        if cls.before != after:
            raise AssertionError("reviewed source changed during ack fault tests")

    def setUp(self):
        self.directory = RUN_DIR / self._testMethodName
        self.directory.mkdir(parents=True, exist_ok=False)
        self.state_root = (self.directory / "state").resolve()
        self.state_root.mkdir()
        self.core = SERVER._load_cadcore()
        self.workspace = SERVER.Workspace(
            self.core, locale="ja", standard="ansi", out_root=str(self.state_root),
        )
        self.workspace.command("add_line", {
            "p1": [0, 0], "p2": [1_000_000, 2_000_000],
        })
        self.original = self.workspace.state()
        self.assertEqual(self.workspace.entity_count(), 1)
        self.saved = self.state_root / "release-ack-fixture.json"
        self.workspace.command("prepare_release", {
            "path": str(self.saved),
            "expected_document_sha256": RELEASE.digest(self.original["doc"]),
            "expected_locale": self.original["locale"],
            "expected_standard": self.original["standard"],
        })
        self.marker = self.state_root / RELEASE.MARKER
        self.checkpoint = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(self.saved.read_bytes(), self.original["doc"].encode("utf-8"))
        self.assertEqual(self.checkpoint["doc"], self.original["doc"])
        self.events = []
        self.servers = []

    def record(self, phase, **fields):
        event = {
            "phase": phase, "observed_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(), **fields,
        }
        self.events.append(event)
        with (self.directory / "observations.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event) + "\n")

    def retained_files(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.state_root.iterdir()) if path.is_file()}

    def build(self, *, state_root=None):
        result = SERVER.build_server(
            self.core, host="127.0.0.1", port=0, locale="en", standard="iso",
            out_root=str(self.state_root if state_root is None else state_root),
        )
        self.addCleanup(result.server_close)
        return result

    def assert_restored(self, workspace, expected):
        actual = workspace.state()
        for key in ("doc", "locale", "standard"):
            self.assertEqual(actual[key], expected[key])
        self.assertEqual(workspace.undo_stack, [])
        self.assertEqual(workspace.redo_stack, [])
        self.assertIsNone(workspace._mesh_cache)
        return actual

    def start_http(self, httpd):
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        thread.start()

        def stop():
            httpd.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "isolated HTTP server failed to stop")

        self.addCleanup(stop)
        host, port = httpd.server_address
        return "http://%s:%s" % (host, port)

    def post(self, base, route, payload):
        request = urllib.request.Request(
            base + route, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def assert_write_matrix(self, targets):
        httpd = self.build()
        self.assert_restored(httpd.workspace, self.original)
        base = self.start_http(httpd)
        current = self.state_root / RELEASE.CURRENT
        archive = next(self.state_root.glob(".opencad-release-restored-*.json"))
        protected = {path: path.read_bytes() for path in (current, archive, self.saved)}
        routes = [
            ("save", "/api/save", None, "path"),
            ("export_dxf", "/api/export/dxf", None, "path"),
            ("export_step", "/api/export/step", None, "path"),
            ("export_pdf", "/api/command", "export_pdf", "out"),
            ("plot", "/api/command", "plot", "out"),
            ("plot_output", "/api/command", "plot", "output"),
            ("plot_pdf", "/api/command", "plot_pdf", "out"),
            ("plot_pdf_out_path", "/api/command", "plot_pdf", "out_path"),
            ("preview_out", "/api/command", "preview", "out"),
            ("preview_out_path", "/api/command", "preview", "out_path"),
            ("model_space_plot", "/api/command", "model_space_plot", "output"),
            ("publish_out_dir", "/api/command", "publish", "out_dir"),
            ("publish_out_path", "/api/command", "publish", "out_path"),
        ]
        aliases = []
        for label, target in targets(current, archive):
            aliases.append((label + "_direct", target))
            if target.exists():
                symbolic = self.state_root / (label + "-symbolic-alias")
                symbolic.symlink_to(target)
                hard = self.state_root / (label + "-hard-alias")
                os.link(target, hard)
                self.assertTrue(symbolic.is_symlink())
                self.assertTrue(hard.samefile(target))
                aliases.extend(((label + "_symlink", symbolic), (label + "_hardlink", hard)))
        identities = {path: (path.lstat().st_dev, path.lstat().st_ino)
                      for _, path in aliases if path.exists() or path.is_symlink()}
        original_state = httpd.workspace.state()
        histories = (list(httpd.workspace.undo_stack), list(httpd.workspace.redo_stack))
        cache = httpd.workspace._mesh_cache
        requests = 0
        for label, destination in aliases:
            for writer, route, command, key in routes:
                arguments = {key: str(destination)}
                if command == "publish" and key == "out_path":
                    arguments["dry_run"] = False
                payload = ({"command": command, "args": arguments}
                           if command else arguments)
                status, response = self.post(base, route, payload)
                requests += 1
                unchanged = all(path.read_bytes() == data for path, data in protected.items())
                namespace_unchanged = all(
                    (path.lstat().st_dev, path.lstat().st_ino) == identity
                    for path, identity in identities.items()
                )
                self.record("protected_write_response", writer=writer, target=label,
                            path=str(destination), status=status, response=response,
                            protected_bytes_unchanged=unchanged,
                            alias_namespace_unchanged=namespace_unchanged)
                if status != 409 or not unchanged:
                    try:
                        recovered = SERVER.Workspace(
                            self.core, locale="en", standard="iso",
                            out_root=str(self.state_root),
                        )
                    except Exception as exc:
                        self.record(
                            "protected_write_restart_failed", writer=writer,
                            target=label, error_type=type(exc).__name__, error=str(exc),
                            traceback="".join(traceback.format_exception(
                                type(exc), exc, exc.__traceback__)),
                        )
                    else:
                        self.record("protected_write_restart_restored", writer=writer,
                                    document_sha256=RELEASE.digest(recovered.l1_text()))
                self.assertEqual(status, 409, "%s must protect %s" % (writer, label))
                self.assertEqual(response.get("error"), "protected_checkpoint")
                self.assertFalse(response.get("ok", True))
                self.assertTrue(response.get("detail"), "rejection must explain its first cause")
                self.assertTrue(unchanged, "%s changed protected bytes" % writer)
                self.assertTrue(namespace_unchanged, "%s replaced a protected alias" % writer)
                self.assertEqual(httpd.workspace.state(), original_state)
                self.assertEqual((httpd.workspace.undo_stack, httpd.workspace.redo_stack), histories)
                self.assertIs(httpd.workspace._mesh_cache, cache)
                if not destination.exists():
                    self.assertFalse(destination.is_symlink())
        self.record("protected_write_matrix_complete", requests=requests,
                    targets=len(aliases), writers=len(routes))
        restarted = self.build()
        self.assert_restored(restarted.workspace, self.original)
        for path, data in protected.items():
            self.assertEqual(path.read_bytes(), data)

    def fail_ack(self, *, compensation_failure):
        real_fsync = RELEASE.os.fsync
        real_link = RELEASE.os.link
        real_server = SERVER.ThreadingHTTPServer
        directory_syncs = []
        compensation_attempts = []

        def directory_fault(descriptor):
            real_fsync(descriptor)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                return
            directory_syncs.append(self.retained_files())
            self.record("real_directory_fsync", call=len(directory_syncs),
                        files=directory_syncs[-1])
            if len(directory_syncs) == 2:
                # An error may be reported even after namespace changes persist.
                self.assertFalse(self.marker.exists())
                raise OSError(errno.EIO, "injected post-unlink directory fsync first cause")

        def link_fault(source, destination, *args, **kwargs):
            if (Path(destination) == self.marker and
                    Path(source).name.startswith(".opencad-release-restored-")):
                compensation_attempts.append((str(source), str(destination)))
                self.record("compensation_link", fail=compensation_failure,
                            source=str(source), destination=str(destination))
                if compensation_failure:
                    raise OSError(errno.EIO, "injected compensation link failure")
            return real_link(source, destination, *args, **kwargs)

        def capture_server(*args, **kwargs):
            httpd = real_server(*args, **kwargs)
            self.servers.append(httpd)
            self.addCleanup(httpd.server_close)
            return httpd

        with patch.object(RELEASE.os, "fsync", side_effect=directory_fault):
            with patch.object(RELEASE.os, "link", side_effect=link_fault):
                with patch.object(SERVER, "ThreadingHTTPServer", side_effect=capture_server):
                    with self.assertRaises(OSError) as caught:
                        self.build()
        failure = caught.exception
        self.record("ack_failed", error=str(failure),
                    context=str(failure.__context__),
                    traceback="".join(traceback.format_exception(
                        type(failure), failure, failure.__traceback__)),
                    directory_syncs=len(directory_syncs),
                    compensation_attempts=len(compensation_attempts),
                    files=self.retained_files())
        self.assertGreaterEqual(len(directory_syncs), 2)
        self.assertEqual(len(compensation_attempts), 1)
        self.assertEqual(len(self.servers), 1)
        self.assertEqual(self.servers[0].socket.fileno(), -1)
        self.assertEqual(self.saved.read_bytes(), self.original["doc"].encode("utf-8"))
        return failure

    def test_post_unlink_fsync_failure_with_successful_link_restores_on_retry(self):
        failure = self.fail_ack(compensation_failure=False)
        self.assertIn("post-unlink directory fsync first cause", str(failure))
        self.assertTrue(self.marker.exists())
        self.assertEqual(json.loads(self.marker.read_text()), self.checkpoint)
        recovered = self.build()
        restored = recovered.workspace.state()
        self.record("retry_started", files=self.retained_files(),
                    expected_document_sha256=RELEASE.digest(self.original["doc"]),
                    restored_document_sha256=RELEASE.digest(restored["doc"]))
        for key in ("doc", "locale", "standard"):
            self.assertEqual(restored[key], self.original[key])
        self.assertFalse(self.marker.exists())

    def test_post_unlink_fsync_and_compensation_link_failure_never_start_empty(self):
        failure = self.fail_ack(compensation_failure=True)
        self.assertIn("compensation link failure", str(failure))
        self.assertIn("post-unlink directory fsync first cause", str(failure.__context__))
        self.assertFalse(self.marker.exists())
        archives = list(self.state_root.glob(".opencad-release-restored-*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text()), self.checkpoint)
        try:
            recovered = self.build()
        except Exception as exc:
            self.record("retry_refused", error=str(exc), files=self.retained_files())
            self.assertTrue(str(exc), "startup rejection must retain a diagnostic")
            return
        restored = recovered.workspace.state()
        writable = False
        try:
            recovered.workspace.command("add_line", {
                "p1": [3_000_000, 0], "p2": [4_000_000, 1_000_000],
            })
        except SERVER.ApiError as exc:
            self.record("retry_edit_refused", code=exc.code, detail=exc.detail)
        else:
            writable = True
        self.record(
            "retry_started", expected_document_sha256=RELEASE.digest(self.original["doc"]),
            restored_document_sha256=RELEASE.digest(restored["doc"]),
            original_entities=1, restored_entities=len(
                json.loads(restored["doc"])["sketches"][0]["entities"]),
            writable=writable, files=self.retained_files(),
        )
        self.assertEqual(
            restored["doc"], self.original["doc"],
            "failed ack lost pending recovery and a new server accepted an empty document",
        )

    def test_successful_ack_then_current_only_restart_restores_checkpoint(self):
        initial = self.build()
        self.assert_restored(initial.workspace, self.original)
        self.assertFalse(self.marker.exists())
        current = self.state_root / RELEASE.CURRENT
        self.assertFalse(current.is_symlink())
        self.assertEqual(json.loads(current.read_text()), self.checkpoint)
        current_bytes = current.read_bytes()
        initial.server_close()

        guarded = SERVER.Workspace(
            self.core, locale="en", standard="iso", out_root=str(self.state_root),
        )
        self.assert_restored(guarded, self.original)
        self.assertTrue(guarded._release_pending)
        self.assertEqual(guarded._release_startup_marker, current)
        with self.assertRaises(SERVER.ApiError) as caught:
            guarded.command("add_line", {"p1": [0, 0], "p2": [1, 1]})
        self.assertEqual(caught.exception.code, "maintenance")
        self.record("current_only_before_bind",
                    document_sha256=RELEASE.digest(guarded.l1_text()),
                    guard=True, files=self.retained_files())

        restarted = self.build()
        self.assert_restored(restarted.workspace, self.original)
        self.assertFalse(restarted.workspace._release_pending)
        self.assertFalse(self.marker.exists())
        self.assertEqual(current.read_bytes(), current_bytes)
        restarted.workspace.command("add_line", {
            "p1": [3_000_000, 0], "p2": [4_000_000, 1_000_000],
        })
        live = restarted.workspace.state()
        self.assertNotEqual(live["doc"], self.original["doc"])
        self.assertEqual(restarted.workspace.entity_count(), 2)
        self.assertEqual(current.read_bytes(), current_bytes)
        self.assertEqual(self.saved.read_bytes(), self.original["doc"].encode("utf-8"))
        self.record("current_only_after_bind",
                    checkpoint_sha256=RELEASE.digest(self.original["doc"]),
                    live_document_sha256=RELEASE.digest(live["doc"]),
                    writable=True, files=self.retained_files())

    def test_second_prepare_with_current_prefers_new_pending(self):
        initial = self.build()
        workspace = initial.workspace
        self.assert_restored(workspace, self.original)
        current = self.state_root / RELEASE.CURRENT
        old_current = current.read_bytes()
        old_archives = {
            path: path.read_bytes()
            for path in self.state_root.glob(".opencad-release-restored-*.json")
        }
        self.assertEqual(len(old_archives), 1)
        workspace.command("add_line", {
            "p1": [5_000_000, 0], "p2": [6_000_000, 1_000_000],
        })
        workspace.set_locale("en")
        workspace.set_standard("iso")
        newer = workspace.state()
        self.assertNotEqual(newer["doc"], self.original["doc"])
        second_saved = self.state_root / "release-ack-second.json"
        prepared = workspace.command("prepare_release", {
            "path": str(second_saved),
            "expected_document_sha256": RELEASE.digest(newer["doc"]),
            "expected_locale": newer["locale"],
            "expected_standard": newer["standard"],
        })
        pending = json.loads(self.marker.read_text())
        self.assertNotEqual(pending["id"], self.checkpoint["id"])
        self.assertEqual(prepared["checkpoint"]["id"], pending["id"])
        self.assertEqual(pending["doc"], newer["doc"])
        self.assertEqual(second_saved.read_bytes(), newer["doc"].encode("utf-8"))
        self.assertEqual(current.read_bytes(), old_current)
        with self.assertRaises(SERVER.ApiError) as caught:
            workspace.command("add_line", {"p1": [0, 0], "p2": [1, 1]})
        self.assertEqual(caught.exception.code, "maintenance")
        initial.server_close()

        guarded = SERVER.Workspace(
            self.core, locale="ja", standard="ansi", out_root=str(self.state_root),
        )
        self.assert_restored(guarded, newer)
        self.assertEqual(guarded._release_startup_marker, self.marker)
        self.assertTrue(guarded._release_pending)
        self.assertEqual(current.read_bytes(), old_current)
        self.record("second_prepare_before_bind", pending_id=pending["id"],
                    old_current_id=self.checkpoint["id"],
                    restored_document_sha256=RELEASE.digest(guarded.l1_text()),
                    files=self.retained_files())

        restarted = self.build()
        self.assert_restored(restarted.workspace, newer)
        self.assertFalse(restarted.workspace._release_pending)
        self.assertFalse(self.marker.exists())
        self.assertEqual(json.loads(current.read_text()), pending)
        self.assertEqual(self.saved.read_bytes(), self.original["doc"].encode("utf-8"))
        for archive, original_bytes in old_archives.items():
            self.assertEqual(archive.read_bytes(), original_bytes)
        restarted.server_close()
        again = self.build()
        self.assert_restored(again.workspace, newer)
        self.assertFalse(again.workspace._release_pending)
        self.assertEqual(json.loads(current.read_text()), pending)
        self.record("second_prepare_current_only_restart",
                    restored_document_sha256=RELEASE.digest(again.workspace.l1_text()),
                    current_id=pending["id"], files=self.retained_files())

    def test_corrupt_or_symlink_current_refuses_startup_before_bind(self):
        initial = self.build()
        self.assert_restored(initial.workspace, self.original)
        self.assertFalse(self.marker.exists())
        original_current = (self.state_root / RELEASE.CURRENT).read_bytes()
        initial.server_close()
        real_server = SERVER.ThreadingHTTPServer

        for kind in ("invalid_json", "digest_mismatch", "symlink", "dangling_symlink"):
            with self.subTest(kind=kind):
                root = (self.directory / kind / "state").resolve()
                root.mkdir(parents=True, exist_ok=False)
                saved = root / "drawing.json"
                saved.write_bytes(self.saved.read_bytes())
                checkpoint = {**self.checkpoint, "path": str(saved)}
                current = root / RELEASE.CURRENT
                reference = root / "valid-current-reference.json"
                RELEASE.write_new(reference, checkpoint)
                RELEASE.write_new(current, checkpoint)
                positive = self.build(state_root=root)
                self.assert_restored(positive.workspace, self.original)
                self.assertFalse(positive.workspace._release_pending)
                positive.server_close()
                self.assertFalse((root / RELEASE.MARKER).exists())
                before_saved = saved.read_bytes()
                before_reference = reference.read_bytes()

                if kind == "invalid_json":
                    current.write_text('{"broken":', encoding="utf-8")
                elif kind == "digest_mismatch":
                    current.write_text(json.dumps({
                        **checkpoint, "document_sha256": "0" * 64,
                    }), encoding="utf-8")
                else:
                    link = root / "invalid-current-link"
                    link.symlink_to(reference if kind == "symlink" else root / "absent")
                    os.replace(link, current)
                    self.assertTrue(current.is_symlink())

                bound = []

                def capture_server(*args, **kwargs):
                    httpd = real_server(*args, **kwargs)
                    bound.append(httpd)
                    self.addCleanup(httpd.server_close)
                    return httpd

                with patch.object(SERVER, "ThreadingHTTPServer", side_effect=capture_server):
                    with self.assertRaises((SERVER.ApiError, json.JSONDecodeError)) as caught:
                        self.build(state_root=root)
                failure = caught.exception
                self.record(
                    "invalid_current_refused", kind=kind, state_root=str(root),
                    error_type=type(failure).__name__, error=str(failure),
                    code=getattr(failure, "code", None),
                    traceback="".join(traceback.format_exception(
                        type(failure), failure, failure.__traceback__)),
                    bind_calls=len(bound), current_is_symlink=current.is_symlink(),
                    current_exists=current.exists(),
                )
                self.assertEqual(bound, [], "invalid CURRENT must fail before socket binding")
                self.assertTrue(str(failure), "rejection must preserve a diagnostic")
                if kind == "invalid_json":
                    self.assertIsInstance(failure, json.JSONDecodeError)
                else:
                    self.assertEqual(failure.code, "checkpoint_mismatch" if
                                     kind == "digest_mismatch" else "unsafe_checkpoint")
                self.assertEqual(saved.read_bytes(), before_saved)
                self.assertEqual(reference.read_bytes(), before_reference)
                self.assertFalse((root / RELEASE.MARKER).exists())
        self.assertEqual((self.state_root / RELEASE.CURRENT).read_bytes(), original_current)

    def test_save_current_rejected_without_bytes_change_or_broken_restart(self):
        httpd = self.build()
        self.assert_restored(httpd.workspace, self.original)
        current = self.state_root / RELEASE.CURRENT
        before = current.read_bytes()
        self.assertEqual(json.loads(before), self.checkpoint)
        self.assertFalse(self.marker.exists())
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        thread.start()

        def stop_http():
            httpd.shutdown()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "isolated HTTP server failed to stop")

        self.addCleanup(stop_http)
        host, port = httpd.server_address
        request = urllib.request.Request(
            "http://%s:%s/api/save" % (host, port),
            data=json.dumps({"path": str(current)}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
        after = current.read_bytes()
        recovery_error = None
        try:
            recovered = SERVER.Workspace(
                self.core, locale="en", standard="iso", out_root=str(self.state_root),
            )
        except Exception as exc:
            recovery_error = exc
            self.record(
                "post_save_current_restart_failed", error_type=type(exc).__name__,
                error=str(exc), context=str(exc.__context__),
                traceback="".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)),
            )
        else:
            self.record("post_save_current_restart_restored",
                        document_sha256=RELEASE.digest(recovered.l1_text()))
        self.record(
            "save_current_response", status=status, payload=payload,
            before_sha256=hashlib.sha256(before).hexdigest(),
            after_sha256=hashlib.sha256(after).hexdigest(), bytes_unchanged=before == after,
            recovery_error_type=(type(recovery_error).__name__
                                 if recovery_error is not None else None),
            files=self.retained_files(),
        )
        self.assertEqual(status, 409, "save(CURRENT) must refuse control-file overwrite")
        self.assertEqual(after, before, "rejected save must leave CURRENT byte-for-byte intact")
        self.assertIsNone(recovery_error, "rejected save must not break startup recovery")
        self.assert_restored(recovered, self.original)
        self.assertEqual(self.saved.read_bytes(), self.original["doc"].encode("utf-8"))

    def test_write_routes_protect_control_files_and_aliases(self):
        self.assert_write_matrix(lambda current, archive: [
            ("current", current),
            ("pending_reserved", self.marker),
            ("current_temporary_reserved", self.state_root / ".opencad-current-test"),
        ])

    def test_write_routes_protect_archive_and_aliases(self):
        self.assert_write_matrix(lambda current, archive: [("archive", archive)])

    def test_write_routes_protect_current_saved_document_and_aliases(self):
        self.assert_write_matrix(lambda current, archive: [("saved", self.saved)])

    def test_ordinary_save_remains_writable_without_changing_checkpoint(self):
        httpd = self.build()
        self.assert_restored(httpd.workspace, self.original)
        base = self.start_http(httpd)
        before_files = self.retained_files()
        httpd.workspace.command("add_line", {
            "p1": [7_000_000, 0], "p2": [8_000_000, 1_000_000],
        })
        live = httpd.workspace.state()
        before_histories = (list(httpd.workspace.undo_stack), list(httpd.workspace.redo_stack))
        ordinary = self.state_root / "ordinary-user-drawing.json"
        status, response = self.post(base, "/api/save", {"path": str(ordinary)})
        self.record("ordinary_save_response", status=status, response=response,
                    live_document_sha256=RELEASE.digest(live["doc"]),
                    saved_sha256=hashlib.sha256(ordinary.read_bytes()).hexdigest()
                    if ordinary.exists() else None)
        self.assertEqual(status, 200)
        self.assertTrue(response.get("ok"))
        self.assertEqual(ordinary.read_bytes(), live["doc"].encode("utf-8"))
        self.assertEqual(httpd.workspace.state(), live)
        self.assertEqual((httpd.workspace.undo_stack, httpd.workspace.redo_stack), before_histories)
        for name, expected in before_files.items():
            self.assertEqual(hashlib.sha256((self.state_root / name).read_bytes()).hexdigest(),
                             expected)
        httpd.workspace.open_doc(str(ordinary))
        self.assertEqual(httpd.workspace.l1_text(), live["doc"])


def main():
    global SOURCE_ROOT, RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    parser.add_argument(
        "--test", action="append",
        choices=unittest.defaultTestLoader.getTestCaseNames(ReleaseAckReviewTests),
        help="Run only the named regression; repeat for multiple tests.",
    )
    args = parser.parse_args()
    SOURCE_ROOT = args.source_root.resolve()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_DIR = args.evidence_dir / (stamp + "-" + uuid.uuid4().hex)
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    (RUN_DIR / "write-probe").touch(exist_ok=False)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    clock = time.perf_counter()
    before = source_hashes()
    report = {"verdict": "RUNNING", "started_utc": started,
              "source_root": str(SOURCE_ROOT), "evidence": str(RUN_DIR),
              "physical_power_loss_test": False, "production_operations": False}
    (RUN_DIR / "START.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    suite = (unittest.TestSuite(ReleaseAckReviewTests(name) for name in args.test)
             if args.test else
             unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseAckReviewTests))
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
    print("RELEASE_ACK_REVIEW verdict=%s tests=%s failures=%s errors=%s evidence=%s" %
          (report["verdict"], report["tests"], report["failures"], report["errors"], RUN_DIR))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
