#!/usr/bin/env python3
"""sw_drawings 族实现片 w24：零件序号（气球）标注 —— BOM 行驱动，并与明细表行真对象互链。

族接口（冻结）
--------------
    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    op(sk_or_doc, **kwargs) -> list[str]      # 返回本次新建对象的 uuid
    长度一律整数纳米；错误一律 `cadcore.errors.CadError(reason=...)`。

本片负责的条目（编号口径见任务书）
---------------------------------
`docs/research/cad-parity/features/sw-drawings.json` 的 must_have 共 21 条，按清单顺序取出
`status/sw-drawings.json` 里仍 missing/partial 的 9 条（0..8）：
0 视图调色板/投影视图、1 尺寸标注（导入模型尺寸+智能尺寸）、2 尺寸公差与配合、3 形位公差 GD&T、
4 表面粗糙度、5 孔标注、**6 零件序号（气球）**、7 工程图模板 .drwdot、8 打印与导出。
本席 = 第 15 号 ⇒ 15 mod 9 = 6 ⇒ **零件序号（气球）标注**（status 记 partial）。

做了什么（真几何 + 真数据后果，无桩）
------------------------------------
`balloon_from_bom`：行来源三级 —— 显式 `items=` → 文档顶层 `source_key`（缺省 `x_bom`）→
**文档里已有的 `kind="bom_row"` 行对象**。每行造 ① 序号圆（`Curve.circle`，半径 `radius_nm`）
② 引线（圆左缘点 → 锚点，`Curve.line`）③ 箭头两笔（`_arrow_curves` 按 25° 真算的倒钩），
全部落在 `BALLOON` 图层 + 一个新视图里；同时落一条 `kind="balloon"` 的 annotation，其中
`args["bom_row_uuid"]` **指向文档里真实的 `kind="bom_row"` 行对象**（不是自造行号），
`args` 另记 `bom_row_basis`（行来源出处：`args.items` / `doc.<key>` / `doc_annotations`）、
`bom_rows_linked`、以及几何真值（圆心 / 锚点 / 引线长，整数纳米）。

真数据后果（可机检）
--------------------
* 同一锚点的多行 ⇒ **堆叠气球**：圆心沿 +y 逐个偏移 `2*radius_nm + stack_gap_nm`，各自一条斜引线；
* `require_bom_rows=True` 时，任何一颗气球找不到对应 BOM 行 ⇒ `CadError(reason="balloon_bom_mismatch")`，
  且**先校验后落笔**（不留半成品）；
* 裸草图（没有 `annotations[]`）⇒ `bom_needs_document`；无行来源 ⇒ `bom_source_missing`；
  位置个数 ≠ 行数 ⇒ `balloon_position_mismatch`；整批一颗都放不下 ⇒ `balloon_off_sheet`；
  个别放不下 ⇒ 跳过 + `Sheet.note()` 留痕（跳过的不进返回值）；
* 气球 uuid = `uuid5(图纸 | 序号 | part_key | 圆心)` ⇒ 重复调用**幂等**（同 uuid 覆盖，不重复追加）。

自检（文件自带，不依赖 pytest）
------------------------------
    python3 cadcore/ops/features/sw_drawings__w24.py
    ⇒ 末行 `FEATURE_OK family=sw_drawings op=balloon_from_bom verdict=PASS`

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import json
import math
import os
import sys
import uuid as _uuid
from collections.abc import Sequence as _Seq
from typing import Any, Callable, Mapping

from cadcore.errors import CadError
from cadcore.draw import sheet as _sheet
from cadcore import docmodel as _docmodel
from cadcore.ops import sw_drawings as _base

__all__ = [
    "FAMILY", "OPERATIONS", "BALLOON_LAYER",
    "DEFAULT_RADIUS_NM", "DEFAULT_LEADER_NM", "DEFAULT_ROW_GAP_NM",
    "DEFAULT_STACK_GAP_NM", "DEFAULT_ARROW_NM",
    "balloon_from_bom", "module_facts", "selftest",
]

#: 族 id（冻结接口）。
FAMILY = "sw_drawings"

NM_PER_MM = _base.NM_PER_MM

#: 缺省值一律从 `sw_drawings` 取同源常数，不另立一套口径。
DEFAULT_STANDARD = _base.DEFAULT_STANDARD
DEFAULT_LANGUAGE = _base.DEFAULT_LANGUAGE
DEFAULT_SIZE = _base.DEFAULT_SIZE
DEFAULT_SCALE = _base.DEFAULT_SCALE
DEFAULT_MARGIN_NM = _base.DEFAULT_MARGIN_NM

DEFAULT_RADIUS_NM = 6 * NM_PER_MM
DEFAULT_LEADER_NM = 25 * NM_PER_MM
DEFAULT_ROW_GAP_NM = 4 * NM_PER_MM
DEFAULT_STACK_GAP_NM = 2 * NM_PER_MM
DEFAULT_ARROW_NM = 3 * NM_PER_MM

#: 序号圆/引线/箭头所在图层（另立一层，渲染时可按层开关）。
BALLOON_LAYER = "BALLOON"

_OP_NAME = "balloon_from_bom"
_BOM_ROW_KINDS = ("bom_row", "bom")
_ARROW_HALF_ANGLE_DEG = 25.0


# --------------------------------------------------------------------------------------
# 小工具（校验/登记一律复用 `sw_drawings` 的同一份实现，避免两处口径）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII，面向用户的串走语言包）。"""
    return CadError(str(message), reason=str(reason), **details)


def _non_negative(value: Any, *, what: str) -> int:
    """非负整数纳米校验（0 合法：`leader_nm=0` 表示不画引线）。"""
    out = _base._require_nm(value, what=what)
    if out < 0:
        raise _fail("bad_balloon_size", "%s must be non-negative" % what, field=what, value_nm=out)
    return out


def _doc_target(payload: Mapping[str, Any], target_uuid: Any, sheet_obj: Any) -> str:
    """annotation 的引用目标：显式 uuid → 文档 `meta.doc_uuid` → 由图纸 uuid 派生的确定性 uuid5。"""
    if target_uuid is not None:
        text = str(target_uuid)
        if not _base._UUID_RE.match(text):
            raise _fail("bad_target_uuid", "target_uuid must be a uuid string", value=text)
        return text
    doc_uuid = _base._doc_uuid(payload)
    if doc_uuid:
        return doc_uuid
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                           "cadcore.sw_drawings.%s.target|%s" % (_OP_NAME, sheet_obj.uuid)))


def _int_or_none(value: Any) -> int | None:
    """真整数才认（`bool` 不算，浮点不算 —— 不替调用方猜）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _first_str(args: Mapping[str, Any], keys: _Seq[str]) -> str:
    """按候选键序取第一个非空字符串（行对象的字段名跨模块不统一，读侧放宽、写侧照旧）。"""
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _rows_from_live_annotations(annotations: _Seq[Any]) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """把文档里**已有的** `kind="bom_row"` 行对象读回成行（第三条行来源，也是气球要指向的真对象）。

    返回 `(rows, item→uuid)`；行里带 `bom_row_uuid`（该行的真对象 uuid），读不到 uuid 时为 `None`
    （**不静默填一个假 uuid**）。序号缺字段时按出现顺序编号 1..n，并在 `bom_row_basis` 里留出处。
    """
    rows: list[dict[str, Any]] = []
    index: dict[int, str] = {}
    for entry in annotations:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("kind") or "") not in _BOM_ROW_KINDS:
            continue
        args = entry.get("args")
        if not isinstance(args, Mapping):
            continue
        item: int | None = None
        for key in ("item", "item_no", "row", "row_index", "index"):
            item = _int_or_none(args.get(key))
            if item is not None:
                break
        if item is None:
            item = len(rows) + 1
        qty = _int_or_none(args.get("qty"))
        if qty is None or qty < 1:
            qty = _int_or_none(args.get("quantity")) or 1
        mass = _int_or_none(args.get("mass_mg"))
        if mass is None:
            mass = _int_or_none(args.get("mass"))
        uid = str(entry.get("uuid") or "")
        rows.append({
            "item": int(item), "qty": int(qty),
            "part_key": _first_str(args, ("part_key", "part", "part_name", "name")) or "part.unknown",
            "material_key": _first_str(args, ("material_key", "material")) or _sheet.NA,
            "mass_mg": int(mass) if mass is not None else int(_sheet.UNMEASURED),
            "bom_row_uuid": uid or None,
        })
        if uid:
            index[int(item)] = uid
    return rows, index


def _inside(sheet_obj: Any, x: int, y: int, radius: int) -> bool:
    """序号圆（含半径）是否整颗落在图纸内。"""
    return (x - radius >= 0 and y - radius >= 0
            and x + radius <= int(sheet_obj.width_nm) and y + radius <= int(sheet_obj.height_nm))


def _arrow_curves(tip: tuple[int, int], direction: tuple[float, float], length_nm: int,
                  *, layer: str) -> list[Any]:
    """箭头两笔：自 `tip`（锚点）沿引线方向 ±25° 倒钩，长度 `length_nm`。

    `direction` = 锚点 → 圆左缘的方向（箭头因此**指向锚点**）；方向退化或长度为 0 ⇒ 不给箭头。
    """
    dx, dy = float(direction[0]), float(direction[1])
    norm = math.hypot(dx, dy)
    if norm <= 0.0 or int(length_nm) <= 0:
        return []
    ux, uy = dx / norm, dy / norm
    out: list[Any] = []
    for sign in (1.0, -1.0):
        angle = math.radians(_ARROW_HALF_ANGLE_DEG) * sign
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        rx = ux * cos_a - uy * sin_a
        ry = ux * sin_a + uy * cos_a
        barb = (int(round(tip[0] + int(length_nm) * rx)), int(round(tip[1] + int(length_nm) * ry)))
        out.append(_sheet.Curve.line(tip, barb, layer=layer, name="balloon/arrow", source=_OP_NAME))
    return out


def _leader_length_nm(center: tuple[int, int], anchor: tuple[int, int], radius: int) -> int:
    """引线真长（圆左缘点 → 锚点），整数纳米四舍五入。"""
    return int(round(math.hypot(float(center[0] - radius - anchor[0]), float(center[1] - anchor[1]))))


# --------------------------------------------------------------------------------------
# 操作：零件序号（气球）标注（BOM 行驱动 + 明细表行互链）
# --------------------------------------------------------------------------------------


def balloon_from_bom(sk_or_doc: Any, *, sheet: Any = None, items: Any = None, source_key: str = "x_bom",
                     positions_nm: Any = None, radius_nm: int = DEFAULT_RADIUS_NM,
                     leader_nm: int = DEFAULT_LEADER_NM, margin_nm: int = DEFAULT_MARGIN_NM,
                     row_gap_nm: int = DEFAULT_ROW_GAP_NM, stack_gap_nm: int = DEFAULT_STACK_GAP_NM,
                     arrow_nm: int = DEFAULT_ARROW_NM, start_item: int = 1,
                     require_bom_rows: bool = False, standard: str = DEFAULT_STANDARD,
                     language: str = DEFAULT_LANGUAGE, size: Any = None, scale: Any = None,
                     target_uuid: Any = None, layer: str = BALLOON_LAYER) -> list[str]:
    """**零件序号（气球）标注**：把 BOM 行指到图上 —— 每行一颗序号圆 + 一条引线 + 一个箭头 + 一条 annotation。

    行来源（按优先级）：显式 `items=` → 文档顶层 `source_key`（缺省 `x_bom`）→ 文档里已有的
    `kind="bom_row"` 行对象。命中第三条时，气球的 `bom_row_uuid` 直接取该行对象的 uuid。

    参数（长度整数纳米）：

    * `items`：行序列（`{qty, part_key, material_key?, mass_mg?}`）；
    * `positions_nm`：每行一个**锚点**（零件上被指的位置）；`None` ⇒ 沿图纸右留白自下而上自动排一列
      （圆心 `x = width - margin - radius`，`y = margin + radius + i*(2*radius+row_gap)`）；
      显式给锚点时，**同一锚点的多行自动堆叠**（圆心沿 +y 逐个偏移 `2*radius+stack_gap_nm`）；
    * `radius_nm` / `leader_nm` / `arrow_nm`：圆半径 / 引线长 / 箭头条长（`leader_nm=0` ⇒ 不画引线与箭头）；
    * `margin_nm` / `row_gap_nm` / `stack_gap_nm`：自动排布边距 / 行距 / 堆叠间距；
    * `start_item`：图上序号起点（顺序现编，**不采信输入里的 item**；源行号另记在 `bom_item`）；
    * `require_bom_rows`：`True` ⇒ 每颗气球都必须链到文档里真实的 `kind="bom_row"` 行对象，
      任一颗链不上就抛 `balloon_bom_mismatch`（**先校验后落笔**）。

    边界：无行来源 ⇒ `bom_source_missing`；行不合法 ⇒ `bad_bom_row`/`bad_bom_source`（`sw_drawings` 同一口径）；
    裸草图没有 `annotations[]` ⇒ `bom_needs_document`；`positions_nm` 个数 ≠ 行数 ⇒ `balloon_position_mismatch`；
    尺寸非法 ⇒ `bad_balloon_size`；个别圆越出图纸 ⇒ 跳过 + `Sheet.note()`（跳过的不进返回值）；
    整批一颗都放不下 ⇒ `balloon_off_sheet`（不返回空列表冒充成功）。

    返回：`[图纸 uuid（仅当本操作新建了图纸）] + [balloon 视图 uuid] + [每颗序号一条 annotation uuid …]`。
    视图 uuid 另外会在文档 `annotations[]` 里留一条 `kind="balloon_from_bom"` 的视图记录（无 `args`）；
    `kind="balloon"` 的序号行带 `args`（消费者按 `args` 有无区分）。
    """
    payload = _base._as_payload(sk_or_doc)
    annotations = _base._live_annotations(sk_or_doc)
    doc_rows, row_index = _rows_from_live_annotations(annotations)

    if items is not None or payload.get(str(source_key)) is not None:
        rows = _base._bom_rows(payload, items, source_key)
        basis = "args.items" if items is not None else "doc.%s" % str(source_key)
        for row in rows:
            row["bom_row_uuid"] = row_index.get(int(row["item"]))
    else:
        if not doc_rows:
            raise _fail("bom_source_missing", "no BOM source found",
                        source_key=str(source_key),
                        hint="pass items=[{qty, part_key}, ...], set that document key, "
                             "or put a kind=bom_row table on the sheet first")
        rows = doc_rows
        basis = "doc_annotations"

    radius = _base._require_nm(radius_nm, what="radius_nm", positive=True)
    leader = _non_negative(leader_nm, what="leader_nm")
    margin = _non_negative(margin_nm, what="margin_nm")
    gap = _non_negative(row_gap_nm, what="row_gap_nm")
    stack_gap = _non_negative(stack_gap_nm, what="stack_gap_nm")
    arrow = _non_negative(arrow_nm, what="arrow_nm")
    start = _base._require_count(start_item, what="start_item", minimum=1)
    strict = _base._require_flag(require_bom_rows, what="require_bom_rows")
    layer_name = str(layer or BALLOON_LAYER).strip() or BALLOON_LAYER

    # 位置：先校验个数（不落笔、不建图纸就报错）。
    explicit: list[tuple[int, int]] | None = None
    if positions_nm is not None:
        if not isinstance(positions_nm, _Seq) or isinstance(positions_nm, (str, bytes)):
            raise _fail("bad_balloon_positions", "positions_nm must be a sequence of anchor points",
                        value_type=type(positions_nm).__name__)
        if len(positions_nm) != len(rows):
            raise _fail("balloon_position_mismatch",
                        "positions_nm must carry exactly one anchor per BOM row",
                        points=len(positions_nm), rows=len(rows))
        explicit = [_base._point_nm(point, what="positions_nm[%d]" % index)
                    for index, point in enumerate(positions_nm)]

    # 互链校验：任何一颗链不上真实 BOM 行 ⇒ 先报错（此时文档还没被改动）。
    linked = sum(1 for row in rows if row.get("bom_row_uuid"))
    missing = [start + index for index, row in enumerate(rows) if not row.get("bom_row_uuid")]
    if strict and missing:
        raise _fail("balloon_bom_mismatch", "some balloons have no BOM row to point at",
                    items=missing, bom_rows=len(row_index), basis=basis,
                    hint="run bom_table/bom_row_items first or drop require_bom_rows")

    sheet_obj, created = _base._sheet_for(sk_or_doc, payload, sheet=sheet, standard=standard,
                                          language=language, size=size, scale=scale)
    target = _doc_target(payload, target_uuid, sheet_obj)

    centers: list[tuple[int, int]] = []
    anchors: list[tuple[int, int]] = []
    stack_slot: list[int] = []
    if explicit is None:
        step = 2 * radius + gap
        for index in range(len(rows)):
            cy = margin + radius + index * step
            cx = int(sheet_obj.width_nm) - margin - radius
            centers.append((cx, cy))
            anchors.append((cx - radius - leader, cy))
            stack_slot.append(0)
    else:
        groups: dict[tuple[int, int], list[int]] = {}
        order: list[tuple[int, int]] = []
        for index, point in enumerate(explicit):
            if point not in groups:
                groups[point] = []
                order.append(point)
            groups[point].append(index)
        centers = [(0, 0)] * len(rows)
        anchors = [(0, 0)] * len(rows)
        stack_slot = [0] * len(rows)
        stack_step = 2 * radius + stack_gap
        for anchor in order:
            for slot, index in enumerate(groups[anchor]):
                ax, ay = int(anchor[0]), int(anchor[1])
                centers[index] = (ax + radius + leader, ay + slot * stack_step)
                anchors[index] = (ax, ay)
                stack_slot[index] = slot

    curves: list[Any] = []
    entries: list[dict[str, Any]] = []
    placed: list[str] = []
    skipped = 0
    for index, row in enumerate(rows):
        cx, cy = centers[index]
        ax, ay = anchors[index]
        if not _inside(sheet_obj, cx, cy, radius):
            skipped += 1
            sheet_obj.note("%s: item %d at (%d, %d) nm does not fit on the sheet -- skipped "
                           "(not returned; radius_nm=%d)" % (_OP_NAME, int(row["item"]), cx, cy, radius))
            continue
        item = start + index
        row_uuid = row.get("bom_row_uuid")
        uid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                              "cadcore.sw_drawings.%s|%s|%d|%s|%d|%d"
                              % (_OP_NAME, sheet_obj.uuid, item, str(row["part_key"]), cx, cy)))
        curves.append(_sheet.Curve.circle((cx, cy), radius, layer=layer_name,
                                          name="balloon/circle", source=_OP_NAME))
        if leader > 0:
            curves.append(_sheet.Curve.line((cx - radius, cy), (ax, ay), layer=layer_name,
                                            name="balloon/leader", source=_OP_NAME))
            curves.extend(_arrow_curves((ax, ay), (float(cx - radius - ax), float(cy - ay)),
                                        arrow, layer=layer_name))
        entries.append({
            "uuid": uid, "sheet": str(sheet_obj.uuid), "drawing": str(sheet_obj.uuid),
            "op": _OP_NAME, "associative": True, "target": [target, "bom"],
            "kind": "balloon", "text_key": "bom.row", "literal": False,
            "args": {
                "item": item, "bom_item": int(row.get("item") or 0), "stack_slot": int(stack_slot[index]),
                "qty": int(row["qty"]), "part_key": str(row["part_key"]),
                "material_key": str(row["material_key"]), "mass_mg": int(row["mass_mg"]),
                "bom_row_uuid": row_uuid, "bom_row_basis": basis, "bom_rows_linked": int(linked),
                "bom_rows_available": len(row_index),
                "sheet_uuid": sheet_obj.uuid, "language": str(language or DEFAULT_LANGUAGE),
                "layer": layer_name, "radius_nm": radius, "leader_nm": leader, "arrow_nm": arrow,
                "position_nm": [int(cx), int(cy)], "anchor_nm": [int(ax), int(ay)],
                "leader_length_nm": _leader_length_nm((cx, cy), (ax, ay), radius),
            },
        })
        placed.append(uid)

    if not placed:
        raise _fail("balloon_off_sheet", "no balloon fits on this sheet",
                    rows=len(rows), skipped=skipped, radius_nm=radius, sheet_uuid=sheet_obj.uuid)

    view = _sheet.View(
        uuid=sheet_obj.next_uuid(_OP_NAME, len(placed), radius), name="balloon", at_nm=(0, 0),
        scale=(1, 1), scale_text="1:1", hidden=False, visible=tuple(curves), hidden_curves=(),
        shape=None, shape_repr="balloon-set", projection_source=_OP_NAME,
        projection_shape=_OP_NAME,
        bounds_model_nm=(0, 0, int(sheet_obj.width_nm), int(sheet_obj.height_nm)),
    )
    sheet_obj.views.append(view)
    _base._register_extra_layer(sheet_obj, layer_name, "balloon item callouts")
    for entry in entries:
        _base._register(annotations, entry)
    # 只把视图登记回文档（序号行已按 uuid 幂等自登记；再走 _publish 会把它们覆盖成通用记录）。
    _base._publish(_base._attachment_host(sk_or_doc), sheet_obj, [view.uuid], _OP_NAME)
    sheet_obj.note("%s: rows=%d placed=%d skipped=%d linked=%d/%d basis=%s radius_nm=%d "
                   "leader_nm=%d layer=%s view_uuid=%s"
                   % (_OP_NAME, len(rows), len(placed), skipped, linked, len(rows), basis,
                      radius, leader, layer_name, view.uuid))
    return ([sheet_obj.uuid] if created else []) + [view.uuid] + placed


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    _OP_NAME: balloon_from_bom,
}


def module_facts() -> dict[str, Any]:
    """本片的机读事实（族 / 文件 / 条目号 / 操作名 / 缺省值），不参与几何。"""
    return {
        "family": FAMILY,
        "module": "cadcore/ops/features/sw_drawings__w24.py",
        "seat": "sw_drawings__w24",
        "seat_number": 15,
        "gap_list_size": 9,
        "item_index_in_gap_list": 6,
        "feature": "balloon_callout_linked_to_bom_row",
        "operations": sorted(OPERATIONS),
        "layer": BALLOON_LAYER,
        "defaults_nm": {"radius": DEFAULT_RADIUS_NM, "leader": DEFAULT_LEADER_NM,
                        "row_gap": DEFAULT_ROW_GAP_NM, "stack_gap": DEFAULT_STACK_GAP_NM,
                        "arrow": DEFAULT_ARROW_NM},
    }


# --------------------------------------------------------------------------------------
# 自检（硬断言：真几何对得上 annotation；气球真链到 BOM 行对象；负例真抛错；重跑幂等）
# --------------------------------------------------------------------------------------


def _balloon_annotations(doc: Any) -> list[Mapping[str, Any]]:
    """文档里本次写下、带 `args` 的序号行（视图记录无 `args`，据此区分）。"""
    return [entry for entry in _base._live_annotations(doc)
            if isinstance(entry, Mapping) and str(entry.get("kind") or "") == "balloon"
            and isinstance(entry.get("args"), Mapping)]


def _balloon_view(sheet_obj: Any) -> Any:
    for view in reversed(list(getattr(sheet_obj, "views", []) or [])):
        if str(getattr(view, "projection_source", "") or "") == _OP_NAME:
            return view
    return None


def selftest() -> int:
    """两条以上硬断言 + 负例；任一不过 ⇒ rc=1 且末行 `verdict=FAIL`（宁可红不许假绿）。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append((name, bool(ok), str(detail)))
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + detail) if detail else ""))
        return bool(ok)

    rows = [{"qty": 2, "part_key": "part.base", "material_key": "mat.steel", "mass_mg": 1500},
            {"qty": 1, "part_key": "part.cover", "material_key": "mat.alu", "mass_mg": 400}]

    doc = _docmodel.new_document()
    _base.bom_table(doc, items=rows)                       # 真先出明细表（真 bom_row 行对象）
    payload = _base._as_payload(doc)
    sheet_obj, _created = _base._sheet_for(doc, payload, sheet=None, standard=DEFAULT_STANDARD,
                                           language=DEFAULT_LANGUAGE, size=None, scale=None)
    first = balloon_from_bom(doc, sheet=sheet_obj)         # 第三条行来源：文档里的 BOM 行对象

    ann = _balloon_annotations(doc)
    check("balloon_rows_match_bom_rows", len(ann) == len(rows),
          "balloons=%d rows=%d" % (len(ann), len(rows)))

    annotations = _base._live_annotations(doc)
    bom_uuid_kinds = {str(e.get("uuid")): str(e.get("kind"))
                      for e in annotations if isinstance(e, Mapping)}
    view_uuid = [uid for uid in first if uid in bom_uuid_kinds]
    check("returned_uuids_resolve_in_document",
          bool(view_uuid) and all(str(uid) in bom_uuid_kinds for uid in first),
          "returned=%d resolved=%d" % (len(first), len(view_uuid)))

    derived, _index = _rows_from_live_annotations(annotations)
    by_uuid = {str(row["bom_row_uuid"]): row for row in derived}
    linked_ok = len(derived) == len(rows) and bool(ann)
    for entry in ann:
        args = entry["args"]
        row_uuid = args.get("bom_row_uuid")
        linked_ok = linked_ok and bool(row_uuid) and str(row_uuid) in by_uuid \
            and bom_uuid_kinds.get(str(row_uuid)) == "bom_row" \
            and str(by_uuid[str(row_uuid)]["part_key"]) == str(args["part_key"]) \
            and int(by_uuid[str(row_uuid)]["qty"]) == int(args["qty"])
    check("balloons_link_to_real_bom_row_objects", linked_ok,
          "linked=%d basis=%s" % (sum(1 for e in ann if e["args"].get("bom_row_uuid")),
                                  ann[0]["args"].get("bom_row_basis") if ann else "NA"))

    view = _balloon_view(sheet_obj)
    visible = list(getattr(view, "visible", ()) or ()) if view is not None else []
    circles = [c for c in visible if str(getattr(c, "name", "")) == "balloon/circle"]
    leaders = [c for c in visible if str(getattr(c, "name", "")) == "balloon/leader"]
    arrows = [c for c in visible if str(getattr(c, "name", "")) == "balloon/arrow"]
    circle_ok = len(circles) == len(ann) and all(int(c.radius_nm) == DEFAULT_RADIUS_NM for c in circles) \
        and {tuple(int(v) for v in c.center) for c in circles} == {tuple(e["args"]["position_nm"]) for e in ann}
    check("geometry_circle_equals_annotation", circle_ok,
          "circles=%d radius_nm=%d" % (len(circles), DEFAULT_RADIUS_NM))

    leader_ok = len(leaders) == len(ann)
    for entry in ann:
        cx, cy = entry["args"]["position_nm"]
        anchor = tuple(entry["args"]["anchor_nm"])
        leader_ok = leader_ok and any(
            tuple(c.points[0]) == (int(cx) - DEFAULT_RADIUS_NM, int(cy)) and tuple(c.points[1]) == anchor
            and c.length_nm() == int(entry["args"]["leader_length_nm"]) for c in leaders)
    check("geometry_leader_edge_to_anchor_exact", leader_ok,
          "leaders=%d len_nm=%s" % (len(leaders), sorted(c.length_nm() for c in leaders[:3])))

    tips = [tuple(c.points[0]) for c in arrows]
    anchors = [tuple(e["args"]["anchor_nm"]) for e in ann]
    check("geometry_arrows_sit_on_anchors",
          len(arrows) == 2 * len(ann) and all(tips.count(a) == 2 for a in anchors),
          "arrows=%d anchors=%d" % (len(arrows), len(anchors)))

    # 堆叠：同一锚点的两行 ⇒ 圆心等 x、y 相差正好 2*radius+stack_gap_nm。
    doc2 = _docmodel.new_document()
    anchor = (50 * NM_PER_MM, 50 * NM_PER_MM)
    balloon_from_bom(doc2, items=rows, positions_nm=[anchor, anchor])
    stacked = sorted(tuple(e["args"]["position_nm"]) for e in _balloon_annotations(doc2))
    stack_step = 2 * DEFAULT_RADIUS_NM + DEFAULT_STACK_GAP_NM
    check("stacked_balloons_offset_exactly",
          len(stacked) == 2 and stacked[0][0] == stacked[1][0]
          and stacked[1][1] - stacked[0][1] == stack_step
          and [e["args"]["stack_slot"] for e in _balloon_annotations(doc2)] == [0, 1],
          "centers=%s step_nm=%d" % (stacked, stack_step))

    # 幂等：同样输入重跑 ⇒ 序号行 uuid 集合不变、条数不涨。
    before = sorted(str(e["uuid"]) for e in ann)
    second = balloon_from_bom(doc, sheet=sheet_obj)
    after = sorted(str(e["uuid"]) for e in _balloon_annotations(doc))
    check("rerun_is_idempotent", before == after and len(after) == len(rows)
          and sorted(str(uid) for uid in second if str(uid) in bom_uuid_kinds)[-len(rows):] == before,
          "before=%d after=%d" % (len(before), len(after)))

    # 真数据后果：文档 L1 里必须能读到气球与 BOM 行的 token。
    blob = json.dumps(_base._as_payload(doc), ensure_ascii=False, default=str)
    check("document_carries_balloon_and_bom_tokens",
          "balloon" in blob and "bom_row" in blob and "anchor_nm" in blob, "dump_chars=%d" % len(blob))

    # 负例 1：strict 模式下链不上 BOM 行 ⇒ 先报错、不落半成品。
    doc3 = _docmodel.new_document()
    try:
        balloon_from_bom(doc3, items=rows, require_bom_rows=True)
        reason = "no_error"
    except CadError as exc:
        reason = str(getattr(exc, "reason", "") or "")
    check("strict_missing_bom_row_raises", reason == "balloon_bom_mismatch", "reason=%s" % reason)

    # 负例 2：裸草图没有 annotations[] ⇒ bom_needs_document。
    try:
        balloon_from_bom({"entities": []}, items=rows)
        reason = "no_error"
    except CadError as exc:
        reason = str(getattr(exc, "reason", "") or "")
    check("bare_sketch_needs_document", reason == "bom_needs_document", "reason=%s" % reason)

    # 负例 3：锚点个数 ≠ 行数 ⇒ balloon_position_mismatch。
    try:
        balloon_from_bom(_docmodel.new_document(), items=rows, positions_nm=[anchor])
        reason = "no_error"
    except CadError as exc:
        reason = str(getattr(exc, "reason", "") or "")
    check("position_count_mismatch_raises", reason == "balloon_position_mismatch", "reason=%s" % reason)

    failed = [name for name, ok, _detail in checks if not ok]
    verdict = "PASS" if not failed else "FAIL"
    print("BALLOON_FROM_BOM_SELFTEST verdict=%s checks=%d failed=%d%s"
          % (verdict, len(checks), len(failed), (" names=" + ",".join(failed)) if failed else ""))
    print("FEATURE_OK family=%s op=%s verdict=%s" % (FAMILY, _OP_NAME, verdict))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(selftest())
