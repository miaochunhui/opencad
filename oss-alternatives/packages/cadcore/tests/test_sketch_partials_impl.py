# SPDX-License-Identifier: Apache-2.0
"""test_sketch_partials_impl —— G13 草图族 partial 条目的**库级**补齐验收（2026-10-01，mch8）。

覆盖（每条 partial 一个类，断言打在做完的事上，不打在"函数存在"上）
------------------------------------------------------------------
1. 构造几何不进轮廓：`closed_profiles` 剔除 `construction` 边（矩形拆一条边 ⇒ 0 环）；
2. 原点基准：`("origin", "start")` 可被约束/尺寸引用，解算真的把几何拉到原点，且**不进 DoF 变量表**；
   未知 uuid 仍报 UnknownReference（阴性对照：不是"什么都收"）；
3. DoF 三态配色：empty/under/fully/over/conflict 五态各一色，`dof_colors()` 是副本；
4. 区域选择：`select_region` 命中包含该点的环、嵌套取最内层、点在外 ⇒ `None`（真 NA）；
5. 过定义诊断与修复：点名冲突/冗余来源 uuid，`repair_conflicts` 真删真重解到收敛；健康草图报真零。

夹具自证
--------
全部落在内存草图上（不写盘、无副作用）；每条几何夹具先断言"真件在场"（实体/环确实进了文档），
再动被测能力 —— 夹具本身不成立时先红在这里。
非 docstring 的字符串字面量一律 ASCII（与 `test_sketch.py` 既有口径一致）。
"""

from __future__ import annotations

import copy
import unittest

from cadcore.sketch import commands as cmd
from cadcore.sketch import constraints as cons
from cadcore.sketch import editing as ed
from cadcore.sketch import primitives as prim

MM = 1_000_000


def new_sketch() -> dict:
    """一张空草图（契约结构：entities / constraints / dimensions）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def start_nm(sk, uuid_):
    geom = cons.find_entity(sk, uuid_)["geom"]
    return (geom["x1"], geom["y1"])


class ConstructionProfileTests(unittest.TestCase):
    """构造几何：参考边/参考圆不进闭合轮廓（SolidWorks 语义）。"""

    def test_construction_edge_drops_out_of_closed_profiles(self):
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10 * MM, 5 * MM))
        self.assertEqual(len(cons.closed_profiles(sk)), 1, "fixture self-check: plain rectangle closes")
        cons.find_entity(sk, rect[0])["construction"] = True
        self.assertEqual(cons.closed_profiles(sk), [], "a construction edge must not carry a profile")
        self.assertEqual(prim.closed_profiles(sk, tol_nm=0), [])

    def test_construction_circle_is_not_a_profile(self):
        sk = new_sketch()
        circle = cmd.add_circle(sk, center=(0, 0), radius_nm=MM, construction=True)
        self.assertIsNotNone(cons.find_entity(sk, circle), "fixture self-check: the circle is in the doc")
        self.assertEqual(cons.closed_profiles(sk), [])

    def test_construction_polyline_is_not_a_profile(self):
        sk = new_sketch()
        poly = prim.add_polyline(sk, [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM), (0, 0)], closed=True)[0]
        self.assertEqual(len(prim.closed_profiles(sk)), 1, "fixture self-check: closed polyline is a loop")
        cons.find_entity(sk, poly)["construction"] = True
        self.assertEqual(prim.closed_profiles(sk), [])


class OriginDatumTests(unittest.TestCase):
    """原点基准：可引用、承重（几何真被拉到原点）、不进 DoF；坏引用照样报错。"""

    def test_coincident_to_origin_pulls_geometry_and_keeps_dof_accounting(self):
        sk = new_sketch()
        cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        moving = cmd.add_line(sk, p1=(4 * MM, 5 * MM), p2=(9 * MM, 5 * MM))
        before = cons.solve(sk)
        cid = cons.add_constraint(sk, kind="coincident", refs=[(moving, "start"), (cons.ORIGIN_UUID, "start")])
        self.assertTrue(cid, "fixture self-check: the constraint uuid is real")
        res = cons.solve(sk)
        self.assertTrue(res.converged)
        self.assertEqual(start_nm(sk, moving), (0, 0), "the moving endpoint must land on the origin")
        self.assertEqual(res.variables, before.variables, "the origin datum adds no solver variable")

    def test_origin_helpers_and_unknown_datum_refusal(self):
        self.assertEqual(cons.origin_point(), (0, 0))
        self.assertTrue(cons.is_origin_reference((cons.ORIGIN_UUID, "start")))
        self.assertTrue(cons.is_origin_reference(("origin", "center")))
        self.assertFalse(cons.is_origin_reference(("origin", "end")))
        self.assertFalse(cons.is_origin_reference(("00000000-0000-0000-0000-000000000000", "start")))
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(MM, MM), p2=(2 * MM, MM))
        with self.assertRaises(cons.UnknownReference):
            cons.add_constraint(sk, kind="coincident", refs=[(line, "start"), ("no-such-datum", "start")])
        with self.assertRaises(cons.UnknownReference):
            cons.add_constraint(sk, kind="coincident", refs=[(line, "start"), (cons.ORIGIN_UUID, "end")])
        with self.assertRaises(cons.UnknownReference):
            cons.add_constraint(sk, kind="fix", refs=[(cons.ORIGIN_UUID, "start")])
        with self.assertRaises(cons.UnknownReference):
            cons.add_constraint(sk, kind="parallel", refs=[(line, ""), (cons.ORIGIN_UUID, "")])

    def test_linear_dimension_from_origin_drives_the_point(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(3 * MM, 4 * MM))
        cmd.add_dimension(sk, kind="linear", refs=[(line, "start"), (cons.ORIGIN_UUID, "start")],
                          value_nm=5 * MM)
        res = cons.solve(sk)
        x, y = start_nm(sk, line)
        self.assertTrue(res.converged)
        self.assertLessEqual(abs((x * x + y * y) ** 0.5 - 5 * MM), 1.0, "start must sit 5 mm from the origin")


class DofStateColorTests(unittest.TestCase):
    """三态判定 → 画布配色：五态五色，真源唯一（副本改了不影响真源）。"""

    @staticmethod
    def _under():
        sk = new_sketch()
        cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        return sk

    @staticmethod
    def _fully():
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cons.add_constraint(sk, kind="fix", refs=[(line, "start")])
        cons.add_constraint(sk, kind="horizontal", refs=[(line, "")])
        cmd.add_dimension(sk, kind="linear", refs=[(line, "")], value_nm=10 * MM)
        return sk

    @staticmethod
    def _over():
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        cons.add_constraint(sk, kind="horizontal", refs=[(line, "")])
        cons.add_constraint(sk, kind="horizontal", refs=[(line, "")])
        return sk

    @staticmethod
    def _conflict():
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        cons.add_constraint(sk, kind="coincident", refs=[(b, "start"), (a, "start")])
        cons.add_constraint(sk, kind="horizontal", refs=[(b, "")])
        cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=10 * MM)
        cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=20 * MM)
        return sk

    def test_each_state_maps_to_its_own_color(self):
        cases = {"empty": new_sketch(), "under": self._under(), "fully": self._fully(),
                 "over": self._over(), "conflict": self._conflict()}
        seen = {}
        for expected, sk in cases.items():
            self.assertTrue(cons.sketch_entities(sk) or expected == "empty",
                            "fixture self-check: geometry is really in the document")
            block = cons.sketch_state_color(sk)
            self.assertEqual(block["state"], expected)
            self.assertEqual(block["color"], cons.dof_colors()[expected])
            self.assertEqual(cons.sketch_state(sk), expected)
            seen[expected] = block["color"]
            self.assertEqual(block["solved"]["dof"], block["dof"], "the color block quotes the same solve")
        self.assertEqual(len(set(seen.values())), 5, "the five states must not share a colour")

    def test_dof_colors_returns_a_copy(self):
        table = cons.dof_colors()
        table["under"] = "#000000"
        self.assertNotEqual(cons.dof_colors()["under"], "#000000", "mutating the copy must not leak")


class SelectRegionTests(unittest.TestCase):
    """点选闭合区域：命中 / 嵌套取最内层 / 点在外返回 None（真 NA）。"""

    def _two_shapes(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10 * MM, 5 * MM))
        circle = cmd.add_circle(sk, center=(20 * MM, 0), radius_nm=2 * MM)
        loops = cons.closed_profiles(sk)
        self.assertEqual(len(loops), 2, "fixture self-check: one rectangle + one circle")
        rect_loop = next(loop for loop in loops if len(loop) == 4)
        return sk, rect_loop, (circle,)

    def test_pick_inside_each_shape_and_none_outside(self):
        sk, rect_loop, circle_loop = self._two_shapes()
        self.assertEqual(cons.select_region(sk, point_nm=(5 * MM, 2 * MM)), rect_loop)
        self.assertEqual(cons.select_region(sk, point_nm=(20 * MM, 0)), circle_loop)
        self.assertIsNone(cons.select_region(sk, point_nm=(100 * MM, 100 * MM)))

    def test_nested_loops_pick_the_innermost(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(20 * MM, 20 * MM))
        inner = cmd.add_circle(sk, center=(10 * MM, 10 * MM), radius_nm=2 * MM)
        self.assertEqual(len(cons.closed_profiles(sk)), 2, "fixture self-check: outer + inner")
        self.assertEqual(cons.select_region(sk, point_nm=(10 * MM, 10 * MM)), (inner,))

    def test_bad_point_is_refused(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(MM, MM))
        for bad in ("x", (1, 2, 3), (1.5, 2)):
            with self.assertRaises(cons.SketchError):
                cons.select_region(sk, point_nm=bad)


class ConflictDiagnosisTests(unittest.TestCase):
    """过定义诊断点名来源 + 真修复到收敛；健康草图报真零。"""

    @staticmethod
    def _conflicting():
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        cons.add_constraint(sk, kind="coincident", refs=[(b, "start"), (a, "start")])
        cons.add_constraint(sk, kind="horizontal", refs=[(b, "")])
        first = cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=10 * MM)
        second = cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=20 * MM)
        return sk, {first, second}

    @staticmethod
    def _healthy():
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        cons.add_constraint(sk, kind="coincident", refs=[(b, "start"), (a, "start")])
        cons.add_constraint(sk, kind="horizontal", refs=[(b, "")])
        cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=10 * MM)
        return sk

    def test_diagnosis_names_the_source_uuids(self):
        sk, dims = self._conflicting()
        diag = cons.diagnose_conflicts(sk)
        self.assertTrue(diag["conflicting"])
        self.assertTrue(diag["conflicts"], "a conflict must name at least one source item")
        named = {item["uuid"] for item in diag["conflicts"]} | {item["uuid"] for item in diag["redundant_items"]}
        self.assertTrue(named & dims, "the diagnosis must point at the driving dimensions")
        self.assertTrue(all(item["source"] in ("constraint", "dimension") for item in diag["conflicts"]))

    def test_repair_drops_sources_and_converges(self):
        sk, dims = self._conflicting()
        report = cons.repair_conflicts(sk)
        self.assertTrue(report["converged"])
        self.assertTrue(report["removed"])
        self.assertTrue(set(report["removed"]) & dims)
        self.assertEqual(report["reason"], "converged")
        self.assertFalse(cons.diagnose_conflicts(sk)["conflicting"])

    def test_healthy_sketch_reports_true_zero(self):
        sk = self._healthy()
        self.assertTrue(cons.solve(sk).converged, "fixture self-check: the healthy sketch solves")
        diag = cons.diagnose_conflicts(sk)
        self.assertFalse(diag["conflicting"])
        self.assertEqual(diag["conflicts"], [])
        self.assertEqual(diag["redundant"], 0)
        self.assertEqual(diag["redundant_items"], [])
        report = cons.repair_conflicts(sk)
        self.assertEqual(report["removed"], [], "nothing to repair is a true zero, not a failure")
        self.assertTrue(report["converged"])


class UpdateDimensionTests(unittest.TestCase):
    """驱动尺寸改值：真调用入口 + 重解后几何跟着变；参考尺寸 / 未知 uuid / 坏角度都当场拒绝。"""

    def test_update_driving_dimension_resizes_geometry(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(4 * MM, 0))
        dim = cmd.add_dimension(sk, kind="linear", refs=[(line, "")], value_nm=6 * MM)
        cons.solve(sk)
        self.assertLessEqual(abs(cons.measure_dimension(sk, sk["dimensions"][0]) - 6 * MM), 1.0)
        self.assertEqual(cmd.update_dimension(sk, dim, value_nm=11 * MM), dim)
        cons.solve(sk)
        self.assertLessEqual(abs(cons.measure_dimension(sk, sk["dimensions"][0]) - 11 * MM), 1.0)

    def test_reference_dimension_and_unknown_uuid_are_refused(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(4 * MM, 0))
        reference = cmd.add_dimension(sk, kind="linear", refs=[(line, "")], value_nm=None)
        with self.assertRaises(cons.GeometryError):
            cmd.update_dimension(sk, reference, value_nm=MM)
        with self.assertRaises(cons.UnknownReference):
            cmd.update_dimension(sk, "00000000-0000-0000-0000-000000000000", value_nm=MM)

    def test_angular_bounds_are_still_enforced(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 0), p2=(5 * MM, 1 * MM))
        cons.add_constraint(sk, kind="coincident", refs=[(b, "start"), (a, "start")])
        dim = cmd.add_dimension(sk, kind="angular", refs=[(a, ""), (b, "")], value_nm=30 * cons.UDEG_PER_DEG)
        with self.assertRaises(cons.OutOfRange):
            cmd.update_dimension(sk, dim, value_nm=0)
        with self.assertRaises(cons.OutOfRange):
            cmd.update_dimension(sk, dim, value_nm=360 * cons.UDEG_PER_DEG)


class DeleteUndoRedoTests(unittest.TestCase):
    """删除实体：连带清引用、进撤销栈、undo/redo 整包往返。"""

    @staticmethod
    def _doc():
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        first = cons.add_constraint(sk, kind="horizontal", refs=[(a, "")])
        doomed = cons.add_constraint(sk, kind="horizontal", refs=[(b, "")])
        dim = cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=10 * MM)
        return sk, a, b, first, doomed, dim

    def test_delete_cascades_and_undo_redo_round_trip(self):
        sk, a, b, first, doomed, dim = self._doc()
        before = {key: copy.deepcopy(sk[key]) for key in ("entities", "constraints", "dimensions")}
        report = cmd.delete_entity(sk, b)
        self.assertEqual(report["uuid"], b)
        self.assertEqual(report["constraints_removed"], [doomed])
        self.assertEqual(report["dimensions_removed"], [dim])
        self.assertEqual([item["uuid"] for item in sk["entities"]], [a])
        self.assertEqual([item["uuid"] for item in sk["constraints"]], [first])
        self.assertEqual(sk["dimensions"], [])
        self.assertTrue(cmd.undo(sk))
        self.assertEqual({key: sk[key] for key in ("entities", "constraints", "dimensions")}, before,
                         "undo must restore the document tables byte for byte")
        self.assertTrue(cmd.redo(sk))
        self.assertEqual([item["uuid"] for item in sk["entities"]], [a])
        self.assertEqual([item["uuid"] for item in sk["constraints"]], [first])
        self.assertEqual(sk["dimensions"], [])

    def test_history_lives_in_the_registered_extra_container(self):
        sk, _a, b, _first, _doomed, _dim = self._doc()
        cmd.delete_entity(sk, b)
        store = sk["extra"][cmd.HISTORY_KEY]
        self.assertEqual(len(store["undo"]), 1)
        self.assertEqual(store["redo"], [])

    def test_missing_entity_and_empty_stacks_are_reported_not_silent(self):
        sk, _a, _b, _first, _doomed, _dim = self._doc()
        with self.assertRaises(cons.UnknownReference):
            cmd.delete_entity(sk, "00000000-0000-0000-0000-000000000000")
        self.assertFalse(cmd.undo(sk), "nothing deleted yet -> undo is False")
        self.assertFalse(cmd.redo(sk), "nothing undone yet -> redo is False")


class FeatureCheckTests(unittest.TestCase):
    """特征前体检：闭合/悬空/矛盾/欠定义各归其位，ready 只被 error 挡。"""

    def test_healthy_rectangle_is_ready_with_under_defined_warning(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10 * MM, 5 * MM))
        report = ed.check_sketch(sk)
        self.assertTrue(report["ready"])
        self.assertEqual(report["n_errors"], 0)
        self.assertEqual(report["closed_profiles"], 1)
        self.assertEqual(report["dangling_endpoints"], 0)
        self.assertEqual([item["code"] for item in report["warnings"]], ["err.sketch.under_defined"])

    def test_open_chain_reports_dangling_and_no_profile(self):
        sk = new_sketch()
        cmd.add_line(sk, p1=(0, 0), p2=(5 * MM, 0))
        cmd.add_line(sk, p1=(5 * MM, 0), p2=(5 * MM, 5 * MM))
        report = ed.check_sketch(sk)
        self.assertFalse(report["ready"])
        codes = {item["code"] for item in report["errors"]}
        self.assertIn("err.sketch.open_contour", codes)
        self.assertIn("err.sketch.no_closed_contour", codes)
        self.assertEqual(report["dangling_endpoints"], 2)

    def test_conflicting_sketch_reports_the_conflict(self):
        sk = ConflictDiagnosisTests._conflicting()[0]
        report = ed.check_sketch(sk)
        self.assertTrue(report["conflicting"])
        self.assertIn("err.sketch.conflicting_constraints", {item["code"] for item in report["errors"]})
        self.assertFalse(report["ready"])

    def test_empty_sketch_is_reported_as_empty(self):
        report = ed.check_sketch(new_sketch())
        self.assertFalse(report["ready"])
        self.assertIn("err.sketch.empty", {item["code"] for item in report["errors"]})
        self.assertEqual(report["dof"], 0, "an empty sketch has a true zero dof, not an unmeasured one")
        self.assertEqual(report["state"], "empty")

    def test_construction_only_geometry_is_no_profile(self):
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10 * MM, 5 * MM))
        for uid in rect:
            cons.find_entity(sk, uid)["construction"] = True
        report = ed.check_sketch(sk)
        self.assertEqual(report["closed_profiles"], 0)
        self.assertIn("err.sketch.no_closed_contour", {item["code"] for item in report["errors"]})


class DragEntityTests(unittest.TestCase):
    """拖动：欠定义真能动、完全定义被拉回、锚定实体不动、折线显式拒绝。"""

    def test_under_defined_entity_moves_and_constraints_hold(self):
        sk = new_sketch()
        anchor = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        moving = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        cons.add_constraint(sk, kind="horizontal", refs=[(moving, "")])
        cons.solve(sk)
        report = ed.drag_entity(sk, moving, delta_nm=(5 * MM, 0))
        self.assertGreater(report["moved_nm"], 0.0)
        self.assertEqual(start_nm(sk, moving), (5 * MM, 5 * MM))
        self.assertEqual(start_nm(sk, anchor), (0, 0), "the anchored line must not drift")
        self.assertGreater(report["dof"], 0)
        self.assertTrue(report["converged"])

    def test_fully_defined_entity_is_pulled_back(self):
        # 绝对锚定用原点基准（`fix` 是"锚在当前坐标"，拖动后它会跟着新坐标走 —— 不是绝对约束）。
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cons.add_constraint(sk, kind="coincident", refs=[(line, "start"), (cons.ORIGIN_UUID, "start")])
        cons.add_constraint(sk, kind="horizontal", refs=[(line, "")])
        cmd.add_dimension(sk, kind="linear", refs=[(line, "")], value_nm=10 * MM)
        self.assertEqual(cons.solve(sk).dof, 0, "fixture self-check: fully defined against the origin")
        report = ed.drag_entity(sk, line, delta_nm=(5 * MM, 0))
        self.assertLessEqual(report["moved_nm"], 1.0, "a fully defined sketch pulls the geometry back")
        self.assertEqual(report["dof"], 0)
        self.assertEqual(report["state"], "fully")

    def test_anchored_entity_is_not_moved(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(1 * MM, 2 * MM), p2=(4 * MM, 2 * MM), fixed=True)
        report = ed.drag_entity(sk, line, delta_nm=(5 * MM, 5 * MM))
        self.assertTrue(report["anchored"])
        self.assertEqual(report["moved_nm"], 0.0)
        self.assertEqual(start_nm(sk, line), (1 * MM, 2 * MM))

    def test_polyline_drag_is_refused(self):
        sk = new_sketch()
        poly = prim.add_polyline(sk, [(0, 0), (MM, 0), (MM, MM)])[0]
        with self.assertRaises(cons.UnsupportedKind):
            ed.drag_entity(sk, poly, delta_nm=(MM, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
