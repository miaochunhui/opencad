"""Real persistent authentication contracts and an external evidence runner.

Standalone: python3 -B THIS_FILE --evidence /hf3fs-jd/data/tmp/NAME
The runner invokes the repository acceptance suite API, saves all child logs,
checks source drift, and never touches production or existing credentials.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
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


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opencad-auth-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
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
