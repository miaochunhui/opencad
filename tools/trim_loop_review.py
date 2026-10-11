#!/usr/bin/env python3
"""Independent exact trim-loop review and installed FreeCAD API probe.

Only fresh external evidence and local runtime are written. The probe reads
the installed backend documentation and performs a real UV-wire construction.
No source overlay, dependency installation, production service or retry.

Generic Web recipe: {op:"trim", surface:NURBS_OR_RECTANGULAR_TRIM,
outer:[[u,v],...,first], holes:[[[u,v],...,first],...]}. Explicit closure,
CCW outer, CW holes; bounds is mutually exclusive. Up to 128 vertices per
ring, 16 holes and 512 points including closures. Straight UV segments map
to exact support-surface pcurves, not straight world chords, projected shells,
rectangles or mesh booleans. Nested polygon trims fail visibly.

This review measures concave double-hole area, bidirectional thickening volume,
sewing, rational cylinder support points and actual reloaded BRep/STEP hole
locations. It does not prove all-domain G2, surface injectivity, all offsets,
guide lofts, periodic surfaces, general curved UV edges or industrial parity.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "oss-alternatives/packages/cadcore"
sys.path.insert(0, str(PACKAGE))

PROBE = '''import FreeCAD as App
from FreeCAD import Base
import Part
import json
import os
import traceback
from pathlib import Path
result = {"ok": False}
try:
    result["docs"] = {
        "Face": Part.Face.__doc__,
        "Line2dSegment": Part.Geom2d.Line2dSegment.__doc__,
        "line_toShape": Part.Geom2d.Line2dSegment.toShape.__doc__,
        "Wire": Part.Wire.__doc__,
        "Face_isPartOfDomain": Part.Face.isPartOfDomain.__doc__,
    }
    surface = Part.BSplineSurface()
    surface.buildFromPolesMultsKnots(
        [[App.Vector(i * 10, j * 10, 0) for j in range(2)] for i in range(2)],
        [2,2], [2,2], [0,1], [0,1], False, False, 1, 1)
    polygons = [
        [[0,0],[1,0],[1,1],[0,1],[0,0]],
        [[.2,.2],[.2,.4],[.4,.4],[.4,.2],[.2,.2]],
    ]
    wires = []
    for points in polygons:
        edges = [Part.Geom2d.Line2dSegment(Base.Vector2d(*a), Base.Vector2d(*b)).toShape(surface)
                 for a,b in zip(points,points[1:])]
        wires.append(Part.Wire(edges))
    face = Part.Face(surface, wires[0])
    for hole in wires[1:]:
        face = Part.Face(face, hole)
    result["face"] = {"area": face.Area, "wires": len(face.Wires),
                      "valid": face.isValid(), "surface": type(face.Surface).__name__}
    result["domain"] = [face.isPartOfDomain(.3,.3), face.isPartOfDomain(.8,.8)]
    assert face.isValid() and len(face.Wires) == 2 and abs(face.Area - 96) < 1e-8
    assert result["domain"] == [False, True]
    result["ok"] = True
except Exception:
    result["traceback"] = traceback.format_exc()
    print(result["traceback"])
Path(os.environ["TRIM_PROBE_RESULT"]).write_text(json.dumps(result, indent=2))
print("TRIM_API_PROBE", "PASS" if result["ok"] else "FAIL")
if __name__ == "__main__":
    raise SystemExit(0 if result["ok"] else 3)
'''

READBACK = '''import FreeCAD as App
import Part
import hashlib
import json
import os
import traceback
from pathlib import Path
result = {"ok": False, "checks": [], "shapes": []}
def check(label, condition):
    result["checks"].append({"label": label, "passed": bool(condition)})
    if not condition:
        raise AssertionError(label)
try:
    result["backend"] = {"freecad_components": list(App.Version()), "occt": Part.OCC_VERSION}
    fixtures = json.loads(Path(os.environ["TRIM_REVIEW_INPUT"]).read_text())
    for fixture in fixtures:
        for fmt, artifact in fixture["result"]["artifacts"].items():
            path = Path(artifact["path"])
            check(fixture["name"] + "/" + fmt + "/artifact-hash",
                  hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"])
            shape = Part.Shape()
            if fmt == "brep":
                shape.importBrep(artifact["path"])
            else:
                shape = Part.read(artifact["path"])
            label = fixture["name"] + "/" + fmt
            check(label + "/valid", shape.isValid() and bool(shape.Faces))
            holes = sorted(len(face.Wires) - 1 for face in shape.Faces)
            check(label + "/per-face-holes", holes == fixture["hole_signature"])
            check(label + "/closed-wires", all(w.isClosed() for f in shape.Faces for w in f.Wires))
            expected = fixture.get("volume_mm3")
            if expected is None:
                check(label + "/open-surface", len(shape.Solids) == 0)
                if "area_mm2" in fixture:
                    check(label + "/analytic-area", abs(shape.Area - fixture["area_mm2"]) < 1e-6)
            else:
                check(label + "/closed-solid", len(shape.Solids) == 1 and shape.isClosed())
                check(label + "/analytic-volume", abs(shape.Volume - expected) < 1e-6)
            witnesses = []
            for witness in fixture["witnesses"]:
                point = App.Vector(*witness["point_mm"])
                if expected is None:
                    distance = shape.distToShape(Part.Vertex(point))[0]
                    actual = distance < 1e-7
                    witnesses.append({"point_mm": witness["point_mm"], "distance_mm": distance,
                                      "on_face": actual, "expected": witness["inside"]})
                else:
                    actual = shape.isInside(point, 1e-7, False)
                    witnesses.append({"point_mm": witness["point_mm"], "inside": actual,
                                      "expected": witness["inside"]})
                check(label + "/witness/" + str(witness["point_mm"]), actual == witness["inside"])
            result["shapes"].append({"label": label, "area_mm2": shape.Area,
                                      "volume_mm3": shape.Volume if shape.Solids else None,
                                      "hole_signature": holes, "witnesses": witnesses})
    # A bounding rectangle would fill both holes and the concave notch.
    box_face = Part.makePlane(8, 8, App.Vector(1, 1, 0))
    rejected = box_face.distToShape(Part.Vertex(App.Vector(2.5, 2.5, 0)))[0] < 1e-7
    check("negative-control/rectangle-fills-hole-detected", rejected)
    result["negative_control"] = "rectangle substitution rejected by hole witness"
    result["ok"] = True
except Exception:
    result["traceback"] = traceback.format_exc()
    print(result["traceback"])
Path(os.environ["TRIM_PROBE_RESULT"]).write_text(json.dumps(result, indent=2))
print("TRIM_EXACT_READBACK", "PASS" if result["ok"] else "FAIL")
if __name__ == "__main__":
    raise SystemExit(0 if result["ok"] else 3)
'''


def fixtures(kernel):
    from cadcore.kernel.surfaces import make_nurbs, make_trim, make_thicken, make_sew
    import math
    mm = 1_000_000
    def plane(x=0):
        return make_nurbs(poles_nm=[[[int((x+i*10)*mm), j*10*mm, 0] for j in range(2)]
                                   for i in range(2)], u_degree=1, v_degree=1,
                          u_knots=[0, 1], v_knots=[0, 1], u_mults=[2, 2], v_mults=[2, 2])
    outer = [[.1, .1], [.9, .1], [.9, .5], [.6, .5], [.6, .9], [.1, .9], [.1, .1]]
    holes = [[[.2, .2], [.2, .3], [.3, .3], [.3, .2], [.2, .2]],
             [[.4, .2], [.4, .4], [.5, .2], [.4, .2]]]
    plate = make_trim(plane(), outer=outer, holes=holes)
    points = [([1.5, 5, 0], True), ([2.5, 2.5, 0], False),
              ([4.3, 2.5, 0], False), ([7, 7, 0], False)]
    data = []
    def add(name, recipe, signature, witnesses, **expected):
        data.append(dict(name=name, recipe=recipe, result=kernel.build(recipe),
                         hole_signature=signature,
                         witnesses=[dict(point_mm=p, inside=inside) for p, inside in witnesses],
                         **expected))
    add("concave-two-holes", plate, [2], points, area_mm2=50)
    for inward in (False, True):
        z = -1 if inward else 1
        thick = make_thicken(plate, thickness_nm=2*mm, inward=inward)
        measured = kernel.build(thick)
        # Six outer sides plus seven hole sides and two end faces.
        data.append(dict(name="thicken-inward-%s" % inward, recipe=thick, result=measured,
                         hole_signature=[0]*13 + [2, 2], volume_mm3=100,
                         witnesses=[dict(point_mm=[p[0], p[1], z], inside=inside)
                                    for p, inside in points]))
    triangle = make_trim(plane(), outer=[[.1, .1], [.9, .1], [.5, .9], [.1, .1]])
    add("diagonal-triangle", triangle, [0], [([5, 3, 0], True), ([1.5, 8, 0], False)],
        area_mm2=32)
    cyl = make_nurbs(poles_nm=[[[x*mm, y*mm, z*mm] for z in (0, 10)]
                               for x, y in ((5, 0), (5, 5), (0, 5))],
                     u_degree=2, v_degree=1, u_knots=[0, 1], v_knots=[0, 1],
                     u_mults=[3, 3], v_mults=[2, 2], weights=[[1, 1], [math.sqrt(.5)]*2, [1, 1]])
    def cylinder_point(u, v):
        w = math.sqrt(.5)
        denominator = (1-u)**2 + 2*w*u*(1-u) + u*u
        return [5*((1-u)**2 + 2*w*u*(1-u))/denominator,
                5*(2*w*u*(1-u) + u*u)/denominator, 10*v]
    add("rational-cylinder", make_trim(cyl, outer=outer, holes=holes), [2],
        [(cylinder_point(u, v), inside) for u, v, inside in
         ((.15, .5, True), (.25, .25, False), (.43, .25, False), (.7, .7, False))])
    left = make_trim(plane(), outer=[[.1, .1], [1, .1], [1, .5], [.6, .5],
                                    [.6, .9], [.1, .9], [.1, .1]], holes=holes)
    right = make_trim(plane(10), outer=[[0, .1], [.8, .1], [.8, .5], [0, .5], [0, .1]])
    add("sewn-two-faces", make_sew(surfaces=[left, right]), [0, 2],
        points + [([12, 3, 0], True)], area_mm2=86)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-probe", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    parent = args.out.resolve()
    runtime = args.runtime_root.resolve()
    if parent.is_relative_to(ROOT) or runtime.is_relative_to(ROOT) or runtime.is_relative_to(parent):
        parser.error("evidence/runtime must be external and runtime separate from evidence")
    stamp = datetime.now(timezone.utc)
    out = parent / stamp.strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    runtime.mkdir(parents=True, exist_ok=False)
    from cadcore.kernel.subproc import _run, find_executable
    from cadcore.kernel.surfaces import SurfaceKernel
    from acceptance import source_manifest
    import bootstrap_from_infra as bootstrap
    before = source_manifest(ROOT)
    exe = find_executable()
    if not exe:
        raise RuntimeError("real FreeCAD executable missing")
    script = out / "trim_probe.py"
    start = time.monotonic()
    report = {"verdict": "FAIL", "started_at": stamp.isoformat(),
              "source_root": str(ROOT), "head": subprocess.check_output(
                  ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
              "source_sha256": bootstrap.digest(before), "sources_before": before,
              "runtime_root": str(runtime), "evidence_dir": str(out)}
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(runtime),
               QT_QPA_PLATFORM="offscreen", TRIM_PROBE_RESULT=str(out / "probe.json"))
    try:
        if not args.api_probe:
            data = fixtures(SurfaceKernel(executable=exe, evidence_dir=out / "jobs"))
            report["fixtures"] = data
            path = out / "fixtures.json"
            path.write_text(json.dumps(data, indent=2))
            env["TRIM_REVIEW_INPUT"] = str(path)
        script.write_text(PROBE if args.api_probe else READBACK, encoding="utf-8")
        outcome = _run([exe, str(script)], cwd=str(runtime), env=env, timeout_s=60)
        (out / "stdout.log").write_text(outcome.stdout)
        (out / "stderr.log").write_text(outcome.stderr)
        report["rc"] = outcome.rc
        result = json.loads((out / "probe.json").read_text())
        report["readback"] = result
        report["checks"] = len(result.get("checks", []))
        report["verdict"] = "PASS" if outcome.rc == 0 and result["ok"] else "FAIL"
    except Exception as exc:
        import traceback
        report["error"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc(),
                           "worker_details": getattr(exc, "details", None)}
        if getattr(exc, "details", None):
            report["rc"] = exc.details.get("rc")
    after = source_manifest(ROOT)
    report.update(finished_at=datetime.now(timezone.utc).isoformat(),
                  elapsed_s=time.monotonic() - start, sources_after=after,
                  source_drift=before != after, source_after_sha256=bootstrap.digest(after),
                  tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if before != after:
        report["verdict"] = "FAIL"
    (out / "result.json").write_text(json.dumps(report, indent=2))
    print("TRIM_REVIEW verdict=%s rc=%s checks=%s drift=%s evidence=%s" %
          (report["verdict"], report.get("rc"), report.get("checks"), report["source_drift"], out))
    if report["verdict"] != "PASS":
        print(json.dumps(report.get("error", report.get("readback")), indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
