"""Snapshot adapter controls; actual FreeCAD/EGL tested by gpu_render_probe."""

import hashlib
import json
import threading
import unittest
from unittest.mock import patch

import industrial_render as adapter
import render_service as r


class Workspace:
    def __init__(self):
        self._lock = threading.RLock()
        self.text = '{"features":[{"kind":"test-solid"}]}'
        self.undo_stack, self.redo_stack, self.standard = [], [], "gbt"
        self.mesh_calls = 0

    def l1_text(self):
        return self.text

    def mesh(self):
        self.mesh_calls += 1
        return {"ok": True, "document_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
                "solids": [{"vertices_mm": [[0, 0, 0], [100, 0, 0], [0, 100, 100]],
                            "triangles": [[0, 1, 2]]}]}


def head(workspace, revision=3):
    snapshot = {"doc": workspace.text, "undo": list(workspace.undo_stack),
                "redo": list(workspace.redo_stack), "standard": workspace.standard}
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode()
    return {"revision": revision, "sha256": hashlib.sha256(canonical).hexdigest(),
            "snapshot": snapshot}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Workspace()
        self.head = head(self.workspace)
        self.bound = adapter.bind_snapshot(self.workspace, self.head)
        self.service = patch.object(adapter, "SERVICE", r.RenderService())
        self.service.start()
        self.addCleanup(self.service.stop)

    def test_png_and_distinct_sha_headers(self):
        response = adapter.handler(self.bound, {"width": 64, "height": 64})
        self.assertEqual((response.status, response.content_type), (200, "image/png"))
        self.assertTrue(response.payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(response.headers["X-OpenCAD-Revision"], "3")
        self.assertEqual(response.headers["X-OpenCAD-Snapshot-SHA256"], self.head["sha256"])
        self.assertEqual(response.headers["X-OpenCAD-Model-SHA256"],
                         hashlib.sha256(self.workspace.text.encode()).hexdigest())
        self.assertNotEqual(response.headers["X-OpenCAD-Model-SHA256"], self.head["sha256"])
        self.assertEqual(response.headers["X-OpenCAD-PNG-SHA256"],
                         hashlib.sha256(response.payload).hexdigest())
        self.assertEqual(head(self.workspace), self.head)

    def test_unbound_refused(self):
        self.assertEqual(adapter.handler(self.workspace, {}).status, 500)

    def test_corrupt_binding(self):
        for change in ({"revision": True}, {"sha256": "0" * 64}):
            with self.assertRaises(adapter.SnapshotError):
                adapter.bind_snapshot(self.workspace, dict(self.head, **change))

    def test_stale_revision_precedes_cad(self):
        value = adapter.handler(self.bound, {"expected_revision": 2,
                                             "expected_sha": self.head["sha256"]})
        self.assertEqual(value.status, 409)
        self.assertEqual(self.workspace.mesh_calls, 0)

    def test_bad_fields_precede_cad(self):
        for body in ({"path": "/etc/passwd"}, {"url": "https://example.invalid"},
                     {"vertices": []}, {"camera": "perspective"}, {"width": 1025},
                     {"expected_revision": 3}, {"expected_sha": "0" * 64},
                     {"expected_revision": True, "expected_sha": self.head["sha256"]},
                     {"request_id": "bad\r\nheader"}, {"backend": "auto"}):
            self.assertEqual(adapter.handler(self.bound, body).status, 400, body)
        self.assertEqual(self.workspace.mesh_calls, 0)

    def test_explicit_fallback(self):
        value = adapter.handler(self.bound, {"backend": "gpu"})
        self.assertEqual(value.status, 503)
        with self.assertLogs("opencad.render", level="WARNING"):
            value = adapter.handler(self.bound, {"backend": "gpu", "allow_cpu_fallback": True})
        self.assertEqual(value.status, 200)
        self.assertEqual(value.headers["X-OpenCAD-Render-Status"], "CPU_FALLBACK")
        self.assertEqual(value.headers["X-OpenCAD-Hardware"], "false")
        self.assertIn("disabled", value.headers["X-OpenCAD-Fallback-Reason"])

    def test_empty_and_wrong_mesh_sha(self):
        with patch.object(self.workspace, "mesh", return_value={
                "ok": True, "document_sha256": hashlib.sha256(self.workspace.text.encode()).hexdigest(),
                "solids": []}):
            self.assertEqual(adapter.handler(self.bound, {}).status, 422)
        with patch.object(self.workspace, "mesh", return_value={
                "ok": True, "document_sha256": "0" * 64, "solids": []}):
            self.assertEqual(adapter.handler(self.bound, {}).status, 500)

    def test_readonly_mutation_detected(self):
        original = self.workspace.mesh
        def changed():
            value = original()
            self.workspace.undo_stack.append(self.workspace.text)
            return value
        with patch.object(self.workspace, "mesh", side_effect=changed):
            self.assertEqual(adapter.handler(self.bound, {}).status, 500)

    def test_changed_after_binding(self):
        self.workspace.text = "{}"
        self.assertEqual(adapter.handler(self.bound, {}).status, 500)
        self.assertEqual(self.workspace.mesh_calls, 0)

    def test_history_changed_after_binding(self):
        self.workspace.undo_stack.append(self.workspace.text)
        self.assertEqual(adapter.handler(self.bound, {}).status, 500)
        self.assertEqual(self.workspace.mesh_calls, 0)

    def test_camera_changes_frame_without_doc_change(self):
        a = adapter.handler(self.bound, {"camera": "front"})
        b = adapter.handler(self.bound, {"camera": "isometric"})
        self.assertEqual((a.status, b.status), (200, 200))
        self.assertNotEqual(a.headers["X-OpenCAD-Frame-SHA256"], b.headers["X-OpenCAD-Frame-SHA256"])
        self.assertEqual(head(self.workspace), self.head)

    def test_bounded_merge_and_bad_index(self):
        value = self.workspace.mesh()
        value["solids"] *= r.MAX_TRIANGLES + 1
        with self.assertRaises(r.InvalidInput):
            adapter._merge_mesh(value, value["document_sha256"])
        value = self.workspace.mesh()
        value["solids"][0]["triangles"] = [[0, 1, 99]]
        with self.assertRaises(adapter.SnapshotError):
            adapter._merge_mesh(value, value["document_sha256"])

    def test_configure_requires_explicit_types(self):
        with self.assertRaises(ValueError):
            adapter.configure(enable_gpu="yes")


if __name__ == "__main__":
    unittest.main()
