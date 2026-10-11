"""Read-only DB-snapshot -> bounded offscreen PNG adapter.

The gateway must authenticate/authorize first, then call
handler(bind_snapshot(workspace, head), body). This module never opens a path,
fetches a URL, writes a document, commits a revision or changes credentials.
RenderReply has the same four attributes consumed by secure_web.Handler._reply.
GPU configuration is process-owner supplied, never a request option.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import re

from render_service import (InvalidInput, MAX_TRIANGLES, MAX_VERTICES, RenderError,
                            RenderService, project_mesh)

SHA = re.compile(r"[0-9a-f]{64}\Z")
VIEWS = ("front", "top", "right", "isometric")
SERVICE = RenderService()  # Explicit configure() opt-in; no ambient privilege calls.


class SnapshotError(RenderError):
    code = "render_snapshot_invalid"


class RevisionMismatch(RenderError):
    code = "revision_conflict"
    http_status = 409


class EmptyModel(RenderError):
    code = "no_solid_feature"
    http_status = 422


@dataclass
class RenderReply:
    status: int = 200
    payload: object = b""
    content_type: str = "image/png"
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotWorkspace:
    workspace: object
    revision: int
    snapshot_sha256: str
    document: str


def configure(*, enable_gpu=False, cuda_device=0):
    """Owner-only startup configuration. Do not call with HTTP body fields."""
    global SERVICE
    if type(enable_gpu) is not bool or type(cuda_device) is not int or not 0 <= cuda_device <= 63:
        raise ValueError("explicit GPU boolean and CUDA ordinal 0..63 required")
    SERVICE = RenderService(enable_gpu=enable_gpu, cuda_device=cuda_device)


def bind_snapshot(workspace, head):
    """Bind one verified DB head, not client metadata or a shared live workspace."""
    if type(head) is not dict or type(head.get("revision")) is not int or not 1 <= head["revision"] < 2**63:
        raise SnapshotError("trusted DB revision required")
    digest = head.get("sha256")
    snapshot = head.get("snapshot")
    if not isinstance(digest, str) or not SHA.fullmatch(digest) or type(snapshot) is not dict:
        raise SnapshotError("trusted DB snapshot SHA required")
    if set(snapshot) != {"doc", "undo", "redo", "standard"} or type(snapshot["doc"]) is not str:
        raise SnapshotError("DB snapshot shape invalid")
    try:
        canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False).encode("utf8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SnapshotError("cannot fingerprint DB snapshot") from exc
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise SnapshotError("DB snapshot SHA mismatch")
    with workspace._lock:
        if _snapshot(workspace) != snapshot:
            raise SnapshotError("workspace is not the exact DB snapshot")
    return SnapshotWorkspace(workspace, head["revision"], digest, snapshot["doc"])


def _snapshot(workspace):
    return {"doc": workspace.l1_text(), "undo": list(workspace.undo_stack),
            "redo": list(workspace.redo_stack), "standard": workspace.standard}


def _options(body):
    allowed = {"camera", "width", "height", "backend", "allow_cpu_fallback",
               "expected_revision", "expected_sha", "request_id"}
    if type(body) is not dict or set(body) - allowed:
        raise InvalidInput("unknown render fields; mesh/path/url/shader not accepted")
    camera = body.get("camera", "isometric")
    if camera not in VIEWS:
        raise InvalidInput("camera must be front, top, right or isometric")
    # Validate cheap options before any CAD work, using a fixed nonempty mesh.
    from render_service import validate_request
    options = {name: body[name] for name in
               ("width", "height", "backend", "allow_cpu_fallback") if name in body}
    validate_request(dict(options, vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                          triangles=[[0, 1, 2]]))
    if ("expected_revision" in body) != ("expected_sha" in body):
        raise InvalidInput("expected_revision and expected_sha must be supplied together")
    if "expected_revision" in body:
        if type(body["expected_revision"]) is not int or not 1 <= body["expected_revision"] < 2**63:
            raise InvalidInput("expected_revision must be a positive integer")
        if not isinstance(body["expected_sha"], str) or not SHA.fullmatch(body["expected_sha"]):
            raise InvalidInput("expected_sha must be a lowercase snapshot SHA256")
    if "request_id" in body and (not isinstance(body["request_id"], str) or
                                 not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["request_id"])):
        raise InvalidInput("invalid request_id; render never commits a receipt")
    return camera, options


def _merge_mesh(value, document_sha):
    if type(value) is not dict or value.get("ok") is not True:
        raise SnapshotError("CAD mesh operation failed")
    if value.get("document_sha256") != document_sha:
        raise SnapshotError("mesh/document SHA mismatch")
    solids = value.get("solids")
    if type(solids) is not list:
        raise SnapshotError("CAD mesh solids shape invalid")
    if not solids:
        raise EmptyModel("the selected DB snapshot has no solid")
    vertices, triangles = [], []
    for solid in solids:
        if type(solid) is not dict:
            raise SnapshotError("invalid solid mesh")
        points, faces = solid.get("vertices_mm"), solid.get("triangles")
        if type(points) is not list or type(faces) is not list or not points or not faces:
            raise SnapshotError("nonempty CAD mesh required")
        if len(vertices) + len(points) > MAX_VERTICES or len(triangles) + len(faces) > MAX_TRIANGLES:
            raise InvalidInput("combined CAD mesh exceeds renderer budget")
        offset = len(vertices)
        for face in faces:
            if (type(face) is not list or len(face) != 3 or
                    any(type(i) is not int or not 0 <= i < len(points) for i in face)):
                raise SnapshotError("invalid CAD mesh indices")
            triangles.append([offset + i for i in face])
        vertices.extend(points)
    return {"vertices_mm": vertices, "triangles": triangles}


def handler(workspace, body):
    """Return RenderReply PNG or structured error, without persisting anything.

    ``workspace`` MUST be the result of bind_snapshot(). Unbound legacy/shared
    workspaces are refused. The caller owns auth, CSRF and project selection.
    """
    try:
        if not isinstance(workspace, SnapshotWorkspace):
            raise SnapshotError("bind an authorized DB head before rendering")
        camera, options = _options(body)
        if "expected_revision" in body and (
                body["expected_revision"], body["expected_sha"]) != (
                workspace.revision, workspace.snapshot_sha256):
            raise RevisionMismatch("requested revision no longer matches selected DB head")
        original = workspace.workspace
        with original._lock:
            before = _snapshot(original)
            snapshot_sha = hashlib.sha256(json.dumps(
                before, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False).encode("utf8")).hexdigest()
            if before["doc"] != workspace.document or snapshot_sha != workspace.snapshot_sha256:
                raise SnapshotError("bound snapshot changed before render")
            document_sha = hashlib.sha256(workspace.document.encode("utf8")).hexdigest()
            try:
                mesh = _merge_mesh(original.mesh(), document_sha)
            finally:
                if _snapshot(original) != before:
                    raise SnapshotError("read-only tessellation changed document/history")
        payload = dict(project_mesh(mesh, view=camera), **options)
        result = SERVICE.render(payload)
        png = base64.b64decode(result["image_png_base64"], validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(png).hexdigest() != result["png_sha256"]:
            raise SnapshotError("renderer PNG digest mismatch")
        headers = {
            "X-OpenCAD-Revision": str(workspace.revision),
            "X-OpenCAD-Snapshot-SHA256": workspace.snapshot_sha256,
            "X-OpenCAD-Document-SHA256": document_sha,
            "X-OpenCAD-Model-SHA256": document_sha,
            "X-OpenCAD-Geometry-SHA256": result["geometry_sha256"],
            "X-OpenCAD-Frame-SHA256": result["frame_sha256"],
            "X-OpenCAD-PNG-SHA256": result["png_sha256"],
            "X-OpenCAD-Render-Backend": result["backend"],
            "X-OpenCAD-Render-Status": result["status"],
            "X-OpenCAD-Render-Camera": camera,
            "X-OpenCAD-Render-Elapsed-S": f'{result["elapsed_s"]:.6f}',
            "X-OpenCAD-Hardware": "true" if result["renderer"]["hardware"] else "false",
            "Cache-Control": "no-store",
        }
        renderer = result["renderer"]["renderer"]
        headers["X-OpenCAD-Renderer"] = renderer.encode("ascii", "backslashreplace").decode("ascii")
        if result["fallback_reason"]:
            headers["X-OpenCAD-Fallback-Reason"] = result["fallback_reason"].encode(
                "ascii", "backslashreplace").decode("ascii")
        if any("\r" in v or "\n" in v for v in headers.values()):
            raise SnapshotError("unsafe renderer header")
        return RenderReply(payload=png, headers=headers)
    except RenderError as exc:
        return RenderReply(exc.http_status, {"ok": False, "error": exc.code, "detail": str(exc)},
                           "application/json; charset=utf-8")
