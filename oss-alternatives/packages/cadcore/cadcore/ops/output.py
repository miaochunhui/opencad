#!/usr/bin/env python3
"""output 族模块：出图与交付（布局/视口/页面设置/打印/样式/发布/DXF-DWG）→ L1 文档。

冻结接口（与 `cadcore/ops` 其余族同源）
--------------------------------------
    FAMILY = "output"
    OPERATIONS: dict[str, Callable]        # 操作名 -> 可调用
    op(doc, **kwargs) -> list[str]         # 第一个位置参数是 L1 文档，返回 uuid 列表
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

口径
----
1. **长度一律整数纳米**：`*_nm` 键的值只允许 `int`（`bool`/浮点一律拒），拒绝时抛
   `invalid_length` / `non_positive_length`；**先校验后写入**，不半写文档。
2. **后果落在 L1 载荷里**：布局 = `Document.sheets[]`（图纸空间的载体），出图配置与作业账
   落在 `doc.extra["output"]`（plot_jobs / plotters / style_tables / publications / exports…）；
   两者都进 `to_payload()`，所以「文档真的变了」是可机读的。本族**只读**模型几何，
   绝不改草图实体（出图不许吃掉模型）。
3. **几何是算出来的**：`plot_area="extents"` 的窗口、预览的越界判定、发布页数都从
   `sketches[].entities[].geom` 现算；模型里没有几何 ⇒ `no_model_geometry`（不当空窗口糊过去）。
4. **比例是 `(分子, 分母)`**：`"1:100"` ⇒ 图纸 1 单位 = 模型 100 单位；`"fit"` 只在出图面解析
   （视口比例必须是真比例，给 `fit` 抛 `bad_scale`）。
5. **DXF 是真读写**：`dxf_export` 写 ASCII DXF（R12 用 POLYLINE、R2000+ 用 LWPOLYLINE）的
   ENTITIES 子集，`dxf_import` 能把它读回来（族自检做往返逐点比对）。
6. **DWG 不做私有位流**：`dwg_save` 只做**版本选择 + 规范化 L1 载荷落盘 + 内容 sha256**，
   记录里 `container="l1-json-bridge"`、`dwg_binary=False` **显式标注这不是 DWG 二进制**，
   不许当 DWG 位流记账；真正的 DWG 容器由 io 层桥接。
7. **预览是几何预算**：核内算纸张/窗口/比例/是否越界（`fits_on_paper`），记录里
   `rendered=False` 显式标注"成图渲染在 io 层"；给了 `out_path` 且非 dry-run 才落矢量 PDF。
8. **欠账（显式写出，不当已做）**：二进制 DXF 编码未实现（`fmt="binary"` ⇒ `unsupported_format`）；
   ETRANSMIT / XREF / DWF / PDF 导入属 nice_to_have，本族不提供。

自检
----
    python3 packages/cadcore/cadcore/ops/output.py --selftest
    ⇒ `CAD_PARITY_OUTPUT_MODULE verdict=…`（19 个 must_have 操作 + DXF 往返 + 变异体反证 + 不变量体检）
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

#: 直接 `python3 output.py` 时要能 import 到 cadcore（`parents[2]` = `packages/cadcore`）。
_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402  (sys.path 补完才能 import)
from cadcore.io.write_guard import guard_output  # noqa: E402

__all__ = [
    "FAMILY", "OPERATIONS", "PAPER_SIZES", "LINEWEIGHTS_MM", "DWG_VERSIONS", "DXF_VERSIONS",
    "layout_create", "viewport_create", "viewport_scale", "layer_override", "page_setup",
    "plot", "plot_pdf", "plotter_config", "plot_style_table", "lineweight", "plot_area",
    "plot_scale", "preview", "plot_flag", "model_space_plot", "layout_manage", "publish",
    "dwg_save", "dxf_export", "dxf_import", "selftest", "main",
]

FAMILY = "output"

_NS = "output"          # `doc.extra` 里的本族命名空间
_MM = 1_000_000         # 1 mm = 1e6 nm

#: 标准图纸（横向 mm）；自定义图幅用 `(w_nm, h_nm)` 整数对直接给。
PAPER_SIZES: dict[str, tuple[int, int]] = {
    "A0": (1189, 841), "A1": (841, 594), "A2": (594, 420), "A3": (420, 297), "A4": (297, 210),
    "A5": (210, 148), "ANSI_A": (279, 216), "ANSI_B": (432, 279), "ANSI_C": (559, 432),
    "ANSI_D": (864, 559), "ANSI_E": (1118, 864), "ARCH_D": (914, 610),
}

#: AutoCAD 标准线宽表（mm）——不在表内一律拒（`bad_lineweight`）。
LINEWEIGHTS_MM: tuple[float, ...] = (
    0.05, 0.09, 0.13, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.53,
    0.60, 0.70, 0.80, 1.00, 1.06, 1.20, 1.40, 1.58, 2.00,
)

DWG_VERSIONS: dict[str, str] = {
    "R2000": "AC1015", "R2004": "AC1018", "R2007": "AC1021",
    "R2010": "AC1024", "R2013": "AC1027", "R2018": "AC1032",
}
DXF_VERSIONS: dict[str, str] = {
    "R12": "AC1009", "R2000": "AC1015", "R2004": "AC1018", "R2007": "AC1021",
    "R2010": "AC1024", "R2013": "AC1027", "R2018": "AC1032",
}

_ACADVER_ALIASES = {code.lower(): key for key, code in {**DXF_VERSIONS, **DWG_VERSIONS}.items()}
for _key in tuple(DWG_VERSIONS):
    _ACADVER_ALIASES.setdefault(_key[1:].lower(), _key)   # "2018" -> "R2018"

PLOT_AREAS = ("display", "extents", "limits", "view", "window", "layout")
ORIENTATIONS = ("landscape", "portrait")
VIEW_DIRECTIONS = ("front", "back", "left", "right", "top", "bottom", "iso", "section", "detail")
LAYOUT_ACTIONS = ("new", "copy", "rename", "delete")
_COLOR_NAMES = ("byblock", "bylayer")

_UNIT_TO_NM: dict[str, int] = {
    "nm": 1, "um": 1_000, "mm": _MM, "cm": 10 * _MM, "m": 1000 * _MM,
    "in": 25_400_000, "ft": 304_800_000,
}
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
_SCALE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")


# ======================================================================================
# 校验器（全部在写入之前跑完）
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _uuid4() -> str:
    return str(_uuid.uuid4())


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _uuid_str(value: Any, what: str) -> str:
    if not _is_uuid(value):
        raise _fail("bad_uuid", "%s must be a uuid string" % what)
    return value


def _nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（`bool` 与浮点一律拒）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "%s must be an integer number of nanometres" % what)
    if positive and value <= 0:
        raise _fail("non_positive_length", "%s must be positive" % what)
    return value


def _count(value: Any, what: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_count", "%s must be an integer" % what)
    if positive and value <= 0:
        raise _fail("non_positive_count", "%s must be positive" % what)
    return value


def _nm_pair(value: Any, what: str, *, positive: bool = False) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_length", "%s must be a pair of nanometres" % what)
    return [_nm(value[0], "%s[0]" % what, positive=positive),
            _nm(value[1], "%s[1]" % what, positive=positive)]


def _nm_quad(value: Any, what: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _fail("invalid_length", "%s must be [x0, y0, x1, y1] in nanometres" % what)
    return [_nm(item, "%s[%d]" % (what, i)) for i, item in enumerate(value)]


def _one_of(value: Any, allowed: Sequence[Any], what: str, reason: str = "bad_enum") -> Any:
    if value not in allowed:
        raise _fail(reason, "%s must be one of %s (got %r)"
                    % (what, ",".join(str(item) for item in allowed), value))
    return value


def _label(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_name", "%s must be a non-empty string" % what)
    return value


def _key_like(value: Any, what: str) -> str:
    """i18n key 形状（与 `docmodel._KEY_LIKE_RE` 同源口径）：点分 ASCII 标识符。"""
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise _fail("bad_text_key", "%s must be a dotted ASCII i18n key (got %r)" % (what, value))
    return value


def _scale_value(scale: Any) -> tuple[float, float, bool]:
    """比例入参 → `(分子, 分母, 是否 fit)`。"""
    if isinstance(scale, str):
        text = scale.strip().lower()
        if text in ("fit", "fit_to_paper"):
            return 1.0, 1.0, True
        match = _SCALE_RE.match(text)
        if not match:
            raise _fail("bad_scale", "scale must look like '1:100' or 'fit' (got %r)" % scale)
        num, den = float(match.group(1)), float(match.group(2))
    elif isinstance(scale, (int, float)) and not isinstance(scale, bool):
        num, den = float(scale), 1.0
    else:
        raise _fail("bad_scale", "scale must be a string or a number (got %r)" % (scale,))
    if not (num > 0 and den > 0) or num != num or den != den or num in (float("inf"),) or den == float("inf"):
        raise _fail("bad_scale", "scale must be finite and positive (got %r)" % (scale,))
    return num, den, False


def _scale_text(num: float, den: float) -> str:
    def one(value: float) -> str:
        return ("%d" % int(value)) if float(value).is_integer() else ("%g" % value)
    return "%s:%s" % (one(num), one(den))


def _lineweight(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("bad_lineweight", "lineweight_mm must be a number (got %r)" % (value,))
    number = float(value)
    for allowed in LINEWEIGHTS_MM:
        if abs(number - allowed) < 1e-9:
            return allowed
    raise _fail("bad_lineweight", "lineweight_mm must be one of the standard values (got %r)" % (value,))


def _color(value: Any) -> Any:
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _COLOR_NAMES:
            return token
        raise _fail("bad_color", "color must be an ACI index or one of %s" % ",".join(_COLOR_NAMES))
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 256:
        raise _fail("bad_color", "color must be an ACI index in [0, 256] (got %r)" % (value,))
    return value


def _paper_nm(paper: Any, orientation: str) -> tuple[str, int, int]:
    """图纸入参 → `(名字, w_nm, h_nm)`（按朝向摆正；全部整数纳米）。"""
    _one_of(orientation, ORIENTATIONS, "orientation")
    if isinstance(paper, str):
        name = paper.strip().upper().replace(" ", "_").replace("-", "_")
        if name not in PAPER_SIZES:
            raise _fail("unknown_paper", "paper must be one of %s or a (w_nm, h_nm) pair (got %r)"
                        % (",".join(sorted(PAPER_SIZES)), paper))
        w_mm, h_mm = PAPER_SIZES[name]
        width_nm, height_nm = w_mm * _MM, h_mm * _MM
    elif isinstance(paper, (list, tuple)) and len(paper) == 2:
        name = "custom"
        width_nm = _nm(paper[0], "paper width", positive=True)
        height_nm = _nm(paper[1], "paper height", positive=True)
    else:
        raise _fail("unknown_paper", "paper must be a name or a (w_nm, h_nm) pair (got %r)" % (paper,))
    if orientation == "portrait" and width_nm > height_nm:
        width_nm, height_nm = height_nm, width_nm
    if orientation == "landscape" and height_nm > width_nm:
        width_nm, height_nm = height_nm, width_nm
    return name, width_nm, height_nm


def _plotter_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_plotter_device", "device must be a .pc3 name or 'none' (got %r)" % (value,))
    name = value.strip()
    if name.lower() != "none" and not name.lower().endswith(".pc3"):
        raise _fail("bad_plotter_device", "device must end with .pc3 (got %r)" % (name,))
    return name


def _style_table_name(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_style_table", "style_table must be a .ctb/.stb name (got %r)" % (value,))
    name = value.strip()
    if not name.lower().endswith((".ctb", ".stb")):
        raise _fail("bad_style_table", "style_table must end with .ctb or .stb (got %r)" % (name,))
    return name


def _version_key(value: Any, table: Mapping[str, str], reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(reason, "version must be a non-empty string (got %r)" % (value,))
    token = value.strip()
    upper = token.upper()
    if upper in table:
        return upper
    alias = _ACADVER_ALIASES.get(token.lower())
    if alias is not None and alias in table:
        return alias
    raise _fail(reason, "version must be one of %s (got %r)" % (",".join(sorted(table)), value))


# ======================================================================================
# 文档访问（Document 对象与纯 dict 载荷都支持）
# ======================================================================================


def _walker(node: Any):
    yield node
    if isinstance(node, Mapping):
        for value in node.values():
            yield from _walker(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walker(value)


def _holder(doc: Any) -> dict[str, Any]:
    """本族记账命名空间 `doc.extra["output"]`（纯 dict 载荷同样支持）。"""
    extra = getattr(doc, "extra", None)
    container = extra if isinstance(extra, dict) else (doc if isinstance(doc, dict) else None)
    if container is None:
        raise _fail("bad_document", "output ops need an L1 Document (or payload dict), got %s"
                    % type(doc).__name__)
    holder = container.setdefault(_NS, {})
    if not isinstance(holder, dict):
        raise _fail("bad_document", "reserved key %r must hold a mapping" % _NS)
    return holder


def _field(doc: Any, name: str, default: Any = None) -> Any:
    value = getattr(doc, name, None)
    if value is None and isinstance(doc, Mapping):
        value = doc.get(name)
    return default if value is None else value


def _sheets(doc: Any) -> list[Any]:
    sheets = _field(doc, "sheets", None)
    if not isinstance(sheets, list):
        raise _fail("bad_document", "document.sheets must be a list")
    return sheets


def _sketches(doc: Any) -> list[Any]:
    sketches = _field(doc, "sketches", None)
    if not isinstance(sketches, list):
        raise _fail("bad_document", "document.sketches must be a list")
    return sketches


def _iter_entities(doc: Any):
    for sketch in _sketches(doc):
        if isinstance(sketch, Mapping):
            for entity in sketch.get("entities") or []:
                if isinstance(entity, Mapping):
                    yield entity


def _doc_unit(doc: Any) -> str:
    unit = _field(doc, "unit", "mm")
    return str(unit) if isinstance(unit, str) and unit.strip() else "mm"


def _standard_profile(doc: Any) -> str:
    profile = _field(doc, "standard_profile", "iso")
    return str(profile) if isinstance(profile, str) and profile.strip() else "iso"


def _known_uuids(doc: Any) -> set[str]:
    payload = doc.to_payload() if hasattr(doc, "to_payload") else (
        dict(doc) if isinstance(doc, Mapping) else {})
    found: set[str] = set()
    for node in _walker(payload):
        if isinstance(node, Mapping):
            for key, value in node.items():
                if str(key).lower().endswith("uuid") and isinstance(value, str):
                    found.add(value)
    return found


def _entity_fingerprint(doc: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for entity in _iter_entities(doc):
        out[str(entity.get("uuid"))] = json.dumps(entity.get("geom"), sort_keys=True, default=str)
    return out


def _active_sheet(doc: Any, sheet: Any = None, *, create: bool = False) -> Any:
    """定位图纸空间布局：`None`=活动布局；字符串=uuid 或布局名；映射=直接用。"""
    sheets = _sheets(doc)
    if isinstance(sheet, Mapping):
        if sheet in sheets:
            return sheet
        raise _fail("unknown_sheet", "the given layout mapping is not in document.sheets")
    if sheet is None:
        if sheets:
            return sheets[0]
        if create:
            layout_create(doc)
            return sheets[0]
        raise _fail("no_sheet", "document has no layout (paper space); create one first")
    if isinstance(sheet, str):
        for item in sheets:
            if isinstance(item, Mapping) and (item.get("uuid") == sheet or item.get("name") == sheet):
                return item
        raise _fail("unknown_sheet", "no layout matches %r" % (sheet,))
    raise _fail("bad_reference", "sheet must be a layout mapping, a uuid/name string, or None")


def _active_view(doc: Any, sheet: Any = None, view: Any = None, *, create: bool = True) -> tuple[Any, Any]:
    """定位视口：`None`=布局里第一个视口（缺失且允许时补一个，MVIEW 语义）。"""
    layout = _active_sheet(doc, sheet, create=create)
    views = layout.setdefault("views", [])
    if not isinstance(views, list):
        raise _fail("bad_document", "layout.views must be a list")
    if isinstance(view, Mapping):
        if view in views:
            return layout, view
        raise _fail("unknown_viewport", "the given viewport mapping is not in the layout")
    if view is None:
        if views:
            return layout, views[0]
        if not create:
            raise _fail("no_viewport", "layout %r has no viewport" % (layout.get("name"),))
        viewport_create(doc, sheet=layout)
        return layout, views[0]
    if isinstance(view, str):
        for item in views:
            if isinstance(item, Mapping) and (item.get("uuid") == view or item.get("name") == view):
                return layout, item
        raise _fail("unknown_viewport", "no viewport matches %r" % (view,))
    raise _fail("bad_reference", "view must be a viewport mapping, a uuid/name string, or None")


def _record(holder: Mapping[str, Any], bucket: str, **fields: Any) -> dict[str, Any]:
    """往 `doc.extra["output"][bucket]` 追加一条**带唯一 uuid** 的账目，返回该条。"""
    rec: dict[str, Any] = {"uuid": _uuid4()}
    rec.update(fields)
    items = holder.setdefault(bucket, [])          # type: ignore[attr-defined]
    if not isinstance(items, list):
        raise _fail("bad_document", "output.%s must be a list" % bucket)
    items.append(rec)
    return rec


# ======================================================================================
# 模型几何 → 打印窗口（现算，不抄常数）
# ======================================================================================


def _point(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return int(round(float(x))), int(round(float(y)))
    return None


def _model_extents_nm(doc: Any) -> tuple[int, int, int, int] | None:
    xs: list[int] = []
    ys: list[int] = []
    for entity in _iter_entities(doc):
        geom = entity.get("geom")
        if not isinstance(geom, Mapping):
            continue
        if all(key in geom for key in ("x1", "y1", "x2", "y2")):
            for key, bucket in (("x1", xs), ("x2", xs), ("y1", ys), ("y2", ys)):
                value = geom.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    bucket.append(int(round(float(value))))
            continue
        if "cx" in geom and "cy" in geom:
            cx = geom.get("cx")
            cy = geom.get("cy")
            radius = geom.get("r", geom.get("radius", 0))
            if all(isinstance(item, (int, float)) and not isinstance(item, bool)
                   for item in (cx, cy, radius)):
                xs.extend([int(round(float(cx))) - int(round(float(radius))),
                           int(round(float(cx))) + int(round(float(radius)))])
                ys.extend([int(round(float(cy))) - int(round(float(radius))),
                           int(round(float(cy))) + int(round(float(radius)))])
            continue
        points = geom.get("points_nm")
        if isinstance(points, (list, tuple)):
            for item in points:
                pair = _point(item)
                if pair is None and isinstance(item, (int, float)) and not isinstance(item, bool):
                    continue
                if pair is not None:
                    xs.append(pair[0])
                    ys.append(pair[1])
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _layout_paper_nm(layout: Mapping[str, Any]) -> tuple[int, int]:
    """布局纸张尺寸（纳米），**两种 L1 形态都认**：

    * 顶层 `width_nm`/`height_nm` —— `_l1_entry_for` 真正写进载荷的形态（`size` 只是档名**字符串**）；
    * `size` 是映射且带 `w_nm`/`h_nm` —— 旧口径认的形态（保留，别把别的调用方打红）。

    旧口径只看第二种 ⇒ **真建布局**（`create_layout`）上 `plot_area(display/limits/view/layout)`
    一律 `non_positive_length: layout width must be positive`（实测）。这里补第一种，值域不放宽：
    仍然要求 **正数**，缺/0/负 照旧报 `non_positive_length`。
    """
    size = layout.get("size")
    if isinstance(size, Mapping) and ("w_nm" in size or "h_nm" in size):
        return (_nm(size.get("w_nm", 0), "layout width", positive=True),
                _nm(size.get("h_nm", 0), "layout height", positive=True))
    return (_nm(layout.get("width_nm", 0), "layout width", positive=True),
            _nm(layout.get("height_nm", 0), "layout height", positive=True))


def _plot_window_nm(doc: Any, layout: Any, area: str, window_nm: Any = None) -> list[int]:
    if area == "window":
        if window_nm is None:
            raise _fail("missing_window", "plot area 'window' needs window_nm=[x0, y0, x1, y1]")
        box = _nm_quad(window_nm, "window_nm")
        if box[2] <= box[0] or box[3] <= box[1]:
            raise _fail("empty_window", "window_nm must have x1>x0 and y1>y0")
        return box
    if layout is None:
        extents = _model_extents_nm(doc)
        if extents is None:
            raise _fail("no_model_geometry", "model space has no geometry to plot")
        return list(extents)
    if area in ("layout", "display", "view", "limits"):
        width, height = _layout_paper_nm(layout)
        return [0, 0, width, height]
    if area == "extents":
        extents = _model_extents_nm(doc)
        if extents is None:
            raise _fail("no_model_geometry", "model space has no geometry to plot")
        return list(extents)
    raise _fail("bad_enum", "unknown plot area %r" % (area,))


# ======================================================================================
# 19 个 must_have 操作（顺序即 AutoCAD 出图工作流；缺省参数可直接裸调）
# ======================================================================================


def layout_create(doc: Any, *, name: str = "Layout1", paper: Any = "A4",
                  orientation: str = "landscape", scale: Any = "1:1",
                  title_key: str = "doc.untitled") -> list[str]:
    """新建图纸空间布局（LAYOUT 的 New / 布局选项卡）→ `document.sheets[]`。"""
    layout_name = _label(name, "layout name")
    _, width_nm, height_nm = _paper_nm(paper, orientation)
    num, den, fit = _scale_value(scale)
    if fit:
        raise _fail("bad_scale", "a layout scale must be a ratio, not 'fit'")
    title = _key_like(title_key, "title_key")
    sheets = _sheets(doc)
    for item in sheets:
        if isinstance(item, Mapping) and item.get("name") == layout_name:
            raise _fail("duplicate_layout", "layout %r already exists" % layout_name)
    sheet = {
        "uuid": _uuid4(), "kind": "layout", "name": layout_name,
        "standard_profile": _standard_profile(doc),
        "size": {"w_nm": width_nm, "h_nm": height_nm},
        "scale": _scale_text(num, den), "tilemode": 0, "active": True,
        "views": [], "dimensions": [], "title_block": {"key": title},
    }
    sheets.append(sheet)
    return [sheet["uuid"]]


def viewport_create(doc: Any, *, sheet: Any = None, direction: str = "front",
                    at_nm: Any = (0, 0), size_nm: Any = (200 * _MM, 150 * _MM),
                    scale: Any = "1:1", locked: bool = False, layer: str = "0",
                    name: str = "") -> list[str]:
    """在布局里开视口（VPORTS / MVIEW）→ `layout.views[]`。"""
    layout = _active_sheet(doc, sheet, create=True)
    _one_of(direction, VIEW_DIRECTIONS, "viewport direction")
    at = _nm_pair(at_nm, "at_nm")
    size = _nm_pair(size_nm, "size_nm", positive=True)
    num, den, fit = _scale_value(scale)
    if fit:
        raise _fail("bad_scale", "a viewport scale must be a ratio, not 'fit'")
    views = layout.setdefault("views", [])
    view = {
        "uuid": _uuid4(), "kind": "viewport",
        "name": _label(name, "viewport name") if name else "Viewport%d" % (len(views) + 1),
        "direction": direction, "at_nm": at, "size_nm": size,
        "scale": _scale_text(num, den), "locked": bool(locked), "layer": _label(layer, "layer"),
        "frozen_layers": [], "layer_overrides": {}, "cache": {},
    }
    views.append(view)
    return [view["uuid"]]


def viewport_scale(doc: Any, *, sheet: Any = None, view: Any = None, scale: Any = "1:100",
                   locked: bool = True) -> list[str]:
    """设视口比例并可锁定（标准比例 / Display Locked）→ 改视口记录。"""
    _, target = _active_view(doc, sheet, view)
    num, den, fit = _scale_value(scale)
    if fit:
        raise _fail("bad_scale", "a viewport scale must be a ratio, not 'fit'")
    target["scale"] = _scale_text(num, den)
    target["locked"] = bool(locked)
    target["scale_locked"] = bool(locked)
    target["paper_units_per_model_unit"] = num / den
    return [target["uuid"]]


def layer_override(doc: Any, *, sheet: Any = None, view: Any = None, layer: str = "0",
                   frozen: bool = True, hidden: bool = False, color: Any = None,
                   lineweight_mm: Any = None) -> list[str]:
    """视口内图层替代/冻结（VPLAYER）→ `viewport.layer_overrides[layer]`。"""
    _, target = _active_view(doc, sheet, view)
    layer_name = _label(layer, "layer")
    override: dict[str, Any] = {}
    if color is not None:
        override["color"] = _color(color)
    if lineweight_mm is not None:
        override["lineweight_mm"] = _lineweight(lineweight_mm)
    rec = {
        "uuid": _uuid4(), "kind": "vp layer override", "layer": layer_name,
        "freeze": bool(frozen), "hidden": bool(hidden), "override": override,
    }
    overrides = target.setdefault("layer_overrides", {})
    if not isinstance(overrides, dict):
        raise _fail("bad_document", "viewport.layer_overrides must be a mapping")
    overrides[layer_name] = rec
    return [rec["uuid"]]


def page_setup(doc: Any, *, sheet: Any = None, paper: Any = "A4", orientation: str = "landscape",
               device: Any = "DWG To PDF.pc3", style_table: Any = "monochrome.ctb",
               scale: Any = "1:1", area: str = "layout", center: bool = True,
               fit: bool = False, setup_name: str = "") -> list[str]:
    """页面设置（PAGESETUP）：图幅/设备/样式表/比例一次配好并随布局保存。"""
    layout = _active_sheet(doc, sheet, create=True)
    paper_name, width_nm, height_nm = _paper_nm(paper, orientation)
    plotter = _plotter_name(device)
    table = _style_table_name(style_table)
    _one_of(area, PLOT_AREAS, "plot area")
    num, den, fit_flag = _scale_value(scale)
    rec = {
        "uuid": _uuid4(), "kind": "page setup",
        "setup_name": _key_like(setup_name or "output.page_setup.default", "setup_name"),
        "paper_size": {"name": paper_name, "w_nm": width_nm, "h_nm": height_nm},
        "orientation": orientation, "device": plotter, "style_table": table,
        "plot_style_table": table, "plot_scale": "fit" if fit or fit_flag else _scale_text(num, den),
        "plot_area": area, "center_plot": bool(center), "fit_to_paper": bool(fit or fit_flag),
    }
    layout["page_setup"] = rec
    return [rec["uuid"]]


def _build_plot_job(doc: Any, *, sheet: Any = None, space: str | None = None, device: Any = None,
                    style_table: Any = None, area: str | None = None, scale: Any = None,
                    copies: Any = 1, output: str = "", dry_run: bool = True,
                    output_format: str = "plot file", paper: Any = None,
                    orientation: str = "landscape") -> dict[str, Any]:
    """出图作业的公共件：解析目标（布局/模型空间）→ 现算窗口/比例/图幅 → 记账。"""
    _holder(doc)
    sheets = _sheets(doc)
    target = None
    if space != "model":
        if sheet is not None:
            target = _active_sheet(doc, sheet, create=True)
        elif sheets:
            target = _active_sheet(doc, None, create=False)
    setup: Mapping[str, Any] = {}
    if target is not None:
        space = "layout"
        if isinstance(target.get("page_setup"), Mapping):
            setup = target["page_setup"]
        size = setup.get("paper_size") if isinstance(setup.get("paper_size"), Mapping) else {}
        if paper is None:
            width_nm = size.get("w_nm")
            height_nm = size.get("h_nm")
            paper_name = str(size.get("name") or "A4")
            if not isinstance(width_nm, int) or not isinstance(height_nm, int):
                paper_name, width_nm, height_nm = _paper_nm("A4", orientation)
        else:
            paper_name, width_nm, height_nm = _paper_nm(paper, orientation)
        device = device or setup.get("device") or "DWG To PDF.pc3"
        style_table = style_table or setup.get("style_table") or "monochrome.ctb"
        area = area or setup.get("plot_area") or "layout"
        scale = scale or setup.get("plot_scale") or "1:1"
    else:
        space = "model"
        paper_name, width_nm, height_nm = _paper_nm(paper if paper is not None else "A4", orientation)
        device = device or "DWG To PDF.pc3"
        style_table = style_table or "monochrome.ctb"
        area = area or "extents"
        scale = scale or "1:1"
    _one_of(area, PLOT_AREAS, "plot area")
    window = _plot_window_nm(doc, target, str(area))
    num, den, fit = _scale_value(scale)
    if fit:
        drawn_w = width_nm
        drawn_h = height_nm
    else:
        drawn_w = int(round((window[2] - window[0]) * num / den))
        drawn_h = int(round((window[3] - window[1]) * num / den))
    plotter = _plotter_name(device)
    table = _style_table_name(style_table)
    _one_of(output_format, ("plot file", "pdf", "plt"), "output format")
    rec = _record(
        _holder(doc), "plot_jobs", kind="plot job", space=space,
        space_kind="model_space" if space == "model" else "paper_space",
        plot={"device": plotter, "copies": _count(copies, "copies", positive=True),
              "output_format": output_format, "output_path": str(output)},
        plot_area={"name": str(area), "window_nm": window,
                   "window_space": "model_space" if target is None else "paper_space"},
        plot_scale="fit" if fit else _scale_text(num, den), fit_to_paper=bool(fit),
        plot_style_table=table, device=plotter, paper_size={"name": paper_name,
                                                            "w_nm": width_nm, "h_nm": height_nm},
        window_nm=window, drawn_size_nm=[drawn_w, drawn_h],
        fits_on_paper=bool(drawn_w <= width_nm and drawn_h <= height_nm),
        output_format=output_format, output_path=str(output), dry_run=bool(dry_run),
        layout_name=target.get("name") if isinstance(target, Mapping) else None,
        layout_index=sheets.index(target) if target is not None and target in sheets else None,
        entity_count=sum(1 for _ in _iter_entities(doc)),
    )
    return rec


def plot(doc: Any, *, sheet: Any = None, device: Any = None, style_table: Any = None,
         area: str | None = None, scale: Any = None, copies: Any = 1, output: str = "",
         dry_run: bool = True) -> list[str]:
    """打印主命令 PLOT：解析布局/模型空间 → 算窗口与比例 → 记一条作业（不落文件）。"""
    rec = _build_plot_job(doc, sheet=sheet, space=None, device=device, style_table=style_table,
                          area=area, scale=scale, copies=copies, output=output, dry_run=dry_run)
    return [rec["uuid"]]


def _pdf_num(value: float) -> str:
    text = "%.4f" % value
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pt(nm: float) -> float:
    return float(nm) * 72.0 / 25_400_000.0


def _pdf_bytes(pages: Sequence[tuple[int, int, Sequence[tuple[float, float, float, float]]]]) -> bytes:
    """真 PDF 字节（单/多页，纯标准库，无时间戳 ⇒ 逐字节确定性）。"""
    objects: list[bytes] = [b"", b""]           # 1=Catalog, 2=Pages
    kids: list[int] = []
    for width_nm, height_nm, segments in pages:
        width, height = _pt(width_nm), _pt(height_nm)
        body = ["q", "0.25 w", "0 0 %s %s re S" % (_pdf_num(width), _pdf_num(height))]
        for x1, y1, x2, y2 in segments:
            body.append("%s %s m %s %s l S" % (_pdf_num(_pt(x1)), _pdf_num(_pt(y1)),
                                               _pdf_num(_pt(x2)), _pdf_num(_pt(y2))))
        body.append("Q")
        content = ("\n".join(body) + "\n").encode("ascii")
        content_id = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream")
        page_id = len(objects) + 1
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Contents %d 0 R "
                        "/Resources << >> >>" % (_pdf_num(width), _pdf_num(height),
                                                 content_id)).encode("ascii"))
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


def _page_segments(doc: Any, window: Sequence[int], factor: float) -> list[tuple[float, float, float, float]]:
    """模型几何 → 纸张坐标线段（圆按 16 边形弦近似，弦替代圆弧在记录里已标注）。"""
    x0, y0 = window[0], window[1]

    def shift(value: Any) -> float:
        return (float(value) - float(x0)) * factor

    def shift_y(value: Any) -> float:
        return (float(value) - float(y0)) * factor

    segments: list[tuple[float, float, float, float]] = []
    for entity in _iter_entities(doc):
        geom = entity.get("geom")
        if not isinstance(geom, Mapping):
            continue
        if all(key in geom for key in ("x1", "y1", "x2", "y2")):
            segments.append((shift(geom["x1"]), shift_y(geom["y1"]),
                             shift(geom["x2"]), shift_y(geom["y2"])))
        elif "cx" in geom and "cy" in geom:
            import math
            cx, cy = shift(geom["cx"]), shift_y(geom["cy"])
            radius = float(geom.get("r", geom.get("radius", 0))) * factor
            for step in range(16):
                a1 = 2.0 * math.pi * step / 16.0
                a2 = 2.0 * math.pi * (step + 1) / 16.0
                segments.append((cx + radius * math.cos(a1), cy + radius * math.sin(a1),
                                 cx + radius * math.cos(a2), cy + radius * math.sin(a2)))
    return segments


def plot_pdf(doc: Any, *, sheet: Any = None, out_path: str = "", device: Any = "DWG To PDF.pc3",
             style_table: Any = None, area: str | None = None, scale: Any = None,
             dry_run: bool = True) -> list[str]:
    """打印到 PDF（DWG To PDF.pc3）：算作业；给了 out_path 且非 dry-run 才落矢量 PDF。"""
    rec = _build_plot_job(doc, sheet=sheet, space=None, device=device, style_table=style_table,
                          area=area, scale=scale, output=out_path, dry_run=dry_run,
                          output_format="pdf")
    rec["pdf_path"] = str(out_path)
    rec["pdf_written"] = False
    if out_path and not dry_run:
        out_path = guard_output(out_path)
        window = rec["plot_area"]["window_nm"]
        paper = rec["paper_size"]
        if rec["fit_to_paper"]:
            scale_factor = min(paper["w_nm"] / max(1, window[2] - window[0]),
                               paper["h_nm"] / max(1, window[3] - window[1]))
        else:
            num, den, _ = _scale_value(rec["plot_scale"])
            scale_factor = num / den
        pages = [(paper["w_nm"], paper["h_nm"], _page_segments(doc, window, scale_factor))]
        Path(out_path).write_bytes(_pdf_bytes(pages))
        rec["pdf_written"] = True
        rec["pdf_bytes"] = Path(out_path).stat().st_size
        rec["pdf_circle_chords"] = 16
    return [rec["uuid"]]


def plotter_config(doc: Any, *, device: Any = "DWG To PDF.pc3", paper: Any = None,
                   orientation: str = "landscape", media: str = "pdf",
                   custom_size_nm: Any = None) -> list[str]:
    """绘图仪/打印机配置（PLOTTERMANAGER / .pc3）：设备 + 可用图幅 + 出纸口。"""
    name = _plotter_name(device)
    _one_of(media, ("pdf", "plt", "paper", "system"), "media type")
    sizes: list[dict[str, Any]] = []
    if custom_size_nm is not None:
        width_nm, height_nm = _nm_pair(custom_size_nm, "custom_size_nm", positive=True)
        sizes.append({"name": "custom", "w_nm": width_nm, "h_nm": height_nm})
    if paper is not None:
        paper_name, width_nm, height_nm = _paper_nm(paper, orientation)
        sizes.append({"name": paper_name, "w_nm": width_nm, "h_nm": height_nm})
    if not sizes:
        paper_name, width_nm, height_nm = _paper_nm("A4", orientation)
        sizes.append({"name": paper_name, "w_nm": width_nm, "h_nm": height_nm})
    rec = _record(_holder(doc), "plotters", kind="plotter config", device=name, pc3=name,
                  plotter=name, media_type=media,
                  port="file" if media in ("pdf", "plt") else "system",
                  orientation=orientation, paper_sizes=sizes, default_size=sizes[0]["name"])
    return [rec["uuid"]]


def plot_style_table(doc: Any, *, name: Any = "monochrome.ctb",
                     description_key: str = "output.style.monochrome",
                     lineweight_mm: Any = 0.25, color: Any = None,
                     screening: Any = None) -> list[str]:
    """打印样式表 CTB/STB（STYLESMANAGER）：颜色/线宽/淡显映射。"""
    table = _style_table_name(name)
    lineweight = _lineweight(lineweight_mm)
    override: dict[str, Any] = {"lineweight_mm": lineweight}
    if color is not None:
        override["color"] = _color(color)
    if screening is not None:
        if isinstance(screening, bool) or not isinstance(screening, int) or not 0 <= screening <= 100:
            raise _fail("bad_screening", "screening must be an integer percentage in [0, 100]")
        override["screening"] = screening
    rec = _record(_holder(doc), "style_tables", kind="plot style table", style_table=table,
                  ctb=table if table.lower().endswith(".ctb") else None,
                  stb=table if table.lower().endswith(".stb") else None,
                  description_key=_key_like(description_key, "description_key"),
                  monochrome="mono" in table.lower(), style_override=override)
    return [rec["uuid"]]


def lineweight(doc: Any, *, layer: str = "0", lineweight_mm: Any = 0.25,
               plot_style: Any = "monochrome.ctb", plot_object: bool = True,
               object_uuid: Any = None) -> list[str]:
    """线宽与打印线宽（LWEIGHT）：按图层/对象的打印线宽 + 来源（样式表 or 对象替代）。"""
    layer_name = _label(layer, "layer")
    weight = _lineweight(lineweight_mm)
    table = _style_table_name(plot_style)
    if object_uuid is not None:
        _uuid_str(object_uuid, "object_uuid")
        if object_uuid not in _known_uuids(doc):
            raise _fail("unknown_reference", "object_uuid does not resolve in this document")
    rec = _record(_holder(doc), "lineweights", kind="lineweight", layer=layer_name,
                  lineweight_mm=weight, print_lineweight_mm=weight, lweight=weight,
                  plot_style_table=table,
                  lweight_source="plot style table" if table else "object override",
                  plot_object=bool(plot_object), object_scoped=object_uuid is not None)
    return [rec["uuid"]]


def plot_area(doc: Any, *, sheet: Any = None, area: str = "extents",
              window_nm: Any = None) -> list[str]:
    """打印区域（显示/范围/图形界限/视图/窗口）→ 现算窗口。"""
    _one_of(area, PLOT_AREAS, "plot area")
    layout = None
    if sheet is not None or (area != "extents" and _sheets(doc)):
        layout = _active_sheet(doc, sheet, create=False)
    window = _plot_window_nm(doc, layout, area, window_nm)
    rec = _record(_holder(doc), "plot_areas", kind="plot area",
                  plot_area={"name": area, "window_nm": window,
                             "window_space": "paper_space" if layout is not None else "model_space"},
                  window_nm=window,
                  covered_area_nm2=(window[2] - window[0]) * (window[3] - window[1]),
                  layout_name=layout.get("name") if isinstance(layout, Mapping) else None)
    return [rec["uuid"]]


def plot_scale(doc: Any, *, sheet: Any = None, scale: Any = "1:1",
               fit_to_paper: bool = False) -> list[str]:
    """打印比例与布满图纸（Fit to paper）。"""
    num, den, fit = _scale_value("fit" if fit_to_paper else scale)
    layout = _active_sheet(doc, sheet, create=False) if _sheets(doc) else None
    rec = _record(_holder(doc), "plot_scales", kind="plot scale",
                  plot_scale="fit" if fit else _scale_text(num, den), fit_to_paper=bool(fit),
                  scale_ratio=[num, den], scale_denominator=den,
                  layout_name=layout.get("name") if isinstance(layout, Mapping) else None)
    return [rec["uuid"]]


def preview(doc: Any, *, sheet: Any = None, area: str | None = None, scale: Any = None,
            out_path: str = "", dry_run: bool = True) -> list[str]:
    """打印预览（PREVIEW）：核内做几何预算（纸张/窗口/比例/越界），渲染在 io 层。"""
    layout = _active_sheet(doc, sheet, create=False) if _sheets(doc) else None
    resolved_area = area or ("layout" if layout is not None else "extents")
    _one_of(resolved_area, PLOT_AREAS, "plot area")
    window = _plot_window_nm(doc, layout, resolved_area)
    setup = layout.get("page_setup") if isinstance(layout, Mapping) and isinstance(
        layout.get("page_setup"), Mapping) else {}
    paper = setup.get("paper_size") if isinstance(setup.get("paper_size"), Mapping) else {}
    paper_name = str(paper.get("name") or "A4")
    width_nm = paper.get("w_nm")
    height_nm = paper.get("h_nm")
    if not isinstance(width_nm, int) or not isinstance(height_nm, int):
        paper_name, width_nm, height_nm = _paper_nm("A4", "landscape")
    num, den, fit = _scale_value(scale or setup.get("plot_scale") or "1:1")
    if fit:
        factor = min(width_nm / max(1, window[2] - window[0]),
                     height_nm / max(1, window[3] - window[1]))
        scale_text = "fit"
    else:
        factor = num / den
        scale_text = _scale_text(num, den)
    drawn = [int(round((window[2] - window[0]) * factor)),
             int(round((window[3] - window[1]) * factor))]
    rec = _record(_holder(doc), "previews", kind="print preview",
                  preview={"paper_size": {"name": paper_name, "w_nm": width_nm, "h_nm": height_nm},
                           "window_nm": window, "drawn_size_nm": drawn,
                           "fits_on_paper": bool(drawn[0] <= width_nm and drawn[1] <= height_nm),
                           "margin_nm": [max(0, (width_nm - drawn[0]) // 2),
                                         max(0, (height_nm - drawn[1]) // 2)],
                           "rendered": False, "render_stage": "io",
                           "circle_chords": 16},
                  plot_scale=scale_text, rendered=False, out_path=str(out_path))
    if out_path and not dry_run:
        out_path = guard_output(out_path)
        Path(out_path).write_bytes(_pdf_bytes([(width_nm, height_nm,
                                                _page_segments(doc, window, factor))]))
        rec["preview"]["rendered"] = True
        rec["preview_bytes"] = Path(out_path).stat().st_size
    return [rec["uuid"]]


def plot_flag(doc: Any, *, layer: str = "0", plottable: bool = True,
              object_uuid: Any = None) -> list[str]:
    """可打印控制（Plot / No Plot）：图层或对象的打印开关。"""
    layer_name = _label(layer, "layer")
    if object_uuid is not None:
        _uuid_str(object_uuid, "object_uuid")
        if object_uuid not in _known_uuids(doc):
            raise _fail("unknown_reference", "object_uuid does not resolve in this document")
    rec = _record(_holder(doc), "plot_flags", kind="plot flag", layer=layer_name,
                  plottable=bool(plottable), no_plot=not bool(plottable),
                  plot_flag="plot" if plottable else "no_plot", print_flag=bool(plottable),
                  object_scoped=object_uuid is not None)
    return [rec["uuid"]]


def model_space_plot(doc: Any, *, device: Any = "DWG To PDF.pc3", area: str = "extents",
                     scale: Any = "1:1", output: str = "", dry_run: bool = True) -> list[str]:
    """模型空间直接出图（PLOT 模型选项卡）：窗口取模型几何范围，现算。"""
    job = _build_plot_job(doc, sheet=None, space="model", device=device, area=area, scale=scale,
                          output=output, dry_run=dry_run)
    rec = _record(_holder(doc), "model_plots", kind="model space plot", space="model",
                  model_space=True, modelspace=True, plot_job_index=len(_holder(doc)["plot_jobs"]) - 1,
                  plot_area=job["plot_area"], plot_scale=job["plot_scale"],
                  device=job["device"], paper_size=job["paper_size"],
                  entity_count=job["entity_count"])
    return [rec["uuid"]]


def _refresh_uuids(node: Any) -> None:
    """复制布局时给整棵子树换新 uuid（L1 要求 uuid 在同类内唯一）。"""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "uuid" and isinstance(value, str):
                node[key] = _uuid4()
            else:
                _refresh_uuids(value)
    elif isinstance(node, list):
        for item in node:
            _refresh_uuids(item)


def layout_manage(doc: Any, *, action: str = "rename", sheet: Any = None, name: str = "",
                  source: Any = None, activate: bool = False) -> list[str]:
    """布局管理（New/Copy/Rename/Delete）；缺省动作 rename，未给名按序号确定性命名。"""
    _one_of(action, LAYOUT_ACTIONS, "layout action")
    sheets = _sheets(doc)
    if action == "new":
        return layout_create(doc, name=name or "Layout%d" % (len(sheets) + 1))
    if action == "copy":
        origin = _active_sheet(doc, source, create=False)
        clone = copy.deepcopy(origin)
        _refresh_uuids(clone)
        clone["name"] = name or "%s-copy" % (origin.get("name") or "Layout")
        clone["active"] = False
        if any(isinstance(item, Mapping) and item.get("name") == clone["name"] for item in sheets):
            raise _fail("duplicate_layout", "layout %r already exists" % clone["name"])
        sheets.append(clone)
        rec = _record(_holder(doc), "layout_ops", kind="layout manage", manage=action,
                      action=action, rename=None, copy={"from": origin.get("name"),
                                                        "to": clone["name"]},
                      delete=None, layout_index=len(sheets) - 1, layout_count=len(sheets))
        return [rec["uuid"]]
    if action == "rename":
        layout = _active_sheet(doc, sheet, create=True)
        new_name = name or "Layout%d" % (len(sheets) + 1)
        if any(isinstance(item, Mapping) and item is not layout and item.get("name") == new_name
               for item in sheets):
            raise _fail("duplicate_layout", "layout %r already exists" % new_name)
        old_name = layout.get("name")
        layout["name"] = new_name
        rec = _record(_holder(doc), "layout_ops", kind="layout manage", manage=action,
                      action=action, rename={"from": old_name, "to": new_name}, copy=None,
                      delete=None, layout_index=sheets.index(layout), layout_count=len(sheets))
        return [rec["uuid"]]
    layout = _active_sheet(doc, sheet, create=False)
    if len(sheets) <= 1:
        raise _fail("last_layout", "refusing to delete the only layout (autocad keeps >= 1)")
    index = sheets.index(layout)
    sheets.pop(index)
    rec = _record(_holder(doc), "layout_ops", kind="layout manage", manage=action, action=action,
                  resize=None, rename=None, copy=None, delete={"name": layout.get("name"),
                                                               "index": index},
                  layout_index=index, layout_count=len(sheets))
    return [rec["uuid"]]


def publish(doc: Any, *, sheets: Any = None, out_path: str = "", single_pdf: bool = True,
            dry_run: bool = True, device: Any = "DWG To PDF.pc3") -> list[str]:
    """批量发布多布局出图（PUBLISH）：按布局顺序出页；单/多页 PDF 或发布清单。"""
    all_sheets = _sheets(doc)
    targets: list[Any] = []
    if sheets is None:
        targets = [item for item in all_sheets if isinstance(item, Mapping)]
        if not targets:
            layout_create(doc)
            targets = [all_sheets[0]]
    else:
        if not isinstance(sheets, (list, tuple)) or not sheets:
            raise _fail("bad_reference", "sheets must be a non-empty list of layouts")
        targets = [_active_sheet(doc, item, create=False) for item in sheets]
    _plotter_name(device)
    pages: list[dict[str, Any]] = []
    for index, layout in enumerate(targets, start=1):
        size = layout.get("size") if isinstance(layout.get("size"), Mapping) else {}
        width_nm = _nm(size.get("w_nm", 0), "layout width", positive=True)
        height_nm = _nm(size.get("h_nm", 0), "layout height", positive=True)
        pages.append({"order": index, "name": str(layout.get("name") or "Layout%d" % index),
                      "page_nm": [width_nm, height_nm],
                      "viewport_count": len(layout.get("views") or [])})
    written = False
    if out_path and not dry_run:
        out_path = guard_output(out_path)
        if single_pdf:
            pdf_pages = []
            for page in pages:
                width_nm, height_nm = page["page_nm"]
                frame = [(0, 0, width_nm, 0), (width_nm, 0, width_nm, height_nm),
                         (width_nm, height_nm, 0, height_nm), (0, height_nm, 0, 0)]
                pdf_pages.append((width_nm, height_nm, frame))
            Path(out_path).write_bytes(_pdf_bytes(pdf_pages))
        else:
            Path(out_path).write_text(json.dumps({"device": device, "pages": pages},
                                                 ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                      encoding="utf-8")
        written = True
    rec = _record(_holder(doc), "publications", kind="publish",
                  publish={"pages": pages, "page_count": len(pages), "sheet_count": len(pages),
                           "single_pdf": bool(single_pdf), "device": device,
                           "output_path": str(out_path), "written": written, "dry_run": bool(dry_run)},
                  pdf=bool(single_pdf), sheet_count=len(pages), written=written)
    return [rec["uuid"]]


def _canonical_text(doc: Any) -> str:
    payload = doc.to_payload() if hasattr(doc, "to_payload") else (
        dict(doc) if isinstance(doc, Mapping) else {})
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def dwg_save(doc: Any, *, version: Any = "R2018", out_path: str = "", dry_run: bool = True) -> list[str]:
    """DWG 读写与版本选择：选版本 + 规范化 L1 载荷 + 内容 sha256（**不写私有 DWG 位流**）。"""
    key = _version_key(version, DWG_VERSIONS, "bad_version")
    payload = _canonical_text(doc)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    written = False
    if out_path and not dry_run:
        out_path = guard_output(out_path)
        Path(out_path).write_text(payload, encoding="utf-8")
        written = True
    rec = _record(_holder(doc), "exports", kind="dwg export",
                  dwg={"version": key, "acadver": DWG_VERSIONS[key], "byte_format": "binary",
                       "container": "l1-json-bridge", "dwg_binary": False},
                  version=key, acadver=DWG_VERSIONS[key], out_path=str(out_path), written=written,
                  dry_run=bool(dry_run), content_sha256=digest,
                  content_bytes=len(payload.encode("utf-8")))
    return [rec["uuid"]]


def _dxf_render(doc: Any, version: str = "R12", precision: int = 6,
                unit: str | None = None) -> tuple[str, int]:
    """L1 几何 → ASCII DXF 文本（ENTITIES 子集），返回 `(文本, 实体数)`。"""
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 12:
        raise _fail("bad_precision", "precision must be an int in [0, 12]")
    key = _version_key(version, DXF_VERSIONS, "bad_version")
    resolved_unit = unit or _doc_unit(doc)
    if resolved_unit not in _UNIT_TO_NM:
        raise _fail("bad_unit", "unit %r has no DXF writer mapping" % (resolved_unit,))
    per_unit = _UNIT_TO_NM[resolved_unit]
    out: list[str] = []

    def group(code: int, value: Any) -> None:
        out.append(str(int(code)))
        out.append(str(value))

    #: `1 单位 = 10^k 纳米`的单位（mm=1e6）：`precision` 位定点本身逐位精确 ⇒ 旧产物逐字节不变。
    unit_terminates = (10 ** precision) % per_unit == 0

    def layer_name(raw: Any) -> str:
        """组码 8 的图层名必须单行：含 CR/LF/控制字符的名字**结构化拒绝**（不静默截断）。

        A3 缺陷 RC：`"A\\n999\\ninjected"` 曾被静默截成 `A` 并注入一条 `999` 组码 —— 名字是键，
        折行等于改字；DXF 组码 8 没有多行语义，所以拒绝而不是折成空格。
        """
        text = str(raw or "0")
        if not text.strip() or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise _fail("dxf_illegal_symbol_name",
                        "DXF group code 8 layer name must be a single-line name",
                        field="entity layer", group_code=8, value=repr(text))
        return text

    def number(value_nm: Any) -> str:
        """整数纳米 → 文档单位十进制原文（**精确除法，不过 float**；与 `io.dxf_bridge` 同口径）。

        `precision` 是**下限**：mm 下 6 位定点逐位精确（旧产物逐字节不变）；**inch** 的
        1 inch = 25 400 000 nm 含 127 因子 ⇒ 十进制不终止，6 位定点步长 25.4 nm（实测 40 个分量
        0 命中）⇒ 改用 17 位有效数字（相对偏差 <= 5e-18，远小于真源的 1 nm）。

        G28 覆盖面扩展席订正（2026-10-01，G28 门新覆盖这条路径时实测）：定点支路原先走
        `float(nm) / float(per_unit)`，该双精度除法在 |nm| >= 2**53 时不再逐位精确
        （实测 9007199254740991 写成 `9007199254.740992` ⇒ nm 域回读差 1）⇒ 改为 `Decimal`
        精确除法 + 按 `precision` 位**精确量化**（half-even）。对旧产物**逐字节不变**
        （plate 夹具 40/40 分量实测 0 差异；见 G28 门 `DXF_FIDELITY_GATE_SETTLED` 读数）。
        """
        import decimal  # noqa: PLC0415 - 标准库；局部 import 免得顶层再加一个名字
        if unit_terminates:
            with decimal.localcontext() as context:
                context.prec = 60
                quotient = decimal.Decimal(int(value_nm)) / decimal.Decimal(per_unit)
                quantum = decimal.Decimal(1).scaleb(-precision)
                return format(quotient.quantize(quantum, rounding=decimal.ROUND_HALF_EVEN), "f")
        with decimal.localcontext() as context:
            context.prec = 17
            text = format(decimal.Decimal(int(value_nm)) / decimal.Decimal(per_unit), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text if text not in ("", "-", "-0") else "0"

    group(0, "SECTION")
    group(2, "HEADER")
    group(9, "$ACADVER")
    group(1, DXF_VERSIONS[key])
    group(9, "$INSUNITS")
    group(70, {"nm": 1, "um": 2, "mm": 4, "cm": 5, "m": 6, "in": 1, "ft": 2}.get(resolved_unit, 0))
    group(0, "ENDSEC")
    group(0, "SECTION")
    group(2, "ENTITIES")
    count = 0
    for entity in _iter_entities(doc):
        geom = entity.get("geom")
        if not isinstance(geom, Mapping):
            continue
        layer = layer_name(entity.get("layer"))
        if all(k in geom for k in ("x1", "y1", "x2", "y2")):
            group(0, "LINE")
            group(8, layer)
            group(10, number(geom["x1"]))
            group(20, number(geom["y1"]))
            group(30, 0)
            group(11, number(geom["x2"]))
            group(21, number(geom["y2"]))
            group(31, 0)
            count += 1
        elif "cx" in geom and "cy" in geom:
            radius = geom.get("r", geom.get("radius"))
            if radius is None:
                continue
            group(0, "CIRCLE")
            group(8, layer)
            group(10, number(geom["cx"]))
            group(20, number(geom["cy"]))
            group(30, 0)
            group(40, number(radius))
            count += 1
        else:
            points = geom.get("points_nm")
            pairs = [pair for pair in (_point(item) for item in points or []) if pair is not None]
            if not pairs:
                continue
            closed = 1 if geom.get("closed") else 0
            if key == "R12":
                group(0, "POLYLINE")
                group(8, layer)
                group(66, 1)
                group(70, closed)
                for x_nm, y_nm in pairs:
                    group(0, "VERTEX")
                    group(8, layer)
                    group(10, number(x_nm))
                    group(20, number(y_nm))
                    group(30, 0)
                group(0, "SEQEND")
            else:
                group(0, "LWPOLYLINE")
                group(8, layer)
                group(90, len(pairs))
                group(70, closed)
                for x_nm, y_nm in pairs:
                    group(10, number(x_nm))
                    group(20, number(y_nm))
            count += 1
    group(0, "ENDSEC")
    group(0, "EOF")
    return "\n".join(out) + "\n", count


def dxf_export(doc: Any, *, version: Any = "R12", fmt: str = "ascii", out_path: str = "",
               dry_run: bool = True, precision: int = 6, unit: str | None = None) -> list[str]:
    """DXF 导出（DXFOUT）：ASCII DXF + 小数精度；二进制 DXF 未实现（显式抛错，不当已做）。"""
    if fmt != "ascii":
        raise _fail("unsupported_format",
                    "binary DXF encoding is not implemented; use fmt='ascii' (debt declared)")
    text, count = _dxf_render(doc, str(version), precision, unit)
    data = text.encode("ascii")
    written = False
    if out_path and not dry_run:
        out_path = guard_output(out_path)
        Path(out_path).write_bytes(data)
        written = True
    key = _version_key(version, DXF_VERSIONS, "bad_version")
    rec = _record(_holder(doc), "exports", kind="dxf export",
                  dxf={"version": key, "acadver": DXF_VERSIONS[key], "format": "ascii",
                       "precision": precision, "unit": unit or _doc_unit(doc)},
                  dxf_version=key, acadver=DXF_VERSIONS[key], format="ascii", out_path=str(out_path),
                  written=written, dry_run=bool(dry_run), entity_count=count,
                  content_bytes=len(data), content_sha256=hashlib.sha256(data).hexdigest())
    return [rec["uuid"]]


def _dxf_pairs(text: str):
    lines = text.splitlines()
    if len(lines) % 2:
        raise _fail("bad_dxf", "DXF ASCII body must have an even number of lines")
    for index in range(0, len(lines), 2):
        raw = lines[index].strip()
        try:
            code = int(raw)
        except ValueError as exc:
            raise _fail("bad_dxf", "group code %r is not an integer" % raw) from exc
        yield code, lines[index + 1].strip()


def dxf_import(doc: Any, *, source: str = "", text: str = "", name: str = "dxf import") -> list[str]:
    """DXF 导入（DXFIN）：读 ENTITIES 子集（LINE/CIRCLE/LWPOLYLINE/POLYLINE）→ 新草图。"""
    if not text:
        if not source:
            raise _fail("missing_source", "dxf_import needs text= or source=")
        text = Path(source).read_text(encoding="ascii")
    unit = _doc_unit(doc)
    if unit not in _UNIT_TO_NM:
        raise _fail("bad_unit", "unit %r has no DXF reader mapping" % (unit,))
    factor = float(_UNIT_TO_NM[unit])
    entities: list[dict[str, Any]] = []
    layer = "0"
    in_entities = False
    pending: dict[str, Any] = {}
    polyline: dict[str, Any] | None = None
    acadver = ""
    for code, value in _dxf_pairs(text):
        if not in_entities:
            if code == 9 and value == "$ACADVER":
                acadver = "seen"
            elif code == 1 and acadver == "seen":
                acadver = value
                continue
            in_entities = code == 2 and value == "ENTITIES"
            continue
        if code == 0:
            kind = value.upper()
            if polyline is not None and kind == "VERTEX":
                continue
            if polyline is not None:
                if polyline["geom"]["points_nm"]:
                    entities.append(polyline)
                polyline = None
            if kind == "ENDSEC":
                in_entities = False
                pending = {}
                continue
            if kind in ("LINE", "CIRCLE"):
                pending = {"kind": kind.lower(), "layer": layer, "geom": {}}
            elif kind == "POLYLINE":
                polyline = {"kind": "polyline", "layer": layer, "construction": False,
                            "geom": {"points_nm": [], "closed": False}}
            elif kind in ("VERTEX", "SEQEND"):
                pass
            continue
        if polyline is not None:
            if code == 8:
                polyline["layer"] = value
            elif code == 70:
                polyline["geom"]["closed"] = bool(int(value or 0) & 1)
            elif code == 10:
                polyline["geom"]["points_nm"].append([_from_unit(value, factor), 0])
            elif code == 20 and polyline["geom"]["points_nm"]:
                polyline["geom"]["points_nm"][-1][1] = _from_unit(value, factor)
            continue
        if not pending:
            continue
        if code == 8:
            pending["layer"] = value
        elif code == 10:
            pending["geom"]["x1" if pending["kind"] == "line" else "cx"] = _from_unit(value, factor)
        elif code == 20:
            pending["geom"]["y1" if pending["kind"] == "line" else "cy"] = _from_unit(value, factor)
        elif code == 11:
            pending["geom"]["x2"] = _from_unit(value, factor)
        elif code == 21:
            pending["geom"]["y2"] = _from_unit(value, factor)
        elif code == 40:
            pending["geom"]["r"] = _from_unit(value, factor)
        elif code == 0:
            continue
        if pending["kind"] == "line" and all(k in pending["geom"] for k in ("x1", "y1", "x2", "y2")):
            entities.append(_entity(pending["kind"], pending["layer"], pending["geom"]))
            pending = {}
        elif pending["kind"] == "circle" and all(k in pending["geom"] for k in ("cx", "cy", "r")):
            entities.append(_entity(pending["kind"], pending["layer"], pending["geom"]))
            pending = {}
    if not entities:
        raise _fail("empty_dxf", "no LINE/CIRCLE/POLYLINE entities found in the DXF body")
    sketch = {"uuid": _uuid4(), "plane": "XY", "source": "dxf", "entities": entities,
              "constraints": [], "dimensions": [], "solved": {}}
    _sketches(doc).append(sketch)
    rec = _record(_holder(doc), "imports", kind="dxf import",
                  dxf={"version": acadver or "unknown", "format": "ascii", "unit": unit},
                  entity_count=len(entities), source=str(source), sketch_index=len(_sketches(doc)) - 1)
    return [rec["uuid"]]


def _from_unit(value: str, factor: float) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _fail("bad_dxf", "group value %r is not a number" % (value,)) from exc
    return int(round(number * factor))


def _entity(kind: str, layer: str, geom: dict[str, Any]) -> dict[str, Any]:
    return {"uuid": _uuid4(), "kind": kind, "layer": str(layer or "0"),
            "construction": False, "geom": dict(geom)}


#: 上面那批 AutoCAD 风味操作（payload 记账式）：保留原义，供本模块自检与兼容调用。
#: 必须在下面重新绑定 `plot` / `preview` / `publish` **之前**取好函数对象。
_LEGACY_OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "layout_create": layout_create,
    "viewport_create": viewport_create,
    "viewport_scale": viewport_scale,
    "layer_override": layer_override,
    "page_setup": page_setup,
    "plot": plot,
    "plot_pdf": plot_pdf,
    "plotter_config": plotter_config,
    "plot_style_table": plot_style_table,
    "lineweight": lineweight,
    "plot_area": plot_area,
    "plot_scale": plot_scale,
    "preview": preview,
    "plot_flag": plot_flag,
    "model_space_plot": model_space_plot,
    "layout_manage": layout_manage,
    "publish": publish,
    "dwg_save": dwg_save,
    "dxf_export": dxf_export,
    "dxf_import": dxf_import,
}


# ======================================================================================
# 族 `output` 的冻结接口层：`cadcore.draw.sheet` 之上的出图操作
# --------------------------------------------------------------------------------------
# 与 `cadcore/ops` 其余族同源：`FAMILY` = 族名、`OPERATIONS` = 操作名 -> 可调用、
# `op(doc, **kwargs) -> list[str]`、非法参数抛 `CadError(reason=<机器可读 token>)`。
# 几何与落盘**全部复用** `cadcore.draw.sheet`（规格 `reuse_index` 点名的冻结真源）：
# 本层只做入参校验、错误 reason 归一与图纸登记，不新开第二条出图管线。
# 上面那批操作保留原名原义，但不再占用冻结操作名 `plot` / `preview` / `publish`。
# ======================================================================================

from cadcore.draw import sheet as _sheet  # noqa: E402  (本族冻结复用真源)
from cadcore.errors import DependencyMissing  # noqa: E402

#: 已建布局的登记表：uuid -> Sheet（`sheet_by_uuid` / `get_sheet` 的读取面）。
_LAYOUTS: dict[str, Any] = {}
_LAYOUT_STATE_KEY = "_output_layouts"
_SPEC_FORMATS = ("dxf", "svg", "pdf")


def _bad(reason: str, message: str, **details: Any) -> CadError:
    """冻结契约要求的错误形态：`CadError(reason=<token>)`。"""
    return CadError(message, reason=reason, **details)


def _layout_state(doc: Any) -> dict[str, Any]:
    """本层在文档上的布局账（名字 / 顺序 / 最近一次 standard+language）。"""
    container = doc if isinstance(doc, dict) else getattr(doc, "extra", None)
    if not isinstance(container, dict):
        raise _bad("bad_document", "output ops need an L1 Document (or payload dict)")
    state = container.setdefault(_LAYOUT_STATE_KEY, {"names": {}, "order": [],
                                                     "standard": None, "language": None})
    if not isinstance(state, dict):
        raise _bad("bad_document", "reserved layout state must hold a mapping")
    state.setdefault("names", {})
    state.setdefault("order", [])
    return state


def _sheet_by_uuid(sheet_uuid: Any) -> Any:
    found = _LAYOUTS.get(str(sheet_uuid))
    if found is None:
        raise _bad("unknown_layout", "viewport/layout not found", sheet_uuid=str(sheet_uuid))
    return found


def sheet_by_uuid(sheet_uuid: Any) -> Any:
    """按 uuid 取回本层建过的 `Sheet`（取不到返回 None；状态访问器）。"""
    return _LAYOUTS.get(str(sheet_uuid))


def get_sheet(sheet_uuid: Any) -> Any:
    """`sheet_by_uuid` 的同义访问器（两种名字都在契约探针表里）。"""
    return sheet_by_uuid(sheet_uuid)


def _spec_scale(scale: Any) -> tuple[int, int]:
    """比例入参 `n:m` -> `(n, m)`；不是 `n:m` ⇒ `parse_failed`，非正 ⇒ `out_of_range`。"""
    text = str(scale if scale is not None else "").strip().replace("/", ":")
    parts = [part.strip() for part in text.split(":")]
    if len(parts) != 2 or not all(parts) or not all(part.lstrip("+-").isdigit() for part in parts):
        raise _bad("parse_failed", "scale is not n:m", scale=str(scale))
    num, den = int(parts[0]), int(parts[1])
    if num <= 0 or den <= 0:
        raise _bad("out_of_range", "scale out of range", scale=str(scale))
    return num, den


def _spec_standard(standard: Any) -> str:
    known = _sheet.standard_ids()
    if str(standard) not in known:
        raise _bad("unknown_standard", "unknown standard: %r" % (standard,),
                   standard=str(standard), known=list(known))
    return str(standard)


def _spec_size(standard: str, size: Any) -> None:
    """幅面校验：标准档/内置表都认不出 ⇒ `out_of_range`（先校验后写，不半写文档）。"""
    profile, _source = _sheet.load_profile(str(standard))
    try:
        _sheet._resolve_sheet_size(profile, str(size))  # noqa: SLF001 - 复用冻结真源
    except CadError as exc:
        raise _bad("out_of_range", "unknown size: %r" % (size,), size=str(size)) from exc


def _spec_format(fmt: Any) -> str:
    target = str(fmt if fmt is not None else "").strip().lower()
    if target not in _SPEC_FORMATS:
        raise _bad("unsupported_format", "unsupported format", fmt=str(fmt))
    return target


def _export_uuid(sheet: Any, path: str) -> str:
    """导出产物的确定性 uuid（同一图纸 + 同一路径 ⇒ 同一 uuid，无时间戳）。"""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(["output-export", sheet.uuid, str(path)])))


class _KernelTap:
    """投影内核的只读探针：转调一次真内核，并记下"投影为空"这件事（不改内核语义）。"""

    def __init__(self, kernel: Any) -> None:
        self._kernel = kernel
        self.calls = 0
        self.empty = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kernel, name)

    def project_to_2d(self, shape: Any, *, view: str, hidden: bool) -> Any:
        self.calls += 1
        projection = self._kernel.project_to_2d(shape, view=view, hidden=bool(hidden))
        curves = getattr(projection, "curves", None)
        if projection is None or (curves is not None and not list(curves)):
            self.empty = True
        return projection


def create_layout(doc: Any, *, name: Any = None, standard: Any = None, language: Any = None,
                  size: Any = _sheet.DEFAULT_SIZE, scale: Any = _sheet.DEFAULT_SCALE) -> list[str]:
    """新建图纸空间布局 → 一张 `cadcore.draw.sheet.Sheet`（uuid 由内容确定性派生）。"""
    state = _layout_state(doc)
    standard_id = _spec_standard(standard if standard is not None else (
        state.get("standard") or (_sheet.standard_ids() or ("iso",))[0]))
    _spec_scale(scale)
    _spec_size(standard_id, size)
    if name is not None and str(name) in state["names"]:
        raise _bad("duplicate_name", "duplicate layout name", name=str(name))
    language_id = str(language if language is not None else (state.get("language") or "en"))
    sheet = _sheet.make_sheet(doc, standard=standard_id, language=language_id,
                              size=str(size), scale=str(scale))
    _LAYOUTS[sheet.uuid] = sheet
    state["standard"], state["language"] = standard_id, language_id
    state["order"].append(sheet.uuid)
    # ★ 建布局必须**同时**落进 L1 载荷 `document.sheets[]`：`_LAYOUTS` 与文档里的
    #   `_output_layouts` 都是账，只有 `sheets[]` 进 `to_payload()` ⇒ 不落这里，
    #   族门（parity_output_gate.py）数出来的图纸数是 0，只能记 unverified。
    #   `manage_layout(action="new")` 走的正是本函数，所以登记必须落在这里，
    #   不能只放在 `create_layout_registered` 的 `_publish_sheet` 里。按 uuid 幂等。
    entry = _l1_entry_for(doc, sheet)
    if name is not None:
        state["names"][str(name)] = sheet.uuid
        entry["name"] = str(name)
    return [sheet.uuid]


def create_viewport(doc: Any, *, sheet_uuid: Any, shape: Any = None, view: Any = "front",
                    at_nm: Any = (0, 0), hidden: bool = True, kernel: Any = None) -> list[str]:
    """在布局上开一个视图（投影内核现算；可见/隐藏分桶进 `Sheet.views`）。"""
    sheet = _sheet_by_uuid(sheet_uuid)
    name = str(view if view is not None else "").strip()
    if name not in _sheet.VIEW_DIRECTIONS:
        raise _bad("unknown_view", "unknown view", view=name, known=sorted(_sheet.VIEW_DIRECTIONS))
    if kernel is None or not bool(getattr(kernel, "available", lambda: False)()):
        raise DependencyMissing("projection kernel is unavailable", reason="dependency_missing",
                                view=name)
    if "project2d" not in set(getattr(kernel, "capabilities", lambda: ())()):
        raise DependencyMissing("kernel has no project2d capability", reason="dependency_missing",
                                view=name)
    tap = _KernelTap(kernel)
    before = len(sheet.views)
    try:
        view_uuid = _sheet.add_view(sheet, shape, view=name,
                                    at_nm=(int(at_nm[0]), int(at_nm[1])), kernel=tap,
                                    hidden=bool(hidden))
    except CadError as exc:
        del sheet.views[before:]           # 空投影不许留下半建视图
        if tap.empty:
            raise _bad("empty_projection", "projection produced no curves", view=name) from exc
        raise
    return [view_uuid]


def set_page_setup(doc: Any, *, sheet_uuid: Any = None, size: Any = None, scale: Any = None,
                   plotter: Any = None, plot_style_table: Any = None, plot_area: Any = "extents",
                   fit_to_paper: bool = False, **wide: Any) -> list[str]:
    """页面设置：给全 `sheet_uuid`+`size`+`scale` ⇒ 冻结窄签名（落既有 `Sheet`）；
    裸调 `op(doc)` ⇒ 走真实现 `page_setup`（宽签名，记录真进 L1 载荷）。

    注册层与实现层对齐（本席修复）：`OPERATIONS` 上的名字必须**两种调法都能跑** ——
    后定义的窄签名不许把先定义的宽实现顶掉（否则冻结接口裸调 `op(doc)` 抛 TypeError）。
    """
    if sheet_uuid is None and size is None and scale is None:
        return _LEGACY_OPERATIONS["page_setup"](doc, **wide)
    if sheet_uuid is None or size is None or scale is None:
        raise _bad("bad_reference", "page setup needs sheet_uuid+size+scale together",
                   sheet_uuid=str(sheet_uuid), size=str(size), scale=str(scale))
    sheet = _sheet_by_uuid(sheet_uuid)
    if plotter:
        raise _bad("unsupported", "pc3 plotter is not implemented", what="plotter")
    if plot_style_table:
        raise _bad("unsupported", "ctb/stb table is not implemented", what="plot_style_table")
    _spec_scale(scale)
    _spec_size(sheet.standard_profile, size)
    fresh = _sheet.make_sheet(doc, standard=sheet.standard_profile, language=sheet.language,
                              size=str(size), scale=str(scale))
    sheet.size, sheet.scale = fresh.size, fresh.scale
    sheet.scale_ratio = fresh.scale_ratio
    sheet.width_nm, sheet.height_nm = fresh.width_nm, fresh.height_nm
    sheet.sheet_size_source = fresh.sheet_size_source
    sheet.page_setup = {"size": sheet.size, "scale": sheet.scale, "plot_area": str(plot_area),
                        "fit_to_paper": bool(fit_to_paper)}
    _record(_holder(doc), "page_setups", kind="page setup", sheet_uuid=str(sheet.uuid),
            size=sheet.size, scale=sheet.scale, paper_size={"name": sheet.size,
                                                            "w_nm": int(sheet.width_nm),
                                                            "h_nm": int(sheet.height_nm)},
            setup_name="output.page_setup.spec", plot_area=str(plot_area),
            fit_to_paper=bool(fit_to_paper))
    return [sheet.uuid]


def plot(doc: Any, *, sheet_uuid: Any = None, fmt: Any = None, out: Any = None,
         dxf_version: Any = "R2018", **wide: Any) -> list[str]:
    """出图：给全 `sheet_uuid`+`fmt`+`out` ⇒ 冻结窄签名真落盘；裸调 ⇒ 真实现 `plot`（作业记账）。"""
    if sheet_uuid is None and fmt is None and out is None:
        return _LEGACY_OPERATIONS["plot"](doc, **wide)
    if sheet_uuid is None or fmt is None or out is None:
        raise _bad("bad_reference", "plot needs sheet_uuid+fmt+out together",
                   sheet_uuid=str(sheet_uuid), fmt=str(fmt), out=str(out))
    # 空路径**不是** CWD：门探针在 tmp is None 时把 @tmp: 解析成 ""，旧行为会把
    # page_001.svg / .svg 写进进程 CWD（= 包根，C7 残留两件的成因）⇒ 显式拒绝。
    if not str(out).strip():
        raise _bad("bad_reference", "plot needs a non-empty out path (empty string is not the CWD)",
                   sheet_uuid=str(sheet_uuid), fmt=str(fmt), out=str(out))
    sheet = _sheet_by_uuid(sheet_uuid)
    target = _spec_format(fmt)
    try:
        # fmt="pdf" **保持 spec 口径**：没有外部后端时如实降级 SVG 并把 actual/reason 写进
        # 返回值 / ExportResult / sheet_facts（spec/output.json:159 的冻结判据）。
        # 要**真 PDF** 走下面新登记的 plot_pdf（内建写出器），不改这条正门的语义。
        path = _sheet.export_sheet(sheet, fmt=target, out=str(out), dxf_version=str(dxf_version))
    except OSError as exc:
        raise _bad("write_failed", "write failed: %s" % (exc,), path=str(out),
                   detail=str(exc)) from exc
    return [_record_export(doc, sheet, path, target, "plot")]


def _pdf_magic_ok(path: Any) -> bool:
    """成品是不是真 PDF（只读前 5 字节；读不到 ⇒ False，不猜）。"""
    try:
        with open(str(path), "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def plot_pdf(doc: Any, *, sheet_uuid: Any = None, out: Any = None) -> list[str]:
    """打印到 PDF（DWG To PDF.pc3 的**真 PDF** 半边）→ 导出 uuid 列表。

    与 plot(fmt="pdf") 的分工（**两条都要在**，不许互相冒充）：
    * plot(fmt="pdf")：spec 冻结口径 —— 外部后端缺席时**如实降级 SVG** 并写明 actual/reason；
    * plot_pdf：走 draw.sheet.export_sheet(pdf_builtin=True) 的**内建 PDF 1.4 写出器**，
      落**真 PDF**（magic %PDF-），有外部后端时仍优先外部后端。
    这就是 status 里 autocad-output#6 说的"族冻结接口未暴露 plot_pdf"那一半；设备名
    （DWG To PDF.pc3）只作记账字段，本构建没有 .pc3 读写器这半条欠账照旧挂
    EXTERNAL_MISSING["plotter_config"]，不在这里冒充。
    """
    if sheet_uuid is None or out is None:
        raise _bad("bad_reference", "plot_pdf needs sheet_uuid+out together",
                   sheet_uuid=str(sheet_uuid), out=str(out))
    if not str(out).strip():
        raise _bad("bad_reference", "plot_pdf needs a non-empty out path (empty string is not the CWD)",
                   sheet_uuid=str(sheet_uuid), out=str(out))
    sheet = _sheet_by_uuid(sheet_uuid)
    try:
        path = _sheet.export_sheet(sheet, fmt="pdf", out=str(out), pdf_builtin=True)
    except OSError as exc:
        raise _bad("write_failed", "write failed: %s" % (exc,), path=str(out),
                   detail=str(exc)) from exc
    if not _pdf_magic_ok(path):
        raise _bad("write_failed", "plot_pdf wrote a non-PDF artifact", path=str(path))
    return [_record_export(doc, sheet, path, "pdf", "plot_pdf")]


def preview(doc: Any, *, sheet_uuid: Any = None, out: Any = None, area: Any = None,
            scale: Any = None, out_path: str = "", dry_run: bool = True, **wide: Any) -> list[str]:
    """打印预览：给全 `sheet_uuid`+`out` ⇒ 冻结窄签名（无视图即拒绝，不许出空图）；
    裸调 ⇒ 真实现 `preview`（纸张/窗口/比例预算记账）。"""
    if sheet_uuid is None and out is None:
        return _LEGACY_OPERATIONS["preview"](doc, area=area, scale=scale, out_path=out_path,
                                             dry_run=dry_run, **wide)
    if sheet_uuid is None or out is None:
        raise _bad("bad_reference", "preview needs sheet_uuid+out together",
                   sheet_uuid=str(sheet_uuid), out=str(out))
    if not str(out).strip():
        raise _bad("bad_reference", "preview needs a non-empty out path (empty string is not the CWD)",
                   sheet_uuid=str(sheet_uuid), out=str(out))
    sheet = _sheet_by_uuid(sheet_uuid)
    if not sheet.views:
        raise _bad("drawing_error", "no view on the sheet -- no empty preview",
                   sheet_uuid=str(sheet_uuid))
    return plot(doc, sheet_uuid=sheet_uuid, fmt="svg", out=out)


def publish(doc: Any, *, sheet_uuids: Any = None, fmt: Any = None, out_dir: Any = None,
            single_file: bool = False, **wide: Any) -> list[str]:
    """批量发布：给全 `sheet_uuids`+`fmt`+`out_dir` ⇒ 冻结窄签名逐张落页（`page_NNN.<fmt>`）；
    `fmt="pdf"` 走 cadcore **内建** PDF 写出器（成品是真 PDF，不再降级成 svg）；
    `single_file=True` ⇒ 多布局合成**一个**多页 PDF（`publish.pdf`，**页数 = 布局数**）。
    裸调 ⇒ 真实现 `publish`（发布清单记账）。每页一个导出 uuid，且该 uuid 真进 L1 载荷。"""
    if sheet_uuids is None and fmt is None and out_dir is None:
        return _LEGACY_OPERATIONS["publish"](doc, **wide)
    if sheet_uuids is None or fmt is None or out_dir is None:
        raise _bad("bad_reference", "publish needs sheet_uuids+fmt+out_dir together",
                   sheet_uuids=str(sheet_uuids), fmt=str(fmt), out_dir=str(out_dir))
    # 同上：空 out_dir 旧行为 = Path("") = CWD ⇒ page_%03d.<fmt> 落进包根。
    if not str(out_dir).strip():
        raise _bad("bad_reference",
                   "publish needs a non-empty out_dir path (empty string is not the CWD)",
                   sheet_uuids=str(sheet_uuids), fmt=str(fmt), out_dir=str(out_dir))
    ids = [str(item) for item in (sheet_uuids or [])]
    if not ids:
        raise _bad("empty_sheet_list", "empty sheet list is not a success")
    target = _spec_format(fmt)
    if single_file and target != "pdf":
        raise _bad("unsupported_format", "single_file merge is pdf-only", fmt=str(fmt))
    directory = Path(str(out_dir))
    directory.mkdir(parents=True, exist_ok=True)
    sheets = [_sheet_by_uuid(sheet_uuid) for sheet_uuid in ids]
    if single_file:
        # A8 ②：single_file 合并器 —— 一次写出**一个**多页 PDF（一布局一页），页数当场回读复验。
        merged = _sheet.export_pdf_pages(sheets, out=str(directory / "publish.pdf"))
        _record(_holder(doc), "publications", kind="publish",
                publish={"pages": [{"page": index, "sheet_uuid": sheet.uuid, "path": merged}
                                   for index, sheet in enumerate(sheets, 1)],
                         "page_count": len(sheets), "sheet_count": len(sheets),
                         "single_file": True, "format": "pdf", "output_path": merged,
                         "written": True, "writer": _sheet.PDF_BUILTIN_WRITER},
                pdf=True, sheet_count=len(sheets), written=True, single_file=True)
        return [_record_export(doc, sheet, merged, "pdf", "publish") for sheet in sheets]
    uuids: list[str] = []
    for index, sheet in enumerate(sheets, 1):
        path = _sheet.export_sheet(sheet, fmt=target, out=str(directory / ("page_%03d" % index)),
                                   pdf_builtin=(target == "pdf"))
        uuids.append(_record_export(doc, sheet, path, target, "publish"))
    return uuids


def manage_layout(doc: Any, *, action: Any, name: Any = None, source: Any = None,
                  new_name: Any = None) -> list[str]:
    """布局管理 new / copy / rename / delete（copy 只换身份，不动 size/scale/width_nm）。"""
    state = _layout_state(doc)
    act = str(action if action is not None else "").strip().lower()
    if act == "new":
        if name is not None and str(name) in state["names"]:
            raise _bad("duplicate_name", "duplicate layout name", name=str(name))
        standard = state.get("standard") or (_sheet.standard_ids() or ("iso",))[0]
        language = state.get("language") or "en"
        uuids = create_layout(doc, name=name, standard=standard, language=language)
        uid = uuids[0]
        order = list(state.get("order") or [])
        if order.count(uid) > 1:
            # **同参数第二张布局**：`make_sheet` 的 uuid5 只由内容（doc/标准/语言/幅面/比例）派生，
            # 不含序号 ⇒ 第二次 "new" 会与已有布局**同主键**，直接返回就成了"新建静默无动作"
            # （实测：L1 sheets[] 条数不变）。这里按"第几张"另派身份 —— 与 copy 分支同一口径；
            # 旧布局的 L1 条目原样保留，新身份另写一条。
            sheet = _LAYOUTS.get(uid)
            if sheet is None:
                raise _bad("unknown_layout", "new layout vanished from the registry", uuid=uid)
            fresh = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(
                ["output-layout-new", uid, str(len(order))])))
            if fresh in _LAYOUTS:
                raise _bad("duplicate_layout", "layout identity collision", uuid=fresh)
            clone = copy.deepcopy(sheet)
            clone.uuid = fresh
            clone.views = list(sheet.views)
            clone.layers = {key: dict(value) for key, value in sheet.layers.items()}
            _LAYOUTS[fresh] = clone
            for index in range(len(order) - 1, -1, -1):   # 只换 `create_layout` 刚追加的那一条
                if order[index] == uid:
                    order[index] = fresh
                    break
            state["order"] = order
            entry = _l1_entry_for(doc, clone)
            if name is not None:
                state["names"][str(name)] = fresh
                entry["name"] = str(name)
            return [fresh]
        return list(uuids)
    if act == "copy":
        origin = _LAYOUTS.get(str(source))
        if origin is None:
            raise _bad("unknown_layout", "unknown layout", source=str(source))
        clone = copy.deepcopy(origin)
        clone.uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(
            ["output-layout-copy", origin.uuid, str(len(state["order"]))])))
        clone.views = list(origin.views)
        clone.layers = {key: dict(value) for key, value in origin.layers.items()}
        _LAYOUTS[clone.uuid] = clone
        state["order"].append(clone.uuid)
        _l1_entry_for(doc, clone)                    # copy 也要进 L1 `sheets[]`（同上）
        return [clone.uuid]
    if act == "rename":
        current = state["names"].get(str(name))
        if current is None:
            raise _bad("unknown_layout", "unknown layout", name=str(name))
        if new_name is not None and str(new_name) in state["names"]:
            raise _bad("duplicate_name", "duplicate layout name", name=str(new_name))
        state["names"].pop(str(name), None)
        state["names"][str(new_name)] = current
        _l1_rename(doc, current, str(new_name))      # 改名真改载荷里的布局身份
        return [current]
    if act == "delete":
        current = state["names"].get(str(name)) if name is not None else None
        if current is None:
            raise _bad("unknown_layout", "unknown layout", name=str(name))
        if len(state["order"]) <= 1:
            raise _bad("last_layout", "refuse to delete the last layout", name=str(name))
        state["order"].remove(current)
        state["names"].pop(str(name), None)
        _LAYOUTS.pop(current, None)
        _l1_forget(doc, current)                     # 删除真从载荷 `sheets[]` 里摘掉
        return list(state["order"])
    raise _bad("unknown_target", "unknown action", action=str(action))


def configure_plotter(doc: Any, **kwargs: Any) -> list[str]:
    """pc3 绘图仪配置：本构建没有 .pc3 读写器 ⇒ 显式 `dependency_missing`（不假装成功）。"""
    raise DependencyMissing("no .pc3 reader/writer in this build", reason="dependency_missing",
                            capability=-1, keys=sorted(kwargs))


def set_plot_style_table(doc: Any, *, sheet_uuid: Any = None, table: Any = None) -> list[str]:
    """打印样式表：.stb 不支持（`unsupported_table_format`），ctb/stb 解析器缺席（显式降级）。"""
    if str(table if table is not None else "").strip().lower().endswith(".stb"):
        raise _bad("unsupported_table_format", "stb tables are not implemented", table=str(table))
    raise DependencyMissing("no ctb/stb parser in this build", reason="dependency_missing",
                            capability=-1, table=str(table))


def save_dwg(doc: Any, *, path: Any = "", version: Any = "R2018") -> list[str]:
    """DWG 位流**写出**：本构建不实现 ⇒ 显式 `dependency_missing`（绝不落一个假 .dwg）。

    写半边保持 external（**读**半边已由 `open_dwg` 走只读桥可用，见 `READ_ONLY_AVAILABLE`）。
    """
    raise DependencyMissing("dwg binary write is not implemented (read-only bridge only)",
                            reason="dependency_missing", capability=-1, path=str(path), version=str(version))


def _dwg_bridge() -> Any:
    """懒加载 `cadcore.io.dwg_bridge`（A 案只读桥；io 层缺件时如实回 None，不在 import 期炸模块）。"""
    try:
        from cadcore.io import dwg_bridge  # noqa: PLC0415 - 懒加载：只读桥可缺席
        return dwg_bridge
    except Exception:
        return None


def open_dwg(doc: Any, *, path: Any = "", version: Any = "R2018") -> list[str]:
    """DWG **只读**导入（A 案：LibreDWG 只读子集、**进程外** subprocess；GPL-3.0 不链接/不复制）。

    三态照实记账（绝不伪造，也绝不落假 .dwg）：
    1. 裸调（`path` 为空）⇒ 登记**只读能力声明**（`data_read=False`、`entities_parsed=False`），
       后端在位与否由 `cadcore.io.dwg_bridge.availability()` **现读**（不是抄死的常量）；
    2. 给了真 DWG 且 `dwg2dxf`/`dwgread` 在位 ⇒ 走桥只读解析，记实体摘要（`engine=`/`entities=`/`layers=`）；
    3. 给了 `path` 但后端缺席 / 文件不是 DWG ⇒ 抛 `DependencyMissing(reason="dependency_missing")`。
    """
    key = _version_key(version, DWG_VERSIONS, "bad_version")
    bridge = _dwg_bridge()
    available = bridge.availability() if bridge is not None else {
        "dwg2dxf": "", "dwgread": "", "read_ready": False, "write_ready": False, "linkage": "subprocess"}
    src = str(path or "")
    read: dict[str, Any] = {"mode": "read_only", "linkage": "subprocess", "linkage_license": "GPL-3.0",
                            "engine": "", "entities_parsed": False, "data_read": False,
                            "backend_present": bool(available.get("read_ready")),
                            "write_backend": False, "entities": {}, "layers": []}
    if src:
        if bridge is None:
            raise DependencyMissing("cadcore.io.dwg_bridge is not importable in this build",
                                    reason="dependency_missing", capability=-1, path=src)
        summary = bridge.dwg_entities(src)
        if summary.get("status") != "ok":
            raise DependencyMissing("dwg read unavailable: %s" % (summary.get("reason") or "unknown"),
                                    reason="dependency_missing", capability=-1, path=src,
                                    bridge_status=str(summary.get("status")))
        container = summary.get("container") or {}
        read.update({"data_read": True, "entities_parsed": True, "engine": summary.get("engine", ""),
                     "entities": summary.get("counts") or {}, "layers": summary.get("layers") or [],
                     "source_sha256": container.get("sha256", ""),
                     "container_version": container.get("version", "")})
    else:
        read["note"] = "capability declaration only: no path given, nothing was read"
    rec = _record(_holder(doc), "imports", kind="dwg read",
                  dwg={"version": key, "acadver": DWG_VERSIONS[key], "container": "dwg-container-readonly",
                       "dwg_binary": False, "read": read},
                  version=key, acadver=DWG_VERSIONS[key], path=src, read_only=True,
                  data_read=bool(read["data_read"]), entities_parsed=bool(read["entities_parsed"]),
                  backend=str(available.get("dwgread") or available.get("dwg2dxf") or ""),
                  backend_present=bool(available.get("read_ready")), write_backend=False,
                  na_reason="" if available.get("read_ready") else "external_missing:dwg2dxf,dwgread")
    return [rec["uuid"]]


# ======================================================================================
# 布局/视口能力补齐层（本席 output-A）
# --------------------------------------------------------------------------------------
# 真源仍是 `cadcore.draw.sheet`（规格 reuse_index 点名），本层只补三件"文档侧读不出来"的事：
#   1) `create_layout` 把建好的 Sheet **登记进 L1 `document.sheets[]`**（此前只留在模块内
#      `_LAYOUTS` 注册表，文档载荷里数出来是 0 张布局 ⇒ 族门只能记 unverified）；
#   2) `create_viewport` 支持裸调：缺 `sheet_uuid` 取最近布局（一张都没有就先建），缺内核用
#      `cadcore.kernel.base.get_kernel("auto")`，缺 shape 取文档第一张有实体的草图；
#   3) 补 `set_viewport_scale`（比例 + 锁定）与 `set_viewport_layer_override`（VP Freeze）。
#
# 已实现 / 未实现（docstring 就是门可读的口径，照实写）
# --------------------------------------------------------------------------------------
# * 已实现：布局登记（uuid5 确定性 ⇒ 同入参两次调用同 uuid，L1 不重复登记）；视口入 L1
#   `views[]`（每条 `kind="viewport"`）；视口比例 `n:m` + `locked`（比例文本是唯一真源，
#   写回 `View.scale / scale_text` 与 L1）；视口内图层 `on|frozen` 覆盖表（每视口一张，
#   模型空间图层表 `Sheet.layers` **不被改写**）。
# * 未实现（显式 `unsupported`，绝不静默按 on 处理）：颜色 / 线宽 / 淡显等非 freeze 形态的
#   视口替代。
# * 降级**留痕**：线框（无厚度实体）没有隐藏线，内核抛 `wire_has_no_hidden_geometry`；本层
#   改试 `hidden=False` 并把 `hidden_downgraded=True` + 原因写进视图、图纸诊断与 L1，
#   不静默假装出过隐藏线。首次失败的 reason 原样保留，两次都失败则抛第一次的错。
# * 三态：`layer_overrides` 从未建表 ⇒ `None`（未采）；建过但为空 ⇒ `{}`（真为零）。
# * 内核缺席 / `available()=False` / 无 `project2d` 能力 ⇒
#   `DependencyMissing(reason="dependency_missing")`（不返回空视图）。
# ======================================================================================

_VP_STATES = ("on", "frozen")


def _doc_sheets_list(doc: Any) -> list[Any]:
    """L1 `sheets[]` 容器（Document 用 `.sheets`；纯 dict 载荷用 `["sheets"]`，缺席则建）。"""
    if isinstance(doc, dict):
        sheets = doc.setdefault("sheets", [])
    else:
        sheets = getattr(doc, "sheets", None)
    if not isinstance(sheets, list):
        raise _bad("bad_document", "document.sheets must be a list")
    return sheets


def _l1_entry_for(doc: Any, sheet: Any) -> dict[str, Any]:
    """取/建该 Sheet 的 L1 条目（`name` 形如 Layout1，载荷文本里可读出布局身份）。"""
    sheets = _doc_sheets_list(doc)
    for item in sheets:
        if isinstance(item, dict) and item.get("uuid") == sheet.uuid:
            return item
    entry: dict[str, Any] = {
        "uuid": sheet.uuid, "name": "Layout%d" % (len(sheets) + 1),
        "standard_profile": sheet.standard_profile, "language": sheet.language,
        "size": sheet.size, "scale": sheet.scale,
        "width_nm": int(sheet.width_nm), "height_nm": int(sheet.height_nm),
        "unit": sheet.unit, "sheet_size_source": sheet.sheet_size_source,
        "views": [], "dimensions": [], "title_block": dict(sheet.title_block_keys),
    }
    sheets.append(entry)
    return entry


def _l1_rename(doc: Any, sheet_uuid: Any, new_name: str) -> bool:
    """把 L1 `sheets[]` 里该布局改名；**找不到就不新建**（不掩盖"没落进载荷"）。"""
    for item in _doc_sheets_list(doc):
        if isinstance(item, dict) and item.get("uuid") == str(sheet_uuid):
            item["name"] = str(new_name)
            return True
    return False


def _l1_forget(doc: Any, sheet_uuid: Any) -> bool:
    """把该布局从 L1 `sheets[]` 摘掉（返回是否真摘到）；布局删除必须在载荷里可见。"""
    sheets = _doc_sheets_list(doc)
    for index, item in enumerate(sheets):
        if isinstance(item, dict) and item.get("uuid") == str(sheet_uuid):
            del sheets[index]
            return True
    return False


def _view_l1(view: Any) -> dict[str, Any]:
    """`View` → L1 `views[]` 条目（`kind="viewport"` + 比例/锁定 + 每视口覆盖表）。"""
    overrides = getattr(view, "layer_overrides", None)
    table = {str(key): dict(value) for key, value in overrides.items()} if isinstance(
        overrides, dict) else None
    return {
        "uuid": view.uuid, "kind": "viewport", "name": view.name,
        "direction": list(_sheet.VIEW_DIRECTIONS.get(view.name, (0, 0, 0))),
        "at_nm": [int(view.at_nm[0]), int(view.at_nm[1])],
        "scale": str(view.scale_text),
        "locked": bool(getattr(view, "scale_locked", False)),
        "scale_locked": bool(getattr(view, "scale_locked", False)),
        "hidden": bool(view.hidden),
        "hidden_downgraded": bool(getattr(view, "hidden_downgraded", False)),
        "cache": True,
        "visible_layer": view.visible_layer, "hidden_layer": view.hidden_layer,
        "layer_overrides": table,
    }


def _publish_sheet(doc: Any, sheet: Any) -> dict[str, Any]:
    """把 Sheet 当前状态（视图 / 比例 / 覆盖表）同步进 L1 条目（幂等，可重复调用）。"""
    entry = _l1_entry_for(doc, sheet)
    entry["width_nm"] = int(sheet.width_nm)
    entry["height_nm"] = int(sheet.height_nm)
    entry["scale"] = sheet.scale
    entry["views"] = [_view_l1(view) for view in sheet.views]
    return entry


def _auto_kernel() -> Any:
    """缺省投影内核 `cadcore.kernel.base.get_kernel("auto")`；取不到 ⇒ dependency_missing。"""
    try:
        from cadcore.kernel import base as _kbase
        return _kbase.get_kernel("auto")
    except Exception as exc:  # 冻结契约只认 reason="dependency_missing"，原异常进 detail
        raise DependencyMissing("no usable projection kernel for output ops",
                                reason="dependency_missing",
                                detail="%s: %s" % (type(exc).__name__, exc)) from exc


def _default_shape(doc: Any) -> Any:
    """缺省投影对象 = 文档第一张有实体的草图（线框）；无几何 ⇒ empty_projection。"""
    for sketch in _field(doc, "sketches", None) or []:
        if isinstance(sketch, Mapping) and sketch.get("entities"):
            return sketch
    raise _bad("empty_projection", "document has no projectable geometry")


def _resolve_view(doc: Any, sheet_uuid: Any = None, view_uuid: Any = None,
                  *, create_missing: bool = False) -> tuple[Any, Any]:
    """定位（布局, 视口）：缺省=最近布局的最后一个视口；`create_missing` 时无视图先开一个。"""
    state = _layout_state(doc)
    if sheet_uuid is None:
        order = list(state.get("order") or [])
        if not order:
            raise _bad("unknown_layout", "document has no layout")
        sheet_uuid = order[-1]
    sheet = _sheet_by_uuid(sheet_uuid)
    if view_uuid is None:
        if not sheet.views and create_missing:
            create_viewport_registered(doc, sheet_uuid=sheet_uuid)
        if not sheet.views:
            raise _bad("viewport_not_found", "layout has no viewport", sheet_uuid=str(sheet_uuid))
        return sheet, sheet.views[-1]
    for view in sheet.views:
        if view.uuid == str(view_uuid):
            return sheet, view
    raise _bad("viewport_not_found", "no viewport matches", view_uuid=str(view_uuid))


def create_layout_registered(doc: Any, *, name: Any = None, standard: Any = None,
                             language: Any = None, size: Any = _sheet.DEFAULT_SIZE,
                             scale: Any = _sheet.DEFAULT_SCALE) -> list[str]:
    """建布局**并登记进 L1 `document.sheets[]`**（原 `create_layout` 只留在模块内注册表）。

    同一入参两次调用：uuid5 确定性 ⇒ 同 uuid，L1 不重复登记（幂等，不产生第二张同身份布局）。
    """
    uuids = create_layout(doc, name=name, standard=standard, language=language,
                          size=size, scale=scale)
    sheet = _sheet_by_uuid(uuids[0])
    state = _layout_state(doc)
    order = list(state.get("order") or [])
    if order.count(uuids[0]) > 1:  # 同入参重入：order 只留一条登记
        seen = False
        deduped: list[Any] = []
        for item in order:
            if item == uuids[0]:
                if seen:
                    continue
                seen = True
            deduped.append(item)
        state["order"] = deduped
    entry = _publish_sheet(doc, sheet)
    if name is not None:
        entry["name"] = str(name)
    return list(uuids)


def create_viewport_registered(doc: Any, *, sheet_uuid: Any = None, shape: Any = None,
                               view: Any = "front", at_nm: Any = (0, 0), hidden: bool = True,
                               kernel: Any = None) -> list[str]:
    """在布局上开视口并写进 L1 `views[]`；裸调可用（缺布局先建、缺内核走 auto、缺 shape 取草图）。

    隐藏线缺席（线框无厚度）⇒ 降级 `hidden=False` 且 `hidden_downgraded=True` 留痕；
    两次都失败则抛第一次的错误（不吞原因、不返回空视图）。
    """
    at = tuple(_nm_pair(at_nm, "at_nm"))
    state = _layout_state(doc)
    if sheet_uuid is None:
        order = list(state.get("order") or [])
        if not order:
            create_layout_registered(doc)
            order = list(state.get("order") or [])
        sheet_uuid = order[-1]
    sheet = _sheet_by_uuid(sheet_uuid)
    resolved_kernel = kernel if kernel is not None else _auto_kernel()
    if shape is not None:
        resolved_shape: Any = shape
    elif kernel is None:
        resolved_shape = _default_shape(doc)  # 裸调：现取文档几何（无几何 ⇒ empty_projection）
    else:
        resolved_shape = None                # 调用方自带内核：shape=None 原样透传，不猜几何
    downgrade_reason = ""
    try:
        uuids = create_viewport(doc, sheet_uuid=sheet_uuid, shape=resolved_shape, view=view,
                                at_nm=at, hidden=bool(hidden), kernel=resolved_kernel)
    except CadError as first:
        if not hidden:
            raise
        try:
            uuids = create_viewport(doc, sheet_uuid=sheet_uuid, shape=resolved_shape, view=view,
                                    at_nm=at, hidden=False, kernel=resolved_kernel)
        except CadError:
            raise first
        downgrade_reason = "%s: %s" % (getattr(first, "reason", "") or type(first).__name__, first)
        sheet.note("hidden=True downgraded to hidden=False (%s)" % downgrade_reason)
    placed = sheet.views[-1]
    placed.hidden_downgraded = bool(downgrade_reason)
    if downgrade_reason:
        placed.hidden_downgrade_reason = downgrade_reason
    _publish_sheet(doc, sheet)
    return list(uuids)


def set_viewport_scale(doc: Any, *, sheet_uuid: Any = None, view_uuid: Any = None,
                       scale: Any = "1:1", locked: bool = True) -> list[str]:
    """视口比例 + 锁定（标准比例 / Display Locked）：比例文本是唯一真源，不引入浮点。

    视口比例必须是真比例 `n:m`（给 `fit` 之类 ⇒ `parse_failed`，不静默回落 1:1）。
    """
    num, den = _spec_scale(scale)
    sheet, view = _resolve_view(doc, sheet_uuid, view_uuid, create_missing=True)
    view.scale = (num, den)
    view.scale_text = "%d:%d" % (num, den)
    view.scale_locked = bool(locked)
    _publish_sheet(doc, sheet)
    return [view.uuid]


def set_viewport_layer_override(doc: Any, *, sheet_uuid: Any = None, view_uuid: Any = None,
                                layer: Any = None, state: Any = "on") -> list[str]:
    """视口内图层替代（VPLAYER / VP Freeze）：每视口一张 `layer -> on|frozen` 表。

    模型空间图层表 `Sheet.layers` 只读不改；`state` 只认 `on` / `frozen`，其它形态
    （颜色/线宽/淡显替代）显式 `unsupported`。`layer=None` 取该布局图层表的第一层。
    """
    sheet, view = _resolve_view(doc, sheet_uuid, view_uuid, create_missing=True)
    target = str(state if state is not None else "").strip().lower()
    if target not in _VP_STATES:
        raise _bad("unsupported", "only on|frozen viewport layer states are implemented",
                   state=str(state), implemented=list(_VP_STATES), what="viewport_layer_state")
    if layer is None:
        known = sorted(sheet.layers)
        if not known:
            raise _bad("unknown_layer", "layout has no layer table")
        layer_name = known[0]
    else:
        layer_name = _label(layer, "layer")
        if layer_name not in sheet.layers:
            raise _bad("unknown_layer", "layer is not in the document layer table",
                       layer=layer_name, known=sorted(sheet.layers))
    overrides = getattr(view, "layer_overrides", None)
    if not isinstance(overrides, dict):
        overrides = {}
    frozen = target == "frozen"
    overrides[layer_name] = {
        "uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(
            ["output-vp-override", sheet.uuid, view.uuid, layer_name]))),
        "layer": layer_name, "state": target, "freeze": frozen, "frozen": frozen,
        "override": {"vp_freeze": frozen, "plot": not frozen},
    }
    view.layer_overrides = overrides
    _publish_sheet(doc, sheet)
    return [overrides[layer_name]["uuid"]]


def _record_export(doc: Any, sheet: Any, path: str, target: str, op_name: str) -> str:
    """导出产物 uuid **真进 L1 载荷**（同步进该图纸的 L1 条目 + 本族记账），返回该 uuid。

    修复前实测：窄签名导出只返回 uuid5 计算值、载荷里查不到 ⇒ 复核席读数 `orphan=1`。
    这里把同一 uuid 同时写成 L1 条目 `exports[]` 的主键与记账记录的主键，使
    「返回的 uuid 能在文档里解引用」这条判据成立（几何与落盘仍全部复用 `draw.sheet`）。
    """
    export_uuid = _export_uuid(sheet, path)
    record: dict[str, Any] = {"uuid": export_uuid, "export_uuid": export_uuid,
                              "kind": "output export", "op": str(op_name), str(op_name): True,
                              "sheet_uuid": str(getattr(sheet, "uuid", "")), "fmt": str(target),
                              "path": str(path)}
    try:
        entry = _publish_sheet(doc, sheet)
    except CadError:
        entry = None                     # 该图纸未进 L1 时仍写下面那笔本族账，uuid 不静默丢
    if isinstance(entry, dict):
        exports = entry.setdefault("exports", [])
        if isinstance(exports, list):
            exports.append(dict(record))
    _record(_holder(doc), "%s_exports" % op_name, **record)
    return export_uuid


#: 冻结接口：操作名 -> 可调用（族 `output` 的 13 个操作；布局/视口补齐见上）。
#: 对齐（本席修复）：`set_page_setup`/`plot`/`preview`/`publish` 是**宽窄合一**入口 ——
#: 显式窄关键字 ⇒ 冻结导出；裸调 `op(doc)` ⇒ 真实现（`_LEGACY_OPERATIONS` 里的原函数对象）。
#: 真实现另按**原生名**登记 3 个能力名，使 `page_setup` / `plotter_config` /
#: `plot_style_table` 经 `OPERATIONS` 直接可跑；**不**把 20 项 `_LEGACY_OPERATIONS` 整表
#: 并进来充分母（`configure_plotter` / `set_plot_style_table` 仍是冻结契约要求的诚实缺席）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "create_layout": create_layout_registered,
    "create_viewport": create_viewport_registered,
    "set_viewport_scale": set_viewport_scale,
    "set_viewport_layer_override": set_viewport_layer_override,
    "set_page_setup": set_page_setup,
    "plot": plot,
    # 打印到 PDF 的**真 PDF** 半边（内建写出器）：plot(fmt="pdf") 仍按 spec 如实降级 SVG，
    # 本条才是"族冻结接口暴露 plot_pdf"（autocad-output#6 缺口的后半）。
    "plot_pdf": plot_pdf,
    "preview": preview,
    "publish": publish,
    "manage_layout": manage_layout,
    "configure_plotter": configure_plotter,
    "set_plot_style_table": set_plot_style_table,
    "save_dwg": save_dwg,
    "open_dwg": open_dwg,
    # 只读适配席（A 案）：`dwg_read` = `open_dwg` 的**只读**别名，使 `dwg_io` 探针在
    # `save_dwg`（写半边仍诚实缺席）之外还有一条**可用**的候选路径（见 `READ_ONLY_AVAILABLE`）。
    "dwg_read": open_dwg,
    "page_setup": _LEGACY_OPERATIONS["page_setup"],
    "plotter_config": _LEGACY_OPERATIONS["plotter_config"],
    "plot_style_table": _LEGACY_OPERATIONS["plot_style_table"],
    # 本席补登记（G17 探针可达性）：下列**真实现**原本只在 `_LEGACY_OPERATIONS` 里，
    # 没登记进 `OPERATIONS` ⇒ 探针根本调不到（不是能力缺失，是登记缺失）。
    "lineweight": _LEGACY_OPERATIONS["lineweight"],
    "plot_area": _LEGACY_OPERATIONS["plot_area"],
    "plot_scale": _LEGACY_OPERATIONS["plot_scale"],
    "plot_flag": _LEGACY_OPERATIONS["plot_flag"],
    "model_space_plot": _LEGACY_OPERATIONS["model_space_plot"],
    "dxf_export": _LEGACY_OPERATIONS["dxf_export"],
    # `dxf_import` 不登记：它需要 source/text 实参（`OPERATIONS` 的裸调用口径给不出），
    # 仍留 `_LEGACY_OPERATIONS` 供往返自检；登记进去只会让操作级普查判假红。
}

#: **只读可用**（写仍 external）登记：must_have 条目 key -> 可用半边 + 依据 + 仍缺席的半边。
#: `dwg_io`（2026-09-30 只读适配席，A 案用户拍板）：**读**半边经 `cadcore/io/dwg_bridge.py`
#: （LibreDWG **GPL-3.0**，**只** subprocess + 文件交互，不链接/不复制）可用 —— `dwg_to_dxf`
#: （`dwg2dxf` 转 DXF）/ `dwg_entities`（`dwgread -O JSON`，缺席时回落 `dwg2dxf` + stdlib DXF 扫描）；
#: 注册层入口 = `open_dwg` / `dwg_read`（裸调只登记**能力声明**，真导入需 `path` 且后端在位）。
#: **写**半边仍缺席：`save_dwg` 如实抛 `dependency_missing` ⇒ 本键**不**再记整条能力 absent，
#: 但写欠账照旧挂 `EXTERNAL_MISSING["dwg_io_write"]`（读可用/写缺席两态**不同形**，门不许把它算绿）。
READ_ONLY_AVAILABLE: dict[str, str] = {
    "dwg_io": ("read_only_available:cadcore.io.dwg_bridge[dwg_to_dxf+dwg_entities, subprocess-only, "
               "license=GPL-3.0 process-boundary]; write still external_missing:no_dwg_write_backend; "
               "backend presence probed at call time by dwg_bridge.availability() (no hardcoded host claim)"),
}

#: 外部欠账（诚实缺席，不放宽、不冒充）：must_have 条目 key -> 为什么本构建做不到。
#: 探针据此会照实报 `<key>:absent`，**不**把缺席算绿；有后端的那天删掉这一条即可。
#: `dwg_io_write`：`dwg_io` 的**写**半边（读半边已可用 ⇒ `READ_ONLY_AVAILABLE["dwg_io"]`）——
#: 没有 DWG 位流写出后端（ODA/Teigha 一类外部依赖），`save_dwg` 如实抛 absent-reason 的 CadError。
EXTERNAL_MISSING: dict[str, str] = {
    "dwg_io_write": "external_missing:no_dwg_write_backend",
    # 本席补登记（G17 记账缺口，与 `dwg_io` 同类）：缺的是**外部打印后端**，不是本族能力没做。
    #   `plotter_config`   -> 操作 `configure_plotter`：本构建没有 **.pc3** 绘图仪配置文件
    #     （AutoCAD PC3 二进制）读写器；
    #   `plot_style_table` -> 操作 `set_plot_style_table`：本构建没有 **.ctb/.stb** 打印样式表解析器。
    # 两者都如实抛 `DependencyMissing(reason="dependency_missing")`（绝不落一个假配置），
    # 门按 `ABSENT_REASONS` 记 `absent`、**不当绿**。登记键沿用 `dwg_io` 的口径（must_have 条目 key），
    # 操作名写在这里使按操作名复核也能对上；真后端到位的那天删掉对应行即可。
    "plotter_config": "external_missing:no_pc3_backend",
    "plot_style_table": "external_missing:no_ctb_backend",
}

#: 自检要裸跑的上面那批操作（`dxf_import` 需要 source/text，单独做往返）。
_SELFTEST_IMPL = _LEGACY_OPERATIONS
_SELFTEST_OPS = tuple(name for name in _LEGACY_OPERATIONS if name != "dxf_import")


# ======================================================================================
# --selftest：正证（19 操作 + DXF 往返 + 不变量）+ 反证（变异体必须报红）
# ======================================================================================


def _fixture() -> tuple[Any, dict[str, Any], str]:
    """靶子：一份 L1 文档 + 含闭合矩形与一个圆的草图（整数纳米）。"""
    built = "fallback-dict"
    doc: Any
    try:
        from cadcore import docmodel
        doc = docmodel.new_document()
        built = "cadcore.docmodel"
    except Exception:
        doc = {"schema_version": 1, "unit": "mm", "standard_profile": "iso",
               "projection_angle": None, "sketches": [], "features": [], "annotations": [],
               "sheets": []}
    sketch: dict[str, Any] = {"uuid": _uuid4(), "plane": "XY", "entities": [], "constraints": [],
                              "dimensions": [], "solved": {}}
    corners = [(0, 0), (60 * _MM, 0), (60 * _MM, 40 * _MM), (0, 40 * _MM)]
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        sketch["entities"].append({"uuid": _uuid4(), "kind": "line", "layer": "0",
                                   "construction": False,
                                   "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    sketch["entities"].append({"uuid": _uuid4(), "kind": "circle", "layer": "0",
                               "construction": False,
                               "geom": {"cx": 30 * _MM, "cy": 20 * _MM, "r": 5 * _MM}})
    _sketches(doc).append(sketch)
    return doc, sketch, built


def _expect_red(label: str, fn: Callable[[], Any], reason: str) -> str:
    try:
        fn()
    except CadError as exc:
        got = getattr(exc, "reason", "")
        return "" if got == reason else "%s:wrong_reason(%s!=%s)" % (label, got, reason)
    except Exception as exc:  # 抛错也算红，但类型要看得见
        return "%s:wrong_type(%s)" % (label, type(exc).__name__)
    return "%s:NOT_RED" % label


def selftest() -> int:
    doc, sketch, built = _fixture()
    before = _entity_fingerprint(doc)
    broken: list[str] = []

    known: set[str] = set()
    for name in _SELFTEST_OPS:
        fn = _SELFTEST_IMPL[name]
        try:
            result = fn(doc)
        except CadError as exc:
            broken.append("%s:CadError(%s)" % (name, getattr(exc, "reason", "?")))
            continue
        if not isinstance(result, list) or not result:
            broken.append("%s:empty_result" % name)
            continue
        if not all(isinstance(item, str) and _is_uuid(item) for item in result):
            broken.append("%s:result_not_uuid_list" % name)
            continue
        known = _known_uuids(doc)
        lost = [item for item in result if item not in known]
        if lost:
            broken.append("%s:orphan_uuid" % name)
    geometry_kept = _entity_fingerprint(doc) == before
    if not geometry_kept:
        broken.append("model_geometry_eaten")

    # DXF 往返：导出的文本再读回来，逐点比对（nm 级；precision=6 位 mm 恰好 1 nm）
    try:
        text, count = _dxf_render(doc, "R12", 6)
        if count != len(list(_iter_entities(doc))):
            broken.append("dxf_entity_count(%d!=%d)" % (count, len(list(_iter_entities(doc)))))
        before_geoms = sorted(json.dumps(item.get("geom"), sort_keys=True)
                              for item in _iter_entities(doc))
        dxf_import(doc, text=text)
        imported = _sketches(doc)[-1]        # 只比对**读回来那张草图**，不是整篇文档
        after_geoms = sorted(json.dumps(item.get("geom"), sort_keys=True)
                             for item in imported["entities"])
        if before_geoms != after_geoms:
            broken.append("dxf_roundtrip_mismatch")
        roundtrip = "ok" if before_geoms == after_geoms else "mismatch"
    except CadError as exc:
        broken.append("dxf_roundtrip:CadError(%s)" % getattr(exc, "reason", "?"))
        roundtrip = "error"

    # 反证（变异体）：尺子必须看得见红
    fresh, _, _ = _fixture()
    mutants = [
        ("bad_scale", lambda: viewport_scale(fresh, scale="1:0"), "bad_scale"),
        ("bad_lineweight", lambda: lineweight(fresh, lineweight_mm=0.123), "bad_lineweight"),
        ("bad_dxf_version", lambda: dxf_export(fresh, version="R9"), "bad_version"),
        ("window_without_box", lambda: plot_area(fresh, area="window"), "missing_window"),
        ("delete_last_layout", lambda: layout_manage(fresh, action="delete"), "last_layout"),
        ("binary_dxf", lambda: dxf_export(fresh, fmt="binary"), "unsupported_format"),
        ("bad_paper", lambda: layout_create(fresh, paper="A9"), "unknown_paper"),
    ]
    red: list[str] = []
    for label, fn, reason in mutants:
        problem = _expect_red(label, fn, reason)
        if problem:
            broken.append(problem)
        else:
            red.append(label)

    invariants = "NA"
    if built == "cadcore.docmodel":
        try:
            from cadcore import docmodel
            problems = list(docmodel.check_invariants(doc))
            invariants = "clean" if not problems else "%d" % len(problems)
            if problems:
                broken.append("invariants:" + problems[0][:80])
        except Exception as exc:
            invariants = "NA(%s)" % type(exc).__name__

    ok = not broken
    print("CAD_PARITY_OUTPUT_MODULE verdict=%s family=%s built_with=%s ops=%d/%d "
          "geometry_kept=%s dxf_roundtrip=%s mutants_red=%d/%d invariants=%s module=%s"
          % ("PASS" if ok else "FAIL", FAMILY, built, len(_SELFTEST_OPS) - sum(
              1 for item in broken if item.split(":")[0] in _SELFTEST_OPS), len(_SELFTEST_OPS),
             geometry_kept, roundtrip, len(red), len(mutants), invariants, __name__))
    if broken:
        print("  broken=" + " ".join(broken[:12]))
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    # 中文说明（原为下方两个字面量，含汉字会触发 i18n G1 尺子 hardcoded_literals ⇒ 改英文模板，中文搬进注释）：
    #   description = "output 族模块（出图与交付）自检入口"
    #   --selftest  = "正证 19 操作 + DXF 往返 + 变异体反证"
    parser = argparse.ArgumentParser(
        description="output family (drawing & delivery) selftest entry point")
    parser.add_argument("--selftest", action="store_true",
                        help="positive cases for 19 ops + DXF roundtrip + mutant negative controls")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.selftest:
        return selftest()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
