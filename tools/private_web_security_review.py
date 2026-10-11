#!/usr/bin/env python3
"""Finite, source-bound private Web review against an isolated real service.

Use --source-root to review another tree read-only. No production URL is
accepted. SQLite fixtures require --runtime-root or OPENCAD_TEST_RUNTIME_ROOT
on local storage outside source/evidence. Each run owns a new evidence directory.
Raw credentials and HTTP request bytes are deliberately never written to logs.
HTTP cases use the real auth/store/gateway; browser cases use real Chromium and
its network, with explicit response-loss injection rather than product mocks.
This is not a complete security assessment or production release authorization.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = None


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest(root):
    entries = {}
    for base in (root / "oss-alternatives/apps/opencad",
                 root / "oss-alternatives/packages"):
        for directory, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name not in {
                "__pycache__", ".pytest_cache", "node_modules", ".venv", ".git"}]
            for name in files:
                path = Path(directory) / name
                if path.suffix not in {".py", ".js", ".css", ".html", ".json", ".toml"}:
                    continue
                if path.is_symlink():
                    raise ValueError("Source symlink is not accepted: " + str(path))
                entries[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    for name in ("tools/private_web_acceptance.py",):
        entries[name] = sha((root / name).read_bytes())
    if not entries:
        raise ValueError("Empty source selection")
    return dict(sorted(entries.items()))


def parse_responses(raw):
    """Parse only complete Content-Length responses; never save response bodies."""
    statuses = []
    offset = 0
    while offset < len(raw):
        end = raw.find(b"\r\n\r\n", offset)
        if end < 0:
            raise ValueError("Incomplete response headers")
        header = raw[offset:end].decode("iso-8859-1")
        lines = header.split("\r\n")
        match = re.fullmatch(r"HTTP/1\.[01] ([0-9]{3})(?: .*)?", lines[0])
        if not match:
            raise ValueError("Invalid response status line")
        lengths = [line.split(":", 1)[1].strip() for line in lines[1:]
                   if line.lower().startswith("content-length:")]
        if len(lengths) != 1 or not lengths[0].isdecimal():
            raise ValueError("Ambiguous response length")
        offset = end + 4 + int(lengths[0])
        if offset > len(raw):
            raise ValueError("Incomplete response body")
        statuses.append(int(match[1]))
    if not statuses:
        raise ValueError("Empty response stream")
    return statuses


class ParserTests(unittest.TestCase):
    def test_two_complete_responses(self):
        self.assertEqual(parse_responses(
            b"HTTP/1.1 403 Denied\r\nContent-Length: 2\r\n\r\n{}"
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"), [403, 200])

    def test_empty_is_not_green(self):
        with self.assertRaises(ValueError):
            parse_responses(b"")

    def test_incomplete_is_not_green(self):
        with self.assertRaises(ValueError):
            parse_responses(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{")

    def test_duplicate_length_is_not_green(self):
        with self.assertRaises(ValueError):
            parse_responses(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n"
                            b"Content-Length: 0\r\n\r\n")

    def test_failure_status_is_preserved(self):
        self.assertEqual(parse_responses(
            b"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n"), [500])


class Service:
    def __init__(self, config):
        self.config = config
        self.app_dir = config.source_root / "oss-alternatives/apps/opencad"
        packages = config.source_root / "oss-alternatives/packages"
        os.environ["OPENCAD_CADCORE_ROOT"] = str(packages / "cadcore")
        os.environ["OPENCAD_OUT_ROOT"] = str(config.out / "legacy-unused")
        os.environ["TMPDIR"] = str(config.out / "tmp")
        tempfile.tempdir = None
        for path in (packages / "vault", packages / "dxf", packages / "cadcore", self.app_dir):
            sys.path.insert(0, str(path))
        self.modules = {name: importlib.import_module(name) for name in (
            "access_control", "project_store", "secure_web", "project_gateway")}
        for name, module in self.modules.items():
            if Path(module.__file__).resolve() != self.app_dir / (name + ".py"):
                raise ValueError("Foreign import: " + name)
        self.directory = tempfile.TemporaryDirectory(
            prefix="private-security-", dir=config.runtime_root)
        self.root = Path(self.directory.name)
        self.clock = [time.time()]
        self.auth = self.modules["access_control"].AuthController(
            self.root / "accounts", allow_registration=True, clock=lambda: self.clock[0])
        self.store = self.modules["project_store"].ProjectStore(self.root / "projects")
        self.app = self.modules["project_gateway"].ProjectGateway(
            self.auth, self.store, self.root / "requests", secure_cookies=False)
        self.server = self.modules["secure_web"].build_server(self.app)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.cookies, self.tokens, self.users, self.heads = {}, {}, {}, {}
        for name in ("alice", "bob"):
            password = uuid.uuid4().hex
            self.users[name] = self.auth.register(name, password)["user_id"]
            status, headers, value = self.call("POST", "/api/auth/login",
                                              {"username": name, "password": password})
            if status != 200 or value.get("ok") is not True:
                raise AssertionError("Real login prerequisite failed")
            self.cookies[name] = headers["Set-Cookie"].split(";", 1)[0]
            self.tokens[name] = self.cookies[name].split("=", 1)[1]
            if name == "bob":
                replacement = self.auth.login(name, password)
                self.replacement_bob_cookie = "opencad_session=" + replacement.token
            status, _, value = self.call("POST", "/api/projects",
                {"title": "Review " + name, "request_id": uuid.uuid4().hex}, user=name)
            if status != 201:
                raise AssertionError("Real project prerequisite failed")
            self.heads[name] = value
        self.bindings = {}
        for name, module in list(sys.modules.items()):
            if name in {"server", "release_state", "solid_commands", "viewer3d_page"} or (
                    name.startswith(("cadcore", "dxf.", "freechat_vault."))):
                file = getattr(module, "__file__", None)
                if file:
                    path = Path(file).resolve()
                    if not path.is_relative_to(config.source_root):
                        raise ValueError("Foreign runtime dependency: " + name)
                    self.bindings[name] = path.relative_to(config.source_root).as_posix()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.close()
        self.directory.cleanup()
        if self.thread.is_alive():
            raise RuntimeError("Isolated HTTP server did not stop")

    def call(self, method, route, body=None, *, user=None, project=None, headers=None):
        actual = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if user:
            actual["Cookie"] = self.cookies[user]
        if project:
            actual["X-OpenCAD-Project"] = project
        if body is not None:
            actual["Content-Type"] = "application/json"
        actual.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, route,
                json.dumps(body) if body is not None else None, actual)
            response = connection.getresponse()
            raw = response.read()
            value = json.loads(raw) if response.getheader(
                "Content-Type", "").startswith("application/json") else raw
            return response.status, dict(response.getheaders()), value
        finally:
            connection.close()

    def stream(self, request):
        raw = bytearray()
        closed = False
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as connection:
            connection.settimeout(3)
            connection.sendall(request)
            try:
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:
                        closed = True
                        break
                    raw.extend(chunk)
            except socket.timeout:
                # A timeout is an observed open connection, never a successful close.
                pass
        return {"statuses": parse_responses(bytes(raw)), "closed": closed,
                "response_bytes": len(raw)}

    def packet(self, method, route, headers, body=b""):
        lines = [f"{method} {route} HTTP/1.1", f"Host: 127.0.0.1:{self.port}"]
        lines.extend(f"{name}: {value}" for name, value in headers)
        return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body

    def inner(self):
        return self.packet("GET", "/api/auth/me", [
            ("Cookie", self.cookies["alice"]), ("Connection", "close")])


class HTTPReviewTests(unittest.TestCase):
    service = None

    def assert_rejected_stream(self, method, route, headers, *, statuses=(400,)):
        body = self.service.inner()
        result = self.service.stream(self.service.packet(
            method, route, [*headers, ("Content-Length", str(len(body)))], body))
        self.assertIn(result["statuses"][0], statuses, result)
        self.assertEqual(len(result["statuses"]), 1, result)
        self.assertTrue(result["closed"], result)

    def test_get_api_body_rejected_before_second_request(self):
        self.assert_rejected_stream("GET", "/api/auth/me", [
            ("Cookie", self.service.cookies["alice"])])

    def test_get_static_body_rejected_before_second_request(self):
        self.assert_rejected_stream("GET", "/", [])

    def test_head_body_rejected_without_following_request(self):
        body = self.service.inner()
        raw = bytearray()
        closed = False
        with socket.create_connection(("127.0.0.1", self.service.port), timeout=3) as connection:
            connection.settimeout(3)
            connection.sendall(self.service.packet("HEAD", "/", [
                ("Content-Length", str(len(body)))], body))
            try:
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:
                        closed = True
                        break
                    raw.extend(chunk)
            except socket.timeout:
                pass
        # HEAD carries the advertised GET length but no response body.
        header, separator, remainder = raw.partition(b"\r\n\r\n")
        self.assertTrue(separator, "HEAD rejection has no complete headers")
        self.assertTrue(header.startswith(b"HTTP/1.1 400 "), "HEAD body was not rejected")
        self.assertEqual(remainder, b"", "HEAD body caused another response")
        self.assertTrue(closed, "HEAD body rejection left the connection open")

    def test_csrf_early_rejection_closes_unconsumed_body(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Cookie", self.service.cookies["alice"]),
            ("Content-Type", "application/json"),
            ("Origin", "https://foreign.invalid")], statuses=(403,))

    def test_ambiguous_authorization_closes_unconsumed_body(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Authorization", "Bearer " + self.service.tokens["alice"]),
            ("Authorization", "Bearer " + self.service.tokens["bob"]),
            ("Content-Type", "application/json")], statuses=(401,))

    def test_ambiguous_project_closes_unconsumed_body(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Authorization", "Bearer " + self.service.tokens["alice"]),
            ("X-OpenCAD-Project", self.service.heads["alice"]["project_id"]),
            ("X-OpenCAD-Project", self.service.heads["bob"]["project_id"]),
            ("Content-Type", "application/json")])

    def test_duplicate_cookie_headers_rejected(self):
        result = self.service.stream(self.service.packet("GET", "/api/auth/me", [
            ("Cookie", self.service.cookies["alice"]),
            ("Cookie", self.service.cookies["bob"]), ("Connection", "close")]))
        self.assertEqual(result["statuses"], [401], result)

    def test_duplicate_session_cookie_names_rejected(self):
        result = self.service.stream(self.service.packet("GET", "/api/auth/me", [
            ("Cookie", self.service.cookies["alice"] + "; " + self.service.cookies["bob"]),
            ("Connection", "close")]))
        self.assertEqual(result["statuses"], [401], result)

    def test_transfer_encoding_rejected_even_on_static_get(self):
        self.assert_rejected_stream("GET", "/", [("Transfer-Encoding", "chunked")])

    def test_empty_transfer_encoding_rejected(self):
        result = self.service.stream(self.service.packet("GET", "/api/auth/me", [
            ("Transfer-Encoding", ""), ("Cookie", self.service.cookies["alice"]),
            ("Connection", "close")]))
        self.assertEqual(result["statuses"], [400], result)

    def test_empty_transfer_encoding_post_rejected(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Transfer-Encoding", ""),
            ("Authorization", "Bearer " + self.service.tokens["alice"]),
            ("Content-Type", "application/json")])

    def test_duplicate_transfer_encoding_rejected(self):
        self.assert_rejected_stream("GET", "/", [
            ("Transfer-Encoding", ""), ("Transfer-Encoding", "chunked")])

    def test_duplicate_cookie_post_rejection_closes_body(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Cookie", self.service.cookies["alice"]),
            ("Cookie", self.service.cookies["bob"]),
            ("Origin", self.service.origin), ("X-OpenCAD-Request", "1"),
            ("Content-Type", "application/json")], statuses=(401,))

    def test_duplicate_content_length_rejected(self):
        self.assert_rejected_stream("POST", "/api/save", [
            ("Authorization", "Bearer " + self.service.tokens["alice"]),
            ("Content-Type", "application/json"), ("Content-Length", "0")])

    def test_bodyless_get_and_fully_consumed_posts_keep_alignment(self):
        # Compute length from real bytes, not from a fixture's handwritten count.
        body = b'{"locale":"en"}'
        first = self.service.packet("POST", "/api/locale", [
            ("Authorization", "Bearer " + self.service.tokens["alice"]),
            ("Content-Type", "application/json"), ("Content-Length", str(len(body)))], body)
        result = self.service.stream(first + self.service.inner())
        self.assertEqual(result["statuses"], [200, 200], result)
        self.assertTrue(result["closed"], result)

    def test_bodyless_get_pipeline_keeps_alignment(self):
        first = self.service.packet("GET", "/api/auth/me", [
            ("Cookie", self.service.cookies["alice"])])
        result = self.service.stream(first + self.service.inner())
        self.assertEqual(result["statuses"], [200, 200], result)
        self.assertTrue(result["closed"], result)

    def test_cross_user_all_project_routes_denied_and_unchanged(self):
        head = self.service.heads["alice"]
        before = self.service.store.read_head(head["project_id"])
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex}
        routes = [("GET", path, None) for path in (
            "/api/state", "/api/mesh", "/api/document", "/api/step", "/api/dxf", "/api/pdf")]
        routes += [
            ("POST", "/api/clearance", {}),
            ("POST", "/api/command", {"command": "precision_status"}),
            ("POST", "/api/command", {**body, "command": "add_line",
                                     "args": {"p1": [0, 0], "p2": [1000, 0]}}),
            ("POST", "/api/standard", {**body, "standard": "ansi"}),
            ("POST", "/api/open", {**body, "doc": before["snapshot"]["doc"]}),
            ("POST", "/api/save", body),
        ]
        for method, route, payload in routes:
            with self.subTest(method=method, route=route):
                status, _, value = self.service.call(
                    method, route, payload, user="bob", project=head["project_id"])
                self.assertEqual(status, 403, {"route": route, "status": status})
                self.assertFalse(value["ok"])
        self.assertEqual(self.service.store.read_head(head["project_id"]), before)

    def test_identity_forged_headers_and_anonymous_never_downgrade(self):
        for headers in ({}, {"X-User": self.service.users["alice"], "X-Admin": "true"},
                        {"Authorization": "Bearer forged"}):
            with self.subTest(kind="forged" if headers else "anonymous"):
                status, _, _ = self.service.call("GET", "/api/projects", headers=headers)
                self.assertEqual(status, 401)
        self.assertEqual(self.service.call("GET", "/api/release/state", user="alice")[0], 403)

    def test_cookie_csrf_and_logout_revocation(self):
        # Existing fixture cookies remain valid for the other independent cases.
        for headers in ({"Origin": "https://foreign.invalid"}, {"X-OpenCAD-Request": "0"}):
            self.assertEqual(self.service.call("POST", "/api/auth/logout", {},
                                              user="bob", headers=headers)[0], 403)
        self.assertEqual(self.service.call("GET", "/api/auth/me", user="bob")[0], 200)
        try:
            status, headers, _ = self.service.call("POST", "/api/auth/logout", {}, user="bob")
            self.assertEqual(status, 200)
            self.assertIn("Max-Age=0", headers["Set-Cookie"])
            self.assertEqual(self.service.call("GET", "/api/auth/me", user="bob")[0], 401)
        finally:
            # Other cases keep an independently issued real session, never a
            # forged principal or an exception-dependent anonymous fallback.
            self.service.cookies["bob"] = self.service.replacement_bob_cookie
            self.service.tokens["bob"] = self.service.cookies["bob"].split("=", 1)[1]

    def test_expired_ticket_denied_over_http(self):
        original = self.service.clock[0]
        try:
            self.service.clock[0] += 8 * 3600 + 1
            self.assertEqual(self.service.call("GET", "/api/auth/me", user="alice")[0], 401)
        finally:
            self.service.clock[0] = original

    def test_preferences_and_project_list_are_owner_scoped(self):
        for name, locale in (("alice", "zh-CN"), ("bob", "ja")):
            self.assertEqual(self.service.call("POST", "/api/locale", {"locale": locale},
                                               user=name)[0], 200)
            status, _, value = self.service.call("GET", "/api/auth/me", user=name)
            self.assertEqual(status, 200)
            self.assertEqual(value["user"]["user_id"], self.service.users[name])
            self.assertEqual(value["preferences"]["locale"], locale)
            status, _, value = self.service.call("GET", "/api/projects", user=name)
            self.assertEqual(status, 200)
            self.assertEqual({item["project_id"] for item in value["projects"]},
                             {self.service.heads[name]["project_id"]})
            self.assertNotIn("token", value)
            self.assertNotIn("password", value)

    def test_static_csp_and_cookie_attributes(self):
        for route in ("/", "/web/3d.html", "/web/js/project-session.js"):
            status, headers, raw = self.service.call("GET", route)
            self.assertEqual(status, 200, route)
            policy = headers["Content-Security-Policy"]
            for directive in ("script-src 'self'", "object-src 'none'",
                              "frame-ancestors 'none'", "base-uri 'none'"):
                self.assertIn(directive, policy)
            self.assertNotIn("'unsafe-inline'", policy)
            if "html" in headers["Content-Type"]:
                self.assertNotRegex(raw.decode(), r"<script(?![^>]*\bsrc=)[^>]*>\s*\S")
        cookie = self.service.modules["secure_web"].session_cookie(
            self.service.tokens["alice"], secure=True)
        for attribute in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertTrue(attribute in cookie, "Missing cookie attribute: " + attribute)


BROWSER_INSTRUMENTATION = r"""
(function() {
  const key = "private-security-review-audit";
  const audit = JSON.parse(sessionStorage.getItem(key) || "[]");
  window.securityReview = {audit: audit, lose: false, csp: []};
  window.addEventListener("securitypolicyviolation", function(event) {
    securityReview.csp.push({directive:event.effectiveDirective,
                            blocked:event.blockedURI});
  });
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function(input, init) {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== location.origin) {
      throw new Error("Review fixture rejects foreign network");
    }
    let record = null;
    if (request.method === "POST" &&
        ["/api/command", "/api/save"].includes(url.pathname)) {
      const body = JSON.parse(await request.clone().text());
      // Read-only legacy queries share /api/command but carry no transaction ID.
      if (typeof body.request_id === "string") {
        record = {path:url.pathname, request_id:body.request_id,
          revision:body.expected_revision, sha256:body.expected_sha};
        audit.push(record);
        sessionStorage.setItem(key, JSON.stringify(audit));
      }
    }
    const response = await nativeFetch(input, init);
    if (record) {
      // Delay a real received response so concurrent callers exercise the queue.
      await response.clone().arrayBuffer();
      await new Promise(function(resolve) { setTimeout(resolve, 50); });
      record.status = response.status;
      sessionStorage.setItem(key, JSON.stringify(audit));
      if (securityReview.lose) {
        securityReview.lose = false;
        throw new Error("Review injected loss after real server confirmation");
      }
    }
    return response;
  };
})();
"""


class Browser:
    def __init__(self, service, executable):
        self.service = service
        self.executable = executable
        # AF_UNIX has a short path limit; only sockets/profile use local storage.
        self.profile = tempfile.TemporaryDirectory(prefix="s-", dir=service.config.runtime_root)
        self.log = (service.config.out / "chrome.log").open("xb")
        self.process = self.ws = None

    def start(self):
        service = self.service
        helper_path = service.app_dir / "tests/legacy_browser_compat_gate.py"
        spec = importlib.util.spec_from_file_location("private_review_cdp", helper_path)
        helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = helper
        spec.loader.exec_module(helper)
        self.helper = helper
        self.process = subprocess.Popen(
            [str(self.executable), "--headless", "--no-sandbox", "--disable-dev-shm-usage",
             "--remote-debugging-port=0", "--user-data-dir=" + self.profile.name,
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            env=dict(os.environ, TMPDIR=self.profile.name),
            stdout=self.log, stderr=subprocess.STDOUT)
        port_file = Path(self.profile.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.is_file():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Chromium did not become ready")
            time.sleep(0.05)
        self.debug = "http://127.0.0.1:" + port_file.read_text().splitlines()[0]
        with urlopen(self.debug + "/json/list", timeout=10) as response:
            tabs = json.load(response)
        self.ws = helper._WSClient(next(tab["webSocketDebuggerUrl"]
                                       for tab in tabs if tab["type"] == "page"))
        self.seq = {"id": 0}
        self.call("Runtime.enable", {})
        self.call("Page.enable", {})
        self.call("Network.enable", {})
        cookie = self.call("Network.setCookie", {
            "name": "opencad_session", "value": service.tokens["alice"],
            "url": service.origin, "httpOnly": True, "sameSite": "Strict"})
        if cookie.get("success") is not True:
            raise RuntimeError("Browser cookie fixture was not installed")
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source":
            "sessionStorage.setItem('opencad-project-id'," +
            json.dumps(service.heads["alice"]["project_id"]) + ");\n" +
            BROWSER_INSTRUMENTATION})
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": service.config.width, "height": 900, "deviceScaleFactor": 1,
            "mobile": service.config.width < 640})
        self.call("Page.navigate", {"url": service.origin +
                                   ("/web/3d.html" if service.config.page == "3d" else "/")})
        self.open_project()

    def call(self, method, params):
        result = self.helper._cdp_call(self.ws, self.seq, method, params, 30)
        if "error" in result or "exceptionDetails" in result.get("result", {}):
            raise RuntimeError("CDP evaluation failed: " + method)
        return result["result"]

    def evaluate(self, expression):
        return self.call("Runtime.evaluate", {"expression": expression,
            "returnByValue": True, "awaitPromise": True})["result"].get("value")

    def wait(self, expression):
        deadline = time.monotonic() + 20
        while not self.evaluate(expression):
            if time.monotonic() >= deadline:
                raise AssertionError("Browser completion condition not reached")
            time.sleep(0.05)

    def open_project(self):
        self.wait("Boolean(document.querySelector('dialog[open] select')&&"
                  "document.querySelector('dialog[open] select').options.length)")
        self.click("打开项目", selector="dialog[open] button")
        self.wait("document.readyState==='complete'&&!document.querySelector('dialog[open]')")

    def edit(self, *, x=1000):
        return self.evaluate("""
          fetch('/api/command',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({command:'add_line',args:{p1:[0,0],p2:[%d,0]}})})
            .then(async function(r){return {status:r.status,value:await r.json()};})
            .catch(function(e){return {rejected:true};})
        """ % x)

    def audit(self):
        return self.evaluate("securityReview.audit")

    def notice(self):
        return self.evaluate("(document.querySelector('.project-status output')||{}).textContent||''")

    def click(self, text, *, selector=".project-status button"):
        target = self.evaluate("""(function(){
          const button=Array.from(document.querySelectorAll(%s))
            .find(function(b){return b.textContent===%s;});
          if(!button||button.disabled||button.hidden)return {clickable:false};
          button.scrollIntoView({block:'center'});
          const r=button.getBoundingClientRect();
          const x=r.x+r.width/2,y=r.y+r.height/2;
          const hit=document.elementFromPoint(x,y);
          return {x:x,y:y,clickable:hit===button||button.contains(hit)};
        })()""" % (json.dumps(selector), json.dumps(text)))
        if not target["clickable"]:
            raise AssertionError("Real pointer target is obscured or unavailable: " + text)
        self.call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": target["x"], "y": target["y"],
            "button": "left", "clickCount": 1})
        self.call("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": target["x"], "y": target["y"],
            "button": "left", "clickCount": 1})

    def close(self):
        if self.ws is not None:
            self.ws.sock.close()
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
        self.profile.cleanup()


class BrowserReviewTests(unittest.TestCase):
    browser = None

    def test_01_real_csp_assets_and_httponly_cookie(self):
        browser = self.browser
        self.assertEqual(browser.evaluate("securityReview.csp"), [])
        self.assertFalse(browser.evaluate("document.cookie.includes('opencad_session')"))
        self.assertTrue(browser.evaluate(
            "Boolean(document.querySelector('script[src=\"/web/js/project-session.js\"]'))"))
        self.assertTrue(browser.evaluate("Boolean(document.querySelector('.project-status'))"))

    def test_02_concurrent_edits_serialize_real_versions(self):
        browser = self.browser
        count = len(browser.audit())
        results = browser.evaluate("""
          Promise.all([1000,2000].map(function(x){
            return fetch('/api/command',{method:'POST',
              headers:{'Content-Type':'application/json'},
              body:JSON.stringify({command:'add_line',args:{p1:[0,0],p2:[x,0]}})})
              .then(async function(r){return {status:r.status,value:await r.json()};});
          }))
        """)
        self.assertEqual([result["status"] for result in results], [200, 200])
        records = browser.audit()[count:]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["revision"], records[0]["revision"] + 1)
        self.assertNotEqual(records[0]["request_id"], records[1]["request_id"])

    def test_03_lost_response_blocks_edits_and_replays_exact_receipt(self):
        browser = self.browser
        service = browser.service
        project = service.heads["alice"]["project_id"]
        before = service.store.read_head(project)
        count = len(browser.audit())
        browser.evaluate("securityReview.lose=true")
        self.assertTrue(browser.edit(x=3000).get("rejected"))
        self.assertTrue(browser.edit(x=4000).get("rejected"))
        records = browser.audit()[count:]
        self.assertEqual(len(records), 1)
        after = service.store.read_head(project)
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertIn("未确认", browser.notice())
        browser.click("确认上一笔编辑")
        browser.open_project()
        records = browser.audit()[count:]
        self.assertEqual(len(records), 2)
        for field in ("request_id", "revision", "sha256", "path"):
            self.assertEqual(records[0][field], records[1][field], field)
        self.assertEqual([record["status"] for record in records], [200, 200])
        self.assertEqual(service.store.read_head(project), after)

    def test_04_conflict_blocks_following_mutations_until_refresh(self):
        browser = self.browser
        service = browser.service
        project = service.heads["alice"]["project_id"]
        head = service.store.read_head(project)
        body = {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, "command": "add_line",
                "args": {"p1": [0, 0], "p2": [5000, 0]}}
        self.assertEqual(service.call("POST", "/api/command", body, user="alice",
                                      project=project)[0], 200)
        count = len(browser.audit())
        self.assertEqual(browser.edit(x=6000)["status"], 409)
        self.assertTrue(browser.edit(x=7000).get("rejected"))
        self.assertEqual(len(browser.audit()) - count, 1)
        self.assertIn("冲突", browser.notice())
        browser.click("刷新最新版本")
        browser.open_project()
        self.assertEqual(browser.edit(x=8000)["status"], 200)

    def test_05_read_401_is_explicit_in_active_project(self):
        browser = self.browser
        self.assertEqual(browser.service.call("POST", "/api/auth/logout", {},
                                              user="alice")[0], 200)
        status = browser.evaluate(
            "fetch('/api/state').then(function(r){return r.status;})")
        self.assertEqual(status, 401)
        self.assertIn("登录", browser.notice())

    def test_06_download_401_is_explicit(self):
        browser = self.browser
        browser.evaluate("document.querySelector('.project-status output').textContent=''")
        status = browser.evaluate("""fetch('/api/export/dxf',{method:'POST',
            headers:{'Content-Type':'application/json'},body:'{}'})
            .then(function(r){return r.status;})""")
        self.assertEqual(status, 401)
        self.assertIn("登录", browser.notice())

    def test_07_mutation_401_does_not_confirm_or_advance_head(self):
        browser = self.browser
        project = browser.service.heads["alice"]["project_id"]
        before = browser.service.store.read_head(project)
        self.assertEqual(browser.edit(x=9000)["status"], 401)
        self.assertIn("登录", browser.notice())
        self.assertEqual(browser.service.store.read_head(project), before)

    def test_08_real_csp_remains_clean_after_interactions(self):
        self.assertEqual(self.browser.evaluate("securityReview.csp"), [])

    def test_09_status_output_does_not_cover_controls(self):
        browser = self.browser
        for width in (390, 1280):
            with self.subTest(width=width):
                browser.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 900, "deviceScaleFactor": 1,
                    "mobile": width < 640})
                value = browser.evaluate("""(function(){
                  const output=document.querySelector('.project-status output');
                  const button=Array.from(document.querySelectorAll('.project-status button'))
                    .find(function(b){return b.textContent==='退出登录';});
                  const r=button.getBoundingClientRect();
                  const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                  return {position:getComputedStyle(output).position,
                    clear:hit===button||button.contains(hit)};
                })()""")
                self.assertEqual(value["position"], "static")
                self.assertTrue(value["clear"], value)


def run_tests(suite, log):
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    text = stream.getvalue()
    log.write(text)
    log.flush()
    summary = {"tests": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skips": len(result.skipped),
               "passed": result.testsRun - len(result.failures) - len(result.errors)
               - len(result.skipped)}
    summary["verdict"] = "PASS" if result.testsRun and result.wasSuccessful(
        ) and not result.skipped else "FAIL"
    return summary


def main(argv=None):
    global CONFIG
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--runtime-root", type=Path, default=os.environ.get(
        "OPENCAD_TEST_RUNTIME_ROOT"))
    parser.add_argument("--stage", choices=("selftest", "http", "browser"), default="http")
    parser.add_argument("--browser", type=Path, help="Explicit real Chromium executable")
    parser.add_argument("--page", choices=("2d", "3d"), default="2d")
    parser.add_argument("--width", type=int, choices=(390, 1280), default=1280)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    args.source_root = args.source_root.resolve()
    args.out = (args.out or Path("/hf3fs-jd/data/tmp/opencad-auth-20261011/security") /
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).absolute()
    if not args.out.is_relative_to(Path("/hf3fs-jd/data/tmp")):
        parser.error("Evidence must be under /hf3fs-jd/data/tmp")
    evidence_root = Path("/hf3fs-jd/data/tmp")
    if not args.out.resolve().is_relative_to(evidence_root.resolve()):
        parser.error("Evidence alias must resolve inside the official temporary root")
    if any(path.is_symlink() for path in (args.out, *args.out.parents)
           if path.is_relative_to(evidence_root)):
        parser.error("Evidence descendants must not traverse symlinks")
    if args.stage != "selftest":
        if args.runtime_root is None:
            parser.error("Explicit local runtime root or OPENCAD_TEST_RUNTIME_ROOT is required")
        args.runtime_root = args.runtime_root.absolute()
        if args.runtime_root.resolve().is_relative_to(args.source_root) or (
                str(args.runtime_root.resolve()).startswith(("/hf3fs", "/3fs"))):
            parser.error("SQLite runtime root must be local and outside the reviewed tree")
        args.runtime_root.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "tmp").mkdir()
    CONFIG = args
    report = {"schema": "opencad.private-security-review.v1", "started_at": now(),
              "source_root": str(args.source_root), "stage": args.stage, "verdict": "FAIL",
              "page": args.page, "width": args.width,
              "production_requests": 0, "auto_retries": 0,
              "review_tool_sha256": sha(Path(__file__).read_bytes())}
    service, browser, before = None, None, None
    with (args.out / "review.log").open("x") as log:
        try:
            if args.stage == "selftest":
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(ParserTests)
            else:
                before = manifest(args.source_root)
                report["source_before"] = before
                snapshot = args.out / "candidate"
                snapshot.mkdir()
                for relative in ("secure_web.py", "project_gateway.py", "web/js/project-session.js"):
                    target = snapshot / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((args.source_root /
                                        "oss-alternatives/apps/opencad" / relative).read_bytes())
                service = Service(args)
                report["runtime_bindings"] = service.bindings
                if args.stage == "http":
                    HTTPReviewTests.service = service
                    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HTTPReviewTests)
                else:
                    if args.browser is None or not args.browser.is_file():
                        raise ValueError("Real browser executable must be supplied")
                    browser = Browser(service, args.browser)
                    browser.start()
                    BrowserReviewTests.browser = browser
                    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrowserReviewTests)
            report["counts"] = run_tests(suite, log)
            report["verdict"] = report["counts"]["verdict"]
        except Exception as exc:
            # Exception types are retained; arbitrary external exception messages
            # can contain credentials and are not safe evidence fields.
            report["harness_error"] = type(exc).__name__
            report["harness_frames"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(exc.__traceback__)]
            log.write("HARNESS_ERROR type=" + type(exc).__name__ + "\n")
        finally:
            if browser is not None:
                try:
                    try:
                        if browser.ws is not None:
                            report["browser_audit"] = browser.audit()
                            report["browser_notice"] = browser.notice()
                            report["browser_csp"] = browser.evaluate("securityReview.csp")
                    finally:
                        browser.close()
                except Exception as exc:
                    report["browser_cleanup_error"] = type(exc).__name__
                    report["verdict"] = "FAIL"
            if service is not None:
                try:
                    service.close()
                except Exception as exc:
                    report["service_cleanup_error"] = type(exc).__name__
                    report["verdict"] = "FAIL"
            if before is not None:
                try:
                    after = manifest(args.source_root)
                    report["source_drift"] = sorted(name for name in before.keys() | after.keys()
                                                   if before.get(name) != after.get(name))
                    if report["source_drift"]:
                        report["verdict"] = "FAIL"
                except Exception as exc:
                    report["source_finalization_error"] = type(exc).__name__
                    report["verdict"] = "FAIL"
            report["review_tool_sha256_after"] = sha(Path(__file__).read_bytes())
            if report["review_tool_sha256_after"] != report["review_tool_sha256"]:
                report["verdict"] = "FAIL"
            report["finished_at"] = now()
            line = (f"PRIVATE_WEB_SECURITY_REVIEW verdict={report['verdict']} "
                    f"stage={args.stage} counts={report.get('counts')} "
                    f"drift={len(report.get('source_drift', []))} evidence={args.out}")
            log.write(line + "\n")
    report["log_sha256"] = sha((args.out / "review.log").read_bytes())
    (args.out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(line)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
