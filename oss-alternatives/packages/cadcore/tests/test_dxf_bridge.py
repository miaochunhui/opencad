"""A13 `cadcore.io.dxf_bridge` 的单测：图纸/草图 → DXF 字节，以及五项保真尺子。

覆盖的承重判据（每条都真跑真件，不桩掉 `packages/dxf`）：

1. **未改实体逐字节相等** —— `read_dxf` → `write_dxf` 与输入 sha256 相同（`packages/dxf` 的自检）。
2. **未知实体保留** —— `MYSTERY_ENTITY` + XDATA（1001/1000）读回后类型/标签一字不丢。
3. **图纸 → DXF 能读回**，`DIMENSION` 条数 = `sheet.dimensions` 条数（复用件当尺子）。
4. **`draw.sheet._render_dxf` 自动改走桥**（`sheet_facts()["dxf_writer"]` 不再是 builtin）。
5. **五项保真 + 四个做坏夹具必须报红**（阴性对照：丢 tag / 改类型计数 / 改坐标 / 丢实体）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG = str(_HERE.parent.parent)                      # packages/cadcore
_PACKAGES = str(_HERE.parent.parent.parent)          # packages
for _path in (_PKG, os.path.join(_PACKAGES, "dxf"), _PACKAGES):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from cadcore.errors import DocumentError  # noqa: E402
from cadcore.io import dxf_bridge  # noqa: E402
from cadcore.draw import sheet as sh  # noqa: E402
from cadcore.kernel import nano  # noqa: E402
from dxf import reader as dxf_reader  # noqa: E402
from dxf import writer as dxf_writer  # noqa: E402

ZH = "zh-CN"
STANDARD = "gbt"
PLATE = _HERE.parent / "data" / "plate.json"


def _sample_dxf() -> str:
    """手写 clean-room 样本：两个已知实体 + 一个未知实体 + XDATA（不搬任何第三方素材）。"""
    return (
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n"
        "0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n30\n0.0\n11\n50.0\n21\n25.0\n31\n0.0\n"
        "0\nCIRCLE\n8\n0\n10\n10.0\n20\n10.0\n30\n0.0\n40\n5.0\n"
        "0\nMYSTERY_ENTITY\n8\n0\n1\nhello\n1001\nMYAPP\n1000\nnote-one\n"
        "0\nENDSEC\n0\nEOF\n"
    )


def _dropped(block: tuple) -> str:
    """从样本里**整块**删掉给定连续行（做坏夹具：丢 tag / 丢实体）。"""
    lines = _sample_dxf().split("\n")
    wanted = list(block)
    start = next((index for index in range(len(lines))
                  if lines[index:index + len(wanted)] == wanted), None)
    assert start is not None, f"fixture self-check failed: block {wanted!r} not in sample"
    del lines[start:start + len(wanted)]
    return "\n".join(lines)


def _replaced(old: str, new: str) -> str:
    text = _sample_dxf()
    assert old in text, f"fixture self-check failed: {old!r} not in sample"
    return text.replace(old, new, 1)


def _tag_summary(report: dict) -> str:
    return (f"entities={report['entities_in']}->{report['entities_out']} "
            f"type_match={report['type_counts_match']} delta={report['max_coord_delta']} "
            f"lost={report['lost_tags']} audit={report['audit_errors']}")


class DxfBridgeFixtureCheck(unittest.TestCase):
    """E2.5 夹具自证：证明测的是真件（真 `packages/dxf`），不是本地替身。"""

    def test_fixture_selfcheck_uses_the_real_dxf_package(self):
        modules = dxf_bridge._dxf_modules()
        self.assertIs(modules["reader"], dxf_reader)
        self.assertIs(modules["writer"], dxf_writer)
        document = dxf_reader.parse_bytes(_sample_dxf().encode("utf-8"))
        self.assertEqual(document.entity_count(), 3, "sample fixture must carry 3 top-level entities")
        self.assertEqual(document.entity_type_counts(),
                         {"LINE": 1, "CIRCLE": 1, "MYSTERY_ENTITY": 1})

    def test_sample_fixture_round_trips_byte_for_byte(self):
        payload = _sample_dxf().encode("utf-8")
        self.assertEqual(dxf_writer.dump_bytes(dxf_reader.parse_bytes(payload)), payload)


class SheetToDxfTests(unittest.TestCase):
    """A13 图纸出口：真图纸（plate.json + nano 内核）→ 字节 → 复用件读回。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(PLATE.read_text(encoding="utf-8"))
        cls.kernel = nano.make_kernel()
        skin = cls.doc["sketches"][0]
        cls.shape, cls.hidden = cls.doc, True
        try:                       # 2D wire 没有隐藏线；整份文档才带体，nano 才给得出隐藏线
            cls.kernel.project_to_2d(cls.shape, view="front", hidden=True)
        except Exception:  # noqa: BLE001
            cls.shape, cls.hidden = skin, False
        cls.sheet = sh.make_sheet(cls.doc, standard=STANDARD, language=ZH)
        reverse = {tuple(value): name
                   for name, value in (getattr(sh, "VIEW_DIRECTIONS", {}) or {}).items()}
        specs = ((cls.doc.get("sheets") or [{}])[0].get("views") or [])
        kinds = [reverse.get(tuple(spec.get("direction") or ()), "front") for spec in specs] or ["front"]
        margin = 20 * int(getattr(sh, "NM_PER_MM", 1_000_000))
        slot = max(1, (int(cls.sheet.width_nm) - 2 * margin) // max(1, len(kinds)))
        for index, name in enumerate(kinds):
            at_nm = (margin + index * slot,
                     margin + (int(cls.sheet.height_nm) - 2 * margin) // 4)
            sh.add_view(cls.sheet, cls.shape, view=name, at_nm=at_nm,
                        kernel=cls.kernel, hidden=cls.hidden)
        sh.add_dimensions(cls.sheet, min_count=8, kernel=cls.kernel)

    def test_sheet_to_dxf_is_bytes_and_reads_back_with_every_dimension(self):
        payload = dxf_bridge.sheet_to_dxf(self.sheet)
        self.assertIsInstance(payload, bytes)
        self.assertTrue(payload.startswith(b"0\nSECTION"), payload[:32])
        document = dxf_bridge.read_dxf(payload)
        self.assertGreaterEqual(len(self.sheet.dimensions), 8)
        self.assertEqual(document.entity_type_counts().get("DIMENSION", 0), len(self.sheet.dimensions))
        self.assertIn("VISIBLE", document.layer_names())

    def test_export_sheet_routes_through_the_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plate.dxf"
            written = sh.export_sheet(self.sheet, fmt="dxf", out=str(out))
            self.assertEqual(Path(written), out)
            self.assertTrue(out.exists())
            self.assertEqual(sh.sheet_facts(self.sheet)["dxf_writer"],
                             "cadcore.io.dxf_bridge.sheet_to_dxf")
            document = dxf_bridge.read_dxf(out.read_bytes())
            self.assertEqual(document.entity_type_counts().get("DIMENSION", 0),
                             int(sh.sheet_facts(self.sheet)["dim_entities"]))

    def test_sheet_dxf_coordinates_are_document_units_not_nanometres(self):
        document = dxf_bridge.read_dxf(dxf_bridge.sheet_to_dxf(self.sheet))
        box = document.section("HEADER").header_vars()["$EXTMAX"]
        self.assertLess(float(box[0].value), 1_000.0, "EXTMAX must be in mm, not nanometres")

    def test_reused_reader_contract_holds_for_mm_layers_and_hidden_usage(self):
        """`tests/test_draw.py` 那条 DXF 断言里**除 writer 名字外**的每一条，在本桥下仍成立。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plate.dxf"
            sh.export_sheet(self.sheet, fmt="dxf", out=str(out))
            document = dxf_bridge.read_dxf(out.read_bytes())
            counts = document.entity_type_counts()
            self.assertEqual(8, counts.get("DIMENSION", 0))
            self.assertGreater(counts.get("LINE", 0), 0)
            self.assertGreater(counts.get("TEXT", 0), 0)
            layers = document.layer_names()
            self.assertIn(sh.LAYER_HIDDEN, layers)
            self.assertIn(sh.LAYER_VISIBLE, layers)
            self.assertGreater(document.layer_usage().get(sh.LAYER_HIDDEN, 0), 0)
            xs = [point[0] for entity in document.entities_of_type("LINE") for point in entity.points()]
            self.assertTrue(xs)
            self.assertLess(max(xs), 420.0)
            self.assertGreater(max(xs), 300.0)

    def test_unsupported_version_is_rejected_loudly(self):
        with self.assertRaises(DocumentError) as caught:
            dxf_bridge.sheet_to_dxf(self.sheet, version="R9")
        self.assertEqual(caught.exception.reason, "unsupported_dxf_version")

    def test_unknown_sheet_entity_type_is_rejected_not_dropped(self):
        sheet = types.SimpleNamespace(unit="mm", layers={}, note=lambda message: None)
        original = sh.iter_entities
        sh.iter_entities = lambda _sheet: [{"type": "SPLINE", "layer": "0"}]
        try:
            with self.assertRaises(DocumentError) as caught:
                dxf_bridge.sheet_to_dxf(sheet)
        finally:
            sh.iter_entities = original
        self.assertEqual(caught.exception.reason, "unsupported_entity_type")


class SketchToDxfTests(unittest.TestCase):
    """A13 草图出口：L1 `entity{uuid,kind,layer,geom}` → DXF。"""

    def _sketch(self, dimensions=()):
        return types.SimpleNamespace(entities=[
            {"uuid": "u1", "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}},
            {"uuid": "u2", "kind": "circle", "layer": "0", "construction": False,
             "geom": {"cx": 5_000_000, "cy": 5_000_000, "r": 2_000_000}},
            {"uuid": "u3", "kind": "arc", "layer": "0", "construction": False,
             "geom": {"cx": 0, "cy": 0, "r": 3_000_000, "start_deg": 0.0, "end_deg": 90.0}},
        ], dimensions=list(dimensions))

    def test_sketch_to_dxf_emits_real_geometry(self):
        document = dxf_bridge.read_dxf(dxf_bridge.sketch_to_dxf(self._sketch()))
        self.assertEqual(document.entity_type_counts(), {"LINE": 1, "CIRCLE": 1, "ARC": 1})
        line = document.entities_of_type("LINE")[0]
        self.assertEqual(line.start()[:2], (0.0, 0.0))
        self.assertEqual(line.end()[:2], (10.0, 0.0))
        self.assertEqual(document.entities_of_type("ARC")[0].angles(), (0.0, 90.0))

    def test_sketch_dimensions_are_marked_not_silently_dropped(self):
        payload = dxf_bridge.sketch_to_dxf(self._sketch(dimensions=[{"uuid": "d1"}]))
        self.assertIn(b"skipped 1 sketch dimension", payload)
        self.assertEqual(dxf_bridge.read_dxf(payload).entity_type_counts().get("DIMENSION", 0), 0)

    def test_sketch_unknown_kind_is_rejected(self):
        sketch = types.SimpleNamespace(entities=[
            {"uuid": "u9", "kind": "spline", "layer": "0", "geom": {}}], dimensions=[])
        with self.assertRaises(DocumentError) as caught:
            dxf_bridge.sketch_to_dxf(sketch)
        self.assertEqual(caught.exception.reason, "unsupported_sketch_entity_kind")

    def test_sketch_non_integral_nanometres_are_rejected(self):
        sketch = types.SimpleNamespace(entities=[
            {"uuid": "u9", "kind": "line", "layer": "0",
             "geom": {"x1": 0.5, "y1": 0, "x2": 1, "y2": 0}}], dimensions=[])
        with self.assertRaises(DocumentError) as caught:
            dxf_bridge.sketch_to_dxf(sketch)
        self.assertEqual(caught.exception.reason, "sketch_geometry_invalid")


class ReadWriteFidelityTests(unittest.TestCase):
    """承重：未改实体逐字节相等 + 未知实体保留 + 五项保真 + 四个做坏夹具报红。"""

    def setUp(self):
        self.payload = _sample_dxf().encode("utf-8")

    def test_unmodified_read_write_is_byte_identical(self):
        document = dxf_bridge.read_dxf(self.payload)
        self.assertEqual(dxf_bridge.write_dxf(document), self.payload)

    def test_write_dxf_preserves_the_input_sha256(self):
        rendered = dxf_bridge.write_dxf(dxf_bridge.read_dxf(self.payload))
        self.assertEqual(hashlib.sha256(rendered).hexdigest(),
                         hashlib.sha256(self.payload).hexdigest())

    def test_unknown_entity_and_xdata_are_preserved(self):
        document = dxf_bridge.read_dxf(self.payload)
        unknown = document.unknown_entities()
        self.assertEqual([entity.type for entity in unknown], ["MYSTERY_ENTITY"])
        self.assertTrue(unknown[0].has_xdata)
        self.assertEqual([tag.value for tag in unknown[0].xdata if tag.code == 1000], ["note-one"])
        self.assertEqual(dxf_bridge.write_dxf(document), self.payload)

    def test_read_dxf_accepts_a_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.dxf"
            path.write_bytes(self.payload)
            self.assertEqual(dxf_bridge.read_dxf(str(path)).entity_count(), 3)

    def test_roundtrip_five_metrics_pass(self):
        report = dxf_bridge.roundtrip(self.payload)
        data = report.as_dict()
        self.assertEqual(data["entities_conserved"], 1, _tag_summary(data))
        self.assertEqual(data["type_counts_match"], 1, _tag_summary(data))
        self.assertEqual(data["max_coord_delta"], 0.0, _tag_summary(data))
        self.assertEqual(data["lost_tags"], 0, _tag_summary(data))
        self.assertEqual(data["audit_errors"], 0, _tag_summary(data))
        self.assertEqual(data["bytes_identical"], 1, _tag_summary(data))
        self.assertTrue(report.passed, report.verdict_line())
        self.assertIn("verdict=PASS", report.verdict_line())

    def test_roundtrip_reports_audit_error_for_unreadable_input(self):
        report = dxf_bridge.roundtrip(b"not a dxf at all\n")
        self.assertEqual(report.audit_errors, 1)
        self.assertFalse(report.passed)
        self.assertEqual(report.verdict_line().split()[1], "verdict=NA")

    def test_unmeasured_bytes_are_minus_one_not_zero(self):
        document = dxf_reader.parse_text(_sample_dxf())      # 没有输入字节可比
        other = dxf_reader.parse_text(_sample_dxf())
        report = dxf_bridge.compare_documents(document, other)
        self.assertEqual(report.bytes_identical, -1)
        self.assertFalse(report.measured)
        self.assertIn("not measured", " ".join(report.detail))

    def _bad(self, text: str) -> dict:
        return dxf_bridge.compare_documents(dxf_bridge.read_dxf(self.payload),
                                            dxf_bridge.read_dxf(text.encode("utf-8"))).as_dict()

    def test_negative_control_lost_tag(self):
        report = self._bad(_dropped(("1000", "note-one")))
        self.assertGreater(report["lost_tags"], 0, _tag_summary(report))

    def test_negative_control_type_count(self):
        report = self._bad(_replaced("0\nCIRCLE\n", "0\nCIRCLE_MUTANT\n"))
        self.assertEqual(report["type_counts_match"], 0, _tag_summary(report))

    def test_negative_control_coord_delta(self):
        report = self._bad(_replaced("11\n50.0\n", "11\n98765.4321\n"))
        self.assertGreater(report["max_coord_delta"], 1e-9, _tag_summary(report))

    def test_negative_control_entity_dropped(self):
        report = self._bad(_dropped(("0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "30",
                                     "0.0", "11", "50.0", "21", "25.0", "31", "0.0")))
        self.assertEqual(report["entities_conserved"], 0, _tag_summary(report))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
