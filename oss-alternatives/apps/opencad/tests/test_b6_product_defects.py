"""test_b6_product_defects —— C 线 B6 产品面缺陷回归（opencad API 侧 7 条 A 类）。

缺陷源：`/hf3fs-jd/data/tmp/cad-scan/scan.json` 的 `defects_A`（7 条）与 `defect_root_causes`（3 条）。
本文件按"**真起服务、真打 HTTP**"的体例写（与 `test_server.py` 同一取舍：不 import server 内部函数），
每条缺陷一组断言，改回去立刻报红。

覆盖（缺陷 id → 断言）
----------------------
- `endpoint:POST /api/standard` / `illegal:standard_bogus` / `illegal:standard_null`（RC1）：
  缺失 / 非字符串 / 未知值 ⇒ **HTTP 400** + `ok:false` + 机读 error 码 + 非空 `detail` +
  `known` 可用值列表；被拒时**不改**当前标准档；合法值照旧 200（阳性对照）。
- `edit:undo` / `edit:redo`（RC3）：文档里有一条 polyline 时，undo/redo 之后的 L1 文本
  必须与目标状态**逐字节相同**（旧行为：多出一条 `unknown_field_preserved` 伪诊断）；
  同场景**去掉 polyline** 的阴性对照必须一直绿（证明这条尺子不是恒红）。
- `roundtrip:with_polyline`（RC3）：save → open 往返逐字节相同 + 读回不带伪诊断。
- `extreme:huge_int_309digit`（RC2）：309 位整数坐标不再被接受；此后导出不得冒裸
  `OverflowError`，也不许留下 0 字节产物；盘上已有的越界文档在**装载闸**被结构化拒收
  （`invalid_document` + 逐字段原因），不进工作区。

跑法::

    cd oss-alternatives/apps/opencad
    python3 -m unittest discover -s tests -t .          # 契约命令
    python3 tests/test_b6_product_defects.py -v        # 单跑本文件

产物：本文件只写自己 `tempfile.mkdtemp()` 出来的 out-root，收尾 `rmtree`，**仓内不留产物**。
"""

from __future__ import annotations

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

_KNOWN = ["iso", "gbt", "ansi", "din", "jis"]
_BASE_DOC = {
    "schema_version": 1,
    "meta": {"doc_uuid": "b6000000-0000-4000-8000-0000000000aa",
             "created_at": "2026-10-01T00:00:00Z", "app_version": "0.1.0",
             "title_key": "doc.untitled"},
    "unit": "mm", "standard_profile": "gbt", "projection_angle": "first",
    "sketches": [{"uuid": "b6000000-0000-4000-8000-0000000000ab", "plane": "XY",
                  "entities": [], "constraints": [], "dimensions": [], "solved": None}],
    "features": [], "annotations": [], "sheets": [],
}


class ServerProcess:
    """真服务：`server.py --port 0 --out-root <临时目录>`，从判定行读实际端口。"""

    def __init__(self, out_root: str) -> None:
        self.out_root = out_root
        self.proc: "subprocess.Popen | None" = None
        self.port = 0
        self.startup_line = ""

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
                self.startup_line = line
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

    def request(self, method: str, path: str, body: object = None):
        if self.proc is not None and self.proc.poll() is not None:
            raise AssertionError("server process died (rc=%s)" % self.proc.poll())
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=20)
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

    def post(self, path: str, body: object):
        return self.request("POST", path, body)


class B6Case(unittest.TestCase):
    """每个测试自己起一个真服务器 + 一个自己的 out-root（顺序无关，收尾即删）。"""

    def setUp(self) -> None:
        self.assertIsNot(len(_KNOWN), 0, "夹具自证：已知标准档清单非空")
        self.out_root = tempfile.mkdtemp(prefix="opencad-b6-")
        self.addCleanup(shutil.rmtree, self.out_root, True)
        os.makedirs(os.path.join(self.out_root, "cases"), exist_ok=True)
        self._write_case("_base_empty.json", _BASE_DOC)
        self.server = ServerProcess(self.out_root).start()
        self.addCleanup(self.server.stop)

    # ── 夹具 ────────────────────────────────────────────────────────────────────────
    def _write_case(self, name: str, payload: object) -> str:
        path = os.path.join(self.out_root, "cases", name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        return path

    def open_case(self, name: str) -> dict:
        status, body = self.server.get("/api/open?path=" + "cases/" + name)
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), True, body)
        return body

    def command(self, verb: str, args: dict | None = None) -> dict:
        status, body = self.server.post("/api/command", {"command": verb, "args": args or {}})
        self.assertEqual(status, 200, body)
        return body

    def state(self) -> dict:
        status, body = self.server.get("/api/state")
        self.assertEqual(status, 200, body)
        return body

    def doc_text(self) -> str:
        return str(self.state().get("doc") or "")

    def build_corner(self, *, with_polyline: bool = True) -> dict:
        """与 scan 的 `corner` 夹具同形（第 5 条几何 = polyline，正是 RC3 的触发点）。"""
        mm = 1_000_000
        self.open_case("_base_empty.json")
        line1 = self.command("add_line", {"p1": [0, 0], "p2": [30 * mm, 0]})["uuid"]
        self.command("add_line", {"p1": [30 * mm, 0], "p2": [30 * mm, 30 * mm], "layer": "WALL"})
        self.command("add_circle", {"center": [10 * mm, 10 * mm], "radius_nm": 5 * mm})
        self.command("add_arc", {"center": [0, 0], "radius_nm": 20 * mm,
                                 "start_deg": 0, "end_deg": 90})
        if with_polyline:
            self.command("add_polyline",
                         {"points_nm": [[0, 40 * mm], [10 * mm, 45 * mm], [20 * mm, 40 * mm]],
                          "layer": "POLY"})
        return {"L1": line1}


class StandardParamValidationTests(B6Case):
    """RC1：`/api/standard` 的参数校验（非法/缺失 ⇒ 结构化 400 + detail + known）。"""

    def assert_structured_400(self, body: dict, label: str) -> None:
        status, payload = self.server.post("/api/standard", body)
        self.assertEqual(status, 400, "%s: 非法参数必须 400，实际 %s %r" % (label, status, payload))
        self.assertIs(payload.get("ok"), False, payload)
        self.assertNotEqual(payload.get("error"), "internal_error",
                            "%s: 不许落兜底 internal_error" % label)
        self.assertTrue(str(payload.get("error") or "").strip(), payload)
        self.assertTrue(str(payload.get("detail") or "").strip(), payload)
        self.assertEqual(payload.get("known"), _KNOWN, "%s: known 必须是可用值提示" % label)
        self.assertNotIn("I18nError", json.dumps(payload, ensure_ascii=False),
                         "%s: 不许把内部异常串塞进响应" % label)

    def test_missing_standard_is_structured_400(self) -> None:
        self.assert_structured_400({}, "missing")

    def test_bogus_standard_is_structured_400(self) -> None:
        self.assert_structured_400({"standard": "bogus"}, "bogus")

    def test_null_standard_is_structured_400(self) -> None:
        self.assert_structured_400({"standard": None}, "null")

    def test_non_string_standard_is_structured_400(self) -> None:
        self.assert_structured_400({"standard": 123}, "int")

    def test_rejected_standard_does_not_change_document(self) -> None:
        before = self.state()
        for body in ({}, {"standard": "bogus"}, {"standard": None}):
            status, _payload = self.server.post("/api/standard", body)
            self.assertEqual(status, 400)
        after = self.state()
        self.assertEqual(after.get("standard"), before.get("standard"),
                         "被拒的标准档请求不许改动文档状态")
        self.assertEqual(after.get("projection"), before.get("projection"))
        self.assertEqual(after.get("doc"), before.get("doc"))

    def test_valid_standard_still_switches(self) -> None:
        """阳性对照：把坏的拦住，不许把好的也拦住。"""
        for standard, projection in (("ansi", "third"), ("iso", "NA"), ("gbt", "first")):
            status, body = self.server.post("/api/standard", {"standard": standard})
            self.assertEqual(status, 200, body)
            self.assertIs(body.get("ok"), True, body)
            self.assertEqual(body.get("standard"), standard)
            self.assertEqual(body.get("projection"), projection, body)
            self.assertEqual(body.get("standards"), _KNOWN, body)


class UndoRedoTests(B6Case):
    """RC3 的产品面症状：undo/redo `ok:true` 但状态没回到目标态。"""

    def _undo_redo_sequence(self, *, with_polyline: bool) -> tuple[str, str, str, str]:
        uuids = self.build_corner(with_polyline=with_polyline)
        pre = self.doc_text()
        body = self.command("move", {"refs": [uuids["L1"]], "dx_nm": 7_000_000,
                                     "dy_nm": 3_000_000})
        self.assertIs(body.get("ok"), True, body)
        post = self.doc_text()
        self.assertNotEqual(pre, post, "夹具自证：move 必须真的改动文档")
        undo = self.command("undo")
        self.assertEqual(undo.get("undo_depth"), int(body.get("undo_depth")) - 1,
                         "undo 之后撤销栈必须恰好浅一层：%r" % (undo,))
        self.assertEqual(undo.get("redo_depth"), 1, undo)
        after_undo = self.doc_text()
        redo = self.command("redo")
        self.assertIs(redo.get("ok"), True, redo)
        self.assertEqual(redo.get("redo_depth"), 0, redo)
        after_redo = self.doc_text()
        return pre, post, after_undo, after_redo

    def test_undo_redo_restore_l1_byte_identically_with_polyline(self) -> None:
        pre, post, after_undo, after_redo = self._undo_redo_sequence(with_polyline=True)
        self.assertEqual(after_undo, pre, "undo 之后的 L1 必须与移动前逐字节相同")
        self.assertEqual(after_redo, post, "redo 之后的 L1 必须与移动后逐字节相同")
        self.assertNotIn("unknown_field_preserved", after_undo,
                         "自家写的 polyline 不许在读回时变成伪诊断")

    def test_undo_redo_control_without_polyline_stays_green(self) -> None:
        """阴性对照：没有 polyline 时这条路本来就是绿的 —— 证明上面的红不是尺子恒红。"""
        pre, post, after_undo, after_redo = self._undo_redo_sequence(with_polyline=False)
        self.assertEqual(after_undo, pre)
        self.assertEqual(after_redo, post)


class RoundTripTests(B6Case):
    """RC3 的独立复现面：save → open 往返稳定性。"""

    def test_save_open_roundtrip_is_byte_stable_with_polyline(self) -> None:
        self.build_corner(with_polyline=True)
        before = self.doc_text()
        status, saved = self.server.post("/api/save", {"path": "cases/rt_polyline.json"})
        self.assertEqual(status, 200, saved)
        self.assertIs(saved.get("ok"), True, saved)
        self.open_case("rt_polyline.json")
        after = self.doc_text()
        self.assertEqual(after, before, "save → open 往返必须逐字节相同")
        self.assertNotIn("unknown_field_preserved", after)

    def test_save_open_control_without_polyline_stays_green(self) -> None:
        self.build_corner(with_polyline=False)
        before = self.doc_text()
        self.server.post("/api/save", {"path": "cases/rt_control.json"})
        self.open_case("rt_control.json")
        self.assertEqual(self.doc_text(), before)


class ExtremeCoordinateTests(B6Case):
    """RC2：极值坐标不再被接受；下游导出不冒裸内建异常、不留空产物。"""

    def test_huge_int_add_is_rejected_structurally(self) -> None:
        self.open_case("_base_empty.json")
        big = int("9" * 309)
        status, body = self.server.post("/api/command", {
            "command": "add_line", "args": {"p1": [big, 0], "p2": [1_000_000, 0]}})
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), False, "309 位整数坐标不许被接受：%r" % (body,))
        self.assertTrue(str(body.get("error") or "").strip(), body)
        self.assertNotIn("OverflowError", json.dumps(body, ensure_ascii=False), body)
        self.assertEqual(self.state().get("entities"), 0, "拒收的坐标不许落进文档")

    def test_export_after_rejected_add_is_not_a_crash(self) -> None:
        self.test_huge_int_add_is_rejected_structurally()
        status, body = self.server.post("/api/export/dxf", {"path": "dxf/b6_after_reject.dxf"})
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), True, body)
        target = os.path.join(self.out_root, "dxf", "b6_after_reject.dxf")
        self.assertTrue(os.path.getsize(target) > 0, "导出不许留下 0 字节产物")

    def test_preloaded_out_of_range_document_is_rejected_at_load(self) -> None:
        """盘上已有越界文档（旧档/手工改过）：**装载闸**结构化拒收，不许裸 OverflowError、不许半截落库。

        口径升级（B6 装载闸）：初版口径是"能打开、导出时结构化失败"；装载闸落地后，越界真源
        **在装进工作区之前**就被拒（`error=invalid_document`）—— 比旧口径更强：越界值不再有机会
        在内存/响应/后续 save 里流转。旧口径真正要守的两条照旧断言：
        ①响应里不许出现裸内建异常名；②不许留下 0 字节产物。
        """
        doc = json.loads(json.dumps(_BASE_DOC))
        doc["sketches"][0]["entities"] = [{
            "uuid": "b6000000-0000-4000-8000-0000000000ac", "kind": "line", "layer": "0",
            "geom": {"x1": int("9" * 309), "y1": 0, "x2": 0, "y2": 0}}]
        self._write_case("huge_on_disk.json", doc)
        status, body = self.server.get("/api/open?path=cases/huge_on_disk.json")
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), False, "越界真源不许被接受：%r" % (body,))
        self.assertEqual(body.get("error"), "invalid_document", body)
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("OverflowError", blob, "拒收原因里不许出现裸内建异常名")
        self.assertNotIn("internal_error", blob, "拒收必须是结构化判定，不是兜底 internal_error")
        self.assertIn("nm_out_of_range", blob, "拒收原因要指到具体字段：%r" % (body,))
        # 被拒文档不许进工作区：后续导出照旧是对当前（空）文档的成功导出，且非空产物。
        status, exported = self.server.post("/api/export/dxf", {"path": "dxf/b6_on_disk.dxf"})
        self.assertEqual(status, 200, exported)
        self.assertIs(exported.get("ok"), True, exported)
        target = os.path.join(self.out_root, "dxf", "b6_on_disk.dxf")
        self.assertTrue(os.path.getsize(target) > 0, "成功导出必须是非空产物")


if __name__ == "__main__":
    unittest.main(verbosity=2)
