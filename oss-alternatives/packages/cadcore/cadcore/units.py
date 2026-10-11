#!/usr/bin/env python3
"""A4 单位层：长度真源 = **整数纳米**。

为什么：浮点毫米当坐标真源会在布尔/重合判定里漂（0.1+0.2 类问题在 CAD 里表现为"本该重合的两点
差 1e-16"）。所以 L1 文档只存 `int` 纳米，浮点只出现在"显示/导出/用户输入"这两端。

口径：
- `format_length()` **不做 locale 猜测** —— 小数分隔符由制图标准档（`data/standards/*.json` 的
  `decimal_separator`）显式传入，界面语言与图纸格式是**正交**的两件事。
- `parse_length()` 同时接受 `.` 与 `,` 作小数点（欧洲用户手输 `12,5`）；两者同时出现时把 `,`
  当千分位。
"""

from __future__ import annotations

import math
from typing import Any

from .errors import CadError

__all__ = ["NM_PER_MM", "NM_PER_INCH", "mm_to_nm", "nm_to_mm", "inch_to_nm", "nm_to_inch",
           "unit_scale_nm", "format_length", "parse_length", "finite_number"]

NM_PER_MM = 1_000_000
NM_PER_INCH = 25_400_000
_UNITS = {"mm": NM_PER_MM, "inch": NM_PER_INCH, "in": NM_PER_INCH}


def unit_scale_nm(unit: str) -> int:
    """单位符号 → 每单位多少纳米；未知单位抛错（不静默按 mm 处理）。"""
    key = str(unit or "").strip().lower()
    if key not in _UNITS:
        raise CadError("unknown length unit", reason="unknown_unit", unit=unit, known=sorted(_UNITS))
    return _UNITS[key]


def finite_number(value: Any) -> float | None:
    """任意数值 → 有限 `float`；**不可表示**（超大整数 / NaN / ±inf / 非数值）⇒ `None`。

    为什么它是唯一真源（B6 实测缺陷 RC5）：`math.isfinite(float(v))` 这种"先转再判"的写法对 309 位整数
    **在判据执行之前**就抛 `OverflowError: int too large to convert to float` —— 参数校验自己成了裸内建
    异常的来源（同形实测 5 条：`scale.factor` / `rotate.angle_deg` / `add_arc.start_deg` /
    `snap_query.tolerance_nm` / `snap_query.point_nm`）。可表示性判断只此一份，调用点别再各写各的
    `float(v)`；`bool` 按老口径不算数（它是 `int` 子类，但 `True` 不是长度/角度）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except OverflowError:  # 10**309 这类整数：float 表示不了，越界而非"类型错"
        return None
    return out if math.isfinite(out) else None


def mm_to_nm(mm: float) -> int:
    """毫米 → 整数纳米（四舍五入到最近纳米；真源只存 int）。"""
    return int(round(float(mm) * NM_PER_MM))


def nm_to_mm(nm: int) -> float:
    """整数纳米 → 毫米（只用于显示/导出，不得回流当真源）。"""
    return int(nm) / NM_PER_MM


def inch_to_nm(inch: float) -> int:
    """英寸 → 整数纳米。"""
    return int(round(float(inch) * NM_PER_INCH))


def nm_to_inch(nm: int) -> float:
    """整数纳米 → 英寸。"""
    return int(nm) / NM_PER_INCH


def format_length(nm: int, *, unit: str = "mm", decimals: int = 2, decimal_separator: str = ".") -> str:
    """按文档单位与标准档小数分隔符渲染长度（不走 locale，见模块 docstring）。"""
    scale = unit_scale_nm(unit)
    text = f"{int(nm) / scale:.{max(0, int(decimals))}f}"
    if decimal_separator and decimal_separator != ".":
        text = text.replace(".", str(decimal_separator)[:1])
    return text


def parse_length(text: str, *, unit: str = "mm") -> int:
    """用户输入 → 纳米。同时接受 `12.5` 与 `12,5`；两者同时出现时 `,` 视为千分位。"""
    raw = str(text or "").strip().replace(" ", "")
    if not raw:
        raise CadError("empty length", reason="empty_length")
    for suffix in ("mm", "in", '"'):
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)]
            if suffix in ("in", '"'):
                unit = "inch"
            break
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError as exc:
        raise CadError("not a number", reason="not_a_number", text=text) from exc
    if not math.isfinite(value):
        raise CadError("length is not finite", reason="not_finite", text=text)
    return int(round(value * unit_scale_nm(unit)))
