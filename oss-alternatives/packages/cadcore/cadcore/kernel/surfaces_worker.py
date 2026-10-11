# SPDX-License-Identifier: Apache-2.0
"""FreeCAD-only worker. Run via SurfaceKernel, never import heavy CAD in host."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from surfaces import validate_recipe, validate_seam, validate_trim_loops, _number, _fields

MARKER = "cadcore-surfaces-v1"
MM = 1_000_000.0


def _vector(point):
    return App.Vector(*[coordinate / MM for coordinate in point])


def _valid(shape, label):
    if shape.isNull() or not shape.isValid() or not shape.Faces:
        raise ValueError("%s did not produce valid nonempty exact BRep faces" % label)
    if not math.isfinite(shape.Area) or shape.Area <= 0:
        raise ValueError("%s did not produce positive area" % label)
    return shape


def _solid(shape, label):
    _valid(shape, label)
    if len(shape.Solids) != 1 or not shape.isClosed():
        raise ValueError("%s requires one closed solid" % label)
    solid = shape.Solids[0]
    if not math.isfinite(solid.Volume) or solid.Volume <= 0 or not solid.isValid():
        raise ValueError("%s did not produce positive-volume valid solid" % label)
    return solid


def _face(shape):
    if len(shape.Faces) != 1 or shape.Solids:
        raise ValueError("parameter operation requires exactly one surface face")
    return shape.Faces[0]


def build(recipe, reports, offset_checks):
    op = recipe["op"]
    if op == "nurbs":
        surface = Part.BSplineSurface()
        surface.buildFromPolesMultsKnots(
            [[_vector(p) for p in row] for row in recipe["poles_nm"]],
            recipe["u_mults"], recipe["v_mults"], recipe["u_knots"], recipe["v_knots"],
            False, False, recipe["u_degree"], recipe["v_degree"], recipe.get("weights"))
        face = _valid(surface.toShape(), op)
        if not isinstance(face.Surface, Part.BSplineSurface):
            raise ValueError("nurbs result lost its BSplineSurface geometry")
        return face
    if op == "trim":
        face = _face(build(recipe["surface"], reports, offset_checks))
        u0, u1, v0, v1 = face.ParameterRange
        if "outer" in recipe:
            validate_trim_loops(recipe["outer"], recipe["holes"], [u0, u1, v0, v1])
            from FreeCAD import Base
            surface = face.Surface
            wires = []
            for ring in [recipe["outer"], *recipe["holes"]]:
                edges = [
                    Part.Geom2d.Line2dSegment(Base.Vector2d(*a), Base.Vector2d(*b)).toShape(surface)
                    for a, b in zip(ring, ring[1:])]
                wire = Part.Wire(edges)
                if not wire.isClosed() or not wire.isValid() or len(wire.Edges) != len(edges):
                    raise ValueError("trim UV ring did not produce a closed valid exact wire")
                wires.append(wire)
            # The surface/list overload adds wires to the default rectangle.
            # The surface/wire overload instead establishes the true outer ring.
            trimmed = Part.Face(surface, wires[0])
            for wire in wires[1:]:
                trimmed = Part.Face(trimmed, wire)
            _valid(trimmed, "UV polygon trim")
            if len(trimmed.Wires) != len(wires) or any(not w.isClosed() for w in trimmed.Wires):
                raise ValueError("trim lost outer or hole wires")
            if not isinstance(trimmed.Surface, Part.BSplineSurface):
                raise ValueError("trim lost its exact NURBS support")
            for edge in trimmed.Edges:
                if trimmed.curveOnSurface(edge) is None:
                    raise ValueError("trim edge has no authoritative UV pcurve")
            return trimmed
        a, b, c, d = recipe["bounds"]
        if not (u0 <= a < b <= u1 and v0 <= c < d <= v1):
            raise ValueError("trim bounds exceed the current face parameter domain")
        # Segment the support too: OCCT's generic bbox otherwise includes
        # original NURBS poles outside the trimmed face.
        surface = face.Surface.copy()
        surface.segment(a, b, c, d)
        return _valid(surface.toShape(), op)
    if op == "sew":
        faces = [_face(build(item, reports, offset_checks)) for item in recipe["surfaces"]]
        for spec in recipe["seams"]:
            settings = {key: value for key, value in spec.items() if key not in ("left", "right")}
            report = seam_report(faces[spec["left"] - 1], faces[spec["right"] - 1], settings)
            reports.append(report)
            if not report["passed"]:
                raise ValueError("sew seam continuity rejected: %s" %
                                 json.dumps(report, allow_nan=False, sort_keys=True))
        shape = Part.makeCompound(faces)
        shape.sewShape()
        _valid(shape, op)
        if len(shape.Shells) != 1 or len(shape.Faces) != len(faces):
            raise ValueError("sew requires one connected shell without lost faces")
        shell = shape.Shells[0]
        if len(shell.Faces) != len(faces):
            raise ValueError("sew requires one connected shell containing every input face")
        if not shell.isValid():
            raise ValueError("sew produced invalid shell")
        if not recipe["solid"]:
            return shell
        if not shell.isClosed():
            raise ValueError("sew solid rejected unclosed shell")
        solid = Part.makeSolid(shell)
        if solid.Volume < 0:
            solid.reverse()
        return _solid(solid, "sew")
    if op in ("offset", "thicken"):
        shape = build(recipe["surface"], reports, offset_checks)
        if shape.Solids:
            raise ValueError("%s requires a surface, not a solid" % op)
        distance = recipe["offset_nm"] if op == "offset" else recipe["thickness_nm"]
        if op == "thicken" and recipe["inward"]:
            distance = -distance
        # OCCT skin offset, no self-intersection option; intersection join (2).
        result = shape.makeOffsetShape(distance / MM, 1e-6, False, False, 0, 2,
                                       op == "thicken")
        offset_checks.append(_offset_regularity(shape, distance / MM))
        if op == "thicken":
            return _solid(result, op)
        if result.Solids:
            raise ValueError("offset unexpectedly produced a solid")
        return _valid(result, op)
    raise ValueError("unsupported surface operation: %s" % op)


def _unit(vector, label):
    if not math.isfinite(vector.Length) or vector.Length <= 1e-12:
        raise ValueError("undefined %s at singular surface parameter" % label)
    return vector / vector.Length


def _derivatives(face, u, v):
    u0, u1, v0, v1 = face.ParameterRange
    if not (u0 <= u <= u1 and v0 <= v <= v1):
        raise ValueError("evaluation parameter outside trimmed face")
    if not face.isPartOfDomain(u, v):
        raise ValueError("evaluation parameter outside actual trimmed UV domain or inside a hole")
    surface = face.Surface
    p = surface.getD0(u, v)
    du = surface.getDN(u, v, 1, 0)
    dv = surface.getDN(u, v, 0, 1)
    normal = _unit(du.cross(dv), "normal")
    return p, du, dv, normal


def _second_form(face, u, v, du, dv, normal, tangent, cross):
    """Second fundamental form in a common orthonormal world tangent basis."""
    surface = face.Surface
    e, f, g = du.dot(du), du.dot(dv), dv.dot(dv)
    determinant = e * g - f * f
    if determinant <= 1e-24:
        raise ValueError("undefined curvature at singular surface parameter")
    l = normal.dot(surface.getDN(u, v, 2, 0))
    m = normal.dot(surface.getDN(u, v, 1, 1))
    n = normal.dot(surface.getDN(u, v, 0, 2))

    def coefficients(direction):
        a, b = direction.dot(du), direction.dot(dv)
        return ((g * a - f * b) / determinant, (e * b - f * a) / determinant)

    def form(a, b):
        return l * a[0] * b[0] + m * (a[0] * b[1] + a[1] * b[0]) + n * a[1] * b[1]

    x, y = coefficients(tangent), coefficients(cross)
    return [form(x, x), form(x, y), form(y, y)]


def _offset_regularity(shape, distance):
    """Reject sampled focal crossing even when OCCT returns a valid solid."""
    minimum = float("inf")
    count = 0
    witness = None
    for index, face in enumerate(shape.Faces, 1):
        u0, u1, v0, v1 = face.ParameterRange
        us = _samples(face, "v_min", 17)
        vs = _samples(face, "u_min", 17)
        for ut in sorted(us):
            for vt in sorted(vs):
                u, v = u0 + (u1 - u0) * ut, v0 + (v1 - v0) * vt
                if not face.isPartOfDomain(u, v):
                    continue
                _p, du, dv, normal = _derivatives(face, u, v)
                if face.Orientation == "Reversed":
                    normal = -normal
                tangent = _unit(du, "offset tangent")
                cross = _unit(normal.cross(tangent), "offset transverse tangent")
                a, b, c = _second_form(face, u, v, du, dv, normal, tangent, cross)
                discriminant = math.hypot(a - c, 2 * b)
                curvatures = [(a + c - discriminant) / 2, (a + c + discriminant) / 2]
                factors = [1 - distance * curvature for curvature in curvatures]
                if not all(math.isfinite(value) for value in factors):
                    raise ValueError("offset regularity has nonfinite derivative data")
                count += 1
                if min(factors) < minimum:
                    minimum = min(factors)
                    witness = dict(face_index=index, uv=[u, v],
                                   principal_curvatures_per_mm=curvatures, factors=factors)
    if count == 0:
        raise ValueError("offset regularity has no samples in the actual UV domain")
    report = dict(offset_mm=distance, sample_count=count, minimum_regular_factor=minimum,
                  whole_domain_proof=False, witness=witness,
                  method="exact DN; sampled principal-curvature factors 1-offset*curvature")
    if minimum <= 1e-8:
        raise ValueError("offset regularity rejected focal crossing: %s" %
                         json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _boundary(face, edge, t):
    a, b, c, d = face.ParameterRange
    if edge.startswith("u"):
        u, v = (a if edge == "u_min" else b), c + (d - c) * t
    else:
        u, v = a + (b - a) * t, (c if edge == "v_min" else d)
    p, du, dv, normal = _derivatives(face, u, v)
    tangent = _unit(dv if edge.startswith("u") else du, "seam tangent")
    return (u, v, p, du, dv, normal, tangent)


def _angle(a, b, *, unoriented=False):
    dot = max(-1.0, min(1.0, a.dot(b)))
    if unoriented:
        dot = abs(dot)
    return math.acos(dot)


def _samples(face, edge, count):
    values = set(index / (count - 1) for index in range(count))
    surface = face.Surface
    if isinstance(surface, Part.BSplineSurface):
        a, b, c, d = face.ParameterRange
        low, high = (c, d) if edge.startswith("u") else (a, b)
        knots = surface.getVKnots() if edge.startswith("u") else surface.getUKnots()
        partitions = sorted(set([low, high] + [k for k in knots if low < k < high]))
        for x, y in zip(partitions, partitions[1:]):
            for value in (x, (x + y) / 2, y):
                values.add((value - low) / (high - low))
    return values


def seam_report(left, right, settings):
    validate_seam(settings)
    left, right = _face(left), _face(right)
    reverse = settings["reverse"]
    samples = _samples(left, settings["left_edge"], settings["samples"])
    samples.update(1 - t if reverse else t for t in
                   _samples(right, settings["right_edge"], settings["samples"]))
    rows = []
    required = settings["continuity"]
    for t in sorted(samples):
        a = _boundary(left, settings["left_edge"], t)
        b = _boundary(right, settings["right_edge"], 1 - t if reverse else t)
        bt = -b[6] if reverse else b[6]
        gap = (a[2] - b[2]).Length * MM
        normal_angle = _angle(a[5], b[5], unoriented=True)
        tangent_angle = _angle(a[6], bt)
        curvature = None
        if required == "G2":
            normal_b = b[5] if a[5].dot(b[5]) >= 0 else -b[5]
            tangent = a[6]
            cross = _unit(a[5].cross(tangent), "seam transverse tangent")
            qa = _second_form(left, a[0], a[1], a[3], a[4], a[5], tangent, cross)
            qb = _second_form(right, b[0], b[1], b[3], b[4], normal_b, tangent, cross)
            curvature = max(abs(x - y) for x, y in zip(qa, qb))
        rows.append(dict(t=t, gap_nm=gap, normal_angle_rad=normal_angle,
                         tangent_angle_rad=tangent_angle,
                         curvature_difference_per_mm=curvature))
    gap = max(row["gap_nm"] for row in rows)
    angle = max(max(row["normal_angle_rad"], row["tangent_angle_rad"]) for row in rows)
    curve = max(row["curvature_difference_per_mm"] for row in rows) if required == "G2" else None
    g0 = gap <= settings["position_tolerance_nm"]
    g1 = g0 and angle <= settings["angle_tolerance_rad"]
    g2 = g1 and curve <= settings["curvature_tolerance_per_mm"] if required == "G2" else None
    return dict(passed={"G0": g0, "G1": g1, "G2": g2}[required],
                requested=required, g0=g0, g1=g1, g2=g2,
                max_gap_nm=gap, max_angle_rad=angle,
                max_curvature_difference_per_mm=curve, sample_count=len(rows),
                method="exact D0/DN; sampled boundary; common-world second fundamental form",
                whole_domain_proof=False, settings=settings, measurements=rows)


def _properties(shape):
    _valid(shape, "measurement")
    # 0.19.2 has a broken optional-argument parser; no-argument call is safe.
    # cleaned() removes triangulation, so AddOptimal must use BRep geometry.
    bounds = shape.cleaned().optimalBoundingBox()
    faces = []
    for face in shape.Faces:
        surface = face.Surface
        row = dict(type=type(surface).__name__, bounds=list(face.ParameterRange),
                   wire_count=len(face.Wires), inner_wire_count=len(face.Wires) - 1,
                   wires_closed=all(w.isClosed() for w in face.Wires))
        if isinstance(surface, Part.BSplineSurface):
            row.update(u_degree=surface.UDegree, v_degree=surface.VDegree,
                       rational=bool(surface.isURational() or surface.isVRational()))
        faces.append(row)
    return dict(shape_type=shape.ShapeType, valid=shape.isValid(), closed=shape.isClosed(),
                face_count=len(shape.Faces), shell_count=len(shape.Shells),
                solid_count=len(shape.Solids), area_mm2=shape.Area,
                inner_wire_count=sum(row["inner_wire_count"] for row in faces),
                volume_mm3=shape.Volume if shape.Solids else None,
                bbox_mm=[bounds.XMin, bounds.YMin, bounds.ZMin,
                         bounds.XMax, bounds.YMax, bounds.ZMax],
                bbox_method="OCCT AddOptimal; no triangulation; numerical precision margin",
                bbox_tolerance_mm=1e-6, faces=faces)


def _export(shape, result_dir):
    artifacts, roundtrip = {}, {}
    expected = _properties(shape)
    for fmt in ("brep", "step"):
        path = result_dir / ("shape." + fmt)
        if fmt == "brep":
            shape.exportBrep(str(path))
            loaded = Part.Shape()
            loaded.importBrep(str(path))
        else:
            shape.exportStep(str(path))
            loaded = Part.read(str(path))
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("%s export missing or empty" % fmt)
        actual = _properties(loaded)
        for field in ("face_count", "solid_count", "inner_wire_count"):
            if actual[field] != expected[field]:
                raise ValueError("%s roundtrip lost %s" % (fmt, field))
        if (sorted(row["inner_wire_count"] for row in actual["faces"])
                != sorted(row["inner_wire_count"] for row in expected["faces"])
                or not all(row["wires_closed"] for row in actual["faces"])):
            raise ValueError("%s roundtrip changed per-face closed hole topology" % fmt)
        for field in ("area_mm2", "volume_mm3"):
            a, b = actual[field], expected[field]
            if b is not None and (a is None or abs(a - b) > max(1e-7, abs(b) * 1e-7)):
                raise ValueError("%s roundtrip changed %s" % (fmt, field))
        if expected["solid_count"] and not actual["closed"]:
            raise ValueError("%s roundtrip solid is not closed" % fmt)
        artifacts[fmt] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              bytes=path.stat().st_size)
        roundtrip[fmt] = actual
    return artifacts, roundtrip


def main():
    global App, Part
    result_path = os.environ.get("CADCORE_SURFACE_RESULT")
    request_path = os.environ.get("CADCORE_SURFACE_REQUEST")
    result = dict(ok=False, marker=MARKER, schema=1)
    try:
        if not result_path or not request_path:
            raise ValueError("missing surface worker request/result paths")
        import FreeCAD as App
        import Part
        components = list(App.Version())
        result["backend"] = dict(freecad=".".join(x for x in components[:3] if x),
                                 freecad_components=components,
                                 occt=str(getattr(Part, "OCC_VERSION", "unmeasured")))
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        op = request.get("op")
        result["op"] = op
        reports = []
        offset_checks = []
        if op == "probe":
            _fields(request, ("op",), ("op",), "probe")
            surface = Part.BSplineSurface()
            result["apis"] = {name: callable(getattr(surface, name, None)) for name in
                              ("buildFromPolesMultsKnots", "toShape", "segment", "getD0", "getDN")}
            result["apis"].update({name: callable(getattr(Part.Shape(), name, None)) for name in
                                   ("sewShape", "makeOffsetShape", "cleaned", "optimalBoundingBox",
                                    "tessellate", "exportBrep", "exportStep")})
            result["apis"].update(
                uv_segment=callable(getattr(Part.Geom2d, "Line2dSegment", None)),
                uv_pcurve=callable(getattr(Part.Face(), "curveOnSurface", None)),
                uv_domain=callable(getattr(Part.Face(), "isPartOfDomain", None)))
            if not all(result["apis"].values()):
                raise ValueError("required FreeCAD surface API missing")
        elif op in ("build", "evaluate"):
            field = "deflection_mm" if op == "build" else "uv"
            _fields(request, ("op", "surface", field), ("op", "surface", field), op)
            recipe = validate_recipe(request["surface"])
            shape = build(recipe, reports, offset_checks)
            result["properties"] = _properties(shape)
            result["seams"] = reports
            result["offset_checks"] = offset_checks
            if op == "build":
                deflection = _number(request[field], field, positive=True)
                vertices, triangles = shape.tessellate(deflection)
                if not vertices or not triangles or len(triangles) > 250000:
                    raise ValueError("surface mesh empty or exceeds triangle budget")
                result["mesh"] = dict(vertices_mm=[list(tuple(p)) for p in vertices],
                                      triangles=[list(t) for t in triangles],
                                      source="exact BRep tessellation", deflection_mm=deflection)
                result["artifacts"], result["roundtrip"] = _export(shape, Path(result_path).parent)
            else:
                face = _face(shape)
                pairs = request["uv"]
                if not isinstance(pairs, list) or not 1 <= len(pairs) <= 256:
                    raise ValueError("evaluation uv requires 1..256 parameter pairs")
                rows = []
                for pair in pairs:
                    if not isinstance(pair, list) or len(pair) != 2:
                        raise ValueError("evaluation uv requires parameter pairs")
                    u, v = [_number(x, "evaluation parameter") for x in pair]
                    p, du, dv, normal = _derivatives(face, u, v)
                    rows.append(dict(uv=pair, point_mm=list(tuple(p)), du=list(tuple(du)),
                                     dv=list(tuple(dv)), normal=list(tuple(normal))))
                result["evaluations"] = rows
        elif op == "seam":
            _fields(request, ("op", "left", "right", "seam"),
                    ("op", "left", "right", "seam"), op)
            result["seam"] = seam_report(build(validate_recipe(request["left"]), reports, offset_checks),
                                         build(validate_recipe(request["right"]), reports, offset_checks),
                                         validate_seam(request["seam"]))
        else:
            raise ValueError("unsupported surface worker request: %s" % op)
        result["ok"] = True
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["traceback"] = traceback.format_exc()
        sys.stderr.write(result["traceback"])
    if result_path:
        path = Path(result_path)
        pending = path.with_suffix(".part")
        pending.write_text(json.dumps(result, allow_nan=False, sort_keys=True), encoding="utf-8")
        pending.replace(path)
    print(MARKER, "PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 3


# FreeCADCmd can import its script rather than executing it as __main__.
_rc = main()
if __name__ == "__main__":
    sys.exit(_rc)
