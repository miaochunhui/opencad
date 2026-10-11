#!/usr/bin/env python3
"""Source-bound independent assembly/private Web review; never production.

Run from this owned worktree with --source-root pointing at the chair's tree.
Only isolated loopback HTTP, owned disposable local databases, browser profiles
and external evidence are written. Missing routes/mounts and failed negative
controls remain FAIL; a diagnostic browser mount never proves product wiring.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/data/dev/opencad")
DEFAULT_OUT = Path("/hf3fs-jd/data/tmp/opencad-assembly-private-review-20261011")


def write(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def document_difference(before, after, path="$"):
    if type(before) is not type(after):
        return [{"path": path, "before": before, "after": after}]
    if isinstance(before, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                changes.append({"path": path + "." + key, "before_present": key in before,
                                "after_present": key in after})
            else:
                changes.extend(document_difference(before[key], after[key], path + "." + key))
        return changes
    if isinstance(before, list):
        changes = []
        if len(before) != len(after):
            changes.append({"path": path + ".length", "before": len(before), "after": len(after)})
        for index, (left, right) in enumerate(zip(before, after)):
            changes.extend(document_difference(left, right, "%s[%d]" % (path, index)))
        return changes
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fingerprint(root):
    paths = [root / "AGENTS.md"]
    for base in ("oss-alternatives/apps/opencad", "oss-alternatives/packages/cadcore",
                 "oss-alternatives/packages/dxf", "oss-alternatives/packages/vault"):
        paths.extend(p for p in (root / base).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts)
    paths.extend(root / name for name in (
        "oss-alternatives/tools/cad3d/viewer_controls_check.py",
        "oss-alternatives/tools/cad3d/web3d_browser_check.py",
    ))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


class Review:
    def __init__(self, args, out):
        self.args, self.out, self.source = args, out, args.source_root.resolve()
        self.app_dir = self.source / "oss-alternatives/apps/opencad"
        self.checks, self.findings, self.http_calls, self.stage_errors = [], [], [], []
        self.kernel, self.application, self.httpd, self.thread = None, None, None, None
        self.cookies = {}

    def check(self, name, condition, detail=None, *, stage):
        item = {"name": name, "pass": bool(condition), "stage": stage}
        if detail is not None:
            item["detail"] = detail
        self.checks.append(item)
        print("ASSEMBLY_PRIVATE check=%s verdict=%s" % (name, "PASS" if condition else "FAIL"),
              flush=True)
        return bool(condition)

    def stage(self, name, action):
        try:
            action()
        except Exception:
            cause = traceback.format_exc()
            self.stage_errors.append({"stage": name, "cause": cause})
            self.check(name + "-completed", False, cause, stage=name)
            write(self.out / (name + "-failure.json"), {"first_cause": cause})

    def modules(self):
        package = self.source / "oss-alternatives/packages"
        sys.path[:0] = [str(self.app_dir), str(package / "cadcore"),
                       str(package / "dxf"), str(package / "vault")]
        os.environ["OPENCAD_CADCORE_ROOT"] = str(package / "cadcore")
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True
        import industrial_assembly
        import project_gateway
        import project_store
        import secure_web
        import access_control
        from cadcore import assembly, docmodel
        from cadcore.kernel.subproc import SubprocessKernel
        self.adapter, self.assembly, self.docmodel = industrial_assembly, assembly, docmodel
        self.gateway, self.store_module = project_gateway, project_store
        self.transport, self.auth_module = secure_web, access_control
        for module in (industrial_assembly, project_gateway, project_store, secure_web,
                       access_control, assembly, docmodel):
            actual = Path(module.__file__).resolve()
            if not actual.is_relative_to(self.source):
                raise RuntimeError("foreign module binding: " + str(actual))
        self.fixture = load("assembly_private_real_fixture",
                            package / "cadcore/tests/test_real_assembly.py")
        self.kernel = SubprocessKernel(work_dir=self.out / "kernel", keep_work_dir=True,
                                      op_timeout_s=40,
                                      extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
        probe = self.kernel.probe_report()
        self.check("actual-FreeCAD-probe", probe.available, probe.as_dict(), stage="components")
        if not probe.available:
            raise RuntimeError("real FreeCAD unavailable; cannot skip")

    def components(self):
        assembly = self.fixture.fixture()
        self.fixture_assembly = assembly
        doc = self.docmodel.new_document()
        before = self.docmodel.document_to_l1(doc)
        candidate = self.adapter.prepare_assembly(doc, {
            "action": "reopen", "assembly_json": assembly.to_json()}, kernel=self.kernel)
        self.good_doc = candidate.doc
        measured = candidate.response["evaluation"]
        pair = measured["collisions"][0]
        self.check("independent-two-part-hole-gap", measured["backend"] == "FreeCAD-BRep"
                   and measured["solid_count"] == 2 and pair["status"] == "separated"
                   and abs(pair["distance_nm"] - 1_000_000) <= 2
                   and pair["interference_volume_nm3"] == 0,
                   pair, stage="components")
        self.check("true-instance-mesh", all(
            len(c["mesh"]["triangles"]) > 0 and len(c["mesh"]["vertices_mm"]) > 0
            for c in measured["instances"]), stage="components")
        self.check("candidate-does-not-change-document",
                   self.docmodel.document_to_l1(doc) == before and "revision" not in candidate.response,
                   stage="components")
        moved = replace(assembly, components=(assembly.components[0],
            replace(assembly.components[1], transform=self.assembly.Transform(
                translation_nm=(2_000_000, 0, 0)))))
        collision = self.assembly.evaluate_assembly(moved, self.kernel)["collisions"][0]
        self.check("hole-wall-real-interference", collision["status"] == "penetrating"
                   and collision["interference_volume_nm3"] > 0, collision, stage="components")
        extra = replace(assembly.parts[1], configuration="alternative")
        configured = replace(assembly, parts=(*assembly.parts, extra), components=(
            *assembly.components, self.assembly.Component("pin-alt", "pin",
                configuration="alternative", transform=self.assembly.Transform(
                    translation_nm=(30_000_000, 0, 0)))))
        bom = self.assembly.bom(configured)
        self.check("BOM-configurations-separated", len(bom) == 3
                   and sum(c["quantity"] for c in bom) == 3
                   and {c["configuration"] for c in bom} == {"default", "alternative"},
                   bom, stage="components")
        faces = {item["component_id"]: item["faces"] for item in measured["instances"]}
        axes = {key: next(face["face_index"] for face in inventory if face["kind"] == "axis")
                for key, inventory in faces.items()}
        planes = {key: min((face for face in inventory if face["kind"] == "plane"),
                          key=lambda face: face["point_nm"][2])["face_index"]
                  for key, inventory in faces.items()}
        axis = self.assembly.Mate("axis", "axis", "ring-1", axes["ring-1"],
                                  "pin-1", axes["pin-1"])
        plane = self.assembly.Mate("plane", "plane", "ring-1", planes["ring-1"],
                                   "pin-1", planes["pin-1"])
        displaced = replace(assembly, mates=(axis, plane), components=(
            assembly.components[0], replace(assembly.components[1],
                transform=self.assembly.Transform(translation_nm=(2_000_000, 0, 3_000_000)))))
        solved, solver = self.assembly.solve_mates(displaced, self.kernel)
        self.check("real-mate-numeric-residual-and-DoF", solver["dof"] == 1
                   and solver["jacobian_rank"] == 5 and len(solver["residuals"]) == 2
                   and all(item["linear_residual_nm"] <= 100
                           and item["angular_residual_rad"] <= 1e-7 for item in solver["residuals"])
                   and solved.components[1].transform != displaced.components[1].transform,
                   solver, stage="components")
        for name, invalid_mates, reason in (
            ("real-mate-BRep-face-rejection", (replace(axis, b_face=4095),), "bad_mate_reference"),
            ("real-mate-overconstraint-rejection", (axis, replace(axis, mate_id="duplicate")),
             "mate_overconstrained"),
        ):
            try:
                self.assembly.solve_mates(replace(assembly, mates=invalid_mates), self.kernel)
            except Exception as exc:
                self.check(name, getattr(exc, "reason", None) == reason,
                           {"reason": getattr(exc, "reason", None), "detail": str(exc)}, stage="components")
            else:
                self.check(name, False, stage="components")
        reopened = self.adapter.prepare_assembly(
            self.docmodel.document_from_l1(candidate.doc),
            {"action": "reopen", "assembly_json": candidate.response["assembly_json"]},
            kernel=self.kernel)
        self.check("assembly-candidate-reopen-exact", reopened.doc == candidate.doc
                   and reopened.response["assembly_sha256"] == candidate.response["assembly_sha256"],
                   stage="components")
        invalid = assembly.as_dict()
        invalid["parts"][1]["document_sha256"] = "0" * 64
        self.invalid_doc = json.loads(candidate.doc)
        self.invalid_doc[self.adapter.ASSEMBLY_KEY] = invalid
        self.invalid_doc = json.dumps(self.invalid_doc, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        try:
            self.adapter.prepare_assembly(doc, {"action": "reopen",
                "assembly_json": json.dumps(invalid)}, kernel=self.kernel)
        except Exception as exc:
            self.check("adapter-bad-part-SHA-rejected",
                       getattr(exc, "reason", None) == "part_digest_mismatch", str(exc), stage="components")
        else:
            self.check("adapter-bad-part-SHA-rejected", False, stage="components")
        write(self.out / "component-measurements.json", {
            "measured": measured, "wall_collision": collision, "bom": bom, "solver": solver,
            "candidate_document_sha256": candidate.response["document_sha256"]})

    def start_http(self, local):
        auth = self.auth_module.AuthController(local / "accounts", allow_registration=True)
        passwords = {name: uuid.uuid4().hex for name in ("review-alice", "review-bob")}
        for name, password in passwords.items():
            auth.register(name, password)
        self.store = self.store_module.ProjectStore(local / "projects")
        self.application = self.gateway.ProjectGateway(auth, self.store, self.out / "private-requests",
                                                       secure_cookies=False)
        self.httpd = self.transport.build_server(self.application)
        self.origin = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        for name, password in passwords.items():
            status, headers, value = self.call("POST", "/api/auth/login",
                                              {"username": name, "password": password}, capture=False)
            if status != 200:
                raise AssertionError("isolated login failed")
            self.cookies[name] = headers["Set-Cookie"].split(";", 1)[0]
        # Never retain generated passwords in attributes, logs or artifacts.
        passwords.clear()

    def call(self, method, path, body=None, *, project=None, user="review-alice", capture=True):
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if user in self.cookies:
            headers["Cookie"] = self.cookies[user]
        if project:
            headers["X-OpenCAD-Project"] = project["project_id"]
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=60)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers=headers)
            response = connection.getresponse()
            raw = response.read()
            response_headers = dict(response.getheaders())
            value = json.loads(raw) if "application/json" in response_headers.get(
                "Content-Type", "") else raw.decode("utf-8")
            if capture:
                self.http_calls.append({"method": method, "path": path, "status": response.status,
                    "revision": value.get("revision") if isinstance(value, dict) else None,
                    "error": value.get("error") if isinstance(value, dict) else None})
            return response.status, response_headers, value
        finally:
            connection.close()

    def create(self, title):
        status, _, value = self.call("POST", "/api/projects",
                                    {"title": title, "request_id": uuid.uuid4().hex})
        if status != 201:
            raise AssertionError("private project create failed")
        return value

    def state(self, head):
        status, _, value = self.call("GET", "/api/state", project=head)
        if status != 200:
            raise AssertionError("private state read failed")
        return value

    def write_body(self, head, body):
        return {**body, "expected_revision": head["revision"],
                "expected_sha": head["sha256"], "request_id": uuid.uuid4().hex}

    def private_persistence(self):
        project = self.create("Assembly persisted review")
        self.browser_project = project
        body = self.write_body(project, {"doc": self.good_doc})
        status, _, loaded = self.call("POST", "/api/open", body, project=project)
        self.check("private-open-valid-assembly", status == 200, {"status": status}, stage="persistence")
        if status != 200:
            return
        first = self.state(project)
        self.check("private-open-stable-L1", first["doc"] == self.good_doc, stage="persistence")
        status, _, available = self.call("GET", "/api/assembly?action=state", project=project)
        self.route_available = status == 200 and available.get("ok") is True
        if self.route_available:
            component = self.fixture_assembly.components[1].as_dict()
            component["transform"]["translation_nm"] = [2_000_000, 0, 0]
            edit_route = "/api/assembly"
            edit = self.write_body(first, {"action": "put_component", "component": component})
            status, _, error = self.call("POST", "/api/command", self.write_body(first, {
                "command": "add_line", "args": {"p1": [0, 0], "p2": [1_000_000, 0]}}),
                project=project)
            self.check("assembly-rejects-incompatible-sketch-edit", status == 422
                       and error.get("error") == "unsupported_geometry_domain"
                       and self.state(project)["revision"] == first["revision"]
                       and self.state(project)["doc"] == first["doc"], stage="persistence")
        else:
            edit_route = "/api/command"
            edit = self.write_body(first, {"command": "add_line",
                                          "args": {"p1": [0, 0], "p2": [1_000_000, 0]}})
        status, _, edited = self.call("POST", edit_route, edit, project=project)
        self.check("private-edit-keeps-assembly", status == 200, stage="persistence")
        second = self.state(project)
        stored = self.store.read_head(project["project_id"])["snapshot"]
        self.check("private-history-starts-at-confirmed-document",
                   stored["undo"] == [first["doc"]],
                   {"confirmed_document_sha256": sha_bytes(first["doc"].encode()),
                    "undo_document_sha256": [sha_bytes(text.encode()) for text in stored["undo"]]},
                   stage="persistence")
        self.check("private-undo-preserves-assembly-bytes",
                   second["undo_depth"] == 1
                   and self.adapter.ASSEMBLY_KEY in json.loads(second["doc"])
                   and json.loads(stored["undo"][0])[self.adapter.ASSEMBLY_KEY] ==
                   json.loads(self.good_doc)[self.adapter.ASSEMBLY_KEY], stage="persistence")
        status, _, repeated = self.call("POST", edit_route, edit, project=project)
        self.check("private-receipt-replay-exactly-once", status == 200
                   and repeated == edited and self.state(project)["revision"] == second["revision"],
                   stage="persistence")
        stale = dict(edit, request_id=uuid.uuid4().hex)
        status, _, error = self.call("POST", edit_route, stale, project=project)
        self.check("private-stale-CAS-rejected", status == 409
                   and error.get("error") == "revision_conflict"
                   and self.state(project)["doc"] == second["doc"], stage="persistence")
        status, _, _ = self.call("POST", "/api/command",
            self.write_body(second, {"command": "undo", "args": {}}), project=project)
        undone = self.state(project)
        difference = document_difference(json.loads(first["doc"]), json.loads(undone["doc"]))
        self.check("private-undo-exact-assembly-doc", status == 200
                   and undone["doc"] == self.good_doc and undone["redo_depth"] == 1,
                   {"status": status, "redo_depth": undone["redo_depth"],
                    "document_difference": difference}, stage="persistence")
        if undone["doc"] != first["doc"]:
            self.findings.append({"priority": "P1", "name": "undo-before-confirmed-version",
                "source": "oss-alternatives/apps/opencad/server.py",
                "detail": "A no-sketch assembly document acquires a sketch before the generic "
                          "command undo snapshot; undo does not restore the acknowledged open bytes.",
                "document_difference": difference})
        status, _, _ = self.call("POST", "/api/command",
            self.write_body(undone, {"command": "redo", "args": {}}), project=project)
        redone = self.state(project)
        redo_difference = document_difference(json.loads(second["doc"]), json.loads(redone["doc"]))
        self.check("private-redo-exact-assembly-doc", status == 200
                   and redone["doc"] == second["doc"],
                   {"status": status, "document_difference": redo_difference}, stage="persistence")
        if status == 200 and redone["doc"] != second["doc"]:
            self.findings.append({"priority": "P1", "name": "redo-not-confirmed-document",
                "source": "oss-alternatives/apps/opencad/server.py",
                "detail": "Redo differs from the exact acknowledged assembly edit document.",
                "document_difference": redo_difference})
        write(self.out / "undo-boundary.json", {
            "confirmed": first, "edited": second, "undo": undone, "redo": redone,
            "persisted_edit_snapshot": stored, "document_difference": difference,
            "redo_document_difference": redo_difference})
        target_standard = next(item for item in redone["standards"]
                               if item != redone["standard"])
        status, _, _ = self.call("POST", "/api/standard",
                                self.write_body(redone, {"standard": target_standard}), project=project)
        standard = self.state(project)
        self.check("project-standard-not-part-configuration",
                   status == 200 and standard["standard"] == target_standard
                   and standard["doc"] != redone["doc"]
                   and json.loads(standard["doc"])[self.adapter.ASSEMBLY_KEY] ==
                   json.loads(second["doc"])[self.adapter.ASSEMBLY_KEY],
                   {"before": redone["standard"], "after": standard["standard"]},
                   stage="persistence")
        status, _, _ = self.call("POST", "/api/command",
            self.write_body(standard, {"command": "undo", "args": {}}), project=project)
        self.check("standard-undo-restores-document-and-snapshot", status == 200
                   and self.state(project)["doc"] == redone["doc"], stage="persistence")
        saved = self.store.read_head(project["project_id"])
        reopened_store = self.store_module.ProjectStore(self.store.root)
        try:
            reopened_head = reopened_store.read_head(project["project_id"])
            self.check("private-store-reopen-last-confirmation", reopened_head == saved,
                       {"revision": saved["revision"], "snapshot_sha256": saved["sha256"]},
                       stage="persistence")
        finally:
            reopened_store.close()
        status, _, error = self.call("GET", "/api/state", project=project, user="review-bob")
        self.check("cross-user-project-denied", status == 403, error.get("error"), stage="persistence")
        # An imported assembly with no top-level solid features must still be
        # validated. A 200 acknowledgement of corrupted part bytes is a bug.
        bad_project = self.create("Negative SHA import")
        before = self.state(bad_project)
        status, _, value = self.call("POST", "/api/open",
            self.write_body(before, {"doc": self.invalid_doc}), project=bad_project)
        after = self.state(bad_project)
        rejected = status == 422 and after["revision"] == before["revision"] and after["doc"] == before["doc"]
        self.check("private-open-invalid-part-SHA-rejected", rejected,
                   {"status": status, "revision_before": before["revision"],
                    "revision_after": after["revision"], "error": value.get("error")}, stage="persistence")
        if not rejected:
            self.findings.append({"priority": "P1", "name": "assembly-extension-open-validation",
                "source": "oss-alternatives/apps/opencad/project_gateway.py",
                "detail": "POST /api/open acknowledged invalid immutable assembly part SHA; "
                          "assembly-only documents bypass the candidate.features validation branch."})
        status, _, page = self.call("GET", "/web/3d.html")
        self.page = page
        self.check("private-page-loads-assembly-panel-assets", status == 200
                   and "/web/js/assembly-panel.js" in page and "/web/css/assembly-panel.css" in page,
                   stage="wiring")
        status, _, value = self.call("GET", "/api/assembly?action=state", project=project)
        self.route_available = status == 200 and value.get("ok") is True
        self.check("private-assembly-GET-route", self.route_available,
                   {"status": status, "error": value.get("error")}, stage="wiring")
        status, _, value = self.call("POST", "/api/assembly",
            self.write_body(self.state(project), {"action": "checkpoint"}), project=project)
        self.check("private-assembly-POST-route", status == 200,
                   {"status": status, "error": value.get("error")}, stage="wiring")
        if not self.route_available:
            self.findings.append({"priority": "P1", "name": "private-assembly-not-wired",
                "source": "oss-alternatives/apps/opencad/project_gateway.py",
                "detail": "Actual private GET/POST /api/assembly return not_found; "
                          "the adapter commit alone does not make the private panel usable."})

    def assembly_routes(self):
        if not getattr(self, "route_available", False):
            self.check("assembly-route-positive-negative-controls", False,
                       "UNTESTED_DEPENDENCY: actual /api/assembly unavailable; no test route injected",
                       stage="assembly-http")
            return
        project = self.create("Assembly route controls")
        body = self.write_body(project, {"action": "reopen",
                                        "assembly_json": self.fixture_assembly.to_json()})
        status, _, first = self.call("POST", "/api/assembly", body, project=project)
        self.check("assembly-route-reopen-valid", status == 200
                   and first.get("evaluation", {}).get("solid_count") == 2, stage="assembly-http")
        if status != 200:
            return
        status, _, same = self.call("POST", "/api/assembly", body, project=project)
        self.check("assembly-route-replay", status == 200 and same == first, stage="assembly-http")
        status, _, reused = self.call("POST", "/api/assembly", dict(body, action="checkpoint"),
                                     project=project)
        self.check("assembly-request-id-different-body-rejected", status == 409
                   and reused.get("error") == "idempotency_conflict", stage="assembly-http")
        status, _, bad = self.call("POST", "/api/assembly",
                                  dict(body, request_id=uuid.uuid4().hex), project=project)
        self.check("assembly-route-stale", status == 409
                   and bad.get("error") == "revision_conflict", stage="assembly-http")
        status, _, read = self.call("GET", "/api/assembly?action=state", project=project)
        stable = self.state(project)
        snapshot_before_read = self.store.read_head(project["project_id"])
        status, _, bom_read = self.call("GET", "/api/assembly?action=bom", project=project)
        self.check("assembly-reads-do-not-commit-history", status == 200
                   and len(bom_read["bom"]) == 2
                   and self.store.read_head(project["project_id"]) == snapshot_before_read,
                   stage="assembly-http")
        self.check("assembly-route-mesh-BOM-gap", status == 200
                   and len(read["bom"]) == 2 and read["evaluation"]["collisions"][0]["status"] == "separated"
                   and all(c["mesh"]["triangles"] for c in read["evaluation"]["instances"]),
                   stage="assembly-http")
        status, _, bad_reopen = self.call("POST", "/api/assembly", self.write_body(stable, {
            "action": "reopen",
            "assembly_json": json.dumps(json.loads(self.invalid_doc)[self.adapter.ASSEMBLY_KEY])}),
            project=project)
        self.check("assembly-route-corrupt-reopen-no-CAS", status == 422
                   and bad_reopen.get("error") == "part_digest_mismatch"
                   and self.store.read_head(project["project_id"]) == snapshot_before_read,
                   stage="assembly-http")
        status, _, value = self.call("POST", "/api/assembly", self.write_body(stable, {
            "action": "state"}), project=project)
        self.check("assembly-read-action-not-accepted-as-write", status == 422
                   and value.get("error") == "bad_assembly_action"
                   and self.store.read_head(project["project_id"]) == snapshot_before_read,
                   stage="assembly-http")
        wrong_sha = self.write_body(stable, {"action": "checkpoint"})
        wrong_sha["expected_sha"] = "0" * 64
        status, _, value = self.call("POST", "/api/assembly", wrong_sha, project=project)
        self.check("assembly-same-revision-wrong-snapshot-SHA-rejected", status == 409
                   and value.get("error") == "revision_conflict"
                   and self.store.read_head(project["project_id"]) == snapshot_before_read,
                   stage="assembly-http")
        for query in ("action=state&action=bom", "action=state&unexpected=1"):
            status, _, value = self.call("GET", "/api/assembly?" + query, project=project)
            self.check("assembly-query-rejected-" + query, status == 400
                       and value.get("error") == "bad_arguments", stage="assembly-http")
        status, _, value = self.call("GET", "/api/assembly?action=state",
                                    project=project, user="review-bob")
        self.check("assembly-cross-user-read-denied", status == 403, stage="assembly-http")
        status, _, value = self.call("POST", "/api/assembly", self.write_body(stable, {
            "action": "checkpoint"}), project=project, user="review-bob")
        self.check("assembly-cross-user-write-denied", status == 403
                   and self.state(project)["doc"] == stable["doc"], stage="assembly-http")
        component = self.fixture_assembly.components[1].as_dict()
        component["transform"]["translation_nm"] = [2_000_000, 0, 0]
        status, _, moved = self.call("POST", "/api/assembly",
            self.write_body(stable, {"action": "put_component", "component": component}), project=project)
        self.check("assembly-route-BRep-wall-collision", status == 200
                   and moved["evaluation"]["collisions"][0]["interference_volume_nm3"] > 0,
                   stage="assembly-http")
        before = self.state(project)
        bad_mate = {"mate_id": "bad", "kind": "axis", "a": "ring-1", "b": "pin-1",
                    "a_face": 4095, "b_face": 4095}
        status, _, value = self.call("POST", "/api/assembly", self.write_body(
            before, {"action": "put_mate", "mate": bad_mate}), project=project)
        self.check("assembly-route-bad-mate-no-CAS", status == 422
                   and self.state(project)["doc"] == before["doc"]
                   and self.state(project)["revision"] == before["revision"],
                   {"status": status, "error": value.get("error")}, stage="assembly-http")
        status, _, _ = self.call("POST", "/api/command", self.write_body(
            before, {"command": "undo", "args": {}}), project=project)
        undone = self.state(project)
        self.check("assembly-route-undo", status == 200
                   and undone["doc"] == stable["doc"], stage="assembly-http")
        status, _, exported = self.call("GET", "/api/assembly?action=export", project=project)
        status, _, restored = self.call("POST", "/api/assembly", self.write_body(
            undone, {"action": "reopen", "assembly_json": exported["assembly_json"]}), project=project)
        self.check("assembly-route-export-reopen", status == 200
                   and restored["doc"] == undone["doc"], stage="assembly-http")

    def browser(self):
        browser_module = load("assembly_private_browser",
            self.source / "oss-alternatives/tools/cad3d/viewer_controls_check.py")

        class SafeBrowser(browser_module.BrowserSession):
            def call(self, method, params):
                value = self.helper._cdp_call(self.ws, self.sequence, method, params, 10)
                # Never persist Input.insertText or credentials in CDP events.
                self.events.append({"method": method, "ok": "error" not in value})
                if "error" in value or "exceptionDetails" in value.get("result", {}):
                    raise RuntimeError("private CDP operation failed: " + method)
                return value["result"]

        with SafeBrowser(self.args.browser, self.out / "browser",
                         source_root=self.source) as browser:
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
            # HTTP-only cookie is installed for the generated isolated session;
            # no production cookie/password is read or stored by the browser.
            token = self.cookies["review-alice"].partition("=")[2]
            browser.call("Network.setCookie", {"name": self.transport.COOKIE_NAME, "value": token,
                         "url": self.origin, "httpOnly": True, "sameSite": "Strict"})
            browser.call("Page.reload", {})
            browser.wait("!!document.querySelector('dialog[open] select')")
            browser.evaluate("document.querySelector('dialog select').value=" +
                             json.dumps(self.browser_project["project_id"]))
            browser.click("dialog button")
            browser.wait("!document.querySelector('dialog')")
            browser.wait("window.opencad3d && !window.opencad3d.busy")
            lazy_host = browser.evaluate("!!document.querySelector('[data-assembly-host]')")
            if lazy_host:
                browser.click("details:has([data-assembly-host]) > summary")
                browser.wait("!!window.opencadAssemblyPanel && !window.opencadAssemblyPanel.busy")
            mounted = browser.evaluate("!!document.querySelector('.assembly-panel canvas')")
            self.check("private-page-auto-mounts-assembly", mounted, stage="browser")
            if mounted:
                self.check("private-mounted-real-two-part-mesh", browser.evaluate(
                    "!!opencadAssemblyPanel.state && opencadAssemblyPanel.triangles>0"
                    " && opencadAssemblyPanel.state.evaluation.solid_count===2"
                    " && opencadAssemblyPanel.state.bom.length===2"), stage="browser")
                panel_before = browser.evaluate("opencadAssemblyPanel.state.revision")
                browser.evaluate("""opencadAssemblyPanel.select('pin-1');
                  document.querySelector('[data-form=pose] input[name=translation_mm]').value='[0,0,0]';
                  document.querySelector('[data-form=pose]').requestSubmit();""")
                browser.wait("!opencadAssemblyPanel.busy && (opencadAssemblyPanel.error || "
                             "opencadAssemblyPanel.state.revision>" + str(panel_before) + ")")
                panel = browser.evaluate("""({state:opencadAssemblyPanel.state,
                  error:opencadAssemblyPanel.error,triangles:opencadAssemblyPanel.triangles})""")
                persisted = self.state(self.browser_project)
                self.check("private-browser-pose-form-real-commit", not panel["error"]
                           and panel["state"]["revision"] == panel_before + 1
                           and persisted["doc"] == panel["state"]["doc"]
                           and persisted["revision"] == panel["state"]["revision"]
                           and panel["triangles"] > 0
                           and panel["state"]["evaluation"]["collisions"][0]["status"] == "separated",
                           {"revision": panel["state"]["revision"], "error": panel["error"]},
                           stage="browser")
                write(self.out / "browser" / "panel-confirmed.json", panel)
            if not mounted:
                # Diagnostic-only mount uses real product assets and private
                # fetch transport, but is explicitly NOT an integration PASS.
                browser.evaluate("""(async function(){
                  if(!window.opencadAssembly){
                    const css=document.createElement('link');css.rel='stylesheet';
                    css.href='/web/css/assembly-panel.css';document.head.append(css);
                    await new Promise((resolve,reject)=>{const script=document.createElement('script');
                      script.src='/web/js/assembly-panel.js';script.onload=resolve;script.onerror=reject;
                      document.head.append(script);});
                  }
                  const root=document.createElement('section');root.id='review-assembly';
                  document.body.append(root);
                  window.reviewAssembly=opencadAssembly.mount(root,{transport:async r=>{
                    const response=await fetch(r.path,r.method==='POST'?{method:'POST',
                      headers:{'Content-Type':'application/json'},body:JSON.stringify(r.body)}:{});
                    const value=await response.json();
                    if(!response.ok||value.ok!==true)throw new Error(value.error||value.detail);
                    return value;
                  }});
                  try{await reviewAssembly.ready;return true;}catch(e){return false;}
                })()""")
                if not self.route_available:
                    self.check("diagnostic-panel-fail-loud-on-missing-route", browser.evaluate(
                        "!!reviewAssembly.error && !reviewAssembly.state && !reviewAssembly.busy"
                        " && reviewAssembly.triangles===0"), stage="browser")
                else:
                    self.check("diagnostic-panel-private-transport", browser.evaluate(
                        "!!reviewAssembly.state && !reviewAssembly.busy"
                        " && reviewAssembly.triangles>0"), stage="browser")
            before = self.state(self.browser_project)
            if self.route_available:
                component = self.fixture_assembly.components[1].as_dict()
                component["transform"]["translation_nm"] = [3_000_000, 0, 0]
                external_route = "/api/assembly"
                external_body = {"action": "put_component", "component": component}
            else:
                external_route = "/api/command"
                external_body = {"command": "add_line", "args": {
                    "p1": [0, 0], "p2": [2_000_000, 0]}}
            status, _, value = self.call("POST", external_route, self.write_body(before, external_body),
                                        project=self.browser_project)
            # Private session's actual fetch must send its stale CAS, receive
            # 409 and block another write until deliberate conflict resolution.
            if mounted:
                browser.evaluate("""opencadAssemblyPanel.select('pin-1');
                  document.querySelector('[data-form=pose] input[name=translation_mm]').value='[4,0,0]';
                  document.querySelector('[data-form=pose]').requestSubmit();""")
                browser.wait("!opencadAssemblyPanel.busy && !!opencadAssemblyPanel.error")
                probe = browser.evaluate("""(async function(){
                  const initial={error:opencadAssemblyPanel.error,
                    revision:opencadAssemblyPanel.state.revision,
                    document_sha256:opencadAssemblyPanel.state.document_sha256};
                  let blocked=false;try{await opencadAssemblyPanel.command({action:'checkpoint'});}
                  catch(e){blocked=/刷新|新版本/.test(e.message);}
                  return {blocked,initial,error:opencadAssemblyPanel.error,
                    notice:document.querySelector('.project-status output').textContent};
                })()""")
                after = self.state(self.browser_project)
                self.check("private-browser-form-stale-and-block-next", status == 200
                           and probe["blocked"]
                           and "冲突" in probe["notice"] and after["doc"] == value["doc"]
                           and after["revision"] == value["revision"]
                           and probe["initial"]["revision"] == before["revision"],
                           probe, stage="browser")
            else:
                probe = browser.evaluate("""(async function(){
              const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({command:'add_line',args:{p1:[0,0],p2:[3000000,0]}})});
              const v=await r.json();
              let blocked=false;try{await fetch('/api/command',{method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({command:'add_line',args:{p1:[0,0],p2:[4000000,0]}})});}
              catch(e){blocked=true;}
              return {status:r.status,error:v.error,blocked,
                notice:document.querySelector('.project-status output').textContent};
                })()""")
                self.check("private-browser-stale-and-block-next", status == 200
                           and probe["status"] == 409 and probe["error"] == "revision_conflict"
                           and probe["blocked"], probe, stage="browser")
            for width, height, name in ((1440, 900, "desktop"), (420, 900, "narrow")):
                browser.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 650})
                browser.evaluate("document.querySelector('.assembly-panel')?.scrollIntoView({block:'start'})")
                browser.screenshot(name)
                self.check(name + "-page-no-overflow", browser.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"), stage="browser")
            write(self.out / "browser" / "scope.json", {
                "automatic_product_mount": mounted, "diagnostic_mount": not mounted,
                "production_requests": 0, "gpu_performance": "NOT_ASSESSED"})

    def close(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("isolated HTTP server did not terminate")
            self.httpd, self.thread = None, None
        if self.application:
            self.application.close()
            self.application = None
        if self.kernel:
            self.kernel.close()
            self.kernel = None


def result_validity(report, source_manifest, artifact_root):
    errors = []
    checks = report["checks"]
    if not checks or len({c["name"] for c in checks}) != len(checks):
        errors.append("empty-or-duplicate-checks")
    if any(type(c["pass"]) is not bool for c in checks):
        errors.append("non-boolean-check")
    if (not report["source_before"] or report["source_after"] != source_manifest
            or report["source_before"] != report["source_after"]
            or report["source_drift"] or report["source_unchanged"] is not True):
        errors.append("source-binding")
    if report["production_requests"] != 0:
        errors.append("production-scope")
    if report["tool_sha256"] != sha_bytes(Path(__file__).read_bytes()):
        errors.append("tool-binding")
    if not report["artifacts"]:
        errors.append("empty-artifacts")
    for name, value in report["artifacts"].items():
        target = (artifact_root / name).resolve()
        if not target.is_relative_to(artifact_root.resolve()):
            errors.append("artifact-outside-evidence")
            continue
        if (not target.is_file() or target.stat().st_size != value["bytes"]
                or sha_bytes(target.read_bytes()) != value["sha256"]):
            errors.append("artifact-binding:" + name)
    expected = ("PASS" if checks and all(c["pass"] is True for c in checks)
                and not report["stage_errors"] and not report["source_drift"] else "FAIL")
    if report["verdict"] != expected:
        errors.append("verdict-inconsistent")
    return errors


def verify(path, source):
    try:
        report = json.loads(path.read_text())
        errors = result_validity(report, fingerprint(source), path.parent)
        if Path(report["source_root"]).resolve() != source.resolve():
            errors.append("source-root")
        findings = [c["name"] for c in report["checks"] if not c["pass"]]
        print("ASSEMBLY_PRIVATE_VERIFY evidence_valid=%s verdict=%s failed=%s errors=%s result=%s" % (
            not errors, report["verdict"], json.dumps(findings), json.dumps(errors), path))
        return 0 if not errors and report["verdict"] == "PASS" else 1
    except Exception as exc:
        print("ASSEMBLY_PRIVATE_VERIFY verdict=FAIL cause=%s result=%s" % (str(exc), path))
        return 1


def inspect_result(path):
    report = json.loads(path.read_text())
    boundary = json.loads((path.parent / "undo-boundary.json").read_text())
    summary = {"verdict": report["verdict"], "failed": [
        c["name"] for c in report["checks"] if not c["pass"]],
        "source_drift": report["source_drift"],
        "undo_difference": document_difference(json.loads(boundary["confirmed"]["doc"]),
                                              json.loads(boundary["undo"]["doc"])),
        "redo_difference": document_difference(json.loads(boundary["edited"]["doc"]),
                                              json.loads(boundary["redo"]["doc"]))}
    print(json.dumps(summary, sort_keys=True))
    return 0


def selftest(out):
    artifact = out / "fixture.txt"
    with artifact.open("xb") as stream:
        stream.write(b"synthetic validator fixture; no CAD or production\n")
    manifest = {"fixture.py": sha_bytes(b"source fixture")}
    good = {"checks": [{"name": "fixture", "pass": True}], "source_before": manifest,
        "source_after": manifest, "source_drift": [], "source_unchanged": True,
        "production_requests": 0, "stage_errors": [], "verdict": "PASS",
        "tool_sha256": sha_bytes(Path(__file__).read_bytes()),
        "artifacts": {"fixture.txt": {"bytes": artifact.stat().st_size,
                                    "sha256": sha_bytes(artifact.read_bytes())}}}
    cases = []

    def case(name, report, expected):
        errors = result_validity(report, manifest, out)
        cases.append({"name": name, "pass": errors == expected, "observed": errors})

    case("valid-evidence", good, [])
    failed = deepcopy(good)
    failed["checks"][0]["pass"], failed["verdict"] = False, "FAIL"
    case("honest-FAIL-remains-valid-FAIL", failed, [])
    failed["verdict"] = "PASS"
    case("false-PASS-rejected", failed, ["verdict-inconsistent"])
    drift = deepcopy(good)
    drift["source_after"] = {"fixture.py": sha_bytes(b"changed")}
    case("source-drift-rejected", drift, ["source-binding"])
    tool = deepcopy(good)
    tool["tool_sha256"] = sha_bytes(b"other tool")
    case("tool-drift-rejected", tool, ["tool-binding"])
    artifact_drift = deepcopy(good)
    artifact_drift["artifacts"]["fixture.txt"]["sha256"] = sha_bytes(b"changed artifact")
    case("artifact-drift-rejected", artifact_drift, ["artifact-binding:fixture.txt"])
    empty = deepcopy(good)
    empty["checks"] = []
    case("empty-checks-rejected", empty, ["empty-or-duplicate-checks", "verdict-inconsistent"])
    missing = deepcopy(good)
    missing["artifacts"] = {}
    case("empty-artifacts-rejected", missing, ["empty-artifacts"])
    stage = deepcopy(good)
    stage["stage_errors"] = [{"cause": "fixture error"}]
    case("stage-error-rejected", stage, ["verdict-inconsistent"])
    scoped = deepcopy(good)
    scoped["production_requests"] = 1
    case("production-scope-rejected", scoped, ["production-scope"])
    passed = all(c["pass"] for c in cases)
    write(out / "result.json", {"verdict": "PASS" if passed else "FAIL", "checks": cases,
        "scope": "validator selftest only; not product acceptance",
        "tool_sha256": good["tool_sha256"], "finished_at": datetime.now(timezone.utc).isoformat()})
    line = "ASSEMBLY_PRIVATE_SELFTEST verdict=%s tests=%d failed=%d result=%s" % (
        "PASS" if passed else "FAIL", len(cases), sum(not c["pass"] for c in cases), out / "result.json")
    with (out / "selftest.log").open("x") as stream:
        stream.write(line + "\n")
    print(line)
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=Path("/data/dev/opencad-assembly-private-fixtures"))
    parser.add_argument("--browser", default=
                        "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
    parser.add_argument("--verify-result", type=Path)
    parser.add_argument("--inspect-result", type=Path, help="read-only undo/redo document differences")
    parser.add_argument("--selftest", action="store_true", help="validator negative controls, no server")
    args = parser.parse_args()
    args.source_root = args.source_root.resolve()
    if args.inspect_result:
        return inspect_result(args.inspect_result.resolve())
    if args.verify_result:
        return verify(args.verify_result.resolve(), args.source_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or DEFAULT_OUT / stamp).resolve()
    runtime = args.runtime_root.resolve()
    for path in (out, runtime):
        if path.is_relative_to(ROOT) or path.is_relative_to(args.source_root):
            raise ValueError("runtime/evidence must be outside both source trees")
    if runtime.is_relative_to(Path("/hf3fs-jd")) or str(runtime).startswith("/3fs/"):
        raise ValueError("SQLite runtime must be a local filesystem")
    out.mkdir(parents=True, exist_ok=False)
    if args.selftest:
        return selftest(out)
    runtime.mkdir(parents=True, exist_ok=True)
    before = fingerprint(args.source_root)
    tool_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    started = time.monotonic()
    review = Review(args, out)
    with (out / "review.log").open("x") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            review.modules()
            review.stage("components", review.components)
            with tempfile.TemporaryDirectory(prefix="review-", dir=runtime) as directory:
                review.start_http(Path(directory))
                review.stage("persistence", review.private_persistence)
                review.stage("assembly-http", review.assembly_routes)
                review.stage("browser", review.browser)
                review.close()
        except Exception:
            review.stage_errors.append({"stage": "initialization", "cause": traceback.format_exc()})
        finally:
            review.stage("cleanup", review.close)
    after = fingerprint(args.source_root)
    drift = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if drift:
        review.check("source-unchanged", False, drift, stage="binding")
    artifacts = {str(path.relative_to(out)): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                            "bytes": path.stat().st_size}
                 for path in out.rglob("*") if path.is_file()}
    complete = (bool(review.checks) and all(c["pass"] for c in review.checks)
                and not review.stage_errors and not drift)
    report = {"verdict": "PASS" if complete else "FAIL", "checks": review.checks,
        "findings": review.findings, "stage_errors": review.stage_errors,
        "source_root": str(args.source_root), "source_before": before, "source_after": after,
        "source_drift": drift, "source_unchanged": not drift, "tool_sha256": tool_sha,
        "artifacts": artifacts, "http_calls": review.http_calls, "production_requests": 0,
        "gpu_performance": "NOT_ASSESSED", "wall_s": time.monotonic() - started,
        "finished_at": datetime.now(timezone.utc).isoformat()}
    write(out / "result.json", report)
    line = "ASSEMBLY_PRIVATE_REVIEW verdict=%s checks=%d failed=%d source_drift=%d result=%s" % (
        report["verdict"], len(review.checks), sum(not c["pass"] for c in review.checks), len(drift),
        out / "result.json")
    (out / "verdict.log").write_text(line + "\n")
    print(line)
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
