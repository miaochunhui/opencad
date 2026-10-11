"""Real HTTP tests of the private-project boundary, including rejection paths."""

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from secure_web import Reply, build_server, session_cookie


class RecordingApplication:
    def __init__(self):
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        return Reply(payload={"ok": True, "project": request.project_id,
                              "authenticated": request.token is not None})


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="opencad-http-")
        root = Path(self.directory.name)
        (root / "index.html").write_text("<html>fixture</html>")
        self.app = RecordingApplication()
        self.server = build_server(self.app, web_root=root)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.directory.cleanup()

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            connection.close()

    def test_static_public_and_confined(self):
        status, headers, raw = self.call("GET", "/")
        self.assertEqual((status, raw), (200, b"<html>fixture</html>"))
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.app.requests, [])
        status, _, _ = self.call("GET", "/web/../../README.md")
        self.assertEqual(status, 404)

    def test_no_shared_workspace_fallback(self):
        status, _, raw = self.call("GET", "/api/state", headers={"X-User": "forged"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["authenticated"])
        self.assertIsNone(self.app.requests[-1].token)

    def test_bearer_project_context(self):
        status, _, _ = self.call("GET", "/api/state", headers={
            "Authorization": "Bearer opaque", "X-OpenCAD-Project": "project-a"})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.requests[-1].project_id, "project-a")
        self.assertEqual(self.app.requests[-1].token, "opaque")
        self.assertFalse(self.app.requests[-1].cookie_auth)

    def test_cookie_csrf_and_login_csrf(self):
        for path, cookie in [("/api/command", "opencad_session=opaque"),
                             ("/api/auth/login", "")]:
            before = len(self.app.requests)
            status, _, raw = self.call("POST", path, "{}", {
                "Content-Type": "application/json", "Cookie": cookie,
                "Origin": "https://attacker.invalid", "X-OpenCAD-Request": "1"})
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(raw)["error"], "cross_site_request")
            self.assertEqual(len(self.app.requests), before)
        status, _, _ = self.call("POST", "/api/command", "{}", {
            "Content-Type": "application/json", "Cookie": "opencad_session=opaque",
            "Origin": self.origin, "X-OpenCAD-Request": "1"})
        self.assertEqual(status, 200)

    def test_identity_ambiguity_rejected(self):
        status, _, raw = self.call("GET", "/api/state", headers={
            "Cookie": "opencad_session=opaque", "Authorization": "Bearer other"})
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "ambiguous_identity")
        status, _, raw = self.call("GET", "/api/state", headers={
            "Cookie": "opencad_session=one; opencad_session=two"})
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "ambiguous_identity")

    def test_read_body_is_rejected_before_static_or_application(self):
        for path in ("/", "/api/state"):
            status, _, raw = self.call("GET", path, "{}",
                                      {"Content-Type": "application/json"})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(raw)["error"], "unexpected_body")
        self.assertEqual(self.app.requests, [])

    def test_json_negative_controls(self):
        for body in ['{"a":1,"a":2}', '{"a":NaN}', "[]", "{"]:
            before = len(self.app.requests)
            status, _, _ = self.call("POST", "/api/command", body,
                                     {"Content-Type": "application/json"})
            self.assertEqual(status, 400, body)
            self.assertEqual(len(self.app.requests), before)

    def test_media_and_length_rejected(self):
        status, _, _ = self.call("POST", "/api/command", "{}",
                                 {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.call("POST", "/api/command", "{}",
                                 {"Content-Length": "invalid"})
        self.assertEqual(status, 400)
        self.assertEqual(self.app.requests, [])

    def test_cookie_attributes(self):
        cookie = session_cookie("opaque123", secure=True)
        for field in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(field, cookie)
        self.assertIn("Max-Age=0", session_cookie("", secure=True, clear=True))
        with self.assertRaises(ValueError):
            session_cookie("bad;injection", secure=True)

    def test_public_origin_validation(self):
        for origin in ("http://example.com", "https://example.com/",
                       "https://user@example.com", "https://example.com?x=1",
                       "https://example.com\n", "https://example.com:invalid",
                       "https://example.com:65536", "https://example.com:"):
            with self.assertRaises(ValueError, msg=origin):
                build_server(self.app, public_origin=origin)

    def test_actual_cli_bad_origin_does_not_initialize_databases(self):
        root = Path(self.directory.name) / "must-not-exist-origin"
        process = subprocess.run([
            sys.executable, "-B", str(APP / "secure_web.py"), "--data-root", str(root),
            "--public-origin", "http://example.com", "--port", "0"],
            capture_output=True, timeout=15, check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b"HTTPS origin", process.stderr)
        self.assertNotIn(b"verdict=READY", process.stdout)
        self.assertFalse(root.exists(), "Invalid configuration initialized private databases")

    def test_actual_cli_bind_failure_does_not_initialize_databases(self):
        root = Path(self.directory.name) / "must-not-exist-bind"
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            process = subprocess.run([
                sys.executable, "-B", str(APP / "secure_web.py"), "--data-root", str(root),
                "--public-origin", "http://127.0.0.1",
                "--port", str(occupied.getsockname()[1])],
                capture_output=True, timeout=15, check=False)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn(b"Address already in use", process.stderr)
        self.assertNotIn(b"verdict=READY", process.stdout)
        self.assertFalse(root.exists(), "Listener bind failure initialized private databases")

    def test_server_close_drains_admitted_request(self):
        entered, release, closed, acknowledged = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        app = self.app
        original = app.handle

        def in_flight(request):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release its admitted request")
            return original(request)

        app.handle = in_flight
        failures = []

        def request():
            try:
                self.assertEqual(self.call("POST", "/api/save", "{}",
                                           {"Content-Type": "application/json"})[0], 200)
                acknowledged.set()
            except BaseException as exc:
                failures.append(exc)

        client = threading.Thread(target=request)
        client.start()
        self.assertTrue(entered.wait(timeout=3))

        def close():
            self.server.shutdown()
            self.server.server_close()
            closed.set()

        closer = threading.Thread(target=close)
        closer.start()
        try:
            self.assertFalse(closed.wait(timeout=.1),
                             "Server closed while an admitted request was still running")
        finally:
            release.set()
            client.join(timeout=5)
            closer.join(timeout=5)
        self.assertFalse(client.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(acknowledged.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(len(self.app.requests), 1)

    def test_connection_capacity_and_release(self):
        acquired = []
        try:
            for _ in range(32):
                self.assertTrue(self.server.slots.acquire(blocking=False))
                acquired.append(True)
            status, _, _ = self.call("GET", "/api/state")
            self.assertEqual(status, 503)
            self.assertEqual(self.app.requests, [])
        finally:
            for _ in acquired:
                self.server.slots.release()
        self.assertEqual(self.call("GET", "/api/state")[0], 200)


if __name__ == "__main__":
    unittest.main()
