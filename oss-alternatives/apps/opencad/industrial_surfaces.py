# SPDX-License-Identifier: Apache-2.0
"""Read/build-only adapter; authorization, CAS and undo remain gateway-owned.

read_surfaces(workspace, body) never writes. prepare_surfaces(workspace, body)
returns SurfaceCandidate(before_document_sha256, doc, response), never publishes.
The publisher MUST call candidate.verify_integrity() before consuming it.
No artifact path or backend traceback is exposed to HTTP clients.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from cadcore import docmodel
from cadcore.errors import CadError
from cadcore.kernel.surfaces import SurfaceKernel, SurfaceExecutionError, validate_recipe, validate_seam
from server import ApiError

NAMESPACE = "x_industrial_surfaces"
SCHEMA = 1


@dataclass(frozen=True)
class SurfaceCandidate:
    before_document_sha256: str
    doc: object
    response: dict
    document_bytes: bytes | None = None
    creation_envelope_sha256: str | None = None

    def __post_init__(self):
        # init=True preserves both anchors when dataclasses.replace is used.
        if self.creation_envelope_sha256 is None:
            try:
                if self.document_bytes is None:
                    object.__setattr__(self, "document_bytes",
                                       docmodel.document_to_l1(self.doc).encode("utf-8"))
                object.__setattr__(self, "creation_envelope_sha256",
                                   _json_sha(_candidate_envelope(self)))
            except (ApiError, CadError, ValueError, TypeError, OverflowError,
                    RecursionError, AttributeError) as exc:
                raise _candidate_changed(exc) from exc

    def verify_integrity(self):
        """Reject changes since creation, including nested data and replace().

        This is an in-process handoff seal, not authentication against malicious
        server code able to forge/reset the creation anchors. Publication/CAS
        must consume the verified request-private candidate without mutation.
        """
        try:
            if (not _is_sha(self.creation_envelope_sha256)
                    or _json_sha(_candidate_envelope(self)) != self.creation_envelope_sha256):
                raise ValueError("surface candidate creation envelope changed")
        except (ApiError, CadError, ValueError, TypeError, OverflowError,
                RecursionError, AttributeError) as exc:
            raise _candidate_changed(exc) from exc


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_sha(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value):
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _candidate_changed(exc):
    return ApiError("candidate_changed", "Surface candidate changed after preparation",
                    status=422, reason="candidate_changed", first_cause=str(exc))


def _candidate_envelope(candidate):
    state = validate_document(candidate.doc)
    raw = docmodel.document_to_l1(candidate.doc).encode("utf-8")
    if type(candidate.document_bytes) is not bytes or raw != candidate.document_bytes:
        raise ValueError("surface candidate canonical document bytes changed")
    if not _is_sha(candidate.before_document_sha256):
        raise ValueError("surface candidate base must be a SHA256")
    digest = hashlib.sha256(raw).hexdigest()
    response = candidate.response
    _fields(response, {"ok", "schema", "document_sha256", "surface", "empty", "result"},
            {"ok", "schema", "document_sha256", "surface", "empty", "result"})
    if (response["ok"] is not True or type(response["schema"]) is not int
            or response["schema"] != SCHEMA or response["empty"] is not False
            or state["recipe"] is None or response["document_sha256"] != digest
            or _json_sha(response["surface"]) != _json_sha(state)):
        raise ValueError("surface candidate response/document domain binding changed")
    result = response["result"]
    if (not isinstance(result, dict) or not isinstance(result.get("properties"), dict)
            or not isinstance(result.get("mesh"), dict)):
        raise ValueError("surface candidate requires measured geometry")
    return {"base_sha256": candidate.before_document_sha256,
            "document_sha256": digest, "response_sha256": _json_sha(response),
            "domain": {"namespace": NAMESPACE, "schema": SCHEMA,
                       "recipe_sha256": _json_sha(state["recipe"]),
                       "measurement_sha256": _json_sha(result)}}


def _fields(value, allowed, required):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ApiError("bad_arguments", "Unexpected or missing surface fields", status=400)


def _state(doc):
    if not isinstance(doc, docmodel.Document) or not isinstance(doc.extra, dict):
        raise ValueError("surface validation requires a Document with object extra")
    if NAMESPACE not in doc.extra:
        return {"schema": SCHEMA, "recipe": None}
    value = doc.extra[NAMESPACE]
    _fields(value, {"schema", "recipe"}, {"schema", "recipe"})
    if type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise ValueError("unsupported persisted surface schema")
    return {"schema": SCHEMA, "recipe": validate_recipe(value["recipe"])}


def validate_document(doc):
    """Return detached surface state or raise ApiError on schema/recipe defects.

    An absent namespace is valid and returns recipe=None. A present namespace
    requires schema=1 (never bool), exactly schema/recipe, and a strict finite
    bounded recipe. This performs no geometry build or publication and does not
    certify other domains or OCC topology; the gateway owns domain admission.
    """
    return _run(lambda: _state(doc))


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
    while publishing, call verify_integrity() unconditionally before consuming
    doc/response, then use its durable project CAS. The creation-time seal binds
    canonical document bytes, base SHA, full response and measured surface data.
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
