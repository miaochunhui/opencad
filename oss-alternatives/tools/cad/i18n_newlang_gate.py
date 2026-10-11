#!/usr/bin/env python3
"""G8 判定行：**新增一门语言不需要改任何代码**的可复跑实证门。

判什么（三条腿，全部在临时"用户语言目录"里现造现测，纯标准库）：

1. **加文件即加语言**：把一份新语言包（缺省 `eo.json`，键集合与 `en` 逐条一致）放进
   `<user_dir>/`，`catalog.scan_catalogs([user_dir, builtin_dir])` 必须发现它（`dir_index=0`，
   用户目录优先），`set_locale(code, dirs=...)` 必须真的切过去（`resolved==code`、
   `catalog_present=True`、`changed=True`），`t(key)` 必须返回**该语言包里的串**
   （不是 en 串、不是 key 名、不是空串）；被留空的键必须回落到 en（`t()` 的硬保证）。
   全程**不改源码**：跑前跑后对 `catalog.py` 与 `en.json` 取 sha256，必须逐字节相同
   （`code_unchanged=1`）。
2. **阴性对照（尺子必须看得见红）**：①键集合缺一个 ⇒ `key_diff` 的 `missing` 非空且准入判红
   （`drop_key_detected=1`）；②语言包没有 `_meta` ⇒ 准入层**必须拒**（`meta_absent_rejected=1`）。
   ⚠️ 分层事实（不粉饰）：底层 `catalog.py` 对缺 `_meta` 的文件是**容忍**的（语言码回落到文件名主干，
   `license`/`version`/`source` 全记 `None`），所以"没 `_meta` 必须被拒"这条**由本门的准入层承担**，
   报告里 `baselib_tolerates_meta_absent=1` 把这个事实显式打出来，不让读者以为底层会拒。
3. **语言清单来自扫盘、不来自源码常量**：同目录里放一个声明了语言码的 `.py`，扫盘**不得**因此多出
   这门语言（`code_only_no_newlang=1`）；并用 G1 门 `i18n_gate.scan_source_literals` 的**同一把尺子**
   扫产品源码（`locale_literals=0` / `hardcoded_literals=0`）。

口径：`0`=真为零 / `-1`=未采 / `NA`=不适用。判定行字段一律以本脚本当次输出为准，不手抄。
三态 `rc`：`0=PASS`、`1=FAIL`（真不达标）、`2=UNMEASURED`（`catalog.py` 或随包语言包缺席、G1 尺子
import 不到 ⇒ 明确报未采，**不许当绿**）。

用法：
    python3 oss-alternatives/tools/cad/i18n_newlang_gate.py --check
    python3 oss-alternatives/tools/cad/i18n_newlang_gate.py --check --lang eo
    python3 oss-alternatives/tools/cad/i18n_newlang_gate.py --selftest

`--selftest` 做坏四种夹具（干净 / 缺一个键 / 无 `_meta` / 源码塞语言码字面量），前一种必须绿、
后三种必须红；尺子看不见红 ⇒ 自检自己 `rc=1`。

临时夹具走 `tempfile.mkdtemp()`（跑完即弃、<1MB 的进程内探针，属 D2 的显式例外，不落 3FS）。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

GATE = "I18N_NEWLANG"
SELFTEST_GATE = "I18N_NEWLANG_SELFTEST"
RC_PASS, RC_FAIL, RC_UNMEASURED = 0, 1, 2

NEW_LANGUAGE = "eo"                      # 普通两字母语言码；不是 <lang>-<REGION> 形状的字面量
PSEUDO_PREFIX = "eo.pseudo:"             # 夹具译文前缀：可判别"串来自这门新语言包"，且保留占位符
FIXTURE_SOURCE = "g8-test-fixture-pseudo-translation"   # 不冒充真实译者产出
MAX_EXAMPLES = 5
ADMIT_REQUIRED_META = ("code", "native_name", "license", "source", "version")

TOOLS_DIR = Path(__file__).resolve().parent                 # <oss-alternatives>/tools/cad
OSS_ROOT = TOOLS_DIR.parents[1]                             # <oss-alternatives>
DEFAULT_TREE = OSS_ROOT / "packages" / "cadcore"
DEFAULT_BUILTIN = DEFAULT_TREE / "cadcore" / "data" / "i18n"
G1_GATE_FILE = TOOLS_DIR / "i18n_gate.py"


# ── 装载（包导入优先，失败按文件装载 —— 与 G1 门同一策略）──────────────────────────────────
def load_module(name: str, path: Path) -> Any:
    """按文件路径装载一个模块（不依赖包 `__init__.py` 是否已落盘）。"""
    spec = importlib.util.spec_from_file_location(name, Path(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"spec_from_file_location returned nothing for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_catalog_module(tree: Path) -> tuple[Any | None, str | None]:
    """装载 `cadcore.i18n.catalog`；包不可导入时退到文件级装载，仍失败则返回原因。"""
    tree = Path(tree).resolve()
    if str(tree) not in sys.path:
        sys.path.insert(0, str(tree))
    try:
        return importlib.import_module("cadcore.i18n.catalog"), None
    except Exception as exc:  # noqa: BLE001 - 树上的包状态不由本门决定
        package_error = f"{type(exc).__name__}: {exc}"
    catalog_file = tree / "cadcore" / "i18n" / "catalog.py"
    if not catalog_file.is_file():
        return None, f"catalog.py absent on tree={tree} (package import failed: {package_error})"
    try:
        return load_module("cadcore_i18n_catalog_for_newlang_gate", catalog_file), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def load_g1_scanner() -> tuple[Any | None, str | None]:
    """装载 G1 门（复用它的 `scan_source_literals`，**不另造一把尺子**）。"""
    gate_file = Path(G1_GATE_FILE)
    if not gate_file.is_file():
        return None, f"g1 gate absent: {gate_file}"
    try:
        return load_module("i18n_gate_for_newlang", gate_file), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


# ── 准入层（本门承担的数据模型校验）────────────────────────────────────────────────────
@dataclass(frozen=True)
class Admission:
    """一份用户语言包的准入结论；`reasons` 是机读原因码，不吞错。"""

    ok: bool
    code: str
    keys_missing: int = -1
    keys_extra: int = -1
    keys_total: int = -1
    blank_values: int = -1
    meta_missing: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


def admit_catalog_file(
    path: Path,
    *,
    module: Any,
    base_file: Path,
) -> Admission:
    """准入校验：`_meta` 齐备 + 键集合与源串语言**双向**一致（任一非空即拒）。

    底层 `catalog.py` 对缺 `_meta` 只记 debug 并回落文件名主干 ⇒ 本函数是那条例外的执行点：
    `license` / `source` / `version` 缺席的语言包**不许进树**（合规与可追溯性）。
    """
    path = Path(path)
    reasons: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Admission(ok=False, code="", reasons=(f"unreadable_json:{type(exc).__name__}",))
    if not isinstance(payload, Mapping):
        return Admission(ok=False, code="", reasons=("payload_not_object",))
    code = ""
    meta = payload.get("_meta")
    if not isinstance(meta, Mapping):
        reasons.append("meta_absent")
        meta = {}
    else:
        code = str(meta.get("code") or "")
    missing_meta = tuple(
        name for name in ADMIT_REQUIRED_META
        if not str(meta.get(name) or "").strip()
    )
    if missing_meta:
        reasons.append("meta_missing:" + ",".join(missing_meta))
    strings = payload.get("strings")
    if not isinstance(strings, Mapping):
        reasons.append("strings_absent")
        return Admission(ok=False, code=code, meta_missing=missing_meta, reasons=tuple(reasons))
    bad_types = [key for key, value in strings.items() if not isinstance(key, str) or not isinstance(value, str)]
    if bad_types:
        reasons.append(f"strings_not_str_to_str:{len(bad_types)}")
    blank = sum(1 for value in strings.values() if isinstance(value, str) and not value.strip())
    base_payload = json.loads(Path(base_file).read_text(encoding="utf-8"))
    base_strings = base_payload.get("strings") if isinstance(base_payload, Mapping) else None
    if not isinstance(base_strings, Mapping):
        return Admission(ok=False, code=code, meta_missing=missing_meta, reasons=("base_catalog_unusable",))
    base_keys = set(base_strings)
    keys = set(strings)
    missing = base_keys - keys
    extra = keys - base_keys
    if missing:
        reasons.append(f"keys_missing:{len(missing)}")
    if extra:
        reasons.append(f"keys_extra:{len(extra)}")
    expected = str(meta.get("code") or path.stem)
    if module is not None and getattr(module, "_normalize", None) is not None:
        normalized = module._normalize(expected)
        if normalized != module._normalize(path.stem):
            reasons.append(f"code_mismatch:{expected}!={path.stem}")
    return Admission(
        ok=not reasons,
        code=code or path.stem,
        keys_missing=len(missing),
        keys_extra=len(extra),
        keys_total=len(keys),
        blank_values=blank,
        meta_missing=missing_meta,
        reasons=tuple(reasons),
    )


# ── 夹具 ────────────────────────────────────────────────────────────────────────────────
def pseudo_translation(value: str) -> str:
    """伪译文：`eo.pseudo:<en 原文>` —— 前缀可判别来源，且**逐字符保留占位符**（`{n}` 不破）。"""
    return PSEUDO_PREFIX + value


def pick_plain_key(strings: Mapping[str, str], *, exclude: Sequence[str] = ()) -> str:
    """挑一个无占位符、非空的键（占位符替换失败会掩盖"串来自哪门语言"这个判据）。"""
    for key in sorted(strings):
        value = strings[key]
        if key in exclude or not value.strip() or "{" in value:
            continue
        return key
    raise KeyError("no plain (placeholder-free, non-empty) key in the base catalog")


def build_payload(
    base_payload: Mapping[str, Any],
    *,
    code: str,
    fixture: str = "clean",
    blank_key: str | None = None,
    drop_key: str | None = None,
) -> dict[str, Any]:
    """由源串语言包派生一份新语言包夹具；`fixture` 决定注入哪种做坏。"""
    base_strings = dict(base_payload["strings"])
    base_meta = dict(base_payload.get("_meta") or {})
    meta: dict[str, Any] = {
        "code": code,
        "native_name": "Fixture (pseudo translation)",
        "fallback": None,
        "font_fallback": list(base_meta.get("font_fallback") or ()),
        "default_standard": base_meta.get("default_standard") or "iso",
        "license": "Apache-2.0",
        "source": FIXTURE_SOURCE,
        "version": 1,
        "_doc": "Generated by i18n_newlang_gate; values are pseudo translations, not real copy.",
    }
    strings = {
        key: ("" if key == blank_key else pseudo_translation(value))
        for key, value in base_strings.items()
    }
    if drop_key is not None:
        strings.pop(drop_key, None)
    payload: dict[str, Any] = {"strings": strings}
    if fixture != "no_meta":
        payload["_meta"] = meta
    return payload


def write_catalog(path: Path, payload: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── 判定结果 ────────────────────────────────────────────────────────────────────────────
@dataclass
class Report:
    """一次 G8 判定的全部机读字段；`finalize()` 才收敛 `verdict`。"""

    new_language: str = NEW_LANGUAGE
    fixture: str = "clean"
    catalogs_before: int = -1
    catalogs_after: int = -1
    discovered: int = -1
    user_dir_index: int = -1
    admit: int = -1
    switched: int = -1
    resolved: str = ""
    keys_in_new_catalog: int = -1
    translated: int = -1
    fallback_ok: int = -1
    blank_values: int = -1
    key_diff_missing: int = -1
    key_diff_extra: int = -1
    meta_absent_rejected: int = -1
    drop_key_detected: int = -1
    code_only_no_newlang: int = -1
    code_unchanged: int = -1
    locale_literals: int = -1
    hardcoded_literals: int = -1
    files_scanned: int = -1
    baselib_tolerates_meta_absent: int = -1
    aborted: str = ""            # 准入拒绝等**决定性**中止（此时后半段未跑 ⇒ -1 是"未采"，但结论是 FAIL）
    probe: str = ""
    missing_examples: list[str] = field(default_factory=list)
    locale_examples: list[str] = field(default_factory=list)
    admit_reasons: tuple[str, ...] = ()
    reason: str = ""
    verdict: str = "UNMEASURED"
    rc: int = RC_UNMEASURED

    def finalize(self) -> "Report":
        if self.reason:
            self.verdict, self.rc = "UNMEASURED", RC_UNMEASURED
            return self
        if self.aborted:
            # 准入层**当场**判红（缺键 / 缺 _meta）：这是真不达标 rc=1，
            # 不是"采不到" rc=2 —— 后半段字段留 -1（未采）如实标注，不许把拒绝混成未采。
            self.verdict, self.rc = "FAIL", RC_FAIL
            self.reason = ""
            return self
        if self.locale_literals == -1 or self.hardcoded_literals == -1:
            self.verdict, self.rc = "UNMEASURED", RC_UNMEASURED
            self.reason = "g1_literal_scanner_unavailable"
            return self
        ok = (
            self.catalogs_after == self.catalogs_before + 1
            and self.discovered == 1
            and self.user_dir_index == 0
            and self.admit == 1
            and self.switched == 1
            and self.translated == 1
            and self.fallback_ok == 1
            and self.key_diff_missing == 0
            and self.key_diff_extra == 0
            and self.meta_absent_rejected == 1
            and self.drop_key_detected == 1
            and self.code_only_no_newlang == 1
            and self.code_unchanged == 1
            and self.locale_literals == 0
            and self.hardcoded_literals == 0
        )
        self.verdict = "PASS" if ok else "FAIL"
        self.rc = RC_PASS if ok else RC_FAIL
        return self

    def line(self) -> str:
        parts = [
            f"{GATE}: verdict={self.verdict}",
            f"new_language={self.new_language}",
            f"catalogs_before={self.catalogs_before}",
            f"catalogs_after={self.catalogs_after}",
            f"switched={self.switched}",
            f"key_diff_missing={self.key_diff_missing}",
            f"key_diff_extra={self.key_diff_extra}",
            f"discovered={self.discovered}",
            f"user_dir_index={self.user_dir_index}",
            f"admit={self.admit}",
            f"translated={self.translated}",
            f"fallback_ok={self.fallback_ok}",
            f"keys_total={self.keys_in_new_catalog}",
            f"meta_absent_rejected={self.meta_absent_rejected}",
            f"drop_key_detected={self.drop_key_detected}",
            f"code_only_no_newlang={self.code_only_no_newlang}",
            f"code_unchanged={self.code_unchanged}",
            f"locale_literals={self.locale_literals}",
            f"hardcoded_literals={self.hardcoded_literals}",
            f"baselib_tolerates_meta_absent={self.baselib_tolerates_meta_absent}",
            f"files_scanned={self.files_scanned}",
            f"fixture={self.fixture}",
        ]
        if self.probe:
            parts.append(f"probe={self.probe}")
        if self.admit_reasons:
            parts.append("admit_reasons=" + ",".join(self.admit_reasons))
        if self.missing_examples:
            parts.append("missing_examples=" + "|".join(self.missing_examples))
        if self.locale_examples:
            parts.append("locale_examples=" + "|".join(self.locale_examples))
        if self.aborted:
            parts.append(f"aborted={self.aborted}")
        if self.reason:
            parts.append(f"reason={self.reason}")
        return " ".join(parts)


# ── 主判定 ──────────────────────────────────────────────────────────────────────────────
def run_check(
    new_code: str = NEW_LANGUAGE,
    *,
    tree: Path | None = None,
    builtin: Path | None = None,
    fixture: str = "clean",
    source_tree: Path | None = None,
) -> Report:
    """跑一次完整判定。`fixture` ∈ {clean, drop_key, no_meta}；`source_tree` 供自检换尺子对象。"""
    tree = Path(tree) if tree else DEFAULT_TREE
    builtin = Path(builtin) if builtin else DEFAULT_BUILTIN
    report = Report(new_language=new_code, fixture=fixture)

    module, why = load_catalog_module(tree)
    if module is None:
        report.reason = why or "catalog_module_unavailable"
        return report.finalize()
    module_file = Path(getattr(module, "__file__", "") or (tree / "cadcore" / "i18n" / "catalog.py"))
    base_file = builtin / f"{getattr(module, 'DEFAULT_LANGUAGE', 'en')}.json"
    if not base_file.is_file():
        report.reason = f"builtin_base_catalog_absent:{base_file}"
        return report.finalize()
    base_payload = json.loads(base_file.read_text(encoding="utf-8"))
    base_strings = dict(base_payload["strings"])
    probe_key = pick_plain_key(base_strings)
    blank_key = pick_plain_key(base_strings, exclude=[probe_key])
    before_sha = _sha256(module_file)
    base_file_sha = _sha256(base_file)

    workdir = Path(tempfile.mkdtemp(prefix="i18n-newlang-"))
    try:
        user_dir = workdir / "user-i18n"
        user_dir.mkdir()
        eo_path = write_catalog(
            user_dir / f"{new_code}.json",
            build_payload(base_payload, code=new_code, fixture=fixture,
                          blank_key=blank_key,
                          drop_key=(probe_key if fixture == "drop_key" else None)),
        )
        module.clear_cache()

        catalogs_before = [info for info in module.scan_catalogs([str(builtin)]) if info.valid]
        report.catalogs_before = len(catalogs_before)
        infos_after = module.scan_catalogs([str(user_dir), str(builtin)])
        report.catalogs_after = len([info for info in infos_after if info.valid])
        found = next((info for info in infos_after if info.code == new_code), None)
        report.discovered = 1 if (found is not None and found.valid) else 0
        report.user_dir_index = found.dir_index if found is not None else -1

        admission = admit_catalog_file(eo_path, module=module, base_file=base_file)
        report.admit = 1 if admission.ok else 0
        report.admit_reasons = admission.reasons
        report.blank_values = admission.blank_values
        if not admission.ok:
            report.aborted = "admission_refused"
            report.key_diff_missing = admission.keys_missing
            report.key_diff_extra = admission.keys_extra
            report.code_unchanged = 1 if _sha256(module_file) == before_sha else 0
            return report.finalize()

        if getattr(module, "active_locale", None) is not None and module.active_locale() == new_code:
            # 同一进程里第二次跑：先把活动语言退回源串语言，否则 `set_locale` 按契约返回
            # `changed=False`（避免无谓重绘）会把"真的切过去了"这条判据打成假红。
            module.set_locale(getattr(module, "DEFAULT_LANGUAGE", "en"), dirs=[str(builtin)])
        change = module.set_locale(new_code, dirs=[str(user_dir), str(builtin)])
        report.switched = 1 if (
            change.resolved == new_code and change.catalog_present and bool(change.changed)
        ) else 0
        report.resolved = change.resolved or ""
        report.keys_in_new_catalog = change.keys
        got = module.t(probe_key)
        report.translated = 1 if got == pseudo_translation(base_strings[probe_key]) else 0
        report.probe = f"{probe_key}={got[:48]!r}"
        fallback_value = module.t(blank_key)
        report.fallback_ok = 1 if (
            fallback_value == base_strings[blank_key] and fallback_value.strip()
        ) else 0

        new_catalog = module.load_catalog(new_code, dirs=[str(user_dir), str(builtin)])
        base_catalog = module.load_catalog(getattr(module, "DEFAULT_LANGUAGE", "en"), dirs=[str(builtin)])
        missing, extra = module.key_diff(base_catalog, new_catalog)
        report.key_diff_missing = len(missing)
        report.key_diff_extra = len(extra)
        report.missing_examples = sorted(missing)[:MAX_EXAMPLES]

        # ③ 语言清单来自扫盘：同一目录里的源码常量不得造出语言。
        code_only_dir = workdir / "code-only"
        code_only_dir.mkdir()
        constants = "SUPPORTED_LANGUAGES = (" + ", ".join(
            json.dumps(item) for item in (new_code, getattr(module, "DEFAULT_LANGUAGE", "en"))
        ) + ")\n"
        (code_only_dir / "language_constants.py").write_text(constants, encoding="utf-8")
        seen_codes = [
            info.code for info in module.scan_catalogs([str(code_only_dir), str(builtin)]) if info.valid
        ]
        report.code_only_no_newlang = 1 if new_code not in seen_codes else 0

        # ② 没有 `_meta` ⇒ 准入层必须拒（并如实记录底层容忍这一事实）。
        no_meta_dir = workdir / "no-meta"
        no_meta_path = write_catalog(
            no_meta_dir / f"{new_code}.json",
            build_payload(base_payload, code=new_code, fixture="no_meta", blank_key=blank_key),
        )
        module.clear_cache()
        no_meta_admit = admit_catalog_file(no_meta_path, module=module, base_file=base_file)
        report.meta_absent_rejected = 1 if (
            not no_meta_admit.ok and "meta_absent" in no_meta_admit.reasons
        ) else 0
        raw_infos = module.scan_catalogs([str(no_meta_dir), str(builtin)])
        raw_found = next((info for info in raw_infos if info.code == new_code), None)
        report.baselib_tolerates_meta_absent = 1 if (
            raw_found is not None and raw_found.valid and raw_found.license is None
        ) else 0

        # ① 缺一个键 ⇒ 键差集非空且准入判红（尺子看得见红）。
        drop_dir = workdir / "drop-key"
        drop_path = write_catalog(
            drop_dir / f"{new_code}.json",
            build_payload(base_payload, code=new_code, fixture="drop_key",
                          blank_key=blank_key, drop_key=probe_key),
        )
        drop_admit = admit_catalog_file(drop_path, module=module, base_file=base_file)
        report.drop_key_detected = 1 if (
            not drop_admit.ok and drop_admit.keys_missing >= 1
            and probe_key in module.key_diff(
                base_catalog, module.load_catalog(new_code, dirs=[str(drop_dir), str(builtin)])
            )[0]
        ) else 0

        # 源码字面量：复用 G1 的尺子。
        scanner, scan_why = load_g1_scanner()
        if scanner is None:
            report.locale_literals = -1
            report.hardcoded_literals = -1
            report.reason = scan_why or "g1_scanner_unavailable"
        else:
            scan = scanner.scan_source_literals(Path(source_tree) if source_tree else tree)
            report.locale_literals = scan.locale_literals
            report.hardcoded_literals = scan.hardcoded_literals
            report.files_scanned = scan.files_scanned
            report.locale_examples = list(scan.locale_examples)

        report.code_unchanged = 1 if (
            _sha256(module_file) == before_sha and _sha256(base_file) == base_file_sha
        ) else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return report.finalize()


# ── 自检：阴阳双证 ──────────────────────────────────────────────────────────────────────
def selftest(
    *,
    tree: Path | None = None,
    builtin: Path | None = None,
    new_code: str = NEW_LANGUAGE,
) -> int:
    """干净夹具必绿 + 三种做坏必红；尺子看不见红 ⇒ 自检 rc=1。"""
    tree = Path(tree) if tree else DEFAULT_TREE
    builtin = Path(builtin) if builtin else DEFAULT_BUILTIN
    cases: list[tuple[str, int, dict[str, Any], Report]] = []

    clean = run_check(new_code, tree=tree, builtin=builtin, fixture="clean")
    cases.append(("clean", RC_PASS, {"verdict": "PASS", "translated": 1, "admit": 1,
                                     "catalogs_before": (">=", 1), "locale_literals": 0}, clean))

    dropped = run_check(new_code, tree=tree, builtin=builtin, fixture="drop_key")
    cases.append(("mutant_drop_key", RC_FAIL, {"verdict": "FAIL", "key_diff_missing": (">=", 1),
                                              "admit": 0, "aborted": "admission_refused"}, dropped))

    no_meta = run_check(new_code, tree=tree, builtin=builtin, fixture="no_meta")
    cases.append(("mutant_no_meta", RC_FAIL, {"verdict": "FAIL", "admit": 0,
                                             "aborted": "admission_refused"}, no_meta))

    workdir = Path(tempfile.mkdtemp(prefix="i18n-newlang-selftest-"))
    try:
        probe_tree = workdir / "mutant-tree"
        (probe_tree / "cadcore" / "i18n").mkdir(parents=True)
        literal = "-".join(("zh", "CN"))
        (probe_tree / "cadcore" / "i18n" / "probe_hardcoded_language.py").write_text(
            "PROBE_LANGUAGE = " + json.dumps(literal) + "\n", encoding="utf-8"
        )
        mutant = run_check(new_code, tree=tree, builtin=builtin, fixture="clean", source_tree=probe_tree)
        cases.append(("mutant_source_locale_literal", RC_FAIL,
                      {"verdict": "FAIL", "locale_literals": (">=", 1)}, mutant))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    ok = 0
    for name, expect_rc, expect, report in cases:
        matched = report.rc == expect_rc
        for field_name, wanted in expect.items():
            got = getattr(report, field_name, None)
            if isinstance(wanted, tuple):
                op, bound = wanted
                matched = matched and isinstance(got, int) and (
                    got >= bound if op == ">=" else got == bound
                )
            else:
                matched = matched and got == wanted
        ok += 1 if matched else 0
        print(f"{SELFTEST_GATE}_CASE: case={name} expect_rc={expect_rc} got_rc={report.rc} "
              f"ok={1 if matched else 0} || {report.line()}")

    verdict = "PASS" if ok == len(cases) else "FAIL"
    print(f"{SELFTEST_GATE}: verdict={verdict} cases={len(cases)} ok={ok} "
          f"new_language={new_code} tree={tree}")
    return RC_PASS if ok == len(cases) else RC_FAIL


# ── CLI ────────────────────────────────────────────────────────────────────────────────
def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verdict line G8: adding a language needs no code change")
    parser.add_argument("--check", action="store_true", help="run the empirical check (rc 0/1/2)")
    parser.add_argument("--selftest", action="store_true", help="clean fixture green + three mutants red")
    parser.add_argument("--lang", default=NEW_LANGUAGE, help="language code of the fixture catalog")
    parser.add_argument("--tree", default=None, help="product tree holding cadcore.i18n.catalog")
    parser.add_argument("--builtin", default=None, help="built-in catalog dir (source-string language)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.selftest:
        return selftest(
            tree=Path(args.tree) if args.tree else None,
            builtin=Path(args.builtin) if args.builtin else None,
            new_code=args.lang,
        )
    if args.check:
        report = run_check(
            args.lang,
            tree=Path(args.tree) if args.tree else None,
            builtin=Path(args.builtin) if args.builtin else None,
        )
        print(report.line())
        return report.rc
    print(f"{GATE}: verdict=UNMEASURED reason=no_mode_selected_use_--check_or_--selftest")
    return RC_UNMEASURED


if __name__ == "__main__":
    raise SystemExit(main())
