#!/usr/bin/env python3
"""Real isolated browser test of the advanced 3D forms, not production state."""

import argparse
import base64
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
sys.path.insert(0, str(APP))
import server
import web3d_browser_check as browser_check


class BrowserSession:
    def __init__(self, executable, out):
        self.executable, self.out = executable, out
        self.cache = None
        self.process = None
        self.socket = None
        self.sequence = {"id": 0}

    def __enter__(self):
        spec = importlib.util.spec_from_file_location("advanced_cdp", APP / "tests/legacy_browser_compat_gate.py")
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.cache = tempfile.TemporaryDirectory(prefix="opencad-advanced-", dir="/tmp")
        self.log = (self.out / "browser.log").open("w")
        try:
            self.process = subprocess.Popen([
                self.executable, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--remote-debugging-port=0",
                "--user-data-dir=" + self.cache.name, "--no-first-run", "about:blank"],
                env=dict(os.environ, TMPDIR=self.cache.name), stdout=self.log,
                stderr=subprocess.STDOUT, start_new_session=True)
            active = Path(self.cache.name) / "DevToolsActivePort"
            deadline = time.monotonic() + 20
            while not active.exists():
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("browser startup failed; see browser.log")
                time.sleep(0.1)
            debug = "http://127.0.0.1:" + active.read_text().splitlines()[0]
            with urllib.request.urlopen(debug + "/json/list", timeout=10) as response:
                tabs = json.load(response)
            self.socket = self.helper._WSClient(next(tab for tab in tabs if tab["type"] == "page")["webSocketDebuggerUrl"])
            self.call("Runtime.enable", {})
            self.call("Emulation.setDeviceMetricsOverride",
                      {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def call(self, method, params):
        value = self.helper._cdp_call(self.socket, self.sequence, method, params, 10)
        if "error" in value or "exceptionDetails" in value.get("result", {}):
            raise RuntimeError(f"browser operation failed: {value}")
        return value["result"]

    def evaluate(self, expression):
        return self.call("Runtime.evaluate", {"expression": expression,
                         "returnByValue": True, "awaitPromise": True})["result"].get("value")

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})

    def wait(self, expression):
        deadline = time.monotonic() + 30
        while not self.evaluate(expression):
            error = self.evaluate("window.opencad3d && window.opencad3d.error")
            if error:
                raise RuntimeError("3D page failed: " + error)
            if time.monotonic() >= deadline:
                raise RuntimeError("browser condition unmet: " + expression)
            time.sleep(0.1)

    def screenshot(self, path):
        data = self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(data["data"]))
        if path.stat().st_size < 10000:
            raise RuntimeError("screenshot lacks rendered content")

    def __exit__(self, *unused):
        if self.socket is not None:
            self.socket.sock.close()
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.cache is not None:
            self.cache.cleanup()
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome")
    parser.add_argument("--out", default="/hf3fs-jd/data/tmp/opencad-features-20261010/advanced-browser")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = Path(args.out) / stamp
    out.mkdir(parents=True, exist_ok=False)
    result = {"verdict": "FAIL", "time": stamp, "tests": 0}
    http = server.build_server(None, host="127.0.0.1", port=0, locale="zh-CN",
                               standard="gbt", out_root=str(out))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(http.server_port)
    try:
        browser_check.request(base, "/api/command", {"command": "add_circle",
                               "args": {"center": [0, 0], "radius_nm": 2_000_000}})
        with BrowserSession(args.browser, out) as session:
            def evaluate(expression):
                return session.evaluate(expression)

            session.navigate(base + "/web/3d.html")
            session.wait("window.opencadAdvanced && document.getElementById('profile').options.length > 0")
            evaluate("document.querySelector('[data-solid=sweep_solid]').requestSubmit()")
            session.wait("window.opencad3d.triangles > 0 && window.opencad3d.volume_mm3 > 250")
            actual = browser_check.request(base, "/api/mesh")
            volume = actual["solids"][0]["properties"]["volume_nm3"] / 1e18
            if abs(volume - math.pi * 4 * 20) > 1e-5:
                raise RuntimeError(f"form did not build actual sweep: {volume}")
            result["tests"] += 1
            session.wait("document.querySelectorAll('#feature-tree li').length === 1")
            result["tests"] += 1
            before = browser_check.request(base, "/api/state")["doc"]
            evaluate("document.querySelector('#feature-tree button').click()")
            session.wait("window.opencad3d.triangles === 0")
            if browser_check.request(base, "/api/state")["doc"] == before:
                raise RuntimeError("suppression form did not edit feature")
            if evaluate("window.opencad3d.volume_mm3 !== null || window.opencad3d.bbox_nm !== null || "
                        "document.getElementById('metrics').textContent !== ''"):
                raise RuntimeError("empty_model_stale_metrics")
            result["tests"] += 1
            evaluate("document.querySelector('#feature-tree button').click()")
            session.wait("window.opencad3d.triangles > 0")
            result["tests"] += 1
            # Interleave a real server edit between state and mesh fetches.
            feature = json.loads(browser_check.request(base, "/api/state")["doc"])["features"][0]
            edited_params = dict(feature["params"])
            edited_params["path_points_nm"] = [[0, 0, 0], [0, 0, 30_000_000]]
            edit_body = {"command": "edit_feature_params",
                         "args": {"feature_uuid": feature["uuid"], "params": edited_params}}
            before_ui = evaluate("({sha:window.opencad3d.document_sha256,"
                                 "volume:window.opencad3d.volume_mm3,"
                                 "metrics:document.getElementById('metrics').textContent,"
                                 "tree:document.getElementById('feature-tree').textContent})")
            probe = evaluate("""(async function(){
              var original=window.fetch, edited=false;
              window.fetch=async function(path, options){
                if(path==='/api/mesh' && !edited){
                  edited=true;
                  var change=await original('/api/command',{method:'POST',
                    headers:{'Content-Type':'application/json'},body:JSON.stringify(""" +
                             json.dumps(edit_body) + """)});
                  var body=await change.json();if(!body.ok)throw new Error(JSON.stringify(body));
                }
                return original(path,options);
              };
              try{await window.opencad3d.refresh();return {rejected:false,edited:edited};}
              catch(error){return {rejected:true,edited:edited,error:String(error)};}
              finally{window.fetch=original;}
            })()""")
            if not probe["edited"] or not probe["rejected"] or "document_snapshot_conflict" not in probe.get("error", ""):
                raise RuntimeError("state_mesh_version_mismatch_accepted: " + str(probe))
            after_ui = evaluate("({sha:window.opencad3d.document_sha256,"
                                "volume:window.opencad3d.volume_mm3,"
                                "metrics:document.getElementById('metrics').textContent,"
                                "tree:document.getElementById('feature-tree').textContent})")
            if after_ui != before_ui:
                raise RuntimeError("mismatched_snapshot_changed_accepted_geometry")
            result["tests"] += 1
            evaluate("window.opencad3d.refresh()")
            if abs(evaluate("window.opencad3d.volume_mm3") - math.pi * 4 * 30) > 1e-5:
                raise RuntimeError("matched_snapshot_recovery_did_not_show_real_new_geometry")
            result["tests"] += 1
            newest_params = dict(edited_params)
            newest_params["path_points_nm"] = [[0, 0, 0], [0, 0, 40_000_000]]
            newest_body = {"command": "edit_feature_params",
                           "args": {"feature_uuid": feature["uuid"], "params": newest_params}}
            late = evaluate("""(async function(){
              var original=window.fetch, release, started, intercepted=false;
              var captured=new Promise(function(resolve){started=resolve;});
              window.__snapshotEvents=[];
              var listener=function(event){window.__snapshotEvents.push(event.detail.doc);};
              window.addEventListener('opencad:state',listener);
              window.fetch=async function(path,options){
                var response=await original(path,options);
                if(path==='/api/mesh' && !intercepted){
                  intercepted=true;
                  await new Promise(function(resolve){release=resolve;started();});
                }
                return response;
              };
              try{
                document.getElementById('feature-edit-id').value=""" + json.dumps(feature["uuid"]) + """;
                document.getElementById('feature-parameters').value=""" +
                                 json.dumps(json.dumps(edit_body["args"]["params"])) + """;
                document.querySelector('[data-solid=edit_feature_params]').requestSubmit();
                await captured;
                var changed=await original('/api/command',{method:'POST',
                  headers:{'Content-Type':'application/json'},body:JSON.stringify(""" +
                                 json.dumps(newest_body) + """)});
                var payload=await changed.json();if(!payload.ok)throw new Error(JSON.stringify(payload));
                await window.opencad3d.refresh();
                var events=window.__snapshotEvents.length;
                release();
                while(window.opencad3d.busy){await new Promise(function(resolve){setTimeout(resolve,10);});}
                await new Promise(function(resolve){setTimeout(resolve,0);});
                var current=await (await original('/api/state')).json();
                return {events:events,finalEvents:window.__snapshotEvents.length,
                  eventDoc:window.__snapshotEvents[0],doc:current.doc,busy:window.opencad3d.busy,
                  bbox:window.opencad3d.bbox_nm,volume:window.opencad3d.volume_mm3,
                  status:document.getElementById('status').textContent,
                  ready:current.strings['status.ready'],error:window.opencad3d.error};
              }finally{window.fetch=original;window.removeEventListener('opencad:state',listener);}
            })()""")
            if (late["events"] != 1 or late["status"] != late["ready"]
                    or late["finalEvents"] != 1 or late["error"] or late["busy"]
                    or late["eventDoc"] != late["doc"]
                    or abs(late["volume"] - math.pi * 4 * 40) > 1e-5
                    or late["bbox"][5] != 40_000_000):
                raise RuntimeError("late_refresh_overwrote_latest_snapshot: " + str(late))
            result["tests"] += 1
            # Real server rejection must still reach the advanced form's error UI.
            before_rejection = browser_check.request(base, "/api/state")["doc"]
            rejected_params = dict(newest_params)
            rejected_params["path_points_nm"] = [[0, 0, 0], [0, 0, 0]]
            evaluate("document.getElementById('feature-parameters').value=" +
                     json.dumps(json.dumps(rejected_params)) + ";"
                     "document.querySelector('[data-solid=edit_feature_params]').requestSubmit()")
            rejection = evaluate("""(async function(){
              while(window.opencad3d.busy){await new Promise(function(resolve){setTimeout(resolve,10);});}
              await new Promise(function(resolve){setTimeout(resolve,0);});
              return {status:document.getElementById('status').textContent,
                error:window.opencad3d.error,volume:window.opencad3d.volume_mm3,
                bbox:window.opencad3d.bbox_nm,busy:window.opencad3d.busy};
            })()""")
            after_rejection = browser_check.request(base, "/api/state")["doc"]
            if (after_rejection != before_rejection or rejection["busy"]
                    or "zero-length segment" not in rejection["status"]
                    or rejection["status"] != rejection["error"]
                    or abs(rejection["volume"] - math.pi * 4 * 40) > 1e-5
                    or rejection["bbox"][5] != 40_000_000):
                raise RuntimeError("real_form_error_hidden_or_geometry_mutated: " + str(rejection))
            result["tests"] += 1
            evaluate("window.opencad3d.refresh()")
            session.screenshot(out / "advanced.png")
        result["verdict"] = "PASS"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"OPENCAD_ADVANCED_BROWSER verdict={result['verdict']} tests={result['tests']} evidence={out}")
    if "error" in result:
        print(result["error"])
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
