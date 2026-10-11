#!/usr/bin/env python3
"""Real private-project browser acceptance on isolated local runtime databases.

No production service or real account is used. Passwords are generated in
memory and never written into CDP logs, source fingerprints or screenshots.
Software browser rendering tests UI correctness, not server GPU acceleration.
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
sys.path.insert(0, str(APP))
from access_control import AuthController
from project_store import ProjectStore
from project_gateway import ProjectGateway
from secure_web import build_server


def load_browser():
    path = ROOT / "oss-alternatives/tools/cad3d/viewer_controls_check.py"
    spec = importlib.util.spec_from_file_location("private_browser_helper", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class PrivateBrowser(module.BrowserSession):
        def __exit__(self, exc_type, exc, tb):
            if exc_type is not None and self.ws is not None:
                state = self.evaluate(
                    "({notice:document.querySelector('.project-status output')?.textContent,"
                    "buttons:Array.from(document.querySelectorAll('.project-status button')).map("
                    "b=>({text:b.textContent,hidden:b.hidden,disabled:b.disabled})),"
                    "dialog:!!document.querySelector('dialog[open]'),status:"
                    "document.querySelector('#status')?.textContent})")
                write(self.out / "failure-state.json", json.dumps(state).encode())
                self.screenshot("failure")
            return super().__exit__(exc_type, exc, tb)

        def call(self, method, params):
            result = self.helper._cdp_call(self.ws, self.sequence, method, params, 10)
            # Even test-generated credentials never enter persisted events.
            self.events.append({"method": method, "ok": "error" not in result})
            if "error" in result or "exceptionDetails" in result.get("result", {}):
                raise RuntimeError("Private browser operation failed: " + method)
            return result["result"]

    return PrivateBrowser


def write(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runtime-root", type=Path,
                        default=Path("/data/dev/opencad-private-fixtures"))
    parser.add_argument("--browser", default=
                        "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or Path("/hf3fs-jd/data/tmp/opencad-private-web") /
           (stamp + "-browser")).resolve()
    if out.is_relative_to(ROOT) or args.runtime_root.resolve().is_relative_to(ROOT):
        raise ValueError("Evidence and runtime databases must be external")
    out.mkdir(parents=True, exist_ok=False)
    sources = [
        APP / name for name in (
            "secure_web.py", "project_gateway.py", "project_store.py", "access_control.py",
            "web/js/project-session.js", "web/css/project-session.css",
            "web/js/viewer3d.js", "web/js/advanced3d.js", "viewer3d_page.py",
            "web/js/parametric-panel.js", "web/js/drawing-panel.js",
            "web/js/industrial-panels.js", "web/js/app.js", "server.py",
        )
    ] + [Path(__file__).resolve()]

    def fingerprints():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources}

    report = {"verdict": "FAIL", "started_at": datetime.now(timezone.utc).isoformat(),
              "checks": [], "production_mutated": False, "gpu_speed": "NOT_ASSESSED"}
    before = fingerprints()
    args.runtime_root.mkdir(parents=True, exist_ok=True)
    old_tmp = tempfile.tempdir
    tempfile.tempdir = str(out)
    httpd, thread, app = None, None, None

    def check(name, condition):
        report["checks"].append({"name": name, "pass": bool(condition)})
        if not condition:
            raise AssertionError(name)

    try:
        with tempfile.TemporaryDirectory(prefix="browser-db-", dir=args.runtime_root) as local:
            root = Path(local)
            auth = AuthController(root / "accounts", allow_registration=True)
            password = uuid.uuid4().hex
            auth.register("browser-user", password)
            app = ProjectGateway(auth, ProjectStore(root / "projects"), root / "runtime",
                                 secure_cookies=False)
            httpd = build_server(app)
            thread = threading.Thread(target=httpd.serve_forever,
                                      kwargs={"poll_interval": .01}, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{httpd.server_address[1]}"
            Browser = load_browser()
            for width, height in ((1440, 900), (390, 844)):
                with Browser(args.browser, out / str(width), width=width, height=height,
                             source_root=ROOT) as browser:
                    browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                        "const realFetch=window.fetch.bind(window);"
                        "window.fetch=async function(input,options){"
                        "const r=await realFetch(input,options);"
                        "if(window.__loseCommand&&String(input).includes('/api/command')){"
                        "const accepted=await r.clone().json();"
                        "if(!r.ok||accepted.ok!==true){"
                        "throw Error('Loss fixture edit was rejected: '+r.status+' '+accepted.error);}"
                        "window.__loseCommand=false;throw Error('Injected lost response');}return r;};"})
                    browser.call("Page.navigate", {"url": origin + "/web/3d.html"})
                    browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
                    browser.click("input[name=username]")
                    browser.call("Input.insertText", {"text": "browser-user"})
                    browser.click("input[name=password]")
                    browser.call("Input.insertText", {"text": password})
                    browser.click("dialog button[type=submit]")
                    browser.wait("!!document.querySelector('input[aria-label=\"新项目名称\"]')")
                    browser.click("input[aria-label=\"新项目名称\"]")
                    browser.call("Input.insertText", {"text": "Browser " + str(width)})
                    browser.click("dialog button:nth-of-type(2)")
                    browser.wait("!document.querySelector('dialog')")
                    state = browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>"
                        "({ok:s.ok,revision:s.revision,entities:s.entities,locale:s.locale,"
                        "sketch_uuid:s.sketch_uuid,project_id:s.project_id}))")
                    check(str(width) + "-login-and-create", state["ok"] and state["revision"] == 1)
                    cookies = browser.call("Network.getCookies", {"urls": [origin]})["cookies"]
                    cookie = next(item for item in cookies if item["name"] == "opencad_session")
                    check(str(width) + "-httponly", cookie["httpOnly"] and cookie["sameSite"] == "Strict")
                    browser.wait("!!document.querySelector('#locale option[value=\"zh-CN\"]')"
                                 " && !document.querySelector('#locale').disabled")
                    browser.evaluate("document.querySelector('#locale').value='zh-CN';"
                                     "document.querySelector('#locale').dispatchEvent(new Event('change'))")
                    browser.wait("fetch('/api/state').then(r=>r.json()).then(s=>s.locale==='zh-CN')")
                    points = [[0, 0], [40000000, 0], [40000000, 20000000], [0, 20000000]]
                    bodies = [{"command": "add_line", "args": {
                        "p1": point, "p2": points[(i + 1) % 4]}} for i, point in enumerate(points)]
                    bodies.append({"command": "add_circle", "args": {
                        "center": [20000000, 10000000], "radius_nm": 3000000}})
                    result = browser.evaluate(
                        "(async function(){for(const b of " + json.dumps(bodies) + "){"
                        "const r=await fetch('/api/command',{method:'POST',headers:"
                        "{'Content-Type':'application/json'},body:JSON.stringify(b)});"
                        "if(!r.ok)return false;}await window.opencad3d.refresh();return true;})()")
                    check(str(width) + "-versioned-sketch", result)
                    browser.click("#apply")
                    browser.wait("fetch('/api/mesh').then(r=>r.json()).then(m=>m.solids.length===1)",
                                 timeout=30)
                    check(str(width) + "-real-extrusion", browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>s.revision===7)"))
                    browser.wait("document.querySelector('#metrics').textContent.length>0")
                    browser.wait("!window.opencad3d.busy")
                    browser.screenshot("private-3d")
                    browser.click("#undo")
                    browser.wait("fetch('/api/state').then(r=>r.json()).then(s=>s.revision===8)")
                    browser.wait("!window.opencad3d.busy")
                    browser.click("#redo")
                    browser.wait("fetch('/api/state').then(r=>r.json()).then(s=>s.revision===9)")
                    browser.wait("!window.opencad3d.busy")
                    check(str(width) + "-undo-redo", browser.evaluate(
                        "fetch('/api/mesh').then(r=>r.json()).then(m=>m.solids.length===1)"))
                    browser.call("Page.reload", {})
                    browser.wait("!!document.querySelector('dialog[open] select')")
                    browser.click("dialog button")
                    browser.wait("!document.querySelector('dialog')")
                    check(str(width) + "-reopen-history", browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>s.revision===9&&s.undo_depth===6)"))
                    check(str(width) + "-no-credential-storage", browser.evaluate(
                        "Object.keys(localStorage).length===0 && "
                        "Object.keys(sessionStorage).every(k=>k==='opencad-project-id')"))
                    check(str(width) + "-no-script-errors", not browser.evaluate(
                        "document.querySelector('#status').textContent.includes('Error:')"))
                    browser.wait("!window.opencad3d.busy && window.opencad3d.triangles>0")
                    browser.click("#depth")
                    browser.wait("document.activeElement===document.querySelector('#depth')")
                    browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a",
                                                           "code": "KeyA", "modifiers": 2,
                                                           "windowsVirtualKeyCode": 65})
                    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a",
                                                           "code": "KeyA", "modifiers": 2,
                                                           "windowsVirtualKeyCode": 65})
                    browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                                           "code": "Backspace",
                                                           "windowsVirtualKeyCode": 8})
                    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                                           "code": "Backspace",
                                                           "windowsVirtualKeyCode": 8})
                    browser.call("Input.insertText", {"text": "17"})
                    check(str(width) + "-draft-input-fixture", browser.evaluate(
                        "document.querySelector('#depth').value==='17' && "
                        "document.querySelector('#depth').dataset.dirty==='true'"))
                    browser.click("#feature-parameters")
                    browser.call("Input.insertText", {"text": '{"draft":17}'})
                    browser.evaluate("window.__recoveryDocument=document;"
                                     "window.__recoveryDepth=document.querySelector('#depth');"
                                     "window.__recoveryDraft=document.querySelector('#feature-parameters');"
                                     "window.__recoveryDraftValue=window.__recoveryDraft.value;")
                    browser.evaluate("window.__loseCommand=true")
                    outcome = browser.evaluate(
                        "fetch('/api/command',{method:'POST',headers:{'Content-Type':"
                        "'application/json'},body:JSON.stringify({command:'undo',"
                        "args:{}})}).then(()=>({rejected:false}),e=>"
                        "({rejected:true,message:e.message}))")
                    check(str(width) + "-lost-response-detected",
                          outcome["rejected"] and outcome["message"] == "Injected lost response")
                    check(str(width) + "-lost-response-actually-committed", browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>"
                        "s.revision===10&&s.undo_depth===5&&s.redo_depth===1)"))
                    check(str(width) + "-unconfirmed-edit-blocks-next", browser.evaluate(
                        "fetch('/api/command',{method:'POST',body:JSON.stringify({"
                        "command:'add_line',args:{p1:[0,0],p2:[2000000,2000000]}})})."
                        "then(()=>false,()=>true)"))
                    browser.click(".project-status button")
                    browser.wait("document.querySelector('.project-status output').textContent"
                                 ".includes('最新视图已核对')")
                    check(str(width) + "-recovery-preserves-page-and-unsaved-inputs", browser.evaluate(
                        "document===window.__recoveryDocument && "
                        "document.querySelector('#depth')===window.__recoveryDepth && "
                        "window.__recoveryDepth.value==='17' && "
                        "document.querySelector('#feature-parameters')===window.__recoveryDraft && "
                        "window.__recoveryDraft.value===window.__recoveryDraftValue"))
                    check(str(width) + "-receipt-replay-exactly-one-edit", browser.evaluate(
                        "fetch('/api/state').then(r=>r.json()).then(s=>"
                        "s.revision===10&&s.entities===5&&s.undo_depth===5&&s.redo_depth===1)"))
                    browser.wait("!window.opencad3d.busy")
                    browser.click("#redo")
                    browser.wait("fetch('/api/state').then(r=>r.json()).then(s=>s.revision===11)")
                    check(str(width) + "-recovery-preserves-real-model", browser.evaluate(
                        "fetch('/api/mesh').then(r=>r.json()).then(m=>m.solids.length===1)"))
            report["verdict"] = "PASS"
    except Exception:
        report["cause"] = traceback.format_exc()
        if "browser" in locals() and browser.ws is not None:
            try:
                report["browser_failure_state"] = browser.evaluate(
                    "({notice:document.querySelector('.project-status output')?.textContent,"
                    "buttons:Array.from(document.querySelectorAll('.project-status button')).map("
                    "b=>({text:b.textContent,hidden:b.hidden,disabled:b.disabled})),"
                    "dialog:!!document.querySelector('dialog[open]'),status:"
                    "document.querySelector('#status')?.textContent})")
            except Exception:
                report["browser_failure_state"] = "not_captured_after_browser_closed"
        write(out / "failure.log", report["cause"].encode())
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        if app:
            app.close()
        tempfile.tempdir = old_tmp
        after = fingerprints()
        report.update(source_sha256=before, source_drift=[
            name for name in before if before[name] != after[name]],
            finished_at=datetime.now(timezone.utc).isoformat())
        if report["source_drift"]:
            report["verdict"] = "FAIL"
        line = (f"OPENCAD_PRIVATE_BROWSER verdict={report['verdict']} "
                f"checks={len(report['checks'])} collected_at={report['finished_at']}")
        write(out / "verdict.log", (line + "\n").encode())
        write(out / "result.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
        print(line)
        print("evidence=" + str(out))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
