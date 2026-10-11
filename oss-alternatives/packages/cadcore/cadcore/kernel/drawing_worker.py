"""Isolated FreeCAD BRep drawing job; inputs are fixed files in its own cwd.

The parent copies this source to job/scripts and supplies OPENCAD_DRAWING_JOB.
No mesh, outline-only fallback, user path, or substitute geometry is accepted.
Drawing.projectEx's installed API documents [V,V1,VN,VO,VI,H,H1,HN,HO,HI].
"""

import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True
NM = 1000000
MAX_STEP_BYTES = 64 * 1024 * 1024
BASES = {
    "top": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "front": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "right": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "iso": ((1 / math.sqrt(2), 1 / math.sqrt(2), 0),
            (-1 / math.sqrt(6), 1 / math.sqrt(6), 2 / math.sqrt(6)),
            (1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3))),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def vector_nm(point):
    return [int(round(float(item) * NM)) for item in point]


def checked_solid(shape):
    if shape.isNull() or not shape.isValid() or not shape.Solids:
        raise ValueError("BRep is not a valid solid")
    if not math.isfinite(shape.Volume) or shape.Volume <= 0:
        raise ValueError("BRep volume is not positive and finite")
    return shape


def flattened(shape, view):
    import FreeCAD as App

    matrix = App.Matrix()
    for row, basis in enumerate(BASES[view], 1):
        for column, value in enumerate(basis, 1):
            setattr(matrix, "A%d%d" % (row, column), value)
    result = shape.copy()
    result.transformShape(matrix)
    return result


def edge_data(edge, kind):
    import Part

    curve = edge.Curve
    linear = isinstance(curve, Part.Line) or isinstance(curve, Part.LineSegment)
    circular = isinstance(curve, Part.Circle)
    points = []
    for point in edge.discretize(2 if linear else 65):
        flat = vector_nm((point.x, point.y))
        if not points or flat != points[-1]:
            points.append(flat)
    if len(points) < 2:
        raise ValueError("HLR produced a degenerate edge")
    data = {"kind": kind, "geometry": "line" if linear else "polyline",
            "points_nm": points}
    if circular:
        data.update(geometry="circle" if edge.isClosed() else "arc",
                    center_nm=vector_nm((curve.Center.x, curve.Center.y)),
                    radius_nm=int(round(curve.Radius * NM)))
    if data["geometry"] == "circle":
        key = (kind, "circle", tuple(data["center_nm"]), data["radius_nm"])
    else:
        key = (kind, tuple(min(points, list(reversed(points)))[0]),
               json.dumps(min(points, list(reversed(points)))))
    data["edge_sha256"] = digest(repr(key).encode())
    return data


def hlr(shape, view):
    import Drawing
    import FreeCAD as App

    projector = getattr(Drawing, "projectEx", None)
    if not callable(projector):
        raise RuntimeError("exact BRep HLR unavailable: Drawing.projectEx required")
    groups = projector(flattened(checked_solid(shape), view), App.Vector(0, 0, 1))
    if len(groups) != 10:
        raise RuntimeError("Drawing.projectEx must return ten visibility buckets")
    edges = {}
    for index, group in enumerate(groups):
        if not hasattr(group, "Edges"):
            raise RuntimeError("HLR bucket is not a BRep shape")
        for edge in group.Edges:
            data = edge_data(edge, "visible" if index < 5 else "hidden")
            edges[data["edge_sha256"]] = data
    curves = [edges[key] for key in sorted(edges)]
    if not any(item["kind"] == "visible" for item in curves):
        raise RuntimeError("HLR returned no visible geometry")
    return {"direction": view, "source": "Drawing.projectEx",
            "hidden_measured": True, "curves": curves}


def section(shape, definition):
    import FreeCAD as App
    import Part

    axis = "xyz".index(definition["axis"])
    box = shape.BoundBox
    low = [box.XMin, box.YMin, box.ZMin]
    high = [box.XMax, box.YMax, box.ZMax]
    offset = definition["offset_nm"] / NM
    if not low[axis] < offset < high[axis]:
        raise ValueError("section plane must be strictly inside the model bounds")
    padding = max(high[i] - low[i] for i in range(3)) + 1
    origin = [(low[i] + high[i]) / 2 for i in range(3)]
    origin[axis] = offset
    normal = [0, 0, 0]
    normal[axis] = 1
    # The exact planar intersection measures material, including hole gaps.
    plane = Part.makePlane(4 * padding, 4 * padding, App.Vector(-2 * padding, -2 * padding, 0))
    plane.Placement = App.Placement(App.Vector(*origin),
                                    App.Rotation(App.Vector(0, 0, 1), App.Vector(*normal)))
    material = shape.common(plane)
    if not material.Faces or not material.isValid() or material.Area <= 0:
        raise ValueError("section has no valid material face")
    clip_low = [low[i] - padding for i in range(3)]
    clip_high = [high[i] + padding for i in range(3)]
    if definition["keep"] == "positive":
        clip_low[axis] = offset
    else:
        clip_high[axis] = offset
    clip = Part.makeBox(*(clip_high[i] - clip_low[i] for i in range(3)),
                        App.Vector(*clip_low))
    kept = checked_solid(shape.common(clip))
    view = {"x": "right", "y": "front", "z": "top"}[definition["axis"]]
    projected = hlr(kept, view)
    profile = flattened(material, view)
    projected.update(name=definition["name"], section=dict(definition),
                     material_area_nm2=int(round(material.Area * NM * NM)),
                     material_faces=len(material.Faces),
                     material_wires=sum(len(face.Wires) for face in material.Faces),
                     profile=[edge_data(edge, "section") for edge in profile.Edges],
                     kept_volume_nm3=int(round(kept.Volume * NM ** 3)))
    return projected


def projected_point(point, view):
    return [int(round(sum(point[i] * basis[i] for i in range(3))))
            for basis in BASES[view][:2]]


def measured_dimensions(shape, requests):
    import Part

    box = shape.BoundBox
    lows = vector_nm((box.XMin, box.YMin, box.ZMin))
    highs = vector_nm((box.XMax, box.YMax, box.ZMax))
    result = []
    for request in requests:
        view = request["view"]
        if request["kind"] == "extent":
            axis = "xyz".index(request["axis"])
            basis = [BASES[view][i][axis] for i in range(2)]
            if sum(item * item for item in basis) < 1 - 1e-8:
                raise ValueError("extent axis is foreshortened or absent in this view")
            vertices = [vector_nm(vertex.Point) for vertex in shape.Vertexes]
            starts = [point for point in vertices if abs(point[axis] - lows[axis]) <= 10]
            ends = [point for point in vertices if abs(point[axis] - highs[axis]) <= 10]
            pairs = [(first, second) for first in starts for second in ends
                     if all(abs(first[i] - second[i]) <= 10 for i in range(3) if i != axis)]
            if not pairs:
                raise ValueError("extent has no aligned real BRep vertex witnesses")
            start, end = min(pairs, key=lambda pair: (pair[0], pair[1]))
            value = highs[axis] - lows[axis]
            witnesses = [projected_point(start, view), projected_point(end, view)]
            measurement = "BRep.BoundBox+Vertices"
        elif request["kind"] == "diameter":
            selector = request["selector"]
            center = selector["center_nm"]
            radius = selector["radius_nm"]
            normal = selector["normal"]
            if abs(sum(normal[i] * BASES[view][2][i] for i in range(3))) < 1 - 1e-8:
                raise ValueError("diameter must be shown in an unforeshortened view")
            matches = []
            for edge in shape.Edges:
                curve = edge.Curve
                if not isinstance(curve, Part.Circle) or not edge.isClosed():
                    continue
                actual = vector_nm(curve.Center)
                delta = [actual[i] - center[i] for i in range(3)]
                axial = sum(delta[i] * normal[i] for i in range(3))
                radial_error = max(abs(delta[i] - axial * normal[i]) for i in range(3))
                alignment = abs(sum(curve.Axis[i] * normal[i] for i in range(3)))
                if (abs(curve.Radius * NM - radius) <= 10
                        and radial_error <= 10 and alignment > 1 - 1e-8):
                    matches.append(curve)
            if not matches:
                raise ValueError("circle reference does not match a real BRep circular edge")
            actual = matches[0]
            value = int(round(2 * actual.Radius * NM))
            flat = projected_point(vector_nm(actual.Center), view)
            witnesses = [[flat[0] - value // 2, flat[1]],
                         [flat[0] + value // 2, flat[1]]]
            measurement = "BRep.Circle"
        else:
            raise ValueError("unsupported dimension kind")
        if value <= 0 or witnesses[0] == witnesses[1]:
            raise ValueError("dimension has no positive measurable span")
        result.append({key: value_ for key, value_ in request.items() if key != "selector"})
        result[-1].update(value_nm=value, witnesses_nm=witnesses, measurement=measurement)
    return result


def execute(job):
    import FreeCAD as App
    import Part

    source = Path("model.step")
    if source.is_symlink() or not source.is_file():
        raise ValueError("job model.step must be a regular local file")
    if not 0 < source.stat().st_size <= MAX_STEP_BYTES:
        raise ValueError("job STEP size is outside worker limits")
    data = source.read_bytes()
    if digest(data) != job["step_sha256"]:
        raise ValueError("job STEP bytes changed")
    shape = Part.Shape()
    shape.read(str(source.resolve()))
    checked_solid(shape)
    if len(shape.Solids) != 1:
        raise ValueError("this drawing profile supports exactly one solid")
    box = shape.BoundBox
    views = []
    for view in job["views"]:
        entry = hlr(shape, view)
        entry["name"] = view
        views.append(entry)
    views.extend(section(shape, item) for item in job["sections"])
    return {"ok": True, "schema": "associative-worker/1",
            "step_sha256": digest(data), "worker_sha256": digest(Path(__file__).read_bytes()),
            "source_doc_sha256": job["source_doc_sha256"],
            "model_version": job["model_version"],
            "freecad_version": ".".join(App.Version()[:3]),
            "solid_count": len(shape.Solids),
            "volume_nm3": int(round(shape.Volume * NM ** 3)),
            "bbox_nm": vector_nm((box.XMin, box.YMin, box.ZMin,
                                  box.XMax, box.YMax, box.ZMax)),
            "views": views, "dimensions": measured_dimensions(shape, job["dimensions"])}


def main():
    result = Path.cwd() / "worker-result.json"
    try:
        job_path = Path(os.environ["OPENCAD_DRAWING_JOB"])
        if job_path != Path.cwd() / "job.json" or job_path.is_symlink():
            raise ValueError("worker job must be its own cwd/job.json")
        if Path(__file__).resolve().parent != Path.cwd() / "scripts":
            raise ValueError("worker script is not inside this job")
        if not 0 < job_path.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError("worker JSON size limit")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        payload = execute(job)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
    with result.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, allow_nan=False)
        handle.write("\n")
    marker = "DRAWING_WORKER verdict=" + ("PASS" if payload["ok"] else "FAIL")
    print(marker, flush=True)
    if not payload["ok"]:
        print(payload["traceback"], file=sys.stderr, flush=True)
        raise SystemExit(3)


if os.environ.get("OPENCAD_DRAWING_JOB"):
    main()
