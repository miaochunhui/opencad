"""Real FreeCAD BRep clearance and fail-loud response acceptance.

sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_features_clearance.py

The direct entry records unique timed results, logs and raw kernel jobs outside
the worktree. Real geometry is never mocked or skipped. Deliberately corrupted
responses are tested in the same real subprocess adapter, after real geometry.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

from cadcore.kernel.features_clearance import (
    ADAPTER_SOURCE, _validate_clearance_response, make_job, measure,
)
from cadcore.kernel.subproc import KernelExecutionError, SubprocessKernel

MM = 1_000_000
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-clearance")
_RUN_ROOT = None
_OBSERVATIONS = []


def box_recipe(x_mm=0, *, y_mm=0, width_mm=10, height_mm=10, depth_mm=10):
    x, y, w, h = [int(value * MM) for value in (x_mm, y_mm, width_mm, height_mm)]
    points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return {"recipe": [
        {"op": "sketch", "sketch": {"plane": "XY", "entities": [
            {"kind": "line", "geom": {"p1": list(points[index]),
                                     "p2": list(points[(index + 1) % 4])}}
            for index in range(4)]}},
        {"op": "extrude", "distance_nm": int(depth_mm * MM), "plane": "XY"},
    ]}


def cylinder_recipe(cx_mm, radius_mm=2):
    return {"recipe": [
        {"op": "sketch", "sketch": {"entities": [
            {"kind": "circle", "geom": {"center": [int(cx_mm * MM), 0],
                                       "radius_nm": int(radius_mm * MM)}}]}},
        {"op": "extrude", "distance_nm": 10 * MM},
    ]}


def axial_cylinder(z_mm=0, *, outer_mm=2, inner_mm=None):
    entities = [{"kind": "circle", "geom": {
        "center": [0, 0, int(z_mm * MM)], "radius_nm": int(outer_mm * MM)}}]
    if inner_mm is not None:
        entities.append({"kind": "circle", "geom": {
            "center": [0, 0, int(z_mm * MM)], "radius_nm": int(inner_mm * MM)}})
    return {"recipe": [
        {"op": "sketch", "sketch": {"entities": entities}},
        {"op": "extrude", "distance_nm": 10 * MM},
    ]}


def translated_cylinder(z_mm):
    value = axial_cylinder()
    value["recipe"].append({
        "op": "_test_clearance_translation", "translation_nm": [0, 0, int(z_mm * MM)],
    })
    return value


def injected_script(template):
    anchor = "\ndef _build_recipe(recipe):"
    branch = '        elif op == "mass":'
    if anchor not in template or branch not in template:
        raise AssertionError("FreeCAD adapter injection coordinates changed")
    fixture = '''
if "_EXTENDED_BUILDERS" not in globals():
    _EXTENDED_BUILDERS = {}
def _test_clearance_translation(step, previous):
    instance = previous.copy()
    instance.translate(_vector(step["translation_nm"]))
    return Part.makeCompound([instance])
_EXTENDED_BUILDERS["_test_clearance_translation"] = _test_clearance_translation
'''
    template = template.replace(anchor, "\n" + ADAPTER_SOURCE + fixture + anchor, 1)
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    if "if op in _EXTENDED_BUILDERS:" not in template:
        template = template.replace(
            dispatch, "    if op in _EXTENDED_BUILDERS:\n"
            "        return _EXTENDED_BUILDERS[op](step, previous)\n" + dispatch, 1)
    if 'elif op == "clearance":' not in template:
        template = template.replace(
            branch, '        elif op == "clearance":\n'
            '            payload["clearance"] = _measure_clearance(job)\n' + branch, 1)
    return template


def valid_response():
    return {"ok": True, "op": "clearance", "clearance": {
        "distance_nm": 5 * MM, "interference_volume_nm3": 0,
        "nearest_points_nm": [[[10 * MM, 0, 0], [15 * MM, 0, 0]]],
        "status": "separated",
    }}


class ClearanceContractTests(unittest.TestCase):
    def test_job_copies_two_json_recipes_without_aliasing(self):
        a, b = box_recipe(), box_recipe(15)
        job = make_job(a=a, b=b)
        self.assertEqual(job, {"op": "clearance", "a": a, "b": b})
        self.assertEqual(json.loads(json.dumps(job)), job)
        a["recipe"][1]["distance_nm"] = MM
        b["recipe"][0]["sketch"]["entities"][0]["geom"]["p1"][0] = 99 * MM
        self.assertEqual(job["a"], box_recipe())
        self.assertEqual(job["b"], box_recipe(15))

    def test_tuple_recipe_mapping_is_normalized(self):
        from types import MappingProxyType

        a = MappingProxyType({"recipe": tuple(box_recipe()["recipe"])})
        self.assertIsInstance(make_job(a=a, b=box_recipe(15))["a"]["recipe"], list)

    def test_empty_and_malformed_operands_fail(self):
        for value in (None, [], {}, {"recipe": []}, {"recipe": {}},
                      {"recipe": [None]}, {"recipe": [{}]},
                      {"recipe": [{"op": True}]}, {"recipe": [{"op": ""}]}):
            for side in ("a", "b"):
                operands = {"a": box_recipe(), "b": box_recipe(15), side: value}
                with self.subTest(side=side, value=value), self.assertRaises(ValueError):
                    make_job(**operands)

    def test_noninteger_boolean_and_nonfinite_nm_fail(self):
        for value in (True, False, 1.5, 1.0, "10", None,
                      float("nan"), float("inf"), float("-inf"), 10 ** 400):
            a = box_recipe()
            a["recipe"][1]["distance_nm"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_job(a=a, b=box_recipe(15))
            a = box_recipe()
            a["recipe"][0]["sketch"]["entities"][0]["geom"]["p1"][0] = value
            with self.subTest(coordinate=value), self.assertRaises(ValueError):
                make_job(a=a, b=box_recipe(15))

    def test_out_of_safe_integer_nm_range_is_rejected_before_kernel(self):
        for value in (2**53 + 1, -2**53 - 1):
            a = box_recipe()
            a["recipe"][0]["sketch"]["entities"][0]["geom"]["p1"][0] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "safe integer"):
                make_job(a=a, b=box_recipe(15))

    def test_nonfinite_or_non_json_data_fail(self):
        for value in (float("nan"), float("inf"), object(), {1: 2}):
            a = box_recipe()
            a["recipe"][0]["bad"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_job(a=a, b=box_recipe(15))

    def test_zero_or_negative_dimensions_fail_but_negative_positions_are_valid(self):
        for depth in (0, -MM):
            a = box_recipe()
            a["recipe"][1]["distance_nm"] = depth
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                make_job(a=a, b=box_recipe(15))
        for radius in (0, -MM):
            a = cylinder_recipe(0)
            a["recipe"][0]["sketch"]["entities"][0]["geom"]["radius_nm"] = radius
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                make_job(a=a, b=box_recipe())
        self.assertEqual(make_job(a=box_recipe(-20), b=box_recipe(-5))["a"], box_recipe(-20))

    def test_missing_empty_false_and_wrong_operation_responses_fail(self):
        for payload in (None, {}, [], {"ok": False}, {"ok": True},
                        {"ok": True, "op": "mass", "clearance": {}},
                        {"ok": True, "op": "clearance", "clearance": None},
                        {"ok": True, "op": "clearance", "clearance": {}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _validate_clearance_response(payload)
        for field in ("distance_nm", "interference_volume_nm3", "nearest_points_nm", "status"):
            response = valid_response()
            del response["clearance"][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                _validate_clearance_response(response)

    def test_response_numbers_must_be_nonnegative_integers(self):
        for field in ("distance_nm", "interference_volume_nm3"):
            for value in (True, -1, 1.0, "0", float("nan"), float("inf"), None):
                response = valid_response()
                response["clearance"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    _validate_clearance_response(response)

    def test_malformed_empty_or_inconsistent_nearest_points_fail(self):
        for pairs in ([], None, [[0, 0, 0]], [[[0, 0], [0, 0, 0]]],
                      [[[False, 0, 0], [5 * MM, 0, 0]]],
                      [[[float("nan"), 0, 0], [5 * MM, 0, 0]]],
                      [[[0, 0, 0], [MM, 0, 0]]]):
            response = valid_response()
            response["clearance"]["nearest_points_nm"] = pairs
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                _validate_clearance_response(response)

    def test_contradictory_status_or_interference_fail(self):
        response = valid_response()
        response["clearance"]["interference_volume_nm3"] = 1
        with self.assertRaises(ValueError):
            _validate_clearance_response(response)
        response = valid_response()
        response["clearance"]["status"] = "penetrating"
        with self.assertRaises(ValueError):
            _validate_clearance_response(response)

    def test_adapter_is_ascii_and_uses_real_distance_and_intersection(self):
        self.assertTrue(ADAPTER_SOURCE.isascii())
        self.assertNotIn("import cadcore", ADAPTER_SOURCE)
        self.assertIn("left.distToShape(right)", ADAPTER_SOURCE)
        self.assertIn("left.common(right)", ADAPTER_SOURCE)
        compile(ADAPTER_SOURCE, "<clearance-adapter>", "exec")


class RealClearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _RUN_ROOT
        if _RUN_ROOT is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            _RUN_ROOT = EVIDENCE_ROOT / ("unittest-" + stamp)
            _RUN_ROOT.mkdir(parents=True, exist_ok=False)
        cls.kernel = SubprocessKernel(work_dir=str(_RUN_ROOT / "kernel"),
                                     keep_work_dir=True, probe_timeout_s=15, op_timeout_s=60)
        report = cls.kernel.probe_report()
        if not report.available:
            raise AssertionError("real FreeCAD required: %s" % report.as_dict())
        template = cls.kernel._profile.adapter_script
        if "import FreeCAD as App" not in template or "import Part" not in template:
            raise AssertionError("fixture must use the actual FreeCAD adapter")
        cls.kernel._profile = replace(cls.kernel._profile, adapter_script=injected_script(template))
        cls.addClassCleanup(cls.kernel.close)
        previous_tempdir = tempfile.tempdir
        temp = _RUN_ROOT / "temporary"
        temp.mkdir(exist_ok=False)
        tempfile.tempdir = str(temp)
        cls.addClassCleanup(setattr, tempfile, "tempdir", previous_tempdir)
        _OBSERVATIONS.append({"kind": "probe", "reading": report.as_dict()})

    def reading(self, a, b, name):
        result = measure(self.kernel, a, b)
        self.assertTrue(result["nearest_points_nm"])
        for left, right in result["nearest_points_nm"]:
            self.assertLessEqual(abs(math.dist(left, right) - result["distance_nm"]), 2)
        (_RUN_ROOT / (name + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
        _OBSERVATIONS.append({"kind": name, "reading": result})
        return result

    def test_five_mm_is_brep_gap_not_origin_distance(self):
        result = self.reading(box_recipe(), box_recipe(15), "separated-5mm")
        self.assertEqual(result["distance_nm"], 5 * MM)
        self.assertNotEqual(result["distance_nm"], 15 * MM)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "separated")
        for a, b in result["nearest_points_nm"]:
            self.assertEqual(a[0], 10 * MM)
            self.assertEqual(b[0], 15 * MM)

    def test_face_contact_has_zero_gap_and_zero_shared_volume(self):
        result = self.reading(box_recipe(), box_recipe(10), "face-contact")
        self.assertEqual(result["distance_nm"], 0)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "touching")

    def test_edge_contact_is_not_penetration(self):
        result = self.reading(box_recipe(), box_recipe(10, y_mm=10), "edge-contact")
        self.assertEqual(result["distance_nm"], 0)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "touching")

    def test_penetration_has_zero_gap_and_analytic_shared_volume(self):
        result = self.reading(box_recipe(), box_recipe(8), "overlap-2mm")
        self.assertEqual(result["distance_nm"], 0)
        self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18, 2 * 10 * 10, places=7)
        self.assertEqual(result["status"], "penetrating")

    def test_identical_solids_have_full_interference_volume(self):
        result = self.reading(box_recipe(), box_recipe(), "identical")
        self.assertEqual(result["distance_nm"], 0)
        self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18, 1000, places=7)
        self.assertEqual(result["status"], "penetrating")

    def test_contained_solid_is_penetration_without_surface_contact(self):
        inner = box_recipe(2, y_mm=2, width_mm=2, height_mm=2, depth_mm=2)
        for entity in inner["recipe"][0]["sketch"]["entities"]:
            entity["geom"]["p1"].append(2 * MM)
            entity["geom"]["p2"].append(2 * MM)
        result = self.reading(box_recipe(), inner, "contained")
        self.assertEqual(result["distance_nm"], 0)
        self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18, 8, places=7)
        self.assertEqual(result["status"], "penetrating")

    def test_cylinders_measure_surface_gap_not_center_gap(self):
        result = self.reading(cylinder_recipe(0), cylinder_recipe(9), "curved-gap")
        self.assertEqual(result["distance_nm"], 5 * MM)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "separated")
        for a, b in result["nearest_points_nm"]:
            self.assertEqual(a[0], 2 * MM)
            self.assertEqual(b[0], 7 * MM)

    def test_axial_cylinder_contact_has_coincident_brep_witness(self):
        result = self.reading(axial_cylinder(), axial_cylinder(10), "axial-cylinder-contact")
        self.assertEqual(result["distance_nm"], 0)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "touching")
        for a, b in result["nearest_points_nm"]:
            self.assertEqual(a, b)
            self.assertEqual(a[2], 10 * MM)

    def test_axial_cylinder_overlap_uses_common_volume_not_extrema_residual(self):
        result = self.reading(axial_cylinder(), axial_cylinder(5), "axial-cylinder-overlap")
        self.assertEqual(result["distance_nm"], 0)
        self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18,
                               math.pi * 2 ** 2 * 5, places=7)
        self.assertEqual(result["status"], "penetrating")
        for a, b in result["nearest_points_nm"]:
            self.assertEqual(a, b)

    def test_cylinder_inside_real_hole_is_separated_despite_overlapping_boxes(self):
        result = self.reading(axial_cylinder(outer_mm=5, inner_mm=3),
                              axial_cylinder(outer_mm=2), "cylinder-in-hole")
        self.assertEqual(result["distance_nm"], MM)
        self.assertEqual(result["interference_volume_nm3"], 0)
        self.assertEqual(result["status"], "separated")

    def test_actual_translated_cylinder_matches_ui_contact_and_interference(self):
        for z, volume, status in ((10, 0, "touching"),
                                  (5, math.pi * 4 * 5, "penetrating"),
                                  (11, 0, "separated")):
            with self.subTest(z=z):
                result = self.reading(
                    axial_cylinder(), translated_cylinder(z), "translated-cylinder-%d" % z)
                self.assertEqual(result["distance_nm"], MM if z == 11 else 0)
                self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18,
                                       volume, places=7)
                self.assertEqual(result["status"], status)
                if z != 11:
                    for a, b in result["nearest_points_nm"]:
                        self.assertEqual(a, b)

    def test_off_origin_translated_curves_do_not_turn_common_into_sub_nm_gap(self):
        for center in ((10 * MM, 20 * MM), (1234567, -7654321), (0, 0)):
            source = cylinder_recipe(0)
            source["recipe"][0]["sketch"]["entities"][0]["geom"]["center"] = list(center)
            for offset in (5, 10):
                shifted = json.loads(json.dumps(source))
                shifted["recipe"].append({
                    "op": "_test_clearance_translation",
                    "translation_nm": [0, 0, offset * MM],
                })
                with self.subTest(center=center, offset=offset):
                    result = measure(self.kernel, source, shifted)
                    self.assertEqual(result["distance_nm"], 0)
                    self.assertAlmostEqual(result["interference_volume_nm3"] / 1e18,
                                           math.pi * 20 if offset == 5 else 0, places=7)
                    self.assertEqual(result["status"], "penetrating" if offset == 5 else "touching")
                    for a, b in result["nearest_points_nm"]:
                        self.assertEqual(a, b)
                    _OBSERVATIONS.append({"kind": "off-origin-curves", "center": center,
                                          "offset_mm": offset, "reading": result})

    def test_real_separated_diagonal_sub_nm_gap_is_not_clamped_to_contact(self):
        # About 0.4nm: above BRep tolerance, below integer-nm rounding.
        a, b = cylinder_recipe(0, radius_mm=0.001), cylinder_recipe(0, radius_mm=0.001)
        b["recipe"][0]["sketch"]["entities"][0]["geom"]["center"] = [2000, 40]
        with self.assertRaises(KernelExecutionError) as caught:
            measure(self.kernel, a, b)
        self.assertEqual(caught.exception.reason, "op_failed")
        self.assertEqual(caught.exception.details["rc"], 3)
        self.assertIn("below integer-nanometre resolution", str(caught.exception))
        _OBSERVATIONS.append({"kind": "real-sub-nm-separation-rejected",
                              "details": caught.exception.details})

    def test_zero_witness_is_a_real_common_or_section_vertex_not_bbox(self):
        profile = self.kernel._profile
        check = '''
            left = _build_recipe(job["a"]["recipe"])
            right = _build_recipe(job["b"]["recipe"])
            common = left.common(right)
            witness = common if common.Vertexes else left.section(right)
            actual = [[int(round(c * 1000000)) for c in (v.Point.x, v.Point.y, v.Point.z)]
                      for v in witness.Vertexes]
            for first, second in payload["clearance"]["nearest_points_nm"]:
                if first != second or first not in actual:
                    raise ValueError("zero-clearance witness not in real shared topology")
            payload["verified_shared_vertices_nm"] = actual
'''
        script = profile.adapter_script.replace(
            'payload["clearance"] = _measure_clearance(job)',
            'payload["clearance"] = _measure_clearance(job)\n' + check, 1)
        self.kernel._profile = replace(profile, adapter_script=script)
        try:
            for offset in (5, 10):
                result = self.kernel._run_job(make_job(
                    a=axial_cylinder(), b=translated_cylinder(offset)))
                self.assertTrue(result["verified_shared_vertices_nm"])
                _OBSERVATIONS.append({"kind": "shared-topology-witness-verified", "reading": result})
        finally:
            self.kernel._profile = profile

    def test_curved_extrema_and_common_topology_are_recorded(self):
        profile = self.kernel._profile
        diagnostic = '''
            left = _build_recipe(job["a"]["recipe"])
            right = _build_recipe(job["b"]["recipe"])
            distance, pairs, support = left.distToShape(right)
            common = left.common(right)
            section = left.section(right)
            payload["clearance_diagnostic"] = {
                "distance_mm": distance,
                "pairs_mm": [[[p.x, p.y, p.z] for p in pair] for pair in pairs],
                "support": support,
                "common_null": common.isNull(), "common_valid": common.isValid(),
                "common_volume_mm3": common.Volume,
                "common_topology": [len(common.Solids), len(common.Faces),
                                    len(common.Edges), len(common.Vertexes)],
                "common_vertices_mm": [[v.Point.x, v.Point.y, v.Point.z]
                                       for v in common.Vertexes],
                "section_topology": [len(section.Faces), len(section.Edges),
                                     len(section.Vertexes)],
            }
'''
        script = profile.adapter_script.replace(
            'payload["clearance"] = _measure_clearance(job)', diagnostic, 1)
        self.kernel._profile = replace(profile, adapter_script=script)
        try:
            for z in (5, 10, 11):
                payload = self.kernel._run_job(make_job(
                    a=axial_cylinder(), b=translated_cylinder(z)))
                value = payload["clearance_diagnostic"]
                (_RUN_ROOT / ("curved-topology-%d.json" % z)).write_text(
                    json.dumps(value, indent=2), encoding="utf-8")
                _OBSERVATIONS.append({"kind": "curved-topology-%d" % z, "reading": value})
                self.assertTrue(value["common_valid"])
        finally:
            self.kernel._profile = profile

    def test_diagonal_gap_is_euclidean_brep_distance(self):
        result = self.reading(box_recipe(), box_recipe(13, y_mm=14), "diagonal-3-4-5")
        self.assertEqual(result["distance_nm"], 5 * MM)
        self.assertEqual(result["interference_volume_nm3"], 0)

    def test_order_swap_preserves_distance_and_volume(self):
        result = self.reading(box_recipe(15), box_recipe(), "order-swap")
        self.assertEqual(result["distance_nm"], 5 * MM)
        self.assertEqual(result["interference_volume_nm3"], 0)
        for a, b in result["nearest_points_nm"]:
            self.assertEqual(a[0], 15 * MM)
            self.assertEqual(b[0], 10 * MM)

    def test_non_solid_or_empty_geometry_fails_with_root_cause(self):
        for operand, cause in (({"recipe": box_recipe()["recipe"][:1]}, "positive-volume solid"),
                               ({"recipe": [{"op": "sketch", "sketch": {"entities": []}}]},
                                "sketch has no entities")):
            with self.subTest(cause=cause), self.assertRaises(KernelExecutionError) as caught:
                measure(self.kernel, operand, box_recipe(15))
            self.assertEqual(caught.exception.reason, "op_failed")
            self.assertEqual(caught.exception.details["rc"], 3)
            self.assertIn(cause, str(caught.exception))
            self.assertIn("_measure_clearance", caught.exception.details["traceback_tail"])
            _OBSERVATIONS.append({"kind": "expected-kernel-failure", "error": str(caught.exception),
                                  "details": caught.exception.details})

    def test_unknown_recipe_operation_retains_kernel_root_cause(self):
        with self.assertRaises(KernelExecutionError) as caught:
            measure(self.kernel, {"recipe": [{"op": "does_not_exist"}]}, box_recipe())
        self.assertEqual(caught.exception.reason, "op_failed")
        self.assertIn("unsupported build op: does_not_exist", str(caught.exception))
        self.assertEqual(caught.exception.details["rc"], 3)

    def test_replay_rejects_tampered_nm_parameters(self):
        for depth, cause in ((True, "integer nm"), (0, "positive"), (-MM, "positive")):
            operand = box_recipe()
            operand["recipe"][1]["distance_nm"] = depth
            with self.subTest(depth=depth), self.assertRaises(KernelExecutionError) as caught:
                self.kernel._run_job({"op": "clearance", "a": operand, "b": box_recipe()})
            self.assertEqual(caught.exception.reason, "op_failed")
            self.assertEqual(caught.exception.details["rc"], 3)
            self.assertIn(cause, str(caught.exception))

    def test_bad_real_subprocess_response_is_not_defaulted_to_zero(self):
        mutations = [
            '{}',
            'None',
            '{"distance_nm": True, "nearest_points_nm": [], "interference_volume_nm3": 0}',
            '{"distance_nm": 0, "nearest_points_nm": [], "interference_volume_nm3": 0}',
        ]
        profile = self.kernel._profile
        for mutation in mutations:
            script = profile.adapter_script.replace(
                'payload["clearance"] = _measure_clearance(job)',
                'payload["clearance"] = _measure_clearance(job)\n'
                '            payload["clearance"] = ' + mutation, 1)
            self.kernel._profile = replace(profile, adapter_script=script)
            try:
                with self.assertRaises(KernelExecutionError) as caught:
                    measure(self.kernel, box_recipe(), box_recipe(15))
                self.assertEqual(caught.exception.reason, "invalid_clearance_response")
                self.assertIsInstance(caught.exception.__cause__, ValueError)
                _OBSERVATIONS.append({"kind": "expected-response-failure",
                                      "mutation": mutation, "error": str(caught.exception)})
            finally:
                self.kernel._profile = profile

    def test_real_binding_distance_contract_is_recorded(self):
        profile = self.kernel._profile
        script = profile.adapter_script.replace(
            'payload["clearance"] = _measure_clearance(job)',
            'payload["clearance"] = _measure_clearance(job)\n'
            '            payload["clearance_api_doc"] = Part.Shape.distToShape.__doc__', 1)
        self.kernel._profile = replace(profile, adapter_script=script)
        try:
            payload = self.kernel._run_job(make_job(a=box_recipe(), b=box_recipe(15)))
        finally:
            self.kernel._profile = profile
        doc = payload["clearance_api_doc"]
        self.assertIn("distToShape", doc)
        self.assertIn("distance", doc.lower())
        (_RUN_ROOT / "freecad-distance-api.txt").write_text(doc, encoding="utf-8")
        _OBSERVATIONS.append({"kind": "real-binding-api", "doc": doc})


def main():
    global _RUN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args()
    root = args.evidence_root.absolute()
    root.resolve().relative_to(Path("/hf3fs-jd/data/tmp").resolve())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _RUN_ROOT = root / stamp
    _RUN_ROOT.mkdir(parents=True, exist_ok=False)
    (_RUN_ROOT / "write-probe").touch(exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    module = sys.modules[make_job.__module__]
    files = [Path(inspect.getfile(module)), Path(__file__).resolve()]
    dependencies = sorted((PACKAGE / "cadcore/kernel").glob("*.py")) + files
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in dependencies}
    wall_start = time.monotonic()
    logs = _RUN_ROOT / "logs"
    logs.mkdir()
    log_path = logs / "tests.log"
    with log_path.open("x", encoding="utf-8") as log:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in dependencies}
        drift = [path for path in before if before[path] != after[path]]
        verdict = ("PASS" if result.wasSuccessful() and result.testsRun > 0
                   and not result.skipped and not drift else "FAIL")
        ended = datetime.now(timezone.utc).isoformat()
        line = "CLEARANCE_REAL verdict=%s time=%s tests=%d failed=%d errors=%d skipped=%d" % (
            verdict, ended, result.testsRun, len(result.failures), len(result.errors), len(result.skipped))
        log.write("\n" + line + "\n")
    payload = {
        "verdict": verdict, "started_at": started, "time": ended,
        "wall_s": time.monotonic() - wall_start, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "log": str(log_path), "observations": _OBSERVATIONS, "uid": os.getuid(),
        "source_unchanged": not drift, "source_drift": drift,
        "dependencies_before": before, "dependencies_after": after,
        "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "command": ["sudo", "-n", "-u", "infra", "python3", "-B", str(files[1]),
                    "--evidence-root", str(root)],
    }
    (_RUN_ROOT / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(line)
    print("evidence=" + str(_RUN_ROOT))
    if verdict != "PASS":
        print(log_path.read_text(encoding="utf-8"))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
