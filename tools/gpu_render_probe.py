#!/usr/bin/env python3
"""Real loopback HTTP + headless GPU correctness probe; all evidence goes to 3FS.

No dependency installs, driver changes, remote services or arbitrary resource
operations. --require-gpu fails if the requested RTX3080 path is unavailable.
CPU fallback controls do not turn an unavailable hardware stage into PASS.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import logging
import os
from pathlib import Path
import resource
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
import traceback
import re
import urllib.error
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
sys.path.insert(0, str(APP))
import render_service as r


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    with path.open("x", encoding="utf8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def fingerprint():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (APP / "render_service.py", APP / "tests/test_render_service.py",
                         APP / "industrial_render.py", APP / "tests/test_industrial_render.py",
                         APP / "web/js/render-panel.js", APP / "web/css/render-panel.css",
                         Path(__file__).resolve())}


def verify_official_sources(out):
    """Verify captured body bytes/logs, never convert failed candidates to facts."""
    ledger = json.loads((ROOT / "docs/research/official-sources.json").read_text())
    rows = []
    for source in ledger["sources"]:
        status = source["capture_status"]
        log = Path(source["fetch_log"]).read_text()
        item = {"id": source["id"], "capture_status": status}
        if status in ("CAPTURED_BODY", "CAPTURED_OTHER_PAGE"):
            body = Path(source["body_path"]).read_bytes()
            assert body and len(body) == source["bytes"], source["id"]
            assert hashlib.sha256(body).hexdigest() == source["sha256"], source["id"]
            assert "ASTRiLL_FETCH ok=1" in log and source["sha256"] in log, source["id"]
            text = html.unescape(body.decode("utf8"))
            for fact in source["facts"]:
                assert fact["anchor"] in text, f"official anchor missing: {source['id']} {fact['anchor']}"
            item.update(bytes=len(body), sha256=source["sha256"], facts=len(source["facts"]))
            if status == "CAPTURED_OTHER_PAGE":
                assert not source["facts"] and "Welcome to Onshape Help" in text
        else:
            assert status == "NOT_CAPTURED" and not source["facts"] and source["sha256"] is None
            assert "ASTRiLL_FETCH ok=0" in log, source["id"]
            item["facts"] = 0
        rows.append(item)
    result = {"verdict": "PASS", "at": stamp(), "source_rows": rows,
              "ledger_sha256": hashlib.sha256(
                  (ROOT / "docs/research/official-sources.json").read_bytes()).hexdigest(),
              "industrial_parity": "NOT_ASSESSED", "web_tool_capture": "NOT_CAPTURED"}
    write_json(out / "official-source-verification.json", result)
    return result


def inventory(out):
    facts = {"at": stamp(), "hostname": os.uname().nodename, "uid": os.geteuid(), "commands": []}
    for name, argv in (
        ("nvidia", ["nvidia-smi", "--query-gpu=index,name,uuid,driver_version,memory.total,memory.used,utilization.gpu",
                    "--format=csv,noheader"]),
        ("libraries", ["ldconfig", "-p"]),
        ("python_packages", [sys.executable, "-B", "-m", "pip", "show", "moderngl", "glcontext",
                             "PyOpenGL", "vtk", "numpy", "pillow"]),
    ):
        try:
            process = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            output, error, rc = process.stdout, process.stderr, process.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            output, error, rc = "", f"{type(exc).__name__}: {exc}", None
        with (out / (name + ".log")).open("x") as handle:
            handle.write(output + "\nSTDERR:\n" + error)
        item = {"name": name, "argv": argv, "rc": rc, "stderr": error}
        if name == "libraries":
            item["relevant"] = [line.strip() for line in output.splitlines()
                                if any(s in line for s in ("libEGL", "libGLESv2", "libOSMesa"))]
        elif name == "nvidia":
            item["stdout"] = output
        facts["commands"].append(item)
    facts["python_module_availability"] = {name: importlib.util.find_spec(name) is not None
                                           for name in ("moderngl", "OpenGL", "vtk", "numpy", "PIL")}
    return facts


def triangle():
    return {"vertices": [[-0.75, -0.75, 0], [0.75, -0.75, 0], [0, 0.75, 0]],
            "triangles": [[0, 1, 2]], "colors": [[20, 80, 200]], "width": 64, "height": 64}


def cube():
    points = [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
              [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]]
    triangles = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                 [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                 [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    mesh = r.project_mesh({"vertices_mm": points, "triangles": triangles})
    mesh.update(width=128, height=128, colors=[[40 + i * 15, 100, 180] for i in range(12)])
    return mesh


def cad_geometry(out):
    """Actual FreeCAD BRep -> tessellation, numeric volume independently pinned."""
    sys.path.insert(0, str(ROOT / "oss-alternatives/packages/cadcore"))
    from cadcore.kernel.subproc import SubprocessKernel
    from cadcore.ops import sw_drawings

    os.environ["TMPDIR"] = str(out)
    kernel_dir = out / "kernel-work"
    kernel_dir.mkdir()
    kernel = SubprocessKernel(work_dir=kernel_dir, keep_work_dir=True)
    try:
        probe = kernel.probe_report()
        write_json(out / "kernel-probe.json", probe.as_dict())
        if not probe.available:
            raise RuntimeError(f"real CAD backend unavailable: {probe.reason} {probe.detail}")
        document = sw_drawings._sample_model().to_payload()
        mesh = kernel.tessellate(document)
        assert mesh["solid_count"] == 1
        assert abs(mesh["properties"]["volume_nm3"] / 1e18 - 48000) < 1e-6
        write_json(out / "cad-document.json", document)
        write_json(out / "cad-kernel-mesh.json", mesh)
        value = r.project_mesh(mesh)
        value.update(width=128, height=128)
        return value
    finally:
        kernel.close()


def decode_png(data, width, height):
    """Independent PNG CRC/geometry/readback check, not the renderer's encoder."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("PNG magic absent")
    offset, compressed, seen = 8, bytearray(), []
    while offset < len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[offset + 8 + size:offset + 12 + size])[0]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc, "PNG CRC mismatch"
        seen.append(kind)
        if kind == b"IHDR":
            assert struct.unpack(">IIBBBBB", body) == (width, height, 8, 6, 0, 0, 0)
        if kind == b"IDAT":
            compressed.extend(body)
        offset += size + 12
    assert seen == [b"IHDR", b"IDAT", b"IEND"] and offset == len(data)
    raw = zlib.decompress(compressed)
    stride = width * 4 + 1
    assert len(raw) == stride * height
    assert all(raw[y * stride] == 0 for y in range(height))
    return b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


def post(base, payload):
    req = urllib.request.Request(base + "/render", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def verify_frame(value, payload, out, label):
    png = base64.b64decode(value["image_png_base64"], validate=True)
    assert hashlib.sha256(png).hexdigest() == value["png_sha256"]
    pixels = decode_png(png, payload["width"], payload["height"])
    assert hashlib.sha256(pixels).hexdigest() == value["frame_sha256"]
    # Independently known background corner and interior, not a nonempty-image test.
    assert pixels[:4] == bytes(r.BACKGROUND)
    center = ((payload["height"] // 2) * payload["width"] + payload["width"] // 2) * 4
    assert pixels[center:center + 4] != bytes(r.BACKGROUND)
    with (out / (label + ".png")).open("xb") as handle:
        handle.write(png)
    write_json(out / (label + ".json"), {k: v for k, v in value.items() if k != "image_png_base64"})
    return pixels


def benchmark(base, cases, repeats, out, cuda_device):
    """Serial ABBA is intentional: order control on the same shared device.

    This is a small-mesh diagnostic, not a production throughput or CAD-kernel
    acceleration claim. HTTP costs include encoding, transport and decoding;
    renderer elapsed includes context creation, readback and PNG encoding.
    """
    samples, records, stop = [], [], threading.Event()

    def sample_gpu():
        while not stop.is_set():
            started = stamp()
            try:
                process = subprocess.run(
                    ["nvidia-smi", f"--id={cuda_device}",
                     "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
                samples.append({"at": started, "rc": process.returncode,
                                "stdout": process.stdout, "stderr": process.stderr})
                if process.returncode:
                    stop.set()
            except (OSError, subprocess.TimeoutExpired) as exc:
                samples.append({"at": started, "error": f"{type(exc).__name__}: {exc}"})
                stop.set()
            stop.wait(0.1)

    sampler = threading.Thread(target=sample_gpu, daemon=True)
    before = resource.getrusage(resource.RUSAGE_SELF)
    sampler.start()
    started = time.perf_counter()
    try:
        with (out / "benchmark-requests.jsonl").open("x") as log:
            for name, payload in cases.items():
                expected = {}
                for backend in ("cpu", "gpu"):
                    status, value = post(base, dict(payload, backend=backend))
                    assert status == 200 and value["backend"] == backend
                    expected[backend] = value["frame_sha256"]
                # Same-config adjacent arms estimate local noise; then ABBA.
                for repeat in range(repeats):
                    order = ("cpu", "cpu", "gpu", "gpu", "cpu", "gpu", "gpu", "cpu")
                    for position, backend in enumerate(order):
                        begin = time.perf_counter()
                        status, value = post(base, dict(payload, backend=backend))
                        elapsed = time.perf_counter() - begin
                        assert status == 200 and value["backend"] == backend
                        assert value["frame_sha256"] == expected[backend]
                        assert value["fallback_reason"] is None
                        record = {"case": name, "repeat": repeat, "position": position,
                                  "kind": "noise_control" if position < 4 else "ABBA",
                                  "backend": backend, "http_elapsed_s": elapsed,
                                  "render_elapsed_s": value["elapsed_s"],
                                  "geometry_sha256": value["geometry_sha256"],
                                  "frame_sha256": value["frame_sha256"],
                                  "renderer": value["renderer"]}
                        log.write(json.dumps(record, sort_keys=True) + "\n")
                        log.flush()
                        records.append(record)
    finally:
        stop.set()
        sampler.join(6)
        write_json(out / "benchmark-gpu-samples.json", samples)
        assert not sampler.is_alive(), "owned resource sampler did not terminate"
    assert samples and all(item.get("rc") == 0 for item in samples), "GPU sampling failed"
    after = resource.getrusage(resource.RUSAGE_SELF)
    summary = []
    for name in cases:
        item = {"case": name}
        for backend in ("cpu", "gpu"):
            values = [r for r in records if r["case"] == name and r["backend"] == backend
                      and r["kind"] == "ABBA"]
            controls = [r["http_elapsed_s"] for r in records if r["case"] == name
                        and r["backend"] == backend and r["kind"] == "noise_control"]
            item[backend] = {
                "http_median_s": statistics.median(r["http_elapsed_s"] for r in values),
                "render_median_s": statistics.median(r["render_elapsed_s"] for r in values),
                "http_min_s": min(r["http_elapsed_s"] for r in values),
                "http_max_s": max(r["http_elapsed_s"] for r in values),
                "identical_config_noise_median_s": statistics.median(
                    abs(controls[i] - controls[i + 1]) for i in range(0, len(controls), 2))}
        item["diagnostic_cpu_over_gpu_http_ratio"] = (
            item["cpu"]["http_median_s"] / item["gpu"]["http_median_s"])
        summary.append(item)
    report = {"status": "MEASURED_SMALL_MESH_ONLY", "repeats": repeats,
              "order": "same-config CPU CPU GPU GPU, then ABBA CPU GPU GPU CPU",
              "requests": len(records), "cases": summary,
              "wall_s": time.perf_counter() - started,
              "process_user_cpu_s": after.ru_utime - before.ru_utime,
              "process_system_cpu_s": after.ru_stime - before.ru_stime,
              "process_lifetime_maxrss_kib": after.ru_maxrss,
              "gpu_sampling_scope": "device-wide; shared host, not exclusive utilization",
              "nsys_available": shutil.which("nsys") is not None,
              "ncu_available": shutil.which("ncu") is not None,
              "nsight_profile": "NOT_CAPTURED",
              "production_speedup": "NOT_ESTABLISHED",
              "limitations": ["small bounded frames", "nonexclusive shared GPU",
                              "no auth/project route", "no industrial workload",
                              "no persistent context", "no geometry-kernel timing"]}
    write_json(out / "benchmark.json", report)
    return report


def adapter_probe(out, cuda_device, browser):
    """Actual FreeCAD snapshot and PNG HTTP adapter, NOT the auth/DB gateway.

    Fixture head is canonical snapshot data; a real durable DB/authorization
    integration must be accepted by the chair separately. Browser never draws
    geometry: it only requests and displays server PNGs.
    """
    import industrial_render as adapter
    from server import Workspace, _load_cadcore
    spec = importlib.util.spec_from_file_location(
        "render_probe_cdp", APP / "tests/legacy_browser_compat_gate.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    _WSClient = helper._WSClient

    adapter.configure(enable_gpu=True, cuda_device=cuda_device)
    original = Workspace(_load_cadcore(), locale="zh-CN", standard="iso",
                         out_root=str(out / "adapter-work"))
    original.doc = original.docmodel.document_from_l1((out / "cad-document.json").read_text())
    snapshot = adapter._snapshot(original)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    head = {"revision": 7, "sha256": digest, "snapshot": snapshot}
    bound = adapter.bind_snapshot(original, head)
    write_json(out / "adapter-fixture-head.json", head)
    events, image_checks = [], []
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<link rel="stylesheet" href="/web/css/render-panel.css">'
            '<script defer src="/web/js/render-panel.js"></script></head>'
            '<body><main data-opencad-render-panel></main></body></html>').encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info("ADAPTER_HTTP %s", fmt % args)

        def reply(self, status, data, media, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' data:; connect-src 'self'; object-src 'none'")
            for key, value in (headers or {}).items():
                if key != "Cache-Control":
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self.reply(200, json.dumps({"ok": True, "revision": head["revision"],
                                             "sha256": head["sha256"]}).encode(), "application/json")
            elif self.path in ("/web/js/render-panel.js", "/web/css/render-panel.css"):
                path = APP / self.path.lstrip("/")
                media = "text/javascript" if path.suffix == ".js" else "text/css"
                self.reply(200, path.read_bytes(), media)
            else:
                self.reply(404, b'{"error":"not_found"}', "application/json")

        def do_POST(self):
            if self.path != "/api/render":
                self.reply(404, b'{"error":"not_found"}', "application/json")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                self.reply(400, b'{"error":"bad_body_size"}', "application/json")
                return
            body = r.decode_request(self.rfile.read(length))
            result = adapter.handler(bound, body)
            events.append({"body": body, "status": result.status, "headers": result.headers})
            data = result.payload if isinstance(result.payload, bytes) else json.dumps(result.payload).encode()
            if result.status == 200:
                pixels = decode_png(data, body.get("width", 256), body.get("height", 256))
                assert hashlib.sha256(pixels).hexdigest() == result.headers["X-OpenCAD-Frame-SHA256"]
                image_checks.append(result.headers["X-OpenCAD-Frame-SHA256"])
            self.reply(result.status, data, result.content_type, result.headers)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    process = ws = None
    report = {"http_base": base, "auth_db_route": "NOT_INTEGRATED",
              "browser": "NOT_RUN", "production_mutated": False}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(body):
            req = urllib.request.Request(base + "/api/render", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=15) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers), exc.read()

        for backend in ("cpu", "gpu"):
            status, headers, data = request({"backend": backend, "width": 128, "height": 128})
            assert status == 200 and headers["X-OpenCAD-Render-Backend"] == backend
            assert headers["X-OpenCAD-Revision"] == "7"
            assert headers["X-OpenCAD-Snapshot-SHA256"] == digest
            with (out / f"adapter-{backend}.png").open("xb") as stream:
                stream.write(data)
        for body, expected in (({"path": "/etc/passwd"}, 400), ({"url": "https://example.invalid"}, 400),
                               ({"expected_revision": 6, "expected_sha": digest}, 409)):
            assert request(body)[0] == expected
        if browser:
            if not Path(browser).is_file():
                raise FileNotFoundError(f"existing browser missing: {browser}")
            profile = out / "browser-profile"
            with (out / "browser.log").open("xb") as stream:
                process = subprocess.Popen(
                    [browser, "--headless", "--no-sandbox", "--disable-gpu",
                     "--disable-dev-shm-usage", "--disable-background-networking",
                     "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
                     f"--user-data-dir={profile}", "about:blank"], stdout=stream, stderr=subprocess.STDOUT,
                    env=dict(os.environ, TMPDIR="/dev/shm"))
            deadline = time.monotonic() + 20
            active = profile / "DevToolsActivePort"
            while not active.is_file():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("browser startup failed; browser.log")
                time.sleep(0.05)
            port = active.read_text().splitlines()[0]
            tab = None
            while tab is None:
                with opener.open(f"http://127.0.0.1:{port}/json/list") as response:
                    tab = next((item for item in json.load(response) if item["type"] == "page"), None)
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("browser page target absent; browser.log")
                if tab is None:
                    time.sleep(0.05)
            ws = _WSClient(tab["webSocketDebuggerUrl"])
            sequence = 0
            def call(method, params):
                nonlocal sequence
                sequence += 1
                ws.send_text(json.dumps({"id": sequence, "method": method, "params": params}))
                while True:
                    message = json.loads(ws.recv_text())
                    if message.get("id") == sequence:
                        assert "error" not in message, message
                        result = message["result"]
                        assert "exceptionDetails" not in result, result
                        return result
            def evaluate(expression):
                return call("Runtime.evaluate", {"expression": expression, "returnByValue": True})["result"].get("value")
            def wait_image():
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    value = evaluate("(()=>{const p=document.querySelector('.oc-render-panel');"
                                     "const i=p&&p.querySelector('img');return p&&{ready:!p.querySelector('button').disabled,"
                                     "image:i&&!i.hidden&&i.complete&&i.naturalWidth>0,status:p.querySelector('p').textContent,"
                                     "overflow:document.documentElement.scrollWidth>innerWidth,"
                                     "canvas:document.querySelectorAll('canvas').length};})()")
                    if value and value["ready"] and value["image"]:
                        assert value["canvas"] == 0 and not value["overflow"], value
                        return value
                    time.sleep(0.05)
                raise AssertionError(f"browser image completion failed: {value}")
            call("Page.enable", {})
            call("Network.enable", {})
            # Block all non-loopback page resources; driver/binary is unchanged.
            call("Network.setBlockedURLs", {"urls": ["https://*", "http://[!1]*"]})
            call("Page.navigate", {"url": base})
            deadline = time.monotonic() + 15
            while not evaluate("!!document.querySelector('.oc-render-panel button')"):
                if time.monotonic() > deadline:
                    raise AssertionError("render controls did not mount")
                time.sleep(0.05)
            views = []
            for width in (1440, 420):
                call("Emulation.setDeviceMetricsOverride",
                     {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
                rect = evaluate("(()=>{const r=document.querySelector('.oc-render-panel button').getBoundingClientRect();"
                                "return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
                count = len(events)
                for kind in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", dict(rect, type=kind, button="left", clickCount=1))
                view = wait_image()
                assert len(events) == count + 1 and events[-1]["body"]["backend"] == "gpu"
                assert events[-1]["headers"]["X-OpenCAD-Hardware"] == "true"
                shot = call("Page.captureScreenshot", {"format": "png"})["data"]
                with (out / f"render-panel-{width}.png").open("xb") as stream:
                    stream.write(base64.b64decode(shot))
                views.append(dict(view, width=width))
            count = len(events)
            evaluate("document.querySelector('.oc-render-panel select').focus()")
            for kind in ("keyDown", "keyUp"):
                call("Input.dispatchKeyEvent", {"type": kind, "key": "Home", "code": "Home",
                                               "windowsVirtualKeyCode": 36})
            for kind in ("keyDown", "keyUp"):
                call("Input.dispatchKeyEvent", {"type": kind, "key": "ArrowDown", "code": "ArrowDown",
                                               "windowsVirtualKeyCode": 40})
            view = wait_image()
            assert len(events) > count and events[-1]["body"]["camera"] == "front"
            report.update(browser="PASS", views=views, camera_control=view,
                          browser_version=call("Browser.getVersion", {}))
        assert adapter._snapshot(original) == snapshot, "adapter changed document/history"
        report.update(verdict="PASS", calls=len(events), independent_png_checks=len(image_checks),
                      snapshot_unchanged=True, model_sha256=hashlib.sha256(snapshot["doc"].encode()).hexdigest())
        return report
    finally:
        if ws:
            ws.sock.close()
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        server.shutdown()
        thread.join(5)
        server.server_close()
        write_json(out / "adapter-http-events.json", events)
        write_json(out / "adapter-result.json", report)
        assert not thread.is_alive()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--cad-geometry", action="store_true")
    parser.add_argument("--cuda-device", type=int, default=0)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--benchmark-repeats", type=int, choices=range(3, 11), default=3)
    parser.add_argument("--adapter", action="store_true")
    parser.add_argument("--browser", help="existing Chromium path; no installs")
    parser.add_argument("--verify-official", action="store_true")
    args = parser.parse_args()
    if args.adapter and not args.cad_geometry:
        parser.error("--adapter requires --cad-geometry")
    if args.browser and not args.adapter:
        parser.error("--browser requires --adapter")
    out = args.out.resolve()
    out.relative_to(Path("/hf3fs-jd/data/tmp").resolve())
    if os.geteuid() == 0:
        parser.error("probe refuses root execution")
    out.mkdir(parents=True, exist_ok=False)
    log = out / "probe.log"
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(log, mode="x"),
                                                      logging.StreamHandler()])
    report = {"started_at": stamp(), "argv": sys.argv, "source_before": fingerprint(),
              "verdict": "FAIL", "speedup": "NOT_MEASURED", "production_mutated": False,
              "hardware_stage": "NOT_PROBED", "controls": [], "tests": None}
    start = time.perf_counter()
    server = thread = None
    try:
        report["inventory"] = inventory(out)
        if args.verify_official:
            report["official_sources"] = verify_official_sources(out)
        if args.inventory_only:
            report["verdict"] = "INVENTORY_ONLY"
            return 0
        with (out / "unit-tests.log").open("xb") as handle:
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest",
                                   "tests.test_render_service", "tests.test_industrial_render", "-v"], cwd=APP,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                                  stdout=handle, stderr=subprocess.STDOUT)
        report["tests"] = {"rc": proc.returncode, "log": str(out / "unit-tests.log")}
        assert proc.returncode == 0, "unit tests failed; inspect unit-tests.log"
        text = (out / "unit-tests.log").read_text()
        counts = re.findall(r"Ran (\d+) tests in ([0-9.]+)s", text)
        assert len(counts) == 1 and int(counts[0][0]) > 0 and text.rstrip().endswith("OK")
        report["tests"].update(count=int(counts[0][0]), skipped=0, failures=0, errors=0,
                               completed_marker="OK")
        server = r.make_http_server(r.RenderService(enable_gpu=True, cuda_device=args.cuda_device))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        report["http_base"] = base
        cases = {"triangle": triangle(), "cube": cube()}
        if args.cad_geometry:
            cases["cad-brep"] = cad_geometry(out)
        write_json(out / "render-inputs.json", cases)
        cpu_frames = {}
        for label, payload in cases.items():
            status, value = post(base, dict(payload, backend="cpu"))
            assert status == 200 and value["backend"] == "cpu" and not value["renderer"]["hardware"]
            cpu_frames[label] = verify_frame(value, payload, out, label + "-cpu")
        assert cpu_frames["triangle"][(32 * 64 + 32) * 4:(32 * 64 + 32) * 4 + 4] == bytes((20, 80, 200, 255))
        report["controls"].append("analytic_cpu_triangle_center_and_background")
        # Real missing device, not mock hardware; permissive fallback must retain cause.
        unavailable = r.RenderService(enable_gpu=True, cuda_device=63)
        disabled_server = r.make_http_server(unavailable)
        disabled_thread = threading.Thread(target=disabled_server.serve_forever, daemon=True)
        disabled_thread.start()
        try:
            disabled_base = f"http://127.0.0.1:{disabled_server.server_port}"
            status, value = post(disabled_base, dict(cases["triangle"], backend="gpu"))
            assert status == 503 and value["error"] == "gpu_unavailable"
            write_json(out / "gpu-negative-control.json", {"http_status": status, "body": value})
            status, value = post(disabled_base, dict(cases["triangle"], backend="gpu", allow_cpu_fallback=True))
            assert status == 200 and value["status"] == "CPU_FALLBACK" and value["fallback_reason"]
            assert not value["renderer"]["hardware"]
            pixels = verify_frame(value, cases["triangle"], out, "explicit-cpu-fallback")
            assert pixels == cpu_frames["triangle"]
            report["controls"].append("real_missing_cuda_device_503_and_explicit_cpu_fallback")
        finally:
            disabled_server.shutdown()
            disabled_thread.join(5)
            disabled_server.server_close()
            assert not disabled_thread.is_alive()
        report["gpu_comparisons"] = []
        for label, payload in cases.items():
            status, value = post(base, dict(payload, backend="gpu"))
            if status != 200:
                report["hardware_stage"] = "UNAVAILABLE"
                report["hardware_failure"] = {"status": status, "body": value}
                raise r.GPUUnavailable(f"real HTTP GPU path failed: {status} {value}")
            assert value["backend"] == "gpu" and value["renderer"]["hardware"]
            gpu = verify_frame(value, payload, out, label + "-gpu")
            status, again = post(base, dict(payload, backend="gpu"))
            assert status == 200 and again["frame_sha256"] == value["frame_sha256"]
            cpu = cpu_frames[label]
            mismatches = sum(cpu[i:i + 4] != gpu[i:i + 4] for i in range(0, len(cpu), 4))
            fraction = mismatches / (payload["width"] * payload["height"])
            # GPU top-left edge and finite depth precision can differ from CPU on edges.
            assert fraction <= 0.02, f"GPU/CPU mismatch exceeds 2%: {fraction}"
            center = ((payload["height"] // 2) * payload["width"] + payload["width"] // 2) * 4
            assert cpu[center:center + 4] == gpu[center:center + 4], "geometry center/depth mismatch"
            report["gpu_comparisons"].append({"case": label, "mismatched_pixels": mismatches,
                                               "fraction": fraction, "renderer": value["renderer"],
                                               "frame_sha256": value["frame_sha256"],
                                               "repeat_sha_equal": True})
        report["hardware_stage"] = "PASS"
        # Fixed far-plane negative geometry must be background on both backends.
        far = dict(triangle(), vertices=[[-0.75, -0.75, 1], [0.75, -0.75, 1], [0, 0.75, 1]])
        for backend in ("cpu", "gpu"):
            status, value = post(base, dict(far, backend=backend))
            assert status == 200
            pixels = decode_png(base64.b64decode(value["image_png_base64"]), 64, 64)
            assert pixels == bytes(r.BACKGROUND) * (64 * 64), "far-plane depth negative failed"
            write_json(out / (backend + "-far-plane.json"),
                       {k: v for k, v in value.items() if k != "image_png_base64"})
        report["controls"].append("cpu_gpu_far_depth_plane_negative")
        for invalid in ({}, dict(triangle(), width=1024, height=1024),
                        dict(triangle(), triangles=[[0, 1, 99]])):
            status, value = post(base, invalid)
            assert status == 400 and value["error"] == "invalid_render_input"
        report["controls"].append("real_http_empty_oversize_and_bad_indices_400")
        if args.benchmark:
            report["benchmark"] = benchmark(base, cases, args.benchmark_repeats,
                                             out, args.cuda_device)
            report["speedup"] = "NOT_ESTABLISHED"
        if args.adapter:
            report["adapter"] = adapter_probe(out, args.cuda_device, args.browser)
        report["verdict"] = "PASS"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc()}
        logging.exception("GPU_RENDER_PROBE failed")
        if isinstance(exc, r.GPUUnavailable) and not args.require_gpu:
            report["verdict"] = "CPU_ONLY_GPU_UNAVAILABLE"
    finally:
        if server:
            server.shutdown()
            thread.join(5)
            server.server_close()
            if thread.is_alive():
                report["verdict"] = "FAIL"
                report["shutdown_error"] = "owned HTTP thread did not terminate"
        report["source_after"] = fingerprint()
        report["source_drift"] = report["source_before"] != report["source_after"]
        if report["source_drift"]:
            report["verdict"] = "FAIL"
        report["finished_at"], report["wall_s"] = stamp(), time.perf_counter() - start
        write_json(out / "result.json", report)
        logging.info("GPU_RENDER_PROBE verdict=%s hardware=%s at=%s evidence=%s",
                     report["verdict"], report["hardware_stage"], report["finished_at"], out)
    return 0 if report["verdict"] in ("PASS", "CPU_ONLY_GPU_UNAVAILABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
