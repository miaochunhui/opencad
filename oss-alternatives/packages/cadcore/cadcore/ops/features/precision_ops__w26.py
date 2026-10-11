#!/usr/bin/env python3
"""cadcore.ops.features.precision_ops__w26 —— 精度族第 17 号条目「极轴捕捉（PolarSnap）」的**距离步长**合成语义。

本片负责哪一条
--------------
- 清单：`docs/research/cad-parity/features/autocad-precision.json` 的 `must_have`（21 条）。
- 仍 missing/partial 的条目按清单顺序编号 0..20 —— 本族 21 条**全部**仍是 missing/partial
  （status: 0 implemented / 16 partial / 5 missing），故第 **17** 号 = `极轴捕捉（PolarSnap）`。
- 该条现状（partial）的缺口原文：「无捕捉步长（极轴捕捉的距离量化）与状态栏 F10 开关、
  apps/opencad/web 无调用点」。**本片只补第一项：捕捉步长（距离量化）**；F10/状态栏与 web 取点接线
  **不在本片**，故条目整体仍记 partial —— 本片 verdict=PASS 只表示"库级 PolarSnap 合成语义已落地并通过硬断言"，
  **不表示该条目已 implemented**（宁可红不许假绿）。

实现的语义（与 spec/precision_ops.json 第 17 项一致）
--------------------------------------------------
1. **方向**：由 `cadcore.precision.osnap.polar_track` 定（复用，不抄第二份几何）。方位角与最近极轴增量
   的夹角 `> tolerance_deg` ⇒ **方向未吸住**，点原样返回，且**不做步进**（spec: 不是错误）。
2. **距离**：方向吸住后，把 `base → point` 的半径量化到 `step_nm` 的整数倍，量化口径**复用**
   `cadcore.precision.osnap` 的 `_snap_scalar`（整数 `divmod`、半格向外远离零）—— 即 spec 写的
   "距离由 grid_snap 的口径沿射线步进"。
3. **落点** = `base + 步进后距离 × 单位方向向量`，坐标按 `_round_half_away` 落整纳米。
4. 方向命中但点本来就在格点上 ⇒ 仍在"生效"，但 `moved=False`（与"没吸住"可区分）。

冻结接口
--------
- ``FAMILY = "precision_ops"``；``OPERATIONS: dict[str, Callable]``；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``，返回**受影响记录的 uuid 列表**；
- 长度一律整数纳米（int）；错误一律 ``cadcore.errors.CadError(reason=...)``。

落点（唯一机读真源，别在调用点手搓）
----------------------------------
设置与结果**不改 L1 实体/草图字段**，只写一个额外容器（与 `cadcore/ops/precision_ops.py` 同族的
"容器 + 往返"做法，但**用本片自己的键** `precision_polar_snap`）：

- 目标是**文档**（`cadcore.docmodel.Document`）⇒ 表在 ``doc.extra["precision_polar_snap"]``；
  `extra` 是文档层明写的"未知顶层字段可往返"容器，`document_to_l1` → `document_from_l1` 一圈后原样还在
  （自测有一条断言专门验它）。
- 目标是**草图**（`{entities, constraints, dimensions}` 或同形映射）⇒ 表在 ``sk["precision_polar_snap"]``。
- 表结构（**只加键不改键**）：``{"schema": 1, "step": {...}, "results": [...]}``：
  - ``step``：``{"uuid","kind","step_nm","enabled"}``，重复设置**沿用同一个 uuid**（稳定 uuid）；
  - ``results``：逐次 `polar_snap` 的结果记录，**按时间追加**，只保留最近 `_MAX_RESULTS` 条；
    数值结果**只有这一处**（读者取 ``results[-1]``），本文件不写第二份镜像。

为什么用本片自己的容器键（而不是塞进 ``extra["precision"]``）
----------------------------------------------------------
`extra["precision"]` 的表结构由同族模块 `cadcore/ops/precision_ops.py` 的 `_ensure` 归一化
（modes/snap_kinds 记录）。往里加不认识的子键有被它归一化时丢掉的风险 —— 那会变成"设置写进去、存盘后
消失"的静默退化。本片因此用一个**独立的顶层键**：既不与同族模块抢同一个表的解释权，也不抄它的容器逻辑
（本文件自己解析 holder，见 `_holder`）。

口径（与全仓一致）
----------------
- ``0 = 真为零``：`shift_nm = 0` 表示结果点与输入点**确实**重合；`distance_nm = 0` 表示确实落在基点上。
- ``NA = 不适用``：不适用时**该键缺席**（方向未吸住 ⇒ 没有 `distance_nm`/`steps`/`increment_deg`；
  基点与输入点重合 ⇒ 连 `angle_udeg` 都不出现），不写 `None`、不写 0 冒充。
- 「未采」：`polar_snap_readout` 返回 `[]` = **还没算过**（不是"算出 0"）；设置未写 ⇒ 抛
  `CadError(reason='settings_missing')`，不是静默用 0 步长。

有意限定的边界（写下来，防止下游靠猜）
------------------------------------
1. 本层不做文件 I/O；持久化 = 写容器，存盘交给 io 层。
2. 入参错必须炸：`step_nm <= 0` / 非整数 / 增量角为空 ⇒ `CadError(reason='bad_arg')`；
   `osnap` 对坏入参抛 `ValueError` ⇒ 这里**捕获并转成 CadError**，不让 `ValueError` 逃逸。
3. `polar_snap_readout` 是纯查询（不写、不改）。
4. 非 docstring 的字符串字面量一律 ASCII：面向用户的文案由语言层按 key 取词。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/precision_ops__w26.py 时的引导（见文件末 __main__）
    import os as _os
    import sys as _sys

    _sys.path.insert(
        0,
        _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        ),
    )
    __package__ = "cadcore.ops.features"

import math
from collections.abc import Iterable as _IterableABC
from collections.abc import MutableMapping as _MutableMappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.precision.osnap import (
    _ang_diff,
    _angle_deg,
    _polar_angles,
    _round_half_away,
    _snap_scalar,
    polar_track,
)
from cadcore.sketch.constraints import deg_to_udeg, new_uuid

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "polar_snap",
    "polar_snap_readout",
    "polar_snap_step_set",
]

#: 族 id（冻结，= spec/precision_ops.json 的 FAMILY 值）。
FAMILY = "precision_ops"

#: 容器键：本片自己的顶层键（理由见模块 docstring）。
_STORE_KEY = "precision_polar_snap"
#: 容器 schema 版本（只加不改；将来迁移从这里分叉）。
_SCHEMA = 1
#: 缺省极轴增量角（与 osnap.polar_track 的缺省一致）。
_DEFAULT_INCREMENTS: tuple[float, ...] = (15.0, 30.0, 45.0, 90.0)
#: 缺省角度容差（度）。
_DEFAULT_TOLERANCE_DEG = 3.0
#: `results` 只保留最近这么多条（step 记录不裁）。
_MAX_RESULTS = 32


# --------------------------------------------------------------------------------------
# 入参把关（本片自己的校验；几何一律复用 osnap）
# --------------------------------------------------------------------------------------


def _bad_arg(detail: str, **details: Any) -> CadError:
    return CadError(detail, reason="bad_arg", **details)


def _int_nm(value: Any, *, what: str) -> int:
    """长度必须是**真整数**纳米（bool 不算；缺省不用 0 冒充）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad_arg("length must be an integer number of nanometres", what=what, value=repr(value))
    return int(value)


def _positive_nm(value: Any, *, what: str) -> int:
    number = _int_nm(value, what=what)
    if number <= 0:
        raise _bad_arg("length must be > 0", what=what, value=number)
    return number


def _point(value: Any, *, what: str) -> tuple[int, int]:
    if value is None:
        raise _bad_arg("point is required", what=what)
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _bad_arg("point must be a 2-sequence of integer nanometres", what=what, value=repr(value))
    return (_int_nm(value[0], what=f"{what}[0]"), _int_nm(value[1], what=f"{what}[1]"))


def _increments(value: Any) -> tuple[float, ...] | None:
    """None ⇒ 用缺省；坏值交给 osnap 的口径判（`_polar_angles` 抛 ValueError ⇒ 转 CadError）。"""
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, _IterableABC):
        raise _bad_arg("increments_deg must be an iterable of angles", value=repr(value))
    items = tuple(value)
    if not items:
        raise _bad_arg("increments_deg must contain at least one angle", value=repr(value))
    return items


def _tolerance(value: Any) -> float:
    if value is None:
        return _DEFAULT_TOLERANCE_DEG
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad_arg("tolerance_deg must be a number", value=repr(value))
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 90.0:
        raise _bad_arg("tolerance_deg must be finite and within [0, 90]", value=number)
    return number


def _enabled(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _bad_arg("enabled must be a bool", value=repr(value))
    return bool(value)


# --------------------------------------------------------------------------------------
# 容器（holder / load / save）—— 只解析，不抄同族模块的表逻辑
# --------------------------------------------------------------------------------------


def _holder(target: Any) -> _MutableMappingABC:
    """取承载容器：`Document.extra`（或任何带 `extra` 映射的对象）/ 草图映射本身。"""
    extra = getattr(target, "extra", None)
    if isinstance(extra, _MutableMappingABC):
        return extra
    if isinstance(target, _MutableMappingABC):
        return target
    raise _bad_arg("target must expose a mutable 'extra' mapping or be a mutable mapping",
                   what="target", value=repr(type(target).__name__))


def _load(target: Any) -> dict[str, Any]:
    """读容器（缺省给一张空表；表在则**就地**返回，改完由 `_save` 写回）。"""
    holder = _holder(target)
    raw = holder.get(_STORE_KEY)
    if raw is None:
        return {"schema": _SCHEMA, "results": []}
    if not isinstance(raw, _MutableMappingABC):
        raise _bad_arg("precision_polar_snap container must be a mapping",
                       what=_STORE_KEY, value=repr(type(raw).__name__))
    return raw  # type: ignore[return-value]


def _save(target: Any, store: dict[str, Any]) -> None:
    _holder(target)[_STORE_KEY] = store


def _results(store: Any) -> list[Any]:
    raw = store.get("results") if isinstance(store, _MutableMappingABC) else None
    return raw if isinstance(raw, list) else []


def _last(store: Any) -> dict[str, Any] | None:
    items = _results(store)
    return items[-1] if items and isinstance(items[-1], _MutableMappingABC) else None


def _step_record(store: Any) -> dict[str, Any] | None:
    raw = store.get("step") if isinstance(store, _MutableMappingABC) else None
    return raw if isinstance(raw, _MutableMappingABC) else None


def _resolve_step(store: Any, step_nm: Any) -> int:
    """显式 `step_nm` 优先；否则读容器里的步长记录。未设置 ⇒ **炸**（不许静默用 0 步长）。"""
    if step_nm is not None:
        return _positive_nm(step_nm, what="step_nm")
    record = _step_record(store)
    if record is None:
        raise CadError("polar snap step is not configured", reason="settings_missing",
                       what="polar_snap.step_nm", hint="polar_snap_step_set")
    if record.get("enabled") is not True:
        raise CadError("polar snap step is disabled", reason="settings_missing",
                       what="polar_snap.enabled", hint="polar_snap_step_set")
    value = record.get("step_nm")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CadError("stored polar snap step is not a positive integer", reason="settings_missing",
                       what="polar_snap.step_nm", value=repr(value))
    return int(value)


# --------------------------------------------------------------------------------------
# 操作
# --------------------------------------------------------------------------------------


def polar_snap_step_set(target: Any, *, step_nm: Any, enabled: Any = True) -> list[str]:
    """设置 PolarSnap 的**捕捉步长**（距离量化步长）；返回 ``[步长记录 uuid]``。

    重复调用**沿用同一个 uuid**（稳定 uuid：下游引用不会因为改设置而失效）。`step_nm <= 0` 或非整数 ⇒
    `CadError(reason='bad_arg')`（不允许 0/负步长，也不允许用"关掉"表示未设置）。
    """
    number = _positive_nm(step_nm, what="step_nm")
    flag = _enabled(enabled)
    store = _load(target)
    record = _step_record(store)
    if record is None:
        record = {"uuid": new_uuid(), "kind": "polar_snap_step", "step_nm": number, "enabled": flag}
    else:
        record["kind"] = "polar_snap_step"
        record["step_nm"] = number
        record["enabled"] = flag
        if not isinstance(record.get("uuid"), str) or not record["uuid"]:
            record["uuid"] = new_uuid()
    store["schema"] = _SCHEMA
    store["step"] = record
    if not isinstance(store.get("results"), list):
        store["results"] = []
    _save(target, store)
    return [str(record["uuid"])]


def polar_snap(target: Any, *, point_nm: Any = None, base_nm: Any = None,
               increments_deg: Any = None, tolerance_deg: Any = None,
               step_nm: Any = None) -> list[str]:
    """把光标点吸到"极轴方向 + 步长"的格点上；返回 ``[结果记录 uuid]``。

    语义（顺序写死）：
    1. 方向：`osnap.polar_track(base, point, increments_deg, tolerance_deg)`；方位角与最近极轴增量的
       夹角超容差 ⇒ **未吸住**，点原样返回、**不步进**（不是错误）。
    2. 距离：方向吸住后，半径量化到 `step_nm` 的整数倍（口径 = `osnap._snap_scalar`，整数 divmod、半格外向）。
    3. 落点 = `base + 步进距离 × 单位方向向量`，坐标 round-half-away 落整纳米。

    被记录并返回的字段（唯一真源 = ``extra["precision_polar_snap"]["results"][-1]``）：
    ``uuid / kind / base_nm / input_nm / point_nm / applied / direction_hit / moved / step_nm /
    shift_nm``；方向吸住时另有 ``increment_deg / radius_nm / distance_nm / steps``；基点与输入点重合时
    ``angle_udeg`` 整个缺席（NA，不写 0 冒充）。`applied=False` ⇒ 未吸住，且上列步进键**全部缺席**。

    `step_nm` 未显式给 ⇒ 读 `polar_snap_step_set` 写下的步长；没配过 ⇒
    `CadError(reason='settings_missing')`（不是静默 0 步长）。增量角为空 / 坏值 ⇒
    `CadError(reason='bad_arg')`（`osnap` 抛的 `ValueError` 在这里被转成 CadError，不逃逸）。
    """
    base = _point(base_nm, what="base_nm")
    point = _point(point_nm, what="point_nm")
    increments_arg = _increments(increments_deg)
    tolerance = _tolerance(tolerance_deg)
    store = _load(target)
    step = _resolve_step(store, step_nm)

    try:
        candidates = _polar_angles(_DEFAULT_INCREMENTS if increments_arg is None else increments_arg)
        increments = _DEFAULT_INCREMENTS if increments_arg is None else increments_arg
        direction_point = polar_track(base, point, increments_deg=increments, tolerance_deg=tolerance)
    except ValueError as exc:  # osnap 对坏入参抛 ValueError ⇒ 本层必须转成 CadError
        raise _bad_arg("increments_deg/tolerance_deg rejected by osnap", what="increments_deg",
                       detail=str(exc)) from exc

    angle = _angle_deg(base, point)  # 与基点重合 ⇒ None（方向未定义）
    direction_hit = False
    increment_deg: float | None = None
    if angle is not None:
        best = min(candidates, key=lambda deg: (_ang_diff(angle, deg), deg))
        if _ang_diff(angle, best) <= tolerance:
            direction_hit = True
            increment_deg = float(best)

    radius_nm = _round_half_away(math.hypot(point[0] - base[0], point[1] - base[1]))
    record: dict[str, Any] = {
        "uuid": new_uuid(),
        "kind": "polar_snap_result",
        "base_nm": [base[0], base[1]],
        "input_nm": [point[0], point[1]],
        "step_nm": int(step),
    }
    if direction_hit:
        assert increment_deg is not None  # direction_hit 只在 angle 非 None 时置位
        distance_nm = _snap_scalar(radius_nm, step)
        radians = math.radians(increment_deg)
        result = (
            base[0] + _round_half_away(distance_nm * math.cos(radians)),
            base[1] + _round_half_away(distance_nm * math.sin(radians)),
        )
        record.update({
            "direction_hit": True,
            "applied": True,
            "increment_deg": increment_deg,
            "direction_point_nm": [direction_point[0], direction_point[1]],
            "radius_nm": int(radius_nm),
            "distance_nm": int(distance_nm),
            "steps": int(distance_nm // step),
            "angle_udeg": int(deg_to_udeg(increment_deg)),
        })
    else:
        result = point
        record.update({"direction_hit": False, "applied": False})
        if angle is not None:
            record["angle_udeg"] = int(deg_to_udeg(angle))

    record["point_nm"] = [result[0], result[1]]
    record["moved"] = result != point
    record["shift_nm"] = _round_half_away(math.hypot(result[0] - point[0], result[1] - point[1]))

    items = _results(store)
    items.append(record)
    if len(items) > _MAX_RESULTS:
        del items[: len(items) - _MAX_RESULTS]
    store["results"] = items
    store["schema"] = _SCHEMA
    _save(target, store)
    return [str(record["uuid"])]


def polar_snap_readout(target: Any) -> list[str]:
    """纯查询：返回 ``[最近一次 polar_snap 结果记录的 uuid]``；**没算过 ⇒ ``[]``**（未采 ≠ 算出 0）。"""
    last = _last(_load(target))
    if last is None:
        return []
    uuid = last.get("uuid")
    return [str(uuid)] if isinstance(uuid, str) and uuid else []


#: 操作名 → 函数（冻结接口）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "polar_snap": polar_snap,
    "polar_snap_step_set": polar_snap_step_set,
    "polar_snap_readout": polar_snap_readout,
}


# --------------------------------------------------------------------------------------
# 自测（真几何硬断言；>= 2 条）
# --------------------------------------------------------------------------------------


if __name__ == "__main__":
    import sys as _sys
    import traceback as _traceback

    from cadcore.docmodel import document_from_l1, document_to_l1, new_document

    def _selftest() -> int:
        checks = 0
        doc = new_document()

        # A1：设置步长返回稳定 uuid
        first = polar_snap_step_set(doc, step_nm=5_000_000)
        assert len(first) == 1 and isinstance(first[0], str) and first[0]
        checks += 1

        # A2：方向 0.57 度 → 吸到 0 度；距离 10_000_499.99 → 最近 5mm 整数倍 = 10_000_000（spec 测试向量 1）
        out = polar_snap(doc, base_nm=(0, 0), point_nm=(10_000_000, 100_000))
        assert len(out) == 1 and out[0] != first[0]
        last = _load(doc)["results"][-1]
        assert last["applied"] is True and last["moved"] is True
        assert last["point_nm"] == [10_000_000, 0], last
        assert last["increment_deg"] == 0.0 and last["angle_udeg"] == 0
        assert last["radius_nm"] == 10_000_500 and last["distance_nm"] == 10_000_000
        assert last["steps"] == 2 and last["distance_nm"] % last["step_nm"] == 0
        checks += 1

        # A3：26.57 度 超 3 度容差 → 原样返回、**不步进**，且步进键整个缺席（NA 不写 0 冒充）（spec 测试向量 2）
        polar_snap(doc, base_nm=(0, 0), point_nm=(10_000_000, 5_000_000))
        last = _load(doc)["results"][-1]
        assert last["direction_hit"] is False and last["applied"] is False
        assert last["point_nm"] == [10_000_000, 5_000_000] and last["shift_nm"] == 0
        for absent in ("distance_nm", "steps", "increment_deg", "radius_nm"):
            assert absent not in last, (absent, last)
        checks += 1

        # A4：30 度方向 + 5mm 步长 —— 真几何落点（cos30/sin30 落整纳米）
        polar_snap(doc, base_nm=(0, 0), point_nm=(8_660_254, 5_000_000),
                   increments_deg=(30,), tolerance_deg=3.0)
        last = _load(doc)["results"][-1]
        assert last["increment_deg"] == 30.0 and last["distance_nm"] == 10_000_000
        assert last["point_nm"] == [8_660_254, 5_000_000], last
        checks += 1

        # A5：方向命中但点已在格点上 ⇒ applied=True 且 moved=False（与"没吸住"可区分）
        polar_snap(doc, base_nm=(0, 0), point_nm=(10_000_000, 0))
        last = _load(doc)["results"][-1]
        assert last["applied"] is True and last["moved"] is False
        assert last["point_nm"] == [10_000_000, 0] and last["shift_nm"] == 0
        checks += 1

        # A6：显式 step_nm 覆盖已存步长（12.6mm：5mm 步长 → 15mm；2.5mm 步长 → 12.5mm）
        polar_snap(doc, base_nm=(0, 0), point_nm=(12_600_000, 0), step_nm=5_000_000)
        over = _load(doc)["results"][-1]
        assert over["distance_nm"] == 15_000_000 and over["point_nm"] == [15_000_000, 0], over
        polar_snap(doc, base_nm=(0, 0), point_nm=(12_600_000, 0), step_nm=2_500_000)
        over = _load(doc)["results"][-1]
        assert over["distance_nm"] == 12_500_000 and over["point_nm"] == [12_500_000, 0], over
        checks += 1

        # A7：L1 往返 —— 设置与结果经 document_to_l1/document_from_l1 原样还在（真数据后果）
        text = document_to_l1(doc)
        doc2 = document_from_l1(text)
        store2 = _load(doc2)
        assert store2["step"]["uuid"] == first[0] and store2["step"]["step_nm"] == 5_000_000
        assert store2["step"]["enabled"] is True
        assert [r["point_nm"] for r in store2["results"]] == \
               [r["point_nm"] for r in _load(doc)["results"]]
        assert len(store2["results"]) == 6
        checks += 1

        # A8：重设步长沿用同一 uuid（稳定 uuid），旧结果记录不被动到
        again = polar_snap_step_set(doc2, step_nm=1_000_000)
        assert again == first, (again, first)
        store3 = _load(doc2)
        assert store3["step"]["step_nm"] == 1_000_000 and len(store3["results"]) == 6
        checks += 1

        # A9：读数面 —— 新文档"没算过" ⇒ []（未采 ≠ 算出 0）；算过 ⇒ 给出最近记录 uuid
        fresh = {"entities": [], "constraints": [], "dimensions": []}
        assert polar_snap_readout(fresh) == []
        assert polar_snap_readout(doc2) == [store3["results"][-1]["uuid"]]
        checks += 1

        # A10：草图容器同样可持久化（无 Document 时的路径）
        polar_snap_step_set(fresh, step_nm=2_000_000)
        assert fresh[_STORE_KEY]["step"]["step_nm"] == 2_000_000
        sketch_out = polar_snap(fresh, base_nm=(0, 0), point_nm=(3_100_000, 0))
        assert len(sketch_out) == 1 and fresh[_STORE_KEY]["results"][-1]["distance_nm"] == 4_000_000
        checks += 1

        # A11：错误必须炸（坏步长 / 未配置步长 / 空增量角），reason 机器可读
        for kwargs, reason in (
            ({"step_nm": 0}, "bad_arg"),
            ({"step_nm": 5_000_000, "increments_deg": []}, "bad_arg"),
        ):
            try:
                polar_snap(doc2, base_nm=(0, 0), point_nm=(1, 0), **kwargs)
            except CadError as exc:
                assert exc.reason == reason, (exc.reason, reason)
            else:
                raise AssertionError(("expected CadError", kwargs))
        try:
            polar_snap_step_set(new_document(), step_nm=-1)
        except CadError as exc:
            assert exc.reason == "bad_arg"
        else:
            raise AssertionError("expected CadError for negative step")
        try:
            polar_snap(new_document(), base_nm=(0, 0), point_nm=(10_000_000, 0))
        except CadError as exc:
            assert exc.reason == "settings_missing", exc.reason
        else:
            raise AssertionError("expected CadError for unset step")
        checks += 1

        # A12：冻结面不受污染（FAMILY / OPERATIONS 契约）
        assert FAMILY == "precision_ops" and set(OPERATIONS) == {
            "polar_snap", "polar_snap_step_set", "polar_snap_readout"}
        assert all(callable(fn) for fn in OPERATIONS.values())
        checks += 1
        return checks

    try:
        _n = _selftest()
    except BaseException:  # noqa: BLE001 —— 自测失败必须响亮退出，不许静默
        _traceback.print_exc()
        print("SELFTEST precision_ops__w26 op=polar_snap result=FAIL")
        print("FEATURE_OK family=precision_ops op=polar_snap verdict=FAIL")
        _sys.exit(1)
    print("NOTE family=precision_ops item_index=17 item=polar_snap "
          "distance_step=implemented ui_f10=missing_not_this_slice item_status=partial")
    print(f"SELFTEST precision_ops__w26 op=polar_snap assertions={_n} result=PASS")
    print("FEATURE_OK family=precision_ops op=polar_snap verdict=PASS")
