#!/usr/bin/env python3
"""tests.test_sw_features_ops —— family sw_features 的独立单测（对账席自持，新文件）。

本文件测的是 ``cadcore/ops/sw_features.py``（族模块）**真件**，不是它的复述：
夹具（L1 文档 / 裸草图 dict）先由 ``cadcore.kernel.nano.NanoKernel`` 自证是**真方言**
（同一张 L1 夹克拉成实体后量出 60 mm² / 32 mm；线框无边界面，直接量只会得 -1 未采），
再用**精确整数纳米**断言几何后果与参数记录；
错误路径逐条断言 ``CadError.reason``（机器可读 reason，不只看"抛了异常"）。

三态口径（0 = 真为零 / -1 = 未采 / NA = 不适用）
------------------------------------------------
``profile_min_dim_nm`` 在**量得出**时必须正好等于轮廓短边（10×6 mm 矩形 = 6_000_000 nm），
在**量不出**（草图无实体）时必须正好 -1；两种情形都不许是 0 —— 0 只表示"真为零"。

不许出现的红点判定
------------------
本文件**不含任何 skip**：族模块在场后行为用例全部真跑，夹具不成立要报红而不是回避。
断言只加不减：实现改坏（把测量退化成 -1、把校验器改成静默通过）时本文件必须红。
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402
from cadcore.kernel.nano import NanoKernel  # noqa: E402
from cadcore.ops import sw_features as sw  # noqa: E402

MM = 1_000_000                       # 1 mm = 1e6 nm
_SK1 = "a11ce000-0000-4000-8000-000000000010"
_SK2 = "a11ce000-0000-4000-8000-000000000020"


# --------------------------------------------------------------------------------------
# 夹具：L1 方言（entities + geom.x1/y1/x2/y2），与 tests/data/plate.json 同构
# --------------------------------------------------------------------------------------


def _line(x1: int, y1: int, x2: int, y2: int, index: int = 0) -> dict[str, Any]:
    return {"uuid": f"edge-{index}", "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


def _rect_entities(width_nm: int, height_nm: int) -> list[dict[str, Any]]:
    x0, y0 = -3 * MM, -2 * MM                      # 非零起点：包围盒尺寸不受原点影响
    corners = [(x0, y0), (x0 + width_nm, y0), (x0 + width_nm, y0 + height_nm), (x0, y0 + height_nm)]
    return [_line(corners[i][0], corners[i][1], corners[(i + 1) % 4][0], corners[(i + 1) % 4][1], i)
            for i in range(4)]


def _rect_sketch(uuid: str = _SK1, width_nm: int = 10 * MM, height_nm: int = 6 * MM) -> dict[str, Any]:
    """L1 草图：宽 10 mm × 高 6 mm 的闭合矩形（短边 6 mm）。"""
    return {"uuid": uuid, "plane": "XY", "entities": _rect_entities(width_nm, height_nm)}


def _unmeasured_sketch(uuid: str = _SK1) -> dict[str, Any]:
    """量不出包围盒的草图（无实体）：短边必须记 -1（未采），不是 0。"""
    return {"uuid": uuid, "plane": "XY", "entities": []}


def _doc(sketches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    default = [_rect_sketch(_SK1), _rect_sketch(_SK2, 20 * MM, 8 * MM)]
    return {"schema_version": 1, "unit": "mm", "standard_profile": "gbt",
            "sketches": list(sketches) if sketches is not None else default,
            "features": []}


def _uuids(doc: dict[str, Any]) -> list[str]:
    return [str(item.get("uuid")) for item in doc["features"] if isinstance(item, dict)]


def _record(doc: dict[str, Any], uuid: str) -> dict[str, Any]:
    for item in doc["features"]:
        if isinstance(item, dict) and item.get("uuid") == uuid:
            return item
    raise AssertionError(f"record {uuid} not in features table: {_uuids(doc)}")


def _expect(self: unittest.TestCase, reason: str, call: Any, *args: Any, **kwargs: Any) -> CadError:
    """断言抛 CadError 且 reason 精确匹配（reason 是机器可读契约，不是自由文案）。"""
    with self.assertRaises(CadError) as ctx:
        call(*args, **kwargs)
    self.assertEqual(ctx.exception.reason, reason, f"expected reason={reason!r}, got {ctx.exception.reason!r}")
    return ctx.exception


# --------------------------------------------------------------------------------------
# 0. 夹具自证（E2.5）：内核量我们的夹具，证明它是真件、不是自造方言
# --------------------------------------------------------------------------------------


class FixtureSelfProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = NanoKernel()

    def test_kernel_is_present_and_measures_the_fixture(self) -> None:
        """自证：真内核把 L1 夹具量成 10×6 mm 矩形（60 mm² / 32 mm）。

        量路径必须**有面**：nano 的 ``mass_properties`` 是轮廓级、只吃 ``role="face-bottom"``
        的边（``_prism`` 造的实体）。``sketch_to_wire`` 出的是**线框**（无边界面），拿它去量只会
        得到 ``area_nm2 = -1``（未采）—— 那是"尺子没搭到面上"，不是夹具不成立。故先按内核文档
        的文档级入口 ``shape_from_document``（与 ``GeometryTests`` 同一条路径）把夹具拉成实体再量；
        本自证只用内核、**不经过被被测的 sw_features**，避免循环取证。
        """
        self.assertTrue(self.kernel.available(), "nano kernel must be available; fixture proof needs it")
        document = {"sketches": [_rect_sketch()],
                    "features": [{"uuid": "feat-proof", "kind": "extrude", "params": {"distance_nm": MM}}]}
        solid = self.kernel.shape_from_document(document)
        props = self.kernel.mass_properties(solid)
        self.assertGreaterEqual(props.loops, 1, "a contour loop must really be measured (not an empty chain)")
        self.assertEqual(props.area_nm2, 60 * MM * MM, "fixture must be a real 10x6 mm L1 rectangle")
        self.assertEqual(props.perimeter_nm, 32 * MM)

    def test_fixture_sketch_is_read_only_for_the_family(self) -> None:
        sk = _rect_sketch()
        before = copy.deepcopy(sk)
        doc = _doc(sketches=[sk])
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        self.assertEqual(sk, before, "the family must not rewrite the profile sketch")


# --------------------------------------------------------------------------------------
# 1. 结构契约：FAMILY / OPERATIONS / MUST_HAVE_MAP / CALL_PLANS
# --------------------------------------------------------------------------------------


class ContractTests(unittest.TestCase):
    def test_family_and_operation_table(self) -> None:
        self.assertEqual(sw.FAMILY, "sw_features")
        self.assertEqual(len(sw.OPERATIONS), 18)
        for name, op in sw.OPERATIONS.items():
            self.assertTrue(callable(op), f"{name} must be callable")
        self.assertEqual(len(sw.MUST_HAVE_MAP), 18)
        for key, op_name in sw.MUST_HAVE_MAP.items():
            self.assertIn(op_name, sw.OPERATIONS, f"must_have key {key} maps to unknown op {op_name}")
        for key, plans in sw.CALL_PLANS.items():
            self.assertIn(key, sw.MUST_HAVE_MAP)
            for plan in plans:
                self.assertIsInstance(plan, dict)

    def test_every_operation_is_drivable_and_returns_records_in_the_table(self) -> None:
        doc = _doc()
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)          # 切割/修饰类需要已有实体
        drives: list[tuple[str, dict[str, Any]]] = [
            ("extrude_boss", {"distance_nm": 5 * MM, "sketch": _SK1}),
            ("extrude_cut", {"distance_nm": 2 * MM, "sketch": _SK1}),
            ("revolve_boss", {"angle_deg": 270.0, "sketch": _SK2}),
            ("revolve_cut", {"angle_deg": 180.0, "sketch": _SK2}),
            ("sweep_boss", {"path": _SK2}),
            ("loft_boss", {"sections": [_SK1, _SK2]}),
            ("fillet", {"radius_nm": 1 * MM}),
            ("chamfer", {"distance_nm": 1 * MM}),
            ("hole_wizard", {"diameter_nm": 4 * MM, "depth_nm": 3 * MM, "hole_type": "counterbore"}),
            ("simple_hole", {"diameter_nm": 4 * MM, "depth_nm": 2 * MM}),
            ("shell", {"thickness_nm": 1 * MM}),
            ("draft", {"angle_deg": 5.0, "neutral_plane": "XY"}),
            ("rib", {"thickness_nm": 1 * MM, "direction": "normal"}),
            ("set_end_condition", {"end_condition": "through_all"}),
            ("thin_extrude", {"thickness_nm": 1 * MM, "distance_nm": 4 * MM}),
            ("select_contours", {"distance_nm": 5 * MM}),
            ("merge_result", {}),
            ("edit_feature", {"distance_nm": 7 * MM}),
        ]
        self.assertEqual(sorted(name for name, _ in drives), sorted(sw.OPERATIONS))
        for name, kwargs in drives:
            with self.subTest(op=name):
                sketches_before = copy.deepcopy(doc["sketches"])
                created = sw.OPERATIONS[name](doc, **kwargs)
                self.assertIsInstance(created, list)
                self.assertTrue(created, f"{name} returned no uuid")
                self.assertTrue(all(isinstance(item, str) and item for item in created))
                known = set(_uuids(doc))
                for uuid in created:
                    self.assertIn(uuid, known, f"{name} returned a dangling uuid {uuid}")
                self.assertEqual(doc["sketches"], sketches_before,
                                 f"{name} must not touch the sketches table")

    def test_call_plans_are_literal_and_drive_their_operations(self) -> None:
        for key, plans in sw.CALL_PLANS.items():
            for plan in plans:
                self.assertNotIn("$SKETCH", repr(plan), "CALL_PLANS must stay literal")
        doc = _doc()
        thin = sw.OPERATIONS[sw.MUST_HAVE_MAP["thin_feature"]](doc, **sw.CALL_PLANS["thin_feature"][0])
        self.assertEqual(len(thin), 1)
        params = _record(doc, thin[0])["params"]
        self.assertEqual(params["thickness_nm"], 1 * MM)
        self.assertEqual(params["distance_nm"], 4 * MM)
        self.assertIs(params["thin"], True)
        doc2 = _doc()
        picked = sw.OPERATIONS[sw.MUST_HAVE_MAP["selected_contours"]](doc2, **sw.CALL_PLANS["selected_contours"][0])
        self.assertEqual(len(picked), 1)
        params2 = _record(doc2, picked[0])["params"]
        self.assertEqual(params2["distance_nm"], 5 * MM)
        self.assertEqual(params2["contours"], [_SK1, _SK2])


# --------------------------------------------------------------------------------------
# 2. 几何后果：10 mm × 6 mm × 5 mm（数值级，内核现算）
# --------------------------------------------------------------------------------------


class GeometryTests(unittest.TestCase):
    def test_extrude_boss_geometry_is_10x6x5(self) -> None:
        doc = _doc()
        created = sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        kernel = NanoKernel()
        shape = kernel.shape_from_document(doc)
        self.assertEqual(shape.depth_nm, 5 * MM, "the record's depth must reach the kernel")
        props = kernel.mass_properties(shape)
        self.assertEqual(props.area_nm2, 60 * MM * MM)
        self.assertEqual(props.volume_nm3, 300 * MM ** 3, "10mm x 6mm x 5mm = 300 mm^3")

    def test_measured_profile_short_edge_is_exact_integer_nm(self) -> None:
        doc = _doc()
        created = sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        params = _record(doc, created[0])["params"]
        self.assertEqual(params["profile_min_dim_nm"], 6 * MM, "short edge of a 10x6 rectangle is 6 mm")
        self.assertNotEqual(params["profile_min_dim_nm"], 0)

    def test_unmeasured_profile_is_minus_one_not_zero(self) -> None:
        doc = _doc(sketches=[_unmeasured_sketch(_SK1)])
        created = sw.thin_extrude(doc, thickness_nm=1 * MM, distance_nm=4 * MM, sketch=_SK1)
        params = _record(doc, created[0])["params"]
        self.assertEqual(params["profile_min_dim_nm"], -1, "unmeasured must be -1 (not sampled), never 0")
        self.assertEqual(params["thickness_nm_profile_check"], "unmeasured")
        self.assertNotEqual(params["profile_min_dim_nm"], 0)

    def test_fillet_guard_measures_the_real_l1_profile(self) -> None:
        doc = _doc()
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        _expect(self, "dimension_exceeds_profile", sw.fillet, doc, radius_nm=7 * MM)
        created = sw.fillet(doc, radius_nm=3 * MM)
        params = _record(doc, created[0])["params"]
        self.assertEqual(params["radius_nm_profile_check"], "ok")
        self.assertEqual(params["profile_min_dim_nm"], 6 * MM)

    def test_chamfer_shell_rib_thin_wall_guards_fire(self) -> None:
        doc = _doc()
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        _expect(self, "dimension_exceeds_profile", sw.chamfer, doc, distance_nm=6 * MM)
        _expect(self, "dimension_exceeds_profile", sw.shell, doc, thickness_nm=6 * MM)
        _expect(self, "dimension_exceeds_profile", sw.rib, doc, thickness_nm=6 * MM)
        _expect(self, "dimension_exceeds_profile", sw.thin_extrude, doc,
                thickness_nm=6 * MM, distance_nm=4 * MM)

    def test_hole_fit_checks_against_the_measured_bbox(self) -> None:
        doc = _doc()
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        _expect(self, "dimension_exceeds_profile", sw.hole_wizard, doc,
                diameter_nm=12 * MM, depth_nm=3 * MM)
        _expect(self, "dimension_exceeds_profile", sw.simple_hole, doc, diameter_nm=12 * MM, depth_nm=2 * MM)
        created = sw.hole_wizard(doc, diameter_nm=4 * MM, depth_nm=3 * MM)
        params = _record(doc, created[0])["params"]
        self.assertEqual(params["diameter_nm"], 4 * MM)
        self.assertEqual(params["profile_max_dim_nm"], 10 * MM, "10x6 rectangle bbox max edge is 10 mm")


# --------------------------------------------------------------------------------------
# 3. 坏入参：逐条 reason（不静默降级）
# --------------------------------------------------------------------------------------


class ValidationTests(unittest.TestCase):
    def test_length_validator_reasons(self) -> None:
        doc = _doc()
        _expect(self, "missing_parameter", sw.extrude_boss, doc, distance_nm=None)
        _expect(self, "bad_length", sw.extrude_boss, doc, distance_nm=True)
        _expect(self, "sub_nm_precision", sw.extrude_boss, doc, distance_nm=1.5)
        _expect(self, "non_positive_length", sw.extrude_boss, doc, distance_nm=0)
        _expect(self, "non_positive_length", sw.extrude_boss, doc, distance_nm=-5)

    def test_angle_validator_reasons(self) -> None:
        doc = _doc()
        _expect(self, "angle_out_of_range", sw.revolve_boss, doc, angle_deg=0.0)
        _expect(self, "angle_out_of_range", sw.revolve_boss, doc, angle_deg=361.0)
        _expect(self, "bad_angle", sw.revolve_boss, doc, angle_deg="ninety")
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        _expect(self, "angle_out_of_range", sw.draft, doc, angle_deg=90.0)
        _expect(self, "angle_out_of_range", sw.draft, doc, angle_deg=-89.0)

    def test_reference_validator_reasons(self) -> None:
        doc = _doc()
        _expect(self, "no_sketch", sw.extrude_boss, {"sketches": [], "features": []}, distance_nm=MM)
        _expect(self, "unknown_sketch", sw.extrude_boss, doc, distance_nm=5 * MM, sketch="not-a-sketch")
        _expect(self, "no_body", sw.extrude_cut, doc, distance_nm=MM)
        _expect(self, "unknown_ref", sw.sweep_boss, doc, path="not-a-path")
        _expect(self, "missing_parameter", sw.loft_boss, doc)
        _expect(self, "bad_ref", sw.loft_boss, doc, sections=[_SK1])

    def test_missing_and_extra_parameter_reasons(self) -> None:
        doc = _doc()
        _expect(self, "missing_parameter", sw.extrude_boss, doc, sketch=_SK1)
        _expect(self, "unexpected_kwarg", sw.extrude_boss, doc, distance_nm=MM, nonsense=1)
        sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        _expect(self, "nothing_to_edit", sw.edit_feature, doc)
        _expect(self, "unknown_feature", sw.edit_feature, doc, feature_uuid="nope", distance_nm=MM)


# --------------------------------------------------------------------------------------
# 4. 语义：记录口径 / 就地提升 / 抑制 / 终止条件 / 编辑
# --------------------------------------------------------------------------------------


class RecordSemanticsTests(unittest.TestCase):
    def test_bare_sketch_dict_is_promoted_in_place(self) -> None:
        sk = _rect_sketch()
        created = sw.extrude_boss(sk, distance_nm=5 * MM)
        self.assertIn("features", sk, "a bare sketch dict must be promoted to a single-sketch document in place")
        self.assertEqual(len(sk["features"]), 1)
        self.assertEqual(sk["features"][0]["uuid"], created[0])
        self.assertEqual(sk["features"][0]["sketch_uuid"], _SK1)

    def test_record_shape_and_cut_semantics(self) -> None:
        doc = _doc()
        boss = sw.extrude_boss(doc, distance_nm=5 * MM, sketch=_SK1)
        boss_record = _record(doc, boss[0])
        self.assertEqual(boss_record["kind"], "extrude")
        self.assertEqual(boss_record["sketch_uuid"], _SK1)
        self.assertEqual(boss_record["inputs"], [_SK1])
        self.assertIs(boss_record["suppressed"], False)
        self.assertEqual(boss_record["params"]["op"], "new")
        cut = sw.extrude_cut(doc, distance_nm=1 * MM, flip=True)
        cut_record = _record(doc, cut[0])
        self.assertEqual(cut_record["params"]["op"], "cut")
        self.assertIs(cut_record["params"]["flip"], True)
        self.assertEqual(cut_record["inputs"], [boss[0], _SK1], "a cut must reference the body it cuts")

    def test_uuid_is_fresh_per_call(self) -> None:
        doc = _doc()
        first = sw.extrude_boss(doc, distance_nm=5 * MM)[0]
        second = sw.extrude_boss(doc, distance_nm=6 * MM)[0]
        self.assertNotEqual(first, second)
        self.assertEqual(len(_uuids(doc)), 2)

    def test_suppress_removes_the_body_from_live_features(self) -> None:
        control = _doc()                                          # 阳性对照用独立文档：
        sw.extrude_boss(control, distance_nm=5 * MM)              # 有实体时 fillet 不报 no_body
        self.assertEqual(len(sw.fillet(control, radius_nm=MM)), 1)
        doc = _doc()
        boss = sw.extrude_boss(doc, distance_nm=5 * MM)[0]
        edited = sw.edit_feature(doc, feature_uuid=boss, suppress=True)
        self.assertEqual(edited, [boss])
        self.assertIs(_record(doc, boss)["suppressed"], True)
        self.assertEqual(_record(doc, boss)["params"]["edit_count"], 1)
        self.assertEqual(len(_record(doc, boss)["params"]["edit_log"]), 1)
        _expect(self, "no_body", sw.fillet, doc, radius_nm=MM)

    def test_edit_feature_reorder_and_rename(self) -> None:
        doc = _doc()
        first = sw.extrude_boss(doc, distance_nm=5 * MM)[0]
        second = sw.extrude_boss(doc, distance_nm=6 * MM)[0]
        third = sw.extrude_boss(doc, distance_nm=7 * MM)[0]
        sw.edit_feature(doc, feature_uuid=first, reorder_to=2)
        self.assertEqual(_uuids(doc), [second, third, first])
        sw.edit_feature(doc, feature_uuid=first, rename="Base Flange")
        self.assertEqual(_record(doc, first)["params"]["name"], "base_flange")
        _expect(self, "bad_index", sw.edit_feature, doc, feature_uuid=first, reorder_to=-1)
        sw.edit_feature(doc, feature_uuid=first, distance_nm=9 * MM, depth_nm=None)
        self.assertEqual(_record(doc, first)["params"]["distance_nm"], 9 * MM)

    def test_set_end_condition_retargets_and_materialises(self) -> None:
        doc = _doc()
        boss = sw.extrude_boss(doc, distance_nm=5 * MM)[0]
        returned = sw.set_end_condition(doc, "through_all")
        self.assertEqual(returned, [boss], "retargeting must return the existing feature uuid")
        params = _record(doc, boss)["params"]
        self.assertEqual(params["end_condition"], "through_all")
        self.assertEqual(params["termination"], "through_all")
        self.assertEqual(params["extent"], "through_all")
        _expect(self, "missing_parameter", sw.set_end_condition, doc, "blind")
        fresh = _doc()
        materialised = sw.set_end_condition(fresh, "up_to_next")
        self.assertEqual(len(materialised), 1)
        self.assertIs(_record(fresh, materialised[0])["params"]["end_condition_materialised"], True)

    def test_merge_result_and_simple_hole_through_all(self) -> None:
        doc = _doc()
        boss = sw.extrude_boss(doc, distance_nm=5 * MM)[0]
        merged = sw.merge_result(doc)
        params = _record(doc, merged[0])["params"]
        self.assertEqual(params["op"], "boolean")
        self.assertEqual(params["sources"], [boss])
        self.assertEqual(params["source_count"], 1)
        hole = sw.simple_hole(doc, diameter_nm=4 * MM, through_all=True)
        hole_params = _record(doc, hole[0])["params"]
        self.assertIs(hole_params["through_all"], True)
        self.assertIsNone(hole_params["depth_nm"], "through_all carries no depth")
        self.assertEqual(hole_params["hole_type"], "simple")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
