#!/usr/bin/env python3
"""Exercise an isolated real Web 3D workspace and record actual GPU rendering.

No production document is edited. Hardware mode rejects software renderers.
Browser profile sockets stay local; screenshots, logs and samples go to 3FS.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "oss-alternatives/apps/opencad"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def request(base, path, body=None):
    req = urllib.request.Request(base + path)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.load(response)
    if not data.get("ok"):
        raise RuntimeError(f"{path}: {data}")
    return data


def browser(args, base, out, width, *, empty=False):
    helper = load("web3d_cdp", APP / "tests/legacy_browser_compat_gate.py")
    with tempfile.TemporaryDirectory(prefix="opencad-webgl-", dir="/tmp") as directory:
        profile = Path(directory)
        flags = ["--enable-gpu", "--use-gl=angle", "--use-angle=" + args.angle,
                 "--ignore-gpu-blocklist"] if args.hardware else [
                     "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        if args.hardware and args.angle == "vulkan":
            flags.extend(["--disable-features=Vulkan"])
        env = dict(os.environ)
        # Chromium also puts its singleton socket in TMPDIR, not just the profile.
        env["TMPDIR"] = directory
        if args.hardware:
            env["VK_ICD_FILENAMES"] = "/etc/vulkan/icd.d/nvidia_icd.json"
        suffix = f"{width}-empty" if empty else str(width)
        log = out / f"chrome-{suffix}.log"
        with log.open("w") as stream:
            proc = subprocess.Popen([args.browser, "--headless", "--no-sandbox",
                                     "--disable-dev-shm-usage", "--remote-debugging-port=0",
                                     f"--user-data-dir={profile}", "--no-first-run",
                                     "--no-default-browser-check", *flags, "about:blank"],
                                    env=env, stdout=stream, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            ws = None
            samples, snapshots, stop = [], [], threading.Event()

            def sample():
                while not stop.is_set():
                    p = subprocess.run(["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used",
                                        "--format=csv,noheader,nounits"], capture_output=True, text=True)
                    samples.append({"time": time.time(), "rc": p.returncode,
                                    "data": p.stdout.strip(), "error": p.stderr.strip()})
                    stop.wait(0.3)

            sampler = threading.Thread(target=sample, daemon=True)
            sampler.start()
            try:
                active = profile / "DevToolsActivePort"
                deadline = time.monotonic() + 30
                while not active.exists():
                    if proc.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"browser startup failed: {log}")
                    time.sleep(0.1)
                debug = "http://127.0.0.1:" + active.read_text().splitlines()[0]
                with urllib.request.urlopen(debug + "/json/list", timeout=10) as response:
                    tabs = json.load(response)
                ws = helper._WSClient(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"])
                sequence = {"id": 0}

                def call(method, params):
                    msg = helper._cdp_call(ws, sequence, method, params, 10)
                    if "error" in msg or "exceptionDetails" in msg.get("result", {}):
                        raise RuntimeError(f"CDP failed: {msg}")
                    return msg["result"]

                def evaluate(expression):
                    return call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                     "awaitPromise": True})["result"].get("value")

                def wait_ready(expected_volume, *, after_event=0, locale=None):
                    deadline = time.monotonic() + (10 if locale is not None else 20)
                    while True:
                        snap = evaluate("""(function(){
                          var m=window.opencad3d, node=document.getElementById('model');
                          var context=node && node.getContext('webgl');
                          var event=window.__web3dState || {count:0,locale:null};
                          return m ? {renderer:m.renderer, triangles:m.triangles, frames:m.frames,
                            volume:m.volume_mm3,error:m.error,locale:document.documentElement.lang,
                            busy:m.busy===true,locale_disabled:document.getElementById('locale').disabled,
                            probe_installed:!!window.__web3dState,
                            state_events:event.count,event_locale:event.locale,
                            context_lost:context ? context.isContextLost() : null,
                            status:document.getElementById('status').textContent,
                            width:document.documentElement.scrollWidth, viewport:innerWidth} : null;
                        })()""")
                        if snap:
                            snapshots.append(dict(snap, time=time.time(), expected_locale=locale,
                                                  after_event=after_event, expected_volume=expected_volume))
                        if snap and snap["error"]:
                            raise RuntimeError(f"3D page error: {snap}")
                        if snap and not snap["probe_installed"]:
                            raise RuntimeError(f"state-event probe not installed: {snap}")
                        if snap and snap["context_lost"] is True:
                            raise RuntimeError(f"WebGL context lost: {snap}")
                        if (snap and not snap["busy"] and not snap["locale_disabled"]
                                and snap["state_events"] > after_event
                                and snap["context_lost"] is False
                                and snap["event_locale"] == snap["locale"]
                                and (locale is None or snap["locale"] == locale)
                                and (snap["frames"] > 0 and snap["triangles"] == 0 if empty else
                                     snap["triangles"] > 0 and abs(snap["volume"] - expected_volume) < 1e-5)):
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"3D page did not complete (locale={locale}, "
                                               f"after_event={after_event}): {snap}")
                        time.sleep(0.1)
                    if snap["width"] > snap["viewport"] + 1:
                        raise RuntimeError(f"mobile overflow: {snap}")
                    return snap

                def trigger(expression):
                    # Do not dispatch a synthetic change into a disabled control.
                    return evaluate("""(function(){
                      if(window.opencad3d.busy || document.getElementById('locale').disabled)
                        throw new Error('test_operation_before_idle');
                      var before=window.__web3dState.count;
                      """ + expression + "; return before;})()")

                call("Runtime.enable", {})
                call("Page.enable", {})
                call("Page.addScriptToEvaluateOnNewDocument", {"source": """
                  window.__web3dState={count:0,locale:null};
                  window.addEventListener('opencad:state',function(event){
                    window.__web3dState={count:window.__web3dState.count+1,
                      locale:event.detail.locale};
                  });
                """})
                call("Emulation.setDeviceMetricsOverride",
                     {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": width < 650})
                call("Page.navigate", {"url": base + "/web/3d.html"})
                volume = (120 * 80 - math.pi * 8**2) * 6
                snap = wait_ready(volume)
                if args.hardware and ("NVIDIA" not in snap["renderer"] or "3080" not in snap["renderer"]
                                      or "SwiftShader" in snap["renderer"]):
                    raise RuntimeError(f"hardware not in use: {snap['renderer']}")
                if empty:
                    strings = request(base, "/api/state")["strings"]
                    mesh = request(base, "/api/mesh")
                    if mesh["solids"] or mesh["reason"] not in ("empty_document", "no_solid_feature"):
                        raise RuntimeError(f"empty fixture contains a solid: {mesh}")
                    if snap["status"] != strings["viewer3d." + mesh["reason"]]:
                        raise RuntimeError(f"empty 3D workspace is not localized: {snap}")
                    return snap
                pixels = evaluate("window.opencad3d.pixelCount()")
                if pixels < 1000:
                    raise RuntimeError(f"real model not visible: pixels={pixels}")
                if width > 650:
                    if not evaluate("document.getElementById('depth').value='12';"
                                    "document.getElementById('depth').checkValidity()"):
                        raise RuntimeError("valid extrusion depth rejected by browser form")
                    event = trigger("document.getElementById('apply').click()")
                    wait_ready(volume * 2, after_event=event)
                    event = trigger("document.getElementById('undo').click()")
                    wait_ready(volume, after_event=event)
                    depth = evaluate("Number(document.getElementById('depth').value)")
                    if depth != 6:
                        raise RuntimeError(f"undo restored 6 mm solid but input shows {depth} mm")
                    event = trigger("document.getElementById('redo').click()")
                    wait_ready(volume * 2, after_event=event)
                    event = trigger("document.getElementById('undo').click()")
                    wait_ready(volume, after_event=event)
                    before = request(base, "/api/state")["doc"]
                    for locale in ("en", "ja", "zh-CN"):
                        event = trigger(f"document.getElementById('locale').value={json.dumps(locale)};"
                                        "document.getElementById('locale').dispatchEvent(new Event('change'))")
                        wait_ready(volume, after_event=event, locale=locale)
                        strings = request(base, "/api/state")["strings"]
                        accessible = evaluate("document.getElementById('model').getAttribute('aria-label')")
                        if accessible != strings["viewer3d.canvas"]:
                            raise RuntimeError(f"incorrect canvas translation: {locale}")
                    if request(base, "/api/state")["doc"] != before:
                        raise RuntimeError("language change mutated the drawing")
                    labels = evaluate("Array.from(document.querySelectorAll('[data-i18n]')).map(n=>"
                                      "({key:n.getAttribute('data-i18n'),text:n.textContent}))")
                    strings = request(base, "/api/state")["strings"]
                    if any(n["text"] != strings[n["key"]] for n in labels):
                        raise RuntimeError(f"incorrect translated labels: {labels}")
                    names = evaluate("Array.from(document.getElementById('locale').options).map(n=>"
                                     "({code:n.value,text:n.textContent}))")
                    catalogs = request(base, "/api/catalogs")["catalogs"]
                    expected = {item["code"]: item["native_name"] for item in catalogs if item["valid"]}
                    if {item["code"]: item["text"] for item in names} != expected:
                        raise RuntimeError(f"language names differ from catalog: {names}")
                    snap["benchmarks"] = [evaluate("window.opencad3d.benchmark(300)")
                                          for _ in range(3)]
                    # Real pointer input rotates the current GPU-rendered solid.
                    for kind, x, y in (("mousePressed", 700, 350), ("mouseMoved", 750, 380),
                                       ("mouseReleased", 750, 380)):
                        call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                                         "button": "left", "buttons": 0 if kind == "mouseReleased" else 1,
                                                         "clickCount": 1})
                screenshot = call("Page.captureScreenshot", {"format": "png"})
                image = out / f"viewer-{width}.png"
                image.write_bytes(base64.b64decode(screenshot["data"]))
                if image.stat().st_size < 10000:
                    raise RuntimeError(f"screenshot missing content: {image}")
                return dict(snap, pixels=pixels, screenshot=str(image))
            finally:
                stop.set()
                sampler.join(timeout=3)
                write(out / f"gpu-samples-{suffix}.json", samples)
                write(out / f"state-snapshots-{suffix}.json", snapshots)
                if ws is not None:
                    try:
                        helper._cdp_call(ws, sequence, "Browser.close", {}, 5)
                    except (OSError, RuntimeError, TimeoutError):
                        # Browser.close can close the socket before its response.
                        pass
                    ws.sock.close()
                # Chromium children can outlive Browser.close and still write Default/.
                # Terminate only this isolated browser group before removing its profile.
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def main():
    global ROOT, APP
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--angle", choices=("vulkan", "gl"), default="vulkan")
    parser.add_argument("--out", default="/hf3fs-jd/data/tmp/opencad-3d-green-20261010")
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="Read product resources directly from this tree; no overlay.")
    args = parser.parse_args()
    ROOT = args.source_root.resolve(strict=True)
    APP = ROOT / "oss-alternatives/apps/opencad"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = Path(args.out) / (stamp + ("-hardware" if args.hardware else "-software"))
    out.mkdir(parents=True)
    probe = out / ".write-probe"
    probe.touch(exist_ok=False)
    probe.unlink()
    acceptance = load("web3d_source_manifest", ROOT / "oss-alternatives/tools/cad3d/opencad_acceptance.py")
    acceptance.ROOT = ROOT
    acceptance.CORE = ROOT / "oss-alternatives/packages/cadcore"
    source_before = acceptance.source_fingerprint()
    tool = Path(__file__).resolve()
    tool_sha = hashlib.sha256(tool.read_bytes()).hexdigest()
    os.environ["TMPDIR"] = str(out)
    tempfile.tempdir = None
    os.environ["OPENCAD_CADCORE_ROOT"] = str(ROOT / "oss-alternatives/packages/cadcore")
    sys.path.insert(0, str(APP))
    server = load("web3d_server", APP / "server.py")
    httpd = server.build_server(None, host="127.0.0.1", port=0, locale="zh-CN",
                                standard="gbt", out_root=str(out))
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = "http://127.0.0.1:" + str(httpd.server_port)
    result = {"time": stamp, "hardware_required": args.hardware, "base": base,
              "source_root": str(ROOT), "source": source_before,
              "tool": str(tool), "tool_sha256": tool_sha}
    try:
        result["empty_workspace"] = browser(args, base, out, 1440, empty=True)
        for a, b in (((0, 0), (120, 0)), ((120, 0), (120, 80)),
                     ((120, 80), (0, 80)), ((0, 80), (0, 0))):
            request(base, "/api/command", {"command": "add_line", "args": {
                "p1": [v * 1_000_000 for v in a], "p2": [v * 1_000_000 for v in b]}})
        request(base, "/api/command", {"command": "add_circle", "args": {
            "center": [60_000_000, 40_000_000], "radius_nm": 8_000_000}})
        request(base, "/api/command", {"command": "extrude", "args": {"distance_nm": 6_000_000}})
        result["views"] = []
        for width in (1440, 420):
            result["views"].append(browser(args, base, out, width))
        result["verdict"] = "PASS"
    except Exception as exc:
        result["verdict"] = "FAIL"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join()
        source_after = acceptance.source_fingerprint()
        result["source_unchanged"] = source_before == source_after
        result["tool_unchanged"] = tool_sha == hashlib.sha256(tool.read_bytes()).hexdigest()
        if not result["source_unchanged"] or not result["tool_unchanged"]:
            result["verdict"] = "FAIL"
            result["source_changes"] = [
                key for key in sorted(source_before["files"].keys() | source_after["files"].keys())
                if source_before["files"].get(key) != source_after["files"].get(key)]
        write(out / "result.json", result)
        summary = (f"OPENCAD_WEB3D verdict={result['verdict']} hardware={args.hardware} "
                   f"time={stamp} evidence={out}")
        (out / "browser-check.log").write_text(summary + "\n" + result.get("error", "") + "\n")
        print(summary, flush=True)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
