#!/usr/bin/env python3
"""cadcore.ops.features.sw_pattern_refgeom__w13 —— 片模块：镜像实体（多实体零件）。

对应清单条目
------------
族 ``sw_pattern_refgeom`` 的 must_have 里**仍 missing/partial** 的条目按清单顺序重编号后，
本片负责 **#4 = 镜像实体（多实体零件）**（alias: Mirror Bodies / Mirror Solid / 合并实体）：

    0 线性阵列第二方向            missing
    1 阵列对象类型（特征/面/实体）  missing
    2 跳过与恢复实例              missing
    3 圆周阵列角度范围（含 360°）  missing
    4 镜像实体（多实体零件）  <-- 本文件
    5 装配体镜像零部件            missing
    6 阵列/镜像的参数化重建        missing
    7 质量属性                    partial

一句话用途
----------
把"**按实体（body）而不是按特征**做镜像，并且镜像结果是**另一个实体**还是**并回原实体**"
做成 L1 文档上的真实几何操作：对实体成员实体逐条做关于镜像线的**真反射**，
新实体写入 sketch 的 ``entities``（真 L1 数据），实体归属与度量写入族表。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_pattern_refgeom"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- ``mirror_body(sk_or_doc, **kwargs) -> list[str]``：第一个位置参数是 ``sk_or_doc``，
  返回**新建实体**的 uuid 列表；长度一律整数纳米（nm）；错误一律抛
  ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

实现口径（为什么这在本层是真实现，而不是桩）
--------------------------------------------
1. **真几何**：镜像 = 关于过 ``p1_nm``/``p2_nm`` 直线的反射 ``x' = P1 + 2*((x-P1)·v)/|v|² * v - (x-P1)``，
   用 ``Fraction`` 精确有理数计算后再落到整数纳米；每个点记录它是否**恰好落在纳米格上**
   （``points_exact`` / ``points_rounded``，轴对齐镜像 ⇒ ``points_rounded == 0``）。
2. **真数据后果**：新实体真的 append 进 ``sketch.entities``（同一份 L1 节点字段集合，
   键集合与原实体逐字相同，只有 uuid 与几何点变了）；实体表真的记录每个 body 的
   ``bbox_nm`` / ``signed_area2_nm2`` / ``area_nm2`` / ``centroid_nm``。
3. **多实体语义**：``merge=False``（缺省）⇒ 镜像结果是**独立 body**（多实体零件），
   ``len(bodies)`` +1；``merge=True`` ⇒ 镜像实体**并回源 body**（焊件并实体），
   ``len(bodies)`` 不变、成员数翻倍。这就是"镜像实体"与"镜像特征"的分界：
   前者以 body 为单位并带合并语义，后者只复制几何。
4. **三态口径**：body 面积/形心算不出时记 ``-1``（未采）并在 ``metric_reason`` 写明原因，
   **绝不用 0 冒充"没量出来"**；``area_nm2`` 是**无符号**面积，方向信息在
   ``signed_area2_nm2`` 里（镜像必然使其变号，这正是"真反射"的证据）。

L1 装得下什么、装不下什么（写下来，防止下游靠猜）
--------------------------------------------------
- **装得下**：几何（``sketch.entities[*].geom``）与操作日志/实体归属（族表）。
- **装不下**：L1 没有 body/solid 节点（``docmodel._NODE_FIELDS`` 里只有 entity/constraint/
  dimension/feature/annotation），所以 **body 归属是本模块族表里的一等记录**
  （``sk_or_doc["sw_pattern_refgeom__w13"].bodies``），而不是 L1 冻结字段。
  这条边界是本条 must_have 记 ``implemented`` 的**上限**：内核级/装配级多实体（布尔并、
  实体级质量属性、装配体镜像）**不在本片范围内**。
- 有意**不支持就报错**而不是猜：``arc`` 的镜像要翻转角向（start/end 角语义随约定而变），
  本层拿不到可靠约定 ⇒ ``CadError(reason="unsupported_entity_kind")``；
  未知引用 ``unknown_ref``；退化镜像线 ``degenerate_plane``；非整数纳米 ``invalid_length``。
- ``circle`` 的镜像**是精确的**（圆心反射、半径不变，圆本身对各向同性），
  但它让 body 不再是单一多边形 ⇒ 该 body 的 ``area_nm2 = -1``（未采），bbox 仍按 圆心±半径 给真值。
- ``spline`` 的镜像精确（反射是仿射映射，控制点反射 ⇒ 曲线反射），但控制点多边形**不是**曲线，
  故不参与面积/形心（``non_polygonal_member:spline``）；其 bbox 取控制点凸包，对曲线是**保守上界**。

族表落点（只加自己的键，不动 L1 冻结字段）
------------------------------------------
``{"schema": 1, "datum_planes": [], "bodies": [], "mirrors": []}`` 落在
``sk_or_doc["sw_pattern_refgeom__w13"]``（对象形态放同名属性）。
镜像面可以复用既有族模块 ``sw_pattern_refgeom`` 表里的 ``datum_planes`` 记录（**只读**）。

自测
----
``python3 cadcore/ops/features/sw_pattern_refgeom__w13.py``：硬断言含
①轴对齐镜像坐标逐位精确 ②面积守恒 ∧ 有向面积变号 ③多实体/合并两种语义 ④往返（对合）复原
⑤退化/未知引用/非整数/arc 四类错误 reason ⑥L1 不变式不新增违规类别。
"""

from __future__ import annotations

import copy
import os
import re
import sys
from fractions import Fraction
from typing import Any, Callable, Mapping, Sequence

FAMILY = "sw_pattern_refgeom"
_TABLE_KEY = "sw_pattern_refgeom__w13"
_FAMILY_TABLE_KEY = "sw_pattern_refgeom"
_SUPPORTED_KINDS = ("line", "polyline", "spline", "circle", "point")

__all__ = ["FAMILY", "OPERATIONS", "mirror_body"]


# --------------------------------------------------------------------------------------
# 基础导入（包内相对导入优先；收尾自测命令是"脚本直跑"，那时回落绝对导入）
# --------------------------------------------------------------------------------------


def _ensure_root_on_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


from cadcore.errors import CadError
from cadcore.sketch.constraints import new_uuid as _new_uuid


def _fail(reason: str, message: str, **details: Any) -> Any:
    return CadError(message, reason=reason, **details)


# --------------------------------------------------------------------------------------
# 容器读写（dict 形态 / 对象形态统一）
# --------------------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, Mapping):
        try:
            obj[key] = value  # type: ignore[index]
            return
        except Exception as exc:
            raise _fail("table_not_writable", "document mapping is read-only", key=key, detail=str(exc))
    setattr(obj, key, value)


def _table(container: Any) -> dict[str, Any]:
    table = _get(container, _TABLE_KEY)
    if table is None:
        table = {"schema": 1, "datum_planes": [], "bodies": [], "mirrors": []}
        _set(container, _TABLE_KEY, table)
    if not isinstance(table, dict):
        raise _fail("invalid_table", "family table is not a mapping", key=_TABLE_KEY)
    for name in ("datum_planes", "bodies", "mirrors"):
        if not isinstance(table.get(name), list):
            table[name] = []
    return table


def _sketch_of(sk_or_doc: Any, *, sketch_index: Any = 0, sketch_uuid: Any = None) -> Any:
    sketches = _get(sk_or_doc, "sketches")
    if sketches is None:
        return sk_or_doc
    pool = list(sketches)
    if not pool:
        raise _fail("invalid_document", "document has no sketches")
    if sketch_uuid is not None:
        for sketch in pool:
            if str(_get(sketch, "uuid") or "") == str(sketch_uuid):
                return sketch
        raise _fail("unknown_ref", "sketch_uuid not found", sketch_uuid=str(sketch_uuid))
    if isinstance(sketch_index, bool) or not isinstance(sketch_index, int):
        raise _fail("invalid_argument", "sketch_index must be an int", sketch_index=repr(sketch_index))
    if sketch_index < 0 or sketch_index >= len(pool):
        raise _fail("invalid_argument", "sketch_index out of range", sketch_index=sketch_index, sketches=len(pool))
    return pool[sketch_index]


def _entities_of(sketch: Any) -> list[Any]:
    entities = _get(sketch, "entities")
    if entities is None:
        raise _fail("invalid_document", "sketch has no entities", sketch_uuid=str(_get(sketch, "uuid") or ""))
    if not isinstance(entities, list):
        raise _fail("invalid_document", "sketch.entities must be a list", entities_type=type(entities).__name__)
    return entities


# --------------------------------------------------------------------------------------
# 数值 / 点 / 反射
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "length must be an integer number of nanometres",
                    field=what, got=repr(value))
    return int(value)


def _as_point_nm(raw: Any, what: str) -> tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return (_as_nm(raw[0], what + ".x"), _as_nm(raw[1], what + ".y"))
    raise _fail("invalid_argument", "point must be a 2-item (x_nm, y_nm) sequence",
                field=what, got=repr(raw))


def _is_int_point(value: Any, dims: Sequence[int]) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) not in dims:
        return False
    return all(isinstance(v, int) and not isinstance(v, bool) for v in value)


def _round_nm(value: Fraction) -> tuple[int, bool]:
    """有理数 → 整数纳米；返回 (值, 是否精确)。半值取偶，绝不静默截断。"""
    if value.denominator == 1:
        return (int(value), True)
    floor = value.numerator // value.denominator
    rem = value - floor
    half = Fraction(1, 2)
    if rem < half:
        out = floor
    elif rem > half:
        out = floor + 1
    else:
        out = floor if floor % 2 == 0 else floor + 1
    return (out, False)


def _reflect_xy(px: int, py: int, p1: tuple[int, int], p2: tuple[int, int]) -> tuple[Fraction, Fraction]:
    """关于过 p1/p2 直线的精确反射（Fraction）。两点重合 ⇒ degenerate_plane。"""
    vx = p2[0] - p1[0]
    vy = p2[1] - p1[1]
    den = vx * vx + vy * vy
    if den == 0:
        raise _fail("degenerate_plane", "mirror plane endpoints coincide",
                    p1_nm=list(p1), p2_nm=list(p2))
    dx = px - p1[0]
    dy = py - p1[1]
    num = dx * vx + dy * vy
    sx = Fraction(p1[0], 1) + Fraction(2 * num * vx, den) - dx
    sy = Fraction(p1[1], 1) + Fraction(2 * num * vy, den) - dy
    return sx, sy


def _reflect_point_any(point: Sequence[int], p1: tuple[int, int], p2: tuple[int, int]) -> tuple[list[int], bool]:
    sx, sy = _reflect_xy(int(point[0]), int(point[1]), p1, p2)
    ix, ex = _round_nm(sx)
    iy, ey = _round_nm(sy)
    if len(point) >= 3:
        return ([ix, iy, int(point[2])], bool(ex and ey))
    return ([ix, iy], bool(ex and ey))


def _transform_geom(geom: Any, p1: tuple[int, int], p2: tuple[int, int]) -> tuple[dict[str, Any], int, int, int]:
    """变换 geom 里所有 ``*_nm`` 的点字段；返回 (新 geom, 点数, 精确点数, 四舍五入点数)。

    - 半径等 ``*_nm`` 标量：等距变换下不变，原样保留；
    - 非 ``*_nm`` 键（kind/layer/构造位等）：逐字 deepcopy，键集合与源实体完全相同。
    """
    if not isinstance(geom, Mapping):
        raise _fail("unsupported_entity_geom", "entity geom must be a mapping", geom_type=type(geom).__name__)
    out: dict[str, Any] = {}
    n_points = n_exact = n_round = 0
    for key, value in geom.items():
        name = str(key)
        if not name.endswith("_nm"):
            out[key] = copy.deepcopy(value)
            continue
        if _is_int_point(value, (2, 3)):
            point, exact = _reflect_point_any(value, p1, p2)
            out[key] = point
            n_points += 1
            n_exact += 1 if exact else 0
            n_round += 0 if exact else 1
            continue
        if isinstance(value, (list, tuple)) and len(value) > 0 and all(_is_int_point(item, (2, 3)) for item in value):
            points = []
            for item in value:
                point, exact = _reflect_point_any(item, p1, p2)
                points.append(point)
                n_points += 1
                n_exact += 1 if exact else 0
                n_round += 0 if exact else 1
            out[key] = points
            continue
        out[key] = copy.deepcopy(value)
    return out, n_points, n_exact, n_round


# --------------------------------------------------------------------------------------
# 几何读取（body 度量）
# --------------------------------------------------------------------------------------

_KEYS_P1 = ("p1_nm", "a_nm", "start_nm")
_KEYS_P2 = ("p2_nm", "b_nm", "end_nm")


def _first_point(geom: Any, keys: Sequence[str]) -> tuple[int, int] | None:
    if not isinstance(geom, Mapping):
        return None
    for key in keys:
        value = geom.get(key)
        if _is_int_point(value, (2, 3)):
            return (int(value[0]), int(value[1]))
    return None


def _point_sequence(geom: Any, keys: Sequence[str]) -> list[tuple[int, int]]:
    if not isinstance(geom, Mapping):
        return []
    for key in keys:
        value = geom.get(key)
        if isinstance(value, (list, tuple)) and value and all(_is_int_point(item, (2, 3)) for item in value):
            return [(int(item[0]), int(item[1])) for item in value]
    return []


def _bbox_extend(bbox: tuple[int, int, int, int] | None, point: Sequence[int]) -> tuple[int, int, int, int]:
    x, y = int(point[0]), int(point[1])
    if bbox is None:
        return (x, y, x, y)
    return (min(bbox[0], x), min(bbox[1], y), max(bbox[2], x), max(bbox[3], y))


def _chain_loop(segments: Sequence[tuple[tuple[int, int], tuple[int, int]]]) -> list[tuple[int, int]] | None:
    """把线段首尾相接成**单一闭合环**；接不上/多环/顶点不足 ⇒ None（不猜）。"""
    remaining = list(segments)
    if not remaining:
        return None
    start, current = remaining.pop(0)
    loop = [start, current]
    while remaining:
        found = None
        for index, (a_pt, b_pt) in enumerate(remaining):
            if a_pt == loop[-1]:
                found = (index, b_pt)
                break
            if b_pt == loop[-1]:
                found = (index, a_pt)
                break
        if found is None:
            return None
        index, next_pt = found
        remaining.pop(index)
        if next_pt == loop[0]:
            return loop if (not remaining and len(loop) >= 3) else None
        loop.append(next_pt)
    return None


def _body_metrics(members: Sequence[Any]) -> dict[str, Any]:
    """成员实体 → body 度量（真算：按几何值，不看 uuid）。area<0 表示未采。"""
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    bbox: tuple[int, int, int, int] | None = None
    polygonal_ok = True
    reason = ""
    for entity in members:
        kind = str(_get(entity, "kind") or "").strip().lower()
        geom = _get(entity, "geom")
        if not isinstance(geom, Mapping):
            raise _fail("unsupported_entity_geom", "entity geom must be a mapping", kind=kind)
        if kind == "line":
            first = _first_point(geom, _KEYS_P1)
            second = _first_point(geom, _KEYS_P2)
            if first is None or second is None:
                polygonal_ok = False
                reason = reason or "line_endpoints_missing"
                continue
            segments.append((first, second))
            bbox = _bbox_extend(bbox, first)
            bbox = _bbox_extend(bbox, second)
        elif kind == "polyline":
            points = _point_sequence(geom, ("points_nm", "vertices_nm"))
            if len(points) < 2:
                polygonal_ok = False
                reason = reason or "polyline_points_missing"
                continue
            for point in points:
                bbox = _bbox_extend(bbox, point)
            for index in range(len(points) - 1):
                segments.append((points[index], points[index + 1]))
        elif kind == "spline":
            points = _point_sequence(geom, ("points_nm", "vertices_nm"))
            if not points:
                polygonal_ok = False
                reason = reason or "spline_points_missing"
                continue
            for point in points:
                bbox = _bbox_extend(bbox, point)
            polygonal_ok = False
            reason = reason or "non_polygonal_member:spline"
        elif kind == "circle":
            center = _first_point(geom, ("center_nm", "centre_nm", "c_nm"))
            radius = geom.get("radius_nm", geom.get("r_nm"))
            if center is None or isinstance(radius, bool) or not isinstance(radius, int):
                polygonal_ok = False
                reason = reason or "circle_geometry_missing"
                continue
            bbox = _bbox_extend(bbox, (center[0] - radius, center[1] - radius))
            bbox = _bbox_extend(bbox, (center[0] + radius, center[1] + radius))
            polygonal_ok = False
            reason = reason or "non_polygonal_member:circle"
        elif kind == "point":
            point = _first_point(geom, ("point_nm", "at_nm", "p1_nm"))
            if point is None:
                polygonal_ok = False
                reason = reason or "point_geometry_missing"
                continue
            bbox = _bbox_extend(bbox, point)
        else:
            raise _fail("unsupported_entity_kind", "body metrics support line/polyline/circle/point", kind=kind)

    record: dict[str, Any] = {
        "member_uuids": [str(_get(entity, "uuid") or "") for entity in members],
        "bbox_nm": list(bbox) if bbox is not None else None,
        "closed": False,
        "area_nm2": -1,
        "signed_area2_nm2": -1,
        "centroid_nm": None,
        "centroid_exact": False,
        "area_exact": False,
        "metric_reason": reason,
    }
    if not polygonal_ok:
        return record
    loop = _chain_loop(segments)
    if loop is None:
        record["metric_reason"] = record["metric_reason"] or "body_not_closed"
        return record
    total = 0
    for index in range(len(loop)):
        x1, y1 = loop[index]
        x2, y2 = loop[(index + 1) % len(loop)]
        total += x1 * y2 - x2 * y1
    record["closed"] = True
    record["signed_area2_nm2"] = total
    area, area_exact = _round_nm(Fraction(abs(total), 2))
    record["area_nm2"] = area
    record["area_exact"] = bool(area_exact)
    if total == 0:
        record["metric_reason"] = "degenerate_polygon"
        return record
    cx_num = 0
    cy_num = 0
    for index in range(len(loop)):
        x1, y1 = loop[index]
        x2, y2 = loop[(index + 1) % len(loop)]
        cross = x1 * y2 - x2 * y1
        cx_num += (x1 + x2) * cross
        cy_num += (y1 + y2) * cross
    den = 3 * total
    cx, cx_exact = _round_nm(Fraction(cx_num, den))
    cy, cy_exact = _round_nm(Fraction(cy_num, den))
    record["centroid_nm"] = [cx, cy]
    record["centroid_exact"] = bool(cx_exact and cy_exact)
    return record


# --------------------------------------------------------------------------------------
# 镜像面解析（可直接给两点，也可引用既有族表的 datum_planes —— 只读复用）
# --------------------------------------------------------------------------------------


def _plane_from_record(record: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if not isinstance(record, Mapping):
        return None
    first = _first_point(record, _KEYS_P1)
    second = _first_point(record, _KEYS_P2)
    if first is not None and second is not None:
        return first, second
    points = _point_sequence(record, ("points_nm",))
    if len(points) >= 2:
        return points[0], points[1]
    first = _first_point(record, ("plane_p1_nm",))
    second = _first_point(record, ("plane_p2_nm",))
    if first is not None and second is not None:
        return first, second
    origin = _first_point(record, ("origin_nm", "at_nm", "point_nm"))
    direction = _first_point(record, ("dir_nm", "direction_nm"))
    if origin is not None and direction is not None:
        return origin, (origin[0] + direction[0], origin[1] + direction[1])
    return None


def _plane_points(raw_p1: Any, raw_p2: Any, plane_ref: Any, sk_or_doc: Any) -> tuple[tuple[int, int], tuple[int, int]]:
    if raw_p1 is not None or raw_p2 is not None:
        if raw_p1 is None or raw_p2 is None:
            raise _fail("invalid_argument", "mirror plane needs both p1_nm and p2_nm")
        p1 = _as_point_nm(raw_p1, "p1_nm")
        p2 = _as_point_nm(raw_p2, "p2_nm")
        if p1 == p2:
            raise _fail("degenerate_plane", "mirror plane endpoints coincide", p1_nm=list(p1), p2_nm=list(p2))
        return p1, p2
    if plane_ref is None:
        raise _fail("invalid_argument", "mirror_body needs p1_nm/p2_nm or plane_ref")
    pools: list[Any] = []
    mine = _get(sk_or_doc, _TABLE_KEY)
    if isinstance(mine, Mapping) and isinstance(mine.get("datum_planes"), list):
        pools.append(mine["datum_planes"])
    family = _get(sk_or_doc, _FAMILY_TABLE_KEY)
    if isinstance(family, Mapping) and isinstance(family.get("datum_planes"), list):
        pools.append(family["datum_planes"])
    for pool in pools:
        for record in pool:
            if str(_get(record, "uuid") or "") != str(plane_ref):
                continue
            got = _plane_from_record(record)
            if got is None:
                raise _fail("invalid_plane_record", "datum plane record has no resolvable endpoints",
                            plane_ref=str(plane_ref))
            p1, p2 = got
            if p1 == p2:
                raise _fail("degenerate_plane", "mirror plane endpoints coincide",
                            p1_nm=list(p1), p2_nm=list(p2))
            return p1, p2
    raise _fail("unknown_ref", "plane_ref not found in family tables", plane_ref=str(plane_ref))


# --------------------------------------------------------------------------------------
# 成员解析 / 实体克隆
# --------------------------------------------------------------------------------------


def _resolve_members(entity_list: Sequence[Any], refs: Any) -> list[Any]:
    if refs is None:
        raise _fail("invalid_argument", "mirror_body needs refs=<uuid list>|'all'")
    if isinstance(refs, str):
        if refs == "all":
            if not entity_list:
                raise _fail("invalid_argument", "sketch has no entities to mirror")
            return list(entity_list)
        wanted = [refs]
    elif isinstance(refs, (list, tuple)):
        wanted = [str(item) for item in refs]
    elif isinstance(refs, (set, frozenset)):
        wanted = sorted(str(item) for item in refs)
    else:
        raise _fail("invalid_argument", "refs must be a uuid, a sequence of uuids, or 'all'",
                    refs_type=type(refs).__name__)
    if not wanted:
        raise _fail("invalid_argument", "refs is empty")
    index: dict[str, Any] = {}
    for entity in entity_list:
        uuid = str(_get(entity, "uuid") or "")
        if uuid:
            index[uuid] = entity
    out: list[Any] = []
    seen: set[str] = set()
    for uuid in wanted:
        if uuid in seen:
            continue
        entity = index.get(uuid)
        if entity is None:
            raise _fail("unknown_ref", "entity uuid not found in sketch", ref=uuid)
        seen.add(uuid)
        out.append(entity)
    return out


def _mirror_entity(entity_list: list[Any], entity: Any, p1: tuple[int, int],
                   p2: tuple[int, int]) -> tuple[Any, int, int, int]:
    kind = str(_get(entity, "kind") or "").strip().lower()
    if kind not in _SUPPORTED_KINDS:
        raise _fail("unsupported_entity_kind",
                    "mirror_body supports line/polyline/spline/circle/point", kind=kind)
    new_geom, n_points, n_exact, n_round = _transform_geom(_get(entity, "geom"), p1, p2)
    if n_points == 0:
        raise _fail("unsupported_entity_geom", "entity geom carries no *_nm point field to mirror", kind=kind)
    clone = copy.deepcopy(entity)
    _set(clone, "geom", new_geom)
    _set(clone, "uuid", str(_new_uuid()))
    entity_list.append(clone)
    return clone, n_points, n_exact, n_round


def _body_key(member_uuids: Sequence[Any]) -> str:
    return "|".join(sorted(str(uuid) for uuid in member_uuids))


def _find_body(table: Mapping[str, Any], member_uuids: Sequence[Any]) -> dict[str, Any] | None:
    key = _body_key(member_uuids)
    for record in table.get("bodies") or []:
        if isinstance(record, dict) and record.get("key") == key:
            return record
    return None


def _new_body_record(metrics: Mapping[str, Any], *, mirror_of: Any) -> dict[str, Any]:
    return {
        "uuid": str(_new_uuid()),
        "kind": "body",
        "mirror_of": mirror_of,
        "key": _body_key(metrics["member_uuids"]),
        "member_uuids": list(metrics["member_uuids"]),
        "bbox_nm": metrics["bbox_nm"],
        "closed": metrics["closed"],
        "area_nm2": metrics["area_nm2"],
        "signed_area2_nm2": metrics["signed_area2_nm2"],
        "centroid_nm": metrics["centroid_nm"],
        "centroid_exact": metrics["centroid_exact"],
        "area_exact": metrics["area_exact"],
        "metric_reason": metrics["metric_reason"],
        "merged_mirrors": [],
    }


def _members_by_uuid(entity_list: Sequence[Any], uuids: Sequence[Any]) -> list[Any]:
    index = {str(_get(entity, "uuid") or ""): entity for entity in entity_list}
    out: list[Any] = []
    for uuid in uuids:
        entity = index.get(str(uuid))
        if entity is None:
            raise _fail("unknown_ref", "body member uuid missing from sketch", ref=str(uuid))
        out.append(entity)
    return out


def _apply_metrics(record: dict[str, Any], metrics: Mapping[str, Any]) -> None:
    record["key"] = _body_key(metrics["member_uuids"])
    record["member_uuids"] = list(metrics["member_uuids"])
    record["bbox_nm"] = metrics["bbox_nm"]
    record["closed"] = metrics["closed"]
    record["area_nm2"] = metrics["area_nm2"]
    record["signed_area2_nm2"] = metrics["signed_area2_nm2"]
    record["centroid_nm"] = metrics["centroid_nm"]
    record["centroid_exact"] = metrics["centroid_exact"]
    record["area_exact"] = metrics["area_exact"]
    record["metric_reason"] = metrics["metric_reason"]


# --------------------------------------------------------------------------------------
# 操作
# --------------------------------------------------------------------------------------


def mirror_body(sk_or_doc: Any, *, refs: Any = None, p1_nm: Any = None, p2_nm: Any = None,
                plane_ref: Any = None, merge: Any = False, sketch_index: Any = 0,
                sketch_uuid: Any = None) -> list[str]:
    """按**实体（body）**镜像：整组实体一起反射，结果是新实体（独立 body 或并回源 body）。

    入参
    ----
    - ``refs``：源 body 的成员实体 uuid 列表（或 ``"all"``；单个 uuid 字符串亦可）；
    - ``p1_nm`` / ``p2_nm``：镜像线（2D）上的两个整数纳米点；或 ``plane_ref`` 引用
      ``datum_planes`` 记录（本模块表 或 既有族表 ``sw_pattern_refgeom``，只读）；
    - ``merge``：``False``（缺省）= 镜像结果是**独立 body**（多实体零件）；``True`` = 并回源 body；
    - ``sketch_index`` / ``sketch_uuid``：``sk_or_doc`` 是文档时定位草图（缺省第 0 张）。

    返回
    ----
    新建（镜像）实体的 uuid 列表，顺序与 ``refs`` 解析后的成员顺序一致。

    真数据后果
    ----------
    - 新实体 append 进 ``sketch.entities``（键集合与源实体逐字相同，几何点为真反射值）；
    - ``sk_or_doc["sw_pattern_refgeom__w13"].mirrors`` 追加一条操作记录（含
      ``points_transformed`` / ``points_exact`` / ``points_rounded`` / ``area_delta_nm2``）；
    - ``.bodies`` 记录被新建或就地更新（``merge=True`` 时成员翻倍、度量重算）。

    错误（``CadError.reason``）
    ----------------------------
    ``invalid_argument`` / ``invalid_length`` / ``degenerate_plane`` / ``unknown_ref`` /
    ``invalid_plane_record`` / ``unsupported_entity_kind``（arc 等）/ ``unsupported_entity_geom`` /
    ``invalid_document`` / ``invalid_table`` / ``table_not_writable``。
    """
    if not isinstance(merge, bool):
        raise _fail("invalid_argument", "merge must be a bool", merge=repr(merge))
    sketch = _sketch_of(sk_or_doc, sketch_index=sketch_index, sketch_uuid=sketch_uuid)
    entity_list = _entities_of(sketch)
    p1, p2 = _plane_points(p1_nm, p2_nm, plane_ref, sk_or_doc)
    members = _resolve_members(entity_list, refs)

    source_metrics = _body_metrics(members)

    new_uuids: list[str] = []
    new_members: list[Any] = []
    points_transformed = points_exact = points_rounded = 0
    for entity in members:
        clone, n_points, n_exact, n_round = _mirror_entity(entity_list, entity, p1, p2)
        new_uuids.append(str(_get(clone, "uuid") or ""))
        new_members.append(clone)
        points_transformed += n_points
        points_exact += n_exact
        points_rounded += n_round
    target_metrics = _body_metrics(new_members)

    table = _table(sk_or_doc)
    source_record = _find_body(table, source_metrics["member_uuids"])
    if source_record is None:
        source_record = _new_body_record(source_metrics, mirror_of=None)
        table["bodies"].append(source_record)

    mirror_uuid = str(_new_uuid())
    if merge:
        merged_uuids = list(source_record["member_uuids"]) + list(target_metrics["member_uuids"])
        merged_metrics = _body_metrics(_members_by_uuid(entity_list, merged_uuids))
        _apply_metrics(source_record, merged_metrics)
        source_record.setdefault("merged_mirrors", []).append(mirror_uuid)
        target_record = source_record
    else:
        target_record = _new_body_record(target_metrics, mirror_of=source_record["uuid"])
        table["bodies"].append(target_record)

    area_delta = -1
    if source_metrics["area_nm2"] >= 0 and target_metrics["area_nm2"] >= 0:
        area_delta = int(target_metrics["area_nm2"]) - int(source_metrics["area_nm2"])

    table["mirrors"].append({
        "uuid": mirror_uuid,
        "kind": "mirror_body",
        "plane": {"p1_nm": [p1[0], p1[1]], "p2_nm": [p2[0], p2[1]]},
        "source_body": source_record["uuid"],
        "target_body": target_record["uuid"],
        "merged": bool(merge),
        "member_uuids": list(new_uuids),
        "points_transformed": points_transformed,
        "points_exact": points_exact,
        "points_rounded": points_rounded,
        "area_delta_nm2": area_delta,
        "source_area_nm2": source_metrics["area_nm2"],
        "target_area_nm2": target_metrics["area_nm2"],
    })
    return new_uuids


OPERATIONS: dict[str, Callable[..., list[str]]] = {"mirror_body": mirror_body}


# --------------------------------------------------------------------------------------
# 自测（硬断言；不 skip、不放宽）
# --------------------------------------------------------------------------------------

_U_SKETCH = "b0d10000-0000-4000-8000-000000000001"
_U_L1 = "b0d10000-0000-4000-8000-000000000002"
_U_L2 = "b0d10000-0000-4000-8000-000000000003"
_U_L3 = "b0d10000-0000-4000-8000-000000000004"
_U_ARC = "b0d10000-0000-4000-8000-000000000005"
_U_PLANE = "b0d10000-0000-4000-8000-000000000006"
_MM = 1_000_000


def _fixture() -> dict[str, Any]:
    def line(uuid: str, a: tuple[int, int], b: tuple[int, int]) -> dict[str, Any]:
        return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False,
                "geom": {"p1_nm": [a[0], a[1]], "p2_nm": [b[0], b[1]]}}

    doc: dict[str, Any] = {
        "schema_version": 1,
        "unit": "mm",
        "sketches": [{"uuid": _U_SKETCH, "entities": [
            line(_U_L1, (0, 0), (10 * _MM, 0)),
            line(_U_L2, (10 * _MM, 0), (0, 10 * _MM)),
            line(_U_L3, (0, 10 * _MM), (0, 0)),
        ]}],
        "features": [],
    }
    doc[_TABLE_KEY] = {
        "schema": 1,
        "datum_planes": [{"uuid": _U_PLANE, "kind": "datum_plane",
                          "p1_nm": [0, 0], "p2_nm": [1 * _MM, 0]}],
        "bodies": [],
        "mirrors": [],
    }
    return doc


def _points_of(entities: Sequence[Any]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for entity in entities:
        geom = _get(entity, "geom") or {}
        for key in ("p1_nm", "p2_nm"):
            value = geom.get(key)
            if _is_int_point(value, (2, 3)):
                out.append((int(value[0]), int(value[1])))
    return sorted(out)


def _l1_problems(doc: Any) -> tuple[list[str], bool]:
    """L1 不变式问题清单；返回 (问题列表, 尺子是否可用)。尺子不可用 ⇒ ok=False（不假绿）。"""
    _ensure_root_on_path()
    try:
        from cadcore.docmodel import check_invariants  # type: ignore
    except Exception as exc:
        return (["docmodel_unavailable:" + type(exc).__name__ + ":" + str(exc)], False)
    try:
        return ([str(item) for item in check_invariants(doc)], True)
    except Exception as exc:
        return (["check_invariants_raised:" + type(exc).__name__ + ":" + str(exc)], False)


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, cond: Any, detail: str = "") -> None:
        checks.append((name, bool(cond), str(detail)))

    doc = _fixture()
    problems_before, ruler_ok = _l1_problems(doc)
    norm = lambda items: sorted({re.sub(r"\[[0-9]+\]", "[#]", item) for item in items})  # noqa: E731
    kinds_before = norm([item for item in problems_before if _TABLE_KEY not in item])

    new = mirror_body(doc, refs=[_U_L1, _U_L2, _U_L3], plane_ref=_U_PLANE)
    entities = doc["sketches"][0]["entities"]
    index = {entity["uuid"]: entity for entity in entities}

    check("contract.returns_uuid_list", isinstance(new, list) and all(isinstance(u, str) and u for u in new), repr(new))
    check("contract.new_uuid_count", len(new) == 3, "len=%d" % len(new))
    check("contract.uuids_are_new_and_present",
          not (set(new) & {_U_L1, _U_L2, _U_L3}) and all(u in index for u in new), repr(new))

    mirrored_l2 = index[new[1]]["geom"]
    check("geom.axis_mirror_exact",
          mirrored_l2 == {"p1_nm": [10 * _MM, 0], "p2_nm": [0, -10 * _MM]}, repr(mirrored_l2))
    check("geom.keys_identical_to_source", set(index[new[1]].keys()) == set(index[_U_L2].keys()))
    check("geom.point_ints_preserved",
          all(isinstance(v, int) and not isinstance(v, bool)
              for v in list(mirrored_l2["p1_nm"]) + list(mirrored_l2["p2_nm"])),
          repr(mirrored_l2))
    check("geom.non_point_fields_copied",
          all(index[new[i]][key] == index[[_U_L1, _U_L2, _U_L3][i]][key]
              for i in range(3) for key in ("kind", "layer", "construction")),
          repr(index[new[0]]))

    bodies = doc[_TABLE_KEY]["bodies"]
    check("body.multi_body_two_records", len(bodies) == 2, "bodies=%d" % len(bodies))
    source, target = bodies[0], bodies[1]
    check("body.mirror_of_link", target["mirror_of"] == source["uuid"], repr(target["mirror_of"]))
    check("body.area_preserved",
          source["area_nm2"] == target["area_nm2"] == 50_000_000_000_000,
          "%s/%s" % (source["area_nm2"], target["area_nm2"]))
    check("body.orientation_reversed",
          source["signed_area2_nm2"] == 100_000_000_000_000 and target["signed_area2_nm2"] == -100_000_000_000_000,
          "%s/%s" % (source["signed_area2_nm2"], target["signed_area2_nm2"]))
    check("body.centroid_mirrored",
          source["centroid_nm"] == [3_333_333, 3_333_333] and target["centroid_nm"] == [3_333_333, -3_333_333],
          "%s/%s" % (source["centroid_nm"], target["centroid_nm"]))
    check("body.bbox_nm",
          source["bbox_nm"] == [0, 0, 10 * _MM, 10 * _MM] and target["bbox_nm"] == [0, -10 * _MM, 10 * _MM, 0],
          "%s/%s" % (source["bbox_nm"], target["bbox_nm"]))

    operation = doc[_TABLE_KEY]["mirrors"][0]
    check("mirror.exact_on_nm_lattice",
          operation["points_transformed"] == 6 and operation["points_rounded"] == 0, repr(operation))
    check("mirror.area_delta_zero", operation["area_delta_nm2"] == 0, repr(operation["area_delta_nm2"]))

    back = mirror_body(doc, refs=new, p1_nm=(0, 0), p2_nm=(1, 0))
    entities = doc["sketches"][0]["entities"]
    index = {entity["uuid"]: entity for entity in entities}
    check("body.involution_adds_third_body", len(doc[_TABLE_KEY]["bodies"]) == 3,
          "bodies=%d" % len(doc[_TABLE_KEY]["bodies"]))
    check("geom.involution_round_trip",
          _points_of([index[u] for u in back]) == _points_of([index[_U_L1], index[_U_L2], index[_U_L3]]),
          "%s vs %s" % (_points_of([index[u] for u in back]), _points_of([index[_U_L1]])))

    bodies_before_merge = len(doc[_TABLE_KEY]["bodies"])
    mirror_body(doc, refs=[_U_L1, _U_L2, _U_L3], p1_nm=(0, 0), p2_nm=(1, 0), merge=True)
    bodies_after_merge = len(doc[_TABLE_KEY]["bodies"])
    merged_source = doc[_TABLE_KEY]["bodies"][0]
    last_op = doc[_TABLE_KEY]["mirrors"][-1]
    check("merge.no_new_body", bodies_after_merge == bodies_before_merge,
          "%d -> %d" % (bodies_before_merge, bodies_after_merge))
    check("merge.members_extended", len(merged_source["member_uuids"]) == 6, repr(merged_source["member_uuids"]))
    check("merge.logged_on_source_body",
          last_op["merged"] is True and last_op["target_body"] == merged_source["uuid"]
          and bool(merged_source["merged_mirrors"]), repr(last_op))
    check("merge.multi_region_area_unsampled",
          merged_source["area_nm2"] == -1 and merged_source["metric_reason"] == "body_not_closed",
          "%s/%s" % (merged_source["area_nm2"], merged_source["metric_reason"]))

    def reason_of(call: Callable[[], Any]) -> tuple[bool, str]:
        try:
            call()
        except CadError as exc:
            return (True, str(exc.reason))
        except Exception as exc:  # noqa: BLE001 - 非 CadError 也要当失败报出来
            return (False, type(exc).__name__ + ":" + str(exc))
        return (False, "no_error")

    ok, got = reason_of(lambda: mirror_body(doc, refs=[_U_L1], p1_nm=(0, 0), p2_nm=(0, 0)))
    check("error.degenerate_plane", ok and got == "degenerate_plane", got)
    ok, got = reason_of(lambda: mirror_body(doc, refs=["no-such-uuid"], p1_nm=(0, 0), p2_nm=(1, 0)))
    check("error.unknown_ref", ok and got == "unknown_ref", got)
    ok, got = reason_of(lambda: mirror_body(doc, refs=[_U_L1], p1_nm=(0, 0), p2_nm=(1.5, 0)))
    check("error.invalid_length", ok and got == "invalid_length", got)
    ok, got = reason_of(lambda: mirror_body(doc, refs=[_U_L1], p1_nm=(0, 0), p2_nm=(1, 0), merge="yes"))
    check("error.merge_not_bool", ok and got == "invalid_argument", got)
    ok, got = reason_of(lambda: mirror_body(doc, refs=[_U_L1], plane_ref="missing-plane"))
    check("error.unknown_plane_ref", ok and got == "unknown_ref", got)

    doc_arc = _fixture()
    doc_arc["sketches"][0]["entities"].append(
        {"uuid": _U_ARC, "kind": "arc", "layer": "0", "construction": False,
         "geom": {"center_nm": [0, 0], "radius_nm": 5 * _MM, "start_deg": 0.0, "end_deg": 90.0}})
    ok, got = reason_of(lambda: mirror_body(doc_arc, refs=[_U_ARC], p1_nm=(0, 0), p2_nm=(1, 0)))
    check("error.arc_refused_not_silently_wrong", ok and got == "unsupported_entity_kind", got)

    problems_after, ruler_ok_after = _l1_problems(doc)
    kinds_after = norm([item for item in problems_after if _TABLE_KEY not in item])
    check("l1.no_new_invariant_violation_class",
          ruler_ok and ruler_ok_after and kinds_after == kinds_before,
          "ruler=%s/%s kinds=%s -> %s" % (ruler_ok, ruler_ok_after, kinds_before, kinds_after))

    failed = [item for item in checks if not item[1]]
    for name, ok_flag, detail in checks:
        print("CHECK %-38s %s%s" % (name, "PASS" if ok_flag else "FAIL",
                                    (" " + detail) if (detail and not ok_flag) else ""))
    print("SELFTEST checks=%d failed=%d" % (len(checks), len(failed)))
    if failed:
        print("FEATURE_FAIL family=%s op=mirror_body verdict=FAIL" % FAMILY)
        return 1
    print("FEATURE_OK family=%s op=mirror_body verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
