#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w20 —— 片模块（family=hatch_measure，席位 w20）。

负责的 must_have 条目
--------------------
族状态件 `docs/research/cad-parity/status/autocad-hatch-measure.json` 里仍是 missing/partial 的
must_have 只有两条，按清单顺序重编号 0..1：
  0 = 渐变色填充（partial，L1 无颜色字段 ⇒ 本层只能用声明形态）
  1 = **通用测量（含悬停快速读数）**（partial）
本席取编号 11 对 2 取模 = 1 ⇒ 实现 **通用测量（含悬停快速读数）**。

本片补齐的两条缺口（都真跑真几何，不是桩）
------------------------------------------
1. **统一入口缺失**：族模块只有 `measure_area` / `measure_perimeter` / `measure_distance` 三条分列 op，
   OPERATIONS 表内没有 `measure_geom`。本片补 `measure_geom`（别名 `measure`）：`mode` ∈
   ``auto`` / ``distance`` / ``area`` / ``perimeter`` / ``all``；``auto`` = 给了距离操作数就走 distance，
   否则走 all（area + perimeter）。三条腿的结果**各自加腿前缀**（``distance.`` / ``area.`` /
   ``perimeter.``）再合并 —— 因为 area 腿与 perimeter 腿都产 ``perimeter_nm`` / ``closed``，
   不加前缀就是"同一个键两套话"。
2. **悬停/实时快速读数缺失**：补 `measure_hover`（别名 `hover_measure`）与 `measure_hover_track`
   （别名 `hover_track`）。光标点 → 拾取孔径内的候选实体 → 最近的实体 → 一次给全快速读数；
   轨迹版吃一串光标采样、逐样出读数。两者**只读**：不建表、不改实体、不动族表。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "hatch_measure"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数（含别名，别名与主名同函数）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``。本片全是**只读测量** ⇒ 返回 ``"key=value"``
  结果 token 列表（token 名与顺序在各 op docstring 里写死），不返回 uuid（没有新增实体）。
- 长度一律**整数纳米（nm）**（面积 nm²）；错误一律抛 ``cadcore.errors.CadError(reason=...)``。

口径（与族模块一致，别自己发明）
------------------------------
- ``0 = 真为零``；``-1 = 未采``；``NA = 不适用`` —— 三者不同形。悬停 miss ⇒ ``hover_hit=0`` 且
  ``uuid=NA`` / ``cursor_to_entity_nm=NA`` / ``candidate_count=0``（"没命中"与"命中但距离为 0"不同形）。
- 孔径：``tol_nm`` 显式给值就用它（``0`` = 严格命中：只有几何真穿过光标点才算命中）；没给（``None``）
  先读族表的 ``gap_nm``（HPGAPTOL 同源），仍为 0 时用本片缺省拾取孔径 ``_DEFAULT_HOVER_TOL_NM``
  （5 mm，等价于 AutoCAD 的 pickbox 取一个整数纳米常数）。实际用的孔径写在 ``tol_nm=`` token 里，
  下游不需要猜。
- 最近点是真几何：line / polyline 走线段投影（端点内夹紧），circle 走径向投影（解析），arc 走
  "径向角落在扫掠范围内则径向投影、否则取两端点最近者"。距离再四舍五入到整数纳米。
- 悬停面积只对"实体自身成环"的实体给值（circle；首末点重合的 polyline），其余 ``NA``
  （一条直线没有面积 ⇒ 不适用，不是 0）。

落盘形态与有意限定的边界（写下来，防止下游靠猜）
--------------------------------------------
1. 本片只交付**内核侧**读数（统一入口 + 悬停读数 + 轨迹读数）。apps 层"悬停即显数"的消费方不在本片
   写面（族状态件 top_gaps 第 2 条点名的 apps 侧 0 命中由其它席负责），本片不宣称渲染/UI 已通。
2. 复用而非重写：几何/成环/测量全部走 ``cadcore.ops.hatch_measure`` 的既有实现（``_entities`` /
   ``_entity_points`` / ``_curve_length`` / ``_shoelace`` / ``_loop_metrics`` 同一族的 ``measure_*``），
   入参校验复用 ``_point`` / ``_nm`` / ``_gap_tolerance`` / ``_fail``。本文件不抄第二份校验器。
3. 不写 ``__init__.py``（归别的席）；直跑本文件时用文件末尾的引导自举 ``sys.path``。
"""

from __future__ import annotations

import math
import os as _os
import sys as _sys
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/hatch_measure__w20.py 时的引导
    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)

from cadcore.edit.modify import _round_nm
from cadcore.errors import CadError
from cadcore.ops import hatch_measure as _HM
from cadcore.sketch.constraints import _get

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "measure_geom",
    "measure_hover",
    "measure_hover_track",
    "selftest",
]

FAMILY = "hatch_measure"

#: 缺省悬停拾取孔径（5 mm，整数纳米）。只在族表 gap_nm = 0 且调用方没给 tol_nm 时生效。
_DEFAULT_HOVER_TOL_NM = 5_000_000

_MODES: tuple[str, ...] = ("auto", "distance", "area", "perimeter", "all")

#: 距离腿在 `mode=all`、但调用方没给距离操作数时的填位 token（NA = 不适用，不是 0）。
_DISTANCE_NA = ["distance_nm=NA", "dx_nm=NA", "dy_nm=NA", "nearest_a_nm=NA", "nearest_b_nm=NA"]


# ======================================================================================
# 小工具
# ======================================================================================


def _token_map(tokens: Any) -> dict[str, str]:
    """``["k=v", ...]`` → ``{"k": "v"}``（只按第一个 ``=`` 切，值里允许有 ``=``）。"""
    table: dict[str, str] = {}
    for token in tokens or []:
        text = str(token)
        key, _, value = text.partition("=")
        table[key] = value
    return table


def _prefixed(leg: str, tokens: Any) -> list[str]:
    """给一条腿的 token 加腿前缀（见模块 docstring 第 1 条：避免同名键两套话）。"""
    return ["%s.%s" % (leg, str(token)) for token in tokens]


def _segment_nearest(ax: float, ay: float, bx: float, by: float, px: float, py: float) -> tuple[float, float, float]:
    """线段上离 ``(px, py)`` 最近的点与距离（投影参数夹紧到 [0, 1]）。"""
    dx, dy = float(bx) - float(ax), float(by) - float(ay)
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return float(ax), float(ay), math.dist((float(ax), float(ay)), (px, py))
    t = ((px - float(ax)) * dx + (py - float(ay)) * dy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    nx, ny = float(ax) + t * dx, float(ay) + t * dy
    return nx, ny, math.dist((nx, ny), (px, py))


def _nearest_of_points(points: Any, px: float, py: float) -> tuple[float, float, float]:
    best: tuple[float, float, float] | None = None
    for item in points or []:
        x, y = float(item[0]), float(item[1])
        distance = math.dist((x, y), (px, py))
        if best is None or distance < best[2]:
            best = (x, y, distance)
    if best is None:
        raise _HM._fail("no_geometry", "entity has no sample points to measure against")
    return best


def _nearest_on_entity(entity: Any, px: float, py: float) -> tuple[float, float, float]:
    """实体上离光标最近的 ``(x_nm, y_nm, distance_nm)``（真几何，见模块 docstring 口径）。"""
    kind = _HM._kind_of(entity)
    if kind == "line":
        return _segment_nearest(_HM._num_field(entity, "x1"), _HM._num_field(entity, "y1"),
                                _HM._num_field(entity, "x2"), _HM._num_field(entity, "y2"), px, py)
    if kind == "circle":
        cx, cy = float(_HM._num_field(entity, "cx")), float(_HM._num_field(entity, "cy"))
        radius = float(_HM._num_field(entity, "r"))
        dx, dy = px - cx, py - cy
        norm = math.hypot(dx, dy)
        if radius <= 0.0:
            return cx, cy, norm
        if norm <= 0.0:
            return cx + radius, cy, radius
        return cx + dx / norm * radius, cy + dy / norm * radius, abs(norm - radius)
    if kind == "arc":
        cx, cy = float(_HM._num_field(entity, "cx")), float(_HM._num_field(entity, "cy"))
        radius = float(_HM._num_field(entity, "r"))
        start = float(_HM._num_field(entity, "start_deg"))
        sweep = float(_HM._arc_sweep(entity))
        dx, dy = px - cx, py - cy
        norm = math.hypot(dx, dy)
        angle = start if norm <= 0.0 else math.degrees(math.atan2(dy, dx))
        if 0.0 <= (angle - start) % 360.0 <= sweep:
            radians = math.radians(angle)
            return (cx + radius * math.cos(radians), cy + radius * math.sin(radians), abs(norm - radius))
        return _nearest_of_points(_HM._arc_points(cx, cy, radius, start, start + sweep), px, py)
    if kind == "polyline":
        points, _closed = _HM._entity_points(entity)
        if len(points) < 2:
            raise _HM._fail("no_geometry", "polyline has fewer than two vertices",
                            uuid=_HM._uuid_of(entity))
        best: tuple[float, float, float] | None = None
        for index in range(len(points) - 1):
            candidate = _segment_nearest(points[index][0], points[index][1],
                                         points[index + 1][0], points[index + 1][1], px, py)
            if best is None or candidate[2] < best[2]:
                best = candidate
        if best is None:  # pragma: no cover - 上面已挡住 < 2 点
            raise _HM._fail("no_geometry", "polyline has no measurable segment", uuid=_HM._uuid_of(entity))
        return best
    raise _HM._fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                    kind=kind, uuid=_HM._uuid_of(entity))


def _loop_area_of(entity: Any) -> float | None:
    """实体**自身成环**时的面积（nm²）；不成环 ⇒ ``None``（调用方写成 NA，不写 0）。"""
    kind = _HM._kind_of(entity)
    if kind == "circle":
        radius = float(_HM._num_field(entity, "r"))
        return math.pi * radius * radius if radius > 0.0 else None
    if kind == "polyline":
        points, closed = _HM._entity_points(entity)
        if closed and len(points) >= 3:
            return abs(_HM._shoelace(points))
    return None


def _hover_readout(sketch: Any, doc: Any, point_nm: Any, tol_nm: Any) -> list[str]:
    """悬停读数核心（只读，固定 10 个 token、顺序固定；两个悬停 op 共用）。"""
    x_nm, y_nm = _HM._point(point_nm, what="point")
    tolerance = _HM._gap_tolerance(sketch, doc, tol_nm)
    if tol_nm is None and tolerance == 0:
        tolerance = _DEFAULT_HOVER_TOL_NM
    px, py = float(x_nm), float(y_nm)
    best: tuple[float, Any, float, float] | None = None
    candidates = 0
    for entity in _HM._entities(sketch):
        if bool(_get(entity, "construction", False)):
            continue  # 构造几何不是可拾取实体
        nx, ny, distance = _nearest_on_entity(entity, px, py)
        if distance <= float(tolerance):
            candidates += 1
            if best is None or distance < best[0]:
                best = (distance, entity, nx, ny)
    if best is None:
        return ["hover_hit=0", "uuid=NA", "kind=NA", "layer=NA", "cursor_to_entity_nm=NA",
                "nearest_nm=NA", "entity_length_nm=NA", "entity_loop_area_nm2=NA",
                "candidate_count=%d" % candidates, "tol_nm=%d" % tolerance]
    distance, entity, nx, ny = best
    area = _loop_area_of(entity)
    return ["hover_hit=1",
            "uuid=%s" % _HM._uuid_of(entity),
            "kind=%s" % _HM._kind_of(entity),
            "layer=%s" % (str(_get(entity, "layer", "0") or "0")),
            "cursor_to_entity_nm=%d" % _round_nm(distance),
            "nearest_nm=%d,%d" % (_round_nm(nx), _round_nm(ny)),
            "entity_length_nm=%d" % _round_nm(_HM._curve_length(entity)),
            "entity_loop_area_nm2=%s" % ("NA" if area is None else "%d" % _round_nm(area)),
            "candidate_count=%d" % candidates,
            "tol_nm=%d" % tolerance]


# ======================================================================================
# 操作
# ======================================================================================


def measure_geom(sk_or_doc: Any, *, mode: Any = None, p1_nm: Any = None, p2_nm: Any = None,
                 ref_a: Any = None, ref_b: Any = None, boundary: Any = None,
                 tol_nm: Any = 0) -> list[str]:
    """通用测量的**统一入口**（只读）。`mode` ∈ ``auto`` / ``distance`` / ``area`` / ``perimeter`` / ``all``。

    ``auto``（缺省）= 给了任一距离操作数（`p1_nm` / `p2_nm` / `ref_a` / `ref_b`）就走 ``distance``，
    否则走 ``all``（区域测量：area + perimeter）。显式 ``distance`` 但操作数不足 ⇒
    ``CadError(reason="invalid_argument")``（不猜原点）。未知 ``mode`` 同样报 ``invalid_argument``。

    **返回**：``mode=<最终模式>``，随后按腿拼接、每个 token 都带腿前缀（见模块 docstring 第 1 条）：
    ``distance.`` 5 个 token（``distance_nm`` / ``dx_nm`` / ``dy_nm`` / ``nearest_a_nm`` /
    ``nearest_b_nm``，与 ``measure_distance`` 同源）、``area.`` 6 个 token（与 ``measure_area`` 同源）、
    ``perimeter.`` 3 个 token（与 ``measure_perimeter`` 同源）。``mode=all`` 时距离腿若没给操作数则
    5 个 token 全为 ``NA`` 并附 ``distance_given=0``；给了操作数则 ``distance_given=1``。
    """
    name = "auto" if mode is None else str(mode).strip().lower()
    if name not in _MODES:
        raise _HM._fail("invalid_argument", "mode must be one of auto/distance/area/perimeter/all",
                        mode=repr(mode))
    has_distance = any(value is not None for value in (p1_nm, p2_nm, ref_a, ref_b))
    if name == "auto":
        name = "distance" if has_distance else "all"
    tokens = ["mode=%s" % name]
    if name == "distance":
        if not has_distance:
            raise _HM._fail("invalid_argument",
                            "mode=distance needs two operands: p1_nm/p2_nm, p1_nm+ref_b, ref_a+p2_nm or ref_a+ref_b")
        return tokens + _prefixed("distance", _HM.measure_distance(
            sk_or_doc, p1_nm=p1_nm, p2_nm=p2_nm, ref_a=ref_a, ref_b=ref_b))
    if name == "area":
        return tokens + _prefixed("area", _HM.measure_area(sk_or_doc, boundary=boundary, tol_nm=tol_nm))
    if name == "perimeter":
        return tokens + _prefixed("perimeter", _HM.measure_perimeter(sk_or_doc, boundary=boundary))
    if has_distance:
        tokens = tokens + _prefixed("distance", _HM.measure_distance(
            sk_or_doc, p1_nm=p1_nm, p2_nm=p2_nm, ref_a=ref_a, ref_b=ref_b)) + ["distance_given=1"]
    else:
        tokens = tokens + _prefixed("distance", _DISTANCE_NA) + ["distance_given=0"]
    tokens = tokens + _prefixed("area", _HM.measure_area(sk_or_doc, boundary=boundary, tol_nm=tol_nm))
    return tokens + _prefixed("perimeter", _HM.measure_perimeter(sk_or_doc, boundary=boundary))


def measure_hover(sk_or_doc: Any, *, point: Any = None, tol_nm: Any = None) -> list[str]:
    """**悬停快速读数**（只读、零副作用）：光标点 → 孔径内候选实体 → 最近的实体 → 一次给全读数。

    `point=(x_nm, y_nm)` 必给（缺 ⇒ ``invalid_argument``）。`tol_nm=None` 时先后读族表 ``gap_nm``、
    再回落缺省拾取孔径（5 mm）；显式 ``0`` = 严格命中。

    **返回**（固定 10 个 token、顺序固定）：``hover_hit``（0/1）、``uuid``、``kind``、``layer``、
    ``cursor_to_entity_nm``（命中 = 整数纳米距离；未命中 = ``NA``）、``nearest_nm=x,y``（实体上离光标
    最近的点）、``entity_length_nm``（该实体弧长：line 端点距离 / circle 2πr / arc r·sweep /
    polyline 折线和）、``entity_loop_area_nm2``（实体自身成环才有值，否则 ``NA``）、``candidate_count``、
    ``tol_nm``（本次实际用的孔径）。未命中 ⇒ ``hover_hit=0``、``uuid=NA``、``candidate_count=0``
    —— "没命中"与"命中且距离为 0"不同形。
    """
    if point is None:
        raise _HM._fail("invalid_argument", "measure_hover needs point=(x_nm, y_nm)")
    sketch, doc = _HM._sketch_of(sk_or_doc)
    return _hover_readout(sketch, doc, point, tol_nm)


def measure_hover_track(sk_or_doc: Any, *, points: Any = None, tol_nm: Any = None) -> list[str]:
    """**悬停轨迹（实时）读数**（只读）：吃一串光标采样，逐样出读数，供实时消费方直接转发。

    `points` 必给且非空（缺 / 空 ⇒ ``invalid_argument`` / ``empty_selection``）。

    **返回**：``sample_count=<n>``，随后逐样 3 个 token（``hover<i>.hit`` / ``hover<i>.uuid`` /
    ``hover<i>.cursor_to_entity_nm``，``i`` 从 0 递增），最后 ``hit_count=<n>``、``miss_count=<n>``
    （两者相加 == ``sample_count``；真为零就是 0）。
    """
    sketch, doc = _HM._sketch_of(sk_or_doc)
    if isinstance(points, (str, bytes)) or not isinstance(points, _SequenceABC):
        raise _HM._fail("invalid_argument", "points must be a sequence of (x_nm, y_nm) pairs",
                        value=repr(points))
    samples = list(points)
    if not samples:
        raise _HM._fail("empty_selection", "points is empty: nothing to read (refusing to no-op silently)")
    tokens = ["sample_count=%d" % len(samples)]
    hits = 0
    for index, sample in enumerate(samples):
        readout = _token_map(_hover_readout(sketch, doc, sample, tol_nm))
        hit = readout.get("hover_hit") == "1"
        if hit:
            hits += 1
        tokens.append("hover%d.hit=%d" % (index, 1 if hit else 0))
        tokens.append("hover%d.uuid=%s" % (index, readout.get("uuid", "NA")))
        tokens.append("hover%d.cursor_to_entity_nm=%s" % (index, readout.get("cursor_to_entity_nm", "NA")))
    tokens.append("hit_count=%d" % hits)
    tokens.append("miss_count=%d" % (len(samples) - hits))
    return tokens


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "measure_geom": measure_geom,
    "measure": measure_geom,
    "measure_hover": measure_hover,
    "hover_measure": measure_hover,
    "measure_hover_track": measure_hover_track,
    "hover_track": measure_hover_track,
}


# ======================================================================================
# 自测（硬断言；`python3 <本文件>` 直接跑）
# ======================================================================================


def _raises_reason(call: Any, expected: str) -> bool:
    """真调用一次，核 ``CadError.reason`` 是否等于期望值（不吞异常、不看文案）。"""
    try:
        call()
    except CadError as exc:
        return exc.reason == expected
    except Exception:
        return False
    return False


def selftest() -> list[tuple[str, bool, str]]:
    """自测：真几何硬断言 + 只读证明 + 错误 reason 机读证明。返回 ``[(名, 是否通过, 说明), ...]``。"""
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    rectangle = _HM._selftest_rectangle()
    circle = _HM._selftest_circle()

    # 断言 1（硬）：统一入口 distance 腿 —— 3-4-5 直角三角形，精确 50000 / 30000 / 40000 nm
    distance_tokens = _token_map(measure_geom(rectangle, mode="distance",
                                              p1_nm=(0, 0), p2_nm=(30_000, 40_000)))
    record("distance_3_4_5_exact",
           distance_tokens.get("mode") == "distance"
           and distance_tokens.get("distance.distance_nm") == "50000"
           and distance_tokens.get("distance.dx_nm") == "30000"
           and distance_tokens.get("distance.dy_nm") == "40000"
           and distance_tokens.get("distance.nearest_a_nm") == "0,0"
           and distance_tokens.get("distance.nearest_b_nm") == "30000,40000",
           "tokens=%d" % len(distance_tokens))

    # 断言 2（硬）：auto 分发 + area 腿 —— 缺省圆半径 10 mm ⇒ 面积 = πr² 精确整数纳米²
    entities = list(_HM._entities(circle))
    circles = [entity for entity in entities if _HM._kind_of(entity) == "circle"]
    record("fixture_single_circle_r10mm",
           len(circles) == 1 and _HM._num_field(circles[0], "r") == 10_000_000,
           "circles=%d entities=%d" % (len(circles), len(entities)))
    if len(circles) != 1:
        return checks
    circle_entity = circles[0]
    circle_uuid = _HM._uuid_of(circle_entity)
    radius = _HM._num_field(circle_entity, "r")
    center_x = _HM._num_field(circle_entity, "cx")
    center_y = _HM._num_field(circle_entity, "cy")
    expected_area = _round_nm(math.pi * radius * radius)

    area_tokens = _token_map(measure_geom(circle, mode="auto"))
    record("auto_region_area_exact",
           area_tokens.get("mode") == "all"
           and area_tokens.get("area.area_nm2") == str(expected_area)
           and area_tokens.get("area.outer_area_nm2") == str(expected_area)
           and area_tokens.get("area.hole_area_nm2") == "0"
           and area_tokens.get("area.loop_count") == "1"
           and area_tokens.get("area.closed") == "1"
           and area_tokens.get("distance.distance_nm") == "NA"
           and area_tokens.get("distance_given") == "0"
           and area_tokens.get("perimeter.perimeter_nm") == str(_round_nm(2.0 * math.pi * radius)),
           "radius_nm=%d expected_area=%d" % (radius, expected_area))

    # 断言 3（硬）：悬停命中 —— 光标压在圆周上 ⇒ hit=1 / uuid 一致 / 距离 0 / 面积与 area 腿同源一致
    hover_on = _token_map(measure_hover(circle, point=(center_x + radius, center_y)))
    record("hover_hit_on_circle",
           hover_on.get("hover_hit") == "1" and hover_on.get("uuid") == circle_uuid
           and hover_on.get("kind") == "circle" and hover_on.get("cursor_to_entity_nm") == "0"
           and hover_on.get("nearest_nm") == "%d,%d" % (center_x + radius, center_y)
           and hover_on.get("entity_length_nm") == str(_round_nm(2.0 * math.pi * radius))
           and hover_on.get("entity_loop_area_nm2") == str(expected_area)
           and hover_on.get("candidate_count") == "1"
           and hover_on.get("tol_nm") == str(_DEFAULT_HOVER_TOL_NM),
           "uuid=%s" % hover_on.get("uuid"))

    # 断言 4（硬）：悬停 miss 与"真为零"不同形 —— 孔径外 ⇒ hit=0 / uuid=NA / candidate_count=0
    far_x = center_x + radius + 2 * _DEFAULT_HOVER_TOL_NM
    hover_off = _token_map(measure_hover(circle, point=(far_x, center_y)))
    record("hover_miss_outside_aperture",
           hover_off.get("hover_hit") == "0" and hover_off.get("uuid") == "NA"
           and hover_off.get("cursor_to_entity_nm") == "NA" and hover_off.get("nearest_nm") == "NA"
           and hover_off.get("candidate_count") == "0"
           and hover_off.get("tol_nm") == str(_DEFAULT_HOVER_TOL_NM),
           "point_x=%d" % far_x)

    # 断言 5（硬）：悬停轨迹逐样出数（命中 1 + 未命中 1）
    track = _token_map(measure_hover_track(circle, points=[(center_x + radius, center_y), (far_x, center_y)]))
    record("hover_track_two_samples",
           track.get("sample_count") == "2" and track.get("hit_count") == "1" and track.get("miss_count") == "1"
           and track.get("hover0.hit") == "1" and track.get("hover0.uuid") == circle_uuid
           and track.get("hover1.hit") == "0" and track.get("hover1.uuid") == "NA",
           "hits=%s misses=%s" % (track.get("hit_count"), track.get("miss_count")))

    # 断言 6（硬）：只读证明 —— 跑完所有读数后实体清单不变、族表没被建出来
    after = [_HM._uuid_of(entity) for entity in _HM._entities(circle)]
    record("read_only_no_mutation",
           after == [_HM._uuid_of(entity) for entity in entities]
           and _get(circle, "hatch_measure", None) is None,
           "entities=%d" % len(after))

    # 断言 7（硬）：错误一律带机读 reason（不静默、不吞）
    reason_results = [
        _raises_reason(lambda: measure_geom(circle, mode="bogus"), "invalid_argument"),
        _raises_reason(lambda: measure_geom(circle, mode="distance"), "invalid_argument"),
        _raises_reason(lambda: measure_hover(circle), "invalid_argument"),
        _raises_reason(lambda: measure_hover(circle, point=(0, 0), tol_nm=-1), "invalid_length"),
        _raises_reason(lambda: measure_hover_track(circle, points=[]), "empty_selection"),
    ]
    record("error_reasons_machine_readable", all(reason_results),
           "ok=%d/%d" % (sum(1 for item in reason_results if item), len(reason_results)))
    return checks


def _main() -> int:
    results = selftest()
    failed = [item for item in results if not item[1]]
    for name, ok, detail in results:
        print("CHECK %-34s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    if failed:
        print("FEATURE_FAIL family=%s op=measure_geom verdict=FAIL checks=%d failed=%d"
              % (FAMILY, len(results), len(failed)))
        return 1
    print("FEATURE_SUMMARY family=%s ops=%d checks=%d" % (FAMILY, len(OPERATIONS), len(results)))
    print("FEATURE_OK family=hatch_measure op=measure_geom verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "hover_measure": {"tier": "query", "readonly": True, "returns": "data"},
    "hover_track": {"tier": "query", "readonly": True, "returns": "data"},
    "measure": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_geom": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_hover": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_hover_track": {"tier": "query", "readonly": True, "returns": "data"},
}
