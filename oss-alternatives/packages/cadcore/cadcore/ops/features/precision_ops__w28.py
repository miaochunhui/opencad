#!/usr/bin/env python3
"""cadcore.ops.features.precision_ops__w28 —— 片席 w28：精度族第 19 号 must_have「坐标与直接距离输入」。

① 本族仍 missing/partial 的 must_have（真源
``docs/research/cad-parity/status/autocad-precision.json`` + ``features/autocad-precision.json``，
共 21 条 = 全部 must_have，``implemented=0 / partial=16 / missing=5``；按清单顺序 0..20 编号，
机读形态见本模块常量 ``REMAINING_MUST_HAVE``，中文名照抄清单）：

===== ==================================== =========
index must_have                            状态
===== ==================================== =========
0     对象捕捉总开关                        partial
1     端点捕捉                              partial
2     中点捕捉                              partial
3     圆心与象限点捕捉                      partial
4     交点与外观交点捕捉                    partial
5     垂足、切点、最近点捕捉                partial
6     延长线与平行捕捉                      missing
7     插入点与节点捕捉                      missing
8     单次临时捕捉（临时替代）              missing
9     捕捉设置：优先级与忽略图案填充对象    partial
10    AutoSnap 捕捉反馈（标记/磁吸/工具提示） missing
11    正交模式                              partial
12    极轴追踪开关与增量角                  partial
13    极轴附加角与角度测量基准              partial
14    对象捕捉追踪                          partial
15    栅格显示                              partial
16    栅格捕捉（步进对齐）                  partial
17    极轴捕捉（PolarSnap）                 partial
18    动态输入                              partial
19    坐标与直接距离输入                    partial   ← 本席负责（``ASSIGNED_INDEX = 19``）
20    状态栏开关与快捷键体系                missing
===== ==================================== =========

② 本席实现第 19 号「坐标与直接距离输入」（规格别名 ``@x,y / @长度<角度 / 直接距离输入``）。

真实现的缺口（status 原文点名）是：现有实现只有 ``apps/opencad/web/js/app.js`` 的
``parsePoint`` 解析**绝对** ``"x,y"``；**缺** ``@x,y`` 相对坐标、``@长度<角度`` 极坐标、
以及「先定方向再直接输距离」。本模块在 **L1 文档模型**上把这三件事做成真能力：

- 语法（长度按文档单位 ``unit`` 换算，落盘一律整数纳米）：
  ``x,y`` 绝对直角 / ``#x,y`` 显式绝对 / ``@dx,dy`` 相对上一点 /
  ``长度<角度`` 绝对极坐标（自原点量）/ ``@长度<角度`` 相对极坐标（自上一点量）/
  ``距离`` 直接距离输入（方向 = 基点→光标，允许被正交 / 极轴模式锁到轴线上）。
- 真数据后果：每次输入在文档容器的 ``extra["coord_input"]["inputs"]`` 里**追加一条记录**
  （``uuid / text / form / point_nm / length_nm / angle_udeg / locked_by`` 等），
  随 ``document_to_l1`` → ``document_from_l1`` 一圈原样往返（自检有断言）；
  返回 ``[输入记录 uuid, *本次生效的模式记录 uuid]``。
- 真几何：直接距离输入复用 ``cadcore.precision.osnap.ortho_lock`` / ``polar_track``，
  **只读**消费 ``extra["precision"]`` 里由 ``cadcore.ops.precision_ops`` 持久化的
  正交 / 极轴模式状态（正交优先于极轴，与 ORTHO 覆盖 POLAR 一致）；本模块不写别人的键。
- 入参错一律炸（``CadError(reason=...)``）：``invalid_input`` / ``missing_base`` /
  ``missing_direction`` / ``degenerate_direction`` / ``disabled`` / ``unknown_unit`` /
  ``out_of_range`` / ``bad_target`` / ``bad_store``；未知单位不猜、静默兜底一律不做。

已知欠账（不假装完成）：本条在状态表里**仍记 partial** —— 用户侧入口（``apps/opencad/web``
的状态栏 / 命令行接线）不在本席写面（本席只准写本文件），故本条只把**库级能力**补齐。

冻结接口：``FAMILY = "precision_ops"``；``OPERATIONS: dict[str, Callable]``；
每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``（返回受影响记录 uuid 列表，顺序在各函数
docstring 里写明）；长度整数纳米；错误抛 ``cadcore.errors.CadError``。
非 docstring 字符串字面量一律 ASCII（面向用户的文案走语言层）。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/precision_ops__w28.py 时的引导
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
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.precision.osnap import ortho_lock, polar_track
from cadcore.sketch.constraints import new_uuid

__all__ = [
    "ASSIGNED_INDEX",
    "ASSIGNED_SLUG",
    "FAMILY",
    "OPERATIONS",
    "REMAINING_MUST_HAVE",
    "coord_history",
    "coord_input_probe",
    "coord_input_set",
    "coordinate_input",
]

#: 族 id（冻结；与 spec/precision_ops.json 的 FAMILY 一致）。
FAMILY = "precision_ops"

#: 本席负责的 must_have 下标（0-based，按 features 清单顺序）与其 ASCII slug。
ASSIGNED_INDEX = 19
ASSIGNED_SLUG = "coordinate_direct_distance"

#: 该族仍 missing/partial 的全部 must_have：(index, ASCII slug, status)。中文名见模块 docstring。
REMAINING_MUST_HAVE: tuple[tuple[int, str, str], ...] = (
    (0, "osnap_toggle", "partial"),
    (1, "endpoint", "partial"),
    (2, "midpoint", "partial"),
    (3, "center_quadrant", "partial"),
    (4, "intersection_apparent", "partial"),
    (5, "perpendicular_tangent_nearest", "partial"),
    (6, "extension_parallel", "missing"),
    (7, "insert_node", "missing"),
    (8, "snap_override_once", "missing"),
    (9, "snap_settings_priority_hatch", "partial"),
    (10, "autosnap_feedback", "missing"),
    (11, "ortho", "partial"),
    (12, "polar_track", "partial"),
    (13, "polar_additional_angle_angbase", "partial"),
    (14, "otrack", "partial"),
    (15, "grid_display", "partial"),
    (16, "grid_snap", "partial"),
    (17, "polar_snap", "partial"),
    (18, "dynamic_input", "partial"),
    (19, "coordinate_direct_distance", "partial"),
    (20, "statusbar_shortcuts", "missing"),
)

#: 本模块在文档/草图容器里的键名（**独立于** `precision`，不碰别人的键）。
_STORE_KEY = "coord_input"
#: 只读消费的正交/极轴模式表（`cadcore.ops.precision_ops` 写的那个）。
_PRECISION_KEY = "precision"
_SCHEMA = 1
_MODE_NAME = "coord_input"
_MODE_NAMES = (_MODE_NAME,)
_UNSET: Any = object()
_DEFAULT_HISTORY_LIMIT = 32
_DEFAULT_TOLERANCE_DEG = 3.0
_DEFAULT_INCREMENTS_DEG = (90.0,)
_DOC_KEYS = ("schema_version", "meta", "sketches", "features", "annotations", "sheets")
#: 文档单位 → 纳米倍数（1 unit = N nm）。
_UNIT_NM = {
    "nm": 1.0,
    "um": 1_000.0,
    "mm": 1_000_000.0,
    "cm": 10_000_000.0,
    "dm": 100_000_000.0,
    "m": 1_000_000_000.0,
    "in": 25_400_000.0,
    "ft": 304_800_000.0,
}
_MAX_ABS_NM = float(2 ** 53)


# ======================================================================================
# 入参 / 容器小工具
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _bool(value: Any, what: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))


def _positive_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail("invalid_argument", f"{what} must be an int >= 1", what=what, value=repr(value))
    return int(value)


def _nm_int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be an integer number of nm", what=what)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise _fail("invalid_argument", f"{what} must be an integral number of nm",
                        what=what, value=repr(value))
        return int(value)
    raise _fail("invalid_argument", f"{what} must be an integer number of nm",
                what=what, value=repr(value))


def _point(value: Any, what: str) -> tuple[int, int]:
    """点入参 → ``(x_nm, y_nm)`` 整数对（形状坏 ⇒ 当场报错，不静默丢点）。"""
    if isinstance(value, (str, bytes)) or isinstance(value, _MappingABC):
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair",
                    what=what, value=repr(value))
    try:
        if len(value) != 2:
            raise TypeError("length")
        x, y = value[0], value[1]
    except Exception as exc:  # noqa: BLE001 - 形状坏统一转 CadError
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair",
                    what=what, value=repr(value)) from exc
    return (_nm_int(x, f"{what}.x"), _nm_int(y, f"{what}.y"))


def _number(text: Any, what: str) -> float:
    """用户输入的单个数字（拒绝空串 / NaN / 无穷 / 非数字，不做静默兜底）。"""
    raw = str(text).strip()
    if not raw:
        raise _fail("invalid_input", f"{what} is empty", what=what)
    try:
        num = float(raw)
    except (TypeError, ValueError) as exc:
        raise _fail("invalid_input", f"{what} is not a number", what=what, value=raw) from exc
    if not math.isfinite(num):
        raise _fail("invalid_input", f"{what} must be finite", what=what, value=raw)
    return num


def _unit_of(target: Any) -> str:
    unit = target.get("unit") if isinstance(target, _MappingABC) else getattr(target, "unit", None)
    if unit is None:
        return "mm"
    name = str(unit)
    if name not in _UNIT_NM:
        raise _fail("unknown_unit", "document unit is not supported by length parsing",
                    unit=name, supported=sorted(_UNIT_NM))
    return name


def _to_nm(value_mm: float, unit: str, what: str) -> int:
    factor = _UNIT_NM[unit]
    nm = float(value_mm) * factor
    if not math.isfinite(nm) or abs(nm) > _MAX_ABS_NM:
        raise _fail("out_of_range", f"{what} is out of range for nm storage",
                    what=what, value=float(value_mm), unit=unit)
    return int(round(nm))


def _holder(target: Any, *, create: bool) -> Any:
    """文档 ⇒ ``extra`` 容器；映射草图 ⇒ 草图自身；其它 ⇒ ``None``（读路径）/ 报错（写路径）。"""
    if isinstance(target, _MappingABC):
        extra = target.get("extra")
        if isinstance(extra, _MutableMappingABC):
            return extra
        doc_like = any(key in target for key in _DOC_KEYS)
        if not doc_like and isinstance(target, _MutableMappingABC):
            return target
        if isinstance(target, _MutableMappingABC) and create:
            extra = {}
            target["extra"] = extra
            return extra
        return None
    extra = getattr(target, "extra", None)
    if isinstance(extra, _MutableMappingABC):
        return extra
    return None


def _store_of(target: Any, *, create: bool) -> Any:
    holder = _holder(target, create=create)
    if holder is None:
        if create:
            raise _fail("bad_target", "target has no mutable container for coordinate input state")
        return None
    raw = holder.get(_STORE_KEY)
    if raw is None:
        if not create:
            return None
        raw = {"schema": _SCHEMA, "modes": [], "inputs": []}
        holder[_STORE_KEY] = raw
    if not isinstance(raw, _MutableMappingABC):
        raise _fail("bad_store", "coordinate input state is malformed",
                    key=_STORE_KEY, type=type(raw).__name__)
    raw.setdefault("schema", _SCHEMA)
    if not isinstance(raw.get("modes"), list) or not isinstance(raw.get("inputs"), list):
        raise _fail("bad_store", "coordinate input state lost its modes/inputs lists", key=_STORE_KEY)
    return raw


def _default_config(name: str) -> dict[str, Any]:
    if name == _MODE_NAME:
        return {"history_limit": int(_DEFAULT_HISTORY_LIMIT),
                "direct_distance": True,
                "relative_forms": True}
    raise _fail("unknown_mode", "unknown coordinate input mode", mode=name)


def _mode(store: Any, name: str, *, create: bool = True) -> Any:
    for record in store["modes"]:
        if isinstance(record, _MappingABC) and record.get("name") == name:
            if not isinstance(record.get("config"), _MutableMappingABC):
                raise _fail("bad_store", "mode record has no mutable config", mode=name)
            return record
    if not create:
        return None
    record = {"uuid": str(new_uuid()), "name": name, "enabled": True,
              "config": _default_config(name)}
    store["modes"].append(record)
    return record


def _history_limit(mode: Any) -> int:
    return _positive_int(mode["config"].get("history_limit", _DEFAULT_HISTORY_LIMIT),
                         "history_limit")


def _trim(store: Any, mode: Any) -> None:
    limit = _history_limit(mode)
    inputs = store["inputs"]
    if len(inputs) > limit:
        del inputs[: len(inputs) - limit]


def _record_by_uuid(store: Any, uuid_value: str) -> Any:
    for record in store["inputs"]:
        if isinstance(record, _MappingABC) and record.get("uuid") == uuid_value:
            return record
    return None


def _precision_modes(target: Any) -> dict[str, Any]:
    """**只读**取 ``extra["precision"]`` 里正交/极轴模式记录（缺席 ⇒ 空表，不猜缺省）。"""
    holder = _holder(target, create=False)
    if holder is None:
        return {}
    store = holder.get(_PRECISION_KEY)
    if not isinstance(store, _MappingABC):
        return {}
    out: dict[str, Any] = {}
    for record in store.get("modes") or ():
        if isinstance(record, _MappingABC) and isinstance(record.get("name"), str):
            out[record["name"]] = record
    return out


def _config_of(record: Any, key: str, default: Any) -> Any:
    config = record.get("config") if isinstance(record, _MappingABC) else None
    if isinstance(config, _MappingABC) and key in config:
        return config[key]
    return default


# ======================================================================================
# 解析：文本 → 整数纳米点
# ======================================================================================


def _norm_udeg(deg: float) -> int:
    return int(round(deg * 1_000_000.0)) % 360_000_000


def _lock_direction(base: tuple[int, int], cursor: tuple[int, int],
                    precision: dict[str, Any]) -> tuple[tuple[int, int], Any, list[str]]:
    """直接距离输入的方向：正交优先于极轴；未锁住 ⇒ 原方向（`locked_by=None`）。"""
    ortho = precision.get("ortho")
    polar = precision.get("polar")
    if isinstance(ortho, _MappingABC) and bool(ortho.get("enabled")):
        tolerance = float(_config_of(ortho, "tolerance_deg", _DEFAULT_TOLERANCE_DEG))
        try:
            candidate = ortho_lock(base, cursor, tolerance_deg=tolerance)
        except ValueError as exc:
            raise _fail("invalid_argument", str(exc), mode="ortho") from exc
        candidate = (int(candidate[0]), int(candidate[1]))
        if candidate != cursor:
            return candidate, "ortho", [str(ortho["uuid"])]
        return cursor, None, []
    if isinstance(polar, _MappingABC) and bool(polar.get("enabled")):
        increments = _config_of(polar, "increments_deg", _DEFAULT_INCREMENTS_DEG)
        tolerance = float(_config_of(polar, "tolerance_deg", _DEFAULT_TOLERANCE_DEG))
        try:
            candidate = polar_track(base, cursor,
                                    increments_deg=tuple(float(v) for v in increments),
                                    tolerance_deg=tolerance)
        except ValueError as exc:
            raise _fail("invalid_argument", str(exc), mode="polar") from exc
        candidate = (int(candidate[0]), int(candidate[1]))
        if candidate != cursor:
            return candidate, "polar", [str(polar["uuid"])]
    return cursor, None, []


def _parse(text: Any, *, unit: str, base: Any, cursor: Any,
           mode: Any, precision: dict[str, Any]) -> dict[str, Any]:
    raw = "" if text is None else str(text).strip()
    if not raw:
        raise _fail("invalid_input", "coordinate input text is empty", text=raw)
    absolute = True
    if raw[0] in "#@":
        absolute = raw[0] == "#"
        raw = raw[1:].strip()
        if not raw:
            raise _fail("invalid_input", "coordinate input has a prefix but no payload",
                        text=str(text))
        if not absolute and not bool(mode["config"].get("relative_forms", True)):
            raise _fail("disabled", "relative coordinate input is disabled by settings",
                        form="relative")
    config = mode["config"]

    if "," in raw:
        parts = [item.strip() for item in raw.split(",")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise _fail("invalid_input", "cartesian input must be exactly x,y",
                        text=raw, form="cartesian")
        dx = _to_nm(_number(parts[0], "x"), unit, "x")
        dy = _to_nm(_number(parts[1], "y"), unit, "y")
        if absolute:
            point = (dx, dy)
            form = "absolute_cartesian"
        else:
            if base is None:
                raise _fail("missing_base", "relative coordinate input needs a base point",
                            form="relative_cartesian")
            point = (base[0] + dx, base[1] + dy)
            form = "relative_cartesian"
    elif "<" in raw:
        left, _, right = raw.partition("<")
        length = _number(left, "length")
        angle_deg = _number(right, "angle")
        if length < 0:
            raise _fail("invalid_input", "polar length must be >= 0", text=raw, form="polar")
        if abs(angle_deg) > 1_000_000.0:
            raise _fail("out_of_range", "polar angle is out of range", text=raw)
        length_nm = _to_nm(length, unit, "length")
        radians = math.radians(angle_deg)
        if absolute:
            origin = (0, 0)
            form = "absolute_polar"
        else:
            if base is None:
                raise _fail("missing_base", "relative polar input needs a base point",
                            form="relative_polar")
            origin = base
            form = "relative_polar"
        point = (origin[0] + int(round(length_nm * math.cos(radians))),
                 origin[1] + int(round(length_nm * math.sin(radians))))
    else:
        if not bool(config.get("direct_distance", True)):
            raise _fail("disabled", "direct distance entry is disabled by settings",
                        form="direct_distance")
        distance = _number(raw, "distance")
        if distance <= 0:
            raise _fail("invalid_input", "direct distance must be > 0",
                        text=raw, form="direct_distance")
        if base is None:
            raise _fail("missing_base", "direct distance entry needs the previous point",
                        form="direct_distance")
        if cursor is None:
            raise _fail("missing_direction", "direct distance entry needs a cursor direction",
                        form="direct_distance")
        if tuple(cursor) == tuple(base):
            raise _fail("degenerate_direction", "cursor coincides with the base point",
                        form="direct_distance")
        length_nm = _to_nm(distance, unit, "distance")
        if length_nm <= 0:
            raise _fail("invalid_input", "direct distance rounds to 0 nm",
                        text=raw, form="direct_distance")
        locked, locked_by, mode_uuids = _lock_direction(base, cursor, precision)
        deltas = (locked[0] - base[0], locked[1] - base[1])
        norm = math.hypot(float(deltas[0]), float(deltas[1]))
        if norm <= 0.0:
            raise _fail("degenerate_direction", "locked direction collapsed to zero length",
                        form="direct_distance")
        point = (base[0] + int(round(deltas[0] / norm * length_nm)),
                 base[1] + int(round(deltas[1] / norm * length_nm)))
        form = "direct_distance"
        reference = base
        return {"point": point, "form": form, "length_nm": int(length_nm),
                "angle_udeg": _norm_udeg(math.degrees(math.atan2(deltas[1], deltas[0]))),
                "locked_by": locked_by, "mode_uuids": mode_uuids}

    reference = base if base is not None else (0, 0)
    dx = point[0] - reference[0]
    dy = point[1] - reference[1]
    return {"point": point, "form": form, "length_nm": int(round(math.hypot(dx, dy))),
            "angle_udeg": _norm_udeg(math.degrees(math.atan2(dy, dx))) if (dx or dy) else 0,
            "locked_by": None, "mode_uuids": []}


# ======================================================================================
# 操作（OPERATIONS）
# ======================================================================================


def coordinate_input(target: Any, *, text: Any, base_nm: Any = None,
                     cursor_nm: Any = None) -> list[str]:
    """解析一条坐标 / 直接距离输入，把结果**真写进**文档的坐标输入记录表。

    支持 ``x,y``（绝对直角）、``#x,y``（显式绝对）、``@dx,dy``（相对上一点）、
    ``长度<角度``（绝对极坐标，自原点量）、``@长度<角度``（相对极坐标）、
    ``距离``（直接距离输入：方向 = ``base_nm`` → ``cursor_nm``，正交 / 极轴模式开启时先锁方向）。

    返回 ``[本条输入记录 uuid, *本次生效的模式记录 uuid]``；``locked_by`` 写进记录里
    （``None`` / ``"ortho"`` / ``"polar"``），未锁住时**不**附模式 uuid（不假装生效）。
    超出 ``history_limit`` 时只丢**最旧**的记录（保留最新，返回值仍在表里）。
    错误：``invalid_input`` / ``missing_base`` / ``missing_direction`` / ``degenerate_direction``
    / ``disabled`` / ``unknown_unit`` / ``out_of_range`` / ``bad_target`` / ``bad_store``。
    """
    store = _store_of(target, create=True)
    mode = _mode(store, _MODE_NAME, create=True)
    if not bool(mode.get("enabled", False)):
        raise _fail("disabled", "coordinate input is disabled by settings",
                    mode_uuid=str(mode["uuid"]))
    unit = _unit_of(target)
    base = None if base_nm is None else _point(base_nm, "base_nm")
    cursor = None if cursor_nm is None else _point(cursor_nm, "cursor_nm")
    parsed = _parse(text, unit=unit, base=base, cursor=cursor, mode=mode,
                    precision=_precision_modes(target))
    record = {
        "uuid": str(new_uuid()),
        "text": str(text).strip(),
        "form": parsed["form"],
        "unit": unit,
        "base_nm": None if base is None else [base[0], base[1]],
        "cursor_nm": None if cursor is None else [cursor[0], cursor[1]],
        "point_nm": [parsed["point"][0], parsed["point"][1]],
        "length_nm": int(parsed["length_nm"]),
        "angle_udeg": int(parsed["angle_udeg"]),
        "locked_by": parsed["locked_by"],
        "index": len(store["inputs"]),
    }
    store["inputs"].append(record)
    _trim(store, mode)
    return [record["uuid"], *parsed["mode_uuids"]]


def coord_input_probe(target: Any, *, text: Any, base_nm: Any = None,
                      cursor_nm: Any = None) -> list[str]:
    """**纯查询（dry run）**：解析同样的输入但**不落记录**（表里一个字节都不动）。

    返回``[coord_input 模式记录 uuid, *本次会生效的模式记录 uuid]``；
    坐标输入模式关掉、或表还没建出来 ⇒ ``[]``（"这条输入会被忽略"，不是"当作没关"）。
    非法输入照样抛错（不把 bad input 当"没命中"）。
    """
    store = _store_of(target, create=False)
    if store is None:
        return []
    mode = _mode(store, _MODE_NAME, create=False)
    if mode is None or not bool(mode.get("enabled", False)):
        return []
    unit = _unit_of(target)
    base = None if base_nm is None else _point(base_nm, "base_nm")
    cursor = None if cursor_nm is None else _point(cursor_nm, "cursor_nm")
    parsed = _parse(text, unit=unit, base=base, cursor=cursor, mode=mode,
                    precision=_precision_modes(target))
    return [str(mode["uuid"]), *parsed["mode_uuids"]]


def coord_input_set(target: Any, *, enabled: Any = _UNSET, history_limit: Any = _UNSET,
                    direct_distance: Any = _UNSET, relative_forms: Any = _UNSET) -> list[str]:
    """设置坐标输入模式（缺省参数不动）。返回 ``[coord_input 模式记录 uuid]``。

    - ``enabled``：总开关；关掉后 ``coordinate_input`` 抛 ``disabled``、``coord_input_probe`` 返回 ``[]``。
    - ``history_limit``：记录表上限（>= 1）；调小时立刻裁掉最旧记录。
    - ``direct_distance`` / ``relative_forms``：分别允许"裸数字直输距离"与 ``@`` 相对形式。
    """
    store = _store_of(target, create=True)
    mode = _mode(store, _MODE_NAME, create=True)
    if enabled is not _UNSET:
        mode["enabled"] = _bool(enabled, "enabled")
    if history_limit is not _UNSET:
        mode["config"]["history_limit"] = _positive_int(history_limit, "history_limit")
    if direct_distance is not _UNSET:
        mode["config"]["direct_distance"] = _bool(direct_distance, "direct_distance")
    if relative_forms is not _UNSET:
        mode["config"]["relative_forms"] = _bool(relative_forms, "relative_forms")
    _trim(store, mode)
    return [str(mode["uuid"])]


def coord_history(target: Any, *, limit: int = -1) -> list[str]:
    """读回坐标输入记录 uuid，**最新在前**。``limit < 0`` = 全部，``limit = 0`` = 空。

    表不存在 ⇒ ``[]``：本模块是该表的**唯一写者**，所以这里的空是**真为零**（不是"未采"）。
    """
    store = _store_of(target, create=False)
    if store is None:
        return []
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _fail("invalid_argument", "limit must be an int", limit=repr(limit))
    records = [record for record in store["inputs"]
               if isinstance(record, _MappingABC) and record.get("uuid")]
    if limit == 0:
        return []
    if limit > 0:
        records = records[-limit:]
    return [str(record["uuid"]) for record in reversed(records)]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "coordinate_input": coordinate_input,
    "coord_input_probe": coord_input_probe,
    "coord_input_set": coord_input_set,
    "coord_history": coord_history,
}


# ======================================================================================
# 自测（硬断言：真几何数值 + 真数据后果 + 往返 + 报错 reason）
# ======================================================================================


def selftest() -> int:
    from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1, new_document
    from ..precision_ops import ortho_set

    results: list[tuple[str, bool, str]] = []

    def check(name: str, passed: Any, detail: Any = "") -> None:
        results.append((name, bool(passed), str(detail)))

    # --- 设置与真数据后果 -------------------------------------------------------------
    doc = new_document()
    assert isinstance(OPERATIONS["coordinate_input"], Callable)
    returned = coord_input_set(doc, history_limit=8)
    store = doc.extra[_STORE_KEY]
    check("set_returns_mode_uuid", len(returned) == 1 and returned[0] == store["modes"][0]["uuid"],
          returned)
    check("history_limit_persisted", store["modes"][0]["config"]["history_limit"] == 8,
          store["modes"][0]["config"])

    base = (1_000_000, 2_000_000)  # 1.0mm, 2.0mm
    u_rel = coordinate_input(doc, text="@10.5,-2", base_nm=base, cursor_nm=(0, 0))
    rec_rel = _record_by_uuid(store, u_rel[0])
    check("rel_cartesian_exact_point", rec_rel is not None and rec_rel["point_nm"] == [11_500_000, 0],
          None if rec_rel is None else rec_rel["point_nm"])
    check("rel_cartesian_no_lock_uuid", u_rel == [rec_rel["uuid"]], u_rel)

    u_pol = coordinate_input(doc, text="@5<90", base_nm=base)
    check("rel_polar_exact_point",
          _record_by_uuid(store, u_pol[0])["point_nm"] == [1_000_000, 7_000_000],
          _record_by_uuid(store, u_pol[0])["point_nm"])

    u_abs = coordinate_input(doc, text="3,4")
    rec_abs = _record_by_uuid(store, u_abs[0])
    check("abs_cartesian_exact_point", rec_abs["point_nm"] == [3_000_000, 4_000_000],
          rec_abs["point_nm"])
    check("abs_cartesian_length_angle", rec_abs["length_nm"] == 5_000_000
          and rec_abs["angle_udeg"] == 53_130_102, (rec_abs["length_nm"], rec_abs["angle_udeg"]))

    # --- 直接距离输入 + 正交锁（真调用 precision_ops 建状态，只读消费） ----------------
    ortho_uuid = ortho_set(doc, enabled=True)[0]
    ortho_record = None
    for item in doc.extra[_PRECISION_KEY]["modes"]:
        if item.get("name") == "ortho":
            ortho_record = item
    ortho_record["config"]["tolerance_deg"] = _DEFAULT_TOLERANCE_DEG
    u_dde = coordinate_input(doc, text="25", base_nm=(0, 0), cursor_nm=(9_000_000, 200_000))
    rec_dde = _record_by_uuid(store, u_dde[0])
    check("dde_ortho_exact_point", rec_dde["point_nm"] == [25_000_000, 0], rec_dde["point_nm"])
    check("dde_locked_by_ortho_and_uuid",
          rec_dde["locked_by"] == "ortho" and u_dde == [rec_dde["uuid"], ortho_uuid],
          (rec_dde["locked_by"], u_dde))

    # --- probe 不写盘 ---------------------------------------------------------------
    before = len(store["inputs"])
    probed = coord_input_probe(doc, text="@1<0", base_nm=(0, 0))
    check("probe_no_write_and_hits_mode",
          len(store["inputs"]) == before and probed == [store["modes"][0]["uuid"]],
          (before, len(store["inputs"]), probed))

    # --- 历史上限（真裁掉最旧） ------------------------------------------------------
    doc_small = new_document()
    coord_input_set(doc_small, history_limit=2)
    uuids = [coordinate_input(doc_small, text=f"{i},0")[0] for i in (1, 2, 3)]
    hist = coord_history(doc_small)
    check("history_trim_keeps_newest_first",
          hist == [uuids[2], uuids[1]] and len(doc_small.extra[_STORE_KEY]["inputs"]) == 2, hist)

    # --- 报错 reason（不许静默兜底） -------------------------------------------------
    for kwargs, reason in (({"text": "@1,1"}, "missing_base"),
                           ({"text": "25", "base_nm": (0, 0)}, "missing_direction"),
                           ({"text": "25", "base_nm": (0, 0), "cursor_nm": (0, 0)},
                            "degenerate_direction"),
                           ({"text": "banana"}, "invalid_input"),
                           ({"text": "1,2,3"}, "invalid_input")):
        try:
            coordinate_input(doc, **kwargs)
            got = "no-error"
        except CadError as exc:
            got = exc.reason
        check("error_reason_" + reason + "_" + str(kwargs["text"]), got == reason, got)

    doc_off = new_document()
    coord_input_set(doc_off, enabled=False)
    try:
        coordinate_input(doc_off, text="1,1")
        got = "no-error"
    except CadError as exc:
        got = exc.reason
    check("disabled_input_is_loud", got == "disabled", got)
    check("disabled_probe_empty", coord_input_probe(doc_off, text="1,1") == [])

    # --- L1 往返（真数据后果要存得住） -----------------------------------------------
    doc2 = document_from_l1(document_to_l1(doc))
    check("l1_roundtrip_store_equal", doc2.extra.get(_STORE_KEY) == store,
          repr(doc2.extra.get(_STORE_KEY))[:200])
    check("l1_roundtrip_history", coord_history(doc2) == coord_history(doc), coord_history(doc2))
    problems = [item for item in check_invariants(doc2) if _STORE_KEY in str(item)]
    check("no_invariant_problem_in_store", problems == [], problems)

    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print("FEATURE_FAIL family=%s op=coordinate_input verdict=FAIL failed=%s"
              % (FAMILY, ",".join(failed)))
        for name, ok, detail in results:
            if not ok:
                print("  FAIL %s detail=%s" % (name, detail))
        return 1
    print("FEATURE_OK family=%s op=coordinate_input verdict=PASS" % FAMILY)
    print("SELFTEST assertions=%d passed=%d failed=0 assigned_index=%d slug=%s"
          % (len(results), len(results), ASSIGNED_INDEX, ASSIGNED_SLUG))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
