#!/usr/bin/env python3
"""Real definition HTTP/CLI gate, with owned local SQLite scheduling actors.

Run --evidence-dir <fresh 3FS> --runtime-root <fresh /data/tmp child>.
--controls-only and --verify-evidence reuse the source-bound core receipt ruler.
No existing suite, browser, production service or acceptance.py is invoked.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "oss-alternatives/apps/opencad"
CORE = ROOT / "oss-alternatives/packages/cadcore"
sys.path[:0] = [str(APP), str(CORE)]


def revoke_actor(argv):
    """Touch only generated sessions under an explicitly supplied local fixture."""
    parser = argparse.ArgumentParser(description="Generated SQLite revocation actor")
    parser.add_argument("--revoke-actor", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args(argv)
    root = args.fixture_root.absolute()
    database = args.revoke_actor.absolute()
    if (not root.is_relative_to(Path("/data/tmp")) or
            any(path.is_symlink() for path in (database, *database.parents)) or
            not database.is_relative_to(root) or not database.is_file() or
            database.name != "opencad-auth.sqlite3" or
            not args.username.startswith("definition_")):
        raise ValueError("Refusing non-generated/local auth fixture")
    db = sqlite3.connect(database, timeout=0, isolation_level=None)
    try:
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code == 5 or (code is None and str(exc) == "database is locked"):
                print(json.dumps({"state": "BUSY", "changed": 0}, sort_keys=True))
                return 0
            raise
        row = db.execute("SELECT user_id FROM users WHERE username=?", (args.username,)).fetchone()
        if row is None:
            raise ValueError("Generated account missing")
        changed = db.execute("UPDATE sessions SET revoked=1 WHERE user_id=? AND revoked=0",
                             (row[0],)).rowcount
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    print(json.dumps({"state": "REVOKED", "changed": changed}, sort_keys=True))
    return 0


if __name__ == "__main__" and "--revoke-actor" in sys.argv:
    raise SystemExit(revoke_actor(sys.argv[1:]))

spec = importlib.util.spec_from_file_location(
    "definition_gate", CORE / "tests/test_parametric_definitions.py")
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)
from access_control import AuthController
from cadcore import docmodel
from cadcore.kernel.subproc import SubprocessKernel
import industrial_parametric as industrial
from project_gateway import ProjectGateway
from project_store import ProjectStore
from secure_web import build_server

BASE_VOLUME = (800 - math.pi * 16) * 8

# Declarative exchange contract. The gate parses this literal without importing
# the producer; actual requests must claim and fulfill each source-defined slot.
HTTP_FLOW = {
    "DefinitionCLITests.test_real_cli_crud_lossresponse_save_undo_redo_restart": [
        ("setup",), ("witness", "http-base"),
        ("parametric", "create_configuration", 0, 200, True),
        ("parametric", "create_configuration", 0, 200),
        ("login",), ("parametric", "create_configuration", 0, 200),
        ("witness", "http-create-restart"),
        ("parametric", "replace_configuration", 0, 200), ("witness", "http-replace"),
        ("parametric", "set_suppressions", 0, 200), ("witness", "http-unsuppress"),
        ("state",), ("command", "undo"), ("witness", "http-undo"),
        ("state",), ("command", "redo"), ("witness", "http-redo"),
        ("state",), ("save",), ("state",), ("login",), ("witness", "http-save-restart"),
        ("parametric", "delete_configuration", 0, 200), ("witness", "http-delete"),
        ("parametric", "delete_configuration", 1, 403), ("logout",),
        ("parametric", "delete_configuration", 0, 401),
    ],
    "DefinitionHTTPTests.test_action_schema_boolean_parameter_and_active_delete_rejections": [
        ("setup",), ("schema-cases",), ("parametric", "rebuild", 0, 200),
        ("state",), ("parametric", "delete_configuration", 0, 422),
    ],
    "DefinitionHTTPTests.test_candidate_measured_once_and_final_cas": [
        ("setup",), ("parametric", "create_configuration", 0, 200),
    ],
    "DefinitionHTTPTests.test_owner_and_three_exact_compare_fields": [
        ("setup",), ("parametric", "create_configuration", 1, 403), ("compare-fields",),
    ],
    "DefinitionHTTPTests.test_two_measured_candidates_one_cas_receipt": [
        ("setup",), ("cas-pair",), ("parametric", "create_configuration", 0, 200),
        ("parametric", "create_configuration", 0, 409),
    ],
    "DefinitionHTTPTests.test_prior_revocation_prevents_candidate_receipt": [
        ("setup",), ("parametric", "create_configuration", 0, 401),
    ],
    "DefinitionHTTPTests.test_guard_wins_revoke_busy_then_receipt_replay_rechecks_live_session": [
        ("setup",), ("parametric", "create_configuration", 0, 200),
        ("parametric", "create_configuration", 0, 401),
    ],
    "DefinitionHTTPTests.test_step_failure_and_real_sqlite_receipt_abort_are_atomic": [
        ("setup",), ("parametric", "create_configuration", 0, 503),
        ("parametric", "create_configuration", 0, 503),
        ("parametric", "create_configuration", 0, 200),
    ],
}


def process_identity(process, argv):
    gate.require(process.poll() is None, "cli_exited_before_observation")
    path = Path("/proc") / str(process.pid)
    first = (path / "stat").read_text()
    raw = (path / "cmdline").read_bytes()
    actual = [part.decode() for part in raw.rstrip(b"\0").split(b"\0")]
    second = (path / "stat").read_text()
    ticks = lambda text: text[text.rfind(")") + 2:].split()[19]
    gate.require(actual == argv, "cli_proc_argv_mismatch")
    gate.require(ticks(first) == ticks(second), "cli_proc_startticks_changed")
    gate.require(process.poll() is None, "cli_exited_during_observation")
    return {"pid": process.pid, "startticks": ticks(first), "argv": actual,
            "cmdline_sha256": gate.digest(raw), "observed_after_ready": True}


class Client:
    def __init__(self, port, *, fixture):
        self.port = port
        self.origin = "http://127.0.0.1:" + str(port)
        self.fixture = str(fixture)
        self.cookies = []
        self.actors = []
        self.last_index = None

    def call(self, method, route, body=None, *, user=0, project=None, lose_response=False):
        flow_index = gate.claim_http_flow(method, route, body, user, project, lose_response)
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if user is not None:
            headers["Cookie"] = self.cookies[user]
        if project is not None:
            headers["X-OpenCAD-Project"] = project
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        try:
            connection.request(method, route, body=None if body is None else json.dumps(body),
                               headers=headers)
            response = connection.getresponse()
            response_headers = dict(response.getheaders())
            retained_headers = {}
            for name, value in response.getheaders():
                key = name.lower()
                if key in gate.HTTP_HEADERS:
                    gate.require(key not in retained_headers, "duplicate_response_header")
                    retained_headers[key] = value
            raw = b"" if lose_response else response.read()
            row = {"fixture": self.fixture, "at": gate.now(), "method": method, "route": route,
                   "status": response.status, "bytes": len(raw),
                   "response_sha256": gate.digest(raw), "lost_body": lose_response,
                   "project_id": project, "request_id": None if body is None else body.get("request_id"),
                   "actor": "anonymous" if user is None else self.actors[user],
                   "request": ({"username": body["username"], "password_omitted": True}
                               if route == "/api/auth/login" else copy.deepcopy(body)),
                   "headers": retained_headers, "test": gate.CONTEXT.active_test,
                   "flow_index": flow_index, "user": user}
            with gate.CONTEXT.lock:
                index = len(gate.CONTEXT.http)
                row.update(index=index, body_file=f"responses/{index:04d}.body",
                           record_file=f"responses/{index:04d}.json")
                directory = gate.CONTEXT.out / "responses"
                directory.mkdir(exist_ok=True)
                for data in (raw, gate.canonical(row)):
                    gate.require(not any(secret in data for secret in gate.CONTEXT.secrets),
                                 "secret_in_http_evidence")
                gate.write(gate.CONTEXT.out / row["body_file"], raw)
                gate.write(gate.CONTEXT.out / row["record_file"], row)
                gate.CONTEXT.http.append(row)
                self.last_index = index
            value = None if lose_response else (
                json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json")
                else raw)
            return response.status, response_headers, value
        finally:
            connection.close()

    def login(self, names, passwords):
        self.cookies = []
        self.actors = []
        for name, password in zip(names, passwords):
            status, headers, reply = self.call(
                "POST", "/api/auth/login", {"username": name, "password": password}, user=None)
            gate.require(status == 200 and reply["ok"] is True, "fixture_login_failed")
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            gate.CONTEXT.secrets.extend([cookie.encode(), cookie.split("=", 1)[1].encode()])
            self.cookies.append(cookie)
            self.actors.append(reply["user"]["user_id"])

    def state(self, project):
        status, _, value = self.call("GET", "/api/state", project=project)
        gate.require(status == 200, "state_http_failed")
        return value

    @staticmethod
    def envelope(state, **fields):
        return {"expected_revision": state["revision"], "expected_sha": state["sha256"],
                "request_id": uuid4().hex, **fields}

    def body(self, state, action, **fields):
        return self.envelope(state, action=action,
                             expected_document_sha256=gate.digest(state["doc"]), **fields)

    def create(self, *, plate=True):
        status, _, reply = self.call(
            "POST", "/api/projects", {"title": "Definition fixture", "request_id": uuid4().hex})
        gate.require(status == 201, "project_create_failed")
        project = reply["project_id"]
        state = self.state(project)
        if plate:
            status, _, reply = self.call(
                "POST", "/api/parametric", self.body(state, "create_configurable_plate"), project=project)
            gate.require(status == 200, "plate_create_failed")
            state = self.state(project)
        return project, state

    def witness(self, project, expected, label, kernel):
        state = self.state(project)
        indices = {"before": self.last_index}
        status, _, definition = self.call("GET", "/api/parametric", project=project)
        indices["definition"] = self.last_index
        gate.require(status == 200 and definition["document_sha256"] == gate.digest(state["doc"]),
                     "definition_read_version_mismatch")
        status, _, mesh = self.call("GET", "/api/mesh", project=project)
        indices["mesh"] = self.last_index
        gate.require(status == 200 and len(mesh["solids"]) == 1, "mesh_http_failed")
        gate.require((mesh["project_id"], mesh["revision"], mesh["sha256"]) ==
                     (project, state["revision"], state["sha256"]), "mesh_snapshot_mismatch")
        status, headers, step = self.call("GET", "/api/step", project=project)
        indices["step"] = self.last_index
        gate.require(status == 200 and headers["X-OpenCAD-Document-SHA256"] ==
                     gate.digest(state["doc"]) and int(headers["X-OpenCAD-Revision"]) ==
                     state["revision"],
                     "step_snapshot_mismatch")
        after = self.state(project)
        indices["after"] = self.last_index
        gate.require(all(after[key] == state[key] for key in (
            "project_id", "revision", "sha256", "doc")), "step_readback_snapshot_changed")
        gate.CONTEXT.measure(docmodel.document_from_l1(state["doc"]), mesh["solids"][0],
                             step, expected, label, kernel, receipt=state, mesh_envelope=mesh,
                             http_indices=indices)
        return state


class DefinitionHTTPTests(unittest.TestCase):
    def setUp(self):
        gate.ensure_context()
        gate.CONTEXT.active_test = self.id()
        self.root = gate.CONTEXT.child("http")
        self.auth = AuthController(self.root / "accounts", allow_registration=True)
        self.names = ["definition_" + uuid4().hex for _ in range(2)]
        self.passwords = [uuid4().hex for _ in self.names]
        gate.CONTEXT.secrets.extend(value.encode() for value in self.passwords)
        for name, password in zip(self.names, self.passwords):
            self.auth.register(name, password)
        self.store = ProjectStore(self.root / "projects")
        self.app = ProjectGateway(self.auth, self.store, self.root / "runtime", secure_cookies=False)
        self.http = build_server(self.app)
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.stop)
        self.client = Client(self.http.server_address[1], fixture=self.root)
        self.client.login(self.names, self.passwords)
        self.project, self.state = self.client.create()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        self.app.close()

    def anchor(self):
        db = sqlite3.connect(self.store.db_path.as_uri() + "?mode=ro", uri=True)
        try:
            counts = {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                      for table in ("projects", "snapshots", "receipts", "create_receipts")}
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            return self.store.read_head(self.project), counts
        finally:
            db.close()

    def assert_no_receipt(self, body):
        self.assertIsNone(self.store.read_receipt(
            self.project, body["request_id"], request_payload={**body, "route": "/api/parametric"}))

    def mutate(self, **fields):
        return self.client.call(
            "POST", "/api/parametric", self.client.body(self.state, **fields), project=self.project)

    def actor(self):
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--revoke-actor",
                str(self.auth.path), "--fixture-root", str(gate.CONTEXT.runtime),
                "--username", self.names[0]]
        process = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=15)
        row = {"argv": argv, "rc": process.returncode, "at": gate.now(),
               "stdout": process.stdout, "stderr": process.stderr, "fixture": str(self.root)}
        gate.CONTEXT.actors.append(row)
        self.assertEqual(process.returncode, 0, process.stderr)
        reply = json.loads(process.stdout)
        self.assertEqual(set(reply), {"state", "changed"})
        row["outcome"] = reply
        return reply

    def test_action_schema_boolean_parameter_and_active_delete_rejections(self):
        anchor = self.anchor()
        cases = [
            ({"action": "create_configuration", "name": "new", "definition": gate.definition(),
              "configuration": None}, 400, "bad_parametric_arguments"),
            ({"action": "delete_configuration", "name": "solid", "definition": {}},
             400, "bad_parametric_arguments"),
            ({"action": "create_configuration", "name": "new", "definition": gate.definition(),
              "overrides": {}}, 400, "bad_parametric_arguments"),
            ({"action": "set_suppressions", "name": "solid", "suppressions": {"hole_cut": 1}},
             422, "bad_feature_configuration"),
            ({"action": "set_suppressions", "name": "solid", "suppressions": {"other": True}},
             422, "unknown_feature_binding"),
            ({"action": "create_configuration", "name": "new", "definition": gate.definition("50")},
             422, "unit_mismatch"),
            ({"action": "create_configuration", "name": "new",
              "definition": gate.definition("hole_x * 2")}, 422, "parameter_cycle"),
            ({"action": "create_configuration", "name": "new",
              "definition": {"parameters": {}, "suppressions": {"plate_boss": True}}},
             422, "configuration_dependency_invalid"),
        ]
        for fields, expected, reason in cases:
            body = self.client.body(self.state, **fields)
            status, _, failure = self.client.call("POST", "/api/parametric", body, project=self.project)
            self.assertEqual((status, failure["error"]), (expected, reason), failure)
            self.assertEqual(self.anchor(), anchor)
            self.assert_no_receipt(body)
        status, _, active = self.mutate(action="rebuild", configuration="solid")
        self.assertEqual(status, 200, active)
        self.state = self.client.state(self.project)
        anchor = self.anchor()
        body = self.client.body(self.state, "delete_configuration", name="solid")
        status, _, failure = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual((status, failure["error"]), (422, "active_configuration_requires_selection"))
        self.assertEqual(self.anchor(), anchor)
        self.assert_no_receipt(body)

    def test_candidate_measured_once_and_final_cas(self):
        original = SubprocessKernel.shape_from_document
        calls = []
        def measured(kernel, *args, **kwargs):
            calls.append(1)
            return original(kernel, *args, **kwargs)
        with patch.object(SubprocessKernel, "shape_from_document", new=measured):
            status, _, reply = self.mutate(action="create_configuration", name="custom",
                                          definition=gate.definition(), configuration="custom")
        self.assertEqual(status, 200, reply)
        self.assertEqual(len(calls), 1, "Definition candidate must measure one complete replay")
        self.assertEqual(reply["revision"], self.state["revision"] + 1)
        self.assertEqual(reply["configuration_definitions"]["custom"], {"width": "50 * mm"})

    def test_owner_and_three_exact_compare_fields(self):
        body = self.client.body(self.state, "create_configuration", name="new",
                                definition=gate.definition())
        anchor = self.anchor()
        status, _, _ = self.client.call("POST", "/api/parametric", body,
                                       project=self.project, user=1)
        self.assertEqual(status, 403)
        for key, value in (("expected_document_sha256", "0"*64),
                           ("expected_sha", "0"*64),
                           ("expected_revision", self.state["revision"] + 1)):
            changed = {**body, key: value, "request_id": uuid4().hex}
            status, _, _ = self.client.call("POST", "/api/parametric", changed, project=self.project)
            self.assertEqual(status, 409)
            self.assert_no_receipt(changed)
        self.assertEqual(self.anchor(), anchor)

    def test_two_measured_candidates_one_cas_receipt(self):
        original = industrial.prepare_parametric
        barrier = threading.Barrier(2)
        def prepare(*args, **kwargs):
            candidate = original(*args, **kwargs)
            barrier.wait(timeout=90)
            return candidate
        bodies = [self.client.body(self.state, "create_configuration", name="custom"+str(index),
                                   definition=gate.definition(), configuration="custom"+str(index))
                  for index in range(2)]
        before = self.anchor()
        with patch.object(industrial, "prepare_parametric", side_effect=prepare):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda body: self.client.call(
                    "POST", "/api/parametric", body, project=self.project), bodies))
        self.assertEqual(sorted(row[0] for row in outcomes), [200, 409], outcomes)
        winner = next(index for index, row in enumerate(outcomes) if row[0] == 200)
        self.assert_no_receipt(bodies[1-winner])
        after = self.anchor()
        self.assertEqual(after[1]["receipts"], before[1]["receipts"] + 1)
        self.assertEqual(after[0]["revision"], before[0]["revision"] + 1)
        status, _, replay = self.client.call(
            "POST", "/api/parametric", bodies[winner], project=self.project)
        self.assertEqual((status, replay), (200, outcomes[winner][2]))
        self.assertEqual(self.anchor(), after)
        changed = {**bodies[winner], "name": "different"}
        status, _, _ = self.client.call("POST", "/api/parametric", changed, project=self.project)
        self.assertEqual(status, 409)
        self.assertEqual(self.anchor(), after)

    def test_prior_revocation_prevents_candidate_receipt(self):
        original = industrial.prepare_parametric
        outcomes = []
        def prepare(*args, **kwargs):
            candidate = original(*args, **kwargs)
            outcomes.append(self.actor())
            return candidate
        before = self.anchor()
        body = self.client.body(self.state, "create_configuration", name="custom",
                                definition=gate.definition(), configuration="custom")
        with patch.object(industrial, "prepare_parametric", side_effect=prepare):
            status, _, _ = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual(status, 401)
        self.assertEqual(outcomes, [{"state": "REVOKED", "changed": 1}])
        self.assertEqual(self.anchor(), before)
        self.assert_no_receipt(body)

    def test_guard_wins_revoke_busy_then_receipt_replay_rechecks_live_session(self):
        original = self.store.commit
        outcomes = []
        def commit(*args, **kwargs):
            outcomes.append(self.actor())
            return original(*args, **kwargs)
        body = self.client.body(self.state, "create_configuration", name="custom",
                                definition=gate.definition(), configuration="custom")
        with patch.object(self.store, "commit", side_effect=commit):
            status, _, reply = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual(status, 200, reply)
        self.assertEqual(outcomes, [{"state": "BUSY", "changed": 0}])
        anchor = self.anchor()
        original = self.store.read_receipt
        def receipt(*args, **kwargs):
            saved = original(*args, **kwargs)
            if saved is not None:
                self.assertEqual(self.actor(), {"state": "REVOKED", "changed": 1})
            return saved
        with patch.object(self.store, "read_receipt", side_effect=receipt):
            status, _, _ = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual(status, 401)
        self.assertEqual(self.anchor(), anchor)

    def test_step_failure_and_real_sqlite_receipt_abort_are_atomic(self):
        before = self.anchor()
        body = self.client.body(self.state, "create_configuration", name="custom",
                                definition=gate.definition(), configuration="custom")
        with patch.object(SubprocessKernel, "export", side_effect=OSError("owned STEP boundary failure")):
            status, _, failure = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual((status, failure["error"]), (503, "parametric_artifact_failed"))
        self.assertEqual(self.anchor(), before)
        self.assert_no_receipt(body)
        evidence = Path(failure["evidence_path"])
        self.assertTrue(evidence.is_relative_to(self.root))
        self.assertIn("owned STEP boundary failure", (evidence / "failure.log").read_text())
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("CREATE TRIGGER owned_receipt_abort BEFORE INSERT ON receipts "
                       "BEGIN SELECT RAISE(ABORT, 'owned receipt boundary failure'); END")
        try:
            body["request_id"] = uuid4().hex
            status, _, failure = self.client.call("POST", "/api/parametric", body, project=self.project)
            self.assertEqual((status, failure["error"]), (503, "storage_failure"))
            self.assertEqual(self.anchor(), before)
            self.assert_no_receipt(body)
        finally:
            with sqlite3.connect(self.store.db_path) as db:
                db.execute("DROP TRIGGER owned_receipt_abort")
        status, _, reply = self.client.call("POST", "/api/parametric", body, project=self.project)
        self.assertEqual(status, 200, reply)


class DefinitionCLITests(unittest.TestCase):
    def setUp(self):
        gate.ensure_context()
        gate.CONTEXT.active_test = self.id()
        self.root = gate.CONTEXT.child("cli")
        self.names = ["definition_" + uuid4().hex for _ in range(2)]
        self.passwords = [uuid4().hex for _ in self.names]
        gate.CONTEXT.secrets.extend(value.encode() for value in self.passwords)
        auth = AuthController(self.root / "accounts", allow_registration=True)
        try:
            for name, password in zip(self.names, self.passwords):
                auth.register(name, password)
        finally:
            auth.close()
        self.process = None
        self.addCleanup(self.stop)
        self.work = gate.CONTEXT.child("step-reader")
        self.kernel = SubprocessKernel(work_dir=str(self.work), keep_work_dir=True,
                                      extra_env={"TMPDIR": str(self.work),
                                                 "PYTHONDONTWRITEBYTECODE": "1"})
        self.addCleanup(self.kernel.close)

    def start(self):
        self.assertIsNone(self.process)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        argv = [sys.executable, "-B", str(APP / "secure_web.py"), "--data-root", str(self.root),
                "--host", "127.0.0.1", "--port", str(port),
                "--public-origin", "http://127.0.0.1:" + str(port)]
        number = len(gate.CONTEXT.cli)
        self.log_path = gate.CONTEXT.out / f"cli-{number:03d}.log"
        self.log = self.log_path.open("xb")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(self.root))
        self.process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        self.cli_row = {"argv": argv, "fixture": str(self.root), "started_at": gate.now(),
                        "log_file": self.log_path.name, "rc": None, "identity": None}
        gate.CONTEXT.cli.append(self.cli_row)
        deadline = time.monotonic() + 30
        ready = f"OPENCAD_PRIVATE_WEB verdict=READY port={port}\n"
        while time.monotonic() < deadline:
            text = self.log_path.read_text()
            if ready in text:
                self.assertEqual(text.count(ready), 1)
                self.cli_row["identity"] = process_identity(self.process, argv)
                self.cli_row["ready_marker"] = ready
                self.client = Client(port, fixture=self.root)
                self.client.login(self.names, self.passwords)
                return
            self.assertIsNone(self.process.poll(), "Original CLI exited before READY; see cli log")
            time.sleep(.05)
        self.fail("Original CLI did not produce READY; log retained")

    def stop(self):
        if self.process is None:
            return
        process = self.process
        try:
            self.assertEqual(process_identity(process, self.cli_row["argv"]),
                             self.cli_row["identity"])
            process.send_signal(signal.SIGTERM)
            rc = process.wait(timeout=25)
            self.cli_row.update(rc=rc, finished_at=gate.now(), normal_signal="SIGTERM")
            self.assertEqual(rc, 0, "Original CLI shutdown must return rc0")
        except BaseException:
            if process.poll() is None:
                process.kill()
                rc = process.wait(timeout=10)
                self.cli_row.update(rc=rc, forced_failure_shutdown=True, finished_at=gate.now())
            raise
        finally:
            self.log.close()
            self.process = None

    def test_real_cli_crud_lossresponse_save_undo_redo_restart(self):
        self.start()
        project, state = self.client.create()
        base = self.client.witness(project, BASE_VOLUME, "http-base", self.kernel)
        body = self.client.body(base, "create_configuration", name="custom",
                                definition=gate.definition(), configuration="custom")
        status, _, _ = self.client.call("POST", "/api/parametric", body,
                                       project=project, lose_response=True)
        self.assertEqual(status, 200)
        store = ProjectStore(self.root / "projects")
        try:
            head = store.read_head(project)
            receipt = store.read_receipt(
                project, body["request_id"], request_payload={**body, "route": "/api/parametric"})
            self.assertEqual(receipt["revision"], base["revision"] + 1)
            self.assertEqual(head["revision"], receipt["revision"])
            status, _, replay = self.client.call("POST", "/api/parametric", body, project=project)
            self.assertEqual(status, 200, replay)
            self.assertEqual(replay, {**receipt["response"], "project_id": project,
                                     "revision": receipt["revision"], "sha256": receipt["sha256"]})
            self.assertEqual(store.read_head(project), head)
            old_cookie = self.client.cookies[0]
            self.stop()
            self.start()
            self.assertEqual(store.read_head(project), head)
            current_cookie = self.client.cookies[0]
            self.client.cookies[0] = old_cookie
            status, _, repeated = self.client.call("POST", "/api/parametric", body, project=project)
            self.assertEqual((status, repeated), (200, replay))
            self.client.cookies[0] = current_cookie
        finally:
            store.close()
        custom = self.client.witness(project, 10000, "http-create-restart", self.kernel)
        body = self.client.body(custom, "replace_configuration", name="custom",
                                definition=gate.definition("60 * mm"), configuration="custom")
        status, _, reply = self.client.call("POST", "/api/parametric", body, project=project)
        self.assertEqual(status, 200, reply)
        wide = self.client.witness(project, 14400, "http-replace", self.kernel)
        body = self.client.body(wide, "set_suppressions", name="custom", suppressions={"hole_cut": False})
        status, _, reply = self.client.call("POST", "/api/parametric", body, project=project)
        self.assertEqual(status, 200, reply)
        pierced = self.client.witness(project, (1200-math.pi*36)*12, "http-unsuppress", self.kernel)
        for command, expected, old in (("undo", 14400, wide), ("redo", (1200-math.pi*36)*12, pierced)):
            state = self.client.state(project)
            status, _, reply = self.client.call(
                "POST", "/api/command", self.client.envelope(state, command=command, args={}),
                project=project)
            self.assertEqual(status, 200, reply)
            witness = self.client.witness(project, expected, "http-"+command, self.kernel)
            self.assertEqual(witness["doc"], old["doc"])
        state = self.client.state(project)
        status, _, reply = self.client.call(
            "POST", "/api/save", self.client.envelope(state), project=project)
        self.assertEqual(status, 200, reply)
        saved = self.client.state(project)
        self.stop()
        self.start()
        restarted = self.client.witness(
            project, (1200-math.pi*36)*12, "http-save-restart", self.kernel)
        for key in ("doc", "revision", "sha256", "undo_depth", "redo_depth"):
            self.assertEqual(restarted[key], saved[key])
        body = self.client.body(restarted, "delete_configuration", name="custom", configuration="")
        status, _, reply = self.client.call("POST", "/api/parametric", body, project=project)
        self.assertEqual(status, 200, reply)
        deleted = self.client.witness(project, BASE_VOLUME, "http-delete", self.kernel)
        self.assertNotIn("custom", json.loads(deleted["doc"])["parametric"]["configurations"])
        status, _, _ = self.client.call("POST", "/api/parametric", body, project=project, user=1)
        self.assertEqual(status, 403)
        status, _, _ = self.client.call("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)
        status, _, _ = self.client.call("POST", "/api/parametric", body, project=project)
        self.assertEqual(status, 401)
        self.stop()


if __name__ == "__main__":
    raise SystemExit(gate.run_gate(
        [gate.GateControls, DefinitionCLITests, DefinitionHTTPTests],
        label="http-definition", minimum_measurements=8))
