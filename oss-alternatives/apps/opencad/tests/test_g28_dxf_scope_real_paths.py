#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G28 SCOPE 两条**真路径**的定向测试：HTTP `/api/export/dxf` 端到端 + `ops.output.dxf_export` 面。

被测对象（都是产品真件，没有替身）
----------------------------------
1. `oss-alternatives/apps/opencad/server.py` 的 `POST /api/export/dxf`：本文件**不 import 服务端**，
   一律 `subprocess` 起真进程（`--port 0`，端口从它自己打的那行
   `OPENCAD_SERVER verdict=PASS port=<n> routes=<n>` 里读），再用标准库 `http.client` 打真 HTTP：
   先 `/api/command` 造几何，再 `/api/export/dxf` 导出，最后**从磁盘读回字节**逐条对账。
2. `oss-alternatives/packages/cadcore/cadcore/ops/output.py` 的 `dxf_export`：真落盘、真记账
   （`doc.extra["output"]["exports"]`）、`fmt="binary"` 真结构化拒绝。

为什么单独立一个文件
--------------------
G28 保真门（`oss-alternatives/tools/cad/dxf_fidelity_gate.py --check`）把这两条从 SCOPE 的
「不覆盖」移进了「覆盖」，门的判据句柄在门内；本文件是**同一批真路径在应用测试面的回归保障**：
门跑不动时（或有人只跑应用测试时）这两条仍然有人看着。判据与门同源，不另立一套口径。

跑法
----
    python3 -m unittest discover -s oss-alternatives/apps/opencad/tests -v
    python3 oss-alternatives/apps/opencad/tests/test_g28_dxf_scope_real_paths.py -v

产物：每个测试只写自己的外置临时目录，先停止服务器再清理。
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from http import client as http_client

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_HERE)
_OSS_ROOT = os.path.dirname(os.path.dirname(_APP_DIR))
_CADCORE = os.path.join(_OSS_ROOT, "packages", "cadcore")
_DXF_PKG = os.path.join(_OSS_ROOT, "packages", "dxf")
for _path in (_CADCORE, _DXF_PKG):
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

SERVER_PY = os.path.join(_APP_DIR, "server.py")
STARTUP_PASS = "OPENCAD_SERVER verdict=PASS"
ARTIFACT_ROOT = "/hf3fs-jd/data/tmp/opencad-3d-green-20261010"

MM = 1_000_000
LINE_P1 = (1_234_567, -987_654_321)
LINE_P2 = (2 ** 53, 3)
CIRCLE_CENTER = (1, 2)
CIRCLE_RADIUS_NM = 25_400_001
POLYLINE = ((0, 0), (10 * MM, 0), (10 * MM, 5 * MM))


class HttpServer:
    """真服务：`subprocess` 起 `server.py --port 0`，从判定行读端口；`stop()` 幂等且必杀。"""

    def __init__(self, out_root: str) -> None:
        self.proc = None
        self.port = 0
        self.routes = -1
        self.startup_line = ""
        self.out_root = out_root

    def start(self) -> "HttpServer":
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_PY, "--host", "127.0.0.1", "--port", "0",
             "--out-root", self.out_root],
            cwd=_APP_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            ready, _w, _x = select.select([self.proc.stdout], [], [], 0.2)
            if not ready:
                if self.proc.poll() is not None:
                    break
                continue
            line = (self.proc.stdout.readline() or "").strip()
            if not line:
                break
            if line.startswith("OPENCAD_SERVER verdict=FAIL"):
                raise AssertionError("server refused to start: %s" % line)
            if line.startswith(STARTUP_PASS):
                self.startup_line = line
                self.port = int(line.split("port=")[1].split()[0])
                self.routes = int(line.split("routes=")[1].split()[0])
                return self
        raise AssertionError("no verdict line within 20s (rc=%s)" % self.proc.poll())

    def request(self, method: str, route: str, body=None):
        """真 HTTP（`http.client`：路径原样发出，不被 urllib 规范化掉）。"""
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload, headers = None, {}
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers = {"Content-Type": "application/json",
                           "Content-Length": str(len(payload))}
            conn.request(method, route, body=payload, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()

    def post(self, route: str, payload):
        return self.request("POST", route, body=payload)

    def get(self, route: str):
        return self.request("GET", route)

    def stop(self) -> None:
        if self.proc is None:
            return
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
        self.proc = None


class RealPathCase(unittest.TestCase):
    """每个测试自己起一个真服务、自己收尸；产物目录收尾即删。"""

    def setUp(self) -> None:
        self.assertTrue(os.path.isfile(SERVER_PY), "NA: server.py not found at %s" % SERVER_PY)
        workspace = tempfile.TemporaryDirectory(
            prefix="g28scope-", dir=os.environ.get("OPENCAD_OUT_ROOT", ARTIFACT_ROOT))
        self.addCleanup(workspace.cleanup)
        self.artifacts = os.path.realpath(workspace.name)
        self.server = HttpServer(self.artifacts)
        self.addCleanup(self.server.stop)
        self.server.start()

    def export(self, name: str):
        """真打一次 `/api/export/dxf`；返回 `(status, body, 磁盘字节)`。"""
        status, body = self.server.post("/api/export/dxf", {"path": name})
        target = os.path.join(self.artifacts, name)
        with open(target, "rb") as handle:
            payload = handle.read() if os.path.isfile(target) else b""
        return status, body, payload

    def add_geometry(self) -> None:
        for verb, args in (
                ("add_line", {"p1": list(LINE_P1), "p2": list(LINE_P2)}),
                ("add_circle", {"center": list(CIRCLE_CENTER), "radius_nm": CIRCLE_RADIUS_NM}),
                ("add_polyline", {"points_nm": [list(pair) for pair in POLYLINE]})):
            status, body = self.server.post("/api/command", {"command": verb, "args": args})
            self.assertEqual(status, 200)
            self.assertIs(body.get("ok"), True, body)


class TestHttpDxfRealPath(RealPathCase):
    def test_fixture_talks_to_a_real_server_process_not_a_stub(self) -> None:
        """夹具自证：真子进程、真端口、未知路由真 404（桩不会有这个行为）。"""
        self.assertIsNotNone(self.server.proc)
        self.assertNotEqual(self.server.proc.pid, os.getpid())
        self.assertGreater(self.server.port, 0)
        self.assertIn("verdict=PASS", self.server.startup_line)
        status, body = self.server.get("/api/g28scope-not-a-route")
        self.assertEqual(status, 404)
        self.assertIs(body.get("ok"), False)
        self.assertEqual(body.get("error"), "not_found")

    def test_http_export_bytes_match_response_and_survive_reread(self) -> None:
        from cadcore.io import dxf_bridge

        self.add_geometry()
        status, body, payload = self.export("export.dxf")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertTrue(payload, "the exported DXF must be real bytes on disk")
        self.assertEqual(len(payload), body["bytes"],
                         "response bytes must equal the bytes really on disk")
        self.assertEqual(os.path.realpath(body["path"]),
                         os.path.realpath(os.path.join(self.artifacts, "export.dxf")))
        entities = dxf_bridge.read_dxf(payload).entities()
        kinds = [str(getattr(entity, "type", "")) for entity in entities]
        self.assertEqual(len(kinds), body["entities"])
        self.assertEqual(kinds.count("LINE"), 1, kinds)
        self.assertEqual(kinds.count("CIRCLE"), 1, kinds)
        self.assertEqual(kinds.count("LWPOLYLINE"), 1, kinds)
        self.assertEqual(entities[0].tag(10).raw_value, "1.234567",
                         "the first LINE start.x must be the exact nm value in mm")

    def test_export_is_byte_identical_on_a_second_real_request(self) -> None:
        self.add_geometry()
        _status, _body, first = self.export("twice.dxf")
        status, body, second = self.export("twice.dxf")
        self.assertEqual(status, 200)
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest(),
                         "two real exports of the same document must be byte-identical")

    def test_out_of_root_export_path_is_rejected_and_writes_nothing(self) -> None:
        self.add_geometry()
        escaped = os.path.join(self.artifacts, "..", "g28scope-escaped.dxf")
        status, body = self.server.post("/api/export/dxf", {"path": "../g28scope-escaped.dxf"})
        self.assertEqual(status, 200, "business failures are 200 + ok:false")
        self.assertIs(body.get("ok"), False, body)
        self.assertEqual(body.get("error"), "path_rejected", body)
        self.assertFalse(os.path.exists(escaped), "a rejected path must not write anything")


class TestDxfExportFace(unittest.TestCase):
    """`ops.output.dxf_export`：真落盘 / 真记账 / 二进制结构化拒绝。"""

    def setUp(self) -> None:
        from cadcore.ops import output as output_ops

        self.output = output_ops
        self.workdir = tempfile.mkdtemp(prefix="g28scope-export-")
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.document = {"unit": "in", "sketches": [{"entities": [
            {"uuid": "g28scope-line", "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 1_234_567, "y1": -987_654_321,
                      "x2": 9_007_199_254_740_992, "y2": 3}},
            {"uuid": "g28scope-circle", "kind": "circle", "layer": "0", "construction": False,
             "geom": {"cx": 1, "cy": 2, "r": 25_400_001}},
        ]}]}

    def records(self):
        return self.document["output"]["exports"]

    def test_disk_record_and_dry_run_and_binary_refusal(self) -> None:
        from cadcore.io import dxf_bridge

        out_path = os.path.join(self.workdir, "ops.dxf")
        uuids = self.output.dxf_export(self.document, version="R12", fmt="ascii",
                                       out_path=out_path, dry_run=False)
        self.assertEqual(len(uuids), 1, "op(doc) -> list[str] contract")
        with open(out_path, "rb") as handle:
            payload = handle.read()
        self.assertTrue(payload, "dxf_export must really write bytes")
        record = self.records()[-1]
        self.assertEqual(uuids[0], record["uuid"])
        self.assertIs(record["written"], True)
        self.assertIs(record["dry_run"], False)
        self.assertEqual(record["out_path"], out_path)
        self.assertEqual(record["content_bytes"], len(payload))
        self.assertEqual(record["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(record["format"], "ascii")
        self.assertEqual(record["dxf_version"], "R12")
        self.assertEqual(record["acadver"], "AC1009")
        self.assertEqual(record["entity_count"], len(dxf_bridge.read_dxf(payload).entities()))

        dry_path = os.path.join(self.workdir, "dry.dxf")
        dry_uuids = self.output.dxf_export(self.document, version="R12", fmt="ascii",
                                           out_path=dry_path, dry_run=True)
        dry_record = self.records()[-1]
        self.assertFalse(os.path.exists(dry_path), "dry_run=True must write nothing")
        self.assertIs(dry_record["written"], False)
        self.assertIs(dry_record["dry_run"], True)
        self.assertEqual(dry_uuids, [dry_record["uuid"]])

        before = len(self.records())
        binary_path = os.path.join(self.workdir, "binary.dxf")
        with self.assertRaises(Exception) as ctx:
            self.output.dxf_export(self.document, version="R12", fmt="binary",
                                   out_path=binary_path, dry_run=False)
        self.assertEqual(getattr(ctx.exception, "reason", ""), "unsupported_format",
                         "binary DXF is declared debt: it must refuse in a structured way")
        self.assertEqual(len(self.records()), before, "a refused export must not be recorded")
        self.assertFalse(os.path.exists(binary_path), "a refused export must not write")


if __name__ == "__main__":
    unittest.main(verbosity=2)
