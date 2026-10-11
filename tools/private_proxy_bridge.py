#!/usr/bin/env python3
"""Peer-bound Unix HTTP bridge and offline deployment plan, never a cutover.

Only an explicitly pinned secure CLI process on loopback is forwarded to.
Public TLS and SSH are separate trust boundaries; forwarded headers confer no
identity. Plans create only a new external child and never execute SSH.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EDGES = ("192.168.1.183", "192.168.1.184")
OWNED = ("tools/private_proxy_bridge.py", "tools/test_private_proxy_bridge.py",
         "docs/design/private-proxy-bridge.md")
MAX_BODY = 16 * 1024 * 1024
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection"}
CADDY_TEMPLATE = """# Candidate only: merge into the reviewed existing site.
{
    servers {
        strict_sni_host on
    }
}
@@ORIGIN@@ {
    @wrong_authority expression `{http.request.hostport} != "@@AUTHORITY@@"`
    respond @wrong_authority "Misdirected Request" 421
    reverse_proxy unix/@@REMOTE_SOCKET@@ {
        header_up Host @@AUTHORITY@@
        header_up -Forwarded
        header_up -X-Forwarded-For
        header_up -X-Forwarded-Host
        header_up -X-Forwarded-Proto
        header_up -X-Real-IP
        header_up -X-Original-URL
        header_up -X-Rewrite-URL
        lb_try_duration 0s
    }
}
"""
SERVICE_TEMPLATE = """# Candidate only; renew the pin explicitly after CLI restart.
[Unit]
Description=OpenCAD private peer-bound Unix bridge
After=network-online.target

[Service]
Type=simple
User=infra
UMask=0177
ExecStart=/usr/bin/python3 -B @@SOURCE@@/tools/private_proxy_bridge.py --action serve --source-root @@SOURCE@@ --expect-head @@HEAD@@ --socket @@LOCAL_SOCKET@@ --upstream-pid @@PID@@ --upstream-port @@PORT@@ --data-root @@DATA_ROOT@@ --public-origin @@ORIGIN@@ --peer-uid @@UID@@
Restart=no
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@@LOCAL_PARENT@@
RestrictAddressFamilies=AF_UNIX AF_INET
"""


class Refused(RuntimeError):
    pass


def need(condition, code):
    if not condition:
        raise Refused(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def checked(argv):
    child = subprocess.run(argv, capture_output=True)
    if child.returncode:
        sys.stderr.buffer.write(child.stderr)
        sys.stderr.flush()
        exc = Refused("LOCAL_COMMAND_RC_" + str(child.returncode))
        exc.actual_rc = child.returncode
        raise exc
    return child


def failure(exc):
    return {"type": type(exc).__name__,
            "code": str(exc) if isinstance(exc, Refused) else type(exc).__name__,
            "actual_rc": getattr(exc, "actual_rc", None),
            "coordinates": [{"file": frame.filename, "line": frame.lineno,
                             "function": frame.name}
                            for frame in traceback.extract_tb(exc.__traceback__)]}


def source_binding(source):
    names = checked(["git", "-C", str(source), "ls-files", "-z"]).stdout.split(b"\0")
    files = {name.decode(): digest((source / name.decode()).read_bytes())
             for name in names if name}
    for name in OWNED:
        files[name] = digest((source / name).read_bytes())
    return {"root": str(source),
            "head": checked(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.decode().strip(),
            "status": checked(["git", "-C", str(source), "status", "--porcelain=v1"]).stdout.decode(),
            "files": files}


def literal_path(value):
    path = Path(value)
    need(path.is_absolute() and ".." not in path.parts
         and re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)), "UNSAFE_PATH")
    for part in (path, *path.parents):
        need(not part.is_symlink(), "SYMLINK_PATH_REFUSED")
    return path


def private_parent(path):
    parent = literal_path(path).parent
    info = parent.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and not info.st_mode & 0o077, "PRIVATE_OWNED_PARENT_REQUIRED")
    need(len(os.fsencode(path)) < 104, "UNIX_SOCKET_PATH_TOO_LONG")
    return parent


def origin_authority(origin):
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError as exc:
        raise Refused("INVALID_HTTPS_ORIGIN") from exc
    need(parts.scheme == "https" and parts.hostname and not parts.username
         and not parts.password and not parts.path and not parts.query
         and not parts.fragment and not any(c.isspace() for c in origin)
         and not parts.netloc.endswith(":") and (port is None or port > 0),
         "EXACT_HTTPS_ORIGIN_REQUIRED")
    need(re.fullmatch(r"[A-Za-z0-9.:-]+", parts.netloc), "INVALID_ORIGIN_AUTHORITY")
    return parts.netloc


def process_start(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return raw[raw.rfind(")") + 2:].split()[19]


def listener_owned_by(pid, port):
    info = Path(f"/proc/{pid}").stat()
    need(info.st_uid == os.geteuid(), "UPSTREAM_UID_MISMATCH")
    inodes = set()
    for path in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    rows = Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]
    matches = [row.split() for row in rows
               if row.split()[1] == f"0100007F:{port:04X}"
               and row.split()[3] == "0A"]
    need(len(matches) == 1 and matches[0][9] in inodes, "PINNED_LOOPBACK_LISTENER_REQUIRED")
    return matches[0][9]


def connection_owned_by(pid, connection):
    """Check the accepted server socket before sending any HTTP credentials."""
    local_port = connection.sock.getsockname()[1]
    remote_port = connection.sock.getpeername()[1]
    deadline = time.monotonic() + 2
    while True:
        rows = Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]
        candidates = [row.split()[9] for row in rows
                      if row.split()[1] == f"0100007F:{remote_port:04X}"
                      and row.split()[2] == f"0100007F:{local_port:04X}"
                      and row.split()[3] == "01" and row.split()[9] != "0"]
        sockets = set()
        for path in Path(f"/proc/{pid}/fd").iterdir():
            try:
                sockets.add(os.readlink(path))
            except FileNotFoundError:
                continue
        if any("socket:[" + inode + "]" in sockets for inode in candidates):
            return
        need(time.monotonic() < deadline, "UPSTREAM_ACCEPTED_SOCKET_NOT_OWNED")
        time.sleep(0.01)


class UpstreamPin:
    def __init__(self, source, head, pid, port, origin, data_root):
        self.source = literal_path(source)
        self.head, self.pid, self.port = head, pid, port
        self.origin, self.data_root = origin, literal_path(data_root)
        need(re.fullmatch(r"[0-9a-f]{40}", head) and pid > 1
             and 0 < port <= 65535, "INVALID_UPSTREAM_PIN")
        self.authority = origin_authority(origin)
        app = self.source / "oss-alternatives/apps/opencad"
        self.cli = app / "secure_web.py"
        self.binding = source_binding(self.source)
        self.tool_hash = digest(Path(__file__).read_bytes())
        self.started = process_start(pid)
        self.inode = listener_owned_by(pid, port)
        self.validate()

    def validate(self):
        need(process_start(self.pid) == self.started, "UPSTREAM_PROCESS_REPLACED")
        argv = Path(f"/proc/{self.pid}/cmdline").read_bytes().split(b"\0")
        expected = [str(self.cli), "--host", "127.0.0.1", "--port", str(self.port),
                    "--public-origin", self.origin, "--data-root", str(self.data_root)]
        args = [item.decode() for item in argv if item]
        need(len(args) == len(expected) + 2 and args[1] == "-B"
             and args[2:] == expected, "UPSTREAM_SECURE_CLI_ARGUMENTS_MISMATCH")
        need(Path(f"/proc/{self.pid}/exe").resolve() == Path(sys.executable).resolve(),
             "UPSTREAM_INTERPRETER_MISMATCH")
        need(listener_owned_by(self.pid, self.port) == self.inode, "UPSTREAM_LISTENER_REPLACED")
        binding = source_binding(self.source)
        need(binding["head"] == self.head, "SOURCE_HEAD_DRIFT")
        need(binding == self.binding and digest(Path(__file__).read_bytes()) == self.tool_hash,
             "UPSTREAM_SOURCE_DRIFT")


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version, sys_version = "OpenCADPrivateBridge", ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def reply_error(self, status, code):
        raw = encode({"ok": False, "error": code})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _forward(self):
        self.close_connection = True
        try:
            _, uid, _ = struct.unpack("3i", self.connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            need(uid == self.server.peer_uid, "BRIDGE_PEER_UID_REFUSED")
            need(self.headers.get_all("Host", []) == [self.server.pin.authority],
                 "BRIDGE_HOST_REFUSED")
            need(self.path.startswith("/") and not self.path.startswith("//")
                 and "#" not in self.path
                 and not any(ord(c) < 32 for c in self.path), "BRIDGE_TARGET_REFUSED")
            tokens = {part.strip().lower()
                      for value in self.headers.get_all("Connection", [])
                      for part in value.split(",")}
            need(tokens <= {"close", "keep-alive"}, "BRIDGE_CONNECTION_TOKENS_REFUSED")
            need(not any(name.lower() == "forwarded"
                         or name.lower().startswith("x-forwarded-")
                         or name.lower() in {"x-real-ip", "x-original-url", "x-rewrite-url"}
                         for name in self.headers), "BRIDGE_FORWARDED_REFUSED")
            need(not self.headers.get_all("Transfer-Encoding", []),
                 "BRIDGE_TRANSFER_ENCODING_REFUSED")
            lengths = self.headers.get_all("Content-Length", [])
            need(len(lengths) <= 1, "BRIDGE_AMBIGUOUS_LENGTH")
            length = lengths[0] if lengths else "0"
            need(length.isascii() and length.isdecimal(), "BRIDGE_INVALID_LENGTH")
            length = int(length)
            need(length <= MAX_BODY, "BRIDGE_BODY_LIMIT")
            need(self.command == "POST" or length == 0, "BRIDGE_READ_BODY_REFUSED")
            if self.server.read_only and self.command == "POST":
                need(self.path in {"/api/auth/login", "/api/auth/logout"}, "BRIDGE_READ_ONLY")
            self.server.pin.validate()
            body = self.rfile.read(length)
            need(len(body) == length, "BRIDGE_INCOMPLETE_BODY")
        except Refused as exc:
            code = str(exc)
            status = 403 if code in {"BRIDGE_PEER_UID_REFUSED", "BRIDGE_HOST_REFUSED",
                                    "BRIDGE_READ_ONLY"} else 400
            self.server.record(code, type(exc).__name__)
            if not code.startswith("BRIDGE_"):
                status, code = 502, "BRIDGE_UPSTREAM_IDENTITY_FAILED"
            return self.reply_error(status, code)
        except Exception as exc:
            self.server.record("BRIDGE_UPSTREAM_PIN_FAILED", type(exc).__name__)
            return self.reply_error(502, "BRIDGE_UPSTREAM_IDENTITY_FAILED")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.pin.port, timeout=15)
        try:
            connection.connect()
            self.server.pin.validate()
            connection_owned_by(self.server.pin.pid, connection)
            self.server.pin.validate()
            connection.putrequest(self.command, self.path, skip_host=True,
                                  skip_accept_encoding=True)
            for name, value in self.headers.raw_items():
                lower = name.lower()
                if (lower in HOP | tokens or lower == "forwarded"
                        or lower.startswith("x-forwarded-")
                        or lower in {"x-real-ip", "x-original-url", "x-rewrite-url"}):
                    continue
                connection.putheader(name, value)
            connection.putheader("Connection", "close")
            connection.endheaders(body)
            response = connection.getresponse()
            raw = response.read(MAX_BODY + 1)
            need(len(raw) <= MAX_BODY, "BRIDGE_RESPONSE_LIMIT")
            need(not response.getheader("Transfer-Encoding"), "BRIDGE_RESPONSE_FRAMING")
            advertised = response.getheaders()
            sizes = [value for name, value in advertised if name.lower() == "content-length"]
            need(len(sizes) == 1 and sizes[0].isdecimal()
                 and (self.command == "HEAD" or int(sizes[0]) == len(raw)),
                 "BRIDGE_RESPONSE_LENGTH")
            self.server.pin.validate()
        except Exception as exc:
            self.server.record(str(exc) if isinstance(exc, Refused)
                               else "BRIDGE_UPSTREAM_FAILED", type(exc).__name__)
            return self.reply_error(502, "BRIDGE_UPSTREAM_FAILED")
        finally:
            connection.close()
        self.send_response(response.status)
        for name, value in advertised:
            if name.lower() not in HOP | {"server", "date", "content-length"}:
                self.send_header(name, value)
        self.send_header("Content-Length", sizes[0])
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    do_GET = do_HEAD = do_POST = _forward


class BridgeServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, path, pin, peer_uid, *, read_only=False, events=None):
        path = literal_path(path)
        private_parent(path)
        need(not path.exists(), "EXISTING_SOCKET_REFUSED")
        need(type(peer_uid) is int and peer_uid >= 0, "INVALID_PEER_UID")
        self.pin, self.peer_uid, self.read_only = pin, peer_uid, read_only
        self.path, self.identity = path, None
        self.events = events
        self.slots = threading.BoundedSemaphore(32)
        mask = os.umask(0o177)
        try:
            super().__init__(str(path), BridgeHandler)
        finally:
            os.umask(mask)
        info = path.stat()
        self.identity = (info.st_dev, info.st_ino)

    def record(self, code, exception_type=None):
        event = {"at": now(), "code": code, "exception_type": exception_type}
        if self.events is not None:
            self.events(event)
        sys.stderr.write("PRIVATE_BRIDGE_FAILURE " + json.dumps(event) + "\n")
        sys.stderr.flush()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            self.record("BRIDGE_CAPACITY_REFUSED")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_close(self):
        super().server_close()
        if self.identity is not None and self.path.exists():
            info = self.path.lstat()
            need(stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.identity,
                 "SOCKET_REPLACED_NO_CLEANUP")
            self.path.unlink()


def ssh_argv(edge, remote_socket, local_socket):
    need(edge in EDGES, "EDGE_NOT_APPROVED")
    literal_path(remote_socket)
    literal_path(local_socket)
    need(str(remote_socket).startswith("/home/infra/opencad-private-bridge/")
         and len(os.fsencode(remote_socket)) < 104, "REMOTE_PRIVATE_SOCKET_REQUIRED")
    return ["/usr/bin/ssh", "-N", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
            "-o", "ExitOnForwardFailure=yes", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2", "-R",
            f"{remote_socket}:{local_socket}", "infra@" + edge]


def render_plan(out, edge, remote_socket, local_socket, source, head,
                port, pid, origin, data_root):
    need(out.is_absolute() and ".." not in out.parts
         and out != Path("/hf3fs-jd/data/tmp")
         and out.is_relative_to(Path("/hf3fs-jd/data/tmp")),
         "NEW_EXTERNAL_EVIDENCE_REQUIRED")
    need(not out.exists(), "EXISTING_OUTPUT_REFUSED")
    out.mkdir(mode=0o700)
    report = {"verdict": "FAIL", "at": now(), "first_cause": None,
              "source_before": None, "source_after": None,
              "remote_commands_executed": 0, "production_ready": False}
    try:
        return _render_plan(out, edge, remote_socket, local_socket, source, head,
                            port, pid, origin, data_root, report)
    except Exception as exc:
        report["verdict"] = "FAIL"
        report["first_cause"] = failure(exc)
        (out / "result.json").write_bytes(encode(report))
        raise


def _render_plan(out, edge, remote_socket, local_socket, source, head,
                 port, pid, origin, data_root, report):
    source, data_root = literal_path(source), literal_path(data_root)
    need(re.fullmatch(r"[0-9a-f]{40}", head) and type(pid) is int and pid > 1
         and type(port) is int and 0 < port <= 65535, "INVALID_UPSTREAM_PIN")
    argv = ssh_argv(edge, remote_socket, local_socket)
    authority = origin_authority(origin)
    binding = source_binding(source)
    report["source_before"] = binding
    need(binding["head"] == head and not binding["status"], "CLEAN_PINNED_SOURCE_REQUIRED")
    values = {"ORIGIN": origin, "AUTHORITY": authority,
              "REMOTE_SOCKET": str(remote_socket), "LOCAL_SOCKET": str(local_socket),
              "LOCAL_PARENT": str(local_socket.parent),
              "SOURCE": str(source), "HEAD": head, "PORT": str(port), "PID": str(pid),
              "DATA_ROOT": str(data_root), "UID": str(os.geteuid())}
    for name, text in (("Caddyfile", CADDY_TEMPLATE),
                       ("opencad-private-bridge.service", SERVICE_TEMPLATE)):
        for key, value in values.items():
            text = text.replace("@@" + key + "@@", value)
        need("@@" not in text, "UNRESOLVED_TEMPLATE")
        with (out / name).open("x") as handle:
            handle.write(text)
    report.update({"verdict": "PLANNED", "at": now(), "ssh_argv": argv,
              "source_before": binding,
              "remote_commands_executed": 0, "production_ready": False,
              "conditions": ["existing strict infra SSH authentication",
                             "server AllowStreamLocalForwarding and socket mode 0600",
                             "new edge parent owned infra mode 0700",
                             "Caddy root or same explicitly approved UID",
                             "same source and exact secure CLI PID/start/listener",
                             "no retry/fallback/legacy address", "separate cutover authorization"]})
    report["source_after"] = source_binding(source)
    need(report["source_before"] == report["source_after"], "PLAN_SOURCE_DRIFT")
    (out / "result.json").write_bytes(encode(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--action", choices=("serve", "plan"), required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expect-head", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--upstream-pid", type=int, required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--peer-uid", type=int, default=os.geteuid())
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--edge", choices=EDGES)
    parser.add_argument("--remote-socket", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
    if args.action == "plan":
        need(args.out is not None and args.edge is not None
             and args.remote_socket is not None, "PLAN_COORDINATES_REQUIRED")
        report = render_plan(args.out, args.edge, args.remote_socket, args.socket,
                             args.source_root, args.expect_head, args.upstream_port,
                             args.upstream_pid, args.public_origin, args.data_root)
        print("PRIVATE_BRIDGE verdict=PLANNED remote_commands=0 result=" +
              str(args.out / "result.json"), flush=True)
        return 0
    binding = source_binding(args.source_root)
    need(binding["head"] == args.expect_head and not binding["status"],
         "CLEAN_PINNED_SOURCE_REQUIRED")
    pin = UpstreamPin(args.source_root, args.expect_head, args.upstream_pid,
                      args.upstream_port, args.public_origin, args.data_root)
    server = BridgeServer(args.socket, pin, args.peer_uid, read_only=args.read_only)
    stop = threading.Event()
    for signame in ("SIGTERM", "SIGINT"):
        signal.signal(getattr(signal, signame), lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("PRIVATE_BRIDGE verdict=READY socket=" + str(args.socket), flush=True)
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        need(not thread.is_alive(), "BRIDGE_SHUTDOWN_INCOMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("PRIVATE_BRIDGE verdict=FAIL cause=" +
              (str(exc) if isinstance(exc, Refused) else type(exc).__name__),
              file=sys.stderr, flush=True)
        raise SystemExit(1)
