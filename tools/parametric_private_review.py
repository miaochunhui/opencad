#!/usr/bin/env python3
"""Independent real private-HTTP parametric acceptance; never connects to production.

--preflight reads the selected source tree and emits a complete binding digest.
--selftest checks the review's own positive/negative measurement boundaries.
Full execution requires --expect-source-sha, creates fresh local SQLite state
and an ephemeral loopback server, and keeps logs/STEP/results in external 3FS.
Only test-account passwords are generated in memory; no credentials are logged.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.cookies import SimpleCookie
import http.client
import importlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-private-review")
DEFAULT_RUNTIME = Path("/data/dev/opencad-private-fixtures")
REQUIRED = (
    "oss-alternatives/apps/opencad/secure_web.py",
    "oss-alternatives/apps/opencad/project_gateway.py",
    "oss-alternatives/apps/opencad/project_store.py",
    "oss-alternatives/apps/opencad/access_control.py",
    "oss-alternatives/apps/opencad/server.py",
    "oss-alternatives/apps/opencad/industrial_parametric.py",
    "oss-alternatives/packages/cadcore/cadcore/parametric.py",
)
_SHA = set("0123456789abcdef")


def sha(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def valid_sha(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA


def regular_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Non-regular source: " + str(path))
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def source_binding(root):
    root = root.resolve(strict=True)
    paths = []
    for relative in ("oss-alternatives/apps/opencad", "oss-alternatives/packages/cadcore",
                     "oss-alternatives/packages/dxf", "oss-alternatives/packages/vault"):
        directory = root / relative
        if any(part.is_symlink() for part in (directory, *directory.parents) if part != root):
            raise ValueError("Linked source ancestor: " + relative)
        if not directory.is_dir():
            raise ValueError("Required source directory absent: " + relative)
        for parent, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in {"__pycache__", "build", "node_modules"})
            if any((Path(parent) / name).is_symlink() for name in dirs):
                raise ValueError("Linked source directory")
            paths.extend(Path(parent) / name for name in names
                         if Path(name).suffix in {".py", ".js", ".css", ".html", ".json", ".yaml", ".yml",
                                                  ".svg", ".dxf", ".step", ".stp", ".l1", ".txt", ".csv"})
    manifest = {path.relative_to(root).as_posix(): {"sha256": sha(regular_bytes(path)),
                                                  "bytes": path.stat().st_size}
                for path in sorted(paths)}
    if not manifest or any(path not in manifest for path in REQUIRED):
        raise ValueError("Incomplete source binding")
    gateway = regular_bytes(root / REQUIRED[1]).decode("utf-8")
    ready = all(name in gateway for name in (
        '"/api/parametric"', "read_parametric", "prepare_parametric", "before_document_sha256"))
    return {"root": str(root), "manifest": manifest, "sha256": sha(canonical(manifest)), "route_ready": ready}


def frozen_source(binding, expected):
    if not valid_sha(expected) or binding["sha256"] != expected:
        raise ValueError("Frozen source SHA absent or mismatched")
    if not binding["route_ready"]:
        raise ValueError("Real parametric gateway route is absent")


def source_changes(before, after):
    return {"added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(name for name in before.keys() & after.keys()
                              if before[name] != after[name])}


def runtime_bindings(source):
    prefixes = {"cadcore": source / "oss-alternatives/packages/cadcore",
                "dxf": source / "oss-alternatives/packages/dxf",
                "freechat_vault": source / "oss-alternatives/packages/vault"}
    app = source / "oss-alternatives/apps/opencad"
    app_names = {path.stem for path in app.glob("*.py")}
    bindings = {}
    for name, module in list(sys.modules.items()):
        prefix = prefixes.get(name.split(".")[0])
        if prefix is None and name not in app_names:
            continue
        filename = getattr(module, "__file__", None)
        if not filename:
            locations = [Path(path).resolve(strict=True) for path in getattr(module, "__path__", [])]
            if not locations or any(not path.is_relative_to(prefix or app) or not path.is_dir()
                                    for path in locations):
                raise ValueError("Unbound/foreign product namespace: " + name)
            bindings[name] = [path.relative_to(source).as_posix() for path in locations]
            continue
        path = Path(filename).resolve(strict=True)
        if not path.is_relative_to(prefix or app):
            raise ValueError("Foreign product runtime import: " + name)
        bindings[name] = path.relative_to(source).as_posix()
    if not bindings or "cadcore.parametric" not in bindings:
        raise ValueError("Incomplete runtime import binding")
    return dict(sorted(bindings.items()))


def remember_failure(report, review, exc):
    failure = review.redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    report["verdict"] = "FAIL"
    if "first_cause" not in report:
        report["first_cause"] = failure
    else:
        report.setdefault("secondary_failures", []).append(failure)
    sys.stderr.write(failure)
    try:
        with (review.out / "failure.log").open("a", encoding="utf-8") as log:
            log.write(failure)
    except OSError as secondary:
        report.setdefault("secondary_failures", []).append(
            review.redact("Failure evidence write also failed: " + str(secondary)))
        sys.stderr.write(report["secondary_failures"][-1] + "\n")


def safe_external(path, source, *, local=False):
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("External target cannot traverse symlinks: " + str(path))
    if path.resolve().is_relative_to(source.resolve()):
        raise ValueError("External target is inside source")
    if local and any(path.resolve().is_relative_to(base) for base in (
            Path("/3fs"), Path("/hf3fs-jd"), Path("/proc"), Path("/sys"), Path("/dev"))):
        raise ValueError("SQLite runtime requires an external durable local filesystem")
    return path


class Review:
    def __init__(self, out):
        self.out, self.assertions, self.checks = out, 0, []
        self.sensitive = []
        self.lock = threading.RLock()

    def redact(self, value):
        if isinstance(value, str):
            for secret in sorted(self.sensitive, key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: "[REDACTED]" if key.lower() in {
                "password", "authorization", "cookie", "set-cookie", "token"}
                    else self.redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        return value

    def record(self, name, **details):
        with self.lock:
            entry = self.redact({"at": datetime.now(timezone.utc).isoformat(), "name": name, **details})
            with (self.out / "audit.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(canonical(entry) + "\n")
            self.checks.append(entry)

    def check(self, condition, name, **details):
        with self.lock:
            self.assertions += 1
            self.record(name, passed=bool(condition), **details)
        if not condition:
            raise AssertionError(self.redact(name + ": " + canonical(details)))

    def receipt(self, response, state, project):
        self.check(response.get("project_id") == state.get("project_id") == project
                   and response.get("revision") == state.get("revision")
                   and response.get("sha256") == state.get("sha256")
                   and response.get("document_sha256") == sha(state["doc"]),
                   "committed_receipt_exact_project_revision_snapshot_document")


class Caller:
    def __init__(self, server, review, cookie=None):
        address = server.server_address
        if address[0] != "127.0.0.1":
            raise ValueError("Review caller only accepts its own IPv4 loopback server")
        self.address, self.review, self.cookie = address, review, cookie

    def call(self, path, body=None, project=None, *, login=False):
        if not path.startswith("/api/"):
            raise ValueError("Only explicit private API routes are allowed")
        connection = http.client.HTTPConnection(*self.address, timeout=60)
        headers = {"Connection": "close", "Origin": "http://127.0.0.1:" + str(self.address[1]),
                   "X-OpenCAD-Request": "1"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if project:
            headers["X-OpenCAD-Project"] = project
        data = None if body is None else canonical(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        started = time.monotonic()
        try:
            connection.request("GET" if body is None else "POST", path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            media = response.getheader("Content-Type", "").split(";", 1)[0]
            values = dict(response.getheaders())
            parsed = json.loads(raw) if media == "application/json" else raw
            self.review.record("http", path=path, method="GET" if body is None else "POST",
                               status=response.status, media=media, bytes=len(raw),
                               wall_s=time.monotonic() - started,
                               error=parsed.get("error") if isinstance(parsed, dict) else None,
                               detail=parsed.get("detail") if isinstance(parsed, dict) else None,
                               response_sha256=None if login else sha(raw))
            return response.status, parsed, values
        except Exception as exc:
            try:
                self.review.record("http_transport_failure", path=path,
                                   wall_s=time.monotonic() - started, cause=str(exc))
            except Exception as secondary:
                sys.stderr.write(self.review.redact(
                    f"HTTP diagnostic write also failed: {secondary}; primary: {exc}\n"))
            raise
        finally:
            connection.close()

    def json(self, path, body=None, project=None, status=200):
        actual, value, headers = self.call(path, body, project)
        success = actual == status and isinstance(value, dict) and value.get("ok") is True
        detail = ({"keys": sorted(value)} if success else value if isinstance(value, dict)
                  else {"type": type(value).__name__, "bytes": len(value)})
        self.review.check(success,
                          "http_success", path=path, actual_status=actual, expected_status=status,
                          response=detail)
        return value, headers

    def reject(self, path, body=None, project=None, *, status, error=None):
        actual, value, _ = self.call(path, body, project)
        self.review.check(actual == status and isinstance(value, dict) and value.get("ok") is False
                          and (error is None or value.get("error") == error),
                          "http_rejection", path=path, status=actual, expected_status=status, response=value)
        return value


def edit_body(state, **fields):
    return {"expected_revision": state["revision"], "expected_sha": state["sha256"],
            "request_id": uuid4().hex, **fields}


def parameter_body(state, **fields):
    defaults = {"action": "rebuild", "expected_document_sha256": sha(state["doc"])}
    return edit_body(state, **{**defaults, **fields})


def mesh_measure(mesh):
    if (not isinstance(mesh, dict) or mesh.get("solid_count") != 1
            or not isinstance(mesh.get("vertices_mm"), list) or not mesh["vertices_mm"]
            or not isinstance(mesh.get("triangles"), list) or not mesh["triangles"]):
        raise ValueError("Measured solid mesh absent")
    vertices, terms, edges = mesh["vertices_mm"], [], {}
    for point in vertices:
        if len(point) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
            raise ValueError("Nonfinite mesh vertex")
    for triangle in mesh["triangles"]:
        if len(triangle) != 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in triangle):
            raise ValueError("Invalid mesh triangle")
        a, b, c = (vertices[i] for i in triangle)
        terms.append((a[0] * (b[1] * c[2] - b[2] * c[1]) + a[1] * (b[2] * c[0] - b[0] * c[2])
                      + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted(tuple(round(v, 7) for v in vertices[triangle[index]]) for index in (i, j)))
            edges[edge] = edges.get(edge, 0) + 1
    if not edges or any(count != 2 for count in edges.values()):
        raise ValueError("Mesh is not closed by welded edge incidence")
    volume = abs(math.fsum(terms))
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("Mesh has no finite positive enclosed volume")
    return volume


def verify_plate(review, state, response, width, radius, depth):
    document_sha = sha(state["doc"])
    expected = (width * 20 - math.pi * radius**2) * depth
    review.check(response["document_sha256"] == document_sha,
                 "mesh_document_binding", revision=state["revision"], document_sha256=document_sha)
    solids = response.get("solids")
    review.check(isinstance(solids, list) and len(solids) == 1, "one_measured_solid")
    mesh = solids[0]
    volume = mesh["properties"]["volume_nm3"] / 1e18
    integrated = mesh_measure(mesh)
    review.check(abs(volume - expected) <= 1e-5, "brep_analytic_volume",
                 volume_mm3=volume, expected_mm3=expected)
    review.check(abs(integrated - expected) <= expected * .005, "mesh_independent_volume",
                 integrated_mm3=integrated, expected_mm3=expected)
    review.check(mesh["properties"]["bbox_nm"] == [0, 0, 0, width * 1_000_000, 20_000_000, depth * 1_000_000],
                 "measured_bbox")
    document = json.loads(state["doc"])
    circle = next(e for e in document["sketches"][1]["entities"] if e["kind"] == "circle")
    review.check(circle["geom"] == {"cx": width * 500_000, "cy": 10_000_000, "r": radius * 1_000_000},
                 "downstream_hole_geometry")
    review.check([f["params"]["distance_nm"] for f in document["features"]] == [depth * 1_000_000] * 2,
                 "downstream_boss_cut")


def store_anchor(store, project):
    head = store.read_head(project)
    with sqlite3.connect("file:" + str(store.db_path) + "?mode=ro", uri=True) as connection:
        counts = {table: connection.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE project_id=?", (project,)).fetchone()[0]
                  for table in ("snapshots", "receipts")}
    return {"head": head, "counts": counts}


def import_product(source):
    app = source / "oss-alternatives/apps/opencad"
    core = source / "oss-alternatives/packages/cadcore"
    sys.path[:0] = [str(app), str(core), str(source / "oss-alternatives/packages/dxf")]
    modules = {}
    for name, relative in (
            ("server", REQUIRED[4]), ("access_control", REQUIRED[3]),
            ("project_store", REQUIRED[2]), ("project_gateway", REQUIRED[1]),
            ("secure_web", REQUIRED[0]), ("industrial_parametric", REQUIRED[5]),
            ("cadcore.parametric", REQUIRED[6])):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve() != source / relative:
            raise ValueError("Foreign runtime import: " + name)
        modules[name] = module
    return modules


@contextmanager
def live_service(modules, data, review):
    auth = store = http = thread = primary = None
    try:
        auth = modules["access_control"].AuthController(data / "accounts", allow_registration=False)
        store = modules["project_store"].ProjectStore(data / "projects")
        application = modules["project_gateway"].ProjectGateway(
            auth, store, review.out / "request-runtime", secure_cookies=False)
        http = modules["secure_web"].build_server(application, host="127.0.0.1", port=0)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        review.check(http.server_address[0] == "127.0.0.1" and http.server_address[1] not in {8766, 8767},
                     "isolated_ephemeral_port", port=http.server_address[1])
        yield http, application
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanups = []
        if http is not None:
            if thread is not None and thread.ident is not None:
                cleanups.append(http.shutdown)
            cleanups.append(http.server_close)
        if thread is not None and thread.ident is not None:
            cleanups.append(lambda: thread.join(timeout=5))
            cleanups.append(lambda: review.check(not thread.is_alive(), "own_server_closed"))
        cleanups.extend(resource.close for resource in (auth, store) if resource is not None)
        failures = []
        for cleanup in cleanups:
            try:
                cleanup()
            except BaseException:
                failures.append(traceback.format_exc())
        if failures:
            if primary is None:
                raise RuntimeError("Own service cleanup failed:\n" + "\n".join(failures))
            sys.stderr.write(review.redact("Secondary cleanup failures:\n" + "\n".join(failures)))


def login(http, review, name, credential):
    anonymous = Caller(http, review)
    status, result, headers = anonymous.call("/api/auth/login", {"username": name, "password": credential},
                                             login=True)
    cookie = SimpleCookie()
    cookie.load(headers.get("Set-Cookie", ""))
    item = cookie.get("opencad_session")
    if item:
        review.sensitive.append(item.value)
    review.check(status == 200 and isinstance(result, dict) and result.get("ok") is True
                 and item is not None and item["httponly"] and item["samesite"] == "Strict",
                 "real_http_login_cookie", status=status)
    return Caller(http, review, "opencad_session=" + item.value)


def workflow(modules, data, review):
    account_names = ("review_" + uuid4().hex, "other_" + uuid4().hex)
    credentials = (secrets.token_urlsafe(24), secrets.token_urlsafe(24))
    review.sensitive.extend(credentials)
    registration = modules["access_control"].AuthController(data / "accounts", allow_registration=True)
    try:
        for name, credential in zip(account_names, credentials):
            registration.register(name, credential)
    finally:
        registration.close()
    saved = None
    with live_service(modules, data, review) as (http, application):
        anonymous = Caller(http, review)
        anonymous.reject("/api/parametric", status=401)
        caller = login(http, review, account_names[0], credentials[0])
        other = login(http, review, account_names[1], credentials[1])
        caller.json("/api/auth/me")
        created, _ = caller.json("/api/projects", {"title": "Parametric measured plate",
                                                  "request_id": uuid4().hex, "standard": "gbt"}, status=201)
        project = created["project_id"]
        state, _ = caller.json("/api/state", project=project)
        initial_anchor = store_anchor(application.store, project)
        initial, _ = caller.json("/api/parametric", project=project)
        review.check(initial["enabled"] is False and initial["can_create_plate"] is True
                     and initial["document_sha256"] == sha(state["doc"]), "initial_blank_project")
        request = parameter_body(state, action="create_plate")
        first, _ = caller.json("/api/parametric", request, project)
        first_state, _ = caller.json("/api/state", project=project)
        review.receipt(first, first_state, project)
        review.check(first_state["revision"] == state["revision"] + 1 and first_state["undo_depth"] == 1
                     and first["document_sha256"] == sha(first_state["doc"]), "creation_durable_history")
        mesh, _ = caller.json("/api/mesh", project=project)
        verify_plate(review, first_state, mesh, 40, 4, 8)
        anchor = store_anchor(application.store, project)
        replay, _ = caller.json("/api/parametric", request, project)
        review.check(replay == first and store_anchor(application.store, project) == anchor,
                     "creation_receipt_exact_replay")
        conflict = dict(request, overrides={"width": "60 * mm"})
        caller.reject("/api/parametric", conflict, project, status=409, error="idempotency_conflict")
        review.check(store_anchor(application.store, project) == anchor, "receipt_conflict_no_write")
        other.reject("/api/state", project=project, status=403)
        other.reject("/api/parametric", parameter_body(first_state, configuration="wide"),
                     project, status=403)
        review.check(store_anchor(application.store, project) == anchor, "other_user_no_write")
        changed, _ = caller.json("/api/parametric", parameter_body(first_state, configuration="wide"), project)
        wide_state, _ = caller.json("/api/state", project=project)
        review.receipt(changed, wide_state, project)
        wide_mesh, _ = caller.json("/api/mesh", project=project)
        verify_plate(review, wide_state, wide_mesh, 60, 6, 12)
        review.check(changed["mesh"]["document_sha256"] == changed["step"]["document_sha256"]
                     == sha(wide_state["doc"]) and wide_state["undo_depth"] == 2,
                     "candidate_artifacts_and_history")
        status, step, headers = caller.call("/api/step", project=project)
        review.check(status == 200 and isinstance(step, bytes) and step.startswith(b"ISO-10303-21;")
                     and headers.get("X-OpenCAD-Document-SHA256") == sha(wide_state["doc"])
                     and headers.get("X-OpenCAD-Revision") == str(wide_state["revision"]),
                     "actual_step_version_headers", bytes=len(step))
        (review.out / "plate-wide.step").write_bytes(step)
        from cadcore import docmodel
        from cadcore.kernel.subproc import SubprocessKernel
        from cadcore.kernel.features_step import make_step
        make_step(step_text=step.decode("ascii"))
        imported = docmodel.new_document()
        imported.features.append({"uuid": str(uuid4()), "kind": "import_step",
                                  "params": {"step_text": step.decode("ascii")}, "suppressed": False})
        kernel = SubprocessKernel(work_dir=str(review.out / "step-roundtrip"), keep_work_dir=True,
                                  extra_env={"TMPDIR": str(review.out), "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            shape = kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
            volume = kernel.mass_properties(shape).volume_nm3 / 1e18
            review.check(abs(volume - (1200 - math.pi * 36) * 12) < 1e-5,
                         "step_real_roundtrip_volume", volume_mm3=volume)
        finally:
            kernel.close()
        anchor = store_anchor(application.store, project)
        sentinel = review.out / "expression-execution-must-not-exist"
        for overrides, reason in (
                ({"radius": "11 * mm"}, "design_check_failed"),
                ({"width": "-1 * mm"}, "invalid_parameter"),
                ({"width": "hole_x * 2"}, "parameter_cycle"),
                ({"width": "__import__('pathlib').Path(" + repr(str(sentinel))
                            + ").write_text('must never execute')"}, "unsafe_expression"),
                ({"hole_x": "80 * mm"}, "hole_outside_profile")):
            malicious = parameter_body(wide_state, overrides=overrides)
            caller.reject("/api/parametric", malicious, project, status=422, error=reason)
            review.check(store_anchor(application.store, project) == anchor,
                         "rejected_candidate_zero_store_history_receipt_change", reason=reason)
            if reason == "unsafe_expression":
                review.check(not sentinel.exists(), "malicious_expression_has_no_filesystem_side_effect")
            review.check(application.store.read_receipt(
                project, malicious["request_id"], request_payload={**malicious, "route": "/api/parametric"}) is None,
                "rejection_has_no_success_receipt", reason=reason)
        caller.reject("/api/parametric", parameter_body(first_state, overrides={"width": "50 * mm"}),
                      project, status=409, error="revision_conflict")
        caller.reject("/api/parametric", parameter_body(wide_state, expected_document_sha256="0" * 64),
                      project, status=409, error="parametric_conflict")
        review.check(store_anchor(application.store, project) == anchor, "stale_cas_no_write")
        undone, _ = caller.json("/api/command", edit_body(wide_state, command="undo", args={}), project)
        undo_state, _ = caller.json("/api/state", project=project)
        review.check(undo_state["doc"] == first_state["doc"] and undo_state["redo_depth"] == 1
                     and undone["revision"] == wide_state["revision"] + 1, "undo_exact_L1_and_version")
        redone, _ = caller.json("/api/command", edit_body(undo_state, command="redo", args={}), project)
        redo_state, _ = caller.json("/api/state", project=project)
        review.check(redo_state["doc"] == wide_state["doc"] and redo_state["redo_depth"] == 0
                     and redone["revision"] == undo_state["revision"] + 1, "redo_exact_L1_and_version")
        save_response, _ = caller.json("/api/save", edit_body(redo_state), project)
        saved_state, _ = caller.json("/api/state", project=project)
        review.check(save_response["saved"] is True and saved_state["doc"] == redo_state["doc"],
                     "explicit_save_preserves_exact_document")
        barrier = threading.Barrier(2)
        attempts = [parameter_body(saved_state, overrides={"width": expression})
                    for expression in ("50 * mm", "55 * mm")]

        def send(payload):
            independent = Caller(http, review, caller.cookie)
            barrier.wait(timeout=10)
            return independent.call("/api/parametric", payload, project)

        before_concurrency = store_anchor(application.store, project)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, request) for request in attempts]
            outcomes = [future.result() for future in futures]
        review.check(sorted(outcome[0] for outcome in outcomes) == [200, 409],
                     "real_concurrent_CAS_exactly_one_winner",
                     statuses=[outcome[0] for outcome in outcomes])
        winner = next(i for i, outcome in enumerate(outcomes) if outcome[0] == 200)
        latest, _ = caller.json("/api/state", project=project)
        review.receipt(outcomes[winner][1], latest, project)
        after_concurrency = store_anchor(application.store, project)
        review.check(latest["doc"] == outcomes[winner][1]["doc"]
                     and latest["revision"] == saved_state["revision"] + 1
                     and after_concurrency["counts"]["receipts"] == before_concurrency["counts"]["receipts"] + 1,
                     "concurrent_winner_durable_and_loser_no_receipt")
        late_replay, _ = caller.json("/api/parametric", request, project)
        review.check(late_replay == first and store_anchor(application.store, project) == after_concurrency,
                     "old_receipt_replay_does_not_roll_back_head")
        saved = {"state": latest, "project": project, "anchor": after_concurrency,
                 "request": attempts[winner], "response": outcomes[winner][1]}
        review.check(initial_anchor["counts"] == {"snapshots": 1, "receipts": 0}, "fixture_initial_store_empty")
    # Real controllers and HTTP server reopen; no test-side Document replacement.
    with live_service(modules, data, review) as (http, application):
        caller = login(http, review, account_names[0], credentials[0])
        reopened, _ = caller.json("/api/state", project=saved["project"])
        review.check(reopened["doc"] == saved["state"]["doc"]
                     and reopened["revision"] == saved["state"]["revision"]
                     and reopened["sha256"] == saved["state"]["sha256"],
                     "real_service_reopen_exact_head")
        parameters, _ = caller.json("/api/parametric", project=saved["project"])
        review.check(parameters["document_sha256"] == sha(reopened["doc"]), "reopen_parameters_bound")
        mesh, _ = caller.json("/api/mesh", project=saved["project"])
        width = int(next(p for p in parameters["parameters"] if p["name"] == "width")["value"]["value"]) / 1e6
        verify_plate(review, reopened, mesh, width, width / 10, width / 5)
        replay, _ = caller.json("/api/parametric", saved["request"], saved["project"])
        review.check(replay == saved["response"]
                     and store_anchor(application.store, saved["project"]) == saved["anchor"],
                     "receipt_survives_real_service_reopen")
        opened, _ = caller.json("/api/open", edit_body(reopened, doc=reopened["doc"]), saved["project"])
        opened_state, _ = caller.json("/api/state", project=saved["project"])
        review.check(opened["doc"] == reopened["doc"] and opened_state["doc"] == reopened["doc"]
                     and opened_state["undo_depth"] == opened_state["redo_depth"] == 0,
                     "real_open_canonical_doc_history_contract")
        second, _ = caller.json("/api/parametric", parameter_body(opened_state), saved["project"])
        review.check(second["document_sha256"] == sha(reopened["doc"]), "rebuild_after_real_open_same_docSHA")
        final_state, _ = caller.json("/api/state", project=saved["project"])
        review.receipt(second, final_state, saved["project"])
        review.check(final_state["undo_depth"] == final_state["redo_depth"] == 0,
                     "unchanged_rebuild_does_not_add_document_history")


def scan_private_values(review, roots):
    files, matches = 0, []
    needles = [value.encode() for value in review.sensitive if value]
    if not needles:
        raise ValueError("Private-value scan has no ephemeral credentials to check")
    for root in roots:
        for parent, directories, names in os.walk(root, followlinks=False):
            if any((Path(parent) / name).is_symlink() for name in directories):
                raise ValueError("Linked private evidence directory")
            for name in names:
                path = Path(parent) / name
                value = regular_bytes(path)
                files += 1
                if any(needle in value for needle in needles):
                    matches.append(str(path))
    review.check(files > 0 and not matches, "ephemeral_credentials_absent_from_evidence_and_database",
                 scanned_files=files, matching_paths=matches)


class SelfTests(unittest.TestCase):
    def tetrahedron(self):
        return {"solid_count": 1, "vertices_mm": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "triangles": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}

    def test_mesh_independent_positive_and_open_negative(self):
        mesh = self.tetrahedron()
        self.assertAlmostEqual(mesh_measure(mesh), 1 / 6)
        mesh["triangles"].pop()
        with self.assertRaises(ValueError):
            mesh_measure(mesh)

    def test_mesh_nonfinite_and_invalid_index_negative(self):
        for change in ("nonfinite", "index", "empty"):
            mesh = self.tetrahedron()
            if change == "nonfinite":
                mesh["vertices_mm"][0][0] = float("nan")
            elif change == "index":
                mesh["triangles"][0][0] = True
            else:
                mesh["triangles"] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                mesh_measure(mesh)

    def test_sha_full_tail_binding_and_empty_negative(self):
        value = sha("source")
        self.assertTrue(valid_sha(value))
        binding = {"sha256": value, "route_ready": True}
        frozen_source(binding, value)
        with self.assertRaises(ValueError):
            frozen_source(binding, value[:-1] + ("0" if value[-1] != "0" else "1"))
        with self.assertRaises(ValueError):
            frozen_source({**binding, "route_ready": False}, value)
        self.assertFalse(valid_sha(""))
        self.assertFalse(valid_sha("g" * 64))

    def test_source_diff_detects_tail_add_remove_without_vacuum(self):
        initial = {"kept.py": {"sha256": sha("before"), "bytes": 6},
                   "removed.py": {"sha256": sha("old"), "bytes": 3}}
        changed = {**initial, "kept.py": {"sha256": sha("after"), "bytes": 6},
                   "added.py": {"sha256": sha("new"), "bytes": 3}}
        del changed["removed.py"]
        self.assertEqual(source_changes(initial, changed),
                         {"added": ["added.py"], "removed": ["removed.py"], "changed": ["kept.py"]})
        self.assertEqual(source_changes(initial, initial), {"added": [], "removed": [], "changed": []})

    def test_credentials_recursive_redaction_and_no_hash(self):
        review = Review(Path("/not-used"))
        review.sensitive.append("test only ephemeral value")
        value = review.redact({"detail": "cause test only ephemeral value",
                               "nested": [{"Set-Cookie": "other"}], "sha": "noncredential"})
        self.assertNotIn("test only ephemeral value", canonical(value))
        self.assertEqual(value["nested"][0]["Set-Cookie"], "[REDACTED]")
        self.assertEqual(value["sha"], "noncredential")

    def test_candidate_schema_preserves_CAS_and_new_request(self):
        state = {"doc": "{}\n", "revision": 3, "sha256": sha("snapshot")}
        first, second = parameter_body(state), parameter_body(state)
        self.assertEqual(first["expected_document_sha256"], sha(state["doc"]))
        self.assertEqual(first["expected_sha"], state["sha256"])
        self.assertNotEqual(first["request_id"], second["request_id"])
        created = parameter_body(state, action="create_plate")
        self.assertEqual(created["action"], "create_plate")
        stale = parameter_body(state, expected_document_sha256="0" * 64)
        self.assertEqual(stale["expected_document_sha256"], "0" * 64)

    def test_local_network_root_and_source_escape_rejected(self):
        with self.assertRaises(ValueError):
            safe_external(Path("/3fs/research3fs/data/tmp/probe"), ROOT, local=True)
        with self.assertRaises(ValueError):
            safe_external(ROOT / "runtime", ROOT)

    def test_nonregular_source_and_link_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            regular = directory / "source.py"
            regular.write_bytes(b"known source\n")
            self.assertEqual(regular_bytes(regular), b"known source\n")
            with self.assertRaises(ValueError):
                regular_bytes(directory)
            linked = directory / "linked.py"
            linked.symlink_to(regular)
            with self.assertRaises(OSError):
                regular_bytes(linked)
            with self.assertRaises(ValueError):
                source_binding(directory)

    def test_audit_concurrency_counts_and_redacts(self):
        with tempfile.TemporaryDirectory() as name:
            review = Review(Path(name))
            review.sensitive.append("ephemeral private sample")
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda i: review.check(True, "count", sequence=i,
                                                    detail=review.sensitive[0]), range(24)))
            entries = [json.loads(line) for line in (Path(name) / "audit.jsonl").read_text().splitlines()]
            self.assertEqual(review.assertions, 24)
            self.assertEqual(sorted(e["sequence"] for e in entries), list(range(24)))
            self.assertNotIn(review.sensitive[0], canonical(entries))

    def test_failure_log_double_fault_preserves_primary(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "failure.log").mkdir()
            review, report = Review(directory), {"verdict": "PASS"}
            remember_failure(report, review, ValueError("primary rebuild failure"))
            remember_failure(report, review, OSError("secondary source read failure"))
            self.assertEqual(report["verdict"], "FAIL")
            self.assertIn("primary rebuild failure", report["first_cause"])
            self.assertNotIn("secondary source read failure", report["first_cause"])
            self.assertIn("secondary source read failure", canonical(report["secondary_failures"]))

    def test_private_value_scan_positive_and_negative(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            review = Review(root)
            secret = secrets.token_urlsafe(24)
            review.sensitive.append(secret)
            (root / "safe.log").write_bytes(b"ordinary noncredential evidence")
            scan_private_values(review, (root,))
            (root / "unsafe.log").write_bytes(secret.encode())
            with self.assertRaises(AssertionError):
                scan_private_values(review, (root,))

    def test_receipt_cross_project_revision_and_document_negative(self):
        with tempfile.TemporaryDirectory() as name:
            review = Review(Path(name))
            state = {"project_id": "p1", "revision": 4, "sha256": sha("snapshot"), "doc": "{}\n"}
            response = {**state, "document_sha256": sha(state["doc"])}
            review.receipt(response, state, "p1")
            for field, bad in (("project_id", "p2"), ("revision", 3),
                               ("sha256", sha("wrong")), ("document_sha256", sha("wrong"))):
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    review.receipt({**response, field: bad}, state, "p1")

    def test_nonjson_success_status_is_loud_assertion_not_attribute_error(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            caller = Caller(SimpleNamespace(server_address=("127.0.0.1", 1)), Review(Path(name)))
            with patch.object(caller, "call", return_value=(200, b"unexpected non-JSON body", {})):
                with self.assertRaisesRegex(AssertionError, "http_success"):
                    caller.json("/api/state")

    def test_owned_source_hygiene_real_scanner_with_positive_control(self):
        from bootstrap_from_infra import scan_bytes
        for relative in ("tools/parametric_private_review.py", "docs/design/parametric-private-review.md"):
            scan_bytes(relative, regular_bytes(ROOT / relative))
        forbidden = ("pass" + "word = '" + secrets.token_urlsafe(24) + "'").encode()
        with self.assertRaises(ValueError):
            scan_bytes("isolated-positive-control", forbidden)

    def test_namespace_runtime_local_positive_foreign_and_empty_negative(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            source = Path(name)
            app = source / "oss-alternatives/apps/opencad"
            core = source / "oss-alternatives/packages/cadcore/cadcore"
            app.mkdir(parents=True)
            core.mkdir(parents=True)
            real = core / "parametric.py"
            real.write_bytes(b"known isolated module\n")
            fake = {"cadcore.parametric": SimpleNamespace(__file__=str(real)),
                    "cadcore.i18n": SimpleNamespace(__path__=[str(core)])}
            with patch.dict(sys.modules, fake, clear=True):
                result = runtime_bindings(source)
                self.assertEqual(result["cadcore.i18n"], [core.relative_to(source).as_posix()])
                for locations in ([], [str(source)]):
                    with self.subTest(locations=locations):
                        sys.modules["cadcore.i18n"].__path__ = locations
                        with self.assertRaises(ValueError):
                            runtime_bindings(source)


def worker(source, out, runtime, expected):
    review = Review(out)
    before = None
    report = {"verdict": "FAIL", "production_operations": False,
              "scope": "real_private_HTTP_DB_FreeCAD",
              "tool_sha256": sha(regular_bytes(Path(__file__))), "source_drift": "NOT_MEASURED"}
    started = time.monotonic()
    data = None
    try:
        before = source_binding(source)
        report["source_before"] = before
        frozen_source(before, expected)
        review.check(True, "frozen_source_preflight")
        modules = import_product(source)
        runtime = safe_external(runtime, source, local=True)
        runtime.mkdir(parents=True, exist_ok=True)
        data = Path(tempfile.mkdtemp(prefix="parametric-private-", dir=runtime))
        review.check(not any(data.iterdir()), "fresh_isolated_local_database_root")
        report["runtime_root"] = str(data)
        workflow(modules, data, review)
        report["runtime_bindings"] = runtime_bindings(source)
        scan_private_values(review, (out, data))
        report["verdict"] = "PASS"
    except Exception as exc:
        remember_failure(report, review, exc)
    finally:
        try:
            after = source_binding(source)
            report.update(source_after_sha256=after["sha256"],
                          source_drift=before is None or before["manifest"] != after["manifest"])
            if before is not None:
                report["source_changes"] = source_changes(before["manifest"], after["manifest"])
            if report["source_drift"]:
                remember_failure(report, review, ValueError("Selected source changed during real review"))
        except Exception as exc:
            remember_failure(report, review, exc)
        report.update(assertions=review.assertions, checks=review.checks, wall_s=time.monotonic() - started)
        if report["source_drift"] is not False or review.assertions == 0:
            report["verdict"] = "FAIL"
        (out / "result.json").write_text(json.dumps(review.redact(report), indent=2, sort_keys=True) + "\n")
    print(f"PARAMETRIC_PRIVATE_REVIEW verdict={report['verdict']} assertions={review.assertions} "
          f"source_drift={report['source_drift']} evidence={out}", flush=True)
    return 0 if report["verdict"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--expect-source-sha")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--compare-evidence", type=Path,
                        help="Read a prior source-binding.json and report current differences only")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    source = args.source_root.resolve(strict=True)
    if args.worker:
        if not valid_sha(args.expect_source_sha):
            parser.error("Worker requires a full frozen source SHA")
        return worker(source, args.out.resolve(strict=True), args.runtime_root, args.expect_source_sha)
    # The external 3FS deployment alias is resolved once before creating evidence.
    out_parent = args.out.resolve()
    if out_parent.is_relative_to(source):
        parser.error("Evidence must be outside selected source")
    out = out_parent / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    if args.selftest:
        from contextlib import redirect_stderr
        with (out / "tests.log").open("x") as log, redirect_stderr(log):
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(SelfTests))
        report = {"verdict": "PASS" if result.wasSuccessful() else "FAIL",
                  "scope": "review_tool_only", "tests": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors),
                  "tool_sha256": sha(regular_bytes(Path(__file__)))}
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"PARAMETRIC_PRIVATE_SELFTEST verdict={report['verdict']} tests={result.testsRun} evidence={out}")
        return 0 if result.wasSuccessful() else 1
    binding = source_binding(source)
    (out / "source-binding.json").write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n")
    if args.compare_evidence:
        old = json.loads(regular_bytes(args.compare_evidence / "source-binding.json"))
        if sha(canonical(old["manifest"])) != old["sha256"]:
            raise ValueError("Prior evidence source binding is corrupt")
        changes = source_changes(old["manifest"], binding["manifest"])
        report = {"verdict": "SAME" if not any(changes.values()) else "DRIFT",
                  "acceptance": "NOT_RUN", "source_before_sha256": old["sha256"],
                  "source_after_sha256": binding["sha256"], "source_changes": changes,
                  "tool_sha256": sha(regular_bytes(Path(__file__)))}
        (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"PARAMETRIC_PRIVATE_COMPARE verdict={report['verdict']} changes={changes} evidence={out}")
        return 0 if report["verdict"] == "SAME" else 2
    if args.preflight:
        report = {"verdict": "READY" if binding["route_ready"] else "NOT_READY",
                  "acceptance": "NOT_RUN", "source_sha256": binding["sha256"],
                  "files": len(binding["manifest"]), "tool_sha256": sha(regular_bytes(Path(__file__)))}
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"PARAMETRIC_PRIVATE_PREFLIGHT verdict={report['verdict']} "
              f"source_sha256={binding['sha256']} files={report['files']} evidence={out}")
        return 0 if binding["route_ready"] else 2
    if not valid_sha(args.expect_source_sha) or binding["sha256"] != args.expect_source_sha:
        (out / "result.json").write_text(json.dumps({
            "verdict": "FAIL", "acceptance": "NOT_RUN", "first_cause": "Frozen source SHA absent or mismatched"}) + "\n")
        print("PARAMETRIC_PRIVATE_REVIEW verdict=FAIL reason=frozen_source_mismatch evidence=" + str(out))
        return 2
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
               "--source-root", str(source), "--out", str(out), "--runtime-root", str(args.runtime_root),
               "--expect-source-sha", args.expect_source_sha]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(out), PYTHONPATH="")
    with (out / "worker.log").open("xb") as log:
        child = subprocess.run(command, cwd=out, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    report_path = out / "result.json"
    if not report_path.is_file():
        report_path.write_text(json.dumps({"verdict": "FAIL", "child_rc": child.returncode,
                                          "first_cause": "Worker failed before result; see worker.log"}) + "\n")
    report = json.loads(report_path.read_text())
    passed = child.returncode == 0 and report["verdict"] == "PASS" and report.get("assertions", 0) > 0
    print(f"PARAMETRIC_PRIVATE_REVIEW verdict={'PASS' if passed else 'FAIL'} "
          f"assertions={report.get('assertions', 'NOT_CAPTURED')} child_rc={child.returncode} evidence={out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
