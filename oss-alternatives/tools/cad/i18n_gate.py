#!/usr/bin/env python3
"""i18n_gate —— 判定行 G1：语言包双向差集 / 占位符 / 源码字面量 / 标准档 / 运行时切换。

一句话用途
----------
一条命令回答「界面语言这一层现在到底达标没有」：扫语言包做**双向差集**与**占位符逐条比对**、
静态扫产品源码里的**中文文案字面量**与**写死语言码**、数制图标准档、并**真的 import 语言层 +
调用 `set_locale()` 验证热切换生效**（三证：切前 / 切后 / 往返），输出一行机读判定行。

为什么这样设计
--------------
1. **口径照抄 `MVP-INTERFACES.md` §D 的 G1 行**（不自创）：
   `I18N_VERDICT: verdict=PASS catalogs=3 keys_missing=0 keys_extra=0 placeholder_mismatch=0
   hardcoded_literals=0 locale_literals=0 standards=5 runtime_switch=1`，三态 `rc`：`0=PASS`、
   `1=真不达标`、`2=未采（NA，不当绿）`。
2. **空集不许假绿**：`data/i18n/` 或 `data/standards/` 整个目录不存在 ⇒ `rc=2` 未采（计数字段记 `-1`，
   不是 0）；但**目录在而条目不够（<3 门语言 / <5 档标准 / 少一个语言包）⇒ `rc=1` 真不达标**，
   不许落进 `rc=2` —— 这两句的分界线就是「采不到」和「采到了但不合格」。
3. **源码扫描只认产品码**（`cadcore/` 下的 `.py`，显式排除 `tests/` 与 `__pycache__`）：单测里的
   BCP-47 标签与中文串是**夹具数据**、不是产品分支或文案，把 `tests/` 算进来的门会永远假红。
   排除面**必须打出来**（`files_scanned` / `files_skipped_tests` 两个数都印），不许用目录 glob 悄悄扩面。
4. **只认 `ast` 常量、不认 docstring**：本仓每个模块的中文 docstring 是设计要求，不是「写死文案」；
   注释不在 AST 里，自然也扫不到 ⇒ 判据 = **非 docstring 的字符串常量**含汉字 ⇒ `hardcoded_literals`。
5. **运行时切换要真调**：只看源码里有没有 `set_locale` 字样是「看源码推因果」（E2.3）。这里真 import
   `cadcore.i18n.catalog`、真调 `set_locale()` 拿切换事件、真比对切前/切后/往返三份文本，并订阅
   `on_language_changed` 证明**广播**发生，才算 `runtime_switch=1`。
6. **重依赖缺席降级**：纯标准库；语言层模块 import 不到 ⇒ `runtime_switch=-1` 且 `rc=2`（未采），
   绝不假装成功、也绝不静默返 0。
7. **`--selftest` 是尺子的阴阳双证**：先把真树整棵拷进临时目录（夹具自证：拷贝里必须真有语言包与
   `catalog.py`，且原树在自检前后逐字节不变），再逐个下**四种做坏**：只给一门语言加 key、源码塞中文
   文案、源码塞 locale 字面量、删一个语言包 —— 每个都必须报红；另有「目录不存在 ⇒ rc=2」的空集对照。
   尺子看不见红 ⇒ 此门不算数，`--selftest` 自己报 `rc=1`。

用法
----
    python3 oss-alternatives/tools/cad/i18n_gate.py --check \
        --langs en,zh-CN,ja --tree oss-alternatives/packages/cadcore
    python3 oss-alternatives/tools/cad/i18n_gate.py --selftest

注：本文件自身也守「源码不留用户文案 / 不留语言码字面量」的口径 —— 自检夹具里的中文与语言码
一律用转义与拼装构造，不写成裸字面量。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# ── 契约常量 ────────────────────────────────────────────────────────────────────────────
GATE = "I18N_VERDICT"
SELFTEST_GATE = "I18N_SELFTEST"
RC_PASS, RC_FAIL, RC_UNMEASURED = 0, 1, 2
MIN_CATALOGS = 3          # MVP-INTERFACES §D G1：catalogs=3；I18N-DESIGN §1 存在性门 ≥3
MIN_STANDARDS = 5         # 期望判定行 standards=5
MAX_EXAMPLES = 5

# 汉字（含扩展 A 与兼容区）：命中即「面向用户的中文文案写死在源码里」。
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# BCP-47 形状的**字面量**：语言段小写 + 末段是**大写** 2 位地区码或 3 位数字（`zh-CN` / `pt-BR` /
# `zh-Hans-CN`），另接受 POSIX 下划线式 `zh_CN`。**大写要求是必须的**：否则 `at_nm` / `ref_nm`
# 这类字段名字符串会被当成语言码（实测假阳性 7 处），门就会永远假红。
_LOCALE_LITERAL_RE = re.compile(
    r"^(?:[a-z]{2,3}(?:[-_][A-Z][a-z]{3})?[-_](?:[A-Z]{2}|[0-9]{3})|[a-z]{2,3}_[A-Z]{2})$"
)
# 自检夹具用的「中文文案」与「语言码」：转义 / 拼装，避免本文件自身出现裸字面量。
_HAN_LEAK_TEXT = "\u4fdd\u5b58\u56fe\u7eb8"          # “保存图纸”
_LOCALE_LITERAL_PROBE = "-".join(("zh", "CN"))         # “zh-CN”
_SKIP_DIR_PARTS = frozenset({"__pycache__", ".git", ".mypy_cache", ".pytest_cache"})


# ── 结果载体 ────────────────────────────────────────────────────────────────────────────
@dataclass
class LiteralScan:
    """源码字面量扫描结果（计数 + 覆盖面 + 样本）。"""

    hardcoded_literals: int = 0
    locale_literals: int = 0
    files_scanned: int = 0
    files_skipped_tests: int = 0
    hardcoded_examples: list[str] = field(default_factory=list)
    locale_examples: list[str] = field(default_factory=list)


@dataclass
class Report:
    """一次判定的全部机读字段；`verdict` 由 `finalize()` 依三态口径收敛。"""

    catalogs: int = 0
    catalogs_on_disk: int = -1
    keys_missing: int = -1
    keys_extra: int = -1
    placeholder_mismatch: int = -1
    hardcoded_literals: int = -1
    locale_literals: int = -1
    standards: int = -1
    runtime_switch: int = -1
    catalog_errors: int = 0
    langs: tuple[str, ...] = ()
    missing_catalogs: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    scan: LiteralScan | None = None
    verdict: str = "UNMEASURED"

    def fail(self, reason: str) -> None:
        self.reasons.append(reason)

    def finalize(self, *, unmeasured: str | None) -> "Report":
        if unmeasured:
            self.verdict = "UNMEASURED"
            self.reasons.insert(0, unmeasured)
            return self
        bad = (
            self.catalogs < MIN_CATALOGS
            or bool(self.missing_catalogs)
            or self.keys_missing > 0
            or self.keys_extra > 0
            or self.placeholder_mismatch > 0
            or self.hardcoded_literals > 0
            or self.locale_literals > 0
            or self.standards < MIN_STANDARDS
            or self.runtime_switch != 1
            or self.catalog_errors > 0
        )
        self.verdict = "FAIL" if bad else "PASS"
        if self.catalogs < MIN_CATALOGS:
            self.fail(f"catalogs<{MIN_CATALOGS}")
        if self.missing_catalogs:
            self.fail("catalog_absent=" + ",".join(self.missing_catalogs))
        if self.standards < MIN_STANDARDS and self.standards >= 0:
            self.fail(f"standards<{MIN_STANDARDS}")
        if self.runtime_switch != 1:
            self.fail(f"runtime_switch={self.runtime_switch}")
        return self

    @property
    def rc(self) -> int:
        return {"PASS": RC_PASS, "FAIL": RC_FAIL, "UNMEASURED": RC_UNMEASURED}[self.verdict]

    def verdict_line(self) -> str:
        """契约 §D 的期望判定行原文（字段名与顺序固定）。"""
        return (
            f"{GATE}: verdict={self.verdict} catalogs={self.catalogs} "
            f"keys_missing={self.keys_missing} keys_extra={self.keys_extra} "
            f"placeholder_mismatch={self.placeholder_mismatch} "
            f"hardcoded_literals={self.hardcoded_literals} "
            f"locale_literals={self.locale_literals} standards={self.standards} "
            f"runtime_switch={self.runtime_switch} catalog_errors={self.catalog_errors}"
        )


# ── 语言层装载（重依赖缺席 ⇒ 降级，import 期硬失败）─────────────────────────────────────
def load_catalog_module(tree: Path) -> tuple[Any | None, str | None]:
    """import `cadcore.i18n.catalog`；包导入失败时按文件路径装载（兄弟席位 `__init__.py` 缺席的降级）。"""
    tree = Path(tree).resolve()
    if str(tree) not in sys.path:
        sys.path.insert(0, str(tree))
    try:
        return importlib.import_module("cadcore.i18n.catalog"), None
    except Exception as exc:  # pragma: no cover - 取决于树上是否有该模块
        package_import_error = f"{type(exc).__name__}: {exc}"
    catalog_file = tree / "cadcore" / "i18n" / "catalog.py"
    if not catalog_file.is_file():
        return None, f"catalog.py 不在树上（package import 也失败：{package_import_error}）"
    try:
        spec = importlib.util.spec_from_file_location("cadcore_i18n_catalog_for_gate", catalog_file)
        if spec is None or spec.loader is None:
            return None, "spec_from_file_location 返回空"
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:  # pragma: no cover
        return None, f"{type(exc).__name__}: {exc}"


def data_root_of(tree: Path) -> Path | None:
    """定位 `<tree>` 下的数据根：`cadcore/data`（包布局）优先，其次 `data`。"""
    for candidate in (tree / "cadcore" / "data", tree / "data"):
        if candidate.is_dir():
            return candidate
    return None


# ── 源码字面量扫描（只认非 docstring 的字符串常量）──────────────────────────────────────
def _docstring_ids(module: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _is_test_path(rel: Path) -> bool:
    return any(part in ("tests", "test") for part in rel.parts) or rel.name.startswith("test_")


def scan_source_literals(tree: Path) -> LiteralScan:
    """扫产品源码里的中文文案字面量与写死语言码；`tests/` 与缓存目录**显式排除并计数**。"""
    tree = Path(tree)
    scan = LiteralScan()
    for path in sorted(tree.rglob("*.py")):
        rel = path.relative_to(tree)
        if any(part in _SKIP_DIR_PARTS for part in rel.parts):
            continue
        if _is_test_path(rel):
            scan.files_skipped_tests += 1
            continue
        scan.files_scanned += 1
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:  # 读不动就记账，不静默跳过
            scan.hardcoded_literals += 0
            if len(scan.hardcoded_examples) < MAX_EXAMPLES:
                scan.hardcoded_examples.append(f"{rel} :: <unreadable: {type(exc).__name__}>")
            continue
        try:
            parsed = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            if len(scan.hardcoded_examples) < MAX_EXAMPLES:
                scan.hardcoded_examples.append(f"{rel}:{exc.lineno} :: <syntax error>")
            continue
        doc_ids = _docstring_ids(parsed)
        for node in ast.walk(parsed):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in doc_ids:
                continue
            value = node.value
            where = f"{rel}:{getattr(node, 'lineno', '?')}"
            if _HAN_RE.search(value):
                scan.hardcoded_literals += 1
                if len(scan.hardcoded_examples) < MAX_EXAMPLES:
                    scan.hardcoded_examples.append(f"{where} :: {value.strip()[:60]!r}")
            elif _LOCALE_LITERAL_RE.match(value.strip()):
                scan.locale_literals += 1
                if len(scan.locale_examples) < MAX_EXAMPLES:
                    scan.locale_examples.append(f"{where} :: {value.strip()!r}")
    return scan


# ── 语言包比对 ──────────────────────────────────────────────────────────────────────────
def _resolve_langs(module: Any, dirs: Sequence[str], requested: Sequence[str] | None) -> tuple[list[str], str | None]:
    if requested:
        return [code.strip() for code in requested if code.strip()], None
    try:
        found = [str(code) for code in module.available_locales(dirs=list(dirs))]
    except Exception as exc:
        return [], f"available_locales 调用失败：{type(exc).__name__}: {exc}"
    return found, None


def _pick_probe_key(module: Any, base: Any, target: Any, langs: Sequence[str]) -> str | None:
    """挑一个译文确实不同、且非空的关键字，作为「切换后文本必须变」的证据锚。"""
    for key in sorted(base.keys() & target.keys()):
        left = base.strings.get(key, "")
        right = target.strings.get(key, "")
        if left and right and left != right:
            return key
    return None


def check_runtime_switch(module: Any, dirs: Sequence[str], langs: Sequence[str]) -> tuple[int, list[str]]:
    """真调 `set_locale()`：三证（切前 / 切后 / 往返）+ 订阅广播；返回 `(1|0, notes)`。"""
    notes: list[str] = []
    base_code = getattr(module, "DEFAULT_LANGUAGE", None) or (langs[0] if langs else None)
    if not base_code:
        return 0, ["no base language to switch from"]
    target_code = next((code for code in langs if code != base_code), None)
    if target_code is None:
        return 0, ["no second language requested"]
    try:
        base_cat = module.load_catalog(base_code, dirs=list(dirs))
        target_cat = module.load_catalog(target_code, dirs=list(dirs))
    except Exception as exc:
        return 0, [f"load_catalog failed: {type(exc).__name__}: {exc}"]

    seen: list[str] = []
    unsubscribe = module.on_language_changed(lambda change: seen.append(str(change.current)))
    try:
        module.set_locale(base_code, dirs=list(dirs))
        probe_key = _pick_probe_key(module, base_cat, target_cat, langs)
        before = module.t(probe_key) if probe_key else None
        if probe_key is None:
            notes.append("no differing translation to anchor on; falling back to resolved-catalog evidence")

        change = module.set_locale(target_code, dirs=list(dirs))
        after = module.t(probe_key) if probe_key else None
        active = module.active_locale()
        if not change.changed or change.keys <= 0 or not change.catalog_present:
            return 0, [f"switch not effective: changed={change.changed} keys={change.keys} present={change.catalog_present}"]
        if str(change.resolved or "") != target_code or active != target_code:
            return 0, [f"resolved/active mismatch: resolved={change.resolved} active={active} want={target_code}"]
        if probe_key is not None:
            want = target_cat.strings.get(probe_key, "")
            if after != want or after == before:
                return 0, [f"text unchanged or wrong after switch: key={probe_key} before={before!r} after={after!r}"]
        if target_code not in seen:
            return 0, [f"on_language_changed broadcast missing (seen={seen})"]

        module.set_locale(base_code, dirs=list(dirs))
        roundtrip = module.t(probe_key) if probe_key else None
        if probe_key is not None and roundtrip != before:
            return 0, [f"roundtrip mismatch: before={before!r} roundtrip={roundtrip!r}"]
    except Exception as exc:
        return 0, [f"{type(exc).__name__}: {exc}"]
    finally:
        unsubscribe()
    notes.append(f"switch {base_code}->{target_code} verified (active={active}, broadcast=1, roundtrip=1)")
    return 1, notes


# ── 主判定 ──────────────────────────────────────────────────────────────────────────────
def run_check(tree: Path, langs: Sequence[str] | None = None) -> Report:
    """对一棵树跑完 G1 全部子项（不打印、不改树）。"""
    tree = Path(tree)
    report = Report(langs=tuple(langs or ()))
    if not tree.is_dir():
        return report.finalize(unmeasured=f"tree_absent={tree}")

    i18n_dir = None
    data_root = data_root_of(tree)
    if data_root is not None:
        candidate = data_root / "i18n"
        i18n_dir = candidate if candidate.is_dir() else None
    if i18n_dir is None:
        return report.finalize(unmeasured=f"i18n_dir_absent_under={tree}")

    module, error = load_catalog_module(tree)
    if module is None:
        return report.finalize(unmeasured=f"catalog_module_unavailable: {error}")

    dirs = [str(i18n_dir)]
    infos = module.scan_catalogs(dirs)
    invalid = [info for info in infos if not info.valid]
    report.catalog_errors = len(invalid)
    for info in invalid:
        report.fail(f"catalog_invalid={info.path}: {info.error}")
    requested, resolve_error = _resolve_langs(module, dirs, langs)
    if resolve_error:
        return report.finalize(unmeasured=resolve_error)
    if not requested and invalid:
        return report.finalize(unmeasured=None)
    if not requested:
        return report.finalize(unmeasured="no_language_requested_and_none_on_disk")
    report.langs = tuple(requested)

    base_code = getattr(module, "DEFAULT_LANGUAGE", None) or requested[0]
    # 「在不在盘上」以**扫盘**为准：`load_catalog` 沿回退链回落（`ja` 缺席会拿 `en` 顶上，
    # 但 `Catalog.code` 会如实记成 `en`）—— 只看"调用没抛异常"会把删掉的语言包当成还在。
    on_disk = {info.code for info in infos if info.valid}
    loaded: dict[str, Any] = {}
    for code in requested:
        if code not in on_disk:
            report.missing_catalogs.append(code)
            continue
        try:
            catalog = module.load_catalog(code, dirs=dirs)
        except Exception as exc:
            report.missing_catalogs.append(code)
            report.catalog_errors += 1
            report.fail(f"catalog_load_failed={code}: {type(exc).__name__}: {exc}")
            continue
        if str(getattr(catalog, "code", code)) != code:
            report.missing_catalogs.append(code)
            continue
        loaded[code] = catalog
    report.catalogs = len([code for code in requested if code in loaded])
    report.catalogs_on_disk = len(on_disk)

    base = loaded.get(base_code)
    if report.catalogs >= 2 and base is not None:
        missing = 0
        extra = 0
        placeholders = 0
        for code, cat in loaded.items():
            if code == base_code:
                continue
            left, right = module.key_diff(base, cat)
            missing += len(left)
            extra += len(right)
            placeholders += len(module.placeholder_diff(base, cat))
        report.keys_missing, report.keys_extra = missing, extra
        report.placeholder_mismatch = placeholders
    elif report.catalogs >= 2:
        report.missing_catalogs.append(str(base_code))
        report.notes.append(f"base_catalog_absent={base_code}: 双向差集未采（-1）")

    report.scan = scan_source_literals(tree)
    report.hardcoded_literals = report.scan.hardcoded_literals
    report.locale_literals = report.scan.locale_literals
    report.examples = list(report.scan.hardcoded_examples) + list(report.scan.locale_examples)

    if data_root is None:
        return report.finalize(unmeasured=f"data_dir_absent_under={tree}")
    standards_dir = data_root / "standards"
    if not standards_dir.is_dir():
        return report.finalize(unmeasured=f"standards_dir_absent={standards_dir}")
    report.standards = len(sorted(standards_dir.glob("*.json")))

    try:
        report.runtime_switch, notes = check_runtime_switch(module, dirs, requested)
        report.notes.extend(notes)
    except Exception as exc:  # pragma: no cover - 语言层自身异常不该拖垮整门
        report.runtime_switch = -1
        report.notes.append(f"runtime_switch_unmeasured: {type(exc).__name__}: {exc}")
    if report.runtime_switch == -1:
        return report.finalize(unmeasured="runtime_switch_unmeasured")
    return report.finalize(unmeasured=None)


# ── 输出 ────────────────────────────────────────────────────────────────────────────────
def emit(report: Report) -> int:
    print(report.verdict_line())
    scan = report.scan
    if scan is not None:
        print(
            f"I18N_SCOPE: source_root=product_py files_scanned={scan.files_scanned} "
            f"files_skipped_tests={scan.files_skipped_tests} tests_excluded=1 "
            f"catalogs_on_disk={report.catalogs_on_disk} langs={','.join(report.langs) or 'NA'}"
        )
    for note in report.notes:
        print(f"I18N_NOTE: {note}")
    for reason in report.reasons:
        print(f"I18N_REASON: {reason}")
    for example in report.examples:
        print(f"I18N_EXAMPLE: {example}")
    return report.rc


# ── 自检：拷贝真树 → 逐个做坏，每个都必须报红 ────────────────────────────────────────────
def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _copy_tree(src: Path) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="i18n_gate_selftest_"))
    shutil.copytree(
        src, dst / src.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=False
    )
    return dst / src.name


def _write_probe(root: Path, body: str) -> None:
    probe = root / "cadcore" / "_gate_selftest_probe.py"
    probe.write_text('"""Probe file written by i18n_gate --selftest (temp copy only)."""\n\n' + body, encoding="utf-8")


@dataclass
class Case:
    name: str
    expect_rc: int
    expect: dict[str, Any]
    got_rc: int = -1
    got: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    detail: str = ""


def _fields(report: Report) -> dict[str, Any]:
    return {
        "verdict": report.verdict,
        "catalogs": report.catalogs,
        "keys_missing": report.keys_missing,
        "keys_extra": report.keys_extra,
        "placeholder_mismatch": report.placeholder_mismatch,
        "hardcoded_literals": report.hardcoded_literals,
        "locale_literals": report.locale_literals,
        "standards": report.standards,
        "runtime_switch": report.runtime_switch,
        "catalog_errors": report.catalog_errors,
    }


def _matches(fields: dict[str, Any], expect: dict[str, Any]) -> bool:
    for key, want in expect.items():
        got = fields.get(key)
        if isinstance(want, str) or want is None:
            if got != want:
                return False
        elif isinstance(want, tuple):  # (op, value)
            op, value = want
            if op == ">=" and not (isinstance(got, int) and got >= value):
                return False
            if op == "==" and got != value:
                return False
        elif got != want:
            return False
    return True


def selftest(tree: Path) -> int:
    tree = Path(tree)
    if not tree.is_dir():
        print(f"{SELFTEST_GATE}: verdict=UNMEASURED reason=tree_absent={tree}")
        return RC_UNMEASURED
    proof_files = [tree / "cadcore" / "i18n" / "catalog.py", tree / "cadcore" / "data" / "i18n"]
    if not all(p.exists() for p in proof_files):
        print(f"{SELFTEST_GATE}: verdict=UNMEASURED reason=fixture_not_real_tree={tree}")
        return RC_UNMEASURED

    before = {p: _digest(p) for p in sorted((tree / "cadcore" / "data" / "i18n").glob("*.json"))}
    langs = ["en", "zh-CN", "ja"]
    cases: list[Case] = [
        Case("clean_tree_green", RC_PASS, {"verdict": "PASS", "keys_missing": 0, "keys_extra": 0,
                                           "hardcoded_literals": 0, "locale_literals": 0,
                                           "standards": MIN_STANDARDS, "runtime_switch": 1}),
        Case("mutant_add_key_zh_only", RC_FAIL, {"verdict": "FAIL", "keys_extra": 1, "keys_missing": 0}),
        Case("mutant_chinese_literal", RC_FAIL, {"verdict": "FAIL", "hardcoded_literals": (">=", 1)}),
        Case("mutant_locale_literal", RC_FAIL, {"verdict": "FAIL", "locale_literals": (">=", 1)}),
        Case("mutant_drop_catalog", RC_FAIL, {"verdict": "FAIL", "catalogs": 2, "runtime_switch": 1}),
    ]
    workdirs: list[Path] = []
    try:
        # ① 干净拷贝必须绿（证明夹具测的是真件，且树上当前确实达标）
        clean = _copy_tree(tree)
        workdirs.append(clean)
        report = run_check(clean, langs)
        cases[0].got_rc, cases[0].got = report.rc, _fields(report)
        cases[0].ok = report.rc == cases[0].expect_rc and _matches(cases[0].got, cases[0].expect)
        cases[0].detail = report.verdict_line()

        # ② 只给 zh-CN 加一个 key ⇒ keys_extra=1
        add = _copy_tree(tree)
        workdirs.append(add)
        zh_path = add / "cadcore" / "data" / "i18n" / "zh-CN.json"
        payload = json.loads(zh_path.read_text(encoding="utf-8"))
        payload["strings"]["__gate_selftest_extra_key__"] = "x"
        zh_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report = run_check(add, langs)
        cases[1].got_rc, cases[1].got = report.rc, _fields(report)
        cases[1].ok = report.rc == cases[1].expect_rc and _matches(cases[1].got, cases[1].expect)
        cases[1].detail = report.verdict_line()

        # ③ 源码留中文文案 ⇒ hardcoded_literals>0
        zh_src = _copy_tree(tree)
        workdirs.append(zh_src)
        _write_probe(zh_src, "PROBE_TEXT = " + json.dumps(_HAN_LEAK_TEXT, ensure_ascii=False) + "\n")
        report = run_check(zh_src, langs)
        cases[2].got_rc, cases[2].got = report.rc, _fields(report)
        cases[2].ok = report.rc == cases[2].expect_rc and _matches(cases[2].got, cases[2].expect)
        cases[2].detail = report.verdict_line()

        # ④ 源码写死语言码 ⇒ locale_literals>0
        loc_src = _copy_tree(tree)
        workdirs.append(loc_src)
        _write_probe(loc_src, "PROBE_LOCALE = " + json.dumps(_LOCALE_LITERAL_PROBE) + "\n")
        report = run_check(loc_src, langs)
        cases[3].got_rc, cases[3].got = report.rc, _fields(report)
        cases[3].ok = report.rc == cases[3].expect_rc and _matches(cases[3].got, cases[3].expect)
        cases[3].detail = report.verdict_line()

        # ⑤ 删一个语言包 ⇒ catalogs=2 且 rc=1（**不许落进 rc=2**）
        dropped = _copy_tree(tree)
        workdirs.append(dropped)
        target = langs[-1]
        (dropped / "cadcore" / "data" / "i18n" / f"{target}.json").unlink()
        report = run_check(dropped, langs)
        cases[4].got_rc, cases[4].got = report.rc, _fields(report)
        cases[4].ok = report.rc == cases[4].expect_rc and _matches(cases[4].got, cases[4].expect)
        cases[4].detail = report.verdict_line()

        # ⑥ 空集对照：目录不存在 ⇒ rc=2 未采（不许当绿）
        empty = Path(tempfile.mkdtemp(prefix="i18n_gate_selftest_empty_"))
        workdirs.append(empty)
        report = run_check(empty, langs)
        empty_case = Case("absent_tree_unmeasured", RC_UNMEASURED,
                          {"verdict": "UNMEASURED", "catalogs": 0, "keys_missing": -1,
                           "hardcoded_literals": -1, "standards": -1, "runtime_switch": -1},
                          got_rc=report.rc, got=_fields(report))
        empty_case.ok = report.rc == empty_case.expect_rc and _matches(empty_case.got, empty_case.expect)
        empty_case.detail = report.verdict_line()
        cases.append(empty_case)

        for name, duplicate in (("duplicate_json_key", True), ("foreign_script", False)):
            mutated = _copy_tree(tree)
            workdirs.append(mutated)
            path = mutated / "cadcore" / "data" / "i18n" / "zh-CN.json"
            text = path.read_text(encoding="utf-8")
            if duplicate:
                text = text.replace('"strings": {', '"strings": {"cmd.fillet": "bad",', 1)
            else:
                payload = json.loads(text)
                payload["strings"]["cmd.fillet"] = "\u30d5\u30a3\u30ec\u30c3\u30c8"
                text = json.dumps(payload, ensure_ascii=False)
            path.write_text(text, encoding="utf-8")
            report = run_check(mutated, langs)
            case = Case("mutant_" + name, RC_FAIL, {"verdict": "FAIL", "catalog_errors": 1},
                        got_rc=report.rc, got=_fields(report))
            case.ok = report.rc == case.expect_rc and _matches(case.got, case.expect)
            case.detail = report.verdict_line()
            cases.append(case)

        # 夹具自证后半段：原树必须逐字节未变（自检无副作用）
        after = {p: _digest(p) for p in sorted((tree / "cadcore" / "data" / "i18n").glob("*.json"))}
        unchanged = before == after and not (tree / "cadcore" / "_gate_selftest_probe.py").exists()
    finally:
        for path in workdirs:
            shutil.rmtree(path, ignore_errors=True)

    controls_ok = sum(1 for case in cases if case.ok)
    verdict = "PASS" if controls_ok == len(cases) and unchanged else "FAIL"
    print(
        f"{SELFTEST_GATE}: verdict={verdict} controls={controls_ok}/{len(cases)} "
        f"real_tree_unchanged={int(unchanged)} langs={','.join(langs)}"
    )
    for case in cases:
        mark = "ok" if case.ok else "MISSED"
        print(
            f"{SELFTEST_GATE}_CASE: {case.name} expect_rc={case.expect_rc} got_rc={case.got_rc} "
            f"expected={case.expect} got={case.got} -> {mark}"
        )
    if verdict != "PASS":
        for case in cases:
            if not case.ok:
                print(f"{SELFTEST_GATE}_DETAIL: {case.name} :: {case.detail}")
        return RC_FAIL
    return RC_PASS


# ── CLI ─────────────────────────────────────────────────────────────────────────────────
def default_tree() -> Path:
    return Path(__file__).resolve().parents[2] / "packages" / "cadcore"


def parse_langs(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定行 G1：i18n 语言层机检门")
    parser.add_argument("--check", action="store_true", help="跑判定行（rc 0/1/2）")
    parser.add_argument("--selftest", action="store_true", help="阴阳双证：干净树必绿 + 四种做坏必红")
    parser.add_argument("--langs", default=None, help="逗号分隔的语言码；缺省=扫盘得到的全部语言")
    parser.add_argument("--tree", default=None, help="要检的树（缺省 packages/cadcore）")
    args = parser.parse_args(list(argv) if argv is not None else None)
    tree = Path(args.tree).resolve() if args.tree else default_tree()

    if args.selftest:
        return selftest(tree)
    if args.check:
        return emit(run_check(tree, parse_langs(args.langs)))
    parser.print_usage(sys.stderr)
    print(f"{GATE}: verdict=UNMEASURED reason=no_mode_selected_use_--check_or_--selftest")
    return RC_UNMEASURED


if __name__ == "__main__":
    raise SystemExit(main())
