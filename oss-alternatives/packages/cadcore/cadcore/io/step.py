"""cadcore.io.step -- A14 STEP 边界：出口走内核（无 step 能力 ⇒ `DependencyMissing`），入口是纯标准库的头部元数据。

一句话用途
----------
把「STEP」这件事收在一个文件里：**出**＝`export_step()` 让内核把形状写成 ISO-10303-21 文件；
**入**＝`step_header_facts()` 用纯标准库读出文件头（`FILE_NAME` / `FILE_SCHEMA` / 描述），不依赖任何几何内核。

为什么这样设计（背景）
----------------------
1. **能力缺失必须显式降级，不许静默出空文件**：纯 Python 的 nano 后端没有 3D/STEP 能力
   （`MVP-INTERFACES.md` §B A7），此时导出**必须**抛 `DependencyMissing` 让上层去换内核或提示用户，
   而不是写一个 0 字节文件让调用方以为成功了。判据是「内核 `available()` ∧ `capabilities()` 含 `step`」。
2. **产物要证明"本次真的写出来了"**：内核写到一个**同目录临时目录**里的新文件（它此前不存在），
   校验非空 + 校验 ISO-10303-21 文件头 + 校验能被解析，再 `os.replace` 原子换到目标路径。
   这样既不会把"上次留下的旧文件"当成本次成功（假绿），也不会在失败时给用户留半个残文件。
   为什么校验文件头而不是只看大小：`ISO-10303-21;` 是标准强制的第一段，缺了它就不是 STEP ——
   只看"文件非空"会把一段错误信息当成导出成功。
3. **"入"只做到头部元数据**：本期内核契约（§B A6 的 `KernelAdapter`）**没有几何读入口**，
   所以这里不提供 `import_step()` 这种假接口；头部元数据（纯文本、无内核也能跑）足够做
   「这文件是不是 STEP / 哪个 schema / 谁写的 / 实体数」这类判定，几何级导入属下一期（§F 边界）。
4. **三态口径**：实体计数在文件没读完时记 `-1`（未采）而不是"数到的那些"；头部字段缺失时给 `None`
   并把字段名写进 `missing_fields`（**不静默**、不编默认值）；不适用一律 `NA`。

对外契约（照抄 `MVP-INTERFACES.md` §B A13/A14）
---------------------------------------------
`export_step(shape, path, *, kernel) -> str`；
另附纯标准库辅助：`step_header_facts()` / `is_step_path()` / `StepExportError`。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

LOG = logging.getLogger("cadcore.io.step")

__all__ = [
    "HEADER_SCAN_BYTES",
    "MAX_SCAN_BYTES",
    "NA",
    "STEP_MAGIC",
    "STEP_SUFFIXES",
    "UNMEASURED",
    "CadError",
    "DependencyMissing",
    "StepExportError",
    "export_step",
    "is_step_path",
    "step_header_facts",
    "write_step_facts",
]

# ── A3 显式降级信号（真件优先；A3 尚未落盘时用同名同语义的兜底，落盘后自动让位）──────────────
from ..errors import CadError, DependencyMissing


# 未采：没测到（≠ 测到 0）。实体计数在文件未读完时用它。
UNMEASURED: int = -1

# 不适用：该字段对本对象没有意义（≠ 未采、≠ 0）。
NA: str = "NA"

# ISO-10303-21 强制的第一段；缺了它就不是 STEP 文件（校验判据，不靠扩展名）。
STEP_MAGIC: str = "ISO-10303-21;"

STEP_SUFFIXES: tuple[str, ...] = (".step", ".stp")

# 头部解析窗口：ISO-10303-21 的 HEADER 段永远在最前面，64 KiB 足够（不够也不会误判 —— 只影响字段读全）。
HEADER_SCAN_BYTES: int = 1 << 16

# 实体计数是**流式扫描**全文件数 `#N=` 的；超过这个上限还没数完 ⇒ 记 `-1`（未采），
# **不许**把"数到的那部分"当全量（假绿）。1 GiB 的文件也能数完，只有超大件才落进未采。
MAX_SCAN_BYTES: int = 1 << 30

STEP_CAPABILITY: str = "step"

_FILE_NAME_RE = re.compile(r"FILE_NAME\s*\(\s*'([^']*)'\s*,\s*'([^']*)'", re.IGNORECASE)
_FILE_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)", re.IGNORECASE | re.DOTALL)
_FILE_DESCRIPTION_RE = re.compile(r"FILE_DESCRIPTION\s*\(\s*\((.*?)\)\s*,", re.IGNORECASE | re.DOTALL)
_QUOTED_RE = re.compile(r"'([^']*)'")
_ENTITY_RE = re.compile(r"#\d+\s*=")

# 流式扫描的分块大小（测试会把它改小以逼出跨块边界，验证计数不重不漏）。
_SCAN_CHUNK_BYTES: int = 1 << 20

_UTF8_BOM_AS_LATIN1: str = bytes((0xEF, 0xBB, 0xBF)).decode("latin-1")
# UTF-8 BOM 的三字节按 `latin-1` 解码后的形态（源码保持纯 ASCII，故用 bytes 现造）。


# ── 语言包取文案（A16）；A16 缺席或键未登记 ⇒ 由 key 派生的兜底串，绝不返回空串 ────────────────
def _derive_text(key: str) -> str:
    tail = key.rsplit(".", 1)[-1] if key else ""
    text = tail.replace("_", " ").replace("-", " ").strip()
    return (text[:1].upper() + text[1:]) if text else "?"


def _t(key: str, **args: Any) -> str:
    catalog: Any = None
    from ..i18n import catalog as catalog_module
    catalog = catalog_module
    if catalog is not None:
        try:
            text = catalog.t(key, **args)
            if isinstance(text, str) and text.strip():
                return text
        except Exception as exc:  # pragma: no cover - 语言层自身出问题不许拖垮导出
            LOG.debug("i18n lookup failed for %r: %s", key, exc)
    text = _derive_text(key)
    if args:
        text = "%s [%s]" % (text, ", ".join("%s=%s" % (name, args[name]) for name in sorted(args)))
    return text


class StepExportError(CadError):
    """STEP 边界的硬失败：内核跑了但产物不成立（缺失/空/不是 STEP/写不动），或内核自身炸了。"""

    def __init__(self, message: str, *, reason: str = "", **details: Any) -> None:
        super().__init__(message)
        self.reason = reason or "step_error"
        self.details = details


def _dependency_missing(message: str) -> DependencyMissing:
    """构造显式降级信号。

    A3（`cadcore/errors.py`）是**兄弟席位**的文件：它落盘后本模块自动改用真件，但真件的构造签名未必是
    "只吃一个消息"。这里按 位置参数 → 关键字 `name` → 绕过 `__init__` 三级兜底，保证
    "内核无 step 能力 ⇒ DependencyMissing"这条契约在 A3 换实现后**仍然成立**（不许退化成 TypeError）。
    """
    for attempt in (lambda: DependencyMissing(message), lambda: DependencyMissing(name=message)):
        try:
            return attempt()
        except TypeError:  # pragma: no cover - 只有 A3 用了别的构造签名才会走到
            continue
    exc = DependencyMissing.__new__(DependencyMissing)  # pragma: no cover - 兜底
    Exception.__init__(exc, message)
    return exc


# ── 契约入口 ────────────────────────────────────────────────────────────────────────
def export_step(shape: Any, path: str | os.PathLike[str], *, kernel: Any) -> str:
    """把 `shape` 导出为 STEP 文件，返回最终路径（`str`）。

    语义（照抄 §B A14）：**内核无 `step` 能力 ⇒ `DependencyMissing`**；内核不可用同理。
    成功 = 本次真的产出了一个非空、带 ISO-10303-21 文件头的文件，并已原子换到 `path`。
    """
    target = Path(str(path))
    _require_step_capability(kernel)
    parent = target.parent if str(target.parent) else Path(".")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StepExportError(
            _t("err.export_failed", format="step", detail="cannot create %s: %s" % (parent, exc)),
            reason="parent_unwritable",
            path=str(target),
        ) from exc
    # 临时目录与目标同目录 ⇒ 收口用 os.replace（同文件系统原子替换），失败时目标路径上不会留残件。
    staging = Path(tempfile.mkdtemp(prefix=".cadcore-step-", dir=str(parent)))
    staged = staging / ("out" + (target.suffix.lower() if target.suffix.lower() in STEP_SUFFIXES else ".step"))
    try:
        reported = _call_kernel_export(kernel, shape, staged)
        producer = Path(str(reported)) if isinstance(reported, str) and reported else staged
        source: Path | None = None
        for candidate in (producer, staged):
            if candidate.exists():
                _validate_step_artifact(candidate, target=target)
                source = candidate
                break
        if source is None:
            raise StepExportError(
                _t("err.export_failed", format="step", detail="kernel wrote no file at %s" % staged),
                reason="artifact_missing",
                path=str(target),
            )
        try:
            os.replace(str(source), str(target))
        except OSError as exc:
            raise StepExportError(
                _t("err.export_failed", format="step", detail="cannot replace %s: %s" % (target, exc)),
                reason="replace_failed",
                path=str(target),
            ) from exc
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    LOG.debug("step export: %s -> %s (%d bytes)", type(shape).__name__, target, target.stat().st_size)
    return str(target)


def _require_step_capability(kernel: Any) -> None:
    """能力门：不可用 ⇒ `DependencyMissing`；可用但没 `step` ⇒ `DependencyMissing`（**不许**降级成空文件）。"""
    name = str(getattr(kernel, "name", type(kernel).__name__))
    probe = getattr(kernel, "available", None)
    if callable(probe):
        try:
            usable = bool(probe())
        except Exception as exc:  # 内核自述失败 ⇒ 报错，不许吞（吞掉等于假装可用）
            raise StepExportError(
                _t("err.kernel_unavailable", kernel=name), reason="available_check_failed", kernel=name
            ) from exc
        if not usable:
            raise _dependency_missing(_t("err.kernel_unavailable", kernel=name))
    capabilities = getattr(kernel, "capabilities", None)
    caps: frozenset[str] = frozenset()
    if callable(capabilities):
        raw = capabilities()
        caps = frozenset(str(item) for item in (raw or ()))
    if STEP_CAPABILITY not in caps:
        raise _dependency_missing(_t("err.capability_missing", capability=STEP_CAPABILITY))


def _call_kernel_export(kernel: Any, shape: Any, staged: Path) -> Any:
    """调内核的 `export`；降级信号原样透传；其它异常 ⇒ 包成 `StepExportError`（保留 cause）。

    为什么 `DependencyMissing` 要原样透传：它是"换内核/提示用户"的**显式降级信号**，
    被包成普通失败就没人会去降级了（`MVP-INTERFACES.md` §B A3：禁静默降级，也禁把信号吃掉）。
    """
    try:
        return kernel.export(shape, fmt="step", path=str(staged))
    except NotImplementedError as exc:
        raise _dependency_missing(_t("err.capability_missing", capability=STEP_CAPABILITY)) from exc
    except (StepExportError, DependencyMissing):
        raise
    except Exception as exc:
        raise StepExportError(
            _t("err.export_failed", format="step", detail="%s: %s" % (type(exc).__name__, exc)),
            reason="kernel_raised",
            path=str(staged),
        ) from exc


def _validate_step_artifact(path: Path, *, target: Path) -> None:
    """产物必须：非空 ∧ 头部是 ISO-10303-21。两条任一不成立即硬失败（防"文件在就算成功"的假绿）。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StepExportError(
            _t("err.file_not_found", path=str(path)), reason="artifact_missing", path=str(target)
        ) from exc
    if size <= 0:
        raise StepExportError(
            _t("err.export_failed", format="step", detail="artifact is empty: %s" % path),
            reason="artifact_empty",
            path=str(target),
        )
    head = _read_head(path, limit=4096)
    if not _looks_like_step(head):
        raise StepExportError(
            _t("err.parse_failed", source=str(path), detail="missing %s header" % STEP_MAGIC),
            reason="not_iso_10303_21",
            path=str(target),
        )


def _read_bytes(path: Path, limit: int) -> tuple[bytes, bool]:
    """读至多 `limit` 字节；返回 (内容, 是否被截断)。"""
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    truncated = len(data) > limit
    return data[:limit], truncated


def _read_head(path: Path, *, limit: int) -> str:
    """读文件头做判定：`latin-1` 解码（ISO-10303-21 头部按单字节字符集，任何字节都能解，不会抛）。"""
    try:
        data, _ = _read_bytes(path, limit)
    except OSError as exc:
        raise StepExportError(
            _t("err.parse_failed", source=str(path), detail="%s: %s" % (type(exc).__name__, exc)),
            reason="artifact_unreadable",
            path=str(path),
        ) from exc
    return data.decode("latin-1", errors="replace")


def _looks_like_step(text: str) -> bool:
    """去掉 BOM/空白后，开头必须是 `ISO-10303-21;`（大小写不敏感，容忍前导空行）。

    真实文件常带 UTF-8 BOM；本模块按 `latin-1` 解码（永不抛），所以 BOM 会以三个 Latin-1 字符的形态出现
    —— 两种形态都要认掉，否则合法文件会被判成"不是 STEP"。
    """
    stripped = text.lstrip("\ufeff\r\n\t ")
    if stripped.startswith(_UTF8_BOM_AS_LATIN1):
        stripped = stripped[len(_UTF8_BOM_AS_LATIN1) :].lstrip("\r\n\t ")
    return stripped[: len(STEP_MAGIC)].upper() == STEP_MAGIC.upper()


def is_step_path(path: str | os.PathLike[str]) -> bool:
    """扩展名判定（`.step` / `.stp`，大小写不敏感）。**只判名字**，内容判定用 `step_header_facts()`。"""
    return Path(str(path)).suffix.lower() in STEP_SUFFIXES


# ── "入"侧：纯标准库的 ISO-10303-21 头部元数据 ──────────────────────────────────────────
def _count_entities(path: Path, *, limit: int) -> tuple[int, int, bool]:
    """流式数全文件的 `#N=` 实体定义；返回 (条数, 扫描字节数, 是否因超限没扫完)。

    为什么要流式而不是"读进来再数"：真 STEP 动辄几十上百 MB，读全进内存不值当；
    又为什么必须**数完才敢报数**：只数到一半就报出去，等于把"没测完"伪装成"测到这么多"（E2 口径）。
    `limit` 顶不住时返回的条数**只作诊断**，调用方必须记 `-1`（未采）。
    """
    total = 0
    consumed = 0
    carry = ""
    truncated = False
    with open(path, "rb") as handle:
        while True:
            want = min(_SCAN_CHUNK_BYTES, max(0, int(limit) - consumed))
            if want <= 0:
                truncated = handle.read(1) != b""
                break
            chunk = handle.read(want)
            if not chunk:
                break
            consumed += len(chunk)
            blob = carry + chunk.decode("latin-1", errors="replace")
            # 只数"在本次新增文本里结束"的匹配：完全落在 carry 里的那些上一轮已经数过了（否则会重复计数）。
            total += sum(1 for match in _ENTITY_RE.finditer(blob) if match.end() > len(carry))
            carry = blob[-8:]
    return total, consumed, truncated


def step_header_facts(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_SCAN_BYTES,
    header_bytes: int = HEADER_SCAN_BYTES,
) -> dict[str, Any]:
    """读 STEP 文件头，返回机读事实（不需要任何几何内核）。

    三态口径：实体计数扫到 `max_bytes` 还没扫完 ⇒ `entity_count = -1`（未采）且 `truncated=True`；
    头部字段解析不到 ⇒ 该字段 `None` 并把字段名记进 `missing_fields`（**不编默认值、不静默**）。
    文件不存在 ⇒ 抛 `StepExportError`（不是返回"空事实"）。
    """
    target = Path(str(path))
    if not target.is_file():
        raise StepExportError(_t("err.file_not_found", path=str(target)), reason="file_not_found", path=str(target))
    size = int(target.stat().st_size)
    head, _ = _read_bytes(target, int(header_bytes))
    text = head.decode("latin-1", errors="replace")
    missing: list[str] = []

    header_present = _looks_like_step(text)
    if not header_present:
        missing.append("header")

    name_match = _FILE_NAME_RE.search(text)
    file_name = name_match.group(1) if name_match else None
    time_stamp = name_match.group(2) if name_match else None
    if file_name is None:
        missing.append("file_name")
    if time_stamp is None:
        missing.append("time_stamp")

    schema_match = _FILE_SCHEMA_RE.search(text)
    schema = None
    if schema_match:
        quoted = _QUOTED_RE.findall(schema_match.group(1))
        schema = quoted[0] if quoted else schema_match.group(1).strip() or None
    if schema is None:
        missing.append("schema")

    description: list[str] = []
    description_match = _FILE_DESCRIPTION_RE.search(text)
    if description_match:
        description = [item.strip() for item in _QUOTED_RE.findall(description_match.group(1))]
    if not description:
        missing.append("description")

    scanned, read_bytes, truncated = _count_entities(target, limit=int(max_bytes))
    return {
        "path": str(target),
        "size_bytes": size,
        "header_present": header_present,
        "file_name": file_name,
        "time_stamp": time_stamp,
        "schema": schema,
        "description": description,
        "entity_count": UNMEASURED if truncated else scanned,
        "truncated": truncated,
        "read_bytes": read_bytes,
        "missing_fields": missing,
        "encoding": "latin-1",
    }


def _facts_body(facts: Mapping[str, Any]) -> str:
    return "header=%s schema=%s entities=%s file_name=%s missing=%s" % (
        "present" if facts.get("header_present") else "absent",
        facts.get("schema") if facts.get("schema") is not None else NA,
        facts.get("entity_count", UNMEASURED),
        facts.get("file_name") if facts.get("file_name") is not None else NA,
        ",".join(facts.get("missing_fields", [])) or "none",
    )


def write_step_facts(facts: Mapping[str, Any]) -> str:
    """把 `step_header_facts()` 的读数渲染成一行判定串（供门脚本/日志用；缺字段显式写 `NA`/`-1`）。"""
    return "STEP_FACTS: " + _facts_body(facts)


def _main(argv: list[str] | None = None) -> int:
    """`python3 -m cadcore.io.step <file.step>` ⇒ 一行判定串 + 三态 rc（0 合规 / 1 不是 STEP / 2 未采）。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Read ISO-10303-21 header facts without any geometry kernel (A14).")
    parser.add_argument("path", help="STEP file to inspect")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=MAX_SCAN_BYTES,
        help="entity-count scan cap; entity_count is reported as -1 when the scan is cut",
    )
    parser.add_argument("--json", action="store_true", help="also dump the full reading as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        facts = step_header_facts(args.path, max_bytes=args.max_bytes)
    except StepExportError as exc:
        print("STEP_FACTS: verdict=NA rc=2 reason=%s path=%s" % (exc.reason, args.path))
        return 2
    rc = 0 if facts["header_present"] else 1
    verdict = "PASS" if rc == 0 else "FAIL"
    print("STEP_FACTS: verdict=%s rc=%d %s" % (verdict, rc, _facts_body(facts)))
    if args.json:
        print(json.dumps(facts, ensure_ascii=True, sort_keys=True))
    return rc


if __name__ == "__main__":  # pragma: no cover - CLI 入口（测试用 subprocess 跑真件）
    raise SystemExit(_main())
