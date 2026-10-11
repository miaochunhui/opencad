"""Two-user private CAD tests over real HTTP and durable SQLite stores."""

import concurrent.futures
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from access_control import AuthController
from project_store import ProjectStore, StorageFailure
from project_gateway import ProjectGateway
from secure_web import build_server


class PrivateProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT",
                                      "/data/dev/opencad-private-fixtures")).absolute()
        if runtime.resolve().is_relative_to(APP.parents[2]):
            raise ValueError("Test databases must be outside the repository")
        runtime.mkdir(parents=True, exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="opencad-project-http-", dir=runtime)
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.auth = AuthController(cls.root / "accounts", allow_registration=True)
        cls.addClassCleanup(cls.auth.close)
        # Test-only generated credentials never enter artifacts or Git.
        cls.passwords = {name: uuid.uuid4().hex for name in ("alice", "bob")}
        for name, password in cls.passwords.items():
            cls.auth.register(name, password)
        cls.store = ProjectStore(cls.root / "projects")
        cls.addClassCleanup(cls.store.close)
        cls.app = ProjectGateway(cls.auth, cls.store, cls.root / "runtime", secure_cookies=False)
        cls.server = build_server(cls.app)
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                     kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()
        cls.cookies = {}
        for name, password in cls.passwords.items():
            status, headers, data = cls.call("POST", "/api/auth/login",
                                            {"username": name, "password": password})
            if status != 200 or data.get("ok") is not True:
                raise AssertionError("real login failed")
            cls.cookies[name] = headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.app.close()
        cls.directory.cleanup()

    @classmethod
    def call(cls, method, route, body=None, *, user=None, project=None, headers=None):
        actual = {"X-OpenCAD-Request": "1", "Origin": cls.origin}
        if body is not None:
            actual["Content-Type"] = "application/json"
        if user:
            actual["Cookie"] = cls.cookies[user]
        if project:
            actual["X-OpenCAD-Project"] = project
        actual.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=30)
        try:
            connection.request(method, route, body=json.dumps(body) if body is not None else None,
                               headers=actual)
            response = connection.getresponse()
            raw = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                data = json.loads(raw)
            else:
                data = raw
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def create(self, user="alice"):
        request_id = uuid.uuid4().hex
        body = {"title": "HTTP project", "request_id": request_id}
        status, _, head = self.call("POST", "/api/projects", body, user=user)
        self.assertEqual(status, 201, head)
        return head, body

    def state(self, head, user="alice"):
        status, _, state = self.call("GET", "/api/state", user=user, project=head["project_id"])
        self.assertEqual(status, 200, state)
        return state

    @staticmethod
    def edit_body(head, command="add_line", args=None):
        return {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, "command": command,
                "args": args or {"p1": [0, 0], "p2": [10000000, 0]}}

    def edit(self, head, body, user="alice"):
        return self.call("POST", "/api/command", body, user=user, project=head["project_id"])

    def industrial(self, head, route, fields):
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, **fields}
        return self.call("POST", route, body, user="alice", project=head["project_id"]), body

    def test_industrial_routes_deny_other_users(self):
        head, _ = self.create()
        for route in ("/api/parametric", "/api/surfaces", "/api/assembly", "/api/drawing",
                      "/api/drawing/pdf", "/api/drawing/dxf", "/api/drawing/svg"):
            self.assertEqual(self.call("GET", route, user="bob",
                                      project=head["project_id"])[0], 403, route)
        for route in ("/api/parametric", "/api/surfaces", "/api/assembly",
                      "/api/drawing", "/api/render"):
            self.assertEqual(self.call("POST", route, {}, user="bob",
                                      project=head["project_id"])[0], 403, route)

    def test_actual_cli_preserves_gateway_error_class(self):
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen([
                sys.executable, "-B", str(APP / "secure_web.py"), "--data-root", str(self.root),
                "--port", "0", "--public-origin", "http://127.0.0.1"],
                stdout=subprocess.PIPE, stderr=log)
            try:
                readable, _, _ = select.select([process.stdout], [], [], 15)
                self.assertTrue(readable, "Private CLI readiness marker absent")
                line = process.stdout.readline().decode()
                match = re.fullmatch(r"OPENCAD_PRIVATE_WEB verdict=READY port=(\d+)\n", line)
                self.assertIsNotNone(match, line)
                port = int(match.group(1))
                ticket = self.auth.login("alice", self.passwords["alice"])
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                try:
                    connection.request("POST", "/api/render", body="{}", headers={
                        "Authorization": "Bearer " + ticket.token,
                        "Content-Type": "application/json"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertEqual(json.loads(response.read())["error"], "project_required")
                finally:
                    connection.close()
            finally:
                process.terminate()
                self.assertEqual(process.wait(timeout=5), 0)
                process.stdout.close()

    def test_candidate_verifier_is_required_before_publication(self):
        import industrial_surfaces
        head, _ = self.create()
        before = self.store.read_head(head["project_id"])
        doc = before["snapshot"]["doc"]
        candidate = SimpleNamespace(before_document_sha256=hashlib.sha256(doc.encode()).hexdigest(),
                                    doc=doc, response={"ok": True})
        with patch.object(industrial_surfaces, "prepare_surfaces", return_value=candidate):
            (status, _, result), body = self.industrial(head, "/api/surfaces", {"recipe": {}})
        self.assertEqual((status, result["error"]), (422, "candidate_changed"))
        self.assertEqual(self.store.read_head(head["project_id"]), before)
        self.assertIsNone(self.store.read_receipt(
            head["project_id"], body["request_id"],
            request_payload={**body, "route": "/api/surfaces"}))

    def test_from_root_rejects_network_storage_before_account_creation(self):
        rejected = Path("/hf3fs-jd/data/tmp").resolve() / (
            "opencad-refused-" + uuid.uuid4().hex)
        self.assertEqual(rejected.parent, rejected.parent.resolve())
        self.assertFalse(rejected.exists())
        with patch("access_control.AuthController") as accounts:
            with self.assertRaises(Exception) as failure:
                ProjectGateway.from_root(rejected)
        self.assertIn("durable local filesystem", str(failure.exception))
        accounts.assert_not_called()
        self.assertFalse(rejected.exists())

    def test_open_malformed_industrial_namespaces_never_commits(self):
        for namespace, malformed in (
            ("x_industrial_surfaces", {"schema": 1, "recipe": {"op": "nurbs"}}),
            ("industrial_assembly_v1", {"schema": 1, "parts": [], "components": [], "mates": []}),
            ("x_industrial_drawing", {"schema": "industrial-drawing-definition/1"}),
            ("parametric", {"version": 1, "parameters": {}}),
        ):
            with self.subTest(namespace=namespace):
                head, _ = self.create()
                before = self.store.read_head(head["project_id"])
                doc = self.app.core["docmodel"].document_from_l1(before["snapshot"]["doc"])
                doc.extra[namespace] = malformed
                text = self.app.core["docmodel"].document_to_l1(doc)
                (status, _, result), body = self.industrial(head, "/api/open", {"doc": text})
                self.assertIn(status, (400, 422), result)
                self.assertIs(result["ok"], False)
                self.assertEqual(self.store.read_head(head["project_id"]), before)
                self.assertIsNone(self.store.read_receipt(
                    head["project_id"], body["request_id"],
                    request_payload={**body, "route": "/api/open"}))

    def test_malformed_persisted_history_is_rejected_before_reads_and_edits(self):
        head, _ = self.create()
        before = self.store.read_head(head["project_id"])
        doc = self.app.core["docmodel"].document_from_l1(before["snapshot"]["doc"])
        doc.extra["x_industrial_surfaces"] = {"schema": 1, "recipe": {"op": "nurbs"}}
        history = self.app.core["docmodel"].document_to_l1(doc)
        # Model an externally corrupted but checksum-valid store snapshot.
        request_id = uuid.uuid4().hex
        request = {"fixture": "corrupt-history", "request_id": request_id,
                   "expected_revision": head["revision"], "expected_sha": head["sha256"]}
        poisoned = self.store.commit(
            head["project_id"], expected_revision=head["revision"], expected_sha=head["sha256"],
            request_id=request_id, request_payload=request,
            payload={**before["snapshot"], "undo": [history]}, response={"ok": True})
        stable = self.store.read_head(head["project_id"])
        self.assertEqual(stable["snapshot"]["undo"], [history])
        self.assertEqual(stable["revision"], head["revision"] + 1)
        status, _, result = self.call("GET", "/api/state", user="alice", project=head["project_id"])
        self.assertIn(status, (400, 422), result)
        (status, _, result), body = self.industrial(poisoned, "/api/save", {})
        self.assertIn(status, (400, 422), result)
        self.assertEqual(self.store.read_head(head["project_id"]), stable)
        self.assertIsNone(self.store.read_receipt(
            head["project_id"], body["request_id"],
            request_payload={**body, "route": "/api/save"}))

    def test_surface_durable_history_replay_and_domain_guard(self):
        head, _ = self.create()
        recipe = {"op": "nurbs", "poles_nm": [
            [[0, 0, 0], [0, 10000000, 0]], [[10000000, 0, 0], [10000000, 10000000, 0]]],
            "u_degree": 1, "v_degree": 1, "u_knots": [0, 1], "v_knots": [0, 1],
            "u_mults": [2, 2], "v_mults": [2, 2], "weights": None}
        (status, _, saved), body = self.industrial(head, "/api/surfaces", {"recipe": recipe})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["revision"], 2)
        self.assertAlmostEqual(saved["result"]["properties"]["area_mm2"], 100)
        self.assertEqual(self.state(head)["undo_depth"], 1)
        self.assertEqual(self.call("POST", "/api/surfaces", body, user="alice",
                                  project=head["project_id"])[2], saved)
        for route in ("/api/mesh", "/api/step", "/api/drawing"):
            self.assertEqual(self.call("GET", route, user="alice",
                                      project=head["project_id"])[0], 422, route)
        self.assertEqual(self.edit(saved, self.edit_body(saved))[0], 422)
        status, _, undone = self.edit(saved, self.edit_body(saved, "undo", {}))
        self.assertEqual(status, 200, undone)
        status, _, surface = self.call("GET", "/api/surfaces", user="alice",
                                       project=head["project_id"])
        self.assertEqual(status, 200, surface)
        self.assertTrue(surface["empty"])
        status, _, redone = self.edit(undone, self.edit_body(undone, "redo", {}))
        self.assertEqual(status, 200, redone)
        self.assertEqual(self.state(head)["undo_depth"], 1)

    def test_parametric_and_drawing_real_shared_cas(self):
        head, _ = self.create()
        digest = hashlib.sha256(self.state(head)["doc"].encode()).hexdigest()
        (status, _, created), _ = self.industrial(head, "/api/parametric", {
            "action": "create_plate", "expected_document_sha256": digest})
        self.assertEqual(status, 200, created)
        self.assertEqual(created["revision"], 2)
        (status, _, wide), body = self.industrial(created, "/api/parametric", {
            "action": "rebuild", "configuration": "wide",
            "expected_document_sha256": created["document_sha256"]})
        self.assertEqual(status, 200, wide)
        volume = wide["mesh"]["solids"][0]["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(volume, (60 * 20 - math.pi * 6**2) * 12, places=5)
        repeated = self.call("POST", "/api/parametric", body, user="alice",
                             project=head["project_id"])
        self.assertEqual(repeated[2], wide)
        self.assertEqual(self.state(head)["undo_depth"], 2)
        for route in ("/api/mesh", "/api/step"):
            status, headers, result = self.call("GET", route, user="alice",
                                                project=head["project_id"])
            self.assertEqual(status, 200, result)
            if route == "/api/mesh":
                self.assertEqual(result["document_sha256"], wide["document_sha256"])
            else:
                self.assertEqual(headers["X-OpenCAD-Document-SHA256"], wide["document_sha256"])
        bad = {**body, "request_id": uuid.uuid4().hex, "expected_revision": wide["revision"],
               "expected_sha": wide["sha256"], "expected_document_sha256": wide["document_sha256"],
               "overrides": {"radius": "20 * mm"}}
        before = self.state(head)
        self.assertEqual(self.call("POST", "/api/parametric", bad, user="alice",
                                  project=head["project_id"])[0], 422)
        self.assertEqual(self.state(head)["sha256"], before["sha256"])
        definition = {"title": "Version bound drawing", "views": ["top", "front", "right"],
                      "sections": [{"name": "section-x", "axis": "x", "offset_nm": 30000000,
                                    "keep": "positive"}],
                      "dimensions": [], "bom_refs": []}
        (status, _, drawing), _ = self.industrial(wide, "/api/drawing", {"definition": definition})
        self.assertEqual(status, 200, drawing)
        status, _, read = self.call("GET", "/api/drawing", user="alice",
                                   project=head["project_id"])
        self.assertEqual(status, 200, read)
        self.assertEqual(read["revision"], read["model_version"])
        self.assertEqual(read["drawing_sha256"], drawing["drawing_sha256"])
        self.assertIn('data-layer="HIDDEN"', read["svg"])
        for fmt, magic in (("pdf", b"%PDF"), ("dxf", b"0"), ("svg", b"<svg")):
            status, headers, data = self.call("GET", "/api/drawing/" + fmt, user="alice",
                                             project=head["project_id"])
            self.assertEqual(status, 200, data)
            self.assertTrue(data.lstrip().startswith(magic), fmt)
            self.assertEqual(headers["X-OpenCAD-Model-Version"], str(drawing["revision"]))
            self.assertEqual(headers["X-OpenCAD-Drawing-SHA256"], read["drawing_sha256"])
            self.assertEqual(headers["X-OpenCAD-Artifact-SHA256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.state(head)["undo_depth"], 3)

    def test_render_real_snapshot_read_only_and_stale_rejection(self):
        head, _ = self.create()
        digest = hashlib.sha256(self.state(head)["doc"].encode()).hexdigest()
        (status, _, head), _ = self.industrial(head, "/api/parametric", {
            "action": "create_plate", "expected_document_sha256": digest})
        self.assertEqual(status, 200, head)
        before = self.state(head)
        status, headers, png = self.call("POST", "/api/render", {
            "width": 64, "height": 64, "backend": "cpu",
            "expected_revision": head["revision"], "expected_sha": head["sha256"]},
            user="alice", project=head["project_id"])
        self.assertEqual(status, 200, png)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(headers["X-OpenCAD-Snapshot-SHA256"], head["sha256"])
        self.assertEqual(headers["X-OpenCAD-PNG-SHA256"], hashlib.sha256(png).hexdigest())
        self.assertEqual(self.state(head)["sha256"], before["sha256"])
        self.assertEqual(self.call("POST", "/api/render", {
            "expected_revision": 1, "expected_sha": head["sha256"]},
            user="alice", project=head["project_id"])[0], 409)

    def test_anonymous_and_forged_identity_denied(self):
        status, _, data = self.call("GET", "/api/projects", headers={"X-User": "alice"})
        self.assertEqual(status, 401, data)
        status, _, _ = self.call("GET", "/api/projects",
                                 headers={"Authorization": "Bearer forged"})
        self.assertEqual(status, 401)

    def test_project_create_idempotency(self):
        head, body = self.create()
        status, _, repeated = self.call("POST", "/api/projects", body, user="alice")
        self.assertEqual(status, 201, repeated)
        self.assertEqual(repeated, head)
        changed = {**body, "title": "changed"}
        status, _, data = self.call("POST", "/api/projects", changed, user="alice")
        self.assertEqual(status, 409, data)

    def test_two_users_project_locale_and_history_isolation(self):
        first, _ = self.create()
        second, _ = self.create("bob")
        status, _, _ = self.call("POST", "/api/locale", {"locale": "ja"}, user="bob")
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", "/api/locale", {"locale": "zh-CN"},
                                   user="alice")[0], 200)
        status, _, edited = self.edit(first, self.edit_body(first))
        self.assertEqual(status, 200, edited)
        alice, bob = self.state(first), self.state(second, "bob")
        self.assertEqual(alice["entities"], 1)
        self.assertEqual(bob["entities"], 0)
        self.assertEqual(alice["locale"], "zh-CN")
        self.assertEqual(bob["locale"], "ja")
        self.assertEqual((alice["undo_depth"], bob["undo_depth"]), (1, 0))
        for route in ("/api/state", "/api/mesh", "/api/document", "/api/step"):
            status, _, data = self.call("GET", route, user="bob", project=first["project_id"])
            self.assertEqual(status, 403, data)

    def test_same_base_competition_and_replay(self):
        head, _ = self.create()
        requests = [self.edit_body(head), self.edit_body(head)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda body: self.edit(head, body), requests))
        self.assertEqual(sorted(result[0] for result in results), [200, 409], results)
        winner = next(i for i, result in enumerate(results) if result[0] == 200)
        response = results[winner][2]
        self.assertEqual(response["revision"], head["revision"] + 1)
        status, _, replay = self.edit(head, requests[winner])
        self.assertEqual((status, replay), (200, response))
        changed = {**requests[winner], "args": {"p1": [0, 0], "p2": [20000000, 0]}}
        self.assertEqual(self.edit(head, changed)[0], 409)
        self.assertEqual(self.state(head)["entities"], 1)

    def test_failure_and_storage_error_leave_head_unchanged(self):
        head, _ = self.create()
        before = self.state(head)
        status, _, data = self.edit(head, self.edit_body(head, "unknown"))
        self.assertEqual(status, 422, data)
        self.assertEqual(self.state(head)["doc"], before["doc"])
        with patch.object(self.store, "commit", side_effect=StorageFailure("injected commit failure")):
            status, _, data = self.edit(head, self.edit_body(head))
        self.assertEqual(status, 503, data)
        after = self.state(head)
        self.assertEqual((after["doc"], after["revision"], after["undo_depth"]),
                         (before["doc"], before["revision"], before["undo_depth"]))

    def test_get_open_is_readonly_and_forbidden(self):
        head, _ = self.create()
        before = self.state(head)
        self.assertEqual(self.call("GET", "/api/open?path=anything", user="alice",
                                   project=head["project_id"])[0], 405)
        self.assertEqual(self.state(head)["doc"], before["doc"])

    def test_malformed_command_and_schema_are_client_errors(self):
        head, _ = self.create()
        for command in ([], {}, None, True, 12):
            status, _, data = self.edit(head, self.edit_body(head, command))
            self.assertEqual(status, 400, data)
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, "doc": '{"schema_version":1}'}
        status, _, data = self.call("POST", "/api/open", body, user="alice",
                                   project=head["project_id"])
        self.assertEqual(status, 422, data)
        self.assertEqual(self.state(head)["revision"], head["revision"])

    def test_file_writing_commands_do_not_acknowledge_deleted_paths(self):
        from server import Workspace
        head, _ = self.create()
        for command in [*Workspace._DOC_PATH_ARGS, "export_pdf", "prepare_release"]:
            status, _, data = self.edit(head, self.edit_body(
                head, command, {"out": "request.pdf"}))
            self.assertEqual(status, 405, data)
        self.assertEqual(self.state(head)["revision"], head["revision"])

    def test_standard_undo_and_restart_restore(self):
        head, _ = self.create()
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, "standard": "ansi"}
        status, _, edited = self.call("POST", "/api/standard", body,
                                      user="alice", project=head["project_id"])
        self.assertEqual(status, 200, edited)
        self.assertEqual(self.state(head)["standard"], "ansi")
        status, _, undone = self.edit(edited, self.edit_body(edited, "undo", {}))
        self.assertEqual(status, 200, undone)
        self.assertEqual(self.state(head)["standard"], "gbt")
        other_auth = AuthController(self.root / "accounts")
        other_store = ProjectStore(self.root / "projects")
        other = ProjectGateway(other_auth, other_store, self.root / "restart-runtime",
                               secure_cookies=False)
        from secure_web import Request
        token = self.cookies["alice"].split("=", 1)[1]
        try:
            reply = other.handle(Request("GET", "/api/state", {}, {}, token,
                                         head["project_id"], True))
            self.assertEqual(reply.status, 200, reply.payload)
            self.assertEqual(reply.payload["standard"], "gbt")
            self.assertEqual(reply.payload["redo_depth"], 1)
            self.assertEqual(reply.payload["revision"], undone["revision"])
        finally:
            other.close()

    def test_logout_revokes_old_cookie(self):
        ticket = self.auth.login("alice", self.passwords["alice"])
        from secure_web import Request
        self.auth.logout(ticket.token)
        reply = self.app.handle(Request("GET", "/api/projects", {}, {}, ticket.token, None, True))
        self.assertEqual(reply.status, 401)

    def race_identity(self):
        username, password = "race-" + uuid.uuid4().hex, uuid.uuid4().hex
        self.auth.register(username, password)
        status, headers, reply = self.call(
            "POST", "/api/auth/login", {"username": username, "password": password})
        self.assertEqual(status, 200, reply)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return username, cookie

    def revoke_actor(self, username):
        result = subprocess.run([
            sys.executable, "-B", str(APP / "tests/auth_boundary_actor.py"),
            "--database", str(self.auth.path), "--username", username],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_revocation_cannot_cross_final_project_commit(self):
        username, cookie = self.race_identity()
        status, _, head = self.call(
            "POST", "/api/projects",
            {"title": "Revocation boundary", "request_id": uuid.uuid4().hex},
            headers={"Cookie": cookie})
        self.assertEqual(status, 201, head)
        body = self.edit_body(head)
        commit, outcomes = self.store.commit, []

        def commit_with_competing_revocation(*args, **kwargs):
            outcomes.append(self.revoke_actor(username))
            return commit(*args, **kwargs)

        with patch.object(self.store, "commit", side_effect=commit_with_competing_revocation):
            status, _, saved = self.call(
                "POST", "/api/command", body, project=head["project_id"],
                headers={"Cookie": cookie})
        self.assertEqual(status, 200, saved)
        self.assertEqual(outcomes, [{"state": "BUSY", "changed": 0}])
        persisted = self.store.read_head(head["project_id"])
        self.assertEqual(persisted["revision"], head["revision"] + 1)
        receipt = self.store.read_receipt(
            head["project_id"], body["request_id"],
            request_payload={**body, "route": "/api/command"})
        self.assertEqual(receipt["revision"], saved["revision"])
        self.assertEqual(self.revoke_actor(username), {"state": "REVOKED", "changed": 1})
        status, _, denied = self.call(
            "POST", "/api/command", self.edit_body(saved), project=head["project_id"],
            headers={"Cookie": cookie})
        self.assertEqual(status, 401, denied)
        self.assertEqual(self.store.read_head(head["project_id"]), persisted)

    def test_revocation_cannot_cross_project_creation(self):
        username, cookie = self.race_identity()
        create, outcomes = self.store.create_project, []

        def create_with_competing_revocation(*args, **kwargs):
            outcomes.append(self.revoke_actor(username))
            return create(*args, **kwargs)

        with patch.object(self.store, "create_project", side_effect=create_with_competing_revocation):
            status, _, created = self.call(
                "POST", "/api/projects",
                {"title": "Creation boundary", "request_id": uuid.uuid4().hex},
                headers={"Cookie": cookie})
        self.assertEqual(status, 201, created)
        self.assertEqual(outcomes, [{"state": "BUSY", "changed": 0}])
        self.assertEqual(self.revoke_actor(username), {"state": "REVOKED", "changed": 1})
        status, _, denied = self.call(
            "GET", "/api/state", project=created["project_id"], headers={"Cookie": cookie})
        self.assertEqual(status, 401, denied)

    def test_revocation_before_final_guard_prevents_receipt(self):
        username, cookie = self.race_identity()
        status, _, head = self.call(
            "POST", "/api/projects",
            {"title": "Revoke before confirmation", "request_id": uuid.uuid4().hex},
            headers={"Cookie": cookie})
        self.assertEqual(status, 201, head)
        before = self.store.read_head(head["project_id"])
        body = self.edit_body(head)
        snapshot, outcomes = self.app._snapshot, []

        def candidate_with_prior_revocation(workspace):
            candidate = snapshot(workspace)
            outcomes.append(self.revoke_actor(username))
            return candidate

        with patch.object(self.app, "_snapshot", side_effect=candidate_with_prior_revocation):
            status, _, denied = self.call(
                "POST", "/api/command", body, project=head["project_id"],
                headers={"Cookie": cookie})
        self.assertEqual(status, 401, denied)
        self.assertEqual(outcomes, [{"state": "REVOKED", "changed": 1}])
        self.assertEqual(self.store.read_head(head["project_id"]), before)
        self.assertIsNone(self.store.read_receipt(
            head["project_id"], body["request_id"],
            request_payload={**body, "route": "/api/command"}))

    def test_real_3d_hole_plate_and_step_same_version(self):
        head, _ = self.create()
        points = [[0, 0], [40000000, 0], [40000000, 20000000], [0, 20000000]]
        for index, point in enumerate(points):
            status, _, head = self.edit(head, self.edit_body(
                head, args={"p1": point, "p2": points[(index + 1) % 4]}))
            self.assertEqual(status, 200, head)
        status, _, head = self.edit(head, self.edit_body(
            head, "add_circle", {"center": [20000000, 10000000], "radius_nm": 3000000}))
        self.assertEqual(status, 200, head)
        state = self.state(head)
        status, _, head = self.edit(head, self.edit_body(
            head, "extrude", {"sketch_uuid": state["sketch_uuid"], "distance_nm": 6000000}))
        self.assertEqual(status, 200, head)
        status, _, mesh = self.call("GET", "/api/mesh", user="alice", project=head["project_id"])
        self.assertEqual(status, 200, mesh)
        self.assertEqual(mesh["revision"], head["revision"])
        self.assertEqual(len(mesh["solids"]), 1)
        self.assertTrue(mesh["solids"][0])
        volume = mesh["solids"][0]["properties"]["volume_nm3"] / 1e18
        self.assertAlmostEqual(volume, (40 * 20 - math.pi * 3**2) * 6, places=5)
        state = self.state(head)
        digest = hashlib.sha256(state["doc"].encode()).hexdigest()
        self.assertEqual(mesh["document_sha256"], digest)
        status, headers, step = self.call("GET", "/api/step", user="alice",
                                         project=head["project_id"])
        self.assertEqual(status, 200, step)
        self.assertTrue(step.startswith(b"ISO-10303-21;"))
        self.assertEqual(headers["X-OpenCAD-Document-SHA256"], digest)
        self.assertEqual(int(headers["X-OpenCAD-Revision"]), head["revision"])


if __name__ == "__main__":
    unittest.main()
