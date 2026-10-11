"""Real Workspace candidate, isolated HTTP persistence and browser panel tests.

Standalone runs retain logs and exact source hashes under 3FS. Discovery runs
the API suite; --browser additionally measures real input/clicks and rendering.
The HTTP fixture models CAS and receipts, not production authentication/gateway.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
CORE = ROOT / "oss-alternatives/packages/cadcore"
for directory in (APP, CORE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import server
from cadcore import docmodel, parametric
from cadcore.errors import CadError
from cadcore.kernel import subproc
from cadcore.kernel.features_step import make_step
from cadcore.sketch import commands
import industrial_parametric as industrial

EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-web-parametric")
RUN_ROOT = None
BROWSER = None
MEASUREMENTS = {}
MM = 1_000_000


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


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def new_workspace(root):
    root.mkdir(parents=True, exist_ok=True)
    return server.Workspace(server._load_cadcore(), locale="zh-CN", standard="gbt",
                            out_root=str(root))


def snapshot(workspace):
    return (workspace.l1_text(), copy.deepcopy(workspace.undo_stack),
            copy.deepcopy(workspace.redo_stack), copy.deepcopy(
                getattr(workspace, "_mesh_cache", None)), workspace.standard,
            workspace.catalog.active_locale())


def body(workspace, action="rebuild", **kwargs):
    return {"action": action, "expected_document_sha256": digest(workspace.l1_text()), **kwargs}


def install(workspace, candidate):
    """Test-only gateway boundary; production storage/CAS is chair-owned."""
    with workspace._lock:
        candidate.verify_integrity()
        if getattr(workspace, "_release_pending", False):
            raise server.ApiError("maintenance", status=409)
        before = workspace.l1_text()
        if digest(before) != candidate.before_document_sha256:
            raise server.ApiError("parametric_conflict", "Test CAS rejected a late edit", status=409)
        document = docmodel.document_from_l1(candidate.doc)
        problems = docmodel.load_blocking_problems(document)
        if problems:
            raise AssertionError("Fixture cannot install invalid L1: " + repr(problems))
        undo = workspace.undo_stack + [before] if candidate.doc != before else workspace.undo_stack
        workspace.doc, workspace.undo_stack, workspace.redo_stack, workspace._mesh_cache = (
            document, undo, [] if candidate.doc != before else workspace.redo_stack,
            copy.deepcopy(candidate.response["mesh"]))


def integrated_volume(mesh):
    vertices = mesh["vertices_mm"]
    terms, incidence = [], {}
    for triangle in mesh["triangles"]:
        a, b, c = (vertices[index] for index in triangle)
        terms.append((a[0] * (b[1] * c[2] - b[2] * c[1])
                      + a[1] * (b[2] * c[0] - b[0] * c[2])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((tuple(round(v, 7) for v in vertices[triangle[i]]),
                                 tuple(round(v, 7) for v in vertices[triangle[j]]))))
            incidence[edge] = incidence.get(edge, 0) + 1
    return abs(math.fsum(terms)), incidence


class CandidateSealTests(unittest.TestCase):
    """Protocol-only mesh fixture; these tests do not measure geometry."""

    def setUp(self):
        self.document = industrial.make_plate_document(docmodel.new_document())
        self.text = docmodel.document_to_l1(
            docmodel.document_from_l1(docmodel.document_to_l1(self.document)))
        self.base = digest(docmodel.document_to_l1(docmodel.new_document()))
        measured = {"document_sha256": digest(self.text), "vertices_mm": [[0, 0, 0]],
                    "triangles": [], "solid_count": 1}
        measured["mesh_sha256"] = industrial._fingerprint(measured)
        self.response = {
            "doc": self.text, "document_sha256": digest(self.text),
            "base_document_sha256": self.base,
            "mesh": {"document_sha256": digest(self.text), "solids": [measured]},
            "step": {"document_sha256": digest(self.text), "sha256": digest(b"protocol STEP")},
            "metadata": {"scope": "protocol fixture, not measured BRep"}}
        self.candidate = industrial.ParametricCandidate(self.base, self.text, self.response)

    def assert_changed(self, candidate):
        with self.assertRaises(parametric.ParametricError) as caught:
            candidate.verify_integrity()
        self.assertEqual(caught.exception.reason, "candidate_changed")

    def rebound_response(self, text):
        response = copy.deepcopy(self.response)
        response.update(doc=text, document_sha256=digest(text))
        response["step"]["document_sha256"] = digest(text)
        response["mesh"]["document_sha256"] = digest(text)
        measured = response["mesh"]["solids"][0]
        measured["document_sha256"] = digest(text)
        measured["mesh_sha256"] = industrial._fingerprint({
            key: value for key, value in measured.items() if key != "mesh_sha256"})
        return response

    def test_this_checkout_and_valid_candidate_copy_replace(self):
        self.assertEqual(Path(industrial.__file__).resolve(), (APP / "industrial_parametric.py").resolve())
        for candidate in (self.candidate, copy.copy(self.candidate),
                          copy.deepcopy(self.candidate), replace(self.candidate),
                          replace(self.candidate, response=copy.deepcopy(self.response))):
            candidate.verify_integrity()
        self.assertEqual(docmodel.document_to_l1(docmodel.document_from_l1(self.text)), self.text)

    def test_creation_envelope_has_init_true_optional_seal(self):
        seal = next((row for row in fields(industrial.ParametricCandidate)
                     if row.name == "_envelope_sha256"), None)
        self.assertIsNotNone(seal)
        self.assertTrue(seal.init)
        self.assertIsNone(seal.default)
        expected = industrial._fingerprint({
            "before_document_sha256": self.base, "doc": self.text, "response": self.response})
        self.assertEqual(self.candidate._envelope_sha256, expected)
        self.assertEqual(replace(self.candidate)._envelope_sha256, expected)

    def test_replace_response_rehash_is_rejected(self):
        response = copy.deepcopy(self.response)
        response["step"]["sha256"] = digest(b"altered STEP")
        self.assert_changed(replace(self.candidate, response=response))

    def test_replace_doc_with_all_artifact_bindings_rehashed_is_rejected(self):
        document = docmodel.document_from_l1(self.text)
        document.meta["project_label"] = "edited document"
        text = docmodel.document_to_l1(document)
        self.assert_changed(replace(self.candidate, doc=text, response=self.rebound_response(text)))

    def test_replace_base_with_response_binding_is_rejected(self):
        base = digest("different base")
        response = copy.deepcopy(self.response)
        response["base_document_sha256"] = base
        self.assert_changed(replace(self.candidate, before_document_sha256=base, response=response))

    def test_nested_step_mutation_is_rejected(self):
        self.candidate.response["step"]["sha256"] = digest(b"nested changed STEP")
        self.assert_changed(self.candidate)

    def test_nested_mesh_self_rehash_is_rejected(self):
        measured = self.candidate.response["mesh"]["solids"][0]
        measured["vertices_mm"][0][0] = 1
        measured["mesh_sha256"] = industrial._fingerprint({
            key: value for key, value in measured.items() if key != "mesh_sha256"})
        self.assert_changed(self.candidate)

    def test_nested_metadata_mutation_is_rejected(self):
        self.candidate.response["metadata"]["scope"] = "misrepresented scope"
        self.assert_changed(self.candidate)

    def test_equivalent_response_key_order_keeps_envelope(self):
        response = dict(reversed(list(self.response.items())))
        replace(self.candidate, response=response).verify_integrity()

    def test_new_candidate_noncanonical_doc_is_rejected(self):
        text = json.dumps(json.loads(self.text), separators=(",", ":"))
        self.assert_changed(industrial.ParametricCandidate(
            self.base, text, self.rebound_response(text)))

    def test_new_candidate_invalid_base_digest_is_rejected(self):
        response = copy.deepcopy(self.response)
        response["base_document_sha256"] = "not a digest"
        self.assert_changed(industrial.ParametricCandidate("not a digest", self.text, response))

    def test_new_candidate_artifact_document_mismatch_is_rejected(self):
        response = copy.deepcopy(self.response)
        response["step"]["document_sha256"] = digest("another document")
        self.assert_changed(industrial.ParametricCandidate(self.base, self.text, response))

    def test_malformed_sealed_response_is_rejected_without_attribute_error(self):
        malformed = [[]]
        for key, value in (("mesh", []), ("step", [])):
            response = copy.deepcopy(self.response)
            response[key] = value
            malformed.append(response)
        for solids in (None, {}, [], [None]):
            response = copy.deepcopy(self.response)
            response["mesh"]["solids"] = solids
            malformed.append(response)
        for response in malformed:
            with self.subTest(response=response):
                self.assert_changed(industrial.ParametricCandidate(self.base, self.text, response))


class DocumentValidationTests(unittest.TestCase):
    def setUp(self):
        self.document = industrial.make_plate_document(docmodel.new_document())

    def assert_invalid(self, edit, reason):
        edit(self.document.extra["parametric"])
        before = docmodel.document_to_l1(self.document)
        with self.assertRaises(CadError) as caught:
            industrial.validate_document(self.document)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(docmodel.document_to_l1(self.document), before)

    def test_valid_real_document_schema_and_active_configuration_read_only(self):
        for configuration in (None, "wide"):
            with self.subTest(configuration=configuration):
                self.document.extra["parametric"]["active_configuration"] = configuration
                before = docmodel.document_to_l1(self.document)
                with patch.object(industrial, "SubprocessKernel",
                                  side_effect=AssertionError("schema validator started geometry")), \
                        patch.object(parametric, "solve",
                                     side_effect=AssertionError("schema validator started solver")):
                    self.assertIsNone(industrial.validate_document(self.document))
                self.assertEqual(docmodel.document_to_l1(self.document), before)

    def test_plain_document_has_no_parametric_namespace(self):
        self.assertIsNone(industrial.validate_document(docmodel.new_document()))

    def test_document_type_and_null_namespace_rejected(self):
        with self.assertRaises(parametric.ParametricError) as caught:
            industrial.validate_document(json.loads(docmodel.document_to_l1(self.document)))
        self.assertEqual(caught.exception.reason, "bad_document")
        self.document.extra["parametric"] = None
        with self.assertRaises(parametric.ParametricError) as caught:
            industrial.validate_document(self.document)
        self.assertEqual(caught.exception.reason, "bad_parametric_spec")

    def test_unknown_schema_field_rejected(self):
        self.assert_invalid(lambda spec: spec.update(unknown=1), "bad_parametric_spec")

    def test_cycle_rejected(self):
        self.assert_invalid(lambda spec: spec["parameters"]["width"].update(expression="hole_x * 2"),
                            "parameter_cycle")

    def test_unsafe_expression_rejected(self):
        self.assert_invalid(lambda spec: spec["parameters"]["width"].update(expression="open('x')"),
                            "unsafe_expression")

    def test_negative_and_wrong_units_rejected(self):
        for expression, reason in (("-1 * mm", "invalid_parameter"), ("1", "unit_mismatch")):
            with self.subTest(expression=expression):
                self.document = industrial.make_plate_document(docmodel.new_document())
                self.assert_invalid(lambda spec: spec["parameters"]["width"].update(expression=expression),
                                    reason)

    def test_missing_dimension_binding_target_rejected(self):
        self.assert_invalid(lambda spec: spec["bindings"]["plate_width"].update(target_uuid=str(uuid4())),
                            "unknown_binding_target")

    def test_duplicate_binding_target_rejected(self):
        self.assert_invalid(lambda spec: spec["bindings"].update(
            duplicate=copy.deepcopy(spec["bindings"]["plate_width"])), "duplicate_binding")

    def test_feature_binding_field_rejected(self):
        self.assert_invalid(lambda spec: spec["bindings"]["boss_depth"].update(field="op"),
                            "unsupported_binding")

    def test_unknown_binding_expression_rejected(self):
        self.assert_invalid(lambda spec: spec["bindings"]["boss_depth"].update(expression="missing"),
                            "unknown_parameter")

    def test_check_rejected_before_any_geometry(self):
        self.assert_invalid(lambda spec: spec["parameters"]["radius"].update(expression="11 * mm"),
                            "design_check_failed")

    def test_guard_unknown_target_and_schema_rejected(self):
        for changes, reason in (({"circle_uuid": str(uuid4())}, "unknown_binding_target"),
                                ({"unknown": True}, "bad_parametric_spec")):
            with self.subTest(changes=changes):
                self.document = industrial.make_plate_document(docmodel.new_document())
                self.assert_invalid(lambda spec: spec["geometry_guards"]["interior_hole"].update(changes),
                                    reason)

    def test_reference_dimension_binding_rejected_without_mutation(self):
        binding = self.document.extra["parametric"]["bindings"]["plate_width"]
        sketch = next(row for row in self.document.sketches if row["uuid"] == binding["sketch_uuid"])
        dimension = next(row for row in sketch["dimensions"] if row["uuid"] == binding["target_uuid"])
        dimension["value_nm"] = None
        before = docmodel.document_to_l1(self.document)
        with self.assertRaises(parametric.ParametricError) as caught:
            industrial.validate_document(self.document)
        self.assertEqual(caught.exception.reason, "unsupported_binding")
        self.assertIsInstance(caught.exception.__cause__, industrial.SketchError)
        self.assertEqual(docmodel.document_to_l1(self.document), before)


class ConfigurationValidationTests(unittest.TestCase):
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

    def setUp(self):
        self.document = industrial.make_configurable_plate_document(docmodel.new_document())

    def test_v1_factory_unchanged_and_v2_factory_strict(self):
        self.assertEqual(industrial.make_plate_document(docmodel.new_document()).extra["parametric"]["version"], 1)
        self.assertEqual(self.document.extra["parametric"]["version"], 2)
        before = docmodel.document_to_l1(self.document)
        with patch.object(parametric, "solve", side_effect=AssertionError("unexpected solve")), \
                patch.object(industrial, "SubprocessKernel", side_effect=AssertionError("unexpected kernel")):
            industrial.validate_document(self.document)
        self.assertEqual(docmodel.document_to_l1(self.document), before)

    def test_unknown_nonbool_dependency_and_saved_mismatch_rejected(self):
        for mode, reason in (
                ("unknown", "unknown_feature_binding"), ("nonbool", "bad_feature_configuration"),
                ("dependency", "configuration_dependency_invalid"),
                ("mismatch", "configuration_state_mismatch"), ("uuid", "unknown_binding_target")):
            document = copy.deepcopy(self.document)
            spec = document.extra["parametric"]
            if mode == "unknown":
                spec["configuration_suppressions"]["solid"]["unknown"] = True
            elif mode == "nonbool":
                spec["configuration_suppressions"]["solid"]["hole_cut"] = None
            elif mode == "dependency":
                spec["configuration_suppressions"]["solid"] = {"plate_boss": True}
            elif mode == "mismatch":
                spec["active_configuration"] = "solid"
            else:
                spec["feature_bindings"]["hole_cut"]["target_uuid"] = uuid4().hex
            before = docmodel.document_to_l1(document)
            with self.subTest(mode=mode), self.assertRaises(parametric.ParametricError) as caught:
                industrial.validate_document(document)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(docmodel.document_to_l1(document), before)

    def test_configuration_candidate_seal_rejects_nested_flags_and_rehash(self):
        text = docmodel.document_to_l1(docmodel.document_from_l1(docmodel.document_to_l1(self.document)))
        measured = {"document_sha256": digest(text)}
        measured["mesh_sha256"] = industrial._fingerprint(measured)
        base = digest("base")
        response = {"doc": text, "document_sha256": digest(text), "base_document_sha256": base,
                    "mesh": {"document_sha256": digest(text), "solids": [measured]},
                    "step": {"document_sha256": digest(text)}, "feature_bindings": {"hole_cut": False}}
        candidate = industrial.ParametricCandidate(base, text, response)
        candidate.verify_integrity()
        candidate.response["feature_bindings"]["hole_cut"] = True
        with self.assertRaises(parametric.ParametricError) as caught:
            candidate.verify_integrity()
        self.assertEqual(caught.exception.reason, "candidate_changed")


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = RUN_ROOT or EVIDENCE
        root.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix="candidate-suite-", dir=root))
        workspace = new_workspace(cls.root / "seed")
        cls.seed = industrial.prepare_parametric(workspace, body(workspace, "create_plate"))
        cls.seed.verify_integrity()
        MEASUREMENTS["seed"] = {"document_sha256": cls.seed.response["document_sha256"],
                                "step": cls.seed.response["step"]}

    def setUp(self):
        self.workspace = new_workspace(self.root / ("workspace-" + uuid4().hex))
        self.workspace.doc = docmodel.document_from_l1(self.seed.doc)
        self.workspace._mesh_cache = copy.deepcopy(self.seed.response["mesh"])

    def assert_plate(self, candidate, width, radius, depth):
        candidate.verify_integrity()
        response = candidate.response
        mesh = response["mesh"]["solids"][0]
        expected = (width * 20 - math.pi * radius**2) * depth
        actual = mesh["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(actual, expected, delta=1e-5)
        self.assertEqual(mesh["properties"]["bbox_nm"], [0, 0, 0, width * MM, 20 * MM, depth * MM])
        self.assertEqual(mesh["solid_count"], 1)
        measured, incidence = integrated_volume(mesh)
        self.assertAlmostEqual(measured, expected, delta=expected * .005)
        self.assertTrue(incidence and all(count == 2 for count in incidence.values()))
        self.assertEqual(response["mesh"]["document_sha256"], digest(candidate.doc))
        self.assertEqual(response["step"]["document_sha256"], digest(candidate.doc))
        self.assertGreater(response["step"]["bytes"], 1000)
        self.assertFalse(docmodel.load_blocking_problems(docmodel.document_from_l1(candidate.doc)))
        self.assertTrue(all(report["converged"] and report["dof"] == 0
                            for report in response["sketch_reports"]))
        MEASUREMENTS[str(width)] = {"brep_volume_mm3": actual, "mesh_volume_mm3": measured,
                                    "analytic_volume_mm3": expected, "document_sha256": digest(candidate.doc)}

    def assert_rejected(self, payload, reason, status=None):
        before = snapshot(self.workspace)
        with self.assertRaises(server.ApiError) as caught:
            industrial.prepare_parametric(self.workspace, payload)
        self.assertEqual(caught.exception.code, reason)
        if status is not None:
            self.assertEqual(caught.exception.status, status)
        self.assertEqual(snapshot(self.workspace), before)
        return caught.exception

    def test_runtime_imports_are_from_this_checkout(self):
        for module, expected in ((server, APP / "server.py"),
                                 (industrial, APP / "industrial_parametric.py"),
                                 (parametric, CORE / "cadcore/parametric.py"),
                                 (subproc, CORE / "cadcore/kernel/subproc.py")):
            self.assertEqual(Path(module.__file__).resolve(), expected.resolve())

    def test_get_empty_and_configured_is_read_only(self):
        before = snapshot(self.workspace)
        read = industrial.read_parametric(self.workspace, {})
        self.assertTrue(read["enabled"])
        self.assertEqual(len(read["driving_dimensions"]), 5)
        self.assertEqual(snapshot(self.workspace), before)
        empty = new_workspace(self.root / ("empty-" + uuid4().hex))
        before = snapshot(empty)
        read = industrial.read_parametric(empty, None)
        self.assertFalse(read["enabled"])
        self.assertTrue(read["can_create_plate"])
        self.assertEqual(snapshot(empty), before)

    def test_create_real_plate_candidate_does_not_install(self):
        workspace = new_workspace(self.root / ("create-" + uuid4().hex))
        workspace.doc.meta["project_label"] = "retained project metadata"
        before = snapshot(workspace)
        candidate = industrial.prepare_parametric(workspace, body(workspace, "create_plate"))
        self.assert_plate(candidate, 40, 4, 8)
        self.assertEqual(snapshot(workspace), before)
        self.assertEqual({key: json.loads(candidate.doc)["meta"][key] for key in workspace.doc.meta},
                         workspace.doc.meta)
        self.assertEqual(candidate.before_document_sha256, digest(before[0]))
        self.assertIsInstance(candidate.doc, str)

    def test_configuration_updates_all_sketch_and_feature_dependencies(self):
        before = snapshot(self.workspace)
        candidate = industrial.prepare_parametric(self.workspace, body(self.workspace, configuration="wide"))
        self.assert_plate(candidate, 60, 6, 12)
        self.assertEqual(snapshot(self.workspace), before)
        document = json.loads(candidate.doc)
        circle = next(row for row in document["sketches"][1]["entities"] if row["kind"] == "circle")
        self.assertEqual(circle["geom"], {"cx": 30 * MM, "cy": 10 * MM, "r": 6 * MM})
        self.assertEqual([f["params"]["distance_nm"] for f in document["features"]], [12 * MM, 12 * MM])

    def test_driving_dimension_override_preserves_semantic_uuid(self):
        candidate = industrial.prepare_parametric(
            self.workspace, body(self.workspace, dimensions={"plate_width": "60 * mm"}))
        self.assert_plate(candidate, 60, 6, 12)
        self.assertEqual(json.loads(candidate.doc)["parametric"]["bindings"],
                         self.workspace.doc.extra["parametric"]["bindings"])

    def test_actual_l1_save_reopen_and_repeat_rebuild_same_sha(self):
        candidate = industrial.prepare_parametric(self.workspace, body(self.workspace, configuration="wide"))
        install(self.workspace, candidate)
        path = self.root / ("saved-" + uuid4().hex + ".l1")
        docmodel.save_l1(self.workspace.doc, str(path))
        reopened = new_workspace(self.root / ("reopened-" + uuid4().hex))
        reopened.doc = docmodel.load_l1(str(path))
        self.assertEqual(reopened.l1_text(), candidate.doc)
        rebuilt = industrial.prepare_parametric(reopened, body(reopened))
        self.assertEqual(rebuilt.doc, candidate.doc)
        self.assert_plate(rebuilt, 60, 6, 12)
        self.assertFalse(rebuilt.response["changed"])
        self.assertEqual(len(self.workspace.undo_stack), 1)

    def test_step_download_true_roundtrip_measures_same_document(self):
        candidate = industrial.prepare_parametric(self.workspace, body(self.workspace, configuration="wide"))
        install(self.workspace, candidate)
        prior = tempfile.tempdir
        tempfile.tempdir = str(self.root)
        try:
            data = self.workspace.step_bytes()
        finally:
            tempfile.tempdir = prior
        make_step(step_text=data.decode("ascii"))
        document = docmodel.new_document()
        document.features.append({"uuid": str(uuid4()), "kind": "import_step",
                                  "params": {"step_text": data.decode("ascii")}, "suppressed": False})
        directory = self.root / ("step-roundtrip-" + uuid4().hex)
        kernel = subproc.SubprocessKernel(work_dir=str(directory), keep_work_dir=True,
                                          extra_env={"TMPDIR": str(self.root)})
        try:
            shape = kernel.shape_from_document(json.loads(docmodel.document_to_l1(document)))
            actual = kernel.mass_properties(shape).volume_nm3 / 1e18
            expected = candidate.response["mesh"]["solids"][0]["properties"]["volume_nm3"] / 1e18
            self.assertAlmostEqual(actual, expected, delta=1e-5)
            self.assertEqual(digest(self.workspace.l1_text()), candidate.response["step"]["document_sha256"])
            (self.root / "download.step").write_bytes(data)
            MEASUREMENTS["download"] = {"sha256": digest(data), "bytes": len(data),
                                        "roundtrip_volume_mm3": actual,
                                        "document_sha256": digest(self.workspace.l1_text())}
        finally:
            kernel.close()

    def test_invalid_hole_negative_cycle_and_unsafe_expression_atomic(self):
        for override, reason in (({"radius": "11 * mm"}, "design_check_failed"),
                                 ({"width": "-1 * mm"}, "invalid_parameter"),
                                 ({"width": "hole_x * 2"}, "parameter_cycle"),
                                 ({"width": "__import__('os')"}, "unsafe_expression"),
                                 ({"hole_x": "80 * mm"}, "hole_outside_profile")):
            with self.subTest(reason=reason):
                self.assert_rejected(body(self.workspace, overrides=override), reason)

    def test_real_brep_failure_retains_first_cause_and_kernel_evidence(self):
        self.workspace.doc.features.append({
            "uuid": str(uuid4()), "kind": "fillet", "inputs": [self.workspace.doc.features[-1]["uuid"]],
            "params": {"size_nm": 1000 * MM, "edge_indices": [1]}, "suppressed": False})
        exc = self.assert_rejected(body(self.workspace), "brep_rebuild_failed")
        self.assertIsNotNone(exc.__cause__.__cause__)
        log = Path(exc.extra["evidence_path"]) / "failure.log"
        text = log.read_text()
        self.assertIn("fillet", text)
        self.assertIn("KernelExecutionError", text)
        self.assertIn("OCCError: BRep_API: command not done", text)
        self.assertTrue(list(log.parent.rglob("*.json")))

    def test_sketch_reference_dimension_error_is_explicit_and_atomic(self):
        binding = self.workspace.doc.extra["parametric"]["bindings"]["plate_width"]
        dimension = next(d for d in self.workspace.doc.sketches[0]["dimensions"]
                         if d["uuid"] == binding["target_uuid"])
        dimension["value_nm"] = None
        exc = self.assert_rejected(body(self.workspace), "parametric_sketch_failed")
        self.assertIsNotNone(exc.__cause__)
        self.assertTrue(exc.extra["sketch_key"].startswith("err."))

    def test_artifact_failure_keeps_doc_history_cache_and_traceback(self):
        with patch.object(subproc.SubprocessKernel, "export", side_effect=OSError("injected STEP disk error")):
            exc = self.assert_rejected(body(self.workspace), "parametric_artifact_failed", 503)
        self.assertIn("injected STEP disk error", exc.detail)
        self.assertIn("injected STEP disk error", (Path(exc.extra["evidence_path"]) / "failure.log").read_text())

    def test_stale_before_digest_and_maintenance_reject_before_work(self):
        self.assert_rejected(body(self.workspace, expected_document_sha256="0" * 64),
                             "parametric_conflict", 409)
        self.workspace._release_pending = True
        self.assert_rejected(body(self.workspace), "maintenance", 409)

    def test_late_edit_is_never_overwritten_and_outer_cas_rejects(self):
        original = industrial.parametric.prepare_rebuild

        def edit_after_measurement(*args, **kwargs):
            candidate = original(*args, **kwargs)
            with self.workspace._lock:
                self.workspace.doc.meta["late_edit"] = "accepted and retained"
            return candidate

        with patch.object(industrial.parametric, "prepare_rebuild", side_effect=edit_after_measurement):
            candidate = industrial.prepare_parametric(self.workspace, body(self.workspace, configuration="wide"))
        before = snapshot(self.workspace)
        with self.assertRaises(server.ApiError) as caught:
            install(self.workspace, candidate)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(snapshot(self.workspace), before)
        self.assertIn("accepted and retained", self.workspace.l1_text())

    def test_candidate_mesh_and_document_tampering_rejected(self):
        for kind in ("mesh", "doc", "before", "step"):
            candidate = copy.deepcopy(self.seed)
            if kind == "mesh":
                candidate.response["mesh"]["solids"][0]["vertices_mm"][0][0] += 1
            elif kind == "doc":
                candidate.response["doc"] += " "
            elif kind == "before":
                candidate.response["base_document_sha256"] = "0" * 64
            else:
                candidate.response["step"]["sha256"] = "0" * 64
            with self.subTest(kind=kind), self.assertRaises(parametric.ParametricError):
                candidate.verify_integrity()

    def test_strict_api_fields_and_unknown_dimensions(self):
        for payload in (None, {}, [], body(self.workspace, expected_revision=1),
                        body(self.workspace, action="other"), body(self.workspace, dimensions={"width": 60}),
                        body(self.workspace, overrides={"width": True})):
            with self.subTest(payload=payload):
                self.assert_rejected(payload, "bad_parametric_arguments", 400)
        self.assert_rejected(body(self.workspace, dimensions={"absent": "60 * mm"}),
                             "unknown_driving_dimension", 400)
        self.assert_rejected(body(self.workspace, dimensions={"plate_width": "60 * mm"},
                                  overrides={"width": "50 * mm"}), "duplicate_parametric_override", 400)
        with self.assertRaises(server.ApiError):
            industrial.read_parametric(self.workspace, {"unexpected": 1})

    def test_create_cannot_replace_geometry_history_dimensions_or_extensions(self):
        for kind in ("geometry", "history", "dimensions", "extension", "plane", "second_sketch"):
            workspace = new_workspace(self.root / (kind + "-" + uuid4().hex))
            if kind == "geometry":
                commands.add_circle(workspace.sketch, center=(0, 0), radius_nm=MM)
            elif kind == "history":
                workspace.undo_stack.append(workspace.l1_text())
            elif kind == "dimensions":
                workspace.sketch["dimensions"].append({"uuid": str(uuid4()), "kind": "linear"})
            elif kind == "extension":
                workspace.doc.extra["unknown_asset"] = {"keep": True}
            elif kind == "plane":
                workspace.sketch["plane"] = "XZ"
            else:
                workspace.doc.sketches.append(workspace._new_sketch())
            self.assertFalse(industrial.read_parametric(workspace)["can_create_plate"])
            before = snapshot(workspace)
            with self.subTest(kind=kind), self.assertRaises(server.ApiError) as caught:
                industrial.prepare_parametric(workspace, body(workspace, "create_plate"))
            self.assertEqual(caught.exception.code, "parametric_requires_empty")
            self.assertEqual(snapshot(workspace), before)

    def test_runtime_root_alias_positive_and_interior_symlink_negative(self):
        self.assertEqual(industrial._scratch_root(self.workspace), Path(self.workspace.out_root).resolve())
        alias = self.root / ("linked-root-" + uuid4().hex)
        alias.symlink_to(Path(self.workspace.out_root).resolve(), target_is_directory=True)
        self.workspace.out_root = str(alias)
        self.assert_rejected(body(self.workspace), "parametric_runtime_symlink", 500)

    def test_runtime_root_source_and_missing_rejected(self):
        self.workspace.out_root = str(APP)
        self.assert_rejected(body(self.workspace), "parametric_runtime_in_source", 500)
        self.workspace.out_root = str(self.root / "absent")
        self.assert_rejected(body(self.workspace), "parametric_runtime_missing", 500)

    def test_malformed_binding_table_rejected_explicitly_without_mutation(self):
        self.workspace.doc.extra["parametric"]["bindings"] = []
        self.assert_rejected(body(self.workspace), "bad_parametric_spec", 422)
        with self.assertRaises(server.ApiError) as caught:
            industrial.read_parametric(self.workspace)
        self.assertEqual(caught.exception.code, "bad_parametric_spec")

    def test_unknown_configuration_and_parameter_rejected_atomically(self):
        self.assert_rejected(body(self.workspace, configuration="absent"), "unknown_configuration")
        self.assert_rejected(body(self.workspace, overrides={"absent": "10 * mm"}), "unknown_parameter")

    def test_configuration_reset_rebuilds_base_and_clears_overrides(self):
        changed = industrial.prepare_parametric(
            self.workspace, body(self.workspace, configuration="wide", overrides={"width": "50 * mm"}))
        install(self.workspace, changed)
        reset = industrial.prepare_parametric(self.workspace, body(self.workspace, configuration=""))
        self.assert_plate(reset, 40, 4, 8)
        self.assertEqual(json.loads(reset.doc)["parametric"]["active_overrides"], {})

    def test_solver_conflict_rejects_real_candidate_without_mutation(self):
        binding = self.workspace.doc.extra["parametric"]["bindings"]["plate_width"]
        target = next(row for row in self.workspace.doc.sketches[0]["dimensions"]
                      if row["uuid"] == binding["target_uuid"])
        commands.add_dimension(self.workspace.doc.sketches[0], kind="linear",
                               refs=target["refs"], value_nm=10 * MM)
        self.assert_rejected(body(self.workspace, configuration="wide"), "sketch_not_converged")

    def test_candidate_error_loader_matches_actual_workspace_class(self):
        specification = importlib.util.spec_from_file_location("parametric_private_server", APP / "server.py")
        private = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(private)
        workspace = private.Workspace(private._load_cadcore(), locale="zh-CN", standard="gbt",
                                      out_root=str(self.root))
        with self.assertRaises(private.ApiError) as caught:
            industrial.prepare_parametric(workspace, {})
        self.assertIs(type(caught.exception), private.ApiError)

    def test_failure_log_error_keeps_original_artifact_first_cause(self):
        original = Path.write_text

        def disk_failure(path, *args, **kwargs):
            if path.name == "failure.log":
                raise OSError("injected secondary evidence failure")
            return original(path, *args, **kwargs)

        with patch.object(subproc.SubprocessKernel, "export", side_effect=OSError("primary STEP failure")), \
                patch.object(Path, "write_text", disk_failure):
            exc = self.assert_rejected(body(self.workspace), "parametric_artifact_failed", 503)
        self.assertEqual(str(exc.__cause__), "primary STEP failure")
        self.assertIn("primary STEP failure", exc.detail)
        self.assertIn("secondary evidence failure", exc.detail)


class ConfigurationGatewayTests(unittest.TestCase):
    """Real auth/gateway/store HTTP; own fresh local SQLite, retained 3FS evidence."""

    def setUp(self):
        from access_control import AuthController
        from project_store import ProjectStore
        from project_gateway import ProjectGateway
        from secure_web import build_server
        self.types = AuthController, ProjectStore, ProjectGateway, build_server
        evidence = RUN_ROOT or EVIDENCE
        evidence.mkdir(parents=True, exist_ok=True)
        self.evidence = Path(tempfile.mkdtemp(prefix="configuration-http-", dir=evidence))
        runtime = Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT",
                                      "/data/tmp/opencad-config-suppression-20261012")).absolute()
        runtime.mkdir(parents=True, exist_ok=True)
        self.data = Path(tempfile.mkdtemp(prefix="configuration-gateway-", dir=runtime))
        filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(self.data)], text=True).strip()
        self.assertFalse(filesystem.startswith("fuse") or filesystem in {"nfs", "nfs4", "tmpfs", "cifs"})
        self.assertFalse(self.data.resolve().is_relative_to(ROOT))
        self.names = ["config_" + uuid4().hex, "other_" + uuid4().hex]
        self.passwords = [uuid4().hex, uuid4().hex]
        self.audit = []
        self.start(register=True)
        self.addCleanup(self.stop)

    def start(self, register=False):
        Auth, Store, Gateway, build = self.types
        self.auth = Auth(self.data / "accounts", allow_registration=register)
        if register:
            for name, password in zip(self.names, self.passwords):
                self.auth.register(name, password)
        self.store = Store(self.data / "projects")
        self.app = Gateway(self.auth, self.store, self.data / "runtime", secure_cookies=False)
        self.http = build(self.app)
        self.port = self.http.server_address[1]
        self.origin = "http://127.0.0.1:" + str(self.port)
        self.thread = threading.Thread(target=self.http.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.cookies = []
        for name, password in zip(self.names, self.passwords):
            status, headers, result = self.call("POST", "/api/auth/login",
                                               {"username": name, "password": password}, user=None)
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.cookies.append(headers["Set-Cookie"].split(";", 1)[0])

    def stop(self):
        if getattr(self, "http", None) is not None:
            self.http.shutdown()
            self.http.server_close()
            self.thread.join(timeout=5)
            self.assertFalse(self.thread.is_alive())
            self.app.close()
            self.http = None
        (self.evidence / "http-audit.json").write_text(json.dumps(self.audit, indent=2) + "\n")

    def call(self, method, route, payload=None, *, user=0, project=None):
        headers = {"X-OpenCAD-Request": "1", "Origin": self.origin}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if user is not None:
            headers["Cookie"] = self.cookies[user]
        if project is not None:
            headers["X-OpenCAD-Project"] = project
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            connection.request(method, route, body=None if payload is None else json.dumps(payload),
                               headers=headers)
            response = connection.getresponse()
            raw = response.read()
            self.audit.append({"at": datetime.now(timezone.utc).isoformat(), "method": method,
                               "route": route, "status": response.status, "bytes": len(raw),
                               "sha256": None if route.startswith("/api/auth/") else digest(raw)})
            result = json.loads(raw) if response.getheader("Content-Type", "").startswith(
                "application/json") else raw
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def state(self, project):
        status, _, result = self.call("GET", "/api/state", project=project)
        self.assertEqual(status, 200, result)
        return result

    def mutation(self, state, **fields):
        return {"expected_revision": state["revision"], "expected_sha": state["sha256"],
                "request_id": uuid4().hex, **fields}

    def db_anchor(self):
        connection = sqlite3.connect(self.store.db_path.as_uri() + "?mode=ro", uri=True)
        try:
            counts = {table: connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                      for table in ("projects", "snapshots", "receipts", "create_receipts")}
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            return counts
        finally:
            connection.close()

    def assert_geometry(self, project, expected, label):
        state = self.state(project)
        status, _, read = self.call("GET", "/api/parametric", project=project)
        self.assertEqual(status, 200, read)
        self.assertEqual(read["document_sha256"], digest(state["doc"]))
        status, _, mesh = self.call("GET", "/api/mesh", project=project)
        self.assertEqual(status, 200, mesh)
        measured = mesh["solids"][0]
        actual = measured["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(actual, expected, delta=1e-5)
        volume, incidence = integrated_volume(measured)
        self.assertAlmostEqual(volume, expected, delta=expected * .005)
        self.assertTrue(incidence and all(count == 2 for count in incidence.values()))
        self.assertEqual(mesh["document_sha256"], digest(state["doc"]))
        self.assertEqual((mesh["revision"], mesh["sha256"]), (state["revision"], state["sha256"]))
        status, headers, step = self.call("GET", "/api/step", project=project)
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-OpenCAD-Document-SHA256"], digest(state["doc"]))
        self.assertEqual(int(headers["X-OpenCAD-Revision"]), state["revision"])
        path = self.evidence / (label + ".step")
        path.write_bytes(step)
        imported = docmodel.new_document()
        imported.features.append({"uuid": uuid4().hex, "kind": "import_step", "suppressed": False,
                                  "params": {"step_text": step.decode("ascii")}})
        kernel = subproc.SubprocessKernel(work_dir=str(self.evidence / (label + "-worker")),
                                          keep_work_dir=True,
                                          extra_env={"TMPDIR": str(self.evidence.resolve()),
                                                     "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            shape = kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
            roundtrip = kernel.mass_properties(shape).volume_nm3 / 1e18
            self.assertAlmostEqual(roundtrip, expected, delta=1e-5)
        finally:
            kernel.close()
        record = {"configuration": read["configuration"], "expected_mm3": expected,
                  "brep_mm3": actual, "mesh_mm3": volume, "step_mm3": roundtrip,
                  "doc_sha256": digest(state["doc"]), "revision": state["revision"],
                  "step_path": str(path), "step_sha256": digest(step),
                  "feature_bindings": read["feature_bindings"]}
        MEASUREMENTS.setdefault("configuration_gateway", []).append(record)
        return state

    def test_real_toggle_save_undo_redo_restart_receipts_and_failures(self):
        status, _, head = self.call("POST", "/api/projects",
                                   {"title": "Configuration plate", "request_id": uuid4().hex})
        self.assertEqual(status, 201, head)
        project = head["project_id"]
        state = self.state(project)
        payload = self.mutation(state, action="create_configurable_plate",
                                expected_document_sha256=digest(state["doc"]))
        status, _, created = self.call("POST", "/api/parametric", payload, project=project)
        self.assertEqual(status, 200, created)
        self.assertEqual(created["parametric_version"], 2)
        self.assertTrue(all(r["converged"] and r["dof"] == 0 and r["residual_nm"] <= 1
                            for r in created["sketch_reports"]))
        pierced = self.assert_geometry(project, (800 - math.pi * 16) * 8, "pierced")
        payload = self.mutation(pierced, action="rebuild", configuration="wide_solid",
                                expected_document_sha256=digest(pierced["doc"]))
        status, _, solid = self.call("POST", "/api/parametric", payload, project=project)
        self.assertEqual(status, 200, solid)
        wide = self.assert_geometry(project, 14400, "wide-solid")
        self.assertTrue(solid["feature_bindings"]["hole_cut"]["suppressed"])
        before = self.store.read_head(project)
        before_db = self.db_anchor()
        status, _, replay = self.call("POST", "/api/parametric", payload, project=project)
        self.assertEqual(status, 200, replay)
        self.assertEqual(replay, solid)
        self.assertEqual(self.store.read_head(project), before)
        self.assertEqual(self.db_anchor(), before_db)
        status, _, _ = self.call("GET", "/api/parametric", project=project, user=1)
        self.assertEqual(status, 403)
        for mode, reason in (("dependency", "configuration_dependency_invalid"),
                             ("nonbool", "bad_feature_configuration"),
                             ("unknown", "unknown_feature_binding"),
                             ("mismatch", "configuration_state_mismatch")):
            invalid = json.loads(wide["doc"])
            spec = invalid["parametric"]
            if mode == "dependency":
                spec["configuration_suppressions"]["solid"] = {"plate_boss": True}
            elif mode == "nonbool":
                spec["configuration_suppressions"]["solid"]["hole_cut"] = None
            elif mode == "unknown":
                spec["configuration_suppressions"]["solid"]["missing"] = True
            else:
                invalid["features"][1]["suppressed"] = False
            opening = self.mutation(wide, doc=json.dumps(invalid))
            status, _, failure = self.call("POST", "/api/open", opening, project=project)
            self.assertEqual(status, 422, failure)
            self.assertEqual(failure["error"], reason)
            self.assertEqual(self.store.read_head(project), before)
            self.assertEqual(self.db_anchor(), before_db)
            self.assertIsNone(self.store.read_receipt(project, opening["request_id"],
                request_payload={**opening, "route": "/api/open"}))
        for configuration, overrides, reason in (
                ("unknown", {}, "unknown_configuration"), (None, {"radius": "11 * mm"}, "design_check_failed"),
                (None, {"width": "hole_x * 2"}, "parameter_cycle")):
            rejected = self.mutation(wide, action="rebuild", configuration=configuration, overrides=overrides,
                                     expected_document_sha256=digest(wide["doc"]))
            status, _, failure = self.call("POST", "/api/parametric", rejected, project=project)
            self.assertEqual(status, 422, failure)
            self.assertEqual(failure["error"], reason)
            self.assertEqual(self.store.read_head(project), before)
            self.assertEqual(self.db_anchor(), before_db)
            self.assertIsNone(self.store.read_receipt(project, rejected["request_id"],
                request_payload={**rejected, "route": "/api/parametric"}))
        stale = self.mutation(pierced, action="rebuild", configuration="",
                              expected_document_sha256=digest(pierced["doc"]))
        status, _, _ = self.call("POST", "/api/parametric", stale, project=project)
        self.assertEqual(status, 409)
        self.assertEqual(self.store.read_head(project), before)
        self.assertEqual(self.db_anchor(), before_db)
        status, _, undone = self.call("POST", "/api/command",
            self.mutation(wide, command="undo", args={}), project=project)
        self.assertEqual(status, 200, undone)
        undo = self.assert_geometry(project, (800 - math.pi * 16) * 8, "undo-pierced")
        self.assertEqual(undo["doc"], pierced["doc"])
        status, _, redone = self.call("POST", "/api/command",
            self.mutation(undo, command="redo", args={}), project=project)
        self.assertEqual(status, 200, redone)
        redo = self.assert_geometry(project, 14400, "redo-solid")
        self.assertEqual(redo["doc"], wide["doc"])
        status, _, saved = self.call("POST", "/api/save", self.mutation(redo), project=project)
        self.assertEqual(status, 200, saved)
        saved_state = self.state(project)
        saved_head = self.store.read_head(project)
        self.stop()
        self.start()
        self.assertEqual(self.store.read_head(project), saved_head)
        restarted = self.assert_geometry(project, 14400, "restart-solid")
        self.assertEqual(restarted["doc"], saved_state["doc"])
        repeated = self.mutation(restarted, action="rebuild",
                                 expected_document_sha256=digest(restarted["doc"]))
        status, _, rebuilt = self.call("POST", "/api/parametric", repeated, project=project)
        self.assertEqual(status, 200, rebuilt)
        self.assertEqual(rebuilt["doc"], restarted["doc"])
        reopened = self.state(project)
        self.assertEqual(self.store.read_head(project)["snapshot"]["doc"], restarted["doc"])
        self.assertTrue(all(r["dof"] == 0 and r["residual_nm"] <= 1 for r in rebuilt["sketch_reports"]))
        MEASUREMENTS["configuration_gateway_solver"] = rebuilt["sketch_reports"]
        downloaded_status, _, downloaded = self.call("GET", "/api/document", project=project)
        self.assertEqual(downloaded_status, 200)
        opening = self.mutation(reopened, doc=json.dumps(downloaded))
        status, _, opened = self.call("POST", "/api/open", opening, project=project)
        self.assertEqual(status, 200, opened)
        reopened = self.state(project)
        self.assertEqual(reopened["doc"], restarted["doc"])
        self.assertEqual((reopened["undo_depth"], reopened["redo_depth"]), (0, 0))
        restarted = reopened
        reset = self.mutation(restarted, action="rebuild", configuration="",
                              expected_document_sha256=digest(restarted["doc"]))
        status, _, result = self.call("POST", "/api/parametric", reset, project=project)
        self.assertEqual(status, 200, result)
        restored = self.assert_geometry(project, (800 - math.pi * 16) * 8, "reset-pierced")
        self.assertEqual(restored["doc"], pierced["doc"])
        MEASUREMENTS["configuration_gateway_runtime"] = str(self.data)
        MEASUREMENTS["configuration_gateway_db_counts"] = self.db_anchor()
        MEASUREMENTS["configuration_gateway_http_count"] = len(self.audit)

class FixtureProject:
    """Isolated durable test project; no production routes or credentials."""

    def __init__(self, root):
        self.root = root
        self.workspace = new_workspace(root / "workspace")
        self.lock = threading.RLock()
        self.revision = 1
        self.project_id = uuid4().hex
        self.receipts = {}
        self.write()

    def state_bytes(self):
        return json.dumps({"doc": self.workspace.l1_text(), "undo": self.workspace.undo_stack,
                           "redo": self.workspace.redo_stack, "revision": self.revision},
                          sort_keys=True).encode()

    def write(self):
        path = self.root / "saved-project.json"
        temporary = self.root / "saved-project.tmp"
        with temporary.open("wb") as handle:
            handle.write(self.state_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def metadata(self):
        return {"project_id": self.project_id, "revision": self.revision,
                "sha256": digest(self.state_bytes())}

    def reopen(self):
        saved = json.loads((self.root / "saved-project.json").read_text())
        workspace = new_workspace(self.root / ("reopened-" + uuid4().hex))
        workspace.doc = docmodel.document_from_l1(saved["doc"])
        workspace.undo_stack, workspace.redo_stack = saved["undo"], saved["redo"]
        self.workspace = workspace
        self.revision = saved["revision"]

    def mutate(self, payload):
        with self.lock:
            request_id = payload["request_id"]
            fingerprint = digest(json.dumps(payload, sort_keys=True))
            if request_id in self.receipts:
                prior, response = self.receipts[request_id]
                if prior != fingerprint:
                    raise server.ApiError("idempotency_conflict", status=409)
                return response
            if (payload["expected_revision"], payload["expected_sha"]) != (
                    self.revision, self.metadata()["sha256"]):
                raise server.ApiError("revision_conflict", status=409)
            fields = {key: value for key, value in payload.items()
                      if key not in {"expected_revision", "expected_sha", "request_id"}}
            candidate = industrial.prepare_parametric(self.workspace, fields)
            install(self.workspace, candidate)
            self.revision += int(candidate.response["changed"])
            self.write()
            response = {**candidate.response, **self.metadata()}
            self.receipts[request_id] = fingerprint, response
            return response


SESSION_FIXTURE = """
/* TEST ONLY: models project-session metadata injection, no authentication. */
(function(){
  const nativeFetch=window.fetch; let metadata=null;
  window.__requests=[];
  window.addEventListener('opencad:parametric',function(event){window.__parametricLast=event.detail;});
  window.fetch=async function(path,options){
    options=options || {};
    if(options.method==='POST' && path==='/api/parametric'){
      if(!metadata)throw new Error('fixture_metadata_absent');
      const body=JSON.parse(options.body);
      Object.assign(body,{expected_revision:metadata.revision,expected_sha:metadata.sha256,
                         request_id:crypto.randomUUID()});
      window.__requests.push(body); options=Object.assign({},options,{body:JSON.stringify(body)});
    }
    const response=await nativeFetch(path,options);
    if(response.headers.get('Content-Type').includes('application/json')){
      const value=await response.clone().json();
      if(value.ok && value.project_id)metadata=value;
    }
    return response;
  };
}());
"""


def fixture_http(project, log_path):
    import viewer3d_page

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            with log_path.open("a") as log:
                log.write((fmt % args) + "\n")

        def respond(self, data, media="application/json", status=200, **headers):
            payload = json.dumps(data).encode() if media == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in headers.items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            try:
                with project.lock:
                    if self.path == "/api/parametric":
                        return self.respond({**industrial.read_parametric(project.workspace), **project.metadata()})
                    if self.path == "/api/state":
                        return self.respond({**project.workspace.state(), **project.metadata()})
                    if self.path == "/api/mesh":
                        return self.respond({**project.workspace.mesh(), **project.metadata()})
                    if self.path == "/api/catalogs":
                        return self.respond(project.workspace.catalogs())
                    if self.path == "/api/step":
                        return self.respond(project.workspace.step_bytes(), "application/step", **{
                            "X-OpenCAD-Document-SHA256": digest(project.workspace.l1_text())})
                if self.path == "/":
                    html = viewer3d_page.render().replace("</head>", """
<link rel="stylesheet" href="/web/css/parametric-panel.css">
<script src="/fixture-session.js"></script>
<script src="/web/js/parametric-panel.js" defer></script></head>""")
                    return self.respond(html.encode(), "text/html")
                if self.path == "/fixture-session.js":
                    return self.respond(SESSION_FIXTURE.encode(), "text/javascript")
                if self.path.startswith("/web/"):
                    target = (APP / self.path.lstrip("/")).resolve()
                    if target.is_relative_to(APP / "web") and target.is_file():
                        media = "text/css" if target.suffix == ".css" else "text/javascript"
                        return self.respond(target.read_bytes(), media)
                self.respond({"ok": False, "error": "not_found"}, status=404)
            except server.ApiError as exc:
                self.respond(exc.payload(), status=exc.status)
            except Exception:
                with log_path.open("a") as log:
                    log.write(traceback.format_exc())
                self.respond({"ok": False, "error": "fixture_failed"}, status=500)

        def do_POST(self):
            try:
                if self.path != "/api/parametric":
                    raise server.ApiError("not_found", status=404)
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.respond(project.mutate(payload))
            except server.ApiError as exc:
                self.respond(exc.payload(), status=exc.status)
            except Exception:
                with log_path.open("a") as log:
                    log.write(traceback.format_exc())
                self.respond({"ok": False, "error": "fixture_failed"}, status=500)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return http, thread


def http_json(base, payload=None):
    request = urllib.request.Request(base + "/api/parametric",
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


class HttpContractTests(unittest.TestCase):
    def setUp(self):
        root = RUN_ROOT or EVIDENCE
        root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="http-suite-", dir=root))
        self.project = FixtureProject(self.root)
        self.http, self.thread = fixture_http(self.project, self.root / "http.log")
        self.base = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def mutation(self, state, action="rebuild", **kwargs):
        return {"action": action, "expected_document_sha256": state["document_sha256"],
                "expected_revision": state["revision"], "expected_sha": state["sha256"],
                "request_id": uuid4().hex, **kwargs}

    def test_http_candidate_commit_receipt_idempotency_and_reopen(self):
        status, state = http_json(self.base)
        self.assertEqual(status, 200)
        payload = self.mutation(state, "create_plate")
        status, created = http_json(self.base, payload)
        self.assertEqual(status, 200)
        self.assertEqual(created["revision"], 2)
        saved = (self.root / "saved-project.json").read_bytes()
        status, replay = http_json(self.base, payload)
        self.assertEqual(replay, created)
        self.assertEqual((self.root / "saved-project.json").read_bytes(), saved)
        status, changed = http_json(self.base, self.mutation(created, configuration="wide"))
        self.assertEqual(status, 200)
        self.assertEqual(changed["revision"], 3)
        self.project.reopen()
        status, reopened = http_json(self.base)
        self.assertEqual(reopened["document_sha256"], changed["document_sha256"])
        self.assertEqual(reopened["configuration"], "wide")
        self.assertEqual(self.project.workspace.l1_text(), changed["doc"])
        status, rebuilt = http_json(self.base, self.mutation(reopened))
        self.assertEqual(rebuilt["document_sha256"], changed["document_sha256"])
        self.assertEqual(rebuilt["revision"], 3)

    def test_http_invalid_hole_and_stale_version_preserve_saved_bytes(self):
        _, state = http_json(self.base)
        _, created = http_json(self.base, self.mutation(state, "create_plate"))
        before = (self.root / "saved-project.json").read_bytes()
        status, failure = http_json(self.base, self.mutation(created, overrides={"radius": "11 * mm"}))
        self.assertEqual(status, 422)
        self.assertEqual(failure["error"], "design_check_failed")
        self.assertEqual((self.root / "saved-project.json").read_bytes(), before)
        status, stale = http_json(self.base, self.mutation(state))
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"], "revision_conflict")
        self.assertEqual((self.root / "saved-project.json").read_bytes(), before)


class _BrowserPanelScenario:
    def test_real_inputs_rebuild_viewer_and_saved_reopen(self):
        helper_path = ROOT / "oss-alternatives/tools/cad3d/advanced_browser_check.py"
        if str(helper_path.parent) not in sys.path:
            sys.path.insert(0, str(helper_path.parent))
        spec = importlib.util.spec_from_file_location("parametric_browser_helper", helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with helper.BrowserSession(BROWSER, self.root) as browser:
            browser.navigate(self.base + "/")
            browser.wait("window.opencad3d && window.opencad3d.document_sha256 && "
                         "document.querySelector('[data-parametric-create]') && "
                         "!document.querySelector('[data-parametric-create]').hidden")

            def click(selector):
                position = browser.evaluate("(function(){const e=document.querySelector(" +
                    json.dumps(selector) + ");e.scrollIntoView({block:'center'});"
                    "const r=e.getBoundingClientRect(),a=e.closest('aside').getBoundingClientRect();"
                    "const top=Math.max(0,r.top,a.top),bottom=Math.min(innerHeight,r.bottom,a.bottom);"
                    "const x=r.x+r.width/2,y=(top+bottom)/2;"
                    "return {x,y,visible:bottom>top,hit:e.contains(document.elementFromPoint(x,y))};}())")
                if not position["visible"] or not position["hit"]:
                    browser.screenshot(self.root / "click-failed.png")
                    (self.root / "click-failed.json").write_text(json.dumps({
                        "selector": selector, "position": position,
                        "dom": browser.evaluate("({notice:document.querySelector('.parametric-notice').textContent,"
                            "result:document.querySelector('.parametric-result').textContent,"
                            "html:document.querySelector('#parametric-panel').outerHTML})")}, indent=2))
                self.assertTrue(position.pop("visible"), selector + " is clipped")
                self.assertTrue(position.pop("hit"), selector + " is occluded")
                browser.call("Input.dispatchMouseEvent", {"type": "mousePressed", **position,
                                                           "button": "left", "clickCount": 1})
                browser.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **position,
                                                           "button": "left", "clickCount": 1})

            def fill(selector, value):
                click(selector)
                browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a",
                                                       "code": "KeyA", "modifiers": 2,
                                                       "windowsVirtualKeyCode": 65})
                browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a",
                                                       "code": "KeyA", "modifiers": 2,
                                                       "windowsVirtualKeyCode": 65})
                browser.call("Input.insertText", {"text": value})
                self.assertEqual(browser.evaluate("document.querySelector(" +
                                                  json.dumps(selector) + ").value"), value)

            click("[data-parametric-create]")
            browser.wait("document.querySelector('.parametric-result').dataset.volumeMm3 && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            self.assertAlmostEqual(browser.evaluate("window.opencad3d.volume_mm3"),
                                   (800 - math.pi * 16) * 8, delta=1e-5)
            self.assertGreater(browser.evaluate("window.opencad3d.pixelCount()"), 500)
            fill('[data-dimension="plate_width"]', "60 * mm")
            click("[data-parametric-rebuild]")
            browser.wait("Number(document.querySelector('.parametric-result').dataset.volumeMm3)>13000 && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            self.assertAlmostEqual(browser.evaluate("window.opencad3d.volume_mm3"),
                                   (1200 - math.pi * 36) * 12, delta=1e-5)
            self.assertEqual(browser.evaluate("document.querySelector('#parametric-panel').dataset.documentSha"),
                             digest(self.project.workspace.l1_text()))
            self.assertEqual(browser.evaluate("document.querySelectorAll('#parametric-panel legend').length"), 2)
            requests = browser.evaluate("window.__requests")
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[-1]["dimensions"], {"plate_width": "60 * mm"})
            self.assertTrue(all(request["request_id"] and request["expected_sha"] for request in requests))
            browser.screenshot(self.root / "panel-wide.png")
            before = self.project.state_bytes()
            before_ui = browser.evaluate("({sha:window.opencad3d.document_sha256,volume:window.opencad3d.volume_mm3})")
            controls = browser.evaluate("""(async function(){
              const old=window.fetch, original=document.querySelector('.parametric-result').textContent;
              const variants=['receipt','mesh','step','project'], results=[];
              try{
                for(const variant of variants){
                  window.fetch=async function(path,options){
                    if(path!=='/api/parametric'||!options||options.method!=='POST')return old(path,options);
                    const value=JSON.parse(JSON.stringify(window.__parametricLast));
                    value.base_document_sha256=value.document_sha256;
                    if(variant==='receipt')delete value.revision;
                    if(variant==='mesh')value.mesh.document_sha256='0'.repeat(64);
                    if(variant==='step')value.step.document_sha256='0'.repeat(64);
                    if(variant==='project')value.project_id='another-project';
                    return new Response(JSON.stringify(value),{status:200,
                      headers:{'Content-Type':'application/json'}});
                  };
                  try{await window.opencadParametric.submit('rebuild');results.push({variant,accepted:true});}
                  catch(error){results.push({variant,accepted:false,error:String(error),
                    preserved:document.querySelector('.parametric-result').textContent===original});}
                }
              }finally{window.fetch=old;}
              return results;
            }())""")
            self.assertTrue(all(not control["accepted"] and control["preserved"] for control in controls))
            self.assertEqual([control["error"].split(":")[1].strip() for control in controls],
                             ["parametric_response_without_project_receipt",
                              "parametric_artifact_version_mismatch", "parametric_artifact_version_mismatch",
                              "parametric_commit_receipt_conflict"])
            self.assertEqual(self.project.state_bytes(), before)
            fill('[data-parameter="radius"]', "11 * mm")
            click("[data-parametric-rebuild]")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('design_check_failed') && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            self.assertEqual(self.project.state_bytes(), before)
            self.assertEqual(browser.evaluate("({sha:window.opencad3d.document_sha256,"
                                             "volume:window.opencad3d.volume_mm3})"), before_ui)
            self.project.reopen()
            browser.navigate(self.base + "/")
            browser.wait("document.querySelector('[data-parameter=width]') && "
                         "document.querySelector('[data-parameter=width]').value==='60 * mm' && "
                         "window.opencad3d.volume_mm3>13000")
            self.assertEqual(browser.evaluate("document.querySelector('[data-parameter=radius]').value"), "width / 10")
            browser.evaluate("""(function(){
              window.__readOriginal=window.fetch;
              window.__lateRead=new Promise(resolve=>{window.__releaseLateRead=resolve;});
              window.__lateReadStarted=false;
              window.fetch=async function(path,options){
                const response=await window.__readOriginal(path,options);
                if(path==='/api/parametric'&&(!options||options.method!=='POST')){
                  window.__lateReadStarted=true;await window.__lateRead;
                }
                return response;
              };
              window.__lateRefresh=window.opencadParametric.refresh();
            }())""")
            browser.wait("window.__lateReadStarted")
            fill('[data-parameter="width"]', "50 * mm")
            browser.evaluate("""(async function(){
              window.__releaseLateRead();await window.__lateRefresh;
              window.fetch=window.__readOriginal;
            }())""")
            self.assertEqual(browser.evaluate("document.querySelector('[data-parameter=width]').value"), "50 * mm")
            click("[data-parametric-rebuild]")
            browser.wait("window.opencad3d.volume_mm3>9000 && window.opencad3d.volume_mm3<10000 && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            self.assertAlmostEqual(browser.evaluate("window.opencad3d.volume_mm3"),
                                   (1000 - math.pi * 25) * 10, delta=1e-5)
            click("select[aria-label='参数配置']")
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowDown",
                                                    "code": "ArrowDown", "windowsVirtualKeyCode": 40})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowDown",
                                                    "code": "ArrowDown", "windowsVirtualKeyCode": 40})
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter",
                                                    "code": "Enter", "windowsVirtualKeyCode": 13})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter",
                                                    "code": "Enter", "windowsVirtualKeyCode": 13})
            self.assertEqual(browser.evaluate("document.querySelector('select[aria-label=\"参数配置\"]').value"), "wide")
            click("[data-parametric-rebuild]")
            browser.wait("window.opencad3d.volume_mm3>13000 && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            self.assertAlmostEqual(browser.evaluate("window.opencad3d.volume_mm3"),
                                   (1200 - math.pi * 36) * 12, delta=1e-5)
            self.project.reopen()
            browser.navigate(self.base + "/")
            browser.wait("document.querySelector('select[aria-label=\"参数配置\"]') && "
                         "document.querySelector('select[aria-label=\"参数配置\"]').value==='wide' && "
                         "window.opencad3d.volume_mm3>13000")
            browser.call("Emulation.setDeviceMetricsOverride",
                         {"width": 360, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            overflow = browser.evaluate("(function(){const p=document.querySelector('#parametric-panel');"
                "return {page:document.documentElement.scrollWidth<=innerWidth,"
                "panel:p.scrollWidth<=p.clientWidth,fields:Array.from(p.querySelectorAll('input,select'))"
                ".every(e=>e.getBoundingClientRect().width>20 && e.getBoundingClientRect().right<=innerWidth)};}())")
            self.assertTrue(all(overflow.values()), repr(overflow))
            click("[data-parametric-reload]")
            browser.wait("document.querySelector('select[aria-label=\"参数配置\"]').value==='wide'")
            click("[data-parametric-rebuild]")
            browser.wait("document.querySelector('.parametric-result').dataset.volumeMm3 && "
                         "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")
            browser.screenshot(self.root / "panel-360.png")
            MEASUREMENTS["browser"] = {"actual_input": True, "actual_click": True,
                                        "real_viewer_pixels": browser.evaluate("window.opencad3d.pixelCount()"),
                                        "reopened_document_sha256": digest(self.project.workspace.l1_text()),
                                        "viewport_360": overflow,
                                        "ui_negative_controls": controls,
                                        "gateway_authentication": "NOT_MEASURED"}


def sources_now():
    paths = list((CORE / "cadcore").rglob("*.py"))
    paths += [APP / "server.py", APP / "release_state.py", APP / "viewer3d_page.py",
              APP / "industrial_parametric.py", Path(__file__),
              APP / "web/js/parametric-panel.js", APP / "web/css/parametric-panel.css",
              APP / "web/js/viewer3d.js", APP / "web/css/viewer3d.css",
              ROOT / "oss-alternatives/tools/cad3d/advanced_browser_check.py",
              ROOT / "oss-alternatives/tools/cad3d/web3d_browser_check.py",
              APP / "tests/legacy_browser_compat_gate.py",
              ROOT / "oss-alternatives/docs/design/industrial-parametric.md"]
    paths += [ROOT / "docs/design/parametric-configurations.md", APP / "project_gateway.py",
              APP / "project_store.py", APP / "access_control.py", APP / "secure_web.py"]
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths if path.is_file()}


def source_snapshot():
    tools = ROOT / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import acceptance
    manifest = acceptance.source_manifest(ROOT)
    owned = (
        "oss-alternatives/packages/cadcore/cadcore/parametric.py",
        "oss-alternatives/packages/cadcore/tests/test_parametric.py",
        "oss-alternatives/apps/opencad/industrial_parametric.py",
        "oss-alternatives/apps/opencad/tests/test_industrial_parametric.py",
        "docs/design/parametric-configurations.md")
    for module, expected in ((industrial, APP / "industrial_parametric.py"),
                             (server, APP / "server.py"),
                             (parametric, CORE / "cadcore/parametric.py"),
                             (subproc, CORE / "cadcore/kernel/subproc.py")):
        if Path(module.__file__).resolve() != expected.resolve():
            raise ValueError("foreign parametric runtime module: " + module.__name__)
    return {"root": str(ROOT), "manifest": manifest,
            "owned_sources": {name: manifest[name] for name in owned},
            "modes": {name: (ROOT / name).stat().st_mode & 0o777 for name in manifest},
            "head": subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                            "rev-parse", "HEAD"],
                                            text=True).strip(),
            "branch": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(ROOT), "branch", "--show-current"],
                text=True).strip(),
            "status": subprocess.check_output(
                ["git", "--no-replace-objects", "-C", str(ROOT), "status", "--porcelain=v1"],
                text=True),
            "sha256": acceptance.bootstrap.digest(manifest)}


def main():
    global RUN_ROOT, BROWSER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    parser.add_argument("--browser", help="Chromium executable; adds real browser tests")
    parser.add_argument("--browser-only", action="store_true", help="only the isolated browser scenario")
    parser.add_argument("--discovery-check", action="store_true", help="also verify unittest discovery")
    parser.add_argument("--integrity-only", action="store_true",
                        help="only creation seal and persistent schema tests, no geometry jobs")
    args = parser.parse_args()
    if args.browser_only and not args.browser:
        parser.error("--browser-only requires --browser")
    if args.integrity_only and (args.browser or args.browser_only or args.discovery_check):
        parser.error("--integrity-only cannot run browser or full discovery")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_ROOT = args.evidence_dir / stamp
    RUN_ROOT.mkdir(parents=True, exist_ok=False)
    BROWSER = args.browser
    prior = tempfile.tempdir
    tempfile.tempdir = str(RUN_ROOT.resolve())
    before = sources_now()
    frozen = source_snapshot()
    started_at = datetime.now(timezone.utc).isoformat()
    integrity_classes = [CandidateSealTests, DocumentValidationTests, ConfigurationValidationTests]
    classes = [] if args.browser_only else (
        integrity_classes if args.integrity_only else integrity_classes + [
            CandidateTests, HttpContractTests, ConfigurationGatewayTests])
    if BROWSER:
        # Do not inherit and double-count the HTTP suite in the browser run.
        classes.append(type("BrowserPanelTests", (_BrowserPanelScenario, HttpContractTests), {}))
    suite = unittest.TestSuite()
    for cls in classes:
        if issubclass(cls, _BrowserPanelScenario):
            suite.addTest(cls("test_real_inputs_rebuild_viewer_and_saved_reopen"))
        else:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    discovered = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]).countTestCases()
    try:
        with (RUN_ROOT / "tests.log").open("x") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=EvidenceResult).run(suite)
    finally:
        tempfile.tempdir = prior
    after = sources_now()
    discovery = None
    if args.discovery_check and result.wasSuccessful():
        with (RUN_ROOT / "discovery.log").open("x") as log:
            child = subprocess.run([
                sys.executable, "-B", "-m", "unittest", "discover", "-s", str(APP / "tests"),
                "-p", "test_industrial_parametric.py", "-t", str(APP), "-v"],
                cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                  TMPDIR=str(RUN_ROOT.resolve())),
                stdout=log, stderr=subprocess.STDOUT, check=False)
        text = (RUN_ROOT / "discovery.log").read_text()
        count = re.search(r"Ran (\d+) tests? in", text)
        discovery = {"rc": child.returncode, "tests": int(count[1]) if count else None,
                     "verdict": "PASS" if child.returncode == 0 and count
                     and int(count[1]) == discovered and text.rstrip().endswith("OK") else "FAIL"}
        after = sources_now()
    first_cause = result.first_cause
    report = {"verdict": "PASS" if result.testsRun and result.wasSuccessful()
              and not result.skipped and before == after else "FAIL",
              "tests": result.testsRun, "passed": result.testsRun - len(result.failures)
              - len(result.errors) - len(result.skipped), "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped),
              "source_root": str(ROOT), "sources": before, "source_drift": before != after,
              "measurements": MEASUREMENTS, "browser_requested": bool(BROWSER),
              "discovery": discovery,
              "discovery_collected_tests": discovered, "integrity_only": args.integrity_only,
              "geometry": "NOT_MEASURED" if args.integrity_only else "measured by candidate tests",
              "production_gateway": "NOT_MEASURED", "production_operations": False}
    final_source = source_snapshot()
    report.update(source_before=frozen, source_after=final_source,
                  source_snapshot_drift=frozen != final_source,
                  started_at=started_at, first_cause=first_cause,
                  finished_at=datetime.now(timezone.utc).isoformat())
    report["configuration_gateway"] = ("MEASURED_LOCAL_AUTH_HTTP_SQLITE"
                                       if "configuration_gateway" in MEASUREMENTS else "NOT_MEASURED")
    if frozen != final_source:
        report["verdict"] = "FAIL"
    if discovery is not None and discovery["verdict"] != "PASS":
        report["verdict"] = "FAIL"
    if report["first_cause"] is None and report["verdict"] != "PASS":
        report["first_cause"] = {
            "reason": "source_drift" if frozen != final_source or before != after
            else "discovery_failed" if discovery is not None and discovery["verdict"] != "PASS"
            else "empty_or_skipped_suite"}
    report["actual_rc"] = 0 if report["verdict"] == "PASS" else 1
    (RUN_ROOT / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"INDUSTRIAL_PARAMETRIC verdict={report['verdict']} tests={report['tests']} "
          f"failures={report['failures']} errors={report['errors']} evidence={RUN_ROOT}")
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
