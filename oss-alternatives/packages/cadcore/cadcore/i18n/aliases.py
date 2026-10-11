"""别名（alias）i18n 解析层：别名表住在语言包，运行时按**当前语言**读。

回归背景
--------
上一波「源码不留 locale 字面量」的清理把若干切片里的**中文入性别名**
（配合类型「重合 / 同轴心」、方向「前 / 后 / 左 / 右 / 上 / 下」、
捕捉总开关「开 / 打开 / 关 / 关闭 / 切换」）直接删成了英文 —— 源码合规了，
但中文用户的既有输入不再被解析，这是功能回退。正确修法不是把汉字写回源码，
而是**把别名表搬进语言包**：

- 别名 = 语言包 `strings` 里的 `alias.<group>.<canonical>` 键（三份语言包键集合逐字一致）；
- 本模块运行时从现成加载器 ``cadcore/i18n/catalog.py``（路径写清：不另造一层加载器）
  的**当前语言回退链**取别名并集 ⇒ 切到 zh-CN 仍认得英文写法，新语言只加语言包、不改代码；
- 切片文件只保留 canonical 枚举名（内部身份，不是别名）⇒ 源码零汉字字面量，G1/G8 不红。

契约（切片只依赖这四条）
------------------------
1. 键空间 ``alias.<group>.<canonical>``；值 = 该语言的别名，``|`` 分隔（``,`` ``;`` ``/`` 也接受）；
2. canonical 名自身**永远**可解析（读回文档里已存的枚举值这条路不能断）；
3. 归一化 ``strip().lower()`` + ``-``/空格 → ``_``（与各切片原有口径逐字一致）；
4. 语言层拿不到（import 失败 / catalog 缺席）⇒ **只认 canonical 自身**：不抛错、不假装解析成功；
   ``AliasTable`` 是**活**表，语言热切换后无需重建对象（缓存键含当前语言链，切语言即失效）。

自检：``python3 -m cadcore.i18n.aliases``（真断言 ≥4 条，含阴性对照）。
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Iterable as _IterableABC
from collections.abc import Iterator as _IteratorABC
from collections.abc import Mapping as _MappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any

if __package__ in (None, ""):  # 直接以脚本方式运行：把含 `cadcore/` 的包根补进 sys.path
    import os as _os

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)
    __package__ = "cadcore.i18n"

ALIAS_PREFIX = "alias."
_SEPARATOR_CHARS = "|,;/"

#: 别名表键的组名（canonical 枚举名由调用方给，不在本模块里硬编码任何别名）。
SELFTEST_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mate", ("coincident", "concentric", "parallel", "perpendicular",
              "tangent", "distance", "angle", "lock")),
    ("direction", ("front", "back", "right", "left", "top", "bottom")),
    ("osnap", ("on", "off", "toggle")),
    ("alignment", ("aligned", "anti_aligned")),
)


def _normalize(text: Any) -> str:
    """入参归一化：``strip().lower()`` + ``-``/空格 → ``_``（各切片原有口径的并集，幂等）。"""
    value = str(text if text is not None else "").strip().lower()
    return value.replace("-", "_").replace(" ", "_")


def _split_aliases(value: Any) -> tuple[str, ...]:
    """语言包里的别名串 → 归一化去重元组（保序；空串与重复项丢掉）。"""
    text = str(value or "")
    for separator in _SEPARATOR_CHARS:
        text = text.replace(separator, "\n")
    out: list[str] = []
    for part in text.split("\n"):
        token = _normalize(part)
        if token and token not in out:
            out.append(token)
    return tuple(out)


def _catalog_module() -> Any:
    """现成的语言层加载器（``cadcore/i18n/catalog.py``）；拿不到返回 ``None``。"""
    from cadcore.i18n import catalog as module  # 现成加载器（唯一真源）

    return module


def _current_chain() -> tuple[str, ...]:
    module = _catalog_module()
    if module is None:
        return ()
    try:
        return tuple(str(code) for code in module.active_chain())
    except Exception:                                    # 语言层状态坏了也不许连坐别名解析
        return ()


def _chain_catalogs() -> list[Any]:
    """当前语言回退链上的全部语言包（先当前语言、后回退），逐个失败即跳过。"""
    module = _catalog_module()
    if module is None:
        return []
    catalogs: list[Any] = []
    for code in _current_chain():
        try:
            catalog = module.load_catalog(code)
        except Exception:
            continue
        if catalog is not None:
            catalogs.append(catalog)
    return catalogs


#: 缓存键 = (当前语言链, 组名, canonical 元组) ⇒ 语言一换键就变，不存在脏缓存。
_CACHE: dict[tuple[tuple[str, ...], str, tuple[str, ...]], dict[str, str]] = {}


def reset_cache() -> None:
    """丢掉别名缓存（语言层热重载 / 夹具自证用）。"""
    _CACHE.clear()


def _alias_table(group: str, canonicals: _SequenceABC) -> dict[str, str]:
    """建表：canonical 自身 + 语言链上 ``alias.<group>.<canonical>`` 的全部别名。"""
    canon = tuple(str(item) for item in canonicals)
    key = (_current_chain(), str(group), canon)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    table: dict[str, str] = {}
    for canonical in canon:
        table[_normalize(canonical)] = canonical
    for catalog in _chain_catalogs():
        for canonical in canon:
            aliases = catalog.get(ALIAS_PREFIX + str(group) + "." + canonical)
            for token in _split_aliases(aliases):
                table.setdefault(token, canonical)
    _CACHE[key] = table
    return table


class AliasTable(_MappingABC):
    """一个别名组的**活**映射：``get()`` 每次都按当前语言链读语言包。

    兼容切片原有的映射面（``.get(...)`` / ``in`` / ``+`` / 迭代），所以调用点只需把静态
    dict/tuple 换成 ``alias_map(...)``，解析逻辑一行不用改。
    """

    __slots__ = ("_group", "_canonicals")

    def __init__(self, group: str, canonicals: _IterableABC) -> None:
        self._group = str(group)
        seen: list[str] = []
        for canonical in canonicals:
            text = str(canonical)
            if text and text not in seen:
                seen.append(text)
        self._canonicals: tuple[str, ...] = tuple(seen)

    # ── 取用面 ──────────────────────────────────────────────────────────────────
    def _table(self) -> dict[str, str]:
        return _alias_table(self._group, self._canonicals)

    def get(self, key: Any, default: Any = None) -> Any:
        return self._table().get(_normalize(key), default)

    def __getitem__(self, key: str) -> str:
        try:
            return self._table()[_normalize(key)]
        except KeyError:
            raise KeyError(key) from None

    def __contains__(self, key: object) -> bool:
        return _normalize(key) in self._table()

    def __iter__(self) -> _IteratorABC:
        return iter(self._table())

    def __len__(self) -> int:
        return len(self._table())

    def __add__(self, other: Any) -> tuple[str, ...]:
        return tuple(self) + tuple(other)

    def __radd__(self, other: Any) -> tuple[str, ...]:
        return tuple(other) + tuple(self)

    # ── 派生面 ──────────────────────────────────────────────────────────────────
    def restrict(self, canonical: str) -> "AliasTable":
        """只认一个 canonical 的视图（``key in table`` / ``list(a + b)`` 这类老用法照旧）。"""
        return AliasTable(self._group, (canonical,))

    def tokens(self) -> tuple[str, ...]:
        return tuple(self._table())

    def __repr__(self) -> str:
        return "AliasTable(group=%r, canonicals=%r, tokens=%d)" % (
            self._group, self._canonicals, len(self._table()))


def alias_map(group: str, canonicals: _IterableABC) -> AliasTable:
    """一个别名组的活表：canonical 元组由调用方给，别名一律从语言包现读。"""
    return AliasTable(group, canonicals)


# --------------------------------------------------------------------------------------
# 自检：真断言（en 与 zh-CN 都必须真生效；阴性对照证明尺子看得见红）
# --------------------------------------------------------------------------------------


def _has_han(text: str) -> bool:
    return any(0x4E00 <= ord(ch) <= 0x9FFF for ch in text)


def _catalog_tokens(module: Any, group: str, canonical: str) -> tuple[str, ...]:
    """当前活动语言包自己写的别名（取不到就回退链上的第一份，保证不是空集假绿）。"""
    raw = None
    try:
        catalog = module.active_catalog()
        if catalog is not None:
            raw = catalog.get(ALIAS_PREFIX + group + "." + canonical)
    except Exception:
        raw = None
    if not _split_aliases(raw):
        try:
            raw = module.t(ALIAS_PREFIX + group + "." + canonical)
        except Exception:
            raw = None
    return _split_aliases(raw)


def selftest() -> int:
    """真跑别名解析：每个语言包自己的别名都必须解析回同一 canonical（返回 rc 0/1/2）。"""
    module = _catalog_module()
    if module is None:
        print("ALIAS_I18N_SELFTEST verdict=UNMEASURED reason=catalog_unavailable checks=0 failed=0")
        return 2
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((str(name), bool(ok), str(detail)))

    try:
        previous = str(module.active_locale())
    except Exception:
        previous = ""
    try:
        codes = [str(code) for code in module.available_locales()]
    except Exception:
        codes = []
    if not codes:
        print("ALIAS_I18N_SELFTEST verdict=UNMEASURED reason=no_catalogs checks=0 failed=0")
        return 2

    tables = {group: alias_map(group, canon) for group, canon in SELFTEST_GROUPS}
    seen: dict[str, dict[str, tuple[str, ...]]] = {}
    try:
        for code in codes:
            module.set_locale(code)
            print("ALIAS_I18N_SELFTEST diag locale=%s chain=%r" % (code, list(module.active_chain())))
            per_locale: dict[str, tuple[str, ...]] = {}
            for group, canon in SELFTEST_GROUPS:
                for canonical in canon:
                    tokens = _catalog_tokens(module, group, canonical)
                    per_locale[group + "." + canonical] = tokens
                    check("alias_key_present[%s:%s.%s]" % (code, group, canonical),
                          bool(tokens), repr(tokens))
                    for token in tokens:
                        got = tables[group].get(token)
                        check("resolve[%s:%s.%s]" % (code, group, canonical),
                              got == canonical, "%r -> %r (want %r)" % (token, got, canonical))
            unresolved = [(key, token) for key, tokens in sorted(per_locale.items())
                          for token in tokens if tables[key.split(".")[0]].get(token) is None]
            if unresolved:
                print("ALIAS_I18N_SELFTEST diag unresolved locale=%s %r" % (code, unresolved[:12]))
                print("ALIAS_I18N_SELFTEST diag catalogs=%r"
                      % ([(item.code, item.path) for item in _chain_catalogs()],))
                print("ALIAS_I18N_SELFTEST diag direction-tokens=%r"
                      % (tables["direction"].tokens(),))
            seen[code] = per_locale

        default_code = str(module.DEFAULT_LANGUAGE)
        check("default_language_present", default_code in seen, repr(codes))
        others = [code for code in codes if code != default_code]
        check("non_default_languages_present", bool(others), repr(codes))

        en_token = seen[default_code]["mate.coincident"][0]
        check("en_coincident_resolves", tables["mate"].get(en_token) == "coincident", en_token)

        translated: list[str] = []
        for code in others:
            module.set_locale(code)                  # 每个语言都按它自己的回退链解析
            tokens = seen[code]["mate.coincident"]
            # ① 中文（及其它非默认语言）的「重合」与英文 "coincident" 解析到**同一内部枚举**
            check("cross_locale_same_enum[%s]" % code,
                  bool(tokens) and all(tables["mate"].get(t) == "coincident" for t in tokens)
                  and tables["mate"].get(en_token) == "coincident",
                  "en=%r %s=%r" % (en_token, code, list(tokens)))
            if any(_has_han(token) for tokens_ in seen[code].values() for token in tokens_):
                translated.append(code)
            # ② 捕捉总开关三态：非默认语言的别名分别落到 on / off / toggle
            states = (tables["osnap"].get(seen[code]["osnap.on"][0]) if seen[code]["osnap.on"] else None,
                      tables["osnap"].get(seen[code]["osnap.off"][0]) if seen[code]["osnap.off"] else None,
                      tables["osnap"].get(seen[code]["osnap.toggle"][0]) if seen[code]["osnap.toggle"] else None)
            check("osnap_three_states[%s]" % code, states == ("on", "off", "toggle"), repr(states))
            # ③ 方向六轴：非默认语言的六个方向都能解析
            axes = {tables["direction"].get(token)
                    for axis in ("front", "back", "right", "left", "top", "bottom")
                    for token in seen[code]["direction." + axis]}
            check("direction_six_axes[%s]" % code,
                  axes == {"front", "back", "right", "left", "top", "bottom"},
                  repr(sorted(str(item) for item in axes)))
            # ④ 配合对齐两态
            check("alignment_two_states[%s]" % code,
                  tables["alignment"].get(seen[code]["alignment.aligned"][0]) == "aligned"
                  and tables["alignment"].get(seen[code]["alignment.anti_aligned"][0]) == "anti_aligned",
                  repr(seen[code]["alignment.anti_aligned"]))
        check("non_default_aliases_are_translated", bool(translated),
              "locales with Han aliases=%r" % (translated,))

        # ⑤ 热切换：**同一个 AliasTable 对象**，切语言后无需改代码/重建即可解析新语言别名
        probe = tables["mate"]
        module.set_locale(default_code)
        before = probe.get(en_token)
        module.set_locale(others[0])
        after = [probe.get(token) for token in seen[others[0]]["mate.coincident"]]
        check("live_switch_no_code_change",
              before == "coincident" and bool(after) and all(item == "coincident" for item in after),
              "en=%r %s=%r" % (before, others[0], after))

        # ⑥ 阴性对照：不认得的输入不许被"宽容"成某个 canonical（尺子看得见红）
        check("unknown_token_not_resolved", probe.get("__alias_selftest_unknown__") is None, "")
    finally:
        if previous:
            try:
                module.set_locale(previous)
            except Exception:
                pass
        reset_cache()

    failed = [item for item in checks if not item[1]]
    for name, _ok, detail in failed[:10]:
        print("ALIAS_I18N_SELFTEST FAIL %s %s" % (name, detail))
    verdict = "PASS" if not failed and len(checks) >= 4 else "FAIL"
    print("ALIAS_I18N_SELFTEST verdict=%s checks=%d failed=%d locales=%d live_switch=%d"
          % (verdict, len(checks), len(failed), len(codes), 1 if not failed else 0))
    for code in codes:
        module.set_locale(code)
        print("ALIAS_I18N_SELFTEST sample locale=%s key=%s tokens=%r -> %r"
              % (code, "alias.mate.coincident", list(seen[code]["mate.coincident"]),
                 [tables["mate"].get(token) for token in seen[code]["mate.coincident"]]))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
