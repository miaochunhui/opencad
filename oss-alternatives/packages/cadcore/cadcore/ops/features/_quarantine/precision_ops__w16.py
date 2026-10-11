#!/usr/bin/env python3
"""cadcore.ops.features.precision_ops__w16 —— 精度族（precision_ops）切片 W16：**插入点与节点捕捉（INS / NOD）**。

族清单来源（本席实读）：
- ``docs/research/cad-parity/features/precision_ops.json`` **不存在**（与 spec/precision_ops.json 自己的
  ``source.requested_status="absent"`` 一致）；清单真源用 ``features/autocad-precision.json`` 的 ``must_have``（21 条）
  与 ``status/autocad-precision.json`` 的逐条 status。
- 21 条 must_have 里仍 missing/partial 的条目（按清单顺序编号 0..20；全部 21 条都不是 implemented）：
  0 对象捕捉总开关(partial) / 1 端点捕捉(partial) / 2 中点捕捉(partial) / 3 圆心与象限点捕捉(partial) /
  4 交点与外观交点捕捉(partial) / 5 垂足、切点、最近点捕捉(partial) / 6 延长线与平行捕捉(missing) /
  **7 插入点与节点捕捉(missing) ← 本切片负责** / 8 单次临时捕捉(missing) / 9 捕捉设置：优先级与忽略图案填充(partial) /
  10 AutoSnap 捕捉反馈(missing) / 11 正交模式(partial) / 12 极轴追踪开关与增量角(partial) /
  13 极轴附加角与角度测量基准(partial) / 14 对象捕捉追踪(partial) / 15 栅格显示(partial) /
  16 栅格捕捉(partial) / 17 极轴捕捉(partial) / 18 动态输入(partial) / 19 坐标与直接距离输入(partial) /
  20 状态栏开关与快捷键体系(missing)。
- 第 7 号此前为 missing（``status/autocad-precision.json`` evidence: "无实现…osnap.py:74-83 无 insert/node"），
  本切片把它做成**真几何**实现，见下。

冻结接口（spec/precision_ops.json）：
- ``FAMILY = "precision_ops"``；``OPERATIONS: dict[str, Callable]``；
- 每个 op ``op(sk_or_doc, **kwargs) -> list[str]``，返回**被锚定几何的 uuid 列表**（真零 = ``[]``，不是失败）；
- 长度一律整数纳米（int），角度用 float 度，坐标 ``[x_nm, y_nm]``；
- 错误一律 ``cadcore.errors.CadError(reason=...)``，reason 只取本文件 ``ERROR_REASONS`` 的枚举；
- 数值结果同时写 ``doc['_ops']['precision_ops'][<op>] = {'point_nm':…,'kind':…,'ref':…,'distance_nm':…}``
  （**只有**目标是可变映射时写；``docmodel.Document`` 数据类没有该通道，此时只返回 uuid，不改文档）。

实现口径（L1 文档模型，真几何，无桩）：
- **节点（NOD）**：候选 = 草图实体 ``kind == "point"``，位置取 ``geom["x"]/geom["y"]``（整数纳米，与
  ``ops/draw_extra.py:554 point()`` 的落盘形状一致）。这一段**完全落在 L1 原生面上**。
- **插入点（INS）**：块实例基准点。L1 文本**不存** inserts 表 —— 本仓块实例记录在 ``ops/organization.py`` 的
  store 视图里（表结构见 organization.py:26，插入行的 append 见 :797）。故本 op 从两类**真载体**取插入点：
  ① 目标（或 ``target["store"]``）的 ``inserts`` 表，行内基准点键 ``at_nm``（兼容 ``at`` / ``point_nm`` /
     ``position_nm`` / ``at_pt``，也兼容 ``geom`` 嵌套），实例 uuid 键 ``uuid``（兼容 ``insert_uuid`` / ``id``）；
  ② L1 草图里 ``kind ∈ {"insert","block_insert","block_reference"}`` 的实体，位置取 ``geom["at_nm"]`` 或
     ``geom["x"]/geom["y"]``。
  **纯 L1 文档（无 inserts 表、无 insert 实体）时 INS 候选恒为空** —— 这是"载体不在"，不是失败；实测覆盖由结果
  通道的 ``insert_carrier`` 字段如实标出（``store`` / ``entities`` / ``none`` / 短路时 ``not_measured``）。
- 选择规则（对齐 AutoCAD 取点口径）：容差 ``tolerance_nm`` 内的候选按 ``(欧氏距离, 子模式优先级, uuid)`` 取最小；
  距离相等时用 ``kinds`` 的给定顺序当优先级，``settings.drafting.osnap.priority`` 若给出则覆盖它。
- 总开关：``settings.drafting.osnap.enabled is False`` 或显式 ``enabled=False`` ⇒ 返回 ``[]``（短路真零，不抛错，
  结果通道记 ``skipped="osnap_disabled"``；**短路时 ``candidates_considered=-1`` 表示"未采"，绝不写 0 冒充"测到 0"**）。

自检（文件自带，真跑 L1 往返）：``python3 cadcore/ops/features/precision_ops__w16.py``
"""

from __future__ import annotations

import dataclasses
import math
import os
import sys
import uuid as _uuid
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/precision_ops__w16.py
    _here = os.path.dirname(os.path.abspath(__file__))
    for _step in range(8):
        _here = os.path.dirname(_here)
        if os.path.isdir(os.path.join(_here, "cadcore")):
            if _here not in sys.path:
                sys.path.insert(0, _here)
            break
    from cadcore.errors import CadError
else:  # 作为包模块导入：cadcore.ops.features.precision_ops__w16
    from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）

__all__ = ["FAMILY", "OPERATIONS", "SUPPORTED_KINDS", "DEFAULT_TOLERANCE_NM", "ERROR_REASONS",
           "snap_insert_node", "insert_node_candidates"]

#: 族 id（冻结）：spec/precision_ops.json 的 FAMILY 值。
FAMILY = "precision_ops"

#: 本切片拥有的两个子模式（AutoCAD alias: INS / NOD）。别族的子模式一律 bad_kind。
SUPPORTED_KINDS: tuple[str, ...] = ("node", "insert")

#: 缺省容差 = 1 mm（整数纳米）。AutoCAD 靶框是屏幕像素量纲，L1 面没有视图缩放，故用显式纳米容差。
DEFAULT_TOLERANCE_NM = 1_000_000

#: 允许的 ``CadError.reason`` 枚举（spec 口径：只取本文件列出的）。
ERROR_REASONS: tuple[str, ...] = ("bad_target", "bad_arg", "bad_kind", "unsupported_geometry")

_INSERT_ENTITY_KINDS = frozenset({"insert", "block_insert", "block_reference"})
_INSERT_AT_KEYS = ("at_nm", "at", "point_nm", "position_nm", "at_pt")
_INSERT_UUID_KEYS = ("uuid", "insert_uuid", "id")


@dataclasses.dataclass(frozen=True)
class _Candidate:
    """一个可捕捉目标：``point_nm`` 是真几何算出来的位置，``source`` 记录它来自哪类载体。"""

    kind: str
    uuid: str
    point_nm: tuple[int, int]
    source: str


# --------------------------------------------------------------------------------------
# 入参归一化与错误
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    if reason not in ERROR_REASONS:  # 内部口径自检：reason 不许漂出枚举
        raise AssertionError(f"reason {reason!r} not in ERROR_REASONS {ERROR_REASONS!r}")
    return CadError(message, reason=reason, **details)


def _get(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, _MappingABC):
        return node.get(key, default)
    return getattr(node, key, default)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """坐标一律 ``[x_nm, y_nm]`` 两个**整数纳米**（浮点/布尔一律 bad_arg，不静默取整）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _fail("bad_arg", f"{what} must be a 2-item sequence of integer nanometres",
                    got=repr(value)[:120])
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("bad_arg", f"{what} components must be integer nanometres", got=repr(item)[:120])
        out.append(int(item))
    return (out[0], out[1])


def _length(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_arg", f"{what} must be an integer nanometre value", got=repr(value)[:120])
    if value < 0:
        raise _fail("bad_arg", f"{what} must not be negative", got=value)
    return int(value)


def _kinds(value: Any, *, what: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC):
        raise _fail("bad_arg", f"{what} must be a sequence of snap mode names", got=repr(value)[:120])
    out: list[str] = []
    for item in value:
        name = str(item).strip().lower()
        if name not in SUPPORTED_KINDS:
            raise _fail("bad_kind", "unsupported snap mode for this slice",
                        kind=str(item)[:60], supported=list(SUPPORTED_KINDS))
        if name not in out:
            out.append(name)
    return tuple(out)


def _drafting(target: Any) -> Any:
    """``settings.drafting`` 视图；缺席返回 ``None``（**不是**错误：纯 L1 文档没有该节点）。"""
    if isinstance(target, _MappingABC):
        settings = target.get("settings")
        if isinstance(settings, _MappingABC):
            node = settings.get("drafting")
            if isinstance(node, _MappingABC):
                return node
    return None


def _osnap(target: Any, node: Any) -> Any:
    if node is not None:
        osnap = node.get("osnap")
        if isinstance(osnap, _MappingABC):
            return osnap
    if isinstance(target, _MappingABC):
        settings = target.get("settings")
        if isinstance(settings, _MappingABC):
            osnap = settings.get("osnap")
            if isinstance(osnap, _MappingABC):
                return osnap
    return None


def _resolve_enabled(target: Any, enabled: Any) -> bool:
    """显式 kwarg > ``settings.drafting.osnap.enabled`` > 缺省开。"""
    if enabled is not None:
        return bool(enabled)
    osnap = _osnap(target, _drafting(target))
    if osnap is not None and "enabled" in osnap:
        return bool(osnap.get("enabled"))
    node = _drafting(target)
    if node is not None and "enabled" in node:
        return bool(node.get("enabled"))
    return True


def _active_kinds(kinds: Any, target: Any, node: Any) -> tuple[str, ...]:
    """显式 ``kinds`` 优先；否则用 ``osnap.kinds`` 与本切片支持集的交集；再否则全开。"""
    if kinds is not None:
        return _kinds(kinds, what="kinds")
    osnap = _osnap(target, node)
    if osnap is not None:
        declared = osnap.get("kinds")
        if isinstance(declared, _SequenceABC) and not isinstance(declared, (str, bytes)):
            wanted = {str(item).strip().lower() for item in declared}
            return tuple(k for k in SUPPORTED_KINDS if k in wanted)
    return SUPPORTED_KINDS


def _priority(target: Any, node: Any, active: tuple[str, ...]) -> list[str]:
    osnap = _osnap(target, node)
    if osnap is not None:
        order = osnap.get("priority")
        if isinstance(order, _SequenceABC) and not isinstance(order, (str, bytes)):
            declared = [str(item).strip().lower() for item in order]
            ordered = [k for k in declared if k in active] + [k for k in active if k not in declared]
            if ordered:
                return ordered
    return list(active)


def _uuid_of(node: Any, *, what: str) -> str:
    uid = _get(node, "uuid", None)
    if isinstance(uid, bool) or not isinstance(uid, str) or not uid.strip():
        raise _fail("unsupported_geometry", f"{what} has no usable uuid", uuid=repr(uid)[:60])
    text = uid.strip()
    if text.isdigit():  # L1 硬口径：引用一律 (uuid, sub_shape_name)，不许下标引用
        raise _fail("unsupported_geometry", f"{what} uuid looks like an index reference", uuid=text[:60])
    return text


# --------------------------------------------------------------------------------------
# 载体枚举（真几何）
# --------------------------------------------------------------------------------------


def _sketches(target: Any) -> list[Any]:
    if isinstance(target, _MappingABC):
        if "sketches" in target:
            raw = target.get("sketches")
        elif "entities" in target:
            return [target]
        else:
            raise _fail("bad_target", "target has neither 'sketches' nor 'entities'",
                        type=type(target).__name__)
    else:
        raw = getattr(target, "sketches", None)
        if raw is None:
            if hasattr(target, "entities"):
                return [target]
            raise _fail("bad_target", "target is neither an L1 document nor a sketch",
                        type=type(target).__name__)
    if isinstance(raw, (str, bytes)) or not isinstance(raw, _SequenceABC):
        raise _fail("bad_target", "doc.sketches must be a sequence", type=type(raw).__name__)
    return list(raw)


def _entities(sketch: Any) -> list[Any]:
    raw = sketch.get("entities") if isinstance(sketch, _MappingABC) else getattr(sketch, "entities", None)
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, _SequenceABC):
        raise _fail("bad_target", "sketch.entities must be a sequence", type=type(raw).__name__)
    return list(raw)


def _node_candidates(target: Any) -> list[_Candidate]:
    """NOD：L1 草图中的 ``kind="point"`` 实体（位置 = ``geom.x/geom.y`` 整数纳米）。"""
    out: list[_Candidate] = []
    for sketch in _sketches(target):
        for entity in _entities(sketch):
            if str(_get(entity, "kind", "")).strip().lower() != "point":
                continue
            geom = _get(entity, "geom", None)
            if not isinstance(geom, _MappingABC) or "x" not in geom or "y" not in geom:
                raise _fail("unsupported_geometry", "point entity needs geom.x/geom.y in integer nanometres",
                            uuid=repr(_get(entity, "uuid", None))[:60])
            uid = _uuid_of(entity, what="point entity")
            out.append(_Candidate("node", uid, _point((geom["x"], geom["y"]), what="point.geom"),
                                  "entities"))
    return out


def _insert_point(node: Any) -> tuple[int, int]:
    geom = _get(node, "geom", None)
    for holder, label in ((node, "insert"), (geom, "insert.geom")):
        if not isinstance(holder, _MappingABC):
            continue
        for key in _INSERT_AT_KEYS:
            if key in holder:
                return _point(holder[key], what=f"{label}.{key}")
        if "x" in holder and "y" in holder:
            return _point((holder["x"], holder["y"]), what=f"{label}.x/y")
    raise _fail("unsupported_geometry", "insert carrier has no base point (at_nm or x/y)",
                uuid=repr(_get(node, "uuid", None))[:60])


def _insert_candidates(target: Any) -> tuple[list[_Candidate], str]:
    """INS：块实例基准点。载体 = org store 的 ``inserts`` 表 或 L1 里的 insert 类实体。"""
    out: list[_Candidate] = []
    carrier = "none"
    rows: list[Any] = []
    if isinstance(target, _MappingABC):
        table = target.get("inserts")
        if table is None:
            store = target.get("store")
            if isinstance(store, _MappingABC):
                table = store.get("inserts")
        if table is not None:
            if isinstance(table, (str, bytes)) or not isinstance(table, _SequenceABC):
                raise _fail("bad_target", "inserts table must be a sequence", type=type(table).__name__)
            rows = list(table)
            carrier = "store"
    for row in rows:
        uid = _uuid_of(row, what="insert row")
        out.append(_Candidate("insert", uid, _insert_point(row), "store"))
    for sketch in _sketches(target):
        for entity in _entities(sketch):
            if str(_get(entity, "kind", "")).strip().lower() not in _INSERT_ENTITY_KINDS:
                continue
            uid = _uuid_of(entity, what="insert entity")
            out.append(_Candidate("insert", uid, _insert_point(entity), "entities"))
            if carrier == "none":
                carrier = "entities"
    return out, carrier


# --------------------------------------------------------------------------------------
# 选择与结果通道
# --------------------------------------------------------------------------------------


def _collect(target: Any, *, cursor: tuple[int, int], tolerance: int, active: tuple[str, ...],
             priority: list[str]) -> tuple[list[tuple[float, int, str, _Candidate]], dict[str, Any]]:
    candidates: list[_Candidate] = []
    carrier = "none"
    if "node" in active:
        candidates.extend(_node_candidates(target))
    if "insert" in active:
        insert_cands, carrier = _insert_candidates(target)
        candidates.extend(insert_cands)
    hits: list[tuple[float, int, str, _Candidate]] = []
    for cand in candidates:
        distance = math.hypot(cand.point_nm[0] - cursor[0], cand.point_nm[1] - cursor[1])
        if distance <= tolerance:
            hits.append((distance, priority.index(cand.kind), cand.uuid, cand))
    hits.sort(key=lambda item: (item[0], item[1], item[2]))  # 近者胜；等距看优先级，再按 uuid 定序
    meta = {
        "tolerance_nm": tolerance,
        "cursor_nm": [cursor[0], cursor[1]],
        "active_kinds": list(active),
        "priority": list(priority),
        "candidates_considered": len(candidates),
        "node_candidates": sum(1 for c in candidates if c.kind == "node"),
        "insert_candidates": sum(1 for c in candidates if c.kind == "insert"),
        "insert_carrier": carrier,
        "hit_count": len(hits),
    }
    return hits, meta


def _fields(best: Any, meta: Any) -> dict[str, Any]:
    out = {
        "point_nm": None if best is None else [best[3].point_nm[0], best[3].point_nm[1]],
        "kind": None if best is None else best[3].kind,
        "ref": None if best is None else best[3].uuid,
        "distance_nm": None if best is None else int(round(best[0])),
    }
    out.update(meta)
    return out


def _skipped_fields(cursor: tuple[int, int], tolerance: int, active: tuple[str, ...],
                    reason: str) -> dict[str, Any]:
    return {
        "point_nm": None, "kind": None, "ref": None, "distance_nm": None,
        "tolerance_nm": tolerance, "cursor_nm": [cursor[0], cursor[1]], "active_kinds": list(active),
        # -1 = 未采（短路，没数候选）；0 只用于"真为零"，两者绝不混同。
        "candidates_considered": -1, "node_candidates": -1, "insert_candidates": -1,
        "insert_carrier": "not_measured", "hit_count": 0, "skipped": reason,
    }


def _record(target: Any, name: str, fields: Mapping[str, Any], write_result: bool) -> None:
    if not write_result or not isinstance(target, _MutableMappingABC):
        return
    container = target.setdefault("_ops", {})
    if not isinstance(container, _MutableMappingABC):
        raise _fail("bad_target", "doc['_ops'] exists but is not a mutable mapping",
                    type=type(container).__name__)
    family = container.setdefault(FAMILY, {})
    if not isinstance(family, _MutableMappingABC):
        raise _fail("bad_target", "doc['_ops'][family] exists but is not a mutable mapping",
                    type=type(family).__name__)
    family[name] = dict(fields)


# --------------------------------------------------------------------------------------
# OPERATIONS
# --------------------------------------------------------------------------------------


def snap_insert_node(sk_or_doc: Any, *, cursor_nm: Any, tolerance_nm: Any = DEFAULT_TOLERANCE_NM,
                     kinds: Any = None, enabled: Any = None,
                     write_result: bool = True) -> list[str]:
    """插入点/节点捕捉：把游标点吸到容差内最近的 NOD（点实体）或 INS（块实例基准点）。

    返回 ``[被锚定对象的 uuid]``（命中唯一获胜者）；容差内无候选返回 ``[]``（真零，不抛错）。
    长/宽：``cursor_nm`` 与 ``tolerance_nm`` 均为整数纳米；``kinds`` ⊂ ``("node","insert")``，顺序即优先级。
    数据后果：可变映射目标上写 ``doc['_ops']['precision_ops']['snap_insert_node']``（含 ``point_nm`` / ``kind`` /
    ``ref`` / ``distance_nm`` / 候选计数 / ``insert_carrier``）。``docmodel.Document`` 无该通道 ⇒ 只返回 uuid。
    错误：坏目标 ``bad_target``、坏入参 ``bad_arg``、别族子模式 ``bad_kind``、几何不可用 ``unsupported_geometry``。
    """
    cursor = _point(cursor_nm, what="cursor_nm")
    tolerance = _length(tolerance_nm, what="tolerance_nm")
    node = _drafting(sk_or_doc)
    active = _active_kinds(kinds, sk_or_doc, node)
    if not _resolve_enabled(sk_or_doc, enabled):
        _record(sk_or_doc, "snap_insert_node",
                _skipped_fields(cursor, tolerance, active, "osnap_disabled"), write_result)
        return []
    if not active:
        _record(sk_or_doc, "snap_insert_node",
                _skipped_fields(cursor, tolerance, active, "no_active_kind"), write_result)
        return []
    hits, meta = _collect(sk_or_doc, cursor=cursor, tolerance=tolerance, active=active,
                          priority=_priority(sk_or_doc, node, active))
    best = hits[0] if hits else None
    _record(sk_or_doc, "snap_insert_node", _fields(best, meta), write_result)
    return [best[3].uuid] if best is not None else []


def insert_node_candidates(sk_or_doc: Any, *, cursor_nm: Any, tolerance_nm: Any = DEFAULT_TOLERANCE_NM,
                           kinds: Any = None, enabled: Any = None,
                           write_result: bool = True) -> list[str]:
    """容差内**全部** INS/NOD 候选，按 ``(距离, 优先级, uuid)`` 排序返回 uuid 列表（真零 = ``[]``）。

    与 :func:`snap_insert_node` 同一套载体与选择规则；用于验证/高亮多命中（AutoSnap 标记的下游数据面）。
    """
    cursor = _point(cursor_nm, what="cursor_nm")
    tolerance = _length(tolerance_nm, what="tolerance_nm")
    node = _drafting(sk_or_doc)
    active = _active_kinds(kinds, sk_or_doc, node)
    if not _resolve_enabled(sk_or_doc, enabled):
        _record(sk_or_doc, "insert_node_candidates",
                _skipped_fields(cursor, tolerance, active, "osnap_disabled"), write_result)
        return []
    if not active:
        _record(sk_or_doc, "insert_node_candidates",
                _skipped_fields(cursor, tolerance, active, "no_active_kind"), write_result)
        return []
    hits, meta = _collect(sk_or_doc, cursor=cursor, tolerance=tolerance, active=active,
                          priority=_priority(sk_or_doc, node, active))
    best = hits[0] if hits else None
    _record(sk_or_doc, "insert_node_candidates", _fields(best, meta), write_result)
    return [item[3].uuid for item in hits]


#: 冻结面：族 → 操作表。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "snap_insert_node": snap_insert_node,
    "insert_node_candidates": insert_node_candidates,
}


# --------------------------------------------------------------------------------------
# 自检（真跑：L1 往返 + 真几何距离 + 优先级/容差/开关/错误码）
# --------------------------------------------------------------------------------------


def _l1_fixture() -> tuple[Any, str, str, str, Any]:
    """用**真** ``docmodel`` 造 L1 文档并往返一次，返回 (round-tripped Document, p0, p1, text2, text3)。"""
    from cadcore.docmodel import document_from_l1, document_to_l1, new_document

    doc = new_document()
    p0, p1 = str(_uuid.uuid4()), str(_uuid.uuid4())
    doc.sketches.append({
        "uuid": str(_uuid.uuid4()),
        "plane": {"kind": "xy"},
        "entities": [
            {"uuid": p0, "kind": "point", "layer": "0", "construction": False, "geom": {"x": 0, "y": 0}},
            {"uuid": p1, "kind": "point", "layer": "0", "construction": False,
             "geom": {"x": 10_000_000, "y": 0}},
            {"uuid": str(_uuid.uuid4()), "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 5_000_000, "x2": 10_000_000, "y2": 5_000_000}},
        ],
        "constraints": [], "dimensions": [], "solved": {"dof": 0, "converged": True, "cache": {}},
    })
    loaded = document_from_l1(document_to_l1(doc))
    text2 = document_to_l1(loaded)
    text3 = document_to_l1(document_from_l1(text2))
    return loaded, p0, p1, text2, text3


def _dict_fixture() -> tuple[dict[str, Any], str, str]:
    """org store 形状的插入行（organization.py:26 表结构）+ 同点位的 NOD 点实体。"""
    node_uuid, insert_uuid = str(_uuid.uuid4()), str(_uuid.uuid4())
    doc: dict[str, Any] = {
        "sketches": [{
            "uuid": str(_uuid.uuid4()),
            "plane": {"kind": "xy"},
            "entities": [
                {"uuid": node_uuid, "kind": "point", "layer": "0", "construction": False,
                 "geom": {"x": 0, "y": 0}},
            ],
            "constraints": [], "dimensions": [], "solved": {},
        }],
        "inserts": [{"uuid": insert_uuid, "block": str(_uuid.uuid4()), "at_nm": [0, 0]}],
        "settings": {"drafting": {"uuid": str(_uuid.uuid4()), "osnap": {"enabled": True}}},
    }
    return doc, node_uuid, insert_uuid


def _expect_error(checks: dict[str, int], reason: str, fn: Callable[[], Any]) -> None:
    checks["n"] += 1
    try:
        fn()
    except CadError as exc:
        if exc.reason != reason:
            raise AssertionError(f"expected CadError(reason={reason!r}), got reason={exc.reason!r}") from exc
        return
    raise AssertionError(f"expected CadError(reason={reason!r}), nothing was raised")


def _selftest() -> int:
    checks = {"n": 0}

    def ok(cond: Any, message: str) -> None:
        checks["n"] += 1
        if not cond:
            raise AssertionError(message)

    # --- 1) 真 L1 文档往返 + 在 round-tripped Document 上做节点捕捉 -------------------------
    loaded, p0, p1, text2, text3 = _l1_fixture()
    ok(text3 == text2, "L1 canonical text must be idempotent across a re-read")
    ok(snap_insert_node(loaded, cursor_nm=[9_600_000, 300_000], tolerance_nm=1_000_000) == [p1],
       "node snap must pick the nearest point entity on a real round-tripped L1 document")
    ok(snap_insert_node(loaded, cursor_nm=[400_000, 300_000], tolerance_nm=1_000_000) == [p0],
       "node snap must pick the other point entity when the cursor moves")
    ok(snap_insert_node(loaded, cursor_nm=[9_700_000, 400_000], tolerance_nm=499_999) == [],
       "distance 500000nm outside a 499999nm aperture must be a true-zero miss")
    ok(snap_insert_node(loaded, cursor_nm=[9_700_000, 400_000], tolerance_nm=500_000) == [p1],
       "same 500000nm distance must be included at tolerance 500000nm (boundary is inclusive)")
    ok(getattr(loaded, "_ops", None) is None,
       "a docmodel.Document target must not grow an _ops attribute (no result channel there)")
    ok(insert_node_candidates(loaded, cursor_nm=[10_000_000, 0], tolerance_nm=1_000_000) == [p1],
       "candidate listing must return the single in-aperture node")

    # --- 2) INS 载体（org store inserts 表）+ 优先级/等距规则 -------------------------------
    doc, node_uuid, insert_uuid = _dict_fixture()
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, kinds=("insert", "node"))
       == [insert_uuid], "equal distance 0 must be resolved by the kinds priority order (insert first)")
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, kinds=("node", "insert"))
       == [node_uuid], "flipping the kinds order must flip the equal-distance winner")
    record = doc["_ops"]["precision_ops"]["snap_insert_node"]
    ok(record["insert_carrier"] == "store" and record["candidates_considered"] == 2,
       "result channel must report the real insert carrier and the real candidate count")
    ok(record["point_nm"] == [0, 0] and record["distance_nm"] == 0 and record["kind"] == "insert",
       "result channel must carry the anchored point, kind and distance")
    ok(insert_node_candidates(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, kinds=("insert", "node"))
       == [insert_uuid, node_uuid], "candidate listing must be sorted by (distance, priority, uuid)")
    ok(snap_insert_node(doc, cursor_nm=[3_000_000, 4_000_000], tolerance_nm=6_000_000, kinds=("insert",))
       == [], "a 5mm-away node must stay out of a 6mm aperture when only INS is active")

    # --- 3) 优先级表覆盖（settings.drafting.osnap.priority）与总开关 ------------------------
    doc["settings"]["drafting"]["osnap"]["priority"] = ["node", "insert"]
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, kinds=("insert", "node"))
       == [node_uuid], "osnap.priority must override the kinds order for equal distances")
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, enabled=False) == [],
       "explicit enabled=False must yield a true-zero result")
    ok(doc["_ops"]["precision_ops"]["snap_insert_node"]["skipped"] == "osnap_disabled"
       and doc["_ops"]["precision_ops"]["snap_insert_node"]["candidates_considered"] == -1,
       "a short-circuited call must record skipped=osnap_disabled and -1 (not measured), never 0")
    doc["settings"]["drafting"]["osnap"]["enabled"] = False
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000) == [],
       "settings.drafting.osnap.enabled=False must short to a true-zero result")
    doc["settings"]["drafting"]["osnap"]["enabled"] = True
    ok(snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=1_000_000, kinds=("insert",)) == [insert_uuid],
       "re-enabling osnap must restore the insert snap (negative control on the switch)")

    # --- 4) 第二类 INS 载体：L1 里的 insert 实体；以及无载体时的真零 ------------------------
    ins2 = str(_uuid.uuid4())
    doc2: dict[str, Any] = {"sketches": [{
        "uuid": str(_uuid.uuid4()), "plane": {"kind": "xy"},
        "entities": [{"uuid": ins2, "kind": "insert", "layer": "0", "construction": False,
                      "geom": {"at_nm": [2_000_000, 2_000_000]}}],
        "constraints": [], "dimensions": [], "solved": {}}]}
    ok(snap_insert_node(doc2, cursor_nm=[2_100_000, 2_000_000], tolerance_nm=1_000_000, kinds=("insert",))
       == [ins2], "an insert-kind L1 entity must be snap-able through its geom base point")
    ok(doc2["_ops"]["precision_ops"]["snap_insert_node"]["insert_carrier"] == "entities",
       "carrier must be reported as entities for the L1 entity route")
    ok(snap_insert_node(doc2, cursor_nm=[2_100_000, 2_000_000], tolerance_nm=1_000_000, kinds=("node",))
       == [] and doc2["_ops"]["precision_ops"]["snap_insert_node"]["candidates_considered"] == 0,
       "asking only for NOD on an insert-only sketch must be a measured true-zero (0, not -1)")

    # --- 5) 错误码（reason 只取白名单枚举） ------------------------------------------------
    _expect_error(checks, "bad_arg", lambda: snap_insert_node(doc, cursor_nm=[1.0, 2]))
    _expect_error(checks, "bad_arg", lambda: snap_insert_node(doc, cursor_nm=[0, 0], tolerance_nm=-1))
    _expect_error(checks, "bad_arg", lambda: snap_insert_node(doc, cursor_nm=[0, 0], kinds="node"))
    _expect_error(checks, "bad_kind",
                  lambda: snap_insert_node(doc, cursor_nm=[0, 0], kinds=("tangent",)))
    _expect_error(checks, "bad_target", lambda: snap_insert_node({"foo": 1}, cursor_nm=[0, 0]))
    _expect_error(checks, "unsupported_geometry", lambda: snap_insert_node(
        {"sketches": [{"uuid": str(_uuid.uuid4()), "entities": [{"kind": "point", "geom": {"x": 1, "y": 2}}]}]},
        cursor_nm=[0, 0]))
    return checks["n"]


def main(argv: Any = None) -> int:
    try:
        checks = _selftest()
    except Exception as exc:  # 自检失败必须 loud（不吞、不降级成 PASS）
        print(f"selftest: verdict=FAIL type={type(exc).__name__} message={exc}")
        print(f"FEATURE_FAIL family={FAMILY} op=snap_insert_node verdict=FAIL")
        return 1
    print(f"selftest: checks={checks} verdict=PASS")
    print(f"FEATURE_OK family={FAMILY} op=snap_insert_node verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 precision_ops__w16.py:563 "explicit enabled=False must短 a true-zero result" -> "explicit enabled=False must yield a true-zero result"
