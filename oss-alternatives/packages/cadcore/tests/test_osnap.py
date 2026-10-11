"""tests.test_osnap —— E3 精度辅助单测（对象捕捉 / 正交 / 极轴 / 栅格）。

设计一句话
----------
**精度辅助最容易出的两类事故是"假命中"与"该炸不炸"**：光标明明离特征很远却回一个吸附点（用户以为
对齐了，其实差了半毫米），或者交点无解时抛异常把画图流程打断。所以本文件的尺子成对出现：
既测"该绿时绿"（端点/中点/圆心/象限点/交点/垂足/最近点确实命中，且坐标是**整数纳米**的准确值），
也测"该红时红"（超出容差 ⇒ `None`；无交点/平行/交点在段外 ⇒ `None` 且**不抛异常**；坏入参 ⇒ `ValueError`）。

夹具自证
--------
夹具全部落在内存里的 L1 草图上（不碰仓库里任何别人的文件、不写盘、无副作用）；`IntegrationTest`
用 A9 真件 `cadcore.sketch.commands.add_line/add_circle/add_arc` 现造草图，证明本模块读的是**真 L1 文档**
而不是自造的一种方言；`StructuralTest` 用对象形态夹具证明按**结构**读（映射/对象都认）。

断言口径
--------
坐标与距离都是**精确整数纳米**（本模块的返回值就是 int，不是浮点近似），所以断言一律用
`assertEqual` 而不是 `assertAlmostEqual`；只有极轴追踪保留半径那一处出现三角函数的 1 nm 级取整，
那里用的是数学上精确的输入（45° 等距点、轴向点）⇒ 仍然可以精确断言。
本文件里**非 docstring 的字符串字面量一律 ASCII**（与 `tests/test_sketch.py` 的既有口径一致）。
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.precision import SNAP_KINDS, SnapResult  # noqa: E402
from cadcore.precision import grid_snap as pkg_grid_snap  # noqa: E402
from cadcore.precision import ortho_lock as pkg_ortho_lock  # noqa: E402
from cadcore.precision import polar_track as pkg_polar_track  # noqa: E402
from cadcore.precision import snap as pkg_snap  # noqa: E402
from cadcore.precision.osnap import grid_snap, ortho_lock, polar_track, snap  # noqa: E402

MM = 1_000_000  # 1 毫米 = 1e6 纳米（本文件所有坐标的单位都是纳米）


def _line(uuid: str, p1: tuple[int, int], p2: tuple[int, int]) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]}}


def _circle(uuid: str, center: tuple[int, int], r: int) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "circle", "layer": "0", "construction": False,
            "geom": {"cx": center[0], "cy": center[1], "r": r}}


def _arc(uuid: str, center: tuple[int, int], r: int, start: float, end: float) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "arc", "layer": "0", "construction": False,
            "geom": {"cx": center[0], "cy": center[1], "r": r, "start_deg": start, "end_deg": end}}


def _polyline(uuid: str, points: list[tuple[int, int]]) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "polyline", "layer": "0", "construction": False,
            "geom": {"points": [[x, y] for x, y in points]}}


def _sketch(*entities: dict[str, Any]) -> dict[str, Any]:
    """L1 草图：`{"entities":[...], "constraints":[...], "dimensions":[...]}`（共享接口原文）。"""
    return {"entities": list(entities), "constraints": [], "dimensions": []}


class _Obj:
    """对象形态夹具（证明按结构读：属性而非映射键）。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


LINE_SK = _sketch(_line("L1", (0, 0), (10 * MM, 0)))
CIRCLE_SK = _sketch(_circle("C1", (0, 0), 5 * MM))
ARC_SK = _sketch(_arc("A1", (0, 0), 10 * MM, 0.0, 90.0))
POLY_SK = _sketch(_polyline("P1", [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)]))


def _point(uuid: str, at: tuple[int, int]) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "point", "layer": "0", "construction": False,
            "geom": {"x": at[0], "y": at[1]}}


POINT_SK = _sketch(_point("PT1", (3 * MM, 4 * MM)))
# 判别夹具：点实体与线端点 / 线中点**同坐标**，用来量"新类插在哪一档"。
POINT_VS_ENDPOINT_SK = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _point("PT1", (0, 0)))
POINT_VS_MIDPOINT_SK = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _point("PT1", (5 * MM, 0)))


# --------------------------------------------------------------------------------------
# 一之二、点实体（kind="point"）进捕捉面：真命中 / 优先级口径 / 落空 / 坏几何跳过
# --------------------------------------------------------------------------------------


class PointEntitySnapTest(unittest.TestCase):
    """`kind="point"`（geom={x,y}）的回归尺子：缺口是 `_parse` 只认 line/circle/arc/polyline。

    口径（与 `osnap` 模块 docstring 一致，写死在这里便于回归时一眼看出被改没改）：
    * `point` 是新增的第 8 个捕捉类，固定在第 2 位 ⇒ `endpoint > point > midpoint > ...`；
    * 点实体**只**参与 `point` 类，不冒充 endpoint/nearest（所以每个"不冒充"的类都必须落空）；
    * 命中判据与既有 7 类同一条：`distance <= tolerance_nm` 严格取等算命中，超出 ⇒ `None`；
    * 坏几何（缺字段 / 非有限 / 缺 geom）按"跳过"处理，**不抛异常**，好点照常命中。

    ⚠️ 与 `cadcore.ops.draw_extra.snap_point` 的已知差异（本席实读，非本文件口径）：那个助手把
    `point` 排在**最前**（它自己的冻结口径）；本文件是 `endpoint > point`。两处各自冻结，
    要哪一种就调哪一个。
    """

    def test_point_entity_is_hit_exactly_with_integer_nm(self) -> None:
        res = snap(POINT_SK, (3 * MM, 4 * MM), kinds=("point",), tolerance_nm=1)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.kind, "point")
        self.assertEqual(res.point_nm, (3 * MM, 4 * MM))
        self.assertEqual(res.ref, ("PT1", "point"))
        self.assertEqual(res.distance_nm, 0)
        self.assertIsInstance(res.point_nm[0], int)
        self.assertIsInstance(res.point_nm[1], int)

    def test_point_kind_slot_and_the_seven_existing_kinds_are_untouched(self) -> None:
        self.assertIn("point", SNAP_KINDS)
        self.assertEqual(len(SNAP_KINDS), 8)
        self.assertEqual(SNAP_KINDS[1], "point")
        self.assertEqual(tuple(kind for kind in SNAP_KINDS if kind != "point"),
                         ("endpoint", "midpoint", "center", "quadrant", "intersection",
                          "perpendicular", "nearest"))

    def test_point_entity_is_hit_with_default_kinds(self) -> None:
        res = snap(POINT_SK, (3 * MM + 1_000, 4 * MM), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "point")
        self.assertEqual(res.point_nm, (3 * MM, 4 * MM))
        self.assertEqual(res.distance_nm, 1_000)

    def test_point_tolerance_boundary_is_strict_less_equal(self) -> None:
        inside = snap(POINT_SK, (3 * MM + 1_000, 4 * MM), kinds=("point",), tolerance_nm=1_000)
        outside = snap(POINT_SK, (3 * MM + 1_001, 4 * MM), kinds=("point",), tolerance_nm=1_000)
        assert inside is not None
        self.assertEqual(inside.distance_nm, 1_000)  # 正好等于容差 = 命中
        self.assertIsNone(outside)

    def test_point_loses_to_endpoint_but_beats_midpoint(self) -> None:
        # (1) 同坐标上有线的端点与点实体 ⇒ endpoint 更高，点实体不顶掉它。
        endpoint_win = snap(POINT_VS_ENDPOINT_SK, (100_000, 0), tolerance_nm=MM)
        assert endpoint_win is not None
        self.assertEqual(endpoint_win.kind, "endpoint")
        self.assertEqual(endpoint_win.ref, ("L1", "start"))
        # 阳性对照：同一夹具只放开 point 类 ⇒ 点实体确实可达（证明上面不是"点根本不可见"）。
        point_only = snap(POINT_VS_ENDPOINT_SK, (100_000, 0), kinds=("point",), tolerance_nm=MM)
        assert point_only is not None
        self.assertEqual(point_only.kind, "point")
        self.assertEqual(point_only.point_nm, (0, 0))
        # (2) 同坐标上有线段中点与点实体 ⇒ point 在 midpoint 之前，改判 point（写清的口径）。
        point_wins = snap(POINT_VS_MIDPOINT_SK, (5 * MM, 0), tolerance_nm=MM)
        assert point_wins is not None
        self.assertEqual(point_wins.kind, "point")
        self.assertEqual(point_wins.point_nm, (5 * MM, 0))
        # 阳性对照：关掉 point 类，同一问点仍命中 midpoint（证明线还在，不是几何消失）。
        midpoint_win = snap(POINT_VS_MIDPOINT_SK, (5 * MM, 0), kinds=("midpoint",), tolerance_nm=MM)
        assert midpoint_win is not None
        self.assertEqual(midpoint_win.kind, "midpoint")
        self.assertEqual(midpoint_win.ref, ("L1", "midpoint"))  # 线段中点（非多段线的 midpoint:<i>）

    def test_point_entity_miss_returns_none(self) -> None:
        self.assertIsNone(snap(POINT_SK, (3 * MM + 2, 4 * MM), kinds=("point",), tolerance_nm=1))
        self.assertIsNone(snap(_sketch(), (0, 0), kinds=("point",), tolerance_nm=MM))
        # 只有点实体时，问点落在点上但 kinds 不含 point ⇒ 落空（点不冒充别的类）。
        self.assertIsNone(snap(POINT_SK, (3 * MM, 4 * MM), kinds=("nearest",), tolerance_nm=MM))

    def test_point_entity_does_not_impersonate_the_other_kinds(self) -> None:
        for kind in ("endpoint", "midpoint", "center", "quadrant", "perpendicular", "nearest"):
            with self.subTest(kind=kind):
                self.assertIsNone(snap(POINT_SK, (3 * MM, 4 * MM), kinds=(kind,), tolerance_nm=MM))

    def test_point_bad_geometry_is_skipped_not_raised(self) -> None:
        broken = _sketch(
            {"uuid": "P0", "kind": "point", "geom": {"x": 1}},                      # 缺 y
            {"uuid": "P1", "kind": "point", "geom": {"x": 1, "y": float("nan")}},   # 非有限
            {"uuid": "P2", "kind": "point"},                                        # 缺 geom
            _point("PT1", (7 * MM, 8 * MM)),                                        # 好点：仍必须命中
        )
        res = snap(broken, (7 * MM, 8 * MM), kinds=("point",), tolerance_nm=1)
        assert res is not None
        self.assertEqual(res.ref, ("PT1", "point"))
        self.assertEqual(res.point_nm, (7 * MM, 8 * MM))

    def test_point_object_form_hits_and_unknown_kinds_still_raise(self) -> None:
        obj = _Obj(uuid="PO", kind="point", geom=_Obj(x=9 * MM, y=-2 * MM))
        res = snap(_sketch(obj), (9 * MM, -2 * MM), kinds=("point",), tolerance_nm=1)
        assert res is not None
        self.assertEqual(res.point_nm, (9 * MM, -2 * MM))
        self.assertEqual(res.ref, ("PO", "point"))
        # L1 无 node/insert/insertion 实体 kind ⇒ 本文件不发明这三类；未知 kind 名照旧必须炸。
        for bad in ("node", "insert", "insertion"):
            with self.subTest(kind=bad):
                with self.assertRaises(ValueError):
                    snap(POINT_SK, (3 * MM, 4 * MM), kinds=(bad,), tolerance_nm=MM)


# --------------------------------------------------------------------------------------
# 一、对象捕捉：端点 / 中点 / 圆心 / 象限点
# --------------------------------------------------------------------------------------


class EndpointMidpointCenterQuadrantTest(unittest.TestCase):
    def test_endpoint_line_exact_point_distance_and_ref(self) -> None:
        res = snap(LINE_SK, (300_000, 400_000), kinds=("endpoint",), tolerance_nm=MM)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.kind, "endpoint")
        self.assertEqual(res.point_nm, (0, 0))
        self.assertEqual(res.distance_nm, 500_000)  # 3-4-5 三角形，整数
        self.assertEqual(res.ref, ("L1", "start"))
        self.assertIsInstance(res.point_nm[0], int)
        self.assertIsInstance(res.point_nm[1], int)

    def test_endpoint_picks_the_nearer_end(self) -> None:
        res = snap(LINE_SK, (9_900_000, 100_000), kinds=("endpoint",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (10 * MM, 0))
        self.assertEqual(res.ref, ("L1", "end"))

    def test_midpoint_line(self) -> None:
        res = snap(LINE_SK, (5_000_000, 200_000), kinds=("midpoint",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "midpoint")
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertEqual(res.ref, ("L1", "midpoint"))

    def test_priority_endpoint_beats_a_closer_nearest(self) -> None:
        # 问点就在线上：nearest 命中距离是 0，但 endpoint 优先级更高 ⇒ 必须返回 endpoint。
        res = snap(LINE_SK, (100_000, 0), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "endpoint")
        self.assertEqual(res.point_nm, (0, 0))
        self.assertEqual(res.distance_nm, 100_000)

    def test_circle_center_and_priority_over_quadrant(self) -> None:
        res = snap(CIRCLE_SK, (300_000, 0), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "center")
        self.assertEqual(res.point_nm, (0, 0))
        self.assertEqual(res.ref, ("C1", "center"))

    def test_circle_all_four_quadrants(self) -> None:
        cases = (
            ((5_100_000, 100_000), (5 * MM, 0), "quadrant:0"),
            ((100_000, 5_100_000), (0, 5 * MM), "quadrant:90"),
            ((-5_100_000, -100_000), (-5 * MM, 0), "quadrant:180"),
            ((-100_000, -5_100_000), (0, -5 * MM), "quadrant:270"),
        )
        for point, expected, sub in cases:
            with self.subTest(sub=sub):
                res = snap(CIRCLE_SK, point, kinds=("quadrant",), tolerance_nm=MM)
                assert res is not None
                self.assertEqual(res.kind, "quadrant")
                self.assertEqual(res.point_nm, expected)
                self.assertEqual(res.ref, ("C1", sub))

    def test_quadrant_beats_a_closer_nearest(self) -> None:
        res = snap(CIRCLE_SK, (5_100_000, 100_000), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "quadrant")
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertLessEqual(res.distance_nm, MM)

    def test_arc_endpoints_are_derived_from_angles(self) -> None:
        start_hit = snap(ARC_SK, (9_900_000, 100_000), kinds=("endpoint",), tolerance_nm=MM)
        end_hit = snap(ARC_SK, (100_000, 9_900_000), kinds=("endpoint",), tolerance_nm=MM)
        assert start_hit is not None and end_hit is not None
        self.assertEqual(start_hit.point_nm, (10 * MM, 0))
        self.assertEqual(start_hit.ref, ("A1", "start"))
        self.assertEqual(end_hit.point_nm, (0, 10 * MM))
        self.assertEqual(end_hit.ref, ("A1", "end"))

    def test_arc_midpoint_is_half_sweep(self) -> None:
        res = snap(ARC_SK, (7_071_068, 7_071_068), kinds=("midpoint",), tolerance_nm=1000)
        assert res is not None
        self.assertEqual(res.kind, "midpoint")
        self.assertEqual(res.point_nm, (7_071_068, 7_071_068))  # r*cos45 取整
        self.assertEqual(res.ref, ("A1", "midpoint"))

    def test_arc_midpoint_wraps_through_zero(self) -> None:
        sk = _sketch(_arc("A2", (0, 0), 10 * MM, 350.0, 10.0))
        res = snap(sk, (9_900_000, 100_000), kinds=("midpoint",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (10 * MM, 0))

    def test_arc_quadrant_only_when_on_arc(self) -> None:
        on_arc = snap(ARC_SK, (100_000, 9_900_000), kinds=("quadrant",), tolerance_nm=MM)
        assert on_arc is not None
        self.assertEqual(on_arc.point_nm, (0, 10 * MM))
        self.assertEqual(on_arc.ref, ("A1", "quadrant:90"))
        # 90 度弧不含 180 度象限点：问点就在 (-10mm, 0) 附近，仍必须落空
        off_arc = snap(ARC_SK, (-9_900_000, 100_000), kinds=("quadrant",), tolerance_nm=MM)
        self.assertIsNone(off_arc)

    def test_arc_quadrant_on_three_quarter_arc(self) -> None:
        sk = _sketch(_arc("A3", (0, 0), 10 * MM, 0.0, 270.0))
        res = snap(sk, (-9_900_000, 100_000), kinds=("quadrant",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (-10 * MM, 0))
        self.assertEqual(res.ref, ("A3", "quadrant:180"))

    def test_polyline_vertices_and_segment_midpoints(self) -> None:
        vertex = snap(POLY_SK, (9_900_000, 100_000), kinds=("endpoint",), tolerance_nm=MM)
        middle = snap(POLY_SK, (5_100_000, 100_000), kinds=("midpoint",), tolerance_nm=MM)
        assert vertex is not None and middle is not None
        self.assertEqual(vertex.point_nm, (10 * MM, 0))
        self.assertEqual(vertex.ref, ("P1", "vertex:1"))
        self.assertEqual(middle.point_nm, (5 * MM, 0))
        self.assertEqual(middle.ref, ("P1", "midpoint:0"))


# --------------------------------------------------------------------------------------
# 二、最近点 / 垂足 / 容差 / kinds 过滤
# --------------------------------------------------------------------------------------


class NearestPerpendicularToleranceTest(unittest.TestCase):
    def test_nearest_on_line_is_clamped_to_the_segment(self) -> None:
        res = snap(LINE_SK, (15 * MM, 1 * MM), kinds=("nearest",), tolerance_nm=10 * MM)
        assert res is not None
        self.assertEqual(res.kind, "nearest")
        self.assertEqual(res.point_nm, (10 * MM, 0))  # 夹到线段端点，不是延长线上的垂足
        self.assertEqual(res.ref, ("L1", "nearest"))

    def test_nearest_on_circle_is_radial(self) -> None:
        res = snap(CIRCLE_SK, (10 * MM, 0), kinds=("nearest",), tolerance_nm=10 * MM)
        assert res is not None
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertEqual(res.ref, ("C1", "nearest"))

    def test_nearest_on_arc_outside_sweep_falls_back_to_endpoint(self) -> None:
        res = snap(ARC_SK, (-10 * MM, 1 * MM), kinds=("nearest",), tolerance_nm=100 * MM)
        assert res is not None
        self.assertEqual(res.point_nm, (0, 10 * MM))  # 弧上距该点最近的是 end 端

    def test_nearest_on_polyline_uses_second_segment(self) -> None:
        res = snap(POLY_SK, (11 * MM, 5 * MM), kinds=("nearest",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (10 * MM, 5 * MM))

    def test_perpendicular_on_line_accepts_the_extension(self) -> None:
        res = snap(LINE_SK, (15 * MM, 1 * MM), kinds=("perpendicular",), tolerance_nm=2 * MM)
        assert res is not None
        self.assertEqual(res.kind, "perpendicular")
        self.assertEqual(res.point_nm, (15 * MM, 0))  # 延长线上的垂足（AutoCAD 口径）
        self.assertEqual(res.ref, ("L1", "perpendicular"))

    def test_perpendicular_on_line_inside_the_segment(self) -> None:
        # 垂足 (5mm, 0) 距问点 3mm ⇒ 容差 1mm 时不该命中（命中半径是"问到候选点"的距离）
        self.assertIsNone(snap(LINE_SK, (5 * MM, 3 * MM), kinds=("perpendicular",), tolerance_nm=MM))
        res = snap(LINE_SK, (5 * MM, 3 * MM), kinds=("perpendicular",), tolerance_nm=5 * MM)
        assert res is not None
        self.assertEqual(res.point_nm, (5 * MM, 0))

    def test_perpendicular_on_circle_is_along_the_radius(self) -> None:
        res = snap(CIRCLE_SK, (3 * MM, 4 * MM), kinds=("perpendicular",), tolerance_nm=1000)
        assert res is not None
        self.assertEqual(res.kind, "perpendicular")
        self.assertEqual(res.point_nm, (3 * MM, 4 * MM))  # |P|=r，垂足就是 P 本身
        self.assertEqual(res.distance_nm, 0)

    def test_tolerance_miss_returns_none(self) -> None:
        res = snap(LINE_SK, (3 * MM, 3 * MM), kinds=("endpoint", "midpoint"), tolerance_nm=100_000)
        self.assertIsNone(res)

    def test_kinds_filter_is_actually_applied(self) -> None:
        # 端点更近，但 kinds 里没有 endpoint ⇒ 只能命中中点（距离 4.9mm，容差 10mm 内）。
        res = snap(LINE_SK, (100_000, 0), kinds=("midpoint",), tolerance_nm=10 * MM)
        assert res is not None
        self.assertEqual(res.kind, "midpoint")
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertEqual(res.distance_nm, 4_900_000)

    def test_empty_kinds_and_empty_sketch_return_none(self) -> None:
        self.assertIsNone(snap(LINE_SK, (100_000, 0), kinds=(), tolerance_nm=10 * MM))
        self.assertIsNone(snap(_sketch(), (0, 0), tolerance_nm=10 * MM))
        self.assertIsNone(snap(None, (0, 0), tolerance_nm=10 * MM))

    def test_zero_tolerance_still_hits_exactly(self) -> None:
        res = snap(LINE_SK, (0, 0), kinds=("endpoint",), tolerance_nm=0)
        assert res is not None
        self.assertEqual(res.point_nm, (0, 0))
        self.assertEqual(res.distance_nm, 0)

    def test_default_kinds_are_the_frozen_priority_order(self) -> None:
        # `point`（点实体，第 2 位）是新插进来的第 8 类：既有 7 类的**相对顺序一字未改**，
        # 新类只插在 endpoint 之后（见 osnap 模块 docstring 的"命中优先级"）。顺序即语义，
        # 所以这条钉子仍然用**全等**断言，改顺序必须在这里显式改。
        self.assertEqual(
            SNAP_KINDS,
            ("endpoint", "point", "midpoint", "center", "quadrant", "intersection", "perpendicular",
             "nearest"),
        )


# --------------------------------------------------------------------------------------
# 三、交点：只做 line×line 与 line×circle；无解返回 None，绝不抛异常
# --------------------------------------------------------------------------------------


class IntersectionTest(unittest.TestCase):
    def test_line_line_intersection(self) -> None:
        sk = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _line("L2", (5 * MM, -5 * MM), (5 * MM, 5 * MM)))
        res = snap(sk, (5_100_000, 100_000), kinds=("intersection",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.kind, "intersection")
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertEqual(res.ref, ("L1", "intersection", "L2", "intersection"))
        self.assertEqual(res.distance_nm, 141_421)  # hypot(1e5, 1e5) 取整

    def test_parallel_lines_return_none_without_raising(self) -> None:
        sk = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _line("L3", (0, 2 * MM), (10 * MM, 2 * MM)))
        self.assertIsNone(snap(sk, (5 * MM, MM), kinds=("intersection",), tolerance_nm=10 * MM))

    def test_collinear_lines_return_none(self) -> None:
        sk = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _line("L4", (20 * MM, 0), (30 * MM, 0)))
        self.assertIsNone(snap(sk, (10 * MM, 0), kinds=("intersection",), tolerance_nm=100 * MM))

    def test_intersection_outside_segments_returns_none(self) -> None:
        # 支撑直线交于 (5mm, 0)，但它在 L4 的**线段**之外（t < 0）⇒ 不命中
        sk = _sketch(_line("L4", (20 * MM, 0), (30 * MM, 0)), _line("L2", (5 * MM, -5 * MM), (5 * MM, 5 * MM)))
        self.assertIsNone(snap(sk, (5 * MM, 0), kinds=("intersection",), tolerance_nm=100 * MM))

    def test_line_circle_two_roots(self) -> None:
        sk = _sketch(_circle("C1", (0, 0), 5 * MM), _line("L5", (-10 * MM, 0), (10 * MM, 0)))
        res = snap(sk, (4_900_000, 0), kinds=("intersection",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (5 * MM, 0))
        self.assertEqual(res.ref, ("C1", "intersection", "L5", "intersection"))

    def test_line_circle_tangent_single_root(self) -> None:
        sk = _sketch(_circle("C1", (0, 0), 5 * MM), _line("L6", (-10 * MM, 5 * MM), (10 * MM, 5 * MM)))
        res = snap(sk, (100_000, 5_000_000), kinds=("intersection",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (0, 5 * MM))

    def test_line_circle_miss_returns_none(self) -> None:
        sk = _sketch(_circle("C1", (0, 0), 5 * MM), _line("L7", (-10 * MM, 20 * MM), (10 * MM, 20 * MM)))
        self.assertIsNone(snap(sk, (0, 20 * MM), kinds=("intersection",), tolerance_nm=10 * MM))

    def test_polyline_arc_and_circle_circle_are_not_intersection_candidates(self) -> None:
        # 只做 line×line 与 line×circle：polyline 参与、arc 参与、circle×circle 一律不返回候选
        poly_line = _sketch(
            _polyline("P1", [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)]),
            _line("L8", (5 * MM, -5 * MM), (5 * MM, 5 * MM)),
        )
        self.assertIsNone(snap(poly_line, (5 * MM, 0), kinds=("intersection",), tolerance_nm=100 * MM))
        arc_line = _sketch(_arc("A1", (0, 0), 10 * MM, 0.0, 90.0), _line("L1", (0, 0), (10 * MM, 0)))
        self.assertIsNone(snap(arc_line, (10 * MM, 0), kinds=("intersection",), tolerance_nm=100 * MM))
        two_circles = _sketch(_circle("C1", (0, 0), 5 * MM), _circle("C2", (8 * MM, 0), 5 * MM))
        self.assertIsNone(snap(two_circles, (4 * MM, 3 * MM), kinds=("intersection",), tolerance_nm=100 * MM))

    def test_malformed_entities_never_raise(self) -> None:
        sk = _sketch(
            {"uuid": "B1", "kind": "line", "geom": {"x1": "a", "y1": 0, "x2": 1, "y2": 0}},
            {"uuid": "B2", "kind": "circle", "geom": {"cx": 0, "cy": 0, "r": 0}},
            {"uuid": "B3", "kind": "spline", "geom": {}},
            {"uuid": "B4", "kind": "polyline", "geom": {"points": [[0, 0]]}},
            {"uuid": "B5", "kind": "line"},
            {"uuid": "B6", "kind": "arc", "geom": {"cx": 0, "cy": 0, "r": -5, "start_deg": 0, "end_deg": 90}},
            {"uuid": "B7", "kind": "polyline", "geom": {"points": [[0, 0], [1, 1]]}},
        )
        all_kinds = {"endpoint", "midpoint", "center", "quadrant", "intersection", "perpendicular", "nearest"}
        res = snap(sk, (100, 100), kinds=tuple(all_kinds), tolerance_nm=10 * MM)
        assert res is not None
        # 唯一一条合法实体（B7 折线）照常命中：问点 (100,100) 离末端 (1,1) 更近 ⇒ end
        self.assertEqual(res.ref, ("B7", "end"))
        self.assertIsNone(snap({"entities": "not-a-list"}, (0, 0), tolerance_nm=MM))
        self.assertIsNone(snap(42, (0, 0), tolerance_nm=MM))


# --------------------------------------------------------------------------------------
# 四、正交锁定 / 极轴追踪 / 栅格步进
# --------------------------------------------------------------------------------------


class OrthoPolarGridTest(unittest.TestCase):
    def test_ortho_lock_snaps_to_horizontal_and_vertical(self) -> None:
        self.assertEqual(ortho_lock((0, 0), (10 * MM, 100_000)), (10 * MM, 0))
        self.assertEqual(ortho_lock((0, 0), (-10 * MM, 100_000)), (-10 * MM, 0))  # 180 度侧
        self.assertEqual(ortho_lock((0, 0), (100_000, 10 * MM)), (0, 10 * MM))
        self.assertEqual(ortho_lock((0, 0), (-100_000, -10 * MM)), (0, -10 * MM))  # 270 度侧

    def test_ortho_lock_respects_tolerance_boundary(self) -> None:
        # atan(0.0524) = 2.9997 度 <= 3.0 ⇒ 锁；0.06 ⇒ 3.43 度 > 3.0 ⇒ 原样返回
        self.assertEqual(ortho_lock((0, 0), (10 * MM, 524_000)), (10 * MM, 0))
        self.assertEqual(ortho_lock((0, 0), (10 * MM, 600_000)), (10 * MM, 600_000))
        self.assertEqual(ortho_lock((0, 0), (10 * MM, 600_000), tolerance_deg=5.0), (10 * MM, 0))

    def test_ortho_lock_non_axis_and_degenerate_cases(self) -> None:
        self.assertEqual(ortho_lock((0, 0), (10 * MM, 10 * MM)), (10 * MM, 10 * MM))
        self.assertEqual(ortho_lock((5, 5), (5, 5)), (5, 5))  # 与基点重合：方向未定义，原样返回
        self.assertEqual(ortho_lock((MM, 2 * MM), (11 * MM, 2_100_000)), (11 * MM, 2 * MM))

    def test_polar_track_snaps_and_preserves_radius(self) -> None:
        self.assertEqual(polar_track((0, 0), (10 * MM, 100_000)), (10_000_500, 0))
        self.assertEqual(polar_track((0, 0), (0, 10 * MM)), (0, 10 * MM))
        self.assertEqual(polar_track((0, 0), (100_000, 10 * MM)), (0, 10_000_500))
        self.assertEqual(polar_track((0, 0), (7 * MM, 7 * MM)), (7 * MM, 7 * MM))
        self.assertEqual(polar_track((MM, MM), (11 * MM, MM)), (11 * MM, MM))

    def test_polar_track_no_snap_outside_tolerance(self) -> None:
        # 34.99 度：离最近的 30 度增量差 4.99 度 > 3.0 ⇒ 原样返回
        self.assertEqual(polar_track((0, 0), (10 * MM, 7 * MM)), (10 * MM, 7 * MM))

    def test_polar_track_increment_selection(self) -> None:
        point = (7 * MM, 7 * MM)  # 45 度
        self.assertEqual(polar_track((0, 0), point, increments_deg=(90,)), point)  # 只有 0/90/180/270
        self.assertEqual(polar_track((0, 0), point, increments_deg=(45,)), point)
        self.assertEqual(polar_track((0, 0), point, increments_deg=(15, 30)), point)  # 45 不在增量里
        self.assertEqual(polar_track((5, 5), (5, 5)), (5, 5))  # 与基点重合

    def test_grid_snap_steps_and_rounds_half_away_from_zero(self) -> None:
        self.assertEqual(grid_snap((1_400_000, -1_600_000), spacing_nm=MM), (MM, -2 * MM))
        self.assertEqual(grid_snap((1_500_000, -1_500_000), spacing_nm=MM), (2 * MM, -2 * MM))
        self.assertEqual(grid_snap((3 * MM, 0), spacing_nm=MM), (3 * MM, 0))
        self.assertEqual(grid_snap((1_200_000, -1_200_000), spacing_nm=500_000), (MM, -MM))
        self.assertEqual(grid_snap((7, -7), spacing_nm=1), (7, -7))


# --------------------------------------------------------------------------------------
# 五、入参把关、真件集成、结构读法、无状态
# --------------------------------------------------------------------------------------


class ArgumentValidationTest(unittest.TestCase):
    def test_bad_points_and_tolerances_raise(self) -> None:
        for point in ((1.5, 0), (0,), "xy", (0, 0, 0)):
            with self.subTest(point=point):
                with self.assertRaises(ValueError):
                    snap(LINE_SK, point, tolerance_nm=MM)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            snap(LINE_SK, (0, 0), tolerance_nm=-1)
        with self.assertRaises(ValueError):
            snap(LINE_SK, (0, 0), tolerance_nm=float("nan"))
        with self.assertRaises(ValueError):
            snap(LINE_SK, (0, 0), kinds=("endpoint", "bogus"), tolerance_nm=MM)
        with self.assertRaises(ValueError):
            ortho_lock((0, 0), (1, 1), tolerance_deg=-1)
        with self.assertRaises(ValueError):
            grid_snap((0, 0), spacing_nm=0)
        with self.assertRaises(ValueError):
            grid_snap((0, 0), spacing_nm=1.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            polar_track((0, 0), (1, 1), increments_deg=())
        with self.assertRaises(ValueError):
            polar_track((0, 0), (1, 1), increments_deg=(0,))
        with self.assertRaises(ValueError):
            polar_track((0, 0), (1, 1), increments_deg=(400,))

    def test_snap_result_is_json_serialisable(self) -> None:
        res = snap(LINE_SK, (100_000, 0), kinds=("endpoint",), tolerance_nm=MM)
        assert res is not None
        payload = json.loads(json.dumps(res.as_dict()))
        self.assertEqual(payload["kind"], "endpoint")
        self.assertEqual(payload["point_nm"], [0, 0])
        self.assertIsInstance(SnapResult("endpoint", (0, 0), ("L1", "start"), 0), SnapResult)

    def test_package_reexports_are_the_same_objects(self) -> None:
        self.assertIs(pkg_snap, snap)
        self.assertIs(pkg_ortho_lock, ortho_lock)
        self.assertIs(pkg_polar_track, polar_track)
        self.assertIs(pkg_grid_snap, grid_snap)


class StructuralAndIntegrationTest(unittest.TestCase):
    def test_object_form_sketch_is_accepted(self) -> None:
        geom = _Obj(x1=0, y1=0, x2=10 * MM, y2=0)
        entity = _Obj(uuid="O1", kind="line", layer="0", construction=False, geom=geom)
        sketch = _Obj(entities=[entity], constraints=[], dimensions=[])
        res = snap(sketch, (9_900_000, 100_000), kinds=("endpoint",), tolerance_nm=MM)
        assert res is not None
        self.assertEqual(res.point_nm, (10 * MM, 0))
        self.assertEqual(res.ref, ("O1", "end"))

    def test_reads_a_real_l1_document_built_by_a9_commands(self) -> None:
        from cadcore.sketch.commands import add_arc, add_circle, add_line

        sk: dict[str, Any] = {"entities": [], "constraints": [], "dimensions": []}
        line_uuid = add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        circle_uuid = add_circle(sk, center=(20 * MM, 0), radius_nm=5 * MM)
        arc_uuid = add_arc(sk, center=(0, 20 * MM), radius_nm=5 * MM, start_deg=0.0, end_deg=90.0)
        self.assertEqual(len(sk["entities"]), 3)

        endpoint = snap(sk, (300_000, 400_000), kinds=("endpoint",), tolerance_nm=MM)
        assert endpoint is not None
        self.assertEqual(endpoint.ref, (line_uuid, "start"))
        self.assertEqual(endpoint.point_nm, (0, 0))

        quadrant = snap(sk, (24_900_000, 100_000), kinds=("quadrant",), tolerance_nm=MM)
        assert quadrant is not None
        self.assertEqual(quadrant.ref, (circle_uuid, "quadrant:0"))
        self.assertEqual(quadrant.point_nm, (25 * MM, 0))

        arc_start = snap(sk, (4_900_000, 20_100_000), kinds=("endpoint",), tolerance_nm=MM)
        assert arc_start is not None
        self.assertEqual(arc_start.ref, (arc_uuid, "start"))
        self.assertEqual(arc_start.point_nm, (5 * MM, 20 * MM))

    def test_snap_is_stateless_and_does_not_mutate_the_sketch(self) -> None:
        sk = _sketch(_line("L1", (0, 0), (10 * MM, 0)), _circle("C1", (0, 0), 5 * MM))
        before = copy.deepcopy(sk)
        for point in ((100_000, 0), (5 * MM, 5 * MM), (4_900_000, 0), (0, 0)):
            snap(sk, point, tolerance_nm=MM)
            ortho_lock((0, 0), point)
            polar_track((0, 0), point)
            grid_snap(point, spacing_nm=MM)
        self.assertEqual(sk, before)
        first = snap(sk, (100_000, 0), kinds=("endpoint",), tolerance_nm=MM)
        second = snap(sk, (100_000, 0), kinds=("endpoint",), tolerance_nm=MM)
        self.assertEqual(first, second)  # 确定性：同输入同输出


if __name__ == "__main__":
    unittest.main()
