#!/usr/bin/env python3
"""cadcore.ops.precision_ops —— 精度命令族模块：捕捉开关与优先级 / 正交 / 极轴 / 捕捉追踪 / 动态输入 / 栅格步进。

一句话用途
----------
把 `cadcore.precision.osnap` 那套**无状态纯几何**（`snap` / `ortho_lock` / `polar_track` / `grid_snap`）
包成一层**有状态的命令层**：用户设过的精度开关、优先级、容差、追踪基点、动态输入字段、栅格步长
**持久化在文档/草图里**（关掉会话再打开还在），并由 `precision_apply` 按**固定顺序**把一条原始光标点
解析成吸附后的整数纳米点。这是 `cadcore.ops.<module>` 家族模块之一。

冻结接口（本文件对外只有这三样是契约）
------------------------------------
- ``FAMILY = "precision_ops"``（spec/precision_ops.json 冻结值）；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回**受影响的记录 uuid 列表**（每个操作在
  自己的 docstring 里写清"返回的是谁的 uuid、顺序如何"）。
- 长度一律**整数纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

落盘形态（设置放哪，为什么）
--------------------------
设置**不改 L1 的实体/草图字段**，只放一个额外容器（与 `organization` 族同一套做法）：

- 目标是**文档**（`cadcore.docmodel.Document` 或同形映射）⇒ 表放 ``doc.extra["precision"]``；
  `extra` 是文档层明写的"未知顶层字段可往返"容器，`document_to_l1` → `document_from_l1`
  一圈之后原样还在（自检有一条断言专门验它）。
- 目标是**草图**（`{entities, constraints, dimensions}` 或对象）⇒ 表放 ``sk["precision"]``。
- 表结构（**只加键不改键**）：``{"schema": 1, "modes": [...], "snap_kinds": [...], "cursor": {...}}``：
  - ``modes``：六条模式记录 `{"uuid","name","enabled","config"}`，`name ∈ MODE_NAMES`；
  - ``snap_kinds``：七条捕捉类型记录 `{"uuid","kind","enabled"}`，**列表顺序即优先级**（高 → 低）；
  - ``cursor``：`precision_apply` 写下的"最后一次光标读数"（动态输入显示的来源），可选。

口径（与全仓一致，别自己发明）
----------------------------
- ``0 = 真为零``：`cursor.readout.distance_nm = 0` 表示光标与基点**确实**重合；`angle_udeg = 0`
  表示确实在 +x 轴方向上。
- ``-1 = 未采``：`grid.config.major_every = -1` 表示**用户从未设置**主栅格间距（按缺省生效），
  与 `major_every >= 1`（真设过）可区分。
- ``NA = 不适用``：不适用时**该键缺席**，不写 `None`、不写 0 冒充（未给基点 ⇒ `readout` 里
  `distance_nm` / `angle_udeg` 两个键整个不出现）。
- 坐标一律整数纳米；`*_nm` 键只能是 int。

`precision_apply` 的解析链（顺序写死，下游照抄）
---------------------------------------------
1. **对象捕捉**（`osnap` 模式开启时）：按 `snap_kinds` 的**列表顺序**当优先级，只把 `enabled=True`
   的类型交给 `osnap.snap`；命中 ⇒ 用命中点，且该模式记录 / 类型记录 / 被捕捉实体的 uuid 依次进"生效表"。
2. **栅格步进**：仅当上一步**没有命中**、且 `grid` 模式开启、`snap_enabled=True` 时，`osnap.grid_snap`
   吸到 `spacing_nm` 的整数倍（点本来就在格点上 ⇒ 不算生效）。
3. **正交 / 极轴**（只有在给了基点时才做）：`ortho` 开启 ⇒ 只用 `ortho_lock`；`ortho` 关闭而
   `polar` 开启 ⇒ 用 `polar_track`。**正交优先于极轴**（与 AutoCAD 的 ORTHO 覆盖 POLAR 一致），
   两者不叠加，避免两个锁互相抢方向。
4. **捕捉追踪**（`otrack`）：仅当第 3 步**没有生效**（点没被锁住）时，尝试把点对齐到"过已采集追踪点"
   的**水平 / 垂直对齐路径**上（本层只做 0/90/180/270 四条方向，不做任意极轴角），取容差内最近者；
   平手时按 `points_nm` 的下标靠前者、再按"水平路径优先"。
5. 写 `cursor` 记录（原始点 / 结果点 / 生效表 / 命中信息 / 动态输入读数），返回
   ``[cursor_record_uuid, *生效记录uuid]``（命中对象捕捉时还附上被捕捉实体的 uuid）。

有意限定的边界（写下来，防止下游靠猜）
------------------------------------
1. **本层不做任何文件 I/O**：持久化 = 写进文档/草图容器，存盘交给 io 层。
2. **`snap_probe` 是纯查询**：不改任何设置、不写 `cursor`、不返回假命中（未命中 ⇒ 空列表）；
   开关关掉时也返回空列表 —— 而不是"当作全开"。
3. **`precision_filter` 是纯查询**：表还没建出来就返回 `[]`（"表里还没有记录"），不读缺省值冒充已设置；
   缺省值由各 `*_set` 第一次写盘时落成**真记录**（唯一真源，别在调用点手搓）。
4. **入参错必须炸**：未知捕捉类型 ⇒ `unknown_snap_kind`；优先级没列全 ⇒ `incomplete_priority`；
   未知模式名 ⇒ `unknown_mode`；未知动态输入字段 ⇒ `unknown_field`；坏长度 ⇒ `invalid_length`。
   静默兜底会把调用方的 bug 藏起来。
5. 复用而非重写：几何全部走 `cadcore.precision.osnap`（含命中优先级、容差口径、圆整规则）；
   入参把关复用 `cadcore.edit.modify._as_nm` / `_as_angle`；结构访问走 `cadcore.sketch.constraints`
   的 `_get` / `_set` / `new_uuid` / `deg_to_udeg`。本文件不抄第二份几何。
6. 非 docstring 的字符串字面量一律 ASCII：面向用户的文案由语言层按 key 取词。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/precision_ops.py 时的引导（见文件末 __main__）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    __package__ = "cadcore.ops"

import math
from collections.abc import Iterable as _IterableABC
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

from ..edit.modify import _as_angle, _as_nm
from ..errors import CadError
from ..precision.osnap import SNAP_KINDS, grid_snap, ortho_lock, polar_track, snap
from ..sketch.constraints import _get, _set, deg_to_udeg, new_uuid

__all__ = [
    "FAMILY",
    "MODE_NAMES",
    "OPERATIONS",
    "cursor_readout",
    "dyninput_set",
    "grid_set",
    "ortho_set",
    "otrack_set",
    "polar_set",
    "precision_apply",
    "precision_filter",
    "precision_reset",
    "snap_enable",
    "snap_kinds_set",
    "snap_priority_set",
    "snap_probe",
    "snap_tolerance_set",
]

#: 族 id（冻结）。
#: 族 id（冻结）：spec/precision_ops.json 的 FAMILY 值（同文件 docstring 旧称以本行准）。

#: 精度设置容器在文档 `extra` / 草图里的键名。
_STORE_KEY = "precision"
#: 设置表自身的 schema 版本（只加不改；将来迁移从这里分叉）。
_SCHEMA = 1

#: 六个模式名（冻结面：顺序即 `precision_filter()` 无参时的返回顺序）。
MODE_NAMES: tuple[str, ...] = ("osnap", "ortho", "polar", "otrack", "dyninput", "grid")
#: 动态输入可显示的字段名（冻结面）。
DYN_FIELDS: tuple[str, ...] = ("pointer", "dimension", "dynamic_prompt")

#: 缺省开关（AutoCAD 出厂口径：对象捕捉与动态输入开，正交/极轴/追踪/栅格捕捉关）。
_DEFAULT_ENABLED = {"osnap": True, "ortho": False, "polar": False,
                    "otrack": False, "dyninput": True, "grid": False}
#: 缺省捕捉容差 5 mm（整数纳米）。
_DEFAULT_TOLERANCE_NM = 5_000_000
#: 缺省栅格步长 10 mm（整数纳米）。
_DEFAULT_SPACING_NM = 10_000_000
#: "未传参"哨兵（`None` / `False` / `0` 都是合法取值，不能拿它们当缺省）。
_UNSET: Any = object()


# ======================================================================================
# 错误与入参把关
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(detail, reason=reason, **details)


def _rw(exc: CadError, *, what: str, value: Any, fallback: str) -> CadError:
    """把 modify 校验器的错误换成基类 `CadError`（保留 reason/文案，不丢归因）。"""
    return _fail(str(exc.reason or fallback), str(exc.message), what=what, value=repr(value))


def _bool(value: Any, *, what: str) -> bool:
    """开关入参 → 严格 bool（不收 0/1 冒充，避免"看起来设了"）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))
    return bool(value)


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（坏入参当场报 `invalid_length`）。"""
    try:
        return _as_nm(value, what=what, positive=positive)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_length") from exc


def _tolerance_nm(value: Any, *, what: str = "tolerance_nm") -> int:
    """容差入参 → 非负整数纳米。"""
    tol = _nm(value, what=what)
    if tol < 0:
        raise _fail("out_of_range", f"{what} must be >= 0 nm", what=what, value=int(tol))
    return int(tol)


def _deg(value: Any, *, what: str, nonneg: bool = True) -> float:
    """角度入参 → 有限浮点度（角度不是长度，允许实数）。"""
    try:
        out = _as_angle(value, what=what)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_argument") from exc
    if nonneg and out < 0.0:
        raise _fail("out_of_range", f"{what} must be >= 0", what=what, value=repr(value))
    return out


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    return (_nm(value[0], what=f"{what}.x"), _nm(value[1], what=f"{what}.y"))


def _points(value: Any, *, what: str) -> list[tuple[int, int]]:
    """点序列入参 → `[(x_nm, y_nm), ...]`（形状坏 ⇒ 当场报错，不静默丢点）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC):
        raise _fail("invalid_argument", f"{what} must be a sequence of (x_nm, y_nm) pairs",
                    what=what, value=repr(value))
    return [_point(item, what=f"{what}[{index}]") for index, item in enumerate(value)]


def _snap_kind(value: Any, *, what: str = "kind") -> str:
    """捕捉类型名 → 冻结枚举内的名字。"""
    if not isinstance(value, str) or value not in SNAP_KINDS:
        raise _fail("unknown_snap_kind", f"{what} is not a known snap kind",
                    what=what, value=repr(value), known=list(SNAP_KINDS))
    return value


def _snap_kinds(value: Any, *, what: str) -> list[str]:
    """捕捉类型集合 → 去重后的名字列表（裸字符串不算集合，报 `invalid_argument`）。"""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _IterableABC):
        raise _fail("invalid_argument", f"{what} must be an iterable of snap kind names",
                    what=what, value=repr(value))
    out: list[str] = []
    for item in value:
        name = _snap_kind(item, what=f"{what} item")
        if name not in out:
            out.append(name)
    return out


def _mode_name(value: Any) -> str:
    """模式名 → 冻结枚举内的名字。"""
    if not isinstance(value, str) or value not in MODE_NAMES:
        raise _fail("unknown_mode", "name is not a known precision mode",
                    value=repr(value), known=list(MODE_NAMES))
    return value


def _increments(value: Any, *, what: str = "increments_deg") -> list[float]:
    """极轴增量集合 → `(0, 360]` 内去重升序保留原顺序的浮点度列表。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) == 0:
        raise _fail("invalid_argument", f"{what} must be a non-empty sequence of angles",
                    what=what, value=repr(value))
    out: list[float] = []
    for index, item in enumerate(value):
        deg = _deg(item, what=f"{what}[{index}]", nonneg=False)
        if deg <= 0.0 or deg > 360.0:
            raise _fail("out_of_range", f"{what} items must be in (0, 360]", what=what, value=repr(item))
        if deg not in out:
            out.append(deg)
    return out


def _dyn_fields(value: Any, *, what: str = "fields") -> list[str]:
    """动态输入字段集合 → 去重后的冻结枚举名列表。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _IterableABC):
        raise _fail("invalid_argument", f"{what} must be an iterable of field names",
                    what=what, value=repr(value))
    out: list[str] = []
    for item in value:
        if item not in DYN_FIELDS:
            raise _fail("unknown_field", f"{what} item is not a known dynamic-input field",
                        what=what, value=repr(item), known=list(DYN_FIELDS))
        if item not in out:
            out.append(item)
    return out


def _major_every(value: Any) -> int:
    """主栅格间隔 → `-1`（未采 = 用户从未设置）或 `>= 1`（真设过）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "major_every must be an int (-1 = unset) or >= 1",
                    value=repr(value))
    if value == -1 or value >= 1:
        return int(value)
    raise _fail("out_of_range", "major_every must be -1 (unset) or >= 1", value=int(value))


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米：四舍五入、远离零（`±0.5` 对称，可复现）。"""
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


# ======================================================================================
# 缺省表与结构校验
# ======================================================================================


def _default_config(name: str) -> dict[str, Any]:
    """单个模式的出厂配置（**唯一真源**：新表只从这里长，别在调用点手搓）。"""
    if name == "osnap":
        return {"tolerance_nm": int(_DEFAULT_TOLERANCE_NM)}
    if name == "ortho":
        return {"tolerance_deg": 3.0}
    if name == "polar":
        return {"increments_deg": [15.0, 30.0, 45.0, 90.0], "tolerance_deg": 3.0}
    if name == "otrack":
        return {"tolerance_nm": int(_DEFAULT_TOLERANCE_NM), "points_nm": []}
    if name == "dyninput":
        return {"fields": list(DYN_FIELDS)}
    if name == "grid":
        return {"spacing_nm": int(_DEFAULT_SPACING_NM), "snap_enabled": False, "major_every": -1}
    raise _fail("unknown_mode", "name is not a known precision mode", value=repr(name), known=list(MODE_NAMES))


def _empty_store() -> dict[str, Any]:
    """空设置表（`modes` / `snap_kinds` 由 `_ensure()` 按缺省补齐）。"""
    return {"schema": int(_SCHEMA), "modes": [], "snap_kinds": []}


def _ensure(store: dict[str, Any]) -> dict[str, Any]:
    """就地补齐缺省记录并校验结构（坏结构报 `corrupt_store`，不静默重置成缺省）。"""
    schema = store.get("schema")
    if schema is None:
        store["schema"] = int(_SCHEMA)
    elif schema != _SCHEMA:
        raise _fail("unsupported_schema", "precision store schema is not supported",
                    schema=repr(schema), supported=int(_SCHEMA))

    modes = store.get("modes")
    if modes is None:
        modes = []
        store["modes"] = modes
    if not isinstance(modes, list):
        raise _fail("corrupt_store", "precision store 'modes' must be a list",
                    value_type=type(modes).__name__)
    seen: set[str] = set()
    for record in modes:
        if (not isinstance(record, _MutableMappingABC) or not isinstance(record.get("name"), str)
                or not isinstance(record.get("uuid"), str)):
            raise _fail("corrupt_store", "precision mode record is malformed", value=repr(record))
        if not isinstance(record.get("config"), _MutableMappingABC):
            record["config"] = _default_config(record["name"])
        if not isinstance(record.get("enabled"), bool):
            record["enabled"] = bool(_DEFAULT_ENABLED.get(record["name"], False))
        seen.add(record["name"])
    for name in MODE_NAMES:
        if name not in seen:
            modes.append({"uuid": new_uuid(), "name": name,
                          "enabled": bool(_DEFAULT_ENABLED[name]), "config": _default_config(name)})

    kinds = store.get("snap_kinds")
    if kinds is None:
        kinds = []
        store["snap_kinds"] = kinds
    if not isinstance(kinds, list):
        raise _fail("corrupt_store", "precision store 'snap_kinds' must be a list",
                    value_type=type(kinds).__name__)
    known: set[str] = set()
    for record in kinds:
        if (not isinstance(record, _MutableMappingABC) or record.get("kind") not in SNAP_KINDS
                or not isinstance(record.get("uuid"), str)):
            raise _fail("corrupt_store", "precision snap-kind record is malformed", value=repr(record))
        if not isinstance(record.get("enabled"), bool):
            record["enabled"] = True
        known.add(record["kind"])
    for kind in SNAP_KINDS:
        if kind not in known:
            kinds.append({"uuid": new_uuid(), "kind": kind, "enabled": True})
    return store


def _holder(target: Any) -> Any:
    """文档 ⇒ `extra` 容器；映射草图 ⇒ 草图自身；对象草图 ⇒ `None`（走属性）。"""
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        extra = _get(target, "extra", None)
        if isinstance(extra, _MutableMappingABC):
            return extra
        if isinstance(target, _MutableMappingABC):
            return target
        raise _fail("bad_target", "document target has no mutable extra container",
                    value_type=type(target).__name__)
    if isinstance(target, _MutableMappingABC):
        return target
    return None


def _load(target: Any) -> Any:
    """取原始设置表（缺席 ⇒ `None`，不建表、不写盘）。"""
    holder = _holder(target)
    if holder is not None:
        return holder.get(_STORE_KEY)
    return _get(target, _STORE_KEY, None)


def _save(target: Any, store: Any) -> None:
    """写回设置表（对象草图走属性；写不进去由 `_set` 显式失败）。"""
    holder = _holder(target)
    if holder is not None:
        holder[_STORE_KEY] = store
        return
    _set(target, _STORE_KEY, store)


def _store(target: Any, *, create: bool = True, ensure: bool = True) -> dict[str, Any] | None:
    """`sk_or_doc` → 设置表（`create=False` 时没表就返回 `None`；`ensure=False` 时不补齐缺省）。"""
    value = _load(target)
    if value is None:
        if not create:
            return None
        value = _empty_store()
        _save(target, value)
    if not isinstance(value, _MutableMappingABC):
        raise _fail("corrupt_store", "precision store is not a mapping", value_type=type(value).__name__)
    return _ensure(value) if ensure else value


def _raw_records(store: dict[str, Any], key: str) -> list[Any]:
    """查询用：读原始记录列表（缺 ⇒ 空表；形状坏 ⇒ `corrupt_store`）。"""
    value = store.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _fail("corrupt_store", "precision store table is not a list", table=key,
                    value_type=type(value).__name__)
    for record in value:
        if not isinstance(record, _MutableMappingABC) or not isinstance(record.get("uuid"), str):
            raise _fail("corrupt_store", "precision record is malformed", table=key, value=repr(record))
    return value


def _modes(store: dict[str, Any]) -> list[Any]:
    """模式记录列表（已校验）。"""
    return _raw_records(store, "modes")


def _kind_records(store: dict[str, Any]) -> list[Any]:
    """捕捉类型记录列表（**顺序即优先级**）。"""
    return _raw_records(store, "snap_kinds")


def _mode(store: dict[str, Any], name: str) -> Any:
    """取单条模式记录（缺席 ⇒ `corrupt_store`，不静默造一条）。"""
    for record in _modes(store):
        if record.get("name") == name:
            return record
    raise _fail("corrupt_store", "precision mode record is missing", mode=name)


def _kind_record(store: dict[str, Any], kind: str) -> Any:
    """取单条捕捉类型记录。"""
    for record in _kind_records(store):
        if record.get("kind") == kind:
            return record
    raise _fail("corrupt_store", "precision snap-kind record is missing", kind=kind)


def _config(record: Any, key: str) -> Any:
    """读模式配置项（缺 ⇒ `corrupt_store`：坏表不许静默按缺省跑）。"""
    config = record.get("config")
    if not isinstance(config, _MappingABC) or key not in config:
        raise _fail("corrupt_store", "precision mode config entry is missing",
                    mode=record.get("name"), key=key)
    return config[key]


def _pick_sketch(target: Any, sketch: Any) -> Any:
    """选一个做捕捉的草图：文档要多草图时用 `sketch=<uuid>` 点名（禁下标引用）。"""
    sketches = _get(target, "sketches", None)
    if not isinstance(sketches, list):
        return target
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


# ======================================================================================
# 几何调用包装（把 osnap 的 ValueError 换成 CadError；CadError 原样上抛）
# ======================================================================================


def _osnap_call(sk: Any, point: tuple[int, int], kinds: list[str], tolerance_nm: int) -> Any:
    """`osnap.snap` 的族内调用口径（未命中返回 `None`，不伪造命中）。"""
    try:
        return snap(sk, point, kinds=tuple(kinds), tolerance_nm=int(tolerance_nm))
    except ValueError as exc:
        raise _fail("invalid_argument", str(exc), point_nm=[int(point[0]), int(point[1])]) from exc


def _grid_call(point: tuple[int, int], spacing_nm: int) -> tuple[int, int]:
    """`osnap.grid_snap` 的族内调用口径。"""
    try:
        return grid_snap(point, spacing_nm=int(spacing_nm))
    except ValueError as exc:
        raise _fail("invalid_argument", str(exc), spacing_nm=int(spacing_nm)) from exc


def _ortho_call(base: tuple[int, int], point: tuple[int, int], tolerance_deg: Any) -> tuple[int, int]:
    """`osnap.ortho_lock` 的族内调用口径。"""
    try:
        return ortho_lock(base, point, tolerance_deg=float(tolerance_deg))
    except ValueError as exc:
        raise _fail("invalid_argument", str(exc)) from exc


def _polar_call(base: tuple[int, int], point: tuple[int, int],
                increments_deg: Any, tolerance_deg: Any) -> tuple[int, int]:
    """`osnap.polar_track` 的族内调用口径。"""
    try:
        return polar_track(base, point, increments_deg=tuple(increments_deg), tolerance_deg=float(tolerance_deg))
    except ValueError as exc:
        raise _fail("invalid_argument", str(exc)) from exc


def _align_track(point: tuple[int, int], points_nm: Any, tolerance_nm: Any) -> tuple[int, int] | None:
    """把点对齐到"过已采集追踪点"的水平/垂直路径上（容差内最近者）；无候选 ⇒ `None`。"""
    tolerance = float(tolerance_nm)
    best: tuple[int, int] | None = None
    best_key: tuple[float, int, int] | None = None
    for index, item in enumerate(points_nm):
        px, py = int(item[0]), int(item[1])
        candidates = ((point[0], py), (px, point[1]))  # 水平路径优先于垂直路径
        for rank, candidate in enumerate(candidates):
            dist = math.hypot(float(candidate[0] - point[0]), float(candidate[1] - point[1]))
            if dist > tolerance:
                continue
            key = (dist, index, rank)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
    return best


# ======================================================================================
# 插件：捕捉开关与优先级
# ======================================================================================


def snap_enable(target: Any, *, enabled: bool) -> list[str]:
    """对象捕捉**总开关**。返回 `[osnap 模式记录 uuid]`。

    关掉总开关后 `snap_probe` / `precision_apply` 一律不走对象捕捉（返回空列表 / 不生效），
    但各捕捉类型自己的 `enabled` 位原样保留 —— 重新打开不用重设。
    """
    flag = _bool(enabled, what="enabled")
    store = _store(target)
    record = _mode(store, "osnap")
    record["enabled"] = flag
    return [record["uuid"]]


def snap_kinds_set(target: Any, *, kinds: Any) -> list[str]:
    """设置**哪些捕捉类型参与**（列出的开、没列出的关）。

    返回**已启用**的类型记录 uuid，**按当前优先级顺序**（不是入参顺序）。空集合合法 = 全部关掉。
    """
    wanted = _snap_kinds(kinds, what="kinds")
    store = _store(target)
    for record in _kind_records(store):
        record["enabled"] = record["kind"] in wanted
    return [record["uuid"] for record in _kind_records(store) if record["enabled"]]


def snap_priority_set(target: Any, *, priority: Any) -> list[str]:
    """重排捕捉**优先级**（列表顺序 = 优先级，高 → 低）。

    `priority` 必须把 `SNAP_KINDS` 的每个类型**恰好列一次**（漏列/重复 ⇒ `incomplete_priority`）。
    返回重排后的**全部**类型记录 uuid（顺序 = 新优先级）。
    """
    order = _snap_kinds(priority, what="priority")
    if len(order) != len(SNAP_KINDS) or set(order) != set(SNAP_KINDS):
        raise _fail("incomplete_priority", "priority must list every snap kind exactly once",
                    missing=sorted(set(SNAP_KINDS) - set(order)),
                    duplicate=sorted({k for k in order if order.count(k) > 1}))
    store = _store(target)
    by_kind = {record["kind"]: record for record in _kind_records(store)}
    ordered = [by_kind[kind] for kind in order]
    store["snap_kinds"] = ordered
    return [record["uuid"] for record in ordered]


def snap_tolerance_set(target: Any, *, tolerance_nm: Any) -> list[str]:
    """设置对象捕捉命中半径（整数纳米，`>= 0`）。返回 `[osnap 模式记录 uuid]`。"""
    tol = _tolerance_nm(tolerance_nm)
    store = _store(target)
    record = _mode(store, "osnap")
    record["config"]["tolerance_nm"] = int(tol)
    return [record["uuid"]]


def snap_probe(target: Any, *, point_nm: Any, sketch: Any = None) -> list[str]:
    """用**持久化设置**做一次对象捕捉（纯查询，不写盘）。

    返回 `[命中的捕捉类型记录 uuid]`；未命中、总开关关掉、或没有任何类型启用 ⇒ `[]`
    （**不返回假命中**，也不把开关关掉当成全开）。
    """
    point = _point(point_nm, what="point_nm")
    store = _store(target, create=False)
    if store is None:
        return []
    mode = _mode(store, "osnap")
    if not mode["enabled"]:
        return []
    kinds = [record["kind"] for record in _kind_records(store) if record["enabled"]]
    if not kinds:
        return []
    hit = _osnap_call(_pick_sketch(target, sketch), point, kinds, int(_config(mode, "tolerance_nm")))
    if hit is None:
        return []
    return [_kind_record(store, hit.kind)["uuid"]]


# ======================================================================================
# 插件：正交 / 极轴 / 捕捉追踪
# ======================================================================================


def ortho_set(target: Any, *, enabled: Any = _UNSET, tolerance_deg: Any = _UNSET) -> list[str]:
    """正交锁定开关 / 容差（度）。只传要改的项。返回 `[ortho 模式记录 uuid]`。

    正交开启时**极轴不参与**（与 AutoCAD 的 ORTHO 覆盖 POLAR 一致），见 `precision_apply` 解析链。
    """
    store = _store(target)
    record = _mode(store, "ortho")
    if enabled is not _UNSET:
        record["enabled"] = _bool(enabled, what="enabled")
    if tolerance_deg is not _UNSET:
        record["config"]["tolerance_deg"] = _deg(tolerance_deg, what="tolerance_deg")
    return [record["uuid"]]


def polar_set(target: Any, *, enabled: Any = _UNSET, increments_deg: Any = _UNSET,
              tolerance_deg: Any = _UNSET) -> list[str]:
    """极轴追踪开关 / 增量角集合 / 容差。返回 `[polar 模式记录 uuid]`。

    增量角要求都在 `(0, 360]`；命中后**只改方向不改距离**（口径在 `osnap.polar_track` 里冻结）。
    """
    store = _store(target)
    record = _mode(store, "polar")
    if enabled is not _UNSET:
        record["enabled"] = _bool(enabled, what="enabled")
    if increments_deg is not _UNSET:
        record["config"]["increments_deg"] = _increments(increments_deg)
    if tolerance_deg is not _UNSET:
        record["config"]["tolerance_deg"] = _deg(tolerance_deg, what="tolerance_deg")
    return [record["uuid"]]


def otrack_set(target: Any, *, enabled: Any = _UNSET, points_nm: Any = _UNSET,
               tolerance_nm: Any = _UNSET) -> list[str]:
    """捕捉追踪（对象捕捉追踪）开关 / 已采集追踪点 / 对齐容差。返回 `[otrack 模式记录 uuid]`。

    追踪点是**持久化的采集点**（AutoCAD 里悬停后出现的小十字）；`points_nm` 是整数纳米点序列，
    传空列表 = 清空采集。对齐路径本层只做水平/垂直（见 `_align_track`）。
    """
    store = _store(target)
    record = _mode(store, "otrack")
    if enabled is not _UNSET:
        record["enabled"] = _bool(enabled, what="enabled")
    if points_nm is not _UNSET:
        record["config"]["points_nm"] = [[p[0], p[1]] for p in _points(points_nm, what="points_nm")]
    if tolerance_nm is not _UNSET:
        record["config"]["tolerance_nm"] = _tolerance_nm(tolerance_nm)
    return [record["uuid"]]


# ======================================================================================
# 插件：动态输入 / 栅格步进
# ======================================================================================


def dyninput_set(target: Any, *, enabled: Any = _UNSET, fields: Any = _UNSET) -> list[str]:
    """动态输入开关 / 显示字段集合（`pointer` / `dimension` / `dynamic_prompt`）。

    返回 `[dyninput 模式记录 uuid]`。字段只影响 `precision_apply` 写下的 `cursor.readout.fields`
    （UI 拿它决定显示什么），**不影响几何解析链**。
    """
    store = _store(target)
    record = _mode(store, "dyninput")
    if enabled is not _UNSET:
        record["enabled"] = _bool(enabled, what="enabled")
    if fields is not _UNSET:
        record["config"]["fields"] = _dyn_fields(fields)
    return [record["uuid"]]


def grid_set(target: Any, *, enabled: Any = _UNSET, spacing_nm: Any = _UNSET,
             snap_enabled: Any = _UNSET, major_every: Any = _UNSET) -> list[str]:
    """栅格步进持久化：显示开关 / 步长（整数纳米，`> 0`）/ 是否参与捕捉 / 主格间隔。

    `major_every` 口径：``-1 = 未采``（用户从未设置，按缺省生效）、``>= 1`` = 真设过。
    返回 `[grid 模式记录 uuid]`。
    """
    store = _store(target)
    record = _mode(store, "grid")
    if enabled is not _UNSET:
        record["enabled"] = _bool(enabled, what="enabled")
    if spacing_nm is not _UNSET:
        record["config"]["spacing_nm"] = _nm(spacing_nm, what="spacing_nm", positive=True)
    if snap_enabled is not _UNSET:
        record["config"]["snap_enabled"] = _bool(snap_enabled, what="snap_enabled")
    if major_every is not _UNSET:
        record["config"]["major_every"] = _major_every(major_every)
    return [record["uuid"]]


# ======================================================================================
# 插件：解析一条光标点 / 查询 / 复位
# ======================================================================================


def precision_apply(target: Any, *, point_nm: Any, base_nm: Any = None,
                    sketch: Any = None) -> list[str]:
    """按**持久化设置**把一条原始光标点解析成吸附后的点，并写下 `cursor` 读数记录。

    解析链（顺序写死，见模块 docstring）：对象捕捉 → 栅格步进（前者未命中才做）→ 正交/极轴（有基点才做）
    → 捕捉追踪（前三步都没锁住才做）。

    返回 ``[cursor 记录 uuid, *生效记录 uuid]``：生效表按**实际发生顺序**排列，命中对象捕捉时还会附上
    被捕捉实体的 uuid。结果点 / 读数从 ``cursor_readout()`` 读（动态输入与状态栏的唯一来源）。
    """
    point = _point(point_nm, what="point_nm")
    base = None if base_nm is None else _point(base_nm, what="base_nm")
    store = _store(target)
    fired: list[str] = []
    resolved = point
    hit_info: dict[str, Any] | None = None

    # 1. 对象捕捉（优先级 = snap_kinds 的列表顺序）
    osnap_record = _mode(store, "osnap")
    if osnap_record["enabled"]:
        kinds = [record["kind"] for record in _kind_records(store) if record["enabled"]]
        if kinds:
            hit = _osnap_call(_pick_sketch(target, sketch), point, kinds,
                              int(_config(osnap_record, "tolerance_nm")))
            if hit is not None:
                resolved = (int(hit.point_nm[0]), int(hit.point_nm[1]))
                fired.append(osnap_record["uuid"])
                fired.append(_kind_record(store, hit.kind)["uuid"])
                entity = hit.ref[0] if hit.ref else ""
                if isinstance(entity, str) and entity:
                    fired.append(entity)
                hit_info = {
                    "kind": hit.kind,
                    "ref": [part if isinstance(part, str) else str(part) for part in hit.ref],
                    "distance_nm": int(hit.distance_nm),
                }

    # 2. 栅格步进：只在对象捕捉没命中时接管
    if resolved == point:
        grid_record = _mode(store, "grid")
        if grid_record["enabled"] and bool(_config(grid_record, "snap_enabled")):
            snapped = _grid_call(point, _nm(_config(grid_record, "spacing_nm"),
                                            what="spacing_nm", positive=True))
            if snapped != point:
                resolved = snapped
                fired.append(grid_record["uuid"])

    # 3. 正交 / 极轴（只有给了基点才做；正交优先，极轴不与之叠加）
    if base is not None:
        locked = False
        ortho_record = _mode(store, "ortho")
        polar_record = _mode(store, "polar")
        if ortho_record["enabled"]:
            candidate = _ortho_call(base, resolved, _config(ortho_record, "tolerance_deg"))
            if candidate != resolved:
                resolved = candidate
                locked = True
                fired.append(ortho_record["uuid"])
        elif polar_record["enabled"]:
            candidate = _polar_call(base, resolved, _config(polar_record, "increments_deg"),
                                    _config(polar_record, "tolerance_deg"))
            if candidate != resolved:
                resolved = candidate
                locked = True
                fired.append(polar_record["uuid"])

        # 4. 捕捉追踪：只在正交/极轴没锁住时尝试（避免两个锁互抢）
        otrack_record = _mode(store, "otrack")
        if not locked and otrack_record["enabled"]:
            aligned = _align_track(resolved, _config(otrack_record, "points_nm"),
                                   _config(otrack_record, "tolerance_nm"))
            if aligned is not None and aligned != resolved:
                resolved = aligned
                fired.append(otrack_record["uuid"])

    # 5. 写 cursor 读数（动态输入 / 状态栏的唯一来源）
    cursor = store.get("cursor")
    if not isinstance(cursor, _MutableMappingABC):
        cursor = {"uuid": new_uuid()}
        store["cursor"] = cursor
    cursor["raw_nm"] = [int(point[0]), int(point[1])]
    cursor["point_nm"] = [int(resolved[0]), int(resolved[1])]
    cursor["source"] = list(fired)
    if hit_info is None:
        cursor.pop("hit", None)
    else:
        cursor["hit"] = hit_info

    dyninput_record = _mode(store, "dyninput")
    readout: dict[str, Any] = {"position_nm": [int(resolved[0]), int(resolved[1])]}
    if base is not None:  # 没有基点 ⇒ distance/angle 是 NA，键整个不出现（不写 0 冒充）
        dx = resolved[0] - base[0]
        dy = resolved[1] - base[1]
        readout["distance_nm"] = _round_nm(math.hypot(float(dx), float(dy)))
        if dx != 0 or dy != 0:  # 与基点重合 ⇒ 方向未定义，angle 键缺席
            readout["angle_udeg"] = deg_to_udeg(math.degrees(math.atan2(float(dy), float(dx))) % 360.0)
    readout["fields"] = (list(_config(dyninput_record, "fields"))
                         if dyninput_record["enabled"] else [])
    cursor["readout"] = readout
    return [cursor["uuid"], *fired]


def cursor_readout(target: Any) -> dict[str, Any] | None:
    """读回最后一次 `precision_apply` 的光标读数（没跑过 ⇒ `None`）。

    **不是** OPERATIONS 里的操作：它返回字典而不是 uuid 列表（冻结接口只约束操作）。
    返回的是**展平视图**：`cursor` 记录的字段（`uuid` / `raw_nm` / `point_nm` / `source` / 可选 `hit`）
    与动态输入读数（`position_nm` / `distance_nm` / `angle_udeg` / `fields`）**同在顶层** ——
    `precision_apply` 的 docstring、本模块自检 `main()` 与状态栏/动态输入都按这个形态读。
    存储形态不变，仍是 `cursor["readout"]` 嵌套（口径见模块头 `cursor.readout.distance_nm`）。
    口径：`distance_nm == 0` 是真为零；没有基点时 `distance_nm` / `angle_udeg` 键缺席 = NA。
    """
    store = _store(target, create=False, ensure=False)
    if store is None:
        return None
    cursor = store.get("cursor")
    if not isinstance(cursor, _MappingABC) or not isinstance(cursor.get("uuid"), str):
        return None
    flat = dict(cursor)
    readout = cursor.get("readout")
    if isinstance(readout, _MappingABC):
        flat.update(readout)
    return flat


def precision_filter(target: Any, *, name: Any = None, kind: Any = None,
                     enabled: Any = None) -> list[str]:
    """查询精度设置表的记录 uuid（纯查询，不建表、不补缺省、不写盘）。

    - `kind=<捕捉类型名>` ⇒ 该捕捉类型的记录 uuid（可再叠 `enabled=` 过滤）；
    - `name=<模式名>` ⇒ 该模式记录 uuid（可叠 `enabled=`）；
    - 都不传 ⇒ **全部模式**记录 uuid，按 `MODE_NAMES` 顺序（可叠 `enabled=`）。
    表还没建出来 ⇒ `[]`（"表里还没有记录"），不拿缺省值冒充已设置。
    """
    store = _store(target, create=False, ensure=False)
    if store is None:
        return []
    flag = None if enabled is None else _bool(enabled, what="enabled")
    if kind is not None:
        if name is not None:
            raise _fail("invalid_argument", "pass either name or kind, not both",
                        name=repr(name), kind=repr(kind))
        wanted = _snap_kind(kind)
        return [record["uuid"] for record in _raw_records(store, "snap_kinds")
                if record.get("kind") == wanted and (flag is None or bool(record.get("enabled")) is flag)]
    records = _raw_records(store, "modes")
    if name is not None:
        wanted_mode = _mode_name(name)
        return [record["uuid"] for record in records
                if record.get("name") == wanted_mode and (flag is None or bool(record.get("enabled")) is flag)]
    out: list[str] = []
    for mode_name in MODE_NAMES:
        for record in records:
            if record.get("name") == mode_name and (flag is None or bool(record.get("enabled")) is flag):
                out.append(record["uuid"])
    return out


def precision_reset(target: Any) -> list[str]:
    """恢复出厂缺省：丢弃整张设置表（含 `cursor` 读数）后按缺省重建。

    返回 ``[*modes uuid（按 MODE_NAMES 顺序）, *snap_kinds uuid（按缺省优先级）]``。
    这是本层唯一的"回滚"入口（口径见 G1：负优化必须能正确回滚）。
    """
    fresh = _empty_store()
    _save(target, fresh)
    store = _ensure(fresh)
    return [record["uuid"] for record in _modes(store)] + [record["uuid"] for record in _kind_records(store)]


# ======================================================================================
# 操作表（冻结接口之二）
# ======================================================================================

# ======================================================================================
# spec 面：docs/research/cad-parity/spec/precision_ops.json 的冻结接口
#   - 状态落 ``doc["settings"]["drafting"]``（节点缺席 ⇒ CadError(reason='settings_missing')）；
#   - 数值结果同时落 ``doc["_ops"]["precision_ops"][<op>]``（返回的是被锚定实体 uuid 列表）；
#   - 坏入参一律 ``CadError(reason='bad_arg')``，未知捕捉类型 ⇒ ``'unknown_kind'``；
#   - 几何全部转交 ``cadcore.precision.osnap``（不抄第二份几何）。
#   同名的 ortho_set / polar_set / grid_set 在本节被"两面合一"的版本覆写：
#   带 ``settings.drafting`` 的文档走 spec 面，其余目标转交 ``_legacy_*``（族内 precision 表），
#   旧实现仍被引用，不是死代码。
# ======================================================================================

#: 状态栏七键（spec item 21 冻结）。
DRAFTING_KEYS: tuple[str, ...] = ("osnap", "grid", "ortho", "snap_step", "polar", "otrack", "dyn")
#: 正交与极轴同时开启时的裁决字段取值（spec item 12）。
_ORDER_VALUES: tuple[str, ...] = ("ortho_first", "polar_first")
#: 坐标输入单位 → 纳米（spec item 20 的冻结键集）。
_UNIT_SCALE_NM: dict[str, int] = {"mm": 1_000_000, "cm": 10_000_000, "m": 1_000_000_000,
                                  "in": 25_400_000, "nm": 1}

try:  # 单位表优先复用 cadcore.units（只在口径一致时采用；缺席/不一致 ⇒ 用上面的冻结表）
    from ..units import unit_scale_nm as _UNIT_TABLE  # type: ignore

    if isinstance(_UNIT_TABLE, _MappingABC) and int(_UNIT_TABLE.get("mm", 0)) == 1_000_000:
        _UNIT_SCALE_NM = {str(k): int(v) for k, v in _UNIT_TABLE.items()}
except Exception:  # noqa: BLE001 - 单位模块缺席不改变本文件的单位口径
    pass

#: 引擎函数别名（下面 ortho_lock / polar_track / grid_snap 会被 spec 面覆写，别名先存好）。
_ENGINE_ORTHO_LOCK = ortho_lock
_ENGINE_POLAR_TRACK = polar_track
_ENGINE_GRID_SNAP = grid_snap
#: 族内面（precision 表）的旧实现，供覆写版转交。
_legacy_ortho_set = ortho_set
_legacy_polar_set = polar_set
_legacy_grid_set = grid_set


def _bad(what: str, value: Any = None) -> CadError:
    """spec 面的坏入参信号：reason 一律 'bad_arg'。"""
    return _fail("bad_arg", f"{what} is invalid", what=what, value=repr(value))


def _spec_node(target: Any) -> dict[str, Any]:
    """取 ``doc["settings"]["drafting"]``（缺席 ⇒ settings_missing）。"""
    node = None
    if isinstance(target, _MappingABC):
        settings = target.get("settings")
        if isinstance(settings, _MappingABC):
            node = settings.get("drafting")
    if not isinstance(node, _MutableMappingABC):
        raise _fail("settings_missing", "doc has no settings.drafting node")
    return node


def _is_drafting_target(target: Any) -> bool:
    """目标是不是带 ``settings.drafting`` 的文档（决定同名操作走 spec 面还是族内面）。"""
    if not isinstance(target, _MutableMappingABC):
        return False
    settings = target.get("settings")
    return isinstance(settings, _MappingABC) and isinstance(settings.get("drafting"), _MutableMappingABC)


def _spec_int(value: Any, what: str) -> int:
    """spec 面整数纳米（bool/float 一律 bad_arg，不四舍五入）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad(what, value)
    return int(value)


def _spec_pt(value: Any, what: str = "point_nm") -> tuple[int, int]:
    """spec 面点 = 长度 2 的整数纳米序列。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        raise _bad(what, value)
    return (_spec_int(value[0], f"{what}.x"), _spec_int(value[1], f"{what}.y"))


def _spec_tol(value: Any, what: str = "tolerance_nm") -> int:
    """spec 面容差：非负整数纳米（0 合法 = 只精确命中）。"""
    tol = _spec_int(value, what)
    if tol < 0:
        raise _bad(what, value)
    return tol


def _spec_deg(value: Any, what: str, *, positive: bool = False) -> float:
    """spec 面角度：有限浮点；``positive=True`` 时要求 > 0，否则要求 >= 0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _bad(what, value)
    out = float(value)
    if out <= 0.0 if positive else out < 0.0:
        raise _bad(what, value)
    return out


def _spec_names(value: Any, what: str) -> list[str]:
    """spec 面枚举序列（str/bytes 不当序列，坏形状 ⇒ bad_arg）。"""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _IterableABC):
        raise _bad(what, value)
    return [str(item) for item in value]


def _spec_num(text: str, what: str) -> float:
    """文本 → 有限浮点（解析不了/非有限 ⇒ bad_arg）。"""
    try:
        value = float(text)
    except Exception:  # noqa: BLE001 - 解析失败就是坏入参
        raise _bad(what, text) from None
    if not math.isfinite(value):
        raise _bad(what, text)
    return value


def _entities(container: Any) -> list[Any]:
    """spec 面目标上的 L1 实体列表（缺席 ⇒ bad_target）。"""
    ents = container.get("entities") if isinstance(container, _MappingABC) else _get(container, "entities", None)
    if not isinstance(ents, _SequenceABC) or isinstance(ents, (str, bytes)):
        raise _fail("bad_target", "target has no entity list", value_type=type(container).__name__)
    return list(ents)


def _chan(container: Any, name: str, **fields: Any) -> None:
    """spec 结果通道：``doc['_ops']['precision_ops'][<op>] = {...}``。"""
    if isinstance(container, _MutableMappingABC):
        container.setdefault("_ops", {}).setdefault("precision_ops", {})[name] = dict(fields)


def _anchored(container: Any, ref: Any) -> list[str]:
    """把引擎命中的 ref 折成"被锚定实体 uuid"列表（丢掉 'intersection' 之类标记）。"""
    items = [str(part) for part in (ref or ()) if isinstance(part, str)]
    known: set[str] = set()
    try:
        for ent in _entities(container):
            uid = _get(ent, "uuid", None)
            if isinstance(uid, str):
                known.add(uid)
    except CadError:
        known = set()
    if known:
        hits = [item for item in items if item in known]
        if hits:
            return hits
    return items[:1]


def _drafting_cfg(container: Any) -> tuple[bool, list[str], Any]:
    """读 ``settings.drafting.osnap``（文档缺席 ⇒ 全开 + SNAP_KINDS 全集；不抛错）。"""
    node = None
    if isinstance(container, _MappingABC):
        settings = container.get("settings")
        if isinstance(settings, _MappingABC):
            node = settings.get("drafting")
    enabled, modes = True, list(SNAP_KINDS)
    if isinstance(node, _MappingABC):
        cfg = node.get("osnap")
        if isinstance(cfg, _MappingABC):
            if "enabled" in cfg:
                enabled = bool(cfg["enabled"])
            if cfg.get("modes"):
                modes = [str(m) for m in cfg["modes"]]
    return enabled, modes, node


# ---- item 1/10：对象捕捉总开关 / 优先级 / 忽略图案填充 --------------------------------


def osnap_set(sk_or_doc: Any, *, enabled: Any = _UNSET, modes: Any = _UNSET,
              priority: Any = _UNSET, ignore_hatch: Any = _UNSET) -> list[str]:
    """对象捕捉设置（spec item 1 & 10）：开关 / 参与类型 / 优先级 / 忽略图案填充。

    写 ``doc['settings']['drafting']['osnap']``，返回 ``[drafting_uuid]``。
    ``modes`` 只做过滤，**不改优先级顺序**（顺序只由 SNAP_KINDS 决定）；
    ``priority`` 是捕捉类型的**部分优先顺序**：列出的按给定顺序排最前，未列的保持 SNAP_KINDS 的
    缺省相对顺序；空列表 / 重复类型 ⇒ ``bad_arg``，未知类型 ⇒ ``unknown_kind``。
    全部入参先校验后落盘：校验失败时状态一个字节都不动（幂等失败）。
    """
    node = _spec_node(sk_or_doc)
    if enabled is not _UNSET and not isinstance(enabled, bool):
        raise _bad("enabled", enabled)
    mode_list: list[str] | None = None
    if modes is not _UNSET and modes is not None:
        mode_list = _spec_names(modes, "modes")
        bad = [m for m in mode_list if m not in SNAP_KINDS]
        if bad:
            raise _fail("unknown_kind", "unknown snap kind", kinds=bad)
    prio: list[str] | None = None
    if priority is not _UNSET and priority is not None:
        prio = _spec_names(priority, "priority")
        # 为什么不再要求"恰好把 SNAP_KINDS 列全"：捕捉类型集是会长的（引擎 2026-09-30 起 SNAP_KINDS
        # 已含新增的 `point`，见 cadcore/precision/osnap.py 的注释）。要求列全 ⇒ 类型集一扩充，既有
        # 调用方（含对标门按旧 7 类写死的夹具）集体 `bad_arg`，尺子量到的是"枚举过期"而不是"设置写
        # 不进去"。因此这里按**部分顺序**收：重复 / 未知 / 空列表照旧拒，且仍先校验后落盘（零副作用）。
        if not prio or len(set(prio)) != len(prio):
            raise _bad("priority", priority)
        unknown = [k for k in prio if k not in SNAP_KINDS]
        if unknown:
            raise _fail("unknown_kind", "unknown snap kind", kinds=unknown)
    if ignore_hatch is not _UNSET and not isinstance(ignore_hatch, bool):
        raise _bad("ignore_hatch", ignore_hatch)
    cfg = node.get("osnap")
    if not isinstance(cfg, _MutableMappingABC):
        cfg = {"enabled": True, "modes": list(SNAP_KINDS)}
        node["osnap"] = cfg
    if enabled is not _UNSET:
        cfg["enabled"] = bool(enabled)
    if mode_list is not None:
        cfg["modes"] = mode_list
    if prio is not None:
        cfg["priority"] = prio
    if ignore_hatch is not _UNSET:
        cfg["ignore_hatch"] = bool(ignore_hatch)
    return [str(node.get("uuid", ""))]


# ---- item 2/3/4/5/6/8/9：捕捉解析 ---------------------------------------------------


def osnap_resolve(sk_or_doc: Any, *, point_nm: Any, tolerance_nm: Any,
                  kinds: Any = None, one_shot: bool = False) -> list[str]:
    """按持久化设置（或 ``kinds`` 覆盖）解析一次捕捉（spec item 2/3/4/5/6/8/9）。

    - 返回**被锚定实体**的 uuid 列表；无命中/开关关掉/``kinds=[]`` ⇒ ``[]``（真零），
      并在结果通道写 ``reason``（``no_hit`` / ``osnap_disabled`` / ``no_such_entity_kind``）；
    - ``kinds=('insertion','node')`` ⇒ ``[]`` + ``reason='no_such_entity_kind'``（L1 无这类实体，不拿端点顶替）；
    - 未知名 ⇒ ``unknown_kind``；``one_shot=True`` 时忽略总开关，但**不写回设置**；
    - 引擎的 ValueError 一律转 ``CadError(reason='bad_arg')``。
    """
    point = _spec_pt(point_nm)
    tol = _spec_tol(tolerance_nm)
    if not isinstance(one_shot, bool):
        raise _bad("one_shot", one_shot)
    enabled, modes, _node = _drafting_cfg(sk_or_doc)
    ks = list(modes) if kinds is None else _spec_names(kinds, "kinds")
    if any(k in ("insertion", "node") for k in ks):
        _chan(sk_or_doc, "osnap_resolve", point_nm=None, kind=None, reason="no_such_entity_kind")
        return []
    bad = [k for k in ks if k not in SNAP_KINDS]
    if bad:
        raise _fail("unknown_kind", "unknown snap kind", kinds=bad)
    if not ks:
        return []
    if not enabled and not one_shot:
        _chan(sk_or_doc, "osnap_resolve", point_nm=None, kind=None, reason="osnap_disabled")
        return []
    try:
        hit = snap(sk_or_doc, point, kinds=tuple(ks), tolerance_nm=int(tol))
    except ValueError as exc:
        raise _bad("point_nm", point_nm) from exc
    if hit is None:
        _chan(sk_or_doc, "osnap_resolve", point_nm=None, kind=None, reason="no_hit")
        return []
    _chan(sk_or_doc, "osnap_resolve", point_nm=[int(hit.point_nm[0]), int(hit.point_nm[1])],
          kind=str(hit.kind), ref=list(hit.ref), distance_nm=int(hit.distance_nm))
    return _anchored(sk_or_doc, hit.ref)


def osnap_apparent(sk_or_doc: Any, *, a_ref: Any, b_ref: Any, tolerance_nm: Any) -> list[str]:
    """外观交点：两条**直线**延长线的虚拟交点（spec item 5），返回两条线的 uuid。

    非 line×line ⇒ ``unsupported_kind``；平行（无交点）或交点圆整后超出容差 ⇒ ``[]``（真零）。
    """
    tol = _spec_tol(tolerance_nm)
    ents = {str(_get(e, "uuid", "")): e for e in _entities(sk_or_doc)}
    a, b = ents.get(str(a_ref)), ents.get(str(b_ref))
    if a is None or b is None:
        raise _fail("unknown_reference", "unknown entity reference", a=repr(a_ref), b=repr(b_ref))
    ka, kb = str(_get(a, "kind", "")), str(_get(b, "kind", ""))
    if ka != "line" or kb != "line":
        raise _fail("unsupported_kind", "apparent intersection needs line x line", kinds=[ka, kb])
    ga, gb = _get(a, "geom", {}), _get(b, "geom", {})
    d1 = (int(ga["x2"]) - int(ga["x1"]), int(ga["y2"]) - int(ga["y1"]))
    d2 = (int(gb["x2"]) - int(gb["x1"]), int(gb["y2"]) - int(gb["y1"]))
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if den == 0:
        return []
    t = ((int(gb["x1"]) - int(ga["x1"])) * d2[1] - (int(gb["y1"]) - int(ga["y1"])) * d2[0]) / den
    fx, fy = int(ga["x1"]) + t * d1[0], int(ga["y1"]) + t * d1[1]
    px, py = _round_nm(fx), _round_nm(fy)
    if (fx - px) ** 2 + (fy - py) ** 2 > float(tol) ** 2:
        return []
    order = sorted([str(a_ref), str(b_ref)])
    _chan(sk_or_doc, "osnap_apparent", kind="apparent", point_nm=[px, py],
          ref=[order[0], "intersection", order[1], "intersection"])
    return order


def osnap_tangent(sk_or_doc: Any, *, from_nm: Any, tolerance_nm: Any) -> list[str]:
    """切点捕捉（spec item 6）：从 ``from_nm`` 向每个圆/弧作切线，返回被锚定实体 uuid。

    圆外 2 个切点、圆上 1 个；圆内无解 ⇒ 跳过该实体（真零，不是错误）；
    结果点同时落结果通道 ``points_nm``。
    """
    tol = _spec_tol(tolerance_nm)
    f = _spec_pt(from_nm, "from_nm")
    uuids: list[str] = []
    points: list[list[int]] = []
    for ent in _entities(sk_or_doc):
        if str(_get(ent, "kind", "")) not in ("circle", "arc"):
            continue
        g = _get(ent, "geom", {})
        cx, cy, r = int(g["cx"]), int(g["cy"]), int(g["r"])
        dx, dy = f[0] - cx, f[1] - cy
        d2 = dx * dx + dy * dy
        if d2 < r * r:
            continue
        if d2 == r * r:
            points.append([f[0], f[1]])
        else:
            d = math.sqrt(float(d2))
            base_ang = math.atan2(float(dy), float(dx))
            th = math.acos(max(-1.0, min(1.0, float(r) / d)))
            for ang in (base_ang + th, base_ang - th):
                points.append([_round_nm(cx + r * math.cos(ang)), _round_nm(cy + r * math.sin(ang))])
        uuids.append(str(_get(ent, "uuid", "")))
    if not uuids:
        return []
    _chan(sk_or_doc, "osnap_tangent", kind="tangent", point_nm=list(points[0]),
          points_nm=[list(p) for p in points], ref=[uuids[0], "tangent"])
    return uuids


def osnap_extension(sk_or_doc: Any, *, point_nm: Any, tolerance_nm: Any) -> list[str]:
    """延长线捕捉（spec item 7）：只往悬停端**外侧**延长，容差内最近者胜；无候选 ⇒ ``[]``。"""
    p = _spec_pt(point_nm)
    tol = float(_spec_tol(tolerance_nm))
    best: tuple[float, str, tuple[float, float]] | None = None
    for ent in _entities(sk_or_doc):
        if str(_get(ent, "kind", "")) != "line":
            continue
        g = _get(ent, "geom", {})
        ends = ((int(g["x2"]), int(g["y2"]), int(g["x1"]), int(g["y1"])),
                (int(g["x1"]), int(g["y1"]), int(g["x2"]), int(g["y2"])))
        for ex, ey, ix, iy in ends:
            vx, vy = ex - ix, ey - iy
            l2 = float(vx * vx + vy * vy)
            if l2 == 0.0:
                continue
            t = ((p[0] - ex) * vx + (p[1] - ey) * vy) / l2
            if t < 0.0:
                continue
            fx, fy = ex + t * vx, ey + t * vy
            dist = math.hypot(float(p[0] - fx), float(p[1] - fy))
            if dist <= tol and (best is None or dist < best[0]):
                best = (dist, str(_get(ent, "uuid", "")), (fx, fy))
    if best is None:
        return []
    _chan(sk_or_doc, "osnap_extension", kind="extension",
          point_nm=[_round_nm(best[2][0]), _round_nm(best[2][1])])
    return [best[1]]


def osnap_parallel(sk_or_doc: Any, *, base_nm: Any, point_nm: Any, ref: Any,
                   tolerance_deg: Any = 3.0) -> list[str]:
    """平行捕捉（spec item 7）：``ref`` 是 line/arc，base→point 方向与参考方向夹角在容差内则投影。

    返回 ``[ref_uuid]``；``ref`` 不是 line/arc ⇒ ``unsupported_kind``；容差不在 ``(0,90]`` ⇒ ``bad_arg``。
    """
    base = _spec_pt(base_nm, "base_nm")
    p = _spec_pt(point_nm)
    tdeg = _spec_deg(tolerance_deg, "tolerance_deg", positive=True)
    if tdeg > 90.0:
        raise _bad("tolerance_deg", tolerance_deg)
    ent = None
    for cand in _entities(sk_or_doc):
        if str(_get(cand, "uuid", "")) == str(ref):
            ent = cand
            break
    if ent is None:
        raise _fail("unknown_reference", "unknown ref", ref=repr(ref))
    kind = str(_get(ent, "kind", ""))
    if kind not in ("line", "arc"):
        raise _fail("unsupported_kind", "parallel needs a line/arc ref", kind=kind)
    g = _get(ent, "geom", {})
    if kind == "line":
        dir_ang = math.atan2(float(int(g["y2"]) - int(g["y1"])), float(int(g["x2"]) - int(g["x1"])))
    else:
        dir_ang = math.radians(float(g.get("start_deg", 0.0)))
    vx, vy = p[0] - base[0], p[1] - base[1]
    radius = math.hypot(float(vx), float(vy))
    if radius == 0.0:
        return []
    diff = abs((math.degrees(math.atan2(float(vy), float(vx))) - math.degrees(dir_ang)
                + 180.0) % 360.0 - 180.0)
    if diff > tdeg:
        return []
    px = base[0] + _round_nm(radius * math.cos(dir_ang))
    py = base[1] + _round_nm(radius * math.sin(dir_ang))
    _chan(sk_or_doc, "osnap_parallel", kind="parallel", point_nm=[px, py],
          angle_deg=math.degrees(dir_ang))
    return [str(_get(ent, "uuid", ""))]


def osnap_feedback(sk_or_doc: Any, *, point_nm: Any, tolerance_nm: Any, kinds: Any = None) -> list[str]:
    """AutoSnap 捕捉反馈（spec item 11）：标记点 / 磁吸 / 工具提示键。

    命中 ⇒ ``marker_kind``、``marker_point_nm``、``magnet_applied=True``、``tooltip_key='osnap.<kind>'``；
    未命中 ⇒ ``[]`` 且 ``magnet_applied=False``（真零，不是错误）。``tooltip_key`` 是 i18n 键，**不回退成英文串**。
    """
    point = _spec_pt(point_nm)
    tol = _spec_tol(tolerance_nm)
    ks = list(SNAP_KINDS) if kinds is None else _spec_names(kinds, "kinds")
    bad = [k for k in ks if k not in SNAP_KINDS]
    if bad:
        raise _fail("unknown_kind", "unknown snap kind", kinds=bad)
    try:
        hit = snap(sk_or_doc, point, kinds=tuple(ks), tolerance_nm=int(tol))
    except ValueError as exc:
        raise _bad("point_nm", point_nm) from exc
    if hit is None:
        _chan(sk_or_doc, "osnap_feedback", marker_kind=None, marker_point_nm=None,
              magnet_applied=False, tooltip_key=None)
        return []
    _chan(sk_or_doc, "osnap_feedback", marker_kind=str(hit.kind),
          marker_point_nm=[int(hit.point_nm[0]), int(hit.point_nm[1])], magnet_applied=True,
          tooltip_key=f"osnap.{hit.kind}")
    return _anchored(sk_or_doc, hit.ref)


# ---- item 12/13/14：正交与极轴 -------------------------------------------------------


def _polar_on(node: Any) -> bool:
    """drafting.polar.enabled（缺 ⇒ False）。"""
    cfg = node.get("polar") if isinstance(node, _MappingABC) else None
    return bool(cfg.get("enabled")) if isinstance(cfg, _MappingABC) else False


def ortho_set(sk_or_doc: Any, *, enabled: Any = _UNSET, tolerance_deg: Any = _UNSET,
              order: Any = _UNSET) -> list[str]:
    """正交模式（spec item 12）**与**族内 precision 表版本（同形双面）。

    文档面：写 ``settings.drafting.ortho``，返回 ``[drafting_uuid]``；
    正交与极轴同时开启时必须先有 ``order ∈ {'ortho_first','polar_first'}``，否则 ``bad_arg``（不静默偏袒其一）。
    族内面：写 ``sk['precision']`` 的 ortho 记录，返回 ``[该记录 uuid]``。
    """
    if _is_drafting_target(sk_or_doc):
        node = _spec_node(sk_or_doc)
        if enabled is not _UNSET and not isinstance(enabled, bool):
            raise _bad("enabled", enabled)
        if order is not _UNSET and order not in _ORDER_VALUES:
            raise _bad("order", order)
        cfg = node.get("ortho")
        if not isinstance(cfg, _MutableMappingABC):
            cfg = {"enabled": False, "tolerance_deg": 3.0}
            node["ortho"] = cfg
        if order is not _UNSET:
            node["order"] = order
        if enabled is True and _polar_on(node) and node.get("order") not in _ORDER_VALUES:
            raise _bad("order", None)
        if enabled is not _UNSET:
            cfg["enabled"] = bool(enabled)
        if tolerance_deg is not _UNSET:
            cfg["tolerance_deg"] = _spec_deg(tolerance_deg, "tolerance_deg")
        return [str(node.get("uuid", ""))]
    return _legacy_ortho_set(sk_or_doc, enabled=enabled, tolerance_deg=tolerance_deg)


def ortho_lock(base_nm: Any, point_nm: Any, *, tolerance_deg: Any = 3.0) -> tuple[int, int]:
    """正交锁定（spec item 12，纯几何）：容差内把点锁到过基点的水平/垂直轴，返回整数纳米点。"""
    base = _spec_pt(base_nm, "base_nm")
    point = _spec_pt(point_nm)
    tol = _spec_deg(tolerance_deg, "tolerance_deg")
    try:
        locked = _ENGINE_ORTHO_LOCK(base, point, tolerance_deg=tol)
    except ValueError as exc:
        raise _bad("tolerance_deg", tolerance_deg) from exc
    return (int(locked[0]), int(locked[1]))


def polar_set(sk_or_doc: Any, *, enabled: Any = _UNSET, increments_deg: Any = _UNSET,
              tolerance_deg: Any = _UNSET, additional_deg: Any = _UNSET,
              measurement: Any = _UNSET) -> list[str]:
    """极轴追踪设置（spec item 13/14）**与**族内 precision 表版本（同形双面）。

    文档面：写 ``settings.drafting.polar``（``increments_deg`` ⊂ (0,360]、``additional_deg`` ⊂ [0,360)、
    ``measurement ∈ {'absolute','last_segment'}``），返回 ``[drafting_uuid]``；先校验后落盘。
    族内面：写 ``sk['precision']`` 的 polar 记录，返回 ``[该记录 uuid]``。
    """
    if _is_drafting_target(sk_or_doc):
        node = _spec_node(sk_or_doc)
        cfg = node.get("polar")
        if not isinstance(cfg, _MutableMappingABC):
            cfg = {"enabled": False, "increments_deg": [15.0, 30.0, 45.0, 90.0],
                   "additional_deg": [], "measurement": "absolute"}
            node["polar"] = cfg
        if enabled is not _UNSET and not isinstance(enabled, bool):
            raise _bad("enabled", enabled)
        incs: list[float] | None = None
        if increments_deg is not _UNSET and increments_deg is not None:
            incs = _increments(increments_deg, what="increments_deg")
        add: list[float] | None = None
        if additional_deg is not _UNSET and additional_deg is not None:
            add = [_deg(x, what="additional_deg", nonneg=False) for x in
                   _spec_names_float(additional_deg, "additional_deg")]
            if any(not (0.0 <= x < 360.0) for x in add):
                raise _bad("additional_deg", additional_deg)
        meas: str | None = None
        if measurement is not _UNSET and measurement is not None:
            if measurement not in ("absolute", "last_segment"):
                raise _bad("measurement", measurement)
            meas = str(measurement)
        if enabled is not _UNSET and enabled is True and _ortho_on(node) and node.get("order") not in _ORDER_VALUES:
            raise _bad("order", None)
        if enabled is not _UNSET:
            cfg["enabled"] = bool(enabled)
        if incs is not None:
            cfg["increments_deg"] = incs
        if add is not None:
            cfg["additional_deg"] = add
        if meas is not None:
            cfg["measurement"] = meas
        return [str(node.get("uuid", ""))]
    return _legacy_polar_set(sk_or_doc, enabled=enabled, increments_deg=increments_deg,
                             tolerance_deg=tolerance_deg)


def _ortho_on(node: Any) -> bool:
    """drafting.ortho.enabled（缺 ⇒ False）。"""
    cfg = node.get("ortho") if isinstance(node, _MappingABC) else None
    return bool(cfg.get("enabled")) if isinstance(cfg, _MappingABC) else False


def _spec_names_float(value: Any, what: str) -> list[Any]:
    """数值序列入参（str/bytes 不算序列）。"""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _SequenceABC):
        raise _bad(what, value)
    return list(value)


def polar_track(base_nm: Any, point_nm: Any, *, increments_deg: Any = (15, 30, 45, 90),
                tolerance_deg: Any = 3.0) -> tuple[int, int]:
    """极轴追踪（spec item 13，纯几何）：方向吸到增量角，**只改方向不改距离**。

    增量集合为空/含 ``<=0`` 或 ``>360`` ⇒ ``bad_arg``；引擎 ValueError 一律转 CadError。
    """
    base = _spec_pt(base_nm, "base_nm")
    point = _spec_pt(point_nm)
    incs = _increments(increments_deg, what="increments_deg")
    tol = _spec_deg(tolerance_deg, "tolerance_deg")
    try:
        out = _ENGINE_POLAR_TRACK(base, point, increments_deg=tuple(incs), tolerance_deg=tol)
    except ValueError as exc:
        raise _bad("increments_deg", increments_deg) from exc
    return (_round_nm(float(out[0])), _round_nm(float(out[1])))


# ---- item 15：对象捕捉追踪 -----------------------------------------------------------


def otrack_resolve(sk_or_doc: Any, *, bases_nm: Any, point_nm: Any,
                   tolerance_deg: Any = 3.0) -> list[str]:
    """对象捕捉追踪（spec item 15）：两条追踪路径（水平/垂直）的交点，需 ≥2 个基点。

    ``bases_nm`` 少于 2 个 ⇒ ``[]``（真零：单基准不构成追踪交点）；路径平行/超出角度容差 ⇒ ``[]``；
    命中时把交点写进结果通道 ``point_nm``；非整数纳米基点 ⇒ ``bad_arg``。
    """
    p = _spec_pt(point_nm)
    bases = [_spec_pt(b, "bases_nm") for b in _spec_bases(bases_nm)]
    tol = _spec_deg(tolerance_deg, "tolerance_deg")
    if len(bases) < 2:
        return []
    b0, b1 = bases[0], bases[1]
    cand = (b1[0], b0[1])
    for base in (b0, b1):
        vx, vy = p[0] - base[0], p[1] - base[1]
        if vx == 0 and vy == 0:
            return []
        ang_p = math.degrees(math.atan2(float(vy), float(vx)))
        wx, wy = cand[0] - base[0], cand[1] - base[1]
        if wx * vx + wy * vy < 0:
            return []
        ang_c = math.degrees(math.atan2(float(wy), float(wx)))
        if abs((ang_c - ang_p + 180.0) % 360.0 - 180.0) > tol:
            return []
    _chan(sk_or_doc, "otrack_resolve", kind="otrack", point_nm=[int(cand[0]), int(cand[1])])
    return []


def _spec_bases(value: Any) -> list[Any]:
    """基点序列入参（str/bytes 不算序列）。"""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _SequenceABC):
        raise _bad("bases_nm", value)
    return list(value)


# ---- item 16/17/18：栅格与极轴捕捉 ---------------------------------------------------


#: **公开契约**：栅格显示记录的落点键 = ``doc.meta["precision_grid"]``。
#: 写者只有本模块的 ``grid_set``，读者只有 ``ops/features/precision_ops__w24.py``（其 ``GRID_KEY`` 同名同址）；
#: 两边共用一个桶，禁止任何一侧另立私有键（私有键会让公开 API 造不出读者要的上下文 ⇒ ``grid_not_configured``）。
GRID_DISPLAY_KEY = "precision_grid"

_GRID_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "spacing_nm": 10 * 1_000_000,
    "spacing_source": "user",
    "adaptive": False,
    "major_every": 5,
    "min_px": 24.0,
    "min_display_px": 1.0,
    "max_lines": 4096,
    "limits": None,
    "snap_spacing_nm": 0,
    "displayed": False,
    "too_dense": False,
    "last_view": None,
    "last_scale_px_per_mm": None,
    "lines": [],
    "line_count": 0,
}


def _grid_store(target: Any) -> Any:
    """公开栅格记录（``GRID_DISPLAY_KEY``）的承载体 —— **与读者同址**，逐条对齐读者 ``_store_of``。

    Document ⇒ ``.meta``；文档形映射 ⇒ 其 ``meta``（缺席则建）；裸映射 ⇒ 自身。
    """
    if target is None:
        raise _fail("unsupported_target", "expected a Document, a mapping or None", target="None")
    if isinstance(target, _MutableMappingABC):
        meta = target.get("meta")
        if isinstance(meta, _MutableMappingABC):
            return meta
        if "sketches" in target or "schema_version" in target:
            meta = target.setdefault("meta", {})
            if not isinstance(meta, _MutableMappingABC):
                raise _fail("unsupported_target", "meta must be a mapping", target="mapping")
            return meta
        return target
    meta = getattr(target, "meta", None)
    if isinstance(meta, _MutableMappingABC):
        return meta
    raise _fail("unsupported_target", "expected a Document, a mapping or None", target=type(target).__name__)


def _publish_grid_display(target: Any, *, enabled: Any = _UNSET, spacing_nm: Any = _UNSET,
                          snap_enabled: Any = _UNSET, major_every: Any = _UNSET) -> dict[str, Any]:
    """把公开 API 的栅格设置落进 ``doc.meta["precision_grid"]``（读者唯一读的那个公开桶）。

    首次调用即建**完整记录**（字段集与读者的 ``_DEFAULTS`` 同形），此后只就地改传进来的字段，
    记录 uuid 不随重复调用漂移；改了几何参数就清空线集（``line_count=0`` 即「需重算」）。
    """
    store = _grid_store(target)
    rec = store.get(GRID_DISPLAY_KEY)
    if rec is None:
        rec = dict(_GRID_DEFAULTS)
        rec["lines"] = []
        rec["uuid"] = str(new_uuid())
        store[GRID_DISPLAY_KEY] = rec
    elif not isinstance(rec, _MutableMappingABC):
        raise _fail("corrupt_record", "grid display record must be a mapping", key=GRID_DISPLAY_KEY)
    dirty = False
    if enabled is not _UNSET:
        rec["enabled"] = _bool(enabled, what="enabled")
        dirty = True
    if spacing_nm is not _UNSET:
        rec["spacing_nm"] = _nm(spacing_nm, what="spacing_nm", positive=True)
        rec["spacing_source"] = "user"
        dirty = True
    if major_every is not _UNSET and isinstance(major_every, int) and not isinstance(major_every, bool) \
            and major_every >= 1:
        rec["major_every"] = int(major_every)   # -1（未采）不落公开桶，保留读者缺省
        dirty = True
    if snap_enabled is not _UNSET:
        rec["snap_spacing_nm"] = int(rec.get("spacing_nm") or 0) if _bool(snap_enabled, what="snap_enabled") else 0
        dirty = True
    if dirty:
        rec["displayed"] = False
        rec["too_dense"] = False
        rec["lines"] = []
        rec["line_count"] = 0
    return rec


def grid_set(sk_or_doc: Any, *, visible: Any = _UNSET, enabled: Any = _UNSET,
             spacing_nm: Any = _UNSET, snap_enabled: Any = _UNSET,
             major_every: Any = _UNSET) -> list[str]:
    """栅格显示（spec item 16）**与**族内 precision 表版本（同形双面）。

    文档面：``visible``（旧名 ``enabled`` 兼容）+ ``spacing_nm > 0`` + ``major_every >= 1``
    写 ``settings.drafting.grid``，返回 ``[drafting_uuid]``；先校验后落盘。
    族内面：写 ``sk['precision']`` 的 grid 记录（含 ``snap_enabled`` / ``major_every=-1 未采``）。

    **公开契约**：无论走哪一面，本函数都把栅格显示配置落进 ``doc.meta["precision_grid"]``
    （键名 ``GRID_DISPLAY_KEY``）—— 这是栅格显示记录**唯一**的公开落点，也是
    ``ops/features/precision_ops__w24.py`` 唯一读的桶；调用方（UI/状态栏/命令行）只认这一个字段。
    上面那两处（``settings.drafting.grid`` / precision 表的 grid 模式）是各自的**私有**消费面，
    不构成第二份栅格显示配置：读者不读它们，调用方也不该读。
    """
    if _is_drafting_target(sk_or_doc):
        node = _spec_node(sk_or_doc)
        vis = visible if visible is not _UNSET else enabled
        if vis is _UNSET or not isinstance(vis, bool):
            raise _bad("visible", vis)
        sp = _spec_int(spacing_nm, "spacing_nm")
        if sp <= 0:
            raise _bad("spacing_nm", spacing_nm)
        if major_every is _UNSET:
            major = 5
        else:
            major = _spec_int(major_every, "major_every")
            if major < 1:
                raise _bad("major_every", major_every)
        node["grid"] = {"visible": bool(vis), "spacing_nm": sp, "major_every": major}
        _publish_grid_display(sk_or_doc, enabled=bool(vis), spacing_nm=sp, major_every=major)
        return [str(node.get("uuid", ""))]
    out = _legacy_grid_set(sk_or_doc, enabled=enabled, spacing_nm=spacing_nm,
                           snap_enabled=snap_enabled, major_every=major_every)
    _publish_grid_display(sk_or_doc, enabled=enabled, spacing_nm=spacing_nm,
                          snap_enabled=snap_enabled, major_every=major_every)
    return out


def snap_step(sk_or_doc: Any, *, enabled: Any, spacing_nm: Any) -> list[str]:
    """栅格捕捉（步进）开关（spec item 17）：写 ``settings.drafting.snap_step``，返回 ``[drafting_uuid]``。

    与栅格**显示**（``grid``）互不影响：改显示不动步进，反之亦然。
    """
    node = _spec_node(sk_or_doc)
    if not isinstance(enabled, bool):
        raise _bad("enabled", enabled)
    sp = _spec_int(spacing_nm, "spacing_nm")
    if sp <= 0:
        raise _bad("spacing_nm", spacing_nm)
    node["snap_step"] = {"enabled": bool(enabled), "spacing_nm": sp}
    return [str(node.get("uuid", ""))]


def grid_snap(point_nm: Any, *, spacing_nm: Any) -> tuple[int, int]:
    """栅格步进（spec item 17，纯几何）：吸到 ``spacing_nm`` 的整数倍（半格向外）。

    ``spacing_nm`` 必须正整数纳米（0/负数/浮点 ⇒ ``bad_arg``）；引擎 ValueError 转 CadError。
    """
    point = _spec_pt(point_nm)
    sp = _spec_int(spacing_nm, "spacing_nm")
    if sp <= 0:
        raise _bad("spacing_nm", spacing_nm)
    try:
        out = _ENGINE_GRID_SNAP(point, spacing_nm=int(sp))
    except ValueError as exc:
        raise _bad("spacing_nm", spacing_nm) from exc
    return (_round_nm(float(out[0])), _round_nm(float(out[1])))


def polar_snap(base_nm: Any, point_nm: Any, *, increments_deg: Any = (15, 30, 45, 90),
               step_nm: Any, tolerance_deg: Any = 3.0) -> tuple[int, int]:
    """极轴捕捉 PolarSnap（spec item 18）：先吸方向，再沿该方向按 ``step_nm`` 步进取整。

    方向未命中 ⇒ 原样返回（不是错误）；``step_nm <= 0`` ⇒ ``bad_arg``。
    """
    base = _spec_pt(base_nm, "base_nm")
    point = _spec_pt(point_nm)
    step = _spec_int(step_nm, "step_nm")
    if step <= 0:
        raise _bad("step_nm", step_nm)
    tracked = polar_track(base, point, increments_deg=increments_deg, tolerance_deg=tolerance_deg)
    if tracked == point:
        return point
    vx, vy = tracked[0] - base[0], tracked[1] - base[1]
    radius = math.hypot(float(vx), float(vy))
    if radius == 0.0:
        return point
    scaled = _round_nm(radius / float(step)) * step
    return (base[0] + _round_nm(scaled * (vx / radius)), base[1] + _round_nm(scaled * (vy / radius)))


# ---- item 19/20：动态输入 / 坐标输入 -------------------------------------------------


def dyn_input_parse(sk_or_doc: Any, *, text: Any, base_nm: Any = None,
                    mode: Any = "relative") -> list[str]:
    """动态输入解析（spec item 19）：``@10<30`` / ``10,5`` / ``@10,5``（缺省单位 mm）。

    ``mode ∈ {'absolute','relative','direct'}``；relative/direct 缺 ``base_nm`` ⇒ ``bad_arg``；
    解析结果写结果通道 ``point_nm``；返回 ``[]``（本操作不锚定几何）。
    """
    if mode not in ("absolute", "relative", "direct"):
        raise _bad("mode", mode)
    if not isinstance(text, str) or not text.strip():
        raise _bad("text", text)
    body = text.strip()
    if body.startswith("@"):
        body = body[1:]
    base = None if base_nm is None else _spec_pt(base_nm, "base_nm")
    if mode != "absolute" and base is None:
        raise _bad("base_nm", base_nm)
    if "<" in body:
        ln, _, ang = body.partition("<")
        radius = _round_nm(_spec_num(ln, "text") * 1_000_000.0)
        theta = math.radians(_spec_num(ang, "text"))
        if base is None:
            raise _bad("base_nm", base_nm)
        point = (base[0] + _round_nm(radius * math.cos(theta)),
                 base[1] + _round_nm(radius * math.sin(theta)))
    elif "," in body:
        parts = body.split(",")
        if len(parts) != 2:
            raise _bad("text", text)
        dx = _round_nm(_spec_num(parts[0], "text") * 1_000_000.0)
        dy = _round_nm(_spec_num(parts[1], "text") * 1_000_000.0)
        point = (dx, dy) if base is None else (base[0] + dx, base[1] + dy)
    else:
        raise _bad("text", text)
    _chan(sk_or_doc, "dyn_input_parse", point_nm=[point[0], point[1]], mode=str(mode))
    return []


def coord_input_parse(sk_or_doc: Any, *, text: Any, base_nm: Any = None,
                      unit: Any = "mm") -> list[str]:
    """坐标输入解析（spec item 20）：``10,5``（绝对）/ ``@10,5``（相对，需基点），单位见 ``_UNIT_SCALE_NM``。

    未知单位/分量数不为 2/相对缺基点 ⇒ ``bad_arg``；结果写结果通道 ``point_nm``；返回 ``[]``。
    """
    if not isinstance(text, str) or not text.strip():
        raise _bad("text", text)
    scale = _UNIT_SCALE_NM.get(str(unit))
    if scale is None:
        raise _bad("unit", unit)
    body = text.strip()
    rel = body.startswith("@")
    if rel:
        body = body[1:]
        if base_nm is None:
            raise _bad("base_nm", base_nm)
    base = None if base_nm is None else _spec_pt(base_nm, "base_nm")
    parts = body.split(",")
    if len(parts) != 2:
        raise _bad("text", text)
    dx = _round_nm(_spec_num(parts[0], "text") * float(scale))
    dy = _round_nm(_spec_num(parts[1], "text") * float(scale))
    point = (base[0] + dx, base[1] + dy) if (rel and base is not None) else (dx, dy)
    _chan(sk_or_doc, "coord_input_parse", point_nm=[point[0], point[1]], unit=str(unit))
    return []


# ---- item 21：状态栏开关 -------------------------------------------------------------


def drafting_status(sk_or_doc: Any) -> list[str]:
    """状态栏快照（spec item 21）：七个键一个不少地进结果通道 ``drafting``，返回 ``[drafting_uuid]``。"""
    node = _spec_node(sk_or_doc)
    _chan(sk_or_doc, "drafting_status", drafting={key: node.get(key, None) for key in DRAFTING_KEYS})
    return [str(node.get("uuid", ""))]


def drafting_set(sk_or_doc: Any, *, key: Any, value: Any) -> list[str]:
    """状态栏开关键（spec item 21）：``key`` 必须是七个键之一、``value`` 必须是 bool，否则 ``bad_arg``。"""
    node = _spec_node(sk_or_doc)
    if key not in DRAFTING_KEYS:
        raise _bad("key", key)
    if not isinstance(value, bool):
        raise _bad("value", value)
    cur = node.get(key)
    if isinstance(cur, _MutableMappingABC):
        cur["enabled"] = bool(value)
    else:
        node[key] = {"enabled": bool(value)}
    return [str(node.get("uuid", ""))]


# ======================================================================================
# 操作表（冻结接口之二）
# ======================================================================================

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 族内面（precision 容器；返回记录 uuid）
    "snap_enable": snap_enable,
    "snap_kinds_set": snap_kinds_set,
    "snap_priority_set": snap_priority_set,
    "snap_tolerance_set": snap_tolerance_set,
    "snap_probe": snap_probe,
    "otrack_set": otrack_set,
    "dyninput_set": dyninput_set,
    "precision_apply": precision_apply,
    "precision_filter": precision_filter,
    "precision_reset": precision_reset,
    # spec 面（settings.drafting + _ops 结果通道）
    "osnap_set": osnap_set,
    "osnap_resolve": osnap_resolve,
    "osnap_apparent": osnap_apparent,
    "osnap_tangent": osnap_tangent,
    "osnap_extension": osnap_extension,
    "osnap_parallel": osnap_parallel,
    "osnap_feedback": osnap_feedback,
    "ortho_set": ortho_set,
    "ortho_lock": ortho_lock,
    "polar_set": polar_set,
    "polar_track": polar_track,
    "otrack_resolve": otrack_resolve,
    "grid_set": grid_set,
    "snap_step": snap_step,
    "grid_snap": grid_snap,
    "polar_snap": polar_snap,
    "dyn_input_parse": dyn_input_parse,
    "coord_input_parse": coord_input_parse,
    "drafting_status": drafting_status,
    "drafting_set": drafting_set,
}


# ======================================================================================
# 自检（python3 cadcore/ops/precision_ops.py 或 python3 -m cadcore.ops.precision_ops）
# ======================================================================================


def _expect_error(call: Callable[[], Any], reason: str, *, label: str) -> None:
    """断言"这个调用必须以这个 reason 报 CadError"（阳性对照：异常类型与 reason 都要对）。"""
    try:
        call()
    except CadError as exc:
        assert exc.reason == reason, f"{label}: expected reason={reason!r}, got {exc.reason!r}"
        return
    raise AssertionError(f"{label}: expected CadError(reason={reason!r}), but the call succeeded")


def _line(uuid: str, x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
    """自检夹具：一条 L1 线段。"""
    return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


def _circle(uuid: str, cx: int, cy: int, r: int) -> dict[str, Any]:
    """自检夹具：一个 L1 圆。"""
    return {"uuid": uuid, "kind": "circle", "layer": "0", "construction": False,
            "geom": {"cx": cx, "cy": cy, "r": r}}


def main(argv: Any = None) -> int:
    """自检：5 组断言（捕捉开关与优先级 / 正交与极轴 / 栅格步进 / 追踪与动态输入 / 文档往返与复位）。"""
    sketch = {"entities": [_line(new_uuid(), 0, 0, 1000, 0), _circle(new_uuid(), 500, 0, 100)],
              "constraints": [], "dimensions": []}

    # ---- 1. 捕捉开关与优先级 --------------------------------------------------------
    on = snap_kinds_set(sketch, kinds=["endpoint", "nearest"])
    assert len(on) == 2, "two snap kinds enabled"
    assert snap_kinds_set(sketch, kinds=["nearest", "endpoint"]) == on, "kinds order is not priority order"
    order = snap_priority_set(sketch, priority=["nearest"] + [k for k in SNAP_KINDS if k != "nearest"])
    assert len(order) == len(SNAP_KINDS), "priority covers every snap kind"
    assert order[0] == precision_filter(sketch, kind="nearest")[0], "the 'nearest' record moved to the head"
    _expect_error(lambda: snap_priority_set(sketch, priority=["endpoint"]), "incomplete_priority",
                  label="short priority")
    _expect_error(lambda: snap_priority_set(sketch, priority=["endpoint"] * len(SNAP_KINDS)),
                  "incomplete_priority", label="duplicate priority")
    _expect_error(lambda: snap_kinds_set(sketch, kinds=["tangent"]), "unknown_snap_kind", label="unknown kind")
    _expect_error(lambda: snap_kinds_set(sketch, kinds="endpoint"), "invalid_argument", label="bare string kinds")
    _expect_error(lambda: precision_filter(sketch, name="grid", kind="endpoint"), "invalid_argument",
                  label="name and kind together")
    off = snap_enable(sketch, enabled=False)
    assert precision_filter(sketch, name="osnap", enabled=False) == off, "master switch is persisted"
    assert snap_probe(sketch, point_nm=(1000, 5)) == [], "master off means no probe hit"
    snap_enable(sketch, enabled=True)
    snap_priority_set(sketch, priority=list(SNAP_KINDS))
    assert snap_probe(sketch, point_nm=(1000, 5)) == precision_filter(sketch, kind="endpoint"), \
        "endpoint wins at the line end"
    assert snap_probe(sketch, point_nm=(50_000_000, 50_000_000)) == [], "far away is no hit, not a fake hit"
    _expect_error(lambda: snap_tolerance_set(sketch, tolerance_nm=-1), "out_of_range", label="negative tolerance")

    # ---- 2. 正交 / 极轴（持久化 + 正交优先 + 读数口径） -------------------------------
    snap_enable(sketch, enabled=False)
    u_ortho = ortho_set(sketch, enabled=True, tolerance_deg=3.0)[0]
    assert precision_filter(sketch, name="ortho", enabled=True) == [u_ortho], "ortho switch is persisted"
    u_polar = polar_set(sketch, enabled=True, increments_deg=[45.0], tolerance_deg=3.0)[0]
    assert precision_filter(sketch, name="polar") == [u_polar], "polar record is reachable by name"
    out = precision_apply(sketch, point_nm=(1000, 20), base_nm=(0, 0))
    readout = cursor_readout(sketch)
    assert readout is not None and readout["uuid"] == out[0], "cursor record is the first returned uuid"
    assert readout["point_nm"] == [1000, 0], "ortho locks the off-axis coordinate"
    assert out[1] == u_ortho, "ortho record reported as fired"
    assert readout["distance_nm"] == 1000, "locking the axis keeps the projected distance"
    assert readout["angle_udeg"] == 0, "zero microdegrees is a real zero on the +x axis"

    # ---- 3. 栅格步进持久化 ----------------------------------------------------------
    u_grid = grid_set(sketch, enabled=True, spacing_nm=10_000_000, snap_enabled=True, major_every=-1)[0]
    assert precision_filter(sketch, name="grid", enabled=True) == [u_grid], "grid switch is persisted"
    _expect_error(lambda: grid_set(sketch, spacing_nm=0), "invalid_length", label="zero spacing")
    _expect_error(lambda: grid_set(sketch, major_every=0), "out_of_range", label="major_every=0")
    out = precision_apply(sketch, point_nm=(12_000_000, 4_000_000))
    readout = cursor_readout(sketch)
    assert readout["point_nm"] == [10_000_000, 0], "grid step snaps to the nearest multiple"
    assert out[1] == u_grid, "grid record reported as fired"
    assert "distance_nm" not in readout and "angle_udeg" not in readout, "NA: no base point, no relative keys"
    grid_set(sketch, enabled=False)

    # ---- 4. 捕捉追踪 + 动态输入 -----------------------------------------------------
    u_otrack = otrack_set(sketch, enabled=True, points_nm=[(3_000_000, 7_000_000)],
                          tolerance_nm=1_000_000)[0]
    out = precision_apply(sketch, point_nm=(3_400_000, 9_000_000), base_nm=(0, 0))
    readout = cursor_readout(sketch)
    assert readout["point_nm"] == [3_000_000, 9_000_000], "tracking aligns to the vertical path"
    assert out[1] == u_otrack, "otrack record reported as fired"
    # 角度是**微度**（UDEG_PER_DEG = 1e6，真源 sketch/constraints.deg_to_udeg）：
    # 结果点 (3e6, 9e6) 相对基点 (0,0) 是 71.565° ≈ 71_565_000 微度；旧断言按 1e-4 度尺度写，错 100 倍
    expected_udeg = deg_to_udeg(math.degrees(math.atan2(9_000_000, 3_000_000)))
    assert readout["distance_nm"] > 9_000_000 and abs(readout["angle_udeg"] - expected_udeg) <= 1_000, \
        "distance and angle survive the tracking step"
    assert readout["fields"] == ["pointer", "dimension", "dynamic_prompt"], "dynamic input defaults"
    u_dyn = dyninput_set(sketch, enabled=False)[0]
    assert precision_filter(sketch, name="dyninput", enabled=False) == [u_dyn], "dyninput switch is persisted"
    precision_apply(sketch, point_nm=(3_400_000, 9_000_000), base_nm=(0, 0))
    assert cursor_readout(sketch)["fields"] == [], "dynamic input off means no readout fields"
    dyninput_set(sketch, enabled=True, fields=["pointer"])
    _expect_error(lambda: dyninput_set(sketch, fields=["nope"]), "unknown_field", label="unknown dyn field")
    precision_apply(sketch, point_nm=(0, 0), base_nm=(0, 0))
    readout = cursor_readout(sketch)
    assert readout["distance_nm"] == 0, "coincident points are a true zero distance"
    assert "angle_udeg" not in readout, "NA: no direction when the point coincides with the base"
    assert readout["fields"] == ["pointer"], "readout reflects the persisted fields"

    # ---- 5. 文档目标：L1 不变量 + 往返 + 复位 ----------------------------------------
    from .. import docmodel  # 自检专用（族模块本身不依赖文档层）

    doc = docmodel.new_document()
    doc.sketches.append({"uuid": new_uuid(), "plane": "XY", "entities": [], "constraints": [],
                         "dimensions": []})
    u_doc = grid_set(doc, enabled=True, spacing_nm=1_000_000, snap_enabled=True, major_every=5)[0]
    assert precision_filter(doc, name="grid", enabled=True) == [u_doc], "document target keeps its own table"
    ortho_set(doc, enabled=True, tolerance_deg=2.0)
    assert docmodel.check_invariants(doc) == [], "precision store must not break L1 invariants"
    again = docmodel.document_from_l1(docmodel.document_to_l1(doc))
    record = [r for r in again.extra["precision"]["modes"] if r["name"] == "grid"][0]
    assert record["config"]["spacing_nm"] == 1_000_000 and record["config"]["major_every"] == 5, \
        "grid settings survive the L1 round trip"
    doc.sketches.append({"uuid": new_uuid(), "plane": "XY", "entities": [], "constraints": [],
                         "dimensions": []})
    _expect_error(lambda: snap_probe(doc, point_nm=(0, 0)), "ambiguous_target", label="multi-sketch document")
    fresh = precision_reset(doc)
    assert len(fresh) == len(MODE_NAMES) + len(SNAP_KINDS), "reset returns every record uuid"
    assert precision_filter(doc, name="grid", enabled=True) == [], "reset turns optional aids off"
    assert precision_filter(doc, name="osnap", enabled=True) != [], "reset restores the osnap default"

    print(f"PRECISION_OPS_OPS verdict=PASS ops={len(OPERATIONS)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())


# ======================================================================================
# spec 面收尾：族 id 与极轴增量入参口径（同名定义见文件前部，本块是最终生效值）
# ======================================================================================

#: 族 id（冻结）：spec/precision_ops.json 的 FAMILY 值。
FAMILY = "precision_ops"


def _increments(value: Any, *, what: str = "increments_deg") -> list[float]:
    """极轴增量集合 → (0, 360] 内的浮点度列表；坏入参一律 CadError(reason='bad_arg')。"""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _SequenceABC) or len(value) == 0:
        raise _bad(what, value)
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise _bad(what, item)
        deg = float(item)
        if not math.isfinite(deg) or deg <= 0.0 or deg > 360.0:
            raise _bad(what, item)
        if deg not in out:
            out.append(deg)
    return out
