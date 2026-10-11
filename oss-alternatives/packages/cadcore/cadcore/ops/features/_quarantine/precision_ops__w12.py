#!/usr/bin/env python3
"""cadcore.ops.features.precision_ops__w12 —— 精度族实现片：**圆心与象限点捕捉**（清单第 3 号）。

清单来源与编号（0-based，按 `docs/research/cad-parity/status/autocad-precision.json` 的
`items[]` 顺序；`docs/research/cad-parity/features/precision_ops.json` 在本仓**不存在**，
故编号取状态件的 items 顺序，该族 21 条 must_have 全为 missing/partial）：

====  ====================================  ========
 #     must_have 条目                        状态
====  ====================================  ========
 0     对象捕捉总开关                          partial
 1     端点捕捉                                partial
 2     中点捕捉                                partial
 **3** **圆心与象限点捕捉（本片实现）**         **partial → 本片给出 doc 模型级真实现**
 4     交点与外观交点捕捉                      partial
 5     垂足、切点、最近点捕捉                  partial
 6     延长线与平行捕捉                        missing
 7     插入点与节点捕捉                        missing
 8     单次临时捕捉（临时替代）                missing
 9     捕捉设置：优先级与忽略图案填充对象      partial
 10    AutoSnap 捕捉反馈（标记/磁吸/工具提示）  missing
 11    正交模式                                partial
 12    极轴追踪开关与增量角                    partial
 13    极轴附加角与角度测量基准                partial
 14    对象捕捉追踪                            partial
 15    栅格显示                                partial
 16    栅格捕捉（步进对齐）                    partial
 17    极轴捕捉（PolarSnap）                   partial
 18    动态输入                                partial
 19    坐标与直接距离输入                      partial
 20    状态栏开关与快捷键体系                  missing
====  ====================================  ========

本片负责 **第 3 号 = 圆心与象限点捕捉**，在 L1 文档模型上做**真几何**：

- 候选点 = 每个 `circle` / `arc` 实体的**圆心**，以及 **0/90/180/270 度四个象限点**
  （`cx + r*cos θ`、`cy + r*sin θ`，θ ∈ {0°,90°,180°,270°}）；
- `arc` 按 **CCW 起止角**做弧段过滤：只有落在这段弧上的象限点才是候选（半圆/整圆口径见
  `_in_sweep`）；起止角缺席的 `arc` 按整圆处理（不静默丢掉候选点）；
- 光标点到候选点的距离按欧氏距离（浮点纳米）算，**超过 `tolerance_nm` 一律不算命中**；
- 命中后**真的改数据**：把结果记录写进 L1 文档的
  ``doc["_ops"]["precision_ops"]["snap_center_quadrant"]``（与同族 spec 面
  `cadcore/ops/precision_ops.py` 的 `_ops` 结果通道同一约定），并给缺 uuid 的实体补一个持久
  `uuid`，返回 ``[结果记录 uuid, 被捕捉实体 uuid]``。

冻结接口（本文件对外契约）
--------------------------
- ``FAMILY = "precision_ops"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``（返回 uuid 列表，顺序在各自 docstring 写明）；
- 长度一律**整数纳米**（`*_nm` 键只能是 int，bool 不算 int）；角度用浮点度（`*_deg`）；
- 坏入参一律 ``cadcore.errors.CadError(reason=...)``，reason 机器可读：
  ``bad_arg``（形状/类型/范围坏）、``bad_target``（目标装不下结果通道）、
  ``ambiguous_target``（多草图未点名）、``unknown_reference``（点名的草图 uuid 不存在）、
  ``corrupt_entity``（circle/arc 的几何字段坏，不静默跳过）。

与 `settings.drafting.osnap` 的接线（真开关，不是装饰）
----------------------------------------------------
`:func:`_enabled_kinds`` 读同族 `osnap_set` 写下的 ``doc["settings"]["drafting"]["osnap"]``：

- 节点缺席 ⇒ 圆心 + 象限点**全开**（不静默关）；
- ``enabled == False`` ⇒ 总开关关，两个子模式都不命中（返回 ``[]``）；
- ``modes`` 显式列出 ⇒ 只按列出的子模式命中（列了 `center` 没列 `quadrant` ⇒ 象限点不命中）；
- ``modes`` 是空列表 ⇒ 全关。

有意限定的边界（写下来，防止下游靠猜）
------------------------------------
1. 本层只吃 **L1 实体映射/对象** 的 `circle` / `arc`：几何字段口径为
   ``center_nm``/``radius_nm`` 优先，``center``/``centre``/``radius``/``r`` 亦按**纳米**读
   （整数或有限数值，四舍五入到整数纳米）—— 不做单位推断，避免"看起来对"的静默错。
2. 其它实体类型（line/polyline/hatch/point…）**不产生候选**，直接跳过（不是候选，不是错误）。
3. `center_quadrant_probe` 是**纯查询**：不写任何键、不补 uuid、未命中返回 ``[]``（不伪造命中）；
   命中的实体若没有 uuid，也返回 ``[]``（查询层不替文档生成身份）。
4. 不做任何文件 I/O；存盘交给 io 层。
5. 本文件不抄第二份捕捉引擎：命中优先级、容差口径在这里按上面写死的规则实现，只服务
   圆心/象限两个子模式（同族 `precision_ops.py` 的 `osnap` 引擎负责其余子模式）。
6. 非 docstring 的字符串字面量一律 ASCII。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/precision_ops__w12.py 的引导
    import os as _os
    import sys as _sys

    _sys.path.insert(
        0,
        _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        ),
    )
    __package__ = "cadcore.ops.features"

import json as _json  # 自检用（冻结快照比对）
import math as _math
import sys as _sys
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）
from cadcore.sketch.constraints import new_uuid  # 绝对导入（隔离区深一层，`...sketch` 会解析成 cadcore.ops.sketch）

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "SNAP_MODES",
    "center_quadrant_probe",
    "snap_center_quadrant",
]

#: 族 id（冻结）。
FAMILY = "precision_ops"
#: 本片负责的清单条目（0-based，见模块 docstring 表）。
ITEM_INDEX = 3
ITEM_NAME = "center_and_quadrant_snap"

#: 本片实现的两个捕捉子模式（与 osnap 族的 `SUB_CENTER` / `SUB_QUADRANT` 同名口径）。
SNAP_MODES: tuple[str, ...] = ("center", "quadrant")
#: 象限点角度（度，CCW，+x 起）。
_QUADRANT_DEG: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)
#: 缺省捕捉容差 5 mm（整数纳米）。
_DEFAULT_TOLERANCE_NM = 5_000_000
#: 圆心类实体类型（其余类型不产生候选）。
_CENTER_KINDS = ("circle", "arc")
#: 几何字段候选键（前者优先）。
_CENTER_KEYS = ("center_nm", "center", "centre_nm", "centre")
_RADIUS_KEYS = ("radius_nm", "radius", "r_nm", "r")
_START_KEYS = ("start_angle_deg", "start_angle", "start_deg", "a0_deg")
_END_KEYS = ("end_angle_deg", "end_angle", "end_deg", "a1_deg")
_TYPE_KEYS = ("type", "kind", "entity_type", "shape")


# ======================================================================================
# 错误与入参把关
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _nm(value: Any, *, what: str) -> int:
    """长度入参 → 整数纳米（bool 不算 int；坏入参当场 `bad_arg`）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_arg", f"{what} must be an integer nanometre", what=what, value=repr(value))
    return int(value)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)`；接受 2 元序列或 `{x|x_nm, y|y_nm}` 映射。"""
    if isinstance(value, _MappingABC):
        x = value.get("x_nm", value.get("x", None))
        y = value.get("y_nm", value.get("y", None))
        if x is None or y is None:
            raise _fail("bad_arg", f"{what} mapping needs x/x_nm and y/y_nm",
                        what=what, value=repr(value))
        return (_nm(x, what=f"{what}.x"), _nm(y, what=f"{what}.y"))
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _fail("bad_arg", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    return (_nm(value[0], what=f"{what}.x"), _nm(value[1], what=f"{what}.y"))


def _tolerance(value: Any, *, what: str = "tolerance_nm") -> int:
    """容差入参 → 非负整数纳米（`0` 合法 = 只精确命中）。"""
    tol = _nm(value, what=what)
    if tol < 0:
        raise _fail("bad_arg", f"{what} must be >= 0 nm", what=what, value=int(tol))
    return int(tol)


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米：四舍五入、远离零（`±0.5` 对称，可复现）。"""
    if value >= 0.0:
        return int(_math.floor(value + 0.5))
    return int(_math.ceil(value - 0.5))


# ======================================================================================
# L1 结构访问（映射与对象两种形态都吃）
# ======================================================================================


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """映射取键 / 对象取属性（两种 L1 形态统一入口）。"""
    if isinstance(obj, _MappingABC):
        return obj[name] if name in obj else default
    return getattr(obj, name, default)


def _put(container: Any, key: str, value: Any) -> None:
    """写回容器（映射写键，对象写属性）；装不下 ⇒ `bad_target`（不静默丢结果）。"""
    if isinstance(container, _MutableMappingABC):
        container[key] = value
        return
    try:
        setattr(container, key, value)
    except Exception as exc:  # noqa: BLE001 - 任何写不进去都归 bad_target
        raise _fail("bad_target", "target cannot hold the ops result channel",
                    key=key, value_type=type(container).__name__) from exc


def _pick_container(sk_or_doc: Any, sketch: Any) -> Any:
    """`sk_or_doc` → 承载实体的容器：文档按 `sketch=<uuid>` 选草图，草图直接用自身。"""
    sketches = _attr(sk_or_doc, "sketches", None)
    if isinstance(sketches, _SequenceABC) and not isinstance(sketches, (str, bytes)):
        items = list(sketches)
        if not items:
            raise _fail("bad_target", "document has no sketches")
        if sketch is None:
            if len(items) != 1:
                raise _fail("ambiguous_target", "document has multiple sketches: pass sketch=<uuid>",
                            sketch_count=len(items))
            return items[0]
        if not isinstance(sketch, str) or not sketch:
            raise _fail("bad_arg", "sketch must be a sketch uuid string", value=repr(sketch))
        for item in items:
            if _attr(item, "uuid", None) == sketch:
                return item
        raise _fail("unknown_reference", "no sketch with this uuid in the document", sketch=sketch)
    return sk_or_doc


def _entities(container: Any) -> list[Any]:
    """容器上的 L1 实体列表（缺席 ⇒ `bad_target`，不当作空表冒充"没有可捕捉对象"）。"""
    ents = _attr(container, "entities", None)
    if isinstance(ents, (str, bytes)) or not isinstance(ents, _SequenceABC):
        raise _fail("bad_target", "target has no entity list", value_type=type(container).__name__)
    return list(ents)


# ======================================================================================
# 几何：circle / arc 的圆心与象限点候选
# ======================================================================================


def _entity_kind(entity: Any) -> str | None:
    """实体类型名（小写）；没有类型字段 ⇒ `None`（不是候选，也不是错误）。"""
    for key in _TYPE_KEYS:
        value = _attr(entity, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _center_nm(entity: Any) -> tuple[int, int]:
    """圆心 → 整数纳米（坏几何 ⇒ `corrupt_entity`）。"""
    for key in _CENTER_KEYS:
        value = _attr(entity, key, None)
        if value is None:
            continue
        if isinstance(value, _MappingABC):
            x = value.get("x_nm", value.get("x", None))
            y = value.get("y_nm", value.get("y", None))
        elif (not isinstance(value, (str, bytes)) and isinstance(value, _SequenceABC)
              and len(value) == 2):
            x, y = value[0], value[1]
        else:
            raise _fail("corrupt_entity", "entity center is malformed",
                        key=key, value=repr(value))
        if x is None or y is None:
            raise _fail("corrupt_entity", "entity center is incomplete",
                        key=key, value=repr(value))
        return (_nm_len(x, what=f"{key}.x"), _nm_len(y, what=f"{key}.y"))
    raise _fail("corrupt_entity", "circle/arc entity has no center",
                keys=list(_CENTER_KEYS))


def _nm_len(value: Any, *, what: str) -> int:
    """几何字段长度 → 整数纳米（有限数值四舍五入；坏值 ⇒ `corrupt_entity`）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("corrupt_entity", "entity geometry field is not a number",
                    what=what, value=repr(value))
    if not _math.isfinite(float(value)):
        raise _fail("corrupt_entity", "entity geometry field is not finite",
                    what=what, value=repr(value))
    return int(value) if isinstance(value, int) else _round_nm(float(value))


def _radius_nm(entity: Any) -> int:
    """半径 → 正整数纳米（`> 0`；坏几何 ⇒ `corrupt_entity`）。"""
    for key in _RADIUS_KEYS:
        value = _attr(entity, key, None)
        if value is None:
            continue
        radius = _nm_len(value, what=key)
        if radius <= 0:
            raise _fail("corrupt_entity", "circle/arc radius must be > 0",
                        key=key, value=repr(value))
        return radius
    raise _fail("corrupt_entity", "circle/arc entity has no radius", keys=list(_RADIUS_KEYS))


def _angle_deg(entity: Any, keys: tuple[str, ...]) -> float | None:
    """弧起止角 → 浮点度；缺席 ⇒ `None`（按整圆处理，不静默丢象限点）。"""
    for key in keys:
        value = _attr(entity, key, None)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _fail("corrupt_entity", "arc angle is not a number", key=key, value=repr(value))
        if not _math.isfinite(float(value)):
            raise _fail("corrupt_entity", "arc angle is not finite", key=key, value=repr(value))
        return float(value) % 360.0
    return None


def _in_sweep(deg: float, start: float, end: float) -> bool:
    """象限角 `deg` 是否落在 CCW 弧 `start → end` 上（含端点；`start == end` ⇒ 整圆）。"""
    sweep = (end - start) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    return (deg - start) % 360.0 <= sweep + 1e-9


def _candidates(entity: Any, index: int) -> list[dict[str, Any]]:
    """单个实体的候选点：圆心（rank 0）+ 弧段内的象限点（rank 1），不产生假候选。"""
    kind = _entity_kind(entity)
    if kind not in _CENTER_KINDS:
        return []
    center = _center_nm(entity)
    radius = _radius_nm(entity)
    out: list[dict[str, Any]] = [{
        "entity_index": int(index), "kind": "center", "point_nm": center,
        "quadrant_deg": None, "quadrant_index": -1,
    }]
    if kind == "arc":
        start = _angle_deg(entity, _START_KEYS)
        end = _angle_deg(entity, _END_KEYS)
        if start is None or end is None:
            start = end = None  # 起止角缺席 ⇒ 整圆口径
    else:
        start = end = None
    for quadrant_index, deg in enumerate(_QUADRANT_DEG):
        if start is not None and not _in_sweep(deg, start, end):
            continue
        point = (center[0] + _round_nm(float(radius) * _math.cos(_math.radians(deg))),
                 center[1] + _round_nm(float(radius) * _math.sin(_math.radians(deg))))
        out.append({
            "entity_index": int(index), "kind": "quadrant", "point_nm": point,
            "quadrant_deg": float(deg), "quadrant_index": int(quadrant_index),
        })
    return out


def _enabled_kinds(container: Any) -> set[str]:
    """读 `settings.drafting.osnap` 的开关（节点缺席 ⇒ 全开；不静默关用户能力）。"""
    settings = _attr(container, "settings", None)
    node = _attr(settings, "drafting", None) if settings is not None else None
    osnap = _attr(node, "osnap", None) if node is not None else None
    if not isinstance(osnap, _MappingABC):
        return set(SNAP_MODES)
    if osnap.get("enabled") is False:
        return set()
    modes = osnap.get("modes")
    if isinstance(modes, _SequenceABC) and not isinstance(modes, (str, bytes)):
        names = {str(item) for item in modes}
        if not names:
            return set()
        return {mode for mode in SNAP_MODES if mode in names}
    return set(SNAP_MODES)


def _best_hit(container: Any, cursor: tuple[int, int], tolerance_nm: int,
              kinds: set[str]) -> tuple[int, Any, dict[str, Any], float] | None:
    """容差内最近候选：排序键 `(距离, 实体下标, 圆心优先, 象限序号)`（可复现、无平手歧义）。"""
    best: tuple[int, Any, dict[str, Any], float] | None = None
    best_key: tuple[float, int, int, int] | None = None
    for index, entity in enumerate(_entities(container)):
        for cand in _candidates(entity, index):
            if cand["kind"] not in kinds:
                continue
            dist = _math.hypot(float(cursor[0] - cand["point_nm"][0]),
                               float(cursor[1] - cand["point_nm"][1]))
            if dist > float(tolerance_nm):
                continue
            key = (dist, int(index), 0 if cand["kind"] == "center" else 1, int(cand["quadrant_index"]))
            if best_key is None or key < best_key:
                best_key, best = key, (int(index), entity, cand, float(dist))
    return best


def _ensure_uuid(entity: Any) -> str:
    """取实体 uuid；没有就**真写一个进去**（后续引用靠它，不许只活在返回值里）。"""
    uid = _attr(entity, "uuid", None)
    if isinstance(uid, str) and uid:
        return uid
    uid = new_uuid()
    if isinstance(entity, _MutableMappingABC):
        entity["uuid"] = uid
    else:
        try:
            setattr(entity, "uuid", uid)
        except Exception as exc:  # noqa: BLE001 - 写不进去就是 bad_target
            raise _fail("bad_target", "entity cannot carry a uuid",
                        value_type=type(entity).__name__) from exc
    return uid


def _channel(container: Any, name: str, payload: dict[str, Any]) -> None:
    """结果通道：``doc["_ops"]["precision_ops"][<op>] = {...}``（与同族 spec 面同一约定）。"""
    ops = _attr(container, "_ops", None)
    if ops is None:
        ops = {}
        _put(container, "_ops", ops)
    if not isinstance(ops, _MutableMappingABC):
        raise _fail("bad_target", "_ops channel is not mutable",
                    value_type=type(ops).__name__)
    family = ops.get(FAMILY)
    if family is None:
        family = {}
        ops[FAMILY] = family
    if not isinstance(family, _MutableMappingABC):
        raise _fail("bad_target", "ops family channel is not mutable",
                    family=FAMILY, value_type=type(family).__name__)
    family[name] = dict(payload)


# ======================================================================================
# 操作（冻结接口之三）
# ======================================================================================


def snap_center_quadrant(sk_or_doc: Any, *, point_nm: Any,
                         tolerance_nm: Any = _DEFAULT_TOLERANCE_NM,
                         sketch: Any = None) -> list[str]:
    """**圆心与象限点捕捉**（清单第 3 号）：把一条光标点吸附到圆心/象限点。

    真几何：候选 = 每个 `circle`/`arc` 的圆心 + 该实体上 0/90/180/270 度象限点
    （`arc` 只取弧段内的象限点，见 `_in_sweep`）；命中 = 容差内欧氏距离最近者，
    平手按 `(距离, 实体下标, 圆心优先, 象限序号)` 定序 —— 同样入参永远同样结果。

    真数据后果：命中时写 ``doc["_ops"]["precision_ops"]["snap_center_quadrant"]``
    （含 `uuid`/`kind`/`entity_uuid`/`point_nm`/`quadrant_deg`/`cursor_nm`/`distance_nm`/
    `tolerance_nm`/`entity_index`/`schema`），并给缺 uuid 的实体补上持久 uuid。

    返回 ``[结果记录 uuid, 被捕捉实体 uuid]``；未命中（含总开关/子模式关掉、目标里没有
    圆心类实体）返回 ``[]`` —— **不返回假命中、不写记录**。坏入参/坏目标抛
    `CadError(reason='bad_arg'|'bad_target'|'ambiguous_target'|'unknown_reference'|'corrupt_entity')`。
    """
    cursor = _point(point_nm, what="point_nm")
    tolerance = _tolerance(tolerance_nm)
    container = _pick_container(sk_or_doc, sketch)
    kinds = _enabled_kinds(container)
    if not kinds:
        return []
    hit = _best_hit(container, cursor, tolerance, kinds)
    if hit is None:
        return []
    index, entity, cand, dist = hit
    uid = _ensure_uuid(entity)
    payload: dict[str, Any] = {
        "schema": 1,
        "op": "snap_center_quadrant",
        "uuid": new_uuid(),
        "entity_uuid": uid,
        "kind": str(cand["kind"]),
        "point_nm": [int(cand["point_nm"][0]), int(cand["point_nm"][1])],
        "cursor_nm": [int(cursor[0]), int(cursor[1])],
        "distance_nm": int(_round_nm(dist)),
        "tolerance_nm": int(tolerance),
        "entity_index": int(index),
    }
    if cand["quadrant_deg"] is not None:
        payload["quadrant_deg"] = float(cand["quadrant_deg"])  # 圆心命中 ⇒ 该键缺席（NA）
    _channel(container, "snap_center_quadrant", payload)
    return [str(payload["uuid"]), uid]


def center_quadrant_probe(sk_or_doc: Any, *, point_nm: Any,
                          tolerance_nm: Any = _DEFAULT_TOLERANCE_NM,
                          sketch: Any = None) -> list[str]:
    """纯查询版：命中的实体 uuid（未命中 / 子模式关 / 实体没有 uuid ⇒ ``[]``）。

    **不写任何键**（不建 `_ops` 通道、不补 uuid）——查询不许改文档；命中口径与
    `snap_center_quadrant` 完全一致，两者共用 `_best_hit`。
    """
    cursor = _point(point_nm, what="point_nm")
    tolerance = _tolerance(tolerance_nm)
    container = _pick_container(sk_or_doc, sketch)
    kinds = _enabled_kinds(container)
    if not kinds:
        return []
    hit = _best_hit(container, cursor, tolerance, kinds)
    if hit is None:
        return []
    uid = _attr(hit[1], "uuid", None)
    return [uid] if isinstance(uid, str) and uid else []


#: 操作表（冻结接口之二）：操作名 → 函数。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "snap_center_quadrant": snap_center_quadrant,
    "center_quadrant_probe": center_quadrant_probe,
}


# ======================================================================================
# 自检（>= 2 条硬断言；含阴性对照，宁可红不许假绿）
# ======================================================================================


def _selftest_doc() -> dict[str, Any]:
    """夹具：一个圆（圆心 10,20 mm / r=5 mm）、一段 0°→90° 的弧（圆心 0,0 / r=2 mm）、一条直线。"""
    return {
        "schema": "cadcore.l1",
        "units": "mm",
        "entities": [
            {"uuid": "ent-circle", "type": "circle",
             "center_nm": [10_000_000, 20_000_000], "radius_nm": 5_000_000},
            {"uuid": "ent-arc", "type": "arc",
             "center_nm": [0, 0], "radius_nm": 2_000_000,
             "start_angle_deg": 0.0, "end_angle_deg": 90.0},
            {"uuid": "ent-line", "type": "line",
             "start_nm": [0, 0], "end_nm": [1_000_000, 0]},
        ],
        "settings": {"drafting": {"osnap": {"enabled": True, "modes": ["center", "quadrant"]}}},
    }


def _selftest() -> list[str]:
    """跑硬断言，返回失败清单（空 = 全绿）。"""
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}: {detail}")

    # A1 圆心命中：真写记录 + 真返回（记录 uuid, 实体 uuid）
    doc = _selftest_doc()
    out = snap_center_quadrant(doc, point_nm=[10_100_000, 20_050_000], tolerance_nm=1_000_000)
    rec = doc.get("_ops", {}).get(FAMILY, {}).get("snap_center_quadrant")
    check("A1.center_hit_pair", isinstance(out, list) and len(out) == 2 and out[1] == "ent-circle",
          f"out={out!r}")
    check("A1.center_record_persisted",
          isinstance(rec, dict) and rec.get("kind") == "center"
          and rec.get("point_nm") == [10_000_000, 20_000_000]
          and rec.get("entity_uuid") == "ent-circle"
          and isinstance(rec.get("distance_nm"), int) and 0 <= rec["distance_nm"] <= 1_000_000
          and isinstance(out[0], str) and out[0] == rec.get("uuid") and out[0] != "ent-circle"
          and "quadrant_deg" not in rec,
          f"rec={rec!r}")

    # A2 象限点命中：90° 象限点 == (cx, cy + r)，且 quadrant_deg 落盘
    doc2 = _selftest_doc()
    out2 = snap_center_quadrant(doc2, point_nm=[0, 2_000_000], tolerance_nm=1_000)
    rec2 = doc2.get("_ops", {}).get(FAMILY, {}).get("snap_center_quadrant")
    check("A2.quadrant_90_hit",
          out2 == [out2[0], "ent-arc"] if out2 else False,
          f"out2={out2!r}")
    check("A2.quadrant_point_and_deg",
          isinstance(rec2, dict) and rec2.get("kind") == "quadrant"
          and rec2.get("point_nm") == [0, 2_000_000]
          and rec2.get("quadrant_deg") == 90.0 and rec2.get("distance_nm") == 0,
          f"rec2={rec2!r}")

    # A3 阴性对照（弧段过滤是真的）：180° 象限点不在 0°→90° 弧上 ⇒ 不许命中
    doc3 = _selftest_doc()
    out3 = snap_center_quadrant(doc3, point_nm=[-100_000, -2_000_000], tolerance_nm=1_000_000)
    check("A3.sweep_filter_blocks_out_of_arc_quadrant", out3 == [] and "_ops" not in doc3,
          f"out3={out3!r}")

    # A4 阴性对照（无假命中、无写入）：远点 ⇒ [] 且文档逐字节不变
    doc4 = _selftest_doc()
    before4 = _json.dumps(doc4, sort_keys=True)
    out4 = snap_center_quadrant(doc4, point_nm=[0, 100_000_000], tolerance_nm=1_000_000)
    check("A4.no_fake_hit_and_no_write",
          out4 == [] and _json.dumps(doc4, sort_keys=True) == before4,
          f"out4={out4!r}")

    # A5 坏入参必须炸（不许静默兜底）
    for bad_kwargs, why in (
        ({"point_nm": [0.5, 0]}, "float length"),
        ({"point_nm": [0, 0], "tolerance_nm": -1}, "negative tolerance"),
        ({"point_nm": [0, 0], "tolerance_nm": True}, "bool tolerance"),
        ({"point_nm": "0,0"}, "string point"),
    ):
        try:
            snap_center_quadrant(_selftest_doc(), **bad_kwargs)
            check(f"A5.bad_arg_raises[{why}]", False, "no CadError raised")
        except CadError as exc:
            check(f"A5.bad_arg_raises[{why}]", exc.reason == "bad_arg", f"reason={exc.reason!r}")

    # A6 子模式开关是真开关：modes 只列 center ⇒ 象限点不命中
    doc6 = _selftest_doc()
    doc6["settings"]["drafting"]["osnap"]["modes"] = ["center"]
    check("A6.quadrant_disabled_no_hit",
          snap_center_quadrant(doc6, point_nm=[0, 2_000_000], tolerance_nm=1_000) == [],
          "quadrant sub-mode disabled but hit")

    # A7 总开关关掉 ⇒ 圆心也不命中
    doc7 = _selftest_doc()
    doc7["settings"]["drafting"]["osnap"]["enabled"] = False
    check("A7.master_switch_off",
          snap_center_quadrant(doc7, point_nm=[10_000_000, 20_000_000], tolerance_nm=1_000) == [],
          "osnap master switch off but hit")

    # A8 纯查询不改文档，且命中口径与写版一致
    doc8 = _selftest_doc()
    before8 = _json.dumps(doc8, sort_keys=True)
    probe = center_quadrant_probe(doc8, point_nm=[10_000_000, 20_000_000], tolerance_nm=1_000)
    check("A8.probe_pure_and_hit",
          probe == ["ent-circle"] and _json.dumps(doc8, sort_keys=True) == before8,
          f"probe={probe!r}")

    # A9 缺 uuid 的实体被真补上 uuid（不是只活在返回值里）
    doc9 = {"entities": [{"type": "circle", "center_nm": [0, 0], "radius_nm": 3_000_000}]}
    out9 = snap_center_quadrant(doc9, point_nm=[0, 0], tolerance_nm=1)
    uid9 = doc9["entities"][0].get("uuid")
    check("A9.uuid_persisted_on_entity",
          isinstance(uid9, str) and uid9 and len(out9) == 2 and out9[1] == uid9,
          f"uid9={uid9!r} out9={out9!r}")

    # A10 操作表契约：FAMILY + 每个操作可调用且返回列表
    check("A10.family_and_ops",
          FAMILY == "precision_ops" and set(OPERATIONS) == {"snap_center_quadrant", "center_quadrant_probe"}
          and all(callable(fn) for fn in OPERATIONS.values()),
          f"family={FAMILY!r} ops={sorted(OPERATIONS)}")
    return failures


def main() -> int:
    """自检入口：全绿打判定行，红则把失败项写 stderr 并以非零退出（不许假绿）。"""
    failures = _selftest()
    if failures:
        for item in failures:
            print(f"SELFTEST_FAIL {item}", file=_sys.stderr)
        print(f"FEATURE_FAIL family={FAMILY} op=snap_center_quadrant "
              f"failures={len(failures)}", file=_sys.stderr)
        return 1
    print("FEATURE_OK family=precision_ops op=snap_center_quadrant verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
