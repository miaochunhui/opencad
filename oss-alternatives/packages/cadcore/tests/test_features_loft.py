"""Real FreeCAD loft acceptance, without mocks or dependency skips.

Run as infra from the cadcore package:
``python3 -B tests/test_features_loft.py --evidence-dir <external directory>``.
Each run keeps timed unittest logs, analytic measurements and adapter jobs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadcore.kernel.features_loft import ADAPTER_SOURCE, _validate_loft_parameters, make_step
from cadcore.kernel.subproc import KernelExecutionError, SubprocessKernel, SubprocessShape

MM = 1_000_000
DEFAULT_EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-loft")


def rectangle(width=10 * MM, height=8 * MM):
    points = [[0, 0, 0], [width, 0, 0], [width, height, 0],
              [0, height, 0], [0, 0, 0]]
    return {"recipe": [{"op": "sketch", "sketch": {
        "plane": "XY", "entities": [{"kind": "polyline", "geom": {"points": points}}]}}]}


def circle(radius=5 * MM):
    return {"recipe": [{"op": "sketch", "sketch": {
        "plane": "XY", "entities": [{"kind": "circle", "geom": {
            "radius_nm": radius, "center": [0, 0, 0]}}]}}]}


def inject_adapter(source):
    anchor = "def _build_recipe(recipe):"
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    if source.count(anchor) != 1 or source.count(dispatch) != 1:
        raise AssertionError("adapter injection anchors changed; integration must be updated")
    source = source.replace(anchor, "_EXTENDED_BUILDERS = {}\n" + ADAPTER_SOURCE + "\n" + anchor, 1)
    return source.replace(dispatch, (
        "    if op in _EXTENDED_BUILDERS:\n"
        "        return _EXTENDED_BUILDERS[op](step, previous)\n" + dispatch
    ), 1)


class LoftContractTests(unittest.TestCase):
    def parameters(self):
        return {"profiles": [rectangle(), rectangle()],
                "offsets_nm": [[0, 0, 0], [0, 0, 20 * MM]]}

    def test_contract_defaults_detaches_and_is_json(self):
        params = self.parameters()
        step = make_step(**params)
        self.assertEqual(step["op"], "loft")
        self.assertIs(step["ruled"], False)
        self.assertEqual(json.loads(json.dumps(step, allow_nan=False)), step)
        params["profiles"][0]["recipe"][0]["sketch"]["plane"] = "XZ"
        params["offsets_nm"][1][2] = 0
        self.assertEqual(step["profiles"][0]["recipe"][0]["sketch"]["plane"], "XY")
        self.assertEqual(step["offsets_nm"][1][2], 20 * MM)

    def test_profiles_reject_empty_short_and_malformed_recipes(self):
        for profiles in (None, [], [rectangle()], (rectangle(), rectangle()),
                         [None, rectangle()], [{}, rectangle()],
                         [{"recipe": []}, rectangle()], [{"recipe": "x"}, rectangle()],
                         [{"recipe": [None]}, rectangle()],
                         [{"recipe": [{"op": ""}]}, rectangle()]):
            with self.subTest(profiles=profiles), self.assertRaises(ValueError):
                make_step(profiles=profiles, offsets_nm=[[0, 0, 0], [0, 0, MM]])

    def test_offsets_reject_count_dimension_type_nonfinite_and_overflow(self):
        for offsets in (None, [], [[0, 0, 0]], ((0, 0, 0), (0, 0, MM)),
                        [[0, 0, 0], [0, MM]], [[0, 0, 0], [0, 0, 0, MM]],
                        [[0, 0, 0], "xyz"], [[0, 0, 0], [0, 0, True]],
                        [[0, 0, 0], [0, 0, "10"]], [[0, 0, 0], [0, 0, None]],
                        [[0, 0, 0], [0, 0, float("nan")]],
                        [[0, 0, 0], [0, 0, float("inf")]],
                        [[0, 0, 0], [0, 0, -float("inf")]],
                        [[0, 0, 0], [0, 0, 10 ** 400]]):
            with self.subTest(offsets=offsets), self.assertRaises(ValueError):
                make_step(profiles=[rectangle(), rectangle()], offsets_nm=offsets)

    def test_offsets_strict_integer_nanometres_on_every_axis(self):
        invalid = (0.0, 1.0, 1.5, -1.0, -1.5, True, False,
                   2 ** 53 + 1, -(2 ** 53 + 1), float(2 ** 53))
        for axis in range(3):
            for value in invalid:
                params = self.parameters()
                params["offsets_nm"][1][axis] = value
                with self.subTest(axis=axis, value=value), self.assertRaises(ValueError):
                    make_step(**params)

    def test_offsets_integer_range_is_inclusive(self):
        namespace = {"json": json, "_EXTENDED_BUILDERS": {}}
        exec(compile(ADAPTER_SOURCE, "<loft-adapter>", "exec"), namespace)
        embedded_validator = namespace["_validate_loft_parameters"]
        for axis in range(3):
            for value in (0, 1, -1, 2 ** 53, -(2 ** 53)):
                params = self.parameters()
                params["offsets_nm"][1][axis] = value
                with self.subTest(axis=axis, value=value):
                    step = make_step(**params)
                    self.assertIs(type(step["offsets_nm"][1][axis]), int)
                    self.assertEqual(step["offsets_nm"][1][axis], value)
                    self.assertEqual(embedded_validator(
                        params["profiles"], params["offsets_nm"], False), step)

    def test_ruled_rejects_coercion(self):
        for ruled in (0, 1, None, "false", [], {}):
            with self.subTest(ruled=ruled), self.assertRaisesRegex(ValueError, "ruled"):
                make_step(**self.parameters(), ruled=ruled)

    def test_recipe_rejects_nonjson_and_nonfinite(self):
        for value in (object(), float("nan"), float("inf")):
            params = self.parameters()
            params["profiles"][0]["recipe"][0]["invalid"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "JSON"):
                make_step(**params)

    def test_adapter_is_ascii_isolated_and_registered(self):
        self.assertTrue(ADAPTER_SOURCE.isascii())
        self.assertNotIn("cadcore", ADAPTER_SOURCE)
        self.assertNotIn("import FreeCAD", ADAPTER_SOURCE)
        self.assertIn("_EXTENDED_BUILDERS['loft'] = _build_loft", ADAPTER_SOURCE)
        compile(ADAPTER_SOURCE, "<loft-adapter>", "exec")

    def test_host_and_adapter_use_identical_single_validator_source(self):
        source = inspect.getsource(_validate_loft_parameters)
        self.assertTrue(ADAPTER_SOURCE.startswith(source))
        self.assertEqual(ADAPTER_SOURCE.count("def _validate_loft_parameters("), 1)
        self.assertEqual(ADAPTER_SOURCE.count("_validate_loft_parameters("), 2)
        self.assertIn("return _validate_loft_parameters(profiles, offsets_nm, ruled)",
                      inspect.getsource(make_step))
        self.assertNotIn("int | float", inspect.getsource(make_step))


class RealLoftTests(unittest.TestCase):
    evidence_root = DEFAULT_EVIDENCE
    measurements = []

    @classmethod
    def setUpClass(cls):
        cls.measurements = []
        cls.evidence_root.mkdir(parents=True, exist_ok=True)
        cls.executable = shutil.which("freecadcmd")
        if cls.executable is None:
            raise AssertionError("real FreeCAD required: freecadcmd is not on PATH")
        cls.kernel = SubprocessKernel(
            executable=cls.executable, probe_timeout_s=15,
            work_dir=cls.evidence_root / "kernel", keep_work_dir=True,
            extra_env={"QT_QPA_PLATFORM": "offscreen"},
        )
        cls.kernel._profile = replace(
            cls.kernel._profile, adapter_script=inject_adapter(cls.kernel._profile.adapter_script))
        report = cls.kernel.probe_report(force=True)
        (cls.evidence_root / "probe.json").write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        if not report.available:
            raise AssertionError("real FreeCAD required: %s" % report)
        if not {"mass", "step"}.issubset(cls.kernel.capabilities()):
            raise AssertionError("real FreeCAD Part capabilities missing")
        if "mock" in report.executable.lower() or report.executable != cls.executable:
            raise AssertionError("unexpected backend executable")

    @classmethod
    def tearDownClass(cls):
        (cls.evidence_root / "measurements.json").write_text(
            json.dumps(cls.measurements, indent=2, sort_keys=True), encoding="utf-8")
        (cls.evidence_root / "kernel-stats.json").write_text(
            json.dumps(cls.kernel.stats(), indent=2, sort_keys=True), encoding="utf-8")
        cls.kernel.close()

    def shape(self, profiles, offsets, ruled=False):
        return SubprocessShape(recipe=(make_step(
            profiles=profiles, offsets_nm=offsets, ruled=ruled),))

    def measure(self, shape, expected, bbox=None):
        props = self.kernel.mass_properties(shape)
        self.assertEqual(props.source, "external")
        self.assertFalse(props.unmeasured)
        actual = props.volume_nm3 / 1e18
        self.assertAlmostEqual(actual, expected, delta=max(expected * 1e-8, 1e-6))
        if bbox is not None:
            for value, bound in zip(props.bbox_nm, bbox):
                self.assertAlmostEqual(value, bound, delta=2)
        self.measurements.append({"test": self.id(), "expected_volume_mm3": expected,
                                  "actual_volume_mm3": actual, "bbox_nm": props.bbox_nm,
                                  "center_nm": props.center_nm, "ruled": shape.recipe[0]["ruled"]})
        return props

    def assert_real_error(self, step, message):
        before = self.kernel.stats()["runs"]
        with self.assertRaisesRegex(KernelExecutionError, message) as caught:
            self.kernel.mass_properties(SubprocessShape(recipe=(step,)))
        self.assertEqual(self.kernel.stats()["runs"], before + 1)
        self.assertEqual(caught.exception.reason, "op_failed")
        self.assertIn("_build_loft", caught.exception.details["traceback_tail"])
        self.measurements.append({"test": self.id(), "expected_error": message,
                                  "actual_error": str(caught.exception),
                                  "details": caught.exception.details})

    def test_identical_rectangles_two_ends_analytic_volume(self):
        for ruled in (False, True):
            with self.subTest(ruled=ruled):
                shape = self.shape([rectangle(), rectangle()], [[0, 0, 0], [0, 0, 20 * MM]], ruled)
                props = self.measure(shape, 1600, (0, 0, 0, 10 * MM, 8 * MM, 20 * MM))
                self.assertEqual(props.center_nm, (5 * MM, 4 * MM, 10 * MM))

    def test_equal_circles_two_ends_analytic_volume(self):
        for ruled in (False, True):
            with self.subTest(ruled=ruled):
                shape = self.shape([circle(), circle()], [[0, 0, 0], [0, 0, 12 * MM]], ruled)
                self.measure(shape, math.pi * 25 * 12,
                             (-5 * MM, -5 * MM, 0, 5 * MM, 5 * MM, 12 * MM))

    def test_frustum_analytic_volume_bounds_and_center(self):
        for ruled in (False, True):
            with self.subTest(ruled=ruled):
                shape = self.shape([circle(6 * MM), circle(3 * MM)],
                                   [[0, 0, 0], [0, 0, 18 * MM]], ruled)
                props = self.measure(shape, math.pi * 18 / 3 * (36 + 18 + 9),
                                     (-6 * MM, -6 * MM, 0, 6 * MM, 6 * MM, 18 * MM))
                self.assertAlmostEqual(props.center_nm[2], 18 * MM * 11 / 28, delta=2)

    def test_world_offsets_and_negative_direction(self):
        for offsets, bbox in (
            ([[2 * MM, -3 * MM, 5 * MM], [7 * MM, -MM, 25 * MM]],
             (2 * MM, -3 * MM, 5 * MM, 17 * MM, 7 * MM, 25 * MM)),
            ([[0, 0, 0], [0, 0, -20 * MM]],
             (0, 0, -20 * MM, 10 * MM, 8 * MM, 0)),
        ):
            with self.subTest(offsets=offsets):
                self.measure(self.shape([rectangle(), rectangle()], offsets, True), 1600, bbox)

    def test_three_closed_sections(self):
        shape = self.shape([rectangle(), rectangle(), rectangle()],
                           [[0, 0, 0], [0, 0, 9 * MM], [0, 0, 20 * MM]], True)
        self.measure(shape, 1600, (0, 0, 0, 10 * MM, 8 * MM, 20 * MM))

    def test_real_mesh_closed_manifold_and_volume(self):
        shape = self.shape([circle(6 * MM), circle(3 * MM)],
                           [[0, 0, 0], [0, 0, 18 * MM]], True)
        expected = math.pi * 378
        mesh = self.kernel.tessellate(shape, deflection_mm=0.05)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, expected, delta=1e-5)
        vertices = mesh["vertices_mm"]
        triangles = mesh["triangles"]
        self.assertGreater(len(vertices), 8)
        self.assertGreater(len(triangles), 12)
        edges = {}
        volume = 0.0
        for triangle in triangles:
            self.assertEqual(len(triangle), 3)
            self.assertEqual(len(set(triangle)), 3)
            for index in triangle:
                self.assertIsInstance(index, int)
                self.assertTrue(0 <= index < len(vertices))
            a, b, c = [vertices[index] for index in triangle]
            self.assertTrue(all(math.isfinite(v) for point in (a, b, c) for v in point))
            volume += (a[0] * (b[1] * c[2] - b[2] * c[1])
                       + a[1] * (b[2] * c[0] - b[0] * c[2])
                       + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
            for i, j in ((0, 1), (1, 2), (2, 0)):
                # Weld face-boundary duplicates using actual XYZ, not index identity.
                endpoints = tuple(sorted(tuple(round(v, 8) for v in vertices[triangle[k]])
                                         for k in (i, j)))
                edges[endpoints] = edges.get(endpoints, 0) + 1
        self.assertTrue(all(count == 2 for count in edges.values()))
        self.assertAlmostEqual(abs(volume), expected, delta=expected * 0.02)
        (self.evidence_root / "frustum-mesh.json").write_text(
            json.dumps(mesh, sort_keys=True), encoding="utf-8")
        self.measurements.append({"test": self.id(), "vertices": len(vertices),
                                  "triangles": len(triangles), "mesh_volume_mm3": abs(volume),
                                  "expected_volume_mm3": expected, "all_edges_incident_twice": True})

    def test_open_profile_rejected_by_real_kernel(self):
        profile = rectangle()
        profile["recipe"][0]["sketch"]["entities"][0]["geom"]["points"].pop()
        step = make_step(profiles=[profile, rectangle()], offsets_nm=[[0, 0, 0], [0, 0, MM]])
        self.assert_real_error(step, "must be closed")

    def test_holes_and_disconnected_loops_explicitly_rejected(self):
        for radii in ((6 * MM, 2 * MM), (3 * MM, 3 * MM)):
            profile = circle(radii[0])
            inner = circle(radii[1])["recipe"][0]["sketch"]["entities"][0]
            if radii[0] == radii[1]:
                inner["geom"]["center"] = [20 * MM, 0, 0]
            profile["recipe"][0]["sketch"]["entities"].append(inner)
            step = make_step(profiles=[profile, rectangle()], offsets_nm=[[0, 0, 0], [0, 0, MM]])
            with self.subTest(radii=radii):
                self.assert_real_error(step, "holes and multiple loops are unsupported")

    def test_solid_profile_is_not_misrepresented_as_a_wire(self):
        profile = rectangle()
        profile["recipe"].append({"op": "extrude", "distance_nm": MM})
        step = make_step(profiles=[profile, rectangle()], offsets_nm=[[0, 0, 0], [0, 0, 5 * MM]])
        self.assert_real_error(step, "single loop|must be a wire")

    def test_coincident_sections_do_not_return_empty_success(self):
        step = make_step(profiles=[rectangle(), rectangle()], offsets_nm=[[0, 0, 0], [0, 0, 0]])
        self.assert_real_error(step, "positive-volume solid|OCCError")

    def test_adapter_revalidates_untrusted_steps(self):
        base = make_step(profiles=[rectangle(), rectangle()], offsets_nm=[[0, 0, 0], [0, 0, MM]])
        mutations = [
            ("profiles", [], "at least two"), ("profiles", [rectangle()], "at least two"),
            ("profiles", [{"recipe": []}, rectangle()], "nonempty recipe"),
            ("offsets_nm", [[0, 0, 0]], "profile count"),
            ("offsets_nm", [[0, 0, 0], [0, MM]], "integer nanometres"),
            ("offsets_nm", [[0, 0, 0], [0, 0, True]], "integer nanometres"),
            ("offsets_nm", [[0, 0, 0], [0, 0, "x"]], "integer nanometres"),
            ("offsets_nm", [[0, 0, 0], [0, 0, float("nan")]], "integer nanometres"),
            ("offsets_nm", [[0, 0, 0], [0, 0, float("inf")]], "integer nanometres"),
            ("ruled", "false", "ruled must be a bool"),
        ]
        for field, value, message in mutations:
            with self.subTest(field=field, value=value):
                step = copy.deepcopy(base)
                step[field] = value
                self.assert_real_error(step, message)

    def test_adapter_rejects_float_and_out_of_range_nanometres(self):
        base = make_step(profiles=[rectangle(), rectangle()], offsets_nm=[[0, 0, 0], [0, 0, MM]])
        invalid = (0.0, 1.0, 1.5, -1.0, -1.5, True, False,
                   2 ** 53 + 1, -(2 ** 53 + 1), float(2 ** 53))
        for axis in range(3):
            for value in invalid:
                with self.subTest(axis=axis, value=value):
                    step = copy.deepcopy(base)
                    step["offsets_nm"][1][axis] = value
                    self.assert_real_error(step, "integer nanometres")


def run_acceptance():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--baseline-sources", type=Path)
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    run_dir = args.evidence_dir / started.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "write-probe").touch(exist_ok=False)
    RealLoftTests.evidence_root = run_dir
    wall = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    with (run_dir / "tests.log").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    status = subprocess.run(["git", "status", "--short", "--branch"], check=True,
                            text=True, capture_output=True)
    (run_dir / "git-status.txt").write_text(status.stdout, encoding="utf-8")
    sources = [Path(__file__).resolve(),
               Path(__file__).resolve().parents[1] / "cadcore/kernel/features_loft.py"]
    baseline_mismatches = None
    if args.baseline_sources is not None:
        snapshot = json.loads(args.baseline_sources.read_text(encoding="utf-8"))
        repo = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                              text=True, capture_output=True).stdout.strip()
        baseline_mismatches = []
        for relative, expected in snapshot.items():
            path = Path(repo) / relative
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != expected:
                baseline_mismatches.append({"path": relative, "expected": expected, "actual": actual})
    summary = {
        "verdict": "PASS" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "FAIL",
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": time.monotonic() - wall, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "evidence_dir": str(run_dir), "uid": os.getuid(),
        "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
        "baseline_sources": str(args.baseline_sources) if args.baseline_sources is not None else None,
        "baseline_mismatches": baseline_mismatches,
        "failure_details": [{"test": str(test), "traceback": detail}
                            for test, detail in result.failures + result.errors],
    }
    if baseline_mismatches:
        summary["verdict"] = "FAIL"
    (run_dir / "result.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("LOFT_ACCEPTANCE verdict=%s tests=%s failures=%s errors=%s skipped=%s elapsed_s=%.3f at=%s evidence=%s"
          % (summary["verdict"], result.testsRun, len(result.failures), len(result.errors),
             len(result.skipped), summary["elapsed_s"], summary["finished_at"], run_dir))
    if summary["verdict"] != "PASS":
        print((run_dir / "tests.log").read_text(encoding="utf-8")[-12000:])
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_acceptance())
