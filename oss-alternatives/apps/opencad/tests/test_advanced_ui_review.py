"""Independent advanced UI review against the tree containing this file.

Default: static contracts only, with explicit browser NOT_RUN. After the main
agent confirms all feature modules are ready, use --browser-ready to exercise
real Chromium, real HTTP commands and delayed real requests, not fake replies.
All logs, measurements and screenshots belong to the external evidence root.
Unittest discovery runs all 21 static/browser contracts without dependency skips.
The server, viewer, page and CAD modules are read-only sources in the selected
tree; --source-root explicitly selects another tree, and --advanced-source is
an optional script overlay. Discovery and direct runs keep external evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "oss-alternatives/apps/opencad"
CORE = ROOT / "oss-alternatives/packages/cadcore"
OUT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-ui-review")
BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
ADVANCED = APP / "web/js/advanced3d.js"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    if result.get("ok") is not True:
        raise AssertionError(f"real HTTP rejected {path}: {result}")
    return result


class StaticReviewTests(unittest.TestCase):
    def setUp(self):
        self.advanced = ADVANCED.read_text()
        self.viewer = (APP / "web/js/viewer3d.js").read_text()
        self.page = load("ui_review_page", APP / "viewer3d_page.py").render(advanced=True)

    def test_page_exposes_all_advanced_operations_once(self):
        commands = ("sweep_solid", "loft_solid", "fillet_solid", "chamfer_solid",
                    "shell_solid", "pattern_solid", "mirror_solid",
                    "import_step", "edit_feature_params", "measure_clearance")
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.page.count('data-solid="' + command + '"'), 1)
                self.assertIn('operation === "' + command + '"', self.advanced)

    def test_three_catalogs_cover_dynamic_tree_and_page_keys(self):
        keys = set(re.findall(r'data-i18n="([^"]+)"', self.page))
        keys.update("solid." + kind for kind in (
            "sweep", "loft", "fillet", "chamfer", "shell", "pattern",
            "mirror", "import_step", "extrude", "revolve", "suppress", "resume", "edit",
            "clearance", "separated", "touching", "penetrating"))
        solid_sets = []
        for locale in ("en", "ja", "zh-CN"):
            catalog = json.loads((CORE / "cadcore/data/i18n" / (locale + ".json")).read_text())
            strings = catalog["strings"]
            solid_sets.append({key for key in strings if key.startswith("solid.")})
            for key in keys:
                with self.subTest(locale=locale, key=key):
                    self.assertTrue(strings.get(key), "missing translation: " + key)
            self.assertIn("[0,0,0]", strings["solid.translations"])
        self.assertEqual(solid_sets[0], solid_sets[1])
        self.assertEqual(solid_sets[0], solid_sets[2])

    def test_async_refresh_is_completion_driven_not_fixed_delay(self):
        self.assertIsNone(
            re.search(r"setTimeout\s*\(\s*refresh\s*,\s*\d+", self.advanced),
            "300/700ms reads may precede real locale/undo completion; subscribe to completion")
        self.assertIn('window.addEventListener("opencad:state"', self.advanced)
        self.assertNotIn('"/api/state"', self.advanced)
        self.assertNotIn('"/api/command"', self.advanced)
        self.assertNotIn('panel.querySelectorAll("[data-i18n]")', self.advanced)

    def test_commands_share_one_cross_panel_serialization_boundary(self):
        private_advanced = re.search(r"\bbusy\s*=\s*false", self.advanced)
        private_viewer = re.search(r"\bbusy\s*=\s*false", self.viewer)
        self.assertFalse(private_advanced and private_viewer,
                         "advanced and viewer commands use independent busy flags")
        self.assertIn("return model.command(command, args);", self.advanced)

    def test_scalar_nanometres_do_not_bypass_safe_integer_conversion(self):
        self.assertIsNone(
            re.search(r"(size_nm|thickness_nm|offset_nm)\s*:\s*Math\.round\s*\(\s*Number\s*\(",
                      self.advanced),
            "scalar dimensions bypass finite/safe-integer checks used for point coordinates")

    def test_busy_rejection_uses_error_display_path(self):
        early = re.search(r"if\s*\(busy\)\s*return\s+Promise\.reject\s*\(", self.advanced)
        handled_dom = re.search(
            r"execute\([^;\n]*\)\s*\.catch\s*\(", self.advanced)
        disabled_controls = re.search(r"\.disabled\s*=", self.advanced + self.viewer)
        self.assertFalse(
            early and not handled_dom and not disabled_controls,
            "busy rejection lacks a DOM catch or disabled pending controls")


class BrowserReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = [CORE / "cadcore/kernel" / ("features_" + name + ".py")
                    for name in ("sweep", "loft", "edges", "shell", "pattern", "step", "clearance")]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise AssertionError("browser prerequisite missing, do not launch yet: " + repr(missing))
        cls.helper = load("ui_review_cdp", APP / "tests/legacy_browser_compat_gate.py")
        cls.seq = {"id": 0}
        cls.ws = None
        cls.server = cls.chrome = None
        cls.stack = []
        try:
            web = OUT / "web-overlay"
            cls.main_resources = ADVANCED == APP / "web/js/advanced3d.js"
            if cls.main_resources:
                web = APP / "web"
            else:
                shutil.copytree(APP / "web", web)
                shutil.copyfile(ADVANCED, web / "js/advanced3d.js")
            if (web / "js/advanced3d.js").read_bytes() != ADVANCED.read_bytes():
                raise AssertionError("served advanced differs from reviewed source")
            (OUT / "web-resource-manifest.json").write_text(json.dumps({
                "mode": "MAIN_DIRECT" if cls.main_resources else "ADVANCED_OVERLAY",
                "web_root": str(web),
                "files": {str(path.relative_to(web)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(web.rglob("*")) if path.is_file()},
            }, indent=2))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(OUT),
                       OPENCAD_CADCORE_ROOT=str(CORE))
            cls.server_log = (OUT / "server.log").open("w")
            cls.server = subprocess.Popen(
                [sys.executable, "-B", str(APP / "server.py"), "--host", "127.0.0.1",
                 "--port", "0", "--web", str(web), "--out-root", str(OUT / "server-data")],
                cwd=APP, env=env, stdout=cls.server_log, stderr=subprocess.STDOUT,
                start_new_session=True)
            deadline = time.monotonic() + 30
            while True:
                text = (OUT / "server.log").read_text()
                match = re.search(r"OPENCAD_SERVER verdict=PASS port=(\d+)", text)
                if match:
                    cls.base = "http://127.0.0.1:" + match[1]
                    break
                if cls.server.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("isolated server startup failed: " + text[-2000:])
                time.sleep(0.1)
            cls.command("add_circle", {"center": [0, 0], "radius_nm": 2_000_000})
            sketch = json.loads(http(cls.base, "/api/state")["doc"])["sketches"][0]["uuid"]
            cls.command("sweep_solid", {"sketch_uuid": sketch,
                        "path_points_nm": [[0, 0, 0], [0, 0, 10_000_000]]})
            cls.baseline = http(cls.base, "/api/state")["doc"]
            http(cls.base, "/api/save", {"path": "review-baseline.json"})
            mesh = http(cls.base, "/api/mesh")
            cls.volume = mesh["solids"][0]["properties"]["volume_nm3"] / 1e18
            if not mesh["solids"][0]["triangles"] or abs(cls.volume - 40 * 3.141592653589793) > 1e-6:
                raise AssertionError("real sweep fixture has incorrect measured volume or mesh")
            # Local-only browser socket/profile is the documented storage exception.
            cls.profile = tempfile.TemporaryDirectory(prefix="ui-review-chrome-", dir="/tmp")
            env["TMPDIR"] = cls.profile.name
            cls.chrome_log = (OUT / "chrome.log").open("w")
            cls.chrome = subprocess.Popen(
                [BROWSER, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                 "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                 "--remote-debugging-port=0", "--user-data-dir=" + cls.profile.name,
                 "--no-first-run", "--no-default-browser-check", "about:blank"],
                env=env, stdout=cls.chrome_log, stderr=subprocess.STDOUT,
                start_new_session=True)
            port_file = Path(cls.profile.name) / "DevToolsActivePort"
            deadline = time.monotonic() + 30
            while not port_file.exists():
                if cls.chrome.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("Chromium startup failed; see chrome.log")
                time.sleep(0.1)
            debug = "http://127.0.0.1:" + port_file.read_text().splitlines()[0]
            with urllib.request.urlopen(debug + "/json/list", timeout=10) as response:
                tabs = json.load(response)
            cls.ws = cls.helper._WSClient(next(
                tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page"))
            cls.call("Runtime.enable", {})
            served = urllib.request.urlopen(cls.base + "/web/js/advanced3d.js", timeout=10)
            with served:
                if served.read() != ADVANCED.read_bytes():
                    raise AssertionError("real HTTP advanced source differs from selected bytes")
            cls.call("Emulation.setDeviceMetricsOverride",
                     {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def command(cls, command, args=None):
        return http(cls.base, "/api/command", {"command": command, "args": args or {}})

    @classmethod
    def call(cls, method, params):
        message = cls.helper._cdp_call(cls.ws, cls.seq, method, params, 30)
        if "error" in message or "exceptionDetails" in message.get("result", {}):
            raise AssertionError("CDP failure: " + repr(message))
        return message["result"]

    @classmethod
    def evaluate(cls, expression):
        return cls.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })["result"].get("value")

    def wait(self, expression, expected=True):
        deadline = time.monotonic() + 20
        while True:
            value = self.evaluate(expression)
            if value == expected:
                return value
            if time.monotonic() > deadline:
                self.fail("browser condition did not converge: " + expression + " got " + repr(value))
            time.sleep(0.1)

    def setUp(self):
        state = http(self.base, "/api/state")
        if state["doc"] != self.baseline:
            raise AssertionError("previous browser test mutated baseline without restoring it")
        http(self.base, "/api/locale", {"locale": "en"})
        query = "?review=" + str(time.time_ns())
        self.call("Page.navigate", {"url": self.base + "/web/3d.html" + query})
        self.wait("location.search===%s && document.readyState==='complete'" % json.dumps(query))
        self.wait("Boolean(window.opencadAdvanced && window.opencad3d && "
                  "window.opencad3d.triangles > 0 && document.querySelectorAll('#feature-tree li').length===1)")
        self.evaluate("""window.reviewEvents=[];window.reviewUnhandled=[];
          window.addEventListener('unhandledrejection',function(event){
            reviewUnhandled.push(String(event.reason));});
          window.addEventListener('opencad:state',function(event){
            reviewEvents.push({locale:event.detail.locale,doc:event.detail.doc,
              triangles:opencad3d.triangles,busy:Boolean(opencad3d.busy),
              tree:Array.from(document.querySelectorAll('#feature-tree li button')).map(function(b){
                return {label:b.textContent,disabled:b.disabled};})});
          });""")

    def tearDown(self):
        screenshot = self.call("Page.captureScreenshot", {"format": "png"})
        (OUT / (self._testMethodName + ".png")).write_bytes(base64.b64decode(screenshot["data"]))
        self.assertEqual(self.evaluate("reviewUnhandled"), [],
                         "a DOM event handler leaked an unhandled rejected promise")
        self.assertEqual(http(self.base, "/api/state")["doc"], self.baseline,
                         "browser test did not restore the baseline")

    def delay_real_request(self, *, path, command=None):
        self.evaluate("""(function(rule){
          var nativeFetch=window.fetch;
          window.reviewNetwork={active:0,allActive:0,maxActive:0,completed:0,requests:[],reads:[],delays:[],responses:[]};
          window.fetch=function(path,options){
            var body=options&&options.body?JSON.parse(options.body):{};
            var matches=path===rule.path&&(!rule.command||body.command===rule.command);
            var mutating=Boolean(options&&options.method==='POST');
            var started=performance.now();
            reviewNetwork.allActive++;
            if(!mutating){reviewNetwork.reads.push(path);}
            if(mutating){reviewNetwork.active++;reviewNetwork.maxActive=Math.max(
              reviewNetwork.maxActive,reviewNetwork.active);reviewNetwork.requests.push({path:path,body:body});}
            var start=matches?new Promise(function(resolve){setTimeout(resolve,1400);}):Promise.resolve();
            return start.then(function(){return nativeFetch.call(window,path,options);})
              .then(function(response){
                if(path!=='/api/clearance'){return response;}
                return response.clone().json().then(function(value){
                  reviewNetwork.responses.push({path:path,status:response.status,body:value});return response;
                });
              }).finally(function(){
              reviewNetwork.allActive--;
              if(matches){reviewNetwork.delays.push(performance.now()-started);}
              if(mutating){reviewNetwork.active--;reviewNetwork.completed++;}
            });
          };
        })(%s)""" % json.dumps({"path": path, "command": command}))

    def wait_painted_idle(self):
        self.wait("reviewNetwork.allActive", 0)
        self.wait("!window.opencad3d.busy")
        self.evaluate("""new Promise(function(resolve){
          requestAnimationFrame(function(){requestAnimationFrame(function(){resolve(true);});});
        })""")
        self.wait("reviewNetwork.allActive", 0)

    def tree_matches(self):
        state = http(self.base, "/api/state")
        features = json.loads(state["doc"])["features"]
        expected = [{"name": state["strings"]["solid." + f["kind"]] + " / " + f["uuid"][:8],
                     "toggle": state["strings"]["solid.resume" if f.get("suppressed") else "solid.suppress"],
                     "edit": state["strings"]["solid.edit"]} for f in features]
        actual = self.evaluate("""Array.from(document.querySelectorAll('#feature-tree li')).map(function(li){
          return {name:li.querySelector('span').textContent,
            toggle:li.querySelectorAll('button')[0].textContent,
            edit:li.querySelectorAll('button')[1].textContent};})""")
        self.stack.append({"test": self.id(), "locale": state["locale"],
                           "tree_actual": actual, "tree_expected": expected})
        self.assertEqual(actual, expected)
        return actual

    def load_fixture(self, sketches):
        document = json.loads(self.baseline)
        document["features"] = []
        document["sketches"] = sketches
        path = OUT / "server-data" / (self._testMethodName + ".json")
        path.write_text(json.dumps(document))
        http(self.base, "/api/open?path=" + path.name)
        self.evaluate("opencad3d.refresh()")
        self.assertEqual(self.evaluate("opencad3d.triangles"), 0)
        return document

    def restore_fixture(self):
        http(self.base, "/api/open?path=review-baseline.json")
        self.evaluate("opencad3d.refresh()")

    def circle_sketch(self, radius):
        sketch = json.loads(self.baseline)["sketches"][0]
        sketch["uuid"] = str(uuid.uuid4())
        sketch["entities"][0]["uuid"] = str(uuid.uuid4())
        sketch["entities"][0]["geom"]["r"] = int(radius * 1e6)
        return sketch

    def submit_form(self, operation, values, expected_features):
        event_count = self.evaluate("reviewEvents.length")
        self.evaluate("""(function(values){
          var form=document.querySelector('form[data-solid=%s]');
          form.parentElement.open=true;
          Object.keys(values).forEach(function(name){
            var input=form.elements.namedItem(name);
            if(input.type==='checkbox'){input.checked=values[name];}
            else{input.value=values[name];}
          });
          form.querySelector('button').click();
        })(%s)""" % (json.dumps(operation), json.dumps(values)))
        self.wait("!opencad3d.busy")
        error = self.evaluate("opencad3d.error")
        self.assertFalse(error, "actual form command failed: " + str(error))
        self.assertEqual(self.evaluate("reviewEvents.length"), event_count + 1,
                         "form did not complete a shared state+mesh refresh")
        self.assertEqual(len(json.loads(http(self.base, "/api/state")["doc"])["features"]),
                         expected_features)
        self.tree_matches()
        return json.loads(http(self.base, "/api/state")["doc"])["features"][-1]["params"]

    def assert_real_mesh(self, volume, bbox, solids=1):
        sys.path.insert(0, str(CORE))
        from cadcore.kernel.subproc import SubprocessKernel
        document = json.loads(http(self.base, "/api/state")["doc"])
        kernel = SubprocessKernel(work_dir=OUT / (self._testMethodName + "-mass"),
                                  keep_work_dir=True,
                                  extra_env={"TMPDIR": str(OUT), "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            props = kernel.mass_properties(kernel.shape_from_document(document))
        finally:
            kernel.close()
        self.assertEqual(props.source, "external")
        self.assertFalse(props.unmeasured)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, volume, places=6)
        for actual, expected in zip(props.bbox_nm, bbox):
            self.assertAlmostEqual(actual / 1e6, expected, delta=1e-6)
        mesh = http(self.base, "/api/mesh")["solids"][0]
        self.assertTrue(mesh["triangles"])
        self.assertEqual(mesh["solid_count"], solids)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, volume, places=6)
        self.assertAlmostEqual(self.evaluate("opencad3d.volume_mm3"), volume, places=6)
        # OCC BoundBox after tessellation includes polygonal face sampling;
        # compare fresh BRep mass strictly, sampled bounds within mesh deflection.
        for actual, expected in zip(mesh["properties"]["bbox_nm"], bbox):
            self.assertAlmostEqual(actual / 1e6, expected, delta=0.1)
        self.stack.append({"test": self.id(), "mesh_properties": mesh["properties"],
                           "mass_properties": props.as_dict(),
                           "solid_count": mesh["solid_count"], "triangles": len(mesh["triangles"]),
                           "expected_volume_mm3": volume, "expected_bbox_mm": bbox})

    def test_form_sweep_uses_selected_profile_and_negative_path(self):
        first, second = self.circle_sketch(2), self.circle_sketch(1)
        try:
            self.load_fixture([first, second])
            self.evaluate("document.getElementById('profile').value=%s" % json.dumps(second["uuid"]))
            params = self.submit_form("sweep_solid", {"path": "[[0,0,0],[0,0,-7.5]]"}, 1)
            self.assertEqual(params["sketch_uuid"], second["uuid"])
            self.assertEqual(params["path_points_nm"], [[0, 0, 0], [0, 0, -7_500_000]])
            self.assert_real_mesh(7.5 * 3.141592653589793, [-1, -1, -7.5, 1, 1, 0])
        finally:
            self.restore_fixture()

    def test_form_loft_trims_profile_ids_and_converts_offsets_and_ruled(self):
        first, second = self.circle_sketch(2), self.circle_sketch(1)
        try:
            self.load_fixture([first, second])
            params = self.submit_form("loft_solid", {
                "profiles": " " + first["uuid"] + ", " + second["uuid"] + " ",
                "offsets": "[[0,0,0],[0,0,9]]", "ruled": True,
            }, 1)
            self.assertEqual(params["profile_uuids"], [first["uuid"], second["uuid"]])
            self.assertEqual(params["offsets_nm"], [[0, 0, 0], [0, 0, 9_000_000]])
            self.assertIs(params["ruled"], True)
            self.assert_real_mesh(21 * 3.141592653589793, [-2, -2, 0, 2, 2, 9])
        finally:
            self.restore_fixture()

    def test_form_shell_creates_real_open_box_with_selected_face(self):
        sketch = self.circle_sketch(2)
        sketch["entities"][0]["kind"] = "polyline"
        sketch["entities"][0]["geom"] = {"points": [
            [0, 0], [20_000_000, 0], [20_000_000, 16_000_000],
            [0, 16_000_000], [0, 0]]}
        try:
            self.load_fixture([sketch])
            self.command("extrude", {"distance_nm": 12_000_000})
            self.evaluate("opencad3d.refresh()")
            params = self.submit_form("shell_solid", {"thickness": "2", "faces": "6"}, 2)
            self.assertEqual(params["thickness_nm"], 2_000_000)
            self.assertEqual(params["face_indices"], [6])
            self.assertIs(params["inward"], True)
            self.assert_real_mesh(1920, [0, 0, 0, 20, 16, 12])
        finally:
            self.restore_fixture()

    def test_form_pattern_all_instances_compound_and_real_overlap_fuse(self):
        try:
            params = self.submit_form("pattern_solid", {
                "translations": "[[0,0,0],[10,0,0],[20,0,0]]", "fuse": False,
            }, 2)
            self.assertEqual(params["translations_nm"],
                             [[0, 0, 0], [10_000_000, 0, 0], [20_000_000, 0, 0]])
            self.assertIs(params["fuse"], False)
            self.assert_real_mesh(self.volume * 3, [-2, -2, 0, 22, 2, 10], solids=3)
            self.evaluate("opencad3d.command('undo')")
            params = self.submit_form("pattern_solid", {
                "translations": "[[0,0,0],[0,0,5]]", "fuse": True,
            }, 2)
            self.assertIs(params["fuse"], True)
            self.assert_real_mesh(self.volume * 1.5, [-2, -2, 0, 2, 2, 15])
        finally:
            self.restore_fixture()

    def test_form_mirror_offset_and_keep_original_transform_real_brep(self):
        try:
            params = self.submit_form("mirror_solid", {
                "axis": "x", "offset": "5", "keep": False,
            }, 2)
            self.assertEqual(params, {"normal_axis": "x", "offset_nm": 5_000_000,
                                      "keep_original": False})
            self.assert_real_mesh(self.volume, [8, -2, 0, 12, 2, 10])
            self.evaluate("opencad3d.command('undo')")
            params = self.submit_form("mirror_solid", {
                "axis": "z", "offset": "-1", "keep": True,
            }, 2)
            self.assertEqual(params["offset_nm"], -1_000_000)
            self.assertIs(params["keep_original"], True)
            self.assert_real_mesh(self.volume * 2, [-2, -2, -12, 2, 2, 10], solids=2)
        finally:
            self.restore_fixture()

    def test_three_language_tree_labels_after_delayed_real_locale(self):
        self.delay_real_request(path="/api/locale")
        for index, locale in enumerate(("ja", "zh-CN", "en"), 1):
            self.evaluate("document.getElementById('locale').value=%s;"
                          "document.getElementById('locale').dispatchEvent(new Event('change'))"
                          % json.dumps(locale))
            self.wait("reviewNetwork.completed", index)
            self.wait("document.documentElement.lang", locale)
            self.wait_painted_idle()
            with self.subTest(locale=locale):
                labels = self.tree_matches()
                self.stack.append({"test": self.id(), "locale": locale, "labels": labels})
        snap = self.evaluate("reviewNetwork")
        self.stack.append({"test": self.id(), "network": snap, "events": self.evaluate("reviewEvents")})
        self.assertEqual(snap["reads"].count("/api/state"), 3, "advanced made redundant state GETs")
        self.assertEqual([e["locale"] for e in self.evaluate("reviewEvents")], ["ja", "zh-CN", "en"])
        self.assertTrue(all(delay >= 1400 for delay in snap["delays"]))
        self.assertEqual(http(self.base, "/api/state")["doc"], self.baseline)

    def test_tree_updates_after_delayed_real_undo(self):
        feature = json.loads(self.baseline)["features"][0]
        self.command("suppress_solid", {"feature_uuid": feature["uuid"], "suppressed": True})
        self.evaluate("window.opencad3d.refresh()")
        self.delay_real_request(path="/api/command", command="undo")
        self.evaluate("document.getElementById('undo').click()")
        self.wait("reviewNetwork.completed", 1)
        self.wait("window.opencad3d.triangles>0")
        self.wait_painted_idle()
        self.stack.append({"test": self.id(), "network": self.evaluate("reviewNetwork")})
        self.tree_matches()
        self.assertAlmostEqual(self.evaluate("opencad3d.volume_mm3"), self.volume, places=6)
        self.assertEqual(self.evaluate("reviewNetwork.reads.filter(function(p){return p==='/api/state';}).length"), 1)

    def test_advanced_and_undo_do_not_overlap_real_mutations(self):
        self.delay_real_request(path="/api/command", command="suppress_solid")
        try:
            self.evaluate("document.querySelector('#feature-tree li button').click();"
                          "document.getElementById('undo').click()")
            self.wait("reviewNetwork.active", 0)
            self.wait_painted_idle()
            snap = self.evaluate("reviewNetwork")
            self.stack.append({"test": self.id(), "network": snap})
            self.assertEqual(snap["maxActive"], 1, "two panels submitted overlapping mutations")
            self.assertEqual(len(snap["requests"]), 1, "busy undo button submitted a second mutation")
            self.assertEqual(self.evaluate("opencad3d.triangles"), 0)
        finally:
            state = json.loads(http(self.base, "/api/state")["doc"])
            if state["features"] and state["features"][0].get("suppressed"):
                self.command("undo")
            if not state["features"]:
                self.command("redo")

    def test_overlapping_api_writes_reject_busy_without_second_post(self):
        self.delay_real_request(path="/api/command", command="suppress_solid")
        feature = json.loads(self.baseline)["features"][0]
        try:
            result = self.evaluate("""Promise.allSettled([
              opencadAdvanced.execute('suppress_solid',%s),
              opencad3d.command('undo',{})
            ]).then(function(results){return results.map(function(r){
              return {status:r.status,error:r.reason?String(r.reason):null};});})""" %
              json.dumps({"feature_uuid": feature["uuid"], "suppressed": True}))
            self.wait_painted_idle()
            self.stack.append({"test": self.id(), "promises": result, "network": self.evaluate("reviewNetwork")})
            self.assertEqual([r["status"] for r in result], ["fulfilled", "rejected"])
            self.assertIn("operation_in_progress", result[1]["error"])
            self.assertEqual(self.evaluate("reviewNetwork.maxActive"), 1)
            self.assertEqual(self.evaluate("reviewNetwork.requests.length"), 1)
        finally:
            if json.loads(http(self.base, "/api/state")["doc"])["features"][0].get("suppressed"):
                self.command("undo")

    def test_pending_mesh_and_state_boundary_disable_old_and_new_tree_buttons(self):
        self.evaluate("""(function(){
          var nativeFetch=window.fetch;
          window.reviewGate={held:false,done:false,unhandled:[],commands:[]};
          window.fetch=function(path,options){
            if(path==='/api/command'&&options&&options.body){
              reviewGate.commands.push(JSON.parse(options.body));}
            return nativeFetch.call(window,path,options).then(function(response){
              if(path!=='/api/mesh'){return response;}
              reviewGate.held=true;
              return new Promise(function(resolve){
                reviewGate.release=function(){reviewGate.done=true;resolve(response);};
              });
            });
          };
        })()""")
        try:
            self.evaluate("document.querySelector('#feature-tree li button').click()")
            self.wait("reviewGate.held && opencad3d.busy")
            before = self.evaluate("""(function(){
              var button=document.querySelector('#feature-tree li button');
              return {disabled:button.disabled,label:button.textContent,
                status:document.getElementById('status').textContent};
            })()""")
            self.evaluate("document.querySelector('#feature-tree li button').click()")
            after = self.evaluate("""({unhandled:reviewUnhandled,commands:reviewGate.commands,
              status:document.getElementById('status').textContent})""")
            self.stack.append({"test": self.id(), "mesh_response_held": before,
                               "second_click": after})
            self.assertFalse(after["unhandled"], "pending user click leaked an unhandled rejection")
            self.assertTrue(before["disabled"], "pending old toggle stayed clickable")
            self.assertEqual(len(after["commands"]), 1)
        finally:
            self.evaluate("if(reviewGate.release){reviewGate.release();}")
            self.wait("reviewGate.done")
            self.wait("document.querySelector('#feature-tree li button').textContent", "Resume")
            self.wait("!opencad3d.busy")
            event = self.evaluate("reviewEvents[reviewEvents.length-1]")
            self.stack.append({"test": self.id(), "state_boundary": event})
            self.assertEqual(event["triangles"], 0)
            self.assertTrue(event["busy"])
            self.assertTrue(all(b["disabled"] for b in event["tree"]),
                            "state render recreated enabled buttons before command unlock")
            self.assertFalse(self.evaluate("document.querySelector('#feature-tree li button').disabled"))
            self.command("undo")

    def test_failed_command_remains_rejected_and_visible(self):
        result = self.evaluate("""window.opencadAdvanced.execute('unknown_review_command',{}).then(
          function(){return {rejected:false,status:document.getElementById('status').textContent};},
          function(error){return {rejected:true,status:document.getElementById('status').textContent};})""")
        self.stack.append({"test": self.id(), "failure": result})
        self.assertTrue(result["status"], "failure not shown")
        self.assertTrue(result["rejected"], "catch(fail) silently fulfilled the failed operation")

    def test_real_suppress_resume_restores_measured_volume_and_tree(self):
        self.delay_real_request(path="/api/command", command="suppress_solid")
        try:
            self.evaluate("document.querySelector('#feature-tree li button').click()")
            self.wait("reviewNetwork.completed", 1)
            self.wait_painted_idle()
            self.assertEqual(self.evaluate("opencad3d.triangles"), 0)
            self.assertEqual(http(self.base, "/api/mesh")["solids"], [])
            self.tree_matches()
            self.evaluate("document.querySelector('#feature-tree li button').click()")
            self.wait("reviewNetwork.completed", 2)
            self.wait_painted_idle()
            self.tree_matches()
            mesh = http(self.base, "/api/mesh")["solids"][0]
            self.assertTrue(mesh["triangles"])
            self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, self.volume, places=6)
            self.assertAlmostEqual(self.evaluate("opencad3d.volume_mm3"), self.volume, places=6)
            self.stack.append({"test": self.id(), "volume_mm3": self.volume,
                               "network": self.evaluate("reviewNetwork"),
                               "events": self.evaluate("reviewEvents")})
        finally:
            if json.loads(http(self.base, "/api/state")["doc"])["features"][0].get("suppressed"):
                self.command("undo")

    def test_invalid_params_dom_error_is_visible_without_request_or_unhandled_rejection(self):
        self.delay_real_request(path="/api/command")
        cases = [
            ("mirror_solid", "offset", "9007199255", "length_out_of_range"),
            ("fillet_solid", "size", "", "invalid_length_mm"),
            ("pattern_solid", "translations", "[]", "invalid_points_mm"),
            ("pattern_solid", "translations", json.dumps([[0, 0, 0]] * 257), "too_many_pattern_instances"),
            ("measure_clearance", "translation", "[true,0,0]", "invalid_translation_mm"),
            ("measure_clearance", "translation", "[0,0]", "invalid_translation_mm"),
            ("measure_clearance", "translation", "[9007199255,0,0]", "length_out_of_range"),
        ]
        for operation, field, value, expected in cases:
            with self.subTest(operation=operation, value=value[:30]):
                result = self.evaluate("""(function(){
                  var form=document.querySelector('form[data-solid=%s]');
                  form.elements.namedItem(%s).value=%s;
                  document.getElementById('status').textContent='';
                  form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
                  return document.getElementById('status').textContent;
                })()""" % (json.dumps(operation), json.dumps(field), json.dumps(value)))
                self.assertIn(expected, result)
        self.assertEqual(self.evaluate("reviewNetwork.requests.length"), 0)
        self.evaluate("""document.getElementById('feature-edit-id').value=
          JSON.parse(%s).features[0].uuid;
          document.getElementById('feature-parameters').value='{"unexpected_review_parameter":1}';
          document.querySelector('form[data-solid=edit_feature_params]').dispatchEvent(
            new Event('submit',{bubbles:true,cancelable:true}));""" % json.dumps(self.baseline))
        self.wait("reviewNetwork.completed", 1)
        self.wait_painted_idle()
        self.assertIn("Error:", self.evaluate("document.getElementById('status').textContent"))
        self.stack.append({"test": self.id(), "invalid_params_status":
                           self.evaluate("document.getElementById('status').textContent")})

    def check_readonly_separated_measurement_labels_follow_three_languages(self):
        self.delay_real_request(path="/api/locale")
        self.evaluate("""(function(){
          var form=document.querySelector('form[data-solid=measure_clearance]');
          form.parentElement.open=true;form.querySelector('button').click();
        })()""")
        self.wait("!document.querySelector('form[data-solid=measure_clearance] button').disabled")
        text = self.evaluate("document.getElementById('clearance-result').textContent")
        self.assertIn("16 mm", text)
        for locale in ("ja", "zh-CN", "en"):
            self.evaluate("document.getElementById('locale').value=%s;"
                          "document.getElementById('locale').dispatchEvent(new Event('change'))"
                          % json.dumps(locale))
            self.wait("document.documentElement.lang", locale)
            self.wait_painted_idle()
            text = self.evaluate("document.getElementById('clearance-result').textContent")
            self.assertIn(http(self.base, "/api/state")["strings"]["solid.separated"], text)
            self.stack.append({"test": self.id(), "locale": locale, "measurement_text": text})
        self.assertEqual(self.evaluate("reviewNetwork.requests.filter(function(r){"
                         "return r.path==='/api/clearance';}).length"), 1)

    def test_real_readonly_clearance_statuses_and_locale_follow_completion(self):
        self.delay_real_request(path="/api/clearance")
        states = []
        for index, (vector, status, distance, volume) in enumerate((
                ([20, 0, 0], "separated", 16, 0),
                ([0, 0, 10], "touching", 0, 0),
                ([0, 0, 5], "penetrating", 0, self.volume / 2)), 1):
            with self.subTest(vector_mm=vector, expected_status=status):
                self.evaluate("""(function(){
                  var form=document.querySelector('form[data-solid=measure_clearance]');
                  form.parentElement.open=true;
                  form.elements.namedItem('translation').value=%s;
                  form.querySelector('button').click();
                })()""" % json.dumps(json.dumps(vector)))
                self.wait("reviewNetwork.completed", index)
                self.wait("!document.querySelector('form[data-solid=measure_clearance] button').disabled")
                state = http(self.base, "/api/state")
                text = self.evaluate("document.getElementById('clearance-result').textContent")
                response = self.evaluate("reviewNetwork.responses[reviewNetwork.responses.length-1]")
                observation = {"test": self.id(), "vector_mm": vector, "text": text,
                               "status": self.evaluate("document.getElementById('status').textContent"),
                               "response": response}
                self.stack.append(observation)
                states.append(observation)
                self.assertTrue(response["body"]["ok"], repr(response["body"]))
                self.assertIn(state["strings"]["solid." + status], text)
                measured = response["body"]["measurement"]
                self.assertEqual(measured["status"], status)
                self.assertEqual(measured["distance_nm"] / 1e6, distance)
                self.assertAlmostEqual(measured["interference_volume_nm3"] / 1e18, volume, places=6)
                self.assertEqual(response["body"]["translation_nm"], [int(mm * 1e6) for mm in vector])
                self.assertEqual(response["body"]["document_sha256"], self.evaluate("opencad3d.document_sha256"))
        self.assertEqual(self.evaluate("reviewNetwork.reads"), [], "measurement caused state/mesh refresh")
        self.assertEqual(self.evaluate("reviewEvents"), [], "read-only measurement changed model state")
        for locale in ("ja", "zh-CN", "en"):
            self.evaluate("document.getElementById('locale').value=%s;"
                          "document.getElementById('locale').dispatchEvent(new Event('change'))"
                          % json.dumps(locale))
            self.wait("document.documentElement.lang", locale)
            self.wait_painted_idle()
            text = self.evaluate("document.getElementById('clearance-result').textContent")
            self.assertIn(http(self.base, "/api/state")["strings"]["solid.penetrating"], text)
            self.tree_matches()
        self.stack.append({"test": self.id(), "measurements": states,
                           "network": self.evaluate("reviewNetwork")})

    def test_delayed_clearance_for_changed_model_is_rejected_not_displayed(self):
        self.evaluate("""(function(){
          var nativeFetch=window.fetch;
          window.reviewMeasureGate={held:false,done:false};
          window.fetch=function(path,options){
            return nativeFetch.call(window,path,options).then(function(response){
              if(path!=='/api/clearance'){return response;}
              reviewMeasureGate.held=true;
              return new Promise(function(resolve){
                reviewMeasureGate.release=function(){reviewMeasureGate.done=true;resolve(response);};
              });
            });
          };
        })()""")
        try:
            self.evaluate("""document.querySelector('form[data-solid=measure_clearance]').dispatchEvent(
              new Event('submit',{bubbles:true,cancelable:true}));""")
            self.wait("reviewMeasureGate.held")
            self.evaluate("""document.querySelector('form[data-solid=measure_clearance]').dispatchEvent(
              new Event('submit',{bubbles:true,cancelable:true}));""")
            self.assertIn("measurement_in_progress",
                          self.evaluate("document.getElementById('status').textContent"))
            self.evaluate("document.querySelector('#feature-tree li button').click()")
            self.wait("opencad3d.triangles===0 && !opencad3d.busy")
            self.evaluate("reviewMeasureGate.release()")
            self.wait("document.getElementById('status').textContent.includes('stale_clearance_measurement')")
            self.assertEqual(self.evaluate("document.getElementById('clearance-result').textContent"), "")
            self.stack.append({"test": self.id(), "status":
                               self.evaluate("document.getElementById('status').textContent")})
        finally:
            self.evaluate("if(reviewMeasureGate.release){reviewMeasureGate.release();}")
            if json.loads(http(self.base, "/api/state")["doc"])["features"][0].get("suppressed"):
                self.command("undo")

    @classmethod
    def tearDownClass(cls):
        if cls.ws is not None:
            try:
                screenshot = cls.call("Page.captureScreenshot", {"format": "png"})
                (OUT / "review.png").write_bytes(base64.b64decode(screenshot["data"]))
            finally:
                cls.ws.sock.close()
        for proc in (cls.chrome, cls.server):
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        if hasattr(cls, "profile"):
            cls.profile.cleanup()
        for name in ("chrome_log", "server_log"):
            if hasattr(cls, name):
                getattr(cls, name).close()
        (OUT / "browser-observations.json").write_text(json.dumps(cls.stack, indent=2))


def source_paths(browser):
    sources = [APP / path for path in ("web/js/advanced3d.js", "web/js/viewer3d.js",
                                      "viewer3d_page.py")]
    if ADVANCED not in sources:
        sources.append(ADVANCED)
    sources += [CORE / "cadcore/data/i18n" / (code + ".json") for code in ("en", "ja", "zh-CN")]
    sources.append(APP / "web/css/viewer3d.css")
    sources.append(Path(__file__))
    if browser:
        sources += [APP / path for path in ("server.py", "solid_commands.py",
                                            "tests/legacy_browser_compat_gate.py")]
        sources += [CORE / "cadcore/kernel" / name for name in ("subproc.py", "features_registry.py")]
        sources += [CORE / "cadcore/kernel" / ("features_" + name + ".py")
                    for name in ("sweep", "loft", "edges", "shell", "pattern", "step", "clearance")]
    return sources


def fingerprints(sources):
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise AssertionError("review prerequisite missing (never skipped): " + repr(missing))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}


def build_suite(loader, *, browser, suite_name="all", names=()):
    classes = [StaticReviewTests] + ([BrowserReviewTests] if browser else [])
    suite = unittest.TestSuite()
    selected = set()
    for cls in classes:
        for name in loader.getTestCaseNames(cls):
            included = (suite_name == "all" or
                        (suite_name == "forms" and name.startswith("test_form_")) or
                        (suite_name == "review" and not name.startswith("test_form_")))
            if (names and name in names) or (not names and included):
                suite.addTest(cls(name))
                selected.add(name)
    if set(names) - selected:
        raise ValueError("unknown or unavailable test: " + repr(sorted(set(names) - selected)))
    return suite


def write_report(*, before, after, tests, failures, errors, skipped, browser,
                 suite_name, names=()):
    payload = {
        "verdict": "PASS" if tests and not failures and not errors and before == after and not skipped else "FAIL",
        "collected_at": datetime.now(timezone.utc).isoformat(), "tests": tests,
        "failures": len(failures), "errors": len(errors), "skips": len(skipped),
        "browser": "RUN" if browser else "NOT_RUN_WAIT_MAIN_READY",
        "resource_mode": "MAIN_DIRECT" if ADVANCED == APP / "web/js/advanced3d.js" else "ADVANCED_OVERLAY",
        "source_root": str(ROOT), "suite": suite_name, "selected_tests": list(names),
        "source_before": before, "source_after": after, "source_unchanged": before == after,
        "findings": [{"test": test.id(), "traceback": text} for test, text in failures + errors],
    }
    (OUT / "result.json").write_text(json.dumps(payload, indent=2))
    for name, original, reviewed in (
            ("advanced3d.diff", APP / "web/js/advanced3d.js", ADVANCED),
            ("test_advanced_ui_review.diff", Path("/dev/null"), Path(__file__))):
        diff = subprocess.run(["git", "diff", "--no-index", "--", str(original), str(reviewed)],
                              text=True, capture_output=True)
        if diff.returncode not in (0, 1):
            raise RuntimeError("diff evidence failed: " + diff.stderr)
        (OUT / name).write_text(diff.stdout)
    line = ("ADVANCED_UI_REVIEW verdict=%s tests=%s failures=%s errors=%s browser=%s evidence=%s"
            % (payload["verdict"], tests, len(failures), len(errors), payload["browser"], OUT / "result.json"))
    with (OUT / "tests.log").open("a") as log:
        log.write(line + "\n")
    print(line)
    return payload["verdict"] == "PASS"


class DiscoverySuite(unittest.TestSuite):
    def run(self, result, debug=False):
        global OUT
        if debug:
            raise ValueError("debug discovery does not produce verified test results")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        OUT = OUT / (stamp + "-discovery")
        OUT.mkdir(parents=True)
        sources = source_paths(True)
        before = fingerprints(sources)
        start = (result.testsRun, len(result.failures), len(result.errors), len(result.skipped))
        with (OUT / "tests.log").open("w") as log:
            resultclass = type(result) if hasattr(result, "passed") else unittest.TextTestResult
            inner = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=resultclass).run(
                unittest.TestSuite(self))
        result.testsRun += inner.testsRun
        if hasattr(result, "passed"):
            result.passed += inner.passed
        result.failures.extend(inner.failures)
        result.errors.extend(inner.errors)
        result.skipped.extend(inner.skipped)
        result.expectedFailures.extend(inner.expectedFailures)
        result.unexpectedSuccesses.extend(inner.unexpectedSuccesses)
        after = fingerprints(sources)
        write_report(before=before, after=after, tests=result.testsRun - start[0],
                     failures=result.failures[start[1]:], errors=result.errors[start[2]:],
                     skipped=result.skipped[start[3]:], browser=True, suite_name="all")
        if before != after:
            raise AssertionError("review source changed during unittest discovery; see " + str(OUT / "result.json"))
        return result


def load_tests(loader, tests, pattern):
    return DiscoverySuite(build_suite(loader, browser=True, suite_name="all"))


def main():
    global ROOT, APP, CORE, OUT, BROWSER, ADVANCED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=OUT)
    parser.add_argument("--browser-ready", action="store_true",
                        help="explicit confirmation that all feature modules are ready")
    parser.add_argument("--browser", default=BROWSER)
    parser.add_argument("--advanced-source", type=Path,
                        help="optional overlay; default is the selected source tree's actual script")
    parser.add_argument("--test", action="append", default=[],
                        help="run exact test method names for a diagnosed failure or focused evidence")
    parser.add_argument("--suite", choices=("review", "forms", "all"), default="all",
                        help="all: 21 contracts; review: original 16; forms: five real interactions")
    parser.add_argument("--collect-only", action="store_true",
                        help="verify discovery count and source-root routing without launching a browser")
    parser.add_argument("--discovery", action="store_true",
                        help="exercise the unittest load_tests hook against the selected tree")
    args = parser.parse_args()
    ROOT, BROWSER = args.source_root.resolve(), args.browser
    APP, CORE = ROOT / "oss-alternatives/apps/opencad", ROOT / "oss-alternatives/packages/cadcore"
    ADVANCED = args.advanced_source.resolve() if args.advanced_source else APP / "web/js/advanced3d.js"
    if args.collect_only:
        discovered = load_tests(unittest.defaultTestLoader, unittest.TestSuite(), None)
        if discovered.countTestCases() != 21 or ROOT != Path(__file__).resolve().parents[4]:
            raise AssertionError("discovery or default source-root contract drifted")
        print("ADVANCED_UI_REVIEW_COLLECTION verdict=PASS tests=21 source_root=%s browser=NOT_RUN" % ROOT)
        return 0
    if args.discovery:
        if not args.browser_ready or args.suite != "all" or args.test:
            parser.error("--discovery requires --browser-ready --suite all without --test")
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        if suite.countTestCases() != 21:
            raise AssertionError("unittest discovery must collect exactly 21 contracts")
        result = suite.run(unittest.TestResult())
        payload = json.loads((OUT / "result.json").read_text())
        return 0 if result.wasSuccessful() and payload["verdict"] == "PASS" else 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    OUT = args.evidence_dir / stamp
    OUT.mkdir(parents=True)
    sources = source_paths(args.browser_ready)
    before = fingerprints(sources)
    suite = build_suite(unittest.defaultTestLoader, browser=args.browser_ready,
                        suite_name=args.suite, names=args.test)
    with (OUT / "tests.log").open("w") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    passed = write_report(before=before, after=fingerprints(sources), tests=result.testsRun,
                          failures=result.failures, errors=result.errors, skipped=result.skipped,
                          browser=args.browser_ready, suite_name=args.suite, names=args.test)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
