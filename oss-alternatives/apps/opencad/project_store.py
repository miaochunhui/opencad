"""Durable project heads and request receipts, independent of CAD execution.

SQLite is the only authoritative head. Full workspace JSON, immutable history
and replay responses commit together. The caller owns authorization and CAD
validation; this module never executes commands or publishes export files.
Use a private, external, local-filesystem root (SQLite WAL is not network-safe).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Mapping
import uuid

__all__ = [
    "ProjectStore", "ProjectStoreError", "InvalidPayload", "ProjectNotFound",
    "RevisionConflict", "IdempotencyConflict", "StoreCorrupt", "StorageFailure",
    "validate_root",
]

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MAX_REVISION = 2**63 - 1
_APPLICATION_ID = 0x4F434144
_SCHEMA_VERSION = 1


class ProjectStoreError(Exception):
    status = 500
    code = "project_store_error"


class InvalidPayload(ProjectStoreError):
    status = 400
    code = "invalid_payload"


class ProjectNotFound(ProjectStoreError):
    status = 404
    code = "project_not_found"


class RevisionConflict(ProjectStoreError):
    status = 409
    code = "revision_conflict"

    def __init__(self, revision: int, sha256: str):
        self.revision = revision
        self.sha256 = sha256
        super().__init__(f"head changed: revision={revision} sha256={sha256}")


class IdempotencyConflict(ProjectStoreError):
    status = 409
    code = "idempotency_conflict"


class StoreCorrupt(ProjectStoreError):
    code = "store_corrupt"


class StorageFailure(ProjectStoreError):
    """No success is acknowledged. An uncertain COMMIT must be resolved by ID."""

    status = 503
    code = "storage_failure"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidPayload(f"{label} must be a 1..128 character identifier")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise InvalidPayload(f"{label} must be non-empty text without NUL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidPayload(f"{label} is not valid UTF-8") from exc
    return value


def _revision(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_REVISION:
        raise InvalidPayload("expected_revision must be a positive SQLite integer")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise InvalidPayload("expected_sha must be a lowercase SHA-256")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPayload(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise InvalidPayload(f"non-finite JSON number: {value}")


def _loads(text: str) -> Any:
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_bad_constant)
        return _json_value(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidPayload("invalid JSON") from exc


def _json_value(value: Any) -> Any:
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise InvalidPayload("JSON text is not valid UTF-8") from exc
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise InvalidPayload("JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise InvalidPayload(f"not a finite JSON value: {type(value).__name__}")


def _canonical(value: Any) -> str:
    try:
        plain = _json_value(value)
        return json.dumps(plain, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidPayload("cannot canonicalize JSON") from exc


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _workspace(payload: Any) -> str:
    if isinstance(payload, str):
        payload = _loads(payload)
    if not isinstance(payload, Mapping) or set(payload) != {"doc", "undo", "redo", "standard"}:
        raise InvalidPayload("workspace must have exactly doc, undo, redo, standard")
    _text(payload["standard"], "standard")
    texts = [_text(payload["doc"], "doc")]
    for name in ("undo", "redo"):
        items = payload[name]
        if not isinstance(items, list):
            raise InvalidPayload(f"{name} must be a list of L1 strings")
        texts.extend(_text(item, name) for item in items)
    for text in texts:
        if not isinstance(_loads(text), dict):
            raise InvalidPayload("each L1 document must be a JSON object")
    return _canonical(payload)


def _request(payload: Any) -> tuple[str, int, str]:
    if not isinstance(payload, Mapping):
        raise InvalidPayload("request_payload must be the complete request object")
    revision = _revision(payload.get("expected_revision"))
    sha = _sha(payload.get("expected_sha"))
    return _canonical(payload), revision, sha


_DDL = (
    """CREATE TABLE projects (
        project_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
        head_revision INTEGER NOT NULL CHECK(head_revision >= 1),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(project_id, head_revision)
            REFERENCES snapshots(project_id, revision) DEFERRABLE INITIALLY DEFERRED
    )""",
    """CREATE TABLE snapshots (
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        revision INTEGER NOT NULL CHECK(revision >= 1),
        sha256 TEXT NOT NULL, payload TEXT NOT NULL, committed_at TEXT NOT NULL,
        PRIMARY KEY(project_id, revision)
    )""",
    """CREATE TABLE receipts (
        project_id TEXT NOT NULL, request_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, request_payload TEXT NOT NULL,
        revision INTEGER NOT NULL, response TEXT NOT NULL, response_sha256 TEXT NOT NULL,
        committed_at TEXT NOT NULL, PRIMARY KEY(project_id, request_id),
        FOREIGN KEY(project_id, revision) REFERENCES snapshots(project_id, revision)
    )""",
    """CREATE TABLE create_receipts (
        owner TEXT NOT NULL, request_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, request_payload TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        response TEXT NOT NULL, response_sha256 TEXT NOT NULL,
        PRIMARY KEY(owner, request_id)
    )""",
    "CREATE INDEX projects_owner ON projects(owner, project_id)",
    """CREATE TRIGGER monotonic_head BEFORE UPDATE ON projects
        WHEN NEW.head_revision != OLD.head_revision + 1
          OR NEW.project_id != OLD.project_id OR NEW.owner != OLD.owner
          OR NEW.title != OLD.title OR NEW.created_at != OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'non-monotonic or changed project identity'); END""",
)


def _regular_or_absent(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        # SQLite can remove WAL sidecars when another connection closes.
        if exc.errno == errno.ENOENT:
            return
        raise
    if not stat.S_ISREG(mode):
        raise InvalidPayload(f"storage file is linked or non-regular: {path}")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_root(root: str | os.PathLike) -> Path:
    """Check a local external storage path without creating or opening data.

    This is a point-in-time preflight, not a reservation. Store initialization
    repeats it and retains the creation collision and database file checks.
    """
    root = Path(os.path.abspath(os.fspath(root)))
    for path in (root, *root.parents):
        if path.is_symlink():
            raise InvalidPayload(f"storage root has a linked component: {path}")
        if path.exists() and not path.is_dir():
            raise InvalidPayload(f"storage component is not a directory: {path}")
        if (path / ".git").exists() or (path / ".git").is_symlink():
            raise InvalidPayload("storage root must be outside Git worktrees")
    resolved = root.resolve()
    if any(resolved.is_relative_to(base) for base in
           (Path("/3fs"), Path("/hf3fs-jd"), Path("/proc"), Path("/sys"), Path("/dev"))):
        raise InvalidPayload("storage root must be on a durable local filesystem")
    mountinfo = Path("/proc/self/mountinfo")
    if mountinfo.exists():
        mounts = []
        for line in mountinfo.read_text().splitlines():
            fields = line.split()
            separator = fields.index("-")
            mount = Path(fields[4].replace("\\040", " ").replace("\\134", "\\"))
            if resolved.is_relative_to(mount):
                mounts.append((len(mount.parts), fields[separator + 1]))
        if mounts:
            fs = max(mounts)[1]
            if fs.startswith(("fuse", "nfs", "cifs", "smb", "9p")) or fs == "tmpfs":
                raise InvalidPayload(f"WAL root filesystem is not supported: {fs}")
    path = root
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise InvalidPayload("storage parent is not a directory")
    return root


class ProjectStore:
    def __init__(self, root: str | os.PathLike, *, timeout: float = 5.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise InvalidPayload("timeout must be a non-negative finite number")
        if not math.isfinite(timeout) or timeout < 0:
            raise InvalidPayload("timeout must be a non-negative finite number")
        self.timeout = float(timeout)
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.db_path = self.root / "projects.sqlite3"
        self._closed = False
        self._prepare_root()
        try:
            with self._transaction() as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                app_id = conn.execute("PRAGMA application_id").fetchone()[0]
                if version == 0 and app_id == 0:
                    tables = conn.execute(
                        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    if tables:
                        raise StoreCorrupt("refusing to adopt an unrelated database")
                    for statement in _DDL:
                        conn.execute(statement)
                    for table in ("snapshots", "receipts", "create_receipts"):
                        for action in ("UPDATE", "DELETE"):
                            conn.execute(
                                f"CREATE TRIGGER immutable_{table}_{action.lower()} "
                                f"BEFORE {action} ON {table} BEGIN "
                                "SELECT RAISE(ABORT, 'immutable history'); END"
                            )
                    conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                elif (version, app_id) != (_SCHEMA_VERSION, _APPLICATION_ID):
                    raise StoreCorrupt(f"unsupported database identity/version: {app_id}/{version}")
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise StoreCorrupt("SQLite quick_check failed")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise StoreCorrupt("SQLite foreign_key_check failed")
            _sync_directory(self.root)
        except OSError as exc:
            raise StorageFailure(f"storage initialization sync failed: {exc}") from exc

    def _prepare_root(self) -> None:
        validate_root(self.root)
        missing = []
        path = self.root
        while not path.exists():
            missing.append(path)
            path = path.parent
        if not path.is_dir():
            raise InvalidPayload("storage parent is not a directory")
        try:
            for path in reversed(missing):
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    if path.is_symlink() or not path.is_dir():
                        raise InvalidPayload("storage root creation collision")
                _sync_directory(path.parent)
            if not self.root.is_dir():
                raise InvalidPayload("storage root is not a directory")
            self._check_files()
        except OSError as exc:
            raise StorageFailure(f"storage root preparation failed: {exc}") from exc

    def _check_files(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            _regular_or_absent(Path(str(self.db_path) + suffix))

    @contextmanager
    def _connection(self):
        if self._closed:
            raise StorageFailure("ProjectStore is closed")
        self._check_files()
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=self.timeout)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise StorageFailure("SQLite did not enable WAL")
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise StorageFailure("SQLite did not enable synchronous=FULL")
            yield conn
        except sqlite3.Error as exc:
            raise StorageFailure(f"SQLite failed: {exc}") from exc
        finally:
            if conn is not None:
                conn.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException as primary:
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except BaseException as secondary:
                        # Keep the original failure as the raised cause, not rollback.
                        if hasattr(primary, "add_note"):
                            primary.add_note(f"rollback also failed: {secondary}")
                raise

    @staticmethod
    def _head(conn: sqlite3.Connection, project_id: str, revision: int | None = None) -> dict:
        row = conn.execute(
            "SELECT p.*, s.revision, s.sha256, s.payload, s.committed_at "
            "FROM projects p JOIN snapshots s ON s.project_id=p.project_id "
            "AND s.revision=COALESCE(?,p.head_revision) WHERE p.project_id=?",
            (revision, project_id),
        ).fetchone()
        if row is None:
            raise ProjectNotFound("project or snapshot not found")
        try:
            payload = _workspace(row["payload"])
        except InvalidPayload as exc:
            raise StoreCorrupt("stored workspace is invalid") from exc
        if payload != row["payload"] or _digest(payload) != row["sha256"]:
            raise StoreCorrupt("stored snapshot SHA/canonical bytes mismatch")
        return {
            "project_id": row["project_id"], "owner": row["owner"], "title": row["title"],
            "revision": row["revision"], "sha256": row["sha256"],
            "snapshot": _loads(payload), "created_at": row["created_at"],
            "updated_at": row["committed_at"],
        }

    @staticmethod
    def _receipt(conn: sqlite3.Connection, project_id: str, request_id: str,
                 request: str) -> dict | None:
        row = conn.execute(
            "SELECT r.*, s.sha256 FROM receipts r JOIN snapshots s "
            "ON s.project_id=r.project_id AND s.revision=r.revision "
            "WHERE r.project_id=? AND r.request_id=?", (project_id, request_id),
        ).fetchone()
        if row is None:
            return None
        if _digest(row["request_payload"]) != row["request_sha256"]:
            raise StoreCorrupt("stored request fingerprint mismatch")
        if row["request_payload"] != request:
            raise IdempotencyConflict("request_id was already committed with a different request")
        if _digest(row["response"]) != row["response_sha256"]:
            raise StoreCorrupt("stored response fingerprint mismatch")
        ProjectStore._head(conn, project_id, row["revision"])
        try:
            response = _loads(row["response"])
        except InvalidPayload as exc:
            raise StoreCorrupt("stored response is invalid") from exc
        if not isinstance(response, dict) or _canonical(response) != row["response"]:
            raise StoreCorrupt("stored response is not a canonical object")
        return {
            "project_id": project_id, "request_id": request_id,
            "request_sha256": row["request_sha256"], "revision": row["revision"],
            "sha256": row["sha256"], "response": response,
            "committed_at": row["committed_at"],
        }

    @staticmethod
    def _create_receipt(conn: sqlite3.Connection, owner: str, request_id: str,
                        request: str) -> dict | None:
        row = conn.execute("SELECT * FROM create_receipts WHERE owner=? AND request_id=?",
                           (owner, request_id)).fetchone()
        if row is None:
            return None
        if _digest(row["request_payload"]) != row["request_sha256"]:
            raise StoreCorrupt("stored create fingerprint mismatch")
        if row["request_payload"] != request:
            raise IdempotencyConflict("create request_id was already used with a different body")
        if _digest(row["response"]) != row["response_sha256"]:
            raise StoreCorrupt("stored create response fingerprint mismatch")
        head = ProjectStore._head(conn, row["project_id"], 1)
        if _canonical(head) != row["response"]:
            raise StoreCorrupt("stored create response disagrees with initial snapshot")
        return head

    def create_project(self, owner: str, *, title: str, request_id: str, payload: Any) -> dict:
        owner = _text(owner, "owner")
        title = _text(title, "title")
        request_id = _identifier(request_id, "request_id")
        workspace = _workspace(payload)
        request = _canonical({"title": title, "payload": _loads(workspace)})
        with self._transaction() as conn:
            existing = self._create_receipt(conn, owner, request_id, request)
            if existing is not None:
                return existing
            project_id, now = uuid.uuid4().hex, _now()
            conn.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)",
                         (project_id, owner, title, 1, now, now))
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                         (project_id, 1, _digest(workspace), workspace, now))
            head = self._head(conn, project_id)
            response = _canonical(head)
            conn.execute("INSERT INTO create_receipts VALUES (?,?,?,?,?,?,?)",
                         (owner, request_id, _digest(request), request, project_id,
                          response, _digest(response)))
        return head

    def read_create_receipt(self, owner: str, request_id: str, *, title: str,
                            payload: Any) -> dict | None:
        owner, title = _text(owner, "owner"), _text(title, "title")
        request_id = _identifier(request_id, "request_id")
        request = _canonical({"title": title, "payload": _loads(_workspace(payload))})
        with self._connection() as conn:
            return self._create_receipt(conn, owner, request_id, request)

    def list_projects(self, owner: str) -> list[dict]:
        owner = _text(owner, "owner")
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT p.project_id,p.owner,p.title,p.head_revision AS revision,s.sha256,"
                "p.created_at,p.updated_at FROM projects p JOIN snapshots s "
                "ON s.project_id=p.project_id AND s.revision=p.head_revision "
                "WHERE p.owner=? ORDER BY p.created_at,p.project_id", (owner,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict:
        return self.read_head(project_id)

    def read_head(self, project_id: str) -> dict:
        project_id = _identifier(project_id, "project_id")
        with self._connection() as conn:
            return self._head(conn, project_id)

    def read_snapshot(self, project_id: str, revision: int) -> dict:
        project_id, revision = _identifier(project_id, "project_id"), _revision(revision)
        with self._connection() as conn:
            return self._head(conn, project_id, revision)

    def read_receipt(self, project_id: str, request_id: str, *,
                     request_payload: Mapping) -> dict | None:
        project_id = _identifier(project_id, "project_id")
        request_id = _identifier(request_id, "request_id")
        request, _, _ = _request(request_payload)
        with self._connection() as conn:
            return self._receipt(conn, project_id, request_id, request)

    def commit(self, project_id: str, *, expected_revision: int, expected_sha: str,
               request_id: str, request_payload: Mapping, payload: Any,
               response: Mapping) -> dict:
        project_id = _identifier(project_id, "project_id")
        request_id = _identifier(request_id, "request_id")
        expected_revision, expected_sha = _revision(expected_revision), _sha(expected_sha)
        request, revision, sha = _request(request_payload)
        if (revision, sha) != (expected_revision, expected_sha):
            raise InvalidPayload("request_payload base does not match commit base")
        with self._transaction() as conn:
            existing = self._receipt(conn, project_id, request_id, request)
            if existing is not None:
                return existing
            head = self._head(conn, project_id)
            if (head["revision"], head["sha256"]) != (expected_revision, expected_sha):
                raise RevisionConflict(head["revision"], head["sha256"])
            if expected_revision == _MAX_REVISION:
                raise StoreCorrupt("revision exhausted; refusing wraparound")
            workspace = _workspace(payload)
            if not isinstance(response, Mapping):
                raise InvalidPayload("response must be a JSON object")
            response_json = _canonical(response)
            new_revision, now = expected_revision + 1, _now()
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                         (project_id, new_revision, _digest(workspace), workspace, now))
            conn.execute("UPDATE projects SET head_revision=?,updated_at=? WHERE project_id=?",
                         (new_revision, now, project_id))
            conn.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?)",
                         (project_id, request_id, _digest(request), request, new_revision,
                          response_json, _digest(response_json), now))
            receipt = self._receipt(conn, project_id, request_id, request)
            if receipt is None:
                raise StoreCorrupt("newly inserted receipt is absent")
        return receipt

    def close(self) -> None:
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
