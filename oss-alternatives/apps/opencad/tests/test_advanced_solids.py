"""Real HTTP geometry, persistence and rejected-edit atomicity."""

import json
import math
from pathlib import Path

from tests.test_server import ServerCase

MM = 1_000_000


class AdvancedSolidsTests(ServerCase):
    def cmd(self, command, args=None):
        status, body = self.server.post("/api/command", {"command": command, "args": args or {}})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        return body

    def mesh(self):
        status, body = self.server.get("/api/mesh")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        return body

    def circle(self):
        self.cmd("add_circle", {"center": [0, 0], "radius_nm": 2 * MM})
        return json.loads(self.state()["doc"])["sketches"][0]["uuid"]

    def box(self):
        for a, b in (((0, 0), (40, 0)), ((40, 0), (40, 20)),
                     ((40, 20), (0, 20)), ((0, 20), (0, 0))):
            self.cmd("add_line", {"p1": [v * MM for v in a], "p2": [v * MM for v in b]})
        self.cmd("extrude", {"distance_nm": 30 * MM})

    def reject_unchanged(self, command, args):
        before = self.state()["doc"]
        status, body = self.server.post("/api/command", {"command": command, "args": args})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(self.state()["doc"], before)
        return body

    def test_sweep_persistence_edit_suppress_and_undo(self):
        sketch = self.circle()
        item = self.cmd("sweep_solid", {"sketch_uuid": sketch,
                        "path_points_nm": [[0, 0, 0], [0, 0, 10 * MM]]})
        volume = math.pi * 4 * 10
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18, volume, places=5)
        before = self.state()["doc"]
        status, saved = self.server.post("/api/save", {"path": "sweep.json"})
        self.assertTrue(saved["ok"])
        self.cmd("undo")
        self.assertEqual(self.mesh()["solids"], [])
        status, opened = self.server.get("/api/open?path=sweep.json")
        self.assertTrue(opened["ok"])
        self.assertEqual(self.state()["doc"], before)
        feature = json.loads(before)["features"][0]
        params = dict(feature["params"])
        params["path_points_nm"] = [[0, 0, 0], [0, 0, 20 * MM]]
        self.cmd("edit_feature_params", {"feature_uuid": item["uuid"], "params": params})
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18, volume * 2, places=5)
        self.cmd("undo")
        self.assertEqual(self.state()["doc"], before)
        self.cmd("suppress_solid", {"feature_uuid": item["uuid"], "suppressed": True})
        self.assertEqual(self.mesh()["solids"], [])
        self.cmd("suppress_solid", {"feature_uuid": item["uuid"], "suppressed": False})
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18, volume, places=5)
        self.reject_unchanged("edit_feature_params", {"feature_uuid": item["uuid"],
                              "params": dict(params, path_points_nm=[[0, 0, 0], [0, 0, 0]])})
        self.reject_unchanged("suppress_solid", {"feature_uuid": item["uuid"], "suppressed": "false"})

    def test_edge_finishing_real_volume_and_failed_operation_atomicity(self):
        self.box()
        self.cmd("fillet_solid", {"size_nm": 2 * MM, "edge_indices": [1]})
        fillet = self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(fillet, 24000 - (4 - math.pi) * 30, places=5)
        self.cmd("undo")
        self.cmd("chamfer_solid", {"size_nm": 2 * MM, "edge_indices": [1]})
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18,
                               24000 - 60, places=5)
        self.reject_unchanged("fillet_solid", {"size_nm": 100 * MM, "edge_indices": [1]})
        self.reject_unchanged("chamfer_solid", {"size_nm": True, "edge_indices": [1]})
        self.reject_unchanged("chamfer_solid", {"size_nm": MM, "edge_indices": [999]})

    def test_pattern_mirror_and_undo_rebuild_real_compound(self):
        self.box()
        self.cmd("pattern_solid", {"translations_nm": [[0, 0, 0], [50 * MM, 0, 0]]})
        mesh = self.mesh()["solids"][0]
        self.assertEqual(mesh["solid_count"], 2)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, 48000, places=5)
        self.cmd("mirror_solid", {"normal_axis": "x", "offset_nm": 0})
        self.assertEqual(self.mesh()["solids"][0]["properties"]["bbox_nm"][0], -90 * MM)
        self.cmd("undo")
        self.assertEqual(self.mesh()["solids"][0]["properties"]["bbox_nm"][0], 0)
        self.reject_unchanged("pattern_solid", {"translations_nm": [[0, 0, 0], [0, 0, 0]]})

    def test_loft_real_profile_references(self):
        sketch = self.circle()
        self.cmd("loft_solid", {"profile_uuids": [sketch, sketch],
                               "offsets_nm": [[0, 0, 0], [0, 0, 10 * MM]]})
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18,
                               math.pi * 4 * 10, places=4)
        self.reject_unchanged("loft_solid", {"profile_uuids": [sketch, "missing"],
                             "offsets_nm": [[0, 0, 0], [0, 0, 10 * MM]]})

    def test_import_real_step_and_reexport(self):
        self.box()
        status, headers, step = self.server.request("GET", "/api/step")
        self.assertEqual(status, 200)
        self.assertTrue(step.startswith(b"ISO-10303-21;"))
        self.cmd("undo")
        self.cmd("import_step", {"step_text": step.decode("ascii")})
        self.assertAlmostEqual(self.mesh()["solids"][0]["properties"]["volume_nm3"] / 1e18,
                               24000, places=5)
        before = self.state()["doc"]
        status, saved = self.server.post("/api/save", {"path": "import.json"})
        self.assertTrue(saved["ok"])
        self.cmd("undo")
        status, opened = self.server.get("/api/open?path=import.json")
        self.assertTrue(opened["ok"])
        self.assertEqual(self.state()["doc"], before)
        self.reject_unchanged("import_step", {"step_text": "ISO-10303-21;"})

    def test_advanced_page_and_three_catalogs_have_identical_keys(self):
        status, headers, page = self.server.request("GET", "/web/3d.html")
        self.assertEqual(status, 200)
        self.assertIn(b'id="advanced"', page)
        self.assertIn(b'id="feature-tree"', page)
        keys = []
        for code in ("en", "ja", "zh-CN"):
            status, response = self.server.post("/api/locale", {"locale": code})
            self.assertTrue(response["ok"])
            strings = self.state()["strings"]
            keys.append({key for key in strings if key.startswith("solid.")})
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[0], keys[2])
        self.assertGreater(len(keys[0]), 20)
        status, headers, asset = self.server.request("GET", "/web/js/advanced3d.js")
        self.assertEqual(status, 200)
        self.assertGreater(len(asset), 1000)
