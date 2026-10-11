"""cadcore.io.plot_config —— 出图打印配置三件套（.pc3 / .ctb / .stb）的真读写器。

为什么有这一件
--------------
ops/output.py 的 configure_plotter / set_plot_style_table 此前如实抛
DependencyMissing(reason="dependency_missing")：本构建没有 .pc3 读写器、也没有 .ctb/.stb
解析器，打印配置只能用内存记录记账 ⇒ 无法按标准线宽/灰度出图（对标件
docs/research/cad-parity/SCOPE.md D2 记为「未验收（无解析器）」）。本模块把这三个格式的
**真解析器 + 真写出器**落到唯一真源里：读头、版本、颜色表项、线宽/线型映射全部真读真写，
不占位、不静默降级。

三个格式的落盘形态（本模块的实现口径，全部能被 dumps → loads 逐字段现证）
----------------------------------------------------------------------------
1) .ctb / .stb（二进制容器 + 文本载荷）
   - 头 60 字节 = 48 字节 ASCII 魔数
     'PIAFILEVERSION_2.0,<CTBVER1|STBVER1>,compress' + CRLF + 'pmzlibcodec'
     + 3 个小端 uint32：adler32(压缩体) / 未压缩载荷字节数 / 压缩体字节数；
   - 其后是 zlib 流；解压后的载荷是文本，**末字节为 NUL**；
   - 载荷语法：顶层 name=value 与 name{...} 块（aci_table / plot_style /
     custom_lineweight_table）；plot_style 块内每条样式是 <index>{...} 子块；
     字符串值写作「开头一个双引号、行尾即结束」（容器自身的历史约定，例如 name="Color_1）。
   该容器布局与 ezdxf.addons.acadctb（MIT）一致 —— 它是本仓
   docs/research/cad-base-selection/units/ezdxf.engineering.md 认定的 CTB/STB 参考实现；
   门脚本的 --xref 腿把它当**第二把尺子**（两把尺子互读得出来才算真解析）。
   写侧一律 struct.pack("<LLL", ...) 钉死 12 字节头：参考实现在 LP64 平台用 native LLL
   会写成 24 字节（它自己的读侧按 60 偏移读 ⇒ 自读失败），那是它的平台缺陷；真件（Windows
   生成）是 12 字节，故 zlib 流偏移恒为 60。
2) .pc3（文本）
   - [Section] + Key=Value；已知段 [Version] / [Plotter] / [Lineweights] /
     [Linetypes] / [ColorTable]；**未知段原样保留**（extra_sections）并在写回时逐行重现；
   - 色表项文法：
     Color<ACI>=<color>;LW:<lineweight 表下标>;LT:<linetype 表下标>;SCREEN:<0..100>;DITHER:<0|1>;GRAY:<0|1>
     其中 <color> ∈ OBJECT / LAYER / BLOCK / RGB:r,g,b / ACI:n（必填项之外的键缺省即默认值）；
   - 读侧额外认同族 PIA 容器（版本 token 以 PC3 开头时先解包、再按同一文本 schema 解析），
     写出时保持读入时的容器形态（读到文本写文本、读到容器写容器）。

口径（与本仓 AGENTS E2.3 一致：0 = 真为零 / -1 = 未采 / NA = 不适用）
--------------------------------------------------------------------
- **读不到就抛**：坏魔数 / 截断 / 校验不符 / 长度不符 / 语法错 / 缺必填键 / 字段越界，
  一律 PlotConfigError(reason=...)，**绝不**返回「空表 + 默认值」。
- **没采到不装 0**：容器头里的 adler 与长度允许两种记账基准（本模块写出用压缩体 adler +
  字节长度；读侧两种都认并**记录**用了哪种，见 ContainerInfo.adler_basis / length_basis），
  两种都对不上才抛；未采字段记 -1 / NA。
- **不丢字段**：CTB/STB 顶层未知项与样式项内未知键、PC3 未知段一律原样保留并重现，
  因此 dumps(loads(raw)) 是不动点（canonical fixpoint，见 dumps docstring）。
- **不静默补默认**：顶层 plot_style / scale_factor、样式项 name / color /
  lineweight / linetype、PC3 的 [Version]/Version/[Plotter]/Device 都是必填；
  真缺了就抛。允许缺省的可选项会把名字记进 defaulted_fields，判定行看得见「补过哪些」。

用法
----
    from cadcore.io import plot_config as pc
    cfg = pc.new_ctb(description="office standard")
    data = pc.dumps(cfg)                 # 真字节（容器 + zlib）
    back = pc.loads(data)                # 逐字段一致
    back.to_dict() == cfg.to_dict()      # True

    # 路径用相对名：本项目的跨平台门（G7）把字符串常量里的 POSIX 绝对路径计为 posix_paths
    pc.save(pc.new_pc3(device="DWG To PDF.pc3"), "office.pc3")
    pc.load("office.pc3").device         # 'DWG To PDF.pc3'

只读保证：本模块不写仓库内文件（save 只写调用方给的路径）。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..errors import CadError

__all__ = [
    "PlotConfigError",
    "ContainerInfo",
    "PlotStyle",
    "PlotStyleTable",
    "ColorMapping",
    "PlotterConfig",
    "PlotConfig",
    "load",
    "loads",
    "save",
    "dumps",
    "new_ctb",
    "new_stb",
    "new_pc3",
    "default_lineweights",
    "color_rgb",
    "pack_mode_color",
    "int2color",
    "wrap_container",
    "unwrap_container",
    "container_tag_for",
    "kind_from_tag",
    "parse_style_table",
    "parse_pc3",
    "COLOR_OBJECT",
    "COLOR_BY_LAYER",
    "COLOR_BY_BLOCK",
    "COLOR_RGB",
    "COLOR_ACI",
    "DITHERING_ON",
    "GRAYSCALE_ON",
    "NAMED_COLOR",
    "LINETYPE_AUTOMATIC",
    "LINETYPE_OBJECT",
    "STYLE_COUNT",
]

# ── 常量：容器 / 颜色打包 / 线型 / 默认线宽表 ────────────────────────────────────────────
MAGIC_PREFIX = b"PIAFILEVERSION_2.0,"
MAGIC_SUFFIX = b",compress\r\npmzlibcodec"
VERSION_TAG_BYTES = 7                                          # CTBVER1 / STBVER1 / PC3VER1
MAGIC_BYTES = len(MAGIC_PREFIX) + VERSION_TAG_BYTES + len(MAGIC_SUFFIX)   # 48
CONTAINER_HEADER_BYTES = 12                                    # 3 个小端 uint32
CONTAINER_PREFIX_BYTES = MAGIC_BYTES + CONTAINER_HEADER_BYTES  # 60
CTB_TAG = "CTBVER1"
STB_TAG = "STBVER1"
KINDS = ("pc3", "ctb", "stb")

#: 颜色打包口径（与参考实现一致）：高字节是 color_type，低三字节是 R/G/B 或 ACI。
COLOR_BY_LAYER = 0xC0
COLOR_BY_BLOCK = 0xC1
COLOR_RGB = 0xC2
COLOR_ACI = 0xC3
COLOR_OBJECT = -1
COLOR_OBJECT2 = -1006632961

#: color_policy 位。
DITHERING_ON = 1
GRAYSCALE_ON = 2
NAMED_COLOR = 4

#: 线型：0 = 自动，31 = 随对象；其余 1..30 是打印样式线型表下标。
LINETYPE_AUTOMATIC = 0
LINETYPE_OBJECT = 31

#: CTB 的颜色表项条数（ACI 1..255）。
STYLE_COUNT = 255

#: 参考实现（ezdxf.addons.acadctb，MIT）的缺省线宽表，单位 mm；索引 0 = 随对象。
_DEFAULT_LINEWEIGHTS: tuple[float, ...] = (
    0.00, 0.05, 0.09, 0.10, 0.13, 0.15, 0.18, 0.20, 0.25, 0.30,
    0.35, 0.40, 0.45, 0.50, 0.53, 0.60, 0.65, 0.70, 0.80, 0.90,
    1.00, 1.06, 1.20, 1.40, 1.58, 2.00, 2.11,
)

END_STYLE_BUTT, END_STYLE_SQUARE, END_STYLE_ROUND = 0, 1, 2
END_STYLE_DIAMOND, END_STYLE_OBJECT = 3, 4
JOIN_STYLE_MITER, JOIN_STYLE_BEVEL, JOIN_STYLE_ROUND = 0, 1, 2
JOIN_STYLE_DIAMOND, JOIN_STYLE_OBJECT = 3, 5
FILL_STYLE_SOLID, FILL_STYLE_OBJECT = 64, 73

#: 顶层必填项 / 可缺省项（缺省时把名字记进 defaulted_fields，判定行可辨「补过哪些」）。
_STYLE_TABLE_REQUIRED = ("plot_style", "scale_factor")
_STYLE_TABLE_OPTIONAL = ("description", "aci_table_available", "apply_factor",
                         "custom_lineweight_display_units", "aci_table",
                         "custom_lineweight_table")
_STYLE_ENTRY_REQUIRED = ("name", "color", "lineweight", "linetype")
_PC3_KNOWN_SECTIONS = ("Version", "Plotter", "Lineweights", "Linetypes", "ColorTable")
_PC3_REQUIRED_SECTIONS = ("Version", "Plotter")


def default_lineweights() -> tuple[float, ...]:
    """参考实现的缺省线宽表（mm）的副本；索引 0 = 随对象。"""
    return tuple(_DEFAULT_LINEWEIGHTS)


class PlotConfigError(CadError):
    """打印配置读写的可预期错误：带机器可读 reason，不吞、不降级。"""

    default_reason = "plot_config_error"


def _fail(reason: str, message: str, **details: Any) -> PlotConfigError:
    return PlotConfigError(message, reason=reason, **details)


# ── 结果载体 ────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ContainerInfo:
    """容器读到的头部事实（未采字段一律 -1 / NA，不装 0）。"""

    container: str = "text"          # "text" | "pmzlibcodec"
    version_tag: str = ""            # "CTBVER1" / "STBVER1" / "PC3VER1" / ""
    adler32: int = -1
    plain_len: int = -1
    comp_len: int = -1
    adler_basis: str = "NA"          # "compressed" | "plain" | "NA"
    length_basis: str = "NA"         # "bytes" | "chars" | "NA"

    def to_dict(self) -> dict[str, Any]:
        return {"container": self.container, "version_tag": self.version_tag,
                "adler32": self.adler32, "plain_len": self.plain_len,
                "comp_len": self.comp_len, "adler_basis": self.adler_basis,
                "length_basis": self.length_basis}


@dataclass(frozen=True)
class PlotStyle:
    """.ctb/.stb 的一条打印样式（颜色 + 线宽 + 线型 + 淡显 + 端点/连接/填充）。"""

    index: int
    name: str
    localized_name: str = ""
    description: str = ""
    color: int = COLOR_OBJECT
    mode_color: int | None = None
    color_policy: int = DITHERING_ON
    physical_pen_number: int = 0
    virtual_pen_number: int = 0
    screen: int = 100
    linepattern_size: float = 0.5
    linetype: int = LINETYPE_OBJECT
    adaptive_linetype: bool = True
    lineweight: int = 0
    fill_style: int = FILL_STYLE_OBJECT
    end_style: int = END_STYLE_OBJECT
    join_style: int = JOIN_STYLE_OBJECT
    #: 该项内本模块不认识的键的原文行（原样保留、原样重现）。
    extra: tuple[str, ...] = ()

    # -- 颜色 --
    def has_object_color(self) -> bool:
        return self.color in (COLOR_OBJECT, COLOR_OBJECT2)

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        """样式的 RGB；随对象/随图层/随块颜色时返回 None。"""
        if self.has_object_color():
            return None
        packed = self.mode_color if self.mode_color is not None else self.color
        return int2color(packed)[:3]

    @property
    def color_type(self) -> int | None:
        if self.has_object_color():
            return None
        packed = self.mode_color if self.mode_color is not None else self.color
        return int2color(packed)[3]

    # -- color_policy 位 --
    @property
    def dithering(self) -> bool:
        return bool(self.color_policy & DITHERING_ON)

    @property
    def grayscale(self) -> bool:
        return bool(self.color_policy & GRAYSCALE_ON)

    @property
    def named_color(self) -> bool:
        return bool(self.color_policy & NAMED_COLOR)

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name, "localized_name": self.localized_name,
                "description": self.description, "color": self.color,
                "mode_color": self.mode_color, "color_policy": self.color_policy,
                "physical_pen_number": self.physical_pen_number,
                "virtual_pen_number": self.virtual_pen_number, "screen": self.screen,
                "linepattern_size": self.linepattern_size, "linetype": self.linetype,
                "adaptive_linetype": self.adaptive_linetype, "lineweight": self.lineweight,
                "fill_style": self.fill_style, "end_style": self.end_style,
                "join_style": self.join_style, "extra": list(self.extra)}


@dataclass(frozen=True)
class PlotStyleTable:
    """.ctb（按 ACI 索引）或 .stb（按命名）打印样式表。"""

    kind: str
    container: ContainerInfo = field(default_factory=ContainerInfo)
    description: str = ""
    scale_factor: float = 1.0
    apply_factor: bool = False
    custom_lineweight_display_units: int = 0
    aci_table_available: bool = False
    lineweights: tuple[float, ...] = field(default_factory=default_lineweights)
    aci_names: tuple[str, ...] = ()
    styles: tuple[PlotStyle, ...] = ()
    extra: tuple[str, ...] = ()
    #: 允许缺省、且文件里真缺、由本模块补上的顶层项（没补就是空）。
    defaulted_fields: tuple[str, ...] = ()

    # -- 查表 --
    def style_for_aci(self, aci: int) -> PlotStyle:
        """按 ACI（1..255）取样式；越界抛 reason=index_out_of_range。"""
        if self.kind != "ctb":
            raise _fail("unsupported_kind", "named table has no ACI index", kind=self.kind,
                        aci=aci)
        if not 1 <= int(aci) <= STYLE_COUNT:
            raise _fail("index_out_of_range", "ACI out of range", aci=int(aci))
        wanted = int(aci) - 1
        for style in self.styles:
            if style.index == wanted:
                return style
        raise _fail("index_out_of_range", "no style entry for ACI", aci=int(aci))

    def style_by_name(self, name: str) -> PlotStyle:
        """按样式名取样式（大小写敏感，与文件一致）。"""
        for style in self.styles:
            if style.name == str(name):
                return style
        raise _fail("unknown_style", "no style with that name", name=str(name))

    def lineweight_mm(self, style: PlotStyle | int) -> float:
        """样式的打印线宽（mm）：style.lineweight 是线宽表下标。"""
        index = style.lineweight if isinstance(style, PlotStyle) else int(style)
        if not 0 <= index < len(self.lineweights):
            raise _fail("index_out_of_range", "lineweight index out of range",
                        index=index, table=len(self.lineweights))
        return float(self.lineweights[index])

    def resolve(self, aci: int) -> dict[str, Any]:
        """一条 ACI 的完整打印映射（颜色 / 线宽 mm / 线型 / 淡显 / grayscale）。"""
        style = self.style_for_aci(aci)
        return {"aci": int(aci), "name": style.name, "color": style.color,
                "rgb": style.rgb, "lineweight_index": style.lineweight,
                "lineweight_mm": self.lineweight_mm(style), "linetype": style.linetype,
                "screen": style.screen, "dithering": style.dithering,
                "grayscale": style.grayscale}

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "container": self.container.to_dict(),
                "description": self.description, "scale_factor": self.scale_factor,
                "apply_factor": self.apply_factor,
                "custom_lineweight_display_units": self.custom_lineweight_display_units,
                "aci_table_available": self.aci_table_available,
                "lineweights": [float(item) for item in self.lineweights],
                "aci_names": list(self.aci_names),
                "styles": [item.to_dict() for item in self.styles],
                "extra": list(self.extra),
                "defaulted_fields": list(self.defaulted_fields)}

    def payload_dict(self) -> dict[str, Any]:
        """语义载荷（**不含**容器实测事实）：往返逐字段比对的唯一口径。

        容器里的 adler32 / 长度是"这份文件长什么样"的**测量事实**，不是模型字段；
        拿一个从没落过盘的构造模型去比它只会制造假差异，故比对一律用本方法，
        容器事实另由头解析测试与门的 container 检查负责。
        """
        data = self.to_dict()
        data.pop("container", None)
        return data


@dataclass(frozen=True)
class ColorMapping:
    """PC3 色表的一项：颜色 → 线宽表下标 / 线型表下标 / 淡显 / 抖动 / 灰度。"""

    aci: int
    color: str = "OBJECT"
    lineweight_index: int = 0
    linetype_index: int = 0
    screening: int = 100
    dither: bool = True
    grayscale: bool = False

    def color_rgb(self) -> tuple[int, int, int] | None:
        """RGB:r,g,b → (r, g, b)；其它形态返回 None（表示随对象/随层/随块/按 ACI）。"""
        if not self.color.upper().startswith("RGB:"):
            return None
        parts = self.color.split(":", 1)[1].split(",")
        if len(parts) != 3:
            raise _fail("bad_field", "malformed RGB color", color=self.color)
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    def to_dict(self) -> dict[str, Any]:
        return {"aci": self.aci, "color": self.color,
                "lineweight_index": self.lineweight_index,
                "linetype_index": self.linetype_index, "screening": self.screening,
                "dither": self.dither, "grayscale": self.grayscale}


@dataclass(frozen=True)
class PlotterConfig:
    """.pc3 绘图仪配置：设备 / 出纸口 / 图幅 + 线宽表 + 线型表 + 色表映射。"""

    container: ContainerInfo = field(default_factory=ContainerInfo)
    format_id: str = "PC3-TEXT-1"
    version: str = "4.0"
    device: str = "DWG To PDF.pc3"
    port: str = "file"
    media: tuple[str, ...] = ("A4",)
    default_media: str = "A4"
    lineweights: tuple[float, ...] = field(default_factory=default_lineweights)
    linetypes: tuple[str, ...] = ("CONTINUOUS",)
    color_map: tuple[ColorMapping, ...] = ()
    extra_sections: tuple[str, ...] = ()
    defaulted_fields: tuple[str, ...] = ()

    def mapping_for_aci(self, aci: int) -> ColorMapping:
        for item in self.color_map:
            if item.aci == int(aci):
                return item
        raise _fail("index_out_of_range", "no color table entry for ACI", aci=int(aci))

    def lineweight_mm(self, mapping: ColorMapping) -> float:
        index = int(mapping.lineweight_index)
        if not 0 <= index < len(self.lineweights):
            raise _fail("index_out_of_range", "lineweight index out of range",
                        index=index, table=len(self.lineweights))
        return float(self.lineweights[index])

    def linetype_name(self, mapping: ColorMapping) -> str:
        index = int(mapping.linetype_index)
        if not 0 <= index < len(self.linetypes):
            raise _fail("index_out_of_range", "linetype index out of range",
                        index=index, table=len(self.linetypes))
        return str(self.linetypes[index])

    def validate(self) -> None:
        """结构自检：设备名/图幅/索引越界/ACI 唯一性 —— 建对象时和写出前都跑。"""
        if not self.device.lower().endswith(".pc3"):
            raise _fail("bad_field", "device must be a .pc3 name", device=self.device)
        if not self.media:
            raise _fail("bad_field", "media list must not be empty")
        if self.default_media not in self.media:
            raise _fail("bad_field", "default media is not in the media list",
                        default_media=self.default_media, media=list(self.media))
        if not self.lineweights:
            raise _fail("bad_field", "lineweight table must not be empty")
        if not self.linetypes:
            raise _fail("bad_field", "linetype table must not be empty")
        seen: set[int] = set()
        for item in self.color_map:
            if not 1 <= item.aci <= STYLE_COUNT:
                raise _fail("index_out_of_range", "ACI out of range", aci=item.aci)
            if item.aci in seen:
                raise _fail("bad_field", "duplicate ACI in color table", aci=item.aci)
            seen.add(item.aci)
            if not 0 <= item.lineweight_index < len(self.lineweights):
                raise _fail("index_out_of_range", "lineweight index out of range",
                            aci=item.aci, index=item.lineweight_index)
            if not 0 <= item.linetype_index < len(self.linetypes):
                raise _fail("index_out_of_range", "linetype index out of range",
                            aci=item.aci, index=item.linetype_index)
            if not 0 <= item.screening <= 100:
                raise _fail("bad_field", "screening must be within 0..100",
                            aci=item.aci, screening=item.screening)
            rgb = item.color_rgb()
            if rgb is not None:
                for channel in rgb:
                    if not 0 <= channel <= 255:
                        raise _fail("bad_field", "RGB channel out of range", aci=item.aci,
                                    color=item.color)

    @property
    def kind(self) -> str:
        return "pc3"

    def payload_dict(self) -> dict[str, Any]:
        """语义载荷（**不含**容器实测事实）：往返逐字段比对的唯一口径（同 PlotStyleTable）。"""
        data = self.to_dict()
        data.pop("container", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "pc3", "container": self.container.to_dict(),
                "format_id": self.format_id, "version": self.version,
                "device": self.device, "port": self.port, "media": list(self.media),
                "default_media": self.default_media,
                "lineweights": [float(item) for item in self.lineweights],
                "linetypes": [str(item) for item in self.linetypes],
                "color_map": [item.to_dict() for item in self.color_map],
                "extra_sections": list(self.extra_sections),
                "defaulted_fields": list(self.defaulted_fields)}


PlotConfig = PlotStyleTable | PlotterConfig


# ── 颜色打包小工具（与参考实现同口径）────────────────────────────────────────────────────
def int2color(value: int) -> tuple[int, int, int, int]:
    """打包整数 → (r, g, b, color_type)。"""
    packed = int(value) & 0xFFFFFFFF
    return ((packed & 0xFF0000) >> 16, (packed & 0xFF00) >> 8, packed & 0xFF,
            (packed & 0xFF000000) >> 24)


def color_rgb(rgb: Sequence[int], *, color_type: int = COLOR_RGB) -> int:
    """(r, g, b) + color_type → 打包整数（负值形态，与参考实现一致）。"""
    red, green, blue = (int(item) for item in rgb)
    for name, channel in (("r", red), ("g", green), ("b", blue)):
        if not 0 <= channel <= 255:
            raise _fail("bad_field", "RGB channel out of range", channel=name, value=channel)
    packed = ((int(color_type) << 24) + (red << 16) + (green << 8) + blue) & 0xFFFFFFFF
    # 参考实现存二补码负形态（其 mode_color2int）；int2color 用 & 0xFFFFFFFF 还原字节。
    return packed - 0x100000000 if packed else 0


def pack_mode_color(red: int, green: int, blue: int, *, color_type: int = COLOR_RGB) -> int:
    """mode_color 的打包形态（color_rgb 的同义入口，便于读侧对照）。"""
    return color_rgb((red, green, blue), color_type=color_type)


# ── 文本载荷语法：TOP-LEVEL items（值 or 块），保留原文行 ────────────────────────────────
@dataclass(frozen=True)
class _Item:
    name: str
    value: Any                      # str | dict[str, _Item]
    lines: tuple[str, ...]          # 原文行（含 { / } 行）


def _lines_of(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.split("\n")]


def _parse_items(lines: Sequence[str], *, where: str) -> list[_Item]:
    """解析 name=value / name{...} 列表；语法错一律抛 reason=bad_syntax。"""
    items: list[_Item] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        if stripped == "}":
            raise _fail("bad_syntax", "unbalanced '}'", where=where, line=index + 1)
        if stripped.endswith("{"):
            name = stripped[:-1].strip()
            if not name:
                raise _fail("bad_syntax", "block without a name", where=where, line=index + 1)
            start = index
            depth = 1
            index += 1
            while index < len(lines) and depth:
                probe = lines[index].strip()
                if probe.endswith("{"):
                    depth += 1
                elif probe == "}":
                    depth -= 1
                elif probe and "=" not in probe:
                    raise _fail("bad_syntax", "line is neither 'name=value' nor a block",
                                where=where, line=index + 1, text=probe[:80])
                index += 1
            if depth:
                raise _fail("bad_syntax", "unclosed block", where=where, name=name)
            body_lines = list(lines[start + 1:index - 1])
            body = _items_to_map(_parse_items(body_lines, where=where), where=where)
            items.append(_Item(name, body, tuple(lines[start:index])))
            continue
        if "=" not in stripped:
            raise _fail("bad_syntax", "line is neither 'name=value' nor a block",
                        where=where, line=index + 1, text=stripped[:80])
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not name:
            raise _fail("bad_syntax", "empty key", where=where, line=index + 1)
        items.append(_Item(name, value.strip(), (raw,)))
        index += 1
    return items


def _items_to_map(items: Iterable[_Item], *, where: str) -> dict[str, _Item]:
    out: dict[str, _Item] = {}
    for item in items:
        if item.name in out:
            raise _fail("bad_syntax", "duplicate key", where=where, key=item.name)
        out[item.name] = item
    return out


def _unquote(value: str) -> str:
    """容器里的字符串约定：值以双引号开头，行尾即结束。"""
    text = str(value)
    if text.startswith('"'):
        return text[1:]
    return text


def _as_int(value: Any, *, where: str, key: str) -> int:
    if isinstance(value, dict):
        raise _fail("bad_field", "expected a scalar, got a block", where=where, key=key)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise _fail("bad_field", "expected an integer", where=where, key=key,
                    value=str(value)[:60]) from exc


def _as_float(value: Any, *, where: str, key: str) -> float:
    if isinstance(value, dict):
        raise _fail("bad_field", "expected a scalar, got a block", where=where, key=key)
    text = str(value).strip()
    # 真件里偶见 "0.00762 (+7.Z..." 这类尾随注释：只取第一个 token，且必须能解析成数。
    head = text.split(" ", 1)[0]
    try:
        return float(head)
    except (TypeError, ValueError) as exc:
        raise _fail("bad_field", "expected a number", where=where, key=key,
                    value=text[:60]) from exc


def _as_bool(value: Any, *, where: str, key: str) -> bool:
    text = str(value).strip().upper()
    if text == "TRUE":
        return True
    if text == "FALSE":
        return False
    raise _fail("bad_field", "expected TRUE/FALSE", where=where, key=key, value=text[:20])


def _fmt_float(value: float) -> str:
    """最短往返表示（repr）：保证 float(text) == value，不引入精度漂移。"""
    return repr(float(value))


# ── 容器（.ctb/.stb 的 60 字节头 + zlib）────────────────────────────────────────────────
def wrap_container(payload: str, tag: str) -> bytes:
    """文本载荷 → 容器字节（12 字节小端头；写出侧的唯一定义）。"""
    tag_bytes = str(tag).encode("ascii")
    if len(tag_bytes) != VERSION_TAG_BYTES:
        raise _fail("bad_field", "version tag has an unexpected length", tag=str(tag))
    magic = MAGIC_PREFIX + tag_bytes + MAGIC_SUFFIX
    body = payload.encode("utf-8") + b"\x00"
    comp = zlib.compress(body)
    header = struct.pack("<LLL", zlib.adler32(comp), len(body), len(comp))
    return magic + header + comp


def unwrap_container(raw: bytes) -> tuple[str, bytes, ContainerInfo]:
    """容器字节 → (version_tag, 载荷字节（去掉尾 NUL）, ContainerInfo)；畸形一律抛。"""
    if len(raw) < CONTAINER_PREFIX_BYTES:
        raise _fail("truncated", "container shorter than its 60-byte prefix",
                    size=len(raw), need=CONTAINER_PREFIX_BYTES)
    magic = raw[:MAGIC_BYTES]
    if not magic.startswith(MAGIC_PREFIX) or not magic.endswith(MAGIC_SUFFIX):
        raise _fail("bad_magic", "not a PIA container", head=magic[:16].hex())
    try:
        tag = magic[len(MAGIC_PREFIX):len(magic) - len(MAGIC_SUFFIX)].decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise _fail("bad_magic", "version tag is not ASCII", head=magic[:16].hex()) from exc
    adler, plain_len, comp_len = struct.unpack("<LLL", raw[MAGIC_BYTES:CONTAINER_PREFIX_BYTES])
    if comp_len <= 0:
        raise _fail("length_mismatch", "stored compressed length is not positive",
                    comp_len=comp_len)
    if CONTAINER_PREFIX_BYTES + comp_len > len(raw):
        raise _fail("length_mismatch", "file is shorter than the stored compressed length",
                    file=len(raw), need=CONTAINER_PREFIX_BYTES + comp_len)
    if CONTAINER_PREFIX_BYTES + comp_len != len(raw):
        raise _fail("trailing_data", "bytes after the compressed stream",
                    trailing=len(raw) - CONTAINER_PREFIX_BYTES - comp_len)
    comp = raw[CONTAINER_PREFIX_BYTES:CONTAINER_PREFIX_BYTES + comp_len]
    try:
        plain = zlib.decompress(comp)
    except zlib.error as exc:
        raise _fail("decompress_failed", "zlib could not inflate the payload",
                    detail=str(exc)[:120]) from exc
    if not plain.endswith(b"\x00"):
        raise _fail("missing_terminator", "payload does not end with the NUL terminator",
                    tail=plain[-4:].hex())
    # adler：写出侧用「压缩体」基准；读侧两种基准都认，并**记录**用了哪种（不装 0）。
    adler_basis = "NA"
    if adler == zlib.adler32(comp):
        adler_basis = "compressed"
    elif adler == zlib.adler32(plain):
        adler_basis = "plain"
    else:
        raise _fail("checksum_mismatch", "stored adler32 matches neither basis",
                    stored=adler, compressed=zlib.adler32(comp), plain=zlib.adler32(plain))
    # 长度：写出侧记字节数；参考实现记字符数（ASCII 时两者相等）——两种都认并记录。
    length_basis = "NA"
    if plain_len == len(plain):
        length_basis = "bytes"
    else:
        try:
            chars = len(plain.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise _fail("length_mismatch", "stored length matches neither bytes nor chars",
                        stored=plain_len, size=len(plain)) from exc
        if plain_len == chars:
            length_basis = "chars"
        else:
            raise _fail("length_mismatch", "stored length matches neither bytes nor chars",
                        stored=plain_len, size=len(plain), chars=chars)
    info = ContainerInfo(container="pmzlibcodec", version_tag=tag, adler32=adler,
                         plain_len=plain_len, comp_len=comp_len, adler_basis=adler_basis,
                         length_basis=length_basis)
    return tag, plain[:-1], info


def container_tag_for(kind: str) -> str:
    if kind == "ctb":
        return CTB_TAG
    if kind == "stb":
        return STB_TAG
    if kind == "pc3":
        return "PC3VER1"
    raise _fail("unknown_kind", "unknown plot config kind", kind=str(kind))


def kind_from_tag(tag: str) -> str:
    text = str(tag).upper()
    for prefix, kind in (("CTB", "ctb"), ("STB", "stb"), ("PC3", "pc3")):
        if text.startswith(prefix):
            return kind
    raise _fail("unknown_version_tag", "unknown container version tag", tag=str(tag))


# ── .ctb / .stb：文本载荷 ↔ 模型 ────────────────────────────────────────────────────────
def _dump_style_table(cfg: PlotStyleTable) -> str:
    lines: list[str] = []
    lines.append('description="%s' % cfg.description)
    lines.append("aci_table_available=%s" % ("TRUE" if cfg.aci_table_available else "FALSE"))
    lines.append("scale_factor=%s" % _fmt_float(cfg.scale_factor))
    lines.append("apply_factor=%s" % ("TRUE" if cfg.apply_factor else "FALSE"))
    lines.append("custom_lineweight_display_units=%d" % cfg.custom_lineweight_display_units)
    if cfg.aci_names:
        lines.append("aci_table{")
        for index, name in enumerate(cfg.aci_names):
            lines.append(' %d="%s' % (index, name))
        lines.append("}")
    lines.append("plot_style{")
    for style in cfg.styles:
        lines.append(" %d{" % style.index)
        lines.append('  name="%s' % style.name)
        lines.append('  localized_name="%s' % (style.localized_name or style.name))
        lines.append('  description="%s' % style.description)
        lines.append("  color=%d" % style.color)
        if not style.has_object_color():
            mode = style.color if style.mode_color is None else style.mode_color
            lines.append("  mode_color=%d" % mode)
        lines.append("  color_policy=%d" % style.color_policy)
        lines.append("  physical_pen_number=%d" % style.physical_pen_number)
        lines.append("  virtual_pen_number=%d" % style.virtual_pen_number)
        lines.append("  screen=%d" % style.screen)
        lines.append("  linepattern_size=%s" % _fmt_float(style.linepattern_size))
        lines.append("  linetype=%d" % style.linetype)
        lines.append("  adaptive_linetype=%s" % ("TRUE" if style.adaptive_linetype else "FALSE"))
        lines.append("  lineweight=%d" % style.lineweight)
        lines.append("  fill_style=%d" % style.fill_style)
        lines.append("  end_style=%d" % style.end_style)
        lines.append("  join_style=%d" % style.join_style)
        lines.extend(style.extra)
        lines.append(" }")
    lines.append("}")
    lines.append("custom_lineweight_table{")
    for index, weight in enumerate(cfg.lineweights):
        lines.append(" %d=%s" % (index, _fmt_float(weight)))
    lines.append("}")
    lines.extend(cfg.extra)
    return "\n".join(lines) + "\n"


def _parse_style_entry(index: int, block: Mapping[str, _Item], *, where: str, kind: str,
                       lineweight_count: int) -> PlotStyle:
    data = _items_to_map(block.values(), where=where)
    missing = [key for key in _STYLE_ENTRY_REQUIRED if key not in data]
    if missing:
        raise _fail("missing_key", "style entry is missing required keys", where=where,
                    index=index, missing=missing)
    color = _as_int(data["color"].value, where=where, key="color")
    name = _unquote(str(data["name"].value))
    localized = _unquote(str(data["localized_name"].value)) if "localized_name" in data else name
    description = _unquote(str(data["description"].value)) if "description" in data else ""
    policy = _as_int(data["color_policy"].value, where=where, key="color_policy") \
        if "color_policy" in data else DITHERING_ON
    pen = _as_int(data["physical_pen_number"].value, where=where, key="physical_pen_number") \
        if "physical_pen_number" in data else 0
    vpen = _as_int(data["virtual_pen_number"].value, where=where, key="virtual_pen_number") \
        if "virtual_pen_number" in data else 0
    screen = _as_int(data["screen"].value, where=where, key="screen") if "screen" in data else 100
    if not 0 <= screen <= 100:
        raise _fail("bad_field", "screen must be within 0..100", where=where, value=screen)
    pattern = _as_float(data["linepattern_size"].value, where=where, key="linepattern_size") \
        if "linepattern_size" in data else 0.5
    if pattern < 0:
        raise _fail("bad_field", "linepattern_size must not be negative", where=where,
                    value=pattern)
    linetype = _as_int(data["linetype"].value, where=where, key="linetype")
    if not 0 <= linetype <= LINETYPE_OBJECT:
        raise _fail("bad_field", "linetype index out of range", where=where, value=linetype)
    adaptive = _as_bool(data["adaptive_linetype"].value, where=where, key="adaptive_linetype") \
        if "adaptive_linetype" in data else True
    lineweight = _as_int(data["lineweight"].value, where=where, key="lineweight")
    if not 0 <= lineweight < lineweight_count:
        raise _fail("index_out_of_range", "lineweight index out of range", where=where,
                    index=lineweight, table=lineweight_count)
    fill = _as_int(data["fill_style"].value, where=where, key="fill_style") \
        if "fill_style" in data else FILL_STYLE_OBJECT
    end = _as_int(data["end_style"].value, where=where, key="end_style") \
        if "end_style" in data else END_STYLE_OBJECT
    join = _as_int(data["join_style"].value, where=where, key="join_style") \
        if "join_style" in data else JOIN_STYLE_OBJECT
    known = {"name", "localized_name", "description", "color", "mode_color", "color_policy",
             "physical_pen_number", "virtual_pen_number", "screen", "linepattern_size",
             "linetype", "adaptive_linetype", "lineweight", "fill_style", "end_style",
             "join_style"}
    extra = tuple(line for key in data if key not in known for line in data[key].lines)
    mode: int | None = None
    if color not in (COLOR_OBJECT, COLOR_OBJECT2):
        mode = _as_int(data["mode_color"].value, where=where, key="mode_color") \
            if "mode_color" in data else color
    return PlotStyle(index=int(index), name=name, localized_name=localized,
                     description=description, color=color, mode_color=mode, color_policy=policy,
                     physical_pen_number=pen, virtual_pen_number=vpen, screen=screen,
                     linepattern_size=pattern, linetype=linetype, adaptive_linetype=adaptive,
                     lineweight=lineweight, fill_style=fill, end_style=end, join_style=join,
                     extra=extra)


def parse_style_table(kind: str, payload: bytes, info: ContainerInfo) -> PlotStyleTable:
    """.ctb/.stb 的文本载荷 → PlotStyleTable（缺必填、语法错、越界一律抛）。"""
    where = "payload"
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail("bad_syntax", "payload is not valid UTF-8", where=where) from exc
    top = _items_to_map(_parse_items(_lines_of(text), where=where), where=where)
    missing = [key for key in _STYLE_TABLE_REQUIRED if key not in top]
    if missing:
        raise _fail("missing_key", "payload is missing required item(s)", where=where,
                    missing=missing)
    # aci_table 只对 CTB 有意义：STB 缺它不算"补了默认"。
    defaulted: list[str] = [key for key in _STYLE_TABLE_OPTIONAL
                            if key not in top and not (key == "aci_table" and kind != "ctb")]
    description = _unquote(str(top["description"].value)) if "description" in top else ""
    scale = _as_float(top["scale_factor"].value, where=where, key="scale_factor")
    if scale <= 0:
        raise _fail("bad_field", "scale_factor must be positive", where=where, value=scale)
    apply_factor = _as_bool(top["apply_factor"].value, where=where, key="apply_factor") \
        if "apply_factor" in top else False
    display_units = _as_int(top["custom_lineweight_display_units"].value, where=where,
                            key="custom_lineweight_display_units") \
        if "custom_lineweight_display_units" in top else 0
    aci_flag = _as_bool(top["aci_table_available"].value, where=where, key="aci_table_available") \
        if "aci_table_available" in top else (kind == "ctb")
    aci_names: tuple[str, ...] = ()
    if "aci_table" in top:
        table = top["aci_table"].value
        if not isinstance(table, dict):
            raise _fail("bad_field", "aci_table must be a block", where=where)
        rows = [(_as_int(key, where=where, key="aci_table"), _unquote(str(item.value)))
                for key, item in table.items()]
        rows.sort(key=lambda pair: pair[0])
        if [row[0] for row in rows] != list(range(len(rows))):
            raise _fail("bad_field", "aci_table indices must be contiguous from 0", where=where)
        aci_names = tuple(row[1] for row in rows)
    if "custom_lineweight_table" in top:
        table = top["custom_lineweight_table"].value
        if not isinstance(table, dict):
            raise _fail("bad_field", "custom_lineweight_table must be a block", where=where)
        rows = [(_as_int(key, where=where, key="custom_lineweight_table"),
                 _as_float(item.value, where=where, key="custom_lineweight_table"))
                for key, item in table.items()]
        rows.sort(key=lambda pair: pair[0])
        if [row[0] for row in rows] != list(range(len(rows))):
            raise _fail("bad_field", "lineweight indices must be contiguous from 0", where=where)
        if not rows:
            raise _fail("bad_field", "custom_lineweight_table must not be empty", where=where)
        if any(value < 0 for _, value in rows):
            raise _fail("bad_field", "lineweight must not be negative", where=where)
        lineweights = tuple(value for _, value in rows)
    else:
        lineweights = default_lineweights()
    block = top["plot_style"].value
    if not isinstance(block, dict):
        raise _fail("bad_field", "plot_style must be a block", where=where)
    entries: list[tuple[int, PlotStyle]] = []
    for key, item in block.items():
        index = _as_int(key, where=where, key="plot_style")
        if index < 0 or index >= STYLE_COUNT:
            raise _fail("index_out_of_range", "style index out of range", where=where, index=index)
        if not isinstance(item.value, dict):
            raise _fail("bad_field", "style entry must be a block", where=where, index=index)
        entries.append((index, _parse_style_entry(index, item.value, where=where, kind=kind,
                                                  lineweight_count=len(lineweights))))
    entries.sort(key=lambda pair: pair[0])
    known_top = set(_STYLE_TABLE_REQUIRED) | set(_STYLE_TABLE_OPTIONAL)
    extra = tuple(line for key in top if key not in known_top for line in top[key].lines)
    return PlotStyleTable(kind=kind, container=info, description=description,
                          scale_factor=scale, apply_factor=apply_factor,
                          custom_lineweight_display_units=display_units,
                          aci_table_available=aci_flag, lineweights=lineweights,
                          aci_names=aci_names, styles=tuple(style for _, style in entries),
                          extra=extra, defaulted_fields=tuple(defaulted))


# ── .pc3：文本 ↔ 模型 ───────────────────────────────────────────────────────────────────
def _dump_pc3(cfg: PlotterConfig) -> str:
    cfg.validate()
    lines: list[str] = []
    lines.append("[Version]")
    lines.append("Format=%s" % cfg.format_id)
    lines.append("Version=%s" % cfg.version)
    lines.append("[Plotter]")
    lines.append("Device=%s" % cfg.device)
    lines.append("Port=%s" % cfg.port)
    lines.append("Media=%s" % ",".join(cfg.media))
    lines.append("DefaultMedia=%s" % cfg.default_media)
    lines.append("[Lineweights]")
    for index, weight in enumerate(cfg.lineweights):
        lines.append("%d=%s" % (index, _fmt_float(weight)))
    lines.append("[Linetypes]")
    for index, name in enumerate(cfg.linetypes):
        lines.append("%d=%s" % (index, name))
    lines.append("[ColorTable]")
    for item in cfg.color_map:
        lines.append("Color%d=%s;LW:%d;LT:%d;SCREEN:%d;DITHER:%d;GRAY:%d"
                     % (item.aci, item.color, item.lineweight_index, item.linetype_index,
                        item.screening, 1 if item.dither else 0, 1 if item.grayscale else 0))
    lines.extend(cfg.extra_sections)
    return "\n".join(lines) + "\n"


def _parse_pc3_color(value: str, *, where: str, aci: int) -> ColorMapping:
    parts = [part.strip() for part in str(value).split(";") if part.strip()]
    if not parts:
        raise _fail("bad_field", "empty color table entry", where=where, aci=aci)
    color = parts[0]
    upper = color.upper()
    if upper.startswith("RGB:"):
        channels = upper.split(":", 1)[1].split(",")
        if len(channels) != 3 or any(not part.strip().isdigit() for part in channels):
            raise _fail("bad_field", "malformed RGB color", where=where, aci=aci, color=color)
    elif upper.startswith("ACI:"):
        if not upper.split(":", 1)[1].strip().isdigit():
            raise _fail("bad_field", "malformed ACI color", where=where, aci=aci, color=color)
    elif upper not in ("OBJECT", "LAYER", "BLOCK"):
        raise _fail("bad_field", "unknown color token", where=where, aci=aci, color=color[:40])
    fields = {"LW": 0, "LT": 0, "SCREEN": 100, "DITHER": 1, "GRAY": 0}
    for part in parts[1:]:
        if ":" not in part:
            raise _fail("bad_field", "malformed color table key", where=where, aci=aci,
                        text=part[:40])
        key, raw = part.split(":", 1)
        key = key.strip().upper()
        if key not in fields:
            raise _fail("bad_field", "unknown color table key", where=where, aci=aci, key=key)
        fields[key] = _as_int(raw, where=where, key=key)
    if not 0 <= fields["SCREEN"] <= 100:
        raise _fail("bad_field", "screening must be within 0..100", where=where, aci=aci)
    for key in ("DITHER", "GRAY"):
        if fields[key] not in (0, 1):
            raise _fail("bad_field", "%s must be 0 or 1" % key, where=where, aci=aci)
    return ColorMapping(aci=aci, color=color, lineweight_index=fields["LW"],
                        linetype_index=fields["LT"], screening=fields["SCREEN"],
                        dither=bool(fields["DITHER"]), grayscale=bool(fields["GRAY"]))


def parse_pc3(payload: bytes, info: ContainerInfo) -> PlotterConfig:
    """.pc3 文本 → PlotterConfig（未知段原样保留；缺段/坏行/越界一律抛）。"""
    where = "pc3"
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail("bad_syntax", "pc3 text is not valid UTF-8", where=where) from exc
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for lineno, raw in enumerate(_lines_of(text), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            if not current:
                raise _fail("bad_syntax", "empty section name", where=where, line=lineno)
            if current in sections:
                raise _fail("bad_syntax", "duplicate section", where=where, section=current)
            sections[current] = []
            order.append(current)
            continue
        if current is None:
            raise _fail("bad_syntax", "content before the first [Section]", where=where,
                        line=lineno)
        sections[current].append(raw)
    missing = [name for name in _PC3_REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise _fail("missing_key", "pc3 is missing required section(s)", where=where,
                    missing=missing)

    def key_values(section: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw in sections.get(section, []):
            line = raw.strip()
            if "=" not in line:
                raise _fail("bad_syntax", "section line is not Key=Value", where=where,
                            section=section, text=line[:60])
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                raise _fail("bad_syntax", "empty key", where=where, section=section)
            if key in out:
                raise _fail("bad_syntax", "duplicate key", where=where, section=section, key=key)
            out[key] = value.strip()
        return out

    version_keys = key_values("Version")
    if "Version" not in version_keys:
        raise _fail("missing_key", "pc3 Version section is missing a key", where=where,
                    key="Version")
    format_id = version_keys.get("Format", "PC3-TEXT-1")
    plotter_keys = key_values("Plotter")
    for key in ("Device", "Media"):
        if key not in plotter_keys:
            raise _fail("missing_key", "pc3 Plotter section is missing a key", where=where,
                        key=key)
    device = plotter_keys["Device"]
    if not device.lower().endswith(".pc3"):
        raise _fail("bad_field", "device must be a .pc3 name", where=where, device=device[:60])
    port = plotter_keys.get("Port", "file")
    media = tuple(part.strip() for part in plotter_keys["Media"].split(",") if part.strip())
    if not media:
        raise _fail("bad_field", "media list must not be empty", where=where)
    default_media = plotter_keys.get("DefaultMedia", media[0])
    if default_media not in media:
        raise _fail("bad_field", "default media is not in the media list", where=where,
                    default_media=default_media)

    weights: dict[int, float] = {}
    for key, value in key_values("Lineweights").items():
        weights[_as_int(key, where=where, key="Lineweights")] = \
            _as_float(value, where=where, key="Lineweights")
    if sorted(weights) != list(range(len(weights))) or not weights:
        raise _fail("bad_field", "lineweight indices must be contiguous from 0 and non-empty",
                    where=where, count=len(weights))
    if any(value < 0 for value in weights.values()):
        raise _fail("bad_field", "lineweight must not be negative", where=where)
    lineweights = tuple(weights[index] for index in range(len(weights)))

    types: dict[int, str] = {}
    for key, value in key_values("Linetypes").items():
        types[_as_int(key, where=where, key="Linetypes")] = value
    if sorted(types) != list(range(len(types))) or not types:
        raise _fail("bad_field", "linetype indices must be contiguous from 0 and non-empty",
                    where=where, count=len(types))
    linetypes = tuple(types[index] for index in range(len(types)))

    colors: list[ColorMapping] = []
    for key, value in key_values("ColorTable").items():
        if not key.upper().startswith("COLOR"):
            raise _fail("bad_field", "color table key must be Color<ACI>", where=where,
                        key=key[:40])
        aci = _as_int(key[5:], where=where, key="ColorTable")
        if not 1 <= aci <= STYLE_COUNT:
            raise _fail("index_out_of_range", "ACI out of range", where=where, aci=aci)
        colors.append(_parse_pc3_color(value, where=where, aci=aci))
    colors.sort(key=lambda item: item.aci)

    known = set(_PC3_KNOWN_SECTIONS)
    # 未知段按**整段**（含 [Section] 头行）保留：只留键值行会让它在下一次解析时
    # 落进上一个已知段 ⇒ 往返必须连头一起带上。
    extra_lines: list[str] = []
    for name in order:
        if name not in known:
            extra_lines.append("[%s]" % name)
            extra_lines.extend(sections[name])
    extra_sections = tuple(extra_lines)
    cfg = PlotterConfig(container=info, format_id=format_id, version=version_keys["Version"],
                        device=device, port=port, media=media, default_media=default_media,
                        lineweights=lineweights, linetypes=linetypes,
                        color_map=tuple(colors), extra_sections=extra_sections,
                        defaulted_fields=("Format",) if "Format" not in version_keys else ())
    cfg.validate()
    return cfg


# ── 公共入口：loads / dumps / load / save ──────────────────────────────────────────────
def loads(data: bytes, *, kind: str | None = None) -> PlotConfig:
    """字节 → 模型；kind 只作诊断提示，真实格式**以内容为准**（魔数 / [Section]）。

    畸形输入一律 PlotConfigError：不认识就抛，绝不返回空模型。
    """
    raw = bytes(data)
    if raw.startswith(MAGIC_PREFIX):
        tag, payload, info = unwrap_container(raw)
        detected = kind_from_tag(tag)
        if kind is not None and str(kind).lower() != detected:
            raise _fail("unknown_kind", "container kind disagrees with the requested kind",
                        requested=str(kind), detected=detected, tag=tag)
        if detected == "pc3":
            return parse_pc3(payload, info)
        return parse_style_table(detected, payload, info)
    if raw.lstrip().startswith(b"["):
        info = ContainerInfo(container="text", version_tag="")
        return parse_pc3(raw, info)
    # 带 .ctb/.stb 提示、或文件头明显是 PIA 家族的残骸 ⇒ 报 bad_magic（比 unknown_kind 准确）；
    # 其余情况才是不认识的载荷。诊断只影响 reason，不改变"畸形即抛"的口径。
    hint = str(kind).lower() if kind else ""
    if hint in ("ctb", "stb") or raw[:3] == b"PIA":
        raise _fail("bad_magic", "container magic is missing or damaged",
                    expected=hint or "ctb|stb", head=raw[:16].hex())
    if hint == "pc3":
        raise _fail("bad_syntax", "pc3 text must start with a [Section]",
                    expected="pc3", head=raw[:16].hex())
    raise _fail("unknown_kind", "not a .pc3/.ctb/.stb payload",
                expected=hint or "pc3|ctb|stb", head=raw[:16].hex())


def dumps(cfg: PlotConfig) -> bytes:
    """模型 → 真字节。

    dumps(loads(raw)) 是不动点（顶层未知项/未知段原样重现），
    且对任意构造出的模型 dumps(loads(dumps(cfg))) == dumps(cfg)。
    """
    if isinstance(cfg, PlotStyleTable):
        if cfg.kind not in ("ctb", "stb"):
            raise _fail("unknown_kind", "style table kind must be ctb or stb", kind=str(cfg.kind))
        tag = cfg.container.version_tag or container_tag_for(cfg.kind)
        detected = kind_from_tag(tag)
        if detected != cfg.kind:
            raise _fail("unknown_kind", "container tag disagrees with the table kind",
                        tag=tag, kind=cfg.kind)
        payload = _dump_style_table(cfg)
        if cfg.container.container == "text":
            return payload.encode("utf-8")
        return wrap_container(payload, tag)
    if isinstance(cfg, PlotterConfig):
        payload = _dump_pc3(cfg)
        if cfg.container.container == "pmzlibcodec":
            tag = cfg.container.version_tag or container_tag_for("pc3")
            return wrap_container(payload, tag)
        return payload.encode("utf-8")
    raise _fail("unknown_kind", "not a plot config model", type=type(cfg).__name__)


def load(path: str | Path) -> PlotConfig:
    """文件 → 模型；格式以内容为准，扩展名只进诊断信息。"""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise _fail("read_failed", "could not read the plot config file", path=str(target),
                    detail=str(exc)[:120]) from exc
    suffix = target.suffix.lower().lstrip(".")
    hint = suffix if suffix in KINDS else None
    try:
        return loads(raw, kind=hint)
    except PlotConfigError as exc:
        if "path" not in exc.details:
            exc.details["path"] = str(target)
        raise


def save(cfg: PlotConfig, path: str | Path) -> Path:
    """模型 → 文件（真字节）。父目录必须已存在，缺了就抛（不替调用方造目录）。"""
    target = Path(path)
    if not target.parent.is_dir():
        raise _fail("write_failed", "parent directory does not exist", path=str(target))
    target.write_bytes(dumps(cfg))
    return target


# ── 构造器（缺省即参考实现的缺省，便于测试与门脚本建样本）──────────────────────────────
def _default_style(index: int) -> PlotStyle:
    name = "Color_%d" % (index + 1)
    return PlotStyle(index=index, name=name, localized_name=name)


def _copy_style(style: PlotStyle, index: int) -> PlotStyle:
    return PlotStyle(index=index, name=style.name,
                     localized_name=style.localized_name or style.name,
                     description=style.description, color=style.color,
                     mode_color=style.mode_color, color_policy=style.color_policy,
                     physical_pen_number=style.physical_pen_number,
                     virtual_pen_number=style.virtual_pen_number, screen=style.screen,
                     linepattern_size=style.linepattern_size, linetype=style.linetype,
                     adaptive_linetype=style.adaptive_linetype, lineweight=style.lineweight,
                     fill_style=style.fill_style, end_style=style.end_style,
                     join_style=style.join_style, extra=style.extra)


def new_ctb(*, description: str = "", scale_factor: float = 1.0, apply_factor: bool = False,
            lineweights: Sequence[float] | None = None,
            styles: Sequence[PlotStyle] | None = None) -> PlotStyleTable:
    """一份 STYLE_COUNT 条的 CTB（ACI 1..255）；styles 给了就按 ACI-1 覆盖下标。"""
    weights = tuple(float(item) for item in (lineweights or default_lineweights()))
    rows = [_default_style(index) for index in range(STYLE_COUNT)]
    for style in styles or ():
        if not 0 <= style.index < STYLE_COUNT:
            raise _fail("index_out_of_range", "style index out of range", index=style.index)
        rows[style.index] = style
    return PlotStyleTable(kind="ctb", container=ContainerInfo(container="pmzlibcodec",
                                                              version_tag=CTB_TAG),
                          description=description, scale_factor=float(scale_factor),
                          apply_factor=bool(apply_factor), aci_table_available=True,
                          lineweights=weights, aci_names=tuple(row.name for row in rows),
                          styles=tuple(rows))


def new_stb(*, description: str = "", scale_factor: float = 1.0, apply_factor: bool = False,
            lineweights: Sequence[float] | None = None,
            styles: Sequence[PlotStyle] | None = None) -> PlotStyleTable:
    """一份命名样式表：首条固定 Normal（与参考实现一致），其余按给定顺序。"""
    weights = tuple(float(item) for item in (lineweights or default_lineweights()))
    rows = [PlotStyle(index=0, name="Normal", localized_name="Normal")]
    for offset, style in enumerate(styles or (), 1):
        rows.append(_copy_style(style, offset))
    return PlotStyleTable(kind="stb", container=ContainerInfo(container="pmzlibcodec",
                                                              version_tag=STB_TAG),
                          description=description, scale_factor=float(scale_factor),
                          apply_factor=bool(apply_factor), aci_table_available=False,
                          lineweights=weights, styles=tuple(rows))


def new_pc3(*, device: str = "DWG To PDF.pc3", port: str = "file",
            media: Sequence[str] = ("A4", "A3", "Letter"), default_media: str = "A4",
            lineweights: Sequence[float] | None = None,
            linetypes: Sequence[str] = ("CONTINUOUS", "DASHED", "DASHDOT", "HIDDEN"),
            color_map: Sequence[ColorMapping] | None = None) -> PlotterConfig:
    """一份文本形态的 PC3 绘图仪配置（色表给空就按 7 条常见 ACI 建样本）。"""
    if color_map is None:
        color_map = (
            ColorMapping(aci=1, color="RGB:0,0,0", lineweight_index=8, linetype_index=0,
                         screening=100, dither=True, grayscale=False),
            ColorMapping(aci=2, color="RGB:255,0,0", lineweight_index=8, linetype_index=0,
                         screening=100, dither=True),
            ColorMapping(aci=3, color="RGB:0,255,0", lineweight_index=8, linetype_index=1,
                         screening=100, dither=True),
            ColorMapping(aci=4, color="RGB:0,255,255", lineweight_index=8, linetype_index=1,
                         screening=100, dither=True),
            ColorMapping(aci=5, color="RGB:0,0,255", lineweight_index=8, linetype_index=2,
                         screening=100, dither=True),
            ColorMapping(aci=6, color="RGB:255,0,255", lineweight_index=8, linetype_index=2,
                         screening=100, dither=True),
            ColorMapping(aci=7, color="RGB:255,255,255", lineweight_index=8, linetype_index=0,
                         screening=50, dither=False, grayscale=True),
        )
    cfg = PlotterConfig(container=ContainerInfo(container="text", version_tag=""),
                        device=str(device), port=str(port),
                        media=tuple(str(item) for item in media),
                        default_media=str(default_media),
                        lineweights=tuple(float(item)
                                          for item in (lineweights or default_lineweights())),
                        linetypes=tuple(str(item) for item in linetypes),
                        color_map=tuple(color_map))
    cfg.validate()
    return cfg
