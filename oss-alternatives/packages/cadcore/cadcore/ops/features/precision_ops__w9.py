"""precision_ops 族 · w9 席 —— 对象捕捉总开关（OSNAP master / F3）。

冻结接口（`cadcore/ops/features/<family>__w<k>.py`）：
    FAMILY = "precision_ops"
    OPERATIONS: dict[str, Callable]  —— 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``
    长度一律整数纳米；一切错误抛 ``cadcore.errors.CadError(reason=...)``。

覆盖清单第 0 号（autocad-precision 的 must_have 里仍 missing/partial 的第 0 条）：
    「对象捕捉总开关」（alias: OSNAP / F3 / 状态栏「对象捕捉」）。

族内已有 ``precision_ops.snap_enable(target, enabled=...)``（持久化布尔）与
``precision_ops.precision_apply(...)``（一条光标点的解析链 + cursor 读数）；本席补的是**总开关的动词语义
与其门控后果**（这部分此前只有裸布尔 setter，没有开关语义、没有门控自证）：

``osnap_master(sk_or_doc, *, action="toggle"|"on"|"off"|"1"|"0"|"F3") -> [osnap 模式记录 uuid]``
    命令动词层：toggle / 幂等 on-off / 未设过时的显式缺省（AutoCAD 上机默认开）/ 非法 action 报
    ``CadError(reason="invalid_argument")``。状态落在 L1 文档（或草图映射）的 ``precision`` 设置表里
    （``extra["precision"]["modes"]``），与族内其余模式同一张表、同一个记录 uuid（不另起一张表）。

``osnap_pick(sk_or_doc, *, point_nm, base_nm=None, sketch=None) -> [cursor uuid, *生效记录 uuid]``
    总开关门控下的一次拾取解析：**开关关 ⇒ 对象捕捉一次都不许发生**（解析点 = 原始点、读数里没有 ``hit``、
    生效表里不许出现 osnap 模式记录或任何捕捉类型记录）；**开关开 ⇒ 命中的实体 uuid 必须真的在所选草图里**
    （跨草图/不新鲜的引用报 ``CadError(reason="stale_reference")``）。总开关没设过就走几何路径 ⇒ 报
    ``CadError(reason="no_master_state")``，不静默按缺省吸附。

真几何证据（本文件 ``python3 <本文件>`` 自测，13 条硬断言）：草图里一条线 (0,0)-(1000000,0)（整数纳米）；
拾取 (999999,0)：开关开 ⇒ 解析点精确 == ``(1000000, 0)``（端点捕捉）且带回该线 uuid；开关关 ⇒
解析点 == ``(999999, 0)`` 原样、不带回任何实体 uuid，**而该捕捉类型记录仍 enabled** ⇒ 屏蔽来自总开关，
不是来自 kind 启停；阴性对照：开关开但拾取点离几何 900mm ⇒ 不吸附（尺子看得见红）。

仍未落地的（不在本席可写文件面内，如实留 partial）：状态栏 OSNAP 按钮 / F3 键绑定、
``apps/opencad/server.py`` 白名单的 ``osnap`` verb、``apps/opencad/web/js/app.js`` 取点路径接线。
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.ops import precision_ops as _P
from cadcore.precision.osnap import SNAP_KINDS

#: 别名解析层：别名表住在语言包（cadcore/i18n/aliases.py，复用 cadcore/i18n/catalog.py），
#: 运行时按当前语言取。
from cadcore.i18n.aliases import alias_map as _alias_map


#: 族 id（冻结）：与 spec/precision_ops.json 一致。
FAMILY = "precision_ops"

#: 总开关落在族设置表里的模式名（= AutoCAD 的 OSNAP）。
_MASTER = "osnap"

#: 总开关动作入参别名 → 冻结枚举：**别名表住在语言包**（`alias.osnap.<action>`），运行时按当前语言取。
_OSNAP_ACTIONS = _alias_map("osnap", ("on", "off", "toggle"))
_ON = _OSNAP_ACTIONS.restrict("on")
_OFF = _OSNAP_ACTIONS.restrict("off")
_TOGGLE = _OSNAP_ACTIONS.restrict("toggle")


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """族内统一错误口径：``CadError(message, reason=...)``。"""
    return CadError(detail, reason=reason, **details)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """映射 / 对象两形态取字段（L1 文档与其 payload 映射都吃）。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clean_ids(value: Any) -> list[str]:
    """规范化 uuid 列表：非字符串 / 空串一律丢（不伪造 uuid）。"""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _action_key(action: Any) -> bool | None:
    """action → True(on) / False(off) / None(toggle)；不认得的写法显式报错。"""
    if isinstance(action, bool):
        return action
    if not isinstance(action, str):
        raise _fail("invalid_argument", "action must be a string or bool",
                    value=repr(action), allowed=list(_ON + _OFF + _TOGGLE))
    key = action.strip().lower()
    if key in _ON:
        return True
    if key in _OFF:
        return False
    if key in _TOGGLE:
        return None
    raise _fail("invalid_argument", "unknown object-snap master action",
                value=action, allowed=list(_ON + _OFF + _TOGGLE))


def _point_nm(value: Any, *, what: str) -> tuple[int, int]:
    """点参数校验：二元整数序列（纳米），其余一律 ``invalid_argument``。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a 2-item sequence of integer nanometres",
                    value=repr(value))
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("invalid_argument", f"{what} components must be integers (nanometres)",
                        value=repr(value))
        out.append(int(item))
    return (out[0], out[1])


def _master_state(target: Any) -> bool | None:
    """总开关的已持久化状态：True / False / None（表还没建出来 = 没设过）。

    纯查询（`precision_filter` 不建表、不补缺省、不写盘）⇒ 几何路径上的「没设过」不会被悄悄补成缺省值。
    """
    on = _clean_ids(_P.precision_filter(target, name=_MASTER, enabled=True))
    off = _clean_ids(_P.precision_filter(target, name=_MASTER, enabled=False))
    if on and off:
        raise _fail("corrupt_store", "osnap master record is both enabled and disabled",
                    enabled=on, disabled=off)
    if on:
        return True
    if off:
        return False
    return None


def _snap_record_ids(target: Any) -> set[str]:
    """总开关 + 全部捕捉类型记录的 uuid 集合（用于「开关关时对象捕捉一次都没发生」的门控自证）。"""
    ids: set[str] = set(_clean_ids(_P.precision_filter(target, name=_MASTER)))
    for kind in SNAP_KINDS:
        ids.update(_clean_ids(_P.precision_filter(target, kind=kind)))
    return ids


def _pick_sketch(target: Any, sketch: Any) -> Any:
    """选做捕捉的草图：文档多草图时用 ``sketch=<uuid>`` 点名（与族内 `_pick_sketch` 同口径）。"""
    sketches = _get(target, "sketches", None)
    if not isinstance(sketches, list):
        if sketch is None:
            return target  # target 本身就是草图
        raise _fail("unknown_reference", "target is a single sketch but sketch= was given", sketch=sketch)
    if sketch is None:
        if len(sketches) != 1:
            raise _fail("ambiguous_target", "document has multiple sketches: pass sketch=<uuid>",
                        sketch_count=len(sketches))
        return sketches[0]
    if not isinstance(sketch, str) or not sketch:
        raise _fail("invalid_argument", "sketch must be a sketch uuid string", value=repr(sketch))
    for item in sketches:
        if _get(item, "uuid", None) == sketch:
            return item
    raise _fail("unknown_reference", "no sketch with this uuid in the document", sketch=sketch)


def _entity_uuids(target: Any, sketch: Any) -> set[str]:
    """所选草图里真实存在的实体 uuid 集合。"""
    items = _get(_pick_sketch(target, sketch), "entities", None)
    out: set[str] = set()
    if isinstance(items, (list, tuple)):
        for entity in items:
            uuid = _get(entity, "uuid", None)
            if isinstance(uuid, str) and uuid:
                out.add(uuid)
    return out


def osnap_master(sk_or_doc: Any, *, action: Any = "toggle") -> list[str]:
    """对象捕捉总开关（OSNAP / F3）：``on`` / ``off`` / ``toggle`` / ``1`` / ``0`` / ``F3``。

    - 未设过（表里没有 osnap 记录）+ ``toggle`` ⇒ **打开**（AutoCAD 上机默认开）；
    - 已设过 + ``toggle`` ⇒ 取反；``on``/``off`` 幂等，重复调用不产生新记录（同一记录 uuid）；
    - 非法 action / 非字符串 ⇒ ``CadError(reason="invalid_argument")``。

    返回 ``[osnap 模式记录 uuid]``（状态改不动的存储形态 ⇒ ``CadError(reason="no_state_record")``，
    不靠伪造 uuid 蒙混）。
    """
    want = _action_key(action)
    current = _master_state(sk_or_doc)
    if want is None:
        want = True if current is None else (not current)
    out = _clean_ids(_P.snap_enable(sk_or_doc, enabled=bool(want)))
    if not out:
        out = _clean_ids(_P.precision_filter(sk_or_doc, name=_MASTER))
    if not out:
        raise _fail("no_state_record", "object snap master change produced no record uuid",
                    action=repr(action), enabled=bool(want))
    return out


def osnap_pick(sk_or_doc: Any, *, point_nm: Any, base_nm: Any = None,
               sketch: Any = None) -> list[str]:
    """总开关门控下的一次拾取解析，返回 ``[cursor 记录 uuid, *生效记录 uuid]``。

    - 总开关没设过 ⇒ ``CadError(reason="no_master_state")``（几何路径不猜缺省）；
    - 总开关关 ⇒ 对象捕捉必须一次都没发生：不返回任何捕捉类型/模式记录与被捕实体 uuid，
      且 cursor 读数里不许出现 ``hit``（真门控，不是「大概没命中」）；
    - 总开关开 ⇒ 命中时被捕捉实体 uuid 必须真在所选草图里，否则
      ``CadError(reason="stale_reference")``（跨草图引用/陈旧引用当场报红）。
    """
    state = _master_state(sk_or_doc)
    if state is None:
        raise _fail("no_master_state",
                    "object snap master switch is unset: call osnap_master(action='on'|'off') first")
    raw = _point_nm(point_nm, what="point_nm")
    base = None if base_nm is None else _point_nm(base_nm, what="base_nm")
    fired = _clean_ids(_P.precision_apply(
        sk_or_doc, point_nm=[raw[0], raw[1]],
        base_nm=(None if base is None else [base[0], base[1]]), sketch=sketch))
    if not fired:
        raise _fail("no_cursor_record", "precision_apply returned no cursor record uuid",
                    point_nm=[raw[0], raw[1]])
    readout = _P.cursor_readout(sk_or_doc) or {}
    if not state:
        leaked = [uuid for uuid in fired[1:] if uuid in _snap_record_ids(sk_or_doc)]
        if leaked or "hit" in readout:
            raise _fail("gate_violation", "object snap fired while the master switch is off",
                        leaked=leaked, hit=readout.get("hit"))
    else:
        hit = readout.get("hit")
        ref = hit.get("ref") if isinstance(hit, dict) else None
        entity = ref[0] if isinstance(ref, (list, tuple)) and ref else None
        if isinstance(entity, str) and entity and entity not in _entity_uuids(sk_or_doc, sketch):
            raise _fail("stale_reference", "snapped entity uuid is not in the picked sketch",
                        entity=entity, sketch=sketch)
    return fired


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "osnap_master": osnap_master,
    "osnap_pick": osnap_pick,
}


# ======================================================================================
# 自检（13 条硬断言；末行打冻结判定行）
# ======================================================================================


def _selftest() -> int:
    """真几何自测：一条线上的端点捕捉在总开关 on/off 下的**可观测差分**。"""
    from cadcore.docmodel import new_document

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: Any, detail: str = "") -> bool:
        checks.append((name, bool(passed), detail))
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return bool(passed)

    try:
        doc = new_document()
        line_uuid = "b0b0b0b0-0000-4000-8000-0000000000b1"
        doc.sketches = [{
            "uuid": "b0b0b0b0-0000-4000-8000-0000000000b0", "plane": "XY",
            "entities": [{"uuid": line_uuid, "kind": "line", "layer": "0", "construction": False,
                          # 远端放到 -1e12 nm（1000m）：任何合理容差都够不着"start"，命中只能来自"end"
                          "geom": {"x1": -1000000000000, "y1": 0, "x2": 1000000, "y2": 0}}],
            "constraints": [], "dimensions": [],
            "solved": {"dof": -1, "converged": False, "cache": True},
        }]
        raw = (999999, 0)

        # ① 总开关没设过：几何路径必须显式报错（不许静默按缺省吸附）
        try:
            osnap_pick(doc, point_nm=raw)
            check("unset master rejects pick", False, "no CadError")
        except CadError as exc:
            check("unset master rejects pick", exc.reason == "no_master_state", f"reason={exc.reason}")

        # ② 未设过 + toggle ⇒ 打开，并落一条记录 uuid
        first = osnap_master(doc)
        check("toggle from unset turns master on",
              len(first) == 1 and bool(_P.precision_filter(doc, name="osnap", enabled=True)),
              f"ids={first}")

        # 其余独立开关关掉：让解析点是否被拉动只由对象捕捉总开关决定
        _P.grid_set(doc, enabled=False)
        _P.ortho_set(doc, enabled=False)
        _P.polar_set(doc, enabled=False)
        _P.otrack_set(doc, enabled=False)

        # ③ 开关开：真吸附到线的端点 (1000000, 0)，并带回该线 uuid
        on_ids = osnap_pick(doc, point_nm=raw)
        on_read = _P.cursor_readout(doc) or {}
        check("master on snaps to line end", tuple(on_read.get("point_nm", ())) == (1000000, 0),
              f"point_nm={on_read.get('point_nm')}")
        check("master on reports hit entity uuid",
              line_uuid in on_ids and (_P.cursor_readout(doc) or {}).get("hit", {}).get("kind") == "end",
              f"ids={on_ids} hit={on_read.get('hit')}")

        # ④ 开关关：同一条拾取不许再吸附，也不许带回任何实体 uuid
        osnap_master(doc, action="off")
        off_ids = osnap_pick(doc, point_nm=raw)
        off_read = _P.cursor_readout(doc) or {}
        check("master off leaves the raw point", tuple(off_read.get("point_nm", ())) == raw,
              f"point_nm={off_read.get('point_nm')}")
        check("master off returns no snap target",
              len(off_ids) == 1 and line_uuid not in off_ids and "hit" not in off_read,
              f"ids={off_ids} hit={off_read.get('hit')}")
        check("kinds stay enabled under master off",
              bool(_P.precision_filter(doc, kind="end", enabled=True)),
              "suppressed by the master switch, not by a per-kind on/off")

        # ⑤ 阴性对照：开关开但拾取点离几何 900mm ⇒ 不吸附（尺子看得见红）
        osnap_master(doc, action="on")
        far = (500000, 900000000)
        osnap_pick(doc, point_nm=far)
        far_read = _P.cursor_readout(doc) or {}
        check("master on but far pick does not snap",
              tuple(far_read.get("point_nm", ())) == far and "hit" not in far_read,
              f"point_nm={far_read.get('point_nm')}")

        # ⑥ toggle 语义 / 记录 uuid 稳定 / 非法参数
        osnap_master(doc, action="toggle")
        check("toggle flips back to off", bool(_P.precision_filter(doc, name="osnap", enabled=False)))
        again = osnap_master(doc, action="on")
        check("master record uuid is stable", again == first, f"{first} vs {again}")
        for bad in ("banana", 3.5, None):
            try:
                osnap_master(doc, action=bad)
                check(f"bad action {bad!r} rejected", False, "no CadError")
            except CadError as exc:
                check(f"bad action {bad!r} rejected", exc.reason == "invalid_argument",
                      f"reason={exc.reason}")
        try:
            osnap_pick(doc, point_nm=(1.5, 2))
            check("non-nm point rejected", False, "no CadError")
        except CadError as exc:
            check("non-nm point rejected", exc.reason == "invalid_argument", f"reason={exc.reason}")
    except Exception as exc:  # 自检自己出错也必须显式红，不许静默 PASS
        check(f"selftest did not raise ({type(exc).__name__})", False, str(exc))

    failed = [name for name, passed, _ in checks if not passed]
    print(f"SELFTEST checks={len(checks)} failed={len(failed)}")
    if failed or not checks:
        print("FEATURE_OK family=precision_ops op=osnap_master verdict=FAIL")
        return 1
    print("FEATURE_OK family=precision_ops op=osnap_master verdict=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 precision_ops__w9.py:60 "开" -> "on"
# 原 precision_ops__w9.py:60 "打开" -> "enabled"
# 原 precision_ops__w9.py:61 "关" -> "off"
# 原 precision_ops__w9.py:61 "关闭" -> "disabled"
# 原 precision_ops__w9.py:62 "切换" -> "toggle"
# 原 precision_ops__w9.py:308 "屏蔽来自总开关，不是来自 kind 启停" -> "suppressed by the master switch, not by a per-kind on/off"
