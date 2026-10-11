"""freechat_vault —— 本地优先的聊天数据底座（Python 标准库实现，零第三方依赖）。

接口冻结见 `oss-alternatives/docs/design/impl-interfaces.md`；格式规范见 `docs/design/fcx-format.md`。
本文件由 Lead 维护，**实现席不要改**。
"""

from __future__ import annotations

__version__ = "0.1.0"
SCHEMA_VERSION = 1
FORMAT_VERSION = 1

__all__ = ["__version__", "SCHEMA_VERSION", "FORMAT_VERSION"]
