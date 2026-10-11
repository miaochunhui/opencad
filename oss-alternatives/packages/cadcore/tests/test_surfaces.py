# SPDX-License-Identifier: Apache-2.0
"""Exact FreeCAD surface acceptance; no dependency skips or fake geometry.

python3 -B oss-alternatives/packages/cadcore/tests/test_surfaces.py
    --evidence-dir /hf3fs-jd/data/tmp/opencad-industrial-surfaces-20261011/seat-surfaces
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadcore.kernel.surfaces import (
    DEFAULT_EVIDENCE, SurfaceKernel, SurfaceExecutionError, make_nurbs,
    make_trim, make_sew, make_offset, make_thicken, validate_recipe,
)

MM = 1_000_000
RUN_DIR = DEFAULT_EVIDENCE / (
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)


def plane(origin=(0, 0, 0), u=(10, 0, 0), v=(0, 10, 0)):
    poles = [[[int((origin[k] + i * u[k] + j * v[k]) * MM) for k in range(3)]
              for j in range(2)] for i in range(2)]
    return make_nurbs(poles_nm=poles, u_degree=1, v_degree=1,
                      u_knots=[0, 1], v_knots=[0, 1],
                      u_mults=[2, 2], v_mults=[2, 2])


def parabola(x0=0, z=(0, 0, 10)):
    return make_nurbs(
        poles_nm=[[[int(x * MM), int(y * MM), int(h * MM)] for y in (0, 10)]
                  for x, h in zip((x0, x0 + 5, x0 + 10), z)],
        u_degree=2, v_degree=1, u_knots=[0, 1], v_knots=[0, 1],
        u_mults=[3, 3], v_mults=[2, 2])


def quarter_cylinder():
    return make_nurbs(
        poles_nm=[[[x * MM, y * MM, z * MM] for z in (0, 10)]
                  for x, y in ((5, 0), (5, 5), (0, 5))],
        u_degree=2, v_degree=1, u_knots=[0, 1], v_knots=[0, 1],
        u_mults=[3, 3], v_mults=[2, 2],
        weights=[[1, 1], [math.sqrt(0.5), math.sqrt(0.5)], [1, 1]])


def polygon_plate(surface=None):
    """Concave outer ring with square and triangular holes: 50 mm2 on plane."""
    return make_trim(
        plane() if surface is None else surface,
        outer=[[.1, .1], [.9, .1], [.9, .5], [.6, .5], [.6, .9], [.1, .9], [.1, .1]],
        holes=[[[.2, .2], [.2, .3], [.3, .3], [.3, .2], [.2, .2]],
               [[.4, .2], [.4, .4], [.5, .2], [.4, .2]]])


def cube_faces():
    return [
        plane(), plane((0, 0, 10)),
        plane(u=(10, 0, 0), v=(0, 0, 10)),
        plane((0, 10, 0), u=(10, 0, 0), v=(0, 0, 10)),
        plane(u=(0, 10, 0), v=(0, 0, 10)),
        plane((10, 0, 0), u=(0, 10, 0), v=(0, 0, 10)),
    ]


def seam_spec(*, continuity="G2", reverse=False, left=1, right=2):
    return dict(left=left, right=right, left_edge="u_max", right_edge="u_min",
                reverse=reverse, continuity=continuity, samples=17,
                position_tolerance_nm=100, angle_tolerance_rad=1e-6,
                curvature_tolerance_per_mm=1e-6)


class SurfaceSchemaTests(unittest.TestCase):
    def test_schema_detaches_preserves_nm_and_rational_weights(self):
        value = plane()
        detached = validate_recipe(value)
        value["poles_nm"][0][0][0] = MM
        self.assertEqual(detached["poles_nm"][0][0][0], 0)
        self.assertEqual(json.loads(json.dumps(detached, allow_nan=False)), detached)
        self.assertNotEqual(quarter_cylinder()["weights"][1][0], 1)

    def test_poles_reject_bool_float_nonfinite_ragged_and_unsafe_nm(self):
        for value in (True, 0.0, float("nan"), float("inf"), 2 ** 53 + 1):
            recipe = plane()
            recipe["poles_nm"][0][0][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_recipe(recipe)
        for value in ([], [[0, 0, 0]], [[[0, 0, 0]], [[0, 0, 0], [1, 1, 1]]]):
            recipe = plane()
            recipe["poles_nm"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_recipe(recipe)

    def test_knots_degrees_weights_unknown_fields_rejected(self):
        mutations = [
            ("u_degree", True), ("u_degree", 0), ("u_degree", 2),
            ("u_knots", [0, 0]), ("u_knots", [0, float("inf")]),
            ("u_mults", [1, 2]), ("u_mults", [2, True]),
            ("weights", [[0, 1], [1, 1]]),
            ("weights", [[1, 1], [1, float("nan")]]),
            ("weights", [[1]]), ("unknown", 1),
        ]
        for name, value in mutations:
            recipe = plane()
            recipe[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                validate_recipe(recipe)

    def test_trim_bounds_schema_rejected(self):
        for bounds in ([], [0, 1, 0], [1, 0, 0, 1], [0, 1, True, 1]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                validate_recipe(dict(op="trim", surface=plane(), bounds=bounds))

    def test_trim_loop_schema_detaches_exact_shape_and_legacy_exclusivity(self):
        recipe = polygon_plate()
        before = copy.deepcopy(recipe)
        detached = validate_recipe(recipe)
        recipe["outer"][0][0] = .2
        self.assertEqual(detached, before)
        self.assertEqual(len(detached["holes"]), 2)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            make_trim(plane(), outer=before["outer"], holes=[], u_min=0)
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_recipe({**before, "bounds": [0, 1, 0, 1]})
        with self.assertRaisesRegex(ValueError, "nested polygon"):
            make_trim(before, u_min=0, u_max=1, v_min=0, v_max=1)
        rectangular = make_trim(plane(), u_min=.1, u_max=.9, v_min=.1, v_max=.9)
        self.assertEqual(polygon_plate(rectangular)["surface"], rectangular)

    def test_trim_bad_loops_nonfinite_orientation_selftouch_and_outside(self):
        valid = polygon_plate()
        mutations = [
            {"outer": valid["outer"][:-1]},
            {"outer": list(reversed(valid["outer"]))},
            {"outer": [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]},
            {"outer": [[0, 0], [.5, 0], [.25, 0], [0, 1], [0, 0]]},
            {"outer": [[0, 0], [0, 0], [1, 1], [0, 0]]},
            {"outer": [[0, 0], [.5, 0], [1, 0], [0, 0]]},
            {"outer": [[0, 0], [1, 0], [1, True], [0, 0]]},
            {"outer": [[0, 0], [1, 0], [1, float("inf")], [0, 0]]},
            {"outer": [[0, 0], [1, 0], [1, float("nan")], [0, 0]]},
            {"outer": [[-.1, 0], [1, 0], [1, 1], [-.1, 0]]},
            {"holes": [list(reversed(valid["holes"][0]))]},
            {"holes": [[[.7, .6], [.7, .7], [.8, .7], [.8, .6], [.7, .6]]]},
            {"holes": [[[.05, .2], [.05, .3], [.2, .3], [.2, .2], [.05, .2]]]},
            {"holes": [valid["holes"][0], valid["holes"][0]]},
            {"holes": [valid["holes"][0],
                       [[.22, .22], [.22, .25], [.25, .25], [.25, .22], [.22, .22]]]},
            {"holes": [valid["holes"][0],
                       [[.3, .2], [.3, .3], [.4, .3], [.4, .2], [.3, .2]]]},
            {"holes": [valid["holes"][0]] * 17},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_recipe({**valid, **mutation})
        repeated = [[index / 130, 0] for index in range(130)] + [[0, 1], [0, 0]]
        with self.assertRaisesRegex(ValueError, "vertices"):
            make_trim(plane(), outer=repeated)

    def test_trim_diagonal_and_collinear_forward_vertices_are_not_rectangles(self):
        triangle = make_trim(plane(), outer=[[.1, .1], [.5, .1], [.9, .1],
                                             [.5, .9], [.1, .1]])
        self.assertEqual(len(triangle["outer"]), 5)
        self.assertNotIn("bounds", triangle)

    def test_offset_thickness_strict_units_and_boolean(self):
        for value in (True, 0, 0.5, MM * 1.0, -(2 ** 53 + 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_offset(plane(), offset_nm=value)
        for value in (True, 0, -MM, MM * 1.0, 2 ** 53 + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_thicken(plane(), thickness_nm=value)
        with self.assertRaises(ValueError):
            make_thicken(plane(), thickness_nm=MM, inward=1)

    def test_sew_invalid_refs_and_schema_rejected(self):
        for key, value in (("left", 0), ("right", 3), ("left", True),
                           ("right", 1), ("continuity", "C3"), ("reverse", 1),
                           ("samples", 2), ("position_tolerance_nm", 0),
                           ("angle_tolerance_rad", 2), ("unknown", True)):
            spec = seam_spec()
            spec[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                make_sew(surfaces=[plane(), plane((10, 0, 0))], seams=[spec])

    def test_budget_cyclic_and_solid_inputs_rejected(self):
        recipe = plane()
        for _ in range(10):
            recipe = dict(op="trim", surface=recipe, bounds=[0, 1, 0, 1])
        with self.assertRaisesRegex(ValueError, "budget"):
            validate_recipe(recipe)
        cycle = dict(op="trim", bounds=[0, 1, 0, 1])
        cycle["surface"] = cycle
        with self.assertRaisesRegex(ValueError, "acyclic"):
            validate_recipe(cycle)
        with self.assertRaisesRegex(ValueError, "solid"):
            make_offset(make_sew(surfaces=cube_faces(), solid=True), offset_nm=MM)

    def test_host_has_no_heavy_imports_and_worker_is_actual_source(self):
        import cadcore.kernel.surfaces as module
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import FreeCAD", source)
        self.assertNotIn("import Part", source)
        worker = Path(module.__file__).with_name("surfaces_worker.py")
        self.assertTrue(worker.is_file())
        self.assertIn("surface.buildFromPolesMultsKnots(", worker.read_text(encoding="utf-8"))
        self.assertFalse("FreeCAD" in sys.modules or "Part" in sys.modules)


class RealSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUN_DIR
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        cls.kernel = SurfaceKernel(evidence_dir=RUN_DIR / "jobs")
        cls.probe = cls.kernel.probe()
        if not all(cls.probe["apis"].values()):
            raise AssertionError("real surface APIs missing")
        if cls.probe["backend"]["occt"] in ("", "unmeasured"):
            raise AssertionError("OCCT version not measured")
        cls.measurements = []

    @classmethod
    def tearDownClass(cls):
        (RUN_DIR / "measurements.json").write_text(
            json.dumps(cls.measurements, indent=2, sort_keys=True), encoding="utf-8")

    def record(self, value):
        self.measurements.append(dict(test=self.id(), result=value))
        return value

    def error(self, recipe, pattern):
        with self.assertRaisesRegex(SurfaceExecutionError, pattern) as caught:
            self.kernel.build(recipe)
        error = caught.exception
        self.assertEqual(error.reason, "op_failed")
        result = error.details["worker_result"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["traceback"])
        self.assertNotEqual(error.details["rc"], 0)
        path = Path(error.details["job_dir"])
        self.assertRegex((path / "stderr.log").read_text(encoding="utf-8"), pattern)
        self.assertIn(result["error"], (path / "stderr.log").read_text(encoding="utf-8"))
        self.record(error.details)

    def test_actual_api_probe_and_no_mock_or_host_freecad(self):
        self.assertEqual(self.probe["marker"], "cadcore-surfaces-v1")
        self.assertEqual(self.probe["execution"]["rc"], 0)
        self.assertNotIn("mock", self.probe["execution"]["executable"].lower())
        self.assertEqual(self.probe["execution"]["sources_before"],
                         self.probe["execution"]["sources_after"])
        self.assertFalse("FreeCAD" in sys.modules or "Part" in sys.modules)
        self.record(self.probe)

    def test_backend_version_preserves_measured_components(self):
        backend = self.probe["backend"]
        self.assertGreaterEqual(len(backend["freecad_components"]), 3)
        self.assertFalse(backend["freecad"].endswith("."))
        self.assertEqual(backend["freecad"], ".".join(
            x for x in backend["freecad_components"][:3] if x))

    def test_plane_exact_nurbs_area_open_brep_step_and_mesh(self):
        result = self.record(self.kernel.build(plane()))
        props = result["properties"]
        self.assertEqual(props["shape_type"], "Face")
        self.assertEqual(props["solid_count"], 0)
        self.assertIsNone(props["volume_mm3"])
        self.assertAlmostEqual(props["area_mm2"], 100, delta=1e-8)
        self.assertEqual(props["faces"][0]["type"], "BSplineSurface")
        self.assertTrue(result["mesh"]["triangles"])
        for fmt in ("brep", "step"):
            artifact = result["artifacts"][fmt]
            self.assertGreater(artifact["bytes"], 100)
            self.assertEqual(hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest(),
                             artifact["sha256"])
            self.assertEqual(result["roundtrip"][fmt]["solid_count"], 0)

    def test_rational_quarter_cylinder_points_and_area(self):
        result = self.record(self.kernel.evaluate(quarter_cylinder(),
                            uv=[[0, 0], [0.25, 0.5], [0.5, 0.5], [1, 1]]))
        for row in result["evaluations"]:
            x, y, z = row["point_mm"]
            self.assertAlmostEqual(x * x + y * y, 25, delta=1e-9)
            self.assertTrue(0 <= z <= 10)
        midpoint = result["evaluations"][2]["point_mm"]
        self.assertAlmostEqual(midpoint[0], 5 / math.sqrt(2), delta=1e-9)
        self.assertAlmostEqual(midpoint[1], 5 / math.sqrt(2), delta=1e-9)
        props = result["properties"]
        self.assertTrue(props["faces"][0]["rational"])
        self.assertAlmostEqual(props["area_mm2"], math.pi * 25, delta=1e-6)
        exported = self.record(self.kernel.build(quarter_cylinder()))
        self.assertTrue(exported["roundtrip"]["brep"]["faces"][0]["rational"])

    def test_trim_exact_domain_area_and_outside_rejected(self):
        recipe = make_trim(plane(), u_min=0.2, u_max=0.8, v_min=0.25, v_max=0.75)
        result = self.record(self.kernel.build(recipe))
        self.assertAlmostEqual(result["properties"]["area_mm2"], 30, delta=1e-8)
        for actual, expected in zip(result["properties"]["bbox_mm"], [2, 2.5, 0, 8, 7.5, 0]):
            self.assertAlmostEqual(actual, expected, delta=3e-7)
        self.assertEqual(result["properties"]["bbox_tolerance_mm"], 1e-6)
        evaluation = self.kernel.evaluate(recipe, uv=[[0.2, 0.25], [0.8, 0.75]])
        for actual, expected in zip(evaluation["evaluations"][0]["point_mm"], [2, 2.5, 0]):
            self.assertAlmostEqual(actual, expected, delta=1e-9)
        self.error(make_trim(recipe, u_min=0.1, u_max=0.8, v_min=0.25, v_max=0.75),
                   "trim bounds exceed")

    def test_polygon_trim_concave_two_holes_exact_area_and_export_topology(self):
        recipe = polygon_plate()
        result = self.record(self.kernel.build(recipe))
        self.assertEqual(result["properties"]["shape_type"], "Face")
        self.assertEqual(result["properties"]["faces"][0]["type"], "BSplineSurface")
        self.assertAlmostEqual(result["properties"]["area_mm2"], 50, delta=1e-8)
        self.assertEqual(result["properties"]["inner_wire_count"], 2)
        self.assertEqual(result["properties"]["faces"][0]["wire_count"], 3)
        self.assertTrue(result["properties"]["faces"][0]["wires_closed"])
        for fmt in ("brep", "step"):
            self.assertEqual(result["roundtrip"][fmt]["inner_wire_count"], 2)
            self.assertAlmostEqual(result["roundtrip"][fmt]["area_mm2"], 50, delta=1e-7)
        # The notch is inside the bounding rectangle but outside the exact face.
        for uv in ([[.25, .25]], [[.7, .7]]):
            with self.assertRaisesRegex(SurfaceExecutionError, "actual trimmed UV domain") as caught:
                self.kernel.evaluate(recipe, uv=uv)
            self.record(caught.exception.details)

    def test_polygon_trim_thicken_keeps_through_holes_and_analytic_volume(self):
        for inward in (False, True):
            with self.subTest(inward=inward):
                result = self.record(self.kernel.build(make_thicken(
                    polygon_plate(), thickness_nm=2 * MM, inward=inward)))
                self.assertEqual(result["properties"]["solid_count"], 1)
                self.assertTrue(result["properties"]["closed"])
                self.assertAlmostEqual(result["properties"]["volume_mm3"], 100, delta=1e-7)
                points = result["mesh"]["vertices_mm"]
                triangles = result["mesh"]["triangles"]
                volume, edges = 0, {}
                for tri in triangles:
                    a, b, c = [points[i] for i in tri]
                    volume += (a[0] * (b[1]*c[2] - b[2]*c[1]) +
                               a[1] * (b[2]*c[0] - b[0]*c[2]) +
                               a[2] * (b[0]*c[1] - b[1]*c[0])) / 6
                    for i, j in ((0, 1), (1, 2), (2, 0)):
                        key = tuple(sorted(tuple(round(x, 8) for x in points[tri[k]])
                                           for k in (i, j)))
                        edges[key] = edges.get(key, 0) + 1
                self.assertAlmostEqual(abs(volume), 100, delta=1e-7)
                self.assertTrue(all(count == 2 for count in edges.values()))
                # Two end faces, each with the original two holes.
                self.assertEqual(result["properties"]["inner_wire_count"], 4)
                for fmt in ("brep", "step"):
                    self.assertEqual(result["roundtrip"][fmt]["inner_wire_count"], 4)
                    self.assertAlmostEqual(result["roundtrip"][fmt]["volume_mm3"], 100, delta=1e-6)

    def test_diagonal_triangle_trim_not_bbox_analytic_area(self):
        recipe = make_trim(plane(), outer=[[.1, .1], [.9, .1], [.5, .9], [.1, .1]])
        result = self.record(self.kernel.build(recipe))
        self.assertAlmostEqual(result["properties"]["area_mm2"], 32, delta=1e-8)
        self.assertEqual(result["properties"]["inner_wire_count"], 0)
        thick = self.record(self.kernel.build(make_thicken(recipe, thickness_nm=MM)))
        self.assertAlmostEqual(thick["properties"]["volume_mm3"], 32, delta=1e-7)

    def test_polygon_cylinder_exact_support_points_pcurves_and_hole_domain(self):
        recipe = polygon_plate(quarter_cylinder())
        result = self.record(self.kernel.evaluate(recipe, uv=[[.1, .1], [.5, .6], [.6, .9]]))
        self.assertTrue(result["properties"]["faces"][0]["rational"])
        self.assertEqual(result["properties"]["inner_wire_count"], 2)
        for row in result["evaluations"]:
            u, v = row["uv"]
            x, y, z = row["point_mm"]
            weight = math.sqrt(.5)
            denominator = (1 - u) ** 2 + 2 * weight * u * (1 - u) + u * u
            self.assertAlmostEqual(x, 5 * ((1-u)**2 + 2*weight*u*(1-u))/denominator, delta=1e-9)
            self.assertAlmostEqual(y, 5 * (2*weight*u*(1-u) + u*u)/denominator, delta=1e-9)
            self.assertAlmostEqual(x*x + y*y, 25, delta=1e-9)
            self.assertAlmostEqual(z, 10 * v, delta=1e-9)
        exported = self.record(self.kernel.build(recipe))
        self.assertEqual(exported["roundtrip"]["step"]["inner_wire_count"], 2)
        with self.assertRaisesRegex(SurfaceExecutionError, "inside a hole"):
            self.kernel.evaluate(recipe, uv=[[.25, .25]])

    def test_polygon_sew_preserves_holes_and_no_face_is_discarded(self):
        left = polygon_plate()
        right = make_trim(plane((10, 0, 0)),
                          outer=[[0, .1], [.8, .1], [.8, .9], [0, .9], [0, .1]])
        # Extend the left outer to the shared edge; keep its notch and both holes.
        left["outer"] = [[.1, .1], [1, .1], [1, .5], [.6, .5], [.6, .9], [.1, .9], [.1, .1]]
        # Match only the left boundary's actual v span, not its parameter bbox.
        right["outer"] = [[0, .1], [.8, .1], [.8, .5], [0, .5], [0, .1]]
        result = self.record(self.kernel.build(make_sew(surfaces=[left, right])))
        self.assertEqual(result["properties"]["shell_count"], 1)
        self.assertEqual(result["properties"]["face_count"], 2)
        self.assertEqual(result["properties"]["inner_wire_count"], 2)
        self.assertAlmostEqual(result["properties"]["area_mm2"], 86, delta=1e-7)
        self.assertEqual(result["roundtrip"]["step"]["inner_wire_count"], 2)

    def test_worker_raw_bad_loops_visible_firstcause_and_no_fallback(self):
        recipe = polygon_plate()
        recipe["outer"] = [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]
        with self.assertRaisesRegex(SurfaceExecutionError, "self-intersects") as caught:
            self.kernel._job(dict(op="build", surface=recipe, deflection_mm=.05))
        error = caught.exception
        self.record(error.details)
        self.assertFalse(error.details["worker_result"]["ok"])
        self.assertIn("self-intersects", error.details["worker_result"]["traceback"])
        self.assertNotIn("artifacts", error.details["worker_result"])
        tiny = make_trim(plane(), outer=[[.1, .1], [.1+1e-12, .1],
                                         [.1, .1+1e-12], [.1, .1]])
        self.error(tiny, "wire|BRep|OCCError|null|area")

    def test_g2_planes_and_exact_curved_patches_positive(self):
        for left, right in ((plane(), plane((10, 0, 0))),
                            (parabola(), parabola(10, (10, 20, 40)))):
            with self.subTest(left=left["u_degree"]):
                report = self.record(self.kernel.check_seam(left, right))["seam"]
                self.assertTrue(report["passed"])
                self.assertTrue(report["g0"] and report["g1"] and report["g2"])
                self.assertLessEqual(report["max_gap_nm"], 1e-6)
                self.assertLess(report["max_curvature_difference_per_mm"], 1e-10)
                self.assertGreaterEqual(report["sample_count"], 17)
                self.assertFalse(report["whole_domain_proof"])

    def test_g0_gap_negative_not_visual_success(self):
        report = self.record(self.kernel.check_seam(plane(), plane((10.01, 0, 0))))["seam"]
        self.assertFalse(report["passed"])
        self.assertFalse(report["g0"])
        self.assertAlmostEqual(report["max_gap_nm"], 10000, delta=1e-5)

    def test_g1_crease_negative_but_g0_positive(self):
        right = plane((10, 0, 0), u=(10, 0, 5))
        report = self.record(self.kernel.check_seam(plane(), right, continuity="G1"))["seam"]
        self.assertTrue(report["g0"])
        self.assertFalse(report["g1"])
        self.assertAlmostEqual(report["max_angle_rad"], math.atan(0.5), delta=1e-8)
        self.assertTrue(self.kernel.check_seam(plane(), right, continuity="G0")["seam"]["passed"])

    def test_g2_curvature_negative_despite_matching_tangents(self):
        right = parabola(10)
        report = self.record(self.kernel.check_seam(plane(), right))["seam"]
        self.assertTrue(report["g0"] and report["g1"])
        self.assertFalse(report["g2"])
        self.assertAlmostEqual(report["max_curvature_difference_per_mm"], 0.2, delta=1e-9)

    def test_reversed_parameter_correspondence_positive_and_negative(self):
        right = plane((10, 10, 0), v=(0, -10, 0))
        report = self.record(self.kernel.check_seam(plane(), right, reverse=True))["seam"]
        self.assertTrue(report["passed"])
        self.assertFalse(self.kernel.check_seam(plane(), right)["seam"]["passed"])

    def test_g2_guard_rejects_bad_sew_before_topology_and_retains_firstcause(self):
        recipe = make_sew(surfaces=[plane(), parabola(10)], seams=[seam_spec()])
        self.error(recipe, "sew seam continuity rejected")
        good = self.record(self.kernel.build(make_sew(
            surfaces=[plane(), plane((10, 0, 0))], seams=[seam_spec()])))
        self.assertTrue(good["seams"][0]["passed"])
        self.assertEqual(good["properties"]["solid_count"], 0)
        self.assertAlmostEqual(good["properties"]["area_mm2"], 200, delta=1e-7)

    def test_six_exact_faces_sewn_closed_solid_analytic_volume(self):
        result = self.record(self.kernel.build(make_sew(surfaces=cube_faces(), solid=True)))
        props = result["properties"]
        self.assertEqual(props["solid_count"], 1)
        self.assertEqual(props["face_count"], 6)
        self.assertTrue(props["valid"] and props["closed"])
        self.assertAlmostEqual(props["volume_mm3"], 1000, delta=1e-7)
        self.assertAlmostEqual(props["area_mm2"], 600, delta=1e-7)
        self.assertAlmostEqual(result["roundtrip"]["step"]["volume_mm3"], 1000, delta=1e-7)

    def test_unclosed_and_disconnected_shells_cannot_claim_solid(self):
        self.error(make_sew(surfaces=cube_faces()[:-1], solid=True), "unclosed shell")
        self.error(make_sew(surfaces=[plane(), plane((20, 0, 0))], solid=True),
                   "one connected shell")

    def test_sewing_gap_does_not_close_missing_surface(self):
        faces = cube_faces()
        faces[-1] = plane((10.01, 0, 0), u=(0, 10, 0), v=(0, 0, 10))
        self.error(make_sew(surfaces=faces, solid=True), "one connected shell|unclosed shell")

    def test_sew_cannot_discard_isolated_face_beside_closed_cube(self):
        faces = cube_faces() + [plane((30, 0, 0))]
        for solid in (False, True):
            with self.subTest(solid=solid):
                self.error(make_sew(surfaces=faces, solid=solid), "one connected shell")

    def test_offset_open_plane_analytic_and_not_solid(self):
        result = self.record(self.kernel.build(make_offset(plane(), offset_nm=2 * MM)))
        props = result["properties"]
        self.assertEqual(props["solid_count"], 0)
        self.assertAlmostEqual(props["area_mm2"], 100, delta=1e-8)
        self.assertAlmostEqual(abs(props["bbox_mm"][2]), 2, delta=3e-7)
        self.assertAlmostEqual(props["bbox_mm"][2], props["bbox_mm"][5], delta=3e-7)

    def test_thicken_open_plane_true_volume_both_directions(self):
        for inward in (False, True):
            with self.subTest(inward=inward):
                result = self.record(self.kernel.build(make_thicken(
                    plane(), thickness_nm=MM, inward=inward)))
                props = result["properties"]
                self.assertEqual(props["solid_count"], 1)
                self.assertTrue(props["closed"])
                self.assertAlmostEqual(props["volume_mm3"], 100, delta=1e-7)
                self.assertAlmostEqual(props["bbox_mm"][5] - props["bbox_mm"][2], 1, delta=3e-7)

    def test_rational_curved_offset_and_thicken_analytic_volume(self):
        offset = self.record(self.kernel.build(make_offset(quarter_cylinder(), offset_nm=MM)))
        self.assertEqual(offset["properties"]["solid_count"], 0)
        self.assertAlmostEqual(offset["properties"]["area_mm2"], 30 * math.pi, delta=1e-6)
        for actual, expected in zip(offset["properties"]["bbox_mm"], [0, 0, 0, 6, 6, 10]):
            self.assertAlmostEqual(actual, expected, delta=1e-6)
        for inward, volume in ((False, 27.5 * math.pi), (True, 22.5 * math.pi)):
            with self.subTest(inward=inward):
                result = self.record(self.kernel.build(make_thicken(
                    quarter_cylinder(), thickness_nm=MM, inward=inward)))
                self.assertEqual(result["properties"]["solid_count"], 1)
                self.assertTrue(result["properties"]["closed"])
                self.assertAlmostEqual(result["properties"]["volume_mm3"], volume, delta=1e-5)
                self.assertGreater(result["offset_checks"][0]["minimum_regular_factor"], 0)
                self.assertFalse(result["offset_checks"][0]["whole_domain_proof"])

    def test_curved_inward_thickness_at_radius_or_beyond_rejected(self):
        for thickness in (5 * MM, 6 * MM):
            with self.subTest(thickness=thickness):
                self.error(make_thicken(quarter_cylinder(), thickness_nm=thickness, inward=True),
                           "valid|closed|positive|BRep|OCCError|focal crossing")

    def test_thickened_solid_mesh_manifold_and_analytic_volume(self):
        result = self.record(self.kernel.build(make_thicken(plane(), thickness_nm=MM)))
        mesh = result["mesh"]
        points, triangles = mesh["vertices_mm"], mesh["triangles"]
        edges, volume = {}, 0
        for tri in triangles:
            self.assertEqual(len(set(tri)), 3)
            self.assertTrue(all(type(i) is int and 0 <= i < len(points) for i in tri))
            a, b, c = [points[i] for i in tri]
            self.assertTrue(all(math.isfinite(x) for p in (a, b, c) for x in p))
            volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) +
                       a[1] * (b[2] * c[0] - b[0] * c[2]) +
                       a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
            for i, j in ((0, 1), (1, 2), (2, 0)):
                edge = tuple(sorted(tuple(round(x, 8) for x in points[tri[k]]) for k in (i, j)))
                edges[edge] = edges.get(edge, 0) + 1
        self.assertTrue(all(count == 2 for count in edges.values()))
        self.assertAlmostEqual(abs(volume), 100, delta=1e-7)

    def test_degenerate_surface_rejected_and_next_valid_job_recovers(self):
        bad = plane(u=(0, 0, 0))
        before = copy.deepcopy(bad)
        self.error(bad, "valid nonempty|positive area|BRep")
        self.assertEqual(before, bad)
        result = self.kernel.build(plane())
        self.assertAlmostEqual(result["properties"]["area_mm2"], 100, delta=1e-7)

    def test_worker_revalidates_raw_request_unknown_and_bad_units(self):
        for field, value, pattern in (("unknown", 1, "unknown"),
                                      ("poles_nm", [[[True, 0, 0]]], "poles")):
            recipe = plane()
            recipe[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(SurfaceExecutionError, pattern):
                self.kernel._job(dict(op="build", surface=recipe, deflection_mm=0.05))
        with self.assertRaisesRegex(SurfaceExecutionError, "unknown"):
            self.kernel._job(dict(op="probe", unknown=True))


def run_acceptance():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--only", choices=("probe", "all"), default="all")
    parser.add_argument("--repository-check", action="store_true",
                        help="also run independent source scan and exact unittest discovery")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc)
    RUN_DIR = args.evidence_dir / (stamp.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    (RUN_DIR / "write-probe").touch(exist_ok=False)
    package = Path(__file__).resolve().parents[1]
    repo = package.parents[2]
    sources = [Path(__file__).resolve(), package / "cadcore/kernel/surfaces.py",
               package / "cadcore/kernel/surfaces_worker.py",
               repo / "docs/design/industrial-surfaces.md"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    started = time.monotonic()
    if args.only == "probe":
        suite = unittest.TestSuite([RealSurfaceTests("test_actual_api_probe_and_no_mock_or_host_freecad")])
    else:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    with (RUN_DIR / "tests.log").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    checks = []
    if args.repository_check and result.wasSuccessful() and not result.skipped:
        commands = [
            ("source-scan", [sys.executable, "-B", str(repo / "tools/acceptance.py"),
                             "--stage", "scan", "--out", str(RUN_DIR / "source-scan")]),
            ("discovery", [sys.executable, "-B", str(repo / "tools/acceptance.py"),
                           "--suite", str(package / "tests"), "--top", str(package),
                           "--pattern", "test_surfaces.py"]),
        ]
        for label, command in commands:
            with (RUN_DIR / (label + ".log")).open("xb") as log:
                child = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            checks.append(dict(label=label, argv=command, rc=child.returncode))
            if child.returncode:
                break
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    summary = dict(
        verdict="PASS" if result.wasSuccessful() and result.testsRun > 0 and
        not result.skipped and before == after and
        all(check["rc"] == 0 for check in checks) else "FAIL",
        tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), uid=os.getuid(), elapsed_s=time.monotonic() - started,
        started_at=stamp.isoformat(), finished_at=datetime.now(timezone.utc).isoformat(),
        sources_before=before, sources_after=after, source_drift=before != after,
        repository_checks=checks,
        evidence_dir=str(RUN_DIR),
        failure_details=[dict(test=str(t), traceback=trace) for t, trace in result.failures + result.errors])
    (RUN_DIR / "result.json").write_text(json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
    print("SURFACES_ACCEPTANCE verdict=%s tests=%s failures=%s errors=%s skipped=%s drift=%s elapsed_s=%.3f evidence=%s"
          % (summary["verdict"], summary["tests"], summary["failures"], summary["errors"],
             summary["skipped"], summary["source_drift"], summary["elapsed_s"], RUN_DIR))
    if summary["verdict"] != "PASS":
        print((RUN_DIR / "tests.log").read_text(encoding="utf-8")[-12000:])
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_acceptance())
