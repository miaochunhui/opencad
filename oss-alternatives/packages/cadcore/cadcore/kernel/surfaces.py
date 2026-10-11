# SPDX-License-Identifier: Apache-2.0
"""Exact surface recipes and an isolated FreeCAD file-protocol client.

No FreeCAD module is imported into the caller. Rectangular parameter trimming
is supported, not arbitrary trimming wires. Boundary indices are recipe-local,
not persistent topological names. See docs/design/industrial-surfaces.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any

__all__ = [
    "SurfaceKernel", "SurfaceExecutionError", "make_nurbs", "make_trim",
    "make_sew", "make_offset", "make_thicken", "validate_recipe",
    "validate_seam",
]

MAX_NM = 2 ** 53
EDGES = ("u_min", "u_max", "v_min", "v_max")
DEFAULT_EVIDENCE = Path(
    "/hf3fs-jd/data/tmp/opencad-industrial-surfaces-20261011/seat-surfaces")


def _number(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("%s must be a finite number, never bool" % label)
    if positive and value <= 0:
        raise ValueError("%s must be positive" % label)
    return float(value)


def _nm(value, label, *, positive=False):
    if type(value) is not int or abs(value) > MAX_NM:
        raise ValueError("%s must be integer nanometres with abs <= 2**53" % label)
    if positive and value <= 0:
        raise ValueError("%s must be positive" % label)
    return value


def _fields(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError("%s must be an object" % label)
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise ValueError("%s unknown=%s missing=%s" %
                         (label, sorted(unknown, key=str), sorted(missing)))


def _detach(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("surface recipe requires finite acyclic JSON: %s" % exc) from exc


def _axis(knots, mults, degree, count, label):
    if type(degree) is not int or not 1 <= degree <= min(8, count - 1):
        raise ValueError("%s degree must be an integer from 1 to min(8, poles-1)" % label)
    if not isinstance(knots, list) or not 2 <= len(knots) <= 32:
        raise ValueError("%s knots require 2..32 entries" % label)
    values = [_number(x, label + " knot") for x in knots]
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError("%s knots must be strictly increasing" % label)
    if not isinstance(mults, list) or len(mults) != len(values):
        raise ValueError("%s multiplicities must match knots" % label)
    if any(type(x) is not int or not 1 <= x <= degree + 1 for x in mults):
        raise ValueError("%s multiplicities must be positive integers <= degree+1" % label)
    if mults[0] != degree + 1 or mults[-1] != degree + 1:
        raise ValueError("%s requires clamped nonperiodic end knots" % label)
    if any(x > degree for x in mults[1:-1]):
        raise ValueError("%s interior knot multiplicity exceeds degree" % label)
    if sum(mults) != count + degree + 1:
        raise ValueError("%s knot multiplicity sum does not match poles and degree" % label)


def validate_recipe(recipe):
    """Strict detached schema; actual topology is always checked by FreeCAD."""
    value = _detach(recipe)
    budget = [0]

    def walk(item, depth):
        budget[0] += 1
        if depth > 8 or budget[0] > 64:
            raise ValueError("surface recipe exceeds depth/node budget")
        if not isinstance(item, dict):
            raise ValueError("surface recipe must be an object")
        op = item.get("op")
        if op == "nurbs":
            names = ("op", "poles_nm", "u_degree", "v_degree", "u_knots",
                     "v_knots", "u_mults", "v_mults", "weights")
            _fields(item, names, names[:-1], "nurbs")
            poles = item["poles_nm"]
            if not isinstance(poles, list) or not 2 <= len(poles) <= 32:
                raise ValueError("nurbs poles require 2..32 U rows")
            if not isinstance(poles[0], list) or not 2 <= len(poles[0]) <= 32:
                raise ValueError("nurbs poles require 2..32 V columns")
            columns = len(poles[0])
            for row in poles:
                if not isinstance(row, list) or len(row) != columns:
                    raise ValueError("nurbs poles must be rectangular")
                for point in row:
                    if not isinstance(point, list) or len(point) != 3:
                        raise ValueError("nurbs pole must have three coordinates")
                    for coordinate in point:
                        _nm(coordinate, "nurbs pole")
            for axis, count in (("u", len(poles)), ("v", columns)):
                _axis(item[axis + "_knots"], item[axis + "_mults"],
                      item[axis + "_degree"], count, axis)
            weights = item.get("weights")
            if weights is not None:
                if not isinstance(weights, list) or len(weights) != len(poles):
                    raise ValueError("nurbs weights must match poles")
                for row in weights:
                    if not isinstance(row, list) or len(row) != columns:
                        raise ValueError("nurbs weights must match poles")
                    for weight in row:
                        if not 1e-12 <= _number(weight, "weight", positive=True) <= 1e12:
                            raise ValueError("nurbs weight outside supported range")
        elif op == "trim":
            _fields(item, ("op", "surface", "bounds"), ("op", "surface", "bounds"), op)
            walk(item["surface"], depth + 1)
            if item["surface"]["op"] not in ("nurbs", "trim"):
                raise ValueError("trim requires a single parameterized NURBS face")
            bounds = item["bounds"]
            if not isinstance(bounds, list) or len(bounds) != 4:
                raise ValueError("trim bounds require [u_min,u_max,v_min,v_max]")
            a, b, c, d = [_number(x, "trim bound") for x in bounds]
            if a >= b or c >= d:
                raise ValueError("trim bounds must be increasing")
        elif op == "sew":
            _fields(item, ("op", "surfaces", "solid", "seams"),
                    ("op", "surfaces", "solid", "seams"), op)
            if type(item["solid"]) is not bool:
                raise ValueError("sew solid must be bool")
            faces = item["surfaces"]
            if not isinstance(faces, list) or not 2 <= len(faces) <= 32:
                raise ValueError("sew requires 2..32 surfaces")
            for face in faces:
                walk(face, depth + 1)
                if face["op"] not in ("nurbs", "trim", "offset"):
                    raise ValueError("sew inputs must be individual surface faces")
            if not isinstance(item["seams"], list) or len(item["seams"]) > 64:
                raise ValueError("sew seams must be a bounded list")
            for seam in item["seams"]:
                validate_seam(seam, indexed_count=len(faces))
        elif op in ("offset", "thicken"):
            length = "offset_nm" if op == "offset" else "thickness_nm"
            names = ("op", "surface", length) + (("inward",) if op == "thicken" else ())
            _fields(item, names, names, op)
            walk(item["surface"], depth + 1)
            if item["surface"].get("solid") or item["surface"]["op"] == "thicken":
                raise ValueError("%s requires an open face or shell, not a solid" % op)
            distance = _nm(item[length], length, positive=(op == "thicken"))
            if distance == 0:
                raise ValueError("offset_nm must be nonzero")
            if op == "thicken" and type(item["inward"]) is not bool:
                raise ValueError("thicken inward must be bool")
        else:
            raise ValueError("unsupported surface operation: %s" % op)
    walk(value, 0)
    return value


def make_nurbs(*, poles_nm, u_degree, v_degree, u_knots, v_knots,
               u_mults, v_mults, weights=None):
    return validate_recipe(dict(op="nurbs", poles_nm=poles_nm,
                                u_degree=u_degree, v_degree=v_degree,
                                u_knots=u_knots, v_knots=v_knots,
                                u_mults=u_mults, v_mults=v_mults, weights=weights))


def make_trim(surface, *, u_min, u_max, v_min, v_max):
    return validate_recipe(dict(op="trim", surface=surface,
                                bounds=[u_min, u_max, v_min, v_max]))


def make_sew(*, surfaces, solid=False, seams=None):
    return validate_recipe(dict(op="sew", surfaces=surfaces, solid=solid,
                                seams=[] if seams is None else seams))


def make_offset(surface, *, offset_nm):
    return validate_recipe(dict(op="offset", surface=surface, offset_nm=offset_nm))


def make_thicken(surface, *, thickness_nm, inward=False):
    return validate_recipe(dict(op="thicken", surface=surface,
                                thickness_nm=thickness_nm, inward=inward))


def validate_seam(seam, *, indexed_count=None):
    names = ("left_edge", "right_edge", "reverse", "continuity", "samples",
             "position_tolerance_nm", "angle_tolerance_rad", "curvature_tolerance_per_mm")
    if indexed_count is not None:
        names += ("left", "right")
    _fields(seam, names, names, "seam")
    if seam["left_edge"] not in EDGES or seam["right_edge"] not in EDGES:
        raise ValueError("seam edge must be u_min/u_max/v_min/v_max")
    if type(seam["reverse"]) is not bool:
        raise ValueError("seam reverse must be bool")
    if seam["continuity"] not in ("G0", "G1", "G2"):
        raise ValueError("seam continuity must be G0/G1/G2")
    if type(seam["samples"]) is not int or not 3 <= seam["samples"] <= 65:
        raise ValueError("seam samples require integer 3..65")
    _nm(seam["position_tolerance_nm"], "seam position tolerance", positive=True)
    angle = _number(seam["angle_tolerance_rad"], "seam angle tolerance", positive=True)
    if angle >= math.pi / 2:
        raise ValueError("seam angle tolerance must be below pi/2")
    _number(seam["curvature_tolerance_per_mm"], "seam curvature tolerance", positive=True)
    if indexed_count is not None:
        for label in ("left", "right"):
            index = seam[label]
            if type(index) is not int or not 1 <= index <= indexed_count:
                raise ValueError("seam %s must reference a one-based input surface" % label)
        if seam["left"] == seam["right"]:
            raise ValueError("seam cannot compare a surface with itself")
    return _detach(seam)


class SurfaceExecutionError(RuntimeError):
    def __init__(self, message, *, reason, details):
        super().__init__(message)
        self.reason = reason
        self.details = details


class SurfaceKernel:
    """One job per isolated FreeCAD process; no retry, no nano/mock fallback."""

    def __init__(self, *, executable=None, evidence_dir=DEFAULT_EVIDENCE, timeout_s=60):
        self.executable = executable
        self.evidence_dir = Path(evidence_dir)
        self.timeout_s = _number(timeout_s, "timeout_s", positive=True)
        if self.timeout_s > 3600:
            raise ValueError("timeout_s exceeds 3600 seconds")

    def _job(self, request):
        from .subproc import _run, find_executable

        exe = self.executable or find_executable()
        if not exe:
            raise SurfaceExecutionError("real FreeCAD executable not found",
                                        reason="dependency_missing", details={})
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        job_dir = Path(tempfile.mkdtemp(prefix="surface-", dir=self.evidence_dir))
        scripts = job_dir / "scripts"
        scripts.mkdir()
        sources = [Path(__file__).resolve(), Path(__file__).with_name("surfaces_worker.py").resolve()]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        for source in sources:
            (scripts / source.name).write_bytes(source.read_bytes())
        (job_dir / "request.json").write_text(json.dumps(request, allow_nan=False, sort_keys=True),
                                              encoding="utf-8")
        env = dict(os.environ)
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1",
                   CADCORE_SURFACE_REQUEST=str(job_dir / "request.json"),
                   CADCORE_SURFACE_RESULT=str(job_dir / "result.json"))
        started = time.monotonic()
        outcome = _run([str(exe), str(scripts / "surfaces_worker.py")],
                       cwd=str(job_dir), env=env, timeout_s=self.timeout_s)
        (job_dir / "stdout.log").write_text(outcome.stdout, encoding="utf-8")
        (job_dir / "stderr.log").write_text(outcome.stderr, encoding="utf-8")
        after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        observation = dict(rc=outcome.rc, rc_measured=outcome.rc_measured,
                           elapsed_s=time.monotonic() - started, executable=str(exe),
                           sources_before=before, sources_after=after, job_dir=str(job_dir))
        (job_dir / "execution.json").write_text(json.dumps(observation, sort_keys=True, indent=2),
                                                encoding="utf-8")
        try:
            result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result = None
            observation["result_error"] = "%s: %s" % (type(exc).__name__, exc)
        if outcome.timed_out:
            reason = "timeout"
        elif outcome.launch_error:
            reason = "launch_failed"
            observation["launch_error"] = outcome.launch_error
        elif not isinstance(result, dict) or result.get("marker") != "cadcore-surfaces-v1":
            reason = "result_missing_or_invalid"
        elif result.get("ok") is not True:
            reason = "op_failed"
        elif outcome.rc != 0:
            reason = "nonzero_exit"
        elif before != after:
            reason = "source_drift"
        else:
            result["execution"] = observation
            for artifact in result.get("artifacts", {}).values():
                path = Path(artifact["path"])
                if path.parent.resolve() != job_dir.resolve() or not path.is_file():
                    raise SurfaceExecutionError("surface artifact missing or foreign",
                                                reason="artifact_missing", details=observation)
                if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise SurfaceExecutionError("surface artifact digest mismatch",
                                                reason="artifact_drift", details=observation)
            return result
        observation["worker_result"] = result
        error = result.get("error", "") if isinstance(result, dict) else ""
        raise SurfaceExecutionError(error or outcome.stderr or reason,
                                    reason=reason, details=observation)

    def probe(self):
        return self._job({"op": "probe"})

    def build(self, surface, *, deflection_mm=0.05):
        deflection = _number(deflection_mm, "deflection_mm", positive=True)
        return self._job(dict(op="build", surface=validate_recipe(surface),
                              deflection_mm=deflection))

    def evaluate(self, surface, *, uv):
        if not isinstance(uv, list) or not 1 <= len(uv) <= 256:
            raise ValueError("evaluation uv requires 1..256 parameter pairs")
        for pair in uv:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("evaluation uv requires parameter pairs")
            for value in pair:
                _number(value, "evaluation parameter")
        return self._job(dict(op="evaluate", surface=validate_recipe(surface), uv=_detach(uv)))

    def check_seam(self, left, right, *, left_edge="u_max", right_edge="u_min",
                   reverse=False, continuity="G2", samples=17,
                   position_tolerance_nm=100, angle_tolerance_rad=1e-6,
                   curvature_tolerance_per_mm=1e-6):
        seam = validate_seam(dict(
            left_edge=left_edge, right_edge=right_edge, reverse=reverse,
            continuity=continuity, samples=samples,
            position_tolerance_nm=position_tolerance_nm,
            angle_tolerance_rad=angle_tolerance_rad,
            curvature_tolerance_per_mm=curvature_tolerance_per_mm))
        return self._job(dict(op="seam", left=validate_recipe(left),
                              right=validate_recipe(right), seam=seam))
