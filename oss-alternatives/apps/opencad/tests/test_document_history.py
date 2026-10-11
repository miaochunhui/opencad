"""Exact no-sketch history boundaries using the real Workspace and L1 codec."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from server import ApiError, Workspace, _load_cadcore


class DocumentHistoryTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT",
                                   "/data/dev/opencad-private-fixtures"))
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="history-", dir=root)
        self.addCleanup(self.directory.cleanup)
        self.workspace = Workspace(_load_cadcore(), locale="zh-CN", standard="iso",
                                   out_root=self.directory.name)
        self.workspace.doc.sketches.clear()
        self.empty = self.workspace.l1_text()
        self.assertEqual(self.workspace.doc.sketches, [])

    def test_state_and_unknown_command_preserve_no_sketch_bytes(self):
        self.assertIsNone(self.workspace.state()["sketch_uuid"])
        with self.assertRaises(ApiError):
            self.workspace.command("not-a-command", {})
        self.assertEqual(self.workspace.l1_text(), self.empty)
        self.assertEqual(self.workspace.undo_stack, [])
        self.assertEqual(self.workspace.redo_stack, [])

    def test_empty_undo_and_redo_rejections_preserve_bytes(self):
        for verb in ("undo", "redo"):
            with self.subTest(verb=verb), self.assertRaises(ApiError):
                self.workspace.command(verb, {})
            self.assertEqual(self.workspace.l1_text(), self.empty)

    def test_sketch_creation_is_inside_history_and_failure_rollback(self):
        with self.assertRaises(ApiError):
            self.workspace.command("add_line", {})
        self.assertEqual(self.workspace.l1_text(), self.empty)
        self.assertEqual(self.workspace.undo_stack, [])
        self.workspace.command("add_line", {"p1": [0, 0], "p2": [1000000, 0]})
        edited = self.workspace.l1_text()
        self.assertEqual(len(self.workspace.doc.sketches), 1)
        self.assertNotEqual(edited, self.empty)
        self.assertEqual(self.workspace.undo_stack, [self.empty])
        self.workspace.command("undo", {})
        self.assertEqual(self.workspace.l1_text(), self.empty)
        self.assertEqual(self.workspace.redo_stack, [edited])
        self.workspace.command("redo", {})
        self.assertEqual(self.workspace.l1_text(), edited)

    def test_document_command_history_does_not_create_a_sketch(self):
        self.workspace.command("layer_create", {"name": "History"})
        edited = self.workspace.l1_text()
        self.assertNotEqual(edited, self.empty)
        self.assertEqual(self.workspace.doc.sketches, [])
        self.workspace.command("undo", {})
        self.assertEqual(self.workspace.l1_text(), self.empty)
        self.workspace.command("redo", {})
        self.assertEqual(self.workspace.l1_text(), edited)
        self.assertEqual(self.workspace.doc.sketches, [])


if __name__ == "__main__":
    unittest.main()
