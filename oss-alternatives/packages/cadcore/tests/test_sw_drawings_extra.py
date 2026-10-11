"""tests.test_sw_drawings_extra —— SW-DRAWINGS-EXTRA 族（`cadcore.ops.sw_drawings_extra`）单测。

本文件是**族接口与三条操作的尺子**，不是实现的复印件：断言只打冻结接口与可机读后果，
不 import 被测模块的内部 helper（只读 `sw_drawings` 的公开登记面确认 uuid 真落文档）。

冻结接口（逐条打分）
--------------------
1. `FAMILY` 非空字符串、`OPERATIONS` 是 `dict[str, Callable]`；
2. 每个操作第一个位置参叫 `sk_or_doc` 且可位置传递，其余参数 KEYWORD_ONLY；
3. 返回值是**本次新建对象**的 uuid 列表，每个 uuid 都能在调用后的文档里解析到
   （图纸 uuid 落 `sheets[]`、其余落 `annotations[]` —— 与 `sw_drawings._publish` 同一套口径）；
4. 错误一律 `cadcore.errors.CadError`，带机器可读 `reason`（不靠解析文案）。

三类事故是这把尺子专门盯的（都留了阴性对照）
--------------------------------------------
① 接不上接口：签名检查器 + 直接按位置调用；② 静默改数据：该抛错处返回空列表/静默近似 ⇒ 逐条断言
`reason`；③ 假绿尺子：三个行为变异体反证签名检查器会说"违规"。

夹具自证
--------
* 夹具只用 `cadcore.sketch.primitives.add_rectangle`（真件）与 L1 文档映射（族冻结入参形态②），
  全程内存操作、不落盘（`out=` 用例写在 `tempfile.TemporaryDirectory()` 里）；
* `test_module_under_test_is_the_real_file` 证明被测对象就是 `cadcore/ops/sw_drawings_extra.py` 本身。
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import sys
import tempfile
import unittest
import uuid as _uuid
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.draw import sheet as sheet_mod  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import sw_drawings as sw_base  # noqa: E402
from cadcore.ops import sw_drawings_extra as extra  # noqa: E402
from cadcore.sketch import primitives  # noqa: E402

MM = 1_000_000


# --------------------------------------------------------------------------------------
# 夹具（真草图 + 真拉伸特征的 L1 文档映射）
# --------------------------------------------------------------------------------------


def _document() -> dict:
    sketch = {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.test.sketch")),
              "plane": "XY", "entities": [], "constraints": [], "dimensions": []}
    primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=(60 * MM, 40 * MM))
    return {
        "meta": {"doc_uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.test.doc"))},
        "sketches": [sketch],
        "features": [{"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_extra.test.extrude")),
                      "kind": "extrude", "params": {"distance_nm": 20 * MM}, "suppressed": False}],
        "annotations": [], "sheets": [],
    }


def _items() -> list[dict]:
    """三行明细：part.base 两行（其中一行质量未采）+ part.bolt 一行。"""
    return [
        {"qty": 2, "part_key": "part.base", "material_key": "mat.steel", "mass_mg": 1500},
        {"qty": 1, "part_key": "part.base", "material_key": "mat.steel"},
        {"qty": 4, "part_key": "part.bolt", "material_key": "mat.steel", "mass_mg": 25},
    ]


def _annotations(doc: dict) -> dict:
    return {str(entry.get("uuid")): entry for entry in doc["annotations"]}


def _resolvable(doc: dict, uuid_text: str) -> bool:
    """返回的 uuid 必须在调用后的文档里解析到：图纸在 `sheets[]`，其余在 `annotations[]`。"""
    return uuid_text in _annotations(doc) or any(
        str(entry.get("uuid")) == uuid_text for entry in doc.get("sheets", []))


def _live_sheet(doc: dict):
    sheets = sw_base._sheet_objects(doc)
    return sheets[-1] if sheets else None


def _entries(doc: dict, kind: str) -> list[dict]:
    return [entry for entry in doc["annotations"] if entry.get("kind") == kind]


def _violations(operations: dict) -> list[str]:
    """冻结签名检查器：返回违规清单（空 = 合规），比实现本身更容易看见红。"""
    bad: list[str] = []
    for name, op in operations.items():
        try:
            params = list(inspect.signature(op).parameters.values())
        except (TypeError, ValueError) as exc:  # pragma: no cover - 只有不可内省对象会到这里
            bad.append("%s: not introspectable (%s)" % (name, type(exc).__name__))
            continue
        if not params or params[0].name != "sk_or_doc" or params[0].kind not in (
                inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            bad.append("%s: first parameter must be positional sk_or_doc" % name)
        for param in params[1:]:
            if param.kind is not inspect.Parameter.KEYWORD_ONLY:
                bad.append("%s: parameter %s must be KEYWORD_ONLY" % (name, param.name))
    return bad


def _mutant_renamed(model, **kwargs):  # 变异体①：首参名写错
    return []


def _mutant_positional(sk_or_doc, depth_nm, **kwargs):  # 变异体②：多余位置参
    return []


def _mutant_varargs(sk_or_doc, *args, **kwargs):  # 变异体③：吞掉任意位置参
    return []


# --------------------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------------------


class InterfaceTests(unittest.TestCase):
    def test_family_and_operations_shape(self) -> None:
        self.assertIsInstance(extra.FAMILY, str)
        self.assertTrue(extra.FAMILY.strip())
        self.assertIsInstance(extra.OPERATIONS, dict)
        for name, op in extra.OPERATIONS.items():
            self.assertIsInstance(name, str)
            self.assertTrue(callable(op), name)
        self.assertEqual(sorted(extra.OPERATIONS),
                         ["balloon", "bom_row_items", "print_svg_text"])
        self.assertEqual(sorted(extra.module_facts()["operations"]), sorted(extra.OPERATIONS))

    def test_signature_is_frozen(self) -> None:
        self.assertEqual(_violations(extra.OPERATIONS), [])

    def test_checker_sees_violations(self) -> None:
        for name, op in (("renamed", _mutant_renamed), ("positional", _mutant_positional),
                         ("varargs", _mutant_varargs)):
            self.assertTrue(_violations({name: op}), "%s: checker did not see the violation" % name)

    def test_module_under_test_is_the_real_file(self) -> None:
        path = Path(inspect.getsourcefile(extra)).resolve()
        self.assertEqual(path.name, "sw_drawings_extra.py")
        self.assertTrue(path.is_file())
        source = path.read_text(encoding="utf-8")
        self.assertIn("FAMILY", source)
        self.assertIn("OPERATIONS", source)


# --------------------------------------------------------------------------------------
# 操作 1：零件序号
# --------------------------------------------------------------------------------------


class BalloonTests(unittest.TestCase):
    def test_balloon_places_callouts_and_returns_resolvable_uuids(self) -> None:
        doc = _document()
        out = extra.OPERATIONS["balloon"](doc, items=_items())
        self.assertEqual(len(out), 2 + len(_items()))          # 图纸 + 视图 + 每行一颗
        for uid in out:
            self.assertTrue(_resolvable(doc, uid), "dangling uuid %s" % uid)
        self.assertEqual(doc["sheets"][-1]["uuid"], out[0])    # 图纸 uuid 落 sheets[]
        rows = [entry for entry in _entries(doc, "balloon") if "args" in entry]
        self.assertEqual(len(rows), len(_items()))             # 视图记录无 args，不算行
        self.assertEqual(sorted(int(entry["args"]["item"]) for entry in rows), [1, 2, 3])
        sheet = _live_sheet(doc)
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet.uuid, out[0])
        views = [view for view in sheet.views if view.name == "balloon"]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].uuid, out[1])
        self.assertGreaterEqual(len(views[0].visible), 2 * len(_items()))   # 圆 + 引线
        self.assertIn(extra.BALLOON_LAYER, sheet.layers)
        self.assertTrue(any(entry.get("kind") == "balloon" and "args" not in entry
                            for entry in doc["annotations"]), "view record missing in annotations[]")

    def test_balloon_accepts_explicit_positions(self) -> None:
        doc = _document()
        points = [(20 * MM, 20 * MM), (30 * MM, 30 * MM), (40 * MM, 40 * MM)]
        out = extra.balloon(doc, items=_items(), positions_nm=points)
        rows = [entry["args"] for entry in _entries(doc, "balloon") if "args" in entry]
        self.assertEqual([tuple(row["position_nm"]) for row in rows], points)
        self.assertEqual(len(out), 2 + len(points))

    def test_balloon_position_count_mismatch_raises(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.balloon(_document(), items=_items(), positions_nm=[(10 * MM, 10 * MM)])
        self.assertEqual(caught.exception.reason, "balloon_position_mismatch")

    def test_balloon_off_sheet_is_loud(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.balloon(_document(), items=_items(), positions_nm=[(0, 0)] * len(_items()))
        self.assertEqual(caught.exception.reason, "balloon_off_sheet")

    def test_balloon_bool_radius_is_rejected(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.balloon(_document(), items=_items(), radius_nm=True)
        self.assertTrue(str(caught.exception.reason).strip())

    def test_balloon_missing_source_raises(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.balloon(_document())
        self.assertEqual(caught.exception.reason, "bom_source_missing")

    def test_balloon_needs_document(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.balloon({"entities": []}, items=_items())
        self.assertEqual(caught.exception.reason, "bom_needs_document")

    def test_balloon_reads_document_source_key(self) -> None:
        doc = _document()
        doc["x_bom"] = _items()
        out = extra.balloon(doc)
        self.assertEqual(len(out), 2 + len(_items()))


# --------------------------------------------------------------------------------------
# 操作 2：BOM 明细表行项
# --------------------------------------------------------------------------------------


class BomRowTests(unittest.TestCase):
    def test_rows_merge_quantity_and_mass(self) -> None:
        doc = _document()
        out = extra.OPERATIONS["bom_row_items"](doc, items=_items())
        rows = _entries(doc, "bom_row")
        self.assertEqual(len(rows), 2)                       # 三行 → 两组
        self.assertEqual(len(out), 1 + len(rows))
        for uid in out:
            self.assertTrue(_resolvable(doc, uid), "dangling uuid %s" % uid)
        by_item = {int(entry["args"]["item"]): entry["args"] for entry in rows}
        self.assertEqual(sorted(by_item), [1, 2])
        self.assertEqual(by_item[1]["part_key"], "part.base")
        self.assertEqual(by_item[1]["qty"], 3)
        self.assertEqual(by_item[1]["source_rows"], 2)
        self.assertEqual(by_item[1]["mass_unmeasured_rows"], 1)
        self.assertEqual(by_item[1]["mass_mg"], sheet_mod.UNMEASURED)   # 一行未采 ⇒ 整组未采
        self.assertEqual(by_item[2]["part_key"], "part.bolt")
        self.assertEqual(by_item[2]["qty"], 4)
        self.assertEqual(by_item[2]["mass_mg"], 25)                     # 全采 ⇒ 求和
        self.assertEqual(by_item[2]["mass_unmeasured_rows"], 0)

    def test_rows_without_merge_keep_every_row_and_reissue_item_numbers(self) -> None:
        doc = _document()
        items = [dict(row, item=99 + index) for index, row in enumerate(_items())]
        out = extra.bom_row_items(doc, items=items, merge_duplicates=False, start_item=7)
        rows = _entries(doc, "bom_row")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(out), 4)
        self.assertEqual(sorted(int(entry["args"]["item"]) for entry in rows), [7, 8, 9])

    def test_rows_are_deterministic(self) -> None:
        first = extra.bom_row_items(_document(), items=_items())
        second = extra.bom_row_items(_document(), items=_items())
        self.assertEqual(first[1:], second[1:])              # 只比行 uuid：图纸 uuid 不是本操作的口径

    def test_rows_empty_source_raises(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.bom_row_items(_document(), items=[])
        self.assertEqual(caught.exception.reason, "bom_source_missing")

    def test_rows_need_document(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.bom_row_items({"entities": []}, items=_items())
        self.assertEqual(caught.exception.reason, "bom_needs_document")

    def test_rows_bad_row_raises(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.bom_row_items(_document(), items=[{"qty": 1}])
        self.assertEqual(caught.exception.reason, "bad_bom_row")


# --------------------------------------------------------------------------------------
# 操作 3：打印导出 SVG 文本
# --------------------------------------------------------------------------------------


class PrintSvgTests(unittest.TestCase):
    def test_print_carries_svg_text_and_records_views_zero(self) -> None:
        doc = _document()
        out = extra.OPERATIONS["print_svg_text"](doc)
        self.assertEqual(len(out), 2)                         # 图纸 + 记录
        for uid in out:
            self.assertTrue(_resolvable(doc, uid), "dangling uuid %s" % uid)
        records = _entries(doc, extra.SHEET_RECORD)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["uuid"], out[1])
        args = records[0]["args"]
        text = str(args["svg_text"])
        self.assertTrue(text.lstrip().startswith("<?xml"))
        self.assertIn("<svg", text)
        self.assertTrue(text.rstrip().endswith("</svg>"))
        blob = text.encode("utf-8")
        self.assertEqual(args["bytes"], len(blob))
        self.assertEqual(args["sha256"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(args["views"], 0)                    # 真为零（不是未采）
        self.assertEqual(args["out"], "")
        sheet = _live_sheet(doc)
        self.assertTrue(any("no views" in note for note in sheet.diagnostics))

    def test_print_is_deterministic(self) -> None:
        texts = []
        for _ in range(2):
            doc = _document()
            extra.print_svg_text(doc)
            texts.append(str(_entries(doc, extra.SHEET_RECORD)[0]["args"]["svg_text"]))
        self.assertEqual(texts[0], texts[1])

    def test_print_writes_out_file_and_export_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = _document()
            extra.print_svg_text(doc, out=str(Path(tmp) / "drawing"))   # 无扩展名 ⇒ 落 .svg
            target = Path(tmp) / "drawing.svg"
            self.assertTrue(target.is_file())
            text = str(_entries(doc, extra.SHEET_RECORD)[0]["args"]["svg_text"])
            self.assertEqual(target.read_text(encoding="utf-8"), text)
            sheet = _live_sheet(doc)
            self.assertEqual(len(sheet.exports), 1)
            self.assertEqual(sheet.exports[0].actual, "svg")
            self.assertEqual(sheet.exports[0].path, str(target))

    def test_print_bare_sketch_without_out_raises(self) -> None:
        with self.assertRaises(CadError) as caught:
            extra.print_svg_text({"entities": []})
        self.assertEqual(caught.exception.reason, "print_needs_document")

    def test_print_bare_sketch_with_out_still_writes_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = extra.print_svg_text({"entities": []}, out=str(Path(tmp) / "bare.svg"))
            target = Path(tmp) / "bare.svg"
            self.assertTrue(target.is_file())
            self.assertTrue(target.read_text(encoding="utf-8").rstrip().endswith("</svg>"))
            # 裸草图没有文档可登记：返回 [新建图纸, 内容寻址记录]，文本只在落盘文件里
            self.assertEqual(len(out), 2)

    def test_print_uses_existing_sheet_without_returning_it(self) -> None:
        doc = _document()
        made = extra.bom_row_items(doc, items=_items())
        sheet = _live_sheet(doc)
        out = extra.print_svg_text(doc, sheet=sheet)
        self.assertEqual(len(out), 1)                          # 复用既有图纸 ⇒ 不重复返回它
        self.assertNotIn(str(made[0]), out)


# --------------------------------------------------------------------------------------
# 操作 3b：真 PDF 导出（纯标准库写出器，G21 export 补席）
# --------------------------------------------------------------------------------------


class PdfExportTests(unittest.TestCase):
    """`print_svg_text` 的**真 PDF 后果**：魔数 / 非空页面流 / 实体数映射 / 非法参数抛 CadError。

    断言只打可机读后果（PDF 字节本身 + annotation `pdf_*` 键 + `ExportResult`），不 import 写出器内部 helper。
    """

    def _args(self, doc: dict) -> dict:
        records = _entries(doc, extra.SHEET_RECORD)
        self.assertEqual(len(records), 1)
        return records[0]["args"]

    def test_pdf_record_carries_real_magic_and_page_stream(self) -> None:
        doc = _document()
        extra.balloon(doc, items=_items())
        sheet = _live_sheet(doc)
        out = extra.print_svg_text(doc, sheet=sheet)
        self.assertEqual(len(out), 1)                       # 复用既有图纸 ⇒ 不重复返回它
        args = self._args(doc)
        self.assertEqual(extra.PDF_MAGIC, "%PDF-1.4")
        self.assertEqual(args["pdf_magic"], extra.PDF_MAGIC)
        self.assertTrue(str(args["pdf_b64"]).startswith("JVBERi0xLjQ"))   # base64("%PDF-1.4")
        payload = base64.b64decode(str(args["pdf_b64"]))
        self.assertEqual(payload[:8], b"%PDF-1.4")          # 真魔数，不是字段自称
        self.assertIn(b"/Type /Page", payload)
        self.assertIn(b"stream", payload)
        self.assertIn(b"xref", payload)
        self.assertTrue(payload.rstrip().endswith(b"%%EOF"))
        self.assertEqual(int(args["pdf_bytes"]), len(payload))
        self.assertEqual(str(args["pdf_sha256"]), hashlib.sha256(payload).hexdigest())
        self.assertEqual(int(args["pdf_pages"]), 1)
        self.assertGreater(int(args["pdf_stream_bytes"]), 0)              # 非空页面内容流
        self.assertIn("builtin_pdf", str(args["pdf_writer"]))
        self.assertGreaterEqual(int(args["pdf_paths"]), 2 * len(_items()))  # 气球圆 + 引线
        self.assertEqual(len(sheet.exports), 0)             # 没给 out ⇒ 不假装落过盘

    def test_pdf_out_file_bytes_start_with_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = _document()
            extra.balloon(doc, items=_items())
            sheet = _live_sheet(doc)
            extra.print_svg_text(doc, sheet=sheet, out=str(Path(tmp) / "sheet.pdf"))
            target = Path(tmp) / "sheet.pdf"
            self.assertTrue(target.is_file())
            blob = target.read_bytes()
            self.assertEqual(blob[:8], b"%PDF-1.4")         # 与 `head -c 8` 同一份字节
            args = self._args(doc)
            self.assertEqual(args["out"], str(target))
            self.assertEqual(args["export_format"], "pdf")
            self.assertEqual(int(args["pdf_bytes"]), len(blob))
            self.assertEqual(str(args["pdf_sha256"]), hashlib.sha256(blob).hexdigest())
            self.assertEqual(len(sheet.exports), 1)
            self.assertEqual(sheet.exports[0].requested, "pdf")
            self.assertEqual(sheet.exports[0].actual, "pdf")
            self.assertFalse(sheet.exports[0].downgraded)
            self.assertEqual(sheet.exports[0].path, str(target))
            self.assertEqual(sheet.exports[0].bytes, len(blob))

    def test_pdf_entity_mapping_matches_iter_entities(self) -> None:
        doc = _document()
        extra.balloon(doc, items=_items())
        sheet = _live_sheet(doc)
        extra.print_svg_text(doc, sheet=sheet)
        args = self._args(doc)
        entities = len(sheet_mod.iter_entities(sheet))
        self.assertGreater(entities, 0)
        self.assertEqual(int(args["pdf_entities"]), entities)
        self.assertEqual(int(args["pdf_paths"]) + int(args["pdf_texts"]) + int(args["pdf_skipped"]),
                         entities)
        self.assertGreater(int(args["pdf_paths"]), 0)

    def test_pdf_is_deterministic(self) -> None:
        digests = []
        for _ in range(2):
            doc = _document()
            extra.balloon(doc, items=_items())
            extra.print_svg_text(doc, sheet=_live_sheet(doc))
            digests.append(str(self._args(doc)["pdf_sha256"]))
        self.assertEqual(digests[0], digests[1])

    def test_pdf_forced_format_wins_over_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = _document()
            extra.print_svg_text(doc, out=str(Path(tmp) / "sheet.svg"), export_format="pdf")
            self.assertTrue((Path(tmp) / "sheet.pdf").is_file())
            self.assertFalse((Path(tmp) / "sheet.svg").exists())
            self.assertEqual((Path(tmp) / "sheet.pdf").read_bytes()[:8], b"%PDF-1.4")

    def test_pdf_invalid_format_and_dwg_raise_cad_error(self) -> None:
        for bad in ("dwg", "jpg", 7, object()):
            with self.assertRaises(CadError) as caught:
                extra.print_svg_text(_document(), export_format=bad)
            self.assertIn(str(caught.exception.reason),
                          ("export_format_external", "bad_export_format"))
        with self.assertRaises(CadError) as caught:
            extra.print_svg_text(_document(), export_format="dwg")
        self.assertEqual(caught.exception.reason, "export_format_external")
        with self.assertRaises(CadError) as caught:
            extra.print_svg_text(_document(), export_format="dxf")
        self.assertEqual(caught.exception.reason, "export_needs_out")


if __name__ == "__main__":
    program = unittest.main(exit=False, verbosity=2)
    result = program.result
    verdict = "PASS" if result.wasSuccessful() else "FAIL"
    print("SW_DRAWINGS_EXTRA verdict=%s ops=%d tests=%d failures=%d errors=%d"
          % (verdict, len(extra.OPERATIONS), result.testsRun,
             len(result.failures), len(result.errors)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
