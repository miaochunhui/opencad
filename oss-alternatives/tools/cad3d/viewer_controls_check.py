#!/usr/bin/env python3
"""Real isolated WebGL view/control acceptance, with CDP input and PNG pixels.

BrowserSession(browser, out, width=1440, height=900) is a reusable context
manager with call/evaluate/wait/click/key/mouse/screenshot methods. It uses
the existing read-only CDP transport. Profiles/sockets are disposable local
resources; all durable evidence and real L1 fixture files remain on 3FS.
No renderer mocks, production services, browser downloads or source rewrites.
--source-root selects the read-only service, web assets, cadcore and tests;
it defaults to this runner's repository, not the current working directory.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "oss-alternatives/apps/opencad"
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
DEFAULT_OUT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-views")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module cannot be loaded: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


class BrowserSession:
    """Own exactly one software Chromium group; all input uses actual CDP."""

    def __init__(self, browser, out, *, width=1440, height=900, source_root=ROOT):
        self.browser, self.out = str(browser), Path(out)
        self.app = Path(source_root).resolve() / "oss-alternatives/apps/opencad"
        self.width, self.height = width, height
        self.ws, self.proc, self.profile, self.log = None, None, None, None
        self.sequence = {"id": 0}
        self.events = []

    def __enter__(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.profile = tempfile.TemporaryDirectory(prefix="opencad-controls-", dir="/tmp")
        directory = Path(self.profile.name)
        self.log = (self.out / "browser.log").open("w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen([
                self.browser, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                "--remote-debugging-port=0", "--user-data-dir=" + str(directory),
                "--no-first-run", "--no-default-browser-check",
                "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank",
            ], env=dict(os.environ, TMPDIR=str(directory)), stdout=self.log,
                stderr=subprocess.STDOUT, start_new_session=True)
            active = directory / "DevToolsActivePort"
            deadline = time.monotonic() + 30
            while not active.exists():
                if self.proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("browser startup failed: %s" % (self.out / "browser.log"))
                time.sleep(0.1)
            import urllib.request
            debug = "http://127.0.0.1:" + active.read_text().splitlines()[0]
            with urllib.request.urlopen(debug + "/json/list", timeout=10) as response:
                tabs = json.load(response)
            self.helper = load("viewer_controls_cdp", self.app / "tests/legacy_browser_compat_gate.py")
            self.ws = self.helper._WSClient(next(
                tab for tab in tabs if tab["type"] == "page")["webSocketDebuggerUrl"])
            self.call("Runtime.enable", {})
            self.call("Page.enable", {})
            self.call("Emulation.setDeviceMetricsOverride", {
                "width": self.width, "height": self.height, "deviceScaleFactor": 1,
                "mobile": self.width < 650,
            })
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, tb):
        # Only the owned process group is terminated; unrelated browsers stay up.
        if self.ws is not None:
            self.ws.sock.close()
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.log is not None:
            self.log.close()
        write(self.out / "cdp-events.json", self.events)
        if self.profile is not None:
            self.profile.cleanup()

    def call(self, method, params):
        response = self.helper._cdp_call(self.ws, self.sequence, method, params, 10)
        self.events.append({"time": datetime.now(timezone.utc).isoformat(),
                            "method": method,
                            "params": params if method.startswith("Input.") else {},
                            "ok": "error" not in response})
        if "error" in response or "exceptionDetails" in response.get("result", {}):
            raise RuntimeError("CDP %s failed: %s" % (method, response))
        return response["result"]

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })["result"]
        return result.get("value")

    def wait(self, expression, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            value = self.evaluate(expression)
            if value:
                return value
            if time.monotonic() >= deadline:
                raise RuntimeError("browser condition not met: " + expression[:200])
            time.sleep(0.1)

    def mouse(self, kind, x, y, *, button="left", buttons=None, modifiers=0, **extra):
        params = {"type": kind, "x": x, "y": y, "button": button,
                  "buttons": (0 if kind == "mouseReleased" else 1) if buttons is None else buttons,
                  "modifiers": modifiers, "clickCount": 1}
        params.update(extra)
        return self.call("Input.dispatchMouseEvent", params)

    def click(self, selector):
        point = self.evaluate("(function(){var n=document.querySelector(%s);"
                              "if(!n)throw Error('missing click target');"
                              "n.scrollIntoView({block:'center'});var r=n.getBoundingClientRect();"
                              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % json.dumps(selector))
        self.mouse("mousePressed", **point)
        self.mouse("mouseReleased", **point)

    def key(self, key, *, modifiers=0):
        code = "Home" if key == "Home" else "Key" + key.upper()
        virtual = 36 if key == "Home" else ord(key.upper())
        self.call("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": key, "code": code, "modifiers": modifiers,
            "windowsVirtualKeyCode": virtual,
            "text": key if len(key) == 1 and not modifiers else "",
        })
        self.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key, "code": code, "modifiers": modifiers,
            "windowsVirtualKeyCode": virtual,
        })

    def screenshot(self, name):
        image = self.call("Page.captureScreenshot", {"format": "png"})
        path = self.out / (name + ".png")
        path.write_bytes(base64.b64decode(image["data"]))
        if path.stat().st_size < 1000:
            raise RuntimeError("empty screenshot: %s" % path)
        return path


class ControlsHarness:
    """Real server + non-symmetric stepped B-Rep for independent view rulers."""

    def __init__(self, out, browser=DEFAULT_BROWSER, *, source_root=ROOT):
        self.out, self.browser = Path(out), browser
        self.root = Path(source_root).resolve()
        self.app = self.root / "oss-alternatives/apps/opencad"
        self.cadcore = self.root / "oss-alternatives/packages/cadcore"

    def __enter__(self):
        self.out.mkdir(parents=True, exist_ok=True)
        self.old_tmp, self.old_path = tempfile.tempdir, sys.path[:]
        self.old_env = {key: os.environ.get(key) for key in
                        ("TMPDIR", "QT_QPA_PLATFORM", "OPENCAD_CADCORE_ROOT")}
        self.old_bytecode = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        scratch = self.out / "scratch"
        scratch.mkdir()
        tempfile.tempdir = str(scratch)
        os.environ["TMPDIR"] = str(scratch)
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["OPENCAD_CADCORE_ROOT"] = str(self.cadcore)
        sys.path[:0] = [str(self.cadcore), str(self.app)]
        self.httpd, self.worker = None, None
        self.session = None
        try:
            for name, module in tuple(sys.modules.items()):
                if name == "cadcore" or name.startswith("cadcore."):
                    path = getattr(module, "__file__", None)
                    if path and not Path(path).resolve().is_relative_to(self.cadcore):
                        raise RuntimeError("cadcore already loaded outside source-root: " + path)
            self.reference = load("viewer_controls_reference", self.root / "oss-alternatives/tools/cad3d/web3d_browser_check.py")
            self.server = load("viewer_controls_server", self.app / "server.py")
            self.httpd = self.server.build_server(
                None, host="127.0.0.1", port=0, locale="en", standard="iso", out_root=str(self.out))
            self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.worker.start()
            self.base = "http://127.0.0.1:" + str(self.httpd.server_port)
            self.make_fixture()
            self.session = BrowserSession(self.browser, self.out / "browser", source_root=self.root)
            self.b = self.session.__enter__()
            self.b.call("Page.navigate", {"url": self.base + "/web/3d.html"})
            self.ready()
            self.loaded_viewer = self.viewer_resource()
            self.loaded_css = self.page_resource("/web/css/viewer3d.css", "Stylesheet")
            self.pixels("desktop-initial")
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc, tb):
        if self.session is not None:
            self.session.__exit__(exc_type, exc, tb)
        if self.httpd is not None:
            if self.worker is not None:
                self.httpd.shutdown()
                self.worker.join()
            self.httpd.server_close()
        tempfile.tempdir = self.old_tmp
        sys.path[:] = self.old_path
        sys.dont_write_bytecode = self.old_bytecode
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def viewer_resource(self):
        """Fingerprint the script resource actually loaded by this page."""
        return self.page_resource("/web/js/viewer3d.js", "Script")

    def page_resource(self, path, kind):
        tree = self.b.call("Page.getResourceTree", {})["frameTree"]
        url = self.base + path
        if not any(item["url"] == url and item["type"] == kind
                   for item in tree["resources"]):
            raise AssertionError("target resource is not loaded by browser: " + url)
        resource = self.b.call("Page.getResourceContent", {
            "frameId": tree["frame"]["id"], "url": url})
        raw = (base64.b64decode(resource["content"]) if resource["base64Encoded"]
               else resource["content"].encode("utf-8"))
        return {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    def runtime_sources(self):
        paths = {"server": str(Path(self.server.__file__).resolve()),
                 "reference": str(Path(self.reference.__file__).resolve()),
                 "cdp_helper": str(Path(self.b.helper.__file__).resolve())}
        for name, module in tuple(sys.modules.items()):
            if name == "cadcore" or name.startswith("cadcore."):
                path = getattr(module, "__file__", None)
                if path:
                    actual = Path(path).resolve()
                    if not actual.is_relative_to(self.cadcore):
                        raise AssertionError("runtime cadcore outside source-root: " + str(actual))
                    paths[name] = str(actual)
        return paths

    def request(self, path, body=None):
        return self.reference.request(self.base, path, body)

    def make_fixture(self):
        # Build the base via actual public commands, then persist an L1 second
        # sketch/feature before opening it through the real service.
        for a, b in (((0, 0), (120, 0)), ((120, 0), (120, 80)),
                     ((120, 80), (0, 80)), ((0, 80), (0, 0))):
            self.request("/api/command", {"command": "add_line", "args": {
                "p1": [v * 1000000 for v in a], "p2": [v * 1000000 for v in b]}})
        self.request("/api/command", {"command": "extrude", "args": {"distance_nm": 30000000}})
        doc = json.loads(self.request("/api/state")["doc"])
        sketch = {"uuid": "913c4d82-c2e0-4bcd-bd58-8d4ec1338498", "plane": "XY", "entities": [{
            "uuid": "da902f9c-5c3d-47ee-a773-af74c4cc849c", "kind": "polyline",
            "geom": {"points": [[0, 0], [30000000, 0], [30000000, 20000000],
                                [0, 20000000], [0, 0]]},
        }]}
        doc["sketches"].append(sketch)
        doc["features"].append({"uuid": "fb553fb8-ac2a-42af-9d04-774c5472c898", "kind": "extrude",
                                "sketch_uuid": sketch["uuid"],
                                "params": {"distance_nm": 50000000, "op": "add"}})
        write(self.out / "fixture.json", doc)
        self.request("/api/open?path=fixture.json")
        mesh = self.request("/api/mesh")
        write(self.out / "fixture-mesh.json", mesh)
        solid = mesh["solids"][0]
        if solid["solid_count"] != 1 or abs(solid["properties"]["volume_nm3"] / 1e18 - 300000) > 1e-5:
            raise AssertionError("fixture must be a real 300000 mm3 stepped solid")
        self.expected_doc = self.request("/api/state")["doc"]
        self.mesh = solid

    def ready(self, volume=300000):
        return self.b.wait("(function(){var m=window.opencad3d;"
                           "if(m&&m.error)throw Error(m.error);"
                           "return m&&m.frames>0&&m.triangles>0&&Math.abs(m.volume_mm3-%s)<0.001;})()" % volume)

    def snapshot(self):
        return self.b.evaluate("(function(){var m=window.opencad3d;return {yaw:m.yaw,"
                               "pitch:m.pitch,view:m.view,zoom:m.zoom,pan:m.pan.slice(),"
                               "error:m.error,frames:m.frames};})()")

    def rect(self):
        return self.b.evaluate("(function(){var r=document.getElementById('model').getBoundingClientRect();"
                               "return {x:r.x,y:r.y,width:r.width,height:r.height};})()")

    def frame(self):
        self.b.evaluate("window.opencad3d.draw()")
        self.b.evaluate("new Promise(function(r){requestAnimationFrame(function(){requestAnimationFrame(r);});})")

    def view(self, name):
        self.b.evaluate("window.opencad3d.setView(%s)" % json.dumps(name))
        self.frame()

    def pixels(self, name):
        from PIL import Image
        self.frame()
        path = self.b.screenshot(name)
        rect = self.rect()
        viewport = self.b.evaluate("({width:innerWidth,height:innerHeight,"
                                  "scroll_x:scrollX,scroll_y:scrollY,"
                                  "document_width:document.documentElement.scrollWidth,"
                                  "document_height:document.documentElement.scrollHeight})")
        aside = self.b.evaluate("(function(){var n=document.querySelector('aside');"
                                "var r=n.getBoundingClientRect();return {x:r.x,y:r.y,"
                                "width:r.width,height:r.height,scroll_top:n.scrollTop,"
                                "client_height:n.clientHeight,scroll_height:n.scrollHeight};})()")
        visible = [max(0, rect["x"]), max(0, rect["y"]),
                   min(viewport["width"], rect["x"] + rect["width"]),
                   min(viewport["height"], rect["y"] + rect["height"])]
        area = max(0, visible[2] - visible[0]) * max(0, visible[3] - visible[1])
        fraction = area / (rect["width"] * rect["height"]) if rect["width"] * rect["height"] else 0
        with Image.open(path) as full:
            dimensions = [full.width, full.height]
            sx, sy = full.width / viewport["width"], full.height / viewport["height"]
            image = full.convert("RGB").crop((
                round(rect["x"] * sx), round(rect["y"] * sy),
                round((rect["x"] + rect["width"]) * sx),
                round((rect["y"] + rect["height"]) * sy)))
        found = [(x, y) for y in range(image.height) for x in range(image.width)
                 if image.getpixel((x, y))[0] < 210
                 and image.getpixel((x, y))[2] > image.getpixel((x, y))[0] + 25]
        result = {"time": datetime.now(timezone.utc).isoformat(),
                  "screenshot": str(path), "screenshot_dimensions": dimensions,
                  "pixels": len(found), "bbox_px": None,
                  "screenshot_scale": [sx, sy], "canvas_rect": rect, "viewport": viewport,
                  "aside_rect": aside, "visible_rect": visible,
                  "visible_fraction": fraction, "canvas_fully_visible": fraction >= 1 - 1e-6}
        # Write before asserting so offscreen/empty failures retain real bounds.
        write(self.out / (name + "-pixels.json"), result)
        if not result["canvas_fully_visible"] or rect["height"] < 240:
            raise AssertionError("canvas not fully visible without scrolling: %s" % result)
        if len(found) < 1000:
            raise AssertionError("GPU screenshot has no real solid pixels: %s" % result)
        xs, ys = zip(*found)
        box = [min(xs), min(ys), max(xs), max(ys)]
        width, height = box[2] - box[0] + 1, box[3] - box[1] + 1
        upper_left = sum(1 for x, y in found if x < box[0] + width/2 and y < box[1] + height*0.3)
        upper_right = sum(1 for x, y in found if x >= box[0] + width/2 and y < box[1] + height*0.3)
        result.update(bbox_px=box, ratio=width/height,
                      upper_left=upper_left, upper_right=upper_right)
        write(self.out / (name + "-pixels.json"), result)
        return result

    def drag(self, *, shift=False, dx=70, dy=35):
        rect = self.rect()
        x, y = rect["x"] + rect["width"]/2, rect["y"] + rect["height"]/2
        self.b.mouse("mousePressed", x, y, modifiers=8 if shift else 0)
        self.b.mouse("mouseMoved", x + dx, y + dy, modifiers=8 if shift else 0)
        self.b.mouse("mouseReleased", x + dx, y + dy, modifiers=8 if shift else 0)
        self.frame()

    def wheel(self, delta=-240):
        rect = self.rect()
        x, y = rect["x"] + rect["width"]/2, rect["y"] + rect["height"]/2
        self.b.evaluate("window._wheelTrace=[];document.addEventListener('wheel',function(e){"
                        "window._wheelTrace.push({target:e.target.id,delta:e.deltaY,"
                        "x:e.clientX,y:e.clientY});},{once:true,capture:true})")
        self.b.mouse("mouseMoved", x, y, button="none", buttons=0)
        self.b.mouse("mouseWheel", x, y,
                     button="none", buttons=0, deltaX=0, deltaY=delta)
        try:
            self.b.wait("window.opencad3d.zoom !== 1", timeout=3)
        except RuntimeError as exc:
            trace = self.b.evaluate("({events:window._wheelTrace,"
                                    "hit:document.elementFromPoint(%s,%s).id,"
                                    "zoom:window.opencad3d.zoom})" % (x, y))
            write(self.out / "wheel-failure.json", trace)
            raise RuntimeError("real wheel failed: %s" % trace) from exc
        self.frame()


class RecordedResult(unittest.TextTestResult):
    """Retain each actual unittest outcome, not a manually declared PASS."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verdicts = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.verdicts[test._testMethodName] = "PASS"

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.verdicts[test._testMethodName] = "FAIL"

    def addError(self, test, err):
        super().addError(test, err)
        self.verdicts[test._testMethodName] = "ERROR"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.verdicts[test._testMethodName] = "SKIP"


def source_audit(run, manifest, baseline_viewer, *, source_root=ROOT):
    """Verify the dispatched overlay and emit only the viewer's local hunks."""
    sources = json.loads(Path(manifest).read_text(encoding="utf-8"))
    viewer = "oss-alternatives/apps/opencad/web/js/viewer3d.js"
    changed = []
    for relative, expected in sources.items():
        path = source_root / relative
        if not path.is_file():
            changed.append({"path": relative, "reason": "missing"})
        else:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                changed.append({"path": relative, "before": expected, "after": actual})
    unexpected = [item for item in changed if item["path"] != viewer]
    audit = {"checked": len(sources), "changed": changed, "unexpected": unexpected}
    write(run / "source-audit.json", audit)
    if unexpected:
        raise AssertionError("unexpected dispatched overlay changes: %s" % unexpected)
    baseline = Path(baseline_viewer).read_bytes()
    if hashlib.sha256(baseline).hexdigest() != sources[viewer]:
        raise AssertionError("viewer patch baseline differs from dispatched snapshot")
    patch = "".join(difflib.unified_diff(
        baseline.decode("utf-8").splitlines(keepends=True),
        (source_root / viewer).read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile="a/" + viewer, tofile="b/" + viewer,
    ))
    if not patch.strip():
        raise AssertionError("viewer patch must not be empty")
    patch_path = run / "viewer3d-controls.patch"
    patch_path.write_text(patch, encoding="utf-8")
    return {"source_audit": str(run / "source-audit.json"), "patch": str(patch_path)}


def source_hashes(root, test_path):
    """Bind actual product inputs; exclude only reproducible bytecode/caches."""
    app = root / "oss-alternatives/apps/opencad"
    core = root / "oss-alternatives/packages/cadcore/cadcore"
    paths = {app / "server.py", Path(test_path).resolve(),
             app / "tests/legacy_browser_compat_gate.py",
             root / "oss-alternatives/tools/cad3d/web3d_browser_check.py"}
    paths.update(app.glob("*.py"))
    for directory in (app / "web", core):
        if not directory.is_dir():
            raise ValueError("source-root missing product directory: " + str(directory))
        paths.update(path for path in directory.rglob("*") if path.is_file()
                     and path.suffix in (".py", ".js", ".css", ".html", ".json")
                     and not {"__pycache__", ".pytest_cache"}.intersection(path.parts))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def run_discovery(*, source_root=ROOT, out=DEFAULT_OUT):
    """Run ordinary unittest discovery in a child, with durable real outcomes."""
    root = Path(source_root).resolve()
    app = root / "oss-alternatives/apps/opencad"
    test_path = app / "tests/test_viewer3d_controls.py"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run = Path(out) / ("discovery-" + stamp)
    run.mkdir(parents=True, exist_ok=False)
    before = source_hashes(root, test_path)
    command = [sys.executable, "-B", "-m", "unittest", "discover",
               "-s", "tests", "-p", "test_viewer3d_controls.py", "-v"]
    result = {"time": stamp, "verdict": "FAIL", "source_root": str(root),
              "command": command, "cwd": str(app), "evidence": str(run),
              "source_hashes_before": before}
    started = time.monotonic()
    with (run / "tests.log").open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(command, cwd=app, stdout=log, stderr=subprocess.STDOUT,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                           TMPDIR=str(run)), check=False)
            log.flush()
            text = (run / "tests.log").read_text(encoding="utf-8")
            count = re.findall(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
            checks = [{"test": name, "verdict": {"ok": "PASS", "FAIL": "FAIL",
                       "ERROR": "ERROR", "skipped": "SKIP"}[status]}
                      for name, status in re.findall(
                          r"^(test_\w+) \([^\n]+\) \.\.\. (ok|FAIL|ERROR|skipped)",
                          text, re.MULTILINE)]
            tests = int(count[-1]) if count else -1
            after = source_hashes(root, test_path)
            drift = [path for path in sorted(before.keys() | after.keys())
                     if before.get(path) != after.get(path)]
            ok = (proc.returncode == 0 and tests == 13 and len(checks) == tests
                  and all(check["verdict"] == "PASS" for check in checks) and not drift)
            result.update(verdict="PASS" if ok else "FAIL", process_rc=proc.returncode,
                          tests=tests, checks=checks,
                          failures=sum(c["verdict"] == "FAIL" for c in checks),
                          errors=sum(c["verdict"] == "ERROR" for c in checks),
                          skips=sum(c["verdict"] == "SKIP" for c in checks),
                          source_hashes=after, source_drift=drift, source_unchanged=not drift,
                          source_sha256=before["oss-alternatives/apps/opencad/web/js/viewer3d.js"])
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            traceback.print_exc(file=log)
    result["wall_s"] = time.monotonic() - started
    write(run / "result.json", result)
    print("VIEWER_DISCOVERY verdict=%s tests=%s time=%s evidence=%s" %
          (result["verdict"], result.get("tests", -1), stamp, run), flush=True)
    if result["verdict"] != "PASS":
        print((run / "tests.log").read_text(encoding="utf-8")[-18000:])
    return 0 if result["verdict"] == "PASS" else 1


def run_tests(test_path, *, out=DEFAULT_OUT, browser=DEFAULT_BROWSER, pattern="",
              manifest=None, baseline_viewer=None, source_root=ROOT):
    source_root = Path(source_root).resolve()
    test_path = Path(test_path).resolve()
    out = Path(out)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run = out / ("run-" + stamp)
    run.mkdir(parents=True)
    started = time.monotonic()
    runner = Path(__file__).resolve()
    result = {"time": stamp, "verdict": "FAIL", "evidence": str(run),
              "source_root": str(source_root), "test_path": str(test_path),
              "runner": {"path": str(runner),
                         "sha256": hashlib.sha256(runner.read_bytes()).hexdigest()}}
    before = None
    with (run / "tests.log").open("w", encoding="utf-8") as log:
        try:
            before = source_hashes(source_root, test_path)
            result["source_hashes_before"] = before
            with redirect_stdout(log), redirect_stderr(log), ControlsHarness(
                    run, browser, source_root=source_root) as harness:
                viewer = "oss-alternatives/apps/opencad/web/js/viewer3d.js"
                result["browser_viewer"] = harness.loaded_viewer
                if harness.loaded_viewer["sha256"] != before[viewer]:
                    raise AssertionError("loaded browser viewer differs from target source fingerprint")
                result["browser_css"] = harness.loaded_css
                if harness.loaded_css["sha256"] != before["oss-alternatives/apps/opencad/web/css/viewer3d.css"]:
                    raise AssertionError("loaded browser CSS differs from target source fingerprint")
                module = load("viewer_controls_tests", test_path)
                module.HARNESS = harness
                loader = unittest.TestLoader()
                if pattern:
                    loader.testNamePatterns = ["*" + pattern + "*"]
                suite = loader.loadTestsFromModule(module)
                tested = unittest.TextTestRunner(
                    stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
                result["runtime_sources"] = harness.runtime_sources()
                ok = tested.wasSuccessful() and tested.testsRun > 0 and not tested.skipped
                result.update(verdict="PASS" if ok else "FAIL", tests=tested.testsRun,
                              failures=len(tested.failures), errors=len(tested.errors),
                              skips=len(tested.skipped), checks=[
                                  dict(item, verdict=tested.verdicts.get(item["test"], "ERROR"))
                                  for item in module.RESULTS],
                              renderer=harness.b.evaluate("window.opencad3d.renderer"))
                result["source_sha256"] = before[viewer]
                if manifest is not None or baseline_viewer is not None:
                    if manifest is None or baseline_viewer is None:
                        raise ValueError("source audit needs manifest and baseline-viewer together")
                    result.update(source_audit(run, manifest, baseline_viewer,
                                               source_root=source_root))
                if source_root != ROOT:
                    relative = "oss-alternatives/tools/cad3d/viewer_controls_check.py"
                    baseline = source_root / relative
                    patch = "".join(difflib.unified_diff(
                        baseline.read_text(encoding="utf-8").splitlines(keepends=True),
                        runner.read_text(encoding="utf-8").splitlines(keepends=True),
                        fromfile="a/" + relative, tofile="b/" + relative))
                    if patch:
                        patch_path = run / "viewer-controls-source-root.patch"
                        patch_path.write_text(patch, encoding="utf-8")
                        result["runner_patch"] = str(patch_path)
                    relative = "oss-alternatives/apps/opencad/tests/test_viewer3d_controls.py"
                    patch = "".join(difflib.unified_diff(
                        (source_root / relative).read_text(encoding="utf-8").splitlines(keepends=True),
                        (ROOT / relative).read_text(encoding="utf-8").splitlines(keepends=True),
                        fromfile="a/" + relative, tofile="b/" + relative))
                    if patch:
                        patch_path = run / "viewer-controls-discovery.patch"
                        patch_path.write_text(patch, encoding="utf-8")
                        result["discovery_patch"] = str(patch_path)
        except Exception as exc:
            result["verdict"] = "FAIL"
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            traceback.print_exc(file=log)
        finally:
            if before is not None:
                try:
                    after = source_hashes(source_root, test_path)
                    result["source_hashes"] = after
                    drift = [{"path": path, "before": before.get(path), "after": after.get(path)}
                             for path in sorted(before.keys() | after.keys())
                             if before.get(path) != after.get(path)]
                    result.update(source_drift=drift, source_unchanged=not drift)
                    if drift:
                        result["verdict"] = "FAIL"
                        result["source_error"] = "target product sources changed during acceptance"
                except Exception as exc:
                    result.update(verdict="FAIL", source_unchanged=False,
                                  source_error="%s: %s" % (type(exc).__name__, exc))
                    traceback.print_exc(file=log)
    result["wall_s"] = time.monotonic() - started
    result["canvas_evidence"] = [str(path) for path in sorted(run.glob("*-pixels.json"))]
    write(run / "result.json", result)
    print("VIEWER_CONTROLS verdict=%s tests=%s time=%s evidence=%s" %
          (result["verdict"], result.get("tests", -1), stamp, run), flush=True)
    if result["verdict"] != "PASS":
        print((run / "tests.log").read_text(encoding="utf-8")[-18000:])
    return 0 if result["verdict"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="read-only target repository for service, assets, cadcore and tests")
    parser.add_argument("--discovery", action="store_true",
                        help="run all 13 tests through ordinary target unittest discovery")
    parser.add_argument("-k", default="")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline-viewer", type=Path)
    args = parser.parse_args()
    if args.discovery:
        return run_discovery(source_root=args.source_root, out=args.out)
    return run_tests(args.source_root.resolve() / "oss-alternatives/apps/opencad/tests/test_viewer3d_controls.py",
                     out=args.out, browser=args.browser, pattern=args.k,
                     manifest=args.manifest, baseline_viewer=args.baseline_viewer,
                     source_root=args.source_root)


if __name__ == "__main__":
    raise SystemExit(main())
