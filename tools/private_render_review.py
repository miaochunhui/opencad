#!/usr/bin/env python3
"""Read-only source review with isolated auth/project/FreeCAD/EGL fixtures.

Never connect to a deployed service, load existing accounts or patch a gateway.
--http-db exercises the original secure CLI with new local durable fixtures;
evidence stays on 3FS. Historical --handler-only is explicitly narrower.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
from http.cookies import SimpleCookie
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import traceback
import zlib

ROOT = Path(__file__).resolve().parents[1]
APP_REL = Path("oss-alternatives/apps/opencad")
BACKGROUND = bytes((245, 246, 248, 255))
SHA_HEADERS = ("Snapshot", "Document", "Model", "Geometry", "Frame", "PNG")
LOG = logging.getLogger("private-render-review")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def write_json(path, value):
    with path.open("x", encoding="utf8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def source_state(roots):
    paths = {Path(__file__).resolve(), ROOT / "docs/research/official-sources.json",
             ROOT / "docs/design/private-render-review.md"}
    for root in roots:
        for folder in (root / APP_REL, root / "oss-alternatives/packages"):
            for suffix in ("*.py", "*.js", "*.css"):
                paths.update(folder.rglob(suffix))
    return {str(path): digest(path.read_bytes()) for path in sorted(paths) if path.is_file()}


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load source module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def decode_png(png, width, height):
    """Independent strict PNG framing/CRC and bounded RGBA8 inflation."""
    if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) > 2 * 1024 * 1024:
        raise AssertionError("PNG magic/byte budget invalid")
    offset, seen, compressed = 8, [], bytearray()
    while offset < len(png):
        if len(png) - offset < 12:
            raise AssertionError("truncated PNG chunk")
        size = struct.unpack(">I", png[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(png):
            raise AssertionError("PNG chunk exceeds response")
        kind, body = png[offset + 4:offset + 8], png[offset + 8:end - 4]
        crc = struct.unpack(">I", png[end - 4:end])[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise AssertionError("PNG CRC mismatch")
        seen.append(kind)
        if kind == b"IHDR":
            if body != struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0):
                raise AssertionError("PNG dimensions/RGBA8 mismatch")
        elif kind == b"IDAT":
            compressed.extend(body)
        elif kind != b"IEND" or body:
            raise AssertionError("unexpected PNG chunk")
        offset = end
    if seen != [b"IHDR", b"IDAT", b"IEND"]:
        raise AssertionError("PNG chunk order mismatch")
    expected = (width * 4 + 1) * height
    inflater = zlib.decompressobj()
    raw = inflater.decompress(compressed, expected + 1)
    if (len(raw) != expected or not inflater.eof or inflater.unused_data
            or inflater.unconsumed_tail):
        raise AssertionError("PNG inflation size/termination mismatch")
    stride = width * 4 + 1
    if any(raw[y * stride] != 0 for y in range(height)):
        raise AssertionError("unsupported PNG filter")
    return b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


def projected_geometry(mesh, camera, width, height):
    """Independently derive the documented orthographic geometry fingerprint."""
    points, faces = [], []
    for solid in mesh["solids"]:
        offset = len(points)
        faces.extend([[offset + i for i in face] for face in solid["triangles"]])
        points.extend(solid["vertices_mm"])
    projected = []
    for x, y, z in points:
        if camera == "front":
            projected.append((x, z, -y))
        elif camera == "top":
            projected.append((x, y, -z))
        elif camera == "right":
            projected.append((y, z, -x))
        elif camera == "isometric":
            projected.append(((x - y) / math.sqrt(2), (x + y - 2 * z) / math.sqrt(6),
                              (x + y + z) / math.sqrt(3)))
        else:
            raise AssertionError("unsupported camera")
    lows = [min(p[i] for p in projected) for i in range(3)]
    highs = [max(p[i] for p in projected) for i in range(3)]
    scale = max(highs[i] - lows[i] for i in range(3))
    vertices = [[(p[i] - (lows[i] + highs[i]) / 2) * 1.6 / scale
                 for i in range(3)] for p in projected]
    value = {"vertices": vertices, "triangles": faces,
             "colors": [[32, 140, 200]] * len(faces), "width": width, "height": height}
    return digest(canonical(value))


def verify_reply(reply, head, mesh, options):
    if reply.status != 200 or reply.content_type != "image/png":
        raise AssertionError(f"expected PNG success, observed {reply.status}/{reply.content_type}")
    headers = reply.headers
    for name in SHA_HEADERS:
        value = headers.get(f"X-OpenCAD-{name}-SHA256", "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise AssertionError(f"missing/invalid {name} SHA header")
    document_sha = digest(head["snapshot"]["doc"].encode("utf8"))
    expected = {
        "X-OpenCAD-Revision": str(head["revision"]),
        "X-OpenCAD-Snapshot-SHA256": head["sha256"],
        "X-OpenCAD-Document-SHA256": document_sha,
        "X-OpenCAD-Model-SHA256": document_sha,
        "X-OpenCAD-Geometry-SHA256": projected_geometry(
            mesh, options["camera"], options["width"], options["height"]),
        "X-OpenCAD-Render-Camera": options["camera"], "Cache-Control": "no-store",
    }
    for key, value in expected.items():
        if headers.get(key) != value:
            raise AssertionError(f"render header mismatch: {key}")
    if document_sha == head["sha256"]:
        raise AssertionError("fixture must distinguish document and snapshot SHA")
    pixels = decode_png(reply.payload, options["width"], options["height"])
    if digest(reply.payload) != headers["X-OpenCAD-PNG-SHA256"]:
        raise AssertionError("PNG SHA header mismatch")
    if digest(pixels) != headers["X-OpenCAD-Frame-SHA256"]:
        raise AssertionError("RGBA SHA header mismatch")
    width, height = options["width"], options["height"]
    center = ((height // 2) * width + width // 2) * 4
    if pixels[:4] != BACKGROUND or pixels[-4:] != BACKGROUND:
        raise AssertionError("analytic background corner mismatch")
    if pixels[center:center + 4] == BACKGROUND:
        raise AssertionError("analytic solid center absent")
    if any(pixels[i + 3] != 255 for i in range(0, len(pixels), 4)):
        raise AssertionError("unexpected alpha")
    if float(headers["X-OpenCAD-Render-Elapsed-S"]) <= 0:
        raise AssertionError("invalid render elapsed header")
    backend, status = headers.get("X-OpenCAD-Render-Backend"), headers.get("X-OpenCAD-Render-Status")
    if status == "CPU_FALLBACK":
        if (options["backend"] != "gpu" or not options["allow_cpu_fallback"]
                or backend != "cpu" or headers.get("X-OpenCAD-Hardware") != "false"
                or not headers.get("X-OpenCAD-Fallback-Reason")):
            raise AssertionError("unauthorized/undisclosed CPU fallback")
    elif status != "OK" or backend != options["backend"] or headers.get("X-OpenCAD-Fallback-Reason"):
        raise AssertionError("render backend/status contract mismatch")
    return pixels


def pixel_validator_controls(reply, head, mesh, options):
    """The acceptance ruler must actually reject corrupt pixels and metadata."""
    controls = {}
    for label, mutate in (
        ("revision", lambda r: r.headers.update({"X-OpenCAD-Revision": "999"})),
        ("snapshot", lambda r: r.headers.update({"X-OpenCAD-Snapshot-SHA256": "0" * 64})),
        ("frame", lambda r: r.headers.update({"X-OpenCAD-Frame-SHA256": "0" * 64})),
        ("png_crc", lambda r: setattr(r, "payload", r.payload[:-1] + bytes([r.payload[-1] ^ 1]))),
    ):
        corrupted = copy.deepcopy(reply)
        mutate(corrupted)
        try:
            verify_reply(corrupted, head, mesh, options)
        except AssertionError as exc:
            controls[label] = {"verdict": "REJECTED", "reason": str(exc)}
        else:
            raise AssertionError(f"independent ruler accepted corrupt {label}")
    return controls


def store_state(store):
    # Query the whole logical database, including receipts, not only current head.
    if isinstance(store, FixtureHeads):
        return {"scope": "IN_MEMORY_FIXTURE_NOT_DB", "sha256": digest(canonical(store.heads)),
                "counts": {"fixture_heads": len(store.heads)}}
    with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not names or any(not name.replace("_", "").isalnum() for name in names):
            raise AssertionError("invalid/empty fixture schema")
        rows = {name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
                for name in names}
    return {"sha256": digest(canonical(rows)), "counts": {name: len(v) for name, v in rows.items()}}


class FixtureHeads:
    """Explicit handler-only input source, never evidence of durable ProjectStore."""

    def __init__(self):
        self.heads = {}

    def create_project(self, owner, *, title, request_id, payload):
        project = "fixture-" + request_id
        if project in self.heads:
            raise AssertionError("duplicate fixture head")
        self.heads[project] = {"project_id": project, "owner": owner, "title": title,
                               "revision": 1, "sha256": digest(canonical(payload)),
                               "snapshot": copy.deepcopy(payload)}
        return self.read_head(project)

    def read_head(self, project):
        if project not in self.heads:
            raise AssertionError("unknown fixture project")
        return copy.deepcopy(self.heads[project])

    def close(self):
        pass


class ObservedWorkspace:
    def __init__(self, real, counts):
        self.real, self.counts = real, counts

    def __getattr__(self, name):
        return getattr(self.real, name)

    def mesh(self):
        self.counts["mesh"] += 1
        return self.real.mesh()


class ObservedRenderer:
    def __init__(self, real, counts):
        self.real, self.counts = real, counts

    def render(self, payload):
        self.counts["render"] += 1
        return self.real.render(payload)


@contextmanager
def secure_cli(app, runtime, out, label, flags, report):
    """Launch only a new owned loopback process; never attach to a service."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-B", str(app / "secure_web.py"),
               "--host", "127.0.0.1", "--port", str(port),
               "--data-root", str(runtime), "--public-origin", origin, *flags]
    dependency = app.parents[2]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(out),
               OPENCAD_CADCORE_ROOT=str(dependency / "oss-alternatives/packages/cadcore"),
               PYTHONPATH=os.pathsep.join(str(path) for path in (
                   app, dependency / "oss-alternatives/packages/cadcore",
                   dependency / "oss-alternatives/packages/dxf",
                   dependency / "oss-alternatives/packages/vault")))
    log_path = out / (label + "-secure-cli.log")
    entry = {"name": label, "argv": command, "log": str(log_path),
             "started_at": stamp(), "patched": False}
    report.setdefault("cli_processes", []).append(entry)
    with log_path.open("xb") as log:
        process = subprocess.Popen(command, cwd=dependency, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            marker = f"OPENCAD_PRIVATE_WEB verdict=READY port={port}"
            while marker not in log_path.read_text(errors="replace"):
                if process.poll() is not None:
                    raise RuntimeError(f"{label}: secure CLI exited rc={process.returncode}; {log_path}")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{label}: secure CLI READY absent; {log_path}")
                time.sleep(0.05)
            entry.update(ready_at=stamp(), origin=origin)
            yield port, origin
        finally:
            # These are our Popen children, not existing service PIDs.
            if process.poll() is None:
                process.terminate()
            try:
                entry["rc"] = process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                entry["rc"] = process.wait(timeout=5)
                entry["shutdown_error"] = "owned CLI required kill after SIGTERM"
                raise RuntimeError(f"{label}: graceful fixture shutdown failed") from exc
            entry.update(finished_at=stamp(), log_sha256=digest(log_path.read_bytes()))
            if entry["rc"] != 0:
                raise RuntimeError(f"{label}: owned CLI exit rc={entry['rc']}; {log_path}")


def run_http_db(args, out, report):
    """Real HTTP cookie login/create/open/mesh/render, with unchanged product."""
    dependency = args.gateway_root.resolve()
    app = dependency / APP_REL
    required = ("access_control.py", "project_store.py", "project_gateway.py",
                "secure_web.py", "server.py", "industrial_render.py", "render_service.py")
    for name in required:
        if not (app / name).is_file():
            raise FileNotFoundError(f"read-only private source absent: {app / name}")
    for path in (app, dependency / "oss-alternatives/packages/cadcore",
                 dependency / "oss-alternatives/packages/dxf",
                 dependency / "oss-alternatives/packages/vault"):
        sys.path.insert(0, str(path))
    # Unlike handler-only review, never replace the integrated renderer module.
    import access_control
    import project_store
    import industrial_render as adapter
    from cadcore.ops import sw_drawings
    from server import _load_cadcore
    for module in (access_control, project_store, adapter, sys.modules["render_service"],
                   sys.modules["server"]):
        if Path(module.__file__).resolve() != app / (module.__name__ + ".py"):
            raise RuntimeError(f"foreign runtime import: {module.__name__}")
    report["runtime_origins"] = {
        name: str(Path(sys.modules[name].__file__).resolve())
        for name in ("access_control", "project_store", "industrial_render", "render_service", "server")
    }
    runtime_parent = args.runtime_root.absolute()
    approved = Path("/data/dev/opencad-private-fixtures")
    for part in (runtime_parent, *runtime_parent.parents):
        if part.is_symlink():
            raise ValueError("local fixture root cannot contain symlinks")
    if runtime_parent != approved or not runtime_parent.is_dir():
        raise ValueError("use the existing /data/dev/opencad-private-fixtures parent only")
    runtime = runtime_parent / ("research-gpu-" + out.name + "-" + secrets.token_hex(6))
    runtime.mkdir(mode=0o700, exist_ok=False)
    report.update(runtime_root=str(runtime), head_source="DURABLE_PROJECT_DB",
                  durable_db="NOT_TESTED", cookie_login="NOT_TESTED",
                  product_patched=False, production_speedup="NOT_ESTABLISHED")
    write_json(out / "fixture-manifest.json", {
        "runtime_root": str(runtime), "evidence_root": str(out),
        "new_owned_fixture": True, "existing_accounts_read": False,
        "local_storage_reason": "ProjectStore rejects network/FUSE SQLite WAL",
        "recipe": str(dependency / "tools/private_web_acceptance.py"),
        "at": stamp(),
    })
    credentials, cookies, principals = {}, {}, {}
    secret_bytes = []
    setup_auth = access_control.AuthController(runtime / "accounts", allow_registration=True)
    try:
        for name in ("alice", "bob"):
            credentials[name] = secrets.token_hex(24)
            secret_bytes.append(credentials[name].encode())
            setup_auth.register(name, credentials[name])
    finally:
        setup_auth.close()
    checks = report["checks"] = []
    requests = report["http_requests"] = []
    heads, meshes, frames = {}, {}, {}
    core = _load_cadcore()
    store = project_store.ProjectStore(runtime / "projects")

    def record(label, **facts):
        checks.append({"name": label, "verdict": "PASS", "at": stamp(), **facts})
        LOG.info("CHECK verdict=PASS name=%s", label)

    def request(port, origin, label, method, route, body=None, user=None, project=None,
                cross_origin=False, forged=False, csrf=True):
        headers = {"Origin": "https://example.invalid" if cross_origin else origin}
        if csrf:
            headers["X-OpenCAD-Request"] = "1"
        if user:
            headers["Cookie"] = ("opencad_session=forged-session" if forged else cookies[user])
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        start = time.perf_counter()
        try:
            connection.request(method, route, canonical(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AssertionError("HTTP response byte budget exceeded")
            response_headers = dict(response.getheaders())
            entry = {"name": label, "at": stamp(), "method": method, "path": route,
                     "user": user, "project_id": project, "status": response.status,
                     "bytes": len(raw), "http_elapsed_s": time.perf_counter() - start}
            # Login body/cookie/token never enters logs, hashes or evidence.
            if route != "/api/auth/login":
                entry.update(response_sha256=digest(raw), headers={
                    key: value for key, value in response_headers.items()
                    if key.lower() != "set-cookie"})
            requests.append(entry)
            return adapter.RenderReply(response.status, raw,
                                       response_headers.get("Content-Type", "").split(";")[0],
                                       response_headers)
        finally:
            connection.close()

    def json_reply(reply, status):
        if reply.status != status or reply.content_type != "application/json":
            raise AssertionError(f"expected HTTP JSON {status}, got {reply.status}/{reply.content_type}")
        return json.loads(reply.payload)

    def denial(port, origin, label, body, expected, code, *, user="alice", project=None,
               cross_origin=False, forged=False, csrf=True):
        reply = request(port, origin, label, "POST", "/api/render", body, user, project,
                        cross_origin, forged, csrf)
        if args.diagnostic and (reply.status != expected or
                                json.loads(reply.payload).get("error") != code):
            finding = {"name": label, "verdict": "FAIL", "at": stamp(),
                       "expected_status": expected, "observed_status": reply.status,
                       "expected_error": code, "observed": json.loads(reply.payload)}
            if reply.content_type != "application/json":
                raise AssertionError(f"{label}: diagnostic denial is not JSON")
            report.setdefault("findings", []).append(finding)
            checks.append(finding)
            LOG.error("CHECK verdict=FAIL name=%s expected=%s observed=%s",
                      label, expected, reply.status)
            return finding["observed"]
        value = json_reply(reply, expected)
        if value.get("ok") is not False or value.get("error") != code:
            raise AssertionError(f"{label}: wrong denial code {value.get('error')}")
        if any(key.startswith("X-OpenCAD-Frame") for key in reply.headers):
            raise AssertionError(f"{label}: denied request leaked a frame header")
        record(label, status=reply.status, error=value["error"], detail=value.get("detail"))
        return value

    def persist_state(label):
        state = store_state(store)
        write_json(out / (label + "-project-db.json"), state)
        return state

    def assert_head_http(port, origin, name, head):
        reply = request(port, origin, "state_" + name, "GET", "/api/state",
                        user=name, project=head["project_id"])
        state = json_reply(reply, 200)
        if (state.get("revision"), state.get("sha256"), state.get("project_id")) != (
                head["revision"], head["sha256"], head["project_id"]):
            raise AssertionError("real HTTP state is not the durable head")

    def frame(port, origin, label, name, options):
        head = heads[name]
        body = dict(options, expected_revision=head["revision"],
                    expected_sha=head["sha256"], request_id="read-only-" + label)
        reply = request(port, origin, label, "POST", "/api/render", body,
                        name, head["project_id"])
        pixels = verify_reply(reply, head, meshes[name], body)
        with (out / (label + ".png")).open("xb") as handle:
            handle.write(reply.payload)
        write_json(out / (label + "-headers.json"), reply.headers)
        return reply, pixels, body

    try:
        flags = ["--enable-gpu", "--cuda-device", str(args.cuda_device)] if args.require_gpu else []
        # Arms depend on the same persisted heads and cookies across CLI restarts.
        with secure_cli(app, runtime, out, "owner-opt-in", flags, report) as (port, origin):
            for name in ("alice", "bob"):
                reply = request(port, origin, "login_" + name, "POST", "/api/auth/login",
                                {"username": name, "password": credentials[name]})
                value = json_reply(reply, 200)
                cookie = SimpleCookie()
                cookie.load(reply.headers.get("Set-Cookie", ""))
                item = cookie.get("opencad_session")
                if (value.get("ok") is not True or item is None or not item["httponly"]
                        or item["samesite"] != "Strict" or item["path"] != "/"):
                    raise AssertionError("real HTTP login cookie contract invalid")
                cookies[name] = "opencad_session=" + item.value
                secret_bytes.append(item.value.encode())
                principals[name] = value["user"]["user_id"]
                record("http_login_cookie_" + name, httponly=True, samesite="Strict",
                       cookie_values_logged=False)
            credentials.clear()
            report["cookie_login"] = "PASS"
            for name, depth in (("alice", 20), ("bob", 10)):
                created = json_reply(request(
                    port, origin, "create_" + name, "POST", "/api/projects",
                    {"title": "isolated render " + name, "request_id": name + "-create"},
                    user=name), 201)
                doc = sw_drawings._sample_model()
                doc.features[0]["params"]["distance_nm"] = depth * 1_000_000
                text = core["docmodel"].document_to_l1(doc)
                opened = json_reply(request(port, origin, "open_" + name, "POST", "/api/open", {
                    "doc": text, "request_id": name + "-open",
                    "expected_revision": created["revision"], "expected_sha": created["sha256"],
                }, name, created["project_id"]), 200)
                head = store.read_head(created["project_id"])
                if (head["snapshot"]["doc"] != text or head["owner"] != principals[name]
                        or head["revision"] != 2 or opened["sha256"] != head["sha256"]
                        or digest(canonical(head["snapshot"])) != head["sha256"]):
                    raise AssertionError("HTTP import is not the exact persisted owner/revision/model")
                heads[name] = head
                write_json(out / (name + "-db-head.json"), head)
                assert_head_http(port, origin, name, head)
                mesh = json_reply(request(port, origin, "mesh_" + name, "GET", "/api/mesh",
                                          user=name, project=head["project_id"]), 200)
                if (mesh.get("revision"), mesh.get("sha256")) != (head["revision"], head["sha256"]):
                    raise AssertionError("HTTP mesh version is not the persisted head")
                meshes[name] = mesh
                write_json(out / (name + "-http-mesh.json"), mesh)
                if len(mesh["solids"]) != 1:
                    raise AssertionError("expected one real BRep solid")
                solid = mesh["solids"][0]
                extent = [max(p[i] for p in solid["vertices_mm"]) -
                          min(p[i] for p in solid["vertices_mm"]) for i in range(3)]
                if (len(solid["triangles"]) != 12 or any(abs(a - b) > 1e-7
                                                       for a, b in zip(extent, (60, 40, depth)))):
                    raise AssertionError("real HTTP FreeCAD geometry differs from analytic cuboid")
                record("http_db_real_geometry_" + name, extent_mm=extent, triangles=12,
                       revision=head["revision"], snapshot_sha256=head["sha256"])
            empty = json_reply(request(port, origin, "create_empty", "POST", "/api/projects",
                                      {"title": "empty negative", "request_id": "empty-create"},
                                      user="alice"), 201)
            before = persist_state("before-render")
            expected_counts = {"projects": 3, "snapshots": 5, "receipts": 2, "create_receipts": 3}
            if before["counts"] != expected_counts:
                raise AssertionError(f"fixture schema/counts mismatch: {before['counts']}")
            report["store_before"] = before
            options = {"camera": "isometric", "width": 128, "height": 128,
                       "backend": "cpu", "allow_cpu_fallback": False}
            for name in ("alice", "bob"):
                for camera in ("isometric", "front"):
                    body_options = dict(options, camera=camera)
                    cpu, pixels, cpu_body = frame(port, origin, name + "-" + camera + "-cpu",
                                                name, body_options)
                    if cpu.headers.get("X-OpenCAD-Hardware") != "false":
                        raise AssertionError("HTTP CPU response claims hardware")
                    frames[name, camera, "cpu"] = pixels
                    record("http_cpu_" + name + "_" + camera, headers=cpu.headers)
                    if name == "alice" and camera == "isometric":
                        report["ruler_negative_controls"] = pixel_validator_controls(
                            cpu, heads[name], meshes[name], cpu_body)
                    if args.require_gpu:
                        gpu_options = dict(body_options, backend="gpu")
                        gpu, pixels, _ = frame(port, origin, name + "-" + camera + "-gpu",
                                              name, gpu_options)
                        observed = gpu.headers.get("X-OpenCAD-Renderer", "").lower()
                        if (gpu.headers.get("X-OpenCAD-Hardware") != "true"
                                or "nvidia" not in observed or "rtx 3080" not in observed
                                or any(v in observed for v in
                                       ("swiftshader", "angle", "llvmpipe", "software"))):
                            raise AssertionError("integrated HTTP did not observe real NVIDIA RTX3080")
                        cpu_pixels = frames[name, camera, "cpu"]
                        mismatch = sum(pixels[i:i + 4] != cpu_pixels[i:i + 4]
                                       for i in range(0, len(pixels), 4))
                        center = ((128 // 2) * 128 + 128 // 2) * 4
                        if (mismatch > 0.02 * 128 * 128
                                or pixels[center:center + 4] != cpu_pixels[center:center + 4]):
                            raise AssertionError("HTTP GPU/CPU independent pixels disagree")
                        again, repeated, _ = frame(port, origin,
                                                  name + "-" + camera + "-gpu-repeat", name, gpu_options)
                        if repeated != pixels or again.headers["X-OpenCAD-Geometry-SHA256"] != gpu.headers[
                                "X-OpenCAD-Geometry-SHA256"]:
                            raise AssertionError("real repeated GPU geometry/frame drift")
                        frames[name, camera, "gpu"] = pixels
                        record("http_gpu_" + name + "_" + camera,
                               mismatched_pixels=mismatch, repeat_sha_equal=True, headers=gpu.headers)
            if frames["alice", "isometric", "cpu"] == frames["bob", "isometric", "cpu"]:
                raise AssertionError("different persisted projects produced identical frames")
            record("http_distinct_project_frames")
            report["hardware_stage"] = "PASS" if args.require_gpu else "NOT_TESTED"
            alice, bob = heads["alice"], heads["bob"]
            stolen = dict(options, backend="gpu" if args.require_gpu else "cpu",
                          expected_revision=alice["revision"], expected_sha=alice["sha256"])
            denial(port, origin, "cross_owner_bob_to_alice", stolen, 403, "project_forbidden",
                   user="bob", project=alice["project_id"])
            denial(port, origin, "cross_owner_alice_to_bob", stolen, 403, "project_forbidden",
                   project=bob["project_id"])
            denial(port, origin, "anonymous", stolen, 401, "missing_token",
                   user=None, project=alice["project_id"])
            denial(port, origin, "forged_cookie", stolen, 401, "invalid_token",
                   project=alice["project_id"], forged=True)
            denial(port, origin, "csrf_wrong_origin", stolen, 403, "cross_site_request",
                   project=alice["project_id"], cross_origin=True)
            denial(port, origin, "csrf_missing_marker", stolen, 403, "cross_site_request",
                   project=alice["project_id"], csrf=False)
            denial(port, origin, "missing_project", stolen, 400, "project_required")
            for label, body, status, code in (
                ("cross_project_sha", dict(stolen, expected_sha=bob["sha256"]), 409, "revision_conflict"),
                ("stale_revision", dict(stolen, expected_revision=1), 409, "revision_conflict"),
                ("external_url", dict(stolen, url="https://example.invalid/mesh"), 400, "invalid_render_input"),
                ("unauthorized_path", dict(stolen, path="/etc/passwd"), 400, "invalid_render_input"),
                ("client_mesh", dict(stolen, mesh={}), 400, "invalid_render_input"),
                ("client_cuda_device", dict(stolen, cuda_device=0), 400, "invalid_render_input"),
                ("client_shader", dict(stolen, shader="not-accepted"), 400, "invalid_render_input"),
                ("oversize_frame", dict(stolen, width=1024, height=1024), 400, "invalid_render_input"),
                ("unknown_camera", dict(stolen, camera="perspective"), 400, "invalid_render_input"),
            ):
                denial(port, origin, label, body, status, code, project=alice["project_id"])
            denial(port, origin, "empty_project", options, 422, "no_solid_feature",
                   project=empty["project_id"])
            if persist_state("after-opt-in") != before:
                raise AssertionError("render/denial changed logical DB or receipts")
            report["gateway_route"] = {"verdict": "CPU_GPU_HTTP_PASS" if args.require_gpu else "CPU_HTTP_PASS",
                                       "source": str(app / "project_gateway.py"), "patched": False}
        # Restarting the ORIGINAL CLI proves persistent heads/sessions, not a mock.
        arms = [("owner-disabled", [], "GPU disabled by service configuration")]
        if args.require_gpu:
            arms.insert(0, ("owner-missing-device", ["--enable-gpu", "--cuda-device", "63"],
                            "no NVIDIA EGL device for CUDA ordinal 63"))
        for label, flags, cause in arms:
            with secure_cli(app, runtime, out, label, flags, report) as (port, origin):
                assert_head_http(port, origin, "alice", heads["alice"])
                missing_body = dict(options, backend="gpu", expected_revision=alice["revision"],
                                    expected_sha=alice["sha256"])
                value = denial(port, origin, label + "_fail_loud", missing_body, 503,
                               "gpu_unavailable", project=alice["project_id"])
                if cause not in value.get("detail", ""):
                    raise AssertionError("real GPU denial lost first cause")
                reply, pixels, _ = frame(port, origin, label + "-explicit-fallback", "alice",
                                         dict(options, backend="gpu", allow_cpu_fallback=True))
                if (pixels != frames["alice", "isometric", "cpu"]
                        or reply.headers.get("X-OpenCAD-Render-Status") != "CPU_FALLBACK"
                        or cause not in reply.headers.get("X-OpenCAD-Fallback-Reason", "")):
                    raise AssertionError("explicit HTTP CPU fallback differs or hides cause")
                record(label + "_explicit_cpu_fallback", headers=reply.headers)
                if persist_state("after-" + label) != before:
                    raise AssertionError("fallback/restarted CLI changed logical project DB")
        store.close()
        store = project_store.ProjectStore(runtime / "projects")
        if persist_state("after-reopen") != before:
            raise AssertionError("reopened durable DB differs after read-only render")
        for name, head in heads.items():
            if store.read_head(head["project_id"]) != head:
                raise AssertionError("persisted head differs after original CLI restarts")
        record("durable_db_heads_receipts_unchanged_across_cli_restarts", counts=before["counts"])
        report.update(store_after=store_state(store), durable_db="PASS",
                      private_chain="REAL_COOKIE_DB_HTTP_FAIL" if report.get("findings") else
                      "REAL_COOKIE_DB_HTTP_PASS", verdict="FAIL" if report.get("findings") else "PASS")
        report["check_counts"] = {
            verdict: sum(check["verdict"] == verdict for check in checks)
            for verdict in ("PASS", "FAIL")}
        artifact_files = [path for path in out.rglob("*") if path.is_file()]
        if not artifact_files:
            raise AssertionError("empty credential evidence scan")
        for path in artifact_files:
            raw = path.read_bytes()
            if any(value in raw for value in secret_bytes):
                raise AssertionError(f"fixture secret found in evidence: {path.name}")
        if any(value in canonical(report) for value in secret_bytes):
            raise AssertionError("fixture secret found in report")
        report["credential_evidence_scan"] = {
            "verdict": "PASS", "files": len(artifact_files), "matches": 0,
            "scope": "exact generated fixture passwords and session cookies"}
        report["untested"].extend([
            "deployed production service/HTTPS ingress", "browser panel clicks at the private endpoint",
            "industrial surface/assembly complete-model rendering", "representative performance/controlled A/B",
            "concurrent render/edit race and multi-process GPU admission",
            "instrumented no-kernel-call count inside the unmodified CLI",
        ])
    finally:
        store.close()
        credentials.clear()
        cookies.clear()
        secret_bytes.clear()


def verify_saved_result(evidence, out):
    """Re-read real PNG/headers/DB fingerprints; a failed suite stays failed."""
    evidence = evidence.resolve()
    evidence.relative_to(Path("/hf3fs-jd/data/tmp").resolve())
    report = json.loads((evidence / "result.json").read_text())
    if report.get("head_source") != "DURABLE_PROJECT_DB" or report.get("cookie_login") != "PASS":
        raise AssertionError("not a completed real-cookie DB review")
    if report.get("source_drift") or report["source_before"] != report["source_after"]:
        raise AssertionError("source drift in saved evidence")
    if len(report["source_before"]) < 1 or report["check_count"] != len(report["checks"]):
        raise AssertionError("empty source manifest or invalid check count")
    counts = {v: sum(check["verdict"] == v for check in report["checks"]) for v in ("PASS", "FAIL")}
    if counts != report.get("check_counts") or sum(counts.values()) != len(report["checks"]):
        raise AssertionError("check verdict/count mismatch")
    findings = [check for check in report["checks"] if check["verdict"] == "FAIL"]
    if findings != report.get("findings", []):
        raise AssertionError("failure ledger differs from actual checks")
    if report["verdict"] != ("FAIL" if findings else "PASS"):
        raise AssertionError("suite verdict masks findings")
    if report.get("durable_db") != "PASS" or report["store_before"] != report["store_after"]:
        raise AssertionError("logical project DB changed")
    db_files = list(evidence.glob("*-project-db.json"))
    if len(db_files) != 5 or any(json.loads(path.read_text()) != report["store_before"] for path in db_files):
        raise AssertionError("persisted DB arm fingerprints differ/missing")
    if {k: v["verdict"] for k, v in report["ruler_negative_controls"].items()} != {
            "revision": "REJECTED", "snapshot": "REJECTED", "frame": "REJECTED", "png_crc": "REJECTED"}:
        raise AssertionError("ruler corruption controls missing")
    verified = []
    for entry in report["http_requests"]:
        if entry.get("headers", {}).get("Content-Type") != "image/png":
            continue
        label = entry["name"]
        if "/" in label or ".." in label:
            raise AssertionError("invalid artifact name")
        name = "bob" if label.startswith("bob-") else "alice"
        head = json.loads((evidence / (name + "-db-head.json")).read_text())
        mesh = json.loads((evidence / (name + "-http-mesh.json")).read_text())
        if digest(canonical(head["snapshot"])) != head["sha256"]:
            raise AssertionError("persisted head snapshot checksum invalid")
        png = (evidence / (label + ".png")).read_bytes()
        headers = json.loads((evidence / (label + "-headers.json")).read_text())
        if headers != entry["headers"] or digest(png) != entry["response_sha256"] or len(png) != entry["bytes"]:
            raise AssertionError("HTTP artifact differs from captured response")
        fallback = headers["X-OpenCAD-Render-Status"] == "CPU_FALLBACK"
        options = {"camera": headers["X-OpenCAD-Render-Camera"], "width": 128, "height": 128,
                   "backend": "gpu" if fallback else headers["X-OpenCAD-Render-Backend"],
                   "allow_cpu_fallback": fallback}
        pixels = verify_reply(type("SavedReply", (), {
            "status": entry["status"], "payload": png, "headers": headers,
            "content_type": "image/png"})(), head, mesh, options)
        verified.append({"name": label, "frame_sha256": digest(pixels),
                         "geometry_sha256": headers["X-OpenCAD-Geometry-SHA256"]})
    if len(verified) != 14 or len(report["cli_processes"]) != 3:
        raise AssertionError("expected 14 PNG responses and three original CLI arms")
    for process in report["cli_processes"]:
        raw = Path(process["log"]).read_bytes()
        if digest(raw) != process["log_sha256"] or process["rc"] != 0:
            raise AssertionError("CLI log/exit evidence changed")
    result = {"at": stamp(), "verdict": "EVIDENCE_VERIFIED", "suite_verdict": report["verdict"],
              "check_counts": counts, "hardware_stage": report["hardware_stage"],
              "durable_db": report["durable_db"], "source_drift": False,
              "result_sha256": digest((evidence / "result.json").read_bytes()),
              "source": str(evidence), "verified_pngs": verified, "findings": findings,
              "wall_s": report["wall_s"], "untested": report["untested"]}
    write_json(out / "verification.json", result)
    line = (f"PRIVATE_RENDER_EVIDENCE verdict=EVIDENCE_VERIFIED suite={report['verdict']} "
            f"passed={counts['PASS']} failed={counts['FAIL']} pngs={len(verified)} at={stamp()}")
    with (out / "verification.log").open("x") as handle:
        handle.write(line + "\n")
    print(line)
    return 0


def run_review(args, out, report):
    dependency = args.gateway_root.resolve()
    app = dependency / APP_REL
    required = ("access_control.py", "project_store.py", "project_gateway.py", "secure_web.py", "server.py")
    missing = [str(app / name) for name in required if not (app / name).is_file()]
    if missing:
        report["private_chain"] = "NOT_TESTED"
        raise FileNotFoundError("read-only gateway source dependencies absent: " + ", ".join(missing))
    for path in (app, dependency / "oss-alternatives/packages/cadcore",
                 dependency / "oss-alternatives/packages/dxf", dependency / "oss-alternatives/packages/vault"):
        sys.path.insert(0, str(path))
    renderer = load_file("render_service", ROOT / APP_REL / "render_service.py")
    adapter = load_file("industrial_render", ROOT / APP_REL / "industrial_render.py")
    import access_control
    import project_store
    import project_gateway
    import secure_web
    from cadcore.ops import sw_drawings
    from server import _load_cadcore
    modules = (access_control, project_store, project_gateway, secure_web, sys.modules["server"])
    for module in modules:
        if Path(module.__file__).resolve() != app / (module.__name__ + ".py"):
            raise RuntimeError(f"foreign runtime import: {module.__name__}")
    if not Path(sw_drawings.__file__).resolve().is_relative_to(dependency):
        raise RuntimeError("foreign CAD runtime import")
    report["runtime_origins"] = {m.__name__: str(Path(m.__file__).resolve()) for m in
                                 (*modules, renderer, adapter, sw_drawings)}
    counts = {"mesh": 0, "render": 0, "workspace": 0}
    auth = store = gateway = httpd = thread = None
    adapter.configure(enable_gpu=args.require_gpu, cuda_device=args.cuda_device)
    adapter.SERVICE = ObservedRenderer(adapter.SERVICE, counts)
    checks, frames = [], {}
    report["checks"] = checks

    def record(label, **facts):
        checks.append({"name": label, "verdict": "PASS", "at": stamp(), **facts})
        LOG.info("CHECK verdict=PASS name=%s", label)

    try:
        auth = access_control.AuthController(out / "accounts", allow_registration=True)
        tickets = {}
        for name in ("alice", "bob"):
            password = secrets.token_hex(24)
            auth.register(name, password)
            tickets[name] = auth.login(name, password)
        del password
        store = FixtureHeads() if args.handler_only else project_store.ProjectStore(out / "projects")
        report["head_source"] = "IN_MEMORY_FIXTURE" if args.handler_only else "DURABLE_PROJECT_DB"
        report["durable_db"] = "NOT_TESTED" if args.handler_only else "PASS"
        if args.handler_only:
            report["untested"].extend(["durable ProjectStore read_head/receipt immutability",
                                       "DB-backed /api/render authorization end-to-end"])
        gateway = project_gateway.ProjectGateway(auth, store, out / "runtime",
                                                 core=_load_cadcore(), secure_cookies=False)
        heads, meshes = {}, {}
        for name, depth in (("alice", 20), ("bob", 10)):
            document = sw_drawings._sample_model()
            document.features[0]["params"]["distance_nm"] = depth * 1_000_000
            snapshot = {"doc": gateway.core["docmodel"].document_to_l1(document),
                        "undo": [], "redo": [], "standard": document.standard_profile}
            heads[name] = store.create_project(tickets[name].principal.user_id,
                                               title="isolated render review " + name,
                                               request_id=name + "-create", payload=snapshot)
            head = store.read_head(heads[name]["project_id"])
            heads[name] = head
            if digest(canonical(head["snapshot"])) != head["sha256"]:
                raise AssertionError("real DB snapshot canonical SHA mismatch")
            with gateway._workspace(head["snapshot"], "en") as workspace:
                meshes[name] = workspace.mesh()
            solid = meshes[name]["solids"][0]
            points = solid["vertices_mm"]
            extent = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
            if len(solid["triangles"]) != 12 or any(abs(a - b) > 1e-7
                                                    for a, b in zip(extent, (60, 40, depth))):
                raise AssertionError("actual FreeCAD cuboid geometry differs from analytic fixture")
            record("snapshot_and_real_brep_" + name, revision=head["revision"],
                   snapshot_sha256=head["sha256"], extent_mm=extent, triangles=12)
        before = store_state(store)
        report["store_before"] = before

        def invoke(user, project, body, *, token_override=None):
            token = tickets[user].token if user else None
            if token_override is not None:
                token = token_override
            request = secure_web.Request("POST", "/api/render", {}, body, token, project, False)
            principal = auth.authenticate(request.token)
            head = gateway._authorized_head(request, principal)
            counts["workspace"] += 1
            with gateway._workspace(head["snapshot"], gateway._locale(principal)) as real:
                observed = ObservedWorkspace(real, counts)
                bound = adapter.bind_snapshot(observed, head)
                reply = adapter.handler(bound, body)
                if gateway._snapshot(real) != head["snapshot"]:
                    raise AssertionError("handler changed document/history/standard")
                return reply

        options = {"camera": "isometric", "width": 128, "height": 128, "backend": "cpu",
                   "allow_cpu_fallback": False}
        for name in ("alice", "bob"):
            head = heads[name]
            body = dict(options, expected_revision=head["revision"], expected_sha=head["sha256"])
            reply = invoke(name, head["project_id"], body)
            frames[name, "cpu"] = verify_reply(reply, head, meshes[name], body)
            if reply.headers["X-OpenCAD-Hardware"] != "false":
                raise AssertionError("CPU response claims hardware")
            (out / (name + "-cpu.png")).write_bytes(reply.payload)
            write_json(out / (name + "-cpu-headers.json"), reply.headers)
            record("authorized_handler_cpu_" + name, headers=reply.headers)
            if name == "alice":
                report["ruler_negative_controls"] = pixel_validator_controls(reply, head, meshes[name], body)
            if args.require_gpu:
                gpu_body = dict(body, backend="gpu")
                gpu = invoke(name, head["project_id"], gpu_body)
                pixels = verify_reply(gpu, head, meshes[name], gpu_body)
                observed = gpu.headers["X-OpenCAD-Renderer"].lower()
                if (gpu.headers["X-OpenCAD-Hardware"] != "true" or "nvidia" not in observed
                        or "rtx 3080" not in observed
                        or any(v in observed for v in ("swiftshader", "angle", "llvmpipe", "software"))):
                    raise AssertionError("actual authorized RTX3080 hardware not observed")
                if gpu.headers["X-OpenCAD-Render-Backend"] != "gpu":
                    raise AssertionError("requested hardware silently fell back")
                mismatch = sum(pixels[i:i + 4] != frames[name, "cpu"][i:i + 4]
                               for i in range(0, len(pixels), 4))
                if mismatch / (128 * 128) > 0.02:
                    raise AssertionError("CPU/GPU frame mismatch exceeds edge tolerance")
                repeated = invoke(name, head["project_id"], gpu_body)
                verify_reply(repeated, head, meshes[name], gpu_body)
                if repeated.headers["X-OpenCAD-Frame-SHA256"] != gpu.headers["X-OpenCAD-Frame-SHA256"]:
                    raise AssertionError("repeat hardware frame drift")
                frames[name, "gpu"] = pixels
                (out / (name + "-gpu.png")).write_bytes(gpu.payload)
                write_json(out / (name + "-gpu-headers.json"), gpu.headers)
                record("authorized_handler_gpu_" + name, headers=gpu.headers, mismatched_pixels=mismatch)
        if frames["alice", "cpu"] == frames["bob", "cpu"]:
            raise AssertionError("different project geometries produced the same fixture frame")
        record("different_projects_distinct_model_and_frame")
        report["hardware_stage"] = "PASS" if args.require_gpu else "NOT_TESTED"

        def denied(label, user, target, body, expected, token_override=None):
            entry = dict(counts)
            try:
                reply = invoke(user, target, body, token_override=token_override)
            except (access_control.AuthError, secure_web.HttpError) as exc:
                status, code = exc.status, exc.code
            else:
                status, code = reply.status, reply.payload.get("error")
            if status != expected or counts != entry:
                raise AssertionError(f"{label}: expected denial {expected} before workspace/mesh/render, got {status}")
            record(label, status=status, error=code, no_workspace_mesh_render=True)

        alice, bob = heads["alice"], heads["bob"]
        stolen = dict(options, expected_revision=alice["revision"], expected_sha=alice["sha256"])
        denied("cross_owner_bob_to_alice", "bob", alice["project_id"], stolen, 403)
        denied("cross_owner_alice_to_bob", "alice", bob["project_id"], options, 403)
        denied("anonymous", None, alice["project_id"], options, 401)
        denied("forged_session", "alice", alice["project_id"], options, 401,
               token_override="forged-session")
        denied("missing_project", "alice", None, options, 400)
        for label, body, status in (
            ("cross_project_expected_sha", dict(options, expected_revision=bob["revision"],
                                                expected_sha=bob["sha256"]), 409),
            ("stale_revision", dict(stolen, expected_revision=alice["revision"] + 1), 409),
            ("unauthorized_file_path", dict(options, path="/etc/passwd"), 400),
            ("external_url", dict(options, url="https://example.invalid/mesh"), 400),
            ("client_mesh", dict(options, mesh={}), 400),
            ("oversize_frame", dict(options, width=1024, height=1024), 400),
        ):
            entry = dict(counts)
            reply = invoke("alice", alice["project_id"], body)
            if reply.status != status or counts["mesh"] != entry["mesh"] or counts["render"] != entry["render"]:
                raise AssertionError(f"{label}: denial must precede tessellation/render")
            record(label, status=status, no_mesh_render=True)
        with gateway._workspace(alice["snapshot"], "en") as workspace:
            unbound = adapter.handler(workspace, options)
            if unbound.status != 500 or unbound.payload.get("error") != "render_snapshot_invalid":
                raise AssertionError("unbound workspace accepted")
            record("unbound_workspace_refused", status=unbound.status)
            for label, head in (("workspace_head_cross_project", bob),
                                ("corrupt_snapshot_sha", dict(alice, sha256="0" * 64))):
                try:
                    adapter.bind_snapshot(workspace, head)
                except adapter.SnapshotError as exc:
                    record(label, error=exc.code)
                else:
                    raise AssertionError(f"binder accepted {label}")
            # Demonstrate scope: these fields are not authenticated by the binder.
            metadata_only = dict(alice, owner=bob["owner"], project_id=bob["project_id"])
            adapter.bind_snapshot(workspace, metadata_only)
            record("binder_not_authorization_boundary", metadata_only_binding="ACCEPTED",
                   requirement="gateway authorizes real server-owned head before binding")
        if args.require_gpu:
            adapter.configure(enable_gpu=True, cuda_device=63)
            adapter.SERVICE = ObservedRenderer(adapter.SERVICE, counts)
            missing_body = dict(stolen, backend="gpu")
            missing = invoke("alice", alice["project_id"], missing_body)
            if missing.status != 503 or missing.payload.get("error") != "gpu_unavailable":
                raise AssertionError("real missing CUDA device must fail 503")
            fallback_body = dict(missing_body, allow_cpu_fallback=True)
            fallback = invoke("alice", alice["project_id"], fallback_body)
            pixels = verify_reply(fallback, alice, meshes["alice"], fallback_body)
            if (pixels != frames["alice", "cpu"]
                    or fallback.headers["X-OpenCAD-Render-Status"] != "CPU_FALLBACK"
                    or fallback.headers["X-OpenCAD-Hardware"] != "false"
                    or "63" not in fallback.headers.get("X-OpenCAD-Fallback-Reason", "")):
                raise AssertionError("explicit fallback lost cause or disagrees with CPU")
            record("real_gpu_missing_device_and_explicit_cpu_fallback",
                   status=missing.status, reason=missing.payload["detail"])
            adapter.configure(enable_gpu=True, cuda_device=args.cuda_device)

        # Use the unmodified secure HTTP server and unmodified application.
        httpd = secure_web.build_server(gateway)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        origin = httpd.public_origin
        port = httpd.server_address[1]

        def http_call(method, route, body=None, user=None, project=None, cross_origin=False):
            headers = {"Origin": "https://example.invalid" if cross_origin else origin,
                       "X-OpenCAD-Request": "1"}
            if user:
                headers["Cookie"] = secure_web.COOKIE_NAME + "=" + tickets[user].token
            if project:
                headers["X-OpenCAD-Project"] = project
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                connection.request(method, route, body=canonical(body) if body is not None else None,
                                   headers=headers)
                response = connection.getresponse()
                data, response_headers = response.read(), dict(response.getheaders())
                return response.status, response_headers, data
            finally:
                connection.close()

        for user, project in (("alice", alice), ("bob", bob)):
            status, _, raw = http_call("GET", "/api/state", user=user, project=project["project_id"])
            state = json.loads(raw)
            if status != 200 or (state["revision"], state["sha256"]) != (project["revision"], project["sha256"]):
                raise AssertionError("unmodified private HTTP state does not match DB head")
        for label, user, target, cross_origin, expected in (
            ("http_cross_owner", "bob", alice["project_id"], False, 403),
            ("http_anonymous", None, alice["project_id"], False, 401),
            ("http_csrf", "alice", alice["project_id"], True, 403),
        ):
            status, _, raw = http_call("POST", "/api/render", stolen, user, target, cross_origin)
            if status != expected or raw.startswith(b"\x89PNG"):
                raise AssertionError(f"{label}: actual HTTP authorization/CSRF denial failed")
            record(label, status=status, error=json.loads(raw)["error"])
        status, headers, raw = http_call("POST", "/api/render", stolen, "alice", alice["project_id"])
        report["gateway_route"] = {"status": status, "source": str(app / "project_gateway.py"),
                                   "patched": False}
        if status == 404:
            report["gateway_route"].update(verdict="NOT_INTEGRATED", error=json.loads(raw)["error"])
            report["untested"].extend(["authorized /api/render HTTP PNG success",
                                       "integrated GPU route startup configuration",
                                       "browser panel through authenticated render endpoint"])
        elif status == 200:
            reply = adapter.RenderReply(status, raw, headers.get("Content-Type", "").split(";")[0], headers)
            verify_reply(reply, alice, meshes["alice"], stolen)
            report["gateway_route"]["verdict"] = "CPU_HTTP_PASS"
            record("unmodified_gateway_render_http_cpu")
            if args.require_gpu:
                gpu_body = dict(stolen, backend="gpu")
                status, headers, raw = http_call("POST", "/api/render", gpu_body, "alice", alice["project_id"])
                gpu_reply = adapter.RenderReply(status, raw, headers.get("Content-Type", "").split(";")[0], headers)
                verify_reply(gpu_reply, alice, meshes["alice"], gpu_body)
                if headers.get("X-OpenCAD-Hardware") != "true":
                    raise AssertionError("integrated HTTP GPU request did not use hardware")
                report["gateway_route"]["verdict"] = "CPU_GPU_HTTP_PASS"
        else:
            raise AssertionError(f"unmodified gateway render owner request failed with unexpected status {status}")
        if store_state(store) != before:
            raise AssertionError("read-only requests changed project DB snapshots or receipts")
        report["store_after"] = store_state(store)
        report["invocation_counts"] = counts
        report["private_chain"] = "AUTH_HANDLER_FIXTURE_PASS" if args.handler_only else "PASS"
        report["hardware_stage"] = "PASS" if args.require_gpu else "NOT_TESTED"
        report["untested"].extend(["deployed production service", "enterprise/industrial modules",
                                   "representative production performance"])
        report["verdict"] = "PARTIAL" if args.handler_only or report["gateway_route"]["verdict"] == "NOT_INTEGRATED" else "PASS"
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError("owned fixture HTTP thread did not terminate")
        if gateway is not None:
            gateway.close()
        else:
            if auth is not None:
                auth.close()
            if store is not None:
                store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument("--handler-only", action="store_true",
                        help="explicit in-memory heads; real auth/Workspace/renderer, NOT durable DB acceptance")
    parser.add_argument("--http-db", action="store_true",
                        help="original secure CLI + real HTTP login/project DB/render; no product patch")
    parser.add_argument("--runtime-root", type=Path,
                        help="http-db only: existing isolated local private fixture parent")
    parser.add_argument("--diagnostic", action="store_true",
                        help="http-db: collect HTTP denial mismatches; any finding keeps final FAIL")
    parser.add_argument("--verify-result", type=Path,
                        help="only re-read saved HTTP/PNG/DB evidence; never changes a failed suite verdict")
    parser.add_argument("--cuda-device", type=int, choices=range(64), default=0)
    args = parser.parse_args()
    if args.verify_result is None and args.gateway_root is None:
        parser.error("--gateway-root is required for a live fixture review")
    if args.http_db and (args.handler_only or args.runtime_root is None):
        parser.error("--http-db requires --runtime-root and forbids --handler-only")
    if args.runtime_root is not None and not args.http_db:
        parser.error("--runtime-root is only accepted with --http-db")
    if args.diagnostic and not args.http_db:
        parser.error("--diagnostic requires --http-db")
    if os.geteuid() == 0:
        parser.error("review refuses root execution")
    out = args.out.resolve()
    out.relative_to(Path("/hf3fs-jd/data/tmp").resolve())
    out.mkdir(parents=True, exist_ok=False)
    os.environ["TMPDIR"] = str(out)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    if args.verify_result is not None:
        try:
            return verify_saved_result(args.verify_result, out)
        except Exception as exc:
            write_json(out / "verification-failure.json", {
                "verdict": "FAIL", "at": stamp(), "source": str(args.verify_result),
                "error": {"type": type(exc).__name__, "detail": str(exc),
                          "traceback": traceback.format_exc()}})
            with (out / "verification.log").open("x") as handle:
                handle.write(f"PRIVATE_RENDER_EVIDENCE verdict=FAIL at={stamp()}\n")
            raise
    logging.basicConfig(level=logging.INFO, handlers=[
        logging.FileHandler(out / "review.log", mode="x"), logging.StreamHandler()])
    before = source_state((ROOT, args.gateway_root.resolve()))
    report = {"started_at": stamp(), "argv": sys.argv, "verdict": "FAIL",
              "private_chain": "NOT_TESTED", "hardware_stage": "NOT_TESTED", "untested": [],
              "production_mutated": False, "official_research_changed": False,
              "source_before": before}
    start = time.perf_counter()
    try:
        process = subprocess.run(["nvidia-smi", "--query-gpu=index,name,driver_version,memory.total",
                                  "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        report["hardware_inventory"] = {"rc": process.returncode, "stdout": process.stdout,
                                        "stderr": process.stderr, "at": stamp()}
        if args.http_db:
            run_http_db(args, out, report)
        else:
            run_review(args, out, report)
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc()}
        report["verdict"] = "FAIL"
        LOG.exception("PRIVATE_RENDER_REVIEW failed")
    finally:
        report["source_after"] = source_state((ROOT, args.gateway_root.resolve()))
        report["source_drift"] = report["source_after"] != before
        if report["source_drift"]:
            report["verdict"] = "FAIL"
        report["finished_at"], report["wall_s"] = stamp(), time.perf_counter() - start
        report["check_count"] = len(report.get("checks", []))
        write_json(out / "result.json", report)
        LOG.info("PRIVATE_RENDER_REVIEW verdict=%s chain=%s hardware=%s checks=%s at=%s evidence=%s",
                 report["verdict"], report["private_chain"], report["hardware_stage"],
                 report["check_count"], report["finished_at"], out)
    return 0 if report["verdict"] in ("PASS", "PARTIAL") else 1


if __name__ == "__main__":
    raise SystemExit(main())
