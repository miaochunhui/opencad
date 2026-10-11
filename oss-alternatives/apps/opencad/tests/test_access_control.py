"""Real persistent authentication contracts and an external evidence runner.

Standalone: python3 -B THIS_FILE --evidence /hf3fs-jd/data/tmp/NAME
The runner invokes the repository acceptance suite API, saves all child logs,
checks source drift, and never touches production or existing credentials.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[2]
CORE = ROOT / "oss-alternatives/packages/cadcore"
sys.path.insert(0, str(APP))
from access_control import AuthController, AuthError, ExternalIdentity, Principal, PRODUCT
from project_store import ProjectStore


def _revoker_process(root, operation, target, ready, start, channel):
    """Probe the real SQLite lock, then run the public revocation API."""
    auth = None
    try:
        options = {"operator_mode": True} if operation != "logout" else {}
        auth = AuthController(root, **options)
        ready.set()
        if not start.wait(10):
            raise RuntimeError("revoker scheduling signal missing")
        with sqlite3.connect(auth.path, timeout=0) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                # Python 3.10 has no sqlite_errorcode; this exact BEGIN error
                # still distinguishes the writer lock from unrelated failures.
                if str(exc) != "database is locked":
                    raise
                acquired = False
            else:
                acquired = True
                if operation == "logout":
                    db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=? AND product=?",
                               (auth._digest(target), PRODUCT))
                else:
                    db.execute("UPDATE sessions SET revoked=1 WHERE user_id=? AND product=?",
                               (target, PRODUCT))
                db.commit()
        channel.send({"phase": "probe", "acquired": acquired})
        if operation == "logout":
            auth.logout(target)
        elif operation == "revoke_all":
            auth.revoke_all_sessions(target)
        elif operation == "disable":
            auth.disable_user(target)
        else:
            raise RuntimeError("invalid revoker operation")
        channel.send({"phase": "done"})
    except BaseException as exc:
        channel.send({"phase": "error", "type": type(exc).__name__})
        raise
    finally:
        if auth is not None:
            auth.close()
        channel.close()


class AuthTests(unittest.TestCase):
    def setUp(self):
        # Retain isolated SQLite fixtures, including failed-case state, for readback.
        self.root = Path(tempfile.mkdtemp(
            prefix="opencad-auth-test-",
            dir=os.environ.get("OPENCAD_TEST_RUNTIME_ROOT", "/data/tmp")))
        self.clock = [10000]
        self.auth = AuthController(self.root / "auth", allow_registration=True,
                                   clock=lambda: self.clock[0], session_ttl_seconds=60)
        self.addCleanup(self.auth.close)
        self.secret = secrets.token_urlsafe(24)
        self.alice = self.auth.register("alice", self.secret)
        self.bob = self.auth.register("bob", self.secret)

    def error(self, code, callback, status=401):
        with self.assertRaises(AuthError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.status, status)
        self.assertNotIn(self.secret, str(raised.exception))

    def login(self, name="alice"):
        return self.auth.login(name, self.secret)

    def test_true_local_login_and_exact_public_contract(self):
        ticket = self.login()
        self.assertEqual(ticket.expires_at, 10060)
        self.assertEqual(ticket.principal.as_dict(), self.alice)
        self.assertEqual(len(ticket.token), 64)
        self.assertTrue(ticket.token.isalnum())
        self.assertNotIn(ticket.token, repr(ticket))
        self.assertEqual(self.auth.authenticate(ticket.token).as_dict(), self.alice)

    def test_real_database_stores_scrypt_salt_and_token_hash_only(self):
        ticket = self.login()
        self.assertTrue(self.auth.path.is_file())
        with sqlite3.connect(self.auth.path) as db:
            rows = db.execute("SELECT salt,verifier FROM users ORDER BY username").fetchall()
            sessions = db.execute("SELECT token_hash,product FROM sessions").fetchall()
        self.assertTrue(all(len(salt) == 16 and len(verifier) == 32 for salt, verifier in rows))
        self.assertTrue(rows[0][0] != rows[1][0])
        self.assertTrue(rows[0][1] != rows[1][1])
        self.assertTrue(sessions[0][0] != ticket.token)
        self.assertEqual(sessions[0][1], PRODUCT)
        raw = self.auth.path.read_bytes()
        self.assertTrue(self.secret.encode() not in raw)
        self.assertTrue(ticket.token.encode() not in raw)
        self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)

    def test_default_registration_is_closed_without_anonymous_login(self):
        closed = AuthController(self.root / "closed")
        self.addCleanup(closed.close)
        self.error("registration_disabled", lambda: closed.register("guest", self.secret), 403)
        self.error("invalid_credentials", lambda: closed.login("guest", self.secret))

    def test_wrong_and_unknown_credentials_do_not_issue_sessions(self):
        self.error("invalid_credentials", lambda: self.auth.login("alice", secrets.token_urlsafe(24)))
        self.error("invalid_credentials", lambda: self.auth.login("nobody", self.secret))
        with sqlite3.connect(self.auth.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_missing_forged_malformed_and_foreign_product_ticket_are_rejected(self):
        self.error("missing_token", lambda: self.auth.authenticate(None))
        for bad in ("Freechat.v1.bad", "Bearer " + secrets.token_hex(32), 123, " "):
            self.error("invalid_token", lambda: self.auth.authenticate(bad))
        self.error("invalid_token", lambda: self.auth.authenticate(secrets.token_hex(32)))
        ticket = self.login()
        forged = ("0" if ticket.token[0] != "0" else "1") + ticket.token[1:]
        self.error("invalid_token", lambda: self.auth.authenticate(forged))
        with sqlite3.connect(self.auth.path) as db:
            db.execute("UPDATE sessions SET product='freechat'")
        self.error("invalid_token", lambda: self.auth.authenticate(ticket.token))

    def test_expiration_boundary_is_closed(self):
        ticket = self.login()
        self.clock[0] = 10059.999
        self.assertEqual(self.auth.authenticate(ticket.token).user_id, self.alice["user_id"])
        self.clock[0] = 10060
        self.error("expired_token", lambda: self.auth.authenticate(ticket.token))
        self.error("expired_token", lambda: self.auth.authorize(ticket.principal,
                   owner_id=self.alice["user_id"]))

    def test_logout_revokes_across_independent_controllers_and_restart(self):
        ticket = self.login()
        other = AuthController(self.auth.data_root, clock=lambda: self.clock[0])
        self.addCleanup(other.close)
        self.assertEqual(other.authenticate(ticket.token).user_id, self.alice["user_id"])
        other.logout(ticket.token)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        self.auth.close()
        restarted = AuthController(self.auth.data_root, clock=lambda: self.clock[0])
        self.addCleanup(restarted.close)
        self.error("revoked_token", lambda: restarted.authenticate(ticket.token))
        restarted.logout(ticket.token)

    def test_live_session_survives_restart_without_raw_token_storage(self):
        ticket = self.login()
        self.auth.close()
        other = AuthController(self.auth.data_root, clock=lambda: self.clock[0])
        self.addCleanup(other.close)
        self.assertEqual(other.authenticate(ticket.token).as_dict(), self.alice)

    def test_two_users_owner_authorization_and_preferences_do_not_mix(self):
        alice, bob = self.login(), self.login("bob")
        self.auth.authorize(alice.principal, owner_id=self.alice["user_id"])
        self.auth.authorize(bob.principal, owner_id=self.bob["user_id"])
        self.error("project_forbidden", lambda: self.auth.authorize(alice.principal,
                   owner_id=self.bob["user_id"]), 403)
        self.assertEqual(self.auth.set_preferences(self.alice["user_id"], locale="zh_CN"),
                         {"locale": "zh-CN"})
        self.assertEqual(self.auth.set_preferences(self.bob["user_id"], locale="ja"), {"locale": "ja"})
        other = AuthController(self.auth.data_root)
        self.addCleanup(other.close)
        self.assertEqual(other.get_preferences(self.alice["user_id"]), {"locale": "zh-CN"})
        self.assertEqual(other.get_preferences(self.bob["user_id"]), {"locale": "ja"})
        for value in (None, "", "ja\n", 123):
            self.error("invalid_locale", lambda: self.auth.set_preferences(
                self.alice["user_id"], locale=value), 400)
        self.assertEqual(self.auth.get_preferences(self.alice["user_id"]), {"locale": "zh-CN"})

    def test_constructed_and_tampered_principals_cannot_authorize(self):
        ticket = self.login()
        forged = Principal(self.bob["user_id"], True)
        self.error("invalid_principal", lambda: self.auth.authorize(forged, owner_id=forged.user_id))
        self.error("invalid_principal", lambda: self.auth.authorize(
            replace(ticket.principal, admin=True), owner_id=self.bob["user_id"], allow_admin=True))
        self.error("invalid_principal", lambda: self.auth.authorize(
            replace(ticket.principal, user_id=self.bob["user_id"]), owner_id=self.bob["user_id"]))
        self.auth.logout(ticket.token)
        self.error("revoked_token", lambda: self.auth.authorize(ticket.principal,
                   owner_id=self.alice["user_id"]))

    def test_admin_only_when_persisted_and_explicitly_allowed(self):
        with sqlite3.connect(self.auth.path) as db:
            db.execute("UPDATE users SET admin=1 WHERE user_id=?", (self.alice["user_id"],))
        ticket = self.login()
        self.assertTrue(ticket.principal.admin)
        self.error("project_forbidden", lambda: self.auth.authorize(
            ticket.principal, owner_id=self.bob["user_id"]), 403)
        self.auth.authorize(ticket.principal, owner_id=self.bob["user_id"], allow_admin=True)
        with sqlite3.connect(self.auth.path) as db:
            db.execute("UPDATE users SET admin=0 WHERE user_id=?", (self.alice["user_id"],))
        self.error("invalid_principal", lambda: self.auth.authorize(
            ticket.principal, owner_id=self.bob["user_id"], allow_admin=True))

    def test_disabled_account_invalidates_existing_session(self):
        ticket = self.login()
        with sqlite3.connect(self.auth.path) as db:
            db.execute("UPDATE users SET enabled=0 WHERE user_id=?", (self.alice["user_id"],))
        self.error("account_disabled", lambda: self.auth.authenticate(ticket.token))

    def test_cross_store_tokens_do_not_authorize(self):
        ticket = self.login()
        other = AuthController(self.root / "different")
        self.addCleanup(other.close)
        self.error("invalid_token", lambda: other.authenticate(ticket.token))

    def test_sql_injection_and_bad_password_inputs_fail_loud(self):
        self.error("invalid_username", lambda: self.auth.login("alice' OR 1=1", self.secret), 400)
        for value in (None, "short", "x" * 1025):
            self.error("invalid_password", lambda: self.auth.register("bad", value), 400)
        self.error("username_taken", lambda: self.auth.register("alice", self.secret), 409)

    def test_unavailable_or_foreign_storage_and_closed_controller_fail_closed(self):
        invalid = self.root / "invalid"
        invalid.mkdir()
        (invalid / "opencad-auth.sqlite3").write_bytes(b"not a sqlite database")
        self.error("auth_unavailable", lambda: AuthController(invalid), 503)
        with sqlite3.connect(self.auth.path) as db:
            db.execute("UPDATE auth_meta SET value='freechat' WHERE name='product'")
        self.error("auth_configuration", lambda: AuthController(self.auth.data_root), 503)
        self.auth.close()
        self.error("auth_unavailable", lambda: self.auth.authenticate(secrets.token_hex(32)), 503)

    def test_registration_and_lifetime_flags_are_strict(self):
        self.error("auth_configuration", lambda: AuthController(
            self.root / "bad", allow_registration="true"), 503)
        for ttl in (True, 0, 1.5, -1):
            self.error("auth_configuration", lambda: AuthController(
                self.root / "bad", session_ttl_seconds=ttl), 503)

    def test_configured_external_adapter_has_no_local_or_anonymous_fallback(self):
        class Adapter:
            configured = True

            def authenticate(inner, username, password, *, product):
                raise ConnectionError("fixture external provider unavailable")

        external = AuthController(self.auth.data_root, external_adapter=Adapter())
        self.addCleanup(external.close)
        self.error("auth_unavailable", lambda: external.login("alice", self.secret), 503)
        self.error("auth_configuration", lambda: AuthController(
            self.root / "external", external_adapter=object()), 503)

    def test_external_identity_must_be_typed_product_bound_and_never_returned_as_ticket(self):
        class Adapter:
            configured = True
            identity = ExternalIdentity("external-user", "carol", "opencad", False)

            def authenticate(inner, username, password, *, product):
                return inner.identity

        adapter = Adapter()
        external = AuthController(self.root / "external", external_adapter=adapter)
        self.addCleanup(external.close)
        ticket = external.login("carol", self.secret)
        self.assertEqual(ticket.principal.user_id, "external-user")
        self.assertEqual(external.authenticate(ticket.token).user_id, "external-user")
        adapter.identity = replace(adapter.identity, product="freechat")
        self.error("invalid_external_identity", lambda: external.login("carol", self.secret))
        adapter.identity = {"user_id": "external-user", "admin": True}
        self.error("invalid_external_identity", lambda: external.login("carol", self.secret))

    def test_unexpected_external_error_is_retained_as_first_cause(self):
        class Adapter:
            configured = True

            def authenticate(inner, username, password, *, product):
                raise RuntimeError("provider failure fixture")

        external = AuthController(self.root / "external", external_adapter=Adapter())
        self.addCleanup(external.close)
        with self.assertRaises(AuthError) as raised:
            external.login("carol", self.secret)
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(raised.exception.status, 503)

    @contextmanager
    def _write_guard(self, principal, owner):
        if hasattr(self.auth, "authorized_transaction"):
            with self.auth.authorized_transaction(principal, owner_id=owner) as live:
                yield live
        else:
            # A fixed pre-fix control: the old API releases auth before project commit.
            self.auth.authorize(principal, owner_id=owner)
            yield principal

    def _store(self):
        store = ProjectStore(self.root / "projects")
        self.addCleanup(store.close)
        payload = {"doc": "{}", "undo": [], "redo": [], "standard": "gbt"}
        head = store.create_project(self.alice["user_id"], title="race-fixture",
                                    request_id="create-fixture", payload=payload)
        return store, head, payload

    def _commit(self, store, head, payload, request_id="guarded-save"):
        request = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                   "operation": "fixture-save"}
        receipt = store.commit(
            head["project_id"], expected_revision=head["revision"],
            expected_sha=head["sha256"], request_id=request_id,
            request_payload=request, payload=payload, response={"ok": True})
        return receipt, request

    def _start_revoker(self, operation, target):
        context = multiprocessing.get_context("spawn")
        ready, start = context.Event(), context.Event()
        parent, child = context.Pipe()
        process = context.Process(target=_revoker_process,
                                  args=(str(self.auth.data_root), operation, target,
                                        ready, start, child))
        process.start()
        child.close()
        self.addCleanup(parent.close)

        def finish():
            start.set()
            process.join(6)
            if process.is_alive():
                process.terminate()
                process.join(2)
                raise AssertionError("revoker exceeded bounded deadlock detector")

        self.addCleanup(finish)
        self.assertTrue(ready.wait(5), "revoker did not initialize")
        return process, start, parent

    def _message(self, channel, phase):
        self.assertTrue(channel.poll(5), "revoker progress marker missing")
        message = channel.recv()
        self.assertEqual(message["phase"], phase)
        return message

    def _guard_wins(self, operation):
        ticket = self.login()
        store, head, payload = self._store()
        target = ticket.token if operation == "logout" else ticket.principal.user_id
        process, start, channel = self._start_revoker(operation, target)
        with self._write_guard(ticket.principal, ticket.principal.user_id) as live:
            self.assertEqual(live.as_dict(), ticket.principal.as_dict())
            start.set()
            probe = self._message(channel, "probe")
            if not probe["acquired"]:
                self.assertFalse(channel.poll(0.1), "revocation completed inside guard")
            receipt, request = self._commit(store, head, payload)
            self.assertEqual(receipt["revision"], 2)
        self._message(channel, "done")
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(store.read_head(head["project_id"])["revision"], 2)
        self.assertEqual(store.read_receipt(head["project_id"], "guarded-save",
                                           request_payload=request), receipt)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        self.assertFalse(probe["acquired"],
                         "TOCTOU: external revocation committed before a successful project save")

    def test_logout_guard_wins_real_process_commit_boundary(self):
        self._guard_wins("logout")

    def test_revoke_all_guard_wins_real_process_commit_boundary(self):
        self._guard_wins("revoke_all")

    def test_disable_guard_wins_real_process_commit_boundary(self):
        self._guard_wins("disable")

    def test_revoke_first_denies_save_without_receipt_across_processes(self):
        for operation in ("logout", "revoke_all", "disable"):
            with self.subTest(operation=operation):
                operator = AuthController(self.auth.data_root, operator_mode=True)
                operator.enable_user(self.alice["user_id"])
                operator.close()
                ticket = self.login()
                store, head, payload = self._store()
                process, start, channel = self._start_revoker(
                    operation, ticket.token if operation == "logout" else ticket.principal.user_id)
                start.set()
                self.assertTrue(self._message(channel, "probe")["acquired"])
                self._message(channel, "done")
                process.join(5)
                self.assertEqual(process.exitcode, 0)
                request_id = "revoked-first-" + operation
                with self.assertRaises(AuthError) as denied:
                    with self.auth.authorized_transaction(
                            ticket.principal, owner_id=ticket.principal.user_id):
                        self._commit(store, head, payload, request_id)
                self.assertEqual(denied.exception.code, "revoked_token")
                self.assertEqual(denied.exception.status, 401)
                self.assertEqual(store.read_head(head["project_id"]), head)
                with sqlite3.connect(store.db_path) as db:
                    self.assertEqual(db.execute(
                        "SELECT COUNT(*) FROM receipts WHERE project_id=?",
                        (head["project_id"],)).fetchone()[0], 0)

    def test_guarded_create_finishes_before_external_logout(self):
        ticket = self.login()
        store = ProjectStore(self.root / "created-project")
        self.addCleanup(store.close)
        process, start, channel = self._start_revoker("logout", ticket.token)
        with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
            start.set()
            self.assertFalse(self._message(channel, "probe")["acquired"])
            payload = {"doc": "{}", "undo": [], "redo": [], "standard": "gbt"}
            head = store.create_project(ticket.principal.user_id, title="guarded-create",
                                        request_id="guarded-create", payload=payload)
        self._message(channel, "done")
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(store.read_create_receipt(
            ticket.principal.user_id, "guarded-create", title="guarded-create",
            payload=payload), head)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))

    def test_revoke_first_denies_create_and_existing_receipt_replay(self):
        ticket = self.login()
        store, head, payload = self._store()
        receipt, request = self._commit(store, head, payload)
        self.auth.logout(ticket.token)
        with self.assertRaises(AuthError) as denied:
            with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                store.create_project(ticket.principal.user_id, title="forbidden-create",
                                     request_id="forbidden-create", payload=payload)
        self.assertEqual(denied.exception.code, "revoked_token")
        with self.assertRaises(AuthError) as denied:
            with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                store.read_receipt(head["project_id"], "guarded-save", request_payload=request)
        self.assertEqual(denied.exception.code, "revoked_token")
        self.assertEqual(len(store.list_projects(ticket.principal.user_id)), 1)
        self.assertIsNone(store.read_create_receipt(
            ticket.principal.user_id, "forbidden-create", title="forbidden-create", payload=payload))
        self.assertEqual(store.read_receipt(head["project_id"], "guarded-save",
                                           request_payload=request), receipt)

    def test_shared_same_connection_validation_and_guard_security(self):
        ticket = self.login()
        with self.auth._db() as db:
            self.assertEqual(self.auth._authorize_in_db(
                db, ticket.principal, owner_id=ticket.principal.user_id).as_dict(), self.alice)
        for principal, owner, allow_admin, code, status in (
                (Principal(ticket.principal.user_id), ticket.principal.user_id, False,
                 "invalid_principal", 401),
                (replace(ticket.principal, admin=True), self.bob["user_id"], True,
                 "invalid_principal", 401),
                (replace(ticket.principal, user_id=self.bob["user_id"]), self.bob["user_id"], False,
                 "invalid_principal", 401),
                (ticket.principal, self.bob["user_id"], False, "project_forbidden", 403),
                (ticket.principal, "", False, "invalid_authorization", 400),
                (ticket.principal, ticket.principal.user_id, "true", "invalid_authorization", 400)):
            with self.subTest(code=code):
                with self.assertRaises(AuthError) as raised:
                    with self.auth.authorized_transaction(
                            principal, owner_id=owner, allow_admin=allow_admin):
                        self.fail("invalid guard was entered")
                self.assertEqual((raised.exception.code, raised.exception.status), (code, status))
        with self.auth._db() as db:
            db.execute("UPDATE users SET admin=1 WHERE user_id=?", (ticket.principal.user_id,))
        admin = self.login()
        with self.auth.authorized_transaction(admin.principal, owner_id=self.bob["user_id"],
                                              allow_admin=True) as live:
            self.assertTrue(live.admin)
        with self.auth._db() as db:
            db.execute("UPDATE users SET admin=0 WHERE user_id=?", (admin.principal.user_id,))
        with self.assertRaises(AuthError) as raised:
            with self.auth.authorized_transaction(admin.principal, owner_id=self.bob["user_id"],
                                                  allow_admin=True):
                self.fail("stale admin guard was entered")
        self.assertEqual(raised.exception.code, "invalid_principal")
        self.clock[0] = ticket.expires_at
        with self.assertRaises(AuthError) as raised:
            with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                self.fail("expired guard was entered")
        self.assertEqual(raised.exception.code, "expired_token")

    def test_guard_failure_releases_lock_and_preserves_project_failure_cause(self):
        ticket = self.login()
        store, head, payload = self._store()
        failure = sqlite3.OperationalError("fixture project operation failure")
        with self.assertRaises(sqlite3.OperationalError) as raised:
            with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                raise failure
        self.assertIs(raised.exception, failure)
        self.assertEqual(store.read_head(head["project_id"]), head)
        self.auth.logout(ticket.token)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))

    def test_real_project_sqlite_failure_keeps_head_receipts_and_auth_lock_reusable(self):
        from project_store import StorageFailure
        ticket = self.login()
        store, head, payload = self._store()
        with sqlite3.connect(store.db_path) as db:
            db.execute("""CREATE TRIGGER deny_fixture_receipt BEFORE INSERT ON receipts
                          BEGIN SELECT RAISE(ABORT, 'fixture_receipt_rejected'); END""")
        with self.assertRaises(StorageFailure) as raised:
            with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                self._commit(store, head, payload)
        self.assertIsInstance(raised.exception.__cause__, sqlite3.Error)
        self.assertEqual(store.read_head(head["project_id"]), head)
        with sqlite3.connect(store.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)
        self.auth.logout(ticket.token)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))

    def test_failed_auth_commit_preserves_sqlite_cause_and_rolls_back_operator_changes(self):
        def authorizer(operation, argument, *_):
            return (sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_TRANSACTION
                    and argument == "COMMIT" else sqlite3.SQLITE_OK)

        with self.assertRaises(AuthError) as raised:
            with self.auth._db() as db:
                db.execute("UPDATE users SET enabled=0 WHERE user_id=?", (self.alice["user_id"],))
                db.set_authorizer(authorizer)
        self.assertEqual((raised.exception.code, raised.exception.status), ("auth_unavailable", 503))
        self.assertIsInstance(raised.exception.__cause__, sqlite3.Error)
        self.assertEqual(self.auth.authenticate(self.login().token).as_dict(), self.alice)

    def test_guard_rejects_auth_reentry_and_authorize_does_not_leave_long_lock(self):
        ticket = self.login()
        self.auth.authorize(ticket.principal, owner_id=ticket.principal.user_id)
        with sqlite3.connect(self.auth.path, timeout=0) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
            self.error("auth_transaction_active", lambda: self.auth.get_preferences(
                ticket.principal.user_id), 503)
            self.error("auth_transaction_active", lambda: self.auth.set_preferences(
                ticket.principal.user_id, locale="ja"), 503)
            self.error("auth_transaction_active", lambda: self.auth.authenticate(ticket.token), 503)
            with self.assertRaises(AuthError) as raised:
                with self.auth.authorized_transaction(ticket.principal, owner_id=ticket.principal.user_id):
                    self.fail("nested guard entered")
            self.assertEqual(raised.exception.code, "auth_transaction_active")
        self.assertEqual(self.auth.get_preferences(ticket.principal.user_id), {"locale": "en"})

    def _operator(self):
        operator = AuthController(self.auth.data_root, operator_mode=True,
                                  clock=lambda: self.clock[0])
        self.addCleanup(operator.close)
        return operator

    def test_operator_controls_require_explicit_configuration_and_valid_target(self):
        for method in (self.auth.disable_user, self.auth.enable_user, self.auth.revoke_all_sessions):
            self.error("operator_required", lambda: method(self.alice["user_id"]), 403)
        operator = self._operator()
        for method in (operator.disable_user, operator.enable_user, operator.revoke_all_sessions):
            self.error("unknown_user", lambda: method("unknown-user"), 404)
            self.error("invalid_user_id", lambda: method(None), 400)
        self.error("auth_configuration", lambda: AuthController(
            self.root / "operator-bad", operator_mode="true"), 503)

    def test_disable_enable_is_repeatable_and_never_revives_old_sessions_or_admin(self):
        first, second = self.login(), self.login()
        operator = self._operator()
        disabled = operator.disable_user(first.principal.user_id)
        self.assertEqual(disabled["sessions_revoked"], 2)
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled["admin"])
        self.assertEqual(operator.disable_user(first.principal.user_id)["sessions_revoked"], 0)
        self.error("invalid_credentials", lambda: self.login())
        enabled = operator.enable_user(first.principal.user_id)
        self.assertTrue(enabled["enabled"])
        self.assertFalse(enabled["admin"])
        self.assertEqual(operator.enable_user(first.principal.user_id)["sessions_revoked"], 0)
        for ticket in (first, second):
            self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        fresh = self.login()
        operator.enable_user(fresh.principal.user_id)
        self.assertEqual(self.auth.authenticate(fresh.token).as_dict(), self.alice)

    def test_enable_legacy_disabled_row_revokes_old_sessions_and_preserves_demoted_role(self):
        with self.auth._db() as db:
            db.execute("UPDATE users SET admin=1 WHERE user_id=?", (self.alice["user_id"],))
        ticket = self.login()
        with self.auth._db() as db:
            db.execute("UPDATE users SET enabled=0,admin=0 WHERE user_id=?", (self.alice["user_id"],))
        result = self._operator().enable_user(ticket.principal.user_id)
        self.assertEqual(result["sessions_revoked"], 1)
        self.assertFalse(result["admin"])
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        self.assertFalse(self.login().principal.admin)

    def test_revoke_all_is_repeatable_product_scoped_and_survives_restart(self):
        tickets = [self.login(), self.login()]
        bob = self.login("bob")
        with self.auth._db() as db:
            db.execute("INSERT INTO sessions VALUES (?, 'foreign', ?, 10060, 0)",
                       (secrets.token_hex(32), self.alice["user_id"]))
        operator = self._operator()
        self.assertEqual(operator.revoke_all_sessions(self.alice["user_id"])["sessions_revoked"], 2)
        self.assertEqual(operator.revoke_all_sessions(self.alice["user_id"])["sessions_revoked"], 0)
        self.auth.close()
        self.auth = AuthController(self.auth.data_root, clock=lambda: self.clock[0])
        self.addCleanup(self.auth.close)
        for ticket in tickets:
            self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        self.assertEqual(self.auth.authenticate(bob.token).as_dict(), self.bob)
        with self.auth._db() as db:
            self.assertEqual(db.execute(
                "SELECT revoked FROM sessions WHERE product='foreign'").fetchone()[0], 0)
        self.assertEqual(self.auth.authenticate(self.login().token).as_dict(), self.alice)

    def test_operator_cli_repeatable_real_sqlite_without_password_or_role_change(self):
        ticket = self.login()
        user_id = ticket.principal.user_id
        for flag, verdict in (("--disable-user", "DISABLED"), ("--disable-user", "DISABLED"),
                              ("--enable-user", "ENABLED"), ("--enable-user", "ENABLED"),
                              ("--revoke-all", "REVOKED"), ("--revoke-all", "REVOKED")):
            process = subprocess.run(
                [sys.executable, "-B", str(APP / "access_control.py"),
                 "--data-root", str(self.auth.data_root), flag, user_id],
                stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0)
            self.assertIn("OPENCAD_ACCOUNT verdict=" + verdict, process.stdout)
            self.assertNotIn(self.secret, process.stdout + process.stderr)
            self.assertNotIn(ticket.token, process.stdout + process.stderr)
        self.error("revoked_token", lambda: self.auth.authenticate(ticket.token))
        self.assertFalse(self.login().principal.admin)


OWNED = (
    "oss-alternatives/apps/opencad/access_control.py",
    "oss-alternatives/apps/opencad/request_context.py",
    "oss-alternatives/apps/opencad/tests/test_access_control.py",
    "oss-alternatives/apps/opencad/tests/test_request_context.py",
    "oss-alternatives/packages/cadcore/cadcore/i18n/catalog.py",
    "oss-alternatives/packages/cadcore/tests/test_catalog_context.py",
    "oss-alternatives/docs/design/project-auth.md",
)


def evidence_run(out: Path) -> int:
    """Use the existing repository suite entry, not a parallel fake test runner."""
    out = out.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("evidence must be outside the source tree")
    out.mkdir(parents=True, exist_ok=False)
    temporary = out / "tmp"
    temporary.mkdir()
    paths = (*OWNED, "oss-alternatives/apps/opencad/server.py")

    def source():
        return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}

    before = source()
    report = {"verdict": "FAIL", "source_before": before, "source_root": str(ROOT),
              "started_at": datetime.now(timezone.utc).isoformat(), "steps": [],
              "production_mutated": False, "gateway_route_enforcement": "NOT_ASSESSED"}
    started = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(temporary),
               PYTHONPATH=os.pathsep.join((str(APP), str(CORE))))
    for start, top, pattern in (
            (APP / "tests", APP, "test_access_control.py"),
            (APP / "tests", APP, "test_request_context.py"),
            (CORE / "tests", CORE, "test_catalog_context.py"),
            (CORE / "tests", CORE, "test_i18n_catalog.py")):
        argv = [sys.executable, "-B", str(ROOT / "tools/acceptance.py"),
                "--suite", str(start), "--top", str(top), "--pattern", pattern]
        log = out / (pattern + ".log")
        with log.open("xb") as stream:
            process = subprocess.run(argv, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        lines = log.read_text().splitlines()
        markers = [json.loads(line.removeprefix("INDEPENDENT_TEST_COUNTS ")) for line in lines
                   if line.startswith("INDEPENDENT_TEST_COUNTS ")]
        counts = markers[0] if len(markers) == 1 else None
        step = {"command": argv, "rc": process.returncode, "counts": counts,
                "log": str(log), "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
        report["steps"].append(step)
        print("AUTH_CONTEXT_STEP pattern=" + pattern + " rc=" + str(process.returncode)
              + " counts=" + json.dumps(counts, sort_keys=True))
        if process.returncode or not counts or not counts["successful"] or counts["skipped"]:
            break
    after = source()
    report["source_after"] = after
    report["source_drift"] = [name for name in before if before[name] != after[name]]
    if len(report["steps"]) == 4 and not report["source_drift"] and all(
            step["rc"] == 0 and step["counts"] and step["counts"]["successful"]
            and not step["counts"]["skipped"] for step in report["steps"]):
        report["verdict"] = "PASS"
    report["elapsed_seconds"] = time.monotonic() - started
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    line = "AUTH_CONTEXT_ACCEPTANCE verdict=" + report["verdict"] + " evidence=" + str(out)
    (out / "acceptance.log").write_text(line + "\n")
    print(line)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    args, remaining = parser.parse_known_args()
    if args.evidence:
        raise SystemExit(evidence_run(args.evidence))
    unittest.main(argv=[sys.argv[0], *remaining])
