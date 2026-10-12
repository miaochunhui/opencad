"""Real FreeCAD + SQLite CAS + loopback browser acceptance of the owned panel.

No mocks, skips, production requests or shared-source overlays. The loopback
gateway fixture exercises the published candidate/CAS contract, not production
authentication. If this older worktree lacks ProjectStore, --gateway-root
selects the chair's read-only store source; its actual hash is in the evidence.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit
import urllib.error
import urllib.request
import uuid

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
PACKAGE = ROOT / "oss-alternatives/packages/cadcore"
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(APP))

from cadcore.assembly import Assembly, Component, PartReference, Transform
from cadcore.docmodel import document_from_l1, document_to_l1, new_document
from cadcore.errors import CadError
from cadcore.kernel.subproc import SubprocessKernel
from industrial_assembly import ASSEMBLY_KEY, prepare_assembly, read_assembly
import industrial_assembly

OWNED = [
    APP / "industrial_assembly.py", APP / "web/js/assembly-panel.js",
    APP / "web/css/assembly-panel.css", Path(__file__).resolve(),
    ROOT / "docs/design/industrial-assembly-web.md",
]
BOUND = [PACKAGE / "cadcore/assembly.py", PACKAGE / "cadcore/kernel/assembly_worker.py",
         PACKAGE / "cadcore/kernel/subproc.py", PACKAGE / "cadcore/docmodel.py",
         PACKAGE / "tests/test_real_assembly.py",
         ROOT / "oss-alternatives/tools/cad3d/advanced_browser_check.py",
         ROOT / "oss-alternatives/tools/cad3d/web3d_browser_check.py",
         APP / "tests/legacy_browser_compat_gate.py"]
OUT = None
GATEWAY_ROOT = None
OBSERVATIONS = []
MM = 1_000_000


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = load_module("industrial_real_fixtures", PACKAGE / "tests/test_real_assembly.py")
# The reused fixture prepends cadcore; discovery must use the Web tests package.
sys.path[:] = [str(APP), *[value for value in sys.path if value != str(APP)]]


def hashes(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def workspace():
    value = SimpleNamespace(doc=new_document(), undo_stack=[], redo_stack=[], standard="gbt")
    value.l1_text = lambda: document_to_l1(value.doc)
    return value


def install(value, candidate):
    before = value.l1_text()
    if before != candidate.doc:
        value.undo_stack.append(before)
        value.redo_stack.clear()
    value.doc = document_from_l1(candidate.doc)


def snapshot(value):
    return {"doc": value.l1_text(), "undo": list(value.undo_stack),
            "redo": list(value.redo_stack), "standard": value.standard}


class IndustrialAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global OUT
        if OUT is None:
            OUT = Path(tempfile.mkdtemp(prefix="industrial-assembly-discovery-",
                                       dir="/hf3fs-jd/data/tmp"))
        OUT.mkdir(parents=True, exist_ok=True)
        cls.kernel = SubprocessKernel(work_dir=OUT / "kernel", keep_work_dir=True,
                                      op_timeout_s=40,
                                      extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
        cls.addClassCleanup(cls.kernel.close)
        probe = cls.kernel.probe_report()
        if not probe.available:
            raise AssertionError("real FreeCAD required: " + str(probe.as_dict()))
        source = (GATEWAY_ROOT or (
            ROOT if (APP / "project_store.py").is_file() else Path("/data/dev/opencad")))
        cls.store_path = source / "oss-alternatives/apps/opencad/project_store.py"
        cls.store_module = load_module("industrial_readonly_project_store", cls.store_path)
        if Path(sys.modules["industrial_assembly"].__file__).resolve() != OWNED[0]:
            raise AssertionError("foreign assembly adapter source")
        OBSERVATIONS.append({"probe": probe.as_dict(), "store_source": hashes([cls.store_path])})
        cls.profile = cls.kernel._profile

    @classmethod
    def tearDownClass(cls):
        if cls.kernel._profile != cls.profile:
            raise AssertionError("adapter mutated caller kernel profile")

    def setUp(self):
        self.ws = workspace()

    def record(self, name, value):
        OBSERVATIONS.append({"test": self.id(), "name": name, "value": value})

    def candidate(self, body):
        return prepare_assembly(self.ws, body, kernel=self.kernel)

    def load_ring_pin(self, transform=None):
        assembly = fixtures.fixture(transform)
        candidate = self.candidate({"action": "reopen", "assembly_json": assembly.to_json()})
        install(self.ws, candidate)
        return candidate

    def assert_reason(self, reason, action):
        with self.assertRaises(CadError) as caught:
            action()
        self.assertEqual(caught.exception.reason, reason)
        self.record("rejection", {"reason": reason, "detail": str(caught.exception)})

    def test_01_empty_read_is_explicit_and_not_a_geometry_pass(self):
        before = snapshot(self.ws)
        value = read_assembly(self.ws, {"action": "state"}, kernel=self.kernel)
        self.assertFalse(value["present"])
        self.assertIsNone(value["evaluation"])
        self.assertEqual(snapshot(self.ws), before)

    def test_02_independent_uploaded_part_has_real_mesh_and_no_early_commit(self):
        assembly = fixtures.fixture()
        body = {"action": "import_part", "part": assembly.parts[0].as_dict(),
                "component": assembly.components[0].as_dict()}
        before = snapshot(self.ws)
        value = self.candidate(body)
        self.assertEqual(snapshot(self.ws), before)
        self.assertNotIn("saved", value.response)
        self.assertNotIn("revision", value.response)
        measured = value.response["evaluation"]
        self.assertEqual(measured["backend"], "FreeCAD-BRep")
        self.assertEqual(measured["solid_count"], 1)
        self.assertGreater(len(measured["instances"][0]["mesh"]["triangles"]), 0)
        self.assertEqual(value.before_document_sha256,
                         hashlib.sha256(before["doc"].encode()).hexdigest())
        self.record("independent_part", value.response)

    def test_03_hole_gap_not_bbox_collision(self):
        value = self.load_ring_pin().response
        pair = value["evaluation"]["collisions"][0]
        self.assertEqual(pair["status"], "separated")
        self.assertAlmostEqual(pair["distance_nm"], MM, delta=2)
        self.assertEqual(pair["interference_volume_nm3"], 0)
        self.assertNotEqual(value["assembly"]["parts"][0]["document_sha256"],
                            value["assembly"]["parts"][1]["document_sha256"])
        self.record("through_hole", pair)

    def test_04_move_intersects_real_hole_wall(self):
        self.load_ring_pin()
        component = fixtures.fixture().components[1].as_dict()
        component["transform"]["translation_nm"] = [2 * MM, 0, 0]
        before = snapshot(self.ws)
        value = self.candidate({"action": "put_component", "component": component})
        pair = value.response["evaluation"]["collisions"][0]
        self.assertEqual(pair["status"], "penetrating")
        self.assertGreater(pair["interference_volume_nm3"], 0)
        self.assertEqual(snapshot(self.ws), before)
        self.record("wall_intersection", pair)

    def test_05_two_level_rotation_real_mesh_bbox(self):
        assembly = Assembly((PartReference.from_document("block", fixtures.box_document()),), (
            Component("root", transform=Transform(fixtures.rz(90), (10 * MM, 0, 0)), fixed=True),
            Component("sub", parent_id="root", transform=Transform(
                fixtures.rx(90), (0, 5 * MM, 0)), fixed=True),
            Component("block-1", "block", parent_id="sub", fixed=True),
        ))
        value = self.candidate({"action": "reopen", "assembly_json": assembly.to_json()})
        self.assertEqual(value.response["evaluation"]["properties"]["bbox_nm"],
                         [5 * MM, 0, 0, 8 * MM, 4 * MM, 2 * MM])
        self.assertAlmostEqual(value.response["evaluation"]["properties"]["volume_nm3"] / MM**3,
                               24, delta=1e-8)
        self.record("rotated_hierarchy", value.response["evaluation"]["properties"])

    def test_06_plane_axis_solve_measured_residual_and_dof(self):
        initial = fixtures.fixture(Transform(fixtures.rx(12), (MM, MM // 2, 3 * MM)))
        self.load_ring_pin(Transform(fixtures.rx(12), (MM, MM // 2, 3 * MM)))
        faces = self.candidate({"action": "checkpoint"}).response["evaluation"]["instances"]
        ring, pin = faces
        axis = {"mate_id": "axis", "kind": "axis", "a": "ring-1", "b": "pin-1",
                "a_face": next(f["face_index"] for f in ring["faces"] if f["kind"] == "axis"),
                "b_face": next(f["face_index"] for f in pin["faces"] if f["kind"] == "axis")}
        solved = self.candidate({"action": "put_mate", "mate": axis})
        install(self.ws, solved)
        plane = {"mate_id": "plane", "kind": "plane", "a": "ring-1", "b": "pin-1",
                 "a_face": min((f for f in ring["faces"] if f["kind"] == "plane"),
                               key=lambda f: f["point_nm"][2])["face_index"],
                 "b_face": min((f for f in pin["faces"] if f["kind"] == "plane"),
                               key=lambda f: f["point_nm"][2])["face_index"]}
        value = self.candidate({"action": "put_mate", "mate": plane})
        report = value.response["solver"]
        self.assertEqual(report["status"], "solved")
        self.assertEqual(report["dof"], 1)
        self.assertEqual(report["jacobian_rank"], 5)
        self.assertTrue(all(r["linear_residual_nm"] <= 100 for r in report["residuals"]))
        self.assertNotEqual(value.response["assembly"]["components"], initial.as_dict()["components"])
        self.record("mate_solver", report)

    def test_07_bad_mate_failure_preserves_doc_history(self):
        self.load_ring_pin()
        before = snapshot(self.ws)
        self.assert_reason("bad_mate_reference", lambda: self.candidate({
            "action": "put_mate", "mate": {"mate_id": "bad", "kind": "axis",
            "a": "ring-1", "b": "pin-1", "a_face": 4095, "b_face": 4095}}))
        self.assertEqual(snapshot(self.ws), before)

    def test_08_immutable_part_sha_conflict_is_not_rebinding(self):
        self.load_ring_pin()
        assembly = fixtures.fixture()
        part = replace(assembly.parts[0], part_id="pin").as_dict()
        self.assert_reason("immutable_part_conflict", lambda: self.candidate({
            "action": "import_part", "part": part,
            "component": Component("pin-2", "pin").as_dict()}))

    def test_09_digest_mismatch_upload_fails_loud(self):
        part = fixtures.fixture().parts[0].as_dict()
        part["document_sha256"] = "0" * 64
        self.assert_reason("part_digest_mismatch", lambda: self.candidate({
            "action": "import_part", "part": part,
            "component": Component("ring-1", "ring").as_dict()}))

    def test_10_unknown_fields_server_paths_bool_rotation_rejected(self):
        self.assert_reason("bad_assembly_arguments", lambda: self.candidate({
            "action": "reopen", "assembly_json": "{}", "path": "/server/file"}))
        self.assert_reason("bad_assembly_arguments", lambda: read_assembly(
            self.ws, {"action": "state", "request_id": "not-read"}, kernel=self.kernel))
        component = Component("x", "pin").as_dict()
        component["transform"]["translation_nm"][0] = True
        self.load_ring_pin()
        self.assert_reason("bad_assembly_input", lambda: self.candidate({
            "action": "put_component", "component": component}))

    def test_11_removing_referenced_parent_fails_not_silent_cascade(self):
        assembly = fixtures.fixture()
        assembly = replace(assembly, components=(
            Component("root", fixed=True),
            *tuple(replace(c, parent_id="root") for c in assembly.components)))
        install(self.ws, self.candidate({"action": "reopen", "assembly_json": assembly.to_json()}))
        before = snapshot(self.ws)
        self.assert_reason("unknown_parent", lambda: self.candidate({
            "action": "remove_component", "component_id": "root"}))
        self.assertEqual(snapshot(self.ws), before)

    def test_12_bom_export_reopen_preserves_independent_bytes(self):
        self.load_ring_pin()
        before = snapshot(self.ws)
        for action in ("state", "mesh", "collisions", "bom", "export"):
            value = read_assembly(self.ws, {"action": action}, kernel=self.kernel)
            self.assertEqual(snapshot(self.ws), before)
            self.assertEqual(sum(item["quantity"] for item in value["bom"]), 2)
        reopened = self.candidate({"action": "reopen", "assembly_json": value["assembly_json"]})
        self.assertEqual(reopened.doc, before["doc"])
        self.assertEqual(reopened.response["assembly_sha256"], value["assembly_sha256"])

    def test_13_corrupt_present_extension_not_silently_empty(self):
        self.ws.doc.extra[ASSEMBLY_KEY] = None
        self.assert_reason("bad_assembly_input", lambda: read_assembly(
            self.ws, {"action": "state"}, kernel=self.kernel))

    def test_17_contradictory_and_redundant_mates_never_return_candidate(self):
        from cadcore.assembly import Mate
        assembly = fixtures.fixture()
        faces = prepare_assembly(self.ws, {"action": "reopen",
            "assembly_json": assembly.to_json()}, kernel=self.kernel).response["evaluation"]["instances"]
        plane_indices = [min((f for f in c["faces"] if f["kind"] == "plane"),
                             key=lambda f: f["point_nm"][2])["face_index"] for c in faces]
        plane = Mate("plane", "plane", "ring-1", plane_indices[0], "pin-1", plane_indices[1])
        for mates, reason in (
            ((plane, replace(plane, mate_id="contradiction", offset_nm=MM)), "mate_solve_failed"),
            ((plane, replace(plane, mate_id="redundant")), "mate_overconstrained"),
        ):
            before = snapshot(self.ws)
            invalid = replace(assembly, mates=mates)
            self.assert_reason(reason, lambda: self.candidate({
                "action": "reopen", "assembly_json": invalid.to_json()}))
            self.assertEqual(snapshot(self.ws), before)

    def test_18_boundaries_and_no_solid_are_rejected_not_empty_success(self):
        self.assert_reason("bad_assembly_action", lambda: self.candidate({"action": []}))
        self.assert_reason("assembly_absent", lambda: self.candidate({"action": "solve"}))
        self.assert_reason("assembly_absent", lambda: read_assembly(
            self.ws, {"action": "export"}, kernel=self.kernel))
        self.assert_reason("assembly_limit", lambda: self.candidate({
            "action": "reopen", "assembly_json": " " * (8 * 1024 * 1024 + 1)}))
        part = PartReference.from_document("no-solid", new_document())
        before = snapshot(self.ws)
        with self.assertRaises(CadError):
            self.candidate({"action": "import_part", "part": part.as_dict(),
                            "component": Component("no-solid-1", "no-solid").as_dict()})
        self.assertEqual(snapshot(self.ws), before)

    def test_19_workspace_kernel_path_and_stable_l1_after_chair_install(self):
        self.ws.out_root = str(OUT / "workspace-kernel")
        self.ws.doc.extra["foreign_preserved"] = {"value": [1, "unchanged"]}
        assembly = fixtures.fixture()
        candidate = prepare_assembly(self.ws, {
            "action": "reopen", "assembly_json": assembly.to_json()})
        self.assertEqual(candidate.response["doc"], candidate.doc)
        self.assertEqual(candidate.response["document_sha256"],
                         hashlib.sha256(candidate.doc.encode()).hexdigest())
        install(self.ws, candidate)
        self.assertEqual(self.ws.l1_text(), candidate.doc)
        self.assertEqual(self.ws.doc.extra["foreign_preserved"], {"value": [1, "unchanged"]})
        reopened = self.candidate({"action": "checkpoint"})
        self.assertEqual(reopened.doc, candidate.doc)
        self.assertEqual(candidate.response["evaluation"]["assembly_sha256"],
                         candidate.response["assembly_sha256"])
        self.record("stable_candidate", {
            "document_sha256": candidate.response["document_sha256"],
            "l1_bytes": len(candidate.doc.encode()), "roundtrip_exact": True})

    def test_20_candidate_seal_real_BRep_positive(self):
        before = snapshot(self.ws)
        candidate = self.load_ring_pin()
        candidate.verify_integrity()
        self.assertEqual(candidate.response["evaluation"]["backend"], "FreeCAD-BRep")
        self.assertEqual(len(candidate.envelope_sha256), 64)
        self.assertEqual(replace(candidate).envelope_sha256, candidate.envelope_sha256)
        replace(candidate).verify_integrity()
        self.assertEqual(candidate.before_document_sha256,
                         hashlib.sha256(before["doc"].encode()).hexdigest())
        self.record("candidate_seal", {"envelope_sha256": candidate.envelope_sha256,
            "document_sha256": candidate.response["document_sha256"], "verified": True})

    def test_21_candidate_seal_replace_document_rejected(self):
        candidate = self.load_ring_pin()
        before = snapshot(self.ws)
        doc = document_from_l1(candidate.doc)
        doc.meta["title_key"] = "tampered-after-measurement"
        changed = replace(candidate, doc=document_to_l1(doc))
        self.assert_reason("candidate_changed", changed.verify_integrity)
        self.assertEqual(changed.envelope_sha256, candidate.envelope_sha256)
        self.assertEqual(snapshot(self.ws), before)

    def test_22_candidate_seal_nested_response_SHA_rejected(self):
        candidate = self.load_ring_pin()
        before = snapshot(self.ws)
        candidate.response["evaluation"]["document_sha256"] = "0" * 64
        self.assert_reason("candidate_changed", candidate.verify_integrity)
        self.assertEqual(snapshot(self.ws), before)

    def test_23_candidate_seal_nested_mesh_vertex_rejected(self):
        candidate = self.load_ring_pin()
        before = snapshot(self.ws)
        candidate.response["evaluation"]["instances"][0]["mesh"]["vertices_mm"][0][0] += 1
        self.assert_reason("candidate_changed", candidate.verify_integrity)
        self.assertEqual(snapshot(self.ws), before)

    def test_24_candidate_seal_coherent_replace_and_base_rejected(self):
        candidate = self.load_ring_pin()
        response = deepcopy(candidate.response)
        doc = document_from_l1(candidate.doc)
        doc.meta["title_key"] = "coherent-tamper"
        text = document_to_l1(doc)
        response["doc"] = text
        response["document_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        response["evaluation"]["document_sha256"] = response["document_sha256"]
        changed = replace(candidate, doc=text, response=response)
        self.assert_reason("candidate_changed", changed.verify_integrity)
        self.assertEqual(changed.envelope_sha256, candidate.envelope_sha256)
        changed_base = replace(candidate, before_document_sha256="0" * 64)
        self.assert_reason("candidate_changed", changed_base.verify_integrity)
        self.assert_reason("candidate_changed",
                           lambda: replace(candidate, envelope_sha256=None))

    def test_25_candidate_seal_creation_measurement_domain_and_canonical_bytes(self):
        candidate = self.load_ring_pin()
        for field in ("assembly_sha256", "assembly_json", "assembly"):
            response = deepcopy(candidate.response)
            response[field] = None
            self.assert_reason("candidate_changed", lambda: industrial_assembly.AssemblyCandidate(
                candidate.before_document_sha256, candidate.doc, response).verify_integrity())
        response = deepcopy(candidate.response)
        response["evaluation"]["instances"][0]["document_sha256"] = "0" * 64
        self.assert_reason("candidate_changed", lambda: industrial_assembly.AssemblyCandidate(
            candidate.before_document_sha256, candidate.doc, response).verify_integrity())
        response = deepcopy(candidate.response)
        response["evaluation"]["assembly_sha256"] = "0" * 64
        self.assert_reason("candidate_changed", lambda: industrial_assembly.AssemblyCandidate(
            candidate.before_document_sha256, candidate.doc, response).verify_integrity())
        text = json.dumps(json.loads(candidate.doc))
        response = deepcopy(candidate.response)
        response["doc"] = text
        response["document_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        response["evaluation"]["document_sha256"] = response["document_sha256"]
        self.assert_reason("candidate_changed", lambda: industrial_assembly.AssemblyCandidate(
            candidate.before_document_sha256, text, response).verify_integrity())

    def test_26_candidate_document_schema_and_part_L1_validation(self):
        empty = snapshot(self.ws)
        self.assertIsNone(industrial_assembly.validate_document(self.ws.doc))
        self.assertEqual(snapshot(self.ws), empty)
        candidate = self.load_ring_pin()
        before = snapshot(self.ws)
        assembly = industrial_assembly.validate_document(self.ws.doc)
        self.assertEqual(assembly.to_json(), candidate.response["assembly_json"])
        self.assertEqual(snapshot(self.ws), before)
        for invalid in (None, [], {}, {"schema": True}):
            doc = document_from_l1(candidate.doc)
            doc.extra[ASSEMBLY_KEY] = invalid
            with self.assertRaises(CadError):
                industrial_assembly.validate_document(doc)
        invalid = deepcopy(candidate.response["assembly"])
        invalid["parts"][0]["document_sha256"] = "0" * 64
        doc = document_from_l1(candidate.doc)
        doc.extra[ASSEMBLY_KEY] = invalid
        self.assert_reason("part_digest_mismatch",
                           lambda: industrial_assembly.validate_document(doc))
        invalid = deepcopy(candidate.response["assembly"])
        invalid["parts"][0]["l1_text"] = "{}"
        invalid["parts"][0]["document_sha256"] = hashlib.sha256(b"{}").hexdigest()
        doc.extra[ASSEMBLY_KEY] = invalid
        self.assert_reason("bad_part_reference",
                           lambda: industrial_assembly.validate_document(doc))
        self.assertEqual(snapshot(self.ws), before)

    def test_28_candidate_part_namespaces_not_silently_ignored(self):
        assembly = fixtures.fixture()
        before = snapshot(self.ws)
        for namespace in ("x_industrial_surfaces", ASSEMBLY_KEY):
            for feature_only in (True, False):
                part_doc = document_from_l1(assembly.parts[0].l1_text)
                if not feature_only:
                    part_doc.features.clear()
                    part_doc.sketches.clear()
                part_doc.extra[namespace] = (
                    {"schema": 1, "recipe": None} if namespace == "x_industrial_surfaces"
                    else assembly.as_dict())
                part = PartReference.from_document("ring", part_doc)
                nested = replace(assembly, parts=(part, assembly.parts[1]))
                doc = document_from_l1(document_to_l1(new_document()))
                doc.extra[ASSEMBLY_KEY] = nested.as_dict()
                self.assert_reason("unsupported_geometry_domain",
                                   lambda: industrial_assembly.validate_document(doc))
                self.assert_reason("unsupported_geometry_domain", lambda: self.candidate({
                    "action": "reopen", "assembly_json": nested.to_json()}))
        for namespace in ("x_industrial_surfaces", ASSEMBLY_KEY):
            doc = document_from_l1(assembly.parts[0].l1_text)
            doc.extra[ASSEMBLY_KEY] = assembly.as_dict()
            if namespace != ASSEMBLY_KEY:
                doc.extra[namespace] = None
            self.assert_reason("unsupported_geometry_domain",
                               lambda: industrial_assembly.validate_document(doc))
        self.assertEqual(snapshot(self.ws), before)

    def test_29_candidate_verifier_keeps_short_first_cause(self):
        candidate = self.load_ring_pin()
        response = deepcopy(candidate.response)
        doc = document_from_l1(candidate.doc)
        doc.extra[ASSEMBLY_KEY]["parts"][0]["document_sha256"] = "0" * 64
        text = document_to_l1(doc)
        response["doc"] = text
        response["document_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        response["evaluation"]["document_sha256"] = response["document_sha256"]
        invalid = industrial_assembly.AssemblyCandidate(
            candidate.before_document_sha256, text, response)
        with self.assertRaises(CadError) as caught:
            invalid.verify_integrity()
        self.assertEqual(caught.exception.reason, "candidate_changed")
        self.assertIn("part_digest_mismatch", str(caught.exception))
        self.assertIn("part L1 bytes", str(caught.exception))
        self.assertLess(len(str(caught.exception)), 400)
        self.assertIsInstance(caught.exception.__cause__, CadError)
        self.record("verifier_first_cause", str(caught.exception))

    def new_store(self):
        # SQLite/socket state must live on a local filesystem, outside sources.
        runtime = Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT",
                                     "/data/dev/opencad-assembly-fixtures"))
        runtime.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="web-", dir=runtime)
        self.addCleanup(directory.cleanup)
        store = self.store_module.ProjectStore(Path(directory.name) / "store")
        self.addCleanup(store.close)
        head = store.create_project("fixture-owner", title="Assembly HTTP",
                                    request_id=uuid.uuid4().hex, payload=snapshot(self.ws))
        return store, head, Path(directory.name)

    def rigid_system(self):
        assembly = fixtures.scale_fixture()
        measured = fixtures.evaluate_assembly(assembly, self.kernel, mesh=False)
        internal, external = fixtures.scale_mates(measured)
        return replace(assembly, mates=internal+external)

    def test_30_rigid_pallet_candidate_durable_configuration_reopen(self):
        assembly = self.rigid_system()
        before = snapshot(self.ws)
        candidate = self.candidate({"action": "reopen", "assembly_json": assembly.to_json()})
        candidate.verify_integrity()
        self.assertEqual(snapshot(self.ws), before)
        report = candidate.response["solver"]
        self.assertEqual((report["variables"], report["jacobian_rank"], report["dof"]), (6, 5, 1))
        self.assertEqual(len(report["rigid_mates"]), 8)
        self.assertEqual(candidate.response["evaluation"]["solid_count"], 11)
        self.assertEqual(len(candidate.response["evaluation"]["collisions"]), 55)
        self.assertTrue(all(item["mesh"]["triangles"] for item in candidate.response["evaluation"]["instances"]))
        store, head, directory = self.new_store()
        body = {"action": "reopen", "assembly_json": assembly.to_json(),
                "expected_revision": head["revision"], "expected_sha": head["sha256"]}
        request_id = uuid.uuid4().hex
        payload = {**before, "doc": candidate.doc, "undo": [before["doc"]]}
        receipt = store.commit(head["project_id"], expected_revision=head["revision"],
            expected_sha=head["sha256"], request_id=request_id, request_payload=body,
            payload=payload, response=candidate.response)
        self.assertEqual(store.read_receipt(head["project_id"], request_id,
                                           request_payload=body), receipt)
        store.close()
        reopened_store = self.store_module.ProjectStore(directory / "store")
        self.addCleanup(reopened_store.close)
        confirmed = reopened_store.read_head(head["project_id"])
        self.assertEqual(confirmed["snapshot"], payload)
        self.assertEqual(confirmed["revision"], head["revision"]+1)
        loaded = document_from_l1(confirmed["snapshot"]["doc"])
        state = read_assembly(loaded, {"action": "state"}, kernel=self.kernel)
        self.assertEqual(state["assembly_json"], candidate.response["assembly_json"])
        self.assertEqual(state["bom"], candidate.response["bom"])
        self.assertEqual(state["evaluation"], candidate.response["evaluation"])
        checkpoint = prepare_assembly(loaded, {"action": "checkpoint"}, kernel=self.kernel)
        checkpoint.verify_integrity()
        self.assertEqual(checkpoint.doc, candidate.doc)
        self.assertEqual(checkpoint.response["solver"]["jacobian_rank"], 5)
        self.assertEqual(checkpoint.response["solver"]["dof"], 1)
        self.record("rigid_durable_candidate", {"revision": confirmed["revision"],
            "snapshot_sha256": confirmed["sha256"], "document_sha256": state["document_sha256"],
            "assembly_sha256": state["assembly_sha256"], "bom": state["bom"],
            "solver": report, "pure_before_confirmation": True, "restart_exact": True})

    def test_31_rigid_invalid_candidate_keeps_workspace_head_and_receipt_absent(self):
        assembly = self.rigid_system()
        candidate = self.candidate({"action": "reopen", "assembly_json": assembly.to_json()})
        candidate.verify_integrity()
        install(self.ws, candidate)
        store, head, _ = self.new_store()
        before = snapshot(self.ws)
        for mates, reason in (
            ((replace(assembly.mates[1], offset_nm=MM), *assembly.mates[2:]), "mate_solve_failed"),
            ((*assembly.mates, replace(assembly.mates[-1], mate_id="duplicate")), "mate_overconstrained"),
        ):
            request = {"action": "reopen",
                       "assembly_json": replace(assembly, mates=mates).to_json(),
                       "expected_revision": head["revision"], "expected_sha": head["sha256"]}
            request_id = uuid.uuid4().hex
            with self.assertRaises(CadError) as caught:
                self.candidate({key: value for key, value in request.items()
                                if key not in ("expected_revision", "expected_sha")})
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(snapshot(self.ws), before)
            self.assertEqual(store.read_head(head["project_id"]), head)
            self.assertIsNone(store.read_receipt(head["project_id"], request_id,
                                                request_payload=request))
            self.record("rigid_rejected_before_confirmation", caught.exception.as_dict())

    def test_32_rigid_candidate_signed_offset_and_report_seal(self):
        assembly = self.rigid_system()
        # Positive and negative directed plane offsets both use measured geometry.
        for offset in (-8*MM, 8*MM):
            assembly = replace(assembly, mates=(*assembly.mates[:-1],
                replace(assembly.mates[-1], offset_nm=offset)))
            before = snapshot(self.ws)
            candidate = self.candidate({"action": "reopen", "assembly_json": assembly.to_json()})
            candidate.verify_integrity()
            self.assertEqual(snapshot(self.ws), before)
            report = candidate.response["solver"]
            residual = next(r for r in report["residuals"] if r["mate_id"] == "locate-plane")
            self.assertLessEqual(abs(residual["signed_linear_residual_nm"]), 100)
            self.assertEqual((report["jacobian_rank"], report["dof"]), (5, 1))
            self.record("signed_rigid_location", {"offset_nm": offset, "solver": report})
            candidate.response["solver"]["rigid_mates"][0]["status"] = "fabricated"
            self.assert_reason("candidate_changed", candidate.verify_integrity)
            self.assertEqual(snapshot(self.ws), before)

    def test_27_actual_HTTP_seal_rejection_before_DB_commit(self):
        store, head, _ = self.new_store()
        test = self
        evidence = []

        class SealHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                status, response = 200, {}
                try:
                    if self.path not in ("/candidate", "/open"):
                        raise CadError("unknown fixture route", reason="fixture_route")
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    current = store.read_head(head["project_id"])
                    if self.path == "/open":
                        doc = document_from_l1(body["doc"])
                        industrial_assembly.validate_document(doc)
                        payload, response = dict(current["snapshot"]), {"ok": True}
                        payload["doc"] = document_to_l1(doc)
                    else:
                        doc = document_from_l1(current["snapshot"]["doc"])
                        candidate = prepare_assembly(doc, body["operation"], kernel=test.kernel)
                        # Explicit post-measurement variants only in this owned
                        # loopback fixture; product code is not monkeypatched.
                        if body["mutation"] == "replace-doc":
                            changed = document_from_l1(candidate.doc)
                            changed.meta["title_key"] = "changed-after-measurement"
                            candidate = replace(candidate, doc=document_to_l1(changed))
                        elif body["mutation"] == "response-SHA":
                            candidate.response["document_sha256"] = "0" * 64
                        elif body["mutation"] == "mesh-vertex":
                            candidate.response["evaluation"]["instances"][0][
                                "mesh"]["vertices_mm"][0][0] += 1
                        elif body["mutation"] != "none":
                            raise CadError("unknown fixture mutation", reason="fixture_mutation")
                        candidate.verify_integrity()
                        payload, response = dict(current["snapshot"]), candidate.response
                        payload["undo"] = [*payload["undo"], payload["doc"]]
                        payload["redo"] = []
                        payload["doc"] = candidate.doc
                    receipt = store.commit(head["project_id"],
                        expected_revision=body["expected_revision"], expected_sha=body["expected_sha"],
                        request_id=body["request_id"], request_payload=body,
                        payload=payload, response=response)
                    response = {**receipt["response"], "revision": receipt["revision"]}
                except CadError as exc:
                    status, response = 422, {"ok": False, "error": exc.reason}
                except Exception:
                    status, response = 500, {"ok": False, "error": "fixture_failed"}
                    evidence.append({"first_cause": traceback.format_exc()})
                raw = json.dumps(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        http = ThreadingHTTPServer(("127.0.0.1", 0), SealHandler)
        thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)

        def send(route, body):
            request = urllib.request.Request(
                "http://127.0.0.1:%d/%s" % (http.server_port, route),
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            try:
                response = urllib.request.urlopen(request, timeout=60)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, json.loads(response.read())

        operation = {"action": "reopen", "assembly_json": fixtures.fixture().to_json()}
        for mutation in ("replace-doc", "response-SHA", "mesh-vertex"):
            before = store.read_head(head["project_id"])
            body = {"operation": operation, "mutation": mutation, "request_id": uuid.uuid4().hex,
                    "expected_revision": before["revision"], "expected_sha": before["sha256"]}
            status, value = send("candidate", body)
            self.assertEqual((status, value), (422, {"ok": False, "error": "candidate_changed"}))
            self.assertEqual(store.read_head(head["project_id"]), before)
            self.assertIsNone(store.read_receipt(head["project_id"], body["request_id"],
                                                request_payload=body))
            evidence.append({"mutation": mutation, "status": status, "error": value["error"],
                             "revision_unchanged": True, "receipt_absent": True})
        doc = new_document()
        doc.extra[ASSEMBLY_KEY] = None
        before = store.read_head(head["project_id"])
        body = {"doc": document_to_l1(doc), "request_id": uuid.uuid4().hex,
                "expected_revision": before["revision"], "expected_sha": before["sha256"]}
        status, value = send("open", body)
        self.assertEqual(status, 422)
        self.assertEqual(value["error"], "bad_assembly_input")
        self.assertEqual(store.read_head(head["project_id"]), before)
        self.assertIsNone(store.read_receipt(head["project_id"], body["request_id"], request_payload=body))
        body = {"operation": operation, "mutation": "none", "request_id": uuid.uuid4().hex,
                "expected_revision": before["revision"], "expected_sha": before["sha256"]}
        status, value = send("candidate", body)
        self.assertEqual(status, 200)
        self.assertEqual(value["evaluation"]["backend"], "FreeCAD-BRep")
        self.assertEqual(value["evaluation"]["solid_count"], 2)
        self.assertEqual(value["revision"], before["revision"] + 1)
        self.assertEqual(store.read_head(head["project_id"])["snapshot"]["doc"], value["doc"])
        self.assertIsNotNone(store.read_receipt(head["project_id"], body["request_id"], request_payload=body))
        self.record("seal_HTTP_DB", {"controls": evidence, "positive_status": status,
                    "scope": "owned loopback verifier/CAS fixture; not chair gateway/authentication"})

    def test_14_real_cas_restart_last_confirmed_and_duplicate_request(self):
        store, head, path = self.new_store()
        candidate = self.load_ring_pin()
        request = {"action": "reopen", "assembly_json": candidate.response["assembly_json"],
                   "expected_revision": head["revision"], "expected_sha": head["sha256"]}
        identifier = uuid.uuid4().hex
        receipt = store.commit(head["project_id"], expected_revision=head["revision"],
            expected_sha=head["sha256"], request_id=identifier, request_payload=request,
            payload=snapshot(self.ws), response=candidate.response)
        repeated = store.commit(head["project_id"], expected_revision=head["revision"],
            expected_sha=head["sha256"], request_id=identifier, request_payload=request,
            payload=snapshot(self.ws), response=candidate.response)
        self.assertEqual(receipt, repeated)
        store.close()
        reopened = self.store_module.ProjectStore(path / "store")
        self.addCleanup(reopened.close)
        actual = reopened.read_head(head["project_id"])
        self.assertEqual(actual["revision"], head["revision"] + 1)
        self.assertEqual(actual["snapshot"]["doc"], candidate.doc)
        loaded = document_from_l1(actual["snapshot"]["doc"])
        value = read_assembly(loaded, {"action": "state"}, kernel=self.kernel)
        self.assertEqual(value["evaluation"]["solid_count"], 2)
        self.record("reopened_confirmed", {"revision": actual["revision"],
                    "sha256": actual["sha256"], "document_sha256": value["document_sha256"]})

    def test_15_two_clients_real_cas_one_stale(self):
        store, head, _ = self.new_store()
        candidate = self.load_ring_pin()
        request = {"expected_revision": head["revision"], "expected_sha": head["sha256"]}
        def write(index):
            try:
                return store.commit(head["project_id"], expected_revision=head["revision"],
                    expected_sha=head["sha256"], request_id="client-%d" % index,
                    request_payload={**request, "client": index}, payload=snapshot(self.ws),
                    response=candidate.response)
            except self.store_module.RevisionConflict as exc:
                return {"conflict": True, "revision": exc.revision}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, (1, 2)))
        self.assertEqual(sum(item.get("conflict") is True for item in results), 1)
        self.assertEqual(store.read_head(head["project_id"])["revision"], head["revision"] + 1)
        self.record("dual_client_cas", results)

    def test_16_real_browser_import_pose_collision_error_and_reopen(self):
        store, head, _ = self.new_store()
        state = {"head": head, "requests": [], "errors": [], "builds": 0, "replays": 0}
        lock = threading.RLock()
        test = self
        assets = {
            "/assembly-panel.js": (OWNED[1], "text/javascript"),
            "/assembly-panel.css": (OWNED[2], "text/css"),
            "/web/js/locale-service.js": (APP / "web/js/locale-service.js", "text/javascript"),
            "/web/panel-i18n.json": (APP / "web/panel-i18n.json", "application/json"),
        }
        page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/assembly-panel.css"><div id="panel"></div>
        <script>
        window.fixtureErrors=[];window.addEventListener('error',e=>fixtureErrors.push(e.message));
        window.addEventListener('unhandledrejection',e=>fixtureErrors.push(String(e.reason)));
        </script>
        <script src="/web/js/locale-service.js"></script>
        <script>
        window.fixtureLocaleReady=(async function(){
          await window.OpenCADLocale.ready;
          window.OpenCADLocale.update({locale:"zh-CN",strings:{fixture:"locale"}});
          document.documentElement.lang="zh-CN";
          window.fixtureLocaleZh=true;
        }()).catch(function(error){window.fixtureErrors.push(String(error));throw error;});
        </script>
        <script src="/assembly-panel.js"></script><script>
        window.fixtureBase=null;
        window.fixtureTransport=async function(r){
          const body=r.body&&structuredClone(r.body);
          if(r.method==='POST'){
            body.expected_revision=fixtureBase.revision;body.expected_sha=fixtureBase.sha256;
            body.request_id=crypto.randomUUID();
          }
          const response=await fetch(r.path,r.method==='POST'?{
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
          const value=await response.json();
          if(!response.ok||!value.ok)throw new Error(value.error||value.detail);
          if(!fixtureBase||value.revision>=fixtureBase.revision)
            fixtureBase={revision:value.revision,sha256:value.sha256};
          return value;
        };
        window.fixtureLocaleReady.then(function(){
          window.panel=opencadAssembly.mount(document.getElementById('panel'),{
            transport:r=>window.fixtureTransport(r)});
        });
        </script></html>"""

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                state["requests"].append({"method": self.command, "path": self.path, "detail": format % args})

            def reply(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                route = urlsplit(self.path)
                if route.path in assets or route.path == "/":
                    raw, kind = (page.encode(), "text/html") if route.path == "/" else (
                        assets[route.path][0].read_bytes(), assets[route.path][1])
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if route.path != "/api/assembly":
                    self.reply(404, {"ok": False, "error": "not_found"})
                    return
                try:
                    with lock:
                        actual = store.read_head(head["project_id"])
                        doc = document_from_l1(actual["snapshot"]["doc"])
                        value = read_assembly(doc, {"action": parse_qs(route.query).get(
                            "action", ["state"])[0]}, kernel=test.kernel)
                        self.reply(200, {**value, **{k: actual[k] for k in
                                                   ("project_id", "revision", "sha256")}})
                except Exception as exc:
                    state["errors"].append(traceback.format_exc())
                    self.reply(422, {"ok": False, "error": getattr(exc, "reason", str(exc))})

            def do_POST(self):
                try:
                    if self.path != "/api/assembly":
                        self.reply(404, {"ok": False, "error": "not_found"})
                        return
                    count = int(self.headers["Content-Length"])
                    if count > 9 * 1024 * 1024:
                        raise ValueError("request_size")
                    body = json.loads(self.rfile.read(count))
                    with lock:
                        actual = store.read_head(head["project_id"])
                        request = {**body, "route": self.path}
                        receipt = store.read_receipt(head["project_id"], body["request_id"],
                                                     request_payload=request)
                        if receipt is not None:
                            state["replays"] += 1
                            self.reply(200, {**receipt["response"], **{k: receipt[k] for k in
                                                                   ("project_id", "revision", "sha256")}})
                            return
                        if (body["expected_revision"], body["expected_sha"]) != (
                                actual["revision"], actual["sha256"]):
                            raise test.store_module.RevisionConflict(actual["revision"], actual["sha256"])
                        doc = document_from_l1(actual["snapshot"]["doc"])
                        candidate = prepare_assembly(doc, body, kernel=test.kernel)
                        candidate.verify_integrity()
                        state["builds"] += 1
                        payload = dict(actual["snapshot"])
                        if candidate.doc != payload["doc"]:
                            payload["undo"] = [*payload["undo"], payload["doc"]]
                            payload["redo"] = []
                        payload["doc"] = candidate.doc
                        receipt = store.commit(head["project_id"],
                            expected_revision=body["expected_revision"], expected_sha=body["expected_sha"],
                            request_id=body["request_id"], request_payload=request,
                            payload=payload, response=candidate.response)
                        state["head"] = receipt
                        self.reply(200, {**receipt["response"], **{k: receipt[k] for k in
                                                               ("project_id", "revision", "sha256")}})
                except Exception as exc:
                    state["errors"].append(traceback.format_exc())
                    self.reply(getattr(exc, "status", 422), {
                        "ok": False, "error": getattr(exc, "reason", getattr(exc, "code", str(exc)))})

        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        tool_dir = ROOT / "oss-alternatives/tools/cad3d"
        sys.path.insert(0, str(tool_dir))
        browser_module = load_module("industrial_browser_helper", tool_dir / "advanced_browser_check.py")
        browser_out = OUT / "browser"
        browser_out.mkdir()
        browser = os.environ.get("OPENCAD_TEST_BROWSER",
            "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
        with browser_module.BrowserSession(browser, browser_out) as session:
            session.navigate("http://127.0.0.1:%d/" % http.server_port)
            session.wait("window.panel && panel.state && !panel.busy")
            assembly = fixtures.fixture()
            for part, component in zip(assembly.parts, assembly.components):
                # Real file-input event and form submit, not an API-only bypass.
                session.evaluate("""(function(){
                  const form=document.querySelector('[data-form=import]'),dt=new DataTransfer();
                  dt.items.add(new File([""" + json.dumps(part.l1_text) + """],"part.l1"));
                  form.elements.file.files=dt.files;
                  form.elements.part_id.value=""" + json.dumps(part.part_id) + """;
                  form.elements.component_id.value=""" + json.dumps(component.component_id) + """;
                  form.requestSubmit();})()""")
                expected = 1 if part.part_id == "ring" else 2
                session.wait("!panel.busy && panel.state.assembly && panel.state.assembly.parts.length===" +
                             str(expected))
            self.assertGreater(session.evaluate("panel.triangles"), 0)
            self.assertEqual(session.evaluate("panel.state.evaluation.collisions[0].status"), "separated")
            session.screenshot(browser_out / "assembly-desktop.png")
            session.evaluate("""(function(){
              panel.select('pin-1');const form=document.querySelector('[data-form=pose]');
              form.elements.translation_mm.value='[2,0,0]';form.requestSubmit();})()""")
            session.wait("!panel.busy && panel.state.evaluation.collisions[0].status==='penetrating'")
            accepted = session.evaluate("({doc:panel.state.doc,sha:panel.state.sha256,revision:panel.state.revision})")
            session.evaluate("""(function(){
              const form=document.querySelector('[data-form=mate]');
              form.elements.mate.value=JSON.stringify({mate_id:'bad',kind:'axis',a:'ring-1',a_face:4095,
                b:'pin-1',b_face:4095});form.requestSubmit();})()""")
            session.wait("!panel.busy && !!panel.error")
            self.assertEqual(session.evaluate(
                "({doc:panel.state.doc,sha:panel.state.sha256,revision:panel.state.revision})"), accepted)
            self.assertIn("bad_mate_reference", session.evaluate(
                "document.querySelector('[data-role=status]').textContent"))
            # Readonly refresh recovers error presentation without editing.
            session.evaluate("panel.refresh()")
            self.assertEqual(session.evaluate("panel.state.revision"), accepted["revision"])
            self.assertEqual(session.evaluate("panel.error"), None)
            # Successful mate through the actual submit/catch boundary.
            axis = session.evaluate("""(function(){
              const instances=panel.state.evaluation.instances;
              return {mate_id:'axis',kind:'axis',a:'ring-1',b:'pin-1',
                a_face:instances[0].faces.find(f=>f.kind==='axis').face_index,
                b_face:instances[1].faces.find(f=>f.kind==='axis').face_index};
            })()""")
            session.evaluate("""(function(){const form=document.querySelector('[data-form=mate]');
              form.elements.mate.value=JSON.stringify(""" + json.dumps(axis) + """);
              form.requestSubmit();})()""")
            session.wait("!panel.busy && panel.state.solver && panel.state.solver.status==='solved'")
            self.assertEqual(session.evaluate("panel.state.solver.dof"), 2)
            self.assertEqual(session.evaluate("panel.state.evaluation.collisions[0].status"), "separated")
            self.assertIn('"dof": 2', session.evaluate(
                "document.querySelector('[data-role=solver]').textContent"))
            self.record("browser_mate", session.evaluate("panel.state.solver"))
            # Numeric rotation hierarchy by real pose form; fixed local child
            # follows the newly inserted parent. First remove the locating mate.
            session.evaluate("panel.command({action:'remove_mate',mate_id:'axis'})")
            group = Component("sub", transform=Transform(fixtures.rz(90), (5 * MM, 0, 0)), fixed=True)
            session.evaluate("panel.command({action:'put_component',component:" +
                             json.dumps(group.as_dict()) + "})")
            session.evaluate("""(function(){
              panel.select('pin-1');const f=document.querySelector('[data-form=pose]');
              const c=JSON.parse(f.elements.component.value);c.parent_id='sub';c.fixed=true;
              c.transform.rotation=""" + json.dumps(fixtures.rx(90)) + """;
              f.elements.component.value=JSON.stringify(c);
              f.elements.translation_mm.value='[0,0,0]';f.requestSubmit();
            })()""")
            session.wait("!panel.busy && panel.state.assembly.components.some(c=>c.component_id==='pin-1'&&c.parent_id==='sub')")
            world = session.evaluate(
                "panel.state.evaluation.instances.find(c=>c.component_id==='pin-1').world_transform")
            self.assertEqual(world["translation_nm"], [5 * MM, 0, 0])
            self.assertAlmostEqual(world["rotation"][0][2], 1, places=7)
            self.assertIn("sub", session.evaluate("document.querySelector('[data-role=tree]').textContent"))
            self.record("browser_hierarchy", world)
            self.assertEqual(session.evaluate("opencadAssembly.nmVector('[1.125,-2,0]')"),
                             [1_125_000, -2 * MM, 0])
            invalid_vectors = session.evaluate("""(function(){
              return ['[true,0,0]','[1,2]','[1e20,0,0]'].map(v=>{
                try{opencadAssembly.nmVector(v);return false;}catch(e){return true;}
              });
            })()""")
            self.assertEqual(invalid_vectors, [True, True, True])
            # Delay is controlled input, not a guessed UI readiness timeout.
            queued = session.evaluate("""(async function(){
              const original=fixtureTransport;let active=0,maxActive=0,started=0;
              fixtureTransport=async r=>{
                active++;maxActive=Math.max(maxActive,active);started++;
                try{await new Promise(resolve=>setTimeout(resolve,850));return await original(r);}
                finally{active--;}
              };
              const initial=panel.state.revision;
              try{
                const a=panel.command({action:'checkpoint'}),b=panel.command({action:'checkpoint'});
                const disabled=panel.busy&&Array.from(document.querySelectorAll(
                  '#panel input,#panel textarea,#panel button')).every(n=>n.disabled);
                await Promise.all([a,b]);
                return {disabled,maxActive,started,revisionDelta:panel.state.revision-initial,busy:panel.busy};
              }finally{fixtureTransport=original;}
            })()""")
            self.assertEqual(queued, {"disabled": True, "maxActive": 1, "started": 2,
                                      "revisionDelta": 2, "busy": False})
            self.record("browser_serialized_delay", queued)
            rejected = session.evaluate("""(async function(){
              const before={doc:panel.state.doc,sha:panel.state.sha256,triangles:panel.triangles};
              try{await panel.command({action:'put_mate',mate:{mate_id:'rejected',kind:'axis',
                a:'ring-1',b:'pin-1',a_face:4095,b_face:4095}});return {rejected:false};}
              catch(e){return {rejected:true,error:String(e),
                unchanged:JSON.stringify(before)===JSON.stringify({
                  doc:panel.state.doc,sha:panel.state.sha256,triangles:panel.triangles})};}
            })()""")
            self.assertTrue(rejected["rejected"])
            self.assertTrue(rejected["unchanged"])
            self.assertIn("bad_mate_reference", rejected["error"])
            self.record("browser_rejected_error", rejected)
            session.evaluate("panel.refresh()")
            # Negative snapshots originate from real HTTP results, changed only
            # at the browser transport boundary to prove the guard rejects them.
            for mutation, reason in (
                ("v.doc+=' ';", "assembly_document_digest_mismatch"),
                ("v.assembly_sha256='0'.repeat(64);", "assembly_digest_mismatch"),
                ("v.evaluation.document_sha256='0'.repeat(64);", "assembly_evaluation_snapshot_mismatch"),
                ("v.evaluation.instances[0].document_sha256='0'.repeat(64);", "assembly_part_mesh_mismatch"),
                ("v.revision-=1;", "assembly_snapshot_conflict"),
            ):
                probe = session.evaluate("""(async function(){
                  const original=fixtureTransport,before=JSON.stringify({
                    doc:panel.state.doc,sha:panel.state.sha256,triangles:panel.triangles});
                  fixtureTransport=async r=>{const v=await original(r);""" + mutation + """return v;};
                  try{await panel.refresh();return {rejected:false};}
                  catch(e){return {rejected:true,error:String(e),unchanged:before===JSON.stringify({
                    doc:panel.state.doc,sha:panel.state.sha256,triangles:panel.triangles})};}
                  finally{fixtureTransport=original;}
                })()""")
                self.assertTrue(probe["rejected"], reason)
                self.assertTrue(probe["unchanged"], reason)
                self.assertIn(reason, probe["error"])
                self.record("browser_snapshot_guard", probe)
                session.evaluate("panel.refresh()")
            # Real HTTP duplicate receipt/stale clients. Do not rebuild a replay.
            http_probe = session.evaluate("""(async function(){
              const before=await (await fetch('/api/assembly?action=state')).json();
              const body={action:'checkpoint',expected_revision:before.revision,
                expected_sha:before.sha256,request_id:crypto.randomUUID()};
              async function send(b){const r=await fetch('/api/assembly',{method:'POST',
                headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
                return {status:r.status,value:await r.json()};}
              const first=await send(body),second=await send(body);
              const stale=await send({...body,request_id:crypto.randomUUID()});
              const changed=await send({...body,action:'solve'});
              const after=await (await fetch('/api/assembly?action=state')).json();
              return {first:first.status,second:second.status,same:JSON.stringify(first)===JSON.stringify(second),
                stale:stale.status,staleCode:stale.value.error,changed:changed.status,
                changedCode:changed.value.error,revisionDelta:after.revision-before.revision,
                unchangedDoc:after.doc===before.doc};
            })()""")
            self.assertEqual(http_probe, {
                "first": 200, "second": 200, "same": True, "stale": 409,
                "staleCode": "revision_conflict", "changed": 409,
                "changedCode": "idempotency_conflict", "revisionDelta": 1, "unchangedDoc": True})
            self.assertEqual(state["replays"], 1)
            self.record("browser_http_cas_receipts", http_probe)
            session.evaluate("panel.refresh()")
            save_revision = session.evaluate("panel.state.revision")
            session.evaluate("document.querySelector('[data-action=checkpoint]').click()")
            session.wait("!panel.busy && panel.state.revision>" + str(save_revision))
            saved_doc = session.evaluate("panel.state.doc")
            export = session.evaluate(
                "fetch('/api/assembly?action=export').then(r=>r.json()).then(x=>x.assembly_json)")
            session.evaluate("""(function(){const dt=new DataTransfer();
              dt.items.add(new File([""" + json.dumps(export) + """],"assembly.json"));
              const input=document.querySelector('[data-role=reopen]');input.files=dt.files;
              input.dispatchEvent(new Event('change',{bubbles:true}));})()""")
            session.wait("!panel.busy && !panel.error && panel.state.revision>" + str(save_revision + 1))
            self.assertEqual(session.evaluate("panel.state.doc"), saved_doc)
            self.assertEqual(session.evaluate("fixtureErrors"), [])
            session.call("Emulation.setDeviceMetricsOverride",
                         {"width": 420, "height": 900, "deviceScaleFactor": 2, "mobile": True})
            session.screenshot(browser_out / "assembly-narrow.png")
            self.assertTrue(session.evaluate("document.documentElement.scrollWidth<=innerWidth"))
            self.assertTrue(session.evaluate(
                "document.querySelector('canvas').getBoundingClientRect().top<900"))
            self.record("browser", {"triangles": session.evaluate("panel.triangles"),
                "revision": session.evaluate("panel.state.revision"),
                "renderer": session.evaluate("""(function(){const g=document.querySelector('canvas').getContext('webgl'),
                  e=g.getExtension('WEBGL_debug_renderer_info');return e?g.getParameter(e.UNMASKED_RENDERER_WEBGL):'unavailable';})()"""),
                "screenshots": [str(browser_out / "assembly-desktop.png"),
                                str(browser_out / "assembly-narrow.png")]})
        self.record("http", state)


class Log:
    def __init__(self, stream):
        self.stream = stream

    def write(self, value):
        self.stream.write(value)
        self.stream.flush()
        sys.stdout.write(value)

    def flush(self):
        self.stream.flush()
        sys.stdout.flush()


def verify_report(path):
    """Read-only machine check: exact sources, artifacts, counts and raw exits."""
    report = json.loads(path.read_text())
    checks = {
        "pass": report["verdict"] == "PASS",
        "counts": (report["tests"], report["failures"], report["errors"], report["skips"]) ==
                  (32, 0, 0, 0),
        "source_unchanged": report["source_unchanged"] and
                            report["source_before"] == report["source_after"],
        "current_sources": hashes(Path(p) for p in report["source_after"]) == report["source_after"],
        "no_production": report["production_requests"] == 0,
        "discovery": report["discovery"],
    }
    bad = []
    processes = 0
    for name, item in report["artifacts"].items():
        actual = path.parent / name
        if actual.stat().st_size != item["bytes"] or hashlib.sha256(
                actual.read_bytes()).hexdigest() != item["sha256"]:
            bad.append(name)
        if name.endswith("/process.json"):
            value = json.loads(actual.read_text())
            processes += 1
            if value["rc"] != 0 or not value["rc_measured"] or value["timed_out"]:
                bad.append(name)
    checks["artifacts"] = not bad and processes > 0
    checks["screenshots"] = all(
        report["artifacts"].get("browser/" + name, {}).get("bytes", 0) > 10000
        for name in ("assembly-desktop.png", "assembly-narrow.png"))
    print("INDUSTRIAL_VERIFY verdict=%s checks=%s processes=%d result=%s" % (
        "PASS" if all(checks.values()) else "FAIL", json.dumps(checks, sort_keys=True),
        processes, path))
    return 0 if all(checks.values()) else 1


def main():
    global OUT, GATEWAY_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--gateway-root", type=Path)
    parser.add_argument("--discovery", action="store_true")
    parser.add_argument("--integrity-only", action="store_true",
                        help="only candidate seal/schema controls; explicitly not full browser acceptance")
    parser.add_argument("--verify-result", type=Path,
                        help="read-only verify counts, current sources, screenshots and raw jobs")
    args = parser.parse_args()
    if args.verify_result:
        return verify_report(args.verify_result.resolve())
    GATEWAY_ROOT = args.gateway_root
    OUT = args.out or Path("/hf3fs-jd/data/tmp/opencad-industrial-assembly-20261011") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    OUT.mkdir(parents=True, exist_ok=False)
    store_root = GATEWAY_ROOT or (
        ROOT if (APP / "project_store.py").is_file() else Path("/data/dev/opencad"))
    sources = [*OWNED, *BOUND, store_root / "oss-alternatives/apps/opencad/project_store.py"]
    before = hashes(sources)
    started = time.monotonic()
    with (OUT / "tests.log").open("x") as stream:
        if args.integrity_only:
            unittest.defaultTestLoader.testNamePatterns = ["*candidate_*", "*HTTP_seal_*"]
        if args.discovery:
            suite = unittest.defaultTestLoader.discover(str(APP / "tests"),
                pattern="test_industrial_assembly.py", top_level_dir=str(APP))
            module = sys.modules.get("tests.test_industrial_assembly")
            if module is not None:
                module.OUT, module.GATEWAY_ROOT, module.OBSERVATIONS = OUT, GATEWAY_ROOT, OBSERVATIONS
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(IndustrialAssemblyTests)
        result = unittest.TextTestRunner(stream=Log(stream), verbosity=2).run(suite)
    after = hashes(sources)
    artifacts = {str(p.relative_to(OUT)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                        "bytes": p.stat().st_size}
                 for p in OUT.rglob("*") if p.is_file()}
    jobs = [json.loads(p.read_text()) for p in OUT.rglob("process.json")]
    valid_jobs = bool(jobs) and all(
        p["rc"] == 0 and p["rc_measured"] and not p["timed_out"] for p in jobs)
    valid_images = (not args.integrity_only and all(
        artifacts.get("browser/" + name, {}).get("bytes", 0) > 10000
        for name in ("assembly-desktop.png", "assembly-narrow.png")))
    complete = (result.wasSuccessful() and not result.skipped
                and result.testsRun == (13 if args.integrity_only else 32)
                and before == after and valid_jobs and (args.integrity_only or valid_images))
    report = {"verdict": "PASS" if complete else "FAIL", "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped),
              "source_before": before, "source_after": after, "source_unchanged": before == after,
              "discovery": args.discovery, "observations": OBSERVATIONS, "artifacts": artifacts,
              "duration_s": time.monotonic() - started,
              "raw_jobs_valid": valid_jobs, "raw_jobs": len(jobs),
              "screenshots_valid": valid_images,
              "scope": "candidate integrity only" if args.integrity_only else "full adapter and browser",
              "finished_at": datetime.now(timezone.utc).isoformat(),
              "production_requests": 0, "authentication": "NOT_ASSESSED",
              "chair_gateway_dispatch": "NOT_INTEGRATED"}
    (OUT / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print("INDUSTRIAL_ASSEMBLY verdict=%s tests=%d failures=%d errors=%d skips=%d evidence=%s" % (
        report["verdict"], report["tests"], report["failures"], report["errors"], report["skips"], OUT))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
