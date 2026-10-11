"""tests.test_b6_style_consumption —— B6 样式/标注面缺陷的回归尺子（RC-S1 / RC-S2 / RC-S3 / RC-S4）。

这四条缺陷的**共同形状**是"操作报成功、产物没变"（phantom），所以本文件的尺子必须成对：
既测"该绿时绿"（样式真进产物），也测"该红时红"（尺子看得见真的丢文本 / 真的零消费）。

被钉住的行为
------------
1. **RC-S2（phantom_annotation_text_dropped）**：`ops.annotation.add_text/add_mtext(literal=True)`
   把字面量落在 `args["text"]`，`/api/open` 口径落在**顶层** `text` —— 两种写侧形态读侧都必须认。
   钉子：两条链路（`draw.sheet.annotation_texts` 与 `io.dxf_bridge.annotation_text_lines`）
   都要出文本、DXF 字节里要能 grep 到；且**阴性对照**证明"没文本"时确实不出条目。
2. **RC-S1（stale_style_readback）**：同名样式重定义后回读必须是**生效的那条**（`is_current`）。
   钉子 + 阴性对照：只定义一次时回的仍是那一条（不是"永远回最后一条"）。
3. **RC-S3（phantom_style_no_effect）**：`dim_style(decimals=…)` 必须改到图面尺寸文字，
   `dim_override(override_text=…)` 必须逐字进 DXF；覆盖不全的替代**不许静默**（要留 note）。
4. **RC-S4（roundtrip_false_diagnostic）**：自家样式的六张表落 L1 后 save→open→save
   **不许多出 `unknown_field_preserved`**、必须逐字节稳定。
5. **静态消费面**：产物侧模块必须真的引用样式字段（防"改回零消费者"）。

夹具自证
--------
`_style_fixture()` 断言自己造出来的文档**确实有**一条尺寸注解 + 一条 literal 文本注解，
并且 `add_text` 写的字面量**在 `args` 里、不在顶层** —— 否则下面测的就不是 RC-S2 那个形状。
所有落盘都在 `tempfile` 临时目录里，不碰仓库内任何别人的文件。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore import docmodel  # noqa: E402
from cadcore.draw import sheet as dsheet  # noqa: E402
from cadcore.io import dxf_bridge  # noqa: E402
from cadcore.ops import annotation as ann  # noqa: E402
from cadcore.sketch import primitives  # noqa: E402

MM = 1_000_000
LITERAL = "CTRL-TEXT-9F3A"
TOP_LEVEL = "CTRL-TOP-9F3A"
OVERRIDE = "OVR-S07"


def _dict_doc() -> dict:
    sketch: dict = {"uuid": "44444444-4444-4444-4444-444444444444", "plane": "XY",
                    "entities": [], "constraints": [], "dimensions": []}
    line = primitives.add_line(sketch, p1=(0, 0), p2=(40 * MM, 0))
    doc: dict = {"schema_version": 1, "unit": "mm", "standard_profile": "gbt",
                 "sketches": [sketch], "features": [], "annotations": [], "sheets": [],
                 "meta": {"doc_uuid": "55555555-5555-5555-5555-555555555555"}}
    dim = ann.add_dimension(doc, target=[line, "mid"], kind="linear", value_nm=40 * MM)[0]
    text = ann.add_text(doc, at_nm=(0, 0), literal=True, text=LITERAL)[0]
    return {"model": doc, "line": line, "dim": dim, "text": text}


def _style_fixture() -> dict:
    """夹具自证：真的有一条尺寸注解 + 一条 literal 文本注解，且字面量在 `args` 里。"""
    ctx = _dict_doc()
    nodes = {node.get("uuid"): node for node in ctx["model"]["annotations"]}
    text_node = nodes[ctx["text"]]
    assert text_node.get("kind") == "text", text_node
    assert text_node.get("literal") is True, text_node
    assert text_node.get("args", {}).get("text") == LITERAL, text_node
    assert "text" not in text_node, "RC-S2 的形状就是'顶层没有 text'：%r" % (text_node,)
    assert nodes[ctx["dim"]].get("kind") == "linear", nodes[ctx["dim"]]
    return ctx


def _two_view_doc() -> dict:
    """一张真三视图图纸（box 60×40×20 拉伸）—— 样式要作用到**派生**尺寸上。"""
    from cadcore.sketch import commands as sketch_commands

    doc = docmodel.new_document()
    sketch: dict = {"uuid": "22222222-2222-2222-2222-222222222222", "plane": "XY",
                    "entities": [], "constraints": [], "dimensions": []}
    primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=(60 * MM, 40 * MM))
    sketch_commands.add_circle(sketch, center=(30 * MM, 20 * MM), radius_nm=8 * MM)
    doc.sketches = [sketch]
    doc.features = [{"uuid": "33333333-3333-3333-3333-333333333333", "kind": "extrude",
                     "inputs": [sketch["uuid"]],
                     "params": {"distance_nm": 20 * MM, "symmetric": False, "op": "new"},
                     "suppressed": False}]
    payload = json.loads(docmodel.document_to_l1(doc))
    line = str(payload["sketches"][0]["entities"][0]["uuid"])
    dim = ann.add_dimension(payload, target=[line, "mid"], kind="linear", value_nm=60 * MM)[0]
    return {"model": payload, "line": line, "dim": dim}


# ======================================================================================
# RC-S2：标注文本真源（两种写侧形态都要进产物）
# ======================================================================================


class AnnotationTextSourceTests(unittest.TestCase):
    """写侧发得出去的两种形态，读侧必须都认（`docmodel.annotation_text` 是唯一读法）。"""

    def test_args_text_literal_reaches_sheet_and_dxf(self) -> None:
        ctx = _style_fixture()
        payload = ctx["model"]
        sheet = dsheet.make_sheet(payload, standard="gbt", language="en")
        texts = dsheet.annotation_texts(sheet)
        self.assertIn(LITERAL, texts, "add_text(literal=True) 的 args.text 必须进图纸文本清单")
        with tempfile.TemporaryDirectory() as tmp:
            path = dsheet.export_sheet(sheet, fmt="dxf", out=os.path.join(tmp, "s05.dxf"))
            data = Path(path).read_bytes()
        self.assertIn(LITERAL.encode(), data, "args.text 必须逐字进 DXF 字节")

    def test_top_level_text_still_reaches_sheet_and_dxf(self) -> None:
        """阳性对照：`/api/open` 口径（顶层 `literal` + 顶层 `text`）照旧要出字。"""
        payload = _style_fixture()["model"]
        payload["annotations"].append({"uuid": "66666666-6666-6666-6666-666666666666",
                                       "kind": "text", "literal": True, "text": TOP_LEVEL})
        sheet = dsheet.make_sheet(payload, standard="gbt", language="en")
        self.assertIn(TOP_LEVEL, dsheet.annotation_texts(sheet))
        with tempfile.TemporaryDirectory() as tmp:
            path = dsheet.export_sheet(sheet, fmt="dxf", out=os.path.join(tmp, "s06.dxf"))
            data = Path(path).read_bytes()
        self.assertIn(TOP_LEVEL.encode(), data)

    def test_text_key_annotations_still_export_the_key(self) -> None:
        """非字面量注记回落 `text_key`（真源存的就是 key），别被字面量口径吃掉。"""
        payload = _style_fixture()["model"]
        payload["annotations"].append({"uuid": "77777777-7777-7777-7777-777777777777",
                                       "kind": "text", "literal": False,
                                       "text_key": "cmd.note.demo"})
        self.assertIn("cmd.note.demo", dsheet.annotation_texts(
            dsheet.make_sheet(payload, standard="gbt", language="en")))

    def test_negative_control_annotation_without_text_is_not_invented(self) -> None:
        """尺子看得见红：既无字面量也无 text_key 的注解**不许**被编一个空条目出来。"""
        payload = _style_fixture()["model"]
        payload["annotations"].append({"uuid": "88888888-8888-8888-8888-888888888888",
                                       "kind": "text", "literal": True})
        sheet = dsheet.make_sheet(payload, standard="gbt", language="en")
        texts = dsheet.annotation_texts(sheet)
        self.assertIn(LITERAL, texts)
        self.assertNotIn("", texts, "没有文本的注解不许变成空串条目")

    def test_dxf_bridge_uses_the_same_reader(self) -> None:
        """两条链路（图纸 / 草图导出）必须给出同一份文本 —— 禁止各解一遍。"""
        payload = _style_fixture()["model"]
        lines = dxf_bridge.annotation_text_lines(payload["annotations"])
        self.assertTrue(any(LITERAL in line for line in lines), lines)
        sheet = dsheet.make_sheet(payload, standard="gbt", language="en")
        self.assertEqual([line.rsplit("text: ", 1)[-1] for line in lines],
                         dsheet.annotation_texts(sheet))

    def test_mutation_control_reader_sees_a_dropped_text(self) -> None:
        """变异体反证：把真源文本挪走（模拟"写侧发得出去、读侧不认"），尺子必须报红。"""
        payload = _style_fixture()["model"]
        target = next(item for item in payload["annotations"] if item.get("kind") == "text")
        target["args"].pop("text")
        sheet = dsheet.make_sheet(payload, standard="gbt", language="en")
        self.assertNotIn(LITERAL, dsheet.annotation_texts(sheet),
                         "变异体下尺子必须看得见'文本没了'，否则本文件的绿是假绿")


# ======================================================================================
# RC-S1：同名样式重定义 ⇒ 回读生效的那条
# ======================================================================================


class StyleReadbackTests(unittest.TestCase):
    def test_same_name_redefine_reads_back_the_current_one(self) -> None:
        ctx = _style_fixture()
        ann.dim_style(ctx["model"], name="S1", height_nm=9 * MM, decimals=6)
        ann.dim_style(ctx["model"], name="S1", height_nm=1 * MM, decimals=1)
        got = ann.dim_style_of(ctx["model"], subject=ctx["dim"])
        self.assertIsNotNone(got)
        self.assertEqual(got["height_nm"], 1 * MM, "同名是覆盖：必须回读新定义")
        self.assertEqual(got["decimals"], 1)
        self.assertTrue(got["is_current"])
        self.assertEqual(len(ctx["model"]["dim_styles"]), 2, "历史记录仍要留着（可追溯）")

    def test_single_definition_still_reads_back(self) -> None:
        """阴性对照：不是"永远回最后一条" —— 只有一条时就得回那一条。"""
        ctx = _style_fixture()
        ann.dim_style(ctx["model"], name="S1", decimals=4)
        got = ann.dim_style_of(ctx["model"], subject=ctx["dim"])
        self.assertEqual(got["decimals"], 4)

    def test_other_name_definition_does_not_hide_the_first(self) -> None:
        """异名是切当前：定义了 S2 之后，S1 仍要按名字回读得到自己的定义。"""
        ctx = _style_fixture()
        ann.dim_style(ctx["model"], name="S1", decimals=6)
        ann.dim_style(ctx["model"], name="S2", decimals=1)
        self.assertEqual(ann.dim_style_of(ctx["model"], subject=ctx["dim"])["decimals"], 6)

    def test_unknown_name_returns_none(self) -> None:
        ctx = _style_fixture()
        self.assertIsNone(ann.style_record(ctx["model"], bucket="dim_styles", name="NOPE"))


# ======================================================================================
# RC-S3：样式必须真进产物
# ======================================================================================


class StyleReachesArtifactTests(unittest.TestCase):
    def _compose(self, payload: dict) -> tuple:
        from cadcore.ops import sw_drawings as draw

        draw.compose_sheet(payload, kernel=None, standard="gbt", dimensions=True, min_count=2,
                           bom=False, title_block=False, isometric=False)
        sheet = draw._sheet_objects(payload)[-1]
        return sheet, [dim.text for dim in sheet.dimensions]

    def test_decimals_change_dimension_text(self) -> None:
        ctx = _two_view_doc()
        _sheet, before = self._compose(json.loads(json.dumps(ctx["model"])))
        self.assertTrue(before)
        self.assertTrue(all(text.endswith("00") and "." in text for text in before), before)
        styled = json.loads(json.dumps(ctx["model"]))
        ann.dim_style(styled, name="S1", decimals=6)
        _sheet_b, after = self._compose(styled)
        self.assertNotEqual(before, after, "decimals 2→6 必须改到图面尺寸文字")
        self.assertTrue(all(text.count(".") == 1 and len(text.split(".")[1]) == 6 for text in after),
                        after)

    def test_document_wide_override_text_reaches_dxf_bytes(self) -> None:
        styled = _two_view_doc()
        model = json.loads(json.dumps(styled["model"]))
        ann.dim_override(model, subject=styled["dim"], override_text=OVERRIDE)
        sheet, texts = self._compose(model)
        self.assertTrue(all(text == OVERRIDE for text in texts), texts)
        with tempfile.TemporaryDirectory() as tmp:
            path = dsheet.export_sheet(sheet, fmt="dxf", out=os.path.join(tmp, "ovr.dxf"))
            data = Path(path).read_bytes()
        self.assertIn(OVERRIDE.encode(), data, "替代文本必须逐字进 DXF 字节")

    def test_partial_override_is_not_silent(self) -> None:
        """覆盖不全的替代只在数据层生效，但**必须留痕**（不许静默少消费）。"""
        ctx = _style_fixture()
        model = json.loads(json.dumps(ctx["model"]))
        ann.add_dimension(model, target=[ctx["line"], "mid"], kind="linear", value_nm=40 * MM)
        ann.dim_override(model, subject=ctx["dim"], override_text="PARTIAL-1")
        sheet = dsheet.make_sheet(model, standard="gbt", language="en")
        style = dsheet.dimension_style(sheet)
        self.assertEqual(style.unmapped_records, 1,
                         "两条尺寸注解只覆盖一条 ⇒ 必须计入 unmapped，不许静默")
        self.assertEqual(style.text, "", "覆盖不全时不许把替代文本铺到整张图上")

    def test_no_style_means_no_style_sources(self) -> None:
        """三态可辨：文档没有样式 ⇒ `sources` 为空、`style_name` 为 NA，而不是编一个缺省样式。"""
        sheet = dsheet.make_sheet(_style_fixture()["model"], standard="gbt", language="en")
        style = dsheet.dimension_style(sheet)
        self.assertEqual(style.sources, ())
        self.assertEqual(style.style_name, "")
        self.assertEqual(style.decimals, dsheet.DIM_TEXT_DECIMALS)
        self.assertEqual(dsheet.sheet_facts(sheet)["dim_style_name"], dsheet.NA)


# ======================================================================================
# RC-S4：样式表是**自家扩展键**，save→open 不许报伪诊断
# ======================================================================================


class StyleRoundTripTests(unittest.TestCase):
    def test_style_tables_roundtrip_without_false_diagnostic(self) -> None:
        ctx = _style_fixture()
        ann.dim_style(ctx["model"], name="S1", decimals=6)
        ann.dim_override(ctx["model"], subject=ctx["dim"], override_text="OVR-RT")
        ann.dim_edit(ctx["model"], subject=ctx["dim"], override_text="EDIT-RT")
        ann.text_style(ctx["model"], name="T1")
        ann.mleader_style(ctx["model"], name="M1")
        ann.table_style(ctx["model"], name="TB1")
        first = docmodel.document_to_l1(docmodel.document_from_l1(json.dumps(ctx["model"])))
        loaded = docmodel.document_from_l1(first)
        codes = {str(item.get("code")) for item in (loaded.diagnostics or [])}
        self.assertNotIn("unknown_field_preserved", codes,
                         "自家写出的样式表不许被读侧判成未知字段：%s" % sorted(codes))
        self.assertEqual(docmodel.document_to_l1(loaded), first, "save→open→save 必须逐字节稳定")
        for key in ("dim_styles", "dim_overrides", "dim_edits", "text_styles",
                    "mleader_styles", "table_styles"):
            self.assertIn(key, docmodel.PRODUCT_EXTENSION_KEYS["root"],
                          "%s 必须登记为自家扩展键（否则伪诊断会逐轮累积）" % key)

    def test_unknown_root_field_is_still_reported(self) -> None:
        """阴性对照：登记表是精确匹配，真未知字段照旧报诊断（尺子没被放宽）。"""
        ctx = _style_fixture()
        ctx["model"]["definitely_not_ours"] = {"x": 1}
        loaded = docmodel.document_from_l1(json.dumps(ctx["model"]))
        codes = {str(item.get("code")) for item in (loaded.diagnostics or [])}
        self.assertIn("unknown_field_preserved", codes)


# ======================================================================================
# 静态消费面：产物侧模块必须真的引用样式字段（RC-S3 的静态那一半）
# ======================================================================================


class StyleConsumerScanTests(unittest.TestCase):
    TOKENS = ("dim_styles", "dim_overrides", "dim_edits", "text_styles", "mleader_styles",
              "table_styles", "annotation_scales", "text_override", "text_pos_nm",
              "arrow_pos_nm", "dim_style")

    def test_artifact_modules_reference_style_fields(self) -> None:
        """`draw/sheet.py` 必须真的读样式表（改回零消费者 ⇒ 本断言报红）。"""
        import re

        path = _PKG_ROOT / "cadcore" / "draw" / "sheet.py"
        text = path.read_text(encoding="utf-8")
        hits = {token: len(re.findall(r"(?<![\w.])%s(?![\w])" % re.escape(token), text))
                for token in self.TOKENS}
        hits = {key: value for key, value in hits.items() if value}
        self.assertGreaterEqual(sum(hits.values()), 1,
                                "产物链路必须引用标注样式字段，实测 %r" % (hits,))
        self.assertIn("dim_styles", hits)

    def test_scan_ruler_sees_red_on_a_mutation(self) -> None:
        """变异体反证：把尺子拿去扫一段**不含**样式字段的文本，必须得 0（尺子看得见红）。"""
        import re

        text = "def add_view(sheet, shape):\n    return None\n"
        total = sum(len(re.findall(r"(?<![\w.])%s(?![\w])" % re.escape(token), text))
                    for token in self.TOKENS)
        self.assertEqual(total, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
