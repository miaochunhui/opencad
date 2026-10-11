"""test_b6_param_boundary_gate —— B6 参数边界第二轮：**可表示性 / 装量程 / 装载闸**（cadcore 侧）。

缺陷源（实测，探针 `/hf3fs-jd/data/tmp/cad-scan3/probe3.py`，pin 按当前字节重建）：
`CAD_PARAM_SCAN verdict=FAIL defects_A=37`，本文件覆盖其中四类在 cadcore 侧的根因：

1. `naked_builtin_exception:OverflowError`（RC5，5 条）：`float(10**309)` 在**判据执行之前**就抛
   `OverflowError: int too large to convert to float` —— `_as_factor` / `_as_angle` / `_as_deg` /
   `osnap._as_finite` / `osnap.snap(point)` 五处"先转再判"的入参把关自己成了裸内建异常的来源；
2. `out_of_range_persisted`（RC2b，5 条）：仿射变换结果不过 `nm_in_range`，`factor=2**53` 把
   `r=9.007e22` 静默写进 L1 真源（超出产品自述的 `|v| <= 2**53`）；
3. `silent_illegal` + `invariant_broken`（RC9/RC6，标注 `value_nm` 与 `text_key`）：`/api/open` 把
   布尔/字符串/浮点长度与非 i18n key 静默收下；
4. `accepted_annotation_text_silently_dropped`（RC7，8 条）：`doc.annotations` 的文本**一个字都不进产物**。

判据（全部现算，每条都带阳性/阴性对照，证明尺子不是恒红）
----------------------------------------------------------
1. `finite_number` 是"能不能转成有限 float"的唯一真源：`10**309`/`inf`/`nan`/`bool` ⇒ `None`，
   普通整数/浮点 ⇒ 值本身；
2. 五个 OverflowError 入口对大整数抛**域错误**（`CadError`/`GeometryError`/`ValueError`），
   且异常链里**没有** `OverflowError`；
3. 变换结果越界 ⇒ `CadError(reason="out_of_range")`，且草图上一个字段都没被改写（不落半截）；
   量程内的变换逐字不变（阴性对照）；
4. `docmodel.load_blocking_problems` 对非法值/类型/引用形状/不可编码文本返回非空清单，
   对产品自己写出来的干净文档返回空（**阳性对照**：证明这道闸不是恒拒）；
5. `text_key` 判据写侧（`add_dimension`）与读侧（`check_invariants`）**同源**：
   合法 key 收、非法 key 拒，且被拒时尺寸表里不多一条；
6. 标注文本进产物：`dxf_bridge.annotation_text_lines` 与 `draw.sheet.annotation_texts` 都吐文本，
   未加过的文本一个都不出现（阴性对照）。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .                 # 契约命令
    python3 tests/test_b6_param_boundary_gate.py -v            # 单跑本文件

本文件不写盘、不起服务、不改产品码。
"""

from __future__ import annotations

import json
import unittest

from cadcore.docmodel import (
    check_invariants,
    document_from_l1,
    load_blocking_problems,
    range_problems,
    text_encoding_problems,
)
from cadcore.draw import sheet as draw_sheet
from cadcore.edit import modify as edit_modify
from cadcore.errors import CadError
from cadcore.i18n.keys import is_text_key
from cadcore.io import dxf_bridge
from cadcore.precision import osnap
from cadcore.sketch import commands as cmds
from cadcore.units import finite_number

MM = 1_000_000
HUGE = int("9" * 309)
MAX_NM = 1 << 53
LINE_UUID = "bbbb2222-0000-4000-8000-000000000011"
CIRCLE_UUID = "bbbb2222-0000-4000-8000-000000000012"
ANN_UUID = "aaaa1111-0000-4000-8000-000000000001"


def _line_sketch():
    return {"entities": [{"uuid": LINE_UUID, "kind": "line", "layer": "0", "construction": False,
                          "geom": {"x1": 0, "y1": 0, "x2": 10 * MM, "y2": 0}}],
            "constraints": [], "dimensions": []}


def _circle_sketch():
    return {"entities": [{"uuid": CIRCLE_UUID, "kind": "circle", "layer": "0", "construction": False,
                          "geom": {"cx": 10 * MM, "cy": 0, "r": 5 * MM}}],
            "constraints": [], "dimensions": []}


def _l1_doc(*, annotations=(), entities=()):
    return {"schema_version": 1,
            "meta": {"doc_uuid": "b6000000-0000-4000-8000-0000000000aa",
                     "title_key": "doc.untitled"},
            "unit": "mm", "standard_profile": "gbt", "projection_angle": "first",
            "sketches": [{"uuid": "b6000000-0000-4000-8000-0000000000ab", "plane": "XY",
                          "entities": list(entities), "constraints": [], "dimensions": [],
                          "solved": None}],
            "features": [], "annotations": list(annotations), "sheets": []}


def _annotation(**over):
    ann = {"uuid": ANN_UUID, "target": [LINE_UUID, "start"], "kind": "linear",
           "value_nm": 5 * MM, "text_key": None, "literal": True, "text": "MARKER_XYZ", "args": {}}
    ann.update(over)
    return ann


def _l1_line():
    return {"uuid": LINE_UUID, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": 0, "y1": 0, "x2": 10 * MM, "y2": 0}}


class FiniteNumberTruthSourceTests(unittest.TestCase):
    """判据 1：可表示性判断的唯一真源。"""

    def test_unrepresentable_inputs_are_none(self) -> None:
        for label, value in (("huge309", HUGE), ("inf", float("inf")), ("neginf", float("-inf")),
                             ("nan", float("nan")), ("true", True), ("false", False),
                             ("str", "10"), ("none", None)):
            self.assertIsNone(finite_number(value), label)

    def test_representable_inputs_keep_their_value(self) -> None:
        self.assertEqual(finite_number(10), 10.0)
        self.assertEqual(finite_number(-2.5), -2.5)
        self.assertEqual(finite_number(MAX_NM), float(MAX_NM))

    def test_probe_style_call_never_raises(self) -> None:
        """老写法 `math.isfinite(float(v))` 在 10**309 上抛 OverflowError；本函数必须不抛。"""
        try:
            finite_number(HUGE)
        except OverflowError as exc:  # pragma: no cover - 回归即红
            self.fail(f"finite_number must not leak OverflowError: {exc}")


class OverflowEntryPointsTests(unittest.TestCase):
    """判据 2：五个入口的裸 OverflowError 全部转成域错误。"""

    def _assert_domain_error(self, fn, *, allow: tuple) -> BaseException:
        """`fn` 必须抛**域错误**（类型名在 `allow` 里），且异常链里不许出现裸内建异常。"""
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - 任何类型都要能被判成"是不是回归"
            name = type(exc).__name__
            self.assertIn(name, allow, f"expected one of {allow}, got {name}: {exc}")
            chain = []
            cursor: BaseException | None = exc
            while cursor is not None:
                chain.append(type(cursor).__name__)
                cursor = cursor.__cause__ or cursor.__context__
            self.assertNotIn("OverflowError", chain, f"异常链里不许有裸内建异常：{chain}")
            return exc
        self.fail("expected a rejection, but the call succeeded")
        raise AssertionError("unreachable")

    def test_scale_factor_huge309(self) -> None:
        exc = self._assert_domain_error(
            lambda: edit_modify.scale(_line_sketch(), [LINE_UUID], center_nm=(0, 0), factor=HUGE),
            allow=("CadError",))
        self.assertEqual(getattr(exc, "reason", ""), "invalid_argument")

    def test_rotate_angle_huge309(self) -> None:
        exc = self._assert_domain_error(
            lambda: edit_modify.rotate(_line_sketch(), [LINE_UUID], center_nm=(0, 0), angle_deg=HUGE),
            allow=("CadError",))
        self.assertEqual(getattr(exc, "reason", ""), "invalid_argument")

    def test_add_arc_start_deg_huge309(self) -> None:
        self._assert_domain_error(
            lambda: cmds.add_arc(_line_sketch(), center=(0, 0), radius_nm=5 * MM,
                                 start_deg=HUGE, end_deg=90),
            allow=("GeometryError",))

    def test_snap_tolerance_huge309(self) -> None:
        self._assert_domain_error(
            lambda: osnap.snap(_circle_sketch(), (10 * MM, 0), tolerance_nm=HUGE),
            allow=("ValueError",))

    def test_snap_point_huge309(self) -> None:
        self._assert_domain_error(
            lambda: osnap.snap(_circle_sketch(), (HUGE, 0), tolerance_nm=MM),
            allow=("ValueError",))

    def test_snap_and_ortho_still_work_on_normal_values(self) -> None:
        """阴性对照：闸不是恒拒。"""
        hit = osnap.snap(_circle_sketch(), (10 * MM, 0), tolerance_nm=MM)
        self.assertIsNotNone(hit)
        self.assertEqual(tuple(hit.point_nm), (10 * MM, 0))
        self.assertEqual(osnap.ortho_lock((0, 0), (10 * MM, 5)), (10 * MM, 0))


class TransformRangeGateTests(unittest.TestCase):
    """判据 3：变换结果越界 ⇒ 结构化拒绝，且不落半截。"""

    def test_scale_by_2p53_is_rejected(self) -> None:
        sk = _circle_sketch()
        before = json.dumps(sk, sort_keys=True)
        with self.assertRaises(CadError) as caught:
            edit_modify.scale(sk, [CIRCLE_UUID], center_nm=(0, 0), factor=1 << 53)
        self.assertEqual(caught.exception.reason, "out_of_range")
        self.assertEqual(json.dumps(sk, sort_keys=True), before, "被拒的变换不许改草图")

    def test_move_dx_2p53_is_rejected(self) -> None:
        sk = _line_sketch()
        before = json.dumps(sk, sort_keys=True)
        with self.assertRaises(CadError) as caught:
            edit_modify.move(sk, [LINE_UUID], dx_nm=1 << 53, dy_nm=0)
        self.assertEqual(caught.exception.reason, "out_of_range")
        self.assertEqual(json.dumps(sk, sort_keys=True), before)

    def test_in_range_transform_is_unchanged(self) -> None:
        """阴性对照：量程内的缩放/平移逐字不变。"""
        sk = _circle_sketch()
        edit_modify.scale(sk, [CIRCLE_UUID], center_nm=(0, 0), factor=2)
        self.assertEqual(sk["entities"][0]["geom"]["r"], 10 * MM)
        sk2 = _line_sketch()
        edit_modify.move(sk2, [LINE_UUID], dx_nm=MM, dy_nm=0)
        self.assertEqual(sk2["entities"][0]["geom"]["x2"], 11 * MM)


class LoadGateTests(unittest.TestCase):
    """判据 4/5：装载闸 + 写读同源的 text_key 判据。"""

    def test_clean_product_document_passes_the_gate(self) -> None:
        """阳性对照：产品自己读进来又写出去的文档必须过闸（否则闸就是恒红）。"""
        doc = document_from_l1(json.dumps(_l1_doc(entities=[_l1_line()])))
        sk = doc.sketches[0]
        cmds.add_dimension(sk, kind="linear",
                           refs=[(LINE_UUID, "start"), (LINE_UUID, "end")],
                           value_nm=10 * MM, text_key="dim.length.overall")
        self.assertEqual(check_invariants(doc), [])
        self.assertEqual(load_blocking_problems(doc), [])

    def test_annotation_value_nm_illegal_types_are_blocked(self) -> None:
        for label, value, head in (("bool", True, "nm_is_bool"),
                                   ("str", "5", "nm_not_a_number"),
                                   ("float", 1.5, "nm_not_integer"),
                                   ("huge309", HUGE, "nm_out_of_range")):
            doc = _l1_doc(annotations=[_annotation(value_nm=value)], entities=[_l1_line()])
            problems = load_blocking_problems(doc)
            self.assertTrue(problems, f"{label} 必须被装载闸拒收")
            self.assertTrue(any(item.startswith(head) for item in problems),
                            f"{label}: 期望 {head}，实得 {problems}")

    def test_annotation_text_key_shape_is_blocked(self) -> None:
        for label, key in (("empty", ""), ("spaces", "   "), ("cjk", "中文标注"),
                           ("newline", "a\nb"), ("latex", "$ _ ^"), ("surrogate", "\ud800")):
            doc = _l1_doc(annotations=[_annotation(literal=False, text=None, text_key=key)],
                          entities=[_l1_line()])
            self.assertTrue(load_blocking_problems(doc), f"{label} text_key 必须被拒收")

    def test_annotation_target_shape_is_blocked(self) -> None:
        bad_shape = _l1_doc(annotations=[_annotation(target="not-a-pair")], entities=[_l1_line()])
        self.assertTrue(any(item.startswith("refs_not_a_list")
                            for item in load_blocking_problems(bad_shape)))
        index_ref = _l1_doc(annotations=[_annotation(target=[LINE_UUID, "0"])], entities=[_l1_line()])
        self.assertTrue(any(item.startswith("ref_index_used")
                            for item in load_blocking_problems(index_ref)))

    def test_lone_surrogate_literal_text_is_blocked_before_serialisation(self) -> None:
        doc = _l1_doc(annotations=[_annotation(text="\ud800")], entities=[_l1_line()])
        problems = load_blocking_problems(doc)
        self.assertTrue(any(item.startswith("text_not_utf8") for item in problems), problems)
        self.assertEqual(text_encoding_problems(doc), sorted(set(text_encoding_problems(doc))))
        self.assertIn("text_not_utf8", " ".join(problems))

    def test_range_problems_reports_out_of_range_lengths_only(self) -> None:
        good = _l1_doc(annotations=[_annotation(value_nm=MAX_NM)], entities=[_l1_line()])
        self.assertEqual(range_problems(good), [])
        bad = _l1_doc(annotations=[_annotation(value_nm=MAX_NM + 1)], entities=[_l1_line()])
        self.assertTrue(any(item.startswith("nm_out_of_range") for item in range_problems(bad)))

    def test_add_dimension_text_key_shares_the_same_ruler(self) -> None:
        def build(text_key):
            sk = _line_sketch()
            return cmds.add_dimension(sk, kind="linear",
                                      refs=[(LINE_UUID, "start"), (LINE_UUID, "end")],
                                      value_nm=10 * MM, text_key=text_key), sk

        uuid_text, sk = build("dim.length.overall")
        self.assertTrue(uuid_text)
        self.assertEqual(len(sk["dimensions"]), 1)
        uuid_none, sk_none = build(None)
        self.assertTrue(uuid_none)
        self.assertEqual(len(sk_none["dimensions"]), 1)
        for label, bad in (("cjk", "中文标注"), ("newline", "a\nb"), ("spaces", "   "),
                           ("empty", ""), ("latex", "$ _ ^"), ("surrogate", "\ud800")):
            sk = _line_sketch()
            with self.assertRaises(cmds.GeometryError) as caught:
                cmds.add_dimension(sk, kind="linear",
                                   refs=[(LINE_UUID, "start"), (LINE_UUID, "end")],
                                   value_nm=10 * MM, text_key=bad)
            self.assertIn("i18n key", str(caught.exception), label)
            self.assertEqual(sk["dimensions"], [], f"{label}: 被拒的尺寸不许落进草图")
            self.assertFalse(is_text_key(bad), label)


class AnnotationTextIntoProductsTests(unittest.TestCase):
    """判据 6：标注文本逐字进产物；没加过的文本不许出现。"""

    PAYLOADS = (("spaces", "   "), ("long10k", "A" * 10000),
                ("newline_tab", "line1\nline2\ttabbed\r\n"), ("cjk_emoji", "中文标注 😀 尺寸"),
                ("latex", "$ _ ^ \\ { } % & # ~"),
                ("quotes", "he said \"hi\" and 'bye' `tick`"), ("marker", "SERIAL_MARKER"))

    def test_dxf_carries_annotation_text(self) -> None:
        for label, text in self.PAYLOADS:
            ann = [_annotation(text=text)]
            payload = dxf_bridge.sketch_to_dxf({"entities": [_l1_line()], "dimensions": []},
                                               annotations=ann)
            hits = (text.encode("utf-8") in payload) or (text.encode("latin-1", "replace") in payload)
            if label == "newline_tab":
                # DXF 值行必须是单行（`_single_line` 折成空格）：嵌入换行的原文由图纸/PDF 侧承载。
                self.assertFalse(hits, "DXF 值行不该出现裸换行")
                continue
            self.assertTrue(hits, f"{label} 的文本必须出现在 DXF 产物里")

    def test_sheet_and_pdf_carry_annotation_text(self) -> None:
        from cadcore.ops import sw_drawings_extra as pdf_writer

        for label, text in self.PAYLOADS:
            doc = _l1_doc(annotations=[_annotation(text=text)], entities=[_l1_line()])
            sheet = draw_sheet.make_sheet(doc, standard="gbt", language="zh-CN")
            self.assertIn(text, draw_sheet.annotation_texts(sheet), label)
            if label != "newline_tab":
                continue  # 其余文本已由上面 DXF 那格覆盖；这里专测裸换行的承载
            blob = pdf_writer.render_pdf(sheet)
            self.assertIn(text.encode("utf-8"), blob, "PDF 内容流必须承载嵌入换行的原文")

    def test_absent_text_never_appears(self) -> None:
        """阴性对照：没加过的文本不许凭空出现。"""
        doc = _l1_doc(annotations=[_annotation(text="ONLY_THIS_ONE")], entities=[_l1_line()])
        sheet = draw_sheet.make_sheet(doc, standard="gbt", language="zh-CN")
        payload = dxf_bridge.sketch_to_dxf({"entities": [_l1_line()], "dimensions": []},
                                           annotations=doc["annotations"])
        for needle in ("NOT_ADDED_MARKER", "ANNOTATION_MARKER_XYZ"):
            self.assertNotIn(needle.encode("utf-8"), payload)
            self.assertNotIn(needle, draw_sheet.annotation_texts(sheet))

    def test_annotation_text_lines_skips_empty_texts(self) -> None:
        empty = _annotation(literal=False, text=None, text_key=None)
        self.assertEqual(dxf_bridge.annotation_text_lines([empty]), [])
        key_only = _annotation(literal=False, text=None, text_key="dim.length.overall")
        lines = dxf_bridge.annotation_text_lines([key_only])
        self.assertEqual(len(lines), 1)
        self.assertIn("dim.length.overall", lines[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
