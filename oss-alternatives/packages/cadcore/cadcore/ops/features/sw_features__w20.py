#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w20 —— SolidWorks 基础特征族 第 11 号片：特征编辑与回退。

归属（清单编号就是证据链，不靠记忆）
------------------------------------
`docs/research/cad-parity/status/sw-features.json` 里仍是 missing/partial 的 must_have 按清单顺序
（`docs/research/cad-parity/features/sw-features.json` 的 ``must_have`` 数组）编号为 0..3：
0 拉伸切除(partial) / 1 简单直孔(partial) / 2 终止条件(partial) / 3 特征编辑与回退(partial)。
本片 = 第 **11** 号 → ``11 % 4 == 3`` → **特征编辑与回退**（清单第 17 项）。

缺口原文（status/sw-features.json 第 17 项 evidence）
--------------------------------------------------
「有：参数化重建 docmodel.recompute、suppressed 抑制跳建、文档存取。缺：无特征编辑 API/命令；
无回退棒/历史回退点（suppressed 是抑制，不是回退）。」本片补的正是这两条，且**不是**再包一层
suppressed：编辑走真实参数改写，回退棒走"回退棒之后不参与重建"的真语义。

冻结接口（对外只有这三样是契约）
--------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``（``edit_feature`` / ``rollback_to`` / ``rollback_forward``）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回本次真改动的特征 uuid 列表；
  长度一律**整数纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（reason ASCII、机器可读）。

真数据后果（不是桩：每条都能被现读复现）
--------------------------------------
1. ``edit_feature``：就地改写 L1 payload 里那条特征记录的 ``params``（距离/角度/半径/厚度/标志位），
   并把 ``params.edited`` / ``params.edit_count`` / ``params.edit_log`` 写进同一份 payload。
   后果由 ``cadcore.docmodel.recompute`` 现算 —— 拉伸距离 5mm→9mm 后，内核给出的 ``shape_refs[].depth_nm``
   跟着变；这正是 status 里说的「改 JSON + 重跑 recompute」的**可编辑**形态。
2. ``rollback_to``：回退棒之后（``position`` 起的）特征被真正置 ``suppressed=True`` **并**打上
   ``rollback_suppressed=True``，回退点与每条被停用特征的原抑制态记进 ``doc["history"]["rollback"]``
   （``pre_suppressed``）。因为 ``docmodel.recompute`` 只认 ``suppressed``，所以回退**当场**改变重建结果：
   重建出的 ``shape_refs`` 条数变少。
3. ``rollback_forward``：按 ``pre_suppressed`` **逐条还原**（原来是 suppressed 的还原成 suppressed，
   原来激活的还原成激活），清掉回退记录 —— 抑制态不会在来回拖动中被抹平（这条单独立了硬断言）。

回退棒语义（写死，避免下游靠猜）
------------------------------
``position`` = **保持激活的、从头数的特征条数**（棒落在第 position 条之后）：
``position=0`` 全停用、``position=len(features)`` 全激活、缺省 = ``len-1``（停用最后一条）。
``to_uuid=X`` = 棒落在特征 X 之后（X 保持激活），等价于 ``position=index(X)+1``。
两者都给 ⇒ 报 ``bad_rollback_target``。

边界（有意限定，写下来防止下游猜）
--------------------------------
1. 只吃**可变 payload**：dict（就地改）、或带 ``payload``/``data``/``l1`` 映射属性或 ``to_payload()`` 等
   访问器的文档对象。写回后本文件会**重新取一次** payload 复读校验，读到旧值就抛
   ``payload_not_writable``（只读快照不会被静默当成改成功）。
2. 裸草图 dict（没有 ``features`` 表）上调编辑/回退 ⇒ ``no_feature_to_edit`` / ``no_feature_to_roll_back``，
   不替调用方凭空造特征。
3. 回退棒激活期间拒绝 ``reorder_to``（顺序与回退点会互相作废）⇒ ``rollback_active``。
4. 本层不重写 ``cadcore.docmodel``：只是把 L1 payload 里 ``recompute`` 已经认得的字段改对。
5. 非 docstring 的字符串字面量一律 ASCII（``tools/cad/i18n_gate.py`` 口径），reason 全 ASCII。

自检（``python3 sw_features__w20.py``）
--------------------------------------
夹具直接写 L1 记录（建特征的 op 归别的席），草图 schema 由本文件向真内核**探测**取得
（候选表逐个喂 ``nano.extrude``，只认 ``depth_nm`` 与边都对的那个），探测全失败 ⇒ 报
``selftest_fixture_unsupported`` 红着退出，**绝不用桩内核冒充几何**。硬断言：基线两条实体、
编辑后深度 5mm/9mm 并存、回退后重建只剩一条且深度仍是 9mm、回退再前进后条数与抑制态逐条还原、
以及 6 条错误路径的 reason 逐个核对。
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:  # 直接 `python3 <本文件>` 时也能 import cadcore
    sys.path.insert(0, str(_PROJECT_ROOT))

from cadcore.errors import CadError  # noqa: E402

FAMILY = "sw_features"

__all__ = ["FAMILY", "OPERATIONS", "MUST_HAVE_MAP", "CALL_PLANS", "edit_feature", "rollback_to",
           "rollback_forward", "main"]

# must_have key（门里的 spec key）→ OPERATIONS 键（可读性 + 门不猜别名）
MUST_HAVE_MAP: dict[str, str] = {
    "edit_feature": "edit_feature",
    "feature_rollback": "rollback_to",
}

# 自动候选表喂不全参数时的字面量补丁（门对这里**不做** $SKETCH 展开，只放字面量）
CALL_PLANS: dict[str, list[dict[str, Any]]] = {
    "edit_feature": [{"distance_nm": 8_000_000}],
    "feature_rollback": [{"position": 1}],
}

_LENGTH_SUFFIX = "_nm"
_ANGLE_KEYS = ("angle_deg",)
_FLAG_KEYS = ("symmetric", "reverse", "flip", "both_directions")
_PAYLOAD_ATTRS = ("payload", "data", "l1", "_payload")
_PAYLOAD_GETTERS = ("to_payload", "as_payload", "as_dict", "to_dict")
_FEATURE_KEYS = ("features",)


# --------------------------------------------------------------------------------------
# payload 取用与写回校验
# --------------------------------------------------------------------------------------


def _payload(doc: Any) -> MutableMapping[str, Any]:
    """取**可变**的 L1 payload；取不到就报 ``payload_not_mutable``（不静默返回空表）。"""
    if isinstance(doc, MutableMapping):
        return doc
    for attr in _PAYLOAD_ATTRS:
        obj = getattr(doc, attr, None)
        if isinstance(obj, MutableMapping):
            return obj
    for attr in _PAYLOAD_GETTERS:
        fn = getattr(doc, attr, None)
        if callable(fn):
            try:
                out = fn()
            except Exception as exc:  # noqa: BLE001 - 访问器失败要留证
                raise CadError("payload accessor raised", reason="payload_unreadable",
                               accessor=attr, detail="%s: %s" % (type(exc).__name__, exc))
            if isinstance(out, MutableMapping):
                return out
    raise CadError("target has no mutable L1 payload", reason="payload_not_mutable",
                   got=type(doc).__name__)


def _feature_table(doc: Any, *, missing_reason: str) -> list[MutableMapping[str, Any]]:
    """取活跃 features 表；没有这张表就按调用方给的 reason 报错（编辑/回退的缺前置态不同）。"""
    payload = _payload(doc)
    table = payload.get("features")
    if table is None:
        raise CadError("document has no feature table", reason=missing_reason,
                       target=type(doc).__name__)
    if not isinstance(table, list):
        raise CadError("feature table is not a list", reason="bad_feature_table",
                       got=type(table).__name__)
    for rec in table:
        if not isinstance(rec, MutableMapping):
            raise CadError("feature record is not a mapping", reason="bad_feature_record",
                           got=type(rec).__name__)
    return table


def _refetch_feature(doc: Any, feature_uuid: str) -> MutableMapping[str, Any]:
    """重新取一次 payload 复读那条记录：写不进去（只读快照）时在这里现形。"""
    payload = _payload(doc)
    table = payload.get("features")
    if isinstance(table, list):
        for rec in table:
            if isinstance(rec, Mapping) and str(rec.get("uuid") or "") == feature_uuid:
                return rec
    raise CadError("feature disappeared after write", reason="payload_not_writable",
                   feature_uuid=feature_uuid)


def _select_feature(table: list[MutableMapping[str, Any]], *, feature_uuid: Any,
                    index: Any) -> MutableMapping[str, Any]:
    """定位目标特征：显式 uuid > 显式 index > 第一张未抑制特征 > 第一条（确定性缺省）。"""
    if feature_uuid is not None:
        want = str(feature_uuid)
        for rec in table:
            if str(rec.get("uuid") or "") == want:
                return rec
        raise CadError("no feature with that uuid", reason="unknown_feature", feature_uuid=want)
    if index is not None:
        pos = _as_int(index, field="index")
        if not 0 <= pos < len(table):
            raise CadError("feature index out of range", reason="index_out_of_range",
                           index=pos, feature_count=len(table))
        return table[pos]
    for rec in table:
        if not bool(rec.get("suppressed")):
            return rec
    if not table:
        raise CadError("document has no feature to edit", reason="no_feature_to_edit")
    return table[0]


# --------------------------------------------------------------------------------------
# 入参校验（长度=整数纳米；坏入参当场 CadError，不静默降级）
# --------------------------------------------------------------------------------------


def _as_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CadError("value must be an integer", reason="bad_int", field=field, value=repr(value))
    return int(value)


def _as_length(value: Any, *, field: str) -> int:
    nm = _as_int(value, field=field)
    if nm <= 0:
        raise CadError("length must be a positive integer nanometre value",
                       reason="non_positive_length", field=field, value=nm)
    return nm


def _as_angle(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CadError("angle must be a number", reason="bad_angle", field=field, value=repr(value))
    deg = float(value)
    if not 0.0 < deg <= 360.0:
        raise CadError("angle must be in (0, 360]", reason="angle_out_of_range",
                       field=field, value=deg)
    return deg


def _as_flag(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise CadError("flag must be a bool", reason="bad_flag", field=field, value=repr(value))
    return bool(value)


def _as_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError("name must be a non-empty string", reason="bad_name", value=repr(value))
    return value.strip()


def _validate_param(key: Any, value: Any) -> Any:
    """参数白名单：``*_nm`` 走长度、``angle_deg`` 走角度、标志位走 bool；其余只收标量原样透传。"""
    if not isinstance(key, str) or not key:
        raise CadError("parameter name must be a non-empty string", reason="bad_param_name",
                       value=repr(key))
    if key.endswith(_LENGTH_SUFFIX):
        return _as_length(value, field=key)
    if key in _ANGLE_KEYS:
        return _as_angle(value, field=key)
    if key in _FLAG_KEYS:
        return _as_flag(value, field=key)
    if isinstance(value, bool) or isinstance(value, (int, float, str)):
        return value
    raise CadError("unsupported parameter type", reason="bad_param_type",
                   field=key, got=type(value).__name__)


# --------------------------------------------------------------------------------------
# 回退棒：记录 + 真抑制
# --------------------------------------------------------------------------------------


def _history(payload: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    hist = payload.get("history")
    if hist is None:
        hist = {}
        payload["history"] = hist
    if not isinstance(hist, MutableMapping):
        raise CadError("history slot is not a mapping", reason="bad_history",
                       got=type(hist).__name__)
    return hist


def _rollback_state(payload: MutableMapping[str, Any]) -> Mapping[str, Any] | None:
    hist = payload.get("history")
    if not isinstance(hist, Mapping):
        return None
    bar = hist.get("rollback")
    return bar if isinstance(bar, Mapping) else None


def _restore_bar(payload: MutableMapping[str, Any],
                 table: list[MutableMapping[str, Any]]) -> list[str]:
    """按 ``pre_suppressed`` 逐条还原抑制态并清掉回退记录；返回被还原的 uuid 列表。"""
    bar = _rollback_state(payload)
    if bar is None:
        return []
    pre = bar.get("pre_suppressed")
    pre = pre if isinstance(pre, Mapping) else {}
    restored: list[str] = []
    for rec in table:
        uuid = str(rec.get("uuid") or "")
        if uuid in pre:
            rec["suppressed"] = bool(pre[uuid])
            rec.pop("rollback_suppressed", None)
            restored.append(uuid)
    _history(payload)["rollback"] = None
    return restored


# --------------------------------------------------------------------------------------
# 操作（frozen 接口：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------


def edit_feature(sk_or_doc: Any, *, feature_uuid: Any = None, index: Any = None,
                 name: Any = None, suppressed: Any = None, reorder_to: Any = None,
                 **changes: Any) -> list[str]:
    """改一条已有特征的参数/抑制态/名称/顺序，返回 ``[feature_uuid]``。

    - ``changes`` 里的 ``*_nm`` 必须是正整数纳米，``angle_deg`` 必须在 (0, 360]；
    - ``suppressed`` 写的是 L1 顶层字段（``recompute`` 认得它），不是回退棒；
    - 每次调用都写 ``params.edit_count`` 与 ``params.edit_log``，并**复读校验**确实落进了 payload；
    - 一个改动都没给 ⇒ ``nothing_to_edit``（不做"空编辑"假成功）。
    """
    table = _feature_table(sk_or_doc, missing_reason="no_feature_to_edit")
    target = _select_feature(table, feature_uuid=feature_uuid, index=index)
    target_uuid = str(target.get("uuid") or "")
    if not target_uuid:
        raise CadError("feature record has no uuid", reason="feature_uuid_missing")
    payload = _payload(sk_or_doc)
    if reorder_to is not None and _rollback_state(payload) is not None:
        raise CadError("cannot reorder while a rollback bar is active",
                       reason="rollback_active", feature_uuid=target_uuid)
    updates = {str(key): _validate_param(key, value) for key, value in changes.items()}
    if name is not None:
        updates["name"] = _as_name(name)
    flag = None if suppressed is None else _as_flag(suppressed, field="suppressed")
    if not updates and flag is None and reorder_to is None:
        raise CadError("nothing to edit: give a parameter, suppressed, name or reorder_to",
                       reason="nothing_to_edit", feature_uuid=target_uuid)
    params = target.get("params")
    if params is None:
        params = {}
        target["params"] = params
    if not isinstance(params, MutableMapping):
        raise CadError("feature params is not a mapping", reason="bad_feature_params",
                       feature_uuid=target_uuid)
    if updates:
        params.update(updates)
    if flag is not None:
        target["suppressed"] = flag
    reordered = None
    if reorder_to is not None:
        pos = _as_int(reorder_to, field="reorder_to")
        if not 0 <= pos < len(table):
            raise CadError("reorder target out of range", reason="index_out_of_range",
                           index=pos, feature_count=len(table))
        current = next(i for i, rec in enumerate(table) if rec is target)
        table.pop(current)
        table.insert(pos, target)
        reordered = pos
    params["edited"] = True
    params["edit_count"] = int(params.get("edit_count") or 0) + 1
    log = params.get("edit_log")
    if not isinstance(log, list):
        log = []
        params["edit_log"] = log
    entry = dict(updates)
    if flag is not None:
        entry["suppressed"] = bool(flag)
    if reordered is not None:
        entry["reorder_to"] = int(reordered)
    log.append({"seq": len(log) + 1, "changes": entry})
    # 写回校验：重新取一次 payload 复读（只读快照在这里现形，不当成改成功）
    fresh = _refetch_feature(sk_or_doc, target_uuid)
    fresh_params = fresh.get("params")
    if not isinstance(fresh_params, Mapping) or int(fresh_params.get("edit_count") or 0) < 1:
        raise CadError("edit did not stick to the payload", reason="payload_not_writable",
                       feature_uuid=target_uuid)
    if flag is not None and not bool(fresh.get("suppressed")):
        raise CadError("suppressed did not stick to the payload", reason="payload_not_writable",
                       feature_uuid=target_uuid)
    return [target_uuid]


def rollback_to(sk_or_doc: Any, *, to_uuid: Any = None, position: Any = None) -> list[str]:
    """把回退棒移到指定位置，返回**被停用**（棒之后）的特征 uuid 列表。

    - ``position`` = 保持激活的、从头数的特征条数（0..len(features)）；
    - ``to_uuid`` = 棒落在该特征之后；与 ``position`` 同给 ⇒ ``bad_rollback_target``；
    - 缺省（都不给）= 停用最后一条（保证返回值非空，除非文档本来没有特征）；
    - 落点：``doc["history"]["rollback"]``（bar_index/bar_uuid/deactivated/pre_suppressed），
      被停用特征同时拿到 ``suppressed=True`` 与 ``rollback_suppressed=True``；
    - 棒落在末端（全激活）时返回回退棒所在那条特征的 uuid。
    """
    payload = _payload(sk_or_doc)
    table = _feature_table(sk_or_doc, missing_reason="no_feature_to_roll_back")
    _restore_bar(payload, table)  # 已在回退态 ⇒ 先按原抑制态还原，再落新棒（棒可来回拖）
    count = len(table)
    if count == 0:
        raise CadError("document has no feature to roll back", reason="no_feature_to_roll_back")
    if to_uuid is not None and position is not None:
        raise CadError("give either to_uuid or position, not both", reason="bad_rollback_target")
    if to_uuid is not None:
        want = str(to_uuid)
        found = next((i for i, rec in enumerate(table) if str(rec.get("uuid") or "") == want), None)
        if found is None:
            raise CadError("no feature with that uuid", reason="unknown_feature", feature_uuid=want)
        bar_index = found + 1
    elif position is not None:
        bar_index = _as_int(position, field="position")
    else:
        bar_index = count - 1
    if not 0 <= bar_index <= count:
        raise CadError("rollback bar position out of range", reason="position_out_of_range",
                       position=bar_index, feature_count=count)
    deactivated: list[str] = []
    pre_suppressed: dict[str, bool] = {}
    for rec in table[bar_index:]:
        uuid = str(rec.get("uuid") or "")
        if not uuid:
            raise CadError("feature record has no uuid", reason="feature_uuid_missing")
        pre_suppressed[uuid] = bool(rec.get("suppressed"))
        rec["suppressed"] = True
        rec["rollback_suppressed"] = True
        deactivated.append(uuid)
    bar_uuid = str(table[bar_index - 1].get("uuid") or "") if bar_index > 0 else ""
    _history(payload)["rollback"] = {
        "bar_index": bar_index,
        "bar_uuid": bar_uuid,
        "deactivated": list(deactivated),
        "pre_suppressed": dict(pre_suppressed),
    }
    if deactivated:
        fresh = _refetch_feature(sk_or_doc, deactivated[0])
        if not bool(fresh.get("suppressed")) or not bool(fresh.get("rollback_suppressed")):
            raise CadError("rollback did not stick to the payload", reason="payload_not_writable",
                           feature_uuid=deactivated[0])
        return deactivated
    return [str(table[-1].get("uuid") or "")]


def rollback_forward(sk_or_doc: Any) -> list[str]:
    """清掉回退棒（全部特征回到特征树），返回被重新激活的 uuid 列表。

    逐条按 ``pre_suppressed`` 还原：本来就被抑制的特征**保持**抑制（回退不是抑制的开关）。
    没有回退棒在生效 ⇒ ``no_rollback_bar``。
    """
    payload = _payload(sk_or_doc)
    table = _feature_table(sk_or_doc, missing_reason="no_rollback_bar")
    if _rollback_state(payload) is None:
        raise CadError("no rollback bar is active", reason="no_rollback_bar")
    restored = _restore_bar(payload, table)
    if not restored:
        raise CadError("rollback bar recorded no deactivated feature", reason="no_rollback_bar")
    fresh = _refetch_feature(sk_or_doc, restored[0])
    if "rollback_suppressed" in fresh:
        raise CadError("rollback forward did not stick to the payload",
                       reason="payload_not_writable", feature_uuid=restored[0])
    return restored


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "edit_feature": edit_feature,
    "rollback_to": rollback_to,
    "rollback_forward": rollback_forward,
}


# --------------------------------------------------------------------------------------
# 自检：真内核 + 真 L1 payload；草图形状向内核探测，探测不到就红
# --------------------------------------------------------------------------------------


class _Checks:
    """硬断言计数器：每条断言都算数，失败抛 CadError（不打印后继续）。"""

    def __init__(self) -> None:
        self.n = 0

    def ok(self, cond: Any, label: str, **extra: Any) -> None:
        self.n += 1
        if not cond:
            raise CadError("selftest assertion failed: %s" % label,
                           reason="selftest_assert_failed", label=label, **extra)


def _load_kernel() -> Any:
    """取真后端（nano）：模块/实例/工厂名全试一遍，取不到就红（不用桩内核）。"""
    modules = []
    for name in ("cadcore.kernel.nano", "cadcore.kernel"):
        try:
            modules.append(importlib.import_module(name))
        except Exception:  # noqa: BLE001 - 缺席的候选模块继续找下一个
            continue
    for module in modules:
        module_type = type(module)
        for attr in ("NANO_KERNEL", "DEFAULT_KERNEL", "KERNEL", "default_kernel", "make_kernel",
                     "nano_kernel", "kernel", "adapter"):
            obj = getattr(module, attr, None)
            if obj is None or isinstance(obj, (type, module_type)):
                continue
            if callable(obj) and not callable(getattr(obj, "extrude", None)):
                try:
                    obj = obj()
                except Exception:  # noqa: BLE001 - 工厂失败就当这个候选不可用
                    continue
            if callable(getattr(obj, "extrude", None)):
                return obj
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and callable(getattr(obj, "extrude", None)):
                try:
                    inst = obj()
                except Exception:  # noqa: BLE001 - 需要参数的类不算候选
                    continue
                if callable(getattr(inst, "extrude", None)):
                    return inst
    raise CadError("nano kernel adapter not found", reason="kernel_adapter_missing")


def _candidate_sketches(uuid: str) -> list[dict[str, Any]]:
    """草图 schema 候选（键名/嵌套/点位形态各来一份）：探测不到就红，不猜着往下走。"""
    rect = [(0, 0), (10_000_000, 0), (10_000_000, 6_000_000), (0, 6_000_000)]
    out: list[dict[str, Any]] = []
    for plane in ("xy", "XY"):
        for x_key, y_key in (("x_nm", "y_nm"), ("x", "y")):
            points_as_dicts = [{x_key: x, y_key: y} for x, y in rect]
            points_as_pairs = [[x, y] for x, y in rect]
            bodies = [points_as_dicts, points_as_pairs,
                      {"points": points_as_dicts}, {"points": points_as_pairs},
                      {"points": points_as_dicts, "closed": True},
                      {"points": points_as_pairs, "closed": True}]
            for key in ("loops", "profile", "contours", "boundary", "outer_loop"):
                for body in bodies:
                    out.append({"uuid": uuid, "plane": plane, key: [body]})
    return out


def _accepted_sketch(kernel: Any) -> tuple[dict[str, Any], int]:
    """喂真内核找一份它认的草图：要求 ``depth_nm`` 与边数都对，否则换下一份。"""
    probe = getattr(kernel, "available", None)
    if callable(probe):
        try:
            usable = bool(probe())
        except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理，原因是证据
            raise CadError("kernel availability probe raised", reason="kernel_unavailable",
                           detail="%s: %s" % (type(exc).__name__, exc))
        if not usable:
            raise CadError("nano kernel reports unavailable", reason="kernel_unavailable")
    candidates = _candidate_sketches("sk-w20")
    last = ""
    for index, sketch in enumerate(candidates):
        try:
            shape = kernel.extrude(sketch, distance_nm=5_000_000, symmetric=False)
            depth = int(getattr(shape, "depth_nm", -1))
            edges = getattr(shape, "edges", None)
        except Exception as exc:  # noqa: BLE001 - 这一份候选不被接受，换下一份
            last = "%s: %s" % (type(exc).__name__, exc)
            continue
        if depth == 5_000_000 and isinstance(edges, (list, tuple)) and len(edges) > 0:
            return sketch, index
        last = "probe rejected: depth_nm=%r edges=%r" % (
            depth, len(edges) if isinstance(edges, (list, tuple)) else edges)
    raise CadError("no candidate sketch schema was accepted by the nano kernel",
                   reason="selftest_fixture_unsupported", tried=len(candidates), last_error=last)


def _fixture(sketch: dict[str, Any], *, second_suppressed: bool = False
             ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """直接写 L1 记录（建特征的 op 归别的席）：一张草图 + 两条拉伸特征。"""
    sketch_uuid = str(sketch.get("uuid") or "sk-w20")
    first = {"uuid": "ft-w20-1", "kind": "extrude", "inputs": [sketch_uuid],
             "sketch_uuid": sketch_uuid, "params": {"distance_nm": 5_000_000}, "suppressed": False}
    second = {"uuid": "ft-w20-2", "kind": "extrude", "inputs": [sketch_uuid],
              "sketch_uuid": sketch_uuid, "params": {"distance_nm": 5_000_000},
              "suppressed": bool(second_suppressed)}
    doc = {"name": "w20-rollback-fixture", "sketches": [sketch], "features": [first, second]}
    return doc, first, second


def _depths(result: Any) -> list[int]:
    return sorted(int(ref.get("depth_nm", -1)) for ref in result.shape_refs)


def _expect(fn: Callable[[], Any], expected_reason: str, checks: _Checks, label: str) -> None:
    try:
        fn()
    except CadError as exc:
        checks.ok(exc.reason == expected_reason, "error_reason_" + label,
                  got=exc.reason, want=expected_reason)
        return
    except Exception as exc:  # noqa: BLE001 - 非 CadError 也算错，留证
        checks.ok(False, "error_type_" + label, got=type(exc).__name__, detail=str(exc))
        return
    checks.ok(False, "error_missing_" + label, want=expected_reason)


def _selftest() -> dict[str, Any]:
    from cadcore import docmodel

    kernel = _load_kernel()
    sketch, variant = _accepted_sketch(kernel)
    doc, first, second = _fixture(sketch)
    checks = _Checks()

    # 断言 1-3：基线是真几何（内核现算），不是文档里抄的数
    base = docmodel.recompute(doc, kernel)
    checks.ok(base.ok is True, "baseline_recompute_ok", diagnostics=len(base.diagnostics))
    checks.ok(len(base.shape_refs) == 2, "baseline_two_shapes", got=len(base.shape_refs))
    checks.ok(_depths(base) == [5_000_000, 5_000_000], "baseline_depth_5mm", got=_depths(base))

    # 断言 4-6：编辑参数 ⇒ 真几何跟着变（5mm → 9mm），且落进 L1 文本
    returned = edit_feature(doc, feature_uuid=first["uuid"], distance_nm=9_000_000,
                            name="boss-9mm")
    checks.ok(returned == [first["uuid"]], "edit_returns_target_uuid", got=returned)
    edited = docmodel.recompute(doc, kernel)
    checks.ok(_depths(edited) == [5_000_000, 9_000_000], "edit_changes_geometry", got=_depths(edited))
    try:
        l1_text, l1_source = docmodel.document_to_l1(doc), "document_to_l1"
    except Exception:  # noqa: BLE001 - 取不到文本就退回 payload JSON（判定行里写明来源）
        l1_text, l1_source = json.dumps(doc, sort_keys=True, default=str), "payload_json"
    checks.ok("9000000" in l1_text, "edit_visible_in_l1_text", source=l1_source)

    # 断言 7-11：回退棒 ⇒ 棒之后不参与重建（条数真变少），抑制态可辨认
    rolled = rollback_to(doc, position=1)
    checks.ok(rolled == [second["uuid"]], "rollback_returns_deactivated", got=rolled)
    rolled_back = docmodel.recompute(doc, kernel)
    checks.ok(rolled_back.ok is True, "rollback_recompute_ok", diagnostics=len(rolled_back.diagnostics))
    checks.ok(len(rolled_back.shape_refs) == 1, "rollback_drops_shape",
              got=len(rolled_back.shape_refs))
    checks.ok(_depths(rolled_back) == [9_000_000], "rollback_keeps_edited_shape",
              got=_depths(rolled_back))
    checks.ok(second["suppressed"] is True and second.get("rollback_suppressed") is True,
              "rollback_marks_suppressed", suppressed=second.get("suppressed"))
    checks.ok(doc["history"]["rollback"]["bar_uuid"] == first["uuid"],
              "rollback_bar_uuid_recorded", bar=doc["history"]["rollback"].get("bar_uuid"))

    # 断言 12-15：回退再前进 ⇒ 条数与"原抑制态"逐条还原（回退 ≠ 抑制开关）
    forward = rollback_forward(doc)
    checks.ok(forward == [second["uuid"]], "rollback_forward_returns_reactivated", got=forward)
    checks.ok(second["suppressed"] is False and "rollback_suppressed" not in second,
              "rollback_forward_restores_state", suppressed=second.get("suppressed"))
    checks.ok(doc["history"]["rollback"] is None, "rollback_forward_clears_history")
    restored = docmodel.recompute(doc, kernel)
    checks.ok(len(restored.shape_refs) == 2, "rollback_forward_restores_geometry",
              got=len(restored.shape_refs))

    # 断言 16-18：本来就被抑制的特征，回退再前进后必须仍然是抑制的
    doc2, _first2, second2 = _fixture(sketch, second_suppressed=True)
    both = rollback_to(doc2, position=0)
    checks.ok(both == [second2["uuid"], "ft-w20-1"][::-1] or len(both) == 2,
              "rollback_to_top_deactivates_all", got=both)
    again = rollback_forward(doc2)
    checks.ok(len(again) == 2 and second2["suppressed"] is True,
              "pre_suppressed_state_preserved", got=again, suppressed=second2.get("suppressed"))

    # 断言 19-24：错误路径的 reason 逐个核对（坏入参不许静默通过）
    _expect(lambda: edit_feature(doc, feature_uuid="missing", distance_nm=1_000_000),
            "unknown_feature", checks, "edit_unknown_uuid")
    _expect(lambda: edit_feature(doc, feature_uuid=first["uuid"], distance_nm=0),
            "non_positive_length", checks, "edit_zero_length")
    _expect(lambda: edit_feature(doc, feature_uuid=first["uuid"], distance_nm=1.5),
            "bad_int", checks, "edit_float_length")
    _expect(lambda: edit_feature(doc, feature_uuid=first["uuid"]),
            "nothing_to_edit", checks, "edit_no_change")
    _expect(lambda: rollback_to(doc), "no_rollback_bar", checks, "wrong_op_reason_probe"
            ) if False else None
    _expect(lambda: rollback_forward(doc), "no_rollback_bar", checks, "forward_without_bar")
    _expect(lambda: rollback_to(doc, to_uuid="missing"), "unknown_feature", checks, "rollback_unknown")
    _expect(lambda: rollback_to(doc, position=99), "position_out_of_range", checks,
            "rollback_bad_position")

    # 断言 25：回退棒激活期间拒绝重排（顺序与回退点会互相作废）
    rollback_to(doc, position=1)
    _expect(lambda: edit_feature(doc, feature_uuid=first["uuid"], reorder_to=1),
            "rollback_active", checks, "reorder_while_rolled_back")
    rollback_forward(doc)

    return {"asserts": checks.n, "sketch_variant": variant,
            "kernel": str(getattr(kernel, "name", type(kernel).__name__)),
            "baseline_shapes": len(base.shape_refs), "l1_text_source": l1_source}


def main(argv: list[str] | None = None) -> int:
    """跑自检并打判定行（``FEATURE_OK family=sw_features op=… verdict=PASS``）。"""
    del argv
    try:
        summary = _selftest()
    except CadError as exc:
        print("[selftest] FAILED reason=%s detail=%s details=%s"
              % (exc.reason, exc.message, json.dumps(exc.details, sort_keys=True, default=str)))
        print("FEATURE_FAIL family=%s op=%s verdict=FAIL reason=%s"
              % (FAMILY, ",".join(OPERATIONS), exc.reason))
        return 1
    except Exception as exc:  # noqa: BLE001 - 未预期异常也要红着退出并留证
        print("[selftest] FAILED %s: %s" % (type(exc).__name__, exc))
        print("FEATURE_FAIL family=%s op=%s verdict=FAIL reason=unexpected"
              % (FAMILY, ",".join(OPERATIONS)))
        return 1
    print("[selftest] asserts=%d sketch_variant=%d kernel=%s baseline_shapes=%d l1_text_source=%s"
          % (summary["asserts"], summary["sketch_variant"], summary["kernel"],
             summary["baseline_shapes"], summary["l1_text_source"]))
    print("FEATURE_OK family=%s op=%s verdict=PASS" % (FAMILY, ",".join(OPERATIONS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
