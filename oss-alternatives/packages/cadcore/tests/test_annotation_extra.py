#!/usr/bin/env python3
"""tests.test_annotation_extra —— annotation-extra 族（`cadcore.ops.annotation_extra`）单测。

本文件是**冻结接口的尺子**，不是实现的复印件：断言只打族接口、落盘字段与几何后果，
不 import 实现内部 helper（`_parse_fit` 这类私有名一个都不碰）。

冻结接口（逐条打分，与 `ops/annotation.py` 同源）
----------------------------------------------
1. `FAMILY` 非空字符串；`OPERATIONS` 是 `dict[str, Callable]`；
2. 每个操作**第一个位置参数必须叫 `sk_or_doc`**（POSITIONAL_OR_KEYWORD），其余参数必须
   `KEYWORD_ONLY` ⇒ `op(sk_or_doc, **kwargs)` 是唯一合法调用形态；
3. 返回**本次真正新建**的注解 uuid 列表：每个元素是合法 uuid 字符串、互不重复，且
   `doc["annotations"]` 的增量**恰好**等于返回条数；
4. 长度一律整数纳米（浮点/bool 一律 `CadError`）；错误一律 `cadcore.errors.CadError`，
   且 `reason` 必须是**具体**的机器可读 token（不是基类缺省 `cad_error`）；
5. 引用一律 `(uuid, sub_shape_name)` 且能在草图里解析（`cadcore.sketch.constraints.find_entity`）。

三类事故是这把尺子专门盯的（都留了阴性对照）
------------------------------------------
① **接不上接口**：参数顺序/关键字名与冻结接口不一致 ⇒ `test_first_param_is_sk_or_doc_*`
   外加 `test_interface_checker_sees_violations`（两个行为变异体反证尺子会说"违规"）；
② **静默改文档**：该抛错的地方返回空列表或写半批 ⇒ 每个边界断言 `reason`，批量还断言
   "失败后 `annotations[]` 条数一条没变"（原子性）；
③ **假绿尺子**：`test_count_ruler_sees_red` 用"写两条只报一条"的变异体反证计数尺子看得见红。

夹具自证
--------
* 夹具是合法 L1（`check_invariants` 0 违规、uuid 合法、长度整数纳米），且实体 uuid 能被
  `constraints.find_entity` 解析 —— 断言打的是真件；
* 全程内存操作，不落盘、不碰别人的文件。
"""

from __future__ import annotations

import inspect
import io
import subprocess
import sys
import unittest
import uuid as _uuid
from contextlib import redirect_stdout
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.docmodel import check_invariants  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import annotation_extra as ax  # noqa: E402
from cadcore.sketch import constraints as cons  # noqa: E402

MM = 1_000_000
EXPECTED_FAMILY = "annotation_extra"
EXPECTED_OPS = ("add_baseline_batch", "add_continue_batch", "add_dimension_chain_batch",
                "add_fit_tolerance", "add_surface_finish", "add_tolerance")
#: L1 冻结的 annotation 字段集（`docmodel._NODE_FIELDS["annotation"]`）：多一个字段就是污染契约。
L1_ANNOTATION_FIELDS = {"uuid", "target", "kind", "value_nm", "text_key", "literal", "args"}


# --------------------------------------------------------------------------------------
# 夹具（L1 文档：一草图两直线 + 一圆，一拉伸特征，annotations 为空）
# --------------------------------------------------------------------------------------


def _uid(index: int) -> str:
    return "%08x-0000-4000-8000-%012x" % (index, index)


LINE_H = _uid(2)   # (0,0) -> (400000,0) 长度恰好 400000 nm
LINE_V = _uid(3)   # (0,0) -> (0,300000)
CIRCLE = _uid(4)   # cx=1000000 cy=250000 r=125000


def new_document() -> dict:
    return {
        "schema_version": 1,
        "meta": {"doc_uuid": _uid(100), "created_at": "2026-09-30T00:00:00Z",
                 "app_version": "0.1.0", "title_key": "doc.untitled"},
        "unit": "mm",
        "standard_profile": "iso",
        "projection_angle": None,
        "sketches": [{
            "uuid": _uid(1),
            "plane": "XY",
            "entities": [
                {"uuid": LINE_H, "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 400000, "y2": 0}},
                {"uuid": LINE_V, "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": 300000}},
                {"uuid": CIRCLE, "kind": "circle", "layer": "0", "construction": False,
                 "geom": {"cx": 1000000, "cy": 250000, "r": 125000}},
            ],
            "constraints": [], "dimensions": [],
            "solved": {"dof": -1, "converged": False, "cache": True},
        }],
        "features": [{"uuid": _uid(5), "kind": "extrude", "inputs": [_uid(1)],
                      "params": {"distance_nm": 1000000, "symmetric": False, "angle_deg": 0.0,
                                 "op": "new"}, "suppressed": False}],
        "annotations": [],
        "sheets": [],
    }


def new_sketch() -> dict:
    return new_document()["sketches"][0]


def node_of(doc: dict, annotation_uuid: str) -> dict:
    for item in doc["annotations"]:
        if item["uuid"] == annotation_uuid:
            return item
    raise AssertionError("annotation %s not in doc['annotations']" % annotation_uuid)


def assert_uuid_list(testcase: unittest.TestCase, out, doc: dict) -> None:
    testcase.assertIsInstance(out, list)
    testcase.assertTrue(out, "an operation must return the uuids it created")
    known = {item["uuid"] for item in doc["annotations"]}
    for item in out:
        testcase.assertIsInstance(item, str)
        _uuid.UUID(item)
        testcase.assertIn(item, known)
    testcase.assertEqual(len(set(out)), len(out), "returned uuids must be distinct")


# --------------------------------------------------------------------------------------
# 1) 夹具自证（不依赖被测模块的形状假设）
# --------------------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):

    def test_fixture_is_valid_l1(self):
        doc = new_document()
        self.assertEqual(check_invariants(doc), [])
        for entity in doc["sketches"][0]["entities"]:
            _uuid.UUID(entity["uuid"])

    def test_fixture_refs_resolve(self):
        sketch = new_sketch()
        for entity_uuid in (LINE_H, LINE_V, CIRCLE):
            self.assertIsNotNone(cons.find_entity(sketch, entity_uuid))


# --------------------------------------------------------------------------------------
# 2) 接口形状（冻结契约）
# --------------------------------------------------------------------------------------


def _interface_problem(op) -> str:
    """返回违规描述；`""` = 合规。尺子本身是纯函数，便于用变异体反证。"""
    params = list(inspect.signature(op).parameters.values())
    if not params or params[0].name != "sk_or_doc":
        return "first parameter is not named sk_or_doc"
    if params[0].kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
        return "first parameter is not positional"
    for param in params[1:]:
        if param.kind is not inspect.Parameter.KEYWORD_ONLY:
            return "%s is not keyword-only" % param.name
    return ""


class InterfaceTests(unittest.TestCase):

    def test_family_and_operations_shape(self):
        self.assertEqual(ax.FAMILY, EXPECTED_FAMILY)
        self.assertIsInstance(ax.OPERATIONS, dict)
        self.assertEqual(tuple(sorted(ax.OPERATIONS)), EXPECTED_OPS)
        for name, op in ax.OPERATIONS.items():
            self.assertIsInstance(name, str)
            self.assertTrue(name.islower() and name == name.strip())
            self.assertTrue(callable(op))
            self.assertIs(getattr(ax, name), op, "%s must be exported as a module attribute" % name)

    def test_first_param_is_sk_or_doc_and_rest_keyword_only(self):
        for name, op in ax.OPERATIONS.items():
            self.assertEqual(_interface_problem(op), "", "%s: %s" % (name, _interface_problem(op)))

    def test_interface_checker_sees_violations(self):
        """阴性对照：尺子必须能看见"接口不符"，否则它是假绿。"""
        def bad_first_param(document, *, target):
            return []

        def bad_extra_positional(sk_or_doc, target):
            return []

        def good(sk_or_doc, *, target):
            return []

        self.assertEqual(_interface_problem(good), "")
        self.assertIn("sk_or_doc", _interface_problem(bad_first_param))
        self.assertIn("keyword-only", _interface_problem(bad_extra_positional))

    def test_every_operation_returns_created_uuids(self):
        table = (
            ("add_tolerance", {"target": (LINE_H, "edge"), "upper_nm": 1000, "lower_nm": -1000}),
            ("add_fit_tolerance", {"target": (LINE_H, "edge"), "fit": "H7/g6", "value_nm": MM}),
            ("add_surface_finish", {"target": (LINE_H, "edge"), "ra_nm": 1600}),
            ("add_dimension_chain_batch", {"entries": [
                {"target": (LINE_H, "edge"), "values_nm": [MM], "role": "continue"}]}),
            ("add_baseline_batch", {"entries": [
                {"target": (LINE_H, "edge"), "values_nm": [MM, 2 * MM]}]}),
            ("add_continue_batch", {"entries": [
                {"target": (LINE_H, "edge"), "values_nm": [MM]}]}),
        )
        self.assertEqual(tuple(sorted(name for name, _ in table)), EXPECTED_OPS)
        for name, kwargs in table:
            doc = new_document()
            before = len(doc["annotations"])
            out = getattr(ax, name)(doc, **kwargs)  # 第一个位置参真位置传递
            assert_uuid_list(self, out, doc)
            self.assertEqual(len(doc["annotations"]) - before, len(out),
                             "%s wrote %d but returned %d"
                             % (name, len(doc["annotations"]) - before, len(out)))

    def test_operation_first_argument_is_a_document(self):
        """第一个位置参真能吃到文档（不是"名字对、语义错"）。"""
        doc = new_document()
        out = ax.add_tolerance(doc, target=(LINE_H, "edge"), upper_nm=1000, lower_nm=-1000)
        self.assertEqual(len(doc["annotations"]), 1)
        self.assertEqual(doc["annotations"][0]["uuid"], out[0])


# --------------------------------------------------------------------------------------
# 3) 公差
# --------------------------------------------------------------------------------------


class ToleranceTests(unittest.TestCase):

    def setUp(self):
        self.doc = new_document()

    def assert_reason(self, reason, op, sk_or_doc, **kwargs):
        before = len(sk_or_doc["annotations"])
        with self.assertRaises(CadError) as ctx:
            op(sk_or_doc, **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        self.assertNotEqual(ctx.exception.reason, "cad_error")
        self.assertEqual(len(sk_or_doc["annotations"]), before, "失败调用不许写半条注解")

    def test_tolerance_records_exact_integer_nm(self):
        out = ax.add_tolerance(self.doc, target=(LINE_H, "edge"), upper_nm=21000,
                               lower_nm=-8000, value_nm=400000, tolerance_class="IT7",
                               text_key="ann.tolerance.linear")
        assert_uuid_list(self, out, self.doc)
        node = node_of(self.doc, out[0])
        self.assertEqual(node["kind"], "tolerance")
        self.assertEqual(node["value_nm"], 400000)
        self.assertEqual(node["args"]["tol_upper_nm"], 21000)
        self.assertEqual(node["args"]["tol_lower_nm"], -8000)
        self.assertEqual(node["args"]["tol_class"], "IT7")
        self.assertEqual(node["target"], [LINE_H, "edge"])
        self.assertEqual(node["text_key"], "ann.tolerance.linear")
        self.assertEqual(check_invariants(self.doc), [])

    def test_tolerance_without_nominal_has_no_value_nm(self):
        out = ax.add_tolerance(self.doc, target=(LINE_H, "edge"), upper_nm=0, lower_nm=-500)
        node = node_of(self.doc, out[0])
        self.assertNotIn("value_nm", node, "不给公称尺寸就不许落 value_nm（不写假 0）")
        self.assertEqual(node["args"]["tol_class"], "-")

    def test_boundary_float_deviation_raises(self):
        self.assert_reason("invalid_length", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=1000.0, lower_nm=-1000)

    def test_boundary_bool_deviation_raises(self):
        self.assert_reason("invalid_length", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=True, lower_nm=-1)

    def test_boundary_positive_lower_raises(self):
        self.assert_reason("bad_tolerance_range", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=1000, lower_nm=500)

    def test_boundary_negative_upper_raises(self):
        self.assert_reason("bad_tolerance_range", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=-1000, lower_nm=-2000)

    def test_asymmetric_tolerance_is_accepted(self):
        """上下偏差不必对称：`+100 / -1000` 是合法公差（不是"区间颠倒"）。"""
        out = ax.add_tolerance(self.doc, target=(LINE_H, "edge"), upper_nm=100, lower_nm=-1000)
        node = node_of(self.doc, out[0])
        self.assertEqual((node["args"]["tol_upper_nm"], node["args"]["tol_lower_nm"]),
                         (100, -1000))

    def test_boundary_degenerate_tolerance_raises(self):
        self.assert_reason("degenerate_tolerance", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=0, lower_nm=0)

    def test_boundary_bad_tolerance_class_raises(self):
        self.assert_reason("bad_tolerance_class", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=1000, lower_nm=-1000,
                           tolerance_class="fine")

    def test_boundary_unknown_target_raises(self):
        self.assert_reason("unknown_reference", ax.add_tolerance, self.doc,
                           target=(_uid(999), "edge"), upper_nm=1000, lower_nm=-1000)

    def test_boundary_bare_sketch_raises(self):
        sketch = new_sketch()
        with self.assertRaises(CadError) as ctx:
            ax.add_tolerance(sketch, target=(LINE_H, "edge"), upper_nm=1000, lower_nm=-1000)
        self.assertEqual(ctx.exception.reason, "annotation_needs_document")

    def test_boundary_bad_text_key_raises(self):
        self.assert_reason("bad_text_key", ax.add_tolerance, self.doc,
                           target=(LINE_H, "edge"), upper_nm=1000, lower_nm=-1000,
                           text_key="surface finish general")


# --------------------------------------------------------------------------------------
# 4) 配合（ISO 286）
# --------------------------------------------------------------------------------------


class FitTests(unittest.TestCase):

    def setUp(self):
        self.doc = new_document()

    def add(self, fit: str):
        out = ax.add_fit_tolerance(self.doc, target=(LINE_H, "edge"), fit=fit,
                                   value_nm=30 * MM)
        return node_of(self.doc, out[0])

    def assert_reason(self, reason, **kwargs):
        before = len(self.doc["annotations"])
        with self.assertRaises(CadError) as ctx:
            ax.add_fit_tolerance(self.doc, target=(LINE_H, "edge"),
                                 value_nm=kwargs.pop("value_nm", 30 * MM), **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(len(self.doc["annotations"]), before)

    def test_clearance_fit(self):
        node = self.add("H7/g6")
        self.assertEqual(node["kind"], "fit_tolerance")
        self.assertEqual(node["value_nm"], 30 * MM)
        self.assertEqual(node["args"]["fit"], "H7/g6")
        self.assertEqual(node["args"]["hole_code"], "H7")
        self.assertEqual(node["args"]["shaft_code"], "g6")
        self.assertEqual(node["args"]["fit_kind"], "clearance")
        self.assertEqual(node["args"]["basis"], "hole")

    def test_transition_fit(self):
        self.assertEqual(self.add("H7/k6")["args"]["fit_kind"], "transition")

    def test_interference_fit(self):
        self.assertEqual(self.add("H7/p6")["args"]["fit_kind"], "interference")

    def test_both_basis_is_clearance(self):
        node = self.add("H7/h6")
        self.assertEqual(node["args"]["fit_kind"], "clearance")
        self.assertEqual(node["args"]["basis"], "both")

    def test_shaft_basis_fit_uses_hole_position(self):
        node = self.add("P7/h6")
        self.assertEqual(node["args"]["fit_kind"], "interference")
        self.assertEqual(node["args"]["basis"], "shaft")

    def test_two_letter_position_is_supported(self):
        self.assertEqual(self.add("H7/js6")["args"]["fit_kind"], "transition")

    def test_boundary_missing_slash_raises(self):
        self.assert_reason("bad_fit_code", fit="H7")

    def test_boundary_lowercase_hole_raises(self):
        self.assert_reason("bad_fit_code", fit="h7/g6")

    def test_boundary_uppercase_shaft_raises(self):
        self.assert_reason("bad_fit_code", fit="H7/G6")

    def test_boundary_nonstandard_position_letter_raises(self):
        self.assert_reason("bad_fit_code", fit="Q7/g6")

    def test_boundary_no_basis_side_raises(self):
        """非基准侧既非孔 H 也非轴 h ⇒ 显式报错，不静默当成某类配合。"""
        self.assert_reason("unsupported_fit", fit="A7/g6")

    def test_boundary_non_integer_nominal_raises(self):
        self.assert_reason("invalid_length", fit="H7/g6", value_nm=30.0 * MM)

    def test_boundary_non_positive_nominal_raises(self):
        self.assert_reason("non_positive_length", fit="H7/g6", value_nm=0)

    def test_boundary_unknown_target_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_fit_tolerance(self.doc, target=(_uid(999), "edge"), fit="H7/g6",
                                 value_nm=30 * MM)
        self.assertEqual(ctx.exception.reason, "unknown_reference")

    def test_document_stays_l1_clean_after_fits(self):
        for fit in ("H7/g6", "H7/k6", "H7/p6"):
            self.add(fit)
        self.assertEqual(check_invariants(self.doc), [])


# --------------------------------------------------------------------------------------
# 5) 表面粗糙度符号
# --------------------------------------------------------------------------------------


class SurfaceFinishTests(unittest.TestCase):

    def setUp(self):
        self.doc = new_document()

    def assert_reason(self, reason, **kwargs):
        before = len(self.doc["annotations"])
        with self.assertRaises(CadError) as ctx:
            ax.add_surface_finish(self.doc, target=(LINE_H, "edge"), **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        self.assertEqual(len(self.doc["annotations"]), before)

    def test_default_symbol_and_ra_value(self):
        out = ax.add_surface_finish(self.doc, target=(LINE_H, "edge"), ra_nm=3200)
        assert_uuid_list(self, out, self.doc)
        node = node_of(self.doc, out[0])
        self.assertEqual(node["kind"], "surface_finish")
        self.assertEqual(node["value_nm"], 3200)
        self.assertEqual(node["args"], {"symbol": "basic", "lay": "-", "ra_unit": "nm"})
        self.assertEqual(node["target"], [LINE_H, "edge"])
        self.assertEqual(check_invariants(self.doc), [])

    def test_lay_and_position_are_preserved_exactly(self):
        out = ax.add_surface_finish(self.doc, target=(CIRCLE, "edge"), ra_nm=800,
                                    symbol="removal_required", lay="crossed",
                                    at_nm=(1000, -2000), text_key="ann.finish.grinding")
        node = node_of(self.doc, out[0])
        self.assertEqual(node["args"]["symbol"], "removal_required")
        self.assertEqual(node["args"]["lay"], "crossed")
        self.assertEqual(node["args"]["at_nm"], [1000, -2000])
        self.assertEqual(node["text_key"], "ann.finish.grinding")
        self.assertEqual(node["value_nm"], 800)

    def test_boundary_float_ra_nm_raises(self):
        self.assert_reason("invalid_length", ra_nm=3.2)

    def test_boundary_bool_ra_nm_raises(self):
        self.assert_reason("invalid_length", ra_nm=True)

    def test_boundary_zero_ra_nm_raises(self):
        self.assert_reason("non_positive_length", ra_nm=0)

    def test_boundary_out_of_range_ra_nm_raises(self):
        self.assert_reason("out_of_range_roughness", ra_nm=1_000_001)

    def test_boundary_bad_symbol_raises(self):
        self.assert_reason("bad_finish_symbol", ra_nm=3200, symbol="polished")

    def test_boundary_bad_lay_raises(self):
        self.assert_reason("bad_lay_symbol", ra_nm=3200, lay="spiral")

    def test_boundary_float_position_raises(self):
        self.assert_reason("bad_position", ra_nm=3200, at_nm=(1.0, 2))

    def test_boundary_unknown_target_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_surface_finish(self.doc, target=(_uid(999), "edge"), ra_nm=3200)
        self.assertEqual(ctx.exception.reason, "unknown_reference")


# --------------------------------------------------------------------------------------
# 6) 基线 / 连续标注批量入口（含原子性）
# --------------------------------------------------------------------------------------


class BatchTests(unittest.TestCase):

    def setUp(self):
        self.doc = new_document()

    def test_baseline_batch_writes_every_segment(self):
        before = len(self.doc["annotations"])
        out = ax.add_baseline_batch(self.doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [10 * MM, 25 * MM]},
            {"target": (LINE_V, "edge"), "values_nm": [5 * MM, 15 * MM, 30 * MM],
             "text_key": "ann.baseline.series"},
        ])
        assert_uuid_list(self, out, self.doc)
        self.assertEqual(len(out), 5)
        self.assertEqual(len(self.doc["annotations"]) - before, 5)
        nodes = [node_of(self.doc, item) for item in out]
        self.assertEqual([node["value_nm"] for node in nodes],
                         [10 * MM, 25 * MM, 5 * MM, 15 * MM, 30 * MM])
        self.assertEqual({node["args"]["dim_role"] for node in nodes}, {"baseline"})
        self.assertEqual([node["args"]["batch_index"] for node in nodes], [0, 0, 1, 1, 1])
        self.assertEqual([node["args"]["segment_index"] for node in nodes], [0, 1, 0, 1, 2])
        self.assertEqual(nodes[2]["text_key"], "ann.baseline.series")
        self.assertEqual(check_invariants(self.doc), [])

    def test_continue_batch_needs_no_increasing_order(self):
        out = ax.add_continue_batch(self.doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [30 * MM, 10 * MM, 20 * MM]},
        ])
        nodes = [node_of(self.doc, item) for item in out]
        self.assertEqual({node["args"]["dim_role"] for node in nodes}, {"continue"})
        self.assertEqual([node["value_nm"] for node in nodes], [30 * MM, 10 * MM, 20 * MM])

    def test_chain_batch_accepts_mixed_roles(self):
        out = ax.add_dimension_chain_batch(self.doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [10 * MM, 20 * MM], "role": "baseline"},
            {"target": (LINE_V, "edge"), "values_nm": [7 * MM], "role": "continue"},
        ])
        roles = [node_of(self.doc, item)["args"]["dim_role"] for item in out]
        self.assertEqual(roles, ["baseline", "baseline", "continue"])

    def test_batch_is_atomic_on_dangling_reference(self):
        before = len(self.doc["annotations"])
        with self.assertRaises(CadError) as ctx:
            ax.add_continue_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM]},
                {"target": (_uid(999), "edge"), "values_nm": [MM]},
            ])
        self.assertEqual(ctx.exception.reason, "unknown_reference")
        self.assertEqual(len(self.doc["annotations"]), before,
                         "整批失败必须一条都不写（先校验后写入）")

    def test_batch_is_atomic_on_bad_order_in_second_entry(self):
        before = len(self.doc["annotations"])
        with self.assertRaises(CadError) as ctx:
            ax.add_baseline_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [10 * MM, 20 * MM]},
                {"target": (LINE_V, "edge"), "values_nm": [30 * MM, 25 * MM]},
            ])
        self.assertEqual(ctx.exception.reason, "bad_dimension_order")
        self.assertEqual(len(self.doc["annotations"]), before)

    def test_boundary_empty_batch_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_baseline_batch(self.doc, entries=[])
        self.assertEqual(ctx.exception.reason, "empty_batch")

    def test_boundary_entry_must_be_a_mapping(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_continue_batch(self.doc, entries=[("edge", [MM])])
        self.assertEqual(ctx.exception.reason, "bad_batch_entry")

    def test_boundary_unknown_entry_key_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_continue_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM], "valus_nm": [MM]}])
        self.assertEqual(ctx.exception.reason, "bad_batch_entry")

    def test_boundary_missing_role_in_chain_batch_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_dimension_chain_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM]}])
        self.assertEqual(ctx.exception.reason, "bad_dimension_role")

    def test_boundary_conflicting_role_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_baseline_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM], "role": "continue"}])
        self.assertEqual(ctx.exception.reason, "bad_dimension_role")

    def test_boundary_float_chain_value_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_continue_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM, 2.5 * MM]}])
        self.assertEqual(ctx.exception.reason, "invalid_length")
        self.assertEqual(len(self.doc["annotations"]), 0)

    def test_boundary_bad_entry_text_key_raises(self):
        with self.assertRaises(CadError) as ctx:
            ax.add_continue_batch(self.doc, entries=[
                {"target": (LINE_H, "edge"), "values_nm": [MM], "text_key": "chain note"}])
        self.assertEqual(ctx.exception.reason, "bad_text_key")


# --------------------------------------------------------------------------------------
# 7) 组合：多操作同页 / L1 字段集 / 互不覆盖
# --------------------------------------------------------------------------------------


class IntegrationTests(unittest.TestCase):

    def test_mixed_operations_keep_document_l1_clean(self):
        doc = new_document()
        ax.add_tolerance(doc, target=(LINE_H, "edge"), upper_nm=21000, lower_nm=-8000,
                         value_nm=400000, tolerance_class="IT7")
        ax.add_fit_tolerance(doc, target=(LINE_V, "edge"), fit="H7/g6", value_nm=30 * MM)
        ax.add_surface_finish(doc, target=(CIRCLE, "edge"), ra_nm=3200, lay="crossed",
                              at_nm=(0, 0))
        ax.add_baseline_batch(doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [10 * MM, 20 * MM]}])
        ax.add_continue_batch(doc, entries=[
            {"target": (LINE_V, "edge"), "values_nm": [5 * MM, 5 * MM]}])
        self.assertEqual(len(doc["annotations"]), 7)
        self.assertEqual(check_invariants(doc), [])
        for node in doc["annotations"]:
            self.assertTrue(set(node) <= L1_ANNOTATION_FIELDS,
                            "annotation node leaked fields: %s" % sorted(set(node)))
            self.assertIsNotNone(cons.find_entity(new_sketch(), node["target"][0]))

    def test_earlier_annotations_are_not_clobbered(self):
        doc = new_document()
        tol = ax.add_tolerance(doc, target=(LINE_H, "edge"), upper_nm=21000, lower_nm=-8000,
                               value_nm=400000)[0]
        snapshot = dict(node_of(doc, tol))
        ax.add_surface_finish(doc, target=(LINE_H, "edge"), ra_nm=3200)
        ax.add_continue_batch(doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [MM]}])
        self.assertEqual(node_of(doc, tol), snapshot)
        self.assertEqual(len({node["uuid"] for node in doc["annotations"]}), 3)

    def test_module_facts(self):
        facts = ax.module_facts()
        self.assertEqual(facts["family"], EXPECTED_FAMILY)
        self.assertEqual(facts["ops"], EXPECTED_OPS)
        self.assertEqual(facts["three_state"], {"zero": 0, "unmeasured": -1,
                                                "not_applicable": "NA"})


# --------------------------------------------------------------------------------------
# 8) 自带自检与计数尺子的阴性对照
# --------------------------------------------------------------------------------------


class SelftestTests(unittest.TestCase):

    def test_selftest_results_are_named_and_green(self):
        results = ax.selftest()
        self.assertIsInstance(results, list)
        self.assertTrue(results)
        for item in results:
            self.assertEqual(len(item), 3)
            name, ok, detail = item
            self.assertIsInstance(name, str)
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(detail, str)
        self.assertEqual([name for name, ok, _ in results if not ok], [])

    def test_main_prints_extra_ops_verdict(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = ax.main()
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertIn("EXTRA_OPS verdict=PASS", output)
        self.assertIn("ops=%d" % len(ax.OPERATIONS), output)
        self.assertIn("ops=6", output)

    def test_module_entry_point_prints_verdict(self):
        proc = subprocess.run([sys.executable, "-m", "cadcore.ops.annotation_extra"],
                              cwd=str(_PKG_ROOT), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("EXTRA_OPS verdict=PASS ops=6", proc.stdout)

    def test_count_ruler_sees_red(self):
        """尺子反证：写两条只报一条的变异体必须被判红，真件必须通过。"""
        def count_ruler(op, doc, **kwargs):
            before = len(doc["annotations"])
            out = op(doc, **kwargs)
            return len(doc["annotations"]) - before == len(out)

        def mutant(sk_or_doc, *, target, value_nm):
            node = {"uuid": str(_uuid.uuid4()), "target": list(target), "kind": "linear",
                    "value_nm": value_nm, "args": {}}
            sk_or_doc["annotations"].append(node)
            sk_or_doc["annotations"].append(dict(node, uuid=str(_uuid.uuid4())))
            return [node["uuid"]]

        mutant_doc = new_document()
        real_doc = new_document()
        self.assertFalse(count_ruler(mutant, mutant_doc, target=(LINE_H, "edge"),
                                     value_nm=400000))
        self.assertTrue(count_ruler(ax.add_continue_batch, real_doc, entries=[
            {"target": (LINE_H, "edge"), "values_nm": [MM]}]))

    def test_source_has_no_locale_literal(self):
        source = Path(inspect.getsourcefile(ax)).read_text(encoding="utf-8")
        for literal in ('"zh-CN"', '"en"', '"ja"'):
            self.assertNotIn(literal, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
