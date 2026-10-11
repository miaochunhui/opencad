"""Real FreeCAD sweep acceptance; run directly to retain timed JSON and logs.

sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_features_sweep.py

Each run writes a unique evidence directory under the seat-sweep root. Missing
FreeCAD is a hard test failure, never a mock, skip or successful empty suite.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

from cadcore.kernel.features_sweep import ADAPTER_SOURCE, make_step
from cadcore.kernel.subproc import KernelExecutionError, SubprocessKernel, SubprocessShape

MM = 1_000_000
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-sweep")
_RUN_ROOT = None
_OBSERVATIONS = []


def circle_profile(radius_nm=2 * MM, *, plane="XY", center=(0, 0)):
    return {"recipe": [{"op": "sketch", "sketch": {
        "plane": plane, "entities": [
            {"kind": "circle", "geom": {"center": list(center), "radius_nm": radius_nm}}
        ]}}]}


def injected_script(template):
    anchor = "\ndef _build_recipe(recipe):"
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    if anchor not in template or dispatch not in template:
        raise AssertionError("adapter template injection coordinates changed")
    registry = ('\nif "_EXTENDED_BUILDERS" not in globals():\n'
                '    _EXTENDED_BUILDERS = {}\n')
    template = template.replace(anchor, registry + ADAPTER_SOURCE + anchor, 1)
    if "if op in _EXTENDED_BUILDERS:" not in template:
        template = template.replace(
            dispatch, "    if op in _EXTENDED_BUILDERS:\n"
            "        return _EXTENDED_BUILDERS[op](step, previous)\n" + dispatch, 1)
    return template


class SweepContractTests(unittest.TestCase):
    def test_step_is_normalized_json_and_does_not_alias_inputs(self):
        profile = circle_profile()
        path = [[0, 0, 0], [0, 0, 10 * MM]]
        expected = {"op": "sweep", "profile": profile, "path_points_nm": path}
        step = make_step(profile=profile, path_points_nm=path)
        self.assertEqual(step, expected)
        self.assertEqual(json.loads(json.dumps(step)), step)
        profile["recipe"][0]["sketch"]["entities"][0]["geom"]["radius_nm"] = 99 * MM
        path[1][2] = 99 * MM
        self.assertEqual(step["profile"], circle_profile())
        self.assertEqual(step["path_points_nm"][1][2], 10 * MM)

    def test_mapping_and_tuple_recipe_are_accepted(self):
        from types import MappingProxyType

        profile = MappingProxyType({"recipe": tuple(circle_profile()["recipe"])})
        step = make_step(profile=profile, path_points_nm=[(0, 0, 0), (0, 0, MM)])
        self.assertIsInstance(step["profile"]["recipe"], list)
        self.assertEqual(step["path_points_nm"], [[0, 0, 0], [0, 0, MM]])

    def test_empty_or_malformed_profiles_fail(self):
        for profile in (None, [], {}, {"recipe": []}, {"recipe": {}},
                        {"recipe": [None]}, {"recipe": [{}]},
                        {"recipe": [{"op": True}]}, {"recipe": [{"op": ""}]}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                make_step(profile=profile, path_points_nm=[[0, 0, 0], [0, 0, MM]])

    def test_non_json_or_nonfinite_recipe_data_fail(self):
        for value in (float("inf"), float("-inf"), float("nan"), object(), {1: 2}):
            profile = circle_profile()
            profile["recipe"][0]["bad"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_step(profile=profile, path_points_nm=[[0, 0, 0], [0, 0, MM]])

    def test_empty_malformed_and_noninteger_points_fail(self):
        paths = [None, [], [[0, 0, 0]], ((0, 0, 0), (0, 0, MM)),
                 [[0, 0], [0, 0, MM]], [[0, 0, 0], "123"],
                 [[0, 0, 0], [0, 0, 1, 2]]]
        paths.extend([[0, 0, 0], [0, 0, value]]
                     for value in (True, False, 1.5, 1.0, "10", None,
                                   float("inf"), float("-inf"), float("nan"), 10 ** 400))
        for path in paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                make_step(profile=circle_profile(), path_points_nm=path)

    def test_degenerate_closed_and_backtracking_paths_fail(self):
        for path in (
            [[0, 0, 0], [0, 0, 0]],
            [[0, 0, 0], [0, 0, MM], [0, 0, MM]],
            [[0, 0, 0], [0, 0, MM], [0, 0, 0]],
            [[0, 0, 0], [0, 0, 2 * MM], [0, 0, MM]],
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                make_step(profile=circle_profile(), path_points_nm=path)

    def test_adapter_is_ascii_and_has_no_cadcore_import(self):
        self.assertTrue(ADAPTER_SOURCE.isascii())
        self.assertNotIn("import cadcore", ADAPTER_SOURCE)
        self.assertTrue(ADAPTER_SOURCE.rstrip().endswith(
            "_EXTENDED_BUILDERS['sweep'] = _build_sweep"))
        compile(ADAPTER_SOURCE, "<sweep-adapter>", "exec")

    def test_injection_preserves_existing_registry_and_dispatch(self):
        kernel = SubprocessKernel()
        template = "\n_EXTENDED_BUILDERS = {}\n" + kernel._profile.adapter_script
        template = template.replace(
            "\ndef _build_recipe(recipe):",
            "\n_EXTENDED_BUILDERS['other_feature'] = _existing_builder\n"
            "_registry_before = _EXTENDED_BUILDERS\n"
            "_builders_before = dict(_EXTENDED_BUILDERS)\n"
            "\ndef _build_recipe(recipe):", 1)
        script = injected_script(template)
        compile(script, "<combined-adapter>", "exec")
        self.assert_registry_execution(script)
        # A real reset must fail even though an earlier legitimate init is allowed.
        mutant = script.replace(
            '_builders_before = dict(_EXTENDED_BUILDERS)',
            '_builders_before = dict(_EXTENDED_BUILDERS)\n_EXTENDED_BUILDERS = {}', 1)
        with self.assertRaises(AssertionError):
            self.assert_registry_execution(mutant)

    def assert_registry_execution(self, script):
        """Execute registration/dispatch only; geometry stays in real CLI tests."""
        prefix = script.split("\ndef _build_recipe(recipe):", 1)[0]
        tree = ast.parse(prefix)
        tree.body = [
            node for node in tree.body
            if not (isinstance(node, ast.Import)
                    and any(alias.name in ("FreeCAD", "Part") for alias in node.names))
        ]
        namespace = {"_existing_builder": lambda step, previous: previous}
        exec(compile(tree, "<registry-prefix>", "exec"), namespace)
        registry = namespace["_EXTENDED_BUILDERS"]
        self.assertIs(registry, namespace["_registry_before"])
        for name, builder in namespace["_builders_before"].items():
            if name != "sweep":
                self.assertIs(registry.get(name), builder)
        self.assertIs(registry["sweep"], namespace["_build_sweep"])
        dispatch = next(
            node for node in ast.parse(script).body
            if isinstance(node, ast.FunctionDef) and node.name == "_build_step")
        exec(compile(ast.Module(body=[dispatch], type_ignores=[]),
                     "<registry-dispatch>", "exec"), namespace)
        previous = object()
        self.assertIs(namespace["_build_step"]({"op": "other_feature"}, previous), previous)
        _OBSERVATIONS.append({"kind": "registry-execution", "same_registry": True,
                              "preserved_builders": sorted(namespace["_builders_before"]),
                              "dispatch_preserved": True})

    def test_injection_initializes_missing_registry_and_can_be_repeated(self):
        kernel = SubprocessKernel()
        script = injected_script(kernel._profile.adapter_script)
        script = script.replace(
            "\ndef _build_recipe(recipe):",
            "\n_EXTENDED_BUILDERS['other_feature'] = _existing_builder\n"
            "_registry_before = _EXTENDED_BUILDERS\n"
            "_builders_before = dict(_EXTENDED_BUILDERS)\n"
            "\ndef _build_recipe(recipe):", 1)
        self.assert_registry_execution(injected_script(script))


class RealSweepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _RUN_ROOT
        if _RUN_ROOT is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            _RUN_ROOT = EVIDENCE_ROOT / ("unittest-" + stamp)
            _RUN_ROOT.mkdir(parents=True, exist_ok=False)
        cls.kernel = SubprocessKernel(
            work_dir=str(_RUN_ROOT / "kernel"), keep_work_dir=True,
            probe_timeout_s=15, op_timeout_s=60,
        )
        report = cls.kernel.probe_report()
        if not report.available:
            raise AssertionError("real FreeCAD required: %s" % report.as_dict())
        cls.kernel._profile = replace(
            cls.kernel._profile,
            adapter_script=injected_script(cls.kernel._profile.adapter_script),
        )
        _OBSERVATIONS.append({"kind": "probe", "reading": report.as_dict()})
        cls.assert_script_uses_real_bindings()

    @classmethod
    def assert_script_uses_real_bindings(cls):
        script = cls.kernel._profile.adapter_script
        if "import FreeCAD as App" not in script or "import Part" not in script:
            raise AssertionError("fixture is not the actual FreeCAD adapter template")
        if "makePipeShell([outer], True, False, 1)" not in script:
            raise AssertionError("fixture does not execute the Part solid sweep")

    def solid(self, profile=None, path=None):
        step = make_step(
            profile=circle_profile() if profile is None else profile,
            path_points_nm=[[0, 0, 0], [0, 0, 10 * MM]] if path is None else path,
        )
        return SubprocessShape(recipe=(step,))

    def measure(self, shape, name):
        properties = self.kernel.mass_properties(shape)
        mesh = self.kernel.tessellate(shape, deflection_mm=0.05)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertGreaterEqual(len(mesh["vertices_mm"]), 8)
        self.assertGreater(len(mesh["triangles"]), 8)
        self.assertEqual(mesh["properties"]["volume_nm3"], properties.volume_nm3)
        self.assertNotIn("volume_nm3", properties.unmeasured)
        self.assertNotIn("center_nm", properties.unmeasured)
        self.assertIsNotNone(properties.center_nm)
        self.assertEqual(properties.source, "external")
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            self.assertEqual(len(set(triangle)), 3)
            self.assertTrue(all(0 <= index < len(mesh["vertices_mm"]) for index in triangle))
        (_RUN_ROOT / (name + "-mesh.json")).write_text(json.dumps(mesh), encoding="utf-8")
        _OBSERVATIONS.append({
            "kind": name, "volume_mm3": properties.volume_nm3 / 1e18,
            "bbox_nm": properties.bbox_nm, "center_nm": properties.center_nm,
            "solid_count": mesh["solid_count"], "vertices": len(mesh["vertices_mm"]),
            "triangles": len(mesh["triangles"]),
        })
        return properties, mesh

    def test_straight_circle_matches_analytic_cylinder_not_envelope(self):
        props, mesh = self.measure(self.solid(), "straight-circle")
        expected = math.pi * 2 ** 2 * 10
        self.assertAlmostEqual(props.volume_nm3 / 1e18, expected, places=7)
        self.assertEqual(props.bbox_nm, (-2 * MM, -2 * MM, 0, 2 * MM, 2 * MM, 10 * MM))
        self.assertEqual(props.center_nm, (0, 0, 5 * MM))
        self.assertGreater(abs(props.volume_nm3 / 1e18 - 4 * 4 * 10), 30)
        for x, y, z in mesh["vertices_mm"]:
            self.assertLessEqual(x * x + y * y, 4 + 1e-6)
            self.assertGreaterEqual(z, -1e-7)
            self.assertLessEqual(z, 10 + 1e-7)

    def test_right_angle_path_is_one_real_mitered_solid(self):
        shape = self.solid(path=[[0, 0, 0], [0, 0, 10 * MM], [10 * MM, 0, 10 * MM]])
        props, mesh = self.measure(shape, "right-angle")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 4 * 20, places=6)
        self.assertEqual(props.bbox_nm, (-2 * MM, -2 * MM, 0, 10 * MM, 2 * MM, 12 * MM))
        for x, y, z in mesh["vertices_mm"]:
            # L-shaped pipe, not the full bounding prism.
            self.assertTrue(x <= 2 + 1e-6 or z >= 8 - 1e-6)
        self.assertTrue(any(x > 8 for x, _, _ in mesh["vertices_mm"]))
        self.assertTrue(any(z < 2 for _, _, z in mesh["vertices_mm"]))

    def test_hollow_profile_keeps_hole(self):
        profile = circle_profile()
        profile["recipe"][0]["sketch"]["entities"].extend(
            circle_profile(MM)["recipe"][0]["sketch"]["entities"])
        props, _ = self.measure(self.solid(profile), "hollow-circle")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * (4 - 1) * 10, places=7)

    def test_yz_profile_sweeps_in_world_x(self):
        shape = self.solid(circle_profile(plane="YZ"), [[0, 0, 0], [10 * MM, 0, 0]])
        props, _ = self.measure(shape, "yz-world-x")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 4 * 10, places=7)
        self.assertEqual(props.bbox_nm, (0, -2 * MM, -2 * MM, 10 * MM, 2 * MM, 2 * MM))

    def test_translated_profile_and_path_keep_world_coordinates(self):
        shape = self.solid(circle_profile(center=(3 * MM, -4 * MM)),
                           [[3 * MM, -4 * MM, 0], [3 * MM, -4 * MM, 10 * MM]])
        props, _ = self.measure(shape, "translated")
        self.assertEqual(props.center_nm, (3 * MM, -4 * MM, 5 * MM))
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 4 * 10, places=7)

    def test_closed_rectangle_is_swept_not_replaced_by_circle(self):
        points = [(-MM, -2 * MM), (MM, -2 * MM), (MM, 2 * MM), (-MM, 2 * MM)]
        entities = [{"kind": "line", "geom": {"p1": list(points[index]),
                                              "p2": list(points[(index + 1) % 4])}}
                    for index in range(4)]
        profile = {"recipe": [{"op": "sketch", "sketch": {"entities": entities}}]}
        props, _ = self.measure(self.solid(profile), "rectangle")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 2 * 4 * 10, places=7)
        self.assertEqual(props.bbox_nm, (-MM, -2 * MM, 0, MM, 2 * MM, 10 * MM))

    def test_negative_axis_path_sweeps_without_implicit_rotation(self):
        props, _ = self.measure(self.solid(path=[[0, 0, 0], [0, 0, -10 * MM]]),
                                "negative-axis")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 4 * 10, places=7)
        self.assertEqual(props.center_nm, (0, 0, -5 * MM))

    def test_multiple_collinear_segments_keep_total_path_length(self):
        props, _ = self.measure(
            self.solid(path=[[0, 0, 0], [0, 0, 4 * MM], [0, 0, 10 * MM]]), "collinear")
        self.assertAlmostEqual(props.volume_nm3 / 1e18, math.pi * 4 * 10, places=7)

    def test_real_binding_documents_pipe_shell_contract(self):
        script = self.kernel._profile.adapter_script.replace(
            'payload["properties"] = _mass_properties',
            'payload["sweep_api_doc"] = Part.Wire.makePipeShell.__doc__\n'
            '            payload["properties"] = _mass_properties', 1)
        profile = self.kernel._profile
        self.kernel._profile = replace(profile, adapter_script=script)
        try:
            payload = self.kernel._run_job(
                {"op": "mass", "shape": {"recipe": list(self.solid().recipe)}})
        finally:
            self.kernel._profile = profile
        doc = payload["sweep_api_doc"]
        self.assertIn("makePipeShell", doc)
        self.assertIn("transition", doc.lower())
        (_RUN_ROOT / "freecad-pipe-api.txt").write_text(doc, encoding="utf-8")
        _OBSERVATIONS.append({"kind": "real-binding-api", "doc": doc})

    def assert_kernel_failure(self, shape, message):
        with self.assertRaises(KernelExecutionError) as caught:
            self.kernel.mass_properties(shape)
        error = caught.exception
        self.assertEqual(error.reason, "op_failed")
        self.assertIn(message, str(error))
        self.assertIn("_build_sweep", error.details["traceback_tail"])
        self.assertEqual(error.details["rc"], 3)
        _OBSERVATIONS.append({"kind": "expected-failure", "error": str(error),
                              "details": error.details})

    def test_open_profile_fails_with_root_cause(self):
        profile = {"recipe": [{"op": "sketch", "sketch": {"entities": [
            {"kind": "line", "geom": {"p1": [-MM, 0], "p2": [MM, 0]}}
        ]}}]}
        self.assert_kernel_failure(self.solid(profile), "closed wires")

    def test_empty_profile_geometry_fails_with_root_cause(self):
        profile = {"recipe": [{"op": "sketch", "sketch": {"entities": []}}]}
        self.assert_kernel_failure(self.solid(profile), "sketch has no entities")

    def test_solid_is_not_accepted_as_profile(self):
        profile = circle_profile()
        profile["recipe"].append({"op": "extrude", "distance_nm": MM})
        self.assert_kernel_failure(self.solid(profile), "not a solid or shell")

    def test_nonplanar_closed_profile_fails_in_real_kernel(self):
        points = [(0, 0, 0), (MM, 0, 0), (MM, MM, MM), (0, MM, 0)]
        profile = {"recipe": [{"op": "sketch", "sketch": {"entities": [
            {"kind": "line", "geom": {"p1": list(points[index]),
                                     "p2": list(points[(index + 1) % 4])}}
            for index in range(4)
        ]}}]}
        with self.assertRaises(KernelExecutionError) as caught:
            self.kernel.mass_properties(self.solid(profile))
        self.assertEqual(caught.exception.reason, "op_failed")
        self.assertEqual(caught.exception.details["rc"], 3)
        self.assertIn("_build_sweep", caught.exception.details["traceback_tail"])
        _OBSERVATIONS.append({"kind": "nonplanar-rejection", "error": str(caught.exception),
                              "details": caught.exception.details})

    def test_oblique_or_out_of_plane_path_is_rejected(self):
        for path, reason in (
            ([[0, 0, 0], [MM, 0, MM]], "normal to the profile plane"),
            ([[0, 0, MM], [0, 0, 2 * MM]], "start in the profile plane"),
        ):
            with self.subTest(path=path):
                self.assert_kernel_failure(self.solid(path=path), reason)

    def test_replay_revalidates_tampered_integer_and_degenerate_paths(self):
        for path, reason in (
            ([[0, 0, 0], [0, 0, True]], "integer nm"),
            ([[0, 0, 0], [0, 0, 1.5]], "integer nm"),
            ([[0, 0, 0], [0, 0, float("inf")]], "integer nm"),
            ([[0, 0, 0], [0, 0, 0]], "zero-length"),
        ):
            step = {"op": "sweep", "profile": circle_profile(), "path_points_nm": path}
            with self.subTest(path=path):
                self.assert_kernel_failure(SubprocessShape(recipe=(step,)), reason)


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
    probe_path = _RUN_ROOT / "write-probe"
    probe_path.touch(exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    wall_start = time.monotonic()
    log = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    log_path = _RUN_ROOT / "tests.log"
    verdict = "PASS" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "FAIL"
    ended = datetime.now(timezone.utc).isoformat()
    line = "SWEEP_REAL verdict=%s time=%s tests=%d failed=%d errors=%d skipped=%d" % (
        verdict, ended, result.testsRun, len(result.failures), len(result.errors), len(result.skipped))
    log_path.write_text(log.getvalue() + "\n" + line + "\n", encoding="utf-8")
    module = sys.modules[make_step.__module__]
    files = [Path(inspect.getfile(module)), Path(__file__).resolve()]
    payload = {
        "verdict": verdict, "started_at": started, "time": ended,
        "wall_s": time.monotonic() - wall_start, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "log": str(log_path),
        "observations": _OBSERVATIONS, "uid": os.getuid(),
        "sources": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "command": ["sudo", "-n", "-u", "infra", "python3", "-B", str(files[1]),
                    "--evidence-root", str(root)],
    }
    (_RUN_ROOT / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(line)
    print("evidence=" + str(_RUN_ROOT))
    if verdict != "PASS":
        print(log.getvalue())
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
