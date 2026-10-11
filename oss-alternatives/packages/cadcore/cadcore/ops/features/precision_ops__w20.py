#!/usr/bin/env python3
"""cadcore.ops.features.precision_ops__w20 —— 精度族 w20 片：正交模式（ORTHO / F8）的**文档级几何后果**。

一句话用途
----------
把"正交模式"从**光标的点吸附**（`cadcore.precision.osnap.ortho_lock`，容差内才锁）升级为**L1 文档上的
真几何约束**：无条件把点投影到过基点的最近水平/垂直轴，并让这个约束**真的落到文档实体上** ——
新建正交段、把已有斜线掰正、把整张草图里所有斜线一次掰正。这是 `cadcore.ops.features.<family>__w<k>`
家族模块之一（`FAMILY` / `OPERATIONS` / `op(sk_or_doc, **kwargs) -> list[str]` 为冻结接口）。

本片对应清单条目（读 features/autocad-precision.json 的 must_have 顺序；status 里仍 missing/partial 的
条目按同一顺序编号 0..N-1）
----------------------------------------------------------------------------------------------
第 **11** 号 = 「正交模式」（`must_have` 第 12 项，`status.items` 记 `partial`）。前 11 条
（对象捕捉总开关 / 端点 / 中点 / 圆心与象限点 / 交点与外观交点 / 垂足切点最近点 / 延长线与平行 /
插入点与节点 / 单次临时捕捉 / 捕捉优先级 / AutoSnap 反馈）按清单顺序编号 0..10，故取第 11 号。

与既有实现的关系（为什么这不是重复劳动）
------------------------------------
- `osnap.ortho_lock(base_nm, point_nm, *, tolerance_deg=3.0)`：**容差内**才锁，超出容差**原样返回**
  （口径是"吸附"）。而 AutoCAD 的 ORTHO（F8）是**开关式硬约束**：只要开着，方向**永远**被锁到最近轴，
  与角度大小无关。本片补的正是这个"无条件"语义（见 `ortho_project`），自检里有一条断言专门对拍两者差别。
- `ops.precision_ops.ortho_set` / `precision_apply` 第 3 步：只把**光标读数**锁轴，**不改任何实体**。
  本片补的是"锁轴之后**实体真的变成正交**"这一段，所以后果可以在 L1 载荷里读回来（不是桩、不是只写读数）。

操作（`OPERATIONS`；长度一律整数纳米，错误一律 `CadError(reason=...)`）
--------------------------------------------------------------------
- ``ortho_segment_add(sk_or_doc, *, base_nm, point_nm, sketch=None, layer="0", construction=False)``
  → 在目标草图 `entities` 里**新建**一条线实体，其终点是正交投影后的点；返回 ``[新实体 uuid]``。
- ``ortho_snap_segment(sk_or_doc, *, entity_uuid, sketch=None, base="start")``
  → 把一条**已有**线实体的活动端点掰到过基点端点的最近轴上（真改几何）；返回 ``[该实体 uuid]``。
- ``ortho_constrain_sketch(sk_or_doc, *, sketch=None)``
  → 把该草图里**所有斜线**一次掰正；返回**被改动的**实体 uuid（本来就正交/零长的**不进列表**，
  口径是"0 = 真没改"，不是"没算"）。
- ``ortho_state_set(sk_or_doc, *, enabled)``
  → ORTHO 开关的持久化：**不另立一套状态**，直接委托既有 `ops.precision_ops.ortho_set`（单一真源），
  返回它给的记录 uuid。

口径（与全仓一致，别自己发明）
----------------------------
- 投影 = **只动一个坐标**（`锁轴不缩距`，与 `ortho_lock` 同口径）：水平锁 ⇒ `y := base.y`；垂直锁 ⇒ `x := base.x`。
- 平手（恰好 45°）⇒ **取水平**（与 `ortho_lock` 的 `horizontal <= vertical` 同口径）。
- 基点上、下点**重合** ⇒ 方向未定义 ⇒ `CadError(reason="degenerate_ortho")`，**不猜**一个方向。
- 点已在轴上 ⇒ 投影是恒等变换，**照样算"约束成立"**（会建实体 / 会被 `ortho_snap_segment` 认下），
  但 `ortho_constrain_sketch` **不把它计入返回值**（零改动就是零改动）。

本片**不覆盖**（unsupported at L1 / 不在本文件写面内，如实留 missing）
------------------------------------------------------------------
1. **状态栏 ORTHO 按钮与 F8 快捷键**：属于 `apps/opencad`（`server.py` 白名单 / `web/js/app.js` 键位），
   不是 `cadcore` 文档模型能装的，本文件一行都不改它。
2. **取点时的实时正交预览（橡皮筋）**：需要 UI 事件循环；L1 只有"落盘后的文档"，没有光标流。
3. **AutoSnap 正交标记/工具提示的视觉反馈**：同上，属渲染层。
   因此 `status/autocad-precision.json` 的「正交模式」条目在本片之后**仍是 partial**（库级与文档级已足，
   用户入口仍缺）—— 本片**不**把它报成 done。
"""

from __future__ import annotations

import math
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/precision_ops__w20.py 时的引导
    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1, new_document
    from cadcore.errors import CadError
    from cadcore.precision import osnap
else:
    from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1, new_document
    from cadcore.errors import CadError
    from cadcore.precision import osnap

#: 家族名（spec/precision_ops.json 冻结值）。
FAMILY = "precision_ops"

__all__ = ["FAMILY", "OPERATIONS", "ortho_constrain_sketch", "ortho_project",
           "ortho_segment_add", "ortho_snap_segment", "ortho_state_set", "selftest", "main"]

_AXES = ("horizontal", "vertical")


# --------------------------------------------------------------------------------------
# 小工具：错误 / 取值 / 节点读写（L1 节点既可能是 dict，也可能是对象属性形态）
# --------------------------------------------------------------------------------------
def _fail(reason: str, detail: str, **details: Any) -> CadError:
    return CadError(detail, reason=reason, **details)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """两点元组校验：必须是两个**整数**纳米（bool 不算 int，浮点一律拒）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("bad_point", "expected a 2-item integer-nm sequence", what=what, value=repr(value))
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("bad_point", "coordinates must be integers in nm", what=what, value=repr(item))
        out.append(int(item))
    return (out[0], out[1])


def _get(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        return node.get(key, default)
    return getattr(node, key, default)


def _set(node: Any, key: str, value: Any) -> None:
    if isinstance(node, dict):
        node[key] = value
    else:
        setattr(node, key, value)


def _uuid_text(value: Any, *, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_uuid", "expected a non-empty uuid string", what=what, value=repr(value))
    return value


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _is_sketch(node: Any) -> bool:
    return node is not None and _get(node, "entities") is not None and (
        _get(node, "constraints") is not None or _get(node, "dimensions") is not None)


def _sketches_of(target: Any) -> list[Any]:
    sketches = _get(target, "sketches")
    if sketches is None:
        raise _fail("bad_target", "target is neither a sketch nor a document with sketches")
    if not isinstance(sketches, list):
        raise _fail("bad_target", "document.sketches must be a list", value=type(sketches).__name__)
    return sketches


def _pick_sketch(target: Any, sketch: Any) -> Any:
    """目标是草图 ⇒ 用它本人；是文档 ⇒ 按 uuid / 下标 / 缺省取第一张。"""
    if _is_sketch(target):
        if sketch not in (None, 0):
            raise _fail("sketch_ambiguous", "target is a sketch; 'sketch' selector is not applicable")
        return target
    sketches = _sketches_of(target)
    if sketch is None:
        if not sketches:
            raise _fail("no_sketch", "document has no sketch to constrain")
        return sketches[0]
    if isinstance(sketch, bool):
        raise _fail("sketch_not_found", "sketch selector must be an index or a uuid", value=repr(sketch))
    if isinstance(sketch, int):
        if not -len(sketches) <= sketch < len(sketches):
            raise _fail("sketch_not_found", "sketch index out of range", index=sketch, count=len(sketches))
        return sketches[sketch]
    for cand in sketches:
        if _get(cand, "uuid") == sketch:
            return cand
    raise _fail("sketch_not_found", "no sketch with that uuid", uuid=repr(sketch))


def _entities(sk: Any) -> list[Any]:
    entities = _get(sk, "entities")
    if entities is None:
        raise _fail("bad_sketch", "sketch has no entities container")
    if isinstance(entities, list):
        return entities
    frozen = list(entities)
    _set(sk, "entities", frozen)
    return frozen


def _line_geom(entity: Any) -> dict[str, Any]:
    kind = _get(entity, "kind")
    if kind != "line":
        raise _fail("not_a_line", "only line entities carry ortho geometry here",
                    uuid=repr(_get(entity, "uuid")), kind=repr(kind))
    geom = _get(entity, "geom")
    if geom is None:
        raise _fail("bad_entity", "line entity has no geom", uuid=repr(_get(entity, "uuid")))
    if not isinstance(geom, dict):
        copied = dict(geom)
        _set(entity, "geom", copied)
        return copied
    return geom


def _axis_deltas(geom: Mapping[str, Any]) -> tuple[int, int]:
    x1 = _point((_get(geom, "x1"), _get(geom, "y1")), what="geom.p1")
    x2 = _point((_get(geom, "x2"), _get(geom, "y2")), what="geom.p2")
    return (x2[0] - x1[0], x2[1] - x1[1])


def _find_entity(sk: Any, entity_uuid: Any) -> Any:
    wanted = _uuid_text(entity_uuid, what="entity_uuid")
    for cand in _entities(sk):
        if _get(cand, "uuid") == wanted:
            return cand
    raise _fail("entity_not_found", "no entity with that uuid in the target sketch", uuid=wanted)


# --------------------------------------------------------------------------------------
# 核心几何：无条件正交投影（ORTHO 的硬约束语义）
# --------------------------------------------------------------------------------------
def _ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def ortho_project(base_nm: Any, point_nm: Any) -> tuple[int, int]:
    """把 `point_nm` **无条件**投影到过 `base_nm` 的最近水平/垂直轴上（ORTHO 硬约束）。

    与 `osnap.ortho_lock` 的**唯一**语义差别：本函数**不看容差** —— 点偏轴 30° 也照样投影；
    `ortho_lock` 超出 `tolerance_deg` 会原样返回（那是"吸附"，不是"正交模式"）。
    只动一个坐标（水平锁 y / 垂直锁 x），45° 平手取水平；两点重合 ⇒ `reason="degenerate_ortho"`。
    """
    base = _point(base_nm, what="base_nm")
    point = _point(point_nm, what="point_nm")
    dx = point[0] - base[0]
    dy = point[1] - base[1]
    if dx == 0 and dy == 0:
        raise _fail("degenerate_ortho", "base and point coincide: ortho direction is undefined",
                    base_nm=list(base))
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    horizontal = min(_ang_diff(angle, 0.0), _ang_diff(angle, 180.0))
    vertical = min(_ang_diff(angle, 90.0), _ang_diff(angle, 270.0))
    if horizontal <= vertical:  # 平手取水平（与 ortho_lock 同口径）
        return (point[0], base[1])
    return (base[0], point[1])


# --------------------------------------------------------------------------------------
# 操作 1：新建一条正交约束的线段（真实体落进 L1）
# --------------------------------------------------------------------------------------
def ortho_segment_add(sk_or_doc: Any, *, base_nm: Any, point_nm: Any, sketch: Any = None,
                      layer: str = "0", construction: bool = False) -> list[str]:
    """按正交约束在草图里**新建**一条线实体；返回 `[新实体 uuid]`。

    `point_nm` 先过 `ortho_project` 得到锁定点，再以 `base_nm → 锁定点` 建实体（端点都在整数纳米上）。
    点已在轴上 ⇒ 几何不变（恒等投影），**仍然建实体**（用户确实画了这一笔）。
    落盘字段与 `docmodel` 样例同形：`{"uuid","kind":"line","layer","construction","geom":{x1,y1,x2,y2}}`。
    """
    sk = _pick_sketch(sk_or_doc, sketch)
    base = _point(base_nm, what="base_nm")
    locked = ortho_project(base, point_nm)
    if not isinstance(layer, str) or not layer:
        raise _fail("bad_layer", "layer must be a non-empty string", value=repr(layer))
    entity: dict[str, Any] = {
        "uuid": _new_uuid(), "kind": "line", "layer": layer, "construction": bool(construction),
        "geom": {"x1": base[0], "y1": base[1], "x2": locked[0], "y2": locked[1]},
    }
    _entities(sk).append(entity)
    return [str(entity["uuid"])]


# --------------------------------------------------------------------------------------
# 操作 2：把一条已有线掰正（真改几何）
# --------------------------------------------------------------------------------------
def ortho_snap_segment(sk_or_doc: Any, *, entity_uuid: Any, sketch: Any = None,
                       base: str = "start") -> list[str]:
    """把已有线实体的**活动端点**掰到过基点端点的最近轴上；返回 `[该实体 uuid]`。

    `base="start"` ⇒ 动 `(x2,y2)`（`(x1,y1)` 当基点）；`base="end"` ⇒ 反之。
    点已在轴上 ⇒ 恒等变换：**不改数值、返回 `[]`** —— 与 `ortho_constrain_sketch` 同口径
    （零改动就是零改动，不拿一个没被改过的 uuid 冒充"已改动"）。
    零长线（两端点重合）⇒ `reason="degenerate_ortho"`（方向未定义，不猜）。
    """
    sk = _pick_sketch(sk_or_doc, sketch)
    entity = _find_entity(sk, entity_uuid)
    geom = _line_geom(entity)
    p1 = _point((_get(geom, "x1"), _get(geom, "y1")), what="geom.p1")
    p2 = _point((_get(geom, "x2"), _get(geom, "y2")), what="geom.p2")
    if base not in ("start", "end"):
        raise _fail("bad_base", "base must be 'start' or 'end'", value=repr(base))
    anchor, moving = (p1, p2) if base == "start" else (p2, p1)
    locked = ortho_project(anchor, moving)
    if (int(locked[0]), int(locked[1])) == (int(moving[0]), int(moving[1])):
        return []  # 恒等变换：零改动就是不报（与 ortho_constrain_sketch 同口径）
    if base == "start":
        geom["x2"], geom["y2"] = int(locked[0]), int(locked[1])
    else:
        geom["x1"], geom["y1"] = int(locked[0]), int(locked[1])
    return [_uuid_text(_get(entity, "uuid"), what="entity.uuid")]


# --------------------------------------------------------------------------------------
# 操作 3：把整张草图的斜线一次掰正（只报真改动的）
# --------------------------------------------------------------------------------------
def ortho_constrain_sketch(sk_or_doc: Any, *, sketch: Any = None) -> list[str]:
    """把目标草图里所有斜线一次掰正；返回**被改动**的实体 uuid（按实体顺序）。

    已经正交的线（`dx == 0` 或 `dy == 0`，含零长线）**不改也不报** —— 返回值里没有它，
    表示"零改动"，与"没算"可区分。非 line 实体一律不动、也不报（本片只管线段的正交）。
    """
    sk = _pick_sketch(sk_or_doc, sketch)
    changed: list[str] = []
    for entity in _entities(sk):
        if _get(entity, "kind") != "line":
            continue
        geom = _line_geom(entity)
        dx, dy = _axis_deltas(geom)
        if dx == 0 or dy == 0:
            continue  # 已正交或零长：什么都不做（零改动 ≠ 未采）
        p1 = _point((_get(geom, "x1"), _get(geom, "y1")), what="geom.p1")
        p2 = _point((_get(geom, "x2"), _get(geom, "y2")), what="geom.p2")
        locked = ortho_project(p1, p2)
        geom["x2"], geom["y2"] = int(locked[0]), int(locked[1])
        changed.append(_uuid_text(_get(entity, "uuid"), what="entity.uuid"))
    return changed


# --------------------------------------------------------------------------------------
# 操作 4：ORTHO 开关键 —— 委托既有单一真源（不另立状态）
# --------------------------------------------------------------------------------------
def ortho_state_set(sk_or_doc: Any, *, enabled: Any) -> list[str]:
    """ORTHO 开关的持久化：委托 `ops.precision_ops.ortho_set`，返回它给的记录 uuid 列表。

    **不在这里再写一份状态**（那会变成"两套话"）：开关的真源是 `precision_ops` 的
    `doc.extra["precision"] / sk["precision"]` 容器。委托不可用 ⇒ `CadError(reason="dependency_missing")`。
    """
    if not isinstance(enabled, bool):
        raise _fail("bad_flag", "enabled must be a bool", value=repr(enabled))
    if __package__ in (None, ""):
        from cadcore.ops.precision_ops import ortho_set
    else:
        from ..precision_ops import ortho_set
    if not callable(ortho_set):
        raise CadError("precision_ops.ortho_set is not callable", reason="dependency_missing")
    return list(ortho_set(sk_or_doc, enabled=enabled))


#: 冻结接口：操作名 → 可调用（每个都是 `op(sk_or_doc, **kwargs) -> list[str]`）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "ortho_segment_add": ortho_segment_add,
    "ortho_snap_segment": ortho_snap_segment,
    "ortho_constrain_sketch": ortho_constrain_sketch,
    "ortho_state_set": ortho_state_set,
}

_MM = 1_000_000


# --------------------------------------------------------------------------------------
# 自检：≥2 条硬断言（真几何 / 真数据后果 / 阴性对照 / 错误路径）
# --------------------------------------------------------------------------------------
def _fixture() -> Any:
    """最小 L1 文档：一张草图四条线（两条斜、一条水平、一条垂直）+ 一个拉伸特征。"""
    doc = new_document()
    sk_uuid = "a11ce000-0000-4000-8000-0000000000b0"
    doc.sketches = [{
        "uuid": sk_uuid, "plane": "XY",
        "entities": [
            {"uuid": "a11ce000-0000-4000-8000-0000000000b1", "kind": "line", "layer": "0",
             "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 20 * _MM, "y2": 5 * _MM}},
            {"uuid": "a11ce000-0000-4000-8000-0000000000b2", "kind": "line", "layer": "0",
             "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 3 * _MM, "y2": 30 * _MM}},
            {"uuid": "a11ce000-0000-4000-8000-0000000000b3", "kind": "line", "layer": "0",
             "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 10 * _MM, "y2": 0}},
            {"uuid": "a11ce000-0000-4000-8000-0000000000b4", "kind": "line", "layer": "0",
             "construction": False, "geom": {"x1": 5 * _MM, "y1": 0, "x2": 5 * _MM, "y2": 9 * _MM}},
        ],
        "constraints": [], "dimensions": [],
        "solved": {"dof": -1, "converged": False, "cache": True},
    }]
    doc.features = [{"uuid": "a11ce000-0000-4000-8000-0000000000b5", "kind": "extrude",
                     "inputs": [sk_uuid],
                     "params": {"distance_nm": _MM, "symmetric": False, "angle_deg": "0", "op": "new"},
                     "suppressed": False}]
    return doc


def selftest() -> int:
    """跑真几何 + 真落盘 + 阴性对照 + 错误路径；返回失败条数（0 = 全绿）。"""
    failed: list[str] = []
    checks = 0

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not passed:
            failed.append(f"{name}{(' :: ' + detail) if detail else ''}")

    # A. 无条件投影 vs 容差吸附：同一点，本片投影、osnap.ortho_lock 原样返回（阴性对照=尺子看得见差别）
    a_locked = ortho_project((0, 0), (10 * _MM, 10 * _MM))
    a_legacy = osnap.ortho_lock((0, 0), (10 * _MM, 10 * _MM))
    check("A1_ortho_project_unconditional_45deg_tie_horizontal",
          a_locked == (10 * _MM, 0), f"got={a_locked!r}")
    check("A2_legacy_ortho_lock_leaves_out_of_tolerance_point",
          tuple(a_legacy) == (10 * _MM, 10 * _MM), f"got={a_legacy!r}")
    b_locked = ortho_project((0, 0), (3 * _MM, 30 * _MM))
    check("A3_vertical_wins_when_near_vertical", b_locked == (0, 30 * _MM), f"got={b_locked!r}")

    # B. 新建正交段：真实体落进草图 + 不变量干净 + 只有 x 动了
    doc = _fixture()
    added = OPERATIONS["ortho_segment_add"](doc, base_nm=(0, 0), point_nm=(12 * _MM, 4 * _MM),
                                            layer="ORTHO", construction=True)
    sk = doc.sketches[0]
    check("B1_returns_single_new_uuid",
          len(added) == 1 and isinstance(added[0], str) and len(added[0]) >= 8, f"got={added!r}")
    fresh = [e for e in sk["entities"] if e["uuid"] == added[0]]
    check("B2_entity_really_appended_with_ortho_geom",
          len(fresh) == 1 and fresh[0]["geom"] == {"x1": 0, "y1": 0, "x2": 12 * _MM, "y2": 0}
          and fresh[0]["kind"] == "line" and fresh[0]["layer"] == "ORTHO"
          and fresh[0]["construction"] is True,
          f"got={fresh!r}")
    check("B3_check_invariants_clean", check_invariants(doc) == [], f"got={check_invariants(doc)!r}")

    # C. L1 往返：实体与新几何必须读得回来（持久化真后果，不是内存里的桩）
    text = document_to_l1(doc)
    back = document_from_l1(text)
    back_ents = back.sketches[0]["entities"]
    check("C1_roundtrip_keeps_new_entity",
          any(e["uuid"] == added[0] and e["geom"]["x2"] == 12 * _MM and e["geom"]["y2"] == 0
              for e in back_ents),
          f"count={len(back_ents)}")
    check("C2_roundtrip_check_invariants_clean", check_invariants(back) == [],
          f"got={check_invariants(back)!r}")

    # D. 掰正已有斜线：只动活动端点，另一端点逐字节不变
    doc2 = _fixture()
    got = OPERATIONS["ortho_snap_segment"](doc2, entity_uuid="a11ce000-0000-4000-8000-0000000000b2")
    g2 = doc2.sketches[0]["entities"][1]["geom"]
    check("D1_snap_segment_returns_that_uuid",
          got == ["a11ce000-0000-4000-8000-0000000000b2"], f"got={got!r}")
    check("D2_snap_segment_makes_it_axis_aligned",
          g2 == {"x1": 0, "y1": 0, "x2": 0, "y2": 30 * _MM}, f"got={g2!r}")
    check("D3_snap_segment_invariants_clean", check_invariants(doc2) == [],
          f"got={check_invariants(doc2)!r}")

    # E. 整图掰正：只报真改动的两条斜线（已正交的两条**不进**列表 = 零改动零报）
    doc3 = _fixture()
    changed = OPERATIONS["ortho_constrain_sketch"](doc3)
    check("E1_only_diagonals_reported",
          changed == ["a11ce000-0000-4000-8000-0000000000b1",
                      "a11ce000-0000-4000-8000-0000000000b2"], f"got={changed!r}")
    aligned_untouched = [e["geom"] for e in doc3.sketches[0]["entities"][2:]]
    check("E2_already_aligned_untouched_bytewise",
          aligned_untouched == [{"x1": 0, "y1": 0, "x2": 10 * _MM, "y2": 0},
                                {"x1": 5 * _MM, "y1": 0, "x2": 5 * _MM, "y2": 9 * _MM}],
          f"got={aligned_untouched!r}")
    check("E3_idempotent_second_pass_reports_nothing",
          OPERATIONS["ortho_constrain_sketch"](doc3) == [])
    check("E4_constrain_invariants_clean", check_invariants(doc3) == [],
          f"got={check_invariants(doc3)!r}")

    # F. 错误路径：必须抛 CadError 且带机读 reason（不许静默出零结果）
    try:
        OPERATIONS["ortho_snap_segment"](_fixture(), entity_uuid="00000000-0000-4000-8000-00000000dead")
        check("F1_unknown_entity_raises", False, "no exception")
    except CadError as exc:
        check("F1_unknown_entity_raises", exc.reason == "entity_not_found", f"reason={exc.reason!r}")
    try:
        ortho_project((7 * _MM, 7 * _MM), (7 * _MM, 7 * _MM))
        check("F2_coincident_raises_degenerate", False, "no exception")
    except CadError as exc:
        check("F2_coincident_raises_degenerate", exc.reason == "degenerate_ortho", f"reason={exc.reason!r}")
    try:
        OPERATIONS["ortho_segment_add"](_fixture(), base_nm=(0, 0), point_nm=(1.5, 2))
        check("F3_float_nm_rejected", False, "no exception")
    except CadError as exc:
        check("F3_float_nm_rejected", exc.reason == "bad_point", f"reason={exc.reason!r}")

    # G. ORTHO 开关键走既有单一真源（真落盘，真读回）
    doc4 = _fixture()
    on = OPERATIONS["ortho_state_set"](doc4, enabled=True)
    store = doc4.extra.get("precision") if isinstance(doc4.extra, dict) else None
    modes = (store or {}).get("modes") or []
    ortho_rec = [m for m in modes if isinstance(m, dict) and m.get("name") == "ortho"]
    check("G1_state_set_returns_record_uuid",
          len(on) >= 1 and all(isinstance(u, str) and u for u in on), f"got={on!r}")
    check("G2_state_persisted_enabled_true",
          len(ortho_rec) == 1 and ortho_rec[0].get("enabled") is True, f"got={ortho_rec!r}")
    back4 = document_from_l1(document_to_l1(doc4))
    modes4 = ((back4.extra or {}).get("precision") or {}).get("modes") or []
    check("G3_state_survives_l1_roundtrip",
          any(m.get("name") == "ortho" and m.get("enabled") is True for m in modes4),
          f"got={modes4!r}")

    print(f"SELFTEST family={FAMILY} checks={checks} failed={len(failed)}")
    for item in failed:
        print(f"  FAIL {item}")
    return len(failed)


def main(argv: Sequence[str] | None = None) -> int:
    """跑自检并打判定行（判定行必须是最后一行）。"""
    op_name = "ortho_segment_add"
    try:
        failed = selftest()
    except Exception as exc:  # 自检自己炸了也要有判定行，不许无声
        print(f"  FAIL selftest_crashed :: {type(exc).__name__}: {exc}")
        print(f"FEATURE_FAIL family={FAMILY} op={op_name} verdict=FAIL reason=selftest_crashed")
        return 1
    if failed:
        print(f"FEATURE_FAIL family={FAMILY} op={op_name} verdict=FAIL failed={failed}")
        return 1
    print(f"FEATURE_OK family={FAMILY} op={op_name} verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
