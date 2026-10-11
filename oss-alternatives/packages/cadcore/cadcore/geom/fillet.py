#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cadcore.geom.fillet -- bounded fillet geometry operator (corner edge pair -> trim + tangent arc).

一句话用途
---------
把"在两条相邻边构成的角上做半径 r 的圆角"从**参数记录**抬成**真几何**：对轮廓做
**裁剪 / 延伸 + 圆弧过渡**，输出真实圆弧（圆心 / 半径 / 切点 / 扫掠区间）与一条可直接交给
cadcore.kernel 现算出实体的**已倒圆轮廓**（entities），并给出有界半径的**硬边界**：
半径超过边界一律 CadError(reason='radius_exceeds_bound')，**绝不静默夹紧或退化**。

几何唯一真源（本模块不重写圆角算法）
----------------------------------
逐角调用 cadcore.edit.modify.fillet —— 本仓"直线-直线圆角"的唯一实现，自带后置硬断言
（圆心到两条直线距离 == 半径、切点与两条线的新端点重合）。本模块只做它不做（也不该做）的三件事：

1. **实体角边 -> 轮廓角**：持久拓扑引用 topo:edge:<feature_uuid>:lateral/i+lateral/j
   是"两个相邻侧面之间的竖直棱"，映射到轮廓角 k（两条相邻轮廓线的公共顶点）；
2. **有界半径**：切线长 t_k = r * cot(theta_k / 2)（theta_k = 该角内角），
   逐边检查"两端圆角切线长之和 <= 该边长"；超界报 radius_exceeds_bound 并给出
   max_radius_nm（可用的最大整数纳米半径）与超界的那条边。半径恰好等于边界（边长被吃光）
   是**允许**的：该边退化为一点，显式记进 dropped_edges，不静默。
3. **合成轮廓 + 出实体**：被裁剪的直线（uuid 沿用源实体，几何已改）+ 真实 arc 实体，
   交给 cadcore.kernel 的 extrude 现算出真实体（shape_from_plan）。

三态口径（0 = 真为零 / -1 = 未采 / NA = 不适用）
-----------------------------------------------
量不出来的量记 -1（未采），只有真为零才写 0；没有可求值实体 / 轮廓不是"全直线闭合环"
一律抛 CadError（机器可读 reason），不把"没算"写成"算出来是 0"。

有意限定的边界（写下来，防止下游靠猜）
-------------------------------------
- 只做**凸角**（内角 0 < theta < pi）与**直线-直线**轮廓；凹角 / 圆弧 / 样条一律
  CadError(reason='unsupported_corner' / 'profile_not_fillettable')。
- 只做**竖直棱**（两个 lateral 侧面之间的棱）对应的轮廓角：本层把"实体边的圆角"定义为
  "拉伸轮廓的角圆角"（棱柱的竖直棱倒圆 == 轮廓角倒圆）。显式传入上下端面的棱引用一律
  CadError(reason='unsupported_edge_kind')，不做"看着像就凑合"的近似。
- 本模块**不写文档**：只把算出来的几何交回调用方（特征记录 / 内核），写盘口径由上层定。
"""

from __future__ import annotations

import copy
import math
import uuid as _uuid
from dataclasses import dataclass, field, replace as dataclass_replace
from typing import Any, Mapping, Sequence

from ..errors import CadError
from ..edit import modify as _edit_modify

__all__ = [
    "ARC_NAMESPACE",
    "CornerFillet",
    "FilletPlan",
    "corner_index_of_ref",
    "corner_refs",
    "corner_bound_nm",
    "plan_from_document",
    "plan_from_entities",
    "profile_entities",
    "shape_from_plan",
    "solid_from_document",
    "evaluated_fillet_feature",
]

#: 派生圆弧实体的确定性 uuid 命名空间（同一角 + 同一半径 => 同一 uuid，记录可逐字节复现；
#: 派生实体不写回草图，不与用户实体共享命名空间）。
ARC_NAMESPACE = _uuid.UUID("6f2f2a9e-6f5b-5d5e-9d3a-2f5b0f1c8a11")

MM = 1_000_000


# --------------------------------------------------------------------------------------
# 几何小工具（只做"读数"，不做圆角算法）
# --------------------------------------------------------------------------------------


def _line_points(entity: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]] | None:
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        return None
    try:
        p1 = (int(geom["x1"]), int(geom["y1"]))
        p2 = (int(geom["x2"]), int(geom["y2"]))
    except (KeyError, TypeError, ValueError):
        return None
    if p1 == p2:
        return None
    return p1, p2


def _other_end(points: tuple[tuple[int, int], tuple[int, int]], ref: tuple[int, int]) -> tuple[int, int]:
    return points[1] if points[0] == ref else points[0]


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return int(round(number)) if math.isfinite(number) else None


def _payload(document: Any) -> dict[str, Any]:
    """L1 文档 dict / docmodel.Document -> 载荷 dict（不改入参）。"""
    if hasattr(document, "to_payload") and callable(getattr(document, "to_payload")):
        payload = document.to_payload()
        return dict(payload) if isinstance(payload, Mapping) else {}
    if isinstance(document, Mapping):
        if "sketches" in document or "features" in document:
            return dict(document)
        if "entities" in document or "plane" in document:      # 裸草图
            return {"sketches": [document], "features": list(document.get("features") or [])}
    raise CadError("target is neither a document nor a sketch", reason="bad_document",
                   got=type(document).__name__)


def _pick_sketch(payload: Mapping[str, Any], uuid: Any = None) -> Mapping[str, Any]:
    """轮廓草图：显式 uuid 优先（必须存在），否则第一张草图；都没有抛 CadError。"""
    sketches = payload.get("sketches")
    if isinstance(sketches, Sequence):
        if uuid:
            for item in sketches:
                if isinstance(item, Mapping) and str(item.get("uuid") or "") == str(uuid):
                    return item
            raise CadError("unknown profile sketch", reason="unknown_sketch", sketch=str(uuid))
        if sketches and isinstance(sketches[0], Mapping):
            return sketches[0]
    raise CadError("document has no sketch to fillet", reason="no_sketch")


def _ordered_lines(sketch: Mapping[str, Any]) -> list[dict[str, Any]]:
    """草图的非 construction 直线 -> **闭合环顺序**的实体表；不是"全直线单闭合环"返回空表。

    与 cadcore.topo._loop_of 同口径（都要求全直线、闭合、无歧义），只是这里要的是
    **实体**（带 uuid，才能喂给 edit.modify.fillet）而不是点列。
    """
    raw: list[dict[str, Any]] = []
    entities = sketch.get("entities")
    if not isinstance(entities, Sequence):
        return []
    for item in entities:
        if not isinstance(item, Mapping) or bool(item.get("construction")):
            continue
        if str(item.get("kind") or "").strip().lower() != "line":
            return []
        if _line_points(item) is None:
            return []
        raw.append(dict(item))
    if len(raw) < 3:
        return []
    remaining = list(raw)
    head = remaining.pop(0)
    head_pts = _line_points(head)
    if head_pts is None:                                            # pragma: no cover - 上面已过滤
        return []
    chain = [head]
    cursor = head_pts[1]
    while remaining:
        matches = []
        for index, item in enumerate(remaining):
            points = _line_points(item)
            if points is not None and cursor in points:
                matches.append(index)
        if len(matches) != 1:
            return []                                   # 分叉 / 断裂 / 悬空 => 不猜
        nxt = remaining.pop(matches[0])
        points = _line_points(nxt)
        if points is None:                              # pragma: no cover - 上面已过滤
            return []
        chain.append(nxt)
        cursor = _other_end(points, cursor)
    if cursor != head_pts[0]:
        return []
    return chain


def _loop_nm(chain: Sequence[Mapping[str, Any]]) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for entity in chain:
        pts = _line_points(entity)
        if pts is None:                                 # pragma: no cover - 调用前已校验
            raise CadError("profile has a degenerate line", reason="profile_not_fillettable")
        points.append(pts[0])
    return tuple(points)


def _signed_area_nm2(loop: Sequence[tuple[int, int]]) -> int:
    total = 0
    for index in range(len(loop)):
        x1, y1 = loop[index]
        x2, y2 = loop[(index + 1) % len(loop)]
        total += x1 * y2 - x2 * y1
    return int(round(abs(total) / 2.0))


def _interior_deg(loop: Sequence[tuple[int, int]], index: int) -> float:
    n = len(loop)
    a, c, b = loop[(index - 1) % n], loop[index], loop[(index + 1) % n]
    v1 = (a[0] - c[0], a[1] - c[1])
    v2 = (b[0] - c[0], b[1] - c[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0.0 or n2 == 0.0:
        raise CadError("degenerate corner", reason="unsupported_corner", corner=index)
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(dot))


# --------------------------------------------------------------------------------------
# 持久拓扑引用 -> 轮廓角
# --------------------------------------------------------------------------------------


def corner_index_of_ref(ref: Any, loop_len: int) -> int | None:
    """topo:edge:...:lateral/i+lateral/j -> 轮廓角 index；不是"两相邻侧面的竖直棱"返回 None。

    侧面 lateral/i 覆盖轮廓边 loop[i] -> loop[i+1]；两面共享的顶点即轮廓角 k：
    (i+1) % n == j => k = j；否则 (j+1) % n == i => k = i。
    """
    if not isinstance(ref, str) or loop_len < 3:
        return None
    parts = ref.split(":", 3)
    if len(parts) != 4 or parts[0] != "topo" or parts[1] != "edge":
        return None
    atoms = parts[3].split("+")
    if len(atoms) != 2 or not all(atom.startswith("lateral/") for atom in atoms):
        return None
    try:
        i = int(atoms[0].split("/", 1)[1])
        j = int(atoms[1].split("/", 1)[1])
    except (IndexError, ValueError):
        return None
    if not (0 <= i < loop_len and 0 <= j < loop_len) or i == j:
        return None
    if (i + 1) % loop_len == j:
        return j
    if (j + 1) % loop_len == i:
        return i
    return None


def corner_refs(refs: Sequence[Any], loop_len: int) -> tuple[list[tuple[int, str]], list[str]]:
    """引用表 -> ([(角 index, ref)] 按角排序, 不支持的引用表)；重复角只留第一条。"""
    picked: dict[int, str] = {}
    unsupported: list[str] = []
    for ref in refs:
        index = corner_index_of_ref(ref, loop_len)
        if index is None:
            unsupported.append(str(ref))
            continue
        picked.setdefault(index, str(ref))
    return sorted(picked.items()), unsupported


# --------------------------------------------------------------------------------------
# 有界半径
# --------------------------------------------------------------------------------------


def _cot_half(loop: Sequence[tuple[int, int]], index: int) -> float:
    theta = math.radians(_interior_deg(loop, index))
    half = math.tan(theta / 2.0)
    if half <= 0.0:
        raise CadError("corner angle is degenerate", reason="unsupported_corner", corner=index)
    return 1.0 / half


def corner_bound_nm(loop: Sequence[tuple[int, int]], corners: Sequence[int]) -> tuple[int, int]:
    """这组角在半径上能开到多大：返回 (max_radius_nm, 限制边 index)。

    切线长 t_k = r * cot(theta_k / 2) 与 r 成正比；逐边检查两端切线长之和不超过边长
    （== 允许，那条边退化为一点，由调用方记进 dropped_edges）。没有任何限制边返回 (-1, -1)。
    """
    n = len(loop)
    selected = set(int(item) for item in corners)
    best: tuple[float, int] | None = None
    for edge in range(n):
        start = edge
        end = (edge + 1) % n
        coeff = 0.0
        if start in selected:
            coeff += _cot_half(loop, start)
        if end in selected:
            coeff += _cot_half(loop, end)
        if coeff <= 0.0:
            continue
        limit = math.dist(loop[start], loop[end]) / coeff
        if best is None or limit < best[0]:
            best = (limit, edge)
    if best is None:
        return -1, -1
    return int(math.floor(best[0] + 1e-6)), int(best[1])


# --------------------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CornerFillet:
    """一个角上的圆角几何（长度整数纳米，角度按 L1 口径存度）。"""

    index: int
    corner_nm: tuple[int, int]
    radius_nm: int
    center_nm: tuple[int, int]
    tangent_in_nm: tuple[int, int]
    tangent_out_nm: tuple[int, int]
    trim_in_nm: int
    trim_out_nm: int
    interior_deg: float
    arc: Mapping[str, Any]                 # L1 arc 实体（uuid 确定性派生）
    area_removed_nm2: int                  # 解析值 r^2 * (cot(theta/2) - (pi - theta)/2)
    arc_len_nm: int

    def as_dict(self) -> dict[str, Any]:
        geom = self.arc.get("geom") if isinstance(self.arc, Mapping) else {}
        return {
            "index": int(self.index),
            "corner_nm": list(self.corner_nm),
            "radius_nm": int(self.radius_nm),
            "center_nm": list(self.center_nm),
            "tangent_in_nm": list(self.tangent_in_nm),
            "tangent_out_nm": list(self.tangent_out_nm),
            "trim_in_nm": int(self.trim_in_nm),
            "trim_out_nm": int(self.trim_out_nm),
            "interior_deg": round(float(self.interior_deg), 6),
            "start_deg": float((geom or {}).get("start_deg", 0.0)),
            "end_deg": float((geom or {}).get("end_deg", 0.0)),
            "arc_radius_nm": int((geom or {}).get("r", -1)),
            "area_removed_nm2": int(self.area_removed_nm2),
            "arc_len_nm": int(self.arc_len_nm),
        }


@dataclass(frozen=True)
class FilletPlan:
    """一次"角上倒圆"的完整几何后果（可直接进特征记录，也可直接出实体）。"""

    radius_nm: int
    max_radius_nm: int
    bound_edge: int
    loop_nm: tuple[tuple[int, int], ...]
    corners: tuple[CornerFillet, ...]
    entities: tuple[dict[str, Any], ...]
    refs_used: tuple[str, ...]
    ignored_refs: tuple[str, ...]
    area_before_nm2: int
    area_after_nm2: int
    area_removed_nm2: int
    depth_nm: int
    symmetric: bool
    volume_removed_nm3: int
    dropped_edges: tuple[int, ...]
    status: str = "measured"
    notes: tuple[str, ...] = field(default=())

    @property
    def arc_count(self) -> int:
        return len(self.corners)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "radius_nm": int(self.radius_nm),
            "max_radius_nm": int(self.max_radius_nm),
            "bound_edge": int(self.bound_edge),
            "corner_indices": [int(item.index) for item in self.corners],
            "refs_used": list(self.refs_used),
            "unused_refs": list(self.ignored_refs),
            "area_before_nm2": int(self.area_before_nm2),
            "area_after_nm2": int(self.area_after_nm2),
            "area_removed_nm2": int(self.area_removed_nm2),
            "depth_nm": int(self.depth_nm),
            "symmetric": bool(self.symmetric),
            "volume_removed_nm3": int(self.volume_removed_nm3),
            "dropped_edges": [int(item) for item in self.dropped_edges],
            "arc_count": int(self.arc_count),
            "corners": [item.as_dict() for item in self.corners],
            "entities": [dict(item) for item in self.entities],
        }


# --------------------------------------------------------------------------------------
# 主算法
# --------------------------------------------------------------------------------------


def _as_radius(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CadError("radius must be an integer number of nanometres", reason="bad_radius",
                       got=repr(value)[:48])
    if value <= 0:
        raise CadError("radius must be positive", reason="bad_radius", radius_nm=value)
    return int(value)


def plan_from_entities(chain: Sequence[Mapping[str, Any]], corners: Sequence[int], radius_nm: Any,
                       *, depth_nm: int = -1, symmetric: bool = False,
                       refs_used: Sequence[str] = ()) -> FilletPlan:
    """闭合直线环 + 角 index 表 + 半径 -> FilletPlan（真正的裁剪 + 圆弧过渡）。

    chain 必须是 _ordered_lines 口径的单闭合直线环；corners 是环上的角 index。
    半径超界抛 CadError(reason='radius_exceeds_bound')。
    """
    radius = _as_radius(radius_nm)
    loop = _loop_nm(chain)
    n = len(loop)
    if n < 3:
        raise CadError("profile is not a closed line loop", reason="profile_not_fillettable",
                       lines=len(chain))
    indices = sorted(set(int(item) for item in corners))
    if not indices:
        raise CadError("no filletable corner was selected", reason="no_fillet_edges")
    for index in indices:
        if not 0 <= index < n:
            raise CadError("corner index out of range", reason="no_fillet_edges",
                           corner=index, loop_len=n)
    for index in indices:            # 凸角才裁料；共线/退化角显式拒绝
        a, c, b = loop[(index - 1) % n], loop[index], loop[(index + 1) % n]
        cross = (c[0] - a[0]) * (b[1] - c[1]) - (c[1] - a[1]) * (b[0] - c[0])
        if cross == 0:
            raise CadError("corner is collinear", reason="unsupported_corner", corner=index)
    bound, bound_edge = corner_bound_nm(loop, indices)
    if bound < 0:
        raise CadError("no bounded radius could be computed", reason="no_fillet_edges")
    if radius > bound:
        raise CadError("fillet radius exceeds the bounded radius", reason="radius_exceeds_bound",
                       radius_nm=radius, max_radius_nm=bound, bound_edge=bound_edge,
                       loop_len=n, corners=list(indices))
    base = {"uuid": "fillet-base", "plane": "XY", "entities": [dict(item) for item in chain],
            "constraints": [], "dimensions": []}
    corner_objs: list[CornerFillet] = []
    trims: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
    for k in indices:
        work = copy.deepcopy(base)
        prev_uuid = str(chain[(k - 1) % n].get("uuid") or "")
        this_uuid = str(chain[k].get("uuid") or "")
        created = _edit_modify.fillet(work, prev_uuid, this_uuid, radius_nm=radius)
        created_set = set(str(item) for item in created)
        work_lines = {str(item.get("uuid")): item for item in work.get("entities") or []
                      if isinstance(item, Mapping)}
        arc = next((item for item in work.get("entities") or []
                    if isinstance(item, Mapping) and str(item.get("uuid")) in created_set), None)
        if arc is None or str(arc.get("kind") or "").lower() != "arc":
            raise CadError("fillet produced no arc", reason="degenerate_fillet", corner=k)
        corner_pt = loop[k]
        prev_pts = _line_points(chain[(k - 1) % n])
        this_pts = _line_points(chain[k])
        if prev_pts is None or this_pts is None:        # pragma: no cover - 调用前已校验
            raise CadError("profile has a degenerate line", reason="profile_not_fillettable")
        now_prev = _line_points(work_lines.get(prev_uuid) or {})
        now_this = _line_points(work_lines.get(this_uuid) or {})
        if now_prev is None or now_this is None:
            raise CadError("fillet collapsed a profile edge", reason="degenerate_fillet", corner=k)
        tin = _other_end(now_prev, _other_end(prev_pts, corner_pt))
        tout = _other_end(now_this, _other_end(this_pts, corner_pt))
        geom = dict(arc.get("geom") or {})
        cx, cy = _to_int(geom.get("cx")), _to_int(geom.get("cy"))
        arc_r = _to_int(geom.get("r"))
        if cx is None or cy is None or arc_r is None:
            raise CadError("fillet arc has no readable geometry", reason="degenerate_fillet", corner=k)
        theta = math.radians(_interior_deg(loop, k))
        removed = arc_r * arc_r * (1.0 / math.tan(theta / 2.0) - (math.pi - theta) / 2.0)
        arc_entity = {"uuid": str(_uuid.uuid5(ARC_NAMESPACE, "arc:%d:%d" % (k, radius))),
                      "kind": "arc", "layer": str(arc.get("layer") or "0"),
                      "construction": False, "geom": geom}
        trims[k] = (tin, tout)
        corner_objs.append(CornerFillet(
            index=k, corner_nm=corner_pt, radius_nm=radius, center_nm=(cx, cy),
            tangent_in_nm=tin, tangent_out_nm=tout,
            trim_in_nm=int(round(math.dist(corner_pt, tin))),
            trim_out_nm=int(round(math.dist(corner_pt, tout))),
            interior_deg=math.degrees(theta), arc=arc_entity,
            area_removed_nm2=int(round(removed)), arc_len_nm=int(round(arc_r * (math.pi - theta)))))
    entities, dropped = _compose(chain, loop, trims, corner_objs)
    area_before = _signed_area_nm2(loop)
    sampled: list[tuple[int, int]] = []
    for entity in entities:
        sampled.extend(_entity_loop_points(entity))
    outline = _drop_closing(sampled)
    area_after = _signed_area_nm2(outline) if len(outline) >= 3 else -1
    removed_exact = sum(item.area_removed_nm2 for item in corner_objs)
    depth = int(depth_nm)
    volume = removed_exact * depth if depth > 0 else -1
    return FilletPlan(
        radius_nm=radius, max_radius_nm=bound, bound_edge=bound_edge, loop_nm=loop,
        corners=tuple(corner_objs), entities=tuple(dict(item) for item in entities),
        refs_used=tuple(str(item) for item in refs_used), ignored_refs=(), area_before_nm2=area_before,
        area_after_nm2=area_after, area_removed_nm2=int(removed_exact), depth_nm=depth,
        symmetric=bool(symmetric), volume_removed_nm3=int(volume), dropped_edges=tuple(dropped),
        notes=("area_after_nm2 is sampled at the kernel arc resolution; -1 when unmeasured",))


def _compose(chain: Sequence[Mapping[str, Any]], loop: Sequence[tuple[int, int]],
             trims: Mapping[int, tuple[tuple[int, int], tuple[int, int]]],
             corner_objs: Sequence[CornerFillet]) -> tuple[list[dict[str, Any]], list[int]]:
    """按环顺序合成"裁剪后的直线 + 真实圆弧"实体表；被两角吃光的边显式丢弃。"""
    n = len(loop)
    arcs = {item.index: item for item in corner_objs}
    entities: list[dict[str, Any]] = []
    dropped: list[int] = []
    for edge in range(n):
        pts = _line_points(chain[edge])
        if pts is None:                                 # pragma: no cover - 调用前已校验
            raise CadError("profile has a degenerate line", reason="profile_not_fillettable")
        start = trims[edge][1] if edge in trims else pts[0]
        nxt = (edge + 1) % n
        end = trims[nxt][0] if nxt in trims else pts[1]
        if start == end:
            dropped.append(edge)
        else:
            entities.append({"uuid": str(chain[edge].get("uuid") or ""), "kind": "line",
                             "layer": str(chain[edge].get("layer") or "0"), "construction": False,
                             "geom": {"x1": int(start[0]), "y1": int(start[1]),
                                      "x2": int(end[0]), "y2": int(end[1])}})
        if nxt in arcs:
            entities.append(dict(arcs[nxt].arc))
    return entities, dropped


def _entity_loop_points(entity: Mapping[str, Any]) -> list[tuple[int, int]]:
    """L1 实体 -> 轮廓点（与内核 nano._entity_loop 同口径：直线两端点 / 圆弧按内核分辨率采样）。

    只用来量"合成后轮廓的采样面积"，不是新的几何真源：圆弧按 24 段 / 90 度采样，
    与内核一致，所以量出来的面积与内核实测同量级、可逐点对拍。
    """
    kind = str(entity.get("kind") or "").lower()
    geom = entity.get("geom") or {}
    if kind == "line":
        return [(int(geom["x1"]), int(geom["y1"])), (int(geom["x2"]), int(geom["y2"]))]
    if kind == "arc":
        cx, cy, r = float(geom["cx"]), float(geom["cy"]), float(geom["r"])
        start, end = float(geom.get("start_deg") or 0.0), float(geom.get("end_deg") or 0.0)
        while end <= start:
            end += 360.0
        sweep = end - start
        segments = max(4, int(24 * sweep / 90.0) + 1)
        points = []
        for i in range(segments + 1):
            angle = math.radians(start + sweep * i / segments)
            points.append((int(round(cx + r * math.cos(angle))), int(round(cy + r * math.sin(angle)))))
        return points
    return []


def _drop_closing(points: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out = list(points)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def plan_from_document(document: Any, *, refs: Sequence[Any], radius_nm: Any,
                       sketch_uuid: Any = None, require_all_refs: bool = False) -> FilletPlan:
    """L1 文档 + 持久边引用 + 半径 -> FilletPlan（读轮廓、深度与拓扑引用）。

    require_all_refs=True（显式传边时用）：引用表里只要有一条不是"两相邻侧面的竖直棱"
    就抛 CadError(reason='unsupported_edge_kind')；False（缺省选边）只取支持的角边，
    被忽略的引用记进 unused_refs（下游看得见，不静默丢）。
    """
    payload = _payload(document)
    sketch = _pick_sketch(payload, sketch_uuid)
    chain = _ordered_lines(sketch)
    if not chain:
        raise CadError("profile is not a single closed line loop", reason="profile_not_fillettable")
    loop = _loop_nm(chain)
    picked, unsupported = corner_refs(list(refs), len(loop))
    if unsupported and require_all_refs:
        raise CadError("edge reference is not a corner edge between two lateral faces",
                       reason="unsupported_edge_kind", refs=unsupported[:4])
    if not picked:
        raise CadError("no corner edge was referenced", reason="no_fillet_edges",
                       refs_total=len(list(refs)))
    depth, symmetric = _extent(payload)
    plan = plan_from_entities(chain, [index for index, _ in picked], radius_nm,
                              depth_nm=depth, symmetric=symmetric,
                              refs_used=[ref for _, ref in picked])
    return dataclass_replace(plan, ignored_refs=tuple(unsupported))


def _extent(payload: Mapping[str, Any]) -> tuple[int, bool]:
    """父拉伸特征 -> (深度 nm, 是否对称)；找不到记 -1（未采，不用 0 冒充）。"""
    features = payload.get("features")
    if not isinstance(features, Sequence):
        return -1, False
    for feature in features:
        if not isinstance(feature, Mapping) or bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() != "extrude":
            continue
        params = feature.get("params") or {}
        depth = params.get("distance_nm")
        if isinstance(depth, bool) or not isinstance(depth, int) or depth <= 0:
            continue
        return int(depth), bool(params.get("symmetric"))
    return -1, False


# --------------------------------------------------------------------------------------
# 出实体（真几何）
# --------------------------------------------------------------------------------------


def _default_kernel() -> Any:
    from ..kernel import base as _kbase
    for name in ("nano", "auto"):
        try:
            kernel = _kbase.get_kernel(name)
        except Exception:
            continue
        if kernel is not None and bool(kernel.available()):
            return kernel
    raise CadError("no geometry kernel is available", reason="kernel_unavailable")


def shape_from_plan(plan: FilletPlan, *, kernel: Any = None) -> Any:
    """已倒圆轮廓 -> 真实体（kernel.extrude）；深度未采（-1）时抛 CadError。"""
    if not plan.entities:
        raise CadError("fillet plan has no geometry", reason="empty_profile")
    if plan.depth_nm <= 0:
        raise CadError("fillet plan has no measured depth", reason="no_solid_feature",
                       depth_nm=plan.depth_nm)
    engine = kernel if kernel is not None else _default_kernel()
    profile = {"entities": [dict(item) for item in plan.entities]}
    return engine.extrude(profile, distance_nm=int(plan.depth_nm), symmetric=bool(plan.symmetric))


def evaluated_fillet_feature(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """文档里最后一个未抑制的 fillet 特征（没有则 None）。"""
    features = payload.get("features")
    if not isinstance(features, Sequence):
        return None
    last: Mapping[str, Any] | None = None
    for feature in features:
        if not isinstance(feature, Mapping) or bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() == "fillet":
            last = feature
    return last


def solid_from_document(document: Any, *, kernel: Any = None) -> Any:
    """L1 文档 -> 最后一个圆角特征产生的**真实体**（真几何的文档级入口）。

    记录里没有可用几何时抛 CadError(reason='fillet_geometry_unmeasured') —— 不回落成
    "父实体"，那会把"没算圆角"伪装成"圆角算出来跟原来一样"。
    """
    payload = _payload(document)
    feature = evaluated_fillet_feature(payload)
    if feature is None:
        raise CadError("document has no fillet feature", reason="no_fillet_feature")
    params = feature.get("params")
    geometry = params.get("fillet_geometry") if isinstance(params, Mapping) else None
    if not isinstance(geometry, Mapping) or str(geometry.get("status")) != "measured":
        raise CadError("fillet geometry is not measured", reason="fillet_geometry_unmeasured")
    entities = geometry.get("entities")
    if not isinstance(entities, Sequence) or not entities:
        raise CadError("fillet geometry has no entities", reason="fillet_geometry_unmeasured")
    depth = geometry.get("depth_nm")
    if isinstance(depth, bool) or not isinstance(depth, int) or depth <= 0:
        depth, symmetric = _extent(payload)
        if depth <= 0:
            raise CadError("fillet geometry has no measured depth", reason="no_solid_feature")
    else:
        symmetric = bool(geometry.get("symmetric"))
    engine = kernel if kernel is not None else _default_kernel()
    return engine.extrude({"entities": [dict(item) for item in entities]},
                          distance_nm=int(depth), symmetric=bool(symmetric))


def profile_entities(plan: FilletPlan) -> list[dict[str, Any]]:
    """已倒圆轮廓的实体表（拷贝，调用方随便改）。"""
    return [dict(item) for item in plan.entities]
