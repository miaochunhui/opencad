"""Real browser controls and screenshot projection tests. No mocks or skips.

Run directly, or use tools/cad3d/viewer_controls_check.py. Both retain the
actual count, screenshots, CDP input events and failures in seat-views.
"""

from datetime import datetime, timezone
from contextlib import ExitStack
import importlib.util
import json
import math
from pathlib import Path
import time
import unittest

HARNESS = None
RESULTS = []
ROOT = Path(__file__).resolve().parents[4]


class ViewerControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = HARNESS
        if cls.harness is None:
            tool = ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
            spec = importlib.util.spec_from_file_location("viewer_controls_discovery", tool)
            if spec is None or spec.loader is None:
                raise RuntimeError("real browser runner cannot be loaded: %s" % tool)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            stack = ExitStack()
            cls.addClassCleanup(stack.close)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            cls.harness = stack.enter_context(module.ControlsHarness(
                module.DEFAULT_OUT / ("discover-" + stamp)))

    def setUp(self):
        self.started = time.monotonic()
        self.h = self.harness
        self.assertIsNotNone(self.h, "use the isolated browser runner")
        self.b = self.h.b
        self.h.view("iso")
        self.b.evaluate("if(document.activeElement)document.activeElement.blur()")

    def tearDown(self):
        RESULTS.append({"test": self._testMethodName,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "wall_s": time.monotonic() - self.started})

    def pose(self):
        s = self.h.snapshot()
        return {k: s[k] for k in ("yaw", "pitch", "zoom", "pan", "view")}

    def test_real_webgl_and_fixture(self):
        renderer = self.b.evaluate("window.opencad3d.renderer")
        self.assertTrue(renderer)
        self.assertIn("SwiftShader", renderer)
        self.assertEqual(self.b.evaluate("window.opencad3d.bbox_nm"),
                         [0, 0, 0, 120000000, 80000000, 50000000])
        self.assertGreater(self.b.evaluate("window.opencad3d.pixelCount()"), 1000)
        self.assertEqual(self.h.request("/api/state")["doc"], self.h.expected_doc)

    def test_principal_screenshot_envelopes_and_z_up(self):
        views = {}
        for name, ratio in (("front", 120/50), ("top", 120/80), ("right", 80/50)):
            self.h.view(name)
            pixels = self.h.pixels(name)
            self.assertAlmostEqual(pixels["ratio"], ratio, delta=0.025)
            if name != "top":
                self.assertGreater(pixels["upper_left"], 1000)
                self.assertLess(pixels["upper_right"], 30)
            views[name] = pixels["bbox_px"]
        self.assertEqual(len({tuple(v) for v in views.values()}), 3)
        self.h.view("iso")
        iso = self.h.pixels("iso")
        self.assertGreater(iso["pixels"], 5000)
        self.assertNotEqual(iso["bbox_px"], views["top"])

    def test_named_view_axes_and_invalid_name(self):
        for name, yaw, pitch in (("front", 0, -math.pi/2), ("top", 0, 0),
                                 ("right", -math.pi/2, -math.pi/2),
                                 ("iso", -math.pi/4, -math.atan(math.sqrt(2)))):
            self.h.view(name)
            s = self.h.snapshot()
            self.assertAlmostEqual(s["yaw"], yaw)
            self.assertAlmostEqual(s["pitch"], pitch)
            self.assertEqual(s["view"], name)
        before = self.pose()
        error = self.b.evaluate("(function(){try{window.opencad3d.setView('bogus');}"
                                "catch(e){return String(e);}return null;})()")
        self.assertIn("unsupported_view", error)
        self.assertEqual(self.pose(), before)

    def test_buttons_dispatch_through_api(self):
        self.b.evaluate("window._viewCalls=[];window._setView=window.opencad3d.setView;"
                        "window.opencad3d.setView=function(n){window._viewCalls.push(n);return window._setView(n);}")
        try:
            for name in ("front", "top", "right", "iso"):
                self.b.click('[data-view="%s"]' % name)
                self.assertEqual(self.pose()["view"], name)
            self.assertEqual(self.b.evaluate("window._viewCalls"), ["front", "top", "right", "iso"])
        finally:
            self.b.evaluate("window.opencad3d.setView=window._setView")

    def test_fit_and_home_restore_pan_zoom_not_direction(self):
        self.h.view("front")
        original = self.h.pixels("fit-original")
        self.h.drag(shift=True)
        self.h.wheel()
        moved = self.pose()
        self.assertNotEqual(moved["pan"], [0, 0])
        self.assertNotEqual(moved["zoom"], 1)
        self.b.evaluate("window.opencad3d.fit()")
        self.assertEqual(self.pose()["pan"], [0, 0])
        self.assertEqual(self.pose()["zoom"], 1)
        self.assertEqual(self.pose()["view"], "front")
        self.assertEqual(self.h.pixels("fit-restored")["bbox_px"], original["bbox_px"])
        self.h.drag(shift=True)
        self.h.wheel()
        self.b.key("Home")
        self.assertEqual(self.pose()["pan"], [0, 0])
        self.assertEqual(self.pose()["zoom"], 1)
        self.assertEqual(self.pose()["view"], "front")

    def test_shortcuts_when_canvas_focused(self):
        self.b.click("#model")
        for key, view in (("f", "front"), ("T", "top"), ("r", "right"), ("I", "iso")):
            self.b.key(key)
            self.assertEqual(self.pose()["view"], view)

    def test_inputs_select_textarea_contenteditable_do_not_steal_keys(self):
        self.b.evaluate("(function(){['textarea','div'].forEach(function(tag){"
                        "var n=document.createElement(tag);n.id='controls-'+tag;"
                        "if(tag==='div'){n.contentEditable='true';var child=document.createElement('span');"
                        "child.textContent='editable';n.appendChild(child);}document.body.appendChild(n);});})()")
        try:
            for selector in ("#depth", "#locale", "#controls-textarea", "#controls-div"):
                self.b.evaluate("document.querySelector(%s).focus()" % json.dumps(selector))
                self.assertTrue(self.b.evaluate("document.activeElement.matches(%s)" % json.dumps(selector)))
                before = self.pose()
                for key in ("f", "t", "r", "i", "Home"):
                    self.b.key(key)
                    self.assertEqual(self.pose(), before, selector + " stole " + key)
            self.assertIn("ftri", self.b.evaluate("document.getElementById('controls-textarea').value"))
        finally:
            self.b.evaluate("document.getElementById('controls-textarea').remove();"
                            "document.getElementById('controls-div').remove();"
                            "document.getElementById('depth').value='50'")

    def test_modified_and_composing_keys_do_not_change_view(self):
        self.b.click("#model")
        before = self.pose()
        for modifier in (1, 2, 4):
            self.b.key("f", modifiers=modifier)
            self.assertEqual(self.pose(), before)
        self.b.evaluate("document.getElementById('model').dispatchEvent("
                        "new KeyboardEvent('keydown',{key:'f',bubbles:true,isComposing:true}))")
        self.assertEqual(self.pose(), before)

    def test_narrow_viewport_projection_and_fit(self):
        self.b.call("Emulation.setDeviceMetricsOverride", {
            "width": 420, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        try:
            self.h.frame()
            self.assertLessEqual(self.b.evaluate("document.documentElement.scrollWidth"), 421)
            for name, ratio in (("front", 120/50), ("top", 120/80), ("right", 80/50)):
                self.h.view(name)
                pixels = self.h.pixels("narrow-" + name)
                self.assertAlmostEqual(pixels["ratio"], ratio, delta=0.04)
            self.h.drag(shift=True, dx=25, dy=15)
            self.h.wheel()
            self.assertNotEqual(self.pose()["pan"], [0, 0])
            self.assertNotEqual(self.pose()["zoom"], 1)
            self.b.key("Home")
            self.assertEqual(self.pose()["pan"], [0, 0])
            self.assertEqual(self.pose()["zoom"], 1)
        finally:
            self.b.call("Emulation.setDeviceMetricsOverride", {
                "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            self.h.frame()

    def test_shift_pan_wheel_and_orbit_are_real_input(self):
        self.h.view("top")
        before = self.pose()
        self.h.drag(shift=True)
        shifted = self.pose()
        self.assertNotEqual(shifted["pan"], before["pan"])
        self.assertEqual(shifted["yaw"], before["yaw"])
        self.assertEqual(shifted["pitch"], before["pitch"])
        self.h.wheel()
        self.assertGreater(self.pose()["zoom"], 1)
        self.h.drag()
        self.assertNotEqual(self.pose()["yaw"], before["yaw"])
        self.assertNotEqual(self.pose()["pitch"], before["pitch"])
        self.assertEqual(self.pose()["view"], "custom")
        self.h.pixels("pan-wheel-orbit")

    def test_cancel_lostcapture_and_exit_end_drag(self):
        for ending in ("cancel", "lostcapture", "exit"):
            self.h.view("top")
            r = self.h.rect()
            x, y = r["x"] + r["width"]/2, r["y"] + r["height"]/2
            self.b.evaluate("window._controlPointer=null;document.getElementById('model')."
                            "addEventListener('pointerdown',function(e){window._controlPointer=e.pointerId;},{once:true})")
            self.b.mouse("mousePressed", x, y)
            self.b.mouse("mouseMoved", x + 20, y + 10)
            self.assertNotEqual(self.pose()["yaw"], 0)
            if ending == "cancel":
                # Native CDP touch cancellation, not a mock pointer event.
                self.b.mouse("mouseReleased", x + 20, y + 10)
                self.b.evaluate("window._cancels=[];document.getElementById('model')."
                                "addEventListener('pointercancel',function(e){window._cancels.push("
                                "{id:e.pointerId,trusted:e.isTrusted});},{once:true});"
                                "document.getElementById('model').addEventListener('pointerdown',"
                                "function(e){window._controlPointer=e.pointerId;},{once:true})")
                self.b.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [
                    {"x": x, "y": y, "id": 7}]})
                touch_before = self.pose()
                self.b.call("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                    {"x": x+20, "y": y+10, "id": 7}]})
                self.assertNotEqual(self.pose()["yaw"], touch_before["yaw"])
                self.b.call("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
                self.assertEqual(self.b.evaluate("window._cancels"),
                                 [{"id": self.b.evaluate("window._controlPointer"), "trusted": True}])
            elif ending == "lostcapture":
                self.b.evaluate("document.getElementById('model').releasePointerCapture(window._controlPointer)")
            else:
                self.b.mouse("mouseMoved", r["x"] - 20, y)
            stopped = self.pose()
            self.b.mouse("mouseMoved", x + 50, y + 40)
            self.assertEqual(self.pose(), stopped, ending + " kept rotating")
            self.b.mouse("mouseReleased", x + 50, y + 40)
            self.assertFalse(self.b.evaluate("document.getElementById('model')."
                                            "hasPointerCapture(window._controlPointer)"))

    def test_pointer_identity_and_blur(self):
        r = self.h.rect()
        x, y = r["x"] + r["width"]/2, r["y"] + r["height"]/2
        self.b.mouse("mousePressed", x, y)
        before = self.pose()
        self.b.evaluate("document.getElementById('model').dispatchEvent(new PointerEvent("
                        "'pointermove',{pointerId:999,buttons:1,clientX:%s,clientY:%s}))" % (x+40, y+40))
        self.assertEqual(self.pose(), before)
        self.b.call("Emulation.setFocusEmulationEnabled", {"enabled": False})
        self.b.evaluate("window.dispatchEvent(new Event('blur'))")
        self.b.mouse("mouseMoved", x+40, y+40)
        self.assertEqual(self.pose(), before)
        self.b.mouse("mouseReleased", x+40, y+40)

    def test_undo_redo_and_locales_preserve_real_document(self):
        self.b.evaluate("document.getElementById('depth').value='60'")
        self.b.click("#apply")
        self.h.ready(306000)
        self.b.click("#undo")
        self.h.ready()
        self.assertEqual(self.h.request("/api/state")["doc"], self.h.expected_doc)
        self.assertEqual(self.b.evaluate("Number(document.getElementById('depth').value)"), 50)
        self.b.click("#redo")
        self.h.ready(306000)
        self.b.click("#undo")
        self.h.ready()
        for locale in ("ja", "zh-CN", "en"):
            self.b.evaluate("document.getElementById('locale').value=%s;"
                            "document.getElementById('locale').dispatchEvent(new Event('change'))" % json.dumps(locale))
            self.b.wait("document.documentElement.lang === %s" % json.dumps(locale))
            self.h.ready()
            strings = self.h.request("/api/state")["strings"]
            self.assertEqual(self.b.evaluate("document.getElementById('model').getAttribute('aria-label')"),
                             strings["viewer3d.canvas"])
            labels = self.b.evaluate("Array.from(document.querySelectorAll('[data-i18n]')).map("
                                    "n=>({key:n.getAttribute('data-i18n'),text:n.textContent}))")
            self.assertTrue(all(n["text"] == strings[n["key"]] for n in labels))
            self.assertEqual(self.h.request("/api/state")["doc"], self.h.expected_doc)


if __name__ == "__main__":
    tool = ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
    spec = importlib.util.spec_from_file_location("viewer_controls_runner", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raise SystemExit(module.main())
