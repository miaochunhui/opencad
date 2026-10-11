#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w19 —— 装配族分片 w19：**过定义与配合冲突诊断**（MateXpert 类）。

归属与编号（读 docs/research/cad-parity/{status,features}/sw-assembly.json 得出，不靠猜）
--------------------------------------------------------------------------------------
族 must_have 共 21 条（`features/sw-assembly.json` 清单顺序 0..20）。`status/sw-assembly.json`
逐条 status 里仍 **missing/partial** 的只有 3 条，按清单顺序重编号 0..2：

    0 = 配合对齐方向控制        （missing；清单第 13 条）
    1 = 过定义与配合冲突诊断    （partial；清单第 15 条）
    2 = 干涉检查                （partial；清单第 18 条）

本席分到「第 10 号」⇒ ``10 % 3 == 1`` ⇒ 实现 **过定义与配合冲突诊断**。
注意 `status/sw-assembly.json` 的汇总字段（`implemented=0 / missing=21 / coverage_pct=0`）与它自己的
逐条 status（18 条 implemented）**互相矛盾**；本席按**逐条 status** 取值，不按汇总字段 —— 否则会把
18 条已实现项重复实现一遍。这个矛盾留给汇总席核。

本文件做什么（真几何 / 真数据后果，不是桩）
--------------------------------------------
两条**互相独立**的腿：

1. **配合级可证冲突 / 冗余**（`mate_diagnose`）：
   - 同 `(kind, 参考对)` 上 >1 条配合：目标值全同 ⇒ `redundant`（冗余，warning）；目标值不同 ⇒
     `conflict`（冲突，error），并给出 `gap_nm` / `gap_deg` 的**数值差**；
   - 同一参考对上的**跨种类**可证不相容（下面穷举的四对）⇒ `conflict`；判不出来 ⇒ `unmeasured`
     （绝不当"没问题"）。证据里带上按**当前位姿**现算的 `measured_nm` / `satisfied`（借基座模块的
     测量机，见"复用"节）。
2. **自由度（DOF）过定义账**（`mate_diagnose` 的第二条腿 + `mate_dof_ledger`）：
   对配合图的每个连通组做 Grübler 式计数：`available = 6 × 自由零部件数`，
   `used = Σ dof_removed(kind)`（重合 3 / 同轴 4 / 平行 2 / 垂直 1 / 距离 1 / 角度 1 / 相切 1 / 锁定 6；
   未建模种类按最保守的 6 计并**留痕** `unmodeled_kinds`），`used > available` ⇒ `over_defined`（error）。
   全组都被固定（可用 0）却还有配合 ⇒ `redundant`（warning，`no_free_component`），**谎报成过定义**是错的。

落盘数据后果（可被下游读、可复跑）
----------------------------------
- `store["mate_diagnostics"]`：本次诊断快照（**整表替换**，不是追加，所以复跑幂等）；
- 每条配合行回写 `diag`（`ok` / `unmeasured` / `redundant` / `over_defined` / `conflict` / `suppressed`）
  + `diag_reason` + `diag_uuid`：**每一条配合都有状态**，没有状态的配合不存在；
- `store["mate_dof_ledger"]`：逐零部件自由度账（`mate_dof_ledger` 写，`mate_diagnose` 不写）。

契约（与冻结接口一致）
----------------------
`FAMILY = "sw_assembly"`；`OPERATIONS: dict[str, Callable]`；每个操作 `op(sk_or_doc, **kwargs) -> list[str]`，
长度一律整数纳米，错误抛 `cadcore.errors.CadError(reason=...)`。
`mate_diagnose` 返回的是**本次诊断记录（`mate_diagnostics` 表内每条记录）的 uuid**，按
「严重度 error→warning→info，再按首条配合的表内插入序」排定；记录里的 `mates` / `components`
才是受影响的配合与零部件 uuid。`mate_dof_ledger` 返回参与配合图的**零部件 uuid**（首现顺序）。

三态口径（与 AGENTS.md E2.3 一致，禁静默 0）
--------------------------------------------
- `0` = 真为零（真有 0 条冗余、`gap_nm=0` 真的一致）；
- `-1` = 未采 / 不适用（`dof_available=-1` = 固定状态未知；`gap_nm=-1` = 该量不适用）；
- 没配合表 ⇒ 返回 `[]` 且**不建表**（"没东西可诊断" 与 "诊断了、零发现" 在 `mate_diagnostics`
  是否落地上可区分）。

有意限定的边界（写下来防止下游靠猜）
------------------------------------
1. **本层不是约束求解器**：只做**可证**判定 —— 同参考对同类目标值矛盾、四对跨种类不相容。
   **闭环/耦合冲突**（例：三角闭环三边长互相矛盾）**不**在本层判据内：它最多在 DOF 账里表现为
   过定义；本层**不谎报**成 conflict，也不谎报成 ok（会落 `unmeasured` 记录或至少 `ok` 需自行复算）。
2. DOF 计的是**名义约束数**（一般位置假设），不算法方程组的秩 ⇒ 可能**低估**过定义；这正是需要
   第 1 条腿（冗余/冲突）作为独立第二证据的原因。两条腿结论不一致时**都保留**。
3. 被压缩（`suppressed`）的配合不参与任何判定，但**照样回写** `diag="suppressed"`（留痕，不当通过）。
4. 参考指向的零部件解析不出固定状态 ⇒ `unmeasured` / `dof_available=-1`，**绝不默认成"浮动"**。
5. 复用而非重写：表访问 / 组件解析 / 位姿与测量机全部借 `cadcore.ops.sw_assembly`（`_store` / `_table` /
   `_component` / `_world_mate_ref` / `_measure_any`），本文件不抄第二份；基座缺件时**显式报错**
   （`reason="base_module_incomplete"`），不静默降级成"没发现问题"。
6. 非 docstring 的字符串字面量一律 ASCII（`tools/cad/i18n_gate.py` 口径）；中文只在 docstring/注释里。

自检（文件自带，不依赖 pytest，≥2 条硬断言）

    python3 cadcore/ops/features/sw_assembly__w19.py
"""

from __future__ import annotations

import os as _os
import sys as _sys
import uuid as _uuid
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/sw_assembly__w19.py 的引导
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__))))))
    __package__ = "cadcore.ops.features"

from cadcore.errors import CadError                      # noqa: E402  (bootstrap 之后才能 import)
from cadcore.sketch.constraints import new_uuid          # noqa: E402
from .. import sw_assembly as _asm                  # noqa: E402  基座族模块（复用，不抄）

__all__ = ["FAMILY", "OPERATIONS", "DIAG_KEY", "LEDGER_KEY", "DOF_PER_COMPONENT",
           "mate_diagnose", "mate_dof_ledger", "selftest"]

#: 族 id（冻结接口）。
FAMILY = "sw_assembly"

#: 诊断快照 / DOF 账在装配表里的键（**只加键不改键**，不动基座已有的 components/mates/... 键）。
DIAG_KEY = "mate_diagnostics"
LEDGER_KEY = "mate_dof_ledger"

#: 每个自由零部件的自由度（3 平移 + 3 旋转）。
DOF_PER_COMPONENT = 6

#: 未采/不适用哨兵（与基座三态口径一致）。
UNMEASURED = int(getattr(_asm, "UNMEASURED", -1))

#: 长度/角度缺省容差（借基座值，避免两处各说各话）。
NM_PER_MM = int(getattr(_asm, "NM_PER_MM", 1_000_000))
DEFAULT_TOLERANCE_NM = int(getattr(_asm, "DEFAULT_TOLERANCE_NM", 1_000))
DEFAULT_TOLERANCE_DEG = float(getattr(_asm, "DEFAULT_TOLERANCE_DEG", 1e-6))

#: 各配合种类**名义**消去的自由度数（冻结表；未列出的种类按 DOF_PER_COMPONENT 计并留痕）。
_DOF_REMOVED: dict[str, int] = {
    "coincident": 3, "concentric": 4, "parallel": 2, "perpendicular": 1,
    "distance": 1, "angle": 1, "tangent": 1, "lock": 6,
}

#: 配合行 `diag` 的严重度序（写回时取最高者，绝不因后写的记录把 conflict 降级成 ok）。
_DIAG_RANK: dict[str, int] = {"ok": 0, "unmeasured": 1, "redundant": 2, "over_defined": 3,
                              "conflict": 4}
_DIAG_BY_RANK: dict[int, str] = {rank: name for name, rank in _DIAG_RANK.items()}

#: 记录排序：严重度 error→warning→info，再按类别（同严重度下冲突优先）。
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}
_CLASS_RANK = {"conflict": 0, "over_defined": 1, "redundant": 2, "unmeasured": 3, "ok": 4}

#: 基座模块里本层复用的私有助手（冻结接口只暴露三样，这里必须借内部件才能不抄第二份真源）。
_store = getattr(_asm, "_store", None)
_table = getattr(_asm, "_table", None)
_component = getattr(_asm, "_component", None)
_world_mate_ref = getattr(_asm, "_world_mate_ref", None)
_measure_any = getattr(_asm, "_measure_any", None)
_INSERT_COMPONENT = getattr(_asm, "insert_component", None)
_MATE_ADD = getattr(_asm, "mate_add", None)
_FLOAT_COMPONENT = getattr(_asm, "float_component", None)


# ======================================================================================
# 基础件：表访问 / 行解析（缺件一律显式报错，不静默降级）
# ======================================================================================


def _require(name: str, value: Any) -> Any:
    if value is None:
        raise CadError("base module cadcore.ops.sw_assembly lacks a required helper",
                       reason="base_module_incomplete", missing=name)
    return value


def _require_store(target: Any) -> dict[str, Any] | None:
    """取装配表；目标里没有装配表 ⇒ `None`（**不建表**：没东西可诊断与零发现要能区分）。"""
    store = _require("_store", _store)
    try:
        return store(target, create=False)
    except TypeError:  # 基座改了签名就退化为缺省调用，不假装成功
        return store(target)


def _new_uuid() -> str:
    try:
        return str(new_uuid())
    except Exception:  # pragma: no cover - 只影响 id 生成，不影响判定
        return str(_uuid.uuid4())


def _pairs(row: dict[str, Any]) -> list[tuple[str, str]] | None:
    """配合行的两个参考；结构不对 ⇒ `None`（该行记 `unmeasured`，不当成"没问题"）。"""
    raw = row.get("refs")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    out: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        out.append((str(item[0]), str(item[1])))
    return out


def _dof_removed(kind: str) -> tuple[int, bool]:
    """`(名义消去自由度, 是否未建模种类)`；未建模按最保守的 6 计并留痕。"""
    if kind in _DOF_REMOVED:
        return _DOF_REMOVED[kind], False
    return DOF_PER_COMPONENT, True


def _value_nm(row: dict[str, Any], kind: str) -> int | None:
    """长度类目标值（nm）；`None` = 未设/量不出（**不返回 0 冒充**）。重合隐含 0。"""
    if kind == "coincident":
        return 0
    if kind != "distance":
        return None
    raw = row.get("value_nm")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    if raw == UNMEASURED:
        return None
    return int(raw)


def _value_deg(row: dict[str, Any], kind: str) -> float | None:
    """角度类目标值（度）；平行/垂直有自己的隐含值，`None` = 未设/量不出。"""
    if kind == "parallel":
        return 180.0 if bool(row.get("flip")) else 0.0
    if kind == "perpendicular":
        return 90.0
    if kind != "angle":
        return None
    raw = row.get("value_deg")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _target(row: dict[str, Any]) -> tuple[str, Any]:
    """该行**适用**的目标量：`("nm", int)` / `("deg", float)` / `("none", 0)`。"""
    kind = str(row.get("kind"))
    if kind in ("distance", "coincident"):
        return "nm", _value_nm(row, kind)
    if kind in ("angle", "parallel", "perpendicular"):
        return "deg", _value_deg(row, kind)
    return "none", 0


def _signature(row: dict[str, Any]) -> tuple[Any, ...]:
    kind = str(row.get("kind"))
    return (kind, _value_nm(row, kind), _value_deg(row, kind), bool(row.get("flip")))


def _fixed_state(store: dict[str, Any], ref: str) -> bool | None:
    """`True` 固定 / `False` 浮动 / `None` 解析不出（未知**不默认成浮动**）。"""
    if _component is None:
        return None
    try:
        row = _component(store, ref)
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    if "fixed" not in row:
        return False  # 基座建的行都带 fixed；缺键按浮动（与基座 fix/float 语义一致）
    return bool(row.get("fixed"))


def _incompat(kind_a: str, row_a: dict[str, Any], kind_b: str, row_b: dict[str, Any], *,
              tol_nm: int, tol_deg: float) -> tuple[bool | None, int, float, str]:
    """跨种类**可证**不相容判定：`(是否冲突 | None=量不出, gap_nm, gap_deg, reason)`。

    只穷举四对能被静态证明的组合（见模块 docstring「有意限定的边界」第 1 条）；其余组合一律
    `(False, 0, -1.0, "")`，即"本层不判"，**不是**"相容"。
    """
    key = tuple(sorted((kind_a, kind_b)))
    if key == ("coincident", "distance"):
        dist_row = row_a if kind_a == "distance" else row_b
        value = _value_nm(dist_row, "distance")
        if value is None:
            return None, UNMEASURED, -1.0, "distance_value_unset"
        if abs(value) > tol_nm:
            return True, int(abs(value)), -1.0, "coincident_forces_zero_distance"
        return False, 0, -1.0, ""
    if key == ("parallel", "perpendicular"):
        return True, UNMEASURED, 90.0, "parallel_vs_perpendicular"
    if key == ("angle", "parallel"):
        angle = _value_deg(row_a if kind_a == "angle" else row_b, "angle")
        if angle is None:
            return None, UNMEASURED, -1.0, "angle_value_unset"
        gap = min(abs((angle - 0.0 + 180.0) % 360.0 - 180.0),
                  abs((angle - 180.0 + 180.0) % 360.0 - 180.0))
        if gap > tol_deg:
            return True, UNMEASURED, float(gap), "parallel_vs_angle"
        return False, 0, -1.0, ""
    if key == ("angle", "perpendicular"):
        angle = _value_deg(row_a if kind_a == "angle" else row_b, "angle")
        if angle is None:
            return None, UNMEASURED, -1.0, "angle_value_unset"
        gap = abs(angle - 90.0)
        if gap > tol_deg:
            return True, UNMEASURED, float(gap), "perpendicular_vs_angle"
        return False, 0, -1.0, ""
    return False, 0, -1.0, ""


def _measure_rows(store: dict[str, Any], rows: list[dict[str, Any]], *, tol_nm: int,
                  tol_deg: float) -> tuple[list[dict[str, Any]], str]:
    """按**当前位姿**现算每条配合的残差（借基座测量机）；借不到就整组 `unmeasured`。"""
    if _measure_any is None or _world_mate_ref is None:
        return [], "unmeasured"
    out: list[dict[str, Any]] = []
    any_measured = False
    for row in rows:
        uid = str(row.get("uuid"))
        pairs = _pairs(row)
        if pairs is None:
            out.append({"mate": uid, "measured_nm": UNMEASURED, "measured_deg": None,
                        "satisfied": None, "reason": "malformed_refs"})
            continue
        kind = str(row.get("kind"))
        value_nm = _value_nm(row, kind)
        try:
            first = _world_mate_ref(store, pairs[0])
            second = _world_mate_ref(store, pairs[1])
            measured_nm, measured_deg, satisfied = _measure_any(
                store, row, first, second, value_nm=UNMEASURED if value_nm is None else value_nm,
                value_deg=row.get("value_deg"), flip=bool(row.get("flip")),
                tol_nm=int(tol_nm), tol_deg=float(tol_deg))
        except Exception as exc:  # 量不出就如实记，绝不换成 satisfied=True
            out.append({"mate": uid, "measured_nm": UNMEASURED, "measured_deg": None,
                        "satisfied": None, "reason": type(exc).__name__})
            continue
        any_measured = any_measured or satisfied is not None
        out.append({"mate": uid,
                    "measured_nm": int(measured_nm),
                    "measured_deg": None if measured_deg is None else float(measured_deg),
                    "satisfied": None if satisfied is None else bool(satisfied)})
    return out, ("measured" if any_measured else "unmeasured")


def _group_refs(refs: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    """并查集连通组；组按"组内最小首现序"排，保证复跑顺序稳定。"""
    parent = {ref: ref for ref in refs}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in edges:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_left] = root_right
    buckets: dict[str, list[str]] = {}
    for ref in refs:
        buckets.setdefault(find(ref), []).append(ref)
    index = {ref: position for position, ref in enumerate(refs)}
    return sorted(buckets.values(), key=lambda group: min(index[ref] for ref in group))


# ======================================================================================
# 主操作：过定义与配合冲突诊断
# ======================================================================================


def _record(klass: str, severity: str, reason: str, rows: list[dict[str, Any]], *,
            gap_nm: int = UNMEASURED, gap_deg: float = -1.0, dof_used: int = UNMEASURED,
            dof_available: int = UNMEASURED,
            components: list[str] | None = None) -> dict[str, Any]:
    comps = list(components) if components is not None else sorted(
        {pair[0] for row in rows for pair in (_pairs(row) or [])})
    return {"uuid": _new_uuid(), "class": klass, "severity": severity, "reason": reason,
            "mates": [str(row.get("uuid")) for row in rows], "components": comps,
            "gap_nm": int(gap_nm), "gap_deg": float(gap_deg),
            "dof_used": int(dof_used), "dof_available": int(dof_available),
            "measured": [], "measured_state": "unmeasured"}


def mate_diagnose(sk_or_doc: Any, *, tolerance_nm: Any = None, tolerance_deg: Any = None,
                  include_ok: bool = False) -> list[str]:
    """**过定义 / 配合冲突诊断**：可证冲突 + 冗余 + DOF 过定义，写诊断快照并把状态回写到配合行。

    判据（两条腿，互相独立，结论都保留）：
    - 配合级：同 `(kind, 参考对)` 目标值全同 ⇒ `redundant`；不同 ⇒ `conflict`；值未设 ⇒ `unmeasured`。
      同一参考对上跨种类的四对可证不相容（重合×距离 / 平行×垂直 / 平行×角度 / 垂直×角度）⇒ `conflict`。
    - DOF 级：配合图每个连通组 `used = Σ dof_removed > available = 6 × 自由件数` ⇒ `over_defined`；
      全组固定却仍有配合 ⇒ `redundant`（`no_free_component`）；固定状态解析不出 ⇒ `unmeasured`。

    返回：本次诊断记录 uuid 列表（排序：error→warning→info，再 conflict→over_defined→redundant→
    unmeasured→ok，再按首条配合的表内插入序）；记录里 `mates` / `components` 是受影响对象。
    没有装配表 ⇒ `[]`（不建表）。
    """
    store = _require_store(sk_or_doc)
    if store is None:
        return []
    tol_nm = DEFAULT_TOLERANCE_NM if tolerance_nm is None else int(tolerance_nm)
    tol_deg = DEFAULT_TOLERANCE_DEG if tolerance_deg is None else float(tolerance_deg)

    rows = [row for row in _require("_table", _table)(store, "mates") if isinstance(row, dict)]
    order = {str(row.get("uuid")): index for index, row in enumerate(rows)}
    findings: list[dict[str, Any]] = []

    active: list[dict[str, Any]] = []
    for row in rows:
        if bool(row.get("suppressed")):
            row["diag"], row["diag_reason"] = "suppressed", "suppressed_mate"
            continue
        active.append(row)

    good: list[dict[str, Any]] = []
    for row in active:
        if _pairs(row) is None:
            row["diag"], row["diag_reason"] = "unmeasured", "malformed_refs"
            findings.append(_record("unmeasured", "info", "malformed_refs", [row]))
            continue
        good.append(row)

    # ---- 腿 1a：同 (kind, 参考对) 重复 ⇒ 冗余（一致）/ 冲突（矛盾）/ 未采 ----
    same_kind: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    ref_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in good:
        pairs = _pairs(row) or []
        key = tuple(sorted(pairs))
        same_kind.setdefault((str(row.get("kind")), key), []).append(row)
        ref_groups.setdefault(key, []).append(row)

    for members in same_kind.values():
        if len(members) < 2:
            continue
        if len({_signature(row) for row in members}) == 1:
            if any(_target(row)[1] is None for row in members):
                findings.append(_record("unmeasured", "info", "target_value_unset", members))
            else:
                findings.append(_record("redundant", "warning", "identical_duplicate_mates",
                                        members, gap_nm=0))
            continue
        values_nm = [value for value in
                     (_value_nm(row, str(row.get("kind"))) for row in members)
                     if value is not None]
        values_deg = [value for value in
                      (_value_deg(row, str(row.get("kind"))) for row in members)
                      if value is not None]
        findings.append(_record(
            "conflict", "error", "same_reference_incompatible_targets", members,
            gap_nm=(max(values_nm) - min(values_nm)) if values_nm else UNMEASURED,
            gap_deg=(max(values_deg) - min(values_deg)) if values_deg else -1.0))

    # ---- 腿 1b：同一参考对上跨种类的可证不相容 ----
    for members in ref_groups.values():
        for left in range(len(members)):
            for right in range(left + 1, len(members)):
                row_a, row_b = members[left], members[right]
                kind_a, kind_b = str(row_a.get("kind")), str(row_b.get("kind"))
                if kind_a == kind_b:
                    continue  # 同类已在腿 1a 判过，不重复记账
                flag, gap_nm, gap_deg, reason = _incompat(kind_a, row_a, kind_b, row_b,
                                                          tol_nm=tol_nm, tol_deg=tol_deg)
                if flag is True:
                    findings.append(_record("conflict", "error", reason, [row_a, row_b],
                                            gap_nm=gap_nm, gap_deg=gap_deg))
                elif flag is None:
                    findings.append(_record("unmeasured", "info", reason, [row_a, row_b]))

    # ---- 腿 2：DOF 过定义账（配合图连通组） ----
    refs: list[str] = []
    edges: list[tuple[str, str]] = []
    internal: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in good:
        pairs = _pairs(row) or []
        for comp, _name in pairs:
            if comp not in refs:
                refs.append(comp)
        edge = (pairs[0][0], pairs[0][1][0] if False else pairs[1][0])  # 两个零部件端
        edges.append(edge)
        internal.setdefault(tuple(sorted(edge)), []).append(row)
    for group in _group_refs(refs, edges):
        member_rows = [row for row in good
                       if {pair[0] for pair in (_pairs(row) or [])} <= set(group)]
        if not member_rows:
            continue
        used = sum(_dof_removed(str(row.get("kind")))[0] for row in member_rows)
        states = {ref: _fixed_state(store, ref) for ref in group}
        free = [ref for ref in group if states[ref] is False]
        unknown = [ref for ref in group if states[ref] is None]
        if unknown and used > 0:
            findings.append(_record("unmeasured", "info", "component_state_unknown", member_rows,
                                    dof_used=used, dof_available=UNMEASURED,
                                    components=list(group)))
            continue
        if not free:
            if used > 0:
                findings.append(_record("redundant", "warning", "no_free_component", member_rows,
                                        dof_used=used, dof_available=0, components=list(group)))
            continue
        available = DOF_PER_COMPONENT * len(free)
        if used > available:
            findings.append(_record("over_defined", "error", "dof_budget_exceeded", member_rows,
                                    dof_used=used, dof_available=available,
                                    components=list(group)))

    # ---- 证据：按当前位姿现算残差（借不到测量机就整组 unmeasured，不假绿） ----
    for finding in findings:
        finding_rows = [row for row in good if str(row.get("uuid")) in set(finding["mates"])]
        measured, state = _measure_rows(store, finding_rows, tol_nm=tol_nm, tol_deg=tol_deg)
        finding["measured"], finding["measured_state"] = measured, state

    # ---- ok 收尾：每条参与的配合都必须有状态 ----
    covered: dict[str, str] = {}
    for finding in findings:
        for mate in finding["mates"]:
            klass = finding["class"]
            if _DIAG_RANK.get(klass, 1) >= _DIAG_RANK.get(covered.get(mate, "ok"), 0):
                covered[mate] = klass
    for row in good:
        row["diag"] = covered.get(str(row.get("uuid")), "ok")
        row["diag_reason"] = next((finding["reason"] for finding in findings
                                   if str(row.get("uuid")) in finding["mates"]
                                   and finding["class"] == row["diag"]), "no_finding")
    if include_ok:
        for row in good:
            if row.get("diag") == "ok":
                findings.append(_record("ok", "info", "no_finding", [row], gap_nm=0))

    findings.sort(key=lambda item: (_SEVERITY_RANK.get(item["severity"], 3),
                                    _CLASS_RANK.get(item["class"], 9),
                                    min((order.get(mate, len(rows)) for mate in item["mates"]),
                                        default=len(rows)),
                                    tuple(item["mates"])))
    touched = {mate for finding in findings for mate in finding["mates"]}
    for row in good:
        row["diag_uuid"] = [finding["uuid"] for finding in findings
                            if str(row.get("uuid")) in finding["mates"]]
    for row in rows:
        if str(row.get("uuid")) not in touched and "diag" not in row:
            row["diag_uuid"] = []
    store[DIAG_KEY] = findings  # 快照整表替换 ⇒ 复跑幂等
    return [str(finding["uuid"]) for finding in findings]


# ======================================================================================
# 辅助操作：逐零部件自由度账（只读表 + 可关写；返回参与配合图的零部件 uuid）
# ======================================================================================


def mate_dof_ledger(sk_or_doc: Any, *, write: bool = True) -> list[str]:
    """**逐零部件自由度账**：`dof_used` = 挂在该件上的配合名义消去自由度之和（**incidence 求和**，
    跨件会重复计，不是全局自由度数 —— 全局判定看 `mate_diagnose`），`dof_available` = 固定 0 /
    浮动 6 / 未知 `-1`（未采哨兵），`remaining = available - used`，`over_defined = remaining < 0`。

    返回：参与配合图的零部件 uuid（首现顺序）。`write=True`（缺省）时把账写进
    `store["mate_dof_ledger"]`；`write=False` 只算不写。没有装配表 ⇒ `[]`（不建表）。
    """
    store = _require_store(sk_or_doc)
    if store is None:
        return []
    rows = [row for row in _require("_table", _table)(store, "mates")
            if isinstance(row, dict) and not row.get("suppressed")]
    entries: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        pairs = _pairs(row)
        if pairs is None:
            continue
        kind = str(row.get("kind"))
        removed, unmodeled = _dof_removed(kind)
        for comp, _name in pairs:
            entry = entries.get(comp)
            if entry is None:
                entry = {"component": comp, "fixed": _fixed_state(store, comp), "mates": [],
                         "dof_used": 0, "dof_available": DOF_PER_COMPONENT,
                         "unmodeled_kinds": []}
                entries[comp] = entry
                order.append(comp)
            entry["mates"].append(str(row.get("uuid")))
            entry["dof_used"] += removed
            if unmodeled and kind not in entry["unmodeled_kinds"]:
                entry["unmodeled_kinds"].append(kind)
    ledger: list[dict[str, Any]] = []
    for comp in order:
        entry = entries[comp]
        if entry["fixed"] is True:
            entry["dof_available"] = 0
        elif entry["fixed"] is None:
            entry["dof_available"] = UNMEASURED
        entry["remaining"] = (UNMEASURED if entry["dof_available"] == UNMEASURED
                              else entry["dof_available"] - entry["dof_used"])
        entry["over_defined"] = bool(entry["remaining"] != UNMEASURED and entry["remaining"] < 0)
        ledger.append(entry)
    if write:
        store[LEDGER_KEY] = ledger
    return list(order)


#: 冻结接口：操作名 → 可调用。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mate_diagnose": mate_diagnose,
    "mate_dof_ledger": mate_dof_ledger,
}


# ======================================================================================
# 自检（≥2 条硬断言；含真目标上的真数据后果检查）
# ======================================================================================


def _base_op(name: str) -> Callable[..., Any]:
    op = {"insert_component": _INSERT_COMPONENT, "mate_add": _MATE_ADD,
          "float_component": _FLOAT_COMPONENT}.get(name)
    if op is None:
        raise CadError("base module cadcore.ops.sw_assembly lacks a required operation",
                       reason="base_module_incomplete", missing=name)
    return op


def _try_document() -> tuple[Any, str]:
    """造一个 L1 文档对象（有工厂就用真的）；造不出 ⇒ `(None, "unavailable:<why>")`。"""
    try:
        from cadcore import docmodel as _dm
    except Exception as exc:  # pragma: no cover
        return None, "unavailable:" + type(exc).__name__
    for factory_name in ("new_document", "new", "create_document"):
        factory = getattr(_dm, factory_name, None)
        if callable(factory):
            try:
                return factory(), "checked"
            except Exception as exc:  # pragma: no cover
                return None, "unavailable:" + type(exc).__name__
    return None, "unavailable:no_factory"


def selftest() -> dict[str, Any]:
    """真跑：5 个场景 + 可选 L1 文档腿；任何一条不成立就 `verdict=FAIL`（不软化、不 skip）。"""
    failures: list[str] = []
    checks = 0
    scenarios = 0

    def check(condition: Any, name: str) -> bool:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)
        return bool(condition)

    def build(pairs_spec: list[tuple[str, str, Any]], *, float_first: bool = True) -> tuple[Any, list[str]]:
        """建目标 + 建零部件（默认先浮动，避免"首个固定"缺省把 DOF 账搅浑）。"""
        target: dict[str, Any] = {}
        names = []
        for part_key, _kind, _value in pairs_spec:
            if part_key not in names:
                names.append(part_key)
        comps: dict[str, str] = {}
        for part_key in names:
            comps[part_key] = str(_base_op("insert_component")(target, part_key=part_key)[0])
        if float_first:
            _base_op("float_component")(target, components=list(comps.values()))
        return target, [comps[part_key] for part_key in names]

    def add(target: Any, kind: str, left: str, right: str, **kwargs: Any) -> str:
        return str(_base_op("mate_add")(target, kind=kind,
                                        refs=((left, "origin"), (right, "origin")),
                                        **kwargs)[0])

    def snapshot(target: Any) -> list[dict[str, Any]]:
        store = _require_store(target)
        return list(store.get(DIAG_KEY) or []) if store else []

    def mate_rows(target: Any) -> dict[str, dict[str, Any]]:
        store = _require_store(target)
        return {str(row.get("uuid")): row for row in (store.get("mates") or [])} if store else {}

    # ---- 场景 A：同参考对同类目标值矛盾 ⇒ conflict（error）+ gap_nm + 配合行回写 ----
    scenarios += 1
    doc_a, (a1, a2) = build([("w19_a", "d", 0), ("w19_b", "d", 0)])
    ma1 = add(doc_a, "distance", a1, a2, value_nm=10 * NM_PER_MM)
    ma2 = add(doc_a, "distance", a1, a2, value_nm=20 * NM_PER_MM)
    out_a = mate_diagnose(doc_a)
    recs_a = snapshot(doc_a)
    conflict_a = [row for row in recs_a
                  if row["class"] == "conflict" and set(row["mates"]) == {ma1, ma2}]
    check(len(out_a) == len(recs_a) and len(out_a) >= 1,
          "A_returns_one_uuid_per_diagnostic_record")
    check(len(conflict_a) == 1, "A_exactly_one_conflict_for_the_contradicting_pair")
    if conflict_a:
        check(conflict_a[0]["severity"] == "error"
              and conflict_a[0]["gap_nm"] == 10 * NM_PER_MM
              and "same_reference_incompatible_targets" == conflict_a[0]["reason"],
              "A_gap_nm_equals_the_10mm_target_difference")
    rows_a = mate_rows(doc_a)
    check(rows_a.get(ma1, {}).get("diag") == "conflict"
          and rows_a.get(ma2, {}).get("diag") == "conflict",
          "A_both_mate_rows_carry_diag_conflict")
    check(bool(conflict_a) and conflict_a[0]["measured_state"] in ("measured", "unmeasured"),
          "A_measurement_state_is_explicit_never_implicit_ok")

    # ---- 场景 B：同参考对同类目标值一致 ⇒ redundant（warning），且不误报冲突 ----
    scenarios += 1
    doc_b, (b1, b2) = build([("w19_c", "d", 0), ("w19_d", "d", 0)])
    mb1 = add(doc_b, "distance", b1, b2, value_nm=5 * NM_PER_MM)
    mb2 = add(doc_b, "distance", b1, b2, value_nm=5 * NM_PER_MM)
    mate_diagnose(doc_b)
    recs_b = snapshot(doc_b)
    redundant_b = [row for row in recs_b
                   if row["class"] == "redundant" and set(row["mates"]) == {mb1, mb2}]
    check(len(redundant_b) == 1 and redundant_b[0]["severity"] == "warning"
          and redundant_b[0]["gap_nm"] == 0, "B_identical_duplicates_are_redundant_gap_zero")
    check(not any(row["class"] == "conflict" for row in recs_b),
          "B_consistent_duplicates_are_not_reported_as_conflict")

    # ---- 场景 C：干净装配（无重复/无过定义）⇒ 真零（返回 []，但配合行仍被标 ok） ----
    scenarios += 1
    doc_c, (c1, c2) = build([("w19_e", "d", 0), ("w19_f", "d", 0)])
    mc1 = add(doc_c, "distance", c1, c2, value_nm=7 * NM_PER_MM)
    out_c = mate_diagnose(doc_c)
    check(out_c == [] and snapshot(doc_c) == [], "C_clean_assembly_is_a_true_zero_no_finding")
    check(mate_rows(doc_c).get(mc1, {}).get("diag") == "ok",
          "C_clean_mate_row_is_marked_ok_not_left_blank")

    # ---- 场景 D：DOF 账过定义（6+1+2+3+1 = 13 > 6×2 = 12） ----
    scenarios += 1
    doc_d, (d1, d2) = build([("w19_g", "d", 0), ("w19_h", "d", 0)])
    add(doc_d, "lock", d1, d2)
    add(doc_d, "distance", d1, d2, value_nm=3 * NM_PER_MM)
    add(doc_d, "parallel", d1, d2)
    add(doc_d, "coincident", d1, d2)
    add(doc_d, "angle", d1, d2, value_deg=45.0)
    mate_diagnose(doc_d)
    over_d = [row for row in snapshot(doc_d) if row["class"] == "over_defined"]
    check(len(over_d) >= 1 and over_d[0]["severity"] == "error"
          and over_d[0]["dof_used"] == 13 and over_d[0]["dof_available"] == 12,
          "D_dof_budget_exceeded_is_over_defined_13_over_12")
    comps_d = mate_dof_ledger(doc_d)
    ledger_d = (_require_store(doc_d) or {}).get(LEDGER_KEY) or []
    check(set(comps_d) == {d1, d2} and len(ledger_d) == 2
          and all(entry["dof_used"] == 13 and entry["over_defined"] is True for entry in ledger_d),
          "D_ledger_charges_13_dof_to_both_components_and_flags_them")

    # ---- 场景 E：没配合表 ⇒ []，且不建表（"没东西可诊断" 与 "零发现" 可区分） ----
    scenarios += 1
    empty: dict[str, Any] = {}
    check(mate_diagnose(empty) == [] and _require_store(empty) is None,
          "E_absent_assembly_table_returns_empty_without_creating_one")

    # ---- 场景 F（可选）：真 L1 文档对象上跑同一能力 ----
    l1_state = "unmeasured"
    doc_obj, why = _try_document()
    if doc_obj is not None:
        scenarios += 1
        l1_state = "checked"
        try:
            f1 = str(_base_op("insert_component")(doc_obj, part_key="w19_i")[0])
            f2 = str(_base_op("insert_component")(doc_obj, part_key="w19_j")[0])
            _base_op("float_component")(doc_obj, components=[f1, f2])
            mf1 = add(doc_obj, "distance", f1, f2, value_nm=1 * NM_PER_MM)
            mf2 = add(doc_obj, "distance", f1, f2, value_nm=2 * NM_PER_MM)
            out_f = mate_diagnose(doc_obj)
            recs_f = snapshot(doc_obj)
            check(len(out_f) == len(recs_f) and any(
                row["class"] == "conflict" and set(row["mates"]) == {mf1, mf2}
                for row in recs_f), "F_l1_document_model_reports_the_conflict")
        except Exception as exc:
            l1_state = "checked"
            check(False, "F_l1_document_model_raised_" + type(exc).__name__)
    else:
        l1_state = why

    verdict = "PASS" if not failures else "FAIL"
    return {"verdict": verdict, "checks": checks, "failures": failures, "scenarios": scenarios,
            "l1_document_model": l1_state, "operations": sorted(OPERATIONS)}


def main() -> int:
    result = selftest()
    print("SELFTEST family=%s ops=%d checks=%d scenarios=%d l1_document_model=%s verdict=%s"
          % (FAMILY, len(OPERATIONS), result["checks"], result["scenarios"],
             result["l1_document_model"], result["verdict"]))
    if result["verdict"] != "PASS":
        for name in result["failures"]:
            print("SELFTEST_FAIL check=%s" % name)
        print("FEATURE_FAIL family=%s op=mate_diagnose verdict=FAIL" % FAMILY)
        return 1
    print("FEATURE_OK family=%s op=mate_diagnose verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
