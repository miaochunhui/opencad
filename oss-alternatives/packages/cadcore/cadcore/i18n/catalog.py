"""cadcore.i18n.catalog —— A16 语言层：语言包 = 可扫盘的 JSON 数据 + 显式回退链 + 运行时热切换。

一句话用途
----------
给整个 CAD 内核提供「界面语言」这一层：扫目录得到语言清单、按 BCP-47 逐级回落取译文、
运行期换活动语言包并广播 —— **纯标准库、无 Qt 依赖**（Qt 壳与 `QTranslator` 接线属下一期）。

为什么这样设计
--------------
1. **语言是数据不是代码**：语言清单来自扫目录 + `languages.json`，回退链来自标签本身的截断规则。
   于是「加一门语言」= 往目录丢一个 JSON（**不改一行代码、不重启进程**）；
   反过来，源码里按语言码分支（形如 `if lang == <某个具体标签>`）会把"支持哪些语言"钉成编译期决定，
   而本项目的语言集是运行期还在长的东西（用户级附加翻译目录优先，见 `default_dirs`）。
2. **缺译文的行为必须可查**：查不到就沿显式回退链回落英文源串并**记日志**，
   连英文都没有时返回由 key 派生的人读兜底串 —— **绝不把 key 名或空串摆到用户眼前**。
3. **三态记账（`0` 真为零 / `-1` 未采 / `NA` 不适用）**：一个语言包都没扫到时，
   `LanguageChange.keys` 记 `-1`（未采，不是"0 条译文"），`catalog_present=False`，
   降级是**可观测**的，不做"看起来加载成功"的假绿。
4. **本文件所有字符串字面量一律 ASCII**：面向用户的文案只许来自语言包，
   源码里不留任何语言的文案（机检口径见 I18N-DESIGN §3 的 `hardcoded_literals` / `locale_literals`）。

对外契约（照抄 `MVP-INTERFACES.md` §B A16）
------------------------------------------
`DEFAULT_LANGUAGE` · `scan_catalogs` · `load_catalog` · `resolve_chain` · `t` ·
`set_locale` · `on_language_changed` · `active_locale` · `key_diff` · `import_po` · `export_po`
（另附少量只读辅助：`default_dirs` / `available_locales` / `active_catalog` / `active_chain` /
`placeholder_diff` / `stats` / `untranslated_keys` / `clear_cache`）。
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import re
import string
import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "DEFAULT_LANGUAGE",
    "MANIFEST_FILENAME",
    "CatalogError",
    "LocaleUnavailable",
    "Catalog",
    "CatalogInfo",
    "CatalogContext",
    "LanguageChange",
    "active_catalog",
    "active_chain",
    "active_locale",
    "available_locales",
    "bind",
    "clear_cache",
    "default_dirs",
    "export_po",
    "import_po",
    "key_diff",
    "load_catalog",
    "on_language_changed",
    "placeholder_diff",
    "resolve_chain",
    "scan_catalogs",
    "set_locale",
    "stats",
    "t",
    "untranslated_keys",
]

LOG = logging.getLogger("cadcore.i18n.catalog")

#: 源串语言（`t()` 的链尾）。这是契约常量，不是"支持的语言清单"。
DEFAULT_LANGUAGE: str = "en"

#: 人读清单文件名；程序只读清单与目录，不读源码分支。
MANIFEST_FILENAME = "languages.json"

#: 语言包文件后缀；`_` 前缀文件（模板等）不进清单。
CATALOG_SUFFIX = ".json"

#: 用户级附加翻译目录的子目录名（`$XDG_DATA_HOME/<APP_DIR_NAME>/i18n` 等）。
APP_DIR_NAME = "cadcore"

#: 环境变量：追加用户语言包目录（`os.pathsep` 分隔，顺序即优先级，排在最前）。
ENV_DIRS = "CADCORE_I18N_DIRS"

#: `_meta` 字段（数据模型冻结）：缺一项就补 `None`，**不许编数**。
META_FIELDS = (
    "code",
    "native_name",
    "fallback",
    "font_fallback",
    "default_standard",
    "license",
    "source",
    "version",
)

_FORMATTER = string.Formatter()
_KEY_SHAPE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
_WHITESPACE_RE = re.compile(r"\s+")


class CatalogError(Exception):
    """语言层的显式失败信号（坏文件、无法确定语言码、清单不可用）。

    单独定义而不依赖 `cadcore.errors`，是为了让本模块**零包内依赖**：
    语言层必须在任何兄弟模块落盘之前就能单独跑起来（缺席即降级，绝不在 import 期硬失败）。
    """


class LocaleUnavailable(CatalogError):
    """请求的语言在当前目录集合里解析不到（且环境里**并非**一个语言包都没有）。"""


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogInfo:
    """一个语言包文件的**清单项**：扫盘的产物，不含全部译文（避免为列语言而读全量字符串）。

    `valid=False` 的条目**也在返回列表里**：坏文件不许被静默丢掉（E2「没算与没失败不许同形」），
    调用方按 `valid` 过滤，`available_locales()` 只取 valid。
    """

    code: str
    native_name: str
    path: str
    dir_index: int
    valid: bool = True
    error: str | None = None
    shadowed_path: str | None = None
    font_fallback: tuple[str, ...] = ()
    default_standard: str | None = None
    version: Any = None
    license: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "native_name": self.native_name,
            "path": self.path,
            "dir_index": self.dir_index,
            "valid": self.valid,
            "error": self.error,
            "shadowed_path": self.shadowed_path,
            "font_fallback": list(self.font_fallback),
            "default_standard": self.default_standard,
            "version": self.version,
            "license": self.license,
        }


@dataclass(frozen=True)
class Catalog:
    """一门语言的语言包（`_meta` + `strings`）。`requested != code` ⇒ 经回退链降级命中。"""

    code: str
    strings: Mapping[str, str]
    meta: Mapping[str, Any] = field(default_factory=dict)
    path: str | None = None
    requested: str | None = None

    # ── 取用 ────────────────────────────────────────────────────────────────────────
    def get(self, key: str, default: str | None = None) -> str | None:
        """取译文；空串/空白串等同"没有"（不然 `t()` 会把空串摆给用户）。"""
        value = self.strings.get(key, default)
        if isinstance(value, str) and not value.strip():
            return default
        return value

    def keys(self) -> set[str]:
        """键集合（`key_diff` 的双向差集用）。"""
        return set(self.strings.keys())

    # ── 元信息（缺失一律回落为"未知"，不编造）────────────────────────────────────────
    def native_name(self) -> str:
        value = self.meta.get("native_name")
        return str(value) if value else self.code

    def fallback(self) -> str | None:
        value = self.meta.get("fallback")
        return str(value) if value else None

    def font_fallback(self) -> tuple[str, ...]:
        return _as_str_tuple(self.meta.get("font_fallback"))

    def default_standard(self) -> str | None:
        value = self.meta.get("default_standard")
        return str(value) if value else None

    def chain(self) -> list[str]:
        """本语言包自身 code 的回退链（含声明式 `_meta.fallback`）。"""
        return _chain_with_declared(self.code, self.fallback())

    # ── 序列化 ──────────────────────────────────────────────────────────────────────
    def as_dict(self) -> dict[str, Any]:
        """还原成语言包 JSON 结构（`strings` 按键排序，两次调用逐字节可复现）。"""
        meta = {name: self.meta.get(name) for name in META_FIELDS}
        for name, value in self.meta.items():
            meta.setdefault(name, value)
        meta["code"] = self.code
        return {"_meta": meta, "strings": {k: self.strings[k] for k in sorted(self.strings)}}

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        path: str | None = None,
        requested: str | None = None,
        code: str | None = None,
    ) -> "Catalog":
        """从 JSON 结构构造；`strings` 逐条要求 `str -> str`，非法条目**抛错不吞**。"""
        if not isinstance(payload, Mapping):
            raise CatalogError("catalog payload must be a JSON object")
        meta_raw = payload.get("_meta")
        meta: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, Mapping) else {}
        strings_raw = payload.get("strings")
        if not isinstance(strings_raw, Mapping):
            raise CatalogError("catalog payload is missing the 'strings' object")
        strings: dict[str, str] = {}
        for key, value in strings_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise CatalogError(f"catalog entry must be str -> str (offending key: {key!r})")
            strings[key] = value
        ranges = meta.get("forbidden_codepoint_ranges", [])
        if not isinstance(ranges, list):
            raise CatalogError("forbidden_codepoint_ranges must be a list of hexadecimal pairs")
        for bounds in ranges:
            if not (isinstance(bounds, list) and len(bounds) == 2
                    and all(isinstance(bound, str) for bound in bounds)):
                raise CatalogError("forbidden_codepoint_ranges must contain hexadecimal pairs")
            try:
                low, high = (int(bound, 16) for bound in bounds)
            except ValueError as exc:
                raise CatalogError("invalid hexadecimal codepoint range") from exc
            if not 0 <= low <= high <= 0x10FFFF:
                raise CatalogError("invalid codepoint range bounds")
            for key, value in strings.items():
                offending = next((ord(char) for char in value if low <= ord(char) <= high), None)
                if offending is not None:
                    raise CatalogError(f"forbidden codepoint U+{offending:04X} in catalog entry {key!r}")
        resolved_code = _normalize(code or meta.get("code") or (Path(path).stem if path else ""))
        if not resolved_code:
            raise CatalogError("catalog has no usable language code in _meta.code or file name")
        meta["code"] = resolved_code
        return cls(code=resolved_code, strings=strings, meta=meta, path=path, requested=requested)


@dataclass(frozen=True)
class LanguageChange:
    """一次语言切换的事实记录（订阅者收到的那份）。

    降级可观测：`catalog_present=False` / `resolved != current`（例如只有 en 可落）时
    `degraded` 为真；`keys == -1` 表示**未采**（没有语言包可数），不是"0 条译文"。
    """

    previous: str
    current: str
    resolved: str | None
    chain: tuple[str, ...]
    catalog_present: bool
    changed: bool
    keys: int
    timestamp: str

    @property
    def degraded(self) -> bool:
        """是否未精确命中（没语言包，或落到了回退链的下一级）。"""
        return not (self.catalog_present and self.resolved == self.current)

    def as_dict(self) -> dict[str, Any]:
        return {
            "previous": self.previous,
            "current": self.current,
            "resolved": self.resolved,
            "chain": list(self.chain),
            "catalog_present": self.catalog_present,
            "changed": self.changed,
            "keys": self.keys,
            "timestamp": self.timestamp,
            "degraded": self.degraded,
        }


# --------------------------------------------------------------------------------------
# 标签规范化 / 回退链（纯函数，无 I/O —— 契约签名只有 code）
# --------------------------------------------------------------------------------------


def _normalize(code: Any) -> str:
    """BCP-47 规范化：`_`→`-`，语言小写、Script 首字母大写、地区大写（`zh_hans_cn`→`zh-Hans-CN`）。"""
    if code is None:
        return ""
    text = str(code).strip().replace("_", "-")
    parts = [p for p in text.split("-") if p]
    if not parts:
        return ""
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            out.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            out.append(part.upper())
        else:
            out.append(part)
    return "-".join(out)


def resolve_chain(code: str) -> list[str]:
    """BCP-47 标签 → 回退链（逐级截断 + 恒定以源串语言收尾）。

    例（本函数只做截断，不读盘、不认识任何具体语种）：
    `zh-Hans-CN` → `zh-Hans-CN` → `zh-Hans` → `zh` → `en`；
    `pt-BR` → `pt-BR` → `pt` → `en`；`sr-Latn-RS` → `sr-Latn-RS` → `sr-Latn` → `sr` → `en`。
    空/非法输入 → 只给 `[DEFAULT_LANGUAGE]`。
    """
    norm = _normalize(code) or DEFAULT_LANGUAGE
    parts = norm.split("-")
    chain: list[str] = []
    for size in range(len(parts), 0, -1):
        candidate = "-".join(parts[:size])
        if candidate not in chain:
            chain.append(candidate)
    if DEFAULT_LANGUAGE not in chain:
        chain.append(DEFAULT_LANGUAGE)
    return chain


def _chain_with_declared(code: str, declared: str | None) -> list[str]:
    """纯截断链 + 语言包自己声明的 `_meta.fallback`（声明式回退优先于通用截断，仍以 en 收尾）。"""
    chain = resolve_chain(code)
    norm = _normalize(declared)
    if norm and norm not in chain:
        chain = [chain[0], norm, *chain[1:]]
    return chain


# --------------------------------------------------------------------------------------
# 目录
# --------------------------------------------------------------------------------------


def _builtin_dir() -> str:
    """随包语言包目录：`cadcore/data/i18n/`（缺席不算错 —— A18 未落盘时就是空的）。"""
    return str(Path(__file__).resolve().parent.parent / "data" / "i18n")


def _user_dir() -> str:
    """平台用户级附加翻译目录（用户级优先，用于"加一门源码还不认识的语言"）。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return str(Path(base) / APP_DIR_NAME / "i18n")


def default_dirs() -> list[str]:
    """默认目录集合，**顺序即优先级**：环境变量追加目录 → 用户级目录 → 随包目录。"""
    dirs: list[str] = []
    for part in os.environ.get(ENV_DIRS, "").split(os.pathsep):
        part = part.strip()
        if part:
            dirs.append(part)
    dirs.append(_user_dir())
    dirs.append(_builtin_dir())
    out: list[str] = []
    for item in dirs:
        if item not in out:
            out.append(item)
    return out


def _as_dirs(dirs: Sequence[str] | None) -> tuple[str, ...]:
    if dirs is None:
        return tuple(default_dirs())
    if isinstance(dirs, (str, bytes, os.PathLike)):
        raise TypeError("dirs must be a sequence of directory paths, not a single path")
    out: list[str] = []
    for item in dirs:
        text = str(item)
        if text and text not in out:
            out.append(text)
    return tuple(out)


# --------------------------------------------------------------------------------------
# JSON 解析缓存（按内容指纹缓存，避免同大小/同时间戳替换时继续使用旧译文）
# --------------------------------------------------------------------------------------

_JSON_CACHE: dict[tuple[str, str], tuple[Any, str | None]] = {}
_CACHE_LIMIT = 512


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝所有层级的重复键，不允许错误译文以 last-wins 静默覆盖。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[Any, str | None]:
    """读 JSON：返回 `(payload, error)`；**任何失败都变成 error 字符串，不抛给扫盘调用方**。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read file: {exc}"
    key = (str(path), hashlib.sha256(raw).hexdigest())
    cached = _JSON_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = raw.decode("utf-8")
        payload: Any = json.loads(text, object_pairs_hook=_unique_object)
        result: tuple[Any, str | None] = (payload, None)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, CatalogError) as exc:
        result = (None, f"{type(exc).__name__}: {exc}")
    for stale in [k for k in _JSON_CACHE if k[0] == key[0] and k != key]:
        _JSON_CACHE.pop(stale, None)
    if len(_JSON_CACHE) > _CACHE_LIMIT:
        _JSON_CACHE.clear()
    _JSON_CACHE[key] = result
    return result


def clear_cache() -> None:
    """清空 JSON 解析缓存（测试/热重载用；扫盘本身没有持久缓存 ⇒ 丢文件进目录即可见）。"""
    with _LOCK:
        _JSON_CACHE.clear()


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if isinstance(v, (str, int, float)) and str(v))
    return ()


def _manifest_entries(payload: Any) -> list[Mapping[str, Any]]:
    """`languages.json` → 条目列表（容忍 `{"languages": [...]}` 与裸数组两种形态）。"""
    if isinstance(payload, Mapping):
        payload = payload.get("languages")
    if not isinstance(payload, (list, tuple)):
        return []
    return [item for item in payload if isinstance(item, Mapping)]


# --------------------------------------------------------------------------------------
# 扫盘
# --------------------------------------------------------------------------------------


def scan_catalogs(dirs: Sequence[str]) -> list[CatalogInfo]:
    """扫盘生成语言清单：**用户目录优先**（dirs 顺序在前者胜），同 code 后者被遮蔽。

    - 只看目录里的 `*.json`（跳过 `languages.json` 与 `_` 前缀文件），
      语言码取自 `_meta.code`，缺省用文件名主干 —— **不读源码里的语言码数组**；
    - `languages.json` 只补 `native_name` / `font_fallback` / `default_standard` 等元信息；
    - 坏文件**保留在结果里**（`valid=False` + `error`），并记下被它遮蔽的路径（`shadowed_path`）；
    - 目录不存在不是错误（A18 未落盘时 `cadcore/data/i18n/` 就是缺席的），只记 debug；
    - 返回按 `code` 排序（显示顺序请调用方按 `native_name` 自行排）。
    """
    dirs_t = _as_dirs(dirs)
    seen: dict[str, CatalogInfo] = {}
    shadowed: dict[str, str] = {}
    manifest: dict[str, Mapping[str, Any]] = {}
    missing_manifest: list[tuple[str, str]] = []

    for index, directory in enumerate(dirs_t):
        base = Path(directory)
        if not base.is_dir():
            LOG.debug("i18n directory absent: %s", directory)
            continue
        payload, error = _read_json(base / MANIFEST_FILENAME)
        if error is not None and (base / MANIFEST_FILENAME).exists():
            LOG.warning("i18n manifest unreadable: %s (%s)", base / MANIFEST_FILENAME, error)
        for entry in _manifest_entries(payload):
            entry_code = _normalize(entry.get("code"))
            if entry_code:
                manifest.setdefault(entry_code, entry)  # 前面的目录优先

        for file_path in sorted(p for p in base.glob("*" + CATALOG_SUFFIX) if p.is_file()):
            if file_path.name == MANIFEST_FILENAME or file_path.name.startswith("_"):
                continue
            file_payload, file_error = _read_json(file_path)
            meta: Mapping[str, Any] = {}
            if isinstance(file_payload, Mapping) and isinstance(file_payload.get("_meta"), Mapping):
                meta = file_payload["_meta"]
            code = _normalize(meta.get("code") or file_path.stem) or file_path.stem
            if code in seen:
                previous = seen[code]
                if previous.valid:
                    LOG.debug("i18n catalog shadowed by higher-priority dir: %s", file_path)
                else:
                    shadowed[code] = str(file_path)
                continue
            if file_error is not None or not isinstance(file_payload, Mapping):
                seen[code] = CatalogInfo(
                    code=code,
                    native_name=code,
                    path=str(file_path),
                    dir_index=index,
                    valid=False,
                    error=file_error or "catalog root is not a JSON object",
                )
                continue
            if not isinstance(file_payload.get("strings"), Mapping):
                seen[code] = CatalogInfo(
                    code=code,
                    native_name=code,
                    path=str(file_path),
                    dir_index=index,
                    valid=False,
                    error="catalog is missing the 'strings' object",
                )
                continue
            try:
                Catalog.from_dict(file_payload, path=str(file_path), code=code)
            except CatalogError as exc:
                seen[code] = CatalogInfo(
                    code=code, native_name=code, path=str(file_path), dir_index=index,
                    valid=False, error=f"{type(exc).__name__}: {exc}",
                )
                continue
            declared = manifest.get(code, {})
            seen[code] = CatalogInfo(
                code=code,
                native_name=str(meta.get("native_name") or declared.get("native_name") or code),
                path=str(file_path),
                dir_index=index,
                font_fallback=_as_str_tuple(meta.get("font_fallback") or declared.get("font_fallback")),
                default_standard=(
                    str(meta.get("default_standard") or declared.get("default_standard"))
                    if (meta.get("default_standard") or declared.get("default_standard"))
                    else None
                ),
                version=meta.get("version", declared.get("version")),
                license=(str(meta["license"]) if meta.get("license") else None),
            )
            for field_name in META_FIELDS:
                if field_name not in meta:
                    LOG.debug("i18n catalog %s _meta lacks %r (kept as unknown, not invented)", code, field_name)

    for code, entry in manifest.items():
        if code not in seen:
            missing_manifest.append((code, str(entry.get("file") or "")))
    for code, declared_file in sorted(missing_manifest):
        LOG.warning(
            "i18n manifest lists code %s (file=%r) but no catalog file was found on disk",
            code,
            declared_file,
        )

    infos: list[CatalogInfo] = []
    for code, info in seen.items():
        if not info.valid and code in shadowed:
            info = replace(info, shadowed_path=shadowed[code])
        infos.append(info)
    infos.sort(key=lambda item: (item.code, item.dir_index))
    return infos


def available_locales(*, dirs: Sequence[str] | None = None) -> list[str]:
    """当前目录集合里**可用**（文件有效）的语言码，按 code 排序。"""
    return [info.code for info in scan_catalogs(_as_dirs(dirs)) if info.valid]


# --------------------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------------------


def _load_exact(code: str, dirs_t: tuple[str, ...], *, strict: bool) -> Catalog | None:
    """只认精确命中（不走链）。命中坏文件：strict ⇒ 抛错；非 strict ⇒ 记警告后跳过。"""
    norm = _normalize(code)
    if not norm:
        return None
    info = next((i for i in scan_catalogs(dirs_t) if i.code == norm), None)
    if info is None:
        return None
    if not info.valid:
        message = f"i18n catalog {norm} is unusable: {info.error} ({info.path})"
        if info.shadowed_path:
            message += (f"; a lower-priority valid file exists at {info.shadowed_path} "
                        f"but user dirs win, so it is NOT used silently")
        if strict:
            raise CatalogError(message)
        LOG.warning("%s -- skipped (degraded lookup)", message)
        return None
    payload, error = _read_json(Path(info.path))
    if error is not None or not isinstance(payload, Mapping):
        message = f"i18n catalog {norm} became unreadable: {error} ({info.path})"
        if strict:
            raise CatalogError(message)
        LOG.warning("%s -- skipped (degraded lookup)", message)
        return None
    return Catalog.from_dict(payload, path=info.path, requested=norm, code=norm)


def load_catalog(code: str, *, dirs: Sequence[str] | None = None) -> Catalog:
    """按 code 装载语言包；**精确命中优先，否则沿 `resolve_chain` 逐级回落**。

    回落命中时 `Catalog.requested` 保留原始请求码、`Catalog.code` 是真正装载到的语言 ⇒
    调用方能看出"这是降级命中"，不会把 `de-AT` 当成有 `de-AT` 译文。
    链上任何一级命中**坏文件**都抛 `CatalogError`（不静默换一门语言顶上）；
    整条链都没有 ⇒ `LocaleUnavailable`。
    """
    dirs_t = _as_dirs(dirs)
    requested = _normalize(code) or DEFAULT_LANGUAGE
    for candidate in resolve_chain(requested):
        catalog = _load_exact(candidate, dirs_t, strict=True)
        if catalog is not None:
            return replace(catalog, requested=requested)
    raise LocaleUnavailable(
        f"no catalog for {requested!r} (chain={resolve_chain(requested)}) in dirs={list(dirs_t)}; "
        f"available={available_locales(dirs=dirs_t)}"
    )


# --------------------------------------------------------------------------------------
# 活动语言（模块级状态；运行时切换 + 广播）
# --------------------------------------------------------------------------------------

_LOCK = threading.RLock()
_TLS = threading.local()

_state: dict[str, Any] = {
    "ready": False,
    "locale": DEFAULT_LANGUAGE,
    "resolved": None,
    "dirs": (),
    "catalogs": {},
    "chain": (),
}
_listeners: list[Callable[[LanguageChange], None]] = []
_stats: dict[str, int] = {
    "lookups": 0,
    "fallbacks": 0,
    "misses": 0,
    "empty_values": 0,
    "format_errors": 0,
    "switches": 0,
}
_missing_keys: dict[str, int] = {}
_logged_fallbacks: set[tuple[str, str, str]] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _activate(locale: str, dirs_t: tuple[str, ...], *, warn_on_empty: bool,
              strict: bool = False) -> dict[str, Any]:
    """装载 locale 回退链上所有可用语言包，写入模块状态；**不抛错**（懒加载期绝不让 `t()` 崩）。

    链 = 纯截断链（`resolve_chain`）+ 各级语言包自己声明的 `_meta.fallback`
    （声明式回退插在**它自己那一级的后面**，所以仍以源串语言收尾）。
    """
    chain: list[str] = resolve_chain(locale)
    catalogs: dict[str, Catalog] = {}
    index = 0
    while index < len(chain):
        candidate = chain[index]
        catalog = _load_exact(candidate, dirs_t, strict=strict)
        if catalog is not None:
            catalogs[candidate] = catalog
            declared = _normalize(catalog.fallback())
            if declared and declared not in chain:
                chain.insert(index + 1, declared)
        index += 1
    if DEFAULT_LANGUAGE not in chain:
        chain.append(DEFAULT_LANGUAGE)
    if not catalogs and warn_on_empty:
        LOG.warning(
            "i18n: no usable language catalog in dirs=%s -- lookups degrade to derived source strings",
            list(dirs_t),
        )
    return {"locale": locale, "resolved": _pick_resolved(locale, chain, catalogs),
            "dirs": dirs_t, "catalogs": catalogs, "chain": tuple(chain)}


def _pick_resolved(locale: str, chain: Sequence[str], catalogs: Mapping[str, Catalog]) -> str | None:
    for candidate in chain:
        if candidate in catalogs:
            return candidate
    return None


def _ensure_state() -> dict[str, Any]:
    with _LOCK:
        if not _state["ready"]:
            _state.update(_activate(DEFAULT_LANGUAGE, tuple(default_dirs()), warn_on_empty=False))
            _state["ready"] = True
        return _state


_CONTEXT: ContextVar[CatalogContext | None] = ContextVar("cadcore_catalog_context", default=None)


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


class CatalogContext:
    """Fixed language/catalog snapshot for one request or explicit background job.

    Binding redirects implicit module APIs without changing the legacy global
    locale, listeners or counters. Create a new instance after a preference or
    catalog update; changing the locale within a fixed snapshot is an error.
    """

    def __init__(self, locale: str, *, dirs: Sequence[str] | None = None):
        if not isinstance(locale, str) or not _KEY_SHAPE_RE.fullmatch(locale) or len(locale) > 64:
            raise CatalogError("context locale must be a valid nonempty language tag")
        requested = _normalize(locale)
        self._dirs = _as_dirs(dirs)
        with _LOCK:
            loaded = _activate(requested, self._dirs, warn_on_empty=False, strict=True)
        if not loaded["catalogs"]:
            raise LocaleUnavailable("no usable catalog for context locale")
        loaded["catalogs"] = MappingProxyType({
            code: replace(cat, strings=_frozen(cat.strings), meta=_frozen(cat.meta))
            for code, cat in loaded["catalogs"].items()
        })
        self._state = MappingProxyType(loaded)
        self._lock = threading.RLock()
        self._stats = {key: 0 for key in _stats}
        self._missing_keys: dict[str, int] = {}
        self._logged_fallbacks: set[tuple[str, str, str]] = set()

    @property
    def locale(self) -> str:
        return self._state["locale"]

    @contextmanager
    def bind(self):
        token = _CONTEXT.set(self)
        try:
            yield self
        finally:
            _CONTEXT.reset(token)

    def active_locale(self) -> str:
        return self.locale

    def active_catalog(self) -> Catalog | None:
        return self._state["catalogs"].get(self._state["resolved"])

    def active_chain(self) -> list[str]:
        return list(self._state["chain"])

    def set_locale(self, code: str, *, dirs: Sequence[str] | None = None) -> LanguageChange:
        if (not isinstance(code, str) or _normalize(code) != self.locale
                or (dirs is not None and _as_dirs(dirs) != self._dirs)):
            raise CatalogError("fixed catalog context cannot change locale or directories")
        primary = self.active_catalog()
        return LanguageChange(
            previous=self.locale, current=self.locale, resolved=self._state["resolved"],
            chain=tuple(self.active_chain()), catalog_present=primary is not None,
            changed=False, keys=len(primary.strings) if primary is not None else -1,
            timestamp=_now_iso(),
        )

    def t(self, key: str, /, **args: Any) -> str:
        with self.bind():
            return t(key, **args)

    def stats(self) -> dict[str, Any]:
        with self.bind():
            return stats()

    def untranslated_keys(self) -> list[tuple[str, int]]:
        with self._lock:
            return sorted(self._missing_keys.items(), key=lambda item: (-item[1], item[0]))

    def default_dirs(self) -> list[str]:
        return list(self._dirs)

    def available_locales(self, *, dirs: Sequence[str] | None = None) -> list[str]:
        return available_locales(dirs=self._dirs if dirs is None else dirs)

    def load_catalog(self, code: str, *, dirs: Sequence[str] | None = None) -> Catalog:
        return load_catalog(code, dirs=self._dirs if dirs is None else dirs)

    @staticmethod
    def scan_catalogs(dirs: Sequence[str]) -> list[CatalogInfo]:
        return scan_catalogs(dirs)

    @staticmethod
    def resolve_chain(code: str) -> list[str]:
        return resolve_chain(code)


@contextmanager
def bind(locale: str, *, dirs: Sequence[str] | None = None):
    """Construct and bind a fixed catalog snapshot; yield the legacy adapter."""
    context = CatalogContext(locale, dirs=dirs)
    with context.bind():
        yield context


def _runtime():
    context = _CONTEXT.get()
    if context is not None:
        return (context._lock, context._state, context._stats,
                context._missing_keys, context._logged_fallbacks)
    return _LOCK, _ensure_state(), _stats, _missing_keys, _logged_fallbacks


def _dispatch(change: LanguageChange) -> None:
    """广播；**订阅者抛错不许影响切换本身，也不许影响别的订阅者**。"""
    if getattr(_TLS, "broadcasting", False):
        LOG.error("i18n: nested set_locale() inside a language-change callback was ignored")
        return
    with _LOCK:
        listeners = list(_listeners)
    _TLS.broadcasting = True
    try:
        for callback in listeners:
            try:
                callback(change)
            except Exception:  # noqa: BLE001 -- 订阅者是外部代码，坏一个不许拖垮整条链
                LOG.exception("i18n listener %r failed while handling language change", callback)
    finally:
        _TLS.broadcasting = False


def set_locale(code: str, *, dirs: Sequence[str] | None = None) -> LanguageChange:
    """运行时切换活动语言：换语言包 + 广播订阅者，**不重启进程**。

    - `dirs=None` ⇒ `default_dirs()`（用户目录优先）；显式传入即固定这次的目录集合；
    - 精确命中优先，否则沿回退链回落（`resolved` 如实记录落到了哪一门）；
    - 请求码解析不到、但环境里**确实装了别的语言** ⇒ 抛 `LocaleUnavailable`（不假装切换成功）；
    - 环境里一个语言包都没有 ⇒ 显式降级：`catalog_present=False`、`keys=-1`（未采），
      `t()` 继续可用（返回由 key 派生的人读兜底串），并记一条警告；
    - 语言码没变且落点没变 ⇒ 返回 `changed=False` 且**不广播**（避免无谓重绘）。
    """
    context = _CONTEXT.get()
    if context is not None:
        return context.set_locale(code, dirs=dirs)
    dirs_t = _as_dirs(dirs)
    requested = _normalize(code) or DEFAULT_LANGUAGE
    with _LOCK:
        _ensure_state()
        loaded = _activate(requested, dirs_t, warn_on_empty=True)
        catalogs = loaded["catalogs"]
        if not catalogs:
            installed = available_locales(dirs=dirs_t)
            if installed:
                raise LocaleUnavailable(
                    f"no catalog for {requested!r} (chain={resolve_chain(requested)}) "
                    f"in dirs={list(dirs_t)}; available={installed}"
                )
        previous = _state["locale"]
        previous_resolved = _state["resolved"]
        resolved = loaded["resolved"]
        changed = requested != previous or resolved != previous_resolved
        if changed:
            _stats["switches"] += 1
        _state.update(loaded)
        primary = catalogs.get(resolved) if resolved else None
        keys = len(primary.strings) if primary is not None else -1
        change = LanguageChange(
            previous=previous,
            current=requested,
            resolved=resolved,
            chain=tuple(loaded["chain"]),
            catalog_present=primary is not None,
            changed=changed,
            keys=keys,
            timestamp=_now_iso(),
        )
    if changed:
        _dispatch(change)
    return change


def on_language_changed(callback: Callable[[LanguageChange], None]) -> Callable[[], None]:
    """订阅语言切换，返回**退订闭包**（重复调用退订是 no-op）。"""
    if _CONTEXT.get() is not None:
        raise CatalogError("fixed catalog contexts do not subscribe to global language changes")
    if not callable(callback):
        raise TypeError("on_language_changed expects a callable")
    with _LOCK:
        _listeners.append(callback)

    def unsubscribe() -> None:
        with _LOCK:
            for index, item in enumerate(_listeners):
                if item is callback:
                    del _listeners[index]
                    break

    return unsubscribe


def active_locale() -> str:
    """当前活动 BCP-47 标签（请求码原样保留，`zh-CN` 与 `zh-TW` 各是各的）。"""
    context = _CONTEXT.get()
    if context is not None:
        return context.active_locale()
    with _LOCK:
        _ensure_state()
        return str(_state["locale"])


def active_catalog() -> Catalog | None:
    """当前主语言包；`None` ⇒ 未采（降级态，`t()` 走派生兜底串）。"""
    context = _CONTEXT.get()
    if context is not None:
        return context.active_catalog()
    with _LOCK:
        _ensure_state()
        resolved = _state["resolved"]
        return _state["catalogs"].get(resolved) if resolved else None


def active_chain() -> list[str]:
    """当前生效的回退链（含 `_meta.fallback` 注入项，以源串语言收尾）。"""
    context = _CONTEXT.get()
    if context is not None:
        return context.active_chain()
    with _LOCK:
        _ensure_state()
        return list(_state["chain"])


# --------------------------------------------------------------------------------------
# 取词
# --------------------------------------------------------------------------------------


def _derive_source_string(key: str) -> str:
    """由 key 派生**人读**兜底串（`doc.untitled` → `Untitled`）。

    刻意不返回 key 本身：点号分隔的 key 属于开发期标识，摆给用户看等于没做 i18n。
    """
    tail = key.rsplit(".", 1)[-1] if key else ""
    text = _WHITESPACE_RE.sub(" ", tail.replace("_", " ").replace("-", " ")).strip()
    if not text:
        text = key.strip() or "?"
    return text[:1].upper() + text[1:]


def t(key: str, /, **args: Any) -> str:
    """取译文：沿活动回退链找 → 回落源串语言（en）→ 仍无 ⇒ 由 key 派生的兜底串。

    硬保证（判定行 G1 依赖）：
    - **绝不返回空串**（语言包里的 `""`/空白串等同"没有"，继续往链下走）；
    - **绝不返回 key 名本身**（连 en 都没有时返回派生的人读串并记 WARNING）；
    - 占位符替换失败（译文写坏 `{}`）不抛异常：记 WARNING + `stats()["format_errors"]`，返回未替换的原文。
    """
    text_key = str(key)
    lock, state, counters, missing, logged = _runtime()
    with lock:
        catalogs: Mapping[str, Catalog] = state["catalogs"]
        chain: Sequence[str] = state["chain"]
        primary = chain[0] if chain else DEFAULT_LANGUAGE
        counters["lookups"] += 1
        raw: str | None = None
        hit: str | None = None
        for candidate in chain:
            catalog = catalogs.get(candidate)
            if catalog is None:
                continue
            value = catalog.strings.get(text_key)
            if isinstance(value, str):
                if value.strip():
                    raw, hit = value, candidate
                    break
                counters["empty_values"] += 1
                LOG.warning("i18n: empty translation for %r in catalog %s (treated as missing)",
                            text_key, candidate)
                continue
            if value is not None:
                LOG.warning("i18n: non-string translation for %r in catalog %s (ignored)",
                            text_key, candidate)
        if raw is None:
            counters["misses"] += 1
            missing[text_key] = missing.get(text_key, 0) + 1
            raw = _derive_source_string(text_key)
            LOG.warning(
                "i18n: no translation for %r in chain %s -- served derived source string %r",
                text_key, list(chain), raw,
            )
        elif hit != primary:
            counters["fallbacks"] += 1
            marker = (text_key, primary, str(hit))
            if marker not in logged:
                logged.add(marker)
                LOG.warning("i18n: key %r missing in %s -- served from fallback %s",
                            text_key, primary, hit)
    if not args:
        return raw
    try:
        return raw.format(**args)
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        with lock:
            counters["format_errors"] += 1
        LOG.warning("i18n: placeholder substitution failed for %r (%s: %s) -- raw text returned",
                    text_key, type(exc).__name__, exc)
        return raw


# --------------------------------------------------------------------------------------
# 观测
# --------------------------------------------------------------------------------------


def stats() -> dict[str, Any]:
    """运行期计数（只读快照）。全部计数**始终在采**，故 `0` 是真为零；
    唯一可能为 `-1` 的是 `active_catalog_keys`（未采：没有语言包可数）。"""
    lock, state, counters, missing, _ = _runtime()
    with lock:
        catalog = state["catalogs"].get(state["resolved"]) if state["resolved"] else None
        snapshot: dict[str, Any] = dict(counters)
        snapshot.update(
            {
                "active_locale": state["locale"],
                "resolved": state["resolved"],
                "catalog_present": catalog is not None,
                "active_catalog_keys": len(catalog.strings) if catalog is not None else -1,
                "listeners": len(_listeners) if _CONTEXT.get() is None else 0,
                "catalogs_loaded": len(state["catalogs"]),
                "cached_json_files": len(_JSON_CACHE),
                "distinct_missing_keys": len(missing),
            }
        )
        return snapshot


def untranslated_keys() -> list[tuple[str, int]]:
    """链上任何一级都没查到的 key 及次数（按次数降序、key 升序）；空列表 = 真为零。"""
    context = _CONTEXT.get()
    if context is not None:
        return context.untranslated_keys()
    with _LOCK:
        return sorted(_missing_keys.items(), key=lambda item: (-item[1], item[0]))


# --------------------------------------------------------------------------------------
# 差集（判定行 G1：任一非空即 FAIL）
# --------------------------------------------------------------------------------------


def key_diff(base: Catalog, other: Catalog) -> tuple[set[str], set[str]]:
    """键集合**双向差集**：`(missing, extra)`；两者皆空才算键集合一致。"""
    base_keys = base.keys()
    other_keys = other.keys()
    return (set(base_keys - other_keys), set(other_keys - base_keys))


def placeholder_diff(base: Catalog, other: Catalog) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """占位符差集（附加只读辅助，供判定行报 `placeholder_mismatch`）。

    逐键比较占位符**名字与个数**（`{name}` / `{name!r:>8}` / `{0}` 都算），
    返回 `{key: (base_fields, other_fields)}`；空 dict = 逐条一致。
    """
    out: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for key in sorted(base.keys() & other.keys()):
        left = _fields_of(base.strings.get(key, ""))
        right = _fields_of(other.strings.get(key, ""))
        if left != right:
            out[key] = (left, right)
    return out


def _fields_of(template: str) -> tuple[str, ...]:
    names: list[str] = []
    try:
        for _, field_name, _, _ in _FORMATTER.parse(template):
            if field_name is None:
                continue
            names.append(field_name.split(".")[0].split("[")[0] or "<positional>")
    except ValueError:
        return ("<malformed>",)
    return tuple(sorted(names))


# --------------------------------------------------------------------------------------
# gettext .po 备选格式（纯标准库解析；交付给译者 / Weblate 用）
# --------------------------------------------------------------------------------------

_PO_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"',
               "a": "\a", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_PO_KEYWORD_RE = re.compile(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(.*)$")


def _po_unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            out.append(_PO_ESCAPES.get(nxt, nxt))
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _po_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


def _parse_po(text: str) -> list[dict[str, Any]]:
    """极简但完整的 PO 扫描器：注释/前一条 msgid/多行串/复数/msgctxt/废弃条目都认。"""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_field: str | None = None

    def flush() -> None:
        nonlocal current, last_field
        if current is not None and (current["id_seen"] or current["strs"] or current["obsolete"]):
            entries.append(current)
        current = None
        last_field = None

    def ensure() -> dict[str, Any]:
        nonlocal current
        if current is None:
            current = {"ctxt": None, "msgid": "", "plural": None, "strs": {},
                       "flags": set(), "obsolete": False, "line": 0, "id_seen": False}
        return current

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith("#"):
            entry = ensure()
            if entry["line"] == 0:
                entry["line"] = lineno
            if line.startswith("#~"):
                entry["obsolete"] = True
            elif line.startswith("#,"):
                entry["flags"] |= {f.strip() for f in line[2:].split(",") if f.strip()}
            last_field = None
            continue
        match = _PO_KEYWORD_RE.match(line)
        if match:
            keyword, rest = match.group(1), match.group(2).strip()
            if keyword == "msgid" and ensure()["id_seen"]:
                flush()
            entry = ensure()
            if entry["line"] == 0:
                entry["line"] = lineno
            value = _po_unescape(rest[1:-1]) if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"' else ""
            if len(rest) < 2 or rest[0] != '"' or rest[-1] != '"':
                LOG.warning("i18n: .po line %d is not a quoted string: %s", lineno, line)
            if keyword == "msgctxt":
                entry["ctxt"] = value
            elif keyword == "msgid":
                entry["msgid"] = value
                entry["id_seen"] = True
            elif keyword == "msgid_plural":
                entry["plural"] = value
            else:
                index = 0
                if "[" in keyword:
                    index = int(keyword[keyword.index("[") + 1:keyword.index("]")])
                entry["strs"][index] = value
            last_field = keyword
            continue
        if line.startswith('"') and line.endswith('"') and len(line) >= 2:
            if last_field is not None and current is not None:
                value = _po_unescape(line[1:-1])
                if last_field == "msgctxt":
                    current["ctxt"] = (current["ctxt"] or "") + value
                elif last_field == "msgid":
                    current["msgid"] += value
                elif last_field == "msgid_plural":
                    current["plural"] = (current["plural"] or "") + value
                else:
                    index = 0
                    if "[" in last_field:
                        index = int(last_field[last_field.index("[") + 1:last_field.index("]")])
                    current["strs"][index] = current["strs"].get(index, "") + value
                continue
            LOG.warning("i18n: .po line %d is a continuation without a keyword", lineno)
            continue
        LOG.warning("i18n: .po line %d not understood: %s", lineno, line)
    flush()
    return entries


def _po_headers(entry: Mapping[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in str(entry["strs"].get(0, "")).splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return headers


def import_po(path: str, *, include_fuzzy: bool = False) -> Catalog:
    """gettext `.po` → 语言包（备选格式入口，纯标准库）。

    - 语言码取头部 `Language:`，没有则取文件名主干（形如 BCP-47 才认，否则抛 `CatalogError`）；
    - `msgctxt` 按 gettext 惯例拼成 `ctx\\x04msgid` 作为 key；复数取 `msgstr[0]`；
    - 默认**跳过** `#, fuzzy`（未定稿）、空 `msgstr`（未译）、`#~` 废弃条目，
      三类各计数进 `_meta`（`po_skipped_*`）—— 跳过了多少必须看得见，不许静默；
    - 缺失的 `_meta` 字段写 `None`（NA），**不编造**。
    """
    po_path = Path(path)
    try:
        text = po_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogError(f"cannot read .po file {path}: {exc}") from exc
    entries = _parse_po(text)
    header = next((e for e in entries if e["id_seen"] and e["msgid"] == "" and not e["obsolete"]), None)
    headers = _po_headers(header) if header is not None else {}
    code = _normalize(headers.get("language") or po_path.stem)
    if not code or not _KEY_SHAPE_RE.match(code):
        raise CatalogError(
            f"cannot determine language code for {path}: no 'Language:' header and file name "
            f"{po_path.stem!r} is not a BCP-47 style tag"
        )
    strings: dict[str, str] = {}
    skipped_fuzzy = skipped_empty = skipped_obsolete = 0
    for entry in entries:
        if entry["obsolete"]:
            skipped_obsolete += 1
            continue
        if not entry["id_seen"] or entry["msgid"] == "":
            continue  # 头部条目
        if "fuzzy" in entry["flags"] and not include_fuzzy:
            skipped_fuzzy += 1
            continue
        indexes = sorted(entry["strs"])
        value = ""
        for index in indexes:
            if str(entry["strs"][index]).strip():
                value = entry["strs"][index]
                break
        if not value.strip():
            skipped_empty += 1
            continue
        key = f"{entry['ctxt']}\x04{entry['msgid']}" if entry["ctxt"] else entry["msgid"]
        strings[key] = value
    meta: dict[str, Any] = {name: None for name in META_FIELDS}
    meta.update(
        {
            "code": code,
            "license": "Apache-2.0",
            "source": "self-translated",
            "imported_from": str(po_path),
            "po_entries": len(entries),
            "po_skipped_fuzzy": skipped_fuzzy,
            "po_skipped_untranslated": skipped_empty,
            "po_skipped_obsolete": skipped_obsolete,
            "po_include_fuzzy": bool(include_fuzzy),
        }
    )
    return Catalog(code=code, strings=strings, meta=meta, path=str(po_path), requested=code)


def export_po(cat: Catalog, path: str) -> None:
    """语言包 → `.po`（UTF-8、`\\n` 行尾、键排序 ⇒ 同样输入逐字节可复现，可往返）。"""
    out_path = Path(path)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Language pack exported from a cadcore i18n catalog.",
        "# Do not edit by hand the header block; translate the msgstr values.",
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: cadcore\\n"',
        f'"Language: {cat.code}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        '"X-Generator: cadcore.i18n.catalog export_po\\n"',
    ]
    for key in sorted(cat.strings):
        lines.append("")
        if "\x04" in key:
            context, msgid = key.split("\x04", 1)
            lines.append(f'msgctxt "{_po_escape(context)}"')
        else:
            msgid = key
        lines.append(f'msgid "{_po_escape(msgid)}"')
        lines.append(f'msgstr "{_po_escape(cat.strings[key])}"')
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
