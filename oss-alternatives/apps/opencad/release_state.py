"""Atomic drawing checkpoints and startup recovery without live-edit loss.

Preparation requires a running server that already dispatches release commands.
Older processes need a separately verified, quiesced migration before restart.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path


MARKER = ".opencad-release-pending.json"
CURRENT = ".opencad-release-current.json"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def error(workspace, code, detail):
    # Resolve the owning server's class, including __main__ and test loaders.
    api_error = workspace.state.__func__.__globals__["ApiError"]
    return api_error(code, detail, status=409)


def safe_file(workspace, raw):
    path = Path(raw)
    root = Path(workspace.out_root).resolve()
    if not path.is_absolute() or path.parent != root or path.is_symlink():
        raise error(workspace, "unsafe_checkpoint", "checkpoint must be a direct non-symlink file in state root")
    return path


def assert_writable(workspace, target):
    """Keep ordinary exports from destroying durable recovery metadata/data."""
    path = Path(target)
    root = Path(workspace.out_root).resolve()
    if path.parent == root and path.name.startswith((".opencad-release-", ".opencad-current-")):
        raise error(workspace, "protected_checkpoint", "release recovery files cannot be overwritten")
    protected = []
    for marker in (root / MARKER, root / CURRENT):
        if marker.is_symlink():
            raise error(workspace, "unsafe_checkpoint", "release checkpoint cannot be a symlink")
        if marker.exists():
            checkpoint = json.loads(marker.read_text(encoding="utf-8"))
            protected.extend((marker, safe_file(workspace, checkpoint["path"])))
    protected.extend(root.glob(".opencad-release-restored-*.json"))
    for saved in protected:
        if path == saved or (path.exists() and saved.exists() and path.samefile(saved)):
            raise error(workspace, "protected_checkpoint", "release recovery files cannot be overwritten")


def write_new(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".opencad-checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Publish complete bytes without replacing another process's checkpoint.
        os.link(temporary, path)
        sync_directory(path.parent)
    finally:
        os.unlink(temporary)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install_guards(workspace):
    """Guard this old process even though its class predates maintenance."""
    workspace._release_pending = True
    for name in ("command", "set_locale", "set_standard", "open_doc", "_mutate",
                 "save", "export_step", "export_dxf", "export_pdf"):
        if not hasattr(workspace, name):
            continue
        original = getattr(workspace, name)

        def guarded(*args, _original=original, **kwargs):
            with workspace._lock:
                if workspace._release_pending:
                    raise error(workspace, "maintenance", "drawing saved; service restart is in progress")
                return _original(*args, **kwargs)

        setattr(workspace, name, guarded)


def prepare(workspace, args):
    with workspace._lock:
        if set(args).difference({"path", "expected_document_sha256", "expected_locale", "expected_standard"}):
            raise error(workspace, "bad_release_arguments", "unknown release arguments")
        before = workspace.state()
        if args.get("expected_document_sha256") != digest(before["doc"]):
            raise error(workspace, "workspace_changed", "drawing changed; restart cancelled before save")
        for key in ("locale", "standard"):
            if "expected_" + key in args and args["expected_" + key] != before[key]:
                raise error(workspace, "workspace_changed", key + " changed; restart cancelled")
        path = safe_file(workspace, args.get("path", ""))
        marker = Path(workspace.out_root) / MARKER
        if marker.exists() or marker.is_symlink() or path.exists():
            raise error(workspace, "checkpoint_exists", "refusing to overwrite a release checkpoint")
        saved = workspace.save(str(path))
        if path.read_bytes() != before["doc"].encode("utf-8"):
            raise error(workspace, "checkpoint_mismatch", "saved drawing differs from exact L1 bytes")
        checkpoint = {
            "id": str(uuid.uuid4()), "doc": before["doc"], "locale": before["locale"],
            "standard": before["standard"], "document_sha256": digest(before["doc"]),
            "path": str(path),
        }
        install_guards(workspace)
        write_new(marker, checkpoint)
        return {**saved, "checkpoint": {key: checkpoint[key] for key in (
            "id", "document_sha256", "locale", "standard")}}


def load_checkpoint(workspace, checkpoint):
    path = safe_file(workspace, checkpoint["path"])
    text = path.read_text(encoding="utf-8")
    if digest(text) != checkpoint["document_sha256"] or text != checkpoint.get("doc", text):
        raise error(workspace, "checkpoint_mismatch", "saved drawing bytes changed; refusing restoration")
    doc = workspace.docmodel.load_l1(str(path))
    problems = workspace.docmodel.load_blocking_problems(doc)
    if problems:
        raise error(workspace, "invalid_checkpoint", "; ".join(problems[:8]))
    workspace._validated_standard(checkpoint["standard"], list(workspace.standards.load_profiles()))
    previous = (workspace.doc, workspace.standard, workspace.catalog.active_locale())
    try:
        change = workspace._activate_locale(checkpoint["locale"])
        workspace.doc = doc
        workspace._apply_standard(checkpoint["standard"])
        if workspace.l1_text() != text:
            raise error(workspace, "checkpoint_mismatch", "restored drawing differs from exact L1 bytes")
    except Exception:
        workspace.doc, workspace.standard, locale = previous
        workspace._activate_locale(locale)
        raise
    workspace.locale_change = change
    workspace.undo_stack.clear()
    workspace.redo_stack.clear()
    workspace._mesh_cache = None
    return workspace.state()


def recover_pending(workspace):
    marker = Path(workspace.out_root) / MARKER
    if marker.is_symlink():
        raise error(workspace, "unsafe_checkpoint", "pending checkpoint cannot be a symlink")
    if not marker.exists():
        marker = Path(workspace.out_root) / CURRENT
        if marker.is_symlink():
            raise error(workspace, "unsafe_checkpoint", "current checkpoint cannot be a symlink")
        if not marker.exists():
            return
    checkpoint = json.loads(marker.read_text(encoding="utf-8"))
    if str(uuid.UUID(checkpoint["id"])) != checkpoint["id"]:
        raise error(workspace, "invalid_checkpoint", "checkpoint identifier is not a canonical UUID")
    load_checkpoint(workspace, checkpoint)
    install_guards(workspace)
    workspace._release_startup_checkpoint = checkpoint
    workspace._release_startup_marker = marker


def acknowledge_startup(workspace):
    """Consume recovery only after the server has successfully bound its socket."""
    with workspace._lock:
        checkpoint = getattr(workspace, "_release_startup_checkpoint", None)
        if checkpoint is None:
            return
        marker = workspace._release_startup_marker
        if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != checkpoint:
            raise error(workspace, "checkpoint_mismatch", "pending checkpoint changed during startup")
        if marker.name == CURRENT:
            workspace._release_startup_checkpoint = None
            workspace._release_pending = False
            return
        archive = marker.with_name(".opencad-release-restored-" + checkpoint["id"] + ".json")
        if archive.is_symlink():
            raise error(workspace, "unsafe_checkpoint", "restored checkpoint archive cannot be a symlink")
        if archive.exists():
            if archive.read_bytes() != marker.read_bytes():
                raise error(workspace, "checkpoint_exists", "different restored checkpoint archive exists")
        else:
            os.link(marker, archive)
        current = marker.with_name(CURRENT)
        if current.is_symlink():
            raise error(workspace, "unsafe_checkpoint", "current checkpoint cannot be a symlink")
        temporary = marker.with_name(".opencad-current-" + str(uuid.uuid4()))
        try:
            os.link(marker, temporary)
            os.replace(temporary, current)
        finally:
            if temporary.exists():
                temporary.unlink()
        # Recovery must remain durable even if unlink acknowledgement and its
        # compensating link both fail. Pending takes priority over current.
        sync_directory(marker.parent)
        try:
            marker.unlink()
            sync_directory(marker.parent)
        except Exception:
            # A failed acknowledgement must leave recovery available on retry.
            if not marker.exists():
                os.link(archive, marker)
            raise
        workspace._release_startup_checkpoint = None
        workspace._release_pending = False


def restore_empty(workspace, args):
    with workspace._lock:
        if getattr(workspace, "_release_pending", False):
            raise error(workspace, "maintenance", "prepared drawing cannot be replaced during restart")
        if set(args) != {"path", "document_sha256", "locale", "standard", "expected_document_sha256"}:
            raise error(workspace, "bad_release_arguments", "unexpected restoration arguments")
        if args["expected_document_sha256"] != digest(workspace.l1_text()):
            raise error(workspace, "workspace_changed", "current drawing changed; refusing restoration")
        if (digest(workspace.l1_text()) != workspace._release_initial_document_sha256
                or workspace.undo_stack or workspace.redo_stack):
            raise error(workspace, "workspace_not_empty", "refusing to overwrite a live edited workspace")
        return load_checkpoint(workspace, args)
