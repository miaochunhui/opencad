"""test_output_extra —— 族 `output_extra` 的独立单测（真件直连，零 skip、零放宽）。

被测真件：`cadcore/ops/output_extra.py`（打印样式映射表 + SVG/PDF 文本导出）。
本文件**不**做"缺席即跳过"的宽容：真件 import 失败即真报错（那正是要看见的红）。

断言打在哪（照实写）
--------------------
* **返回值契约**：每个操作返回 `list[str]`、元素是文档里**可解引用**的 uuid；契约面（族名 /
  `OPERATIONS` / 第一个位置参名 / 其余全关键字）用 `inspect` 硬断言。
* **样式映射语义**：最具体优先（真彩 > ACI > 命名色 > by_layer > `*`）、批量写**全量校验后落库**
  （坏条目一条都不写）、删除未知键必须报 `unknown_style_mapping`。
* **导出后果**：SVG 的毫米坐标从模型纳米现算（断言 `x1/y1/x2/y2` 数值）、`stroke-width`/虚线/
  淡显/`plot=False` 由映射表驱动；PDF 断言 `MediaBox`、内容流算子、以及**回读 xref 偏移逐条指向
  `N 0 obj`**；落盘字节与 `render_*_text` 逐字节相同、sha256 与磁盘一致。
* **不改模型**：全部导出跑完后 `sketches` 逐字节不变。
* **文本确定性**：同一输入两次渲染逐字节相同。
* **自检判定行**：子进程裸跑族模块，stdout 必须出现 `EXTRA_OPS verdict=PASS ops=10`。

中文只出现在 docstring/注释里（非 docstring 字符串字面量一律 ASCII）。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import subprocess
import sys
import tempfile
import unittest
import uuid as _uuid
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:  # 允许直接 `python3 -m unittest tests.test_output_extra`
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import output_extra as oe  # noqa: E402

MM = oe.NM_PER_MM
_MODULE_PATH = Path(oe.__file__)


def _line(index: int, x1: int, y1: int, x2: int, y2: int, layer: str = "0") -> dict:
    return {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "output-extra-test-line-%d" % index)),
            "kind": "line", "layer": layer, "construction": False,
            "geom": {"x1": x1 * MM, "y1": y1 * MM, "x2": x2 * MM, "y2": y2 * MM}}


def _doc(entities=None, layers=None) -> dict:
    """L1 文档映射（含 `sketches` 键）：本族三种载体形态之一。"""
    if entities is None:
        entities = [_line(0, 0, 0, 100, 0), _line(1, 100, 0, 100, 50),
                    _line(2, 100, 50, 0, 50), _line(3, 0, 50, 0, 0),
                    {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "output-extra-test-circle")),
                     "kind": "circle", "layer": "holes", "construction": False,
                     "geom": {"cx": 50 * MM, "cy": 25 * MM, "r": 10 * MM}}]
    out = {"schema_version": 1, "unit": "mm", "standard_profile": "iso",
           "sketches": [{"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "output-extra-test-sketch")),
                         "plane": "XY", "entities": entities, "constraints": [],
                         "dimensions": []}]}
    out["layers"] = layers if layers is not None else {"0": {"color": "black"},
                                                       "holes": {"color": "red"}}
    return out


def _payload(doc) -> dict:
    return json.loads(json.dumps(doc, default=str))


def _namespace(doc) -> dict:
    return _payload(doc).get("output_extra") or {}


def _uuids(node) -> set:
    found: set = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).endswith("uuid") and isinstance(value, str):
                found.add(value)
            found |= _uuids(value)
    elif isinstance(node, list):
        for item in node:
            found |= _uuids(item)
    return found


def _export_record(doc, export_uuid: str) -> dict:
    for record in _namespace(doc).get("exports") or []:
        if record.get("uuid") == export_uuid:
            return record
    raise AssertionError("export record %s is missing from the payload" % export_uuid)


def _table(doc, name: str = "mono.ctb", **kwargs) -> str:
    return oe.style_table_create(doc, name=name, **kwargs)[0]


class TestFamilyContract(unittest.TestCase):
    def test_family_and_operations_shape(self):
        self.assertEqual(oe.FAMILY, "output_extra")
        self.assertIsInstance(oe.OPERATIONS, dict)
        self.assertEqual(len(oe.OPERATIONS), 10)
        for name, func in oe.OPERATIONS.items():
            self.assertTrue(callable(func), name)
            self.assertIs(getattr(oe, name), func, name)

    def test_first_arg_is_sk_or_doc_rest_keyword_only(self):
        for name, func in oe.OPERATIONS.items():
            parameters = list(inspect.signature(func).parameters.values())
            self.assertEqual(parameters[0].name, "sk_or_doc", name)
            self.assertEqual(parameters[0].kind, inspect.Parameter.POSITIONAL_OR_KEYWORD, name)
            for extra in parameters[1:]:
                self.assertEqual(extra.kind, inspect.Parameter.KEYWORD_ONLY, name)

    def test_module_facts_and_returns_are_resolvable_uuids(self):
        doc = _doc()
        collected: list = []
        collected += oe.style_table_create(doc, name="mono.ctb")
        collected += oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)
        collected += oe.style_map_bulk(doc, table="mono.ctb",
                                       mappings=[{"color": "red", "lineweight_mm": 0.5}])
        collected += oe.style_map_lookup(doc, table="mono.ctb", color="red")
        collected += oe.style_map_apply(doc, table="mono.ctb")
        collected += oe.export_svg_text(doc, style_table="mono.ctb")
        collected += oe.export_pdf_text(doc, style_table="mono.ctb")
        collected += oe.export_manifest(doc)
        collected += oe.style_table_list(doc)
        for value in collected:
            self.assertIsInstance(value, str)
            self.assertRegex(value, r"^[0-9a-f-]{36}$")
        known = _uuids(_payload(doc))
        missing = [value for value in collected if value not in known]
        self.assertEqual(missing, [], "returned uuids must resolve inside the document payload")
        removed = oe.style_map_delete(doc, table="mono.ctb", color="red")
        self.assertIn(removed[0], collected)
        self.assertIn(removed[0], _uuids(_payload(doc)),
                      "deleted mapping keeps a tombstone: the returned uuid must still resolve")
        facts = oe.module_facts(doc)
        self.assertEqual(facts["operations"], sorted(oe.OPERATIONS))
        self.assertEqual(facts["ops_count"], 10)
        self.assertEqual(facts["style_tables"], 1)
        self.assertEqual(facts["style_mappings"], 1)
        self.assertEqual(facts["exports"], 2)

    def test_read_ops_on_empty_document_are_genuine_zero(self):
        doc = _doc()
        self.assertEqual(oe.style_table_list(doc), [])
        self.assertEqual(oe.export_manifest(doc), [])
        self.assertNotIn("output_extra", _payload(doc),
                         "read-only ops must not create the bookkeeping namespace")


class TestStyleTableMapping(unittest.TestCase):
    def test_table_create_list_and_duplicate(self):
        doc = _doc()
        first = _table(doc, "mono.ctb")
        second = _table(doc, "labels.stb")
        self.assertEqual(oe.style_table_list(doc), [first, second])
        self.assertEqual(oe.style_table_list(doc, format="ctb"), [first])
        self.assertEqual(oe.style_table_list(doc, format="stb"), [second])
        with self.assertRaises(CadError) as caught:
            oe.style_table_create(doc, name="mono.ctb")
        self.assertEqual(caught.exception.reason, "duplicate_table")
        self.assertTrue(_namespace(doc)["style_tables"][1]["monochrome"] is False)

    def test_lookup_specificity_order(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        entries = oe.style_map_bulk(doc, table="mono.ctb", mappings=[
            {"color": "*", "lineweight_mm": 0.25},
            {"color": "by_layer", "lineweight_mm": 0.35},
            {"color": "red", "lineweight_mm": 0.5},
            {"color": 1, "lineweight_mm": 0.7},
            {"color": "#FF0000", "lineweight_mm": 0.9},
        ])
        winners = {key: oe.style_map_lookup(doc, table="mono.ctb", color=key)[0]
                   for key in ("red", 1, "#FF0000", "by_layer", "green")}
        self.assertEqual(winners["red"], entries[2])
        self.assertEqual(winners[1], entries[3])
        self.assertEqual(winners["#FF0000"], entries[4])
        self.assertEqual(winners["by_layer"], entries[1])
        self.assertEqual(winners["green"], entries[0], "no exact key must fall back to '*'")
        self.assertEqual(len(set(entries)), 5)

    def test_bulk_is_all_or_nothing(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        ok = oe.style_map_bulk(doc, table="mono.ctb",
                               mappings=[{"color": "*", "lineweight_mm": 0.25}])
        self.assertEqual(len(ok), 1)
        before = oe.module_facts(doc)["style_mappings"]
        with self.assertRaises(CadError) as caught:
            oe.style_map_bulk(doc, table="mono.ctb", mappings=[
                {"color": "red", "lineweight_mm": 0.5},
                {"color": "blue", "lineweight_mm": 0.6, "screening": 101},
            ])
        self.assertEqual(caught.exception.reason, "bad_screening")
        self.assertEqual(oe.module_facts(doc)["style_mappings"], before,
                         "a rejected batch must not write its valid prefix")
        with self.assertRaises(CadError) as caught:
            oe.style_map_bulk(doc, table="mono.ctb",
                              mappings=[{"color": "red", "lineweight": 0.5}])
        self.assertEqual(caught.exception.reason, "bad_mappings")

    def test_delete_unknown_key_is_loud(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)
        with self.assertRaises(CadError) as caught:
            oe.style_map_delete(doc, table="mono.ctb", color="#123456")
        self.assertEqual(caught.exception.reason, "unknown_style_mapping")
        self.assertEqual(caught.exception.details.get("color"), "true_color:#123456")

    def test_apply_accounts_per_colour(self):
        entities = [_line(0, 0, 0, 10, 0, layer="0"),
                    _line(1, 0, 0, 10, 10, layer="0"),
                    _line(2, 0, 0, 5, 5, layer="holes"),
                    {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "output-extra-test-aci")),
                     "kind": "circle", "layer": "0", "construction": False, "color": 5,
                     "geom": {"cx": 0, "cy": 0, "r": 2 * MM}}]
        doc = _doc(entities=entities)
        _table(doc, "mono.ctb")
        star = oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)[0]
        red = oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=0.5)[0]
        aci = oe.style_map_set(doc, table="mono.ctb", color=5, lineweight_mm=0.8)[0]
        applied = oe.style_map_apply(doc, table="mono.ctb")
        self.assertEqual(len(applied), 3)
        records = {record["color"]: record for record in _namespace(doc)["effective_styles"]}
        self.assertEqual(sorted(records), ["aci:5", "named:black", "named:red"])
        self.assertEqual(records["named:black"]["entry"], star)
        self.assertEqual(records["named:red"]["entry"], red)
        self.assertEqual(records["aci:5"]["entry"], aci)
        self.assertEqual(records["named:red"]["lineweight_mm"], 0.5)
        self.assertEqual(sum(record["entity_count"] for record in records.values()), 4)
        self.assertEqual(records["aci:5"]["rgb"], "#000000",
                         "monochrome table ink is black regardless of ACI")
        empty = _doc(entities=[])
        _table(empty, "mono.ctb")
        with self.assertRaises(CadError) as caught:
            oe.style_map_apply(empty, table="mono.ctb")
        self.assertEqual(caught.exception.reason, "no_model_geometry")


    def test_delete_leaves_tombstone_under_the_same_uuid(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        star = oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)[0]
        red = oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=0.5)[0]
        table_record = _namespace(doc)["style_tables"][0]
        revision = table_record["revision"]
        removed = oe.style_map_delete(doc, table="mono.ctb", color="red")
        self.assertEqual(removed, [red])
        self.assertIn(removed[0], _uuids(_payload(doc)),
                      "erasing the entry orphaned the returned uuid; a tombstone keeps it resolvable")
        table_record = _namespace(doc)["style_tables"][0]
        live = {entry["uuid"]: entry for entry in table_record["entries"]}
        self.assertEqual(sorted(live), [star], "the live entry list must drop the deleted mapping")
        self.assertEqual(live[star]["status"], "active")
        self.assertEqual(table_record["revision"], revision + 1, "a delete must bump the revision")
        tombstones = {entry["uuid"]: entry for entry in table_record["tombstones"]}
        self.assertIn(red, tombstones, "the tombstone must carry the deleted entry uuid")
        self.assertIs(tombstones[red]["deleted"], True)
        self.assertEqual(tombstones[red]["status"], "deleted")
        self.assertEqual(tombstones[red]["kind"], "plot style mapping tombstone")
        self.assertEqual(tombstones[red]["key"], "named:red")
        self.assertEqual(tombstones[red]["lineweight_mm"], 0.5,
                         "the tombstone keeps the deleted attributes for audit")

    def test_deleted_entry_is_out_of_the_semantic_face(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        star = oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)[0]
        red = oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=0.5)[0]
        self.assertEqual(oe.module_facts(doc)["style_mappings"], 2)
        oe.style_map_delete(doc, table="mono.ctb", color="red")
        self.assertEqual(oe.module_facts(doc)["style_mappings"], 1,
                         "a tombstone is not a live mapping")
        self.assertEqual(oe.style_map_lookup(doc, table="mono.ctb", color="red"), [star],
                         "a deleted exact key must fall back to '*', never resolve itself")
        self.assertEqual(oe.style_map_delete(doc, table="mono.ctb", color="*"), [star])
        with self.assertRaises(CadError) as caught:
            oe.style_map_lookup(doc, table="mono.ctb", color="red")
        self.assertEqual(caught.exception.reason, "no_style_mapping")
        self.assertEqual(oe.module_facts(doc)["style_mappings"], 0)
        known = _uuids(_payload(doc))
        self.assertIn(red, known)
        self.assertIn(star, known, "every tombstone stays resolvable after its delete")

    def test_repeat_delete_is_deterministic_and_set_revives(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        red = oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=0.5)[0]
        self.assertEqual(oe.style_map_delete(doc, table="mono.ctb", color="red"), [red])
        for _attempt in range(3):
            with self.assertRaises(CadError) as caught:
                oe.style_map_delete(doc, table="mono.ctb", color="red")
            self.assertEqual(caught.exception.reason, "unknown_style_mapping")
            self.assertEqual(caught.exception.details.get("color"), "named:red")
        again = oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=0.7)
        self.assertEqual(again, [red], "reviving a key keeps its deterministic uuid")
        entries = {entry["uuid"]: entry for entry in _namespace(doc)["style_tables"][0]["entries"]}
        self.assertEqual(len(entries), 1, "a revive must not leave a duplicate same-uuid record")
        self.assertIs(entries[red]["deleted"], False)
        self.assertEqual(entries[red]["status"], "active")
        self.assertEqual(entries[red]["lineweight_mm"], 0.7)
        self.assertEqual(oe.style_map_lookup(doc, table="mono.ctb", color="red"), [red])
        self.assertEqual(oe.style_map_delete(doc, table="mono.ctb", color="red"), [red])


class TestSvgExport(unittest.TestCase):
    def _svg_doc(self, name: str = "mono.ctb"):
        doc = _doc()
        _table(doc, name)
        oe.style_map_bulk(doc, table=name, mappings=[
            {"color": "*", "lineweight_mm": 0.25, "screening": 50},
            {"color": "red", "lineweight_mm": 0.5},
        ])
        return doc

    def test_geometry_and_stroke_from_mapping_table(self):
        doc = self._svg_doc()
        text = oe.render_svg_text(doc, style_table="mono.ctb", title="EXTRA")
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertIn('width="297mm" height="210mm" viewBox="0 0 297 210"', text)
        self.assertIn("<title>EXTRA</title>", text)
        lines = {tuple(float(value) for value in match)
                 for match in re.findall(
                     r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"', text)}
        self.assertEqual(lines, {(10.0, 200.0, 110.0, 200.0), (110.0, 200.0, 110.0, 150.0),
                                 (110.0, 150.0, 10.0, 150.0), (10.0, 150.0, 10.0, 200.0)})
        self.assertIn('<circle cx="60" cy="175" r="10"', text)
        self.assertIn('stroke="#000000" stroke-width="0.25" stroke-opacity="0.5"', text,
                      "the '*' mapping drives the wireframe style")
        self.assertIn('stroke-width="0.5"', text, "the red mapping drives the circle style")
        self.assertNotIn("nan", text.lower())

    def test_color_table_without_mono_uses_entry_rgb(self):
        doc = _doc()
        _table(doc, "color.ctb")
        oe.style_map_set(doc, table="color.ctb", color="*", lineweight_mm=0.4, rgb="#00FF00")
        text = oe.render_svg_text(doc, style_table="color.ctb")
        self.assertIn('stroke="#00FF00" stroke-width="0.4"', text)
        self.assertNotIn("stroke-opacity", text)

    def test_export_writes_real_bytes_and_records_digest(self):
        doc = self._svg_doc()
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "nested" / "sheet.svg")
            export_uuid = oe.export_svg_text(doc, out_path=target, style_table="mono.ctb",
                                             title="EXTRA")[0]
            on_disk = Path(target).read_bytes()
        expected = oe.render_svg_text(doc, style_table="mono.ctb", title="EXTRA").encode("utf-8")
        self.assertEqual(on_disk, expected)
        record = _export_record(doc, export_uuid)
        self.assertTrue(record["written"])
        self.assertTrue(record["bytes_match"])
        self.assertEqual(record["bytes"], len(expected))
        self.assertEqual(record["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(record["entity_count"], 5)
        self.assertEqual(record["scale"], "1:1")
        self.assertTrue(record["fits_on_paper"])

    def test_dry_run_does_not_claim_a_file(self):
        doc = self._svg_doc()
        export_uuid = oe.export_svg_text(doc, style_table="mono.ctb")[0]
        record = _export_record(doc, export_uuid)
        self.assertFalse(record["written"])
        self.assertEqual(record["path"], "")

    def test_not_plotted_entities_are_counted_not_silently_dropped(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        oe.style_map_bulk(doc, table="mono.ctb", mappings=[
            {"color": "*", "lineweight_mm": 0.25},
            {"color": "red", "lineweight_mm": 0.25, "plot": False},
        ])
        export_uuid = oe.export_svg_text(doc, style_table="mono.ctb")[0]
        record = _export_record(doc, export_uuid)
        self.assertEqual(record["entity_count"], 4)
        self.assertEqual(record["skipped_kinds"], {"not_plotted": 1})
        text = oe.render_svg_text(doc, style_table="mono.ctb")
        self.assertNotIn("<circle", text)
        hidden = _doc()
        _table(hidden, "mono.ctb")
        oe.style_map_set(hidden, table="mono.ctb", color="*", lineweight_mm=0.25, plot=False)
        with self.assertRaises(CadError) as caught:
            oe.export_svg_text(hidden, style_table="mono.ctb")
        self.assertEqual(caught.exception.reason, "no_model_geometry")
        self.assertEqual(caught.exception.details.get("skipped_kinds"), {"not_plotted": 5})

    def test_fit_and_overflow_are_measured(self):
        doc = self._svg_doc()
        fit_uuid = oe.export_svg_text(doc, style_table="mono.ctb", scale="fit")[0]
        fit = _export_record(doc, fit_uuid)
        self.assertEqual(fit["scale"], "fit")
        self.assertGreater(fit["factor"], 0.0)
        self.assertTrue(fit["fits_on_paper"])
        big_uuid = oe.export_svg_text(doc, style_table="mono.ctb", scale="10:1")[0]
        big = _export_record(doc, big_uuid)
        self.assertFalse(big["fits_on_paper"])
        self.assertGreater(big["overflow_mm"], 0.0)

    def test_render_is_byte_deterministic(self):
        doc = self._svg_doc()
        first = oe.render_svg_text(doc, style_table="mono.ctb", title="EXTRA", scale="fit")
        second = oe.render_svg_text(doc, style_table="mono.ctb", title="EXTRA", scale="fit")
        self.assertEqual(first, second)


class TestPdfExport(unittest.TestCase):
    def _pdf_doc(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        oe.style_map_bulk(doc, table="mono.ctb", mappings=[
            {"color": "*", "lineweight_mm": 0.35},
            {"color": "red", "lineweight_mm": 0.5, "linetype": "dashed"},
        ])
        return doc

    def test_pdf_structure_and_xref_offsets(self):
        doc = self._pdf_doc()
        text = oe.render_pdf_text(doc, style_table="mono.ctb", title="EXTRA")
        self.assertTrue(text.startswith("%PDF-1.4"))
        self.assertTrue(text.rstrip().endswith("%%EOF"))
        self.assertIn("/Count 1", text)
        self.assertIn("/MediaBox [0 0 841.8898 595.2756]", text)
        self.assertIn("\n0.9921 w\n", text, "0.35 mm mapping becomes 0.9921 pt")
        self.assertIn("\n1 g\n", text, "monochrome full screening paints solid black")
        self.assertIn("[1 0.5] 0 d", text, "dashed mapping becomes a PDF dash pattern")
        self.assertIn(" m\n", text)
        self.assertIn(" l\n", text)
        self.assertIn("\nS\n", text)
        self.assertIn("(EXTRA) Tj", text)
        marker = text.rfind("startxref")
        offset = int(text[marker + len("startxref"):].split()[0])
        self.assertTrue(text[offset:].startswith("xref"))
        lines = text[offset:].split("\n")
        count = int(lines[1].split()[1])
        self.assertEqual(count, 6)
        for number, entry in enumerate(lines[2:2 + count]):
            parts = entry.split()
            self.assertEqual(len(parts), 3)
            self.assertEqual(parts[2], "n" if number else "f")
            if number:
                self.assertTrue(text[int(parts[0]):].startswith("%d 0 obj" % number))

    def test_pdf_curves_use_bezier_segments(self):
        doc = self._pdf_doc()
        text = oe.render_pdf_text(doc, style_table="mono.ctb")
        self.assertEqual(text.count("\nc "), 4, "a full circle is four cubic Bezier segments")
        self.assertNotIn("nan", text.lower())

    def test_export_writes_bytes_verified_on_disk(self):
        doc = self._pdf_doc()
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "sheet.pdf")
            export_uuid = oe.export_pdf_text(doc, out_path=target, style_table="mono.ctb")[0]
            on_disk = Path(target).read_bytes()
        record = _export_record(doc, export_uuid)
        self.assertEqual(on_disk, oe.render_pdf_text(doc, style_table="mono.ctb").encode("latin-1"))
        self.assertTrue(record["written"])
        self.assertTrue(record["bytes_match"])
        self.assertTrue(record["xref_verified"])
        self.assertEqual(record["sha256"], hashlib.sha256(on_disk).hexdigest())
        self.assertEqual(record["page_count"], 1)
        self.assertEqual(record["pdf_version"], "1.4")
        self.assertGreater(record["xref_offset"], 0)

    def test_ascii_only_export_text(self):
        doc = self._pdf_doc()
        for render in (oe.render_svg_text, oe.render_pdf_text):
            with self.assertRaises(CadError) as caught:
                render(doc, style_table="mono.ctb", title=chr(0x56FE) + "x")
            self.assertEqual(caught.exception.reason, "non_ascii_text")

    def test_manifest_filters_and_reasons(self):
        doc = self._pdf_doc()
        svg_uuid = oe.export_svg_text(doc, style_table="mono.ctb")[0]
        pdf_uuid = oe.export_pdf_text(doc, style_table="mono.ctb")[0]
        self.assertEqual(oe.export_manifest(doc), [svg_uuid, pdf_uuid])
        self.assertEqual(oe.export_manifest(doc, format="svg"), [svg_uuid])
        self.assertEqual(oe.export_manifest(doc, format="pdf"), [pdf_uuid])
        with self.assertRaises(CadError) as caught:
            oe.export_manifest(doc, format="dxf")
        self.assertEqual(caught.exception.reason, "bad_format")


class TestModelPurityAndErrors(unittest.TestCase):
    def test_exports_never_touch_model_geometry(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)
        before = json.dumps(_payload(doc)["sketches"], sort_keys=True)
        oe.render_svg_text(doc, style_table="mono.ctb")
        oe.render_pdf_text(doc, style_table="mono.ctb")
        with tempfile.TemporaryDirectory() as tmp:
            oe.export_svg_text(doc, out_path=str(Path(tmp) / "a.svg"), style_table="mono.ctb")
            oe.export_pdf_text(doc, out_path=str(Path(tmp) / "a.pdf"), style_table="mono.ctb")
        oe.style_map_apply(doc, table="mono.ctb")
        self.assertEqual(json.dumps(_payload(doc)["sketches"], sort_keys=True), before)

    def test_bare_sketch_mapping_is_a_supported_carrier(self):
        sketch = {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_URL, "output-extra-bare")),
                  "plane": "XY", "entities": [_line(0, 0, 0, 10, 0)]}
        _table(sketch, "mono.ctb")
        oe.style_map_set(sketch, table="mono.ctb", color="*", lineweight_mm=0.25)
        export_uuid = oe.export_svg_text(sketch, style_table="mono.ctb")[0]
        self.assertIn("<svg", oe.render_svg_text(sketch, style_table="mono.ctb"))
        self.assertEqual(
            [record["uuid"] for record in sketch["output_extra"]["exports"]], [export_uuid])

    def test_error_reason_registry(self):
        doc = _doc()
        _table(doc, "mono.ctb")
        oe.style_map_set(doc, table="mono.ctb", color="*", lineweight_mm=0.25)
        empty = {"schema_version": 1, "unit": "mm", "sketches": []}
        _table(empty, "mono.ctb")
        cases = [
            ("bad_paper", lambda: oe.render_svg_text(doc, paper="A9", style_table="mono.ctb")),
            ("bad_orientation",
             lambda: oe.render_svg_text(doc, orientation="diagonal", style_table="mono.ctb")),
            ("bad_margin",
             lambda: oe.render_svg_text(doc, margin_mm=200.0, style_table="mono.ctb")),
            ("bad_scale", lambda: oe.render_svg_text(doc, scale="abc", style_table="mono.ctb")),
            ("unknown_style_table",
             lambda: oe.style_map_lookup(doc, table="missing.ctb")),
            ("bad_table", lambda: oe.style_table_create(doc, name="mono.txt")),
            ("bad_flag", lambda: oe.style_map_set(doc, table="mono.ctb", color="red", plot=1)),
            ("bad_linetype",
             lambda: oe.style_map_set(doc, table="mono.ctb", color="red", linetype="zigzag")),
            ("bad_screening",
             lambda: oe.style_map_set(doc, table="mono.ctb", color="red", screening=101)),
            ("bad_lineweight",
             lambda: oe.style_map_set(doc, table="mono.ctb", color="red", lineweight_mm=99.0)),
            ("bad_color", lambda: oe.style_map_set(doc, table="mono.ctb", color="chartreuse")),
            ("empty_mappings",
             lambda: oe.style_map_bulk(doc, table="mono.ctb", mappings=[])),
            ("non_ascii_text",
             lambda: oe.export_pdf_text(doc, title=chr(0x56FE), style_table="mono.ctb")),
            ("no_model_geometry",
             lambda: oe.export_svg_text(empty, style_table="mono.ctb")),
            ("bad_document", lambda: oe.style_table_list(42)),
        ]
        for reason, call in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(CadError) as caught:
                    call()
                self.assertEqual(caught.exception.reason, reason)

    def test_module_selftest_line(self):
        completed = subprocess.run([sys.executable, str(_MODULE_PATH)], capture_output=True,
                                   text=True, timeout=180)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("EXTRA_OPS verdict=PASS ops=%d" % len(oe.OPERATIONS), completed.stdout)
        self.assertIn("check negative_controls", completed.stdout)


if __name__ == "__main__":
    unittest.main()
