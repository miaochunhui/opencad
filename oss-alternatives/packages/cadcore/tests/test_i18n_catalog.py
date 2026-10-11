"""tests.test_i18n_catalog —— A16 语言层单测（30 例；覆盖扫盘/回退链/热切换/双向差集/gettext 往返）。

设计一句话
----------
**语言层最容易出的两类事故是"静默"与"假绿"**：译文缺了却把 key 名摆给用户、语言包坏了却假装加载成功、
扫不到的目录被当成"0 门语言"。所以本文件的尺子成对出现（阴阳双证）：
既测"该绿时绿"，也测"该红时红"（回退确实发生、坏文件确实报错、计数确实区分 `0` 与 `-1`）。

夹具自证
--------
每个夹具都断言**真件在场**（文件确实落盘、`Catalog.path` 指向它、内容与磁盘一致），
并且全部落在 `tempfile` 临时目录里 —— 不碰仓库内任何别人的文件、不产生副作用。

说明
----
本文件里的 BCP-47 标签（`zh-Hans-CN` 等）是**夹具数据**：单测要打的就是"标签→回退链"这条纯规则，
它不构成任何产品逻辑分支（产品侧的语言清单来自扫盘，见 `test_module_source_has_no_hardcoded_locale_literals`）。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_FILE = _PKG_ROOT / "cadcore" / "i18n" / "catalog.py"


def _load_module():
    """优先按包导入（真实布局）；兄弟席位的 `__init__.py` 还没落盘时按文件路径装载（缺席降级）。"""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))
    try:
        from cadcore.i18n import catalog  # type: ignore[import-not-found]

        return catalog
    except Exception:
        spec = importlib.util.spec_from_file_location("cadcore_i18n_catalog_standalone", _CATALOG_FILE)
        if spec is None or spec.loader is None:  # pragma: no cover - 只有在文件被删时才会走到
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


i18n = _load_module()

# ── 夹具语言码与词表（数据，不是逻辑分支）──────────────────────────────────────────────
EN = "en"
ZH = "zh"
ZH_HANS = "zh-Hans"
ZH_HANS_CN = "zh-Hans-CN"
ZH_TW = "zh-TW"
PT_BR = "pt-BR"
SR_LATN_RS = "sr-Latn-RS"
DE = "de"
DE_AT = "de-AT"
FR = "fr"

EN_STRINGS = {
    "doc.untitled": "Untitled",
    "doc.title": "Title",
    "menu.file": "File",
    "dim.linear": "Linear dimension",
    "err.open_failed": "Could not open {name}",
    "err.two": "{a} and {b}",
    "doc.empty": "Empty document",
    "only.in.english": "Source only",
}
ZH_STRINGS = {
    "doc.untitled": "Wei mingming",
    "doc.title": "Biaoti",
    "menu.file": "Wenjian",
    "dim.linear": "Xianxing chicun",
    "err.open_failed": "Dakai shibai: {name}",
    "err.two": "{a}",
    "doc.empty": "",
    "doc.bad_placeholder": "Chengben {amount",
    "zh.only": "Zhongwen zhuanyou",
    # 刻意不缺 "only.in.english"：那是回退链的阴性对照用的
}
ZH_TW_STRINGS = {"doc.untitled": "Wei mingming (TW)", "menu.file": "Wenjian (TW)"}
DE_STRINGS = {
    "doc.untitled": "Unbenannt",
    "menu.file": "Datei",
    "err.open_failed": "Konnte {name} nicht oeffnen",
}


def _catalog_payload(code, strings, *, native_name=None, fallback=None, extra=None):
    meta = {
        "code": code,
        "native_name": native_name if native_name is not None else code,
        "fallback": fallback,
        "font_fallback": ["Noto Sans CJK"],
        "default_standard": None,
        "license": "Apache-2.0",
        "source": "self-translated",
        "version": 1,
    }
    if extra:
        meta.update(extra)
    return {"_meta": meta, "strings": dict(strings)}


def _write_catalog(directory, code, strings, *, filename=None, **kwargs) -> Path:
    path = Path(directory) / (filename or f"{code}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_catalog_payload(code, strings, **kwargs), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert path.is_file(), f"fixture self-check failed: {path} was not written"  # 夹具自证
    return path


def _write_manifest(directory, entries) -> Path:
    path = Path(directory) / i18n.MANIFEST_FILENAME
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    assert path.is_file()
    return path


def _string_literals(path: Path) -> list[str]:
    """源码里**所有非 docstring 的字符串字面量**（docstring 是开发期文档，不算面向用户的文案）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


class _CatalogFixture:
    """公共夹具（**不是 TestCase**：避免自证用例被每个子类重复收集）：干净模块状态 + 临时目录树。"""

    def setUp(self):
        global i18n
        i18n = importlib.reload(i18n)  # 活动语言/订阅者/计数一律归零，用例之间不串味
        self._tmp = tempfile.TemporaryDirectory(prefix="cadcore-i18n-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.builtin = self.root / "builtin"
        self.user = self.root / "user"
        self.other = self.root / "other"
        self.empty = self.root / "empty"
        for directory in (self.builtin, self.user, self.other, self.empty):
            directory.mkdir(parents=True, exist_ok=True)
        self.en_path = _write_catalog(self.builtin, EN, EN_STRINGS, native_name="English")
        self.zh_path = _write_catalog(self.builtin, ZH, ZH_STRINGS, native_name="Zhongwen")
        self.tw_path = _write_catalog(self.builtin, ZH_TW, ZH_TW_STRINGS, native_name="Zhongwen TW")
        self.de_path = _write_catalog(self.builtin, DE, DE_STRINGS, native_name="Deutsch")

    def assert_fixture_is_real(self):
        """夹具自证：装载到的就是磁盘上那个真件，内容逐条一致（不是内存里另造的一份）。"""
        catalog = i18n.load_catalog(ZH, dirs=[self.builtin])
        self.assertEqual(catalog.path, str(self.zh_path))
        self.assertTrue(Path(catalog.path).is_file())
        on_disk = json.loads(self.zh_path.read_text(encoding="utf-8"))
        self.assertEqual(dict(catalog.strings), on_disk["strings"])
        self.assertEqual(catalog.code, on_disk["_meta"]["code"])


class FixtureSelfCheckTests(_CatalogFixture, unittest.TestCase):
    def test_fixture_catalog_is_the_real_file_on_disk(self):
        self.assert_fixture_is_real()


class ResolveChainTests(_CatalogFixture, unittest.TestCase):
    def test_resolve_chain_truncates_bcp47(self):
        self.assertEqual(i18n.resolve_chain(ZH_HANS_CN), [ZH_HANS_CN, ZH_HANS, ZH, EN])
        self.assertEqual(i18n.resolve_chain(PT_BR), [PT_BR, "pt", EN])
        self.assertEqual(i18n.resolve_chain(SR_LATN_RS), [SR_LATN_RS, "sr-Latn", "sr", EN])

    def test_resolve_chain_normalizes_separators_and_case(self):
        self.assertEqual(i18n.resolve_chain("zh_hans_cn"), [ZH_HANS_CN, ZH_HANS, ZH, EN])
        self.assertEqual(i18n.resolve_chain("PT-br"), [PT_BR, "pt", EN])

    def test_resolve_chain_defaults_to_source_language(self):
        self.assertEqual(i18n.resolve_chain(""), [EN])
        self.assertEqual(i18n.resolve_chain(EN), [EN])
        self.assertEqual(i18n.resolve_chain("en-US"), ["en-US", EN])
        for code in ("", EN, "en-US", ZH_HANS_CN):
            self.assertEqual(i18n.resolve_chain(code)[-1], i18n.DEFAULT_LANGUAGE)

    def test_declared_fallback_meta_is_honored_before_generic_truncation(self):
        """数据里声明的 `_meta.fallback` 优先于通用截断（回退链是数据可调的）。"""
        path = _write_catalog(self.other, "nb", {"doc.title": "Tittel"}, fallback="da")
        _write_catalog(self.other, "da", {"doc.title": "Titel (da)"})
        catalog = i18n.load_catalog("nb", dirs=[self.other])
        self.assertEqual(catalog.chain()[:3], ["nb", "da", EN])
        self.assertEqual(i18n.Catalog.from_dict(
            json.loads(path.read_text(encoding="utf-8")), path=str(path)).fallback(), "da")


class ScanCatalogsTests(_CatalogFixture, unittest.TestCase):
    def test_scan_catalogs_user_dir_wins_over_builtin(self):
        """用户目录优先 —— 真源是**目录顺序**，不是路径魔法（反序则内置胜，阴阳双证）。"""
        _write_catalog(self.user, DE, {"doc.untitled": "Unbenannt (user)"})
        infos = i18n.scan_catalogs([self.user, self.builtin])
        entry = next(i for i in infos if i.code == DE)
        self.assertEqual(entry.dir_index, 0)
        self.assertEqual(entry.path, str(self.user / f"{DE}.json"))
        self.assertEqual(i18n.load_catalog(DE, dirs=[self.user, self.builtin]).strings["doc.untitled"],
                         "Unbenannt (user)")
        reversed_entry = next(i for i in i18n.scan_catalogs([self.builtin, self.user]) if i.code == DE)
        self.assertEqual(reversed_entry.dir_index, 0)
        self.assertEqual(reversed_entry.path, str(self.builtin / f"{DE}.json"))

    def test_scan_catalogs_reports_invalid_files_instead_of_dropping(self):
        broken = self.user / "ja.json"
        broken.write_text("{ this is not json", encoding="utf-8")
        missing_strings = self.user / "ko.json"
        missing_strings.write_text(json.dumps({"_meta": {"code": "ko"}}), encoding="utf-8")
        infos = i18n.scan_catalogs([self.user, self.builtin])
        by_code = {info.code: info for info in infos}
        self.assertIn("ja", by_code)
        self.assertFalse(by_code["ja"].valid)
        self.assertTrue(by_code["ja"].error)
        self.assertFalse(by_code["ko"].valid)
        self.assertIn("strings", str(by_code["ko"].error))
        self.assertTrue(by_code[EN].valid)
        self.assertEqual(i18n.available_locales(dirs=[self.user, self.builtin]),
                         sorted(c for c, i in by_code.items() if i.valid))

    def test_scan_catalogs_ignores_manifest_and_template_files(self):
        _write_manifest(self.builtin, [{"code": EN, "native_name": "English", "file": f"{EN}.json"}])
        (self.builtin / "_template.json").write_text("{}", encoding="utf-8")
        codes = [info.code for info in i18n.scan_catalogs([self.builtin])]
        self.assertNotIn("languages", codes)
        self.assertNotIn("_template", codes)
        self.assertEqual(codes, sorted(codes))

    def test_scan_catalogs_absent_dir_is_not_an_error(self):
        missing = self.root / "does-not-exist"
        self.assertEqual(i18n.scan_catalogs([missing]), [])
        self.assertEqual(i18n.available_locales(dirs=[missing]), [])

    def test_scan_catalogs_reads_manifest_metadata(self):
        payload = _catalog_payload(FR, {"doc.title": "Titre"})
        for field_name in ("native_name", "default_standard", "font_fallback"):
            payload["_meta"].pop(field_name)
        (self.user / "fr.json").write_text(json.dumps(payload), encoding="utf-8")
        _write_manifest(self.user, [{
            "code": FR, "native_name": "Francais", "file": "fr.json",
            "font_fallback": ["Noto Sans"], "default_standard": "iso",
        }])
        entry = next(i for i in i18n.scan_catalogs([self.user, self.builtin]) if i.code == FR)
        self.assertEqual(entry.native_name, "Francais")
        self.assertEqual(entry.font_fallback, ("Noto Sans",))
        self.assertEqual(entry.default_standard, "iso")

    def test_scan_catalogs_warns_about_manifest_entries_without_file(self):
        _write_manifest(self.builtin, [
            {"code": EN, "native_name": "English", "file": f"{EN}.json"},
            {"code": FR, "native_name": "Francais", "file": "fr.json"},
        ])
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            i18n.scan_catalogs([self.builtin])
        self.assertIn(FR, "\n".join(captured.output))


class LoadCatalogTests(_CatalogFixture, unittest.TestCase):
    def test_load_catalog_walks_fallback_chain_and_marks_requested(self):
        catalog = i18n.load_catalog(ZH_HANS_CN, dirs=[self.builtin])
        self.assertEqual(catalog.code, ZH)
        self.assertEqual(catalog.requested, ZH_HANS_CN)
        self.assertEqual(catalog.strings["doc.untitled"], ZH_STRINGS["doc.untitled"])

    def test_load_catalog_exact_hit_keeps_requested_equal_to_code(self):
        catalog = i18n.load_catalog(DE, dirs=[self.builtin])
        self.assertEqual((catalog.code, catalog.requested), (DE, DE))

    def test_load_catalog_raises_when_language_is_unavailable(self):
        """链尾语言也不在盘上 ⇒ 显式报错（**不许**返回一门没被请求的语言当成功）。"""
        _write_catalog(self.other, DE, DE_STRINGS)
        with self.assertRaises(i18n.LocaleUnavailable) as ctx:
            i18n.load_catalog(FR, dirs=[self.other])
        self.assertIn(FR, str(ctx.exception))
        self.assertIn(EN, str(ctx.exception))  # 链尾语言在报错信息里可见
        self.assertEqual(i18n.load_catalog(EN, dirs=[self.builtin]).code, EN)  # 阴证：在盘上就取得到

    def test_load_catalog_refuses_broken_higher_priority_file(self):
        """用户目录里的坏文件**不许**被静默换成内置的好文件 —— 报错，并指出被遮蔽的真件。"""
        (self.user / f"{DE}.json").write_text("{ broken", encoding="utf-8")
        entry = next(i for i in i18n.scan_catalogs([self.user, self.builtin]) if i.code == DE)
        self.assertFalse(entry.valid)
        self.assertEqual(entry.shadowed_path, str(self.builtin / f"{DE}.json"))
        with self.assertRaises(i18n.CatalogError) as ctx:
            i18n.load_catalog(DE, dirs=[self.user, self.builtin])
        self.assertIn(str(self.builtin / f"{DE}.json"), str(ctx.exception))


class RuntimeSwitchTests(_CatalogFixture, unittest.TestCase):
    def test_t_and_set_locale_switch_at_runtime_without_restart(self):
        """阴阳双证：同一个进程、同一个模块对象，切换后 `t()` **立刻**变了。"""
        module_id = id(i18n)
        first = i18n.set_locale(ZH, dirs=[self.builtin, self.user])
        self.assertTrue(first.changed)
        self.assertEqual(first.resolved, ZH)
        before = i18n.t("doc.untitled")
        again = i18n.set_locale(EN, dirs=[self.builtin, self.user])
        after = i18n.t("doc.untitled")
        self.assertEqual(id(i18n), module_id)  # 无重启：还是同一个模块对象
        self.assertEqual(again.previous, ZH)
        self.assertNotEqual(before, after)
        self.assertEqual((before, after), (ZH_STRINGS["doc.untitled"], EN_STRINGS["doc.untitled"]))
        self.assertEqual(i18n.active_locale(), EN)

    def test_active_locale_keeps_cn_and_tw_separate(self):
        i18n.set_locale(ZH_TW, dirs=[self.builtin])
        self.assertEqual(i18n.active_locale(), ZH_TW)
        self.assertEqual(i18n.t("doc.untitled"), ZH_TW_STRINGS["doc.untitled"])
        i18n.set_locale(ZH_HANS_CN, dirs=[self.builtin])
        self.assertEqual(i18n.active_locale(), ZH_HANS_CN)  # 请求码原样保留（链上落到 zh）
        self.assertEqual(i18n.t("doc.untitled"), ZH_STRINGS["doc.untitled"])
        self.assertIn(ZH_TW, [info.code for info in i18n.scan_catalogs([self.builtin])])

    def test_set_locale_reports_degraded_when_only_fallback_available(self):
        change = i18n.set_locale(ZH_TW, dirs=[self.other_dir_with(EN)])
        self.assertEqual((change.current, change.resolved), (ZH_TW, EN))
        self.assertTrue(change.catalog_present)
        self.assertTrue(change.degraded)          # 未精确命中 ⇒ 降级可观测
        self.assertFalse(i18n.set_locale(EN, dirs=[self.builtin]).degraded)

    def test_set_locale_raises_when_other_languages_are_installed(self):
        _write_catalog(self.other, DE, DE_STRINGS)
        before = i18n.active_locale()
        with self.assertRaises(i18n.LocaleUnavailable):
            i18n.set_locale(FR, dirs=[self.other])
        self.assertEqual(i18n.active_locale(), before)  # 失败不改状态

    def test_set_locale_degrades_observably_when_no_catalogs_at_all(self):
        change = i18n.set_locale(ZH_HANS_CN, dirs=[self.empty])
        self.assertFalse(change.catalog_present)
        self.assertIsNone(change.resolved)
        self.assertEqual(change.keys, -1)  # -1 = 未采（不是"0 条译文"）
        self.assertTrue(change.degraded)
        self.assertIsNone(i18n.active_catalog())
        text = i18n.t("doc.untitled")
        self.assertTrue(text.strip())
        self.assertNotIn(".", text)
        self.assertEqual(i18n.stats()["active_catalog_keys"], -1)

    def test_set_locale_same_locale_does_not_rebroadcast(self):
        i18n.set_locale(DE, dirs=[self.builtin])
        seen = []
        i18n.on_language_changed(seen.append)
        change = i18n.set_locale(DE, dirs=[self.builtin])
        self.assertFalse(change.changed)
        self.assertEqual(seen, [])

    def test_on_language_changed_delivers_change_and_unsubscribes(self):
        seen = []
        unsubscribe = i18n.on_language_changed(seen.append)
        change = i18n.set_locale(DE, dirs=[self.builtin])
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], i18n.LanguageChange)
        self.assertEqual(seen[0].as_dict()["current"], DE)
        self.assertEqual(seen[0].keys, len(DE_STRINGS))
        self.assertEqual(seen[0].timestamp, change.timestamp)
        unsubscribe()
        unsubscribe()  # 重复退订是 no-op，不抛错
        i18n.set_locale(EN, dirs=[self.builtin])
        self.assertEqual(len(seen), 1)

    def test_listener_exception_does_not_break_switch(self):
        seen = []

        def bad_listener(change):
            raise RuntimeError("listener boom")

        i18n.on_language_changed(bad_listener)
        i18n.on_language_changed(seen.append)
        with self.assertLogs(i18n.LOG.name, level="ERROR") as captured:
            change = i18n.set_locale(DE, dirs=[self.builtin])
        self.assertEqual(change.resolved, DE)
        self.assertEqual(i18n.active_locale(), DE)
        self.assertEqual(len(seen), 1)  # 坏订阅者不许拖垮好订阅者
        self.assertIn("listener boom", "\n".join(captured.output))

    def test_on_language_changed_rejects_non_callable(self):
        with self.assertRaises(TypeError):
            i18n.on_language_changed("not callable")  # type: ignore[arg-type]

    def other_dir_with(self, code: str) -> Path:
        """造一个只装了某门语言的目录（降级路径用）。"""
        directory = self.root / f"only-{code}"
        directory.mkdir(parents=True, exist_ok=True)
        if code == EN:
            _write_catalog(directory, EN, EN_STRINGS)
        else:  # pragma: no cover - 目前只有 EN 分支被用到
            _write_catalog(directory, code, {"doc.untitled": code})
        return directory

    def test_hot_drop_of_a_new_language_is_visible_without_restart(self):
        """I18N-DESIGN §3 验收动作：往目录丢一门新语言 ⇒ **不改代码、不重启**就该出现并能切过去。"""
        self.assertEqual(i18n.scan_catalogs([self.user]), [])
        _write_catalog(self.user, FR, {"doc.untitled": "Sans titre"})
        self.assertEqual(i18n.available_locales(dirs=[self.user]), [FR])
        change = i18n.set_locale(FR, dirs=[self.user])
        self.assertEqual((change.resolved, change.keys), (FR, 1))
        self.assertFalse(change.degraded)
        self.assertEqual(i18n.t("doc.untitled"), "Sans titre")


class TranslationLookupTests(_CatalogFixture, unittest.TestCase):
    def test_t_falls_back_to_source_string_and_logs(self):
        """阳证：主语言缺键 ⇒ 取到 en 源串 + 记 WARNING + 计数；阴证见下一个用例。"""
        i18n.set_locale(ZH, dirs=[self.builtin])
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            text = i18n.t("only.in.english")
        self.assertEqual(text, EN_STRINGS["only.in.english"])
        self.assertIn("only.in.english", "\n".join(captured.output))
        self.assertGreaterEqual(i18n.stats()["fallbacks"], 1)
        with self.assertNoLogs(i18n.LOG.name, level="WARNING"):  # 同一回落只记一次，不刷屏
            self.assertEqual(i18n.t("only.in.english"), EN_STRINGS["only.in.english"])

    def test_t_primary_hit_does_not_log_or_count_fallback(self):
        """阴证：主语言有这条键时，尺子必须**看不见**回落（否则"回落计数"不可信）。"""
        i18n.set_locale(ZH, dirs=[self.builtin])
        before = i18n.stats()["fallbacks"]
        with self.assertNoLogs(i18n.LOG.name, level="WARNING"):
            text = i18n.t("doc.untitled")
        self.assertEqual(text, ZH_STRINGS["doc.untitled"])
        self.assertEqual(i18n.stats()["fallbacks"], before)

    def test_t_never_returns_key_or_empty_on_total_miss(self):
        i18n.set_locale(ZH, dirs=[self.builtin])
        self.assertEqual(i18n.t("doc.untitled"), ZH_STRINGS["doc.untitled"])  # 尺子先自证看得见"有"
        misses_before = i18n.stats()["misses"]
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            text = i18n.t("nope.missing_key")
        self.assertEqual(text, "Missing key")
        self.assertTrue(text.strip())
        self.assertNotEqual(text, "nope.missing_key")
        self.assertNotIn(".", text)
        self.assertIn("derived source string", "\n".join(captured.output))
        self.assertEqual(i18n.stats()["misses"], misses_before + 1)
        self.assertEqual(i18n.untranslated_keys()[0][0], "nope.missing_key")

    def test_t_treats_blank_translation_as_missing(self):
        i18n.set_locale(ZH, dirs=[self.builtin])
        before = i18n.stats()["empty_values"]
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            text = i18n.t("doc.empty")
        self.assertEqual(text, EN_STRINGS["doc.empty"])  # 空串不摆给用户：继续沿链取到 en 源串
        self.assertTrue(text.strip())
        self.assertGreater(i18n.stats()["empty_values"], before)
        self.assertIn("empty translation", "\n".join(captured.output))

    def test_t_substitutes_placeholders(self):
        i18n.set_locale(ZH, dirs=[self.builtin])
        self.assertEqual(i18n.t("err.open_failed", name="plate.dxf"), "Dakai shibai: plate.dxf")

    def test_t_survives_malformed_template(self):
        i18n.set_locale(ZH, dirs=[self.builtin])
        before = i18n.stats()["format_errors"]
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            text = i18n.t("doc.bad_placeholder", amount=12)
        self.assertEqual(text, ZH_STRINGS["doc.bad_placeholder"])  # 原文返回，绝不空串/异常
        self.assertTrue(text.strip())
        self.assertIn("placeholder substitution failed", "\n".join(captured.output))
        self.assertEqual(i18n.stats()["format_errors"], before + 1)

    def test_t_without_arguments_does_not_touch_braces(self):
        """没传参数就不做替换（译文里合法的花括号不许被误伤）。"""
        i18n.set_locale(ZH, dirs=[self.builtin])
        before = i18n.stats()["format_errors"]
        self.assertEqual(i18n.t("doc.bad_placeholder"), ZH_STRINGS["doc.bad_placeholder"])
        self.assertEqual(i18n.stats()["format_errors"], before)

    def test_t_survives_missing_placeholder_argument(self):
        i18n.set_locale(ZH, dirs=[self.builtin])
        before = i18n.stats()["format_errors"]
        text = i18n.t("err.open_failed", code="plate.dxf")  # 少传 name ⇒ KeyError，必须被吃掉
        self.assertEqual(i18n.stats()["format_errors"], before + 1)
        self.assertEqual(text, ZH_STRINGS["err.open_failed"])
        self.assertTrue(text.strip())


class DiffTests(_CatalogFixture, unittest.TestCase):
    def test_key_diff_is_bidirectional(self):
        zh = i18n.load_catalog(ZH, dirs=[self.builtin])
        en = i18n.load_catalog(EN, dirs=[self.builtin])
        missing, extra = i18n.key_diff(en, zh)
        self.assertEqual(missing, {"only.in.english"})
        self.assertEqual(extra, {"doc.bad_placeholder", "zh.only"})
        self.assertEqual(i18n.key_diff(zh, en),
                         ({"doc.bad_placeholder", "zh.only"}, {"only.in.english"}))
        self.assertEqual(i18n.key_diff(zh, zh), (set(), set()))  # 阴证：一致时双向都空

    def test_placeholder_diff_flags_name_and_count_mismatch(self):
        zh = i18n.load_catalog(ZH, dirs=[self.builtin])
        en = i18n.load_catalog(EN, dirs=[self.builtin])
        diff = i18n.placeholder_diff(en, zh)
        self.assertIn("err.two", diff)
        self.assertEqual(diff["err.two"], (("a", "b"), ("a",)))
        self.assertNotIn("err.open_failed", diff)  # 名字与数量一致 ⇒ 不进差集
        self.assertEqual(i18n.placeholder_diff(en, en), {})


class GettextPoTests(_CatalogFixture, unittest.TestCase):
    PO_TEXT = """# German sample catalog
msgid ""
msgstr ""
"Project-Id-Version: sample\\n"
"Language: de-DE\\n"
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "doc.untitled"
msgstr "Unbenannt"

#, fuzzy
msgid "doc.title"
msgstr "Titel (fuzzy)"

msgid "doc.untranslated"
msgstr ""

msgid "err.open_failed"
msgstr ""
"Konnte "
"{name} nicht oeffnen"

msgctxt "menu"
msgid "file"
msgstr "Datei (Menue)"

#~ msgid "old.key"
#~ msgstr "Alt"
"""

    def _write_po(self, text: str, name: str = "de-DE.po") -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        assert path.is_file()  # 夹具自证
        return path

    def test_import_po_builds_catalog_and_skips_marked_entries(self):
        catalog = i18n.import_po(str(self._write_po(self.PO_TEXT)))
        self.assertEqual(catalog.code, "de-DE")
        self.assertEqual(catalog.strings["doc.untitled"], "Unbenannt")
        self.assertEqual(catalog.strings["err.open_failed"], "Konnte {name} nicht oeffnen")
        self.assertEqual(catalog.strings["menu\x04file"], "Datei (Menue)")
        self.assertNotIn("doc.title", catalog.strings)          # fuzzy 默认跳过
        self.assertNotIn("doc.untranslated", catalog.strings)   # 空 msgstr 跳过
        self.assertNotIn("old.key", catalog.strings)            # 废弃条目跳过
        self.assertEqual(catalog.meta["po_skipped_fuzzy"], 1)
        self.assertEqual(catalog.meta["po_skipped_untranslated"], 1)
        self.assertEqual(catalog.meta["po_skipped_obsolete"], 1)
        self.assertIsNone(catalog.meta["native_name"])          # 没采到就写 None，不编造
        self.assertEqual(catalog.fallback(), None)

    def test_import_po_include_fuzzy_is_opt_in(self):
        catalog = i18n.import_po(str(self._write_po(self.PO_TEXT)), include_fuzzy=True)
        self.assertEqual(catalog.strings["doc.title"], "Titel (fuzzy)")
        self.assertEqual(catalog.meta["po_skipped_fuzzy"], 0)

    def test_import_po_export_po_roundtrip(self):
        first = i18n.import_po(str(self._write_po(self.PO_TEXT)))
        out1 = self.root / "out" / "de-DE.po"
        out2 = self.root / "out2" / "de-DE.po"
        i18n.export_po(first, str(out1))
        i18n.export_po(first, str(out2))
        self.assertTrue(out1.is_file())
        self.assertEqual(out1.read_bytes(), out2.read_bytes())  # 同样输入逐字节可复现
        again = i18n.import_po(str(out1))
        self.assertEqual(again.code, first.code)
        self.assertEqual(dict(again.strings), dict(first.strings))
        self.assertEqual(i18n.key_diff(again, first), (set(), set()))
        self.assertEqual(i18n.placeholder_diff(again, first), {})

    def test_import_po_code_from_file_name_and_missing_code_raises(self):
        payload = 'msgid "doc.title"\nmsgstr "Titel"\n'
        catalog = i18n.import_po(str(self._write_po(payload, name=f"{PT_BR}.po")))
        self.assertEqual(catalog.code, PT_BR)
        with self.assertRaises(i18n.CatalogError):
            i18n.import_po(str(self._write_po(payload, name="messages.po")))

    def test_import_po_rejects_unreadable_file(self):
        with self.assertRaises(i18n.CatalogError):
            i18n.import_po(str(self.root / "nope.po"))


class DuplicateKeyTests(_CatalogFixture, unittest.TestCase):
    def test_same_size_same_timestamp_replacement_does_not_keep_stale_translation(self):
        path = _write_catalog(self.user, DE, {"cmd.fillet": "first"})
        self.assertEqual(i18n.load_catalog(DE, dirs=[self.user]).strings["cmd.fillet"], "first")
        stat = path.stat()
        _write_catalog(self.user, DE, {"cmd.fillet": "other"})
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, stat.st_size)
        self.assertEqual(i18n.load_catalog(DE, dirs=[self.user]).strings["cmd.fillet"], "other")

    def test_data_defined_script_policy_rejects_foreign_copy_without_locale_branch(self):
        for text, char in (("\u30d5", "U+30D5"), ("\uff8c", "U+FF8C")):
            with self.subTest(text=text):
                path = _write_catalog(self.user, DE, {"cmd.fillet": text}, extra={
                    "forbidden_codepoint_ranges": [["3040", "30FF"], ["FF66", "FF9F"]],
                })
                info = i18n.scan_catalogs([self.user])[0]
                self.assertFalse(info.valid)
                self.assertIn(char, info.error)
                self.assertIn("cmd.fillet", info.error)
                with self.assertRaises(i18n.CatalogError) as caught:
                    i18n.load_catalog(DE, dirs=[self.user])
                self.assertIn(str(path), str(caught.exception))
        _write_catalog(self.user, DE, {"cmd.fillet": "\u30d5"})
        self.assertEqual(i18n.load_catalog(DE, dirs=[self.user]).strings["cmd.fillet"], "\u30d5")

    def test_invalid_script_policy_is_not_silently_ignored(self):
        for ranges in ("bad", [["FFFF", "0000"]], [["oops", "FFFF"]], [[0, 10]]):
            with self.subTest(ranges=ranges):
                payload = _catalog_payload(DE, DE_STRINGS, extra={"forbidden_codepoint_ranges": ranges})
                with self.assertRaises(i18n.CatalogError):
                    i18n.Catalog.from_dict(payload)

    def test_duplicate_keys_are_invalid_at_every_object_depth(self):
        for text, key in (
            ('{"strings": {}, "strings": {}}', "strings"),
            ('{"_meta": {"code": "en", "code": "ja"}, "strings": {}}', "code"),
            ('{"strings": {"cmd.fillet": "Fillet", "cmd.fillet": "Wrong"}}', "cmd.fillet"),
        ):
            with self.subTest(key=key):
                path = self.user / "en.json"
                path.write_text(text, encoding="utf-8")
                i18n.clear_cache()
                info = next(item for item in i18n.scan_catalogs([self.user, self.builtin])
                            if item.code == EN)
                self.assertFalse(info.valid)
                self.assertIn("duplicate JSON key", info.error)
                self.assertIn(key, info.error)
                self.assertEqual(info.shadowed_path, str(self.en_path))
                with self.assertRaises(i18n.CatalogError) as caught:
                    i18n.load_catalog(EN, dirs=[self.user, self.builtin])
                self.assertIn(str(path), str(caught.exception))
                self.assertIn(key, str(caught.exception))

    def test_repaired_catalog_reloads_after_cached_duplicate_error(self):
        path = self.user / "en.json"
        path.write_text('{"strings": {"x": "one", "x": "two"}}', encoding="utf-8")
        self.assertFalse(i18n.scan_catalogs([self.user])[0].valid)
        _write_catalog(self.user, EN, EN_STRINGS)
        catalog = i18n.load_catalog(EN, dirs=[self.user])
        self.assertEqual(catalog.path, str(path))
        self.assertEqual(catalog.strings, EN_STRINGS)


class DefaultDirsTests(_CatalogFixture, unittest.TestCase):
    def test_lazy_state_uses_env_dirs_before_any_set_locale(self):
        """没调用过 `set_locale` 也能取词：懒加载就走默认目录（env 追加目录优先）。"""
        with mock.patch.dict(os.environ, {i18n.ENV_DIRS: str(self.builtin)}):
            self.assertEqual(i18n.active_locale(), i18n.DEFAULT_LANGUAGE)
            self.assertEqual(i18n.t("doc.untitled"), EN_STRINGS["doc.untitled"])
            self.assertIsNotNone(i18n.active_catalog())
            self.assertEqual(i18n.active_chain()[-1], i18n.DEFAULT_LANGUAGE)

    def test_default_dirs_puts_env_dir_first_and_builtin_last(self):
        env_dirs_value = os.pathsep.join([str(self.user), str(self.other)])
        with mock.patch.dict(os.environ, {i18n.ENV_DIRS: env_dirs_value}):
            dirs = i18n.default_dirs()
        self.assertEqual(dirs[0], str(self.user))
        self.assertEqual(dirs[1], str(self.other))
        self.assertTrue(dirs[-1].endswith(str(Path("data") / "i18n")))
        self.assertIn(i18n.APP_DIR_NAME, dirs[-2])
        infos = i18n.scan_catalogs(dirs)
        self.assertEqual(next(i for i in infos if i.code == EN).dir_index, len(dirs) - 1)  # 随包目录排最后

    def test_default_dirs_without_env_has_no_extra_dir(self):
        with mock.patch.dict(os.environ, {i18n.ENV_DIRS: ""}):
            dirs = i18n.default_dirs()
        self.assertNotIn(str(self.user), dirs)
        self.assertEqual(len(dirs), len(set(dirs)))
        self.assertEqual(dirs[-1], i18n._builtin_dir())

    def test_scan_catalogs_rejects_a_bare_path_string(self):
        with self.assertRaises(TypeError):
            i18n.scan_catalogs(str(self.builtin))  # type: ignore[arg-type]


class SourceDisciplineTests(_CatalogFixture, unittest.TestCase):
    """机检"语言码/文案不许写死在源码里"—— 尺子自身带阳性对照，证明它看得见红。"""

    LOCALE_LITERAL_RE = re.compile(r"""["'](?:zh|ja|de|pt|fr|en|ru|ko|es|it)[-_][A-Za-z0-9]{2,8}["']""")
    CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")

    def test_source_rulers_see_red_on_a_mutated_copy(self):
        """变异体反证：把**真源码**复制一份、注入写死语言码与中文文案，两条尺子必须报红。"""
        mutated = self.root / "catalog_mutated.py"
        mutated.write_text(
            _CATALOG_FILE.read_text(encoding="utf-8")
            + '\n_LEAK_TAG = "zh-CN"\n_LEAK_TEXT = "\u56fe\u5c42"\n',
            encoding="utf-8",
        )
        self.assertTrue(self.LOCALE_LITERAL_RE.findall(mutated.read_text(encoding="utf-8")))
        cjk = [text for text in _string_literals(mutated) if self.CJK_RE.search(text)]
        self.assertEqual(len(cjk), 1, f"mutation ruler must see exactly the injected copy, saw {cjk}")

    def test_module_source_has_no_hardcoded_locale_literals(self):
        source = _CATALOG_FILE.read_text(encoding="utf-8")
        hits = self.LOCALE_LITERAL_RE.findall(source)
        self.assertEqual(hits, [], f"catalog.py must not hardcode locale tags: {hits}")

    def test_module_source_has_no_cjk_string_literals(self):
        cjk = [text for text in _string_literals(_CATALOG_FILE) if self.CJK_RE.search(text)]
        self.assertEqual(cjk, [], f"catalog.py must keep user-facing copy in language packs: {cjk}")

    def test_module_under_test_is_the_real_file(self):
        """夹具自证：被测模块就是仓里那个文件（不是内存副本、也不是别处的同名模块）。"""
        self.assertTrue(_CATALOG_FILE.is_file())
        self.assertEqual(Path(i18n.__file__).resolve(), _CATALOG_FILE)

    def test_docstring_filter_really_excludes_docstrings(self):
        literals = _string_literals(_CATALOG_FILE)
        self.assertTrue(any("cadcore" in text for text in literals))   # 真读到了字面量
        self.assertFalse(any("\n" in text and "A16" in text for text in literals))  # 模块 docstring 被排除


class RealDataIntegrationTests(_CatalogFixture, unittest.TestCase):
    """对着**随包真数据**（A18 的 `cadcore/data/i18n/`）跑一遍 —— 单测夹具之外的第三条腿。

    这一组是 A16/A18 的**接口契约**：路径对得上、`_meta` 八字段齐、三包键集合与占位符逐条一致、
    切换后取词真的变、缺语言时回落英文源串。数据缺席 ⇒ skip（A16 不替 A18 的进度报红）。
    数据里出现语言码是**数据**（源码纪律只约束 `.py`，见 SourceDisciplineTests）。
    """

    NEVER_INSTALLED = "zz-ZZ"  # 永不发布的标签：用来测"请求语言缺席 ⇒ 回落 en 源串"

    def setUp(self):
        super().setUp()
        self.data_dir = _PKG_ROOT / "cadcore" / "data" / "i18n"
        if not self.data_dir.is_dir():
            self.skipTest(f"A18 language packs are not on disk yet: {self.data_dir}")

    def _catalogs(self) -> dict:
        return {code: i18n.load_catalog(code, dirs=[self.data_dir])
                for code in i18n.available_locales(dirs=[self.data_dir])}

    def test_builtin_dir_points_at_the_shipped_language_packs(self):
        """A16 的"随包语言包目录"必须就是 A18 落盘的那个目录（否则线上永远扫不到语言）。"""
        self.assertEqual(i18n._builtin_dir(), str(self.data_dir))
        self.assertEqual(i18n.default_dirs()[-1], str(self.data_dir))  # 随包目录 = 最低优先级兜底
        infos = {info.code: info for info in i18n.scan_catalogs(i18n.default_dirs())}
        for code in i18n.available_locales(dirs=[self.data_dir]):
            self.assertIn(code, infos, f"shipped pack {code} is not discoverable via default_dirs()")
            self.assertTrue(infos[code].valid)

    def test_real_packs_load_with_complete_meta_and_matching_keys(self):
        catalogs = self._catalogs()
        self.assertGreaterEqual(len(catalogs), 3, f"expected >=3 shipped packs, got {sorted(catalogs)}")
        base_code = next(iter(sorted(catalogs)))
        base = catalogs[base_code]
        for code, catalog in catalogs.items():
            with self.subTest(catalog=code):
                self.assertEqual(catalog.code, code)
                self.assertTrue(catalog.strings, "pack must not be empty")
                self.assertTrue(catalog.native_name())
                self.assertTrue(catalog.font_fallback())
                for field_name in i18n.META_FIELDS:
                    self.assertIn(field_name, catalog.meta)
                blank = [k for k, v in catalog.strings.items() if not v.strip()]
                self.assertEqual(blank, [], f"blank translations would silently fall back: {blank}")
                missing, extra = i18n.key_diff(base, catalog)
                self.assertEqual((sorted(missing)[:8], sorted(extra)[:8]), ([], []),
                                 f"{code} key set differs from {base_code}")
                self.assertEqual(i18n.placeholder_diff(base, catalog), {})

    def test_shipped_simplified_chinese_command_and_status_translations(self):
        catalog = i18n.load_catalog("zh-CN", dirs=[self.data_dir])
        expected = {
            "cmd.rectangle": "\u77e9\u5f62", "cmd.polygon": "\u591a\u8fb9\u5f62",
            "cmd.slot": "\u8170\u5f62\u69fd", "cmd.move": "\u79fb\u52a8",
            "cmd.copy": "\u590d\u5236", "cmd.rotate": "\u65cb\u8f6c",
            "cmd.scale": "\u7f29\u653e", "cmd.mirror": "\u955c\u50cf",
            "cmd.offset": "\u504f\u79fb", "cmd.trim": "\u4fee\u526a",
            "cmd.extend": "\u5ef6\u4f38", "cmd.fillet": "\u5706\u89d2",
            "cmd.chamfer": "\u5012\u89d2", "cmd.array_linear": "\u7ebf\u6027\u9635\u5217",
            "cmd.delete_constraint": "\u5220\u9664\u7ea6\u675f",
            "cmd.snap_query": "\u6355\u6349\u67e5\u8be2", "cmd.ortho": "\u6b63\u4ea4",
            "cmd.polar": "\u6781\u8f74", "status.tool": "\u5de5\u5177",
            "status.snap": "\u6355\u6349", "status.ortho": "\u6b63\u4ea4",
            "status.polar": "\u6781\u8f74", "status.snap.indicator": "\u6355\u6349\uff1a{kind}",
            "status.snap.none": "\u65e0\u6355\u6349\u70b9",
            "status.snap.candidates": "{count} \u4e2a\u5019\u9009\u70b9",
            "status.snap.endpoint": "\u7aef\u70b9", "status.snap.midpoint": "\u4e2d\u70b9",
            "status.snap.center": "\u5706\u5fc3", "status.snap.quadrant": "\u8c61\u9650\u70b9",
            "status.snap.intersection": "\u4ea4\u70b9", "status.snap.perpendicular": "\u5782\u8db3",
            "status.snap.nearest": "\u6700\u8fd1\u70b9", "status.snap.grid": "\u6805\u683c",
            "status.toggle.on": "{name}\uff1a\u5f00", "status.toggle.off": "{name}\uff1a\u5173",
            "status.polar.increment": "\u6781\u8f74\u589e\u91cf {angle}\u00b0",
            "status.command_done": "{command}\uff1a\u5b8c\u6210",
        }
        self.assertEqual({key: catalog.strings[key] for key in expected}, expected)
        self.assertFalse(any(re.search(r"[\u3040-\u30ff\uff66-\uff9f]", value)
                             for value in catalog.strings.values()))

    def test_shipped_english_has_no_chinese_or_japanese_copy(self):
        catalog = i18n.load_catalog(EN, dirs=[self.data_dir])
        self.assertEqual(catalog.strings["cmd.fillet"], "Fillet")
        self.assertEqual(catalog.strings["cmd.move"], "Move")
        self.assertEqual(catalog.strings["status.polar"], "Polar")
        self.assertEqual([key for key, value in catalog.strings.items()
                          if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]", value)], [])

    def test_real_packs_switch_at_runtime_and_differ_from_english(self):
        catalogs = self._catalogs()
        if EN not in catalogs:
            self.skipTest("shipped packs have no source-language pack to compare against")
        source = catalogs[EN]
        for code in sorted(catalogs):
            if code == EN:
                continue
            differing = [k for k, v in catalogs[code].strings.items() if source.strings.get(k) != v]
            self.assertTrue(differing[:3], f"{code} looks like a copy of {EN}")
            specimen = differing[0]
            change = i18n.set_locale(code, dirs=[self.data_dir])
            self.assertEqual((change.resolved, change.degraded), (code, False))
            self.assertEqual(change.keys, len(catalogs[code].strings))
            translated = i18n.t(specimen)
            self.assertEqual(translated, catalogs[code].strings[specimen])
            i18n.set_locale(EN, dirs=[self.data_dir])
            self.assertEqual(i18n.t(specimen), source.strings[specimen])
            self.assertNotEqual(translated, i18n.t(specimen))  # 切回去就变了（同一进程，无重启）

    def test_real_packs_fall_back_to_english_source_string(self):
        catalogs = self._catalogs()
        if EN not in catalogs:
            self.skipTest("shipped packs have no source-language pack")
        source = catalogs[EN]
        change = i18n.set_locale(self.NEVER_INSTALLED, dirs=[self.data_dir])
        self.assertEqual(change.current, self.NEVER_INSTALLED)
        self.assertEqual(change.resolved, EN)
        self.assertTrue(change.degraded)  # 未精确命中 ⇒ 降级可观测
        specimen = sorted(source.strings)[0]
        with self.assertLogs(i18n.LOG.name, level="WARNING") as captured:
            self.assertEqual(i18n.t(specimen), source.strings[specimen])  # 回落英文源串
        self.assertIn(specimen, "\n".join(captured.output))
        unknown = i18n.t("zz.never_defined_key")
        self.assertTrue(unknown.strip())
        self.assertNotEqual(unknown, "zz.never_defined_key")
        self.assertNotIn(".", unknown)


class SuiteShapeTests(_CatalogFixture, unittest.TestCase):
    def test_suite_has_at_least_fourteen_cases(self):
        """交付判据之一：本模块单测 ≥14 例（数字以当次装载为准，不手抄）。"""
        suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
        count = suite.countTestCases()
        self.assertGreaterEqual(count, 14, f"expected >=14 test cases, found {count}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
