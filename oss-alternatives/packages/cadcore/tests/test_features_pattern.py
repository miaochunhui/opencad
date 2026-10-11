"""Real FreeCAD pattern/mirror acceptance; no mocks or optional skips.

Run this file directly with --evidence-dir to retain timestamped test logs,
recipes, adapter scripts, real mass/mesh results and a source-bound verdict.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from cadcore.errors import CadError
from cadcore.kernel import features_pattern
from cadcore.kernel.features_pattern import (
    ADAPTER_SOURCE, make_mirror_step, make_pattern_step,
)
from cadcore.kernel.subproc import SubprocessKernel, SubprocessShape

MM = 1_000_000
EVIDENCE_ROOT = Path(os.environ.get(
    "CADCORE_PATTERN_EVIDENCE_DIR",
    "/hf3fs-jd/data/tmp/opencad-features-20261010/seat-pattern",
))


def _inject_adapter(source):
    anchor = "\ndef _build_recipe(recipe):"
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    test_op_anchor = '        elif op == "mesh":'
    if (source.count(anchor) != 1 or source.count(dispatch) != 1
            or source.count(test_op_anchor) != 1):
        raise AssertionError("adapter injection anchors drifted")
    result = source.replace(
        anchor, "\n_EXTENDED_BUILDERS = {}\n" + ADAPTER_SOURCE + anchor, 1)
    result = result.replace(
        dispatch,
        '    if op in _EXTENDED_BUILDERS:\n'
        '        return _EXTENDED_BUILDERS[op](step, previous)\n' + dispatch, 1)
    # Test-only operation; the original mass function remains byte-for-byte intact.
    return result.replace(test_op_anchor, '''
        elif op == "pattern_test_solid_properties":
            shape = _pattern_solid(_build_recipe(job["shape"]["recipe"]), "test")
            payload["solid_properties"] = []
            for solid in shape.Solids:
                volume = float(solid.Volume)
                center = [float(value) for value in tuple(solid.CenterOfMass)[:3]]
                if (not math.isfinite(volume) or volume <= 0 or len(center) != 3
                        or not all(math.isfinite(value) for value in center)):
                    raise ValueError("test requires measured solid volume and center")
                payload["solid_properties"].append({
                    "volume_mm3": volume, "center_mm": center})
''' + test_op_anchor, 1)


class PatternStepTests(unittest.TestCase):
    def test_pattern_contract_and_defensive_copy(self):
        points = [[0, 0, 0], [3 * MM, -MM, 2 * MM]]
        step = make_pattern_step(translations_nm=points)
        self.assertEqual(step, {"op": "pattern", "translations_nm": points, "fuse": False})
        points[1][0] = 99
        self.assertEqual(step["translations_nm"][1][0], 3 * MM)
        self.assertTrue(make_pattern_step(translations_nm=[[MM, 0, 0]], fuse=True)["fuse"])

    def test_pattern_rejects_invalid_or_degenerate_parameters(self):
        bad = (
            None, True, [], (), [[0, 0, 0]], [[0, 0]], [[0, 0, 0, 0]],
            [(MM, 0, 0)], [MM], [[True, 0, 0]], [[MM, False, 0]],
            [[MM, 0, 0.0]], [[MM, 0, "0"]], [[MM, 0, float("nan")]],
            [[MM, 0, 0], [MM, 0, 0]], [[i * MM, 0, 0] for i in range(257)],
        )
        for points in bad:
            with self.subTest(points=points), self.assertRaises(CadError) as caught:
                make_pattern_step(translations_nm=points)
            self.assertEqual(caught.exception.reason, "invalid_pattern_parameters")
        for value in (0, 1, "false", None):
            with self.subTest(fuse=value), self.assertRaises(CadError):
                make_pattern_step(translations_nm=[[MM, 0, 0]], fuse=value)

    def test_pattern_accepts_count_limit(self):
        self.assertEqual(len(make_pattern_step(
            translations_nm=[[i * MM, 0, 0] for i in range(256)]
        )["translations_nm"]), 256)

    def test_mirror_contract_and_parameter_rejections(self):
        self.assertEqual(make_mirror_step(normal_axis="x"), {
            "op": "mirror", "normal_axis": "x", "offset_nm": 0, "keep_original": False})
        for axis in ("X", "xy", "", None, True, 0, []):
            with self.subTest(axis=axis), self.assertRaises(CadError) as caught:
                make_mirror_step(normal_axis=axis)
            self.assertEqual(caught.exception.reason, "invalid_mirror_parameters")
        for offset in (True, False, 1.0, "0", None, float("inf")):
            with self.subTest(offset=offset), self.assertRaises(CadError):
                make_mirror_step(normal_axis="z", offset_nm=offset)
        for keep in (0, 1, "true", None):
            with self.subTest(keep=keep), self.assertRaises(CadError):
                make_mirror_step(normal_axis="y", keep_original=keep)


class RealPatternMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        cls.evidence = Path(tempfile.mkdtemp(prefix="real-", dir=EVIDENCE_ROOT))
        cls.kernel = SubprocessKernel(
            profile="freecad", work_dir=cls.evidence / "jobs", keep_work_dir=True)
        cls.kernel._profile = dataclasses.replace(
            cls.kernel._profile,
            adapter_script=_inject_adapter(cls.kernel._profile.adapter_script))
        report = cls.kernel.probe_report()
        if not report.available or not report.modules.get("Part"):
            raise AssertionError("real FreeCAD/Part unavailable: " + repr(report))
        cls.records = []
        (cls.evidence / "probe.json").write_text(
            json.dumps(dataclasses.asdict(report), default=list, indent=2), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        (cls.evidence / "measurements.json").write_text(
            json.dumps(cls.records, indent=2), encoding="utf-8")
        cls.kernel.close()

    def box(self, *, x=1, y=2, z=3, width=4, height=3, depth=2):
        points = [
            [x * MM, y * MM], [(x + width) * MM, y * MM],
            [(x + width) * MM, (y + height) * MM], [x * MM, (y + height) * MM],
        ]
        sketch = {"entities": [
            {"kind": "line", "geom": {"p1": points[i], "p2": points[(i + 1) % 4]}}
            for i in range(4)
        ]}
        wire = self.kernel.sketch_to_wire(sketch)
        solid = self.kernel.extrude(wire, distance_nm=depth * MM)
        return self.extend(solid, make_pattern_step(translations_nm=[[0, 0, z * MM]]))

    def extend(self, shape, step):
        return SubprocessShape(recipe=shape.recipe + (step,), kernel=shape.kernel)

    def measure(self, shape, *, volume, bbox, solids, center=None):
        props = self.kernel.mass_properties(shape)
        mesh = self.kernel.tessellate(shape)
        self.assertEqual(props.source, "external")
        self.assertNotIn("volume_nm3", props.unmeasured)
        self.assertNotIn("area_nm2", props.unmeasured)
        self.assertGreater(props.area_nm2, 0)
        self.assertAlmostEqual(props.volume_nm3 / MM ** 3, volume, places=6)
        self.assertEqual(props.bbox_nm, tuple(int(v * MM) for v in bbox))
        self.assertEqual(mesh["solid_count"], solids)
        self.assertEqual(mesh["properties"]["volume_nm3"], props.volume_nm3)
        self.assertEqual(mesh["properties"]["bbox_nm"], list(props.bbox_nm))
        solid_properties = None
        if center is not None:
            measured = self.kernel._run_job({
                "schema": 1, "op": "pattern_test_solid_properties",
                "shape": {"recipe": list(shape.recipe)},
            })
            solid_properties = measured["solid_properties"]
            self.assertEqual(len(solid_properties), solids)
            total_volume = sum(item["volume_mm3"] for item in solid_properties)
            self.assertAlmostEqual(total_volume, volume, places=6)
            weighted_center = tuple(
                sum(item["volume_mm3"] * item["center_mm"][i] for item in solid_properties)
                / total_volume for i in range(3))
            for measured_center, expected_center in zip(weighted_center, center):
                self.assertAlmostEqual(measured_center, expected_center, places=6)
            if "center_nm" in props.unmeasured:
                # FreeCAD 0.19 Compound has no CenterOfMass; never fabricate it.
                self.assertIsNone(props.center_nm)
                self.assertNotIn("center_nm", mesh["properties"])
                self.assertIn("center_nm", mesh["properties"]["unmeasured"])
            else:
                self.assertEqual(props.center_nm, tuple(int(v * MM) for v in center))
        self.assertGreaterEqual(len(mesh["triangles"]), 12 * solids)
        vertices = mesh["vertices_mm"]
        mesh_bbox = tuple(min(p[i] for p in vertices) for i in range(3)) + tuple(
            max(p[i] for p in vertices) for i in range(3))
        for got, expected in zip(mesh_bbox, bbox):
            self.assertAlmostEqual(got, expected, places=6)
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            self.assertTrue(all(type(i) is int and 0 <= i < len(vertices) for i in triangle))
            a, b, c = (vertices[i] for i in triangle)
            u, v = ([b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)])
            self.assertGreater(sum(value ** 2 for value in (
                u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0])), 0)
        record = {"test": self.id(), "recipe": list(shape.recipe),
                  "properties": dataclasses.asdict(props), "mesh": mesh}
        if solid_properties is not None:
            record["measured_solid_properties"] = solid_properties
        self.records.append(record)
        return props, mesh

    def test_fixture_is_real_box_with_nonzero_world_origin(self):
        self.measure(self.box(), volume=24, bbox=(1, 2, 3, 5, 5, 5),
                     solids=1, center=(3, 3.5, 4))

    def test_nonintersecting_three_instances_have_three_times_volume(self):
        base = self.box()
        for fuse in (False, True):
            with self.subTest(fuse=fuse):
                shape = self.extend(base, make_pattern_step(
                    translations_nm=[[0, 0, 0], [10 * MM, 0, 0], [20 * MM, 0, 0]],
                    fuse=fuse))
                self.measure(shape, volume=72, bbox=(1, 2, 3, 25, 5, 5),
                             solids=3, center=(13, 3.5, 4))
        self.measure(base, volume=24, bbox=(1, 2, 3, 5, 5, 5), solids=1)

    def test_original_is_not_implicitly_included(self):
        shape = self.extend(self.box(), make_pattern_step(
            translations_nm=[[10 * MM, -MM, -2 * MM]]))
        self.measure(shape, volume=24, bbox=(11, 1, 1, 15, 4, 3), solids=1)

    def test_fused_intersection_has_union_not_summed_volume(self):
        shape = self.extend(self.box(), make_pattern_step(
            translations_nm=[[0, 0, 0], [2 * MM, 0, 0]], fuse=True))
        self.measure(shape, volume=36, bbox=(1, 2, 3, 7, 5, 5),
                     solids=1, center=(4, 3.5, 4))

    def test_unfused_intersection_retains_two_solids(self):
        shape = self.extend(self.box(), make_pattern_step(
            translations_nm=[[0, 0, 0], [2 * MM, 0, 0]]))
        self.measure(shape, volume=48, bbox=(1, 2, 3, 7, 5, 5),
                     solids=2, center=(4, 3.5, 4))

    def test_mirror_all_axes_and_signed_offsets_preserve_volume(self):
        base = self.box()
        source_bbox = (1, 2, 3, 5, 5, 5)
        for axis in ("x", "y", "z"):
            for offset in (-2, 0, 7):
                with self.subTest(axis=axis, offset=offset):
                    i = ("x", "y", "z").index(axis)
                    bbox = list(source_bbox)
                    bbox[i], bbox[i + 3] = 2 * offset - bbox[i + 3], 2 * offset - bbox[i]
                    shape = self.extend(base, make_mirror_step(
                        normal_axis=axis, offset_nm=offset * MM))
                    self.measure(shape, volume=24, bbox=bbox, solids=1)
        self.measure(base, volume=24, bbox=source_bbox, solids=1)

    def test_mirror_keep_original_has_two_disjoint_solids(self):
        shape = self.extend(self.box(), make_mirror_step(
            normal_axis="x", keep_original=True))
        self.measure(shape, volume=48, bbox=(-5, 2, 3, 5, 5, 5),
                     solids=2, center=(0, 3.5, 4))

    def test_compound_mirror_center_measured_from_real_solids(self):
        pattern = self.extend(self.box(), make_pattern_step(
            translations_nm=[[0, 0, 0], [10 * MM, 0, 0], [20 * MM, 0, 0]]))
        mirrored = self.extend(pattern, make_mirror_step(
            normal_axis="x", offset_nm=-2 * MM))
        self.measure(mirrored, volume=72, bbox=(-29, 2, 3, -5, 5, 5),
                     solids=3, center=(-17, 3.5, 4))

    def test_mirror_keeps_partial_intersection_without_implicit_union(self):
        shape = self.extend(self.box(), make_mirror_step(
            normal_axis="x", offset_nm=2 * MM, keep_original=True))
        self.measure(shape, volume=48, bbox=(-1, 2, 3, 5, 5, 5), solids=2)

    def test_double_mirror_restores_real_geometry(self):
        base = self.box()
        step = make_mirror_step(normal_axis="y", offset_nm=-4 * MM)
        shape = self.extend(self.extend(base, step), step)
        self.measure(shape, volume=24, bbox=(1, 2, 3, 5, 5, 5),
                     solids=1, center=(3, 3.5, 4))

    def test_mirror_preserves_analytic_hole_and_can_be_boolean_cut(self):
        sketch = {"entities": [{"kind": "circle", "geom": {
            "center": [3 * MM, 3 * MM], "radius_nm": MM // 2}}]}
        tool = self.kernel.extrude(self.kernel.sketch_to_wire(sketch), distance_nm=2 * MM)
        tool = self.extend(tool, make_pattern_step(translations_nm=[[0, 0, 3 * MM]]))
        base = self.kernel.boolean("cut", self.box(), tool)
        mirrored = self.extend(base, make_mirror_step(normal_axis="x"))
        self.measure(mirrored, volume=24 - math.pi / 2,
                     bbox=(-5, 2, 3, -1, 5, 5), solids=1)
        self.measure(self.kernel.boolean("intersect", mirrored, self.box(x=-5)),
                     volume=24 - math.pi / 2, bbox=(-5, 2, 3, -1, 5, 5), solids=1)

    def test_real_256_instance_boundary(self):
        shape = self.extend(self.box(), make_pattern_step(
            translations_nm=[[i * 10 * MM, 0, 0] for i in range(256)]))
        self.measure(shape, volume=256 * 24, bbox=(1, 2, 3, 2555, 5, 5), solids=256)

    def assert_external_rejection(self, shape, message):
        for operation in (self.kernel.mass_properties, self.kernel.tessellate):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(CadError) as caught:
                    operation(shape)
                self.assertEqual(caught.exception.reason, "op_failed")
                self.assertIn(message, caught.exception.message)
                self.assertNotEqual(caught.exception.details["rc"], 0)
                self.records.append({"test": self.id(), "recipe": list(shape.recipe),
                                     "operation": operation.__name__, "error": caught.exception.as_dict()})

    def test_adapter_rejects_bypassed_invalid_pattern_parameters(self):
        valid = make_pattern_step(translations_nm=[[MM, 0, 0]])
        bad = (
            ("translations_nm", [], "1..256"),
            ("translations_nm", [[0, 0, 0]], "degenerate"),
            ("translations_nm", [[MM, 0, 0], [MM, 0, 0]], "duplicate"),
            ("translations_nm", [[True, 0, 0]], "integer nanometres"),
            ("translations_nm", [[MM, 0.5, 0]], "integer nanometres"),
            ("translations_nm", [[MM, 0]], "three coordinates"),
            ("translations_nm", [[i * MM, 0, 0] for i in range(257)], "1..256"),
            ("fuse", 1, "boolean"),
        )
        base = self.box()
        for key, value, message in bad:
            step = copy.deepcopy(valid)
            step[key] = value
            with self.subTest(key=key, value=value):
                self.assert_external_rejection(self.extend(base, step), message)

    def test_adapter_rejects_bypassed_invalid_mirror_parameters(self):
        base = self.box()
        for key, value, message in (
            ("normal_axis", "xy", "x/y/z"), ("offset_nm", True, "integer nanometres"),
            ("offset_nm", 0.5, "integer nanometres"), ("keep_original", 1, "boolean"),
        ):
            step = make_mirror_step(normal_axis="x")
            step[key] = value
            with self.subTest(key=key, value=value):
                self.assert_external_rejection(self.extend(base, step), message)

    def test_missing_or_wire_base_is_rejected(self):
        wire = self.kernel.sketch_to_wire({"entities": [{"kind": "circle", "geom": {
            "center": [0, 0], "radius_nm": MM}}]})
        for step in (make_pattern_step(translations_nm=[[MM, 0, 0]]),
                     make_mirror_step(normal_axis="x")):
            for base in (wire, SubprocessShape(kernel="freecad")):
                with self.subTest(step=step, base=base):
                    self.assert_external_rejection(self.extend(base, step), "positive-volume")

    def test_mirror_rejects_duplicate_coincident_kept_original(self):
        shape = self.extend(self.box(), make_mirror_step(
            normal_axis="x", offset_nm=3 * MM, keep_original=True))
        self.assert_external_rejection(shape, "coincident solid")


def _run_acceptance():
    global EVIDENCE_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args()
    EVIDENCE_ROOT = args.evidence_dir
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = (Path(features_pattern.__file__), Path(__file__))
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    log_path = EVIDENCE_ROOT / (stamp + "-tests.log")
    with log_path.open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    verdict = "PASS" if result.wasSuccessful() and not result.skipped and before == after else "FAIL"
    payload = {
        "verdict": verdict, "collected_at": datetime.now(timezone.utc).isoformat(),
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skips": len(result.skipped), "source_before": before, "source_after": after,
        "source_unchanged": before == after, "log": str(log_path),
        "real_evidence": str(getattr(RealPatternMirrorTests, "evidence", "")),
        "failures_detail": [{"test": test.id(), "traceback": detail}
                           for test, detail in result.failures + result.errors],
    }
    result_path = EVIDENCE_ROOT / (stamp + "-result.json")
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("PATTERN_MIRROR verdict=%s tests=%s failures=%s errors=%s skips=%s collected_at=%s evidence=%s"
          % (verdict, result.testsRun, len(result.failures), len(result.errors),
             len(result.skipped), payload["collected_at"], result_path))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(_run_acceptance())
