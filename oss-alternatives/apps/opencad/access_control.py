"""OpenCAD-only accounts, opaque sessions and owner authorization.

Runtime data belongs in an explicitly supplied service-owned data root. Passwords
and raw session tokens are never persisted or included in exception messages.
HTTP transport, CSRF, project lookup and route enforcement belong to the gateway.
Run this file with --data-root PATH --create-user NAME to provision a local user;
the password is prompted, never accepted on the command line. Operator-only
account controls take user IDs and never expose an HTTP administration route.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import getpass
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Protocol
import uuid

PRODUCT = "opencad"
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}\Z")
_LOCALE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*\Z")
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 32768, 8, 3
_KDF_SLOTS = threading.BoundedSemaphore(2)


class AuthError(Exception):
    def __init__(self, code: str, detail: str = "", *, status: int = 401):
        self.code, self.status = code, status
        self.detail = detail
        super().__init__(detail or code)


@dataclass(frozen=True)
class Principal:
    user_id: str
    admin: bool = False
    _session_hash: str = field(default="", repr=False, compare=False)

    def as_dict(self) -> dict:
        return {"user_id": self.user_id, "admin": self.admin}


@dataclass(frozen=True)
class SessionTicket:
    token: str = field(repr=False)
    expires_at: int
    principal: Principal


@dataclass(frozen=True)
class ExternalIdentity:
    user_id: str
    username: str
    product: str
    admin: bool = False


class ExternalAdapter(Protocol):
    configured: bool

    def authenticate(self, username: str, password: str, *,
                     product: str) -> ExternalIdentity: ...


def _username(value: str) -> str:
    if not isinstance(value, str) or not _USERNAME.fullmatch(value):
        raise AuthError("invalid_username", "Invalid username", status=400)
    return value


def _password(value: str) -> bytes:
    if not isinstance(value, str):
        raise AuthError("invalid_password", "Password must be a string", status=400)
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise AuthError("invalid_password", "Invalid password encoding", status=400) from exc
    if not 12 <= len(value) or len(encoded) > 1024:
        raise AuthError("invalid_password", "Password must have at least 12 characters "
                        "and at most 1024 UTF-8 bytes", status=400)
    return encoded


def _locale(value: str) -> str:
    if not isinstance(value, str) or not _LOCALE.fullmatch(value) or len(value) > 64:
        raise AuthError("invalid_locale", "Invalid language tag", status=400)
    parts = value.replace("_", "-").split("-")
    return "-".join([parts[0].lower(), *[
        part.title() if len(part) == 4 and part.isalpha()
        else part.upper() if len(part) == 2 and part.isalpha() else part
        for part in parts[1:]]])


def _derive(encoded: bytes, salt: bytes) -> bytes:
    if not _KDF_SLOTS.acquire(blocking=False):
        raise AuthError("auth_busy", "Password verification is busy", status=429)
    try:
        return hashlib.scrypt(encoded, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                              p=_SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024)
    finally:
        _KDF_SLOTS.release()


class AuthController:
    def __init__(self, data_root, *, allow_registration: bool = False,
                 external_adapter: ExternalAdapter | None = None,
                 session_ttl_seconds: int = 28800, clock=time.time,
                 operator_mode: bool = False):
        if type(allow_registration) is not bool or type(operator_mode) is not bool:
            raise AuthError("auth_configuration", "Auth flags must be boolean", status=503)
        if (type(session_ttl_seconds) is not int
                or not 1 <= session_ttl_seconds <= 7 * 86400):
            raise AuthError("auth_configuration", "Invalid session lifetime", status=503)
        if external_adapter is not None and (
                getattr(external_adapter, "configured", None) is not True
                or not callable(getattr(external_adapter, "authenticate", None))):
            raise AuthError("auth_configuration", "External adapter is not configured", status=503)
        if data_root is None or not str(data_root).strip() or not callable(clock):
            raise AuthError("auth_configuration", "An explicit data root and clock are required", status=503)
        self.allow_registration = allow_registration
        self.external_adapter = external_adapter
        self.session_ttl_seconds = session_ttl_seconds
        self._operator_mode = operator_mode
        self._clock = clock
        self.data_root = Path(data_root).absolute()
        self.path = self.data_root / "opencad-auth.sqlite3"
        self._closed = False
        self._transactions = threading.local()
        try:
            if self.data_root.is_symlink() or any(p.is_symlink() for p in self.data_root.parents):
                raise AuthError("auth_configuration", "Auth data root cannot traverse symlinks", status=503)
            self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.is_symlink():
                raise AuthError("auth_configuration", "Auth database cannot be a symlink", status=503)
            # Explicit file creation prevents sqlite from creating a world-readable DB.
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self.path.is_file():
                    raise AuthError("auth_configuration", "Auth database is not a file", status=503)
            else:
                os.close(fd)
            with self._db() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise AuthError("auth_configuration", "Unsupported auth schema", status=503)
                db.execute("""CREATE TABLE IF NOT EXISTS auth_meta (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL)""")
                identity = db.execute("SELECT value FROM auth_meta WHERE name='product'").fetchone()
                if identity is not None and identity[0] != PRODUCT:
                    raise AuthError("auth_configuration", "Foreign product database", status=503)
                db.execute("INSERT OR IGNORE INTO auth_meta VALUES ('product', ?)", (PRODUCT,))
                db.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('local','external')),
                    salt BLOB, verifier BLOB,
                    admin INTEGER NOT NULL CHECK(admin IN (0,1)),
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    locale TEXT NOT NULL DEFAULT 'en')""")
                db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, product TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                    CHECK(revoked IN (0,1)))""")
                db.execute("PRAGMA user_version=1")
        except (OSError, sqlite3.Error) as exc:
            raise AuthError("auth_unavailable", "Auth storage is unavailable", status=503) from exc

    @contextmanager
    def _db(self, *, translate_body_errors=True):
        if self._closed:
            raise AuthError("auth_unavailable", "Auth controller is closed", status=503)
        if getattr(self._transactions, "active", False):
            raise AuthError("auth_transaction_active",
                            "Do not open another auth connection inside a transaction", status=503)
        db = None
        try:
            try:
                db = sqlite3.connect(self.path, timeout=5)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise AuthError("auth_unavailable", "Auth storage operation failed", status=503) from exc
            self._transactions.active = True
            try:
                yield db
            except sqlite3.Error as exc:
                if not translate_body_errors:
                    raise
                raise AuthError("auth_unavailable", "Auth storage operation failed", status=503) from exc
            try:
                db.commit()
            except sqlite3.Error as exc:
                raise AuthError("auth_unavailable", "Auth storage commit failed", status=503) from exc
        except BaseException as primary:
            if db is not None and db.in_transaction:
                try:
                    db.rollback()
                except sqlite3.Error as secondary:
                    if hasattr(primary, "add_note"):
                        primary.add_note("Auth rollback also failed: " + type(secondary).__name__)
            raise
        finally:
            self._transactions.active = False
            if db is not None:
                db.close()

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AuthError("auth_configuration", "Invalid service clock", status=503)
        return math.floor(value)

    @staticmethod
    def _digest(token: str) -> str:
        if token is None or token == "":
            raise AuthError("missing_token", "Authentication is required")
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise AuthError("invalid_token", "Invalid session")
        return hashlib.sha256((PRODUCT + "\0" + token).encode("ascii")).hexdigest()

    def register(self, username: str, password: str) -> dict:
        if not self.allow_registration or self.external_adapter is not None:
            raise AuthError("registration_disabled", "Registration is disabled", status=403)
        name, encoded = _username(username), _password(password)
        salt = secrets.token_bytes(16)
        verifier = _derive(encoded, salt)
        user_id = str(uuid.uuid4())
        with self._db() as db:
            if db.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
                raise AuthError("username_taken", "Username is already registered", status=409)
            db.execute("INSERT INTO users (user_id,username,kind,salt,verifier,admin,enabled) "
                       "VALUES (?,?,'local',?,?,0,1)", (user_id, name, salt, verifier))
        return {"user_id": user_id, "admin": False}

    def login(self, username: str, password: str) -> SessionTicket:
        name, encoded = _username(username), _password(password)
        if self.external_adapter is not None:
            try:
                identity = self.external_adapter.authenticate(name, password, product=PRODUCT)
            except AuthError:
                raise
            except Exception as exc:
                raise AuthError("auth_unavailable", "External authentication failed", status=503) from exc
            if (not isinstance(identity, ExternalIdentity) or identity.product != PRODUCT
                    or not isinstance(identity.user_id, str) or not identity.user_id.strip()
                    or len(identity.user_id) > 128 or identity.username != name
                    or type(identity.admin) is not bool):
                raise AuthError("invalid_external_identity", "Invalid external identity")
            with self._db() as db:
                row = db.execute("SELECT * FROM users WHERE user_id=? OR username=?",
                                 (identity.user_id, name)).fetchall()
                if row and (len(row) != 1 or row[0]["kind"] != "external"
                            or row[0]["user_id"] != identity.user_id or row[0]["username"] != name):
                    raise AuthError("identity_conflict", "External identity conflicts", status=403)
                if row and not row[0]["enabled"]:
                    raise AuthError("account_disabled", "Account is disabled")
                db.execute("INSERT INTO users (user_id,username,kind,admin,enabled) "
                           "VALUES (?,?,'external',?,1) ON CONFLICT(user_id) "
                           "DO UPDATE SET admin=excluded.admin",
                           (identity.user_id, name, int(identity.admin)))
                user_id = identity.user_id
        else:
            with self._db() as db:
                row = db.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            # Unknown users pay the same KDF cost; never accept another account kind.
            salt = bytes(row["salt"]) if row and row["kind"] == "local" else bytes(16)
            verifier = _derive(encoded, salt)
            if (not row or row["kind"] != "local" or not row["enabled"]
                    or not hmac.compare_digest(verifier, bytes(row["verifier"]))):
                raise AuthError("invalid_credentials", "Invalid username or password")
            user_id = row["user_id"]
        raw = secrets.token_hex(32)
        digest = self._digest(raw)
        expiry = self._now() + self.session_ttl_seconds
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None or not row["enabled"]:
                raise AuthError("account_disabled", "Account is disabled")
            db.execute("INSERT INTO sessions (token_hash,product,user_id,expires_at) VALUES (?,?,?,?)",
                       (digest, PRODUCT, user_id, expiry))
            principal = Principal(user_id, bool(row["admin"]), digest)
        return SessionTicket(raw, expiry, principal)

    def _principal(self, db, digest: str) -> Principal:
        row = db.execute("SELECT s.*, u.admin, u.enabled FROM sessions s "
                         "JOIN users u ON s.user_id=u.user_id WHERE token_hash=?", (digest,)).fetchone()
        if row is None or row["product"] != PRODUCT:
            raise AuthError("invalid_token", "Invalid session")
        if row["revoked"]:
            raise AuthError("revoked_token", "Session was revoked")
        if self._now() >= row["expires_at"]:
            raise AuthError("expired_token", "Session has expired")
        if not row["enabled"]:
            raise AuthError("account_disabled", "Account is disabled")
        return Principal(row["user_id"], bool(row["admin"]), digest)

    def authenticate(self, token: str) -> Principal:
        digest = self._digest(token)
        with self._db() as db:
            return self._principal(db, digest)

    def logout(self, token: str) -> None:
        digest = self._digest(token)
        with self._db() as db:
            row = db.execute("SELECT product FROM sessions WHERE token_hash=?", (digest,)).fetchone()
            if row is None or row["product"] != PRODUCT:
                raise AuthError("invalid_token", "Invalid session")
            db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (digest,))

    def _authorize_in_db(self, db, principal: Principal, *, owner_id: str,
                         allow_admin: bool = False) -> Principal:
        """Validate a live principal and owner using the caller's auth connection."""
        if (not isinstance(principal, Principal)
                or not isinstance(principal._session_hash, str)
                or not _TOKEN.fullmatch(principal._session_hash)
                or not isinstance(principal.user_id, str)
                or type(principal.admin) is not bool):
            raise AuthError("invalid_principal", "A live authenticated principal is required")
        if not isinstance(owner_id, str) or not owner_id.strip() or type(allow_admin) is not bool:
            raise AuthError("invalid_authorization", "Invalid authorization target", status=400)
        try:
            live = self._principal(db, principal._session_hash)
        except sqlite3.Error as exc:
            raise AuthError("auth_unavailable", "Auth verification failed", status=503) from exc
        if live.user_id != principal.user_id or live.admin != principal.admin:
            raise AuthError("invalid_principal", "Principal does not match the live session")
        if live.user_id != owner_id and not (allow_admin and live.admin):
            raise AuthError("project_forbidden", "Project access is forbidden", status=403)
        return live

    def authorize(self, principal: Principal, *, owner_id: str, allow_admin: bool = False) -> None:
        with self._db() as db:
            self._authorize_in_db(db, principal, owner_id=owner_id, allow_admin=allow_admin)

    @contextmanager
    def authorized_transaction(self, principal: Principal, *, owner_id: str,
                               allow_admin: bool = False):
        """Hold auth BEGIN IMMEDIATE through the caller's project transaction.

        Lock order is auth -> project. Prepare candidates/preferences first;
        inside the guard only commit/create/replay, not another auth operation.
        This is revocation serialization, not a cross-database atomic commit.
        """
        with self._db(translate_body_errors=False) as db:
            yield self._authorize_in_db(db, principal, owner_id=owner_id,
                                        allow_admin=allow_admin)

    def _operator_user(self, db, user_id):
        if not self._operator_mode:
            raise AuthError("operator_required", "Operator configuration is required", status=403)
        if (not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128):
            raise AuthError("invalid_user_id", "Invalid account target", status=400)
        row = db.execute("SELECT user_id,enabled,admin FROM users WHERE user_id=?",
                         (user_id,)).fetchone()
        if row is None:
            raise AuthError("unknown_user", "User does not exist", status=404)
        return row

    @staticmethod
    def _revoke_user_sessions(db, user_id):
        result = db.execute("UPDATE sessions SET revoked=1 WHERE user_id=? "
                            "AND product=? AND revoked=0", (user_id, PRODUCT))
        return result.rowcount

    def disable_user(self, user_id: str) -> dict:
        """Operator-only, repeatable disable plus durable old-session revocation."""
        with self._db() as db:
            row = self._operator_user(db, user_id)
            count = self._revoke_user_sessions(db, user_id)
            db.execute("UPDATE users SET enabled=0 WHERE user_id=?", (user_id,))
            return {"user_id": user_id, "enabled": False, "admin": bool(row["admin"]),
                    "sessions_revoked": count}

    def enable_user(self, user_id: str) -> dict:
        """Restore availability, never role or sessions from before disable."""
        with self._db() as db:
            row = self._operator_user(db, user_id)
            # Also handles a disabled schema-v1 row written by an older operator.
            count = self._revoke_user_sessions(db, user_id) if not row["enabled"] else 0
            db.execute("UPDATE users SET enabled=1 WHERE user_id=?", (user_id,))
            return {"user_id": user_id, "enabled": True, "admin": bool(row["admin"]),
                    "sessions_revoked": count}

    def revoke_all_sessions(self, user_id: str) -> dict:
        """Operator-only revoke of all current sessions, not a login prohibition."""
        with self._db() as db:
            row = self._operator_user(db, user_id)
            count = self._revoke_user_sessions(db, user_id)
            return {"user_id": user_id, "enabled": bool(row["enabled"]),
                    "admin": bool(row["admin"]), "sessions_revoked": count}

    def get_preferences(self, user_id: str) -> dict:
        with self._db() as db:
            row = db.execute("SELECT locale FROM users WHERE user_id=? AND enabled=1",
                             (user_id,)).fetchone()
            if row is None:
                raise AuthError("unknown_user", "User does not exist", status=404)
            return {"locale": row["locale"]}

    def set_preferences(self, user_id: str, *, locale: str) -> dict:
        value = _locale(locale)
        with self._db() as db:
            result = db.execute("UPDATE users SET locale=? WHERE user_id=? AND enabled=1",
                                (value, user_id))
            if result.rowcount != 1:
                raise AuthError("unknown_user", "User does not exist", status=404)
        return {"locale": value}

    def close(self) -> None:
        self._closed = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create-user")
    action.add_argument("--disable-user", metavar="USER_ID")
    action.add_argument("--enable-user", metavar="USER_ID")
    action.add_argument("--revoke-all", metavar="USER_ID")
    parser.add_argument("--admin", action="store_true",
                        help="Operator-only provisioning; never expose through HTTP")
    args = parser.parse_args()
    if args.admin and not args.create_user:
        parser.error("--admin is only valid with --create-user")
    first = None
    if args.create_user:
        first = getpass.getpass("Password: ")
        if first != getpass.getpass("Confirm password: "):
            raise AuthError("password_mismatch", "Passwords do not match", status=400)
    controller = AuthController(args.data_root, allow_registration=bool(args.create_user),
                                operator_mode=True)
    try:
        if args.create_user:
            user = controller.register(args.create_user, first)
            if args.admin:
                with controller._db() as db:
                    db.execute("UPDATE users SET admin=1 WHERE user_id=?", (user["user_id"],))
            print("OPENCAD_ACCOUNT verdict=CREATED user_id=" + user["user_id"])
        else:
            target, method, verdict = (
                (args.disable_user, controller.disable_user, "DISABLED") if args.disable_user else
                (args.enable_user, controller.enable_user, "ENABLED") if args.enable_user else
                (args.revoke_all, controller.revoke_all_sessions, "REVOKED"))
            result = method(target)
            print("OPENCAD_ACCOUNT verdict=" + verdict + " user_id=" + result["user_id"]
                  + " sessions_revoked=" + str(result["sessions_revoked"]))
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
