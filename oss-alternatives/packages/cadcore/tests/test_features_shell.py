"""Real FreeCAD shell acceptance; no mocks and no optional-backend skips.

Run this file directly for durable logs/result/time under the seat evidence
directory. --probe measures the installed makeThickness API before acceptance.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import unittest
import urllib.request

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT))

from cadcore.kernel.features_shell import ADAPTER_SOURCE, make_step
from cadcore.kernel.subproc import KernelExecutionError, SubprocessKernel, SubprocessShape

MM = 1_000_000
EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-shell")
RUN_DIR = None

DISPATCH = (
    '    builder = _EXTENDED_BUILDERS.get(op)\n'
    '    if builder is not None:\n'
    '        return builder(step, previous)\n'
    '    raise ValueError("unsupported build op: %s" % op)'
)

PROBE_SOURCE = '''
def _shell_probe(step, previous):
    box = Part.makeBox(20, 16, 12)
    faces = [{"index": index, "area_mm2": face.Area,
              "center_mm": list(face.CenterOfMass)}
             for index, face in enumerate(box.Faces, 1)]
    top = [face for face in box.Faces if abs(face.CenterOfMass.z - 12) < 1e-8]
    result = box.makeThickness(top, -2.0, 1e-7, False, False, 0, 0)
    facts = {"FreeCAD": App.Version(), "OCCT": Part.OCC_VERSION,
             "doc": box.makeThickness.__doc__, "faces": faces,
             "valid": result.isValid(), "volume_mm3": result.Volume,
             "open_point_inside": result.isInside(App.Vector(10, 8, 12), 1e-7, True)}
    with open(step["path"], "w", encoding="utf-8") as handle:
        json.dump(facts, handle, indent=2)
    return result


_EXTENDED_BUILDERS["shell_probe"] = _shell_probe
'''


def inject(source, snippet):
    """Fail if host injection anchors drift instead of silently testing no shell."""
    anchor = "def _build_recipe(recipe):"
    unknown = '    raise ValueError("unsupported build op: %s" % op)'
    if source.count(anchor) != 1 or source.count(unknown) != 1:
        raise AssertionError("adapter injection anchors changed")
    source = source.replace(anchor, "_EXTENDED_BUILDERS = {}\n" + snippet + "\n" + anchor)
    return source.replace(unknown, DISPATCH)


class EvidenceKernel(SubprocessKernel):
    """Record real subprocess outcomes without replacing any CAD computation."""

    def _collect_job_result(self, job, outcome, result_path):
        directory = result_path.parent
        (directory / "stdout.log").write_text(outcome.stdout, encoding="utf-8")
        (directory / "stderr.log").write_text(outcome.stderr, encoding="utf-8")
        (directory / "outcome.json").write_text(
            json.dumps(dataclasses.asdict(outcome), indent=2), encoding="utf-8"
        )
        try:
            return super()._collect_job_result(job, outcome, result_path)
        except KernelExecutionError as exc:
            (directory / "negative.json").write_text(json.dumps({
                "message": str(exc), "reason": exc.reason, "details": exc.details,
            }, indent=2), encoding="utf-8")
            raise


def kernel_for(name, snippet=ADAPTER_SOURCE):
    directory = (RUN_DIR or EVIDENCE / "unittest") / name
    directory.mkdir(parents=True, exist_ok=True)
    kernel = EvidenceKernel(
        work_dir=directory, keep_work_dir=True, probe_timeout_s=15,
        extra_env={"TMPDIR": str(directory), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    kernel._profile = dataclasses.replace(
        kernel._profile, adapter_script=inject(kernel._profile.adapter_script, snippet)
    )
    if not kernel.available():
        raise AssertionError("real FreeCAD required: %s" % kernel.probe_report().to_dict())
    return kernel


def box(kernel, lo=(0, 0, 0), hi=(20, 16, 12)):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    if z0 != 0:
        raise ValueError("box fixture uses a zero-based extrusion")
    sketch = {"entities": [{"kind": "polyline", "geom": {"points": [
        [x0 * MM, y0 * MM], [x1 * MM, y0 * MM], [x1 * MM, y1 * MM],
        [x0 * MM, y1 * MM], [x0 * MM, y0 * MM],
    ]}}]}
    return kernel.extrude(kernel.sketch_to_wire(sketch), distance_nm=z1 * MM)


def shell(shape, indices=None, thickness=2, inward=True):
    return SubprocessShape(
        recipe=shape.recipe + (make_step(
            thickness_nm=int(thickness * MM), face_indices=[6] if indices is None else indices,
            inward=inward),),
        kernel=shape.kernel,
    )


def volume(kernel, shape):
    return kernel.mass_properties(shape).volume_nm3 / 1e18


class ShellContractTests(unittest.TestCase):
    def test_recipe_is_detached_and_defaults_inward(self):
        faces = [6, 5]
        step = make_step(thickness_nm=MM, face_indices=faces)
        self.assertEqual(step, {
            "op": "shell", "thickness_nm": MM, "face_indices": [6, 5], "inward": True,
        })
        faces.clear()
        self.assertEqual(step["face_indices"], [6, 5])

    def test_invalid_thickness_types_and_values(self):
        for value in (0, -MM, True, False, 1.5, "2", None, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_step(thickness_nm=value, face_indices=[6])

    def test_invalid_opening_faces(self):
        for faces in (None, (), (6,), [], [0], [-1], [True], [1.0], ["6"], [6, 6]):
            with self.subTest(faces=faces), self.assertRaises(ValueError):
                make_step(thickness_nm=MM, face_indices=faces)

    def test_inward_is_strict_boolean(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_step(thickness_nm=MM, face_indices=[6], inward=value)

    def test_snippet_registration_and_syntax(self):
        self.assertIn('_EXTENDED_BUILDERS["shell"] = _build_shell', ADAPTER_SOURCE)
        compile(ADAPTER_SOURCE, "<shell-adapter>", "exec")


class RealShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = kernel_for("real")
        cls.base = box(cls.kernel)

    @classmethod
    def tearDownClass(cls):
        cls.kernel.close()

    def assert_volume(self, shape, expected):
        self.assertAlmostEqual(volume(self.kernel, shape), expected, places=6)

    def assert_no_material_overlap(self, material, probe):
        expected = volume(self.kernel, probe)
        self.assertGreater(expected, 0, "the real probe must have positive volume")
        self.assert_volume(self.kernel.boolean("cut", probe, material), expected)

    def test_inward_top_open_box_analytic_volume_and_original_unchanged(self):
        self.assert_volume(self.base, 20 * 16 * 12)
        hollow = shell(self.base)
        self.assert_volume(hollow, 20 * 16 * 12 - 16 * 12 * 10)
        self.assertEqual(self.kernel.mass_properties(hollow).bbox_nm,
                         (0, 0, 0, 20 * MM, 16 * MM, 12 * MM))
        self.assert_volume(self.base, 20 * 16 * 12)

    def test_opening_and_cavity_are_empty_but_walls_and_bottom_remain(self):
        hollow = shell(self.base)
        # A cylinder crosses the exact top opening into the cavity. Its common
        # has no material, unlike an un-shelled block or a capped hollow solid.
        disk = {"entities": [{"kind": "circle", "geom": {
            "center": [10 * MM, 8 * MM, 3 * MM], "radius_nm": MM,
        }}]}
        probe = self.kernel.extrude(self.kernel.sketch_to_wire(disk), distance_nm=10 * MM)
        self.assert_no_material_overlap(hollow, probe)
        wall = box(self.kernel, hi=(1, 16, 12))
        bottom = box(self.kernel, hi=(20, 16, 1))
        self.assert_volume(self.kernel.boolean("intersect", hollow, wall), 1 * 16 * 12)
        self.assert_volume(self.kernel.boolean("intersect", hollow, bottom), 20 * 16 * 1)
        self.assertGreater(volume(self.kernel, self.kernel.boolean("intersect", self.base, probe)), 0)

    def test_two_opposite_openings_form_a_real_through_shell(self):
        hollow = shell(self.base, [5, 6])
        self.assert_volume(hollow, (20 * 16 - 16 * 12) * 12)
        probe = box(self.kernel, lo=(4, 4, 0), hi=(12, 10, 14))
        self.assert_no_material_overlap(hollow, probe)

    def test_bottom_face_selection_moves_the_cavity_not_the_outer_box(self):
        hollow = shell(self.base, [5])
        props = self.kernel.mass_properties(hollow)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, 1920, places=6)
        self.assertEqual(props.center_nm, (10 * MM, 8 * MM, 7 * MM))
        probe = box(self.kernel, lo=(4, 4, 0), hi=(12, 10, 9))
        self.assert_no_material_overlap(hollow, probe)

    def test_cylindrical_shell_is_analytic_curved_geometry_not_a_box(self):
        sketch = {"entities": [{"kind": "circle", "geom": {
            "center": [0, 0], "radius_nm": 10 * MM,
        }}]}
        cylinder = self.kernel.extrude(
            self.kernel.sketch_to_wire(sketch), distance_nm=12 * MM
        )
        hollow = shell(cylinder, [3])
        expected = math.pi * (10 ** 2 * 12 - 8 ** 2 * 10)
        self.assert_volume(hollow, expected)
        mesh = self.kernel.tessellate(hollow, deflection_mm=0.02)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, expected, places=6)
        self.assertTrue(any(
            abs(math.hypot(point[0], point[1]) - 8) < 1e-6
            and abs(point[2] - 2) < 1e-6 for point in mesh["vertices_mm"]
        ), "curved cavity wall and bottom must be present")

    def test_mesh_matches_mass_and_contains_inner_wall_vertices(self):
        hollow = shell(self.base)
        mesh = self.kernel.tessellate(hollow, deflection_mm=0.05)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, 1920, places=6)
        self.assertGreater(len(mesh["triangles"]), 12)
        vertices = mesh["vertices_mm"]
        self.assertTrue(any(
            abs(point[2] - 2) < 1e-7 and 2 - 1e-7 <= point[0] <= 18 + 1e-7
            and 2 - 1e-7 <= point[1] <= 14 + 1e-7 for point in vertices
        ), "real cavity bottom vertices required")
        total = 0
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            self.assertEqual(len(set(triangle)), 3)
            for index in triangle:
                self.assertIsInstance(index, int)
                self.assertTrue(0 <= index < len(vertices))
            a, b, c = [vertices[index] for index in triangle]
            self.assertTrue(all(math.isfinite(value) for point in (a, b, c) for value in point))
            total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                      - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        self.assertAlmostEqual(abs(total), 1920, places=6)

    def test_outward_shell_does_not_keep_original_interior(self):
        hollow = shell(self.base, inward=False)
        self.assertGreater(volume(self.kernel, hollow), 0)
        self.assert_no_material_overlap(hollow, self.base)
        mesh = self.kernel.tessellate(hollow)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertGreater(len(mesh["triangles"]), 12)

    def test_overthick_shell_is_loud_and_preserves_occ_first_cause(self):
        with self.assertRaises(KernelExecutionError) as caught:
            volume(self.kernel, shell(self.base, thickness=40))
        self.assertEqual(caught.exception.reason, "op_failed")
        self.assertNotEqual(caught.exception.details["rc"], 0)
        self.assertIn("shell", str(caught.exception))
        self.assertIn("FreeCAD=", str(caught.exception))
        self.assertIn("OCCT=", str(caught.exception))
        self.assertIn("makeThickness", caught.exception.details["traceback_tail"])
        self.assertIn("Part.OCCError: BRep_API: command not done",
                      caught.exception.details["traceback_tail"])
        for thickness in (8, 12, 20):
            with self.subTest(thickness=thickness), self.assertRaises(KernelExecutionError):
                volume(self.kernel, shell(self.base, thickness=thickness))

    def test_face_range_and_remove_all_are_rejected_by_real_adapter(self):
        for indices, message in (([7], "out of range"), ([1, 2, 3, 4, 5, 6], "every face")):
            with self.subTest(indices=indices), self.assertRaises(KernelExecutionError) as caught:
                volume(self.kernel, shell(self.base, indices))
            self.assertIn(message, str(caught.exception))
            self.assertNotEqual(caught.exception.details["rc"], 0)

    def test_replayed_untrusted_parameters_are_revalidated(self):
        for changes in (
            {"thickness_nm": True}, {"thickness_nm": 0}, {"thickness_nm": "2000000"},
            {"face_indices": []}, {"face_indices": [0]}, {"face_indices": [True]},
            {"face_indices": [6, 6]}, {"inward": 1},
        ):
            step = {"op": "shell", "thickness_nm": 2 * MM, "face_indices": [6], "inward": True}
            step.update(changes)
            shape = SubprocessShape(recipe=self.base.recipe + (step,))
            with self.subTest(changes=changes), self.assertRaises(KernelExecutionError):
                volume(self.kernel, shape)

    def test_missing_or_nonsolid_previous_is_loud(self):
        step = make_step(thickness_nm=MM, face_indices=[1])
        for recipe in ((step,), self.base.recipe[:1] + (step,)):
            with self.subTest(recipe=recipe), self.assertRaises(KernelExecutionError) as caught:
                volume(self.kernel, SubprocessShape(recipe=recipe))
            self.assertIn("previous solid", str(caught.exception))


def main():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    start = time.perf_counter()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_DIR = args.evidence_dir / (stamp + ("-probe" if args.probe else "-tests"))
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    os.environ["TMPDIR"] = str(RUN_DIR)
    import tempfile
    tempfile.tempdir = str(RUN_DIR)
    # Establish writeability without changing permissions of shared directories.
    (RUN_DIR / "START.json").write_text(json.dumps({
        "started_utc": started, "worktree": str(PKG_ROOT.parents[2]),
        "mode": "probe" if args.probe else "tests",
    }, indent=2), encoding="utf-8")
    result = {"verdict": "RUNNING", "started_utc": started, "run_dir": str(RUN_DIR)}
    paths = [Path(__file__).resolve(), PKG_ROOT / "cadcore/kernel/features_shell.py",
             PKG_ROOT / "cadcore/kernel/subproc.py"]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result["known_limitations"] = [
        "Face indices refer only to current one-based Faces order, not persistent topology.",
        "Validated on installed FreeCAD 0.19.2 package with OCCT 7.5.1, not other versions.",
        "Input must be one Solid, not a Compound or multiple solids; no persistent CAD document wiring in this seat.",
        "Host adapter empty intersection mass raises OverflowError; cavity checks use real difference.",
        "Worktree rule archive missing; checked approved /data/dev/infra rules via explicit --root.",
    ]
    result["operational_deviation"] = (
        "Initial shared-directory permission check used --check after path. The existing "
        "script entered normalization mode on the evidence parent before reading --check. "
        "No production or credential paths were involved; no restorative chmod attempted."
    )
    exit_code = 1
    try:
        if args.probe:
            url = ("https://raw.githubusercontent.com/FreeCAD/FreeCAD/0.19.2/"
                   "src/Mod/Part/App/TopoShapePyImp.cpp")
            with urllib.request.urlopen(url, timeout=30) as response:
                official = response.read().decode("utf-8")
            anchor = "PyObject* TopoShapePy::makeThickness"
            position = official.find(anchor)
            if position < 0:
                raise AssertionError("official makeThickness source anchor missing")
            end = official.find("\nPyObject*", position + len(anchor))
            selected = official[position:end if end >= 0 else len(official)]
            (RUN_DIR / "official-makeThickness.cpp").write_text(selected, encoding="utf-8")
            result["official_source"] = {
                "url": url, "source_sha256": hashlib.sha256(official.encode()).hexdigest(),
            }
            kernel = kernel_for("probe", PROBE_SOURCE)
            result["properties"] = dataclasses.asdict(kernel.mass_properties(SubprocessShape(
                recipe=({"op": "shell_probe", "path": str(RUN_DIR / "api.json")},))))
            kernel.close()
            result["verdict"] = "PASS"
            exit_code = 0
        else:
            suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
            with (RUN_DIR / "tests.log").open("w", encoding="utf-8") as log:
                tests = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            result.update(tests=tests.testsRun, failures=len(tests.failures),
                          errors=len(tests.errors), skipped=len(tests.skipped))
            result["verdict"] = "PASS" if tests.wasSuccessful() and tests.testsRun and not tests.skipped else "FAIL"
            exit_code = 0 if result["verdict"] == "PASS" else 1
    except Exception:
        import traceback
        (RUN_DIR / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        result["verdict"] = "FAIL"
        raise
    finally:
        elapsed = time.perf_counter() - start
        result["elapsed_s"] = elapsed
        result["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result["exit_code"] = exit_code
        result["source_sha256"] = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
        }
        result["source_before_sha256"] = before
        result["source_unchanged"] = result["source_sha256"] == before
        if not result["source_unchanged"]:
            result["verdict"] = "FAIL"
            exit_code = 1
            result["exit_code"] = exit_code
        negatives = []
        for path in sorted(RUN_DIR.rglob("negative.json")):
            negative = json.loads(path.read_text(encoding="utf-8"))
            negatives.append({
                "path": str(path), "reason": negative["reason"],
                "rc": negative["details"]["rc"], "message": negative["message"],
            })
        (RUN_DIR / "negative-index.json").write_text(
            json.dumps(negatives, indent=2), encoding="utf-8"
        )
        result["negative_count"] = len(negatives)
        result["negative_index"] = str(RUN_DIR / "negative-index.json")
        (RUN_DIR / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (RUN_DIR / "time.json").write_text(json.dumps({
            "elapsed_s": elapsed, "started_utc": started, "finished_utc": result["finished_utc"],
        }, indent=2), encoding="utf-8")
        print("SHELL_ACCEPTANCE verdict=%s result=%s elapsed_s=%.3f"
              % (result["verdict"], RUN_DIR / "result.json", elapsed))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
