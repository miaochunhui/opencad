#!/usr/bin/env python3
"""Frozen-source private HTTP exact UV polygon acceptance, no product patches.

Preflight binds all Git-visible source bytes, including untracked new files.
Execution requires that digest, uses fresh external evidence/local runtime,
launches the selected tree's original secure_web CLI twice, and preserves FAIL.
Only generated test credentials are used; login bodies/cookies are not logged.
No existing data, evidence, permissions, dependencies or service is modified.
See docs/design/trim-loop-private-review.md for the acceptance boundaries.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP_REL = "oss-alternatives/apps/opencad"
CORE_REL = "oss-alternatives/packages/cadcore"
TABLES = ("create_receipts", "projects", "receipts", "snapshots")
REQUIRED = (APP_REL + "/secure_web.py", APP_REL + "/project_gateway.py",
            APP_REL + "/project_store.py", APP_REL + "/access_control.py",
            APP_REL + "/industrial_surfaces.py", CORE_REL + "/cadcore/kernel/surfaces.py",
            CORE_REL + "/cadcore/kernel/surfaces_worker.py")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def reject_constant(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def read_json(path):
    return json.loads(path.read_bytes(), parse_constant=reject_constant)


def finite_positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)


def valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")


def source_binding(source):
    source = source.resolve(strict=True)
    names = subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    manifest = {}
    for raw in sorted(set(names.split(b"\0")) - {b""}):
        name = os.fsdecode(raw)
        path = source / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != source):
            raise ValueError("Linked source: " + name)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Nonregular/missing source: " + name)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                content = handle.read()
            manifest[name] = {"sha256": sha(content), "bytes": len(content),
                              "executable": bool(info.st_mode & 0o111)}
        finally:
            os.close(fd)
    if not manifest or any(name not in manifest for name in REQUIRED):
        raise ValueError("Incomplete selected source")
    return {"root": str(source), "sha256": sha(canonical(manifest)), "manifest": manifest,
            "head": subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
            "status": subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain"], text=True)}


def frozen(binding, expected):
    if not valid_sha(expected) or binding["sha256"] != expected:
        raise ValueError("Frozen source SHA absent or mismatched")


def external(path, source, *, local=False):
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("External path contains parent traversal")
    known_alias = Path("/hf3fs-jd")
    if known_alias.is_symlink() and known_alias.resolve() != Path("/3fs/research3fs"):
        raise ValueError("Authorized 3FS alias changed")
    if any(part.is_symlink() for part in (path, *path.parents) if part != known_alias):
        raise ValueError("External path traverses a symlink")
    if path.is_relative_to(source) or path.is_relative_to(ROOT):
        raise ValueError("Evidence/runtime must be outside selected and executor source")
    base = Path("/data/tmp") if local else Path("/hf3fs-jd/data/tmp")
    if not path.is_relative_to(base) or path == base:
        raise ValueError("Use fresh /data/tmp runtime or /hf3fs-jd/data/tmp evidence")
    return path


def product_env(source, runtime):
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(runtime),
                QT_QPA_PLATFORM="offscreen", OPENCAD_CADCORE_ROOT=str(source / CORE_REL),
                PYTHONPATH=os.pathsep.join(str(source / part) for part in (
                    APP_REL, CORE_REL, "oss-alternatives/packages/dxf",
                    "oss-alternatives/packages/vault")))


def import_product(source):
    paths = [source / name for name in (APP_REL, CORE_REL, "oss-alternatives/packages/dxf",
                                       "oss-alternatives/packages/vault")]
    sys.path[:0] = [str(path) for path in paths]
    import access_control
    import project_store
    from cadcore import docmodel
    from cadcore.kernel import surfaces, subproc
    modules = (access_control, project_store, docmodel, surfaces, subproc)
    for module in modules:
        if not Path(module.__file__).resolve().is_relative_to(source):
            raise ValueError("Foreign product import: " + module.__name__)
    return modules


def runtime_origins(source):
    prefixes = {"cadcore": source / CORE_REL, "dxf": source / "oss-alternatives/packages/dxf",
                "freechat_vault": source / "oss-alternatives/packages/vault"}
    app_names = {path.stem for path in (source / APP_REL).glob("*.py")}
    origins = {}
    for name, module in list(sys.modules.items()):
        base = prefixes.get(name.split(".")[0])
        if base is None and name not in app_names:
            continue
        base = base or source / APP_REL
        filename = getattr(module, "__file__", None)
        paths = [Path(filename).resolve()] if filename else [
            Path(path).resolve() for path in getattr(module, "__path__", [])]
        if not paths or any(not path.is_relative_to(base) for path in paths):
            raise ValueError("Foreign/unbound runtime namespace: " + name)
        origins[name] = [str(path) for path in paths]
    if "cadcore.kernel.surfaces" not in origins or "access_control" not in origins:
        raise ValueError("Runtime binding is incomplete")
    return origins


class Review:
    def __init__(self, out, report):
        self.out, self.report = out, report
        self.private_values = []

    def redact(self, value):
        text = str(value)
        for secret in self.private_values:
            text = text.replace(secret, "[REDACTED]")
        return text

    def check(self, label, condition, **facts):
        entry = {"name": label, "passed": bool(condition), "at": stamp(), **facts}
        self.report["checks"].append(entry)
        with (self.out / "checks.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(self.redact(canonical(entry)) + "\n")
        print("TRIM_PRIVATE_CHECK verdict=%s name=%s" % ("PASS" if condition else "FAIL", label),
              flush=True)
        if not condition:
            raise AssertionError(label + ": " + self.redact(canonical(facts)))


@contextmanager
def secure_cli(source, runtime, review, label):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = "http://127.0.0.1:%s" % port
    argv = [sys.executable, "-B", str(source / APP_REL / "secure_web.py"),
            "--host", "127.0.0.1", "--port", str(port), "--data-root", str(runtime),
            "--public-origin", origin]
    log_path = review.out / (label + "-secure-cli.log")
    entry = {"label": label, "argv": argv, "log": str(log_path), "started_at": stamp()}
    review.report["cli_processes"].append(entry)
    active_failure = None
    with log_path.open("xb") as log:
        process = subprocess.Popen(argv, cwd=source, env=product_env(source, runtime),
                                   stdout=log, stderr=subprocess.STDOUT)
        entry["pid"] = process.pid
        try:
            deadline = time.monotonic() + 30
            marker = "OPENCAD_PRIVATE_WEB verdict=READY port=%s" % port
            while marker not in log_path.read_text(errors="replace"):
                if process.poll() is not None:
                    raise RuntimeError("Original CLI exited rc=%s: %s" %
                                       (process.returncode, log_path))
                if time.monotonic() > deadline:
                    raise RuntimeError("Original CLI readiness absent: " + str(log_path))
                time.sleep(.05)
            entry["ready_at"] = stamp()
            review.check(label + "/original-cli-ready", True, origin=origin)
            yield port, origin
        except BaseException as exc:
            active_failure = exc
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                entry["rc"] = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                entry["rc"] = process.wait(timeout=5)
                entry["shutdown_error"] = "Owned fixture required kill after SIGTERM"
            entry.update(finished_at=stamp(), log_sha256=sha(log_path.read_bytes()))
            if entry["rc"] != 0 or entry.get("shutdown_error"):
                failure = "Owned CLI shutdown rc=%s log=%s" % (entry["rc"], log_path)
                if active_failure is not None:
                    review.report.setdefault("secondary_failures", []).append(failure)
                else:
                    raise RuntimeError(failure)


class HTTP:
    def __init__(self, port, origin, review, cookies):
        self.port, self.origin, self.review, self.cookies = port, origin, review, cookies

    def call(self, label, route, body=None, *, user="owner", project=None, overrides=None):
        method = "POST" if body is not None else "GET"
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if user in self.cookies:
            headers["Cookie"] = self.cookies[user]
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        for key, value in (overrides or {}).items():
            if value is None:
                headers.pop(key, None)
            else:
                headers[key] = value
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        entry = {"name": label, "at": stamp(), "route": route, "method": method,
                 "user": user, "project_id": project}
        self.review.report["http_requests"].append(entry)
        try:
            connection.request(method, route, canonical(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read(32 * 1024 * 1024 + 1)
            response_headers = dict(response.getheaders())
            entry.update(status=response.status, bytes=len(raw))
            if route != "/api/auth/login":
                entry.update(response_sha256=sha(raw), body=body, headers={
                    key: value for key, value in response_headers.items()
                    if key.lower() != "set-cookie"})
                (self.review.out / ("http-%03d.json" % len(self.review.report["http_requests"]))).write_bytes(raw)
            self.review.check(label + "/response-bounded", len(raw) <= 32 * 1024 * 1024)
            content_type = response.getheader("Content-Type", "").split(";")[0]
            data = json.loads(raw, parse_constant=reject_constant) if content_type == "application/json" else raw
            return response.status, response_headers, data, raw
        finally:
            connection.close()

    def json(self, label, route, body=None, *, status=200, **kwargs):
        code, headers, data, raw = self.call(label, route, body, **kwargs)
        self.review.check(label + "/http-contract",
                          code == status and isinstance(data, dict) and data.get("ok") is True,
                          expected_status=status, actual_status=code,
                          error=data.get("error") if isinstance(data, dict) else "not-json")
        return data, headers, raw


def edit(head, **fields):
    return {"expected_revision": head["revision"], "expected_sha": head["sha256"],
            "request_id": secrets.token_hex(16), **fields}


def db_anchor(store):
    with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if tuple(names) != TABLES:
            raise AssertionError("Unexpected/empty project DB schema: " + repr(names))
        rows = {name: sorted(connection.execute('SELECT * FROM "%s"' % name).fetchall(), key=repr)
                for name in names}
    return {"sha256": sha(canonical(rows)), "counts": {key: len(value) for key, value in rows.items()}}


def plane_recipe():
    return {"op": "nurbs", "poles_nm": [[[i * 10_000_000, j * 10_000_000, 0] for j in range(2)]
                                      for i in range(2)],
            "u_degree": 1, "v_degree": 1, "u_knots": [0, 1], "v_knots": [0, 1],
            "u_mults": [2, 2], "v_mults": [2, 2], "weights": None}


def polygon_recipe():
    return {"op": "trim", "surface": plane_recipe(),
            "outer": [[.1, .1], [.9, .1], [.9, .5], [.6, .5], [.6, .9], [.1, .9], [.1, .1]],
            "holes": [[[.2, .2], [.2, .3], [.3, .3], [.3, .2], [.2, .2]],
                      [[.4, .2], [.4, .4], [.5, .2], [.4, .2]]]}


def analytic_area(recipe):
    def area(ring):
        return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2
    return 100 * (area(recipe["outer"]) + sum(area(hole) for hole in recipe["holes"]))


def mesh_measure(mesh):
    points, triangles = mesh["vertices_mm"], mesh["triangles"]
    if not points or not triangles:
        raise ValueError("Empty mesh")
    if any(len(p) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in p)
           for p in points):
        raise ValueError("Nonfinite/invalid mesh vertex")
    volume, area, edges = 0.0, 0.0, {}
    for tri in triangles:
        if (len(tri) != 3 or len(set(tri)) != 3
                or any(type(i) is not int or not 0 <= i < len(points) for i in tri)):
            raise ValueError("Invalid mesh triangle")
        a, b, c = [points[i] for i in tri]
        ab, ac = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        cross = [ab[1]*ac[2] - ab[2]*ac[1], ab[2]*ac[0] - ab[0]*ac[2],
                 ab[0]*ac[1] - ab[1]*ac[0]]
        triangle_area = math.sqrt(sum(x*x for x in cross)) / 2
        if triangle_area <= 0:
            raise ValueError("Degenerate mesh triangle")
        area += triangle_area
        volume += (a[0]*(b[1]*c[2] - b[2]*c[1]) + a[1]*(b[2]*c[0] - b[0]*c[2])
                   + a[2]*(b[0]*c[1] - b[1]*c[0])) / 6
        for i, j in ((0, 1), (1, 2), (2, 0)):
            key = tuple(sorted(tuple(round(x, 8) for x in points[tri[k]]) for k in (i, j)))
            edges[key] = edges.get(key, 0) + 1
    return {"area_mm2": area, "volume_mm3": abs(volume), "vertices": len(points),
            "triangles": len(triangles), "closed_manifold": all(count == 2 for count in edges.values())}


def measured_contract(result, signature, *, area=None, volume=None):
    if area is not None and not finite_positive(area):
        raise AssertionError("Nonfinite/nonpositive expected area")
    if volume is not None and not finite_positive(volume):
        raise AssertionError("Nonfinite/nonpositive expected volume")
    for item in [result["properties"], result["roundtrip"]["brep"], result["roundtrip"]["step"]]:
        if not finite_positive(item["area_mm2"]):
            raise AssertionError("Nonfinite/nonpositive BRep/STEP area")
        if (item["valid"] is not True or sorted(f["inner_wire_count"] for f in item["faces"]) != signature
                or not all(f["wires_closed"] for f in item["faces"])
                or item["face_count"] != len(signature)):
            raise AssertionError("BRep/STEP hole topology invalid or changed")
        if volume is None:
            if item["solid_count"] != 0 or item["volume_mm3"] is not None:
                raise AssertionError("Open surface falsely reported as solid")
        elif (item["solid_count"] != 1 or item["closed"] is not True
              or not finite_positive(item["volume_mm3"])
              or abs(item["volume_mm3"] - volume) > 1e-6):
            raise AssertionError("Closed BRep/STEP analytic volume mismatch")
        if area is not None and abs(item["area_mm2"] - area) > 1e-6:
            raise AssertionError("BRep/STEP analytic area mismatch")
    mesh = mesh_measure(result["mesh"])
    if volume is not None and (not mesh["closed_manifold"] or abs(mesh["volume_mm3"] - volume) > 1e-6):
        raise AssertionError("Mesh manifold/independent tetrahedron volume mismatch")
    if area is not None and abs(mesh["area_mm2"] - area) > 1e-6:
        raise AssertionError("Mesh independent planar area mismatch")
    for fmt in ("brep", "step"):
        artifact = result["artifacts"][fmt]
        if not valid_sha(artifact["sha256"]) or type(artifact["bytes"]) is not int or artifact["bytes"] <= 0:
            raise AssertionError("Missing real BRep/STEP artifact receipt")
    return mesh


def version_contract(response, state, head):
    for item in (response, state):
        if any(item[key] != head[key] for key in ("project_id", "revision", "sha256")):
            raise AssertionError("Cross-project/version/snapshot response")
    if response["document_sha256"] != sha(state["doc"]):
        raise AssertionError("Response/document version mismatch")


def suite_contract(saved):
    """A partial or originally failed execution cannot become accepted readback."""
    if (saved.get("verdict") != "PASS" or saved.get("rc") != 0
            or saved.get("acceptance") != "REAL_HTTP_DB_BREP_STEP"
            or not saved.get("finished_at") or saved.get("first_cause")
            or saved.get("source_drift") is not False):
        raise AssertionError("Original suite is failed, incomplete or not real acceptance")
    checks = saved["checks"]
    readback = saved["readback"]
    actual = {"checks": len(checks), "passed": sum(item["passed"] is True for item in checks),
              "failed": sum(item["passed"] is not True for item in checks),
              "http_requests": len(saved["http_requests"]),
              "readback_checks": len(readback["checks"]),
              "readback_passed": sum(item["passed"] is True for item in readback["checks"])}
    if (not checks or not readback["checks"] or not saved["http_requests"]
            or actual != saved["counts"] or actual["failed"] != 0
            or actual["readback_checks"] != actual["readback_passed"] or readback["ok"] is not True):
        raise AssertionError("Saved counts disagree, are empty or contain failed checks")
    if (len(saved["cli_processes"]) != 2 or any(
            process.get("rc") != 0 or process.get("shutdown_error") or not process.get("ready_at")
            for process in saved["cli_processes"])
            or saved["readback_execution"] != {"rc": 0, "rc_measured": True}):
        raise AssertionError("Original CLI/readback did not terminate successfully")
    return actual


def verify_saved(source, evidence, review):
    """Read existing logs, HTTP receipts, local DB and artifacts; never rerun them."""
    evidence = external(evidence, source).resolve(strict=True)
    saved = read_json(evidence / "result.json")
    counts = suite_contract(saved)
    review.report.update(original_result=str(evidence / "result.json"),
                         original_result_sha256=sha((evidence / "result.json").read_bytes()),
                         original_finished_at=saved["finished_at"], original_counts=counts)
    review.check("readback/original-terminal-suite", True, counts=counts)
    before, after = saved["source_before"], saved["source_after"]
    if Path(before["root"]).resolve() != source or before["manifest"] != after["manifest"]:
        raise AssertionError("Original source root/drift mismatch")
    if any(sha(canonical(item["manifest"])) != item["sha256"] for item in (before, after)):
        raise AssertionError("Original manifest digest mismatch")
    current = source_binding(source)
    owned = {"tools/trim_loop_private_review.py", "docs/design/trim-loop-private-review.md"}
    old_product = {key: value for key, value in before["manifest"].items() if key not in owned}
    new_product = {key: value for key, value in current["manifest"].items() if key not in owned}
    review.check("readback/current-product-byte-path-mode-binding", old_product == new_product,
                 original_sha=before["sha256"], current_sha=current["sha256"],
                 allowed_tool_document_changes=sorted(owned))

    def bound_file(path):
        path = Path(path)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(evidence):
            raise AssertionError("Foreign evidence artifact: " + str(path))
        if any(part.is_symlink() for part in (path, *path.parents)
               if part.is_relative_to(evidence)):
            raise AssertionError("Linked evidence artifact: " + str(path))
        if not resolved.is_file():
            raise AssertionError("Nonregular evidence artifact: " + str(path))
        return resolved

    for process in saved["cli_processes"]:
        log = bound_file(process["log"])
        text = log.read_text()
        port = process["argv"][process["argv"].index("--port") + 1]
        review.check("readback/cli-log-" + process["label"],
                     sha(log.read_bytes()) == process["log_sha256"]
                     and "OPENCAD_PRIVATE_WEB verdict=READY port=" + port in text
                     and str(source / APP_REL / "secure_web.py") in process["argv"])
    runtime = external(Path(saved["runtime_root"]), source, local=True).resolve(strict=True)
    store = SimpleNamespace(db_path=runtime / "projects/projects.sqlite3")
    review.check("readback/final-complete-project-db", db_anchor(store) == saved["final_db"])
    with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("BEGIN")
        for index, request in enumerate(saved["http_requests"], 1):
            if request["route"] == "/api/auth/login":
                review.check("readback/login-not-recorded-%s" % index,
                             "body" not in request and "response_sha256" not in request
                             and not (evidence / ("http-%03d.json" % index)).exists())
                continue
            path = bound_file(evidence / ("http-%03d.json" % index))
            raw = path.read_bytes()
            review.check("readback/http-reply-%s" % index,
                         len(raw) == request["bytes"] and sha(raw) == request["response_sha256"])
            if request["method"] == "POST" and request["status"] == 200 and request["body"].get("request_id"):
                row = connection.execute(
                    "SELECT r.request_payload,r.request_sha256,r.response,r.response_sha256,"
                    "r.revision,s.sha256 FROM receipts r JOIN snapshots s "
                    "ON r.project_id=s.project_id AND r.revision=s.revision "
                    "WHERE r.project_id=? AND r.request_id=?",
                    (request["project_id"], request["body"]["request_id"])).fetchone()
                if row is None:
                    raise AssertionError("Acknowledged HTTP mutation has no durable receipt")
                payload, payload_sha, response_text, response_sha, revision, snapshot_sha = row
                response = json.loads(response_text, parse_constant=reject_constant)
                actual = json.loads(raw, parse_constant=reject_constant)
                review.check("readback/http-durable-receipt-%s" % index,
                             payload == canonical({**request["body"], "route": request["route"]})
                             and sha(payload) == payload_sha and sha(response_text) == response_sha
                             and actual == {**response, "project_id": request["project_id"],
                                            "revision": revision, "sha256": snapshot_sha})
        head = saved["final_head"]
        row = connection.execute(
            "SELECT p.head_revision,s.sha256,s.payload FROM projects p JOIN snapshots s "
            "ON p.project_id=s.project_id AND p.head_revision=s.revision WHERE p.project_id=?",
            (head["project_id"],)).fetchone()
        review.check("readback/head-canonical-snapshot", row is not None
                     and row[0] == head["revision"] and row[1] == head["sha256"]
                     and sha(row[2]) == row[1]
                     and json.loads(row[2], parse_constant=reject_constant) == head["snapshot"])
    requests = {item["name"]: (index, item) for index, item in enumerate(saved["http_requests"], 1)}
    for measurement in saved["measurements"]:
        index, request = requests[measurement["name"]]
        response = read_json(evidence / ("http-%03d.json" % index))
        state_index, _ = requests[measurement["name"] + "/state"]
        state = read_json(evidence / ("http-%03d.json" % state_index))
        version_contract(response, state, measurement["version"])
        properties = response["result"]["properties"]
        signature = sorted(item["inner_wire_count"] for item in properties["faces"])
        area = 50 if properties["solid_count"] == 0 else None
        volume = 100 if properties["solid_count"] == 1 else None
        mesh = measured_contract(response["result"], signature, area=area, volume=volume)
        review.check("readback/http-measured-version-" + measurement["name"],
                     mesh == measurement["mesh"]
                     and response["result"]["roundtrip"] == measurement["roundtrip"])
    inputs = read_json(evidence / "readback-input.json")
    saved_readback = read_json(evidence / "readback.json")
    review.check("readback/independent-result-exact",
                 saved_readback == saved["readback"] and len(inputs) == 4
                 and (evidence / "readback.py").read_text() == READBACK
                 and saved_readback["ok"] is True
                 and all(item["passed"] is True for item in saved_readback["checks"]),
                 stdout_marker_required=False,
                 terminal_evidence="original measured rc plus completed readback JSON")
    shape_map = {shape["name"]: shape for shape in saved_readback["shapes"]}
    for fixture in inputs:
        measured_contract(fixture["result"], fixture["signature"],
                          area=fixture["area"], volume=fixture["volume"])
        execution = fixture["result"]["execution"]
        job = bound_file(Path(execution["job_dir"]) / "execution.json")
        review.check("readback/real-worker-execution-" + fixture["name"],
                     read_json(job) == execution and execution["rc"] == 0 and execution["rc_measured"] is True
                     and execution["sources_before"] == execution["sources_after"]
                     and all(str(source) in name and before["manifest"][
                         Path(name).relative_to(source).as_posix()]["sha256"] == digest
                             for name, digest in execution["sources_before"].items()))
        for fmt in ("brep", "step"):
            artifact = fixture["result"]["artifacts"][fmt]
            path = bound_file(artifact["path"])
            shape = shape_map[fixture["name"] + "/" + fmt]
            review.check("readback/artifact-and-version-" + fixture["name"] + "/" + fmt,
                         sha(path.read_bytes()) == artifact["sha256"] and path.stat().st_size == artifact["bytes"]
                         and shape["gateway_version"] == fixture["gateway_version"]
                         and shape["hole_signature"] == fixture["signature"]
                         and len(shape["witnesses"]) == len(fixture["witnesses"])
                         and all(actual["point_mm"] == point and actual["actual"] is expected
                                 and actual["expected"] is expected
                                 for actual, (point, expected) in zip(shape["witnesses"], fixture["witnesses"])))
    review.report["verified_geometry"] = saved_readback["shapes"]
    review.report["acceptance"] = "EVIDENCE_VERIFIED_ONLY_NO_HTTP_OR_WORKER_RERUN"


READBACK = '''import FreeCAD as App
import Part
import hashlib
import json
import os
import traceback
from pathlib import Path
result = {"ok": False, "checks": [], "shapes": []}
def check(label, condition):
    result["checks"].append({"name": label, "passed": bool(condition)})
    if not condition:
        raise AssertionError(label)
try:
    result["backend"] = {"freecad": list(App.Version()), "occt": Part.OCC_VERSION}
    inputs = json.loads(Path(os.environ["TRIM_PRIVATE_READBACK_INPUT"]).read_text())
    for fixture in inputs:
        for fmt in ("brep", "step"):
            artifact = fixture["result"]["artifacts"][fmt]
            path = Path(artifact["path"])
            label = fixture["name"] + "/" + fmt
            check(label + "/bytes-and-sha", path.stat().st_size == artifact["bytes"]
                  and hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"])
            shape = Part.Shape()
            if fmt == "brep":
                shape.importBrep(str(path))
            else:
                shape = Part.read(str(path))
            check(label + "/valid", shape.isValid() and bool(shape.Faces))
            signature = sorted(len(face.Wires)-1 for face in shape.Faces)
            check(label + "/holes-and-closed-wires", signature == fixture["signature"]
                  and all(w.isClosed() for face in shape.Faces for w in face.Wires))
            volume = fixture.get("volume")
            if volume is None:
                check(label + "/open", not shape.Solids)
                if fixture.get("area") is not None:
                    check(label + "/analytic-area", abs(shape.Area-fixture["area"]) < 1e-6)
            else:
                check(label + "/closed-analytic-volume", len(shape.Solids) == 1
                      and shape.isClosed() and abs(shape.Volume-volume) < 1e-6)
            witnesses = []
            for point, expected in fixture["witnesses"]:
                p = App.Vector(*point)
                distance = shape.distToShape(Part.Vertex(p))[0]
                actual = shape.isInside(p, 1e-7, False) if volume is not None else distance < 1e-7
                witnesses.append({"point_mm": point, "actual": actual,
                                  "expected": expected, "distance_mm": distance})
                check(label + "/witness/" + str(point), actual == expected)
            result["shapes"].append({"name": label, "area_mm2": shape.Area,
                                    "volume_mm3": shape.Volume if shape.Solids else None,
                                    "hole_signature": signature, "witnesses": witnesses,
                                    "gateway_version": fixture["gateway_version"]})
    rectangle = Part.makePlane(8, 8, App.Vector(1, 1, 0))
    check("negative-control/bounding-rectangle-fills-hole",
          rectangle.distToShape(Part.Vertex(App.Vector(2.5, 2.5, 0)))[0] < 1e-7)
    result["ok"] = True
except Exception:
    result["first_cause"] = traceback.format_exc()
    print(result["first_cause"])
Path(os.environ["TRIM_PRIVATE_READBACK_RESULT"]).write_text(json.dumps(result, indent=2))
print("TRIM_PRIVATE_READBACK verdict=" + ("PASS" if result["ok"] else "FAIL"))
raise SystemExit(0 if result["ok"] else 3)
'''


def workflow(source, runtime, review):
    access, stores, docmodel, surfaces, subproc = import_product(source)
    credentials = {name: secrets.token_hex(24) for name in ("owner", "other")}
    principals = {}
    review.private_values.extend(credentials.values())
    auth = access.AuthController(runtime / "accounts", allow_registration=True)
    try:
        for name, credential in credentials.items():
            principals[name] = auth.register(name, credential)
    finally:
        auth.close()
    store = stores.ProjectStore(runtime / "projects")
    cookies, captures = {}, []
    recipe = polygon_recipe()
    expected_area = analytic_area(recipe)
    review.check("fixture/analytic-concave-double-hole-area", abs(expected_area - 50) < 1e-10,
                 area_mm2=expected_area, bbox_area_mm2=64, holes=2)

    def state(http, head, label):
        data = http.json(label, "/api/state", project=head["project_id"])[0]
        durable = store.read_head(head["project_id"])
        review.check(label + "/durable-head-and-document",
                     all(data[key] == durable[key] for key in ("project_id", "revision", "sha256"))
                     and data["doc"] == durable["snapshot"]["doc"])
        return data

    def receipt(label, route, body, response):
        item = store.read_receipt(response["project_id"], body["request_id"],
                                  request_payload={**body, "route": route})
        review.check(label + "/durable-receipt",
                     item is not None and all(item[key] == response[key] for key in (
                         "project_id", "revision", "sha256"))
                     and {**item["response"], **{key: item[key] for key in (
                         "project_id", "revision", "sha256")}} == response)

    def reject(http, label, head, body, status, error, *, route="/api/surfaces", **kwargs):
        before, old_head = db_anchor(store), store.read_head(head["project_id"])
        code, _, data, _ = http.call(label, route, body, project=head["project_id"], **kwargs)
        review.check(label + "/exact-denial", code == status and isinstance(data, dict)
                     and data.get("ok") is False and data.get("error") == error,
                     expected_status=status, actual_status=code, response=data)
        after = db_anchor(store)
        review.check(label + "/entire-db-head-history-receipts-unchanged",
                     after == before and store.read_head(head["project_id"]) == old_head,
                     before=before, after=after)
        if body is not None and body.get("request_id") and error != "idempotency_conflict":
            review.check(label + "/no-success-receipt", store.read_receipt(
                head["project_id"], body["request_id"], request_payload={**body, "route": route}) is None)

    def capture(http, label, head, expected_recipe, signature, *, area=None, volume=None, witnesses=None):
        before = db_anchor(store)
        current = state(http, head, label + "/state")
        data = http.json(label, "/api/surfaces", project=head["project_id"])[0]
        version_contract(data, current, head)
        document = docmodel.document_from_l1(current["doc"])
        review.check(label + "/canonical-document-recipe",
                     document.extra["x_industrial_surfaces"] == {"schema": 1, "recipe": expected_recipe}
                     and data["surface"] == document.extra["x_industrial_surfaces"]
                     and docmodel.document_to_l1(document) == current["doc"])
        measurement = measured_contract(data["result"], signature, area=area, volume=volume)
        expected_sources = {name: sha((source / CORE_REL / "cadcore/kernel" / name).read_bytes())
                            for name in ("surfaces.py", "surfaces_worker.py")}
        review.check(label + "/http-kernel-source-binding",
                     data["result"]["kernel_source_sha256"] == expected_sources)
        review.check(label + "/get-zero-project-writes", db_anchor(store) == before)
        review.report.setdefault("measurements", []).append(
            {"name": label, "version": {key: data[key] for key in (
                "project_id", "revision", "sha256", "document_sha256")},
             "area_mm2": data["result"]["properties"]["area_mm2"],
             "volume_mm3": data["result"]["properties"]["volume_mm3"],
             "mesh": measurement, "roundtrip": data["result"]["roundtrip"]})
        if witnesses is not None:
            captures.append({"name": label, "recipe": copy.deepcopy(document.extra["x_industrial_surfaces"]["recipe"]),
                             "signature": signature, "area": area, "volume": volume,
                             "witnesses": witnesses, "gateway_version": {
                                 key: data[key] for key in ("project_id", "revision", "sha256", "document_sha256")},
                             "http_properties": data["result"]["properties"]})
        return current, data

    points = [([1.5, 5, 0], True), ([2.5, 2.5, 0], False),
              ([4.3, 2.5, 0], False), ([7, 7, 0], False)]
    try:
        review.check("fixture/initial-complete-db-empty", all(v == 0 for v in db_anchor(store)["counts"].values()))
        with secure_cli(source, runtime, review, "initial") as (port, origin):
            http = HTTP(port, origin, review, cookies)
            for name in credentials:
                login, headers, _ = http.json("login-" + name, "/api/auth/login",
                    {"username": name, "password": credentials[name]}, user=None)
                cookie = headers.get("Set-Cookie", "")
                review.private_values.extend([cookie, cookie.split(";", 1)[0]])
                review.check("login-" + name + "/real-cookie",
                             cookie.startswith("opencad_session=") and "HttpOnly" in cookie
                             and "SameSite=Strict" in cookie)
                cookies[name] = cookie.split(";", 1)[0]
                review.check("login-" + name + "/identity",
                             login["user"] == principals[name])
            head = http.json("create", "/api/projects", {"title": "Exact UV double-hole private review",
                             "request_id": secrets.token_hex(16), "standard": "gbt"}, status=201)[0]
            project = head["project_id"]
            for label, user, overrides, status, error in (
                ("anonymous", None, {}, 401, "missing_token"),
                ("forged", None, {"Cookie": "opencad_session=forged"}, 401, "invalid_token"),
                ("other-owner", "other", {}, 403, "project_forbidden"),
                ("csrf-missing", "owner", {"X-OpenCAD-Request": None}, 403, "cross_site_request"),
                ("csrf-origin", "owner", {"Origin": "https://example.invalid"}, 403, "cross_site_request")):
                reject(http, label, head, edit(head, recipe=recipe), status, error,
                       user=user, overrides=overrides)
            create_body = edit(head, recipe=recipe)
            surface = http.json("polygon-post", "/api/surfaces", create_body, project=project)[0]
            receipt("polygon-post", "/api/surfaces", create_body, surface)
            current, response = capture(http, "polygon", surface, recipe, [2],
                                        area=expected_area, witnesses=points)
            version_contract(surface, current, surface)
            measured_contract(surface["result"], [2], area=expected_area)
            review.check("polygon/one-undo", current["undo_depth"] == 1 and current["redo_depth"] == 0)
            save_body = edit(surface)
            saved = http.json("save", "/api/save", save_body, project=project)[0]
            receipt("save", "/api/save", save_body, saved)
            saved_state, _ = capture(http, "saved-polygon", saved, recipe, [2], area=expected_area)
            review.check("save/exact-document-history", saved["saved"] is True
                         and saved_state["doc"] == current["doc"] and saved_state["undo_depth"] == 1)
            before = db_anchor(store)
            replay = http.json("old-polygon-receipt-replay", "/api/surfaces", create_body, project=project)[0]
            review.check("receipt/old-response-not-current-head", replay == surface
                         and saved["revision"] > replay["revision"] and db_anchor(store) == before)
            conflict = {**create_body, "recipe": {**recipe, "holes": []}}
            reject(http, "receipt-conflict", saved, conflict, 409, "idempotency_conflict")
            reject(http, "stale-base", saved, edit(surface, recipe=recipe), 409, "revision_conflict")
            for route in ("/api/mesh", "/api/step"):
                reject(http, "domain-" + route.rsplit("/", 1)[1], saved, None, 422,
                       "unsupported_geometry_domain", route=route)
            bad_recipes = []
            bad = copy.deepcopy(recipe)
            bad["outer"] = bad["outer"][:-1]
            bad_recipes.append(("unclosed", bad))
            bad = copy.deepcopy(recipe)
            bad["outer"] = [[.1, .1], [.9, .9], [.1, .9], [.9, .1], [.1, .1]]
            bad_recipes.append(("self-cross", bad))
            bad = copy.deepcopy(recipe)
            bad["holes"] = [[[.7, .6], [.7, .8], [.8, .8], [.8, .6], [.7, .6]]]
            bad_recipes.append(("hole-in-concave-notch", bad))
            bad = copy.deepcopy(recipe)
            bad["holes"].append(copy.deepcopy(bad["holes"][0]))
            bad_recipes.append(("holes-overlap", bad))
            bad = copy.deepcopy(recipe)
            bad["outer"] = list(reversed(bad["outer"]))
            bad_recipes.append(("outer-direction", bad))
            bad = copy.deepcopy(recipe)
            bad["holes"][0] = list(reversed(bad["holes"][0]))
            bad_recipes.append(("hole-direction", bad))
            bad = copy.deepcopy(recipe)
            bad["outer"][1][0] = 1.1
            bad_recipes.append(("out-of-uv-bounds", bad))
            bad = copy.deepcopy(recipe)
            bad["outer"] = [[.1, .1], [.2, .1], [.3, .1], [.1, .1]]
            bad_recipes.append(("zero-area", bad))
            bad = copy.deepcopy(recipe)
            bad["outer"].insert(1, list(bad["outer"][0]))
            bad_recipes.append(("duplicate-vertex", bad))
            for label, invalid in bad_recipes:
                reject(http, "invalid-" + label, saved, edit(saved, recipe=invalid), 400, "bad_surface_arguments")
            degenerate = copy.deepcopy(recipe)
            degenerate["surface"]["poles_nm"] = [[[0, j * 10_000_000, 0] for j in range(2)] for i in range(2)]
            reject(http, "physical-degenerate", saved, edit(saved, recipe=degenerate), 422, "surface_failed")
            invalid_doc = docmodel.document_from_l1(saved_state["doc"])
            invalid_doc.extra["x_industrial_surfaces"]["recipe"] = bad_recipes[2][1]
            reject(http, "invalid-open", saved, edit(saved, doc=docmodel.document_to_l1(invalid_doc)),
                   400, "bad_surface_arguments", route="/api/open")
            thick_recipe = {"op": "thicken", "surface": recipe, "thickness_nm": 2_000_000, "inward": False}
            thick_body = edit(saved, recipe=thick_recipe)
            thick = http.json("thicken-post", "/api/surfaces", thick_body, project=project)[0]
            receipt("thicken", "/api/surfaces", thick_body, thick)
            signature = [0] * 13 + [2, 2]
            thick_points = [([p[0], p[1], 1], inside) for p, inside in points]
            thick_state, _ = capture(http, "thickened", thick, thick_recipe, signature,
                                    volume=expected_area * 2, witnesses=thick_points)
            undo_body = edit(thick, command="undo", args={})
            undone = http.json("undo", "/api/command", undo_body, project=project)[0]
            receipt("undo", "/api/command", undo_body, undone)
            undo_state, _ = capture(http, "undo-polygon", undone, recipe, [2], area=expected_area)
            review.check("undo/exact-document-history", undo_state["doc"] == saved_state["doc"]
                         and undo_state["undo_depth"] == 1 and undo_state["redo_depth"] == 1)
            redo_body = edit(undone, command="redo", args={})
            redone = http.json("redo", "/api/command", redo_body, project=project)[0]
            receipt("redo", "/api/command", redo_body, redone)
            redo_state, _ = capture(http, "redo-thickened", redone, thick_recipe, signature,
                                   volume=expected_area * 2)
            review.check("redo/exact-document-history", redo_state["doc"] == thick_state["doc"]
                         and redo_state["undo_depth"] == 2 and redo_state["redo_depth"] == 0)
            # /api/document is JSON bytes, not the API {ok:true} envelope.
            code, headers, _, document_bytes = http.call("download-document", "/api/document", project=project)
            review.check("download/version-and-exact-document", code == 200
                         and document_bytes.decode() == redo_state["doc"]
                         and headers.get("X-OpenCAD-Revision") == str(redone["revision"])
                         and headers.get("X-OpenCAD-Document-SHA256") == sha(document_bytes))
            (review.out / "saved-document.l1").write_bytes(document_bytes)
            restart_anchor = db_anchor(store)
            restart_head = store.read_head(project)
        review.check("initial/original-cli-rc", review.report["cli_processes"][-1]["rc"] == 0)
        with secure_cli(source, runtime, review, "reopen") as (port, origin):
            http = HTTP(port, origin, review, cookies)
            reopened_state, _ = capture(http, "cli-reopened-thickened", redone, thick_recipe, signature,
                                       volume=expected_area * 2, witnesses=thick_points)
            review.check("reopen/cookie-db-history-and-head-survive",
                         db_anchor(store) == restart_anchor and store.read_head(project) == restart_head
                         and reopened_state == redo_state)
            before = db_anchor(store)
            review.check("reopen/old-surface-receipt", http.json(
                "reopen-old-receipt", "/api/surfaces", create_body, project=project)[0] == surface
                and db_anchor(store) == before)
            open_body = edit(redone, doc=document_bytes.decode())
            opened = http.json("open-canonical-document", "/api/open", open_body, project=project)[0]
            receipt("open", "/api/open", open_body, opened)
            opened_state, _ = capture(http, "http-opened-thickened", opened, thick_recipe, signature,
                                     volume=expected_area * 2, witnesses=thick_points)
            review.check("open/canonical-doc-and-history-reset", opened_state["doc"] == redo_state["doc"]
                         and opened_state["undo_depth"] == 0 and opened_state["redo_depth"] == 0)
            review.report["final_head"] = store.read_head(project)
            review.report["final_db"] = db_anchor(store)
        review.check("reopen/original-cli-rc", review.report["cli_processes"][-1]["rc"] == 0)
        kernel = surfaces.SurfaceKernel(evidence_dir=review.out / "kernel-jobs")
        for item in captures:
            item["result"] = kernel.build(item["recipe"])
            measured_contract(item["result"], item["signature"], area=item["area"], volume=item["volume"])
            execution = item["result"]["execution"]
            review.check(item["name"] + "/independent-real-bound-worker",
                         execution["rc"] == 0 and execution["rc_measured"] is True
                         and execution["sources_before"] == execution["sources_after"]
                         and execution["sources_before"] == {
                             str(source / CORE_REL / "cadcore/kernel" / name):
                             sha((source / CORE_REL / "cadcore/kernel" / name).read_bytes())
                             for name in ("surfaces.py", "surfaces_worker.py")})
            review.check(item["name"] + "/independent-vs-http-properties",
                         abs(item["result"]["properties"]["area_mm2"] - item["http_properties"]["area_mm2"]) < 1e-6
                         and item["result"]["properties"]["inner_wire_count"] == item["http_properties"]["inner_wire_count"]
                         and item["result"]["properties"]["volume_mm3"] == item["http_properties"]["volume_mm3"])
        write_json(review.out / "readback-input.json", captures)
        script = review.out / "readback.py"
        script.write_text(READBACK, encoding="utf-8")
        env = product_env(source, runtime)
        env.update(TRIM_PRIVATE_READBACK_INPUT=str(review.out / "readback-input.json"),
                   TRIM_PRIVATE_READBACK_RESULT=str(review.out / "readback.json"))
        outcome = subproc._run([subproc.find_executable(), str(script)], cwd=str(runtime), env=env, timeout_s=60)
        (review.out / "readback-stdout.log").write_text(outcome.stdout)
        (review.out / "readback-stderr.log").write_text(outcome.stderr)
        review.report["readback_execution"] = {"rc": outcome.rc, "rc_measured": outcome.rc_measured}
        result = json.loads((review.out / "readback.json").read_text())
        review.report["readback"] = result
        review.check("independent-brep-step-readback", outcome.rc == 0 and outcome.rc_measured
                     and result["ok"] is True and bool(result["checks"]), result=result)
        review.report["runtime_origins"] = runtime_origins(source)
    finally:
        store.close()


class Controls(unittest.TestCase):
    def test_freeze_full_sha(self):
        binding = {"sha256": "a" * 64}
        frozen(binding, "a" * 64)
        for invalid in ("a" * 63, "a" * 63 + "b", None, ""):
            with self.assertRaises(ValueError):
                frozen(binding, invalid)

    def test_version_cross_project_revision_document(self):
        head = {"project_id": "p", "revision": 3, "sha256": "a" * 64}
        state = {**head, "doc": "canonical"}
        response = {**head, "document_sha256": sha("canonical")}
        version_contract(response, state, head)
        for key, bad in (("project_id", "q"), ("revision", 2), ("sha256", "b" * 64),
                         ("document_sha256", sha("different"))):
            with self.assertRaises(AssertionError):
                version_contract({**response, key: bad}, state, head)

    def test_mesh_tetrahedron_and_open_nonfinite(self):
        mesh = {"vertices_mm": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "triangles": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}
        result = mesh_measure(mesh)
        self.assertTrue(result["closed_manifold"])
        self.assertAlmostEqual(result["volume_mm3"], 1 / 6)
        self.assertFalse(mesh_measure({**mesh, "triangles": mesh["triangles"][:-1]})["closed_manifold"])
        for invalid in (
            {"vertices_mm": [], "triangles": []},
            {**mesh, "triangles": [[0, 1, 9]]},
            {**mesh, "triangles": [[0, 1, True]]},
            {**mesh, "vertices_mm": [[math.nan, 0, 0], *mesh["vertices_mm"][1:]]}):
            with self.assertRaises(ValueError):
                mesh_measure(invalid)

    def test_measurement_wrong_area_filled_hole_step(self):
        item = {"valid": True, "solid_count": 0, "volume_mm3": None, "face_count": 1,
                "faces": [{"inner_wire_count": 2, "wires_closed": True}], "area_mm2": 50}
        mesh = {"vertices_mm": [[0, 0, 0], [10, 0, 0], [0, 10, 0]], "triangles": [[0, 1, 2]]}
        result = {"properties": copy.deepcopy(item), "roundtrip": {"brep": copy.deepcopy(item),
                  "step": copy.deepcopy(item)}, "mesh": mesh,
                  "artifacts": {fmt: {"sha256": "a" * 64, "bytes": 12} for fmt in ("brep", "step")}}
        measured_contract(result, [2], area=50)
        for target, key, value in (("properties", "area_mm2", 64),
                                   ("step", "faces", [{"inner_wire_count": 0, "wires_closed": True}]),
                                   ("brep", "volume_mm3", 100)):
            bad = copy.deepcopy(result)
            (bad[target] if target == "properties" else bad["roundtrip"][target])[key] = value
            with self.assertRaises(AssertionError):
                measured_contract(bad, [2], area=50)

    def test_source_digest_tail_add_remove(self):
        manifest = {"a": {"sha256": "a" * 64, "bytes": 1}}
        baseline = sha(canonical(manifest))
        for changed in ({}, {**manifest, "b": manifest["a"]},
                        {"a": {"sha256": "a" * 63 + "b", "bytes": 1}}):
            self.assertNotEqual(baseline, sha(canonical(changed)))

    def test_nonfinite_brep_measurement_is_rejected(self):
        item = {"valid": True, "solid_count": 0, "volume_mm3": None, "face_count": 1,
                "faces": [{"inner_wire_count": 2, "wires_closed": True}], "area_mm2": math.nan}
        result = {"properties": item, "roundtrip": {"brep": item, "step": item},
                  "mesh": {"vertices_mm": [[0, 0, 0], [10, 0, 0], [0, 10, 0]],
                           "triangles": [[0, 1, 2]]},
                  "artifacts": {fmt: {"sha256": "a" * 64, "bytes": 12} for fmt in ("brep", "step")}}
        with self.assertRaises(AssertionError):
            measured_contract(result, [2], area=50)

    def test_nonfinite_json_and_expected_values(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(ValueError):
                json.loads('{"value":' + value + "}", parse_constant=reject_constant)
        for value in (math.nan, math.inf, -math.inf, True, None, 0, -1):
            self.assertFalse(finite_positive(value))
        self.assertTrue(finite_positive(50))

    def test_failed_partial_suite_never_promoted(self):
        sample = {"verdict": "PASS", "rc": 0, "acceptance": "REAL_HTTP_DB_BREP_STEP",
                  "finished_at": stamp(), "source_drift": False,
                  "checks": [{"passed": True}], "http_requests": [{"status": 200}],
                  "readback": {"ok": True, "checks": [{"passed": True}]},
                  "counts": {"checks": 1, "passed": 1, "failed": 0, "http_requests": 1,
                             "readback_checks": 1, "readback_passed": 1},
                  "cli_processes": [{"rc": 0, "ready_at": stamp()} for i in range(2)],
                  "readback_execution": {"rc": 0, "rc_measured": True}}
        suite_contract(sample)
        for key, value in (("verdict", "FAIL"), ("rc", 1), ("finished_at", None),
                           ("checks", []), ("first_cause", "original failure"),
                           ("acceptance", "PREFLIGHT_ONLY"), ("source_drift", True),
                           ("counts", {**sample["counts"], "passed": 2}),
                           ("cli_processes", [{"rc": -9, "ready_at": stamp()}])):
            with self.assertRaises(AssertionError):
                suite_contract({**sample, key: value})

    def test_analytic_fixture_not_rectangle(self):
        self.assertAlmostEqual(analytic_area(polygon_recipe()), 50)
        self.assertNotEqual(analytic_area(polygon_recipe()), 64)

    def test_external_local_network_and_source_rejected(self):
        external(Path("/data/tmp/trim-private-control"), ROOT, local=True)
        external(Path("/hf3fs-jd/data/tmp/trim-private-control"), ROOT)
        for path, local in ((ROOT / "runtime", True), (Path("/hf3fs-jd/data/tmp/runtime"), True),
                            (Path("/data/tmp/evidence"), False), (Path("/data/tmp/../dev"), True)):
            with self.assertRaises(ValueError):
                external(path, ROOT, local=local)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True, help="external parent; always creates a fresh child")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--expect-source-sha")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--verify-result", type=Path, help="read an existing evidence directory; no HTTP/worker rerun")
    args = parser.parse_args()
    source = args.source_root.resolve(strict=True)
    parent = external(args.out, source)
    out = parent / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(4))
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "acceptance": "NOT_RUN", "started_at": stamp(),
              "source_root": str(source), "evidence_dir": str(out), "checks": [], "http_requests": [],
              "cli_processes": [], "tool_path": str(Path(__file__).resolve()),
              "tool_sha256": sha(Path(__file__).read_bytes()), "rc": 1,
              "boundaries": ["baseline selected source only; not integration/production acceptance",
                             "public surface artifact receipts have hashes/bytes, not download paths",
                             "independent recipe reconstruction is not a gateway STEP download",
                             "no browser draft/race, TLS/proxy, quotas or cross-DB revocation proof",
                             "no guide loft/full industrial parity, global G2, injectivity or all-offset proof",
                             "surface recipe has no solver DoF/residual; these are not measured/zero"]}
    review = Review(out, report)
    before = None
    started = time.monotonic()
    try:
        before = source_binding(source)
        report["source_before"] = before
        if args.selftest:
            with (out / "selftest.log").open("x") as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(Controls))
            report["selftest"] = {"tests": result.testsRun, "failures": len(result.failures),
                                  "errors": len(result.errors), "skips": len(result.skipped)}
            if not result.wasSuccessful() or not result.testsRun:
                raise AssertionError("Review validator selftest failed")
            report["verdict"], report["acceptance"] = "PASS", "SELFTEST_ONLY"
        elif args.verify_result:
            verify_saved(source, args.verify_result, review)
            report["verdict"] = "PASS"
        elif args.preflight:
            report["verdict"], report["acceptance"] = "PASS", "PREFLIGHT_ONLY"
        else:
            frozen(before, args.expect_source_sha)
            if args.runtime_root is None:
                raise ValueError("Execution requires --runtime-root")
            runtime = external(args.runtime_root, source, local=True)
            if runtime.exists():
                raise ValueError("Runtime already exists; no reuse or cleanup: " + str(runtime))
            runtime.mkdir(parents=True, exist_ok=False)
            report["runtime_root"] = str(runtime)
            os.environ.update(product_env(source, runtime))
            workflow(source, runtime, review)
            report["acceptance"], report["verdict"] = "REAL_HTTP_DB_BREP_STEP", "PASS"
        after = source_binding(source)
        report.update(source_after=after, source_drift=before["manifest"] != after["manifest"])
        review.check("source/zero-byte-path-mode-drift", not report["source_drift"])
        for path in out.rglob("*"):
            if path.is_file() and any(value.encode() in path.read_bytes() for value in review.private_values if value):
                raise AssertionError("Generated credential leaked into evidence: " + str(path))
        report["rc"] = 0
    except Exception as exc:
        report["verdict"], report["rc"] = "FAIL", 1
        report["first_cause"] = review.redact(traceback.format_exc())
        report["failure_type"] = type(exc).__name__
        if getattr(exc, "details", None) is not None:
            report["worker_failure"] = exc.details
        (out / "failure.log").write_text(report["first_cause"], encoding="utf-8")
        if before is not None:
            try:
                after = source_binding(source)
                report.update(source_after=after, source_drift=before["manifest"] != after["manifest"])
            except Exception as secondary:
                report.setdefault("secondary_failures", []).append(str(secondary))
        print(report["first_cause"], file=sys.stderr)
    report.update(finished_at=stamp(), elapsed_s=time.monotonic() - started,
                  counts={"checks": len(report["checks"]),
                          "passed": sum(item["passed"] for item in report["checks"]),
                          "failed": sum(not item["passed"] for item in report["checks"]),
                          "http_requests": len(report["http_requests"]),
                          "readback_checks": len(report.get("readback", {}).get("checks", [])),
                          "readback_passed": sum(item["passed"] for item in report.get("readback", {}).get("checks", []))})
    write_json(out / "result.json", json.loads(review.redact(json.dumps(report, allow_nan=False))))
    print("TRIM_LOOP_PRIVATE_REVIEW verdict=%s acceptance=%s rc=%s counts=%s source_sha256=%s evidence=%s" %
          (report["verdict"], report["acceptance"], report["rc"], canonical(report["counts"]),
           before["sha256"] if before else "UNBOUND", out), flush=True)
    return report["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
