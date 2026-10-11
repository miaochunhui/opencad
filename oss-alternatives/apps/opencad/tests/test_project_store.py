"""Real SQLite durability tests; --evidence stores logs/results externally.

WAL fixtures need local storage, not 3FS. --runtime-root is an explicitly
dedicated local fixture directory; logs and source hashes go to --evidence.
Crash workers run this exact test file and exit without closing SQLite.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
sys.path.insert(0, str(APP))
import project_store as module
from project_store import (
    IdempotencyConflict, InvalidPayload, ProjectNotFound, ProjectStore,
    RevisionConflict, StorageFailure, StoreCorrupt,
)

RUNTIME_ROOT = Path("/data/dev/opencad-durable-fixtures-20261011")
_UNSET = object()


def workspace(version=1):
    return {"doc": json.dumps({"version": version}), "undo": [],
            "redo": [], "standard": "gbt"}


def request(head, **extra):
    return {"expected_revision": head["revision"], "expected_sha": head["sha256"],
            "command": "edit", "args": {"version": 2}, **extra}


class DenyCommitStore(ProjectStore):
    @contextmanager
    def _connection(self):
        with super()._connection() as conn:
            def authorize(action, arg1, _arg2, _database, _source):
                if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            yield conn


class CrashBeforeCommitStore(ProjectStore):
    crash_enabled = False

    @contextmanager
    def _transaction(self):
        with super()._transaction() as conn:
            yield conn
            if self.crash_enabled:
                os._exit(74)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.runtime = RUNTIME_ROOT / (self._testMethodName + "-" + uuid.uuid4().hex)
        self.store = ProjectStore(self.runtime)
        self.addCleanup(self.store.close)
        self.head = self.store.create_project(
            "alice", title="Part", request_id="create-1", payload=workspace())
        self.project = self.head["project_id"]

    def commit(self, head=None, request_id="edit-1", payload=_UNSET, response=_UNSET, store=None):
        head = self.head if head is None else head
        return (store or self.store).commit(
            self.project, expected_revision=head["revision"], expected_sha=head["sha256"],
            request_id=request_id, request_payload=request(head),
            payload=workspace(2) if payload is _UNSET else payload,
            response={"ok": True, "entities": 1} if response is _UNSET else response)

    def test_local_module_and_real_sqlite_binding(self):
        self.assertEqual(Path(module.__file__).resolve(), APP / "project_store.py")
        self.assertEqual(self.store.db_path.read_bytes()[:16], b"SQLite format 3\x00")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_head_fields_and_full_workspace(self):
        self.assertEqual(set(self.head), {
            "project_id", "owner", "title", "revision", "sha256",
            "snapshot", "created_at", "updated_at"})
        self.assertEqual(self.head["snapshot"], workspace())
        self.assertEqual(self.head["revision"], 1)
        canonical = json.dumps(workspace(), sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"))
        self.assertEqual(self.head["sha256"], hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(self.store.get_project(self.project), self.head)

    def test_returned_heads_are_detached(self):
        self.head["snapshot"]["undo"].append('{"changed":true}')
        self.assertEqual(self.store.read_head(self.project)["snapshot"]["undo"], [])

    def test_create_is_idempotent_after_reopen(self):
        with ProjectStore(self.runtime) as other:
            duplicate = other.create_project(
                "alice", title="Part", request_id="create-1", payload=workspace())
            self.assertEqual(duplicate, self.head)
            self.assertEqual(len(other.list_projects("alice")), 1)

    def test_create_replay_is_initial_response_after_edits(self):
        self.commit()
        self.assertEqual(self.store.create_project(
            "alice", title="Part", request_id="create-1", payload=workspace()), self.head)
        self.assertEqual(self.store.read_head(self.project)["revision"], 2)

    def test_create_same_id_different_body_conflicts(self):
        for title, payload in (("Other", workspace()), ("Part", workspace(9))):
            with self.subTest(title=title), self.assertRaises(IdempotencyConflict) as error:
                self.store.create_project(
                    "alice", title=title, request_id="create-1", payload=payload)
            self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.store.list_projects("alice")), 1)

    def test_create_receipt_lookup_and_missing(self):
        self.assertEqual(self.store.read_create_receipt(
            "alice", "create-1", title="Part", payload=workspace()), self.head)
        self.assertIsNone(self.store.read_create_receipt(
            "alice", "unknown", title="Part", payload=workspace()))
        with self.assertRaises(IdempotencyConflict):
            self.store.read_create_receipt("alice", "create-1", title="Other", payload=workspace())

    def test_owner_isolation_and_list_metadata(self):
        bob = self.store.create_project("bob", title="B", request_id="create-1", payload=workspace())
        records = self.store.list_projects("alice")
        self.assertEqual(len(records), 1)
        self.assertNotIn("snapshot", records[0])
        self.assertEqual(records[0]["project_id"], self.project)
        self.assertEqual(self.store.list_projects("bob")[0]["project_id"], bob["project_id"])
        self.assertEqual(self.store.list_projects("nobody"), [])

    def test_commit_atomic_snapshot_history_response(self):
        new = {"doc": '{"version":2}', "undo": [workspace()["doc"]],
               "redo": ['{"version":3}'], "standard": "ansi"}
        receipt = self.commit(payload=new, response={"ok": True, "path": "edit-1.step"})
        self.assertEqual(set(receipt), {
            "project_id", "request_id", "request_sha256", "revision",
            "sha256", "response", "committed_at"})
        self.assertEqual(receipt["revision"], 2)
        self.assertEqual(receipt["response"], {"ok": True, "path": "edit-1.step"})
        self.assertEqual(self.store.read_head(self.project)["snapshot"], new)
        self.assertEqual(self.store.read_snapshot(self.project, 1), self.head)
        self.assertEqual(receipt["sha256"], self.store.read_head(self.project)["sha256"])

    def test_retry_returns_identical_receipt_even_with_new_derived_values(self):
        receipt = self.commit()
        replay = self.commit(payload=workspace(999), response={"ok": True, "different": True})
        self.assertEqual(replay, receipt)
        replay["response"]["ok"] = False
        self.assertEqual(self.store.read_receipt(
            self.project, "edit-1", request_payload=request(self.head)), receipt)
        self.assertEqual(self.store.read_head(self.project)["revision"], 2)

    def test_same_request_id_different_request_conflicts_before_action(self):
        receipt = self.commit()
        altered = request(self.head, command="delete")
        with self.assertRaises(IdempotencyConflict) as error:
            self.store.read_receipt(self.project, "edit-1", request_payload=altered)
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(IdempotencyConflict):
            self.store.commit(
                self.project, expected_revision=1, expected_sha=self.head["sha256"],
                request_id="edit-1", request_payload=altered, payload=workspace(),
                response={})
        self.assertEqual(self.store.read_receipt(
            self.project, "edit-1", request_payload=request(self.head)), receipt)

    def test_stale_revision_and_wrong_sha_rejected(self):
        self.commit()
        with self.assertRaises(RevisionConflict) as error:
            self.commit(request_id="stale")
        self.assertEqual(error.exception.revision, 2)
        self.assertEqual(error.exception.status, 409)
        current = self.store.read_head(self.project)
        self.assertEqual(error.exception.sha256, current["sha256"])
        wrong = dict(current, sha256="0" * 64)
        with self.assertRaises(RevisionConflict):
            self.commit(head=wrong, request_id="wrong-sha")
        self.assertIsNone(self.store.read_receipt(
            self.project, "stale", request_payload=request(self.head)))

    def test_aba_stale_sha_cannot_overwrite(self):
        self.commit()
        b = self.store.read_head(self.project)
        self.commit(head=b, request_id="back-a", payload=workspace())
        a_again = self.store.read_head(self.project)
        self.assertEqual(a_again["sha256"], self.head["sha256"])
        self.assertEqual(a_again["revision"], 3)
        with self.assertRaises(RevisionConflict):
            self.commit(request_id="stale-a")
        self.assertEqual(self.store.read_head(self.project), a_again)

    def test_unchanged_snapshot_still_advances_revision(self):
        receipt = self.commit(payload=workspace())
        self.assertEqual(receipt["sha256"], self.head["sha256"])
        self.assertEqual(receipt["revision"], 2)

    def test_two_connections_same_base_only_one_commit(self):
        barrier = threading.Barrier(2)
        with ProjectStore(self.runtime) as other:
            def contender(store, identifier):
                barrier.wait(timeout=5)
                try:
                    return ("committed", self.commit(store=store, request_id=identifier))
                except RevisionConflict as exc:
                    return ("conflict", exc.revision)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contender, store, identifier)
                           for store, identifier in ((self.store, "race-a"), (other, "race-b"))]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(result[0] for result in results), ["committed", "conflict"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM receipts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM snapshots").fetchone()[0], 2)

    def test_two_connections_same_request_one_receipt(self):
        barrier = threading.Barrier(2)
        with ProjectStore(self.runtime) as other:
            def contender(store):
                barrier.wait(timeout=5)
                return self.commit(store=store)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contender, store) for store in (self.store, other)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.store.read_head(self.project)["revision"], 2)

    def test_concurrent_create_is_one_project(self):
        barrier = threading.Barrier(2)
        with ProjectStore(self.runtime) as other:
            def contender(store):
                barrier.wait(timeout=5)
                return store.create_project(
                    "new-owner", title="New", request_id="new-create", payload=workspace())
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(contender, store) for store in (self.store, other)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.store.list_projects("new-owner")), 1)

    def test_full_workspace_recovered_after_close(self):
        receipt = self.commit(payload={"doc": '{"v":2}', "undo": ['{"v":1}'],
                                     "redo": ['{"v":3}'], "standard": "ansi"})
        self.store.close()
        with ProjectStore(self.runtime) as reopened:
            self.assertEqual(reopened.read_head(self.project)["snapshot"],
                             {"doc": '{"v":2}', "undo": ['{"v":1}'],
                              "redo": ['{"v":3}'], "standard": "ansi"})
            self.assertEqual(reopened.read_receipt(
                self.project, "edit-1", request_payload=request(self.head)), receipt)

    def test_sqlite_commit_failure_does_not_ack_or_publish(self):
        failing = DenyCommitStore.__new__(DenyCommitStore)
        failing.__dict__.update(self.store.__dict__)
        with self.assertRaises(StorageFailure) as error:
            self.commit(store=failing)
        self.assertIsInstance(error.exception.__cause__, sqlite3.DatabaseError)
        with ProjectStore(self.runtime) as reopened:
            self.assertEqual(reopened.read_head(self.project), self.head)
            self.assertIsNone(reopened.read_receipt(
                self.project, "edit-1", request_payload=request(self.head)))
            with reopened._connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)

    def test_real_readonly_sqlite_write_failure_no_confirmation(self):
        class ReadonlyStore(ProjectStore):
            @contextmanager
            def _connection(inner):
                with super(ReadonlyStore, inner)._connection() as conn:
                    conn.execute("PRAGMA query_only=ON")
                    yield conn
        failing = ReadonlyStore.__new__(ReadonlyStore)
        failing.__dict__.update(self.store.__dict__)
        with self.assertRaises(StorageFailure) as error:
            self.commit(store=failing)
        self.assertIn("readonly", str(error.exception).lower())
        self.assertEqual(self.store.read_head(self.project), self.head)

    def test_sync_failure_initialization_not_acknowledged(self):
        root = self.runtime / "new-store"
        with patch.object(module.os, "fsync", side_effect=OSError("fixture sync failure")):
            with self.assertRaises(StorageFailure) as error:
                ProjectStore(root)
        self.assertIsInstance(error.exception.__cause__, OSError)
        self.assertIn("fixture sync failure", str(error.exception))
        self.assertFalse((root / "projects.sqlite3").exists())
        with ProjectStore(root) as healthy:
            self.assertEqual(healthy.list_projects("alice"), [])

    def test_crash_after_commit_lost_response_reopen_replay(self):
        process = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--crash-mode",
             "after", "--runtime-root", str(self.runtime), "--project", self.project],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 73, process.stderr)
        self.assertEqual(process.stdout, "")
        with ProjectStore(self.runtime) as reopened:
            receipt = reopened.read_receipt(
                self.project, "crash-edit", request_payload=request(self.head))
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["response"], {"ok": True, "crash": "response-lost"})
            self.assertEqual(reopened.read_head(self.project)["revision"], 2)
            self.assertEqual(self.commit(
                store=reopened, request_id="crash-edit", response={}), receipt)

    def test_crash_before_commit_rolls_back_whole_workspace(self):
        process = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--crash-mode",
             "before", "--runtime-root", str(self.runtime), "--project", self.project],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 74, process.stderr)
        with ProjectStore(self.runtime) as reopened:
            self.assertEqual(reopened.read_head(self.project), self.head)
            self.assertIsNone(reopened.read_receipt(
                self.project, "crash-edit", request_payload=request(self.head)))
            with reopened._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)

    def test_bool_revision_and_request_base_mismatch(self):
        for revision in (True, False, 0, -1, 1.0, "1", 2**63):
            with self.subTest(revision=revision), self.assertRaises(InvalidPayload):
                self.store.commit(self.project, expected_revision=revision,
                    expected_sha=self.head["sha256"], request_id="invalid",
                    request_payload=request(self.head), payload=workspace(), response={})
        with self.assertRaises(InvalidPayload):
            self.store.commit(self.project, expected_revision=1,
                expected_sha=self.head["sha256"], request_id="invalid",
                request_payload=request(self.head, expected_revision=2),
                payload=workspace(), response={})
        self.assertEqual(self.store.read_head(self.project), self.head)

    def test_invalid_workspace_no_partial_commit(self):
        bad = [None, {}, dict(workspace(), extra=1), dict(workspace(), doc=""),
               dict(workspace(), doc="[]"), dict(workspace(), undo=[False]),
               dict(workspace(), redo="[]"), dict(workspace(), standard=""),
               dict(workspace(), doc='{"x":NaN}'),
               dict(workspace(), doc='{"x":1,"x":2}'),
               dict(workspace(), doc='{"x":1e999}')]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(InvalidPayload):
                self.commit(payload=value)
        self.assertEqual(self.store.read_head(self.project), self.head)
        self.assertIsNone(self.store.read_receipt(
            self.project, "edit-1", request_payload=request(self.head)))

    def test_serialized_workspace_canonicalization(self):
        serialized = json.dumps(workspace(), indent=2)
        duplicate = self.store.create_project(
            "alice", title="Part", request_id="create-1", payload=serialized)
        self.assertEqual(duplicate, self.head)
        self.commit(payload=json.dumps(workspace(2), indent=4))
        self.assertEqual(self.store.read_head(self.project)["snapshot"], workspace(2))

    def test_nonfinite_response_and_request_rejected(self):
        for response in ([], {"v": float("nan")}, {"v": float("inf")}, {1: "key"}, {"v": object()}):
            with self.subTest(response=str(response)), self.assertRaises(InvalidPayload):
                self.commit(response=response)
        with self.assertRaises(InvalidPayload):
            self.store.read_receipt(
                self.project, "none", request_payload=request(self.head, extra=float("inf")))
        self.assertEqual(self.store.read_head(self.project), self.head)

    def test_missing_project_and_history_fail_loud(self):
        with self.assertRaises(ProjectNotFound):
            self.store.read_head("missing")
        with self.assertRaises(ProjectNotFound):
            self.store.read_snapshot(self.project, 2)
        with self.assertRaises(ProjectNotFound):
            self.store.commit("missing", expected_revision=1, expected_sha=self.head["sha256"],
                request_id="none", request_payload=request(self.head), payload=workspace(), response={})
        self.assertIsNone(self.store.read_receipt(
            self.project, "none", request_payload=request(self.head)))

    def test_history_and_receipts_cannot_be_overwritten(self):
        self.commit()
        with self.store._connection() as conn:
            for statement in (
                "UPDATE snapshots SET payload='{}'", "DELETE FROM snapshots",
                "UPDATE receipts SET response='{}'", "DELETE FROM receipts",
                "UPDATE create_receipts SET response='{}'", "DELETE FROM create_receipts",
                "UPDATE projects SET head_revision=1",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
        self.assertEqual(self.store.read_head(self.project)["revision"], 2)

    def test_corrupt_snapshot_is_not_returned(self):
        with self.store._connection() as conn:
            conn.execute("DROP TRIGGER immutable_snapshots_update")
            conn.execute("UPDATE snapshots SET payload=?", (module._canonical(workspace(5)),))
        with self.assertRaises(StoreCorrupt):
            self.store.read_head(self.project)
        with self.assertRaises(StoreCorrupt):
            self.store.create_project("alice", title="Part", request_id="create-1", payload=workspace())

    def test_corrupt_receipt_response_is_not_replayed(self):
        self.commit()
        with self.store._connection() as conn:
            conn.execute("DROP TRIGGER immutable_receipts_update")
            conn.execute("UPDATE receipts SET response='{}'")
        with self.assertRaises(StoreCorrupt):
            self.store.read_receipt(self.project, "edit-1", request_payload=request(self.head))

    def test_linked_root_and_database_rejected_without_overwrite(self):
        linked = self.runtime.parent / (self.runtime.name + "-link")
        linked.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises(InvalidPayload):
            ProjectStore(linked)
        target = self.runtime / "not-a-db"
        target.write_bytes(b"unchanged")
        root = self.runtime / "linked-db"
        root.mkdir()
        (root / "projects.sqlite3").symlink_to(target)
        with self.assertRaises(InvalidPayload):
            ProjectStore(root)
        self.assertEqual(target.read_bytes(), b"unchanged")

    def test_network_and_repository_roots_rejected(self):
        for root in ("/hf3fs-jd/data/tmp/forbidden-durable", ROOT / "forbidden-runtime",
                     "/dev/shm/forbidden-durable"):
            with self.subTest(root=str(root)), self.assertRaises(InvalidPayload):
                ProjectStore(root)

    def test_unrelated_or_unknown_schema_rejected(self):
        root = self.runtime / "foreign"
        root.mkdir()
        with sqlite3.connect(root / "projects.sqlite3") as conn:
            conn.execute("CREATE TABLE foreign_data (data TEXT)")
        with self.assertRaises(StoreCorrupt):
            ProjectStore(root)
        with self.store._connection() as conn:
            conn.execute("PRAGMA user_version=999")
        with self.assertRaises(StoreCorrupt):
            ProjectStore(self.runtime)

    def test_closed_store_rejected(self):
        self.store.close()
        with self.assertRaises(StorageFailure):
            self.store.read_head(self.project)


def _write(path, value):
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    global RUNTIME_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT)
    parser.add_argument("--crash-mode", choices=("before", "after"))
    parser.add_argument("--project")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    RUNTIME_ROOT = args.runtime_root.resolve()
    if RUNTIME_ROOT.is_relative_to(ROOT):
        parser.error("runtime fixture root must be external")
    if args.crash_mode:
        store = (CrashBeforeCommitStore if args.crash_mode == "before" else ProjectStore)(RUNTIME_ROOT)
        if isinstance(store, CrashBeforeCommitStore):
            store.crash_enabled = True
        head = store.read_head(args.project)
        store.commit(args.project, expected_revision=head["revision"], expected_sha=head["sha256"],
                     request_id="crash-edit", request_payload=request(head), payload=workspace(2),
                     response={"ok": True, "crash": "response-lost"})
        os._exit(73)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(StoreTests)
    discovered = suite.countTestCases()
    if args.evidence is None:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    out = args.evidence.resolve()
    if out.is_relative_to(ROOT) or out.is_relative_to(RUNTIME_ROOT):
        parser.error("evidence must be external and separate from runtime")
    out.mkdir(parents=True, exist_ok=False)
    started, wall = datetime.now(timezone.utc).isoformat(), time.monotonic()
    sources = [APP / "project_store.py", Path(__file__).resolve(),
               ROOT / "oss-alternatives/docs/design/project-store.md"]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sources}
    with (out / "tests.log").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        after = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sources}
        drift = sorted(name for name in hashes if hashes[name] != after[name])
        passed = (result.wasSuccessful() and result.testsRun == discovered and discovered > 0
                  and not result.skipped and not drift)
        finished = datetime.now(timezone.utc).isoformat()
        line = (f"PROJECT_STORE verdict={'PASS' if passed else 'FAIL'} tests={result.testsRun} "
                f"failures={len(result.failures)} errors={len(result.errors)} "
                f"skip={len(result.skipped)} collected_at={finished}")
        log.write(line + "\n")
        log.flush()
        os.fsync(log.fileno())
    report = {
        "verdict": "PASS" if passed else "FAIL", "started_at": started, "finished_at": finished,
        "wall_s": time.monotonic() - wall, "tests": result.testsRun,
        "discovered": discovered,
        "passed": result.testsRun - len(result.errors) - len(result.failures) - len(result.skipped),
        "failures": len(result.failures), "errors": len(result.errors),
        "skip": len(result.skipped), "source_sha256": hashes, "source_drift": drift,
        "runtime_root": str(RUNTIME_ROOT), "log_sha256": hashlib.sha256(
            (out / "tests.log").read_bytes()).hexdigest(),
        "production": "NOT_ASSESSED", "power_loss_hardware": "NOT_ASSESSED",
    }
    _write(out / "result.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(line)
    print(f"evidence={out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
