"""cadcore.i18n.standards —— A17 制图标准档读取层（**纯数据**：标准代号只许出现在数据里）。

一句话用途
----------
把 `cadcore/data/standards/<id>.json` 读成 `StandardProfile`，供出图层查投影角、图幅、线宽、
标题栏模板等**随标准而变**的约定 —— 纯标准库、无 Qt、无第三方依赖。

为什么这样设计
--------------
1. **标准差异走数据，不走代码分支**：本模块里没有一处 `if standard == "<id>"`。
   `STANDARD_IDS` 也**不是**一张写死的常量表，而是扫 `data/standards/*.json` 得来
   （契约里那张五档顺序只作**排序提示与目录缺席时的兜底**）。于是加一档标准 = 往目录丢一个 JSON。
2. **未核标字段 = `None`，禁先写死**：真源里 `null` 且登记进 `na_fields` 的字段，
   `projection_angle_for()` 一律返回 `None`（= NA），调用方必须显式处理；
   **绝不拿记忆或邻档的值去补**。未知标准 id 与"字段未核"是两件事，不许同形：
   前者抛 `I18nError`，后者返回 `None`。
3. **不许两个真源**：出图层 `cadcore.draw.sheet.load_profile()` 优先调本模块的 `get_profile()`，
   两处读的是同一批文件；本模块另外提供 `audit_drift()` 作**独立复算**的尺子
   （它自己重新 `json.loads`，不复用解析函数），把「声明 vs 盘上」双向差集与
   `na_fields` 自洽性一次报清：`0`=真为零 / `-1`=未采 / `NA`=不适用。
4. **语言轴与标准轴正交**：`default_profile_for_locale()` 只读 `data/i18n/` 里
   `languages.json` 与各语言包 `_meta.default_standard` 声明的映射，**不读界面当前语言**
   （本模块不导入 `cadcore.i18n.catalog`），且仅用于新建文档的默认值，用户可覆盖。

对外契约（照抄 `MVP-INTERFACES.md` §B A17）
------------------------------------------
`STANDARD_IDS` · `load_profiles(dir_=None)` · `get_profile(id)` ·
`projection_angle_for(id)`（未核标 ⇒ `None`）· `default_profile_for_locale(code)`
（另附只读辅助：`PROFILE_FIELDS` / `PROJECTION_ANGLES` / `StandardProfile` /
`resolve_default_profile` / `audit_drift` / `standards_dir` / `clear_cache`）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from ..errors import I18nError

__all__ = [
    "PROFILE_FIELDS",
    "PROJECTION_ANGLES",
    "STANDARD_IDS",
    "StandardProfile",
    "audit_drift",
    "clear_cache",
    "default_profile_for_locale",
    "get_profile",
    "load_profiles",
    "projection_angle_for",
    "resolve_default_profile",
    "standards_dir",
]

LOG = logging.getLogger("cadcore.i18n.standards")

#: 标准档真源字段名（`MVP-INTERFACES.md` §C 冻结，顺序即契约顺序）。
PROFILE_FIELDS: tuple[str, ...] = (
    "projection_angle",
    "sheet_sizes",
    "line_widths",
    "text_height_series",
    "default_font_family",
    "decimal_separator",
    "title_block_template",
    "dimension_terminator",
    "gdt_symbol_set",
    "units",
    "tolerance",
)

#: 投影角契约取值（`Literal["first","third"]`）；这是**枚举白名单**，不是任何标准档的默认值。
PROJECTION_ANGLES: frozenset[str] = frozenset({"first", "third"})

#: 机读未核标清单字段名与来源字段名。
NA_FIELDS_KEY = "na_fields"
SOURCE_KEY = "source"
DOC_KEY = "_doc"

#: 数据根与子目录名（与 `cadcore.draw.sheet` 读的是同一棵树）。
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STANDARDS_DIR_NAME = "standards"
I18N_DIR_NAME = "i18n"
LANGUAGES_FILENAME = "languages.json"
DEFAULT_STANDARD_KEY = "default_standard"

#: 契约冻结的五档顺序：**只**用于排序提示与"目录整个缺席"时的兜底，内容真源永远是数据目录。
_CONTRACT_STANDARD_ORDER: tuple[str, ...] = ("iso", "gbt", "ansi", "din", "jis")

_profile_cache: dict[str, dict[str, StandardProfile]] = {}


def standards_dir(dir_: str | Path | None = None) -> Path:
    """标准档目录（`dir_=None` ⇒ 包内 `cadcore/data/standards`）。"""
    if dir_ is None:
        return _DATA_DIR / STANDARDS_DIR_NAME
    return Path(dir_).expanduser()


def _scan_standard_ids(directory: Path) -> tuple[str, ...]:
    """扫目录得到标准档 id（跳过 `_` 前缀文件；目录不存在 ⇒ 空元组，不抛）。"""
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.stem for path in directory.glob("*.json")
                        if not path.name.startswith("_")))


def _order_ids(on_disk: tuple[str, ...]) -> tuple[str, ...]:
    """契约顺序优先、目录里的新档按名排在后面（加档只需丢 JSON，不必改代码）。"""
    known = [item for item in _CONTRACT_STANDARD_ORDER if item in on_disk]
    extra = sorted(item for item in on_disk if item not in _CONTRACT_STANDARD_ORDER)
    return tuple(known + extra)


#: 可用标准档 id（导入时扫盘得出；目录缺席 ⇒ 退回契约顺序，读取时仍会显式报错而不是假绿）。
STANDARD_IDS: tuple[str, ...] = (
    _order_ids(_scan_standard_ids(_DATA_DIR / STANDARDS_DIR_NAME)) or _CONTRACT_STANDARD_ORDER
)


@dataclass(frozen=True)
class StandardProfile:
    """一档标准档的只读视图；`fields` 只含契约字段，缺失/未核一律 `None`。"""

    standard_id: str
    fields: Mapping[str, Any]
    na_fields: tuple[str, ...] = ()
    source: Mapping[str, Any] | None = None
    path: str | None = None

    def __getattr__(self, name: str) -> Any:
        if name in PROFILE_FIELDS:
            return dict(self.__dict__.get("fields") or {}).get(name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def is_na(self, field: str) -> bool:
        """该字段是否登记为"未核"（`na_fields` 命中 ⇒ 值为 `None` 且**不许**猜）。"""
        return field in self.na_fields

    def as_dict(self) -> dict[str, Any]:
        """普通 dict 形态（字段名与裸 JSON 一致，供 `cadcore.draw.sheet` 无缝消费）。"""
        payload: dict[str, Any] = {"standard_id": self.standard_id}
        for key in PROFILE_FIELDS:
            payload[key] = self.fields.get(key)
        payload[NA_FIELDS_KEY] = list(self.na_fields)
        if self.source is not None:
            payload[SOURCE_KEY] = dict(self.source)
        return payload


def clear_cache() -> None:
    """清空已加载的标准档缓存（测试与"改完数据立刻生效"用）。"""
    _profile_cache.clear()


def _read_json(path: Path) -> tuple[Any, str | None]:
    """读 JSON → `(数据, 错误串)`；错误串非空表示未采（调用方决定是抛还是记）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _parse_profile(standard_id: str, path: Path) -> StandardProfile:
    """读一个档并做自洽检查；坏文件/自相矛盾 ⇒ 抛 `I18nError`（**不猜、不回落**）。"""
    payload, error = _read_json(path)
    if error is not None:
        raise I18nError("standard profile unreadable",
                        reason="standard_profile_parse_failed",
                        standard=standard_id, path=str(path), detail=error)
    if not isinstance(payload, Mapping):
        raise I18nError("standard profile root is not a JSON object",
                        reason="standard_profile_not_object",
                        standard=standard_id, path=str(path))
    fields = {key: payload.get(key) for key in PROFILE_FIELDS}
    declared = payload.get(NA_FIELDS_KEY)
    if declared is None:
        na_fields = tuple(key for key, value in fields.items() if value is None)
    elif isinstance(declared, (list, tuple)):
        na_fields = tuple(str(item) for item in declared)
    else:
        raise I18nError("na_fields is neither a list nor absent",
                        reason="na_fields_malformed",
                        standard=standard_id, path=str(path), got=type(declared).__name__)
    nulls = tuple(key for key, value in fields.items() if value is None)
    if set(na_fields) != set(nulls) or len(na_fields) != len(nulls):
        raise I18nError("na_fields does not match the null-valued contract fields",
                        reason="na_fields_mismatch",
                        standard=standard_id, path=str(path),
                        na_fields=sorted(na_fields), null_fields=sorted(nulls))
    source = payload.get(SOURCE_KEY)
    if source is not None and not isinstance(source, Mapping):
        raise I18nError("source block is not a JSON object",
                        reason="standard_profile_source_malformed",
                        standard=standard_id, path=str(path))
    unknown = sorted(key for key in payload
                     if key not in PROFILE_FIELDS
                     and key not in (NA_FIELDS_KEY, SOURCE_KEY, DOC_KEY, "standard_id"))
    if unknown:
        LOG.warning("standard %s carries keys outside the contract: %s", standard_id, unknown)
    return StandardProfile(standard_id=standard_id, fields=fields, na_fields=na_fields,
                           source=dict(source) if isinstance(source, Mapping) else None,
                           path=str(path))


def load_profiles(dir_: str | Path | None = None) -> dict[str, StandardProfile]:
    """扫目录读全部标准档 → `{id: StandardProfile}`（按契约顺序，目录里的新档排后）。

    目录不存在 / 一个档都没扫到 ⇒ 抛 `I18nError`（**空集不许假绿**：那是"未采"，不是"0 档"）。
    """
    directory = standards_dir(dir_)
    if not directory.is_dir():
        raise I18nError("standard profile directory not found",
                        reason="standards_dir_missing", dir=str(directory))
    key = str(directory)
    cached = _profile_cache.get(key)
    if cached is not None:
        return dict(cached)
    paths = [path for path in sorted(directory.glob("*.json"))
             if not path.name.startswith("_")]
    if not paths:
        raise I18nError("no standard profiles found in directory",
                        reason="standards_dir_empty", dir=str(directory))
    profiles: dict[str, StandardProfile] = {}
    for path in paths:
        profile = _parse_profile(path.stem, path)
        profiles[profile.standard_id] = profile
    order = _order_ids(tuple(profiles)) if dir_ is None else tuple(sorted(profiles))
    ordered = {standard_id: profiles[standard_id] for standard_id in order}
    _profile_cache[key] = ordered
    return dict(ordered)


def get_profile(standard_id: str) -> StandardProfile:
    """按 id 取标准档；未知 id ⇒ 抛 `I18nError`（**未知 ≠ 未核**，不许回落成 None）。"""
    key = str(standard_id or "").strip()
    profiles = load_profiles()
    profile = profiles.get(key)
    if profile is None:
        raise I18nError("unknown standard profile",
                        reason="unknown_standard", standard=key, known=list(profiles))
    return profile


def projection_angle_for(standard_id: str) -> Literal["first", "third"] | None:
    """投影角：`"first"` / `"third"` / `None`(= 未核 NA，调用方必须显式处理，**禁先写死**)。

    未核标档的值一律从数据里读 `null` 得来，本函数体内**不含任何角度字面量**；
    未知标准 id ⇒ 抛 `I18nError`（与"未核"分开，两态不同形）。
    """
    profile = get_profile(standard_id)
    value = profile.fields.get("projection_angle")
    if value is None:
        LOG.info("projection angle not fetched (NA) for standard %s", profile.standard_id)
        return None
    if not isinstance(value, str) or value not in PROJECTION_ANGLES:
        raise I18nError("projection_angle outside the contract enum",
                        reason="projection_angle_invalid",
                        standard=profile.standard_id, value=repr(value))
    return cast('Literal["first", "third"]', value)


def _locale_defaults() -> dict[str, tuple[str, str]]:
    """`语言码 → (标准 id, 声明出处)`，只来自 `data/i18n/` 的**数据声明**（不读活动语言）。"""
    directory = _DATA_DIR / I18N_DIR_NAME
    mapping: dict[str, tuple[str, str]] = {}
    manifest = directory / LANGUAGES_FILENAME
    if manifest.is_file():
        payload, error = _read_json(manifest)
        if error is not None:
            LOG.warning("locale manifest unreadable (%s): %s", manifest, error)
        entries: Any = payload
        if isinstance(payload, Mapping):
            entries = payload.get("languages")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                code = str(entry.get("code") or "").strip()
                declared = entry.get(DEFAULT_STANDARD_KEY)
                if code and isinstance(declared, str) and declared.strip():
                    mapping[code] = (declared.strip(), f"{LANGUAGES_FILENAME}:{code}")
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            if path.name == LANGUAGES_FILENAME or path.name.startswith("_"):
                continue
            payload, error = _read_json(path)
            if error is not None:
                LOG.warning("catalog unreadable (%s): %s", path, error)
                continue
            meta = payload.get("_meta") if isinstance(payload, Mapping) else None
            if not isinstance(meta, Mapping):
                continue
            code = str(meta.get("code") or path.stem).strip()
            declared = meta.get(DEFAULT_STANDARD_KEY)
            if code and isinstance(declared, str) and declared.strip() and code not in mapping:
                mapping[code] = (declared.strip(), path.name)
    return mapping


def _fallback_standard_id() -> str:
    """未知语言码的兜底档：数据目录里排第一的档（`STANDARD_IDS[0]`），一个都没有 ⇒ 抛错。"""
    if STANDARD_IDS:
        return STANDARD_IDS[0]
    on_disk = _order_ids(_scan_standard_ids(_DATA_DIR / STANDARDS_DIR_NAME))
    if on_disk:
        return on_disk[0]
    raise I18nError("no standard profile available for the default mapping",
                    reason="standards_dir_missing", dir=str(standards_dir()))


def _known_standard_ids() -> tuple[str, ...]:
    try:
        return tuple(load_profiles())
    except I18nError as exc:
        LOG.warning("standard profiles unavailable (%s); using the scanned id list", exc.reason)
        return STANDARD_IDS


def resolve_default_profile(code: str) -> dict[str, Any]:
    """`default_profile_for_locale()` 的可查形态：多给 `matched` / `grade` / `source` 三个诊断字段。

    `grade` 取值：`exact` / `truncated` / `primary_subtag` / `ambiguous_primary_subtag` /
    `empty_code` / `unknown_locale` / `unknown_declared_standard`。
    后三者走兜底档（**全部记日志**，不静默），`standard_id` 一定是盘上真实存在的档。
    """
    text = str(code or "").strip().replace("_", "-")
    mapping = _locale_defaults()
    folded = {key.casefold(): (key, value) for key, value in mapping.items()}
    known = _known_standard_ids()
    fallback = _fallback_standard_id()

    def _finish(standard_id: str, *, matched: str | None, grade: str, source: str | None) -> dict[str, Any]:
        if standard_id not in known:
            LOG.warning("declared default standard %r for locale %r is not on disk; falling back",
                        standard_id, text)
            return {"code": text, "standard_id": fallback, "matched": matched,
                    "grade": "unknown_declared_standard", "source": source,
                    "declared": standard_id}
        return {"code": text, "standard_id": standard_id, "matched": matched,
                "grade": grade, "source": source, "declared": standard_id}

    if not text:
        LOG.warning("empty locale code: falling back to the first standard profile")
        return {"code": text, "standard_id": fallback, "matched": None,
                "grade": "empty_code", "source": None}
    parts = [part for part in text.split("-") if part]
    for size in range(len(parts), 0, -1):
        candidate = "-".join(parts[:size])
        hit = folded.get(candidate.casefold())
        if hit is not None:
            declared_code, (standard_id, source) = hit
            grade = "exact" if size == len(parts) else "truncated"
            return _finish(standard_id, matched=declared_code, grade=grade, source=source)
    primary = parts[0].casefold()
    same_primary = [key for key in mapping if key.split("-")[0].casefold() == primary]
    if len(same_primary) == 1:
        declared_code = same_primary[0]
        standard_id, source = mapping[declared_code]
        return _finish(standard_id, matched=declared_code, grade="primary_subtag", source=source)
    if len(same_primary) > 1:
        LOG.warning("ambiguous locale %r: %s share the primary subtag; falling back",
                    text, sorted(same_primary))
        return {"code": text, "standard_id": fallback, "matched": None,
                "grade": "ambiguous_primary_subtag", "source": None,
                "candidates": sorted(same_primary)}
    LOG.warning("unknown locale %r: falling back to the first standard profile", text)
    return {"code": text, "standard_id": fallback, "matched": None,
            "grade": "unknown_locale", "source": None}


def default_profile_for_locale(code: str) -> str:
    """语言码 → 新建文档的默认标准档 id（**仅默认值**，用户可覆盖；与界面语言正交）。

    映射来自 `data/i18n/` 的数据声明（`languages.json` 与各语言包 `_meta.default_standard`），
    本函数不看 `active_locale()`、不导入 `cadcore.i18n.catalog`；未知语言码走兜底并在
    `resolve_default_profile()` 里留下 `grade`，**不静默**。
    """
    return str(resolve_default_profile(code)["standard_id"])


def audit_drift(dir_: str | Path | None = None) -> dict[str, Any]:
    """独立复算：「声明 `STANDARD_IDS` vs 盘上」「`na_fields` vs 空值字段」双向差集。

    **不复用** `_parse_profile()`，自己重新 `json.loads` —— 两把尺子互为对照。
    三态：`verdict=PASS`（0 差异）/ `FAIL`（有差异）/ `NA`（目录缺失或不可读，计数字段记 `-1`）。
    """
    directory = standards_dir(dir_)
    if not directory.is_dir():
        return {"verdict": "NA", "dir": str(directory), "reason": "standards_dir_missing",
                "profiles": -1, "declared": -1, "missing_on_disk": -1,
                "undeclared_on_disk": -1, "na_fields_mismatch": -1}
    paths = [path for path in sorted(directory.glob("*.json")) if not path.name.startswith("_")]
    on_disk = tuple(path.stem for path in paths)
    if not on_disk:
        return {"verdict": "NA", "dir": str(directory), "reason": "standards_dir_empty",
                "profiles": 0, "declared": -1, "missing_on_disk": -1,
                "undeclared_on_disk": -1, "na_fields_mismatch": -1}
    declared = STANDARD_IDS if dir_ is None else on_disk
    missing = sorted(item for item in declared if item not in on_disk)
    undeclared = sorted(item for item in on_disk if item not in declared)
    mismatched: list[str] = []
    unreadable: list[str] = []
    for path in paths:
        payload, error = _read_json(path)
        if error is not None or not isinstance(payload, Mapping):
            unreadable.append(path.stem)
            continue
        nulls = {key for key in PROFILE_FIELDS if payload.get(key) is None}
        listed = payload.get(NA_FIELDS_KEY)
        listed_set = {str(item) for item in listed} if isinstance(listed, list) else None
        if listed_set != nulls:
            mismatched.append(path.stem)
    verdict = "PASS" if not (missing or undeclared or mismatched or unreadable) else "FAIL"
    return {"verdict": verdict, "dir": str(directory), "reason": "-",
            "profiles": len(on_disk), "declared": len(declared),
            "missing_on_disk": len(missing), "undeclared_on_disk": len(undeclared),
            "na_fields_mismatch": len(mismatched), "unreadable": len(unreadable),
            "missing_ids": missing, "undeclared_ids": undeclared,
            "mismatch_ids": mismatched, "unreadable_ids": unreadable}
