# SPDX-License-Identifier: Apache-2.0
"""Real Workspace HTTP and browser acceptance for the candidate-only adapter.

The test route is a narrow reference integration, not a production gateway.
No geometry mocks/skips, production requests or shared source edits.
Run this file with --repository-check for source scan and exact discovery.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "oss-alternatives/apps/opencad"
PACKAGE = ROOT / "oss-alternatives/packages/cadcore"
sys.path[:0] = [str(APP), str(PACKAGE)]
from server import ApiError, Workspace, _load_cadcore
from industrial_surfaces import (
    NAMESPACE, export_surfaces, prepare_surfaces, read_surfaces, validate_document,
)

EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-industrial-surfaces-20261011/seat-surfaces-web")
RUN_DIR = EVIDENCE / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)
MM = 1_000_000


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def plane(x=0, height=0):
    return {"op": "nurbs", "poles_nm": [
        [[x * MM, 0, 0], [x * MM, 10 * MM, 0]],
        [[(x + 10) * MM, 0, height * MM], [(x + 10) * MM, 10 * MM, height * MM]]],
        "u_degree": 1, "v_degree": 1, "u_knots": [0, 1], "v_knots": [0, 1],
        "u_mults": [2, 2], "v_mults": [2, 2], "weights": None}


def cylinder():
    return {"op": "nurbs", "poles_nm": [
        [[x * MM, y * MM, z * MM] for z in (0, 10)]
        for x, y in ((5, 0), (5, 5), (0, 5))],
        "u_degree": 2, "v_degree": 1, "u_knots": [0, 1], "v_knots": [0, 1],
        "u_mults": [3, 3], "v_mults": [2, 2],
        "weights": [[1, 1], [math.sqrt(0.5)] * 2, [1, 1]]}


def polygon_plate():
    return {"op": "trim", "surface": plane(),
            "outer": [[.1, .1], [.9, .1], [.9, .5], [.6, .5],
                      [.6, .9], [.1, .9], [.1, .1]],
            "holes": [[[.2, .2], [.2, .3], [.3, .3], [.3, .2], [.2, .2]],
                      [[.4, .2], [.4, .4], [.5, .2], [.4, .2]]]}


def seam():
    return {"left_edge": "u_max", "right_edge": "u_min", "reverse": False,
            "continuity": "G2", "samples": 17, "position_tolerance_nm": 100,
            "angle_tolerance_rad": 1e-6, "curvature_tolerance_per_mm": 1e-6}


def snapshot(workspace):
    with workspace._lock:
        return {"doc": workspace.l1_text(), "undo": list(workspace.undo_stack),
                "redo": list(workspace.redo_stack),
                "cache": copy.deepcopy(getattr(workspace, "_mesh_cache", None))}


def publish(workspace, candidate):
    """Reference integration only: the real project gateway also needs DB CAS."""
    with workspace._lock:
        candidate.verify_integrity()
        if digest(workspace.l1_text()) != candidate.before_document_sha256:
            raise ApiError("revision_conflict", "candidate base changed", status=409)
        if getattr(workspace, "_release_pending", False):
            raise ApiError("maintenance", "pending restart", status=409)
        before = workspace.l1_text()
        workspace.doc = candidate.doc
        workspace.undo_stack.append(before)
        workspace.redo_stack.clear()
    return candidate.response


EXPORT_READBACK = """import hashlib, json, os
from pathlib import Path
import FreeCAD
import Part
spec = json.loads(Path(os.environ["SURFACE_EXPORT_READBACK_INPUT"]).read_text())
path = Path(spec["path"])
if spec["format"] == "brep":
    shape = Part.Shape()
    shape.importBrep(str(path))
else:
    shape = Part.read(str(path))
signature = sorted(len(face.Wires) - 1 for face in shape.Faces)
result = {
    "marker": "surface-download-independent-v1",
    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "bytes": path.stat().st_size,
    "valid": shape.isValid(), "closed": shape.isClosed(),
    "area_mm2": shape.Area,
    "volume_mm3": shape.Volume if shape.Solids else None,
    "solid_count": len(shape.Solids), "hole_signature": signature,
    "wires_closed": all(wire.isClosed() for face in shape.Faces for wire in face.Wires),
    "witnesses": [shape.isInside(FreeCAD.Vector(*point), 1e-7, True)
                  for point in spec["witnesses"]] if shape.Solids else [],
}
Path(os.environ["SURFACE_EXPORT_READBACK_RESULT"]).write_text(json.dumps(result, allow_nan=False))
print("SURFACE_DOWNLOAD_READBACK verdict=PASS")
"""


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/fixture.css"><title>Surface acceptance</title>
<body style="margin:0;padding:12px;box-sizing:border-box"><main id="surfaces"></main>
<script src="/fixture.js"></script><script>
let base;
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...body,expected_document_sha256:base})});
  const value=await response.json();
  if(!response.ok||!value.ok) throw new Error(value.first_cause||value.detail||value.error);
  base=value.document_sha256; return value;
}
window.panel=OpenCADSurfaces.mount(document.getElementById("surfaces"),{request});
panel.reload().catch(function(error){document.body.dataset.error=String(error);});
</script></body></html>"""


class FixtureHTTP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, media="application/json", headers=None):
        raw = json.dumps(value, allow_nan=False).encode() if media == "application/json" else value
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(raw)))
        for name, content in (headers or {}).items():
            self.send_header(name, content)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            if self.path == "/api/surfaces":
                self.reply(read_surfaces(self.server.workspace, {}))
            elif self.path in ("/api/surfaces/step", "/api/surfaces/brep"):
                exported = export_surfaces(self.server.workspace, {"format": self.path.rsplit("/", 1)[1]})
                self.reply(exported.data, media=exported.content_type, headers=exported.headers)
            elif self.path == "/api/state":
                self.reply({"ok": True, **snapshot(self.server.workspace)})
            elif self.path == "/":
                self.reply(PAGE.encode(), media="text/html")
            elif self.path in ("/fixture.js", "/fixture.css"):
                path = APP / ("web/js/surfaces-panel.js" if self.path.endswith("js")
                              else "web/css/surfaces-panel.css")
                self.reply(path.read_bytes(), media="text/javascript" if self.path.endswith("js") else "text/css")
            else:
                self.reply({"ok": False}, 404)
        except ApiError as exc:
            self.reply(exc.payload(), exc.status)

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            workspace = self.server.workspace
            if self.path == "/api/surfaces":
                if body.get("action") == "check_seam":
                    self.reply(read_surfaces(workspace, body))
                    return
                expected = body.pop("expected_document_sha256")
                if expected != digest(workspace.l1_text()):
                    raise ApiError("revision_conflict", "stale request", status=409)
                candidate = prepare_surfaces(workspace, body)
                self.reply(publish(workspace, candidate))
            elif self.path == "/api/command":
                self.reply(workspace.command(body["command"], body.get("args", {})))
            elif self.path == "/api/save":
                self.reply(workspace.save(body["path"]))
            elif self.path == "/api/open":
                self.reply(workspace.open_doc(body["path"]))
            else:
                self.reply({"ok": False}, 404)
        except ApiError as exc:
            self.reply(exc.payload(), exc.status)
        except Exception as exc:
            self.reply({"ok": False, "error": type(exc).__name__, "detail": str(exc)}, 500)


class IndustrialSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        cls.core = _load_cadcore()
        import cadcore.kernel.surfaces as module
        if Path(module.__file__).resolve() != PACKAGE / "cadcore/kernel/surfaces.py":
            raise AssertionError("foreign surface module")
        if Path(sys.modules["server"].__file__).resolve() != APP / "server.py":
            raise AssertionError("foreign Workspace")

    def setUp(self):
        self.directory = RUN_DIR / (self._testMethodName + "-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.workspace = Workspace(self.core, locale="en", standard="gbt", out_root=str(self.directory))
        self.workspace._mesh_cache = {"test_sentinel": "untouched"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHTTP)
        self.server.workspace = self.workspace
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.events = []

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise AssertionError("owned fixture server did not stop")
        (self.directory / "http-events.json").write_text(json.dumps(self.events, indent=2), encoding="utf-8")

    def call(self, method, route, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        try:
            connection.request(method, route, body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            value = json.loads(response.read())
            self.events.append({"method": method, "route": route, "status": response.status,
                                "error": value.get("error"), "first_cause": value.get("first_cause")})
            return response.status, value
        finally:
            connection.close()

    def post(self, recipe):
        return self.call("POST", "/api/surfaces", {
            "recipe": recipe, "expected_document_sha256": digest(self.workspace.l1_text())})

    def download(self, fmt):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        try:
            connection.request("GET", "/api/surfaces/" + fmt)
            response = connection.getresponse()
            data = response.read()
            headers = dict(response.getheaders())
            self.events.append({"method": "GET", "route": "/api/surfaces/" + fmt,
                                "status": response.status, "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()})
            return response.status, data, headers
        finally:
            connection.close()

    def independent_export_readback(self, data, fmt, solid):
        out = self.directory / ("download-readback-" + fmt + "-" + uuid.uuid4().hex)
        out.mkdir()
        artifact = out / ("download." + fmt)
        artifact.write_bytes(data)
        spec = {"path": str(artifact), "format": fmt,
                "witnesses": [[1.5, 5, 1], [2.5, 2.5, 1], [4.3, 2.5, 1], [7, 7, 1]]}
        input_path, result_path, script = out / "input.json", out / "result.json", out / "readback.py"
        input_path.write_text(json.dumps(spec))
        script.write_text(EXPORT_READBACK)
        from cadcore.kernel.subproc import find_executable
        executable = find_executable()
        self.assertTrue(executable, "real FreeCAD executable is required")
        with (out / "cli.log").open("xb") as log:
            child = subprocess.run([str(executable), str(script)], cwd=out, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=60, env=dict(
                                       os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
                                       SURFACE_EXPORT_READBACK_INPUT=str(input_path),
                                       SURFACE_EXPORT_READBACK_RESULT=str(result_path)))
        self.assertEqual(child.returncode, 0, (out / "cli.log").read_text())
        result = json.loads(result_path.read_text())
        self.assertIn("SURFACE_DOWNLOAD_READBACK verdict=PASS", (out / "cli.log").read_text())
        self.assertEqual(result["marker"], "surface-download-independent-v1")
        self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["bytes"], len(data))
        self.assertTrue(result["valid"] and result["wires_closed"])
        self.assertEqual(result["solid_count"], int(solid))
        self.assertEqual(sum(result["hole_signature"]), 4 if solid else 2)
        if solid:
            self.assertTrue(result["closed"])
            self.assertAlmostEqual(result["volume_mm3"], 100, delta=1e-6)
            self.assertEqual(result["witnesses"], [True, False, False, False])
        else:
            self.assertIsNone(result["volume_mm3"])
            self.assertAlmostEqual(result["area_mm2"], 50, delta=1e-6)
        return result

    def test_export_real_concave_two_holes_brep_step_and_frozen_receipt(self):
        for solid in (False, True):
            operation = ({"op": "thicken", "surface": polygon_plate(),
                          "thickness_nm": 2 * MM, "inward": False} if solid else polygon_plate())
            self.assertEqual(self.post(operation)[0], 200)
            before = snapshot(self.workspace)
            for fmt in ("brep", "step"):
                status, data, headers = self.download(fmt)
                self.assertEqual(status, 200, data[:200])
                self.assertEqual(headers["X-OpenCAD-Document-SHA256"], digest(before["doc"]))
                self.assertEqual(headers["X-OpenCAD-Artifact-SHA256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(headers["Content-Disposition"], 'attachment; filename="surface.%s"' % fmt)
                self.assertNotIn(str(self.directory), json.dumps(headers))
                self.independent_export_readback(data, fmt, solid)
                self.assertEqual(snapshot(self.workspace), before)
        exported = export_surfaces(self.workspace, {"format": "step"})
        with self.assertRaises(TypeError):
            exported.headers["X-OpenCAD-Artifact-SHA256"] = "0" * 64
        with self.assertRaises(AttributeError):
            exported.data = b"changed"

    def test_export_save_reopen_recipe_bytes_same_document(self):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        saved = self.workspace.l1_text()
        self.assertEqual(self.call("POST", "/api/save", {"path": "download.l1"})[0], 200)
        reopened = Workspace(self.core, locale="en", standard="gbt", out_root=str(self.directory))
        reopened.open_doc(str(self.directory / "download.l1"))
        before = snapshot(reopened)
        self.assertEqual(before["doc"], saved)
        self.assertEqual(validate_document(reopened.doc)["recipe"], polygon_plate())
        for fmt in ("brep", "step"):
            exported = export_surfaces(reopened, {"format": fmt})
            self.assertEqual(exported.headers["X-OpenCAD-Document-SHA256"], digest(saved))
            self.independent_export_readback(exported.data, fmt, False)
            self.assertEqual(snapshot(reopened), before)

    def test_export_bad_format_empty_and_mixed_domains_no_worker(self):
        before = snapshot(self.workspace)
        for body in ({}, {"format": None}, {"format": True}, {"format": "stl"},
                     {"format": "STEP"}, {"format": "../step"}, {"format": "step", "path": "x"}):
            with self.subTest(body=body):
                with self.assertRaises(ApiError) as caught:
                    export_surfaces(self.workspace, body)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(snapshot(self.workspace), before)
        with self.assertRaises(ApiError) as caught:
            export_surfaces(self.workspace, {"format": "step"})
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse((self.directory / "surface-jobs").exists())
        self.workspace.doc.extra[NAMESPACE] = {"schema": 1, "recipe": polygon_plate()}
        for domain in ("sketch", "features", "assembly"):
            original = copy.deepcopy(self.workspace.doc)
            if domain == "sketch":
                self.workspace.command("add_line", {"p1": [0, 0], "p2": [MM, 0]})
            elif domain == "features":
                self.workspace.doc.features.append({
                    "uuid": str(uuid.uuid4()), "kind": "import_step",
                    "params": {"step_text": "unconsumed"}, "suppressed": False})
            else:
                self.workspace.doc.extra["industrial_assembly_v1"] = {}
            with self.assertRaises(ApiError) as caught:
                export_surfaces(self.workspace, {"format": "step"})
            self.assertEqual(caught.exception.payload()["error"], "unsupported_geometry_domain")
            self.assertFalse((self.directory / "surface-jobs").exists())
            self.workspace.doc = original

    def test_export_artifact_postbuild_corruption_and_symlink_refused_atomically(self):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        before = snapshot(self.workspace)
        import industrial_surfaces as adapter
        kernel = adapter._kernel(self.workspace)
        build = kernel.build
        for operation in ("bytes", "length", "sha", "symlink", "missing", "source-map"):
            def changed_build(recipe):
                result = build(recipe)
                item = result["artifacts"]["step"]
                path = Path(item["path"])
                if operation == "bytes":
                    path.write_bytes(path.read_bytes() + b"corruption")
                elif operation == "length":
                    item["bytes"] += 1
                elif operation == "sha":
                    item["sha256"] = "0" * 64
                elif operation == "symlink":
                    held = path.with_name("held.step")
                    path.rename(held)
                    path.symlink_to(held)
                elif operation == "missing":
                    path.rename(path.with_name("retained.step"))
                else:
                    result["execution"]["sources_before"] = {}
                return result
            with self.subTest(operation=operation), mock.patch.object(adapter, "_kernel", return_value=kernel):
                with mock.patch.object(kernel, "build", side_effect=changed_build):
                    with self.assertRaises(ApiError) as caught:
                        export_surfaces(self.workspace, {"format": "step"})
                self.assertEqual(caught.exception.status, 422)
                self.assertTrue(caught.exception.payload()["first_cause"])
                self.assertNotIn(str(self.directory), json.dumps(caught.exception.payload()))
                self.assertEqual(snapshot(self.workspace), before)

    def test_export_during_document_or_source_drift_no_stale_bytes_returned(self):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        import industrial_surfaces as adapter
        kernel = adapter._kernel(self.workspace)
        build = kernel.build
        before = snapshot(self.workspace)
        def concurrent_change(recipe):
            result = build(recipe)
            with self.workspace._lock:
                self.workspace.doc.meta["title_key"] = "other-operation"
            return result
        with mock.patch.object(adapter, "_kernel", return_value=kernel):
            with mock.patch.object(kernel, "build", side_effect=concurrent_change):
                with self.assertRaises(ApiError) as caught:
                    export_surfaces(self.workspace, {"format": "step"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.workspace.doc.meta["title_key"], "other-operation")
        self.assertEqual(snapshot(self.workspace)["undo"], before["undo"])
        current = snapshot(self.workspace)
        sources = adapter._sources()
        with mock.patch.object(adapter, "_sources", side_effect=[sources, {**sources, "surfaces.py": "0" * 64}]):
            with self.assertRaises(ApiError) as caught:
                export_surfaces(self.workspace, {"format": "step"})
        self.assertEqual(caught.exception.payload()["reason"], "source_drift")
        self.assertEqual(snapshot(self.workspace), current)

    def test_export_real_kernel_failure_retains_firstcause_no_model_mutation(self):
        operation = {"op": "thicken", "surface": cylinder(), "thickness_nm": 6 * MM, "inward": True}
        self.workspace.doc.extra[NAMESPACE] = {"schema": 1, "recipe": operation}
        before = snapshot(self.workspace)
        with self.assertRaises(ApiError) as caught:
            export_surfaces(self.workspace, {"format": "step"})
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("focal crossing", caught.exception.payload()["first_cause"])
        self.assertNotIn(str(self.directory), json.dumps(caught.exception.payload()))
        self.assertEqual(snapshot(self.workspace), before)
        logs = list((self.directory / "surface-jobs").glob("surface-*/stderr.log"))
        self.assertTrue(any(caught.exception.payload()["first_cause"] in path.read_text() for path in logs))

    def test_empty_read_and_prepare_are_strictly_nonmutating(self):
        before = snapshot(self.workspace)
        status, response = self.call("GET", "/api/surfaces")
        self.assertEqual(status, 200, response)
        self.assertTrue(response["empty"])
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        self.assertEqual(candidate.before_document_sha256, digest(before["doc"]))
        self.assertIsNot(candidate.doc, self.workspace.doc)
        self.assertEqual(snapshot(self.workspace), before)
        self.assertAlmostEqual(candidate.response["result"]["properties"]["area_mm2"], 100, delta=1e-8)
        self.assertTrue(candidate.response["result"]["mesh"]["triangles"])

    def assert_candidate_rejected(self, candidate):
        before = snapshot(self.workspace)
        with self.assertRaises(ApiError) as caught:
            publish(self.workspace, candidate)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload()["reason"], "candidate_changed")
        self.assertTrue(caught.exception.payload()["first_cause"])
        self.assertEqual(snapshot(self.workspace), before)

    def test_real_prepare_creation_seal_and_unchanged_replace_verify(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": cylinder()})
        before = snapshot(self.workspace)
        raw = self.workspace.docmodel.document_to_l1(candidate.doc).encode("utf-8")
        self.assertEqual(candidate.document_bytes, raw)
        self.assertEqual(candidate.response["document_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(candidate.creation_envelope_sha256), 64)
        self.assertAlmostEqual(candidate.response["result"]["properties"]["area_mm2"],
                               25 * math.pi, delta=1e-6)
        self.assertIsNone(candidate.verify_integrity())
        unchanged = replace(candidate)
        self.assertEqual(unchanged.creation_envelope_sha256, candidate.creation_envelope_sha256)
        self.assertIsNone(unchanged.verify_integrity())
        self.assertEqual(snapshot(self.workspace), before)

    def test_real_prepare_nested_document_tamper_rejected(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        candidate.doc.meta["title_key"] = "changed-after-measurement"
        self.assert_candidate_rejected(candidate)

    def test_real_prepare_response_document_sha_tamper_rejected(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        candidate.response["document_sha256"] = "0" * 64
        self.assert_candidate_rejected(candidate)

    def test_real_prepare_mesh_and_measurement_tamper_rejected(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        for label in ("mesh", "measurement", "artifact", "source", "metadata"):
            with self.subTest(label=label):
                changed = copy.deepcopy(candidate)
                result = changed.response["result"]
                if label == "mesh":
                    result["mesh"]["vertices_mm"][0][0] += 100
                elif label == "measurement":
                    result["properties"]["area_mm2"] += 100
                elif label == "artifact":
                    result["artifacts"]["step"]["sha256"] = "0" * 64
                elif label == "source":
                    result["kernel_source_sha256"]["surfaces.py"] = "0" * 64
                else:
                    result["backend"] = {"forged": True}
                self.assertEqual(changed.creation_envelope_sha256,
                                 candidate.creation_envelope_sha256)
                self.assert_candidate_rejected(changed)

    def test_real_prepare_dataclass_replace_retains_original_seal(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        changed_doc = copy.deepcopy(candidate.doc)
        changed_doc.meta["title_key"] = "replacement"
        changed_response = copy.deepcopy(candidate.response)
        changed_response["result"]["mesh"]["triangles"] = []
        replacements = ({"doc": changed_doc}, {"response": changed_response},
                        {"before_document_sha256": "0" * 64},
                        {"document_bytes": candidate.document_bytes + b" "},
                        {"creation_envelope_sha256": "0" * 64})
        for fields in replacements:
            with self.subTest(fields=list(fields)):
                changed = replace(candidate, **fields)
                if "creation_envelope_sha256" not in fields:
                    self.assertEqual(changed.creation_envelope_sha256,
                                     candidate.creation_envelope_sha256)
                self.assert_candidate_rejected(changed)

    def test_real_prepare_coordinated_doc_response_hash_tamper_rejected(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        original_seal = candidate.creation_envelope_sha256
        candidate.doc.extra[NAMESPACE]["recipe"]["poles_nm"][0][0][0] += MM
        candidate.response["surface"] = copy.deepcopy(candidate.doc.extra[NAMESPACE])
        raw = self.workspace.docmodel.document_to_l1(candidate.doc).encode("utf-8")
        candidate.response["document_sha256"] = hashlib.sha256(raw).hexdigest()
        changed = replace(candidate, document_bytes=raw)
        self.assertEqual(changed.creation_envelope_sha256, original_seal)
        self.assert_candidate_rejected(changed)

    def test_real_prepare_nonfinite_response_tamper_retains_firstcause(self):
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        candidate.response["result"]["properties"]["area_mm2"] = float("nan")
        self.assert_candidate_rejected(candidate)
        with self.assertRaises(ApiError) as caught:
            candidate.verify_integrity()
        self.assertIn("JSON", caught.exception.payload()["first_cause"])

    def test_validate_document_absent_present_detached_and_no_kernel_sideeffects(self):
        before = snapshot(self.workspace)
        self.assertEqual(validate_document(self.workspace.doc), {"schema": 1, "recipe": None})
        self.assertEqual(snapshot(self.workspace), before)
        self.workspace.doc.extra[NAMESPACE] = {"schema": 1, "recipe": plane()}
        before = snapshot(self.workspace)
        state = validate_document(self.workspace.doc)
        self.assertEqual(state, self.workspace.doc.extra[NAMESPACE])
        state["recipe"]["poles_nm"][0][0][0] += MM
        self.assertEqual(snapshot(self.workspace), before)
        self.assertFalse((self.directory / "surface-jobs").exists())

    def test_validate_document_strict_schema_recipe_negative_controls(self):
        invalid = [None, {"schema": True, "recipe": plane()}, {"schema": 2, "recipe": plane()},
                   {"schema": 1}, {"schema": 1, "recipe": None},
                   {"schema": 1, "recipe": plane(), "unknown": True}]
        for mutation in ({"unknown": 1}, {"u_degree": True},
                         {"weights": [[float("nan"), 1], [1, 1]]},
                         {"poles_nm": [[[True, 0, 0]] * 2] * 2}):
            invalid.append({"schema": 1, "recipe": {**plane(), **mutation}})
        for state in invalid:
            with self.subTest(state=state):
                self.workspace.doc.extra[NAMESPACE] = state
                before = snapshot(self.workspace) if "nan" not in repr(state) else None
                original = copy.deepcopy(self.workspace.doc.extra)
                with self.assertRaises(ApiError) as caught:
                    validate_document(self.workspace.doc)
                self.assertEqual(caught.exception.status, 400)
                if before is not None:
                    self.assertEqual(snapshot(self.workspace), before)
                else:
                    self.assertEqual(repr(self.workspace.doc.extra), repr(original))
        self.assertFalse((self.directory / "surface-jobs").exists())

    def test_http_nurbs_true_kernel_result_and_no_private_paths(self):
        status, response = self.post(cylinder())
        self.assertEqual(status, 200, response)
        result = response["result"]
        self.assertAlmostEqual(result["properties"]["area_mm2"], 25 * math.pi, delta=1e-6)
        self.assertTrue(result["properties"]["faces"][0]["rational"])
        self.assertIsNone(result["mesh"]["properties"]["volume_nm3"])
        self.assertNotIn(str(self.directory), json.dumps(response))
        self.assertNotIn("traceback", json.dumps(response))
        jobs = list((self.directory / "surface-jobs").glob("surface-*/execution.json"))
        self.assertEqual(len(jobs), 1)
        execution = json.loads(jobs[0].read_text())
        self.assertEqual(execution["rc"], 0)
        self.assertEqual(execution["sources_before"], execution["sources_after"])
        self.assertIn("freecad", execution["executable"].lower())
        self.assertEqual(len(self.workspace.undo_stack), 1)

    def test_http_trim_and_thicken_independent_analytic_geometry(self):
        recipe = {"op": "trim", "surface": plane(), "bounds": [0.2, 0.8, 0.25, 0.75]}
        status, response = self.post({"op": "thicken", "surface": recipe,
                                      "thickness_nm": MM, "inward": True})
        self.assertEqual(status, 200, response)
        result = response["result"]
        self.assertAlmostEqual(result["properties"]["volume_mm3"], 30, delta=1e-7)
        self.assertEqual(result["properties"]["solid_count"], 1)
        points, triangles = result["mesh"]["vertices_mm"], result["mesh"]["triangles"]
        volume = 0
        for tri in triangles:
            a, b, c = [points[i] for i in tri]
            volume += (a[0] * (b[1]*c[2]-b[2]*c[1]) +
                       a[1] * (b[2]*c[0]-b[0]*c[2]) +
                       a[2] * (b[0]*c[1]-b[1]*c[0])) / 6
        self.assertAlmostEqual(abs(volume), 30, delta=1e-7)
        self.assertAlmostEqual(result["roundtrip"]["step"]["volume_mm3"], 30, delta=1e-7)

    def test_polygon_recipe_validation_detached_and_no_geometry_sideeffects(self):
        recipe = polygon_plate()
        self.workspace.doc.extra[NAMESPACE] = {"schema": 1, "recipe": recipe}
        before = snapshot(self.workspace)
        state = validate_document(self.workspace.doc)
        self.assertEqual(state["recipe"], recipe)
        state["recipe"]["holes"][0][0][0] = .7
        self.assertEqual(snapshot(self.workspace), before)
        self.assertFalse((self.directory / "surface-jobs").exists())

    def assert_polygon_measurement(self, response, *, solid=False):
        self.assertEqual(response["document_sha256"], digest(self.workspace.l1_text()))
        self.assertEqual(response["surface"], self.workspace.doc.extra[NAMESPACE])
        properties = response["result"]["properties"]
        self.assertEqual(properties["solid_count"], int(solid))
        self.assertEqual(properties["inner_wire_count"], 4 if solid else 2)
        for fmt in ("brep", "step"):
            loaded = response["result"]["roundtrip"][fmt]
            self.assertEqual(loaded["inner_wire_count"], properties["inner_wire_count"])
            self.assertTrue(all(row["wires_closed"] for row in loaded["faces"]))
            artifact = response["result"]["artifacts"][fmt]
            self.assertGreater(artifact["bytes"], 100)
            self.assertEqual(len(artifact["sha256"]), 64)
            if solid:
                self.assertAlmostEqual(loaded["volume_mm3"], 100, delta=1e-6)
            else:
                self.assertAlmostEqual(loaded["area_mm2"], 50, delta=1e-7)
        if solid:
            self.assertAlmostEqual(properties["volume_mm3"], 100, delta=1e-7)
            self.assertTrue(properties["closed"])
        else:
            self.assertAlmostEqual(properties["area_mm2"], 50, delta=1e-8)
            self.assertIsNone(properties["volume_mm3"])
        self.assertEqual(response["result"]["mesh"]["source"], "exact BRep tessellation")

    def test_http_polygon_save_history_restart_preserves_recipe_and_measurement(self):
        recipe = polygon_plate()
        for solid in (False, True):
            with self.subTest(solid=solid):
                operation = ({"op": "thicken", "surface": recipe,
                              "thickness_nm": 2 * MM, "inward": False} if solid else recipe)
                before = self.workspace.l1_text()
                undo_count = len(self.workspace.undo_stack)
                status, response = self.post(operation)
                self.assertEqual(status, 200, response)
                self.assert_polygon_measurement(response, solid=solid)
                stored = self.workspace.l1_text()
                self.assertEqual(len(self.workspace.undo_stack), undo_count + 1)
                name = "polygon-%s.l1" % solid
                self.assertEqual(self.call("POST", "/api/save", {"path": name})[0], 200)
                self.assertEqual((self.directory / name).read_text(), stored)
                self.assertEqual(self.call("POST", "/api/command", {"command": "undo"})[0], 200)
                self.assertEqual(self.workspace.l1_text(), before)
                self.assertEqual(self.call("POST", "/api/command", {"command": "redo"})[0], 200)
                self.assertEqual(self.workspace.l1_text(), stored)
                captured = snapshot(self.workspace)
                self.assert_polygon_measurement(read_surfaces(self.workspace), solid=solid)
                self.assertEqual(snapshot(self.workspace), captured)
                restarted = Workspace(self.core, locale="en", standard="gbt",
                                      out_root=str(self.directory))
                restarted.open_doc(str(self.directory / name))
                self.assertEqual(restarted.l1_text(), stored)
                reloaded = read_surfaces(restarted)
                self.assertEqual(reloaded["document_sha256"], digest(stored))
                self.assertEqual(reloaded["surface"]["recipe"], operation)
                for fmt in ("brep", "step"):
                    loaded = reloaded["result"]["roundtrip"][fmt]
                    self.assertEqual(loaded["inner_wire_count"], 4 if solid else 2)
                    if solid:
                        self.assertAlmostEqual(loaded["volume_mm3"], 100, delta=1e-6)
                    else:
                        self.assertAlmostEqual(loaded["area_mm2"], 50, delta=1e-7)

    def test_http_polygon_bad_loops_do_not_publish_or_start_worker(self):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        self.workspace.redo_stack = [self.workspace.undo_stack[0]]
        before = snapshot(self.workspace)
        jobs = len(list((self.directory / "surface-jobs").glob("surface-*")))
        recipes = []
        for outer in (
                [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]],
                [[.1, .1], [.1, .1], [.5, .5], [.1, .1]],
                [[.1, .1], [.9, .1], [.5, .9]],
                [[.1, .1], [.9, .1], [.5, 1.1], [.1, .1]]):
            recipes.append({**polygon_plate(), "outer": outer})
        recipes.append({**polygon_plate(), "holes": [
            [[.7, .6], [.7, .8], [.8, .8], [.8, .6], [.7, .6]]]})
        recipes.append({**polygon_plate(), "holes": [polygon_plate()["holes"][0]] * 2})
        recipes.append({**polygon_plate(), "holes": [
            list(reversed(polygon_plate()["holes"][0]))]})
        for recipe in recipes:
            with self.subTest(recipe=recipe):
                status, response = self.post(recipe)
                self.assertEqual(status, 400, response)
                self.assertEqual(response["error"], "bad_surface_arguments")
                self.assertTrue(response["detail"])
                self.assertEqual(snapshot(self.workspace), before)
                self.assertEqual(len(list((self.directory / "surface-jobs").glob("surface-*"))), jobs)
        self.workspace.doc.extra[NAMESPACE]["recipe"] = recipes[0]
        corrupted = snapshot(self.workspace)
        self.assertEqual(self.call("GET", "/api/surfaces")[0], 400)
        self.assertEqual(snapshot(self.workspace), corrupted)

    def test_http_polygon_physical_degeneracy_retains_worker_firstcause(self):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        before = snapshot(self.workspace)
        tiny = {"op": "trim", "surface": plane(), "holes": [],
                "outer": [[.1, .1], [.1 + 1e-12, .1], [.1, .1 + 1e-12], [.1, .1]]}
        status, response = self.post(tiny)
        self.assertEqual(status, 422, response)
        self.assertEqual(response["reason"], "op_failed")
        self.assertTrue(response["first_cause"])
        self.assertEqual(snapshot(self.workspace), before)
        logs = list((self.directory / "surface-jobs").glob("surface-*/stderr.log"))
        self.assertTrue(any(response["first_cause"] in path.read_text() for path in logs))

    def test_http_sew_g2_positive_and_crease_negative_no_publication(self):
        spec = {**seam(), "left": 1, "right": 2}
        recipe = {"op": "sew", "surfaces": [plane(), plane(10)], "solid": False, "seams": [spec]}
        status, response = self.post(recipe)
        self.assertEqual(status, 200, response)
        self.assertTrue(response["result"]["seams"][0]["passed"])
        before = snapshot(self.workspace)
        recipe["surfaces"][1] = plane(10, 5)
        status, response = self.post(recipe)
        self.assertEqual(status, 422, response)
        self.assertIn("sew seam continuity rejected", response["first_cause"])
        self.assertEqual(snapshot(self.workspace), before)

    def test_http_true_g0_g1_g2_readonly_numeric_negative_controls(self):
        before = snapshot(self.workspace)
        for right, field in ((plane(11), "g0"), (plane(10, 5), "g1")):
            status, response = self.call("POST", "/api/surfaces", {
                "action": "check_seam", "left": plane(), "right": right, "seam": seam()})
            self.assertEqual(status, 200, response)
            self.assertFalse(response["seam"][field])
            self.assertFalse(response["seam"]["passed"])
        curved = {"op": "nurbs", "poles_nm": [
            [[x * MM, y * MM, z * MM] for y in (0, 10)]
            for x, z in ((10, 0), (15, 0), (20, 10))],
            "u_degree": 2, "v_degree": 1, "u_knots": [0, 1], "v_knots": [0, 1],
            "u_mults": [3, 3], "v_mults": [2, 2], "weights": None}
        status, response = self.call("POST", "/api/surfaces", {
            "action": "check_seam", "left": plane(), "right": curved, "seam": seam()})
        self.assertEqual(status, 200, response)
        self.assertTrue(response["seam"]["g0"] and response["seam"]["g1"])
        self.assertFalse(response["seam"]["g2"])
        self.assertAlmostEqual(response["seam"]["max_curvature_difference_per_mm"], 0.2, delta=1e-9)
        self.assertEqual(snapshot(self.workspace), before)

    def test_http_schema_failure_preserves_document_histories_cache(self):
        self.assertEqual(self.post(plane())[0], 200)
        self.workspace.redo_stack = [self.workspace.undo_stack[0]]
        before = snapshot(self.workspace)
        for mutation in ({"unknown": 1}, {"u_degree": True}, {"weights": [[0, 1], [1, 1]]}):
            status, response = self.post({**plane(), **mutation})
            self.assertEqual(status, 400, response)
            self.assertEqual(snapshot(self.workspace), before)

    def test_http_focal_crossing_error_keeps_firstcause_and_old_shape(self):
        self.assertEqual(self.post(cylinder())[0], 200)
        before = snapshot(self.workspace)
        status, response = self.post({"op": "thicken", "surface": cylinder(),
                                      "thickness_nm": 6 * MM, "inward": True})
        self.assertEqual(status, 422, response)
        self.assertEqual(response["reason"], "op_failed")
        self.assertIn("focal crossing", response["first_cause"])
        self.assertEqual(snapshot(self.workspace), before)
        logs = list((self.directory / "surface-jobs").glob("surface-*/stderr.log"))
        self.assertTrue(any(response["first_cause"] in path.read_text() for path in logs))
        self.assertEqual(self.call("GET", "/api/surfaces")[0], 200)
        self.assertEqual(snapshot(self.workspace), before)

    def test_http_unclosed_solid_rejected_not_fallback(self):
        before = snapshot(self.workspace)
        status, response = self.post({"op": "sew", "surfaces": [plane(), plane(10)],
                                      "solid": True, "seams": []})
        self.assertEqual(status, 422, response)
        self.assertIn("unclosed", response["first_cause"])
        self.assertEqual(snapshot(self.workspace), before)

    def test_candidates_same_base_one_cas_winner_history_only_once(self):
        before = snapshot(self.workspace)
        first = prepare_surfaces(self.workspace, {"recipe": plane()})
        second = prepare_surfaces(self.workspace, {"recipe": cylinder()})
        self.assertEqual(first.before_document_sha256, second.before_document_sha256)
        self.assertEqual(snapshot(self.workspace), before)
        publish(self.workspace, first)
        won = snapshot(self.workspace)
        with self.assertRaisesRegex(ApiError, "revision_conflict"):
            publish(self.workspace, second)
        self.assertEqual(snapshot(self.workspace), won)
        self.assertEqual(len(self.workspace.undo_stack), 1)

    def test_http_stale_request_refused_before_build(self):
        stale = digest(self.workspace.l1_text())
        self.assertEqual(self.post(plane())[0], 200)
        before = snapshot(self.workspace)
        count = len(list((self.directory / "surface-jobs").glob("surface-*")))
        status, response = self.call("POST", "/api/surfaces", {
            "recipe": cylinder(), "expected_document_sha256": stale})
        self.assertEqual(status, 409, response)
        self.assertEqual(snapshot(self.workspace), before)
        self.assertEqual(len(list((self.directory / "surface-jobs").glob("surface-*"))), count)

    def test_http_save_reopen_roundtrip_rebuild_and_history_undo_redo(self):
        self.assertEqual(self.post(cylinder())[0], 200)
        stored = self.workspace.l1_text()
        status, response = self.call("POST", "/api/save", {"path": "surface.l1"})
        self.assertEqual(status, 200, response)
        self.assertEqual((self.directory / "surface.l1").read_text(), stored)
        for command in ("undo", "redo"):
            status, response = self.call("POST", "/api/command", {"command": command, "args": {}})
            self.assertEqual(status, 200, response)
            expected_empty = command == "undo"
            self.assertEqual(read_surfaces(self.workspace, {})["empty"], expected_empty)
        self.workspace.doc.extra.pop(NAMESPACE)
        status, response = self.call("POST", "/api/open", {"path": "surface.l1"})
        self.assertEqual(status, 200, response)
        self.assertEqual(self.workspace.l1_text(), stored)
        before = snapshot(self.workspace)
        status, response = self.call("GET", "/api/surfaces")
        self.assertEqual(status, 200, response)
        self.assertAlmostEqual(response["result"]["properties"]["area_mm2"], 25 * math.pi, delta=1e-6)
        self.assertEqual(snapshot(self.workspace), before)
        restarted = Workspace(self.core, locale="en", standard="gbt",
                              out_root=str(self.directory))
        restarted.open_doc(str(self.directory / "surface.l1"))
        self.assertEqual(restarted.doc.extra[NAMESPACE], self.workspace.doc.extra[NAMESPACE])
        self.assertAlmostEqual(read_surfaces(restarted, {})["result"]["properties"]["area_mm2"],
                               25 * math.pi, delta=1e-6)

    def test_existing_sketch_features_and_extension_data_not_replaced(self):
        self.workspace.command("add_line", {"p1": [0, 0], "p2": [10 * MM, 0]})
        self.workspace.doc.extra["x_other_owner"] = {"untouched": [1, 2]}
        before = json.loads(self.workspace.l1_text())
        candidate = prepare_surfaces(self.workspace, {"recipe": plane()})
        after = json.loads(self.workspace.docmodel.document_to_l1(candidate.doc))
        after.pop(NAMESPACE)
        self.assertEqual(after, before)
        self.assertEqual(snapshot(self.workspace)["doc"], json.dumps(before, ensure_ascii=False,
                                                                    allow_nan=False, sort_keys=True, indent=2) + "\n")

    def test_corrupt_persisted_surface_fail_loud_read_and_prepare(self):
        self.workspace.doc.extra[NAMESPACE] = {"schema": 99, "recipe": plane()}
        before = snapshot(self.workspace)
        status, response = self.call("GET", "/api/surfaces")
        self.assertEqual(status, 400, response)
        self.assertIn("persisted surface schema", response["detail"])
        with self.assertRaises(ApiError):
            prepare_surfaces(self.workspace, {"recipe": plane()})
        self.assertEqual(snapshot(self.workspace), before)
        self.workspace.doc.extra[NAMESPACE] = None
        before = snapshot(self.workspace)
        self.assertEqual(self.call("GET", "/api/surfaces")[0], 400)
        with self.assertRaises(ApiError):
            prepare_surfaces(self.workspace, {"recipe": plane()})
        self.assertEqual(snapshot(self.workspace), before)

    def test_maintenance_candidate_preparation_denied_without_build(self):
        self.workspace._release_pending = True
        before = snapshot(self.workspace)
        with self.assertRaisesRegex(ApiError, "maintenance"):
            prepare_surfaces(self.workspace, {"recipe": plane()})
        self.assertEqual(snapshot(self.workspace), before)
        self.assertFalse((self.directory / "surface-jobs").exists())

    def test_worker_runtime_cannot_write_into_repository(self):
        original = self.workspace.out_root
        self.workspace.out_root = str(APP)
        before = snapshot(self.workspace)
        with self.assertRaisesRegex(ApiError, "outside the source repository"):
            prepare_surfaces(self.workspace, {"recipe": plane()})
        self.assertEqual(snapshot(self.workspace), before)
        self.workspace.out_root = original

    def browser(self, width):
        path = ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
        spec = importlib.util.spec_from_file_location("surface_browser_helper", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        browser = module.BrowserSession(
            "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
            self.directory / "browser", width=width, height=900, source_root=ROOT)
        with browser:
            browser.call("Page.navigate", {"url": "http://127.0.0.1:%s/" % self.server.server_port})
            browser.wait("window.panel && !panel.model.busy && panel.model.response")
            browser.click('[data-surface-action="cylinder"]')
            browser.click('[data-surface-action="apply"]')
            browser.wait("panel.model.triangles>0 && !panel.model.busy")
            self.assertGreater(browser.evaluate("panel.model.draws"), 0)
            self.assertAlmostEqual(browser.evaluate(
                "panel.model.response.result.properties.area_mm2"), 25 * math.pi, delta=1e-6)
            browser.click('[data-surface-action="thicken"]')
            browser.click('[data-surface-action="apply"]')
            browser.wait("panel.model.response.result.properties.solid_count===1 && !panel.model.busy")
            self.assertAlmostEqual(browser.evaluate(
                "panel.model.response.result.properties.volume_mm3"), 27.5 * math.pi, delta=1e-5)
            self.assertFalse(browser.evaluate("document.documentElement.scrollWidth>innerWidth"))
            self.assertEqual(browser.evaluate("panel.model.error"), "")
            self.assertGreater(browser.evaluate("""(function() {
              const canvas=document.querySelector('.surfaces-preview'), gl=canvas.getContext('webgl');
              gl.finish(); const pixels=new Uint8Array(canvas.width*canvas.height*4);
              gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
              let count=0; for(let i=0;i<pixels.length;i+=4) if(pixels[i]<210) count++;
              return count; })()"""), 100)
            browser.screenshot("surface-%s" % width)
            if width == 1280:
                before = snapshot(self.workspace)
                browser.evaluate("panel.setRecipe(%s)" % json.dumps({
                    "op": "thicken", "surface": cylinder(), "thickness_nm": 6 * MM, "inward": True}))
                browser.click('[data-surface-action="apply"]')
                browser.wait("!panel.model.busy && panel.model.error")
                self.assertIn("focal crossing", browser.evaluate("panel.model.error"))
                self.assertEqual(snapshot(self.workspace), before)
                self.assertAlmostEqual(browser.evaluate(
                    "panel.model.response.result.properties.volume_mm3"), 27.5 * math.pi, delta=1e-5)
                browser.click('[data-surface-action="reload"]')
                browser.wait("!panel.model.busy && panel.model.error===''")
                self.assertEqual(snapshot(self.workspace), before)
                for _ in range(2):
                    self.assertEqual(self.call("POST", "/api/command",
                        {"command": "undo", "args": {}})[0], 200)
                browser.click('[data-surface-action="reload"]')
                browser.wait("!panel.model.busy && panel.model.response.empty")
                self.assertEqual(browser.evaluate("panel.model.triangles"), 0)
                self.assertEqual(browser.evaluate(
                    "document.querySelector('.surfaces-metrics').textContent"), "")
                for _ in range(2):
                    self.assertEqual(self.call("POST", "/api/command",
                        {"command": "redo", "args": {}})[0], 200)
                browser.click('[data-surface-action="reload"]')
                browser.wait("!panel.model.busy && panel.model.triangles>0")
        self.assertEqual(len(self.workspace.undo_stack), 2)

    def test_browser_desktop_actual_click_and_mesh(self):
        self.browser(1280)

    def test_browser_mobile_actual_click_and_mesh(self):
        self.browser(390)

    def browser_download(self, width):
        self.assertEqual(self.post(polygon_plate())[0], 200)
        before = snapshot(self.workspace)
        path = ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
        spec = importlib.util.spec_from_file_location("surface_download_browser_helper", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        downloads = self.directory / "browser-downloads"
        downloads.mkdir()
        browser = module.BrowserSession(
            "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
            self.directory / "download-browser", width=width, height=900, source_root=ROOT)
        with browser:
            browser.call("Browser.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(downloads)})
            browser.call("Page.navigate", {"url": "http://127.0.0.1:%s/" % self.server.server_port})
            browser.wait("window.panel && !panel.model.busy && panel.model.response")
            draft = json.dumps(cylinder(), indent=2)
            browser.click('[data-surface-field="recipe"]')
            browser.call("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2,
                "windowsVirtualKeyCode": 65})
            browser.call("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2,
                "windowsVirtualKeyCode": 65})
            browser.call("Input.insertText", {"text": draft})
            self.assertEqual(browser.evaluate("document.querySelector('.surfaces-recipe').value"), draft)
            browser.evaluate("window.downloadOriginalNode=document.querySelector('.surfaces-recipe')")
            for fmt in ("step", "brep"):
                browser.click('[data-surface-action="download-%s"]' % fmt)
                browser.wait("!panel.model.busy && !panel.model.error && "
                             "document.querySelector('.surfaces-status').textContent.includes('downloaded')")
                target = downloads / ("surface." + fmt)
                deadline = time.monotonic() + 20
                while not target.is_file() or not target.stat().st_size:
                    if time.monotonic() >= deadline:
                        self.fail("Real browser download did not create " + str(target))
                    time.sleep(.1)
                self.independent_export_readback(target.read_bytes(), fmt, False)
                self.assertEqual(browser.evaluate("document.querySelector('.surfaces-recipe').value"), draft)
                self.assertTrue(browser.evaluate(
                    "downloadOriginalNode===document.querySelector('.surfaces-recipe')"))
                self.assertEqual(snapshot(self.workspace), before)
            self.assertFalse(browser.evaluate("document.documentElement.scrollWidth>innerWidth"))
            browser.screenshot("surface-download-%s" % width)

    def test_browser_desktop_real_download_keeps_unsaved_recipe(self):
        self.browser_download(1280)

    def test_browser_mobile_real_download_keeps_unsaved_recipe(self):
        self.browser_download(390)


def run_acceptance():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    parser.add_argument("--repository-check", action="store_true")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc)
    RUN_DIR = args.evidence_dir / (stamp.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    paths = [Path(__file__).resolve(), APP / "industrial_surfaces.py",
             APP / "web/js/surfaces-panel.js", APP / "web/css/surfaces-panel.css",
             ROOT / "docs/design/industrial-surfaces.md", ROOT / "docs/design/surface-download.md",
             APP / "server.py",
             PACKAGE / "cadcore/kernel/surfaces.py", PACKAGE / "cadcore/kernel/surfaces_worker.py"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    clock = time.monotonic()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    with (RUN_DIR / "tests.log").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    checks = []
    if args.repository_check and result.wasSuccessful() and not result.skipped:
        for label, command in (
            ("source-scan", [sys.executable, "-B", str(ROOT / "tools/acceptance.py"),
                             "--stage", "scan", "--out", str(RUN_DIR / "source-scan")]),
            ("discovery", [sys.executable, "-B", str(ROOT / "tools/acceptance.py"),
                           "--suite", str(APP / "tests"), "--top", str(APP),
                           "--pattern", "test_industrial_surfaces.py"]),
        ):
            with (RUN_DIR / (label + ".log")).open("xb") as log:
                child = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            checks.append({"label": label, "rc": child.returncode})
            if child.returncode:
                break
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    summary = {"verdict": "PASS" if result.wasSuccessful() and result.testsRun > 0
               and not result.skipped and before == after and all(c["rc"] == 0 for c in checks) else "FAIL",
               "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "uid": os.getuid(), "elapsed_s": time.monotonic() - clock,
               "started_at": stamp.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
               "sources_before": before, "sources_after": after, "source_drift": before != after,
               "repository_checks": checks, "evidence_dir": str(RUN_DIR),
               "production_gateway": "NOT_INTEGRATED",
               "failures_detail": [dict(test=str(t), traceback=trace)
                                   for t, trace in result.failures + result.errors]}
    (RUN_DIR / "result.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("SURFACES_WEB_ACCEPTANCE verdict=%s tests=%s failures=%s errors=%s skipped=%s drift=%s evidence=%s"
          % (summary["verdict"], summary["tests"], summary["failures"], summary["errors"],
             summary["skipped"], summary["source_drift"], RUN_DIR))
    if summary["verdict"] != "PASS":
        print((RUN_DIR / "tests.log").read_text()[-10000:])
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_acceptance())
