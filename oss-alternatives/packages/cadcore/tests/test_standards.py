"""tests.test_standards —— A17 制图标准档读取层单测（26 例）。

设计一句话
----------
标准档这一层最容易出的两类事故是「**编数**」与「**两套话**」：未核字段被邻档的值填满、
或者出图层与标准层各读各的。所以本文件的尺子成对出现（阴阳双证）：
既测"该绿时绿"（五档读得出、字段与盘上逐字一致、与 `sheet.load_profile` 完全一致），
也测"该红时红"（坏文件报错、`na_fields` 自相矛盾报错、未知 id 报错而不是回 None、
比对尺子对人为改动确实报差异）。

夹具自证
--------
临时夹具全部落在 `tempfile` 目录，并断言**真件在场**（文件确实落盘、内容与磁盘一致、
`dir_` 参数确实生效 —— 临时档出现、真档不出现）；不碰仓库内任何别人的文件。

说明
----
本文件里的 BCP-47 标签（`zh-CN` / `ja-JP` 等）与 `first`/`third` 是**夹具数据**：
单测要打的正是"标签 → 标准档"这条纯规则与"未核 ⇒ None"这条契约，
它们不构成任何产品逻辑分支（产品侧映射来自 `data/i18n/` 的数据声明）。
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.draw import sheet as sh  # noqa: E402
from cadcore.errors import I18nError  # noqa: E402
from cadcore.i18n import catalog as cat  # noqa: E402
from cadcore.i18n import standards as sm  # noqa: E402

_STANDARDS_DIR = _PKG_ROOT / "cadcore" / "data" / "standards"
_SOURCE_FILE = _PKG_ROOT / "cadcore" / "i18n" / "standards.py"


def _raw(standard_id: str) -> dict:
    """盘上裸 JSON（本文件里所有"期望值"都从它现读，不抄死数）。"""
    return json.loads((_STANDARDS_DIR / f"{standard_id}.json").read_text(encoding="utf-8"))


def _fixture_profile(**overrides) -> dict:
    """造一个**自洽**的临时标准档：契约字段齐、`na_fields` 恰为其中的空值字段。"""
    payload = {field: None for field in sm.PROFILE_FIELDS}
    payload.update(overrides)
    payload["na_fields"] = [field for field in sm.PROFILE_FIELDS if payload[field] is None]
    return payload


def _profile_pair(standard_id: str) -> tuple[dict, dict]:
    """A17 侧与出图层侧的同一档：`(standards.as_dict(), sheet.load_profile()[0])`。"""
    return sm.get_profile(standard_id).as_dict(), sh.load_profile(standard_id)[0]


def _compare_keys(ids: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """契约要求的三个关键字段逐一比对 → `(不一致清单, 来源清单)`。

    `sheet.load_profile()` 走 A17 时来源应写成 `cadcore.i18n.standards.get_profile`；
    若退回自己读 JSON，则"两个真源"已经发生，即使字段碰巧相同也必须能看出来。
    """
    diffs: list[str] = []
    sources: list[str] = []
    for standard_id in ids:
        mine, theirs = _profile_pair(standard_id)
        sources.append(str(sh.load_profile(standard_id)[1]))
        if mine.get("projection_angle") != theirs.get("projection_angle"):
            diffs.append(f"{standard_id}:projection_angle")
        if mine.get("sheet_sizes") != theirs.get("sheet_sizes"):
            diffs.append(f"{standard_id}:sheet_sizes")
        if set(mine.get("na_fields") or []) != set(theirs.get("na_fields") or []):
            diffs.append(f"{standard_id}:na_fields")
        if len(mine.get("na_fields") or []) != len(theirs.get("na_fields") or []):
            diffs.append(f"{standard_id}:na_fields_count")
    return diffs, sources


class StandardIdsTest(unittest.TestCase):
    def test_standard_ids_match_data_dir_order_and_contract(self) -> None:
        on_disk = tuple(sorted(path.stem for path in _STANDARDS_DIR.glob("*.json")
                               if not path.name.startswith("_")))
        self.assertEqual(set(sm.STANDARD_IDS), set(on_disk))
        self.assertEqual(len(sm.STANDARD_IDS), len(set(sm.STANDARD_IDS)))
        self.assertEqual(sm.STANDARD_IDS, tuple(
            [item for item in ("iso", "gbt", "ansi", "din", "jis") if item in on_disk]
            + sorted(item for item in on_disk if item not in ("iso", "gbt", "ansi", "din", "jis"))))

    def test_load_profiles_covers_every_declared_id_with_a_real_file(self) -> None:
        profiles = sm.load_profiles()
        self.assertEqual(set(profiles), set(sm.STANDARD_IDS))
        for standard_id, profile in profiles.items():
            self.assertTrue(Path(profile.path).is_file(), standard_id)
            self.assertEqual(profile.standard_id, standard_id)
            self.assertEqual(tuple(profiles), tuple(sm.STANDARD_IDS))


class LoadProfilesTest(unittest.TestCase):
    def test_custom_dir_is_honoured_and_builtin_ids_stay_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first = directory / "zz-demo-a.json"
            second = directory / "zz-demo-b.json"
            first.write_text(json.dumps(_fixture_profile(projection_angle="third",
                                                         sheet_sizes={"A4": [1, 2]})),
                             encoding="utf-8")
            second.write_text(json.dumps(_fixture_profile()), encoding="utf-8")
            self.assertTrue(first.is_file() and second.is_file())
            profiles = sm.load_profiles(tmp)
            self.assertEqual(tuple(profiles), ("zz-demo-a", "zz-demo-b"))
            self.assertEqual(profiles["zz-demo-a"].projection_angle, "third")
            self.assertEqual(profiles["zz-demo-a"].sheet_sizes, {"A4": [1, 2]})
            self.assertEqual(set(profiles["zz-demo-a"].na_fields),
                             set(sm.PROFILE_FIELDS) - {"projection_angle", "sheet_sizes"})
            for builtin in sm.STANDARD_IDS:
                self.assertNotIn(builtin, profiles)

    def test_missing_dir_raises_instead_of_returning_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            with self.assertRaises(I18nError) as ctx:
                sm.load_profiles(missing)
            self.assertEqual(ctx.exception.reason, "standards_dir_missing")
            self.assertFalse(missing.exists())

    def test_empty_dir_raises_instead_of_faking_zero_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(I18nError) as ctx:
                sm.load_profiles(tmp)
            self.assertEqual(ctx.exception.reason, "standards_dir_empty")

    def test_broken_json_raises_with_the_offending_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "zz-broken.json"
            bad.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(I18nError) as ctx:
                sm.load_profiles(tmp)
            self.assertEqual(ctx.exception.reason, "standard_profile_parse_failed")
            self.assertEqual(ctx.exception.details.get("path"), str(bad))
            self.assertTrue(bad.is_file())

    def test_na_fields_contradiction_raises_instead_of_letting_two_truths_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zz-liar.json"
            payload = _fixture_profile()
            payload["na_fields"] = []  # 与空值字段矛盾：两套话，必须报错
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(I18nError) as ctx:
                sm.load_profiles(tmp)
            self.assertEqual(ctx.exception.reason, "na_fields_mismatch")
            self.assertEqual(ctx.exception.details.get("na_fields"), [])
            self.assertIn("projection_angle", ctx.exception.details.get("null_fields") or [])


class GetProfileTest(unittest.TestCase):
    def test_every_field_matches_the_raw_json(self) -> None:
        for standard_id in sm.STANDARD_IDS:
            raw = _raw(standard_id)
            profile = sm.get_profile(standard_id)
            for field in sm.PROFILE_FIELDS:
                self.assertEqual(profile.fields.get(field), raw.get(field), f"{standard_id}.{field}")
            self.assertEqual(set(profile.na_fields), set(raw.get("na_fields") or []))
            self.assertEqual(profile.source, raw.get("source"))

    def test_na_fields_are_exactly_the_null_valued_contract_fields(self) -> None:
        for standard_id in sm.STANDARD_IDS:
            profile = sm.get_profile(standard_id)
            nulls = {key for key in sm.PROFILE_FIELDS if profile.fields.get(key) is None}
            self.assertEqual(set(profile.na_fields), nulls, standard_id)
            self.assertEqual(len(profile.na_fields), len(nulls), standard_id)
            for field in profile.na_fields:
                self.assertTrue(profile.is_na(field), f"{standard_id}.{field}")

    def test_unknown_id_raises_and_lists_what_is_known(self) -> None:
        with self.assertRaises(I18nError) as ctx:
            sm.get_profile("no-such-standard")
        self.assertEqual(ctx.exception.reason, "unknown_standard")
        self.assertEqual(sorted(ctx.exception.details.get("known") or []), sorted(sm.STANDARD_IDS))

    def test_profiles_are_read_only(self) -> None:
        profile = sm.get_profile(sm.STANDARD_IDS[0])
        with self.assertRaises(Exception):
            profile.standard_id = "mutated"  # type: ignore[misc]
        self.assertNotEqual(sm.get_profile(sm.STANDARD_IDS[0]).standard_id, "mutated")


class ProjectionAngleTest(unittest.TestCase):
    def test_value_is_propagated_from_data_including_not_fetched(self) -> None:
        seen_na = 0
        for standard_id in sm.STANDARD_IDS:
            expected = _raw(standard_id).get("projection_angle")
            value = sm.projection_angle_for(standard_id)
            self.assertEqual(value, expected, standard_id)
            if expected is None:
                seen_na += 1
                self.assertIn("projection_angle", sm.get_profile(standard_id).na_fields)
            else:
                self.assertIn(value, sm.PROJECTION_ANGLES)
                self.assertNotIn("projection_angle", sm.get_profile(standard_id).na_fields)
        self.assertGreaterEqual(seen_na, 1)  # 尺子必须真见到"未核 ⇒ None"，否则这条断言是空转

    def test_unfetched_angle_returns_none_never_a_guess(self) -> None:
        unfetched = [standard_id for standard_id in sm.STANDARD_IDS
                     if sm.get_profile(standard_id).is_na("projection_angle")]
        self.assertTrue(unfetched)
        for standard_id in unfetched:
            self.assertIsNone(sm.projection_angle_for(standard_id))
            self.assertIsNone(_raw(standard_id).get("projection_angle"))

    def test_unknown_id_raises_rather_than_sharing_the_none_shape(self) -> None:
        with self.assertRaises(I18nError) as ctx:
            sm.projection_angle_for("no-such-standard")
        self.assertEqual(ctx.exception.reason, "unknown_standard")

    def test_angle_literals_never_appear_in_the_lookup_body(self) -> None:
        source = _SOURCE_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        def angle_literals(node: ast.AST) -> list[str]:
            hits = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and sub.value in sm.PROJECTION_ANGLES:
                    hits.append(str(sub.value))
            return hits

        target = [node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "projection_angle_for"]
        self.assertEqual(len(target), 1)
        body_hits: list[str] = []
        for statement in target[0].body:  # 只看函数体：返回注解里的枚举名是契约，不算"写死"
            body_hits.extend(angle_literals(statement))
        self.assertEqual(body_hits, [])
        # 阳性对照：同一把尺子在一个人为写死的函数上必须报红。
        probe = ast.parse("def fake(s):\n    return 'third'\n").body[0]
        self.assertEqual(angle_literals(probe), ["third"])


class DefaultProfileForLocaleTest(unittest.TestCase):
    def test_mapping_comes_from_the_i18n_data_files(self) -> None:
        manifest = json.loads(
            (_PKG_ROOT / "cadcore" / "data" / "i18n" / "languages.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest)
        for entry in manifest:
            declared = entry.get("default_standard")
            self.assertIn(declared, sm.STANDARD_IDS)
            self.assertEqual(sm.default_profile_for_locale(entry["code"]), declared)
            self.assertEqual(sm.resolve_default_profile(entry["code"])["grade"], "exact")

    def test_region_subtag_falls_back_and_script_only_code_uses_primary_subtag(self) -> None:
        base = sm.default_profile_for_locale("ja")
        self.assertEqual(sm.default_profile_for_locale("ja-JP"), base)
        self.assertEqual(sm.resolve_default_profile("ja-JP")["grade"], "truncated")
        detail = sm.resolve_default_profile("zh-Hans-CN")
        self.assertIn(detail["grade"], {"primary_subtag", "truncated"})
        self.assertIn(detail["standard_id"], sm.STANDARD_IDS)
        self.assertEqual(detail["standard_id"], sm.default_profile_for_locale("zh-CN"))

    def test_unknown_locale_falls_back_to_a_real_profile_and_records_the_grade(self) -> None:
        detail = sm.resolve_default_profile("qq")
        self.assertEqual(detail["grade"], "unknown_locale")
        self.assertIn(detail["standard_id"], sm.STANDARD_IDS)
        self.assertEqual(sm.default_profile_for_locale("qq"), sm.STANDARD_IDS[0])
        self.assertEqual(sm.resolve_default_profile("")["grade"], "empty_code")

    def test_ambiguous_primary_subtag_falls_back_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            i18n_dir = Path(tmp) / "i18n"
            i18n_dir.mkdir()
            (i18n_dir / "languages.json").write_text(json.dumps([
                {"code": "zz-ZZ", "default_standard": sm.STANDARD_IDS[0]},
                {"code": "zz-YY", "default_standard": sm.STANDARD_IDS[-1]},
            ]), encoding="utf-8")
            self.assertTrue((i18n_dir / "languages.json").is_file())
            with mock.patch.object(sm, "_DATA_DIR", Path(tmp)):
                detail = sm.resolve_default_profile("zz-XX")
                self.assertEqual(detail["grade"], "ambiguous_primary_subtag")
                self.assertEqual(detail["standard_id"], sm.STANDARD_IDS[0])
                self.assertEqual(sm.default_profile_for_locale("zz-ZZ"), sm.STANDARD_IDS[0])

    def test_declared_standard_missing_on_disk_falls_back_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            i18n_dir = Path(tmp) / "i18n"
            i18n_dir.mkdir()
            (i18n_dir / "languages.json").write_text(json.dumps([
                {"code": "zz-ZZ", "default_standard": "zz-not-on-disk"},
            ]), encoding="utf-8")
            with mock.patch.object(sm, "_DATA_DIR", Path(tmp)):
                detail = sm.resolve_default_profile("zz-ZZ")
                self.assertEqual(detail["grade"], "unknown_declared_standard")
                self.assertEqual(detail["declared"], "zz-not-on-disk")
                self.assertIn(detail["standard_id"], sm.STANDARD_IDS)

    def test_missing_i18n_dir_is_not_silent_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sm, "_DATA_DIR", Path(tmp)):
                detail = sm.resolve_default_profile("zh-CN")
                self.assertEqual(detail["grade"], "unknown_locale")
                self.assertIn(detail["standard_id"], sm.STANDARD_IDS)

    def test_result_is_orthogonal_to_the_ui_language(self) -> None:
        expected = sm.default_profile_for_locale("zh-CN")
        with mock.patch.object(cat, "active_locale", return_value="ja"):
            self.assertEqual(sm.default_profile_for_locale("zh-CN"), expected)
        tree = ast.parse(_SOURCE_FILE.read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) for alias in node.names}
        imported |= {alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.Import) for alias in node.names}
        self.assertNotIn("catalog", imported)
        self.assertNotIn("cadcore.i18n.catalog", imported)


class SingleSourceOfTruthTest(unittest.TestCase):
    def test_a17_and_sheet_agree_on_every_standard(self) -> None:
        diffs, sources = _compare_keys(sm.STANDARD_IDS)
        self.assertEqual(diffs, [])
        self.assertEqual(tuple(sh.standard_ids()), tuple(sm.STANDARD_IDS))
        for standard_id, source in zip(sm.STANDARD_IDS, sources):
            self.assertEqual(source, "cadcore.i18n.standards.get_profile", standard_id)

    def test_comparison_ruler_sees_a_doctored_profile(self) -> None:
        standard_id = next(item for item in sm.STANDARD_IDS if sm.get_profile(item).na_fields)
        mine, theirs = _profile_pair(standard_id)
        self.assertEqual(set(mine["na_fields"]), set(theirs["na_fields"]))
        doctored = dict(theirs)
        doctored["projection_angle"] = "third" if theirs.get("projection_angle") != "third" else "first"
        self.assertNotEqual(mine.get("projection_angle"), doctored.get("projection_angle"))
        doctored_na = dict(theirs)
        doctored_na["na_fields"] = list(theirs.get("na_fields") or [])[:-1]
        self.assertNotEqual(set(mine["na_fields"]), set(doctored_na["na_fields"]))


class AuditAndPurityTest(unittest.TestCase):
    def test_audit_drift_reports_pass_and_not_measured(self) -> None:
        report = sm.audit_drift()
        self.assertEqual(report["verdict"], "PASS", report)
        self.assertEqual(report["profiles"], len(sm.STANDARD_IDS))
        self.assertEqual(report["missing_on_disk"], 0)
        self.assertEqual(report["undeclared_on_disk"], 0)
        self.assertEqual(report["na_fields_mismatch"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            na = sm.audit_drift(Path(tmp) / "nope")
            self.assertEqual(na["verdict"], "NA")
            self.assertEqual(na["profiles"], -1)
            self.assertEqual(na["missing_on_disk"], -1)

    def test_product_source_has_no_standard_branch_and_no_localized_literal(self) -> None:
        tree = ast.parse(_SOURCE_FILE.read_text(encoding="utf-8"))
        ids = set(sm.STANDARD_IDS)

        def standard_branches(node: ast.AST) -> list[str]:
            hits = []
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Compare):
                    continue
                for side in [sub.left, *sub.comparators]:
                    if isinstance(side, ast.Constant) and side.value in ids:
                        hits.append(str(side.value))
            return hits

        def cjk_literals(node: ast.AST) -> list[str]:
            docstrings = set()
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    body = getattr(sub, "body", [])
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        docstrings.add(id(body[0].value))
            return [sub.value for sub in ast.walk(node)
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                    and id(sub) not in docstrings
                    and any("\u4e00" <= char <= "\u9fff" for char in sub.value)]

        self.assertEqual(standard_branches(tree), [])
        self.assertEqual(cjk_literals(tree), [])
        # 阳性对照：两把尺子在人造违规上都必须报红（否则上面的 0 是空转的 0）。
        probe = ast.parse("def f(standard):\n"
                          f"    if standard == {sm.STANDARD_IDS[0]!r}:\n"
                          "        return '中'\n"
                          "    return 0\n").body[0]
        self.assertEqual(standard_branches(probe), [sm.STANDARD_IDS[0]])
        self.assertEqual(cjk_literals(probe), ["中"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
