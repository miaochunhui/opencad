"""G21 补登记回归（席位 B）：五条 partial 项的**专属真实现**（公差配合 / GD&T / 粗糙度 / 孔标注 / 模板）。

判据（不许放宽）：
* 五项在 `OPERATIONS` 里且 `MUST_HAVE_MAP` 各自点名到自己（不再挂到别的操作上判 partial）；
* 裸调（探针同口径）返回非空 uuid 列表，**每个 uuid 能在调用后的 L1 载荷里解引用**，载荷必须变化，
  且条目专属 token 真的新出现（`tolerance_fit` / `gdt` / `surface_roughness` / `hole_callout` / `template`）；
* 表外 token / 非正数值必须抛错（阴性对照：不能静默编一个数糊过去）；
* `export` 不许拿 SVG 冒充 PDF/DWG：SVG 导出器不在本模块，且 `UNSUPPORTED_CAPABILITIES` 如实登记。
"""

import unittest

from cadcore import docmodel
from cadcore.ops import sw_drawings as sw

NEW_OPS = ("tolerance_fit", "gdt", "surface_roughness", "hole_callout", "template")

#: 各条目专属后果 token（与门 `ITEM_SPECS` 同源口径；必须调用后才新出现）。
TOKENS = {
    "tolerance_fit": ("tolerance_fit", "tolerance"),
    "gdt": ("gdt", "datum"),
    "surface_roughness": ("surface_roughness", "roughness"),
    "hole_callout": ("hole_callout", "hole_qty"),
    "template": ("template", "titleblock_fields"),
}


class SwDrawingsAnnotationOpsTest(unittest.TestCase):
    """族 `sw_drawings`：五条补登记的登记面 + L1 后果 + 阴性对照。"""

    def test_registered_and_mapped(self):
        for name in NEW_OPS:
            self.assertIn(name, sw.OPERATIONS, "%s 未登记进 OPERATIONS" % name)
            self.assertEqual(sw.MUST_HAVE_MAP.get(name), name,
                             "MUST_HAVE_MAP 没把 %s 点名到自己" % name)

    def test_bare_call_records_l1_consequence(self):
        for name in NEW_OPS:
            doc = sw._sample_model()
            before = docmodel.document_to_l1(doc)
            out = sw.OPERATIONS[name](doc)
            self.assertIsInstance(out, list, name)
            self.assertTrue(out, "%s 返回空 uuid 列表" % name)
            after = docmodel.document_to_l1(doc)
            self.assertNotEqual(before, after, "%s 调用后 L1 载荷没变" % name)
            for uid in out:
                self.assertIn(uid, after, "%s 悬空 uuid（载荷里解引用不到）：%s" % (name, uid))
            text = after.lower()
            hits = [tok for tok in TOKENS[name] if tok in text]
            self.assertTrue(hits, "%s 没有专属 token 新出现：%s" % (name, list(TOKENS[name])))

    def test_negative_controls_refuse_bad_tokens(self):
        doc = sw._sample_model()
        cases = (
            ("gdt", {"symbol": "not_a_symbol"}),
            ("surface_roughness", {"ra_um": -1.0}),
            ("surface_roughness", {"param": "Rx"}),
            ("hole_callout", {"diameter_nm": 0}),
            ("hole_callout", {"qty": 0}),
            ("template", {"template": "nope"}),
            ("tolerance_fit", {"nominal_nm": -5}),
        )
        for name, kwargs in cases:
            with self.assertRaises(Exception, msg="%s%s 应当拒收" % (name, kwargs)):
                sw.OPERATIONS[name](doc, **kwargs)

    def test_export_stays_partial_honestly(self):
        self.assertNotIn("print_svg_text", sw.OPERATIONS)
        self.assertIn("export_pdf", sw.UNSUPPORTED_CAPABILITIES)
        self.assertIn("export_dwg", sw.UNSUPPORTED_CAPABILITIES)


if __name__ == "__main__":
    unittest.main()
