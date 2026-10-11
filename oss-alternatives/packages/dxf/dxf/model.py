"""dxf.model —— DXF 数据模型：`Line` / `Tag` / `Entity` / `Section` / `Table` / `Layer` / `Document`。

设计一句话
----------
**字节保真是模型的第一属性。** 模型里每个标签（`Tag`）都攥着它那两行**原文**
（组码行 + 值行）与各自的**行尾**（``\\r\\n`` / ``\\n`` / ``\\r`` / 无），
所以「读进来再写出去」在没有任何改动时是**逐字节相等**的 —— 不是靠"重新格式化得很像"，
而是靠"根本没重排过"：序列化就是把同一批 `Line` 拼回去。

改一个值只动那一行（``tag.value = …`` 覆盖值行文本），别的行连碰都不碰 ⇒
「修改过的文档只重写被改的实体」是**结构性**成立的，不需要额外保证（见 `Entity.modified`）。

数据模型（与 `docs/design/dxf-format-notes.md` 逐条对应）
--------------------------------------------------------
* `Line`  —— 一条物理行：`text`（不含行尾）+ `newline`（行尾本身）+ `index`（0 基行号）。
* `Tag`   —— DXF 的原子：一个**组码**加一个**值**，各自一行。`value` 是按组码区间解析出的
  Python 值（int/float/bool/str），`raw_value` 是**原文**（``1.0`` 绝不会变成 ``1``）。
* `Entity`—— 一个实体：`type` + `tags` + `raw`（**原文切片**）+ 可选 `children`
  （R12 的 `POLYLINE` → `VERTEX…SEQEND`、`INSERT` → `ATTRIB…SEQEND`）。
* `Section`—— 一个段（`0/SECTION` … `0/ENDSEC` 整段都在 `tags` 里）。认的段：
  `ENTITIES`（解析实体）、`TABLES`（解析表/图层）；其它段（`HEADER`/`BLOCKS`/`OBJECTS`/
  我们不认识的段名）**原样保留、只提供只读访问**。
* `Table` / `Layer` —— `TABLES` 段里的表与 `LAYER` 表的图层行（便利视图，不当真源）。
* `Document` —— 段的有序列表 + `raw_lines` 全文快照 + `raw_bytes` 原字节 + `encoding`。

已知实体（有便利访问器，`KNOWN_ENTITY_TYPES`）
----------------------------------------------
`LINE` / `CIRCLE` / `ARC` / `TEXT` / `POINT` / `LWPOLYLINE` / `POLYLINE` / `VERTEX` / `SEQEND`。
**不在这个集合里的类型一律"只保留不解释"**：`entity.is_unknown` 为真，
`points()` 返回空列表（不猜几何），但原文一字不动地带回去。
`ACAD_PROXY_ENTITY`（代理对象）与组码 ≥ 1000 的 XDATA 属于这一类。

只依赖标准库；本模块**不碰** `packages/vault`。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = [
    "SCHEMA",
    "KNOWN_ENTITY_TYPES",
    "PROXY_ENTITY_TYPE",
    "XDATA_CODE_MIN",
    "APP_ID_CODE",
    "DxfError",
    "Line",
    "Tag",
    "Entity",
    "Section",
    "Table",
    "Layer",
    "Document",
    "code_kind",
    "parse_value",
    "format_value",
    "canonical_json",
    "sha256_text",
]

#: 本模型的 schema 标签（写进 vault 的 `ext.schema`，与 `dxf.index` 共用）
SCHEMA = "dxf-document/1"

#: 有便利访问器的实体类型。**不在这里的 = 只保留不解释。**
KNOWN_ENTITY_TYPES = (
    "LINE",
    "CIRCLE",
    "ARC",
    "TEXT",
    "POINT",
    "LWPOLYLINE",
    "POLYLINE",
    "VERTEX",
    "SEQEND",
)

#: 代理对象（AutoCAD 存"我不认识的第三方对象"用的壳）
PROXY_ENTITY_TYPE = "ACAD_PROXY_ENTITY"

#: XDATA 的组码下界（1000 起）；1001 是**应用名**，其后到下一个 1001 之前都是它的数据
XDATA_CODE_MIN = 1000
APP_ID_CODE = 1001


class DxfError(Exception):
    """DXF 层的可预期错误（坏组码 / 缺值行 / 截断 / 段未闭合 / 编码写不回）。

    真源就是这里（`dxf.reader` 只做 re-export）—— 避免 model ↔ reader 循环 import。
    """


# --------------------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """规范化 JSON：键序固定、紧凑分隔符、不转义非 ASCII（与 fcx-format §3 同款口径）。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fmt_num(value: Any) -> str:
    """给**摘要文本**用的数字格式（不是写回文件的格式 —— 写回一律用 `format_value`）。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return repr(value)
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return "%.10g" % value
    return str(value)


# --------------------------------------------------------------------------------------
# 组码 → 值类型（组码区间表；DXF 规范的事实性接口信息，不抄任何规范正文/表格）
# --------------------------------------------------------------------------------------

_STRING_RANGES = (
    (0, 9), (100, 100), (102, 102), (105, 105), (300, 309), (310, 319), (320, 369),
    (390, 399), (410, 419), (430, 439), (470, 481), (999, 999), (1000, 1009),
)
_INT_RANGES = (
    (60, 79), (90, 99), (160, 169), (170, 179), (270, 289), (370, 389), (400, 409),
    (420, 429), (440, 459), (1060, 1071),
)
_FLOAT_RANGES = ((10, 59), (110, 149), (210, 239), (460, 469), (1010, 1059))
_BOOL_RANGES = ((290, 299),)


def _in_ranges(code: int, ranges) -> bool:
    return any(low <= code <= high for low, high in ranges)


def code_kind(code: int) -> str:
    """组码 → 值类型：`"string" | "int" | "float" | "bool"`。

    **没被区间表覆盖的组码一律按 `"string"` 处理** —— 宁可当字符串原样留着，
    也不猜它的语义（猜错会静默改字节）。
    """
    if not isinstance(code, int) or isinstance(code, bool):
        return "string"
    if _in_ranges(code, _BOOL_RANGES):
        return "bool"
    if code >= XDATA_CODE_MIN:
        if _in_ranges(code, _FLOAT_RANGES):
            return "float"
        if _in_ranges(code, _INT_RANGES):
            return "int"
        return "string"
    if _in_ranges(code, _FLOAT_RANGES):
        return "float"
    if _in_ranges(code, _INT_RANGES):
        return "int"
    if _in_ranges(code, _STRING_RANGES):
        return "string"
    return "string"


def parse_value(code: int, raw_text: str) -> Any:
    """值行原文 → Python 值。

    **解析失败不抛异常**：按组码该是数字却写不出数字时，原样返回 `strip()` 后的字符串
    （内容一个字都不丢；`Tag.parse_error` 里留一条可读说明）。
    """
    text = raw_text.strip()
    kind = code_kind(code)
    if kind == "string":
        return raw_text
    if not text:
        return raw_text
    if kind == "bool":
        try:
            return bool(int(text))
        except ValueError:
            return text
    if kind == "int":
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return text
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            return text
    return raw_text


def _format_float(value: float) -> str:
    """浮点写回文本：``1.0`` 写成 ``1.0``（**不是** ``1``）、``0.5`` 写成 ``0.5``。"""
    if value != value or value in (float("inf"), float("-inf")):
        raise DxfError("不能把 %r 写进 DXF 值行" % (value,))
    text = repr(float(value))
    if float(text) != value:                      # pragma: no cover - repr 总是可往返
        text = "%.17g" % value
    return text


def format_value(code: int, value: Any) -> str:
    """Python 值 → 值行文本（`parse_value` 的逆；只在**改过值**时用到）。"""
    kind = code_kind(code)
    if isinstance(value, str):
        return value
    if kind == "bool":
        return "1" if bool(value) else "0"
    if kind == "int":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return _format_float(value) if value != int(value) else str(int(value))
        raise DxfError("组码 %d 是整数码，写不进 %r" % (code, value))
    if kind == "float":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return _format_float(float(value))
        if isinstance(value, float):
            return _format_float(value)
        raise DxfError("组码 %d 是浮点码，写不进 %r" % (code, value))
    if isinstance(value, (int, float)):
        return _fmt_num(value)
    raise DxfError("组码 %d 是字符串码，写不进 %r" % (code, value))


# --------------------------------------------------------------------------------------
# Line / Tag
# --------------------------------------------------------------------------------------


@dataclass
class Line:
    """一条物理行：`text`（不含行尾）+ `newline`（行尾本身，可能是空串）。"""

    text: str
    newline: str = "\n"
    index: int = -1

    def render(self) -> str:
        return self.text + self.newline

    @property
    def lineno(self) -> int:
        """1 基物理行号（报错用）。"""
        return self.index + 1

    def as_dict(self) -> dict:
        return {"index": self.index, "text": self.text, "newline": self.newline}


@dataclass
class Tag:
    """DXF 的原子：一个组码 + 一个值（各占一行）。

    * `raw`   —— 读入时的原文（组码行 + 值行，含行尾）；**改动后不跟着变**。
    * `render()` —— 当前文本；没人改过时 `render() == raw`。
    * `value` —— 按组码区间解析出的 Python 值；赋值即**只改值行文本**。
    """

    code: int
    code_line: Line
    value_line: Line
    raw: str = ""
    parse_error: "str | None" = None

    def __post_init__(self) -> None:
        if not self.raw:
            self.raw = self.render()
        if self.parse_error is None:
            probe = parse_value(self.code, self.value_line.text)
            if not isinstance(probe, (int, float, bool)) and code_kind(self.code) != "string":
                self.parse_error = "组码 %d 期待 %s，值行 %r 解析不出来（按原文保留）" % (
                    self.code, code_kind(self.code), self.value_line.text.strip())

    # —— 原文 / 现值 ——

    @property
    def raw_code(self) -> str:
        return self.code_line.text

    @property
    def raw_value(self) -> str:
        return self.value_line.text

    def render(self) -> str:
        return self.code_line.render() + self.value_line.render()

    @property
    def modified(self) -> bool:
        return self.render() != self.raw

    @property
    def newline(self) -> str:
        return self.value_line.newline

    # —— 值 ——

    @property
    def value(self) -> Any:
        return parse_value(self.code, self.value_line.text)

    @value.setter
    def value(self, new_value: Any) -> None:
        self.value_line.text = format_value(self.code, new_value)

    def set_value(self, new_value: Any) -> "Tag":
        """链式写法：`tag.set_value(10.0)`；只改值行，组码行与行尾一字不动。"""
        self.value = new_value
        return self

    # —— 分类 ——

    @property
    def is_xdata(self) -> bool:
        """XDATA（组码 ≥ 1000）—— 属于"只保留不解释"那一类。"""
        return self.code >= XDATA_CODE_MIN

    @property
    def is_comment(self) -> bool:
        return self.code == 999

    def as_dict(self) -> dict:
        return {"code": self.code, "value": self.value}

    def raw_dict(self) -> dict:
        """连原文一起给出来（索引里存的是解析值；要取证时用这个）。"""
        return {"code": self.code, "raw_code": self.raw_code,
                "raw_value": self.raw_value, "newline": self.newline}


# --------------------------------------------------------------------------------------
# Entity
# --------------------------------------------------------------------------------------


@dataclass
class Entity:
    """一个实体：`type` + `tags` + `raw`（原文切片）+ 可选 `children`。

    `children` 只用于 R12 的**跟随子实体**：`POLYLINE` 后面跟 `VERTEX…SEQEND`、
    `INSERT` 后面跟 `ATTRIB…SEQEND`。它们仍然是独立可寻址的 `Entity`（原文都在），
    只是挂在父实体上，方便 `points()` 之类的访问器一次取全。
    """

    type: str
    tags: list = field(default_factory=list)
    raw: str = ""
    children: list = field(default_factory=list)
    section: str = "ENTITIES"
    index: int = 0                       # ENTITIES 段里的 1 基序号（顶层实体）
    parent_type: str = ""                # 子实体才有：父实体的 type

    def __post_init__(self) -> None:
        if not self.raw:
            self.raw = self.render()

    # —— 原文 / 现值 ——

    def render(self) -> str:
        return "".join(tag.render() for tag in self.tags)

    @property
    def modified(self) -> bool:
        return self.render() != self.raw

    def iter_tags(self, *, recursive: bool = True) -> Iterator[Tag]:
        for tag in self.tags:
            yield tag
        if recursive:
            for child in self.children:
                for tag in child.iter_tags(recursive=True):
                    yield tag

    def iter_entities(self) -> Iterator["Entity"]:
        """自己 + 全部子实体。"""
        yield self
        for child in self.children:
            yield from child.iter_entities()

    def line_span(self) -> tuple:
        """`(首行 0 基线号, 末行 0 基线号)` —— 本实体在文档里占的物理行区间（闭区间）。"""
        if not self.tags:
            return (-1, -1)
        return (self.tags[0].code_line.index, self.tags[-1].value_line.index)

    # —— 取值 ——

    def get(self, code: int, default: Any = None) -> Any:
        """第一个该组码的值；没有就 `default`。"""
        for tag in self.tags:
            if tag.code == code:
                return tag.value
        return default

    def get_all(self, code: int) -> list:
        return [tag.value for tag in self.tags if tag.code == code]

    def get_tags(self, code: "int | None" = None) -> list:
        if code is None:
            return list(self.tags)
        return [tag for tag in self.tags if tag.code == code]

    def tag(self, code: int) -> "Tag | None":
        for item in self.tags:
            if item.code == code:
                return item
        return None

    @property
    def handle(self) -> str:
        """句柄（组码 5）；没有就空串（R12 里不是每个实体都有）。"""
        value = self.get(5)
        return value.strip() if isinstance(value, str) else ("" if value is None else str(value))

    @property
    def layer(self) -> str:
        """图层名（组码 8）；缺省 `"0"`（DXF 的约定）。"""
        value = self.get(8)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "0"

    @property
    def owner_handle(self) -> str:
        value = self.get(330)
        return value.strip() if isinstance(value, str) else ""

    # —— XDATA / 代理 / 未知 ——

    @property
    def xdata(self) -> list:
        """XDATA 标签（组码 ≥ 1000），按原文顺序。"""
        return [tag for tag in self.tags if tag.is_xdata]

    @property
    def xdata_apps(self) -> list:
        """XDATA 用到的应用名（组码 1001），去重保序。"""
        out: list = []
        for tag in self.tags:
            if tag.code == APP_ID_CODE:
                name = str(tag.value).strip()
                if name and name not in out:
                    out.append(name)
        return out

    @property
    def has_xdata(self) -> bool:
        return any(tag.is_xdata for tag in self.tags)

    @property
    def is_proxy(self) -> bool:
        return self.type.upper() == PROXY_ENTITY_TYPE

    @property
    def is_known(self) -> bool:
        return self.type.upper() in KNOWN_ENTITY_TYPES

    @property
    def is_unknown(self) -> bool:
        return not self.is_known

    # —— 几何便利访问器（只对已知类型有意义；未知类型返回空/None，**不猜**）——

    def _point(self, base_code: int) -> "tuple | None":
        """取 `base/base+10/base+20` 三个组码（缺的补 None）。"""
        x = self.get(base_code)
        y = self.get(base_code + 10)
        if x is None and y is None:
            return None
        z = self.get(base_code + 20)
        try:
            return (float(x), float(y), float(z) if z is not None else None)
        except (TypeError, ValueError):
            return None

    def points(self) -> list:
        """实体关心的点（一律 `(x, y, z|None)`）——**未知类型返回 `[]`**。

        * `LINE`        → `[起点, 终点]`
        * `CIRCLE/ARC`  → `[圆心]`
        * `POINT`       → `[点]`
        * `TEXT`        → `[插入点]`
        * `LWPOLYLINE`  → 各顶点
        * `POLYLINE`    → 各 `VERTEX` 子实体的点
        """
        kind = self.type.upper()
        if kind == "LINE":
            return [p for p in (self._point(10), self._point(11)) if p is not None]
        if kind in ("CIRCLE", "ARC", "POINT", "TEXT"):
            point = self._point(10)
            return [point] if point is not None else []
        if kind == "LWPOLYLINE":
            return self.vertices()
        if kind == "POLYLINE":
            return self.vertices()
        if kind == "VERTEX":
            point = self._point(10)
            return [point] if point is not None else []
        return []

    def start(self) -> "tuple | None":
        return self._point(10)

    def end(self) -> "tuple | None":
        """`LINE` 的终点（组码 11/21/31）；其它类型返回 None。"""
        if self.type.upper() != "LINE":
            return None
        return self._point(11)

    def center(self) -> "tuple | None":
        if self.type.upper() not in ("CIRCLE", "ARC"):
            return None
        return self._point(10)

    def radius(self) -> "float | None":
        """组码 40（`CIRCLE`/`ARC` 的半径）。"""
        value = self.get(40)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def angles(self) -> "tuple | None":
        """`ARC` 的 `(起始角, 终止角)`（组码 50/51，度）。"""
        if self.type.upper() != "ARC":
            return None
        try:
            return (float(self.get(50)), float(self.get(51)))
        except (TypeError, ValueError):
            return None

    def vertices(self) -> list:
        """多段线的顶点列表。

        `LWPOLYLINE`：按组码 10/20 在标签流里的**出现顺序**取（重复组码是有序的）。
        `POLYLINE`  ：取 `VERTEX` 子实体的点（R12 的老写法）。
        """
        kind = self.type.upper()
        if kind == "LWPOLYLINE":
            out: list = []
            pending_x: "float | None" = None
            have_x = False
            for tag in self.tags:
                if tag.code == 10:
                    if have_x and pending_x is not None:
                        out.append((pending_x, 0.0, None))
                    try:
                        pending_x = float(tag.value)
                    except (TypeError, ValueError):
                        pending_x = None
                    have_x = True
                elif tag.code == 20 and have_x:
                    try:
                        y = float(tag.value)
                    except (TypeError, ValueError):
                        y = 0.0
                    out.append((pending_x if pending_x is not None else 0.0, y, None))
                    have_x = False
                    pending_x = None
            if have_x and pending_x is not None:
                out.append((pending_x, 0.0, None))
            return out
        if kind == "POLYLINE":
            out = []
            for child in self.children:
                if child.type.upper() == "VERTEX":
                    point = child._point(10)
                    if point is not None:
                        out.append(point)
            return out
        return []

    def closed(self) -> bool:
        """组码 70 的 bit 1（多段线闭合标志）。"""
        value = self.get(70)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return bool(int(value) & 1)

    def elevation(self) -> "float | None":
        """`LWPOLYLINE` 的标高（组码 38）。"""
        value = self.get(38)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def text(self) -> str:
        """`TEXT` 的内容（组码 1）；不是 TEXT 返回空串。"""
        if self.type.upper() != "TEXT":
            return ""
        value = self.get(1)
        return value if isinstance(value, str) else ("" if value is None else str(value))

    def height(self) -> "float | None":
        value = self.get(40)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # —— 摘要（可检索文本）——

    def _fmt_point(self, point: "tuple | None", *, drop_zero_z: bool = True) -> str:
        if point is None:
            return "?"
        x, y, z = point
        if drop_zero_z and (z is None or z == 0):
            return "%s,%s" % (_fmt_num(x), _fmt_num(y))
        return "%s,%s,%s" % (_fmt_num(x), _fmt_num(y), _fmt_num(z))

    def summary(self) -> str:
        """一行可检索摘要（写进 vault 消息的 `text`）。

        * `LINE 0,0 -> 10,10 [layer=轮廓]`
        * `CIRCLE center=10,20 r=5 [layer=0]`
        * `ARC center=0,0 r=10 a=0->90 [layer=0]`
        * `TEXT "剖面 A-A" at 5,5 [layer=文字]`
        * `MYAPP_NOTE [layer=0] [未知实体 原样保留]`
        """
        kind = self.type.upper()
        layer = " [layer=%s]" % self.layer if self.layer else ""
        if kind == "LINE":
            body = "LINE %s -> %s" % (self._fmt_point(self.start()), self._fmt_point(self.end()))
        elif kind == "CIRCLE":
            radius = self.radius()
            body = "CIRCLE center=%s r=%s" % (
                self._fmt_point(self.center()), _fmt_num(radius) if radius is not None else "?")
        elif kind == "ARC":
            angles = self.angles()
            radius = self.radius()
            body = "ARC center=%s r=%s a=%s->%s" % (
                self._fmt_point(self.center()),
                _fmt_num(radius) if radius is not None else "?",
                _fmt_num(angles[0]) if angles else "?",
                _fmt_num(angles[1]) if angles else "?",
            )
        elif kind == "TEXT":
            body = "TEXT %s at %s" % (
                json.dumps(self.text(), ensure_ascii=False), self._fmt_point(self.start()))
        elif kind == "POINT":
            body = "POINT %s" % self._fmt_point(self.start())
        elif kind in ("LWPOLYLINE", "POLYLINE"):
            verts = self.vertices()
            shown = " ".join("(%s)" % self._fmt_point(p) for p in verts[:12])
            if len(verts) > 12:
                shown += " …共 %d 点" % len(verts)
            body = "%s %d 点 %s%s" % (
                kind, len(verts), shown, " 闭合" if self.closed() else "")
        elif kind == "VERTEX":
            body = "VERTEX %s" % self._fmt_point(self.start())
        else:
            body = "%s [未知实体 原样保留]" % self.type
        extras = []
        if self.has_xdata:
            apps = ",".join(self.xdata_apps) or "?"
            extras.append(" [XDATA %s ×%d]" % (apps, len(self.xdata)))
        if self.is_proxy:
            extras.append(" [代理对象 原样保留]")
        return body + layer + "".join(extras)

    # —— 导出 ——

    def as_dict(self) -> dict:
        """索引/JSON 用的形态（`tags` 是 `[{code, value}]`，值已解析）。"""
        data: dict = {
            "type": self.type,
            "layer": self.layer,
            "handle": self.handle,
            "tags": [tag.as_dict() for tag in self.tags],
            "xdata": [tag.as_dict() for tag in self.xdata],
            "unknown": self.is_unknown,
            "proxy": self.is_proxy,
            "summary": self.summary(),
            "raw_sha256": sha256_text(self.raw),
            "raw_bytes": len(self.raw.encode("utf-8")),
        }
        if self.children:
            data["children"] = [child.as_dict() for child in self.children]
        if self.parent_type:
            data["parent_type"] = self.parent_type
        return data

    def fingerprint(self, *, drawing: str = "", seq: int = 0, source_path: str = "") -> str:
        """幂等判据：**只看当前内容**（原文 + 位置），不看 mtime / 索引时间。

        取的是 `render()`（没改过的实体就等于原文切片，两种口径在索引路径上完全一致）；
        **子实体的内容一并进指纹** —— 否则 `POLYLINE` 的 `VERTEX` 改了、父实体的原文没变，
        索引就会**静默不更新**。
        """
        return sha256_text(canonical_json({
            "drawing": drawing,
            "seq": seq,
            "source_path": source_path,
            "type": self.type,
            "raw": self.render(),
            "children": [child.render() for child in self.children],
        }))


# --------------------------------------------------------------------------------------
# Table / Layer
# --------------------------------------------------------------------------------------


@dataclass
class Layer:
    """`LAYER` 表里的一行（便利视图；真源永远是原文标签）。"""

    name: str
    color: "int | None" = None
    linetype: str = ""
    flags: int = 0
    entity: "Entity | None" = None

    @property
    def frozen(self) -> bool:
        return bool(self.flags & 1)

    @property
    def locked(self) -> bool:
        return bool(self.flags & 4)

    @property
    def off(self) -> bool:
        """颜色为负 = 图层关闭（DXF 的约定）。"""
        return self.color is not None and self.color < 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "color": self.color,
            "linetype": self.linetype,
            "flags": self.flags,
            "frozen": self.frozen,
            "locked": self.locked,
            "off": self.off,
        }


@dataclass
class Table:
    """`TABLES` 段里的一张表（`0/TABLE` … `0/ENDTAB`）。"""

    name: str
    tags: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    orphans: list = field(default_factory=list)

    def render(self) -> str:
        return "".join(tag.render() for tag in self.tags)

    def layer_entries(self) -> list:
        """本表里的 `LAYER` 行 → `[Layer, …]`（不是 LAYER 表就返回空）。"""
        out: list = []
        for entry in self.entries:
            if entry.type.upper() != "LAYER":
                continue
            name = entry.get(2)
            color = entry.get(62)
            linetype = entry.get(6)
            flags = entry.get(70)
            out.append(Layer(
                name=name.strip() if isinstance(name, str) else ("" if name is None else str(name)),
                color=int(color) if isinstance(color, (int, float)) and not isinstance(color, bool) else None,
                linetype=linetype if isinstance(linetype, str) else ("" if linetype is None else str(linetype)),
                flags=int(flags) if isinstance(flags, (int, float)) and not isinstance(flags, bool) else 0,
                entity=entry,
            ))
        return out

    def as_dict(self) -> dict:
        return {"name": self.name, "entries": [e.type for e in self.entries]}


# --------------------------------------------------------------------------------------
# Section
# --------------------------------------------------------------------------------------


@dataclass
class Section:
    """一个段：`0/SECTION` … `0/ENDSEC`（**整段**都在 `tags` 里，含首尾两个 0 标签）。"""

    name: str
    tags: list = field(default_factory=list)
    body_tags: list = field(default_factory=list)
    entities: list = field(default_factory=list)      # 仅 ENTITIES 段填充（含 BLOCKS 之外的）
    tables: list = field(default_factory=list)        # 仅 TABLES 段填充
    orphans: list = field(default_factory=list)       # 段体里不属于任何实体的散标签

    @property
    def upper(self) -> str:
        return self.name.upper()

    def render(self) -> str:
        return "".join(tag.render() for tag in self.tags)

    @property
    def modified(self) -> bool:
        return self.render() != "".join(tag.raw for tag in self.tags)

    def header_vars(self) -> dict:
        """`HEADER` 段 → `{"$ACADVER": [Tag, …], …}`（其它段返回空 dict）。

        变量名在组码 9；其后的标签（直到下一个 9）都算这个变量的值。
        """
        out: dict = {}
        if self.upper != "HEADER":
            return out
        current: "str | None" = None
        for tag in self.body_tags:
            if tag.code == 9:
                current = str(tag.value).strip()
                out.setdefault(current, [])
                continue
            if current is None:
                continue
            out[current].append(tag)
        return out

    def entity_types(self) -> dict:
        counts: dict = {}
        for entity in self.entities:
            counts[entity.type] = counts.get(entity.type, 0) + 1
        return counts

    def as_dict(self) -> dict:
        data = {
            "name": self.name,
            "tags": len(self.tags),
            "entities": len(self.entities),
            "tables": len(self.tables),
        }
        if self.entities:
            data["entity_types"] = self.entity_types()
        return data


# --------------------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------------------


@dataclass
class Document:
    """一张图纸。

    * `sections`  —— 段的有序列表（**原始顺序**，不重排）。
    * `raw_lines` —— 读入时的**逐行快照**（每项含行尾，`raw_lines == [line.render()]`）。
    * `lines`     —— 同一批 `Line` 对象（`Tag` 攥着的就是它们 ⇒ 改值即改这里）。
    * `raw_bytes` / `encoding` / `bom` —— 原字节与解码方式（默认按原编码写回）。
    * `preamble` / `epilogue` —— 首个 `SECTION` 之前 / 最后一个 `ENDSEC` 之后的标签
      （正常文件里前者通常是空的，后者是 `0/EOF`）。
    """

    path: str = ""
    encoding: str = "utf-8"
    bom: str = ""
    lines: list = field(default_factory=list)
    raw_lines: list = field(default_factory=list)
    input_text: str = ""
    raw_bytes: "bytes | None" = None
    preamble: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    epilogue: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    # —— 段 / 实体 ——

    def section(self, name: str) -> "Section | None":
        wanted = (name or "").upper()
        for item in self.sections:
            if item.upper == wanted:
                return item
        return None

    def require_section(self, name: str) -> "Section":
        found = self.section(name)
        if found is None:
            raise DxfError("文档里没有 %s 段（有：%s）" % (
                name, ", ".join(item.name for item in self.sections) or "（一个都没有）"))
        return found

    def section_names(self) -> list:
        return [item.name for item in self.sections]

    def entities(self, *, include_children: bool = False) -> list:
        """`ENTITIES` 段里的实体（按原始顺序）。

        `include_children=True` 时把 `POLYLINE`/`INSERT` 的跟随子实体也展开进来。
        """
        out: list = []
        for item in self.sections:
            if item.upper != "ENTITIES":
                continue
            for entity in item.entities:
                if include_children:
                    out.extend(entity.iter_entities())
                else:
                    out.append(entity)
        return out

    def entities_of_type(self, entity_type: str) -> list:
        wanted = (entity_type or "").upper()
        return [e for e in self.entities() if e.type.upper() == wanted]

    def unknown_entities(self, *, include_children: bool = False) -> list:
        return [e for e in self.entities(include_children=include_children) if e.is_unknown]

    def proxy_entities(self) -> list:
        return [e for e in self.entities() if e.is_proxy]

    def xdata_entities(self) -> list:
        return [e for e in self.entities() if e.has_xdata]

    def modified_entities(self) -> list:
        return [e for e in self.entities(include_children=True) if e.modified]

    def entity_count(self, *, include_children: bool = False) -> int:
        return len(self.entities(include_children=include_children))

    def iter_all_tags(self) -> Iterator[Tag]:
        for tag in self.preamble:
            yield tag
        for section in self.sections:
            for tag in section.tags:
                yield tag
        for tag in self.epilogue:
            yield tag

    # —— 表 / 图层 ——

    def tables(self) -> list:
        out: list = []
        for item in self.sections:
            if item.upper == "TABLES":
                out.extend(item.tables)
        return out

    def table(self, name: str) -> "Table | None":
        wanted = (name or "").upper()
        for item in self.tables():
            if item.name.upper() == wanted:
                return item
        return None

    def layers(self) -> list:
        """`LAYER` 表里的图层（没有 LAYER 表就返回空列表）。"""
        table = self.table("LAYER")
        return table.layer_entries() if table is not None else []

    def layer_names(self) -> list:
        return [layer.name for layer in self.layers()]

    def layer_usage(self) -> dict:
        """实体用到的图层 → 实体数（**实测**，不是从 LAYER 表读的声明值）。"""
        counts: dict = {}
        for entity in self.entities():
            counts[entity.layer] = counts.get(entity.layer, 0) + 1
        return counts

    # —— 序列化 ——

    def render_text(self) -> str:
        """当前内容（可能已被改过）。`bom` 在最前面，行尾一律用各 `Line` 自己的。"""
        return self.bom + "".join(line.render() for line in self.lines)

    def render_bytes(self) -> bytes:
        """当前内容的字节。**未修改时必然等于 `raw_bytes`**，否则响亮报错。"""
        text = self.render_text()
        try:
            payload = text.encode(self.encoding)
        except UnicodeEncodeError as exc:
            raise DxfError(
                "按原编码 %s 写不回这段文本（%s）；请改回原字符或显式换 encoding" % (self.encoding, exc)) from None
        if self.raw_bytes is not None and not self.modified and payload != self.raw_bytes:
            raise DxfError(
                "序列化自检失败：未修改的文档重渲染后与原文字节不一致"
                "（原文 %d 字节 / 重渲染 %d 字节）—— 这是本包的 bug，不许静默写出"
                % (len(self.raw_bytes), len(payload)))
        return payload

    @property
    def modified(self) -> bool:
        """有没有被改过（逐行文本对照读入快照；不涉及编码）。"""
        if self.raw_bytes is None:
            return True
        return self.render_text() != self.bom + self.input_text

    @property
    def newline_style(self) -> str:
        """出现最多的行尾（没有行尾时返回空串）。"""
        counts: dict = {}
        for line in self.lines:
            if line.newline:
                counts[line.newline] = counts.get(line.newline, 0) + 1
        if not counts:
            return ""
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    def line_of(self, index: int) -> "Line | None":
        if 0 <= index < len(self.lines):
            return self.lines[index]
        return None

    def sha256_input(self) -> str:
        return hashlib.sha256(self.raw_bytes or b"").hexdigest()

    def sha256_render(self) -> str:
        return hashlib.sha256(self.render_bytes()).hexdigest()

    # —— 汇总 ——

    def entity_type_counts(self) -> dict:
        counts: dict = {}
        for entity in self.entities():
            counts[entity.type] = counts.get(entity.type, 0) + 1
        return counts

    def as_dict(self) -> dict:
        counts = self.entity_type_counts()
        return {
            "path": self.path,
            "encoding": self.encoding,
            "newline": self.newline_style,
            "bytes": len(self.raw_bytes) if self.raw_bytes is not None else len(self.render_bytes()),
            "lines": len(self.lines),
            "tags": sum(1 for _ in self.iter_all_tags()),
            "sections": self.section_names(),
            "entities": self.entity_count(),
            "child_entities": self.entity_count(include_children=True) - self.entity_count(),
            "entity_types": counts,
            "unknown_entities": len(self.unknown_entities()),
            "proxy_entities": len(self.proxy_entities()),
            "xdata_entities": len(self.xdata_entities()),
            "layers": self.layer_names(),
            "layer_usage": self.layer_usage(),
            "modified": self.modified,
            "modified_entities": len(self.modified_entities()),
            "sha256_input": self.sha256_input(),
            "warnings": list(self.warnings),
        }
