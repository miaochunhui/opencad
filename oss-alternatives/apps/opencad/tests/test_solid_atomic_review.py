"""Independent read-only-source OpenCAD atomicity review with real HTTP/CAD.

Run directly with --source-root /data/dev/infra. Only this test file is owned
by the review seat; the application, registry and CAD implementation are loaded
from the chosen main tree without patching or mocking them. Seven feature
modules must exist before the HTTP run. Missing modules are INVALID, never skips.
All documents, logs and failure evidence live below seat-atomic-review.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime
import hashlib
from http import client
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

SOURCE_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-atomic-review")
RUN_DIR = None
SERVER = None
REGISTRY = None
KERNEL = None
MM = 1_000_000
FEATURE_MODULES = ("sweep", "loft", "edges", "shell", "pattern", "step", "clearance")
HTTP_EVENTS = []


def load_real_source(root):
    """Ensure module origins are the reviewed tree, not a cached seat overlay."""
    global SOURCE_ROOT, SERVER, REGISTRY, KERNEL
    SOURCE_ROOT = root.resolve()
    app = SOURCE_ROOT / "oss-alternatives/apps/opencad"
    packages = SOURCE_ROOT / "oss-alternatives/packages"
    for path in (app, packages / "cadcore", packages / "dxf"):
        sys.path.insert(0, str(path))
    SERVER = importlib.import_module("server")
    REGISTRY = importlib.import_module("cadcore.kernel.features_registry")
    KERNEL = importlib.import_module("cadcore.kernel.subproc")
    commands = importlib.import_module("solid_commands")
    features = importlib.import_module("cadcore.ops.sw_features")
    expected = {
        SERVER: app / "server.py",
        commands: app / "solid_commands.py",
        REGISTRY: packages / "cadcore/cadcore/kernel/features_registry.py",
        KERNEL: packages / "cadcore/cadcore/kernel/subproc.py",
        features: packages / "cadcore/cadcore/ops/sw_features.py",
    }
    for module, path in expected.items():
        if Path(module.__file__).resolve() != path.resolve():
            raise AssertionError("review loaded a foreign source module: %s" % module.__file__)
    for name in FEATURE_MODULES:
        module = REGISTRY.module(name)
        path = packages / ("cadcore/cadcore/kernel/features_%s.py" % name)
        if Path(module.__file__).resolve() != path.resolve():
            raise AssertionError("review loaded a foreign feature module: %s" % name)


class SolidAtomicReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUN_DIR
        cls.discovery = RUN_DIR is None
        if cls.discovery:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            RUN_DIR = EVIDENCE_ROOT / ("%s-discovery-%s" % (stamp, uuid.uuid4().hex))
            RUN_DIR.mkdir(parents=True, exist_ok=False)
            cls.discovery_started = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cls.discovery_clock = time.perf_counter()
            cls.discovery_cases = []
            cls.discovery_before = {}
            cls.discovery_ready = False
            cls.saved_env = {key: os.environ.get(key) for key in (
                "TMPDIR", "CADCORE_SUBPROC_KEEP_WORK", "OPENCAD_CADCORE_ROOT",
            )}
            cls.saved_tempdir = tempfile.tempdir
            os.environ["TMPDIR"] = str(RUN_DIR)
            os.environ["CADCORE_SUBPROC_KEEP_WORK"] = "1"
            os.environ["OPENCAD_CADCORE_ROOT"] = str(
                SOURCE_ROOT / "oss-alternatives/packages/cadcore"
            )
            tempfile.tempdir = str(RUN_DIR)
            HTTP_EVENTS.clear()
            cls.addClassCleanup(cls.finish_discovery)
            (RUN_DIR / "START.json").write_text(json.dumps({
                "verdict": "RUNNING", "source_root": str(SOURCE_ROOT),
                "started_utc": cls.discovery_started, "scope": "unittest discovery",
            }, indent=2), encoding="utf-8")
            cls.discovery_before = hashes(source_paths(SOURCE_ROOT))
        if SERVER is None:
            load_real_source(SOURCE_ROOT)
        kernel = KERNEL.SubprocessKernel(
            work_dir=RUN_DIR / "backend-check",
            keep_work_dir=True,
        )
        try:
            report = kernel.probe_report()
            if not report.available:
                raise AssertionError("real FreeCAD required: %s" % report.as_dict())
            if RUN_DIR is not None:
                (RUN_DIR / "backend.json").write_text(
                    json.dumps(report.as_dict(), indent=2), encoding="utf-8"
                )
        finally:
            kernel.close()
        if cls.discovery:
            cls.discovery_ready = True

    @classmethod
    def tearDownClass(cls):
        if cls.discovery:
            cls.discovery_after = hashes(source_paths(SOURCE_ROOT))
            if cls.discovery_before != cls.discovery_after:
                raise AssertionError("reviewed source changed during unittest discovery")

    @classmethod
    def finish_discovery(cls):
        global RUN_DIR
        finished = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            after = hashes([path for path in source_paths(SOURCE_ROOT) if path.is_file()])
            unchanged = bool(cls.discovery_before) and cls.discovery_before == after
            failures = sum(case["failures"] for case in cls.discovery_cases)
            errors = sum(case["errors"] for case in cls.discovery_cases)
            skipped = sum(case["skipped"] for case in cls.discovery_cases)
            passed = (cls.discovery_ready and cls.discovery_cases and not failures
                      and not errors and not skipped and unchanged)
            report = {
                "verdict": "PASS" if passed else "FAIL",
                "source_root": str(SOURCE_ROOT), "run_dir": str(RUN_DIR),
                "scope": "unittest discovery", "tests": len(cls.discovery_cases),
                "failures": failures, "errors": errors, "skipped": skipped,
                "cases": cls.discovery_cases,
                "started_utc": cls.discovery_started, "finished_utc": finished,
                "elapsed_s": time.perf_counter() - cls.discovery_clock,
                "source_before_sha256": cls.discovery_before,
                "source_after_sha256": after, "source_unchanged": unchanged,
                "http_requests": len(HTTP_EVENTS),
                "negative_count": sum(event["response"].get("ok") is False
                                      for event in HTTP_EVENTS),
            }
            (RUN_DIR / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (RUN_DIR / "time.json").write_text(json.dumps({
                key: report[key] for key in ("started_utc", "finished_utc", "elapsed_s")
            }, indent=2), encoding="utf-8")
            (RUN_DIR / "negative-index.json").write_text(json.dumps([
                event for event in HTTP_EVENTS if event["response"].get("ok") is False
            ], indent=2), encoding="utf-8")
            print("SOLID_ATOMIC_DISCOVERY verdict=%s tests=%s result=%s"
                  % (report["verdict"], report["tests"], RUN_DIR / "result.json"))
        finally:
            for key, value in cls.saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            tempfile.tempdir = cls.saved_tempdir
            RUN_DIR = None

    def run(self, result=None):
        result = result if result is not None else self.defaultTestResult()
        fields = ("failures", "errors", "skipped")
        before = {field: len(getattr(result, field)) for field in fields}
        try:
            return super().run(result)
        finally:
            if getattr(type(self), "discovery", False):
                outcome = {"test": self._testMethodName}
                outcome.update({field: len(getattr(result, field)) - before[field]
                                for field in fields})
                type(self).discovery_cases.append(outcome)
                with (RUN_DIR / "tests.log").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(outcome) + "\n")

    def setUp(self):
        self.directory = RUN_DIR / self._testMethodName
        self.directory.mkdir(parents=True, exist_ok=False)
        self.httpd = SERVER.build_server(
            None, host="127.0.0.1", port=0, locale="en", standard="iso",
            out_root=str(self.directory),
        )
        self.workspace = self.httpd.workspace
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.port = self.httpd.server_address[1]
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        self.assertEqual(Path(self.workspace.out_root), self.directory)
        self.assertEqual(Path(SERVER.__file__).resolve(),
                         SOURCE_ROOT / "oss-alternatives/apps/opencad/server.py")
        self.assertEqual(Path(KERNEL.__file__).resolve(),
                         SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore/kernel/subproc.py")
        self.journal = self.directory / "http.jsonl"
        self.events = []
        self.feature_bindings = []
        self.rejections = []
        self.assertTrue(self.get("/api/state")["ok"])

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AssertionError("independent test HTTP server failed to stop")

    def request(self, method, route, body=None):
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        connection = client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        start = time.perf_counter()
        try:
            connection.request(
                method, route, body=encoded,
                headers={} if encoded is None else {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read()
            status = response.status
        finally:
            connection.close()
        payload = json.loads(raw.decode("utf-8"))
        event = {
            "test": self._testMethodName, "method": method, "route": route,
            "request": body, "status": status, "response": payload,
            "elapsed_s": time.perf_counter() - start,
            "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with self.journal.open("a", encoding="utf-8") as log:
            log.write(json.dumps(event) + "\n")
        self.events.append(event)
        HTTP_EVENTS.append(event)
        self.assertEqual(status, 200, payload)
        return payload

    def get(self, route):
        return self.request("GET", route)

    def command(self, name, args=None):
        return self.request("POST", "/api/command", {"command": name, "args": args or {}})

    def success(self, name, args=None):
        response = self.command(name, args)
        self.assertTrue(response["ok"], response)
        return response

    def create_feature(self, name, args, kind, *, response_is_feature=True):
        """Bind identity to the real document delta, not an API UUID assumption."""
        before = self.state_document()["features"]
        identifiers = {feature["uuid"] for feature in before}
        snapshot = self.snapshot()
        response = self.success(name, args)
        after = self.state_document()["features"]
        added = [feature for feature in after if feature["uuid"] not in identifiers]
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(len(added), 1, after)
        feature = added[0]
        self.assertEqual(feature["kind"], kind)
        self.assertIs(feature["suppressed"], False)
        self.assertIsInstance(feature["uuid"], str)
        self.assertTrue(feature["uuid"])
        if response_is_feature:
            self.assertEqual(response["uuid"], feature["uuid"])
        self.assertEqual(len(self.workspace.undo_stack), len(snapshot["undo"]) + 1)
        self.assertEqual(self.workspace.undo_stack[-1], snapshot["doc"])
        self.assertEqual(self.workspace.redo_stack, [])
        self.feature_bindings.append({
            "command": name, "api_uuid": response["uuid"],
            "feature_uuid": feature["uuid"], "kind": kind,
            "before_features": sorted(identifiers),
            "after_features": [item["uuid"] for item in after],
        })
        (self.directory / "feature-bindings.json").write_text(
            json.dumps(self.feature_bindings, indent=2), encoding="utf-8"
        )
        return feature["uuid"]

    def mesh(self):
        response = self.get("/api/mesh")
        self.assertTrue(response["ok"], response)
        text = self.get("/api/state")["doc"]
        self.assertEqual(response["document_sha256"],
                         hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(self.snapshot()["mesh_cache"], response)
        return response

    def volume(self):
        mesh = self.mesh()
        self.assertTrue(mesh["solids"], mesh)
        solid = mesh["solids"][0]
        self.assertEqual(solid["solid_count"], 1)
        self.assertGreater(len(solid["triangles"]), 10)
        return solid["properties"]["volume_nm3"] / 1e18

    def top_mesh_covers_opening_center(self, mesh):
        """Inspect actual planar triangles, with the solid box as a positive control."""
        solid = mesh["solids"][0]
        vertices = solid["vertices_mm"]
        point = (10.25, 8.25)
        top = []
        for indices in solid["triangles"]:
            triangle = [vertices[index] for index in indices]
            if all(abs(vertex[2] - 12) < 1e-7 for vertex in triangle):
                top.append(triangle)
        self.assertTrue(top, "real top rim/cap triangles must be measured")
        for triangle in top:
            sides = []
            for index in range(3):
                a, b = triangle[index], triangle[(index + 1) % 3]
                sides.append((b[0] - a[0]) * (point[1] - a[1])
                             - (b[1] - a[1]) * (point[0] - a[0]))
            if min(sides) >= -1e-7 or max(sides) <= 1e-7:
                return True
        return False

    def state_document(self):
        return json.loads(self.get("/api/state")["doc"])

    def snapshot(self):
        with self.workspace._lock:
            return {
                "doc": self.workspace.l1_text(),
                "undo": list(self.workspace.undo_stack),
                "redo": list(self.workspace.redo_stack),
                "mesh_cache": copy.deepcopy(getattr(self.workspace, "_mesh_cache", None)),
            }

    def reject_atomically(self, name, args, *, reason=None, detail=None):
        before = self.snapshot()
        response = self.command(name, args)
        after = self.snapshot()
        changed = [field for field in before if before[field] != after[field]]
        self.rejections.append({
            "command": name, "args": args, "response": response,
            "before": before, "after": after, "changed_fields": changed,
        })
        (self.directory / "transactions.json").write_text(
            json.dumps(self.rejections, indent=2), encoding="utf-8"
        )
        for field in before:
            if before[field] != after[field]:
                path = self.directory / ("atomicity-%s.json" % field)
                path.write_text(json.dumps({"before": before[field], "after": after[field]},
                                           indent=2), encoding="utf-8")
        self.assertFalse(response["ok"], response)
        for field in before:
            self.assertEqual(after[field], before[field],
                             "rejected command changed %s" % field)
        self.assertEqual(response.get("error"), "command_failed", response)
        if reason is not None:
            self.assertEqual(response.get("cadcore_key"), reason, response)
        if detail is not None:
            self.assertIn(detail, response.get("detail", ""), response)
        return response

    def reject_mesh_atomically(self, detail, *, reason):
        before = self.snapshot()
        response = self.get("/api/mesh")
        after = self.snapshot()
        self.rejections.append({
            "route": "/api/mesh", "response": response,
            "before": before, "after": after,
            "changed_fields": [field for field in before if before[field] != after[field]],
        })
        (self.directory / "transactions.json").write_text(
            json.dumps(self.rejections, indent=2), encoding="utf-8"
        )
        self.assertFalse(response["ok"], response)
        self.assertTrue(response.get("error"), response)
        self.assertEqual(response.get("cadcore_key"), reason, response)
        self.assertIn(detail, response.get("detail", ""), response)
        self.assertNotIn("TypeError", response["detail"])
        self.assertEqual(after, before, "failed mesh changed document, histories or cache")
        return response

    def seed_both_histories_and_cache(self):
        """Prove rollback comparisons cover populated stacks and an actual mesh."""
        before = self.snapshot()
        self.success("add_circle", {"center": [50 * MM, 0], "radius_nm": MM})
        self.success("undo")
        mesh = self.mesh()
        after = self.snapshot()
        self.assertEqual(after["doc"], before["doc"])
        self.assertEqual(after["undo"], before["undo"])
        self.assertTrue(after["undo"])
        self.assertTrue(after["redo"])
        self.assertIsNotNone(after["mesh_cache"])
        self.assertEqual(after["mesh_cache"], mesh)
        return mesh

    def circle(self, radius=10):
        self.success("add_circle", {"center": [0, 0], "radius_nm": radius * MM})

    def base_cylinder(self):
        self.circle()
        identifier = self.create_feature(
            "extrude", {"distance_nm": 12 * MM}, "extrude", response_is_feature=False,
        )
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 12, places=6)
        features = self.state_document()["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["uuid"], identifier)
        self.assertEqual(features[0]["params"]["distance_nm"], 12 * MM)
        return identifier

    def base_box(self):
        self.success("add_polyline", {"points_nm": [
            [0, 0], [20 * MM, 0], [20 * MM, 16 * MM], [0, 16 * MM], [0, 0],
        ]})
        identifier = self.create_feature(
            "extrude", {"distance_nm": 12 * MM}, "extrude", response_is_feature=False,
        )
        self.assertAlmostEqual(self.volume(), 20 * 16 * 12, places=6)
        return identifier

    def shell_cylinder(self, *, explicit_inward=True):
        base = self.base_cylinder()
        params = {"thickness_nm": 2 * MM, "face_indices": [3]}
        if explicit_inward:
            params["inward"] = True
        feature = self.create_feature("shell_solid", params, "shell")
        self.assertAlmostEqual(self.volume(), math.pi * (100 * 12 - 64 * 10), places=6)
        if not explicit_inward:
            self.assertNotIn("inward", self.params_of(feature))
        return base, feature

    def params_of(self, identifier):
        return next(feature["params"] for feature in self.state_document()["features"]
                    if feature["uuid"] == identifier)

    def restore_fixture(self, change):
        """Use the real undo/redo restore implementation on local test documents."""
        before = self.snapshot()
        with self.workspace._lock:
            payload = json.loads(self.workspace.l1_text())
            change(payload)
            self.workspace._restore(json.dumps(payload))
        restored = self.state_document()
        expected_fields = {key: value for key, value in payload.items() if key != "diagnostics"}
        actual_fields = {key: value for key, value in restored.items() if key != "diagnostics"}
        self.assertEqual(actual_fields, expected_fields)
        original_diagnostics = payload.get("diagnostics", [])
        restored_diagnostics = restored.get("diagnostics", [])
        for diagnostic in original_diagnostics:
            self.assertIn(diagnostic, restored_diagnostics)
        for diagnostic in restored_diagnostics:
            if diagnostic not in original_diagnostics:
                self.assertEqual(diagnostic["code"], "unknown_field_preserved", diagnostic)
                self.assertEqual(diagnostic["severity"], "warning", diagnostic)
                self.assertTrue(diagnostic["path"], diagnostic)
        after = self.snapshot()
        for field in ("undo", "redo", "mesh_cache"):
            self.assertEqual(after[field], before[field], "restore fixture changed %s" % field)
        (self.directory / "restored-fixture.json").write_text(
            json.dumps(restored, indent=2), encoding="utf-8"
        )
        return restored

    def assert_unknown_parameter_diagnostic(self, restored, identifier, key):
        index = next(index for index, feature in enumerate(restored["features"])
                     if feature["uuid"] == identifier)
        path = "$.features[%s].params.%s" % (index, key)
        matching = [diagnostic for diagnostic in restored.get("diagnostics", [])
                    if diagnostic.get("code") == "unknown_field_preserved"
                    and diagnostic.get("path") == path]
        self.assertEqual(len(matching), 1, restored)
        self.assertEqual(matching[0]["severity"], "warning")
        self.assertIn(key, restored["features"][index]["params"])

    def test_shell_box_http_success_commits_and_replays_real_mass_and_cache(self):
        self.base_box()
        original_mesh = self.seed_both_histories_and_cache()
        self.assertTrue(self.top_mesh_covers_opening_center(original_mesh))
        before = self.snapshot()
        feature = self.create_feature(
            "shell_solid", {"thickness_nm": 2 * MM, "face_indices": [6], "inward": True},
            "shell",
        )
        hollow = self.snapshot()
        self.assertEqual(len(hollow["undo"]), len(before["undo"]) + 1)
        self.assertEqual(hollow["undo"][-1], before["doc"])
        self.assertEqual(hollow["redo"], [])
        self.assertEqual(self.params_of(feature),
                         {"thickness_nm": 2 * MM, "face_indices": [6], "inward": True})
        self.assertAlmostEqual(self.volume(), 20 * 16 * 12 - 16 * 12 * 10, places=6)
        hollow_mesh = self.mesh()
        self.assertFalse(self.top_mesh_covers_opening_center(hollow_mesh),
                         "the specified opening is still covered by a top cap")
        self.assertNotEqual(hollow_mesh["document_sha256"], original_mesh["document_sha256"])
        self.success("undo")
        undone = self.snapshot()
        self.assertEqual(undone["doc"], before["doc"])
        self.assertEqual(undone["undo"], before["undo"])
        self.assertEqual(undone["redo"], [hollow["doc"]])
        self.assertAlmostEqual(self.volume(), 20 * 16 * 12, places=6)
        self.assertEqual(self.mesh(), original_mesh)
        self.success("redo")
        redone = self.snapshot()
        self.assertEqual(redone["doc"], hollow["doc"])
        self.assertEqual(redone["undo"], hollow["undo"])
        self.assertEqual(redone["redo"], [])
        self.assertAlmostEqual(self.volume(), 20 * 16 * 12 - 16 * 12 * 10, places=6)
        self.assertEqual(self.mesh(), hollow_mesh)

    def test_successful_shell_edit_replaces_cached_mass_and_replays_both_histories(self):
        _, feature = self.shell_cylinder()
        original_mesh = self.seed_both_histories_and_cache()
        before = self.snapshot()
        params = self.params_of(feature)
        params["thickness_nm"] = MM
        self.success("edit_feature_params", {"feature_uuid": feature, "params": params})
        edited = self.snapshot()
        self.assertEqual(self.params_of(feature), params)
        self.assertEqual(len(edited["undo"]), len(before["undo"]) + 1)
        self.assertEqual(edited["undo"][-1], before["doc"])
        self.assertEqual(edited["redo"], [])
        self.assertAlmostEqual(self.volume(), math.pi * (100 * 12 - 81 * 11), places=6)
        edited_mesh = self.mesh()
        self.assertNotEqual(edited_mesh["document_sha256"], original_mesh["document_sha256"])
        self.success("undo")
        self.assertEqual(self.snapshot()["doc"], before["doc"])
        self.assertEqual(self.snapshot()["undo"], before["undo"])
        self.assertEqual(self.snapshot()["redo"], [edited["doc"]])
        self.assertEqual(self.mesh(), original_mesh)
        self.success("redo")
        self.assertEqual(self.snapshot()["doc"], edited["doc"])
        self.assertEqual(self.snapshot()["undo"], edited["undo"])
        self.assertEqual(self.snapshot()["redo"], [])
        self.assertEqual(self.mesh(), edited_mesh)

    def test_failed_occ_shell_keeps_document_both_histories_and_cached_mesh(self):
        self.base_box()
        original_mesh = self.seed_both_histories_and_cache()
        response = self.reject_atomically(
            "shell_solid", {"thickness_nm": 40 * MM, "face_indices": [6], "inward": True},
            reason="op_failed", detail="OCCError",
        )
        self.assertIn("rc=3", response["detail"])
        self.assertIn("makeThickness", response["detail"])
        self.assertEqual(self.mesh(), original_mesh)

    def test_failed_edit_of_real_shell_is_atomic(self):
        _, feature = self.shell_cylinder()
        original_mesh = self.seed_both_histories_and_cache()
        params = self.params_of(feature)
        params["thickness_nm"] = 40 * MM
        response = self.reject_atomically(
            "edit_feature_params", {"feature_uuid": feature, "params": params},
            reason="op_failed", detail="makeThickness produced no valid solid",
        )
        self.assertIn("rc=3", response["detail"])
        self.assertEqual(self.mesh(), original_mesh)

    def test_unknown_command_parameters_and_missing_required_are_rejected(self):
        self.base_cylinder()
        self.seed_both_histories_and_cache()
        self.reject_atomically(
            "shell_solid", {"thickness_nm": MM, "face_indices": [3], "unexpected": 1},
            reason="bad_solid_arguments",
        )
        self.reject_atomically("shell_solid", {"face_indices": [3]})

    def test_invalid_feature_uuid_and_suppression_argument_type(self):
        base = self.base_cylinder()
        self.seed_both_histories_and_cache()
        for identifier in ("unknown-feature", None, [], True):
            with self.subTest(identifier=identifier):
                self.reject_atomically(
                    "suppress_solid", {"feature_uuid": identifier, "suppressed": True},
                    reason="bad_solid_arguments",
                )
        for suppressed in (1, "false", None, []):
            with self.subTest(suppressed=suppressed):
                self.reject_atomically(
                    "suppress_solid", {"feature_uuid": base, "suppressed": suppressed},
                    reason="bad_solid_arguments",
                )

    def test_schema_edit_cannot_remove_required_or_add_unknown_key(self):
        _, feature = self.shell_cylinder()
        self.seed_both_histories_and_cache()
        params = self.params_of(feature)
        removed = dict(params)
        removed.pop("thickness_nm")
        added = dict(params, typo_thickness_nm=MM)
        for new in (removed, added, [], None):
            with self.subTest(params=new):
                self.reject_atomically(
                    "edit_feature_params", {"feature_uuid": feature, "params": new},
                    reason="bad_solid_arguments",
                )

    def test_valid_edit_optional_inward_parameter_when_previously_omitted(self):
        _, feature = self.shell_cylinder(explicit_inward=False)
        params = self.params_of(feature)
        params["inward"] = False
        self.success("edit_feature_params", {"feature_uuid": feature, "params": params})
        self.assertFalse(self.params_of(feature)["inward"])
        self.assertGreater(self.volume(), 0)

    def test_suppression_and_undo_redo_replay_real_geometry(self):
        _, feature = self.shell_cylinder()
        hollow = self.get("/api/state")["doc"]
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 12, places=6)
        self.success("undo")
        self.assertEqual(self.get("/api/state")["doc"], hollow)
        self.assertAlmostEqual(self.volume(), math.pi * (100 * 12 - 64 * 10), places=6)
        self.success("redo")
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 12, places=6)
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": False})
        self.assertAlmostEqual(self.volume(), math.pi * (100 * 12 - 64 * 10), places=6)

    def test_all_suppressed_bad_scalar_params_are_still_rejected(self):
        base = self.base_cylinder()
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        self.assertEqual(self.mesh()["solids"], [])
        self.seed_both_histories_and_cache()
        params = self.params_of(base)
        params["distance_nm"] = -MM
        self.reject_atomically("edit_feature_params", {"feature_uuid": base, "params": params})

    def test_all_suppressed_invalid_shell_faces_are_still_rejected(self):
        base, feature = self.shell_cylinder()
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        self.assertEqual(self.mesh()["solids"], [])
        self.seed_both_histories_and_cache()
        params = self.params_of(feature)
        params["face_indices"] = [0]
        self.reject_atomically("edit_feature_params", {"feature_uuid": feature, "params": params})

    def test_all_suppressed_bad_schema_is_rejected_with_cache_and_both_histories(self):
        base, feature = self.shell_cylinder()
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        self.assertTrue(all(item["suppressed"] is True
                            for item in self.state_document()["features"]))
        empty_mesh = self.seed_both_histories_and_cache()
        self.assertEqual(empty_mesh["solids"], [])
        self.assertEqual(empty_mesh["reason"], "no_solid_feature")
        params = self.params_of(feature)
        missing = dict(params)
        missing.pop("thickness_nm")
        variants = (missing, dict(params, unknown_geometry_option=1), [], None,
                    dict(params, thickness_nm=True), dict(params, inward=1),
                    dict(params, face_indices=[True]))
        for invalid in variants:
            with self.subTest(params=invalid):
                self.reject_atomically(
                    "edit_feature_params", {"feature_uuid": feature, "params": invalid},
                )
                self.assertEqual(self.mesh(), empty_mesh)

    def test_all_suppressed_out_of_range_face_requires_real_topology_validation(self):
        base, feature = self.shell_cylinder()
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        empty_mesh = self.seed_both_histories_and_cache()
        self.assertEqual(empty_mesh["solids"], [])
        params = dict(self.params_of(feature), face_indices=[999])
        self.reject_atomically(
            "edit_feature_params", {"feature_uuid": feature, "params": params},
            reason="op_failed", detail="face",
        )
        self.assertEqual(self.mesh(), empty_mesh)

    def test_all_suppressed_overthick_shell_requires_real_geometry_validation(self):
        base = self.base_box()
        feature = self.create_feature(
            "shell_solid", {"thickness_nm": 2 * MM, "face_indices": [6], "inward": True},
            "shell",
        )
        self.assertAlmostEqual(self.volume(), 20 * 16 * 12 - 16 * 12 * 10, places=6)
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        empty_mesh = self.seed_both_histories_and_cache()
        self.assertEqual(empty_mesh["solids"], [])
        params = dict(self.params_of(feature), thickness_nm=40 * MM)
        response = self.reject_atomically(
            "edit_feature_params", {"feature_uuid": feature, "params": params},
            reason="op_failed", detail="makeThickness",
        )
        self.assertIn("OCCError", response["detail"])
        self.assertIn("rc=3", response["detail"])
        self.assertEqual(self.mesh(), empty_mesh)

    def test_suppressed_unknown_restored_parameter_is_not_silently_accepted(self):
        base = self.base_cylinder()
        self.success("suppress_solid", {"feature_uuid": base, "suppressed": True})
        self.seed_both_histories_and_cache()

        def add_unknown(doc):
            doc["features"][0]["params"]["unknown_geometry_option"] = 1

        restored = self.restore_fixture(add_unknown)
        self.assertEqual(restored["features"][0]["params"]["unknown_geometry_option"], 1)
        self.assert_unknown_parameter_diagnostic(restored, base, "unknown_geometry_option")
        self.reject_atomically(
            "suppress_solid", {"feature_uuid": base, "suppressed": False},
        )

    def test_restored_unknown_shell_parameter_cannot_be_edited_and_swallowed(self):
        _, feature = self.shell_cylinder()
        self.seed_both_histories_and_cache()

        def add_unknown(doc):
            next(item for item in doc["features"] if item["uuid"] == feature
                 )["params"]["unknown_geometry_option"] = 1

        restored = self.restore_fixture(add_unknown)
        params = next(item["params"] for item in restored["features"] if item["uuid"] == feature)
        self.assertEqual(params["unknown_geometry_option"], 1)
        self.assert_unknown_parameter_diagnostic(restored, feature, "unknown_geometry_option")
        params["thickness_nm"] = MM
        self.reject_atomically("edit_feature_params", {"feature_uuid": feature, "params": params})

    def test_restored_unknown_shell_parameter_mesh_is_fail_loud(self):
        _, feature = self.shell_cylinder()
        self.seed_both_histories_and_cache()

        def add_unknown(doc):
            next(item for item in doc["features"] if item["uuid"] == feature
                 )["params"]["typo_thickness_nm"] = MM

        restored = self.restore_fixture(add_unknown)
        self.assertIn("typo_thickness_nm", restored["features"][-1]["params"])
        self.assert_unknown_parameter_diagnostic(restored, feature, "typo_thickness_nm")
        self.reject_mesh_atomically("unknown solid feature parameters", reason="bad_feature_params")

    def test_restored_nonboolean_suppression_is_not_coerced_or_cached(self):
        base = self.base_cylinder()
        self.seed_both_histories_and_cache()
        for suppressed in (1, "false", None, []):
            with self.subTest(suppressed=suppressed):
                def corrupt(doc):
                    next(item for item in doc["features"] if item["uuid"] == base
                         )["suppressed"] = suppressed

                restored = self.restore_fixture(corrupt)
                self.assertEqual(restored["features"][0]["suppressed"], suppressed)
                self.assertIs(type(restored["features"][0]["suppressed"]), type(suppressed))
                self.reject_mesh_atomically("suppressed must be boolean", reason="bad_feature_params")

    def test_registry_rejects_restored_unknown_parameters_before_build(self):
        _, feature = self.shell_cylinder()
        doc = self.state_document()
        params = dict(self.params_of(feature), unknown_geometry_option=1)
        kernel = KERNEL.SubprocessKernel()
        try:
            previous = kernel.shape_from_document(dict(doc, features=doc["features"][:1]))
            with self.assertRaises((ValueError, KERNEL.CadError)):
                REGISTRY.feature_step("shell", params, previous, doc, kernel)
        finally:
            kernel.close()

    def test_suppressing_required_prior_solid_rolls_back(self):
        base, _ = self.shell_cylinder()
        self.seed_both_histories_and_cache()
        self.reject_atomically(
            "suppress_solid", {"feature_uuid": base, "suppressed": True},
            reason="missing_base_solid",
        )

    def test_legacy_edit_solid_validates_suppressed_shell_shadow_atomically(self):
        self.circle(radius=10)
        base = self.create_feature(
            "extrude", {"distance_nm": 10 * MM}, "extrude", response_is_feature=False,
        )
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 10, places=6)
        shell = self.create_feature(
            "shell_solid", {"thickness_nm": MM, "face_indices": [3], "inward": True},
            "shell",
        )
        hollow_volume = math.pi * (100 * 10 - 81 * 9)
        self.assertAlmostEqual(self.volume(), hollow_volume, places=6)
        hollow_mesh = self.mesh()
        self.success("suppress_solid", {"feature_uuid": shell, "suppressed": True})
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 10, places=6)
        original_mesh = self.seed_both_histories_and_cache()
        before = self.snapshot()
        self.assertTrue(before["undo"])
        self.assertTrue(before["redo"])
        self.assertEqual(self.params_of(base)["distance_nm"], 10 * MM)
        self.assertIs(next(item for item in self.state_document()["features"]
                           if item["uuid"] == shell)["suppressed"], True)

        response = self.reject_atomically(
            "edit_solid", {"feature_uuid": base, "distance_nm": MM // 2},
            reason="op_failed", detail="makeThickness",
        )
        self.assertIn("rc=3", response["detail"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.mesh(), original_mesh)
        self.assertEqual(self.params_of(base)["distance_nm"], 10 * MM)

        self.success("suppress_solid", {"feature_uuid": shell, "suppressed": False})
        restored = self.snapshot()
        self.assertEqual(len(restored["undo"]), len(before["undo"]) + 1)
        self.assertEqual(restored["undo"][-1], before["doc"])
        self.assertEqual(restored["redo"], [])
        self.assertIs(next(item for item in self.state_document()["features"]
                           if item["uuid"] == shell)["suppressed"], False)
        self.assertAlmostEqual(self.volume(), hollow_volume, places=6)
        self.assertEqual(self.mesh(), hollow_mesh)

    def test_missing_and_malformed_sweep_profiles_are_loud_and_atomic(self):
        self.circle()
        for reference in ("absent-sketch", [], None, True):
            with self.subTest(reference=reference):
                response = self.reject_atomically(
                    "sweep_solid", {"sketch_uuid": reference,
                                    "path_points_nm": [[0, 0, 0], [0, 0, 12 * MM]]},
                )
                self.assertNotIn("TypeError", response["detail"],
                                 "malformed references need a domain error, not unhashable-type failure")
                self.assertEqual(response["cadcore_key"], "unknown_sketch", response)

    def test_sweep_reference_edit_updates_inputs_and_top_level_reference(self):
        self.circle(radius=10)
        first = self.state_document()["sketches"][0]["uuid"]

        def add_second_sketch(doc):
            second = copy.deepcopy(doc["sketches"][0])
            second["uuid"] = "review-second-sketch"
            second["entities"][0]["geom"]["r"] = 6 * MM
            doc["sketches"].append(second)

        self.restore_fixture(add_second_sketch)
        feature = self.create_feature("sweep_solid", {
            "sketch_uuid": first, "path_points_nm": [[0, 0, 0], [0, 0, 12 * MM]],
        }, "sweep")
        self.assertAlmostEqual(self.volume(), math.pi * 100 * 12, places=6)
        params = self.params_of(feature)
        params["sketch_uuid"] = "review-second-sketch"
        self.success("edit_feature_params", {"feature_uuid": feature, "params": params})
        self.assertAlmostEqual(self.volume(), math.pi * 36 * 12, places=6)
        actual = next(item for item in self.state_document()["features"] if item["uuid"] == feature)
        self.assertEqual(actual["inputs"], ["review-second-sketch"])
        self.assertEqual(actual["sketch_uuid"], "review-second-sketch")

    def test_malformed_sweep_reference_edit_cannot_fallback_to_old_profile(self):
        self.circle()
        feature = self.create_feature("sweep_solid", {
            "path_points_nm": [[0, 0, 0], [0, 0, 12 * MM]],
        }, "sweep")
        self.seed_both_histories_and_cache()
        params = self.params_of(feature)
        for reference in ([], None, True, "", {}, "absent-sketch"):
            with self.subTest(reference=reference):
                original_mesh = self.mesh()
                self.reject_atomically(
                    "edit_feature_params",
                    {"feature_uuid": feature, "params": dict(params, sketch_uuid=reference)},
                    reason="unknown_sketch",
                )
                self.assertEqual(self.mesh(), original_mesh)

    def test_suppressed_sweep_invalid_reference_is_not_accepted_by_edit(self):
        self.circle()
        feature = self.create_feature("sweep_solid", {
            "path_points_nm": [[0, 0, 0], [0, 0, 12 * MM]],
        }, "sweep")
        self.success("suppress_solid", {"feature_uuid": feature, "suppressed": True})
        self.assertEqual(self.mesh()["solids"], [])
        self.seed_both_histories_and_cache()
        params = self.params_of(feature)
        params["sketch_uuid"] = "absent-sketch"
        self.reject_atomically("edit_feature_params", {"feature_uuid": feature, "params": params},
                               reason="unknown_sketch")


def source_paths(root):
    oss = root / "oss-alternatives"
    paths = [
        oss / "apps/opencad/server.py", oss / "apps/opencad/solid_commands.py",
        oss / "packages/cadcore/cadcore/docmodel.py",
        oss / "packages/cadcore/cadcore/kernel/subproc.py",
        oss / "packages/cadcore/cadcore/kernel/features_registry.py",
        oss / "packages/cadcore/cadcore/ops/sw_features.py",
        Path(__file__).resolve(),
    ]
    paths.extend(oss / ("packages/cadcore/cadcore/kernel/features_%s.py" % name)
                 for name in FEATURE_MODULES)
    return paths


def hashes(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def compare_red_evidence(path, report, failures, errors):
    """Link prior red cases to current outcomes without hiding test revisions."""
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if baseline.get("verdict") != "FAIL":
        raise ValueError("red evidence must have verdict=FAIL")
    findings = json.loads((path.parent / "findings.json").read_text(encoding="utf-8"))
    prior_red = {entry["test"].split(" ", 1)[0]
                 for entry in findings["failures"] + findings["errors"]}
    current_red = {str(test).split(" ", 1)[0] for test, _ in failures + errors}
    covered = set(report["selected_cases"])
    before = baseline["source_before_sha256"]
    after = report["source_before_sha256"]
    return {
        "baseline_result": str(path),
        "baseline_result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "baseline_tests": baseline["tests"],
        "current_tests": report["tests"],
        "prior_red_now_green": sorted((prior_red & covered) - current_red),
        "prior_red_still_red": sorted(prior_red & current_red),
        "prior_red_not_run": sorted(prior_red - covered),
        "new_red_cases": sorted(current_red - prior_red),
        "source_changes": {key: {"before": before.get(key), "after": value}
                           for key, value in after.items() if before.get(key) != value},
        "test_revised": before.get(str(Path(__file__).resolve()))
                        != after[str(Path(__file__).resolve())],
    }


def main():
    global RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    parser.add_argument("--baseline-result", type=Path,
                        help="prior FAIL result whose cases must be tracked red to green")
    cases = unittest.defaultTestLoader.getTestCaseNames(SolidAtomicReviewTests)
    parser.add_argument("--case", action="append", choices=cases,
                        help="run only a named case; omitted means the complete review")
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    RUN_DIR = args.evidence_dir / stamp
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    os.environ["TMPDIR"] = str(RUN_DIR)
    os.environ["CADCORE_SUBPROC_KEEP_WORK"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["OPENCAD_CADCORE_ROOT"] = str(args.source_root / "oss-alternatives/packages/cadcore")
    tempfile.tempdir = str(RUN_DIR)
    report = {
        "verdict": "RUNNING", "started_utc": started,
        "source_root": str(args.source_root), "run_dir": str(RUN_DIR),
        "source_access": "read-only; no application/kernel monkeypatches or mock shapes",
        "network": "independent test HTTP servers at 127.0.0.1:OS-assigned-port only",
        "scope": "selected" if args.case else "full",
        "selected_cases": args.case or cases,
    }
    (RUN_DIR / "START.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    paths = source_paths(args.source_root)
    missing = [str(path) for path in paths if not path.is_file()]
    before = {}
    exit_code = 2
    try:
        if missing:
            report.update(verdict="INVALID", missing_sources=missing)
            raise FileNotFoundError("main feature integration incomplete: %s" % missing)
        before = hashes(paths)
        load_real_source(args.source_root)
        if args.case:
            suite = unittest.TestSuite(SolidAtomicReviewTests(name) for name in args.case)
        else:
            suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        with (RUN_DIR / "tests.log").open("w", encoding="utf-8") as log:
            with (RUN_DIR / "http-stderr.log").open("w", encoding="utf-8") as stderr:
                with contextlib.redirect_stderr(stderr):
                    tests = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        report.update(tests=tests.testsRun, failures=len(tests.failures),
                      errors=len(tests.errors), skipped=len(tests.skipped))
        report["verdict"] = "PASS" if tests.wasSuccessful() and tests.testsRun and not tests.skipped else "FAIL"
        exit_code = 0 if report["verdict"] == "PASS" else 1
        (RUN_DIR / "findings.json").write_text(json.dumps({
            "failures": [{"test": str(test), "traceback": text} for test, text in tests.failures],
            "errors": [{"test": str(test), "traceback": text} for test, text in tests.errors],
        }, indent=2), encoding="utf-8")
        if args.baseline_result:
            report["source_before_sha256"] = before
            comparison = compare_red_evidence(
                args.baseline_result, report, tests.failures, tests.errors,
            )
            report["red_green"] = comparison
            (RUN_DIR / "red-green.json").write_text(
                json.dumps(comparison, indent=2), encoding="utf-8"
            )
    except Exception:
        import traceback
        if report["verdict"] != "INVALID":
            report["verdict"] = "FAIL"
        (RUN_DIR / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        elapsed = time.perf_counter() - start
        report.update(elapsed_s=elapsed, exit_code=exit_code,
                      finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        after = hashes([path for path in paths if path.is_file()])
        report.update(source_before_sha256=before, source_after_sha256=after,
                      source_unchanged=bool(before) and before == after)
        if before and before != after:
            report["verdict"] = "FAIL"
            report["exit_code"] = exit_code = 1
        report["http_requests"] = len(HTTP_EVENTS)
        negatives = [event for event in HTTP_EVENTS if event["response"].get("ok") is False]
        (RUN_DIR / "negative-index.json").write_text(json.dumps(negatives, indent=2), encoding="utf-8")
        report["negative_count"] = len(negatives)
        (RUN_DIR / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (RUN_DIR / "time.json").write_text(json.dumps({
            "started_utc": started, "finished_utc": report["finished_utc"], "elapsed_s": elapsed,
        }, indent=2), encoding="utf-8")
        print("SOLID_ATOMIC_REVIEW verdict=%s tests=%s failures=%s errors=%s result=%s"
              % (report["verdict"], report.get("tests", "UNMEASURED"),
                 report.get("failures", "UNMEASURED"), report.get("errors", "UNMEASURED"),
                 RUN_DIR / "result.json"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
