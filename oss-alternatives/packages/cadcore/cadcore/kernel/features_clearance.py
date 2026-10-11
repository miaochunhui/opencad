"""Real BRep separation, contact and interference through the FreeCAD adapter.

Operands are world-coordinate solid recipes, not origins or bounding boxes.
Distances/points are rounded to integer nanometres; intersection volumes are
rounded to integer nm^3. A positive value below that representation's resolution
raises unless real intersection/contact topology proves zero clearance.
FreeCAD's own BRep tolerances still apply. ``measure`` requires a fully
populated, self-consistent response.
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
import math
from typing import Any

from .subproc import KernelExecutionError

__all__ = ["make_job", "measure", "ADAPTER_SOURCE"]


def _clearance_nm_data(value, location):
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError("%s must not be empty" % location)
        return [_clearance_nm_data(item, "%s[%d]" % (location, index))
                for index, item in enumerate(value)]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be integer nm, not boolean" % location)
    try:
        finite = math.isfinite(value / 1_000_000)
    except OverflowError as exc:
        raise ValueError("%s exceeds finite mm range" % location) from exc
    if not finite:
        raise ValueError("%s exceeds finite mm range" % location)
    if abs(value) > 2**53:
        raise ValueError("%s exceeds safe integer nm range" % location)
    return value


def _clearance_copy_data(value, location):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("%s must have string keys" % location)
        result = {}
        for key, item in value.items():
            child = "%s.%s" % (location, key)
            if key.endswith("_nm") or key in ("p1", "p2", "center", "points"):
                result[key] = _clearance_nm_data(item, child)
                if key in ("distance_nm", "radius_nm") and (
                        not isinstance(result[key], int) or result[key] <= 0):
                    raise ValueError("%s must be a positive integer nm dimension" % child)
            else:
                result[key] = _clearance_copy_data(item, child)
        return result
    if isinstance(value, (list, tuple)):
        return [_clearance_copy_data(item, "%s[%d]" % (location, index))
                for index, item in enumerate(value)]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("%s must contain finite JSON recipe data" % location)


def _clearance_operand(value, location):
    if not isinstance(value, Mapping):
        raise ValueError("%s must be a recipe mapping" % location)
    recipe = value.get("recipe")
    if not isinstance(recipe, (list, tuple)) or not recipe:
        raise ValueError("%s.recipe must be nonempty" % location)
    if any(not isinstance(step, Mapping) or not isinstance(step.get("op"), str)
           or not step["op"] for step in recipe):
        raise ValueError("%s.recipe must contain operation mappings" % location)
    return {"recipe": _clearance_copy_data(recipe, location + ".recipe")}


def make_job(*, a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Copy two finite solid recipes into the adapter's clearance job."""
    return {"op": "clearance", "a": _clearance_operand(a, "a"),
            "b": _clearance_operand(b, "b")}


def _validate_clearance_response(payload):
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise ValueError("clearance response must explicitly report ok=true")
    if payload.get("op") != "clearance":
        raise ValueError("clearance response operation mismatch")
    data = payload.get("clearance")
    if not isinstance(data, Mapping):
        raise ValueError("clearance response is missing its measurement mapping")
    values = {}
    for name in ("distance_nm", "interference_volume_nm3"):
        value = data.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("clearance.%s must be a nonnegative integer" % name)
        values[name] = value
    pairs = data.get("nearest_points_nm")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("clearance.nearest_points_nm must contain A/B point pairs")
    copied = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("clearance nearest pair[%d] must contain exactly A and B" % index)
        points = []
        for point in pair:
            if not isinstance(point, (list, tuple)) or len(point) != 3:
                raise ValueError("clearance nearest pair[%d] must contain 3D points" % index)
            points.append(_clearance_nm_data(point, "clearance.nearest_points_nm"))
        try:
            pair_distance = math.dist(points[0], points[1])
        except OverflowError as exc:
            raise ValueError("clearance nearest pair distance exceeds finite range") from exc
        if not math.isfinite(pair_distance) or abs(pair_distance - values["distance_nm"]) > 2:
            raise ValueError("clearance nearest pair distance disagrees with distance_nm")
        copied.append(points)
    distance, volume = values["distance_nm"], values["interference_volume_nm3"]
    if distance > 0 and volume > 0:
        raise ValueError("clearance reports both positive separation and interference")
    status = "separated" if distance > 0 else "penetrating" if volume > 0 else "touching"
    if data.get("status") != status:
        raise ValueError("clearance status disagrees with distance and intersection volume")
    return {**values, "nearest_points_nm": copied, "status": status}


def measure(kernel, a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Run real BRep measurement; no missing/empty field is defaulted to zero.

    Kernel failures propagate unchanged. Protocol failures retain their first
    cause through ``KernelExecutionError(reason='invalid_clearance_response')``.
    """
    job = make_job(a=a, b=b)
    payload = kernel._run_job(job)
    try:
        return _validate_clearance_response(payload)
    except (ValueError, TypeError, OverflowError) as exc:
        raise KernelExecutionError("invalid clearance response: %s" % exc,
                                   reason="invalid_clearance_response",
                                   op="clearance") from exc


_MEASURE_SOURCE = r'''
def _clearance_solid(spec, name):
    shape = _build_recipe(spec["recipe"])
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("clearance %s must build a valid positive-volume solid" % name)
    for solid in shape.Solids:
        if (not solid.isClosed() or not math.isfinite(solid.Volume) or solid.Volume <= 0):
            raise ValueError("clearance %s contains an invalid or nonpositive solid" % name)
    return shape


def _clearance_topology_witness(shape):
    # An actual shared/section vertex belongs to both operands. Never use a
    # bounding-box point, centre of mass or mismatched extrema support pair.
    if not shape.Vertexes:
        raise ValueError("clearance intersection topology has no vertex witness")
    point = shape.Vertexes[0].Point
    coordinates = [point.x, point.y, point.z]
    if not all(math.isfinite(value) for value in coordinates):
        raise ValueError("clearance intersection witness is nonfinite")
    nm = [int(round(value * 1000000)) for value in coordinates]
    return [[nm, list(nm)]]


def _measure_clearance(job):
    a = _clearance_operand(job.get("a"), "a")
    b = _clearance_operand(job.get("b"), "b")
    left = _clearance_solid(a, "a")
    right = _clearance_solid(b, "b")
    common = left.common(right)
    if not common.isNull() and not common.isValid():
        raise ValueError("clearance intersection produced an invalid BRep")
    volumes = [solid.Volume for solid in common.Solids]
    if any(not solid.isClosed() for solid in common.Solids):
        raise ValueError("clearance intersection contains an open shared solid")
    if any(not math.isfinite(volume) or volume <= 0 for volume in volumes):
        raise ValueError("clearance intersection contains an invalid shared solid volume")
    volume_mm3 = sum(volumes)
    if not math.isfinite(volume_mm3) or volume_mm3 < 0:
        raise ValueError("clearance intersection volume is invalid")
    volume_nm3 = int(round(volume_mm3 * 1000000000000000000))
    if volume_mm3 > 0 and volume_nm3 == 0:
        raise ValueError("positive interference is below integer-nm3 resolution")
    if volume_nm3 > 0:
        return {"distance_nm": 0, "nearest_points_nm": _clearance_topology_witness(common),
                "interference_volume_nm3": volume_nm3, "status": "penetrating"}
    contact = common
    if not (contact.Faces or contact.Edges or contact.Vertexes):
        # Solid common() can discard lower-dimensional contact in OCCT.
        contact = left.section(right)
        if not contact.isNull() and not contact.isValid():
            raise ValueError("clearance contact section produced an invalid BRep")
    if contact.Faces or contact.Edges or contact.Vertexes:
        return {"distance_nm": 0, "nearest_points_nm": _clearance_topology_witness(contact),
                "interference_volume_nm3": 0, "status": "touching"}

    reading = left.distToShape(right)
    if not isinstance(reading, (list, tuple)) or len(reading) != 3:
        raise ValueError("distToShape did not return distance, point pairs and support info")
    distance_mm, pairs, support = reading
    if (isinstance(distance_mm, bool) or not isinstance(distance_mm, (int, float))
            or not math.isfinite(distance_mm) or distance_mm < 0):
        raise ValueError("distToShape returned an invalid distance")
    if not isinstance(pairs, (list, tuple)) or not pairs:
        raise ValueError("distToShape returned no nearest point pairs")
    distance_nm = int(round(distance_mm * 1000000))
    if distance_mm > 0 and distance_nm == 0:
        raise ValueError("positive clearance is below integer-nanometre resolution")
    if distance_mm == 0:
        raise ValueError("zero BRep distance has no shared or contact topology witness")
    nearest = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("distToShape returned malformed nearest point pair[%d]" % index)
        copied = []
        for point in pair:
            if not isinstance(point, App.Vector):
                raise ValueError("distToShape nearest point is not a FreeCAD vector")
            coordinates = [point.x, point.y, point.z]
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError("distToShape nearest point is nonfinite")
            copied.append([int(round(value * 1000000)) for value in coordinates])
        nearest.append(copied)

    return {"distance_nm": distance_nm, "nearest_points_nm": nearest,
            "interference_volume_nm3": 0, "status": "separated"}
'''

ADAPTER_SOURCE = (
    "\nfrom collections.abc import Mapping\nimport math\n\n"
    + inspect.getsource(_clearance_nm_data)
    + "\n"
    + inspect.getsource(_clearance_copy_data)
    + "\n"
    + inspect.getsource(_clearance_operand)
    + "\n"
    + _MEASURE_SOURCE
)
