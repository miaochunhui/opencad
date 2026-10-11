"""Replayable solid lofts; FreeCAD stays exclusively in the adapter process.

``make_step`` accepts at least two recipe-wrapped profiles and matching XYZ
translations in integer nanometres (absolute value <= 2**53, never bool).
Topology is checked on the actual replayed wires:
only closed, planar, single-loop profiles are supported, never holes or solids.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

__all__ = ["ADAPTER_SOURCE", "make_step"]


def _validate_loft_parameters(profiles, offsets_nm, ruled):
    """Single source for host and adapter validation; return detached JSON."""
    if not isinstance(profiles, list) or len(profiles) < 2:
        raise ValueError("loft requires at least two profiles")
    if not isinstance(ruled, bool):
        raise ValueError("loft ruled must be a bool")
    if not isinstance(offsets_nm, list) or len(offsets_nm) != len(profiles):
        raise ValueError("loft offsets_nm must match the profile count")
    for index, offset in enumerate(offsets_nm):
        if not isinstance(offset, list) or len(offset) != 3:
            raise ValueError("loft offset %s must contain three integer nanometres with abs <= 2**53" % index)
        for value in offset:
            if type(value) is not int or abs(value) > 2 ** 53:
                raise ValueError("loft offset %s must contain three integer nanometres with abs <= 2**53" % index)
    wrapped = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            raise ValueError("loft profile %s must wrap a nonempty recipe" % index)
        recipe = profile.get("recipe")
        if not isinstance(recipe, (list, tuple)) or not recipe:
            raise ValueError("loft profile %s must wrap a nonempty recipe" % index)
        if any(not isinstance(step, dict) or not isinstance(step.get("op"), str) or not step["op"]
               for step in recipe):
            raise ValueError("loft profile %s contains an invalid recipe step" % index)
        wrapped.append({"recipe": list(recipe)})
    try:
        return json.loads(json.dumps(
            {"op": "loft", "profiles": wrapped, "offsets_nm": offsets_nm, "ruled": ruled},
            allow_nan=False,
        ))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("loft requires finite JSON recipes: %s" % exc) from exc


def make_step(
    *,
    profiles: list[dict[str, Any]],
    offsets_nm: list[list[int]],
    ruled: bool = False,
) -> dict[str, Any]:
    """Validate and detach JSON recipes; invalid topology fails during replay."""
    return _validate_loft_parameters(profiles, offsets_nm, ruled)


ADAPTER_SOURCE = inspect.getsource(_validate_loft_parameters) + '''
def _build_loft(step, previous):
    validated = _validate_loft_parameters(
        step.get("profiles"), step.get("offsets_nm"), step.get("ruled", False))
    profiles = validated["profiles"]
    offsets = validated["offsets_nm"]
    ruled = validated["ruled"]
    wires = []
    for index, (profile, offset) in enumerate(zip(profiles, offsets)):
        shape = _build_recipe(profile["recipe"])
        if shape.isNull() or not shape.isValid():
            raise ValueError("loft profile %s is invalid" % index)
        if len(shape.Wires) != 1:
            raise ValueError("loft profile %s requires a single loop; holes and multiple loops are unsupported" % index)
        if shape.ShapeType != "Wire":
            raise ValueError("loft profile %s must be a wire, not a face or solid" % index)
        wire = shape.Wires[0].copy()
        if not wire.isClosed():
            raise ValueError("loft profile %s must be closed" % index)
        face = Part.Face(wire)
        if not face.isValid() or not isinstance(face.Surface, Part.Plane) or face.Area <= 0:
            raise ValueError("loft profile %s must enclose a positive planar area" % index)
        wire.translate(_vector(offset))
        wires.append(wire)
    # Solid=True caps closed sections; Closed=False must NOT join last to first.
    solid = Part.makeLoft(wires, True, ruled, False)
    if (solid.isNull() or not solid.isValid() or not solid.isClosed()
            or solid.ShapeType != "Solid" or len(solid.Solids) != 1
            or not math.isfinite(solid.Volume) or solid.Volume <= 0):
        raise ValueError("loft did not produce one valid closed positive-volume solid")
    # Return the concrete Solid API (including CenterOfMass), not a generic Shape.
    return solid.Solids[0]


_EXTENDED_BUILDERS['loft'] = _build_loft
'''
