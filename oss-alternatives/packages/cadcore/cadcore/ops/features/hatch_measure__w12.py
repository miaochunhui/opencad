#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w12 —— 填充与查询族实现片 W12：通用测量统一入口。

一句话用途
----------
给「填充与查询」族补上**统一测量入口** ``measure_geom``：一个 op 覆盖 AutoCAD MEASUREGEOM 的三条腿
（距离/面积/周长），按 ``mode`` 分发；``mode=None`` 时按操作数**推断**（见下），返回值是同一套
``key=value`` 结果 token，下游只需一个解析器。

本条对应哪一条 must_have（编号口径写清，别靠猜）
----------------------------------------------
- 清单来源：``docs/research/cad-parity/spec/hatch_measure.json`` 的 ``items``（按清单顺序 0..19，
  即该族 must_have 全集）。**注意**：本席收到路径 ``docs/research/cad-parity/features/hatch_measure.json``
  **不存在**（features/ 下只有 ``detail/hatch_measure-part1..7.json`` 与 md），故 must_have 顺序以 spec 的
  ``items`` 为准、状态以 ``status/autocad-hatch-measure.json`` 的 ``items[].status`` 为准。
- 该族仍 **missing/partial** 的 must_have（按清单顺序编号 0..N-1，N=2）：
  - 0 = 第 12 条「渐变色填充」（partial：L1 无颜色字段，只有声明形态）；
  - 1 = 第 16 条「通用测量（含悬停快速读数）」（partial：缺统一入口 ``measure_geom`` 与悬停快速读数）。
- 本席负责**第 3 号**：条目不足 ⇒ 顺延取模 ``3 % 2 = 1`` ⇒ **第 1 号 = 第 16 条「通用测量」**。

本片做了什么（真几何，不是桩）
------------------------------
``measure_geom(sk_or_doc, *, mode=None, p1_nm=None, p2_nm=None, ref_a=None, ref_b=None,
boundary=None, tol_nm=0) -> list[str]``，**只读**（不建表、不写实体、不改一个纳米）：

- ``mode="distance"``：点-点 / 点-实体 / 实体-实体，取最近点对；token ``distance_nm`` / ``dx_nm`` /
  ``dy_nm`` / ``nearest_a_nm=x,y`` / ``nearest_b_nm=x,y``（整数纳米）。
- ``mode="area"``：闭合环成环（端点按纳米网格配对，``circle`` 与首末点重合的 ``polyline`` 自成一环）
  ⇒ 最大环为外环、被它包住的环算洞；token ``area_nm2``（净 = 外 − 洞）/ ``outer_area_nm2`` /
  ``hole_area_nm2`` / ``perimeter_nm`` / ``loop_count`` / ``closed``。**一个环都没有 ⇒ 前四个 NA、
  loop_count=0、closed=0**（"没算"与"算出来是 0"不同形）。
- ``mode="perimeter"``：逐实体解析式弧长求和（line 端点距离、circle 2πr、arc r·sweep、polyline 折线和）；
  token ``perimeter_nm`` / ``curve_count`` / ``closed``。
- 统一 token 契约：前两个 token 固定 ``mode=<area|perimeter|distance>`` 与 ``auto=<0|1>``，
  其后与该族分列 op（``measure_area``/``measure_perimeter``/``measure_distance``）**同一套词表**，
  下游解析器可复用。
- ``mode=None``（推断）规则写死：四条距离组合**给全** ⇒ distance；否则 ⇒ area；``auto=1`` 标记本次是推断。

几何口径（本文件自带，不复用别处的私有函数）
--------------------------------------------
本片**独立实现**面积/周长/距离的几何计算（shoelace + 解析式弧长 + 最近点对），不调用其它模块的私有
helper —— 这样"统一入口"不是薄壳：它真的算数。单位一律整数纳米（面积 nm²）；面积对 ``circle`` 用解析
πr²（不数离散多边形），对折线/直线用精确端点。环的点列离散化只用于**成环判据**与**包含判定**。

L1 装不下、如实留 partial 的部分（不许用假读数冒充）
--------------------------------------------------
**悬停 / 实时快速读数**（本条的第二个缺口）：它要的是"指针一动就出数"的**交互事件 + UI 消费方**，
L1 文档模型里既没有事件层也没有 app 侧渲染入口（旧审计实测：``oss-alternatives/apps`` 下
``grep -i 'hover|悬停'`` 只命中 CSS 伪类）⇒ 本片**显式 unsupported**，不写"假装实时"的轮询接口。
因此该 must_have 条目在本片交付后**仍是 partial**（统一入口这一半补齐，悬停那一半没有），
本席不把它报成 implemented —— 宁可红，不许假绿。

冻结接口
--------
``FAMILY = "hatch_measure"``；``OPERATIONS = {"measure_geom": measure_geom}``；
``op(sk_or_doc, **kwargs) -> list[str]``；错误一律 ``cadcore.errors.CadError(reason=...)``。
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Callable

from cadcore.errors import CadError

FAMILY = "hatch_measure"

_NM_PER_MM = 1_000_000
_CIRCLE_SEGMENTS = 64
_ARC_SEGMENTS_PER_QUARTER = 16
_MODES = {"area": "area", "perimeter": "perimeter", "distance": "distance",
          "dist": "distance", "perim": "perimeter", "auto": "auto"}

__all__ = ["FAMILY", "OPERATIONS", "measure_geom", "selftest"]


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


# ======================================================================================
# 目标解析与实体读取（只读；缺键不写回）
# ======================================================================================


def _sketch_of(target: Any) -> Any:
    """sketch 映射 / 带 ``.sketch`` 的文档对象 / 带 ``.sketches`` 的文档对象 ⇒ sketch 映射。"""
    if target is None:
        raise _fail("invalid_argument", "measure_geom needs a sketch mapping or a document")
    if hasattr(target, "get") and not isinstance(target, (str, bytes)):
        return target
    sketch = getattr(target, "sketch", None)
    if sketch is not None and hasattr(sketch, "get"):
        return sketch
    sketches = getattr(target, "sketches", None)
    if isinstance(sketches, (list, tuple)) and sketches and hasattr(sketches[0], "get"):
        return sketches[0]
    raise _fail("unsupported_target", "target must be a sketch mapping or a document with .sketch",
                target=type(target).__name__)


def _entities(sketch: Any) -> list[Any]:
    raw = sketch.get("entities")
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _is_mapping(value: Any) -> bool:
    return hasattr(value, "get") and not isinstance(value, (str, bytes))


def _uuid_of(entity: Any) -> str:
    value = entity.get("uuid") if _is_mapping(entity) else None
    return value if isinstance(value, str) else ""


def _kind_of(entity: Any) -> str:
    return str(entity.get("kind", "") or "").strip().lower() if _is_mapping(entity) else ""


def _geom_of(entity: Any) -> Any:
    geom = entity.get("geom") if _is_mapping(entity) else None
    if not _is_mapping(geom):
        raise _fail("geometry_invalid", "entity has no usable geom mapping", uuid=_uuid_of(entity))
    return geom


def _num(entity: Any, key: str, *, default: Any = None) -> float:
    geom = _geom_of(entity)
    value = geom.get(key, default)
    if value is None:
        raise _fail("geometry_invalid", "geom field is missing", uuid=_uuid_of(entity), field=key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("geometry_invalid", "geom field must be a number", uuid=_uuid_of(entity),
                    field=key, value=repr(value))
    return float(value)


def _tolerance(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "tol_nm must be an integer number of nanometres", tol_nm=repr(value))
    if value < 0:
        raise _fail("invalid_length", "tol_nm must not be negative", tol_nm=value)
    return value


def _point(value: Any, *, what: str) -> tuple[float, float]:
    if _is_mapping(value):
        if "x" not in value or "y" not in value:
            raise _fail("invalid_argument", "point mapping needs x and y", what=what, value=repr(value))
        return float(value["x"]), float(value["y"])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    if isinstance(value, str) and "," in value:
        left, _, right = value.partition(",")
        try:
            return float(left), float(right)
        except ValueError:
            raise _fail("invalid_argument", "point string must look like 'x,y'", what=what, value=value)
    raise _fail("invalid_argument", "point must be a mapping, a 2-sequence or 'x,y'",
                what=what, value=repr(value))


# ======================================================================================
# 实体 → 点列 / 弧长（解析式优先）
# ======================================================================================


def _circle_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    step = 2.0 * math.pi / _CIRCLE_SEGMENTS
    return [(cx + r * math.cos(i * step), cy + r * math.sin(i * step)) for i in range(_CIRCLE_SEGMENTS + 1)]


def _arc_points(cx: float, cy: float, r: float, start_deg: float, end_deg: float) -> list[tuple[float, float]]:
    start, end = float(start_deg), float(end_deg)
    while end <= start:
        end += 360.0
    sweep = end - start
    segments = max(4, int(_ARC_SEGMENTS_PER_QUARTER * sweep / 90.0) + 1)
    return [(cx + r * math.cos(math.radians(start + sweep * i / segments)),
             cy + r * math.sin(math.radians(start + sweep * i / segments))) for i in range(segments + 1)]


def _arc_sweep(entity: Any) -> float:
    start, end = _num(entity, "start_deg", default=0.0), _num(entity, "end_deg", default=0.0)
    while end <= start:
        end += 360.0
    return end - start


def _points_of(entity: Any) -> list[tuple[float, float]]:
    """实体 → 点列（``construction`` 实体不参与，返回空表）。"""
    if bool(entity.get("construction", False)):
        return []
    kind = _kind_of(entity)
    if kind == "line":
        return [(_num(entity, "x1"), _num(entity, "y1")), (_num(entity, "x2"), _num(entity, "y2"))]
    if kind == "circle":
        return _circle_points(_num(entity, "cx"), _num(entity, "cy"), _num(entity, "r"))
    if kind == "arc":
        return _arc_points(_num(entity, "cx"), _num(entity, "cy"), _num(entity, "r"),
                           _num(entity, "start_deg", default=0.0), _num(entity, "end_deg", default=0.0))
    if kind == "polyline":
        geom = _geom_of(entity)
        raw = geom.get("points")
        if raw is None:
            raw = geom.get("vertices")
        points: list[tuple[float, float]] = []
        for item in raw or []:
            points.append(_point(item, what="polyline point"))
        return points
    raise _fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                kind=kind, uuid=_uuid_of(entity))


def _curve_length(entity: Any) -> float:
    kind = _kind_of(entity)
    if kind == "line":
        return math.dist((_num(entity, "x1"), _num(entity, "y1")), (_num(entity, "x2"), _num(entity, "y2")))
    if kind == "circle":
        return 2.0 * math.pi * _num(entity, "r")
    if kind == "arc":
        return math.radians(_arc_sweep(entity)) * _num(entity, "r")
    if kind == "polyline":
        points = _points_of(entity)
        return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    raise _fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                kind=kind, uuid=_uuid_of(entity))


def _snap(value: float, tol: int) -> float:
    return float(int(round(value / tol)) * tol) if tol > 0 else float(int(round(value)))


def _same(a: tuple[float, float], b: tuple[float, float], tol: int) -> bool:
    return _snap(a[0], tol) == _snap(b[0], tol) and _snap(a[1], tol) == _snap(b[1], tol)


def _shoelace(points: list[tuple[float, float]]) -> float:
    pts = list(points)
    if len(pts) < 3:
        return 0.0
    if not _same(pts[0], pts[-1], 0):
        pts.append(pts[0])
    total = 0.0
    for index in range(len(pts) - 1):
        x1, y1 = pts[index]
        x2, y2 = pts[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """射线法（严格内部；点在边上/顶点上不算内部 —— 外环自身的顶点不会被误判成洞）。"""
    px, py = point
    pts = list(polygon)
    if len(pts) >= 3 and not _same(pts[0], pts[-1], 0):
        pts.append(pts[0])
    inside = False
    for index in range(len(pts) - 1):
        x1, y1 = pts[index]
        x2, y2 = pts[index + 1]
        if (y1 > py) != (y2 > py):
            crossing = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if crossing > px:
                inside = not inside
    return inside


# ======================================================================================
# 成环（端点按纳米网格配对；circle / 首末点重合 polyline 自成一环）
# ======================================================================================


def _loops(entities: list[Any], tol: int) -> list[dict[str, Any]]:
    loops: list[dict[str, Any]] = []
    open_items: list[Any] = []
    for entity in entities:
        if bool(entity.get("construction", False)):
            continue
        kind = _kind_of(entity)
        if kind == "circle":
            loops.append({"members": [_uuid_of(entity)], "entities": [entity], "circle": True})
            continue
        points = _points_of(entity)
        if not points:
            continue
        if kind == "polyline" and len(points) >= 4 and _same(points[0], points[-1], tol):
            loops.append({"members": [_uuid_of(entity)], "entities": [entity], "circle": False})
            continue
        open_items.append(entity)

    remaining = list(open_items)
    while remaining:
        chain = [remaining.pop(0)]
        points = list(_points_of(chain[0]))
        extended = True
        while extended and remaining:
            extended = False
            if points and _same(points[0], points[-1], tol):
                break
            for index, candidate in enumerate(remaining):
                candidate_points = _points_of(candidate)
                if len(candidate_points) < 2:
                    continue
                head, tail = points[0], points[-1]
                c_head, c_tail = candidate_points[0], candidate_points[-1]
                if _same(tail, c_head, tol):
                    points, inserted = points + candidate_points[1:], True
                elif _same(tail, c_tail, tol):
                    points, inserted = points + list(reversed(candidate_points))[1:], True
                elif _same(head, c_tail, tol):
                    points, inserted = candidate_points[:-1] + points, True
                elif _same(head, c_head, tol):
                    points, inserted = list(reversed(candidate_points))[:-1] + points, True
                else:
                    inserted = False
                if inserted:
                    chain.append(candidate)
                    remaining.pop(index)
                    extended = True
                    break
        if len(points) >= 4 and _same(points[0], points[-1], tol):
            loops.append({"members": [_uuid_of(item) for item in chain], "entities": chain, "circle": False})
    return loops


def _loop_points(loop: dict[str, Any]) -> list[tuple[float, float]]:
    if loop["circle"]:
        entity = loop["entities"][0]
        return _circle_points(_num(entity, "cx"), _num(entity, "cy"), _num(entity, "r"))
    points: list[tuple[float, float]] = []
    for entity in loop["entities"]:
        chunk = _points_of(entity)
        points = chunk if not points else points + chunk[1:]
    return points


def _loop_area(loop: dict[str, Any]) -> float:
    if loop["circle"]:
        radius = _num(loop["entities"][0], "r")
        return math.pi * radius * radius
    return abs(_shoelace(_loop_points(loop)))


def _loop_perimeter(loop: dict[str, Any]) -> float:
    return sum(_curve_length(entity) for entity in loop["entities"])


def _round_nm(value: float) -> int:
    return int(round(value))


# ======================================================================================
# 操作数解析
# ======================================================================================


def _subset(sketch: Any, spec: Any) -> list[Any]:
    entities = _entities(sketch)
    if spec is None:
        return entities
    wanted = [spec] if isinstance(spec, str) else list(spec) if isinstance(spec, (list, tuple)) else None
    if wanted is None:
        raise _fail("invalid_argument", "boundary must be a uuid, a uuid sequence or None", boundary=repr(spec))
    by_uuid = {_uuid_of(entity): entity for entity in entities}
    picked: list[Any] = []
    for reference in wanted:
        if not isinstance(reference, str) or reference not in by_uuid:
            raise _fail("unknown_reference", "no entity with this uuid in the target", reference=repr(reference))
        picked.append(by_uuid[reference])
    return picked


def _find(sketch: Any, reference: Any) -> Any:
    if isinstance(reference, str):
        for entity in _entities(sketch):
            if _uuid_of(entity) == reference:
                return entity
        raise _fail("unknown_reference", "no entity with this uuid in the target", reference=reference)
    raise _fail("invalid_argument", "entity reference must be a uuid string", reference=repr(reference))


def _nearest_pair(points_a: list[tuple[float, float]],
                  points_b: list[tuple[float, float]]) -> tuple[float, tuple[float, float], tuple[float, float]]:
    best: Any = None
    for point_a in points_a:
        for point_b in points_b:
            distance = math.dist(point_a, point_b)
            if best is None or distance < best[0]:
                best = (distance, point_a, point_b)
    if best is None:
        raise _fail("no_geometry", "one of the operands has no measurable geometry")
    return best


# ======================================================================================
# 统一入口：measure_geom
# ======================================================================================


def measure_geom(sk_or_doc: Any, *, mode: Any = None, p1_nm: Any = None, p2_nm: Any = None,
                 ref_a: Any = None, ref_b: Any = None, boundary: Any = None,
                 tol_nm: Any = 0) -> list[str]:
    """通用测量统一入口（只读）：一个 op 覆盖距离 / 面积 / 周长三条腿。

    ``mode``：``"distance"`` / ``"area"`` / ``"perimeter"``（``"dist"`` / ``"perim"`` 是别名；
    大小写不敏感）。``mode=None`` 或 ``"auto"`` ⇒ 推断：四个距离操作数组合**给全**
    （``p1_nm+p2_nm`` / ``p1_nm+ref_b`` / ``ref_a+p2_nm`` / ``ref_a+ref_b``）判 distance，
    否则判 area；此时 ``auto=1``。

    **返回**：前两个 token 固定 ``mode=<...>``、``auto=<0|1>``，其后与该族分列 op 同词表：
    distance ⇒ ``distance_nm`` / ``dx_nm`` / ``dy_nm`` / ``nearest_a_nm=x,y`` / ``nearest_b_nm=x,y``；
    area ⇒ ``area_nm2``（净=外−洞）/ ``outer_area_nm2`` / ``hole_area_nm2`` / ``perimeter_nm`` /
    ``loop_count`` / ``closed``；perimeter ⇒ ``perimeter_nm`` / ``curve_count`` / ``closed``。
    无闭合环 ⇒ 面积/周长的读数位 ``NA``、``loop_count=0``、``closed=0``（与"0 是真为零"不同形）；
    空图/无实体 ⇒ ``perimeter_nm=NA``、``curve_count=0``。

    错误：未知 ``mode`` ⇒ ``unsupported_mode``；``mode="distance"`` 缺一个操作数 ⇒ ``invalid_argument``
    （不猜"到原点"）；未知 uuid ⇒ ``unknown_reference``；``tol_nm`` 非整数/负数 ⇒ ``invalid_length``。

    **不支持（本条 must_have 的另一半）**：悬停 / 实时快速读数需要交互事件与 apps 侧消费方，
    L1 文档模型装不下 ⇒ 本 op 不做轮询式"假实时"，该能力项保持 partial。
    """
    requested = _MODES.get(str(mode if mode is not None else "auto").strip().lower())
    if requested is None:
        raise _fail("unsupported_mode", "mode must be distance/area/perimeter (or None for auto)", mode=repr(mode))
    tolerance = _tolerance(tol_nm)
    sketch = _sketch_of(sk_or_doc)

    pair_complete = ((p1_nm is not None and p2_nm is not None)
                     or (p1_nm is not None and ref_b is not None)
                     or (ref_a is not None and p2_nm is not None)
                     or (ref_a is not None and ref_b is not None))
    auto = 1 if requested == "auto" else 0
    resolved = ("distance" if pair_complete else "area") if requested == "auto" else requested

    if resolved == "distance":
        first_point = None if p1_nm is None else _point(p1_nm, what="p1_nm")
        second_point = None if p2_nm is None else _point(p2_nm, what="p2_nm")
        first_entity = None if ref_a is None else _find(sketch, ref_a)
        second_entity = None if ref_b is None else _find(sketch, ref_b)
        if first_point is not None and second_point is not None:
            candidates_a, candidates_b = [first_point], [second_point]
        elif first_point is not None and second_entity is not None:
            candidates_a, candidates_b = [first_point], _points_of(second_entity)
        elif first_entity is not None and second_point is not None:
            candidates_a, candidates_b = _points_of(first_entity), [second_point]
        elif first_entity is not None and second_entity is not None:
            candidates_a, candidates_b = _points_of(first_entity), _points_of(second_entity)
        else:
            raise _fail("invalid_argument",
                        "measure_geom(mode=distance) needs two operands: p1_nm/p2_nm, "
                        "p1_nm+ref_b, ref_a+p2_nm or ref_a+ref_b",
                        p1_nm=p1_nm is not None, p2_nm=p2_nm is not None,
                        ref_a=ref_a is not None, ref_b=ref_b is not None)
        if not candidates_a or not candidates_b:
            raise _fail("no_geometry", "one of the operands has no measurable geometry",
                        ref_a=repr(ref_a), ref_b=repr(ref_b))
        distance, point_a, point_b = _nearest_pair(candidates_a, candidates_b)
        return ["mode=distance", "auto=%d" % auto,
                "distance_nm=%d" % _round_nm(distance),
                "dx_nm=%d" % _round_nm(point_b[0] - point_a[0]),
                "dy_nm=%d" % _round_nm(point_b[1] - point_a[1]),
                "nearest_a_nm=%d,%d" % (_round_nm(point_a[0]), _round_nm(point_a[1])),
                "nearest_b_nm=%d,%d" % (_round_nm(point_b[0]), _round_nm(point_b[1]))]

    subset = _subset(sketch, boundary)
    if resolved == "perimeter":
        curves = [entity for entity in subset if _points_of(entity)]
        if not curves:
            return ["mode=perimeter", "auto=%d" % auto, "perimeter_nm=NA", "curve_count=0", "closed=0"]
        total = sum(_curve_length(entity) for entity in curves)
        loops = _loops(curves, tolerance)
        covered = {member for loop in loops for member in loop["members"]}
        closed = 1 if covered == {_uuid_of(entity) for entity in curves} else 0
        return ["mode=perimeter", "auto=%d" % auto, "perimeter_nm=%d" % _round_nm(total),
                "curve_count=%d" % len(curves), "closed=%d" % closed]

    loops = _loops(subset, tolerance)
    if not loops:
        return ["mode=area", "auto=%d" % auto, "area_nm2=NA", "outer_area_nm2=NA", "hole_area_nm2=NA",
                "perimeter_nm=NA", "loop_count=0", "closed=0"]
    areas = [_loop_area(loop) for loop in loops]
    outer_index = max(range(len(loops)), key=lambda index: areas[index])
    outer_points = _loop_points(loops[outer_index])
    hole_area = 0.0
    hole_perimeter = 0.0
    hole_count = 0
    for index, loop in enumerate(loops):
        if index == outer_index:
            continue
        first_point = _loop_points(loop)[0]
        if areas[index] < areas[outer_index] and _inside(first_point, outer_points):
            hole_area += areas[index]
            hole_perimeter += _loop_perimeter(loop)
            hole_count += 1
    net_area = areas[outer_index] - hole_area
    return ["mode=area", "auto=%d" % auto,
            "area_nm2=%d" % _round_nm(net_area),
            "outer_area_nm2=%d" % _round_nm(areas[outer_index]),
            "hole_area_nm2=%d" % _round_nm(hole_area),
            "perimeter_nm=%d" % _round_nm(_loop_perimeter(loops[outer_index]) + hole_perimeter),
            "loop_count=%d" % (1 + hole_count), "closed=1"]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "measure_geom": measure_geom,
}


# ======================================================================================
# 自检（硬断言；`python3 <本文件>` 直跑）
# ======================================================================================


def _rect(x0: int, y0: int, x1: int, y1: int, prefix: str) -> list[Any]:
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    entities: list[Any] = []
    for index in range(4):
        ax, ay = corners[index]
        bx, by = corners[(index + 1) % 4]
        entities.append({"uuid": "%s-%d" % (prefix, index), "kind": "line", "layer": "0",
                         "construction": False, "geom": {"x1": ax, "y1": ay, "x2": bx, "y2": by}})
    return entities


def _value(tokens: list[str], key: str) -> str:
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return "<missing>"


def selftest() -> list[tuple[str, bool, str]]:
    """≥2 条硬断言：真几何数值 + 只读无副作用 + 错误口径。"""
    mm = _NM_PER_MM
    outer = _rect(0, 0, 100 * mm, 50 * mm, "outer")
    results: list[tuple[str, bool, str]] = []

    # 断言 1：100mm × 50mm 矩形 ⇒ 面积 5e15 nm²、周长 3e8 nm、1 个闭合环
    tokens = measure_geom({"entities": outer}, mode="area")
    ok = (_value(tokens, "area_nm2") == "5000000000000000"
          and _value(tokens, "perimeter_nm") == "300000000"
          and _value(tokens, "loop_count") == "1" and _value(tokens, "closed") == "1"
          and tokens[0] == "mode=area" and _value(tokens, "auto") == "0")
    results.append(("rect_100x50_area_perimeter", bool(ok), "area=%s perimeter=%s loops=%s"
                    % (_value(tokens, "area_nm2"), _value(tokens, "perimeter_nm"), _value(tokens, "loop_count"))))

    # 断言 2：带洞 ⇒ 净面积 = 外 − 洞（真加减），洞周长计入总周长
    hole = _rect(10 * mm, 10 * mm, 30 * mm, 20 * mm, "hole")
    tokens = measure_geom({"entities": outer + hole}, mode="area")
    ok = (_value(tokens, "area_nm2") == "4800000000000000"
          and _value(tokens, "outer_area_nm2") == "5000000000000000"
          and _value(tokens, "hole_area_nm2") == "200000000000000"
          and _value(tokens, "perimeter_nm") == "360000000"
          and _value(tokens, "loop_count") == "2")
    results.append(("rect_with_hole_net_area", bool(ok), "net=%s outer=%s hole=%s perimeter=%s loops=%s"
                    % (_value(tokens, "area_nm2"), _value(tokens, "outer_area_nm2"),
                       _value(tokens, "hole_area_nm2"), _value(tokens, "perimeter_nm"),
                       _value(tokens, "loop_count"))))

    # 断言 3：距离 3-4-5；auto 推断（给全操作数 ⇒ distance，auto=1）
    sketch = {"entities": outer}
    tokens = measure_geom(sketch, p1_nm=(0, 0), p2_nm=(30 * mm, 40 * mm))
    ok = (tokens[0] == "mode=distance" and _value(tokens, "auto") == "1"
          and _value(tokens, "distance_nm") == "50000000"
          and _value(tokens, "dx_nm") == "30000000" and _value(tokens, "dy_nm") == "40000000")
    results.append(("distance_345_and_auto_infer", bool(ok), "mode=%s auto=%s distance=%s dx=%s dy=%s"
                    % (tokens[0], _value(tokens, "auto"), _value(tokens, "distance_nm"),
                       _value(tokens, "dx_nm"), _value(tokens, "dy_nm"))))

    # 断言 4：错误口径 + 空图三态 + 只读（文档逐字节未变）
    reasons: list[str] = []
    try:
        measure_geom(sketch, mode="bogus")
    except CadError as exc:
        reasons.append(exc.reason)
    try:
        measure_geom(sketch, mode="distance", p1_nm=(0, 0))
    except CadError as exc:
        reasons.append(exc.reason)
    try:
        measure_geom(sketch, mode="area", tol_nm=-1)
    except CadError as exc:
        reasons.append(exc.reason)
    empty = measure_geom({"entities": []}, mode="area")
    empty_perimeter = measure_geom({"entities": []}, mode="perimeter")
    before = json.dumps(sketch, sort_keys=True, default=str)
    measure_geom(sketch, mode="area")
    measure_geom(sketch, mode="perimeter")
    measure_geom(sketch, mode="distance", ref_a="outer-0", ref_b="outer-2")
    after = json.dumps(sketch, sort_keys=True, default=str)
    ok = (reasons == ["unsupported_mode", "invalid_argument", "invalid_length"]
          and _value(empty, "area_nm2") == "NA" and _value(empty, "loop_count") == "0"
          and _value(empty, "closed") == "0"
          and _value(empty_perimeter, "perimeter_nm") == "NA"
          and before == after)
    results.append(("errors_NA_and_readonly", bool(ok), "reasons=%s empty_area=%s empty_perimeter=%s readonly=%d"
                    % (",".join(reasons), _value(empty, "area_nm2"),
                       _value(empty_perimeter, "perimeter_nm"), 1 if before == after else 0)))
    return results


def _main() -> int:
    results = selftest()
    assertions = 0
    for name, ok, detail in results:
        assertions += 1
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    failed = [name for name, ok, _detail in results if not ok]
    if failed or assertions < 2:
        print("FEATURE_FAIL family=%s op=measure_geom verdict=FAIL assertions=%d failed=%s"
              % (FAMILY, assertions, ",".join(failed) or "too_few_assertions"))
        return 1
    print("FEATURE_OK family=%s op=measure_geom verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "measure_geom": {"tier": "query", "readonly": True, "returns": "data"},
}
