#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cadcore.ops.features.sw_drawings__w12 —— sw_drawings 族 must_have 第 3 条：**形位公差（GD&T）符号与基准**。

片模块（frozen interface）
--------------------------
`FAMILY = "sw_drawings"`；`OPERATIONS: dict[str, Callable]`，每个操作签名 `op(sk_or_doc, **kwargs) -> list[str]`
（返回 uuid 列表），长度一律**整数纳米**，错误抛 `cadcore.errors.CadError(reason=...)`。本文件**不写 `__init__.py`**。

本席负责的 must_have 条目
--------------------------
按 `docs/research/cad-parity/status/sw-drawings.json` 的清单顺序，把仍 `missing`/`partial` 的 must_have 编号 0..N-1：

    0 视图调色板拖放视图 / 投影视图            (partial)
    1 尺寸标注：导入模型尺寸 + 手工智能尺寸     (partial)
    2 尺寸公差与配合                          (partial)
    3 形位公差（GD&T）符号与基准               (missing)   <-- **本席实现这一条**
    4 表面粗糙度符号                          (partial)
    5 孔标注                                  (missing)
    6 零件序号（气球）标注                     (partial)
    7 工程图模板（.drwdot）                    (missing)
    8 打印与导出（PDF / DWG / DXF）            (partial)

（`docs/research/cad-parity/features/sw_drawings.json` 这一路径不存在，本族清单真源是同目录下
`features/sw-drawings.json`，其 `must_have` 为列表、顺序与 status 的 `items` 一致。）

这条为什么是"missing"、本文件补了什么
-------------------------------------
旧账：全码域只有 `docmodel.py:655` 的 L1 kind 白名单里出现过一次 `"gdt"` 字面量，**没有任何产生它的路径** ——
既没有形位公差框格（feature control frame）的构造，也没有基准（datum feature symbol）标注，更没有
"实测值 vs 公差带"的判定。本文件给出真实现：

1. `gdt(...)`：按调用方给的框格规格，**现算**每个框格的几何（外框 + 分隔线 + 符号图形 + 引线 + 箭头），
   位置由真实锚点决定（显式 `anchor_nm=` / 目标视图**实测**几何包围盒 / 图纸**实测**幅面尺寸），
   `⌀` 公差带、投影公差带、基准字母序（primary/secondary/tertiary）、材料状态修饰符（MMC/LMC/RFS）
   全部进 L1；框格文本（`frame_ascii`）由数据**算出来**，不是写死的常量。
2. `gdt_datum(...)`：基准要素符号（三角形 + 引出杆 + 字母框 + 引线），支持复合基准 `A-B`。
3. `gdt_evaluate(...)`：把**实测偏差**（整数纳米）与存下来的公差带比对，出 `within_tolerance`
   与 `margin_nm`；`⌀` 公差带的验收极限是**半径**（`tolerance_nm // 2`，GD&T 语义），不是直径。

L1 落点（真数据后果，按三态口径记账）
--------------------------------------
* 主落点：宿主文档的 `x_gdt` 记录（宿主是 dict/Mapping 时写进同一个 dict；是对象时写它的
  `payload`/`data`/`_payload` 映射；都不行则退到对象的 `annotations` 列表里一条 `kind="gdt"` 的
  annotation，`args` 带全量记录；再不行**显式抛** `gdt_host_unsupported`，绝不假装写成功）。
  这是本仓已有的扩展槽写法（同 `bom_table` 读的顶层 `x_bom`）。
* 图纸侧：能定位到 `Sheet`/`View` 对象时，同一批几何以 `source="gdt"`、`layer="GDT"` 的 `Curve.line`
  追加进目标视图的 `visible`（重跑先撤旧 `source="gdt"` 曲线，与 `centerline` 同口径），并登记 `GDT` 图层。
  找不到视图时**不静默**：记录里 `curve_backend="no_view"`，但 `segments`（整数纳米线段）仍在文档里，
  几何没有丢。
* 三态：`counts.segments` 等计数为 0 = 真为零；未采到的东西一律显式写 `measured: false` /
  `within_tolerance: null`（不是 0），不做"静默 0"。

诚实边界（不假装的能力）
------------------------
* 本仓图纸是 **L1 JSON**，不是 SolidWorks 原生 `.sldrsw`/`.slddrw` 二进制；本文件不写原生格式。
* 图形符号用**整数纳米折线**画（圆/圆柱类符号用八边形折线近似，`glyph_geometry="polyline_approximation"`
  写在记录里），因为 L1 线段是唯一有几何真源的落点；不是"符号字体"渲染。
* 符号图形覆盖 `GDT_SYMBOLS` 里 14 个常用符号；表外符号**抛** `unsupported_gdt_symbol`（不落近似物）。
* 公差数值**只来自调用方或文档 `gdt` 数据**；标准档没有取到正文的字段不许编数 —— 本文件因此**不设**
  "缺省公差值"：没有数据就 `gdt_frames_missing`，绝不凭空造一个公差。

错误理由（`CadError.reason`）
-----------------------------
`unsupported_gdt_symbol` · `gdt_tolerance_missing` · `bad_length` · `bad_point` · `bad_flag` · `bad_count`
· `bad_datum_letter` · `gdt_datum_duplicate` · `bad_material_condition` · `gdt_frames_missing`
· `gdt_shortfall` · `gdt_anchor_missing` · `gdt_measurement_missing` · `gdt_out_of_tolerance`(strict)
· `gdt_host_unsupported` · `bad_layer`
"""

from __future__ import annotations

import re as _re
import sys as _sys
import uuid as _uuid
from collections.abc import Mapping as _ABCMapping, MutableMapping as _ABCMutableMapping
from pathlib import Path as _Path
from typing import Any, Callable, Sequence

from cadcore.errors import CadError
from cadcore.draw import sheet as _sheet  # type: ignore

FAMILY = "sw_drawings"
#: 本席条目在"仍 missing/partial 的 must_have"里的编号（0..N-1，见模块 docstring）。
FEATURE_INDEX = 3
FEATURE = "GD&T feature control frames and datum feature symbols"
MODULE = "cadcore.ops.features.sw_drawings__w12"

__all__ = [
    "FAMILY", "FEATURE_INDEX", "FEATURE", "MODULE", "OPERATIONS", "CALL_PLANS", "MUST_HAVE_MAP",
    "GDT_SYMBOLS", "GLYPH_TOKENS", "MATERIAL_CONDITIONS", "GDT_KEY", "GDT_LAYER",
    "gdt", "gdt_datum", "gdt_evaluate",
]

#: 长度真源 = 整数纳米（与 `cadcore.draw.sheet.NM_PER_MM` 同值；本文件不依赖 sheet 可用性）。
NM_PER_MM = 1_000_000

#: 文档侧扩展槽键（与 `bom_table` 读顶层 `x_bom` 同一种写法）。
GDT_KEY = "x_gdt"
#: GD&T 几何落在图纸上的图层名（`sheet.py` 的图层表之外，本操作自己登记）。
GDT_LAYER = "GDT"

#: 制图文字高（ISO/ASME 常用 3.5 mm），框格高 = 2 倍文字高。
DEFAULT_TEXT_HEIGHT_NM = 3_500_000
DEFAULT_MARGIN_NM = 8_000_000
DEFAULT_GAP_NM = 2_000_000

#: 符号图形网格：1 格 = 文字高 / 8（整数纳米，杜绝浮点）。
GLYPH_UNIT = 8

MATERIAL_CONDITIONS: tuple[str, ...] = ("MMC", "LMC", "RFS")

GDT_SYMBOLS: tuple[str, ...] = (
    "straightness", "flatness", "circularity", "cylindricity", "perpendicularity", "parallelism",
    "angularity", "position", "concentricity", "symmetry", "profile_of_a_line",
    "profile_of_a_surface", "runout", "total_runout",
)

#: 框格内的 ASCII 符号代号（面向用户的文案走语言包，这里是工程记号本体）。
GLYPH_TOKENS: dict[str, str] = {
    "straightness": "STR", "flatness": "FLT", "circularity": "CIR", "cylindricity": "CYL",
    "perpendicularity": "PERP", "parallelism": "PAR", "angularity": "ANG", "position": "POS",
    "concentricity": "CONC", "symmetry": "SYM", "profile_of_a_line": "PROF-L",
    "profile_of_a_surface": "PROF-S", "runout": "RUN", "total_runout": "TRUN",
}


def _ring(points: Sequence[tuple[int, int]]) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    """闭合折线（首尾相接）——圆类符号的整数折线近似。"""
    return tuple((points[i], points[(i + 1) % len(points)]) for i in range(len(points)))


_OCT = ((4, 0), (3, 3), (0, 4), (-3, 3), (-4, 0), (-3, -3), (0, -4), (3, -3))
_DIA = ((2, 0), (0, 2), (-2, 0), (0, -2))
_CROSS = (((-4, 0), (4, 0)), ((0, -4), (0, 4)))

#: 符号图形：网格坐标（1 格 = 文字高/8），y 向上，图形居中于符号格。
_GLYPH_STROKES: dict[str, tuple[tuple[tuple[int, int], tuple[int, int]], ...]] = {
    "straightness": (((-4, 0), (4, 0)),),
    "flatness": (((-4, -1), (1, -1)), ((1, -1), (4, 1)), ((4, 1), (-1, 1)), ((-1, 1), (-4, -1))),
    "circularity": _ring(_OCT),
    "cylindricity": _ring(_OCT) + (((-4, 3), (4, 3)), ((-4, -3), (4, -3))),
    "perpendicularity": (((-3, -4), (-3, 4)), ((-3, -2), (3, -2))),
    "parallelism": (((-4, -3), (0, 3)), ((0, -3), (4, 3))),
    "angularity": (((-4, -3), (1, 3)), ((1, -3), (4, 2))),
    "position": _ring(_OCT) + _CROSS,
    "concentricity": _ring(_OCT) + _ring(_DIA),
    "symmetry": (((0, -4), (0, 4)), ((-4, -3), (-4, 3)), ((4, -3), (4, 3))),
    "profile_of_a_line": (((-4, -2), (-2, 3)), ((-2, 3), (0, 4)), ((0, 4), (2, 3)), ((2, 3), (4, -2))),
    "profile_of_a_surface": (((-4, -2), (-2, 3)), ((-2, 3), (0, 4)), ((0, 4), (2, 3)), ((2, 3), (4, -2)),
                             ((-4, -4), (4, -4))),
    "runout": (((-4, -3), (3, 3)), ((3, 3), (0, 3)), ((3, 3), (3, 0))),
    "total_runout": (((-4, -3), (3, 3)), ((3, 3), (0, 3)), ((3, 3), (3, 0)), ((-4, -4), (-4, -2))),
}

_UUID_RE = _re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LABEL_RE = _re.compile(r"^[A-Z](-[A-Z])?$")


# --------------------------------------------------------------------------------------
# 入参校验（口径与本族 `sw_drawings.py::_require_nm` 一致：整数纳米、非法即抛、不静默钳位）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _uid(*parts: Any) -> str:
    """确定性 uuid（同一输入两次生成逐字节相同）。"""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore:%s" % ":".join(str(p) for p in parts)))


def _require_int(value: Any, what: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length", "%s must be an integer nanometre count" % what,
                    field=what, value=repr(value), type=type(value).__name__)
    if minimum is not None and value < minimum:
        raise _fail("bad_length", "%s is below the minimum" % what,
                    field=what, value=value, minimum=minimum)
    return int(value)


def _require_flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _fail("bad_flag", "%s must be a bool" % what, field=what, value=repr(value))
    return bool(value)


def _require_count(value: Any, what: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_count", "%s must be an integer count" % what, field=what, value=repr(value))
    if value < minimum:
        raise _fail("bad_count", "%s is below the minimum" % what, field=what, value=value,
                    minimum=minimum)
    return int(value)


def _as_point(value: Any, what: str) -> tuple[int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise _fail("bad_point", "%s must be a 2-sequence of integer nanometres" % what,
                    field=what, value=repr(value))
    return (_require_int(value[0], what + ".x"), _require_int(value[1], what + ".y"))


def _require_layer(value: Any) -> str:
    layer = str(value or "").strip()
    if not layer or not layer.isascii() or len(layer) > 31 or " " in layer:
        raise _fail("bad_layer", "layer must be a non-empty ASCII token", value=repr(value))
    return layer


def _mm_text(nm: int) -> str:
    """整数纳米 → 毫米文本（四舍五入到微米，全整数运算，无浮点不确定性）。"""
    sign = "-" if nm < 0 else ""
    micro = (abs(int(nm)) + 500) // 1000
    return "%s%d.%03d" % (sign, micro // 1000, micro % 1000)


def _norm_condition(value: Any, *, where: str) -> str | None:
    if value is None:
        return None
    token = str(value).strip().upper()
    if not token:
        return None
    if token not in MATERIAL_CONDITIONS:
        raise _fail("bad_material_condition", "material condition must be one of %s"
                    % list(MATERIAL_CONDITIONS), where=where, value=repr(value))
    return token


def _require_label(value: Any, *, where: str) -> str:
    label = str(value or "").strip().upper()
    if not _LABEL_RE.match(label):
        raise _fail("bad_datum_letter", "datum label must be one A-Z letter or a compound A-B",
                    where=where, value=repr(value))
    return label


def parse_datums(raw: Any, conditions: Any = None) -> list[dict[str, Any]]:
    """基准引用序列 → 机读记录（顺序即 primary/secondary/tertiary，重复即抛）。"""
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, str):
        items = [part for part in raw.replace(",", "|").split("|") if part.strip()]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise _fail("bad_datum_letter", "datums must be a string or a sequence",
                    value=repr(raw))
    cond_map: dict[str, Any] = {}
    if isinstance(conditions, _ABCMapping):
        cond_map = {str(k).strip().upper(): v for k, v in conditions.items()}
    elif conditions is not None:
        raise _fail("bad_material_condition", "datum conditions must be a mapping",
                    value=repr(conditions))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        condition = None
        if isinstance(item, _ABCMapping):
            label_raw = item.get("letter", item.get("label"))
            condition = item.get("material_condition", item.get("condition"))
        else:
            label_raw = item
        label = _require_label(label_raw, where="datums[%d]" % index)
        if label in seen:
            raise _fail("gdt_datum_duplicate", "duplicate datum label in one frame",
                        letter=label, index=index)
        seen.add(label)
        if label in cond_map:
            condition = cond_map[label]
        precedence = ("primary", "secondary", "tertiary")[index] if index < 3 \
            else "additional_%d" % (index + 1)
        out.append({"letter": label, "material_condition": _norm_condition(condition, where=label),
                    "precedence": precedence, "index": index})
    return out


def _normalise_frame(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, _ABCMapping):
        raise _fail("gdt_frames_missing", "each frame must be a mapping", index=index,
                    value=repr(raw))
    symbol_raw = str(raw.get("symbol", raw.get("characteristic", "")) or "").strip().lower()
    if symbol_raw not in GDT_SYMBOLS:
        raise _fail("unsupported_gdt_symbol", "GD&T symbol is not in the supported set",
                    symbol=repr(raw.get("symbol")), known=list(GDT_SYMBOLS), index=index)
    if raw.get("tolerance_nm", raw.get("value_nm")) is None:
        raise _fail("gdt_tolerance_missing", "frame needs tolerance_nm=<integer nm>",
                    symbol=symbol_raw, index=index)
    tolerance = _require_int(raw.get("tolerance_nm", raw.get("value_nm")),
                             "frames[%d].tolerance_nm" % index, minimum=1)
    diameter_zone = _require_flag(bool(raw.get("diameter_zone", False)),
                                  "frames[%d].diameter_zone" % index)
    projected = raw.get("projected_zone_nm")
    frame: dict[str, Any] = {
        "index": index,
        "symbol": symbol_raw,
        "glyph_token": GLYPH_TOKENS[symbol_raw],
        "tolerance_nm": tolerance,
        "tolerance_ascii": _mm_text(tolerance),
        "diameter_zone": diameter_zone,
        "material_condition": _norm_condition(raw.get("material_condition"),
                                              where="frames[%d].tolerance" % index),
        "datum_references": parse_datums(raw.get("datums", raw.get("datum_references")),
                                         raw.get("datum_conditions")),
    }
    if projected is not None:
        frame["projected_zone_nm"] = _require_int(projected, "frames[%d].projected_zone_nm" % index,
                                                  minimum=1)
    if raw.get("at_nm") is not None:
        frame["at"] = list(_as_point(raw.get("at_nm"), "frames[%d].at_nm" % index))
    return frame


def _frame_ascii(frame: _ABCMapping[str, Any]) -> str:
    """框格文本由数据现算：符号 | ⌀公差[|P 投影带] | 基准1 | 基准2(修饰) | …"""
    body = ("DIA " if frame.get("diameter_zone") else "") + str(frame["tolerance_ascii"])
    if frame.get("material_condition"):
        body += "(%s)" % frame["material_condition"]
    parts = [str(frame["glyph_token"]), body]
    if frame.get("projected_zone_nm"):
        parts.append("P " + _mm_text(int(frame["projected_zone_nm"])))
    for ref in frame.get("datum_references", ()):
        token = str(ref["letter"])
        if ref.get("material_condition"):
            token += "(%s)" % ref["material_condition"]
        parts.append(token)
    return " | ".join(parts)


def _datum_frame_ascii(frame: _ABCMapping[str, Any]) -> str:
    return "|".join(str(ref["letter"]) +
                    ("(%s)" % ref["material_condition"] if ref.get("material_condition") else "")
                    for ref in frame.get("datum_references", ()))


# --------------------------------------------------------------------------------------
# 几何：全部整数纳米；框格 = 外框 4 段 + 分隔线 + 符号图形 + 引线 + 箭头
# --------------------------------------------------------------------------------------


def _leader(attach: tuple[int, int], anchor: tuple[int, int] | None, text_height: int
            ) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """引线（1 段）+ 箭头两撇（2 段）；anchor 与 attach 重合则不出引线。"""
    if anchor is None:
        return []
    ax, ay = int(anchor[0]), int(anchor[1])
    if (ax, ay) == attach:
        return []
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = [(attach, (ax, ay))]
    dx, dy = attach[0] - ax, attach[1] - ay
    back = max(1, text_height // 3)
    barb = max(1, back // 2)
    if abs(dx) >= abs(dy):
        sx = 1 if dx > 0 else -1
        segs.append(((ax, ay), (ax + sx * back, ay + barb)))
        segs.append(((ax, ay), (ax + sx * back, ay - barb)))
    else:
        sy = 1 if dy > 0 else -1
        segs.append(((ax, ay), (ax + barb, ay + sy * back)))
        segs.append(((ax, ay), (ax - barb, ay + sy * back)))
    return segs


def _frame_segments(frame: _ABCMapping[str, Any], origin: tuple[int, int], text_height: int,
                    anchor: tuple[int, int] | None
                    ) -> tuple[list[tuple[tuple[int, int], tuple[int, int]]], dict[str, Any]]:
    height = int(text_height)
    unit = max(1, height // GLYPH_UNIT)
    sym_w = 2 * height
    tol_chars = len(str(frame["tolerance_ascii"])) + (4 if frame.get("diameter_zone") else 0)
    tol_w = max(2 * height, (height * (tol_chars + 2) * 3) // 5)
    widths = [sym_w, tol_w]
    for ref in frame.get("datum_references", ()):
        letters = len(str(ref["letter"]))
        modifier = 4 if ref.get("material_condition") else 0
        widths.append(max(height, (height * (letters + 2 + modifier) * 3) // 5))
    total_w = sum(widths)
    box_h = 2 * height
    x0, y0 = int(origin[0]), int(origin[1])
    x1, y1 = x0 + total_w, y0 + box_h
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = [
        ((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    cursor = x0
    for width in widths[:-1]:
        cursor += width
        segs.append(((cursor, y0), (cursor, y1)))
    gx, gy = x0 + sym_w // 2, y0 + box_h // 2
    for p, q in _GLYPH_STROKES[str(frame["symbol"])]:
        segs.append(((gx + p[0] * unit, gy + p[1] * unit), (gx + q[0] * unit, gy + q[1] * unit)))
    segs.extend(_leader((x0, y0 + box_h // 2), anchor, height))
    cells: list[list[int]] = []
    cursor = x0 + sym_w
    for width in widths[1:]:
        cells.append([cursor, y0, cursor + width, y1])
        cursor += width
    meta = {"box": [x0, y0, x1, y1], "origin": [x0, y0], "compartment_count": len(widths),
            "compartment_widths": list(widths), "datum_cells": cells, "glyph_unit_nm": unit,
            "glyph_geometry": "polyline_approximation"}
    return segs, meta


def _datum_segments(label: str, origin: tuple[int, int], text_height: int,
                    anchor: tuple[int, int] | None, leader: bool
                    ) -> tuple[list[tuple[tuple[int, int], tuple[int, int]]], dict[str, Any]]:
    """基准要素符号：三角形 + 引出杆 + 字母框（+ 引线）。"""
    height = int(text_height)
    ax, ay = int(origin[0]), int(origin[1])
    half = max(1, height // 2)
    apex = (ax, ay)
    left = (ax - half, ay + height)
    right = (ax + half, ay + height)
    segs: list[tuple[tuple[int, int], tuple[int, int]]] = [(apex, left), (left, right), (right, apex)]
    stem_x = (left[0] + right[0]) // 2
    stem_top = right[1] + max(1, height // 2)
    segs.append(((stem_x, right[1]), (stem_x, stem_top)))
    box_w = max(height, (height * (len(label) + 2) * 3) // 5)
    bx0 = stem_x - box_w // 2
    bx1 = bx0 + box_w
    by0 = stem_top
    by1 = stem_top + (height * 3) // 2
    segs.extend([((bx0, by0), (bx1, by0)), ((bx1, by0), (bx1, by1)),
                 ((bx1, by1), (bx0, by1)), ((bx0, by1), (bx0, by0))])
    if leader:
        segs.extend(_leader(apex, anchor, height))
    meta = {"origin": [ax, ay], "letter_cell": [bx0, by0, bx1, by1],
            "triangle": [left[0], left[1], right[0], right[1]]}
    return segs, meta


# --------------------------------------------------------------------------------------
# 宿主适配：文档 payload / Sheet / View（找不到就显式记账，不假装）
# --------------------------------------------------------------------------------------


def _l1_payload(model: Any) -> _ABCMutableMapping[str, Any] | None:
    if isinstance(model, _ABCMutableMapping):
        return model
    for attr in ("payload", "data", "_payload", "l1", "doc"):
        value = getattr(model, attr, None)
        if isinstance(value, _ABCMutableMapping):
            return value
    return None


def _sheet_objects(host: Any) -> list[Any]:
    out: list[Any] = []
    for attr in ("sheets", "_sheets", "sheet", "_sheet"):
        value = getattr(host, attr, None)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            out.extend(value)
        else:
            out.append(value)
    return out


def _locate_view(host: Any, sheet: Any, view: Any, view_uuid: Any) -> tuple[Any, Any]:
    candidates: list[Any] = []
    if sheet is not None:
        candidates.extend(sheet if isinstance(sheet, (list, tuple)) else [sheet])
    candidates.extend(_sheet_objects(host))
    want_uuid = str(view_uuid) if view_uuid else ""
    want_name = str(view or "")
    for sheet_obj in candidates:
        views = list(getattr(sheet_obj, "views", ()) or ())
        if want_uuid:
            for item in views:
                if str(getattr(item, "uuid", "")) == want_uuid:
                    return sheet_obj, item
            continue
        for item in views:
            if str(getattr(item, "name", "")) == want_name:
                return sheet_obj, item
        if views:
            return sheet_obj, views[0]
    return (candidates[0] if candidates else None), None


def _curve_points(curve: Any) -> list[tuple[int, int]]:
    pts: list[tuple[int, int]] = []
    for attr in ("points", "vertices"):
        raw = getattr(curve, attr, None)
        if isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    pts.append((int(item[0]), int(item[1])))
    for attr in ("start", "end", "center"):
        raw = getattr(curve, attr, None)
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            pts.append((int(raw[0]), int(raw[1])))
    center = getattr(curve, "center", None)
    radius = getattr(curve, "radius_nm", None)
    if isinstance(center, (list, tuple)) and len(center) == 2 and isinstance(radius, int) \
            and not isinstance(radius, bool):
        cx, cy = int(center[0]), int(center[1])
        pts.extend([(cx - radius, cy), (cx + radius, cy), (cx, cy - radius), (cx, cy + radius)])
    return pts


def _view_bounds(view: Any) -> tuple[int, int, int, int] | None:
    xs: list[int] = []
    ys: list[int] = []
    for curve in getattr(view, "visible", ()) or ():
        for x, y in _curve_points(curve):
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _sheet_size(sheet_obj: Any, payload: Any) -> tuple[int, int] | None:
    for holder in (sheet_obj, payload):
        if holder is None:
            continue
        width = getattr(holder, "width_nm", None)
        height = getattr(holder, "height_nm", None)
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return (width, height)
        if isinstance(holder, _ABCMapping):
            raw_sheets = holder.get("sheets")
            if isinstance(raw_sheets, (list, tuple)) and raw_sheets:
                first = raw_sheets[0]
                if isinstance(first, _ABCMapping):
                    w = first.get("width_nm")
                    h = first.get("height_nm")
                    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                        return (w, h)
    return None


def _existing_record(model: Any) -> tuple[dict[str, Any], Any]:
    payload = _l1_payload(model)
    if payload is not None:
        raw = payload.get(GDT_KEY)
        if isinstance(raw, _ABCMapping):
            return dict(raw), payload
    return {}, payload


def _store(model: Any, record: dict[str, Any]) -> dict[str, Any]:
    """把记录写进文档；三条路径都不成立时显式抛错（不静默丢数据）。"""
    payload = _l1_payload(model)
    if payload is not None:
        payload[GDT_KEY] = record
        return {"home": "payload", "key": GDT_KEY}
    annotations = getattr(model, "annotations", None)
    doc_uuid = str(getattr(model, "uuid", "") or "")
    frames = record.get("frames") or []
    value = frames[0].get("tolerance_nm") if frames else 0
    if isinstance(annotations, list) and _is_uuid(doc_uuid):
        annotations.append({
            "uuid": _uid("gdt:annotation", record.get("record_key", "")),
            "target": [doc_uuid, "gdt"],
            "kind": "gdt",
            "value_nm": int(value) if isinstance(value, int) and not isinstance(value, bool) else 0,
            "text_key": "gdt.frame.record",
            "literal": False,
            "args": {"schema": record.get("schema", 1), "family": FAMILY,
                     "record": record, "units": "nm"},
        })
        return {"home": "annotations", "key": GDT_KEY}
    try:
        setattr(model, GDT_KEY, record)
    except Exception as exc:  # noqa: BLE001 - 归因要带原文
        raise _fail("gdt_host_unsupported", "cannot store the GD&T record on this host",
                    host=type(model).__name__, error=str(exc))
    if getattr(model, GDT_KEY, None) is not record:
        raise _fail("gdt_host_unsupported", "host did not retain the GD&T record",
                    host=type(model).__name__)
    return {"home": "attribute", "key": GDT_KEY}


def _attach_curves(sheet_obj: Any, view_obj: Any, segments: Sequence[Any], layer: str,
                   replace: bool) -> dict[str, Any]:
    """把几何追加进目标视图（`source="gdt"`，重跑先撤旧曲线）；无视图/无后端则如实记账。"""
    if _sheet is None:
        return {"curves": 0, "curve_uuids": [], "backend": "sheet_backend_unavailable",
                "layer_registered": False}
    if sheet_obj is None or view_obj is None:
        return {"curves": 0, "curve_uuids": [], "backend": "no_view", "layer_registered": False}
    curves = [_sheet.Curve.line(tuple(seg[0]), tuple(seg[1]), layer=layer,
                                name="gdt/seg[%d]" % index, source="gdt")
              for index, seg in enumerate(segments)]
    kept = [curve for curve in (getattr(view_obj, "visible", ()) or ())
            if not (replace and str(getattr(curve, "source", "") or "") == "gdt")]
    uuids: list[str] = []
    for index, curve in enumerate(curves):
        if hasattr(sheet_obj, "next_uuid"):
            uuids.append(str(sheet_obj.next_uuid("gdt", str(getattr(view_obj, "uuid", "")), index,
                                                 curve.name)))
        else:
            uuids.append(_uid("gdt:curve", getattr(view_obj, "uuid", ""), index, curve.name))
    view_obj.visible = tuple(kept) + tuple(curves)
    layer_registered = False
    register = getattr(_sheet, "_register_layer", None)
    if callable(register):
        try:
            register(sheet_obj, layer, "continuous", "geometric tolerance frames and datum symbols")
            layer_registered = True
        except Exception:  # noqa: BLE001 - 图层登记失败要留痕（不当成成功）
            layer_registered = False
    return {"curves": len(curves), "curve_uuids": uuids, "backend": "draw.sheet.Curve",
            "layer_registered": layer_registered}


def _anchor_for(sheet_obj: Any, view_obj: Any, payload: Any, anchor_nm: Any, at_nm: Any,
                margin_nm: int) -> tuple[tuple[int, int], str]:
    if anchor_nm is not None:
        return _as_point(anchor_nm, "anchor_nm"), "explicit_anchor"
    if at_nm is not None:
        return _as_point(at_nm, "at_nm"), "explicit_at"
    if view_obj is not None:
        box = _view_bounds(view_obj)
        if box is not None:
            return (box[2] + margin_nm, box[3] + margin_nm), "view_geometry_bounds"
    size = _sheet_size(sheet_obj, payload)
    if size is not None:
        width, height = size
        return (min(margin_nm, max(0, width - margin_nm)),
                max(margin_nm, height - margin_nm)), "sheet_size"
    raise _fail("gdt_anchor_missing",
                "no anchor available: pass anchor_nm=/at_nm= or provide a view with geometry "
                "or a sheet with a real size")


def _declared_frames(payload: Any) -> list[Any]:
    """文档侧声明的框格数据（本仓扩展槽写法：顶层 `gdt` / `x_gdt.requests` / `gdt_frames`）。"""
    if not isinstance(payload, _ABCMapping):
        return []
    raw = payload.get("gdt")
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, _ABCMapping) and isinstance(raw.get("frames"), (list, tuple)):
        return list(raw["frames"])
    existing = payload.get(GDT_KEY)
    if isinstance(existing, _ABCMapping):
        for key in ("requests", "frames"):
            value = existing.get(key)
            if isinstance(value, (list, tuple)):
                return list(value)
    value = payload.get("gdt_frames")
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _freeze(segments: Sequence[Any]) -> list[list[int]]:
    return [[int(seg[0][0]), int(seg[0][1]), int(seg[1][0]), int(seg[1][1])] for seg in segments]


# --------------------------------------------------------------------------------------
# 操作 1：形位公差框格（feature control frame）
# --------------------------------------------------------------------------------------


def gdt(sk_or_doc: Any, *, frames: Any = None, min_count: int = 1, anchor_nm: Any = None,
        at_nm: Any = None, view: Any = "front", view_uuid: Any = None, sheet: Any = None,
        text_height_nm: int = DEFAULT_TEXT_HEIGHT_NM, margin_nm: int = DEFAULT_MARGIN_NM,
        gap_nm: int = DEFAULT_GAP_NM, layer: Any = GDT_LAYER, replace: bool = True) -> list[str]:
    """**形位公差（GD&T）框格**：逐条现算框格几何 + 引线，落 L1（`x_gdt`）与目标视图（`GDT` 图层）。

    数据从哪来：`frames=` 显式给；不给就读文档侧声明的 `gdt` / `x_gdt.requests` / `gdt_frames`
    （本仓扩展槽写法，同 `bom_table` 的 `x_bom`）。两处都没有 ⇒ `CadError(reason="gdt_frames_missing")`
    —— **不设缺省公差值**（标准档没取到正文的字段不许编数）。条数少于 `min_count` ⇒ `gdt_shortfall`。

    每条 frame 的字段：`symbol`（见 `GDT_SYMBOLS`）、`tolerance_nm`（整数纳米，必填）、可选
    `diameter_zone`（⌀ 公差带）、`material_condition`(MMC/LMC/RFS)、`projected_zone_nm`、
    `datums`（`"A|B|C"` 或 `["A", {"letter":"B","material_condition":"MMC"}]`）、`datum_conditions`、
    `at_nm`（该框格自己的落点）。

    几何怎么算：锚点优先 `anchor_nm=` → `at_nm=` → 目标视图**实测**几何包围盒（右上角外侧）
    → 图纸**实测**幅面（`width_nm`/`height_nm`）；都拿不到 ⇒ `gdt_anchor_missing`。框格高 = 2×文字高，
    格宽按文本长度整数算，符号图形按 `GLYPH_UNIT` 网格缩放，引线由框格左中到锚点、带两撇箭头。

    返回：`[框格 uuid …] + [曲线 uuid …] + [记录 uuid]`（每段都是合法 uuid）。
    """
    payload = _l1_payload(sk_or_doc)
    count = _require_count(min_count, "min_count", minimum=0)
    height = _require_int(text_height_nm, "text_height_nm", minimum=1)
    margin = _require_int(margin_nm, "margin_nm", minimum=0)
    gap = _require_int(gap_nm, "gap_nm", minimum=0)
    layer_name = _require_layer(layer)
    with_replace = _require_flag(replace, "replace")

    raw_frames = list(frames) if isinstance(frames, (list, tuple)) else ([frames] if frames else [])
    if not raw_frames:
        raw_frames = _declared_frames(payload)
    if not raw_frames:
        raise _fail("gdt_frames_missing",
                    "no feature control frames: pass frames= or declare gdt/x_gdt.requests on the doc",
                    known_keys=["gdt", "%s.requests" % GDT_KEY, "gdt_frames"])
    if len(raw_frames) < count:
        raise _fail("gdt_shortfall", "fewer frames than min_count", got=len(raw_frames),
                    min_count=count)

    sheet_obj, view_obj = _locate_view(sk_or_doc, sheet, view, view_uuid)
    anchor, anchor_source = _anchor_for(sheet_obj, view_obj, payload, anchor_nm, at_nm, margin)
    box_h = 2 * height

    normalised = [_normalise_frame(raw, index) for index, raw in enumerate(raw_frames)]
    frame_records: list[dict[str, Any]] = []
    all_segments: list[Any] = []
    for index, frame in enumerate(normalised):
        origin = frame.get("at")
        if origin is None:
            origin = [anchor[0] + margin, anchor[1] - index * (box_h + gap)]
        segments, meta = _frame_segments(frame, (origin[0], origin[1]), height, anchor)
        ascii_text = _frame_ascii(frame)
        record = {
            "uuid": _uid("gdt:frame", index, ascii_text, str(getattr(view_obj, "uuid", "") or view)),
            "index": index,
            "symbol": frame["symbol"],
            "glyph_token": frame["glyph_token"],
            "tolerance_nm": frame["tolerance_nm"],
            "tolerance_ascii": frame["tolerance_ascii"],
            "diameter_zone": frame["diameter_zone"],
            "material_condition": frame["material_condition"],
            "datum_references": frame["datum_references"],
            "datum_reference_frame": _datum_frame_ascii(frame),
            "precedence_order": [ref["letter"] for ref in frame["datum_references"]],
            "frame_ascii": ascii_text,
            "segment_count": len(segments),
            "segments": _freeze(segments),
            "evaluated": False,
        }
        if "projected_zone_nm" in frame:
            record["projected_zone_nm"] = frame["projected_zone_nm"]
        record.update(meta)
        frame_records.append(record)
        all_segments.extend(segments)

    previous, _ = _existing_record(sk_or_doc)
    datums_kept = list(previous.get("datums", ())) if with_replace else []
    attachments = _attach_curves(sheet_obj, view_obj, all_segments, layer_name, with_replace)
    settled = sorted({str(frame["material_condition"]) for frame in normalised
                      if frame.get("material_condition")} |
                     {str(ref["material_condition"]) for frame in normalised
                      for ref in frame["datum_references"] if ref.get("material_condition")})
    record_key = "%s:%s:%d" % (str(getattr(view_obj, "uuid", "") or view), layer_name,
                               len(frame_records))
    document = {
        "schema": 1,
        "family": FAMILY,
        "op": "gdt",
        "generated_by": MODULE,
        "units": "nm",
        "record_key": record_key,
        "layer": layer_name,
        "text_height_nm": height,
        "margin_nm": margin,
        "gap_nm": gap,
        "view": str(getattr(view_obj, "name", "") or view),
        "view_uuid": str(getattr(view_obj, "uuid", "") or ""),
        "anchor": [anchor[0], anchor[1]],
        "anchor_source": anchor_source,
        "frames": frame_records,
        "datums": datums_kept,
        "geometric_tolerance": {
            "symbols": sorted({frame["symbol"] for frame in normalised}),
            "material_conditions": settled,
            "datum_label_set": sorted({ref["letter"] for frame in normalised
                                       for ref in frame["datum_references"]}),
            "glyph_geometry": "polyline_approximation",
            "supported_symbols": list(GDT_SYMBOLS),
        },
        "counts": {
            "frames": len(frame_records),
            "datums": len(datums_kept),
            "segments": sum(frame["segment_count"] for frame in frame_records)
                        + sum(int(datum.get("segment_count", 0)) for datum in datums_kept),
            "errors": 0,
        },
        "curve_backend": attachments["backend"],
        "layer_registered": attachments["layer_registered"],
        "curve_uuids": attachments["curve_uuids"],
        "annotations_bucket": "present" if isinstance(
            payload.get("annotations") if isinstance(payload, _ABCMapping) else None, list) else "absent",
    }
    document["home"] = _store(sk_or_doc, document)
    ids = [frame["uuid"] for frame in frame_records]
    ids.extend(attachments["curve_uuids"])
    ids.append(_uid("gdt:record", record_key))
    return ids


# --------------------------------------------------------------------------------------
# 操作 2：基准要素符号（datum feature symbol）
# --------------------------------------------------------------------------------------


def gdt_datum(sk_or_doc: Any, *, letter: Any = "A", anchor_nm: Any = None, at_nm: Any = None,
              leader: bool = True, view: Any = "front", view_uuid: Any = None, sheet: Any = None,
              text_height_nm: int = DEFAULT_TEXT_HEIGHT_NM, margin_nm: int = DEFAULT_MARGIN_NM,
              layer: Any = GDT_LAYER) -> list[str]:
    """**基准要素符号**：三角形 + 引出杆 + 字母框（+ 引线），落 `x_gdt.datums` 与目标视图 `GDT` 图层。

    `letter` 是单字母 `A`…`Z` 或复合基准 `A-B`（非法 ⇒ `bad_datum_letter`）；锚点与框格同口径
    （显式 → 视图实测几何 → 图纸实测幅面，都没有 ⇒ `gdt_anchor_missing`）。返回
    `[基准 uuid] + [曲线 uuid …] + [记录 uuid]`。重复标注同一字母**不静默覆盖**：新记录进 `datums`，
    旧记录保留在 `superseded` 里（可追溯）。
    """
    payload = _l1_payload(sk_or_doc)
    label = _require_label(letter, where="letter")
    height = _require_int(text_height_nm, "text_height_nm", minimum=1)
    margin = _require_int(margin_nm, "margin_nm", minimum=0)
    layer_name = _require_layer(layer)
    with_leader = _require_flag(leader, "leader")

    sheet_obj, view_obj = _locate_view(sk_or_doc, sheet, view, view_uuid)
    anchor, anchor_source = _anchor_for(sheet_obj, view_obj, payload, anchor_nm, at_nm, margin)
    segments, meta = _datum_segments(label, (anchor[0] + margin, anchor[1]), height, anchor,
                                     with_leader)
    datum = {
        "uuid": _uid("gdt:datum", label, anchor[0], anchor[1]),
        "letter": label,
        "compound": "-" in label,
        "text_height_nm": height,
        "anchor": [anchor[0], anchor[1]],
        "anchor_source": anchor_source,
        "segment_count": len(segments),
        "segments": _freeze(segments),
    }
    datum.update(meta)

    attachments = _attach_curves(sheet_obj, view_obj, segments, layer_name, True)
    previous, _ = _existing_record(sk_or_doc)
    datums = list(previous.get("datums", ()))
    superseded = list(previous.get("superseded", ()))
    for index, old in enumerate(datums):
        if str(old.get("letter", "")) == label:
            superseded.append(dict(old, superseded_by=datum["uuid"]))
            datums.pop(index)
            break
    document = dict(previous) if previous else {}
    document.update({
        "schema": 1,
        "family": FAMILY,
        "generated_by": MODULE,
        "units": "nm",
        "layer": layer_name,
        "view": str(getattr(view_obj, "name", "") or view),
        "view_uuid": str(getattr(view_obj, "uuid", "") or ""),
        "text_height_nm": height,
        "datums": datums + [datum],
        "superseded": superseded,
        "curve_backend": attachments["backend"],
        "layer_registered": attachments["layer_registered"],
    })
    frames = list(document.get("frames", ()))
    document["frames"] = frames
    document["counts"] = {
        "frames": len(frames),
        "datums": len(document["datums"]),
        "segments": sum(int(frame.get("segment_count", 0)) for frame in frames)
                    + sum(int(item.get("segment_count", 0)) for item in document["datums"]),
        "errors": 0,
    }
    document["home"] = _store(sk_or_doc, document)
    return [datum["uuid"]] + attachments["curve_uuids"] + [_uid("gdt:datum:record", label)]


# --------------------------------------------------------------------------------------
# 操作 3：实测偏差 vs 公差带（真数据后果：PASS/FAIL + 裕度）
# --------------------------------------------------------------------------------------


def _measurement_table(measurements: Any, frames: Sequence[_ABCMapping[str, Any]]
                       ) -> dict[str, int]:
    table: dict[str, int] = {}
    if measurements is None:
        for frame in frames:
            raw = frame.get("actual_nm")
            if raw is not None:
                table[str(frame["uuid"])] = _require_int(raw, "frame.actual_nm")
        return table
    if isinstance(measurements, _ABCMapping):
        by_uuid = {str(frame["uuid"]): frame for frame in frames}
        by_ascii = {str(frame.get("frame_ascii", "")): frame for frame in frames}
        for key, value in measurements.items():
            key_text = str(key)
            if key_text in by_uuid:
                table[key_text] = _require_int(value, "measurements[%s]" % key_text)
            elif key_text in by_ascii:
                table[str(by_ascii[key_text]["uuid"])] = _require_int(value, "measurements")
            elif key_text.isdigit() and int(key_text) < len(frames):
                table[str(frames[int(key_text)]["uuid"])] = _require_int(value, "measurements")
            else:
                raise _fail("gdt_measurement_missing", "measurement key matches no stored frame",
                            key=key_text)
        return table
    if isinstance(measurements, (list, tuple)):
        for index, item in enumerate(measurements):
            if isinstance(item, _ABCMapping):
                actual = item.get("actual_nm", item.get("deviation_nm"))
                if actual is None:
                    raise _fail("gdt_measurement_missing", "measurement entry needs actual_nm",
                                index=index)
                target = None
                for key in ("uuid", "frame_uuid", "frame_ascii", "index"):
                    if item.get(key) is not None:
                        target = str(item[key])
                        break
                if target is None:
                    if index >= len(frames):
                        raise _fail("gdt_measurement_missing", "too many measurements",
                                    index=index, frames=len(frames))
                    target = str(frames[index]["uuid"])
                match = [frame for frame in frames
                         if target in (str(frame["uuid"]), str(frame.get("frame_ascii", "")),
                                       str(frame.get("index", "")))]
                if not match:
                    raise _fail("gdt_measurement_missing", "measurement matches no stored frame",
                                target=target)
                table[str(match[0]["uuid"])] = _require_int(actual, "measurements[%d]" % index)
            else:
                if index >= len(frames):
                    raise _fail("gdt_measurement_missing", "too many measurements", index=index,
                                frames=len(frames))
                table[str(frames[index]["uuid"])] = _require_int(item, "measurements[%d]" % index)
        return table
    raise _fail("gdt_measurement_missing", "measurements must be a mapping or a sequence",
                value=repr(measurements))


def gdt_evaluate(sk_or_doc: Any, *, measurements: Any = None, strict: bool = False) -> list[str]:
    """**形位公差评定**：把实测值（整数纳米）与已存公差带比对，落 `x_gdt.evaluations`。

    验收极限的 GD&T 语义：`diameter_zone=True`（⌀ 公差带）时公差是**直径**，径向极限 = `tolerance_nm // 2`；
    否则极限 = `tolerance_nm`。逐条出 `deviation_nm` / `limit_nm` / `margin_nm` / `within_tolerance`。
    没给实测值的框格**不是 0**：`measured=False`、`actual_nm=-1`(UNMEASURED)、`within_tolerance=None`
    （三态口径），并计进 `counts.unmeasured`。`strict=True` 且存在超差 ⇒ 先落盘再抛
    `CadError(reason="gdt_out_of_tolerance")`。返回每条评定的 uuid。
    """
    record, _payload = _existing_record(sk_or_doc)
    frames = [frame for frame in record.get("frames", ()) if isinstance(frame, _ABCMapping)]
    if not frames:
        raise _fail("gdt_frames_missing", "no stored GD&T frames to evaluate", key=GDT_KEY)
    with_strict = _require_flag(strict, "strict")
    table = _measurement_table(measurements, frames)
    evaluations: list[dict[str, Any]] = []
    failing = 0
    for frame in frames:
        tolerance = _require_int(frame.get("tolerance_nm"), "frame.tolerance_nm", minimum=1)
        diameter_zone = bool(frame.get("diameter_zone"))
        limit = tolerance // 2 if diameter_zone else tolerance
        actual = table.get(str(frame["uuid"]))
        entry: dict[str, Any] = {
            "uuid": _uid("gdt:eval", frame["uuid"]),
            "frame_uuid": str(frame["uuid"]),
            "symbol": str(frame.get("symbol", "")),
            "frame_ascii": str(frame.get("frame_ascii", "")),
            "tolerance_nm": tolerance,
            "diameter_zone": diameter_zone,
            "limit_nm": limit,
            "units": "nm",
        }
        if actual is None:
            entry.update({"measured": False, "actual_nm": -1, "deviation_nm": -1, "margin_nm": -1,
                          "within_tolerance": None})
        else:
            margin = limit - abs(int(actual))
            within = margin >= 0
            failing += 0 if within else 1
            entry.update({"measured": True, "actual_nm": int(actual), "deviation_nm": int(actual),
                          "margin_nm": margin, "within_tolerance": bool(within)})
        evaluations.append(entry)
        stored = [item for item in record.get("frames", ()) if isinstance(item, MutableMapping := dict)
                  and False]  # placeholder replaced below
    # 把评定结果写回每条框格（真文档变更），unmeasured 与 measured 分开计
    updated_frames: list[Any] = []
    by_uuid = {str(entry["frame_uuid"]): entry for entry in evaluations}
    for frame in record.get("frames", ()):
        if isinstance(frame, _ABCMapping):
            entry = by_uuid.get(str(frame.get("uuid")))
            merged = dict(frame)
            if entry is not None:
                merged["evaluated"] = True
                merged["evaluation"] = {"uuid": entry["uuid"], "measured": entry["measured"],
                                        "within_tolerance": entry["within_tolerance"],
                                        "margin_nm": entry["margin_nm"], "limit_nm": entry["limit_nm"]}
            updated_frames.append(merged)
        else:
            updated_frames.append(frame)
    record["frames"] = updated_frames
    record["evaluations"] = evaluations
    record["counts"] = {
        "frames": len(updated_frames),
        "datums": len(list(record.get("datums", ()))),
        "segments": sum(int(frame.get("segment_count", 0)) for frame in updated_frames
                        if isinstance(frame, _ABCMapping))
                    + sum(int(item.get("segment_count", 0)) for item in record.get("datums", ())
                          if isinstance(item, _ABCMapping)),
        "evaluated": sum(1 for entry in evaluations if entry["measured"]),
        "unmeasured": sum(1 for entry in evaluations if not entry["measured"]),
        "out_of_tolerance": failing,
        "errors": 0,
    }
    record["family"] = FAMILY
    record["generated_by"] = MODULE
    _store(sk_or_doc, record)
    if with_strict and failing:
        raise _fail("gdt_out_of_tolerance", "measured deviations exceed the tolerance zones",
                    out_of_tolerance=failing, evaluated=len(evaluations))
    return [entry["uuid"] for entry in evaluations]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "gdt": gdt,
    "gdt_datum": gdt_datum,
    "gdt_evaluate": gdt_evaluate,
}

#: 对接开关（族门 `parity_sw_drawings_gate.py` 的约定）：声明每条 must_have 用哪些 kwargs 驱动。
#: 这里给的是**全驱动**计划（显式公差数据 + 显式锚点），门裸跑也能拿到真几何，不靠猜形参。
CALL_PLANS: dict[str, list[dict[str, Any]]] = {
    "gdt": [{
        "min_count": 2,
        "anchor_nm": [120_000_000, 200_000_000],
        "frames": [
            {"symbol": "position", "tolerance_nm": 50_000, "diameter_zone": True,
             "datums": ["A", "B", "C"], "datum_conditions": {"B": "MMC"}},
            {"symbol": "flatness", "tolerance_nm": 20_000},
        ],
    }],
    "gdt_datum": [{"letter": "A", "anchor_nm": [120_000_000, 200_000_000]}],
    "gdt_evaluate": [{"measurements": [20_000], "strict": False}],
}

#: must_have key → 本模块的操作名（族门按此把条目挂到真操作上，不再判 partial）。
MUST_HAVE_MAP: dict[str, str] = {
    "gdt": "gdt",
    "geometric_tolerance": "gdt",
    "gdt_datums": "gdt_datum",
    "datum": "gdt_datum",
    "gdt_evaluate": "gdt_evaluate",
}


# --------------------------------------------------------------------------------------
# 自检（≥2 条硬断言；含负证：坏输入必须抛对应 reason，证明尺子看得见红）
# --------------------------------------------------------------------------------------


_CHECKED: list[str] = []


def _check(condition: Any, label: str) -> None:
    if not condition:
        raise AssertionError("selftest check failed: %s" % label)
    _CHECKED.append(label)


def _fixture() -> dict[str, Any]:
    return {"schema": 1, "unit": "mm", "uuid": "5f2b1c00-0000-4000-8000-0000000000w1".replace("w", "a"),
            "sheets": [], "annotations": []}


def _selftest() -> int:
    doc = _fixture()
    frames = [
        {"symbol": "position", "tolerance_nm": 50_000, "diameter_zone": True,
         "datums": ["A", "B", "C"], "datum_conditions": {"B": "MMC"}},
        {"symbol": "flatness", "tolerance_nm": 20_000},
    ]
    ids = gdt(doc, frames=frames, min_count=2, anchor_nm=(120_000_000, 200_000_000))
    record = doc[GDT_KEY]
    _check(len(ids) >= 3 and all(_is_uuid(item) for item in ids), "returns_parseable_uuids")
    _check(record["counts"]["frames"] == 2, "frames_written=2")
    first, second = record["frames"][0], record["frames"][1]
    _check(first["tolerance_nm"] == 50_000 and isinstance(first["tolerance_nm"], int),
           "tolerance_is_integer_nm")
    _check(first["frame_ascii"] == "POS | DIA 0.050 | A | B(MMC) | C",
           "frame_ascii_computed_from_data")
    _check(first["datum_references"][0]["precedence"] == "primary"
           and first["datum_references"][1]["material_condition"] == "MMC"
           and first["datum_reference_frame"] == "A|B(MMC)|C", "datum_order_and_modifier")
    _check(record["counts"]["segments"] == first["segment_count"] + second["segment_count"]
           + sum(int(item["segment_count"]) for item in record["datums"]),
           "segment_count_is_sum_of_parts")
    _check(first["segment_count"] != second["segment_count"], "glyphs_differ_in_geometry")
    _check(all(isinstance(coord, int) for seg in first["segments"] for coord in seg)
           and first["segments"][0] == [first["box"][0], first["box"][1], first["box"][2],
                                        first["box"][1]], "geometry_is_integer_nm_frame_edges")
    _check(record["geometric_tolerance"]["symbols"] == ["flatness", "position"]
           and record["geometric_tolerance"]["material_conditions"] == ["MMC"],
           "record_carries_gdt_and_datum_tokens")
    _check(record["anchor_source"] == "explicit_anchor"
           and record["anchor"] == [120_000_000, 200_000_000], "anchor_traceable")

    # 负证：坏输入必须抛对应 reason（不许静默出近似物）
    for bad, reason in (
            ({"symbol": "wobble", "tolerance_nm": 1_000}, "unsupported_gdt_symbol"),
            ({"symbol": "flatness"}, "gdt_tolerance_missing"),
            ({"symbol": "flatness", "tolerance_nm": 1_000, "datums": ["A", "A"]},
             "gdt_datum_duplicate"),
            ({"symbol": "flatness", "tolerance_nm": 1_000, "datums": ["AB"]}, "bad_datum_letter"),
            ({"symbol": "flatness", "tolerance_nm": 1_000, "datums": ["A"],
              "datum_conditions": {"A": "HUGE"}}, "bad_material_condition"),
            ({"symbol": "flatness", "tolerance_nm": 1.5}, "bad_length")):
        try:
            gdt({"uuid": doc["uuid"]}, frames=[bad], anchor_nm=(1_000, 1_000))
        except CadError as exc:
            _check(exc.reason == reason, "negative_control:%s" % reason)
        else:
            raise AssertionError("no CadError raised for %r" % (bad,))
    try:
        gdt({"uuid": doc["uuid"]}, frames=frames, anchor_nm=(0, 0), min_count=5)
    except CadError as exc:
        _check(exc.reason == "gdt_shortfall", "negative_control:gdt_shortfall")
    else:
        raise AssertionError("min_count not enforced")
    try:
        gdt({"uuid": doc["uuid"]}, frames=None)
    except CadError as exc:
        _check(exc.reason == "gdt_frames_missing", "negative_control:gdt_frames_missing")
    else:
        raise AssertionError("missing frames silently accepted")
    try:
        gdt({}, frames=frames)
    except CadError as exc:
        _check(exc.reason == "gdt_anchor_missing", "negative_control:gdt_anchor_missing")
    else:
        raise AssertionError("fabricated an anchor")

    # 基准符号：真几何 + 复合基准 + 重复标注留 superseded
    datum_ids = gdt_datum(doc, letter="A-B", anchor_nm=(120_000_000, 190_000_000))
    _check(len(datum_ids) >= 2 and all(_is_uuid(item) for item in datum_ids),
           "datum_returns_parseable_uuids")
    datums = doc[GDT_KEY]["datums"]
    _check(len(datums) == 1 and datums[0]["letter"] == "A-B" and datums[0]["compound"] is True
           and datums[0]["segment_count"] == 11, "compound_datum_symbol_geometry")
    gdt_datum(doc, letter="A-B", anchor_nm=(120_000_000, 190_000_000))
    _check(len(doc[GDT_KEY]["superseded"]) == 1, "duplicate_datum_is_superseded_not_lost")

    # 评定：⌀ 公差带按半径判（50 um 公差 -> 25 um 极限），三态记账
    passed = gdt_evaluate(doc, measurements=[20_000])
    evaluations = doc[GDT_KEY]["evaluations"]
    _check(len(passed) == 2 and evaluations[0]["limit_nm"] == 25_000
           and evaluations[0]["within_tolerance"] is True and evaluations[0]["margin_nm"] == 5_000,
           "diameter_zone_uses_radial_limit")
    _check(evaluations[1]["measured"] is False and evaluations[1]["within_tolerance"] is None
           and evaluations[1]["actual_nm"] == -1, "unmeasured_is_not_zero")
    gdt_evaluate(doc, measurements=[30_000])
    _check(doc[GDT_KEY]["evaluations"][0]["within_tolerance"] is False
           and doc[GDT_KEY]["evaluations"][0]["margin_nm"] == -5_000, "out_of_tolerance_detected")
    try:
        gdt_evaluate(doc, measurements=[30_000], strict=True)
    except CadError as exc:
        _check(exc.reason == "gdt_out_of_tolerance", "negative_control:gdt_out_of_tolerance")
    else:
        raise AssertionError("strict=True did not raise")

    # 确定性：同输入两次逐字节相同（uuid5 + 整数几何）
    left, right = _fixture(), _fixture()
    gdt(left, frames=frames, anchor_nm=(120_000_000, 200_000_000))
    gdt(right, frames=frames, anchor_nm=(120_000_000, 200_000_000))
    import json as _json
    _check(_json.dumps(left[GDT_KEY], sort_keys=True) == _json.dumps(right[GDT_KEY], sort_keys=True),
           "deterministic_second_run")

    # 文档侧声明数据也能驱动（gate 用 min_count=1 裸调时的路径）
    declared = _fixture()
    declared["gdt"] = [{"symbol": "perpendicularity", "tolerance_nm": 100_000, "datums": ["A"]}]
    declared_ids = gdt(declared, min_count=1, anchor_nm=(10_000_000, 10_000_000))
    _check(len(declared_ids) >= 2
           and declared[GDT_KEY]["frames"][0]["glyph_token"] == "PERP",
           "declared_doc_frames_drive_op")
    return len(_CHECKED)


if __name__ == "__main__":
    _count = _selftest()
    print("SELFTEST family=%s op=gdt checks=%d verdict=PASS" % (FAMILY, _count))
    print("FEATURE_OK family=%s op=gdt verdict=PASS" % FAMILY)
