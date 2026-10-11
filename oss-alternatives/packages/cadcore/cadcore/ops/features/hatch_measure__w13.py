#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w13 —— hatch_measure 族「渐变色填充（GRADIENT）」的真几何片模块。

一句话用途
----------
把 AutoCAD 的渐变色填充在 L1 文档模型上做成**真几何 + 真数据**的 op：沿渐变轴把真实闭合区域
**精确分带**，每带轮廓落成真实 L1 ``line`` 实体（可被 draw / 投影链路直接消费），每带的参数区间、
颜色区间与**整数纳米面积**记进表；面积全程用 ``fractions.Fraction`` 精确计算并按最大余数法分配，
故 ``Σ band.area_nm2 == area_nm2`` **恒等成立**（自检里逐条断言，不是四舍五入后"差不多"）。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "hatch_measure"``；
- ``OPERATIONS: dict[str, Callable]``：``gradient_fill``（别名 ``gradient``）、``gradient_query``；
- 写操作 ``op(sk_or_doc, **kwargs) -> list[str]`` 返回**新建实体 uuid 列表**（顺序：带号升序 →
  带内片段顺序 → 环上边序）；查询 op 返回 ``"key=value"`` token 列表；长度一律整数纳米（nm）；
  错误一律抛 ``cadcore.errors.CadError(reason=...)``，``reason`` 是唯一机读归因入口。

L1 装得下什么、装不下什么（边界写清，宁可红不许假绿）
----------------------------------------------------
1. **装得下（本模块已真实现）**：渐变轴 ``angle_deg``、分带 ``bands`` / ``stops``（真等值线位置）、
   对称 ``centered``（参数自区域中心向外，真几何镜像）、真实边界/孤岛（``island``）/间隙公差
   （``tol_nm``）、每带**精确面积**、每带**颜色参数区间**、每带轮廓的**真实实体**。
   分带边界是**唯一确定的等值线**（不是拿密集线冒充满色）：带数由 ``bands`` 显式声明、
   相邻带共享轮廓、几何与面积出自**同一次**计算 ⇒ 可被独立复算复核。
2. **装不下**：L1 实体字段白名单 ``{uuid, kind, layer, construction, geom}`` **没有颜色/着色字段**
   ⇒ 颜色过渡**渲染不出来**。记录里两条**不同形**、下游不许把 0 读成"没采"：
   ``color_rendered=0``（真为零：本层一个着色后果都没有）、``geometry_rendered=1``（真几何已落盘）。
3. **显式不支持（问法成立、L1 装不下 ⇒ 报错，不静默兜底）**：``gradient_name`` 只实现 ``LINEAR``
   （有确定的渐变轴）；``CYLINDER`` / ``SPHERICAL`` / ``HELIX`` 是径向/角向渐变，本层不猜轴向 ⇒
   ``CadError(reason="unsupported_gradient")``（冻结枚举仍走既有 ``_gradient_name``，未知名报
   ``unknown_gradient``）。实心 ``solid`` 面同样不冒充（既有族管线报 ``unsupported_pattern``）。
4. **面积口径**：输入坐标 → 有理数（``Fraction(...).limit_denominator(10**6)``）；每带面积 =
   Σ(实心环片段面积) − Σ(洞环片段面积)（有符号面积取绝对值，与环走向无关）；分带区间覆盖全部环
   的轴向投影范围 ⇒ 分带是对区域的一个**真划分**，和恒等于净面积。圆/圆弧边界沿用既有族管线的
   离散成环（``_closed_loops``），故面积是**该多边形**的精确值（解析式面积见 ``measure_area``）。
5. **复用而非重写**：成环 / 孤岛 / 区域 / 入参校验 / 落线一律走 ``cadcore.ops.hatch_measure``
   的既有实现（本文件不抄第二份校验器，也不改它的实体字段集）。表沿用该族的 ``hatch_measure`` 表，
   只**新增** ``gradients`` 列表（只加键不改键）。
6. **没有 ``ops/__init__.py``**（族模块靠命名空间包被发现）；直跑本文件时用下面的导入兜底自举。

错误 reason 清单（机读）
------------------------
``unsupported_gradient`` / ``unknown_gradient`` / ``invalid_colors`` / ``invalid_stops`` /
``invalid_argument`` / ``no_boundary`` / ``degenerate_region`` / ``too_many_lines`` /
``unknown_reference`` / ``invalid_length`` / ``geometry_invalid`` / ``invalid_argument``（入参把关复用既有校验器）。
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence as _SequenceABC
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from cadcore.edit.modify import _round_nm
from cadcore.errors import CadError
from ..hatch_measure import (
        _angle,
        _append_line,
        _closed_loops,
        _count,
        _entities,
        _fail,
        _flag,
        _gap_tolerance,
        _gradient_colors,
        _gradient_name,
        _island_style,
        _layer_name,
        _region,
        _sketch_of,
        _subset,
        _table,
        _transparency,
        _uuid_of,
        measure_area,
    )

#: 本模块实现的渐变类型（其余类型 L1 无确定轴向 ⇒ 显式不支持，见 docstring 第 3 条）。
_LINEAR = "LINEAR"

#: 分带数上限（悬空保护：带数×片段数会线性放大实体数）。
_MAX_BANDS = 64

#: 分带轮廓线段数上限（缺省值；超过报 ``too_many_lines``，不静默截断成"半张图"）。
_MAX_LINES = 20_000


# ======================================================================================
# 有理数几何（精确面积、半平面裁剪）
# ======================================================================================


def _as_fraction(value: Any) -> Fraction:
    """坐标 → 有理数（整数精确；浮点按 1e-6 分母逼近 ⇒ 同一批坐标自洽，面积和恒等成立）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("geometry_invalid", "boundary coordinate must be a number", value=repr(value))
    number = float(value)
    if not math.isfinite(number):
        raise _fail("geometry_invalid", "boundary coordinate must be finite", value=repr(value))
    return Fraction(number).limit_denominator(1_000_000)


def _as_float(value: Fraction) -> float:
    return float(value)


def _signed_area(polygon: list[tuple[Fraction, Fraction]]) -> Fraction:
    """有符号面积（nm²，精确；首末点相同也不受影响：按环遍历，退化边贡献 0）。"""
    if len(polygon) < 3:
        return Fraction(0)
    total = Fraction(0)
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2


def _clip_half(polygon: list[tuple[Fraction, Fraction]], ux: Fraction, uy: Fraction,
               level: Fraction, keep_ge: bool) -> list[tuple[Fraction, Fraction]]:
    """半平面裁剪（``keep_ge=True`` 保留 ``u·p >= level``；False 保留 ``u·p <= level``）。

    Sutherland–Hodgman 单半平面裁剪对**凹多边形**会产出"贴边来回"的退化边，
    但有符号面积仍然正确（退化边在 shoelace 里正负抵消）⇒ 面积守恒不受形状影响。
    """
    if not polygon:
        return []
    out: list[tuple[Fraction, Fraction]] = []
    count = len(polygon)
    for index in range(count):
        current = polygon[index]
        following = polygon[(index + 1) % count]
        d_current = ux * current[0] + uy * current[1] - level
        d_following = ux * following[0] + uy * following[1] - level
        if not keep_ge:
            d_current, d_following = -d_current, -d_following
        inside_current = d_current >= 0
        inside_following = d_following >= 0
        if inside_current:
            out.append(current)
        if inside_current != inside_following:
            ratio = d_current / (d_current - d_following)
            out.append((current[0] + ratio * (following[0] - current[0]),
                        current[1] + ratio * (following[1] - current[1])))
    return out


def _edge_fractions(stops: Any, bands: int) -> list[Fraction]:
    """``stops`` → ``bands+1`` 个递增参数（缺省等分 ``1/bands``）；不合法报 ``invalid_stops``。"""
    if stops is None:
        return [Fraction(index, bands) for index in range(bands + 1)]
    if isinstance(stops, (str, bytes)) or not isinstance(stops, _SequenceABC) or len(stops) != bands + 1:
        raise _fail("invalid_stops", "stops must hold bands+1 finite fractions from 0 to 1",
                    bands=bands, stops=repr(stops))
    values: list[Fraction] = []
    for item in stops:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise _fail("invalid_stops", "stops entries must be finite numbers in [0, 1]", value=repr(item))
        number = float(item)
        if not 0.0 <= number <= 1.0:
            raise _fail("invalid_stops", "stops entries must be in [0, 1]", value=repr(item))
        values.append(Fraction(number).limit_denominator(1_000_000))
    if values[0] != 0 or values[-1] != 1:
        raise _fail("invalid_stops", "stops must start at 0 and end at 1",
                    first=_as_float(values[0]), last=_as_float(values[-1]))
    for index in range(bands):
        if values[index] >= values[index + 1]:
            raise _fail("invalid_stops", "stops must be strictly ascending",
                        index=index, stops=repr(stops))
    return values


def _axis_unit(angle_deg: float) -> tuple[Fraction, Fraction]:
    """角度 → 单位轴（有理化；0°/90° 等整角精确）。"""
    radians = math.radians(float(angle_deg))
    return (Fraction(math.cos(radians)).limit_denominator(10 ** 12),
            Fraction(math.sin(radians)).limit_denominator(10 ** 12))


def _gradient_records(table: Any) -> list[Any]:
    """表里的 ``gradients`` 列表（不存在返回 ``[]``，只读路径不建表）。"""
    if table is None:
        return []
    records = table.get("gradients")
    return records if isinstance(records, list) else []


# ======================================================================================
# 写操作：gradient_fill（渐变色填充 → 真分带几何 + 真面积账）
# ======================================================================================


def gradient_fill(sk_or_doc: Any, *, boundary: Any = None, colors: Any = None,
                  gradient_name: Any = _LINEAR, angle_deg: Any = 0.0, bands: Any = 8,
                  stops: Any = None, centered: Any = False, layer: Any = "0",
                  transparency: Any = None, island: Any = "normal", tol_nm: Any = None,
                  max_lines: Any = _MAX_LINES) -> list[str]:
    """渐变色填充：沿渐变轴把真实闭合区域精确分带，返回**新建分带轮廓线**的 uuid 列表。

    参数（未给的一律不猜：缺边界报 ``no_boundary``，不合法值报对应 reason）
    ------------------------------------------------------------------
    - ``boundary``：``None`` = 全图成环；uuid 或 uuid 序列 = 指定边界（走既有 ``_subset`` /
      ``_closed_loops`` / ``_region`` 管线，孤岛与洞按 ``island`` 归属）。
    - ``gradient_name``：只支持 ``LINEAR``（径向/角向类型报 ``unsupported_gradient``）。
    - ``colors``：``None`` = 单色渐变；``(起点色, 终点色)`` = 双色（一端 None 报 ``invalid_colors``）。
    - ``angle_deg``：渐变轴角度（度，0 = 沿 +X）。
    - ``bands``：分带数（2..64，缺省 8）；``stops``：``bands+1`` 个 [0,1] 严格递增参数
      （缺省等分），决定每带的参数区间。
    - ``centered``：``True`` 时参数自区域中心向两侧展开（AutoCAD 的 centered 语义）：
      第 k 带的几何 = 中心两侧各一片镜像片段，带 0 落在中心。
    - ``layer``：轮廓线落层；``transparency``/``island``/``tol_nm`` 与族内其它 op 同义。
    - ``max_lines``：线段数悬空保护（超过报 ``too_many_lines``，**先算后写**，绝不半张图）。

    几何与数据后果（同一次计算，可被独立复算）
    ------------------------------------------
    - 每带轮廓 = 该带与区域的**交**（半平面夹条裁剪，有理数精确），逐边落成真实 ``line``；
      相邻带共享等值线；无面积片段不产实体（不产零长线、不产假几何）。
    - 表记录（``hatch_measure`` 表 ``gradients`` 列表）含：``bands`` / ``stops`` / ``centered`` /
      ``angle_deg`` / ``colors`` / ``layer`` / ``transparency`` / ``island`` / ``boundary`` /
      ``axis_ux`` / ``axis_uy`` / ``t_min_nm`` / ``t_max_nm`` / ``area_nm2`` / ``loop_count`` /
      ``line_count`` / ``lines`` / ``band_list``（逐带 ``p_from``/``p_to``/``t_from_nm``/
      ``t_to_nm``/``mirror``/``area_nm2``/``line_count``/``lines``）/ ``geometry_rendered=1`` /
      ``color_rendered=0``。
    - **守恒不变量**：``Σ band_list[*].area_nm2 == area_nm2``（整数恒等，最大余数法分配）。
    - 本 op **不动边界几何一个纳米**（只追加新实体 + 记录）。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    name = _gradient_name(gradient_name)
    if name != _LINEAR:
        raise _fail("unsupported_gradient",
                    "L1 band partition has a definite axis only for LINEAR; radial/angular gradients are not faked",
                    gradient_name=name, supported=[_LINEAR])
    colour_list, two_colour = _gradient_colors(colors)
    angle = _angle(angle_deg, what="angle_deg")
    count = _count(bands, what="bands")
    if not 2 <= count <= _MAX_BANDS:
        raise _fail("invalid_argument", "bands must be within the frozen range", bands=count,
                    minimum=2, maximum=_MAX_BANDS)
    centred = _flag(centered, what="centered")
    layer_name = _layer_name(layer)
    transparency_value = _transparency(transparency)
    island_style = _island_style(island)
    tolerance = _gap_tolerance(sketch, doc, tol_nm)
    budget = _count(max_lines, what="max_lines")
    if budget < 1:
        raise _fail("invalid_argument", "max_lines must be >= 1", max_lines=budget)

    edges = _edge_fractions(stops, count)

    entities = _entities(sketch)
    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    if not loops:
        raise _fail("no_boundary",
                    "no closed loop to fill; pass boundary=<uuid,...> or close the loop",
                    boundary=repr(boundary))
    by_uuid = {_uuid_of(entity): entity for entity in entities}
    region = _region(loops, by_uuid, island_style)
    solid_loops = [region["outer"]] + list(region["isolated"])
    hole_loops = list(region["holes"])

    ux, uy = _axis_unit(angle)
    projected = [_as_fraction(point[0]) * ux + _as_fraction(point[1]) * uy
                 for loop in loops for point in loop["points"]]
    if not projected:
        raise _fail("geometry_invalid", "boundary loops carry no vertices", boundary=repr(boundary))
    t_min, t_max = min(projected), max(projected)
    if t_max <= t_min:
        raise _fail("degenerate_region", "boundary has zero extent along the gradient axis",
                    t_min_nm=_round_nm(_as_float(t_min)), t_max_nm=_round_nm(_as_float(t_max)))

    polygons: list[tuple[list[tuple[Fraction, Fraction]], Fraction]] = []
    for loop in solid_loops:
        polygons.append(([(_as_fraction(point[0]), _as_fraction(point[1]))
                          for point in loop["points"]], Fraction(1)))
    for loop in hole_loops:
        polygons.append(([(_as_fraction(point[0]), _as_fraction(point[1])) for point in loop["points"]],
                         Fraction(-1)))

    centre = (t_min + t_max) / 2
    half = (t_max - t_min) / 2
    band_areas: list[Fraction] = []
    band_pieces: list[list[list[tuple[Fraction, Fraction]]]] = []
    band_primary: list[tuple[Fraction, Fraction]] = []
    for index in range(count):
        p_from, p_to = edges[index], edges[index + 1]
        if centred:
            strips = [(centre + p_from * half, centre + p_to * half),
                      (centre - p_to * half, centre - p_from * half)]
        else:
            strips = [(t_min + p_from * (t_max - t_min), t_min + p_to * (t_max - t_min))]
        total = Fraction(0)
        pieces: list[list[tuple[Fraction, Fraction]]] = []
        for polygon, sign in polygons:
            for low, high in strips:
                piece = _clip_half(_clip_half(polygon, ux, uy, low, True), ux, uy, high, False)
                if len(piece) < 3:
                    continue
                area = abs(_signed_area(piece))
                if area == 0:
                    continue
                total += sign * area
                pieces.append(piece)
        band_areas.append(total)
        band_pieces.append(pieces)
        band_primary.append(strips[0])

    net_exact = sum(band_areas)          # 分带是对区域的真划分 ⇒ 与净面积恒等
    if net_exact <= 0:
        raise _fail("degenerate_region", "clipped region has zero net area; check boundary/island",
                    boundary=repr(boundary), island=island_style)
    total_int = int(net_exact + Fraction(1, 2))
    floors = [int(value) for value in band_areas]
    remainder = total_int - sum(floors)
    if not 0 <= remainder <= count:
        raise _fail("geometry_error", "band area apportionment out of range",
                    remainder=remainder, bands=count)
    band_int = list(floors)
    for position in sorted(range(count), key=lambda item: (-(band_areas[item] - floors[item]), item))[:remainder]:
        band_int[position] += 1

    pending: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for index in range(count):
        for piece in band_pieces[index]:
            ring = [(_as_float(point[0]), _as_float(point[1])) for point in piece]
            if len(ring) < 3:
                continue
            for position in range(len(ring)):
                pending.append((index, ring[position], ring[(position + 1) % len(ring)]))
    if len(pending) > budget:
        raise _fail("too_many_lines", "gradient bands would exceed max_lines; lower bands or raise max_lines",
                    max_lines=budget, bands=count, segments=len(pending))

    created: list[str] = []
    band_lines: list[list[str]] = [[] for _ in range(count)]
    for index, start, end in pending:
        line_uuid = _append_line(sketch, start, end, layer_name)
        if line_uuid is not None:
            band_lines[index].append(line_uuid)
            created.append(line_uuid)

    table = _table(sketch, doc, create=True)
    records = table.get("gradients")
    if not isinstance(records, list):
        records = []
        table["gradients"] = records
    band_records = []
    for index in range(count):
        primary = band_primary[index]
        band_records.append({
            "index": index,
            "p_from": _as_float(edges[index]),
            "p_to": _as_float(edges[index + 1]),
            "t_from_nm": int(_round_nm(_as_float(primary[0]))),
            "t_to_nm": int(_round_nm(_as_float(primary[1]))),
            "mirror": 1 if centred else 0,
            "area_nm2": int(band_int[index]),
            "line_count": len(band_lines[index]),
            "lines": list(band_lines[index]),
        })
    records.append({
        "uuid": _new_record_uuid(),
        "kind": "gradient_bands",
        "schema": 1,
        "gradient_name": name,
        "colors": list(colour_list),
        "two_color": int(two_colour),
        "angle_deg": _as_float(angle),
        "centered": 1 if centred else 0,
        "bands": count,
        "stops": [_as_float(value) for value in edges],
        "layer": layer_name,
        "transparency": _as_float(transparency_value),
        "island": island_style,
        "boundary": [member for loop in loops for member in loop["members"]],
        "tol_nm": int(tolerance),
        "axis_ux": _as_float(ux),
        "axis_uy": _as_float(uy),
        "t_min_nm": int(_round_nm(_as_float(t_min))),
        "t_max_nm": int(_round_nm(_as_float(t_max))),
        "area_nm2": total_int,
        "loop_count": len(loops),
        "line_count": len(created),
        "lines": list(created),
        "band_list": band_records,
        "geometry_rendered": 1,
        "color_rendered": 0,
    })
    return created


def _new_record_uuid() -> str:
    """记录 uuid（懒导入，避免与族内 ``new_uuid`` 的导入兜底互相牵连）。"""
    from cadcore.sketch.constraints import new_uuid
    return str(new_uuid())


# ======================================================================================
# 查询：gradient_query（只读，不建表不写入）
# ======================================================================================


def _token(key: str, value: Any) -> str:
    if value is None:
        rendered = "NA"
    else:
        rendered = str(value)
    return "%s=%s" % (key, rendered)


def gradient_query(sk_or_doc: Any, *, gradient: Any = None) -> list[str]:
    """回读一条渐变记录（``gradient=None`` 取最新一条，否则按记录 uuid 取）。

    token 顺序：``gradient`` / ``gradient_name`` / ``colors`` / ``bands`` / ``angle_deg`` /
    ``centered`` / ``area_nm2`` / ``line_count`` / ``geometry_rendered`` / ``color_rendered`` /
    ``band_areas``（``|`` 分隔，带号升序）。没有记录 / uuid 不存在 ⇒ ``unknown_reference``
    （**不返回空列表冒充"查到 0"**）；非本模块写的记录缺字段 ⇒ 该 token 为 ``NA``（未采，不同形于 0）。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=False)
    records = _gradient_records(table)
    if not records:
        raise _fail("unknown_reference", "no gradient record in this target", reference=repr(gradient))
    chosen: Any = None
    if gradient is None:
        chosen = records[-1]
    else:
        reference = gradient if isinstance(gradient, str) else str(gradient)
        for record in records:
            if isinstance(record, dict) and record.get("uuid") == reference:
                chosen = record
                break
        if chosen is None:
            raise _fail("unknown_reference", "no gradient record with this uuid", reference=reference)
    bands = chosen.get("band_list")
    tokens = [
        _token("gradient", chosen.get("uuid")),
        _token("gradient_name", chosen.get("gradient_name")),
        _token("colors", ",".join(str(item) for item in (chosen.get("colors") or []))),
        _token("bands", len(bands) if isinstance(bands, list) else "NA"),
        _token("angle_deg", chosen.get("angle_deg")),
        _token("centered", chosen.get("centered")),
        _token("area_nm2", chosen.get("area_nm2")),
        _token("line_count", chosen.get("line_count")),
        _token("geometry_rendered", chosen.get("geometry_rendered")),
        _token("color_rendered", chosen.get("color_rendered")),
    ]
    if isinstance(bands, list):
        tokens.append("band_areas=" + "|".join(str(item.get("area_nm2", "NA")) for item in bands))
    return tokens


#: 冻结入口（主名 + 别名指向同一函数）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "gradient_fill": gradient_fill,
    "gradient": gradient_fill,
    "gradient_query": gradient_query,
}

#: 族名（族门按它发现本模块）。
FAMILY = "hatch_measure"


# ======================================================================================
# 自检（``if __name__ == "__main__"`` 跑；末尾打 FEATURE_OK family=hatch_measure ...）
# ======================================================================================

_NM_PER_MM = 1_000_000


def _selftest_rectangle(width_mm: float = 100.0, height_mm: float = 50.0) -> Any:
    width, height = int(width_mm * _NM_PER_MM), int(height_mm * _NM_PER_MM)
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"uuid": _new_record_uuid(), "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"entities": entities, "constraints": [], "dimensions": []}


def _selftest_circle(radius_mm: float = 10.0) -> Any:
    radius = int(radius_mm * _NM_PER_MM)
    return {"entities": [{"uuid": _new_record_uuid(), "kind": "circle", "layer": "0", "construction": False,
                          "geom": {"cx": 0, "cy": 0, "r": radius}}],
            "constraints": [], "dimensions": []}


def _token_value(tokens: list[str], key: str) -> str:
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def _last_gradient(sketch: Any) -> Any:
    table = sketch.get("hatch_measure")
    records = (table or {}).get("gradients") or []
    return records[-1] if records else None


def _check_band_partition() -> tuple[bool, str, int]:
    """硬断言 1：100mm×50mm 矩形沿 +X 四等分 ⇒ 真线实体、逐带面积精确、和守恒、参数递增。"""
    sketch = _selftest_rectangle()
    baseline = int(_token_value(measure_area(sketch), "area_nm2"))   # 独立代码路径（族内浮点 shoelace）
    created = gradient_fill(sketch, colors=("RED", "BLUE"), angle_deg=0.0, bands=4, layer="HATCH")
    record = _last_gradient(sketch)
    by_uuid = {entity["uuid"]: entity for entity in sketch["entities"]}
    total = 100 * _NM_PER_MM * 50 * _NM_PER_MM
    per_band = total // 4
    samples = [(record["band_list"][index]["t_from_nm"], record["band_list"][index]["t_to_nm"])
               for index in range(4)]
    checks = [
        len(created) > 0 and record is not None,
        all(item in by_uuid and by_uuid[item]["kind"] == "line" for item in created),
        all(isinstance(by_uuid[item]["geom"][key], int) and not isinstance(by_uuid[item]["geom"][key], bool)
            for item in created for key in ("x1", "y1", "x2", "y2")),
        all(0 <= by_uuid[item]["geom"]["x1"] <= 100 * _NM_PER_MM and 0 <= by_uuid[item]["geom"]["x2"] <= 100 * _NM_PER_MM
            and 0 <= by_uuid[item]["geom"]["y1"] <= 50 * _NM_PER_MM and 0 <= by_uuid[item]["geom"]["y2"] <= 50 * _NM_PER_MM
            for item in created),
        sum(band["area_nm2"] for band in record["band_list"]) == record["area_nm2"] == total == baseline,
        [band["area_nm2"] for band in record["band_list"]] == [per_band] * 4,
        [band["p_from"] for band in record["band_list"]] == [0.0, 0.25, 0.5, 0.75],
        samples == [(0, 25 * _NM_PER_MM), (25 * _NM_PER_MM, 50 * _NM_PER_MM),
                    (50 * _NM_PER_MM, 75 * _NM_PER_MM), (75 * _NM_PER_MM, 100 * _NM_PER_MM)],
        all(band["line_count"] >= 4 and len(band["lines"]) == band["line_count"] for band in record["band_list"]),
        record["line_count"] == len(created) and len(record["lines"]) == len(created),
        record["geometry_rendered"] == 1 and record["color_rendered"] == 0,
        record["centered"] == 0 and record["boundary"] and record["loop_count"] == 1,
    ]
    ok = all(checks)
    detail = "checks=%d/%d bands=%s area=%s baseline=%s lines=%d" % (
        sum(1 for item in checks if item), len(checks),
        [band["area_nm2"] for band in record["band_list"]], record["area_nm2"], baseline, len(created))
    return ok, detail, len(checks)


def _check_centered_axis() -> tuple[bool, str, int]:
    """硬断言 2：centered=1 真改几何（参数自中心向外、带 0 跨中心），面积账不变。"""
    plain = _selftest_rectangle()
    gradient_fill(plain, angle_deg=0.0, bands=4, centered=False)
    centred = _selftest_rectangle()
    gradient_fill(centred, angle_deg=0.0, bands=4, centered=True)
    plain_record, centred_record = _last_gradient(plain), _last_gradient(centred)
    total = 100 * _NM_PER_MM * 50 * _NM_PER_MM
    centre = 50 * _NM_PER_MM
    half = 12.5 * _NM_PER_MM
    plain_lines = [{entity["uuid"]: entity for entity in plain["entities"]}[item] for item in plain_record["lines"]]
    centred_lines = [{entity["uuid"]: entity for entity in centred["entities"]}[item] for item in centred_record["lines"]]
    plain_band0 = [entity for entity in plain_lines if entity["uuid"] in set(plain_record["band_list"][0]["lines"])]
    centred_band0 = [entity for entity in centred_lines if entity["uuid"] in set(centred_record["band_list"][0]["lines"])]
    checks = [
        plain_record["centered"] == 0 and centred_record["centered"] == 1,
        (plain_record["band_list"][0]["t_from_nm"], plain_record["band_list"][0]["t_to_nm"]) == (0, 25 * _NM_PER_MM),
        (centred_record["band_list"][0]["t_from_nm"], centred_record["band_list"][0]["t_to_nm"]) == (centre, centre + half),
        all(band["mirror"] == 1 for band in centred_record["band_list"]),
        [band["area_nm2"] for band in centred_record["band_list"]] == [total // 4] * 4,
        sum(band["area_nm2"] for band in centred_record["band_list"]) == centred_record["area_nm2"] == total,
        bool(plain_band0) and max(entity["geom"]["x2"] for entity in plain_band0) == 25 * _NM_PER_MM,
        bool(centred_band0) and min(entity["geom"]["x1"] for entity in centred_band0) < centre
        and max(entity["geom"]["x2"] for entity in centred_band0) > centre,
    ]
    ok = all(checks)
    detail = "checks=%d/%d plain_band0=%s centred_band0=%s areas=%s" % (
        sum(1 for item in checks if item), len(checks),
        (plain_record["band_list"][0]["t_from_nm"], plain_record["band_list"][0]["t_to_nm"]),
        (centred_record["band_list"][0]["t_from_nm"], centred_record["band_list"][0]["t_to_nm"]),
        [band["area_nm2"] for band in centred_record["band_list"]])
    return ok, detail, len(checks)


def _check_errors_and_query() -> tuple[bool, str, int]:
    """硬断言 3：不支持类型/非法参数/无边界逐条报对 reason；查询回读真值且越界引用报错。"""
    sketch = _selftest_rectangle()
    reasons: list[str] = []
    for kwargs in ({"gradient_name": "SPHERICAL"}, {"bands": 1}, {"gradient_name": "BEZIER"},
                   {"colors": ("RED", None)}, {"bands": 3, "stops": [0.0, 0.6, 0.4, 1.0]}):
        try:
            gradient_fill(sketch, **kwargs)
        except CadError as exc:
            reasons.append(exc.reason)
    empty = {"entities": [], "constraints": [], "dimensions": []}
    try:
        gradient_fill(empty)
    except CadError as exc:
        reasons.append(exc.reason)
    created = gradient_fill(sketch, angle_deg=45.0, bands=2, colors=("RED", "BLUE"))
    tokens = gradient_query(sketch)
    try:
        gradient_query(sketch, gradient="no-such-uuid")
    except CadError as exc:
        reasons.append(exc.reason)
    circle = _selftest_circle(10.0)
    circle_created = gradient_fill(circle, angle_deg=0.0, bands=3)
    record = _last_gradient(sketch)
    checks = [
        reasons == ["unsupported_gradient", "invalid_argument", "unknown_gradient",
                    "invalid_colors", "invalid_stops", "no_boundary", "unknown_reference"],
        len(created) > 0 and _token_value(tokens, "bands") == "2",
        _token_value(tokens, "color_rendered") == "0" and _token_value(tokens, "geometry_rendered") == "1",
        _token_value(tokens, "line_count") == str(len(created)),
        _token_value(tokens, "colors") == "RED,BLUE" and _token_value(tokens, "area_nm2") == str(record["area_nm2"]),
        len(_token_value(tokens, "band_areas").split("|")) == 2,
        len(circle_created) > 0 and _last_gradient(circle)["loop_count"] == 1,
    ]
    ok = all(checks)
    detail = "checks=%d/%d reasons=%s bands=%s area=%s" % (
        sum(1 for item in checks if item), len(checks), ",".join(reasons),
        _token_value(tokens, "bands"), _token_value(tokens, "area_nm2"))
    return ok, detail, len(checks)


def selftest() -> list[tuple[str, bool, str, int]]:
    results = [("band_partition",) + _check_band_partition(),
               ("centered_axis",) + _check_centered_axis(),
               ("errors_and_query",) + _check_errors_and_query()]
    return [(name, ok, detail, count) for name, ok, detail, count in results]


def _main() -> int:
    interface_ok = (FAMILY == "hatch_measure" and isinstance(OPERATIONS, dict)
                    and len(OPERATIONS) >= 2 and all(callable(item) for item in OPERATIONS.values()))
    results = selftest()
    assertions = sum(count for _name, _ok, _detail, count in results)
    failed = [name for name, ok, _detail, _count in results if not ok]
    if not interface_ok:
        failed.append("interface")
    for name, ok, detail, _count in results:
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    print("CHECKS interface=%d assertions=%d ops=%d failed=%s residual=color_rendered_0(l1_has_no_colour_field)"
          % (1 if interface_ok else 0, assertions, len(OPERATIONS), ",".join(failed) if failed else "-"))
    if failed:
        print("FEATURE_OK family=hatch_measure op=gradient_fill verdict=FAIL failed=%s" % ",".join(failed))
        return 1
    print("FEATURE_OK family=hatch_measure op=gradient_fill verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
