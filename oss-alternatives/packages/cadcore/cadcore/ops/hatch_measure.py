#!/usr/bin/env python3
"""cadcore.ops.hatch_measure —— 填充与查询族模块：图案填充 / 边界识别 / 面积周长距离测量 / 特性查询。

一句话用途
----------
把"看图取数"的两件事做成 L1 文档/草图上的**可复用操作**：①图案填充（线型 / 角度 / 间距 / 边界，
含边界自动识别）；②测量与特性查询（面积、周长、距离、实体特性）。这是 `cadcore.ops.<module>` 家族模块之一。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "hatch_measure"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数（含少量**别名**，别名与主名指向同一函数）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``。**写操作**返回**受影响实体的 uuid 列表**
  （顺序在各自 docstring 里写清）；**查询/测量操作是只读的、不建表不写入**，返回 ``"key=value"``
  **结果 token** 列表（同样逐 op 写清 token 名与顺序）。
- 长度一律**整数纳米（nm）**（面积 nm²、体积 nm³）；错误一律抛 ``cadcore.errors.CadError(reason=...)``，
  `reason` 是机器可读的归因入口（本文件不新增异常类型；复用 `edit/modify` 的校验器时其
  `CadError` 子类即 `cadcore.errors.CadError`）。

口径（与全仓一致，别自己发明）
------------------------------
- ``0 = 真为零``：例如 ``hole_area_nm2=0`` = 这个区域真的没有洞；``[]`` = 真的一个闭合轮廓都没找到。
- ``-1 = 未采``：本层**有意不采**的量。例：`prop_query` 的 ``z_nm=-1``（二维草图不采 Z）。
- ``NA = 不适用``：问法对该实体不成立。例：line 的 ``area_nm2=NA``（直线没有面积）、
  空草图 ``measure_area`` 的 ``area_nm2=NA``（没有闭合轮廓，面积不适用）。
- 三种状态**不许同形**：0 与 -1 与 NA 是三个不同的 token 值，下游据此区分"真为零 / 没采 / 不适用"。

落盘形态与有意限定的边界（写下来，防止下游靠猜）
------------------------------------------------
1. **L1 没有 `hatch` 实体种类**（冻结集只有 line/arc/circle/polyline）⇒ 本层**不假装**有填充实体：
   图案按线型/角度/间距**切出真实线段**，落成普通 L1 ``line`` 实体（可直接被 draw / 投影链路消费），
   同时把填充定义（边界 uuid、图案、角度、间距、线型、生成的线 uuid）记进表：
   目标是文档 ⇒ ``doc.extra["hatch_measure"]``；目标是草图 ⇒ ``sk["hatch_measure"]``。
   表结构 ``{"schema": 1, "hatches": []}``，**只加键不改键**。
2. **`solid` 实心填充 ⇒ `CadError(reason="unsupported_pattern")`**：实心面需要面实体，L1 没有；
   用密集线冒充实心是静默错图，宁可显式报不支持（`_PATTERNS` 是冻结枚举，未知图案同样报错）。
3. **非 continuous 线型是把虚线段落成多条 `line` 实体**（L1 实体没有 linetype 字段，线型在图层表上）。
   本文件不手改 `line` 的字段集，也不动图层表 ⇒ `docmodel.check_invariants()` 不被污染。
4. **边界识别只做"首尾相接成环"**：端点按整数纳米（可选 ``tol_nm`` 网格吸附）配成环；
   闭合的 `circle` 与首末点重合的 `polyline` 自成一环。**不闭合的链不是边界**（不猜、不自动补线），
   所以 `boundary_detect` 对开放链返回 `[]`（真为零），`measure_area` 返回 `NA`。
5. **填充不修改边界实体一个纳米**：只追加图案线段；`hatch_delete` / `hatch_update` 只删本层生成的
   图案线，边界几何永远不动。
6. **悬空保护的 `max_lines`**：图案线段数超过上限 ⇒ `CadError(reason="too_many_lines")`
   （不静默截断成"半张填充图"）。
7. **测量是解析式优先**：圆用 πr² / 2πr，圆弧用 r·sweep，折线/直线用精确端点；圆与圆弧的**面积/周长
   不数离散化多边形**（离散点只用于成环判据与距离采样），距离采样段数由 `_CIRCLE_SEGMENTS` 固定。
8. 复用而非重写：入参把关复用 `cadcore.edit.modify` 的 `_as_nm` / `_as_angle` / `_as_factor` / `_round_nm`，
   结构访问走 `cadcore.sketch.constraints` 的 `_get` / `sketch_entities` / `new_uuid`。本文件不抄第二份校验器。
9. **没有 `ops/__init__.py`**（族模块靠命名空间包被发现）；直跑本文件时用下面的引导自举 `__package__`。
10. **渐变色填充（GRADIENT）在 L1 上只有"声明形态"**：L1 实体的字段白名单是
    `{uuid, kind, layer, construction, geom}`（`cadcore.docmodel` 的字段表），**没有颜色/着色字段**，
    实体种类也只有 line/arc/circle/polyline ⇒ 颜色过渡**渲染不出来**。可行形态 = 复用本族边界/孤岛
    管线取真实闭合环、算真实净面积，把渐变**定义**（`gradient_name` / `colors` / `two_color` /
    `angle_deg` / `centered` / `transparency` / 边界 uuid / `area_nm2`）追加进表的 ``gradients`` 列表；
    记录里 `rendered=0`、`entity_count=0`（真为零：本层一个几何都没产），`gradient_fill` **返回 `[]`**。
    **不用密集线冒充满色/渐变**（那是静默错图）。

unsupported 清单（问法成立、但 L1 表达能力不够 ⇒ 本层显式不支持，不许用密集线/假几何冒充）
----------------------------------------------------------------------------------------
1. `pattern="solid"` 实心填充 ⇒ `CadError(reason="unsupported_pattern")`（实心面需要面实体，见第 2 条）。
2. 渐变的**可见着色**（LINEAR / CYLINDER / SPHERICAL / HELIX 的颜色过渡）⇒ 实体没有颜色/着色字段，
   渲染不出来；本层只落"声明"（第 10 条：`rendered=0` / `entity_count=0`），有颜色能力的下游渲染层
   按这份定义自行着色。**本层不写"已渲染"、不产假几何。**
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/hatch_measure.py 时的引导（见文件末 __main__）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    __package__ = "cadcore.ops"

import math
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

from ..edit.modify import _as_angle, _as_factor, _as_nm, _round_nm
from ..errors import CadError
from ..sketch.constraints import _get, new_uuid, sketch_entities

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "hatch_create",
    "hatch_update",
    "hatch_delete",
    "gradient_fill",
    "hatch_list",
    "hatch_query",
    "boundary_detect",
    "boundary_create",
    "measure_area",
    "measure_perimeter",
    "measure_distance",
    "prop_query",
    "selftest",
]

FAMILY = "hatch_measure"

#: 填充/测量表在文档 `extra` / 草图里的键名。
_HM_KEY = "hatch_measure"
#: 表 schema 版本（只加不改；将来迁移从这里分叉）。
_HM_SCHEMA = 1

#: 距离采样用圆/圆弧离散段数（固定值 ⇒ 同一张图两次测量逐位一致）。
_CIRCLE_SEGMENTS = 64
_ARC_SEGMENTS_PER_QUARTER = 8

#: 单次填充最多落多少条图案线（超过报 `too_many_lines`，不静默截断）。
_MAX_HATCH_LINES = 20000

#: 图案 → 线族角度偏移（相对 `angle_deg`）。**冻结枚举**；`solid` 显式不支持，未知图案报错。
_PATTERNS: dict[str, tuple[float, ...]] = {
    "lines": (0.0,),
    "ansi31": (0.0,),
    "ansi32": (0.0, 90.0),
    "cross": (0.0, 90.0),
    "grid": (0.0, 90.0),
}

#: 线型 → 虚线段模式（`on, off, on, off, ...`，单位纳米，乘 `scale` 生效）。`continuous` = 实线。
_LINETYPES: dict[str, tuple[int, ...] | None] = {
    "continuous": None,
    "dashed": (5_000_000, 2_500_000),
    "hidden": (2_500_000, 1_250_000),
    "dotted": (500_000, 2_000_000),
    "dashdot": (5_000_000, 1_250_000, 500_000, 1_250_000),
    "center": (10_000_000, 2_500_000, 2_500_000, 2_500_000),
}

#: 填充图案线的缺省图层名。
DEFAULT_HATCH_LAYER = "HATCH"


# ======================================================================================
# 错误与入参把关
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(detail, reason=reason, **details)


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米；把 modify 校验器的错误**转成基类 CadError**（保留 reason，不丢归因）。"""
    try:
        return _as_nm(value, what=what, positive=positive)
    except CadError as exc:
        raise _fail(str(exc.reason or "invalid_length"), str(exc.message), what=what, value=repr(value)) from None


def _angle(value: Any, *, what: str) -> float:
    try:
        return _as_angle(value, what=what)
    except CadError as exc:
        raise _fail(str(exc.reason or "invalid_argument"), str(exc.message), what=what) from None


def _factor(value: Any, *, what: str) -> float:
    try:
        return _as_factor(value, what=what)
    except CadError as exc:
        raise _fail(str(exc.reason or "invalid_argument"), str(exc.message), what=what) from None


def _count(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("invalid_argument", f"{what} must be a non-negative integer", what=what, value=repr(value))
    return int(value)


def _layer_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "layer must be a non-empty layer name", value=repr(value))
    return value.strip()


def _point(value: Any, *, what: str) -> tuple[int, int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    return (_nm(value[0], what=f"{what}.x"), _nm(value[1], what=f"{what}.y"))


# ======================================================================================
# 目标解析（sketch / doc 双兼容；表放哪见模块 docstring 第 1 条）
# ======================================================================================


def _is_mapping(value: Any) -> bool:
    return isinstance(value, _MappingABC)


def _looks_like_sketch(value: Any) -> bool:
    return _is_mapping(value) and isinstance(value.get("entities"), (list, tuple))


def _sketch_of(target: Any) -> tuple[Any, Any]:
    """`sk_or_doc` → `(sketch, doc_or_None)`。doc 非空表示"目标是文档"，表落 `doc.extra`。"""
    if _is_mapping(target):
        if _looks_like_sketch(target):
            return target, None
        for name in ("sketch", "sk"):
            nested = target.get(name)
            if _looks_like_sketch(nested):
                return nested, target
        extra = target.get("extra")
        if _is_mapping(extra):
            for name in ("sketch", "sk"):
                nested = extra.get(name)
                if _looks_like_sketch(nested):
                    return nested, target
        raise _fail("invalid_argument",
                    "sk_or_doc must be a sketch (entities) or a document exposing one",
                    target=type(target).__name__)
    for name in ("sketch", "sk"):
        nested = getattr(target, name, None)
        if _looks_like_sketch(nested):
            return nested, target
    raise _fail("invalid_argument",
                "sk_or_doc must be a sketch mapping or a document object with a .sketch",
                target=type(target).__name__)


def _entities(sk: Any, *, create: bool = False) -> list[Any]:
    """实体列表（**live list**）；`create=False` 时缺 `entities` 键返回 `[]` 且不写回（只读 op 无副作用）。"""
    entities = _get(sk, "entities", None)
    if entities is None:
        return sketch_entities(sk) if create else []
    return entities


def _table(sk: Any, doc: Any, *, create: bool = False) -> Any:
    """取填充表；`create=False` 时不存在返回 `None`（查询 op 不建表、不写入）。"""
    if doc is None:
        holder, key = sk, _HM_KEY
    else:
        holder = _get(doc, "extra", None)
        key = _HM_KEY
        if not isinstance(holder, _MutableMappingABC):
            raise _fail("document_invalid", "document must expose a mutable 'extra' mapping", target=type(doc).__name__)
    if not isinstance(holder, _MutableMappingABC):
        raise _fail("unsupported_target", "sketch must be a mutable mapping to carry the hatch table")
    table = holder.get(key)
    if not isinstance(table, _MutableMappingABC):
        if not create:
            return None
        table = {"schema": _HM_SCHEMA, "hatches": []}
        holder[key] = table
    records = table.get("hatches")
    if not isinstance(records, list):
        if not create:
            return None
        records = []
        table["hatches"] = records
    table.setdefault("schema", _HM_SCHEMA)
    return table


def _hatch_record(table: Any, hatch: Any) -> Any:
    reference = hatch if isinstance(hatch, str) else str(hatch)
    if not reference:
        raise _fail("invalid_argument", "hatch reference must be a non-empty uuid string")
    if table is None:
        raise _fail("unknown_reference", "no hatch record in this target", reference=reference)
    for record in table.get("hatches") or []:
        if isinstance(record, _MappingABC) and record.get("uuid") == reference:
            return record
    raise _fail("unknown_reference", "no hatch record with this uuid", reference=reference)


# ======================================================================================
# 几何读取（L1 字段 → 整数纳米；点列 → 环）
# ======================================================================================


def _geom(entity: Any) -> Any:
    geom = _get(entity, "geom", None)
    if not _is_mapping(geom):
        raise _fail("geometry_invalid", "entity has no usable geom mapping", uuid=_uuid_of(entity))
    return geom


def _uuid_of(entity: Any) -> str:
    value = _get(entity, "uuid", None)
    return value if isinstance(value, str) else ""


def _kind_of(entity: Any) -> str:
    return str(_get(entity, "kind", "") or "").strip().lower()


def _nm_field(entity: Any, name: str) -> int:
    value = _get(_geom(entity), name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("geometry_invalid", f"geom.{name} must be an integer nanometre value",
                    field=name, uuid=_uuid_of(entity), value=repr(value))
    return int(value)


def _num_field(entity: Any, name: str) -> float:
    value = _get(_geom(entity), name, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _fail("geometry_invalid", f"geom.{name} must be a finite number", field=name, uuid=_uuid_of(entity))
    return float(value)


def _key(point: Any, tol_nm: int) -> tuple[int, int]:
    """端点配对键：`tol_nm=0` 时整数纳米严格相等；`tol_nm>0` 时按该网格吸附。"""
    x, y = float(point[0]), float(point[1])
    if tol_nm > 0:
        return (int(round(x / tol_nm)), int(round(y / tol_nm)))
    return (int(round(x)), int(round(y)))


def _polyline_vertices(geom: Any) -> list[tuple[float, float]]:
    raw = geom.get("points")
    if raw is None:
        raw = geom.get("vertices")
    points: list[tuple[float, float]] = []
    for item in raw or []:
        if _is_mapping(item):
            points.append((float(item.get("x", 0.0)), float(item.get("y", 0.0))))
        elif isinstance(item, _SequenceABC) and len(item) >= 2:
            points.append((float(item[0]), float(item[1])))
    return points


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
    start, end = _num_field(entity, "start_deg"), _num_field(entity, "end_deg")
    while end <= start:
        end += 360.0
    return end - start


def _entity_points(entity: Any) -> tuple[list[tuple[float, float]], bool]:
    """L1 实体 → `(点列, 是否自闭合)`；`construction` 实体不参与（返回空点列）。"""
    if bool(_get(entity, "construction", False)):
        return [], False
    kind = _kind_of(entity)
    if kind == "line":
        return [(_num_field(entity, "x1"), _num_field(entity, "y1")),
                (_num_field(entity, "x2"), _num_field(entity, "y2"))], False
    if kind == "circle":
        cx, cy, r = _num_field(entity, "cx"), _num_field(entity, "cy"), _num_field(entity, "r")
        return _circle_points(cx, cy, r), True
    if kind == "arc":
        cx, cy, r = _num_field(entity, "cx"), _num_field(entity, "cy"), _num_field(entity, "r")
        return _arc_points(cx, cy, r, _num_field(entity, "start_deg"), _num_field(entity, "end_deg")), False
    if kind == "polyline":
        points = _polyline_vertices(_geom(entity))
        closed = len(points) >= 3 and _key(points[0], 0) == _key(points[-1], 0)
        return points, closed
    raise _fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                kind=kind, uuid=_uuid_of(entity))


def _curve_length(entity: Any) -> float:
    """单实体弧长（解析式优先；折线/直线用端点精确值）。"""
    kind = _kind_of(entity)
    if kind == "line":
        return math.dist((_num_field(entity, "x1"), _num_field(entity, "y1")),
                         (_num_field(entity, "x2"), _num_field(entity, "y2")))
    if kind == "circle":
        return 2.0 * math.pi * _num_field(entity, "r")
    if kind == "arc":
        return math.radians(_arc_sweep(entity)) * _num_field(entity, "r")
    if kind == "polyline":
        points = _polyline_vertices(_geom(entity))
        return _polyline_length(points, closed=False)
    raise _fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                kind=kind, uuid=_uuid_of(entity))


def _polyline_length(points: list[tuple[float, float]], *, closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for index in range(len(points) - 1):
        total += math.dist(points[index], points[index + 1])
    if closed and _key(points[0], 0) != _key(points[-1], 0):
        total += math.dist(points[-1], points[0])
    return total


def _shoelace(points: list[tuple[float, float]]) -> float:
    """有符号面积（nm²）；首末点不重合时自动补一条闭合边。"""
    pts = list(points)
    if len(pts) < 3:
        return 0.0
    if _key(pts[0], 0) != _key(pts[-1], 0):
        pts.append(pts[0])
    total = 0.0
    for index in range(len(pts) - 1):
        x1, y1 = pts[index]
        x2, y2 = pts[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _point_in_loop(point: Any, loop: list[tuple[float, float]]) -> bool:
    """射线法（只用于"环是否嵌在另一个环里"的洞判据）。"""
    pts = list(loop)
    if len(pts) >= 2 and _key(pts[0], 0) == _key(pts[-1], 0):
        pts = pts[:-1]
    if len(pts) < 3:
        return False
    x, y = float(point[0]), float(point[1])
    inside = False
    count = len(pts)
    for index in range(count):
        x1, y1 = pts[index]
        x2, y2 = pts[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if crossing > x:
                inside = not inside
    return inside


# --------------------------------------------------------------------------------------
# 边界识别：把首尾相接的实体串成环（成员 uuid 一并带上，`boundary=` token 要用）
# --------------------------------------------------------------------------------------


def _chain_loops(segments: list[tuple[list[tuple[float, float]], bool, str]],
                 tol_nm: int) -> list[dict[str, Any]]:
    """`(点列, 自闭合, uuid)` 段列 → 环记录 `{"points", "members", "closed"}`。

    判据与 `kernel.nano._chain_loops` 同一口径（端点键相等即相接；`_pt_key` 的取整语义，
    这里多带一个 `tol_nm` 网格与成员 uuid —— 边界 token 需要"这个环由哪些实体组成"）。
    """
    remaining = [(list(points), bool(closed), [uuid_]) for points, closed, uuid_ in segments if len(points) >= 2]
    loops: list[dict[str, Any]] = []
    while remaining:
        points, closed, members = remaining.pop(0)
        extended = True
        while extended and _key(points[0], tol_nm) != _key(points[-1], tol_nm):
            extended = False
            for index, (seg, seg_closed, seg_members) in enumerate(remaining):
                if _key(seg[0], tol_nm) == _key(points[-1], tol_nm):
                    points = points + seg[1:]
                elif _key(seg[-1], tol_nm) == _key(points[-1], tol_nm):
                    points = points + list(reversed(seg))[1:]
                elif _key(seg[-1], tol_nm) == _key(points[0], tol_nm):
                    points = seg[:-1] + points
                elif _key(seg[0], tol_nm) == _key(points[0], tol_nm):
                    points = list(reversed(seg))[:-1] + points
                else:
                    continue
                remaining.pop(index)
                members = members + seg_members
                closed = closed or seg_closed
                extended = True
                break
        is_closed = bool(closed) or (len(points) >= 3 and _key(points[0], tol_nm) == _key(points[-1], tol_nm))
        loops.append({"points": points, "members": members, "closed": is_closed})
    return loops


def _closed_loops(entities: list[Any], tol_nm: int) -> list[dict[str, Any]]:
    segments = []
    for entity in entities:
        points, closed = _entity_points(entity)
        if len(points) >= 2:
            segments.append((points, closed, _uuid_of(entity)))
    return [loop for loop in _chain_loops(segments, tol_nm) if loop["closed"] and len(loop["points"]) >= 3]


def _loop_metrics(loop: dict[str, Any], by_uuid: dict[str, Any]) -> tuple[float, float, float]:
    """`(有符号面积 nm², 周长 nm, 围线长 nm)`；单实体圆走解析式（不数离散化多边形）。"""
    members, points = loop["members"], loop["points"]
    if len(members) == 1:
        entity = by_uuid.get(members[0])
        if entity is not None and _kind_of(entity) == "circle":
            radius = _num_field(entity, "r")
            return math.pi * radius * radius, 2.0 * math.pi * radius, 2.0 * math.pi * radius
    return abs(_shoelace(points)), _polyline_length(points, closed=True), _polyline_length(points, closed=True)


#: 孤岛样式（AutoCAD Normal / Outer / Ignore）。`normal` = 交替（洞里的环又是实心 ⇒ 奇偶规则）。
_ISLAND_STYLES: tuple[str, ...] = ("normal", "outer", "ignore")


def _island_style(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in _ISLAND_STYLES:
        raise _fail("unknown_island_style", "island is not in the frozen enumeration",
                    island=repr(value), known=list(_ISLAND_STYLES))
    return value.strip().lower()


def _flag(value: Any, *, what: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise _fail("invalid_argument", f"{what} must be a boolean (or 0/1)", what=what, value=repr(value))


def _color_name(value: Any) -> str:
    if value is None:
        return "BYLAYER"
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "color must be a non-empty name or None", value=repr(value))
    return value.strip()


def _transparency(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _fail("invalid_argument", "transparency must be a finite number in [0, 1]", value=repr(value))
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise _fail("invalid_argument", "transparency must be in [0, 1]", value=repr(value))
    return number


def _gap_tolerance(sketch: Any, doc: Any, value: Any) -> int:
    """`tol_nm` 给了就用它；没给（None）就用表里记的 HPGAPTOL（缺省 0，不吸附）。"""
    if value is not None:
        tolerance = _nm(value, what="tol_nm")
        if tolerance < 0:
            raise _fail("invalid_length", "tol_nm must be >= 0", tol_nm=tolerance)
        return tolerance
    table = _table(sketch, doc, create=False)
    if isinstance(table, _MutableMappingABC):
        stored = table.get("gap_nm")
        if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0:
            return int(stored)
    return 0


def _nesting_depth(index: int, loops: list[dict[str, Any]]) -> int:
    """环 `index` 的嵌套深度 = 有多少别的环把它套在里面（射线法；这是一个真几何判据）。"""
    point = loops[index]["points"][0]
    return sum(1 for other in range(len(loops))
               if other != index and _point_in_loop(point, loops[other]["points"]))


def _region(loops: list[dict[str, Any]], by_uuid: dict[str, Any],
            island: Any = "normal") -> dict[str, Any]:
    """环集合 → 区域：面积最大者为外环；`island` 决定洞怎么算。

    - `normal`：奇偶规则 —— 嵌套深度为偶数的环是实心边界、为奇数的是洞（洞里再套环仍是实心）；
    - `outer` / `ignore`：只认最外层，内环**既不扣面积也不算周长**（AutoCAD 的 Outer/Ignore 语义）。
    """
    style = _island_style(island)
    metrics = [_loop_metrics(loop, by_uuid) for loop in loops]
    areas = [abs(item[0]) for item in metrics]
    outer_index = max(range(len(loops)), key=lambda index: areas[index])
    if style == "normal":
        solid = [index for index in range(len(loops)) if _nesting_depth(index, loops) % 2 == 0]
        holes = [index for index in range(len(loops)) if index not in solid]
    else:
        solid, holes = [outer_index], []
    outer_area = sum(metrics[index][0] for index in solid)
    hole_area = sum(metrics[index][0] for index in holes)
    solid_perimeter = sum(metrics[index][1] for index in solid)
    hole_perimeter = sum(metrics[index][1] for index in holes)
    return {
        "outer": loops[outer_index],
        "holes": [loops[index] for index in holes],
        "isolated": [loops[index] for index in solid if index != outer_index],
        "island": style,
        "outer_area_nm2": outer_area,
        "hole_area_nm2": hole_area,
        "net_area_nm2": outer_area - hole_area,
        "perimeter_nm": solid_perimeter + hole_perimeter,
        "loop_count": len(loops),
    }


def _find(sk: Any, reference: Any) -> Any:
    if not isinstance(reference, str) or not reference:
        raise _fail("invalid_argument", "entity reference must be a non-empty uuid string", reference=repr(reference))
    for entity in _entities(sk):
        if _uuid_of(entity) == reference:
            return entity
    raise _fail("unknown_reference", "no entity with this uuid", reference=reference)


def _subset(sk: Any, boundary: Any) -> list[Any]:
    """`boundary` kwarg（None / uuid / uuid 序列）→ 实体子集；空选择当场报错。"""
    if boundary is None:
        return list(_entities(sk))
    if isinstance(boundary, (str, bytes)):
        references = [boundary]
    elif isinstance(boundary, _SequenceABC):
        references = list(boundary)
    else:
        raise _fail("invalid_argument", "boundary must be a uuid, a sequence of uuids, or None",
                    value=repr(boundary))
    if not references:
        raise _fail("empty_selection", "boundary is empty: nothing to work on (refusing to no-op silently)")
    seen: set[str] = set()
    chosen: list[Any] = []
    for reference in references:
        entity = _find(sk, reference)
        key = _uuid_of(entity)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(entity)
    return chosen


# ======================================================================================
# 图案生成（扫描线 + 奇偶填充规则；虚线段落成多条 line 实体）
# ======================================================================================


def _dash_split(p1: tuple[float, float], p2: tuple[float, float],
                dash: tuple[int, ...] | None) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """一条线段 → 若干"实段"（`dash=None` 即实线；否则按 on/off 交替切）。"""
    if dash is None:
        return [(p1, p2)]
    length = math.dist(p1, p2)
    if length <= 0.0:
        return []
    ux, uy = (p2[0] - p1[0]) / length, (p2[1] - p1[1]) / length
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    travelled = 0.0
    index = 0
    while travelled < length - 1e-9 and index < 100000:
        span = float(dash[index % len(dash)])
        on = (index % 2 == 0)
        end = min(travelled + span, length)
        if on and end - travelled > 1e-9:
            start_point = (p1[0] + ux * travelled, p1[1] + uy * travelled)
            end_point = (p1[0] + ux * end, p1[1] + uy * end)
            out.append((start_point, end_point))
        travelled = end
        index += 1
    return out


def _scaled_dash(linetype: str, scale: float) -> tuple[int, ...] | None:
    if linetype not in _LINETYPES:
        raise _fail("unknown_linetype", "linetype is not in the frozen enumeration",
                    linetype=linetype, known=sorted(_LINETYPES))
    pattern = _LINETYPES[linetype]
    if pattern is None:
        return None
    scaled = tuple(_round_nm(value * scale) for value in pattern)
    if any(value < 1 for value in scaled):
        raise _fail("invalid_length", "scaled dash length is below 1 nm; increase scale", linetype=linetype, scale=scale)
    return scaled


def _hatch_segments(loops: list[dict[str, Any]], *, angle_deg: float, spacing_nm: int,
                    dash: tuple[int, ...] | None, max_lines: int) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """扫描线求交 + 奇偶规则裁剪：环集合（外环 + 洞）→ 图案线段（float 纳米）。"""
    radians = math.radians(angle_deg)
    ux, uy = math.cos(radians), math.sin(radians)
    nx, ny = -uy, ux
    edges: list[tuple[float, float, float, float]] = []
    t_values: list[float] = []
    for loop in loops:
        points = list(loop["points"])
        if _key(points[0], 0) != _key(points[-1], 0):
            points.append(points[0])
        for index in range(len(points) - 1):
            x1, y1 = points[index]
            x2, y2 = points[index + 1]
            s1, t1 = x1 * ux + y1 * uy, x1 * nx + y1 * ny
            s2, t2 = x2 * ux + y2 * uy, x2 * nx + y2 * ny
            if abs(t2 - t1) <= 1e-12 and abs(s2 - s1) <= 1e-12:
                continue
            edges.append((s1, t1, s2, t2))
            t_values.extend((t1, t2))
    if not edges:
        return []
    t_min, t_max = min(t_values), max(t_values)
    start = math.ceil(t_min / spacing_nm - 1e-9)
    stop = math.floor(t_max / spacing_nm + 1e-9)
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for step in range(start, stop + 1):
        level = float(step) * spacing_nm
        hits: list[float] = []
        for (s1, t1, s2, t2) in edges:
            if (t1 <= level < t2) or (t2 <= level < t1):
                ratio = (level - t1) / (t2 - t1)
                hits.append(s1 + ratio * (s2 - s1))
        hits.sort()
        for index in range(0, len(hits) - 1, 2):
            s_a, s_b = hits[index], hits[index + 1]
            if s_b - s_a <= 1e-9:
                continue
            p1 = (s_a * ux + level * nx, s_a * uy + level * ny)
            p2 = (s_b * ux + level * nx, s_b * uy + level * ny)
            out.extend(_dash_split(p1, p2, dash))
            if len(out) > max_lines:
                raise _fail("too_many_lines", "hatch would exceed max_lines; increase spacing or lower scale",
                            max_lines=max_lines, angle_deg=angle_deg, spacing_nm=spacing_nm)
    return out


def _append_line(sk: Any, p1: tuple[float, float], p2: tuple[float, float], layer: str) -> str | None:
    """一条 float 线段 → 普通 L1 `line` 实体（整数纳米；取整后退化成一个点则丢弃）。"""
    x1, y1 = _round_nm(p1[0]), _round_nm(p1[1])
    x2, y2 = _round_nm(p2[0]), _round_nm(p2[1])
    if (x1, y1) == (x2, y2):
        return None
    entity_uuid = new_uuid()
    sketch_entities(sk).append({
        "uuid": entity_uuid,
        "kind": "line",
        "layer": layer,
        "construction": False,
        "geom": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)},
    })
    return entity_uuid


def _drop_entities(sk: Any, uuids: set[str]) -> list[str]:
    """从草图删掉指定 uuid 的实体（返回真正删掉的 uuid，按原顺序）。"""
    entities = _entities(sk)
    removed = [_uuid_of(entity) for entity in entities if _uuid_of(entity) in uuids]
    if removed:
        entities[:] = [entity for entity in entities if _uuid_of(entity) not in uuids]
    return removed


def _pattern_families(pattern: Any) -> tuple[float, ...]:
    if not isinstance(pattern, str) or not pattern.strip():
        raise _fail("invalid_argument", "pattern must be a non-empty pattern name", value=repr(pattern))
    name = pattern.strip().lower()
    if name == "solid":
        raise _fail("unsupported_pattern",
                    "solid fill needs a face entity that L1 does not have; use a line pattern instead",
                    pattern=name)
    if name not in _PATTERNS:
        raise _fail("unknown_pattern", "pattern is not in the frozen enumeration",
                    pattern=name, known=sorted(_PATTERNS) + ["solid"])
    return _PATTERNS[name]


#: 渐变名（**冻结枚举**，大小写敏感；未知报 `unknown_gradient`）。L1 只记定义、不产着色几何。
_GRADIENT_NAMES: tuple[str, ...] = ("LINEAR", "CYLINDER", "SPHERICAL", "HELIX")


def _gradient_name(value: Any) -> str:
    if not isinstance(value, str) or value.strip() not in _GRADIENT_NAMES:
        raise _fail("unknown_gradient", "gradient name is not in the frozen enumeration",
                    gradient_name=repr(value), known=list(_GRADIENT_NAMES))
    return value.strip()


def _gradient_colors(value: Any) -> tuple[list[str], int]:
    """`colors` → `([起点色, 终点色], two_color)`；`None` = 单色渐变（两端同 BYLAYER）。

    双色渐变必须**两端都给**；恰好一端为 None ⇒ `CadError(reason="invalid_colors")`（不静默兜底成单色）。
    """
    if value is None:
        return ["BYLAYER", "BYLAYER"], 0
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _fail("invalid_colors", "colors must be None (single colour) or a (start, end) pair",
                    value=repr(value))
    first, second = value[0], value[1]
    if (first is None) != (second is None):
        raise _fail("invalid_colors",
                    "a two-colour gradient needs both colours; pass colors=None for a single colour",
                    value=repr(value))
    if first is None:
        return ["BYLAYER", "BYLAYER"], 0
    return [_color_name(first), _color_name(second)], 1


def gradient_fill(sk_or_doc: Any, *, boundary: Any = None, colors: Any = None,
                  gradient_name: Any = "LINEAR", angle_deg: Any = 0.0, centered: Any = False,
                  layer: Any = DEFAULT_HATCH_LAYER, transparency: Any = None,
                  island: Any = "normal", tol_nm: Any = None) -> list[str]:
    """渐变色填充（GRADIENT）在 L1 上的**可行形态**：只记定义、不产着色几何。

    L1 实体没有颜色/着色字段（字段白名单 `{uuid, kind, layer, construction, geom}`）⇒ 线性/柱面/
    球面/螺旋的颜色过渡在本层**不可渲染**（见模块 docstring 的 unsupported 清单第 2 条）。本 op 复用
    本族边界/孤岛管线取真实闭合环、算真实**净面积**，把渐变**定义**追加进表的 ``gradients`` 列表；
    本层**新建/改动 0 个实体** ⇒ **返回 `[]`（真为零）**，记录里 `rendered=0`、`entity_count=0`。
    **不用密集线冒充满色/渐变** —— 那是静默错图。

    kwargs：`boundary`（None = 自动识别闭合轮廓；uuid 或其序列）、`colors`（None = 单色渐变；
    `(起点色, 终点色)` = 双色渐变，恰好一端为 None ⇒ `invalid_colors`）、`gradient_name`
    （`_GRADIENT_NAMES`，大小写敏感）、`angle_deg`、`centered`、`layer`、`transparency`（[0,1]，
    None = 0）、`island`、`tol_nm`（端点配对网格；None = 表里记的 HPGAPTOL）。
    失败信号：`no_boundary`（本族口径；对标规格里叫 `missing_boundary`）、`unknown_gradient`、
    `invalid_colors`、`invalid_transparency`、`invalid_argument`。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    name = _gradient_name(gradient_name)
    palette, two_color = _gradient_colors(colors)
    angle = _angle(angle_deg, what="angle_deg")
    centered_flag = _flag(centered, what="centered")
    layer_name = _layer_name(layer)
    try:
        transparency_value = _transparency(transparency)
    except CadError as exc:  # 本 op 的 reason 更具体（对标规格 `invalid_transparency`），归因不丢
        if str(exc.reason) != "invalid_argument":
            raise
        raise _fail("invalid_transparency", str(exc.message), value=repr(transparency)) from None
    island_style = _island_style(island)
    tolerance = _gap_tolerance(sketch, doc, tol_nm)
    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    if not loops:
        raise _fail("no_boundary", "no closed loop found for the gradient; close the boundary or pass boundary=",
                    entities=len(subset), tol_nm=tolerance)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    region = _region(loops, by_uuid, island_style)
    table = _table(sketch, doc, create=True)
    gradients = table.get("gradients")
    if not isinstance(gradients, list):
        gradients = []
        table["gradients"] = gradients
    gradients.append({
        "uuid": new_uuid(),
        "kind": "gradient",
        "gradient_name": name,
        "colors": palette,
        "two_color": int(two_color),
        "angle_deg": float(angle % 360.0),
        "centered": bool(centered_flag),
        "layer": layer_name,
        "transparency": float(transparency_value),
        "island": island_style,
        "gap_nm": int(tolerance),
        "boundary": [member for loop in loops for member in loop["members"]],
        "area_nm2": _round_nm(region["net_area_nm2"]),
        "rendered": 0,
        "entity_count": 0,
        "schema": _HM_SCHEMA,
    })
    return []


# ======================================================================================
# 写操作：图案填充
# ======================================================================================


def hatch_create(sk_or_doc: Any, *, boundary: Any = None, pattern: Any = "lines", angle_deg: Any = 0.0,
                 spacing_nm: Any = 5_000_000, scale: Any = 1.0, linetype: Any = "continuous",
                 layer: Any = DEFAULT_HATCH_LAYER, island: Any = "normal", assoc: Any = False,
                 color: Any = None, transparency: Any = None,
                 tol_nm: Any = None, max_lines: Any = _MAX_HATCH_LINES) -> list[str]:
    """建图案填充：按 线型/角度/间距 切真实线段落成 L1 `line`，并记一条填充记录。

    kwargs：`boundary`（None = 自动识别闭合轮廓；否则 uuid 或其序列，外环 + 内环一起参与奇偶裁剪）、
    `pattern`（`lines`/`ansi31`/`ansi32`/`cross`/`grid`；`solid` 报 `unsupported_pattern`）、
    `angle_deg`、`spacing_nm`（>0，已乘 `scale` 前的值）、`scale`（>0，同时缩放间距与虚线长度）、
    `linetype`（`_LINETYPES` 枚举）、`layer`（图案线落哪层）、`tol_nm`（端点配对网格）、`max_lines`。

    **返回**：新建的图案线 uuid 列表，按"线族顺序 → 扫描线顺序 → 虚线段顺序"排列（顺序确定、可复现）。
    记录 uuid 不进返回值（它由 `hatch_list` / `hatch_query` 给出，不是实体）。
    失败信号：`no_boundary`（没有闭合轮廓）、`unsupported_pattern` / `unknown_pattern` /
    `unknown_linetype` / `too_many_lines`（不静默截断）、`invalid_length` / `invalid_argument`。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    families = _pattern_families(pattern)
    angle = _angle(angle_deg, what="angle_deg")
    spacing = _nm(spacing_nm, what="spacing_nm", positive=True)
    factor = _factor(scale, what="scale")
    effective_spacing = _round_nm(spacing * factor)
    if effective_spacing < 1:
        raise _fail("invalid_length", "scaled spacing is below 1 nm; increase spacing_nm or scale",
                    spacing_nm=spacing, scale=factor)
    dash = _scaled_dash(str(linetype).strip().lower(), factor)
    layer_name = _layer_name(layer)
    island_style = _island_style(island)
    assoc_flag = _flag(assoc, what="assoc")
    color_name = _color_name(color)
    transparency_value = _transparency(transparency)
    tolerance = _gap_tolerance(sketch, doc, tol_nm)
    limit = _count(max_lines, what="max_lines")
    if limit < 1:
        raise _fail("invalid_argument", "max_lines must be >= 1", max_lines=limit)

    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    if not loops:
        raise _fail("no_boundary", "no closed loop found for hatching; close the boundary or pass boundary=",
                    entities=len(subset), tol_nm=tolerance)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    region = _region(loops, by_uuid, island_style)

    planned: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for offset in families:
        planned.extend(_hatch_segments(loops, angle_deg=angle + offset, spacing_nm=effective_spacing,
                                       dash=dash, max_lines=max(1, limit - len(planned))))
        if len(planned) > limit:
            raise _fail("too_many_lines", "hatch exceeds max_lines", max_lines=limit, created=len(planned))
    # 全部族算完再落盘：中途超限（多族图案 grid/cross/ansi32）时一条图案线都不留 —— 失败不留半张填充图。
    created: list[str] = []
    for p1, p2 in planned:
        entity_uuid = _append_line(sketch, p1, p2, layer_name)
        if entity_uuid is not None:
            created.append(entity_uuid)

    record = {
        "uuid": new_uuid(),
        "pattern": str(pattern).strip().lower(),
        "angle_deg": float(angle % 360.0),
        "spacing_nm": int(effective_spacing),
        "scale": float(factor),
        "linetype": str(linetype).strip().lower(),
        "layer": layer_name,
        "island": island_style,
        "assoc": bool(assoc_flag),
        "color": color_name,
        "transparency": float(transparency_value),
        "gap_nm": int(tolerance),
        "boundary": [member for loop in loops for member in loop["members"]],
        "lines": list(created),
        "line_count": len(created),
        "area_nm2": _round_nm(region["net_area_nm2"]),
        "schema": _HM_SCHEMA,
    }
    table = _table(sketch, doc, create=True)
    table["hatches"].append(record)
    return created


def hatch_update(sk_or_doc: Any, *, hatch: Any, pattern: Any = None, angle_deg: Any = None,
                 spacing_nm: Any = None, scale: Any = None, linetype: Any = None,
                 layer: Any = None, tol_nm: Any = 0, max_lines: Any = _MAX_HATCH_LINES) -> list[str]:
    """改填充参数并**重新生成**图案线：旧线删除、新线落盘，记录原地更新。

    未传的 kwargs 沿用记录里的取值（`None` = 不改）。**返回**：重建后的图案线 uuid 列表
    （顺序同 `hatch_create`）。边界实体一个纳米都不动；`boundary` 不是本 op 的参数（改边界请删了重建），
    旧线的 uuid 随删除消失，不在返回值里。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=False)
    record = _hatch_record(table, hatch)
    pattern_value = record.get("pattern") if pattern is None else pattern
    angle_value = record.get("angle_deg") if angle_deg is None else angle_deg
    spacing_value = record.get("spacing_nm") if spacing_nm is None else spacing_nm
    scale_value = record.get("scale") if scale is None else scale
    linetype_value = record.get("linetype") if linetype is None else linetype
    layer_value = record.get("layer") if layer is None else layer
    boundary_value = list(record.get("boundary") or [])
    if not boundary_value:
        raise _fail("no_boundary", "hatch record has no boundary to regenerate from", hatch=str(hatch))
    stale = set(str(item) for item in (record.get("lines") or []))
    for entity_uuid in record.get("lines") or []:
        stale.add(str(entity_uuid))
    _drop_entities(sketch, stale)
    hatches = table.get("hatches")
    recorded = len(hatches) if isinstance(hatches, list) else 0
    # 目标还是原来那个（文档目标必须回文档 ⇒ 表仍落 `doc.extra`，不许在草图上另开一张）。
    created = hatch_create(sketch if doc is None else doc, boundary=boundary_value, pattern=pattern_value,
                           angle_deg=angle_value, spacing_nm=spacing_value, scale=scale_value,
                           linetype=linetype_value, layer=layer_value, tol_nm=tol_nm, max_lines=max_lines)
    # `hatch_create` 会追加一条记录；本 op 的语义是"记录原地更新" ⇒ 摘掉刚追加的那条，表里仍只有一条。
    hatches = table.get("hatches")
    if isinstance(hatches, list) and len(hatches) > recorded:
        del hatches[recorded:]
    record["pattern"] = str(pattern_value).strip().lower()
    record["angle_deg"] = float(_angle(angle_value, what="angle_deg") % 360.0)
    record["spacing_nm"] = _round_nm(_nm(spacing_value, what="spacing_nm", positive=True)
                                     * _factor(scale_value, what="scale"))
    record["scale"] = float(_factor(scale_value, what="scale"))
    record["linetype"] = str(linetype_value).strip().lower()
    record["layer"] = _layer_name(layer_value)
    record["lines"] = list(created)
    record["line_count"] = len(created)
    return created


def hatch_delete(sk_or_doc: Any, *, hatch: Any) -> list[str]:
    """删填充：删除该记录的图案线并摘掉记录（**边界几何不动**）。

    **返回**：被删掉的图案线 uuid 列表（按它们在草图里的原顺序）。记录 uuid 不在其中。
    记录不存在报 `unknown_reference`；记录里的线已被别处删掉 ⇒ 返回 `[]`（真为零）。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=False)
    record = _hatch_record(table, hatch)
    removed = _drop_entities(sketch, set(str(item) for item in (record.get("lines") or [])))
    table["hatches"] = [item for item in table["hatches"] if item is not record]
    return removed


def boundary_create(sk_or_doc: Any, *, boundary: Any = None, index: Any = 0, layer: Any = "0",
                    tol_nm: Any = 0) -> list[str]:
    """把识别出的一个环落成**闭合 `polyline` 实体**（首末点重合 ⇒ 与 `primitives.closed_profiles` 同判据）。

    kwargs：`boundary`（None = 全图自动识别；否则 uuid 或其序列）、`index`（候选环下标，按面积从大到小；
    缺省 0 = 最大环）、`layer`、`tol_nm`。**返回**：`[新折线 uuid]`（只建一个实体）。
    没有闭合轮廓 ⇒ `CadError(reason="no_boundary")`；下标越界 ⇒ `CadError(reason="index_out_of_range")`。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    tolerance = _nm(tol_nm, what="tol_nm")
    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    if not loops:
        raise _fail("no_boundary", "no closed loop found", entities=len(subset), tol_nm=tolerance)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    ordered = sorted(loops, key=lambda loop: -abs(_loop_metrics(loop, by_uuid)[0]))
    position = _count(index, what="index")
    if position >= len(ordered):
        raise _fail("index_out_of_range", "boundary index is out of range",
                    index=position, available=len(ordered))
    points = [[_round_nm(x), _round_nm(y)] for x, y in ordered[position]["points"]]
    if points and list(points[0]) != list(points[-1]):
        points.append(list(points[0]))
    entity_uuid = new_uuid()
    sketch_entities(sketch).append({
        "uuid": entity_uuid,
        "kind": "polyline",
        "layer": _layer_name(layer),
        "construction": False,
        "geom": {"points": points},
    })
    return [entity_uuid]


# ======================================================================================
# 只读操作：边界识别 / 测量 / 特性查询
# ======================================================================================


def boundary_detect(sk_or_doc: Any, *, boundary: Any = None, tol_nm: Any = 0) -> list[str]:
    """识别闭合边界（只读，不建表不写入）。

    端点按整数纳米（`tol_nm>0` 时按该网格吸附）串成环；`circle` 与首末点重合的 `polyline` 自成一环。
    **返回**：每个闭合环一个 token（按"最大面积 → 最小"排序）
    ``boundary=<uuid,uuid,...> area_nm2=<int> perimeter_nm=<int> closed=1``，
    成员 uuid 按环的走向顺序、逗号分隔。**一个闭合环都没有 ⇒ 返回 `[]`（真为零）** ——
    开放链不是边界，本层不猜、不自动补线。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    tolerance = _nm(tol_nm, what="tol_nm")
    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    tokens: list[str] = []
    for loop in sorted(loops, key=lambda item: -abs(_loop_metrics(item, by_uuid)[0])):
        area, perimeter, _girth = _loop_metrics(loop, by_uuid)
        members = ",".join(loop["members"])
        tokens.append("boundary=%s area_nm2=%d perimeter_nm=%d closed=1"
                      % (members, _round_nm(area), _round_nm(perimeter)))
    return tokens


def measure_area(sk_or_doc: Any, *, boundary: Any = None, tol_nm: Any = 0) -> list[str]:
    """量面积/周长（只读）。`boundary` 给的实体先串成环，最大环为外环、被它包住的环算洞。

    **返回**（固定 6 个 token、顺序固定）：``area_nm2``（净面积 = 外 − 洞）、``outer_area_nm2``、
    ``hole_area_nm2``（没有洞 = 0，真为零）、``perimeter_nm``（外环 + 各洞周长）、``loop_count``、``closed``。
    一个闭合环都没有 ⇒ 前四个 token 为 ``NA``（不适用：没有闭合轮廓），``loop_count=0``、``closed=0``
    —— "没算"与"算出来是 0"不许同形。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    tolerance = _nm(tol_nm, what="tol_nm")
    subset = _subset(sketch, boundary)
    loops = _closed_loops(subset, tolerance)
    if not loops:
        return ["area_nm2=NA", "outer_area_nm2=NA", "hole_area_nm2=NA", "perimeter_nm=NA",
                "loop_count=0", "closed=0"]
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    region = _region(loops, by_uuid)
    return ["area_nm2=%d" % _round_nm(region["net_area_nm2"]),
            "outer_area_nm2=%d" % _round_nm(region["outer_area_nm2"]),
            "hole_area_nm2=%d" % _round_nm(region["hole_area_nm2"]),
            "perimeter_nm=%d" % _round_nm(region["perimeter_nm"]),
            "loop_count=%d" % int(region["loop_count"]),
            "closed=1"]


def measure_perimeter(sk_or_doc: Any, *, boundary: Any = None) -> list[str]:
    """量周长：**逐实体弧长求和**（解析式优先：line 端点距离、circle 2πr、arc r·sweep、polyline 折线和）。

    **返回**（固定 3 个 token、顺序固定）：``perimeter_nm``、``curve_count``、``closed``
    （全部实体都参与成环 ⇒ 1，否则 0）。没有实体 ⇒ ``perimeter_nm=NA``、``curve_count=0``、``closed=0``。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    subset = _subset(sketch, boundary)
    curves = [entity for entity in subset if _entity_points(entity)[0]]
    if not curves:
        return ["perimeter_nm=NA", "curve_count=0", "closed=0"]
    total = sum(_curve_length(entity) for entity in curves)
    tolerance = 0
    loops = _closed_loops(curves, tolerance)
    covered = {member for loop in loops for member in loop["members"]}
    is_closed = len(covered) == len(curves)
    return ["perimeter_nm=%d" % _round_nm(total), "curve_count=%d" % len(curves),
            "closed=%d" % (1 if is_closed else 0)]


def measure_distance(sk_or_doc: Any, *, p1_nm: Any = None, p2_nm: Any = None,
                     ref_a: Any = None, ref_b: Any = None) -> list[str]:
    """量距离：点-点 / 点-实体 / 实体-实体（实体按离散点列取最近点对）。

    四种组合**必须给足两个操作数**：`p1_nm+p2_nm`、`p1_nm+ref_b`、`ref_a+p2_nm`、`ref_a+ref_b`；
    缺一个 ⇒ `CadError(reason="invalid_argument")`（宁可报错，不猜"到原点"）。

    **返回**（固定 5 个 token、顺序固定）：``distance_nm``、``dx_nm``、``dy_nm``（最近点对的坐标差）、
    ``nearest_a_nm=x,y``、``nearest_b_nm=x,y``。距离与坐标一律整数纳米。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    first_point = None if p1_nm is None else _point(p1_nm, what="p1_nm")
    second_point = None if p2_nm is None else _point(p2_nm, what="p2_nm")
    first_entity = None if ref_a is None else _find(sketch, ref_a)
    second_entity = None if ref_b is None else _find(sketch, ref_b)
    if first_point is not None and second_point is not None:
        candidates_a = [first_point]
        candidates_b = [second_point]
    elif first_point is not None and second_entity is not None:
        candidates_a = [first_point]
        candidates_b = _entity_points(second_entity)[0]
    elif first_entity is not None and second_point is not None:
        candidates_a = _entity_points(first_entity)[0]
        candidates_b = [second_point]
    elif first_entity is not None and second_entity is not None:
        candidates_a = _entity_points(first_entity)[0]
        candidates_b = _entity_points(second_entity)[0]
    else:
        raise _fail("invalid_argument",
                    "measure_distance needs two operands: p1_nm/p2_nm, p1_nm+ref_b, ref_a+p2_nm or ref_a+ref_b")
    if not candidates_a or not candidates_b:
        raise _fail("no_geometry", "one of the operands has no measurable geometry",
                    ref_a=repr(ref_a), ref_b=repr(ref_b))
    best = None
    for point_a in candidates_a:
        for point_b in candidates_b:
            distance = math.dist(point_a, point_b)
            if best is None or distance < best[0]:
                best = (distance, point_a, point_b)
    distance, point_a, point_b = best
    return ["distance_nm=%d" % _round_nm(distance),
            "dx_nm=%d" % _round_nm(float(point_b[0]) - float(point_a[0])),
            "dy_nm=%d" % _round_nm(float(point_b[1]) - float(point_a[1])),
            "nearest_a_nm=%d,%d" % (_round_nm(point_a[0]), _round_nm(point_a[1])),
            "nearest_b_nm=%d,%d" % (_round_nm(point_b[0]), _round_nm(point_b[1]))]


def prop_query(sk_or_doc: Any, *, ref: Any = None, refs: Any = None) -> list[str]:
    """查实体特性（只读）。`ref` / `refs` 给 uuid（不给 = 查全部实体，空图返回 `[]`）。

    **返回**：每个实体一个 token 块（按入参顺序 / 草图顺序），块内 token 顺序固定：
    ``uuid``、``kind``、``layer``、``construction``、``bbox_nm=x1,y1,x2,y2``、``length_nm``（总弧长）、
    ``area_nm2``（圆 = πr²、闭合折线 = 鞋带公式；其余 ``NA``）、``radius_nm``（圆/弧，其余 ``NA``）、
    ``vertex_count``（折线，其余 ``NA``）、``volume_nm3=NA``（L1 是二维草图，体积不适用）、
    ``z_nm=-1``（**未采**：本层二维草图不采 Z）。块与块之间靠每个 ``uuid=`` 起头切分。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    if ref is not None and refs is not None:
        raise _fail("invalid_argument", "give either ref or refs, not both")
    if refs is not None:
        if isinstance(refs, (str, bytes)) or not isinstance(refs, _SequenceABC):
            raise _fail("invalid_argument", "refs must be a sequence of uuid strings", value=repr(refs))
        chosen = [_find(sketch, item) for item in refs]
    elif ref is not None:
        chosen = [_find(sketch, ref)]
    else:
        chosen = [entity for entity in _entities(sketch)]
    tokens: list[str] = []
    for entity in chosen:
        kind = _kind_of(entity)
        points, closed = _entity_points(entity)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if points:
            bbox = "%d,%d,%d,%d" % (_round_nm(min(xs)), _round_nm(min(ys)),
                                    _round_nm(max(xs)), _round_nm(max(ys)))
        else:
            bbox = "NA"
        length = _curve_length(entity) if points else 0.0
        if kind == "circle":
            radius = _num_field(entity, "r")
            area_token = "%d" % _round_nm(math.pi * radius * radius)
            radius_token = "%d" % _round_nm(radius)
        elif kind == "arc":
            area_token = "NA"
            radius_token = "%d" % _round_nm(_num_field(entity, "r"))
        elif kind == "polyline":
            area_token = "%d" % _round_nm(abs(_shoelace(points))) if closed else "NA"
            radius_token = "NA"
        else:
            area_token = "NA"
            radius_token = "NA"
        vertex_token = "%d" % len(_polyline_vertices(_geom(entity))) if kind == "polyline" else "NA"
        tokens.extend([
            "uuid=%s" % _uuid_of(entity),
            "kind=%s" % kind,
            "layer=%s" % str(_get(entity, "layer", "") or ""),
            "construction=%d" % (1 if bool(_get(entity, "construction", False)) else 0),
            "bbox_nm=%s" % bbox,
            "length_nm=%d" % _round_nm(length),
            "area_nm2=%s" % area_token,
            "radius_nm=%s" % radius_token,
            "vertex_count=%s" % vertex_token,
            "volume_nm3=NA",
            "z_nm=-1",
        ])
    return tokens


def hatch_list(sk_or_doc: Any) -> list[str]:
    """列出填充记录（只读，不建表）：每条记录一个 token ``hatch=<记录 uuid>``，按记录顺序。无记录 ⇒ `[]`。"""
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=False)
    if table is None:
        return []
    return ["hatch=%s" % str(record.get("uuid"))
            for record in table.get("hatches") or [] if _is_mapping(record)]


def hatch_query(sk_or_doc: Any, *, hatch: Any = None) -> list[str]:
    """查填充记录（只读）。`hatch` 给记录 uuid；不给则每个记录一个块（同 `hatch_list`）。

    给了 uuid ⇒ **返回**该记录的 ``key=value`` token 列表，顺序固定：
    ``hatch``、``pattern``、``angle_deg``、``spacing_nm``、``scale``、``linetype``、``layer``、
    ``line_count``、``area_nm2``、``boundary=<uuid,uuid,...>``、``lines=<uuid,uuid,...>``。
    记录不存在 ⇒ `CadError(reason="unknown_reference")`。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=False)
    if hatch is None:
        return hatch_list(sketch)
    record = _hatch_record(table, hatch)
    return [
        "hatch=%s" % str(record.get("uuid")),
        "pattern=%s" % str(record.get("pattern", "")),
        "angle_deg=%s" % _format_number(record.get("angle_deg", 0.0)),
        "spacing_nm=%d" % int(record.get("spacing_nm", 0)),
        "scale=%s" % _format_number(record.get("scale", 1.0)),
        "linetype=%s" % str(record.get("linetype", "")),
        "layer=%s" % str(record.get("layer", "")),
        "line_count=%d" % int(record.get("line_count", 0)),
        "area_nm2=%d" % int(record.get("area_nm2", 0)),
        "boundary=%s" % ",".join(str(item) for item in (record.get("boundary") or [])),
        "lines=%s" % ",".join(str(item) for item in (record.get("lines") or [])),
    ]


def _format_number(value: Any) -> str:
    """角度/比例落 token：整数值不带小数点，其余保留 6 位（可复现，不用 locale）。"""
    number = float(value)
    if number == int(number):
        return "%d" % int(number)
    return "%.6f" % number


# ======================================================================================
# 选择 / 特性匹配 / 系统变量（FILLMODE、HPGAPTOL）
# ======================================================================================


def boundary_from_point(sk_or_doc: Any, *, point: Any, tol_nm: Any = None) -> list[str]:
    """内部点拾取边界（只读）：返回**包含该点的最小闭合环**的成员 uuid，按环走向顺序。

    `point` = `(x_nm, y_nm)`；`tol_nm=None` 时取表里记的 HPGAPTOL（缺省 0，不吸附）。
    **返回**：该环成员的 uuid 列表（都是文档里真实存在的实体）。没有任何闭合环包含该点 ⇒
    `CadError(reason="no_boundary")` —— 宁可报错，**不猜最近环**。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    target = _point(point, what="point")
    tolerance = _gap_tolerance(sketch, doc, tol_nm)
    loops = _closed_loops(_subset(sketch, None), tolerance)
    hits = [loop for loop in loops if _point_in_loop(target, loop["points"])]
    if not hits:
        raise _fail("no_boundary", "no closed loop contains this point",
                    point="%d,%d" % (target[0], target[1]), loops=len(loops))
    hits.sort(key=lambda loop: abs(_shoelace(loop["points"])))
    return [str(member) for member in hits[0]["members"]]


def quick_select(sk_or_doc: Any, *, kind: Any = None, layer: Any = None) -> list[str]:
    """快速选择（只读）：按 `kind` / `layer` 过滤实体，**返回**命中实体的 uuid（草图顺序）。

    `None` = 不筛这一维；显式给空串 ⇒ `CadError(reason="invalid_argument")`（"没筛"与"筛出空"不同形）。
    一个都没命中 ⇒ `[]`（真为零）。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    want_kind = None if kind is None else str(kind).strip().lower()
    want_layer = None if layer is None else str(layer).strip()
    if want_kind == "" or want_layer == "":
        raise _fail("invalid_argument", "kind/layer must be a non-empty string or None",
                    kind=repr(kind), layer=repr(layer))
    chosen: list[str] = []
    for entity in _entities(sketch):
        if want_kind is not None and _kind_of(entity) != want_kind:
            continue
        if want_layer is not None and str(_get(entity, "layer", "") or "") != want_layer:
            continue
        chosen.append(_uuid_of(entity))
    return chosen


def match_properties(sk_or_doc: Any, *, source: Any, targets: Any = None, layer: Any = None) -> list[str]:
    """匹配特性（写）：把 `source` 的图层（或显式 `layer`）刷到 `targets` 上。

    `targets` = uuid / uuid 序列 / `None`（None = 除 `source` 外的全部实体）。
    **返回**：**真正被改动**的实体 uuid（属性本来就一样 ⇒ 不改动、不进返回值 ⇒ `[]` = 真为零）。
    """
    sketch, _doc = _sketch_of(sk_or_doc)
    origin = _find(sketch, source)
    if targets is None:
        chosen = [entity for entity in _entities(sketch) if _uuid_of(entity) != _uuid_of(origin)]
    elif isinstance(targets, (str, bytes)):
        chosen = [_find(sketch, targets)]
    elif isinstance(targets, _SequenceABC):
        chosen = [_find(sketch, item) for item in targets]
    else:
        raise _fail("invalid_argument", "targets must be a uuid, a sequence of uuids, or None",
                    value=repr(targets))
    wanted = _layer_name(layer) if layer is not None else str(_get(origin, "layer", "") or "")
    changed: list[str] = []
    for entity in chosen:
        if not isinstance(entity, _MutableMappingABC):
            raise _fail("unsupported_target", "matched entity must be a mutable mapping",
                        uuid=_uuid_of(entity))
        if str(_get(entity, "layer", "") or "") == wanted:
            continue
        entity["layer"] = wanted
        changed.append(_uuid_of(entity))
    return changed


def fill_mode(sk_or_doc: Any, *, mode: Any = None) -> list[str]:
    """填充显示开关 FILLMODE（文档/草图级）：`mode=None` 读当前值，给了就写（True/1 = 显示图案线）。

    L1 实体没有逐实体显示位 ⇒ 开关记在填充表的 ``fill_mode`` 键（缺省 1），**返回** token
    ``fill_mode=0|1``。下游渲染/投影按它决定要不要画图案线；**边界几何永远不动**。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=mode is not None)
    if mode is not None:
        if table is None:
            raise _fail("unsupported_target", "cannot record fill_mode on this target")
        table["fill_mode"] = 1 if _flag(mode, what="mode") else 0
    current = 1
    if isinstance(table, _MutableMappingABC) and isinstance(table.get("fill_mode"), int):
        current = int(table["fill_mode"])
    return ["fill_mode=%d" % current]


def gap_tolerance(sk_or_doc: Any, *, tol_nm: Any = None) -> list[str]:
    """间隙公差 HPGAPTOL（文档/草图级）：`tol_nm=None` 读当前值，给了就写；**返回** ``gap_nm=<int>``。

    真值语义：`hatch_create` 与 `boundary_from_point` 的 `tol_nm=None` 时**用这里记的值**做端点配对
    网格（缺省 0 = 严格整数纳米相等）。负数 ⇒ `CadError(reason="invalid_length")`。
    """
    sketch, doc = _sketch_of(sk_or_doc)
    table = _table(sketch, doc, create=tol_nm is not None)
    if tol_nm is not None:
        value = _nm(tol_nm, what="tol_nm")
        if value < 0:
            raise _fail("invalid_length", "tol_nm must be >= 0", tol_nm=value)
        if table is None:
            raise _fail("unsupported_target", "cannot record gap tolerance on this target")
        table["gap_nm"] = int(value)
    stored = 0
    if isinstance(table, _MutableMappingABC):
        raw = table.get("gap_nm")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            stored = int(raw)
    return ["gap_nm=%d" % stored]


# ======================================================================================
# 操作表（主名 + 别名；别名与主名指向同一函数，`OPERATIONS` 是唯一发现入口）
# ======================================================================================

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 图案填充（线型/角度/间距/边界/孤岛/关联/外观）
    "hatch_create": hatch_create,
    "hatch": hatch_create,
    # 渐变色填充：L1 无颜色字段 ⇒ 只记定义、不产几何（返回 [] 真为零）
    "gradient_fill": gradient_fill,
    "hatch_update": hatch_update,
    "hatch_edit": hatch_update,
    "hatch_delete": hatch_delete,
    # 填充查询
    "hatch_list": hatch_list,
    "hatch_query": hatch_query,
    # 边界识别与拾取
    "boundary_detect": boundary_detect,
    "boundary": boundary_detect,
    "boundary_create": boundary_create,
    "boundary_from_point": boundary_from_point,
    # 测量
    "measure_area": measure_area,
    "area": measure_area,
    "measure_perimeter": measure_perimeter,
    "perimeter": measure_perimeter,
    "measure_distance": measure_distance,
    "distance": measure_distance,
    # 特性查询与选择
    "prop_query": prop_query,
    "properties": prop_query,
    "entity_query": prop_query,
    "quick_select": quick_select,
    "match_properties": match_properties,
    # 系统变量
    "fill_mode": fill_mode,
    "gap_tolerance": gap_tolerance,
}


# ======================================================================================
# 自检（`if __name__ == "__main__"` 跑；打印 HATCH_MEASURE_OPS verdict=PASS ops=N）
# ======================================================================================

_NM_PER_MM = 1_000_000


def _selftest_rectangle(width_mm: float = 100.0, height_mm: float = 50.0) -> Any:
    width, height = int(width_mm * _NM_PER_MM), int(height_mm * _NM_PER_MM)
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"entities": entities, "constraints": [], "dimensions": []}


def _selftest_circle(radius_mm: float = 10.0) -> Any:
    radius = int(radius_mm * _NM_PER_MM)
    return {"entities": [{"uuid": new_uuid(), "kind": "circle", "layer": "0", "construction": False,
                          "geom": {"cx": 0, "cy": 0, "r": radius}}],
            "constraints": [], "dimensions": []}


def _token_value(tokens: list[str], key: str) -> str:
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def selftest() -> list[tuple[str, bool, str]]:
    """3~5 条自检（接口 / 边界+面积 / 填充 / 距离+特性 / 错误路径），返回 `(名字, 通过?, 细节)`。"""
    results: list[tuple[str, bool, str]] = []

    # 1) 冻结接口：FAMILY / OPERATIONS / 可调用 / 模块可导入（无 ops/__init__.py 的命名空间包）
    ok = (isinstance(FAMILY, str) and FAMILY == "hatch_measure"
          and isinstance(OPERATIONS, dict) and len(OPERATIONS) >= 11
          and all(callable(function) for function in OPERATIONS.values()))
    results.append(("interface", bool(ok), "family=%s ops=%d" % (FAMILY, len(OPERATIONS))))

    # 2) 边界识别 + 面积/周长：100mm x 50mm 矩形
    sketch = _selftest_rectangle()
    detected = boundary_detect(sketch)
    area_tokens = measure_area(sketch)
    expected_area = 100 * _NM_PER_MM * 50 * _NM_PER_MM
    expected_perimeter = 2 * (100 + 50) * _NM_PER_MM
    ok = (len(detected) == 1 and "closed=1" in detected[0]
          and int(_token_value(area_tokens, "area_nm2")) == expected_area
          and int(_token_value(area_tokens, "perimeter_nm")) == expected_perimeter
          and int(_token_value(area_tokens, "hole_area_nm2")) == 0)
    results.append(("boundary_area", bool(ok), "area=%s perimeter=%s loops=%d"
                    % (_token_value(area_tokens, "area_nm2"), _token_value(area_tokens, "perimeter_nm"),
                       len(detected))))

    # 3) 图案填充：45 度 / 5mm 间距 / 虚线线型；线全落在矩形 bbox 内且坐标是整数纳米
    count_before = len(sketch["entities"])
    created = hatch_create(sketch, pattern="ansi31", angle_deg=45.0, spacing_nm=5 * _NM_PER_MM)
    entities = {entity["uuid"]: entity for entity in sketch["entities"]}
    inside = True
    for entity_uuid in created:
        geom = entities[entity_uuid]["geom"]
        for key in ("x1", "y1", "x2", "y2"):
            value = geom[key]
            if not isinstance(value, int) or isinstance(value, bool):
                inside = False
        if not (0 <= geom["x1"] <= 100 * _NM_PER_MM and 0 <= geom["x2"] <= 100 * _NM_PER_MM
                and 0 <= geom["y1"] <= 50 * _NM_PER_MM and 0 <= geom["y2"] <= 50 * _NM_PER_MM):
            inside = False
    listed = hatch_list(sketch)
    details = hatch_query(sketch, hatch=listed[0][len("hatch="):]) if listed else []
    after_first = len(sketch["entities"])   # 第一轮后立刻取数，避免被第二轮创建污染
    dashed = hatch_create(sketch, pattern="lines", angle_deg=0.0, spacing_nm=10 * _NM_PER_MM,
                          linetype="dashed")
    # 两轮各查一次增量：第一轮 = count_before + len(created)，两轮后 = 再加 len(dashed)。
    ok = (len(created) > 0 and inside and after_first == count_before + len(created)
          and len(sketch["entities"]) == count_before + len(created) + len(dashed)
          and len(listed) == 1 and _token_value(details, "line_count") == str(len(created))
          and len(dashed) > len(created) // 2)
    results.append(("hatch", bool(ok), "lines=%d dashed_lines=%d records=%d"
                    % (len(created), len(dashed), len(listed))))

    # 4) 距离 + 特性查询：3-4-5 直角三角形；圆面积 πr²
    distance = measure_distance(sketch, p1_nm=(0, 0), p2_nm=(3 * _NM_PER_MM, 4 * _NM_PER_MM))
    circle = _selftest_circle(10.0)
    props = prop_query(circle)
    expected_circle_area = _round_nm(math.pi * (10 * _NM_PER_MM) ** 2)
    ok = (int(_token_value(distance, "distance_nm")) == 5 * _NM_PER_MM
          and _token_value(distance, "nearest_b_nm") == "3000000,4000000"
          and int(_token_value(props, "area_nm2")) == expected_circle_area
          and _token_value(props, "radius_nm") == str(10 * _NM_PER_MM)
          and _token_value(props, "z_nm") == "-1"
          and _token_value(props, "volume_nm3") == "NA")
    results.append(("distance_props", bool(ok), "distance=%s circle_area=%s"
                    % (_token_value(distance, "distance_nm"), _token_value(props, "area_nm2"))))

    # 5) 错误与口径：solid 不支持 / 未知线型 / 空图 NA / 缺操作数
    reasons = []
    try:
        hatch_create(sketch, pattern="solid")
    except CadError as exc:
        reasons.append(exc.reason)
    try:
        hatch_create(sketch, pattern="lines", linetype="wavy")
    except CadError as exc:
        reasons.append(exc.reason)
    try:
        measure_distance(sketch, p1_nm=(0, 0))
    except CadError as exc:
        reasons.append(exc.reason)
    empty = measure_area({"entities": [], "constraints": [], "dimensions": []})
    ok = (reasons == ["unsupported_pattern", "unknown_linetype", "invalid_argument"]
          and _token_value(empty, "area_nm2") == "NA" and _token_value(empty, "loop_count") == "0")
    results.append(("errors_and_scales", bool(ok), "reasons=%s empty_area=%s"
                    % (",".join(reasons), _token_value(empty, "area_nm2"))))
    return results


def _main() -> int:
    results = selftest()
    for name, ok, detail in results:
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    failed = [name for name, ok, _detail in results if not ok]
    if failed:
        print("HATCH_MEASURE_OPS verdict=FAIL ops=%d failed=%s" % (len(OPERATIONS), ",".join(failed)))
        return 1
    print("HATCH_MEASURE_OPS verdict=PASS ops=%d" % len(OPERATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
