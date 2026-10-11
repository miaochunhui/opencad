"""cadcore.io.plot_config 的单测：三个打印配置格式的真读写 + 阴性对照。

覆盖的承重判据（每条都真跑真件，不桩掉解析器）：

1. **逐字段往返**：.ctb（255 条 ACI）/ .stb（命名样式）/ .pc3（文本）三形态
   dumps → loads 后 to_dict() 与构造时**逐字段相等**；
2. **字节幂等**：dumps(loads(raw)) 是不动点，且 dumps(loads(dumps(cfg))) == dumps(cfg)；
3. **头是真的**：写出的 .ctb/.stb 前 48 字节是 PIA 魔数，第 48..60 字节三个小端 uint32
   与 zlib 流**现算**对得上（adler32 / 未压缩长度 / 压缩长度）；
4. **不丢字段**：CTB/STB 顶层未知项、样式项未知键、PC3 未知段原样保留并重现；
5. **畸形输入必须报红**：坏魔数 / 截断 / 校验不符 / 长度不符 / 尾随字节 / zlib 坏流 /
   缺 NUL / 语法错 / 缺必填键 / 字段越界 —— 逐条断言 PlotConfigError 与 reason；
6. **阴性对照（夹具自证）**：故意改坏一个字段后，读回的 to_dict 必须与原值不同
   —— 证明比对不是"恒真"。
"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG = str(_HERE.parent.parent)                      # packages/cadcore
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from cadcore.io import plot_config as pc  # noqa: E402

CJK_NAME = "图层样式一"
MAGIC_CTB = b"PIAFILEVERSION_2.0,CTBVER1,compress\r\npmzlibcodec"
MAGIC_STB = b"PIAFILEVERSION_2.0,STBVER1,compress\r\npmzlibcodec"


def _ctb_fixture() -> pc.PlotStyleTable:
    """一份非平凡的 CTB 样本：改过颜色/线宽/线型/淡显/端点连接填充 + 自定义线宽表。"""
    styles = (
        pc.PlotStyle(index=0, name="Black", localized_name="Black", description="pen 1",
                     color=pc.color_rgb((0, 0, 0)), mode_color=pc.color_rgb((0, 0, 0)),
                     color_policy=pc.DITHERING_ON, physical_pen_number=1, virtual_pen_number=1,
                     screen=100, linepattern_size=0.5, linetype=0, adaptive_linetype=True,
                     lineweight=8, fill_style=pc.FILL_STYLE_SOLID, end_style=pc.END_STYLE_ROUND,
                     join_style=pc.JOIN_STYLE_ROUND),
        pc.PlotStyle(index=1, name="Red50", localized_name="Red50", description="screened red",
                     color=pc.color_rgb((255, 0, 0)), mode_color=pc.color_rgb((255, 0, 0)),
                     color_policy=pc.DITHERING_ON | pc.GRAYSCALE_ON, physical_pen_number=2,
                     virtual_pen_number=0, screen=50, linepattern_size=0.35, linetype=1,
                     adaptive_linetype=False, lineweight=13,
                     fill_style=pc.FILL_STYLE_OBJECT, end_style=pc.END_STYLE_BUTT,
                     join_style=pc.JOIN_STYLE_MITER),
        pc.PlotStyle(index=254, name=CJK_NAME, localized_name=CJK_NAME, description="utf-8 name",
                     color=pc.color_rgb((12, 34, 56)), mode_color=pc.color_rgb((12, 34, 56)),
                     color_policy=pc.DITHERING_ON, screen=25, linepattern_size=1.25, linetype=31,
                     adaptive_linetype=True, lineweight=0),
    )
    weights = list(pc.default_lineweights()) + [1.37]
    return pc.new_ctb(description="office standard", scale_factor=1.0, apply_factor=False,
                      lineweights=weights, styles=styles)


def _stb_fixture() -> pc.PlotStyleTable:
    styles = (
        pc.PlotStyle(index=0, name="Plot_1", localized_name="Plot_1", description="wide",
                     color=pc.color_rgb((0, 0, 0)), mode_color=pc.color_rgb((0, 0, 0)),
                     color_policy=pc.DITHERING_ON | pc.NAMED_COLOR, screen=100,
                     linepattern_size=0.6, linetype=0, adaptive_linetype=True, lineweight=15,
                     fill_style=pc.FILL_STYLE_SOLID, end_style=pc.END_STYLE_SQUARE,
                     join_style=pc.JOIN_STYLE_BEVEL),
        pc.PlotStyle(index=0, name=CJK_NAME, localized_name=CJK_NAME, description="named cjk",
                     color=pc.color_rgb((200, 100, 50)), mode_color=pc.color_rgb((200, 100, 50)),
                     color_policy=pc.GRAYSCALE_ON, screen=60, linepattern_size=0.2, linetype=2,
                     adaptive_linetype=False, lineweight=11),
    )
    return pc.new_stb(description="named table", scale_factor=2.0, apply_factor=True,
                      styles=styles)


def _pc3_fixture() -> pc.PlotterConfig:
    return pc.new_pc3(device="HP LaserJet.pc3", port="system",
                      media=("A4", "A3", "Letter"), default_media="A3",
                      linetypes=("CONTINUOUS", "DASHED", "DASHDOT"),
                      color_map=(
                          pc.ColorMapping(aci=1, color="RGB:0,0,0", lineweight_index=8,
                                          linetype_index=0, screening=100, dither=True),
                          pc.ColorMapping(aci=2, color="RGB:255,0,0", lineweight_index=13,
                                          linetype_index=1, screening=45, dither=False,
                                          grayscale=True),
                          pc.ColorMapping(aci=7, color="OBJECT", lineweight_index=0,
                                          linetype_index=2, screening=100, dither=True),
                          pc.ColorMapping(aci=9, color="ACI:9", lineweight_index=15,
                                          linetype_index=1, screening=80, dither=True),
                      ))


def _rebuild(raw: bytes, mutate) -> bytes:
    """把容器解开、对文本载荷做 mutate、再按同一 tag 包回去（只用于阴性对照夹具）。"""
    tag, payload, _info = pc.unwrap_container(raw)
    text = payload.decode("utf-8")
    return pc.wrap_container(mutate(text), tag)


class RoundTripTest(unittest.TestCase):
    """三条形态的逐字段往返 + 字节幂等（判据 1/2）。"""

    def test_ctb_roundtrip_all_fields(self):
        cfg = _ctb_fixture()
        data = pc.dumps(cfg)
        back = pc.loads(data)
        self.assertEqual(back.payload_dict(), cfg.payload_dict())
        self.assertEqual(len(back.styles), pc.STYLE_COUNT)
        self.assertEqual(back.styles[0].name, "Black")
        self.assertEqual(back.styles[0].rgb, (0, 0, 0))
        self.assertEqual(back.lineweight_mm(back.style_for_aci(2)), 0.50)
        self.assertEqual(back.resolve(2)["screen"], 50)
        self.assertTrue(back.resolve(2)["grayscale"])
        self.assertEqual(back.style_for_aci(255).name, CJK_NAME)

    def test_stb_roundtrip_named_styles(self):
        cfg = _stb_fixture()
        back = pc.loads(pc.dumps(cfg))
        self.assertEqual(back.payload_dict(), cfg.payload_dict())
        self.assertEqual(back.kind, "stb")
        self.assertEqual([style.name for style in back.styles], ["Normal", "Plot_1", CJK_NAME])
        self.assertEqual(back.style_by_name(CJK_NAME).screen, 60)
        self.assertEqual(back.lineweight_mm(back.style_by_name("Plot_1")), 0.60)

    def test_pc3_roundtrip_text(self):
        cfg = _pc3_fixture()
        data = pc.dumps(cfg)
        self.assertTrue(data.startswith(b"[Version]\n"))
        self.assertFalse(data.startswith(pc.MAGIC_PREFIX))
        back = pc.loads(data)
        self.assertEqual(back.payload_dict(), cfg.payload_dict())
        self.assertEqual(back.device, "HP LaserJet.pc3")
        self.assertEqual(back.default_media, "A3")
        mapping = back.mapping_for_aci(2)
        self.assertEqual(mapping.color_rgb(), (255, 0, 0))
        self.assertEqual(back.lineweight_mm(mapping), 0.50)
        self.assertEqual(back.linetype_name(mapping), "DASHED")
        self.assertEqual(back.mapping_for_aci(7).color, "OBJECT")

    def test_byte_idempotent(self):
        for cfg in (_ctb_fixture(), _stb_fixture(), _pc3_fixture()):
            first = pc.dumps(cfg)
            second = pc.dumps(pc.loads(first))
            self.assertEqual(first, second, msg="dumps is not a fixpoint for %s" % cfg.kind)

    def test_canonical_fixpoint_of_raw_payload(self):
        cfg = _ctb_fixture()
        raw = pc.dumps(cfg)
        canonical = pc.dumps(pc.loads(raw))
        again = pc.dumps(pc.loads(canonical))
        self.assertEqual(canonical, again)


class HeaderTest(unittest.TestCase):
    """判据 3：写出的头是真的（魔数 + 三个小端字段与 zlib 流现算对得上）。"""

    def test_ctb_header_fields_are_real(self):
        raw = pc.dumps(_ctb_fixture())
        self.assertEqual(raw[:48], MAGIC_CTB)
        adler, plain_len, comp_len = struct.unpack("<LLL", raw[48:60])
        comp = raw[60:]
        self.assertEqual(comp_len, len(comp))
        plain = zlib.decompress(comp)
        self.assertEqual(plain_len, len(plain))
        self.assertEqual(adler, zlib.adler32(comp))
        self.assertTrue(plain.endswith(b"\x00"))
        text = plain[:-1].decode("utf-8")
        self.assertIn('description="office standard', text)
        self.assertIn("plot_style{", text)
        self.assertIn("custom_lineweight_table{", text)
        self.assertIn("linetype=", text)
        self.assertIn("lineweight=", text)

    def test_stb_header_tag(self):
        raw = pc.dumps(_stb_fixture())
        self.assertEqual(raw[:48], MAGIC_STB)
        tag, payload, info = pc.unwrap_container(raw)
        self.assertEqual(tag, "STBVER1")
        self.assertEqual(info.adler_basis, "compressed")
        self.assertEqual(info.length_basis, "bytes")
        self.assertTrue(payload.startswith(b'description="named table'))

    def test_container_info_reports_unmeasured_as_na(self):
        cfg = _pc3_fixture()
        back = pc.loads(pc.dumps(cfg))
        self.assertEqual(back.container.container, "text")
        self.assertEqual(back.container.adler32, -1)
        self.assertEqual(back.container.adler_basis, "NA")
        self.assertEqual(back.container.length_basis, "NA")


class FidelityTest(unittest.TestCase):
    """判据 4：不丢字段（未知项/未知键/未知段原样重现）。"""

    def test_unknown_top_level_item_preserved(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text + 'legacy_vendor_key="keep me\n')
        back = pc.loads(patched)
        self.assertIn("legacy_vendor_key", "\n".join(back.extra))
        rewritten = pc.dumps(back)
        self.assertIn(b"legacy_vendor_key=", pc.unwrap_container(rewritten)[1])
        self.assertEqual(rewritten, pc.dumps(pc.loads(rewritten)))

    def test_unknown_style_key_preserved(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("  color_policy=1\n",
                                                          "  color_policy=1\n  vendor_tag=42\n", 1))
        back = pc.loads(patched)
        self.assertIn("vendor_tag=42", [line.strip() for line in back.styles[0].extra])
        self.assertIn(b"vendor_tag=42", pc.unwrap_container(pc.dumps(back))[1])

    def test_unknown_pc3_section_preserved(self):
        raw = pc.dumps(_pc3_fixture()).decode("utf-8")
        patched = (raw + "[VendorCalibration]\nOffsetX=12\nOffsetY=-3\n").encode("utf-8")
        back = pc.loads(patched)
        self.assertEqual(back.extra_sections,
                         ("[VendorCalibration]", "OffsetX=12", "OffsetY=-3"))
        rewritten = pc.dumps(back)
        self.assertIn(b"[VendorCalibration]", rewritten)
        self.assertEqual(rewritten, pc.dumps(pc.loads(rewritten)))

    def test_defaulted_fields_are_recorded(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("apply_factor=FALSE\n", "")
                           .replace("description=", "unused_note=", 1))
        back = pc.loads(patched)
        self.assertIn("apply_factor", back.defaulted_fields)
        self.assertEqual(back.apply_factor, False)
        self.assertEqual(back.description, "")


class NegativeControlTest(unittest.TestCase):
    """判据 5：畸形输入逐条报红（reason 机器可读），绝不返回空模型。"""

    def _assert_reason(self, data: bytes, reason: str, *, kind: str | None = None):
        with self.assertRaises(pc.PlotConfigError) as ctx:
            pc.loads(data, kind=kind)
        self.assertEqual(ctx.exception.reason, reason, msg=str(ctx.exception))

    def test_bad_magic(self):
        raw = bytearray(pc.dumps(_ctb_fixture()))
        raw[3:6] = b"XXX"
        self._assert_reason(bytes(raw), "bad_magic", kind="ctb")

    def test_truncated(self):
        raw = pc.dumps(_ctb_fixture())
        self._assert_reason(raw[:40], "truncated")
        self._assert_reason(raw[:len(raw) - 5], "length_mismatch")

    def test_checksum_tampered(self):
        raw = bytearray(pc.dumps(_ctb_fixture()))
        raw[48] ^= 0xFF
        self._assert_reason(bytes(raw), "checksum_mismatch")

    def test_length_field_tampered(self):
        raw = bytearray(pc.dumps(_ctb_fixture()))
        adler, plain_len, comp_len = struct.unpack("<LLL", bytes(raw[48:60]))
        raw[52:56] = struct.pack("<L", plain_len + 7)
        self._assert_reason(bytes(raw), "length_mismatch")

    def test_trailing_byte(self):
        raw = pc.dumps(_ctb_fixture()) + b"x"
        self._assert_reason(raw, "trailing_data")

    def test_corrupt_zlib_stream(self):
        raw = bytearray(pc.dumps(_ctb_fixture()))
        raw[80] ^= 0xFF
        with self.assertRaises(pc.PlotConfigError) as ctx:
            pc.loads(bytes(raw))
        self.assertIn(ctx.exception.reason, ("decompress_failed", "checksum_mismatch"))

    def test_missing_nul_terminator(self):
        raw = pc.dumps(_ctb_fixture())
        tag, payload, _info = pc.unwrap_container(raw)
        rebuilt = pc.wrap_container(payload.decode("utf-8")[:-1], tag)
        # 载荷本身不以 NUL 结束时，wrap 仍会补 NUL；这里直接手工去掉尾 NUL 再包。
        body = payload  # already NUL-stripped by unwrap_container
        comp = zlib.compress(body)
        header = struct.pack("<LLL", zlib.adler32(comp), len(body), len(comp))
        self._assert_reason(MAGIC_CTB + header + comp, "missing_terminator")

    def test_unknown_kind(self):
        self._assert_reason(b"not a plot config at all", "unknown_kind")
        self._assert_reason(pc.dumps(_ctb_fixture()), "unknown_kind", kind="stb")

    def test_bad_syntax_unbalanced_brace(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("scale_factor=", "}\nscale_factor=", 1))
        self._assert_reason(patched, "bad_syntax")

    def test_bad_syntax_missing_equals(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("plot_style{", "plot_style\n", 1))
        self._assert_reason(patched, "bad_syntax")

    def test_required_item_missing(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("scale_factor=1.0\n", "", 1))
        self._assert_reason(patched, "missing_key")

    def test_style_entry_required_key_missing(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace('  name="Black\n', "", 1))
        self._assert_reason(patched, "missing_key")

    def test_style_field_out_of_range(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("  screen=100\n", "  screen=101\n", 1))
        self._assert_reason(patched, "bad_field")
        patched = _rebuild(raw, lambda text: text.replace("  lineweight=8\n",
                                                          "  lineweight=999\n", 1))
        self._assert_reason(patched, "index_out_of_range")

    def test_bad_bool_literal(self):
        raw = pc.dumps(_ctb_fixture())
        patched = _rebuild(raw, lambda text: text.replace("  adaptive_linetype=TRUE\n",
                                                          "  adaptive_linetype=YES\n", 1))
        self._assert_reason(patched, "bad_field")

    def test_pc3_missing_section(self):
        text = pc.dumps(_pc3_fixture()).decode("utf-8")
        patched = text.replace("[Plotter]\n", "").replace("Device=HP LaserJet.pc3\n", "")
        self._assert_reason(patched.encode("utf-8"), "missing_key")

    def test_pc3_bad_color_token(self):
        text = pc.dumps(_pc3_fixture()).decode("utf-8")
        patched = text.replace("Color2=RGB:255,0,0", "Color2=PURPLE", 1)
        self._assert_reason(patched.encode("utf-8"), "bad_field")

    def test_pc3_bad_line(self):
        text = pc.dumps(_pc3_fixture()).decode("utf-8")
        patched = text.replace("Port=system\n", "Port system\n", 1)
        self._assert_reason(patched.encode("utf-8"), "bad_syntax")

    def test_pc3_bad_device_suffix(self):
        text = pc.dumps(_pc3_fixture()).decode("utf-8")
        patched = text.replace("Device=HP LaserJet.pc3", "Device=HP LaserJet", 1)
        self._assert_reason(patched.encode("utf-8"), "bad_field")


class ComparatorTeethTest(unittest.TestCase):
    """判据 6：**负性对照** —— 故意改坏一个字段，读回必须与原值不同（比对不是恒真）。"""

    def _tamper_roundtrip(self, cfg, field: str, value):
        cfg2 = type(cfg)(**{**cfg.__dict__, field: value})
        return pc.loads(pc.dumps(cfg2)).payload_dict()

    def test_ctb_field_tamper_is_visible(self):
        cfg = _ctb_fixture()
        base = pc.loads(pc.dumps(cfg)).payload_dict()
        for field, value in (("description", "tampered"), ("scale_factor", 2.5),
                             ("apply_factor", True)):
            self.assertNotEqual(self._tamper_roundtrip(cfg, field, value), base,
                                msg="field %s tamper went unnoticed" % field)

    def test_ctb_style_tamper_is_visible(self):
        cfg = _ctb_fixture()
        base = pc.loads(pc.dumps(cfg)).payload_dict()
        for index, field, value in ((0, "color", pc.color_rgb((9, 9, 9))),
                                    (1, "screen", 42),
                                    (1, "linetype", 7),
                                    (0, "lineweight", 20),
                                    (0, "name", "Renamed")):
            style = cfg.styles[index]
            tampered = type(style)(**{**style.__dict__, field: value})
            styles = list(cfg.styles)
            styles[index] = tampered
            cfg2 = type(cfg)(**{**cfg.__dict__, "styles": tuple(styles)})
            self.assertNotEqual(pc.loads(pc.dumps(cfg2)).payload_dict(), base,
                                msg="style %d %s tamper went unnoticed" % (index, field))

    def test_stb_and_pc3_tamper_is_visible(self):
        stb = _stb_fixture()
        base = pc.loads(pc.dumps(stb)).to_dict()
        style = stb.styles[1]
        tampered = type(style)(**{**style.__dict__, "linepattern_size": 9.75})
        cfg2 = type(stb)(**{**stb.__dict__, "styles": (stb.styles[0], tampered)})
        self.assertNotEqual(pc.loads(pc.dumps(cfg2)).payload_dict(), base)

        cfg = _pc3_fixture()
        base = pc.loads(pc.dumps(cfg)).payload_dict()
        mapping = cfg.color_map[1]
        broken = type(mapping)(**{**mapping.__dict__, "linetype_index": 2})
        cfg2 = type(cfg)(**{**cfg.__dict__, "color_map": (cfg.color_map[0], broken)
                            + cfg.color_map[2:]})
        self.assertNotEqual(pc.loads(pc.dumps(cfg2)).payload_dict(), base)


class FileLevelTest(unittest.TestCase):
    """save/load 的文件级往返；写面只在 tempfile 里。"""

    def test_save_load_all_three(self):
        with tempfile.TemporaryDirectory(prefix="plot-config-test-") as tmp:
            root = Path(tmp)
            cases = (("office.ctb", _ctb_fixture()), ("names.stb", _stb_fixture()),
                     ("printer.pc3", _pc3_fixture()))
            for name, cfg in cases:
                target = pc.save(cfg, root / name)
                self.assertEqual(target.read_bytes(), pc.dumps(cfg))
                self.assertEqual(pc.load(target).payload_dict(), cfg.payload_dict())

    def test_pc3_in_container_roundtrip(self):
        cfg = _pc3_fixture()
        wrapped = pc.wrap_container(pc.dumps(cfg).decode("utf-8"), "PC3VER1")
        back = pc.loads(wrapped)
        self.assertEqual(back.container.container, "pmzlibcodec")
        self.assertEqual(back.to_dict()["color_map"], cfg.to_dict()["color_map"])
        self.assertEqual(pc.dumps(back), wrapped)

    def test_save_requires_parent(self):
        with tempfile.TemporaryDirectory(prefix="plot-config-test-") as tmp:
            with self.assertRaises(pc.PlotConfigError) as ctx:
                pc.save(_ctb_fixture(), Path(tmp) / "missing" / "x.ctb")
            self.assertEqual(ctx.exception.reason, "write_failed")

    def test_load_missing_file(self):
        with self.assertRaises(pc.PlotConfigError) as ctx:
            pc.load("/nonexistent/definitely-not-here.ctb")
        self.assertEqual(ctx.exception.reason, "read_failed")

    def test_extension_mismatch_is_loud(self):
        with tempfile.TemporaryDirectory(prefix="plot-config-test-") as tmp:
            target = Path(tmp) / "actually-stb.ctb"
            target.write_bytes(pc.dumps(_stb_fixture()))
            with self.assertRaises(pc.PlotConfigError) as ctx:
                pc.load(target)
            self.assertEqual(ctx.exception.reason, "unknown_kind")


class FixtureWitnessTest(unittest.TestCase):
    """夹具自证（E2.5）：样本必须非平凡，否则上面的往返是空转。"""

    def test_fixtures_are_nontrivial(self):
        ctb = _ctb_fixture()
        self.assertEqual(len(ctb.styles), pc.STYLE_COUNT)
        self.assertGreaterEqual(len(ctb.lineweights), 27)
        self.assertGreater(len(ctb.aci_names), 200)

    def test_helper_math(self):
        packed = pc.color_rgb((1, 2, 3))
        self.assertEqual(pc.int2color(packed), (1, 2, 3, pc.COLOR_RGB))
        self.assertEqual(pc.pack_mode_color(4, 5, 6), pc.color_rgb((4, 5, 6)))
        with self.assertRaises(pc.PlotConfigError):
            pc.color_rgb((256, 0, 0))

    def test_new_ctb_defaults(self):
        cfg = pc.new_ctb()
        self.assertEqual(len(cfg.styles), pc.STYLE_COUNT)
        self.assertEqual(cfg.styles[0].name, "Color_1")
        self.assertEqual(cfg.styles[254].name, "Color_255")
        self.assertEqual(cfg.container.container, "pmzlibcodec")
        self.assertEqual(cfg.container.version_tag, "CTBVER1")

    def test_new_stb_normal_first(self):
        cfg = pc.new_stb()
        self.assertEqual([style.name for style in cfg.styles], ["Normal"])
        self.assertFalse(cfg.aci_table_available)

    def test_pc3_validate_teeth(self):
        cfg = _pc3_fixture()
        broken = type(cfg)(**{**cfg.__dict__, "default_media": "A9"})
        with self.assertRaises(pc.PlotConfigError) as ctx:
            broken.validate()
        self.assertEqual(ctx.exception.reason, "bad_field")


if __name__ == "__main__":
    unittest.main()
