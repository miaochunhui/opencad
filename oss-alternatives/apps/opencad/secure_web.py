"""HTTP boundary for private OpenCAD projects, separate from the legacy demo.

The application supplied to ``build_server`` owns authentication, authorization
and durable transactions. No request falls back to the shared demo workspace.
Only static browser assets are public. Runtime state belongs outside the repo.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

if __name__ == "__main__":
    # Gateway imports must share these exact Request/Reply/HttpError classes.
    sys.modules["secure_web"] = sys.modules[__name__]

MAX_BODY = 16 * 1024 * 1024
COOKIE_NAME = "opencad_session"
APP_DIR = Path(__file__).resolve().parent
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
PRIVATE_ASSETS = (
    '<script src="/web/js/project-session.js"></script>'
    '<link rel="stylesheet" href="/web/css/project-session.css">'
)
INDUSTRIAL_ASSETS = "".join(
    f'<link rel="stylesheet" href="/web/css/{name}-panel.css">'
    f'<script src="/web/js/{name}-panel.js" defer></script>'
    for name in ("parametric", "surfaces", "assembly", "drawing", "render")
) + '<script src="/web/js/industrial-panels.js" defer></script>'


def private_3d_page():
    from viewer3d_page import render
    panels = '<section class="industrial-workbench" aria-label="工业建模工作区">'
    for name, caption in (("parametric", "参数化重建"), ("surfaces", "精确曲面"),
                          ("assembly", "真实装配"), ("drawing", "关联工程图"),
                          ("render", "服务端渲染")):
        panels += (f'<details><summary>{caption}</summary>'
                   f'<div data-{name}-host></div></details>')
    panels += "</section>"
    return (render(advanced=True)
            .replace("<head>", "<head>" + PRIVATE_ASSETS + INDUSTRIAL_ASSETS, 1)
            .replace("<footer>", panels + "<footer>", 1))


class HttpError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


@dataclass
class Reply:
    status: int = 200
    payload: object = field(default_factory=dict)
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    payload: dict
    token: str | None
    project_id: str | None
    cookie_auth: bool


def session_cookie(token: str, *, secure: bool, clear: bool = False) -> str:
    if not clear and (not token or not token.isascii() or not token.isalnum()):
        raise ValueError("session token must be nonempty ASCII alphanumeric")
    cookie = SimpleCookie()
    cookie[COOKIE_NAME] = "" if clear else token
    item = cookie[COOKIE_NAME]
    item["path"], item["httponly"], item["samesite"] = "/", True, "Strict"
    if secure:
        item["secure"] = True
    if clear:
        item["max-age"] = 0
    return item.OutputString()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version, sys_version = "OpenCAD", ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True
            # A disconnected browser cannot receive a reply. No edit is retried.
            if self.server.verbose:
                sys.stderr.write("opencad-http: peer disconnected\n")

    def log_message(self, fmt, *args):
        # Do not log query strings, credentials or JSON request bodies.
        if self.server.verbose:
            sys.stderr.write("opencad-http: " + self.command + " "
                             + urlsplit(self.path).path + "\n")

    def _reply(self, result: Reply):
        if isinstance(result.payload, bytes):
            data = result.payload
        else:
            data = json.dumps(result.payload, ensure_ascii=False, sort_keys=True,
                              allow_nan=False).encode("utf-8")
        self.send_response(result.status)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        if self.close_connection:
            self.send_header("Connection", "close")
        for name, value in result.headers.items():
            if name.lower() in {"content-length", "transfer-encoding"}:
                raise ValueError("application cannot override framing headers")
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _body(self):
        if self.headers.get_all("Transfer-Encoding", []):
            self.close_connection = True
            raise HttpError("unsupported_transfer_encoding", "Use Content-Length")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            self.close_connection = True
            raise HttpError("ambiguous_length", "Only one Content-Length is accepted")
        raw_length = lengths[0] if lengths else "0"
        if not raw_length.isascii() or not raw_length.isdecimal():
            self.close_connection = True
            raise HttpError("invalid_length", "Content-Length must be a decimal integer")
        length = int(raw_length)
        if length > MAX_BODY:
            self.close_connection = True
            raise HttpError("body_too_large", "JSON body exceeds 16 MiB", 413)
        if not length:
            return {}
        if self.headers.get_content_type() != "application/json":
            self.close_connection = True
            raise HttpError("unsupported_media_type", "Use application/json", 415)
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, socket.timeout) as exc:
            self.close_connection = True
            raise HttpError("body_timeout", "Request body did not arrive", 408) from exc
        if len(raw) != length:
            self.close_connection = True
            raise HttpError("incomplete_body", "Request body was truncated")

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        def invalid_constant(value):
            raise ValueError("nonfinite JSON number")

        try:
            value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise HttpError("bad_json_body", "Invalid JSON object") from exc
        if not isinstance(value, dict):
            raise HttpError("bad_json_body", "JSON body must be an object")
        return value

    def _identity(self):
        authorizations = self.headers.get_all("Authorization", [])
        if len(authorizations) > 1:
            raise HttpError("ambiguous_identity", "Only one authorization header is accepted", 401)
        cookie_headers = self.headers.get_all("Cookie", [])
        session_fields = [
            item for header in cookie_headers for item in header.split(";")
            if item.partition("=")[0].strip() == COOKIE_NAME
        ]
        if len(cookie_headers) > 1 or len(session_fields) > 1:
            raise HttpError("ambiguous_identity", "Only one session cookie is accepted", 401)
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError as exc:
            raise HttpError("invalid_cookie", "Invalid session cookie", 401) from exc
        cookie = cookies.get(COOKIE_NAME)
        authorization = authorizations[0] if authorizations else None
        if authorization is not None:
            if cookie is not None:
                raise HttpError("ambiguous_identity", "Do not combine cookie and bearer sessions", 401)
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token or " " in token:
                raise HttpError("invalid_authorization", "Use a bearer session", 401)
            return token, False
        return (cookie.value, True) if cookie is not None else (None, False)

    def _csrf(self, path: str, cookie_auth: bool):
        if self.command != "POST" or not (cookie_auth or path == "/api/auth/login"):
            return
        if (self.headers.get_all("Origin", []) != [self.server.public_origin]
                or self.headers.get_all("X-OpenCAD-Request", []) != ["1"]):
            raise HttpError("cross_site_request", "A same-origin browser request is required", 403)

    def _static(self, path: str):
        if path == "/web/3d.html":
            return Reply(payload=private_3d_page().encode("utf-8"),
                         content_type="text/html; charset=utf-8")
        if path in {"/", "/index.html"}:
            relative = "index.html"
        elif path.startswith("/web/"):
            relative = path[5:]
        else:
            return None
        root = self.server.web_root
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HttpError("not_found", "Static asset does not exist", 404)
        media = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        data = candidate.read_bytes()
        if relative == "index.html":
            data = data.replace(b"<head>", b"<head>" + PRIVATE_ASSETS.encode(), 1)
        return Reply(payload=data, content_type=media)

    def _handle(self):
        try:
            parts = urlsplit(self.path)
            if self.command != "POST":
                lengths = self.headers.get_all("Content-Length", [])
                if (self.headers.get_all("Transfer-Encoding", [])
                        or len(lengths) > 1 or (lengths and lengths != ["0"])):
                    self.close_connection = True
                    raise HttpError("unexpected_body", "Read requests cannot contain a body")
            if self.command in {"GET", "HEAD"}:
                result = self._static(parts.path)
                if result is not None:
                    self._reply(result)
                    return
            token, cookie_auth = self._identity()
            self._csrf(parts.path, cookie_auth)
            projects = self.headers.get_all("X-OpenCAD-Project", [])
            if len(projects) > 1:
                raise HttpError("ambiguous_project", "Only one project header is accepted")
            try:
                query = (parse_qs(parts.query, keep_blank_values=True, strict_parsing=True,
                                  errors="strict", max_num_fields=64) if parts.query else {})
            except (ValueError, UnicodeError) as exc:
                raise HttpError("bad_query", "Invalid or oversized query fields") from exc
            request = Request(
                self.command, parts.path, query,
                self._body() if self.command == "POST" else {}, token,
                projects[0] if projects else None, cookie_auth,
            )
            self._reply(self.server.application.handle(request))
        except HttpError as exc:
            # Early identity/CSRF failures may leave the POST body unread.
            # Closing prevents those bytes from becoming another HTTP request.
            if self.command == "POST":
                self.close_connection = True
            self._reply(Reply(exc.status, {"ok": False, "error": exc.code, "detail": exc.detail}))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            # Preserve the server-side cause, but not request bodies or secrets.
            import traceback
            traceback.print_exc()
            self.close_connection = True
            self._reply(Reply(500, {"ok": False, "error": "internal_error",
                                   "detail": "The request was not confirmed"}))

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()


class BoundedServer(ThreadingHTTPServer):
    """Bound memory and thread occupancy; excess connections receive HTTP 503."""

    def __init__(self, *args, max_connections=32, **kwargs):
        self.slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
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


def validated_origin(origin):
    if not isinstance(origin, str) or not origin or any(char.isspace() for char in origin):
        raise ValueError("public_origin must be a nonempty HTTP(S) origin without whitespace")
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError as exc:
        raise ValueError("public_origin has an invalid authority or port") from exc
    if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment or parts.path
            or parts.netloc.endswith(":") or (port is not None and port < 1)):
        raise ValueError("public_origin must be an exact HTTP(S) origin without a path")
    if parts.scheme != "https" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Public private-project services require an HTTPS origin")
    return origin


def build_server(application, *, host="127.0.0.1", port=0, public_origin=None,
                 web_root=None, verbose=False):
    origin = validated_origin(public_origin) if public_origin is not None else None
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError as exc:
        raise ValueError("Private HTTP upstream requires a literal loopback host") from exc
    if not loopback:
        raise ValueError("Private HTTP upstream must stay on loopback behind a TLS proxy")
    httpd = BoundedServer((host, port), Handler)
    if not ipaddress.ip_address(httpd.server_address[0]).is_loopback:
        httpd.server_close()
        raise ValueError("Private HTTP upstream resolved outside loopback")
    # server_close must drain admitted requests before their databases close.
    httpd.daemon_threads = False
    httpd.block_on_close = True
    httpd.application = application
    httpd.verbose = verbose
    httpd.web_root = Path(web_root or APP_DIR / "web").resolve()
    origin = origin or f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.public_origin = origin
    httpd.secure_cookies = urlsplit(origin).scheme == "https"
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenCAD private-project Web service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--enable-gpu", action="store_true",
                        help="explicitly enable the authorized server RTX3080 renderer")
    parser.add_argument("--cuda-device", type=int, default=0)
    args = parser.parse_args(argv)
    from industrial_render import configure
    configure(enable_gpu=args.enable_gpu, cuda_device=args.cuda_device)
    # Reserve and validate the listener before any account/project DB creation.
    httpd = build_server(None, host=args.host, port=args.port, public_origin=args.public_origin,
                         verbose=args.verbose)
    try:
        from project_gateway import ProjectGateway
        app = ProjectGateway.from_root(args.data_root, secure_cookies=httpd.secure_cookies)
    except BaseException:
        httpd.server_close()
        raise
    httpd.application = app
    thread = None
    try:
        stop = threading.Event()
        for signame in ("SIGTERM", "SIGINT"):
            signal.signal(getattr(signal, signame), lambda *_: stop.set())
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        print(f"OPENCAD_PRIVATE_WEB verdict=READY port={httpd.server_address[1]}", flush=True)
        while not stop.is_set():
            time.sleep(0.2)
    finally:
        try:
            if thread is not None and thread.is_alive():
                httpd.shutdown()
            httpd.server_close()
            if thread is not None:
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError("Private listener did not stop after shutdown")
        finally:
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
