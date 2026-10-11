#!/usr/bin/env python3
"""Real local SQLite recovery tests. Retains all fixtures and external evidence."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import service_recovery as R

ROOT = OUT = None
SERVICE_SCOPE_RUNTIME = None
OBSERVATIONS = []


def workspace(label):
    return {"doc": json.dumps({"label": label}, sort_keys=True),
            "undo": [], "redo": [], "standard": "ISO"}


def transition(store, head, request_id, label):
    request = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
               "request_id": request_id, "route": "/api/save"}
    receipt = store.commit(
        head["project_id"], expected_revision=head["revision"],
        expected_sha=head["sha256"], request_id=request_id, request_payload=request,
        payload=workspace(label), response={"ok": True, "saved": True})
    return receipt, request


def writer(root, ready, release, done, errors, *, hold=False, hold_after_project=False):
    """Separate process uses the same auth->project order as the gateway guard."""
    try:
        with ExitStack() as stack:
            a = sqlite3.connect(root / "accounts/opencad-auth.sqlite3",
                                isolation_level=None, timeout=5)
            stack.callback(a.close)
            p = sqlite3.connect(root / "projects/projects.sqlite3",
                                isolation_level=None, timeout=5)
            stack.callback(p.close)
            if not hold and not hold_after_project:
                ready.set()
            a.execute("BEGIN IMMEDIATE")
            p.execute("BEGIN IMMEDIATE")
            a.execute("UPDATE users SET locale='fr'")
            a.execute("INSERT INTO users (user_id,username,kind,admin,enabled) "
                      "VALUES ('competing-external','competing-ext','external',0,1)")
            pid, at = "competing-project", R.now()
            payload = R.STORE._workspace(workspace("concurrent"))
            p.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)",
                      (pid, "competing-external", "Concurrent", 1, at, at))
            p.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                      (pid, 1, R.STORE._digest(payload), payload, at))
            p.row_factory = sqlite3.Row
            response = R.STORE._canonical(R.STORE.ProjectStore._head(p, pid))
            request = R.STORE._canonical(
                {"title": "Concurrent", "payload": R.STORE._loads(payload)})
            p.execute("INSERT INTO create_receipts VALUES (?,?,?,?,?,?,?)",
                      ("competing-external", "concurrent-create", R.STORE._digest(request),
                       request, pid, response, R.STORE._digest(response)))
            if hold:
                ready.set()
                if not release.wait(10):
                    raise RuntimeError("fixture release missing")
            p.commit()
            if hold_after_project:
                ready.set()
                if not release.wait(10):
                    raise RuntimeError("fixture release missing")
            a.commit()
            done.set()
    except BaseException as exc:
        errors.put(type(exc).__name__)


class RecoveryTests(unittest.TestCase):
    @property
    def fixture_parent(self):
        return ROOT

    def setUp(self):
        self.root = self.fixture_parent / self._testMethodName
        self.root.mkdir(mode=0o700)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        self.auth = R.AUTH.AuthController(self.source / "accounts", allow_registration=True)
        self.password = secrets.token_hex(16)
        self.user = self.auth.register("recovery-test", self.password)["user_id"]
        self.ticket = self.auth.login("recovery-test", self.password)
        self.store = R.STORE.ProjectStore(self.source / "projects")
        self.initial = self.store.create_project(self.user, title="First",
                                                request_id="create-one", payload=workspace("one"))
        self.receipt, self.request = transition(self.store, self.initial, "save-one", "two")
        self.package = self.root / "package"
        self.target = self.root / "restored"
        self.keep = []
        for path in (self.auth.path, self.store.db_path):
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("BEGIN")
            db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            self.keep.append(db)
        # Commit extra content while keepers prevent WAL deletion/checkpoint.
        self.auth.set_preferences(self.user, locale="de")
        self.receipt2, self.request2 = transition(
            self.store, self.store.read_head(self.initial["project_id"]), "save-two", "three")
        self.evidence = R.Evidence(OUT / self._testMethodName)
        self.addCleanup(self.evidence.close)
        self.addCleanup(self.auth.close)
        self.addCleanup(self.store.close)
        self.addCleanup(lambda: [db.close() for db in self.keep])

    def physical(self):
        paths = [self.auth.path, self.store.db_path]
        result = {}
        for path in paths:
            for suffix in ("", "-wal"):
                candidate = Path(str(path) + suffix)
                if candidate.exists():
                    result[str(candidate)] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return result

    def backup(self, **kwargs):
        return R.backup(self.source, self.package, self.evidence, **kwargs)

    def mutate_package(self, kind, sql, parameters=()):
        """Corrupt test-owned bytes then rebind envelope to test semantic checks."""
        manifest = json.loads((self.package / "manifest.json").read_bytes())
        path = self.package / R.FILES[kind]
        with sqlite3.connect(path) as db:
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'"):
                db.execute(f'DROP TRIGGER "{name}"')
            db.execute(sql, parameters)
            # Restore exact schema, so the content check (not missing triggers) fails.
            if kind == "projects":
                for statement in R.STORE._DDL:
                    if statement.startswith("CREATE TRIGGER"):
                        db.execute(statement)
            for table in ("snapshots", "receipts", "create_receipts"):
                for action in ("UPDATE", "DELETE"):
                    if kind == "projects":
                        db.execute(
                            f"CREATE TRIGGER immutable_{table}_{action.lower()} "
                            f"BEFORE {action} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'immutable history'); END")
        data = path.read_bytes()
        manifest["databases"][kind]["sha256"] = R.digest(data)
        manifest["databases"][kind]["bytes"] = len(data)
        # Authorized mutation of our disposable negative-control fixture only.
        with (self.package / "manifest.json").open("wb") as stream:
            stream.write(R.canonical(manifest))

    def rejects(self, code, function, *args, **kwargs):
        with self.assertRaises(R.RecoveryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        OBSERVATIONS.append({"test": self._testMethodName, "rejection": code})

    def test_wal_backup_source_zero_drift(self):
        before = self.physical()
        self.assertGreater(Path(str(self.store.db_path) + "-wal").stat().st_size, 0)
        manifest = self.backup()
        self.assertEqual(self.physical(), before)
        self.assertEqual(manifest["validation"]["databases"]["projects"]["counts"]["snapshots"], 3)
        self.assertEqual(R.verify(self.package, self.evidence), manifest)
        self.assertEqual(self.physical(), before)
        OBSERVATIONS.append({"test": self._testMethodName, "physical_source_drift": [],
                             "lock_order": manifest["lock_order"]})

    def test_restore_reopen_revoke_and_idempotency(self):
        manifest = self.backup()
        self.auth.logout(self.ticket.token)
        original = {name: R.digest((self.package / name).read_bytes())
                    for name in os.listdir(self.package)}
        restored = R.restore(self.package, self.target, self.evidence)
        self.assertTrue(restored["complete"])
        a = R.AUTH.AuthController(self.target / "accounts", allow_registration=True)
        s = R.STORE.ProjectStore(self.target / "projects")
        self.addCleanup(a.close)
        self.addCleanup(s.close)
        with self.assertRaises(R.AUTH.AuthError) as old:
            a.authenticate(self.ticket.token)
        self.assertEqual(old.exception.code, "revoked_token")
        new = a.login("recovery-test", self.password)
        self.assertEqual(a.authenticate(new.token).user_id, self.user)
        current = s.read_head(self.initial["project_id"])
        self.assertEqual(current, self.store.read_head(self.initial["project_id"]))
        replay = s.commit(
            self.initial["project_id"], expected_revision=self.initial["revision"],
            expected_sha=self.initial["sha256"], request_id="save-one",
            request_payload=self.request, payload=workspace("ignored"),
            response={"different": True})
        self.assertEqual(replay, self.receipt)
        created = s.create_project(self.user, title="First", request_id="create-one",
                                   payload=workspace("one"))
        self.assertEqual(created, self.initial)
        self.assertEqual(s.read_head(current["project_id"]), current)
        self.assertEqual(original, {name: R.digest((self.package / name).read_bytes())
                                    for name in os.listdir(self.package)})
        self.assertEqual(restored["validation"]["databases"]["projects"],
                         manifest["validation"]["databases"]["projects"])

    def test_restore_preserves_disabled_and_revoked_accounts(self):
        with self.auth._db() as a:
            a.execute("UPDATE users SET enabled=0 WHERE user_id=?", (self.user,))
            a.execute("UPDATE sessions SET revoked=1")
        self.backup()
        R.restore(self.package, self.target, self.evidence)
        with sqlite3.connect(self.target / "accounts/opencad-auth.sqlite3") as a:
            self.assertEqual(a.execute("SELECT enabled FROM users").fetchone()[0], 0)
            self.assertEqual(a.execute("SELECT COUNT(*) FROM sessions WHERE revoked<>1")
                             .fetchone()[0], 0)

    def process(self, hold=False, hold_after_project=False):
        ctx = multiprocessing.get_context("spawn")
        ready, release, done, errors = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
        process = ctx.Process(target=writer,
                              args=(self.source, ready, release, done, errors),
                              kwargs={"hold": hold, "hold_after_project": hold_after_project})
        process.start()
        self.addCleanup(lambda: process.join(10))
        self.assertTrue(ready.wait(5))
        return process, release, done, errors

    def test_competing_process_blocked_by_both_locks(self):
        children = []

        def barrier(phase):
            if phase == "both_locked":
                p, release, done, errors = self.process()
                children.append((p, done, errors))
                self.assertFalse(done.wait(0.2), "writer crossed held auth lock")
        manifest = self.backup(checkpoint=barrier)
        process, done, errors = children[0]
        self.assertTrue(done.wait(5))
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(errors.empty())
        self.assertEqual(self.auth.get_preferences(self.user)["locale"], "fr")
        self.assertEqual(manifest["lock_order"], R.LOCK_ORDER)
        with sqlite3.connect(self.package / R.FILES["accounts"]) as a:
            self.assertEqual(a.execute("SELECT locale FROM users").fetchone()[0], "de")
            self.assertEqual(a.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        with sqlite3.connect(self.package / R.FILES["projects"]) as p:
            self.assertEqual(p.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
        OBSERVATIONS.append({"test": self._testMethodName, "child_rc": process.exitcode,
                             "writer_blocked_seconds": 0.2})

    def test_existing_writer_timeout_preserves_source_and_no_package(self):
        process, release, done, errors = self.process(hold=True)
        before = self.physical()
        with self.assertRaises(sqlite3.OperationalError):
            self.backup()
        self.assertFalse(self.package.exists())
        self.assertEqual(self.physical(), before)
        release.set()
        self.assertTrue(done.wait(5))
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(errors.empty())
        OBSERVATIONS.append({"test": self._testMethodName, "child_rc": 0,
                             "first_cause": "OperationalError"})

    def test_project_commit_auth_gap_backup_waits_for_consistent_pair(self):
        writer_process, release, done, errors = self.process(hold_after_project=True)
        out = OUT / (self._testMethodName + "-backup")
        command = [sys.executable, "-B", str(R.HERE), "--action", "backup",
                   "--source-root", str(self.source), "--package-root", str(self.package),
                   "--out", str(out)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        self.addCleanup(lambda: process.wait(timeout=10))
        time.sleep(0.2)
        self.assertIsNone(process.poll())
        self.assertFalse(self.package.exists(), "backup crossed held auth guard")
        release.set()
        stdout, stderr = process.communicate(timeout=10)
        writer_process.join(5)
        self.assertTrue(done.is_set())
        self.assertEqual(writer_process.exitcode, 0)
        self.assertTrue(errors.empty())
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("SERVICE_RECOVERY verdict=PASS", stdout)
        manifest = R.verify(self.package, self.evidence)
        self.assertEqual(manifest["validation"]["databases"]["accounts"]["counts"]["users"], 2)
        self.assertEqual(manifest["validation"]["databases"]["projects"]["counts"]["projects"], 2)
        OBSERVATIONS.append({"test": self._testMethodName, "writer_actual_rc": 0,
                             "backup_actual_rc": 0, "cross_database_gap_waited": True})

    def test_backup_failure_partial_and_locks_released(self):
        before = self.physical()

        def fail(phase):
            if phase == "accounts_copied":
                raise R.RecoveryError("INJECTED_COPY_FAILURE")
        self.rejects("INJECTED_COPY_FAILURE", self.backup, checkpoint=fail)
        self.assertTrue((self.package / R.FILES["accounts"]).exists())
        self.assertFalse((self.package / "manifest.json").exists())
        self.assertEqual(self.physical(), before)
        self.auth.set_preferences(self.user, locale="en")
        self.rejects("PACKAGE_FILE_SET_FAILED", R.verify, self.package, self.evidence)

    def test_journal_failure_prevents_complete_manifest(self):
        original = self.evidence.event

        def fail(phase, **fields):
            if phase == "manifest_intent":
                raise OSError("fixture evidence failure")
            return original(phase, **fields)
        with patch.object(self.evidence, "event", side_effect=fail):
            with self.assertRaises(OSError):
                self.backup()
        self.assertFalse((self.package / "manifest.json").exists())
        self.assertTrue((self.package / R.FILES["projects"]).exists())

    def test_final_result_failure_preserves_first_cause(self):
        out = OUT / (self._testMethodName + "-execute")
        original = R.Directory.write

        def fail(directory, name, data):
            if name == "result.json":
                raise OSError("fixture result failure")
            return original(directory, name, data)
        with patch.object(R.Directory, "write", new=fail):
            report = R.execute("backup", out, source_root=self.source,
                               package_root=self.source / "overlap")
        self.assertEqual(report["first_cause"]["code"], "OVERLAPPING_ROOTS")
        self.assertEqual(report["rc"], 1)
        self.assertEqual(report["evidence_errors"], ["OSError"])
        self.assertEqual(report["completion_marker"], "SERVICE_RECOVERY verdict=FAIL")
        self.assertTrue(list(out.glob("journal-*.json")))

    def test_result_write_after_success_failure_not_pass_marker(self):
        out = OUT / (self._testMethodName + "-execute")
        original = R.Directory.write

        def fail(directory, name, data):
            result = original(directory, name, data)
            if name == "result.json":
                raise OSError("fixture post-write durability failure")
            return result
        with patch.object(R.Directory, "write", new=fail):
            report = R.execute("backup", out, source_root=self.source,
                               package_root=self.package)
        self.assertEqual(report["rc"], 1)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["completion_marker"], "SERVICE_RECOVERY verdict=FAIL")
        terminal = json.loads((out / "terminal-failure.json").read_bytes())
        self.assertEqual(terminal["rc"], 1)
        self.assertEqual(terminal["first_cause"]["type"], "OSError")

    def test_restore_rollback_failure_keeps_original_commit_cause(self):
        self.backup()
        original = R.Directory.database
        copied = False

        class FailingCommit:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                raise R.RecoveryError("INJECTED_COMMIT_FAILURE")

            def rollback(self):
                self.connection.rollback()
                raise OSError("fixture rollback report failure")

        def database(directory, name, *, readonly):
            connection = original(directory, name, readonly=readonly)
            if copied and directory.path == self.target / "accounts" and not readonly:
                return FailingCommit(connection)
            return connection

        def checkpoint(phase):
            nonlocal copied
            if phase == "restore_copied":
                copied = True
        with patch.object(R.Directory, "database", new=database):
            with self.assertRaises(R.RecoveryError) as caught:
                R.restore(self.package, self.target, self.evidence, checkpoint=checkpoint)
            self.assertEqual(caught.exception.code, "INJECTED_COMMIT_FAILURE")
            self.assertEqual(R.failure_details(caught.exception)["secondary_errors"],
                             ["OSError"])
        self.assertFalse((self.target / "recovery-complete.json").exists())

    def test_restore_failure_keeps_partial_original_and_no_marker(self):
        self.backup()
        before = {name: R.digest((self.package / name).read_bytes())
                  for name in os.listdir(self.package)}

        def fail(phase):
            if phase == "sessions_revoked":
                raise R.RecoveryError("INJECTED_RESTORE_FAILURE")
        self.rejects("INJECTED_RESTORE_FAILURE", R.restore, self.package, self.target,
                     self.evidence, checkpoint=fail)
        self.assertFalse((self.target / "recovery-complete.json").exists())
        self.assertTrue((self.target / "recovery-intent.json").exists())
        self.assertEqual(before, {name: R.digest((self.package / name).read_bytes())
                                  for name in os.listdir(self.package)})
        with sqlite3.connect(self.target / "accounts/opencad-auth.sqlite3") as a:
            self.assertEqual(a.execute("SELECT COUNT(*) FROM sessions WHERE revoked=0")
                             .fetchone()[0], 0)

    def test_corrupt_package_bytes(self):
        self.backup()
        with (self.package / R.FILES["projects"]).open("ab") as stream:
            stream.write(b"corruption")
        self.rejects("PACKAGE_BYTES_SHA_FAILED", R.verify, self.package, self.evidence)

    def test_snapshot_corruption_even_with_rebound_envelope(self):
        self.backup()
        self.mutate_package("projects", "UPDATE snapshots SET sha256=? WHERE revision=1",
                            ("0" * 64,))
        self.rejects("CANONICAL_SHA_MISMATCH", R.verify, self.package, self.evidence)

    def test_receipt_request_corruption(self):
        self.backup()
        self.mutate_package("projects", "UPDATE receipts SET request_sha256=?", ("0" * 64,))
        self.rejects("CANONICAL_SHA_MISMATCH", R.verify, self.package, self.evidence)

    def test_receipt_response_corruption(self):
        self.backup()
        self.mutate_package("projects", "UPDATE receipts SET response_sha256=?", ("0" * 64,))
        self.rejects("CANONICAL_SHA_MISMATCH", R.verify, self.package, self.evidence)

    def test_create_receipt_corruption(self):
        self.backup()
        self.mutate_package("projects", "UPDATE create_receipts SET response_sha256=?",
                            ("0" * 64,))
        self.rejects("CANONICAL_SHA_MISMATCH", R.verify, self.package, self.evidence)

    def test_schema_and_application_identity(self):
        self.backup()
        self.mutate_package("projects", "PRAGMA application_id=1")
        self.rejects("DATABASE_IDENTITY_FAILED", R.verify, self.package, self.evidence)

    def test_auth_product_corruption(self):
        self.backup()
        self.mutate_package("accounts", "UPDATE auth_meta SET value='other'")
        self.rejects("AUTH_PRODUCT_FAILED", R.verify, self.package, self.evidence)

    def test_extra_schema_object_rejected(self):
        self.backup()
        self.mutate_package("projects", "CREATE TABLE alien(value TEXT)")
        self.rejects("SCHEMA_CONTRACT_FAILED", R.verify, self.package, self.evidence)

    def test_receipt_noncanonical_bytes_with_correct_sha(self):
        self.backup()
        text = json.dumps(self.request, sort_keys=True)
        self.mutate_package(
            "projects", "UPDATE receipts SET request_payload=?,request_sha256=? "
            "WHERE request_id='save-one'", (text, R.STORE._digest(text)))
        self.rejects("NON_CANONICAL_JSON", R.verify, self.package, self.evidence)

    def test_receipt_base_corruption_with_correct_sha(self):
        self.backup()
        text = R.STORE._canonical({**self.request, "expected_sha": "0" * 64})
        self.mutate_package(
            "projects", "UPDATE receipts SET request_payload=?,request_sha256=? "
            "WHERE request_id='save-one'", (text, R.STORE._digest(text)))
        self.rejects("RECEIPT_BASE_MISMATCH", R.verify, self.package, self.evidence)

    def test_create_owner_mapping_corruption(self):
        self.backup()
        self.mutate_package("projects", "UPDATE create_receipts SET owner='wrong'")
        self.rejects("CREATE_OWNER_MAPPING_FAILED", R.verify, self.package, self.evidence)

    def test_invalid_external_verifier_mapping(self):
        with self.auth._db() as a:
            a.execute("INSERT INTO users (user_id,username,kind,salt,admin,enabled) "
                      "VALUES ('bad-external','bad-ext','external',?,0,1)", (bytes(16),))
        self.rejects("INVALID_EXTERNAL_MAPPING", self.backup)
        self.assertFalse(self.package.exists())

    def test_auth_schema_literal_cannot_be_normalized_away(self):
        self.assertNotEqual(R.sql_contract("CHECK(kind IN ('local','external'))"),
                            R.sql_contract("CHECK(kind IN ('lo cal','external'))"))
        self.assertNotEqual(R.sql_contract("CHECK(kind IN ('local','external'))"),
                            R.sql_contract("CHECK(kind IN ('LOCAL','external'))"))

    def test_foreign_key_corruption(self):
        self.backup()
        self.mutate_package("projects", "UPDATE receipts SET project_id='absent'")
        self.rejects("FOREIGN_KEY_FAILED", R.verify, self.package, self.evidence)

    def test_missing_owner_mapping_rejected_before_write(self):
        orphan = self.store.create_project("foreign-account", title="Orphan",
                                          request_id="orphan", payload=workspace("orphan"))
        self.assertEqual(orphan["owner"], "foreign-account")
        self.rejects("OWNER_ACCOUNT_MAPPING_FAILED", self.backup)
        self.assertFalse(self.package.exists())

    def test_external_account_mapping_preserved(self):
        with self.auth._db() as a:
            a.execute("INSERT INTO users (user_id,username,kind,admin,enabled) "
                      "VALUES ('external-provider-subject','ext-test','external',0,1)")
        self.store.create_project("external-provider-subject", title="External",
                                  request_id="ext-create", payload=workspace("external"))
        manifest = self.backup()
        self.assertEqual(manifest["validation"]["external_accounts"], 1)
        R.restore(self.package, self.target, self.evidence)
        with sqlite3.connect(self.target / "accounts/opencad-auth.sqlite3") as a:
            self.assertEqual(a.execute("SELECT user_id,kind FROM users WHERE kind='external'")
                             .fetchone(), ("external-provider-subject", "external"))

    def test_refuse_existing_overlapping_and_network_roots(self):
        self.rejects("OVERLAPPING_ROOTS", R.backup, self.source,
                     self.source / "package", self.evidence)
        self.rejects("OUTSIDE_LOCAL_FIXTURE_SCOPE", R.backup,
                     Path("/data/dev/opencad"), self.package, self.evidence)
        self.rejects("OUTSIDE_LOCAL_FIXTURE_SCOPE", R.restore,
                     Path("/hf3fs-jd/data/tmp/network"), self.target, self.evidence)
        self.backup()
        self.rejects("PACKAGE_ALREADY_EXISTS", self.backup)
        self.target.mkdir(mode=0o700)
        self.rejects("TARGET_ALREADY_EXISTS", R.restore,
                     self.package, self.target, self.evidence)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_refuse_symlink_hardlink_and_dotdot(self):
        linked = self.root / "linked"
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(R.STORE.InvalidPayload):
            R.backup(linked, self.package, self.evidence)
        self.rejects("INVALID_ABSOLUTE_PATH", R.local_path, self.root / ".." / "x")
        hard = self.root / "hard"
        os.link(self.store.db_path, hard)
        self.rejects("LINKED_OR_NON_REGULAR_DATABASE", self.backup)

    def test_directory_replacement_race_detected(self):
        def race(phase):
            if phase == "accounts_copied":
                moved = self.root / "moved-source"
                self.source.rename(moved)
                self.source.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.backup(checkpoint=race)
        self.assertFalse((self.package / "manifest.json").exists())

    def test_source_database_replacement_race_detected(self):
        def race(phase):
            if phase == "accounts_copied":
                self.auth.path.rename(self.auth.path.with_suffix(".moved"))
                self.auth.path.write_bytes(b"replacement")
        self.rejects("DATABASE_PATH_DRIFT", self.backup, checkpoint=race)
        self.assertFalse((self.package / "manifest.json").exists())
        self.assertEqual(self.auth.path.read_bytes(), b"replacement")

    def test_reject_target_nested_under_other_service(self):
        other = self.root / "other-service"
        a = R.AUTH.AuthController(other / "accounts")
        self.addCleanup(a.close)
        self.rejects("NESTED_SERVICE_ROOT", R.local_path, other / "nested")
        self.assertFalse((other / "nested").exists())

    def test_history_gap_and_create_request_corruption(self):
        self.backup()
        self.mutate_package("projects", "DELETE FROM snapshots WHERE revision=2")
        self.rejects("FOREIGN_KEY_FAILED", R.verify, self.package, self.evidence)

    def test_create_request_semantic_corruption_with_correct_sha(self):
        self.backup()
        text = R.STORE._canonical({"title": "Wrong", "payload": workspace("one")})
        self.mutate_package("projects", "UPDATE create_receipts SET "
                            "request_payload=?,request_sha256=?",
                            (text, R.STORE._digest(text)))
        self.rejects("CREATE_REQUEST_MISMATCH", R.verify, self.package, self.evidence)

    def test_manifest_and_extra_file_rejected(self):
        self.backup()
        (self.package / "extra").write_bytes(b"extra")
        self.rejects("PACKAGE_FILE_SET_FAILED", R.verify, self.package, self.evidence)

    def test_cli_actual_exit_and_result(self):
        command = [sys.executable, "-B", str(R.HERE), "--action", "backup",
                   "--source-root", str(self.source), "--package-root", str(self.package),
                   "--out", str(OUT / (self._testMethodName + "-cli-backup"))]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("SERVICE_RECOVERY verdict=PASS rc=0", completed.stdout)
        result = json.loads((OUT / (self._testMethodName + "-cli-backup/result.json"))
                            .read_bytes())
        self.assertEqual(result["rc"], completed.returncode)
        self.assertEqual(result["source_drift"], [])
        bad_command = [sys.executable, "-B", str(R.HERE), "--action", "restore",
                       "--package-root", str(self.package), "--target-root", str(self.source),
                       "--out", str(OUT / (self._testMethodName + "-cli-refuse"))]
        refused = subprocess.run(bad_command, text=True, capture_output=True, timeout=30)
        self.assertEqual(refused.returncode, 1)
        failure = json.loads((OUT / (self._testMethodName + "-cli-refuse/result.json"))
                             .read_bytes())
        self.assertEqual(failure["first_cause"]["code"], "TARGET_ALREADY_EXISTS")
        OBSERVATIONS.append({"test": self._testMethodName,
                             "backup_actual_rc": completed.returncode,
                             "refused_actual_rc": refused.returncode})

    def test_cli_verify_restore_original_exits(self):
        self.backup()
        for action in ("verify", "restore"):
            out = OUT / (self._testMethodName + "-" + action)
            command = [sys.executable, "-B", str(R.HERE), "--action", action,
                       "--package-root", str(self.package), "--out", str(out)]
            if action == "restore":
                command += ["--target-root", str(self.target)]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads((out / "result.json").read_bytes())
            self.assertEqual(result["rc"], 0)
            self.assertEqual(result["completion_marker"], "SERVICE_RECOVERY verdict=PASS")
            self.assertEqual(result["source_drift"], [])
            OBSERVATIONS.append({"test": self._testMethodName, "action": action,
                                 "actual_rc": completed.returncode})


class AuthorizedServiceScopeTests(unittest.TestCase):
    """Exercise public CLI outside the fixture default, without any live service."""

    setUp = RecoveryTests.setUp
    physical = RecoveryTests.physical
    rejects = RecoveryTests.rejects

    @property
    def fixture_parent(self):
        return SERVICE_SCOPE_RUNTIME

    def backup(self, **kwargs):
        return R.backup(self.source, self.package, self.evidence,
                        service_root_authorization=self.root, **kwargs)

    def cli(self, action, suffix, *, authorization=None, target=None):
        out = OUT / (self._testMethodName + "-" + suffix)
        command = [sys.executable, "-B", str(R.HERE), "--action", action,
                   "--package-root", str(self.package), "--out", str(out)]
        if action == "backup":
            command += ["--source-root", str(self.source)]
        if action == "restore":
            command += ["--target-root", str(target or self.target)]
        if authorization is not None:
            command += ["--service-root-authorization", os.fspath(authorization)]
        completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
        report = json.loads((out / "result.json").read_bytes())
        self.assertEqual(report["rc"], completed.returncode)
        self.assertEqual(report["source_drift"], [])
        R.require(not report["evidence_errors"], "UNEXPECTED_SCOPE_EVIDENCE_FAILURE")
        with R.Directory(out.resolve()) as evidence:
            evidence.write("caller-collection.json", R.canonical({
                "argv": command, "actual_rc": completed.returncode, "collected_at": R.now(),
                "stdout_sha256": R.digest(completed.stdout.encode()),
                "stderr_sha256": R.digest(completed.stderr.encode()),
                "result_sha256": R.digest((out / "result.json").read_bytes()),
            }))
        OBSERVATIONS.append({"test": self._testMethodName, "action": action,
                             "actual_rc": completed.returncode,
                             "authorization": os.fspath(authorization)
                             if authorization is not None else None})
        return completed, report

    def test_authorized_cli_backup_verify_restore_reopen_no_source_drift(self):
        self.maxDiff = None
        before = self.physical()
        completed, backup = self.cli("backup", "backup", authorization=self.root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        after = self.physical()
        self.evidence.event("source_physical_after_cli_backup", before=before, after=after)
        self.assertEqual(after, before)
        self.assertEqual(backup["authorization_parent"], str(self.root))
        self.assertEqual(backup["artifact"]["authorization_mode"],
                         "explicit_operator_local_parent")
        original = {name: R.digest((self.package / name).read_bytes())
                    for name in os.listdir(self.package)}
        completed, verified = self.cli("verify", "verify", authorization=self.root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(verified["artifact"], backup["artifact"])
        completed, restored = self.cli("restore", "restore", authorization=self.root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(restored["artifact"]["entitlement_reconciliation"],
                         "REQUIRED_BEFORE_SERVICE_ADOPTION")
        self.assertEqual(original, {name: R.digest((self.package / name).read_bytes())
                                    for name in os.listdir(self.package)})
        self.assertEqual(self.physical(), before)
        with R.STORE.ProjectStore(self.target / "projects") as store:
            self.assertEqual(store.read_head(self.initial["project_id"]),
                             self.store.read_head(self.initial["project_id"]))
            self.assertEqual(store.read_receipt(self.initial["project_id"], "save-two",
                                              request_payload=self.request2), self.receipt2)
        auth = R.AUTH.AuthController(self.target / "accounts")
        self.addCleanup(auth.close)
        with self.assertRaises(R.AUTH.AuthError) as old:
            auth.authenticate(self.ticket.token)
        self.assertEqual(old.exception.code, "revoked_token")
        OBSERVATIONS.append({"test": self._testMethodName, "source_physical_drift": [],
                             "original_package_drift": [], "service_started": False})

    def test_scope_does_not_inherit_from_package_manifest(self):
        self.backup()
        for action in ("verify", "restore"):
            completed, failure = self.cli(action, "default-" + action)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(failure["first_cause"]["code"], "OUTSIDE_LOCAL_FIXTURE_SCOPE")
        self.assertFalse(self.target.exists())

    def test_cli_empty_scope_is_not_default_authorization(self):
        before = self.physical()
        for index, authorization in enumerate(("", "   ")):
            completed, failure = self.cli("backup", "empty-" + str(index),
                                          authorization=authorization)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(failure["first_cause"]["code"], "EMPTY_SERVICE_ROOT_AUTHORIZATION")
        self.assertEqual(before, self.physical())
        self.assertFalse(self.package.exists())

    def test_cli_scope_outside_paths_refused_without_writes(self):
        wrong = self.root / "wrong-parent"
        wrong.mkdir(mode=0o700)
        before = self.physical()
        completed, failure = self.cli("backup", "outside", authorization=wrong)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(failure["first_cause"]["code"], "OUTSIDE_AUTHORIZED_SERVICE_SCOPE")
        self.assertEqual(before, self.physical())
        self.assertFalse(self.package.exists())
        self.assertEqual(list(wrong.iterdir()), [])

    def test_authorized_restore_refuses_live_or_existing_target(self):
        self.backup()
        before = self.physical()
        completed, failure = self.cli("restore", "live-source",
                                      authorization=self.root, target=self.source)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(failure["first_cause"]["code"], "TARGET_ALREADY_EXISTS")
        self.assertEqual(self.physical(), before)
        self.target.mkdir(mode=0o700)
        sentinel = self.target / "sentinel"
        sentinel.write_bytes(b"owned-existing-target")
        completed, failure = self.cli("restore", "existing", authorization=self.root)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(failure["first_cause"]["code"], "TARGET_ALREADY_EXISTS")
        self.assertEqual(sentinel.read_bytes(), b"owned-existing-target")
        self.assertEqual(list(self.target.iterdir()), [sentinel])

    def test_authorization_refuses_repo_network_symlink_broad_missing(self):
        for broad in (Path("/"), Path("/data"), Path("/data/tmp")):
            self.rejects("SERVICE_ROOT_AUTHORIZATION_TOO_BROAD",
                         R.service_parent_authorization, broad)
        self.rejects("SERVICE_ROOT_AUTHORIZATION_PARENT_MISSING",
                     R.service_parent_authorization, self.root / "absent")
        with self.assertRaises(R.STORE.InvalidPayload):
            R.service_parent_authorization(R.HERE.parent.parent)
        with self.assertRaises(R.STORE.InvalidPayload):
            R.service_parent_authorization(Path("/hf3fs-jd/data/tmp"))
        linked = self.root / "linked-parent"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(R.STORE.InvalidPayload):
            R.service_parent_authorization(linked)
        with self.assertRaises(R.STORE.InvalidPayload):
            R.local_path(linked / "target", service_root_authorization=self.root)
        self.rejects("INVALID_ABSOLUTE_PATH", R.service_parent_authorization, "relative")
        self.rejects("INVALID_ABSOLUTE_PATH", R.service_parent_authorization,
                     self.root / ".." / "other")
        self.assertFalse(self.target.exists())

    def test_authorization_parent_and_target_cannot_nest_service(self):
        self.rejects("NESTED_SERVICE_ROOT", R.service_parent_authorization, self.source)
        self.rejects("NESTED_SERVICE_ROOT", R.local_path, self.source / "nested",
                     service_root_authorization=self.root)
        self.backup()
        other = self.root / "other-service"
        auth = R.AUTH.AuthController(other / "accounts")
        self.addCleanup(auth.close)
        self.rejects("NESTED_SERVICE_ROOT", R.restore, self.package, other / "nested",
                     self.evidence, service_root_authorization=self.root)
        self.assertFalse((other / "nested").exists())

    def test_authorized_copy_failure_partial_no_overwrite(self):
        before = self.physical()

        def fail(phase):
            if phase == "accounts_copied":
                raise R.RecoveryError("INJECTED_AUTHORIZED_COPY_FAILURE")
        self.rejects("INJECTED_AUTHORIZED_COPY_FAILURE", self.backup, checkpoint=fail)
        self.assertEqual(self.physical(), before)
        partial = {name: R.digest((self.package / name).read_bytes())
                   for name in os.listdir(self.package)}
        self.assertFalse((self.package / "manifest.json").exists())
        self.rejects("PACKAGE_ALREADY_EXISTS", self.backup)
        self.assertEqual(partial, {name: R.digest((self.package / name).read_bytes())
                                   for name in os.listdir(self.package)})

    def test_authorized_restore_failure_partial_no_overwrite(self):
        self.backup()
        before = self.physical()
        original = {name: R.digest((self.package / name).read_bytes())
                    for name in os.listdir(self.package)}

        def fail(phase):
            if phase == "sessions_revoked":
                raise R.RecoveryError("INJECTED_AUTHORIZED_RESTORE_FAILURE")
        self.rejects("INJECTED_AUTHORIZED_RESTORE_FAILURE", R.restore,
                     self.package, self.target, self.evidence,
                     checkpoint=fail, service_root_authorization=self.root)
        self.assertFalse((self.target / "recovery-complete.json").exists())
        self.rejects("TARGET_ALREADY_EXISTS", R.restore, self.package,
                     self.target, self.evidence, service_root_authorization=self.root)
        self.assertEqual(original, {name: R.digest((self.package / name).read_bytes())
                                    for name in os.listdir(self.package)})
        self.assertEqual(before, self.physical())
        with sqlite3.connect(self.target / "accounts/opencad-auth.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions WHERE revoked<>1")
                             .fetchone()[0], 0)

    def test_authorized_overlap_and_scope_parent_itself_rejected(self):
        for left, right in ((self.source, self.source / "package"),
                            (self.root, self.root / "package")):
            self.rejects("OVERLAPPING_ROOTS", R.backup, left, right,
                         self.evidence, service_root_authorization=self.root)
        self.rejects("OUTSIDE_AUTHORIZED_SERVICE_SCOPE", R.local_path,
                     self.root, service_root_authorization=self.root)
        self.assertFalse(self.package.exists())

    def test_authorized_offline_source_main_bytes_and_logic_unchanged(self):
        for db in self.keep:
            db.close()
        self.keep = []
        before = self.physical()
        with R.source_connections(self.source) as connections:
            logical = R.validate_databases(connections[3], connections[4])
        # Inspecting SQLite can create empty sidecars; record the actual baseline.
        before = self.physical()
        completed, report = self.cli("backup", "offline-backup", authorization=self.root)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        after = self.physical()
        self.evidence.event("offline_source_physical", before=before, after=after)
        for path in (self.auth.path, self.store.db_path):
            self.assertEqual(after[str(path)], before[str(path)])
        for path, fingerprint in before.items():
            self.assertEqual(after.get(path), fingerprint)
        for path in after.keys() - before.keys():
            self.assertTrue(path.endswith("-wal"))
            self.assertEqual(Path(path).stat().st_size, 0)
        self.assertEqual(report["artifact"]["validation"], logical)
        OBSERVATIONS.append({"test": self._testMethodName, "main_db_drift": [],
                             "logical_drift": [], "added_empty_wal": sorted(
                                 after.keys() - before.keys())})


def fingerprints():
    paths = [Path(__file__).resolve(), R.HERE, R.APP / "access_control.py",
             R.APP / "project_store.py",
             R.HERE.parent.parent / "docs/design/service-recovery.md"]
    return {str(path): R.digest(path.read_bytes()) for path in paths}


def audit_owned_sources():
    sync = R.load_module("recovery_secret_audit", R.HERE.parent / "repository_sync.py")
    findings = []
    for path in fingerprints():
        raw = Path(path).read_bytes()
        match = sync.SECRET.search(raw)
        if match:
            findings.append({"file": path, "line": raw[:match.start()].count(b"\n") + 1})
    return {"verdict": "PASS" if not findings else "FAIL",
            "scope": "OWNED_SOURCE_AND_AUTH_STORE_NO_CREDENTIAL_READ",
            "scanner_sha256": R.digest((R.HERE.parent / "repository_sync.py").read_bytes()),
            "findings": findings}


def main():
    global ROOT, OUT, SERVICE_SCOPE_RUNTIME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--test")
    parser.add_argument("--scope-test",
                        help="Run one explicit-scope test with --service-scope-runtime-root")
    parser.add_argument("--service-scope-runtime-root", type=Path,
                        help="New independent child of /data/tmp/opencad-recovery-scope "
                             "for explicit operator-scope tests only")
    args = parser.parse_args()
    R.require(not (args.test and args.scope_test), "TEST_SELECTOR_CONFLICT")
    R.require(not args.scope_test or args.service_scope_runtime_root is not None,
              "SCOPE_TEST_RUNTIME_REQUIRED")
    ROOT = R.local_path(args.runtime_root)
    require_absent = not ROOT.exists()
    R.require(require_absent, "RUNTIME_ALREADY_EXISTS")
    with R.fresh_directory(ROOT):
        pass
    OUT = args.out
    evidence = R.Evidence(OUT)
    if args.service_scope_runtime_root is not None:
        parent = Path("/data/tmp/opencad-recovery-scope")
        candidate = R.absolute(args.service_scope_runtime_root)
        R.require(candidate.parent == parent, "TEST_SERVICE_SCOPE_BOUNDARY")
        SERVICE_SCOPE_RUNTIME = R.local_path(
            candidate, service_root_authorization=parent)
        R.require(not SERVICE_SCOPE_RUNTIME.exists(), "TEST_SERVICE_SCOPE_ALREADY_EXISTS")
        with R.fresh_directory(SERVICE_SCOPE_RUNTIME):
            pass
    before = fingerprints()
    started = datetime.now(timezone.utc).isoformat()
    stream = io.StringIO()
    if args.scope_test:
        suite = unittest.defaultTestLoader.loadTestsFromName(
            args.scope_test, AuthorizedServiceScopeTests)
    else:
        suite = (unittest.defaultTestLoader.loadTestsFromName(args.test, RecoveryTests)
                 if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(RecoveryTests))
    if SERVICE_SCOPE_RUNTIME is not None and not args.test and not args.scope_test:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(
            AuthorizedServiceScopeTests))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = fingerprints()
    drift = sorted(path for path in before if before[path] != after.get(path))
    secret_audit = audit_owned_sources()
    rc = 0 if (result.wasSuccessful() and result.testsRun > 0 and not drift
               and secret_audit["verdict"] == "PASS") else 1
    marker = f"SERVICE_RECOVERY_TESTS verdict={'PASS' if rc == 0 else 'FAIL'}"
    evidence.directory.write("tests.log", stream.getvalue().encode())
    evidence.directory.write("result.json", R.canonical({
        "verdict": "PASS" if rc == 0 else "FAIL", "rc": rc, "started_at": started,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "tests": result.testsRun, "passed": result.testsRun - len(result.failures) -
        len(result.errors) - len(result.skipped), "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "completion_marker": marker, "source_before": before, "source_after": after,
        "source_drift": drift, "tests_log_sha256": R.digest(stream.getvalue().encode()),
        "observations": OBSERVATIONS, "runtime_root": str(ROOT), "secret_audit": secret_audit,
        "service_scope_runtime_root": str(SERVICE_SCOPE_RUNTIME)
        if SERVICE_SCOPE_RUNTIME is not None else None,
        "production_ready": False,
    }))
    evidence.close()
    print(stream.getvalue())
    print(f"{marker} tests={result.testsRun} rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
