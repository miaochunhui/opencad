"""Capture a live page through CDP after actual bootstrap, without virtual time."""

import base64
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def capture(executable, base, out, shot):
    helper = Path(__file__).resolve().parents[2] / "apps/opencad/tests/legacy_browser_compat_gate.py"
    spec = importlib.util.spec_from_file_location("cad_ui_cdp", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="cad-render-", dir="/tmp") as cache:
        profile = Path(cache)
        with (out / "browser-render.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([
                executable, "--headless", "--disable-gpu", "--no-sandbox",
                "--disable-dev-shm-usage", "--remote-debugging-port=0",
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--window-size=1400,900", "about:blank",
            ], env=dict(os.environ, TMPDIR=cache), stdout=log, stderr=subprocess.STDOUT,
               start_new_session=True)
            ws = None
            try:
                active = profile / "DevToolsActivePort"
                deadline = time.monotonic() + 20
                while not active.exists():
                    if proc.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("browser did not start; see browser-render.log")
                    time.sleep(0.1)
                debug = "http://127.0.0.1:" + active.read_text().splitlines()[0]
                with urllib.request.urlopen(debug + "/json/list", timeout=10) as response:
                    tabs = json.load(response)
                ws = module._WSClient(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"])
                sequence = {"id": 0}

                def call(method, params):
                    result = module._cdp_call(ws, sequence, method, params, 10)
                    if "error" in result or "exceptionDetails" in result.get("result", {}):
                        raise RuntimeError(f"browser operation failed: {result}")
                    return result["result"]

                call("Runtime.enable", {})
                call("Page.navigate", {"url": base + "/"})
                deadline = time.monotonic() + 20
                while True:
                    ready = call("Runtime.evaluate", {
                        "expression": """(function(){
                          var c=document.getElementById('canvas'),s=document.getElementById('lang-select');
                          return !!(c && c.width>200 && c.height>200 && s && s.options.length>=3 &&
                            Array.from(document.querySelectorAll('[data-i18n]')).every(n=>n.textContent.trim()));
                        })()""", "returnByValue": True,
                    })["result"].get("value")
                    if ready:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("live page bootstrap incomplete")
                    time.sleep(0.1)
                dom = call("Runtime.evaluate", {"expression": "document.documentElement.outerHTML",
                                                "returnByValue": True})["result"]["value"]
                (out / "browser-dom.txt").write_text(dom, encoding="utf-8")
                image = call("Page.captureScreenshot", {"format": "png"})
                shot.write_bytes(base64.b64decode(image["data"]))
                return dom
            finally:
                if ws is not None:
                    try:
                        module._cdp_call(ws, sequence, "Browser.close", {}, 5)
                    except (OSError, RuntimeError, TimeoutError):
                        pass
                    ws.sock.close()
                # Chromium's renderer/cache workers can outlive its main process.
                # Stop the owned group before TemporaryDirectory removes the profile.
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
