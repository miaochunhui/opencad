"""Read parameters or prepare a measured candidate for the project gateway CAS.

Neither entry point installs a Document, cache or history. Candidate.doc is
canonical L1 text; the gateway owns authorization, persistence and receipts.
Failed FreeCAD/STEP jobs retain their external scratch directory and traceback.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import traceback
from collections.abc import Mapping

from cadcore import docmodel, parametric
from cadcore.errors import CadError
from cadcore.kernel.features_step import MAX_STEP_BYTES, make_step
from cadcore.kernel.subproc import SubprocessKernel
from cadcore.ops import sw_features
from cadcore.sketch import commands, primitives, session
from cadcore.sketch.constraints import SketchError

__all__ = ["ParametricCandidate", "read_parametric", "prepare_parametric",
           "handler", "make_plate_document", "make_configurable_plate_document",
           "validate_document"]

_SCOPE = "Measured single-document sketch/solid rebuild; not industrial CAD parity."


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _is_document_sha256(value):
    return isinstance(value, str) and len(value) == 64 \
        and all(character in "0123456789abcdef" for character in value)


def validate_document(document):
    """Validate persisted parameters and UUID bindings without solving/rebuilding.

    No namespace is valid for an ordinary document. A present namespace must
    pass the core expression/check/binding rules. Binding edits are exercised on
    a deep copy only; guard checks inspect stored sketch geometry, not a new BRep.
    """
    if not isinstance(document, docmodel.Document):
        raise parametric.ParametricError("Validation requires a real Document", reason="bad_document")
    if "parametric" not in document.extra:
        return
    try:
        problems = docmodel.load_blocking_problems(document)
        if problems:
            raise parametric.ParametricError("; ".join(problems[:8]), reason="invalid_parametric_document")
        specification = document.extra["parametric"]
        if not isinstance(specification, dict):
            raise parametric.ParametricError("Invalid parametric specification fields",
                                             reason="bad_parametric_spec")
        evaluation = parametric.evaluate_parameters(specification)
        parametric.feature_configuration(document, evaluation, require_current=True)
        parametric._checks(specification, evaluation.values)
        candidate = copy.deepcopy(document)
        # Reuse the core's UUID resolution, driving-dimension and duplicate rules.
        parametric._bind(candidate, specification, evaluation.values)
        parametric._geometry_guards(candidate, specification, evaluation.values)
    except SketchError as exc:
        raise parametric.ParametricError("Invalid persisted driving-dimension binding: " + str(exc),
                                         reason="unsupported_binding") from exc
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError) as exc:
        raise parametric.ParametricError("Malformed persisted parametric specification",
                                         reason="bad_parametric_spec") from exc


@dataclass(frozen=True)
class ParametricCandidate:
    before_document_sha256: str
    doc: str
    response: dict
    _envelope_sha256: str | None = field(default=None, repr=False)

    def _envelope(self):
        return {"before_document_sha256": self.before_document_sha256,
                "doc": self.doc, "response": self.response}

    def __post_init__(self):
        # init=True preserves the original seal during ordinary dataclass replace.
        if self._envelope_sha256 is None:
            object.__setattr__(self, "_envelope_sha256", _fingerprint(self._envelope()))

    def verify_integrity(self):
        try:
            intact = (_is_document_sha256(self._envelope_sha256)
                      and _fingerprint(self._envelope()) == self._envelope_sha256)
        except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
            raise parametric.ParametricError("Candidate envelope is not canonical JSON",
                                             reason="candidate_changed") from exc
        if not intact:
            raise parametric.ParametricError("Candidate envelope changed after measurement",
                                             reason="candidate_changed")
        if (not _is_document_sha256(self.before_document_sha256)
                or not isinstance(self.doc, str) or not isinstance(self.response, dict)):
            raise parametric.ParametricError("Invalid candidate envelope fields",
                                             reason="candidate_changed")
        try:
            document = docmodel.document_from_l1(self.doc)
            if docmodel.document_to_l1(document) != self.doc:
                raise parametric.ParametricError("Candidate document is not canonical L1",
                                                 reason="candidate_changed")
            validate_document(document)
        except (CadError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise parametric.ParametricError("Candidate document failed persistence validation",
                                             reason="candidate_changed") from exc
        digest = hashlib.sha256(self.doc.encode("utf-8")).hexdigest()
        if self.response.get("doc") != self.doc or self.response.get("document_sha256") != digest:
            raise parametric.ParametricError("Candidate changed after measurement",
                                             reason="candidate_changed")
        mesh = self.response.get("mesh", {})
        step = self.response.get("step", {})
        if not isinstance(mesh, dict) or not isinstance(step, dict):
            raise parametric.ParametricError("Candidate artifacts must be JSON objects",
                                             reason="candidate_changed")
        solids = mesh.get("solids")
        if (mesh.get("document_sha256") != digest or not isinstance(solids, list)
                or len(solids) != 1 or not isinstance(solids[0], dict)
                or step.get("document_sha256") != digest):
            raise parametric.ParametricError("Candidate artifacts disagree with document",
                                             reason="candidate_changed")
        measured = solids[0]
        fingerprint = _fingerprint({key: value for key, value in measured.items() if key != "mesh_sha256"})
        if (measured.get("mesh_sha256") != fingerprint or measured.get("document_sha256") != digest
                or self.response.get("base_document_sha256") != self.before_document_sha256):
            raise parametric.ParametricError("Candidate mesh changed after measurement",
                                             reason="candidate_changed")


def _error(workspace, code, detail, status=422, **details):
    # Workspace may be imported through a private loader, not module "server".
    cls = workspace.state.__func__.__globals__["ApiError"]
    return cls(code, detail, status=status, **details)


def _fields(workspace, payload):
    if not isinstance(payload, Mapping) or set(payload) - {
            "action", "expected_document_sha256", "configuration", "overrides", "dimensions"}:
        raise _error(workspace, "bad_parametric_arguments", "Unexpected parametric fields", 400)
    if payload.get("action") not in ("create_plate", "create_configurable_plate", "rebuild"):
        raise _error(workspace, "bad_parametric_arguments",
                     "action must be create_plate, create_configurable_plate or rebuild", 400)
    digest = payload.get("expected_document_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise _error(workspace, "bad_parametric_arguments", "Exact expected_document_sha256 is required", 400)
    if "configuration" in payload and payload["configuration"] is not None \
            and not isinstance(payload["configuration"], str):
        raise _error(workspace, "bad_parametric_arguments", "configuration must be a string or null", 400)
    for name in ("overrides", "dimensions"):
        if name in payload and (not isinstance(payload[name], Mapping)
                                or len(payload[name]) > 128 or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in payload[name].items())):
            raise _error(workspace, "bad_parametric_arguments", name + " must map names to expressions", 400)


def _definition(expression):
    return {"expression": expression, "unit": "length", "positive": True}


def _empty_document(document):
    if (document.features or document.annotations or document.sheets or document.extra
            or document.diagnostics or len(document.sketches) > 1):
        return False
    for sketch in document.sketches:
        if not isinstance(sketch, dict) or set(sketch) - {
                "uuid", "plane", "entities", "constraints", "dimensions"}:
            return False
        if any(sketch.get(name) for name in ("entities", "constraints", "dimensions")):
            return False
        if sketch.get("plane", "XY") != "XY":
            return False
    return True


def make_plate_document(document):
    """Add a real 40x20mm driven plate with circular cut, retaining metadata.

    The argument is copied. No origin sentinel/edge number is persisted: fixed
    construction datum lines, dimensions and features all have genuine UUIDs.
    """
    if not _empty_document(document):
        raise parametric.ParametricError("Plate creation requires an empty document",
                                         reason="parametric_requires_empty")
    candidate = copy.deepcopy(document)
    if candidate.sketches:
        plate = candidate.sketches[0]
        plate["plane"] = "XY"
        for name in ("entities", "constraints", "dimensions"):
            plate[name] = []
    else:
        session.enter_sketch(candidate, plane="XY")
        plate = candidate.sketches[0]
    plate_uuid = plate["uuid"]
    lines = primitives.add_rectangle(plate, p1_nm=(0, 0), p2_nm=(40_000_000, 20_000_000))
    datum = commands.add_line(plate, p1=(0, 0), p2=(1_000_000, 0), fixed=True, construction=True)
    for index in range(4):
        commands.add_constraint(plate, kind="coincident",
                                refs=[(lines[index], "end"), (lines[(index + 1) % 4], "start")])
    commands.add_constraint(plate, kind="coincident", refs=[(lines[0], "start"), (datum, "start")])
    width = commands.add_dimension(plate, kind="linear", refs=[(lines[0], "")], value_nm=40_000_000)
    height = commands.add_dimension(plate, kind="linear", refs=[(lines[1], "")], value_nm=20_000_000)
    boss = sw_features.extrude_boss(candidate, sketch=plate_uuid, distance_nm=8_000_000)[0]
    hole_uuid = session.enter_sketch(candidate, plane="XY")
    hole = next(sketch for sketch in candidate.sketches if sketch["uuid"] == hole_uuid)
    circle = commands.add_circle(hole, center=(20_000_000, 10_000_000), radius_nm=4_000_000)
    vertical = commands.add_line(hole, p1=(0, 0), p2=(0, 20_000_000), fixed=True, construction=True)
    horizontal = commands.add_line(hole, p1=(0, 0), p2=(40_000_000, 0), fixed=True, construction=True)
    xdim = commands.add_dimension(hole, kind="linear",
                                  refs=[(circle, "center"), (vertical, "")], value_nm=20_000_000)
    ydim = commands.add_dimension(hole, kind="linear",
                                  refs=[(circle, "center"), (horizontal, "")], value_nm=10_000_000)
    radius = commands.add_dimension(hole, kind="radial", refs=[(circle, "")], value_nm=4_000_000)
    cut = sw_features.extrude_cut(candidate, sketch=hole_uuid, distance_nm=8_000_000)[0]
    bindings = {}
    for key, sketch_uuid, target_uuid, expression in (
            ("plate_width", plate_uuid, width, "width"),
            ("plate_height", plate_uuid, height, "height"),
            ("hole_center_x", hole_uuid, xdim, "hole_x"),
            ("hole_center_y", hole_uuid, ydim, "hole_y"),
            ("hole_radius", hole_uuid, radius, "radius")):
        bindings[key] = {"kind": "dimension", "sketch_uuid": sketch_uuid,
                         "target_uuid": target_uuid, "expression": expression}
    for key, target_uuid in (("boss_depth", boss), ("cut_depth", cut)):
        bindings[key] = {"kind": "feature", "target_uuid": target_uuid,
                         "field": "distance_nm", "expression": "depth"}
    candidate.extra["parametric"] = {
        "version": 1,
        "parameters": {name: _definition(expression) for name, expression in (
            ("width", "40 * mm"), ("height", "20 * mm"), ("hole_x", "width / 2"),
            ("hole_y", "height / 2"), ("radius", "width / 10"), ("depth", "width / 5"))},
        "configurations": {"wide": {"width": "60 * mm"}},
        "bindings": bindings,
        "checks": [{"key": "hole_diameter", "left": "radius * 2",
                    "relation": "<", "right": "height"}],
        "geometry_guards": {
            "interior_hole": {"kind": "circle_in_rectangle", "rectangle_sketch_uuid": plate_uuid,
                              "circle_sketch_uuid": hole_uuid, "circle_uuid": circle,
                              "clearance": "0 * mm"}},
    }
    return candidate


def make_configurable_plate_document(document):
    """Create explicit v2 plate/cut configurations; preserve the legacy v1 factory."""
    candidate = make_plate_document(document)
    spec = candidate.extra["parametric"]
    spec.update(
        version=2,
        configurations={"wide": {"width": "60 * mm"}, "solid": {},
                        "wide_solid": {"width": "60 * mm"}},
        feature_bindings={
            "plate_boss": {"target_uuid": candidate.features[0]["uuid"], "base_suppressed": False},
            "hole_cut": {"target_uuid": candidate.features[1]["uuid"], "base_suppressed": False}},
        configuration_suppressions={"wide": {}, "solid": {"hole_cut": True},
                                    "wide_solid": {"hole_cut": True}})
    return candidate


def _overrides(workspace, specification, payload):
    overrides = dict(payload.get("overrides", {}))
    bindings = specification.get("bindings")
    if (not isinstance(bindings, Mapping) or not bindings or len(bindings) > 256
            or any(not isinstance(key, str) or not isinstance(value, Mapping)
                   for key, value in bindings.items())):
        raise _error(workspace, "bad_parametric_spec", "Invalid semantic bindings")
    for key, expression in payload.get("dimensions", {}).items():
        binding = bindings.get(key)
        if not isinstance(binding, Mapping) or binding.get("kind") != "dimension":
            raise _error(workspace, "unknown_driving_dimension", "Unknown semantic driving dimension", 400)
        name = binding.get("expression")
        if not isinstance(name, str) or name not in specification.get("parameters", {}):
            raise _error(workspace, "unsupported_driving_dimension",
                         "Dimension must bind one declared parameter", 400)
        if name in overrides:
            raise _error(workspace, "duplicate_parametric_override",
                         "Parameter and dimension cannot both override the same value", 400)
        overrides[name] = expression
    return overrides


def _find(rows, identifier):
    if not isinstance(rows, list) or not isinstance(identifier, str):
        raise parametric.ParametricError("Invalid binding UUID", reason="unknown_binding_target")
    found = [row for row in rows if isinstance(row, dict) and row.get("uuid") == identifier]
    if len(found) != 1:
        raise parametric.ParametricError("Binding UUID must resolve uniquely",
                                         reason="unknown_binding_target")
    return found[0]


def _read(workspace, document):
    digest = parametric.document_sha256(document)
    specification = document.extra.get("parametric")
    if specification is None:
        return {"ok": True, "enabled": False, "document_sha256": digest,
                "can_create_plate": _empty_document(document) and not workspace.undo_stack
                and not workspace.redo_stack, "scope": _SCOPE}
    evaluation = parametric.evaluate_parameters(specification)
    feature_states = parametric.feature_configuration(document, evaluation, require_current=True)
    definitions = specification["parameters"]
    parameters = []
    for name in evaluation.order:
        value = evaluation.values[name]
        expression = evaluation.overrides.get(
            name, specification.get("configurations", {}).get(evaluation.configuration, {}).get(
                name, definitions[name]["expression"]))
        parameters.append({"name": name, **definitions[name], "expression": expression,
                           "value": value.as_dict()})
    dimensions = []
    bindings = specification.get("bindings")
    if not isinstance(bindings, Mapping) or not bindings or len(bindings) > 256:
        raise parametric.ParametricError("Invalid semantic bindings", reason="bad_parametric_spec")
    for key, binding in bindings.items():
        if not isinstance(key, str) or not isinstance(binding, Mapping):
            raise parametric.ParametricError("Invalid semantic binding", reason="bad_parametric_spec")
        if binding.get("kind") == "dimension":
            if set(binding) != {"kind", "sketch_uuid", "target_uuid", "expression"}:
                raise parametric.ParametricError("Invalid dimension binding fields",
                                                 reason="bad_parametric_spec")
            sketch = _find(document.sketches, binding.get("sketch_uuid"))
            dimension = _find(sketch.get("dimensions"), binding.get("target_uuid"))
            dimensions.append({"key": key, "target_uuid": binding["target_uuid"],
                               "expression": binding["expression"], "value_nm": dimension["value_nm"],
                               "editable": isinstance(binding["expression"], str)
                               and binding["expression"] in definitions})
    return {"ok": True, "enabled": True, "document_sha256": digest, "parameters": parameters,
            "configuration": evaluation.configuration, "configurations": sorted(
                specification.get("configurations", {})), "driving_dimensions": dimensions,
            "parametric_version": specification["version"],
            "feature_bindings": feature_states,
            "configuration_suppressions": copy.deepcopy(
                specification.get("configuration_suppressions", {})),
            "feature_order": [f["uuid"] for f in document.features if not f.get("suppressed")],
            "scope": _SCOPE}


def _scratch_root(workspace):
    # Canonicalize the deployment's 3FS alias once; reject interior symlinks.
    root = Path(workspace.out_root).absolute()
    source = Path(__file__).resolve().parents[3]
    if root.resolve().is_relative_to(source):
        raise _error(workspace, "parametric_runtime_in_source", "Runtime root must be outside source", 500)
    aliases = (Path("/hf3fs-jd"), Path("/3fs/research3fs"))
    if any(path.is_symlink() and not (
            path == aliases[0] and path.resolve() == aliases[1])
           for path in (root, *root.parents)):
        raise _error(workspace, "parametric_runtime_symlink", "Runtime root cannot contain symlinks", 500)
    if not root.is_dir():
        raise _error(workspace, "parametric_runtime_missing", "Runtime root must already exist", 500)
    return root.resolve()


def _gateway_l1(prepared, kernel):
    # L1's integer-nm gate also covers diagnostics. Keep the measured residual
    # as a real-valued mm diagnostic, then remeasure the final canonical bytes.
    document = copy.deepcopy(prepared.document)
    for sketch in document.sketches:
        solved = sketch.get("solved")
        if isinstance(solved, dict) and "residual_nm" in solved:
            solved["residual_mm"] = solved.pop("residual_nm") / 1_000_000
    document = docmodel.document_from_l1(docmodel.document_to_l1(document))
    l1 = docmodel.document_to_l1(document)
    shape = kernel.shape_from_document(json.loads(l1))
    mesh = kernel.tessellate(shape, deflection_mm=0.05)
    if (mesh.get("properties") != prepared.mesh.get("properties")
            or mesh.get("solid_count") != prepared.mesh.get("solid_count")):
        raise parametric.ParametricError("Diagnostic normalization changed measured BRep",
                                         reason="candidate_changed")
    recipe = copy.deepcopy(shape.to_dict())
    document_digest = hashlib.sha256(l1.encode("utf-8")).hexdigest()
    recipe_digest = _fingerprint(recipe)
    mesh.update(document_sha256=document_digest, recipe_sha256=recipe_digest)
    mesh["mesh_sha256"] = _fingerprint(mesh)
    result = replace(prepared, document=document, l1=l1, document_sha256=document_digest,
                     shape=shape, recipe=recipe, mesh=mesh)
    result.verify_integrity()
    return result


def read_parametric(workspace, body=None):
    """Read without changing document, history, cache or catalog preferences."""
    if body is not None and (not isinstance(body, Mapping) or body):
        raise _error(workspace, "bad_parametric_arguments", "GET accepts no fields", 400)
    with workspace._lock:
        try:
            return _read(workspace, copy.deepcopy(workspace.doc))
        except CadError as exc:
            raise _error(workspace, exc.reason, str(exc)) from exc


def prepare_parametric(workspace, payload):
    """Prepare only. Caller must CAS before_document_sha256 before committing.

    Outer expected_revision/expected_sha/request_id are gateway-only fields.
    This does not recheck or overwrite a concurrent live edit after preparation.
    """
    with workspace._lock:
        _fields(workspace, payload)
        payload = copy.deepcopy(dict(payload))
        base_text = workspace.l1_text()
        document = copy.deepcopy(workspace.doc)
        if getattr(workspace, "_release_pending", False):
            raise _error(workspace, "maintenance", "Parametric writes denied during maintenance", 409)
        expected = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
        if payload["expected_document_sha256"] != expected:
            raise _error(workspace, "parametric_conflict", "Document changed before rebuild", 409,
                         document_sha256=expected)
        if payload["action"] in ("create_plate", "create_configurable_plate"):
            if workspace.undo_stack or workspace.redo_stack:
                raise _error(workspace, "parametric_requires_empty",
                             "Plate creation cannot replace an edited project", 409)
            try:
                factory = (make_configurable_plate_document
                           if payload["action"] == "create_configurable_plate" else make_plate_document)
                document = factory(document)
            except CadError as exc:
                raise _error(workspace, exc.reason, str(exc), 409) from exc
        specification = document.extra.get("parametric")
        if not isinstance(specification, dict):
            raise _error(workspace, "parametric_not_configured", "Document has no parametric specification", 409)
        try:
            parametric.evaluate_parameters(specification)
        except CadError as exc:
            raise _error(workspace, exc.reason, str(exc)) from exc
        overrides = _overrides(workspace, specification, payload)
    scratch = Path(tempfile.mkdtemp(prefix="parametric-", dir=_scratch_root(workspace)))
    try:
        kernel = SubprocessKernel(work_dir=str(scratch / "kernel"), keep_work_dir=True,
                                  extra_env={"TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            prepared = parametric.prepare_rebuild(document, kernel=kernel,
                                                  configuration=payload.get("configuration"),
                                                  overrides=overrides)
            prepared = _gateway_l1(prepared, kernel)
            problems = workspace.docmodel.load_blocking_problems(prepared.document)
            if problems:
                raise _error(workspace, "invalid_parametric_document", "; ".join(problems[:8]))
            path = scratch / "candidate.step"
            kernel.export(prepared.shape, fmt="step", path=str(path))
            if path.stat().st_size > MAX_STEP_BYTES:
                raise _error(workspace, "parametric_step_too_large", "STEP exceeds the bounded size")
            step = path.read_bytes()
            make_step(step_text=step.decode("ascii"))
            prepared.verify_integrity()
            response = _read(workspace, prepared.document)
            mesh = {"ok": True, "document_sha256": prepared.document_sha256, "kernel": kernel.name,
                    "unit": "mm", "solids": [copy.deepcopy(prepared.mesh)], "reason": "ok"}
            step_info = {"document_sha256": prepared.document_sha256, "bytes": len(step),
                         "sha256": hashlib.sha256(step).hexdigest(), "download": "/api/step"}
            response.update(base_document_sha256=expected, mesh=mesh, step=step_info,
                            doc=prepared.l1, sketch_reports=list(prepared.sketch_reports),
                            changed=prepared.l1 != base_text)
            candidate = ParametricCandidate(expected, prepared.l1, response)
            candidate.verify_integrity()
        finally:
            kernel.close()
    except Exception as exc:
        log_failure = None
        try:
            (scratch / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError as log_error:
            log_failure = str(log_error)
        detail = str(exc)
        if log_failure is not None:
            detail += "; failure evidence write also failed: " + log_failure
        if isinstance(exc, CadError):
            raise _error(workspace, exc.reason, detail, evidence_path=str(scratch),
                         kernel_reason=exc.details.get("kernel_reason")) from exc
        if isinstance(exc, SketchError):
            raise _error(workspace, "parametric_sketch_failed", detail,
                         evidence_path=str(scratch), sketch_key=exc.key) from exc
        if isinstance(exc, (UnicodeError, ValueError, OSError)):
            raise _error(workspace, "parametric_artifact_failed", f"{type(exc).__name__}: {detail}", 503,
                         evidence_path=str(scratch)) from exc
        if log_failure is not None:
            raise _error(workspace, "parametric_internal_failed", detail, 500,
                         evidence_path=str(scratch)) from exc
        raise
    shutil.rmtree(scratch)
    return candidate


def handler(workspace, payload):
    """Compatibility entry: GET response or POST candidate, never a commit."""
    return read_parametric(workspace) if payload is None else prepare_parametric(workspace, payload)
