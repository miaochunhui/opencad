"""Real FreeCAD edge finishing recipes, kept outside the CLI adapter core.

``edge_indices`` are one-based positions in the previous shape's current Edges
array. They are not persistent topological names and may change after rebuilding
or after any preceding feature.

The adapter core inserts ADAPTER_SOURCE after creating _EXTENDED_BUILDERS and
before _build_recipe, then dispatches unknown steps through that registry.
Only the external FreeCAD process imports the CAD modules.
"""

from __future__ import annotations

import inspect
from typing import Literal

from ..errors import GeometryError

__all__ = ["ADAPTER_SOURCE", "make_step"]


def _edge_finish_parameters(kind, size_nm, edge_indices):
    if not isinstance(kind, str) or kind not in ("fillet", "chamfer"):
        raise ValueError("edge finish kind must be fillet or chamfer")
    if isinstance(size_nm, bool) or not isinstance(size_nm, int) or size_nm <= 0:
        raise ValueError("edge finish size_nm must be a positive integer in nm")
    if not isinstance(edge_indices, list) or not edge_indices:
        raise ValueError("edge_indices must be a nonempty list of one-based integers")
    seen = set()
    for index in edge_indices:
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
            raise ValueError("edge_indices must contain positive one-based integers")
        if index in seen:
            raise ValueError("edge_indices must be unique; duplicate index: %s" % index)
        seen.add(index)
    return {"op": kind, "size_nm": size_nm, "edge_indices": list(edge_indices)}


def make_step(
    *,
    kind: Literal["fillet", "chamfer"],
    size_nm: int,
    edge_indices: list[int],
) -> dict:
    """Validate a recipe step; edge range and solid geometry are checked on replay."""
    try:
        return _edge_finish_parameters(kind, size_nm, edge_indices)
    except ValueError as exc:
        raise GeometryError(str(exc), reason="invalid_edge_finish_parameters") from exc


# Share validation verbatim with the external script, without importing cadcore
# or a CAD library into the FreeCAD process or the calling process respectively.
ADAPTER_SOURCE = inspect.getsource(_edge_finish_parameters) + '''

def _edge_finish_bounds(shape, label):
    if (shape is None or shape.isNull() or not shape.isValid()
            or not shape.Solids or not math.isfinite(shape.Volume) or shape.Volume <= 0):
        raise ValueError("%s requires a valid positive-volume solid" % label)
    if any(not math.isfinite(solid.Volume) or solid.Volume <= 0 for solid in shape.Solids):
        raise ValueError("%s contains a non-positive-volume solid" % label)
    box = shape.BoundBox
    bounds = tuple(float(getattr(box, name)) for name in
                   ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"))
    if (not all(math.isfinite(value) for value in bounds)
            or any(bounds[i + 3] <= bounds[i] for i in range(3))):
        raise ValueError("%s has an invalid solid bounding box" % label)
    return bounds


def _build_edge_finish(step, previous):
    if previous is None:
        raise ValueError("edge finish requires a prior solid")
    before = _edge_finish_bounds(previous, "edge finish input")
    parameters = _edge_finish_parameters(
        step.get("op"), step.get("size_nm"), step.get("edge_indices"))
    edges = previous.Edges
    selected = []
    for index in parameters["edge_indices"]:
        if index > len(edges):
            raise ValueError("edge index %s outside one-based range 1..%s" % (index, len(edges)))
        selected.append(edges[index - 1])
    try:
        size = _mm(parameters["size_nm"])
    except (OverflowError, ValueError) as exc:
        raise ValueError("edge finish size_nm cannot be represented in mm") from exc
    if not math.isfinite(size) or size <= 0:
        raise ValueError("edge finish size must be finite and positive in mm")
    kind = parameters["op"]
    try:
        if kind == "fillet":
            result = previous.makeFillet(size, selected)
        else:
            result = previous.makeChamfer(size, selected)
    except Exception as exc:
        raise ValueError("%s failed: size_nm=%s edge_indices=%s: %s: %s" %
                         (kind, parameters["size_nm"], parameters["edge_indices"],
                          type(exc).__name__, exc)) from exc
    after = _edge_finish_bounds(result, "%s result" % kind)
    if len(result.Solids) != len(previous.Solids):
        raise ValueError("%s changed the number of solids" % kind)
    tolerance = max(1e-7, max(abs(value) for value in before) * 1e-12)
    if any(after[i] < before[i] - tolerance or after[i + 3] > before[i + 3] + tolerance
           for i in range(3)):
        raise ValueError("%s result exceeds the prior solid bounds" % kind)
    # FreeCAD 0.19's generic TopoShape does not expose CenterOfMass. A single
    # Solid exposes it; compounds use the adapter's shared mass-center routine.
    solids = result.Solids
    return solids[0] if len(solids) == 1 else result


_EXTENDED_BUILDERS["fillet"] = _build_edge_finish
_EXTENDED_BUILDERS["chamfer"] = _build_edge_finish
'''
