# SPDX-License-Identifier: Apache-2.0
"""Read/build-only adapter; authorization, CAS and undo remain gateway-owned.

read_surfaces(workspace, body) never writes. prepare_surfaces(workspace, body)
returns SurfaceCandidate(before_document_sha256, doc, response), never publishes.
No artifact path or backend traceback is exposed to HTTP clients.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
from pathlib import Path

from cadcore.kernel.surfaces import SurfaceKernel, SurfaceExecutionError, validate_recipe, validate_seam
from server import ApiError

NAMESPACE = "x_industrial_surfaces"
SCHEMA = 1


@dataclass(frozen=True)
class SurfaceCandidate:
    before_document_sha256: str
    doc: object
    response: dict


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fields(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ApiError("bad_arguments", "Unexpected or missing surface fields", status=400)


def _state(doc):
    if NAMESPACE not in doc.extra:
        return {"schema": SCHEMA, "recipe": None}
    value = doc.extra[NAMESPACE]
    _fields(value, {"schema", "recipe"}, {"schema", "recipe"})
    if type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise ValueError("unsupported persisted surface schema")
    return {"schema": SCHEMA, "recipe": validate_recipe(value["recipe"])}


def _sources():
    import cadcore.kernel.surfaces as module
    source = Path(module.__file__).resolve()
    return {name: hashlib.sha256(source.with_name(name).read_bytes()).hexdigest()
            for name in ("surfaces.py", "surfaces_worker.py")}


def _kernel(workspace):
    root = Path(workspace.out_root).resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[3]):
        raise ValueError("surface runtime output must be outside the source repository")
    return SurfaceKernel(evidence_dir=root / "surface-jobs")


def _public(result):
    properties = copy.deepcopy(result["properties"])
    mesh = copy.deepcopy(result["mesh"])
    mesh["properties"] = {**properties, "bbox_nm": [
        round(value * 1_000_000) for value in properties["bbox_mm"]],
        "volume_nm3": (properties["volume_mm3"] * 1e18
                       if properties["volume_mm3"] is not None else None)}
    return {"properties": properties, "mesh": mesh, "backend": result["backend"],
            "seams": result["seams"], "offset_checks": result["offset_checks"],
            "roundtrip": result["roundtrip"],
            "artifacts": {fmt: {"sha256": item["sha256"], "bytes": item["bytes"]}
                          for fmt, item in result["artifacts"].items()},
            "kernel_source_sha256": {
                Path(path).name: digest
                for path, digest in result["execution"]["sources_before"].items()}}


def _response(state, digest, result=None):
    return {"ok": True, "schema": SCHEMA, "document_sha256": digest,
            "surface": copy.deepcopy(state), "empty": state["recipe"] is None,
            "result": copy.deepcopy(result)}


def _run(operation):
    try:
        return operation()
    except ApiError:
        raise
    except SurfaceExecutionError as exc:
        worker = exc.details.get("worker_result") or {}
        raise ApiError("surface_failed", str(exc), status=422, reason=exc.reason,
                       first_cause=worker.get("error", str(exc))) from exc
    except ValueError as exc:
        raise ApiError("bad_surface_arguments", str(exc), status=400) from exc


def _capture(workspace):
    with workspace._lock:
        before = workspace.l1_text()
        doc = workspace.docmodel.document_from_l1(before)
    return before, doc


def read_surfaces(workspace, body=None):
    """GET body {} reads persisted recipe; POST read-only check uses action.

    check_seam: {action: "check_seam", left, right, seam}. A passed=false
    measurement is not an accepted sew and must not create a project revision.
    Responses are tied to the captured document SHA; the gateway handles reads
    superseded by later project revisions. Neither API writes caches/history.
    """
    return _run(lambda: _read(workspace, {} if body is None else body))


def _read(workspace, body):
    before, doc = _capture(workspace)
    state = _state(doc)
    kernel = _kernel(workspace)
    if body == {}:
        result = _public(kernel.build(state["recipe"])) if state["recipe"] is not None else None
        return _response(state, _sha(before), result)
    _fields(body, {"action", "left", "right", "seam"}, {"action", "left", "right", "seam"})
    if body["action"] != "check_seam":
        raise ApiError("bad_arguments", "Read action must be check_seam", status=400)
    left, right = validate_recipe(body["left"]), validate_recipe(body["right"])
    seam = validate_seam(body["seam"])
    result = kernel.check_seam(left, right, **seam)
    return {"ok": True, "read_only": True, "document_sha256": _sha(before),
            "seam": result["seam"], "backend": result["backend"]}


def prepare_surfaces(workspace, body):
    """Return an independently validated candidate for {recipe: surface_recipe}.

    The caller MUST compare before_document_sha256 against the current document
    while publishing, then use its durable project CAS. Frozen dataclass fields
    do not make doc/response deeply immutable; treat both as request-private.
    """
    return _run(lambda: _prepare(workspace, body))


def _prepare(workspace, body):
    _fields(body, {"recipe"}, {"recipe"})
    recipe = validate_recipe(body["recipe"])
    with workspace._lock:
        if getattr(workspace, "_release_pending", False):
            raise ApiError("maintenance", "Drawing saved; restart in progress", status=409)
    sources = _sources()
    before, candidate = _capture(workspace)
    # Fail loudly on corrupted existing parameters rather than overwrite them.
    _state(candidate)
    state = {"schema": SCHEMA, "recipe": recipe}
    candidate.extra[NAMESPACE] = state
    result = _public(_kernel(workspace).build(recipe))
    text = workspace.docmodel.document_to_l1(candidate)
    reopened = workspace.docmodel.document_from_l1(text)
    if _state(reopened) != state or workspace.docmodel.document_to_l1(reopened) != text:
        raise ValueError("surface candidate did not preserve exact document bytes on reopen")
    problems = workspace.docmodel.load_blocking_problems(reopened)
    if problems:
        raise ValueError("invalid surface candidate: %s" % "; ".join(problems[:8]))
    response = _response(state, _sha(text), result)
    if _sources() != sources:
        raise ApiError("surface_failed", "Kernel source changed during preparation", status=422,
                       reason="source_drift")
    return SurfaceCandidate(_sha(before), reopened, response)
