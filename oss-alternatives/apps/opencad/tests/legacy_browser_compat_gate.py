#!/usr/bin/env python3
"""legacy_browser_compat_gate.py -- OpenCAD Web shell legacy-browser bootstrap gate.

一句话用途：在**离开 Linux 去 Windows 之前**就抓住「前端用了目标浏览器没有的语法/API/CSS」，
并且用**真的旧引擎**跑一遍页面证明 bootstrap 真的起来了（legacy Edge / old Chromium /
minimum browser version / 页面白屏根因）。

为什么需要（2026-10-01 实锚）
----------------------------
真 Windows Server 2022 VM（Edge 86.0.622.38，ISO 自带）上跑打包件时宿主侧记到
WINVM_GUI verdict=FAIL、CDP 里 #lang-select options=0、app_ready 为 null；同一份包在 Edge 154 上
PASS。Linux 侧原来的渲染腿（tools/cad/ui_gate.py）只用本机**新** Chromium 跑 --dump-dom，
所以「前端用了新特性」这类缺陷在 Linux 上**永远抓不到**，只能到 Windows 上才炸。本门补这一段。

两条腿：
* **静态腿（默认开，零依赖）**：按**目标浏览器版本**扫 web/** 的 JS/HTML/CSS，命中任何
  「该版本还没有」的语法/API/CSS 特性即报红，并给 文件:行 + 特性 + 最低版本。
* **动态腿（给了浏览器才跑）**：用**真**旧引擎打开本地起好的页面，断言 #lang-select 真有
  >=3 个 option、[data-i18n-missing] 为 0、canvas 有真实尺寸。--browser 或环境变量
  OPENCAD_LEGACY_CHROME 指到目标版本的 chrome/msedge；没给则记 NA（判词里显式写出，不冒充绿）。

判定行
------
OPENCAD_UI_LEGACY_EDGE verdict=PASS|FAIL|NA edge_version_target=<n> root_cause=<...>
bootstrap_ok=<0|1|NA> lang_options=<n|-1> selftest=PASS(<n>) mutants_red=<n>/<n> rc=<0|1|2>
三态口径（AGENTS.md E2）：NA != PASS；lang_options=-1 = **未采**（动态腿没跑），0 = 真为零
（页面里确实没填出选项，这就是缺陷）。字段语义：fixed=1 = **这份 web 面上没有待修的
不兼容**（不是"我改过代码"）；fixed=0 = 有命中/断言失败。boot_note 里带 ready_polls 与
waited_ms 一类的就绪门读数，门"看着在、其实没等"时能从判定行直接看出来。

用法
----
    python3 oss-alternatives/apps/opencad/tests/legacy_browser_compat_gate.py
    python3 oss-alternatives/apps/opencad/tests/legacy_browser_compat_gate.py --browser /path/to/chrome
    python3 oss-alternatives/apps/opencad/tests/legacy_browser_compat_gate.py --selftest
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
APP_DIR = HERE.parents[1]
WEB_DIR = APP_DIR / "web"
SERVER_PY = APP_DIR / "server.py"
GATE = "OPENCAD_UI_LEGACY_EDGE"

#: 目标「Edge 86 线」。Edge 86.0.622.38 内核 = Chromium 86.0.4240.75（同一 V8 8.6 / Blink 86），
#: 所以 JS 语法与 Web API 的支持面按 Chromium 86 查表即可；CSS 亦同。
EDGE_TARGETS: dict[str, int] = {"edge86": 86, "edge100": 100, "edge120": 120}

# ----------------------------------------------------------------------------------------------
# 特性表 —— 「尺子」本体。每条 = (id, kind, 正则, 支持它的最低 Chromium 版本, 说明)。
# 只收**能被正则稳定认出**的特性；版本号以 MDN/caniuse 的 Chrome 支持版本为准。
# 要扩目标浏览器就加条目/加 target，不要改判定逻辑。
# ----------------------------------------------------------------------------------------------
FEATURES: tuple[tuple[str, str, str, int, str], ...] = (
    ("js.static-class-block", "js", r"(^|[^\w$.])static\s*\{", 94, "class static block（Chrome 94）"),
    ("js.top-level-await", "js", r"(?m)^await\s", 89, "模块顶层 await（Chrome 89）"),
    ("js.private-in-expr", "js", r"#[\w$]+\s+in\s+", 91, "ergonomic brand check（Chrome 91）"),
    ("js.regexp-d-flag", "js", r"/[^/\n]*/[dgimsuvy]*d[dgimsuvy]*[.;,)\s]", 90, "正则 d 标志（Chrome 90）"),
    ("js.numeric-separator", "js", r"\b\d[\d_]*_\d", 75, "数字分隔符 1_000（Chrome 75）"),
    ("js.structured-clone", "js", r"\bstructuredClone\s*\(", 98, "structuredClone()（Chrome 98）"),
    ("js.object-has-own", "js", r"\bObject\.hasOwn\s*\(", 93, "Object.hasOwn()（Chrome 93）"),
    ("js.array-at", "js", r"\.at\s*\(\s*-?\d", 92, "Array/String.prototype.at()（Chrome 92）"),
    ("js.find-last", "js", r"\.findLast(Index)?\s*\(", 97, "findLast（Chrome 97）"),
    ("js.to-sorted", "js", r"\.(toSorted|toReversed|toSpliced|with)\s*\(", 110, "change-array-by-copy（Chrome 110）"),
    ("js.object-group-by", "js", r"\b(Object|Map)\.groupBy\s*\(", 117, "Object.groupBy（Chrome 117）"),
    ("js.promise-any", "js", r"\bPromise\.any\s*\(", 85, "Promise.any()（Chrome 85）"),
    ("js.error-cause", "js", r"new\s+(Error|TypeError|RangeError)\s*\([^)]*\{[^}]*\bcause\b", 93, "Error cause（Chrome 93）"),
    ("web.crypto-random-uuid", "js", r"\bcrypto\.randomUUID\s*\(", 92, "crypto.randomUUID()（Chrome 92）"),
    ("web.url-can-parse", "js", r"\bURL\.canParse\s*\(", 120, "URL.canParse()（Chrome 120）"),
    ("web.intl-segmenter", "js", r"\bIntl\.Segmenter\b", 87, "Intl.Segmenter（Chrome 87）"),
    ("web.canvas-round-rect", "js", r"\.roundRect\s*\(", 99, "CTX2D.roundRect（Chrome 99）"),
    ("web.report-error", "js", r"\breportError\s*\(", 95, "window.reportError（Chrome 95）"),
    ("web.scheduler-yield", "js", r"\bscheduler\.(yield|postTask)\s*\(", 129, "Task Scheduling（Chrome 129/94）"),
    ("web.resize-observer", "js", r"\bnew\s+ResizeObserver\b", 64, "ResizeObserver（Chrome 64）"),
    ("css.is-where", "css", r":(is|where)\s*\(", 88, "CSS :is()/:where()（Chrome 88）"),
    ("css.has", "css", r":has\s*\(", 105, "CSS :has()（Chrome 105）"),
    ("css.aspect-ratio", "css", r"(?m)^\s*aspect-ratio\s*:", 88, "CSS aspect-ratio（Chrome 88）"),
    ("css.inset-property", "css", r"(?m)^\s*inset(-inline|-block)?\s*:", 87, "CSS inset（Chrome 87）"),
    ("css.color-mix", "css", r"\bcolor-mix\s*\(", 111, "CSS color-mix()（Chrome 111）"),
    ("css.oklch", "css", r"\boklch\s*\(", 111, "CSS oklch()（Chrome 111）"),
    ("css.nesting", "css", r"(^|[\s;{])&[\s:.#\[]", 120, "CSS 嵌套（Chrome 120）"),
    ("css.layer", "css", r"@layer\b", 99, "@layer（Chrome 99）"),
    ("css.container", "css", r"@container\b", 105, "@container（Chrome 105）"),
    ("css.dvh", "css", r"\b\d+(\.\d+)?d(vh|vw|vi|vb)\b", 108, "dvh/dvw（Chrome 108）"),
    ("css.scrollbar-width", "css", r"(?m)^\s*scrollbar-width\s*:", 121, "scrollbar-width（Chrome 121）"),
    ("css.text-wrap", "css", r"(?m)^\s*text-wrap\s*:", 114, "CSS text-wrap（Chrome 114）"),
    ("css.accent-color", "css", r"(?m)^\s*accent-color\s*:", 93, "accent-color（Chrome 93）"),
    ("html.dialog-closedby", "html", r"\bclosedby\s*=", 134, "dialog closedby（Chrome 134）"),
    ("html.popover", "html", r"\bpopover\s*(=|>)", 114, "HTML popover（Chrome 114）"),
)

#: 变异体：每条 = (相对路径, 注入源码, 期望命中的 feature id, 人话标签)。注入文本写死在文件里。
INJECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("js/mutant-at.js", "const last = items.at(-1);\n", "js.array-at", "Array.prototype.at()"),
    ("js/mutant-structured-clone.js", "const copy = structuredClone(doc);\n", "js.structured-clone", "structuredClone()"),
    ("js/mutant-static-block.js", "class A { static { this.n = 1; } }\n", "js.static-class-block", "class static block"),
    ("js/mutant-tla.js", "await boot();\n", "js.top-level-await", "顶层 await"),
    ("js/mutant-object-group-by.js", "const g = Object.groupBy(xs, f);\n", "js.object-group-by", "Object.groupBy"),
    ("js/mutant-find-last.js", "const v = xs.findLast(f);\n", "js.find-last", "findLast"),
    ("css/mutant-mixin.css", ".a { color: color-mix(in srgb, red, blue); }\n", "css.color-mix", "color-mix()"),
    ("css/mutant-has.css", ".a:has(> .b) { color: red; }\n", "css.has", ":has()"),
    ("css/mutant-dvh.css", ".a { height: 100dvh; }\n", "css.dvh", "dvh"),
    ("css/mutant-nesting.css", ".a { & .b { color: red; } }\n", "css.nesting", "CSS 嵌套"),
)

CLEAN_FIXTURE_JS = "\"use strict\";\n(function () {\n  const items = [1, 2, 3];\n  const obj = { a: 1 };\n"
CLEAN_FIXTURE_JS += "  const maybe = obj && obj.a ? obj.a : 0;\n"
CLEAN_FIXTURE_JS += "  const mapped = items.map(function (v) { return v * 2; });\n"
CLEAN_FIXTURE_JS += "  const has = Object.prototype.hasOwnProperty.call(obj, \"a\");\n"
CLEAN_FIXTURE_JS += "  async function boot() { const r = await fetch(\"/api/state\"); return (await r.json()).ok; }\n"
CLEAN_FIXTURE_JS += "  boot();\n})();\n"

CLEAN_FIXTURE_CSS = ":root { --fg: #303030; }\n"
CLEAN_FIXTURE_CSS += ".wrap { display: flex; gap: 8px; color: var(--fg); }\n"
CLEAN_FIXTURE_CSS += "@media (prefers-color-scheme: dark) { :root { --fg: #F6F6F6; } }\n"

CLEAN_FIXTURE_HTML = "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title></title></head>\n"
CLEAN_FIXTURE_HTML += "<body><select id=\"lang-select\"></select><canvas id=\"canvas\"></canvas></body></html>\n"


class Finding:
    __slots__ = ("feature", "kind", "min_chrome", "path", "line", "text", "note")

    def __init__(self, feature: str, kind: str, min_chrome: int, path: str, line: int, text: str, note: str):
        self.feature = feature
        self.kind = kind
        self.min_chrome = min_chrome
        self.path = path
        self.line = line
        self.text = text.strip()[:120]
        self.note = note


def _iter_source_files(web: Path) -> list[tuple[str, Path]]:
    """web/** 里会被 index.html 引到的源码文件（js/css/html）。"""
    out: list[tuple[str, Path]] = []
    for path in sorted(web.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in (".js", ".mjs", ".css", ".html", ".htm"):
            out.append(("html" if suffix in (".html", ".htm") else suffix.lstrip("."), path))
    return out


def scan_sources(web: Path, target: int) -> list[Finding]:
    """静态腿：按目标版本扫 web/**，返回所有「目标版本还没有」的特性命中。"""
    findings: list[Finding] = []
    for kind, path in _iter_source_files(web):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for fid, fkind, pattern, min_chrome, note in FEATURES:
            if fkind != kind or min_chrome <= target:
                continue
            try:
                rx = re.compile(pattern)
            except re.error as exc:  # 尺子自己坏了要 loud，不许静默跳过
                raise SystemExit("FEATURES bad regex " + fid + ": " + str(exc))
            for m in rx.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                raw = lines[line_no - 1] if 0 < line_no <= len(lines) else m.group(0)
                findings.append(Finding(fid, kind, min_chrome, str(path), line_no, raw, note))
    return findings


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 起服务期间的任何失败都只是"还没起来"
            time.sleep(0.2)
    return False


class _WSClient:
    """极简 RFC6455 客户端（只为 CDP 用，零第三方依赖）。

    为什么不用 Runtime.evaluate(awaitPromise=True) 里那个长活轮询：2026-10-01 实锚 ——
    Windows VM 的 Edge 86 上那个 awaitPromise 调用返回 error（宿主取 $rr.result.result.value
    得 null，被读成"超时"），**就绪门静默失效**，后面的测量全在页面 bootstrap 完成前做，
    记出 #lang-select options=0 的假红。这里的做法相反：**由主机侧反复发同步 evaluate**，
    每次都是独立短请求，永远不会去等一个长活的 Promise。
    """

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        m = re.match(r"ws://([^/]+)(/.*)", url)
        if not m:
            raise ValueError("bad ws url: " + url)
        hostport, path = m.group(1), m.group(2)
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = ("GET " + path + " HTTP/1.1\r\nHost: " + hostport + "\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("websocket handshake closed")
            buf += chunk
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OSError("websocket handshake failed: " + head.split(b"\r\n")[0].decode("latin-1"))

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, payload: str) -> None:
        data = payload.encode("utf-8")
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_text(self) -> str:
        while True:
            b0, b1 = self._read(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = int.from_bytes(self._read(2), "big")
            elif length == 127:
                length = int.from_bytes(self._read(8), "big")
            if b1 & 0x80:
                mask = self._read(4)
                raw = self._read(length)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
            else:
                data = self._read(length)
            if opcode == 0x1:
                return data.decode("utf-8", "replace")
            if opcode == 0x8:
                raise OSError("websocket closed by peer")
            # ping/pong/continuation：本门只认单帧文本，其余跳过


def _cdp_call(ws: "_WSClient", state: dict, method: str, params: dict, timeout_s: float) -> dict:
    state["id"] += 1
    mid = state["id"]
    ws.send_text(json.dumps({"id": mid, "method": method, "params": params}))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        msg = json.loads(ws.recv_text())
        if msg.get("id") == mid:
            return msg
    raise TimeoutError("cdp " + method + " timed out")


def run_browser_leg(browser: str, web_override: Path | None, timeout_s: int) -> dict:
    """起真 server + 真旧引擎，用 CDP **主机侧轮询**等 bootstrap 完成，再断言。

    返回 dict；lang_options = -1 表示**未采**（绝不冒充 0），ready_polls/waited_ms 是就绪门的
    可审计读数（轮询了几次、等了多久）—— 门失效时必须能从判定行里看出来。
    """
    result = {"ok": False, "lang_options": -1, "missing": -1, "title": "", "canvas": "",
              "note": "", "ready_polls": -1, "waited_ms": -1, "ready_state": "", "browser": ""}
    if not os.path.isfile(browser) or not os.access(browser, os.X_OK):
        result["note"] = "browser_absent"
        return result
    workdir = Path(tempfile.mkdtemp(prefix="opencad-legacy-ui-"))
    port = _free_port()
    dbg = _free_port()
    profile = workdir / "profile"
    cmd = [sys.executable, str(SERVER_PY), "--host", "127.0.0.1", "--port", str(port),
           "--out-root", str(workdir)]
    if web_override is not None:
        cmd += ["--web", str(web_override)]
    proc = subprocess.Popen(cmd, cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    browser_proc = None
    ws = None
    try:
        if not _wait_http("http://127.0.0.1:" + str(port) + "/api/state", 20):
            result["note"] = "server_not_up"
            return result
        app_url = "http://127.0.0.1:" + str(port) + "/"
        browser_proc = subprocess.Popen(
            [browser, "--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
             "--remote-debugging-port=" + str(dbg), "--user-data-dir=" + str(profile),
             "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", app_url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ver = None
        deadline = time.time() + 25
        while time.time() < deadline and ver is None:
            try:
                with urllib.request.urlopen("http://127.0.0.1:" + str(dbg) + "/json/version", timeout=2) as resp:
                    ver = json.loads(resp.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - 浏览器还没把 CDP 端口起来
                time.sleep(0.3)
        if ver is None:
            result["note"] = "browser_no_cdp"
            return result
        result["browser"] = str(ver.get("Browser", ""))
        page_ws = None
        deadline = time.time() + 25
        while time.time() < deadline and not page_ws:
            try:
                with urllib.request.urlopen("http://127.0.0.1:" + str(dbg) + "/json/list", timeout=2) as resp:
                    for t in json.loads(resp.read().decode("utf-8")):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            page_ws = t["webSocketDebuggerUrl"]
                            break
            except Exception:  # noqa: BLE001 - target 列表还没出来
                pass
            if not page_ws:
                time.sleep(0.3)
        if not page_ws:
            result["note"] = "browser_no_page_target"
            return result
        ws = _WSClient(page_ws)
        state = {"id": 0}
        _cdp_call(ws, state, "Runtime.enable", {}, 10)
        t0 = time.time()
        polls = 0
        options = -1
        deadline = t0 + min(float(timeout_s), 60.0)
        while time.time() < deadline:
            polls += 1
            msg = _cdp_call(ws, state, "Runtime.evaluate", {
                "expression": "document.querySelectorAll('#lang-select option').length",
                "returnByValue": True}, 10)
            try:
                options = msg["result"]["result"]["value"]
            except Exception:  # noqa: BLE001 - 单次读数取不到就继续轮询，最后一并记
                options = -1
            if isinstance(options, int) and options >= 3:
                break
            time.sleep(0.25)
        result["ready_polls"] = polls
        result["waited_ms"] = int((time.time() - t0) * 1000)
        expr = ("(function(){var s=document.querySelector('#lang-select');"
                "var c=document.getElementById('canvas');"
                "var labels=s?Array.prototype.slice.call(s.options).map(function(o){return (o.textContent||'').trim();}):[];"
                "return JSON.stringify({options:s?s.options.length:-1,title:document.title,"
                "missing:document.querySelectorAll('[data-i18n-missing]').length,"
                "labels:labels,"
                "ready:document.readyState,canvas:c?c.width+'x'+c.height:''});})()")
        msg = _cdp_call(ws, state, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, 10)
        snap = json.loads(msg["result"]["result"]["value"])
        result["lang_options"] = int(snap.get("options", -1))
        result["missing"] = int(snap.get("missing", -1))
        result["title"] = str(snap.get("title", ""))
        result["canvas"] = str(snap.get("canvas", ""))
        result["ready_state"] = str(snap.get("ready", ""))
        result["labels"] = [str(x) for x in (snap.get("labels") or [])]
        problems: list[str] = []
        if result["lang_options"] < 3:
            problems.append("lang_options=" + str(result["lang_options"]) + "<3")
        if result["missing"] != 0:
            problems.append("i18n_missing_nodes=" + str(result["missing"]))
        if not result["title"]:
            problems.append("title_empty")
        if not result["canvas"]:
            problems.append("canvas_absent")
        else:
            try:
                w, _, hgt = result["canvas"].partition("x")
                if int(w) <= 200 or int(hgt) <= 200:
                    problems.append("canvas_too_small=" + result["canvas"])
            except ValueError:
                problems.append("canvas_unparsable=" + result["canvas"])
        result["ok"] = not problems
        result["note"] = ",".join(problems) if problems else "bootstrap_ok"
        return result
    except Exception as exc:  # noqa: BLE001 - 采到为假就是 FAIL，不是 NA
        result["note"] = "browser_error:" + type(exc).__name__ + ":" + str(exc)
        return result
    finally:
        if ws is not None:
            try:
                ws.sock.close()
            except OSError:
                pass
        for p in (browser_proc, proc):
            if p is None:
                continue
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def _build_clean_fixture(root: Path) -> None:
    (root / "js").mkdir(parents=True, exist_ok=True)
    (root / "css").mkdir(parents=True, exist_ok=True)
    (root / "js" / "app.js").write_text(CLEAN_FIXTURE_JS, encoding="utf-8")
    (root / "css" / "app.css").write_text(CLEAN_FIXTURE_CSS, encoding="utf-8")
    (root / "index.html").write_text(CLEAN_FIXTURE_HTML, encoding="utf-8")


def selftest(target: int, verbose: bool) -> tuple[bool, int, int, list[str]]:
    """阳性对照 + 变异体阴性对照；返回 (ok, checks, mutants_red, notes)。"""
    notes: list[str] = []
    checks = 0
    root = Path(tempfile.mkdtemp(prefix="opencad-legacy-selftest-"))
    try:
        _build_clean_fixture(root)
        clean = scan_sources(root, target)
        checks += 1
        if clean:
            notes.append("positive-control FAIL: clean fixture flagged " + str([f.feature for f in clean]))
            return False, checks, 0, notes
        mutants_red = 0
        for rel, snippet, expect, label in INJECTIONS:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(snippet, encoding="utf-8")
            hit = {f.feature for f in scan_sources(root, target)}
            checks += 1
            ok = hit == {expect}
            if ok:
                mutants_red += 1
            else:
                notes.append("mutant " + rel + " (" + label + ") expect " + expect + " got " + str(sorted(hit)))
            if verbose:
                print("[selftest] mutant " + rel + " expect=" + expect + " got=" + str(sorted(hit)) + " ok=" + str(ok))
            path.unlink()
        return (mutants_red == len(INJECTIONS)), checks, mutants_red, notes
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenCAD Web legacy-browser bootstrap gate")
    ap.add_argument("--target", default="edge86", choices=sorted(EDGE_TARGETS))
    ap.add_argument("--browser", default=os.environ.get("OPENCAD_LEGACY_CHROME", ""),
                    help="目标版本的 chrome/msedge 可执行文件；给了才跑动态腿")
    ap.add_argument("--web", default="", help="覆盖被扫的 web 目录（缺省仓内 web/）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)

    target = EDGE_TARGETS[args.target]
    web = Path(args.web).resolve() if args.web else WEB_DIR

    st_ok, st_checks, mutants_red, st_notes = selftest(target, args.verbose)
    st_verdict = ("PASS(" + str(st_checks) + ")") if st_ok else ("FAIL(" + str(st_checks) + ")")
    for n in st_notes:
        print("[selftest] " + n, file=sys.stderr)

    if args.selftest:
        verdict = "PASS" if st_ok else "FAIL"
        rc = 0 if st_ok else 2
        print(GATE + " verdict=" + verdict + " edge_version_target=" + str(target)
              + " root_cause=selftest_only bootstrap_ok=NA lang_options=-1 selftest=" + st_verdict
              + " mutants_red=" + str(mutants_red) + "/" + str(len(INJECTIONS)) + " rc=" + str(rc))
        return rc

    if not web.is_dir():
        print(GATE + " verdict=NA edge_version_target=" + str(target) + " root_cause=web_dir_absent:" + str(web)
              + " fixed=0 bootstrap_ok=NA lang_options=-1 selftest=" + st_verdict
              + " mutants_red=" + str(mutants_red) + "/" + str(len(INJECTIONS)) + " rc=2")
        return 2

    findings = scan_sources(web, target)
    for f in findings:
        print("[legacy] " + f.path + ":" + str(f.line) + " feature=" + f.feature
              + " min_chrome=" + str(f.min_chrome) + " target=" + str(target) + " -> " + f.note
              + " :: " + f.text)

    boot_ok = "NA"
    lang_options = -1
    boot_note = "browser_absent(NA)"
    if args.browser:
        res = run_browser_leg(args.browser, web if args.web else None, args.timeout)
        print("[legacy] browser=" + str(res.get("browser", "")) + " ready_polls=" + str(res.get("ready_polls"))
              + " waited_ms=" + str(res.get("waited_ms")) + " readyState=" + str(res.get("ready_state"))
              + " title=" + str(res.get("title")) + " canvas=" + str(res.get("canvas"))
              + " lang_labels=" + str(res.get("labels")))
        lang_options = res["lang_options"]
        boot_note = res["note"]
        if res["note"] == "browser_absent":
            boot_ok = "NA"
        else:
            boot_ok = "1" if res["ok"] else "0"
            if not res["ok"]:
                findings.append(Finding("runtime.bootstrap", "runtime", 0, args.browser, 0, "", res["note"]))

    rc = 0
    root_cause = "none"
    if findings:
        rc = 1
        root_cause = " ".join(Path(f.path).name + ":" + str(f.line) + ":" + f.feature
                              + "(min_chrome=" + str(f.min_chrome) + ")" for f in findings[:4])
    verdict = "PASS"
    if findings:
        verdict = "FAIL"
    elif boot_ok == "NA":
        rc = 2                      # 静态腿绿但动态腿未采 ⇒ rc=2（NA 不冒充绿）
    elif boot_ok == "0":
        verdict = "FAIL"
        rc = 1

    print(GATE + " verdict=" + verdict + " edge_version_target=" + str(target)
          + " root_cause=" + root_cause + " fixed=" + ("1" if not findings else "0")
          + " bootstrap_ok=" + str(boot_ok) + " lang_options=" + str(lang_options)
          + " selftest=" + st_verdict + " mutants_red=" + str(mutants_red) + "/" + str(len(INJECTIONS))
          + " static_findings=" + str(len(findings)) + " boot_note=" + boot_note + " rc=" + str(rc))
    return rc


if __name__ == "__main__":
    sys.exit(main())
