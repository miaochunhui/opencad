"""cadcore.io.dxf_bridge —— A13：图纸/草图 → DXF 字节，以及"读回 → 写回"的保真尺子。

契约（`docs/research/cad-base-selection/synthesis/MVP-INTERFACES.md` §B A13）::

    sheet_to_dxf(sheet, *, version="R2018") -> bytes
    sketch_to_dxf(sketch, *, version="R2018") -> bytes
    read_dxf(data) -> Any                     # packages/dxf 的 Document
    write_dxf(document) -> bytes
    roundtrip(data, *, tolerance=1e-9) -> FidelityReport

设计边界
--------
1. **不重造解析器**：读侧走 `packages/dxf/dxf/reader.py`，写侧走它的 `writer.py`；
   本模块只负责"图纸/草图 → DXF 文本"这一段（A13 与 A21 的分工）。
2. **坐标真源是整数纳米**，DXF 里写的是**文档单位**（毫米/英寸）—— 换算只发生在本模块的写出器里，
   与 `draw/sheet.py` 的内置降级写出器同一口径。
3. **未改即逐字节相等**：`read_dxf` 出来的文档没被改动时，`write_dxf` 的字节与输入**完全一致**
   （由 `packages/dxf` 的 `Document.render_bytes()` 自检保证；不等就抛 `DxfError`，不静默写"像"的文件）。
4. **保真度五项**（实体守恒 / 类型计数一致 / 坐标差 / lost_tags / audit_errors）在 `FidelityReport` 里
   **逐项可辨**：`0` = 真为零，`-1` = 未采（没得比 / 米尺没跑起来），**绝不把"没测"记成"测到 0"**。
5. **不静默降级**：`packages/dxf` 缺席 ⇒ `DependencyMissing`；版本不认、实体类型不认 ⇒ `DocumentError`；
   写出的字节必过一次"自己读回来"的自检（实体数/类型计数对不上就不许返回）。

只依赖标准库 + 仓内 `packages/dxf`；本模块**不碰** `packages/dxf` 与 `packages/vault` 的任何文件。
"""

from __future__ import annotations
import decimal
import hashlib
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from itertools import zip_longest
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from ..errors import DependencyMissing, DocumentError
#: 标注文本的**唯一读法**真源（`args["text"]` 与顶层 `text` 两种写侧形态都认下来）。
from ..docmodel import annotation_text

__all__ = [
    "ACADVER_BY_VERSION",
    "FidelityReport",
    "read_dxf",
    "write_dxf",
    "roundtrip",
    "compare_documents",
    "sheet_to_dxf",
    "sketch_to_dxf",
    "annotation_text_lines",
]

#: DXF 版本 → `$ACADVER`。真源在 `cadcore.draw.sheet.DXF_ACADVER`（此表只是它缺席时的兜底）。
ACADVER_BY_VERSION: dict[str, str] = {
    "R12": "AC1009",
    "R13": "AC1012",
    "R14": "AC1014",
    "R2000": "AC1015",
    "R2004": "AC1018",
    "R2007": "AC1021",
    "R2010": "AC1024",
    "R2013": "AC1027",
    "R2018": "AC1032",
}

#: 版本 → 是否为"ASCII-only 时代"的 DXF（非 ASCII 文本写进去要留痕，不许假装是某个代码页）。
_ASCII_ERA = ("R12", "R13", "R14")

#: 版本序（降级判定的唯一真源）：`R12 < R13 < R14 < R2000 < …`。与 `DXF_ACADVER` 同集合。
VERSION_ORDER: tuple[str, ...] = ("R12", "R13", "R14", "R2000", "R2004", "R2007",
                                  "R2010", "R2013", "R2018")

#: 写出器会写、且**不是每个版本都能表达**的实体 → 最低版本（真源＝DXF 版本史：
#: `LWPOLYLINE` 自 R14、`MTEXT` 自 R13）。本表只登记写出器实际会写的那几个，不复制整张实体表。
ENTITY_MIN_VERSION: dict[str, str] = {"LWPOLYLINE": "R14", "MTEXT": "R13"}

#: 同理：LAYER 表里**不是每个版本都能表达**的组码 → 最低版本（组码 370 线宽自 R2000）。
LAYER_CODE_MIN_VERSION: dict[int, str] = {370: "R2000"}


def _min_version_ok(order: int, minimum: str) -> bool:
    """版本序 `order` 能否表达最低要求 `minimum` 的那个实体/组码。"""
    return VERSION_ORDER.index(minimum) <= int(order)


def _version_index(version: Any) -> int:
    """版本名 → 版本序下标；不认的版本**抛错**（与 `_acadver_for` 同一口径：不猜、不回落）。"""
    key = str(version or "").strip().upper()
    if key not in VERSION_ORDER:
        raise DocumentError("unsupported DXF version", reason="unsupported_dxf_version",
                            version=str(version), known=list(VERSION_ORDER))
    return VERSION_ORDER.index(key)

_NM_PER_MM = 1_000_000
_NM_PER_INCH = int(round(25.4 * _NM_PER_MM))

#: 图层 → ACI 颜色（与 `draw/sheet.py` 的 `_LAYER_COLORS` 同口径；它缺席时用这份兜底）。
_LAYER_COLORS_FALLBACK: dict[str, int] = {
    "VISIBLE": 7, "HIDDEN": 3, "DIMENSIONS": 4, "FRAME": 1, "TITLEBLOCK": 2,
}

#: 图层 → 线型（隐藏线用 DASHED；其余 CONTINUOUS）。
_LAYER_LINETYPES_FALLBACK: dict[str, str] = {"HIDDEN": "DASHED"}

_MODULES: dict[str, Any] = {}


# --------------------------------------------------------------------------------------
# 依赖装配（显式降级，不静默）
# --------------------------------------------------------------------------------------


def _packages_root() -> str:
    """`.../oss-alternatives/packages`（本文件在 `<packages>/cadcore/cadcore/io/` 下）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _dxf_modules() -> dict[str, Any]:
    """`packages/dxf` 的三个模块（reader/writer/model）；缺席 ⇒ `DependencyMissing`。"""
    if _MODULES:
        return _MODULES
    candidate = os.path.join(_packages_root(), "dxf")
    root = _packages_root()
    for path in (candidate, root):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    if importlib.util.find_spec("dxf") is None:
        raise DependencyMissing(
            "the dxf package is not importable; DXF export/read-back is unavailable",
            reason="dxf_package_absent", searched=candidate)
    from dxf import model as dxf_model
    from dxf import reader as dxf_reader
    from dxf import writer as dxf_writer
    _MODULES.update({"model": dxf_model, "reader": dxf_reader, "writer": dxf_writer})
    return _MODULES


def _sheet_module() -> Any:
    """`cadcore.draw.sheet`（图纸实体流的唯一真源：`iter_entities` 与版本表都在它那里）。"""
    from ..draw import sheet as sheet_module

    return sheet_module


def _acadver_for(version: str) -> str:
    """版本名 → `$ACADVER` 值；不认的版本**抛错**（不猜、不回落）。"""
    table = dict(getattr(_sheet_module(), "DXF_ACADVER", {}) or {}) or dict(ACADVER_BY_VERSION)
    key = str(version or "").strip().upper()
    value = table.get(key)
    if not value:
        raise DocumentError("unsupported DXF version", reason="unsupported_dxf_version",
                            version=str(version), known=sorted(table))
    return str(value)


# --------------------------------------------------------------------------------------
# 小程序：DXF 文本发射器
# --------------------------------------------------------------------------------------


#: 组码值行的十进制有效位数。DXF 的双精度约 15.95 位十进制有效数字；写 **17 位**保证
#: 「十进制原文 → 最近双精度」无损（往返逐位一致），相对误差 <= 5e-18 —— 比扫描席的 1e-9
#: 相对容差严 9 个数量级，也远小于真源自己的 1 纳米。
_DECIMAL_DIGITS = 17


def _decimal_text(value: decimal.Decimal) -> str:
    """`Decimal` → DXF 值行文本：**定点写法**（不用科学计数，老 reader 只认定点）+ 去掉尾零。"""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def _nm_to_unit_text(value_nm: Any, per_unit: int) -> str:
    """整数纳米 → 文档单位的十进制原文（**精确除法，不过 float**）。

    为什么不能走 float（A1 缺陷 RC）：`round(float(nm)/per_unit, 6)` 先把 nm 舍成 53 位二进制，
    再按 6 位小数定点格式化 —— 对 mm（1e6 nm/mm）恰好 1 nm 无损，对 **inch**（25 400 000 nm/inch）
    的量化步长是 **25.4 nm**，真源里可表示的纳米被静默改写。这里用 `Decimal` 精确除法：
    mm 文档的分母只含 2/5 因子 ⇒ 可表示范围内**逐位精确**；inch 文档的分母含 127 ⇒ 十进制不终止，
    取 17 位有效数字（相对误差 <= 5e-18，且 `Fraction(十进制原文)` 与 `Fraction(nm, per_unit)`
    的偏差远小于任何容差）。
    """
    nm = int(value_nm)
    with decimal.localcontext() as context:
        context.prec = _DECIMAL_DIGITS
        quotient = decimal.Decimal(nm) / decimal.Decimal(per_unit)
    return _decimal_text(quotient)


def _float_text(value: float) -> str:
    """双精度 → 值行文本：**最短且能无损往返**的十进制原文（`repr`），必要时转定点写法。

    A2 缺陷 RC：`f"{value:.6f}"` 对 7 位有效数字的角度是硬截断（33.3333333 → 33.333333，
    相对漂移 9e-9 > 1e-9 容差）。`repr` 给的是"读回来仍是同一个双精度"的最短原文
    （≤17 位有效数字）⇒ `float(写出的原文) == 真源`（逐位一致，可 `struct.pack` 复核）。
    """
    text = repr(float(value))
    if "e" in text or "E" in text:
        text = format(decimal.Decimal(text), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def _value_text(value: Any) -> str:
    """组码值行文本（整数不加小数点；浮点写**无损往返**的十进制原文，去尾零）。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float_text(value)
    return str(value)


class _AsciiDxf:
    """「组码行 + 值行」成对追加（换行统一 `\\n`；写出后必过一次读回自检）。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def tag(self, code: Any, value: Any) -> "_AsciiDxf":
        self.lines.append(str(int(code)))
        self.lines.append(_value_text(value))
        return self

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _as_float(value: Any, default: float = 0.0) -> float:
    """DXF 双精度坐标转换：类型不对退回 `default`（历史口径），**溢出必须结构化报错**。

    为什么不能把 `OverflowError` 一起吞掉：吞了就是把"这个坐标根本写不进 DXF"静默写成 0.0 ——
    画到原点的假几何比报错坏得多（AGENTS.md E2「读侧禁静默 0」）。而放它裸奔的话，导出会以
    内建 `OverflowError` 崩到 API 层（实测缺陷 `extreme:huge_int_309digit`）。两条都不对，所以
    这里转成可归因的 `DocumentError`。
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
    except OverflowError as exc:
        raise DocumentError("coordinate is too large for a DXF double-precision value",
                            reason="coordinate_out_of_range", value=repr(value)) from exc


def _single_line(value: Any) -> str:
    """DXF 值行必须是单行：把换行折成空格（不留痕的截断比换行更糟）。

    只用于**本来就该单行**的字段（DIMENSION 文本、999 注释）；多行文本走 `_mtext_encoded`
    （MTEXT，换行不丢），**不许**在这里静默折行。
    """
    return str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _has_line_break(value: Any) -> bool:
    """值里有没有换行（`\\n` / `\\r` / `\\r\\n` 三种形态都算）。"""
    return any(char in str(value) for char in ("\r", "\n"))


#: MTEXT 组码 1/3 的单块上限（DXF 口径：超过 250 字符要切成多块，最后一块进组码 1）。
_MTEXT_CHUNK = 250


def _mtext_encoded(value: Any) -> str:
    """文本 → MTEXT 值行原文：**换行逐字保留**（`\\n`/`\\r`/`\\r\\n` → MTEXT 的段分隔 `\\P`），
    字面反斜杠转义成 `\\\\`，因此解码回来与真源逐字相等（A4 缺陷 RC：原先折成空格且无留痕）。"""
    text = str(value).replace("\\", "\\\\")
    return text.replace("\r\n", "\\P").replace("\r", "\\P").replace("\n", "\\P")


def _emit_mtext(out: "_AsciiDxf", *, layer: str, at: Sequence[Any], height_nm: Any,
                text: Any, coord: Any) -> None:
    """多行文本 → `MTEXT` 实体（R13+）。文本按 DXF 口径分块：前若干块组码 3、最后一块组码 1。"""
    encoded = _mtext_encoded(text)
    chunks = [encoded[index:index + _MTEXT_CHUNK]
              for index in range(0, len(encoded), _MTEXT_CHUNK)] or [""]
    out.tag(0, "MTEXT").tag(8, layer)
    out.tag(10, coord(at[0])).tag(20, coord(at[1])).tag(30, 0)
    out.tag(40, coord(height_nm))
    out.tag(71, 1).tag(72, 1).tag(7, "STANDARD")
    for chunk in chunks[:-1]:
        out.tag(3, chunk)
    out.tag(1, chunks[-1])


def _comment_text(value: Any) -> str:
    """`999` 注释的值行：换行转义成字面 `\\n`（**不折成空格** —— 注释里的文本也要能逐字还原）。

    注释是**留痕**面，不是可以直接改写内容的字段：折行会让"产物里读到的东西"与真源不同，
    而转义后的 `\\n` 在产物里看得见、可 grep、可还原，且不会给 DXF 文本流注入新行。
    """
    return str(value).replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def _entity_extent_nm(item: Mapping[str, Any]) -> "tuple[float, float, float, float] | None":
    """实体在**纳米**下的包围盒（用于 `$EXTMIN/$EXTMAX`）；算不出来就返回 None。"""
    kind = str(item.get("type") or "").upper()
    points: list[tuple[float, float]] = []
    if kind == "LINE":
        points = [(_as_float(item["start"][0]), _as_float(item["start"][1])),
                  (_as_float(item["end"][0]), _as_float(item["end"][1]))]
    elif kind in ("CIRCLE", "ARC"):
        cx, cy = _as_float(item["center"][0]), _as_float(item["center"][1])
        radius = _as_float(item.get("radius_nm"))
        return (cx - radius, cy - radius, cx + radius, cy + radius)
    elif kind == "LWPOLYLINE":
        points = [(_as_float(x), _as_float(y)) for x, y in (item.get("points") or [])]
    elif kind in ("TEXT", "DIMENSION", "POINT"):
        at = item.get("at")
        if isinstance(at, (list, tuple)) and len(at) >= 2:
            points = [(_as_float(at[0]), _as_float(at[1]))]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _layer_table(sheet: Any, entities: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """要写进 LAYER 表的图层（图纸声明的 ∪ 实体用到的 ∪ `0`）及其颜色/线型/线宽。"""
    module = _sheet_module()
    colors = dict(_LAYER_COLORS_FALLBACK)
    declared_colors = getattr(module, "_LAYER_COLORS", None)
    if isinstance(declared_colors, Mapping):
        colors.update({str(key): int(value) for key, value in declared_colors.items()})
    linetypes = dict(_LAYER_LINETYPES_FALLBACK)

    widths: dict[str, int] = {"_default": 350_000}
    builder = getattr(module, "_line_widths_nm", None)
    if callable(builder):
        try:
            widths.update({str(key): int(value) for key, value in dict(builder(sheet)).items()})
        except Exception:  # noqa: BLE001 - 线宽只是观感，算不出来用标称值并留痕（NA，不编数）
            pass

    names: set[str] = {"0"}
    declared_layers = getattr(sheet, "layers", None)
    if isinstance(declared_layers, Mapping):
        names.update(str(name) for name in declared_layers)
        for name, entry in declared_layers.items():
            if isinstance(entry, Mapping) and entry.get("linetype"):
                linetypes[str(name)] = str(entry["linetype"])
    for item in entities:
        names.add(str(item.get("layer") or "0"))

    default_width = int(widths.get("_default", 350_000))
    table: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        table[name] = {
            "color": int(colors.get(name, 7)),
            "linetype": str(linetypes.get(name, "CONTINUOUS")),
            "width_nm": int(widths.get(name, default_width)),
        }
    return table


class _Emitted(NamedTuple):
    """`_emit` 的产物：字节 + **真正写出的**实体类型（自检按它比，不按源清单猜）+ 降级留痕。"""

    payload: bytes
    entity_types: list
    notes: list


def _emit(entities: Sequence[Mapping[str, Any]], *, acadver: str, version: str, unit: str,
          layers: Mapping[str, Mapping[str, Any]], comments: Sequence[str] = ()) -> "_Emitted":
    """实体流 → DXF ASCII 字节（结构照 `draw/sheet.py` 内置写出器已验证过的组码约定）。

    **声明与内容一致（A5 缺陷 RC）**：`$ACADVER` 跟着 `version` 走，写出的实体与组码也按同一个
    版本序降级 —— 声明 R12 就**不写** `LWPOLYLINE`（降级成 R12 的 `POLYLINE`/`VERTEX`/`SEQEND`，
    与 `ops/output.py::_dxf_render` 的 R12 分支同一结构）与 LAYER 组码 370（R2000+ 才有）；
    本版本**无法表达**的东西（如 R12 里的多行文本）**结构化拒绝**，绝不"声明一套、写另一套"。
    """
    order = _version_index(version)
    per_unit = _NM_PER_INCH if str(unit) == "inch" else _NM_PER_MM

    def coord(value_nm: Any) -> str:
        """纳米 → **文档单位**（DXF 的图纸单位是毫米/英寸，不是纳米）的十进制原文。

        A1 缺陷 RC：`round(float(nm)/per_unit, 6)` 对 inch（25.4 nm 量化步长）会改写真源；
        这里走 `_nm_to_unit_text` 的整数精确除法，mm 逐位精确、inch 相对误差 <= 5e-18。
        """
        return _nm_to_unit_text(value_nm, per_unit)

    written: list = []
    notes: list = []

    out = _AsciiDxf()
    out.tag(0, "SECTION").tag(2, "HEADER")
    out.tag(9, "$ACADVER").tag(1, acadver)
    boxes = [box for box in (_entity_extent_nm(item) for item in entities) if box is not None]
    if boxes:
        out.tag(9, "$EXTMIN").tag(10, coord(min(box[0] for box in boxes))) \
           .tag(20, coord(min(box[1] for box in boxes))).tag(30, 0)
        out.tag(9, "$EXTMAX").tag(10, coord(max(box[2] for box in boxes))) \
           .tag(20, coord(max(box[3] for box in boxes))).tag(30, 0)
    out.tag(0, "ENDSEC")

    out.tag(0, "SECTION").tag(2, "TABLES")
    out.tag(0, "TABLE").tag(2, "LTYPE").tag(70, 2)
    for name, description, pattern in (("CONTINUOUS", "Solid line", ()),
                                       ("DASHED", "Dashed __ __ __", (0.5, -0.25))):
        out.tag(0, "LTYPE").tag(2, name).tag(70, 0).tag(3, description).tag(72, 65)
        out.tag(73, len(pattern)).tag(40, 0.75 if pattern else 0.0)
        for item in pattern:
            out.tag(49, item)
    out.tag(0, "ENDTAB")
    out.tag(0, "TABLE").tag(2, "STYLE").tag(70, 1)
    out.tag(0, "STYLE").tag(2, "STANDARD").tag(70, 0).tag(40, 0.0).tag(41, 1.0).tag(50, 0.0)
    out.tag(71, 0).tag(42, 2.5).tag(3, "txt").tag(4, "")
    out.tag(0, "ENDTAB")
    out.tag(0, "TABLE").tag(2, "LAYER").tag(70, max(1, len(layers)))
    width_code_ok = _min_version_ok(order, LAYER_CODE_MIN_VERSION[370])
    if not width_code_ok:
        notes.append(
            f"LAYER group code 370 (lineweight) omitted: {version} has no lineweight field "
            f"({LAYER_CODE_MIN_VERSION[370]}+); declared version and content stay consistent")
    for name in sorted(layers):
        entry = layers[name]
        out.tag(0, "LAYER").tag(2, name).tag(70, 0).tag(62, int(entry.get("color", 7)))
        out.tag(6, str(entry.get("linetype", "CONTINUOUS")))
        if width_code_ok:
            # 组码 370 的单位是 1/100 毫米（不是纳米，也不是图纸单位）
            out.tag(370, int(round(int(entry.get("width_nm", 350_000)) / 10_000)))
    out.tag(0, "ENDTAB")
    out.tag(0, "ENDSEC")

    dimensions = [item for item in entities if str(item.get("type") or "").upper() == "DIMENSION"]
    if dimensions:
        out.tag(0, "SECTION").tag(2, "BLOCKS")
        for index in range(1, len(dimensions) + 1):
            block = f"*D{index}"
            out.tag(0, "BLOCK").tag(8, str(dimensions[index - 1].get("layer") or "DIMENSIONS"))
            out.tag(2, block).tag(70, 0).tag(10, 0).tag(20, 0).tag(30, 0).tag(3, block).tag(1, "")
            out.tag(0, "ENDBLK").tag(8, str(dimensions[index - 1].get("layer") or "DIMENSIONS"))
        out.tag(0, "ENDSEC")

    out.tag(0, "SECTION").tag(2, "ENTITIES")
    for note in comments:
        out.tag(999, _comment_text(note))
    dimension_index = 0
    for item in entities:
        kind = str(item.get("type") or "").upper()
        layer = str(item.get("layer") or "0")
        if kind == "LINE":
            out.tag(0, "LINE").tag(8, layer)
            out.tag(10, coord(item["start"][0])).tag(20, coord(item["start"][1])).tag(30, 0)
            out.tag(11, coord(item["end"][0])).tag(21, coord(item["end"][1])).tag(31, 0)
            written.append("LINE")
        elif kind == "CIRCLE":
            out.tag(0, "CIRCLE").tag(8, layer)
            out.tag(10, coord(item["center"][0])).tag(20, coord(item["center"][1])).tag(30, 0)
            out.tag(40, coord(item["radius_nm"]))
            written.append("CIRCLE")
        elif kind == "ARC":
            out.tag(0, "ARC").tag(8, layer)
            out.tag(10, coord(item["center"][0])).tag(20, coord(item["center"][1])).tag(30, 0)
            out.tag(40, coord(item["radius_nm"]))
            out.tag(50, _as_float(item.get("start_deg"))).tag(51, _as_float(item.get("end_deg")))
            written.append("ARC")
        elif kind == "LWPOLYLINE":
            points = list(item.get("points") or [])
            closed = 1 if item.get("closed") else 0
            if _min_version_ok(order, ENTITY_MIN_VERSION["LWPOLYLINE"]):
                out.tag(0, "LWPOLYLINE").tag(8, layer).tag(90, len(points))
                out.tag(70, closed)
                for x, y in points:
                    out.tag(10, coord(x)).tag(20, coord(y))
                written.append("LWPOLYLINE")
            else:
                # 声明 R12 却写 LWPOLYLINE（R14+ 才有）就是"打开即报错/丢实体"的产物 ⇒ 按 R12 的
                # POLYLINE/VERTEX/SEQEND 老写法降级（组码 66=1 表明后面跟顶点）。
                out.tag(0, "POLYLINE").tag(8, layer).tag(66, 1).tag(70, closed)
                for x, y in points:
                    out.tag(0, "VERTEX").tag(8, layer)
                    out.tag(10, coord(x)).tag(20, coord(y)).tag(30, 0)
                out.tag(0, "SEQEND").tag(8, layer)
                written.append("POLYLINE")
                notes.append(
                    f"LWPOLYLINE downgraded to POLYLINE/VERTEX/SEQEND ({len(points)} vertices): "
                    f"{version} has no LWPOLYLINE ({ENTITY_MIN_VERSION['LWPOLYLINE']}+)")
        elif kind == "TEXT":
            at = item["at"]
            raw_text = item.get("text", "")
            if _has_line_break(raw_text):
                # A4 缺陷 RC：多行文本被 `_single_line` 折成空格、产物无留痕。正确解是 MTEXT
                # （R13+ 的原生多行实体）；声明版本装不下它时**结构化拒绝**，绝不静默折行。
                if not _min_version_ok(order, ENTITY_MIN_VERSION["MTEXT"]):
                    raise DocumentError(
                        "multi-line text cannot be carried by this DXF version",
                        reason="multiline_text_unsupported_in_version", version=str(version),
                        needs=ENTITY_MIN_VERSION["MTEXT"], text_lines=len(
                            str(raw_text).replace("\r\n", "\n").replace("\r", "\n").split("\n")))
                _emit_mtext(out, layer=layer, at=at, height_nm=item.get("height_nm") or 0,
                            text=raw_text, coord=coord)
                written.append("MTEXT")
                notes.append("multi-line TEXT written as MTEXT (line breaks preserved; "
                             "DXF TEXT group 1 is single-line by definition)")
            else:
                out.tag(0, "TEXT").tag(8, layer)
                out.tag(10, coord(at[0])).tag(20, coord(at[1])).tag(30, 0)
                out.tag(40, coord(item.get("height_nm") or 0))
                out.tag(1, str(raw_text))
                out.tag(7, "STANDARD")
                out.tag(72, 0).tag(11, coord(at[0])).tag(21, coord(at[1])).tag(31, 0)
                written.append("TEXT")
        elif kind == "DIMENSION":
            dimension_index += 1
            at = item["at"]
            if _has_line_break(item.get("text", "")):
                raise DocumentError(
                    "multi-line dimension text cannot be carried by a DXF DIMENSION entity",
                    reason="multiline_text_unsupported_in_entity", entity_type="DIMENSION",
                    hint="split the label or use a TEXT/MTEXT annotation")
            out.tag(0, "DIMENSION").tag(8, layer).tag(2, f"*D{dimension_index}")
            out.tag(3, "STANDARD").tag(70, _dimension_type_code(item.get("kind")))
            out.tag(1, _single_line(item.get("text", "")))
            out.tag(10, coord(at[0])).tag(20, coord(at[1])).tag(30, 0)
            out.tag(11, coord(at[0])).tag(21, coord(at[1])).tag(31, 0)
            out.tag(42, coord(item.get("value_nm") or 0))
            out.tag(13, coord(at[0])).tag(23, coord(at[1])).tag(33, 0)
            out.tag(14, coord(at[0])).tag(24, coord(at[1])).tag(34, 0)
            written.append("DIMENSION")
        else:
            raise DocumentError("unsupported entity type for DXF export",
                                reason="unsupported_entity_type", entity_type=kind)
    out.tag(0, "ENDSEC")
    out.tag(0, "EOF")
    return _Emitted(out.text().encode("utf-8"), written, notes)


def _dimension_type_code(kind: Any) -> int:
    """DXF 组码 70：0=线性/旋转、2=角度、3=直径、4=半径（DXF 自身的类型登记）。"""
    return {"linear": 0, "angular": 2, "diameter": 3, "radial": 4}.get(str(kind), 0)


def _self_check(payload: bytes, written_types: Sequence[str], *, what: str) -> None:
    """写出的字节必须能被复用件读回，且实体数/类型计数与**真正写出的**清单逐项相等。

    比的是 `_emit` 的写出清单（不是源清单）：版本降级（R12 的 LWPOLYLINE→POLYLINE）会改类型，
    拿源清单比会把一次**有意的**降级当成缺陷。
    """
    reader = _dxf_modules()["reader"]
    document = reader.parse_bytes(payload)
    expected: dict[str, int] = {}
    for kind in written_types:
        expected[str(kind).upper()] = expected.get(str(kind).upper(), 0) + 1
    counts = dict(document.entity_type_counts())
    if document.entity_count() != len(written_types) or counts != expected:
        raise DocumentError("DXF self-check failed: written bytes do not carry every entity",
                            reason="dxf_self_check_failed", source=what,
                            entities_expected=len(written_types), entities_read=document.entity_count(),
                            type_counts_expected=expected, type_counts_read=counts)


def _note(sheet: Any, message: str) -> None:
    """给图纸记一条可机读诊断（`Sheet.note`；纯 dict 图纸没有这个方法就跳过）。"""
    note = getattr(sheet, "note", None)
    if callable(note):
        note(message)


# --------------------------------------------------------------------------------------
# A13：图纸 / 草图 → DXF
# --------------------------------------------------------------------------------------


def sheet_to_dxf(sheet: Any, *, version: str = "R2018") -> bytes:
    """图纸 → DXF 字节（R2018 默认）。实体流复用 `draw.sheet.iter_entities`，坐标写文档单位。"""
    acadver = _acadver_for(version)
    entities = [dict(item) for item in _sheet_module().iter_entities(sheet)]
    unit = str(getattr(sheet, "unit", "mm") or "mm")
    layers = _layer_table(sheet, entities)
    emitted = _emit(entities, acadver=acadver, version=str(version), unit=unit, layers=layers)
    payload = emitted.payload
    _self_check(payload, emitted.entity_types, what="sheet_to_dxf")
    for message in emitted.notes:
        _note(sheet, f"dxf_bridge: {message}")
    _note(sheet, "dxf_bridge: header unit variables ($INSUNITS/$MEASUREMENT/$LUNITS/$LUPREC) and "
                 "$DWGCODEPAGE are omitted -- that mapping is not verified (I18N-DESIGN 4); "
                 f"coordinates are written in the document unit {unit!r}")
    if str(version).strip().upper() in _ASCII_ERA and not payload.decode("utf-8").isascii():
        _note(sheet, f"dxf_bridge: {version} is an ASCII-era revision but the drawing carries "
                     "non-ASCII text; bytes are UTF-8 (no codepage is claimed)")
    return payload


def _iter_annotations(annotations: Any) -> list[Any]:
    """标注容器 → 列表（dict 的 `annotations[]`；缺席/坏形态当作"没有标注"，不抛）。"""
    if annotations is None:
        return []
    if isinstance(annotations, Mapping):
        return list(annotations.values())
    if isinstance(annotations, (str, bytes)) or not isinstance(annotations, Sequence):
        return []
    return list(annotations)


def annotation_text_lines(annotations: Any) -> list[str]:
    """L1 标注（`doc.annotations`）→ 导出用文本行；**只出真有文本的那些**，顺序即文档顺序。

    为什么走**注释**而不是实体（B6 缺陷 RC7）：草图的标注只有 `target`（引用）没有位置字段，
    草图导出又没有图纸布局 —— 编一个坐标就是伪造几何，与 `sketch.dimensions` 同一条口径
    （尺寸在草图导出里也只写 `999` 注释）。这样标注文本**逐字进产物**（可 grep、可被下游消费），
    而不是"被接受、往返保留、产物里一个字都没有、也没有诊断"的静默丢数据。

    取值口径唯一真源 = `docmodel.annotation_text`（与 `draw.sheet.annotation_texts` 同一份读法）：
    `literal=True` 的自由文本优先（`args["text"]` 与顶层 `text` 两种写侧形态都认，见 RC-S2），
    否则写 `text_key`（真源存的就是 key，翻译在语言层）。两者都没有（或为空）⇒ 不出行，也不编一行空的。
    """
    out: list[str] = []
    for item in _iter_annotations(annotations):
        uuid_text = str(_get(item, "uuid") or "")
        text = annotation_text(item)
        if not text:
            continue
        where = f" annotation {uuid_text}" if uuid_text else " annotation"
        out.append(f"CADCORE:{where} text: {text}")
    return out


def sketch_to_dxf(sketch: Any, *, version: str = "R2018", annotations: Any = ()) -> bytes:
    """草图 → DXF 字节（R2018 默认）。草图实体照 L1 `entity{uuid,kind,layer,geom}` 读。

    草图**没有图纸布局**，所以 L1 `sketch.dimensions` 不能变成有位置的 `DIMENSION` 实体
    （编一个坐标就是伪造几何）⇒ 数量写进 `999` 注释标签留痕，需要时走 `sheet_to_dxf`。
    `annotations`（文档级 `doc.annotations`，可为空）同走 `999` 注释承载**文本**、不伪造位置
    —— 见 `annotation_text_lines`。
    """
    acadver = _acadver_for(version)
    entities, skipped = _sketch_entities(sketch)
    comments = list(annotation_text_lines(annotations))
    if skipped:
        comments.append(f"CADCORE: skipped {skipped} sketch dimension(s) -- they need a sheet layout "
                        "(use cadcore.io.dxf_bridge.sheet_to_dxf)")
    layers = _layer_table(None, entities)
    emitted = _emit(entities, acadver=acadver, version=str(version), unit="mm", layers=layers,
                    comments=comments)
    _self_check(emitted.payload, emitted.entity_types, what="sketch_to_dxf")
    return emitted.payload


def _sketch_entities(sketch: Any) -> "tuple[list[dict[str, Any]], int]":
    """草图实体 → 写出器吃的实体 dict 清单；返回 `(实体, 被跳过的尺寸条数)`。"""
    raw_entities = _get(sketch, "entities") or []
    if isinstance(raw_entities, Mapping):  # pragma: no cover - 交付面是 list
        raw_entities = list(raw_entities.values())
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw_entities):
        kind = str(_get(item, "kind") or "").strip().lower()
        layer = str(_get(item, "layer") or "0")
        geom = _get(item, "geom") or {}
        if not isinstance(geom, Mapping):
            raise DocumentError("sketch entity geometry is not a mapping",
                                reason="sketch_geometry_invalid", index=index, kind=kind)
        if kind == "line":
            out.append({"type": "LINE", "layer": layer,
                        "start": [_nm(_get(geom, "x1")), _nm(_get(geom, "y1"))],
                        "end": [_nm(_get(geom, "x2")), _nm(_get(geom, "y2"))]})
        elif kind == "circle":
            out.append({"type": "CIRCLE", "layer": layer,
                        "center": [_nm(_get(geom, "cx")), _nm(_get(geom, "cy"))],
                        "radius_nm": _nm(_get(geom, "r"))})
        elif kind == "arc":
            out.append({"type": "ARC", "layer": layer,
                        "center": [_nm(_get(geom, "cx")), _nm(_get(geom, "cy"))],
                        "radius_nm": _nm(_get(geom, "r")),
                        "start_deg": _as_float(_first(geom, ("start_deg", "a1_deg", "start"))),
                        "end_deg": _as_float(_first(geom, ("end_deg", "a2_deg", "end")))})
        elif kind == "polyline":
            points = _first(geom, ("points", "points_nm", "pts")) or []
            out.append({"type": "LWPOLYLINE", "layer": layer,
                        "points": [[_nm(point[0]), _nm(point[1])] for point in points],
                        "closed": bool(_get(geom, "closed"))})
        else:
            raise DocumentError("unsupported sketch entity kind",
                                reason="unsupported_sketch_entity_kind", index=index, kind=kind)
    dimensions = _get(sketch, "dimensions") or []
    return out, len(dimensions)


def _get(item: Any, key: str) -> Any:
    """同时吃 Mapping 与对象（L1 是 dict，dataclass/属性对象也认）。"""
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _first(container: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        value = _get(container, key)
        if value is not None:
            return value
    return None


def _nm(value: Any) -> int:
    """纳米整数（真源口径）；非整数输入**响亮报错**，不静默取整。"""
    if isinstance(value, bool) or value is None:
        raise DocumentError("sketch geometry component is not an integer nanometre value",
                            reason="sketch_geometry_invalid", value=repr(value))
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    raise DocumentError("sketch geometry component is not an integer nanometre value",
                        reason="sketch_geometry_invalid", value=repr(value))


# --------------------------------------------------------------------------------------
# A13：读 / 写 / 往返保真
# --------------------------------------------------------------------------------------


def read_dxf(data: Any) -> Any:
    """字节（或路径）→ `packages/dxf` 的 `Document`；坏输入由它抛带行号的 `DxfError`。"""
    reader = _dxf_modules()["reader"]
    if isinstance(data, (bytes, bytearray, memoryview)):
        return reader.parse_bytes(bytes(data))
    return reader.read(os.fspath(data))


def write_dxf(document: Any) -> bytes:
    """`Document` → 字节。**未改动的文档与输入逐字节相同**（由复用件自检，不等就抛错）。"""
    return _dxf_modules()["writer"].dump_bytes(document)


@dataclass
class FidelityReport:
    """`read → write` 的保真度账（五项 + 取证字段）。

    口径（AGENTS E2.3）：计数字段 `0` = **真为零**；`-1` = **未采**（没得比 / 米尺没跑起来）。
    任何一项未采 ⇒ `passed=False`，**不给"看起来绿"的判定行**。
    """

    tolerance: float = 1e-9
    bytes_in: int = -1
    bytes_out: int = -1
    sha256_in: str = ""
    sha256_out: str = ""
    bytes_identical: int = -1
    entities_in: int = -1
    entities_out: int = -1
    entities_conserved: int = -1
    type_counts_in: dict = field(default_factory=dict)
    type_counts_out: dict = field(default_factory=dict)
    type_counts_match: int = -1
    max_coord_delta: float = -1.0
    lost_tags: int = -1
    audit_errors: int = 0
    detail: list = field(default_factory=list)

    @property
    def measured(self) -> bool:
        """五项里有没有"没采到"的（`-1`）。"""
        return not (self.entities_conserved < 0 or self.type_counts_match < 0
                    or self.max_coord_delta < 0.0 or self.lost_tags < 0
                    or self.bytes_identical < 0)

    @property
    def passed(self) -> bool:
        """判定：字节相等 + 实体守恒 + 类型计数一致 + 坐标差 ≤ 容差 + 零丢标签 + 零审计错。"""
        return (self.measured and self.bytes_identical == 1 and self.entities_conserved == 1
                and self.type_counts_match == 1 and self.lost_tags == 0
                and self.max_coord_delta <= float(self.tolerance) and self.audit_errors == 0)

    def as_dict(self) -> dict:
        """机读形态（`-1` 原样出现，"未采"与"真为零"不同形）。"""
        return {
            "tolerance": float(self.tolerance),
            "bytes_in": int(self.bytes_in), "bytes_out": int(self.bytes_out),
            "sha256_in": self.sha256_in, "sha256_out": self.sha256_out,
            "bytes_identical": int(self.bytes_identical),
            "entities_in": int(self.entities_in), "entities_out": int(self.entities_out),
            "entities_conserved": int(self.entities_conserved),
            "type_counts_in": dict(self.type_counts_in),
            "type_counts_out": dict(self.type_counts_out),
            "type_counts_match": int(self.type_counts_match),
            "max_coord_delta": float(self.max_coord_delta),
            "lost_tags": int(self.lost_tags), "audit_errors": int(self.audit_errors),
            "measured": bool(self.measured), "passed": bool(self.passed),
            "detail": list(self.detail),
        }

    def verdict_line(self) -> str:
        """一行判定行：`0/-1` 都按三态原样打出来。"""
        return ("DXF_BRIDGE_ROUNDTRIP: verdict=%s entities=%d type_counts_match=%d "
                "max_coord_delta=%s lost_tags=%d audit_errors=%d bytes_identical=%d"
                % ("PASS" if self.passed else ("NA" if not self.measured else "FAIL"),
                   self.entities_in, self.type_counts_match,
                   ("NA" if self.max_coord_delta < 0 else _value_text(
                       round(float(self.max_coord_delta), 12))),
                   self.lost_tags, self.audit_errors, self.bytes_identical))


def _tag_stream(document: Any) -> list:
    """全局标签流（组码 + 去空白后的值），按出现顺序 —— `lost_tags` 的对位依据。"""
    stream = []
    for tag in document.iter_all_tags():
        stream.append((int(tag.code), str(tag.value).strip()))
    return stream


def _geom_components(entity: Any) -> "list[float]":
    """一个实体的全部几何分量（点 x/y/z、半径、起止角）；取不到就是空表。"""
    out: list[float] = []
    for point in entity.points() or []:
        for value in point:
            if value is not None:
                out.append(float(value))
    radius = entity.radius()
    if radius is not None:
        out.append(float(radius))
    angles = entity.angles()
    if angles:
        out.extend(float(value) for value in angles)
    return out


def compare_documents(document: Any, other: Any, *, tolerance: float = 1e-9) -> FidelityReport:
    """两份文档的保真对照（`roundtrip` 的核；也供尺子/门直接对拍"改前 vs 改后"）。"""
    report = FidelityReport(tolerance=float(tolerance))
    report.entities_in = int(document.entity_count())
    report.entities_out = int(other.entity_count())
    children_in = int(document.entity_count(include_children=True))
    children_out = int(other.entity_count(include_children=True))
    report.entities_conserved = 1 if (report.entities_in == report.entities_out
                                      and children_in == children_out) else 0

    report.type_counts_in = dict(document.entity_type_counts())
    report.type_counts_out = dict(other.entity_type_counts())
    report.type_counts_match = 1 if report.type_counts_in == report.type_counts_out else 0

    lost = 0
    for left, right in zip_longest(_tag_stream(document), _tag_stream(other)):
        if left != right:
            lost += 1
    report.lost_tags = int(lost)

    delta = 0.0
    measured = False
    left_entities = list(document.entities())
    right_entities = list(other.entities())
    for left, right in zip(left_entities, right_entities):
        try:
            a = _geom_components(left)
            b = _geom_components(right)
        except Exception as exc:  # noqa: BLE001 - 取不出分量是审计失败，不许静默当 0
            report.audit_errors += 1
            report.detail.append(f"geometry comparison failed on entity {left.type!r}: "
                                 f"{type(exc).__name__}: {exc}")
            continue
        if not a and not b:
            continue
        if len(a) != len(b):
            report.audit_errors += 1
            report.detail.append(f"geometry component count differs on entity {left.type!r}: "
                                 f"{len(a)} vs {len(b)}")
            continue
        measured = True
        for first, second in zip(a, b):
            delta = max(delta, abs(first - second))
    if measured:
        report.max_coord_delta = float(delta)
    else:
        report.max_coord_delta = -1.0
        report.detail.append("max_coord_delta not measured: no comparable geometry components")

    if document.raw_bytes is not None:
        try:
            report.bytes_identical = 1 if write_dxf(document) == document.raw_bytes else 0
        except Exception as exc:  # noqa: BLE001 - 写不回去 = 审计失败
            report.audit_errors += 1
            report.detail.append(f"re-render failed: {type(exc).__name__}: {exc}")
    else:
        report.detail.append("bytes_identical not measured: the document carries no input bytes")
    return report


def roundtrip(data: bytes, *, tolerance: float = 1e-9) -> FidelityReport:
    """字节 → 读 → 写 → 再读，给出五项保真账。

    `packages/dxf` 缺席 ⇒ `DependencyMissing` 原样抛出（显式降级信号，不吞）；
    输入读不动（`DxfError`）⇒ 回一份 `audit_errors=1` 的报告（尺子报红，不是崩掉）。
    """
    modules = _dxf_modules()
    payload = bytes(data)
    report = FidelityReport(tolerance=float(tolerance), bytes_in=len(payload),
                            sha256_in=hashlib.sha256(payload).hexdigest())
    try:
        document = read_dxf(payload)
    except Exception as exc:  # noqa: BLE001 - 读不动就是审计失败（判定行口径是"报红"，不是崩溃）
        report.audit_errors += 1
        report.detail.append(f"read_dxf failed: {type(exc).__name__}: {exc}")
        return report
    try:
        rendered = write_dxf(document)
    except Exception as exc:  # noqa: BLE001
        report.audit_errors += 1
        report.detail.append(f"write_dxf failed: {type(exc).__name__}: {exc}")
        return report
    report.bytes_out = len(rendered)
    report.sha256_out = hashlib.sha256(rendered).hexdigest()
    report.bytes_identical = 1 if rendered == payload else 0
    try:
        reread = modules["reader"].parse_bytes(rendered)
    except Exception as exc:  # noqa: BLE001
        report.audit_errors += 1
        report.detail.append(f"read-back failed: {type(exc).__name__}: {exc}")
        return report
    compared = compare_documents(document, reread, tolerance=tolerance)
    compared.bytes_in, compared.bytes_out = report.bytes_in, report.bytes_out
    compared.sha256_in, compared.sha256_out = report.sha256_in, report.sha256_out
    compared.bytes_identical = report.bytes_identical
    compared.audit_errors += report.audit_errors
    compared.detail = list(report.detail) + list(compared.detail)
    return compared
