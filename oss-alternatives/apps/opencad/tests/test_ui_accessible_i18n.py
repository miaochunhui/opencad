"""Real Chromium/HTTP regression for the command input's accessible name.

Discovery defaults to the tree containing this file and never skips missing
dependencies. Direct runs accept --source-root and --app-source so a seat can
serve main's read-only server/catalogs with its sole JavaScript edit. No
production URL is accepted. Locale changes use the real DOM handler and real
HTTP responses; completion is measured, not a fixed sleep. Evidence is external.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[4]
APP_SOURCE = None
WITHOUT_WEB_EXTRAS = False
BROWSER = Path("/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
OUT_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-ui-review/accessible-i18n")
EXPECTED = {
    "en": "Command input",
    "ja": "\u30b3\u30de\u30f3\u30c9\u5165\u529b",
    "zh-CN": "\u547d\u4ee4\u8f93\u5165",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AccessibleI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ROOT / "oss-alternatives/apps/opencad"
        cls.core = ROOT / "oss-alternatives/packages/cadcore"
        cls.source = APP_SOURCE or cls.app / "web/js/app.js"
        cls.out = OUT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        cls.out.mkdir(parents=True, exist_ok=False)
        cls.addClassCleanup(cls.finish)
        cls.proc = cls.chrome = cls.ws = cls.profile = None
        cls.handles = []
        cls.samples = []
        cls.report = {"started_at": datetime.now(timezone.utc).isoformat(),
                      "source_root": str(ROOT), "app_source": str(cls.source),
                      "evidence": str(cls.out), "production_requests": 0}
        required = [cls.app / "server.py", cls.source,
                    cls.app / "tests/legacy_browser_compat_gate.py", BROWSER]
        required += [cls.core / "cadcore/data/i18n" / (code + ".json")
                     for code in EXPECTED]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise AssertionError("real test prerequisite missing: " + repr(missing))
        cls.catalogs = {
            code: json.loads((cls.core / "cadcore/data/i18n" / (code + ".json")).read_text())
            ["strings"] for code in EXPECTED
        }
        cls.sources = [cls.source, cls.app / "server.py", cls.app / "web/index.html"]
        cls.sources += [cls.core / "cadcore/data/i18n" / (code + ".json")
                        for code in EXPECTED]
        cls.before = {str(path): digest(path) for path in cls.sources}
        cls.report["source_sha256_before"] = cls.before
        web = cls.app / "web"
        if WITHOUT_WEB_EXTRAS or cls.source.resolve() != (web / "js/app.js").resolve():
            web = cls.out / "isolated-web"
            ignore = shutil.ignore_patterns("web-i18n.json", "examples.json", "shell.js")
            shutil.copytree(cls.app / "web", web,
                            ignore=ignore if WITHOUT_WEB_EXTRAS else None)
            shutil.copyfile(cls.source, web / "js/app.js")
        cls.report["served_web"] = str(web)
        cls.web_i18n_absent = not (web / "web-i18n.json").is_file()
        cls.report["without_web_extras"] = not any(
            (web / path).is_file() for path in
            ("web-i18n.json", "examples.json", "js/shell.js"))
        cls.report["web_i18n_absent"] = cls.web_i18n_absent
        helper_path = cls.app / "tests/legacy_browser_compat_gate.py"
        spec = importlib.util.spec_from_file_location("accessible_i18n_cdp", helper_path)
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   OPENCAD_CADCORE_ROOT=str(cls.core), TMPDIR=str(cls.out))
        log = (cls.out / "server.log").open("w")
        cls.handles.append(log)
        cls.proc = subprocess.Popen(
            [sys.executable, "-B", str(cls.app / "server.py"), "--host", "127.0.0.1",
             "--port", "0", "--verbose", "--web", str(web),
             "--out-root", str(cls.out / "server-data")],
            cwd=cls.app, env=env, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while True:
            text = (cls.out / "server.log").read_text()
            match = re.search(r"OPENCAD_SERVER verdict=PASS port=(\d+)", text)
            if match:
                cls.base = "http://127.0.0.1:" + match[1]
                break
            if cls.proc.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("isolated server startup failed: " + text[-2000:])
            time.sleep(0.05)
        cls.report["base_url"] = cls.base
        cls.initial_doc = cls.get("/api/state")["doc"]
        # Browser socket/profile requires a local filesystem; all evidence is on 3FS.
        cls.profile = tempfile.TemporaryDirectory(prefix="accessible-i18n-", dir="/tmp")
        chrome_env = dict(env, TMPDIR=cls.profile.name)
        log = (cls.out / "chrome.log").open("w")
        cls.handles.append(log)
        cls.chrome = subprocess.Popen(
            [str(BROWSER), "--headless", "--no-sandbox", "--disable-dev-shm-usage",
             "--remote-debugging-port=0", "--user-data-dir=" + cls.profile.name,
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            env=chrome_env, stdout=log, stderr=subprocess.STDOUT)
        port_file = Path(cls.profile.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.exists():
            if cls.chrome.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("real Chromium startup failed; see chrome.log")
            time.sleep(0.05)
        debug = "http://127.0.0.1:" + port_file.read_text().splitlines()[0]
        with urlopen(debug + "/json/list", timeout=10) as response:
            tabs = json.load(response)
        cls.ws = cls.helper._WSClient(next(
            tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page"))
        cls.seq = {"id": 0}
        cls.call("Runtime.enable", {})
        cls.call("Page.enable", {})
        cls.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          window.accessibleReview={active:0,responses:[],errors:[],blocked:[]};
          window.addEventListener('error',function(e){
            accessibleReview.errors.push(e.message);});
          window.addEventListener('unhandledrejection',function(e){
            accessibleReview.errors.push(String(e.reason));});
          (function(nativeFetch){
            window.fetch=function(path,init){
              var url=new URL(path,location.href);
              var method=init&&init.method||'GET';
              var body=init&&init.body?JSON.parse(init.body):null;
              if(url.origin!==location.origin||
                 (method!=='GET'&&!(method==='POST'&&
                   (url.pathname==='/api/locale'||(url.pathname==='/api/command'&&
                    body&&body.command==='precision_status'))))){
                accessibleReview.blocked.push({url:url.href,method:method,body:body});
                return Promise.reject(new Error('isolated request rejected'));
              }
              accessibleReview.active++;
              return nativeFetch.call(window,path,init).then(async function(response){
                var record={path:url.pathname,method:method,status:response.status,body:body};
                if(url.pathname.indexOf('/api/')===0){
                  record.response=await response.clone().json();
                }
                accessibleReview.responses.push(record);
                return response;
              }).finally(function(){accessibleReview.active--;});
            };
          })(window.fetch);
        """})
        cls.call("Page.navigate", {"url": cls.base + "/"})
        cls.wait("Boolean(window.__opencadV3&&__opencadV3.ready()&&"
                 "__opencadV3.webI18n().state!=='unloaded'&&"
                 "document.readyState==='complete'&&accessibleReview.active===0&&"
                 "document.querySelectorAll('#lang-select option').length===3)")
        cls.report["initial_snapshot"] = cls.snapshot()

    @classmethod
    def call(cls, method, params):
        response = cls.helper._cdp_call(cls.ws, cls.seq, method, params, 30)
        if "error" in response or "exceptionDetails" in response.get("result", {}):
            raise AssertionError("real JS/CDP failure: " + repr(response))
        return response["result"]

    @classmethod
    def evaluate(cls, expression):
        return cls.call("Runtime.evaluate", {"expression": expression,
                       "returnByValue": True, "awaitPromise": True})["result"].get("value")

    @classmethod
    def wait(cls, expression):
        deadline = time.monotonic() + 20
        while True:
            value = cls.evaluate(expression)
            if value is True:
                return
            if time.monotonic() > deadline:
                snapshot = cls.snapshot()
                summary = {key: snapshot[key] for key in
                           ("locale", "aria", "placeholder", "title", "status")}
                raise AssertionError("completion condition not reached: " + expression
                                     + "; snapshot=" + repr(summary))
            time.sleep(0.05)

    @classmethod
    def get(cls, path):
        with urlopen(cls.base + path, timeout=10) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise AssertionError("real HTTP rejected: " + repr(result))
        return result

    @classmethod
    def snapshot(cls):
        return cls.evaluate("""(function(){
          var input=document.getElementById('cmd-line');
          return {locale:document.documentElement.lang,
            aria:input&&input.getAttribute('aria-label'),
            placeholder:input&&input.getAttribute('placeholder'),
            title:input&&input.getAttribute('title'),value:input&&input.value,
            status:document.getElementById('status-message').textContent,
            network:accessibleReview,ready:__opencadV3.ready()};
        })()""")

    @classmethod
    def switch(cls, locale):
        count = cls.evaluate("accessibleReview.responses.filter(function(r){"
                             "return r.path==='/api/locale';}).length")
        cls.evaluate("""(function(code){
          var select=document.getElementById('lang-select');
          if(select.disabled)throw new Error('locale disabled before switch');
          select.value=code;select.dispatchEvent(new Event('change',{bubbles:true}));
        })(%s)""" % json.dumps(locale))
        cls.wait("""document.documentElement.lang===%s&&accessibleReview.active===0&&
          accessibleReview.responses.filter(function(r){return r.path==='/api/locale';})
            .length===%d&&document.getElementById('status-message').textContent===
          (function(){
            var records=accessibleReview.responses.filter(function(r){
              return r.path==='/api/locale';});
            var strings=records[records.length-1].response.strings;
            var catalogs=accessibleReview.responses.filter(function(r){
              return r.path==='/api/catalogs';});
            var nativeName=catalogs[catalogs.length-1].response.catalogs
              .find(function(c){return c.code===%s;}).native_name;
            return strings['lang.switched'].replace('{native_name}',
              nativeName);
          })()""" % (json.dumps(locale), count + 1, json.dumps(locale)))
        state = cls.get("/api/state")
        if state["locale"] != locale or state["doc"] != cls.initial_doc:
            raise AssertionError("real locale switch changed drawing or resolved incorrectly")
        snapshot = cls.snapshot()
        cls.samples.append(snapshot)
        return snapshot, state

    def tearDown(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["network"]["errors"], [])
        self.assertEqual(snapshot["network"]["blocked"], [])
        self.assertEqual(self.get("/api/state")["doc"], self.initial_doc)
        self.samples.append({"test": self._testMethodName, "snapshot": snapshot})
        screenshot = self.call("Page.captureScreenshot", {"format": "png"})
        (self.out / (self._testMethodName + ".png")).write_bytes(
            base64.b64decode(screenshot["data"]))

    def test_01_real_http_serves_exact_script_and_static_index(self):
        with urlopen(self.base + "/web/js/app.js", timeout=10) as response:
            self.assertEqual(response.read(), self.source.read_bytes())
        for route in ("/", "/index.html"):
            with urlopen(self.base + route, timeout=10) as response:
                self.assertEqual(response.read(), (self.app / "web/index.html").read_bytes())
        self.assertTrue(self.report["initial_snapshot"]["ready"])
        if self.web_i18n_absent:
            self.assertFalse((Path(self.report["served_web"]) / "web-i18n.json").exists())
            self.assertEqual(self.evaluate("__opencadV3.webI18n().state"), "absent")

    def test_02_initial_zh_cn_accessible_name_uses_formal_catalog_key(self):
        initial = self.report["initial_snapshot"]
        self.assertEqual(initial["locale"], "zh-CN")
        self.assertEqual(initial["aria"], EXPECTED["zh-CN"])

    def test_03_real_http_strings_have_three_formal_distinct_labels(self):
        for locale, expected in EXPECTED.items():
            with self.subTest(locale=locale):
                _, state = self.switch(locale)
                self.assertEqual(state["strings"]["cmd.label"], expected)
                self.assertEqual(state["strings"]["cmd.label"],
                                 self.catalogs[locale]["cmd.label"])
                self.assertNotEqual(expected, state["strings"]["cmd.line"])
                self.assertNotEqual(expected, state["strings"]["cmd.hint"])

    def test_04_hot_locale_roundtrip_updates_accessible_name_without_reload(self):
        self.evaluate("window.accessibleReviewDocumentToken='same-document'")
        for locale in ("en", "ja", "zh-CN", "en"):
            with self.subTest(locale=locale):
                snapshot, state = self.switch(locale)
                self.assertEqual(snapshot["aria"], state["strings"]["cmd.label"])
                self.assertEqual(snapshot["aria"], EXPECTED[locale])
                self.assertEqual(self.evaluate("accessibleReviewDocumentToken"), "same-document")

    def test_05_placeholder_title_and_typed_value_remain_independent(self):
        self.evaluate("document.getElementById('cmd-line').value='line 0,0 10,0'")
        for locale in ("ja", "en", "zh-CN"):
            with self.subTest(locale=locale):
                snapshot, state = self.switch(locale)
                self.assertEqual(snapshot["placeholder"], state["strings"]["cmd.line"])
                self.assertEqual(snapshot["title"], state["strings"]["cmd.hint"])
                self.assertEqual(snapshot["value"], "line 0,0 10,0")
                self.assertNotEqual(snapshot["placeholder"], snapshot["aria"])
                self.assertNotEqual(snapshot["title"], snapshot["aria"])

    def test_06_all_observed_requests_are_isolated_non_drawing_operations(self):
        snapshot = self.snapshot()
        requests = snapshot["network"]["responses"]
        self.assertGreater(len(requests), 0)
        for request in requests:
            if request["method"] == "POST":
                self.assertIn(request["path"], ("/api/locale", "/api/command"))
                if request["path"] == "/api/command":
                    self.assertEqual(request["body"]["command"], "precision_status")
            if self.web_i18n_absent and request["method"] == "GET" and request["path"] in (
                    "/web/web-i18n.json", "/web-i18n.json"):
                self.assertEqual(request["status"], 404)
            else:
                self.assertEqual(request["status"], 200)
        self.assertEqual(snapshot["network"]["blocked"], [])
        self.assertEqual(self.get("/api/state")["doc"], self.initial_doc)

    @classmethod
    def finish(cls):
        try:
            if cls.ws is not None:
                cls.report["final_snapshot"] = cls.snapshot()
                cls.report["doc_unchanged"] = cls.get("/api/state")["doc"] == cls.initial_doc
            if hasattr(cls, "sources"):
                after = {str(path): digest(path) for path in cls.sources}
                cls.report["source_sha256_after"] = after
                cls.report["source_unchanged"] = after == cls.before
                if after != cls.before:
                    raise AssertionError("reviewed read-only sources drifted during run")
        finally:
            if cls.ws is not None:
                cls.ws.sock.close()
            for proc in (cls.chrome, cls.proc):
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
            for handle in cls.handles:
                handle.close()
            if cls.profile is not None:
                cls.profile.cleanup()
            cls.report["samples"] = cls.samples
            cls.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            (cls.out / "browser-evidence.json").write_text(
                json.dumps(cls.report, ensure_ascii=False, indent=2))
            print("ACCESSIBLE_I18N_EVIDENCE path=" + str(cls.out), flush=True)


def main():
    global ROOT, APP_SOURCE, BROWSER, OUT_ROOT, WITHOUT_WEB_EXTRAS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--app-source", type=Path)
    parser.add_argument("--browser", type=Path, default=BROWSER)
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    parser.add_argument("--without-web-extras", action="store_true",
                        help="serve isolated tracked-style web without the three optional resources")
    args = parser.parse_args()
    ROOT = args.source_root.resolve(strict=True)
    APP_SOURCE = args.app_source.resolve(strict=True) if args.app_source else None
    WITHOUT_WEB_EXTRAS = args.without_web_extras
    BROWSER = args.browser.resolve(strict=True)
    OUT_ROOT = args.out_root.absolute()
    transcript = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AccessibleI18nTests)
    result = unittest.TextTestRunner(stream=transcript, verbosity=2).run(suite)
    out = AccessibleI18nTests.out
    (out / "tests.log").write_text(transcript.getvalue())
    evidence = json.loads((out / "browser-evidence.json").read_text())
    passed = (result.wasSuccessful() and result.testsRun == 6 and not result.skipped
              and evidence.get("source_unchanged") is True
              and evidence.get("doc_unchanged") is True)
    report = {
        "verdict": "PASS" if passed else "FAIL",
        "tests": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "source_root": str(ROOT), "app_source": str(AccessibleI18nTests.source),
        "app_sha256": digest(AccessibleI18nTests.source),
        "test_sha256": digest(Path(__file__)), "evidence": str(out),
        "source_unchanged": evidence.get("source_unchanged"),
        "doc_unchanged": evidence.get("doc_unchanged"),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "result.json").write_text(json.dumps(report, indent=2))
    for case, detail in result.failures + result.errors:
        print("ACCESSIBLE_I18N_FAILURE test=" + str(case) + " detail="
              + detail.strip().splitlines()[-1][:300])
    print("ACCESSIBLE_I18N verdict={verdict} tests={tests} failures={failures} "
          "errors={errors} skipped={skipped} evidence={evidence}".format(**report))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
