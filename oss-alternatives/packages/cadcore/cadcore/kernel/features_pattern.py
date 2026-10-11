"""Validated recipe steps and real FreeCAD BRep pattern/mirror builders.

Translations enumerate ALL instances: the original is retained only when
[0, 0, 0] is present. A lone identity is rejected as a degenerate pattern.
Mirror planes are x/y/z = offset_nm; keep_original makes a compound, not a
union. A coincident original/reflection pair is rejected.

Insert ADAPTER_SOURCE after _EXTENDED_BUILDERS = {} and before _build_recipe.
The owning adapter dispatches _EXTENDED_BUILDERS[op](step, previous).
"""

from __future__ import annotations

from typing import Literal

from ..errors import GeometryError

__all__ = ["make_pattern_step", "make_mirror_step", "ADAPTER_SOURCE"]


def make_pattern_step(
    *, translations_nm: list[list[int]], fuse: bool = False
) -> dict:
    """Enumerate 1..256 distinct translations in integer nanometres."""
    if type(fuse) is not bool:
        raise GeometryError("fuse must be boolean", reason="invalid_pattern_parameters")
    if type(translations_nm) is not list or not 1 <= len(translations_nm) <= 256:
        raise GeometryError("pattern needs 1..256 translations",
                            reason="invalid_pattern_parameters")
    translations = []
    seen = set()
    for index, point in enumerate(translations_nm):
        if (type(point) is not list or len(point) != 3
                or any(type(value) is not int for value in point)):
            raise GeometryError("translation must contain three integer nanometres",
                                reason="invalid_pattern_parameters", index=index)
        key = tuple(point)
        if key in seen:
            raise GeometryError("duplicate pattern translation",
                                reason="invalid_pattern_parameters", index=index)
        seen.add(key)
        translations.append(list(point))
    if seen == {(0, 0, 0)}:
        raise GeometryError("identity-only pattern is degenerate",
                            reason="invalid_pattern_parameters")
    return {"op": "pattern", "translations_nm": translations, "fuse": fuse}


def make_mirror_step(
    *, normal_axis: Literal["x", "y", "z"], offset_nm: int = 0,
    keep_original: bool = False
) -> dict:
    """Reflect a complete BRep across one principal plane."""
    if (type(normal_axis) is not str or normal_axis not in ("x", "y", "z")
            or type(offset_nm) is not int or type(keep_original) is not bool):
        raise GeometryError("mirror needs x/y/z, integer offset and boolean keep_original",
                            reason="invalid_mirror_parameters")
    return {"op": "mirror", "normal_axis": normal_axis, "offset_nm": offset_nm,
            "keep_original": keep_original}


ADAPTER_SOURCE = '''
def _pattern_solid(shape, operation):
    if (shape is None or shape.isNull() or not shape.isValid()
            or not shape.Solids or not math.isfinite(shape.Volume) or shape.Volume <= 0):
        raise ValueError(operation + " requires a valid positive-volume BRep solid")
    # Reject mixed wire/shell/solid compounds rather than copying non-solid debris.
    if (len(shape.Faces) != sum(len(s.Faces) for s in shape.Solids)
            or len(shape.Edges) != sum(len(s.Edges) for s in shape.Solids)):
        raise ValueError(operation + " requires only solids, not loose faces or edges")
    return shape


def _pattern_nm(value, operation):
    if type(value) is not int:
        raise ValueError(operation + " requires integer nanometres, not bool or float")
    try:
        result = _mm(value)
    except OverflowError as exc:
        raise ValueError(operation + " offset is outside the numeric range") from exc
    if not math.isfinite(result):
        raise ValueError(operation + " offset is outside the numeric range")
    return result


def _build_pattern(step, previous):
    translations = step.get("translations_nm")
    fuse = step.get("fuse", False)
    if type(fuse) is not bool:
        raise ValueError("pattern fuse must be boolean")
    if type(translations) is not list or not 1 <= len(translations) <= 256:
        raise ValueError("pattern needs 1..256 translations")
    seen = set()
    numeric_seen = set()
    for point in translations:
        if type(point) is not list or len(point) != 3:
            raise ValueError("pattern translation needs exactly three coordinates")
        numeric = tuple(_pattern_nm(value, "pattern") for value in point)
        key = tuple(point)
        if key in seen or numeric in numeric_seen:
            raise ValueError("duplicate pattern translation")
        seen.add(key)
        numeric_seen.add(numeric)
    if seen == {(0, 0, 0)}:
        raise ValueError("identity-only pattern is degenerate")
    source = _pattern_solid(previous, "pattern")
    instances = []
    for point in translations:
        instance = source.copy()
        instance.translate(_vector(point))
        instances.append(_pattern_solid(instance, "pattern"))
    if fuse:
        result = instances[0]
        for instance in instances[1:]:
            result = result.fuse(instance)
        result = result.removeSplitter()
    else:
        result = Part.makeCompound(instances)
    return _pattern_solid(result, "pattern")


def _build_mirror(step, previous):
    axis = step.get("normal_axis")
    keep = step.get("keep_original", False)
    if type(axis) is not str or axis not in ("x", "y", "z"):
        raise ValueError("mirror normal_axis must be x/y/z")
    if type(keep) is not bool:
        raise ValueError("mirror keep_original must be boolean")
    offset = _pattern_nm(step.get("offset_nm", 0), "mirror")
    source = _pattern_solid(previous, "mirror")
    matrix = App.Matrix()
    if axis == "x":
        matrix.A11, matrix.A14 = -1.0, 2.0 * offset
    elif axis == "y":
        matrix.A22, matrix.A24 = -1.0, 2.0 * offset
    else:
        matrix.A33, matrix.A34 = -1.0, 2.0 * offset
    # transformShape transforms the entire BRep, including analytic faces/holes,
    # and corrects orientation for a negative-determinant rigid reflection.
    mirrored = source.copy()
    mirrored.transformShape(matrix, False, True)
    _pattern_solid(mirrored, "mirror")
    if not keep:
        return mirrored
    left_difference = source.cut(mirrored)
    right_difference = mirrored.cut(source)
    if not left_difference.Solids and not right_difference.Solids:
        raise ValueError("mirror keep_original would duplicate a coincident solid")
    return _pattern_solid(Part.makeCompound([source.copy(), mirrored]), "mirror")


_EXTENDED_BUILDERS.update({"pattern": _build_pattern, "mirror": _build_mirror})
'''
