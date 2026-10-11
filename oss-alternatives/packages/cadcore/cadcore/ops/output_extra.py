#!/usr/bin/env python3
"""OUTPUT-EXTRA 族模块：打印样式映射表（CTB/STB 语义）+ SVG/PDF **文本**导出。

为什么另开一族（而不是改 `cadcore/ops/output.py`）
--------------------------------------------------
`output.py` 的 spec 面把 `set_plot_style_table` / `save_dwg` / `open_dwg` 显式降级成
`DependencyMissing(reason="dependency_missing")`（"本构建没有 .pc3/.ctb 解析器"），这是**诚实的缺席**
不是缺口。本模块**不推翻**那条账，只补三件**纯标准库就能真做**的能力：

1. **打印样式映射表**：`color -> {lineweight_mm, linetype, screening, plot, rgb}` 的映射账，
   支持 `.ctb` / `.stb` 容器名、单条写 / 批量写 / 按**最具体优先**解析 / 删除 / 生效账；
2. **SVG 文本导出**：真算纸张/窗口/比例，逐实体落到毫米坐标，写出可被浏览器直接打开的
   `<svg>`（不是"调用没抛异常"）；
3. **PDF 文本导出**：真写 PDF 1.4 文本容器（对象表 + 内容流 + **逐字节正确的 xref 偏移**），
   曲线用三次 Bezier 段逼近（圆弧/整圆/椭圆），落盘后能被 PDF 阅读器打开。

冻结接口（与 `cadcore/ops` 其余族同源）
--------------------------------------
    FAMILY = "output_extra"
    OPERATIONS: dict[str, Callable]        # 操作名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 第一个位置参 = L1 文档 / 裸草图，返回 uuid 列表
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

`sk_or_doc` 三种形态（本族全操作一致）：

* `docmodel.Document`（记账落在 `doc.extra["output_extra"]`，随 `to_payload()` 一起回写）；
* L1 文档映射（含 `sketches` 键；记账落在 `doc["output_extra"]`）；
* 裸草图映射（含 `entities` 键；该草图自身就是载体）。

口径（**不含糊**）
------------------
1. **长度一律整数纳米**：几何字段只接受 `int`（`bool` 与浮点一律拒），落库不动分毫；
   纸张/线宽/边距这类**图纸量**用 `*_mm`（float，量的是图纸不是模型）。
2. **没做的不写进返回值**：每个操作返回**本次真正新建/改动**的 uuid；"零改动"返回 `[]`；
   该失败就抛 `CadError`（`unknown_style_table` / `no_style_mapping` / `no_model_geometry` …），
   **绝不返回空列表冒充成功**。
3. **映射表真驱动导出**：SVG/PDF 的 `stroke-width` / 虚线 / 淡显 / `plot=False` 一律来自
   `style_map_*` 写下的映射（测试有对照：同几何换表 ⇒ 导出字节变）。表不存在 ⇒
   `unknown_style_table`；表里既无精确键也无 `*` ⇒ `no_style_mapping`（不猜、不静默用 0.25）。
4. **`fit` 与真比例都支持**：`scale="1:100"` ⇒ 图纸 1 单位 = 模型 100 单位（因子 = 1/100）；
   `scale="fit"` ⇒ 按模型外框铺满可打印区。越界不裁剪，落记录 `fits_on_paper` + `overflow_mm`。
5. **`.stb` / 命名颜色 / ACI 的口径**：`.stb` 只作为**容器名**被支持（键语义仍是 color→属性，
   不做命名样式表）；ACI 1..9 与 250..255 用标准值，**10..249 是近似色**（HSV 阶梯，非精确调色板，
   不冒充精确）。
6. **导出不改模型**：本族只读 `sketches[].entities[].geom`；自检有"几何指纹逐字节不变"断言。
7. **文本口径**：`render_svg_text` / `render_pdf_text` 返回**纯文本**（PDF 走 latin-1），
   两次调用逐字节相同（无时间戳、无随机）；PDF 的 `xref` 偏移由本模块**回读复验**后才落盘。
8. **导出文案一律 ASCII**：非 ASCII 标题抛 `CadError(reason="non_ascii_text")`
   —— 不落一个"编码看起来对、打开是乱码"的 PDF。

自检（文件自带，不依赖 pytest）：

    python3 packages/cadcore/cadcore/ops/output_extra.py
    => EXTRA_OPS verdict=PASS ops=10 checks=…/…

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径：非 docstring 的含汉字字符串字面量计
`hardcoded_literals`）。
"""

from __future__ import annotations

import hashlib
import math
import os
import sys
import uuid as _uuid
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Sequence
from typing import Any, Callable

import os as _os
import sys as _sys

_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:  # 直跑脚本与包内导入共用同一条绝对导入路径
    _sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError

__all__ = [
    "FAMILY", "OPERATIONS", "NM_PER_MM", "PAPER_SIZES_MM", "NAMED_COLORS", "LINETYPES",
    "style_table_create", "style_table_list", "style_map_set", "style_map_bulk",
    "style_map_lookup", "style_map_delete", "style_map_apply",
    "export_svg_text", "export_pdf_text", "export_manifest",
    "render_svg_text", "render_pdf_text", "module_facts", "selftest", "main",
]

#: 族 id（冻结）：本模块即 `output` 族的补全面，但族名独立，避免与 `output.py` 的同名操作打架。
FAMILY = "output_extra"

#: 记账命名空间：`doc.extra["output_extra"]`（纯 dict 载荷则落在顶层同名键）。
_NS = "output_extra"

NM_PER_MM = 1_000_000
PT_PER_MM = 72.0 / 25.4
MM_PER_PT = 25.4 / 72.0

#: 纸张（**短边, 长边**，毫米）；`orientation="landscape"` 时交换。
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0),
    "A1": (594.0, 841.0), "A0": (841.0, 1189.0), "Letter": (215.9, 279.4),
}

#: 命名色（ACI 基本色与之一一对应）。
NAMED_COLORS: dict[str, str] = {
    "red": "#FF0000", "yellow": "#FFFF00", "green": "#00FF00", "cyan": "#00FFFF",
    "blue": "#0000FF", "magenta": "#FF00FF", "white": "#FFFFFF", "black": "#000000",
}
_ACI_BASIC: dict[int, str] = {
    1: "#FF0000", 2: "#FFFF00", 3: "#00FF00", 4: "#00FFFF", 5: "#0000FF",
    6: "#FF00FF", 7: "#FFFFFF", 8: "#808080", 9: "#C0C0C0",
}
_ACI_GRAY: dict[int, str] = {
    250: "#333333", 251: "#505050", 252: "#696969", 253: "#828282",
    254: "#BEBEBE", 255: "#FFFFFF",
}

#: 线型 -> 相对线宽的 dash 段（`None` = 不打印，等价 `plot=False`）。
LINETYPES: dict[str, tuple[float, ...] | None] = {
    "continuous": (), "dashed": (2.0, 1.0), "dotted": (1.0, 1.5),
    "dashdot": (4.0, 1.0, 1.0, 1.0), "hidden": None,
}
_POINT_RADIUS_MM = 0.2
_DRAWABLE = ("line", "polyline", "circle", "arc", "ellipse", "point")


# ======================================================================================
# 小工具：错误 / 校验 / 载体
# ======================================================================================


def _err(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _uuid5(*parts: Any) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                          "|".join(["cadcore-output-extra", *[str(item) for item in parts]])))


def _key_like(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _err("bad_key", "%s must be a non-empty string" % what, what=what, value=repr(value))
    return value.strip()


def _label(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 255:
        raise _err("bad_label", "%s must be a 1..255 char string" % what, what=what)
    return value.strip()


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _err("bad_flag", "%s must be a bool" % what, what=what, value=repr(value))
    return value


def _lineweight(value: Any, what: str = "lineweight_mm") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _err("bad_lineweight", "%s must be a number of millimetres" % what, what=what)
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 10.0:
        raise _err("bad_lineweight", "%s must be finite and within 0..10 mm" % what,
                   what=what, value=number)
    return round(number, 6)


def _screening(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise _err("bad_screening", "screening must be an integer percentage in 0..100",
                   value=repr(value))
    return int(value)


def _linetype(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in LINETYPES:
        raise _err("bad_linetype", "linetype must be one of %s" % sorted(LINETYPES),
                   value=repr(value))
    return value.strip().lower()


def _rgb(value: Any, what: str = "rgb") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _err("bad_color", "%s must be '#RRGGBB' or None" % what, what=what)
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        raise _err("bad_color", "%s must be '#RRGGBB'" % what, what=what, value=text)
    try:
        int(text[1:], 16)
    except ValueError:
        raise _err("bad_color", "%s must be '#RRGGBB'" % what, what=what, value=text) from None
    return text.upper()


def _table_spec(value: Any) -> tuple[str, str, bool]:
    """表名 -> (名字, 格式, 是否单色)；后缀必须是 `.ctb` / `.stb`（不猜）。"""
    name = _label(value, "table")
    lowered = name.lower()
    if lowered.endswith(".ctb"):
        fmt = "ctb"
    elif lowered.endswith(".stb"):
        fmt = "stb"
    else:
        raise _err("bad_table", "style table name must end with .ctb or .stb", table=name)
    return name, fmt, "mono" in lowered


def _color_key(value: Any, what: str = "color") -> str:
    """颜色选择键 -> `kind:value`（`any:*` / `by_layer:by_layer` / `aci:N` / `named:x` / `true_color:#RRGGBB`）。"""
    if isinstance(value, bool) or value is None:
        raise _err("bad_color", "%s must be a colour selector" % what, what=what, value=repr(value))
    if isinstance(value, int):
        if not 1 <= value <= 255:
            raise _err("bad_color", "ACI index must be within 1..255", what=what, value=value)
        return "aci:%d" % value
    if isinstance(value, str):
        text = value.strip()
        lowered = text.lower()
        if lowered in ("*", "any", "all"):
            return "any:*"
        if lowered in ("by_layer", "bylayer", "by layer"):
            return "by_layer:by_layer"
        if text.startswith("#"):
            chosen = _rgb(text, what)
            assert chosen is not None
            return "true_color:%s" % chosen
        if lowered in NAMED_COLORS:
            return "named:%s" % lowered
    raise _err("bad_color", "%s is not a known colour selector (try '*', 'by_layer', 1..255, "
               "'#RRGGBB' or a named colour)" % what, what=what, value=repr(value))


def _aci_rgb(index: int) -> str:
    """ACI -> '#RRGGBB'：1..9 / 250..255 是标准值，**10..249 是近似色**（HSV 阶梯）。"""
    if index in _ACI_BASIC:
        return _ACI_BASIC[index]
    if index in _ACI_GRAY:
        return _ACI_GRAY[index]
    hue = float((index - 10) % 24) * 15.0
    value = (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1, 0.05)[(index - 10) // 24]
    chroma = value
    second = chroma * (1.0 - abs(((hue / 60.0) % 2.0) - 1.0))
    base = value - chroma
    sector = int(hue // 60.0) % 6
    trio = ((chroma, second, 0.0), (second, chroma, 0.0), (0.0, chroma, second),
            (0.0, second, chroma), (second, 0.0, chroma), (chroma, 0.0, second))[sector]
    return "#%02X%02X%02X" % tuple(int(round(255 * (component + base))) for component in trio)


def _holder(sk_or_doc: Any, *, create: bool = True) -> dict[str, Any]:
    """本族记账命名空间（`Document.extra["output_extra"]`；纯映射载荷则落在顶层同名键）。"""
    extra = getattr(sk_or_doc, "extra", None)
    container = extra if isinstance(extra, dict) else (
        sk_or_doc if isinstance(sk_or_doc, _Mapping) else None)
    if container is None:
        raise _err("bad_document", "output_extra ops need an L1 Document or payload mapping, got %s"
                   % type(sk_or_doc).__name__)
    if create:
        holder = container.setdefault(_NS, {})
        if not isinstance(holder, dict):
            raise _err("bad_document", "reserved key %r must hold a mapping" % _NS)
        return holder
    holder = container.get(_NS)
    return holder if isinstance(holder, dict) else {}


def _sketch_list(sk_or_doc: Any) -> list[Any]:
    if isinstance(sk_or_doc, _Mapping):
        sketches = sk_or_doc.get("sketches")
        if isinstance(sketches, list):
            return sketches
        if isinstance(sk_or_doc.get("entities"), list):
            return [sk_or_doc]
    sketches = getattr(sk_or_doc, "sketches", None)
    if isinstance(sketches, list):
        return sketches
    raise _err("bad_document", "document.sketches must be a list (or pass a bare sketch mapping)")


def _iter_entities(sk_or_doc: Any) -> list[Any]:
    out: list[Any] = []
    for sketch in _sketch_list(sk_or_doc):
        if isinstance(sketch, _Mapping):
            for entity in sketch.get("entities") or []:
                if isinstance(entity, _Mapping):
                    out.append(entity)
    return out


def _field(sk_or_doc: Any, name: str, default: Any = None) -> Any:
    value = getattr(sk_or_doc, name, None)
    if value is None and isinstance(sk_or_doc, _Mapping):
        value = sk_or_doc.get(name)
    return default if value is None else value


def _layer_colors(sk_or_doc: Any) -> dict[str, Any]:
    """图层名 -> 颜色选择键（`layers` 既可能是映射也可能是列表，两种都认）。

    `Document.extra["layers"]`（L1 未知顶层键）也认 —— 内存形态不便挂新属性时仍有唯一读法。
    """
    layers = _field(sk_or_doc, "layers", None)
    if layers is None:
        extra = getattr(sk_or_doc, "extra", None)
        if isinstance(extra, _Mapping):
            layers = extra.get("layers")
    out: dict[str, Any] = {}
    if isinstance(layers, _Mapping):
        for name, value in layers.items():
            if isinstance(value, _Mapping) and value.get("color") is not None:
                out[str(name)] = value["color"]
    elif isinstance(layers, _Sequence) and not isinstance(layers, (str, bytes)):
        for item in layers:
            if isinstance(item, _Mapping) and item.get("name") is not None:
                if item.get("color") is not None:
                    out[str(item["name"])] = item["color"]
    return out


def _entity_color_key(entity: _Mapping, layers: dict[str, Any]) -> str:
    explicit = entity.get("color")
    if explicit is not None:
        return _color_key(explicit, "entity colour")
    layer = entity.get("layer")
    if layer is not None and str(layer) in layers:
        return _color_key(layers[str(layer)], "layer colour")
    return "by_layer:by_layer"


# ======================================================================================
# 打印样式映射表
# ======================================================================================


def _tables(holder: _Mapping) -> list[Any]:
    found = holder.get("style_tables")
    return found if isinstance(found, list) else []


def _find_table(sk_or_doc: Any, table: Any) -> dict[str, Any] | None:
    name, _fmt, _mono = _table_spec(table)
    for record in _tables(_holder(sk_or_doc)):
        if isinstance(record, dict) and record.get("name") == name:
            return record
    return None


def _require_table(sk_or_doc: Any, table: Any) -> dict[str, Any]:
    record = _find_table(sk_or_doc, table)
    if record is None:
        name, _fmt, _mono = _table_spec(table)
        raise _err("unknown_style_table", "no such plot style table; create it first", table=name)
    return record


def _entries(record: dict[str, Any]) -> list[Any]:
    found = record.get("entries")
    if not isinstance(found, list):
        found = []
        record["entries"] = found
    return found


def _live_entries(record: dict[str, Any]) -> list[dict[str, Any]]:
    """在册（未删）映射条目 = 语义面；墓碑不进这里（见 `style_map_delete`）。"""
    return [entry for entry in _entries(record)
            if isinstance(entry, dict) and not entry.get("deleted")]


def _tombstones(record: dict[str, Any]) -> list[dict[str, Any]]:
    """本表的墓碑账（被删映射的历史）；uuid 与删除前同一条，故返回值仍可解引用。"""
    found = record.get("tombstones")
    if not isinstance(found, list):
        found = []
        record["tombstones"] = found
    return found


def _drop_tombstone(record: dict[str, Any], key: str) -> None:
    """同键复活/重建前清墓碑，避免同一 uuid 在载荷里出现两份（行为确定）。"""
    found = record.get("tombstones")
    if isinstance(found, list):
        record["tombstones"] = [item for item in found
                                if not (isinstance(item, dict) and item.get("key") == key)]


def _match(record: dict[str, Any], target_key: str) -> dict[str, Any] | None:
    """最具体优先：精确键 > `*`（通配只能兜底，不能盖过精确键）。"""
    fallback: dict[str, Any] | None = None
    for entry in _live_entries(record):
        if entry.get("key") == target_key:
            return entry
        if entry.get("key") == "any:*":
            fallback = entry
    return fallback


def _entry_payload(color: Any, lineweight_mm: Any, linetype: Any, screening: Any, plot: Any,
                   rgb: Any) -> dict[str, Any]:
    return {
        "key": _color_key(color),
        "lineweight_mm": _lineweight(lineweight_mm),
        "linetype": _linetype(linetype),
        "screening": _screening(screening),
        "plot": _flag(plot, "plot"),
        "rgb": _rgb(rgb),
    }


def _store_entry(record: dict[str, Any], payload: dict[str, Any]) -> tuple[str, bool]:
    """建立/更新一条映射，返回 (entry uuid, 是否真的改动)；同键墓碑先清掉（uuid5 不变）。"""
    _drop_tombstone(record, payload["key"])
    for entry in _entries(record):
        if isinstance(entry, dict) and entry.get("key") == payload["key"]:
            changed = any(entry.get(field) != payload[field] for field in payload)
            entry.update(payload)
            entry["deleted"] = False
            entry["status"] = "active"
            return str(entry["uuid"]), changed
    key = payload["key"]
    entry = {"uuid": _uuid5("style-entry", record["name"], key),
             "kind": "plot style mapping", "deleted": False, "status": "active", **payload}
    _entries(record).append(entry)
    return str(entry["uuid"]), True


def _style_view(record: dict[str, Any], target_key: str) -> dict[str, Any]:
    """颜色键 -> 生效属性（无精确键且无 `*` ⇒ `no_style_mapping`，不猜）。"""
    chosen = _match(record, target_key)
    if chosen is None:
        raise _err("no_style_mapping",
                   "no mapping matches this colour and the table has no '*' fallback",
                   table=record["name"], color=target_key)
    return {"key": chosen["key"], "lineweight_mm": chosen["lineweight_mm"],
            "linetype": chosen["linetype"], "screening": chosen["screening"],
            "plot": chosen["plot"], "rgb": chosen["rgb"], "source": "entry",
            "entry": chosen["uuid"]}


def _entry_rgb(record: _Mapping, style: _Mapping) -> str:
    """映射 -> 实际墨色：单色表一律黑；显式 rgb 优先；真彩/命名色自身；ACI 用调色板。"""
    if record.get("monochrome"):
        return "#000000"
    if style.get("rgb"):
        return str(style["rgb"])
    kind, _, value = str(style.get("key", "")).partition(":")
    if kind == "true_color":
        return value
    if kind == "named":
        return NAMED_COLORS.get(value, "#000000")
    if kind == "aci":
        return _aci_rgb(int(value))
    return "#000000"


def style_table_create(sk_or_doc: Any, *, name: Any = "monochrome.ctb",
                       description_key: str = "output.style.monochrome",
                       default_lineweight_mm: Any = 0.25,
                       default_linetype: Any = "continuous") -> list[str]:
    """建打印样式表（`.ctb` / `.stb` 容器名）。重名 ⇒ `CadError(reason="duplicate_table")`。"""
    table_name, fmt, mono = _table_spec(name)
    if _find_table(sk_or_doc, table_name) is not None:
        raise _err("duplicate_table", "plot style table already exists", table=table_name)
    record = {
        "uuid": _uuid5("style-table", table_name), "kind": "plot style table",
        "name": table_name, "format": fmt, "monochrome": mono,
        "description_key": _key_like(description_key, "description_key"),
        "default_lineweight_mm": _lineweight(default_lineweight_mm, "default_lineweight_mm"),
        "default_linetype": _linetype(default_linetype),
        "entries": [], "revision": 1,
    }
    _holder(sk_or_doc).setdefault("style_tables", []).append(record)
    return [record["uuid"]]


def style_table_list(sk_or_doc: Any, *, format: Any = None) -> list[str]:
    """列出样式表 uuid（只读）；`format` 可选 `"ctb"` / `"stb"` 过滤。无表 ⇒ `[]`。"""
    wanted = None if format is None else str(format).strip().lower().lstrip(".")
    if wanted is not None and wanted not in ("ctb", "stb"):
        raise _err("bad_table", "format filter must be 'ctb' or 'stb'", format=format)
    holder = _holder(sk_or_doc, create=False)
    return [str(record["uuid"]) for record in _tables(holder)
            if isinstance(record, dict) and (wanted is None or record.get("format") == wanted)]


def style_map_set(sk_or_doc: Any, *, table: Any = "monochrome.ctb", color: Any = "*",
                  lineweight_mm: Any = 0.25, linetype: Any = "continuous",
                  screening: Any = 100, plot: Any = True, rgb: Any = None) -> list[str]:
    """写一条颜色映射（存在则就地更新，键 = 颜色选择器）。返回**该映射条目的 uuid**。"""
    record = _require_table(sk_or_doc, table)
    payload = _entry_payload(color, lineweight_mm, linetype, screening, plot, rgb)
    entry_uuid, changed = _store_entry(record, payload)
    if changed:
        record["revision"] = int(record.get("revision", 1)) + 1
    return [entry_uuid]


def style_map_bulk(sk_or_doc: Any, *, table: Any = "monochrome.ctb",
                   mappings: Any = None) -> list[str]:
    """批量写映射（**先全量校验后写入**，坏条目 ⇒ 一条都不写）。返回逐条 uuid（同序）。"""
    record = _require_table(sk_or_doc, table)
    if isinstance(mappings, (str, bytes)) or not isinstance(mappings, _Sequence) or not mappings:
        raise _err("empty_mappings", "mappings must be a non-empty sequence", count=-1)
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(mappings):
        if not isinstance(item, _Mapping):
            raise _err("bad_mappings", "each mapping must be a mapping", index=index)
        unknown = sorted(set(item) - {"color", "lineweight_mm", "linetype", "screening", "plot",
                                      "rgb"})
        if unknown:
            raise _err("bad_mappings", "unknown mapping fields", index=index, unknown=unknown)
        prepared.append(_entry_payload(item.get("color", "*"), item.get("lineweight_mm", 0.25),
                                       item.get("linetype", "continuous"),
                                       item.get("screening", 100), item.get("plot", True),
                                       item.get("rgb")))
    uuids: list[str] = []
    for payload in prepared:
        entry_uuid, changed = _store_entry(record, payload)
        uuids.append(entry_uuid)
        if changed:
            record["revision"] = int(record.get("revision", 1)) + 1
    return uuids


def style_map_lookup(sk_or_doc: Any, *, table: Any = "monochrome.ctb", color: Any = "*") -> list[str]:
    """按**最具体优先**解析颜色 -> 命中的映射条目 uuid；无精确键且无 `*` ⇒ `no_style_mapping`。"""
    record = _require_table(sk_or_doc, table)
    style = _style_view(record, _color_key(color))
    return [str(style["entry"])]


def style_map_delete(sk_or_doc: Any, *, table: Any = "monochrome.ctb", color: Any = "*") -> list[str]:
    """删一条映射，返回被删条目 uuid；不存在 ⇒ `unknown_style_mapping`（不静默成功）。

    删除 = 条目**离开 `entries[]`**（门 `parity_output_gate.py` 的 `deep:style_delete` 要求它不再
    作为 `plot style mapping` 记录出现），同一 uuid 的**墓碑**移交本表 `tombstones[]`
    （`kind="plot style mapping tombstone"`、`deleted=True`、`status="deleted"`，保留被删属性）。
    形态照本仓先例 `output.py:_record_export`（同类 orphan 修复）：返回的 uuid 调用后仍可在载荷里
    解引用；`lookup` / `apply` / 计数只看在册条目 ⇒ 原条目语义已失效。
    行为确定：重复删同一键 ⇒ 照样 `unknown_style_mapping`；同键再 `style_map_set` ⇒ 清墓碑并按
    同一 uuid5 重建（uuid 不变，载荷里不留同 uuid 的两份）。
    """
    record = _require_table(sk_or_doc, table)
    target = _color_key(color)
    entries = _entries(record)
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.get("key") == target:
            entries.pop(index)
            tombstone = dict(entry)
            tombstone.update({"kind": "plot style mapping tombstone",
                              "deleted": True, "status": "deleted"})
            _tombstones(record).append(tombstone)
            record["revision"] = int(record.get("revision", 1)) + 1
            return [str(tombstone["uuid"])]
    raise _err("unknown_style_mapping", "no mapping with this colour key", table=record["name"],
               color=target)


def style_map_apply(sk_or_doc: Any, *, table: Any = "monochrome.ctb") -> list[str]:
    """对文档里**每个不同的颜色键**解出实际打印属性，落"生效账"并返回其 uuid（按首次出现序）。

    文档里一条实体都没有 ⇒ `no_model_geometry`（"没有几何"不等于"全部生效"）。
    """
    record = _require_table(sk_or_doc, table)
    entities = _iter_entities(sk_or_doc)
    if not entities:
        raise _err("no_model_geometry", "document has no sketch entities to resolve")
    layers = _layer_colors(sk_or_doc)
    bucket = _holder(sk_or_doc).setdefault("effective_styles", [])
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entity in entities:
        key = _entity_color_key(entity, layers)
        style = _style_view(record, key)
        if key not in seen:
            seen[key] = {
                "uuid": _uuid5("effective", record["name"], key), "kind": "effective plot style",
                "table": record["name"], "color": key, "source": style["source"],
                "entry": style["entry"], "lineweight_mm": style["lineweight_mm"],
                "linetype": style["linetype"], "screening": style["screening"],
                "plot": style["plot"], "rgb": _entry_rgb(record, style), "entity_count": 0,
            }
            order.append(key)
        seen[key]["entity_count"] = int(seen[key]["entity_count"]) + 1
    uuids: list[str] = []
    for key in order:
        fresh = seen[key]
        for index, existing in enumerate(bucket):
            if isinstance(existing, dict) and existing.get("uuid") == fresh["uuid"]:
                bucket[index] = fresh
                break
        else:
            bucket.append(fresh)
        uuids.append(str(fresh["uuid"]))
    return uuids


# ======================================================================================
# 几何：模型纳米 -> 图纸毫米
# ======================================================================================


def _paper_mm(paper: Any, orientation: Any) -> tuple[str, float, float]:
    if not isinstance(paper, str) or paper.strip().upper() not in PAPER_SIZES_MM:
        raise _err("bad_paper", "paper must be one of %s" % sorted(PAPER_SIZES_MM),
                   paper=repr(paper))
    name = paper.strip().upper()
    orient = str(orientation).strip().lower()
    if orient not in ("landscape", "portrait"):
        raise _err("bad_orientation", "orientation must be 'landscape' or 'portrait'",
                   orientation=repr(orientation))
    short, long = PAPER_SIZES_MM[name]
    if orient == "landscape":
        return name, long, short
    return name, short, long


def _scale_factor(scale: Any, box: tuple[float, float, float, float],
                  paper_w: float, paper_h: float, margin: float) -> tuple[float, str]:
    minx, miny, maxx, maxy = box
    if isinstance(scale, str):
        text = scale.strip().lower()
        if text == "fit":
            avail_w = max(paper_w - 2 * margin, 1e-6)
            avail_h = max(paper_h - 2 * margin, 1e-6)
            width, height = maxx - minx, maxy - miny
            candidates = []
            if width > 1e-9:
                candidates.append(avail_w / width)
            if height > 1e-9:
                candidates.append(avail_h / height)
            factor = min(candidates) if candidates else 1.0
            if not math.isfinite(factor) or factor <= 0.0:
                raise _err("bad_scale", "fit produced a non-positive factor", scale=scale)
            return factor, "fit"
        if ":" in text:
            num_text, _, den_text = text.partition(":")
            try:
                num, den = float(num_text), float(den_text)
            except ValueError:
                raise _err("bad_scale", "scale must look like 'num:den' or 'fit'",
                           scale=repr(scale)) from None
            if not (math.isfinite(num) and math.isfinite(den)) or num <= 0.0 or den <= 0.0:
                raise _err("bad_scale", "scale terms must be positive and finite",
                           scale=repr(scale))
            return num / den, "%s:%s" % (num_text.strip(), den_text.strip())
    raise _err("bad_scale", "scale must be 'num:den' or 'fit'", scale=repr(scale))


def _nm_to_mm(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _err("bad_length", "geometry lengths must be integer nanometres", value=repr(value))
    return value / float(NM_PER_MM)


def _entity_points(entity: _Mapping, kind: str) -> list[tuple[float, float]]:
    geom = entity.get("geom") or {}
    if kind == "line":
        return [(_nm_to_mm(geom["x1"]), _nm_to_mm(geom["y1"])),
                (_nm_to_mm(geom["x2"]), _nm_to_mm(geom["y2"]))]
    if kind == "polyline":
        points = geom.get("points")
        if not isinstance(points, _Sequence) or isinstance(points, (str, bytes)) or len(points) < 2:
            raise _err("bad_geometry", "polyline needs at least two points",
                       entity=str(entity.get("uuid")))
        return [(_nm_to_mm(pair[0]), _nm_to_mm(pair[1])) for pair in points]
    if kind == "point":
        return [(_nm_to_mm(geom["x"]), _nm_to_mm(geom["y"]))]
    if kind in ("circle", "arc"):
        cx, cy, radius = _nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"]), _nm_to_mm(geom["r"])
        if kind == "circle":
            return [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        start, end = float(geom.get("start_deg", 0.0)), float(geom.get("end_deg", 360.0))
        span = (end - start) % 360.0 or 360.0
        return [(cx + radius * math.cos(math.radians(start + span * index / 32.0)),
                 cy + radius * math.sin(math.radians(start + span * index / 32.0)))
                for index in range(33)]
    if kind == "ellipse":
        cx, cy = _nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"])
        rx, ry = _nm_to_mm(geom["rx"]), _nm_to_mm(geom["ry"])
        rot = math.radians(float(geom.get("rotation_deg", 0.0)))
        points = []
        for index in range(37):
            angle = 2.0 * math.pi * index / 36.0
            x, y = rx * math.cos(angle), ry * math.sin(angle)
            points.append((cx + x * math.cos(rot) - y * math.sin(rot),
                           cy + x * math.sin(rot) + y * math.cos(rot)))
        return points
    raise _err("unsupported_kind", "unsupported entity kind", kind=str(kind))


def _model_view(sk_or_doc: Any, style_table: Any) -> dict[str, Any]:
    """把文档读成"可画清单 + 外框（模型毫米）"，样式由映射表解析；跳过项**逐条记账**。"""
    record = _require_table(sk_or_doc, style_table)
    entities = _iter_entities(sk_or_doc)
    if not entities:
        raise _err("no_model_geometry", "document has no sketch entities to export")
    layers = _layer_colors(sk_or_doc)
    drawable: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for entity in entities:
        kind = str(entity.get("kind", ""))
        if kind not in _DRAWABLE:
            skipped[kind or "unknown"] = skipped.get(kind or "unknown", 0) + 1
            continue
        style = _style_view(record, _entity_color_key(entity, layers))
        if not style["plot"] or LINETYPES[style["linetype"]] is None:
            skipped["not_plotted"] = skipped.get("not_plotted", 0) + 1
            continue
        drawable.append({"uuid": str(entity.get("uuid")), "kind": kind,
                         "geom": dict(entity["geom"]), "style": style,
                         "points": _entity_points(entity, kind)})
    if not drawable:
        raise _err("no_model_geometry", "every entity is either unsupported or not plotted",
                   skipped_kinds=dict(sorted(skipped.items())))
    xs = [point[0] for item in drawable for point in item["points"]]
    ys = [point[1] for item in drawable for point in item["points"]]
    return {"table": record, "drawable": drawable, "skipped": dict(sorted(skipped.items())),
            "box": (min(xs), min(ys), max(xs), max(ys))}


def _num(value: float) -> str:
    text = "%.4f" % (0.0 if abs(value) < 5e-9 else value)
    text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _svg_stroke(record: _Mapping, style: _Mapping) -> str:
    attrs = ['stroke="%s"' % _entry_rgb(record, style),
             'stroke-width="%s"' % _num(float(style["lineweight_mm"]))]
    if int(style["screening"]) < 100:
        attrs.append('stroke-opacity="%s"' % _num(int(style["screening"]) / 100.0))
    pattern = LINETYPES[str(style["linetype"])] or ()
    if pattern:
        weight = float(style["lineweight_mm"]) or 0.1
        attrs.append('stroke-dasharray="%s"'
                     % ",".join(_num(weight * factor) for factor in pattern))
    return " ".join(attrs)


def _svg_geometry(item: _Mapping, mapper: Callable[[float, float], tuple[float, float]],
                  paper_h: float) -> str:
    kind = str(item["kind"])
    geom = item["geom"]

    def to_svg(x_mm: float, y_mm: float) -> tuple[float, float]:
        px, py = mapper(x_mm, y_mm)
        return px, paper_h - py

    if kind == "line":
        (x1, y1), (x2, y2) = (to_svg(_nm_to_mm(geom["x1"]), _nm_to_mm(geom["y1"])),
                              to_svg(_nm_to_mm(geom["x2"]), _nm_to_mm(geom["y2"])))
        return '<line x1="%s" y1="%s" x2="%s" y2="%s"' % (_num(x1), _num(y1), _num(x2), _num(y2))
    if kind == "polyline":
        points = [to_svg(_nm_to_mm(pair[0]), _nm_to_mm(pair[1])) for pair in geom["points"]]
        text = " ".join("%s,%s" % (_num(px), _num(py)) for px, py in points)
        return ('<polygon points="%s"' if geom.get("closed") else '<polyline points="%s"') % text
    if kind == "circle":
        cx, cy = to_svg(_nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"]))
        return '<circle cx="%s" cy="%s" r="%s"' % (_num(cx), _num(cy), _num(item["radius_mm"]))
    if kind == "arc":
        start = float(geom.get("start_deg", 0.0))
        end = float(geom.get("end_deg", 360.0))
        span = (end - start) % 360.0 or 360.0
        cx_mm, cy_mm = _nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"])
        sx, sy = to_svg(cx_mm + item["radius_mm"] * math.cos(math.radians(start)),
                        cy_mm + item["radius_mm"] * math.sin(math.radians(start)))
        ex, ey = to_svg(cx_mm + item["radius_mm"] * math.cos(math.radians(start + span)),
                        cy_mm + item["radius_mm"] * math.sin(math.radians(start + span)))
        sweep = 0 if end >= start else 1  # 模型逆时针 = SVG(y 向下)顺时针 = sweep 0
        large = 1 if span > 180.0 else 0
        return ('<path d="M %s %s A %s %s 0 %d %d %s %s"'
                % (_num(sx), _num(sy), _num(item["radius_mm"]), _num(item["radius_mm"]), large,
                   sweep, _num(ex), _num(ey)))
    if kind == "ellipse":
        cx, cy = to_svg(_nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"]))
        rot = -float(geom.get("rotation_deg", 0.0))  # 模型逆时针 -> SVG rotate 取负
        return ('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" transform="rotate(%s %s %s)"'
                % (_num(cx), _num(cy), _num(_nm_to_mm(geom["rx"])), _num(_nm_to_mm(geom["ry"])),
                   _num(rot), _num(cx), _num(cy)))
    cx, cy = to_svg(_nm_to_mm(geom["x"]), _nm_to_mm(geom["y"]))
    return '<circle cx="%s" cy="%s" r="%s"' % (_num(cx), _num(cy), _num(_POINT_RADIUS_MM))


def _ascii_text(value: Any, what: str) -> str:
    text = str(value)
    if any(ord(char) > 126 for char in text):
        raise _err("non_ascii_text", "%s must be ASCII (no svg/pdf encoding guessing)" % what,
                   what=what)
    return text


def _layout_inputs(sk_or_doc: Any, paper: Any, orientation: Any, scale: Any, style_table: Any,
                   margin_mm: Any, title: Any) -> tuple[str, float, float, float, str, str, dict]:
    paper_name, paper_w, paper_h = _paper_mm(paper, orientation)
    if isinstance(margin_mm, bool) or not isinstance(margin_mm, (int, float)):
        raise _err("bad_margin", "margin_mm must be a number", value=repr(margin_mm))
    margin = float(margin_mm)
    if not math.isfinite(margin) or margin < 0.0 or 2 * margin >= min(paper_w, paper_h):
        raise _err("bad_margin", "margin_mm leaves no printable area", value=margin)
    caption = _ascii_text(title, "title")
    view = _model_view(sk_or_doc, style_table)
    factor, scale_text = _scale_factor(scale, view["box"], paper_w, paper_h, margin)
    return paper_name, paper_w, paper_h, margin, caption, scale_text, \
        {"view": view, "factor": factor}


def _svg_document(sk_or_doc: Any, *, paper: Any, orientation: Any, scale: Any,
                  style_table: Any, margin_mm: Any, title: Any) -> tuple[str, dict[str, Any]]:
    paper_name, paper_w, paper_h, margin, caption, scale_text, state = _layout_inputs(
        sk_or_doc, paper, orientation, scale, style_table, margin_mm, title)
    view, factor = state["view"], state["factor"]
    minx, miny, maxx, maxy = view["box"]

    def mapper(x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm - minx) * factor + margin, (y_mm - miny) * factor + margin

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
             'width="%smm" height="%smm" viewBox="0 0 %s %s">'
             % (_num(paper_w), _num(paper_h), _num(paper_w), _num(paper_h)),
             "<title>%s</title>" % (caption or "cadcore output_extra svg export"),
             "<desc>style_table=%s scale=%s entities=%d</desc>"
             % (view["table"]["name"], scale_text, len(view["drawable"])),
             '<g id="entities" fill="none" stroke-linecap="round" stroke-linejoin="round">']
    for item in view["drawable"]:
        if item["kind"] in ("circle", "arc"):
            item["radius_mm"] = _nm_to_mm(item["geom"]["r"]) * factor
        lines.append("  %s %s />"
                     % (_svg_geometry(item, mapper, paper_h),
                        _svg_stroke(view["table"], item["style"])))
    lines.append("</g>")
    if caption:
        lines.append('<text x="%s" y="%s" font-family="sans-serif" font-size="3" '
                     'fill="#000000">%s</text>'
                     % (_num(margin), _num(margin / 2.0 + 0.5), caption))
    lines.append("</svg>")
    corners = [mapper(minx, miny), mapper(maxx, miny), mapper(maxx, maxy), mapper(minx, maxy)]
    overflow = max([0.0] + [-px for px, _py in corners] + [px - paper_w for px, _py in corners]
                   + [-py for _px, py in corners] + [py - paper_h for _px, py in corners])
    facts = {"paper": paper_name, "paper_w_mm": paper_w, "paper_h_mm": paper_h,
             "orientation": str(orientation).strip().lower(), "scale": scale_text,
             "factor": factor, "margin_mm": margin, "style_table": view["table"]["name"],
             "entity_count": len(view["drawable"]), "skipped_kinds": view["skipped"],
             "fits_on_paper": overflow <= 1e-9, "overflow_mm": round(overflow, 6),
             "viewBox": "0 0 %s %s" % (_num(paper_w), _num(paper_h))}
    return "\n".join(lines) + "\n", facts


def render_svg_text(sk_or_doc: Any, *, paper: Any = "A4", orientation: Any = "landscape",
                    scale: Any = "1:1", style_table: Any = "monochrome.ctb",
                    margin_mm: Any = 10.0, title: Any = "") -> str:
    """只算不落盘：返回 SVG 文本（**纯函数**，两次调用逐字节相同）。"""
    return _svg_document(sk_or_doc, paper=paper, orientation=orientation, scale=scale,
                         style_table=style_table, margin_mm=margin_mm, title=title)[0]


def export_svg_text(sk_or_doc: Any, *, out_path: Any = "", paper: Any = "A4",
                    orientation: Any = "landscape", scale: Any = "1:1",
                    style_table: Any = "monochrome.ctb", margin_mm: Any = 10.0,
                    title: Any = "") -> list[str]:
    """SVG 文本导出：真算坐标 + 真落盘（`out_path` 为空则只算不写，记录 `written=False`）。"""
    text, facts = _svg_document(sk_or_doc, paper=paper, orientation=orientation, scale=scale,
                                style_table=style_table, margin_mm=margin_mm, title=title)
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    record = {"uuid": _uuid5("export-svg", facts["style_table"], facts["scale"], str(out_path),
                             digest),
              "kind": "svg export", "format": "svg", "path": str(out_path), "written": False,
              "bytes": len(payload), "sha256": digest, "text_sha256": digest, **facts}
    if out_path:
        target = os.path.abspath(str(out_path))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(payload)
        with open(target, "rb") as handle:
            on_disk = handle.read()
        record["written"] = True
        record["path"] = target
        record["bytes"] = len(on_disk)
        record["sha256"] = hashlib.sha256(on_disk).hexdigest()
        record["bytes_match"] = on_disk == payload
    _holder(sk_or_doc).setdefault("exports", []).append(record)
    return [record["uuid"]]


# ======================================================================================
# PDF 1.4 文本容器
# ======================================================================================


def _bezier_chain(cx: float, cy: float, rx: float, ry: float, rot_deg: float,
                  start_deg: float, span_deg: float) -> list[tuple[float, ...]]:
    """椭圆/圆/圆弧 -> 三次 Bezier 段链（每段 ≤ 90 度），返回 (c1x,c1y,c2x,c2y,ex,ey)（模型毫米）。"""
    span = span_deg % 360.0 or 360.0
    segments = max(1, int(math.ceil(abs(span) / 90.0)))
    step = span / segments
    alpha = 4.0 / 3.0 * math.tan(math.radians(step / 4.0))
    rot = math.radians(rot_deg)

    def place(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        return (cx + x * math.cos(rot) - y * math.sin(rot),
                cy + x * math.sin(rot) + y * math.cos(rot))

    out: list[tuple[float, ...]] = []
    for index in range(segments):
        angle0 = math.radians(start_deg + index * step)
        angle1 = math.radians(start_deg + (index + 1) * step)
        p0 = (rx * math.cos(angle0), ry * math.sin(angle0))
        p1 = (rx * math.cos(angle1), ry * math.sin(angle1))
        t0 = (-rx * math.sin(angle0), ry * math.cos(angle0))
        t1 = (-rx * math.sin(angle1), ry * math.cos(angle1))
        c1 = place((p0[0] + alpha * t0[0], p0[1] + alpha * t0[1]))
        c2 = place((p1[0] - alpha * t1[0], p1[1] - alpha * t1[1]))
        end = place(p1)
        out.append((c1[0], c1[1], c2[0], c2[1], end[0], end[1]))
    return out


def _pdf_bytes(objects: list[bytes]) -> bytes:
    """对象表 + 逐字节正确的 xref 偏移（偏移在生成时量，不靠事后猜）。"""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_offset))
    return bytes(out)


def _verify_xref(payload: bytes) -> tuple[bool, int]:
    """回读复验：`startxref` 指向 `xref`，声明条数与每个表项偏移都指向 `N 0 obj`。"""
    marker = payload.rfind(b"startxref")
    if marker < 0:
        return False, -1
    try:
        offset = int(payload[marker + len(b"startxref"):].split()[0])
    except (IndexError, ValueError):
        return False, -1
    section = payload[offset:]
    if not section.startswith(b"xref"):
        return False, offset
    lines = section.split(b"\n")
    try:
        count = int(lines[1].split()[1])
    except (IndexError, ValueError):
        return False, offset
    entries = lines[2:2 + count]
    if len(entries) != count or count < 2:
        return False, offset
    for number, line in enumerate(entries[1:], 1):
        parts = line.split()
        if len(parts) != 3 or parts[2] != b"n":
            return False, offset
        if not payload[int(parts[0]):].startswith(b"%d 0 obj" % number):
            return False, offset
    return True, offset


def _pdf_paint(item: _Mapping, mapper: Callable[[float, float], tuple[float, float]],
               monochrome: bool) -> str:
    """一条实体的 PDF 内容流算子（`m`/`l`/`c`/`S`），样式来自映射表。"""
    style = item["style"]
    kind = str(item["kind"])
    geom = item["geom"]
    weight_mm = float(style["lineweight_mm"])
    ops = ["q", "1 J", "1 j", "%s w" % _num(weight_mm * PT_PER_MM)]
    screening = int(style["screening"]) / 100.0
    if monochrome:
        ops.append("%s g" % _num(screening))
    else:
        rgb = _entry_rgb({"monochrome": False}, style)
        ops.append("%s %s %s rg" % (_num(int(rgb[1:3], 16) / 255.0 * screening),
                                    _num(int(rgb[3:5], 16) / 255.0 * screening),
                                    _num(int(rgb[5:7], 16) / 255.0 * screening)))
    pattern = LINETYPES[str(style["linetype"])] or ()
    if pattern:
        scale = max(weight_mm, 0.1)
        ops.append("[%s] 0 d" % " ".join(_num(scale * value) for value in pattern))

    def curve(start: tuple[float, float], chain: list[tuple[float, ...]]) -> None:
        start_x, start_y = mapper(*start)
        ops.append("%s %s m" % (_num(start_x), _num(start_y)))
        for segment in chain:
            points = [mapper(segment[0], segment[1]), mapper(segment[2], segment[3]),
                      mapper(segment[4], segment[5])]
            ops.append("c " + " ".join("%s %s" % (_num(px), _num(py)) for px, py in points))
        ops.append("S")

    if kind == "line":
        x1, y1 = mapper(_nm_to_mm(geom["x1"]), _nm_to_mm(geom["y1"]))
        x2, y2 = mapper(_nm_to_mm(geom["x2"]), _nm_to_mm(geom["y2"]))
        ops += ["%s %s m" % (_num(x1), _num(y1)), "%s %s l" % (_num(x2), _num(y2)), "S"]
    elif kind == "polyline":
        points = [mapper(_nm_to_mm(pair[0]), _nm_to_mm(pair[1])) for pair in geom["points"]]
        ops.append("%s %s m" % (_num(points[0][0]), _num(points[0][1])))
        ops += ["%s %s l" % (_num(px), _num(py)) for px, py in points[1:]]
        ops.append("s" if geom.get("closed") else "S")
    elif kind in ("circle", "arc"):
        cx_mm, cy_mm, radius = _nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"]), _nm_to_mm(geom["r"])
        start = float(geom.get("start_deg", 0.0)) if kind == "arc" else 0.0
        span = ((float(geom.get("end_deg", 360.0)) - start) % 360.0 or 360.0) \
            if kind == "arc" else 360.0
        curve((cx_mm + radius * math.cos(math.radians(start)),
               cy_mm + radius * math.sin(math.radians(start))),
              _bezier_chain(cx_mm, cy_mm, radius, radius, 0.0, start, span))
    elif kind == "ellipse":
        cx_mm, cy_mm = _nm_to_mm(geom["cx"]), _nm_to_mm(geom["cy"])
        rx_mm, ry_mm = _nm_to_mm(geom["rx"]), _nm_to_mm(geom["ry"])
        rot = float(geom.get("rotation_deg", 0.0))
        curve((cx_mm + rx_mm * math.cos(math.radians(rot)),
               cy_mm + rx_mm * math.sin(math.radians(rot))),
              _bezier_chain(cx_mm, cy_mm, rx_mm, ry_mm, rot, 0.0, 360.0))
    else:  # point：落一个 0.4mm 的小方块
        px, py = mapper(_nm_to_mm(geom["x"]), _nm_to_mm(geom["y"]))
        ops += ["%s %s %s %s re" % (_num(px - _POINT_RADIUS_MM), _num(py - _POINT_RADIUS_MM),
                                    _num(2 * _POINT_RADIUS_MM), _num(2 * _POINT_RADIUS_MM)), "f"]
    ops.append("Q")
    return "\n".join(ops)


def _pdf_document(sk_or_doc: Any, *, paper: Any, orientation: Any, scale: Any,
                  style_table: Any, margin_mm: Any, title: Any) -> tuple[bytes, dict[str, Any]]:
    paper_name, paper_w, paper_h, margin, caption, scale_text, state = _layout_inputs(
        sk_or_doc, paper, orientation, scale, style_table, margin_mm, title)
    view, factor = state["view"], state["factor"]
    minx, miny, maxx, maxy = view["box"]

    def mapper(x_mm: float, y_mm: float) -> tuple[float, float]:
        return ((x_mm - minx) * factor + margin) * PT_PER_MM, \
               ((y_mm - miny) * factor + margin) * PT_PER_MM

    monochrome = bool(view["table"].get("monochrome"))
    body = ["q", "1 J", "1 j"]
    for item in view["drawable"]:
        body.append(_pdf_paint(item, mapper, monochrome))
    if caption:
        body += ["BT", "0 g", "/F1 9 Tf",
                 "%s %s Td" % (_num(margin * PT_PER_MM),
                               _num((paper_h - margin / 2.0) * PT_PER_MM)),
                 "(%s) Tj" % caption.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"),
                 "ET"]
    body.append("Q")
    stream = ("\n".join(body) + "\n").encode("latin-1")
    contents = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"
         % (_num(paper_w * PT_PER_MM).encode("ascii"), _num(paper_h * PT_PER_MM).encode("ascii"))),
        contents,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = _pdf_bytes(objects)
    overflow = max(0.0, margin + (maxx - minx) * factor - paper_w,
                   margin + (maxy - miny) * factor - paper_h)
    facts = {"paper": paper_name, "paper_w_mm": paper_w, "paper_h_mm": paper_h,
             "orientation": str(orientation).strip().lower(), "scale": scale_text,
             "factor": factor, "margin_mm": margin, "style_table": view["table"]["name"],
             "entity_count": len(view["drawable"]), "skipped_kinds": view["skipped"],
             "fits_on_paper": overflow <= 1e-9, "overflow_mm": round(overflow, 6),
             "page_count": 1, "pdf_version": "1.4", "monochrome": monochrome}
    return payload, facts


def render_pdf_text(sk_or_doc: Any, *, paper: Any = "A4", orientation: Any = "landscape",
                    scale: Any = "1:1", style_table: Any = "monochrome.ctb",
                    margin_mm: Any = 10.0, title: Any = "") -> str:
    """只算不落盘：返回 PDF 文本（latin-1 解码；含二进制注释字节，两次调用逐字节相同）。"""
    payload, _facts = _pdf_document(sk_or_doc, paper=paper, orientation=orientation, scale=scale,
                                    style_table=style_table, margin_mm=margin_mm, title=title)
    return payload.decode("latin-1")


def export_pdf_text(sk_or_doc: Any, *, out_path: Any = "", paper: Any = "A4",
                    orientation: Any = "landscape", scale: Any = "1:1",
                    style_table: Any = "monochrome.ctb", margin_mm: Any = 10.0,
                    title: Any = "") -> list[str]:
    """PDF 文本导出：真写 PDF 1.4（对象表 / 内容流 / xref），落盘后回读复验 xref 偏移。"""
    payload, facts = _pdf_document(sk_or_doc, paper=paper, orientation=orientation, scale=scale,
                                   style_table=style_table, margin_mm=margin_mm, title=title)
    xref_ok, xref_offset = _verify_xref(payload)
    if not xref_ok:
        raise _err("bad_pdf", "generated pdf failed its own xref verification",
                   xref_offset=xref_offset)
    digest = hashlib.sha256(payload).hexdigest()
    record = {"uuid": _uuid5("export-pdf", facts["style_table"], facts["scale"], str(out_path),
                             digest),
              "kind": "pdf export", "format": "pdf", "path": str(out_path), "written": False,
              "bytes": len(payload), "sha256": digest, "text_sha256": digest,
              "xref_verified": True, "xref_offset": xref_offset, **facts}
    if out_path:
        target = os.path.abspath(str(out_path))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(payload)
        with open(target, "rb") as handle:
            on_disk = handle.read()
        record["written"] = True
        record["path"] = target
        record["bytes"] = len(on_disk)
        record["sha256"] = hashlib.sha256(on_disk).hexdigest()
        record["bytes_match"] = on_disk == payload
        record["xref_verified"] = _verify_xref(on_disk)[0]
    _holder(sk_or_doc).setdefault("exports", []).append(record)
    return [record["uuid"]]


def export_manifest(sk_or_doc: Any, *, format: Any = None) -> list[str]:
    """列出已记账的导出 uuid（只读）；`format` 可选 `"svg"` / `"pdf"`。无导出 ⇒ `[]`。"""
    wanted = None if format is None else str(format).strip().lower().lstrip(".")
    if wanted is not None and wanted not in ("svg", "pdf"):
        raise _err("bad_format", "format filter must be 'svg' or 'pdf'", format=format)
    holder = _holder(sk_or_doc, create=False)
    found = holder.get("exports")
    if not isinstance(found, list):
        return []
    return [str(record["uuid"]) for record in found
            if isinstance(record, dict) and (wanted is None or record.get("format") == wanted)]


def module_facts(sk_or_doc: Any) -> dict[str, Any]:
    """机读体检：族 id / 操作清单 / 各类记账条数（供测试与自检断言，不写盘、不建命名空间）。"""
    holder = _holder(sk_or_doc, create=False)
    tables = _tables(holder)
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "ops_count": len(OPERATIONS),
        "style_tables": len(tables),
        "style_mappings": sum(len(_live_entries(record)) for record in tables
                              if isinstance(record, dict)),
        "effective_styles": len(holder.get("effective_styles") or []),
        "exports": len(holder.get("exports") or []),
    }


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "style_table_create": style_table_create,
    "style_table_list": style_table_list,
    "style_map_set": style_map_set,
    "style_map_bulk": style_map_bulk,
    "style_map_lookup": style_map_lookup,
    "style_map_delete": style_map_delete,
    "style_map_apply": style_map_apply,
    "export_svg_text": export_svg_text,
    "export_pdf_text": export_pdf_text,
    "export_manifest": export_manifest,
}


# ======================================================================================
# 自检：正证（10 操作 + 落盘字节 + xref 复验）+ 反证（坏入参必须报红）
# ======================================================================================


def _payload(sk_or_doc: Any) -> dict[str, Any]:
    if hasattr(sk_or_doc, "to_payload"):
        return dict(sk_or_doc.to_payload())
    return dict(sk_or_doc) if isinstance(sk_or_doc, _Mapping) else {}


def _expect_red(fn: Callable[[], Any], reason: str) -> str:
    try:
        fn()
    except CadError as exc:
        return "ok" if exc.reason == reason else "reason=%s" % exc.reason
    return "no error"


def _raise_no_mapping() -> Any:
    """反证夹具：只写 `red` 不给 `*` 兜底，解析别的颜色 ⇒ 必须 `no_style_mapping`。"""
    doc = _empty_doc()
    style_map_set(doc, table="monochrome.ctb", color="red", lineweight_mm=0.3)
    return style_map_lookup(doc, table="monochrome.ctb", color="blue")


def _empty_doc() -> dict[str, Any]:
    """夹具：有表、有一条映射，但**一条实体都没有**的 L1 载荷。"""
    doc: dict[str, Any] = {"schema_version": 1, "unit": "mm", "sketches": []}
    style_table_create(doc, name="monochrome.ctb")
    return doc


def _selftest_doc() -> Any:
    try:
        from cadcore import docmodel
        doc: Any = docmodel.new_document()
    except Exception:  # pragma: no cover - 只在包结构异常时走这里
        doc = {"schema_version": 1, "unit": "mm", "sketches": [], "sheets": [], "extra": {}}
    entities = [
        {"uuid": _uuid5("entity", "bottom"), "kind": "line", "layer": "0", "construction": False,
         "geom": {"x1": 0, "y1": 0, "x2": 100 * NM_PER_MM, "y2": 0}},
        {"uuid": _uuid5("entity", "right"), "kind": "line", "layer": "0", "construction": False,
         "geom": {"x1": 100 * NM_PER_MM, "y1": 0, "x2": 100 * NM_PER_MM, "y2": 50 * NM_PER_MM}},
        {"uuid": _uuid5("entity", "top"), "kind": "line", "layer": "0", "construction": False,
         "geom": {"x1": 100 * NM_PER_MM, "y1": 50 * NM_PER_MM, "x2": 0, "y2": 50 * NM_PER_MM}},
        {"uuid": _uuid5("entity", "left"), "kind": "line", "layer": "0", "construction": False,
         "geom": {"x1": 0, "y1": 50 * NM_PER_MM, "x2": 0, "y2": 0}},
        {"uuid": _uuid5("entity", "hole"), "kind": "circle", "layer": "holes",
         "construction": False,
         "geom": {"cx": 50 * NM_PER_MM, "cy": 25 * NM_PER_MM, "r": 10 * NM_PER_MM}},
    ]
    sketch = {"uuid": _uuid5("sketch", "main"), "plane": "XY", "entities": entities,
              "constraints": [], "dimensions": []}
    if hasattr(doc, "sketches"):
        doc.sketches.append(sketch)
        try:
            doc.layers = {"0": {"color": "black"}, "holes": {"color": "red"}}
        except Exception:  # pragma: no cover - Document 若是 frozen dataclass
            doc.extra["layers"] = {"0": {"color": "black"}, "holes": {"color": "red"}}
    else:  # pragma: no cover
        doc["sketches"].append(sketch)
        doc["layers"] = {"0": {"color": "black"}, "holes": {"color": "red"}}
    return doc


def selftest() -> int:
    """自带自检（不依赖 pytest）：跑通 10 个操作 + 落盘字节/xref 复验 + 坏入参反证。"""
    import tempfile

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    doc = _selftest_doc()
    fingerprint = hashlib.sha256(repr(_payload(doc).get("sketches")).encode("utf-8")).hexdigest()

    table = style_table_create(doc, name="monochrome.ctb", default_lineweight_mm=0.25)
    check("style_table_create", len(table) == 1 and style_table_list(doc) == table,
          "tables=%s" % style_table_list(doc))
    bulk = style_map_bulk(doc, table="monochrome.ctb", mappings=[
        {"color": "by_layer", "lineweight_mm": 0.35},
        {"color": "red", "lineweight_mm": 0.5},
        {"color": 1, "lineweight_mm": 0.7},
        {"color": "#FF0000", "lineweight_mm": 0.9},
        {"color": "*", "lineweight_mm": 0.25, "screening": 50},
    ])
    check("style_map_bulk", len(bulk) == 5 and len(set(bulk)) == 5, "entries=%d" % len(bulk))
    lookups = {key: style_map_lookup(doc, table="monochrome.ctb", color=key)[0]
               for key in ("red", 1, "#FF0000", "by_layer", "green")}
    check("style_map_lookup_specificity",
          lookups["red"] == bulk[1] and lookups[1] == bulk[2] and lookups["#FF0000"] == bulk[3]
          and lookups["by_layer"] == bulk[0] and lookups["green"] == bulk[4],
          "winners=%s" % {key: lookups[key][:8] for key in lookups})
    applied = style_map_apply(doc, table="monochrome.ctb")
    check("style_map_apply", len(applied) == 2, "colors=%d" % len(applied))
    removed = style_map_delete(doc, table="monochrome.ctb", color="#FF0000")
    check("style_map_delete", removed == [bulk[3]], "removed=%s" % removed)

    with tempfile.TemporaryDirectory() as tmp:
        svg_path = os.path.join(tmp, "sheet.svg")
        pdf_path = os.path.join(tmp, "sheet.pdf")
        svg_uuid = export_svg_text(doc, out_path=svg_path, title="EXTRA")
        pdf_uuid = export_pdf_text(doc, out_path=pdf_path, title="EXTRA")
        with open(svg_path, "rb") as handle:
            svg_bytes = handle.read()
        with open(pdf_path, "rb") as handle:
            pdf_bytes_on_disk = handle.read()
        manifest = export_manifest(doc)
        svg_text = render_svg_text(doc, title="EXTRA")
        check("export_svg_text",
              svg_bytes.decode("utf-8") == svg_text and svg_text.startswith("<?xml")
              and '<line x1="10" y1="200" x2="110" y2="200"' in svg_text
              and 'stroke-width="0.5"' in svg_text, "bytes=%d" % len(svg_bytes))
        pdf_text = pdf_bytes_on_disk.decode("latin-1")
        check("export_pdf_text",
              pdf_text.startswith("%PDF-1.4") and pdf_text.rstrip().endswith("%%EOF")
              and "[0 0 841.8898 595.2756]" in pdf_text and _verify_xref(pdf_bytes_on_disk)[0],
              "bytes=%d" % len(pdf_bytes_on_disk))
        check("export_manifest", manifest == [svg_uuid[0], pdf_uuid[0]], "manifest=%s" % manifest)
        check("render_determinism", render_pdf_text(doc, title="EXTRA") == pdf_text,
              "pdf_text_stable")

    check("geometry_untouched",
          hashlib.sha256(repr(_payload(doc).get("sketches")).encode("utf-8")).hexdigest()
          == fingerprint, "model geometry byte-identical")
    facts = module_facts(doc)
    check("module_facts", facts["operations"] == sorted(OPERATIONS)
          and facts["ops_count"] == len(OPERATIONS) and facts["exports"] == 2,
          "facts=%s" % {key: facts[key] for key in ("ops_count", "style_mappings", "exports")})

    bad = [
        ("bad_lineweight",
         _expect_red(lambda: style_map_set(doc, color="blue", lineweight_mm=-1.0),
                     "bad_lineweight")),
        ("bad_screening",
         _expect_red(lambda: style_map_set(doc, color="blue", screening=101), "bad_screening")),
        ("bad_color", _expect_red(lambda: style_map_set(doc, color="chartreuse"), "bad_color")),
        ("bad_table", _expect_red(lambda: style_table_create(doc, name="plain"), "bad_table")),
        ("duplicate_table", _expect_red(lambda: style_table_create(doc), "duplicate_table")),
        ("unknown_style_table",
         _expect_red(lambda: style_map_lookup(doc, table="x.ctb"), "unknown_style_table")),
        ("unknown_style_mapping",
         _expect_red(lambda: style_map_delete(doc, color="#123456"), "unknown_style_mapping")),
        ("no_style_mapping", _expect_red(_raise_no_mapping, "no_style_mapping")),
        ("bad_scale", _expect_red(lambda: render_svg_text(doc, scale="1:0"), "bad_scale")),
        ("bad_paper", _expect_red(lambda: render_svg_text(doc, paper="A9"), "bad_paper")),
        ("non_ascii_text",
         _expect_red(lambda: render_svg_text(doc, title=chr(0x56FE)), "non_ascii_text")),
        ("empty_mappings",
         _expect_red(lambda: style_map_bulk(doc, mappings=[]), "empty_mappings")),
    ]
    reds = {name: value for name, value in bad if value != "ok"}
    check("negative_controls", not reds, "red=%s" % reds)
    check("empty_document_is_loud",
          _expect_red(lambda: render_svg_text(_empty_doc(), scale="1:1"), "no_model_geometry")
          == "ok", "no geometry is not a silent empty success")

    passed = all(item[1] for item in checks)
    for name, ok, detail in checks:
        print("check %-26s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    print("EXTRA_OPS verdict=%s ops=%d checks=%d/%d"
          % ("PASS" if passed else "FAIL", len(OPERATIONS),
             sum(1 for item in checks if item[1]), len(checks)))
    return 0 if passed else 1


def main(argv: _Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--selftest", "-s"):
        print("usage: output_extra.py [--selftest]")
        return 2
    return selftest()


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())
