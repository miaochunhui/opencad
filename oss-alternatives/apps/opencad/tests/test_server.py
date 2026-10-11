#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opencad 判定行测试 —— U4-tests-readme 席位的 `tests/test_server.py`。

被测对象
--------
`oss-alternatives/apps/opencad/server.py`（本地 Web 外壳服务端）+ 它背后的 cadcore 真件
（`docmodel` L1 文本真源 / `sketch.commands` 编辑器 / `i18n.catalog` 语言包 / `i18n.standards`
标准档 / `io.dxf_bridge` DXF 导出）。本文件**不 import server**，一律**起真进程**：`subprocess`
拉起 `server.py --port 0`，从它自己打的那行 ``OPENCAD_SERVER verdict=PASS port=<n> routes=<n>``
里读实际端口（`--port 0` 由 OS 分配），再用标准库 `http.client` 打真 HTTP。

跑法（三条都真跑过；第二条是**冻结契约里给的那条**）
--------------------------------------------------
    # (1) 缺省：start dir 自己当 top-level（Python 3.10 / 3.11 / 3.12 都能跑）
    python3 -m unittest discover -s oss-alternatives/apps/opencad/tests -v

    # (2) 冻结契约给的那条（-t 指到 app 目录）
    python3 -m unittest discover -s oss-alternatives/apps/opencad/tests \\
                                 -t oss-alternatives/apps/opencad -v

    # (3) 直接跑一个文件
    python3 oss-alternatives/apps/opencad/tests/test_server.py -v

⚠️ **第 (2) 条在 Python 3.10 上有前提**：`unittest` 3.10 的 discovery 要求
「start dir 必须相对 top-level dir 可导入」，判据是 `tests/__init__.py` **存在**
（`discover()` 在 3.10 里 `raise ImportError('Start directory is not importable')`；
命名空间包支持是 **3.11 才进 unittest 的**）。本仓范式件 `apps/freechat/tests/` 与
`packages/cadcore/tests/` 都带 `__init__.py`，opencad 的 `tests/` 目前没有 —— 缺这一个空文件时
第 (2) 条**当场红**（`ImportError`，不是本测试文件的问题，也不是静默跳过）。本席位的写入范围
只有 `tests/test_server.py` 与 `README.md`，**没有**创建 `tests/__init__.py`（那不在名下），
所以这里如实记录：第 (2) 条按原样跑会 rc≠0，第 (1) 条给的是真判定行。补上
`tests/__init__.py`（内容可以是空）后第 (2) 条即通 —— 3.11 及以上无需该文件。

测什么（25 例，**不依赖执行顺序**：每个测试自己起一个进程、自己收尸）
------------------------------------------------------------------
1. 夹具自证：起的是**真 server.py 进程**、判定行里有真端口与 routes=8、未知路由真 404
2. `/api/state` 契约字段与枚举；空文档时 entities/dimensions **真为 0**（不是未采）
3. `/api/catalogs` 语言数 = 3（en / ja / zh-CN）+ `native_name` 是各语言自己的写法；
   `keys` 是**数出来的**（≥1），读不出来才是 -1（未采）—— 0 / -1 / NA 三态不许混
4. `/api/locale` 切 ja：`changed=1`、strings **整包换掉**（真有 key 变了）、`/api/state` 不重启即变；
   同语言再切 `changed=0`；未核语言 ⇒ 显式 `degraded=True` 且 `resolved != locale`（不静默）
5. `/api/standard`：iso⇒NA、gbt⇒first、ansi⇒third、din⇒first、jis⇒NA（**未核标一律 "NA"**，不编角度）；
   切档后 `/api/state` 的 projection 跟着变
6. `/api/command`：line / circle / arc 逐个加，`entities` 严格递增且 uuid 不重复；
   `add_dimension` 只涨 `dimensions`；坏参数 / 坏几何 / 未知动词都是 **HTTP 200 + ok:false + 机读码**
7. `delete_entity` / `undo` / `redo`：删了要掉、undo 要回得来（走 L1 快照栈，不是伪撤销）
8. `/api/export/dxf`：**真字节落盘**、`bytes` 与磁盘大小一致、`packages/dxf` **能读回**（ENTITIES 段
   实体数与 API 报的一致、LINE 条数对得上）；草图尺寸按 999 注释记录 ⇒ `dimension_entities` 真为 0
   而 `document_dimensions` 为 1（**不许编坐标**，这两个数就是"没编"的判据）
9. `/api/save` + `/api/open` 往返：实体数与 **L1 文本逐字相同**；open 不存在的文件要显式
   `file_not_found`，**不许**静默换成空文档
10. 路由：未知 API GET/POST 都是 404 + `ok:false`；已知 API 的**业务失败**一律 200 + `ok:false`
    （**请求参数非法是 400**：`/api/standard` 的缺失/未知 standard 见 `test_b6_product_defects.py`）
11. 路径穿越：`..` / 绝对路径 / 反斜杠 / NUL / `%2e%2e` 一律 `path_rejected`，
    静态面 `/web/../server.py` 404，**阳性对照**（根内正常相对路径必须成功）—— 否则"全拒"也能骗过穿越测试
12. 静态面：`/` 返回 index.html 且**七个 DOM 契约 id 齐全**、无任何 `http://` / `https://` / `cdn` 外链
13. CLI：`--port 70000` ⇒ `verdict=FAIL reason=port_range` + rc=2（**禁 rc=0**）；`--selftest` ⇒ rc=0
    且判定行 PASS

口径三态（AGENTS.md E2）本文件的用法
--------------------------------
凡是计数断言，都写成「**真为零**才 == 0 / 未采就显式 -1 / 不适用就 "NA"」的可分辨形态；
本文件**不产生** 0 与"没测到"同形的断言，也不接受任何"静默返 0"的实现。

出网说明
--------
本文件用 `from http import client`（不是 `urllib`）打 **127.0.0.1 回环**，与范式件
`apps/freechat/tests/test_server.py` 同一写法：回环不算出网，但静态腿不该为此制造假红。
每个测试只写独立的外置临时目录，先停止服务器再清理，不碰别人的文件。
"""

from __future__ import annotations

import ast
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from http import client as http_client
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)
_OSS_ROOT = os.path.dirname(os.path.dirname(_APP_DIR))

#: `packages/dxf` 真件（DXF 读回用）。缺件 ⇒ 那条测试显式报 NA，不静默跳过当通过。
_DXF_DIR = os.path.join(_OSS_ROOT, "packages", "dxf")
if os.path.isdir(_DXF_DIR) and _DXF_DIR not in sys.path:
    sys.path.insert(0, _DXF_DIR)
try:
    from dxf.reader import read as dxf_read  # noqa: E402
except Exception as _dxf_exc:  # noqa: BLE001 - 缺件要能被测试如实报成 NA
    dxf_read = None
    DXF_IMPORT_ERROR = "%s: %s" % (type(_dxf_exc).__name__, _dxf_exc)
else:
    DXF_IMPORT_ERROR = None

SERVER_PY = os.path.join(_APP_DIR, "server.py")
VERDICT_PASS = "OPENCAD_SERVER verdict=PASS"
VERDICT_FAIL = "OPENCAD_SERVER verdict=FAIL"

#: 冻结契约里写死的集合（本文件只断言这些**契约值**，不断言任何译文文本）。
CONTRACT_LOCALES = ("en", "ja", "zh-CN")
CONTRACT_STANDARDS = ("iso", "gbt", "ansi", "din", "jis")
CONTRACT_PROJECTIONS = {"iso": "NA", "gbt": "first", "ansi": "third", "din": "first", "jis": "NA"}
CONTRACT_API_ROUTES = (
    ("GET", "/api/state"), ("GET", "/api/mesh"), ("GET", "/api/step"),
    ("GET", "/api/catalogs"), ("GET", "/api/open"),
    ("POST", "/api/locale"), ("POST", "/api/standard"), ("POST", "/api/command"),
    ("POST", "/api/export/dxf"), ("POST", "/api/export/step"), ("POST", "/api/save"),
    ("POST", "/api/clearance"), ("POST", "/api/release/prepare"),
    ("POST", "/api/release/restore"),
)
CONTRACT_ROUTES = len(CONTRACT_API_ROUTES)
DOM_IDS = ("canvas", "lang-select", "standard-select", "status", "layers", "cmd-line", "toolbar")
TOOL_NAMES = ("select", "line", "circle", "arc", "dimension", "delete")
STRING_KEYS_REQUIRED = ("app.title", "tool.line", "tool.circle", "tool.arc", "tool.dimension",
                        "tool.delete", "tool.select", "panel.layers", "cmd.placeholder", "status.ready")

_ARTIFACT_ROOT = "/hf3fs-jd/data/tmp/opencad-3d-green-20261010"


class ServerProcess:
    """真服务：`subprocess` 起 `server.py --port 0`，从判定行读端口；`stop()` 幂等且必杀。"""

    def __init__(self, out_root: str) -> None:
        self.proc: "subprocess.Popen | None" = None
        self.out_root = os.path.realpath(out_root)
        self.port = 0
        self.startup_line = ""
        self.routes = -1
        self.stderr_text = ""
        self._stopped = False

    def start(self) -> "ServerProcess":
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PY, "--host", "127.0.0.1", "--port", "0",
             "--out-root", self.out_root],
            cwd=_APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 20.0
        while time.time() < deadline:
            ready, _w, _x = select.select([self.proc.stdout], [], [], max(0.1, deadline - time.time()))
            if not ready:
                continue
            line = (self.proc.stdout.readline() or "").strip()
            if not line:
                break  # EOF：进程没了
            if line.startswith(VERDICT_FAIL):
                raise AssertionError("server refused to start: %s" % line)
            if line.startswith(VERDICT_PASS):
                self.startup_line = line
                self.port = int(line.split("port=")[1].split()[0])
                if "routes=" in line:
                    self.routes = int(line.split("routes=")[1].split()[0])
                return self
        self._collect_stderr()
        raise AssertionError("no verdict line within 20s (rc=%s) stderr=%s"
                             % (self.proc.poll(), self.stderr_text[-400:]))

    def _collect_stderr(self) -> None:
        if self.proc is None or self.proc.stderr is None:
            return
        try:
            ready, _w, _x = select.select([self.proc.stderr], [], [], 0.2)
            if ready:
                self.stderr_text += self.proc.stderr.read() or ""
        except (ValueError, OSError):
            pass

    def stop(self) -> None:
        """幂等收尸：terminate → 等 5s → kill；子进程一个不留。"""
        if self._stopped or self.proc is None:
            return
        self._stopped = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()

    # ── HTTP（http.client：路径原样发出，不会被 urllib 规范化掉 `..`）────────────────
    def request(self, method: str, path: str, body: object = None):
        if self.proc is not None and self.proc.poll() is not None:
            raise AssertionError("server process died (rc=%s)" % self.proc.poll())
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            headers = {}
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers = {"Content-Type": "application/json",
                           "Content-Length": str(len(payload))}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, dict(resp.getheaders()), raw
        finally:
            conn.close()

    def get(self, path: str):
        status, _headers, raw = self.request("GET", path)
        return status, json.loads(raw.decode("utf-8"))

    def post(self, path: str, body: object):
        status, _headers, raw = self.request("POST", path, body=body)
        return status, json.loads(raw.decode("utf-8"))


class ServerCase(unittest.TestCase):
    """每个测试自己起一个真服务器（顺序无关）；产物目录收尾即删。"""

    def setUp(self) -> None:
        self.assertTrue(os.path.isfile(SERVER_PY), "NA: server.py not found at %s" % SERVER_PY)
        workspace = tempfile.TemporaryDirectory(
            prefix="u4-tests-", dir=os.environ.get("OPENCAD_OUT_ROOT", _ARTIFACT_ROOT))
        self.addCleanup(workspace.cleanup)
        self.artifacts = os.path.realpath(workspace.name)
        self.server = ServerProcess(self.artifacts)
        self.addCleanup(self.server.stop)
        self.server.start()

    def rel(self, name: str) -> str:
        return name

    def abs_of(self, name: str) -> str:
        return os.path.join(self.artifacts, name)

    def add_line(self, p1=(0, 0), p2=(10000000, 0), layer="0"):
        status, body = self.server.post("/api/command", {
            "command": "add_line", "args": {"p1": list(p1), "p2": list(p2), "layer": layer}})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        return body

    def state(self):
        status, body = self.server.get("/api/state")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        return body


class TestFixtureSelfProof(ServerCase):
    """E2.5：夹具先自证 —— 证明打的是**真 server.py 进程**，不是打桩。"""

    def test_fixture_talks_to_real_server_process(self):
        self.assertNotEqual(self.server.proc.pid, os.getpid())
        self.assertGreater(self.server.port, 0)
        self.assertIn("verdict=PASS", self.server.startup_line)
        self.assertEqual(self.server.routes, CONTRACT_ROUTES,
                         "startup line: %s" % self.server.startup_line)
        status, body = self.server.get("/api/fixture-self-proof-not-a-route")
        self.assertEqual(status, 404)
        self.assertIs(body.get("ok"), False)
        self.assertEqual(body.get("error"), "not_found")

    def test_registered_routes_match_the_explicit_http_contract(self):
        with open(SERVER_PY, encoding="utf-8") as stream:
            tree = ast.parse(stream.read(), filename=SERVER_PY)
        definitions = [node.value for node in tree.body
                       if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == "ROUTES"]
        self.assertEqual(len(definitions), 1)
        routes = ast.literal_eval(definitions[0])
        self.assertEqual(set(routes), set(CONTRACT_API_ROUTES))
        self.assertEqual(len(routes), len(set(routes)), "duplicate route registration")
        self.assertEqual(self.server.routes, len(routes))

    def test_stop_is_idempotent_and_process_really_gone(self):
        proc = self.server.proc
        self.server.stop()
        self.server.stop()
        self.assertIsNotNone(proc.poll(), "child process still alive after stop()")

    def test_explicit_root_wins_over_environment_and_exports_real_bytes(self):
        with tempfile.TemporaryDirectory(prefix="u4-other-", dir=_ARTIFACT_ROOT) as other:
            with patch.dict(os.environ, {"OPENCAD_OUT_ROOT": other}):
                server = ServerProcess(self.artifacts)
                self.addCleanup(server.stop)
                server.start()
            status, body = server.post("/api/save", {"path": self.rel("root-proof.json")})
            self.assertEqual(status, 200, body)
            self.assertIs(body["ok"], True, body)
            target = self.abs_of("root-proof.json")
            self.assertEqual(os.path.realpath(body["path"]), target)
            with open(target, "rb") as handle:
                payload = handle.read()
            self.assertEqual(len(payload), body["bytes"])
            self.assertGreater(len(payload), 0)
            self.assertFalse(os.path.exists(os.path.join(other, "root-proof.json")))
            self.assertNotEqual(os.path.commonpath([self.artifacts, _APP_DIR]), _APP_DIR)
            server.stop()


class TestStateContract(ServerCase):
    def test_state_contract_fields_and_enums(self):
        state = self.state()
        for key in ("ok", "doc", "locale", "standard", "projection", "catalogs", "standards",
                    "strings", "entities"):
            self.assertIn(key, state, "missing contract field %r" % key)
        self.assertIsInstance(state["doc"], str)
        self.assertIn("schema_version", state["doc"])
        self.assertEqual(state["locale"], "zh-CN")
        self.assertEqual(state["standard"], "gbt")
        self.assertIn(state["projection"], ("first", "third", "NA"))
        self.assertEqual(sorted(state["catalogs"]), sorted(CONTRACT_LOCALES))
        self.assertEqual(list(state["standards"]), list(CONTRACT_STANDARDS))
        self.assertIsInstance(state["entities"], int)
        self.assertIsInstance(state["strings"], dict)
        for key in STRING_KEYS_REQUIRED:
            self.assertIn(key, state["strings"], "DOM contract key %r has no string" % key)
            self.assertIsInstance(state["strings"][key], str)

    def test_empty_document_counts_are_true_zero_not_unsampled(self):
        state = self.state()
        self.assertEqual(state["entities"], 0, "0 here must mean really zero entities")
        self.assertEqual(state["dimensions"], 0)
        self.assertTrue(state["sketch_uuid"])


class TestCatalogs(ServerCase):
    def test_catalogs_language_count_and_native_names(self):
        status, body = self.server.get("/api/catalogs")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        catalogs = body["catalogs"]
        self.assertEqual(len(catalogs), len(CONTRACT_LOCALES))
        self.assertEqual(sorted(item["code"] for item in catalogs), sorted(CONTRACT_LOCALES))
        by_code = {item["code"]: item for item in catalogs}
        self.assertEqual(by_code["ja"]["native_name"], "\u65e5\u672c\u8a9e")
        self.assertEqual(by_code["zh-CN"]["native_name"], "\u7b80\u4f53\u4e2d\u6587")
        self.assertEqual(by_code["en"]["native_name"], "English")

    def test_catalog_keys_are_counted_or_explicitly_unsampled(self):
        _status, body = self.server.get("/api/catalogs")
        for item in body["catalogs"]:
            self.assertTrue(item["valid"], item)
            self.assertIsInstance(item["keys"], int)
            self.assertNotEqual(item["keys"], 0,
                                "keys=0 is indistinguishable from 'not sampled'; use -1 (%r)" % item)
            self.assertGreaterEqual(item["keys"], 1)


class TestLocaleRuntimeSwitch(ServerCase):
    def test_simplified_chinese_survives_japanese_and_english_roundtrip(self):
        before = self.state()
        expected = {"cmd.fillet": "\u5706\u89d2", "cmd.trim": "\u4fee\u526a",
                    "cmd.move": "\u79fb\u52a8", "cmd.mirror": "\u955c\u50cf",
                    "cmd.polar": "\u6781\u8f74", "status.polar": "\u6781\u8f74"}
        self.assertEqual({key: before["strings"][key] for key in expected}, expected)
        for locale, fillet in (("ja", "\u30d5\u30a3\u30ec\u30c3\u30c8"), ("en", "Fillet"),
                               ("zh-CN", "\u5706\u89d2")):
            status, body = self.server.post("/api/locale", {"locale": locale})
            self.assertEqual(status, 200, body)
            self.assertEqual(body["resolved"], locale)
            self.assertFalse(body["degraded"])
            self.assertEqual(body["strings"]["cmd.fillet"], fillet)
        after = self.state()
        self.assertEqual(after["doc"], before["doc"])
        self.assertEqual(after["strings"], before["strings"])

    def test_locale_switch_replaces_strings_without_restart(self):
        before = self.state()
        status, body = self.server.post("/api/locale", {"locale": "ja"})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body["locale"], "ja")
        self.assertEqual(body["changed"], 1, "switching zh-CN -> ja must report changed=1")
        self.assertIsInstance(body["strings"], dict)
        self.assertTrue(body["strings"])
        differing = [key for key, value in body["strings"].items()
                     if before["strings"].get(key) != value]
        self.assertTrue(differing, "strings did not change at all after switching to ja")
        after = self.state()
        self.assertEqual(after["locale"], "ja")
        self.assertEqual(after["strings"], body["strings"],
                         "state.strings must match the strings returned by /api/locale")
        self.assertEqual(after["entities"], before["entities"], "locale switch must not touch the doc")

    def test_locale_switch_is_idempotent_and_unknown_locale_is_visible(self):
        _status, first = self.server.post("/api/locale", {"locale": "ja"})
        self.assertEqual(first["changed"], 1)
        _status, again = self.server.post("/api/locale", {"locale": "ja"})
        self.assertEqual(again["changed"], 0, "same locale again is not a change")
        self.assertEqual(again["locale"], "ja")
        _status, bogus = self.server.post("/api/locale", {"locale": "xx-YY"})
        self.assertIs(bogus.get("ok"), True, bogus)
        self.assertIs(bogus["degraded"], True, "an unbacked locale must show up as degraded")
        self.assertNotEqual(bogus["resolved"], "xx-YY")
        self.assertEqual(self.state()["locale"], bogus["locale"])


class TestStandardProfiles(ServerCase):
    def test_projection_angles_and_na_for_unsampled_standards(self):
        for standard, expected in CONTRACT_PROJECTIONS.items():
            status, body = self.server.post("/api/standard", {"standard": standard})
            self.assertEqual(status, 200, standard)
            self.assertIs(body.get("ok"), True, body)
            self.assertEqual(body["standard"], standard)
            self.assertEqual(body["projection"], expected,
                             "%s projection must be %r (NA = not sampled, never a made-up angle)"
                             % (standard, expected))
            self.assertIn(body["projection"], ("first", "third", "NA"))

    def test_state_projection_follows_standard_switch(self):
        self.assertEqual(self.state()["projection"], "first")  # default gbt
        self.server.post("/api/standard", {"standard": "ansi"})
        switched = self.state()
        self.assertEqual(switched["standard"], "ansi")
        self.assertEqual(switched["projection"], "third")
        self.server.post("/api/standard", {"standard": "iso"})
        self.assertEqual(self.state()["projection"], "NA")


class TestCommands(ServerCase):
    def test_command_appends_entities_monotonically(self):
        self.assertEqual(self.state()["entities"], 0)
        seen = []
        line = self.add_line()
        self.assertIsInstance(line["uuid"], str)
        self.assertTrue(line["uuid"])
        seen.append(line["uuid"])
        self.assertEqual(line["entities"], 1)
        _status, circle = self.server.post("/api/command", {
            "command": "add_circle", "args": {"center": [0, 0], "radius_nm": 5000000}})
        self.assertIs(circle.get("ok"), True, circle)
        seen.append(circle["uuid"])
        self.assertEqual(circle["entities"], 2)
        _status, arc = self.server.post("/api/command", {
            "command": "add_arc", "args": {"center": [0, 0], "radius_nm": 5000000,
                                           "start_deg": 0, "end_deg": 90}})
        self.assertIs(arc.get("ok"), True, arc)
        seen.append(arc["uuid"])
        self.assertEqual(arc["entities"], 3)
        self.assertEqual(len(set(seen)), 3, "entity uuids must be unique")
        self.assertEqual(self.state()["entities"], 3)

    def test_add_dimension_bumps_dimensions_not_entities(self):
        line = self.add_line()
        _status, dim = self.server.post("/api/command", {
            "command": "add_dimension",
            "args": {"kind": "linear", "refs": [[line["uuid"], "start"], [line["uuid"], "end"]],
                     "value_nm": 10000000}})
        self.assertIs(dim.get("ok"), True, dim)
        self.assertTrue(dim["uuid"])
        self.assertEqual(dim["entities"], 1)
        self.assertEqual(dim["dimensions"], 1)
        state = self.state()
        self.assertEqual(state["entities"], 1)
        self.assertEqual(state["dimensions"], 1)

    def test_bad_arguments_bad_geometry_and_unknown_verb_are_machine_readable(self):
        cases = [
            ({"command": "add_line", "args": {"p1": [0, 0]}}, "missing_argument"),
            ({"command": "add_circle", "args": {"center": [0, 0], "radius_nm": -5}}, "command_failed"),
            ({"command": "no_such_command", "args": {}}, "unknown_command"),
        ]
        for payload, expected in cases:
            status, body = self.server.post("/api/command", payload)
            self.assertEqual(status, 200, "errors stay HTTP 200 + ok field: %r" % (body,))
            self.assertIs(body.get("ok"), False, body)
            self.assertEqual(body.get("error"), expected, body)
        self.assertEqual(self.state()["entities"], 0, "failed commands must not mutate the document")

    def test_delete_undo_redo_round_trip(self):
        first = self.add_line(p2=(5000000, 0))
        self.add_line(p2=(0, 5000000))
        _status, deleted = self.server.post("/api/command", {
            "command": "delete_entity", "args": {"uuid": first["uuid"]}})
        self.assertIs(deleted.get("ok"), True, deleted)
        self.assertEqual(deleted["entities"], 1)
        _status, undone = self.server.post("/api/command", {"command": "undo", "args": {}})
        self.assertIs(undone.get("ok"), True, undone)
        self.assertEqual(undone["entities"], 2)
        _status, redone = self.server.post("/api/command", {"command": "redo", "args": {}})
        self.assertIs(redone.get("ok"), True, redone)
        self.assertEqual(redone["entities"], 1)
        _status, missing = self.server.post("/api/command", {
            "command": "delete_entity", "args": {"uuid": "00000000-0000-0000-0000-000000000000"}})
        self.assertIs(missing.get("ok"), False, missing)
        self.assertEqual(missing.get("error"), "unknown_reference")


class TestNewCommandSurface(ServerCase):
    """新增命令面：图形基元 / 变换 / 阵列 / 约束删除 / 捕捉查询（本节由 wire 席追加）。

    断言尽量都是**数值**（实体数、uuid 个数、created 个数、距离、DoF），并且三态可分：
    `created=0` 是**真为零**（`array_linear rows=cols=1`）、捕捉没命中回 `hit=0` + `snap=null`、
    解算器给不出的字段回 `"NA"`。⚠️ 文件头那句「25 例」的计数**未随本节刷新**（本节是追加）。
    """

    def _cmd(self, verb, args):
        status, body = self.server.post("/api/command", {"command": verb, "args": args})
        self.assertEqual(status, 200, body)
        return body

    @staticmethod
    def _first_constraint_uuid(l1_text):
        """从 L1 投影里挖一条约束 uuid；挖不到回 None（由调用方显式报 NA，不当通过）。"""
        try:
            doc = json.loads(l1_text)
        except (TypeError, ValueError):
            return None
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "constraints" and isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and isinstance(item.get("uuid"), str):
                                return item["uuid"]
                    stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
        return None

    def test_primitive_counts_are_exact(self):
        rect = self._cmd("add_rectangle", {"p1_nm": [0, 0], "p2_nm": [40000000, 20000000]})
        self.assertIs(rect.get("ok"), True, rect)
        self.assertEqual(rect["entities"], 4, "a rectangle is exactly 4 lines")
        self.assertEqual(rect["created"], 4)
        self.assertEqual(len(rect["uuids"]), 4)
        poly = self._cmd("add_polygon", {"center_nm": [100000000, 0], "radius_nm": 10000000,
                                         "sides": 5})
        self.assertIs(poly.get("ok"), True, poly)
        self.assertEqual(poly["entities"], 9, "a 5-gon adds exactly 5 lines")
        self.assertEqual(poly["created"], 5)
        slot = self._cmd("add_slot", {"p1_nm": [0, 50000000], "p2_nm": [30000000, 50000000],
                                      "width_nm": 10000000})
        self.assertIs(slot.get("ok"), True, slot)
        self.assertEqual(slot["entities"], 13, "a slot adds 2 lines + 2 arcs")
        self.assertEqual(slot["created"], 4)
        polyline = self._cmd("add_polyline", {"points_nm": [[0, 0], [10000000, 0],
                                                            [10000000, 10000000]]})
        self.assertIs(polyline.get("ok"), True, polyline)
        self.assertEqual(polyline["entities"], 14, "a polyline is exactly one entity")
        self.assertEqual(len(polyline["uuids"]), 1)

    def test_transforms_change_counts_as_documented(self):
        rect = self._cmd("add_rectangle", {"p1_nm": [0, 0], "p2_nm": [20000000, 10000000]})
        refs = rect["uuids"][:1]
        moved = self._cmd("move", {"refs": refs, "dx_nm": 1000000, "dy_nm": 2000000})
        self.assertIs(moved.get("ok"), True, moved)
        self.assertEqual(moved["entities"], 4, "move rewrites in place: no new entity")
        copied = self._cmd("copy", {"refs": refs, "dx_nm": 30000000, "dy_nm": 0})
        self.assertIs(copied.get("ok"), True, copied)
        self.assertEqual(copied["entities"], 5, "copy adds exactly one line")
        self.assertEqual(copied["created"], 1)
        rotated = self._cmd("rotate", {"refs": refs, "center_nm": [0, 0], "angle_deg": 90})
        self.assertIs(rotated.get("ok"), True, rotated)
        self.assertEqual(rotated["entities"], 5)
        scaled = self._cmd("scale", {"refs": refs, "center_nm": [0, 0], "factor": 2.0})
        self.assertIs(scaled.get("ok"), True, scaled)
        self.assertEqual(scaled["entities"], 5)
        mirrored = self._cmd("mirror", {"refs": refs, "p1_nm": [0, 0], "p2_nm": [0, 10000000],
                                        "keep_original": True})
        self.assertIs(mirrored.get("ok"), True, mirrored)
        self.assertEqual(mirrored["entities"], 6, "mirror with keep_original adds one line")
        offset = self._cmd("offset", {"ref": refs[0], "distance_nm": 5000000, "side": 1})
        self.assertIs(offset.get("ok"), True, offset)
        self.assertEqual(offset["entities"], 7)
        self.assertEqual(offset["created"], 1)

    def test_array_linear_zero_is_a_real_zero(self):
        line = self.add_line(p1=(0, 0), p2=(10000000, 0))
        none = self._cmd("array_linear", {"refs": [line["uuid"]], "rows": 1, "cols": 1,
                                          "dx_nm": 0, "dy_nm": 0})
        self.assertIs(none.get("ok"), True, none)
        self.assertEqual(none["created"], 0, "rows=cols=1 is a real zero, not 'not measured'")
        self.assertEqual(none["entities"], 1)
        self.assertEqual(none["uuids"], [])
        grid = self._cmd("array_linear", {"refs": [line["uuid"]], "rows": 2, "cols": 2,
                                          "dx_nm": 20000000, "dy_nm": 10000000})
        self.assertIs(grid.get("ok"), True, grid)
        self.assertEqual(grid["entities"], 4, "a 2x2 array adds rows*cols-1 = 3 copies")
        self.assertEqual(grid["created"], 3)
        self.assertEqual(len(grid["uuids"]), 3)

    def test_trim_extend_fillet_chamfer_counts(self):
        # 每个算子都用**新的一对线**：上一次的算子会改写输入，复用会让断言依赖副作用。
        t1 = self.add_line(p1=(0, 0), p2=(10000000, 0))["uuid"]
        t2 = self.add_line(p1=(5000000, -1000000), p2=(5000000, 1000000))["uuid"]
        trimmed = self._cmd("trim", {"target_ref": t1, "cutter_refs": [t2], "keep": "start"})
        self.assertIs(trimmed.get("ok"), True, trimmed)
        self.assertEqual(trimmed["entities"], 2, "trim rewrites in place: no new entity")
        self.assertEqual(trimmed["created"], 1, "trim reports the rewritten target uuid")

        e1 = self.add_line(p1=(0, 20000000), p2=(5000000, 20000000))["uuid"]
        e2 = self.add_line(p1=(8000000, 19000000), p2=(8000000, 21000000))["uuid"]
        extended = self._cmd("extend", {"target_ref": e1, "boundary_refs": [e2], "end": "end"})
        self.assertIs(extended.get("ok"), True, extended)
        self.assertEqual(extended["entities"], 4, "extend rewrites in place: no new entity")

        f1 = self.add_line(p1=(0, 40000000), p2=(20000000, 40000000))["uuid"]
        f2 = self.add_line(p1=(5000000, 30000000), p2=(5000000, 42000000))["uuid"]
        filleted = self._cmd("fillet", {"ref_a": f1, "ref_b": f2, "radius_nm": 1000000})
        self.assertIs(filleted.get("ok"), True, filleted)
        self.assertEqual(filleted["entities"], 7, "fillet adds exactly one arc")
        self.assertEqual(filleted["created"], 1)

        c1 = self.add_line(p1=(0, 60000000), p2=(20000000, 60000000))["uuid"]
        c2 = self.add_line(p1=(5000000, 50000000), p2=(5000000, 62000000))["uuid"]
        chamfered = self._cmd("chamfer", {"ref_a": c1, "ref_b": c2, "distance_nm": 1000000})
        self.assertIs(chamfered.get("ok"), True, chamfered)
        self.assertEqual(chamfered["entities"], 10, "chamfer adds exactly one line")
        self.assertEqual(chamfered["created"], 1)

    def test_snap_query_hits_or_reports_an_honest_zero_and_never_mutates(self):
        line = self.add_line(p1=(0, 0), p2=(10000000, 0))
        self.assertEqual(line["undo_depth"], 1, "exactly one undo entry so far")
        near = self._cmd("snap_query", {"point_nm": [1000, 1000], "tolerance_nm": 5000000})
        self.assertIs(near.get("ok"), True, near)
        self.assertEqual(near["hit"], 1, "a point ~1414 nm from the (0,0) end must hit")
        self.assertIsNotNone(near["snap"])
        self.assertEqual(near["snap"]["point_nm"], [0, 0])
        self.assertIsInstance(near["snap"]["distance_nm"], int)
        self.assertGreaterEqual(near["snap"]["distance_nm"], 0)
        self.assertLessEqual(near["snap"]["distance_nm"], 5000000)
        self.assertEqual(near["tolerance_nm"], 5000000, "the tolerance is echoed, not hidden")
        far = self._cmd("snap_query", {"point_nm": [5000000, 900000000], "tolerance_nm": 1000})
        self.assertIs(far.get("ok"), True, far)
        self.assertEqual(far["hit"], 0, "nothing within 1000 nm: an honest zero")
        self.assertIsNone(far["snap"], "no candidate must be null, not a fabricated hit")
        self.assertEqual(far["tolerance_nm"], 1000)
        self.assertEqual(self.state()["entities"], 1, "snap_query must not mutate the document")
        _status, undone = self.server.post("/api/command", {"command": "undo", "args": {}})
        self.assertEqual(undone["undo_depth"], 0,
                         "queries must not push undo entries: one undo clears the stack")
        self.assertEqual(undone["entities"], 0)

    def test_delete_constraint_unknown_uuid_fails_loudly(self):
        self._cmd("add_rectangle", {"p1_nm": [0, 0], "p2_nm": [10000000, 5000000]})
        missing = self._cmd("delete_constraint",
                            {"constraint_uuid": "00000000-0000-0000-0000-000000000000"})
        self.assertIs(missing.get("ok"), False, missing)
        self.assertEqual(missing.get("error"), "command_failed", missing)
        self.assertEqual(self.state()["entities"], 4, "a failed delete must not mutate the doc")

    def test_delete_constraint_on_a_real_uuid_reports_dof(self):
        rect = self._cmd("add_rectangle", {"p1_nm": [0, 0], "p2_nm": [10000000, 5000000]})
        self.assertEqual(rect["created"], 4)
        uuid = self._first_constraint_uuid(self.state()["doc"])
        if not uuid:
            self.skipTest("NA: the L1 projection exposes no constraint uuid to delete")
        deleted = self._cmd("delete_constraint", {"constraint_uuid": uuid})
        self.assertIs(deleted.get("ok"), True, deleted)
        self.assertEqual(deleted["constraints"], 3, "4 rectangle constraints minus one")
        solve = deleted["solve"]
        self.assertIsInstance(solve["iterations"], int)
        self.assertGreaterEqual(solve["iterations"], 0)
        self.assertIsInstance(solve["dof"], int)
        self.assertEqual(self.state()["entities"], 4,
                         "deleting a constraint never drops geometry")


class TestDxfExport(ServerCase):
    def test_export_writes_bytes_and_packages_dxf_reads_them_back(self):
        if dxf_read is None:
            self.skipTest("NA: packages/dxf unavailable (%s)" % DXF_IMPORT_ERROR)
        self.add_line(p1=(0, 0), p2=(10000000, 0))
        self.add_line(p1=(0, 0), p2=(0, 5000000))
        self.server.post("/api/command", {"command": "add_circle",
                                          "args": {"center": [0, 0], "radius_nm": 2500000}})
        target = self.rel("export.dxf")
        status, body = self.server.post("/api/export/dxf", {"path": target})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertIsInstance(body["bytes"], int)
        self.assertGreater(body["bytes"], 0, "exported DXF must be non-empty bytes")
        path = self.abs_of("export.dxf")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.getsize(path), body["bytes"],
                         "reported bytes must equal the bytes really on disk")
        document = dxf_read(path)
        sections = {str(getattr(section, "name", "")).upper(): section
                    for section in document.sections}
        self.assertIn("ENTITIES", sections, "dxf reader found no ENTITIES section")
        kinds = [str(entity.type).upper() for entity in sections["ENTITIES"].entities]
        self.assertEqual(len(kinds), body["entities"],
                         "entity count must survive the DXF round trip: %r" % (kinds,))
        self.assertEqual(kinds.count("LINE"), 2, kinds)
        self.assertEqual(kinds.count("CIRCLE"), 1, kinds)
        self.assertIsInstance(body["dimension_entities"], int)
        self.assertGreaterEqual(body["dimension_entities"], 0)

    def test_sketch_dimensions_are_not_fabricated_into_dxf_entities(self):
        if dxf_read is None:
            self.skipTest("NA: packages/dxf unavailable (%s)" % DXF_IMPORT_ERROR)
        line = self.add_line(p1=(0, 0), p2=(10000000, 0))
        _status, dim = self.server.post("/api/command", {
            "command": "add_dimension",
            "args": {"kind": "linear", "refs": [[line["uuid"], "start"], [line["uuid"], "end"]],
                     "value_nm": 10000000}})
        self.assertIs(dim.get("ok"), True, dim)
        self.assertEqual(self.state()["dimensions"], 1)
        status, body = self.server.post("/api/export/dxf", {"path": self.rel("dim.dxf")})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body["document_dimensions"], 1)
        self.assertEqual(body["dimension_entities"], 0,
                         "L1 sketch dimensions carry no sheet placement: 0 is the honest count")
        with open(self.abs_of("dim.dxf"), "rb") as handle:
            payload = handle.read()
        self.assertIn(b"999", payload,
                      "the dimension must be recorded as a 999 comment, not silently dropped")


class TestPersistence(ServerCase):
    def test_save_open_round_trip_keeps_entities_and_l1_text(self):
        self.add_line(p1=(0, 0), p2=(10000000, 0))
        self.add_line(p1=(0, 0), p2=(0, 10000000))
        saved_state = self.state()
        status, saved = self.server.post("/api/save", {"path": self.rel("doc.json")})
        self.assertEqual(status, 200)
        self.assertIs(saved.get("ok"), True, saved)
        self.assertGreater(saved["bytes"], 0)
        self.assertTrue(os.path.isfile(self.abs_of("doc.json")))
        self.assertGreater(os.path.getsize(self.abs_of("doc.json")), 0)
        self.add_line(p1=(1, 1), p2=(2, 2))  # drift the live doc away from the saved one
        self.assertEqual(self.state()["entities"], 3)
        status, opened = self.server.get("/api/open?path=" + self.rel("doc.json"))
        self.assertEqual(status, 200)
        self.assertIs(opened.get("ok"), True, opened)
        self.assertEqual(opened["entities"], saved_state["entities"], "entities must survive")
        self.assertEqual(opened["doc"], saved_state["doc"],
                         "the L1 text source of truth must be byte-identical after a round trip")
        self.assertEqual(self.state()["entities"], 2, "open() must replace the live document")

    def test_open_missing_file_reports_error_instead_of_empty_document(self):
        status, body = self.server.get("/api/open?path=" + self.rel("does-not-exist.json"))
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), False, body)
        self.assertEqual(body.get("error"), "file_not_found")


class TestRouting(ServerCase):
    def test_unknown_routes_are_404_json_on_both_methods(self):
        for method in ("GET", "POST"):
            status, _headers, raw = self.server.request(method, "/api/definitely-not-a-route",
                                                        {} if method == "POST" else None)
            self.assertEqual(status, 404, "%s must 404" % method)
            body = json.loads(raw.decode("utf-8"))
            self.assertIs(body.get("ok"), False)
            self.assertEqual(body.get("error"), "not_found")
        _status, body = self.server.get("/api/state")
        self.assertIs(body.get("ok"), True, "known routes must stay HTTP 200")


class TestPathTraversal(ServerCase):
    def test_path_traversal_is_rejected_with_positive_control(self):
        escape_name = "u4_escape_%d.json" % os.getpid()
        outside_app = os.path.join(os.path.dirname(_APP_DIR), escape_name)
        outside_tmp = os.path.join("/tmp", escape_name)
        self.addCleanup(lambda: os.path.exists(outside_app) and os.remove(outside_app))
        self.addCleanup(lambda: os.path.exists(outside_tmp) and os.remove(outside_tmp))

        for raw_path in ("../%s" % escape_name, "/tmp/%s" % escape_name,
                         "..\\%s" % escape_name, "", "build/../../%s" % escape_name):
            status, body = self.server.post("/api/save", {"path": raw_path})
            self.assertEqual(status, 200, "rejections are HTTP 200 + ok:false: %r" % (body,))
            self.assertIs(body.get("ok"), False, "path %r must be rejected" % raw_path)
            self.assertEqual(body.get("error"), "path_rejected", body)

        status, body = self.server.get("/api/open?path=../../../../etc/passwd")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), False, body)
        self.assertEqual(body.get("error"), "path_rejected", body)

        status, _headers, _raw = self.server.request("GET", "/web/../server.py")
        self.assertEqual(status, 404, "static traversal must not serve server.py")

        self.assertFalse(os.path.exists(outside_app), "escaped write landed outside the app dir")
        self.assertFalse(os.path.exists(outside_tmp), "escaped write landed in /tmp")

        # 阳性对照：根内正常相对路径必须成功 —— 否则"全拒绝"也能骗过穿越测试。
        status, ok = self.server.post("/api/save", {"path": self.rel("inside.json")})
        self.assertEqual(status, 200)
        self.assertIs(ok.get("ok"), True, ok)
        self.assertTrue(os.path.isfile(self.abs_of("inside.json")))

    def test_percent_encoded_traversal_is_rejected_too(self):
        status, body = self.server.get("/api/open?path=%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), False, body)
        self.assertIn(body.get("error"), ("path_rejected", "file_not_found"), body)


class TestStaticShell(ServerCase):
    def test_index_has_the_frozen_dom_contract_and_no_external_links(self):
        status, headers, raw = self.server.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = raw.decode("utf-8")
        for dom_id in DOM_IDS:
            self.assertIn('id="%s"' % dom_id, html, "index.html lacks #%s" % dom_id)
        for tool in TOOL_NAMES:
            self.assertIn('data-tool="%s"' % tool, html, "toolbar lacks tool %r" % tool)
        self.assertIn('data-i18n="tool.line"', html)
        self.assertIn('class="tool"', html)
        for forbidden in ("http://", "https://", "//cdn", "cdn."):
            self.assertNotIn(forbidden, html, "index.html must not link outside the app: %r" % forbidden)

    def test_static_assets_are_served_from_web_dir(self):
        for rel in ("web/index.html", "web/js/app.js", "web/css/app.css"):
            status, _headers, raw = self.server.request("GET", "/" + rel)
            self.assertEqual(status, 200, rel)
            self.assertGreater(len(raw), 0, rel)
        status, _headers, _raw = self.server.request("GET", "/web/does-not-exist.js")
        self.assertEqual(status, 404)


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


class TestPdfExport(ServerCase):
    """③ PDF 接线（`sw_drawings_extra.render_pdf`，纯标准库写出器）。

    判据三条：文件头真是 `%PDF-`（`magic` 由**现读落盘文件头**得到）、内容流非空、
    绝对路径 / `..` / NUL 逃逸一律 `path_rejected`（并带**阳性对照**，防"全拒"骗过穿越测试）。
    """

    def export_pdf(self, args=None):
        return self.server.post("/api/command",
                                {"command": "export_pdf", "args": dict(args or {})})

    def test_export_pdf_writes_real_pdf_and_reads_magic_from_disk(self):
        self.add_line()
        status, body = self.export_pdf({"out": self.rel("sheet.pdf")})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        target = body["path"]
        self.assertEqual(os.path.realpath(target), os.path.realpath(self.abs_of("sheet.pdf")))
        with open(target, "rb") as handle:
            head = handle.read(8)
            payload = handle.read()
        self.assertTrue(head.startswith(b"%PDF-"), head)
        self.assertEqual(body["magic"], head.decode("latin-1"),
                         "magic must come from the file header, not from a constant")
        self.assertEqual(body["bytes"], os.path.getsize(target))
        self.assertGreater(body["bytes"], 0)
        self.assertIn(b"xref", payload)
        self.assertIn(b"%%EOF", payload)

    def test_export_pdf_content_stream_is_not_empty(self):
        self.add_line()
        status, body = self.export_pdf({"out": self.rel("stream.pdf")})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertGreater(body["stream_bytes"], 0, "content stream must not be empty")
        with open(body["path"], "rb") as handle:
            text = handle.read().decode("latin-1")
        start = text.index("stream\n") + len("stream\n")
        stream = text[start:text.index("endstream", start)]
        self.assertGreater(len(stream), 0)
        self.assertIn("q", stream)
        self.assertIn("Q", stream)

    def test_export_pdf_rejects_path_escape_and_positive_control(self):
        for bad in ("/tmp/opencad-escape.pdf", "../escape.pdf",
                    "build/../../escape.pdf", "a\\b.pdf", "a\x00b.pdf"):
            status, body = self.export_pdf({"out": bad})
            self.assertEqual(status, 200, bad)
            self.assertIs(body.get("ok"), False, bad)
            self.assertEqual(body.get("error"), "path_rejected", bad)
        status, body = self.export_pdf({"out": self.rel("ok.pdf")})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True,
                      "positive control: in-root relative path must still work: %s" % body)

    def test_export_pdf_defaults_to_current_sheet_and_rejects_unknown_uuid(self):
        state = self.state()
        status, body = self.export_pdf({})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body.get("sheet_uuid"), state["sketch_uuid"])
        self.assertTrue(body.get("pdf_sheet_uuid"), "rendered sheet uuid must be reported")
        self.assertTrue(os.path.isfile(body["path"]))
        self.addCleanup(_unlink_quiet, body["path"])
        status, body = self.export_pdf({"out": self.rel("by-uuid.pdf"),
                                        "sheet_uuid": state["sketch_uuid"]})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        status, body = self.export_pdf({"out": self.rel("nope.pdf"),
                                        "sheet_uuid": "no-such-sheet"})
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), False)
        self.assertEqual(body.get("error"), "sheet_not_found")

    def test_pdf_button_is_wired_and_localised(self):
        html = open(os.path.join(_APP_DIR, "web/index.html"), encoding="utf-8").read()
        self.assertIn('data-action="export-pdf"', html)
        self.assertIn('data-tool="export_pdf"', html)
        self.assertIn('data-i18n="cmd.export.pdf"', html)
        state = self.state()
        self.assertIn("cmd.export.pdf", state["strings"])
        self.assertTrue(state["strings"]["cmd.export.pdf"])


class TestCliVerdictLines(ServerCase):
    def setUp(self) -> None:  # CLI 面不需要常驻服务
        self.assertTrue(os.path.isfile(SERVER_PY), "NA: server.py not found")

    def test_port_out_of_range_fails_loudly_with_rc_nonzero(self):
        done = subprocess.run([sys.executable, SERVER_PY, "--port", "70000"],
                              cwd=_APP_DIR, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(done.returncode, 0, "an unusable port must not exit 0")
        self.assertIn(VERDICT_FAIL, done.stdout)
        self.assertIn("reason=port_range", done.stdout)

    def test_selftest_judgement_line_passes(self):
        done = subprocess.run([sys.executable, SERVER_PY, "--selftest"],
                              cwd=_APP_DIR, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        line = [row for row in done.stdout.splitlines() if "selftest verdict=" in row]
        self.assertTrue(line, done.stdout)
        self.assertIn("verdict=PASS", line[0])
        self.assertNotIn("status=FAIL", done.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
