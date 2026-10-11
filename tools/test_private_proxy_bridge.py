#!/usr/bin/env python3
"""Fail-fast real local TLS/Unix/secure-CLI acceptance; no production traffic.

Only fresh external evidence and a new local fixture child are written.
The existing pinned Caddy image is used without pulling or changing old containers.
Passwords/cookies remain in memory. Fixture processes are stopped, data retained.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import pwd
import secrets
import select
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import private_proxy_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
IMAGE = "sha256:5f4eac53a85872353b24a54bbfa6d5899449717ae9d3b3e1ae7e69a44bdcb411"
STATE = None


def fresh(path, base):
    bridge.need(path.is_absolute() and path != base and path.is_relative_to(base)
                and ".." not in path.parts, "FRESH_EXTERNAL_CHILD_REQUIRED")
    bridge.need(not path.exists(), "FIXTURE_ALREADY_EXISTS")
    path.mkdir(mode=0o700)
    return path


def write(path, value):
    raw = value if isinstance(value, bytes) else bridge.encode(value)
    with path.open("xb") as handle:
        handle.write(raw)
    bridge.need(path.read_bytes() == raw, "ARTIFACT_READBACK_FAILED")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=20)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Run:
    def __init__(self, out, runtime):
        self.out, self.runtime = out, runtime
        self.calls, self.commands, self.events, self.checks = [], [], [], []
        self.sequence = 0
        self.source = None

    def command(self, label, argv):
        child = subprocess.run(argv, capture_output=True)
        record = {"label": label, "argv": argv, "at": bridge.now(),
                  "actual_rc": child.returncode}
        for name, raw in (("stdout", child.stdout), ("stderr", child.stderr)):
            filename = f"command-{len(self.commands):03d}-{name}.log"
            write(self.out / filename, raw)
            record[name] = {"path": filename, "sha256": bridge.digest(raw)}
        self.commands.append(record)
        if child.returncode:
            sys.stderr.buffer.write(child.stderr)
            exc = bridge.Refused("FIXTURE_COMMAND_FAILED_" + label)
            exc.actual_rc = child.returncode
            raise exc
        return child

    def event(self, value):
        self.events.append(value)

    def call(self, label, connection, method, target, body=None, headers=()):
        raw_body = None if body is None else json.dumps(body).encode()
        self.sequence += 1
        connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        if raw_body is not None:
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(raw_body)))
        connection.putheader("Connection", "close")
        try:
            connection.endheaders(raw_body)
            response = connection.getresponse()
            raw = response.read()
            fields = response.getheaders()
            content_type = response.getheader("Content-Type", "")
            payload = json.loads(raw) if raw and content_type.startswith("application/json") else raw
            self.calls.append({"sequence": self.sequence, "label": label, "at": bridge.now(),
                               "method": method, "target": target, "status": response.status,
                               "response_sha256": bridge.digest(raw), "bytes": len(raw)})
            return response.status, fields, payload
        finally:
            connection.close()

    def headers(self, *, user=None, project=None, csrf=True, extra=()):
        result = [("Host", self.authority)]
        if csrf:
            result += [("Origin", self.origin), ("X-OpenCAD-Request", "1")]
        if user:
            result.append(("Cookie", self.cookies[user]))
        if project:
            result.append(("X-OpenCAD-Project", project))
        return result + list(extra)

    def unix(self, label, method="GET", target="/api/auth/me", body=None, **kwargs):
        return self.call(label, UnixConnection(self.socket), method, target, body,
                         self.headers(**kwargs))

    def https(self, label, method="GET", target="/api/auth/me", body=None, **kwargs):
        connection = http.client.HTTPSConnection("localhost", self.tls_port,
                                                 timeout=20, context=self.context)
        return self.call(label, connection, method, target, body, self.headers(**kwargs))

    def direct(self, label, target, user, project):
        connection = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=20)
        return self.call(label, connection, "GET", target,
                         headers=self.headers(user=user, project=project))


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = STATE
        r = cls.r
        r.tls_port, r.upstream_port = port(), port()
        bridge.need(r.tls_port != r.upstream_port, "FIXTURE_PORT_COLLISION")
        r.origin = f"https://localhost:{r.tls_port}"
        r.authority = f"localhost:{r.tls_port}"
        r.socket = r.runtime / "bridge.sock"
        r.data = r.runtime / "private-data"
        r.cookies = {}
        r.passwords = {name: secrets.token_hex(24) for name in ("alice", "bob")}
        sys.path.insert(0, str(APP))
        from access_control import AuthController
        auth = AuthController(r.data / "accounts", allow_registration=True)
        try:
            for name, password in r.passwords.items():
                auth.register(name, password)
        finally:
            auth.close()
        cls.addClassCleanup(cls.stop_secure)
        cls.addClassCleanup(cls.stop_bridge)
        cls.addClassCleanup(cls.stop_caddy)
        cls.start_secure()
        cls.start_bridge()
        cls.start_caddy()

    @classmethod
    def start_secure(cls):
        r = cls.r
        n = sum(item.get("kind") == "secure" for item in r.commands)
        r.secure_err = (r.out / f"secure-{n}.stderr.log").open("xb")
        argv = [sys.executable, "-B", str(APP / "secure_web.py"),
                "--host", "127.0.0.1", "--port", str(r.upstream_port),
                "--public-origin", r.origin, "--data-root", str(r.data)]
        r.secure = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=r.secure_err,
                                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        readable, _, _ = select.select([r.secure.stdout], [], [], 25)
        bridge.need(bool(readable), "SECURE_CLI_READY_TIMEOUT")
        raw = r.secure.stdout.readline()
        write(r.out / f"secure-{n}.ready.log", raw)
        bridge.need(raw == f"OPENCAD_PRIVATE_WEB verdict=READY port={r.upstream_port}\n".encode(),
                    "SECURE_CLI_READY_MISMATCH")
        r.commands.append({"kind": "secure", "pid": r.secure.pid, "argv": argv,
                           "started_at": bridge.now(), "actual_rc": None})
        r.pin = bridge.UpstreamPin(ROOT, r.source["head"], r.secure.pid,
                                   r.upstream_port, r.origin, r.data)

    @classmethod
    def stop_secure(cls):
        r = cls.r
        if getattr(r, "secure", None) is None:
            return
        process = r.secure
        if process.poll() is None:
            process.terminate()
        rc = process.wait(timeout=20)
        process.stdout.close()
        r.secure_err.close()
        for record in reversed(r.commands):
            if record.get("kind") == "secure" and record["pid"] == process.pid:
                record["actual_rc"] = rc
                record["ended_at"] = bridge.now()
                break
        r.secure = None
        bridge.need(rc == 0, "SECURE_CLI_SHUTDOWN_RC_" + str(rc))

    @classmethod
    def start_bridge(cls):
        r = cls.r
        r.server = bridge.BridgeServer(r.socket, r.pin, os.geteuid(), events=r.event)
        r.thread = threading.Thread(target=r.server.serve_forever,
                                    kwargs={"poll_interval": 0.02})
        r.thread.start()

    @classmethod
    def stop_bridge(cls):
        r = cls.r
        if getattr(r, "server", None) is not None:
            r.server.shutdown()
            r.server.server_close()
            r.thread.join(timeout=20)
            bridge.need(not r.thread.is_alive(), "BRIDGE_THREAD_NOT_DRAINED")
            r.server = None

    @classmethod
    def start_caddy(cls):
        r = cls.r
        info = r.command("image-inspect", ["docker", "image", "inspect", IMAGE,
                                         "--format", "{{.Id}}"])
        bridge.need(info.stdout.strip().decode() == IMAGE, "CADDY_IMAGE_ID_MISMATCH")
        tls = r.runtime / "tls"
        tls.mkdir(mode=0o700)
        r.command("fixture-cert", ["openssl", "req", "-x509", "-newkey", "rsa:2048",
                                  "-nodes", "-keyout", str(tls / "key.pem"),
                                  "-out", str(tls / "cert.pem"), "-days", "1",
                                  "-subj", "/CN=localhost",
                                  "-addext", "subjectAltName=DNS:localhost"])
        # The same embedded reverse_proxy stanza is used in the real TLS fixture.
        text = bridge.CADDY_TEMPLATE.replace("@@ORIGIN@@", r.origin).replace(
            "@@AUTHORITY@@", r.authority).replace("@@REMOTE_SOCKET@@", str(r.socket))
        text = text.replace("{\n", "{\n admin off\n auto_https disable_redirects\n", 1).replace(
            r.origin + " {", r.origin + " {\n bind 127.0.0.1\n"
            f" tls {tls / 'cert.pem'} {tls / 'key.pem'}")
        write(r.runtime / "Caddyfile", text.encode())
        r.context = ssl.create_default_context(cafile=str(tls / "cert.pem"))
        r.container_name = "opencad-private-bridge-" + secrets.token_hex(6)
        result = r.command("caddy-start", [
            "docker", "run", "--detach", "--pull=never", "--name", r.container_name,
            "--network", "host", "--user", f"{os.geteuid()}:{os.getegid()}",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid",
            "--env", "XDG_DATA_HOME=/tmp/data", "--env", "XDG_CONFIG_HOME=/tmp/config",
            "--mount", f"type=bind,src={r.runtime},dst={r.runtime}",
            "--entrypoint", "/usr/bin/caddy", IMAGE,
            "run", "--config", str(r.runtime / "Caddyfile"), "--adapter", "caddyfile"])
        r.container_id = result.stdout.strip().decode()
        bridge.need(len(r.container_id) == 64, "CADDY_CONTAINER_ID_MISMATCH")
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", r.tls_port), timeout=1):
                    break
            except ConnectionRefusedError:
                bridge.need(time.monotonic() < deadline, "CADDY_LISTENER_NOT_READY")
                time.sleep(0.1)

    @classmethod
    def stop_caddy(cls):
        r = cls.r
        if not getattr(r, "container_id", None):
            return
        actual = r.command("caddy-identity", ["docker", "inspect", r.container_id,
                                             "--format", "{{.Name}}"])
        bridge.need(actual.stdout.strip().decode() == "/" + r.container_name,
                    "CADDY_IDENTITY_DRIFT_NO_STOP")
        r.command("caddy-stop", ["docker", "stop", "--timeout", "15", r.container_id])
        r.command("caddy-logs", ["docker", "logs", r.container_id])
        result = r.command("caddy-exit", ["docker", "inspect", r.container_id, "--format",
                                         "{{.State.ExitCode}}"])
        bridge.need(result.stdout.strip() == b"0", "CADDY_PROCESS_EXIT_FAILED")
        # Retain the stopped fixture container and all runtime/evidence.
        r.container_id = None

    def check(self, label, condition):
        self.assertTrue(condition, label)
        self.r.checks.append({"label": label, "at": bridge.now(), "passed": True})

    def expect(self, response, status, error=None):
        actual, _, body = response
        self.assertEqual(actual, status, "HTTP status")
        if error:
            self.assertTrue(isinstance(body, dict) and body.get("ok") is False,
                            "negative response must be false")
            self.assertEqual(body.get("error"), error)

    def test_01_real_tls_authentication(self):
        r = self.r
        self.expect(r.https("anonymous-me"), 401, "missing_token")
        self.expect(r.https("login-missing-origin", "POST", "/api/auth/login",
                           {"username": "alice", "password": r.passwords["alice"]},
                           csrf=False), 403, "cross_site_request")
        for name in ("alice", "bob"):
            status, fields, body = r.https("login-" + name, "POST", "/api/auth/login",
                                          {"username": name, "password": r.passwords[name]})
            self.assertEqual(status, 200)
            self.assertTrue(body.get("ok") is True, "real login")
            values = [value for key, value in fields if key.lower() == "set-cookie"]
            self.assertEqual(len(values), 1)
            self.check("cookie-flags-" + name, all(flag in values[0]
                       for flag in ("Secure", "HttpOnly", "SameSite=Strict")))
            r.cookies[name] = values[0].split(";", 1)[0]
        self.expect(r.https("authenticated-me", user="alice"), 200)
        head = r.https("head-static-transparent", method="HEAD", target="/")
        self.check("head-status-body-length", head[0] == 200 and head[2] == b""
                   and any(name.lower() == "content-length" and int(value) > 0
                           for name, value in head[1]))
        self.check("real-loopback-listener", bridge.listener_owned_by(
            r.secure.pid, r.upstream_port) == r.pin.inode)
        info = r.socket.stat()
        r.events.append({"at": bridge.now(), "code": "FIXTURE_SOCKET_METADATA",
                         "mode": oct(stat.S_IMODE(info.st_mode)), "uid": info.st_uid})
        self.check("private-unix-socket", stat.S_ISSOCK(info.st_mode)
                   and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.geteuid())

    def test_02_host_peer_and_spoofed_headers(self):
        r = self.r
        response = r.call("wrong-host-unix", UnixConnection(r.socket), "GET", "/api/auth/me",
                          headers=[("Host", "evil.invalid"), ("Cookie", r.cookies["alice"])])
        self.expect(response, 403, "BRIDGE_HOST_REFUSED")
        self.expect(r.unix("duplicate-host", extra=[("Host", r.authority)]),
                    403, "BRIDGE_HOST_REFUSED")
        for name in ("Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto",
                     "X-Forwarded-For", "X-Real-IP", "X-Original-URL", "X-Rewrite-URL"):
            self.expect(r.unix("spoof-" + name, user="alice",
                               extra=[(name, "evil.invalid")]), 400, "BRIDGE_FORWARDED_REFUSED")
        response = r.call("wrong-host-tls", http.client.HTTPSConnection(
            "localhost", r.tls_port, context=r.context, timeout=20),
            "GET", "/api/auth/me", headers=[("Host", "evil.invalid")])
        self.assertEqual(response[0], 421)
        response = r.call("wrong-port-authority-tls", http.client.HTTPSConnection(
            "localhost", r.tls_port, context=r.context, timeout=20),
            "GET", "/api/auth/me", headers=[("Host", "localhost:1")])
        self.assertEqual(response[0], 421)
        self.expect(r.https("proxy-strips-spoof-no-auth",
                           extra=[("X-Forwarded-Host", r.authority),
                                  ("X-Forwarded-Proto", "https")]), 401, "missing_token")
        self.expect(r.https("spoof-does-not-fix-csrf", "POST", "/api/auth/login",
                           {"username": "alice", "password": r.passwords["alice"]},
                           csrf=False, extra=[("X-Forwarded-Host", r.authority),
                                              ("X-Forwarded-Proto", "https")]),
                    403, "cross_site_request")
        old = r.server.peer_uid
        try:
            r.server.peer_uid = os.geteuid() + 1
            self.expect(r.unix("wrong-real-peer", user="alice"), 403, "BRIDGE_PEER_UID_REFUSED")
        finally:
            r.server.peer_uid = old
        self.expect(r.unix("connection-cannot-strip-origin", "POST", "/api/auth/login", {},
                           extra=[("Connection", "Origin")]),
                    400, "BRIDGE_CONNECTION_TOKENS_REFUSED")
        self.expect(r.unix("duplicate-cookie", user="alice",
                           extra=[("Cookie", r.cookies["alice"])]), 401, "ambiguous_identity")
        self.expect(r.unix("cookie-bearer-conflict", user="alice",
                           extra=[("Authorization", "Bearer " + r.cookies["alice"].split("=", 1)[1])]),
                    401, "ambiguous_identity")
        self.expect(r.https("wrong-origin-post", "POST", "/api/auth/login",
                           {"username": "alice", "password": r.passwords["alice"]},
                           csrf=False, extra=[("Origin", "https://evil.invalid"),
                                              ("X-OpenCAD-Request", "1")]),
                    403, "cross_site_request")
        self.check("peer-host-spoof-negatives", True)

    def test_03_transparent_project_get_post_and_owner(self):
        r = self.r
        response = r.https("create-private-project", "POST", "/api/projects",
                           {"title": "Bridge fixture", "request_id": secrets.token_hex(16)},
                           user="alice")
        self.expect(response, 201)
        r.head = response[2]
        project = r.head["project_id"]
        self.expect(r.https("other-owner-get", user="bob", target="/api/state", project=project),
                    403, "project_forbidden")
        self.expect(r.https("other-owner-post", "POST", "/api/save", {}, user="bob",
                           project=project), 403, "project_forbidden")
        self.expect(r.https("edit-missing-csrf", "POST", "/api/save", {}, user="alice",
                           project=project, csrf=False), 403, "cross_site_request")
        direct = r.direct("direct-document-before", "/api/document", "alice", project)
        proxied = r.https("tls-document-before", target="/api/document",
                         user="alice", project=project)
        self.check("get-response-byte-equivalence", direct[0] == proxied[0] == 200
                   and direct[2] == proxied[2])
        body = {"expected_revision": r.head["revision"], "expected_sha": r.head["sha256"],
                "request_id": secrets.token_hex(16), "command": "add_line",
                "args": {"p1": [0, 0], "p2": [10000000, 0]}}
        edited = r.https("versioned-post-line", "POST", "/api/command", body,
                        user="alice", project=project)
        self.expect(edited, 200)
        self.check("post-revision-advanced", edited[2]["revision"] == r.head["revision"] + 1)
        r.head = edited[2]
        replay = r.https("same-post-receipt", "POST", "/api/command", body,
                        user="alice", project=project)
        self.check("receipt-response-equivalence", replay[0] == 200 and replay[2] == edited[2])
        save = {"expected_revision": r.head["revision"], "expected_sha": r.head["sha256"],
                "request_id": secrets.token_hex(16)}
        saved = r.https("real-save", "POST", "/api/save", save, user="alice", project=project)
        self.expect(saved, 200)
        r.head = saved[2]
        r.document = r.https("saved-document", target="/api/document",
                             user="alice", project=project)[2]
        state = r.https("saved-state", target="/api/state", user="alice", project=project)
        self.expect(state, 200)
        self.check("saved-document-has-line", state[2]["revision"] == r.head["revision"]
                   and state[2]["sha256"] == r.head["sha256"]
                   and bool(state[2].get("entities")))

    def test_04_readonly_and_ambiguous_framing(self):
        r = self.r
        project = r.head["project_id"]
        try:
            r.server.read_only = True
            self.expect(r.https("readonly-state", target="/api/state",
                               user="alice", project=project), 200)
            self.expect(r.https("readonly-save", "POST", "/api/save", {}, user="alice",
                               project=project), 403, "BRIDGE_READ_ONLY")
            self.expect(r.https("readonly-create", "POST", "/api/projects", {},
                               user="alice"), 403, "BRIDGE_READ_ONLY")
            self.expect(r.https("readonly-login", "POST", "/api/auth/login",
                               {"username": "alice", "password": r.passwords["alice"]}), 200)
        finally:
            r.server.read_only = False
        state = r.https("readonly-left-state-unchanged", target="/api/state",
                        user="alice", project=project)[2]
        self.check("readonly-no-revision-change", state["revision"] == r.head["revision"]
                   and state["sha256"] == r.head["sha256"])
        for label, extra, error in (
            ("duplicate-length", [("Content-Length", "0"), ("Content-Length", "0")],
             "BRIDGE_AMBIGUOUS_LENGTH"),
            ("transfer-encoding", [("Transfer-Encoding", "chunked")],
             "BRIDGE_TRANSFER_ENCODING_REFUSED"),
            ("get-body", [("Content-Length", "1")], "BRIDGE_READ_BODY_REFUSED"),
        ):
            self.expect(r.unix(label, user="alice", extra=extra), 400, error)
        self.check("ambiguous-framing-rejected", True)

    def test_05_pin_negative_and_outage_recovery(self):
        r = self.r
        pin = r.pin
        for field, wrong in (("started", "0"), ("head", "0" * 40), ("inode", "0"),
                             ("tool_hash", "0" * 64)):
            original = getattr(pin, field)
            try:
                setattr(pin, field, wrong)
                self.expect(r.https("wrong-pin-" + field, user="alice"),
                            502, "BRIDGE_UPSTREAM_IDENTITY_FAILED")
            finally:
                setattr(pin, field, original)
        self.stop_bridge()
        self.check("own-socket-removed", not r.socket.exists())
        self.expect(r.https("bridge-down-no-fallback", user="alice"), 502)
        self.start_bridge()
        self.expect(r.https("bridge-restarted-valid-pin", user="alice"), 200)
        self.stop_secure()
        self.expect(r.https("secure-down-no-fallback", user="alice"),
                    502, "BRIDGE_UPSTREAM_IDENTITY_FAILED")
        # A real unrelated listener on the exact old port receives no HTTP secrets.
        fake = socket.socket()
        fake.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        fake.bind(("127.0.0.1", r.upstream_port))
        fake.listen()
        fake.settimeout(0.3)
        try:
            self.expect(r.https("old-port-replacement-refused", user="alice"),
                        502, "BRIDGE_UPSTREAM_IDENTITY_FAILED")
            with self.assertRaises(socket.timeout):
                fake.accept()
            self.check("replacement-received-no-connection", True)
        finally:
            fake.close()
        self.start_secure()
        self.expect(r.https("new-cli-old-pin-refused", user="alice"),
                    502, "BRIDGE_UPSTREAM_IDENTITY_FAILED")
        self.stop_bridge()
        self.start_bridge()
        self.expect(r.https("new-cli-new-pin-restored", user="alice"), 200)
        actual = r.https("reopened-document", target="/api/document", user="alice",
                         project=r.head["project_id"])
        self.check("real-cli-reopen-document-equal", actual[0] == 200
                   and actual[2] == r.document)
        self.expect(r.https("reopened-owner-negative", target="/api/state", user="bob",
                           project=r.head["project_id"]), 403, "project_forbidden")

    def test_06_existing_paths_and_cli_source_guard(self):
        r = self.r
        with self.assertRaisesRegex(bridge.Refused, "EXISTING_SOCKET_REFUSED"):
            bridge.BridgeServer(r.socket, r.pin, os.geteuid())
        with self.assertRaisesRegex(bridge.Refused, "EDGE_NOT_APPROVED"):
            bridge.ssh_argv("evil.invalid", Path("/home/infra/opencad-private-bridge/new/s"),
                            r.socket)
        argv = bridge.ssh_argv("192.168.1.183",
                              Path("/home/infra/opencad-private-bridge/new/s"), r.socket)
        self.check("ssh-plan-fixed-identity-no-execution", argv[-1] == "infra@192.168.1.183"
                   and "StrictHostKeyChecking=yes" in argv
                   and "ExitOnForwardFailure=yes" in argv)
        if r.source["status"]:
            argv = [sys.executable, "-B", str(ROOT / bridge.OWNED[0]), "--action", "serve",
                    "--source-root", str(ROOT), "--expect-head", r.source["head"],
                    "--socket", str(r.runtime / "guard.sock"),
                    "--upstream-pid", str(r.secure.pid), "--upstream-port", str(r.upstream_port),
                    "--data-root", str(r.data), "--public-origin", r.origin]
            child = subprocess.run(argv, capture_output=True, timeout=20)
            write(r.out / "cli-source-guard.stderr.log", child.stderr)
            self.check("dirty-source-cli-refused", child.returncode == 1
                       and b"CLEAN_PINNED_SOURCE_REQUIRED" in child.stderr
                       and not (r.runtime / "guard.sock").exists())
        self.expect(r.https("logout", "POST", "/api/auth/logout", {}, user="alice"), 200)
        self.expect(r.https("old-cookie-revoked", user="alice"), 401, "revoked_token")
        self.check("logout-revocation-transparent", True)

    def test_07_plan_late_read_exception_retains_failure(self):
        r = self.r
        before = {**r.source, "status": ""}
        counter = 0

        def binding(_):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError("fixture late fingerprint read unavailable")
            return before

        out = r.out / "plan-late-read"
        # Fault injection covers only the offline result writer, never the HTTP chain.
        with patch.object(bridge, "source_binding", side_effect=binding):
            with self.assertRaises(OSError):
                bridge.render_plan(out, "192.168.1.183",
                                   Path("/home/infra/opencad-private-bridge/new/s"), r.socket,
                                   ROOT, r.source["head"], r.upstream_port, r.secure.pid,
                                   r.origin, r.data)
        report = json.loads((out / "result.json").read_bytes())
        self.check("plan-late-read-fail-loud", report["verdict"] == "FAIL"
                   and report["source_before"] == before and report["source_after"] is None
                   and report["first_cause"]["type"] == "OSError"
                   and report["remote_commands_executed"] == 0)
        self.check("plan-generated-candidates-not-installed",
                   (out / "Caddyfile").is_file()
                   and (out / "opencad-private-bridge.service").is_file())

    def test_08_terminal_binding_failure_preserves_counts(self):
        for variant in ("late-read", "head-only", "byte-drift"):
            report = {"verdict": "PASS", "rc": 0, "source_before": self.r.source,
                      "tests": 6, "passed": 6, "first_cause": {"code": "original"},
                      "source_after": None, "source_drift": None}

            def reader():
                if variant == "late-read":
                    raise OSError("fixture final read unavailable")
                if variant == "head-only":
                    return {**self.r.source, "head": "0" * 40}
                return {**self.r.source, "files": {}}

            finalize_binding(report, reader)
            self.check("terminal-" + variant, report["verdict"] == "FAIL"
                       and report["rc"] == 1 and report["tests"] == 6 and report["passed"] == 6
                       and report["first_cause"] == {"code": "original"}
                       and bool(report["source_drift"])
                       and (variant != "late-read" or report["source_after"] is None))


def finalize_binding(report, reader):
    try:
        report["source_after"] = reader()
        report["source_drift"] = [] if report["source_after"] == report["source_before"] else [
            "SOURCE_BINDING_CHANGED"]
    except BaseException as exc:
        report["source_after"], report["source_drift"] = None, ["UNKNOWN_NOT_VERIFIED"]
        report["first_cause"] = report["first_cause"] or bridge.failure(exc)
        report["after_read_error"] = bridge.failure(exc)
        sys.stderr.write("PRIVATE_BRIDGE_AFTER_READ_FAILURE " +
                         json.dumps(report["after_read_error"]) + "\n")
    if report["source_drift"]:
        report["rc"], report["verdict"] = 1, "FAIL"
        report["first_cause"] = report["first_cause"] or {"code": "FINAL_SOURCE_DRIFT"}


def verify_evidence(evidence, out):
    report = {"verdict": "FAIL", "rc": 1, "at": bridge.now(), "first_cause": None,
              "source_before": None, "source_after": None, "source_drift": None,
              "tests": 0, "passed": 0, "checks": 0, "production_ready": False}
    try:
        before = bridge.source_binding(ROOT)
        report["source_before"] = before
        original = json.loads((evidence / "result.json").read_bytes())
        bridge.need(original["verdict"] == "PASS" and original["rc"] == 0
                    and original["failures"] == original["errors"] == original["skips"] == 0,
                    "ORIGINAL_SUITE_NOT_PASS")
        bridge.need(original["source_before"] == original["source_after"] == before,
                    "EVIDENCE_SOURCE_NOT_CURRENT")
        for name, sha in original["artifacts"].items():
            bridge.need(Path(name).name == name, "UNSAFE_ARTIFACT_NAME")
            bridge.need(bridge.digest((evidence / name).read_bytes()) == sha,
                        "EVIDENCE_ARTIFACT_SHA_MISMATCH")
            report["checks"] += 1
        http = json.loads((evidence / "http.json").read_bytes())
        checks = json.loads((evidence / "checks.json").read_bytes())
        commands = json.loads((evidence / "commands.json").read_bytes())
        bridge.need(len(http) == original["http_requests"]
                    and [row["sequence"] for row in http] == list(range(1, len(http) + 1))
                    and len({row["label"] for row in http}) == len(http), "HTTP_LEDGER_MISMATCH")
        bridge.need(len(checks) == original["checks"] and checks
                    and all(row["passed"] is True for row in checks), "CHECK_LEDGER_MISMATCH")
        bridge.need(commands and all(row["actual_rc"] == 0 for row in commands),
                    "CHILD_EXIT_NOT_ZERO")
        bridge.need(original["tests"] == original["passed"]
                    and original["tests"] in (1, 8), "TEST_COUNT_NOT_RECONCILED")
        tests = (evidence / "tests.log").read_text()
        bridge.need(f"Ran {original['tests']} tests" in tests and tests.endswith("\nOK\n"),
                    "TEST_COMPLETION_MISSING")
        report.update(verdict="PASS", rc=0, tests=original["tests"], passed=original["passed"],
                      checks=report["checks"] + 4, http_requests=len(http),
                      evidence_root=str(evidence), result_sha256=bridge.digest(
                          (evidence / "result.json").read_bytes()))
    except BaseException as exc:
        report["first_cause"] = bridge.failure(exc)
        sys.stderr.write("PRIVATE_BRIDGE_READBACK_FAILURE " +
                         json.dumps(report["first_cause"]) + "\n")
    finalize_binding(report, lambda: bridge.source_binding(ROOT))
    report["completion_marker"] = f"PRIVATE_BRIDGE_READBACK verdict={report['verdict']} rc={report['rc']}"
    write(out / "result.json", report)
    print(report["completion_marker"] + " result=" + str(out / "result.json"), flush=True)
    return report["rc"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--case", choices=("auth", "all"), default="all")
    parser.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args()
    bridge.need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
    out = fresh(args.evidence_dir, Path("/hf3fs-jd/data/tmp"))
    if args.verify_evidence:
        return verify_evidence(args.verify_evidence, out)
    report = {"verdict": "FAIL", "rc": 1, "started_at": bridge.now(), "source_before": None,
              "source_after": None, "source_drift": None, "tests": 0, "passed": 0,
              "failures": 0, "errors": 0, "skips": 0, "first_cause": None,
              "production_ready": False, "remote_ssh_tested": False}
    global STATE
    STATE = Run(out, args.runtime_root)
    try:
        STATE.source = bridge.source_binding(ROOT)
        report["source_before"] = STATE.source
        bridge.need(args.runtime_root is not None, "RUNTIME_ROOT_REQUIRED")
        fresh(bridge.literal_path(args.runtime_root), Path("/data/tmp"))
        names = ["test_01_real_tls_authentication"] if args.case == "auth" else sorted(
            name for name in dir(BridgeTests) if name.startswith("test_"))
        suite = unittest.TestSuite(BridgeTests(name) for name in names)
        with (out / "tests.log").open("x") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2, failfast=True).run(suite)
        report.update(tests=result.testsRun, failures=len(result.failures),
                      errors=len(result.errors), skips=len(result.skipped),
                      passed=max(0, result.testsRun - len(result.failures) - len(result.errors)
                                 - len(result.skipped)))
        if result.failures or result.errors:
            report["first_cause"] = {"test": str((result.failures + result.errors)[0][0]),
                                     "log": str(out / "tests.log")}
        bridge.need(result.wasSuccessful() and result.testsRun == len(names),
                    "REAL_NETWORK_SUITE_FAILED")
        report["verdict"], report["rc"] = "PASS", 0
    except BaseException as exc:
        report["first_cause"] = report["first_cause"] or bridge.failure(exc)
        sys.stderr.write("PRIVATE_BRIDGE_TEST_FAILURE " +
                         json.dumps(report["first_cause"]) + "\n")
    finally:
        finalize_binding(report, lambda: bridge.source_binding(ROOT))
        report.update(ended_at=bridge.now(), checks=len(STATE.checks),
                      http_requests=len(STATE.calls), runtime_root=str(args.runtime_root),
                      caddy_image=IMAGE, fixture_bridge="REAL_BridgeServer_NOT_CLI_OVERRIDE",
                      clean_source_cli_adoption=not bool(STATE.source and STATE.source["status"]))
        for name, data in (("http.json", STATE.calls), ("commands.json", STATE.commands),
                           ("events.json", STATE.events), ("checks.json", STATE.checks)):
            write(out / name, data)
        report["artifacts"] = {path.name: bridge.digest(path.read_bytes())
                               for path in out.iterdir() if path.is_file()}
        report["completion_marker"] = (
            f"PRIVATE_BRIDGE_TEST verdict={report['verdict']} tests={report['tests']} "
            f"passed={report['passed']} http={report['http_requests']} rc={report['rc']}")
        write(out / "result.json", report)
        print(report["completion_marker"] + " result=" + str(out / "result.json"), flush=True)
    return report["rc"]


if __name__ == "__main__":
    old_mask = os.umask(0o077)
    try:
        raise SystemExit(main())
    finally:
        os.umask(old_mask)
