#!/usr/bin/env python3
"""output 族实现片 w10：打印主命令 PLOT（AutoCAD `PLOT` / `-PLOT`）在 L1 文档模型上的真实现。

族内定位（本席只做清单第 1 号那一条）
------------------------------------
族复核 `docs/research/cad-parity/status/autocad-output.json` 的 `still_red_must_have` 按清单顺序为：
0 页面设置（Page Setup）/ **1 打印主命令 PLOT** / 2 打印到 PDF / 3 绘图仪配置(.pc3) /
4 打印样式表 CTB-STB / 5 线宽 LWEIGHT / 6 打印区域选择 / 7 打印比例与布满图纸 / 8 打印预览 PREVIEW /
9 可打印控制 / 10 模型空间直接出图 / 11 布局管理 / 12 批量发布 PUBLISH / 13 DWG 读写 / 14 DXF 导入导出。
⇒ 本片实现 **第 1 号 = `plot`**，别的条目（模型空间出图、STB/CTB 映射、.pc3 驱动、PREVIEW 成图渲染）
**不在本片冒充**：它们要么显式抛 `CadError(reason=…)`，要么只把名字带进作业记录、不声称生效。

冻结接口
--------
    FAMILY = "output"
    OPERATIONS = {"plot": plot}
    plot(sk_or_doc, **kwargs) -> list[str]        # 返回作业 uuid 列表（每个作业一个）
    长度一律整数纳米；错误一律 `cadcore.errors.CadError(reason=<机器可读 token>)`

真实现口径（不是桩）
--------------------
1. **窗口是算出来的**：`area="extents"` 的窗口 = 文档几何（线/圆/多段线）的整数纳米包围盒；
   `area="window"` 用调用方给的 `window_nm`（4 个整数纳米）。模型里没有几何 ⇒ `no_model_geometry`，
   绝不拿空窗口糊过去。
2. **比例是真换算**：`"1:100"` ⇒ 图纸 1 单位 = 模型 100 单位（`factor = 1/100`）；
   `fit_to_paper=True` 时按可打印区（幅面 − 边距）现算 factor。`"fit"` 而不给 `fit_to_paper` ⇒ `bad_scale`。
3. **平铺是真分页**：图纸尺寸 = 窗口 × factor；按可打印区切 `cols × rows` 个瓦片，越界线段在单页里被裁掉，
   `pages = tiles × copies`。**同一窗口在 1:1 下比 1:100 多出页数是可机检的真后果。**
4. **落盘是真文件**：`dry_run=False` 且给了 `out` ⇒ 纯标准库写出**真 PDF**（无时间戳、逐字节确定性）
   或真 SVG；`sha256`/`bytes` 进记录，调用方可复算。`dry_run=True`（缺省）只记作业、不落盘。
5. **后果落 L1 载荷**：作业写进 `doc.extra["output"]["plot_jobs"]`（Mapping 形态的文档则写 `doc["output"]`），
   两条 `to_payload()` 都能读回来 ⇒ 「文档真的变了」可机读。本片**只读**几何，绝不动草图实体。
6. **uuid 确定性**：`uuid5(URL, "cadcore:output:plot:" + sha256(canonical job spec))`，同一输入同一 uuid
   （与全族 `uuid5` 口径一致，便于对拍）。

本片**诚实欠账**（写在 docstring 里，不装成已做）
------------------------------------------------
- `space="model"`（模型空间直接出图）= 族内另一条能力，本片抛 `unsupported_space`，不做。
- `device` 只认缺省 `DWG To PDF.pc3`（只是作业标签，真字节由本片自己的写出器产生）；
  别的绘图仪/`.pc3` 配置 = 族内另一条能力，抛 `unsupported_device`。
- `plot_style_table` 只做**形状校验 + 记名**（`style_table_applied=False`），**不声称**做了 CTB/STB
  的颜色/线宽映射。
- 圆弧按 16 边形弦近似（记录里 `arc_approximation` 显式标注）。

自检
----
    python3 cadcore/ops/features/output__w10.py
    ⇒ `FEATURE_OK family=output op=plot verdict=PASS …`（≥6 条硬断言：窗口逐纳米比对、
      PDF 真落盘 + sha256 复算、逐字节确定性、1:1 平铺页数、三条负例 reason）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

#: 直接 `python3 cadcore/ops/features/output__w10.py` 时要能 import 到 cadcore。
_PKG_ROOT = Path(__file__).resolve().parents[3]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402  (sys.path 补完才能 import)

__all__ = ["FAMILY", "OPERATIONS", "PAPER_SIZES_NM", "plot", "main"]

FAMILY = "output"

#: 幅面 → 整数纳米（ISO A 系 + 北美常用；LETTER/LEGAL/TABLOID 按 25.4 mm/inch 精确值）。
PAPER_SIZES_NM: dict[str, tuple[int, int]] = {
    "A4": (210_000_000, 297_000_000),
    "A3": (297_000_000, 420_000_000),
    "A2": (420_000_000, 594_000_000),
    "A1": (594_000_000, 841_000_000),
    "A0": (841_000_000, 1_189_000_000),
    "LETTER": (215_900_000, 279_400_000),
    "LEGAL": (215_900_000, 355_600_000),
    "TABLOID": (279_400_000, 431_800_000),
}

#: 可打印区边距（每边，整数纳米）；幅面减两侧边距才是能画的面积。
PRINT_MARGIN_NM = 10_000_000
DEFAULT_LINEWEIGHT_MM = 0.25
DEFAULT_DEVICE = "DWG To PDF.pc3"
SUPPORTED_FORMATS = ("pdf", "svg")
SUPPORTED_AREAS = ("extents", "window")
_NM_PER_PT = 25_400_000.0 / 72.0


def _bad(reason: str, message: str, **details: Any) -> CadError:
    """族内统一错误构造：`reason` 机器可读，`details` 供判定行归因。"""
    return CadError(message, reason=reason, **details)


def _int_nm(value: Any, *, key: str) -> int:
    """长度=整数纳米：bool/浮点/字符串一律拒（全族口径）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad("invalid_length", "length must be an int in nanometres", key=key, value=repr(value))
    return int(value)


def _sheets(doc: Any) -> list[Any]:
    """L1 文档/裸 Mapping 两种形态的 `sheets` 取法（都不是 ⇒ bad_reference）。"""
    raw = doc.get("sheets") if isinstance(doc, Mapping) else getattr(doc, "sheets", None)
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _bad("bad_reference", "sheets must be a sequence")
    return list(raw)


def _holder(doc: Any) -> list[dict[str, Any]]:
    """作业账挂点：Document.extra["output"]["plot_jobs"]（或 Mapping 文档的同名键）。"""
    if isinstance(doc, Mapping):
        bucket = doc.get("output")
        if not isinstance(bucket, dict):
            bucket = {}
            doc["output"] = bucket  # type: ignore[index]
    else:
        extra = getattr(doc, "extra", None)
        if not isinstance(extra, dict):
            raise _bad("bad_reference", "document has no extra mapping for output jobs")
        bucket = extra.get("output")
        if not isinstance(bucket, dict):
            bucket = {}
            extra["output"] = bucket
    jobs = bucket.get("plot_jobs")
    if not isinstance(jobs, list):
        jobs = []
        bucket["plot_jobs"] = jobs
    return jobs


def _iter_geom(doc: Any) -> list[Mapping[str, Any]]:
    """收集文档里所有几何（`sketches[].entities[].geom`；裸 sketch 则直接吃 `entities`）。"""
    geoms: list[Mapping[str, Any]] = []
    containers: list[Any] = []
    if isinstance(doc, Mapping):
        if "entities" in doc:
            containers.append(doc)
        containers.extend(list(doc.get("sketches") or []))
    else:
        containers.extend(list(getattr(doc, "sketches", None) or []))
    for sketch in containers:
        entities = sketch.get("entities") if isinstance(sketch, Mapping) else None
        for entity in entities or []:
            geom = entity.get("geom") if isinstance(entity, Mapping) else None
            if isinstance(geom, Mapping):
                geoms.append(geom)
    return geoms


def _points(geom: Mapping[str, Any]) -> list[tuple[int, int]]:
    raw = geom.get("points_nm") or []
    pts: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, Mapping):
            pts.append((_int_nm(item.get("x"), key="points_nm.x"),
                        _int_nm(item.get("y"), key="points_nm.y")))
        elif isinstance(item, Sequence) and len(item) == 2:
            pts.append((_int_nm(item[0], key="points_nm.x"), _int_nm(item[1], key="points_nm.y")))
        else:
            raise _bad("bad_geometry", "points_nm item must be a pair or mapping", item=repr(item))
    return pts


def _bbox(geoms: Sequence[Mapping[str, Any]]) -> tuple[int, int, int, int]:
    """整数纳米包围盒；无几何或退化到零面积 ⇒ 显式失败（不当空窗口）。"""
    xs: list[int] = []
    ys: list[int] = []
    for geom in geoms:
        if all(key in geom for key in ("x1", "y1", "x2", "y2")):
            xs.extend([_int_nm(geom["x1"], key="x1"), _int_nm(geom["x2"], key="x2")])
            ys.extend([_int_nm(geom["y1"], key="y1"), _int_nm(geom["y2"], key="y2")])
        elif "cx" in geom and "cy" in geom:
            radius = _int_nm(geom.get("r", geom.get("radius")), key="r")
            cx, cy = _int_nm(geom["cx"], key="cx"), _int_nm(geom["cy"], key="cy")
            xs.extend([cx - radius, cx + radius])
            ys.extend([cy - radius, cy + radius])
        else:
            for x, y in _points(geom):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise _bad("no_model_geometry", "document has no plottable geometry for extents")
    window = (min(xs), min(ys), max(xs), max(ys))
    if window[2] <= window[0] or window[3] <= window[1]:
        raise _bad("degenerate_extents", "extents have zero area -- nothing to plot", window_nm=list(window))
    return window


def _parse_scale(spec: Any) -> tuple[int, int]:
    """`"1:100"` / `"1/100"` / `(1, 100)` → (分子, 分母)，都必须是正整数。"""
    if isinstance(spec, bool):
        raise _bad("bad_scale", "scale must be a ratio or a pair", scale=repr(spec))
    if isinstance(spec, (tuple, list)):
        if len(spec) != 2:
            raise _bad("bad_scale", "scale pair needs two positive integers", scale=repr(spec))
        num, den = spec
    elif isinstance(spec, str):
        text = spec.strip().replace("：", ":")
        for sep in (":", "/"):
            if sep in text:
                head, _, tail = text.partition(sep)
                try:
                    num, den = int(head.strip()), int(tail.strip())
                except ValueError as exc:
                    raise _bad("bad_scale", "scale is not numeric", scale=spec) from exc
                break
        else:
            raise _bad("bad_scale", "scale must look like '1:100'", scale=spec)
    else:
        raise _bad("bad_scale", "scale must be a ratio string or pair", scale=repr(spec))
    if isinstance(num, bool) or isinstance(den, bool) or int(num) <= 0 or int(den) <= 0:
        raise _bad("non_positive_scale", "scale terms must be positive", scale=repr(spec))
    return int(num), int(den)


def _segments(geoms: Sequence[Mapping[str, Any]], window: Sequence[int],
              factor: float) -> tuple[list[tuple[float, float, float, float]], bool]:
    """模型几何 → 纸张坐标线段（圆按 16 边形弦近似；返回 (线段, 是否含圆弧近似)）。"""
    x0, y0 = float(window[0]), float(window[1])
    out: list[tuple[float, float, float, float]] = []
    arcs = False
    for geom in geoms:
        if all(key in geom for key in ("x1", "y1", "x2", "y2")):
            out.append(((float(_int_nm(geom["x1"], key="x1")) - x0) * factor,
                        (float(_int_nm(geom["y1"], key="y1")) - y0) * factor,
                        (float(_int_nm(geom["x2"], key="x2")) - x0) * factor,
                        (float(_int_nm(geom["y2"], key="y2")) - y0) * factor))
        elif "cx" in geom and "cy" in geom:
            arcs = True
            import math

            cx = (float(_int_nm(geom["cx"], key="cx")) - x0) * factor
            cy = (float(_int_nm(geom["cy"], key="cy")) - y0) * factor
            radius = float(_int_nm(geom.get("r", geom.get("radius")), key="r")) * factor
            for step in range(16):
                a1 = 2.0 * math.pi * step / 16.0
                a2 = 2.0 * math.pi * (step + 1) / 16.0
                out.append((cx + radius * math.cos(a1), cy + radius * math.sin(a1),
                            cx + radius * math.cos(a2), cy + radius * math.sin(a2)))
        else:
            pts = _points(geom)
            if len(pts) < 2:
                continue
            closed = bool(geom.get("closed"))
            for index in range(len(pts) - (0 if closed else 1)):
                ax, ay = pts[index]
                bx, by = pts[(index + 1) % len(pts)]
                out.append(((ax - x0) * factor, (ay - y0) * factor,
                            (bx - x0) * factor, (by - y0) * factor))
    return out, arcs


def _pt(value_nm: float) -> float:
    return float(value_nm) / _NM_PER_PT


def _num(value: float) -> str:
    """PDF/SVG 数字：定点截断去尾零 ⇒ 无浮点尾巴、逐字节可复现。"""
    text = "%.4f" % float(value)
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _tile_page(segments: Sequence[tuple[float, float, float, float]], col: int, row: int,
               tile_w: float, tile_h: float) -> list[tuple[float, float, float, float]]:
    """取第 (col,row) 个瓦片：平移到纸面坐标并裁掉整条越界的线段（真分页，不是复制全图）。"""
    dx, dy = -col * tile_w, -row * tile_h
    page: list[tuple[float, float, float, float]] = []
    for x1, y1, x2, y2 in segments:
        ax, ay, bx, by = x1 + dx, y1 + dy, x2 + dx, y2 + dy
        if max(ax, bx) <= 0.0 or max(ay, by) <= 0.0:
            continue
        if min(ax, bx) >= tile_w or min(ay, by) >= tile_h:
            continue
        page.append((ax, ay, bx, by))
    return page


def _pdf_pages(pages: Sequence[tuple[float, float, Sequence[tuple[float, float, float, float]], float]]) -> bytes:
    """真 PDF 字节（多页、纯标准库、无时间戳 ⇒ 逐字节确定性）。"""
    objects: list[bytes] = [b"", b""]
    kids: list[int] = []
    for width_nm, height_nm, segments, lineweight_mm in pages:
        width, height = _pt(width_nm), _pt(height_nm)
        body = ["q", "%s w" % _num(max(float(lineweight_mm), 0.01)),
                "0 0 %s %s re S" % (_num(width), _num(height))]
        for x1, y1, x2, y2 in segments:
            body.append("%s %s m %s %s l S" % (_num(_pt(x1)), _num(_pt(y1)),
                                               _num(_pt(x2)), _num(_pt(y2))))
        body.append("Q")
        content = ("\n".join(body) + "\n").encode("ascii")
        content_id = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
        page_id = len(objects) + 1
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Contents %d 0 R "
                        "/Resources << >> >>" % (_num(width), _num(height), content_id)).encode("ascii"))
        kids.append(page_id)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = ("<< /Type /Pages /Count %d /Kids [%s] >>"
                  % (len(kids), " ".join("%d 0 R" % kid for kid in kids))).encode("ascii")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_at))
    return bytes(out)


def _svg_pages(pages: Sequence[tuple[float, float, Sequence[tuple[float, float, float, float]], float]]) -> bytes:
    """真 SVG 文本（每页一个 `<g>`；y 轴翻转，线宽毫米 → 用户单位）。"""
    parts: list[str] = []
    for index, (width_nm, height_nm, segments, lineweight_mm) in enumerate(pages, 1):
        width, height = _num(_pt(width_nm)), _num(_pt(height_nm))
        stroke = _num(float(lineweight_mm) * 72.0 / 25.4)
        parts.append('<g id="page%d" transform="translate(0,%s) scale(1,-1)">' % (index, height))
        parts.append('<rect x="0" y="0" width="%s" height="%s" fill="none" stroke="#000" '
                     'stroke-width="%s"/>' % (width, height, stroke))
        for x1, y1, x2, y2 in segments:
            parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#000" stroke-width="%s"/>'
                         % (_num(_pt(x1)), _num(_pt(y1)), _num(_pt(x2)), _num(_pt(y2)), stroke))
        parts.append("</g>")
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="%spt" height="%spt" '
            'viewBox="0 0 %s %s">' % (_num(_pt(pages[0][0])), _num(_pt(pages[0][1])),
                                      _num(_pt(pages[0][0])), _num(_pt(pages[0][1]))))
    return ("\n".join([head] + parts + ["</svg>", ""])).encode("ascii")


def _out_path(out: str, fmt: str) -> Path:
    path = Path(str(out))
    if path.suffix.lower() != "." + fmt:
        known = (".pdf", ".svg")
        path = path.with_suffix("." + fmt) if path.suffix.lower() in known else Path(str(path) + "." + fmt)
    return path


def plot(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """打印主命令 PLOT：布局/窗口/比例/平铺/线宽现算，记一条作业（`dry_run=False` 时真落 PDF/SVG）。

    关键字（全部可选，裸调 `op(doc)` 必须能跑）：
    `sheet_uuid` / `layout`（布局名或 uuid）、`space`（只认 `"layout"`）、`area`（`extents|window`）、
    `window_nm`、`scale`（`"1:100"` 或 `"fit"`）、`fit_to_paper`、`copies`、`device`、
    `plot_style_table`、`lineweight_mm`、`fmt`（`pdf|svg`）、`out`/`out_path`/`output`、`dry_run`。
    返回 `[job_uuid]`（作业账进 L1 载荷）。
    """
    if sk_or_doc is None:
        raise _bad("bad_reference", "plot needs an L1 document or sketch")
    allowed = {"sheet_uuid", "sheet", "layout", "space", "area", "window_nm", "scale",
               "fit_to_paper", "copies", "device", "plot_style_table", "lineweight_mm",
               "fmt", "out", "out_path", "output", "dry_run"}
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise _bad("unknown_option", "unknown plot option", option=unknown[0], known=sorted(allowed))

    space = str(kwargs.get("space", "layout") or "layout").lower()
    if space != "layout":
        raise _bad("unsupported_space", "only layout space is implemented in this slice", space=space)

    sheets = _sheets(sk_or_doc)
    if not sheets:
        raise _bad("no_layout", "document has no layout sheet (model-space plot is a separate capability)")
    wanted = kwargs.get("sheet_uuid", kwargs.get("layout", kwargs.get("sheet")))
    sheet: Any = None
    if wanted is None:
        sheet = sheets[0]
    else:
        token = str(wanted)
        for item in sheets:
            if not isinstance(item, Mapping):
                continue
            if token in (str(item.get("uuid", "")), str(item.get("name", "")),
                         str(item.get("layout_name", ""))):
                sheet = item
                break
        if sheet is None:
            raise _bad("unknown_sheet", "no layout matches the requested sheet", sheet=token)
    if not isinstance(sheet, Mapping):
        raise _bad("bad_reference", "sheet entry must be a mapping", sheet=repr(sheet))

    device = str(kwargs.get("device", DEFAULT_DEVICE) or DEFAULT_DEVICE)
    if device != DEFAULT_DEVICE:
        raise _bad("unsupported_device", "plotter/pc3 configuration is a separate capability", device=device)

    style_table = kwargs.get("plot_style_table")
    if style_table is not None:
        name = str(style_table).strip()
        if not name.lower().endswith((".ctb", ".stb")):
            raise _bad("unsupported_style_table", "style table must name a .ctb/.stb",
                       plot_style_table=str(style_table))
        style_table = name

    if "paper" in kwargs:
        raise _bad("unknown_option", "paper override is not an option of this slice", option="paper")

    size_name = str(sheet.get("size", "A4")).upper()
    if size_name not in PAPER_SIZES_NM:
        raise _bad("unsupported_paper", "unknown paper size", size=size_name,
                   known=sorted(PAPER_SIZES_NM))
    paper_w, paper_h = PAPER_SIZES_NM[size_name]
    if "width_nm" in sheet and "height_nm" in sheet:
        paper_w = _int_nm(sheet["width_nm"], key="width_nm")
        paper_h = _int_nm(sheet["height_nm"], key="height_nm")
    if paper_w <= 2 * PRINT_MARGIN_NM or paper_h <= 2 * PRINT_MARGIN_NM:
        raise _bad("unsupported_paper", "paper is smaller than the print margin", size=size_name)
    usable_w, usable_h = paper_w - 2 * PRINT_MARGIN_NM, paper_h - 2 * PRINT_MARGIN_NM

    geoms = _iter_geom(sk_or_doc)
    if not geoms:
        raise _bad("no_model_geometry", "document has no geometry to plot")
    area = str(kwargs.get("area", "extents") or "extents").lower()
    if area not in SUPPORTED_AREAS:
        raise _bad("unsupported_area", "unsupported plot area", area=area, known=list(SUPPORTED_AREAS))
    if area == "window":
        raw = kwargs.get("window_nm")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 4:
            raise _bad("bad_window", "window_nm must be four nanometre values", window_nm=repr(raw))
        window = tuple(_int_nm(value, key="window_nm") for value in raw)
        if window[2] <= window[0] or window[3] <= window[1]:
            raise _bad("bad_window", "window has zero or negative extent", window_nm=list(window))
    else:
        window = _bbox(geoms)
    width_nm, height_nm = window[2] - window[0], window[3] - window[1]

    fit_to_paper = bool(kwargs.get("fit_to_paper", False))
    spec = kwargs.get("scale")
    if spec is None:
        spec = sheet.get("scale") or "1:1"
    ratio: tuple[int, int] | None = None
    if isinstance(spec, str) and spec.strip().lower() == "fit":
        if not fit_to_paper:
            raise _bad("bad_scale", "fit requires fit_to_paper=True", scale="fit")
        factor = min(float(usable_w) / float(width_nm), float(usable_h) / float(height_nm))
        scale_label, factor_exact = "fit", True
    else:
        ratio = _parse_scale(spec)
        factor = float(ratio[0]) / float(ratio[1])
        scale_label, factor_exact = "%d:%d" % ratio, True
    if factor <= 0.0:
        raise _bad("bad_scale", "scale resolves to a non-positive factor", scale=scale_label)

    copies = kwargs.get("copies", 1)
    if isinstance(copies, bool) or not isinstance(copies, int) or copies < 1:
        raise _bad("bad_copies", "copies must be a positive integer", copies=repr(copies))

    lineweight = kwargs.get("lineweight_mm", DEFAULT_LINEWEIGHT_MM)
    if isinstance(lineweight, bool) or not isinstance(lineweight, (int, float)) or float(lineweight) < 0.0:
        raise _bad("bad_lineweight", "lineweight_mm must be a non-negative number",
                   lineweight_mm=repr(lineweight))
    lineweight = round(float(lineweight), 4)

    fmt = str(kwargs.get("fmt", "pdf") or "pdf").strip().lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise _bad("unsupported_format", "unsupported plot format", fmt=fmt, known=list(SUPPORTED_FORMATS))

    dry_run = bool(kwargs.get("dry_run", True))
    out = kwargs.get("out", kwargs.get("out_path", kwargs.get("output", ""))) or ""
    out = str(out)
    if not dry_run and not out:
        raise _bad("missing_output", "dry_run=False requires an output path", fmt=fmt)

    draw_w, draw_h = float(width_nm) * factor, float(height_nm) * factor
    cols = max(1, int(-(-draw_w // float(usable_w))))
    rows = max(1, int(-(-draw_h // float(usable_h))))
    tiles = cols * rows
    pages = tiles * copies

    segments, arcs = _segments(geoms, window, factor)
    page_specs: list[tuple[float, float, list[tuple[float, float, float, float]], float]] = []
    for copy_index in range(copies):
        for row in range(rows):
            for col in range(cols):
                page_specs.append((float(paper_w), float(paper_h),
                                   _tile_page(segments, col, row, float(usable_w), float(usable_h)),
                                   lineweight))
    del copy_index  # 仅用于分页顺序，副本之间内容逐字节相同

    payload = _pdf_pages(page_specs) if fmt == "pdf" else _svg_pages(page_specs)

    job: dict[str, Any] = {
        "kind": "plot job",
        "space": space,
        "sheet_uuid": str(sheet.get("uuid", "")),
        "layout_name": str(sheet.get("name", sheet.get("layout_name", ""))),
        "device": device,
        "plot_style_table": style_table,
        "style_table_applied": False,
        "area": area,
        "window_nm": [int(value) for value in window],
        "scale": scale_label,
        "scale_ratio": None if ratio is None else [int(ratio[0]), int(ratio[1])],
        "factor_model_to_paper": round(factor, 12),
        "paper": {"name": size_name, "w_nm": int(paper_w), "h_nm": int(paper_h)},
        "printable_nm": {"w_nm": int(usable_w), "h_nm": int(usable_h)},
        "drawing_nm": {"w_nm": int(draw_w), "h_nm": int(draw_h)},
        "tiles": {"cols": cols, "rows": rows, "count": tiles},
        "pages": pages,
        "copies": copies,
        "lineweight_mm": lineweight,
        "fmt": fmt,
        "dry_run": dry_run,
        "rendered": False,
        "pdf_deterministic": fmt == "pdf",
        "arc_approximation": "circle->16-gon chord" if arcs else None,
    }
    if not dry_run:
        path = _out_path(out, fmt)
        try:
            if str(path.parent):
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError as exc:
            raise _bad("write_failed", "plot write failed: %s" % (exc,), path=str(path),
                       detail=str(exc)) from exc
        job["output"] = str(path.resolve())
        job["bytes"] = len(payload)
        job["sha256"] = hashlib.sha256(payload).hexdigest()
        job["rendered"] = True
    digest = hashlib.sha256(json.dumps(job, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    job["uuid"] = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore:output:plot:" + digest))
    _holder(sk_or_doc).append(job)
    return [job["uuid"]]


OPERATIONS: dict[str, Callable[..., list[str]]] = {"plot": plot}


def _selftest() -> int:
    """硬断言自检：真几何 + 真落盘 + 逐字节确定性 + 负例 reason。"""
    from cadcore.docmodel import Document

    checks: list[str] = []

    def expect(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError("selftest assertion failed: %s" % label)
        checks.append(label)

    sheet_uuid = "11111111-1111-4111-8111-111111111111"
    line_mm = 100_000_000          # 100 mm
    height_mm = 50_000_000         # 50 mm
    radius_mm = 10_000_000         # 10 mm

    def make_doc(scale_nm: int = line_mm) -> Any:
        doc = Document()
        doc.sheets.append({"uuid": sheet_uuid, "standard_profile": "iso", "size": "A4",
                           "scale": "1:1", "views": []})
        doc.sketches.append({
            "uuid": "22222222-2222-4222-8222-222222222222",
            "entities": [
                {"uuid": "33333333-3333-4333-8333-333333333333", "kind": "line",
                 "geom": {"x1": 0, "y1": 0, "x2": scale_nm, "y2": height_mm}},
                {"uuid": "44444444-4444-4444-8444-444444444444", "kind": "circle",
                 "geom": {"cx": scale_nm // 2, "cy": height_mm // 2, "r": radius_mm}},
            ],
        })
        return doc

    # A1 裸调 op(doc)：作业真进 L1 载荷，窗口 = 几何包围盒（逐纳米比对）
    doc = make_doc()
    ids = plot(doc)
    payload = doc.to_payload()
    jobs = payload["output"]["plot_jobs"]
    expect(isinstance(ids, list) and len(ids) == 1, "bare op(doc) returns one uuid")
    expect(jobs[0]["uuid"] == ids[0] and payload["output"]["plot_jobs"][0]["window_nm"]
           == [0, 0, line_mm, height_mm], "window_nm equals geometry bbox in the payload")
    expect(jobs[0]["rendered"] is False and jobs[0]["dry_run"] is True, "dry_run default writes nothing")

    # A2 真落盘 PDF + sha256 复算 + 逐字节确定性（两次不同路径同字节、同 uuid）
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        first = str(Path(tmp) / "a1")
        second = str(Path(tmp) / "copy2")
        doc1, doc2 = make_doc(), make_doc()
        job1 = plot(doc1, fmt="pdf", out=first, dry_run=False, area="window",
                    window_nm=[0, 0, line_mm, height_mm])[0]
        job2 = plot(doc2, fmt="pdf", out=second, dry_run=False, area="window",
                    window_nm=[0, 0, line_mm, height_mm])[0]
        rec1 = doc1.to_payload()["output"]["plot_jobs"][0]
        rec2 = doc2.to_payload()["output"]["plot_jobs"][0]
        blob1 = Path(rec1["output"]).read_bytes()
        blob2 = Path(rec2["output"]).read_bytes()
        expect(Path(rec1["output"]).name == "a1.pdf" and blob1.startswith(b"%PDF-1.4"),
               "real PDF bytes land on disk with the format extension")
        expect(rec1["sha256"] == hashlib.sha256(blob1).hexdigest() and rec1["bytes"] == len(blob1)
               and rec1["rendered"] is True, "recorded sha256/bytes recompute over the real file")
        expect(blob1 == blob2 and job1 == job2, "byte-identical and uuid-identical reruns")

        # A3 真平铺：1:1 下 1000mm x 600mm 在 A4 上切 6x3=18 页，copies=2 ⇒ 36 页，PDF 里真 36 个 /Type /Page
        big = make_doc(scale_nm=1_000_000_000)
        big.sketches[0]["entities"][0]["geom"]["y2"] = 600_000_000
        big_uuid = plot(big, scale="1:1", copies=2, fmt="pdf", out=str(Path(tmp) / "big"),
                        dry_run=False, area="window",
                        window_nm=[0, 0, 1_000_000_000, 600_000_000])[0]
        rec = big.to_payload()["output"]["plot_jobs"][0]
        expect(rec["uuid"] == big_uuid and rec["tiles"] == {"cols": 6, "rows": 3, "count": 18},
               "1:1 tiling of a 1000x600 mm drawing on A4 is 6x3 tiles")
        expect(rec["pages"] == 36 and Path(rec["output"]).read_bytes().count(b"/Type /Page ")
               == 36, "pages == tiles*copies and the PDF really carries 36 pages")

        # A4 比例是真换算：同窗口 1:10 ⇒ 页数塌成 1x1=1
        small = make_doc(scale_nm=1_000_000_000)
        small.sketches[0]["entities"][0]["geom"]["y2"] = 600_000_000
        plot(small, scale="1:10", fmt="pdf", out=str(Path(tmp) / "small"), dry_run=False,
             area="window", window_nm=[0, 0, 1_000_000_000, 600_000_000])
        expect(small.to_payload()["output"]["plot_jobs"][0]["tiles"]["count"] == 1,
               "1:10 scale shrinks the same window to a single tile")

    # A5/A6/A7 负例：reason 必须机器可读（没 geometry / 没布局 / 未知布局）
    def fail_reason(call: Callable[[], Any]) -> str:
        try:
            call()
        except CadError as exc:
            return exc.reason
        raise AssertionError("selftest assertion failed: expected CadError was not raised")

    empty = Document()
    empty.sheets.append({"uuid": sheet_uuid, "size": "A4", "scale": "1:1"})
    expect(fail_reason(lambda: plot(empty)) == "no_model_geometry",
           "empty model geometry fails with no_model_geometry")
    no_sheet = Document()
    expect(fail_reason(lambda: plot(no_sheet)) == "no_layout",
           "missing layout fails with no_layout (model space not claimed)")
    expect(fail_reason(lambda: plot(make_doc(), sheet_uuid="does-not-exist")) == "unknown_sheet",
           "unknown layout fails with unknown_sheet")
    expect(fail_reason(lambda: plot(make_doc(), space="model")) == "unsupported_space",
           "model-space plot is explicitly unsupported in this slice")

    print("FEATURE_OK family=%s op=plot verdict=PASS checks=%d assertions=%d passed=%d"
          % (FAMILY, len(checks), len(checks), len(checks)))
    return 0


def main(argv: Any = None) -> int:
    """`python3 cadcore/ops/features/output__w10.py` ⇒ 自检判定行（失败即非零退出）。"""
    del argv
    try:
        return _selftest()
    except AssertionError as exc:
        print("FEATURE_FAIL family=%s op=plot verdict=FAIL reason=selftest detail=%s" % (FAMILY, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
