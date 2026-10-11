#!/usr/bin/env python3
"""cadcore.ops.hatch_measure 族测试：图案填充 / 边界识别 / 面积·周长·距离测量 / 特性查询。

判据（不是"能过就行"的冒烟 —— 每条断言对应冻结接口或模块 docstring 里的一句话）
----------------------------------------------------------------------------
1. **按调用形态验冻结接口，不靠关键词排列组合猜**：`FAMILY` / `OPERATIONS`；
   每个操作**第一个位置参数就叫 `sk_or_doc`**、其余参数一律 KEYWORD_ONLY、返回 `list[str]`。
   少传 `sk_or_doc` ⇒ `TypeError`（那是调用方少传参数，**不是**族实现的缺陷，不许被吞成 `CadError`）。
2. **口径三态各验一次**：`0`（真为零，例如无洞的 `hole_area_nm2`）/ `-1`（未采，例如二维草图的 `z_nm`）/
   `NA`（不适用，例如直线的 `area_nm2`、空图的 `area_nm2`）—— 三者**不许同形**。
3. **长度一律整数纳米**（面积 nm²）：整数算式精确相等；只有 π 参与处容 1 nm² 的取整差。
4. **只读 op 不建表、不写盘**（输入逐字节不变）；写 op 只增删图案线，**边界实体一个纳米都不动**；
   写 op 失败**不留半张填充图**。
5. **错误路径给机器可读的 `CadError.reason`**，不给"静默 0 / 静默 NA"。
6. 本族模块**不许出现 locale 字面量**（文案一律走 key）。判据里的字面量在测试里拼出来，
   避免本文件自己命中同一把尺子。

直跑本文件（不经 `unittest discover`）先打印判定行，再跑全部用例：
    HATCH_MEASURE_OPS_TESTS verdict=PASS ops=18 first_positional_sk_or_doc=18 keyword_only_ok=18 locale_literals=0
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 tests/test_hatch_measure_ops.py 时的引导（同族模块的写法）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import copy
import inspect
import math
import pathlib
import sys
import unittest

from cadcore.errors import CadError
from cadcore.ops import hatch_measure as HM

#: 本族 id（冻结接口里的 `FAMILY`）。
FAMILY_ID = "hatch_measure"

#: 主操作名（别名另算；`OPERATIONS` 是唯一发现入口）。
MAIN_OPS = ("hatch_create", "hatch_update", "hatch_delete", "hatch_list", "hatch_query",
            "boundary_detect", "boundary_create", "measure_area", "measure_perimeter",
            "measure_distance", "prop_query")

#: 别名 → 主名（别名必须与主名指向**同一个函数对象**）。
ALIASES = {"hatch": "hatch_create", "boundary": "boundary_detect", "area": "measure_area",
           "perimeter": "measure_perimeter", "distance": "measure_distance",
           "properties": "prop_query", "entity_query": "prop_query"}

MM = 1_000_000                       # 1 mm = 1e6 nm
SIDE = 10 * MM                       # 正方形边长 10 mm
SQUARE_AREA = SIDE * SIDE            # 1e14 nm²
SQUARE_PERIMETER = 4 * SIDE          # 4e7 nm


# ======================================================================================
# 夹具
# ======================================================================================


def _line(uuid: str, x1: int, y1: int, x2: int, y2: int, layer: str = "0") -> dict:
    return {"uuid": uuid, "kind": "line", "layer": layer, "construction": False,
            "geom": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}}


def _circle(uuid: str, cx: int, cy: int, r: int, layer: str = "0") -> dict:
    return {"uuid": uuid, "kind": "circle", "layer": layer, "construction": False,
            "geom": {"cx": int(cx), "cy": int(cy), "r": int(r)}}


def _square_entities(side: int = SIDE, origin: tuple = (0, 0), prefix: str = "sq") -> list:
    """首尾相接成环的四条 `line`（uuid 固定 ⇒ 同一夹具两次调用逐字节相同）。"""
    x0, y0 = origin
    corners = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    return [_line("%s%d" % (prefix, index), corners[index][0], corners[index][1],
                  corners[(index + 1) % 4][0], corners[(index + 1) % 4][1])
            for index in range(4)]


def _sketch(entities: list) -> dict:
    return {"entities": list(entities)}


def _open_chain() -> list:
    """两条首尾相接但**不闭合**的线（不是边界，本层不猜、不自动补线）。"""
    return [_line("oc0", 0, 0, 5 * MM, 0), _line("oc1", 5 * MM, 0, 5 * MM, 5 * MM)]


def _kv(tokens: list) -> dict:
    """token 列表 → `{key: value}`（按 token 顺序；值按第一个 `=` 切）。

    两种落法都吃：①一个 token 一个键（`measure_area` / `hatch_query` …）；
    ②一个 token 里空格分隔多个键（`boundary_detect` 的**每个环是一条 token**，
    ``boundary=<...> area_nm2=<...> perimeter_nm=<...> closed=1``）。
    """
    out: dict = {}
    for token in tokens:
        for piece in token.split(" "):
            key, _sep, value = piece.partition("=")
            out[key] = value
    return out


def _blocks(tokens: list) -> list:
    """`prop_query` 的 token 流 → 每实体一个 dict（块以 `uuid=` 起头）。"""
    blocks: list = []
    current = None
    for token in tokens:
        if token.startswith("uuid="):
            current = []
            blocks.append(current)
        if current is None:
            raise AssertionError("prop_query token block must start with uuid=: %r" % (token,))
        current.append(token)
    return [_kv(block) for block in blocks]


# ======================================================================================
# ① 冻结接口
# ======================================================================================


class FrozenInterfaceTest(unittest.TestCase):
    def test_family_id_and_operations_table(self):
        self.assertEqual(FAMILY_ID, HM.FAMILY)
        self.assertIsInstance(HM.OPERATIONS, dict)
        for name in MAIN_OPS:
            self.assertIn(name, HM.OPERATIONS)
            self.assertTrue(callable(HM.OPERATIONS[name]), name)

    def test_aliases_are_the_same_function_object(self):
        for alias, main in ALIASES.items():
            self.assertIn(alias, HM.OPERATIONS)
            self.assertIs(HM.OPERATIONS[alias], HM.OPERATIONS[main], alias)

    def test_first_positional_is_sk_or_doc_and_rest_are_keyword_only(self):
        """冻结接口的形状：`op(sk_or_doc, **kwargs)` —— 不靠 20 种关键词组合去猜。"""
        for name, function in sorted(HM.OPERATIONS.items()):
            parameters = list(inspect.signature(function).parameters.values())
            self.assertTrue(parameters, name)
            self.assertEqual("sk_or_doc", parameters[0].name, name)
            self.assertIn(parameters[0].kind,
                          (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD), name)
            for parameter in parameters[1:]:
                self.assertEqual(inspect.Parameter.KEYWORD_ONLY, parameter.kind,
                                 "%s: 参数 %s 必须是关键字参数" % (name, parameter.name))

    def test_sk_or_doc_is_required_positional(self):
        """少传第一个位置参数 ⇒ `TypeError`（调用方的错，不是族实现的缺陷，也不许退化成 CadError）。"""
        for name in ("measure_area", "boundary_detect", "prop_query", "hatch_list"):
            with self.assertRaises(TypeError):
                HM.OPERATIONS[name]()

    def test_every_operation_runs_with_the_frozen_call_shape(self):
        for name in sorted(HM.OPERATIONS):
            sketch, hatch = self._target_with_hatch()
            result = HM.OPERATIONS[name](sketch, **self._canonical_kwargs(name, hatch))
            self.assertIsInstance(result, list, name)
            for token in result:
                self.assertIsInstance(token, str, "%s: %r" % (name, token))

    @staticmethod
    def _target_with_hatch():
        sketch = _sketch(_square_entities())
        HM.hatch_create(sketch, spacing_nm=5 * MM)
        return sketch, _kv(HM.hatch_list(sketch))["hatch"]

    @staticmethod
    def _canonical_kwargs(name: str, hatch: str) -> dict:
        """规范入参按**函数对象**给，不按名字枚举 —— `OPERATIONS` 里别名与主名同参
        （按名字枚举会漏掉 `hatch_edit` 这类别名，且漏一个就少跑一个 op 的形态验证）。
        只给各 op **语义必需**的关键词，其余留缺省。"""
        function = HM.OPERATIONS[name]
        if function is HM.hatch_create:
            return {"spacing_nm": 5 * MM}
        if function is HM.hatch_update or function is HM.hatch_delete:
            return {"hatch": hatch}
        if function is HM.measure_distance:
            return {"p1_nm": (0, 0), "p2_nm": (3 * MM, 4 * MM)}
        if function is HM.boundary_from_point:
            # `point` 是该 op 的语义必需入参（模块 docstring：内部点拾取边界）⇒ 调用形态里必须给，
            # 不是可省的关键词。取正方形内部点 (SIDE/2, SIDE/2)。
            return {"point": (SIDE // 2, SIDE // 2)}
        if function is HM.match_properties:
            return {"source": _square_entities()[0]["uuid"]}
        return {}


# ======================================================================================
# ② 边界识别 / 测量
# ======================================================================================


class BoundaryDetectTest(unittest.TestCase):
    def test_closed_square_is_one_boundary(self):
        sketch = _sketch(_square_entities())
        tokens = HM.boundary_detect(sketch)
        self.assertEqual(1, len(tokens))
        values = _kv(tokens)
        self.assertEqual("1", values["closed"])
        self.assertEqual(str(SQUARE_AREA), values["area_nm2"])
        self.assertEqual(str(SQUARE_PERIMETER), values["perimeter_nm"])
        self.assertEqual(sorted(entity["uuid"] for entity in _square_entities()),
                         sorted(values["boundary"].split(",")))

    def test_open_chain_is_not_a_boundary_true_zero(self):
        sketch = _sketch(_open_chain())
        self.assertEqual([], HM.boundary_detect(sketch))          # 真为零：一个闭合环都没有
        self.assertEqual([], HM.boundary_detect(sketch, boundary=["oc0"]))
        self.assertEqual(["area_nm2=NA", "outer_area_nm2=NA", "hole_area_nm2=NA", "perimeter_nm=NA",
                          "loop_count=0", "closed=0"], HM.measure_area(sketch))

    def test_empty_selection_is_an_error_not_a_silent_noop(self):
        with self.assertRaises(CadError) as ctx:
            HM.boundary_detect(_sketch(_square_entities()), boundary=[])
        self.assertEqual("empty_selection", ctx.exception.reason)

    def test_circle_is_a_boundary_by_itself(self):
        sketch = _sketch([_circle("c0", 0, 0, 5 * MM)])
        values = _kv(HM.measure_area(sketch))
        self.assertAlmostEqual(math.pi * (5 * MM) ** 2, int(values["area_nm2"]), delta=1.0)
        self.assertEqual("1", values["loop_count"])
        self.assertEqual("c0", _kv(HM.boundary_detect(sketch))["boundary"])


class MeasureTest(unittest.TestCase):
    def test_area_net_perimeter_and_true_zero_hole(self):
        sketch = _sketch(_square_entities())
        values = _kv(HM.measure_area(sketch))
        self.assertEqual(str(SQUARE_AREA), values["area_nm2"])
        self.assertEqual(str(SQUARE_AREA), values["outer_area_nm2"])
        self.assertEqual("0", values["hole_area_nm2"])            # 真为零（不是 NA、不是 -1）
        self.assertEqual(str(SQUARE_PERIMETER), values["perimeter_nm"])
        self.assertEqual("1", values["loop_count"])
        self.assertEqual("1", values["closed"])

    def test_hole_is_subtracted(self):
        inner_side = 2 * MM
        sketch = _sketch(_square_entities() + _square_entities(inner_side, (4 * MM, 4 * MM), prefix="in"))
        values = _kv(HM.measure_area(sketch))
        self.assertEqual(str(SQUARE_AREA - inner_side * inner_side), values["area_nm2"])
        self.assertEqual(str(inner_side * inner_side), values["hole_area_nm2"])
        self.assertEqual(str(SQUARE_PERIMETER + 4 * inner_side), values["perimeter_nm"])
        self.assertEqual("2", values["loop_count"])

    def test_empty_sketch_area_is_na_not_zero(self):
        self.assertEqual(["area_nm2=NA", "outer_area_nm2=NA", "hole_area_nm2=NA", "perimeter_nm=NA",
                          "loop_count=0", "closed=0"], HM.measure_area(_sketch([])))

    def test_perimeter_open_and_closed(self):
        self.assertEqual(["perimeter_nm=%d" % (5 * MM), "curve_count=1", "closed=0"],
                         HM.measure_perimeter(_sketch([_line("a", 0, 0, 3 * MM, 4 * MM)])))
        values = _kv(HM.measure_perimeter(_sketch(_square_entities())))
        self.assertEqual(str(SQUARE_PERIMETER), values["perimeter_nm"])
        self.assertEqual("4", values["curve_count"])
        self.assertEqual("1", values["closed"])

    def test_perimeter_empty_sketch_is_na_not_zero(self):
        self.assertEqual(["perimeter_nm=NA", "curve_count=0", "closed=0"],
                         HM.measure_perimeter(_sketch([])))

    def test_distance_point_to_point_is_a_345_triangle(self):
        values = _kv(HM.measure_distance(_sketch([]), p1_nm=(0, 0), p2_nm=(3 * MM, 4 * MM)))
        self.assertEqual(str(5 * MM), values["distance_nm"])
        self.assertEqual(str(3 * MM), values["dx_nm"])
        self.assertEqual(str(4 * MM), values["dy_nm"])
        self.assertEqual("0,0", values["nearest_a_nm"])
        self.assertEqual("%d,%d" % (3 * MM, 4 * MM), values["nearest_b_nm"])

    def test_distance_to_entity_uses_its_discrete_points(self):
        """实体一侧按**离散点列**取最近点对（docstring 明说，不是投影）。"""
        sketch = _sketch([_line("a", 0, 0, 10 * MM, 0)])
        values = _kv(HM.measure_distance(sketch, ref_a="a", p2_nm=(0, 4 * MM)))
        self.assertEqual(str(4 * MM), values["distance_nm"])
        self.assertEqual("0,0", values["nearest_a_nm"])
        self.assertEqual("0,%d" % (4 * MM), values["nearest_b_nm"])

    def test_distance_without_two_operands_raises(self):
        sketch = _sketch(_square_entities())
        with self.assertRaises(CadError) as ctx:
            HM.measure_distance(sketch, p1_nm=(0, 0))             # 缺第二个操作数 ⇒ 报错，不猜"到原点"
        self.assertEqual("invalid_argument", ctx.exception.reason)
        with self.assertRaises(CadError) as ctx:
            HM.measure_distance(sketch, ref_a="missing", ref_b="sq0")
        self.assertEqual("unknown_reference", ctx.exception.reason)


class PropQueryTest(unittest.TestCase):
    def test_line_block_three_state_and_exact_length(self):
        sketch = _sketch([_line("l0", 0, 0, 3 * MM, 4 * MM)])
        blocks = _blocks(HM.prop_query(sketch))
        self.assertEqual(1, len(blocks))
        block = blocks[0]
        self.assertEqual("l0", block["uuid"])
        self.assertEqual("line", block["kind"])
        self.assertEqual(str(5 * MM), block["length_nm"])
        self.assertEqual("0,0,%d,%d" % (3 * MM, 4 * MM), block["bbox_nm"])
        self.assertEqual("NA", block["area_nm2"])                 # 不适用：直线没有面积
        self.assertEqual("NA", block["radius_nm"])
        self.assertEqual("NA", block["vertex_count"])
        self.assertEqual("NA", block["volume_nm3"])               # 不适用：L1 是二维草图
        self.assertEqual("-1", block["z_nm"])                     # 未采（≠ 0，≠ NA）

    def test_circle_and_closed_polyline_blocks(self):
        points = [[0, 0], [2 * MM, 0], [2 * MM, 2 * MM], [0, 2 * MM], [0, 0]]
        sketch = _sketch([_circle("c0", 0, 0, 5 * MM),
                          {"uuid": "p0", "kind": "polyline", "layer": "0", "construction": False,
                           "geom": {"points": points}}])
        circle_block, polyline_block = _blocks(HM.prop_query(sketch))
        self.assertAlmostEqual(math.pi * (5 * MM) ** 2, int(circle_block["area_nm2"]), delta=1.0)
        self.assertEqual(str(5 * MM), circle_block["radius_nm"])
        self.assertEqual("NA", circle_block["vertex_count"])
        self.assertEqual(str(4 * MM * MM), polyline_block["area_nm2"])   # 闭合折线走鞋带公式
        self.assertEqual("5", polyline_block["vertex_count"])
        self.assertEqual(str(8 * MM), polyline_block["length_nm"])
        self.assertEqual("NA", polyline_block["radius_nm"])

    def test_prop_query_refs_order_and_errors(self):
        sketch = _sketch([_line("l0", 0, 0, 1 * MM, 0), _line("l1", 0, 0, 2 * MM, 0)])
        self.assertEqual(["l1", "l0"], [block["uuid"] for block in _blocks(HM.prop_query(sketch, refs=["l1", "l0"]))])
        self.assertEqual(["l0"], [block["uuid"] for block in _blocks(HM.prop_query(sketch, ref="l0"))])
        self.assertEqual([], HM.prop_query(_sketch([])))
        with self.assertRaises(CadError) as ctx:
            HM.prop_query(sketch, ref="missing")
        self.assertEqual("unknown_reference", ctx.exception.reason)
        with self.assertRaises(CadError) as ctx:
            HM.prop_query(sketch, ref="l0", refs=["l1"])
        self.assertEqual("invalid_argument", ctx.exception.reason)


# ======================================================================================
# ③ 写操作：填充 / 边界落盘
# ======================================================================================


class HatchWriteTest(unittest.TestCase):
    def test_hatch_create_makes_lines_and_record(self):
        sketch = _sketch(_square_entities())
        created = HM.hatch_create(sketch, pattern="lines", angle_deg=0.0, spacing_nm=5 * MM)
        # 10mm 方形 / 5mm 间距 / 0 度：扫描线 y=0 与 y=5mm 各一条（y=10mm 落在边界顶点层，半开区间不相交）
        self.assertEqual(2, len(created))
        self.assertEqual(len(created), len(set(created)))
        by_uuid = {entity["uuid"]: entity for entity in sketch["entities"]}
        for entity_uuid in created:
            self.assertEqual("line", by_uuid[entity_uuid]["kind"])
            self.assertEqual("HATCH", by_uuid[entity_uuid]["layer"])
        self.assertEqual({0, 5 * MM}, {by_uuid[entity_uuid]["geom"]["y1"] for entity_uuid in created})
        for entity in _square_entities():                     # 边界实体一个纳米都不动
            self.assertEqual(entity, by_uuid[entity["uuid"]])
        listed = HM.hatch_list(sketch)
        self.assertEqual(1, len(listed))
        record = _kv(HM.hatch_query(sketch, hatch=_kv(listed)["hatch"]))
        self.assertEqual(["hatch", "pattern", "angle_deg", "spacing_nm", "scale", "linetype", "layer",
                          "line_count", "area_nm2", "boundary", "lines"], list(record.keys()))
        self.assertEqual("lines", record["pattern"])
        self.assertEqual("0", record["angle_deg"])
        self.assertEqual(str(5 * MM), record["spacing_nm"])
        self.assertEqual("continuous", record["linetype"])
        self.assertEqual("HATCH", record["layer"])
        self.assertEqual(str(len(created)), record["line_count"])
        self.assertEqual(str(SQUARE_AREA), record["area_nm2"])
        self.assertEqual(sorted(entity["uuid"] for entity in _square_entities()),
                         sorted(record["boundary"].split(",")))
        self.assertEqual(list(created), record["lines"].split(","))

    def test_scale_shrinks_spacing(self):
        sketch = _sketch(_square_entities())
        created = HM.hatch_create(sketch, spacing_nm=5 * MM, scale=0.5)
        record = _kv(HM.hatch_query(sketch, hatch=_kv(HM.hatch_list(sketch))["hatch"]))
        self.assertEqual(str(2 * MM + MM // 2), record["spacing_nm"])     # 5mm × 0.5 = 2.5mm
        self.assertAlmostEqual(0.5, float(record["scale"]))
        self.assertEqual(4, len(created))                                 # 0 / 2.5 / 5 / 7.5 mm

    def test_dashed_linetype_splits_each_scanline(self):
        sketch = _sketch(_square_entities())
        created = HM.hatch_create(sketch, spacing_nm=5 * MM, linetype="dashed")
        record = _kv(HM.hatch_query(sketch, hatch=_kv(HM.hatch_list(sketch))["hatch"]))
        self.assertEqual("dashed", record["linetype"])
        self.assertEqual(4, len(created))            # 每条 10mm 扫描线 = 5mm 实段 + 2.5 空 + 2.5 实段

    def test_hatch_create_error_reasons(self):
        sketch = _sketch(_square_entities())
        for kwargs, reason in ((dict(pattern="solid"), "unsupported_pattern"),
                               (dict(pattern="spiral"), "unknown_pattern"),
                               (dict(pattern="lines", linetype="wavy"), "unknown_linetype"),
                               (dict(pattern="lines", spacing_nm=MM, max_lines=5), "too_many_lines")):
            with self.assertRaises(CadError) as ctx:
                HM.hatch_create(sketch, **kwargs)
            self.assertEqual(reason, ctx.exception.reason, kwargs)
        with self.assertRaises(CadError) as ctx:
            HM.hatch_create(_sketch(_open_chain()))
        self.assertEqual("no_boundary", ctx.exception.reason)

    def test_failed_hatch_leaves_no_half_fill(self):
        """失败不留半张填充图：多族图案（grid）第一族已落线、第二族超限 ⇒ 已落的线要撤掉。"""
        sketch = _sketch(_square_entities())
        with self.assertRaises(CadError) as ctx:
            HM.hatch_create(sketch, pattern="grid", spacing_nm=2 * MM + MM // 2, max_lines=5)
        self.assertEqual("too_many_lines", ctx.exception.reason)
        self.assertEqual(sorted(entity["uuid"] for entity in _square_entities()),
                         sorted(entity["uuid"] for entity in sketch["entities"]))

    def test_hatch_update_regenerates_in_place(self):
        sketch = _sketch(_square_entities())
        first = HM.hatch_create(sketch, spacing_nm=5 * MM)
        hatch = _kv(HM.hatch_list(sketch))["hatch"]
        second = HM.hatch_update(sketch, hatch=hatch, angle_deg=45.0, spacing_nm=4 * MM)
        self.assertTrue(second)
        remaining = {entity["uuid"] for entity in sketch["entities"]}
        for entity_uuid in first:
            self.assertNotIn(entity_uuid, remaining)              # 旧图案线删干净
        listed = HM.hatch_list(sketch)
        self.assertEqual(1, len(listed))                          # 记录**原地更新**：不留第二条
        self.assertEqual(hatch, _kv(listed)["hatch"])
        record = _kv(HM.hatch_query(sketch, hatch=hatch))
        self.assertEqual("45", record["angle_deg"])
        self.assertEqual(str(4 * MM), record["spacing_nm"])
        self.assertEqual(list(second), record["lines"].split(","))
        by_uuid = {entity["uuid"]: entity for entity in sketch["entities"]}
        for entity in _square_entities():                         # 边界实体不动
            self.assertEqual(entity, by_uuid[entity["uuid"]])

    def test_hatch_delete_removes_only_pattern_lines(self):
        sketch = _sketch(_square_entities())
        created = HM.hatch_create(sketch, spacing_nm=5 * MM)
        hatch = _kv(HM.hatch_list(sketch))["hatch"]
        self.assertEqual(sorted(created), sorted(HM.hatch_delete(sketch, hatch=hatch)))
        self.assertEqual(sorted(entity["uuid"] for entity in _square_entities()),
                         sorted(entity["uuid"] for entity in sketch["entities"]))
        self.assertEqual([], HM.hatch_list(sketch))
        with self.assertRaises(CadError) as ctx:
            HM.hatch_delete(sketch, hatch=hatch)
        self.assertEqual("unknown_reference", ctx.exception.reason)

    def test_document_target_keeps_table_in_doc_extra(self):
        sketch = _sketch(_square_entities())
        doc = {"sketch": sketch, "extra": {}}
        created = HM.hatch_create(doc, spacing_nm=5 * MM)
        self.assertTrue(created)
        self.assertIn("hatch_measure", doc["extra"])              # 目标是文档 ⇒ 表落 doc.extra
        self.assertNotIn("hatch_measure", sketch)                 # 草图不留表
        self.assertEqual(1, len(HM.hatch_list(doc)))
        self.assertEqual([], HM.hatch_list(sketch))
        hatch = _kv(HM.hatch_list(doc))["hatch"]
        self.assertTrue(HM.hatch_update(doc, hatch=hatch, angle_deg=30.0))
        self.assertEqual(1, len(HM.hatch_list(doc)))              # 文档目标同样原地更新
        self.assertEqual([], HM.hatch_list(sketch))               # 不许在草图上留第二张表

    def test_boundary_create_makes_closed_polyline(self):
        sketch = _sketch(_square_entities())
        created = HM.boundary_create(sketch, index=0)
        self.assertEqual(1, len(created))
        entity = {item["uuid"]: item for item in sketch["entities"]}[created[0]]
        points = entity["geom"]["points"]
        self.assertEqual("polyline", entity["kind"])
        self.assertEqual([0, 0], list(points[0]))
        self.assertEqual(list(points[0]), list(points[-1]))       # 首末点重合 ⇒ 闭合轮廓判据
        self.assertEqual(5, len(points))

    def test_boundary_create_errors(self):
        with self.assertRaises(CadError) as ctx:
            HM.boundary_create(_sketch([_line("a", 0, 0, MM, 0)]))
        self.assertEqual("no_boundary", ctx.exception.reason)
        with self.assertRaises(CadError) as ctx:
            HM.boundary_create(_sketch(_square_entities()), index=7)
        self.assertEqual("index_out_of_range", ctx.exception.reason)


class ReadOnlyTest(unittest.TestCase):
    def test_read_only_ops_do_not_write_anything(self):
        calls = (("boundary_detect", {}), ("measure_area", {}), ("measure_perimeter", {}),
                 ("measure_distance", {"p1_nm": (0, 0), "p2_nm": (MM, 0)}),
                 ("prop_query", {}), ("hatch_list", {}), ("hatch_query", {}))
        for name, kwargs in calls:
            sketch = _sketch(_square_entities())
            before = copy.deepcopy(sketch)
            result = HM.OPERATIONS[name](sketch, **kwargs)
            self.assertIsInstance(result, list, name)
            self.assertNotIn("hatch_measure", sketch, name)
            self.assertEqual(before, sketch, name)


class NoLocaleLiteralTest(unittest.TestCase):
    def test_family_module_has_no_locale_literal(self):
        source = pathlib.Path(HM.__file__).read_text(encoding="utf-8")
        for token in ("zh" + "-CN", "e" + "n", "j" + "a"):        # 拼出来，免得本文件自己命中同一把尺子
            self.assertNotIn('"%s"' % token, source)


def _selftest() -> int:
    """判定行：`OPERATIONS` 形状 + 本族 locale 字面量（数字与检查项名以本函数当次输出为准）。"""
    bad_first, bad_kwonly = [], []
    for name, function in HM.OPERATIONS.items():
        parameters = list(inspect.signature(function).parameters.values())
        if not parameters or parameters[0].name != "sk_or_doc":
            bad_first.append(name)
        elif any(parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in parameters[1:]):
            bad_kwonly.append(name)
    source = pathlib.Path(HM.__file__).read_text(encoding="utf-8")
    locale_hits = sum(1 for token in ("zh" + "-CN", "e" + "n", "j" + "a") if '"%s"' % token in source)
    ok = not bad_first and not bad_kwonly and locale_hits == 0
    print("HATCH_MEASURE_OPS_TESTS verdict=%s ops=%d first_positional_sk_or_doc=%d keyword_only_ok=%d "
          "locale_literals=%d family=%s" % ("PASS" if ok else "FAIL", len(HM.OPERATIONS),
                                            len(HM.OPERATIONS) - len(bad_first),
                                            len(HM.OPERATIONS) - len(bad_kwonly), locale_hits, HM.FAMILY))
    if not ok:
        print("  first_positional_bad=%s keyword_only_bad=%s" % (bad_first, bad_kwonly))
    return 0 if ok else 1


class GradientFillTest(unittest.TestCase):
    """渐变色填充在 L1 上的**可行形态**：L1 实体没有颜色/着色字段 ⇒ 只记定义、不产几何。

    判据（对应模块 docstring 的 unsupported 清单第 2 条）：
    ①返回 `[]`（真为零：一个实体都没新建/改动）；②既有几何逐字节不变；
    ③表里有一条 `kind="gradient"` 的定义，`area_nm2` 是**真算出来的**净面积；
    ④定义里 `rendered=0`、`entity_count=0` —— 本层不声称渲染过、不用密集线冒充；
    ⑤错误路径给机器可读 reason（unknown_gradient / invalid_colors / invalid_transparency / no_boundary）。
    """

    def test_registered_in_operations(self):
        self.assertIn("gradient_fill", HM.OPERATIONS)
        self.assertIs(HM.OPERATIONS["gradient_fill"], HM.gradient_fill)

    def test_records_definition_with_real_area_and_zero_geometry(self):
        sketch = _sketch(_square_entities())
        before = copy.deepcopy(sketch)
        created = HM.gradient_fill(sketch, colors=("#FFFFFF", "#0000FF"), gradient_name="LINEAR",
                                   angle_deg=90.0)
        self.assertEqual([], created)                                # 真为零：L1 不产着色几何
        self.assertEqual(before["entities"], sketch["entities"])     # 几何一个纳米都没动
        record = sketch["hatch_measure"]["gradients"][0]
        self.assertEqual("gradient", record["kind"])                 # 声明类型就是 gradient
        self.assertEqual("LINEAR", record["gradient_name"])
        self.assertEqual(["#FFFFFF", "#0000FF"], record["colors"])
        self.assertEqual(1, record["two_color"])
        self.assertEqual(str(SQUARE_AREA), str(record["area_nm2"]))  # 面积按真实闭合环算
        self.assertEqual(0, record["entity_count"])                  # 没产几何，也不假装渲染过
        self.assertEqual(0, record["rendered"])
        self.assertEqual(sorted(entity["uuid"] for entity in _square_entities()),
                         sorted(record["boundary"]))

    def test_single_colour_default_and_doc_target(self):
        sketch = _sketch(_square_entities())
        doc = {"sketch": sketch, "extra": {}}
        self.assertEqual([], HM.gradient_fill(doc, gradient_name="SPHERICAL"))
        record = doc["extra"]["hatch_measure"]["gradients"][0]
        self.assertEqual(["BYLAYER", "BYLAYER"], record["colors"])   # 单色渐变：两端同 BYLAYER
        self.assertEqual(0, record["two_color"])
        self.assertNotIn("hatch_measure", sketch)                    # 文档目标 ⇒ 表落 doc.extra

    def test_gradient_error_reasons(self):
        sketch = _sketch(_square_entities())
        for kwargs, reason in ((dict(gradient_name="RADIAL"), "unknown_gradient"),
                               (dict(colors=("#FFFFFF", None)), "invalid_colors"),
                               (dict(colors=("a", "b", "c")), "invalid_colors"),
                               (dict(transparency=2.0), "invalid_transparency")):
            with self.assertRaises(CadError) as ctx:
                HM.gradient_fill(sketch, **kwargs)
            self.assertEqual(reason, ctx.exception.reason, kwargs)
        with self.assertRaises(CadError) as ctx:
            HM.gradient_fill(_sketch(_open_chain()))                 # 开放链不是边界
        self.assertEqual("no_boundary", ctx.exception.reason)
        self.assertEqual([], HM.hatch_list(sketch))                  # 失败路径不留半张表


if __name__ == "__main__":
    verdict = _selftest()
    loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    outcome = unittest.TextTestRunner(verbosity=1).run(loaded)
    raise SystemExit(0 if (verdict == 0 and outcome.wasSuccessful()) else 1)
