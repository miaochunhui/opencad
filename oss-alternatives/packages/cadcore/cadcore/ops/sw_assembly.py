#!/usr/bin/env python3
"""cadcore.ops.sw_assembly —— 装配族模块：插入零部件 / 配合 / 移动旋转 / 干涉检查(粗) / 爆炸视图数据。

一句话用途
----------
把"多零部件装到一起"这件事做成 L1 文档上的**可复用表**：零部件实例（位姿 + 局部包围盒 + 参考几何）、
装配配合（重合 / 平行 / 垂直 / 同轴 / 距离 / 角度）、整体移动与绕轴旋转、**真体积交**干涉检查
（组件当凸实体求交，出相交体积 nm³；`method="aabb"` 保留历史粗检查口径）、爆炸视图数据（逐件位移向量）。
这是 `cadcore.ops.<module>` 家族模块之一。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_assembly"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回本次操作**产出/命中/受影响的 uuid 列表**
  （每个操作各自在 docstring 里写清"返回的是谁的 uuid、顺序如何"）；
- 长度一律**整数纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

落盘形态（装配表放哪）
----------------------
- 目标是**文档**（`cadcore.docmodel.Document` 或同形映射，判定 = 有 `sketches` 列表）⇒ 表放
  ``doc.extra["sw_assembly"]``（A5 的 "未知顶层字段可往返" 容器，不动 L1 冻结字段）；
- 目标是**草图形态映射/对象**（无 `sketches`）⇒ 表放该对象自己的 ``sw_assembly`` 键。
- 表结构（**只加键不改键**）：``{"schema": 1, "components": [], "mates": [], "interferences": [],
  "explodes": []}``。
- 组件位姿的**唯一真源是 3×3 旋转矩阵 + 整数纳米平移**（`rotation` / `at_nm`）。欧拉角只是
  读写两端的人话表示（`rotation_deg` 入参、`component_pose()` 的输出），**不存回**，
  避免"矩阵与欧拉角两份真源各说各话"。

位姿与参考几何口径
------------------
- 组件局部坐标系：``point_world = R @ point_local + at_nm``；``dir_world = normalize(R @ dir_local)``。
- 每个组件自带 ``refs``：参考名 → ``{"point_nm": [x,y,z], "dir": [dx,dy,dz]}``。缺省始终有
  ``origin``（点 ``(0,0,0)``、方向 ``(0,0,1)``）；用户给的 refs 与缺省**合并**（同名用户赢）。
- ``bbox_nm = [[xmin,ymin,zmin],[xmax,ymax,zmax]]``（局部轴对齐盒，整数纳米）；可给 ``size_nm``
  等价写法（盒从局部原点起算）。**没给盒的组件记为"未采"**（facts 里 ``bbox_state=-1``），
  干涉检查对它记 ``status="unmeasured"`` 并**照样报出来**，绝不静默当"不干涉"。

三态口径（与 AGENTS.md E2.3 一致）
----------------------------------
- ``0`` = 真为零（例：真有 0 处碰撞、位移真的为 0）；
- ``-1`` = **未采/未测量**（例：``tolerance_nm=-1`` = 该配合没写容差，检查时用调用方缺省；
  ``measured_nm=-1`` = 量不出来）；
- ``"NA"`` / ``None`` = 不适用（例：平行配合没有"距离"这个量 ⇒ ``measured_nm=-1``、
  ``measured_deg`` 有值；纯距离配合的角度是 ``None``）。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
1. **本层不是约束求解器**：``mate_*`` 只**记录**配合（kind + 两个参考 + 目标值），
   ``mate_check()`` 用组件**当前位姿**现算残差并判"满足 / 违反 / 量不出"。
   "没求解"绝不写成"已满足"（``status="recorded"`` 是第三种状态，机读可见）。
2. **干涉检查出真体积**：组件当**凸实体**（局部包围盒 + 位姿）求交，``volume_nm3`` 是
   **精确相交体积**（顶点枚举 + 散度定理，纯标准库）；AABB 只当预筛与"容差内接触"的外近似。
   ``method="aabb"`` 保留历史粗检查口径（可对拍）。内核 `boolean("intersect", ...)` 若可用则同侧；
   本层不依赖它（nano 后端无 boolean 能力）。
3. **爆炸视图只出数据，不动位姿**：``exploded_view`` 写一条 ``explodes`` 记录（逐件位移向量
   与目标位置），**不改** ``at_nm``；要真的挪件请显式 `move_component`。
4. **每次调用只写自己那张表**：不碰 `sketches` / `features` / `annotations`，不碰别的族模块的表。
5. 复用而非重写：入参把关复用 `cadcore.edit.modify` 的 `_as_nm` / `_as_angle`，结构访问走
   `cadcore.sketch.constraints` 的 `_get` / `_set` / `new_uuid`，本文件不抄第二份。
6. 非 docstring 的字符串字面量一律 ASCII（`tools/cad/i18n_gate.py` 的口径）。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/sw_assembly.py
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/sw_assembly.py 的引导（见文件末 __main__）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    __package__ = "cadcore.ops"

import math
import re
import uuid as _uuid
from copy import deepcopy
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from ..edit.modify import _as_angle, _as_nm
from ..errors import CadError
from ..sketch.constraints import _get, _set, new_uuid

__all__ = [
    "FAMILY", "OPERATIONS", "MATE_KINDS", "NM_PER_MM", "INTERFERENCE_METHODS",
    "insert_component", "component_pose", "move_component", "rotate_component",
    "mate_add", "mate_coincident", "mate_parallel", "mate_perpendicular", "mate_concentric",
    "mate_distance", "mate_angle", "mate_delete", "mate_check",
    "interference_check", "exploded_view", "assembly_facts", "selftest",
    "component_pattern", "measure_distance",
]

#: 族 id（冻结接口）。
FAMILY = "sw_assembly"

#: 装配表在文档 `extra` / 草图对象上的键名。
_ASM_KEY = "sw_assembly"
#: 装配表自身的 schema 版本（只加不改；将来迁移从这里分叉）。
_ASM_SCHEMA = 1

NM_PER_MM = 1_000_000

#: 本层认识的六种配合（冻结枚举；未知 kind 报 `unknown_mate_kind`，不静默当重合）。
MATE_KINDS: tuple[str, ...] = ("coincident", "parallel", "perpendicular", "concentric",
                               "distance", "angle")

#: 缺省容差：长度 1 µm（整数纳米），角度 1e-6 度（几何量算出来的就是精确值，容差只吸浮点尾）。
DEFAULT_TOLERANCE_NM = 1_000
DEFAULT_TOLERANCE_DEG = 1e-6

#: 爆炸位移的缺省步长 10 mm（整数纳米）与缺省模式。
DEFAULT_EXPLODE_STEP_NM = 10 * NM_PER_MM
EXPLODE_MODES: tuple[str, ...] = ("radial", "axis")

#: 缺省参考几何：方向 (0,0,1)、点 (0,0,0)。每个组件都有它，配合可以直接引 `origin`。
_DEFAULT_REF_NAME = "origin"
_DEFAULT_REF: dict[str, Any] = {"point_nm": [0, 0, 0], "dir": [0.0, 0.0, 1.0]}

#: 未采哨兵：`-1` = 未测/未采（与全仓三态口径一致，别用 0 冒充）。
UNMEASURED = -1
#: 不适用哨兵。
NA = "NA"

_HEX_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# ======================================================================================
# 错误与入参把关
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(detail, reason=reason, **details)


def _rw(exc: CadError, *, what: str, value: Any, fallback: str) -> CadError:
    """把 modify 校验器的错误换成基类 `CadError`（保留 reason/文案，不丢归因）。"""
    return _fail(str(exc.reason or fallback), str(exc.message), what=what, value=repr(value))


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（坏入参报 `invalid_length`）。"""
    try:
        return _as_nm(value, what=what, positive=positive)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_length") from exc


def _angle(value: Any, *, what: str = "angle_deg") -> float:
    """角度入参 → 有限浮点度（角度不是长度，允许实数）。"""
    try:
        return _as_angle(value, what=what)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_argument") from exc


def _number(value: Any, *, what: str, minimum: float | None = None) -> float:
    """实数入参 → 有限浮点（`bool` 不算数；低于下界报 `out_of_range`）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_argument", f"{what} must be a finite number", what=what, value=repr(value))
    number = float(value)
    if not math.isfinite(number):
        raise _fail("invalid_argument", f"{what} must be finite", what=what, value=repr(value))
    if minimum is not None and number < minimum:
        raise _fail("out_of_range", f"{what} must be >= {minimum}", what=what, value=number)
    return number


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


def _seq(value: Any, *, what: str, allow_none: bool = False) -> Any:
    """取一个 3 元序列（`None` 仅在 `allow_none` 时放行；字符串一律拒绝）。"""
    if value is None:
        if allow_none:
            return None
        raise _fail("invalid_argument", f"{what} must be a 3-sequence", what=what, value=repr(value))
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _fail("invalid_argument", f"{what} must be a 3-sequence", what=what, value=repr(value))
    return value


def _nm3(value: Any, *, what: str, allow_none: bool = False) -> list[int] | None:
    """3 维长度入参 → `[int nm, int nm, int nm]`（可为负，例如包围盒下界）。"""
    items = _seq(value, what=what, allow_none=allow_none)
    if items is None:
        return None
    return [_nm(items[index], what=f"{what}[{index}]") for index in range(3)]


def _num3(value: Any, *, what: str, allow_none: bool = False) -> list[float] | None:
    """3 维实数入参 → 3 元浮点列表。"""
    items = _seq(value, what=what, allow_none=allow_none)
    if items is None:
        return None
    return [_number(items[index], what=f"{what}[{index}]") for index in range(3)]


def _unit3(value: Any, *, what: str, default: Any = None) -> list[float] | None:
    """3 维方向入参 → 单位向量（零向量报 `degenerate_axis`，不静默换缺省）。"""
    items = _num3(value, what=what, allow_none=True)
    if items is None:
        if default is None:
            return None
        items = list(default)
    length = math.sqrt(sum(item * item for item in items))
    if length <= 1e-12:
        raise _fail("degenerate_axis", f"{what} must not be a zero vector", what=what,
                    value=[repr(item) for item in items])
    return [item / length for item in items]


def _int_count(value: Any, *, what: str, minimum: int = 1) -> int:
    """计数入参 → 严格 int（`bool` 拒绝）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", f"{what} must be an int", what=what, value=repr(value))
    if value < minimum:
        raise _fail("out_of_range", f"{what} must be >= {minimum}", what=what, value=int(value))
    return int(value)


# ======================================================================================
# 3 维向量 / 3×3 旋转矩阵（纯标准库；矩阵行主序，按列向量约定 `v' = R @ v`）
# ======================================================================================


def _sub3(a: Any, b: Any) -> list[float]:
    return [float(a[i]) - float(b[i]) for i in range(3)]


def _add3(a: Any, b: Any) -> list[float]:
    return [float(a[i]) + float(b[i]) for i in range(3)]


def _dot3(a: Any, b: Any) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross3(a: Any, b: Any) -> list[float]:
    return [_float(a[1]) * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _norm3(a: Any) -> float:
    return math.sqrt(_dot3(a, a))


def _float(value: Any) -> float:
    """浮点收口：吸收 `-0.0` 与 1e-15 级噪声，让"同一姿态"逐字节可比。"""
    number = float(value)
    if abs(number) < 1e-12:
        return 0.0
    return round(number, 12)


def _mat_identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _mat_apply(matrix: Any, vector: Any) -> list[float]:
    return [_float(matrix[row][0] * vector[0] + matrix[row][1] * vector[1] + matrix[row][2] * vector[2])
            for row in range(3)]


def _mat_mul(outer: Any, inner: Any) -> list[list[float]]:
    return [[_float(sum(outer[row][k] * inner[k][col] for k in range(3))) for col in range(3)]
            for row in range(3)]


def _rot_axis(unit: Any, angle_deg: float) -> list[list[float]]:
    """绕**过原点**的单位轴旋转 `angle_deg` 度（Rodrigues 公式，行主序）。"""
    x, y, z = (float(unit[0]), float(unit[1]), float(unit[2]))
    radians = math.radians(float(angle_deg))
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    one_c = 1.0 - cos_a
    return [
        [_float(cos_a + x * x * one_c), _float(x * y * one_c - z * sin_a), _float(x * z * one_c + y * sin_a)],
        [_float(y * x * one_c + z * sin_a), _float(cos_a + y * y * one_c), _float(y * z * one_c - x * sin_a)],
        [_float(z * x * one_c - y * sin_a), _float(z * y * one_c + x * sin_a), _float(cos_a + z * z * one_c)],
    ]


def _euler_to_mat(degrees: Any) -> list[list[float]]:
    """XYZ 欧拉角（度）→ 矩阵 `R = Rz @ Ry @ Rx`（与 `_euler_from_mat` 互逆）。"""
    rx, ry, rz = math.radians(float(degrees[0])), math.radians(float(degrees[1])), math.radians(float(degrees[2]))
    return _mat_mul(_rot_axis((0.0, 0.0, 1.0), math.degrees(rz)),
                    _mat_mul(_rot_axis((0.0, 1.0, 0.0), math.degrees(ry)),
                             _rot_axis((1.0, 0.0, 0.0), math.degrees(rx))))


def _euler_from_mat(matrix: Any) -> list[float]:
    """矩阵 → XYZ 欧拉角（度），`R = Rz @ Ry @ Rx` 分解；万向锁处 `rx` 取 0（可读性用，非真源）。"""
    sin_b = max(-1.0, min(1.0, -float(matrix[2][0])))
    ry = math.asin(sin_b)
    if abs(sin_b) > 1.0 - 1e-12:  # 万向锁：只有 (rx + rz) 可定
        return [_float(math.degrees(math.atan2(float(matrix[0][1]), float(matrix[1][1])))), _float(math.degrees(ry)), 0.0]
    rx = math.atan2(float(matrix[2][1]), float(matrix[2][2]))
    rz = math.atan2(float(matrix[1][0]), float(matrix[0][0]))
    return [_float(math.degrees(rx)), _float(math.degrees(ry)), _float(math.degrees(rz))]


def _angle_between(a: Any, b: Any) -> float:
    """两个非零向量的夹角（度，0..180）。"""
    length = _norm3(a) * _norm3(b)
    if length <= 1e-12:
        raise _fail("degenerate_axis", "angle needs two non-zero directions", a=list(a), b=list(b))
    cosine = max(-1.0, min(1.0, _dot3(a, b) / length))
    return math.degrees(math.acos(cosine))


def _point_line_distance(point: Any, origin: Any, unit: Any) -> float:
    """点到直线（过 `origin`、方向 `unit`）的垂距。"""
    rel = _sub3(point, origin)
    along = _dot3(rel, unit)
    return _norm3([rel[i] - along * unit[i] for i in range(3)])


# ======================================================================================
# 装配表定位与结构访问
# ======================================================================================


def _empty_store() -> dict[str, Any]:
    """空装配表（新表只长这里，别在调用点手搓键）。"""
    return {"schema": int(_ASM_SCHEMA), "components": [], "mates": [], "interferences": [],
            "explodes": []}


def _holder(target: Any) -> Any:
    """`sk_or_doc` → 能挂装配表的可变容器（文档走 `extra`；草图映射/对象走自身）。"""
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        extra = _get(target, "extra", None)
        if isinstance(extra, _MutableMappingABC):
            return extra
        if isinstance(target, _MutableMappingABC):
            return target
        raise _fail("bad_target", "document target has no mutable extra container",
                    value_type=type(target).__name__)
    if isinstance(target, (_MutableMappingABC,)) or target is not None and not isinstance(target, (str, bytes, int, float, bool)):
        return target
    raise _fail("bad_target", "sk_or_doc must be a document or a sketch-like mapping",
                value_type=type(target).__name__)


def _store(target: Any, *, create: bool = True) -> dict[str, Any] | None:
    """取（必要时创建）装配表；`create=False` 且没有表时返回 `None`（读侧不许写盘）。"""
    holder = _holder(target)
    value = _get(holder, _ASM_KEY, None)
    if isinstance(value, _MutableMappingABC):
        return value
    if not create:
        return None
    value = _empty_store()
    _set(holder, _ASM_KEY, value)
    return value


def _table(store: dict[str, Any], key: str) -> list[Any]:
    """取表（缺则建空表；类型坏了报 `corrupt_store`，不静默重置）。"""
    value = store.get(key)
    if value is None:
        value = []
        store[key] = value
    if not isinstance(value, list):
        raise _fail("corrupt_store", "assembly table is not a list", table=key,
                    value_type=type(value).__name__)
    return value


def _rows(store: dict[str, Any]) -> list[dict[str, Any]]:
    """逐行取出装配表（行不是映射 ⇒ `corrupt_store`）。"""
    out: list[dict[str, Any]] = []
    for key in ("components", "mates", "interferences", "explodes"):
        for row in _table(store, key):
            if not isinstance(row, _MutableMappingABC):
                raise _fail("corrupt_store", "assembly row is not a mapping", table=key,
                            value_type=type(row).__name__)
            out.append(row)
    return out


def _component(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 找组件（找不到报 `unknown_component`，不做下标引用）。"""
    key = _text(ref, what="component")
    for row in _table(store, "components"):
        if str(row.get("uuid")) == key:
            return row
    raise _fail("unknown_component", "no component with that uuid", component=key,
                known=[str(row.get("uuid")) for row in _table(store, "components")])


def _component_refs(value: Any) -> list[str]:
    """组件引用入参 → uuid 列表（单个字符串或序列都收；空序列报 `invalid_argument`）。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise _fail("invalid_argument", "components must be a uuid or a sequence of uuids",
                    value=repr(value))
    if not items:
        raise _fail("invalid_argument", "components must not be empty", value=repr(value))
    return [_text(item, what="component") for item in items]


def _ref_pair(value: Any) -> list[tuple[str, str]]:
    """配合参考入参 → `[(component_uuid, ref_name), (component_uuid, ref_name)]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_argument", "refs must be a pair of [component, ref] entries",
                    value=repr(value))
    out: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)) or len(item) != 2:
            raise _fail("invalid_argument", "each mate ref must be [component_uuid, ref_name]",
                        value=repr(item))
        out.append((_text(item[0], what="ref component"), _text(item[1], what="ref name")))
    return out


def _ref_geometry(component: dict[str, Any], name: str) -> dict[str, Any]:
    """组件上某个参考名的局部几何（找不到报 `unknown_reference`）。"""
    refs = component.get("refs")
    if not isinstance(refs, _MutableMappingABC) or name not in refs:
        raise _fail("unknown_reference", "component has no such named reference", name=name,
                    component=str(component.get("uuid")), known=sorted(refs) if isinstance(refs, _MutableMappingABC) else [])
    return refs[name]


def _world(component: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    """局部参考几何 → 世界形态 `{"point": [float×3], "dir": unit|None}`。"""
    rotation = component.get("rotation") or _mat_identity()
    at = [float(item) for item in component.get("at_nm", [0, 0, 0])]
    point = _add3(_mat_apply(rotation, [float(item) for item in geometry.get("point_nm", [0, 0, 0])]), at)
    direction = geometry.get("dir")
    if direction is None:
        return {"point": point, "dir": None}
    # 方向必须跟着姿态转：模块 docstring 写的口径是 ``dir_world = normalize(R @ dir_local)``。
    # 漏转会让"转过 90° 的轴"在世界系里还是原方向 ⇒ 平行 / 垂直 / 角度 / 同轴四种配合全算错
    # （族门 G20 实测：mate_parallel 转 90° 后仍报 satisfied）。落点是 `_world()`，不是各配合分支。
    return {"point": point,
            "dir": _unit3(_mat_apply(rotation, [_number(item, what="ref dir") for item in direction]),
                          what="ref dir")}


def _world_mate_ref(store: dict[str, Any], ref: tuple[str, str]) -> dict[str, Any]:
    """配合参考 → 世界几何（组件/参考名任一不存在都会显式报错）。"""
    component = _component(store, ref[0])
    return _world(component, _ref_geometry(component, ref[1]))


def _bbox_world(component: dict[str, Any]) -> dict[str, Any] | None:
    """局部包围盒 → 世界 AABB（8 角点外接，**偏保守**；没有盒 ⇒ `None` = 未采）。"""
    bbox = component.get("bbox_nm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 2:
        return None
    low, high = bbox[0], bbox[1]
    rotation = component.get("rotation") or _mat_identity()
    at = [float(item) for item in component.get("at_nm", [0, 0, 0])]
    corners = []
    for mask in range(8):
        local = [float(high[axis] if (mask >> axis) & 1 else low[axis]) for axis in range(3)]
        corners.append(_add3(_mat_apply(rotation, local), at))
    lo = [int(round(min(corner[axis] for corner in corners))) for axis in range(3)]
    hi = [int(round(max(corner[axis] for corner in corners))) for axis in range(3)]
    return {"min_nm": lo, "max_nm": hi}


def _assembly_center(store: dict[str, Any]) -> list[float] | None:
    """装配中心 = 所有"已测"组件世界 AABB 的并集盒中心；一件都没测 ⇒ `None`（未采）。"""
    boxes = [_bbox_world(row) for row in _table(store, "components")
             if not bool(row.get("suppressed"))]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    lo = [min(box["min_nm"][axis] for box in boxes) for axis in range(3)]
    hi = [max(box["max_nm"][axis] for box in boxes) for axis in range(3)]
    return [(lo[axis] + hi[axis]) / 2.0 for axis in range(3)]


def _target_key(target: Any) -> str:
    """目标身份（`meta.doc_uuid` 优先）—— 只用于事实面标注，不参与 uuid 生成。"""
    doc_uuid = _get(_get(target, "meta", None), "doc_uuid", None)
    return str(doc_uuid) if isinstance(doc_uuid, str) and doc_uuid.strip() else NA


# ======================================================================================
# 操作：插入零部件
# ======================================================================================


def _refs(value: Any) -> dict[str, Any]:
    """参考几何入参 → `{name: {"point_nm": [int×3], "dir": unit}}`（与缺省 `origin` 合并）。"""
    if value is None:
        return {}
    if not isinstance(value, _MutableMappingABC):
        raise _fail("invalid_argument", "refs must be a mapping of name -> geometry",
                    value=repr(value))
    out: dict[str, Any] = {}
    for name, geometry in value.items():
        key = _text(name, what="ref name")
        if not isinstance(geometry, _MutableMappingABC):
            raise _fail("invalid_argument", "each ref must be a mapping with point_nm/dir",
                        name=key, value=repr(geometry))
        point = _nm3(geometry.get("point_nm"), what=f"refs[{key}].point_nm", allow_none=True) or [0, 0, 0]
        direction = _unit3(geometry.get("dir"), what=f"refs[{key}].dir",
                           default=(0.0, 0.0, 1.0))
        out[key] = {"point_nm": [int(item) for item in point], "dir": direction}
    return out


def _bbox(value: Any, size: Any) -> list[list[int]] | None:
    """包围盒入参 → `[[min×3],[max×3]]`；`bbox_nm` 与 `size_nm` 只能给一个。"""
    if value is not None and size is not None:
        raise _fail("invalid_argument", "give either bbox_nm or size_nm, not both")
    if size is not None:
        sizes = _nm3(size, what="size_nm")
        if any(item < 0 for item in sizes):
            raise _fail("out_of_range", "size_nm must be non-negative", size_nm=list(sizes))
        return [[0, 0, 0], [int(item) for item in sizes]]
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_argument", "bbox_nm must be [[xmin,ymin,zmin],[xmax,ymax,zmax]]",
                    value=repr(value))
    low = _nm3(value[0], what="bbox_nm.min")
    high = _nm3(value[1], what="bbox_nm.max")
    for axis in range(3):
        if high[axis] < low[axis]:
            raise _fail("out_of_range", "bbox_nm max must be >= min on every axis", axis=axis,
                        min_nm=int(low[axis]), max_nm=int(high[axis]))
    return [[int(item) for item in low], [int(item) for item in high]]


def insert_component(sk_or_doc: Any, *, part_key: Any, at_nm: Any = (0, 0, 0),
                     rotation_deg: Any = (0.0, 0.0, 0.0), bbox_nm: Any = None, size_nm: Any = None,
                     refs: Any = None, count: int = 1, step_nm: Any = (0, 0, 0),
                     fixed: bool = False, source: Any = None) -> list[str]:
    """**插入零部件实例**：写 `components[]` 一行（位姿 + 可选局部包围盒 + 命名参考几何）。

    参数（长度整数纳米；角度浮点度）：

    * `part_key`：零件名（非空字符串；BOM/图纸侧按它配行，本层不解释语义）；
    * `at_nm`：世界平移；`rotation_deg`：XYZ 欧拉角 `R = Rz @ Ry @ Rx`（存成矩阵，见模块 docstring）；
    * `bbox_nm` / `size_nm`：二选一。都不给 ⇒ **未采**（`bbox_nm=None`，facts 记 `-1`，
      干涉检查对它记 `unmeasured`）；
    * `refs`：参考名 → `{"point_nm": […], "dir": […]}；与缺省 `origin` 合并（同名用户赢）；
    * `count` / `step_nm`：连续插 `count` 件，第 i 件位置 = `at_nm + i * step_nm`（真·多实例）；
    * `fixed`：固定件（`move_component` / `rotate_component` 默认拒动，`allow_fixed=True` 才放行）；
    * `source`：外部件路径/引用（缺省 `"NA"` = 不适用，纯内存件）。

    返回：本次新建的组件 uuid 列表（按插入顺序，长度 = `count`）。
    """
    store = _store(sk_or_doc)
    key = _text(part_key, what="part_key")
    base = _nm3(at_nm, what="at_nm")
    step = _nm3(step_nm, what="step_nm")
    times = _int_count(count, what="count")
    rotation = _euler_to_mat(_num3(rotation_deg, what="rotation_deg"))
    box = _bbox(bbox_nm, size_nm)
    named = dict(_refs(refs))
    named.setdefault(_DEFAULT_REF_NAME, {"point_nm": [0, 0, 0], "dir": [0.0, 0.0, 1.0]})
    if _flag(fixed, what="fixed"):
        pass
    out: list[str] = []
    for index in range(times):
        uuid = new_uuid()
        _table(store, "components").append({
            "uuid": uuid, "part_key": key,
            "at_nm": [int(base[axis] + index * step[axis]) for axis in range(3)],
            "rotation": [list(row) for row in rotation],
            "bbox_nm": None if box is None else [list(box[0]), list(box[1])],
            "refs": {name: {"point_nm": list(item["point_nm"]), "dir": list(item["dir"])}
                     for name, item in named.items()},
            "fixed": bool(fixed), "suppressed": False,
            "source": str(source) if isinstance(source, str) and source.strip() else NA,
        })
        out.append(uuid)
    return out


def component_pose(sk_or_doc: Any, *, component: Any) -> dict[str, Any]:
    """**查询**某组件位姿（不是 `OPERATIONS` 里的操作，返回 dict 而非 uuid 列表）。

    返回 `{"uuid","part_key","at_nm","rotation","euler_deg","bbox_world_nm","bbox_state",
    "fixed","suppressed","refs"}`；`bbox_state`：`0` = 已测、`-1` = 未采（`bbox_world_nm` 亦为
    `-1`，绝不拿零盒冒充）。文档没有装配表 ⇒ `CadError(reason="no_assembly")`。
    """
    store = _store(sk_or_doc, create=False)
    if store is None:
        raise _fail("no_assembly", "target has no assembly table")
    row = _component(store, component)
    box = _bbox_world(row)
    return {
        "uuid": str(row.get("uuid")), "part_key": str(row.get("part_key")),
        "at_nm": [int(item) for item in row.get("at_nm", [0, 0, 0])],
        "rotation": [list(line) for line in (row.get("rotation") or _mat_identity())],
        "euler_deg": _euler_from_mat(row.get("rotation") or _mat_identity()),
        "bbox_world_nm": UNMEASURED if box is None else [list(box["min_nm"]), list(box["max_nm"])],
        "bbox_state": UNMEASURED if box is None else 0,
        "fixed": bool(row.get("fixed")), "suppressed": bool(row.get("suppressed")),
        "refs": sorted(str(name) for name in (row.get("refs") or {})),
    }


def component_pattern(sk_or_doc: Any, *, refs: Any, count: int,
                      dx_nm: int = 0, dy_nm: int = 0, dz_nm: int = 0) -> list[str]:
    """Copy components at world-space integer offsets, excluding the original.

    count includes the original station. Geometry, rotation, references and metadata
    are copied; mates are not. Subassemblies require recursive instancing and are
    rejected rather than creating aliases to their existing children.
    """
    times = _int_count(count, what="count")
    step = _nm3((dx_nm, dy_nm, dz_nm), what="step_nm")
    keys = _component_refs(refs)
    if len(keys) != len(set(keys)):
        raise _fail("duplicate_component", "pattern component references must be unique")
    store = _store(sk_or_doc, create=False)
    if store is None:
        raise _fail("no_assembly", "target has no assembly table")
    originals = [_component(store, key) for key in keys]
    parents = {}
    for row in originals:
        if row.get("kind") == "subassembly" or row.get("children"):
            raise _fail("unsupported_geometry", "recursive subassembly patterns are not supported",
                        component=row["uuid"])
        parent = row.get("parent")
        if parent:
            parents[parent] = _component(store, parent)
            if not isinstance(parents[parent].get("children"), list):
                raise _fail("corrupt_store", "parent component has no children list", component=parent)
    copies = []
    for index in range(1, times):
        for original in originals:
            row = deepcopy(original)
            row["uuid"] = new_uuid()
            row["at_nm"] = [_nm(original["at_nm"][axis] + index * step[axis],
                                what="pattern.at_nm") for axis in range(3)]
            row["pattern"] = {"source": original["uuid"], "index": index,
                              "count": times, "step_nm": list(step)}
            copies.append(row)
    _table(store, "components").extend(copies)
    for row in copies:
        if row.get("parent"):
            parents[row["parent"]]["children"].append(row["uuid"])
    return [row["uuid"] for row in copies]


def measure_distance(sk_or_doc: Any, *, refs: Any, kind: str = "distance") -> list[str]:
    """Record the current distance between two component origins in integer nm.

    This is origin-to-origin distance, not minimum B-Rep surface clearance.
    Integer square-root rounding avoids losing large-coordinate precision.
    """
    if kind != "distance":
        raise _fail("invalid_argument", "only origin distance is supported", kind=kind)
    keys = _component_refs(refs)
    if len(keys) != 2:
        raise _fail("invalid_argument", "distance measurement requires two components")
    store = _store(sk_or_doc, create=False)
    if store is None:
        raise _fail("no_assembly", "target has no assembly table")
    first, second = [_component(store, key) for key in keys]
    start, end = list(first["at_nm"]), list(second["at_nm"])
    square = sum((end[axis] - start[axis]) ** 2 for axis in range(3))
    distance = math.isqrt(square)
    if square - distance ** 2 > distance:
        distance += 1
    row = {"uuid": new_uuid(), "kind": "distance", "basis": "component_origins",
           "refs": keys, "from_nm": start, "to_nm": end, "distance_nm": distance,
           "status": "measured"}
    _table(store, "measurements").append(row)
    return [row["uuid"]]


# ======================================================================================
# 操作：移动 / 旋转
# ======================================================================================


def move_component(sk_or_doc: Any, *, components: Any, delta_nm: Any, allow_fixed: bool = False) -> list[str]:
    """**平移零部件**：`at_nm += delta_nm`（整数纳米，可为负）。姿态与参考几何不变。

    固定件（`fixed=True`）默认拒动 ⇒ `CadError(reason="component_fixed")`；要强动给
    `allow_fixed=True`（显式，不静默跳过 —— "没动"绝不伪装成"动了"）。

    返回：被移动的组件 uuid 列表（按 `components` 给定顺序，去重后首次出现的顺序）。
    """
    store = _store(sk_or_doc)
    delta = _nm3(delta_nm, what="delta_nm")
    allow = _flag(allow_fixed, what="allow_fixed")
    out: list[str] = []
    for ref in _component_refs(components):
        row = _component(store, ref)
        if bool(row.get("fixed")) and not allow:
            raise _fail("component_fixed", "component is fixed; pass allow_fixed=True to override",
                        component=str(row.get("uuid")))
        row["at_nm"] = [int(row["at_nm"][axis]) + int(delta[axis]) for axis in range(3)]
        if str(row["uuid"]) not in out:
            out.append(str(row["uuid"]))
    return out


def _axis_unit(axis: Any) -> list[float]:
    """旋转轴入参 → 单位向量（`"x"`/`"y"`/`"z"` 或 3 元方向；其它报 `unknown_axis`）。"""
    if isinstance(axis, str):
        key = axis.strip().lower()
        if key in ("x", "y", "z"):
            return [1.0 if key == "x" else 0.0, 1.0 if key == "y" else 0.0, 1.0 if key == "z" else 0.0]
        raise _fail("unknown_axis", "axis must be 'x', 'y', 'z' or a 3-vector", axis=axis)
    return _unit3(axis, what="axis")


def rotate_component(sk_or_doc: Any, *, components: Any, angle_deg: Any, axis: Any = "z",
                     center_nm: Any = None, allow_fixed: bool = False) -> list[str]:
    """**绕世界轴旋转零部件**：`R_new = R_axis @ R_old`，位置绕 `center_nm` 同转。

    * `axis`：`"x"` / `"y"` / `"z"` 或 3 元方向（后者按 Rodrigues 精确算，不是欧拉角近似）；
    * `center_nm`：世界旋转中心；缺省 = 装配中心（已测组件的并集盒中心），一件都没测 ⇒ 原点，
      并在返回值上无痕 —— 该缺省**只影响落点**，要写死中心请显式给 `center_nm`；
    * 固定件同 `move_component`（`component_fixed` / `allow_fixed`）。

    返回：被旋转的组件 uuid 列表（按 `components` 顺序，去重）。
    """
    store = _store(sk_or_doc)
    unit = _axis_unit(axis)
    angle = _angle(angle_deg, what="angle_deg")
    allow = _flag(allow_fixed, what="allow_fixed")
    center = _nm3(center_nm, what="center_nm", allow_none=True)
    if center is None:
        center = _assembly_center(store) or [0.0, 0.0, 0.0]
    center = [float(item) for item in center]
    rotation = _rot_axis(unit, angle)
    out: list[str] = []
    for ref in _component_refs(components):
        row = _component(store, ref)
        if bool(row.get("fixed")) and not allow:
            raise _fail("component_fixed", "component is fixed; pass allow_fixed=True to override",
                        component=str(row.get("uuid")))
        at = [float(item) for item in row.get("at_nm", [0, 0, 0])]
        moved = _mat_apply(rotation, _sub3(at, center))
        row["at_nm"] = [int(round(moved[axis_index] + center[axis_index])) for axis_index in range(3)]
        row["rotation"] = _mat_mul(rotation, row.get("rotation") or _mat_identity())
        if str(row["uuid"]) not in out:
            out.append(str(row["uuid"]))
    return out


# ======================================================================================
# 操作：配合（记录 + 现算残差；本层不做约束求解）
# ======================================================================================


def _tolerance_nm(value: Any) -> int:
    """长度容差入参 → 整数纳米；`None` ⇒ `-1`（**未采**，检查时用调用方缺省）。"""
    if value is None:
        return UNMEASURED
    tolerance = _nm(value, what="tolerance_nm")
    if tolerance < 0:
        raise _fail("out_of_range", "tolerance_nm must be >= 0", value=int(tolerance))
    return int(tolerance)


def _tolerance_deg(value: Any) -> float | None:
    """角度容差入参 → 浮点度；`None` ⇒ `None`（NA，检查时用调用方缺省）。"""
    if value is None:
        return None
    tolerance = _angle(value, what="tolerance_deg")
    if tolerance < 0:
        raise _fail("out_of_range", "tolerance_deg must be >= 0", value=float(tolerance))
    return float(tolerance)


def mate_add(sk_or_doc: Any, *, kind: Any, refs: Any, value_nm: Any = None, value_deg: Any = None,
             flip: bool = False, tolerance_nm: Any = None, tolerance_deg: Any = None,
             label: Any = None) -> list[str]:
    """**加一条配合**（重合 / 平行 / 垂直 / 同轴 / 距离 / 角度）—— 记录约束，不做求解。

    六种 `kind` 的语义与"用什么量判满足"（`mate_check()` 现算）：

    * `coincident`：两参考点重合（点距 ≤ tol）且方向同向（`flip=True` ⇒ 反向）；
    * `parallel`：两方向平行（`flip=True` ⇒ 反向平行）；
    * `perpendicular`：两方向夹角 90°；
    * `concentric`：两轴共线（方向平行/反向 **且** 第二点距第一轴 ≤ tol）；
    * `distance`：两参考点在**第一参考方向**上的有符号投影 = `value_nm`（`flip=True` 取负号侧）；
    * `angle`：两方向夹角 = `value_deg`（归一化到 0..180）。

    `value_nm` 只对 `distance` 必需（`0` = 真为零，合法）；`value_deg` 只对 `angle` 必需；
    给了不该给的 ⇒ `unexpected_value`（不静默忽略）。`tolerance_nm` / `tolerance_deg` 省略 ⇒
    记 **未采（-1 / None）**，由 `mate_check()` 的缺省接管，不在这里编一个假容差。
    同 kind + 同参考对 + 同值的重复添加 ⇒ `CadError(reason="duplicate_mate")`。

    返回：`[配合 uuid]`。
    """
    store = _store(sk_or_doc)
    key = _text(kind, what="kind").lower()
    if key not in MATE_KINDS:
        raise _fail("unknown_mate_kind", "kind is not in this layer's frozen enum", kind=kind,
                    known=list(MATE_KINDS))
    pairs = _ref_pair(refs)
    if pairs[0] == pairs[1]:
        # 退化配合：同一 (组件, 参考名) 跟自己配合 —— 记录自洽但约束无意义；与 `_unit3` 拒零向量
        # 同口径：早失败报 `degenerate_mate`，不静默收下。
        raise _fail("degenerate_mate", "a mate needs two distinct references",
                    ref=f"{pairs[0][0]}:{pairs[0][1]}")
    for component_uuid, ref_name in pairs:  # 早失败：组件/参考名不存在当场报，不落半个记录
        _ref_geometry(_component(store, component_uuid), ref_name)
    distance = UNMEASURED
    if value_nm is not None:
        if key != "distance":
            raise _fail("unexpected_value", "value_nm is only meaningful for a distance mate", kind=key)
        distance = _nm(value_nm, what="value_nm")
        if distance < 0:
            raise _fail("out_of_range", "value_nm must be >= 0 (use flip for the negative side)",
                        value=int(distance))
    elif key == "distance":
        raise _fail("missing_value", "a distance mate needs value_nm", kind=key)
    degrees = None
    if value_deg is not None:
        if key != "angle":
            raise _fail("unexpected_value", "value_deg is only meaningful for an angle mate", kind=key)
        degrees = abs(_angle(value_deg, what="value_deg"))
        if degrees > 180.0:
            raise _fail("out_of_range", "value_deg must be within 0..180", value=float(degrees))
    elif key == "angle":
        raise _fail("missing_value", "an angle mate needs value_deg", kind=key)
    uuid = new_uuid()
    row = {
        "uuid": uuid, "kind": key,
        "refs": [[pairs[0][0], pairs[0][1]], [pairs[1][0], pairs[1][1]]],
        "value_nm": int(distance), "value_deg": degrees, "flip": bool(_flag(flip, what="flip")),
        "tolerance_nm": _tolerance_nm(tolerance_nm), "tolerance_deg": _tolerance_deg(tolerance_deg),
        "label": _text(label, what="label") if label is not None else NA,
        "status": "recorded", "measured_nm": UNMEASURED, "measured_deg": None,
    }
    for existing in _table(store, "mates"):
        if (existing.get("kind") == row["kind"] and existing.get("refs") == row["refs"]
                and existing.get("value_nm") == row["value_nm"]
                and existing.get("value_deg") == row["value_deg"]
                and bool(existing.get("flip")) == row["flip"]):
            raise _fail("duplicate_mate", "the same mate is already recorded", mate=str(existing.get("uuid")))
    _table(store, "mates").append(row)
    return [uuid]


def mate_coincident(sk_or_doc: Any, *, refs: Any, flip: bool = False, **kwargs: Any) -> list[str]:
    """**重合配合**：两参考点重合（+ 方向对齐，`flip=True` 反向）。返回 `[配合 uuid]`。"""
    return mate_add(sk_or_doc, kind="coincident", refs=refs, flip=flip, **kwargs)


def mate_parallel(sk_or_doc: Any, *, refs: Any, flip: bool = False, **kwargs: Any) -> list[str]:
    """**平行配合**：两方向平行（`flip=True` 反向平行）。返回 `[配合 uuid]`。"""
    return mate_add(sk_or_doc, kind="parallel", refs=refs, flip=flip, **kwargs)


def mate_perpendicular(sk_or_doc: Any, *, refs: Any, **kwargs: Any) -> list[str]:
    """**垂直配合**：两方向夹角 90°。返回 `[配合 uuid]`。"""
    return mate_add(sk_or_doc, kind="perpendicular", refs=refs, **kwargs)


def mate_concentric(sk_or_doc: Any, *, refs: Any, flip: bool = False, **kwargs: Any) -> list[str]:
    """**同轴配合**：两轴共线（方向平行/反向 且 点距轴 ≤ tol）。返回 `[配合 uuid]`。"""
    return mate_add(sk_or_doc, kind="concentric", refs=refs, flip=flip, **kwargs)


def mate_distance(sk_or_doc: Any, *, refs: Any, value_nm: Any = None, flip: bool = False,
                  **kwargs: Any) -> list[str]:
    """**距离配合**：沿第一参考方向的有符号投影 = `value_nm`（`0` 合法）。返回 `[配合 uuid]`。

    缺 `value_nm` ⇒ `CadError(reason="missing_value")`（**不是** TypeError：本层对外只抛 CadError，
    内建异常名泄漏会被参数边界门抓，见 RC2 口径）。
    """
    return mate_add(sk_or_doc, kind="distance", refs=refs, value_nm=value_nm, flip=flip, **kwargs)


def mate_angle(sk_or_doc: Any, *, refs: Any, value_deg: Any = None, **kwargs: Any) -> list[str]:
    """**角度配合**：两方向夹角 = `value_deg`（0..180）。返回 `[配合 uuid]`。

    缺 `value_deg` ⇒ `CadError(reason="missing_value")`（同上，不泄漏 TypeError）。
    """
    return mate_add(sk_or_doc, kind="angle", refs=refs, value_deg=value_deg, **kwargs)


def mate_delete(sk_or_doc: Any, *, mates: Any) -> list[str]:
    """**删配合**：按 uuid 移除（不改任何组件位姿）。找不到 ⇒ `CadError(reason="unknown_mate")`。

    返回：被删除的配合 uuid 列表（按给定顺序）。

    **原子**（B6 缺陷 RC-A1）：先把给定 uuid **全部解析完**，有一条找不到就整笔拒绝、文档一个字节
    都不改；只有全部命中才进第二趟删除。此前是"边删边校验"，混合列表（存在的 + 不存在的）会抛
    `unknown_mate` **但前面的配合已经删掉** —— 调用方看到失败，真源却已经变了半截（最容易被
    上层当成"没生效"重试，于是删掉更多）。同一个 uuid 给两次 ⇒ `duplicate_mate`（同样不留半截）。
    """
    store = _store(sk_or_doc)
    table = _table(store, "mates")
    wanted = _component_refs(mates)
    # 第一趟：全部解析（只读，不碰表）
    targets: list[str] = []
    seen: set[str] = set()
    for key in wanted:
        if key in seen:
            raise _fail("duplicate_mate", "the same mate uuid was given twice", mate=key,
                        known=[str(row.get("uuid")) for row in table])
        seen.add(key)
        if not any(str(row.get("uuid")) == key for row in table):
            raise _fail("unknown_mate", "no mate with that uuid", mate=key,
                        known=[str(row.get("uuid")) for row in table])
        targets.append(key)
    # 第二趟：删除（到这里所有 uuid 都已确认存在 ⇒ 不会半途抛错）
    out: list[str] = []
    for key in targets:
        for index, row in enumerate(table):
            if str(row.get("uuid")) == key:
                del table[index]
                break
        out.append(key)
    return out


def _measure(kind: str, first: dict[str, Any], second: dict[str, Any], *, value_nm: int,
             value_deg: float | None, flip: bool, tol_nm: int, tol_deg: float) -> tuple[int, float | None, bool | None]:
    """现算一条配合的残差 → `(measured_nm, measured_deg, satisfied)`。

    `measured_nm = -1` = 量不出（未采）；`measured_deg = None` = 该 kind 没有角度量（NA）；
    `satisfied = None` = 量不出（第三种状态，**不是**"满足"）。
    """
    point_a, point_b = first["point"], second["point"]
    dir_a, dir_b = first["dir"], second["dir"]
    if kind == "coincident":
        if point_a is None or point_b is None:
            return UNMEASURED, None, None
        measured = int(round(_norm3(_sub3(point_a, point_b))))
        angle = _angle_between(dir_a, dir_b) if (dir_a and dir_b) else None
        ok = measured <= tol_nm
        if angle is not None:
            ok = ok and abs(angle - (180.0 if flip else 0.0)) <= tol_deg
        return measured, angle, bool(ok)
    if kind in ("parallel", "perpendicular", "angle"):
        if not dir_a or not dir_b:
            return UNMEASURED, None, None
        angle = _angle_between(dir_a, dir_b)
        if kind == "parallel":
            return UNMEASURED, angle, bool(abs(angle - (180.0 if flip else 0.0)) <= tol_deg)
        if kind == "perpendicular":
            return UNMEASURED, angle, bool(abs(angle - 90.0) <= tol_deg)
        return UNMEASURED, angle, bool(abs(angle - float(value_deg)) <= tol_deg)
    if kind == "concentric":
        if point_a is None or point_b is None or not dir_a or not dir_b:
            return UNMEASURED, None, None
        angle = _angle_between(dir_a, dir_b)
        offset = _point_line_distance(point_b, point_a, dir_a)
        parallel_ok = abs(angle - (180.0 if flip else 0.0)) <= tol_deg
        return int(round(offset)), angle, bool(parallel_ok and offset <= tol_nm)
    if kind == "distance":
        if point_a is None or point_b is None:
            return UNMEASURED, None, None
        direction = dir_a or [0.0, 0.0, 1.0]
        projection = _dot3(_sub3(point_b, point_a), direction)
        if flip:
            projection = -projection
        measured = int(round(projection))
        return measured, None, bool(abs(projection - abs(float(value_nm))) <= tol_nm)
    raise _fail("unknown_mate_kind", "kind is not in this layer's frozen enum", kind=kind,
                known=list(MATE_KINDS))


def _mate_stats(store: dict[str, Any] | None) -> dict[str, int]:
    """配合状态计数（`status="recorded"` = 只记录、还没检查过，单独一栏，不与"满足"混）。"""
    stats = {"satisfied": 0, "violated": 0, "unmeasured": 0, "recorded": 0}
    if store is None:
        return stats
    for row in _table(store, "mates"):
        stats[str(row.get("status", "recorded"))] = stats.get(str(row.get("status", "recorded")), 0) + 1
    return stats


def mate_check(sk_or_doc: Any, *, mates: Any = None, tolerance_nm: Any = None,
               tolerance_deg: Any = None, include_ok: bool = False) -> list[str]:
    """**检查配合**：用组件**当前**位姿现算每条配合的残差，回写 `status/measured_*`。

    容差优先级：调用参数 > 记录里存的（非 `-1`/非 `None`）> 模块缺省（1 µm / 1e-6°）。
    `status`：`satisfied` / `violated` / `unmeasured`（量不出，例如参考缺方向）/ `suppressed`
    （被压缩，`suppress_mate` 之后**不再强制**，但状态机读可见，绝不与"满足"混）——
    **量不出绝不记成满足**。`mates=None` ⇒ 检查全部未压缩配合。
    种类分派走 `_measure_any()`（六种原配合 + 后加的相切/锁定）。

    返回：不满足的配合 uuid 列表（`violated` + `unmeasured`，按表内顺序）；`include_ok=True`
    时连 `satisfied` 的一起返回。

    **读侧不写盘、也不把"没表"记成"没配合"**（B6 缺陷 RC-A2 / E2"禁静默 0"）：目标上根本没有
    装配表时抛 `CadError(reason="no_assembly")` —— 空的 `[]` 只能表示"表在、但没有不满足的配合"，
    不许同时表示"这个目标没有装配表"。此前 `_store(create=True)` 会**凭空建一张空表**再返回 `[]`，
    于是"表读不到"被伪装成"装配没问题"（最危险的一类静默 0）。
    """
    store = _store(sk_or_doc, create=False)
    if store is None:
        # 只报"没有表"这一件事：这里**不许**再调 `_target_key()` 之类的读取器 —— 它们在缺字段的
        # 目标上会抛自己的错，把真正的 reason（`no_assembly`）盖掉。
        raise _fail("no_assembly", "this target has no assembly table to check "
                                   "(distinct from 'the assembly has no mates')")
    default_nm = DEFAULT_TOLERANCE_NM if tolerance_nm is None else _nm(tolerance_nm, what="tolerance_nm")
    default_deg = DEFAULT_TOLERANCE_DEG if tolerance_deg is None else _angle(tolerance_deg, what="tolerance_deg")
    if default_nm < 0 or default_deg < 0:
        raise _fail("out_of_range", "tolerances must be >= 0")
    wanted = None if mates is None else [str(item) for item in _component_refs(mates)]
    out: list[str] = []
    for row in _table(store, "mates"):
        row_uuid = str(row.get("uuid"))
        if wanted is not None and row_uuid not in wanted:
            continue
        if bool(row.get("suppressed")):
            row["status"] = "suppressed"
            continue
        pairs = row.get("refs") or []
        stored_nm = int(row.get("tolerance_nm", UNMEASURED))
        stored_deg = row.get("tolerance_deg")
        tol_nm = stored_nm if stored_nm != UNMEASURED else int(default_nm)
        tol_deg = float(stored_deg) if stored_deg is not None else float(default_deg)
        raw_value = row.get("value_nm")
        value_nm = int(raw_value) if isinstance(raw_value, int) and not isinstance(raw_value, bool) else UNMEASURED
        try:
            first = _world_mate_ref(store, (str(pairs[0][0]), str(pairs[0][1])))
            second = _world_mate_ref(store, (str(pairs[1][0]), str(pairs[1][1])))
        except CadError:
            row["status"], row["measured_nm"], row["measured_deg"] = "unmeasured", UNMEASURED, None
            out.append(row_uuid)
            continue
        measured_nm, measured_deg, satisfied = _measure_any(
            store, row, first, second, value_nm=value_nm,
            value_deg=row.get("value_deg"), flip=bool(row.get("flip")), tol_nm=int(tol_nm),
            tol_deg=float(tol_deg))
        row["measured_nm"] = int(measured_nm)
        row["measured_deg"] = None if measured_deg is None else float(measured_deg)
        row["status"] = "unmeasured" if satisfied is None else ("satisfied" if satisfied else "violated")
        if satisfied is None or not satisfied or include_ok:
            out.append(row_uuid)
    return out


# ======================================================================================
# 操作：干涉检查（真体积交）/ 爆炸视图数据
# ======================================================================================

#: 干涉判定方法：`solid`（缺省：真凸体求交，出**相交体积**）/ `aabb`（历史粗检查：世界 AABB）。
INTERFERENCE_METHODS: tuple[str, ...] = ("solid", "aabb")

#: "恰好贴面"的数值地板（纳米）：间隙恰为 0 时 AABB 与真体积都是 0，本层按碰撞计（与历史口径一致）。
_TOUCH_EPS_NM = 1


def _box_halfspaces(component: dict[str, Any]) -> list[dict[str, Any]] | None:
    """组件的**真实体**（局部盒 + 位姿）→ 世界坐标下 6 个半空间 `n·x <= d`（单位外法向）。

    盒 = 6 个半空间的交（凸多面体）。旋转矩阵正交 ⇒ 法向旋转后仍单位长；局部 `x_axis <= high`
    在世界里是 `(R e_axis)·x <= high + (R e_axis)·at`。没有包围盒 ⇒ `None`（未采，不假装有实体）。
    """
    bbox = component.get("bbox_nm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 2:
        return None
    try:
        low = [float(item) for item in bbox[0]]
        high = [float(item) for item in bbox[1]]
    except (TypeError, ValueError):
        return None
    if len(low) != 3 or len(high) != 3:
        return None
    rotation = component.get("rotation") or _mat_identity()
    at = [float(item) for item in component.get("at_nm", [0, 0, 0])]
    out: list[dict[str, Any]] = []
    for axis in range(3):
        for sign, bound in ((1.0, high[axis]), (-1.0, low[axis])):
            local = [0.0, 0.0, 0.0]
            local[axis] = sign
            normal = [float(item) for item in _mat_apply(rotation, local)]
            out.append({"n": normal,
                        "d": float(sign * bound + sum(normal[i] * at[i] for i in range(3)))})
    return out


def _solve3(n1: Any, d1: float, n2: Any, d2: float, n3: Any, d3: float) -> list[float] | None:
    """三平面交点（Cramer）；系数矩阵奇异（行列式≈0）⇒ `None`（三面共轴，不产生顶点）。"""
    det = (n1[0] * (n2[1] * n3[2] - n2[2] * n3[1])
           - n1[1] * (n2[0] * n3[2] - n2[2] * n3[0])
           + n1[2] * (n2[0] * n3[1] - n2[1] * n3[0]))
    if abs(det) <= 1e-12:
        return None
    det_x = (d1 * (n2[1] * n3[2] - n2[2] * n3[1])
             - n1[1] * (d2 * n3[2] - n2[2] * d3)
             + n1[2] * (d2 * n3[1] - n2[1] * d3))
    det_y = (n1[0] * (d2 * n3[2] - n2[2] * d3)
             - d1 * (n2[0] * n3[2] - n2[2] * n3[0])
             + n1[2] * (n2[0] * d3 - d2 * n3[0]))
    det_z = (n1[0] * (n2[1] * d3 - d2 * n3[1])
             - n1[1] * (n2[0] * d3 - d2 * n3[0])
             + d1 * (n2[0] * n3[1] - n2[1] * n3[0]))
    return [det_x / det, det_y / det, det_z / det]


def _convex_points(halfspaces: Sequence[Mapping[str, Any]], *, tol: float) -> list[list[float]]:
    """半空间集合的**顶点枚举**：每三张平面求交，保留满足全部约束的点，去重。

    凸多面体的顶点必是某三张支撑平面的交点（`m=12` ⇒ `C(12,3)=220` 组，代价可忽略），
    所以枚举三平面交点 + 逐点验约束就能拿到**精确顶点集**，不需要增量凸包。
    """
    points: list[list[float]] = []
    total = len(halfspaces)
    for i in range(total):
        for j in range(i + 1, total):
            for k in range(j + 1, total):
                point = _solve3(halfspaces[i]["n"], halfspaces[i]["d"],
                                halfspaces[j]["n"], halfspaces[j]["d"],
                                halfspaces[k]["n"], halfspaces[k]["d"])
                if point is None:
                    continue
                if all(sum(hs["n"][axis] * point[axis] for axis in range(3)) <= hs["d"] + tol
                       for hs in halfspaces):
                    points.append(point)
    unique: list[list[float]] = []
    for point in points:
        if not any(all(abs(point[axis] - seen[axis]) <= tol for axis in range(3)) for seen in unique):
            unique.append(point)
    return unique


def _face_order(points: list[list[float]], normal: Any) -> list[list[float]]:
    """平面上的一组点按绕 `+normal` 的逆时针序排列（外法向侧看为逆时针 ⇒ 面片法向朝外）。"""
    cx = sum(item[0] for item in points) / len(points)
    cy = sum(item[1] for item in points) / len(points)
    cz = sum(item[2] for item in points) / len(points)
    # 取**与法向不平行**的坐标轴做基准（取法向最小分量那一轴）：平行时叉积退化成零向量，
    # 排序会静默失效、面片扇形三角化随即算错体积（实测：轴对齐盒的体积会少 1/3）。
    index = min(range(3), key=lambda item: abs(normal[item]))
    axis = [0.0, 0.0, 0.0]
    axis[index] = 1.0
    u = _cross3(axis, normal)
    length = _norm3(u)
    if length <= 1e-12:
        return list(points)
    u = [item / length for item in u]
    v = _cross3(normal, u)
    return sorted(points, key=lambda item: math.atan2(
        (item[0] - cx) * v[0] + (item[1] - cy) * v[1] + (item[2] - cz) * v[2],
        (item[0] - cx) * u[0] + (item[1] - cy) * u[1] + (item[2] - cz) * u[2]))


def _convex_volume(halfspaces: Sequence[Mapping[str, Any]], points: list[list[float]],
                   *, tol: float) -> tuple[int, int]:
    """凸多面体体积（nm³，整数）：按支撑面分组顶点 → 面内逆时针排 → 扇形三角化 → 散度定理求和。

    返回 `(volume_nm3, face_count)`。**空顶点集 ⇒ (0, 0)**（真的不相交，不是"没算"）。
    坐标先平移到顶点质心：纳米量级坐标（~1e7）直接做行列式会掉精度，平移后量级降到包围盒尺度。
    """
    if len(points) < 4:
        return 0, 0
    center = [sum(item[axis] for item in points) / len(points) for axis in range(3)]
    shifted = [[item[axis] - center[axis] for axis in range(3)] for item in points]
    volume = 0.0
    faces = 0
    # **同一张平面只算一次**：两个盒子的贴合面（例：A 的 y=0 与 B 的 y=0）在并集里是**同一个面**，
    # 逐半空间累加会把它算两次 ⇒ 体积虚高（实测：轴对齐夹具虚高到 5/3）。这里按 (n, d) 去重。
    planes: list[Mapping[str, Any]] = []
    for hs in halfspaces:
        if any(all(abs(hs["n"][axis] - seen["n"][axis]) <= 1e-9 for axis in range(3))
               and abs(hs["d"] - seen["d"]) <= tol for seen in planes):
            continue
        planes.append(hs)
    for hs in planes:
        plane = hs["d"] - sum(hs["n"][axis] * center[axis] for axis in range(3))
        face = [point for point, original in zip(shifted, points)
                if abs(sum(hs["n"][axis] * original[axis] for axis in range(3)) - hs["d"]) <= tol]
        if len(face) < 3:
            continue
        ordered = _face_order(face, hs["n"])
        faces += 1
        first = ordered[0]
        for index in range(1, len(ordered) - 1):
            second, third = ordered[index], ordered[index + 1]
            volume += (first[0] * (second[1] * third[2] - second[2] * third[1])
                       - first[1] * (second[0] * third[2] - second[2] * third[0])
                       + first[2] * (second[0] * third[1] - second[1] * third[0])) / 6.0
    return int(round(abs(volume))), faces


def _solid_intersection(left: dict[str, Any], right: dict[str, Any], *,
                        inflate_nm: float = 0.0) -> dict[str, Any]:
    """两个组件的**真凸体求交**：返回 `{volume_nm3, vertices, faces, state}`。

    `inflate_nm > 0` 时把右件的局部包围盒先膨胀该值（= 用 `L∞` 球做的 Minkowski 外近似）：
    用来回答"两件是否在容差内接触" —— 凸集下 `A ∩ (B ⊕ ball_tol) ≠ ∅ ⟺ dist(A,B) ≤ tol`，
    本层用外接盒近似该膨胀（偏保守、宁误报不漏报），与历史 AABB 口径同侧。
    """
    box_a = _box_halfspaces(left)
    box_b = _box_halfspaces(right)
    if box_a is None or box_b is None:
        return {"volume_nm3": UNMEASURED, "vertices": UNMEASURED, "faces": UNMEASURED,
                "state": "unmeasured"}
    if inflate_nm:
        bbox = right.get("bbox_nm")
        grown = dict(right)
        grown["bbox_nm"] = [[float(item) - inflate_nm for item in bbox[0]],
                            [float(item) + inflate_nm for item in bbox[1]]]
        box_b = _box_halfspaces(grown)
    combined = list(box_a) + list(box_b)
    scale = 0.0
    for hs in combined:
        scale = max(scale, abs(hs["d"]))
    tol = max(1e-6, scale * 1e-9)
    points = _convex_points(combined, tol=tol)
    volume, faces = _convex_volume(combined, points, tol=tol)
    return {"volume_nm3": int(volume), "vertices": len(points), "faces": int(faces), "state": "ok"}


def interference_check(sk_or_doc: Any, *, pairs: Any = None, tolerance_nm: Any = 0,
                       only_hits: bool = True, include_suppressed: bool = False,
                       method: Any = "solid") -> list[str]:
    """**干涉检查（真体积交）**：把每个组件当**真凸实体**（局部盒 + 位姿）求交，出相交体积。

    判定（`method="solid"`，缺省）：

    * `volume_nm3 = vol(A ∩ B)` —— **精确**的凸体交体积（顶点枚举 + 散度定理，零第三方依赖）；
    * 先做世界 AABB 预筛：三轴 clearance 全 `> tolerance_nm` ⇒ 必不相交 ⇒ `clear`、体积 **0（真为零）**；
    * 预筛通过 ⇒ 求交：`volume > 0` ⇒ `collision`（`overlap_nm` 记 AABB 重叠深度，另有真体积）；
    * `volume == 0` 但两件在容差内接触（把 B 按 `tolerance_nm` 膨胀后仍相交，含 1 nm 数值地板
      ⇒ 间隙恰为 0 的贴面仍按碰撞计，与历史口径一致）⇒ `collision`，`volume_nm3=0`；
    * 否则 `clear`：**这正是 AABB 粗检查的假阳性**（旋转盒的外接盒重叠、真实体不相交），
      行里 `aabb_false_positive=True` 留痕，`method="aabb"` 的历史路径仍可选（口径可对拍）。

    `method="aabb"`：历史行为（世界 AABB 相交判定；旋转盒取 8 角点外接 ⇒ 偏保守）。两种方法写**同一张**
    `interferences` 表，行里 `method` 字段区分；换方法 = 刷新同一行（uuid 含方法与容差）。

    三态（**关键**）：没有包围盒的组件 ⇒ `status="unmeasured"`（未采，体积 `-1`），**照样报出来**，
    绝不静默当"不干涉"。

    参数：`pairs=None` ⇒ 所有无序对（按插入序 i<j；同对同容差只记一条，uuid 稳定，重复调用 =
    刷新不追加）；`pairs` 给了就只查这些对（对内按 uuid 字典序规范化）。

    返回：本次评估命中的记录 uuid（`collision` + `unmeasured`，按评估顺序）；`only_hits=False`
    时连 `clear` 的一起返回。**全部 clear 时返回 `[]`（真为零，不是"没查"）**。
    """
    store = _store(sk_or_doc)
    tolerance = _nm(tolerance_nm, what="tolerance_nm")
    if tolerance < 0:
        raise _fail("out_of_range", "tolerance_nm must be >= 0", value=int(tolerance))
    key = _text(method, what="method").lower()
    if key not in INTERFERENCE_METHODS:
        raise _fail("unknown_interference_method", "method is not in this layer's frozen enum",
                    method=method, known=list(INTERFERENCE_METHODS))
    hits_only = _flag(only_hits, what="only_hits")
    flag_suppressed = _flag(include_suppressed, what="include_suppressed")
    live = [row for row in _table(store, "components")
            if flag_suppressed or not bool(row.get("suppressed"))]
    by_uuid = {str(row.get("uuid")): row for row in live}
    if pairs is None:
        candidates = [(str(live[left].get("uuid")), str(live[right].get("uuid")))
                      for left in range(len(live)) for right in range(left + 1, len(live))]
    else:
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, (list, tuple)):
            raise _fail("invalid_argument", "pairs must be a sequence of [a, b] pairs", value=repr(pairs))
        candidates = []
        for item in pairs:
            if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)) or len(item) != 2:
                raise _fail("invalid_argument", "each pair must be [component_uuid, component_uuid]",
                            value=repr(item))
            left, right = _text(item[0], what="pair[0]"), _text(item[1], what="pair[1]")
            if left == right:
                raise _fail("invalid_argument", "a pair needs two distinct components", pair=[left, right])
            for member in (left, right):  # 显式失败：组件被抑制/不存在都不静默跳过
                if member not in by_uuid:
                    raise _fail("unknown_component", "no live component with that uuid", component=member,
                                suppressed_or_missing=True)
            candidates.append(tuple(sorted((left, right))))
    table = _table(store, "interferences")
    table_index = {str(row.get("uuid")): row for row in table}
    out: list[str] = []
    seen: set[str] = set()
    for left, right in candidates:
        uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "cadcore.sw_assembly.interference|%s|%s|%d|%s"
                               % (left, right, int(tolerance), key)))
        if uuid in seen:
            continue
        seen.add(uuid)
        box_a = _bbox_world(by_uuid[left])
        box_b = _bbox_world(by_uuid[right])
        if box_a is None or box_b is None:
            status = "unmeasured"
            row = {"uuid": uuid, "pair": [left, right], "status": status, "method": key,
                   "tolerance_nm": int(tolerance), "clearance_nm": None, "overlap_nm": None,
                   "volume_nm3": UNMEASURED, "intersection_vertices": UNMEASURED,
                   "intersection_faces": UNMEASURED, "aabb_false_positive": False,
                   "bbox_state": UNMEASURED}
        else:
            clearance = [max(box_a["min_nm"][axis], box_b["min_nm"][axis])
                         - min(box_a["max_nm"][axis], box_b["max_nm"][axis]) for axis in range(3)]
            aabb_hit = all(value <= int(tolerance) for value in clearance)
            depth = None if not aabb_hit else int(-max(clearance))
            volume = 0
            vertices = faces = 0
            false_positive = False
            if key == "solid":
                if not aabb_hit:
                    # AABB 分离 ⇒ 真凸体必不相交（预筛）：体积真为零，不做昂贵的顶点枚举
                    status = "clear"
                else:
                    solid = _solid_intersection(by_uuid[left], by_uuid[right])
                    volume = int(solid["volume_nm3"])
                    vertices, faces = int(solid["vertices"]), int(solid["faces"])
                    if volume > 0:
                        status = "collision"
                    else:
                        touch = _solid_intersection(by_uuid[right], by_uuid[left],
                                                    inflate_nm=float(max(int(tolerance), _TOUCH_EPS_NM)))
                        status = "collision" if int(touch["volume_nm3"]) > 0 else "clear"
                        false_positive = status == "clear"
            else:
                status = "collision" if aabb_hit else "clear"
            row = {"uuid": uuid, "pair": [left, right], "status": status, "method": key,
                   "tolerance_nm": int(tolerance), "clearance_nm": clearance, "overlap_nm": depth,
                   "volume_nm3": int(volume), "intersection_vertices": int(vertices),
                   "intersection_faces": int(faces), "aabb_false_positive": bool(false_positive),
                   "bbox_state": 0}
        if uuid in table_index:
            table_index[uuid].update(row)
        else:
            table.append(row)
            table_index[uuid] = row
        if status != "clear" or not hits_only:
            out.append(uuid)
    return out


def exploded_view(sk_or_doc: Any, *, explode: Any = 1.0, mode: Any = "radial", axis: Any = "z",
                  step_nm: Any = None, center_nm: Any = None) -> list[str]:
    """**爆炸视图数据**：给每个组件算一条位移向量 + 目标位置，写一条 `explodes` 记录。

    **不改组件位姿**（要真的挪件请显式 `move_component`）：

    * `mode="radial"`：方向 = 组件世界 AABB 中心 − 装配中心（零长度 ⇒ 退化到 `axis` 方向）；
      位移 = 单位方向 × `step_nm` × `explode`；
    * `mode="axis"`：位移 = `axis` 方向 × `step_nm` × `explode` × 序号（未抑制组件的插入序，1 起）；
    * `explode=0` ⇒ 位移全为 0（**真为零**，合法：表示"还没炸开"）；
    * `step_nm` 缺省 10 mm；`center_nm` 缺省 = 装配中心（同上，仅影响 radial 方向与 `to_nm` 口径）；
    * 只算**已测**（有包围盒）的组件；未测的组件进 `skipped` 列表并计 `-1`，不编造位移。

    幂等：同 `(mode, axis, explode, step_nm, 组件签名)` 只留一条记录（uuid 稳定，重算 = 刷新）。

    返回：`[爆炸记录 uuid]`（**恰好一个**；一条记录里装全部件的数据）。
    """
    store = _store(sk_or_doc)
    factor = _number(explode, what="explode", minimum=0.0)
    key = _text(mode, what="mode").lower()
    if key not in EXPLODE_MODES:
        raise _fail("unknown_explode_mode", "mode is not in this layer's frozen enum", mode=mode,
                    known=list(EXPLODE_MODES))
    unit = _axis_unit(axis)
    step = DEFAULT_EXPLODE_STEP_NM if step_nm is None else _nm(step_nm, what="step_nm")
    if step < 0:
        raise _fail("out_of_range", "step_nm must be >= 0", value=int(step))
    given_center = _nm3(center_nm, what="center_nm", allow_none=True)
    center = [float(item) for item in given_center] if given_center is not None else _assembly_center(store)
    live = [row for row in _table(store, "components") if not bool(row.get("suppressed"))]
    items: list[dict[str, Any]] = []
    skipped: list[str] = []
    index = 0
    for row in live:
        box = _bbox_world(row)
        if box is None:
            skipped.append(str(row.get("uuid")))
            continue
        index += 1
        if key == "axis":
            vector = [unit[axis_index] * float(step) * factor * index for axis_index in range(3)]
        else:
            box_center = [(box["min_nm"][axis_index] + box["max_nm"][axis_index]) / 2.0 for axis_index in range(3)]
            reference = center if center is not None else [0.0, 0.0, 0.0]
            direction = _sub3(box_center, reference)
            if _norm3(direction) <= 1e-9:
                direction = list(unit)
            else:
                length = _norm3(direction)
                direction = [item / length for item in direction]
            vector = [direction[axis_index] * float(step) * factor for axis_index in range(3)]
        at = [int(item) for item in row.get("at_nm", [0, 0, 0])]
        shifted = [int(round(at[axis_index] + vector[axis_index])) for axis_index in range(3)]
        items.append({"component": str(row.get("uuid")), "part_key": str(row.get("part_key")),
                      "vector_nm": [int(round(item)) for item in vector], "to_nm": shifted})
    signature = "|".join("%s@%d,%d,%d" % (item["component"], item["to_nm"][0], item["to_nm"][1],
                                          item["to_nm"][2]) for item in items)
    uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                           "cadcore.sw_assembly.explode|%s|%s|%s|%.6f|%d|%s"
                           % (key, ",".join("%.6f" % item for item in unit), NA if given_center is None
                              else "given", factor, int(step), signature)))
    row = {"uuid": uuid, "mode": key, "axis": [round(item, 12) for item in unit], "explode": float(factor),
           "step_nm": int(step), "center_nm": UNMEASURED if center is None else [int(round(item)) for item in center],
           "items": items, "skipped": skipped, "skipped_state": UNMEASURED if skipped else 0}
    for existing in _table(store, "explodes"):
        if str(existing.get("uuid")) == uuid:
            existing.update(row)
            return [uuid]
    _table(store, "explodes").append(row)
    return [uuid]


# ======================================================================================
# 机读事实面
# ======================================================================================


def assembly_facts(sk_or_doc: Any) -> dict[str, Any]:
    """族模块的机读判定面（不写盘）：计数与状态一律三态（`0` 真为零 / `-1` 未采 / `"NA"` 不适用）。"""
    store = _store(sk_or_doc, create=False)
    facts: dict[str, Any] = {
        "family": FAMILY, "op_count": len(OPERATIONS), "schema": _ASM_SCHEMA if store else NA,
        "store_present": store is not None, "doc_key": _target_key(sk_or_doc),
        "mate_kinds": list(MATE_KINDS),
    }
    if store is None:
        facts.update({"component_count": 0, "suppressed_count": 0, "bbox_unmeasured_count": 0,
                      "mate_count": 0, "mate_satisfied": 0, "mate_violated": 0, "mate_unmeasured": 0,
                      "mate_recorded": 0, "interference_collision": 0, "interference_clear": 0,
                      "interference_unmeasured": 0, "explode_count": 0, "assembly_bbox_nm": NA})
        return facts
    components = _table(store, "components")
    measured = sum(1 for row in components if _bbox_world(row) is not None)
    stats = _mate_stats(store)
    collisions = sum(1 for row in _table(store, "interferences") if row.get("status") == "collision")
    clears = sum(1 for row in _table(store, "interferences") if row.get("status") == "clear")
    unknown = sum(1 for row in _table(store, "interferences") if row.get("status") == "unmeasured")
    boxes = [_bbox_world(row) for row in components if not bool(row.get("suppressed"))]
    boxes = [box for box in boxes if box is not None]
    bbox = NA
    if boxes:
        bbox = [[min(box["min_nm"][axis] for box in boxes) for axis in range(3)],
                [max(box["max_nm"][axis] for box in boxes) for axis in range(3)]]
    facts.update({
        "component_count": len(components),
        "suppressed_count": sum(1 for row in components if bool(row.get("suppressed"))),
        "bbox_unmeasured_count": len(components) - measured,
        "mate_count": len(_table(store, "mates")),
        "mate_satisfied": stats.get("satisfied", 0), "mate_violated": stats.get("violated", 0),
        "mate_unmeasured": stats.get("unmeasured", 0), "mate_recorded": stats.get("recorded", 0),
        "interference_collision": collisions, "interference_clear": clears,
        "interference_unmeasured": unknown,
        "explode_count": len(_table(store, "explodes")), "assembly_bbox_nm": bbox,
    })
    return facts


# ======================================================================================
# 操作：层级（子装配体）/ 替换 / 半径 / 固定浮动（族门对账时按 features 补齐的真能力）
# ======================================================================================

#: 六种原配合之外，本节再把两种冻结枚举补进同一份清单（相切 / 锁定）。
#: 落点选在这里而不是再抄一份枚举：`MATE_KINDS` 仍是**唯一**真源，所有读它的地方（`mate_add`
#: 的 kind 校验、`assembly_facts().mate_kinds`）看到的是同一份八元组。
MATE_KINDS = tuple(MATE_KINDS) + ("tangent", "lock")


def _default_named_refs(refs: Any) -> dict[str, Any]:
    """参考几何入参 → 与缺省 `origin` 合并后的表（插入 / 子装配体 / 替换共用一份口径）。"""
    named = dict(_refs(refs))
    named.setdefault(_DEFAULT_REF_NAME, {"point_nm": [0, 0, 0], "dir": [0.0, 0.0, 1.0]})
    return named


def _named_refs_copy(named: dict[str, Any]) -> dict[str, Any]:
    """参考表深拷贝一层（行与行之间不共享 list，改一行不会串到另一行）。"""
    return {name: {"point_nm": list(item["point_nm"]), "dir": list(item["dir"])}
            for name, item in named.items()}


def _descends_from(store: dict[str, Any], row: dict[str, Any], ancestor_uuid: str) -> bool:
    """`row` 的祖先链（逐级上溯 `parent`）里是否出现 `ancestor_uuid`；visited 环保护（坏数据不死循环）。"""
    seen: set[str] = set()
    parent = row.get("parent")
    while isinstance(parent, str) and parent and parent not in seen:
        if parent == ancestor_uuid:
            return True
        seen.add(parent)
        next_row = None
        for other in _table(store, "components"):
            if str(other.get("uuid")) == parent:
                next_row = other
                break
        parent = None if next_row is None else next_row.get("parent")
    return False


def add_subassembly(sk_or_doc: Any, *, name: Any, components: Any = None, at_nm: Any = (0, 0, 0),
                    rotation_deg: Any = (0.0, 0.0, 0.0), bbox_nm: Any = None, size_nm: Any = None,
                    refs: Any = None, fixed: bool = True, source: Any = None) -> list[str]:
    """**新建子装配体**：往 `components[]` 插一行 `kind="subassembly"` 的层级容器（真行，不是壳）。

    子装配体与普通件的差别只有两处：`kind="subassembly"` 与 `children` 列表（由
    `attach_component()` 维护；层级的唯一真源是子件的 `parent`）。缺省 `fixed=True`
    （真实装配里子装配体通常整体先固定住）。位姿口径与 `insert_component()` 一致。

    ``components`` = **一次成组**（SW 的 "Form New Subassembly" 语义：选中若干件、一次收进
    新子装配体），子件在**同一次调用**里挂到新行下，走**同一份** `attach_component()`
    写路径（父行 `children` 与子件 `parent` 双向都写，不留半截）。引用不存在 ⇒
    `unknown_component`；空序列 ⇒ `invalid_argument`；先解析全部子件引用再动表 ⇒ 要么全挂、
    要么全不挂。`refs` 仍是**参考几何**（与 `insert_component()` 同一口径），不是子件清单。

    返回：新建的 uuid 列表（`[uuid]`）。
    """
    store = _store(sk_or_doc)
    key = _text(name, what="name")
    base = _nm3(at_nm, what="at_nm")
    rotation = _euler_to_mat(_num3(rotation_deg, what="rotation_deg"))
    box = _bbox(bbox_nm, size_nm)
    named = _default_named_refs(refs)
    kids: list[str] = []
    if components is not None:
        kids = [str(_component(store, ref).get("uuid")) for ref in _component_refs(components)]
    uuid = new_uuid()
    _table(store, "components").append({
        "uuid": uuid, "part_key": key, "kind": "subassembly",
        "at_nm": [int(item) for item in base], "rotation": [list(row) for row in rotation],
        "bbox_nm": None if box is None else [list(box[0]), list(box[1])],
        "refs": _named_refs_copy(named),
        "fixed": bool(_flag(fixed, what="fixed")), "suppressed": False, "parent": None,
        "children": [], "radius_nm": UNMEASURED,
        "source": str(source) if isinstance(source, str) and source.strip() else NA,
    })
    for child in kids:
        attach_component(sk_or_doc, component=child, parent=uuid)
    return [uuid]


def attach_component(sk_or_doc: Any, *, component: Any, parent: Any) -> list[str]:
    """**挂到子装配体下**：写子件的 `parent`，并在父行 `children` 里登记（两处同时改，不留半截）。

    父行不是 `subassembly` ⇒ `CadError(reason="not_subassembly")`（不静默接受乱七八糟的层级）；
    组件 / 父件不存在 ⇒ `unknown_component`；父件已在子件子树里（挂上去就成环）⇒ `cycle`。

    返回：被挂接的组件 uuid 列表（`[component]`）。
    """
    store = _store(sk_or_doc)
    child = _component(store, component)
    father = _component(store, parent)
    if str(father.get("kind")) != "subassembly":
        raise _fail("not_subassembly", "parent row is not a subassembly",
                    parent=str(father.get("uuid")), kind=str(father.get("kind")))
    if str(child.get("uuid")) == str(father.get("uuid")):
        raise _fail("invalid_argument", "a component cannot be its own parent",
                    component=str(child.get("uuid")))
    if _descends_from(store, father, str(child.get("uuid"))):
        raise _fail("cycle", "cannot attach: the parent is already inside the child's subtree",
                    component=str(child.get("uuid")), parent=str(father.get("uuid")))
    child["parent"] = str(father.get("uuid"))
    kids = father.get("children")
    if not isinstance(kids, list):
        kids = []
        father["children"] = kids
    if str(child.get("uuid")) not in kids:
        kids.append(str(child.get("uuid")))
    return [str(child.get("uuid"))]


def replace_component(sk_or_doc: Any, *, component: Any, part_key: Any = None,
                      bbox_nm: Any = None, size_nm: Any = None, refs: Any = None) -> list[str]:
    """**替换零部件**：发新 uuid + 沿用原位姿，配合与父子层级**一起改指向**（关系尽量保留）。

    位姿 / 固定态 / 半径原样继承；`part_key` / `bbox_nm`(`size_nm`) / `refs` 给了才覆盖。
    配合的参考对改成新 uuid ⇒ 关系不丢；**本层不做求解**，替换后配合是否满足仍由
    `mate_check()` 现算（残差在替换前后逐位相同，正是"关系没被换丢"的证据）。

    返回：替换后的新组件 uuid 列表（`[new_uuid]`）。
    """
    store = _store(sk_or_doc)
    old = _component(store, component)
    old_uuid = str(old.get("uuid"))
    new = new_uuid()
    row = dict(old)
    row["uuid"] = new
    row["refs"] = {name: {"point_nm": list(item["point_nm"]), "dir": list(item["dir"])}
                   for name, item in (old.get("refs") or {}).items()}
    row["rotation"] = [list(line) for line in (old.get("rotation") or _mat_identity())]
    row["at_nm"] = [int(item) for item in old.get("at_nm", [0, 0, 0])]
    if part_key is not None:
        row["part_key"] = _text(part_key, what="part_key")
    if bbox_nm is not None or size_nm is not None:
        box = _bbox(bbox_nm, size_nm)
        row["bbox_nm"] = None if box is None else [list(box[0]), list(box[1])]
    if refs is not None:
        row["refs"] = _named_refs_copy(_default_named_refs(refs))
    for mate in _table(store, "mates"):
        for pair in mate.get("refs") or []:
            if isinstance(pair, list) and pair and str(pair[0]) == old_uuid:
                pair[0] = new
    table = _table(store, "components")
    for other in table:
        if str(other.get("parent")) == old_uuid:
            other["parent"] = new
        kids = other.get("children")
        if isinstance(kids, list):
            other["children"] = [new if str(item) == old_uuid else str(item) for item in kids]
    table[table.index(old)] = row
    return [new]


def component_radius_set(sk_or_doc: Any, *, component: Any, radius_nm: Any) -> list[str]:
    """**设圆柱类半径**（整数纳米，≥0）：相切配合要靠它算"参考点距 = r1 + r2"。

    没设过的件记 `radius_nm=-1`（未采）⇒ 相切配合在 `mate_check()` 里记 `unmeasured`，
    不拿 0 当半径。

    返回：被设置的组件 uuid 列表（`[component]`）。
    """
    store = _store(sk_or_doc)
    row = _component(store, component)
    radius = _nm(radius_nm, what="radius_nm")
    if radius < 0:
        raise _fail("out_of_range", "radius_nm must be >= 0", value=int(radius))
    row["radius_nm"] = int(radius)
    return [str(row.get("uuid"))]


def fix_component(sk_or_doc: Any, *, components: Any, fixed: bool = True) -> list[str]:
    """**固定 / 浮动**：写 `fixed` 标志（`float_component()` = 本函数 `fixed=False`）。

    后果是硬的：固定件被 `move_component` / `rotate_component` 拒动
    （`CadError(reason="component_fixed")`，要强动必须显式 `allow_fixed=True`）。

    返回：被改标志的组件 uuid 列表（按给定顺序，去重）。
    """
    store = _store(sk_or_doc)
    flag = _flag(fixed, what="fixed")
    out: list[str] = []
    for ref in _component_refs(components):
        row = _component(store, ref)
        row["fixed"] = flag
        if str(row.get("uuid")) not in out:
            out.append(str(row.get("uuid")))
    return out


def float_component(sk_or_doc: Any, *, components: Any) -> list[str]:
    """**浮动**（解除固定）：等价 `fix_component(..., fixed=False)`。返回被解除的 uuid 列表。"""
    return fix_component(sk_or_doc, components=components, fixed=False)


# ======================================================================================
# 操作：相切 / 锁定 / 改配合 / 压缩配合 / 过定义诊断
# ======================================================================================


def _find_mate(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 找配合行（找不到报 `unknown_mate`，不做下标引用）。"""
    key = _text(ref, what="mate")
    for row in _table(store, "mates"):
        if str(row.get("uuid")) == key:
            return row
    raise _fail("unknown_mate", "no mate with that uuid", mate=key,
                known=[str(row.get("uuid")) for row in _table(store, "mates")])


def _record_simple_mate(sk_or_doc: Any, *, kind: str, refs: Any, tolerance_nm: Any, label: Any,
                        extra: Any = None) -> list[str]:
    """无目标值的配合（相切 / 锁定）落一条记录：先校验组件与参考名都在，再入表（早失败）。"""
    store = _store(sk_or_doc)
    pairs = _ref_pair(refs)
    if pairs[0] == pairs[1]:
        raise _fail("degenerate_mate", "a mate needs two distinct references",
                    ref="%s:%s" % (pairs[0][0], pairs[0][1]))
    for component_uuid, ref_name in pairs:
        _ref_geometry(_component(store, component_uuid), ref_name)
    uuid = new_uuid()
    row = {"uuid": uuid, "kind": kind,
           "refs": [[pairs[0][0], pairs[0][1]], [pairs[1][0], pairs[1][1]]],
           "value_nm": UNMEASURED, "value_deg": None, "flip": False,
           "tolerance_nm": _tolerance_nm(tolerance_nm), "tolerance_deg": None,
           "label": _text(label, what="label") if label is not None else NA,
           "status": "recorded", "measured_nm": UNMEASURED, "measured_deg": None,
           "suppressed": False}
    if isinstance(extra, dict):
        row.update(extra)
    _table(store, "mates").append(row)
    return [uuid]


def mate_tangent(sk_or_doc: Any, *, refs: Any, tolerance_nm: Any = None, label: Any = None) -> list[str]:
    """**相切配合**：两参考点距离 = 两件半径之和（半径先走 `component_radius_set()`）。

    任一件没设半径 ⇒ `mate_check()` 记 `unmeasured`（未采），**不**当"满足"。
    返回：`[配合 uuid]`。
    """
    return _record_simple_mate(sk_or_doc, kind="tangent", refs=refs, tolerance_nm=tolerance_nm,
                               label=label)


def mate_lock(sk_or_doc: Any, *, refs: Any, tolerance_nm: Any = None, label: Any = None) -> list[str]:
    """**锁定配合**：记下两参考点当时的相对位移，之后 `mate_check()` 量它有没有漂。

    本层不做求解 ⇒ 锁定不"拖着另一件走"，但漂移一定被量出来（`satisfied=False`），
    绝不静默当满足。返回：`[配合 uuid]`。
    """
    store = _store(sk_or_doc)
    pairs = _ref_pair(refs)
    if pairs[0] == pairs[1]:
        raise _fail("degenerate_mate", "a mate needs two distinct references",
                    ref="%s:%s" % (pairs[0][0], pairs[0][1]))
    first = _world_mate_ref(store, pairs[0])
    second = _world_mate_ref(store, pairs[1])
    offset = [second["point"][axis] - first["point"][axis] for axis in range(3)]
    return _record_simple_mate(sk_or_doc, kind="lock", refs=refs, tolerance_nm=tolerance_nm, label=label,
                               extra={"lock_offset_nm": [int(round(item)) for item in offset]})


def edit_mate(sk_or_doc: Any, *, mate: Any, value_nm: Any = None, value_deg: Any = None,
              flip: Any = None, tolerance_nm: Any = None, tolerance_deg: Any = None,
              label: Any = None) -> list[str]:
    """**改配合**：按 kind 只收该收的值（`distance` 收 `value_nm`、`angle` 收 `value_deg`），
    给错 ⇒ `unexpected_value`（不静默忽略）。改完状态退回 `recorded`（等下次 `mate_check()`
    现算），**绝不**把"改过"写成"已满足"。

    返回：被改的配合 uuid 列表（`[mate]`）。
    """
    store = _store(sk_or_doc)
    row = _find_mate(store, mate)
    kind = str(row.get("kind"))
    if value_nm is not None:
        if kind != "distance":
            raise _fail("unexpected_value", "value_nm is only meaningful for a distance mate", kind=kind)
        distance = _nm(value_nm, what="value_nm")
        if distance < 0:
            raise _fail("out_of_range", "value_nm must be >= 0 (use flip for the negative side)",
                        value=int(distance))
        row["value_nm"] = int(distance)
    if value_deg is not None:
        if kind != "angle":
            raise _fail("unexpected_value", "value_deg is only meaningful for an angle mate", kind=kind)
        degrees = abs(_angle(value_deg, what="value_deg"))
        if degrees > 180.0:
            raise _fail("out_of_range", "value_deg must be within 0..180", value=float(degrees))
        row["value_deg"] = float(degrees)
    if flip is not None:
        row["flip"] = bool(_flag(flip, what="flip"))
    if tolerance_nm is not None:
        row["tolerance_nm"] = _tolerance_nm(tolerance_nm)
    if tolerance_deg is not None:
        row["tolerance_deg"] = _tolerance_deg(tolerance_deg)
    if label is not None:
        row["label"] = _text(label, what="label")
    row["status"], row["measured_nm"], row["measured_deg"] = "recorded", UNMEASURED, None
    return [str(row.get("uuid"))]


def suppress_mate(sk_or_doc: Any, *, mates: Any, suppressed: bool = True) -> list[str]:
    """**压缩 / 解压缩配合**：置 `suppressed` 标志；压缩后 `mate_check()` **不再强制**它，
    状态记 `suppressed`（机读可见）—— 不是"删掉"，也不是"满足"。

    返回：被改标志的配合 uuid 列表（按给定顺序）。
    """
    store = _store(sk_or_doc)
    flag = _flag(suppressed, what="suppressed")
    out: list[str] = []
    for ref in _component_refs(mates):
        row = _find_mate(store, ref)
        row["suppressed"] = flag
        row["status"] = "suppressed" if flag else "recorded"
        out.append(str(row.get("uuid")))
    return out


def diagnose_mates(sk_or_doc: Any) -> list[str]:
    """**过定义 / 冗余诊断**：同一 `(kind, 参考组件对)` 上有 >1 条配合 ⇒ 全部列为冗余/冲突。

    返回：冗余配合 uuid 列表（按插入序；没有冗余 ⇒ `[]` = 真为零）。只读，不写盘。
    """
    store = _store(sk_or_doc, create=False)
    if store is None:
        return []
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for row in _table(store, "mates"):
        pairs = row.get("refs") or []
        members = tuple(sorted(str(pair[0]) for pair in pairs
                               if isinstance(pair, (list, tuple)) and pair))
        groups.setdefault((str(row.get("kind")), members), []).append(str(row.get("uuid")))
    out: list[str] = []
    for _key, uuids in groups.items():
        if len(uuids) > 1:
            out.extend(uuids)
    return out


# ======================================================================================
# 操作：爆炸挪件 / 爆炸直线（数据面仍由 exploded_view() 出，这里只是消费它）
# ======================================================================================


def _explode_record(store: dict[str, Any], uuid: Any) -> dict[str, Any]:
    """按 uuid 取爆炸记录（找不到报 `no_explode_record`，不静默重算一份）。"""
    key = str(uuid)
    for row in _table(store, "explodes"):
        if str(row.get("uuid")) == key:
            return row
    raise _fail("no_explode_record", "no explode record with that uuid", explode=key)


def explode_apply(sk_or_doc: Any, *, explode: Any = 1.0, mode: Any = "radial", axis: Any = "z",
                  step_nm: Any = None, center_nm: Any = None) -> list[str]:
    """**按爆炸数据真挪件**：先算 `exploded_view()` 的数据，再把每件 `at_nm += vector_nm`。

    与 `exploded_view()` 的分工写死：那个只出数据（位姿不动），这个才动位姿；挪的量**就是数据里的
    `vector_nm`** ⇒ "真挪了多少"与"数据说挪多少"永远一致。未采（无包围盒）的件不挪、不返回。

    返回：被移动的组件 uuid 列表（按爆炸记录里的顺序）。
    """
    store = _store(sk_or_doc)
    record = _explode_record(store, exploded_view(sk_or_doc, explode=explode, mode=mode, axis=axis,
                                                  step_nm=step_nm, center_nm=center_nm)[0])
    out: list[str] = []
    for item in record.get("items") or []:
        row = _component(store, item["component"])
        vector = [int(value) for value in item["vector_nm"]]
        row["at_nm"] = [int(row.get("at_nm", [0, 0, 0])[axis_index]) + vector[axis_index]
                        for axis_index in range(3)]
        out.append(str(row.get("uuid")))
    return out


def explode_line_sketch(sk_or_doc: Any, *, explode: Any = 1.0, mode: Any = "radial", axis: Any = "z",
                        step_nm: Any = None, center_nm: Any = None, layer: Any = "explode") -> list[str]:
    """**爆炸直线草图**：给每件造一条"原位 → 爆炸位"的线段，写进本层自己的表
    （`extra["sw_assembly"]["explode_lines"]`；**不碰 `doc.sketches`** —— 本层不写 L1 字段，
    见模块 docstring「有意限定的边界」第 4 条）。

    线段幂等：同 `(爆炸记录, 组件)` 只留一条（uuid 稳定，重算 = 刷新）。
    返回：线段 uuid 列表（每件一条；未采件不编线段、不返回）。
    """
    store = _store(sk_or_doc)
    record_uuid = exploded_view(sk_or_doc, explode=explode, mode=mode, axis=axis, step_nm=step_nm,
                               center_nm=center_nm)[0]
    record = _explode_record(store, record_uuid)
    name = _text(layer, what="layer")
    table = _table(store, "explode_lines")
    index = {str(row.get("uuid")): row for row in table}
    out: list[str] = []
    for item in record.get("items") or []:
        component = _component(store, item["component"])
        uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "cadcore.sw_assembly.explode_line|%s|%s"
                               % (record_uuid, item["component"])))
        row = {"uuid": uuid, "kind": "line", "layer": name, "component": str(component.get("uuid")),
               "from_nm": [int(value) for value in component.get("at_nm", [0, 0, 0])],
               "to_nm": [int(value) for value in item["to_nm"]], "explode": record_uuid}
        if uuid in index:
            index[uuid].update(row)
        else:
            table.append(row)
            index[uuid] = row
        out.append(uuid)
    return out


# ======================================================================================
# 配合量测总入口：六种原配合 + 相切 / 锁定
# ======================================================================================


def _component_radius(store: dict[str, Any], row: dict[str, Any], index: int) -> int | None:
    """配合第 `index` 个参考所属组件的半径；没设（`-1`）⇒ `None` = 未采（不拿 0 冒充）。"""
    pairs = row.get("refs") or []
    if index >= len(pairs) or not isinstance(pairs[index], (list, tuple)) or not pairs[index]:
        return None
    component = _component(store, str(pairs[index][0]))
    radius = component.get("radius_nm")
    if isinstance(radius, bool) or not isinstance(radius, int) or radius < 0:
        return None
    return int(radius)


def _measure_any(store: dict[str, Any], row: dict[str, Any], first: dict[str, Any],
                 second: dict[str, Any], *, value_nm: int, value_deg: float | None, flip: bool,
                 tol_nm: int, tol_deg: float) -> tuple[int, float | None, bool | None]:
    """`_measure()` 的族内总入口：先处理本层后加的 `tangent` / `lock`，其余原样下传。

    三态与 `_measure()` 完全一致：`measured_nm=-1` = 量不出、`satisfied=None` = 第三种状态。
    """
    kind = str(row.get("kind"))
    if kind == "tangent":
        radius_a = _component_radius(store, row, 0)
        radius_b = _component_radius(store, row, 1)
        if first["point"] is None or second["point"] is None or radius_a is None or radius_b is None:
            return UNMEASURED, None, None
        measured = int(round(_norm3(_sub3(second["point"], first["point"]))))
        return measured, None, bool(abs(measured - (radius_a + radius_b)) <= tol_nm)
    if kind == "lock":
        want = row.get("lock_offset_nm")
        if first["point"] is None or second["point"] is None:
            return UNMEASURED, None, None
        if not isinstance(want, (list, tuple)) or len(want) != 3:
            return UNMEASURED, None, None
        drift = _norm3([(second["point"][axis] - first["point"][axis]) - float(want[axis])
                        for axis in range(3)])
        return int(round(drift)), None, bool(drift <= tol_nm)
    return _measure(kind, first, second, value_nm=value_nm, value_deg=value_deg, flip=flip,
                    tol_nm=tol_nm, tol_deg=tol_deg)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "insert_component": insert_component,
    "component_pattern": component_pattern,
    "measure_distance": measure_distance,
    "add_subassembly": add_subassembly,
    "attach_component": attach_component,
    "replace_component": replace_component,
    "component_radius_set": component_radius_set,
    "fix_component": fix_component,
    "float_component": float_component,
    "mate_add": mate_add,
    "mate_coincident": mate_coincident,
    "mate_parallel": mate_parallel,
    "mate_perpendicular": mate_perpendicular,
    "mate_tangent": mate_tangent,
    "mate_concentric": mate_concentric,
    "mate_distance": mate_distance,
    "mate_angle": mate_angle,
    "mate_lock": mate_lock,
    "mate_delete": mate_delete,
    "mate_check": mate_check,
    "edit_mate": edit_mate,
    "suppress_mate": suppress_mate,
    "diagnose_mates": diagnose_mates,
    "move_component": move_component,
    "rotate_component": rotate_component,
    "interference_check": interference_check,
    "exploded_view": exploded_view,
    "explode_apply": explode_apply,
    "explode_line_sketch": explode_line_sketch,
}


# ======================================================================================
# 自检
# ======================================================================================


def _expect_error(reason: str, call: Callable[[], Any], label: str) -> None:
    """阴性对照：必须抛出**指定 reason** 的 `CadError`，否则自检当场炸（尺子看得见红）。"""
    try:
        call()
    except CadError as exc:
        assert exc.reason == reason, "%s: reason=%r expected %r" % (label, exc.reason, reason)
        return
    raise AssertionError("%s: expected CadError(reason=%r)" % (label, reason))


def selftest() -> int:
    """自检：族注册 / 插入与位姿 / 六种配合含阴性对照 / 移动旋转与固定件 / 干涉三态 / 爆炸数据与往返。

    五组检查全部通过 ⇒ 打印 `SW_ASSEMBLY_OPS verdict=PASS ops=<N>` 并返回 0，否则返回 1。
    """
    checks: list[str] = []
    mm = NM_PER_MM

    def ok(name: str) -> None:
        checks.append(name)

    # ---- 1. 族注册 ------------------------------------------------------------------
    assert FAMILY == "sw_assembly", FAMILY
    assert len(OPERATIONS) >= 14, len(OPERATIONS)
    assert all(callable(item) for item in OPERATIONS.values()), "every operation must be callable"
    ok("family_and_ops")

    doc: dict[str, Any] = {"sketches": [], "features": [], "extra": {}, "meta": {"doc_uuid": "asm-1"}}
    plate = insert_component(doc, part_key="plate", at_nm=(0, 0, 0),
                             bbox_nm=[[0, 0, 0], [100 * mm, 100 * mm, 10 * mm]],
                             refs={"face": {"point_nm": [0, 0, 10 * mm], "dir": [0, 0, 1]},
                                   "edge": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]}})[0]
    pin = insert_component(doc, part_key="pin", at_nm=(0, 0, 10 * mm),
                           bbox_nm=[[-5 * mm, -5 * mm, 0], [5 * mm, 5 * mm, 20 * mm]],
                           refs={"face": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]}})[0]
    blank = insert_component(doc, part_key="blank", at_nm=(500 * mm, 500 * mm, 0))[0]
    assert len(plate) == 36 and len(pin) == 36 and len(blank) == 36, "uuids are uuid4 strings"
    counts = assembly_facts(doc)
    assert counts["component_count"] == 3, counts
    assert counts["bbox_unmeasured_count"] == 1, counts
    assert counts["mate_count"] == 0 and counts["mate_satisfied"] == 0, "0 = truly zero"
    pose = component_pose(doc, component=pin)
    assert pose["at_nm"] == [0, 0, 10 * mm] and pose["euler_deg"] == [0.0, 0.0, 0.0], pose
    ok("insert_and_pose")

    # ---- 2. 六种配合 + 阴性对照 ------------------------------------------------------
    facing = mate_coincident(doc, refs=[[plate, "face"], [pin, "face"]])[0]
    assert mate_check(doc) == [], "a coincident mate on matching frames is satisfied"
    assert assembly_facts(doc)["mate_satisfied"] == 1, assembly_facts(doc)
    perp = mate_perpendicular(doc, refs=[[plate, "edge"], [plate, "face"]])[0]
    parallel = mate_parallel(doc, refs=[[plate, "face"], [pin, "face"]])[0]
    concentric = mate_concentric(doc, refs=[[plate, "face"], [pin, "face"]])[0]
    zero_gap = mate_distance(doc, refs=[[plate, "face"], [pin, "face"]], value_nm=0)[0]
    square = mate_angle(doc, refs=[[plate, "edge"], [plate, "face"]], value_deg=90.0)[0]
    assert mate_check(doc) == [], "all six mate kinds line up on this fixture"
    assert len({facing, perp, parallel, concentric, zero_gap, square}) == 6, "distinct mate uuids"
    bad = mate_distance(doc, refs=[[plate, "face"], [pin, "face"]], value_nm=5 * mm)[0]
    hit = mate_check(doc)
    assert hit == [bad], "the ruler sees the violated distance mate"
    assert len(hit) == 1, hit
    _expect_error("duplicate_mate", lambda: mate_coincident(doc, refs=[[plate, "face"], [pin, "face"]]),
                  "duplicate mate")
    _expect_error("unknown_mate_kind", lambda: mate_add(doc, kind="gear", refs=[[plate, "face"], [pin, "face"]]),
                  "unknown kind")
    _expect_error("unknown_reference", lambda: mate_parallel(doc, refs=[[plate, "nope"], [pin, "face"]]),
                  "unknown ref name")
    _expect_error("missing_value", lambda: mate_distance(doc, refs=[[plate, "face"], [pin, "face"]]),
                  "distance without value_nm")
    assert mate_delete(doc, mates=[bad]) == [bad], "mate_delete returns what it removed"
    ok("six_mate_kinds_with_negative_controls")

    # ---- 3. 移动 / 旋转 / 固定件 ------------------------------------------------------
    assert move_component(doc, components=[pin], delta_nm=(0, 0, 10 * mm)) == [pin], "move returns uuid"
    assert component_pose(doc, component=pin)["at_nm"] == [0, 0, 20 * mm], "integer nm translate"
    assert rotate_component(doc, components=[plate], angle_deg=90.0, axis="z",
                            center_nm=(0, 0, 0)) == [plate], "rotate returns uuid"
    rotated = component_pose(doc, component=plate)
    assert rotated["euler_deg"] == [0.0, 0.0, 90.0], rotated["euler_deg"]
    world = _world_mate_ref(_store(doc), (plate, "edge"))
    assert [round(item, 9) for item in world["dir"]] == [0.0, 1.0, 0.0], world["dir"]
    grounded = insert_component(doc, part_key="base", at_nm=(0, 0, -50 * mm), fixed=True)[0]
    _expect_error("component_fixed", lambda: move_component(doc, components=[grounded], delta_nm=(1, 0, 0)),
                  "fixed component refuses to move")
    assert move_component(doc, components=[grounded], delta_nm=(1, 0, 0), allow_fixed=True) == [grounded], \
        "allow_fixed=True is the explicit override"
    ok("move_rotate_and_fixed_guard")

    # ---- 4. 干涉检查：真体积交 / 清爽 / 未采 三态 --------------------------------------
    # 上一步把 pin 抬了 10mm（测整数纳米平移），这里先把它压回板内 5mm：夹具必须**真有体积交**，
    # 否则这条断言量的是"贴面/间隙"而不是"相交"。
    assert move_component(doc, components=[pin], delta_nm=(0, 0, -15 * mm)) == [pin], "seat pin into plate"
    hit_pair = interference_check(doc, pairs=[[plate, pin]], tolerance_nm=0)
    assert len(hit_pair) == 1, hit_pair
    seated = [row for row in _table(_store(doc), "interferences")
              if row["pair"] == sorted([plate, pin])][0]
    assert seated["method"] == "solid" and seated["status"] == "collision", seated
    # 真体积交：x 向 5mm（板 [-100,0] ∩ 销 [-5,5]）× y 向 5mm × z 向 5mm = 125 mm³（解析值）。
    # 体积是 float64 散度求和 ⇒ 相对误差 ~1e-15（1e20 nm³ 量级下 ~1e5 nm³），判据用相对容差。
    _want_volume = 125 * mm ** 3
    assert abs(seated["volume_nm3"] - _want_volume) <= _want_volume * 1e-9, seated["volume_nm3"]
    assert seated["aabb_false_positive"] is False, seated
    assert assembly_facts(doc)["interference_collision"] == 1, assembly_facts(doc)
    assert move_component(doc, components=[pin], delta_nm=(0, 0, 30 * mm)) == [pin], "separate the pin"
    cleared = interference_check(doc, pairs=[[plate, pin]], tolerance_nm=0)
    assert cleared == [], "clear pair returns [] (truly zero hits)"
    still = interference_check(doc, pairs=[[plate, pin]], tolerance_nm=0, only_hits=False)
    assert len(still) == 1, "only_hits=False also returns the clear record"
    unknown = interference_check(doc, pairs=[[plate, blank]], tolerance_nm=0)
    assert len(unknown) == 1, "an unmeasured bbox is reported, never silently clear"
    assert assembly_facts(doc)["interference_unmeasured"] == 1, assembly_facts(doc)
    _expect_error("unknown_component", lambda: interference_check(doc, pairs=[[plate, "nope"]]),
                  "unknown pair member")
    ok("interference_three_states")

    # ---- 5. 爆炸视图数据 + 幂等 + 位姿不动 + L1 往返 -----------------------------------
    before = component_pose(doc, component=plate)["at_nm"]
    first = exploded_view(doc, explode=2.0, mode="radial", step_nm=10 * mm)
    again = exploded_view(doc, explode=2.0, mode="radial", step_nm=10 * mm)
    assert first == again and len(first) == 1, "same parameters are idempotent"
    record = _table(_store(doc), "explodes")[-1]
    moved = [item for item in record["items"] if any(item["vector_nm"])]
    assert len(moved) >= 2, "radial explode moves the measured parts"
    assert record["skipped_state"] == UNMEASURED and record["skipped"], "unmeasured part is skipped, not faked"
    assert component_pose(doc, component=plate)["at_nm"] == before, "explode data must not move anything"
    axis_view = exploded_view(doc, explode=1.0, mode="axis", axis="z", step_nm=5 * mm)
    assert len(axis_view) == 1 and axis_view[0] != first[0], "axis mode is a distinct record"
    from .. import docmodel  # 自检专用：族模块本身不依赖文档层

    document = docmodel.new_document()
    document.meta["doc_uuid"] = "asm-2"
    # 距离配合**沿第一参考方向投影**：夹具必须给 x 向参考（origin 的方向是 z ⇒ 投影恒为 0）。
    uuid_a = insert_component(document, part_key="a", at_nm=(0, 0, 0), size_nm=(10 * mm, 10 * mm, 10 * mm),
                              refs={"x": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]}})[0]
    uuid_b = insert_component(document, part_key="b", at_nm=(20 * mm, 0, 0),
                              size_nm=(10 * mm, 10 * mm, 10 * mm),
                              refs={"x": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]}})[0]
    assert mate_distance(document, refs=[[uuid_a, "x"], [uuid_b, "x"]], value_nm=20 * mm), "mate on doc"
    assert mate_check(document) == [], "document-backed mate is satisfied"
    assert docmodel.check_invariants(document) == [], "assembly table must not break L1 invariants"
    again_doc = docmodel.document_from_l1(docmodel.document_to_l1(document))
    assert len(again_doc.extra["sw_assembly"]["components"]) == 2, "assembly table survives the L1 round trip"
    ok("explode_data_idempotent_and_roundtrip")

    assert len(checks) == 6, checks  # 六组：族注册 / 插入位姿 / 六种配合 / 移动旋转 / 干涉三态 / 爆炸与往返
    print("SW_ASSEMBLY_OPS verdict=PASS ops=%d checks=%d" % (len(OPERATIONS), len(checks)))
    return 0


def main() -> int:
    """自检入口（失败即非零退出并打 FAIL 行，不吞异常）。"""
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败
        print("SW_ASSEMBLY_OPS verdict=FAIL ops=%d error=%s: %s"
              % (len(OPERATIONS), type(exc).__name__, exc))
        raise


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())
