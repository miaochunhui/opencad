#!/usr/bin/env python3
"""A3 错误族：全包共用的异常类型与"显式降级"信号。

背景：本模块落盘之前，`kernel/subproc.py`、`draw/sheet.py` 各自用 `try: from ..errors import ...`
加兜底类（见 subproc.py:85 / sheet.py:195）。本模块落盘后那些兜底自动让位 —— 全仓只有一份定义，
不会出现"两处同名异常互不捕获"的静默退化。

口径（与本包其它模块一致）：
- `DependencyMissing` = **显式降级信号**（可选依赖缺席）。它必须能被上层看见并转成 `NA`/`rc=2`，
  **绝不允许**被 `except Exception` 静默吞掉或换成空结果（那会把"没算"伪装成"没问题"）。
- 所有异常都带 `reason` 与 `details`，判定行/日志据此归因，不靠解析错误文案。

中文只出现在 docstring 里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"计为
`hardcoded_literals`，面向用户的文案一律走语言包（`cadcore.i18n.catalog`）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["CadError", "DependencyMissing", "GeometryError", "DocumentError", "I18nError", "error_types"]


class CadError(Exception):
    """本包所有可预期错误的基类：带机器可读的 `reason` 与 `details`。"""

    default_reason = "cad_error"

    def __init__(self, message: str, *, reason: str = "", **details: Any) -> None:
        super().__init__(str(message))
        self.message = str(message)
        self.reason = str(reason or self.default_reason or "")
        self.details: dict[str, Any] = dict(details)

    def as_dict(self) -> dict[str, Any]:
        """机读形态（判定行/日志用；`type` 与 `reason` 分开，别让下游去解析文案）。"""
        return {"type": type(self).__name__, "reason": self.reason,
                "message": self.message, "details": dict(self.details)}

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        if not self.details:
            return self.message
        pairs = ", ".join(f"{key}={value!r}" for key, value in sorted(self.details.items()))
        return f"{self.message} [{pairs}]"


class DependencyMissing(CadError):
    """可选依赖/能力缺席：显式降级信号，调用方必须处理成 NA 而不是继续跑。"""

    default_reason = "dependency_missing"


class GeometryError(CadError):
    """几何构造/求解失败（自交、无解、退化轮廓等）。"""

    default_reason = "geometry_error"


class DocumentError(CadError):
    """L1 文档层错误（schema 版本、不变量违规、迁移失败）。"""

    default_reason = "document_error"


class I18nError(CadError):
    """语言包层错误（目录缺失、键集合不一致、占位符不匹配）。"""

    default_reason = "i18n_error"


def error_types() -> dict[str, type[CadError]]:
    """名字 → 异常类（`draw/sheet.py` 的 `_fail(kind, ...)` 按名字取类，不硬编码 if 链）。"""
    return {"CadError": CadError, "DependencyMissing": DependencyMissing,
            "GeometryError": GeometryError, "DocumentError": DocumentError, "I18nError": I18nError}
