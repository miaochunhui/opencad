#!/usr/bin/env python3
"""Real integrated private-page locale and shield acceptance on an isolated server.

Starts the shipped private service on loopback with temporary in-memory accounts
and database roots, drives it with the repository's headless Chromium/CDP
helper, and exercises the actual language selector at desktop and mobile
widths. Passwords are generated in memory and are never written to evidence.
No production service, credential, network exposure or GPU path is used.

The run deliberately binds the source bytes and actual browser path, records
all checks and failures, and never cleans or reuses evidence. It is not a CAD
geometry/industrial-parity test.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import traceback
import uuid


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
CATALOG = APP / "web/panel-i18n.json"
TOOL = Path(__file__).resolve()
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
SOURCES = (
    "secure_web.py",
    "web/js/locale-service.js",
    "web/js/project-session.js",
    "web/js/private-locale.js",
    "web/js/industrial-panels.js",
    "web/js/parametric-panel.js",
    "web/js/surfaces-panel.js",
    "web/js/assembly-panel.js",
    "web/js/drawing-panel.js",
    "web/js/render-panel.js",
    "web/js/viewer3d.js",
    "web/panel-i18n.json",
    "viewer3d_page.py",
)
INSTRUMENT = r"""
(() => {
  window.__acceptErrors = [];
  window.__acceptCalls = [];
  const realFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    try {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      if (url.origin === location.origin) {
        window.__acceptCalls.push({method: (init && init.method) || "GET", path: url.pathname});
      }
    } catch (_) {}
    return realFetch(input, init);
  };
  window.addEventListener("error", event => {
    window.__acceptErrors.push(String(event.message || (event.error && event.error.message) || "error"));
  });
  window.addEventListener("unhandledrejection", event => {
    window.__acceptErrors.push(String((event.reason && event.reason.message) || event.reason || "rejection"));
  });
})();
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_exclusive(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def load_browser(source_root):
    path = source_root / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
    spec = importlib.util.spec_from_file_location("locale_browser_helper", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class PrivateBrowser(module.BrowserSession):
        def call(self, method, params):
            result = self.helper._cdp_call(self.ws, self.sequence, method, params, 10)
            # Input.insertText can carry the in-memory test password. Never persist it.
            self.events.append({"method": method, "ok": "error" not in result})
            if "error" in result or "exceptionDetails" in result.get("result", {}):
                raise RuntimeError("Private browser operation failed: " + method)
            return result["result"]

    return PrivateBrowser


def catalog(app_root):
    payload = json.loads((app_root / "web/panel-i18n.json").read_text(encoding="utf-8"))
    return payload["locales"]


def jsstr(value):
    return json.dumps(value, ensure_ascii=False)


def expected_language(js, code):
    return {
        "aria": js[code]["panel.workbench.heading"],
        "summaries": [js[code]["panel.workbench." + name] for name in
                      ("parametric", "surfaces", "assembly", "drawing", "render")],
        "logout": js[code]["panel.session.logout"],
        "panels": [
            ["#parametric-panel", js[code]["panel.parametric.title"]],
            [".surfaces-panel", js[code]["panel.surfaces.heading"]],
            [".assembly-panel", js[code]["panel.assembly.title"]],
            [".drawing-panel", js[code]["panel.drawing.heading"]],
            [".oc-render-panel", js[code]["panel.render.title"]],
        ],
    }


def language_expression(code, expected):
    return ("(() => { const w = document.querySelector('.industrial-workbench'); "
            "const s = document.querySelector('#locale'); "
            f"if (!w || !s || s.value !== {jsstr(code)}) return false; "
            "const nodes = Array.from(w.querySelectorAll('details > summary')).slice(0, 5); "
            "const panels = " + jsstr(expected["panels"]) + ".every(([selector, text]) => "
            "{ const node = document.querySelector(selector);"
            " return Boolean(node && node.textContent.includes(text)); }); "
            "return w.getAttribute('aria-label') === " + jsstr(expected["aria"]) +
            " && nodes.length === 5 && JSON.stringify(nodes.map(n => n.textContent)) === " +
            "JSON.stringify(" + jsstr(expected["summaries"]) + ") && panels; })()")


class Checks:
    def __init__(self, report):
        self.report = report

    def check(self, name, condition, detail=None):
        item = {"name": name, "pass": bool(condition)}
        if detail is not None:
            item["detail"] = detail
        self.report["checks"].append(item)
        if not condition:
            raise AssertionError(name)


def login_and_create(browser, username, password, project_name):
    browser.wait("!!document.querySelector('dialog[data-session-entry] input[name=username]')")
    browser.click("dialog[data-session-entry] input[name=username]")
    browser.call("Input.insertText", {"text": username})
    browser.click("dialog[data-session-entry] input[name=password]")
    browser.call("Input.insertText", {"text": password})
    browser.click("dialog[data-session-entry] button[type=submit]")
    create = "dialog[data-session-entry] button[data-panel-i18n=\"panel.session.create_project\"]"
    browser.wait("!!document.querySelector(%s)" % jsstr(create))
    name = "dialog[data-session-entry] input[data-panel-i18n-placeholder=\"panel.session.new_project_name\"]"
    browser.click(name)
    browser.call("Input.insertText", {"text": project_name})
    browser.click(create)
    browser.wait("!document.querySelector('dialog[data-session-entry]')", timeout=30)
    browser.wait("!!document.querySelector('.industrial-workbench')", timeout=30)
    browser.wait("window.opencad3d && !window.opencad3d.busy", timeout=30)


def inspect_language(browser, code):
    state = browser.evaluate(
        "fetch('/api/state').then(r=>r.json()).then(s=>({revision:s.revision,"
        "sha256:s.sha256,project_id:s.project_id,locale:s.locale}))")
    ui = browser.evaluate(
        "({depth: document.querySelector('#depth')?.value,"
        "focused: document.activeElement === document.querySelector('#depth'),"
        "errors: window.__acceptErrors.slice(),"
        "calls: window.__acceptCalls.slice()})")
    return {"code": code, "state": state, "ui": ui}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    out = args.out.absolute()
    runtime = args.runtime_root.absolute()
    source_root = args.source_root.absolute()
    app_root = source_root / "oss-alternatives/apps/opencad"
    if not (app_root / "secure_web.py").is_file():
        raise ValueError("source root has no OpenCAD app")
    if (not out.is_absolute() or ".." in out.parts or
            not out.is_relative_to(Path("/hf3fs-jd/data/tmp")) or out.exists()):
        raise ValueError("fresh external --out required")
    if (not runtime.is_absolute() or ".." in runtime.parts or
            not runtime.is_relative_to(Path("/data/tmp")) or runtime.exists()):
        raise ValueError("fresh local --runtime-root required")
    if not Path(args.browser).is_file() or not os.access(args.browser, os.X_OK):
        raise ValueError("real browser executable required")
    out.mkdir(parents=True, exist_ok=False)
    runtime.mkdir(parents=True, exist_ok=False)
    old_tmp = tempfile.tempdir
    tempfile.tempdir = str(runtime)
    sys.path.insert(0, str(app_root))
    from access_control import AuthController
    from project_store import ProjectStore
    from project_gateway import ProjectGateway
    from secure_web import build_server

    js = catalog(app_root)
    report = {
        "schema": "opencad.private.locale.browser/1",
        "verdict": "FAIL", "actual_rc": 1, "started_at": now(),
        "checks": [], "browser": args.browser, "source_root": str(source_root),
        "production_mutated": False,
        "gpu_speed": "NOT_ASSESSED", "industrial_parity": "NOT_ASSESSED",
    }
    before = {name: digest((app_root / name).read_bytes()) for name in SOURCES}
    before["__acceptance_tool__"] = digest(TOOL.read_bytes())
    httpd = thread = app = None
    try:
        local = tempfile.mkdtemp(prefix="locale-browser-db-", dir=str(runtime))
        dbroot = Path(local)
        auth = AuthController(dbroot / "accounts", allow_registration=True)
        password = uuid.uuid4().hex
        auth.register("locale-browser-user", password)
        app = ProjectGateway(auth, ProjectStore(dbroot / "projects"), dbroot / "runtime",
                             secure_cookies=False)
        httpd = build_server(app)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{httpd.server_address[1]}"
        report["origin"] = origin
        Browser = load_browser(source_root)
        checks = Checks(report)
        for width, height in ((1440, 900), (390, 844)):
            tag = str(width)
            with Browser(args.browser, out / tag, width=width, height=height,
                         source_root=source_root) as browser:
                browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})
                browser.call("Page.navigate", {"url": origin + "/web/3d.html"})
                try:
                    login_and_create(browser, "locale-browser-user", password,
                                     "Locale browser " + str(width))
                    browser.wait("window.OpenCADLocale && window.OpenCADPrivateLocale", timeout=30)
                    browser.wait("window.opencad3d && !window.opencad3d.busy", timeout=30)
                    ready = browser.evaluate(
                        "Promise.all([window.OpenCADLocale.ready,"
                        "window.OpenCADPrivateLocale.ready]).then("
                        "()=>window.OpenCADLocale.snapshot().status,()=> 'error')")
                    checks.check(tag + "-locale-ready", ready == "ready", ready)
                    browser.wait("document.querySelectorAll('#locale option').length >= 3",
                                 timeout=30)
                    options = browser.evaluate(
                        "Array.from(document.querySelectorAll('#locale option')).map(o=>o.value)")
                    checks.check(tag + "-three-locales",
                                 set(options) >= {"en", "ja", "zh-CN"}, options)
                    browser.evaluate(
                        "Array.from(document.querySelectorAll('.industrial-workbench > details'))"
                        ".forEach(details => { details.open = true; });")
                    browser.wait(
                        "['#parametric-panel', '.surfaces-panel', '.assembly-panel',"
                        " '.drawing-panel', '.oc-render-panel'].every(selector =>"
                        " Boolean(document.querySelector(selector)))", timeout=45)
                    checks.check(tag + "-five-panels-mounted", True)
                    draft = browser.evaluate(
                        "(() => { const d=document.querySelector('#depth'); if(!d) return null;"
                        " d.value='17'; d.dispatchEvent(new Event('input',{bubbles:true}));"
                        " d.dataset.dirty='true'; d.focus();"
                        " return document.activeElement===d ? d.value : null; })()")
                    checks.check(tag + "-draft-fixture", draft == "17", draft)
                    expected_start = browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then("
                        "s=>({revision:s.revision,sha256:s.sha256,project_id:s.project_id}))")
                    checks.check(tag + "-initial-state", bool(expected_start), expected_start)
                    for code in ("ja", "zh-CN", "en"):
                        wanted = expected_language(js, code)
                        report["last_expected"] = {
                            "code": code, "aria": wanted["aria"], "summaries": wanted["summaries"]}
                        before_ui = browser.evaluate(
                            "({depth: document.querySelector('#depth')?.value,"
                            "focused: document.activeElement===document.querySelector('#depth'),"
                            "errors: window.__acceptErrors.slice()})")
                        browser.evaluate("window.__acceptCalls.length=0")
                        browser.evaluate(
                            "(function(){var s=document.querySelector('#locale');"
                            f"s.value={jsstr(code)};"
                            "s.dispatchEvent(new Event('change',{bubbles:true}));return true;})()")
                        browser.wait(language_expression(code, wanted), timeout=45)
                        after = browser.evaluate(
                            "fetch('/api/state').then(r=>r.json()).then(s=>({revision:s.revision,"
                            "sha256:s.sha256,project_id:s.project_id,locale:s.locale}))")
                        after_ui = browser.evaluate(
                            "({depth: document.querySelector('#depth')?.value,"
                            "focused: document.activeElement===document.querySelector('#depth'),"
                            "errors: window.__acceptErrors.slice(),"
                            "calls: window.__acceptCalls.slice(),"
                            "logout:document.querySelector("
                            "'.project-status button[data-panel-i18n=\"panel.session.logout\"]')"
                            "?.textContent})")
                        checks.check(tag + "-" + code + "-state-unchanged",
                                     all(after[key] == expected_start[key]
                                         for key in ("revision", "sha256", "project_id")),
                                     after)
                        checks.check(tag + "-" + code + "-selected", after["locale"] == code, after)
                        checks.check(tag + "-" + code + "-draft-preserved",
                                     after_ui["depth"] == "17" and after_ui["focused"], before_ui)
                        checks.check(tag + "-" + code + "-session-copy",
                                     after_ui["logout"] == wanted["logout"], after_ui["logout"])
                        changes = [item for item in after_ui["calls"]
                                   if item["method"] == "POST" and item["path"] != "/api/locale"]
                        checks.check(tag + "-" + code + "-no-project-mutation",
                                     not changes, after_ui["calls"])
                        checks.check(tag + "-" + code + "-no-page-error",
                                     not after_ui["errors"], after_ui["errors"])
                        browser.evaluate(
                            "document.querySelector('.industrial-workbench')"
                            ".scrollIntoView({block:'start'})")
                        browser.screenshot(tag + "-" + code)
                except Exception:
                    report["browser_failure"] = {
                        "width": width,
                        "state": browser.evaluate(
                            "({url:location.href, title:document.title,"
                            "dialog:!!document.querySelector('dialog'),"
                            "status:document.querySelector('#status')?.textContent,"
                            "body:document.body.innerText.slice(0,2000),"
                            "locale_value:document.querySelector('#locale')?.value,"
                            "workbench:(() => {const w=document.querySelector('.industrial-workbench');"
                            "return w ? {aria:w.getAttribute('aria-label'),"
                            "attrs:Array.from(w.attributes).map(a=>[a.name,a.value]),"
                            "summaries:Array.from(w.querySelectorAll('details > summary')).map(n=>n.textContent),"
                            "details:w.querySelectorAll('details').length} : null;})(),"
                            "panel_roots:Object.fromEntries(['#parametric-panel','.surfaces-panel',"
                            "'.assembly-panel','.drawing-panel','.oc-render-panel']"
                            ".map(selector => [selector, Boolean(document.querySelector(selector))]))})"),
                    }
                    browser.screenshot(tag + "-failure")
                    raise
            if width == 1440:
                # Exercise the real shield contract in a second, desktop browser view.
                with Browser(args.browser, out / "shield", width=1440, height=900,
                             source_root=source_root) as browser:
                    browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": INSTRUMENT})
                    browser.call("Page.navigate", {"url": origin + "/web/3d.html"})
                    login_and_create(browser, "locale-browser-user", password,
                                     "Locale shield")
                    browser.wait("document.querySelectorAll('#locale option').length >= 3")
                    status = browser.evaluate(
                        "fetch('/api/__acceptance_missing__').then(r=>r.status,()=>-1)")
                    browser.wait("document.body.dataset.opencadSessionLocked === 'true'", timeout=30)
                    checks.check("shield-locked", status == 404, status)
                    safe = ("(() => { const n=document.createElement('output');"
                            " n.setAttribute('role','alert'); n.setAttribute('lang','en');"
                            " n.setAttribute('data-opencad-public-locale-error','parametric');"
                            " n.textContent='parametric_locale_initialization_failed';"
                            " document.body.append(n); return true; })()")
                    browser.evaluate(safe)
                    browser.wait("!!document.querySelector('[data-opencad-public-locale-error=parametric]')")
                    check_safe = browser.evaluate(
                        "(() => { const n=document.querySelector('[data-opencad-public-locale-error=parametric]');"
                        " return {visible:getComputedStyle(n).visibility!=='hidden',"
                        " inert:n.inert, role:n.getAttribute('role'), lang:n.getAttribute('lang'),"
                        " text:n.textContent, children:n.children.length,"
                        " attrs:Array.from(n.attributes).map(a=>a.name).sort()}; })()")
                    checks.check("shield-safe-alert-visible",
                                 check_safe["visible"] and not check_safe["inert"] and
                                 check_safe["role"] == "alert" and check_safe["lang"] == "en" and
                                 check_safe["text"] == "parametric_locale_initialization_failed" and
                                 check_safe["children"] == 0 and
                                 set(check_safe["attrs"]) ==
                                 {"role", "lang", "data-opencad-public-locale-error"},
                                 check_safe)
                    bad = ("(() => { const n=document.createElement('output');"
                           " n.setAttribute('role','alert'); n.setAttribute('lang','en');"
                           " n.setAttribute('data-opencad-public-locale-error','private');"
                           " n.setAttribute('title','private-title');"
                           " n.textContent='private_locale_initialization_failed';"
                           " document.body.append(n); return true; })()")
                    browser.evaluate(bad)
                    browser.wait(
                        "(() => { const n=Array.from(document.querySelectorAll('output[role=alert]'))"
                        ".find(x=>x.getAttribute('title')==='private-title');"
                        " return n && n.inert && getComputedStyle(n).visibility==='hidden'; })()")
                    bad_state = browser.evaluate(
                        "(() => { const n=Array.from(document.querySelectorAll('output[role=alert]'))"
                        ".find(x=>x.getAttribute('title')==='private-title');"
                        " return {inert:n.inert,visibility:getComputedStyle(n).visibility,"
                        "text:n.textContent,role:n.getAttribute('role')}; })()")
                    checks.check("shield-nonconforming-alert-hidden",
                                 bad_state["inert"] and bad_state["visibility"] == "hidden",
                                 bad_state)
                    checks.check("shield-safe-survives-repeat",
                                 browser.evaluate(
                                     "(() => { const n=document.querySelector("
                                     "'[data-opencad-public-locale-error=parametric]');"
                                     " return getComputedStyle(n).visibility!=='hidden'&&!n.inert; })()"))
                    browser.screenshot("shield-alerts")
            report["checks"].append({"name": tag + "-viewport-complete", "pass": True})
        report["verdict"] = "PASS"
        report["actual_rc"] = 0
    except Exception:
        report["cause"] = traceback.format_exc()
    finally:
        if httpd is not None:
            httpd.shutdown()
            if thread is not None:
                thread.join(timeout=10)
            httpd.server_close()
        if app is not None:
            app.close()
        tempfile.tempdir = old_tmp
    try:
        after = {name: digest((app_root / name).read_bytes()) for name in SOURCES}
        after["__acceptance_tool__"] = digest(TOOL.read_bytes())
        report["source_before"] = before
        report["source_after"] = after
        report["source_drift"] = sorted(name for name in before if before[name] != after[name])
        if report["source_drift"]:
            report["verdict"], report["actual_rc"] = "FAIL", 1
    except Exception:
        report["source_error"] = traceback.format_exc()
    report["finished_at"] = now()
    report["screenshots"] = [str(p.relative_to(out)) for p in sorted(out.rglob("*.png"))]
    report["two_d_browser_acceptance"] = "NOT_EXECUTED"
    report["cad_model_acceptance"] = "SEPARATE_EXISTING_HARNESS"
    marker = (f"OPENCAD_PRIVATE_LOCALE_BROWSER verdict={report['verdict']} "
              f"checks={len(report['checks'])} rc={report['actual_rc']} "
              f"at={report['finished_at']}")
    write_exclusive(out / "completion.log", (marker + "\n").encode())
    write_exclusive(out / "result.json",
                    (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
    print(marker)
    print("evidence=" + str(out))
    if report.get("cause"):
        print(report["cause"])
    return report["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
