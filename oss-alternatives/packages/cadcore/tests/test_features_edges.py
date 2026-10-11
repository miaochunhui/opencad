"""Real FreeCAD edge finishing, analytic volume and independent mesh regression.

Run from the cadcore package:
  sudo -n -u infra python3 -B tests/test_features_edges.py --evidence-dir
      /hf3fs-jd/data/tmp/opencad-features-20261010/seat-edges

The standalone runner retains timestamped logs, JSON, a human-facing HTML report,
all real CLI job/result files, and source fingerprints. Missing FreeCAD is a
failure, not a skip. The test-only injection does not modify subproc.py.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from cadcore.errors import GeometryError
from cadcore.kernel import features_edges, subproc

MM = 1_000_000
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-edges")
RUN_ROOT = None
MEASUREMENTS = {}

# Measure the previous B-Rep's actual edge lengths and the finishing methods'
# installed documentation. This does not replace or mock the finishing builder.
_METADATA_SOURCE = '''
_edge_test_original_mass = _mass_properties

def _mass_properties(shape, recipe=None):
    result = _edge_test_original_mass(shape, recipe)
    result["edge_lengths_mm"] = [float(edge.Length) for edge in shape.Edges]
    result["valid"] = bool(shape.isValid())
    result["solid_count"] = len(shape.Solids)
    result["api_docs"] = {"makeFillet": shape.makeFillet.__doc__,
                          "makeChamfer": shape.makeChamfer.__doc__}
    return result
'''


def _inject_adapter(source):
    marker = "def _build_recipe(recipe):"
    dispatch = '    raise ValueError("unsupported build op: %s" % op)'
    if "def _build_edge_finish(step, previous):" not in source:
        if source.count(marker) != 1 or source.count(dispatch) != 1:
            raise AssertionError("legacy test adapter injection anchors changed")
        source = source.replace(
            marker, "_EXTENDED_BUILDERS = {}\n" + features_edges.ADAPTER_SOURCE + "\n" + marker, 1)
        source = source.replace(
            dispatch,
            "    if op in _EXTENDED_BUILDERS:\n"
            "        return _EXTENDED_BUILDERS[op](step, previous)\n" + dispatch, 1)
    main = "def main():"
    if source.count(main) != 1:
        raise AssertionError("test adapter main anchor changed")
    return source.replace(main, _METADATA_SOURCE + "\n" + main, 1)


def _rectangle():
    return {"plane": "XY", "entities": [
        {"kind": "line", "geom": {"p1": [0, 0], "p2": [40 * MM, 0]}},
        {"kind": "line", "geom": {"p1": [40 * MM, 0], "p2": [40 * MM, 20 * MM]}},
        {"kind": "line", "geom": {"p1": [40 * MM, 20 * MM], "p2": [0, 20 * MM]}},
        {"kind": "line", "geom": {"p1": [0, 20 * MM], "p2": [0, 0]}},
    ]}


def _mesh_volume(mesh):
    terms = []
    moments = [[], [], []]
    incidence = {}
    vertices = mesh["vertices_mm"]
    for triangle in mesh["triangles"]:
        a, b, c = (vertices[index] for index in triangle)
        volume = (
            (a[0] * (b[1] * c[2] - b[2] * c[1])
             + a[1] * (b[2] * c[0] - b[0] * c[2])
             + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0)
        terms.append(volume)
        for i in range(3):
            moments[i].append(volume * (a[i] + b[i] + c[i]) / 4.0)
        # FreeCAD may duplicate vertices along face seams; weld by coordinates
        # for the closed-manifold check, but integrate the actual triangles.
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((tuple(round(v, 7) for v in vertices[triangle[i]]),
                                 tuple(round(v, 7) for v in vertices[triangle[j]]))))
            incidence[edge] = incidence.get(edge, 0) + 1
    signed_volume = math.fsum(terms)
    center = [math.fsum(moment) / signed_volume for moment in moments]
    return abs(signed_volume), incidence, center


class EdgeRecipeTests(unittest.TestCase):
    def test_contract_and_defensive_copy(self):
        for kind in ("fillet", "chamfer"):
            indices = [3, 1]
            step = features_edges.make_step(kind=kind, size_nm=MM, edge_indices=indices)
            self.assertEqual(step, {"op": kind, "size_nm": MM, "edge_indices": [3, 1]})
            indices.append(2)
            self.assertEqual(step["edge_indices"], [3, 1])
            self.assertEqual(json.loads(json.dumps(step)), step)

    def test_size_requires_positive_integer(self):
        for kind in ("fillet", "chamfer"):
            for size in (0, -1, True, False, 1.5, "1000000", None, float("nan"), float("inf")):
                with self.subTest(kind=kind, size=size), self.assertRaises(GeometryError) as caught:
                    features_edges.make_step(kind=kind, size_nm=size, edge_indices=[1])
                self.assertEqual(caught.exception.reason, "invalid_edge_finish_parameters")

    def test_indices_require_nonempty_unique_positive_integer_list(self):
        for indices in ([], None, (), [0], [-1], [True], [False], [1.0], ["1"],
                        [1, 1], [1, 2, 1], [[1]], {"1": 1}):
            with self.subTest(indices=indices), self.assertRaises(GeometryError):
                features_edges.make_step(kind="fillet", size_nm=MM, edge_indices=indices)

    def test_unknown_kind_rejected(self):
        for kind in ("", "round", "FILLET", None, 1, [], {}):
            with self.subTest(kind=kind), self.assertRaises(GeometryError):
                features_edges.make_step(kind=kind, size_nm=MM, edge_indices=[1])

    def test_snippet_compiles_and_registers_both_builders(self):
        namespace = {"_EXTENDED_BUILDERS": {}}
        exec(compile(features_edges.ADAPTER_SOURCE, "features_edges adapter", "exec"), namespace)
        self.assertIs(namespace["_EXTENDED_BUILDERS"]["fillet"], namespace["_build_edge_finish"])
        self.assertIs(namespace["_EXTENDED_BUILDERS"]["chamfer"], namespace["_build_edge_finish"])
        self.assertTrue(features_edges.ADAPTER_SOURCE.isascii())


class RealEdgeFinishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = RUN_ROOT if RUN_ROOT is not None else EVIDENCE_ROOT
        root.mkdir(parents=True, exist_ok=True)
        cls.work = Path(tempfile.mkdtemp(prefix="kernel-", dir=root))
        cls.kernel = subproc.SubprocessKernel(
            work_dir=cls.work, keep_work_dir=True, probe_timeout_s=15,
            extra_env={"TMPDIR": str(cls.work), "PYTHONDONTWRITEBYTECODE": "1"})
        # Probe also uses tempfile, so keep its artifacts in the evidence area.
        prior_tempdir = tempfile.tempdir
        tempfile.tempdir = str(cls.work)
        try:
            report = cls.kernel.probe_report()
        finally:
            tempfile.tempdir = prior_tempdir
        if not report.available or not report.executable:
            raise AssertionError("real FreeCAD required: %s" % report.as_dict())
        cls.kernel._profile = replace(
            cls.kernel._profile, adapter_script=_inject_adapter(cls.kernel._profile.adapter_script))
        cls.wire = cls.kernel.sketch_to_wire(_rectangle())
        cls.box = cls.kernel.extrude(cls.wire, distance_nm=30 * MM)
        cls.base = cls.properties(cls.box)
        if not (cls.base["valid"] and cls.base["solid_count"] == 1
                and math.isclose(cls.base["volume_nm3"] / 1e18, 24000, abs_tol=1e-8)):
            raise AssertionError("rectangle/extrusion fixture did not produce the actual expected solid")
        MEASUREMENTS["backend"] = report.as_dict()
        MEASUREMENTS["base"] = cls.base

    @classmethod
    def tearDownClass(cls):
        MEASUREMENTS["kernel_stats"] = cls.kernel.stats()
        MEASUREMENTS["kernel_work"] = str(cls.work)
        cls.kernel.close()

    @classmethod
    def properties(cls, shape):
        return cls.kernel._run_job(
            {"schema": subproc.SCHEMA, "op": "mass", "shape": shape.to_dict()})["properties"]

    def finish(self, kind, *, size_nm=2 * MM, indices=None, source=None):
        step = features_edges.make_step(
            kind=kind, size_nm=size_nm, edge_indices=[1] if indices is None else indices)
        shape = self.box if source is None else source
        return replace(shape, recipe=shape.recipe + (step,))

    def assert_solid(self, properties):
        self.assertTrue(properties["valid"])
        self.assertEqual(properties["solid_count"], 1)
        self.assertGreater(properties["volume_nm3"], 0)
        self.assertEqual(properties["bbox_nm"], [0, 0, 0, 40 * MM, 20 * MM, 30 * MM])
        self.assertNotIn("center_nm", properties["unmeasured"])
        self.assertEqual(len(properties["center_nm"]), 3)

    def expected_volume(self, kind):
        length = self.base["edge_lengths_mm"][0]
        self.assertAlmostEqual(length, 30.0, places=7)
        removed = 2.0 ** 2 * length * (1 - math.pi / 4 if kind == "fillet" else 0.5)
        return 24000.0 - removed

    def assert_failure(self, shape, message):
        with self.assertRaises(subproc.KernelExecutionError) as caught:
            self.kernel.mass_properties(shape)
        self.assertEqual(caught.exception.reason, subproc.REASON_OP_FAILED)
        self.assertIn(message, str(caught.exception))
        self.assertNotEqual(self.kernel.stats()["last_rc"], 0)

    def test_single_edge_fillet_analytic_volume(self):
        properties = self.properties(self.finish("fillet"))
        self.assert_solid(properties)
        expected = self.expected_volume("fillet")
        self.assertAlmostEqual(properties["volume_nm3"] / 1e18, expected, places=6)
        self.assertGreater(len(properties["edge_lengths_mm"]), len(self.base["edge_lengths_mm"]))
        MEASUREMENTS["fillet"] = {"properties": properties, "expected_volume_mm3": expected}

    def test_single_edge_chamfer_analytic_volume(self):
        properties = self.properties(self.finish("chamfer"))
        self.assert_solid(properties)
        expected = self.expected_volume("chamfer")
        self.assertAlmostEqual(properties["volume_nm3"] / 1e18, expected, places=6)
        self.assertGreater(len(properties["edge_lengths_mm"]), len(self.base["edge_lengths_mm"]))
        MEASUREMENTS["chamfer"] = {"properties": properties, "expected_volume_mm3": expected}

    def test_fillet_mesh_is_closed_and_has_analytic_volume(self):
        self.check_mesh("fillet", delta=0.2)

    def test_chamfer_mesh_is_closed_and_has_analytic_volume(self):
        self.check_mesh("chamfer", delta=1e-7)

    def check_mesh(self, kind, delta):
        mesh = self.kernel.tessellate(self.finish(kind), deflection_mm=0.01)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertGreater(len(mesh["triangles"]), 12)
        self.assertGreater(len(mesh["vertices_mm"]), 8)
        for vertex in mesh["vertices_mm"]:
            self.assertEqual(len(vertex), 3)
            self.assertTrue(all(math.isfinite(value) for value in vertex))
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            self.assertEqual(len(set(triangle)), 3)
            self.assertTrue(all(isinstance(i, int) and 0 <= i < len(mesh["vertices_mm"])
                                for i in triangle))
        volume, incidence, center = _mesh_volume(mesh)
        self.assertTrue(incidence)
        self.assertTrue(all(count == 2 for count in incidence.values()))
        expected = self.expected_volume(kind)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, expected, places=6)
        self.assertAlmostEqual(volume, expected, delta=delta)
        self.assertNotIn("center_nm", mesh["properties"]["unmeasured"])
        for i in range(3):
            self.assertAlmostEqual(center[i], mesh["properties"]["center_nm"][i] / MM, delta=1e-4)
        bounds = [min(point[i] for point in mesh["vertices_mm"]) for i in range(3)]
        bounds += [max(point[i] for point in mesh["vertices_mm"]) for i in range(3)]
        for measured, expected_bound in zip(bounds, [0, 0, 0, 40, 20, 30]):
            self.assertAlmostEqual(measured, expected_bound, delta=1e-7)
        mesh_path = self.work / ("%s-mesh.json" % kind)
        mesh_path.write_text(json.dumps(mesh, sort_keys=True), encoding="utf-8")
        MEASUREMENTS[kind + "_mesh"] = {
            "path": str(mesh_path), "vertices": len(mesh["vertices_mm"]),
            "triangles": len(mesh["triangles"]), "closed_manifold": True,
            "integrated_volume_mm3": volume, "expected_volume_mm3": expected,
            "integrated_center_mm": center, "center_nm": mesh["properties"]["center_nm"],
            "absolute_error_mm3": abs(volume - expected)}

    def test_single_solid_center_matches_analytic_removed_material(self):
        radius = 2.0
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                properties = self.properties(self.finish(kind))
                volume = self.expected_volume(kind)
                removed = 24000 - volume
                removed_xy = (radius * (5.0 / 6 - math.pi / 4) / (1 - math.pi / 4)
                              if kind == "fillet" else radius / 3)
                expected = [(24000 * coordinate - removed * removed_xy) / volume
                            for coordinate in (20, 10)] + [15.0]
                self.assertNotIn("center_nm", properties["unmeasured"])
                for i in range(3):
                    self.assertAlmostEqual(properties["center_nm"][i] / MM, expected[i], delta=1e-6)

    def test_last_one_based_edge_is_accepted(self):
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                properties = self.properties(
                    self.finish(kind, indices=[len(self.base["edge_lengths_mm"])]))
                self.assert_solid(properties)
                self.assertLess(properties["volume_nm3"], self.base["volume_nm3"])

    def test_multiple_edges_are_actually_finished(self):
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                properties = self.properties(self.finish(kind, indices=[1, 3]))
                self.assert_solid(properties)
                self.assertLess(properties["volume_nm3"] / 1e18, self.expected_volume(kind))

    def test_each_step_consumes_prior_solid(self):
        for kind in ("fillet", "chamfer"):
            step = features_edges.make_step(kind=kind, size_nm=MM, edge_indices=[1])
            with self.subTest(kind=kind, previous="missing"):
                self.assert_failure(subproc.SubprocessShape(recipe=(step,)), "requires a prior solid")
            with self.subTest(kind=kind, previous="wire"):
                self.assert_failure(replace(self.wire, recipe=self.wire.recipe + (step,)),
                                    "requires a valid positive-volume solid")

    def test_replay_rejects_out_of_range_indices(self):
        index = len(self.base["edge_lengths_mm"]) + 1
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                self.assert_failure(self.finish(kind, indices=[index]), "outside one-based range")

    def test_replay_rejects_malformed_indices_without_host_validation(self):
        for kind in ("fillet", "chamfer"):
            for indices in ([], [0], [-1], [True], [1.0], ["1"], [1, 1], None):
                step = {"op": kind, "size_nm": MM, "edge_indices": indices}
                with self.subTest(kind=kind, indices=indices):
                    self.assert_failure(replace(self.box, recipe=self.box.recipe + (step,)),
                                        "edge_indices")

    def test_replay_rejects_malformed_sizes_without_host_validation(self):
        for kind in ("fillet", "chamfer"):
            for size in (0, -1, True, 1.5, "1000000", None):
                step = {"op": kind, "size_nm": size, "edge_indices": [1]}
                with self.subTest(kind=kind, size=size):
                    self.assert_failure(replace(self.box, recipe=self.box.recipe + (step,)),
                                        "positive integer")

    def test_impossible_radius_and_distance_fail_with_coordinates(self):
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                self.assert_failure(self.finish(kind, size_nm=100 * MM),
                                    "%s failed: size_nm=100000000 edge_indices=[1]" % kind)

    def test_unrepresentable_size_is_not_silent_success(self):
        for kind in ("fillet", "chamfer"):
            with self.subTest(kind=kind):
                self.assert_failure(self.finish(kind, size_nm=10 ** 400),
                                    "size_nm cannot be represented")

    def test_replay_is_deterministic_and_source_recipe_unchanged(self):
        original = self.box.to_dict()
        for kind in ("fillet", "chamfer"):
            shape = self.finish(kind)
            first = self.kernel.mass_properties(shape)
            second = self.kernel.mass_properties(shape)
            self.assertEqual(first, second)
        self.assertEqual(self.box.to_dict(), original)


def _fingerprints():
    # Integrated profiles embed every extended module, not just this seat's
    # snippet. Bind the actual complete kernel source set used by get_profile.
    paths = [Path(__file__)] + sorted(Path(subproc.__file__).parent.glob("*.py"))
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    global RUN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_ROOT = args.evidence_dir / stamp
    RUN_ROOT.mkdir(parents=True, exist_ok=False)
    probe = RUN_ROOT / ".write-probe"
    probe.touch(exist_ok=False)
    probe.unlink()
    source_before = _fingerprints()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    source_after = _fingerprints()
    passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
    passed = passed and source_before == source_after
    passed = passed and all(key in MEASUREMENTS for key in
                            ("base", "fillet", "chamfer", "fillet_mesh", "chamfer_mesh"))
    now = datetime.now(timezone.utc).isoformat()
    verdict = "PASS" if passed else "FAIL"
    line = ("OPENCAD_EDGES verdict=%s collected_at=%s tests=%s failures=%s errors=%s skips=%s "
            "source_unchanged=%s evidence=%s" %
            (verdict, now, result.testsRun, len(result.failures), len(result.errors),
             len(result.skipped), source_before == source_after, RUN_ROOT))
    log = stream.getvalue() + "\n" + line + "\n"
    (RUN_ROOT / "tests.log").write_text(log, encoding="utf-8")
    payload = {"verdict": verdict, "collected_at": now, "tests": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors),
               "skips": len(result.skipped), "measurements": MEASUREMENTS,
               "source_before": source_before, "source_after": source_after,
               "source_unchanged": source_before == source_after,
               "command": ["sudo", "-n", "-u", "infra", sys.executable, "-B",
                           str(Path(__file__).resolve()), "--evidence-dir", str(args.evidence_dir)],
               "limits": ["Current B-Rep edge positions, not persistent topological names.",
                          "Equal-distance chamfer and constant-radius fillet only.",
                          "Standalone adapter injection; main-agent integration not changed here.",
                          "Multi-solid centers use the main adapter's shared mass-center routine; "
                          "not verified in this legacy-profile seat run.",
                          "Installed FreeCAD only; not an industrial topology/performance benchmark."]}
    (RUN_ROOT / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True),
                                         encoding="utf-8")
    report = ("<!doctype html><html lang='en'><meta charset='utf-8'>"
              "<title>OpenCAD Edge Finish Evidence</title><h1>" + html.escape(line) +
              "</h1><pre>" + html.escape(json.dumps(payload, indent=2, sort_keys=True)) +
              "</pre><h2>Test Log</h2><pre>" + html.escape(log) + "</pre></html>")
    (RUN_ROOT / "report.html").write_text(report, encoding="utf-8")
    print(line, flush=True)
    if not passed:
        print(stream.getvalue(), file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
