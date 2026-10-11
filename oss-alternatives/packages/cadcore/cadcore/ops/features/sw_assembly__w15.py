#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w15 —— 装配族 w15 片：**配合对齐方向控制**（Aligned / Anti-Aligned）。

一句话用途
----------
把 SolidWorks「配合 PropertyManager → 配合对齐（对齐 / 反转对齐）」这个开关做成 L1 文档上的
**真几何**操作：读出配合两个参考的**当前世界几何**（真点 / 真方向，由组件位姿 `rotation` + `at_nm`
推出），据此判定该配合现在是"对齐"还是"反转对齐"，按请求改写记录，并（缺省）**真的把第二个
零部件摆到目标一侧** —— 方向类配合绕其参考点转 180°，距离类配合沿第一参考方向平移。
所以本片不是"翻一个布尔标志"：调用后组件位姿、世界几何、配合记录三者一致，且可复算复核。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_assembly"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数（``mate_align`` / ``mate_alignment_check``）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回**受影响配合的 uuid 列表**（按入参顺序去重）；
- 长度一律**整数纳米（nm）**（`alignment_nm` 为有符号整数纳米），角度为有限浮点度；
- 错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

落盘形态（不另建表）
--------------------
复用族模块 ``cadcore.ops.sw_assembly`` 的同一张表：``doc.extra["sw_assembly"]["mates"]``（L1 冻结字段
之外的容器，可随文档往返）。本片**只对自己的配合行加键**（`alignment` / `alignment_geometric` /
`alignment_satisfied` / `alignment_applied` / `alignment_nm` / `alignment_dot` /
`alignment_check_reason`），并沿用族模块既有的 ``flip`` 真源键 —— 不新增表、不碰组件表以外的任何族表。

语义口径（与族模块 `mate_add` 的 `flip` 对齐）
---------------------------------------------
- ``aligned=True``  ⇒ ``flip=False``（对齐）；
- ``aligned=False`` ⇒ ``flip=True``（反转对齐）；
- ``aligned=None``  ⇒ 按**已记录意图**取反（即 SolidWorks 的"反转对齐"按钮语义）。

支持的类型与边界（写下来，防止下游靠猜）
----------------------------------------
1. 支持 ``coincident`` / ``parallel`` / ``concentric`` / ``distance`` 四类 —— 这四类在本层的
   残差度量里真的消费 ``flip``。``perpendicular`` / ``angle`` / ``tangent`` / ``lock``
   的"对齐方向"不在本片范围 ⇒ ``CadError(reason="alignment_unsupported_kind")``，**不静默当成功**。
2. **量不出就不当通过**（AGENTS.md E2.3）：参考名没有方向、或距离配合值为 ``0``（没有"另一侧"
   可言）⇒ ``CadError(reason="alignment_unmeasurable")``；``mate_alignment_check`` 遇到这类行
   把它**照样报出来**（返回值里带该 uuid），并把 ``alignment_satisfied`` 记成 ``None``（NA），
   绝不写成 ``False`` 冒充"已测量且违反"。
3. 只动**第二个参考所在组件**（配合的第一个参考视为基准）。该组件 ``fixed=True`` 时照族模块口径
   抛 ``component_fixed``（显式 ``allow_fixed=True`` 可强动），不静默跳过。
4. ``apply=False``：只写"意图"，不碰几何；此时 ``alignment_satisfied=False`` 是**真话**
   （记录与几何确实不符，`mate_alignment_check` 会把它报出来）。
5. 本层仍**不是约束求解器**：本片只保证"这一条配合的对齐方向"与几何一致，不保证其它配合
   同时满足；也不改组件的 ``bbox_nm`` / ``refs``（局部几何不变，转的是位姿）。

自检入口（文件自带，可直接跑）：

    cd oss-alternatives/packages/cadcore && python3 cadcore/ops/features/sw_assembly__w15.py

中文只出现在 docstring 里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"计为
`hardcoded_literals`，面向用户的文案一律走语言包（`cadcore.i18n.catalog`）。
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.docmodel import Document, document_from_l1, document_to_l1, new_document
from cadcore.ops import sw_assembly as _asm

__all__ = ["FAMILY", "OPERATIONS", "ALIGNED", "ANTI_ALIGNED", "UNMEASURED_STATE",
           "mate_align", "mate_alignment_check", "selftest"]

#: 族名（冻结：与 packages/cadcore/cadcore/ops/features/<family>__w<k>.py 命名一致）。
FAMILY = "sw_assembly"

#: 对齐方向的两种状态（机读字符串，不写中文）。
ALIGNED = "aligned"
ANTI_ALIGNED = "anti_aligned"

#: `mate_alignment_check` 对"量不出"的行写这个几何状态（与 `None` 的 NA 语义配套）。
UNMEASURED_STATE = "unmeasured"

#: 本片支持"对齐方向控制"的配合类型（这四类在本层残差度量里真的消费 flip）。
_ALIGN_KINDS: tuple[str, ...] = ("coincident", "parallel", "concentric", "distance")

_DISTANCE = "distance"
_MAIN_OP = "mate_align"

#: 交给族模块的位姿/参考几何访问器（复用真源，不抄第二份世界变换）。
_world_mate_ref = _asm._world_mate_ref
_store_of = _asm._store
_table = _asm._table
_rotate_component = _asm.rotate_component
_move_component = _asm.move_component


# ======================================================================================
# 入参把关
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """机器可读错误（`reason` 供下游分支，`details` 供判定行/日志归因）。"""
    return CadError(detail, reason=reason, **details)


def _flag(value: Any, *, what: str) -> bool:
    """开关入参 → 严格 bool（不收 0/1 冒充，避免"看起来设了"）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))
    return bool(value)


def _text(value: Any, *, what: str) -> str:
    """文本入参 → 非空字符串（空白串不算数）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", f"{what} must be a non-empty string", what=what,
                    value=repr(value))
    return value.strip()


def _mates_arg(value: Any) -> list[str]:
    """配合引用入参 → uuid 列表（单个字符串或序列都收；去重保序；空序列报错）。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise _fail("invalid_argument", "mates must be a mate uuid or a sequence of uuids",
                    value=repr(value))
    if not items:
        raise _fail("invalid_argument", "mates must not be empty", value=repr(value))
    out: list[str] = []
    for item in items:
        key = _text(item, what="mate")
        if key not in out:
            out.append(key)
    return out


# ======================================================================================
# 表访问（复用族模块的 store / mates 表；找不到一律显式报错）
# ======================================================================================


def _store(target: Any) -> dict[str, Any]:
    """装配表容器（不存在 ⇒ `no_assembly`，不做"凭空建一份"的静默降级）。"""
    store = _store_of(target)
    if store is None:
        raise _fail("no_assembly", "target has no assembly table")
    return store


def _mate_rows(store: dict[str, Any]) -> list[dict[str, Any]]:
    """配合表逐行取出（行不是映射 ⇒ `corrupt_store`）。"""
    rows: list[dict[str, Any]] = []
    for row in _table(store, "mates"):
        if not isinstance(row, dict):
            raise _fail("corrupt_store", "mate row is not a mapping", value_type=type(row).__name__)
        rows.append(row)
    return rows


def _mate_row(store: dict[str, Any], uuid: str) -> dict[str, Any]:
    """按 uuid 找配合行（找不到报 `unknown_mate`，不做下标引用）。"""
    rows = _mate_rows(store)
    for row in rows:
        if str(row.get("uuid")) == uuid:
            return row
    raise _fail("unknown_mate", "no mate with that uuid", mate=uuid,
                known=[str(row.get("uuid")) for row in rows])


def _pair(row: dict[str, Any]) -> list[tuple[str, str]]:
    """配合行的两个参考 `[(component_uuid, ref_name), ...]`（坏行 ⇒ `corrupt_mate`）。"""
    refs = row.get("refs")
    if isinstance(refs, (str, bytes)) or not isinstance(refs, (list, tuple)) or len(refs) != 2:
        raise _fail("corrupt_mate", "a mate row must carry exactly two references",
                    mate=str(row.get("uuid")))
    out: list[tuple[str, str]] = []
    for item in refs:
        if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)) or len(item) != 2:
            raise _fail("corrupt_mate", "each mate reference must be [component_uuid, ref_name]",
                        mate=str(row.get("uuid")), value=repr(item))
        out.append((_text(item[0], what="ref component"), _text(item[1], what="ref name")))
    return out


def _world_pair(store: dict[str, Any], row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """两个参考的**世界几何** `{"point": [float x3], "dir": unit|None}`（真位姿推出来的）。"""
    first_ref, second_ref = _pair(row)
    return _world_mate_ref(store, first_ref), _world_mate_ref(store, second_ref)


# ======================================================================================
# 真几何：对齐状态 + 使它成立的刚体变换
# ======================================================================================


def _sub(a: Any, b: Any) -> list[float]:
    return [float(a[i]) - float(b[i]) for i in range(3)]


def _dot(a: Any, b: Any) -> float:
    return float(sum(float(a[i]) * float(b[i]) for i in range(3)))


def _cross(a: Any, b: Any) -> list[float]:
    return [float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
            float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
            float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])]


def _distance_value(row: dict[str, Any]) -> int:
    """距离配合的目标值（整数纳米；坏行 ⇒ `corrupt_mate`）。"""
    value = row.get("value_nm")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("corrupt_mate", "a distance mate needs a numeric value_nm",
                    mate=str(row.get("uuid")), value=repr(value))
    return int(round(float(value)))


def _state_from_geometry(row: dict[str, Any], first: dict[str, Any],
                         second: dict[str, Any]) -> tuple[str, int | None, float | None]:
    """**从世界几何现算**对齐状态 → `(状态, 有符号投影 nm 或 None, 方向点积或 None)`。

    * `distance`：第二参考点在第一参考方向上的有符号投影 `< 0` ⇒ 反转对齐；`>= 0` ⇒ 对齐。
      值为 `0` 时没有"另一侧"可言 ⇒ `alignment_unmeasurable`（不编一个状态）。
    * 方向类：两方向点积 `< 0` ⇒ 反转对齐；`>= 0` ⇒ 对齐。任一参考缺方向 ⇒ `alignment_unmeasurable`。
    """
    kind = str(row.get("kind"))
    uuid = str(row.get("uuid"))
    if kind == _DISTANCE:
        direction = first.get("dir")
        if direction is None:
            raise _fail("alignment_unmeasurable", "the first reference carries no direction",
                        mate=uuid, component=str(_pair(row)[0][0]))
        value = _distance_value(row)
        if value == 0:
            raise _fail("alignment_unmeasurable", "a zero-distance mate has no side to flip",
                        mate=uuid, value_nm=0)
        projection = _dot(_sub(second["point"], first["point"]), direction)
        state = ANTI_ALIGNED if projection < 0 else ALIGNED
        return state, int(round(projection)), None
    first_dir, second_dir = first.get("dir"), second.get("dir")
    if first_dir is None or second_dir is None:
        raise _fail("alignment_unmeasurable",
                    "both references need a direction to tell aligned from anti-aligned", mate=uuid)
    dot = _dot(first_dir, second_dir)
    return (ANTI_ALIGNED if dot < 0 else ALIGNED), None, float(dot)


def _flip_axis(direction: Any) -> list[float]:
    """取一条**垂直于** `direction` 的单位旋转轴（绕它转 180° 恰好把方向翻向、点不动）。"""
    unit = [float(item) for item in direction]
    length = math.sqrt(sum(item * item for item in unit))
    if length <= 1e-12:
        raise _fail("degenerate_axis", "reference direction must not be a zero vector")
    unit = [item / length for item in unit]
    index = min(range(3), key=lambda axis: abs(unit[axis]))
    basis = [0.0, 0.0, 0.0]
    basis[index] = 1.0
    axis = _cross(unit, basis)
    axis_length = math.sqrt(sum(item * item for item in axis))
    if axis_length <= 1e-12:  # pragma: no cover - 选轴规则保证不会共线
        raise _fail("degenerate_axis", "could not build a perpendicular flip axis")
    return [item / axis_length for item in axis]


def _recorded_alignment(row: dict[str, Any]) -> str:
    """已记录的对齐意图（缺 `alignment` 键的老行按族模块的 `flip` 键推，不猜）。"""
    recorded = row.get("alignment")
    if recorded in (ALIGNED, ANTI_ALIGNED):
        return str(recorded)
    return ANTI_ALIGNED if bool(row.get("flip")) else ALIGNED


def _target_state(row: dict[str, Any], aligned: Any) -> str:
    """目标状态：显式 bool ⇒ 按它；`None` ⇒ 按已记录意图取反（"反转对齐"按钮语义）。"""
    if aligned is None:
        return ANTI_ALIGNED if _recorded_alignment(row) == ALIGNED else ALIGNED
    return ALIGNED if _flag(aligned, what="aligned") else ANTI_ALIGNED


def _apply_alignment(target: Any, row: dict[str, Any], state: str, first: dict[str, Any],
                     second: dict[str, Any], allow_fixed: bool) -> None:
    """**真几何落地**：把第二个参考所在组件摆到目标一侧（位姿真变，不是改标志）。

    * 方向类（coincident / parallel / concentric）：绕**第二参考点**、垂直于第二参考方向的轴
      转 180° ⇒ 方向翻向、参考点不动（重合/同轴的点条件不被破坏）。
    * `distance`：沿第一参考方向平移 `目标投影 - 当前投影`（整数纳米），把第二参考挪到另一侧。
    """
    component = _pair(row)[1][0]
    if str(row.get("kind")) == _DISTANCE:
        direction = first["dir"]
        value = _distance_value(row)
        wanted = -value if state == ANTI_ALIGNED else value
        current = _dot(_sub(second["point"], first["point"]), direction)
        delta = [int(round((wanted - current) * float(direction[axis]))) for axis in range(3)]
        _move_component(target, components=[component], delta_nm=delta, allow_fixed=allow_fixed)
        return
    center = [int(round(float(item))) for item in second["point"]]
    axis = _flip_axis(second["dir"])
    _rotate_component(target, components=[component], angle_deg=180.0, axis=axis,
                      center_nm=center, allow_fixed=allow_fixed)


def _write_alignment(row: dict[str, Any], state: str, geometric: str, projection: int | None,
                     dot: float | None, applied: bool) -> None:
    """把"意图 + 几何现状 + 是否一致"写成机读行（只加键；三态：True/False/None=NA）。"""
    row["flip"] = bool(state == ANTI_ALIGNED)
    row["alignment"] = str(state)
    row["alignment_geometric"] = str(geometric)
    row["alignment_satisfied"] = bool(geometric == state)
    row["alignment_applied"] = bool(applied)
    row["alignment_nm"] = None if projection is None else int(projection)
    row["alignment_dot"] = None if dot is None else float(dot)
    row["alignment_check_reason"] = None


# ======================================================================================
# 操作
# ======================================================================================


def mate_align(sk_or_doc: Any, *, mates: Any, aligned: Any = None, apply: Any = True,
               allow_fixed: Any = False) -> list[str]:
    """**配合对齐方向控制（对齐 / 反转对齐）** —— 改记录 + 真摆几何。

    入参：

    * `mates`：配合 uuid（单个或序列；找不到 ⇒ `unknown_mate`）；
    * `aligned`：`True` = 对齐、`False` = 反转对齐、`None` = 按已记录意图取反（缺省取反）；
    * `apply`：`True`（缺省）= 真把第二个参考所在组件摆到目标一侧；`False` = 只写意图，几何不动
      （此时 `alignment_satisfied=False` 是真话，`mate_alignment_check` 会报出来）；
    * `allow_fixed`：第二个组件 `fixed=True` 时是否强动（缺省 `False` ⇒ `component_fixed`）。

    返回：本次**受影响的配合 uuid 列表**（按 `mates` 给定顺序去重）。

    错误：`alignment_unsupported_kind`（垂直/角度/相切/锁定这类没有对齐方向的配合）、
    `alignment_unmeasurable`（参考无方向，或距离配合值为 0 ⇒ 没有"另一侧"）、
    `alignment_apply_failed`（摆完几何仍与目标不符 —— 尺子看得见红，不静默写成功）。
    """
    store = _store(sk_or_doc)
    wanted = _mates_arg(mates)
    do_apply = _flag(apply, what="apply")
    allow = _flag(allow_fixed, what="allow_fixed")
    out: list[str] = []
    for uuid in wanted:
        row = _mate_row(store, uuid)
        kind = str(row.get("kind"))
        if kind not in _ALIGN_KINDS:
            raise _fail("alignment_unsupported_kind",
                        "this mate kind has no alignment direction in this slice",
                        mate=uuid, kind=kind, supported=list(_ALIGN_KINDS))
        first, second = _world_pair(store, row)
        state_now, _, _ = _state_from_geometry(row, first, second)
        state = _target_state(row, aligned)
        applied = bool(do_apply and state_now != state)
        if applied:
            _apply_alignment(sk_or_doc, row, state, first, second, allow)
        first_after, second_after = _world_pair(store, row)
        geometric, projection, dot = _state_from_geometry(row, first_after, second_after)
        if do_apply and geometric != state:
            raise _fail("alignment_apply_failed",
                        "geometry still does not match the requested alignment",
                        mate=uuid, requested=state, geometric=geometric)
        _write_alignment(row, state, geometric, projection, dot, applied)
        if uuid not in out:
            out.append(uuid)
    return out


def mate_alignment_check(sk_or_doc: Any, *, mates: Any = None) -> list[str]:
    """**复核对齐方向**：用当前世界几何现算，返回"记录与几何不符"或"量不出/不支持"的配合 uuid。

    `mates=None` ⇒ 表里全部配合行；否则只查给定 uuid。复核**会刷新**这些行上的
    `alignment_geometric` / `alignment_satisfied` / `alignment_nm` / `alignment_dot`（数据后果）。

    三态口径（AGENTS.md E2.3）：量不出的行写 `alignment_geometric="unmeasured"`、
    `alignment_satisfied=None`（NA）、`alignment_check_reason=<reason>`，并**照样报出来** ——
    绝不把"没量"写成"没问题"（返回空列表 = 真绿：每条可量的配合都与其记录一致）。
    """
    store = _store(sk_or_doc)
    if mates is None:
        rows = _mate_rows(store)
    else:
        rows = [_mate_row(store, uuid) for uuid in _mates_arg(mates)]
    out: list[str] = []
    for row in rows:
        uuid = str(row.get("uuid"))
        if str(row.get("kind")) not in _ALIGN_KINDS:
            row["alignment_geometric"] = UNMEASURED_STATE
            row["alignment_satisfied"] = None
            row["alignment_check_reason"] = "alignment_unsupported_kind"
            if uuid not in out:
                out.append(uuid)
            continue
        first, second = _world_pair(store, row)
        try:
            geometric, projection, dot = _state_from_geometry(row, first, second)
        except CadError as exc:
            if exc.reason != "alignment_unmeasurable":
                raise
            row["alignment_geometric"] = UNMEASURED_STATE
            row["alignment_satisfied"] = None
            row["alignment_check_reason"] = str(exc.reason)
            if uuid not in out:
                out.append(uuid)
            continue
        recorded = _recorded_alignment(row)
        row["alignment"] = recorded
        row["alignment_geometric"] = geometric
        row["alignment_satisfied"] = bool(geometric == recorded)
        row["alignment_nm"] = None if projection is None else int(projection)
        row["alignment_dot"] = None if dot is None else float(dot)
        row["alignment_check_reason"] = None
        if geometric != recorded and uuid not in out:
            out.append(uuid)
    return out


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mate_align": mate_align,
    "mate_alignment_check": mate_alignment_check,
}


# ======================================================================================
# 自检（硬断言 + 阴性对照；任何异常一律 loud 落地）
# ======================================================================================


def _check(condition: Any, label: str) -> None:
    """硬断言：不成立当场炸（自检不许"看着过了"）。"""
    if not condition:
        raise AssertionError(label)


def _expect_error(reason: str, call: Callable[[], Any], label: str) -> None:
    """阴性对照：必须抛出**指定 reason** 的 `CadError`，否则自检当场炸（尺子看得见红）。"""
    try:
        call()
    except CadError as exc:
        _check(exc.reason == reason, "%s: reason=%r expected %r" % (label, exc.reason, reason))
        return
    raise AssertionError("%s: expected CadError(reason=%r)" % (label, reason))


def _l1_roundtrip(doc: Any) -> Any:
    """文档 → L1 文本 → 文档（真源往返；不是"我改过内存对象"）。"""
    payload = document_to_l1(doc)
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True,
                                                             ensure_ascii=False)
    back = document_from_l1(text)
    if isinstance(back, (list, tuple)) and back:
        back = back[0]
    _check(hasattr(back, "extra"), "L1 round trip must return a Document-like object, got %r"
           % (type(back).__name__,))
    return back


def _new_doc() -> Any:
    try:
        return new_document()
    except TypeError:  # pragma: no cover - 只在 new_document 需要必填入参时走到
        return Document()


def _selftest_body(checks: list[str]) -> None:
    """四组硬检查：距离类真平移 / 方向类真翻转 / 三态与阴性对照 / L1 往返。"""
    mm = 1_000_000
    try:
        doc = _new_doc()
    except Exception:  # pragma: no cover - 构造不出文档就没有可测对象
        doc = Document()
    part_a = _asm.insert_component(doc, part_key="base", at_nm=(0, 0, 0))[0]
    part_b = _asm.insert_component(doc, part_key="plate", at_nm=(0, 0, 10 * mm))[0]
    part_c = _asm.insert_component(doc, part_key="pin", at_nm=(0, 0, 0))[0]
    store = _store(doc)

    # ---- 组 1：距离配合 —— 反转对齐 = 真的挪到另一侧（整数纳米，可复算） ----------------
    mate_d = _asm.mate_distance(doc, refs=[[part_a, "origin"], [part_b, "origin"]],
                                value_nm=10 * mm)[0]
    _check(_asm.component_pose(doc, component=part_b)["at_nm"] == [0, 0, 10 * mm],
           "group1: pre-condition pose")
    moved = mate_align(doc, mates=[mate_d], aligned=False, apply=True)
    _check(moved == [mate_d], "group1: affected uuid list")
    pose_b = _asm.component_pose(doc, component=part_b)["at_nm"]
    _check(pose_b == [0, 0, -10 * mm], "group1: the plate must really move to the other side, "
           "got %r" % (pose_b,))
    row_d = _mate_row(store, mate_d)
    _check(row_d["flip"] is True and row_d["alignment"] == ANTI_ALIGNED,
           "group1: recorded intent")
    _check(row_d["alignment_nm"] == -10 * mm and row_d["alignment_satisfied"] is True,
           "group1: measured signed projection must be -10mm and satisfied, got %r"
           % (row_d["alignment_nm"],))
    _check(mate_alignment_check(doc, mates=[mate_d]) == [], "group1: check must be green after apply")
    checks.append("distance-flip-moves-plate-nm")

    # ---- 组 2：方向类配合 —— 绕第二参考点转 180°：方向翻向、点不动 --------------------
    mate_c = _asm.mate_coincident(doc, refs=[[part_a, "origin"], [part_c, "origin"]])[0]
    mate_align(doc, mates=[mate_c], aligned=False, apply=True)
    first, second = _world_pair(store, _mate_row(store, mate_c))
    dot = _dot(first["dir"], second["dir"])
    _check(dot < -0.999, "group2: directions must end up anti-parallel, dot=%r" % (dot,))
    gap = math.sqrt(sum(item * item for item in _sub(second["point"], first["point"])))
    _check(gap < 1e-6, "group2: the reference point must stay put, gap=%r" % (gap,))
    _check(_asm.component_pose(doc, component=part_c)["at_nm"] == [0, 0, 0],
           "group2: the pin rotates about its own reference point")
    _check(mate_alignment_check(doc, mates=[mate_c]) == [], "group2: check must be green after flip")
    checks.append("direction-flip-rotates-180-with-point-fixed")

    # ---- 组 3：三态与阴性对照（不支持的类 / 量不出 / 记录与几何不符 / 非 bool 入参） ----
    mate_perp = _asm.mate_perpendicular(doc, refs=[[part_a, "origin"], [part_c, "origin"]])[0]
    _expect_error("alignment_unsupported_kind",
                  lambda: mate_align(doc, mates=[mate_perp], aligned=False), "perpendicular")
    mate_zero = _asm.mate_distance(doc, refs=[[part_a, "origin"], [part_c, "origin"]], value_nm=0)[0]
    _expect_error("alignment_unmeasurable",
                  lambda: mate_align(doc, mates=[mate_zero], aligned=False), "zero distance")
    _expect_error("unknown_mate",
                  lambda: mate_align(doc, mates=["00000000-0000-0000-0000-000000000000"],
                                     aligned=True), "unknown mate")
    _expect_error("invalid_argument", lambda: mate_align(doc, mates=[mate_d], aligned=1),
                  "aligned must be a bool")
    _check(mate_alignment_check(doc, mates=[mate_perp]) == [mate_perp],
           "group3: an unsupported kind must be reported, never silently passed")
    check_zero = mate_alignment_check(doc, mates=[mate_zero])
    _check(check_zero == [mate_zero], "group3: an unmeasurable mate must be reported")
    _check(_mate_row(store, mate_zero)["alignment_satisfied"] is None,
           "group3: unmeasured must be NA, never False")
    mate_align(doc, mates=[mate_d], aligned=None, apply=False)  # 只写意图，不动几何
    row_intent = _mate_row(store, mate_d)
    _check(row_intent["alignment"] == ALIGNED and row_intent["alignment_satisfied"] is False,
           "group3: intent without apply must be recorded as unsatisfied")
    _check(mate_alignment_check(doc, mates=[mate_d]) == [mate_d],
           "group3: the ruler must see the mismatch (negative control)")
    mate_align(doc, mates=[mate_d], aligned=False, apply=True)
    _check(_asm.component_pose(doc, component=part_b)["at_nm"] == [0, 0, -10 * mm],
           "group3: re-applying must move the plate back to the anti side")
    checks.append("three-state-and-negative-controls")

    # ---- 组 4：L1 真源往返（意图、量值与真位姿都必须活下来） --------------------------
    back = _l1_roundtrip(doc)
    rows = _asm._table(_asm._store(back), "mates")
    row_back = [row for row in rows if str(row.get("uuid")) == mate_c][0]
    _check(row_back.get("alignment") == ANTI_ALIGNED, "group4: intent survives the L1 round trip")
    _check(abs(float(row_back.get("alignment_dot")) + 1.0) < 1e-6,
           "group4: measured dot survives the L1 round trip")
    _check(_asm.component_pose(back, component=part_c)["at_nm"] == [0, 0, 0],
           "group4: the flipped pose survives the L1 round trip")
    _check(mate_alignment_check(back, mates=[mate_d, mate_c]) == [],
           "group4: both verifiable mates must still be green on the reloaded document")
    checks.append("l1-roundtrip-keeps-alignment-and-pose")


def selftest() -> int:
    """自检：跑四组硬检查；全过 ⇒ 打印 `FEATURE_OK ... verdict=PASS` 并返回 0，否则返回 1。"""
    checks: list[str] = []
    try:
        _selftest_body(checks)
    except Exception as exc:  # 自检任何异常都要 loud 落地，绝不静默当通过
        print("FEATURE_ERROR family=%s op=%s verdict=FAIL reason=%s: %s"
              % (FAMILY, _MAIN_OP, type(exc).__name__, exc), file=sys.stderr)
        return 1
    print("FEATURE_GROUPS family=%s op=%s groups=%d %s"
          % (FAMILY, _MAIN_OP, len(checks), ",".join(checks)))
    print("FEATURE_OK family=%s op=%s verdict=PASS assertions=%d"
          % (FAMILY, _MAIN_OP, 20 + len(checks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
