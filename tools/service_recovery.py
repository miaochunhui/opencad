#!/usr/bin/env python3
"""Coordinated local SQLite recovery with explicit operator path authorization."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import traceback
import uuid

HERE = Path(__file__).resolve()
FIXTURES = Path("/data/dev/opencad-private-fixtures")
EVIDENCE = Path("/hf3fs-jd/data/tmp")
FORMAT = "opencad-service-recovery-v1"
FILES = {"accounts": "accounts.sqlite3", "projects": "projects.sqlite3"}
LOCK_ORDER = ["auth:BEGIN IMMEDIATE", "project:BEGIN IMMEDIATE"]
AUTH_DDL = (
    "CREATE TABLE auth_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE users (
        user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('local','external')),
        salt BLOB, verifier BLOB,
        admin INTEGER NOT NULL CHECK(admin IN (0,1)),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        locale TEXT NOT NULL DEFAULT 'en')""",
    """CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY, product TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(user_id),
        expires_at INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
        CHECK(revoked IN (0,1)))""",
)


class RecoveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise RecoveryError(code)


def failure_details(exc):
    value = {"code": getattr(exc, "code", type(exc).__name__),
             "type": type(exc).__name__,
             "coordinates": [{"file": frame.filename, "line": frame.lineno,
                              "function": frame.name}
                             for frame in traceback.extract_tb(exc.__traceback__)]}
    if isinstance(exc, sqlite3.Error):
        value["sqlite_errorcode"] = getattr(exc, "sqlite_errorcode", None)
        value["sqlite_errorname"] = getattr(exc, "sqlite_errorname", None)
    value["secondary_errors"] = getattr(exc, "recovery_secondary_errors", [])
    return value


def retain_secondary(primary, secondary):
    errors = getattr(primary, "recovery_secondary_errors", [])
    primary.recovery_secondary_errors = [*errors, type(secondary).__name__]


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


APP = HERE.parent.parent / "oss-alternatives/apps/opencad"
STORE = load_module("recovery_project_store", APP / "project_store.py")
AUTH = load_module("recovery_access_control", APP / "access_control.py")


def source_fingerprint():
    paths = [HERE, APP / "access_control.py", APP / "project_store.py"]
    return {str(path): digest(path.read_bytes()) for path in paths}


def sql_contract(text):
    """Ignore layout outside SQL literals, never rewrite CHECK/trigger literals."""
    return "".join(part if part.startswith(("'", '"', "`", "["))
                   else re.sub(r"\s+", "", part).lower()
                   for part in re.split(
                       r"""('(?:''|[^'])*'|"(?:\"\"|[^"])*"|`[^`]*`|\[[^\]]*\])""", text))


def absolute(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "INVALID_ABSOLUTE_PATH")
    return path


def service_parent_authorization(value=None):
    """An explicit scope narrows filesystem access; it never relaxes local guards."""
    if value is None:
        parent = FIXTURES
    else:
        require(isinstance(value, (str, os.PathLike)), "INVALID_SERVICE_ROOT_AUTHORIZATION")
        require(bool(os.fspath(value).strip()), "EMPTY_SERVICE_ROOT_AUTHORIZATION")
        parent = absolute(value)
        # Require a named service parent, not a filesystem/system umbrella.
        require(len(parent.parts) >= 4, "SERVICE_ROOT_AUTHORIZATION_TOO_BROAD")
    STORE.validate_root(parent)
    require(parent.is_dir(), "SERVICE_ROOT_AUTHORIZATION_PARENT_MISSING")
    disjoint(parent, HERE.parent.parent)
    for component in (parent, *parent.parents):
        require(not (component / "accounts/opencad-auth.sqlite3").exists()
                and not (component / "projects/projects.sqlite3").exists(),
                "NESTED_SERVICE_ROOT")
    return parent


def local_path(path, *, service_root_authorization=None):
    path = absolute(path)
    parent = service_parent_authorization(service_root_authorization)
    require(path != parent and path.is_relative_to(parent),
            "OUTSIDE_LOCAL_FIXTURE_SCOPE" if service_root_authorization is None
            else "OUTSIDE_AUTHORIZED_SERVICE_SCOPE")
    STORE.validate_root(path)
    for component in path.parents:
        require(not (component / "accounts/opencad-auth.sqlite3").exists()
                and not (component / "projects/projects.sqlite3").exists(),
                "NESTED_SERVICE_ROOT")
        if component == parent:
            break
    return path


def disjoint(*paths):
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            require(not (left.is_relative_to(right) or right.is_relative_to(left)),
                    "OVERLAPPING_ROOTS")


class Directory:
    """Hold every component with O_NOFOLLOW; never re-resolve a held parent."""

    def __init__(self, path):
        self.path = absolute(path)
        self.identities = {}
        self.fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=self.fd)
                os.close(self.fd)
                self.fd = child
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        os.close(self.fd)

    def child(self, name):
        require("/" not in name and name not in ("", ".", ".."), "INVALID_CHILD")
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     dir_fd=self.fd)
        obj = object.__new__(Directory)
        obj.path, obj.fd = self.path / name, fd
        obj.identities = {}
        return obj

    def create(self, name):
        os.mkdir(name, mode=0o700, dir_fd=self.fd)
        os.fsync(self.fd)
        return self.child(name)

    def write(self, name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.fsync(self.fd)

    def read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                    "NON_REGULAR_PACKAGE_FILE")
            return stream.read()

    def database(self, name, *, readonly):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                info = os.stat(name + suffix, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                require(suffix != "", "MISSING_DATABASE")
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "LINKED_OR_NON_REGULAR_DATABASE")
        before = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino)
        require(self.identities.get(name, identity) == identity, "DATABASE_PATH_DRIFT")
        self.identities[name] = identity
        # /proc paths here refer only to our held directory descriptors.
        uri = f"file:/proc/self/fd/{self.fd}/{name}?mode={'ro' if readonly else 'rw'}"
        conn = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        after = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            conn.close()
            raise RecoveryError("DATABASE_PATH_DRIFT")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        return conn

    def unchanged(self):
        with Directory(self.path) as actual:
            left, right = os.fstat(actual.fd), os.fstat(self.fd)
            require((left.st_dev, left.st_ino) == (right.st_dev, right.st_ino),
                    "DIRECTORY_PATH_DRIFT")
        for name, identity in self.identities.items():
            actual = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            require(stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1
                    and (actual.st_dev, actual.st_ino) == identity,
                    "DATABASE_PATH_DRIFT")


def fresh_directory(path):
    with Directory(path.parent) as parent:
        return parent.create(path.name)


class Evidence:
    def __init__(self, out):
        out = absolute(out)
        # The known hf3fs alias is allowed only for external evidence, never DBs.
        require(out.is_relative_to(EVIDENCE) and out != EVIDENCE, "INVALID_EVIDENCE_ROOT")
        for component in (out, *out.parents):
            if component == Path("/hf3fs-jd"):
                require(component.resolve() == Path("/3fs/research3fs"),
                        "UNVERIFIED_EVIDENCE_ALIAS")
            else:
                require(not component.is_symlink(), "LINKED_EVIDENCE_PATH")
        self.out = out
        self.directory = fresh_directory(out.resolve())
        self.sequence = 0

    def close(self):
        self.directory.__exit__()

    def event(self, phase, **fields):
        self.sequence += 1
        self.directory.write(f"journal-{self.sequence:04d}.json",
                             canonical({"at": now(), "phase": phase, **fields}))


def schema(db):
    return sorted((row["type"], row["name"], row["tbl_name"],
                   sql_contract(row["sql"]))
                  for row in db.execute(
                      "SELECT type,name,tbl_name,sql FROM sqlite_master "
                      "WHERE name NOT LIKE 'sqlite_%'"))


def expected_schema(kind):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        for statement in AUTH_DDL if kind == "accounts" else STORE._DDL:
            db.execute(statement)
        if kind == "projects":
            for table in ("snapshots", "receipts", "create_receipts"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER immutable_{table}_{action.lower()} "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'immutable history'); END")
        return schema(db)


def logical_hash(db, tables):
    value = {}
    for table in tables:
        rows = []
        for row in db.execute(f'SELECT * FROM "{table}"'):
            rows.append([{"blob": item.hex()} if isinstance(item, bytes) else item
                         for item in row])
        value[table] = sorted(rows, key=canonical)
    return digest(canonical(value))


def checked_json(text, sha, *, workspace=False):
    require(isinstance(text, str) and STORE._digest(text) == sha,
            "CANONICAL_SHA_MISMATCH")
    value = STORE._loads(text)
    require((STORE._workspace(value) if workspace else STORE._canonical(value)) == text,
            "NON_CANONICAL_JSON")
    return value


def validate_databases(auth, projects):
    dbs = {"accounts": auth, "projects": projects}
    result = {}
    for kind, db in dbs.items():
        require([row[0] for row in db.execute("PRAGMA integrity_check")] == ["ok"],
                "SQLITE_INTEGRITY_FAILED")
        require(db.execute("PRAGMA foreign_key_check").fetchone() is None,
                "FOREIGN_KEY_FAILED")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        app_id = db.execute("PRAGMA application_id").fetchone()[0]
        require((version, app_id) == (1, 0 if kind == "accounts" else 0x4F434144),
                "DATABASE_IDENTITY_FAILED")
        require(schema(db) == expected_schema(kind), "SCHEMA_CONTRACT_FAILED")
        tables = [item[1] for item in schema(db) if item[0] == "table"]
        result[kind] = {
            "user_version": version, "application_id": app_id,
            "schema_sha256": digest(canonical(schema(db))),
            "counts": {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                       for table in tables},
            "logical_sha256": logical_hash(db, tables),
        }
    require([tuple(row) for row in auth.execute("SELECT * FROM auth_meta")] ==
            [("product", "opencad")], "AUTH_PRODUCT_FAILED")
    users = {}
    for row in auth.execute("SELECT * FROM users"):
        uid = row["user_id"]
        require(isinstance(uid, str) and uid.strip() and len(uid) <= 128,
                "INVALID_ACCOUNT_ID")
        AUTH._username(row["username"])
        AUTH._locale(row["locale"])
        require(row["admin"] in (0, 1) and row["enabled"] in (0, 1),
                "INVALID_ACCOUNT_FLAGS")
        if row["kind"] == "local":
            require(isinstance(row["salt"], bytes) and len(row["salt"]) == 16
                    and isinstance(row["verifier"], bytes) and len(row["verifier"]) == 32,
                    "INVALID_LOCAL_VERIFIER")
        else:
            require(row["kind"] == "external" and row["salt"] is None
                    and row["verifier"] is None, "INVALID_EXTERNAL_MAPPING")
        users[uid] = row["kind"]
    for row in auth.execute("SELECT * FROM sessions"):
        require(STORE._SHA.fullmatch(row["token_hash"]) and row["product"] == "opencad"
                and row["user_id"] in users and type(row["expires_at"]) is int
                and row["revoked"] in (0, 1), "INVALID_SESSION")
    heads = {}
    for row in projects.execute("SELECT * FROM projects"):
        STORE._identifier(row["project_id"], "project_id")
        require(row["owner"] in users, "OWNER_ACCOUNT_MAPPING_FAILED")
        require(row["head_revision"] >= 1, "INVALID_HEAD_REVISION")
        STORE._text(row["title"], "title")
        heads[row["project_id"]] = dict(row)
    snapshots = {}
    for row in projects.execute("SELECT * FROM snapshots"):
        checked_json(row["payload"], row["sha256"], workspace=True)
        snapshots[(row["project_id"], row["revision"])] = dict(row)
        STORE.ProjectStore._head(projects, row["project_id"], row["revision"])
    for pid, head in heads.items():
        revisions = sorted(rev for project, rev in snapshots if project == pid)
        require(revisions == list(range(1, head["head_revision"] + 1)),
                "SNAPSHOT_HISTORY_GAP")
        require(head["updated_at"] ==
                snapshots[(pid, head["head_revision"])]["committed_at"],
                "HEAD_TIME_MISMATCH")
        require(head["created_at"] == snapshots[(pid, 1)]["committed_at"],
                "CREATE_TIME_MISMATCH")
    receipt_revisions = []
    for row in projects.execute("SELECT * FROM receipts"):
        STORE._identifier(row["request_id"], "request_id")
        request = checked_json(row["request_payload"], row["request_sha256"])
        require(isinstance(request, dict), "INVALID_RECEIPT_REQUEST")
        require("request_id" not in request or request["request_id"] == row["request_id"],
                "RECEIPT_REQUEST_ID_MISMATCH")
        _, base_rev, base_sha = STORE._request(request)
        previous = snapshots.get((row["project_id"], row["revision"] - 1))
        require(previous is not None and base_rev == row["revision"] - 1
                and base_sha == previous["sha256"], "RECEIPT_BASE_MISMATCH")
        response = checked_json(row["response"], row["response_sha256"])
        require(isinstance(response, dict), "INVALID_RECEIPT_RESPONSE")
        STORE.ProjectStore._receipt(projects, row["project_id"], row["request_id"],
                                    row["request_payload"])
        require(row["committed_at"] ==
                snapshots[(row["project_id"], row["revision"])]["committed_at"],
                "RECEIPT_TIME_MISMATCH")
        receipt_revisions.append((row["project_id"], row["revision"]))
    require(sorted(receipt_revisions) == sorted(key for key in snapshots if key[1] > 1),
            "RECEIPT_HISTORY_GAP")
    creates = []
    for row in projects.execute("SELECT * FROM create_receipts"):
        STORE._identifier(row["request_id"], "request_id")
        pid = row["project_id"]
        require(row["owner"] == heads[pid]["owner"] and row["owner"] in users,
                "CREATE_OWNER_MAPPING_FAILED")
        request = checked_json(row["request_payload"], row["request_sha256"])
        expected = {"title": heads[pid]["title"],
                    "payload": STORE._loads(snapshots[(pid, 1)]["payload"])}
        require(request == expected, "CREATE_REQUEST_MISMATCH")
        checked_json(row["response"], row["response_sha256"])
        STORE.ProjectStore._create_receipt(projects, row["owner"], row["request_id"],
                                           row["request_payload"])
        creates.append(pid)
    require(sorted(creates) == sorted(heads), "CREATE_RECEIPT_HISTORY_GAP")
    return {"product": "opencad", "databases": result,
            "external_accounts": sum(kind == "external" for kind in users.values()),
            "owner_mapping_sha256": digest(canonical(
                sorted((pid, head["owner"]) for pid, head in heads.items())))}


@contextmanager
def source_connections(root):
    with ExitStack() as stack:
        directory = stack.enter_context(Directory(root))
        accounts = stack.enter_context(directory.child("accounts"))
        projects = stack.enter_context(directory.child("projects"))
        # Keep pinned read transactions through writer close, which otherwise
        # may checkpoint WAL on the last writable connection in a CLI process.
        auth_guard = accounts.database("opencad-auth.sqlite3", readonly=True)
        stack.callback(auth_guard.close)
        project_guard = projects.database("projects.sqlite3", readonly=True)
        stack.callback(project_guard.close)
        auth = accounts.database("opencad-auth.sqlite3", readonly=False)
        stack.callback(auth.close)
        project = projects.database("projects.sqlite3", readonly=False)
        stack.callback(project.close)
        yield directory, accounts, projects, auth, project, auth_guard, project_guard


def copy_database(source, directory, name):
    directory.write(name, b"")
    target = directory.database(name, readonly=False)
    try:
        source.backup(target, pages=128, sleep=0.01)
        require(target.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0,
                "DESTINATION_CHECKPOINT_FAILED")
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.fd)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory.fd)


def describe_files(directory, validation):
    result = {}
    for kind, name in FILES.items():
        data = directory.read(name)
        result[kind] = {**validation["databases"][kind], "file": name,
                        "sha256": digest(data), "bytes": len(data)}
    return result


def backup(source_root, package_root, evidence, *, checkpoint=lambda _: None,
           service_root_authorization=None):
    disjoint(absolute(source_root), absolute(package_root))
    source_root = local_path(source_root, service_root_authorization=service_root_authorization)
    package_root = local_path(package_root, service_root_authorization=service_root_authorization)
    disjoint(source_root, package_root)
    require(not package_root.exists(), "PACKAGE_ALREADY_EXISTS")
    evidence.event("backup_preflight", source_root=str(source_root),
                   package_root=str(package_root), lock_order=LOCK_ORDER,
                   authorization_parent=str(service_parent_authorization(
                       service_root_authorization)))
    with source_connections(source_root) as (
            root, accounts, projects, auth, project, auth_guard, project_guard):
        try:
            auth.execute("BEGIN IMMEDIATE")
            evidence.event("auth_locked")
            project.execute("BEGIN IMMEDIATE")
            evidence.event("both_locked")
            for guard in (auth_guard, project_guard):
                guard.execute("BEGIN")
                guard.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            checkpoint("both_locked")
            before = validate_databases(auth, project)
            evidence.event("backup_intent", validation=before)
            with fresh_directory(package_root) as package:
                package.write("intent.json", canonical({
                    "format": FORMAT, "source_root": str(source_root), "at": now()}))
                # The lock connections MUST NOT be the backup source connections.
                with ExitStack() as readers:
                    auth_ro = accounts.database("opencad-auth.sqlite3", readonly=True)
                    readers.callback(auth_ro.close)
                    project_ro = projects.database("projects.sqlite3", readonly=True)
                    readers.callback(project_ro.close)
                    copy_database(auth_ro, package, FILES["accounts"])
                    evidence.event("accounts_copied")
                    checkpoint("accounts_copied")
                    copy_database(project_ro, package, FILES["projects"])
                with ExitStack() as copied_connections:
                    a = package.database(FILES["accounts"], readonly=True)
                    copied_connections.callback(a.close)
                    p = package.database(FILES["projects"], readonly=True)
                    copied_connections.callback(p.close)
                    copied = validate_databases(a, p)
                require(before == copied == validate_databases(auth, project),
                        "LOCKED_SOURCE_DRIFT")
                root.unchanged()
                accounts.unchanged()
                projects.unchanged()
                package.unchanged()
                manifest = {
                    "format": FORMAT, "complete": True, "backup_id": uuid.uuid4().hex,
                    "at": now(), "lock_order": LOCK_ORDER, "source_root": str(source_root),
                    "source_fingerprint": source_fingerprint(), "validation": copied,
                    "databases": describe_files(package, copied),
                    "authorization_parent": str(service_parent_authorization(
                        service_root_authorization)),
                    "authorization_mode": ("fixture_default" if service_root_authorization is None
                                           else "explicit_operator_local_parent"),
                }
                evidence.event("manifest_intent", backup_id=manifest["backup_id"])
                checkpoint("manifest_intent")
                package.write("manifest.json", canonical(manifest))
                evidence.event("backup_complete", backup_id=manifest["backup_id"])
                return manifest
        finally:
            # BEGIN IMMEDIATE is lock-only: never commit a source-side transaction.
            primary = sys.exc_info()[1]
            for connection in (project, auth):
                if connection.in_transaction:
                    try:
                        connection.rollback()
                    except BaseException as secondary:
                        if primary is None:
                            primary = secondary
                        else:
                            retain_secondary(primary, secondary)
            if sys.exc_info()[1] is None and primary is not None:
                raise primary


def verify(package_root, evidence, *, service_root_authorization=None):
    package_root = local_path(package_root, service_root_authorization=service_root_authorization)
    evidence.event("verify_intent", package_root=str(package_root),
                   authorization_parent=str(service_parent_authorization(
                       service_root_authorization)))
    with Directory(package_root) as package:
        require(set(os.listdir(package.fd)) ==
                {"manifest.json", "intent.json", *FILES.values()}, "PACKAGE_FILE_SET_FAILED")
        raw = package.read("manifest.json")
        manifest = STORE._loads(raw.decode("utf-8"))
        require(canonical(manifest) == raw and isinstance(manifest, dict),
                "INVALID_MANIFEST")
        require(manifest.get("format") == FORMAT and manifest.get("complete") is True
                and manifest.get("lock_order") == LOCK_ORDER, "INCOMPLETE_OR_FOREIGN_PACKAGE")
        source_root = local_path(manifest["source_root"],
                                 service_root_authorization=service_root_authorization)
        disjoint(source_root, package_root)
        require(set(manifest["databases"]) == set(FILES), "PACKAGE_DATABASE_SET_FAILED")
        for kind, name in FILES.items():
            record = manifest["databases"][kind]
            require(record["file"] == name, "PACKAGE_FILENAME_FAILED")
            data = package.read(name)
            require(len(data) == record["bytes"] and digest(data) == record["sha256"],
                    "PACKAGE_BYTES_SHA_FAILED")
        with ExitStack() as stack:
            a = package.database(FILES["accounts"], readonly=True)
            stack.callback(a.close)
            p = package.database(FILES["projects"], readonly=True)
            stack.callback(p.close)
            validation = validate_databases(a, p)
        require(validation == manifest["validation"]
                and describe_files(package, validation) == manifest["databases"],
                "PACKAGE_LOGICAL_MANIFEST_FAILED")
        package.unchanged()
        evidence.event("verified", backup_id=manifest["backup_id"], validation=validation)
        return manifest


def restore(package_root, target_root, evidence, *, checkpoint=lambda _: None,
            service_root_authorization=None):
    disjoint(absolute(package_root), absolute(target_root))
    package_root = local_path(package_root, service_root_authorization=service_root_authorization)
    target_root = local_path(target_root, service_root_authorization=service_root_authorization)
    disjoint(package_root, target_root)
    require(not target_root.exists(), "TARGET_ALREADY_EXISTS")
    manifest = verify(package_root, evidence,
                      service_root_authorization=service_root_authorization)
    source_root = local_path(manifest["source_root"],
                             service_root_authorization=service_root_authorization)
    disjoint(source_root, target_root)
    evidence.event("restore_intent", target_root=str(target_root),
                   backup_id=manifest["backup_id"],
                   authorization_parent=str(service_parent_authorization(
                       service_root_authorization)))
    with Directory(package_root) as package:
        # Bind the validated bytes again immediately before copying.
        require(describe_files(package, manifest["validation"]) == manifest["databases"],
                "PACKAGE_CHANGED_AFTER_VERIFY")
        with fresh_directory(target_root) as target:
            target.write("recovery-intent.json", canonical({
                "backup_id": manifest["backup_id"], "at": now(),
                "policy": "revoke_all_existing_sessions"}))
            with target.create("accounts") as accounts, target.create("projects") as projects:
                for kind, destination, name in (
                    ("accounts", accounts, "opencad-auth.sqlite3"),
                    ("projects", projects, "projects.sqlite3"),
                ):
                    src = package.database(FILES[kind], readonly=True)
                    try:
                        copy_database(src, destination, name)
                    finally:
                        src.close()
                checkpoint("restore_copied")
                with ExitStack() as stack:
                    a = accounts.database("opencad-auth.sqlite3", readonly=False)
                    stack.callback(a.close)
                    p = projects.database("projects.sqlite3", readonly=True)
                    stack.callback(p.close)
                    require(validate_databases(a, p) == manifest["validation"],
                            "RESTORE_COPY_VALIDATION_FAILED")
                    a.execute("BEGIN IMMEDIATE")
                    try:
                        a.execute("UPDATE sessions SET revoked=1")
                        a.commit()
                    except BaseException as primary:
                        try:
                            a.rollback()
                        except BaseException as secondary:
                            retain_secondary(primary, secondary)
                        raise
                    checkpoint("sessions_revoked")
                    validation = validate_databases(a, p)
                    require(a.execute("SELECT COUNT(*) FROM sessions WHERE revoked<>1")
                            .fetchone()[0] == 0, "SESSION_REVOCATION_FAILED")
                    require(validation["databases"]["projects"] ==
                            manifest["validation"]["databases"]["projects"],
                            "RESTORED_PROJECT_DRIFT")
                    require(validation["databases"]["accounts"]["counts"] ==
                            manifest["validation"]["databases"]["accounts"]["counts"],
                            "RESTORED_ACCOUNT_COUNT_DRIFT")
                target.unchanged()
                accounts.unchanged()
                projects.unchanged()
            require(describe_files(package, manifest["validation"]) == manifest["databases"],
                    "ORIGINAL_PACKAGE_DRIFT")
            package.unchanged()
            evidence.event("restore_complete", validation=validation)
            receipt = {"format": FORMAT, "complete": True, "at": now(),
                       "backup_id": manifest["backup_id"], "target_root": str(target_root),
                       "sessions_policy": "all_existing_revoked", "validation": validation,
                       "authorization_parent": str(service_parent_authorization(
                           service_root_authorization)),
                       "entitlement_reconciliation": "REQUIRED_BEFORE_SERVICE_ADOPTION"}
            target.write("recovery-complete.json", canonical(receipt))
            return receipt


def execute(action, out, *, source_root=None, package_root=None, target_root=None,
            service_root_authorization=None):
    evidence = Evidence(out)
    before = source_fingerprint()
    report = {"action": action, "started_at": now(), "verdict": "FAIL", "rc": 1,
              "production_ready": False, "source_before": before,
              "first_cause": None, "evidence_errors": [], "artifact": None,
              "service_root_authorization": (os.fspath(service_root_authorization)
                                             if service_root_authorization is not None else None),
              "authorization_parent": None}
    try:
        evidence.event("started", action=action, source_fingerprint=before)
        report["authorization_parent"] = str(service_parent_authorization(
            service_root_authorization))
        evidence.event("scope_validated", authorization_parent=report["authorization_parent"],
                       mode="fixture_default" if service_root_authorization is None
                       else "explicit_operator_local_parent")
        require((source_root is not None) == (action == "backup"),
                "SOURCE_ARGUMENT_SCOPE")
        require((target_root is not None) == (action == "restore"),
                "TARGET_ARGUMENT_SCOPE")
        if action == "backup":
            report["artifact"] = backup(source_root, package_root, evidence,
                                        service_root_authorization=service_root_authorization)
        elif action == "verify":
            report["artifact"] = verify(package_root, evidence,
                                        service_root_authorization=service_root_authorization)
        elif action == "restore":
            report["artifact"] = restore(package_root, target_root, evidence,
                                         service_root_authorization=service_root_authorization)
        else:
            raise RecoveryError("INVALID_ACTION")
        require(source_fingerprint() == before, "TOOL_SOURCE_DRIFT")
        report["verdict"], report["rc"] = "PASS", 0
    except BaseException as exc:
        report["first_cause"] = failure_details(exc)
        # Exception text and DB rows may contain identities or credentials.
        try:
            evidence.event("failed", first_cause=report["first_cause"])
        except BaseException as secondary:
            report["evidence_errors"].append(type(secondary).__name__)
    finally:
        report["ended_at"] = now()
        try:
            report["source_after"] = source_fingerprint()
            report["source_drift"] = sorted(path for path in before
                                          if before[path] != report["source_after"].get(path))
        except BaseException as secondary:
            report["source_after"], report["source_drift"] = None, ["UNKNOWN_NOT_VERIFIED"]
            report["evidence_errors"].append(type(secondary).__name__)
        if report["source_drift"] or report["evidence_errors"]:
            report["rc"], report["verdict"] = 1, "FAIL"
            if report["first_cause"] is None:
                report["first_cause"] = {"code": "FINAL_VERIFICATION_FAILED",
                                         "type": "RecoveryError"}
        report["completion_marker"] = f"SERVICE_RECOVERY verdict={report['verdict']}"
        try:
            evidence.directory.write("result.json", canonical(report))
        except BaseException as secondary:
            report["evidence_errors"].append(type(secondary).__name__)
            report["rc"] = 1
            report["verdict"] = "FAIL"
            report["completion_marker"] = "SERVICE_RECOVERY verdict=FAIL"
            if report["first_cause"] is None:
                report["first_cause"] = failure_details(secondary)
            # Preserve first cause even if the evidence filesystem itself failed.
            print("SERVICE_RECOVERY_EVIDENCE_FAILURE " + canonical({
                "first_cause": report["first_cause"],
                "evidence_errors": report["evidence_errors"]}).decode(), file=sys.stderr)
            try:
                evidence.directory.write("terminal-failure.json", canonical(report))
            except BaseException as terminal_error:
                print("SERVICE_RECOVERY_TERMINAL_EVIDENCE_FAILURE type=" +
                      type(terminal_error).__name__, file=sys.stderr)
        try:
            evidence.close()
        except BaseException as secondary:
            report["rc"], report["verdict"] = 1, "FAIL"
            report["completion_marker"] = "SERVICE_RECOVERY verdict=FAIL"
            if report["first_cause"] is None:
                report["first_cause"] = failure_details(secondary)
            print("SERVICE_RECOVERY_CLOSE_FAILURE type=" + type(secondary).__name__,
                  file=sys.stderr)
    print(f"SERVICE_RECOVERY verdict={report['verdict']} rc={report['rc']}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("backup", "verify", "restore"), required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--service-root-authorization", metavar="ABS_LOCAL_PARENT",
                        help="Existing narrow local parent explicitly authorized by the operator; "
                             "omitting this retains the fixture-only default")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return execute(args.action, args.out, source_root=args.source_root,
                   package_root=args.package_root, target_root=args.target_root,
                   service_root_authorization=args.service_root_authorization)["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
