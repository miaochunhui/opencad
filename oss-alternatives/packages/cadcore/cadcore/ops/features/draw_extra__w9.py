#!/usr/bin/env python3
"""DRAW-EXTRA 实现片 w9：直线链式连续绘制（续接 / C 闭合 / 命令内 U 撤销）。

本条对应族清单里第 0 号仍 partial 的能力 —— `直线`（`status/autocad-draw.json` 的 `items[0]`，
其 `note` 逐字点出缺的三件：无链式连续画（上一段终点不续接）、无命令内 U 撤销上一点、无 C 闭合）。
既有族模块 `ops/draw_ops.py:line_create` 只落**单段**直线（一个 `line` 实体，返回长度恒为 1），
链式语义在 L1 上确实没有。本片在 L1 文档模型上**真实现**它：

* N 个拾取点 ⇒ N-1 个**独立 `line` 实体**（`close=True` 时 N 个），相邻段端点**逐位共享**
  （整数纳米直传，不经过浮点毫米，不做 round）；
* C 闭合 = 追加一条末点→首点的实体（首末点重合时按零长段报错，不静默丢段）；
* U 撤销 = 把链尾 `steps` 个实体**真从草图 entities 里移除**（in-place，列表对象身份不变）。

族接口（冻结）：

    FAMILY = "draw_extra"
    OPERATIONS: dict[str, Callable]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]

本片登记的两个操作：

* `line_chain(sk_or_doc, points_nm, *, close=False, layer="0", construction=False, sketch_index=0)`
  链式连续直线。返回本次**新建**的每个 `line` 实体的 uuid（按拾取顺序，长度 = 段数）。
* `line_chain_undo(sk_or_doc, uuids, *, steps=1, sketch_index=0)`
  命令内 U。把链尾 `steps` 段真删掉，返回**被删**的 uuid（按链序）；只删 `line`，不碰别人的弧/圆。

返回值口径（与族内一致）：只返回本次真改动/新建的对象 uuid；一个都没命中 ⇒ 抛
`CadError(reason="empty_selection")`，**不返回空列表冒充成功**。

输入口径：

* `sk_or_doc` 接受三种形态：① 裸草图映射（含 `entities` 列表）；② L1 载荷/文档映射（含 `sketches`
  列表，用 `sketch_index` 选草图）；③ `docmodel.Document`（用 `sketches[sketch_index]`）。
  **本片不发明草图骨架**：目标文档里没有草图 ⇒ `CadError(reason="sketch_missing")` —— L1 草图 schema
  归 `cadcore.docmodel` 所有，本片不越界造第二种形态（与 `draw_ops._sketch_of` 的口径一致）。
* 长度一律整数纳米（1 mm = 1_000_000 nm）：`bool`、非整数值 float、NaN/Inf 一律拒绝
  （`CadError(reason="err.out_of_range")`），绝不静默取整。
* 实体按 L1 冻结字段落库 `{uuid, kind, layer, construction, geom}`，`line` 的
  `geom = {x1,y1,x2,y2}`（与 `sketch/commands.py:add_line` 同形），**不自造第六个字段**
  （因此 `docmodel.check_invariants()` 不被本片污染）。

错误键（沿用族内既有键，不另立）：

* 少于所需点数 ⇒ `err.dimension_shortfall`（details = `{required, actual}`）；
* 相邻点重合（零长段）/ 闭合段零长 ⇒ `geometry_invalid`（与 `draw_ops.line_create` 的零长线同键）；
* `sketch_index` / `steps` 越界、入参非整数纳米 ⇒ `err.out_of_range`（details = `{value, min, max}`）；
* U 的目标不是 `line` 实体 ⇒ `unsupported_kind`（details = `{uuid, kind}`）。

原子性：全部校验在**任何写之前**完成 —— 校验失败时草图实体数逐位不变（自检有硬断言）。

三态口径（AGENTS.md E2.3）：`0` = 真为零；`-1` = 未采；`"NA"` = 不适用。本片不产用户可见文案
（字符串常量一律 ASCII），错误一律 `reason` + ASCII detail。

自检（文件自带，不依赖 pytest，19 条断言）：

    python3 cadcore/ops/features/draw_extra__w9.py

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import math
import os
import sys
import uuid as _uuid
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch import constraints as _constraints

__all__ = ["FAMILY", "OPERATIONS", "NM_PER_MM", "line_chain", "line_chain_undo", "selftest"]

FAMILY = "draw_extra"
NM_PER_MM = 1_000_000
_KIND_LINE = "line"
_ENTITY_FIELDS = ("construction", "geom", "kind", "layer", "uuid")


# --------------------------------------------------------------------------------------
# 入参校验（整数纳米口径）
# --------------------------------------------------------------------------------------


def _nm_int(value: Any, *, what: str) -> int:
    """整数纳米入参校验：`bool` / 非整数值 / 非有限 float / 其它类型一律拒绝，绝不静默取整。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CadError("nm argument must be an integer number of nanometres",
                       reason="err.out_of_range", what=what, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or not float(value).is_integer():
            raise CadError("nm argument must be a finite integer number of nanometres",
                           reason="err.out_of_range", what=what, value=repr(value))
    return int(value)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参：必须是长度 2 的序列（x_nm, y_nm）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise CadError("point must be a pair of integer nanometre coordinates",
                       reason="err.out_of_range", what=what, value=repr(value))
    return (_nm_int(value[0], what=what + ".x"), _nm_int(value[1], what=what + ".y"))


def _points_nm(value: Any, *, minimum: int) -> list[tuple[int, int]]:
    """点列入参：整列先全量校验（这样失败时一个实体都不会落库）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CadError("points_nm must be a sequence of nanometre coordinate pairs",
                       reason="err.out_of_range", what="points_nm", value=repr(value))
    points = [_point(item, what="points_nm[%d]" % index) for index, item in enumerate(value)]
    if len(points) < minimum:
        raise CadError("not enough points for this polyline chain",
                       reason="err.dimension_shortfall", required=minimum, actual=len(points))
    return points


def _flag(value: Any, *, what: str) -> bool:
    if not isinstance(value, bool):
        raise CadError("flag argument must be a bool", reason="err.out_of_range", what=what, value=repr(value))
    return value


def _layer_name(layer: Any) -> str:
    if not isinstance(layer, str) or not layer.strip():
        raise CadError("layer must be a non-empty string", reason="err.out_of_range", what="layer", value=repr(layer))
    return layer


def _sketch_index_checked(sketch_index: Any) -> int:
    if isinstance(sketch_index, bool) or not isinstance(sketch_index, int) or sketch_index < 0:
        raise CadError("sketch_index must be a non-negative int", reason="err.out_of_range",
                       what="sketch_index", value=repr(sketch_index), min=0, max="NA")
    return int(sketch_index)


def _sketch_of(target: Any, *, sketch_index: int = 0) -> MutableMapping[str, Any]:
    """`sk_or_doc` → 可变草图映射（裸草图 / L1 载荷映射 / `docmodel.Document` 三形态）。"""
    index = _sketch_index_checked(sketch_index)
    if isinstance(target, MutableMapping) and "entities" in target:
        sketch: Any = target
    else:
        sketches: Any = None
        if isinstance(target, Mapping):
            sketches = target.get("sketches")
        elif target is not None:
            sketches = getattr(target, "sketches", None)
        if not isinstance(sketches, Sequence) or isinstance(sketches, (str, bytes)) or len(sketches) == 0:
            raise CadError("target has no sketch to draw into", reason="sketch_missing", sketch_index=index)
        if index >= len(sketches):
            raise CadError("sketch_index out of range", reason="err.out_of_range",
                           what="sketch_index", value=index, min=0, max=len(sketches) - 1)
        sketch = sketches[index]
    if not isinstance(sketch, MutableMapping):
        raise CadError("sketch must be a mutable mapping", reason="geometry_invalid",
                       field="sketches[%d]" % index, value_type=type(sketch).__name__)
    return sketch


def _entities_of(sketch: MutableMapping[str, Any]) -> list[Any]:
    entities = sketch.get("entities")
    if not isinstance(entities, list):
        raise CadError("sketch.entities must be a list", reason="geometry_invalid",
                       field="entities", value_type=type(entities).__name__)
    return entities


def _new_uuid() -> str:
    """实体 id：优先复用 `cadcore.sketch.constraints.new_uuid`（族内单一真源），缺席才回落 stdlib。"""
    maker = getattr(_constraints, "new_uuid", None)
    if callable(maker):
        try:
            value = maker()
        except TypeError:  # 签名不兼容（需要参数）⇒ 走 stdlib，不猜参数
            value = None
        if isinstance(value, str) and value:
            return value
    return str(_uuid.uuid4())


# --------------------------------------------------------------------------------------
# 操作一：链式连续直线
# --------------------------------------------------------------------------------------


def line_chain(sk_or_doc: Any, points_nm: Any = None, *, close: bool = False, layer: str = "0",
               construction: bool = False, sketch_index: int = 0) -> list[str]:
    """链式连续直线：N 个拾取点 ⇒ N-1 个独立 `line` 实体（`close=True` ⇒ N 个），返回 uuid 列表。

    语义（对齐 AutoCAD LINE 的连续画 + C 闭合）：

    * 每一段都是**独立实体**（不是一条 polyline，也不是一个 flag），上一段终点 = 下一段起点，
      坐标整数纳米直传 ⇒ 共享端点**逐位相等**（自检 `joints_shared_bit_exact` 硬断言）；
    * `close=True` 追加「末点 → 首点」一段，也返回它的 uuid（`closed_returns_to_first_pick` 断言）；
    * 退化即报错，不静默跳过：相邻点重合 / 闭合段零长 ⇒ `geometry_invalid`（details 带段号与点）；
    * 原子性：所有校验在写之前完成，报错时草图实体数**逐位不变**（`zero_length_is_atomic` 断言）。
    """
    closed = _flag(close, what="close")
    is_construction = _flag(construction, what="construction")
    layer_name = _layer_name(layer)
    minimum = 3 if closed else 2
    points = _points_nm(points_nm, minimum=minimum)
    sketch = _sketch_of(sk_or_doc, sketch_index=sketch_index)

    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for index in range(len(points) - 1):
        if points[index] == points[index + 1]:
            raise CadError("zero-length segment in chain", reason="geometry_invalid",
                           index=index, point=list(points[index]))
        segments.append((points[index], points[index + 1]))
    if closed:
        if points[-1] == points[0]:
            raise CadError("zero-length closing segment in chain", reason="geometry_invalid",
                           index=len(points) - 1, point=list(points[0]))
        segments.append((points[-1], points[0]))

    entities = _entities_of(sketch)
    created: list[str] = []
    for start, end in segments:
        entity: dict[str, Any] = {
            "uuid": _new_uuid(),
            "kind": _KIND_LINE,
            "layer": layer_name,
            "construction": is_construction,
            "geom": {"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]},
        }
        entities.append(entity)
        created.append(str(entity["uuid"]))
    return created


# --------------------------------------------------------------------------------------
# 操作二：命令内 U 撤销（真删实体）
# --------------------------------------------------------------------------------------


def line_chain_undo(sk_or_doc: Any, uuids: Any = None, *, steps: int = 1, sketch_index: int = 0) -> list[str]:
    """命令内 U：真删链尾 `steps` 段，返回**被删**uuid（按链序）。

    口径：

    * `uuids` 是 `line_chain` 的返回值（或它的前缀）；只删链尾，不按 uuid 大小/几何猜顺序；
    * 目标不在草图里 ⇒ `CadError(reason="empty_selection")`（带 requested/missing 计数，不静默少删）；
    * 目标是 `line` 以外的 kind ⇒ `CadError(reason="unsupported_kind")`，**一个都不删**（先全量校验再删）；
    * 删除是 in-place（`sketch["entities"]` 还是同一个列表对象，`undo_in_place_list_identity` 断言）。
    """
    if isinstance(uuids, (str, bytes)) or not isinstance(uuids, Sequence) or uuids is None:
        raise CadError("uuids must be a sequence of entity uuid strings", reason="err.out_of_range",
                       what="uuids", value=repr(uuids))
    requested = [str(item) for item in uuids]
    if not requested:
        raise CadError("empty selection: nothing to undo", reason="empty_selection", requested=0)
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1 or steps > len(requested):
        raise CadError("steps out of range", reason="err.out_of_range",
                       what="steps", value=repr(steps), min=1, max=len(requested))

    sketch = _sketch_of(sk_or_doc, sketch_index=sketch_index)
    entities = _entities_of(sketch)
    found: dict[str, str] = {}
    for entity in entities:
        if isinstance(entity, Mapping):
            found[str(entity.get("uuid"))] = str(entity.get("kind"))

    doomed = list(requested[-steps:])
    missing = [item for item in doomed if item not in found]
    if missing:
        raise CadError("chain segment not found in sketch", reason="empty_selection",
                       requested=len(doomed), missing=len(missing))
    for item in doomed:
        if found[item] != _KIND_LINE:
            raise CadError("undo target is not a line entity", reason="unsupported_kind",
                           uuid=item, kind=found[item])

    doomed_set = set(doomed)
    entities[:] = [entity for entity in entities
                   if not (isinstance(entity, Mapping) and str(entity.get("uuid")) in doomed_set)]
    return doomed


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "line_chain": line_chain,
    "line_chain_undo": line_chain_undo,
}


# --------------------------------------------------------------------------------------
# 自检（真跑真几何，硬断言；不依赖 pytest）
# --------------------------------------------------------------------------------------

_CHECKS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> bool:
    _CHECKS.append((name, bool(ok), detail))
    if not ok:
        print("CHECK_FAIL %s %s" % (name, detail))
    return bool(ok)


def _reason_of(call: Callable[[], Any]) -> tuple[str, dict[str, Any]]:
    """跑一次调用，返回 (`reason`, `details`)；没报错返回 ("no_error", {}) —— 便于逐条硬断言。"""
    try:
        call()
    except CadError as exc:
        return str(exc.reason), dict(exc.details)
    return "no_error", {}


def selftest() -> int:
    del _CHECKS[:]
    mm = NM_PER_MM

    _check("operations_registered",
           FAMILY == "draw_extra" and set(OPERATIONS) == {"line_chain", "line_chain_undo"},
           "family=%r ops=%r" % (FAMILY, sorted(OPERATIONS)))

    # --- 1) 4 个拾取点不闭合 ⇒ 3 个独立 line 实体，接缝逐位共享，值就是整数纳米
    sketch: dict[str, Any] = {"entities": []}
    picks = [(0, 0), (40 * mm, 0), (40 * mm, 30 * mm), (0, 30 * mm)]
    chain = line_chain(sketch, picks)
    ents = sketch["entities"]
    _check("chain_len", len(chain) == 3 and len(ents) == 3,
           "len(chain)=%d entities=%d" % (len(chain), len(ents)))
    _check("chain_uuid_is_live_entity", [e.get("uuid") for e in ents] == list(chain),
           "uuids=%r" % (list(chain),))
    _check("chain_kind_all_line", all(e.get("kind") == "line" for e in ents),
           "kinds=%r" % ([e.get("kind") for e in ents],))
    geoms = [e.get("geom") for e in ents]
    expect = [{"x1": 0, "y1": 0, "x2": 40 * mm, "y2": 0},
              {"x1": 40 * mm, "y1": 0, "x2": 40 * mm, "y2": 30 * mm},
              {"x1": 40 * mm, "y1": 30 * mm, "x2": 0, "y2": 30 * mm}]
    _check("geom_exact_nm_no_float", geoms == expect, "geoms=%r" % (geoms,))
    _check("joints_shared_bit_exact",
           (geoms[0]["x2"], geoms[0]["y2"]) == (geoms[1]["x1"], geoms[1]["y1"])
           and (geoms[1]["x2"], geoms[1]["y2"]) == (geoms[2]["x1"], geoms[2]["y1"]),
           "joints=%r" % (geoms,))
    _check("all_values_int_nm",
           all(isinstance(v, int) and not isinstance(v, bool) for g in geoms for v in g.values()),
           "types=%r" % ([[type(v).__name__ for v in g.values()] for g in geoms],))
    _check("entity_fields_frozen", all(tuple(sorted(e)) == _ENTITY_FIELDS for e in ents),
           "keys=%r" % ([sorted(e) for e in ents],))

    # --- 2) C 闭合：3 点 ⇒ 3 段，末段回到首点
    sketch2: dict[str, Any] = {"entities": []}
    tri = [(0, 0), (10 * mm, 0), (10 * mm, 10 * mm)]
    closed = line_chain(sketch2, tri, close=True)
    cg = [e["geom"] for e in sketch2["entities"]]
    _check("closed_len_eq_pick_count", len(closed) == 3 and len(cg) == 3, "len=%d geoms=%d" % (len(closed), len(cg)))
    _check("closed_returns_to_first_pick",
           (cg[-1]["x1"], cg[-1]["y1"]) == tri[-1] and (cg[-1]["x2"], cg[-1]["y2"]) == tri[0],
           "last=%r" % (cg[-1],))

    # --- 3) U 撤销：真删实体，且是 in-place
    entities_ref = sketch2["entities"]
    removed = line_chain_undo(sketch2, closed)
    _check("undo_removed_tail_uuid", removed == [closed[-1]], "removed=%r" % (removed,))
    _check("undo_shrank_real_entities",
           len(sketch2["entities"]) == 2 and closed[-1] not in [e["uuid"] for e in sketch2["entities"]],
           "n=%d" % (len(sketch2["entities"]),))
    _check("undo_in_place_list_identity", sketch2["entities"] is entities_ref,
           "identity=%s" % (sketch2["entities"] is entities_ref,))

    # --- 4) 退化与原子性
    sketch3: dict[str, Any] = {"entities": []}
    reason = _reason_of(lambda: line_chain(sketch3, [(0, 0), (0, 0), (5 * mm, 0)]))[0]
    _check("zero_length_segment_raises", reason == "geometry_invalid", "reason=%r" % (reason,))
    _check("zero_length_is_atomic", sketch3["entities"] == [], "entities=%r" % (sketch3["entities"],))

    reason, details = _reason_of(lambda: line_chain(sketch3, [(0, 0)]))
    _check("too_few_points_details",
           reason == "err.dimension_shortfall" and details.get("required") == 2 and details.get("actual") == 1,
           "reason=%r details=%r" % (reason, details))

    reason, details = _reason_of(lambda: line_chain(sketch3, [(0, 0), (mm, 0)], close=True))
    _check("closed_needs_three_points",
           reason == "err.dimension_shortfall" and details.get("required") == 3 and details.get("actual") == 2,
           "reason=%r details=%r" % (reason, details))

    reason = _reason_of(lambda: line_chain(sketch3, [(0, 0), (1.5, 0)]))[0]
    _check("float_nm_rejected", reason == "err.out_of_range", "reason=%r" % (reason,))
    _check("rejected_input_wrote_nothing", sketch3["entities"] == [], "entities=%r" % (sketch3["entities"],))

    # --- 5) U 的护栏：非 line 实体不删；空选择/缺目标 loud
    sketch4: dict[str, Any] = {"entities": [{"uuid": "u-circle", "kind": "circle", "layer": "0",
                                             "construction": False, "geom": {"cx": 0, "cy": 0, "r": mm}}]}
    reason, details = _reason_of(lambda: line_chain_undo(sketch4, ["u-circle"]))
    _check("undo_refuses_non_line",
           reason == "unsupported_kind" and len(sketch4["entities"]) == 1 and details.get("kind") == "circle",
           "reason=%r details=%r n=%d" % (reason, details, len(sketch4["entities"])))
    reason = _reason_of(lambda: line_chain_undo(sketch4, []))[0]
    _check("empty_undo_selection_loud", reason == "empty_selection", "reason=%r" % (reason,))
    reason = _reason_of(lambda: line_chain_undo(sketch4, ["nope"]))[0]
    _check("undo_missing_target_loud", reason == "empty_selection", "reason=%r" % (reason,))

    # --- 6) 载荷/文档形态：落进 sketches[i].entities，闭合 4 点 ⇒ 4 段，bbox = 30 mm x 20 mm
    doc: dict[str, Any] = {"sketches": [{"entities": []}]}
    rect = line_chain(doc, [(0, 0), (30 * mm, 0), (30 * mm, 20 * mm), (0, 20 * mm)], close=True)
    rect_ents = doc["sketches"][0]["entities"]
    _check("doc_form_four_sides", len(rect) == 4 and len(rect_ents) == 4,
           "len=%d entities=%d" % (len(rect), len(rect_ents)))
    xs = [v for e in rect_ents for v in (e["geom"]["x1"], e["geom"]["x2"])]
    ys = [v for e in rect_ents for v in (e["geom"]["y1"], e["geom"]["y2"])]
    _check("doc_form_closed_bbox", (min(xs), min(ys), max(xs), max(ys)) == (0, 0, 30 * mm, 20 * mm),
           "bbox=%r" % ((min(xs), min(ys), max(xs), max(ys)),))
    reason = _reason_of(lambda: line_chain({"sketches": []}, [(0, 0), (mm, 0)]))[0]
    _check("no_sketch_is_loud", reason == "sketch_missing", "reason=%r" % (reason,))

    failed = [name for name, ok, _ in _CHECKS if not ok]
    print("SELFTEST family=%s checks=%d failed=%d %s"
          % (FAMILY, len(_CHECKS), len(failed), "ok" if not failed else "list=" + ",".join(failed)))
    return 0 if not failed else 1


def main(argv: Sequence[str] | None = None) -> int:
    rc = selftest()
    if rc == 0:
        print("FEATURE_OK family=%s op=%s verdict=PASS" % (FAMILY, "line_chain"))
    else:
        print("FEATURE_FAIL family=%s op=%s verdict=FAIL" % (FAMILY, "line_chain"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
