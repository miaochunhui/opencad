"""Validated recipe steps and injectable, real FreeCAD Part solid sweeps.

Profiles use world coordinates, not an implicit local coordinate frame. The
path must start in the profile plane and its first segment must be normal to
that plane. Corners are mitered (Part transition mode 1); no implicit profile
translation, rotation, extrusion fallback or bounding-box approximation occurs.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
import math
from typing import Any

__all__ = ["make_step", "ADAPTER_SOURCE"]


def _sweep_copy_data(value, location):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("%s must have string keys" % location)
        return {
            key: _sweep_copy_data(item, "%s.%s" % (location, key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sweep_copy_data(item, "%s[%d]" % (location, index))
            for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("%s must contain finite JSON recipe data" % location)


def _sweep_validate(profile, path_points_nm):
    if not isinstance(profile, Mapping):
        raise ValueError("sweep profile must be a recipe mapping")
    recipe = profile.get("recipe")
    if not isinstance(recipe, (list, tuple)) or not recipe:
        raise ValueError("sweep profile recipe must be nonempty")
    if any(not isinstance(step, Mapping) or not isinstance(step.get("op"), str)
           or not step["op"] for step in recipe):
        raise ValueError("sweep profile recipe must contain operation mappings")
    copied_profile = {"recipe": _sweep_copy_data(recipe, "profile.recipe")}
    if not isinstance(path_points_nm, list) or len(path_points_nm) < 2:
        raise ValueError("sweep path_points_nm must be a list of at least two 3D points")
    points = []
    for index, point in enumerate(path_points_nm):
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise ValueError("sweep path point[%d] must have exactly 3 coordinates" % index)
        copied = []
        for axis, value in enumerate(point):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("sweep path point[%d][%d] must be integer nm, not boolean"
                                 % (index, axis))
            try:
                mm = value / 1_000_000
            except OverflowError as exc:
                raise ValueError("sweep path point[%d][%d] exceeds finite mm range"
                                 % (index, axis)) from exc
            if not math.isfinite(mm):
                raise ValueError("sweep path point[%d][%d] exceeds finite mm range"
                                 % (index, axis))
            copied.append(value)
        if points and copied == points[-1]:
            raise ValueError("sweep path has a zero-length segment at point[%d]" % index)
        points.append(copied)
    if len({tuple(point) for point in points}) != len(points):
        raise ValueError("sweep path must be open and cannot repeat a point")
    for index in range(1, len(points) - 1):
        before = [points[index][axis] - points[index - 1][axis] for axis in range(3)]
        after = [points[index + 1][axis] - points[index][axis] for axis in range(3)]
        cross = [before[1] * after[2] - before[2] * after[1],
                 before[2] * after[0] - before[0] * after[2],
                 before[0] * after[1] - before[1] * after[0]]
        if not any(cross) and sum(a * b for a, b in zip(before, after)) <= 0:
            raise ValueError("sweep path reverses direction at point[%d]" % index)
    return copied_profile, points


def make_step(
    *,
    profile: Mapping[str, Any],
    path_points_nm: list[list[int] | tuple[int, int, int]],
) -> dict[str, Any]:
    """Return copied sweep data; closure/planarity are checked by real FreeCAD.

    ``profile`` is ``{"recipe": [step, ...]}``, not a SubprocessShape handle.
    Coordinate components must be integers (never coerced from floats, strings
    or bools). Open, reversing, repeated-point paths are rejected before launch.
    Kernel geometry errors propagate through the existing adapter with causes.
    """
    copied_profile, points = _sweep_validate(profile, path_points_nm)
    return {"op": "sweep", "profile": copied_profile, "path_points_nm": points}


_BUILD_SOURCE = r'''
def _build_sweep(step, previous):
    profile_spec, path_nm = _sweep_validate(step.get("profile"), step.get("path_points_nm"))
    profile = _build_recipe(profile_spec["recipe"])
    if profile.isNull() or profile.Solids or profile.Shells:
        raise ValueError("sweep profile must be planar closed wires, not a solid or shell")
    wires = list(profile.Wires)
    if not wires or any(not wire.isClosed() or not wire.isValid() for wire in wires):
        raise ValueError("sweep profile must contain valid closed wires")
    face = _profile_face(profile)
    if not face.isValid() or len(face.Faces) != 1 or face.Area <= 0:
        raise ValueError("sweep profile must define one valid positive-area planar face")
    face = face.Faces[0]
    if not isinstance(face.Surface, Part.Plane):
        raise ValueError("sweep profile must be planar")
    normal = face.normalAt(0, 0)
    normal.normalize()
    anchor = face.Vertexes[0].Point
    points = [_vector(point) for point in path_nm]
    tolerance_mm = 1e-7
    if abs((points[0] - anchor).dot(normal)) > tolerance_mm:
        raise ValueError("sweep path must start in the profile plane")
    direction = points[1] - points[0]
    if direction.Length <= tolerance_mm:
        raise ValueError("sweep path segment is below the kernel tolerance")
    direction.normalize()
    if direction.cross(normal).Length > 1e-7:
        raise ValueError("sweep first path segment must be normal to the profile plane")
    edges = []
    for index in range(1, len(points)):
        if (points[index] - points[index - 1]).Length <= tolerance_mm:
            raise ValueError("sweep path segment[%d] is below the kernel tolerance" % index)
        edges.append(Part.makeLine(points[index - 1], points[index]))
    spine = Part.Wire(edges)
    if not spine.isValid() or spine.Length <= tolerance_mm:
        raise ValueError("sweep path did not build a valid wire")

    # Sweep the outer boundary and each hole separately, then subtract holes.
    # This keeps a multiply-connected profile hollow, rather than filling it.
    outer = face.OuterWire
    solid = spine.makePipeShell([outer], True, False, 1)
    for wire in face.Wires:
        if not wire.isSame(outer):
            hole = spine.makePipeShell([wire], True, False, 1)
            if hole.isNull() or not hole.isValid() or len(hole.Solids) != 1 or hole.Volume <= 0:
                raise ValueError("sweep hole did not produce a valid positive-volume solid")
            solid = solid.cut(hole)
    if (solid.isNull() or not solid.isValid() or len(solid.Solids) != 1
            or not math.isfinite(solid.Volume) or solid.Volume <= 0):
        raise ValueError("sweep did not produce one valid positive-volume solid")
    # FreeCAD's generic TopoShape has no CenterOfMass; the Solid subtype does.
    return solid.Solids[0]


_EXTENDED_BUILDERS['sweep'] = _build_sweep
'''

# Embed the same validators used by make_step, avoiding a second input contract.
# Only standard-library helpers and existing App/Part adapter globals are used.
ADAPTER_SOURCE = (
    "\nfrom collections.abc import Mapping\nimport math\n\n"
    + inspect.getsource(_sweep_copy_data)
    + "\n"
    + inspect.getsource(_sweep_validate)
    + "\n"
    + _BUILD_SOURCE
)
