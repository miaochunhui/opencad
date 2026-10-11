#!/usr/bin/env python3
"""装配族实现片 w22：**过定义与配合冲突诊断**（SolidWorks MateXpert 类能力）。

部件位置（本席只写这一个文件，不写 `__init__.py`）：

一、本族仍缺的 must_have（真源：`docs/research/cad-parity/status/sw-assembly.json` 与
`docs/research/cad-parity/features/sw-assembly.json`；按清单顺序编号 0..N-1，N = 3）：

  0. 配合对齐方向控制（aligned / anti-aligned 方向翻转）—— status = missing
  1. 过定义与配合冲突诊断（MateXpert 类）—— status = partial  ← 本席目标（13 % 3 = 1）
  2. 干涉检查（真布尔求交 / 干涉报表）—— status = partial

其余 18 条 must_have 在 status 里记 implemented（族模块 `ops/sw_assembly.py` 真实现）。
本席只做第 1 号，不碰 0 / 2 号。

二、为什么这条不是"已经有了"：族模块 `ops/sw_assembly.py:1469 diagnose_mates()` 只做**记录级**诊断
（同 kind + 同参考组件对 > 1 条 ⇒ 冗余），既不看几何，也不判"这组配合到底能不能同时满足"。
本片在 **L1 文档模型**（`doc.extra["sw_assembly"]` 的 `components` / `mates` 两张表，与族模块同一张表）
上补上真诊断：

1. **真几何残差**：按 `sw_assembly.py:_measure()` 的同一语义（点距 / 方向夹角 / 同轴偏移 / 沿轴投影，
   含 flip 方向语义）现算每条配合的残差与满足性 —— 三态：满足 / 违反 / 量不出（`None`）；
2. **真求解**：把这些配合写成约束方程（每类配合用**最小独立方程集**：重合 3 点 + 2 向、同轴 2 向 + 2 偏移、
   平行 2、垂直 1、角度 1、距离 1），在零部件位姿空间（每个自由件 6 个自由度）里用**带阻尼的高斯-牛顿
   （LM）+ 确定性多起点**真求解 —— "能不能同时满足"由求解结果判，不由计数猜；雅可比是中心差分数值雅可比
   （平移步长 1 nm、旋转步长 1e-6 rad），列归一化消 nm/rad 量纲差、行归一化等权；
3. **过定义 = 秩亏**：在求解得到的位姿上算雅可比，`冗余方程数 = 方程数 − 秩`（列归一化 + 行归一化 +
   部分主元，容差 1e-7）；再逐条配合做**留一秩比对**，定位到底是哪几条配合冗余；
4. **冲突 = 不可满足的极小集**：对不可满足的配合集合做**删除式 MUS 过滤**，给出极小冲突集
   （每条都不能少，"删掉任一条就满足了"），而不是把整组配合一股脑报红。

三、真数据后果（都写回 `doc.extra`，随 L1 往返存活；不改 `status` 字段 —— 那是 `mate_check()` 的）：

* `store["mate_diagnosis"]`：状态 / 方程数 / 秩 / 剩余自由度 / 冗余清单 / 冲突清单 / 未采清单（带 reason）/
  求解器账（起点数、健康起点数、最优残差、是否可满足）；
* 每条配合行上打 `diagnosis`（`conflict` / `redundant` / `unmeasured` / `ok` / `suppressed`）、
  `over_defined`、`conflict` 三个字段；
* `store["mate_conflict"]`：极小冲突集；
* `store["mate_resolution"]`：`resolve_over_definition()` 的**保序修复**账（压缩或删除 + 修复前后对照 +
  `verified`）——保序 = 保留先记录的那条，后记录且冗余/冲突的那条被压缩；
* `store["mate_dof"]`：逐件剩余自由度（雅可比零空间在该件 6 列上的投影秩）。

四、口径与边界（写死，避免假绿）：

* 未采（缺参考几何、缺方向、组件缺失/被压缩、或配合类型本片写不出方程 —— 如 `tangent` / `lock`）
  的配合**不进**方程集，单独列进 `unmeasured`（带 `reason`），并把 `complete` 记 `False`；
  绝不静默当"满足"或"满足不了"；
* 秩判据与剩余自由度是**位姿局部**的（非凸约束系统的局部秩），本层不是全局符号求解器；
* 全固定（无自由件）时仍可诊断：此时"能不能满足"退化成"当前位姿满不满足"，冲突照报；
* 数值失败（残差/雅可比非有限，或所有起点都不健康）显式抛 `CadError(reason="numeric_failure")`，
  不返回"没发现问题"；
* 只读侧（诊断）不改任何组件位姿；`resolve_over_definition()` 才动数据（压缩 / 删除配合行），
  且修复后**复诊**：仍有冗余或冲突 ⇒ 抛 `CadError(reason="repair_failed")` 并把诊断账留在盘上。

五、自检（文件自带，硬断言）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/features/sw_assembly__w22.py

通过 ⇒ 打 `FEATURE_OK family=sw_assembly op=diagnose_over_defined verdict=PASS`。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/sw_assembly__w22.py 的引导
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
from collections.abc import Mapping as _Mapping
from collections.abc import MutableMapping as _MutableMapping
from typing import Any, Callable, Sequence

from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "diagnose_over_defined",
    "mate_conflict_set",
    "resolve_over_definition",
    "mate_dof_report",
    "selftest",
    "main",
]

#: 族 id（冻结接口）。
FAMILY = "sw_assembly"

#: 装配表在文档 `extra` 上的键名（与 `ops/sw_assembly.py` 同一张表）。
_ASM_KEY = "sw_assembly"
#: 缺省容差：长度 1 µm（整数纳米）、角度 1e-6 度（与族模块一致）。
_DEFAULT_TOL_NM = 1_000
_DEFAULT_TOL_DEG = 1e-6
#: 不适用哨兵。
_NA = "NA"
#: 未采哨兵（与全仓三态口径一致）。
_UNMEASURED = -1
#: 秩容差（行/列归一化之后）。
_RANK_TOL = 1e-7
#: 数值雅可比步长：平移 nm（点/方向对平移是严格线性，任何步长都精确）、旋转 rad。
_FD_TRANS_NM = 1.0
_FD_ROT_RAD = 1e-6
#: LM 迭代上限与阻尼范围。
_GN_ITERS = 60
_LM_START = 1e-6
_LM_MAX = 1e12

#: 本片能写成约束方程的配合类型；其余 kind（如 tangent / lock）记"不支持"，见 `_mate_equations()`。
_EQUATION_KINDS: tuple[str, ...] = ("coincident", "parallel", "perpendicular", "concentric",
                                    "distance", "angle")

#: 多起点扰动（度）：同一姿态下的全局刚体旋转**不能**打破约束奇异性，故按件取不同符号。
_TILTS: tuple[tuple[float, float, float], ...] = (
    (35.0, 0.0, 0.0), (0.0, 35.0, 0.0), (0.0, 0.0, 35.0), (0.0, 0.0, -35.0),
    (25.0, -25.0, 0.0), (0.0, 25.0, -25.0), (-25.0, 0.0, 25.0), (20.0, 20.0, 20.0),
)

#: 自检硬断言计数器（只统计 `_assert()` 过的断言，打印出来的数就是真跑过的数）。
_ASSERTS = [0]


# ======================================================================================
# 错误与对象访问
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(detail, reason=reason, **details)


def _assert(condition: Any, message: str) -> None:
    """自检硬断言（失败即抛，不吞）。"""
    _ASSERTS[0] += 1
    if not condition:
        raise AssertionError(message)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, _Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _holder(target: Any) -> Any:
    """`sk_or_doc` → 能挂装配表的可变容器（文档走 `extra`；草图映射/对象走自身）。"""
    extra = _get(target, "extra", None)
    if isinstance(extra, _MutableMapping):
        return extra
    if isinstance(target, _MutableMapping):
        return target
    raise _fail("bad_target", "sk_or_doc must be a document with extra or a mutable mapping",
                value_type=type(target).__name__)


def _store(target: Any, *, create: bool = True) -> dict[str, Any] | None:
    """取（必要时创建）装配表；`create=False` 且没有表 ⇒ `None`（读侧不许凭空建表）。"""
    holder = _holder(target)
    value = holder.get(_ASM_KEY)
    if isinstance(value, _MutableMapping):
        return value
    if not create:
        return None
    value = {"schema": 1, "components": [], "mates": [], "interferences": [], "explodes": []}
    holder[_ASM_KEY] = value
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


def _rows(store: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """逐行取出装配表（行不是映射 ⇒ `corrupt_store`）。"""
    out: list[dict[str, Any]] = []
    for row in _table(store, key):
        if not isinstance(row, _MutableMapping):
            raise _fail("corrupt_store", "assembly row is not a mapping", table=key,
                        value_type=type(row).__name__)
        out.append(row)
    return out


# ======================================================================================
# 线性代数（纯标准库）
# ======================================================================================


def _sub3(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2])]


def _add3(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2])]


def _dot3(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _cross3(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
            float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
            float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])]


def _norm3(a: Sequence[float]) -> float:
    return math.sqrt(_dot3(a, a))


def _unit3(value: Sequence[float]) -> list[float]:
    """归一化；零向量报 `degenerate_vector`（不静默返回零向量）。"""
    norm = _norm3(value)
    if norm <= 1e-12:
        raise _fail("degenerate_vector", "cannot normalize a zero vector", norm=float(norm))
    return [float(value[0]) / norm, float(value[1]) / norm, float(value[2]) / norm]


def _skew(value: Sequence[float]) -> list[list[float]]:
    x, y, z = float(value[0]), float(value[1]), float(value[2])
    return [[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]]


def _mat_mul(outer: Sequence[Sequence[float]], inner: Sequence[Sequence[float]]) -> list[list[float]]:
    return [[sum(float(outer[i][k]) * float(inner[k][j]) for k in range(3)) for j in range(3)]
            for i in range(3)]


def _mat_vec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(float(matrix[i][k]) * float(vector[k]) for k in range(3)) for i in range(3)]


def _identity3() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _rot_vec(omega: Sequence[float]) -> list[list[float]]:
    """罗德里格斯公式 `exp([w]x)`（小角走级数，避免 0 除）。"""
    x, y, z = float(omega[0]), float(omega[1]), float(omega[2])
    theta = math.sqrt(x * x + y * y + z * z)
    k = _skew([x, y, z])
    k2 = _mat_mul(k, k)
    if theta < 1e-9:
        s, c = 1.0 - theta * theta / 6.0, 0.5 - theta * theta / 24.0
    else:
        s, c = math.sin(theta) / theta, (1.0 - math.cos(theta)) / (theta * theta)
    base = _identity3()
    return [[base[i][j] + s * k[i][j] + c * k2[i][j] for j in range(3)] for i in range(3)]


def _basis2(axis: Sequence[float]) -> tuple[list[float], list[float]]:
    """与单位向量正交的一组**确定性**规范正交基（不随机）。"""
    helper = [1.0, 0.0, 0.0] if abs(float(axis[0])) < 0.9 else [0.0, 1.0, 0.0]
    first = _unit3(_cross3(axis, helper))
    second = _unit3(_cross3(axis, first))
    return first, second


def _rref(matrix: Sequence[Sequence[float]], tol: float = _RANK_TOL):
    """列归一化 + 行归一化 + 部分主元的高斯-约当消元 ⇒ `(秩, RREF 行, 主元列)`。"""
    rows = [[float(value) for value in row] for row in matrix]
    if not rows:
        return 0, [], []
    ncols = len(rows[0])
    if ncols == 0:
        return 0, [list(row) for row in rows], []
    nrows = len(rows)
    for j in range(ncols):
        norm = math.sqrt(sum(rows[i][j] * rows[i][j] for i in range(nrows)))
        if norm > 0.0:
            for i in range(nrows):
                rows[i][j] /= norm
    for i in range(nrows):
        norm = math.sqrt(sum(value * value for value in rows[i]))
        if norm > 0.0:
            rows[i] = [value / norm for value in rows[i]]
    pivots: list[int] = []
    rank = 0
    for col in range(ncols):
        if rank >= nrows:
            break
        best = rank
        for i in range(rank, nrows):
            if abs(rows[i][col]) > abs(rows[best][col]):
                best = i
        if abs(rows[best][col]) <= tol:
            continue
        rows[rank], rows[best] = rows[best], rows[rank]
        pivot = rows[rank][col]
        rows[rank] = [value / pivot for value in rows[rank]]
        for i in range(nrows):
            if i != rank and rows[i][col] != 0.0:
                factor = rows[i][col]
                rows[i] = [rows[i][k] - factor * rows[rank][k] for k in range(ncols)]
        pivots.append(col)
        rank += 1
    return rank, rows, pivots


def _rank_of(matrix: Sequence[Sequence[float]]) -> int:
    return _rref(matrix)[0]


def _nullspace(rref_rows: Sequence[Sequence[float]], pivots: Sequence[int],
               ncols: int) -> list[list[float]]:
    """RREF ⇒ 零空间基（每个自由列一支）。"""
    free = [col for col in range(ncols) if col not in set(pivots)]
    basis: list[list[float]] = []
    for col in free:
        vector = [0.0] * ncols
        vector[col] = 1.0
        for index, pivot_col in enumerate(pivots):
            vector[pivot_col] = -float(rref_rows[index][col])
        basis.append(vector)
    return basis


def _solve_linear(matrix: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float] | None:
    """稠密线性方程 `A x = b`（部分主元）；奇异 ⇒ `None`（调用方加大阻尼，不硬撑）。"""
    size = len(rhs)
    if size == 0:
        return []
    work = [[float(matrix[i][j]) for j in range(size)] + [float(rhs[i])] for i in range(size)]
    for col in range(size):
        best = col
        for i in range(col, size):
            if abs(work[i][col]) > abs(work[best][col]):
                best = i
        if abs(work[best][col]) < 1e-18:
            return None
        work[col], work[best] = work[best], work[col]
        pivot = work[col][col]
        work[col] = [value / pivot for value in work[col]]
        for i in range(size):
            if i != col and work[i][col] != 0.0:
                factor = work[i][col]
                work[i] = [work[i][k] - factor * work[col][k] for k in range(size + 1)]
    return [work[i][size] for i in range(size)]


def _finite(values: Any) -> bool:
    """嵌套数值结构里全是有限数（NaN/Inf 一律算不健康）。"""
    if isinstance(values, (list, tuple)):
        return all(_finite(item) for item in values)
    try:
        return math.isfinite(float(values))
    except (TypeError, ValueError):
        return False


# ======================================================================================
# 位姿状态与参考几何
# ======================================================================================


def _floats3(value: Any) -> list[float] | None:
    """三段数值入参 → `[float×3]`；形态不对 ⇒ `None`（不抛，交由三态处理）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


def _as_matrix(value: Any) -> list[list[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    rows = [_floats3(row) for row in value]
    if any(row is None for row in rows):
        return None
    return [list(row) for row in rows]  # type: ignore[arg-type]


def _build_state(store: dict[str, Any]) -> list[dict[str, Any]]:
    """装配表 ⇒ 位姿状态；每个**自由**件分配 6 个变量 `(dt_nm×3, dω_rad×3)`。"""
    state: list[dict[str, Any]] = []
    offset = 0
    for row in _rows(store, "components"):
        if row.get("suppressed"):
            continue
        at = _floats3(row.get("at_nm")) or [0.0, 0.0, 0.0]
        rotation = _as_matrix(row.get("rotation")) or _identity3()
        free = not bool(row.get("fixed"))
        refs = row.get("refs")
        state.append({"uuid": str(row.get("uuid")), "at": list(at), "rot": rotation, "free": free,
                      "refs": refs if isinstance(refs, _Mapping) else {},
                      "offset": offset if free else -1})
        if free:
            offset += 6
    return state


def _index(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["uuid"]: item for item in items}


def _perturbed(state: Sequence[dict[str, Any]], q: Sequence[float]) -> list[dict[str, Any]]:
    """基础位姿 + 变量增量 ⇒ 试算位姿（不改基础状态）。"""
    out: list[dict[str, Any]] = []
    for item in state:
        at = list(item["at"])
        rotation = [list(row) for row in item["rot"]]
        if item["free"]:
            base = int(item["offset"])
            at = [at[i] + float(q[base + i]) for i in range(3)]
            delta = [float(q[base + 3]), float(q[base + 4]), float(q[base + 5])]
            rotation = _mat_mul(_rot_vec(delta), rotation)
        out.append({"uuid": item["uuid"], "at": at, "rot": rotation, "refs": item["refs"],
                    "free": item["free"], "offset": item["offset"]})
    return out


def _world_of(item: dict[str, Any], name: str) -> dict[str, Any]:
    """局部参考几何 → 世界形态 `{"point": [float×3]|None, "dir": unit|None}`。"""
    refs = item["refs"]
    if not isinstance(refs, _Mapping) or name not in refs:
        raise _fail("unknown_reference", "component has no such named reference", name=name,
                    component=item["uuid"])
    geometry = refs[name]
    if not isinstance(geometry, _Mapping):
        raise _fail("corrupt_store", "named reference is not a mapping", name=name,
                    component=item["uuid"])
    local_point = _floats3(geometry.get("point_nm"))
    local_dir = _floats3(geometry.get("dir"))
    point = None if local_point is None else _add3(_mat_vec(item["rot"], local_point), item["at"])
    direction = None
    if local_dir is not None:
        rotated = _mat_vec(item["rot"], local_dir)
        norm = _norm3(rotated)
        if norm > 1e-12:
            direction = [rotated[i] / norm for i in range(3)]
    return {"point": point, "dir": direction}


def _mate_worlds(items: dict[str, dict[str, Any]], mate: dict[str, Any]):
    """配合 → 两端世界几何（组件缺失/被压缩 ⇒ `unknown_component`，由调用方记"未采"）。"""
    refs = mate.get("refs")
    if not isinstance(refs, (list, tuple)) or len(refs) != 2:
        raise _fail("corrupt_store", "mate refs must be a pair of [component, ref]",
                    mate=str(mate.get("uuid")))
    ends = []
    for entry in refs:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise _fail("corrupt_store", "each mate ref must be [component_uuid, ref_name]",
                        mate=str(mate.get("uuid")))
        item = items.get(str(entry[0]))
        if item is None:
            raise _fail("unknown_component", "mate references a component not in this assembly",
                        component=str(entry[0]), mate=str(mate.get("uuid")))
        ends.append(_world_of(item, str(entry[1])))
    return ends[0], ends[1]


# ======================================================================================
# 配合方程与满足性（语义与 sw_assembly.py:_measure 对齐）
# ======================================================================================


def _mate_equations(mate: dict[str, Any], first: dict[str, Any],
                    second: dict[str, Any]) -> list[float] | None:
    """一条配合 ⇒ **最小独立方程行**的残差；`None` = 本层写不出（未采 / 不支持的 kind）。"""
    kind = str(mate.get("kind") or "")
    if kind not in _EQUATION_KINDS:
        return None
    point_a, dir_a = first["point"], first["dir"]
    point_b, dir_b = second["point"], second["dir"]
    flip = bool(mate.get("flip"))
    if kind == "coincident":
        if point_a is None or point_b is None:
            return None
        rows = [point_b[i] - point_a[i] for i in range(3)]
        if dir_a is not None and dir_b is not None:
            first_basis, second_basis = _basis2(dir_a)
            cross = _cross3(dir_a, dir_b)
            rows.append(_dot3(cross, first_basis))
            rows.append(_dot3(cross, second_basis))
        return rows
    if kind == "parallel":
        if dir_a is None or dir_b is None:
            return None
        first_basis, second_basis = _basis2(dir_a)
        cross = _cross3(dir_a, dir_b)
        return [_dot3(cross, first_basis), _dot3(cross, second_basis)]
    if kind == "perpendicular":
        if dir_a is None or dir_b is None:
            return None
        return [_dot3(dir_a, dir_b)]
    if kind == "angle":
        value = mate.get("value_deg")
        if dir_a is None or dir_b is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return [_dot3(dir_a, dir_b) - math.cos(math.radians(float(value)))]
    if kind == "concentric":
        if dir_a is None or dir_b is None or point_a is None or point_b is None:
            return None
        first_basis, second_basis = _basis2(dir_a)
        cross = _cross3(dir_a, dir_b)
        delta = _sub3(point_b, point_a)
        return [_dot3(cross, first_basis), _dot3(cross, second_basis),
                _dot3(delta, first_basis), _dot3(delta, second_basis)]
    if kind == "distance":
        value = mate.get("value_nm")
        if point_a is None or point_b is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        axis = dir_a if dir_a is not None else [0.0, 0.0, 1.0]
        sign = -1.0 if flip else 1.0
        return [sign * _dot3(_sub3(point_b, point_a), axis) - abs(float(value))]
    return None


def _mate_measure(mate: dict[str, Any], first: dict[str, Any], second: dict[str, Any],
                  *, tol_nm: Any = None, tol_deg: Any = None):
    """现算满足性 ⇒ `(True/False/None, 量出的长度 nm|-1, 量出的角度|None)`；`None` = 量不出。"""
    kind = str(mate.get("kind") or "")
    flip = bool(mate.get("flip"))
    tolerance_nm = mate.get("tolerance_nm") if tol_nm is None else tol_nm
    tolerance_deg = mate.get("tolerance_deg") if tol_deg is None else tol_deg
    if isinstance(tolerance_nm, bool) or not isinstance(tolerance_nm, (int, float)) or float(tolerance_nm) < 0:
        tolerance_nm = _DEFAULT_TOL_NM
    if isinstance(tolerance_deg, bool) or not isinstance(tolerance_deg, (int, float)) or float(tolerance_deg) < 0:
        tolerance_deg = _DEFAULT_TOL_DEG
    limit_nm = float(tolerance_nm)
    limit_deg = float(tolerance_deg)
    point_a, dir_a = first["point"], first["dir"]
    point_b, dir_b = second["point"], second["dir"]
    angle = None
    if dir_a is not None and dir_b is not None:
        angle = math.degrees(math.acos(max(-1.0, min(1.0, _dot3(dir_a, dir_b)))))
    wanted_angle = 180.0 if flip else 0.0
    if kind == "coincident":
        if point_a is None or point_b is None:
            return None, _UNMEASURED, None
        distance = _norm3(_sub3(point_b, point_a))
        satisfied = distance <= limit_nm
        if angle is not None:
            satisfied = satisfied and abs(angle - wanted_angle) <= limit_deg
        return bool(satisfied), int(round(distance)), angle
    if kind == "parallel":
        if angle is None:
            return None, _UNMEASURED, None
        return bool(abs(angle - wanted_angle) <= limit_deg), _UNMEASURED, angle
    if kind == "perpendicular":
        if angle is None:
            return None, _UNMEASURED, None
        return bool(abs(angle - 90.0) <= limit_deg), _UNMEASURED, angle
    if kind == "angle":
        value = mate.get("value_deg")
        if angle is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return None, _UNMEASURED, None
        return bool(abs(angle - float(value)) <= limit_deg), _UNMEASURED, angle
    if kind == "concentric":
        if angle is None or point_a is None or point_b is None or dir_a is None:
            return None, _UNMEASURED, None
        delta = _sub3(point_b, point_a)
        projection = _dot3(delta, dir_a)
        offset = _norm3([delta[i] - projection * dir_a[i] for i in range(3)])
        satisfied = abs(angle - wanted_angle) <= limit_deg and offset <= limit_nm
        return bool(satisfied), int(round(offset)), angle
    if kind == "distance":
        value = mate.get("value_nm")
        if point_a is None or point_b is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            return None, _UNMEASURED, None
        axis = dir_a if dir_a is not None else [0.0, 0.0, 1.0]
        sign = -1.0 if flip else 1.0
        measured = sign * _dot3(_sub3(point_b, point_a), axis)
        return bool(abs(measured - abs(float(value))) <= limit_nm), int(round(measured)), None
    return None, _UNMEASURED, None


# ======================================================================================
# 求解：残差 / 数值雅可比 / 阻尼高斯-牛顿（LM）+ 多起点
# ======================================================================================


def _rows_of(mate: dict[str, Any], items: dict[str, dict[str, Any]]) -> list[float]:
    """实测方程行；写不出就显式抛（可解集合里不该出现）。"""
    first, second = _mate_worlds(items, mate)
    rows = _mate_equations(mate, first, second)
    if rows is None:
        raise _fail("unmeasured_mate", "mate cannot be written as equations",
                    mate=str(mate.get("uuid")))
    return rows


def _residuals_only(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]],
                    q: Sequence[float]) -> list[float]:
    items = _index(_perturbed(state, q))
    rows: list[float] = []
    for mate in mates:
        rows.extend(_rows_of(mate, items))
    return rows


def _system(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]], q: Sequence[float],
            nvars: int):
    """`(残差, 数值雅可比)`（中心差分）。"""
    residual = _residuals_only(state, mates, q)
    count = len(residual)
    jacobian = [[0.0] * nvars for _ in range(count)]
    for column in range(nvars):
        step = _FD_ROT_RAD if (column % 6) >= 3 else _FD_TRANS_NM
        plus = list(q)
        minus = list(q)
        plus[column] += step
        minus[column] -= step
        rows_plus = _residuals_only(state, mates, plus)
        rows_minus = _residuals_only(state, mates, minus)
        for i in range(count):
            jacobian[i][column] = (rows_plus[i] - rows_minus[i]) / (2.0 * step)
    return residual, jacobian


def _scale_system(residual: Sequence[float], jacobian: Sequence[Sequence[float]], nvars: int):
    """列归一化（消 nm/rad 量纲差）+ 行归一化（等权）⇒ `(Jn, rs, 列尺度, 行尺度)`。"""
    scaled = [list(row) for row in jacobian]
    columns = [1.0] * nvars
    for column in range(nvars):
        norm = math.sqrt(sum(scaled[i][column] * scaled[i][column] for i in range(len(scaled))))
        if norm > 0.0:
            columns[column] = norm
            for i in range(len(scaled)):
                scaled[i][column] /= norm
    rows_scale = [1.0] * len(scaled)
    values = [float(value) for value in residual]
    for i in range(len(scaled)):
        norm = math.sqrt(sum(value * value for value in scaled[i]))
        if norm > 0.0:
            rows_scale[i] = norm
            scaled[i] = [value / norm for value in scaled[i]]
            values[i] = values[i] / norm
    return scaled, values, columns, rows_scale


def _scaled_cost(residual: Sequence[float], rows_scale: Sequence[float]) -> float:
    """用给定行尺度算残差平方和（与 `_scale_system` 同一口径，但不用重算雅可比）。"""
    total = 0.0
    for i, value in enumerate(residual):
        scale = rows_scale[i] if i < len(rows_scale) and rows_scale[i] > 0.0 else 1.0
        term = float(value) / scale
        total += term * term
    return total


def _starts(nvars: int, count: int) -> list[list[float]]:
    """确定性多起点：第 1 个是当前位姿，其余按件取**不同符号**的倾角（打破全局旋转对称）。"""
    plans = [[0.0] * nvars]
    for tilt in _TILTS[: max(0, int(count) - 1)]:
        plan = [0.0] * nvars
        for base in range(0, nvars, 6):
            sign = 1.0 if (base // 6) % 2 == 0 else -1.0
            plan[base + 3] = math.radians(tilt[0]) * sign
            plan[base + 4] = math.radians(tilt[1]) * sign
            plan[base + 5] = math.radians(tilt[2]) * sign
        plans.append(plan)
    return plans


def _all_satisfied(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]],
                   q: Sequence[float], tol_nm: Any, tol_deg: Any) -> bool:
    items = _index(_perturbed(state, q))
    for mate in mates:
        first, second = _mate_worlds(items, mate)
        satisfied, _measured, _angle = _mate_measure(mate, first, second, tol_nm=tol_nm, tol_deg=tol_deg)
        if satisfied is not True:
            return False
    return True


def _lm(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]], q0: Sequence[float],
        nvars: int, tol_nm: Any, tol_deg: Any):
    """阻尼高斯-牛顿 ⇒ `(最优位姿, 该位姿是否全满足, 数值是否健康, 最优残差代价)`。"""
    q = [float(value) for value in q0]
    best_q = list(q)
    best_cost = float("inf")
    healthy = False
    damping = _LM_START
    for _ in range(_GN_ITERS):
        residual, jacobian = _system(state, mates, q, nvars)
        if not _finite(residual) or not _finite(jacobian):
            break
        healthy = True
        scaled, values, columns, rows_scale = _scale_system(residual, jacobian, nvars)
        cost = sum(value * value for value in values)
        if cost < best_cost:
            best_cost, best_q = cost, list(q)
        if max((abs(value) for value in values), default=0.0) <= 1e-9:
            break
        improved = False
        for _attempt in range(8):
            normal = [[0.0] * nvars for _ in range(nvars)]
            gradient = [0.0] * nvars
            for i in range(len(scaled)):
                for p in range(nvars):
                    value = scaled[i][p]
                    if value == 0.0:
                        continue
                    gradient[p] -= value * values[i]
                    for k in range(nvars):
                        normal[p][k] += value * scaled[i][k]
            for p in range(nvars):
                normal[p][p] += damping
            step = _solve_linear(normal, gradient)
            if step is None:
                damping *= 10.0
                if damping > _LM_MAX:
                    break
                continue
            candidate = [q[p] + step[p] / (columns[p] if columns[p] > 0.0 else 1.0)
                         for p in range(nvars)]
            candidate_residual = _residuals_only(state, mates, candidate)
            if not _finite(candidate_residual):
                damping *= 10.0
                if damping > _LM_MAX:
                    break
                continue
            candidate_cost = _scaled_cost(candidate_residual, rows_scale)
            if candidate_cost < cost:
                q = candidate
                if candidate_cost < best_cost:
                    best_cost = candidate_cost
                    best_q = list(candidate)
                damping = max(damping / 3.0, 1e-9)
                improved = True
                break
            damping *= 10.0
            if damping > _LM_MAX:
                break
        if not improved:
            break
    return best_q, bool(_all_satisfied(state, mates, best_q, tol_nm, tol_deg)), bool(healthy), best_cost


def _satisfiable(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]], nvars: int,
                 count: int, tol_nm: Any, tol_deg: Any) -> bool:
    """这组配合能不能**同时**满足（多起点真求解；全起点数值不健康 ⇒ 显式报错，不返回"满足不了"）。"""
    if not mates:
        return True
    if nvars == 0:
        return _all_satisfied(state, mates, [], tol_nm, tol_deg)
    healthy = 0
    for plan in _starts(nvars, count):
        _q, satisfied, ok_health, _cost = _lm(state, mates, plan, nvars, tol_nm, tol_deg)
        if ok_health:
            healthy += 1
        if satisfied:
            return True
    if healthy == 0:
        raise _fail("numeric_failure", "no finite configuration while testing satisfiability",
                    mates=[str(mate.get("uuid")) for mate in mates])
    return False


def _search(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]], nvars: int,
            count: int, tol_nm: Any, tol_deg: Any):
    """整组配合的多起点求解 ⇒ `(最优位姿, 是否可满足, 求解器账)`。"""
    if nvars == 0:
        satisfied = _all_satisfied(state, mates, [], tol_nm, tol_deg)
        return [], bool(satisfied), {"starts": 1, "healthy_starts": 1, "best_residual": 0.0,
                                     "satisfiable": bool(satisfied), "mode": "pose_only"}
    plans = _starts(nvars, count)
    best_q = list(plans[0])
    best_cost = float("inf")
    satisfied_any = False
    healthy_starts = 0
    for plan in plans:
        q, satisfied, healthy, cost = _lm(state, mates, plan, nvars, tol_nm, tol_deg)
        if healthy:
            healthy_starts += 1
        if cost < best_cost:
            best_cost, best_q = cost, list(q)
        if satisfied:
            satisfied_any = True
            best_q = list(q)
            best_cost = cost
            break
    if healthy_starts == 0:
        raise _fail("numeric_failure", "constraint solve produced no finite configuration",
                    mates=[str(mate.get("uuid")) for mate in mates])
    return best_q, bool(satisfied_any), {
        "starts": len(plans),
        "healthy_starts": healthy_starts,
        "best_residual": float(best_cost) if math.isfinite(best_cost) else -1.0,
        "satisfiable": bool(satisfied_any),
        "mode": "solved",
    }


def _minimal_conflict(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]], nvars: int,
                      count: int, tol_nm: Any, tol_deg: Any) -> list[dict[str, Any]]:
    """删除式 MUS：逐步删掉"删了也还是不满足"的配合，剩下**极小冲突集**（每条都不能少）。"""
    keep = list(mates)
    for mate in list(mates):
        trial = [item for item in keep if item is not mate]
        if not _satisfiable(state, trial, nvars, count, tol_nm, tol_deg):
            keep = trial
    return keep


# ======================================================================================
# 诊断主体
# ======================================================================================


def _default_result() -> dict[str, Any]:
    return {"store": None, "mates_total": 0, "mates_checked": 0, "unmeasured": [], "unsupported": [],
            "redundant": [], "conflicting": [], "over_defined": [], "equations": 0, "rank": 0,
            "dof_remaining": 0, "nvars": 0, "free": [], "fixed": [], "under_defined": [],
            "fully_defined": [], "components_dof": {}, "satisfiable": True, "complete": True,
            "status": "ok", "solver": {"starts": 0, "healthy_starts": 0, "best_residual": 0.0,
                                       "satisfiable": True, "mode": "no_store"}}


def _starts_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "starts must be an integer", value=repr(value))
    if value < 1 or value > 16:
        raise _fail("out_of_range", "starts must be within 1..16", value=int(value))
    return int(value)


def _analyse(target: Any, *, tol_nm: Any = None, tol_deg: Any = None, starts: int = 6,
             write: bool = True) -> dict[str, Any]:
    """全量诊断（不抛"没问题"式的空结论：未采条目带 reason 进 `unmeasured`）。"""
    result = _default_result()
    store = _store(target, create=False)
    result["store"] = store
    if store is None:
        return result
    state = _build_state(store)
    result["free"] = [item["uuid"] for item in state if item["free"]]
    result["fixed"] = [item["uuid"] for item in state if not item["free"]]
    nvars = 6 * len(result["free"])
    result["nvars"] = nvars
    active = [mate for mate in _rows(store, "mates") if not mate.get("suppressed")]
    result["mates_total"] = len(active)
    items_initial = _index(_perturbed(state, [0.0] * nvars))
    measurable: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    for mate in active:
        uuid_text = str(mate.get("uuid"))
        if str(mate.get("kind") or "") not in _EQUATION_KINDS:
            unmeasured.append({"mate": uuid_text, "reason": "unsupported_kind"})
            continue
        try:
            first, second = _mate_worlds(items_initial, mate)
            rows = _mate_equations(mate, first, second)
            satisfied, _measured, _angle = _mate_measure(mate, first, second,
                                                         tol_nm=tol_nm, tol_deg=tol_deg)
        except CadError as exc:
            unmeasured.append({"mate": uuid_text, "reason": str(exc.reason or "unmeasured")})
            continue
        if rows is None or satisfied is None:
            unmeasured.append({"mate": uuid_text, "reason": "unmeasured_geometry"})
            continue
        measurable.append(mate)
    result["unmeasured"] = unmeasured
    result["unsupported"] = [item["mate"] for item in unmeasured if item["reason"] == "unsupported_kind"]
    result["mates_checked"] = len(measurable)
    result["complete"] = not unmeasured
    q_best: list[float] = [0.0] * nvars
    if measurable:
        q_best, satisfiable, solver = _search(state, measurable, nvars, starts, tol_nm, tol_deg)
        result["satisfiable"] = bool(satisfiable)
        result["solver"] = solver
        if not satisfiable:
            conflict = _minimal_conflict(state, measurable, nvars, starts, tol_nm, tol_deg)
            result["conflicting"] = [str(mate.get("uuid")) for mate in conflict]
    conflicting = set(result["conflicting"])
    consistent = [mate for mate in measurable if str(mate.get("uuid")) not in conflicting]
    items_best = _index(_perturbed(state, q_best))
    rows: list[float] = []
    row_index: dict[str, int] = {}
    for mate in consistent:
        uuid_text = str(mate.get("uuid"))
        row_index[uuid_text] = len(rows)
        rows.extend(_rows_of(mate, items_best))
    result["equations"] = len(rows)
    # 秩的输入必须是**方程行矩阵**（每行 nvars 列的雅可比），不是残差标量列表；
    # `rows` 只做逐配合的方程计数与行号账，其行序与雅可比行一一对应。
    jacobian_rows = _system(state, consistent, q_best, nvars)[1] if consistent else []
    rank, _rref_rows, _pivots = _rref(jacobian_rows)
    result["rank"] = rank
    redundant: list[str] = []
    if consistent and len(rows) - rank > 0:
        for mate in consistent:
            uuid_text = str(mate.get("uuid"))
            begin = row_index[uuid_text]
            span = len(_rows_of(mate, items_best))
            others = jacobian_rows[:begin] + jacobian_rows[begin + span:]
            if _rref(others)[0] == rank:
                redundant.append(uuid_text)
    result["redundant"] = redundant
    over_set = conflicting | set(redundant)
    result["over_defined"] = [str(mate.get("uuid")) for mate in active
                              if str(mate.get("uuid")) in over_set]
    all_rows = _system(state, measurable, q_best, nvars)[1] if measurable else []
    rank_all, rref_all, pivots_all = _rref(all_rows)
    result["dof_remaining"] = max(0, nvars - rank_all)
    basis = _nullspace(rref_all, pivots_all, nvars) if nvars else []
    component_dof: dict[str, int] = {}
    for item in state:
        if not item["free"]:
            component_dof[item["uuid"]] = 0
            continue
        offset = int(item["offset"])
        block = [vector[offset:offset + 6] for vector in basis]
        component_dof[item["uuid"]] = _rank_of(block)
    result["components_dof"] = component_dof
    result["under_defined"] = [item["uuid"] for item in state
                               if item["free"] and component_dof.get(item["uuid"], 0) > 0]
    result["fully_defined"] = [item["uuid"] for item in state
                               if item["free"] and component_dof.get(item["uuid"], 0) == 0]
    if result["conflicting"]:
        result["status"] = "conflict"
    elif result["redundant"]:
        result["status"] = "over_defined"
    else:
        result["status"] = "ok"
    if write:
        _write_records(store, result)
    return result


def _write_records(store: dict[str, Any], result: dict[str, Any]) -> None:
    """把诊断账写回装配表（真数据后果；不动 `status`，那是 `mate_check()` 的字段）。"""
    store["mate_diagnosis"] = {
        "schema": 1,
        "status": result["status"],
        "complete": bool(result["complete"]),
        "mates_total": int(result["mates_total"]),
        "mates_checked": int(result["mates_checked"]),
        "equations": int(result["equations"]),
        "rank": int(result["rank"]),
        "dof_remaining": int(result["dof_remaining"]),
        "nvars": int(result["nvars"]),
        "redundant": list(result["redundant"]),
        "conflicting": list(result["conflicting"]),
        "unmeasured": [dict(item) for item in result["unmeasured"]],
        "unsupported": list(result["unsupported"]),
        "solver": dict(result["solver"]),
    }
    store["mate_conflict"] = {"schema": 1, "mates": list(result["conflicting"]),
                              "size": len(result["conflicting"]),
                              "checked": bool(result["mates_checked"])}
    store["mate_dof"] = {"schema": 1, "dof_remaining": int(result["dof_remaining"]),
                         "free": list(result["free"]), "fully_defined": list(result["fully_defined"]),
                         "components": dict(result["components_dof"])}
    conflicting = set(result["conflicting"])
    redundant = set(result["redundant"])
    unmeasured = {item["mate"] for item in result["unmeasured"]}
    for mate in _rows(store, "mates"):
        uuid_text = str(mate.get("uuid"))
        if mate.get("suppressed"):
            label, over, conflict = "suppressed", False, False
        elif uuid_text in conflicting:
            label, over, conflict = "conflict", True, True
        elif uuid_text in redundant:
            label, over, conflict = "redundant", True, False
        elif uuid_text in unmeasured:
            label, over, conflict = "unmeasured", False, False
        else:
            label, over, conflict = "ok", False, False
        mate["diagnosis"] = label
        mate["over_defined"] = over
        mate["conflict"] = conflict


def _rank_rows_at(state: Sequence[dict[str, Any]], mates: Sequence[dict[str, Any]],
                  nvars: int) -> int:
    """当前位姿下这组配合的方程行秩（用于保序修复里的"是否新增独立约束"判据）。"""
    if not mates:
        return 0
    return _rank_of(_system(state, mates, [0.0] * nvars, nvars)[1])


# ======================================================================================
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# ======================================================================================


def diagnose_over_defined(sk_or_doc: Any, *, tolerance_nm: Any = None, tolerance_deg: Any = None,
                          starts: int = 6) -> list[str]:
    """**过定义诊断**：真几何 + 真求解 + 雅可比秩亏 ⇒ 冗余 / 冲突配合 uuid 列表。

    与族模块 `diagnose_mates()` 的分工写死：那个只按 `(kind, 参考组件对)` 分组数重复，不看几何；
    这个在**位姿局部**判"方程数 > 秩"（冗余）与"这组配合同时满足不了"（冲突），并写回
    `store["mate_diagnosis"]` / `store["mate_conflict"]` / `store["mate_dof"]` + 每条配合行的
    `diagnosis`/`over_defined`/`conflict` 三字段。

    * `tolerance_nm` / `tolerance_deg`：覆盖各配合行自带的容差（`None` = 用行自带 / 缺省 1 µm、1e-6°）；
    * `starts`：多起点数（1..16），起点越多越不容易被约束奇异性骗过。

    返回：过定义（冗余 ∪ 冲突）的配合 uuid 列表，按配合插入序；真没有 ⇒ `[]`（真为零）。
    未采 / 不支持的配合**不进**这个列表，只出现在 `mate_diagnosis["unmeasured"]` 里并把
    `complete` 置 `False`（不许把"没算"伪装成"没问题"）。
    """
    result = _analyse(sk_or_doc, tol_nm=tolerance_nm, tol_deg=tolerance_deg,
                      starts=_starts_count(starts), write=True)
    return list(result["over_defined"])


def mate_conflict_set(sk_or_doc: Any, *, tolerance_nm: Any = None, tolerance_deg: Any = None,
                      starts: int = 6) -> list[str]:
    """**极小冲突集**：删掉其中任一条就能同时满足的那组配合（MUS）。

    可满足 ⇒ `[]`（真为零）。结果同时写回 `store["mate_conflict"]` 与 `mate_diagnosis["conflicting"]`。
    """
    result = _analyse(sk_or_doc, tol_nm=tolerance_nm, tol_deg=tolerance_deg,
                      starts=_starts_count(starts), write=True)
    return list(result["conflicting"])


def mate_dof_report(sk_or_doc: Any, *, tolerance_nm: Any = None, tolerance_deg: Any = None,
                    starts: int = 6) -> list[str]:
    """**未完全定义件清单**：雅可比零空间在该件 6 列上的投影秩 > 0 的自由零部件 uuid。

    全固定 / 无自由件 ⇒ `[]`；逐件自由度与自由件清单同时写回 `store["mate_dof"]`。
    """
    result = _analyse(sk_or_doc, tol_nm=tolerance_nm, tol_deg=tolerance_deg,
                      starts=_starts_count(starts), write=True)
    return list(result["under_defined"])


def resolve_over_definition(sk_or_doc: Any, *, strategy: Any = "suppress", tolerance_nm: Any = None,
                            tolerance_deg: Any = None, starts: int = 6) -> list[str]:
    """**保序修复过定义**：按配合插入序保留"既新增独立约束、又不造成冲突"的配合，其余压缩或删除。

    判据两条（都在真求解 / 真秩上跑）：

    * 加上这条后整组**不再可满足** ⇒ 这条与前序配合冲突，丢它（保住先记录的）；
    * 加上这条后**方程秩不变** ⇒ 这条是新冗余，丢它。

    修复后**复诊**：仍有冗余或冲突 ⇒ 抛 `CadError(reason="repair_failed")`（诊断账已留在盘上）。
    `strategy="suppress"`（缺省）只压缩（`suppressed=True`、`status="suppressed"`，与族模块
    `suppress_mate()` 同口径），`"delete"` 真删行。

    返回：被丢掉的配合 uuid 列表（按插入序）；没得修 ⇒ `[]`。
    """
    strategy_text = str(strategy)
    if strategy_text not in ("suppress", "delete"):
        raise _fail("invalid_argument", "strategy must be 'suppress' or 'delete'",
                    value=strategy_text)
    count = _starts_count(starts)
    store = _store(sk_or_doc, create=False)
    if store is None:
        return []
    before = _analyse(sk_or_doc, tol_nm=tolerance_nm, tol_deg=tolerance_deg, starts=count,
                      write=False)
    state = _build_state(store)
    nvars = int(before["nvars"])
    unmeasured = {item["mate"] for item in before["unmeasured"]}
    active = [mate for mate in _rows(store, "mates") if not mate.get("suppressed")]
    measurable = [mate for mate in active if str(mate.get("uuid")) not in unmeasured]
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for mate in measurable:
        trial = kept + [mate]
        if not _satisfiable(state, trial, nvars, count, tolerance_nm, tolerance_deg):
            dropped.append(mate)
            continue
        if _rank_rows_at(state, kept, nvars) >= _rank_rows_at(state, trial, nvars):
            dropped.append(mate)
            continue
        kept = trial
    applied: list[str] = []
    table = _table(store, "mates")
    for mate in dropped:
        uuid_text = str(mate.get("uuid"))
        if strategy_text == "delete":
            if mate in table:
                table.remove(mate)
        else:
            mate["suppressed"] = True
            mate["status"] = "suppressed"
        mate["resolved_by"] = "sw_assembly__w22"
        applied.append(uuid_text)
    after = _analyse(sk_or_doc, tol_nm=tolerance_nm, tol_deg=tolerance_deg, starts=count, write=True)
    verified = not after["conflicting"] and not after["redundant"]
    store["mate_resolution"] = {
        "schema": 1,
        "strategy": strategy_text,
        "suppressed": list(applied),
        "kept": [str(mate.get("uuid")) for mate in kept],
        "before": {"redundant": list(before["redundant"]),
                   "conflicting": list(before["conflicting"])},
        "after": {"redundant": list(after["redundant"]),
                  "conflicting": list(after["conflicting"]),
                  "dof_remaining": int(after["dof_remaining"])},
        "verified": bool(verified),
    }
    if not verified:
        raise _fail("repair_failed", "resolution did not clear the assembly",
                    redundant=list(after["redundant"]), conflicting=list(after["conflicting"]))
    return applied


#: 冻结注册表（族内本片提供的操作）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "diagnose_over_defined": diagnose_over_defined,
    "mate_conflict_set": mate_conflict_set,
    "resolve_over_definition": resolve_over_definition,
    "mate_dof_report": mate_dof_report,
}


# ======================================================================================
# 自检（硬断言 + 阴性对照）
# ======================================================================================


def _expect_error(reason: str, call: Callable[[], Any], label: str) -> None:
    """阴性对照：必须抛出**指定 reason** 的 `CadError`，否则自检当场炸（尺子看得见红）。"""
    _ASSERTS[0] += 1
    try:
        call()
    except CadError as exc:
        if exc.reason != reason:
            raise AssertionError("%s: reason=%r expected %r" % (label, exc.reason, reason))
        return
    raise AssertionError("%s: expected CadError(reason=%r)" % (label, reason))


def selftest() -> int:
    """自检：接口冻结 / 干净配合零误报 / 冗余定位 / 冲突极小集 / 保序修复 / 未采三态 / 自由度 / L1 往返。"""
    import cadcore.docmodel as docmodel

    from cadcore.ops.sw_assembly import (fix_component, insert_component, mate_angle, mate_check,
                               mate_distance, mate_parallel)

    checks: list[str] = []
    mm = 1_000_000

    def ok(name: str) -> None:
        checks.append(name)

    _assert(FAMILY == "sw_assembly", "FAMILY must be the frozen family id")
    _assert(set(OPERATIONS) == {"diagnose_over_defined", "mate_conflict_set",
                                "resolve_over_definition", "mate_dof_report"},
            "OPERATIONS must be exactly this seat's table")
    _assert(all(callable(function) for function in OPERATIONS.values()),
            "every OPERATIONS entry must be callable")

    def _part(document: Any, key: str, at_nm: tuple[int, int, int]):
        return insert_component(document, part_key=key, at_nm=at_nm,
                                size_nm=(10 * mm, 10 * mm, 10 * mm),
                                refs={"axis": {"point_nm": [0, 0, 0], "dir": [1.0, 0.0, 0.0]}})[0]

    # ① 空装配 + 无表文档：真为零，且读侧不凭空建表
    blank = docmodel.new_document()
    _assert(diagnose_over_defined(blank) == [], "no assembly table -> true zero over-definition")
    _assert(mate_conflict_set(blank) == [], "no assembly table -> true zero conflict")
    _assert("sw_assembly" not in blank.extra, "read-side diagnosis must not create the table")
    _assert(resolve_over_definition(blank) == [], "nothing to resolve on a doc without a table")
    ok("empty_assembly_true_zero")

    # ② 干净的单条平行配合：不过定义、秩与剩余自由度是算出来的确定数
    doc = docmodel.new_document()
    part_a = _part(doc, "plate", (0, 0, 0))
    part_b = _part(doc, "pin", (20 * mm, 0, 0))
    mate_parallel(doc, refs=[[part_a, "axis"], [part_b, "axis"]])
    _assert(mate_check(doc) == [], "family mate_check agrees the parallel mate is satisfied")
    _assert(diagnose_over_defined(doc) == [], "a single satisfied parallel mate is not over-defined")
    record = doc.extra["sw_assembly"]["mate_diagnosis"]
    _assert(record["equations"] == 2 and record["rank"] == 2, record)
    _assert(record["dof_remaining"] == 10, record)
    _assert(record["status"] == "ok" and record["complete"] is True, record)
    ok("clean_mate_rank_and_dof")

    # ③ 冗余：平行 + 同向角度（同一方向约束的第二条方程）⇒ 定位到那一条
    angle_mate = mate_angle(doc, refs=[[part_a, "axis"], [part_b, "axis"]], value_deg=0.0)[0]
    over = diagnose_over_defined(doc)
    _assert(over == [angle_mate], "only the duplicate direction constraint is redundant: %r" % (over,))
    record = doc.extra["sw_assembly"]["mate_diagnosis"]
    _assert(record["status"] == "over_defined" and record["conflicting"] == [], record)
    _assert(record["rank"] == 2 and record["equations"] == 3, record)
    _assert(doc.extra["sw_assembly"]["mates"][1]["diagnosis"] == "redundant", "row stamped redundant")
    _assert(doc.extra["sw_assembly"]["mates"][1]["over_defined"] is True, "row stamped over_defined")
    ok("redundant_mate_located")

    # ④ 冗余的保序修复（压缩）：丢后记录的那条，复诊必须干净
    dropped = resolve_over_definition(doc)
    _assert(dropped == [angle_mate], "keep-first suppresses the later redundant mate: %r" % (dropped,))
    _assert(diagnose_over_defined(doc) == [] and mate_conflict_set(doc) == [], "repair cleared it")
    _assert(doc.extra["sw_assembly"]["mates"][1]["status"] == "suppressed", "row really suppressed")
    _assert(doc.extra["sw_assembly"]["mate_resolution"]["verified"] is True, "resolution verified")
    ok("resolve_redundant_keep_first")

    # ⑤ 冲突：同一轴上 20mm 与 30mm 两条距离配合 ⇒ 极小冲突集 = 两条
    doc2 = docmodel.new_document()
    base = _part(doc2, "base", (0, 0, 0))
    slider = _part(doc2, "slider", (20 * mm, 0, 0))
    near_mate = mate_distance(doc2, refs=[[base, "axis"], [slider, "axis"]], value_nm=20 * mm)[0]
    far_mate = mate_distance(doc2, refs=[[base, "axis"], [slider, "axis"]], value_nm=30 * mm)[0]
    conflict = mate_conflict_set(doc2)
    _assert(sorted(conflict) == sorted([near_mate, far_mate]),
            "both distance mates form the minimal conflict: %r" % (conflict,))
    record = doc2.extra["sw_assembly"]["mate_diagnosis"]
    _assert(record["status"] == "conflict", record)
    _assert(record["redundant"] == [], "conflicting mates must not be double-counted as redundant")
    _assert(record["conflicting"] == conflict, record)
    _assert(doc2.extra["sw_assembly"]["mate_conflict"]["size"] == 2, "conflict record written")
    _assert(diagnose_over_defined(doc2) == conflict, "diagnosis reports exactly the conflict set")
    ok("conflict_minimal_set")

    # ⑥ 冲突的保序修复：保住先记录的 20mm，丢掉后记录的 30mm
    dropped = resolve_over_definition(doc2)
    _assert(dropped == [far_mate], "keep-first keeps the earlier distance mate: %r" % (dropped,))
    _assert(mate_conflict_set(doc2) == [] and diagnose_over_defined(doc2) == [], "conflict cleared")
    _assert(doc2.extra["sw_assembly"]["mate_resolution"]["suppressed"] == [far_mate], "record the ledger")
    ok("resolve_conflict_keep_first")

    # ⑦ 未采三态 + delete 策略：本片写不出方程的 kind（tangent）必须如实记 unsupported
    doc3 = docmodel.new_document()
    left = _part(doc3, "left", (0, 0, 0))
    right = _part(doc3, "right", (10 * mm, 0, 0))
    mate_parallel(doc3, refs=[[left, "axis"], [right, "axis"]])
    redundant_mate = mate_angle(doc3, refs=[[left, "axis"], [right, "axis"]], value_deg=0.0)[0]
    unsupported_mate = "11111111-1111-1111-1111-111111111111"
    doc3.extra["sw_assembly"]["mates"].append({
        "uuid": unsupported_mate, "kind": "tangent",
        "refs": [[left, "axis"], [right, "axis"]], "value_nm": -1, "value_deg": None, "flip": False,
        "tolerance_nm": -1, "tolerance_deg": None, "label": "NA", "status": "recorded",
        "measured_nm": -1, "measured_deg": None})
    dropped = resolve_over_definition(doc3, strategy="delete")
    _assert(dropped == [redundant_mate], "delete strategy drops the redundant mate: %r" % (dropped,))
    record = doc3.extra["sw_assembly"]["mate_diagnosis"]
    _assert(record["complete"] is False, "an unsupported mate must mark the diagnosis incomplete")
    _assert(record["unsupported"] == [unsupported_mate], record)
    _assert(record["unmeasured"] == [{"mate": unsupported_mate, "reason": "unsupported_kind"}], record)
    _assert(len(doc3.extra["sw_assembly"]["mates"]) == 2, "delete strategy really removed the row")
    ok("delete_strategy_and_unsupported_three_state")

    # ⑧ 自由度：两自由件 + 一条平行 ⇒ 12 - 2 = 10；全固定 ⇒ 0 且无未完全定义件
    doc4 = docmodel.new_document()
    fixed_a = _part(doc4, "fa", (0, 0, 0))
    fixed_b = _part(doc4, "fb", (20 * mm, 0, 0))
    mate_parallel(doc4, refs=[[fixed_a, "axis"], [fixed_b, "axis"]])
    under = mate_dof_report(doc4)
    _assert(under == [fixed_a, fixed_b], "both free parts stay under-defined: %r" % (under,))
    _assert(doc4.extra["sw_assembly"]["mate_dof"]["dof_remaining"] == 10, doc4.extra["sw_assembly"]["mate_dof"])
    fix_component(doc4, components=[fixed_a, fixed_b])
    _assert(mate_dof_report(doc4) == [], "a fully fixed assembly has no remaining dof")
    _assert(doc4.extra["sw_assembly"]["mate_dof"]["dof_remaining"] == 0, "fixed parts count zero dof")
    _assert(doc4.extra["sw_assembly"]["mate_diagnosis"]["status"] == "ok", "fixed case still diagnosable")
    ok("dof_report_and_fixed_case")

    # ⑨ L1：诊断字段不破坏不变量，且随 L1 往返存活
    _assert(docmodel.check_invariants(doc2) == [], "diagnosis fields must not break L1 invariants")
    again = docmodel.document_from_l1(docmodel.document_to_l1(doc2))
    stored = again.extra["sw_assembly"]
    _assert(stored["mate_diagnosis"]["status"] == "ok", stored["mate_diagnosis"])
    _assert(stored["mate_conflict"]["size"] == 0, stored["mate_conflict"])
    _assert(stored["mate_resolution"]["suppressed"] == [far_mate], stored["mate_resolution"])
    ok("l1_roundtrip_keeps_diagnosis")

    # ⑩ 阴性对照：非法 strategy / 非法 starts 必须抛指定 reason
    _expect_error("invalid_argument", lambda: resolve_over_definition(doc2, strategy="bogus"),
                  "bad strategy")
    _expect_error("out_of_range", lambda: diagnose_over_defined(doc2, starts=0), "bad starts")
    _assert(isinstance(diagnose_over_defined(doc2), list), "ops must return a list")
    ok("negative_controls")

    _assert(len(checks) == 10, checks)
    print("SW_ASSEMBLY_W22_SELFTEST checks=%d asserts=%d ops=%d"
          % (len(checks), _ASSERTS[0], len(OPERATIONS)))
    print("FEATURE_OK family=%s op=%s verdict=PASS" % (FAMILY, "diagnose_over_defined"))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """自检入口（失败即非零退出并打 FAIL 行，不吞异常）。"""
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败
        print("FEATURE_FAIL family=%s op=%s verdict=FAIL error=%s: %s"
              % (FAMILY, "diagnose_over_defined", type(exc).__name__, exc))
        return 1


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_assembly__w22.py:1229 "账记下来" -> "record the ledger"
