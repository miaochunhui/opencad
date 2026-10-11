"""dxf.writer —— `Document` → DXF ASCII 文本（**未修改的文档逐字节等于输入**）。

承重判据
--------
序列化**不是重新格式化**，而是把模型里那批 `Line` 原样拼回去：每行的文本与行尾都是读入时
记下的那一份，所以「读 → 写」在没有任何改动时**必然逐字节相等**。这条不是"希望"，
是自检：`Document.render_bytes()` 在未修改时会把自己的输出与 `raw_bytes` 对照，
不等就抛 `DxfError`（**不许静默写出一个"看起来像"的文件**）。

「修改过的文档只重写被改的实体」也是结构性的：改一个值只覆盖那一个 `Tag` 的**值行文本**
（`tag.value = …`），同一个实体的其它行、以及别的实体的每一行都还是原来的 `Line` 对象。
`writer.write()` 的返回里带上 `modified_entities` / `changed_line_count`，供尺子取证。

只依赖标准库；本模块**不碰** `packages/vault`。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any

from dxf.model import Document, DxfError

__all__ = ["dumps", "dump_bytes", "write", "write_report", "verify_byte_identity"]


def dumps(document: Document) -> str:
    """`Document` → 文本（含原来的行尾风格与 BOM）。"""
    return document.render_text()


def dump_bytes(document: Document) -> bytes:
    """`Document` → 字节（按 `document.encoding` 编码；编不回去就抛 `DxfError`）。"""
    return document.render_bytes()


def _changed_lines(document: Document) -> int:
    """与读入快照不同的物理行数（未修改 ⇒ 0）。"""
    if not document.lines:
        return 0
    current = [line.render() for line in document.lines]
    snapshot = list(document.raw_lines)
    if len(current) != len(snapshot):        # pragma: no cover - 本版不支持增删行
        return max(len(current), len(snapshot))
    return sum(1 for index, text in enumerate(current) if text != snapshot[index])


def write_report(document: Document, path: "str | os.PathLike | None" = None) -> dict:
    """本次写出的取证信息（不落盘；`write()` 的返回值就是它加上落盘字段）。"""
    payload = dump_bytes(document)
    report: dict[str, Any] = {
        "path": os.fspath(path) if path is not None else document.path,
        "bytes": len(payload),
        "encoding": document.encoding,
        "newline": document.newline_style,
        "modified": bool(document.modified),
        "modified_entities": len(document.modified_entities()),
        "changed_line_count": _changed_lines(document),
        "entities": document.entity_count(),
        "unknown_entities": len(document.unknown_entities()),
        "identical_to_input": bool(document.raw_bytes is not None and payload == document.raw_bytes),
        "sha256": document.sha256_render(),
    }
    return report


def write(document: Document, path: "str | os.PathLike", *, make_dirs: bool = True,
          atomic: bool = True) -> dict:
    """把 `Document` 写到 `path`（原子写：临时文件 + `os.replace`）。返回取证 dict。

    * **未修改** ⇒ 输出与读入字节完全相同（`identical_to_input=1`）；
    * **修改过** ⇒ 只有被改的那些行与输入不同（`changed_line_count` 报出来）；
    * 写不回去（编码表示不了新字符 / 自检不过）⇒ 抛 `DxfError`，**不落半份文件**。
    """
    target = os.fspath(path)
    payload = dump_bytes(document)              # 先算完整内容：算不出来就别碰盘
    directory = os.path.dirname(os.path.abspath(target)) or "."
    if make_dirs:
        os.makedirs(directory, exist_ok=True)
    if not atomic:
        with open(target, "wb") as handle:
            handle.write(payload)
    else:
        handle_fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-dxf-", suffix=".part")
        try:
            with os.fdopen(handle_fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    report = write_report(document, target)
    report["written"] = True
    report["atomic"] = bool(atomic)
    return report


def verify_byte_identity(document: Document) -> dict:
    """自检：把文档重渲染一遍，与读入字节对照（**只读**，不落盘）。

    `ok=1` 表示「读→写」逐字节相等；`raw_bytes` 为空（内存里造的文档）时 `ok=-1`
    —— 「没得比」不与「比过了相等」同形（AGENTS E2.3）。
    """
    if document.raw_bytes is None:
        return {"ok": -1, "reason": "没有输入字节可比（文档不是从文件读来的）",
                "bytes_in": -1, "bytes_out": len(dump_bytes(document))}
    payload = dump_bytes(document)
    return {
        "ok": 1 if payload == document.raw_bytes else 0,
        "reason": "" if payload == document.raw_bytes else "重渲染与原文字节不同",
        "bytes_in": len(document.raw_bytes),
        "bytes_out": len(payload),
        "sha256_in": document.sha256_input(),
        "sha256_out": document.sha256_render(),
    }
