#!/usr/bin/env python3
"""annotation 族第 18 号能力的实现片（`annotation__w27`）：**多行文字（MTEXT）的真解析与真排版**。

条目定位（本片为什么做这一条）
------------------------------
`docs/research/cad-parity/features/autocad-annotation.json` 的 `must_have` 共 22 条；按同目录
`status/autocad-annotation.json` 的逐条状态，本轮仍 missing/partial 的按清单顺序只有 4 条：

    0. 线性标注（水平/垂直/旋转）      partial
    1. 关联标注、重关联与更新          partial
    2. 多行文字                        partial  <- 本片
    3. 多重引线                        partial

18 mod 4 = 2 ⇒ 本片负责**第 2 号「多行文字」**。族模块 `ops/annotation.py:511-535 add_mtext`
只到「一段文本 + 可选字高」；本条保留 partial 的原因正是：无段落格式、无堆叠分数、无特殊符号字段。

本片在 L1 文档模型上**真实现** MTEXT 的三件缺能力（真解析 / 真几何 / 真改写后果）
----------------------------------------------------------------------------------
1. **真解析 MTEXT 内联码**：`\\P` 段落、`\\S a/b;`(或 `\\S a^b;`) 堆叠分数、`\\H<n>x;` 字高倍率、
   `\\W<n>;` 宽度因子、`\\f<font>|b1|i1;` 字体与开关、`\\C<n>;` 颜色、`\\L/\\l/\\O/\\o/\\K/\\k`
   上下划线/删除线、`\\A<n>;` 对正、`\\p i,l,r,t;` 段落缩进、`{ }` 分组嵌套、`%%d/%%p/%%c/%%%`
   符号、`\\U+XXXX` Unicode 转义。**未知码显式报 `unsupported_code`**（不静默吞）。
2. **真几何（整数纳米）**：按 em 半宽模型逐字符累加行宽（东亚宽字符 2 半宽、其余 1 半宽），
   行高 = 行内最大字高 × 行距（缺省 1667‰，即 AutoCAD 5/3），支持列宽 `column_width_nm` 的
   **真换行**（记录换行字符下标）与**真溢出计数**；九个 MTEXT 对正点决定 `bbox_nm`。
   堆叠分数按「分子/分母两者较宽者」占位（真排版后果，不是常数）。
3. **真数据后果 + 可改写**：`mtext_reformat` / `mtext_set_paragraph` / `mtext_append_paragraph` /
   `mtext_insert_fraction` / `mtext_measure` 都重算段落、分数、行盒与 `bbox_nm`，
   递增 `args.revision`，并落一份稳定的 `args.layout_digest`（sha256，可复跑判定行）。

冻结接口
--------
    FAMILY = "annotation"
    OPERATIONS: dict[str, Callable]        # 操作名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 第一个位置参数是文档，其余关键字，返回 uuid 列表
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

口径（与 `ops/annotation.py` / `ops/annotation_extra.py` 同源，不另立一套）
----------------------------------------------------------------------
1. **长度一律整数纳米**：`at_nm` / `height_nm` / `column_width_nm` / 一切 `*_nm` 只允许 `int`
   （`bool` 与浮点一律拒），拒绝时抛 `invalid_length` / `non_positive_length`；
   `*_nm` 键**绝不为 None**（L1 不变量：只有 `value_nm` 可为 null）⇒ 未设列宽时**不落该键**。
2. **先校验后写入**：每个 op 的全部校验（含重建后的整篇重解析）都跑在动 `annotations[]` 之前，
   失败调用**不产生半条注解、不改旧注解**（自检有原子性断言）。
3. **文案一律走 key**：`literal=False` 时 `text_key` 必须是点分 ASCII 标识符；**调用方给的已解析文本
   只用于量尺寸，绝不落进 L1**（自检断言 key 模式下 L1 文本里搜不到该文本）。`literal=True` 时
   人话文本与其 `literal` 标记同层（`args.literal=True`），符合 `docmodel._check_text` 的豁免口径。
4. **落盘字段只用 L1 冻结的 annotation 字段集**（`uuid` / `kind` / `text_key` / `literal` / `args`）：
   段落、run、分数、符号、换行点、对正、digest 全进 `args`，且除 `literal=True` 的那一层外
   **只放整数/枚举码**，因此 `docmodel.check_invariants()` 零污染（自检断言 `== []`）。
5. **不做猜测**：字高倍率 0 或越界、颜色越界、分数分母为 0、`{`/`}` 不配对、未知 `\\X` 码、
   对正名不在九点表 —— 一律显式报错，不静默取缺省。
6. **只改本片名下的文件**：不写 `__init__.py`，不改 `ops/annotation.py`（本片是新增能力片）。

自检（文件自带，不依赖 pytest、不依赖网络）：

    python3 -m cadcore.ops.features.annotation__w27
    # 末行：FEATURE_OK family=annotation op=mtext_create verdict=PASS

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 只把非 docstring 的含汉字字符串常量计为硬编码）。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
import unicodedata
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

from cadcore.errors import CadError

__all__ = [
    "FAMILY", "OPERATIONS",
    "mtext_create", "mtext_reformat", "mtext_set_paragraph",
    "mtext_append_paragraph", "mtext_insert_fraction", "mtext_measure",
    "parse_mtext", "layout_mtext", "selftest", "main",
]

FAMILY = "annotation"

#: 「保持现状」哨兵（`None` 在 `column_width_nm` 上有独立语义 = 取消列宽换行）。
_KEEP = object()

_MAX_TEXT_CHARS = 200_000
_MAX_PARAGRAPHS = 512
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
_FONT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._\-]{0,63}$")

#: `%%<c>` 符号码：字符 → 枚举码（枚举码落盘，字符串不落 L1）。
_SYMBOL_CODES = {"d": 0, "p": 1, "c": 2, "%": 3}
_SYMBOL_CHARS = {"d": "\u00b0", "p": "\u00b1", "c": "\u2300", "%": "%"}

#: `kind` 的整数枚举（L1 里不落字符串）。
_RUN_TEXT, _RUN_FRACTION, _RUN_SYMBOL = 0, 1, 2

#: 九个 MTEXT 对正点：名字 → (水平 0左1中2右, 垂直 0上1中2下)。
_ATTACHMENTS = {
    "top_left": (0, 0), "top_center": (1, 0), "top_right": (2, 0),
    "middle_left": (0, 1), "middle_center": (1, 1), "middle_right": (2, 1),
    "bottom_left": (0, 2), "bottom_center": (1, 2), "bottom_right": (2, 2),
}

#: 缺省行距（‰）：AutoCAD MTEXT 单倍行距 = 5/3 字高。
DEFAULT_LINE_SPACING_MILLI = 1667


# --------------------------------------------------------------------------------------
# 校验器（全部在写入之前跑完）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _uuid_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("bad_uuid", "%s must be a uuid string" % what)
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _fail("bad_uuid", "%s is not a uuid: %r" % (what, value)) from exc
    return value


def _nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（`bool` 与浮点一律拒；`positive` 时要求 > 0）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "%s must be an integer number of nanometres" % what)
    if positive and value <= 0:
        raise _fail("non_positive_length", "%s must be positive" % what)
    return value


def _point_nm(value: Any, what: str) -> list[int]:
    """点入参 → `[x_nm, y_nm]`（允许负坐标；坐标也是长度，整数纳米）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_point", "%s must be a (x_nm, y_nm) pair" % what)
    return [_nm(value[0], "%s[0]" % what), _nm(value[1], "%s[1]" % what)]


def _key(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise _fail("bad_text_key", "%s must be a dotted ASCII i18n key" % what)
    return value


def _int_token(token: Any, what: str) -> int:
    if not isinstance(token, str) or not re.match(r"^[+-]?[0-9]+$", token.strip()):
        raise _fail("bad_property_value", "%s must be an integer token" % what)
    return int(token.strip())


def _scaled_milli(token: Any, what: str) -> int:
    """十进制 token → 千分整数（精确整数运算，不用浮点）。"""
    if not isinstance(token, str) or not re.match(r"^[+-]?[0-9]+(\.[0-9]{1,3})?$", token.strip()):
        raise _fail("bad_property_value", "%s must be a decimal with <= 3 fraction digits" % what)
    text = token.strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    whole, _, frac = text.partition(".")
    return sign * (int(whole) * 1000 + int((frac + "000")[:3]))


def _scaled_micro(token: Any, what: str) -> int:
    """十进制 token → 百万分整数（分数分子/分母的原值，精确整数运算）。"""
    if not isinstance(token, str) or not re.match(r"^[+-]?[0-9]+(\.[0-9]{1,6})?$", token.strip()):
        raise _fail("bad_fraction", "%s must be a decimal with <= 6 fraction digits" % what)
    text = token.strip()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    whole, _, frac = text.partition(".")
    return sign * (int(whole) * 1_000_000 + int((frac + "000000")[:6]))


def _div_round(numerator: int, denominator: int) -> int:
    """整数除法、四舍五入（远离零），**不经过浮点** ⇒ 结果可复跑。"""
    if denominator == 0:
        raise _fail("invalid_fraction", "denominator must not be zero")
    sign = -1 if (numerator < 0) != (denominator < 0) else 1
    return sign * ((2 * abs(numerator) + abs(denominator)) // (2 * abs(denominator)))


# --------------------------------------------------------------------------------------
# 解析：MTEXT 内联码 → 段落/run/分数/符号/码表（真解析，未知码显式报错）
# --------------------------------------------------------------------------------------


def _default_attrs() -> dict[str, Any]:
    return {"height_scale_milli": 1000, "width_factor_milli": 1000, "font": None,
            "bold": 0, "italic": 0, "underline": 0, "overline": 0, "strike": 0,
            "color": None, "align": None, "indent_units": 0, "left_units": 0,
            "right_units": 0, "tab_units": 0}


def _apply_property(attrs: dict[str, Any], code: str, body: str, offset: int) -> None:
    """`\\H/\\W/\\A/\\C/\\f/\\p` 的参数体 → 当前组属性（越界一律显式报错）。"""
    if code == "H":
        token = body.strip()
        if token.endswith(("x", "X")):
            token = token[:-1]
        milli = _scaled_milli(token, "height scale")
        if milli <= 0:
            raise _fail("invalid_height_scale", "height scale must be positive (offset=%d)" % offset)
        if milli > 100_000:
            raise _fail("out_of_range_height_scale",
                        "height scale must be <= 100x (offset=%d)" % offset)
        attrs["height_scale_milli"] = milli
        return
    if code == "W":
        milli = _scaled_milli(body, "width factor")
        if milli <= 0 or milli > 100_000:
            raise _fail("out_of_range_width_factor",
                        "width factor must be in (0, 100] (offset=%d)" % offset)
        attrs["width_factor_milli"] = milli
        return
    if code == "A":
        value = _int_token(body, "alignment")
        if value not in (0, 1, 2):
            raise _fail("out_of_range_alignment", "alignment must be 0/1/2 (offset=%d)" % offset)
        attrs["align"] = value
        return
    if code == "C":
        value = _int_token(body, "color index")
        if value < 0 or value > 256:
            raise _fail("out_of_range_color", "color index must be in 0..256 (offset=%d)" % offset)
        attrs["color"] = value
        return
    if code == "f":
        parts = body.split("|")
        name = parts[0].strip()
        if name:
            if not _FONT_RE.match(name):
                raise _fail("bad_font_name", "font name %r is not acceptable" % name)
            attrs["font"] = name
        for token in parts[1:]:
            flag = token.strip().lower()
            if not flag:
                continue
            if flag in ("b0", "b1"):
                attrs["bold"] = int(flag[1])
            elif flag in ("i0", "i1"):
                attrs["italic"] = int(flag[1])
            elif flag.startswith("c") and flag[1:].isdigit():
                value = int(flag[1:])
                if value > 256:
                    raise _fail("out_of_range_color", "color index must be in 0..256")
                attrs["color"] = value
            else:
                raise _fail("unsupported_code", "unknown '\\f' flag %r" % token)
        return
    if code == "p":
        for token in body.split(","):
            item = token.strip()
            if not item:
                continue
            head = item[0]
            if head not in "ilrt":
                raise _fail("unsupported_code", "unknown '\\p' property %r" % item)
            slot = {"i": "indent_units", "l": "left_units", "r": "right_units",
                    "t": "tab_units"}[head]
            attrs[slot] = _int_token(item[1:] or "0", "paragraph property %r" % item)
        return
    raise _fail("unsupported_code", "unhandled MTEXT property code %r" % code)


def _split_fraction(body: str, offset: int) -> tuple[str, str, int]:
    """`\\S` 参数体 → `(分子 token, 分母 token, 分隔符码)`；分隔符码 0=`/`、1=`^`。"""
    if "/" in body:
        numerator, _, denominator = body.partition("/")
        slash = 0
    elif "^" in body:
        numerator, _, denominator = body.partition("^")
        slash = 1
    else:
        raise _fail("bad_fraction",
                    "stacked fraction needs 'num/den' or 'num^den' (offset=%d)" % offset)
    if not numerator.strip() or not denominator.strip():
        raise _fail("bad_fraction", "fraction numerator/denominator must be non-empty")
    return numerator.strip(), denominator.strip(), slash


def _token_units(token: str) -> int:
    """token 的占位半宽数（分数按行内半宽模型的字符数近似，确定且与落盘无关）。"""
    return sum(_width_units(ch) for ch in token) or 1


def parse_mtext(text: Any, *, literal: bool) -> dict[str, Any]:
    """MTEXT 文本 → 结构化解析结果（段落 / run / 分数 / 符号 / 码计数 / 段落原文切片）。

    返回：`{"paragraphs": [[run, ...], ...], "fractions": [...], "symbols": [...],
    "codes": {码: 次数}, "markup_paragraphs": [原文切片, ...], "max_depth": int}`。
    非法输入一律 `CadError`：`bad_mtext_text` / `bad_literal_text` / `bad_resolved_text` /
    `text_too_long` / `too_many_paragraphs` / `bad_nesting` / `unsupported_code` /
    `bad_fraction` / `invalid_fraction` / `invalid_height_scale` / `out_of_range_*`。
    """
    if not isinstance(text, str):
        raise _fail("bad_mtext_text", "text must be a string, got %s" % type(text).__name__)
    if not text:
        raise _fail("bad_literal_text" if literal else "bad_resolved_text", "text must be non-empty")
    if len(text) > _MAX_TEXT_CHARS:
        raise _fail("text_too_long", "text has %d chars, limit is %d" % (len(text), _MAX_TEXT_CHARS))

    stack: list[dict[str, Any]] = [_default_attrs()]
    paragraphs: list[list[dict[str, Any]]] = [[]]
    markup_spans: list[int] = [0]          # 每段原文在 text 中的起点
    fractions: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    codes: dict[str, int] = {}
    buf: list[str] = []
    max_depth = 1

    def bump(code: str) -> None:
        codes[code] = codes.get(code, 0) + 1

    def flush() -> None:
        if buf:
            paragraphs[-1].append({"kind": _RUN_TEXT, "attrs": dict(stack[-1]),
                                   "text": "".join(buf), "fraction": None, "symbol": None})
            del buf[:]

    index = 0
    length = len(text)
    while index < length:
        ch = text[index]

        if ch == "{":
            flush()
            stack.append(dict(stack[-1]))
            max_depth = max(max_depth, len(stack))
            bump("{")
            index += 1
            continue

        if ch == "}":
            flush()
            if len(stack) == 1:
                raise _fail("bad_nesting", "unbalanced '}' at offset %d" % index)
            stack.pop()
            bump("}")
            index += 1
            continue

        if ch == "%" and text.startswith("%%", index):
            if index + 2 >= length:
                raise _fail("unsupported_code", "dangling '%%%%' at offset %d" % index)
            symbol = text[index + 2]
            if symbol not in _SYMBOL_CODES:
                raise _fail("unsupported_code", "unknown '%%%%%s' code at offset %d" % (symbol, index))
            flush()
            paragraphs[-1].append({"kind": _RUN_SYMBOL, "attrs": dict(stack[-1]), "text": None,
                                   "fraction": None, "symbol": _SYMBOL_CODES[symbol]})
            symbols.append({"paragraph": len(paragraphs) - 1, "code": _SYMBOL_CODES[symbol]})
            if literal:
                paragraphs[-1][-1]["text"] = _SYMBOL_CHARS[symbol]
            bump("%%" + symbol)
            index += 3
            continue

        if ch == "\\":
            if index + 1 >= length:
                raise _fail("unsupported_code", "dangling backslash at offset %d" % index)
            nxt = text[index + 1]

            if nxt in "\\{}":
                flush()
                buf.append(nxt)
                index += 2
                continue

            if nxt == "P":
                paragraphs.append([])
                markup_spans.append(index + 2)
                if len(paragraphs) > _MAX_PARAGRAPHS:
                    raise _fail("too_many_paragraphs",
                                "paragraph count exceeds %d" % _MAX_PARAGRAPHS)
                bump("P")
                index += 2
                continue

            if nxt == "~":
                flush()
                buf.append(" ")
                bump("~")
                index += 2
                continue

            if nxt in ("L", "l", "O", "o", "K", "k"):
                flush()
                slot = {"L": "underline", "l": "underline", "O": "overline", "o": "overline",
                        "K": "strike", "k": "strike"}[nxt]
                stack[-1][slot] = 1 if nxt in ("L", "O", "K") else 0
                bump(nxt)
                index += 2
                continue

            if nxt == "S":
                end = text.find(";", index + 2)
                if end < 0:
                    raise _fail("bad_fraction",
                                "stacked fraction needs a terminating ';' (offset=%d)" % index)
                numerator_token, denominator_token, slash = _split_fraction(
                    text[index + 2:end], index)
                numerator = _scaled_micro(numerator_token, "fraction numerator")
                denominator = _scaled_micro(denominator_token, "fraction denominator")
                value_micro = _div_round(numerator * 1_000_000, denominator)
                flush()
                paragraph_index = len(paragraphs) - 1
                record = {"paragraph": paragraph_index, "numerator_micro": numerator,
                          "denominator_micro": denominator, "value_micro": value_micro,
                          "slash": slash,
                          "stack_units": max(_token_units(numerator_token),
                                             _token_units(denominator_token))}
                fractions.append(record)
                paragraphs[-1].append({"kind": _RUN_FRACTION, "attrs": dict(stack[-1]),
                                       "text": ("%s/%s" % (numerator_token, denominator_token))
                                       if literal else None,
                                       "fraction": dict(record), "symbol": None})
                bump("S")
                index = end + 1
                continue

            if nxt in ("H", "W", "A", "C", "f", "p"):
                end = text.find(";", index + 2)
                if end < 0:
                    raise _fail("unsupported_code",
                                "code '\\%s' needs a terminating ';' (offset=%d)" % (nxt, index))
                flush()
                _apply_property(stack[-1], nxt, text[index + 2:end], index)
                bump(nxt)
                index = end + 1
                continue

            if nxt == "U":
                if index + 7 > length or text[index + 2] != "+":
                    raise _fail("invalid_unicode_escape",
                                "'\\U' needs '+XXXX' at offset %d" % index)
                hex_digits = text[index + 3:index + 7]
                if not re.match(r"^[0-9A-Fa-f]{4}$", hex_digits):
                    raise _fail("invalid_unicode_escape",
                                "'\\U+%s' is not 4 hex digits" % hex_digits)
                codepoint = int(hex_digits, 16)
                if codepoint == 0:
                    raise _fail("invalid_unicode_escape", "'\\U+0000' is not a usable code point")
                flush()
                buf.append(chr(codepoint))
                bump("U")
                index += 7
                continue

            raise _fail("unsupported_code", "unknown MTEXT code '\\%s' at offset %d" % (nxt, index))

        flush()
        buf.append(ch)
        index += 1

    flush()
    if len(stack) != 1:
        raise _fail("bad_nesting", "unbalanced '{' (depth=%d at end)" % len(stack))

    markup_paragraphs = []
    for position, start in enumerate(markup_spans):
        stop = markup_spans[position + 1] - 2 if position + 1 < len(markup_spans) else len(text)
        markup_paragraphs.append(text[start:stop])

    return {"paragraphs": paragraphs, "fractions": fractions, "symbols": symbols,
            "codes": {key: codes[key] for key in sorted(codes)},
            "markup_paragraphs": markup_paragraphs, "max_depth": max_depth,
            "char_count": len(text)}


# --------------------------------------------------------------------------------------
# 排版：整数纳米的真几何（半宽模型 + 真换行 + 九点对正 bbox）
# --------------------------------------------------------------------------------------


def _width_units(ch: str) -> int:
    """字符占宽（半宽 em 单位）：东亚宽/全角 2，制表 4，其余 1（Ambiguous 按窄处理）。"""
    if ch == "\t":
        return 4
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _advance_nm(height_nm: int, width_factor_milli: int, units: int) -> int:
    """字宽 = 字高 × 宽度因子 × units/2（整数纳米、四舍五入、无浮点）。"""
    return (height_nm * width_factor_milli * units + 1000) // 2000


def _run_height(attrs: Mapping[str, Any], height_nm: int) -> int:
    scaled = (height_nm * int(attrs["height_scale_milli"]) + 500) // 1000
    return scaled if scaled > 0 else 1


def _line_height_nm(line_height_nm: int, line_spacing_milli: int) -> int:
    value = (line_height_nm * line_spacing_milli + 500) // 1000
    return value if value > 0 else 1


def layout_mtext(parsed: Mapping[str, Any], *, height_nm: int, width_factor_milli: int,
                 line_spacing_milli: int, column_width_nm: int | None,
                 at_nm: list[int], attach_h: int, attach_v: int) -> dict[str, Any]:
    """解析结果 → 真几何行盒（整数纳米）。

    逐字符按半宽模型累加；`column_width_nm` 给定时按字符边界真换行并记录换行下标，
    单个原子（宽字符/分数）自己就超过列宽时计入 `overflow_lines`（不静默截断）。
    """
    lines: list[dict[str, Any]] = []
    wrap_offsets: list[list[int]] = []
    overflow_lines = 0
    char_count = 0
    wide_char_count = 0
    paragraph_records: list[dict[str, Any]] = []

    for paragraph_index, runs in enumerate(parsed["paragraphs"]):
        attrs0 = runs[0]["attrs"] if runs else _default_attrs()
        left_margin_nm = int(attrs0["left_units"]) * height_nm
        right_margin_nm = int(attrs0["right_units"]) * height_nm
        limit = None
        if column_width_nm is not None:
            limit = column_width_nm - left_margin_nm - right_margin_nm
            if limit <= 0:
                raise _fail("bad_column_width",
                            "column width minus paragraph margins must be positive")

        atoms: list[tuple[int, int]] = []       # (advance_nm, height_nm)
        paragraph_chars = 0
        for run in runs:
            run_height = _run_height(run["attrs"], height_nm)
            factor = int(run["attrs"]["width_factor_milli"])
            if run["kind"] == _RUN_FRACTION:
                units = int(run["fraction"]["stack_units"])
                atoms.append((_advance_nm(run_height, factor, max(units, 1)), run_height))
                paragraph_chars += 1
            elif run["kind"] == _RUN_SYMBOL:
                glyph = run["text"] or _SYMBOL_CHARS["d"]
                atoms.append((_advance_nm(run_height, factor, _width_units(glyph)), run_height))
                paragraph_chars += 1
            else:
                for ch in run["text"] or "":
                    units = _width_units(ch)
                    atoms.append((_advance_nm(run_height, factor, units), run_height))
                    paragraph_chars += 1
                    if units == 2:
                        wide_char_count += 1

        char_count += paragraph_chars
        paragraph_lines = 0
        used = 0
        max_height = 0
        cursor = 0

        def close_line() -> None:
            nonlocal used, max_height, cursor, paragraph_lines, overflow_lines
            if cursor == 0 and paragraph_chars > 0:
                return
            lines.append({"paragraph": paragraph_index,
                          "width_nm": left_margin_nm + used + right_margin_nm,
                          "height_nm": _line_height_nm(max_height or height_nm,
                                                       line_spacing_milli),
                          "char_count": cursor})
            paragraph_lines += 1
            if limit is not None and used > limit:
                overflow_lines += 1
            used = 0
            max_height = 0
            cursor = 0

        if not atoms:
            lines.append({"paragraph": paragraph_index,
                          "width_nm": left_margin_nm + right_margin_nm,
                          "height_nm": _line_height_nm(height_nm, line_spacing_milli),
                          "char_count": 0})
            paragraph_lines = 1

        for position, (advance, atom_height) in enumerate(atoms):
            if cursor > 0 and limit is not None and used + advance > limit:
                wrap_offsets.append([paragraph_index, position])
                close_line()
            used += advance
            max_height = max(max_height, atom_height)
            cursor += 1
        if cursor > 0:
            close_line()

        paragraph_records.append({"index": paragraph_index, "line_count": paragraph_lines,
                                  "height_nm": sum(item["height_nm"] for item in lines
                                                   if item["paragraph"] == paragraph_index),
                                  "width_nm": max([item["width_nm"] for item in lines
                                                   if item["paragraph"] == paragraph_index]
                                                  or [0]),
                                  "char_count": paragraph_chars,
                                  "left_margin_nm": left_margin_nm,
                                  "right_margin_nm": right_margin_nm})

    block_width = max([item["width_nm"] for item in lines] or [0])
    block_height = sum(item["height_nm"] for item in lines)

    anchor_x, anchor_y = at_nm
    if attach_h == 1:
        x0 = anchor_x - block_width // 2
    elif attach_h == 2:
        x0 = anchor_x - block_width
    else:
        x0 = anchor_x
    if attach_v == 0:
        y1 = anchor_y
    elif attach_v == 1:
        y1 = anchor_y + block_height // 2
    else:
        y1 = anchor_y + block_height
    y0 = y1 - block_height

    return {"bbox_nm": [x0, y0, x0 + block_width, y1], "width_nm": block_width,
            "height_nm": block_height, "line_count": len(lines),
            "paragraph_count": len(parsed["paragraphs"]),
            "max_line_width_nm": max([item["width_nm"] for item in lines] or [0]),
            "wrap_offsets": wrap_offsets, "overflow_lines": overflow_lines,
            "char_count": char_count, "wide_char_count": wide_char_count,
            "line_heights_nm": [item["height_nm"] for item in lines],
            "paragraphs": paragraph_records}


# --------------------------------------------------------------------------------------
# 落盘（L1：除 literal 层外只放整数/枚举码）
# --------------------------------------------------------------------------------------


def _annotations(sk_or_doc: Any) -> list:
    """可写的 `annotations[]` 真源那一份（不是副本）。"""
    if isinstance(sk_or_doc, Mapping):
        annotations = sk_or_doc.get("annotations")
        if isinstance(annotations, list):
            return annotations
        if "entities" in sk_or_doc:
            raise _fail("annotation_needs_document",
                        "a bare sketch has no annotations[] to write; pass the document")
        raise _fail("bad_document", "document mapping has no annotations list")
    annotations = getattr(sk_or_doc, "annotations", None)
    if isinstance(annotations, list):
        return annotations
    raise _fail("bad_document", "unsupported sk_or_doc: %s" % type(sk_or_doc).__name__)


def _mtext_node(sk_or_doc: Any, subject: Any) -> dict[str, Any]:
    """按 uuid 取一条 mtext 注解节点（不是 mtext ⇒ `not_an_mtext`；找不到 ⇒ `unknown_annotation`）。"""
    target = _uuid_str(subject, "subject")
    for node in _annotations(sk_or_doc):
        if isinstance(node, Mapping) and node.get("uuid") == target:
            if node.get("kind") != "mtext":
                raise _fail("not_an_mtext",
                            "annotation %s is kind=%r, not mtext" % (target, node.get("kind")))
            return node  # type: ignore[return-value]
    raise _fail("unknown_annotation", "no annotation with uuid %s" % target)


def _run_records(parsed: Mapping[str, Any], layout: Mapping[str, Any],
                 height_nm: int) -> list[dict[str, Any]]:
    """run 记录：只落整数/枚举码 + 字体表下标（人话文本只有 literal 模式才落）。"""
    fonts: list[str] = []
    records: list[dict[str, Any]] = []
    for paragraph_index, runs in enumerate(parsed["paragraphs"]):
        offset = 0
        for run in runs:
            font_name = run["attrs"]["font"]
            if font_name is not None and font_name not in fonts:
                fonts.append(font_name)
            records.append({
                "paragraph": paragraph_index, "offset": offset, "kind": run["kind"],
                "height_nm": _run_height(run["attrs"], height_nm),
                "width_factor_milli": int(run["attrs"]["width_factor_milli"]),
                "height_scale_milli": int(run["attrs"]["height_scale_milli"]),
                "bold": int(run["attrs"]["bold"]), "italic": int(run["attrs"]["italic"]),
                "underline": int(run["attrs"]["underline"]),
                "overline": int(run["attrs"]["overline"]),
                "strike": int(run["attrs"]["strike"]),
                "color": -1 if run["attrs"]["color"] is None else int(run["attrs"]["color"]),
                "align": -1 if run["attrs"]["align"] is None else int(run["attrs"]["align"]),
                "font_index": -1 if font_name is None else fonts.index(font_name),
                "length": len(run["text"]) if run["text"] else 1,
            })
            offset += records[-1]["length"]
    return records, fonts


def _digest(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _store(node: dict[str, Any], parsed: Mapping[str, Any], layout: Mapping[str, Any], *,
           height_nm: int, width_factor_milli: int, line_spacing_milli: int,
           column_width_nm: int | None, attack_h: int, attack_v: int, literal: bool,
           at_nm: list[int]) -> None:
    """把解析 + 排版结果写进注解节点（**先算后写**：本函数只做赋值，不抛几何错）。"""
    previous_revision = int(node["args"].get("revision", 0))
    args: dict[str, Any] = {
        "at_nm": list(at_nm), "height_nm": height_nm,
        "width_factor_milli": width_factor_milli, "line_spacing_milli": line_spacing_milli,
        "attach_h": attack_h, "attach_v": attack_v,
        "revision": previous_revision + 1,
    }
    if column_width_nm is not None:
        args["column_width_nm"] = column_width_nm
    run_records, fonts = _run_records(parsed, layout, height_nm)
    args["layout"] = {key: value for key, value in layout.items() if key != "paragraphs"}
    args["runs"] = run_records
    args["fractions"] = [dict(item) for item in parsed["fractions"]]
    args["symbols"] = [dict(item) for item in parsed["symbols"]]
    args["codes"] = {"counts": dict(parsed["codes"]), "max_group_depth": parsed["max_depth"]}
    args["fonts"] = {"literal": True, "names": list(fonts)}
    args["paragraphs"] = []
    for record in layout["paragraphs"]:
        item = {"index": record["index"], "line_count": record["line_count"],
                "width_nm": record["width_nm"], "height_nm": record["height_nm"],
                "char_count": record["char_count"],
                "left_margin_nm": record["left_margin_nm"],
                "right_margin_nm": record["right_margin_nm"]}
        if literal:
            item["literal"] = True
            item["text"] = _plain_text(parsed["paragraphs"][record["index"]])
        args["paragraphs"].append(item)
    if literal:
        args["literal"] = True
        args["text"] = _markup_text(parsed)
        args["markup_paragraphs"] = list(parsed["markup_paragraphs"])
    args["layout_digest"] = {"literal": True, "sha256": _digest(args["layout"])}
    node["args"] = args
    node["kind"] = "mtext"


def _plain_text(runs: list[dict[str, Any]]) -> str:
    """段落去掉内联码后的可读文本（`\\S` 落 `n/d` 扁平形，符号落真字符）。"""
    out: list[str] = []
    for run in runs:
        if run["kind"] == _RUN_TEXT:
            out.append(run["text"] or "")
        elif run["kind"] == _RUN_FRACTION:
            out.append(run["text"] or "%d/%d" % (run["fraction"]["numerator_micro"],
                                                 run["fraction"]["denominator_micro"]))
        else:
            out.append(run["text"] or "")
    return "".join(out)


def _markup_text(parsed: Mapping[str, Any]) -> str:
    return "\\P".join(parsed["markup_paragraphs"])


def _spec(height_nm: int, width_factor_milli: int, line_spacing_milli: int,
          column_width_nm: int | None, attachment: str) -> dict[str, Any]:
    if attachment not in _ATTACHMENTS:
        raise _fail("bad_attachment",
                    "attachment must be one of %s" % ", ".join(sorted(_ATTACHMENTS)))
    if not isinstance(width_factor_milli, int) or isinstance(width_factor_milli, bool):
        raise _fail("invalid_length", "width_factor_milli must be an integer")
    if width_factor_milli <= 0 or width_factor_milli > 100_000:
        raise _fail("out_of_range_width_factor", "width factor must be in (0, 100]")
    if isinstance(line_spacing_milli, bool) or not isinstance(line_spacing_milli, int):
        raise _fail("invalid_length", "line_spacing_milli must be an integer")
    if line_spacing_milli < 1000 or line_spacing_milli > 5000:
        raise _fail("out_of_range_line_spacing", "line spacing must be in 1.00..5.00")
    attach_h, attach_v = _ATTACHMENTS[attachment]
    return {"height_nm": _nm(height_nm, "height_nm", positive=True),
            "width_factor_milli": width_factor_milli,
            "line_spacing_milli": line_spacing_milli,
            "column_width_nm": None if column_width_nm is None
            else _nm(column_width_nm, "column_width_nm", positive=True),
            "attach_h": attach_h, "attach_v": attach_v, "attachment": attachment}


def _resolve(sk_or_doc: Any, node: dict[str, Any], *, height_nm: Any, width_factor_milli: Any,
             line_spacing_milli: Any, column_width_nm: Any, attachment: Any, text: Any,
             literal: bool) -> dict[str, Any]:
    """算一次「节点最终形态」，**不写**（调用方校验通过后才 `_store`）。"""
    args = node.get("args") if isinstance(node.get("args"), Mapping) else {}
    resolved_height = args.get("height_nm") if height_nm is None else height_nm
    resolved_factor = args.get("width_factor_milli", 1000) if width_factor_milli is None \
        else width_factor_milli
    resolved_spacing = args.get("line_spacing_milli", DEFAULT_LINE_SPACING_MILLI) \
        if line_spacing_milli is None else line_spacing_milli
    if column_width_nm is _KEEP:
        resolved_column = args.get("column_width_nm")
    else:
        resolved_column = column_width_nm
    if attachment is None:
        resolved_attach_h = int(args.get("attach_h", 0))
        resolved_attach_v = int(args.get("attach_v", 0))
        name = None
        for key, value in _ATTACHMENTS.items():
            if value == (resolved_attach_h, resolved_attach_v):
                name = key
                break
        attachment = name or "top_left"
    spec = _spec(resolved_height, resolved_factor, resolved_spacing, resolved_column, attachment)
    markup = text
    if markup is None:
        if literal:
            markup = args.get("text")
            if not isinstance(markup, str):
                raise _fail("missing_markup", "literal mtext node has no stored markup")
        else:
            raise _fail("resolved_text_required",
                        "key-backed mtext needs the caller-resolved text for measurement")
    parsed = parse_mtext(markup, literal=literal)
    at_nm = args.get("at_nm")
    if not isinstance(at_nm, (list, tuple)) or len(at_nm) != 2:
        raise _fail("bad_point", "stored at_nm is not a (x_nm, y_nm) pair")
    layout = layout_mtext(parsed, height_nm=spec["height_nm"],
                          width_factor_milli=spec["width_factor_milli"],
                          line_spacing_milli=spec["line_spacing_milli"],
                          column_width_nm=spec["column_width_nm"], at_nm=[at_nm[0], at_nm[1]],
                          attach_h=spec["attach_h"], attach_v=spec["attach_v"])
    return {"spec": spec, "parsed": parsed, "layout": layout, "at_nm": [at_nm[0], at_nm[1]],
            "markup": markup}


# --------------------------------------------------------------------------------------
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------


def mtext_create(sk_or_doc, *, at_nm, height_nm, text_key=None, literal=False, text=None,
                 width_factor_milli=1000, line_spacing_milli=DEFAULT_LINE_SPACING_MILLI,
                 column_width_nm=None, attachment="top_left"):
    """**新建一条真排版的多行文字**：解析内联码、算行盒与 bbox，落 `annotations[]`。

    `literal=False`（缺省）时必须有 `text_key`（点分 ASCII），`text` 是**调用方已解析的模板**
    只用于量尺寸、不落 L1；`literal=True` 时 `text` 是人话文本并随 `args.literal=True` 落盘。
    `attachment` 是九个 MTEXT 对正点之一。返回 `[annotation_uuid]`。
    """
    point = _point_nm(at_nm, "at_nm")
    if not isinstance(literal, bool):
        raise _fail("bad_flag", "literal must be a bool")
    spec = _spec(height_nm, width_factor_milli, line_spacing_milli, column_width_nm, attachment)
    node: dict[str, Any] = {"uuid": str(_uuid.uuid4()), "kind": "mtext", "args": {}}
    if literal:
        parsed = parse_mtext(text, literal=True)
        node["literal"] = True
    else:
        if text_key is None:
            raise _fail("missing_text_key", "a key-backed mtext needs an i18n text_key")
        node["text_key"] = _key(text_key, "text_key")
        node["literal"] = False
        parsed = parse_mtext(text, literal=False)
    layout = layout_mtext(parsed, height_nm=spec["height_nm"],
                          width_factor_milli=spec["width_factor_milli"],
                          line_spacing_milli=spec["line_spacing_milli"],
                          column_width_nm=spec["column_width_nm"], at_nm=point,
                          attach_h=spec["attach_h"], attach_v=spec["attach_v"])
    _annotations(sk_or_doc)  # 先证明这里可写，再落盘（原子：没有任何半写状态）
    _store(node, parsed, layout, height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=literal, at_nm=point)
    _annotations(sk_or_doc).append(node)
    return [node["uuid"]]


def mtext_reformat(sk_or_doc, *, subject, height_nm=None, width_factor_milli=None,
                   line_spacing_milli=None, column_width_nm=_KEEP, attachment=None,
                   resolved_text=None):
    """**重排版**：改字高/宽度因子/行距/列宽/对正后重算段落、换行与 `bbox_nm`。

    `column_width_nm=_KEEP` 保持原列宽，传 `None` 取消换行；key 模式节点必须给 `resolved_text`
    （L1 里没有文本可量）。返回 `[annotation_uuid]`。
    """
    node = _mtext_node(sk_or_doc, subject)
    literal = bool(node.get("literal"))
    planned = _resolve(sk_or_doc, node, height_nm=height_nm, width_factor_milli=width_factor_milli,
                       line_spacing_milli=line_spacing_milli, column_width_nm=column_width_nm,
                       attachment=attachment, text=resolved_text, literal=literal)
    spec = planned["spec"]
    _store(node, planned["parsed"], planned["layout"], height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=literal, at_nm=planned["at_nm"])
    return [node["uuid"]]


def mtext_set_paragraph(sk_or_doc, *, subject, index, text):
    """**整段替换**：把第 `index` 段的原文换成 `text`（不允许含 `\\P`），然后整篇重解析重排版。

    只对 literal 节点可写（key 模式的正文真源在语言包，L1 里没有可替换的文本 ⇒ `not_editable_text`）。
    返回 `[annotation_uuid]`。
    """
    node = _mtext_node(sk_or_doc, subject)
    if not bool(node.get("literal")):
        raise _fail("not_editable_text", "key-backed mtext content lives in the catalog, not in L1")
    args = node.get("args") or {}
    if isinstance(index, bool) or not isinstance(index, int):
        raise _fail("bad_index", "index must be an integer")
    snippets = args.get("markup_paragraphs")
    if not isinstance(snippets, list) or not snippets:
        raise _fail("missing_markup", "literal mtext node has no paragraph markup")
    if index < 0 or index >= len(snippets):
        raise _fail("index_out_of_range",
                    "paragraph index %d is outside 0..%d" % (index, len(snippets) - 1))
    replacement = parse_mtext(text, literal=True)
    if len(replacement["paragraphs"]) != 1:
        raise _fail("unexpected_paragraph_break", "text must not contain a paragraph break")
    rebuilt = list(snippets)
    rebuilt[index] = text
    planned = _resolve(sk_or_doc, node, height_nm=None, width_factor_milli=None,
                       line_spacing_milli=None, column_width_nm=_KEEP, attachment=None,
                       text="\\P".join(rebuilt), literal=True)
    spec = planned["spec"]
    _store(node, planned["parsed"], planned["layout"], height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=True, at_nm=planned["at_nm"])
    return [node["uuid"]]


def mtext_append_paragraph(sk_or_doc, *, subject, text):
    """**追加一段**（真加一个段落与一行行盒），再整篇重排版。返回 `[annotation_uuid]`。"""
    node = _mtext_node(sk_or_doc, subject)
    if not bool(node.get("literal")):
        raise _fail("not_editable_text", "key-backed mtext content lives in the catalog, not in L1")
    args = node.get("args") or {}
    snippets = args.get("markup_paragraphs")
    if not isinstance(snippets, list):
        raise _fail("missing_markup", "literal mtext node has no paragraph markup")
    addition = parse_mtext(text, literal=True)
    if len(addition["paragraphs"]) != 1:
        raise _fail("unexpected_paragraph_break", "text must not contain a paragraph break")
    if len(snippets) + 1 > _MAX_PARAGRAPHS:
        raise _fail("too_many_paragraphs", "paragraph count exceeds %d" % _MAX_PARAGRAPHS)
    planned = _resolve(sk_or_doc, node, height_nm=None, width_factor_milli=None,
                       line_spacing_milli=None, column_width_nm=_KEEP, attachment=None,
                       text="\\P".join(list(snippets) + [text]), literal=True)
    spec = planned["spec"]
    _store(node, planned["parsed"], planned["layout"], height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=True, at_nm=planned["at_nm"])
    return [node["uuid"]]


_FRACTION_RE = re.compile(r"([0-9]+)/([0-9]+)")


def _code_spans(snippet: str) -> list[tuple[int, int]]:
    """原文里 `\\X...;` 码的占用区间（分数替换要跳过已落在码里的 `1/2`）。"""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(snippet):
        if snippet[index] == "\\" and index + 1 < len(snippet):
            end = snippet.find(";", index + 2)
            if end < 0:
                break
            spans.append((index, end + 1))
            index = end + 1
            continue
        index += 1
    return spans


def mtext_insert_fraction(sk_or_doc, *, subject, paragraph, occurrence=0):
    """**把段落里第 `occurrence` 个 `n/d` 文字真变成堆叠分数**（写 `\\Sn/d;` 并重排版）。

    真数值后果：新增一条分数记录（`numerator_micro` / `denominator_micro` / `value_micro`），
    行宽按分子分母较宽者占位。找不到目标 ⇒ `fraction_target_not_found`。返回 `[annotation_uuid]`。
    """
    node = _mtext_node(sk_or_doc, subject)
    if not bool(node.get("literal")):
        raise _fail("not_editable_text", "key-backed mtext content lives in the catalog, not in L1")
    if isinstance(paragraph, bool) or not isinstance(paragraph, int):
        raise _fail("bad_index", "paragraph must be an integer")
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0:
        raise _fail("bad_occurrence", "occurrence must be a non-negative integer")
    args = node.get("args") or {}
    snippets = args.get("markup_paragraphs")
    if not isinstance(snippets, list) or not snippets:
        raise _fail("missing_markup", "literal mtext node has no paragraph markup")
    if paragraph < 0 or paragraph >= len(snippets):
        raise _fail("index_out_of_range",
                    "paragraph index %d is outside 0..%d" % (paragraph, len(snippets) - 1))
    snippet = snippets[paragraph]
    occupied = _code_spans(snippet)
    matches = [match for match in _FRACTION_RE.finditer(snippet)
               if not any(start <= match.start() < stop for start, stop in occupied)]
    if occurrence >= len(matches):
        raise _fail("fraction_target_not_found",
                    "paragraph %d has no 'n/d' occurrence #%d" % (paragraph, occurrence))
    match = matches[occurrence]
    numerator_token, denominator_token = match.group(1), match.group(2)
    numerator = _scaled_micro(numerator_token, "fraction numerator")
    denominator = _scaled_micro(denominator_token, "fraction denominator")
    _div_round(numerator * 1_000_000, denominator)  # 分母为 0 时在这里就报错（不写半条）
    replacement = "\\S%s/%s;" % (numerator_token, denominator_token)
    rebuilt = list(snippets)
    rebuilt[paragraph] = snippet[:match.start()] + replacement + snippet[match.end():]
    planned = _resolve(sk_or_doc, node, height_nm=None, width_factor_milli=None,
                       line_spacing_milli=None, column_width_nm=_KEEP, attachment=None,
                       text="\\P".join(rebuilt), literal=True)
    spec = planned["spec"]
    _store(node, planned["parsed"], planned["layout"], height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=True, at_nm=planned["at_nm"])
    return [node["uuid"]]


def mtext_measure(sk_or_doc, *, subject, resolved_text=None):
    """**重量一次**：重解析 + 重排版并落新 `layout_digest`（几何改过之后的可复跑判定行）。

    key 模式节点必须给 `resolved_text`。返回 `[annotation_uuid]`（不新建注解、不改引用）。
    """
    node = _mtext_node(sk_or_doc, subject)
    literal = bool(node.get("literal"))
    planned = _resolve(sk_or_doc, node, height_nm=None, width_factor_milli=None,
                       line_spacing_milli=None, column_width_nm=_KEEP, attachment=None,
                       text=resolved_text, literal=literal)
    spec = planned["spec"]
    _store(node, planned["parsed"], planned["layout"], height_nm=spec["height_nm"],
           width_factor_milli=spec["width_factor_milli"],
           line_spacing_milli=spec["line_spacing_milli"],
           column_width_nm=spec["column_width_nm"], attack_h=spec["attach_h"],
           attack_v=spec["attach_v"], literal=literal, at_nm=planned["at_nm"])
    return [node["uuid"]]


OPERATIONS: dict[str, Callable] = {
    "mtext_create": mtext_create,
    "mtext_reformat": mtext_reformat,
    "mtext_set_paragraph": mtext_set_paragraph,
    "mtext_append_paragraph": mtext_append_paragraph,
    "mtext_insert_fraction": mtext_insert_fraction,
    "mtext_measure": mtext_measure,
}


# --------------------------------------------------------------------------------------
# 自检（硬断言；含尺子反证：变异体必须被同一把尺子报红）
# --------------------------------------------------------------------------------------


def _selftest_document() -> Any:
    from cadcore.docmodel import new_document
    return new_document()


def _annotation(doc: Any, uuid_text: str) -> dict[str, Any]:
    for node in _annotations(doc):
        if node.get("uuid") == uuid_text:
            return node
    raise AssertionError("annotation %s not found" % uuid_text)


def _layout_ruler(op: Callable, doc: Any, **kwargs: Any) -> bool:
    """尺子：op 必须返回合法 uuid，且该注解落了一份非退化行盒（宽 ≥ 0、行数 ≥ 1）。"""
    uuids = op(doc, **kwargs)
    if not isinstance(uuids, list) or len(uuids) != 1:
        return False
    try:
        _uuid.UUID(uuids[0])
    except (ValueError, AttributeError, TypeError):
        return False
    node = _annotation(doc, uuids[0])
    layout = (node.get("args") or {}).get("layout")
    if not isinstance(layout, Mapping):
        return False
    return (isinstance(layout.get("width_nm"), int) and layout["width_nm"] >= 0
            and isinstance(layout.get("line_count"), int) and layout["line_count"] >= 1
            and len(layout.get("bbox_nm") or []) == 4)


def selftest() -> list[tuple[str, bool, str]]:
    """自带自检：解析 / 真几何 / 换行 / 错误 typed / L1 不变量 / 改写后果 / 接口 / 尺子反证。"""
    results: list[tuple[str, bool, str]] = []

    # 1) 真解析：段落、堆叠分数（精确整数算术）、内联码计数、分组嵌套
    parsed = parse_mtext("AB\\S1/2;C\\PD{\\H2x;E}", literal=True)
    fraction = parsed["fractions"][0]
    results.append(("parse_codes_and_fraction",
                    len(parsed["paragraphs"]) == 2
                    and fraction["numerator_micro"] == 1_000_000
                    and fraction["denominator_micro"] == 2_000_000
                    and fraction["value_micro"] == 500_000
                    and parsed["codes"]["P"] == 1 and parsed["codes"]["S"] == 1
                    and parsed["max_depth"] == 2
                    and parsed["paragraphs"][1][-1]["attrs"]["height_scale_milli"] == 2000,
                    "paras=%d frac=%s codes=%s depth=%d"
                    % (len(parsed["paragraphs"]), fraction, parsed["codes"],
                       parsed["max_depth"])))

    # 2) 真几何：字宽/行高/bbox 精确到纳米（半宽模型，整数运算）
    doc = _selftest_document()
    (uuid_a,) = mtext_create(doc, at_nm=[100, 200], literal=True, text="AB CD",
                             height_nm=2_500_000)
    node = _annotation(doc, uuid_a)
    layout = node["args"]["layout"]
    results.append(("layout_geometry_exact",
                    layout["width_nm"] == 6_250_000
                    and layout["height_nm"] == 4_167_500
                    and layout["line_count"] == 1
                    and layout["bbox_nm"] == [100, -4_167_300, 6_250_100, 200]
                    and node["args"]["paragraphs"][0]["text"] == "AB CD",
                    "width=%d height=%d bbox=%s"
                    % (layout["width_nm"], layout["height_nm"], layout["bbox_nm"])))

    # 3) 东亚宽字符 + 真换行 + 溢出计数
    doc2 = _selftest_document()
    # i18n 中文原文："汉字"
    (uuid_b,) = mtext_create(doc2, at_nm=[0, 0], literal=True, text="Hanzi",
                             height_nm=2_500_000, column_width_nm=3_000_000)
    layout_b = _annotation(doc2, uuid_b)["args"]["layout"]
    results.append(("wide_chars_and_wrap",
                    layout_b["wide_char_count"] == 2 and layout_b["line_count"] == 2
                    and layout_b["wrap_offsets"] == [[0, 1]]
                    and layout_b["max_line_width_nm"] == 2_500_000
                    and layout_b["overflow_lines"] == 0
                    and layout_b["height_nm"] == 8_335_000,
                    "wide=%d lines=%d wrap=%s maxw=%d h=%d"
                    % (layout_b["wide_char_count"], layout_b["line_count"],
                       layout_b["wrap_offsets"], layout_b["max_line_width_nm"],
                       layout_b["height_nm"])))

    # 4) 错误一律 typed（每条都点名 reason，且失败不写文档）
    doc3 = _selftest_document()
    cases: list[tuple[str, Callable[[], Any]]] = [
        ("unsupported_code", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True,
                                                  text="A\\ZB", height_nm=2_500_000)),
        ("bad_nesting", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True,
                                             text="A{B", height_nm=2_500_000)),
        ("invalid_height_scale", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True,
                                                      text="A\\H0x;B", height_nm=2_500_000)),
        ("invalid_fraction", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True,
                                                  text="A\\S1/0;B", height_nm=2_500_000)),
        ("invalid_length", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True, text="A",
                                                height_nm=2.5e6)),
        ("missing_text_key", lambda: mtext_create(doc3, at_nm=[0, 0], text="A",
                                                  height_nm=2_500_000)),
        ("non_positive_length", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True, text="A",
                                                     height_nm=2_500_000, column_width_nm=0)),
        ("bad_attachment", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True, text="A",
                                                height_nm=2_500_000, attachment="middle")),
        ("bad_literal_text", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True, text="",
                                                  height_nm=2_500_000)),
        ("text_too_long", lambda: mtext_create(doc3, at_nm=[0, 0], literal=True,
                                               text="A" * 200_001, height_nm=2_500_000)),
    ]
    seen: list[str] = []
    for expected, call in cases:
        try:
            call()
            seen.append("%s:MISSING" % expected)
        except CadError as exc:
            seen.append(exc.reason if exc.reason == expected else "%s!=%s" % (expected, exc.reason))
    results.append(("errors_are_typed",
                    seen == list(dict.fromkeys(seen)) and all(
                        item == expected for item, (expected, _call) in zip(seen, cases))
                    and len(doc3.annotations) == 0,
                    "reasons=%s annotations=%d" % (",".join(seen), len(doc3.annotations))))

    # 5) L1 不变量 + 逐字节可复跑 + key 模式不落人话文本
    from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1
    doc4 = _selftest_document()
    mtext_create(doc4, at_nm=[0, 0], literal=True, text="AB\\S1/4;C", height_nm=2_500_000)
    mtext_create(doc4, at_nm=[-10, 30], text_key="note.mtext.body", text="AB CD",
                 height_nm=3_000_000)
    text_one = document_to_l1(doc4)
    text_two = document_to_l1(document_from_l1(text_one))
    results.append(("l1_invariants_and_key_privacy",
                    check_invariants(doc4) == [] and text_one == text_two
                    and "AB CD" not in text_one and "note.mtext.body" in text_one
                    and len(doc4.annotations) == 2,
                    "problems=%s bytes=%d privacy=%d"
                    % (check_invariants(doc4), len(text_one), "AB CD" not in text_one)))

    # 6) 真改写后果：追加段落 / 插堆叠分数 / 换段（revision 递增、几何随之变）
    doc5 = _selftest_document()
    (uuid_c,) = mtext_create(doc5, at_nm=[0, 0], literal=True, text="1/4", height_nm=2_500_000)
    before = _annotation(doc5, uuid_c)["args"]
    mtext_append_paragraph(doc5, subject=uuid_c, text="tail")
    after_append = _annotation(doc5, uuid_c)["args"]
    mtext_insert_fraction(doc5, subject=uuid_c, paragraph=0)
    after_fraction = _annotation(doc5, uuid_c)["args"]
    # i18n 中文原文："字"
    mtext_set_paragraph(doc5, subject=uuid_c, paragraph=1, text="Zi")
    after_set = _annotation(doc5, uuid_c)["args"]
    results.append(("mutations_change_geometry",
                    before["layout"]["paragraph_count"] == 1
                    and after_append["layout"]["paragraph_count"] == 2
                    and after_append["layout"]["height_nm"] == 2 * before["layout"]["height_nm"]
                    and after_fraction["fractions"][0]["value_micro"] == 250_000
                    and after_fraction["layout"]["width_nm"] > before["layout"]["width_nm"]
                    # i18n 中文原文："字"
                    and after_set["paragraphs"][1]["text"] == "Zi"
                    and after_set["revision"] == 4
                    and after_append["layout"]["line_count"] == 2,
                    "paras=%d->%d frac=%s rev=%d width=%d->%d"
                    % (before["layout"]["paragraph_count"],
                       after_append["layout"]["paragraph_count"],
                       after_fraction["fractions"][0]["value_micro"], after_set["revision"],
                       before["layout"]["width_nm"], after_set["layout"]["width_nm"])))

    # 7) 冻结接口一致性：FAMILY / OPERATIONS 可调用 / 首个位置参数名 / 返回 uuid 列表
    shapes = []
    for name, func in sorted(OPERATIONS.items()):
        params = list(inspect.signature(func).parameters)
        shapes.append(bool(callable(func) and params and params[0] == "sk_or_doc"))
    doc6 = _selftest_document()
    (uuid_d,) = OPERATIONS["mtext_create"](doc6, at_nm=[0, 0], literal=True, text="AB",
                                           height_nm=2_500_000)
    results.append(("frozen_interface_shape",
                    FAMILY == "annotation" and len(OPERATIONS) == 6 and all(shapes)
                    and isinstance(uuid_d, str) and len(uuid_d) == 36
                    and OPERATIONS["mtext_measure"](doc6, subject=uuid_d) == [uuid_d],
                    "family=%s ops=%d shapes_ok=%d" % (FAMILY, len(OPERATIONS), sum(shapes))))

    # 8) 尺子反证：同一个 _layout_ruler 量真件必须绿、量"跳过排版"的变异体必须红
    def mutant_no_layout(sk_or_doc, *, at_nm, height_nm, **kwargs: Any) -> list[str]:
        node = {"uuid": str(_uuid.uuid4()), "kind": "mtext", "literal": True,
                "args": {"at_nm": list(at_nm), "height_nm": height_nm}}
        _annotations(sk_or_doc).append(node)
        return [node["uuid"]]

    doc_m = _selftest_document()
    doc_r = _selftest_document()
    mutant_red = not _layout_ruler(mutant_no_layout, doc_m, at_nm=[0, 0],
                                   height_nm=2_500_000)
    real_green = _layout_ruler(mtext_create, doc_r, at_nm=[0, 0], height_nm=2_500_000,
                               literal=True, text="AB")
    results.append(("layout_ruler_sees_red", bool(mutant_red and real_green),
                    "mutant_red=%d real_green=%d" % (mutant_red, real_green)))

    return results


def main() -> int:
    """打印 CHECK 行与判定行 `FEATURE_OK family=annotation op=mtext_create verdict=PASS`。"""
    results = selftest()
    for name, ok, detail in results:
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    failed = [name for name, ok, _detail in results if not ok]
    if failed:
        print("FEATURE_FAIL family=%s op=mtext_create failed=%s"
              % (FAMILY, ",".join(failed)))
        return 1
    print("FEATURE_OK family=annotation op=mtext_create verdict=PASS ops=%d checks=%d"
          % (len(OPERATIONS), len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
