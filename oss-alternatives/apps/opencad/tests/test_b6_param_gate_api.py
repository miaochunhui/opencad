"""test_b6_param_gate_api —— B6 参数边界第二轮（opencad API 侧）：**装载闸 / 变换量程 / 文本进产物**。

缺陷源（实测，探针 `/hf3fs-jd/data/tmp/cad-scan3/probe3.py` 按当前字节重建 pin）：
`CAD_PARAM_SCAN verdict=FAIL defects_A=37`，本文件覆盖其中由 **API 面**承重的四类：

1. `silent_illegal` + `invariant_broken`（4 条）：`/api/open` 把标注的 `value_nm` 是布尔/字符串/浮点/
   309 位整数**静默收下**并落库 ⇒ 产品自己的不变量当场被写坏；
2. `invariant_broken`（文本类，10 条）：非 i18n key 的 `text_key`（空白/汉字/LaTeX/引号）与形状错的
   `target`（非二元组 / 下标引用）同样被静默收下；
3. `internal_error` + `naked_builtin_exception:UnicodeEncodeError`（3 条）：孤立代理对（`"\\ud800"`）
   被收下后 `/api/state` 的 UTF-8 编码当场崩，文档从此读不出来；
4. `out_of_range_persisted`（5 条）：`scale(factor=2**53)` / `move(dx=2**53)` 的结果超出
   `|v| <= 2**53` 却静默落进真源；
5. `accepted_annotation_text_silently_dropped`（8 条）：`doc.annotations` 的文本一个字都不进产物。

判据（全部真起服务、真打 HTTP、现算）
--------------------------------------
* **装载闸**：非法真源 ⇒ `ok:false` + `error=invalid_document` + 逐字段原因；**文档不进工作区**
  （`/api/state` 照旧可读、内容与打开前一致）；
* **不泄漏**：响应原文里不得出现内建异常名（`OverflowError` / `UnicodeEncodeError` / …），
  也不得退化成兜底 `internal_error`；
* **拒绝不留半截**：被拒命令之后 `/api/state` 的 L1 文本与命令前**逐字节相同**，且真源里
  找不到超出 `2**53` 的长度值；
* **文本进产物**：被接受的 literal 文本必须在 DXF 或 PDF 产物字节里出现（`annotation_texts` 计数同证）；
* **阴性对照**：干净文档（合法 key/文本）照旧能开、能往返、能出图 —— 闸不是恒红。

跑法::

    cd oss-alternatives/apps/opencad
    python3 -m unittest discover -s tests -t .          # 契约命令
    python3 tests/test_b6_param_gate_api.py -v          # 单跑本文件

产物：只写自己 `tempfile.mkdtemp()` 出来的 out-root，收尾 `rmtree`，**仓内不留产物**。
"""

from __future__ import annotations

import http.client as http_client
import json
import os
import shutil
import sys
import tempfile
import unittest
from urllib.parse import quote

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

try:  # `-m unittest discover -t .` 时包名是 tests.*
    from tests.test_b6_product_defects import _BASE_DOC, ServerProcess
except ImportError:  # 单跑本文件（sys.path 上是 tests/ 自己）
    from test_b6_product_defects import _BASE_DOC, ServerProcess  # type: ignore[no-redef]

MM = 1_000_000
HUGE = int("9" * 309)
MAX_NM = 1 << 53

LINE_UUID = "bbbb2222-0000-4000-8000-000000000011"
ANN_UUID = "aaaa1111-0000-4000-8000-000000000001"
MARKER = "ANNOTATION_MARKER_XYZ"

#: 响应原文里**不许**出现的内建异常名（探针 `find_exceptions` 的口径子集）。
BUILTIN_EXCEPTION_NAMES = (
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "OverflowError",
    "ZeroDivisionError", "UnicodeEncodeError", "UnicodeDecodeError", "MemoryError",
    "RecursionError", "RuntimeError", "StopIteration", "ArithmeticError", "AssertionError",
)


class _Client(ServerProcess):
    """`ServerProcess` + 拿响应**原文**（判"有没有泄漏内建异常名"必须看原文）。"""

    def get_raw(self, path: str, timeout: float = 10.0):
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def post_raw(self, path: str, body: object, timeout: float = 10.0):
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            payload = json.dumps(body).encode("utf-8")
            conn.request("POST", path, body=payload,
                         headers={"Content-Type": "application/json",
                                  "Content-Length": str(len(payload))})
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()


def _annotation(**over):
    ann = {"uuid": ANN_UUID, "target": [LINE_UUID, "start"], "kind": "linear",
           "value_nm": 5 * MM, "text_key": None, "literal": True, "text": MARKER, "args": {}}
    ann.update(over)
    return ann


def _doc(*, annotations=(), with_line=True, unit="mm"):
    doc = json.loads(json.dumps(_BASE_DOC))
    doc["unit"] = unit
    entities = []
    if with_line:
        entities.append({"uuid": LINE_UUID, "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": 0, "y1": 0, "x2": 10 * MM, "y2": 0}})
    doc["sketches"][0]["entities"] = entities
    doc["annotations"] = list(annotations)
    return doc


class ParamGateCase(unittest.TestCase):
    """每个测试自己起一个真服务器 + 一个自己的 out-root（顺序无关，收尾即删）。"""

    def setUp(self) -> None:
        self.out_root = tempfile.mkdtemp(prefix="opencad-b6pg-")
        self.addCleanup(shutil.rmtree, self.out_root, True)
        os.makedirs(os.path.join(self.out_root, "cases"), exist_ok=True)
        self._write_case("_base_empty.json", _BASE_DOC)
        self.server = _Client(self.out_root).start()
        self.addCleanup(self.server.stop)

    # ── 夹具 ────────────────────────────────────────────────────────────────────────
    def _write_case(self, name: str, payload: object) -> str:
        path = os.path.join(self.out_root, "cases", name)
        with open(path, "w", encoding="utf-8") as handle:
            # `ensure_ascii=True`：孤立代理对（`"\ud800"`）必须写成 \\ud800 转义，
            # 否则夹具自己就先炸 UTF-8 编码 —— 探针写夹具用的是同一口径。
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        return path

    def open_case(self, name: str):
        return self.server.get_raw("/api/open?path=" + "cases/" + name)

    def command(self, verb: str, args: dict | None = None):
        return self.server.post_raw("/api/command", {"command": verb, "args": args or {}})

    def state_doc(self) -> str:
        status, body = self.server.get("/api/state")
        self.assertEqual(status, 200, body)
        self.assertIs(body.get("ok"), True, body)
        return str(body.get("doc") or "")

    def open_empty(self) -> None:
        status, body = self.open_case("_base_empty.json")
        self.assertEqual(status, 200, body)
        self.assertIn('"ok": true', body, body)

    def assert_no_builtin_exception(self, body: str, label: str) -> None:
        for name in BUILTIN_EXCEPTION_NAMES:
            self.assertNotIn(name, body, f"{label}: 响应里泄漏了内建异常名 {name}: {body[:400]}")

    # ── 判据 1/2/3：装载闸 ──────────────────────────────────────────────────────────
    def test_illegal_annotation_value_nm_is_rejected_at_open(self) -> None:
        for label, value in (("bool", True), ("str", "5"), ("float", 1.5), ("huge309", HUGE)):
            self._write_case("ann_%s.json" % label,
                             _doc(annotations=[_annotation(value_nm=value)]))
            status, body = self.open_case("ann_%s.json" % label)
            self.assertEqual(status, 200, body)
            self.assertIn('"ok": false', body, f"{label}: 非法 value_nm 不许被接受")
            self.assertIn("invalid_document", body, body)
            self.assertIn("value_nm", body, f"{label}: 原因要指到字段：{body[:300]}")
            self.assertNotIn("internal_error", body, body)
            self.assert_no_builtin_exception(body, label)
            self.assertIn('"ok": true', self.server.get_raw("/api/state")[1],
                          "被拒文档之后工作区仍必须可读")

    def test_bad_text_key_and_target_shape_are_rejected_at_open(self) -> None:
        cases = (("cjk", _annotation(literal=False, text=None, text_key="中文标注 😀")),
                 ("quotes", _annotation(literal=False, text=None, text_key='he said "hi"')),
                 ("newline", _annotation(literal=False, text=None, text_key="a\nb")),
                 ("spaces", _annotation(literal=False, text=None, text_key="   ")),
                 ("target_str", _annotation(target="not-a-pair")),
                 ("target_index", _annotation(target=[LINE_UUID, "0"])))
        for label, ann in cases:
            self._write_case("ann_%s.json" % label, _doc(annotations=[ann]))
            status, body = self.open_case("ann_%s.json" % label)
            self.assertEqual(status, 200, body)
            self.assertIn('"ok": false', body, f"{label}: 必须被拒：{body[:300]}")
            self.assertIn("invalid_document", body, body)
            self.assert_no_builtin_exception(body, label)

    def test_lone_surrogate_document_is_rejected_not_internal_error(self) -> None:
        for label, ann in (("literal", _annotation(text="\ud800")),
                           ("textkey", _annotation(literal=False, text=None, text_key="\ud800"))):
            self._write_case("ann_sur_%s.json" % label, _doc(annotations=[ann]))
            status, body = self.open_case("ann_sur_%s.json" % label)
            self.assertEqual(status, 200, body)
            self.assertIn('"ok": false', body, f"{label}: 孤立代理对不许被收下：{body[:300]}")
            self.assertNotIn("internal_error", body, body)
            self.assert_no_builtin_exception(body, label)
            self.assertIn('"ok": true', self.server.get_raw("/api/state")[1],
                          "被拒之后工作区必须仍可读（旧行为是 /api/state 直接崩）")

    def test_clean_annotated_document_still_opens_and_roundtrips(self) -> None:
        """阳性对照：合法真源照旧能开、能存、能重开 —— 装载闸不是恒红。"""
        self._write_case("clean.json", _doc(annotations=[_annotation()]))
        status, body = self.open_case("clean.json")
        self.assertEqual(status, 200, body)
        self.assertIn('"ok": true', body, body)
        status, saved = self.server.post("/api/save", {"path": "cases/clean.saved.json"})
        self.assertEqual(status, 200, saved)
        self.assertIs(saved.get("ok"), True, saved)
        status, reopened = self.open_case("clean.saved.json")
        self.assertEqual(status, 200, reopened)
        self.assertIn('"ok": true', reopened, reopened)
        self.assertIn(MARKER, reopened, "往返必须保住标注文本")

    # ── 判据 4：变换量程 + 命令面入参 ───────────────────────────────────────────────
    def test_transform_over_range_is_rejected_without_partial_state(self) -> None:
        self.open_empty()
        self.command("add_circle", {"center_nm": [10 * MM, 0], "radius_nm": 5 * MM})
        before = self.state_doc()
        for label, verb, args in (("scale", "scale", {"refs": [], "center_nm": [0, 0],
                                                      "factor": 1 << 53}),
                                  ("move", "move", {"refs": [], "dx_nm": 1 << 53, "dy_nm": 0})):
            status, body = self.command(verb, args)
            self.assertEqual(status, 200, body)
            self.assertIn('"ok": false', body, f"{label}: 越界结果不许被接受：{body[:300]}")
            self.assert_no_builtin_exception(body, label)
            after = self.state_doc()
            self.assertEqual(after, before, f"{label}: 被拒命令不许改真源（半截状态）")
            self.assertNotIn(str(1 << 53), after, f"{label}: 真源里不许留越界值")

    def test_overflow_parameter_entry_points_are_structured(self) -> None:
        self.open_empty()
        self.command("add_line", {"p1": [0, 0], "p2": [10 * MM, 0]})
        for label, verb, args in (
                ("scale", "scale", {"refs": [], "center_nm": [0, 0], "factor": HUGE}),
                ("rotate", "rotate", {"refs": [], "center_nm": [0, 0], "angle_deg": HUGE}),
                ("add_arc", "add_arc", {"center": [0, 0], "radius_nm": MM, "start_deg": HUGE,
                                        "end_deg": 90}),
                ("snap_tolerance", "snap_query", {"point_nm": [0, 0], "tolerance_nm": HUGE}),
                ("snap_point", "snap_query", {"point_nm": [HUGE, 0], "tolerance_nm": MM})):
            status, body = self.command(verb, args)
            self.assertIn(status, (200, 400), body)
            self.assertIn('"ok": false', body, f"{label}: 309 位整数必须结构化拒绝：{body[:300]}")
            self.assert_no_builtin_exception(body, label)
        self.assertIn('"ok": true', self.server.get_raw("/api/state")[1])

    def test_bad_dimension_text_key_is_rejected_without_partial_state(self) -> None:
        self.open_empty()
        status, line = self.server.post("/api/command", {"command": "add_line",
                                                         "args": {"p1": [0, 0],
                                                                  "p2": [10 * MM, 0]}})
        self.assertEqual(status, 200, line)
        self.assertIs(line.get("ok"), True, line)
        line_uuid = str(line.get("uuid") or "")
        refs = [[line_uuid, "start"], [line_uuid, "end"]]
        before = self.state_doc()
        status, body = self.command("add_dimension", {"kind": "linear", "refs": refs,
                                                      "value_nm": 10 * MM, "text_key": "中文标注"})
        self.assertEqual(status, 200, body)
        self.assertIn('"ok": false', body, body)
        self.assert_no_builtin_exception(body, "add_dimension")
        self.assertEqual(self.state_doc(), before, "被拒的尺寸不许落进草图")
        status, ok_body = self.command("add_dimension", {"kind": "linear", "refs": refs,
                                                         "value_nm": 10 * MM,
                                                         "text_key": "dim.length.overall"})
        self.assertEqual(status, 200, ok_body)
        self.assertIn('"ok": true', ok_body, ok_body)

    # ── 判据 5：标注文本进产物 ──────────────────────────────────────────────────────
    def test_annotation_text_reaches_exports(self) -> None:
        self._write_case("ann_marker.json", _doc(annotations=[_annotation()]))
        status, body = self.open_case("ann_marker.json")
        self.assertEqual(status, 200, body)
        self.assertIn('"ok": true', body, body)
        status, exported = self.server.post("/api/export/dxf", {"path": "dxf/ann.dxf"})
        self.assertEqual(status, 200, exported)
        self.assertIs(exported.get("ok"), True, exported)
        self.assertEqual(exported.get("annotation_texts"), 1, exported)
        dxf_path = os.path.join(self.out_root, "dxf", "ann.dxf")
        with open(dxf_path, "rb") as handle:
            dxf_blob = handle.read()
        status, pdf = self.server.post("/api/command", {"command": "export_pdf",
                                                        "args": {"out": "pdf/ann.pdf"}})
        self.assertEqual(status, 200, pdf)
        self.assertIs(pdf.get("ok"), True, pdf)
        with open(os.path.join(self.out_root, "pdf", "ann.pdf"), "rb") as handle:
            pdf_blob = handle.read()
        hits = (MARKER.encode("utf-8") in dxf_blob) or (MARKER.encode("utf-8") in pdf_blob)
        self.assertTrue(hits, "被接受的标注文本必须在 DXF 或 PDF 产物里可见")
        # 阴性对照：没加过的文本一个都不许出现。
        for needle in (b"NOT_ADDED_MARKER", b"SERIAL_MARKER"):
            self.assertNotIn(needle, dxf_blob)
            self.assertNotIn(needle, pdf_blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
