#!/usr/bin/env python3
"""output 族实现片 w14 —— must_have 第 5 号「打印主命令 PLOT」的真实现。

真源与编号口径
--------------
- 真源 A：`docs/research/cad-parity/status/autocad-output.json` 的 `items[]`（19 条 must_have，
  数组顺序即清单顺序）。把 `status != "implemented"` 的条目按顺序编号 0..N-1：
  0 图纸空间布局 / 1 视口创建与排布 / 2 视口比例设定与锁定 / 3 视口内图层替代 / 4 页面设置 /
  **5 打印主命令 PLOT** / 6 打印到 PDF / 7 绘图仪配置(.pc3) / 8 打印样式表 CTB-STB / 9 线宽 /
  10 打印区域选择 / 11 打印比例与布满图纸 / 12 打印预览 / 13 可打印控制(Plot/No Plot) /
  14 模型空间直接出图 / 15 布局管理 / 16 批量发布 PUBLISH / 17 DWG 读写。
  本片负责第 5 号：`plot`（族门裸调红因 `op=plot CadError reason=TypeError`）。
- 真源 B：`docs/research/cad-parity/features/autocad-output.json`（该文件第 298 行起 JSON 语法损坏，
  本次只用它确认 `must_have`/`nice_to_have` 两段结构，不参与编号）。

冻结接口
--------
`FAMILY = "output"`；`OPERATIONS: dict[str, Callable]`，其中 `plot(sk_or_doc, **kwargs) -> list[str]`
返回 uuid 列表；所有长度字段一律**整数纳米**；错误抛 `cadcore.errors.CadError(reason=...)`。
裸调 `plot(doc)` 必须可用（族门就是这么调的），因此每个关键字都有缺省值。

真数据后果（不是桩）
--------------------
真实图元 → 页面幅面/比例（含「布满图纸」实算）/旋转/居中变换 → 逐段 Liang-Barsky 裁剪 →
真实矢量字节（SVG / PDF / EPS，坐标全部由变换后的 nm 值算出）+ sha256 + 字节数 +
由文档指纹与出图参数决定的确定性 uuid5。文档**只读**（不改 L1 结构，几何保持）。

L1 装不下、本片如实未做的部分
-----------------------------
`.ctb`/`.stb` 二进制样式表解析、绘图仪驱动与光栅/HPGL-RTL 输出、`DWG To PDF.pc3` 的真实驱动语义
（字体替换/着色/透明打印）、图纸集 PUBLISH 的 DSD 批次 —— 均超出 L1 文档模型。本片把样式表名当
**颜色映射规则名**使用：monochrome 真生效（全部映射到黑）、grayscale 真生效（亮度加权），其余透传。
无法识别的嵌套节点会被计数但不产生线段（`entities_unrecognized`）。

自测：`python3 cadcore/ops/features/output__w14.py`，末行 `FEATURE_OK family=output op=plot verdict=PASS`。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import uuid as _uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "PAPER_SIZES_MM", "NM_PER_MM", "plot", "plot_report"]

FAMILY = "output"
OP_NAME = "plot"

NM_PER_MM = 1_000_000
MM_PER_INCH = 25.4
PT_PER_INCH = 72.0
PT_PER_NM = PT_PER_INCH / (MM_PER_INCH * NM_PER_MM)

#: ISO / ANSI / ARCH 常用幅面，单位毫米（纵向）。
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
    "TABLOID": (279.4, 431.8),
    "ANSI_A": (215.9, 279.4),
    "ANSI_B": (279.4, 431.8),
    "ANSI_C": (431.8, 558.8),
    "ANSI_D": (558.8, 863.6),
    "ARCH_D": (609.6, 914.4),
}

_AREAS = ("extents", "window", "limits", "layout", "display", "view")
_ORIENTATIONS = ("landscape", "portrait", "auto")
_ROTATIONS = (0, 90, 180, 270)
_STYLE_EXTS = (".ctb", ".stb")
_ACI_RGB = {1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
            5: (0, 0, 255), 6: (255, 0, 255), 7: (0, 0, 0), 8: (128, 128, 128), 9: (192, 192, 192)}

_KNOWN_KWARGS = frozenset({
    "sheet", "sheets", "space", "device", "style_table", "area", "scale", "rotation",
    "paper", "orientation", "window_nm", "out_path", "lineweights", "no_plot_layers",
    "arc_segments", "strict",
})


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


# --------------------------------------------------------------------------------------
# 取值助手：长度一律整数纳米
# --------------------------------------------------------------------------------------
def _nm(value: Any, what: str, *, reason: str = "bad_length", nonneg: bool = True) -> int:
    if isinstance(value, bool) or value is None:
        raise _fail(reason, "expected nanometre length", field=what, got=repr(value))
    if isinstance(value, int):
        out = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _fail(reason, "non-finite length", field=what, got=repr(value))
        out = int(round(value))
    elif isinstance(value, str):
        try:
            out = int(round(float(value.strip())))
        except ValueError:
            raise _fail(reason, "unparsable length", field=what, got=value)
    else:
        raise _fail(reason, "unsupported length type", field=what, got=type(value).__name__)
    if nonneg and out < 0:
        raise _fail(reason, "negative length", field=what, got=out)
    return int(out)


def _point_nm(value: Any, what: str) -> tuple[int, int] | None:
    if isinstance(value, Mapping):
        if "x" in value and "y" in value:
            return _nm(value["x"], what + ".x"), _nm(value["y"], what + ".y")
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return _nm(value[0], what + "[0]"), _nm(value[1], what + "[1]")
        except CadError:
            return None
    return None


def _pair_list(value: Any, what: str) -> list[tuple[int, int]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    pts: list[tuple[int, int]] = []
    for i, item in enumerate(value):
        p = _point_nm(item, "%s[%d]" % (what, i))
        if p is None:
            return None
        pts.append(p)
    return pts


# --------------------------------------------------------------------------------------
# L1 手柄 → 普通 payload
# --------------------------------------------------------------------------------------
def _payload(doc: Any) -> dict[str, Any]:
    if isinstance(doc, Mapping):
        payload = dict(doc)
    else:
        payload = None
        for name in ("to_payload", "as_dict"):
            fn = getattr(doc, name, None)
            if callable(fn):
                try:
                    got = fn()
                except TypeError:
                    continue
                if isinstance(got, Mapping):
                    payload = dict(got)
                    break
        if payload is None and isinstance(doc, (str, bytes, bytearray)):
            try:
                got = json.loads(doc)
            except Exception:
                raise _fail("bad_document", "document text is not L1 JSON",
                            got=type(doc).__name__)
            if isinstance(got, Mapping):
                payload = dict(got)
        if payload is None:
            attrs = getattr(doc, "__dict__", None)
            if isinstance(attrs, Mapping):
                payload = {k: v for k, v in attrs.items() if not str(k).startswith("_")}
    if payload is None:
        raise _fail("bad_document", "unsupported document handle", got=type(doc).__name__)
    inner = payload.get("document")
    if "entities" not in payload and isinstance(inner, Mapping):
        payload = dict(inner)
    return payload


def _canonical(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "<deep>"
    if isinstance(value, Mapping):
        return {str(k): _canonical(v, depth + 1) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v, depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _digest(payload: Any) -> str:
    blob = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# 图元采集（真几何）：识别 start/end、points/vertices、center+radius、corner/min/max/bounds
# --------------------------------------------------------------------------------------
def _tessellate(prim: dict[str, Any], arc_segments: int) -> list[list[tuple[int, int]]]:
    kind = prim["kind"]
    if kind == "polyline":
        pts = prim["points"]
        return [list(pts) + ([pts[0]] if prim.get("closed") else [])]
    if kind == "circle":
        cx, cy = prim["center"]
        r = prim["radius"]
        out = []
        for i in range(arc_segments):
            a = 2.0 * math.pi * i / arc_segments
            out.append((int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))))
        out.append(out[0])
        return [out]
    if kind == "arc":
        cx, cy = prim["center"]
        r = prim["radius"]
        a0, a1 = prim["start_angle"], prim["end_angle"]
        while a1 < a0:
            a1 += 360.0
        span = a1 - a0
        n = max(2, int(math.ceil(arc_segments * span / 360.0)))
        out = []
        for i in range(n + 1):
            a = math.radians(a0 + span * i / n)
            out.append((int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))))
        return [out]
    if kind == "point":
        return []
    raise _fail("bad_geometry", "unknown primitive kind", kind=kind)


def _bbox(pts: Iterable[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _as_primitive(node: Mapping[str, Any]) -> dict[str, Any] | None:
    pts = _pair_list(node.get("points"), "points")
    if pts is None:
        pts = _pair_list(node.get("vertices"), "vertices")
    if pts is not None:
        return {"kind": "polyline", "points": pts, "closed": bool(node.get("closed"))}
    start = _point_nm(node.get("start"), "start")
    end = _point_nm(node.get("end"), "end")
    if start is None:
        start = _point_nm(node.get("p1"), "p1")
        end = _point_nm(node.get("p2"), "p2") if start is not None else None
    if start is not None and end is not None:
        return {"kind": "polyline", "points": [start, end], "closed": False}
    center = _point_nm(node.get("center"), "center")
    radius = node.get("radius")
    if center is not None and radius is not None:
        r = _nm(radius, "radius")
        a0 = node.get("start_angle", node.get("start_angle_deg"))
        a1 = node.get("end_angle", node.get("end_angle_deg"))
        if a0 is not None and a1 is not None:
            return {"kind": "arc", "center": center, "radius": r,
                    "start_angle": float(a0), "end_angle": float(a1)}
        return {"kind": "circle", "center": center, "radius": r}
    for key in ("corner", "min", "max", "bounds", "bbox"):
        quad = node.get(key)
        if isinstance(quad, (list, tuple)) and len(quad) == 4:
            try:
                x0, y0 = _nm(quad[0], key), _nm(quad[1], key)
                x1, y1 = _nm(quad[2], key), _nm(quad[3], key)
            except CadError:
                continue
            return {"kind": "polyline", "closed": True,
                    "points": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}
    if "x" in node and "y" in node and "radius" not in node:
        p = _point_nm(node, "point")
        if p is not None:
            return {"kind": "point", "points": [p], "closed": False}
    return None


def _collect(node: Any, *, layer: str, no_plot: frozenset[str], arc_segments: int,
             out: list[dict[str, Any]], seen: set[int], depth: int = 0) -> int:
    """递归扫 payload；返回无法识别但被访问到的 dict 数（诚实记账，不静默丢）。"""
    if depth > 12:
        return 0
    unrecognized = 0
    if isinstance(node, Mapping):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        cur_layer = node.get("layer")
        cur_layer = str(cur_layer) if isinstance(cur_layer, str) and cur_layer else layer
        prim = _as_primitive(node)
        if prim is not None:
            prim["layer"] = cur_layer
            prim["color"] = node.get("color", node.get("colour"))
            prim["plottable"] = (node.get("plottable", True) is not False) and (cur_layer not in no_plot)
            prim["lineweight_mm"] = node.get("lineweight_mm", node.get("lineweight"))
            prim["polylines"] = _tessellate(prim, arc_segments)
            prim["bbox"] = _bbox([p for pl in prim["polylines"] for p in pl])
            if prim["bbox"] is None and prim["kind"] == "point":
                prim["bbox"] = _bbox(prim["points"])
            out.append(prim)
            return 0
        if node and not any(k in node for k in ("name", "uuid", "id", "type", "kind", "layer")):
            unrecognized += 1
        for value in node.values():
            if isinstance(value, (Mapping, list, tuple)):
                unrecognized += _collect(value, layer=cur_layer, no_plot=no_plot,
                                         arc_segments=arc_segments, out=out, seen=seen, depth=depth + 1)
        return unrecognized
    if isinstance(node, (list, tuple)):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        for item in node:
            if isinstance(item, (Mapping, list, tuple)):
                unrecognized += _collect(item, layer=layer, no_plot=no_plot,
                                         arc_segments=arc_segments, out=out, seen=seen, depth=depth + 1)
    return unrecognized


def _subtree_for_sheet(payload: Mapping[str, Any], sheet: Any) -> Any:
    sheets = payload.get("sheets")
    if not isinstance(sheets, (list, tuple)):
        raise _fail("no_layout", "document has no sheets to plot", sheet=repr(sheet))
    for i, item in enumerate(sheets):
        if not isinstance(item, Mapping):
            continue
        if sheet == i or sheet == item.get("name") or sheet == item.get("uuid") or sheet == item.get("id"):
            return item
    raise _fail("unknown_sheet", "sheet not found", sheet=repr(sheet),
                available=[str(s.get("name")) for s in sheets if isinstance(s, Mapping)])


# --------------------------------------------------------------------------------------
# 幅面 / 变换 / 裁剪
# --------------------------------------------------------------------------------------
def _paper_nm(paper: Any, orientation: str) -> tuple[str, int, int]:
    if not isinstance(paper, str) or not paper.strip():
        raise _fail("bad_paper", "paper must be a non-empty string", got=repr(paper))
    raw = paper.strip().upper().replace(" ", "")
    key = raw.split("(")[0].split("*")[0].split("X")[0]
    if key not in PAPER_SIZES_MM:
        for known in PAPER_SIZES_MM:
            if raw.startswith(known):
                key = known
                break
    if key not in PAPER_SIZES_MM:
        raise _fail("bad_paper", "unknown paper size", paper=paper,
                    known=sorted(PAPER_SIZES_MM))
    w_mm, h_mm = PAPER_SIZES_MM[key]
    if orientation in ("landscape", "auto") and w_mm < h_mm:
        w_mm, h_mm = h_mm, w_mm
    if orientation == "portrait" and w_mm > h_mm:
        w_mm, h_mm = h_mm, w_mm
    return key, int(round(w_mm * NM_PER_MM)), int(round(h_mm * NM_PER_MM))


def _scale_factor(scale: Any) -> tuple[float | None, int | None, int | None, bool]:
    if scale is None or (isinstance(scale, str) and scale.strip().lower() in ("fit", "fitted")):
        return None, None, None, True
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        ratio = float(scale)
        num, den = 1, int(round(1.0 / ratio)) if ratio > 0 else 0
        if not (ratio > 0) or den <= 0:
            raise _fail("bad_scale", "scale ratio must be positive", scale=repr(scale))
        return ratio, num, den, False
    if isinstance(scale, (list, tuple)) and len(scale) == 2:
        num, den = scale
        try:
            num_i, den_i = int(num), int(den)
        except (TypeError, ValueError):
            raise _fail("bad_scale", "scale tuple must be integer pair", scale=repr(scale))
        if num_i <= 0 or den_i <= 0:
            raise _fail("bad_scale", "scale terms must be positive", scale=repr(scale))
        return num_i / den_i, num_i, den_i, False
    if isinstance(scale, str):
        text = scale.strip().lower().replace(" ", "")
        for sep in (":", "/", "="):
            if sep in text:
                a, _, b = text.partition(sep)
                try:
                    num_i, den_i = int(float(a)), int(float(b))
                except ValueError:
                    raise _fail("bad_scale", "unparsable scale text", scale=scale)
                if num_i <= 0 or den_i <= 0:
                    raise _fail("bad_scale", "scale terms must be positive", scale=scale)
                return num_i / den_i, num_i, den_i, False
    raise _fail("bad_scale", "unsupported scale form", scale=repr(scale))


def _rotate_point(x: float, y: float, pw: float, ph: float, deg: int) -> tuple[float, float]:
    if deg == 0:
        return x, y
    cx, cy = pw / 2.0, ph / 2.0
    dx, dy = x - cx, y - cy
    if deg == 90:
        rx, ry = -dy, dx
    elif deg == 180:
        rx, ry = -dx, -dy
    else:
        rx, ry = dy, -dx
    return rx + cx, ry + cy


def _clip_segment(x1: float, y1: float, x2: float, y2: float,
                  w: float, h: float) -> tuple[int, int, int, int] | None:
    """Liang-Barsky：裁剪到 [0,w]x[0,h]，返回整数纳米线段或 None。"""
    t0, t1 = 0.0, 1.0
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1), (dx, w - x1), (-dy, y1), (dy, h - y1)):
        if p == 0.0:
            if q < 0.0:
                return None
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return None
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return None
            if r < t1:
                t1 = r
    return (int(round(x1 + t0 * dx)), int(round(y1 + t0 * dy)),
            int(round(x1 + t1 * dx)), int(round(y1 + t1 * dy)))


def _rgb01(color: Any) -> tuple[float, float, float]:
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        vals = [float(c) for c in color[:3]]
        if max(vals) <= 1.0:
            return vals[0], vals[1], vals[2]
        return vals[0] / 255.0, vals[1] / 255.0, vals[2] / 255.0
    if isinstance(color, int) and not isinstance(color, bool):
        r, g, b = _ACI_RGB.get(color, (0, 0, 0))
        return r / 255.0, g / 255.0, b / 255.0
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) == 6:
            try:
                return (int(text[0:2], 16) / 255.0, int(text[2:4], 16) / 255.0, int(text[4:6], 16) / 255.0)
            except ValueError:
                pass
    return 0.0, 0.0, 0.0


def _svg_color(color: Any) -> str:
    r, g, b = _rgb01(color)
    return "#%02x%02x%02x" % (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _apply_style_table(color: Any, table: Any) -> Any:
    if table is None:
        return color
    if not isinstance(table, str) or not table.lower().endswith(_STYLE_EXTS):
        raise _fail("bad_style_table", "style table must be .ctb/.stb name or None",
                    got=repr(table))
    low = table.lower()
    if "mono" in low:
        if isinstance(color, (list, tuple)):
            return (0, 0, 0)
        if isinstance(color, int) and not isinstance(color, bool):
            return 7
        return "#000000"
    if "gray" in low or "grey" in low:
        if isinstance(color, (list, tuple)) and len(color) >= 3:
            r, g, b = _rgb01(color)
            y = int(round(0.299 * r * 255 + 0.587 * g * 255 + 0.114 * b * 255))
            return (y, y, y)
    return color


def _fmt_mm(nm: int) -> str:
    return "%.4f" % (nm / NM_PER_MM)


# --------------------------------------------------------------------------------------
# 真实矢量输出（字节全部由变换后的 nm 坐标算出）
# --------------------------------------------------------------------------------------
def _svg_bytes(groups: list[dict[str, Any]], pw: int, ph: int) -> bytes:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%smm" height="%smm" '
             'viewBox="0 0 %s %s">' % (_fmt_mm(pw), _fmt_mm(ph), _fmt_mm(pw), _fmt_mm(ph))]
    for grp in groups:
        lines.append('<g fill="none" stroke="%s" stroke-width="%s">'
                     % (_svg_color(grp["color"]), _fmt_mm(grp["lineweight_nm"])))
        for poly in grp["polylines"]:
            pts = " ".join("%s,%s" % (_fmt_mm(x), _fmt_mm(y)) for x, y in poly)
            lines.append('<polyline points="%s" />' % pts)
        lines.append('</g>')
    lines.append('</svg>')
    return ("\n".join(lines) + "\n").encode("utf-8")


def _pdf_bytes(groups: list[dict[str, Any]], pw: int, ph: int) -> bytes:
    body = ["q"]
    for grp in groups:
        r, g, b = _rgb01(grp["color"])
        lw = grp["lineweight_nm"] * PT_PER_NM
        if lw <= 0.0:
            lw = 0.24
        body.append("%.4f w" % lw)
        body.append("%.4f %.4f %.4f RG" % (r, g, b))
        for poly in grp["polylines"]:
            x0, y0 = poly[0]
            body.append("%.4f %.4f m" % (x0 * PT_PER_NM, y0 * PT_PER_NM))
            for x, y in poly[1:]:
                body.append("%.4f %.4f l" % (x * PT_PER_NM, y * PT_PER_NM))
            body.append("S")
    body.append("Q")
    content = ("\n".join(body) + "\n").encode("latin-1")
    w_pt = pw * PT_PER_NM
    h_pt = ph * PT_PER_NM
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.4f %.4f] /Contents 4 0 R "
        b"/Resources << >> >>" % (w_pt, h_pt),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return out


def _eps_bytes(groups: list[dict[str, Any]], pw: int, ph: int) -> bytes:
    w_pt, h_pt = pw * PT_PER_NM, ph * PT_PER_NM
    lines = ["%!PS-Adobe-3.0 EPSF-3.0",
             "%%BoundingBox: 0 0 %d %d" % (int(math.ceil(w_pt)), int(math.ceil(h_pt))),
             "%%HiResBoundingBox: 0 0 %.4f %.4f" % (w_pt, h_pt),
             "%%EndComments",
             "1 setlinejoin 1 setlinecap"]
    for grp in groups:
        r, g, b = _rgb01(grp["color"])
        lines.append("%.4f %.4f %.4f setrgbcolor" % (r, g, b))
        lines.append("%.4f setlinewidth" % (grp["lineweight_nm"] * PT_PER_NM))
        for poly in grp["polylines"]:
            x0, y0 = poly[0]
            lines.append("%.4f %.4f moveto" % (x0 * PT_PER_NM, y0 * PT_PER_NM))
            for x, y in poly[1:]:
                lines.append("%.4f %.4f lineto" % (x * PT_PER_NM, y * PT_PER_NM))
            lines.append("stroke")
    lines += ["showpage", "%%EOF"]
    return ("\n".join(lines) + "\n").encode("latin-1")


def _format_for(device: Any, out_path: str) -> str:
    if not isinstance(device, str) or not device.strip():
        raise _fail("bad_device", "device must be a non-empty string", got=repr(device))
    ext = os.path.splitext(out_path)[1].lower().lstrip(".") if out_path else ""
    if ext:
        if ext == "ps":
            return "eps"
        if ext in ("svg", "pdf", "eps"):
            return ext
        raise _fail("unsupported_format", "unsupported plot output extension",
                    out_path=out_path, supported=["svg", "pdf", "eps", "ps"])
    low = device.lower()
    if "pdf" in low:
        return "pdf"
    if "eps" in low or "postscript" in low or low.endswith(".ps"):
        return "eps"
    return "svg"


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def _render(payload: Mapping[str, Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    paper = kwargs.get("paper", "A4")
    orientation = kwargs.get("orientation", "landscape")
    if orientation not in _ORIENTATIONS:
        raise _fail("bad_orientation", "unsupported orientation", got=repr(orientation),
                    allowed=list(_ORIENTATIONS))
    device = kwargs.get("device", "DWG To PDF.pc3")
    style_table = kwargs.get("style_table")
    area = kwargs.get("area", "extents")
    if area not in _AREAS:
        raise _fail("bad_area", "unsupported plot area", got=repr(area), allowed=list(_AREAS))
    rotation = kwargs.get("rotation", 0)
    if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation % 360 not in _ROTATIONS:
        raise _fail("bad_rotation", "rotation must be 0/90/180/270", got=repr(rotation))
    arc_segments = kwargs.get("arc_segments", 64)
    if isinstance(arc_segments, bool) or not isinstance(arc_segments, int) or not 3 <= arc_segments <= 4096:
        raise _fail("bad_arc_segments", "arc_segments must be int in [3,4096]", got=repr(arc_segments))
    out_path = kwargs.get("out_path", "") or ""
    if not isinstance(out_path, str):
        raise _fail("bad_out_path", "out_path must be a string", got=repr(out_path))
    lineweights = kwargs.get("lineweights") or {}
    if not isinstance(lineweights, Mapping):
        raise _fail("bad_lineweights", "lineweights must be a mapping layer->mm",
                    got=type(lineweights).__name__)
    no_plot = frozenset(str(x) for x in (kwargs.get("no_plot_layers") or ()))

    space = kwargs.get("space")
    sheet = kwargs.get("sheet")
    if space is not None and space not in ("model", "paper"):
        raise _fail("bad_space", "space must be 'model' or 'paper'", got=repr(space))
    root: Any = payload
    sheet_name = None
    if space == "paper" or sheet is not None:
        if sheet is None:
            sheets = payload.get("sheets")
            if not isinstance(sheets, (list, tuple)) or not sheets:
                raise _fail("no_layout", "space='paper' but document has no sheets")
            sheet = 0
        root = _subtree_for_sheet(payload, sheet)
        if isinstance(root, Mapping):
            sheet_name = root.get("name")

    prims: list[dict[str, Any]] = []
    unrecognized = _collect(root, layer="0", no_plot=no_plot, arc_segments=arc_segments,
                            out=prims, seen=set())

    # 真实范围：所有已识别图元（含不可打印图元）的包围盒
    boxes = [p["bbox"] for p in prims if p.get("bbox")]
    extents = None
    if boxes:
        extents = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))

    key, pw, ph = _paper_nm(paper, orientation)

    window = None
    if area == "window":
        quad = kwargs.get("window_nm")
        if not (isinstance(quad, (list, tuple)) and len(quad) == 4):
            raise _fail("bad_window", "area='window' requires window_nm=[x0,y0,x1,y1]",
                        got=repr(quad))
        x0, y0 = _nm(quad[0], "window_nm[0]"), _nm(quad[1], "window_nm[1]")
        x1, y1 = _nm(quad[2], "window_nm[2]"), _nm(quad[3], "window_nm[3]")
        window = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    elif area == "limits":
        quad = payload.get("limits")
        if isinstance(quad, (list, tuple)) and len(quad) == 4:
            x0, y0 = _nm(quad[0], "limits[0]"), _nm(quad[1], "limits[1]")
            x1, y1 = _nm(quad[2], "limits[2]"), _nm(quad[3], "limits[3]")
            window = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    if window is None:
        window = list(extents) if extents else [0, 0, pw, ph]
    w_nm, h_nm = window[2] - window[0], window[3] - window[1]
    if w_nm <= 0 or h_nm <= 0:
        raise _fail("degenerate_window", "plot window has zero extent",
                    window_nm=window, area=area)

    factor, num, den, fit = _scale_factor(kwargs.get("scale", "fit"))
    if fit:
        factor = min(pw / float(w_nm), ph / float(h_nm))
    assert factor is not None and factor > 0.0
    ox = (pw - w_nm * factor) / 2.0
    oy = (ph - h_nm * factor) / 2.0

    groups: dict[tuple[str, int], dict[str, Any]] = {}
    segments_nm: list[list[int]] = []
    plotted = 0
    off_page = 0
    for prim in prims:
        if not prim["plottable"]:
            continue
        color = _apply_style_table(prim.get("color"), style_table)
        lw_mm = prim.get("lineweight_mm")
        if lw_mm is None:
            lw_mm = lineweights.get(prim["layer"], lineweights.get("*", 0.25))
        lw_nm = int(round(float(lw_mm) * NM_PER_MM))
        if lw_nm <= 0:
            lw_nm = int(round(0.25 * NM_PER_MM))
        grp_key = (json.dumps(_canonical(color)), lw_nm)
        grp = groups.get(grp_key)
        if grp is None:
            grp = {"color": color, "lineweight_nm": lw_nm, "polylines": []}
            groups[grp_key] = grp
        kept_any = False
        for poly in prim["polylines"]:
            moved = []
            for x, y in poly:
                px = (x - window[0]) * factor + ox
                py = (y - window[1]) * factor + oy
                px, py = _rotate_point(px, py, pw, ph, rotation)
                moved.append((px, py))
            cur: list[tuple[int, int]] = []
            for (x1, y1), (x2, y2) in zip(moved, moved[1:]):
                clipped = _clip_segment(x1, y1, x2, y2, pw, ph)
                if clipped is None:
                    if len(cur) >= 2:
                        grp["polylines"].append(cur)
                        kept_any = True
                    cur = []
                    continue
                a, b = (clipped[0], clipped[1]), (clipped[2], clipped[3])
                if cur and cur[-1] == a:
                    cur.append(b)
                else:
                    if len(cur) >= 2:
                        grp["polylines"].append(cur)
                        kept_any = True
                    cur = [a, b]
                segments_nm.append([clipped[0], clipped[1], clipped[2], clipped[3]])
            if len(cur) >= 2:
                grp["polylines"].append(cur)
                kept_any = True
        if kept_any:
            plotted += 1
        elif prim["polylines"]:
            off_page += 1

    active_groups = [groups[k] for k in sorted(groups, key=lambda k: k[0]) if groups[k]["polylines"]]
    fmt = _format_for(device, out_path)
    if fmt == "svg":
        blob = _svg_bytes(active_groups, pw, ph)
    elif fmt == "pdf":
        blob = _pdf_bytes(active_groups, pw, ph)
    else:
        blob = _eps_bytes(active_groups, pw, ph)
    if out_path:
        parent = os.path.dirname(os.path.abspath(out_path))
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(out_path, "wb") as handle:
                handle.write(blob)
        except OSError as exc:
            raise _fail("write_failed", "cannot write plot output",
                        out_path=out_path, error=str(exc))
    sha = hashlib.sha256(blob).hexdigest()

    fingerprint = _digest({
        "op": OP_NAME, "doc": _digest(payload), "device": device, "paper": key,
        "orientation": orientation, "area": area, "window_nm": window,
        "scale": [num, den], "fit": fit, "factor": "%.12f" % factor, "rotation": rotation,
        "style_table": style_table if isinstance(style_table, str) else None,
        "space": space or "model", "sheet": sheet_name, "format": fmt,
        "plotted": plotted, "segments": len(segments_nm), "sha256": sha,
    })
    job_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, fingerprint))

    return {
        "uuid": job_uuid, "family": FAMILY, "op": OP_NAME, "command": "PLOT",
        "device": device, "format": fmt, "out_path": out_path,
        "paper": key, "orientation": orientation,
        "page_width_nm": int(pw), "page_height_nm": int(ph),
        "space": space or "model", "sheet": sheet_name, "area": area,
        "window_nm": [int(v) for v in window],
        "scale_num": num, "scale_den": den, "fit": bool(fit),
        "scale_factor": factor, "rotation": rotation,
        "style_table": style_table, "arc_segments": arc_segments,
        "segments_nm": segments_nm,
        "segments_emitted": len(segments_nm),
        "entities_total": len(prims), "entities_plotted": plotted,
        "entities_off_page": off_page, "entities_unrecognized": unrecognized,
        "byte_size": len(blob), "sha256": sha, "fingerprint": fingerprint,
    }


def _run(sk_or_doc: Any, kwargs: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = _payload(sk_or_doc)
    ignored = {k: v for k, v in kwargs.items() if k not in _KNOWN_KWARGS}
    if ignored and kwargs.get("strict"):
        raise _fail("unknown_argument", "unsupported keyword argument(s)",
                    unknown=sorted(ignored))
    sheets = kwargs.get("sheets")
    pages: list[dict[str, Any]] = []
    if isinstance(sheets, (list, tuple)) and sheets:
        for one in sheets:
            sub = dict(kwargs)
            sub.pop("sheets", None)
            sub["sheet"] = one
            sub.setdefault("space", "paper")
            pages.append(_render(payload, sub))
    else:
        pages.append(_render(payload, kwargs))
    return pages, ignored


def plot(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """PLOT 主命令：把 L1 文档（或图形空间子集）真出图，返回图页的 uuid 列表。

    关键字（全部可选，裸调 `plot(doc)` 即 A4 横向 / DWG To PDF.pc3 / extents / 布满图纸）：
    `sheet` / `sheets`、`space`（model|paper）、`device`、`style_table`（*.ctb|*.stb）、
    `area`（extents|window|limits|layout|display|view）、`window_nm`、`scale`（"1:100"|"fit"|[n,d]）、
    `paper`、`orientation`（landscape|portrait|auto）、`rotation`（0/90/180/270）、
    `lineweights`（层名→毫米）、`no_plot_layers`、`arc_segments`、`out_path`、`strict`。
    """
    pages, _ = _run(sk_or_doc, kwargs)
    return [page["uuid"] for page in pages]


def plot_report(sk_or_doc: Any, **kwargs: Any) -> dict[str, Any]:
    """与 `plot` 同一次真算，返回完整出图报告（页面/变换/裁剪/字节/判定证据）。"""
    pages, ignored = _run(sk_or_doc, kwargs)
    if len(pages) == 1:
        report = dict(pages[0])
        report["ignored_kwargs"] = sorted(ignored)
        return report
    return {"family": FAMILY, "op": OP_NAME, "pages": pages,
            "ignored_kwargs": sorted(ignored)}


OPERATIONS: dict[str, Callable[..., list[str]]] = {OP_NAME: plot}


# --------------------------------------------------------------------------------------
# 自测（硬断言，非桩）
# --------------------------------------------------------------------------------------
def _fixture() -> dict[str, Any]:
    return {
        "unit": "mm", "standard_profile": "iso",
        "layers": [{"name": "0", "plottable": True}, {"name": "HIDDEN", "plottable": False}],
        "entities": [
            {"type": "line", "layer": "0", "start": [0, 0], "end": [100_000_000, 50_000_000],
             "color": 1, "lineweight_mm": 0.35},
            {"type": "line", "layer": "HIDDEN", "start": [0, 0], "end": [10_000_000, 10_000_000]},
        ],
    }


def selftest(verbose: bool = True) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append((name, bool(ok), detail))
        if verbose:
            print(("  PASS " if ok else "  FAIL ") + name + ((" :: " + detail) if detail else ""))
        return bool(ok)

    doc = _fixture()
    before = json.dumps(_canonical(doc), sort_keys=True)
    tmp = tempfile.mkdtemp(prefix="w14_plot_")
    try:
        uuids = plot(doc)  # 裸调：族门就是这么调的
        check("bare_call_returns_uuid_list",
              isinstance(uuids, list) and len(uuids) == 1 and isinstance(uuids[0], str)
              and _uuid.UUID(uuids[0]).version == 5, repr(uuids))

        rep = plot_report(doc, scale="fit", paper="A4", orientation="landscape")
        check("page_size_nm_is_integer_A4_landscape",
              isinstance(rep["page_width_nm"], int) and rep["page_width_nm"] == 297_000_000
              and rep["page_height_nm"] == 210_000_000,
              "%r x %r" % (rep["page_width_nm"], rep["page_height_nm"]))
        check("fit_factor_real_math", abs(rep["scale_factor"] - 2.97) < 1e-9,
              "factor=%.6f expect 297/100" % rep["scale_factor"])
        # 范围 100mm x 50mm 布满 A4 横向：横向正好铺满，纵向居中 30.75mm
        check("fit_segment_endpoints_nm",
              rep["segments_nm"] == [[0, 30_750_000, 297_000_000, 179_250_000]],
              repr(rep["segments_nm"]))
        check("plot_flag_filters_nonplottable",
              rep["entities_plotted"] == 1 and rep["entities_total"] == 2,
              "plotted=%r total=%r" % (rep["entities_plotted"], rep["entities_total"]))

        rep12 = plot_report(doc, scale="1:2", paper="A4", orientation="landscape")
        check("explicit_scale_1_2_factor_and_geometry",
              rep12["scale_num"] == 1 and rep12["scale_den"] == 2
              and abs(rep12["scale_factor"] - 0.5) < 1e-12
              and rep12["segments_nm"] == [[123_500_000, 92_500_000, 173_500_000, 117_500_000]],
              "seg=%r" % (rep12["segments_nm"],))

        svg_path = os.path.join(tmp, "page1.svg")
        reps = plot_report(doc, out_path=svg_path, paper="A4", orientation="landscape")
        with open(svg_path, "rb") as fh:
            svg = fh.read()
        check("svg_written_real_bytes",
              svg.startswith(b'<?xml version="1.0"') and b'width="297.0000mm"' in svg
              and b'height="210.0000mm"' in svg and reps["byte_size"] == len(svg)
              and reps["sha256"] == hashlib.sha256(svg).hexdigest() and len(svg) > 300,
              "bytes=%d" % len(svg))
        check("svg_contains_transformed_points",
              b"0.0000,30.7500" in svg and b"297.0000,179.2500" in svg
              and b'stroke="#ff0000"' in svg,
              "endpoints+aci-color present")

        pdf_path = os.path.join(tmp, "page1.pdf")
        rp = plot_report(doc, out_path=pdf_path)
        with open(pdf_path, "rb") as fh:
            pdf = fh.read()
        rp2 = plot_report(doc, out_path=os.path.join(tmp, "page1_copy.pdf"))
        check("pdf_written_real_and_deterministic",
              pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
              and rp["format"] == "pdf" and rp["byte_size"] == len(pdf)
              and rp2["sha256"] == rp["sha256"] and rp["uuid"] == rp2["uuid"],
              "bytes=%d sha=%s" % (len(pdf), rp["sha256"][:12]))

        circled = {"entities": [{"center": [50_000_000, 50_000_000], "radius": 10_000_000}]}
        rc = plot_report(circled, scale="1:1", paper="A3", orientation="landscape",
                         arc_segments=32, out_path=os.path.join(tmp, "circle.eps"))
        check("circle_tessellation_and_eps",
              rc["segments_emitted"] == 32 and rc["entities_plotted"] == 1
              and open(os.path.join(tmp, "circle.eps"), "rb").read().startswith(b"%!PS-Adobe-3.0 EPSF-3.0"),
              "segments=%d" % rc["segments_emitted"])

        errs = []
        for kw, reason in (({"paper": "A9"}, "bad_paper"), ({"scale": "1:0"}, "bad_scale"),
                           ({"rotation": 45}, "bad_rotation"), ({"area": "nope"}, "bad_area"),
                           ({"style_table": "x.txt"}, "bad_style_table")):
            try:
                plot(doc, **kw)
                errs.append("%s:no_raise" % reason)
            except CadError as exc:
                if exc.reason != reason:
                    errs.append("%s:got_%s" % (reason, exc.reason))
            except Exception as exc:  # noqa: BLE001 - 非 CadError 也必须报红
                errs.append("%s:%s" % (reason, type(exc).__name__))
        check("bad_inputs_raise_cad_error_with_reason", not errs, ",".join(errs) or "5/5 reasons ok")

        win = plot_report(doc, area="window", window_nm=[0, 0, 50_000_000, 25_000_000],
                          scale="1:1", paper="A4", orientation="landscape")
        check("window_area_and_clip",
              win["window_nm"] == [0, 0, 50_000_000, 25_000_000]
              and win["entities_off_page"] == 0 and win["entities_plotted"] == 1
              and win["segments_nm"][0][0] == 123_500_000,
              "seg0=%r" % (win["segments_nm"][0],))

        check("document_unchanged_geometry_kept",
              json.dumps(_canonical(doc), sort_keys=True) == before,
              "L1 payload byte-identical after plot")
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        os.rmdir(tmp)

    failed = sum(1 for _, ok, _ in checks if not ok)
    print("FEATURE_OUTPUT_W14_SELFTEST checks=%d failed=%d verdict=%s"
          % (len(checks), failed, "PASS" if failed == 0 else "FAIL"))
    if failed:
        print("FEATURE_OUTPUT_W14 verdict=FAIL op=plot")
        return 1
    print("FEATURE_OK family=output op=plot verdict=PASS")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--selftest", "-s"):
        return selftest()
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())


# --- 写入型 op 声明（plot 误标修席 2026-09-30，见 tests/test_features_query_declare.py）---
# 撤掉只读声明：PLOT 的职责就是**真出图落盘**（`out_path` → SVG/PDF/EPS 字节，见 `_render` 的
# `open(out_path, "wb")`），旧的 `READONLY = True` / `readonly: True` 是"放回席第三批"批量补声明时
# 误加的 —— 声明只读而真写盘 ⇒ 尺子判 `declared_readonly_but_fs_changed`（phantom），缺陷在声明。
# 现按"写入型 op"如实声明：改不改 L1 由尺子现场记（本 op 只落盘、不改 L1）。
READONLY = False
OPERATIONS_META = {
    "plot": {"tier": "write", "readonly": False, "returns": "uuids"},
}
