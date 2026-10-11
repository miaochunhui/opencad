#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w9 —— 装配族实现片 w9：**配合对齐方向控制**（Aligned / Anti-Aligned）。

一句话用途
----------
补上装配族清单里仍缺的第 0 条：给**已存在的**配合读出 / 设定 **对齐方向**（`aligned` / `anti_aligned`），
并真的把可动零部件**翻到目标那一侧**（刚体最小变换），而不是只改一个标记位。
对照件：`docs/research/cad-parity/features/sw-assembly.json` must_have 第 14 项「配合对齐方向控制」
（alias「对齐 / 反转对齐（Aligned / Anti-Aligned）」，why「同一配合选错方向零件会翻到另一侧」）；
状态件 `docs/research/cad-parity/status/sw-assembly.json` 记 **missing**，证据 = 严格词边界 grep
`aligned|anti_aligned|antialigned` 在 cadcore+opencad+dxf 命中 0。

缺口到底是什么（不做重复劳动）
------------------------------
族模块 `cadcore/ops/sw_assembly.py` 已能加配合、并在 `mate_add(..., flip=...)` 里**一次性地**记录方向，
`mate_check()` 也已按 `flip` 现算残差 —— 但缺三样：
1. 没有 `aligned` / `anti_aligned` 这套**一等的方向词汇**（读不出"这条配合现在是哪个方向"）；
2. 没有**事后改方向**的动作（配合建好就不能翻）；
3. 更关键：翻方向**没有几何后果**（SolidWorks 里翻方向会把零件挪到另一侧）。
本片只补这三样，复用族模块的表结构与检查器，不重写配合入口。

冻结接口
--------
- ``FAMILY = "sw_assembly"``；
- ``OPERATIONS: dict[str, Callable]``（三个操作，见下）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]`` 返回 **uuid 列表**；长度一律整数纳米（nm）；
  错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason 见下表）。

表落在哪（与族模块同口径，只加键不改键）
----------------------------------------
文档（有 ``sketches`` 列表）⇒ ``doc.extra["sw_assembly"]``；草图形态映射/对象 ⇒ 自己身上的
``sw_assembly`` 键。表结构 ``{"schema": 1, "components": [], "mates": [], "interferences": [],
"explodes": []}``。参考几何口径与族模块 `_ref_geometry` **逐字一致**：按名字取 `refs`，
**取不到就报 `unknown_reference`**（本片不隐式补 `origin`；`origin` 由 `insert_component` 种下）。

唯一真源：**不新增 `alignment` 字段**
-------------------------------------
对齐方向的真源就是配合行里**已有的** `flip` 布尔位（族模块 `mate_add` 写入、`_measure` 消费）：
``flip=False`` ⇔ `aligned`，``flip=True`` ⇔ `anti_aligned`。本片**只写 `flip`**，
仓库里不多出第二份真源（同族模块"矩阵与欧拉角不存两份"的口径）。读侧提供 `mate_alignment_get`。

哪些配合种类可施加对齐（判据 = 残差真的依赖这个位）
----------------------------------------------------
`coincident` / `parallel` / `concentric` / `distance` 四种 —— 依据是族模块 `_measure()` 在这四种里
真的读了 `flip`（域内可证）。其余种类（`perpendicular` / `angle` / `tangent` / `lock` 等）⇒
``CadError(reason="unsupported_alignment")``，**不做"改了标记但残差不变"的假动作**；
读侧把它们如实报成 ``alignment="unsupported"``（不静默藏起来）。

几何语义（真后果，写清楚再做）
------------------------------
先按"哪一件能动"定可动件：`side="auto"`（缺省）优先第二参考所属组件，它被显式标记为固定时改用第一件；
两件都显式固定 ⇒ ``CadError(reason="fixed_component")``；固定位**未采**（组件行里没有该键）⇒ 当浮动，
这是本层已知边界。被压缩（`suppressed`）的配合只改方向、不挪件（压缩的配合本就不强制）。

- `coincident` / `parallel` / `concentric`：把可动件的参考方向用**最小旋转**转到目标方向
  （`aligned` ⇒ 与另一参考同向；`anti_aligned` ⇒ 反向），旋转轴**过该件自己的参考点** ⇒
  **参考点不动、只翻方向**（点距 / 轴向偏移这些项不在本操作范围内，本层仍不是求解器）。
  已经同向 ⇒ 零位移（幂等，重复调用不会来回翻）。
- `distance`：沿第一参考方向**平移**可动件，使有符号投影 = `+value_nm`（aligned）/ `-value_nm`（anti），
  与族模块 `_measure()` 的 `flip` 口径逐字一致。

数值口径：`at_nm` 写回前四舍五入到整数纳米（每次 ≤ 1 nm/轴，远小于缺省容差 1 µm）；
`rotation` 是 3×3 浮点矩阵（`<1e-12` 的项清零），仍是位姿的唯一真源。

有意写下来的边界（防下游靠猜）
------------------------------
1. **仍不是约束求解器**：本片只做"这一条配合的方向"这一件事，不重解其它配合；
   被移动组件牵连到的**其它**配合行会被就地置为 ``status="recorded"``、`measured_*=未采`
   （防止陈旧的 `satisfied` 活下来），要拿新状态请重新 `mate_check()`。
2. 返回值只含**调用方点名的**配合 uuid（按给定顺序），不含被牵连失效的那些。
3. 原子性：一次调用里**先全量验证**（uuid 存在 / 种类可对齐 / 几何可解析 / 距离值合法 / 有可动件），
   全部通过才动手 ⇒ 不会落"半个改动"。
4. 非 docstring 的字符串字面量一律 ASCII。

错误 reason 表
--------------
`unknown_mate`（点名了表里没有的配合）/ `unknown_component` / `unknown_reference` /
`unsupported_alignment`（该种类不能施加对齐）/ `bad_alignment`（不是 aligned/anti_aligned）/
`bad_argument`（mates/side 形状不对）/ `empty_mates` / `fixed_component` / `missing_value`
（distance 缺 `value_nm`）/ `unmeasured_reference`（参考方向零长）/ `bad_mate`（配合行 refs 不是两条）/
`bad_reference`（不是 3 向量）/ `not_writable`（表落不上去）。

自检（文件自带，不依赖 pytest；交叉验证走真族模块的 `mate_check`）：

    python3 cadcore/ops/features/sw_assembly__w9.py
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/sw_assembly__w9.py 的引导
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__))))))
    __package__ = "cadcore.ops.features"

import math
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from cadcore.errors import CadError
#: 表定位的唯一真源：本片与族模块**共用同一张表**（`_holder` / `_store`），不再各解一遍（RC-A2）。
from cadcore.ops import sw_assembly as _family

FAMILY = "sw_assembly"

UNMEASURED = -1
NA = "NA"

ALIGNED = "aligned"
ANTI_ALIGNED = "anti_aligned"
UNSUPPORTED = "unsupported"
ALIGNMENTS = (ALIGNED, ANTI_ALIGNED, UNSUPPORTED)

# 判据：族模块 cadcore/ops/sw_assembly.py 的 _measure() 只在这四种里消费 flip
# （coincident / parallel / concentric / distance 四处）。别的种类改了位也不改残差 ⇒ 拒收。
ALIGNMENT_KINDS = ("coincident", "parallel", "concentric", "distance")

_SIDES = ("auto", "first", "second")
_IDENTITY3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


# ======================================================================================
# 小工具：失败 / 结构访问 / 表定位
# ======================================================================================


def _fail(reason: str, message: str = "", **facts: Any) -> CadError:
    """组一个 `CadError(reason=...)`；只依赖 `reason` 这个冻结字段（message/facts 尽力而为）。"""
    try:
        raise CadError(reason=reason, message=message, **facts)
    except TypeError:
        pass
    try:
        raise CadError(reason=reason)
    except TypeError:
        pass
    raise CadError(reason)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, _MutableMappingABC):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, _MutableMappingABC):
        obj[key] = value
        return
    try:
        setattr(obj, key, value)
    except Exception:
        raise _fail("not_writable", "cannot store the sw_assembly table on this object", key=key)


def _document_extra(obj: Any) -> Any:
    """文档判定 = 有 `sketches` 列表（与族模块一致）；返回挂表容器，非文档返回 None。

    容器定位**唯一真源** = 族模块 `sw_assembly._holder`（本片不再自己解一遍表位置）。

    B6 缺陷 RC-A2 的落点：本片此前在文档没有 `extra` 容器时**凭空建一个空 `extra`**，把表放到
    `doc["extra"]["sw_assembly"]`；而族模块把表放在 `doc["sw_assembly"]`（= `extra` 键在 L1 文本里的
    根级落点）。两片口径不一致的实测后果三条：①本片 7 个对齐 op 全部 `unknown_mate(known=[])`；
    ②本片碰过一次文档之后，族模块自己也读不到原表（`component_pose` 报 `unknown_component`，
    `mate_check` 静默返回 `[]`，"表读不到"被伪装成"没有配合"）；③在 L1 根上凭空多出一个非法键
    `extra`（`docmodel._KNOWN_TOP_LEVEL` 里没有它 ⇒ 会变成 `unknown_field_preserved` 伪诊断）。
    """
    if not isinstance(_get(obj, "sketches"), list):
        return None
    return _family._holder(obj)


def _store(sk_or_doc: Any) -> dict[str, Any]:
    """定位族表：**直接复用族模块的 `_store`**（文档走 `extra`/根级 `sw_assembly`，草图走自身）。"""
    return _family._store(sk_or_doc)


def _rows(table: dict[str, Any], key: str) -> list[Any]:
    rows = table.get(key)
    if not isinstance(rows, list):
        rows = []
        table[key] = rows
    return rows


def _find_mate(table: dict[str, Any], mate_uuid: str) -> dict[str, Any]:
    for row in _rows(table, "mates"):
        if isinstance(row, _MappingABC) and str(row.get("uuid")) == str(mate_uuid):
            return row
    raise _fail("unknown_mate", "no mate with that uuid", mate=str(mate_uuid),
                known=[str(r.get("uuid")) for r in _rows(table, "mates")])


def _find_component(table: dict[str, Any], component_uuid: str) -> Any:
    for row in _rows(table, "components"):
        if isinstance(row, _MappingABC) and str(row.get("uuid")) == str(component_uuid):
            return row
    return None


# ======================================================================================
# 小工具：入参 / 向量与旋转
# ======================================================================================


def _as_vec3(value: Any, what: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _fail("bad_reference", "a 3-vector is required", what=what)
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise _fail("bad_reference", "a 3-vector must be numeric", what=what)
        out.append(float(item))
    return out


def _flag(value: Any, what: str = "flag") -> bool:
    if isinstance(value, bool):
        return value
    raise _fail("bad_argument", "a bool is required", what=what)


def _side_key(value: Any) -> str:
    key = str(value).strip().lower()
    if key not in _SIDES:
        raise _fail("bad_argument", "side must be auto, first or second", value=str(value))
    return key


def _uuid_list(mates: Any, *, allow_empty: bool) -> list[str]:
    if isinstance(mates, str):
        items = [mates]
    elif isinstance(mates, (list, tuple)):
        items = [str(item) for item in mates]
    else:
        raise _fail("bad_argument", "mates must be a uuid or a list of uuids")
    if not items and not allow_empty:
        raise _fail("empty_mates", "mates must not be empty")
    return items


def _rotation_of(comp: Any) -> list[list[float]]:
    raw = _get(comp, "rotation")
    if raw is None:
        return [list(row) for row in _IDENTITY3]
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise _fail("bad_reference", "component rotation must be 3x3")
    return [_as_vec3(row, "rotation row") for row in raw]


def _at_of(comp: Any) -> list[float]:
    raw = _get(comp, "at_nm")
    if raw is None:
        return [0.0, 0.0, 0.0]
    return _as_vec3(raw, "at_nm")


def _is_fixed(comp: Any) -> Any:
    """三态：True 固定 / False 浮动 / None 未采（没有固定位 ⇒ 当浮动，本层已知边界）。"""
    for key in ("fixed", "is_fixed"):
        value = _get(comp, key, None)
        if isinstance(value, bool):
            return value
    return None


def _sub(a: list[float], b: list[float]) -> list[float]:
    return [a[i] - b[i] for i in range(3)]


def _scale(vec: list[float], factor: float) -> list[float]:
    return [vec[i] * factor for i in range(3)]


def _dot(a: Any, b: Any) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(vec: Any) -> float:
    return math.sqrt(_dot(vec, vec))


def _unit(vec: Any) -> list[float]:
    length = _norm(vec)
    if length <= 0.0:
        raise _fail("unmeasured_reference", "a direction reference is zero-length")
    return [float(vec[i]) / length for i in range(3)]


def _matvec(mat: list[list[float]], vec: list[float]) -> list[float]:
    return [_dot(mat[i], vec) for i in range(3)]


def _matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    out = []
    for i in range(3):
        out.append([sum(left[i][k] * right[k][j] for k in range(3)) for j in range(3)])
    return out


def _clean(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else float(value)


def _skew(axis: list[float]) -> list[list[float]]:
    x, y, z = axis
    return [[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]]


def _axis_angle_rotation(axis: list[float], angle: float) -> list[list[float]]:
    """Rodrigues：绕单位轴 `axis` 转 `angle`（右手定则）的 3×3 旋转矩阵。"""
    u = _unit(axis)
    kmat = _skew(u)
    ksq = _matmul(kmat, kmat)
    sin_a, cos_a = math.sin(angle), math.cos(angle)
    return [[(1.0 if i == j else 0.0) + sin_a * kmat[i][j] + (1.0 - cos_a) * ksq[i][j]
             for j in range(3)] for i in range(3)]


def _perpendicular_axis(direction: list[float]) -> list[float]:
    """与 `direction` 垂直的**确定**轴：取与它最不平行的世界轴 e，u = normalize(dir × e)。"""
    best = None
    for axis in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]):
        weight = abs(_dot(axis, direction))
        if best is None or weight < best[0]:
            best = (weight, axis)
    return _unit(_cross(list(direction), list(best[1])))


def _align_rotation(source: list[float], target: list[float]) -> tuple[Any, float]:
    """把 `source` 转到 `target` 的**最小**旋转 ⇒ `(axis, angle)`；已同向 ⇒ `(None, 0.0)`。"""
    first, second = _unit(source), _unit(target)
    cosine = max(-1.0, min(1.0, _dot(first, second)))
    if cosine > 1.0 - 1e-12:
        return None, 0.0
    if cosine < -1.0 + 1e-12:  # 反向：轴任取一条垂直的确定轴，转 π
        return _perpendicular_axis(first), math.pi
    return _unit(_cross(first, second)), math.acos(cosine)


def _rotate_about(comp: Any, axis: list[float], angle: float, pivot: list[float]) -> None:
    """把组件整体绕"过 `pivot` 的 `axis` 轴"转 `angle`：`rotation`/`at_nm` 一起写回。"""
    rotation = _rotation_of(comp)
    at = _at_of(comp)
    turn = _axis_angle_rotation(axis, angle)
    new_rotation = _matmul(turn, rotation)
    turned = _matvec(turn, _sub(at, pivot))
    new_at = [pivot[i] + turned[i] for i in range(3)]
    _set(comp, "rotation", [[_clean(value) for value in row] for row in new_rotation])
    _set(comp, "at_nm", [int(round(value)) for value in new_at])


def _translate(comp: Any, delta: list[float]) -> None:
    at = _at_of(comp)
    _set(comp, "at_nm", [int(round(at[i] + delta[i])) for i in range(3)])


def _ref_world(table: dict[str, Any], pair: Any) -> tuple[Any, list[float], list[float]]:
    """参考 → `(组件行, 世界点 nm, 世界方向)`。

    口径与族模块 `_ref_geometry` 一致：**按名字取 `refs`，取不到就报 `unknown_reference`**
    （不隐式补 `origin`；`origin` 由 `insert_component` 写进组件行）。
    """
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise _fail("bad_mate", "a mate ref must be [component_uuid, ref_name]")
    comp_uuid, ref_name = str(pair[0]), str(pair[1])
    comp = _find_component(table, comp_uuid)
    if comp is None:
        raise _fail("unknown_component", "no component with that uuid", component=comp_uuid)
    refs = _get(comp, "refs")
    entry = refs.get(ref_name) if isinstance(refs, _MappingABC) else None
    if entry is None:
        known = sorted(str(key) for key in refs.keys()) if isinstance(refs, _MappingABC) else []
        raise _fail("unknown_reference", "component has no such named reference",
                    component=comp_uuid, name=ref_name, known=known)
    if not isinstance(entry, _MappingABC):
        raise _fail("bad_reference", "a named reference must be a mapping",
                    component=comp_uuid, name=ref_name)
    point_local = _as_vec3(entry.get("point_nm", [0, 0, 0]), "point_nm")
    dir_local = _as_vec3(entry.get("dir", [0, 0, 1]), "dir")
    rotation = _rotation_of(comp)
    at = _at_of(comp)
    point_world = [rotation[i][0] * point_local[0] + rotation[i][1] * point_local[1]
                   + rotation[i][2] * point_local[2] + at[i] for i in range(3)]
    return comp, point_world, _matvec(rotation, dir_local)


# ======================================================================================
# 对齐方向：读 / 写 / 几何后果
# ======================================================================================


def _normalize_alignment(value: Any, *, allow_unsupported: bool = False) -> str:
    """`Aligned` / `Anti-Aligned` / `anti_aligned` … → 冻结枚举；不认识的当场报 `bad_alignment`。"""
    compact = str(value).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if compact == "aligned":
        return ALIGNED
    if compact == "antialigned":
        return ANTI_ALIGNED
    if allow_unsupported and compact == "unsupported":
        return UNSUPPORTED
    known = [ALIGNED, ANTI_ALIGNED] + ([UNSUPPORTED] if allow_unsupported else [])
    raise _fail("bad_alignment", "alignment must be aligned or anti_aligned",
                value=str(value), known=known)


def _alignment_of(row: dict[str, Any]) -> str:
    """配合行的对齐方向：真源 = 行里已有的 `flip`（False=aligned，True=anti_aligned）。"""
    if str(row.get("kind")) not in ALIGNMENT_KINDS:
        return UNSUPPORTED
    return ANTI_ALIGNED if bool(row.get("flip")) else ALIGNED


def _pairs_of(row: dict[str, Any]) -> list[Any]:
    refs = row.get("refs")
    if not isinstance(refs, (list, tuple)) or len(refs) != 2:
        raise _fail("bad_mate", "a mate needs exactly two refs", mate=str(row.get("uuid")))
    return [refs[0], refs[1]]


def _distance_value(row: dict[str, Any]) -> int:
    value = row.get("value_nm")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("missing_value", "a distance mate needs a non-negative integer value_nm",
                    mate=str(row.get("uuid")), value_nm=NA if not isinstance(value, int) else int(value))
    return int(value)


def _pick_movable(comp_a: Any, comp_b: Any, side: str) -> Any:
    if side == "first":
        order = [comp_a]
    elif side == "second":
        order = [comp_b]
    else:
        order = [comp_b, comp_a]
    for comp in order:
        if _is_fixed(comp) is not True:
            return comp
    raise _fail("fixed_component", "no movable component on this mate", side=side)


def _plan_mover(table: dict[str, Any], row: dict[str, Any], side: str) -> Any:
    """验证一条配合能不能动、动哪件（触达几何 ⇒ 早失败）；返回可动组件行。"""
    pairs = _pairs_of(row)
    comp_a, _, dir_a = _ref_world(table, pairs[0])
    comp_b, _, dir_b = _ref_world(table, pairs[1])
    if str(row.get("kind")) == "distance":
        _distance_value(row)
    else:
        _unit(dir_a)
        _unit(dir_b)
    return _pick_movable(comp_a, comp_b, side)


def _apply_direction(table: dict[str, Any], row: dict[str, Any], target: str, mover: Any) -> None:
    """把可动件翻到 `target` 那一侧：方向类 = 绕自身参考点最小旋转；distance = 沿轴平移。"""
    pairs = _pairs_of(row)
    comp_a, point_a, dir_a = _ref_world(table, pairs[0])
    comp_b, point_b, dir_b = _ref_world(table, pairs[1])
    first_moves = str(_get(comp_a, "uuid")) == str(_get(mover, "uuid"))
    kind = str(row.get("kind"))
    if kind == "distance":
        direction = _unit(dir_a)
        value = float(_distance_value(row))
        target_projection = value if target == ALIGNED else -value
        current = _dot(_sub(point_b, point_a), direction)
        shift = (current - target_projection) if first_moves else (target_projection - current)
        _translate(mover, _scale(direction, shift))
        return
    if first_moves:
        source, pivot = dir_a, point_a
        toward = dir_b if target == ALIGNED else [-c for c in dir_b]
    else:
        source, pivot = dir_b, point_b
        toward = dir_a if target == ALIGNED else [-c for c in dir_a]
    axis, angle = _align_rotation(source, toward)
    if axis is None:  # 已经同向：零位移（幂等）
        return
    _rotate_about(mover, axis, angle, pivot)


def _invalidate(table: dict[str, Any], moved_uuids: list[str], changed_uuids: list[str]) -> None:
    """把受影响的配合行置为"未复检"：方向变了 / 引用件被挪了 ⇒ 旧的 satisfied 一律作废。"""
    moved = set(str(item) for item in moved_uuids)
    changed = set(str(item) for item in changed_uuids)
    for row in _rows(table, "mates"):
        if str(row.get("status")) == "suppressed":
            continue
        refs = row.get("refs") or []
        touches = any(isinstance(pair, (list, tuple)) and len(pair) == 2 and str(pair[0]) in moved
                      for pair in refs)
        if str(row.get("uuid")) in changed or touches:
            row["status"] = "recorded"
            row["measured_nm"] = UNMEASURED
            row["measured_deg"] = None


def _apply_alignment_rows(table: dict[str, Any], pairs: list[tuple[dict[str, Any], str]],
                          *, move: bool, side: str) -> None:
    """先全量验证、再逐条应用（不落半个改动）。"""
    prepared = []
    for row, target in pairs:
        if target == UNSUPPORTED:
            raise _fail("unsupported_alignment", "this mate kind has no alignment",
                        mate=str(row.get("uuid")), kind=str(row.get("kind")),
                        known=list(ALIGNMENT_KINDS))
        mover = None
        if move and not bool(row.get("suppressed")):
            mover = _plan_mover(table, row, side)
        prepared.append((row, target, mover))
    moved, changed = [], []
    for row, target, mover in prepared:
        row["flip"] = bool(target == ANTI_ALIGNED)
        changed.append(str(row.get("uuid")))
        if mover is not None:
            _apply_direction(table, row, target, mover)
            moved.append(str(_get(mover, "uuid")))
    _invalidate(table, moved, changed)


# ======================================================================================
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# ======================================================================================


def mate_alignment_get(sk_or_doc: Any, *, alignment: Any = None, mates: Any = None) -> list[str]:
    """**读配合对齐方向**：返回符合筛选的配合 uuid（表内顺序）。

    `alignment`：`None` ⇒ 表内全部配合；`"aligned"` / `"anti_aligned"`（也吃 `Aligned` /
    `Anti-Aligned` 拼法）⇒ 只要该方向的；`"unsupported"` ⇒ 该种类**不能**施加对齐的那些
    （如实报出，不静默藏）。`mates`：只在这些 uuid 里筛（不认识 ⇒ `unknown_mate`）。
    """
    table = _store(sk_or_doc)
    wanted = None
    if mates is not None:
        wanted = _uuid_list(mates, allow_empty=True)
        for key in wanted:
            _find_mate(table, key)
    state = None if alignment is None else _normalize_alignment(alignment, allow_unsupported=True)
    out = []
    for row in _rows(table, "mates"):
        key = str(row.get("uuid"))
        if wanted is not None and key not in wanted:
            continue
        if state is not None and _alignment_of(row) != state:
            continue
        out.append(key)
    return out


def mate_alignment_set(sk_or_doc: Any, *, mates: Any, alignment: Any, move: bool = True,
                       side: str = "auto") -> list[str]:
    """**设定配合对齐方向**（`aligned` / `anti_aligned`），并把可动件真的翻到那一侧。

    - 真源 = 配合行的 `flip`（不新增字段）；`move=False` ⇒ 只改方向不挪件（残差照样变）；
      被压缩的配合只改方向不挪件。
    - `side`：`auto`（缺省，优先第二参考所属件）/ `first` / `second`；两件都显式固定 ⇒
      `fixed_component`。
    - 已经在该方向 ⇒ **零位移**（幂等）；返回 `mates` 里点名的 uuid（按给定顺序）。
    """
    table = _store(sk_or_doc)
    keys = _uuid_list(mates, allow_empty=False)
    target = _normalize_alignment(alignment)
    side_key = _side_key(side)
    move_flag = _flag(move, what="move")
    rows = [_find_mate(table, key) for key in keys]
    _apply_alignment_rows(table, [(row, target) for row in rows], move=move_flag, side=side_key)
    return keys


def mate_alignment_toggle(sk_or_doc: Any, *, mates: Any, move: bool = True,
                          side: str = "auto") -> list[str]:
    """**反转对齐方向**：逐条把 `aligned` ↔ `anti_aligned` 互换（可动件跟着翻到另一侧）。

    与 `mate_alignment_set` 同一套几何语义与原子性；该种类不能施加对齐（如 `perpendicular`）⇒
    `unsupported_alignment`（整批不落半个改动）。返回点名的 uuid（按给定顺序）。
    """
    table = _store(sk_or_doc)
    keys = _uuid_list(mates, allow_empty=False)
    side_key = _side_key(side)
    move_flag = _flag(move, what="move")
    rows = [_find_mate(table, key) for key in keys]
    pairs = []
    for row in rows:
        state = _alignment_of(row)
        if state == UNSUPPORTED:
            raise _fail("unsupported_alignment", "this mate kind has no alignment to toggle",
                        mate=str(row.get("uuid")), kind=str(row.get("kind")),
                        known=list(ALIGNMENT_KINDS))
        pairs.append((row, ANTI_ALIGNED if state == ALIGNED else ALIGNED))
    _apply_alignment_rows(table, pairs, move=move_flag, side=side_key)
    return keys


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mate_alignment_set": mate_alignment_set,
    "mate_alignment_toggle": mate_alignment_toggle,
    "mate_alignment_get": mate_alignment_get,
}


# ======================================================================================
# 自检（真族模块交叉验证 + 硬断言；不依赖 pytest）
# ======================================================================================

MM = 1000000
_ORIG_B = [[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]]  # 绕 X 转 π：face 方向翻成 -Z
_ASSERT_COUNT = 0


class _FixtureDoc(dict):
    """最小 L1 同形夹具：`sketches`/`extra` 既有键又有属性（两种访问口径都命中，无磁盘副作用）。"""

    def __init__(self) -> None:
        super().__init__()
        self["sketches"] = []
        self["extra"] = {}

    @property
    def sketches(self) -> list[Any]:
        return self["sketches"]

    @property
    def extra(self) -> dict[str, Any]:
        return self["extra"]


def _check(condition: Any, label: str) -> None:
    global _ASSERT_COUNT
    if not condition:
        raise SystemExit("SELFTEST_FAIL assert=%s" % label)
    _ASSERT_COUNT += 1
    print("SELFTEST_ASSERT ok=%s" % label)


def _expect_reason(call: Any, reason: str, label: str) -> None:
    try:
        call()
    except CadError as error:
        _check(getattr(error, "reason", None) == reason, label)
        return
    raise SystemExit("SELFTEST_FAIL assert=%s (no CadError raised)" % label)


def _negative_control(call: Any, label: str) -> None:
    """尺子自证：故意用错的 reason 必须被判红，否则 `_expect_reason` 是空转的。"""
    global _ASSERT_COUNT
    try:
        call()
    except SystemExit:
        _ASSERT_COUNT += 1
        print("SELFTEST_ASSERT ok=%s" % label)
        return
    raise SystemExit("SELFTEST_FAIL assert=%s (ruler did not see red)" % label)


def _snapshot(comp: Any) -> tuple[Any, Any]:
    return (list(_get(comp, "at_nm")), [list(row) for row in _get(comp, "rotation")])


def _component(comp_uuid: str, name: str, *, rotation: Any = None, at: Any = (0, 0, 0),
               refs: Any = None, fixed: bool = False) -> dict[str, Any]:
    return {"uuid": comp_uuid, "name": name,
            "rotation": [list(row) for row in (rotation if rotation is not None else _IDENTITY3)],
            "at_nm": [int(value) for value in at], "refs": dict(refs or {}), "fixed": bool(fixed)}


def _new_doc() -> Any:
    """优先真 L1 文档（cadcore.docmodel.Document），不可用则回落最小同形夹具。"""
    try:
        from cadcore import docmodel

        return docmodel.Document()
    except Exception:
        return _FixtureDoc()


def _selftest() -> int:
    import uuid as _uuid

    import cadcore.ops.sw_assembly as family  # 真族模块：交叉验证 mate_check 的残差

    doc = _new_doc()
    table = _store(doc)
    _check(isinstance(_get(doc, "sketches"), list)
           and isinstance(_document_extra(doc), _MutableMappingABC), "fixture_is_l1_shaped")

    a_uuid, b_uuid, c_uuid, d_uuid = (_uuid.uuid4().hex for _ in range(4))
    refs_face = {"face": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]}}
    refs_origin = {"origin": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]}}
    comp_a = _component(a_uuid, "base", refs=refs_face, fixed=True)
    comp_b = _component(b_uuid, "flap", rotation=_ORIG_B, refs=refs_face)
    comp_c = _component(c_uuid, "rail", refs=refs_origin, fixed=True)
    comp_d = _component(d_uuid, "slider", at=(0, 0, 5 * MM), refs=refs_origin)
    _rows(table, "components").extend([comp_a, comp_b, comp_c, comp_d])

    mate_face = family.mate_coincident(doc, refs=[[a_uuid, "face"], [b_uuid, "face"]])[0]
    mate_perp = family.mate_perpendicular(doc, refs=[[a_uuid, "face"], [b_uuid, "face"]])[0]
    mate_dist = family.mate_distance(doc, refs=[[c_uuid, "origin"], [d_uuid, "origin"]],
                                     value_nm=10 * MM, flip=True)[0]
    _check(sorted(family.mate_check(doc)) == sorted([mate_face, mate_perp, mate_dist]),
           "initial_three_mates_all_violated")

    # ---- 读侧：三个方向词汇，谁是谁不混 ----
    _check(mate_alignment_get(doc) == [mate_face, mate_perp, mate_dist], "get_all_in_table_order")
    _check(mate_alignment_get(doc, alignment="aligned") == [mate_face], "get_aligned_only")
    _check(mate_alignment_get(doc, alignment="anti_aligned") == [mate_dist], "get_anti_aligned_only")
    _check(mate_alignment_get(doc, alignment="unsupported") == [mate_perp],
           "get_unsupported_is_explicit")
    _check(mate_alignment_get(doc, mates=[mate_face]) == [mate_face], "get_scoped_to_mates")

    # ---- 方向 1：记录成 anti-aligned（SolidWorks 拼法）→ 残差真变，且不动件 ----
    pose_before = _snapshot(comp_b)
    _check(mate_alignment_set(doc, mates=[mate_face], alignment="Anti-Aligned") == [mate_face],
           "set_accepts_solidworks_spelling")
    _check(_snapshot(comp_b) == pose_before, "record_only_does_not_move")
    _check(family.mate_check(doc, mates=[mate_face]) == [], "anti_aligned_makes_coincident_satisfied")

    # ---- 方向 2：move=False 只改方向 ⇒ 残差回红（证明这个位是真的、不是装饰） ----
    _check(mate_alignment_set(doc, mates=[mate_face], alignment="aligned", move=False) == [mate_face],
           "set_move_false_returns_mate")
    _check(family.mate_check(doc, mates=[mate_face]) == [mate_face],
           "flag_changes_residual_without_motion")
    _check(_snapshot(comp_b) == pose_before, "move_false_keeps_pose")

    # ---- 方向 3：move=True ⇒ 零件真翻到另一侧，参考点不动 ----
    _check(mate_alignment_set(doc, mates=[mate_face], alignment="aligned") == [mate_face],
           "set_aligned_moves_component")
    _, point_b, dir_b = _ref_world(table, [b_uuid, "face"])
    _check(_dot(_unit(dir_b), [0.0, 0.0, 1.0]) > 0.999999, "flipped_direction_now_faces_plus_z")
    _check(_norm(point_b) <= 1.0, "flip_keeps_reference_point")
    _check(family.mate_check(doc, mates=[mate_face]) == [], "aligned_makes_coincident_satisfied")

    # ---- 幂等：同方向再设一次不来回翻 ----
    pose_aligned = _snapshot(comp_b)
    _check(mate_alignment_set(doc, mates=[mate_face], alignment="aligned") == [mate_face],
           "set_same_alignment_is_idempotent")
    _check(_snapshot(comp_b) == pose_aligned, "idempotent_call_does_not_move_again")

    # ---- 反转：几何可逆（旋转矩阵与平移逐位回原） ----
    _check(mate_alignment_toggle(doc, mates=[mate_face]) == [mate_face], "toggle_returns_mate")
    _check(_dot(_unit(_ref_world(table, [b_uuid, "face"])[2]), [0.0, 0.0, -1.0]) > 0.999999,
           "toggle_back_reverses_direction")
    _check(max(abs(_get(comp_b, "rotation")[i][j] - _ORIG_B[i][j])
               for i in range(3) for j in range(3)) < 1e-9, "toggle_back_restores_rotation_matrix")
    _check(_get(comp_b, "at_nm") == [0, 0, 0], "toggle_back_restores_translation")
    _check(family.mate_check(doc, mates=[mate_face]) == [], "toggle_back_still_satisfied")

    # ---- distance 配合：翻方向 = 沿轴平移到另一侧（整数纳米，精确） ----
    _check(mate_alignment_set(doc, mates=[mate_dist], alignment="aligned") == [mate_dist],
           "distance_set_aligned")
    _check(_get(comp_d, "at_nm") == [0, 0, 10 * MM], "distance_aligned_moves_to_plus_side")
    _check(family.mate_check(doc, mates=[mate_dist]) == [], "distance_aligned_satisfied")
    _check(mate_alignment_set(doc, mates=[mate_dist], alignment="anti_aligned") == [mate_dist],
           "distance_set_anti_aligned")
    _check(_get(comp_d, "at_nm") == [0, 0, -10 * MM], "distance_anti_moves_to_minus_side")
    _check(family.mate_check(doc, mates=[mate_dist]) == [], "distance_anti_satisfied")
    pose_d = _snapshot(comp_d)
    _check(mate_alignment_set(doc, mates=[mate_dist], alignment="anti_aligned") == [mate_dist],
           "distance_repeat_is_idempotent")
    _check(_snapshot(comp_d) == pose_d, "distance_idempotent_does_not_move_again")

    # ---- 唯一真源 / 错误面 ----
    _check("alignment" not in _find_mate(table, mate_face), "no_duplicate_alignment_field")
    _expect_reason(lambda: mate_alignment_set(doc, mates=["deadbeef"], alignment="aligned"),
                   "unknown_mate", "err_unknown_mate")
    _expect_reason(lambda: mate_alignment_set(doc, mates=[mate_perp], alignment="aligned"),
                   "unsupported_alignment", "err_unsupported_kind")
    _expect_reason(lambda: mate_alignment_set(doc, mates=[mate_face], alignment="sideways"),
                   "bad_alignment", "err_bad_alignment")
    _expect_reason(lambda: mate_alignment_toggle(doc, mates=[mate_perp]),
                   "unsupported_alignment", "err_toggle_unsupported_kind")
    _negative_control(
        lambda: _expect_reason(lambda: mate_alignment_set(doc, mates=[mate_face], alignment="sideways"),
                               "unknown_mate", "control"), "negative_control_ruler_sees_red")

    # ---- 固定件：两件都显式固定 ⇒ 拒收，且状态原样 ----
    flip_before = bool(_find_mate(table, mate_face).get("flip"))
    _set(comp_b, "fixed", True)
    _expect_reason(lambda: mate_alignment_set(doc, mates=[mate_face], alignment="anti_aligned"),
                   "fixed_component", "err_both_components_fixed")
    _set(comp_b, "fixed", False)
    _check(bool(_find_mate(table, mate_face).get("flip")) == flip_before,
           "err_fixed_component_left_state_untouched")

    # ---- 原子性：第二条不合法 ⇒ 第一条也不许落半个改动 ----
    flip_dist_before = bool(_find_mate(table, mate_dist).get("flip"))
    pose_d_before = _snapshot(comp_d)
    _expect_reason(lambda: mate_alignment_set(doc, mates=[mate_dist, mate_perp], alignment="aligned"),
                   "unsupported_alignment", "err_atomic_second_mate_rejected")
    _check(bool(_find_mate(table, mate_dist).get("flip")) == flip_dist_before,
           "err_atomic_no_half_applied_flag")
    _check(_snapshot(comp_d) == pose_d_before, "err_atomic_no_partial_motion")

    print("SELFTEST_EVIDENCE doc=%s distance_at_nm=%s face_dir_b=%s"
          % (type(doc).__name__, _get(comp_d, "at_nm"), [round(v, 9) for v in
             _ref_world(table, [b_uuid, "face"])[2]]))
    print("SELFTEST family=sw_assembly shard=w9 ops=%d asserts=%d"
          % (len(OPERATIONS), _ASSERT_COUNT))
    print("FEATURE_OK family=sw_assembly op=mate_alignment_set verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "mate_alignment_get": {"tier": "query", "readonly": True, "returns": "data"},
}
