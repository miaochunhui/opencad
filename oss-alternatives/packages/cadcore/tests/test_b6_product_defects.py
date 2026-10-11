"""test_b6_product_defects —— C 线 B6 产品面缺陷回归（cadcore 侧 3 条）。

被测缺陷（来自 `/hf3fs-jd/data/tmp/cad-scan/scan.json` 的 `defects_A`）
--------------------------------------------------------------------
- `roundtrip:with_polyline` / `edit:undo` / `edit:redo`（根因 RC3）：
  `sketch/primitives.add_polyline` 写的是 `geom={"points": [[x, y], ...]}`，而
  `docmodel._NODE_FIELDS["geom"]` 只认 `points_nm` ⇒ 自家写出的字段被自家读侧审计判成
  `unknown_field_preserved` 伪诊断，L1 文本不再逐字节稳定（undo/redo、save/open 往返全中）。
- `extreme:huge_int_309digit`（根因 RC2)：坐标量程没校验 ⇒ 10^309 这类整数纳米被收下，
  等到 DXF 导出才以裸 `OverflowError` 崩（导出层 `float(value)` 溢出）。

判据（每条缺陷一组断言，改坏立刻报红）
--------------------------------------
1. 写入侧 → 读回侧：polyline 的 `geom.points` 不得再触发任何 `unknown_field_preserved`；
   同一文档 `to_l1 → from_l1 → to_l1` **逐字节相同**。
2. **阴性对照**（尺子看得见红）：真的未知字段（`points_typo`）必须照旧报 `unknown_field_preserved`。
3. `geom.points` 与 `points_nm` 同口径：非整数坐标必须被 `check_invariants` 判违规。
4. 量程：`add_line` / `add_polyline` / `modify.move` 对 `|nm| > MAX_ABS_NM` 一律结构化拒收，
   恰好等于上限仍收（边界两态可分辨）。
5. 存储层已被污染的文档（盘上/旧档带着 10^309）：DXF 导出必须是 `DocumentError`，
   **不许**冒 `OverflowError`（裸内建异常）。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .          # 契约命令
    python3 tests/test_b6_product_defects.py -v         # 单跑本文件
"""

from __future__ import annotations

import unittest

from cadcore import docmodel
from cadcore.edit import modify
from cadcore.errors import CadError, DocumentError
from cadcore.io import dxf_bridge
from cadcore.sketch import commands, primitives
from cadcore.sketch.constraints import SketchError

#: 各层自己的失败信号：`commands` 抛 `SketchError` 族，`edit.modify` 抛 `cadcore.errors.CadError`
#: （它有自己的 `_fail`）。断言收两族，但必须是**结构化**异常，不许是内建异常。
_FAILURES = (SketchError, CadError)

#: 可存上限：整数纳米，2^53（`constraints.MAX_ABS_NM`）。测试自己再写一遍是有意的 ——
#: 实现把这个数改小/改大，本文件的边界断言就会报红（不靠 import 同一常量自证）。
_BOUND = 1 << 53


def new_sketch() -> dict:
    return {"uuid": "b6000000-0000-4000-8000-0000000000ff", "plane": "XY",
            "entities": [], "constraints": [], "dimensions": []}


def roundtrip(doc) -> tuple[str, str, list]:
    text = docmodel.document_to_l1(doc)
    again = docmodel.document_from_l1(text)
    return text, docmodel.document_to_l1(again), list(again.diagnostics or [])


class PolylineGeomPointsTests(unittest.TestCase):
    """RC3：自家写出的 `geom.points` 不许被自家读侧判成未知字段。"""

    def test_polyline_points_roundtrip_is_byte_stable_without_diagnostics(self) -> None:
        doc = docmodel.new_document(unit="mm", standard_profile="gbt", projection_angle="first")
        sketch = new_sketch()
        doc.sketches.append(sketch)
        primitives.add_polyline(sketch, [(0, 0), (10_000_000, 5_000_000), (20_000_000, 0)],
                                layer="POLY")
        self.assertEqual(sketch["entities"][0]["geom"]["points"],
                         [[0, 0], [10_000_000, 5_000_000], [20_000_000, 0]],
                         "夹具自证：折线真写进了 geom.points（否则本测试测的不是这条路）")
        first, second, diagnostics = roundtrip(doc)
        spurious = [item for item in diagnostics
                    if item.get("code") == "unknown_field_preserved"]
        self.assertEqual(spurious, [], "自家写出的 geom.points 不得触发 unknown_field_preserved")
        self.assertEqual(second, first, "含折线的 L1 文本往返后必须逐字节相同")

    def test_unknown_geom_field_is_still_diagnosed(self) -> None:
        """阴性对照：尺子必须看得见**真的**未知字段（否则上一条的绿是假绿）。"""
        doc = docmodel.new_document(unit="mm", standard_profile="gbt", projection_angle="first")
        sketch = new_sketch()
        doc.sketches.append(sketch)
        primitives.add_polyline(sketch, [(0, 0), (10_000_000, 5_000_000)])
        sketch["entities"][0]["geom"]["points_typo"] = [[0, 0], [1, 1]]
        _first, _second, diagnostics = roundtrip(doc)
        codes = [(item.get("code"), item.get("path")) for item in diagnostics]
        self.assertIn(("unknown_field_preserved", "$.sketches[0].entities[0].geom.points_typo"),
                      codes, "未知几何字段必须照旧报 unknown_field_preserved")

    def test_geom_points_are_checked_as_integer_nanometres(self) -> None:
        """`points` 与 `points_nm` 同一把尺子：浮点坐标必须进不变量违规清单。"""
        doc = docmodel.new_document(unit="mm", standard_profile="gbt", projection_angle="first")
        sketch = new_sketch()
        doc.sketches.append(sketch)
        primitives.add_polyline(sketch, [(0, 0), (10_000_000, 5_000_000)])
        sketch["entities"][0]["geom"]["points"] = [[0.5, 0], [10_000_000, 5_000_000]]
        problems = docmodel.check_invariants(doc)
        self.assertTrue(any("nm_not_integer" in item and "points" in item for item in problems),
                        "geom.points 的浮点坐标必须被记录成 nm_not_integer，实际=%r" % (problems,))


class NanometreRangeTests(unittest.TestCase):
    """RC2：坐标量程校验 —— 上游收下的数必须能被下游 float64 表示。"""

    def test_add_line_rejects_out_of_range_and_accepts_boundary(self) -> None:
        sketch = new_sketch()
        with self.assertRaises(_FAILURES) as ctx:
            commands.add_line(sketch, p1=(10 ** 309, 0), p2=(1_000_000, 0))
        self.assertNotIsInstance(ctx.exception, (OverflowError, ValueError, TypeError))
        self.assertEqual(sketch["entities"], [], "拒收时不许留下半截几何")
        commands.add_line(sketch, p1=(_BOUND, 0), p2=(1_000_000, 0))  # 恰好上限：收
        self.assertEqual(len(sketch["entities"]), 1)
        with self.assertRaises(_FAILURES):
            commands.add_line(sketch, p1=(_BOUND + 1, 0), p2=(1_000_000, 0))
        with self.assertRaises(_FAILURES):
            commands.add_line(sketch, p1=(-_BOUND - 1, 0), p2=(1_000_000, 0))

    def test_add_polyline_rejects_out_of_range(self) -> None:
        sketch = new_sketch()
        with self.assertRaises(_FAILURES):
            primitives.add_polyline(sketch, [(0, 0), (10 ** 309, 0)])
        with self.assertRaises(_FAILURES):
            primitives.add_polyline(sketch, [(0, 0), (_BOUND + 1, 0)])
        self.assertEqual(sketch["entities"], [], "先全量校验再落库：拒收不许留半截几何")

    def test_move_rejects_out_of_range_delta(self) -> None:
        sketch = new_sketch()
        ref = commands.add_line(sketch, p1=(0, 0), p2=(1_000_000, 0))
        with self.assertRaises(_FAILURES):
            modify.move(sketch, [ref], dx_nm=10 ** 309, dy_nm=0)
        self.assertEqual(sketch["entities"][0]["geom"], {"x1": 0, "y1": 0, "x2": 1_000_000, "y2": 0},
                         "拒收的 move 不许改动实体")

    def test_dxf_export_of_stored_out_of_range_document_is_structured(self) -> None:
        """存储层已被污染的文档：导出必须结构化报错，不许裸 `OverflowError`。"""
        sketch = {"uuid": "b6000000-0000-4000-8000-0000000000fe", "entities": [
            {"uuid": "b6000000-0000-4000-8000-0000000000fd", "kind": "line", "layer": "0",
             "geom": {"x1": 10 ** 309, "y1": 0, "x2": 0, "y2": 0}}], "dimensions": [],
            "constraints": []}
        with self.assertRaises(DocumentError) as ctx:
            dxf_bridge.sketch_to_dxf(sketch)
        self.assertNotIsInstance(ctx.exception, OverflowError)
        self.assertEqual(ctx.exception.reason, "coordinate_out_of_range",
                         "导出层越界必须是可归因的 DocumentError.reason")


if __name__ == "__main__":
    unittest.main(verbosity=2)
