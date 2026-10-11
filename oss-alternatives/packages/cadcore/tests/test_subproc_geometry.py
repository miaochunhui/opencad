"""Real FreeCAD regression: shape references, symmetric solids and sections."""

import json
import unittest

from cadcore.errors import CadError
from cadcore.kernel.subproc import SubprocessKernel
from cadcore.ops import sw_drawings

MM = 1_000_000


class RealGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = SubprocessKernel()
        if not cls.kernel.available():
            raise unittest.SkipTest("FreeCAD CLI unavailable; numeric gate must verify a real backend")

    @classmethod
    def tearDownClass(cls):
        cls.kernel.close()

    def model(self):
        return sw_drawings._sample_model().to_payload()

    def test_l1_document_and_sketch_references(self):
        payload = self.model()
        props = self.kernel.mass_properties(payload)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 48000, places=6)
        sketch = payload["sketches"][0]
        projection = self.kernel.project_to_2d(sketch, view="top", hidden=False)
        self.assertGreater(len(projection.curves), 0)
        self.assertFalse(projection.hidden_measured)
        with self.assertRaises(CadError) as caught:
            self.kernel.project_to_2d(sketch, view="top", hidden=True)
        self.assertEqual(caught.exception.reason, "wire_has_no_hidden_geometry")

    def test_symmetric_extrusion_is_full_depth_and_centered(self):
        payload = self.model()
        payload["features"][0]["params"]["symmetric"] = True
        props = self.kernel.mass_properties(payload)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 48000, places=6)
        self.assertEqual(props.bbox_nm[2::3], (-10 * MM, 10 * MM))
        self.assertEqual(props.center_nm[2], 0)

    def test_construction_geometry_does_not_change_real_solid(self):
        payload = self.model()
        payload["sketches"][0]["entities"].append({
            "uuid": "construction", "kind": "line", "construction": True,
            "geom": {"x1": -100 * MM, "y1": -100 * MM, "x2": 100 * MM, "y2": 100 * MM}})
        props = self.kernel.mass_properties(payload)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 48000, places=6)
        self.assertEqual(props.bbox_nm, (0, 0, 0, 60 * MM, 40 * MM, 20 * MM))

    def test_principal_sketch_planes_control_extrusion_direction(self):
        for plane, bbox in (
            ("XZ", (0, -20 * MM, 0, 60 * MM, 0, 40 * MM)),
            ("YZ", (0, 0, 0, 20 * MM, 60 * MM, 40 * MM)),
        ):
            payload = self.model()
            payload["sketches"][0]["plane"] = plane
            with self.subTest(plane=plane):
                props = self.kernel.mass_properties(payload)
                self.assertEqual(props.bbox_nm, bbox)
                self.assertAlmostEqual(props.volume_nm3 / 1e18, 48000, places=6)
        payload["sketches"][0]["plane"] = "unknown-plane"
        with self.assertRaises(CadError) as caught:
            self.kernel.mass_properties(payload)
        self.assertEqual(caught.exception.reason, "unsupported_sketch_plane")

    def test_arc_profile_rotates_with_sketch_plane(self):
        import math

        payload = self.model()
        payload["sketches"][0]["entities"] = [
            {"kind": "arc", "geom": {"cx": 0, "cy": 0, "r": 10 * MM,
                                     "start_deg": 0, "end_deg": 180}},
            {"kind": "line", "geom": {"x1": -10 * MM, "y1": 0, "x2": 10 * MM, "y2": 0}},
        ]
        for plane, bbox in (
            ("XY", (-10 * MM, 0, 0, 10 * MM, 10 * MM, 20 * MM)),
            ("XZ", (-10 * MM, -20 * MM, 0, 10 * MM, 0, 10 * MM)),
            ("YZ", (0, -10 * MM, 0, 20 * MM, 10 * MM, 10 * MM)),
        ):
            payload["sketches"][0]["plane"] = plane
            with self.subTest(plane=plane):
                props = self.kernel.mass_properties(payload)
                self.assertEqual(props.bbox_nm, bbox)
                self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 100 * 10, places=6)

    def test_mesh_has_real_vertices_indices_and_solid_properties(self):
        mesh = self.kernel.tessellate(self.model())
        self.assertEqual(mesh["solid_count"], 1)
        self.assertGreaterEqual(len(mesh["triangles"]), 12)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, 48000, places=6)
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            for index in triangle:
                self.assertGreaterEqual(index, 0)
                self.assertLess(index, len(mesh["vertices_mm"]))
        for value in (0, -1, 3, float("nan"), True):
            with self.subTest(value=value), self.assertRaises(CadError):
                self.kernel.tessellate(self.model(), deflection_mm=value)

    def test_invalid_solid_parameters_are_rejected_before_recipe_creation(self):
        wire = self.kernel.sketch_to_wire(self.model()["sketches"][0])
        for depth in (0, -MM, True, 1.5, "6000000", float("nan")):
            with self.subTest(depth=depth):
                with self.assertRaises(CadError) as caught:
                    self.kernel.extrude(wire, distance_nm=depth)
                self.assertEqual(caught.exception.reason, "invalid_extrusion_depth")
        with self.assertRaises(CadError):
            self.kernel.extrude(wire, distance_nm=MM, symmetric="false")
        for axis, angle in (("bad", 90), ("z", 0), ("x", -1), ("y", 361),
                            ("z", True), ("z", float("nan"))):
            with self.subTest(axis=axis, angle=angle), self.assertRaises(CadError):
                self.kernel.revolve(wire, axis=axis, angle_deg=angle)
        payload = self.model()
        payload["features"][0]["params"]["distance_nm"] = 0
        with self.assertRaises(CadError) as caught:
            self.kernel.mass_properties(payload)
        self.assertEqual(caught.exception.reason, "invalid_extrusion_depth")
        payload["features"][0]["params"] = []
        with self.assertRaises(CadError) as caught:
            self.kernel.mass_properties(payload)
        self.assertEqual(caught.exception.reason, "bad_feature_params")

    def test_second_sketch_cut_is_replayed_and_unknown_feature_rejected(self):
        import copy
        import math

        payload = self.model()
        sketch = copy.deepcopy(payload["sketches"][0])
        sketch["uuid"] = "hole-sketch"
        sketch["entities"] = [{"uuid": "circle", "kind": "circle",
                               "geom": {"cx": 30 * MM, "cy": 20 * MM, "r": 5 * MM}}]
        payload["sketches"].append(sketch)
        payload["features"].append({"uuid": "cut", "kind": "extrude", "sketch_uuid": "hole-sketch",
                                    "params": {"distance_nm": 20 * MM, "op": "cut"}})
        self.assertAlmostEqual(self.kernel.mass_properties(payload).volume_nm3 / 1e18,
                               48000 - math.pi * 25 * 20, places=5)
        payload["features"].append({"uuid": "unsupported", "kind": "loft", "params": {}})
        with self.assertRaises(CadError) as caught:
            self.kernel.mass_properties(payload)
        self.assertEqual(caught.exception.reason, "unsupported_solid_feature")

    def test_default_section_retains_half_real_volume(self):
        doc = sw_drawings._sample_model()
        sheet = sw_drawings._sheet.make_sheet(doc, standard="iso", language="en")
        sw_drawings.section_view(doc, kernel=self.kernel, sheet=sheet)
        self.assertEqual(len(sheet.views), 1)
        self.assertGreater(len(sheet.views[0].visible), 0)
        props = self.kernel.mass_properties(sheet.views[0].shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 24000, places=6)

    def test_explicit_section_retains_requested_side(self):
        doc = sw_drawings._sample_model()
        sheet = sw_drawings._sheet.make_sheet(doc, standard="iso", language="en")
        sw_drawings.section_view(doc, kernel=self.kernel, sheet=sheet,
                                cut_offset_nm=15 * MM, keep="positive")
        props = self.kernel.mass_properties(sheet.views[0].shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 36000, places=6)

    def test_sections_cut_full_depth_on_every_principal_sketch_plane(self):
        for plane in ("XY", "XZ", "YZ"):
            for axis in ("x", "y"):
                for symmetric in (False, True):
                    for keep in ("negative", "positive"):
                        with self.subTest(plane=plane, axis=axis, symmetric=symmetric, keep=keep):
                            doc = sw_drawings._sample_model()
                            doc.sketches[0]["plane"] = plane
                            doc.features[0]["params"]["symmetric"] = symmetric
                            sheet = sw_drawings._sheet.make_sheet(doc, standard="iso", language="en")
                            sw_drawings.section_view(doc, kernel=self.kernel, sheet=sheet,
                                                     cut_axis=axis, keep=keep, hatch=False)
                            props = self.kernel.mass_properties(sheet.views[0].shape)
                            self.assertAlmostEqual(props.volume_nm3 / 1e18, 24000, places=5)

    def test_drawing_and_section_replay_the_later_cut_feature(self):
        import copy
        import math

        payload = self.model()
        sketch = copy.deepcopy(payload["sketches"][0])
        sketch["uuid"] = "section-hole"
        sketch["entities"] = [{"kind": "circle",
                               "geom": {"cx": 30 * MM, "cy": 20 * MM, "r": 5 * MM}}]
        payload["sketches"].append(sketch)
        payload["features"].append({"uuid": "cut", "kind": "extrude",
                                    "sketch_uuid": sketch["uuid"],
                                    "params": {"distance_nm": 20 * MM, "op": "cut"}})
        shape = sw_drawings._shape_for(payload, self.kernel, 0)[0]
        expected = 48000 - math.pi * 25 * 20
        self.assertAlmostEqual(self.kernel.mass_properties(shape).volume_nm3 / 1e18,
                               expected, places=5)
        sheet = sw_drawings._sheet.make_sheet(payload, standard="iso", language="en")
        sw_drawings.section_view(payload, kernel=self.kernel, sheet=sheet, hatch=False)
        props = self.kernel.mass_properties(sheet.views[0].shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, expected / 2, places=5)

    def test_boundary_section_is_rejected_not_empty_success(self):
        for offset in (0, 60 * MM, -MM, 61 * MM):
            with self.subTest(offset=offset), self.assertRaises(CadError) as caught:
                sw_drawings.section_view(sw_drawings._sample_model(), kernel=self.kernel,
                                        cut_offset_nm=offset)
            self.assertEqual(caught.exception.reason, "degenerate_cut")

    def test_hlr_measures_hidden_edges_and_different_views(self):
        from pathlib import Path

        plate = json.loads((Path(__file__).parent / "data/plate.json").read_text())
        spans = []
        for view in ("top", "front", "right"):
            projection = self.kernel.project_to_2d(plate, view=view, hidden=True)
            self.assertTrue(projection.hidden_measured)
            self.assertGreater(projection.hidden_curves, 0)
            self.assertGreater(projection.visible_curves, 0)
            points = [p for curve in projection.curves for p in curve["points_nm"]]
            spans.append(tuple(max(p[i] for p in points) - min(p[i] for p in points) for i in (0, 1)))
        self.assertEqual(set(spans), {(120 * MM, 80 * MM), (120 * MM, 6 * MM), (80 * MM, 6 * MM)})


if __name__ == "__main__":
    unittest.main()
