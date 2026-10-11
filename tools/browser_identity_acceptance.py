#!/usr/bin/env python3
"""Real local CLI/SQLite/Chromium identity isolation; no production or GPU."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
MARKER = "OPENCAD_BROWSER_IDENTITY"
WORKFLOWS = {
    "loaded_session_source", "alice_geometry", "durable_receipt",
    "stale_cas_no_receipt", "concurrent_cas_single_winner",
    "logout_old_cookie_rejected", "logout_private_storage_cleared",
    "bob_project_document_history_drafts_isolated", "bob_geometry_download",
    "alice_locale_document_restored", "lost_response_receipt_drafts_preserved",
    "offline_read_shields_workspace", "offline_recovery_same_page",
    "offline_download_no_file", "revocation_shields_workspace",
    "wrong_identity_recovery_rejected", "same_identity_recovery",
    "late_response_positive_control", "late_response_rejected",
    "late_mesh_cannot_restore_geometry", "direct_identity_switch_reload",
    "bob_persistent_locale_and_model",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, cause):
    if not condition:
        raise AssertionError(cause)


def write(path, value):
    data = value if isinstance(value, bytes) else (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source(root):
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {
            ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"})
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "linked_source_directory")
        for name in sorted(names):
            if name == ".git":
                continue
            path = Path(directory) / name
            require(path.is_file() and not path.is_symlink(), "nonregular_source")
            data = path.read_bytes()
            files[path.relative_to(root).as_posix()] = {
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                "mode": path.stat().st_mode & 0o777}
    require("oss-alternatives/apps/opencad/web/js/project-session.js" in files,
            "source_incomplete")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, check=True).stdout.decode().strip()
    status = subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                             "status", "--porcelain=v1"], capture_output=True,
                            check=True).stdout.decode().splitlines()
    return {"root": str(root), "head": head, "files": files,
            "sha256": digest(files), "status": status}


def validate_bound_report(report, root, out):
    require(report["verdict"] == "PASS" and report["original_rc"] == 0,
            "producer_not_pass")
    require(report["first_cause"] is None and not report["cleanup_errors"],
            "producer_failure_present")
    require(report["source_before"] == report["source_after"]
            and report["source_drift"] == [], "producer_source_drift")
    require(source(root) == report["source_after"], "current_source_drift")
    checks = report["checks"]
    require(checks and len({item["name"] for item in checks}) == len(checks)
            and all(item["pass"] is True for item in checks), "invalid_check_ledger")
    require({item["name"] for item in checks} == {
        f"{width}-{name}" for width in (1440, 390) for name in WORKFLOWS},
        "missing_workflow_checks")
    require(report["cli"]["ready"] is True and report["cli"]["exit_rc"] == 0
            and report["cli"]["observed_argv"] == report["cli"]["argv"]
            and report["cli"]["startticks"] > 0, "invalid_original_cli")
    require(report["artifacts"], "empty_artifacts")
    required_artifacts = {"cli.log", "checks.log"}
    for width in (1440, 390):
        required_artifacts.update(f"{width}/{name}" for name in (
            "alice-3d.png", "bob-3d.png", "offline-shield.png", "revoked-shield.png",
            "restored-bob.png", "cdp-events.json", "browser.log",
            "downloads/opencad.step", "concurrent-cas.json", "version-witness.json",
            "download-response-witness.json", "independent.step"))
    require(required_artifacts <= set(report["artifacts"]), "missing_artifacts")
    actual_artifacts = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
                        and p.name not in {"completion.log", "result.json"}}
    require(actual_artifacts == set(report["artifacts"]), "artifact_manifest_mismatch")
    for name, record in report["artifacts"].items():
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts, "artifact_escape")
        require(not (out / path).is_symlink(), "artifact_symlink")
        data = (out / path).read_bytes()
        require(hashlib.sha256(data).hexdigest() == record["sha256"]
                and len(data) == record["bytes"], "artifact_hash_mismatch")
    cli = report["cli"]
    require(cli["ready_marker"] == f"OPENCAD_PRIVATE_WEB verdict=READY port={cli['port']}"
            and cli["ready_marker"].encode() in (out / "cli.log").read_bytes(),
            "cli_marker_missing")
    argv = [sys.executable, "-B", str(root / "oss-alternatives/apps/opencad/secure_web.py"),
            "--data-root", report["runtime_root"], "--host", "127.0.0.1",
            "--port", str(cli["port"]), "--public-origin", cli["origin"]]
    require(cli["argv"] == argv and cli["origin"] == f"http://127.0.0.1:{cli['port']}",
            "cli_scope_mismatch")
    require(cli["startticks"] == cli["stop_startticks"], "cli_identity_changed")
    logged = [json.loads(line) for line in (out / "checks.log").read_text().splitlines()]
    require(logged == checks, "check_log_mismatch")
    for width in (1440, 390):
        for name in ("alice-3d", "bob-3d", "offline-shield", "revoked-shield", "restored-bob"):
            data = (out / str(width) / (name + ".png")).read_bytes()
            require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 1000,
                    "invalid_screenshot")
        witness = json.loads((out / str(width) / "download-response-witness.json").read_text())
        downloaded = (out / str(width) / "downloads/opencad.step").read_bytes()
        independent = (out / str(width) / "independent.step").read_bytes()
        require(hashlib.sha256(downloaded).hexdigest() == witness["sha256"]
                and len(downloaded) == witness["bytes"]
                and b"\nDATA;\n" in downloaded
                and downloaded.partition(b"\nDATA;\n")[2] == independent.partition(b"\nDATA;\n")[2],
                "download_witness_mismatch")
    return len(checks)


def validate_evidence(report, root, out):
    count = validate_bound_report(report, root, out)
    require((out / "completion.log").read_text() ==
            f"{MARKER} verdict=PASS checks={count} rc=0\n", "completion_marker_mismatch")
    return count


class Review:
    def __init__(self, root, out, runtime, browser):
        self.root, self.out, self.runtime = root, out, runtime
        self.app = root / "oss-alternatives/apps/opencad"
        self.browser_binary = browser
        self.process = None
        self.cli_log = None
        self.checks, self.secrets, self.cleanup_errors = [], [], []
        self.cli = {}
        self.check_log = (out / "checks.log").open("x", encoding="utf-8")

    def check(self, name, condition, **facts):
        self.checks.append({"name": name, "pass": bool(condition), "at": now(), **facts})
        self.check_log.write(json.dumps(self.checks[-1], sort_keys=True) + "\n")
        self.check_log.flush()
        os.fsync(self.check_log.fileno())
        print(f"IDENTITY_CHECK name={name} pass={bool(condition)}", flush=True)
        require(condition, name)

    def start(self):
        self.accounts = {}
        with closing(self.Auth(self.runtime / "accounts", allow_registration=True)) as auth:
            for name in ("alice", "bob"):
                password = uuid.uuid4().hex
                record = auth.register(name, password)
                self.accounts[name] = {**record, "password": password}
                self.secrets.append(password.encode())
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        argv = [sys.executable, "-B", str(self.app / "secure_web.py"),
                "--data-root", str(self.runtime), "--host", "127.0.0.1",
                "--port", str(port), "--public-origin", self.origin]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   OPENCAD_TEST_RUNTIME_ROOT=str(self.runtime))
        for key in ("PYTHONPATH", "OPENCAD_CADCORE_ROOT", "OPENCAD_I18N_DIRS",
                    "CADCORE_PDF_BACKEND", "CADCORE_SUBPROC_EXECUTABLE"):
            env.pop(key, None)
        self.cli_log = (self.out / "cli.log").open("xb")
        self.process = subprocess.Popen(argv, cwd=self.root, env=env,
                                        stdout=subprocess.PIPE, stderr=self.cli_log)
        readable, _, _ = select.select([self.process.stdout], [], [], 20)
        require(readable, "cli_readiness_timeout")
        line = self.process.stdout.readline()
        require(line == f"OPENCAD_PRIVATE_WEB verdict=READY port={port}\n".encode(),
                "cli_readiness_marker")
        proc = Path("/proc") / str(self.process.pid)
        observed = proc.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        fields = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()
        self.cli = {"argv": argv, "observed_argv": [p.decode() for p in observed],
                    "pid": self.process.pid, "startticks": int(fields[19]),
                    "ready": True, "ready_marker": line.decode().strip(),
                    "ready_at": now(), "exit_rc": None, "port": port, "origin": self.origin}
        self.cli_log.write(line)
        require(self.cli["observed_argv"] == argv, "cli_argv_mismatch_after_ready")

    def stop(self):
        if self.process is not None:
            proc = Path("/proc") / str(self.process.pid)
            fields = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()
            self.cli["stop_startticks"] = int(fields[19])
            self.process.terminate()
            try:
                code = self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=10)
                self.cleanup_errors.append("cli_required_sigkill")
            self.cli["exit_rc"] = code
            self.cli["finished_at"] = now()
            self.cli_log.write(self.process.stdout.read())
            self.process.stdout.close()
            self.cli_log.flush()
            os.fsync(self.cli_log.fileno())
            self.cli_log.close()
            self.process = None
            require(code == 0, "cli_original_exit_nonzero")

    def http(self, method, path, cookie, project=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1",
                                                int(self.origin.rsplit(":", 1)[1]), timeout=30)
        headers = {"Cookie": cookie, "Origin": self.origin, "X-OpenCAD-Request": "1"}
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), (
                json.loads(raw) if response.getheader("Content-Type", "").startswith(
                    "application/json") else raw)
        finally:
            connection.close()

    def browser_class(self):
        module = load("identity_controls", self.root /
                      "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        review = self

        class Browser(module.BrowserSession):
            def __enter__(self):
                self.out.mkdir(parents=True, exist_ok=True)
                self.profile = tempfile.TemporaryDirectory(
                    prefix="b-", dir=review.runtime / "profiles")
                directory = Path(self.profile.name)
                self.log = (self.out / "browser.log").open("x", encoding="utf-8")
                try:
                    self.proc = subprocess.Popen([
                        self.browser, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                        "--remote-debugging-port=0", "--user-data-dir=" + str(directory),
                        "--no-first-run", "--no-default-browser-check",
                        "--disable-background-networking", "--disable-component-update",
                        "--disable-sync", "--disable-breakpad", "--disable-crash-reporter",
                        "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank",
                    ], env=dict(os.environ, HOME=str(directory), TMPDIR=str(directory),
                                XDG_CONFIG_HOME=str(directory / "config"),
                                XDG_CACHE_HOME=str(directory / "cache")),
                        stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
                    active = directory / "DevToolsActivePort"
                    deadline = time.monotonic() + 30
                    while not active.is_file():
                        require(self.proc.poll() is None and time.monotonic() < deadline,
                                "owned_browser_startup_failed")
                        time.sleep(.1)
                    with urllib.request.urlopen("http://127.0.0.1:" +
                            active.read_text().splitlines()[0] + "/json/list", timeout=10) as response:
                        tabs = json.load(response)
                    self.helper = load("identity_cdp", self.app / "tests/legacy_browser_compat_gate.py")
                    self.ws = self.helper._WSClient(next(
                        tab for tab in tabs if tab["type"] == "page")["webSocketDebuggerUrl"])
                    self.call("Runtime.enable", {})
                    self.call("Page.enable", {})
                    self.call("Emulation.setDeviceMetricsOverride", {
                        "width": self.width, "height": self.height,
                        "deviceScaleFactor": 1, "mobile": self.width < 650})
                    return self
                except BaseException:
                    self.__exit__(*sys.exc_info())
                    raise

            def call(self, method, params):
                response = self.helper._cdp_call(self.ws, self.sequence, method, params, 30)
                self.events.append({"method": method, "ok": "error" not in response})
                if "error" in response or "exceptionDetails" in response.get("result", {}):
                    raise RuntimeError("browser_operation_failed:" + method)
                return response["result"]

            def __exit__(self, kind, exc, tb):
                if kind is not None and self.ws is not None:
                    try:
                        self.screenshot("first-failure")
                        write(self.out / "failure-dom.json", self.evaluate(
                            "({lang:document.documentElement.lang,"
                            "locked:document.body.dataset.opencadSessionLocked||null,"
                            "notice:document.querySelector('.project-status output')?.textContent,"
                            "triangles:window.opencad3d?.triangles})"))
                    except Exception as error:
                        review.cleanup_errors.append("failure_capture:" + type(error).__name__)
                errors = []
                if self.ws is not None:
                    try:
                        self.ws.sock.close()
                        self.ws = None
                    except Exception as error:
                        errors.append("cdp_close:" + type(error).__name__)
                if self.proc is not None:
                    try:
                        os.killpg(self.proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                        self.proc.wait(timeout=5)
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.proc = None
                if self.log is not None:
                    self.log.close()
                    self.log = None
                write(self.out / "cdp-events.json", self.events)
                if self.profile is not None:
                    try:
                        self.profile.cleanup()
                    except Exception as error:
                        errors.append("profile_cleanup:" + type(error).__name__)
                review.cleanup_errors.extend(errors)
                if kind is None:
                    require(not errors, "owned_browser_cleanup_failed")

        return Browser

    @staticmethod
    def type(browser, selector, value):
        browser.click(selector)
        browser.key("a", modifiers=2)
        browser.call("Input.insertText", {"text": value})

    def login(self, browser, name):
        browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
        self.type(browser, "dialog[open] input[name=username]", name)
        self.type(browser, "dialog[open] input[name=password]", self.accounts[name]["password"])
        browser.click("dialog[open] button[type=submit]")
        browser.wait("!!document.querySelector('dialog[open] select')")

    def create(self, browser, title):
        self.type(browser, 'input[aria-label="新项目名称"]', title)
        browser.click("dialog[open] button:nth-of-type(2)")
        browser.wait("!document.querySelector('dialog[open]')")
        browser.wait("window.opencad3d && !window.opencad3d.busy && "
                     "document.querySelector('#locale').options.length>0")

    def open_project(self, browser, identifier):
        values = browser.evaluate("Array.from(document.querySelector('dialog[open] select')"
                                  ".options).map(o=>o.value)")
        require(identifier in values, "owned_project_missing")
        browser.click("dialog[open] select")
        browser.key("Home")
        for _ in range(values.index(identifier)):
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowDown",
                         "code": "ArrowDown", "windowsVirtualKeyCode": 40})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowDown",
                         "code": "ArrowDown", "windowsVirtualKeyCode": 40})
        browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter",
                     "code": "Enter", "windowsVirtualKeyCode": 13})
        browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter",
                     "code": "Enter", "windowsVirtualKeyCode": 13})
        browser.click("dialog[open] button")
        browser.wait("!document.querySelector('dialog[open]')")

    @staticmethod
    def api(browser, path, body=None):
        options = "" if body is None else ",{method:'POST',headers:{'Content-Type':" + (
            "'application/json'},body:JSON.stringify(" + json.dumps(body) + ")}")
        return browser.evaluate("fetch(" + json.dumps(path) + options +
                                ").then(async r=>({status:r.status,body:await r.json()}))")

    def state(self, browser):
        result = self.api(browser, "/api/state")
        require(result["status"] == 200 and result["body"]["ok"] is True, "state_read_failed")
        return result["body"]

    def cookie(self, browser):
        cookies = browser.call("Network.getCookies", {"urls": [self.origin]})["cookies"]
        item = next(item for item in cookies if item["name"] == "opencad_session")
        self.secrets.append(item["value"].encode())
        require(item["httpOnly"] and item["sameSite"] == "Strict", "cookie_boundary")
        return "opencad_session=" + item["value"]

    def locale(self, browser, code):
        browser.click("#locale")
        browser.key("Home")
        codes = browser.evaluate("Array.from(document.querySelector('#locale').options).map(o=>o.value)")
        require(code in codes, "locale_option_missing")
        for _ in range(codes.index(code)):
            browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowDown",
                                                   "code": "ArrowDown", "windowsVirtualKeyCode": 40})
            browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowDown",
                                                   "code": "ArrowDown", "windowsVirtualKeyCode": 40})
        browser.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter",
                                               "code": "Enter", "windowsVirtualKeyCode": 13})
        browser.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter",
                                               "code": "Enter", "windowsVirtualKeyCode": 13})
        browser.wait("document.documentElement.lang===" + json.dumps(code) +
                     " && !window.opencad3d.busy")

    def geometry(self, browser, depth):
        points = [[0, 0], [40000000, 0], [40000000, 20000000], [0, 20000000]]
        for i, point in enumerate(points):
            result = self.api(browser, "/api/command", {"command": "add_line", "args": {
                "p1": point, "p2": points[(i + 1) % 4]}})
            require(result["status"] == 200, "sketch_fixture_rejected")
        result = self.api(browser, "/api/command", {"command": "add_circle", "args": {
            "center": [20000000, 10000000], "radius_nm": 3000000}})
        require(result["status"] == 200, "hole_fixture_rejected")
        browser.evaluate("window.opencad3d.refresh()")
        self.type(browser, "#depth", str(depth))
        browser.click("#apply")
        browser.wait("window.opencad3d.triangles>0 && !window.opencad3d.busy", timeout=40)
        return self.state(browser)

    def concurrent_cas(self, cookie, state, out, prefix):
        import threading
        barrier = threading.Barrier(2)
        bodies = [{"expected_revision": state["revision"], "expected_sha": state["sha256"],
                   "request_id": uuid.uuid4().hex} for _ in range(2)]

        def save(body):
            barrier.wait(timeout=10)
            code, _, response = self.http("POST", "/api/save", cookie, state["project_id"], body)
            return {"status": code, "response": response, "body": body}

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, bodies))
        with self.Store(self.runtime / "projects") as store:
            head = store.read_head(state["project_id"])
            receipts = [store.read_receipt(state["project_id"], body["request_id"],
                         request_payload={**body, "route": "/api/save"}) for body in bodies]
        self.check(prefix + "concurrent_cas_single_winner",
                   sorted(item["status"] for item in results) == [200, 409]
                   and sum(item is not None for item in receipts) == 1
                   and head["revision"] == state["revision"] + 1
                   and head["snapshot"]["doc"] == state["doc"])
        write(out / "concurrent-cas.json", {"results": results,
              "receipt_revisions": [r["revision"] if r else None for r in receipts],
              "head_revision": head["revision"]})

    def run_view(self, width, height):
        prefix = str(width) + "-"
        view_out = self.out / str(width)
        view_out.mkdir()
        downloads = view_out / "downloads"
        downloads.mkdir()
        Browser = self.browser_class()
        with Browser(self.browser_binary, view_out, width=width, height=height,
                     source_root=self.root) as browser:
            browser.call("Network.enable", {})
            browser.call("Browser.setDownloadBehavior", {"behavior": "allow",
                                                         "downloadPath": str(downloads)})
            # This wraps the actual network result, not auth/store or DOM.
            browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                "const originalFetch=window.fetch.bind(window);"
                "window.fetch=async function(input,options){"
                "const r=await originalFetch(input,options);"
                "if(window.__holdPath&&String(input).includes(window.__holdPath)){"
                "window.__holdPath=null;window.__held=true;"
                "await new Promise(resolve=>window.__release=resolve);}"
                "if(window.__loseSave&&String(input).includes('/api/save')){"
                "window.__loseSave=false;const v=await r.clone().json();"
                "if(!r.ok||!v.ok)throw Error('loss_fixture_rejected');"
                "window.__lostAccepted=v;throw Error('identity_lost_response');}"
                "if(String(input).includes('/api/step')&&r.ok){"
                "const bytes=await r.clone().arrayBuffer();"
                "const hash=await crypto.subtle.digest('SHA-256',bytes);"
                "window.__stepWitness={bytes:bytes.byteLength,"
                "sha256:Array.from(new Uint8Array(hash)).map(v=>v.toString(16)"
                ".padStart(2,'0')).join(''),revision:r.headers.get('X-OpenCAD-Revision'),"
                "document_sha256:r.headers.get('X-OpenCAD-Document-SHA256')};}"
                "return r;};"})
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            self.login(browser, "alice")
            self.create(browser, "Alice " + str(width))
            resources = browser.call("Page.getResourceTree", {})["frameTree"]["resources"]
            resource = next(r for r in resources if r["url"].endswith("/js/project-session.js"))
            loaded = browser.call("Page.getResourceContent", {
                "frameId": browser.call("Page.getFrameTree", {})["frameTree"]["frame"]["id"],
                "url": resource["url"]})
            data = base64.b64decode(loaded["content"]) if loaded["base64Encoded"] else (
                loaded["content"].encode())
            self.check(prefix + "loaded_session_source", hashlib.sha256(data).hexdigest() ==
                       hashlib.sha256((self.app / "web/js/project-session.js").read_bytes()).hexdigest())
            self.locale(browser, "zh-CN")
            alice = self.geometry(browser, 6)
            alice_cookie = self.cookie(browser)
            mesh = self.api(browser, "/api/mesh")["body"]
            self.check(prefix + "alice_geometry", mesh["document_sha256"] ==
                       hashlib.sha256(alice["doc"].encode()).hexdigest()
                       and browser.evaluate("window.opencad3d.pixelCount()>100")
                       and browser.evaluate("document.documentElement.lang==='zh-CN'"),
                       project_id=alice["project_id"], revision=alice["revision"],
                       document_sha256=mesh["document_sha256"])
            browser.screenshot("alice-3d")
            browser.evaluate("window.__lastReceipt=null;window.addEventListener("
                             "'opencad:project-commit',e=>window.__lastReceipt=e.detail)")
            save = self.api(browser, "/api/save", {})
            event = browser.evaluate("window.__lastReceipt")
            payload = {"expected_revision": event["base"]["expected_revision"],
                       "expected_sha": event["base"]["expected_sha"],
                       "request_id": event["base"]["request_id"], "route": "/api/save"}
            with self.Store(self.runtime / "projects") as store:
                receipt = store.read_receipt(alice["project_id"], payload["request_id"],
                                             request_payload=payload)
            self.check(prefix + "durable_receipt", save["status"] == 200 and receipt is not None
                       and receipt["revision"] == alice["revision"] + 1)
            saved = self.state(browser)
            stale = {**payload, "request_id": uuid.uuid4().hex}
            stale.pop("route")
            code, _, _ = self.http("POST", "/api/save", alice_cookie, alice["project_id"], stale)
            with self.Store(self.runtime / "projects") as store:
                absent = store.read_receipt(alice["project_id"], stale["request_id"],
                                            request_payload={**stale, "route": "/api/save"})
                head = store.read_head(alice["project_id"])
            self.check(prefix + "stale_cas_no_receipt", code == 409 and absent is None
                       and head["revision"] == saved["revision"])
            self.concurrent_cas(alice_cookie, saved, view_out, prefix)
            browser.evaluate("window.opencad3d.refresh()")
            browser.click("#undo")
            browser.wait("!window.opencad3d.busy")
            browser.click("#redo")
            browser.wait("!window.opencad3d.busy && window.opencad3d.triangles>0")
            alice = self.state(browser)
            self.type(browser, "#depth", "17")
            browser.evaluate("document.querySelector('#feature-parameters').closest('details').open=true")
            self.type(browser, "#feature-parameters", '{"alice_draft":17}')
            browser.evaluate("window.__identityCache={owner:'alice',doc:'private'}")
            browser.click(".project-status button:last-child")
            browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
            self.check(prefix + "logout_private_storage_cleared", browser.evaluate(
                "!sessionStorage.getItem('opencad-project-id') && "
                "Object.keys(localStorage).length===0 && !window.__identityCache"))
            code, _, _ = self.http("GET", "/api/state", alice_cookie, alice["project_id"])
            self.check(prefix + "logout_old_cookie_rejected", code == 401)
            self.login(browser, "bob")
            options = browser.evaluate("Array.from(document.querySelector('dialog select').options)"
                                       ".map(o=>o.textContent)")
            require(not any("Alice" in title for title in options), "foreign_project_in_picker")
            self.create(browser, "Bob " + str(width))
            self.locale(browser, "en")
            bob = self.state(browser)
            bob_cookie = self.cookie(browser)
            code, _, _ = self.http("GET", "/api/document", bob_cookie, alice["project_id"])
            self.check(prefix + "bob_project_document_history_drafts_isolated",
                       bob["project_id"] != alice["project_id"] and bob["doc"] != alice["doc"]
                       and bob["undo_depth"] == bob["redo_depth"] == 0 and code == 403
                       and browser.evaluate("!window.__identityCache && "
                                            "document.querySelector('#depth').value==='6' && "
                                            "document.querySelector('#feature-parameters').value==='{}'"))
            bob = self.geometry(browser, 9)
            browser.click("#step")
            deadline = time.monotonic() + 30
            while not (downloads / "opencad.step").is_file():
                require(time.monotonic() < deadline, "real_download_timeout")
                time.sleep(.1)
            code, headers, step = self.http("GET", "/api/step", bob_cookie, bob["project_id"])
            downloaded = (downloads / "opencad.step").read_bytes()
            witness = browser.evaluate("window.__stepWitness")
            write(view_out / "download-response-witness.json", witness)
            write(view_out / "independent.step", step)
            self.check(prefix + "bob_geometry_download", code == 200 and witness is not None
                       and hashlib.sha256(downloaded).hexdigest() == witness["sha256"]
                       and len(downloaded) == witness["bytes"]
                       and witness["revision"] == str(bob["revision"])
                       and witness["document_sha256"] == hashlib.sha256(bob["doc"].encode()).hexdigest()
                       and downloaded.partition(b"\nDATA;\n")[2] == step.partition(b"\nDATA;\n")[2]
                       and b"\nDATA;\n" in downloaded
                       and step.startswith(b"ISO-10303-21;") and
                       headers["X-OpenCAD-Document-SHA256"] == hashlib.sha256(
                           bob["doc"].encode()).hexdigest()
                       and browser.evaluate("window.opencad3d.volume_mm3>6900 && "
                                            "document.documentElement.lang==='en'"),
                       project_id=bob["project_id"], revision=bob["revision"])
            require(abs(browser.evaluate("window.opencad3d.volume_mm3") -
                        (800 - math.pi * 9) * 9) < 1e-5, "bob_analytic_volume_mismatch")
            browser.screenshot("bob-3d")
            browser.click(".project-status button:last-child")
            browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
            self.login(browser, "alice")
            self.open_project(browser, alice["project_id"])
            browser.wait("window.opencad3d.triangles>0 && !window.opencad3d.busy")
            restored = self.state(browser)
            self.check(prefix + "alice_locale_document_restored",
                       restored["doc"] == alice["doc"] and restored["revision"] == alice["revision"]
                       and restored["undo_depth"] == alice["undo_depth"]
                       and restored["redo_depth"] == alice["redo_depth"]
                       and browser.evaluate("document.documentElement.lang==='zh-CN' && "
                                            "document.querySelector('#depth').value==='6'"))
            self.type(browser, "#depth", "23")
            browser.evaluate("window.__savedDocument=document;window.__savedDepth="
                             "document.querySelector('#depth')")
            browser.evaluate("window.__loseSave=true")
            lost = browser.evaluate("fetch('/api/save',{method:'POST',headers:{"
                                    "'Content-Type':'application/json'},body:'{}'}).then("
                                    "()=>false,e=>e.message==='identity_lost_response')")
            accepted = browser.evaluate("window.__lostAccepted")
            require(lost and accepted is not None, "lost_response_fixture_not_committed")
            browser.click(".project-status button:first-of-type")
            browser.wait("document.querySelector('.project-status output').textContent"
                         ".includes('最新视图已核对')")
            recovered = self.state(browser)
            self.check(prefix + "lost_response_receipt_drafts_preserved",
                       lost and recovered["revision"] == accepted["revision"]
                       and recovered["doc"] == alice["doc"] and browser.evaluate(
                           "document===window.__savedDocument && "
                           "document.querySelector('#depth')===window.__savedDepth && "
                           "window.__savedDepth.value==='23'"))
            alice = recovered
            browser.call("Network.emulateNetworkConditions", {"offline": True, "latency": 0,
                                                               "downloadThroughput": 0,
                                                               "uploadThroughput": 0})
            offline = browser.evaluate("fetch('/api/state').then(()=>false,()=>true)")
            self.check(prefix + "offline_read_shields_workspace", offline and browser.evaluate(
                "document.body.dataset.opencadSessionLocked==='true' && "
                "document.querySelector('main').inert && "
                "getComputedStyle(document.querySelector('main')).visibility==='hidden'"))
            browser.screenshot("offline-shield")
            browser.call("Network.emulateNetworkConditions", {"offline": False, "latency": 0,
                                                               "downloadThroughput": -1,
                                                               "uploadThroughput": -1})
            browser.click(".project-status button:nth-of-type(2)")
            browser.wait("document.body.dataset.opencadSessionLocked!=='true'")
            self.check(prefix + "offline_recovery_same_page", browser.evaluate(
                "document===window.__savedDocument && document.querySelector('#depth')==="
                "window.__savedDepth && window.__savedDepth.value==='23'"))
            browser.call("Network.emulateNetworkConditions", {"offline": True, "latency": 0,
                         "downloadThroughput": 0, "uploadThroughput": 0})
            browser.click("#step")
            browser.wait("document.body.dataset.opencadSessionLocked==='true'")
            self.check(prefix + "offline_download_no_file",
                       list(downloads.iterdir()) == [downloads / "opencad.step"])
            browser.call("Network.emulateNetworkConditions", {"offline": False, "latency": 0,
                         "downloadThroughput": -1, "uploadThroughput": -1})
            browser.click(".project-status button:nth-of-type(2)")
            browser.wait("document.body.dataset.opencadSessionLocked!=='true'")
            with closing(self.Auth(self.runtime / "accounts", operator_mode=True)) as auth:
                auth.revoke_all_sessions(self.accounts["alice"]["user_id"])
            revoked = self.api(browser, "/api/state")
            self.check(prefix + "revocation_shields_workspace", revoked["status"] == 401 and
                       browser.evaluate("document.body.dataset.opencadSessionLocked==='true' && "
                                        "document.querySelector('main').inert"))
            browser.screenshot("revoked-shield")
            browser.click(".project-status button:nth-of-type(3)")
            browser.wait("!!document.querySelector('dialog[data-session-recovery]')")
            self.type(browser, "dialog[data-session-recovery] input[name=username]", "bob")
            self.type(browser, "dialog[data-session-recovery] input[name=password]",
                      self.accounts["bob"]["password"])
            browser.click("dialog[data-session-recovery] button[type=submit]")
            browser.wait("document.querySelector('dialog[data-session-recovery] [role=alert]')"
                         ".textContent.length>0")
            self.check(prefix + "wrong_identity_recovery_rejected", browser.evaluate(
                "document.body.dataset.opencadSessionLocked==='true' && "
                "document.querySelector('#depth').value==='23'"))
            self.type(browser, "dialog[data-session-recovery] input[name=username]", "alice")
            self.type(browser, "dialog[data-session-recovery] input[name=password]",
                      self.accounts["alice"]["password"])
            browser.click("dialog[data-session-recovery] button[type=submit]")
            browser.wait("!document.querySelector('dialog[data-session-recovery]')")
            browser.wait("document.body.dataset.opencadSessionLocked!=='true'")
            self.check(prefix + "same_identity_recovery", self.state(browser)["doc"] == alice["doc"]
                       and browser.evaluate("document===window.__savedDocument && "
                                            "document.querySelector('#depth').value==='23'"))
            browser.evaluate("window.__holdPath='/api/state';window.__held=false;"
                             "window.__lateResult=null;"
                             "fetch('/api/state').then(r=>r.json()).then("
                             "s=>window.__lateResult=s.project_id,"
                             "e=>window.__lateResult=e.message);true")
            browser.wait("window.__held===true")
            browser.evaluate("window.__release();true")
            browser.wait("window.__lateResult!==null")
            self.check(prefix + "late_response_positive_control",
                       browser.evaluate("window.__lateResult") == alice["project_id"])
            browser.evaluate("window.__holdPath='/api/state';window.__held=false;"
                             "window.__lateResult=null;"
                             "fetch('/api/state').then(r=>r.json()).then("
                             "s=>window.__lateResult='accepted',"
                             "e=>window.__lateResult=e.message);true")
            browser.wait("window.__held===true")
            with closing(self.Auth(self.runtime / "accounts", operator_mode=True)) as auth:
                auth.revoke_all_sessions(self.accounts["alice"]["user_id"])
            self.api(browser, "/api/state")
            browser.evaluate("window.__release();true")
            browser.wait("window.__lateResult!==null")
            self.check(prefix + "late_response_rejected", browser.evaluate(
                "window.__lateResult==='session_boundary_changed'"))
            browser.click(".project-status button:nth-of-type(3)")
            browser.wait("!!document.querySelector('dialog[data-session-recovery]')")
            self.type(browser, "dialog[data-session-recovery] input[name=username]", "alice")
            self.type(browser, "dialog[data-session-recovery] input[name=password]",
                      self.accounts["alice"]["password"])
            browser.click("dialog[data-session-recovery] button[type=submit]")
            browser.wait("document.body.dataset.opencadSessionLocked!=='true'")
            browser.evaluate("window.__holdPath='/api/mesh';window.__held=false;"
                             "window.__lateMesh=null;window.opencad3d.refresh().then("
                             "()=>window.__lateMesh='accepted',"
                             "e=>window.__lateMesh=e.message);true")
            browser.wait("window.__held===true")
            live_cookie = self.cookie(browser)
            live = self.state(browser)
            body = {"command": "edit_solid", "args": {
                "feature_uuid": json.loads(live["doc"])["features"][-1]["uuid"],
                "distance_nm": 12000000}, "expected_revision": live["revision"],
                "expected_sha": live["sha256"], "request_id": uuid.uuid4().hex}
            code, _, edited = self.http("POST", "/api/command", live_cookie, live["project_id"], body)
            require(code == 200, "concurrent_geometry_edit_rejected")
            with closing(self.Auth(self.runtime / "accounts", operator_mode=True)) as auth:
                auth.revoke_all_sessions(self.accounts["alice"]["user_id"])
            self.api(browser, "/api/state")
            browser.click(".project-status button:nth-of-type(3)")
            browser.wait("!!document.querySelector('dialog[data-session-recovery]')")
            self.type(browser, "dialog[data-session-recovery] input[name=username]", "alice")
            self.type(browser, "dialog[data-session-recovery] input[name=password]",
                      self.accounts["alice"]["password"])
            browser.click("dialog[data-session-recovery] button[type=submit]")
            browser.wait("document.body.dataset.opencadSessionLocked!=='true'")
            new_digest = browser.evaluate("window.opencad3d.document_sha256")
            browser.evaluate("window.__release();true")
            browser.wait("window.__lateMesh!==null")
            self.check(prefix + "late_mesh_cannot_restore_geometry", browser.evaluate(
                "window.__lateMesh!=='accepted' && window.opencad3d.document_sha256===" +
                json.dumps(new_digest) + " && document.querySelector('#depth').value==='23'")
                and new_digest != hashlib.sha256(alice["doc"].encode()).hexdigest()
                and abs(browser.evaluate("window.opencad3d.volume_mm3") -
                        (800 - math.pi * 9) * 12) < 1e-5)
            write(view_out / "version-witness.json", {"old_document_sha256": hashlib.sha256(
                alice["doc"].encode()).hexdigest(), "current_document_sha256": new_digest,
                "new_revision": edited["revision"], "late_outcome": browser.evaluate(
                    "window.__lateMesh")})
            # The public transport must not log another user into stale closures.
            switched = browser.evaluate(
                "fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':"
                "'application/json'},body:JSON.stringify({username:'bob',password:" +
                json.dumps(self.accounts["bob"]["password"]) +
                "})}).then(()=>true,()=>false);true")
            require(switched is True, "identity_switch_dispatch")
            browser.wait("!!document.querySelector('dialog[open] select')")
            self.check(prefix + "direct_identity_switch_reload", browser.evaluate(
                "!window.__savedDocument && !window.__identityCache && "
                "document.querySelector('#depth').value==='6'"))
            self.open_project(browser, bob["project_id"])
            browser.wait("document.documentElement.lang==='en' && "
                         "window.opencad3d.triangles>0 && !window.opencad3d.busy")
            bob_restored = self.state(browser)
            self.check(prefix + "bob_persistent_locale_and_model", bob_restored["doc"] == bob["doc"]
                       and bob_restored["undo_depth"] == bob["undo_depth"]
                       and bob_restored["revision"] == bob["revision"])
            browser.screenshot("restored-bob")

    def run(self):
        sys.path.insert(0, str(self.app))
        from access_control import AuthController
        from project_store import ProjectStore
        self.Auth, self.Store = AuthController, ProjectStore
        self.start()
        (self.runtime / "profiles").mkdir()
        for width, height in ((1440, 900), (390, 844)):
            self.run_view(width, height)


def acceptance(args):
    root, out, runtime = args.source_root.resolve(), args.out.resolve(), args.runtime_root.resolve()
    require(root == ROOT, "runner_must_be_same_source")
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) and
            runtime.is_relative_to(Path("/data/tmp").resolve()), "external_fixture_roots_required")
    require(not out.exists() and not runtime.exists(), "fresh_roots_required")
    out.mkdir(parents=True)
    runtime.mkdir(parents=True)
    report = {"verdict": "FAIL", "original_rc": 1, "started_at": now(),
              "source_before": None, "source_after": None, "source_drift": [],
              "checks": [], "first_cause": None, "cleanup_errors": [],
              "production_mutated": False, "gpu_assessed": False,
              "scope": "local desktop/mobile identity workflow, not all-site language"}
    report["runtime_root"] = str(runtime)
    review = Review(root, out, runtime, args.browser)
    try:
        report["source_before"] = source(root)
        review.run()
    except Exception as error:
        report["first_cause"] = str(error)
        write(out / "failure.log", traceback.format_exc().encode())
    finally:
        try:
            review.stop()
        except Exception as error:
            review.cleanup_errors.append(str(error))
            if report["first_cause"] is None:
                report["first_cause"] = str(error)
        report.update(checks=review.checks, cli=review.cli, cleanup_errors=review.cleanup_errors)
        try:
            review.check_log.close()
            report["source_after"] = source(root)
            report["source_drift"] = [
                key for key in ("head", "files", "status")
                if not report["source_before"] or
                report["source_before"][key] != report["source_after"][key]]
            artifacts = {}
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    require(not any(secret in data for secret in review.secrets),
                            "secret_in_evidence")
                    artifacts[path.relative_to(out).as_posix()] = {
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            report["artifacts"] = artifacts
            if report["first_cause"] is None and not report["source_drift"] and not review.cleanup_errors:
                report.update(verdict="PASS", original_rc=0)
                validate_bound_report(report, root, out)
            elif report["first_cause"] is None:
                report["first_cause"] = "source_or_cleanup_changed"
        except Exception as error:
            report.update(verdict="FAIL", original_rc=1)
            if report["first_cause"] is None:
                report["first_cause"] = str(error)
            else:
                report["cleanup_errors"].append("terminal_binding:" + str(error))
            write(out / "terminal-failure.log", traceback.format_exc().encode())
        report["finished_at"] = now()
        marker = (f"{MARKER} verdict={report['verdict']} checks={len(review.checks)} "
                  f"rc={report['original_rc']}\n")
        write(out / "completion.log", marker.encode())
        write(out / "result.json", report)
        print(marker.strip(), flush=True)
        print("evidence=" + str(out), flush=True)
    return report["original_rc"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    parser.add_argument("--verify-evidence", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_evidence:
        args.out.mkdir(parents=True, exist_ok=False)
        try:
            report = json.loads((args.verify_evidence / "result.json").read_text())
            count = validate_evidence(report, args.source_root.resolve(), args.verify_evidence)
            result = {"verdict": "EVIDENCE_VERIFIED", "checks": count, "original_rc": 0,
                      "result_sha256": hashlib.sha256(
                          (args.verify_evidence / "result.json").read_bytes()).hexdigest(),
                      "at": now()}
        except Exception as error:
            result = {"verdict": "FAIL", "first_cause": str(error), "original_rc": 1, "at": now()}
        write(args.out / "result.json", result)
        print(json.dumps(result), flush=True)
        return result["original_rc"]
    if args.selftest:
        args.out.mkdir(parents=True, exist_ok=False)
        before = source(args.source_root)
        import unittest
        suite = unittest.defaultTestLoader.discover(
            str(args.source_root / "oss-alternatives/apps/opencad/tests"),
            pattern="test_project_session_browser.py")
        old_temp = tempfile.tempdir
        tempfile.tempdir = str(args.out.resolve())
        try:
            with (args.out / "tests.log").open("x") as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        finally:
            tempfile.tempdir = old_temp
        after = source(args.source_root)
        rc = 0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped \
            and before == after else 1
        write(args.out / "result.json", {"verdict": "PASS" if rc == 0 else "FAIL",
              "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skips": len(result.skipped),
              "original_rc": rc, "source_before": before, "source_after": after,
              "source_drift": [] if before == after else ["source"],
              "tests_log_sha256": hashlib.sha256((args.out / "tests.log").read_bytes()).hexdigest(),
              "at": now()})
        print(f"{MARKER}_SELFTEST tests={result.testsRun} rc={rc}", flush=True)
        return rc
    require(args.runtime_root is not None, "runtime_root_required")
    return acceptance(args)


if __name__ == "__main__":
    raise SystemExit(main())
