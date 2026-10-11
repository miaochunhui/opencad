"""Real HTTP, real B-Rep: no mocked solids or successful empty exports."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_server import ServerCase

STEP_READ_HELPER = """
import json
import os
import Part
shape = Part.Shape()
shape.read(os.environ["OPENCAD_TEST_STEP"])
with open(os.environ["OPENCAD_TEST_RESULT"], "w") as handle:
    json.dump({"volume_mm3": shape.Volume, "solids": len(shape.Solids)}, handle)
"""


class Web3DTests(ServerCase):
    def circle(self):
        status, body = self.server.post("/api/command", {
            "command": "add_circle", "args": {"center": [0, 0], "radius_nm": 10_000_000}})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)

    def cmd(self, name, args=None):
        status, body = self.server.post("/api/command", {"command": name, "args": args or {}})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        return body

    def mesh(self):
        status, body = self.server.get("/api/mesh")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        return body

    def test_page_and_assets_are_real_and_generator_is_deterministic(self):
        from viewer3d_page import render

        self.assertEqual(render(), render())
        for path in ("/web/3d.html", "/web/js/viewer3d.js", "/web/css/viewer3d.css"):
            status, headers, raw = self.server.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertGreater(len(raw), 100)
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertIn(b'id="model"', render().encode())

    def test_page_uses_catalog_for_empty_state_accessibility_and_footer(self):
        from viewer3d_page import render

        page = render()
        for key in ("viewer3d.canvas", "viewer3d.empty_document",
                    "viewer3d.no_solid_feature", "viewer3d.modified"):
            for code in ("en", "zh-CN", "ja"):
                status, body = self.server.post("/api/locale", {"locale": code})
                self.assertEqual(status, 200)
                self.assertTrue(body["ok"], body)
                self.assertIn(key, self.state()["strings"])
        self.assertIn('data-i18n-aria="viewer3d.canvas"', page)
        self.assertIn('data-i18n="viewer3d.modified"', page)
        self.assertNotIn('aria-label="3D model"', page)
        source = (Path(__file__).parents[1] / "web/js/viewer3d.js").read_text()
        self.assertNotIn("status.textContent = r.reason", source)
        self.assertIn("native_name", source)

    def test_empty_and_wire_only_are_explicit_read_only_states(self):
        before = self.state()["doc"]
        self.assertEqual(self.mesh()["solids"], [])
        self.assertEqual(self.state()["doc"], before)
        self.circle()
        before = self.state()["doc"]
        self.assertEqual(self.mesh()["reason"], "no_solid_feature")
        self.assertEqual(self.state()["doc"], before)
        status, headers, raw = self.server.request("GET", "/api/step")
        self.assertEqual(status, 200)
        self.assertNotEqual(headers["Content-Type"], "application/step")
        self.assertFalse(json.loads(raw)["ok"])

    def test_extrude_edit_undo_redo_and_step_roundtrip(self):
        import math
        import shutil

        self.circle()
        self.cmd("extrude", {"distance_nm": 6_000_000})
        before = self.state()["doc"]
        mesh = self.mesh()
        self.assertEqual(self.state()["doc"], before)
        solid = mesh["solids"][0]
        self.assertGreater(len(solid["triangles"]), 10)
        self.assertAlmostEqual(solid["properties"]["volume_nm3"] / 1e18, math.pi * 100 * 6, places=6)
        feature = json.loads(before)["features"][0]["uuid"]
        self.cmd("edit_solid", {"feature_uuid": feature, "distance_nm": 12_000_000})
        self.assertNotEqual(self.mesh()["document_sha256"], mesh["document_sha256"])
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18,
                               math.pi * 100 * 12, places=6)
        self.cmd("undo")
        self.assertEqual(self.state()["doc"], before)
        self.cmd("redo")
        status, headers, raw = self.server.request("GET", "/api/step")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/step")
        self.assertTrue(raw.startswith(b"ISO-10303-21;"))
        with tempfile.TemporaryDirectory(prefix="opencad-step-test-") as directory:
            path = Path(directory) / "solid.step"
            path.write_bytes(raw)
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            script = scripts / "read_step.py"
            script.write_text(STEP_READ_HELPER)
            result = Path(directory) / "result.json"
            executable = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
            self.assertIsNotNone(executable, "real STEP reimport requires FreeCAD")
            proc = subprocess.run([executable, str(script)], cwd=directory,
                                  env=dict(os.environ, OPENCAD_TEST_STEP=str(path),
                                           OPENCAD_TEST_RESULT=str(result)),
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            props = json.loads(result.read_text())
            self.assertEqual(props["solids"], 1)
            self.assertAlmostEqual(props["volume_mm3"], math.pi * 100 * 12, places=6)

    def test_failed_solid_command_rolls_back_document_and_undo(self):
        self.add_line()
        before = self.state()["doc"]
        status, body = self.server.post("/api/command", {
            "command": "extrude", "args": {"distance_nm": 6_000_000}})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(self.state()["doc"], before)
        self.cmd("undo")
        self.assertEqual(self.state()["entities"], 0)


if __name__ == "__main__":
    unittest.main()
