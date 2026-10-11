"""Real FreeCAD thickness feature, carried as a replayable adapter snippet.

Face indices address ``previous.Faces`` in its current one-based order only.
They are not persistent topological names and must be reselected after topology
changes. The host inserts ADAPTER_SOURCE before _build_recipe, after defining
_EXTENDED_BUILDERS, and dispatches unknown steps through that registry.

Algorithm contract (measured with FreeCAD 0.19.2 / OCCT 7.5.1): makeThickness
uses the join algorithm with tolerance 1e-7 mm, intersection=False,
self_inter=False, offsetMode=0 (Skin), join=0 (Arc). Inward uses negative offset.
No simplified-algorithm fallback, boolean substitute, healing or retry is used.
"""

from __future__ import annotations

__all__ = ["ADAPTER_SOURCE", "make_step"]


def make_step(
    *, thickness_nm: int, face_indices: list[int], inward: bool = True
) -> dict:
    """Validate shape-independent parameters and return a detached recipe step."""
    if type(thickness_nm) is not int or thickness_nm <= 0:
        raise ValueError("shell thickness_nm must be a positive integer")
    if type(inward) is not bool:
        raise ValueError("shell inward must be a boolean")
    if type(face_indices) is not list or not face_indices:
        raise ValueError("shell face_indices must be a nonempty list")
    if any(type(index) is not int or index < 1 for index in face_indices):
        raise ValueError("shell face_indices must contain positive one-based integers")
    if len(set(face_indices)) != len(face_indices):
        raise ValueError("shell face_indices must not contain duplicates")
    return {
        "op": "shell",
        "thickness_nm": thickness_nm,
        "face_indices": list(face_indices),
        "inward": inward,
    }


ADAPTER_SOURCE = '''
def _build_shell(step, previous):
    thickness_nm = step.get("thickness_nm")
    indices = step.get("face_indices")
    inward = step.get("inward", True)
    if type(thickness_nm) is not int or thickness_nm <= 0:
        raise ValueError("shell thickness_nm must be a positive integer")
    if type(inward) is not bool:
        raise ValueError("shell inward must be a boolean")
    if type(indices) is not list or not indices:
        raise ValueError("shell face_indices must be a nonempty list")
    if any(type(index) is not int or index < 1 for index in indices):
        raise ValueError("shell face_indices must contain positive one-based integers")
    if len(set(indices)) != len(indices):
        raise ValueError("shell face_indices must not contain duplicates")
    if (previous is None or previous.isNull() or not previous.isValid()
            or previous.ShapeType != "Solid" or len(previous.Solids) != 1
            or previous.Volume <= 0):
        raise ValueError("shell requires one valid positive-volume previous solid")
    faces = previous.Faces
    if any(index > len(faces) for index in indices):
        raise ValueError("shell face index out of range: %s (face_count=%s)"
                         % (indices, len(faces)))
    if len(indices) == len(faces):
        raise ValueError("shell cannot remove every face")
    removed = [faces[index - 1] for index in indices]
    offset = _mm(thickness_nm) * (-1.0 if inward else 1.0)
    if not math.isfinite(offset) or offset == 0:
        raise ValueError("shell thickness is not representable in millimetres")
    tolerance = 1e-7
    # FreeCAD 0.19 positional order: faces, offset, tolerance,
    # intersection, self_inter, offsetMode (Skin=0), join (Arc=0).
    context = ("FreeCAD=%s OCCT=%s faces=%s offset_mm=%r tolerance_mm=%r "
               "intersection=False self_inter=False offsetMode=0 join=0"
               % (repr(App.Version()),
                  Part.OCC_VERSION, indices, offset, tolerance))
    try:
        result = previous.makeThickness(
            removed, offset, tolerance, False, False, 0, 0)
    except Exception as exc:
        raise RuntimeError("shell makeThickness failed (%s): %s: %s"
                           % (context, type(exc).__name__, exc)) from exc
    if (result.isNull() or not result.isValid() or result.ShapeType != "Solid"
            or len(result.Solids) != 1 or result.Volume <= 0):
        raise ValueError("shell makeThickness produced no valid solid (%s)" % context)
    volume_tol = max(1e-9, previous.Volume * 1e-9)
    overlap = result.common(previous).Volume
    if inward:
        if (result.Volume >= previous.Volume - volume_tol
                or abs(overlap - result.Volume) > volume_tol):
            raise ValueError("shell inward offset did not form an interior wall (%s)"
                             % context)
    elif abs(overlap) > volume_tol:
        raise ValueError("shell outward offset retained the original interior (%s)"
                         % context)
    for index, face in zip(indices, removed):
        area_tol = max(1e-10, face.Area * 1e-9)
        if result.common(face).Area >= face.Area - area_tol:
            raise ValueError("shell opening face %s remains capped (%s)"
                             % (index, context))
    return result.Solids[0]


_EXTENDED_BUILDERS["shell"] = _build_shell
'''
