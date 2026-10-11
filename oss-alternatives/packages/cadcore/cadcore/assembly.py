"""Immutable, bounded hierarchical assemblies with real BRep evaluation.

Lengths are integer nanometres; rotations are proper row-major 3x3 matrices.
This API is independent of the legacy convex-box assembly operations and L1
schema. Parts retain exact, immutable L1 bytes and their SHA256.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .errors import CadError

__all__ = ["PartReference", "Transform", "Component", "Mate", "Assembly",
           "evaluate_assembly", "solve_mates", "bom", "save_assembly", "load_assembly"]

IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
MAX_NM = 10**12
MAX_PARTS = 32
MAX_COMPONENTS = 64
MAX_DEPTH = 8
MAX_MATES = 64
MAX_MOVABLE = 12
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_ASSEMBLY_BYTES = 8 * 1024 * 1024


def _fail(reason, detail, **data):
    return CadError(detail, reason=reason, **data)


def _name(value, what):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise _fail("bad_assembly_input", "%s must be a nonempty bounded string" % what)
    return value


def _nm(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_NM:
        raise _fail("bad_assembly_input", "%s must be bounded integer nm" % what)
    return value


def _number(value, what):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise _fail("bad_assembly_input", "%s must be finite" % what)
    return float(value)


def _vadd(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _vsub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _mv(matrix, vector):
    return tuple(_dot(row, vector) for row in matrix)


def _mmul(a, b):
    return tuple(tuple(sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def _rotation(vector):
    angle = _norm(vector)
    if angle < 1e-15:
        return IDENTITY
    x, y, z = (value / angle for value in vector)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return ((t*x*x+c, t*x*y-s*z, t*x*z+s*y),
            (t*x*y+s*z, t*y*y+c, t*y*z-s*x),
            (t*x*z-s*y, t*y*z+s*x, t*z*z+c))


@dataclass(frozen=True)
class Transform:
    rotation: tuple = IDENTITY
    translation_nm: tuple = (0, 0, 0)

    def __post_init__(self):
        try:
            if len(self.rotation) != 3 or any(len(row) != 3 for row in self.rotation):
                raise ValueError("rotation dimensions")
            matrix = tuple(tuple(_number(x, "rotation") for x in row) for row in self.rotation)
            if len(self.translation_nm) != 3:
                raise ValueError("translation dimensions")
            translation = tuple(_nm(x, "translation") for x in self.translation_nm)
        except (TypeError, ValueError) as exc:
            raise _fail("bad_assembly_input", "transform must have 3x3 rotation and 3D translation") from exc
        error = max(abs(_dot(matrix[i], matrix[j]) - float(i == j))
                    for i in range(3) for j in range(3))
        determinant = _dot(matrix[0], _cross(matrix[1], matrix[2]))
        if error > 1e-8 or abs(determinant - 1) > 1e-8:
            raise _fail("bad_assembly_input", "rotation must be orthonormal with determinant +1")
        object.__setattr__(self, "rotation", matrix)
        object.__setattr__(self, "translation_nm", translation)

    def as_dict(self):
        return {"rotation": [list(row) for row in self.rotation],
                "translation_nm": list(self.translation_nm)}


def _strict_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _fail("bad_assembly_input", "duplicate JSON key: %s" % key)
            result[key] = value
        return result
    def nonfinite(value):
        raise _fail("bad_assembly_input", "nonfinite JSON: %s" % value)
    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, RecursionError) as exc:
        raise _fail("bad_assembly_input", "invalid or excessively nested JSON") from exc
    pending, count = [(value, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100000:
            raise _fail("assembly_limit", "JSON depth/node count exceeds bound")
        if isinstance(node, dict):
            pending.extend((v, depth+1) for v in node.values())
        elif isinstance(node, list):
            pending.extend((v, depth+1) for v in node)
    return value


@dataclass(frozen=True)
class PartReference:
    part_id: str
    document_sha256: str
    l1_text: str = field(repr=False)
    configuration: str = "default"

    def __post_init__(self):
        _name(self.part_id, "part_id")
        _name(self.configuration, "configuration")
        if not isinstance(self.l1_text, str) or not self.l1_text:
            raise _fail("bad_part_reference", "part must carry exact nonempty L1 text")
        if len(self.l1_text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise _fail("assembly_limit", "part document exceeds size limit")
        sha = hashlib.sha256(self.l1_text.encode("utf-8")).hexdigest()
        if self.document_sha256 != sha:
            raise _fail("part_digest_mismatch", "part L1 bytes do not match document_sha256")
        value = _strict_json(self.l1_text)
        if not isinstance(value, dict) or not isinstance(value.get("meta"), dict):
            raise _fail("bad_part_reference", "part must be an L1 document")
        from .docmodel import document_from_l1, load_blocking_problems
        document = document_from_l1(self.l1_text)
        problems = load_blocking_problems(document)
        if problems:
            raise _fail("bad_part_reference", "; ".join(problems[:8]))
        if len(value.get("features", [])) > 128 or sum(
                len(sk.get("entities", [])) for sk in value.get("sketches", [])) > 2048:
            raise _fail("assembly_limit", "part feature or entity count exceeds limit")

    @classmethod
    def from_document(cls, part_id, document, *, configuration="default"):
        from .docmodel import document_to_l1
        text = document if isinstance(document, str) else document_to_l1(document)
        return cls(part_id, hashlib.sha256(text.encode("utf-8")).hexdigest(), text, configuration)

    def as_dict(self):
        return {"part_id": self.part_id, "configuration": self.configuration,
                "document_sha256": self.document_sha256, "l1_text": self.l1_text}


@dataclass(frozen=True)
class Component:
    component_id: str
    part_id: str | None = None
    parent_id: str | None = None
    transform: Transform = field(default_factory=Transform)
    configuration: str = "default"
    fixed: bool = False

    def __post_init__(self):
        _name(self.component_id, "component_id")
        for name in ("part_id", "parent_id"):
            if getattr(self, name) is not None:
                _name(getattr(self, name), name)
        _name(self.configuration, "configuration")
        if not isinstance(self.transform, Transform) or not isinstance(self.fixed, bool):
            raise _fail("bad_assembly_input", "component needs Transform and boolean fixed")

    def as_dict(self):
        return {"component_id": self.component_id, "part_id": self.part_id,
                "parent_id": self.parent_id, "transform": self.transform.as_dict(),
                "configuration": self.configuration, "fixed": self.fixed}


@dataclass(frozen=True)
class Mate:
    mate_id: str
    kind: str
    a: str
    a_face: int
    b: str
    b_face: int
    offset_nm: int = 0
    aligned: bool = True

    def __post_init__(self):
        for name in ("mate_id", "a", "b"):
            _name(getattr(self, name), name)
        if self.kind not in ("plane", "axis") or self.a == self.b:
            raise _fail("bad_mate", "mate needs two different instances and plane/axis kind")
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 or x > 4095
               for x in (self.a_face, self.b_face)):
            raise _fail("bad_mate", "BRep face indices must be bounded nonnegative integers")
        _nm(self.offset_nm, "offset_nm")
        if not isinstance(self.aligned, bool) or (self.kind == "axis" and self.offset_nm != 0):
            raise _fail("bad_mate", "axis offset is not supported; aligned must be boolean")

    def as_dict(self):
        return dict(vars(self))


@dataclass(frozen=True)
class Assembly:
    parts: tuple[PartReference, ...]
    components: tuple[Component, ...]
    mates: tuple[Mate, ...] = ()

    def __post_init__(self):
        for name in ("parts", "components", "mates"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not 1 <= len(self.parts) <= MAX_PARTS or not 1 <= len(self.components) <= MAX_COMPONENTS:
            raise _fail("assembly_limit", "assembly part/component count exceeds bounds or is empty")
        if len(self.mates) > MAX_MATES:
            raise _fail("assembly_limit", "assembly mate count exceeds bounds")
        if any(not isinstance(p, PartReference) for p in self.parts) or any(
                not isinstance(c, Component) for c in self.components) or any(
                not isinstance(m, Mate) for m in self.mates):
            raise _fail("bad_assembly_input", "assembly needs typed immutable parts/components/mates")
        part_keys = {(p.part_id, p.configuration) for p in self.parts}
        nodes = {c.component_id: c for c in self.components}
        if len(part_keys) != len(self.parts) or len(nodes) != len(self.components):
            raise _fail("bad_assembly_input", "duplicate part/configuration or instance ID")
        if len({m.mate_id for m in self.mates}) != len(self.mates):
            raise _fail("bad_mate", "duplicate mate ID")
        leaves = [c for c in self.components if c.part_id is not None]
        if not 1 <= len(leaves) <= MAX_PARTS:
            raise _fail("assembly_limit", "assembly must have 1..32 part instances")
        for component in self.components:
            if component.part_id is not None and (component.part_id, component.configuration) not in part_keys:
                raise _fail("unknown_part", "component refers to unknown part/configuration")
            seen = {component.component_id}
            parent = component.parent_id
            while parent is not None:
                if parent not in nodes:
                    raise _fail("unknown_parent", "component parent is absent")
                if parent in seen:
                    raise _fail("assembly_cycle", "component hierarchy contains a cycle")
                seen.add(parent)
                if len(seen) > MAX_DEPTH:
                    raise _fail("assembly_limit", "component hierarchy exceeds depth bound")
                if nodes[parent].part_id is not None:
                    raise _fail("bad_assembly_input", "part instances cannot parent other instances")
                parent = nodes[parent].parent_id
        leaf_ids = {c.component_id for c in leaves}
        for mate in self.mates:
            if mate.a not in leaf_ids or mate.b not in leaf_ids:
                raise _fail("bad_mate", "mate endpoints must be actual part instances")
        if len(self.to_json().encode("utf-8")) > MAX_ASSEMBLY_BYTES:
            raise _fail("assembly_limit", "assembly serialization exceeds size bound")

    def as_dict(self):
        return {"schema": 1, "parts": [p.as_dict() for p in self.parts],
                "components": [c.as_dict() for c in self.components],
                "mates": [m.as_dict() for m in self.mates]}

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text):
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_ASSEMBLY_BYTES:
            raise _fail("assembly_limit", "assembly input exceeds serialized size bound")
        value = _strict_json(text)
        if not isinstance(value, dict) or set(value) != {"schema", "parts", "components", "mates"}:
            raise _fail("bad_assembly_input", "unexpected assembly fields")
        if type(value["schema"]) is not int or value["schema"] != 1:
            raise _fail("bad_assembly_input", "unsupported assembly schema")
        if any(not isinstance(value[key], list) for key in ("parts", "components", "mates")):
            raise _fail("bad_assembly_input", "assembly tables must be lists")
        if len(value["parts"]) > MAX_PARTS or len(value["components"]) > MAX_COMPONENTS or len(
                value["mates"]) > MAX_MATES:
            raise _fail("assembly_limit", "serialized record counts exceed bounds")
        try:
            components = []
            for raw in value["components"]:
                raw = dict(raw)
                raw["transform"] = Transform(**raw["transform"])
                components.append(Component(**raw))
            return cls(tuple(PartReference(**p) for p in value["parts"]), tuple(components),
                       tuple(Mate(**m) for m in value["mates"]))
        except (KeyError, TypeError) as exc:
            raise _fail("bad_assembly_input", "invalid assembly record: %s" % exc) from exc


def _poses(assembly, local=None):
    nodes = {c.component_id: c for c in assembly.components}
    result = {}

    def visit(identifier):
        if identifier in result:
            return result[identifier]
        c = nodes[identifier]
        rotation, translation = (local[identifier] if local is not None else
                                 (c.transform.rotation, tuple(x/1e6 for x in c.transform.translation_nm)))
        if c.parent_id is not None:
            pr, pt = visit(c.parent_id)
            rotation, translation = _mmul(pr, rotation), _vadd(_mv(pr, translation), pt)
        if any(not math.isfinite(x) or abs(x) > MAX_NM/1e6 for x in translation):
            raise _fail("assembly_limit", "world transform exceeds coordinate bound")
        result[identifier] = rotation, translation
        return result[identifier]

    for identifier in nodes:
        visit(identifier)
    return result


def _kernel_data(assembly, kernel):
    parts = []
    for part in assembly.parts:
        shape = kernel.shape_from_document(_strict_json(part.l1_text))
        parts.append({"part_id": part.part_id, "configuration": part.configuration,
                      "document_sha256": part.document_sha256, "recipe": list(shape.recipe)})
    return parts


def evaluate_assembly(assembly: Assembly, kernel, *, mesh=True):
    """Rebuild distinct parts and measure placed BReps; never mutate input/kernel profile."""
    if not isinstance(assembly, Assembly) or not isinstance(mesh, bool):
        raise _fail("bad_assembly_input", "evaluation requires Assembly and boolean mesh")
    _poses(assembly)
    from .kernel.assembly_worker import run_assembly_job
    return run_assembly_job(kernel, {"op": "assembly", "parts": _kernel_data(assembly, kernel),
                                    "assembly": assembly.as_dict(), "mesh": mesh})


def bom(assembly: Assembly):
    """Count physical leaf instances, grouped by immutable part/configuration/SHA."""
    parts = {(p.part_id, p.configuration): p for p in assembly.parts}
    counts = Counter((c.part_id, c.configuration) for c in assembly.components if c.part_id is not None)
    return [{"part_id": key[0], "configuration": key[1],
             "document_sha256": parts[key].document_sha256, "quantity": quantity,
             "component_ids": sorted(c.component_id for c in assembly.components
                                     if (c.part_id, c.configuration) == key)}
            for key, quantity in sorted(counts.items())]


def _orientation(a, b):
    cross, dot = _cross(a, b), max(-1.0, min(1.0, _dot(a, b)))
    length = _norm(cross)
    if length < 1e-12:
        if dot > 0:
            return (0.0, 0.0, 0.0)
        seed = min(IDENTITY, key=lambda axis: abs(_dot(axis, a)))
        axis = _cross(a, seed)
        return tuple(math.pi * x / _norm(axis) for x in axis)
    return tuple(math.atan2(length, dot) * x / length for x in cross)


def _residuals(assembly, references, local, *, details=False):
    poses = _poses(assembly, local)
    residuals, records = [], []
    for mate in assembly.mates:
        pa, da = references[(mate.a, mate.a_face, mate.kind)]
        pb, db = references[(mate.b, mate.b_face, mate.kind)]
        ra, ta = poses[mate.a]
        rb, tb = poses[mate.b]
        pa, pb = _vadd(_mv(ra, pa), ta), _vadd(_mv(rb, pb), tb)
        da, db = _mv(ra, da), _mv(rb, db)
        if not mate.aligned:
            db = tuple(-x for x in db)
        angular = _orientation(da, db)
        delta = _vsub(pb, pa)
        if mate.kind == "plane":
            linear = (_dot(delta, da) - mate.offset_nm/1e6,)
        else:
            linear = _vsub(delta, tuple(_dot(delta, da)*x for x in da))
        # Unit balancing: one mm linear residual and one radian angular residual.
        residuals.extend((*angular, *linear))
        records.append({"mate_id": mate.mate_id, "kind": mate.kind,
                        "linear_residual_nm": _norm(linear)*1e6,
                        "angular_residual_rad": _norm(angular)})
    return (residuals, records) if details else residuals


def _perturb(local, movable, delta):
    updated = dict(local)
    for index, identifier in enumerate(movable):
        rotation, translation = local[identifier]
        block = delta[index*6:index*6+6]
        updated[identifier] = (_mmul(_rotation(block[3:]), rotation),
                               _vadd(translation, block[:3]))
    return updated


def _jacobian(assembly, references, local, movable):
    base = _residuals(assembly, references, local)
    columns = []
    for index in range(6*len(movable)):
        delta = [0.0]*(6*len(movable))
        delta[index] = 1e-5 if index % 6 < 3 else 1e-6
        positive = _residuals(assembly, references, _perturb(local, movable, delta))
        delta[index] *= -1
        negative = _residuals(assembly, references, _perturb(local, movable, delta))
        columns.append([(p-n)/(-2*delta[index]) for p, n in zip(positive, negative)])
    return [[column[row] for column in columns] for row in range(len(base))]


def _rank(matrix, tolerance=1e-7):
    if not matrix:
        return 0
    rows = [list(row) for row in matrix]
    rank = 0
    for column in range(len(rows[0])):
        pivot = max(range(rank, len(rows)), key=lambda i: abs(rows[i][column]), default=rank)
        if rank >= len(rows) or abs(rows[pivot][column]) < tolerance:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        scale = rows[rank][column]
        rows[rank] = [value/scale for value in rows[rank]]
        for i in range(rank+1, len(rows)):
            scale = rows[i][column]
            rows[i] = [a-scale*b for a, b in zip(rows[i], rows[rank])]
        rank += 1
        if rank == len(rows):
            break
    return rank


def _linear_solve(matrix, rhs):
    rows = [list(row) + [value] for row, value in zip(matrix, rhs)]
    size = len(rows)
    for column in range(size):
        pivot = max(range(column, size), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) < 1e-18:
            raise _fail("mate_solve_failed", "damped normal system is singular")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [value/scale for value in rows[column]]
        for i in range(size):
            if i != column:
                scale = rows[i][column]
                rows[i] = [a-scale*b for a, b in zip(rows[i], rows[column])]
    return [row[-1] for row in rows]


def solve_mates(assembly: Assembly, kernel, *, tolerance_nm=100, tolerance_rad=1e-7,
                max_iterations=60):
    """Numerically solve real plane/axis references and report local tangent DoF.

    Returns ``(new_assembly, report)``. Fixed means local placement fixed: a fixed
    child still follows a moving parent. Redundant mates and inconsistent
    systems raise CadError with measured residual/rank; input stays immutable.
    """
    if not isinstance(assembly, Assembly):
        raise _fail("bad_assembly_input", "solver requires Assembly")
    _nm(tolerance_nm, "tolerance_nm")
    tolerance_rad = _number(tolerance_rad, "tolerance_rad")
    if tolerance_nm < 1 or tolerance_nm > 10000 or not 1e-10 <= tolerance_rad <= 1e-3:
        raise _fail("bad_mate", "mate tolerance outside bounded solver contract")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 100:
        raise _fail("assembly_limit", "solver iterations must be 1..100")
    movable = [c.component_id for c in assembly.components if not c.fixed]
    if len(movable) > MAX_MOVABLE:
        raise _fail("assembly_limit", "solver supports at most 12 moving nodes")
    measured = evaluate_assembly(assembly, kernel, mesh=False)
    references = {}
    for c in measured["instances"]:
        for face in c["faces"]:
            if face["kind"] in ("plane", "axis"):
                references[(c["component_id"], face["face_index"], face["kind"])] = (
                    tuple(x/1e6 for x in face["point_nm"]), tuple(face["direction"]))
    for mate in assembly.mates:
        for identifier, index in ((mate.a, mate.a_face), (mate.b, mate.b_face)):
            if (identifier, index, mate.kind) not in references:
                raise _fail("bad_mate_reference", "face is absent or not the requested analytic surface",
                            component_id=identifier, face_index=index, kind=mate.kind)
    local = {c.component_id: (c.transform.rotation,
                             tuple(x/1e6 for x in c.transform.translation_nm)) for c in assembly.components}
    damping = 1e-6
    iterations = 0
    for iterations in range(max_iterations+1):
        residual, records = _residuals(assembly, references, local, details=True)
        if all(r["linear_residual_nm"] <= tolerance_nm and
               r["angular_residual_rad"] <= tolerance_rad for r in records):
            break
        if not movable or iterations == max_iterations:
            raise _fail("mate_solve_failed", "mate residual remains above tolerance",
                        residuals=records, iterations=iterations)
        jacobian = _jacobian(assembly, references, local, movable)
        size = 6*len(movable)
        normal = [[sum(row[i]*row[j] for row in jacobian) + (damping if i == j else 0)
                   for j in range(size)] for i in range(size)]
        rhs = [-sum(row[i]*value for row, value in zip(jacobian, residual)) for i in range(size)]
        delta = _linear_solve(normal, rhs)
        # Bound per-step rotations to keep the local linearization meaningful.
        largest = max((_norm(delta[i+3:i+6]) for i in range(0, size, 6)), default=0)
        if largest > 0.5:
            delta = [value*0.5/largest for value in delta]
        trial = _perturb(local, movable, delta)
        if _norm(_residuals(assembly, references, trial)) < _norm(residual):
            local, damping = trial, max(1e-10, damping/3)
        else:
            damping = min(1e12, damping*10)
    components = tuple(replace(c, transform=Transform(
        local[c.component_id][0], tuple(int(round(x*1e6)) for x in local[c.component_id][1])))
        for c in assembly.components)
    solved = replace(assembly, components=components)
    rounded = {c.component_id: (c.transform.rotation, tuple(x/1e6 for x in c.transform.translation_nm))
               for c in solved.components}
    _, records = _residuals(assembly, references, rounded, details=True)
    if any(r["linear_residual_nm"] > tolerance_nm or r["angular_residual_rad"] > tolerance_rad
           for r in records):
        raise _fail("mate_solve_failed", "integer-nm result does not meet mate tolerance", residuals=records)
    jacobian = _jacobian(assembly, references, rounded, movable)
    rank = _rank(jacobian)
    accumulated, previous, cursor = [], 0, 0
    for mate in assembly.mates:
        count = 4 if mate.kind == "plane" else 6
        accumulated.extend(jacobian[cursor:cursor+count])
        next_rank = _rank(accumulated)
        # Plane plus coaxial axis intentionally share orientation equations.
        # Reject a wholly redundant mate, not this standard locating joint.
        if next_rank == previous:
            raise _fail("mate_overconstrained", "mate adds redundant constraints",
                        mate_id=mate.mate_id, jacobian_rank=rank, residuals=records)
        previous, cursor = next_rank, cursor+count
    # Rebuild solved placements, not only algebraic reference points.
    final = evaluate_assembly(solved, kernel, mesh=False)
    return solved, {"status": "solved", "iterations": iterations, "residuals": records,
                    "jacobian_rank": rank, "dof": 6*len(movable)-rank,
                    "variables": 6*len(movable), "evaluation": final,
                    "assembly_sha256": hashlib.sha256(solved.to_json().encode()).hexdigest()}


def save_assembly(assembly: Assembly, path):
    """Persist exact assembly bytes using file fsync, replace, directory fsync."""
    path = Path(path).absolute()
    if path.is_symlink() or not path.parent.is_dir():
        raise _fail("bad_assembly_path", "save needs existing parent and non-symlink target")
    fd, temporary = tempfile.mkstemp(prefix=".assembly-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(assembly.to_json().encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def load_assembly(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ASSEMBLY_BYTES:
        raise _fail("bad_assembly_path", "load needs bounded regular non-symlink assembly file")
    return Assembly.from_json(path.read_text(encoding="utf-8"))
