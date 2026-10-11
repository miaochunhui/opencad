"""Pure Web assembly dispatch: read measured data or prepare an uncommitted L1.

Authentication, ownership, request receipts and atomic ProjectStore CAS belong
to the gateway. This module never changes a workspace, writes a saved document,
accepts server paths, or acknowledges a durable commit.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path

from cadcore.assembly import (
    Assembly, Component, Mate, PartReference, Transform, bom, evaluate_assembly,
    solve_mates,
)
from cadcore.docmodel import document_from_l1, document_to_l1, load_blocking_problems
from cadcore.errors import CadError
from cadcore.kernel.subproc import SubprocessKernel

__all__ = ["ASSEMBLY_KEY", "READ_ACTIONS", "WRITE_ACTIONS", "AssemblyCandidate",
           "read_assembly", "prepare_assembly"]

ASSEMBLY_KEY = "industrial_assembly_v1"
READ_ACTIONS = frozenset(("state", "mesh", "collisions", "bom", "export"))
WRITE_ACTIONS = frozenset(("import_part", "put_component", "put_mate",
                           "remove_component", "remove_mate", "solve",
                           "reopen", "checkpoint"))
BASE_FIELDS = frozenset(("expected_revision", "expected_sha", "request_id"))
_FIELDS = {
    "state": set(), "mesh": set(), "collisions": set(), "bom": set(), "export": set(),
    "import_part": {"part", "component"},
    "put_component": {"component"}, "put_mate": {"mate"},
    "remove_component": {"component_id"}, "remove_mate": {"mate_id"},
    "solve": set(), "reopen": {"assembly_json"}, "checkpoint": set(),
}


def _error(reason, detail):
    return CadError(detail, reason=reason)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _arguments(body, actions):
    if (not isinstance(body, dict) or not isinstance(body.get("action"), str)
            or body["action"] not in actions):
        raise _error("bad_assembly_action", "unknown read/write assembly action")
    action = body["action"]
    keys = set(body) - BASE_FIELDS
    if keys != {"action"} | _FIELDS[action]:
        raise _error("bad_assembly_arguments", "unexpected or missing assembly arguments")
    if actions is READ_ACTIONS and set(body) & BASE_FIELDS:
        raise _error("bad_assembly_arguments", "read actions do not accept commit fields")
    return action


def _document(workspace):
    # A gateway workspace or a standalone Document can be supplied. Never use
    # its mutable sketch accessor or change its history while constructing L1.
    source = workspace.l1_text() if hasattr(workspace, "l1_text") else document_to_l1(workspace)
    doc = document_from_l1(source)
    problems = load_blocking_problems(doc)
    if problems:
        raise _error("invalid_document", "; ".join(problems[:8]))
    assembly = None if ASSEMBLY_KEY not in doc.extra else Assembly.from_json(
        json.dumps(doc.extra[ASSEMBLY_KEY], ensure_ascii=True, allow_nan=False))
    return source, doc, assembly


@contextmanager
def _kernel(workspace, supplied):
    if supplied is not None:
        yield supplied
        return
    root = getattr(workspace, "out_root", None)
    if root is None:
        raise _error("assembly_kernel_required", "standalone documents need an explicit kernel")
    kernel = SubprocessKernel(work_dir=Path(root) / "assembly-kernel", keep_work_dir=True,
                              extra_env={"PYTHONDONTWRITEBYTECODE": "1"})
    try:
        yield kernel
    finally:
        kernel.close()


def _response(source, assembly, evaluation=None, solver=None):
    assembly_sha = _sha(assembly.to_json()) if assembly else None
    if evaluation is not None:
        evaluation = {**evaluation, "assembly_sha256": assembly_sha,
                      "document_sha256": _sha(source)}
    return {
        "ok": True, "present": assembly is not None,
        "doc": source,
        "document_sha256": _sha(source),
        "assembly_sha256": assembly_sha,
        "assembly_json": assembly.to_json() if assembly else None,
        "assembly": assembly.as_dict() if assembly else None,
        "bom": bom(assembly) if assembly else [],
        "evaluation": evaluation, "solver": solver,
    }


def read_assembly(workspace, body, *, kernel=None):
    """GET /api/assembly?action=...; current document and history stay untouched."""
    action = _arguments(body, READ_ACTIONS)
    source, _, assembly = _document(workspace)
    measured = None
    if assembly is not None and action in ("state", "mesh", "collisions"):
        with _kernel(workspace, kernel) as actual:
            measured = evaluate_assembly(assembly, actual, mesh=action != "collisions")
    response = _response(source, assembly, measured)
    if action == "export":
        if assembly is None:
            raise _error("assembly_absent", "no assembly to export")
        response["assembly_json"] = assembly.to_json()
    return response


@dataclass(frozen=True)
class AssemblyCandidate:
    """No commit has happened. Gateway must CAS doc + undo + redo + receipt."""

    before_document_sha256: str
    doc: str
    response: dict


def _component(value):
    if not isinstance(value, dict):
        raise _error("bad_assembly_arguments", "component must be an object")
    try:
        raw = dict(value)
        raw["transform"] = Transform(**raw["transform"])
        return Component(**raw)
    except (KeyError, TypeError) as exc:
        raise _error("bad_assembly_arguments", "invalid component fields") from exc


def _record(factory, value):
    if not isinstance(value, dict):
        raise _error("bad_assembly_arguments", "part/mate must be an object")
    try:
        return factory(**value)
    except TypeError as exc:
        raise _error("bad_assembly_arguments", "invalid part/mate fields") from exc


def _require_present(assembly):
    if assembly is None:
        raise _error("assembly_absent", "load an independent part first")


def _replace_record(records, candidate, key):
    if any(getattr(item, key) == getattr(candidate, key) for item in records):
        return tuple(candidate if getattr(item, key) == getattr(candidate, key) else item
                     for item in records)
    return (*records, candidate)


def _updated(assembly, action, body):
    if action == "reopen":
        return Assembly.from_json(body["assembly_json"])
    if action == "import_part":
        part = _record(PartReference, body["part"])
        component = _component(body["component"])
        if (component.part_id, component.configuration) != (part.part_id, part.configuration):
            raise _error("bad_assembly_arguments", "imported instance must refer to the imported part")
        if assembly is None:
            return Assembly((part,), (component,))
        old = next((p for p in assembly.parts if (p.part_id, p.configuration) ==
                    (part.part_id, part.configuration)), None)
        if old is not None and old != part:
            raise _error("immutable_part_conflict", "part/configuration already binds different L1 bytes")
        if any(c.component_id == component.component_id for c in assembly.components):
            raise _error("duplicate_component", "import cannot replace an existing instance")
        return replace(assembly, parts=assembly.parts if old else (*assembly.parts, part),
                       components=(*assembly.components, component))
    _require_present(assembly)
    if action == "put_component":
        component = _component(body["component"])
        return replace(assembly, components=_replace_record(
            assembly.components, component, "component_id"))
    if action == "put_mate":
        mate = _record(Mate, body["mate"])
        return replace(assembly, mates=_replace_record(assembly.mates, mate, "mate_id"))
    if action in ("remove_mate", "remove_component"):
        key = "mate_id" if action == "remove_mate" else "component_id"
        records = assembly.mates if action == "remove_mate" else assembly.components
        identifier = body[key]
        if not isinstance(identifier, str) or not any(getattr(item, key) == identifier for item in records):
            raise _error("unknown_assembly_record", "record to remove is absent")
        remaining = tuple(item for item in records if getattr(item, key) != identifier)
        # Dangling parents/mates are rejected, never silently cascade-deleted.
        return replace(assembly, **{"mates" if action == "remove_mate" else "components": remaining})
    return assembly


def prepare_assembly(workspace, body, *, kernel=None):
    """Prepare POST /api/assembly candidate; gateway alone acknowledges commit.

    The complete body may carry CAS/receipt fields for the gateway. This layer
    does not validate their authority, use filesystem paths or mutate input.
    Any stored mates must solve before a changed assembly is offered for CAS.
    """
    action = _arguments(body, WRITE_ACTIONS)
    before, doc, original = _document(workspace)
    assembly = _updated(original, action, body)
    solver = None
    with _kernel(workspace, kernel) as actual:
        if assembly.mates:
            assembly, solver = solve_mates(assembly, actual)
        evaluation = evaluate_assembly(assembly, actual, mesh=True)
    doc.extra[ASSEMBLY_KEY] = assembly.as_dict()
    # Unknown extensions may acquire a diagnostic on first load. Return the
    # exact stable L1 that the gateway can load and persist, never a pre-load
    # string whose digest would differ from the committed document.
    loaded = document_from_l1(document_to_l1(doc))
    candidate = document_to_l1(loaded)
    if document_to_l1(document_from_l1(candidate)) != candidate:
        raise _error("assembly_document_unstable", "candidate L1 changes on another reopen")
    problems = load_blocking_problems(loaded)
    if problems:
        raise _error("invalid_document", "; ".join(problems[:8]))
    response = _response(candidate, assembly, evaluation, solver)
    response["action"] = action
    # A candidate response is suitable for a store receipt, not an HTTP reply.
    # There is deliberately no saved/committed flag before the transaction.
    return AssemblyCandidate(_sha(before), candidate, response)
