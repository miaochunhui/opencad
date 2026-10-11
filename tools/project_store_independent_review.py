#!/usr/bin/env python3
"""Independent HTTP/SQLite review of durable project integration.

Run only an isolated loopback fixture. Explicit --source-root, --auth-root and
--store-source-root bind dependencies; mixed roots are never called same-source.
Only --out (3FS evidence) and --runtime-root (private local fixtures) are written.
This is not a production client, general security audit or CAD regression.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import http.client
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve()
SOURCE = AUTH = STORE_SOURCE = RUNTIME = None
CORE = STORE = GATEWAY = HTTP = ACCOUNTS = SERVER = None
OBSERVATIONS = []


class ReviewResult(unittest.TextTestResult):
    """Count failing methods once, while retaining every subtest failure."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failed_methods = set()

    def addFailure(self, test, err):
        self.failed_methods.add(test.id())
        super().addFailure(test, err)

    def addError(self, test, err):
        self.failed_methods.add(test.id())
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.failed_methods.add(test.id())
        super().addSubTest(test, subtest, err)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"not regular: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(f"linked parent: {parent}")
    return path.read_bytes()


def manifest():
    result = {str(HERE): sha(regular(HERE))}
    trees = [SOURCE / "oss-alternatives/apps/opencad",
             SOURCE / "oss-alternatives/packages/cadcore/cadcore",
             SOURCE / "oss-alternatives/packages/dxf/dxf",
             SOURCE / "oss-alternatives/packages/vault/freechat_vault"]
    for tree in trees:
        if not tree.is_dir() or tree.is_symlink():
            raise ValueError(f"runtime source tree missing/linked: {tree}")
        for directory, dirs, names in os.walk(tree, followlinks=False):
            dirs[:] = [name for name in dirs if name not in {
                "tests", "build", "__pycache__", ".git", "web"}]
            for name in names:
                path = Path(directory) / name
                if path.suffix in {".py", ".json", ".yaml", ".yml", ".txt", ".l1"}:
                    result[str(path)] = sha(regular(path))
    for path in (
        AUTH / "oss-alternatives/apps/opencad/access_control.py",
        AUTH / "oss-alternatives/packages/cadcore/cadcore/i18n/catalog.py",
        SOURCE / "oss-alternatives/docs/design/project-store.md",
        STORE_SOURCE / "oss-alternatives/apps/opencad/project_store.py",
        STORE_SOURCE / "oss-alternatives/docs/design/project-store.md",
    ):
        result[str(path)] = sha(regular(path))
    return dict(sorted(result.items()))


def explicit_load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_runtime():
    global CORE, STORE, GATEWAY, HTTP, ACCOUNTS, SERVER
    app = SOURCE / "oss-alternatives/apps/opencad"
    packages = SOURCE / "oss-alternatives/packages"
    os.environ["OPENCAD_CADCORE_ROOT"] = str(packages / "cadcore")
    for path in (app, packages / "cadcore", packages / "dxf", packages / "vault"):
        sys.path.insert(0, str(path))
    package = importlib.import_module("cadcore.i18n")
    catalog_path = AUTH / "oss-alternatives/packages/cadcore/cadcore/i18n/catalog.py"
    package.catalog = explicit_load("cadcore.i18n.catalog", catalog_path)
    ACCOUNTS = explicit_load("access_control", AUTH / "oss-alternatives/apps/opencad/access_control.py")
    STORE = explicit_load(
        "project_store", STORE_SOURCE / "oss-alternatives/apps/opencad/project_store.py")
    GATEWAY = importlib.import_module("project_gateway")
    HTTP = importlib.import_module("secure_web")
    SERVER = importlib.import_module("server")
    CORE = SERVER._load_cadcore()
    expected = {
        "project_store": STORE_SOURCE / "oss-alternatives/apps/opencad/project_store.py",
        "project_gateway": app / "project_gateway.py",
        "secure_web": app / "secure_web.py",
        "server": app / "server.py",
        "access_control": AUTH / "oss-alternatives/apps/opencad/access_control.py",
        "cadcore.i18n.catalog": catalog_path,
    }
    for name, path in expected.items():
        if Path(sys.modules[name].__file__).resolve() != path:
            raise ValueError(f"foreign import: {name}")
    return expected


def runtime_origins(bound):
    result = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path or not (name.startswith(("cadcore.", "dxf.", "freechat_vault.", "industrial_"))
                            or name in {"server", "secure_web", "project_gateway",
                                        "project_store", "access_control", "release_state",
                                        "solid_commands", "viewer3d_page", "render_service"}):
            continue
        actual = str(Path(path).resolve())
        if actual not in bound:
            raise ValueError(f"runtime module not source-bound: {name}: {actual}")
        result[name] = actual
    return result


class IndependentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auth = ACCOUNTS.AuthController(RUNTIME / "accounts", allow_registration=True)
        cls.credential = uuid.uuid4().hex
        cls.user = cls.auth.register("durable-review", cls.credential)["user_id"]

    @classmethod
    def tearDownClass(cls):
        cls.auth.close()
        cls.credential = None

    def setUp(self):
        self.root = RUNTIME / (self._testMethodName + "-" + uuid.uuid4().hex)
        self.store = STORE.ProjectStore(self.root / "projects")
        self.addCleanup(self.store.close)
        self.ticket = self.auth.login("durable-review", self.credential)
        self.app = GATEWAY.ProjectGateway(
            self.auth, self.store, self.root / "runtime", core=CORE, secure_cookies=False)
        self.httpd = HTTP.build_server(self.app, web_root=SOURCE / "oss-alternatives/apps/opencad/web")
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.head, self.create_body = self.create()

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("fixture HTTP thread did not finish")

    def call(self, method, route, body=None, *, project=None, raw=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=15)
        headers = {"Authorization": "Bearer " + self.ticket.token,
                   "X-OpenCAD-Request": "1"}
        if project is not None:
            headers["X-OpenCAD-Project"] = project
        data = raw if raw is not None else json.dumps(body) if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, route, data, headers)
            response = connection.getresponse()
            payload = response.read()
            if response.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def create(self):
        body = {"title": "Durable Review", "request_id": uuid.uuid4().hex, "standard": "gbt"}
        status, _, head = self.call("POST", "/api/projects", body)
        self.assertEqual(status, 201, head)
        return head, body

    def full_head(self):
        return self.store.read_head(self.head["project_id"])

    def rows(self):
        with sqlite3.connect(self.store.db_path) as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                    for table in ("projects", "snapshots", "receipts", "create_receipts")}

    def body(self, head=None, **fields):
        head = self.head if head is None else head
        return {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, **fields}

    def edit(self, body, route="/api/command"):
        return self.call("POST", route, body, project=self.head["project_id"])

    def line(self, head=None):
        return self.body(head, command="add_line", args={"p1": [0, 0], "p2": [10000000, 0]})

    def accepted(self, body, route="/api/command"):
        status, _, result = self.edit(body, route)
        self.assertEqual(status, 200, result)
        self.assertIs(result.get("ok"), True)
        return result

    def test_fixture_actual_source_and_sqlite(self):
        self.assertEqual(Path(STORE.__file__).resolve(),
                         STORE_SOURCE / "oss-alternatives/apps/opencad/project_store.py")
        self.assertEqual(self.store.db_path.read_bytes()[:16], b"SQLite format 3\x00")
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        self.assertEqual(set(self.full_head()["snapshot"]), {"doc", "undo", "redo", "standard"})

    def test_create_http_receipt_replay_and_body_conflict(self):
        before, rows = self.full_head(), self.rows()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.call, "POST", "/api/projects", self.create_body)
                       for _ in range(2)]
            replies = [future.result(timeout=15) for future in futures]
        self.assertEqual([item[0] for item in replies], [201, 201], replies)
        self.assertEqual(replies[0][2], self.head)
        self.assertEqual(replies[0][2], replies[1][2])
        for field, value in (("title", "different"), ("standard", "ansi")):
            with self.subTest(field=field):
                status, _, result = self.call(
                    "POST", "/api/projects", {**self.create_body, field: value})
                self.assertEqual(status, 409, result)
                self.assertEqual(result["error"], "idempotency_conflict")
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_malformed_base_types_before_action(self):
        before, rows = self.full_head(), self.rows()
        calls = []
        original = SERVER.Workspace.command

        def track(workspace, *args, **kwargs):
            calls.append(True)
            return original(workspace, *args, **kwargs)

        with patch.object(SERVER.Workspace, "command", track):
            for field, values in {
                "expected_revision": [True, False, None, 0, -1, 1.0, "1", [], {}, 2**63],
                "expected_sha": [None, True, [], {}, "", "f" * 63, "F" * 64],
                "request_id": [None, True, [], {}, "", "../bad"],
            }.items():
                for value in values:
                    with self.subTest(field=field, type=type(value).__name__):
                        body = self.line()
                        body[field] = value
                        status, _, result = self.edit(body)
                        self.assertEqual(status, 400, result)
        self.assertEqual(calls, [])
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_repeated_request_concurrency_one_receipt(self):
        barrier = threading.Barrier(2)
        original = SERVER.Workspace.command

        def synchronized(workspace, *args, **kwargs):
            barrier.wait(timeout=5)
            return original(workspace, *args, **kwargs)

        body = self.line()
        with patch.object(SERVER.Workspace, "command", synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.edit, body) for _ in range(2)]
                results = [future.result(timeout=15) for future in futures]
        self.assertEqual([item[0] for item in results], [200, 200], results)
        self.assertEqual(results[0][2], results[1][2])
        rows = self.rows()
        self.assertEqual(len(rows["receipts"]), 1)
        self.assertEqual(len(rows["snapshots"]), 2)
        self.assertEqual(self.full_head()["revision"], 2)
        self.assertEqual(len(self.full_head()["snapshot"]["undo"]), 1)

    def test_same_base_different_requests_exactly_one_commit(self):
        barrier = threading.Barrier(2)
        original = SERVER.Workspace.command

        def synchronized(workspace, *args, **kwargs):
            barrier.wait(timeout=5)
            return original(workspace, *args, **kwargs)

        with patch.object(SERVER.Workspace, "command", synchronized):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.edit, self.line()) for _ in range(2)]
                results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(item[0] for item in results), [200, 409], results)
        self.assertEqual(len(self.rows()["receipts"]), 1)

    def test_replay_bypasses_action_and_body_change_conflicts(self):
        body = self.line()
        response = self.accepted(body)
        before, rows = self.full_head(), self.rows()
        with patch.object(SERVER.Workspace, "command", side_effect=AssertionError("replay executed action")):
            self.assertEqual(self.edit(body)[::2], (200, response))
            changed = {**body, "args": {"p1": [0, 0], "p2": [20000000, 0]}}
            status, _, result = self.edit(changed)
            self.assertEqual(status, 409, result)
            self.assertEqual(result["error"], "idempotency_conflict")
            cross_route = {key: value for key, value in body.items() if key not in {"args", "command"}}
            self.assertEqual(self.edit(cross_route, "/api/save")[0], 409)
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_aba_full_workspace_old_revision_rejected(self):
        initial = self.full_head()
        first = self.store.commit(self.head["project_id"], expected_revision=1,
            expected_sha=initial["sha256"], request_id="aba-b",
            request_payload=self.body(command="fixture"), payload={
                **initial["snapshot"], "undo": [initial["snapshot"]["doc"]]}, response={"ok": True})
        self.store.commit(self.head["project_id"], expected_revision=2,
            expected_sha=first["sha256"], request_id="aba-a",
            request_payload=self.body(first, command="fixture"),
            payload=initial["snapshot"], response={"ok": True})
        before, rows = self.full_head(), self.rows()
        self.assertEqual(before["sha256"], initial["sha256"])
        self.assertEqual(before["revision"], 3)
        status, _, result = self.edit(self.line())
        self.assertEqual(status, 409, result)
        self.assertEqual((result["revision"], result["sha256"]), (3, initial["sha256"]))
        self.assertEqual(self.rows(), rows)

    def test_standard_undo_redo_save_open_history(self):
        standard = self.accepted(self.body(standard="ansi"), "/api/standard")
        self.assertEqual(self.full_head()["snapshot"]["standard"], "ansi")
        undone = self.accepted(self.body(standard, command="undo", args={}))
        self.assertEqual(self.full_head()["snapshot"]["standard"], "gbt")
        self.assertEqual(len(self.full_head()["snapshot"]["redo"]), 1)
        redone = self.accepted(self.body(undone, command="redo", args={}))
        before = self.full_head()["snapshot"]
        saved = self.accepted(self.body(redone), "/api/save")
        self.assertEqual(self.full_head()["snapshot"], before)
        self.assertEqual(saved["revision"], redone["revision"] + 1)
        initial_doc = self.store.read_snapshot(self.head["project_id"], 1)["snapshot"]["doc"]
        opened = self.accepted(self.body(saved, doc=initial_doc), "/api/open")
        snapshot = self.full_head()["snapshot"]
        self.assertEqual(snapshot["standard"], "gbt")
        self.assertEqual((snapshot["undo"], snapshot["redo"]), ([], []))
        with STORE.ProjectStore(self.store.root) as reopened:
            self.assertEqual(reopened.read_head(self.head["project_id"]), self.full_head())
        self.assertEqual(opened["revision"], 6)

    def test_sqlite_failure_every_mutation_route_no_publish(self):
        before, rows = self.full_head(), self.rows()
        original = self.store._connection

        @contextmanager
        def deny_commit():
            with original() as conn:
                def authorize(action, arg1, *_):
                    return (sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION
                            and arg1 == "COMMIT" else sqlite3.SQLITE_OK)
                conn.set_authorizer(authorize)
                yield conn

        for route, body in (
            ("/api/command", self.line()),
            ("/api/standard", self.body(standard="ansi")),
            ("/api/save", self.body()),
            ("/api/open", self.body(doc=before["snapshot"]["doc"])),
        ):
            with self.subTest(route=route), patch.object(self.store, "_connection", deny_commit):
                status, _, result = self.edit(body, route)
                self.assertEqual(status, 503, result)
                self.assertEqual(result["error"], "storage_failure")
            self.assertEqual(self.full_head(), before)
            self.assertEqual(self.rows(), rows)
        with STORE.ProjectStore(self.store.root) as reopened:
            self.assertEqual(reopened.read_head(self.head["project_id"]), before)

    def test_invalid_document_history_and_command_do_not_commit(self):
        before, rows = self.full_head(), self.rows()
        for route, body in (
            ("/api/open", self.body(doc='{"schema_version":1}')),
            ("/api/standard", self.body(standard="unknown")),
            ("/api/command", self.body(command="unknown", args={})),
            ("/api/command", self.body(command="undo", args={})),
        ):
            with self.subTest(route=route):
                status, _, result = self.edit(body, route)
                self.assertEqual(self.rows(), rows)
                self.assertEqual(self.full_head(), before)
                OBSERVATIONS.append({"test": self.id(), "route": route,
                                     "status": status, "error": result.get("error"),
                                     "durable_rows_unchanged": True})
                self.assertIn(status, {400, 422}, result)
                self.assertIs(result["ok"], False)
        self.assertEqual(self.full_head(), before)

    def test_open_geometry_scalar_types_rejected(self):
        for value in (True, None, "1"):
            self.head, _ = self.create()
            self.accepted(self.line())
            before, rows = self.full_head(), self.rows()
            candidate = json.loads(before["snapshot"]["doc"])
            candidate["sketches"][0]["entities"][0]["geom"]["x1"] = value
            with self.subTest(type=type(value).__name__):
                status, _, result = self.edit(self.body(before, doc=json.dumps(candidate)), "/api/open")
                OBSERVATIONS.append({"test": self.id(), "case": type(value).__name__,
                                     "status": status, "error": result.get("error"),
                                     "durable_rows_unchanged": self.rows() == rows})
                self.assertIn(status, {400, 422}, result)
                self.assertEqual(self.full_head(), before)
                self.assertEqual(self.rows(), rows)

    def test_open_duplicate_entity_uuid_rejected(self):
        self.accepted(self.line())
        before, rows = self.full_head(), self.rows()
        candidate = json.loads(before["snapshot"]["doc"])
        entities = candidate["sketches"][0]["entities"]
        entities.append(json.loads(json.dumps(entities[0])))
        status, _, result = self.edit(self.body(before, doc=json.dumps(candidate)), "/api/open")
        OBSERVATIONS.append({"test": self.id(), "case": "duplicate_uuid",
                             "status": status, "error": result.get("error"),
                             "durable_rows_unchanged": self.rows() == rows})
        self.assertIn(status, {400, 422}, result)
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_corrupt_computed_history_not_acknowledged(self):
        before, rows = self.full_head(), self.rows()
        original = SERVER.Workspace.command

        def corrupt_history(workspace, *args, **kwargs):
            result = original(workspace, *args, **kwargs)
            workspace.undo_stack.append('{"schema_version":1}')
            return result

        with patch.object(SERVER.Workspace, "command", corrupt_history):
            status, _, result = self.edit(self.line())
        self.assertIs(result.get("ok"), False)
        self.assertNotEqual(status, 200)
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_read_routes_and_queries_do_not_add_history(self):
        line = self.accepted(self.line())
        head = self.accepted(self.body(
            line, command="layer_state_set", args={"layer": "0", "off": False}))
        before, rows = self.full_head(), self.rows()
        for route in ("/api/state", "/api/document", "/api/dxf", "/api/pdf", "/api/mesh"):
            with self.subTest(route=route):
                status, headers, result = self.call("GET", route, project=head["project_id"])
                self.assertEqual(status, 200, str(result)[:200])
                if route in {"/api/document", "/api/dxf", "/api/pdf"}:
                    self.assertEqual(int(headers["X-OpenCAD-Revision"]), head["revision"])
                    self.assertEqual(headers["X-OpenCAD-Document-SHA256"],
                                     sha(before["snapshot"]["doc"].encode()))
        for command, args in (
            ("precision_status", {}), ("layer_state", {"state": "all"}),
            ("layer_visible_entities", {}), ("grip_positions", {"ref": line["uuid"]}),
        ):
            status, _, result = self.edit({"command": command, "args": args})
            self.assertEqual(status, 200, result)
        self.assertEqual(self.rows(), rows)
        self.assertEqual(self.full_head(), before)

    def test_revoked_after_action_no_receipt_or_history(self):
        before, rows = self.full_head(), self.rows()
        original = SERVER.Workspace.command

        def revoke_after_action(workspace, *args, **kwargs):
            result = original(workspace, *args, **kwargs)
            self.auth.logout(self.ticket.token)
            return result

        with patch.object(SERVER.Workspace, "command", revoke_after_action):
            status, _, result = self.edit(self.line())
        self.assertEqual(status, 401, result)
        self.assertEqual(result["error"], "revoked_token")
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_committed_lost_http_response_retry_reopens_receipt(self):
        body = self.line()
        original = self.store.commit

        def lost_response(*args, **kwargs):
            original(*args, **kwargs)
            raise STORE.StorageFailure("fixture response loss after actual COMMIT")

        with patch.object(self.store, "commit", lost_response):
            self.assertEqual(self.edit(body)[0], 503)
        self.assertEqual(self.full_head()["revision"], 2)
        with STORE.ProjectStore(self.store.root) as reopened:
            receipt = reopened.read_receipt(
                self.head["project_id"], body["request_id"],
                request_payload={**body, "route": "/api/command"})
            self.assertIsNotNone(receipt)
        with patch.object(SERVER.Workspace, "command", side_effect=AssertionError("retry reran action")):
            status, _, result = self.edit(body)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["revision"], receipt["revision"])
        self.assertEqual(len(self.rows()["receipts"]), 1)

    def test_malformed_command_container_is_client_error_not_500(self):
        before, rows = self.full_head(), self.rows()
        for command in ([], {}):
            with self.subTest(type=type(command).__name__):
                status, _, result = self.edit(self.body(command=command, args={}))
                self.assertEqual(self.full_head(), before)
                self.assertEqual(self.rows(), rows)
                self.assertEqual(status, 400, result)
        self.assertEqual(self.full_head(), before)
        self.assertEqual(self.rows(), rows)

    def test_mutating_export_does_not_acknowledge_deleted_file(self):
        before, rows = self.full_head(), self.rows()
        body = self.body(command="export_pdf", args={"out": "review.pdf"})
        status, _, result = self.edit(body)
        receipt = self.store.read_receipt(
            self.head["project_id"], body["request_id"],
            request_payload={**body, "route": "/api/command"})
        OBSERVATIONS.append({
            "test": self.id(), "status": status,
            "revision_before": before["revision"],
            "revision_after": self.full_head()["revision"],
            "receipt_committed": receipt is not None,
            "returned_path": result.get("path"),
            "returned_file_exists": Path(result["path"]).is_file() if result.get("path") else False,
        })
        if status == 200:
            self.assertIsNotNone(receipt, "successful mutation lacks a durable receipt")
            self.assertTrue(result.get("path") and Path(result["path"]).is_file(),
                            "durable receipt acknowledged a file deleted on request exit")
        else:
            self.assertIn(status, {400, 405, 422}, result)
            self.assertEqual(self.full_head(), before)
            self.assertEqual(self.rows(), rows)


def write(path, value):
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    global SOURCE, AUTH, STORE_SOURCE, RUNTIME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--auth-root", type=Path,
                        help="Explicit provisional auth/catalog source; defaults to source-root")
    parser.add_argument("--store-source-root", type=Path,
                        help="Explicit candidate store source; defaults to source-root")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    SOURCE = args.source_root.resolve()
    AUTH = (args.auth_root or args.source_root).resolve()
    STORE_SOURCE = (args.store_source_root or args.source_root).resolve()
    RUNTIME = args.runtime_root.absolute()
    out = args.out.resolve()
    for root in (SOURCE, AUTH, STORE_SOURCE, HERE.parents[1]):
        if RUNTIME.resolve().is_relative_to(root) or out.is_relative_to(root):
            parser.error("fixture/evidence must be outside source repositories")
    if not out.is_relative_to(Path("/3fs/research3fs/data/tmp")):
        parser.error("--out must be 3FS tmp evidence")
    if RUNTIME.exists():
        parser.error("--runtime-root must be a new isolated local directory")
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "started_at": now(), "source_root": str(SOURCE),
              "auth_root": str(AUTH), "store_source_root": str(STORE_SOURCE),
              "same_source": SOURCE == AUTH == STORE_SOURCE,
              "runtime_root": str(RUNTIME), "production": "NOT_TOUCHED",
              "security_suite": "NOT_ASSESSED", "power_loss": "NOT_ASSESSED"}
    start = time.monotonic()
    before = None
    with (out / "tests.log").open("x", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                before = manifest()
                report["source_sha256"] = before
                load_runtime()
                # ProjectStore validates the local filesystem before any fixture DB opens.
                probe = STORE.ProjectStore(RUNTIME / "storage-probe")
                probe.close()
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(IndependentTests)
                count = suite.countTestCases()
                result = unittest.TextTestRunner(
                    stream=log, verbosity=2, resultclass=ReviewResult).run(suite)
                report.update(tests=result.testsRun, discovered=count,
                    failures=[{"test": str(test), "traceback": detail} for test, detail in result.failures],
                    errors=[{"test": str(test), "traceback": detail} for test, detail in result.errors],
                    skip=[{"test": str(test), "reason": reason} for test, reason in result.skipped],
                    failed_tests=len(result.failed_methods),
                    passed=result.testsRun - len(result.failed_methods) - len(result.skipped),
                    observations=OBSERVATIONS)
                report["runtime_origins"] = runtime_origins(before)
                if result.wasSuccessful() and result.testsRun == count and count > 0 and not result.skipped:
                    report["verdict"] = "PASS"
            except Exception as exc:
                report["cause"] = {"type": type(exc).__name__, "detail": str(exc),
                                   "traceback": traceback.format_exc()}
                traceback.print_exc()
            finally:
                if before is not None:
                    try:
                        after = manifest()
                        drift = sorted(path for path in before.keys() | after.keys()
                                       if before.get(path) != after.get(path))
                        report["source_drift"] = drift
                        if drift:
                            report["verdict"] = "FAIL"
                    except Exception as exc:
                        report["source_readback_error"] = str(exc)
                        report["verdict"] = "FAIL"
                report["finished_at"] = now()
                report["wall_s"] = time.monotonic() - start
                line = (f"PROJECT_STORE_INDEPENDENT verdict={report['verdict']} "
                        f"tests={report.get('tests', 'NOT_CAPTURED')} "
                        f"same_source={report['same_source']} collected_at={report['finished_at']}")
                log.write(line + "\n")
                log.flush()
                os.fsync(log.fileno())
    report["log_sha256"] = sha((out / "tests.log").read_bytes())
    write(out / "result.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(line)
    print(f"evidence={out}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
