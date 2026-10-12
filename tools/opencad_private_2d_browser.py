#!/usr/bin/env python3
"""Real 2D private-page modelling, language-draft and account-recovery acceptance.

Runs only against an explicit local OpenCAD source root. The shipped private
server application is built in-process on loopback with temporary accounts and
SQLite roots, then a headless Chromium is driven through the actual 2D UI:
login, project creation, a real command-line line, a language switch with an
unsaved command, a real offline shield and same-page refresh recovery, a
full-page reload/reopen, explicit logout, a second account's isolation, and
recovery of the first account's durable project. Desktop and mobile widths are
both exercised.

The source must be a clean committed tree. No production service, credentials,
GPU, or network exposure is used. Passwords are generated in memory and are not
persisted. This tool does not certify 3D, industrial parity, production
multi-tenancy, or the full all-stage suite.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
MARKER = "OPENCAD_PRIVATE_2D_MODEL"


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, cause):
    if not condition:
        raise AssertionError(cause)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, value):
    data = value if isinstance(value, bytes) else (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def git(root, *args):
    return subprocess.run(["/usr/bin/git", "--no-optional-locks", "-C", str(root), *args],
                          capture_output=True, check=True).stdout


def source_state(root):
    head = git(root, "rev-parse", "HEAD").decode().strip()
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode()
    entries = {}
    names = git(root, "ls-files", "-z").split(b"\0")
    for raw in names:
        if not raw:
            continue
        name = raw.decode("utf-8")
        path = root / name
        require(path.is_file() and not path.is_symlink(), "nonregular_tracked_source:" + name)
        data = path.read_bytes()
        entries[name] = {"sha256": digest(data), "bytes": len(data),
                         "mode": path.stat().st_mode & 0o777}
    manifest = digest(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())
    return {"head": head, "status": status, "files": len(entries),
            "manifest_sha256": manifest, "entries": entries}


def load_locale_tool(source_root):
    path = source_root / "tools/opencad_private_locale_browser.py"
    spec = importlib.util.spec_from_file_location("private_2d_locale_tool", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def core_strings(source_root, code):
    path = (source_root / "oss-alternatives/packages/cadcore/cadcore/data/i18n" /
            (code + ".json"))
    return json.loads(path.read_text(encoding="utf-8"))["strings"]


def state(browser):
    value = browser.evaluate(
        "fetch('/api/state').then(async r=>({status:r.status,value:await r.json()}))")
    require(value["status"] == 200 and value["value"].get("ok") is True,
            "state_read_failed:" + repr(value)[:300])
    return value["value"]


def identity(value):
    return {key: value.get(key) for key in
            ("project_id", "revision", "sha256", "doc", "entities")}


def fill(browser, selector, value):
    browser.click(selector)
    browser.key("a", modifiers=2)
    browser.call("Input.insertText", {"text": value})


def press_enter(browser):
    for kind in ("keyDown", "keyUp"):
        browser.call("Input.dispatchKeyEvent", {
            "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        })


def entry_username_wait(browser):
    browser.wait("!!document.querySelector("
                 "'dialog[data-session-entry] input[name=username]')", timeout=45)


def login_submit(browser, username, password):
    entry = "dialog[data-session-entry]"
    fill(browser, entry + " input[name=username]", username)
    fill(browser, entry + " input[name=password]", password)
    browser.click(entry + " button[type=submit]")
    browser.wait("!!document.querySelector("
                 "'dialog[data-session-entry] input"
                 "[data-panel-i18n-placeholder=\"panel.session.new_project_name\"]')",
                 timeout=45)


def create_project(browser, title):
    entry = "dialog[data-session-entry]"
    name = (entry + ' input'
            '[data-panel-i18n-placeholder="panel.session.new_project_name"]')
    submit = entry + ' button[data-panel-i18n="panel.session.create_project"]'
    fill(browser, name, title)
    browser.evaluate("document.querySelector(%s).click()" % json.dumps(submit))
    browser.wait("!document.querySelector(%s)" % json.dumps(entry), timeout=45)
    app_ready(browser)


def app_ready(browser):
    browser.wait(
        "Boolean(window.__opencadV3 && window.__opencadV3.ready && "
        "window.__opencadV3.ready() && "
        "!document.body.dataset.opencadSessionLocked && "
        "document.querySelectorAll('#lang-select option').length>=3 && "
        "document.querySelector('#cmd-line') && document.querySelector('#canvas'))",
        timeout=60)


def project_options(browser):
    return browser.evaluate(
        "Array.from(document.querySelectorAll("
        "'dialog[data-session-entry] select option')).map("
        "o=>({value:o.value,text:o.textContent}))")


def open_project(browser, project_id):
    entry = "dialog[data-session-entry]"
    browser.wait("Array.from(document.querySelector(%s + ' select').options)"
                 ".some(o=>o.value===%s)" % (json.dumps(entry), json.dumps(project_id)),
                 timeout=45)
    values = browser.evaluate(
        "Array.from(document.querySelector(%s + ' select').options).map(o=>o.value)" %
        json.dumps(entry))
    require(project_id in values, "owned_project_missing")
    browser.evaluate(
        "(function(){const s=document.querySelector(%s + ' select');"
        "s.value=%s;s.dispatchEvent(new Event('change',{bubbles:true}));})()" %
        (json.dumps(entry), json.dumps(project_id)))
    browser.evaluate(
        "document.querySelector(%s).click()" %
        json.dumps(entry + ' button[data-panel-i18n="panel.session.open_project"]'))
    browser.wait("!document.querySelector(%s)" % json.dumps(entry), timeout=45)
    app_ready(browser)


def logout(browser):
    button = ('document.querySelector(".project-status button'
              '[data-panel-i18n=\\"panel.session.logout\\"]")')
    browser.evaluate(button + ".click()")
    time.sleep(0.7)
    entry_username_wait(browser)


def draw_line(browser, previous_revision):
    browser.wait(
        "(async function(){const r=await fetch('/api/state');const s=await r.json();"
        "return s.ok===true && s.entities>=1 && s.revision>" + str(previous_revision) + ";})()",
        timeout=60)
    value = state(browser)
    require(value["entities"] >= 1, "2d_line_not_persisted")
    return value


def canvas_hash(browser):
    return browser.evaluate(
        "(async function(){"
        "const c=document.querySelector('#canvas');"
        "if(!c) throw Error('canvas_missing');"
        "await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));"
        "const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;"
        "const b=await crypto.subtle.digest('SHA-256',d);"
        "return Array.from(new Uint8Array(b)).map(x=>x.toString(16).padStart(2,'0')).join('');"
        "})()")


def switch_language(browser, code):
    return browser.evaluate(
        "new Promise((resolve,reject)=>{"
        "const s=document.querySelector('#lang-select');"
        "if(!s) return reject(Error('lang_select_missing'));"
        "const done=()=>{window.removeEventListener('opencad:locale',done);resolve(true);};"
        "window.addEventListener('opencad:locale',done);"
        "setTimeout(()=>reject(Error('locale_event_timeout')),15000);"
        "s.value=%s;s.dispatchEvent(new Event('change',{bubbles:true}));"
        "})" % json.dumps(code))


def page_errors(browser):
    return browser.evaluate("window.__acceptErrors ? window.__acceptErrors.slice() : null")


def screenshots(out):
    result = []
    for path in sorted(out.rglob("*.png")):
        data = path.read_bytes()
        result.append({"path": path.relative_to(out).as_posix(), "bytes": len(data),
                       "sha256": digest(data)})
    return result


def run_view(browser_class, args, out, origin, source_root, alice, bob, width, height,
             checks):
    tag = str(width)
    prefix = tag + "-"
    view_out = out / tag
    view_out.mkdir()
    errors = []

    def check(name, condition, **detail):
        item = {"name": prefix + name, "pass": bool(condition), "at": now()}
        if detail:
            item["detail"] = detail
        checks.append(item)
        if not condition:
            raise AssertionError(prefix + name + ":" + repr(detail)[:400])

    def collect_errors(stage):
        value = page_errors(browser)
        if value is not None:
            errors.extend({"stage": stage, "error": item} for item in value)
            require(not value, "page_error:" + stage + ":" + repr(value)[:300])
            browser.evaluate("window.__acceptErrors.length=0")

    Browser = browser_class
    with Browser(args.browser, view_out, width=width, height=height,
                 source_root=source_root) as browser:
        browser.call("Page.addScriptToEvaluateOnNewDocument",
                     {"source": load_locale_tool(source_root).INSTRUMENT})
        browser.call("Network.enable", {})
        browser.call("Page.navigate", {"url": origin + "/"})
        entry_username_wait(browser)
        login_submit(browser, "alice", alice["password"])
        options = project_options(browser)
        require(all(item["value"] for item in options), "invalid_project_option")
        create_project(browser, "Alice 2D " + tag)
        alice_state = state(browser)
        alice_title = "Alice 2D " + tag
        check("opened-2d-project", alice_state["entities"] == 0,
              project_id=alice_state["project_id"], entities=alice_state["entities"])
        collect_errors("open")
        before_canvas = canvas_hash(browser)
        initial_revision = alice_state["revision"]
        fill(browser, "#cmd-line", "line 0,0 40,20")
        check("draft-before-submit", browser.evaluate(
            "document.querySelector('#cmd-line').value==='line 0,0 40,20'"))
        press_enter(browser)
        drawn = draw_line(browser, initial_revision)
        collect_errors("draw")
        drawn_canvas = canvas_hash(browser)
        check("line-committed-in-ui", drawn["entities"] >= 1 and
              drawn["revision"] > initial_revision and
              browser.evaluate("document.querySelector('#status-entities').textContent.trim()!==''"),
              revision=drawn["revision"], entities=drawn["entities"])
        check("canvas-changed-after-line", before_canvas != drawn_canvas,
              before=before_canvas, after=drawn_canvas)
        browser.screenshot("alice-2d")
        draft = "line 60,0 80,10"
        fill(browser, "#cmd-line", draft)
        check("ui-draft-entered", browser.evaluate(
            "document.activeElement===document.querySelector('#cmd-line') && "
            "document.querySelector('#cmd-line').value===" + json.dumps(draft)))
        pre_switch = identity(state(browser))
        browser.evaluate("window.__acceptCalls.length=0")
        current_locale = browser.evaluate("document.querySelector('#lang-select').value")
        target = "ja" if current_locale != "ja" else "en"
        try:
            switch_language(browser, target)
        except RuntimeError as exc:
            errors.append({"stage": "locale", "error": str(exc)})
            raise
        expected = core_strings(source_root, target)
        browser.wait("document.documentElement.lang===" + json.dumps(target) +
                     " && document.querySelector('#lang-select').value===" + json.dumps(target),
                     timeout=45)
        after_switch_ui = browser.evaluate(
            "({lang:document.documentElement.lang,value:document.querySelector('#lang-select').value,"
            "draft:document.querySelector('#cmd-line').value,"
            "draftFocused:document.activeElement===document.querySelector('#cmd-line'),"
            "aria:document.querySelector('#cmd-line').getAttribute('aria-label'),"
            "calls:window.__acceptCalls.slice(),errors:window.__acceptErrors.slice()})")
        after_switch = identity(state(browser))
        after_switch_canvas = canvas_hash(browser)
        forbidden = [item for item in after_switch_ui["calls"] if
                     item.get("method") == "POST" and
                     item.get("path") not in ("/api/locale",)]
        check("language-switched-to-" + target,
              after_switch_ui["lang"] == target and after_switch_ui["value"] == target and
              after_switch_ui["aria"] == expected["cmd.label"],
              lang=after_switch_ui["lang"], aria=after_switch_ui["aria"])
        check("language-preserves-draft-and-focus",
              after_switch_ui["draft"] == draft and after_switch_ui["draftFocused"])
        check("language-does-not-mutate-project", after_switch == pre_switch,
              before=pre_switch, after=after_switch)
        check("language-has-no-project-post", not forbidden, calls=after_switch_ui["calls"])
        check("language-keeps-entity-value",
              after_switch["entities"] == pre_switch["entities"] and
              browser.evaluate("document.querySelector('#status-entities').textContent.trim()!==''"),
              entities=after_switch["entities"])
        collect_errors("locale")
        browser.evaluate("window.__2dRecoveryNode=document.querySelector('#cmd-line');true")
        browser.call("Network.emulateNetworkConditions", {
            "offline": True, "latency": 0, "downloadThroughput": 0, "uploadThroughput": 0})
        offline_read = browser.evaluate(
            "fetch('/api/state').then(()=>false,()=>true)")
        browser.wait("document.body.dataset.opencadSessionLocked==='true'", timeout=30)
        offline_ui = browser.evaluate(
            "({locked:document.body.dataset.opencadSessionLocked==='true',"
            "draft:document.querySelector('#cmd-line')?.value||null,"
            "errors:window.__acceptErrors.slice()})")
        check("offline-shields-and-retains-draft",
              offline_read and offline_ui["locked"] and offline_ui["draft"] == draft and
              not offline_ui["errors"], ui=offline_ui)
        browser.screenshot("alice-offline")
        browser.call("Network.emulateNetworkConditions", {
            "offline": False, "latency": 0,
            "downloadThroughput": -1, "uploadThroughput": -1})
        browser.wait(
            "!document.querySelector('.project-status button[data-panel-i18n=\"panel.session.refresh\"]').hidden",
            timeout=30)
        browser.evaluate(
            "document.querySelector('.project-status button[data-panel-i18n=\"panel.session.refresh\"]').click();true")
        browser.wait("document.body.dataset.opencadSessionLocked!=='true'", timeout=45)
        time.sleep(0.3)
        recovery_ui = browser.evaluate(
            "({sameNode:window.__2dRecoveryNode===document.querySelector('#cmd-line'),"
            "draft:document.querySelector('#cmd-line')?.value||null,"
            "errors:window.__acceptErrors.slice()})")
        recovered_state = identity(state(browser))
        check("offline-refresh-restores-same-page",
              recovery_ui["sameNode"] and recovery_ui["draft"] == draft and
              not recovery_ui["errors"] and recovered_state == after_switch,
              ui=recovery_ui, expected=after_switch, actual=recovered_state)
        collect_errors("offline-recovery")
        browser.screenshot("alice-offline-recovered")
        saved = identity(state(browser))
        browser.screenshot("alice-language")
        before_reload_errors = page_errors(browser)
        require(not before_reload_errors, "page_error:pre-reload:" + repr(before_reload_errors))
        browser.call("Page.navigate", {"url": origin + "/"})
        time.sleep(0.7)
        browser.wait(
            "document.readyState==='complete' && ("
            "!!document.querySelector('dialog[data-session-entry] select') || "
            "(!!window.__opencadV3 && __opencadV3.ready() && "
            "!document.body.dataset.opencadSessionLocked))", timeout=60)
        if browser.evaluate("!!document.querySelector('dialog[data-session-entry] select')"):
            open_project(browser, saved["project_id"])
        app_ready(browser)
        recovered = identity(state(browser))
        check("full-reload-recovers-project", recovered == saved,
              expected=saved, actual=recovered)
        check("full-reload-preserves-language",
              browser.evaluate("document.querySelector('#lang-select').value===" +
                               json.dumps(target) +
                               " && document.documentElement.lang===" + json.dumps(target)),
              target=target)
        check("full-reload-renders-entities",
              browser.evaluate("document.querySelector('#status-entities').textContent.trim()!==''"))
        collect_errors("reload")
        browser.screenshot("alice-reloaded")
        logout(browser)
        login_submit(browser, "bob", bob["password"])
        bob_options = project_options(browser)
        check("bob-cannot-see-alice-project",
              all(item["value"] != saved["project_id"] and item["text"] != alice_title
                  for item in bob_options), options=bob_options)
        create_project(browser, "Bob 2D " + tag)
        bob_state = state(browser)
        check("bob-has-distinct-empty-project",
              bob_state["project_id"] != saved["project_id"] and bob_state["entities"] == 0 and
              bob_state.get("doc") != saved.get("doc"),
              project_id=bob_state["project_id"], entities=bob_state["entities"])
        check("bob-does-not-inherit-alice-canvas",
              canvas_hash(browser) != after_switch_canvas)
        collect_errors("bob")
        browser.screenshot("bob-2d")
        logout(browser)
        login_submit(browser, "alice", alice["password"])
        open_project(browser, saved["project_id"])
        restored = identity(state(browser))
        check("alice-project-restored-after-switch", restored == saved,
              expected=saved, actual=restored)
        check("alice-language-restored-after-switch",
              browser.evaluate("document.querySelector('#lang-select').value===" +
                               json.dumps(target)))
        check("alice-2d-account-switch-no-page-error", not collect_errors("final-alice") and
              not page_errors(browser))
        browser.screenshot("alice-recovered")
        check("no-page-errors", not errors and not page_errors(browser), errors=errors)
    return


def acceptance(args):
    source_root = args.source_root.resolve(strict=True)
    out, runtime = args.out.resolve(), args.runtime_root.resolve()
    app = source_root / "oss-alternatives/apps/opencad"
    require((app / "secure_web.py").is_file(), "source_root_has_no_opencad")
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) and not out.exists(),
            "fresh_external_out_required")
    require(runtime.is_relative_to(Path("/data/tmp").resolve()) and not runtime.exists(),
            "fresh_local_runtime_required")
    require(Path(args.browser).is_file() and os.access(args.browser, os.X_OK),
            "real_browser_required")
    out.mkdir(parents=True)
    runtime.mkdir(parents=True)
    sys.dont_write_bytecode = True
    for package in (app, source_root / "oss-alternatives/packages/cadcore",
                    source_root / "oss-alternatives/packages/dxf",
                    source_root / "oss-alternatives/packages/vault"):
        sys.path.insert(0, str(package))
    old_temp = tempfile.tempdir
    tempfile.tempdir = str(runtime)
    locale_tool = load_locale_tool(source_root)
    Browser = locale_tool.load_browser(source_root)
    report = {"schema": "opencad.private.2d.model.browser/1", "verdict": "FAIL",
              "actual_rc": 1, "started_at": now(), "source_root": str(source_root),
              "browser": str(Path(args.browser).resolve()), "checks": [],
              "first_cause": None, "cleanup_errors": [], "source_drift": [],
              "production_mutated": False, "gpu_assessed": False,
              "scope": "2D local desktop/mobile model, language draft, reload and account recovery"}
    source_before = source_state(source_root)
    report["source_before"] = source_before
    report["tool_sha256"] = digest(Path(__file__).read_bytes())
    httpd = thread = app_gateway = None
    try:
        require(not source_before["status"], "clean_committed_source_required")
        from access_control import AuthController
        from project_store import ProjectStore
        from project_gateway import ProjectGateway
        from secure_web import build_server
        local = tempfile.mkdtemp(prefix="2d-model-db-", dir=str(runtime))
        dbroot = Path(local)
        auth = AuthController(dbroot / "accounts", allow_registration=True)
        accounts = {}
        for name in ("alice", "bob"):
            password = uuid.uuid4().hex
            record = auth.register(name, password)
            accounts[name] = {**record, "password": password}
        app_gateway = ProjectGateway(auth, ProjectStore(dbroot / "projects"),
                                     dbroot / "runtime", secure_cookies=False)
        httpd = build_server(app_gateway)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01},
                                  daemon=True)
        thread.start()
        origin = "http://127.0.0.1:" + str(httpd.server_address[1])
        report["origin"] = origin
        for width, height in ((1440, 900), (390, 844)):
            viewport_checks = []
            try:
                run_view(Browser, args, out, origin, source_root, accounts["alice"],
                         accounts["bob"], width, height, viewport_checks)
            finally:
                report["checks"].extend(viewport_checks)
        report["verdict"], report["actual_rc"] = "PASS", 0
    except Exception:
        report["first_cause"] = traceback.format_exc()
    finally:
        tempfile.tempdir = old_temp
        try:
            if httpd is not None:
                httpd.shutdown()
                if thread is not None:
                    thread.join(10)
                httpd.server_close()
            if app_gateway is not None:
                app_gateway.close()
        except Exception as error:
            report["cleanup_errors"].append(type(error).__name__ + ":" + str(error)[:200])
    source_after = source_state(source_root)
    report["source_after"] = source_after
    report["source_drift"] = [key for key in ("head", "status", "manifest_sha256", "files")
                              if source_before.get(key) != source_after.get(key)]
    report["screenshots"] = screenshots(out)
    if report["source_drift"] or report["cleanup_errors"]:
        report["verdict"], report["actual_rc"] = "FAIL", 1
        if report["first_cause"] is None:
            report["first_cause"] = "source_drift_or_cleanup_error"
    report["finished_at"] = now()
    write_new(out / "source-before.json", source_before)
    write_new(out / "source-after.json", source_after)
    write_new(out / "result.json", report)
    marker = (f"{MARKER} verdict={report['verdict']} checks={len(report['checks'])} "
              f"rc={report['actual_rc']}\n")
    write_new(out / "completion.log", marker.encode())
    print(marker.strip(), flush=True)
    print("evidence=" + str(out), flush=True)
    if report["first_cause"]:
        print(report["first_cause"], flush=True)
    return report["actual_rc"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    args = parser.parse_args(argv)
    return acceptance(args)


if __name__ == "__main__":
    raise SystemExit(main())
