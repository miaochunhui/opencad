"""Atomic release regression using real Workspace and on-disk L1 bytes."""

import hashlib
import json
from pathlib import Path

import tempfile
import unittest
import release_state
import server


class ReleaseStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            prefix="release-tests-", dir="/hf3fs-jd/data/tmp/opencad-features-20261010")
        self.addCleanup(self.directory.cleanup)
        self.ws = server.Workspace(server._load_cadcore(), locale="zh-CN", standard="gbt",
                                   out_root=str(Path(self.directory.name).resolve()))

    def workspace(self):
        return self.ws

    def prepare(self):
        workspace = self.workspace()
        before = workspace.state()
        target = Path(workspace.out_root) / "release-fixture.json"
        return before, workspace.command("prepare_release", {
            "path": str(target), "expected_document_sha256": release_state.digest(before["doc"]),
            "expected_locale": before["locale"], "expected_standard": before["standard"],
        })

    def test_exact_save_maintenance_and_startup_restore(self):
        ws = self.workspace()
        ws.command("add_circle", {"center": [0, 0], "radius_nm": 2000000})
        ws.set_locale("ja")
        ws.set_standard("ansi")
        before, saved = self.prepare()
        self.assertEqual(Path(saved["path"]).read_bytes(), before["doc"].encode())
        for action in (
                lambda: ws.command("undo", {}),
                lambda: ws.set_locale("en"),
                lambda: ws.set_standard("iso"),
                lambda: ws.open_doc(saved["path"])):
            with self.assertRaises(server.ApiError) as caught:
                action()
            self.assertEqual(caught.exception.code, "maintenance")
        candidate = server.Workspace(ws.core, locale="en", standard="iso", out_root=ws.out_root)
        self.assertTrue((Path(ws.out_root) / release_state.MARKER).exists())
        with self.assertRaises(server.ApiError) as caught:
            candidate.command("add_line", {"p1": [0, 0], "p2": [1000000, 0]})
        self.assertEqual(caught.exception.code, "maintenance")
        httpd = server.build_server(ws.core, host="127.0.0.1", port=0,
                                   locale="en", standard="iso", out_root=ws.out_root)
        self.addCleanup(httpd.server_close)
        recovered = httpd.workspace
        after = recovered.state()
        for key in ("doc", "locale", "standard"):
            self.assertEqual(after[key], before[key])
        self.assertFalse((Path(ws.out_root) / release_state.MARKER).exists())
        self.assertEqual(recovered.undo_stack, [])
        recovered.command("add_line", {"p1": [0, 0], "p2": [1000000, 0]})
        self.assertNotEqual(recovered.l1_text(), before["doc"])

    def test_stale_preparation_never_saves_or_freezes(self):
        ws = self.workspace()
        original = ws.l1_text()
        ws.command("add_line", {"p1": [0, 0], "p2": [1000000, 0]})
        with self.assertRaises(server.ApiError) as caught:
            ws.command("prepare_release", {
                "path": str(Path(ws.out_root) / "release-stale.json"),
                "expected_document_sha256": release_state.digest(original)})
        self.assertEqual(caught.exception.code, "workspace_changed")
        self.assertFalse((Path(ws.out_root) / release_state.MARKER).exists())
        ws.command("undo", {})
        self.assertEqual(ws.l1_text(), original)

    def test_restore_rejects_edited_document_and_bad_saved_bytes(self):
        ws = self.workspace()
        before = ws.state()
        path = Path(ws.out_root) / "manual.json"
        ws.save(str(path))
        args = {"path": str(path), "document_sha256": release_state.digest(before["doc"]),
                "locale": before["locale"], "standard": before["standard"],
                "expected_document_sha256": release_state.digest(before["doc"])}
        path.write_bytes(b"invalid checkpoint")
        with self.assertRaises(server.ApiError):
            release_state.restore_empty(ws, args)
        self.assertEqual(ws.state()["doc"], before["doc"])
        path.write_bytes(before["doc"].encode())
        ws.command("add_line", {"p1": [0, 0], "p2": [1000000, 0]})
        current = ws.l1_text()
        args["expected_document_sha256"] = release_state.digest(current)
        with self.assertRaises(server.ApiError) as caught:
            release_state.restore_empty(ws, args)
        self.assertEqual(caught.exception.code, "workspace_not_empty")
        self.assertEqual(ws.l1_text(), current)

    def test_checkpoint_symlink_rejected_and_initial_restore_atomic(self):
        ws = self.workspace()
        before = ws.state()
        target = Path(ws.out_root) / "original.json"
        ws.save(str(target))
        link = Path(ws.out_root) / "release-link.json"
        link.symlink_to(target)
        with self.assertRaises(server.ApiError):
            ws.command("prepare_release", {
                "path": str(link), "expected_document_sha256": release_state.digest(before["doc"])})
        self.assertEqual(target.read_bytes(), before["doc"].encode())
        recovered = server.Workspace(ws.core, locale="en", standard="iso", out_root=ws.out_root)
        args = {"path": str(target), "document_sha256": release_state.digest(before["doc"]),
                "locale": before["locale"], "standard": before["standard"],
                "expected_document_sha256": release_state.digest(recovered.l1_text())}
        result = release_state.restore_empty(recovered, args)
        self.assertEqual(result["doc"], before["doc"])
        self.assertEqual(result["standard"], before["standard"])
