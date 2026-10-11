"""dxf 包测试（`python3 -m unittest discover -s tests -t . -v`，cwd=packages/dxf）。

覆盖（判据全是**真跑**出来的，不是声明）：
读取结构（段/实体/顺序/未知段）/ 逐字节 round-trip（LF + CRLF + BOM）/ 数值原始表示保留 /
组码与值行空白形态保留 / 未知实体与代理对象逐字节保留 / XDATA 保留 / 坏输入报错（带行号）/
已知实体便利访问器（LINE·CIRCLE·ARC·TEXT·POINT·LWPOLYLINE·POLYLINE+VERTEX）/
**改一个实体后只有它所在的片段变化** / 索引幂等（真跑两遍比 content_digest）/ 删文件墓碑与复活 /
从活图纸里删实体也墓碑 / entities(type)·layers·stats·FTS 检索 / `.fcx` 导出→导入→再导出逐字节 /
`BASE_METHODS` 清单与**真实用法**一致。
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dxf import __version__ as DXF_VERSION                # noqa: E402
from dxf import index as di                               # noqa: E402
from dxf import model as dm                               # noqa: E402
from dxf import reader as dr                              # noqa: E402
from dxf import writer as dw                              # noqa: E402
from freechat_vault.fcx import export_fcx, import_fcx     # noqa: E402
from freechat_vault.store import DB_FILENAME, Vault       # noqa: E402

STAMP = "2026-09-29T09:00:00+08:00"

# --------------------------------------------------------------------------------------
# 测试样本：**本仓自研 clean-room**（每一对组码/值都是这里现写的）
# --------------------------------------------------------------------------------------

#: 组码行刻意留了前导空格的组码（测"空白形态保留"）
_INDENTED_CODE = "  8"
#: 值行刻意留了尾部空格的值（测"空白形态保留"）
_TRAILING_SPACE_VALUE = "note-42 "

SAMPLE_PAIRS = [
    ("999", "clean-room 测试样本 —— 本仓自研，不含任何第三方样例内容"),
    ("0", "SECTION"), ("2", "HEADER"),
    ("9", "$ACADVER"), ("1", "AC1009"),
    ("9", "$INSBASE"), ("10", "0.0"), ("20", "0.0"), ("30", "0.0"),
    ("0", "ENDSEC"),
    ("0", "SECTION"), ("2", "TABLES"),
    ("0", "TABLE"), ("2", "LAYER"), ("70", "3"),
    ("0", "LAYER"), ("2", "0"), ("70", "0"), ("62", "7"), ("6", "CONTINUOUS"),
    ("0", "LAYER"), ("2", "轮廓"), ("70", "0"), ("62", "3"), ("6", "CONTINUOUS"),
    ("0", "LAYER"), ("2", "标注"), ("70", "0"), ("62", "2"), ("6", "CONTINUOUS"),
    ("0", "ENDTAB"),
    ("0", "ENDSEC"),
    ("0", "SECTION"), ("2", "MYAPP_PRIVATE"),          # 我们不认识的段：原样保留
    ("0", "MYSTERY_OBJECT"), ("5", "C0"), ("1", "第三方私有段的内容"),
    ("0", "ENDSEC"),
    ("0", "SECTION"), ("2", "ENTITIES"),
    # 1 LINE
    ("0", "LINE"), ("5", "10"), ("8", "轮廓"),
    ("10", "0.0"), ("20", "0.0"), ("30", "0.0"),
    ("11", "120.0"), ("21", "0.0"), ("31", "0.0"),
    # 2 CIRCLE，带 XDATA
    ("0", "CIRCLE"), ("5", "11"), ("8", "轮廓"),
    ("10", "30.0"), ("20", "40.0"), ("30", "0.0"), ("40", "12.50"),
    ("1001", "MYAPP"), ("1000", "孔位-A"), ("1040", "12.5"),
    # 3 ARC，半径写成指数形式（数值原始表示保留）
    ("0", "ARC"), ("5", "12"), ("8", "轮廓"),
    ("10", "120.0"), ("20", "40.0"), ("30", "0.0"),
    ("40", "2.0e1"), ("50", "90.0"), ("51", "270.0"),
    # 4 POINT，组码行有前导空格
    ("0", "POINT"), ("5", "13"), (_INDENTED_CODE, "标注"),
    ("10", "60.0"), ("20", "40.0"), ("30", "0.0"),
    # 5 TEXT
    ("0", "TEXT"), ("5", "14"), ("8", "标注"),
    ("10", "10.0"), ("20", "60.0"), ("30", "0.0"), ("40", "5.0"), ("1", "房间 A"),
    # 6 LWPOLYLINE（闭合矩形）
    ("0", "LWPOLYLINE"), ("5", "15"), ("8", "轮廓"), ("90", "4"), ("70", "1"), ("38", "0.0"),
    ("10", "0.0"), ("20", "0.0"),
    ("10", "10.0"), ("20", "0.0"),
    ("10", "10.0"), ("20", "10.0"),
    ("10", "0.0"), ("20", "10.0"),
    # 7 POLYLINE + 2 个 VERTEX + SEQEND（R12 老写法）
    ("0", "POLYLINE"), ("5", "16"), ("8", "轮廓"), ("66", "1"), ("70", "1"),
    ("10", "0.0"), ("20", "0.0"), ("30", "0.0"),
    ("0", "VERTEX"), ("5", "17"), ("8", "轮廓"), ("10", "0.0"), ("20", "5.0"), ("30", "0.0"),
    ("0", "VERTEX"), ("5", "18"), ("8", "轮廓"), ("10", "7.0"), ("20", "5.0"), ("30", "0.0"),
    ("0", "SEQEND"), ("5", "19"), ("8", "轮廓"),
    # 8 自定义未知实体（带 XDATA 与尾部空格的值）
    ("0", "MYAPP_NOTE"), ("5", "1A"), ("8", "标注"), ("70", "1"), ("1", "第三方批注"),
    ("1001", "MYAPP"), ("1000", _TRAILING_SPACE_VALUE), ("1040", "3.5"),
    # 9 代理对象
    ("0", "ACAD_PROXY_ENTITY"), ("5", "1B"), ("8", "0"),
    ("90", "1"), ("91", "2"), ("92", "3"), ("93", "4"), ("310", "0A1B2C3D"),
    ("1001", "ACAD"), ("1000", "代理对象只保留不解释"), ("1040", "1.0"),
    ("0", "ENDSEC"),
    ("0", "EOF"),
]

TOP_LEVEL_ENTITIES = 9        # LINE CIRCLE ARC POINT TEXT LWPOLYLINE POLYLINE MYAPP_NOTE PROXY
CHILD_ENTITIES = 3            # 2 VERTEX + SEQEND
UNKNOWN_ENTITIES = 2          # MYAPP_NOTE + ACAD_PROXY_ENTITY


def build_text(pairs, *, newline="\n", bom=False):
    """把 `(组码, 值)` 对拼成 DXF 文本（组码/值都可以是带空白的字符串）。"""
    body = "".join("%s%s%s%s" % (code, newline, value, newline) for code, value in pairs)
    return ("\ufeff" if bom else "") + body


def broken_text(pairs, *, drop=(), replace=()):
    """造坏样本：按索引丢行 / 换行。"""
    kept = [(code, value) for index, (code, value) in enumerate(pairs) if index not in set(drop)]
    for index, code, value in replace:
        kept[index] = (code, value)
    return build_text(kept)


def find_pair(pairs, code, value):
    """找到某一对的下标（造坏样本时按内容定位，别写死数字）。"""
    for index, item in enumerate(pairs):
        if item == (code, value):
            return index
    raise AssertionError("样本里没有 (%r, %r)" % (code, value))


def without_entity(pairs, entity_type):
    """删掉一个顶层实体（连带它的 VERTEX/SEQEND 之类的子实体）。"""
    followers = {"POLYLINE": {"VERTEX", "SEQEND"}, "INSERT": {"ATTRIB", "SEQEND"}}.get(
        entity_type, set())
    start = find_pair(pairs, "0", entity_type)
    end = start + 1
    while end < len(pairs) and pairs[end][0] != "0":
        end += 1
    while end < len(pairs) and pairs[end][0] == "0" and pairs[end][1] in followers:
        end += 1
        while end < len(pairs) and pairs[end][0] != "0":
            end += 1
    return pairs[:start] + pairs[end:]


class TempCase(unittest.TestCase):
    """每个用例一个临时目录（图纸 + vault），跑完删干净。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="dxf-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def path(self, *parts) -> str:
        target = os.path.join(self.tmp, *parts)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return target

    def write_text(self, rel: str, text: str) -> str:
        target = self.path(rel)
        with open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return target

    def write_sample(self, rel: str = "plan-a.dxf", *, pairs=None, newline="\n", bom=False) -> str:
        return self.write_text(rel, build_text(pairs or SAMPLE_PAIRS, newline=newline, bom=bom))

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def sample_doc(self, **kwargs):
        return dr.read(self.write_sample(**kwargs))

    def new_vault(self, rel="vault"):
        return Vault(self.path(rel))


# --------------------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------------------


class TestReader(TempCase):
    def test_version_and_module_surface(self):
        self.assertEqual(DXF_VERSION, "0.1.0")
        self.assertIs(dr.DxfError, dm.DxfError)
        for name in ("read", "write", "index_drawings"):
            self.assertTrue(hasattr(__import__("dxf"), name), name)

    def test_sections_parsed_in_original_order(self):
        document = self.sample_doc()
        self.assertEqual(document.section_names(),
                         ["HEADER", "TABLES", "MYAPP_PRIVATE", "ENTITIES"])
        self.assertEqual(document.epilogue[-1].value, "EOF")
        self.assertEqual(len(document.preamble), 1)
        self.assertIn("clean-room", document.preamble[0].value)

    def test_unknown_section_is_kept_verbatim(self):
        document = self.sample_doc()
        section = document.section("MYAPP_PRIVATE")
        self.assertIsNotNone(section)
        self.assertEqual(section.body_tags[0].code, 0)
        self.assertEqual(section.body_tags[0].value, "MYSTERY_OBJECT")
        self.assertEqual(section.body_tags[-1].value, "第三方私有段的内容")
        self.assertIn("MYSTERY_OBJECT", section.render())
        self.assertTrue(any("MYAPP_PRIVATE" in item for item in document.warnings))

    def test_entity_count_and_types(self):
        document = self.sample_doc()
        self.assertEqual(document.entity_count(), TOP_LEVEL_ENTITIES)
        self.assertEqual(document.entity_count(include_children=True),
                         TOP_LEVEL_ENTITIES + CHILD_ENTITIES)
        counts = document.entity_type_counts()
        self.assertEqual(counts["LINE"], 1)
        self.assertEqual(counts["CIRCLE"], 1)
        self.assertEqual(counts["ARC"], 1)
        self.assertEqual(counts["TEXT"], 1)
        self.assertEqual(counts["POINT"], 1)
        self.assertEqual(counts["LWPOLYLINE"], 1)
        self.assertEqual(counts["POLYLINE"], 1)
        self.assertNotIn("VERTEX", counts)          # 子实体不占顶层序号
        self.assertEqual(document.entities()[0].index, 1)
        self.assertEqual(document.entities()[-1].index, TOP_LEVEL_ENTITIES)

    def test_unknown_entities_kept_verbatim(self):
        document = self.sample_doc()
        unknown = document.unknown_entities()
        self.assertEqual(len(unknown), UNKNOWN_ENTITIES)
        for entity in unknown:
            self.assertTrue(entity.is_unknown)
            self.assertFalse(entity.is_known)
            self.assertEqual(entity.render(), entity.raw)
            self.assertIn(entity.type, entity.raw)
        note = document.entities_of_type("MYAPP_NOTE")[0]
        self.assertEqual(note.get(1), "第三方批注")
        self.assertEqual(note.layer, "标注")

    def test_proxy_entity_kept_verbatim(self):
        document = self.sample_doc()
        proxies = document.proxy_entities()
        self.assertEqual(len(proxies), 1)
        proxy = proxies[0]
        self.assertTrue(proxy.is_proxy)
        self.assertTrue(proxy.is_unknown)
        self.assertEqual(proxy.get(310), "0A1B2C3D")          # 二进制块按原文留着
        self.assertEqual(proxy.get(90), 1)                    # 代理对象的版本号（整数码）
        self.assertEqual(proxy.render(), proxy.raw)

    def test_xdata_kept(self):
        document = self.sample_doc()
        circle = document.entities_of_type("CIRCLE")[0]
        self.assertTrue(circle.has_xdata)
        self.assertEqual(circle.xdata_apps, ["MYAPP"])
        self.assertEqual([tag.code for tag in circle.xdata], [1001, 1000, 1040])
        self.assertEqual(circle.get(1000), "孔位-A")
        self.assertEqual(circle.get(1040), 12.5)
        self.assertEqual(len(document.xdata_entities()), 3)   # CIRCLE + MYAPP_NOTE + 代理对象

    def test_numeric_representation_preserved(self):
        document = self.sample_doc()
        arc = document.entities_of_type("ARC")[0]
        self.assertEqual(arc.tag(40).raw_value, "2.0e1")      # 原文就是 2.0e1
        self.assertEqual(arc.radius(), 20.0)                  # 解析成 20.0
        circle = document.entities_of_type("CIRCLE")[0]
        self.assertEqual(circle.tag(40).raw_value, "12.50")
        self.assertEqual(circle.radius(), 12.5)
        line = document.entities_of_type("LINE")[0]
        self.assertEqual(line.tag(10).raw_value, "0.0")       # 不会被写成 0
        self.assertEqual(line.tag(11).raw_value, "120.0")
        self.assertEqual(line.tag(11).value, 120.0)

    def test_whitespace_shape_preserved(self):
        document = self.sample_doc()
        point = document.entities_of_type("POINT")[0]
        layer_tag = point.tag(8)
        self.assertEqual(layer_tag.raw_code, _INDENTED_CODE)  # 组码行的前导空格还在
        self.assertEqual(layer_tag.value, "标注")              # 语义取值不受影响
        note = document.entities_of_type("MYAPP_NOTE")[0]
        self.assertEqual(note.tag(1000).raw_value, _TRAILING_SPACE_VALUE)

    def test_line_endings_preserved(self):
        lf = self.sample_doc(rel="lf.dxf", newline="\n")
        self.assertEqual(lf.newline_style, "\n")
        crlf = self.sample_doc(rel="crlf.dxf", newline="\r\n")
        self.assertEqual(crlf.newline_style, "\r\n")
        self.assertEqual(crlf.section_names(), lf.section_names())
        # 末行无行尾也要能读
        text = build_text(SAMPLE_PAIRS, newline="\n").rstrip("\n")
        raw = text.encode("utf-8")
        tail = dr.parse_bytes(raw, path="<inline>")
        self.assertEqual(tail.newline_style, "\n")
        self.assertEqual(tail.line_of(len(tail.lines) - 1).newline, "")
        self.assertEqual(tail.render_bytes(), raw)

    def test_bom_preserved(self):
        raw = build_text(SAMPLE_PAIRS, bom=True).encode("utf-8")
        document = dr.parse_bytes(raw, path="<bom>")
        self.assertEqual(document.bom, "\ufeff")
        self.assertEqual(document.sections[0].name, "HEADER")   # BOM 没混进组码行
        self.assertEqual(document.render_bytes(), raw)

    def test_tables_and_layers(self):
        document = self.sample_doc()
        self.assertEqual(document.layer_names(), ["0", "轮廓", "标注"])
        layer = document.layers()[1]
        self.assertEqual(layer.name, "轮廓")
        self.assertEqual(layer.color, 3)
        self.assertEqual(layer.linetype, "CONTINUOUS")
        self.assertFalse(layer.frozen)
        self.assertFalse(layer.locked)
        self.assertFalse(layer.off)
        self.assertEqual(document.table("LAYER").name, "LAYER")
        self.assertEqual(document.layer_usage(), {"轮廓": 5, "标注": 3, "0": 1})

    def test_header_vars(self):
        document = self.sample_doc()
        header = document.require_section("HEADER")
        variables = header.header_vars()
        self.assertEqual(variables["$ACADVER"][0].value, "AC1009")
        self.assertEqual([tag.code for tag in variables["$INSBASE"]], [10, 20, 30])
        self.assertEqual(document.section("ENTITIES").header_vars(), {})

    def test_gbk_bytes_roundtrip(self):
        """非 UTF-8 图纸：按 GBK 解，且**验证能原样编回**才采用。"""
        raw = build_text(SAMPLE_PAIRS).encode("gbk")
        document = dr.parse_bytes(raw, path="<gbk>")
        self.assertEqual(document.encoding, "gbk")
        self.assertEqual(document.entities_of_type("TEXT")[0].text(), "房间 A")
        self.assertEqual(document.render_bytes(), raw)

    def test_latin1_fallback_is_lossless(self):
        """既不是 UTF-8 也不是 GBK 的字节：落 latin-1，仍然逐字节可逆。"""
        raw = b"0\nSECTION\n2\nENTITIES\n999\n\x81\x8d\x90 weird\n0\nENDSEC\n0\nEOF\n"
        document = dr.parse_bytes(raw, path="<latin1>")
        self.assertEqual(document.encoding, "latin-1")
        self.assertEqual(document.render_bytes(), raw)


class TestReaderErrors(TempCase):
    def test_bad_group_code_raises_with_lineno(self):
        index = find_pair(SAMPLE_PAIRS, "0", "LINE")
        text = broken_text(SAMPLE_PAIRS, replace=[(index, "零", "LINE")])
        path = self.write_text("bad-code.dxf", text)
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        message = str(caught.exception)
        self.assertIn("组码不是整数", message)
        # 每一对占两个物理行 ⇒ 组码行号 = 对下标 * 2 + 1
        self.assertIn("第 %d 行" % (index * 2 + 1), message)
        self.assertIn("'零'", message)

    def test_missing_value_line_raises(self):
        path = self.write_text("odd.dxf", build_text(SAMPLE_PAIRS) + "0\n")
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        message = str(caught.exception)
        self.assertIn("缺少值行", message)
        self.assertIn("第 %d 行" % (len(SAMPLE_PAIRS) * 2 + 1), message)

    def test_truncated_file_raises(self):
        text = build_text(SAMPLE_PAIRS)
        path = self.write_text("cut.dxf", text[: len(text) // 2])
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        message = str(caught.exception)
        self.assertTrue("截断" in message or "缺少 0/EOF" in message or "没有闭合" in message, message)

    def test_missing_eof_raises(self):
        pairs = [item for item in SAMPLE_PAIRS if item != ("0", "EOF")]
        path = self.write_text("no-eof.dxf", build_text(pairs))
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        self.assertIn("0/EOF", str(caught.exception))

    def test_unterminated_section_raises(self):
        pairs = SAMPLE_PAIRS[: find_pair(SAMPLE_PAIRS, "0", "ENDSEC") + 1]
        pairs = pairs + [("0", "SECTION"), ("2", "ENTITIES"), ("0", "LINE"), ("8", "0")]
        path = self.write_text("open-section.dxf", build_text(pairs))
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        self.assertIn("SECTION 没有闭合", str(caught.exception))

    def test_table_without_endtab_raises(self):
        pairs = [item for item in SAMPLE_PAIRS if item != ("0", "ENDTAB")]
        path = self.write_text("no-endtab.dxf", build_text(pairs))
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        self.assertIn("ENDTAB", str(caught.exception))

    def test_empty_and_missing_file_raise(self):
        empty = self.path("empty.dxf")
        with open(empty, "w", encoding="utf-8"):
            pass
        with self.assertRaises(dm.DxfError):
            dr.read(empty)
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(self.path("does-not-exist.dxf"))
        self.assertIn("读不了这个文件", str(caught.exception))

    def test_no_section_raises(self):
        path = self.write_text("no-section.dxf", "999\n只有一段注释\n0\nEOF\n")
        with self.assertRaises(dm.DxfError) as caught:
            dr.read(path)
        self.assertIn("没有任何 SECTION", str(caught.exception))

    def test_trailing_blank_lines_are_kept_not_swallowed(self):
        text = build_text(SAMPLE_PAIRS) + "\n\n"
        raw = text.encode("utf-8")
        document = dr.parse_bytes(raw, path="<blank-tail>")
        self.assertTrue(any("空白" in item for item in document.warnings))
        self.assertEqual(document.render_bytes(), raw)      # 空行没被吞掉

    def test_blank_only_file_raises(self):
        raw = b"\n\n   \n"
        with self.assertRaises(dm.DxfError) as caught:
            dr.parse_bytes(raw, path="<blank>")
        self.assertIn("全是空白行", str(caught.exception))

    def test_tags_after_eof_are_warned_and_kept(self):
        text = build_text(SAMPLE_PAIRS) + "999\nEOF 之后还有东西\n"
        raw = text.encode("utf-8")
        document = dr.parse_bytes(raw, path="<after-eof>")
        self.assertTrue(any("EOF" in item for item in document.warnings))
        self.assertEqual(document.epilogue[-1].value, "EOF 之后还有东西")
        self.assertEqual(document.render_bytes(), raw)


# --------------------------------------------------------------------------------------
# 便利访问器
# --------------------------------------------------------------------------------------


class TestAccessors(TempCase):
    def setUp(self):
        super().setUp()
        self.document = self.sample_doc()

    def entity(self, kind):
        return self.document.entities_of_type(kind)[0]

    def test_line_accessors(self):
        line = self.entity("LINE")
        self.assertEqual(line.start(), (0.0, 0.0, 0.0))
        self.assertEqual(line.end(), (120.0, 0.0, 0.0))
        self.assertEqual(line.points(), [(0.0, 0.0, 0.0), (120.0, 0.0, 0.0)])
        self.assertEqual(line.layer, "轮廓")
        self.assertEqual(line.handle, "10")
        self.assertEqual(line.owner_handle, "")
        self.assertEqual(line.summary(), "LINE 0,0 -> 120,0 [layer=轮廓]")

    def test_circle_and_arc_accessors(self):
        circle = self.entity("CIRCLE")
        self.assertEqual(circle.center(), (30.0, 40.0, 0.0))
        self.assertEqual(circle.radius(), 12.5)
        self.assertEqual(circle.points(), [(30.0, 40.0, 0.0)])
        self.assertEqual(circle.summary(), "CIRCLE center=30,40 r=12.5 [layer=轮廓] [XDATA MYAPP ×3]")
        arc = self.entity("ARC")
        self.assertEqual(arc.center(), (120.0, 40.0, 0.0))
        self.assertEqual(arc.radius(), 20.0)
        self.assertEqual(arc.angles(), (90.0, 270.0))
        self.assertEqual(arc.summary(), "ARC center=120,40 r=20 a=90->270 [layer=轮廓]")
        self.assertIsNone(circle.angles())
        self.assertIsNone(circle.end())

    def test_text_and_point_accessors(self):
        text = self.entity("TEXT")
        self.assertEqual(text.text(), "房间 A")
        self.assertEqual(text.start(), (10.0, 60.0, 0.0))
        self.assertEqual(text.height(), 5.0)
        self.assertEqual(text.summary(), 'TEXT "房间 A" at 10,60 [layer=标注]')
        point = self.entity("POINT")
        self.assertEqual(point.points(), [(60.0, 40.0, 0.0)])
        self.assertEqual(point.summary(), "POINT 60,40 [layer=标注]")
        self.assertEqual(text.text() if point.type == "TEXT" else "", "")

    def test_lwpolyline_accessors(self):
        poly = self.entity("LWPOLYLINE")
        self.assertEqual(poly.vertices(), [(0.0, 0.0, None), (10.0, 0.0, None),
                                           (10.0, 10.0, None), (0.0, 10.0, None)])
        self.assertTrue(poly.closed())
        self.assertEqual(poly.elevation(), 0.0)
        self.assertIn("LWPOLYLINE 4 点", poly.summary())
        self.assertIn("闭合", poly.summary())
        self.assertEqual(poly.get(90), 4)

    def test_polyline_children_and_vertices(self):
        poly = self.entity("POLYLINE")
        self.assertEqual([child.type for child in poly.children], ["VERTEX", "VERTEX", "SEQEND"])
        for child in poly.children:
            self.assertEqual(child.parent_type, "POLYLINE")
        self.assertEqual(poly.vertices(), [(0.0, 5.0, 0.0), (7.0, 5.0, 0.0)])
        self.assertTrue(poly.closed())
        self.assertEqual(len(list(poly.iter_entities())), 4)

    def test_unknown_entity_accessors_do_not_guess(self):
        note = self.entity("MYAPP_NOTE")
        self.assertEqual(note.points(), [])
        self.assertEqual(note.vertices(), [])
        self.assertIsNone(note.radius())
        self.assertIsNone(note.center())
        self.assertEqual(note.text(), "")
        self.assertIn("未知实体 原样保留", note.summary())
        proxy = self.entity("ACAD_PROXY_ENTITY")
        self.assertIn("代理对象 原样保留", proxy.summary())

    def test_as_dict_and_fingerprint(self):
        line = self.entity("LINE")
        payload = line.as_dict()
        self.assertEqual(payload["type"], "LINE")
        self.assertEqual(payload["layer"], "轮廓")
        self.assertEqual(payload["tags"][0], {"code": 0, "value": "LINE"})
        self.assertFalse(payload["unknown"])
        self.assertEqual(payload["summary"], line.summary())
        first = line.fingerprint(drawing="a.dxf", seq=1, source_path="a.dxf")
        self.assertEqual(first, line.fingerprint(drawing="a.dxf", seq=1, source_path="a.dxf"))
        self.assertNotEqual(first, line.fingerprint(drawing="a.dxf", seq=2, source_path="a.dxf"))
        poly = self.entity("POLYLINE")
        before = poly.fingerprint(drawing="a.dxf", seq=1, source_path="a.dxf")
        poly.children[0].tags[3].value = 99.0          # 只动子实体
        self.assertNotEqual(before, poly.fingerprint(drawing="a.dxf", seq=1, source_path="a.dxf"))


# --------------------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------------------


class TestWriter(TempCase):
    def test_write_unmodified_is_byte_identical(self):
        for name, newline in (("lf.dxf", "\n"), ("crlf.dxf", "\r\n")):
            path = self.write_sample(name, newline=newline)
            before = self.read_bytes(path)
            document = dr.read(path)
            report = dw.write(document, self.path(name + ".out"))
            after = self.read_bytes(report["path"])
            self.assertEqual(before, after, name)
            self.assertEqual(report["identical_to_input"], True)
            self.assertEqual(report["modified"], False)
            self.assertEqual(report["changed_line_count"], 0)
            self.assertEqual(report["bytes"], len(before))

    def test_write_unmodified_never_touches_lines(self):
        path = self.write_sample()
        document = dr.read(path)
        self.assertEqual(document.render_text(), document.bom + document.input_text)
        self.assertEqual(document.raw_lines[0], document.lines[0].render())
        checked = dw.verify_byte_identity(document)
        self.assertEqual(checked["ok"], 1)
        self.assertEqual(checked["sha256_in"], checked["sha256_out"])

    def test_write_then_read_then_write_is_stable(self):
        path = self.write_sample()
        first = self.path("first.dxf")
        second = self.path("second.dxf")
        dw.write(dr.read(path), first)
        dw.write(dr.read(first), second)
        self.assertEqual(self.read_bytes(path), self.read_bytes(first))
        self.assertEqual(self.read_bytes(first), self.read_bytes(second))

    def test_modify_one_entity_only_that_slice_changes(self):
        path = self.write_sample()
        document = dr.read(path)
        line = document.entities_of_type("LINE")[0]
        start, end = line.line_span()
        line.tag(11).value = 99.0                     # 只改终点 X

        target = self.path("edited.dxf")
        report = dw.write(document, target)
        self.assertEqual(report["modified"], True)
        self.assertEqual(report["modified_entities"], 1)
        self.assertEqual(report["changed_line_count"], 1)

        before = document.raw_lines
        after = [item.render() for item in document.lines]
        diff = [index for index in range(len(before)) if before[index] != after[index]]
        self.assertEqual(diff, [line.tag(11).value_line.index])
        for index in diff:
            self.assertGreaterEqual(index, start)
            self.assertLessEqual(index, end)

        # 重新读回来：除了被改的那个实体，其余实体的**原文切片**一字未动
        edited = dr.read(target)
        self.assertEqual(edited.entities_of_type("LINE")[0].render(),
                         line.render().replace("120.0", "120.0"))  # 结构没散
        self.assertEqual(edited.entities_of_type("LINE")[0].get(11), 99.0)
        originals = document.entities()
        for index, entity in enumerate(edited.entities()):
            if index == 0:
                continue
            self.assertEqual(entity.render(), originals[index].raw,
                             "第 %d 个实体不该变" % (index + 1))
        self.assertEqual(edited.entities_of_type("MYAPP_NOTE")[0].raw,
                         document.entities_of_type("MYAPP_NOTE")[0].raw)

    def test_modify_value_only_rewrites_the_value_line(self):
        document = self.sample_doc()
        circle = document.entities_of_type("CIRCLE")[0]
        radius_tag = circle.tag(40)
        code_line = radius_tag.raw_code
        newline = radius_tag.newline
        before = circle.render()
        radius_tag.value = 30
        self.assertEqual(radius_tag.raw_value, "30.0")     # int 进浮点码 ⇒ 带小数点
        self.assertEqual(radius_tag.raw_code, code_line)
        self.assertEqual(radius_tag.newline, newline)
        self.assertTrue(circle.modified)
        old_lines = before.splitlines()
        new_lines = circle.render().splitlines()
        self.assertEqual(len(old_lines), len(new_lines))    # 行数不变（没有增删行）
        self.assertEqual([index for index in range(len(old_lines))
                          if old_lines[index] != new_lines[index]],
                         [radius_tag.value_line.index - circle.tags[0].code_line.index])

    def test_write_float_keeps_decimal_point(self):
        document = self.sample_doc()
        line = document.entities_of_type("LINE")[0]
        line.tag(10).value = 1.0
        self.assertEqual(line.tag(10).raw_value, "1.0")    # 不是 "1"
        line.tag(20).value = 0.5
        self.assertEqual(line.tag(20).raw_value, "0.5")
        line.tag(30).value = -3
        self.assertEqual(line.tag(30).raw_value, "-3.0")

    def test_write_rejects_unencodable_text_loudly(self):
        document = dr.parse_bytes(
            b"0\nSECTION\n2\nENTITIES\n999\n\x81 ascii-ish\n0\nENDSEC\n0\nEOF\n", path="<latin1>")
        self.assertEqual(document.encoding, "latin-1")
        document.epilogue[0].value = "中文写不进 latin-1"
        with self.assertRaises(dm.DxfError) as caught:
            dw.write(document, self.path("boom.dxf"))
        self.assertIn("写不回", str(caught.exception))
        self.assertFalse(os.path.exists(self.path("boom.dxf")))

    def test_atomic_write_leaves_no_partial_file(self):
        document = self.sample_doc()
        target = self.path("atomic.dxf")
        report = dw.write(document, target)
        self.assertEqual(report["atomic"], True)
        leftovers = [name for name in os.listdir(os.path.dirname(target))
                     if name.startswith(".tmp-dxf-")]
        self.assertEqual(leftovers, [])


# --------------------------------------------------------------------------------------
# 索引层
# --------------------------------------------------------------------------------------


class TestIndex(TempCase):
    def drawing_dir(self, *, count=1, newline="\n"):
        root = self.path("drawings")
        os.makedirs(root, exist_ok=True)
        names = []
        for index in range(count):
            name = "plan-%s.dxf" % chr(ord("a") + index)
            with open(os.path.join(root, name), "w", encoding="utf-8", newline="") as handle:
                handle.write(build_text(SAMPLE_PAIRS, newline=newline))
            names.append(name)
        return root, names

    def test_index_first_run_then_idempotent(self):
        root, _names = self.drawing_dir(count=2)
        with self.new_vault() as vault:
            first = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(first.discovered, 2)
            self.assertEqual(first.indexed, 2)
            self.assertEqual(first.skipped, 0)
            self.assertEqual(first.imported, 2 * TOP_LEVEL_ENTITIES)
            self.assertEqual(first.updated, 0)
            self.assertEqual(first.drawings, 2)
            self.assertEqual(first.entities, 2 * TOP_LEVEL_ENTITIES)
            self.assertEqual(first.unknown_entities, 2 * UNKNOWN_ENTITIES)
            self.assertEqual(first.entities_by_type["LINE"], 2)
            self.assertEqual(first.layers, 3)
            self.assertTrue(first.digest_changed)

            before = vault.content_digest()
            second = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.updated, 0)
            self.assertEqual(second.writes, 0)
            self.assertEqual(second.chats_written, 0)
            self.assertEqual(second.unchanged, 2 * TOP_LEVEL_ENTITIES)
            self.assertFalse(second.digest_changed)
            self.assertEqual(vault.content_digest(), before)

    def test_index_message_ids_and_ext(self):
        root, _names = self.drawing_dir(count=1)
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            chats = [chat for chat in vault.iter_chats() if chat["id"].startswith("dxf:")]
            self.assertEqual([chat["id"] for chat in chats], ["dxf:plan-a.dxf"])
            self.assertEqual(chats[0]["title"], "plan-a.dxf")
            file_mtime = di.load_drawings(vault)["plan-a.dxf"]["mtime"]
            self.assertEqual(chats[0]["updated_at"], file_mtime)
            messages = list(vault.iter_messages("dxf:plan-a.dxf"))
            self.assertEqual(len(messages), TOP_LEVEL_ENTITIES)
            first = messages[0]
            self.assertEqual(first["id"], "ent:plan-a.dxf/1")
            self.assertEqual(first["kind"], "text")
            self.assertEqual(first["text"], "LINE 0,0 -> 120,0 [layer=轮廓]")
            self.assertEqual(first["sent_at"], file_mtime)      # 时间戳 = 文件 mtime
            ext = first["ext"]
            for key in di.EXT_REQUIRED_KEYS:
                self.assertIn(key, ext)
            self.assertEqual(ext["type"], "LINE")
            self.assertEqual(ext["layer"], "轮廓")
            self.assertEqual(ext["source_path"], "plan-a.dxf")
            self.assertEqual(ext["mtime"], file_mtime)
            self.assertIn({"code": 11, "value": 120.0}, ext["tags"])
            self.assertEqual(ext["indexed_at"], STAMP)
            self.assertEqual([item["id"] for item in messages][-1], "ent:plan-a.dxf/9")
            note = [item for item in messages if item["ext"]["type"] == "MYAPP_NOTE"][0]
            self.assertTrue(note["ext"]["unknown"])
            self.assertEqual(note["ext"]["xdata_apps"], ["MYAPP"])
            proxy = [item for item in messages if item["ext"]["type"] == "ACAD_PROXY_ENTITY"][0]
            self.assertTrue(proxy["ext"]["proxy"])
            poly = [item for item in messages if item["ext"]["type"] == "POLYLINE"][0]
            self.assertEqual(len(poly["ext"]["children"]), 3)

    def test_index_delete_tombstones_and_restore_revives(self):
        root, names = self.drawing_dir(count=2)
        with self.new_vault() as vault:
            first = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(first.drawings, 2)
            self.assertEqual(first.deleted_drawings, 0)

            # 删掉一张图纸 ⇒ 会话 + 它的实体全部墓碑
            os.remove(os.path.join(root, names[1]))
            second = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(second.deleted_drawings, 1)
            self.assertEqual(second.deleted_entities, TOP_LEVEL_ENTITIES)
            self.assertEqual(second.drawings, 1)
            self.assertEqual(second.tombstoned_drawings, 1)
            self.assertEqual(second.tombstoned_entities, TOP_LEVEL_ENTITIES)
            # 三次索引：已经是墓碑 ⇒ 不再重写（幂等）
            again = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(again.deleted_drawings, 0)
            self.assertEqual(again.deleted_entities, 0)
            self.assertEqual(again.writes, 0)
            # 活实体检索里看不到墓碑
            self.assertEqual(len(di.entities(vault)), TOP_LEVEL_ENTITIES)
            self.assertEqual(len(di.entities(vault, include_deleted=True)),
                             2 * TOP_LEVEL_ENTITIES)

            # 文件回来 ⇒ 清墓碑（restored）
            with open(os.path.join(root, names[1]), "w", encoding="utf-8", newline="") as handle:
                handle.write(build_text(SAMPLE_PAIRS))
            third = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(third.restored_drawings, 1)
            self.assertEqual(third.restored, TOP_LEVEL_ENTITIES)
            self.assertEqual(third.drawings, 2)
            self.assertEqual(third.tombstoned_drawings, 0)

    def test_trailing_entity_removed_from_live_drawing_is_tombstoned(self):
        """图纸还在、末尾的实体没了 ⇒ 那条消息打墓碑（不留 Ghost）。"""
        root, names = self.drawing_dir(count=1)
        path = os.path.join(root, names[0])
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            shortened = without_entity(SAMPLE_PAIRS, "ACAD_PROXY_ENTITY")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(build_text(shortened))
            second = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(second.deleted_entities, 1)
            self.assertEqual(second.updated, 0)
            self.assertEqual(second.entities, TOP_LEVEL_ENTITIES - 1)
            self.assertEqual(len(di.entities(vault, "ACAD_PROXY_ENTITY")), 0)
            self.assertEqual(len(di.entities(vault, "ACAD_PROXY_ENTITY", include_deleted=True)), 1)

    def test_middle_removal_renumbers_following_ids(self):
        """**已知代价**：id 用序号，中间删一个 ⇒ 它后面的实体全部改号（记成 updated）。

        这不是 bug 而是"id = 序号"的直接后果；这里把它钉成断言，别让它悄悄漂。
        """
        root, names = self.drawing_dir(count=1)
        path = os.path.join(root, names[0])
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            shortened = without_entity(SAMPLE_PAIRS, "MYAPP_NOTE")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(build_text(shortened))
            second = di.index_drawings(vault, root, indexed_at=STAMP)
            self.assertEqual(second.updated, 1)                  # 代理对象从 9 号变成 8 号
            self.assertEqual(second.deleted_entities, 1)         # 老 9 号没人认领 ⇒ 墓碑
            self.assertEqual(second.entities, TOP_LEVEL_ENTITIES - 1)
            self.assertEqual(di.entities(vault)[-1]["seq"], 8)
            self.assertEqual(di.entities(vault)[-1]["type"], "ACAD_PROXY_ENTITY")

    def test_entities_filter_by_type_and_drawing(self):
        root, _names = self.drawing_dir(count=2)
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            lines = di.entities(vault, "LINE")
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0]["type"], "LINE")
            self.assertEqual(lines[0]["summary"], "LINE 0,0 -> 120,0 [layer=轮廓]")
            self.assertEqual(len(di.entities(vault, "line")), 2)          # 大小写不敏感
            self.assertEqual(len(di.entities(vault, "LINE", drawing="plan-a.dxf")), 1)
            self.assertEqual(len(di.entities(vault, unknown_only=True)), 2 * UNKNOWN_ENTITIES)
            self.assertEqual(len(di.entities(vault, limit=3)), 3)
            self.assertEqual(len(di.entities(vault, "NOPE")), 0)
            with self.assertRaises(di.DxfIndexError):
                di.entities(vault, limit=-1)
            self.assertEqual(di.entity_types(vault)["TEXT"], 2)

    def test_layers_and_stats(self):
        root, _names = self.drawing_dir(count=2)
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            table = di.layers(vault)
            names = [row["layer"] for row in table]
            self.assertEqual(names, ["轮廓", "标注", "0"])
            outline = table[0]
            self.assertEqual(outline["entities"], 10)        # 每张图 5 个轮廓实体
            self.assertEqual(outline["drawings"], ["plan-a.dxf", "plan-b.dxf"])
            self.assertEqual(outline["types"]["LINE"], 2)
            self.assertEqual([row["layer"] for row in di.layers(vault, drawing="plan-a.dxf")],
                             ["轮廓", "标注", "0"])
            info = di.stats(vault)
            self.assertEqual(info["drawings"], 2)
            self.assertEqual(info["entities"], 2 * TOP_LEVEL_ENTITIES)
            self.assertEqual(info["unknown_entities"], 2 * UNKNOWN_ENTITIES)
            self.assertEqual(info["proxy_entities"], 2)
            self.assertEqual(info["xdata_entities"], 2 * 3)
            self.assertEqual(info["child_entities"], 2 * CHILD_ENTITIES)
            self.assertEqual(info["indexed_at"], STAMP)
            self.assertEqual(len(info["source_paths"]), 2)
            self.assertGreater(info["entity_raw_bytes"], 0)
            self.assertIn("messages", info["vault_stats"])

    def test_empty_vault_stats_say_not_measured(self):
        with self.new_vault() as vault:
            info = di.stats(vault)
            self.assertEqual(info["drawings"], 0)
            self.assertEqual(info["entities"], 0)
            self.assertIsNone(info["indexed_at"])          # "没采到" ≠ "真为零"

    def test_search_finds_summary_and_chinese_layer(self):
        root, _names = self.drawing_dir(count=1)
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            hits = di.search_entities(vault, "LINE")
            self.assertGreaterEqual(len(hits), 1)
            self.assertEqual(hits[0]["drawing"], "plan-a.dxf")
            self.assertEqual(hits[0]["type"], "LINE")
            chinese = di.search_entities(vault, "轮廓")
            self.assertGreaterEqual(len(chinese), 1)
            self.assertEqual(di.search_entities(vault, ""), [])
            self.assertEqual(di.search_entities(vault, "LINE", limit=0), [])
            with self.assertRaises(di.DxfIndexError):
                di.search_entities(vault, "LINE", limit=True)

    def test_scan_skips_broken_and_duplicate_with_reasons(self):
        root = self.path("mixed")
        os.makedirs(root, exist_ok=True)
        good = build_text(SAMPLE_PAIRS)
        with open(os.path.join(root, "ok.dxf"), "w", encoding="utf-8", newline="") as handle:
            handle.write(good)
        with open(os.path.join(root, "broken.dxf"), "w", encoding="utf-8", newline="") as handle:
            handle.write(good[: len(good) // 3])
        os.makedirs(os.path.join(root, "sub"), exist_ok=True)
        with open(os.path.join(root, "sub", "ok.dxf"), "w", encoding="utf-8", newline="") as handle:
            handle.write(good)
        report = di.scan_drawings(root)
        self.assertEqual(report.discovered, 3)
        self.assertEqual([drawing.name for drawing in report.drawings], ["ok.dxf"])
        reasons = sorted(reason for _rel, reason in report.skipped)
        self.assertEqual(reasons, ["duplicate_drawing", "parse_error"])
        self.assertTrue(any("解析失败" in item for item in report.warnings))
        with self.assertRaises(di.DxfIndexError):
            di.scan_drawings(self.path("nope"))

    def test_load_drawings_view(self):
        root, _names = self.drawing_dir(count=1)
        with self.new_vault() as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
            views = di.load_drawings(vault)
            self.assertEqual(list(views), ["plan-a.dxf"])
            view = views["plan-a.dxf"]
            self.assertEqual(view["encoding"], "utf-8")
            self.assertEqual(view["newline"], "\n")
            self.assertEqual(view["sections"], ["HEADER", "TABLES", "MYAPP_PRIVATE", "ENTITIES"])
            self.assertEqual(len(view["entities"]), TOP_LEVEL_ENTITIES)
            self.assertEqual(view["entities"][1]["type"], "LINE")
            self.assertEqual(view["entities"][2]["type"], "CIRCLE")
            self.assertEqual([item["name"] for item in di.drawings(vault)], ["plan-a.dxf"])

    def test_fcx_export_import_reexport_is_byte_identical(self):
        """复用底座的 `.fcx`：导出 → 导入空库 → 再导出，两个归档**逐字节相同**。"""
        root, _names = self.drawing_dir(count=2)
        with self.new_vault("vault-a") as vault_a:
            di.index_drawings(vault_a, root, indexed_at=STAMP)
            from_a = self.path("a.fcx")
            export_fcx(vault_a, from_a, export_id="same", exported_at=STAMP)
            with self.new_vault("vault-b") as vault_b:
                stats = import_fcx(vault_b, from_a)
                self.assertEqual(stats.failed, 0)
                self.assertGreater(stats.imported, 0)
                from_b = self.path("b.fcx")
                export_fcx(vault_b, from_b, export_id="same", exported_at=STAMP)
                second = import_fcx(vault_b, from_a)
                self.assertEqual(second.imported, 0)
                self.assertIn("messages", di.stats(vault_b)["vault_stats"])
        self.assertEqual(self.read_bytes(from_a), self.read_bytes(from_b))

    def test_base_methods_are_the_frozen_ones(self):
        frozen = {
            "Vault.__init__", "Vault.close", "Vault.__enter__", "Vault.__exit__",
            "Vault.upsert_chat", "Vault.upsert_message", "Vault.iter_chats",
            "Vault.iter_messages", "Vault.search", "Vault.stats", "Vault.content_digest",
            "fcx.export_fcx", "fcx.import_fcx", "fcx.FcxArchive", "fcx.ImportStats",
        }
        self.assertTrue(frozen.issubset(set(di.BASE_METHODS)))
        self.assertEqual(len(di.BASE_METHODS), len(set(di.BASE_METHODS)))
        from freechat_vault import fcx as fcx_mod
        from freechat_vault import store as store_mod
        for name in di.BASE_METHODS:
            head, _, attribute = name.partition(".")
            if head == "Vault":
                self.assertTrue(hasattr(Vault, attribute), name)
            elif head == "fcx":
                self.assertTrue(hasattr(fcx_mod, attribute), name)
            elif head == "store":
                self.assertTrue(hasattr(store_mod, attribute), name)
            else:                                             # pragma: no cover - 名单写错才会到
                self.fail("BASE_METHODS 里有认不出的前缀：%s" % name)

    def test_base_methods_match_real_usage(self):
        """`BASE_METHODS` 不能是"写着好看"的清单：源码里真出现过才算数。"""
        source = inspect.getsource(di)
        tree = ast.parse(source)
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "vault" and node.attr in {
                    "upsert_chat", "upsert_message", "iter_chats", "iter_messages",
                    "search", "stats", "content_digest", "close", "__enter__", "__exit__",
                }:
                    used.add("Vault." + node.attr)
        for name in sorted(used):
            self.assertIn(name, di.BASE_METHODS, "%s 用了但没登记进 BASE_METHODS" % name)
        for name in ("Vault.upsert_chat", "Vault.upsert_message", "Vault.iter_messages",
                     "Vault.search", "Vault.content_digest"):
            self.assertIn(name, used, "BASE_METHODS 里登记了 %s，但源码里没用到" % name)

    def test_index_writes_only_inside_the_vault_directory(self):
        """索引只往 vault 目录里写（SQLite + blobs），不往图纸目录或包源码目录里写。"""
        root, names = self.drawing_dir(count=1)
        before = sorted(os.listdir(root))
        before_bytes = self.read_bytes(os.path.join(root, names[0]))
        vault_dir = self.path("vault")
        with Vault(vault_dir) as vault:
            di.index_drawings(vault, root, indexed_at=STAMP)
        self.assertTrue(os.path.isfile(os.path.join(vault_dir, DB_FILENAME)))
        self.assertEqual(sorted(os.listdir(root)), before)          # 图纸目录没被污染
        self.assertEqual(self.read_bytes(os.path.join(root, names[0])), before_bytes)
        package_dir = os.path.dirname(os.path.abspath(di.__file__))
        self.assertTrue(package_dir.endswith(os.path.join("packages", "dxf", "dxf")))
        self.assertFalse(os.path.exists(os.path.join(package_dir, "vault.sqlite3")))


if __name__ == "__main__":                              # pragma: no cover
    unittest.main(verbosity=2)
