#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w19 —— 填充与查询族补片（席 w19）：渐变色填充的 L1 可达后果。

一句话用途
----------
补上族审计里"渐变色填充"仍 partial 的那一半：现有 ``ops/hatch_measure.py:gradient_fill`` 只把渐变**定义**
记进表（``rendered=0`` / ``entity_count=0``，返回 ``[]``），**一个几何都不产**。本 op 在这一层交付
**L1 真能表达的那部分后果**：按冻结枚举的渐变坡道切出**真实等值线**（gradient 的 level set），用真实
边界环（含洞、含孤岛奇偶）裁剪成**真实线段实体**，并给出**真实分带面积**（各带面积之和 = 净面积，
落记录里可核对）。交付物是几何与面积，不是一句声明。

冻结接口（本文件对外只有这三样是契约）
------------------------------------
- ``FAMILY = "hatch_measure"``；
- ``OPERATIONS: dict[str, Callable]``：唯一键 ``gradient_bands``；
- ``gradient_bands(sk_or_doc, **kwargs) -> list[str]``：返回**本次新建的 L1 实体 uuid 列表**，
  顺序 = 等值线从坡道起点到终点（LINEAR 沿 ``angle_deg`` 方向；CYLINDER/SPHERICAL 沿半径），
  同一条等值线内按生成顺序。长度一律**整数纳米**；错误一律抛 ``cadcore.errors.CadError(reason=...)``。
- ``sk_or_doc``：草图 dict（``{"entities": [...]}``）、裸实体 list、或带 ``entities`` / ``sketch`` 的
  草图对象（兜底走 ``cadcore.sketch.constraints.sketch_entities``）。**本层不猜 Document 的内部结构**。

本 op 的口径（与全仓一致，别自己发明）
------------------------------------
- 渐变坡道被切成 ``bands`` 段（``centered=True`` 时按"起点→终点→起点"的**镜像坡道**切），相邻段之间
  的等值位置就是等值线：非居中 ``bands-1`` 条，居中 ``2*(bands-1)`` 条。
- 等值线是**数学等值集**在真实区域上的裁剪：LINEAR 用沿梯度轴的行切（精确直线，偶奇配对处理洞与凹形）；
  CYLINDER/SPHERICAL 用等半径圆弧采样（采样步数 ``arc_segments``，圆心 ``origin`` 或区域包围盒中心）。
- 分带面积 ``band_areas_nm2``：**仅 LINEAR** 给（凸 slab 精确裁剪 + 偶奇符号），长度 = ``bands``，
  按坡道顺序；CYLINDER/SPHERICAL 记 ``None`` = **不适用（未算）**，不是 0。空等值线记进 ``empty_levels``
  （不静默丢掉某个位置）。
- ``boundary=None`` = 自动识别全部闭合环；显式给 uuid（或 uuid 序列）时其中的**不可解释实体种类报错**
  （``unsupported_boundary``），不给 uuid 时那些种类只计数进 ``skipped_kinds``。**空序列 ``[]`` 报
  ``invalid_argument``**（"没筛"与"筛出空"不同形）。
- 边界实体**一个纳米都不改**：本 op 只追加图案线段。

不产什么（写下来，防止下游靠猜）
------------------------------
1. **不产像素级/连续着色**：L1 实体字段白名单是 ``{uuid, kind, layer, construction, geom}``，
   没有颜色/着色位。本 op 交付等值线几何 + 分带面积；每条带用什么颜色由下游按冻结的渐变坡道
   （``colors`` / ``two_color`` / ``centered``）确定，记录里的 ``color_source="downstream_ramp"``。
   **本层不写"已渲染"**。若判定口径强制要求"实体自带颜色"，本条仍应记 **partial**。
2. **不用密集线冒充满色/渐变**：``bands`` 上限 ``_MAX_BANDS=64``，超了报 ``too_many_bands``；
   交付的是稀疏等值线（"分带的边界"），不是密排填充线冒充的假实心。
3. ``HELIX`` 渐变**显式不支持** ⇒ ``CadError(reason="unsupported_gradient")``：螺旋渐变沿锥面扫相位，
   投影到 L1 需要一个 L1 没有的逐顶点相位/颜色场，宁可不做也不近似成普通径向场。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑本文件时的自举（与 ops/hatch_measure.py 同款，只多一层目录）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))))))
    __package__ = "cadcore.ops.features"

import math
from typing import Any, Callable

from cadcore.edit.modify import _round_nm
from cadcore.errors import CadError
from cadcore.sketch.constraints import new_uuid, sketch_entities

__all__ = ["FAMILY", "OPERATIONS", "gradient_bands", "DEFAULT_LAYER", "selftest"]

FAMILY = "hatch_measure"

#: 未指定图层且边界实体也没带层名时用这个。
DEFAULT_LAYER = "HATCH"

#: 本席记录表在草图 dict / 文档 ``extra`` 里的键名（只加键不改键，与族模块的 ``hatch_measure`` 分开）。
_TABLE_KEY = "hatch_measure__w19"
_SCHEMA = 1

#: 渐变名（**冻结枚举**，大小写敏感）；``HELIX`` 在 ``_UNSUPPORTED_GRADIENTS`` 里显式拒绝。
_GRADIENT_NAMES: tuple[str, ...] = ("LINEAR", "CYLINDER", "SPHERICAL", "HELIX")
_UNSUPPORTED_GRADIENTS = ("HELIX",)

#: 圆/圆弧边界离散段数与等半径采样段数的缺省值（固定值 ⇒ 同一张图两次跑逐位一致）。
_CIRCLE_SEGMENTS = 64

#: ``bands`` 上限：超过就是"用密集线冒充渐变"，本层拒绝。
_MAX_BANDS = 64
_MAX_ENTITIES = 20000
_MIN_SEGMENTS = 16
_MAX_SEGMENTS = 4096

#: L1 实体字段白名单（自检用；本文件只写这五个键）。
_L1_KEYS = ("uuid", "kind", "layer", "construction", "geom")


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """统一错误构造：机器可读的 ``reason`` 是归因入口。"""
    return CadError(message, reason=reason, **details)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# ======================================================================================
# 入参把关（本文件自持；只复用签名已确证的 _round_nm）
# ======================================================================================

def _as_gradient(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in _GRADIENT_NAMES:
        raise _fail("unknown_gradient", "gradient_name is not in the frozen enumeration",
                    gradient_name=repr(value), known=list(_GRADIENT_NAMES))
    return value.strip()


def _as_colors(value: Any) -> tuple[list[str], int]:
    """``None`` = 单色坡道；``(起点, 终点)`` = 双色坡道。恰好一端为 None ⇒ ``invalid_colors``。"""
    if value is None:
        return ["BYLAYER", "BYLAYER"], 0
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_colors", "colors must be None (single ramp) or a (start, end) pair",
                    value=repr(value))
    first, second = value[0], value[1]
    if (first is None) != (second is None):
        raise _fail("invalid_colors",
                    "a two-colour ramp needs both stops; pass colors=None for a single ramp",
                    value=repr(value))
    if first is None:
        return ["BYLAYER", "BYLAYER"], 0
    for stop in (first, second):
        if not isinstance(stop, str) or not stop.strip():
            raise _fail("invalid_colors", "colour stops must be non-empty strings", value=repr(value))
    return [first.strip(), second.strip()], 1


def _as_angle_deg(value: Any) -> float:
    if not _is_num(value):
        raise _fail("invalid_argument", "angle_deg must be a finite number", value=repr(value))
    return float(value)


def _as_bands(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "bands must be an integer", value=repr(value))
    if value < 2:
        raise _fail("invalid_argument", "bands must be >= 2 (a ramp with one band has no contour)",
                    value=int(value))
    if value > _MAX_BANDS:
        raise _fail("too_many_bands",
                    "bands exceeds the sparse-contour limit; dense lines masquerading as a gradient are refused",
                    bands=int(value), max_bands=_MAX_BANDS)
    return int(value)


def _as_flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _fail("invalid_argument", "%s must be a boolean" % what, value=repr(value))
    return bool(value)


def _as_origin(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_argument", "origin must be a (x_nm, y_nm) pair", value=repr(value))
    if not (_is_num(value[0]) and _is_num(value[1])):
        raise _fail("invalid_argument", "origin coordinates must be finite numbers in nanometres",
                    value=repr(value))
    return (float(value[0]), float(value[1]))


def _as_layer(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "layer must be a non-empty layer name", value=repr(value))
    return value.strip()


def _as_tol(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "tol_nm must be an integer number of nanometres", value=repr(value))
    if value < 0:
        raise _fail("invalid_length", "tol_nm must not be negative", tol_nm=int(value))
    return int(value)


def _as_segments(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "arc_segments must be an integer", value=repr(value))
    if value < _MIN_SEGMENTS or value > _MAX_SEGMENTS:
        raise _fail("invalid_argument", "arc_segments is out of range",
                    value=int(value), min_segments=_MIN_SEGMENTS, max_segments=_MAX_SEGMENTS)
    return int(value)


def _as_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 16:
        raise _fail("invalid_argument", "max_entities must be an integer >= 16", value=repr(value))
    return int(value)


# ======================================================================================
# L1 句柄与边界选择
# ======================================================================================

def _entity_list(sk_or_doc: Any) -> list:
    """取 L1 实体列表；顺序写在 docstring 里，不猜 Document 内部结构。"""
    if isinstance(sk_or_doc, list):
        return sk_or_doc
    if isinstance(sk_or_doc, dict):
        entities = sk_or_doc.get("entities")
        if entities is None:
            entities = sk_or_doc.setdefault("entities", [])
        if not isinstance(entities, list):
            raise _fail("invalid_document", "sketch['entities'] must be a list",
                        value=repr(type(entities).__name__))
        return entities
    entities = getattr(sk_or_doc, "entities", None)
    if isinstance(entities, list):
        return entities
    sketch = getattr(sk_or_doc, "sketch", None)
    if sketch is not None and sketch is not sk_or_doc:
        inner = getattr(sketch, "entities", None)
        if isinstance(inner, list):
            return inner
    try:
        found = sketch_entities(sk_or_doc)
    except Exception as exc:  # 定位失败必须显式报错，不许当"空图"继续跑
        raise _fail("invalid_document", "cannot locate the L1 entity list on this handle",
                    handle=type(sk_or_doc).__name__, error=type(exc).__name__) from None
    if not isinstance(found, list):
        raise _fail("invalid_document", "sketch_entities() did not return a list",
                    value=repr(type(found).__name__))
    return found


def _boundary_entities(entities: list, boundary: Any) -> tuple[list, bool]:
    """返回 ``(参与成环的实体, 是否显式指定)``。空序列报错（"没筛"与"筛出空"不同形）。"""
    if boundary is None:
        return list(entities), False
    if isinstance(boundary, str):
        wanted = [boundary]
    elif isinstance(boundary, (list, tuple)):
        wanted = list(boundary)
    else:
        raise _fail("invalid_argument", "boundary must be None, a uuid, or a sequence of uuids",
                    value=repr(boundary))
    if not wanted:
        raise _fail("invalid_argument", "boundary sequence is empty; pass boundary=None to auto-detect",
                    value=repr(boundary))
    by_uuid = {}
    for entity in entities:
        if isinstance(entity, dict):
            uid = entity.get("uuid")
            if isinstance(uid, str):
                by_uuid[uid] = entity
    selected = []
    for item in wanted:
        if not isinstance(item, str):
            raise _fail("invalid_argument", "boundary members must be uuid strings", value=repr(item))
        entity = by_uuid.get(item)
        if entity is None:
            raise _fail("unknown_entity", "boundary uuid is not in the document", uuid=item)
        selected.append(entity)
    return selected, True


# ======================================================================================
# 几何：成环 / 奇偶判定 / 面积 / 裁剪
# ======================================================================================

def _same_point(a: tuple, b: tuple, tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _inside(x: float, y: float, loops: list) -> bool:
    """偶奇规则：点是否在若干闭合环围成的区域**内**（洞与外环一起判，奇数次穿越 = 内）。"""
    crossings = 0
    for poly in loops:
        count = len(poly)
        for index in range(count):
            x1, y1 = poly[index]
            x2, y2 = poly[(index + 1) % count]
            if (y1 <= y < y2) or (y2 <= y < y1):
                xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if xin > x:
                    crossings += 1
    return crossings % 2 == 1


def _shoelace(poly: list) -> float:
    total = 0.0
    count = len(poly)
    for index in range(count):
        x1, y1 = poly[index]
        x2, y2 = poly[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return 0.5 * total


def _poly_area(poly: list) -> float:
    return abs(_shoelace(poly))


def _polyline_points(geom: dict) -> list:
    """``geom["points"]`` → 点列表；接受 ``[[x,y], ...]`` 与 ``[x,y,x,y, ...]`` 两种冻结形态。"""
    raw = geom.get("points")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise _fail("unsupported_boundary", "polyline needs a points list with at least two points",
                    value=repr(type(raw).__name__))
    points = []
    pairs = all(isinstance(item, (list, tuple)) and len(item) >= 2 and _is_num(item[0]) and _is_num(item[1])
                for item in raw)
    if pairs:
        points = [(float(item[0]), float(item[1])) for item in raw]
    elif all(_is_num(item) for item in raw):
        if len(raw) % 2:
            raise _fail("unsupported_boundary", "flat polyline points must have an even length",
                        length=len(raw))
        points = [(float(raw[i]), float(raw[i + 1])) for i in range(0, len(raw), 2)]
    else:
        raise _fail("unsupported_boundary", "polyline points must be pairs or a flat number list",
                    value=repr(type(raw).__name__))
    return points


def _line_points(geom: dict) -> list:
    keys = ("x1", "y1", "x2", "y2")
    if not all(_is_num(geom.get(key)) for key in keys):
        raise _fail("unsupported_boundary", "line needs numeric x1/y1/x2/y2", value=repr(sorted(geom)))
    return [(float(geom["x1"]), float(geom["y1"])), (float(geom["x2"]), float(geom["y2"]))]


def _circle_points(geom: dict, segments: int) -> list:
    radius = geom.get("r", geom.get("radius"))
    if not _is_num(radius) or float(radius) <= 0.0:
        raise _fail("unsupported_boundary", "circle needs a positive radius field (r / radius)",
                    value=repr(geom.get("r", geom.get("radius"))))
    cx, cy = geom.get("cx"), geom.get("cy")
    if not (_is_num(cx) and _is_num(cy)):
        center = geom.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 2 and _is_num(center[0]) and _is_num(center[1]):
            cx, cy = float(center[0]), float(center[1])
        elif isinstance(center, dict) and _is_num(center.get("x")) and _is_num(center.get("y")):
            cx, cy = float(center["x"]), float(center["y"])
        else:
            raise _fail("unsupported_boundary", "circle needs a centre (cx/cy or center)")
    r = float(radius)
    return [(float(cx) + r * math.cos(2.0 * math.pi * i / segments),
             float(cy) + r * math.sin(2.0 * math.pi * i / segments)) for i in range(segments)]


def _chain_loops(chains: list, tol: float) -> list:
    """把开放折线/直线按端点（容差 ``tol`` 纳米）串成闭合环；串不上的链**不猜**，直接丢掉。"""
    loops = []
    pool = [list(chain) for chain in chains if len(chain) >= 2]
    while pool:
        current = pool.pop(0)
        changed = True
        while changed:
            changed = False
            for index, segment in enumerate(pool):
                if _same_point(current[-1], segment[0], tol):
                    current = current + segment[1:]
                elif _same_point(current[-1], segment[-1], tol):
                    current = current + list(reversed(segment))[1:]
                elif _same_point(current[0], segment[-1], tol):
                    current = segment[:-1] + current
                elif _same_point(current[0], segment[0], tol):
                    current = list(reversed(segment))[:-1] + current
                else:
                    continue
                pool.pop(index)
                changed = True
                break
        if len(current) >= 4 and _same_point(current[0], current[-1], tol):
            closed = current[:-1]
            if len(closed) >= 3:
                loops.append(closed)
    return loops


def _collect_loops(entities: list, tol_nm: int, segments: int, explicit: bool) -> tuple[list, dict]:
    """实体 → 闭合环列表（洞与孤岛都是独立环，归属由偶奇规则决定）+ 被跳过的实体种类计数。"""
    loops = []
    chains = []
    skipped: dict[str, int] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            raise _fail("invalid_document", "L1 entities must be mappings",
                        value=repr(type(entity).__name__))
        kind = entity.get("kind")
        geom = entity.get("geom")
        if not isinstance(geom, dict):
            raise _fail("unsupported_boundary", "entity has no geom mapping",
                        kind=repr(kind), uuid=repr(entity.get("uuid")))
        if kind == "circle":
            loops.append(_circle_points(geom, segments))
        elif kind == "polyline":
            points = _polyline_points(geom)
            if len(points) >= 4 and _same_point(points[0], points[-1], tol_nm):
                loops.append(points[:-1])
            else:
                chains.append(points)
        elif kind == "line":
            chains.append(_line_points(geom))
        else:
            if explicit:
                raise _fail("unsupported_boundary",
                            "this entity kind cannot form a gradient boundary in this layer",
                            kind=repr(kind), uuid=repr(entity.get("uuid")))
            key = str(kind)
            skipped[key] = skipped.get(key, 0) + 1
    loops.extend(_chain_loops(chains, tol_nm))
    return [loop for loop in loops if len(loop) >= 3], skipped


def _nesting_signs(loops: list) -> list:
    """按嵌套深度定符号：偶数层 +（外环/孤岛），奇数层 −（洞）⇒ 净面积 = 偶奇面积。"""
    signs = []
    for index, loop in enumerate(loops):
        probe = loop[0]
        depth = 0
        for other_index, other in enumerate(loops):
            if other_index != index and _inside(probe[0], probe[1], [other]):
                depth += 1
        signs.append(1 if depth % 2 == 0 else -1)
    return signs


def _clip_slab(poly: list, lo: float, hi: float) -> list:
    """Sutherland–Hodgman：把 (s,t) 平面的环裁到 ``lo <= t <= hi``（凸 slab，精确裁剪）。"""
    def _cut(a: tuple, b: tuple, level: float) -> tuple:
        span = b[1] - a[1]
        if span == 0.0:
            return (b[0], float(level))
        ratio = (level - a[1]) / span
        return (a[0] + (b[0] - a[0]) * ratio, float(level))

    def _pass(points: list, keep: Any, level: float) -> list:
        if not points:
            return []
        out = []
        prev = points[-1]
        prev_inside = keep(prev)
        for cur in points:
            cur_inside = keep(cur)
            if cur_inside:
                if not prev_inside:
                    out.append(_cut(prev, cur, level))
                out.append(cur)
            elif prev_inside:
                out.append(_cut(prev, cur, level))
            prev, prev_inside = cur, cur_inside
        return out

    points = [(float(p[0]), float(p[1])) for p in poly]
    points = _pass(points, lambda p: p[1] >= lo, lo)
    return _pass(points, lambda p: p[1] <= hi, hi)


def _linear_segments(frame: list, level: float) -> list:
    """(s,t) 平面环集在 ``t = level`` 上的偶奇配对 → ``[(s_a, s_b), ...]``（洞与凹形一起处理）。"""
    hits = []
    for poly in frame:
        count = len(poly)
        for index in range(count):
            s1, t1 = poly[index]
            s2, t2 = poly[(index + 1) % count]
            if (t1 <= level < t2) or (t2 <= level < t1):
                hits.append(s1 + (level - t1) * (s2 - s1) / (t2 - t1))
    hits.sort()
    pieces = []
    for index in range(0, len(hits) - 1, 2):
        s_a, s_b = hits[index], hits[index + 1]
        if s_b - s_a > 1e-9:
            pieces.append((s_a, s_b))
    return pieces


def _radial_segments(loops: list, center: tuple, radius: float, segments: int) -> list:
    """等半径圆弧在区域内的可见段：按 ``segments`` 步采样，连续内侧样本连成线段。"""
    cx, cy = center
    points = []
    inside_flags = []
    for index in range(segments):
        theta = 2.0 * math.pi * index / segments
        point = (cx + radius * math.cos(theta), cy + radius * math.sin(theta))
        points.append(point)
        inside_flags.append(_inside(point[0], point[1], loops))
    pieces = []
    if all(inside_flags):
        for index in range(segments):
            pieces.append((points[index], points[(index + 1) % segments]))
        return pieces
    if not any(inside_flags):
        return []
    start = inside_flags.index(False)
    run = []
    for step in range(1, segments + 1):
        index = (start + step) % segments
        if inside_flags[index]:
            run.append(points[index])
        else:
            for offset in range(len(run) - 1):
                pieces.append((run[offset], run[offset + 1]))
            run = []
    for offset in range(len(run) - 1):
        pieces.append((run[offset], run[offset + 1]))
    return pieces


def _bbox_center(loops: list) -> tuple:
    xs = [p[0] for loop in loops for p in loop]
    ys = [p[1] for loop in loops for p in loop]
    return (0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)))


def _ramp_geometry(bands: int, centered: bool) -> tuple[list, list]:
    """坡道分带 → ``(levels_u, band_spans)``。

    ``levels_u`` = 相邻坡道段之间的等值位置（0/1 不在内）；``band_spans`` = 每个坡道段对应的
    ``u`` 区间列表。``centered=True`` 时坡道是"起点→终点→起点"的镜像，每个坡道段可能对应两段 ``u``。
    """
    bounds = [index / bands for index in range(bands + 1)]
    spans = []
    for index in range(bands):
        v0, v1 = bounds[index], bounds[index + 1]
        if not centered:
            spans.append([(v0, v1)])
            continue
        parts = []
        first = (v0 / 2.0, v1 / 2.0)
        if first[1] > first[0]:
            parts.append(first)
        second = (1.0 - v1 / 2.0, 1.0 - v0 / 2.0)
        if second[1] > second[0]:
            parts.append(second)
        spans.append(parts)
    positions = set()
    for parts in spans:
        for (u0, u1) in parts:
            if 0.0 < u0 < 1.0:
                positions.add(round(u0, 15))
            if 0.0 < u1 < 1.0:
                positions.add(round(u1, 15))
    return sorted(positions), spans


def _inherit_layer(entities: list) -> str:
    for entity in entities:
        if isinstance(entity, dict):
            name = entity.get("layer")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return DEFAULT_LAYER


def _emit_world_segments(entities: list, pieces: list, to_world: Any, layer: str,
                         created: list, limit: int) -> None:
    for (a, b) in pieces:
        p1 = to_world(a)
        p2 = to_world(b)
        x1, y1 = _round_nm(p1[0]), _round_nm(p1[1])
        x2, y2 = _round_nm(p2[0]), _round_nm(p2[1])
        if (int(x1), int(y1)) == (int(x2), int(y2)):  # 取整后退化成一个点 ⇒ 丢弃
            continue
        if len(created) >= limit:
            raise _fail("too_many_entities",
                        "gradient contours would exceed max_entities; lower bands or arc_segments",
                        max_entities=int(limit), created=len(created))
        uid = new_uuid()
        entities.append({
            "uuid": uid,
            "kind": "line",
            "layer": layer,
            "construction": False,
            "geom": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)},
        })
        created.append(uid)


def _store_record(sk_or_doc: Any, record: dict, record_into: Any) -> int:
    """把渐变记录追加进 ``hatch_measure__w19`` 表（只加键不改键）；找不到落点就不落，不假装落过。"""
    target = None
    if isinstance(record_into, dict):
        target = record_into
    elif isinstance(sk_or_doc, dict):
        target = sk_or_doc
    else:
        extra = getattr(sk_or_doc, "extra", None)
        if isinstance(extra, dict):
            target = extra
    if target is None:
        return 0
    table = target.get(_TABLE_KEY)
    if not isinstance(table, dict):
        table = {"schema": _SCHEMA, "gradients": []}
        target[_TABLE_KEY] = table
    gradients = table.get("gradients")
    if not isinstance(gradients, list):
        gradients = []
        table["gradients"] = gradients
    gradients.append(record)
    return 1


# ======================================================================================
# 操作入口
# ======================================================================================

def gradient_bands(sk_or_doc: Any, *, boundary: Any = None, gradient_name: Any = "LINEAR",
                   colors: Any = None, angle_deg: Any = 0.0, bands: Any = 8, centered: Any = False,
                   origin: Any = None, layer: Any = None, tol_nm: Any = None,
                   arc_segments: Any = _CIRCLE_SEGMENTS, max_entities: Any = _MAX_ENTITIES,
                   record_into: Any = None) -> list[str]:
    """渐变填充的 L1 可达后果：真实等值线几何 + 真实分带面积（返回新建实体 uuid，按坡道顺序）。

    kwargs：``boundary``（None = 自动识别闭合环；uuid 或其序列 = 只用这些实体成环）、
    ``gradient_name``（``LINEAR``/``CYLINDER``/``SPHERICAL``；``HELIX`` ⇒ ``unsupported_gradient``）、
    ``colors``（None = 单色坡道；``(起点, 终点)`` = 双色；恰好一端为 None ⇒ ``invalid_colors``）、
    ``angle_deg``（LINEAR 的梯度轴方向）、``bands``（坡道分带数，2..64）、``centered``（镜像坡道）、
    ``origin``（CYLINDER/SPHERICAL 的圆心；None = 区域包围盒中心）、``layer``（None = 继承边界实体的层）、
    ``tol_nm``（成环端点配对容差；None = 0 = 严格相等）、``arc_segments``（圆环离散/半径采样段数）、
    ``max_entities``（悬空保护：超出报 ``too_many_entities``，不静默截成半张图）、
    ``record_into``（记录落点 mapping；缺省时 dict 草图落自身、对象草图落 ``.extra``）。

    失败信号：``unknown_gradient`` / ``unsupported_gradient`` / ``invalid_colors`` / ``invalid_argument`` /
    ``too_many_bands`` / ``invalid_length`` / ``invalid_document`` / ``unknown_entity`` /
    ``unsupported_boundary`` / ``no_boundary`` / ``degenerate_extent`` / ``too_many_entities`` /
    ``no_gradient_geometry``。
    """
    name = _as_gradient(gradient_name)
    if name in _UNSUPPORTED_GRADIENTS:
        raise _fail("unsupported_gradient",
                    "this gradient sweeps a phase that L1 entities cannot carry (no per-vertex field)",
                    gradient_name=name,
                    supported=[item for item in _GRADIENT_NAMES if item not in _UNSUPPORTED_GRADIENTS])
    palette, two_color = _as_colors(colors)
    angle = _as_angle_deg(angle_deg)
    band_count = _as_bands(bands)
    centered_flag = _as_flag(centered, "centered")
    origin_point = _as_origin(origin)
    layer_name = _as_layer(layer)
    tolerance = _as_tol(tol_nm)
    segments = _as_segments(arc_segments)
    limit = _as_limit(max_entities)

    entities = _entity_list(sk_or_doc)
    subset, explicit = _boundary_entities(entities, boundary)
    loops, skipped = _collect_loops(subset, tolerance, segments, explicit)
    if not loops:
        raise _fail("no_boundary", "no closed loop found for the gradient; close the boundary or pass boundary=",
                    entities=len(subset), tol_nm=int(tolerance))

    signs = _nesting_signs(loops)
    net_area = int(_round_nm(sum(sign * _poly_area(loop) for sign, loop in zip(signs, loops))))
    levels_u, band_spans = _ramp_geometry(band_count, centered_flag)
    out_layer = layer_name or _inherit_layer(subset)

    created: list[str] = []
    empty_levels: list[float] = []
    band_areas: list[int] | None = None
    center_nm = None
    center_source = None

    if name == "LINEAR":
        dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        nx, ny = -dy, dx
        frame = [[(p[0] * nx + p[1] * ny, p[0] * dx + p[1] * dy) for p in loop] for loop in loops]
        extent = [p[1] for poly in frame for p in poly]
        lo, hi = min(extent), max(extent)
        if hi - lo <= 0.0:
            raise _fail("degenerate_extent", "the region has no extent along the gradient axis",
                        angle_deg=float(angle % 360.0))
        for u in levels_u:
            level = lo + (hi - lo) * u
            pieces = _linear_segments(frame, level)
            if not pieces:
                empty_levels.append(float(u))
                continue
            _emit_world_segments(entities,
                                 [((s_a, level), (s_b, level)) for (s_a, s_b) in pieces],
                                 lambda p, dx=dx, dy=dy, nx=nx, ny=ny: (p[0] * nx + p[1] * dx,
                                                                          p[0] * ny + p[1] * dy),
                                 out_layer, created, limit)
        band_areas = []
        for parts in band_spans:
            total = 0.0
            for (u0, u1) in parts:
                t0 = lo + (hi - lo) * u0
                t1 = lo + (hi - lo) * u1
                for sign, poly in zip(signs, frame):
                    total += sign * _poly_area(_clip_slab(poly, t0, t1))
            band_areas.append(int(_round_nm(total)))
    else:
        if origin_point is None:
            center = _bbox_center(loops)
            center_source = "bbox"
        else:
            center = origin_point
            center_source = "origin"
        cx, cy = center
        radii = [math.hypot(p[0] - cx, p[1] - cy) for loop in loops for p in loop]
        r_max = max(radii)
        r_min = 0.0 if _inside(cx, cy, loops) else min(radii)
        if r_max - r_min <= 0.0:
            raise _fail("degenerate_extent", "the region has no radial extent around the gradient centre",
                        center_nm=[int(round(cx)), int(round(cy))])
        for u in levels_u:
            radius = r_min + (r_max - r_min) * u
            pieces = _radial_segments(loops, center, radius, segments)
            if not pieces:
                empty_levels.append(float(u))
                continue
            _emit_world_segments(entities, pieces, lambda p: p, out_layer, created, limit)
        center_nm = [int(round(cx)), int(round(cy))]

    if not created:
        raise _fail("no_gradient_geometry",
                    "no gradient contour fell inside the region; check boundary/origin/angle",
                    gradient_name=name, bands=int(band_count))

    record = {
        "uuid": new_uuid(),
        "kind": "gradient_bands",
        "schema": _SCHEMA,
        "gradient_name": name,
        "colors": palette,
        "two_color": int(two_color),
        "centered": bool(centered_flag),
        "angle_deg": float(angle % 360.0),
        "bands": int(band_count),
        "layer": out_layer,
        "levels_u": [float(u) for u in levels_u],
        "entities": list(created),
        "geometry": "isocontour_level_sets",
        "color_source": "downstream_ramp",
        "band_areas_nm2": band_areas,
        "net_area_nm2": int(net_area),
        "skipped_kinds": dict(skipped),
        "empty_levels": empty_levels,
    }
    if name != "LINEAR":
        record["center_nm"] = center_nm
        record["center_source"] = center_source
        record["arc_segments"] = int(segments)
    _store_record(sk_or_doc, record, record_into)
    return list(created)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 渐变色填充：L1 无颜色位 ⇒ 交付等值线几何 + 分带面积（不是声明，是真几何）
    "gradient_bands": gradient_bands,
}


# ======================================================================================
# 自检（`python3 cadcore/ops/features/hatch_measure__w19.py`）
# ======================================================================================

def selftest() -> int:
    """硬断言自检：几何真有后果、洞真被裁、面积真守恒、边界真没被动过。失败即 rc=1。"""
    counter = {"n": 0}

    def check(condition: Any, label: str, **details: Any) -> None:
        counter["n"] += 1
        if not condition:
            print("HATCH_MEASURE_W19_SELFTEST verdict=FAIL check=%s details=%r" % (label, details))
            raise SystemExit(1)

    mm = 1_000_000
    outer_pts = [0, 0, 100 * mm, 0, 100 * mm, 100 * mm, 0, 100 * mm, 0, 0]  # 扁平形态
    hole_pts = [[40 * mm, 40 * mm], [60 * mm, 40 * mm], [60 * mm, 60 * mm],
                [40 * mm, 60 * mm], [40 * mm, 40 * mm]]                      # 点对形态
    outer = {"uuid": "u-outer", "kind": "polyline", "layer": "BOUND", "construction": False,
             "geom": {"points": outer_pts}}
    hole = {"uuid": "u-hole", "kind": "polyline", "layer": "BOUND", "construction": False,
            "geom": {"points": hole_pts}}
    open_line = {"uuid": "u-open", "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 200 * mm, "y1": 0, "x2": 250 * mm, "y2": 0}}
    sketch = {"entities": [outer, hole, open_line]}
    boundary_before = repr([outer, hole])

    uuids = gradient_bands(sketch, gradient_name="LINEAR", angle_deg=0.0, bands=4)
    record = sketch["hatch_measure__w19"]["gradients"][-1]
    by_uuid = {entity["uuid"]: entity for entity in sketch["entities"]}

    # 1) 洞真被裁：4 段（x=25/75 各 1 段，x=50 被洞切成 2 段），不是"每层 1 段"的 3 段
    check(len(uuids) == 4, "linear_hole_split", created=len(uuids))
    check(record["entities"] == uuids and len(set(uuids)) == 4, "returned_uuids_are_created_entities",
          n=len(uuids))
    # 2) 每条线段中点真在区域内、且不在洞里（洞被忽略时这条必红）
    hole_poly = _polyline_points(hole["geom"])
    outer_poly = _polyline_points(outer["geom"])
    for uid in uuids:
        geom = by_uuid[uid]["geom"]
        mx = 0.5 * (geom["x1"] + geom["x2"])
        my = 0.5 * (geom["y1"] + geom["y2"])
        check(_inside(mx, my, [outer_poly]), "segment_midpoint_inside_region", uuid=uid)
        check(not _inside(mx, my, [hole_poly]), "segment_midpoint_avoids_hole", uuid=uid)
        check((geom["x1"], geom["y1"]) != (geom["x2"], geom["y2"]), "segment_not_degenerate", uuid=uid)
    # 3) 等值线沿梯度轴单调（x 25 < 50 < 50 < 75）
    axis = sorted(by_uuid[uid]["geom"]["x1"] for uid in uuids)
    check(axis == [25 * mm, 50 * mm, 50 * mm, 75 * mm], "contours_monotone_along_axis", axis=axis)
    # 4) 分带面积守恒：4 带 = 2500/2300/2300/2500 mm²，和 = 净面积 = 9600 mm²
    areas = record["band_areas_nm2"]
    expected = [2500 * 10 ** 12, 2300 * 10 ** 12, 2300 * 10 ** 12, 2500 * 10 ** 12]
    check(len(areas) == 4 and len(record["levels_u"]) == 3, "bands_and_levels",
          bands=len(areas), levels=len(record["levels_u"]))
    for got, want in zip(areas, expected):
        check(abs(got - want) <= 10 ** 4, "band_area_matches_geometry", got=got, want=want)
    check(abs(sum(areas) - 9600 * 10 ** 12) <= 10 ** 4, "band_areas_conserve_net_area",
          total=sum(areas), net=record["net_area_nm2"])
    check(record["net_area_nm2"] == 9600 * 10 ** 12, "net_area_honours_hole", net=record["net_area_nm2"])
    # 5) 边界一个纳米都没动 + 返回实体是合法 L1 形态（白名单五键、整数纳米）
    check(repr([outer, hole]) == boundary_before, "boundary_untouched")
    check(len(sketch["entities"]) == 3 + 4, "only_new_entities_appended", n=len(sketch["entities"]))
    for uid in uuids:
        entity = by_uuid[uid]
        check(set(entity) == set(_L1_KEYS), "l1_field_whitelist", keys=sorted(entity))
        check(entity["kind"] == "line" and entity["layer"] == "BOUND" and entity["construction"] is False,
              "l1_kind_layer_inherited", uuid=uid)
        check(all(isinstance(entity["geom"][key], int) for key in ("x1", "y1", "x2", "y2")),
              "l1_integer_nanometres", uuid=uid)

    # 6) 径向渐变：圆边界 r=50mm ⇒ 3 条等半径圆环 × 64 段，端点半径真等于等值半径（±2 nm 取整误差）
    circle = {"uuid": "c-1", "kind": "circle", "layer": "0", "construction": False,
              "geom": {"center": {"x": 0, "y": 0}, "radius": 50 * mm}}
    radial_sketch = {"entities": [circle]}
    radial = gradient_bands(radial_sketch, gradient_name="SPHERICAL", bands=4, arc_segments=64)
    radial_record = radial_sketch["hatch_measure__w19"]["gradients"][-1]
    check(len(radial) == 3 * 64, "radial_level_count", created=len(radial))
    check(radial_record["center_nm"] == [0, 0] and radial_record["center_source"] == "bbox",
          "radial_bbox_center", center=radial_record["center_nm"])
    for level_index, radius_nm in enumerate([12.5 * mm, 25 * mm, 37.5 * mm]):
        block = radial[level_index * 64:(level_index + 1) * 64]
        worst = 0.0
        for uid in block:
            geom = {e["uuid"]: e for e in radial_sketch["entities"]}[uid]["geom"]
            for (x, y) in ((geom["x1"], geom["y1"]), (geom["x2"], geom["y2"])):
                worst = max(worst, abs(math.hypot(x, y) - radius_nm))
        check(worst <= 2.0, "radial_arc_radius_matches_level", level=level_index, worst_nm=worst)

    # 7) centered=True 的镜像坡道：等值线翻倍且分带面积仍然守恒
    centered_sketch = {"entities": [dict(outer), dict(hole)]}
    centered_uuids = gradient_bands(centered_sketch, gradient_name="LINEAR", bands=4, centered=True)
    centered_record = centered_sketch["hatch_measure__w19"]["gradients"][-1]
    check(len(centered_record["levels_u"]) == 6, "centered_ramp_mirrors_levels",
          levels=len(centered_record["levels_u"]))
    check(abs(sum(centered_record["band_areas_nm2"]) - 9600 * 10 ** 12) <= 10 ** 4,
          "centered_band_areas_conserve", total=sum(centered_record["band_areas_nm2"]),
          created=len(centered_uuids))

    # 8) 失败面：HELIX 显式不支持、bands 非法、开放链不成环、显式边界里夹不支持种类
    for kwargs, reason, label in (
        ({"gradient_name": "HELIX"}, "unsupported_gradient", "helix_is_explicitly_unsupported"),
        ({"bands": 1}, "invalid_argument", "bands_below_two_refused"),
        ({"boundary": ["u-open"]}, "no_boundary", "open_chain_is_not_a_boundary"),
        ({"boundary": []}, "invalid_argument", "empty_boundary_selection_refused"),
        ({"bands": 65}, "too_many_bands", "dense_band_fake_fill_refused"),
    ):
        try:
            gradient_bands({"entities": [dict(outer), dict(hole), dict(open_line)]}, **kwargs)
        except CadError as exc:
            check(exc.reason == reason, label, reason=exc.reason, want=reason)
        else:
            check(False, label, reason="no_error_raised", want=reason)
    try:
        gradient_bands({"entities": [dict(outer), {"uuid": "a-1", "kind": "arc", "layer": "0",
                                                   "construction": False,
                                                   "geom": {"cx": 0, "cy": 0, "r": mm}}]},
                       boundary=["u-outer", "a-1"])
    except CadError as exc:
        check(exc.reason == "unsupported_boundary", "explicit_unsupported_kind_refused", reason=exc.reason)
    else:
        check(False, "explicit_unsupported_kind_refused", reason="no_error_raised")

    print("HATCH_MEASURE_W19_SELFTEST verdict=PASS assertions=%d" % counter["n"])
    print("FEATURE_OK family=%s op=%s verdict=PASS assertions=%d bands=4 contours=3 radial_entities=%d "
          "net_area_nm2=%d band_area_sum_nm2=%d unsupported=HELIX,color_on_entity"
          % (FAMILY, "gradient_bands", counter["n"], len(radial), record["net_area_nm2"], sum(areas)))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
