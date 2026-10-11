"""G8 实证单测：**新增一门语言不需要改任何代码**（纯标准库，可 `pytest` 也可直接 `python3` 跑）。

判据（对应用户可见结论「加语言 = 加文件」）：

- 在临时"用户语言目录"里放一份新语言包（`eo.json`，键集合与 `en` 逐条一致，译文是**伪译文**
  `eo.pseudo:<en 原文>` —— 只用于判别"串来自这份新包"，不冒充真实译者产出），
  `catalog.scan_catalogs([user_dir, builtin_dir])` 必须发现它（`dir_index=0`，用户目录优先）、
  `catalog.set_locale("eo", dirs=...)` 必须真的切过去、`catalog.t(key)` 必须返回该包里的串；
  留空的键必须回落到 `en`（`t()` 硬保证：绝不返回空串、绝不返回 key 名）。
- 全程**零改代码**：跑前跑后对 `catalog.py` 与随包 `en.json` 取 sha256，必须逐字节相同。
- **阴性对照（尺子必须看得见红）**：①键集合缺一个 ⇒ `key_diff` 的 `missing` 非空 + G8 门准入判红；
  ②没有 `_meta` ⇒ G8 门准入**必须拒**（⚠️ 底层 `catalog.py` 对此是**容忍**的：语言码回落到文件名主干、
  `license`/`version`/`source` 全记 `None` —— 本文件把这个分层事实**显式断言**出来，不冒充成"底层会拒"）；
  ③只改代码不改语言包**不可能**出现新语言（语言清单来自扫盘，不来自源码常量），
  并用 G1 门的 `scan_source_literals` 同一把尺子证明产品源码里没有语言码字面量、且该尺子确实看得见红。

口径：`0`=真为零 / `-1`=未采 / `NA`=不适用；依赖或真件缺席时**报错（红）**，不静默当绿。
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

def _find_oss_root(start: Path) -> Path:
    """从本文件向上找 `oss-alternatives/` 根。

    为什么不写死 `parents[N]`（2026-09-29 实撞）：本测试原先在 `oss-alternatives/tests/`，
    移进 `oss-alternatives/packages/cadcore/tests/` 后 `parents[1]` 从 `oss-alternatives/`
    变成了 `packages/cadcore/`，路径拼出一棵不存在的树 ⇒ `setUpClass` 当场断言失败。
    改成"按特征向上找"，文件挪到哪都成立。
    """
    for candidate in (start, *start.parents):
        if (candidate / "packages" / "cadcore" / "cadcore" / "i18n" / "catalog.py").is_file():
            return candidate
    raise RuntimeError(f"cannot locate oss-alternatives root from {start}")


OSS_ROOT = _find_oss_root(Path(__file__).resolve())
CADCORE_PKG = OSS_ROOT / "packages" / "cadcore"
CATALOG_PY = CADCORE_PKG / "cadcore" / "i18n" / "catalog.py"
BUILTIN_I18N = CADCORE_PKG / "cadcore" / "data" / "i18n"
GATE_PY = OSS_ROOT / "tools" / "cad" / "i18n_newlang_gate.py"
G1_GATE_PY = OSS_ROOT / "tools" / "cad" / "i18n_gate.py"

NEW_CODE = "eo"
PLAIN_CODE = "en"


def _load_by_path(name: str, path: Path) -> Any:
    """按文件路径装载模块（`__init__.py` 是否已落盘不影响本测试）。"""
    spec = importlib.util.spec_from_file_location(name, Path(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_catalog_module() -> Any:
    """优先包导入 `cadcore.i18n.catalog`，失败退到文件级装载（两路都失败即红）。"""
    if str(CADCORE_PKG) not in sys.path:
        sys.path.insert(0, str(CADCORE_PKG))
    try:
        return importlib.import_module("cadcore.i18n.catalog")
    except Exception:
        return _load_by_path("cadcore_i18n_catalog_for_newlang_test", CATALOG_PY)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NewLanguageWithoutCodeChangeTest(unittest.TestCase):
    """一次语言新增的端到端实证 + 三条阴性对照。"""

    @classmethod
    def setUpClass(cls) -> None:
        for required in (CATALOG_PY, BUILTIN_I18N, GATE_PY, G1_GATE_PY):
            assert required.exists(), f"required real artefact missing: {required}"
        cls.catalog = _load_catalog_module()
        cls.gate = _load_by_path("i18n_newlang_gate_under_test", GATE_PY)
        cls.g1 = _load_by_path("i18n_gate_for_newlang_test", G1_GATE_PY)
        base_file = BUILTIN_I18N / f"{cls.catalog.DEFAULT_LANGUAGE}.json"
        cls.base_file = base_file
        cls.base_payload = json.loads(base_file.read_text(encoding="utf-8"))
        cls.base_strings = dict(cls.base_payload["strings"])
        cls.probe_key = cls.gate.pick_plain_key(cls.base_strings)
        cls.blank_key = cls.gate.pick_plain_key(cls.base_strings, exclude=[cls.probe_key])

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="g8-i18n-newlang-"))
        self.user_dir = self.tmp / "user-i18n"
        self.user_dir.mkdir()
        self.dirs = [str(self.user_dir), str(BUILTIN_I18N)]

    def tearDown(self) -> None:
        self.catalog.clear_cache()
        self.catalog.set_locale(self.catalog.DEFAULT_LANGUAGE)  # 还原全局语言态，避免污染别的测试
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 夹具 ────────────────────────────────────────────────────────────────────────────
    def _write_fixture(self, *, fixture: str = "clean") -> Path:
        payload = self.gate.build_payload(
            self.base_payload, code=NEW_CODE, fixture=fixture,
            blank_key=self.blank_key,
            drop_key=(self.probe_key if fixture == "drop_key" else None),
        )
        path = self.user_dir / f"{NEW_CODE}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        self.catalog.clear_cache()
        return path

    # ── ① 加文件即加语言（主判据） ───────────────────────────────────────────────────────
    def test_new_language_via_catalog_file_only(self) -> None:
        catalog = self.catalog
        code_sha = _sha256(CATALOG_PY)
        base_sha = _sha256(self.base_file)

        before = [i for i in catalog.scan_catalogs([str(BUILTIN_I18N)]) if i.valid]
        self.assertNotIn(NEW_CODE, [i.code for i in before], "new code must not exist before the file is added")

        path = self._write_fixture()
        infos = catalog.scan_catalogs(self.dirs)
        codes = [i.code for i in infos if i.valid]
        self.assertIn(NEW_CODE, codes)
        self.assertEqual(len(codes), len(before) + 1, "exactly one new language discovered")
        info = next(i for i in infos if i.code == NEW_CODE)
        self.assertTrue(info.valid, f"catalog must be valid: {info.error}")
        self.assertEqual(info.dir_index, 0, "user dir must win over the builtin dir")
        self.assertEqual(Path(info.path), path)

        change = catalog.set_locale(NEW_CODE, dirs=self.dirs)
        self.assertTrue(change.catalog_present)
        self.assertEqual(change.resolved, NEW_CODE)
        self.assertTrue(change.changed)
        self.assertEqual(catalog.active_locale(), NEW_CODE)
        self.assertGreater(change.keys, 0, "new catalog must expose a non-empty key set")

        expected = self.gate.pseudo_translation(self.base_strings[self.probe_key])
        self.assertEqual(catalog.t(self.probe_key), expected, "t() must serve the new catalog string")
        self.assertNotEqual(catalog.t(self.probe_key), self.base_strings[self.probe_key])

        blank_served = catalog.t(self.blank_key)
        self.assertEqual(blank_served, self.base_strings[self.blank_key], "blank entry falls back to en")
        self.assertTrue(blank_served.strip(), "t() must never return an empty string")

        new_cat = catalog.load_catalog(NEW_CODE, dirs=self.dirs)
        base_cat = catalog.load_catalog(catalog.DEFAULT_LANGUAGE, dirs=[str(BUILTIN_I18N)])
        self.assertEqual(catalog.key_diff(base_cat, new_cat), (set(), set()),
                         "fixture key set must equal the source-string language exactly")

        self.assertEqual(_sha256(CATALOG_PY), code_sha, "catalog.py must be byte-identical (no code change)")
        self.assertEqual(_sha256(self.base_file), base_sha, "builtin en.json must be untouched")

    # ── ②a 阴性对照：缺一个键必须判红 ────────────────────────────────────────────────────
    def test_negative_dropped_key_is_red(self) -> None:
        catalog = self.catalog
        self._write_fixture(fixture="drop_key")
        new_cat = catalog.load_catalog(NEW_CODE, dirs=self.dirs)
        base_cat = catalog.load_catalog(catalog.DEFAULT_LANGUAGE, dirs=[str(BUILTIN_I18N)])
        missing, extra = catalog.key_diff(base_cat, new_cat)
        self.assertEqual(missing, {self.probe_key})
        self.assertEqual(extra, set())

        admission = self.gate.admit_catalog_file(self.user_dir / f"{NEW_CODE}.json",
                                                 module=catalog, base_file=self.base_file)
        self.assertFalse(admission.ok, "admission must refuse a key-incomplete catalog")
        self.assertEqual(admission.keys_missing, 1)

        report = self.gate.run_check(NEW_CODE, fixture="drop_key")
        self.assertEqual(report.rc, 1, report.line())
        self.assertEqual(report.verdict, "FAIL")
        self.assertGreaterEqual(report.key_diff_missing, 1)

    # ── ②b 阴性对照：没有 `_meta` 必须被拒 ───────────────────────────────────────────────
    def test_negative_missing_meta_is_refused(self) -> None:
        catalog = self.catalog
        self._write_fixture(fixture="no_meta")
        path = self.user_dir / f"{NEW_CODE}.json"
        admission = self.gate.admit_catalog_file(path, module=catalog, base_file=self.base_file)
        self.assertFalse(admission.ok, "a catalog without _meta must not be admitted")
        self.assertIn("meta_absent", admission.reasons)

        # 分层事实（如实断言，不冒充底层会拒）：catalog.py 只按文件名主干取码 ⇒ 仍然 valid，
        # 且 license/version/source 全缺 ⇒ 这正是准入层必须存在的理由。
        raw = [i for i in catalog.scan_catalogs(self.dirs) if i.code == NEW_CODE]
        self.assertEqual(len(raw), 1)
        self.assertTrue(raw[0].valid, "baseline library tolerates a missing _meta (recorded, not endorsed)")
        self.assertIsNone(raw[0].license)
        self.assertIsNone(raw[0].version)

        report = self.gate.run_check(NEW_CODE, fixture="no_meta")
        self.assertEqual(report.rc, 1, report.line())
        self.assertEqual(report.admit, 0)
        self.assertEqual(report.verdict, "FAIL")

    # ── ②c 阴性对照：语言清单来自扫盘，不来自源码常量 ────────────────────────────────────
    def test_languages_come_from_disk_not_source(self) -> None:
        catalog = self.catalog
        constants = "SUPPORTED_LANGUAGES = (" + ", ".join(
            json.dumps(item) for item in (NEW_CODE, catalog.DEFAULT_LANGUAGE)
        ) + ")\n"
        (self.user_dir / "language_constants.py").write_text(constants, encoding="utf-8")
        catalog.clear_cache()
        codes = [i.code for i in catalog.scan_catalogs(self.dirs) if i.valid]
        self.assertNotIn(NEW_CODE, codes, "a source constant must not invent a language")

        self._write_fixture()
        codes_after = [i.code for i in catalog.scan_catalogs(self.dirs) if i.valid]
        self.assertIn(NEW_CODE, codes_after, "the language appears only once the catalog file exists")

    # ── ②d 尺子自证：产品源码无语言码字面量，且该尺子看得见红 ─────────────────────────────
    def test_source_has_no_locale_literals_and_ruler_sees_red(self) -> None:
        scan = self.g1.scan_source_literals(CADCORE_PKG)
        self.assertEqual(scan.locale_literals, 0, f"examples: {scan.locale_examples}")
        self.assertEqual(scan.hardcoded_literals, 0, f"examples: {scan.hardcoded_examples}")
        self.assertGreater(scan.files_scanned, 0, "the literal ruler must actually scan product files")

        probe_tree = self.tmp / "mutant-tree"
        (probe_tree / "cadcore" / "i18n").mkdir(parents=True)
        literal = "-".join(("zh", "CN"))     # 拼装出语言码，本文件自身不留裸字面量
        (probe_tree / "cadcore" / "i18n" / "probe_hardcoded_language.py").write_text(
            "PROBE_LANGUAGE = " + json.dumps(literal) + "\n", encoding="utf-8")
        mutant_scan = self.g1.scan_source_literals(probe_tree)
        self.assertGreaterEqual(mutant_scan.locale_literals, 1, "the ruler must see a hardcoded locale literal")

    # ── ③ 门自检：干净夹具绿 + 三种做坏红 ────────────────────────────────────────────────
    def test_gate_selftest_is_green(self) -> None:
        self.assertEqual(self.gate.selftest(new_code=NEW_CODE), 0, "G8 gate selftest must pass")
        self.assertEqual(self.gate.main(["--check", "--lang", NEW_CODE]), 0, "G8 --check must be green")


if __name__ == "__main__":
    unittest.main(verbosity=2)
