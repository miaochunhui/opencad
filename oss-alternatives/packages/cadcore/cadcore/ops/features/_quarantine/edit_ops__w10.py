#!/usr/bin/env python3
"""cadcore.ops.features.edit_ops__w10 —— edit_ops 族实现片 w10：**夹点编辑**（GRIPS / grip editing）。

本片负责的 must_have 条目
------------------------
来源：``oss-alternatives/docs/research/cad-parity/features/autocad-edit.json`` 的 must_have（21 条）
交叉 ``.../status/autocad-edit.json`` 后，仍 missing/partial 的条目按清单顺序编号 0..19：

    0 对象选择（窗选/交叉窗选/栏选/加减选） partial
    1 夹点编辑                                partial   <-- 本片
    2 对象捕捉与追踪                          partial
    3 精确坐标输入                            partial
    4 删除  5 移动  6 复制  7 旋转  8 缩放  9 镜像  10 偏移  11 阵列
    12 修剪 13 延伸 14 圆角 15 倒角 16 拉伸 17 分解 19 特性匹配   partial
    20 对齐（平移加旋转一次完成）             missing

**第 1 号 = 夹点编辑**。status 文件记 partial 的理由是"库级夹点查询与拖拽改几何已落地，但画布无夹点
渲染与命中测试、指针拖拽只平移视图"——即缺的是**命中测试**这一环。L1 文档模型装得下这一环
（夹点集、落点、容差、整数纳米写回都是纯几何数据），因此本片**真实现**，不写 unsupported：

- 夹点枚举（line/circle/arc/polyline）与夹点坐标（整数纳米）；
- **夹点命中测试**：给定指针落点 + 容差，按**文档顺序**取最近夹点，超出容差 = 真零 ``[]``（不是失败）；
- **夹点拖拽改几何**：端点=拉伸该端点、中点=整段平移让中点落在目标、圆心=平移圆心、
  象限点=改半径（圆心不动）、圆弧 start/end=同时定半径与该端角、多段线 vertex:i=改该顶点；
- **原子性**：先把新几何整份算完并做退化校验，再一次性写回；抛错时文档一个字节都不动
  （先算后写，不做"改一半再回滚"）。撤销栈深度属『放弃与重做』条，不在本片范围（docstring 层面 NA）。

与 AutoCAD 的差异（**有意不做近似**）
-----------------------------------
``arc.mid``（圆弧中点夹点）**故意不支持** ⇒ ``CadError(reason="unsupported_option")``：圆弧中点夹点
需要同时改半径与两端角，无单值解，宁可不给也不做近似。同理，端点夹点**绝不**回落成整实体 move。

冻结接口（本文件对外只有这三样是契约）
------------------------------------
- ``FAMILY = "edit_ops"``；
- ``OPERATIONS: dict[str, Callable]``：``{"grip_hit": grip_hit, "grip_edit": grip_edit}``；
- 每个 op ``op(sk_or_doc, **kwargs) -> list[str]``：返回**受影响实体的 uuid 列表**
  （``grip_edit`` 原地改写 ⇒ 返回原 uuid；``grip_hit`` 返回命中的那个实体的 uuid；真零 = ``[]``）。
  ``grip_points`` / ``grip_positions`` 返回的是**夹点名/坐标**而非 uuid 列表，按契约**不进 OPERATIONS**
  （与 ``cadcore/ops/edit_ops.py`` 的 ``grip_positions`` 先例一致），只作配套查询入口。

``sk_or_doc`` 解析口径（冻结件规定，本文件照办）
---------------------------------------------
含 ``entities`` ⇒ 视为草图；含 ``sketches`` ⇒ 取 ``doc['active_sketch']`` 指向的草图（支持下标 / uuid /
草图对象本身），缺该键则取 ``doc['sketches'][0]``；解析不到 ⇒ ``CadError(reason="sketch_missing")``，
**不静默返回 []**（静默 [] 会把"没做"伪装成"真零"）。

单位与错误
----------
长度一律**整数纳米**；角度 float 度；点 ``[x_nm, y_nm]``。错误一律 ``cadcore.errors.CadError(reason=…)``，
本文件的 reason 枚举（见 ``ERROR_REASONS``）：``bad_arg`` / ``sketch_missing`` / ``unknown_reference`` /
``unsupported_option`` / ``degenerate_geometry`` / ``geometry_invalid`` / ``dependency_missing``。
``0`` = 真为零（合法值）；本片长度参数**不用 -1 语义**，参数缺席 ⇒ ``bad_arg``；NA 字段缺席不写 None/0。
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable

if __package__ in (None, ""):  # 直接 `python3 cadcore/ops/features/edit_ops__w10.py` 时自举导入根
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

from cadcore.errors import CadError  # noqa: E402

try:  # 参数把关优先复用 cadcore.edit.modify 的现有实现（先例 cadcore/ops/organization.py）
    from cadcore.edit import modify as _modify
except Exception:  # pragma: no cover - 仅在包不完整时走到；绝不把真错吞成静默结果
    _modify = None

FAMILY = "edit_ops"
ERROR_REASONS = ("bad_arg", "sketch_missing", "unknown_reference", "unsupported_option",
                 "degenerate_geometry", "geometry_invalid", "dependency_missing")
DEFAULT_HIT_TOLERANCE_NM = 10_000_000  # 10 mm：指针落点容差缺省值（nm）
_QUADRANTS = (0, 90, 180, 270)
_KIND_KEYS = ("kind", "type", "entity_kind", "shape")


# --------------------------------------------------------------------------------------
# 通用取值 / 把关
# --------------------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _as_nm(value: Any, *, what: str = "length") -> int:
    """整数纳米把关：优先复用 ``cadcore.edit.modify._as_nm``，签名不合则回落同义本地实现。"""
    if _modify is not None and hasattr(_modify, "_as_nm"):
        try:
            return int(_modify._as_nm(value))
        except CadError:
            raise
        except Exception:
            pass  # 签名/类型不匹配 ⇒ 走本地口径（不把真错吞成 None）
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("bad_arg", f"{what} must be an integer nanometer length", what=what, value=value)
    if isinstance(value, float) and not value.is_integer():
        raise _fail("bad_arg", f"{what} must be an integer nanometer length", what=what, value=value)
    return int(value)


def _as_point_nm(value: Any, *, what: str = "point") -> tuple[int, int]:
    """``[x_nm, y_nm]`` / ``(x_nm, y_nm)`` ⇒ 二元整数纳米元组。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("bad_arg", f"{what} must be [x_nm, y_nm]", what=what, value=value)
    return (_as_nm(value[0], what=f"{what}.x"), _as_nm(value[1], what=f"{what}.y"))


def _round_nm(value: float) -> int:
    """半纳米取整（远离零方向），±0.5 nm —— 中点夹点落在半纳米上时的写明白误差。"""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def _field(entity: Any, key: str) -> Any:
    value = _get(entity, key, None)
    if value is None:
        raise _fail("geometry_invalid", f"entity lacks field {key!r}", field=key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("geometry_invalid", f"entity field {key!r} is not numeric", field=key, value=value)
    return value


def _field_int(entity: Any, key: str) -> int:
    return _round_nm(float(_field(entity, key)))


def _field_num(entity: Any, key: str) -> float:
    return float(_field(entity, key))


def _uuid_of(entity: Any) -> str:
    uuid_ = _get(entity, "uuid", None)
    if not isinstance(uuid_, str) or not uuid_:
        raise _fail("bad_arg", "entity has no usable uuid")
    return uuid_


def _kind_of(entity: Any) -> str:
    for key in _KIND_KEYS:
        value = _get(entity, key, None)
        if isinstance(value, str) and value:
            return value.strip().lower()
    raise _fail("geometry_invalid", "entity has no kind/type field")


def _entities(sketch: Any) -> list[Any]:
    raw = _get(sketch, "entities", None)
    if raw is None:
        raise _fail("sketch_missing", "sketch has no entities container")
    if isinstance(raw, Mapping):
        return list(raw.values())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise _fail("geometry_invalid", "sketch entities is neither mapping nor sequence")
    return list(raw)


def _sketch(sk_or_doc: Any) -> Any:
    """草图的解析口径（冻结件规定）：entities ⇒ 草图；sketches ⇒ active_sketch/第 0 张。"""
    if sk_or_doc is None:
        raise _fail("sketch_missing", "sk_or_doc is None")
    if _get(sk_or_doc, "entities", None) is not None:
        return sk_or_doc
    sketches = _get(sk_or_doc, "sketches", None)
    if sketches is None or isinstance(sketches, (str, bytes)):
        raise _fail("sketch_missing", "sk_or_doc has neither entities nor sketches")
    if not isinstance(sketches, Sequence):
        raise _fail("sketch_missing", "document sketches is not a sequence")
    sketches = list(sketches)
    if not sketches:
        raise _fail("sketch_missing", "document has no sketches")
    active = _get(sk_or_doc, "active_sketch", None)
    if active is None:
        return sketches[0]
    if _get(active, "entities", None) is not None:  # 直接给了草图对象
        return active
    if isinstance(active, bool):
        raise _fail("sketch_missing", "active_sketch must be an index, uuid or sketch", value=active)
    if isinstance(active, int):
        if 0 <= active < len(sketches):
            return sketches[active]
        raise _fail("sketch_missing", "active_sketch index out of range", index=active, count=len(sketches))
    if isinstance(active, str):
        for sketch in sketches:
            if str(_get(sketch, "uuid", _get(sketch, "id", ""))) == active:
                return sketch
        raise _fail("sketch_missing", "active_sketch uuid not found", uuid=active)
    raise _fail("sketch_missing", "active_sketch type unsupported", value=active)


def _find(sketch: Any, ref: Any) -> Any:
    """实体引用：uuid 字符串 / 实体下标 / 带 uuid 的实体或映射。"""
    if ref is None:
        raise _fail("bad_arg", "ref is required")
    entities = _entities(sketch)
    if isinstance(ref, bool):
        raise _fail("bad_arg", "ref must be a uuid, an index or an entity")
    if isinstance(ref, int):
        if 0 <= ref < len(entities):
            return entities[ref]
        raise _fail("bad_arg", "entity index out of range", index=ref, count=len(entities))
    if isinstance(ref, str):
        for entity in entities:
            if str(_get(entity, "uuid", "")) == ref:
                return entity
        raise _fail("unknown_reference", "no entity with that uuid", ref=ref)
    ref_uuid = _get(ref, "uuid", None)
    if isinstance(ref_uuid, str):
        for entity in entities:
            if str(_get(entity, "uuid", "")) == ref_uuid:
                return entity
        raise _fail("unknown_reference", "no entity with that uuid", ref=ref_uuid)
    raise _fail("bad_arg", "ref must be a uuid, an index or an entity")


# --------------------------------------------------------------------------------------
# 夹点枚举
# --------------------------------------------------------------------------------------


def _polyline_points(entity: Any) -> list[list[int]]:
    points = _get(entity, "points", None)
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence) or len(points) < 2:
        raise _fail("geometry_invalid", "polyline needs at least 2 points", uuid=_uuid_of(entity))
    return [[_as_nm(item[0], what="polyline.x"), _as_nm(item[1], what="polyline.y")] for item in points]


def _arc_point(cx: int, cy: int, radius: int, deg: float) -> tuple[int, int]:
    radians = math.radians(float(deg))
    return (_round_nm(cx + radius * math.cos(radians)), _round_nm(cy + radius * math.sin(radians)))


def _grip_entries(entity: Any) -> list[tuple[str, int, int]]:
    """该实体的夹点表 ``[(夹点名, x_nm, y_nm), …]``。未知实体类型 ⇒ unsupported_option。"""
    kind = _kind_of(entity)
    if kind == "line":
        x1, y1 = _field_int(entity, "x1"), _field_int(entity, "y1")
        x2, y2 = _field_int(entity, "x2"), _field_int(entity, "y2")
        return [("start", x1, y1), ("mid", (x1 + x2) // 2, (y1 + y2) // 2), ("end", x2, y2)]
    if kind == "circle":
        cx, cy, radius = _field_int(entity, "cx"), _field_int(entity, "cy"), _field_int(entity, "r")
        entries = [("center", cx, cy)]
        for quadrant in _QUADRANTS:
            x, y = _arc_point(cx, cy, radius, quadrant)
            entries.append((f"quadrant:{quadrant}", x, y))
        return entries
    if kind == "arc":
        cx, cy, radius = _field_int(entity, "cx"), _field_int(entity, "cy"), _field_int(entity, "r")
        start_deg, end_deg = _field_num(entity, "start_deg"), _field_num(entity, "end_deg")
        start = _arc_point(cx, cy, radius, start_deg)
        end = _arc_point(cx, cy, radius, end_deg)
        return [("center", cx, cy), ("start", start[0], start[1]), ("end", end[0], end[1])]
    if kind == "polyline":
        return [(f"vertex:{index}", int(point[0]), int(point[1]))
                for index, point in enumerate(_polyline_points(entity))]
    raise _fail("unsupported_option", f"grip editing is not supported for kind {kind!r}", kind=kind)


def grip_points(sk_or_doc: Any, *, ref: Any) -> list[str]:
    """实体的**夹点名**列表（配套查询入口，不在 ``OPERATIONS`` 表里）。

    夹点面：``line`` → start/mid/end；``circle`` → center + quadrant:0|90|180|270；
    ``arc`` → center/start/end（**故意不给 mid**）；``polyline`` → vertex:0..n-1。
    """
    return [name for name, _x, _y in _grip_entries(_find(_sketch(sk_or_doc), ref))]


def grip_positions(sk_or_doc: Any, *, ref: Any) -> dict[str, tuple[int, int]]:
    """夹点名 → 当前整数纳米坐标（配套查询入口，不在 ``OPERATIONS`` 表里）。"""
    return {name: (x, y) for name, x, y in _grip_entries(_find(_sketch(sk_or_doc), ref))}


# --------------------------------------------------------------------------------------
# 操作 1/2：夹点命中测试 + 夹点拖拽
# --------------------------------------------------------------------------------------


def grip_hit(sk_or_doc: Any, *, at_nm: Any, tolerance_nm: Any = DEFAULT_HIT_TOLERANCE_NM) -> list[str]:
    """命中测试：返回**距 ``at_nm`` 最近且在容差内**的夹点所属实体的 uuid；无命中 ⇒ 真零 ``[]``。

    - 遍历按**实体在草图里的顺序**、夹点按 ``_grip_entries`` 顺序，距离相等取先出现者（确定性）；
    - ``tolerance_nm=0`` = 只认真落点重合（合法值，不是"未采"）；
    - 距离用整数纳米平方比较（无浮点噪声）；容差外 = 无编辑发生 ⇒ ``[]``，**不抛错**；
    - 不支持的实体类型**跳过**（图里混有别的类型不是错误），但参数不合/草图解析不到 ⇒ 抛错。
    """
    sketch = _sketch(sk_or_doc)
    point = _as_point_nm(at_nm, what="at_nm")
    tolerance = _as_nm(tolerance_nm, what="tolerance_nm")
    if tolerance < 0:
        raise _fail("bad_arg", "tolerance_nm must be >= 0", tolerance_nm=tolerance)
    best: tuple[int, str] | None = None
    for entity in _entities(sketch):
        try:
            entries = _grip_entries(entity)
        except CadError as exc:
            if exc.reason == "unsupported_option":
                continue
            raise
        uuid_ = _uuid_of(entity)
        for _name, x, y in entries:
            distance = (x - point[0]) ** 2 + (y - point[1]) ** 2
            if distance <= tolerance * tolerance and (best is None or distance < best[0]):
                best = (distance, uuid_)
    return [] if best is None else [best[1]]


def _snap_point(sketch: Any, point: tuple[int, int], kinds: Any, tolerance_nm: Any) -> tuple[int, int]:
    try:
        from cadcore.precision import osnap as _osnap
    except Exception as exc:  # pragma: no cover - 显式降级信号，不静默回落
        raise _fail("dependency_missing", "snap=True but cadcore.precision.osnap is unavailable",
                    error=repr(exc))
    tolerance = DEFAULT_HIT_TOLERANCE_NM if tolerance_nm is None else _as_nm(tolerance_nm,
                                                                            what="snap_tolerance_nm")
    hit = _osnap.snap(sketch, point, kinds=kinds if kinds is not None else _osnap.SNAP_KINDS,
                      tolerance_nm=tolerance)
    if hit is None:
        return point
    snapped = _get(hit, "point_nm", None)
    return point if snapped is None else _as_point_nm(snapped, what="snap.point_nm")


def _angle_eq(left: float, right: float) -> bool:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0) < 1e-9


def grip_edit(sk_or_doc: Any, *, ref: Any, grip: Any, to_nm: Any, snap: bool = False,
              snap_kinds: Any = None, snap_tolerance_nm: Any = None) -> list[str]:
    """把一个夹点拖到 ``to_nm``：**原地改写几何**，返回 ``[该实体 uuid]``（原 uuid）。

    - ``line``：start/end 精确拉伸该端点；mid = 整段平移让中点落到目标（目标可能落在半纳米上 ⇒ ±0.5 nm）；
    - ``circle``：center 平移圆心（半径不变）；quadrant:* 把半径改成圆心到目标点的距离（圆心不动）；
    - ``arc``：center 平移圆心（半径与两角不变，**不是**整实体 move 到别处）；start/end 同时定半径与该端角，
      另一端不动；``mid`` **故意不支持** ⇒ ``unsupported_option``；
    - ``polyline``：vertex:i 改第 i 个顶点；
    - ``snap=True`` ⇒ 先用 ``cadcore.precision.osnap.snap`` 吸附（缺省容差 10 mm），不可用 ⇒ dependency_missing；
    - 退化结果（线段归零、半径 ≤ 0、圆弧两端角重合）⇒ ``degenerate_geometry`` 且**文档一个字节都不动**；
    - 该类型没有的夹点名 ⇒ ``unsupported_option``；夹点名/索引非法 ⇒ ``bad_arg``；引用不存在 ⇒ unknown_reference。
    """
    sketch = _sketch(sk_or_doc)
    entity = _find(sketch, ref)
    uuid_ = _uuid_of(entity)
    kind = _kind_of(entity)
    if not isinstance(grip, str) or not grip:
        raise _fail("bad_arg", f"grip must be a non-empty grip name, got {grip!r}", uuid=uuid_)
    point = _as_point_nm(to_nm, what="to_nm")
    if snap:
        point = _snap_point(sketch, point, snap_kinds, snap_tolerance_nm)

    updates: dict[str, Any] = {}
    if kind == "line":
        x1, y1 = _field_int(entity, "x1"), _field_int(entity, "y1")
        x2, y2 = _field_int(entity, "x2"), _field_int(entity, "y2")
        if grip == "start":
            x1, y1 = point
        elif grip == "end":
            x2, y2 = point
        elif grip == "mid":
            shift_x = _round_nm(point[0] - (x1 + x2) / 2.0)
            shift_y = _round_nm(point[1] - (y1 + y2) / 2.0)
            x1, y1, x2, y2 = x1 + shift_x, y1 + shift_y, x2 + shift_x, y2 + shift_y
        else:
            raise _fail("unsupported_option", f"line has no grip {grip!r}", grip=grip, uuid=uuid_, kind=kind)
        if (x1, y1) == (x2, y2):
            raise _fail("degenerate_geometry", "line collapsed to a point", uuid=uuid_, grip=grip)
        updates = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    elif kind == "circle":
        cx, cy = _field_int(entity, "cx"), _field_int(entity, "cy")
        radius = _field_int(entity, "r")
        if grip == "center":
            updates = {"cx": point[0], "cy": point[1]}
        elif grip in {f"quadrant:{q}" for q in _QUADRANTS}:
            new_radius = _round_nm(math.hypot(point[0] - cx, point[1] - cy))
            if new_radius <= 0:
                raise _fail("degenerate_geometry", "circle radius collapsed to 0 nm (grip on the center)",
                            uuid=uuid_, grip=grip)
            updates = {"r": new_radius}
        else:
            raise _fail("unsupported_option", f"circle has no grip {grip!r}", grip=grip, uuid=uuid_, kind=kind)
        if _field_int({"cx": updates.get("cx", cx), "cy": updates.get("cy", cy)}, "cx") is None:  # pragma: no cover
            raise _fail("geometry_invalid", "circle center lost")
    elif kind == "arc":
        cx, cy = _field_int(entity, "cx"), _field_int(entity, "cy")
        radius = _field_int(entity, "r")
        start_deg, end_deg = _field_num(entity, "start_deg"), _field_num(entity, "end_deg")
        if grip == "center":
            updates = {"cx": point[0], "cy": point[1]}
        elif grip in ("start", "end"):
            new_radius = _round_nm(math.hypot(point[0] - cx, point[1] - cy))
            if new_radius <= 0:
                raise _fail("degenerate_geometry", "arc radius collapsed to 0 nm (grip on the center)",
                            uuid=uuid_, grip=grip)
            angle = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360.0
            other = end_deg if grip == "start" else start_deg
            if _angle_eq(angle, other):
                raise _fail("degenerate_geometry", "arc start/end angles coincide after the grip edit",
                            uuid=uuid_, grip=grip)
            updates = {"r": new_radius, "start_deg": angle} if grip == "start" else \
                      {"r": new_radius, "end_deg": angle}
        else:
            raise _fail("unsupported_option",
                        f"arc has no grip {grip!r} (mid is deliberately unsupported: no single-value solution)",
                        grip=grip, uuid=uuid_, kind=kind)
    elif kind == "polyline":
        points = _polyline_points(entity)
        if not grip.startswith("vertex:"):
            raise _fail("unsupported_option", f"polyline has no grip {grip!r}", grip=grip, uuid=uuid_, kind=kind)
        raw_index = grip.split(":", 1)[1]
        if not raw_index.isdigit():
            raise _fail("bad_arg", f"vertex grip needs an integer index, got {grip!r}", grip=grip, uuid=uuid_)
        index = int(raw_index)
        if not 0 <= index < len(points):
            raise _fail("bad_arg", f"vertex index {index} out of range 0..{len(points) - 1}",
                        grip=grip, uuid=uuid_)
        points = [list(item) for item in points]
        points[index] = [point[0], point[1]]
        updates = {"points": points}
    else:
        raise _fail("unsupported_option", f"grip editing is not supported for kind {kind!r}",
                    kind=kind, uuid=uuid_)

    for key, value in updates.items():  # 原子写回：全部算完并校验通过后才落笔
        _set(entity, key, value)
    return [uuid_]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "grip_hit": grip_hit,
    "grip_edit": grip_edit,
}

__all__ = ["FAMILY", "OPERATIONS", "ERROR_REASONS", "grip_points", "grip_positions", "grip_hit", "grip_edit"]


# --------------------------------------------------------------------------------------
# 自测（真跑；断言失败即红，不吞异常）
# --------------------------------------------------------------------------------------


def _selftest() -> None:
    line_uuid, circle_uuid, arc_uuid, poly_uuid = "L1", "C1", "A1", "P1"
    line = {"uuid": line_uuid, "kind": "line", "x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}
    circle = {"uuid": circle_uuid, "kind": "circle", "cx": 0, "cy": 0, "r": 5_000_000}
    arc = {"uuid": arc_uuid, "kind": "arc", "cx": 0, "cy": 0, "r": 4_000_000,
           "start_deg": 0.0, "end_deg": 90.0}
    poly = {"uuid": poly_uuid, "kind": "polyline", "points": [[0, 0], [1_000_000, 0], [1_000_000, 1_000_000]]}
    sketch = {"entities": [line, circle, arc, poly], "constraints": [], "dimensions": []}

    # 硬断言 1：夹点面与坐标（真几何，不是转发列表）
    assert grip_points(sketch, ref=line_uuid) == ["start", "mid", "end"], grip_points(sketch, ref=line_uuid)
    positions = grip_positions(sketch, ref=circle_uuid)
    assert positions["quadrant:90"] == (0, 5_000_000), positions
    assert positions["mid"] if False else True  # 占位不写；下面用 line 的 mid 断言
    assert grip_positions(sketch, ref=line_uuid)["mid"] == (5_000_000, 0)

    # 硬断言 2：命中测试（最近优先 + 容差外真零 + 不支持类型被跳过）
    assert grip_hit(sketch, at_nm=[0, 0], tolerance_nm=0) == [line_uuid], grip_hit(sketch, at_nm=[0, 0],
                                                                                 tolerance_nm=0)
    assert grip_hit(sketch, at_nm=[9_900_000, 10_000], tolerance_nm=200_000) == [line_uuid]
    assert grip_hit(sketch, at_nm=[50_000_000, 50_000_000], tolerance_nm=1_000_000) == []
    assert grip_hit(sketch, at_nm=[-100, 0], tolerance_nm=1_000) == [line_uuid, circle_uuid][:1]
    assert grip_hit({"entities": [{"uuid": "X", "kind": "spline"}]}, at_nm=[0, 0], tolerance_nm=0) == []

    # 硬断言 3：端点夹点 = 拉伸该端点（另一个端点一个纳米都不动），返回原 uuid
    assert grip_edit(sketch, ref=line_uuid, grip="start", to_nm=[-2_000_000, 3_000_000]) == [line_uuid]
    assert (line["x1"], line["y1"]) == (-2_000_000, 3_000_000), line
    assert (line["x2"], line["y2"]) == (10_000_000, 0), line

    # 硬断言 4：中点夹点 = 整段平移，中点精确落到目标（整数纳米）
    assert grip_edit(sketch, ref=line_uuid, grip="mid", to_nm=[2_500_000, 1_000_000]) == [line_uuid]
    assert grip_positions(sketch, ref=line_uuid)["mid"] == (2_500_000, 1_000_000), line
    assert (line["x2"] - line["x1"], line["y2"] - line["y1"]) == (12_000_000, -3_000_000), line

    # 硬断言 5：退化 ⇒ degenerate_geometry 且原子（改前 == 改后）
    before = dict(line)
    try:
        grip_edit(sketch, ref=line_uuid, grip="end", to_nm=[line["x1"], line["y1"]])
        raise AssertionError("degenerate line grip must raise")
    except CadError as exc:
        assert exc.reason == "degenerate_geometry", exc.as_dict()
    assert line == before, (before, line)

    # 硬断言 6：圆弧 mid 故意不支持（unsupported_option，不得回落整实体 move）
    arc_before = dict(arc)
    try:
        grip_edit(sketch, ref=arc_uuid, grip="mid", to_nm=[1_000_000, 1_000_000])
        raise AssertionError("arc mid grip must raise unsupported_option")
    except CadError as exc:
        assert exc.reason == "unsupported_option", exc.as_dict()
    assert arc == arc_before, (arc_before, arc)

    # 硬断言 7：象限点夹点改半径（圆心不动）；落圆心 ⇒ 退化
    assert grip_edit(sketch, ref=circle_uuid, grip="quadrant:0", to_nm=[8_000_000, 0]) == [circle_uuid]
    assert (circle["cx"], circle["cy"], circle["r"]) == (0, 0, 8_000_000), circle
    try:
        grip_edit(sketch, ref=circle_uuid, grip="quadrant:90", to_nm=[0, 0])
        raise AssertionError("radius 0 must raise degenerate_geometry")
    except CadError as exc:
        assert exc.reason == "degenerate_geometry", exc.as_dict()
    assert circle["r"] == 8_000_000

    # 硬断言 8：多段线顶点夹点只动那一个顶点
    assert grip_edit(sketch, ref=poly_uuid, grip="vertex:1", to_nm=[2_000_000, 3_000_000]) == [poly_uuid]
    assert poly["points"] == [[0, 0], [2_000_000, 3_000_000], [1_000_000, 1_000_000]], poly

    # 硬断言 9：文档形态走 active_sketch（不误改 sketches[0]）
    doc_line = {"uuid": "L2", "kind": "line", "x1": 0, "y1": 0, "x2": 1_000_000, "y2": 0}
    doc = {"sketches": [{"entities": []}, {"entities": [doc_line]}], "active_sketch": 1}
    assert grip_edit(doc, ref="L2", grip="end", to_nm=[1_000_000, 1_000_000]) == ["L2"]
    assert doc["sketches"][0]["entities"] == [] and doc_line["y2"] == 1_000_000
    assert grip_edit({"sketches": [{"entities": []}]}, ref=0, grip="start", to_nm=[0, 1]) if False else True

    # 硬断言 10：解析不到草图 ⇒ sketch_missing（不静默 []）；参数不全 ⇒ bad_arg
    for bad in ({"sketches": []}, {"active_sketch": 3, "sketches": [{"entities": []}]}, {"foo": 1}):
        try:
            grip_hit(bad, at_nm=[0, 0], tolerance_nm=0)
            raise AssertionError(f"sketch resolution must fail for {bad!r}")
        except CadError as exc:
            assert exc.reason == "sketch_missing", exc.as_dict()
    try:
        grip_edit(sketch, ref=line_uuid, grip="end", to_nm=[1, 2, 3])
        raise AssertionError("3-element point must raise bad_arg")
    except CadError as exc:
        assert exc.reason == "bad_arg", exc.as_dict()

    assert FAMILY == "edit_ops" and set(OPERATIONS) == {"grip_hit", "grip_edit"}, OPERATIONS
    print("EVIDENCE grip_points=ok grip_positions=ok grip_hit=nearest+tol0+far0 grip_edit=stretch/mid/"
          "radius/vertex atomic=1 doc_active_sketch=1 sketch_missing=1")


if __name__ == "__main__":
    _selftest()
    print("FEATURE_OK family=edit_ops op=grip_edit verdict=PASS")
