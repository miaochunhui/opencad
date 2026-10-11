"""Real Document/FreeCAD parameter rebuild and independent mesh/STEP regression.

Run with python3 -B tests/test_parametric.py --evidence-dir <new-3FS-directory>.
Discovery uses this checkout, never a hard-coded main tree. Missing FreeCAD is
a failure. Each standalone run retains logs, source hashes and real CLI jobs.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import uuid4

CORE = Path(__file__).resolve().parents[1]
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from cadcore import docmodel, parametric
from cadcore.kernel import subproc
from cadcore.ops import sw_features
from cadcore.sketch import commands, constraints, primitives, session

MM = 1_000_000
EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-parametric")
RUN_ROOT = None
MEASUREMENTS = {}


class EvidenceResult(unittest.TextTestResult):
    first_cause = None

    def addFailure(self, test, error):
        super().addFailure(test, error)
        if self.first_cause is None:
            self.first_cause = {"test": str(test), "traceback": self.failures[-1][1]}

    def addError(self, test, error):
        super().addError(test, error)
        if self.first_cause is None:
            self.first_cause = {"test": str(test), "traceback": self.errors[-1][1]}

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None and self.first_cause is None:
            self.first_cause = {"test": str(subtest), "traceback": self._exc_info_to_string(error, test)}


class ProjectState:
    """Test integration contract, not a substitute for the production gateway."""

    def __init__(self, document):
        self.lock = threading.Lock()
        self.document = document
        self.cache = {"old_mesh": [1, 2, 3]}
        self.history = []

    def commit(self, prepared):
        with self.lock:
            prepared.verify_integrity()
            if parametric.document_sha256(self.document) != prepared.base_document_sha256:
                raise parametric.ParametricError("project changed during rebuild", reason="stale_document")
            old_l1 = docmodel.document_to_l1(self.document)
            self.document, self.cache, self.history = (
                prepared.document, prepared.mesh, self.history + [old_l1])


def definition(expression, unit="length", positive=True):
    return {"expression": expression, "unit": unit, "positive": positive}


def parameters():
    return {
        "version": 1,
        "parameters": {
            "hole_x": definition("width / 2"),
            "hole_y": definition("height / 2"),
            "radius": definition("width / 10"),
            "depth": definition("width / 5"),
            "width": definition("40 * mm"),
            "height": definition("20 * mm"),
        },
        "configurations": {"wide": {"width": "60 * mm"}, "small": {"width": "30 * mm"}},
    }


def plate_document():
    doc = docmodel.new_document()
    plate_uuid = session.enter_sketch(doc, plane="XY")
    plate = doc.sketches[0]
    lines = primitives.add_rectangle(plate, p1_nm=(0, 0), p2_nm=(40 * MM, 20 * MM))
    for index in range(4):
        commands.add_constraint(plate, kind="coincident",
                                refs=[(lines[index], "end"), (lines[(index + 1) % 4], "start")])
    commands.add_constraint(plate, kind="coincident",
                            refs=[(lines[0], "start"), ("origin", "start")])
    width = commands.add_dimension(plate, kind="linear", refs=[(lines[0], "")], value_nm=40 * MM)
    height = commands.add_dimension(plate, kind="linear", refs=[(lines[1], "")], value_nm=20 * MM)
    boss = sw_features.extrude_boss(doc, sketch=plate_uuid, distance_nm=8 * MM)[0]
    hole_uuid = session.enter_sketch(doc, plane="XY")
    hole = doc.sketches[-1]
    circle = commands.add_circle(hole, center=(20 * MM, 10 * MM), radius_nm=4 * MM)
    vertical = commands.add_line(hole, p1=(0, 0), p2=(0, 20 * MM), fixed=True, construction=True)
    horizontal = commands.add_line(hole, p1=(0, 0), p2=(40 * MM, 0), fixed=True, construction=True)
    xdim = commands.add_dimension(hole, kind="linear",
                                  refs=[(circle, "center"), (vertical, "")], value_nm=20 * MM)
    ydim = commands.add_dimension(hole, kind="linear",
                                  refs=[(circle, "center"), (horizontal, "")], value_nm=10 * MM)
    radius = commands.add_dimension(hole, kind="radial", refs=[(circle, "")], value_nm=4 * MM)
    cut = sw_features.extrude_cut(doc, sketch=hole_uuid, distance_nm=8 * MM)[0]
    spec = parameters()
    spec["bindings"] = {}
    for key, sketch_uuid, target_uuid, expression in (
            ("plate_width", plate_uuid, width, "width"),
            ("plate_height", plate_uuid, height, "height"),
            ("hole_center_x", hole_uuid, xdim, "hole_x"),
            ("hole_center_y", hole_uuid, ydim, "hole_y"),
            ("hole_radius", hole_uuid, radius, "radius")):
        spec["bindings"][key] = {"kind": "dimension", "sketch_uuid": sketch_uuid,
                                 "target_uuid": target_uuid, "expression": expression}
    for key, target_uuid in (("boss_depth", boss), ("cut_depth", cut)):
        spec["bindings"][key] = {"kind": "feature", "target_uuid": target_uuid,
                                 "field": "distance_nm", "expression": "depth"}
    spec["checks"] = [
        {"key": "positive_clearance", "left": "radius * 2", "relation": "<", "right": "height"},
    ]
    spec["geometry_guards"] = {
        "interior_hole": {"kind": "circle_in_rectangle", "rectangle_sketch_uuid": plate_uuid,
                          "circle_sketch_uuid": hole_uuid, "circle_uuid": circle,
                          "clearance": "0 * mm"},
    }
    doc.extra["parametric"] = spec
    return doc


def configurable_plate_document():
    doc = plate_document()
    doc.extra["parametric"].update(
        version=2,
        feature_bindings={
            "boss": {"target_uuid": doc.features[0]["uuid"], "base_suppressed": False},
            "cut": {"target_uuid": doc.features[1]["uuid"], "base_suppressed": False}},
        configurations={"wide": {"width": "60 * mm"}, "solid": {},
                        "wide_solid": {"width": "60 * mm"}},
        configuration_suppressions={"wide": {}, "solid": {"cut": True},
                                    "wide_solid": {"cut": True}})
    return doc


def mesh_volume(mesh):
    terms = []
    incidence = {}
    vertices = mesh["vertices_mm"]
    for triangle in mesh["triangles"]:
        a, b, c = (vertices[index] for index in triangle)
        terms.append((a[0] * (b[1] * c[2] - b[2] * c[1])
                      + a[1] * (b[2] * c[0] - b[0] * c[2])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((tuple(round(x, 7) for x in vertices[triangle[i]]),
                                 tuple(round(x, 7) for x in vertices[triangle[j]]))))
            incidence[edge] = incidence.get(edge, 0) + 1
    return abs(math.fsum(terms)), incidence


class ParameterGraphTests(unittest.TestCase):
    def test_evidence_first_subtest_error_precedes_later_failure(self):
        class OrderedFailure(unittest.TestCase):
            def runTest(self):
                with self.subTest(stage="first"):
                    raise RuntimeError("FIRST_CAUSE_SENTINEL")
                with self.subTest(stage="later"):
                    self.fail("LATER_FAILURE_SENTINEL")

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=EvidenceResult).run(
            unittest.TestSuite([OrderedFailure()]))
        self.assertEqual((len(result.errors), len(result.failures)), (1, 1))
        self.assertIn("FIRST_CAUSE_SENTINEL", result.first_cause["traceback"])
        self.assertNotIn("LATER_FAILURE_SENTINEL", result.first_cause["traceback"])

    def reject(self, spec, reason):
        with self.assertRaises(parametric.ParametricError) as caught:
            parametric.evaluate_parameters(spec)
        self.assertEqual(caught.exception.reason, reason)

    def test_topological_order_and_exact_units(self):
        result = parametric.evaluate_parameters(parameters())
        self.assertLess(result.order.index("width"), result.order.index("hole_x"))
        self.assertEqual(result.values["hole_x"].value, 20 * MM)
        spec = parameters()
        spec["parameters"]["width"]["expression"] = "0.1 * mm + 1 * inch"
        result = parametric.evaluate_parameters(spec)
        self.assertEqual(result.values["width"].value, 25_500_000)

    def test_forward_dependency_and_scalar_ratios(self):
        spec = parameters()
        spec["parameters"]["ratio"] = definition("width / height", "scalar")
        self.assertEqual(parametric.evaluate_parameters(spec).values["ratio"].value, Fraction(2))

    def test_configurations_and_explicit_override(self):
        spec = parameters()
        frozen = copy.deepcopy(spec)
        result = parametric.evaluate_parameters(spec, configuration="wide", overrides={"width": "50 * mm"})
        self.assertEqual(result.values["width"].value, 50 * MM)
        self.assertEqual(spec, frozen)

    def test_cycle_and_self_cycle(self):
        for expression in ("hole_x * 2", "width"):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, "parameter_cycle")

    def test_cycle_created_by_configuration(self):
        with self.assertRaises(parametric.ParametricError) as caught:
            parametric.evaluate_parameters(parameters(), overrides={"width": "hole_x * 2"})
        self.assertEqual(caught.exception.reason, "parameter_cycle")

    def test_unknown_parameter_and_configuration(self):
        spec = parameters()
        spec["parameters"]["width"]["expression"] = "unknown * mm"
        self.reject(spec, "unknown_parameter")
        for kwargs in ({"configuration": "absent"}, {"overrides": {"absent": "1 * mm"}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(parametric.ParametricError):
                parametric.evaluate_parameters(parameters(), **kwargs)

    def test_negative_zero_and_subnanometre(self):
        for expression, reason in (("-1 * mm", "invalid_parameter"), ("0 * mm", "invalid_parameter"),
                                   ("nm / 3", "subnanometre_length")):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, reason)

    def test_unit_mismatch_and_missing_units(self):
        for expression in ("40", "40 * mm + 1", "mm * mm"):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, "unit_mismatch")

    def test_safe_expression_rejects_python_and_pow(self):
        for expression in ("__import__('os')", "width.real", "width[0]", "2 ** 100",
                           "[1, 2]", "True", "'40 mm'", "(x for x in [1])"):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, "unsafe_expression")

    def test_division_by_zero_nonfinite_and_range(self):
        for expression, reason in (("mm / 0", "division_by_zero"), ("1e309 * mm", "expression_range"),
                                   ("999999999999999999 * mm", "expression_range")):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, reason)

    def test_extreme_small_decimal_is_rejected_before_fraction_construction(self):
        for expression in ("1e-1000000000 * mm", "0e-1000000000 * mm", "1e-78 * mm"):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, "expression_range")

    def test_empty_reserved_names_and_version_rejected(self):
        for mutate in ("empty", "reserved", "version"):
            spec = parameters()
            if mutate == "empty":
                spec["parameters"] = {}
            elif mutate == "reserved":
                spec["parameters"]["mm"] = definition("1 * mm")
            else:
                spec["version"] = True
            with self.subTest(mutate=mutate), self.assertRaises(parametric.ParametricError):
                parametric.evaluate_parameters(spec)

    def test_bounded_expression_and_parameter_count(self):
        for expression in ("+" * 2100, "+".join(["1"] * 200)):
            spec = parameters()
            spec["parameters"]["width"]["expression"] = expression
            self.reject(spec, "unsafe_expression")
        spec = parameters()
        spec["parameters"] = {f"p{i}": definition("1 * mm") for i in range(129)}
        self.reject(spec, "bad_parametric_spec")

    def test_runtime_import_bound_to_this_checkout(self):
        self.assertEqual(Path(parametric.__file__).resolve(), CORE / "cadcore/parametric.py")
        self.assertEqual(Path(subproc.__file__).resolve(), CORE / "cadcore/kernel/subproc.py")

    def test_v1_remains_dimension_only_and_v2_is_explicit(self):
        self.assertEqual(parametric.evaluate_parameters(parameters()).configuration, None)
        doc = configurable_plate_document()
        result = parametric.evaluate_parameters(doc.extra["parametric"], configuration="solid")
        states = parametric.feature_configuration(doc, result)
        self.assertTrue(states["cut"]["suppressed"])
        self.assertFalse(states["boss"]["suppressed"])
        self.assertFalse(doc.features[1]["suppressed"])
        spec = parameters()
        spec["configuration_suppressions"] = {}
        self.reject(spec, "bad_parametric_spec")

    def test_v2_unknown_null_nonbool_and_duplicate_feature_maps(self):
        for mode, reason in (
                ("missing", "bad_parametric_spec"), ("null", "bad_parametric_spec"),
                ("nonbool", "bad_feature_configuration"), ("unknown", "unknown_feature_binding"),
                ("duplicate", "duplicate_binding"), ("configuration", "unknown_configuration"),
                ("baseline", "bad_feature_configuration")):
            spec = configurable_plate_document().extra["parametric"]
            if mode == "missing":
                del spec["feature_bindings"]
            elif mode == "null":
                spec["configuration_suppressions"] = None
            elif mode == "nonbool":
                spec["configuration_suppressions"]["solid"]["cut"] = 1
            elif mode == "unknown":
                spec["configuration_suppressions"]["solid"]["unknown"] = True
            elif mode == "duplicate":
                spec["feature_bindings"]["duplicate"] = dict(spec["feature_bindings"]["cut"])
            elif mode == "configuration":
                spec["configuration_suppressions"]["unknown"] = {}
            else:
                spec["feature_bindings"]["cut"]["base_suppressed"] = None
            with self.subTest(mode=mode):
                self.reject(spec, reason)

    def test_v2_suppressed_dependency_and_missing_uuid_are_loud(self):
        for mode in ("dependency", "missing", "forward", "stored_nonbool", "all_suppressed"):
            doc = configurable_plate_document()
            spec = doc.extra["parametric"]
            if mode == "dependency":
                spec["configuration_suppressions"]["solid"] = {"boss": True}
            elif mode == "missing":
                spec["feature_bindings"]["cut"]["target_uuid"] = str(uuid4())
            elif mode == "forward":
                doc.features[0]["inputs"].append(doc.features[1]["uuid"])
            elif mode == "stored_nonbool":
                doc.features[0]["suppressed"] = 0
            else:
                spec["configuration_suppressions"]["solid"] = {"boss": True, "cut": True}
            evaluation = parametric.evaluate_parameters(spec)
            before = docmodel.document_to_l1(doc)
            with self.subTest(mode=mode), self.assertRaises(parametric.ParametricError):
                parametric.feature_configuration(doc, evaluation)
            self.assertEqual(docmodel.document_to_l1(doc), before)

    def test_v2_does_not_guess_index_topology_after_suppression(self):
        doc = configurable_plate_document()
        doc.features.append({"uuid": str(uuid4()), "kind": "fillet",
                             "inputs": [doc.features[0]["uuid"]], "suppressed": False,
                             "params": {"size_nm": MM, "edge_indices": [1]}})
        evaluation = parametric.evaluate_parameters(doc.extra["parametric"])
        with self.assertRaises(parametric.ParametricError) as caught:
            parametric.feature_configuration(doc, evaluation)
        self.assertEqual(caught.exception.reason, "configuration_topology_unsupported")

    def test_v2_saved_state_mismatch_rejected_and_baseline_true_restored(self):
        doc = configurable_plate_document()
        doc.extra["parametric"]["active_configuration"] = "solid"
        evaluation = parametric.evaluate_parameters(doc.extra["parametric"])
        with self.assertRaises(parametric.ParametricError) as caught:
            parametric.feature_configuration(doc, evaluation, require_current=True)
        self.assertEqual(caught.exception.reason, "configuration_state_mismatch")
        doc.features[1]["suppressed"] = True
        spec = doc.extra["parametric"]
        spec["active_configuration"] = None
        spec["feature_bindings"]["cut"]["base_suppressed"] = True
        spec["configuration_suppressions"]["wide"] = {"cut": False}
        state = parametric.feature_configuration(doc, parametric.evaluate_parameters(spec),
                                                require_current=True)
        self.assertTrue(state["cut"]["suppressed"])


class RealParametricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = RUN_ROOT or EVIDENCE
        root.mkdir(parents=True, exist_ok=True)
        cls.work = Path(tempfile.mkdtemp(prefix="real-kernel-", dir=root))
        cls.kernel = subproc.SubprocessKernel(
            work_dir=cls.work, keep_work_dir=True, probe_timeout_s=15,
            extra_env={"TMPDIR": str(cls.work), "PYTHONDONTWRITEBYTECODE": "1"})
        prior = tempfile.tempdir
        tempfile.tempdir = str(cls.work)
        try:
            report = cls.kernel.probe_report()
        finally:
            tempfile.tempdir = prior
        if not report.available:
            raise AssertionError("FreeCAD is required, not skipped: " + repr(report.as_dict()))
        MEASUREMENTS["probe"] = report.as_dict()

    @classmethod
    def tearDownClass(cls):
        MEASUREMENTS["kernel_stats"] = cls.kernel.stats()
        cls.kernel.close()

    def prepare(self, doc, **kwargs):
        return parametric.prepare_rebuild(doc, kernel=self.kernel, **kwargs)

    def assert_atomic_failure(self, doc, reason, **kwargs):
        cache, history = {"old_mesh": [1, 2, 3]}, [{"old_state": "retained"}]
        original = docmodel.document_to_l1(doc)
        snapshot = copy.deepcopy((cache, history))
        holder = {"document": doc, "cache": cache, "history": history}
        with self.assertRaises(parametric.ParametricError) as caught:
            result = self.prepare(holder["document"], **kwargs)
            result.verify_integrity()
            holder.update(document=result.document, cache=result.mesh)
            holder["history"].append(original)
        self.assertEqual(caught.exception.reason, reason)
        self.assertIs(holder["document"], doc)
        self.assertEqual(docmodel.document_to_l1(doc), original)
        self.assertEqual((holder["cache"], holder["history"]), snapshot)
        return caught.exception

    def verify_plate(self, result, width, height, radius, depth):
        result.verify_integrity()
        expected = (width * height - math.pi * radius**2) * depth
        props = self.kernel.mass_properties(result.shape)
        actual = props.volume_nm3 / 1e18
        self.assertAlmostEqual(actual, expected, delta=1e-5)
        self.assertEqual(props.bbox_nm, (0, 0, 0, width * MM, height * MM, depth * MM))
        self.assertEqual(result.mesh["properties"]["volume_nm3"], props.volume_nm3)
        self.assertEqual(result.mesh["document_sha256"], parametric.document_sha256(result.document))
        self.assertEqual(result.mesh["solid_count"], 1)
        integrated, incidence = mesh_volume(result.mesh)
        self.assertAlmostEqual(integrated, expected, delta=expected * 0.005)
        self.assertTrue(incidence)
        self.assertTrue(all(count == 2 for count in incidence.values()))
        circle = next(e for e in result.document.sketches[1]["entities"] if e["kind"] == "circle")
        self.assertEqual(circle["geom"], {"cx": width * MM // 2, "cy": height * MM // 2, "r": radius * MM})
        self.assertTrue(all(report["converged"] and report["dof"] == 0
                            and report["residual_nm"] <= 1 for report in result.sketch_reports))
        MEASUREMENTS[str(width)] = {
            "document_sha256": result.document_sha256, "brep_volume_mm3": actual,
            "analytic_volume_mm3": expected, "mesh_volume_mm3": integrated,
            "triangles": len(result.mesh["triangles"]), "sketch_reports": result.sketch_reports,
            "feature_order": result.feature_order,
        }

    def test_real_plate_40_to_60_and_downstream_hole_extrusion(self):
        doc = plate_document()
        original = docmodel.document_to_l1(doc)
        first = self.prepare(doc)
        self.verify_plate(first, 40, 20, 4, 8)
        changed = self.prepare(first.document, configuration="wide")
        self.verify_plate(changed, 60, 20, 6, 12)
        self.assertNotEqual(first.document_sha256, changed.document_sha256)
        self.assertEqual(changed.base_document_sha256, first.document_sha256)
        self.assertEqual(docmodel.document_to_l1(doc), original)
        self.assertEqual(first.document.features[0]["params"]["distance_nm"], 8 * MM)
        self.assertEqual(changed.document.features[1]["params"]["distance_nm"], 12 * MM)
        self.assertEqual(first.feature_order, changed.feature_order)

    def test_project_snapshot_cas_success_and_late_edit_rejection(self):
        state = ProjectState(plate_document())
        with state.lock:
            snapshot = copy.deepcopy(state.document)
        first = self.prepare(snapshot)
        state.commit(first)
        self.assertIs(state.document, first.document)
        self.assertIs(state.cache, first.mesh)
        self.assertEqual(state.history, [docmodel.document_to_l1(snapshot)])
        with state.lock:
            snapshot = copy.deepcopy(state.document)
        pending = self.prepare(snapshot, configuration="wide")
        with state.lock:
            state.document.meta["x_late_edit"] = "retained"
            before = docmodel.document_to_l1(state.document)
            cache, history = state.cache, copy.deepcopy(state.history)
        with self.assertRaises(parametric.ParametricError) as caught:
            state.commit(pending)
        self.assertEqual(caught.exception.reason, "stale_document")
        self.assertEqual(docmodel.document_to_l1(state.document), before)
        self.assertIs(state.cache, cache)
        self.assertEqual(state.history, history)

    def test_configuration_reset_and_persistent_overrides(self):
        result = self.prepare(plate_document(), configuration="wide", overrides={"width": "50 * mm"})
        self.assertEqual(result.document.features[0]["params"]["distance_nm"], 10 * MM)
        rebuilt = self.prepare(docmodel.document_from_l1(result.l1))
        self.assertEqual(rebuilt.document_sha256, result.document_sha256)
        reset = self.prepare(result.document, configuration="")
        self.assertEqual(reset.parameters.values["width"].value, 40 * MM)
        self.assertEqual(reset.parameters.overrides, {})
        self.assertEqual(reset.document.features[1]["params"]["distance_nm"], 8 * MM)

    def test_step_roundtrip_rebuild_and_exact_document_persistence(self):
        result = self.prepare(plate_document(), configuration="wide")
        path = self.work / ("plate-" + result.document_sha256 + ".step")
        self.kernel.export(result.shape, fmt="step", path=str(path))
        text = path.read_text(encoding="ascii")
        self.assertGreater(path.stat().st_size, 1000)
        imported = docmodel.new_document()
        imported.features.append({"uuid": str(uuid4()), "kind": "import_step",
                                  "params": {"step_text": text}, "suppressed": False})
        imported_shape = self.kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
        props = self.kernel.mass_properties(imported_shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18,
                               result.mesh["properties"]["volume_nm3"] / 1e18, delta=1e-5)
        imported_mesh = self.kernel.tessellate(imported_shape)
        integrated, incidence = mesh_volume(imported_mesh)
        self.assertTrue(all(count == 2 for count in incidence.values()))
        self.assertAlmostEqual(integrated, props.volume_nm3 / 1e18, delta=props.volume_nm3 / 1e18 * 0.005)
        reopened = docmodel.document_from_l1(result.l1)
        self.assertEqual(docmodel.document_to_l1(reopened), result.l1)
        rebuilt = self.prepare(reopened)
        if RUN_ROOT is not None:
            (RUN_ROOT / "plate-candidate.l1").write_text(result.l1, encoding="utf-8")
            (RUN_ROOT / "plate-rebuilt.l1").write_text(rebuilt.l1, encoding="utf-8")
        self.assertEqual(rebuilt.document_sha256, result.document_sha256)
        MEASUREMENTS["step"] = {"path": str(path), "bytes": path.stat().st_size,
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "source_document_sha256": result.document_sha256,
                                 "roundtrip_volume_nm3": props.volume_nm3}

    def test_negative_and_cycle_keep_doc_cache_history(self):
        for override, reason in ({"width": "-1 * mm"}, "invalid_parameter"), (
                {"width": "hole_x * 2"}, "parameter_cycle"):
            self.assert_atomic_failure(plate_document(), reason, overrides=override)

    def test_hole_outside_rejected_on_solved_geometry(self):
        self.assert_atomic_failure(plate_document(), "hole_outside_profile", overrides={"hole_x": "80 * mm"})

    def test_design_check_failure_is_atomic(self):
        self.assert_atomic_failure(plate_document(), "design_check_failed", overrides={"radius": "11 * mm"})

    def test_solver_failure_is_atomic(self):
        doc = plate_document()
        width_binding = doc.extra["parametric"]["bindings"]["plate_width"]
        target = next(d for d in doc.sketches[0]["dimensions"] if d["uuid"] == width_binding["target_uuid"])
        commands.add_dimension(doc.sketches[0], kind="linear", refs=target["refs"], value_nm=10 * MM)
        self.assert_atomic_failure(doc, "sketch_not_converged", configuration="wide")

    def test_real_brep_failure_preserves_first_cause_and_state(self):
        doc = plate_document()
        doc.features.append({"uuid": str(uuid4()), "kind": "fillet", "inputs": [doc.features[-1]["uuid"]],
                             "params": {"size_nm": 1000 * MM, "edge_indices": [1]}, "suppressed": False})
        exc = self.assert_atomic_failure(doc, "brep_rebuild_failed", configuration="wide")
        self.assertIsNotNone(exc.__cause__)
        self.assertIn("fillet", str(exc).lower())
        MEASUREMENTS["brep_failure"] = {"reason": exc.reason, "detail": str(exc),
                                        "cause": str(exc.__cause__)}

    def test_unknown_duplicate_and_reference_dimension_bindings_rejected(self):
        for mode, reason in (("missing", "unknown_binding_target"), ("duplicate", "duplicate_binding"),
                             ("reference", None)):
            doc = plate_document()
            binding = doc.extra["parametric"]["bindings"]["plate_width"]
            if mode == "missing":
                binding["target_uuid"] = str(uuid4())
            elif mode == "duplicate":
                doc.extra["parametric"]["bindings"]["second_width"] = copy.deepcopy(binding)
            else:
                target = next(d for d in doc.sketches[0]["dimensions"] if d["uuid"] == binding["target_uuid"])
                target["value_nm"] = None
            if reason:
                self.assert_atomic_failure(doc, reason)
            else:
                before = docmodel.document_to_l1(doc)
                with self.assertRaises(constraints.SketchError):
                    self.prepare(doc)
                self.assertEqual(docmodel.document_to_l1(doc), before)

    def test_candidate_tamper_and_stale_gateway_cas(self):
        result = self.prepare(plate_document())
        result.mesh["vertices_mm"][0][0] += 1
        with self.assertRaises(parametric.ParametricError) as caught:
            result.verify_integrity()
        self.assertEqual(caught.exception.reason, "candidate_changed")
        other = plate_document()
        self.assertNotEqual(result.base_document_sha256, parametric.document_sha256(other))

    def test_document_and_recipe_tamper_rejected(self):
        result = self.prepare(plate_document())
        tampered = copy.deepcopy(result)
        tampered.document.features[0]["params"]["distance_nm"] += MM
        with self.assertRaises(parametric.ParametricError):
            tampered.verify_integrity()
        result.recipe["recipe"][0]["op"] = "tampered"
        with self.assertRaises(parametric.ParametricError):
            result.verify_integrity()

    def test_non_real_kernel_and_non_document_rejected(self):
        for document, kernel in (({}, self.kernel), (plate_document(), object())):
            with self.subTest(document=type(document).__name__), self.assertRaises(parametric.ParametricError):
                parametric.prepare_rebuild(document, kernel=kernel)

    def test_real_suppression_switch_mesh_step_reopen_and_base_restore(self):
        doc = configurable_plate_document()
        original = docmodel.document_to_l1(doc)
        pierced = self.prepare(doc)
        self.verify_plate(pierced, 40, 20, 4, 8)
        records = []
        current = pierced
        for configuration, width, depth, has_hole in (
                ("solid", 40, 8, False), ("wide_solid", 60, 12, False),
                ("wide", 60, 12, True), ("", 40, 8, True)):
            result = self.prepare(current.document, configuration=configuration)
            expected = (width * 20 - (math.pi * (width / 10)**2 if has_hole else 0)) * depth
            props = self.kernel.mass_properties(result.shape)
            self.assertAlmostEqual(props.volume_nm3 / 1e18, expected, delta=1e-5)
            integrated, incidence = mesh_volume(result.mesh)
            self.assertAlmostEqual(integrated, expected, delta=expected * .005)
            self.assertTrue(incidence and all(count == 2 for count in incidence.values()))
            self.assertEqual([f["uuid"] for f in result.document.features],
                             [f["uuid"] for f in doc.features])
            self.assertEqual(result.feature_order, tuple(f["uuid"] for f in doc.features
                                                        if has_hole or f is doc.features[0]))
            self.assertEqual([f["params"]["distance_nm"] for f in result.document.features],
                             [depth * MM, depth * MM])
            self.assertTrue(all(r["converged"] and r["dof"] == 0 and r["residual_nm"] <= 1
                                for r in result.sketch_reports))
            path = self.work / ("configuration-" + (configuration or "base") + ".step")
            self.kernel.export(result.shape, fmt="step", path=str(path))
            imported = docmodel.new_document()
            imported.features.append({"uuid": str(uuid4()), "kind": "import_step",
                                      "params": {"step_text": path.read_text(encoding="ascii")},
                                      "suppressed": False})
            shape = self.kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
            roundtrip = self.kernel.mass_properties(shape).volume_nm3 / 1e18
            self.assertAlmostEqual(roundtrip, expected, delta=1e-5)
            saved = self.work / ("configuration-" + (configuration or "base") + ".l1")
            docmodel.save_l1(result.document, str(saved))
            reopened = self.prepare(docmodel.load_l1(str(saved)))
            self.assertEqual(reopened.l1, result.l1)
            self.assertEqual(reopened.document_sha256, result.document_sha256)
            records.append({"configuration": configuration, "analytic_volume_mm3": expected,
                            "brep_volume_mm3": props.volume_nm3 / 1e18,
                            "mesh_volume_mm3": integrated, "step_volume_mm3": roundtrip,
                            "step_path": str(path), "step_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "document_sha256": result.document_sha256,
                            "recipe_sha256": result.mesh["recipe_sha256"],
                            "mesh_sha256": result.mesh["mesh_sha256"],
                            "feature_order": result.feature_order, "sketch_reports": result.sketch_reports})
            current = result
        self.assertEqual(current.l1, pierced.l1)
        self.assertEqual(docmodel.document_to_l1(doc), original)
        MEASUREMENTS["configurations"] = records

    def test_real_configuration_failure_and_cas_preserve_state(self):
        doc = configurable_plate_document()
        doc.extra["parametric"]["configuration_suppressions"]["solid"] = {"boss": True}
        self.assert_atomic_failure(doc, "configuration_dependency_invalid", configuration="solid")
        state = ProjectState(configurable_plate_document())
        candidate = self.prepare(state.document, configuration="solid")
        state.document.meta["x_late_edit"] = "retained"
        before = docmodel.document_to_l1(state.document)
        with self.assertRaises(parametric.ParametricError):
            state.commit(candidate)
        self.assertEqual(docmodel.document_to_l1(state.document), before)
        self.assertEqual(state.cache, {"old_mesh": [1, 2, 3]})
        self.assertEqual(state.history, [])

    def test_real_true_baseline_is_restored_after_enabled_configuration(self):
        doc = configurable_plate_document()
        spec = doc.extra["parametric"]
        spec["feature_bindings"]["cut"]["base_suppressed"] = True
        spec["configuration_suppressions"]["wide"] = {"cut": False}
        doc.features[1]["suppressed"] = True
        base = self.prepare(doc)
        self.assertAlmostEqual(base.mesh["properties"]["volume_nm3"] / 1e18, 6400, delta=1e-5)
        enabled = self.prepare(base.document, configuration="wide")
        self.verify_plate(enabled, 60, 20, 6, 12)
        restored = self.prepare(enabled.document, configuration="")
        self.assertEqual(restored.l1, base.l1)
        self.assertTrue(restored.document.features[1]["suppressed"])
        self.assertAlmostEqual(restored.mesh["properties"]["volume_nm3"] / 1e18, 6400, delta=1e-5)

    def test_real_live_downstream_mirror_rebuilds_after_suppression(self):
        doc = configurable_plate_document()
        mirror = str(uuid4())
        doc.features.append({
            "uuid": mirror, "kind": "mirror", "inputs": [doc.features[0]["uuid"]],
            "suppressed": False,
            "params": {"normal_axis": "x", "offset_nm": 80 * MM, "keep_original": False}})
        doc.extra["parametric"]["bindings"]["mirror_offset"] = {
            "kind": "feature", "target_uuid": mirror, "field": "offset_nm",
            "expression": "width * 2"}
        current = self.prepare(doc)
        records = []
        for configuration, width, depth, has_hole in (
                ("wide_solid", 60, 12, False), ("wide", 60, 12, True),
                ("", 40, 8, True)):
            result = self.prepare(current.document, configuration=configuration)
            expected = (width * 20 - (math.pi * (width / 10)**2 if has_hole else 0)) * depth
            properties = self.kernel.mass_properties(result.shape)
            self.assertAlmostEqual(properties.volume_nm3 / 1e18, expected, delta=1e-5)
            self.assertEqual(properties.bbox_nm,
                             (3 * width * MM, 0, 0, 4 * width * MM, 20 * MM, depth * MM))
            self.assertEqual(result.document.features[-1]["params"]["offset_nm"], 2 * width * MM)
            self.assertEqual(result.feature_order[-1], mirror)
            self.assertEqual(result.recipe["recipe"][-1]["op"], "mirror")
            volume, incidence = mesh_volume(result.mesh)
            self.assertAlmostEqual(volume, expected, delta=expected * .005)
            self.assertTrue(incidence and all(count == 2 for count in incidence.values()))
            path = self.work / ("downstream-" + (configuration or "base") + ".step")
            self.kernel.export(result.shape, fmt="step", path=str(path))
            imported = docmodel.new_document()
            imported.features.append({
                "uuid": str(uuid4()), "kind": "import_step", "suppressed": False,
                "params": {"step_text": path.read_text(encoding="ascii")}})
            shape = self.kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
            readback = self.kernel.mass_properties(shape)
            self.assertAlmostEqual(readback.volume_nm3 / 1e18, expected, delta=1e-5)
            self.assertEqual(readback.bbox_nm, properties.bbox_nm)
            records.append({"configuration": configuration, "document_sha256": result.document_sha256,
                            "brep_volume_mm3": properties.volume_nm3 / 1e18,
                            "step_volume_mm3": readback.volume_nm3 / 1e18,
                            "bbox_nm": properties.bbox_nm, "feature_order": result.feature_order,
                            "step_path": str(path),
                            "step_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            current = result
        MEASUREMENTS["downstream_mirror"] = records


def source_snapshot(repository):
    """Bind full source bytes/modes and Git metadata, not just the edited module."""
    tools = repository / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import acceptance
    manifest = acceptance.source_manifest(repository)
    owned = (
        "oss-alternatives/packages/cadcore/cadcore/parametric.py",
        "oss-alternatives/packages/cadcore/tests/test_parametric.py",
        "oss-alternatives/apps/opencad/industrial_parametric.py",
        "oss-alternatives/apps/opencad/tests/test_industrial_parametric.py",
        "docs/design/parametric-configurations.md")
    return {"root": str(repository), "manifest": manifest,
            "owned_sources": {name: manifest[name] for name in owned},
            "modes": {name: (repository / name).stat().st_mode & 0o777 for name in manifest},
            "head": subprocess.check_output(["git", "--no-replace-objects", "-C", str(repository),
                                            "rev-parse", "HEAD"],
                                            text=True).strip(),
            "branch": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(repository), "branch", "--show-current"],
                text=True).strip(),
            "status": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(repository), "status", "--porcelain=v1"],
                text=True),
            "sha256": acceptance.bootstrap.digest(manifest)}


def main():
    global RUN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    parser.add_argument("--unit-only", action="store_true")
    parser.add_argument("--acceptance-smoke", action="store_true",
                        help="also run this checkout's independent acceptance smoke")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_ROOT = args.evidence_dir / stamp
    RUN_ROOT.mkdir(parents=True, exist_ok=False)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    repository = CORE.parents[2]

    def sources_now():
        paths = list(CORE.rglob("*.py")) + [repository / "docs/design/parametric-rebuild.md",
                                          repository / "docs/design/parametric-configurations.md"]
        return {str(path.relative_to(repository)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths if "__pycache__" not in path.parts and path.is_file()}

    sources = sources_now()
    frozen = source_snapshot(repository)
    started_at = datetime.now(timezone.utc).isoformat()
    classes = (ParameterGraphTests,) if args.unit_only else (ParameterGraphTests, RealParametricTests)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes)
    with (RUN_ROOT / "tests.log").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=EvidenceResult).run(suite)
    acceptance = None
    if args.acceptance_smoke and result.wasSuccessful() and not result.skipped:
        with (RUN_ROOT / "acceptance-console.log").open("x", encoding="utf-8") as log:
            child = subprocess.run([sys.executable, "-B", str(repository / "tools/acceptance.py"),
                                    "--stage", "smoke", "--out", str(RUN_ROOT / "acceptance-smoke")],
                                   cwd=repository, stdout=log, stderr=subprocess.STDOUT, check=False)
        report = json.loads((RUN_ROOT / "acceptance-smoke/result.json").read_text(encoding="utf-8"))
        acceptance = {"rc": child.returncode, "verdict": report["verdict"],
                      "source_drift": report.get("source_drift"), "path": str(RUN_ROOT / "acceptance-smoke")}
    after = sources_now()
    final_source = source_snapshot(repository)
    parametric_verdict = ("PASS" if result.wasSuccessful() and sources == after
                         and frozen == final_source and result.testsRun and not result.skipped else "FAIL")
    first_cause = result.first_cause
    if first_cause is None and parametric_verdict != "PASS":
        first_cause = {"reason": "source_drift" if frozen != final_source or sources != after
                       else "empty_or_skipped_suite"}
    summary = {"verdict": parametric_verdict, "parametric_verdict": parametric_verdict,
               "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
               "skipped": len(result.skipped), "source_drift": sources != after,
               "source_before": frozen, "source_after": final_source,
               "source_snapshot_drift": frozen != final_source,
               "started_at": started_at, "first_cause": first_cause,
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "source_root": str(CORE), "sources": sources, "measurements": MEASUREMENTS,
               "unit_only": args.unit_only, "acceptance_smoke": acceptance}
    if acceptance is not None and (acceptance["rc"] or acceptance["verdict"] != "PASS"
                                   or acceptance["source_drift"] != []):
        summary["verdict"] = "FAIL"
        if summary["first_cause"] is None:
            summary["first_cause"] = {"reason": "acceptance_smoke_failed", **acceptance}
    summary["actual_rc"] = 0 if summary["verdict"] == "PASS" else 1
    (RUN_ROOT / "result.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PARAMETRIC_REBUILD verdict={summary['verdict']} tests={result.testsRun} "
          f"failures={summary['failures']} errors={summary['errors']} evidence={RUN_ROOT}")
    return summary["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
