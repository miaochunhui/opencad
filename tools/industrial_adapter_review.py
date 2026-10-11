#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only-source, real-kernel candidate and cross-domain boundary review.

--scope modules probes newly integrated modules without claiming Web acceptance.
--scope gateway checks chair-frozen private HTTP admission and candidate sealing.
The scope deliberately does not rerun their existing unit suites. All scratch
files and retained errors go to unique external evidence directories. Only
explicitly selected boundaries are assessed; no industrial parity is inferred.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import http.client
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
import uuid
from unittest.mock import patch

DEFAULT_EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces")
MM = 1_000_000
SOURCE_ROOT = None
RUN_DIR = None
MODULES = {}
OBSERVATIONS = []
SOURCE_PATHS = (
    "oss-alternatives/packages/cadcore/cadcore/docmodel.py",
    "oss-alternatives/packages/cadcore/cadcore/errors.py",
    "oss-alternatives/packages/cadcore/cadcore/parametric.py",
    "oss-alternatives/packages/cadcore/cadcore/assembly.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/assembly_worker.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/subproc.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/features_registry.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/surfaces.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/surfaces_worker.py",
    "oss-alternatives/packages/cadcore/cadcore/draw/associative.py",
    "oss-alternatives/packages/cadcore/cadcore/kernel/drawing_worker.py",
    "oss-alternatives/packages/cadcore/cadcore/ops/sw_features.py",
    "oss-alternatives/packages/cadcore/cadcore/sketch/commands.py",
    "oss-alternatives/packages/cadcore/cadcore/sketch/session.py",
    "oss-alternatives/apps/opencad/server.py",
    "oss-alternatives/apps/opencad/industrial_surfaces.py",
    "oss-alternatives/apps/opencad/industrial_assembly.py",
    "oss-alternatives/apps/opencad/project_gateway.py",
    "oss-alternatives/apps/opencad/secure_web.py",
    "oss-alternatives/apps/opencad/project_store.py",
    "oss-alternatives/apps/opencad/access_control.py",
    "oss-alternatives/apps/opencad/industrial_parametric.py",
    "oss-alternatives/apps/opencad/industrial_drawing.py",
    "oss-alternatives/apps/opencad/industrial_render.py",
    "oss-alternatives/apps/opencad/web/js/assembly-panel.js",
)


class SourceDriftError(ValueError):
    """Retained evidence cannot certify the currently observed source."""


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(root):
    result = {}
    for name in SOURCE_PATHS:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("required reviewed source is missing/nonregular: " + name)
        result[name] = sha_bytes(path.read_bytes())
    return result


def load_source(root):
    global SOURCE_ROOT
    SOURCE_ROOT = root.resolve()
    package = SOURCE_ROOT / "oss-alternatives/packages/cadcore"
    app = SOURCE_ROOT / "oss-alternatives/apps/opencad"
    sys.path[:0] = [str(package), str(app)]
    for name, relative in (
        ("cadcore.docmodel", "cadcore/docmodel.py"),
        ("cadcore.errors", "cadcore/errors.py"),
        ("cadcore.parametric", "cadcore/parametric.py"),
        ("cadcore.assembly", "cadcore/assembly.py"),
        ("cadcore.kernel.subproc", "cadcore/kernel/subproc.py"),
        ("cadcore.kernel.surfaces", "cadcore/kernel/surfaces.py"),
        ("cadcore.draw.associative", "cadcore/draw/associative.py"),
        ("cadcore.ops.sw_features", "cadcore/ops/sw_features.py"),
        ("cadcore.sketch.commands", "cadcore/sketch/commands.py"),
        ("cadcore.sketch.session", "cadcore/sketch/session.py"),
        ("industrial_surfaces", None), ("industrial_assembly", None), ("server", None),
        ("project_gateway", None), ("project_store", None), ("secure_web", None),
        ("access_control", None), ("industrial_parametric", None),
    ):
        module = importlib.import_module(name)
        expected = package / relative if relative else app / (name + ".py")
        if Path(module.__file__).resolve() != expected:
            raise ValueError("foreign review module: " + name)
        MODULES[name] = module


def observe(test, **facts):
    OBSERVATIONS.append({"test": test, "sampled_at": datetime.now(timezone.utc).isoformat(), **facts})
    (RUN_DIR / "observations.json").write_text(
        json.dumps(OBSERVATIONS, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def annulus():
    """Two true sketch circles; a depth binding, not a handmade measured result."""
    model = MODULES["cadcore.docmodel"]
    session = MODULES["cadcore.sketch.session"]
    commands = MODULES["cadcore.sketch.commands"]
    features = MODULES["cadcore.ops.sw_features"]
    doc = model.new_document()
    profile = session.enter_sketch(doc, plane="XY")
    commands.add_circle(doc.sketches[0], center=(0, 0), radius_nm=10 * MM)
    commands.add_circle(doc.sketches[0], center=(0, 0), radius_nm=3 * MM)
    feature = features.extrude_boss(doc, sketch=profile, distance_nm=5 * MM)[0]
    doc.extra["parametric"] = {
        "version": 1,
        "parameters": {"depth": {"expression": "5 * mm", "unit": "length", "positive": True}},
        "configurations": {"deep": {"depth": "7 * mm"}},
        "bindings": {"boss_depth": {"kind": "feature", "target_uuid": feature,
                                     "field": "distance_nm", "expression": "depth"}},
    }
    return model.document_from_l1(model.document_to_l1(doc))


def remote_surface():
    module = MODULES["cadcore.kernel.surfaces"]
    return module.make_nurbs(
        poles_nm=[[[x * MM, y * MM, 0] for y in (0, 10)] for x in (30, 40)],
        u_degree=1, v_degree=1, u_knots=[0, 1], v_knots=[0, 1],
        u_mults=[2, 2], v_mults=[2, 2])


def mesh_volume(mesh):
    values = []
    for triangle in mesh["triangles"]:
        a, b, c = [mesh["vertices_mm"][i] for i in triangle]
        values.append((a[0]*(b[1]*c[2]-b[2]*c[1]) +
                       a[1]*(b[2]*c[0]-b[0]*c[2]) +
                       a[2]*(b[0]*c[1]-b[1]*c[0])) / 6)
    return abs(math.fsum(values))


def mixed_document(base, domain):
    document = copy.deepcopy(base)
    if domain in ("surface", "both"):
        document.extra[MODULES["industrial_surfaces"].NAMESPACE] = {
            "schema": 1, "recipe": remote_surface()}
    if domain in ("assembly", "both"):
        assembly = MODULES["cadcore.assembly"]
        part = assembly.PartReference.from_document("remote_ring", base)
        value = assembly.Assembly((part,), (assembly.Component(
            "remote_ring_1", "remote_ring",
            transform=assembly.Transform(translation_nm=(50 * MM, 0, 0))),))
        document.extra[MODULES["industrial_assembly"].ASSEMBLY_KEY] = value.as_dict()
    model = MODULES["cadcore.docmodel"]
    return model.document_from_l1(model.document_to_l1(document))


def workspace_snapshot(workspace):
    return {"doc": workspace.l1_text(), "undo": copy.deepcopy(workspace.undo_stack),
            "redo": copy.deepcopy(workspace.redo_stack),
            "mesh_cache": copy.deepcopy(getattr(workspace, "_mesh_cache", None))}


class ModuleBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = MODULES["cadcore.docmodel"]
        cls.parametric = MODULES["cadcore.parametric"]
        cls.kernel = MODULES["cadcore.kernel.subproc"].SubprocessKernel(
            work_dir=RUN_DIR / "parametric-kernel", keep_work_dir=True)
        cls.addClassCleanup(cls.kernel.close)
        cls.original = annulus()
        cls.original_bytes = cls.model.document_to_l1(cls.original)
        cls.prepared = cls.parametric.prepare_rebuild(
            cls.original, kernel=cls.kernel, configuration="deep")
        cls.prepared.verify_integrity()
        (RUN_DIR / "parametric-candidate.l1").write_text(cls.prepared.l1, encoding="utf-8")

    def test_parametric_true_mass_and_mesh_bind_to_candidate_bytes(self):
        candidate = self.prepared
        mass = self.kernel.mass_properties(candidate.shape)
        expected = 91 * math.pi * 7
        measured = candidate.mesh["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(measured, expected, delta=1e-5)
        self.assertAlmostEqual(mass.volume_nm3 / 1e18, expected, delta=1e-5)
        self.assertLess(abs(mesh_volume(candidate.mesh) - expected) / expected, 0.005)
        self.assertEqual(sha_bytes(candidate.l1.encode()), candidate.document_sha256)
        self.assertEqual(candidate.mesh["document_sha256"], candidate.document_sha256)
        self.assertEqual(self.model.document_to_l1(self.original), self.original_bytes)
        observe(self.id(), document_sha256=candidate.document_sha256,
                exact_volume_mm3=measured, independently_integrated_mesh_mm3=mesh_volume(candidate.mesh),
                expected_volume_mm3=expected, original_unchanged=True)

    def test_parametric_legal_json_byte_tamper_and_wrong_sha_rejected(self):
        candidate = self.prepared
        # Whitespace changes legal JSON bytes, not merely geometry.
        tampered = replace(candidate, l1=candidate.l1 + " ")
        wrong_sha = replace(candidate, document_sha256="0" * 64)
        for label, value in (("bytes", tampered), ("sha", wrong_sha)):
            with self.subTest(label=label), self.assertRaises(self.parametric.ParametricError) as caught:
                value.verify_integrity()
            self.assertEqual(caught.exception.reason, "candidate_changed")
            observe(self.id(), mutation=label, rejected_reason=caught.exception.reason,
                    first_cause=str(caught.exception))
        candidate.verify_integrity()

    def test_parametric_wrong_mesh_provenance_and_mass_tamper_rejected(self):
        for label in ("document_sha256", "recipe_sha256", "volume_nm3"):
            candidate = self.prepared
            mesh = copy.deepcopy(candidate.mesh)
            if label == "volume_nm3":
                mesh["properties"]["volume_nm3"] += 1
            else:
                mesh[label] = "0" * 64
            with self.subTest(label=label), self.assertRaises(self.parametric.ParametricError) as caught:
                replace(candidate, mesh=mesh).verify_integrity()
            self.assertEqual(caught.exception.reason, "candidate_changed")
            observe(self.id(), mutation=label, rejected_reason=caught.exception.reason,
                    first_cause=str(caught.exception))

    def test_parametric_nested_document_tamper_rejected(self):
        document = copy.deepcopy(self.prepared.document)
        document.features[0]["params"]["distance_nm"] += MM
        self.assertNotEqual(self.model.document_to_l1(document), self.prepared.l1)
        with self.assertRaises(self.parametric.ParametricError) as caught:
            replace(self.prepared, document=document).verify_integrity()
        self.assertEqual(caught.exception.reason, "candidate_changed")
        self.assertEqual(self.model.document_to_l1(self.original), self.original_bytes)
        observe(self.id(), mutation="nested_document", rejected_reason=caught.exception.reason,
                first_cause=str(caught.exception), original_unchanged=True)

    def test_assembly_same_name_part_changed_bytes_or_hash_rejected(self):
        assembly = MODULES["cadcore.assembly"]
        text = self.prepared.l1
        part = assembly.PartReference.from_document("ring", self.prepared.document)
        for label, data, sha in (
            ("changed_bytes", text + " ", part.document_sha256),
            ("wrong_hash", text, "0" * 64),
        ):
            with self.subTest(label=label), self.assertRaises(MODULES["cadcore.errors"].CadError) as caught:
                assembly.PartReference("ring", sha, data)
            self.assertEqual(caught.exception.reason, "part_digest_mismatch")
            observe(self.id(), mutation=label, rejected_reason=caught.exception.reason,
                    first_cause=str(caught.exception))

    def test_surface_candidate_mutability_boundary_is_explicit(self):
        server = MODULES["server"]
        adapter = MODULES["industrial_surfaces"]
        workspace = server.Workspace(server._load_cadcore(), locale="en", standard="gbt",
                                     out_root=str(RUN_DIR / "surface-workspace"))
        before = workspace.l1_text()
        prepared = adapter.prepare_surfaces(workspace, {"recipe": remote_surface()})
        original_sha = prepared.response["document_sha256"]
        prepared.doc.extra[adapter.NAMESPACE]["recipe"]["poles_nm"][0][0][0] += MM
        changed = workspace.docmodel.document_to_l1(prepared.doc)
        self.assertNotEqual(sha_bytes(changed.encode()), original_sha)
        self.assertEqual(workspace.l1_text(), before)
        observe(self.id(), candidate_verify_integrity=callable(getattr(prepared, "verify_integrity", None)),
                changed_doc_sha256=sha_bytes(changed.encode()), measured_doc_sha256=original_sha,
                workspace_unchanged=True,
                disposition="gateway must independently seal/verify candidate bytes and response")

    def _mixed_workspace(self, domain, label):
        server = MODULES["server"]
        workspace = server.Workspace(server._load_cadcore(), locale="en", standard="gbt",
                                     out_root=str(RUN_DIR / label))
        workspace.doc = mixed_document(self.prepared.document, domain)
        workspace.undo_stack[:] = [self.original_bytes]
        workspace.redo_stack[:] = [self.prepared.l1]
        # A previous valid cache is evidence of failure atomicity, not a mock kernel.
        workspace._mesh_cache = {"document_sha256": self.prepared.document_sha256,
                                 "measured_mesh": copy.deepcopy(self.prepared.mesh)}
        self.assertNotEqual(sha_bytes(workspace.l1_text().encode()),
                            workspace._mesh_cache["document_sha256"])
        return workspace

    def test_workspace_mixed_mesh_refused_preserves_document_histories_cache(self):
        for domain in ("surface", "assembly"):
            with self.subTest(domain=domain):
                workspace = self._mixed_workspace(domain, "default-mesh-" + domain)
                before = workspace_snapshot(workspace)
                try:
                    result = workspace.mesh()
                except MODULES["server"].ApiError as exc:
                    observe(self.id(), domain=domain, rejected=exc.payload(),
                            first_cause=str(exc), disposition="unsupported full-model action refused")
                else:
                    bbox = [solid["properties"]["bbox_nm"] for solid in result["solids"]]
                    observe(self.id(), domain=domain, response=result,
                            document_sha256=sha_bytes(before["doc"].encode()),
                            workspace_after=workspace_snapshot(workspace),
                            disposition="FAIL whole-document-bound mesh omitted an active domain")
                    self.fail("default mesh accepted %s+solid with bbox=%s; "
                              "extra geometry is not reconstructed" % (domain, bbox))
                self.assertEqual(workspace_snapshot(workspace), before)

    def test_workspace_mixed_step_refused_without_artifact_or_state_change(self):
        for domain in ("surface", "assembly"):
            with self.subTest(domain=domain):
                workspace = self._mixed_workspace(domain, "default-step-" + domain)
                before = workspace_snapshot(workspace)
                target = Path(workspace.out_root) / "whole-model.step"
                self.assertFalse(target.exists())
                try:
                    result = workspace.export_step("whole-model.step")
                except MODULES["server"].ApiError as exc:
                    observe(self.id(), domain=domain, rejected=exc.payload(),
                            first_cause=str(exc), disposition="unsupported full-model STEP refused")
                else:
                    data = target.read_bytes()
                    self.assertTrue(data.startswith(b"ISO-10303-21;"))
                    directory = RUN_DIR / ("step-readback-" + domain)
                    directory.mkdir()
                    (directory / "scripts").mkdir()
                    # Invoke the real STEP reader/HLR worker against actual exported bytes.
                    worker = SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/kernel/drawing_worker.py"
                    (directory / "scripts/drawing_worker.py").write_bytes(worker.read_bytes())
                    job = {"source_doc_sha256": sha_bytes(before["doc"].encode()),
                           "model_version": 10, "step_sha256": sha_bytes(data),
                           "views": ["top"], "sections": [], "dimensions": []}
                    (directory / "model.step").write_bytes(data)
                    (directory / "job.json").write_text(json.dumps(job), encoding="utf-8")
                    environment = dict(os.environ, OPENCAD_DRAWING_JOB=str(directory / "job.json"),
                                       PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen")
                    drawings = MODULES["cadcore.draw.associative"]
                    drawings.run_logged(
                        [self.kernel.probe_report().executable,
                         str(directory / "scripts/drawing_worker.py")],
                        directory, "worker", env=environment)
                    readback = json.loads((directory / "worker-result.json").read_text())
                    observe(self.id(), domain=domain, exported=result, readback=readback,
                            artifact_sha256=sha_bytes(data),
                            state_unchanged=workspace_snapshot(workspace) == before,
                            disposition="FAIL STEP contains only local feature solid")
                    self.fail("default STEP accepted %s+solid: real readback bbox=%s"
                              % (domain, readback["bbox_nm"]))
                self.assertFalse(target.exists())
                self.assertEqual(workspace_snapshot(workspace), before)

    def test_independent_domain_reads_coexist_and_keep_real_geometry(self):
        workspace = self._mixed_workspace("both", "coexisting-panels")
        before = workspace_snapshot(workspace)
        surfaces = MODULES["industrial_surfaces"].read_surfaces(workspace, {})
        assembly = MODULES["industrial_assembly"].read_assembly(
            workspace, {"action": "mesh"}, kernel=self.kernel)
        self.assertEqual(surfaces["document_sha256"], sha_bytes(before["doc"].encode()))
        self.assertEqual(assembly["document_sha256"], sha_bytes(before["doc"].encode()))
        surface_mesh = surfaces["result"]["mesh"]
        self.assertTrue(surface_mesh["triangles"])
        self.assertGreater(max(point[0] for point in surface_mesh["vertices_mm"]), 39.99)
        instances = assembly["evaluation"]["instances"]
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0]["mesh"]["triangles"])
        self.assertGreater(max(point[0] for point in instances[0]["mesh"]["vertices_mm"]), 59.99)
        self.assertEqual(workspace_snapshot(workspace), before)
        observe(self.id(), surface_bbox_mm=surfaces["result"]["properties"]["bbox_mm"],
                assembly_bbox_nm=assembly["evaluation"]["properties"]["bbox_nm"],
                domain_scope="separate panel reads, not combined geometry",
                workspace_unchanged=True)

    def test_drawing_solid_positive_has_real_hole_not_bbox_only(self):
        drawings = MODULES["cadcore.draw.associative"]
        snapshot = drawings.rebuild_drawing(
            self.prepared.document, model_version=7, spec={"views": ["top"]},
            kernel=self.kernel, work_dir=RUN_DIR / "drawing-positive")
        value = snapshot.to_dict()
        self.assertAlmostEqual(value["volume_nm3"] / 1e18, 91 * math.pi * 7, delta=1e-5)
        self.assertEqual(value["source_doc_sha256"], self.prepared.document_sha256)
        curves = value["views"][0]["curves"]
        observe(self.id(), drawing=value, disposition="positive real HLR control")
        # Exact HLR must retain the inner circular hole, not just model bounds.
        self.assertTrue(any(row.get("radius_nm") == 3 * MM for row in curves), curves)

    def test_drawing_mixed_remote_surface_rejected_not_silently_omitted(self):
        drawings = MODULES["cadcore.draw.associative"]
        module = MODULES["cadcore.kernel.surfaces"]
        shape = module.SurfaceKernel(evidence_dir=RUN_DIR / "remote-surface").build(remote_surface())
        self.assertGreaterEqual(shape["properties"]["bbox_mm"][3], 39.99)
        document = copy.deepcopy(self.prepared.document)
        document.extra["x_industrial_surfaces"] = {"schema": 1, "recipe": remote_surface()}
        before = self.model.document_to_l1(document)
        try:
            snapshot = drawings.rebuild_drawing(document, model_version=8, spec={"views": ["top"]},
                kernel=self.kernel, work_dir=RUN_DIR / "drawing-mixed-surface")
        except drawings.AssociativeDrawingError as exc:
            observe(self.id(), rejected_reason=exc.reason, first_cause=str(exc),
                    source_sha256=sha_bytes(before.encode()), disposition="unsupported domain rejected")
        else:
            value = snapshot.to_dict()
            observe(self.id(), drawing=value, surface_bbox_mm=shape["properties"]["bbox_mm"],
                    source_sha256=sha_bytes(before.encode()),
                    disposition="FAIL mixed geometry accepted without supported-domain refusal")
            self.fail("mixed-domain drawing accepted: measured solid bbox=%s vs actual remote surface bbox=%s"
                      % (value["bbox_nm"], shape["properties"]["bbox_mm"]))
        finally:
            self.assertEqual(self.model.document_to_l1(document), before)

    def test_drawing_mixed_remote_assembly_rejected_not_silently_omitted(self):
        module = MODULES["cadcore.assembly"]
        drawings = MODULES["cadcore.draw.associative"]
        adapter = MODULES["industrial_assembly"]
        part = module.PartReference.from_document("remote_ring", self.prepared.document)
        assembly = module.Assembly((part,), (module.Component(
            "remote_ring_1", "remote_ring", transform=module.Transform(translation_nm=(50 * MM, 0, 0))),))
        measured = module.evaluate_assembly(assembly, self.kernel, mesh=True)
        self.assertGreater(measured["properties"]["bbox_nm"][3], 59 * MM)
        document = copy.deepcopy(self.prepared.document)
        document.extra[adapter.ASSEMBLY_KEY] = assembly.as_dict()
        document = self.model.document_from_l1(self.model.document_to_l1(document))
        before = self.model.document_to_l1(document)
        try:
            snapshot = drawings.rebuild_drawing(document, model_version=9, spec={"views": ["top"]},
                kernel=self.kernel, work_dir=RUN_DIR / "drawing-mixed-assembly")
        except drawings.AssociativeDrawingError as exc:
            observe(self.id(), rejected_reason=exc.reason, first_cause=str(exc),
                    source_sha256=sha_bytes(before.encode()), disposition="unsupported domain rejected")
        else:
            value = snapshot.to_dict()
            observe(self.id(), drawing=value, assembly_bbox_nm=measured["properties"]["bbox_nm"],
                    source_sha256=sha_bytes(before.encode()),
                    disposition="FAIL mixed geometry accepted without supported-domain refusal")
            self.fail("mixed-domain drawing accepted: solid bbox=%s vs true assembly bbox=%s"
                      % (value["bbox_nm"], measured["properties"]["bbox_nm"]))
        finally:
            self.assertEqual(self.model.document_to_l1(document), before)


def git_read(root, *args):
    child = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if child.returncode:
        raise ValueError("read-only Git anchor command failed rc=%s" % child.returncode)
    return child.stdout.strip()


def readback_results(paths, root, head, sources):
    """Verify retained evidence against live source, without retrying its tests."""
    records = []
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("duplicate readback evidence")
    for path in paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        if Path(result["source_root"]).resolve() != root:
            raise ValueError("evidence belongs to another source root: " + str(path))
        if result["head_before"] != head or result["head_after"] != head:
            raise SourceDriftError("evidence HEAD does not match current source: " + str(path))
        changed = sorted(name for name in set(sources) | set(result["sources_before"]) |
                         set(result["sources_after"]) if result["sources_before"].get(name) !=
                         sources.get(name) or result["sources_after"].get(name) != sources.get(name))
        if changed:
            observe("readback_source_mismatch", result_path=str(path), changed_paths=changed)
            raise SourceDriftError("evidence source fingerprint differs from live files: %s paths=%s"
                                   % (path, ", ".join(changed)))
        if result["source_drift"] or result["tests"] <= 0 or result["skipped"]:
            raise ValueError("drifted, empty or skipped evidence: " + str(path))
        tests = (path.parent / "tests.log").read_text(encoding="utf-8")
        command = (path.parent / "command.log").read_text(encoding="utf-8")
        if "Ran %s test" % result["tests"] not in tests:
            raise ValueError("missing real unittest summary: " + str(path))
        failed = bool(result["failures"] or result["errors"])
        marker = "FAILED (" if failed else "\nOK\n"
        verdict = "FAIL" if failed else "GATEWAY_BOUNDARIES_PASS"
        if marker not in tests or result["verdict"] != verdict:
            raise ValueError("evidence verdict/log disagrees: " + str(path))
        if "verdict=%s tests=%s failures=%s errors=%s skipped=%s" % (
                verdict, result["tests"], result["failures"], result["errors"],
                result["skipped"]) not in command:
            raise ValueError("command summary disagrees with result: " + str(path))
        records.append({"path": str(path.resolve()), "result_sha256": sha_bytes(path.read_bytes()),
                        "tests_log_sha256": sha_bytes(tests.encode()),
                        "verdict": verdict, "tests": result["tests"],
                        "failures": result["failures"], "errors": result["errors"],
                        "original_started_at": result["started_at"],
                        "original_finished_at": result["finished_at"]})
    return {"readback_records": records, "tests_reexecuted": False,
            "verdict": ("READBACK_MATCH_WITH_OPEN_FINDINGS" if any(
                record["verdict"] == "FAIL" for record in records) else "READBACK_MATCH")}


def scan_owned(root):
    """Reuse the repository credential scanner; never print matched bytes."""
    scanner_path = root / "tools/bootstrap_from_infra.py"
    spec = importlib.util.spec_from_file_location("review_bootstrap_scanner", scanner_path)
    scanner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = scanner
    spec.loader.exec_module(scanner)
    owned = {}
    seat = Path(__file__).resolve().parents[1]
    for name in ("tools/industrial_adapter_review.py", "docs/design/industrial-adapter-review.md"):
        data = scanner.regular_bytes(seat / name)
        scanner.scan_bytes(name, data)
        owned[name] = sha_bytes(data)
    return {"owned_files_sha256": owned,
            "credential_scan": {"files": len(owned), "findings": 0,
                                "scanner_sha256": sha_bytes(scanner_path.read_bytes())}}


class GatewayBoundaryTests(unittest.TestCase):
    """Real private HTTP/store; only candidate handoff mutations are injected."""

    @classmethod
    def setUpClass(cls):
        cls.model = MODULES["cadcore.docmodel"]
        cls.kernel = MODULES["cadcore.kernel.subproc"].SubprocessKernel(
            work_dir=RUN_DIR / "gateway-fixture-kernel", keep_work_dir=True)
        cls.addClassCleanup(cls.kernel.close)
        prepared = MODULES["cadcore.parametric"].prepare_rebuild(
            annulus(), kernel=cls.kernel, configuration="deep")
        prepared.verify_integrity()
        cls.part_doc = prepared.document
        assembly = MODULES["cadcore.assembly"]
        cls.part = assembly.PartReference.from_document("remote_ring", cls.part_doc)
        cls.component = assembly.Component("remote_ring_1", "remote_ring",
            transform=assembly.Transform(translation_nm=(50 * MM, 0, 0)))
        cls.assembly_body = {"action": "import_part", "part": cls.part.as_dict(),
                             "component": cls.component.as_dict()}
        # SQLite WAL needs local storage; never copy authentication data into evidence.
        runtime = Path("/data/tmp/opencad-industrial-adapter-review") / RUN_DIR.name
        runtime.mkdir(parents=True, exist_ok=False)
        filesystem = subprocess.check_output(
            ["stat", "-f", "-c", "%T", str(runtime)], text=True).strip()
        if filesystem not in ("ext2/ext3", "xfs", "btrfs", "zfs"):
            raise ValueError("SQLite fixture runtime must be local persistent storage: " + filesystem)
        observe("gateway_fixture", local_runtime=str(runtime), filesystem=filesystem,
                credential_values_logged=False)
        cls.auth = MODULES["access_control"].AuthController(runtime / "accounts", allow_registration=True)
        cls.addClassCleanup(cls.auth.close)
        password = uuid.uuid4().hex
        cls.auth.register("reviewer", password)
        cls.store = MODULES["project_store"].ProjectStore(runtime / "projects")
        cls.addClassCleanup(cls.store.close)
        cls.gateway = MODULES["project_gateway"].ProjectGateway(
            cls.auth, cls.store, RUN_DIR / "gateway-requests", secure_cookies=False)
        cls.httpd = MODULES["secure_web"].build_server(cls.gateway)
        cls.port = cls.httpd.server_address[1]
        cls.origin = "http://127.0.0.1:%s" % cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)
        status, headers, data = cls.call("POST", "/api/auth/login",
                                         {"username": "reviewer", "password": password}, login=True)
        if status != 200 or data.get("ok") is not True:
            raise AssertionError("real HTTP login failed, status=%s" % status)
        cls.cookie = headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def stop_server(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        if cls.thread.is_alive():
            raise AssertionError("private HTTP fixture did not stop")

    @classmethod
    def call(cls, method, route, body=None, *, head=None, login=False, raw_bytes=False):
        headers = {"Origin": cls.origin, "X-OpenCAD-Request": "1"}
        if not login:
            headers["Cookie"] = cls.cookie
        if head:
            headers["X-OpenCAD-Project"] = head["project_id"]
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        try:
            connection.request(method, route,
                               body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            value = json.loads(raw) if not raw_bytes and response.getheader(
                "Content-Type", "").startswith("application/json") else raw
            return response.status, dict(response.getheaders()), value
        finally:
            connection.close()

    def create(self):
        status, _, head = self.call("POST", "/api/projects", {
            "title": "Independent domain audit", "request_id": uuid.uuid4().hex})
        self.assertEqual(status, 201, head)
        return head

    def mutate(self, head, route, fields):
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, **fields}
        return self.call("POST", route, body, head=head), body

    def domain_project(self, domain):
        head = self.create()
        if domain == "part":
            fields, route = {"doc": self.model.document_to_l1(self.part_doc)}, "/api/open"
        elif domain == "surface":
            fields, route = {"recipe": remote_surface()}, "/api/surfaces"
        else:
            fields, route = copy.deepcopy(self.assembly_body), "/api/assembly"
        (status, _, updated), _ = self.mutate(head, route, fields)
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["revision"], 2)
        return updated

    def assert_refused_atomic(self, head, method, route, fields=None, *, code="unsupported_geometry_domain"):
        before = self.store.read_head(head["project_id"])
        if method == "POST":
            (status, _, response), body = self.mutate(head, route, fields or {})
        else:
            status, _, response = self.call(method, route, head=head)
            body = None
        after = self.store.read_head(head["project_id"])
        receipt = (self.store.read_receipt(head["project_id"], body["request_id"],
                   request_payload={**body, "route": route}) if body else None)
        observe(self.id(), route=route, method=method, status=status,
                error=response.get("error") if isinstance(response, dict) else "binary_response",
                detail=response.get("detail") if isinstance(response, dict) else None,
                head_unchanged=after == before, receipt_present=receipt is not None,
                before_revision=before["revision"], after_revision=after["revision"])
        self.assertEqual(status, 422, response if isinstance(response, dict) else "unexpected artifact")
        self.assertEqual(response.get("error"), code)
        self.assertEqual(after, before)
        self.assertIsNone(receipt)

    def test_gateway_full_model_domain_matrix_refuses_before_publication(self):
        for domain in ("surface", "assembly"):
            head = self.domain_project(domain)
            for route in ("/api/mesh", "/api/step", "/api/dxf", "/api/pdf",
                          "/api/drawing", "/api/drawing/svg", "/api/drawing/pdf", "/api/drawing/dxf"):
                with self.subTest(domain=domain, route=route):
                    self.assert_refused_atomic(head, "GET", route)
            for route, fields in (
                ("/api/render", {"width": 64, "height": 64, "backend": "cpu"}),
                ("/api/clearance", {"translation_nm": [MM, 0, 0]}),
                ("/api/drawing", {"definition": {"views": ["top"]}}),
                ("/api/parametric", {"action": "rebuild", "expected_document_sha256":
                    sha_bytes(self.store.read_head(head["project_id"])["snapshot"]["doc"].encode())}),
                ("/api/command", {"command": "add_line", "args": {"p1": [0, 0], "p2": [MM, 0]}}),
            ):
                with self.subTest(domain=domain, route=route):
                    self.assert_refused_atomic(head, "POST", route, fields)

    def test_gateway_cross_writes_and_mixed_open_refused_atomically(self):
        for domain, route, fields in (
            ("part", "/api/surfaces", {"recipe": remote_surface()}),
            ("part", "/api/assembly", self.assembly_body),
            ("surface", "/api/assembly", self.assembly_body),
            ("assembly", "/api/surfaces", {"recipe": remote_surface()}),
        ):
            with self.subTest(domain=domain, route=route):
                self.assert_refused_atomic(self.domain_project(domain), "POST", route, fields)
        for domain in ("surface", "assembly", "both"):
            with self.subTest(mixed_open=domain):
                doc = mixed_document(self.part_doc, domain)
                self.assert_refused_atomic(self.create(), "POST", "/api/open",
                                          {"doc": self.model.document_to_l1(doc)})

    def test_gateway_malformed_single_industrial_domain_open_refused(self):
        for key, value, expected_status, expected_error in (
            ("x_industrial_surfaces", {"schema": 1, "recipe": {"op": "nurbs"}},
             400, "bad_surface_arguments"),
            ("industrial_assembly_v1", {"schema": 1, "parts": [], "components": []},
             422, "bad_assembly_input"),
        ):
            with self.subTest(namespace=key):
                head = self.create()
                before = self.store.read_head(head["project_id"])
                doc = self.model.document_from_l1(before["snapshot"]["doc"])
                doc.extra[key] = value
                text = self.model.document_to_l1(doc)
                self.assertFalse(self.model.load_blocking_problems(self.model.document_from_l1(text)))
                (status, _, response), body = self.mutate(head, "/api/open", {"doc": text})
                after = self.store.read_head(head["project_id"])
                observe(self.id(), namespace=key, status=status,
                        error=response.get("error"), before_revision=before["revision"],
                        after_revision=after["revision"], head_unchanged=after == before)
                self.assertEqual(status, expected_status,
                                 "malformed domain rejection status differs: %s" % response)
                self.assertIs(response.get("ok"), False)
                self.assertEqual(response.get("error"), expected_error)
                self.assertEqual(after, before)
                self.assertIsNone(self.store.read_receipt(head["project_id"], body["request_id"],
                    request_payload={**body, "route": "/api/open"}))

    def test_gateway_real_surface_candidate_tamper_cannot_commit(self):
        adapter = MODULES["industrial_surfaces"]
        real_prepare = adapter.prepare_surfaces
        for mutation in ("doc", "hash", "mesh"):
            with self.subTest(mutation=mutation):
                head = self.create()
                before = self.store.read_head(head["project_id"])
                measured = []

                def prepare(workspace, body):
                    candidate = real_prepare(workspace, body)
                    self.assertAlmostEqual(candidate.response["result"]["properties"]["area_mm2"], 100)
                    self.assertTrue(candidate.response["result"]["mesh"]["triangles"])
                    original_sha = candidate.response["document_sha256"]
                    if mutation == "doc":
                        candidate.doc.extra[adapter.NAMESPACE]["recipe"]["poles_nm"][0][0][0] += MM
                    elif mutation == "hash":
                        candidate.response["document_sha256"] = "0" * 64
                    else:
                        candidate.response["result"]["mesh"]["vertices_mm"][0][0] += 100
                    measured.append({"mutation": mutation, "measured_sha256": original_sha,
                                     "candidate_sha256": sha_bytes(
                                         self.model.document_to_l1(candidate.doc).encode())})
                    return candidate

                with patch.object(adapter, "prepare_surfaces", new=prepare):
                    (status, _, response), body = self.mutate(head, "/api/surfaces",
                                                              {"recipe": remote_surface()})
                after = self.store.read_head(head["project_id"])
                receipt = self.store.read_receipt(head["project_id"], body["request_id"],
                                  request_payload={**body, "route": "/api/surfaces"})
                self.assertEqual(len(measured), 1)
                observe(self.id(), **measured[0], status=status, error=response.get("error"),
                        response_document_sha256=response.get("document_sha256"),
                        committed_document_sha256=sha_bytes(after["snapshot"]["doc"].encode()),
                        head_unchanged=after == before, receipt_present=receipt is not None,
                        before_revision=before["revision"], after_revision=after["revision"])
                self.assertNotEqual(status, 200, "tampered %s real candidate committed" % mutation)
                self.assertFalse(response.get("ok"))
                self.assertEqual(after, before)
                self.assertIsNone(receipt)

    def test_gateway_real_parametric_candidate_tamper_refused_atomically(self):
        adapter = MODULES["industrial_parametric"]
        real_prepare = adapter.prepare_parametric
        for mutation in ("bytes", "hash", "mesh"):
            with self.subTest(mutation=mutation):
                head = self.domain_project("part")
                before = self.store.read_head(head["project_id"])
                called = []

                def prepare(workspace, body):
                    candidate = real_prepare(workspace, body)
                    candidate.verify_integrity()
                    self.assertAlmostEqual(candidate.response["mesh"]["solids"][0]["properties"][
                        "volume_nm3"] / 1e18, 91 * math.pi * 7, delta=1e-5)
                    called.append(mutation)
                    if mutation == "bytes":
                        return replace(candidate, doc=candidate.doc + " ")
                    if mutation == "hash":
                        candidate.response["document_sha256"] = "0" * 64
                    else:
                        candidate.response["mesh"]["solids"][0]["properties"]["volume_nm3"] += 1
                    return candidate

                with patch.object(adapter, "prepare_parametric", new=prepare):
                    (status, _, response), body = self.mutate(head, "/api/parametric", {
                        "action": "rebuild", "configuration": "deep", "expected_document_sha256":
                        sha_bytes(before["snapshot"]["doc"].encode())})
                after = self.store.read_head(head["project_id"])
                self.assertEqual(called, [mutation])
                observe(self.id(), mutation=mutation, status=status, error=response.get("error"),
                        head_unchanged=after == before)
                self.assertEqual(status, 422, response)
                self.assertEqual(response["error"], "candidate_changed")
                self.assertEqual(after, before)
                self.assertIsNone(self.store.read_receipt(head["project_id"], body["request_id"],
                    request_payload={**body, "route": "/api/parametric"}))

    def test_gateway_real_assembly_candidate_tamper_cannot_commit(self):
        adapter = MODULES["industrial_assembly"]
        real_prepare = adapter.prepare_assembly
        for mutation in ("doc", "hash", "mesh"):
            with self.subTest(mutation=mutation):
                head = self.create()
                before = self.store.read_head(head["project_id"])
                called = []

                def prepare(workspace, body):
                    candidate = real_prepare(workspace, body)
                    instances = candidate.response["evaluation"]["instances"]
                    self.assertEqual(len(instances), 1)
                    self.assertAlmostEqual(instances[0]["properties"]["volume_nm3"] / 1e18,
                                           91 * math.pi * 7, delta=1e-5)
                    self.assertGreater(max(p[0] for p in instances[0]["mesh"]["vertices_mm"]), 59.99)
                    called.append(mutation)
                    if mutation == "doc":
                        document = self.model.document_from_l1(candidate.doc)
                        document.extra[adapter.ASSEMBLY_KEY]["components"][0][
                            "transform"]["translation_nm"][0] += 20 * MM
                        return replace(candidate, doc=self.model.document_to_l1(document))
                    if mutation == "hash":
                        candidate.response["document_sha256"] = "0" * 64
                    else:
                        instances[0]["mesh"]["vertices_mm"][0][0] += 100
                    return candidate

                with patch.object(adapter, "prepare_assembly", new=prepare):
                    (status, _, response), body = self.mutate(head, "/api/assembly",
                                                              copy.deepcopy(self.assembly_body))
                after = self.store.read_head(head["project_id"])
                receipt = self.store.read_receipt(head["project_id"], body["request_id"],
                                  request_payload={**body, "route": "/api/assembly"})
                self.assertEqual(called, [mutation])
                observe(self.id(), mutation=mutation, status=status, error=response.get("error"),
                        response_document_sha256=response.get("document_sha256"),
                        committed_document_sha256=sha_bytes(after["snapshot"]["doc"].encode()),
                        head_unchanged=after == before, receipt_present=receipt is not None,
                        before_revision=before["revision"], after_revision=after["revision"])
                self.assertNotEqual(status, 200, "tampered %s real assembly candidate committed" % mutation)
                self.assertFalse(response.get("ok"))
                self.assertEqual(after, before)
                self.assertIsNone(receipt)

    def test_gateway_readonly_command_shortcut_obeys_strict_domain_contract(self):
        head = self.domain_project("surface")
        before = self.store.read_head(head["project_id"])
        status, _, response = self.call("POST", "/api/command",
            {"command": "precision_status", "args": {}}, head=head)
        after = self.store.read_head(head["project_id"])
        observe(self.id(), status=status, command="precision_status", response=response,
                head_unchanged=after == before,
                disposition="strict announced command policy: only undo/redo allowed")
        self.assertEqual(after, before)
        self.assertEqual(status, 422, "read-only command shortcut bypasses announced domain admission")
        self.assertEqual(response["error"], "unsupported_geometry_domain")

    def test_gateway_assembly_domain_read_save_undo_redo_positive(self):
        head = self.domain_project("assembly")
        before = self.store.read_head(head["project_id"])
        status, _, read = self.call("GET", "/api/assembly?action=mesh", head=head)
        self.assertEqual(status, 200, read)
        self.assertEqual(len(read["evaluation"]["instances"]), 1)
        self.assertGreater(max(p[0] for p in read["evaluation"]["instances"][0][
            "mesh"]["vertices_mm"]), 59.99)
        status, headers, document_bytes = self.call("GET", "/api/document", head=head, raw_bytes=True)
        self.assertEqual(status, 200)
        self.assertEqual(document_bytes.decode(), before["snapshot"]["doc"])
        self.assertEqual(headers["X-OpenCAD-Document-SHA256"], sha_bytes(document_bytes))
        self.assertEqual(self.store.read_head(head["project_id"]), before)
        (status, _, saved), _ = self.mutate(head, "/api/save", {})
        self.assertEqual(status, 200, saved)
        saved_snapshot = self.store.read_head(head["project_id"])["snapshot"]
        self.assertEqual(saved_snapshot, before["snapshot"])
        (status, _, undone), _ = self.mutate(saved, "/api/command", {"command": "undo", "args": {}})
        self.assertEqual(status, 200, undone)
        undo_snapshot = self.store.read_head(head["project_id"])["snapshot"]
        self.assertNotIn(MODULES["industrial_assembly"].ASSEMBLY_KEY,
                         self.model.document_from_l1(undo_snapshot["doc"]).extra)
        self.assertEqual(len(undo_snapshot["redo"]), 1)
        (status, _, redone), _ = self.mutate(undone, "/api/command", {"command": "redo", "args": {}})
        self.assertEqual(status, 200, redone)
        self.assertEqual(self.store.read_head(head["project_id"])["snapshot"], before["snapshot"])
        observe(self.id(), explicit_domain="assembly", read_revision=head["revision"],
                save_revision=saved["revision"], undo_revision=undone["revision"],
                redo_revision=redone["revision"], exact_snapshot_restored=True,
                whole_model_geometry_claimed=False)


def main():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expect-head", help="chair-supplied frozen commit; checked before and after")
    parser.add_argument("--scope", choices=("modules", "gateway"), default="modules")
    parser.add_argument("--case", action="append",
                        help="exact test method; omitted means all new review cases")
    parser.add_argument("--readback-result", type=Path, action="append",
                        help="verify retained gateway evidence/source/logs, without running tests")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc)
    RUN_DIR = args.evidence_dir.resolve() / (stamp.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)
    root = args.source_root.resolve()
    if RUN_DIR.is_relative_to(root) or RUN_DIR.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("review evidence must be outside source and seat")
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    (RUN_DIR / "write-probe").touch(exist_ok=False)
    scratch = RUN_DIR / "scratch"
    scratch.mkdir()
    os.environ["TMPDIR"] = str(scratch)
    os.environ["CADCORE_SUBPROC_KEEP_WORK"] = "1"
    tempfile.tempdir = None
    print("INDUSTRIAL_ADAPTER_REVIEW started evidence=%s" % RUN_DIR, flush=True)
    started = time.monotonic()
    summary = {"verdict": "INVALID", "scope": args.scope, "source_root": str(root),
               "gateway": "REAL_PRIVATE_HTTP" if args.scope == "gateway" else
                          "PENDING_CHAIR_FREEZE_NOT_TESTED", "tests": 0, "failures": 0,
               "errors": 0, "skipped": 0, "production_mutated": False,
               "started_at": stamp.isoformat(), "review_tool_sha256": sha_bytes(Path(__file__).read_bytes())}
    before = None
    try:
        head = git_read(root, "rev-parse", "HEAD")
        summary["head_before"] = head
        if args.expect_head and head != args.expect_head:
            raise ValueError("review HEAD differs from expected source freeze")
        before = fingerprint(root)
        summary["sources_before"] = before
        if args.readback_result:
            if args.case or args.scope != "gateway":
                raise ValueError("readback requires gateway scope and no test selection")
            summary.update(scan_owned(root))
            summary.update(readback_results(args.readback_result, root, head, before))
        else:
            load_source(root)
            test_class = GatewayBoundaryTests if args.scope == "gateway" else ModuleBoundaryTests
            methods = unittest.defaultTestLoader.getTestCaseNames(test_class)
            selected = methods if args.case is None else args.case
            if not selected or len(selected) != len(set(selected)) or set(selected) - set(methods):
                raise ValueError("review cases must be nonempty, unique exact method names")
            summary["selected_cases"] = selected
            suite = unittest.TestSuite(test_class(name) for name in selected)
            with (RUN_DIR / "tests.log").open("x", encoding="utf-8") as log, (
                    RUN_DIR / "http-server.log").open("x", encoding="utf-8") as server_log:
                with redirect_stderr(server_log):
                    result = unittest.TextTestRunner(
                        stream=log, verbosity=2, failfast=True).run(suite)
            summary.update(tests=result.testsRun, failures=len(result.failures),
                           errors=len(result.errors), skipped=len(result.skipped),
                           verdict=("GATEWAY_BOUNDARIES_PASS" if args.scope == "gateway" else
                                    "MODULE_BOUNDARIES_PASS_GATEWAY_PENDING") if result.wasSuccessful()
                           and result.testsRun and not result.skipped else "FAIL",
                           failure_details=[{"test": str(case), "traceback": detail}
                                            for case, detail in result.failures + result.errors])
    except Exception as exc:
        if isinstance(exc, SourceDriftError):
            summary["verdict"] = "INVALID_SOURCE_DRIFT"
        summary["first_cause"] = {"type": type(exc).__name__, "detail": str(exc),
                                  "traceback": traceback.format_exc()}
    finally:
        if before is not None:
            try:
                after = fingerprint(root)
                summary["sources_after"] = after
                summary["source_drift"] = [key for key in before if before[key] != after[key]]
                summary["head_after"] = git_read(root, "rev-parse", "HEAD")
                if summary["source_drift"] or summary["head_before"] != summary["head_after"]:
                    summary["verdict"] = "INVALID_SOURCE_DRIFT"
            except Exception as exc:
                summary["verdict"] = "INVALID_SOURCE_DRIFT"
                summary["source_anchor_error"] = {
                    "type": type(exc).__name__, "detail": str(exc), "traceback": traceback.format_exc()}
        summary.update(finished_at=datetime.now(timezone.utc).isoformat(),
                       elapsed_s=time.monotonic() - started, uid=os.getuid(),
                       evidence_dir=str(RUN_DIR))
        (RUN_DIR / "result.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        line = ("INDUSTRIAL_ADAPTER_REVIEW verdict=%s tests=%s failures=%s errors=%s skipped=%s evidence=%s"
                % (summary["verdict"], summary["tests"], summary["failures"],
                   summary["errors"], summary["skipped"], RUN_DIR))
        (RUN_DIR / "command.log").write_text(line + "\n", encoding="utf-8")
        print(line)
        if "first_cause" in summary:
            print(summary["first_cause"]["type"] + ": " + summary["first_cause"]["detail"])
        if summary.get("failure_details"):
            print((RUN_DIR / "tests.log").read_text()[-8000:])
    return 0 if summary["verdict"] in (
        "MODULE_BOUNDARIES_PASS_GATEWAY_PENDING", "GATEWAY_BOUNDARIES_PASS",
        "READBACK_MATCH", "READBACK_MATCH_WITH_OPEN_FINDINGS") else 1


if __name__ == "__main__":
    sys.exit(main())
