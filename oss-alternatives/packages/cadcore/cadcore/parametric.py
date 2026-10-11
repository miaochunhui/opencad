"""Prepare an isolated, dimension-driven Document and a measured full rebuild.

This module never commits a Workspace, cache or history. The caller must compare
base_document_sha256 under its project lock before installing the candidate.
Expressions use explicit units (``40 * mm``), not Python eval or implicit units.
Semantic binding keys resolve stored UUIDs, not persistent B-Rep edge numbering.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .docmodel import Document, document_from_l1, document_to_l1
from .errors import CadError
from .kernel.subproc import SubprocessKernel
from .sketch.commands import update_dimension
from .sketch.constraints import solve

__all__ = [
    "ParametricError", "Quantity", "ParameterEvaluation", "PreparedRebuild",
    "document_sha256", "evaluate_parameters", "feature_configuration", "prepare_rebuild",
]

MAX_PARAMETERS = 128
MAX_BINDINGS = 256
MAX_EXPRESSIONS = 256
MAX_LENGTH_NM = 2**53
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_UNITS = {"nm": 1, "um": 1000, "mm": 1_000_000, "cm": 10_000_000,
          "m": 1_000_000_000, "inch": 25_400_000}
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
            ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
_V1_FIELDS = {"version", "parameters", "configurations", "bindings", "checks",
              "geometry_guards", "active_configuration", "active_overrides"}
_V2_FIELDS = _V1_FIELDS | {"feature_bindings", "configuration_suppressions"}


class ParametricError(CadError):
    """A rejected candidate, preserving the underlying failure with chaining."""


def _fail(reason: str, detail: str, **details: Any) -> ParametricError:
    return ParametricError(detail, reason=reason, **details)


@dataclass(frozen=True)
class Quantity:
    value: Fraction
    power: int = 0

    def __post_init__(self) -> None:
        if (self.value.numerator.bit_length() > 256
                or self.value.denominator.bit_length() > 256
                or abs(self.power) > 3 or abs(self.value) > MAX_LENGTH_NM):
            raise _fail("expression_range", "expression quantity exceeds the bounded range")

    def as_dict(self) -> dict[str, Any]:
        return {"value": str(self.value), "length_power": self.power}


@dataclass(frozen=True)
class ParameterEvaluation:
    values: Mapping[str, Quantity]
    order: tuple[str, ...]
    configuration: str | None
    overrides: Mapping[str, str]


@dataclass(frozen=True)
class PreparedRebuild:
    """Measured candidate; frozen wrapper does not make its Document immutable."""

    document: Document
    l1: str
    base_document_sha256: str
    document_sha256: str
    shape: Any
    recipe: Mapping[str, Any]
    mesh: Mapping[str, Any]
    parameters: ParameterEvaluation
    sketch_reports: tuple[Mapping[str, Any], ...]
    feature_order: tuple[str, ...]

    def verify_integrity(self) -> None:
        """Reject candidate edits before the caller's CAS/commit operation."""
        if document_to_l1(self.document) != self.l1:
            raise _fail("candidate_changed", "candidate Document changed after measurement")
        if _digest(self.l1) != self.document_sha256:
            raise _fail("candidate_changed", "candidate L1 fingerprint mismatch")
        if self.shape.to_dict() != self.recipe:
            raise _fail("candidate_changed", "candidate recipe changed after measurement")
        if (self.mesh.get("document_sha256") != self.document_sha256
                or self.mesh.get("recipe_sha256") != _json_digest(self.recipe)):
            raise _fail("candidate_changed", "mesh provenance does not match the candidate")
        if self.mesh.get("mesh_sha256") != _mesh_digest(self.mesh):
            raise _fail("candidate_changed", "measured mesh changed after preparation")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _mesh_digest(mesh: Mapping[str, Any]) -> str:
    return _json_digest({key: value for key, value in mesh.items() if key != "mesh_sha256"})


def document_sha256(document: Document) -> str:
    return _digest(document_to_l1(document))


def _table(value: Any, label: str, limit: int) -> dict:
    if not isinstance(value, Mapping) or len(value) > limit:
        raise _fail("bad_parametric_spec", label + " must be a bounded mapping")
    return dict(value)


def _name(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or value in _UNITS:
        raise _fail("bad_parameter_name", "invalid or reserved parameter name")
    return value


def _parse(expression: Any) -> ast.Expression:
    if not isinstance(expression, str) or not expression or len(expression) > 2048:
        raise _fail("unsafe_expression", "expression must be a nonempty bounded string")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise _fail("unsafe_expression", "expression syntax rejected") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 256 or any(not isinstance(node, _ALLOWED) for node in nodes):
        raise _fail("unsafe_expression", "only names, numeric literals and +-*/ are allowed")
    for node in nodes:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise _fail("unsafe_expression", "only numeric literals are allowed")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise _fail("expression_range", "nonfinite literal rejected")
    return tree


def _dependencies(tree: ast.Expression, names: set[str]) -> set[str]:
    dependencies = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    unknown = dependencies - names - _UNITS.keys()
    if unknown:
        raise _fail("unknown_parameter", "unknown expression name", names=sorted(unknown))
    return dependencies & names


def _literal(token: str) -> Quantity:
    try:
        decimal = Decimal(token.replace("_", ""))
    except (ValueError, ArithmeticError) as exc:
        raise _fail("unsafe_expression", "only decimal numeric literals are allowed") from exc
    digits = decimal.as_tuple()
    # Bound the exponent before Fraction constructs 10**abs(exponent).
    if not decimal.is_finite() or len(digits.digits) > 77 or abs(digits.exponent) > 77:
        raise _fail("expression_range", "decimal literal exceeds the bounded precision/exponent")
    return Quantity(Fraction(decimal))


def _evaluate(expression: str, values: Mapping[str, Quantity]) -> Quantity:
    tree = _parse(expression)
    _dependencies(tree, set(values))

    def visit(node: ast.AST, depth: int = 0) -> Quantity:
        if depth > 64:
            raise _fail("unsafe_expression", "expression nesting exceeds 64")
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant):
            token = ast.get_source_segment(expression, node)
            return _literal(token)
        if isinstance(node, ast.Name):
            return Quantity(Fraction(_UNITS[node.id]), 1) if node.id in _UNITS else values[node.id]
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand, depth + 1)
            return Quantity(-value.value if isinstance(node.op, ast.USub) else value.value, value.power)
        left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left.power != right.power:
                raise _fail("unit_mismatch", "addition/subtraction require matching dimensions")
            return Quantity(left.value + right.value if isinstance(node.op, ast.Add)
                            else left.value - right.value, left.power)
        if isinstance(node.op, ast.Mult):
            return Quantity(left.value * right.value, left.power + right.power)
        if not right.value:
            raise _fail("division_by_zero", "division by zero in parameter expression")
        return Quantity(left.value / right.value, left.power - right.power)

    return visit(tree)


def _length(value: Quantity, *, positive: bool = False) -> int:
    if value.power != 1:
        raise _fail("unit_mismatch", "a length quantity with explicit units is required")
    if value.value.denominator != 1:
        raise _fail("subnanometre_length", "length must resolve to exact integer nanometres")
    result = int(value.value)
    if abs(result) > MAX_LENGTH_NM or (positive and result <= 0):
        raise _fail("invalid_parameter", "length is outside its allowed range")
    return result


def evaluate_parameters(specification: Mapping[str, Any], *, configuration: str | None = None,
                        overrides: Mapping[str, str] | None = None) -> ParameterEvaluation:
    """Evaluate a bounded DAG. Empty configuration selects the base configuration."""
    spec = _table(specification, "parametric", 10)
    if type(spec.get("version")) is not int or spec["version"] not in (1, 2):
        raise _fail("bad_parametric_spec", "parametric version must be integer 1 or 2")
    if set(spec) - (_V1_FIELDS if spec["version"] == 1 else _V2_FIELDS):
        raise _fail("bad_parametric_spec", "unknown parametric specification fields")
    definitions = _table(spec.get("parameters"), "parameters", MAX_PARAMETERS)
    if not definitions:
        raise _fail("bad_parametric_spec", "parameters must not be empty")
    names = {_name(name) for name in definitions}
    for name, definition in definitions.items():
        row = _table(definition, "parameter definition", 3)
        if set(row) != {"expression", "unit", "positive"}:
            raise _fail("bad_parametric_spec", "parameter requires expression, unit and positive")
        if row["unit"] not in ("length", "scalar") or type(row["positive"]) is not bool:
            raise _fail("bad_parametric_spec", "invalid parameter unit or positive flag", parameter=name)
        _dependencies(_parse(row["expression"]), names)
    configs = _table(spec.get("configurations", {}), "configurations", 32)
    for name, row in configs.items():
        _name(name)
        entries = _table(row, "configuration", MAX_PARAMETERS)
        if set(entries) - names:
            raise _fail("unknown_parameter", "configuration overrides an undeclared parameter")
        for expression in entries.values():
            _dependencies(_parse(expression), names)
    if spec["version"] == 2:
        features = _table(spec.get("feature_bindings"), "feature bindings", MAX_BINDINGS)
        if not features:
            raise _fail("bad_parametric_spec", "version 2 requires explicit feature bindings")
        targets = set()
        for key, row in features.items():
            _name(key)
            if (not isinstance(row, Mapping) or set(row) != {"target_uuid", "base_suppressed"}
                    or not isinstance(row["target_uuid"], str) or not row["target_uuid"]
                    or type(row["base_suppressed"]) is not bool):
                raise _fail("bad_feature_configuration", "invalid feature binding", binding=key)
            if row["target_uuid"] in targets:
                raise _fail("duplicate_binding", "feature configuration target is duplicated")
            targets.add(row["target_uuid"])
        suppressions = _table(spec.get("configuration_suppressions"),
                              "configuration suppressions", 32)
        if set(suppressions) != set(configs):
            raise _fail("unknown_configuration", "suppression tables must match configurations")
        for name, row in suppressions.items():
            entries = _table(row, "configuration suppression", MAX_BINDINGS)
            if set(entries) - set(features):
                raise _fail("unknown_feature_binding", "configuration targets an undeclared feature")
            if any(type(value) is not bool for value in entries.values()):
                raise _fail("bad_feature_configuration", "suppression values must be booleans",
                            configuration=name)
    active = spec.get("active_configuration") if configuration is None else configuration
    if active == "":
        active = None
    if active is not None and (not isinstance(active, str) or active not in configs):
        raise _fail("unknown_configuration", "configuration is not declared")
    effective = {} if configuration is not None else _table(
        spec.get("active_overrides", {}), "active overrides", MAX_PARAMETERS)
    if overrides is not None:
        effective.update(_table(overrides, "overrides", MAX_PARAMETERS))
    if set(effective) - names:
        raise _fail("unknown_parameter", "override targets an undeclared parameter")
    expressions = {name: row["expression"] for name, row in definitions.items()}
    if active is not None:
        expressions.update(configs[active])
    expressions.update(effective)
    dependencies = {name: _dependencies(_parse(expression), names)
                    for name, expression in expressions.items()}
    order: list[str] = []
    remaining = set(names)
    while remaining:
        ready = sorted(name for name in remaining if not dependencies[name] & remaining)
        if not ready:
            raise _fail("parameter_cycle", "parameter dependency cycle", parameters=sorted(remaining))
        order.extend(ready)
        remaining.difference_update(ready)
    values: dict[str, Quantity] = {}
    for name in order:
        value = _evaluate(expressions[name], values)
        definition = definitions[name]
        if definition["unit"] == "length":
            _length(value, positive=definition["positive"])
        elif value.power != 0:
            raise _fail("unit_mismatch", "scalar parameter must be dimensionless", parameter=name)
        if definition["positive"] and value.value <= 0:
            raise _fail("invalid_parameter", "positive parameter is not positive", parameter=name)
        values[name] = value
    return ParameterEvaluation(values, tuple(order), active, dict(effective))


def _find(rows: Any, identifier: Any, label: str) -> dict:
    if not isinstance(identifier, str) or not identifier or not isinstance(rows, list):
        raise _fail("unknown_binding_target", "invalid " + label + " UUID")
    found = [row for row in rows if isinstance(row, dict) and row.get("uuid") == identifier]
    if len(found) != 1:
        raise _fail("unknown_binding_target", label + " UUID must resolve uniquely", target=identifier)
    return found[0]


def feature_configuration(document: Document, evaluation: ParameterEvaluation, *,
                          require_current: bool = False) -> dict[str, dict[str, Any]]:
    """Resolve version 2 UUID states; validate all configurations without geometry.

    Only declared flags change. Dependencies are never rebound or cascaded and
    index-based topology consumers are refused when an earlier feature is absent.
    """
    spec = document.extra["parametric"]
    if spec["version"] == 1:
        return {}
    bindings = spec["feature_bindings"]
    resolved = {key: _find(document.features, row["target_uuid"], "feature")
                for key, row in bindings.items()}
    sketch_ids = set()
    identifiers = set()
    for label, rows in (("sketch", document.sketches), ("feature", document.features)):
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("uuid"), str)
                    or not row["uuid"] or row["uuid"] in identifiers):
                raise _fail("configuration_dependency_invalid", "geometry UUIDs must be unique",
                            table=label)
            identifiers.add(row["uuid"])
            if label == "sketch":
                sketch_ids.add(row["uuid"])
            elif type(row.get("suppressed")) is not bool:
                raise _fail("bad_feature_configuration", "stored suppression must be boolean",
                            feature=row["uuid"])
    base = {row["uuid"]: row["suppressed"] for row in document.features}
    for key, row in bindings.items():
        base[resolved[key]["uuid"]] = row["base_suppressed"]

    def state(configuration):
        result = dict(base)
        for key, suppressed in spec["configuration_suppressions"].get(configuration, {}).items():
            result[resolved[key]["uuid"]] = suppressed
        return result

    for configuration in (None, *spec["configurations"]):
        flags = state(configuration)
        prior = set()
        live = set()
        absent = False
        for feature in document.features:
            identifier = feature["uuid"]
            refs = feature.get("inputs", [])
            if (not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs)
                    or any(ref not in sketch_ids | prior for ref in refs)):
                raise _fail("configuration_dependency_invalid",
                            "missing, self or forward feature dependency",
                            feature=identifier, configuration=configuration)
            if not flags[identifier]:
                inactive = [ref for ref in refs if ref in prior and ref not in live]
                if inactive:
                    raise _fail("configuration_dependency_invalid",
                                "live feature depends on a suppressed feature", feature=identifier,
                                dependencies=inactive, configuration=configuration)
                params = feature.get("params", {})
                if absent and isinstance(params, Mapping) and (
                        "edge_indices" in params or "face_indices" in params):
                    raise _fail("configuration_topology_unsupported",
                                "suppression invalidates unproven edge/face index identity",
                                feature=identifier, configuration=configuration)
                live.add(identifier)
            else:
                absent = True
            prior.add(identifier)
        if not live:
            raise _fail("configuration_dependency_invalid", "configuration has no live solid feature",
                        configuration=configuration)
    selected = state(evaluation.configuration)
    if require_current and any(feature["suppressed"] != selected[feature["uuid"]]
                               for feature in document.features):
        raise _fail("configuration_state_mismatch",
                    "stored suppression disagrees with the active configuration")
    return {key: {"target_uuid": row["target_uuid"],
                  "base_suppressed": row["base_suppressed"],
                  "suppressed": selected[row["target_uuid"]]}
            for key, row in bindings.items()}


def _configure_features(candidate: Document, evaluation: ParameterEvaluation) -> None:
    for row in feature_configuration(candidate, evaluation).values():
        _find(candidate.features, row["target_uuid"], "feature")["suppressed"] = row["suppressed"]


def _bind(candidate: Document, spec: Mapping[str, Any], values: Mapping[str, Quantity]) -> None:
    bindings = _table(spec.get("bindings"), "bindings", MAX_BINDINGS)
    if not bindings:
        raise _fail("bad_parametric_spec", "at least one semantic binding is required")
    targets: set[tuple[str, ...]] = set()
    for key, binding in bindings.items():
        _name(key)
        row = _table(binding, "binding", 5)
        value = _length(_evaluate(row.get("expression"), values))
        if row.get("kind") == "dimension":
            if set(row) != {"kind", "sketch_uuid", "target_uuid", "expression"}:
                raise _fail("bad_parametric_spec", "dimension binding fields rejected", binding=key)
            sketch = _find(candidate.sketches, row["sketch_uuid"], "sketch")
            dimension = _find(sketch.get("dimensions"), row["target_uuid"], "dimension")
            if dimension.get("kind") not in ("linear", "radial", "diameter"):
                raise _fail("unsupported_binding", "only driving length dimensions are supported")
            target = ("dimension", row["sketch_uuid"], row["target_uuid"])
            if target in targets:
                raise _fail("duplicate_binding", "multiple semantic keys write the same target")
            update_dimension(sketch, row["target_uuid"], value_nm=value)
        elif row.get("kind") == "feature":
            if set(row) != {"kind", "target_uuid", "field", "expression"}:
                raise _fail("bad_parametric_spec", "feature binding fields rejected", binding=key)
            feature = _find(candidate.features, row["target_uuid"], "feature")
            field = row["field"]
            params = feature.get("params")
            if (not isinstance(field, str) or not field.endswith("_nm")
                    or not isinstance(params, dict) or field not in params
                    or type(params[field]) is not int):
                raise _fail("unsupported_binding", "feature binding requires an existing scalar nm field")
            target = ("feature", row["target_uuid"], field)
            if target in targets:
                raise _fail("duplicate_binding", "multiple semantic keys write the same target")
            params[field] = value
        else:
            raise _fail("unsupported_binding", "binding kind is unsupported")
        targets.add(target)


def _checks(spec: Mapping[str, Any], values: Mapping[str, Quantity]) -> None:
    checks = spec.get("checks", [])
    if not isinstance(checks, list) or len(checks) > MAX_EXPRESSIONS:
        raise _fail("bad_parametric_spec", "checks must be a bounded list")
    keys = set()
    for row in checks:
        if not isinstance(row, Mapping) or set(row) != {"key", "left", "relation", "right"}:
            raise _fail("bad_parametric_spec", "check fields rejected")
        key = _name(row["key"])
        if key in keys:
            raise _fail("bad_parametric_spec", "duplicate check key")
        keys.add(key)
        left, right = _evaluate(row["left"], values), _evaluate(row["right"], values)
        if left.power != right.power:
            raise _fail("unit_mismatch", "check compares different dimensions", check=key)
        if row["relation"] not in (">", ">=", "<", "<=", "=="):
            raise _fail("bad_parametric_spec", "check relation is unsupported")
        accepted = {">": left.value > right.value, ">=": left.value >= right.value,
                    "<": left.value < right.value, "<=": left.value <= right.value,
                    "==": left.value == right.value}[row["relation"]]
        if not accepted:
            raise _fail("design_check_failed", "design check rejected parameter values", check=key)


def _geometry_guards(candidate: Document, spec: Mapping[str, Any],
                     values: Mapping[str, Quantity]) -> None:
    guards = _table(spec.get("geometry_guards", {}), "geometry guards", MAX_EXPRESSIONS)
    for key, row in guards.items():
        _name(key)
        if not isinstance(row, Mapping) or set(row) != {
                "kind", "rectangle_sketch_uuid", "circle_sketch_uuid", "circle_uuid", "clearance"}:
            raise _fail("bad_parametric_spec", "geometry guard fields rejected", guard=key)
        if row["kind"] != "circle_in_rectangle":
            raise _fail("unsupported_guard", "only XY circle-in-rectangle guards are supported")
        rectangle = _find(candidate.sketches, row["rectangle_sketch_uuid"], "rectangle sketch")
        sketch = _find(candidate.sketches, row["circle_sketch_uuid"], "circle sketch")
        circle = _find(sketch.get("entities"), row["circle_uuid"], "circle")
        entities = [entity for entity in rectangle.get("entities", [])
                    if not entity.get("construction", False)]
        if (rectangle.get("plane") != "XY" or sketch.get("plane") != "XY"
                or circle.get("kind") != "circle" or circle.get("construction", False)
                or len(entities) != 4 or any(e.get("kind") != "line" for e in entities)):
            raise _fail("unsupported_guard", "guard requires four XY lines and a real XY circle")
        points = [(e["geom"][x], e["geom"][y]) for e in entities
                  for x, y in (("x1", "y1"), ("x2", "y2"))]
        xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
        ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
        corners = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))
        sides = set()
        for index in range(4):
            endpoints = []
            for point in points[2 * index:2 * index + 2]:
                matches = [i for i, corner in enumerate(corners)
                           if max(abs(point[j] - corner[j]) for j in (0, 1)) <= 1]
                if len(matches) != 1:
                    raise _fail("invalid_guard_geometry", "outline is not an axis-aligned rectangle")
                endpoints.append(matches[0])
            sides.add(tuple(sorted(endpoints)))
        if sides != {(0, 1), (1, 2), (2, 3), (0, 3)}:
            raise _fail("invalid_guard_geometry", "rectangle does not contain each side exactly once")
        clearance = _length(_evaluate(row["clearance"], values))
        geom = circle["geom"]
        if clearance < 0 or geom["r"] <= 0 or min(
                geom["cx"] - geom["r"] - xmin, xmax - geom["cx"] - geom["r"],
                geom["cy"] - geom["r"] - ymin, ymax - geom["cy"] - geom["r"]) <= clearance:
            raise _fail("hole_outside_profile", "solved circle lacks strict interior clearance", guard=key)


def _validate_mesh(mesh: Any) -> dict:
    if not isinstance(mesh, dict):
        raise _fail("invalid_rebuild_mesh", "backend did not return a measured mesh")
    properties = mesh.get("properties", {})
    volume = properties.get("volume_nm3") if isinstance(properties, Mapping) else None
    if type(volume) is not int or volume <= 0 or type(mesh.get("solid_count")) is not int \
            or mesh["solid_count"] <= 0:
        raise _fail("invalid_rebuild_mesh", "mesh must have measured positive-volume solids")
    vertices, triangles = mesh.get("vertices_mm"), mesh.get("triangles")
    if not isinstance(vertices, list) or not vertices or not isinstance(triangles, list) \
            or not triangles or len(triangles) > 250000:
        raise _fail("invalid_rebuild_mesh", "empty or oversized measured mesh")
    for vertex in vertices:
        if not isinstance(vertex, (list, tuple)) or len(vertex) != 3 or any(
                type(value) not in (int, float) or not math.isfinite(value) for value in vertex):
            raise _fail("invalid_rebuild_mesh", "mesh has invalid real coordinates")
    for triangle in triangles:
        if not isinstance(triangle, (list, tuple)) or len(triangle) != 3 or any(
                type(index) is not int or index < 0 or index >= len(vertices) for index in triangle):
            raise _fail("invalid_rebuild_mesh", "mesh has invalid face indices")
    return copy.deepcopy(mesh)


def prepare_rebuild(document: Document, *, kernel: SubprocessKernel,
                    configuration: str | None = None,
                    overrides: Mapping[str, str] | None = None) -> PreparedRebuild:
    """Clone, evaluate, solve every sketch and measure the complete feature replay.

    A genuine SubprocessKernel is required: a numeric/fake geometry adapter cannot
    validate this operation. Exceptions leave the caller's Document untouched.
    """
    if not isinstance(document, Document):
        raise _fail("bad_document", "prepare_rebuild requires a real Document")
    if not isinstance(kernel, SubprocessKernel):
        raise _fail("real_kernel_required", "a real FreeCAD SubprocessKernel is required")
    base = document_to_l1(document)
    candidate = copy.deepcopy(document)
    spec = candidate.extra.get("parametric")
    if not isinstance(spec, dict):
        raise _fail("bad_parametric_spec", "Document.extra.parametric must be a mapping")
    persisted = evaluate_parameters(spec)
    feature_configuration(candidate, persisted, require_current=True)
    evaluation = evaluate_parameters(spec, configuration=configuration, overrides=overrides)
    _configure_features(candidate, evaluation)
    _checks(spec, evaluation.values)
    _bind(candidate, spec, evaluation.values)
    reports = []
    for sketch in candidate.sketches:
        if not isinstance(sketch, dict):
            raise _fail("bad_sketch", "sketch must be a mapping")
        if sketch.get("constraints") or sketch.get("dimensions"):
            result = solve(sketch, tol_nm=0.01)
            if not result.converged or not math.isfinite(result.residual):
                raise _fail("sketch_not_converged", "candidate sketch solver did not converge",
                            sketch=sketch.get("uuid"), residual=result.residual, status=result.status)
            reports.append({"uuid": sketch.get("uuid"), "converged": True, "dof": result.dof,
                            "residual_nm": result.residual, "iterations": result.iterations})
            # Iteration cost belongs to this run's report, not the document hash.
            sketch["solved"].pop("iterations", None)
        else:
            reports.append({"uuid": sketch.get("uuid"), "converged": None,
                            "dof": None, "residual_nm": None, "iterations": None})
    _geometry_guards(candidate, spec, evaluation.values)
    spec["active_configuration"] = evaluation.configuration
    spec["active_overrides"] = dict(evaluation.overrides)
    # Preserve the reader's extension diagnostics before measuring/fingerprinting.
    # The shared document schema is intentionally not modified by this module.
    candidate = document_from_l1(document_to_l1(candidate))
    l1 = document_to_l1(candidate)
    if document_to_l1(document_from_l1(l1)) != l1:
        raise _fail("unstable_document", "candidate L1 does not roundtrip byte-for-byte")
    try:
        shape = kernel.shape_from_document(json.loads(l1))
        mesh = _validate_mesh(kernel.tessellate(shape, deflection_mm=0.05))
    except CadError as exc:
        raise _fail("brep_rebuild_failed", "full feature B-Rep rebuild failed: " + str(exc),
                    kernel_reason=exc.reason) from exc
    sha = _digest(l1)
    recipe = copy.deepcopy(shape.to_dict())
    mesh.update(document_sha256=sha, recipe_sha256=_json_digest(recipe))
    mesh["mesh_sha256"] = _mesh_digest(mesh)
    prepared = PreparedRebuild(
        candidate, l1, _digest(base), sha, shape, recipe, mesh, evaluation,
        tuple(reports), tuple(feature["uuid"] for feature in candidate.features
                              if feature.get("suppressed") is not True))
    prepared.verify_integrity()
    return prepared
