#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""test_a10_doc_command_surface —— 文档级命令面（图层面 / 出图面）真起服务、真打 HTTP。

背景
----
`server.py` 原本只有`_PASSTHROUGH`（草图级动词，第一实参 = 草图）。图层面（layer_*）与出图面
（plot_* / publish / layout / viewport）建在**真文档**上，没有入口 ⇒ 用户可达性为零
（autocad-organization #1/#2、autocad-output #3/#6/#9/#10/#11/#12/#13/#14/#15/#16 的 partial 都卡在这）。

本文件覆盖新增的 `_DOC_PASSTHROUGH`（第一实参 = `self.doc`）+ 只读动词不进撤销栈：

* 图层写路径：layer_create / layer_set(locked) / layer_off / layer_freeze / layer_state_set(current)；
* 图层读路径：layer_state（按状态列层）、layer_visible_entities（frozen/off 的**消费者**）；
* 对象改层：layer_assign（改层不动几何）；
* 出图面：lineweight / plot_flag / plot_area / plot_scale / model_space_plot / create_layout /
  manage_layout / publish(fmt=pdf,single_file) / preview / set_viewport_layer_override；
* 阴性对照：未知 verb 必须**结构化报错**（不许静默 ok），只读 verb 不许把撤销栈做深。

跑法::

    cd oss-alternatives/apps/opencad
    python3 -m unittest tests.test_a10_doc_command_surface -v
"""

from __future__ import annotations

import glob
import http.client as http_client
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
SERVER_PY = os.path.join(_APP_DIR, "server.py")
VERDICT_PASS = "OPENCAD_SERVER verdict=PASS"


class ServerProcess:
    """真服务：`server.py --port 0 --out-root <临时目录>`，从判定行读实际端口。"""

    def __init__(self, out_root: str) -> None:
        self.out_root = out_root
        self.proc = None
        self.port = 0

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
                break
            if line.startswith(VERDICT_PASS):
                self.port = int(line.split("port=")[1].split()[0])
                return self
        raise AssertionError("no startup verdict within 20s (rc=%s)" % (self.proc.poll(),))

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()

    def request(self, method: str, path: str, body=None):
        if self.proc is not None and self.proc.poll() is not None:
            raise AssertionError("server process died (rc=%s)" % self.proc.poll())
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=30)
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
            return resp.status, json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body):
        return self.request("POST", path, body)


class DocCommandSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.out_root = tempfile.mkdtemp(prefix="opencad-a10-")
        self.addCleanup(shutil.rmtree, self.out_root, True)
        self.server = ServerProcess(self.out_root).start()
        self.addCleanup(self.server.stop)

    # ── 夹具 ────────────────────────────────────────────────────────────────────────
    def command(self, verb: str, args: dict | None = None) -> dict:
        status, body = self.server.post("/api/command", {"command": verb, "args": args or {}})
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), True, body)
        return body

    def command_error(self, verb: str, args: dict | None = None) -> dict:
        status, body = self.server.post("/api/command", {"command": verb, "args": args or {}})
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), False, body)
        return body

    def doc_text(self) -> str:
        status, body = self.server.get("/api/state")
        self.assertEqual(status, 200, body)
        return str(body.get("doc") or "")

    # ── 夹具自证（E2.5）：先证明这条腿真跑得动 ─────────────────────────────────────
    def test_00_fixture_selfproof(self) -> None:
        line = self.command("add_line", {"p1": [0, 0], "p2": [10_000_000, 0]})
        self.assertTrue(line.get("uuid"), line)
        self.assertEqual(line.get("entities"), 1, line)

    # ── 图层写路径 + 读路径（autocad-organization #1） ──────────────────────────────
    def test_layer_state_writes_and_consumer(self) -> None:
        line = self.command("add_line", {"p1": [0, 0], "p2": [10_000_000, 0]})
        layer = self.command("layer_create", {"name": "WALL"})
        self.assertTrue(layer.get("uuid"), layer)
        self.command("layer_assign", {"refs": [line["uuid"]], "layer": "WALL"})
        # 关 / 冻结：各有写路径（当前层还停在 "0" —— AutoCAD 不许关/冻当前层）
        self.command("layer_off", {"layer": "WALL", "off": True})
        self.command("layer_freeze", {"layer": "WALL", "frozen": True})
        # 读路径：按状态列层 = 图层面板数据源
        off_ids = self.command("layer_state", {"state": "off"}).get("uuids") or []
        self.assertIn(layer["uuid"], off_ids, off_ids)
        frozen_ids = self.command("layer_state", {"state": "frozen"}).get("uuids") or []
        self.assertIn(layer["uuid"], frozen_ids, frozen_ids)
        # 消费者：frozen/off 必须真把实体从显示集里摘掉（不是只写一张没人读的表）
        hidden = self.command("layer_visible_entities", {"purpose": "display"}).get("uuids") or []
        self.assertNotIn(line["uuid"], hidden, hidden)
        self.command("layer_off", {"layer": "WALL", "off": False})
        self.command("layer_freeze", {"layer": "WALL", "frozen": False})
        shown = self.command("layer_visible_entities", {"purpose": "display"}).get("uuids") or []
        self.assertIn(line["uuid"], shown, shown)
        # 当前层切换（CLAYER 同口径）+ 切换后拿到的状态位是真读回来的
        self.command("layer_state_set", {"layer": "WALL", "current": True})
        current_ids = self.command("layer_state", {"state": "current"}).get("uuids") or []
        self.assertIn(layer["uuid"], current_ids, current_ids)
        # 阴性对照：当前层不许被关（拒绝理由要能机读，不是只回一句文案）
        refused = self.command_error("layer_off", {"layer": "WALL", "off": True})
        self.assertEqual(refused.get("cadcore_key"), "current_layer_off", refused)

    def test_layer_lock_blocks_and_unlock_restores(self) -> None:
        line = self.command("add_line", {"p1": [0, 0], "p2": [10_000_000, 0]})
        self.command("layer_create", {"name": "WALL"})
        self.command("layer_assign", {"refs": [line["uuid"]], "layer": "WALL"})
        self.command("layer_set", {"layer": "WALL", "locked": True})
        blocked = self.command_error("layer_assign", {"refs": [line["uuid"]], "layer": "0"})
        self.assertEqual(blocked.get("cadcore_key"), "layer_locked", blocked)
        self.command("layer_set", {"layer": "WALL", "locked": False})
        moved = self.command("layer_assign", {"refs": [line["uuid"]], "layer": "0"})
        self.assertEqual(moved.get("uuids"), [line["uuid"]], moved)

    # ── 只读 verb 不许把撤销栈做深（三态：读 ≠ 写） ────────────────────────────────
    def test_readonly_verb_does_not_push_undo(self) -> None:
        self.command("layer_create", {"name": "WALL"})   # 状态账先存在（空表 ⇒ no_layer_table，是另一条判据）
        depth_before = self.command("layer_state", {"state": "all"}).get("undo_depth")
        self.server.post("/api/command", {"command": "layer_state", "args": {"state": "all"}})
        status, body = self.server.post("/api/command", {"command": "layer_state", "args": {"state": "all"}})
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("undo_depth"), depth_before, body)

    # ── 出图面（autocad-output #3/#6/#9/#10/#11/#12/#13/#14/#15/#16） ────────────────
    def test_output_surface_end_to_end(self) -> None:
        line = self.command("add_line", {"p1": [0, 0], "p2": [100_000_000, 60_000_000]})
        layout = self.command("create_layout", {"name": "L1", "standard": "iso", "size": "A3",
                                                "scale": "1:1"})
        sheet = layout["uuid"]
        self.assertTrue(sheet, layout)
        # 线宽 / 可打印（LWEIGHT / Plot-No Plot）
        self.command("lineweight", {"layer": "0", "lineweight_mm": 0.35})
        self.command("plot_flag", {"layer": "0", "plottable": False})
        self.command("plot_flag", {"layer": "0", "plottable": True})
        # 打印区域 / 比例 / 模型空间出图
        self.command("plot_area", {"sheet": sheet, "area": "extents"})
        self.command("plot_scale", {"sheet": sheet, "scale": "1:2"})
        model = self.command("model_space_plot", {"device": "DWG To PDF.pc3", "area": "extents"})
        self.assertTrue(model.get("uuid"), model)
        # 布局管理：rename（copy / delete 见 manage_layout 契约）
        renamed = self.command("manage_layout", {"action": "rename", "name": "L1", "new_name": "L2"})
        self.assertTrue(renamed.get("uuid"), renamed)
        # 视口 + 视口内图层替代（VPLAYER / VP Freeze）
        viewport = self.command("create_viewport", {"sheet_uuid": sheet})
        self.assertTrue(viewport.get("uuid"), viewport)
        self.command("set_viewport_scale", {"sheet_uuid": sheet, "view_uuid": viewport["uuid"],
                                            "scale": "1:1", "locked": True})
        # 视口内图层替代挂的是**图纸自己的层表**（开视口时建的 FRAME/VISIBLE/... 那几张），
        # 不是草图层的 "0"（用 "0" 会如实回 unknown_layer —— 层表就是真源，不猜）
        override = self.command("set_viewport_layer_override",
                                {"sheet_uuid": sheet, "view_uuid": viewport["uuid"],
                                 "layer": "VISIBLE", "state": "frozen"})
        self.assertTrue(override.get("uuid"), override)
        # 打印预览：冻结窄签名要 sheet_uuid+out **成对**（缺一如实回 bad_reference，不出一张空图）。
        # 路径给**落盘根内的绝对路径**（相对名会被钉到进程 cwd；根外路径 `path_rejected`）。
        preview_out = os.path.join(self.out_root, "preview_1")
        preview = self.command("preview", {"sheet_uuid": sheet, "out": preview_out})
        self.assertTrue(preview.get("uuid"), preview)
        rendered = sorted(glob.glob(preview_out + "*"))
        self.assertTrue(rendered, "预览没落产物：%s" % sorted(os.listdir(self.out_root)))
        self.assertGreater(os.path.getsize(rendered[0]), 0)
        # 阴性对照：落盘根之外的路径必须被结构化拒（越权写盘没门）
        escaped = self.command_error("preview", {"sheet_uuid": sheet,
                                                 "out": "/tmp/a10-escape"})
        self.assertEqual(escaped.get("error"), "path_rejected", escaped)
        # 批量发布（单张多页 PDF）
        published = self.command("publish", {"sheet_uuids": [sheet], "fmt": "pdf",
                                             "out_dir": tmp_publish_dir(self.out_root)})
        self.assertTrue(published.get("uuid"), published)
        self.assertNotIn(line["uuid"], [], "实体 uuid 与出图链路无冲突")

    def test_unknown_verb_is_structured_error(self) -> None:
        body = self.command_error("no_such_verb_a10", {})
        self.assertEqual(body.get("error"), "unknown_command", body)


def tmp_publish_dir(out_root: str) -> str:
    path = os.path.join(out_root, "publish")
    os.makedirs(path, exist_ok=True)
    return path


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    unittest.main(verbosity=2)
