#!/usr/bin/env python3
"""SW-DRAWINGS-EXTRA 族模块：装配图信息闭环的三条补全操作（零件序号 / BOM 明细表行项 / 打印导出）。

族接口（冻结，与 `cadcore.ops.sw_drawings` 同一套口径）：

    FAMILY = "sw_drawings_extra"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]
    第一个位置参一律叫 `sk_or_doc`，其余参数一律关键字；返回值 = **本次新建对象**的 uuid 列表；
    长度一律整数纳米；错误一律 `cadcore.errors.CadError(reason=...)`。

为什么是新文件而不是改 `sw_drawings.py`
--------------------------------------
`sw_drawings.py` 的 8 条操作（三视图 / 轴测 / 剖视 / 局部视图 / 尺寸 / BOM 表 / 比例标题栏 / 装配）已落盘，
本文件按族约定补齐该族仍缺的三条装配图能力：`balloon`（零件序号）、`bom_row_items`（明细表行项）、
`print_svg_text`（打印导出：SVG 文本 + 真 PDF）。**只 import 已存在的名字**（`cadcore.errors` /
`cadcore.draw.sheet` / `cadcore.ops.sw_drawings` / `cadcore.sketch.primitives`），不复制它们的内部逻辑，
也不另立一套长度或三态口径。

三条操作各做什么（都是真对象上的真动作，不做"看起来像"）
------------------------------------------------------
1. `balloon`：BOM 行 → 图上气球序号。逐行造 ① 一个圆（半径 `radius_nm`）② 一条引线（圆边 → 锚点，
   锚点在圆的 `-x` 方向 `leader_nm` 处），并把该行写成 `kind="balloon"` 的 annotation（`args` 里带
   行号 / 数量 / 零件 / 材料 / 质量 / 圆心 / 锚点 / 半径 / 引线长），框线整体落一个 `balloon` 视图；
   圆心缺省按右留白**自动排一列**，也可由 `positions_nm` 逐行指定。
2. `bom_row_items`：明细表**行项**（不含表框）。逐行做**同零件同材料合并**（数量求和、质量按"全采才求和，
   有一行未采则整组记 `-1`"），行号从 `start_item` 顺序现编、**不采信输入里的 item**；每行落一条
   `kind="bom_row"` 的 annotation —— 文本真源在文档 `annotations[]`，渲染面另有其人。
3. `print_svg_text`：把图纸导出成 **SVG 文本 + 真 PDF**。SVG 文本由 `draw.sheet` 的唯一写出器渲染，
   写进 `kind="svg_print"` 的 annotation `args["svg_text"]`；**PDF 由本文件 `render_pdf()` 现算**
   （纯标准库：`%PDF-1.4` 头 + 对象表 + 内容流 + xref + trailer，实体 → 线段/圆弧路径/文字操作），
   载荷与 `sha256`/`bytes`/`pages`/实体数一起落在同一条 annotation 的 `pdf_*` 键上（机读可取）。
   `out=` 给了就**同时落盘**并登记 `ExportResult`；没视图也**不冒充有内容** —— 记 `views=0` 并
   `Sheet.note()` 留痕。**DWG 是外部欠账**（见下）。

返回值与文档落地（与 `sw_drawings` 同一套 `_publish` 口径）
----------------------------------------------------------
返回的**图纸 uuid 落 `sheets[]`**（不在 `annotations[]` 里），其余 uuid 落 `annotations[]`；
视图会被 `_publish` 登记成一条 `kind=<操作名>` 的**视图记录**（无 `args`）—— 因此 `balloon` 的
`annotations[]` 里既有"序号行"（有 `args`）也有"视图记录"（无 `args`），消费者按 `args` 有无区分。
裸草图没有文档可落 ⇒ `_publish` 不登记（返回的 uuid 只在调用方手里，**不假装落过**）。

已知边界（显式抛错/留痕，不静默近似）
------------------------------------
* 行来源：`items=` 优先，否则读文档顶层 `source_key`（缺省 `x_bom`）；空/缺 ⇒ `bom_source_missing`；
  行不合法 ⇒ `bad_bom_row`；裸草图没有 `annotations[]` ⇒ `bom_needs_document`（与 BOM 表同一批 reason）。
* `positions_nm` 个数 ≠ 行数 ⇒ `balloon_position_mismatch`；`target_uuid` 不是 uuid ⇒ `bad_target_uuid`；
  序号圆落在图纸外 ⇒ **跳过并 `Sheet.note()`**（跳过的不进返回值），整批一个都放不下 ⇒ `balloon_off_sheet`。
* 裸草图 + `out=None` 导出 ⇒ `print_needs_document`（没有文档就没有承载导出载荷的 annotation）；
  渲染结果不是完整 SVG 文档 ⇒ `svg_render_failed`（**不**返回半张图）。
* 导出格式：`export_format="dwg"` ⇒ `export_format_external`；`"dxf"` 但没有 `out=` ⇒ `export_needs_out`；
  未知格式/非字符串 ⇒ `bad_export_format`（**不静默回落**成 SVG）。
* **外部欠账（external）**：**DWG** 需要外部转换器（ODA/Teigha 之类），本模块纯标准库、不引第三方库
  ⇒ 一律抛 `export_format_external` 并记进 `module_facts()["external_debt"]`，**不拿 PDF/SVG 冒充 DWG**。
  PDF 已由本文件的纯标准库写出器**真产出**（不再是"后端缺席即降级"）。

三态口径（与 AGENTS.md E2.3 一致）：`0` = 真为零；`-1`（`sheet.UNMEASURED`）= 未采；
`"NA"`（`sheet.NA`）= 不适用/数据未核标。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/sw_drawings_extra.py
    ⇒ 末行 `EXTRA_OPS verdict=PASS ops=3 …`
    python3 …/sw_drawings_extra.py --pdf proof.pdf
    ⇒ `PDF_PROOF … head=%PDF-1.4 …`（真落盘到当前目录，可 `head -c 8` 复验；
      示例一律用**相对路径**，不写死 POSIX 绝对临时路径 —— 三平台通用，G7 门不报 `posix_paths`）

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import base64
import hashlib
import math
import os
import sys
import tempfile
import uuid as _uuid
from collections.abc import Sequence as _Seq
from pathlib import Path
from typing import Any, Callable, Mapping

from ..errors import CadError
from ..draw import sheet as _sheet
from ..sketch import primitives as _primitives
from . import sw_drawings as _base

__all__ = [
    "FAMILY", "OPERATIONS", "BALLOON_LAYER", "SHEET_RECORD",
    "DEFAULT_RADIUS_NM", "DEFAULT_LEADER_NM", "DEFAULT_ROW_GAP_NM",
    "PDF_MAGIC", "PDF_WRITER", "NATIVE_FORMATS", "EXTERNAL_FORMATS",
    "balloon", "bom_row_items", "print_svg_text", "render_pdf", "write_pdf", "pdf_facts",
    "module_facts", "selftest",
]

#: 族 id（冻结接口）。
FAMILY = "sw_drawings_extra"

NM_PER_MM = _base.NM_PER_MM

#: 缺省值（与 `sw_drawings` 同源：标准档/语言/幅面/比例/留白一律从那边取，不另立一套）。
DEFAULT_STANDARD = _base.DEFAULT_STANDARD
DEFAULT_LANGUAGE = _base.DEFAULT_LANGUAGE
DEFAULT_SIZE = _base.DEFAULT_SIZE
DEFAULT_SCALE = _base.DEFAULT_SCALE
DEFAULT_MARGIN_NM = _base.DEFAULT_MARGIN_NM
DEFAULT_RADIUS_NM = 6 * NM_PER_MM
DEFAULT_LEADER_NM = 25 * NM_PER_MM
DEFAULT_ROW_GAP_NM = 4 * NM_PER_MM

#: 序号圆/引线的图层（`draw.sheet` 的可见层之外另立一层，渲染时可按层开关）。
BALLOON_LAYER = "BALLOON"

#: annotation `args` 里承载 SVG 原文的键名（消费者只认这一个键，别去解析别处）。
#: 历史名保留；现在同一条记录的 `pdf_*` 键还承载**真 PDF 载荷**（见 `render_pdf`）。
SHEET_RECORD = "svg_print"

#: 纯标准库 PDF 写出器的魔数与写出器标识（判定行/探针只认这两个字符串）。
PDF_MAGIC = "%PDF-1.4"
PDF_WRITER = "builtin_pdf:sw_drawings_extra.render_pdf"

#: 本族**原生**支持的导出格式（真产物）；`dwg` 记外部欠账，绝不静默降级冒充。
NATIVE_FORMATS = ("svg", "pdf")
EXTERNAL_FORMATS = ("dwg",)
SUPPORTED_FORMATS = NATIVE_FORMATS + ("dxf",)


# --------------------------------------------------------------------------------------
# 小工具（校验/登记一律复用 `sw_drawings` 的同一份实现，避免两处口径）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII，面向用户的串走语言包）。"""
    return CadError(str(message), reason=str(reason), **details)


def _flag(value: Any, *, what: str) -> bool:
    """bool 入参校验：`0/1` 不算，`True/False` 才算（不替调用方猜）。"""
    if not isinstance(value, bool):
        raise _fail("bad_flag", "flag must be a bool", field=what, value_type=type(value).__name__)
    return value


def _target(payload: Mapping[str, Any], target_uuid: Any, sheet_obj: Any, tag: str) -> str:
    """annotation 的引用目标：显式 uuid → 文档 `meta.doc_uuid` → 由图纸 uuid 派生的确定性 uuid5。"""
    if target_uuid is not None:
        text = str(target_uuid)
        if not _base._UUID_RE.match(text):
            raise _fail("bad_target_uuid", "target_uuid must be a uuid string", value=text)
        return text
    doc = _base._doc_uuid(payload)
    if doc:
        return doc
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                           "cadcore.sw_drawings_extra.%s.target|%s" % (tag, sheet_obj.uuid)))


def _positions_checked(positions_nm: Any, count: int) -> list[tuple[int, int]]:
    """显式气球位置：必须是"每行一个 2 元整数纳米点"，个数不符直接抛错（不补齐、不截断）。"""
    if not isinstance(positions_nm, _Seq) or isinstance(positions_nm, (str, bytes)):
        raise _fail("bad_balloon_positions", "positions_nm must be a sequence of 2-element points",
                    value_type=type(positions_nm).__name__)
    if len(positions_nm) != count:
        raise _fail("balloon_position_mismatch",
                    "positions_nm must carry exactly one point per BOM row",
                    points=len(positions_nm), rows=count)
    return [_base._point_nm(point, what="positions_nm[%d]" % index)
            for index, point in enumerate(positions_nm)]


def _auto_positions(sheet_obj: Any, count: int, radius: int, margin: int, gap: int) -> list[tuple[int, int]]:
    """缺省排布：沿图纸右留白自下而上排一列（圆心离边 = `margin + radius`）。"""
    x = int(sheet_obj.width_nm) - int(margin) - int(radius)
    step = 2 * int(radius) + int(gap)
    return [(x, int(margin) + int(radius) + index * step) for index in range(count)]


def _inside(sheet_obj: Any, x: int, y: int, radius: int) -> bool:
    """序号圆（含半径）是否整颗落在图纸内。"""
    return (x - radius >= 0 and y - radius >= 0
            and x + radius <= int(sheet_obj.width_nm) and y + radius <= int(sheet_obj.height_nm))


def _merge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同零件同材料合并：数量求和；质量"全采才求和，有一行未采则整组 `-1`（未采）"。"""
    order: list[tuple[str, str]] = []
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["part_key"]), str(row["material_key"]))
        if key not in acc:
            order.append(key)
            acc[key] = {"part_key": key[0], "material_key": key[1], "qty": 0,
                        "mass_measured_mg": 0, "mass_unmeasured_rows": 0, "source_rows": 0}
        bucket = acc[key]
        bucket["qty"] = int(bucket["qty"]) + int(row["qty"])
        bucket["source_rows"] = int(bucket["source_rows"]) + 1
        if int(row["mass_mg"]) < 0:
            bucket["mass_unmeasured_rows"] = int(bucket["mass_unmeasured_rows"]) + 1
        else:
            bucket["mass_measured_mg"] = int(bucket["mass_measured_mg"]) + int(row["mass_mg"])
    out: list[dict[str, Any]] = []
    for key in order:
        bucket = dict(acc[key])
        bucket["mass_mg"] = (_sheet.UNMEASURED if int(bucket["mass_unmeasured_rows"]) > 0
                             else int(bucket["mass_measured_mg"]))
        bucket.pop("mass_measured_mg")
        out.append(bucket)
    return out


# --------------------------------------------------------------------------------------
# 纯标准库 PDF 写出器（A11 export 的真产物路径）
#   最小矢量 PDF = 头 + 对象表（Catalog/Pages/Page/Contents/Font）+ 内容流 + xref + trailer。
#   坐标：图纸空间整数纳米 → PDF 用户单位（点，1/72 英寸）；图纸 y 轴向上，与 PDF 同向，不翻转。
# --------------------------------------------------------------------------------------

#: 1 纳米 = 多少点（PDF 用户单位）：nm → mm → 英寸 → 点。
_PDF_PT_PER_NM = 72.0 / (25.4 * NM_PER_MM)
#: 线宽（0.35 mm，工程图细实线的常见档）。
_PDF_LINE_WIDTH_NM = 350_000
#: 完整圆的四段三次贝塞尔控制点系数（k = 4/3 * tan(pi/8)）。
_PDF_KAPPA = 0.5522847498307936


def _pdf_num(nm: Any) -> str:
    """纳米 → PDF 点文本（4 位小数，去掉尾零）。"""
    try:
        value = float(nm) * _PDF_PT_PER_NM
    except (TypeError, ValueError):
        value = 0.0
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _pdf_escape(text: Any) -> str:
    """PDF 字面串转义；base-14 Helvetica + WinAnsi 编不了的字（如中文）替换成 `?`（不假装编得出）。"""
    body = str(text).encode("latin-1", "replace").decode("latin-1")
    return body.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf_pair(value: Any) -> tuple[float, float] | None:
    """任意"点"表示 → `(x, y)` 浮点；认 2 元序列与带 `x`/`y` 的映射。"""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    if isinstance(value, Mapping):
        try:
            return (float(value["x"]), float(value["y"]))
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _pdf_first_pair(entity: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[float, float] | None:
    for key in keys:
        pair = _pdf_pair(entity.get(key))
        if pair is not None:
            return pair
    return None


def _pdf_points(entity: Mapping[str, Any]) -> list[tuple[float, float]]:
    """折线点串：认 `points` / `points_nm` 两种键名。"""
    raw = entity.get("points") or entity.get("points_nm") or []
    out: list[tuple[float, float]] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            pair = _pdf_pair(item)
            if pair is not None:
                out.append(pair)
    return out


def _pdf_line_points(entity: Mapping[str, Any]) -> list[tuple[float, float]]:
    for left, right in (("p1", "p2"), ("start", "end"), ("a", "b"), ("from", "to")):
        p1 = _pdf_pair(entity.get(left))
        p2 = _pdf_pair(entity.get(right))
        if p1 is not None and p2 is not None:
            return [p1, p2]
    points = _pdf_points(entity)
    return [points[0], points[-1]] if len(points) >= 2 else []


def _pdf_center_radius(entity: Mapping[str, Any]) -> tuple[tuple[float, float], float] | None:
    center = _pdf_first_pair(entity, ("center", "center_nm", "centre", "c"))
    radius = entity.get("radius_nm", entity.get("radius"))
    if center is None or radius is None:
        return None
    try:
        value = float(radius)
    except (TypeError, ValueError):
        return None
    return (center, value) if value > 0 else None


def _pdf_polyline_ops(points: list[tuple[float, float]], *, closed: bool) -> list[str]:
    ops: list[str] = []
    for index, (x, y) in enumerate(points):
        ops.append("%s %s %s" % (_pdf_num(x), _pdf_num(y), "m" if index == 0 else "l"))
    if closed:
        ops.append("h")
    ops.append("S")
    return ops


def _pdf_circle_ops(center: tuple[float, float], radius: float) -> list[str]:
    """整圆 → 4 段三次贝塞尔（PDF 原生没有 circle 操作符）。"""
    cx, cy = center
    k = _PDF_KAPPA * radius
    quadrants = (
        ((cx + radius, cy + k), (cx + k, cy + radius), (cx, cy + radius)),
        ((cx - k, cy + radius), (cx - radius, cy + k), (cx - radius, cy)),
        ((cx - radius, cy - k), (cx - k, cy - radius), (cx, cy - radius)),
        ((cx + k, cy - radius), (cx + radius, cy - k), (cx + radius, cy)),
    )
    ops = ["%s %s m" % (_pdf_num(cx + radius), _pdf_num(cy))]
    for c1, c2, end in quadrants:
        ops.append("%s %s %s %s %s %s c" % (_pdf_num(c1[0]), _pdf_num(c1[1]),
                                            _pdf_num(c2[0]), _pdf_num(c2[1]),
                                            _pdf_num(end[0]), _pdf_num(end[1])))
    ops.extend(("h", "S"))
    return ops


def _pdf_arc_ops(center: tuple[float, float], radius: float, start_deg: Any, end_deg: Any) -> list[str]:
    """圆弧 → 折线（每段 ≤10°，矢量保真，不假装是真曲线）。"""
    try:
        start = float(start_deg or 0.0)
        end = float(end_deg or 0.0)
    except (TypeError, ValueError):
        return []
    sweep = (end - start) % 360.0
    if sweep <= 0.0:
        sweep = 360.0
    steps = max(2, int(math.ceil(sweep / 10.0)))
    ops: list[str] = []
    for index in range(steps + 1):
        angle = math.radians(start + sweep * index / steps)
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        ops.append("%s %s %s" % (_pdf_num(x), _pdf_num(y), "m" if index == 0 else "l"))
    ops.append("S")
    return ops


def _pdf_entity_ops(entity: Mapping[str, Any]) -> tuple[list[str], str]:
    """一条图纸实体 → (PDF 内容流操作, 归类)；归类 ∈ path / text / skip（skip 也记账，不静默丢）。"""
    kind = str(entity.get("type") or entity.get("kind") or "").upper()
    try:
        if kind == "LINE":
            points = _pdf_line_points(entity)
            if len(points) >= 2:
                return _pdf_polyline_ops(points, closed=False), "path"
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            points = _pdf_points(entity)
            if len(points) >= 2:
                return _pdf_polyline_ops(points, closed=bool(entity.get("closed"))), "path"
        elif kind in ("CIRCLE", "ARC"):
            found = _pdf_center_radius(entity)
            if found is not None:
                if kind == "CIRCLE":
                    return _pdf_circle_ops(found[0], found[1]), "path"
                arc = _pdf_arc_ops(found[0], found[1], entity.get("start_deg"), entity.get("end_deg"))
                if arc:
                    return arc, "path"
        elif kind in ("TEXT", "DIMENSION"):
            at = _pdf_first_pair(entity, ("at", "at_nm", "position"))
            text = entity.get("text")
            if at is not None and text not in (None, ""):
                height = entity.get("height_nm") or entity.get("height") or 3.5 * NM_PER_MM
                return (["BT /F1 %s Tf %s %s Td (%s) Tj ET"
                         % (_pdf_num(height), _pdf_num(at[0]), _pdf_num(at[1]), _pdf_escape(text))], "text")
    except Exception:  # noqa: BLE001 - 单条实体画不出 ⇒ 记 skip（计数进 record/facts），不拖垮整页
        return [], "skip"
    return [], "skip"


def _pdf_parts(sheet_obj: Any) -> dict[str, Any]:
    """内容流 + 逐条实体记账（实体数↔路径数/文字数/skip 数三者必须对得上）。"""
    entities = list(_sheet.iter_entities(sheet_obj))
    lines = ["q", "%s w" % _pdf_num(_PDF_LINE_WIDTH_NM)]
    paths = texts = skipped = 0
    for entity in entities:
        ops, bucket = _pdf_entity_ops(entity if isinstance(entity, Mapping) else {})
        if bucket == "path":
            paths += 1
        elif bucket == "text":
            texts += 1
        else:
            skipped += 1
        lines.extend(ops)
    lines.append("Q")
    stream = ("\n".join(lines) + "\n").encode("latin-1", "replace")
    return {"entities": len(entities), "paths": paths, "texts": texts, "skipped": skipped,
            "stream": stream, "pages": 1,
            "media_box_nm": [int(sheet_obj.width_nm), int(sheet_obj.height_nm)]}


def render_pdf(sheet_obj: Any, *, parts: Mapping[str, Any] | None = None) -> bytes:
    """**纯标准库 PDF 写出器**：图纸实体 → 完整 PDF 字节（零第三方依赖，确定性输出）。

    结构（PDF 1.4）：`%PDF-1.4` 头 + 二进制标记行 + 5 个对象（Catalog / Pages / Page / Contents / Font）
    + `xref` 表 + `trailer` + `%%EOF`；页面 `MediaBox` 按图纸物理尺寸（纳米 → 点）现算，
    内容流是线段/贝塞尔圆/折线弧/文字操作。同一张图纸两次调用**逐字节相同**（无时间戳、无随机量）。
    """
    facts = _pdf_parts(sheet_obj) if parts is None else parts
    stream = bytes(facts["stream"])
    width_nm, height_nm = facts["media_box_nm"]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
         "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
         % (_pdf_num(width_nm), _pdf_num(height_nm))).encode("latin-1"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    blob = bytearray(PDF_MAGIC.encode("ascii") + b"\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(blob))
        blob += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref_at = len(blob)
    blob += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        blob += b"%010d 00000 n \n" % offset
    blob += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
             % (len(objects) + 1, xref_at))
    return bytes(blob)


def write_pdf(sheet_obj: Any, out: Any) -> str:
    """把图纸写成 `.pdf` 文件（扩展名强制 `.pdf`），返回实际落盘路径。"""
    target = Path(_sheet._with_extension(str(out), "pdf"))
    if str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(render_pdf(sheet_obj))
    return str(target)


def pdf_facts(sheet_obj: Any) -> dict[str, Any]:
    """PDF 写出器的机读事实（判定行/探针可直接读，不用解析字节）。"""
    facts = _pdf_parts(sheet_obj)
    return {"writer": PDF_WRITER, "magic": PDF_MAGIC, "pages": int(facts["pages"]),
            "entities": int(facts["entities"]), "paths": int(facts["paths"]),
            "texts": int(facts["texts"]), "skipped": int(facts["skipped"]),
            "stream_bytes": len(bytes(facts["stream"])),
            "media_box_nm": [int(value) for value in facts["media_box_nm"]]}


def _checked_format(value: Any) -> str:
    """导出格式校验：原生格式通过；`dwg` 记外部欠账；其余显式抛错（**不静默回落**）。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail("bad_export_format", "export_format must be a string",
                    value_type=type(value).__name__)
    token = value.strip().lower().lstrip(".")
    if not token:
        return ""
    if token in EXTERNAL_FORMATS:
        raise _fail("export_format_external",
                    "this format needs an external converter; the family imports no third-party library",
                    fmt=token, external=list(EXTERNAL_FORMATS),
                    hint="use draw.sheet.export_sheet with an external DWG converter")
    if token not in SUPPORTED_FORMATS:
        raise _fail("bad_export_format", "unknown export format",
                    fmt=token, known=list(SUPPORTED_FORMATS))
    return token


# --------------------------------------------------------------------------------------
# 操作 1：零件序号（balloon）
# --------------------------------------------------------------------------------------


def balloon(sk_or_doc: Any, *, sheet: Any = None, items: Any = None, source_key: str = "x_bom",
            standard: str = DEFAULT_STANDARD, language: str = DEFAULT_LANGUAGE,
            size: Any = None, scale: Any = None, positions_nm: Any = None,
            radius_nm: int = DEFAULT_RADIUS_NM, leader_nm: int = DEFAULT_LEADER_NM,
            margin_nm: int = DEFAULT_MARGIN_NM, row_gap_nm: int = DEFAULT_ROW_GAP_NM,
            start_item: int = 1, target_uuid: Any = None,
            layer: str = BALLOON_LAYER) -> list[str]:
    """**零件序号（气球）**：把 BOM 行指到图上 —— 每行一颗序号圆 + 一条引线 + 一条 annotation。

    参数（长度整数纳米）：

    * `items`：行序列（`{qty, part_key, material_key?, mass_mg?}`）；`None` ⇒ 读文档顶层 `source_key`；
    * `positions_nm`：每行一个圆心点；`None` ⇒ 沿右留白自动排一列（自下而上，间距 `2*radius_nm+row_gap_nm`）；
    * `radius_nm` / `leader_nm`：圆的半径 / 引线长度（引线自圆左边缘指向 `-x`，`leader_nm=0` ⇒ 不画引线）；
    * `margin_nm` / `row_gap_nm`：自动排布的边距 / 行距；
    * `start_item`：序号起点（1 起，顺序现编，**不采信输入里的 item**）；
    * `layer`：序号圆与引线所在图层（缺省 `BALLOON`，会登记进图纸图层表）。

    边界：行来源空/缺 ⇒ `bom_source_missing`；行不合法 ⇒ `bad_bom_row`；裸草图 ⇒ `bom_needs_document`；
    位置个数不符 ⇒ `balloon_position_mismatch`；圆越出图纸 ⇒ **跳过 + `Sheet.note()`**；
    整批一颗都放不下 ⇒ `balloon_off_sheet`（不返回空列表冒充成功）。

    返回：`[图纸 uuid（若本操作新建）] + [balloon 视图 uuid] + [每颗序号一条 annotation uuid …]`。
    视图 uuid 同时会在 `annotations[]` 里留一条 `kind="balloon"` 的**视图记录**（无 `args`）。
    """
    payload = _base._as_payload(sk_or_doc)
    annotations = _base._live_annotations(sk_or_doc)
    rows = _base._bom_rows(payload, items, source_key)
    radius = _base._require_nm(radius_nm, what="radius_nm", positive=True)
    leader = _base._require_nm(leader_nm, what="leader_nm")
    margin = _base._require_nm(margin_nm, what="margin_nm")
    gap = _base._require_nm(row_gap_nm, what="row_gap_nm")
    start = _base._require_count(start_item, what="start_item", minimum=1)
    layer_name = str(layer or BALLOON_LAYER).strip() or BALLOON_LAYER
    explicit = None if positions_nm is None else _positions_checked(positions_nm, len(rows))
    sheet_obj, created = _base._sheet_for(sk_or_doc, payload, sheet=sheet, standard=standard,
                                          language=language, size=size, scale=scale)
    target = _target(payload, target_uuid, sheet_obj, "balloon")
    positions = explicit if explicit is not None else _auto_positions(sheet_obj, len(rows), radius, margin, gap)
    curves: list[Any] = []
    entries: list[dict[str, Any]] = []
    placed: list[str] = []
    skipped = 0
    for index, row in enumerate(rows):
        x, y = int(positions[index][0]), int(positions[index][1])
        if not _inside(sheet_obj, x, y, radius):
            skipped += 1
            sheet_obj.note("balloon: item %d at (%d, %d) nm does not fit on the sheet -- skipped "
                           "(not returned; radius_nm=%d)" % (int(row["item"]), x, y, radius))
            continue
        item = start + index
        anchor = (x - radius - leader, y)
        curves.append(_sheet.Curve.circle((x, y), radius, layer=layer_name,
                                          name="balloon/circle", source="balloon"))
        if leader > 0:
            curves.append(_sheet.Curve.line((x - radius, y), anchor, layer=layer_name,
                                            name="balloon/leader", source="balloon"))
        uid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                              "cadcore.sw_drawings_extra.balloon|%s|%d|%s|%d|%d"
                              % (sheet_obj.uuid, item, str(row["part_key"]), x, y)))
        entries.append({
            "uuid": uid, "target": [target, "bom"], "kind": "balloon",
            "text_key": "bom.row", "literal": False,
            "args": {"item": item, "qty": int(row["qty"]), "part_key": str(row["part_key"]),
                     "material_key": str(row["material_key"]), "mass_mg": int(row["mass_mg"]),
                     "sheet_uuid": sheet_obj.uuid, "language": str(language or DEFAULT_LANGUAGE),
                     "radius_nm": radius, "leader_nm": leader, "layer": layer_name,
                     "position_nm": [x, y], "anchor_nm": [int(anchor[0]), int(anchor[1])]},
        })
        placed.append(uid)
    if not placed:
        raise _fail("balloon_off_sheet", "no balloon fits on this sheet",
                    rows=len(rows), skipped=skipped, radius_nm=radius,
                    sheet_uuid=sheet_obj.uuid)
    view = _sheet.View(
        uuid=sheet_obj.next_uuid("balloon", len(placed), radius), name="balloon", at_nm=(0, 0),
        scale=(1, 1), scale_text="1:1", hidden=False, visible=tuple(curves), hidden_curves=(),
        shape=None, shape_repr="balloon-set", projection_source="balloon", projection_shape="balloon",
        bounds_model_nm=(0, 0, int(sheet_obj.width_nm), int(sheet_obj.height_nm)),
    )
    sheet_obj.views.append(view)
    _base._register_extra_layer(sheet_obj, layer_name, "balloon callouts")
    for entry in entries:
        _base._register(annotations, entry)
    _base._publish(_base._attachment_host(sk_or_doc), sheet_obj, [view.uuid], "balloon")
    sheet_obj.note("balloon: rows=%d placed=%d skipped=%d radius_nm=%d leader_nm=%d layer=%s view_uuid=%s"
                   % (len(rows), len(placed), skipped, radius, leader, layer_name, view.uuid))
    return ([sheet_obj.uuid] if created else []) + [view.uuid] + placed


# --------------------------------------------------------------------------------------
# 操作 2：BOM 明细表行项（bom_row_items）
# --------------------------------------------------------------------------------------


def bom_row_items(sk_or_doc: Any, *, sheet: Any = None, items: Any = None, source_key: str = "x_bom",
                  merge_duplicates: bool = True, start_item: int = 1,
                  standard: str = DEFAULT_STANDARD, language: str = DEFAULT_LANGUAGE,
                  size: Any = None, scale: Any = None, target_uuid: Any = None) -> list[str]:
    """**BOM 明细表行项**：只出行（文本真源），不出表框 —— 表框由 `sw_drawings.bom_table` 负责。

    真动作在"行项本身"上（不是把输入原样抄一遍）：

    * `merge_duplicates=True`：**同 `part_key` + 同 `material_key` 合并**，数量求和，质量按
      "全采才求和、有一行未采则整组记 `-1`（未采）"，并记下 `source_rows` / `mass_unmeasured_rows`；
      合并后行序 = 首次出现序（稳定，不排序字典）；
    * 行号 `item` 从 `start_item` 顺序现编，**输入里的 item 一律不采信**（避免重号/跳号）；
    * 每行落一条 `kind="bom_row"` 的 annotation（`text_key="bom.row"`，`args` 带
      `item/qty/part_key/material_key/mass_mg/source_rows/mass_unmeasured_rows/sheet_uuid/language`）。

    参数：`sheet` 缺省时新建一张图纸（行项要带 `sheet_uuid` 才能和表框/视图配对，与 `bom_table` 同规）；
    `standard`/`language`/`size`/`scale` 只在新建图纸时生效。

    边界：行来源空/缺 ⇒ `bom_source_missing`；行不合法 ⇒ `bad_bom_row`；裸草图 ⇒ `bom_needs_document`；
    `start_item` 非整数/`<1` 或 `merge_duplicates` 非 bool ⇒ `CadError`；`target_uuid` 非 uuid ⇒ `bad_target_uuid`。

    返回：`[图纸 uuid（若本操作新建）] + [每行一条 annotation uuid …]`。
    """
    payload = _base._as_payload(sk_or_doc)
    annotations = _base._live_annotations(sk_or_doc)
    rows = _base._bom_rows(payload, items, source_key)
    merge = _flag(merge_duplicates, what="merge_duplicates")
    start = _base._require_count(start_item, what="start_item", minimum=1)
    sheet_obj, created = _base._sheet_for(sk_or_doc, payload, sheet=sheet, standard=standard,
                                          language=language, size=size, scale=scale)
    target = _target(payload, target_uuid, sheet_obj, "bom_row")
    groups = (_merge_rows(rows) if merge
              else [dict(row, source_rows=1, mass_unmeasured_rows=(1 if int(row["mass_mg"]) < 0 else 0))
                    for row in rows])
    row_uuids: list[str] = []
    for index, group in enumerate(groups):
        item = start + index
        uid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                              "cadcore.sw_drawings_extra.bom_row|%s|%d|%s|%d|%s"
                              % (sheet_obj.uuid, item, str(group["part_key"]), int(group["qty"]),
                                 str(group["material_key"]))))
        _base._register(annotations, {
            "uuid": uid, "target": [target, "bom"], "kind": "bom_row",
            "text_key": "bom.row", "literal": False,
            "args": {"item": item, "qty": int(group["qty"]), "part_key": str(group["part_key"]),
                     "material_key": str(group["material_key"]), "mass_mg": int(group["mass_mg"]),
                     "source_rows": int(group["source_rows"]),
                     "mass_unmeasured_rows": int(group["mass_unmeasured_rows"]),
                     "sheet_uuid": sheet_obj.uuid, "language": str(language or DEFAULT_LANGUAGE)},
        })
        row_uuids.append(uid)
    _base._publish(_base._attachment_host(sk_or_doc), sheet_obj, [], "bom_row_items")
    sheet_obj.note("bom_row_items: source_rows=%d rows=%d merged=%s start_item=%d sheet_uuid=%s"
                   % (len(rows), len(row_uuids), bool(merge), start, sheet_obj.uuid))
    return ([sheet_obj.uuid] if created else []) + row_uuids


# --------------------------------------------------------------------------------------
# 操作 3：打印导出（print_svg_text：SVG 文本 + 真 PDF 载荷）
# --------------------------------------------------------------------------------------


def print_svg_text(sk_or_doc: Any, *, sheet: Any = None, out: Any = None, export_format: Any = "",
                   standard: str = DEFAULT_STANDARD, language: str = DEFAULT_LANGUAGE,
                   size: Any = None, scale: Any = None) -> list[str]:
    """**打印与导出**：SVG 文本 + **真 PDF 载荷**（纯标准库写出器，零第三方依赖）。

    真动作（两件，缺一不算）：

    1. **SVG 文本**：用 `draw.sheet` 的唯一写出器渲染，`args["svg_text"]` + `sha256`/`bytes`/`views`/`entities`；
    2. **PDF**：本文件 `render_pdf()` 把图纸实体（LINE / LWPOLYLINE / CIRCLE / ARC / TEXT / DIMENSION）
       转成 PDF 内容流，装配成**完整 PDF 文件**（`%PDF-1.4` 头 + 对象表 + 内容流 + xref + trailer），
       载荷、`sha256`、`bytes`、`pages`、逐类实体数落在同一条 annotation 的 `pdf_*` 键上。

    `export_format`：`""`（缺省；落盘格式由 `out` 扩展名决定，无扩展名按历史口径落 `.svg`）、`"svg"`、
    `"pdf"`（真 PDF 字节）、`"dxf"`（要 `out=`，转交 `draw.sheet.export_sheet` 的内置 ASCII 写出器）；
    `"dwg"` ⇒ `export_format_external`（**外部欠账**：DWG 为闭源二进制格式，本模块不引第三方库，
    绝不拿 PDF/SVG 冒充）；未知格式/非字符串 ⇒ `bad_export_format`。

    落盘：`out=` 给了就写文件并登记 `ExportResult`（`requested == actual`，不冒充）；`out=None` 时
    PDF 载荷只走文档 annotation（机读面），**不假装落过盘**。

    边界：没有文档承载 annotation 且没给 `out` ⇒ `print_needs_document`（**不**假装导出过）；
    渲染结果不是完整 SVG 文档 ⇒ `svg_render_failed`；图纸没有视图 ⇒ `views=0` 并 `Sheet.note()` 留痕
    （**不**把"空图"说成有内容）。

    返回：`[图纸 uuid（若本操作新建）] + [导出记录 uuid]`；裸草图（没有文档可登记）时返回的图纸 uuid
    只存在于调用方手里，导出记录 uuid 也**没有**落进任何 `annotations[]` —— 此时载荷只在 `out=` 文件里，
    **不假装落过文档**。
    """
    payload = _base._as_payload(sk_or_doc)
    host = _base._attachment_host(sk_or_doc)
    annotations = _base._live_annotations(sk_or_doc) if host is not None else None
    fmt = _checked_format(export_format)
    if annotations is None and out is None:
        raise _fail("print_needs_document",
                    "a bare sketch has no annotations[] to carry the export payload -- pass out=<path>",
                    hint="pass an L1 document mapping / Document, or out='drawing.pdf'")
    if fmt == "dxf" and out is None:
        raise _fail("export_needs_out", "dxf needs a target path", fmt=fmt)
    sheet_obj, created = _base._sheet_for(sk_or_doc, payload, sheet=sheet, standard=standard,
                                          language=language, size=size, scale=scale)
    blob = _sheet._render_svg(sheet_obj)
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise _fail("svg_render_failed", "the sheet renderer produced no payload")
    blob = bytes(blob)
    text = blob.decode("utf-8")
    stripped = text.strip()
    if "<svg" not in stripped or not stripped.endswith("</svg>"):
        raise _fail("svg_render_failed", "rendered payload is not a complete SVG document",
                    bytes=len(blob))
    parts = _pdf_parts(sheet_obj)
    pdf_blob = render_pdf(sheet_obj, parts=parts)
    digest = hashlib.sha256(blob).hexdigest()
    pdf_digest = hashlib.sha256(pdf_blob).hexdigest()
    entities = int(parts["entities"])
    path_text = ""
    actual_format = "svg"
    if out is not None:
        requested = fmt or ("pdf" if str(out).strip().lower().endswith(".pdf") else "svg")
        if requested == "dxf":
            path_text = str(_sheet.export_sheet(sheet_obj, fmt="dxf", out=str(out)))
            actual_format = "dxf"
        elif requested == "pdf":
            path_text = write_pdf(sheet_obj, out)
            actual_format = "pdf"
            sheet_obj.exports.append(_sheet.ExportResult(
                requested="pdf", actual="pdf", path=path_text, downgraded=False, reason="",
                writer=PDF_WRITER, entities=entities, bytes=len(pdf_blob),
                titleblock_rendered=bool(sheet_obj.title_fields)))
        else:
            target_path = Path(_sheet._with_extension(str(out), "svg"))
            if str(target_path.parent):
                target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(blob)
            path_text = str(target_path)
            try:
                sheet_obj.exports.append(_sheet.ExportResult(
                    requested="svg", actual="svg", path=path_text, downgraded=False, reason="",
                    writer="builtin_svg:print_svg_text", entities=entities, bytes=len(blob),
                    titleblock_rendered=bool(sheet_obj.title_fields)))
            except TypeError as exc:  # 构造签名不符 ⇒ 显式降级留痕，不静默丢导出记录
                sheet_obj.note("print_svg_text: ExportResult not recorded (%s) -- the SVG text is still "
                               "carried by the svg_print annotation" % type(exc).__name__)
    record_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                                  "cadcore.sw_drawings_extra.svg_print|%s|%s|%s"
                                  % (sheet_obj.uuid, digest, pdf_digest)))
    if annotations is not None:
        _base._register(annotations, {
            "uuid": record_uuid, "target": [str(sheet_obj.uuid), "sheet"], "kind": SHEET_RECORD,
            "text_key": "", "literal": True,
            "args": {"sheet_uuid": sheet_obj.uuid, "sha256": digest, "bytes": len(blob),
                     "views": len(sheet_obj.views), "entities": entities, "out": path_text,
                     "language": str(language or DEFAULT_LANGUAGE), "svg_text": text,
                     "export_format": actual_format, "requested_format": fmt or actual_format,
                     "pdf_magic": PDF_MAGIC, "pdf_writer": PDF_WRITER, "pdf_bytes": len(pdf_blob),
                     "pdf_sha256": pdf_digest, "pdf_pages": int(parts["pages"]),
                     "pdf_entities": entities, "pdf_paths": int(parts["paths"]),
                     "pdf_texts": int(parts["texts"]), "pdf_skipped": int(parts["skipped"]),
                     "pdf_stream_bytes": len(bytes(parts["stream"])),
                     "pdf_b64": base64.b64encode(pdf_blob).decode("ascii")},
        })
    if not sheet_obj.views:
        sheet_obj.note("print_svg_text: sheet has no views -- the export carries only the frame (views=0)")
    _base._publish(host, sheet_obj, [], "print_svg_text")
    sheet_obj.note("print_svg_text: views=%d entities=%d svg_bytes=%d pdf_bytes=%d pdf_sha256=%s "
                   "format=%s out=%s record_uuid=%s"
                   % (len(sheet_obj.views), entities, len(blob), len(pdf_blob), pdf_digest,
                      actual_format, path_text or "-", record_uuid))
    return ([sheet_obj.uuid] if created else []) + [record_uuid]


# --------------------------------------------------------------------------------------
# 机读事实与自检
# --------------------------------------------------------------------------------------


def module_facts() -> dict[str, Any]:
    """族模块机读事实（三态可辨：`0` 真为零 / `-1` 未采 / `"NA"` 不适用）。"""
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "base_family": _base.FAMILY,
        "base_operations": sorted(_base.OPERATIONS),
        "base_op_count": len(_base.OPERATIONS),
        "balloon_layer": BALLOON_LAYER,
        "svg_record_kind": SHEET_RECORD,
        "default_radius_nm": DEFAULT_RADIUS_NM,
        "default_leader_nm": DEFAULT_LEADER_NM,
        "svg_writer": "draw.sheet._render_svg",
        "pdf_writer": PDF_WRITER,
        "pdf_magic": PDF_MAGIC,
        "formats_native": list(NATIVE_FORMATS),
        "formats_external": list(EXTERNAL_FORMATS),
        "external_debt": {"dwg": "needs an external converter (ODA/Teigha); this family is stdlib-only"},
        "kernel_required": False,
        "kernel": _sheet.NA,
        "kernel_capabilities": _sheet.UNMEASURED,
    }


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "balloon": balloon,
    "bom_row_items": bom_row_items,
    "print_svg_text": print_svg_text,
}


def _selftest_document() -> dict[str, Any]:
    """自检夹具：60×40×20 mm 板（真草图 + 真拉伸特征）的 L1 文档映射，**不依赖任何夹具文件**。"""
    sketch: dict[str, Any] = {
        "uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.selftest.sketch")),
        "plane": "XY", "entities": [], "constraints": [], "dimensions": []}
    _primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=(60 * NM_PER_MM, 40 * NM_PER_MM))
    return {
        "meta": {"doc_uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.selftest.doc"))},
        "sketches": [sketch],
        "features": [{"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.selftest.extrude")),
                      "kind": "extrude", "params": {"distance_nm": 20 * NM_PER_MM}, "suppressed": False}],
        "annotations": [], "sheets": [],
    }


def _selftest_items() -> list[dict[str, Any]]:
    """自检行：三行明细（part.base 两行，其中一行质量未采）+ part.bolt 一行。"""
    return [{"qty": 2, "part_key": "part.base", "material_key": "mat.steel", "mass_mg": 1500},
            {"qty": 1, "part_key": "part.base", "material_key": "mat.steel"},
            {"qty": 4, "part_key": "part.bolt", "material_key": "mat.steel", "mass_mg": 25}]


def _selftest_export(pdf_path: str | None = None) -> int:
    """导出自检/取证入口：真跑一条"气球 + 导出"，落 PDF 后打印魔数判定行。"""
    doc = _selftest_document()
    OPERATIONS["balloon"](doc, items=_selftest_items())
    # 跨平台写法：临时目录用 stdlib tempfile 现取（Linux 仍是 /tmp，Windows 是 %TEMP%），
    # 拼接用 os.path.join —— 不写死 POSIX 绝对路径（G7 门 posix_paths 判据）。
    target = pdf_path or os.path.join(tempfile.gettempdir(), "cadcore_sw_drawings_extra_proof.pdf")
    OPERATIONS["print_svg_text"](doc, out=target)
    blob = Path(target).read_bytes()
    facts = pdf_facts(_base._sheet_objects(doc)[-1])
    print("PDF_PROOF path=%s head=%s bytes=%d pages=%d entities=%d paths=%d texts=%d skipped=%d "
          "stream_bytes=%d sha256=%s"
          % (target, blob[:8].decode("latin-1"), len(blob), facts["pages"], facts["entities"],
             facts["paths"], facts["texts"], facts["skipped"], facts["stream_bytes"],
             hashlib.sha256(blob).hexdigest()))
    return 0


def selftest() -> int:
    """文件自带自检：接口 + 三条操作的真后果 + PDF 载荷 + 边界 reason，逐条记账后打印判定行。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    check("family", FAMILY == "sw_drawings_extra", FAMILY)
    check("operations", sorted(OPERATIONS) == ["balloon", "bom_row_items", "print_svg_text"],
          ",".join(sorted(OPERATIONS)))
    items = _selftest_items()

    doc = _selftest_document()
    out = OPERATIONS["balloon"](doc, items=items)
    annos = {str(entry.get("uuid")) for entry in doc["annotations"]}
    sheet_uuids = {str(entry.get("uuid")) for entry in doc["sheets"]}
    view_uid = str(out[1])
    balloons = [entry for entry in doc["annotations"]
                if entry.get("kind") == "balloon" and str(entry.get("uuid")) != view_uid]
    check("balloon.count", len(out) == 2 + len(items), "returned=%d" % len(out))
    check("balloon.rows", len(balloons) == len(items), "balloon_rows=%d" % len(balloons))
    check("balloon.resolve", all(uid in annos or uid in sheet_uuids for uid in out),
          "unresolved=%d" % sum(1 for uid in out if uid not in annos and uid not in sheet_uuids))
    check("balloon.sheet_entry", str(out[0]) in sheet_uuids, "sheets=%d" % len(doc["sheets"]))

    rows_doc = _selftest_document()
    rows_out = OPERATIONS["bom_row_items"](rows_doc, items=items)
    row_args = [entry["args"] for entry in rows_doc["annotations"] if entry.get("kind") == "bom_row"]
    check("bom_row.count", len(rows_out) == 3 and len(row_args) == 2,
          "returned=%d rows=%d" % (len(rows_out), len(row_args)))
    merged = {int(arg["item"]): arg for arg in row_args}
    check("bom_row.merge", merged.get(1, {}).get("qty") == 3 and merged.get(1, {}).get("source_rows") == 2,
          "qty=%s source_rows=%s" % (merged.get(1, {}).get("qty"), merged.get(1, {}).get("source_rows")))
    check("bom_row.mass_unmeasured", merged.get(1, {}).get("mass_mg") == _sheet.UNMEASURED,
          "mass_mg=%s" % merged.get(1, {}).get("mass_mg"))
    check("bom_row.measured_mass", merged.get(2, {}).get("mass_mg") == 25,
          "mass_mg=%s" % merged.get(2, {}).get("mass_mg"))

    svg_doc = _selftest_document()
    svg_out = OPERATIONS["print_svg_text"](svg_doc)
    record = [entry for entry in svg_doc["annotations"] if entry.get("kind") == SHEET_RECORD]
    text = str(record[0]["args"]["svg_text"]) if record else ""
    check("print.record", len(svg_out) == 2 and len(record) == 1,
          "returned=%d records=%d" % (len(svg_out), len(record)))
    check("print.svg", "<svg" in text and text.strip().endswith("</svg>"),
          "bytes=%d" % len(text.encode("utf-8")))
    check("print.sha", bool(record) and record[0]["args"]["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest(),
          "sha=%s" % (record[0]["args"]["sha256"][:12] if record else "-"))
    second = _selftest_document()
    OPERATIONS["print_svg_text"](second)
    text2 = str([e for e in second["annotations"] if e.get("kind") == SHEET_RECORD][0]["args"]["svg_text"])
    check("print.deterministic", text == text2, "equal=%s" % (text == text2))
    check("print.views_zero_is_real", bool(record) and record[0]["args"]["views"] == 0,
          "views=%s" % (record[0]["args"]["views"] if record else "-"))

    pdf_args = dict(record[0]["args"]) if record else {}
    pdf_payload = b""
    if pdf_args.get("pdf_b64"):
        pdf_payload = base64.b64decode(str(pdf_args["pdf_b64"]))
    check("export.pdf_magic", pdf_args.get("pdf_magic") == PDF_MAGIC
          and pdf_payload[:8] == PDF_MAGIC.encode("ascii"),
          "magic=%s head=%s" % (pdf_args.get("pdf_magic"), pdf_payload[:8].decode("latin-1")))
    check("export.pdf_structure", b"/Type /Page" in pdf_payload and b"stream" in pdf_payload
          and pdf_payload.rstrip().endswith(b"%%EOF") and b"xref" in pdf_payload,
          "bytes=%d" % len(pdf_payload))
    check("export.pdf_stream_nonempty", int(pdf_args.get("pdf_stream_bytes") or 0) > 0
          and int(pdf_args.get("pdf_bytes") or 0) == len(pdf_payload),
          "stream_bytes=%s pdf_bytes=%s" % (pdf_args.get("pdf_stream_bytes"), pdf_args.get("pdf_bytes")))
    check("export.pdf_entity_mapping",
          int(pdf_args.get("pdf_paths", -1)) + int(pdf_args.get("pdf_texts", -1))
          + int(pdf_args.get("pdf_skipped", -1)) == int(pdf_args.get("pdf_entities", -2))
          and int(pdf_args.get("pdf_entities", -1)) >= 0,
          "entities=%s paths=%s texts=%s skipped=%s"
          % (pdf_args.get("pdf_entities"), pdf_args.get("pdf_paths"),
             pdf_args.get("pdf_texts"), pdf_args.get("pdf_skipped")))
    check("export.pdf_sha", pdf_args.get("pdf_sha256") == hashlib.sha256(pdf_payload).hexdigest(),
          "sha=%s" % str(pdf_args.get("pdf_sha256"))[:12])
    geom_doc = _selftest_document()
    OPERATIONS["balloon"](geom_doc, items=items)
    geom_out = OPERATIONS["print_svg_text"](geom_doc, sheet=_base._sheet_objects(geom_doc)[-1])
    geom_record = [e for e in geom_doc["annotations"] if e.get("kind") == SHEET_RECORD]
    geom_args = dict(geom_record[0]["args"]) if geom_record else {}
    check("export.pdf_geometry_paths", len(geom_out) == 1 and int(geom_args.get("pdf_paths") or 0) >= 2 * len(items),
          "paths=%s" % geom_args.get("pdf_paths"))

    try:
        OPERATIONS["balloon"](_selftest_document(), items=[])
        check("edge.bom_source_missing", False, "no error raised")
    except CadError as exc:
        check("edge.bom_source_missing", exc.reason == "bom_source_missing", exc.reason)
    try:
        OPERATIONS["balloon"](_selftest_document(), items=items,
                              positions_nm=[(10 * NM_PER_MM, 10 * NM_PER_MM)])
        check("edge.position_mismatch", False, "no error raised")
    except CadError as exc:
        check("edge.position_mismatch", exc.reason == "balloon_position_mismatch", exc.reason)
    try:
        OPERATIONS["balloon"](_selftest_document(), items=items, positions_nm=[(0, 0)] * len(items))
        check("edge.off_sheet", False, "no error raised")
    except CadError as exc:
        check("edge.off_sheet", exc.reason == "balloon_off_sheet", exc.reason)
    try:
        OPERATIONS["print_svg_text"]({"entities": []})
        check("edge.print_needs_document", False, "no error raised")
    except CadError as exc:
        check("edge.print_needs_document", exc.reason == "print_needs_document", exc.reason)
    try:
        OPERATIONS["print_svg_text"](_selftest_document(), export_format="dwg")
        check("edge.export_format_external", False, "no error raised")
    except CadError as exc:
        check("edge.export_format_external", exc.reason == "export_format_external", exc.reason)
    try:
        OPERATIONS["print_svg_text"](_selftest_document(), export_format="jpg")
        check("edge.bad_export_format", False, "no error raised")
    except CadError as exc:
        check("edge.bad_export_format", exc.reason == "bad_export_format", exc.reason)
    try:
        OPERATIONS["print_svg_text"](_selftest_document(), export_format="dxf")
        check("edge.export_needs_out", False, "no error raised")
    except CadError as exc:
        check("edge.export_needs_out", exc.reason == "export_needs_out", exc.reason)

    failed = [name for name, ok, _ in checks if not ok]
    print("EXTRA_OPS verdict=%s ops=%d checks=%d failed=%d%s"
          % ("PASS" if not failed else "FAIL", len(OPERATIONS), len(checks), len(failed),
             "" if not failed else " failed_names=" + ",".join(failed)))
    for name, ok, detail in checks:
        print("EXTRA_OPS case=%s state=%s detail=%s" % (name, "PASS" if ok else "RED", detail or "-"))
    return 0 if not failed else 1


if __name__ == "__main__":
    if "--pdf" in sys.argv:
        _index = sys.argv.index("--pdf")
        # 注意：变量名不许叫 `_target` —— 模块级赋值会遮掉同名 helper（本席实撞过一次）。
        _out_path = sys.argv[_index + 1] if _index + 1 < len(sys.argv) else None
        raise SystemExit(_selftest_export(_out_path) or selftest())
    raise SystemExit(selftest())
