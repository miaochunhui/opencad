#!/usr/bin/env python3
"""cadcore.i18n.keys —— **i18n key 形状的唯一真源**（"真源存 key，不存人话"）。

一句话用途
----------
判断一个值是不是合法的 i18n key（点分 ASCII 标识符、无空白、无汉字），以及校验失败时就地报错。
写侧（`sketch.commands.add_dimension` 的 `text_key`）与读侧（`docmodel.check_invariants` 的
`text_not_key`）判据必须**同源**：两边各写各的正则，迟早出现"写侧发得出去、读侧判它违规"
的静默分歧 —— 那正是 B6 缺陷 RC6（命令面能造出违反自己不变量、还能存盘的文档）的形态。

口径（冻死，改动即改语义）
--------------------------
- 合法：`^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)*$`（例：`dim.length.overall`、`doc.untitled`）；
- 非法：空串、纯空白、含空白/换行、含汉字（`[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]`）、
  以数字或点开头、LaTeX/引号等标点。
- **自由文本不走这条判据**：`literal=True` 的标注/尺寸存的是人话，读侧由 `check_invariants`
  自己按 `literal` 放行；本模块只管"该是 key 的字段"。

已知欠账（不在本文件解决）
--------------------------
同形正则在 `ops/` 下还有若干处历史副本（`annotation.py` / `annotation_extra.py` / `output.py` /
`organization.py` / `features/*`）。本文件只把**两条承重路径**（写侧入口 `add_dimension`、
读侧判据 `docmodel.check_invariants`）接上唯一真源；其余副本的合并需要各自回归，属另一件事。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["KEY_RE", "HAN_RE", "is_text_key"]

#: i18n key 形状：点分 ASCII 标识符。
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

#: 汉字区间（含扩展 A / 兼容表意文字）：人话文案里出现它就一定不是 key。
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def is_text_key(value: Any) -> bool:
    """`value` 是不是合法 i18n key（`bool`/`int`/`None` 一律 False，不抛异常）。"""
    if not isinstance(value, str) or not value:
        return False
    if HAN_RE.search(value) or any(ch.isspace() for ch in value):
        return False
    return bool(KEY_RE.match(value))
