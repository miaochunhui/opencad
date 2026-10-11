#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w28 —— 装配族功能片：过定义与配合冲突诊断。

本片对应的清单条目（第 19 号）
------------------------------
族清单 ``docs/research/cad-parity/features/sw-assembly.json`` 的 ``must_have``（21 条）
与状态 ``docs/research/cad-parity/status/sw-assembly.json`` 逐条对齐后，本族**仍 missing/partial**
的 must_have 只有三条，按清单顺序编号：

    0  配合对齐方向控制          status=missing   （aligned / anti-aligned）
    1  过定义与配合冲突诊断      status=partial   <-- 本片负责
    2  干涉检查                  status=partial   （现为粗 AABB 检查）

``19 % 3 == 1`` ⇒ 本片实现第 1 号：**过定义与配合冲突诊断**。
族模块 ``cadcore/ops/sw_assembly.py`` 的 ``diagnose_mates`` 只做**单条配合的记录级残差**，
既没有自由度（DOF）账、没有配合之间的**矛盾判定**，也没有任何解法 ⇒ status 停在 partial。

本片新增的真东西（不是桩，也不是把族模块的函数改名再包一层）
----------------------------------------------------------
1. **DOF 账**：把装配体按"配合"连成无向图（零部件 = 节点，配合 = 边），逐连通分量算
   ``dof_available = 6 × 零部件数``、``dof_removed = Σ 每条生效配合按 kind 扣掉的自由度``，
   ``dof_left < 0`` ⇒ ``verdict="over_defined"``（真过定义；0/正数分别是 fully_defined /
   under_defined / unconstrained）。DOF 扣减表是本层**写死的口径**（见 ``_DOF_REMOVED``）：
   重合 3、同轴 4、平行 2、垂直 2、距离 1、角度 1、相切 1、锁定 6；**表里没有的 kind 记
   ``unmeasured`` 并如实报出来**，绝不按 0 计（E2.3：没算 ≠ 没约束）。
2. **矛盾判定（闭式几何）**：对**同一参考对**上的两条配合，按 kind 组合判不可同时满足：
   平行×垂直、重合×距离(≠0)、距离×距离(值差 > 容差)、角度×角度(值差 > 容差)、
   平行×角度(非 0/180)、垂直×角度(非 90)。判定只用配合记录本身，不看位姿，
   所以"位姿没求解"不会被误报成"冲突"（这是族模块 ``mate_check`` 做不到的分辨）。
3. **残差真几何**：逐条生效配合，把两个参考经 ``point_world = R @ p + at_nm`` 与
   ``dir_world = normalize(R @ d)`` 换到世界系，现算实测值（点距 / 点到轴线的距离 / 轴夹角投影
   距离）与残差，三态落盘：``0`` 真为零、``-1`` 量不出、``None`` = 该 kind 没有这个量（NA）。
4. **冗余判定**：同参考对同 kind 同值 = 重复配合；同参考对上已有同轴 ⇒ 平行被蕴含；
   平行(A,B) + 平行(B,C) ⇒ 平行(A,C) 被蕴含（图上可达性）。
5. **解法（真数据后果）**：``suggested_suppress`` 给出最小压制集 —— 先压矛盾里较晚的一条、
   重复/被蕴含的那条，再按"表内顺序从后往前"压到每个分量的 ``dof_left >= 0`` 为止；
   ``apply_overdefined_resolution`` 真把 ``suppressed=True`` 写回配合行（幂等：只压还没压的），
   并刷新诊断记录。诊断记录 upsert 进 ``doc.extra["sw_assembly"]["diagnoses"]``
   （scope 唯一，uuid 用 uuid5 定值，重复调用不堆记录）。

落盘形态 / 复用
--------------
- 表位置只有**一个**复用点：``cadcore.ops.sw_assembly._store(target, create=True)``
  （文档 ⇒ ``doc.extra["sw_assembly"]``；草图形态 ⇒ 该对象自己的 ``sw_assembly`` 键），
  本文件不抄第二份定位逻辑。
- 只加键不改键：新增 ``diagnoses`` 槽；``components`` / ``mates`` / ``interferences`` /
  ``explodes`` 一律只读（``apply_overdefined_resolution`` 只改配合行的 ``suppressed`` 标志）。
- 非 docstring 字符串字面量一律 ASCII（``tools/cad/i18n_gate.py`` 的口径）。

有意限定的边界（写下来，防止下游靠猜）
------------------------------------
1. **不是约束求解器**：本片只诊断、只给压制建议；它不改任何零部件位姿。
2. **矛盾判定只在"同一参考对"上做闭式组合**；跨参考对、需要数值秩判定的过约束不在此列
   （那需要求解器 Jacobian），此类情况由 DOF 账 + 残差共同暴露，不会静默放过。
3. DOF 扣减表是**本层口径**（SW 语义的整数量化），不是 rank 计算；因此只在 ``dof_left < 0``
   时报 ``over_defined``（欠定/完全定义不动声色），避免把"计数巧合"说成"数学证明"。

自检（文件自带，不依赖 pytest）
------------------------------
    cd oss-alternatives/packages/cadcore && python3 cadcore/ops/features/sw_assembly__w28.py
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑本文件的引导（见文件末 __main__）
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
import uuid as _uuid
from collections.abc import Mapping as _Mapping
from typing import Any, Callable

from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）
from cadcore.ops import sw_assembly as _assembly

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "diagnose_overdefined",
    "apply_overdefined_resolution",
]

FAMILY = "sw_assembly"

_SCHEMA = 1
_DIAGNOSES = "diagnoses"
_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_DOF_TOTAL = 6
#: 本层的自由度扣减口径（整数，按 SW 配合语义量化；表外的 kind 记 unmeasured，不按 0 计）。
_DOF_REMOVED = {
    "coincident": 3,
    "concentric": 4,
    "parallel": 2,
    "perpendicular": 2,
    "distance": 1,
    "angle": 1,
    "tangent": 1,
    "lock": 6,
}
#: 本层有残差量测规则的 kind（其余 kind 的残差记 unmeasured）。
_MEASURED_KINDS = ("coincident", "concentric", "parallel", "perpendicular", "distance", "angle")
_OP_DIAGNOSE = "diagnose_overdefined"
_OP_RESOLVE = "apply_overdefined_resolution"


# --------------------------------------------------------------------------------------
# 失败 / 取值
# --------------------------------------------------------------------------------------
def _fail(reason: str, detail: str, **details: Any) -> CadError:
    return CadError(detail, reason=reason, **details)


def _flag(value: Any) -> bool:
    return value is True or value == 1


def _is_fixed(component: Any) -> bool:
    return isinstance(component, _Mapping) and _flag(component.get("fixed"))


def _get_store(target: Any, *, create: bool) -> dict[str, Any]:
    store = _assembly._store(target, create=create)
    if not isinstance(store, dict):
        raise _fail("assembly_table_missing", "target exposes no sw_assembly table", create=create)
    return store


def _table(store: dict[str, Any], key: str) -> list[Any]:
    rows = store.get(key)
    if rows is None:
        rows = []
        store[key] = rows
    if not isinstance(rows, list):
        raise _fail("invalid_table", "assembly table slot is not a list", key=key, got=type(rows).__name__)
    return rows


def _int_nm(value: Any, *, what: str) -> int:
    if value is None or isinstance(value, bool):
        raise _fail("invalid_argument", "expected an integer nanometre value", what=what, value=repr(value))
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise _fail("invalid_argument", "expected an integer nanometre value", what=what, value=repr(value)) from exc


def _float_num(value: Any, *, what: str) -> float:
    if value is None or isinstance(value, bool):
        raise _fail("invalid_argument", "expected a number", what=what, value=repr(value))
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise _fail("invalid_argument", "expected a number", what=what, value=repr(value)) from exc


def _vec3(value: Any, *, default: tuple[float, float, float], what: str) -> list[float]:
    if value is None:
        return [float(default[0]), float(default[1]), float(default[2])]
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _fail("invalid_argument", "expected three coordinates", what=what, value=repr(value))
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise _fail("invalid_argument", "coordinate is not a number", what=what, value=repr(item))
        out.append(float(item))
    return out


def _is_matrix(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            return False
        for item in row:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return False
    return True


def _mat_apply(matrix: Any, vector: Any) -> list[float]:
    rows = matrix if _is_matrix(matrix) else _IDENTITY
    out: list[float] = []
    for row in rows:
        out.append(sum(float(row[k]) * float(vector[k]) for k in range(3)))
    return out


def _sub3(left: Any, right: Any) -> list[float]:
    return [float(left[k]) - float(right[k]) for k in range(3)]


def _add3(left: Any, right: Any) -> list[float]:
    return [float(left[k]) + float(right[k]) for k in range(3)]


def _neg3(vector: Any) -> list[float]:
    return [-float(vector[k]) for k in range(3)]


def _dot3(left: Any, right: Any) -> float:
    return sum(float(left[k]) * float(right[k]) for k in range(3))


def _cross3(left: Any, right: Any) -> list[float]:
    return [
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    ]


def _norm3(vector: Any) -> float:
    return math.sqrt(_dot3(vector, vector))


def _unit3(vector: Any) -> list[float]:
    length = _norm3(vector)
    if length <= 0.0:
        raise _fail("degenerate_reference", "reference direction has zero length", vector=list(vector))
    return [float(vector[k]) / length for k in range(3)]


def _angle_deg(left: Any, right: Any) -> float:
    unit_left = _unit3(left)
    unit_right = _unit3(right)
    cosine = max(-1.0, min(1.0, _dot3(unit_left, unit_right)))
    return math.degrees(math.acos(cosine))


def _point_line_distance(point: Any, origin: Any, unit: Any) -> float:
    return _norm3(_cross3(_sub3(point, origin), unit))


# --------------------------------------------------------------------------------------
# 装配表读取
# --------------------------------------------------------------------------------------
def _component_index(components: list[Any], ref: Any, *, what: str) -> int:
    if ref is None or isinstance(ref, bool):
        raise _fail("invalid_component_ref", "component reference must be a uuid, name or index",
                    ref=repr(ref), what=what)
    index: int | None = None
    if isinstance(ref, int):
        index = int(ref)
    elif isinstance(ref, str):
        for position, row in enumerate(components):
            if not isinstance(row, _Mapping):
                continue
            for field in ("uuid", "name", "part_key"):
                if row.get(field) is not None and str(row.get(field)) == ref:
                    return position
        if ref.lstrip("-").isdigit():
            index = int(ref)
        else:
            raise _fail("unknown_component", "no component matches this reference", ref=ref, what=what)
    else:
        raise _fail("invalid_component_ref", "component reference must be a uuid, name or index",
                    ref=repr(ref), what=what)
    if index < 0:
        index += len(components)
    if not 0 <= index < len(components):
        raise _fail("unknown_component", "component index out of range", ref=repr(ref),
                    count=len(components), what=what)
    return index


def _endpoints(mate: Any) -> tuple[tuple[Any, str], tuple[Any, str]]:
    if not isinstance(mate, _Mapping):
        raise _fail("invalid_mate_record", "mate row is not a mapping", row=repr(mate))
    refs = mate.get("refs")
    if not isinstance(refs, (list, tuple)) or len(refs) != 2:
        raise _fail("invalid_mate_record", "mate record needs exactly two endpoints",
                    mate=str(mate.get("uuid")))
    pair: list[tuple[Any, str]] = []
    for entry in refs:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise _fail("invalid_mate_record", "mate endpoint must be [component, ref]",
                        mate=str(mate.get("uuid")))
        pair.append((entry[0], str(entry[1])))
    return pair[0], pair[1]


def _resolved_pairs(components: list[Any], mates: list[Any], indices: list[int]) -> list[tuple[int, int, int, str, str]]:
    """(mate 下标, 分量下标 A, 分量下标 B, 参考名 A, 参考名 B)，顺序与 ``indices`` 一致。"""
    out: list[tuple[int, int, int, str, str]] = []
    for index in indices:
        mate = mates[index]
        first_ref, second_ref = _endpoints(mate)
        first = _component_index(components, first_ref[0], what="mate:" + str(mate.get("uuid")))
        second = _component_index(components, second_ref[0], what="mate:" + str(mate.get("uuid")))
        out.append((index, first, second, first_ref[1], second_ref[1]))
    return out


def _world_ref(component: Any, name: str, *, what: str) -> dict[str, list[float]]:
    if not isinstance(component, _Mapping):
        raise _fail("invalid_component", "component row is not a mapping", row=repr(component))
    refs = component.get("refs")
    geometry = refs.get(name) if isinstance(refs, _Mapping) else None
    if not isinstance(geometry, _Mapping):
        if str(name) == "origin":
            geometry = {}
        else:
            raise _fail("unknown_ref", "component has no such reference", component=str(component.get("uuid")),
                        ref=str(name), what=what)
    point = _vec3(geometry.get("point_nm"), default=(0.0, 0.0, 0.0), what="refs.point_nm")
    direction = _vec3(geometry.get("dir"), default=(0.0, 0.0, 1.0), what="refs.dir")
    rotation = component.get("rotation")
    if not _is_matrix(rotation):
        rotation = _IDENTITY
    at = _vec3(component.get("at_nm"), default=(0.0, 0.0, 0.0), what="at_nm")
    return {
        "point": _add3(_mat_apply(rotation, point), at),
        "dir": _unit3(_mat_apply(rotation, direction)),
    }


def _visible_components(components: list[Any]) -> list[int]:
    return [i for i, row in enumerate(components) if not (isinstance(row, _Mapping) and _flag(row.get("suppressed")))]


def _visible_mates(mates: list[Any]) -> list[int]:
    return [i for i, row in enumerate(mates) if not (isinstance(row, _Mapping) and _flag(row.get("suppressed")))]


def _value_nm(mate: Any) -> int | None:
    value = mate.get("value_nm") if isinstance(mate, _Mapping) else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _value_deg(mate: Any) -> float | None:
    value = mate.get("value_deg") if isinstance(mate, _Mapping) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_tolerance_nm(mate: Any, *, default: int) -> int:
    value = mate.get("tolerance_nm") if isinstance(mate, _Mapping) else None
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) < 0):
        return default  # -1 = 未采 ⇒ 用调用方缺省（三态口径）
    return int(value)


def _row_tolerance_deg(mate: Any, *, default: float) -> float:
    value = mate.get("tolerance_deg") if isinstance(mate, _Mapping) else None
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) < 0.0):
        return default
    return float(value)


# --------------------------------------------------------------------------------------
# 残差（真几何）
# --------------------------------------------------------------------------------------
def _measure(kind: str, first: dict[str, Any], second: dict[str, Any], *, flip: bool,
             value_nm: int | None, value_deg: float | None,
             tol_nm: int, tol_deg: float) -> dict[str, Any]:
    """按当前位姿现算一条配合的实测值与残差。三态：``0`` 真为零 / ``-1`` 量不出 / ``None`` = NA。"""
    if kind not in _MEASURED_KINDS:
        return {"status": "unmeasured", "reason": "no_measure_rule", "measured_nm": -1,
                "measured_deg": None, "residual_nm": -1, "residual_deg": None}
    second_dir = _neg3(second["dir"]) if flip else second["dir"]
    if kind == "coincident":
        distance = _norm3(_sub3(first["point"], second["point"]))
        return {"status": "satisfied" if distance <= tol_nm else "violated", "reason": "",
                "measured_nm": int(round(distance)), "measured_deg": None,
                "residual_nm": int(round(distance)), "residual_deg": None}
    if kind == "concentric":
        distance = _point_line_distance(first["point"], second["point"], second["dir"])
        angle = _angle_deg(first["dir"], second_dir)
        ok = distance <= tol_nm and angle <= tol_deg
        return {"status": "satisfied" if ok else "violated", "reason": "",
                "measured_nm": int(round(distance)), "measured_deg": round(angle, 9),
                "residual_nm": int(round(distance)), "residual_deg": round(angle, 9)}
    if kind in ("parallel", "perpendicular"):
        angle = _angle_deg(first["dir"], second_dir)
        residual = abs(angle - (0.0 if kind == "parallel" else 90.0))
        return {"status": "satisfied" if residual <= tol_deg else "violated", "reason": "",
                "measured_nm": None, "measured_deg": round(angle, 9),
                "residual_nm": None, "residual_deg": round(residual, 9)}
    if kind == "distance":
        if value_nm is None:
            return {"status": "unmeasured", "reason": "missing_value", "measured_nm": -1,
                    "measured_deg": None, "residual_nm": -1, "residual_deg": None}
        measured = abs(_dot3(_sub3(first["point"], second["point"]), first["dir"]))
        residual = abs(measured - float(value_nm))
        return {"status": "satisfied" if residual <= tol_nm else "violated", "reason": "",
                "measured_nm": int(round(measured)), "measured_deg": None,
                "residual_nm": int(round(residual)), "residual_deg": None}
    # angle
    if value_deg is None:
        return {"status": "unmeasured", "reason": "missing_value", "measured_nm": None,
                "measured_deg": None, "residual_nm": None, "residual_deg": None}
    angle = _angle_deg(first["dir"], second_dir)
    residual = abs(angle - float(value_deg))
    return {"status": "satisfied" if residual <= tol_deg else "violated", "reason": "",
            "measured_nm": None, "measured_deg": round(angle, 9),
            "residual_nm": None, "residual_deg": round(residual, 9)}


# --------------------------------------------------------------------------------------
# 矛盾 / 冗余
# --------------------------------------------------------------------------------------
def _contradiction(first: Any, second: Any, *, tol_nm: int, tol_deg: float) -> str | None:
    """同一参考对上两条配合是否**闭式**不可同时满足（不看位姿，故不会把"没求解"误报成冲突）。"""
    kind_a = str(first.get("kind") or "").lower()
    kind_b = str(second.get("kind") or "").lower()
    pair = {kind_a, kind_b}
    value_nm_a, value_nm_b = _value_nm(first), _value_nm(second)
    value_deg_a, value_deg_b = _value_deg(first), _value_deg(second)
    if pair == {"parallel", "perpendicular"}:
        return "parallel_vs_perpendicular"
    if pair == {"coincident", "distance"}:
        value = value_nm_a if kind_a == "distance" else value_nm_b
        if value is not None and abs(value) > tol_nm:
            return "coincident_vs_distance"
        return None
    if kind_a == kind_b == "distance" and value_nm_a is not None and value_nm_b is not None:
        if abs(value_nm_a - value_nm_b) > tol_nm:
            return "distance_vs_distance"
        return None
    if kind_a == kind_b == "angle" and value_deg_a is not None and value_deg_b is not None:
        if abs(value_deg_a - value_deg_b) > tol_deg:
            return "angle_vs_angle"
        return None
    if pair == {"parallel", "angle"}:
        value = value_deg_a if kind_a == "angle" else value_deg_b
        if value is not None and min(abs(value), abs(value - 180.0)) > tol_deg:
            return "parallel_vs_angle"
        return None
    if pair == {"perpendicular", "angle"}:
        value = value_deg_a if kind_a == "angle" else value_deg_b
        if value is not None and abs(value - 90.0) > tol_deg:
            return "perpendicular_vs_angle"
        return None
    return None


def _conflict_pairs(components: list[Any], mates: list[Any], visible_mates: list[int], *,
                    tol_nm: int, tol_deg: float) -> tuple[list[tuple[int, int, str]], dict[Any, list[int]]]:
    buckets: dict[Any, list[int]] = {}
    for index, first, second, ref_a, ref_b in _resolved_pairs(components, mates, visible_mates):
        if first == second:
            continue
        key = tuple(sorted([(str(components[first].get("uuid")), ref_a), (str(components[second].get("uuid")), ref_b)]))
        buckets.setdefault(key, []).append(index)
    pairs: list[tuple[int, int, str]] = []
    for key in sorted(buckets):
        indices = buckets[key]
        for left in range(len(indices)):
            for right in range(left + 1, len(indices)):
                rule = _contradiction(mates[indices[left]], mates[indices[right]], tol_nm=tol_nm, tol_deg=tol_deg)
                if rule is not None:
                    pairs.append((indices[left], indices[right], rule))
    return pairs, buckets


def _reachable(adjacency: dict[Any, set[Any]], start: Any, goal: Any) -> bool:
    if start == goal:
        return True
    seen = {start}
    queue = [start]
    while queue:
        node = queue.pop()
        for neighbour in adjacency.get(node, ()):
            if neighbour == goal:
                return True
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return False


def _redundant_hits(mates: list[Any], buckets: dict[Any, list[int]], *,
                    tol_nm: int, tol_deg: float) -> list[tuple[int, str, tuple[int, ...]]]:
    hits: list[tuple[int, str, tuple[int, ...]]] = []
    parallel_edges: dict[Any, tuple[Any, Any, list[int]]] = {}
    for key in sorted(buckets):
        indices = buckets[key]
        by_kind: dict[str, list[int]] = {}
        for index in indices:
            by_kind.setdefault(str(mates[index].get("kind") or "").lower(), []).append(index)
        for kind, group in by_kind.items():
            if kind in ("distance", "angle"):
                for left in range(len(group)):
                    for right in range(left + 1, len(group)):
                        earlier, later = group[left], group[right]
                        if kind == "distance":
                            same = _value_nm(mates[earlier]) is not None and _value_nm(mates[earlier]) == _value_nm(mates[later])
                        else:
                            same = _value_deg(mates[earlier]) is not None and abs(
                                float(_value_deg(mates[earlier])) - float(_value_deg(mates[later]))) <= tol_deg
                        if same:
                            hits.append((later, "duplicate", (earlier,)))
            else:
                for position in range(1, len(group)):
                    if bool(mates[group[0]].get("flip")) == bool(mates[group[position]].get("flip")):
                        hits.append((group[position], "duplicate", (group[0],)))
        if "concentric" in by_kind and "parallel" in by_kind:
            for index in by_kind["parallel"]:
                hits.append((index, "implied_by_concentric", tuple(by_kind["concentric"])))
        if "parallel" in by_kind:
            parallel_edges[key] = (key[0], key[1], by_kind["parallel"])
    for key in sorted(parallel_edges):
        node_a, node_b, indices = parallel_edges[key]
        adjacency: dict[Any, set[Any]] = {}
        for other_key in sorted(parallel_edges):
            if other_key == key:
                continue
            other_a, other_b, _other = parallel_edges[other_key]
            adjacency.setdefault(other_a, set()).add(other_b)
            adjacency.setdefault(other_b, set()).add(other_a)
        if _reachable(adjacency, node_a, node_b):
            hits.append((indices[-1], "implied_transitive", tuple(indices[:-1])))
    return hits


# --------------------------------------------------------------------------------------
# 自由度（DOF）账
# --------------------------------------------------------------------------------------
def _dof_groups(components: list[Any], mates: list[Any], visible_components: list[int],
                visible_mates: list[int], *, exclude: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for node in visible_components:
        find(node)
    pairs = _resolved_pairs(components, mates, visible_mates)
    for index, first, second, _ref_a, _ref_b in pairs:
        if index in exclude or first == second:
            continue
        union(first, second)
    groups: dict[int, dict[str, Any]] = {}
    for node in visible_components:
        groups.setdefault(find(node), {"members": [], "mate_indices": [], "unmeasured_indices": []})["members"].append(node)
    for index, first, second, _ref_a, _ref_b in pairs:
        if index in exclude or first == second:
            continue
        group = groups[find(first)]
        group["mate_indices"].append(index)
        if str(mates[index].get("kind") or "").lower() not in _DOF_REMOVED:
            group["unmeasured_indices"].append(index)
    out: list[dict[str, Any]] = []
    for group in groups.values():
        members = sorted(group["members"])
        removed = 0
        for index in group["mate_indices"]:
            removed += _DOF_REMOVED.get(str(mates[index].get("kind") or "").lower(), 0)
        available = _DOF_TOTAL * len(members)
        left = available - removed
        unmeasured = sorted(group["unmeasured_indices"])
        if left < 0:
            verdict = "over_defined"
        elif unmeasured:
            verdict = "unmeasured"
        elif left == 0:
            verdict = "fully_defined"
        elif removed == 0:
            verdict = "unconstrained"
        else:
            verdict = "under_defined"
        out.append({
            "members": members,
            "mate_indices": sorted(group["mate_indices"]),
            "unmeasured_indices": unmeasured,
            "anchored": any(_is_fixed(components[node]) for node in members),
            "dof_available": available,
            "dof_removed": removed,
            "dof_left": left,
            "verdict": verdict,
        })
    out.sort(key=lambda item: item["members"][0] if item["members"] else -1)
    return out


def _public_group(components: list[Any], mates: list[Any], group: dict[str, Any]) -> dict[str, Any]:
    return {
        "components": [str(components[node].get("uuid")) for node in group["members"]],
        "mates": [str(mates[index].get("uuid")) for index in group["mate_indices"]],
        "anchored": group["anchored"],
        "dof_available": group["dof_available"],
        "dof_removed": group["dof_removed"],
        "dof_left": group["dof_left"],
        "dof_unmeasured_mates": len(group["unmeasured_indices"]),
        "verdict": group["verdict"],
    }


def _verdict(conflict_pairs: list[Any], groups: list[dict[str, Any]], redundant_hits: list[Any]) -> str:
    if conflict_pairs:
        return "conflicting"
    if any(group["verdict"] == "over_defined" for group in groups):
        return "over_defined"
    if redundant_hits:
        return "redundant"
    if any(group["verdict"] in ("under_defined", "unconstrained") for group in groups):
        return "under_defined"
    if any(group["verdict"] == "unmeasured" for group in groups):
        return "unmeasured"
    return "clean"


# --------------------------------------------------------------------------------------
# 诊断主体
# --------------------------------------------------------------------------------------
def _scope(components: list[Any], mates: list[Any], component: Any) -> tuple[str, list[int], list[int]]:
    visible_components = _visible_components(components)
    visible_mates = _visible_mates(mates)
    if component is None:
        return "all", visible_components, visible_mates
    pivot = _component_index(components, component, what="component")
    keep = {pivot}
    for _index, first, second, _ref_a, _ref_b in _resolved_pairs(components, mates, visible_mates):
        if pivot in (first, second):
            keep.add(first)
            keep.add(second)
    scoped = [index for index, first, second, _ra, _rb in _resolved_pairs(components, mates, visible_mates)
              if first in keep and second in keep]
    return "component:" + str(components[pivot].get("uuid")), sorted(keep), scoped


def _stable_uuid(scope: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore.ops.features.sw_assembly__w28:" + scope))


def _diagnose(store: dict[str, Any], *, component: Any, tol_nm: int, tol_deg: float) -> dict[str, Any]:
    components = _table(store, "components")
    mates = _table(store, "mates")
    scope, visible_components, visible_mates = _scope(components, mates, component)
    suppressed = len(mates) - len(_visible_mates(mates))

    residual_rows: list[dict[str, Any]] = []
    for order, (index, first, second, ref_a, ref_b) in enumerate(_resolved_pairs(components, mates, visible_mates)):
        mate = mates[index]
        kind = str(mate.get("kind") or "").lower()
        row_tol_nm = _row_tolerance_nm(mate, default=tol_nm)
        row_tol_deg = _row_tolerance_deg(mate, default=tol_deg)
        row: dict[str, Any] = {"uuid": str(mate.get("uuid")), "order": order, "kind": kind,
                               "tolerance_nm": row_tol_nm, "tolerance_deg": row_tol_deg}
        if first == second:
            row.update({"status": "unmeasured", "reason": "self_mate", "measured_nm": -1,
                        "measured_deg": None, "residual_nm": -1, "residual_deg": None})
            residual_rows.append(row)
            continue
        row.update(_measure(
            kind,
            _world_ref(components[first], ref_a, what="mate:" + str(mate.get("uuid"))),
            _world_ref(components[second], ref_b, what="mate:" + str(mate.get("uuid"))),
            flip=bool(mate.get("flip")), value_nm=_value_nm(mate), value_deg=_value_deg(mate),
            tol_nm=row_tol_nm, tol_deg=row_tol_deg,
        ))
        residual_rows.append(row)

    conflict_pairs, buckets = _conflict_pairs(components, mates, visible_mates, tol_nm=tol_nm, tol_deg=tol_deg)
    redundant_hits = _redundant_hits(mates, buckets, tol_nm=tol_nm, tol_deg=tol_deg)
    groups = _dof_groups(components, mates, visible_components, visible_mates)

    flags: set[int] = set()
    for _earlier, later, _rule in conflict_pairs:
        flags.add(later)
    for index, _reason, _by in redundant_hits:
        flags.add(index)
    while True:
        trial = _dof_groups(components, mates, visible_components, visible_mates, exclude=frozenset(flags))
        pick: int | None = None
        for group in trial:
            if group["dof_left"] < 0:
                candidates = [index for index in group["mate_indices"] if index not in flags]
                if candidates:
                    pick = max(candidates)
                    break
        if pick is None:
            break
        flags.add(pick)

    implicated: set[int] = set(flags)
    for earlier, later, _rule in conflict_pairs:
        implicated.add(earlier)
        implicated.add(later)
    for row in residual_rows:
        if row["status"] == "violated":
            implicated.add(visible_mates[row["order"]])

    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "scope": scope,
        "op": _OP_DIAGNOSE,
        "verdict": _verdict(conflict_pairs, groups, redundant_hits),
        "tolerance_nm": tol_nm,
        "tolerance_deg": tol_deg,
        "counts": {"components": len(visible_components), "mates_active": len(visible_mates),
                   "mates_suppressed": suppressed},
        "groups": [_public_group(components, mates, group) for group in groups],
        "residuals": residual_rows,
        "conflicts": [{"a": str(mates[earlier].get("uuid")), "b": str(mates[later].get("uuid")), "rule": rule}
                      for earlier, later, rule in conflict_pairs],
        "redundant": [{"uuid": str(mates[index].get("uuid")), "reason": reason,
                       "by": [str(mates[other].get("uuid")) for other in by]}
                      for index, reason, by in redundant_hits],
        "violated": [row["uuid"] for row in residual_rows if row["status"] == "violated"],
        "dof_unmeasured": [str(mates[index].get("uuid")) for group in groups for index in group["unmeasured_indices"]],
        "implicated": [str(mates[index].get("uuid")) for index in visible_mates if index in implicated],
        "suggested_suppress": [str(mates[index].get("uuid")) for index in sorted(flags)],
    }

    records = _table(store, _DIAGNOSES)
    for position, record in enumerate(records):
        if isinstance(record, _Mapping) and record.get("schema") == _SCHEMA and record.get("scope") == scope:
            payload["uuid"] = str(record.get("uuid") or _stable_uuid(scope))
            records[position] = payload
            return payload
    payload["uuid"] = _stable_uuid(scope)
    records.append(payload)
    return payload


# --------------------------------------------------------------------------------------
# 冻结接口：两个操作
# --------------------------------------------------------------------------------------
def diagnose_overdefined(sk_or_doc: Any, *, component: Any = None,
                         tolerance_nm: Any = 1000, tolerance_deg: Any = 0.05) -> list[str]:
    """诊断装配体的**过定义 / 冗余 / 配合冲突 / 残差违反**，返回命中配合的 uuid 列表。

    返回的是**配合行（mates）的 uuid**，顺序 = 配合表内顺序去重；"命中" = 矛盾双方、
    冗余（重复/被蕴含）、残差违反、以及解法建议要压制的那些。诊断记录 upsert 到
    ``<store>["diagnoses"]``（scope 唯一，uuid 由 scope 定值），所以重复调用不堆记录。

    ``component``：给定时只诊断该分量所在的子装配（该分量 + 与它直接相连的分量）。
    ``tolerance_nm`` / ``tolerance_deg``：调用方缺省容差；配合行自带 ``tolerance_nm`` /
    ``tolerance_deg`` 时以行为准（``-1`` = 未采 ⇒ 回落本缺省，三态口径）。
    """
    tol_nm = _int_nm(tolerance_nm, what="tolerance_nm")
    tol_deg = _float_num(tolerance_deg, what="tolerance_deg")
    if tol_nm < 0 or tol_deg < 0.0:
        raise _fail("invalid_argument", "tolerances must be non-negative",
                    tolerance_nm=tol_nm, tolerance_deg=tol_deg)
    store = _get_store(sk_or_doc, create=True)
    return list(_diagnose(store, component=component, tol_nm=tol_nm, tol_deg=tol_deg)["implicated"])


def apply_overdefined_resolution(sk_or_doc: Any, *, component: Any = None,
                                 tolerance_nm: Any = 1000, tolerance_deg: Any = 0.05) -> list[str]:
    """执行诊断给出的压制建议：把 ``suggested_suppress`` 里的配合行置 ``suppressed=True``。

    真数据后果 = 这些配合行从此退出生效集（DOF 账 / 残差 / 矛盾判定都按新状态重算），
    并立刻刷新 ``diagnoses`` 记录；**不动任何零部件位姿**。返回本次真正被压制的配合 uuid
    列表（已经是 suppressed 的不重复计入，故本操作幂等）。
    """
    tol_nm = _int_nm(tolerance_nm, what="tolerance_nm")
    tol_deg = _float_num(tolerance_deg, what="tolerance_deg")
    if tol_nm < 0 or tol_deg < 0.0:
        raise _fail("invalid_argument", "tolerances must be non-negative",
                    tolerance_nm=tol_nm, tolerance_deg=tol_deg)
    store = _get_store(sk_or_doc, create=True)
    record = _diagnose(store, component=component, tol_nm=tol_nm, tol_deg=tol_deg)
    wanted = {str(item) for item in record["suggested_suppress"]}
    affected: list[str] = []
    for row in _table(store, "mates"):
        if not isinstance(row, _Mapping):
            continue
        uuid = str(row.get("uuid"))
        if uuid in wanted and not _flag(row.get("suppressed")):
            row["suppressed"] = True
            row["suppressed_by"] = _OP_RESOLVE
            affected.append(uuid)
    if affected:
        _diagnose(store, component=component, tol_nm=tol_nm, tol_deg=tol_deg)
    return affected


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    _OP_DIAGNOSE: diagnose_overdefined,
    _OP_RESOLVE: apply_overdefined_resolution,
}


# --------------------------------------------------------------------------------------
# 自检（真跑族模块建装配体，硬断言；不依赖 pytest）
# --------------------------------------------------------------------------------------
def _selftest() -> int:
    checks = 0
    doc: dict[str, Any] = {"sketches": [], "extra": {}}
    refs = {
        "axis": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]},
        "face": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]},
    }
    first_component = _assembly.insert_component(doc, part_key="base-plate", refs=refs)[0]
    second_component = _assembly.insert_component(doc, part_key="shaft", refs=refs)[0]
    use_uuid = True
    try:
        _assembly.component_pose(doc, component=first_component)
    except CadError:
        use_uuid = False
    first, second = (first_component, second_component) if use_uuid else (0, 1)

    m_coincident = _assembly.mate_coincident(doc, refs=[[first, "face"], [second, "face"]])[0]
    m_axis = _assembly.mate_concentric(doc, refs=[[first, "axis"], [second, "axis"]])[0]
    m_face = _assembly.mate_concentric(doc, refs=[[first, "face"], [second, "face"]])[0]
    m_perp = _assembly.mate_perpendicular(doc, refs=[[first, "axis"], [second, "face"]])[0]
    store = _get_store(doc, create=True)

    # 断言 1-4：DOF 账 = 12 可用 vs 3+4+4+2=13 扣减 ⇒ 真过定义，且已落盘诊断记录
    hit = diagnose_overdefined(doc)
    record = store["diagnoses"][-1]
    assert record["verdict"] == "over_defined", record["verdict"]
    assert len(record["groups"]) == 1, record["groups"]
    group = record["groups"][0]
    assert (group["dof_available"], group["dof_removed"], group["dof_left"]) == (12, 13, -1), group
    assert hit and set(hit) <= {m_coincident, m_axis, m_face, m_perp}, hit
    checks += 4

    # 断言 5-7：真几何残差 —— 位姿被挪 5000nm 后重合配合必须报 violated 且残差量级正确
    _assembly.move_component(doc, components=[second], delta_nm=(5000, 0, 0))
    hit_moved = diagnose_overdefined(doc)
    residuals = {row["uuid"]: row for row in store["diagnoses"][-1]["residuals"]}
    assert residuals[m_coincident]["status"] == "violated", residuals[m_coincident]
    assert residuals[m_coincident]["residual_nm"] >= 4000, residuals[m_coincident]
    assert m_coincident in hit_moved, hit_moved
    checks += 3

    # 断言 8-9：挪回原位 ⇒ 残差全清零，但过定义仍在（DOF 账与残差是两件事）
    _assembly.move_component(doc, components=[second], delta_nm=(-5000, 0, 0))
    diagnose_overdefined(doc)
    record_back = store["diagnoses"][-1]
    assert record_back["violated"] == [], record_back["violated"]
    assert record_back["verdict"] == "over_defined", record_back["verdict"]
    checks += 2

    # 断言 10-13：解法有真后果 —— 压制集写回配合行，重诊断不再过定义、不再有命中
    flagged = apply_overdefined_resolution(doc)
    rows = {str(row.get("uuid")): row for row in store["mates"]}
    assert flagged, flagged
    assert all(rows[uuid]["suppressed"] is True for uuid in flagged), flagged
    record_fixed = store["diagnoses"][-1]
    assert all(item["dof_left"] >= 0 for item in record_fixed["groups"]), record_fixed["groups"]
    assert diagnose_overdefined(doc) == [], record_fixed["implicated"]
    checks += 4

    assert checks == 13, checks
    print("SELFTEST family=sw_assembly checks=%d verdict=over_defined->resolved mates=4" % checks)
    return checks


if __name__ == "__main__":
    _checks = _selftest()
    print("FEATURE_OK family=%s op=%s verdict=PASS assertions=%d" % (FAMILY, _OP_DIAGNOSE, _checks))
