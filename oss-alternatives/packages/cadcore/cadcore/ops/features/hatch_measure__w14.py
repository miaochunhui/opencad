#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w14 —— 通用测量统一入口（MEASUREGEOM）+ 悬停快速读数（L1 侧）。

一句话用途
----------
把本族三条**分列**测量 op（``measure_distance`` / ``measure_area`` / ``measure_perimeter``）收进
**一个**入口 ``measure_geom``（对齐 AutoCAD MEASUREGEOM / MEA），并补上"悬停即读数"在 L1 文档模型上
**可落的那一半**：``measure_hover`` 用真实几何算出"光标最近的那个捕捉点"及其读数
（捕捉类型 / 最近点 / 距离 / 方位 / 被捕捉实体自身的长度），一次调用给齐，供上层状态行直接消费。

本片对应的缺口（来自 status/autocad-hatch-measure.json 的 must_have #15"通用测量（含悬停快速读数）"，
该条判 ``partial``）
----------------------------------------------------------------------------------------------
- 缺①：**统一入口名** ``measure_geom`` —— OPERATIONS 表里只有三个分列 op，没有 MEASUREGEOM 那一个键。
- 缺②：**悬停/实时快速读数** —— 在 ``oss-alternatives/apps`` 下 grep ``hover`` 只命中 CSS 伪类，
  没有测量读数入口；内核侧也没有"光标 → 最近捕捉点 → 读数"这条几何通路。
本片补齐的是②的**内核半条**（真几何、真数字），并把①落成表内唯一键。

诚实边界（写下来，防止下游把"内核半条"当成"产品全条"）
------------------------------------------------------
1. **本片不做 GUI 绑定**：cadcore 是内核，悬停的鼠标事件、状态行渲染都在 apps 层，不在本文件写权内。
   ⇒ 上面那条 ``partial`` 在**产品层仍然只满足一半**：内核读数通路已真实现，UI 消费方**未声明已实现**。
2. 本文件**全是只读 op**：不建表、不写实体、不改系统变量，返回值是 ``"key=value"`` 结果 token
   （与本族测量口径一致）。要"看得见的几何后果"请用写 op（``hatch_create`` / ``boundary_create``）。
3. 捕捉候选**只报真几何算出来的点**：线段端点、线段中点、圆/弧圆心（几何里有就报，没有就跳过）、
   以及曲线上的最近点。"捕捉类型"由候选点与光标的真实距离决定，**不猜、不外推**。

口径（与全仓一致，别自己发明）
------------------------------
- ``0 = 真为零``（``candidate_count=0`` 就是孔径内一个实体都没有）；``NA = 不适用``（没有捕捉点时
  ``snap_kind=NA`` / ``distance_nm=NA``）；``-1 = 未采``（本片不采的量，例如本层没有体积/三维量）。
- 长度一律**整数纳米**；面积 nm²；角度用度（``%.3f``）。
- 错误一律抛 ``cadcore.errors.CadError(reason=...)``：未知 mode ⇒ ``unsupported_mode``；
  ``refs=[]`` ⇒ ``empty_selection``（复用本族的 ``_subset``，不静默返回空结果）。

复用而非重写（本族同一把尺子）
------------------------------
入参与几何全部复用 ``cadcore.ops.hatch_measure`` 的既有实现：``_sketch_of`` / ``_subset`` /
``_entity_points`` / ``_curve_length`` / ``_polyline_vertices`` / ``_point`` / ``_nm`` / ``_fail``，
面积/周长/距离直接调用它的 ``measure_area`` / ``measure_perimeter`` / ``measure_distance`` /
``boundary_from_point`` —— 本文件**不抄第二份几何或校验器**，统一入口与分列 op 必须给出同源数字。

自测（``python3 cadcore/ops/features/hatch_measure__w14.py``）
-------------------------------------------------------------
硬断言（不是"跑通即绿"）：100×50 mm 矩形 ``area_nm2=5000000000000000``、``perimeter_nm=300000000``；
统一入口 token 与分列 op **逐 token 相等**；悬停到线段真实中点 ⇒ ``snap_kind=midpoint`` 且距离 ≤1 nm；
孔径 1 nm 且光标远在 1e12 nm 外 ⇒ ``candidate_count=0`` 真为零；``refs=[]`` ⇒ CadError(empty_selection)；
未知 mode ⇒ CadError(unsupported_mode)；圆半径 == 10000000 nm（周长按族内同一把尺子，容差 0.5%）。
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Callable

if __package__:
    from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）
    from cadcore.ops.hatch_measure import (
        _curve_length,
        _entities,
        _entity_points,
        _fail,
        _geom,
        _kind_of,
        _nm,
        _point,
        _polyline_vertices,
        _round_nm,
        _selftest_circle,
        _selftest_rectangle,
        _sketch_of,
        _subset,
        _uuid_of,
        boundary_from_point,
        measure_area,
        measure_distance,
        measure_perimeter,
    )
else:  # pragma: no cover - 直跑本文件时的自举（本包是命名空间包，没有 __init__.py）
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from cadcore.errors import CadError
    from cadcore.ops.hatch_measure import (
        _curve_length,
        _entities,
        _entity_points,
        _fail,
        _geom,
        _kind_of,
        _nm,
        _point,
        _polyline_vertices,
        _round_nm,
        _selftest_circle,
        _selftest_rectangle,
        _sketch_of,
        _subset,
        _uuid_of,
        boundary_from_point,
        measure_area,
        measure_distance,
        measure_perimeter,
    )

__all__ = ["FAMILY", "OPERATIONS", "measure_geom", "measure_hover", "measure_entity", "measure_point", "selftest"]

FAMILY = "hatch_measure"

#: 本片的主 op（判定行里的 op= 用它）；OPERATIONS 里主名 + 别名指向同一函数。
PRIMARY_OP = "measure_geom"

#: 捕捉类型的优先级：距离相同时端点 > 中点 > 圆心 > 曲线最近点（与 CAD 的捕捉直觉一致）。
_SNAP_RANK = {"endpoint": 0, "midpoint": 1, "center": 2, "nearest": 3}

_MODES = ("distance", "area", "perimeter", "quick", "entity", "point")


# ======================================================================================
# 小工具（token 解析、点归一化、捕捉候选）
# ======================================================================================


def _token(tokens: Any, key: str) -> str | None:
    """从结果 token 列表里取 `key=value` 的 value；没有该键 ⇒ None（"没这个键"与"值为空"不同形）。"""
    prefix = str(key) + "="
    for token in tokens or ():
        text = str(token)
        if text.startswith(prefix):
            return text[len(prefix):]
    return None


def _token_int(tokens: Any, key: str) -> int | None:
    """取整数 token；缺键或 NA ⇒ None（**不把 NA 当 0**）。"""
    raw = _token(tokens, key)
    if raw is None or raw == "NA":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _pair(value: Any) -> tuple[float, float] | None:
    """(x, y) 归一化：接受 list/tuple（取前两位）与 {'x','y'} 映射；其余 ⇒ None（不猜 0）。"""
    if isinstance(value, dict):
        if "x" in value and "y" in value:
            return (float(value["x"]), float(value["y"]))
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        first = value[0]
        if isinstance(first, (list, tuple, dict)):
            return None
        return (float(value[0]), float(value[1]))
    return None


def _points_of(entity: Any) -> tuple[list[tuple[float, float]], bool]:
    """实体的离散点列 + 闭合标志；几何不可用（无 geom / 未知种类）⇒ ([], False)，不抛、不猜。"""
    try:
        raw, closed = _entity_points(entity)
    except CadError:
        return [], False
    points: list[tuple[float, float]] = []
    for item in raw or ():
        pair = _pair(item)
        if pair is not None:
            points.append(pair)
    return points, bool(closed)


def _center_of(entity: Any) -> tuple[float, float] | None:
    """圆心/弧心：几何里真有该字段才返回，没有 ⇒ None（**不拟合、不外推**）。"""
    try:
        geom = _geom(entity)
    except CadError:
        return None
    for key_x, key_y in (("cx", "cy"), ("center_x", "center_y"), ("x", "y")):
        if key_x in geom and key_y in geom:
            try:
                return (float(geom[key_x]), float(geom[key_y]))
            except (TypeError, ValueError):
                return None
    return None


def _snap_candidates(entity: Any, points: list[tuple[float, float]], closed: bool) -> list[tuple[float, float, str]]:
    """真实捕捉候选：开放曲线的两端端点、折线各段中点、圆心/弧心。"""
    candidates: list[tuple[float, float, str]] = []
    kind = _kind_of(entity)
    if points and not closed:
        candidates.append((points[0][0], points[0][1], "endpoint"))
        candidates.append((points[-1][0], points[-1][1], "endpoint"))
    if kind == "polyline":
        vertices: list[tuple[float, float]] = []
        try:
            for item in list(_polyline_vertices(_geom(entity))):
                pair = _pair(item)
                if pair is not None:
                    vertices.append(pair)
        except CadError:
            vertices = []
        for vertex in vertices:
            candidates.append((vertex[0], vertex[1], "endpoint"))
        segments = list(zip(vertices, vertices[1:]))
        if closed and len(vertices) > 2:
            segments.append((vertices[-1], vertices[0]))
        for start, end in segments:
            candidates.append(((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0, "midpoint"))
    elif kind == "line" and len(points) >= 2:
        first, last = points[0], points[-1]
        candidates.append(((first[0] + last[0]) / 2.0, (first[1] + last[1]) / 2.0, "midpoint"))
    center = _center_of(entity)
    if center is not None:
        candidates.append((center[0], center[1], "center"))
    return candidates


def _nearest_on(entity: Any, cursor: tuple[int, int]) -> tuple[float, float, float, str] | None:
    """光标 → 该实体的最近捕捉点：返回 (距离, x, y, 捕捉类型)；无可用几何 ⇒ None。"""
    points, closed = _points_of(entity)
    if not points:
        return None
    best: tuple[float, float, float, str] | None = None
    for x, y, tag in _snap_candidates(entity, points, closed):
        distance = math.hypot(x - cursor[0], y - cursor[1])
        if best is None or (distance, _SNAP_RANK[tag]) < (best[0], _SNAP_RANK[best[3]]):
            best = (distance, x, y, tag)
    for x, y in points:
        distance = math.hypot(x - cursor[0], y - cursor[1])
        if best is None or distance < best[0] - 1e-9:
            best = (distance, x, y, "nearest")
    return best


# ======================================================================================
# 悬停快速读数（内核半条：光标 → 最近捕捉点 → 读数）
# ======================================================================================


def measure_hover(sk_or_doc: Any, *, cursor_nm: Any, refs: Any = None, aperture_nm: Any = 0,
                  max_results: Any = 1) -> list[str]:
    """悬停快速读数：给定光标位置，返回**最近捕捉点**及其读数（只读，不落任何图元）。

    参数：``cursor_nm=(x,y)`` 光标（整数纳米）；``refs`` 候选实体（uuid / uuid 序列 / None=全图；
    给空序列 ⇒ ``CadError(reason="empty_selection")``）；``aperture_nm`` 捕捉孔径（0=不限，
    >0 时只报距离 ≤ 孔径的实体，"孔径内一个都没有" ⇒ ``candidate_count=0`` **真为零**）；
    ``max_results`` 取前几名（>1 时追加 ``alt_<i>_*`` 行）。

    **返回 token（顺序固定）**：``cursor_nm=x,y``、``candidate_count``、``snap_nm=x,y|NA``、
    ``snap_kind=endpoint|midpoint|center|nearest|NA``、``distance_nm|NA``、``angle_deg|NA``
    （光标→捕捉点的方位角，度）、``entity=<uuid>|NA``、``curve_length_nm|NA``（被捕捉实体自身长度）、
    ``readout_count``、``hover_readout=1``。

    **口径**：``candidate_count=0`` 与 ``snap_kind=NA`` 只在"孔径内真没有候选"时出现，
    与"没采/不适用"不同形；距离为整数纳米，取整与族内同一把尺子（``_round_nm``）。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    cursor = _point(cursor_nm, what="cursor_nm")
    entities = _subset(sketch, refs)
    aperture = _nm(aperture_nm, what="aperture_nm")
    limit = _nm(max_results, what="max_results", positive=True)

    rows: list[tuple[float, float, float, str, Any]] = []
    for entity in entities:
        nearest = _nearest_on(entity, cursor)
        if nearest is None:
            continue
        distance, x, y, tag = nearest
        if aperture > 0 and distance > aperture:
            continue
        rows.append((distance, x, y, tag, entity))
    rows.sort(key=lambda row: (row[0], _uuid_of(row[4])))

    taken = rows[: int(limit)]
    tokens = [
        "cursor_nm=%d,%d" % (cursor[0], cursor[1]),
        "candidate_count=%d" % len(rows),
        "readout_count=%d" % len(taken),
    ]
    if not taken:
        tokens.extend(["snap_nm=NA", "snap_kind=NA", "distance_nm=NA", "angle_deg=NA",
                       "entity=NA", "curve_length_nm=NA"])
    for index, (distance, x, y, tag, entity) in enumerate(taken):
        prefix = "snap" if index == 0 else "alt_%d" % index
        points, _closed = _points_of(entity)
        length = _round_nm(_curve_length(entity)) if points else None
        angle = math.degrees(math.atan2(y - cursor[1], x - cursor[0]))
        tokens.extend([
            "%s_nm=%d,%d" % (prefix, _round_nm(x), _round_nm(y)),
            "%s_kind=%s" % (prefix, tag),
            "%s_distance_nm=%d" % (prefix, _round_nm(distance)),
            "%s_angle_deg=%.3f" % (prefix, angle),
            "%s_entity=%s" % (prefix, _uuid_of(entity)),
        ])
        if index == 0:
            tokens.append("curve_length_nm=%s" % ("NA" if length is None else "%d" % length))
    tokens.append("hover_readout=1")
    return tokens


# ======================================================================================
# 逐实体读数 / 内部点读数
# ======================================================================================


def measure_entity(sk_or_doc: Any, *, ref: Any = None, refs: Any = None) -> list[str]:
    """逐实体读数：每条实体给 ``entity`` / ``kind`` / ``curve_length_nm`` / ``closed`` /
    ``radius_nm`` / ``area_nm2`` / ``angle_deg``（只读）。

    - ``radius_nm``：几何里真有 r/radius 字段才报，否则 ``NA``（不拟合）。
    - ``area_nm2``：实体自己首尾闭合才用族内同一把面积尺子算；不闭合 ⇒ ``NA``（**不是 0**）。
    - ``angle_deg``：只对 ``line`` 报端点方位角，其余种类 ⇒ ``NA``。
    - 缺 ``ref``/``refs`` ⇒ ``CadError(reason="invalid_argument")``；``refs=[]`` ⇒ ``empty_selection``。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    references = refs if refs is not None else ref
    if references is None:
        raise _fail("invalid_argument", "measure_entity needs ref (one uuid) or refs (uuid sequence)")
    entities = _subset(sketch, references)
    tokens = ["entity_count=%d" % len(entities)]
    for entity in entities:
        uuid = _uuid_of(entity)
        points, closed = _points_of(entity)
        length = _round_nm(_curve_length(entity)) if points else None
        radius = None
        try:
            geom = _geom(entity)
        except CadError:
            geom = {}
        for key in ("r", "radius", "r_nm", "radius_nm"):
            if key in geom:
                try:
                    radius = _round_nm(float(geom[key]))
                except (TypeError, ValueError):
                    radius = None
                break
        area = None
        if closed:
            area = _token_int(measure_area(sk_or_doc, boundary=[uuid]), "area_nm2")
        angle = None
        if _kind_of(entity) == "line" and len(points) >= 2:
            angle = math.degrees(math.atan2(points[-1][1] - points[0][1], points[-1][0] - points[0][0]))
        tokens.extend([
            "entity=%s" % uuid,
            "kind=%s" % _kind_of(entity),
            "curve_length_nm=%s" % ("NA" if length is None else "%d" % length),
            "closed=%d" % (1 if closed else 0),
            "radius_nm=%s" % ("NA" if radius is None else "%d" % radius),
            "area_nm2=%s" % ("NA" if area is None else "%d" % area),
            "angle_deg=%s" % ("NA" if angle is None else "%.3f" % angle),
        ])
    return tokens


def measure_point(sk_or_doc: Any, *, point: Any, tol_nm: Any = None) -> list[str]:
    """内部点读数：光标点落在哪个闭合区域 ⇒ 该区域的边界成员 + 面积/周长（只读）。

    区域识别复用 ``boundary_from_point``（点在区域内且有最小包含环，否则 ``CadError(reason="no_boundary")``
    —— 不猜最近环）；面积/周长复用 ``measure_area``，因此与分列 op **同源同数字**。
    """
    members = boundary_from_point(sk_or_doc, point=point, tol_nm=tol_nm)
    tolerance = 0 if tol_nm is None else _nm(tol_nm, what="tol_nm")
    tokens = ["loop_entities=%d" % len(members), "boundary=%s" % ",".join(str(item) for item in members)]
    return tokens + list(measure_area(sk_or_doc, boundary=members, tol_nm=tolerance))


# ======================================================================================
# 统一入口（MEASUREGEOM / MEA）
# ======================================================================================


def measure_geom(sk_or_doc: Any, *, mode: Any = "distance", **kwargs: Any) -> list[str]:
    """**统一测量入口**：一个 op 覆盖距离 / 面积 / 周长 / 悬停读数 / 逐实体读数 / 内部点读数。

    ``mode`` 取值（大小写不敏感，别名并列）：``distance``（dist）、``area``（region）、
    ``perimeter``（length）、``quick``（hover / readout）、``entity``（object / entities）、
    ``point``（interior）。其余值 ⇒ ``CadError(reason="unsupported_mode")``（带 ``allowed`` 明细，
    **不静默退回默认量法**）。

    ``kwargs`` 原样转给对应分列 op（例如 ``mode="distance"`` 收 ``p1_nm/p2_nm/ref_a/ref_b``；
    ``mode="quick"`` 收 ``cursor_nm/aperture_nm/refs/max_results``；``mode="perimeter"`` 不收 ``tol_nm``，
    因为 ``measure_perimeter`` 本身没有该参数 —— 传了会由 Python 抛 TypeError，这里**不吞**）。

    **返回**：``mode=<规范名>`` 开头，后接该量法的 token（与分列 op 逐 token 相同）。
    """
    key = str(mode if mode is not None else "").strip().lower()
    if key in ("distance", "dist"):
        canonical, tokens = "distance", measure_distance(sk_or_doc, **kwargs)
    elif key in ("area", "region"):
        canonical, tokens = "area", measure_area(sk_or_doc, **kwargs)
    elif key in ("perimeter", "length"):
        canonical, tokens = "perimeter", measure_perimeter(sk_or_doc, **kwargs)
    elif key in ("quick", "hover", "readout"):
        canonical, tokens = "quick", measure_hover(sk_or_doc, **kwargs)
    elif key in ("entity", "object", "entities"):
        canonical, tokens = "entity", measure_entity(sk_or_doc, **kwargs)
    elif key in ("point", "interior"):
        canonical, tokens = "point", measure_point(sk_or_doc, **kwargs)
    else:
        raise _fail("unsupported_mode", "unknown measurement mode",
                    mode=str(mode), allowed=list(_MODES))
    return ["mode=%s" % canonical] + list(tokens)


# ======================================================================================
# 操作表（主名 + 别名；别名与主名指向同一函数，`OPERATIONS` 是唯一发现入口）
# ======================================================================================

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 统一入口（MEASUREGEOM / MEA）：一个键覆盖距离/面积/周长/悬停/逐实体/内部点
    "measure_geom": measure_geom,
    "measuregeom": measure_geom,
    "mea": measure_geom,
    # 悬停快速读数（读数数据后果；GUI 绑定在 apps 层，不在本层）
    "measure_hover": measure_hover,
    "quick_measure": measure_hover,
    # 逐实体读数 / 内部点读数
    "measure_entity": measure_entity,
    "measure_point": measure_point,
}


# ======================================================================================
# 自测（硬断言；真几何真数字，不做"跑通即绿"）
# ======================================================================================


def _demo() -> bool:
    """打印真实读数（证据行）；任一步失败 ⇒ 返回 False（不静默跳过）。"""
    try:
        rectangle = _selftest_rectangle(100.0, 50.0)
        print("DEMO measure_geom mode=area | " + " ".join(measure_geom(rectangle, mode="area")))
        sketch = _selftest_rectangle(100.0, 50.0)
        entities = _entities(sketch)
        cursor = (0, 0)
        for entity in entities:
            points, _closed = _points_of(entity)
            if points:
                cursor = (int(round((points[0][0] + points[-1][0]) / 2.0)),
                          int(round((points[0][1] + points[-1][1]) / 2.0)))
                break
        print("DEMO measure_geom mode=quick | " + " ".join(
            measure_geom(sketch, mode="quick", cursor_nm=cursor)))
        circle = _selftest_circle(10.0)
        refs = [_uuid_of(entity) for entity in _entities(circle)]
        print("DEMO measure_geom mode=entity | " + " ".join(
            measure_geom(circle, mode="entity", refs=refs)))
        return True
    except CadError as exc:
        print("DEMO_FAIL reason=%s detail=%s" % (exc.reason, exc.message))
        return False


def selftest() -> list[tuple[str, bool, str]]:
    """硬断言清单：返回 (名称, 是否通过, 明细)。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, bool(ok), detail))

    # ---- 统一入口 == 分列 op（同源同数字）+ 100x50mm 矩形的解析真值 ----
    rectangle = _selftest_rectangle(100.0, 50.0)
    unified = measure_geom(rectangle, mode="area")
    direct = measure_area(rectangle)
    check("unified_entry_matches_direct_ops", list(unified[1:]) == list(direct),
          "unified=%r direct=%r" % (list(unified[1:]), list(direct)))
    check("rect_area_exact_nm2", _token(unified, "area_nm2") == "5000000000000000",
          "area_nm2=%s" % _token(unified, "area_nm2"))
    check("rect_perimeter_exact_nm", _token(unified, "perimeter_nm") == "300000000",
          "perimeter_nm=%s" % _token(unified, "perimeter_nm"))

    # ---- 悬停读数：光标压在真实线段中点上 ----
    sketch = _selftest_rectangle(100.0, 50.0)
    straight = None
    for entity in _entities(sketch):
        if _kind_of(entity) in ("line", "polyline"):
            straight = entity
            break
    if straight is None:
        check("hover_snap_kind_midpoint", False, "no line/polyline entity in rectangle sketch")
    else:
        points, _closed = _points_of(straight)
        if not points:
            check("hover_snap_kind_midpoint", False, "chosen entity has no usable geometry")
        else:
            cursor = (int(round((points[0][0] + points[-1][0]) / 2.0)),
                      int(round((points[0][1] + points[-1][1]) / 2.0)))
            hover = measure_geom(sketch, mode="quick", cursor_nm=cursor)
            check("hover_snap_kind_midpoint", _token(hover, "snap_kind") == "midpoint",
                  "snap_kind=%s cursor=%d,%d snap_nm=%s"
                  % (_token(hover, "snap_kind"), cursor[0], cursor[1], _token(hover, "snap_nm")))
            snapped = _token(hover, "snap_nm")
            near = False
            if snapped and snapped != "NA":
                sx, sy = (int(part) for part in snapped.split(","))
                near = abs(sx - cursor[0]) <= 1 and abs(sy - cursor[1]) <= 1
            check("hover_snap_hits_real_midpoint", near,
                  "snap_nm=%s cursor=%d,%d" % (snapped, cursor[0], cursor[1]))
            distance = _token_int(hover, "distance_nm")
            check("hover_distance_le_1nm", distance is not None and distance <= 1,
                  "distance_nm=%s" % _token(hover, "distance_nm"))
            check("hover_readout_flag", _token(hover, "hover_readout") == "1",
                  "hover_readout=%s" % _token(hover, "hover_readout"))

    # ---- 三态与错误路径：孔径内真为零 / 空选择 / 未知 mode ----
    far = measure_geom(sketch, mode="quick", cursor_nm=(10 ** 12, 10 ** 12), aperture_nm=1)
    check("hover_aperture_true_zero",
          _token(far, "candidate_count") == "0" and _token(far, "snap_kind") == "NA",
          "candidate_count=%s snap_kind=%s" % (_token(far, "candidate_count"), _token(far, "snap_kind")))
    try:
        measure_geom(sketch, mode="quick", cursor_nm=(0, 0), refs=[])
        check("hover_empty_refs_raises_empty_selection", False, "no CadError raised")
    except CadError as exc:
        check("hover_empty_refs_raises_empty_selection", exc.reason == "empty_selection",
              "reason=%s" % exc.reason)
    try:
        measure_geom(sketch, mode="no_such_mode")
        check("unknown_mode_raises_unsupported", False, "no CadError raised")
    except CadError as exc:
        check("unknown_mode_raises_unsupported", exc.reason == "unsupported_mode",
              "reason=%s" % exc.reason)

    # ---- 逐实体读数：圆半径解析真值 10mm=1e7 nm；周长容差 0.5%（族内同一把尺子） ----
    circle = _selftest_circle(10.0)
    circle_refs = [_uuid_of(entity) for entity in _entities(circle)]
    if not circle_refs:
        check("circle_radius_exact_nm", False, "circle sketch has no entities")
    else:
        report = measure_geom(circle, mode="entity", refs=circle_refs)
        check("circle_radius_exact_nm", _token(report, "radius_nm") == "10000000",
              "radius_nm=%s" % _token(report, "radius_nm"))
        length = _token_int(report, "curve_length_nm")
        expected = 2.0 * math.pi * 10.0 * 1e6
        ok_length = length is not None and abs(length - expected) <= 0.005 * expected
        check("circle_circumference_within_half_percent", ok_length,
              "curve_length_nm=%s expect~%d" % (_token(report, "curve_length_nm"), int(expected)))
    return checks


def _main() -> int:
    demo_ok = _demo()
    checks = selftest()
    passed = sum(1 for _name, ok, _detail in checks if ok)
    for name, ok, detail in checks:
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if demo_ok and checks and passed == len(checks):
        print("FEATURE_OK family=%s op=%s verdict=PASS assertions=%d/%d"
              % (FAMILY, PRIMARY_OP, passed, len(checks)))
        return 0
    print("FEATURE_FAIL family=%s op=%s verdict=FAIL assertions=%d/%d demo=%d"
          % (FAMILY, PRIMARY_OP, passed, len(checks), int(demo_ok)))
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "measure_geom": {"tier": "query", "readonly": True, "returns": "data"},
    "measuregeom": {"tier": "query", "readonly": True, "returns": "data"},
    "mea": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_hover": {"tier": "query", "readonly": True, "returns": "data"},
    "quick_measure": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_entity": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_point": {"tier": "query", "readonly": True, "returns": "data"},
}
