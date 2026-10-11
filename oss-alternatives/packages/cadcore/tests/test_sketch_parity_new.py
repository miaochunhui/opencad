"""test_sketch_parity_new —— 草图命令面补全（B6/G13）的验收：编辑 / 推断 / 补齐 / 投影 / 显隐。

设计一句话
----------
**"命令在不在"不算数，"落库后的几何对不对"才算数**：每条断言都打在**文档**上（uuid → 实体/约束/尺寸），
不只看返回值。九条能力逐条至少一条回归用例，另加错误路径（未知引用 / 退化几何 / 无交点）与
**阴性对照**（不该加的关系不加、该报错的必须报错、构造上的坏字段必须被审计抓住）。

夹具自证
--------
夹具全部落在内存草图上（不写盘、不碰仓库里任何别的文件、无副作用）；每个几何夹具都先断言
`真件在场`（实体确实进了 `sketch.entities`、uuid 互不相同）。显隐那条另用
`docmodel.migrate` 做**双向**自证：自家写出来的状态不许多出 `unknown_field_preserved`，
而构造上的坏字段**必须**被同一条尺子报出来（证明尺子看得见红）。

说明
----
本文件里**非 docstring 的字符串字面量一律 ASCII**（断言文案用英文，与 `test_sketch.py` 的既有口径一致）。
"""

from __future__ import annotations

import copy
import json
import math
import unittest

from cadcore import docmodel
from cadcore.sketch import commands as cmd
from cadcore.sketch import constraints as cons
from cadcore.sketch import editing as ed
from cadcore.sketch import primitives as prim

MM = 1_000_000


def new_sketch() -> dict:
    """一张空草图（契约结构：entities / constraints / dimensions）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def entity(sk, uuid_):
    return cons.find_entity(sk, uuid_)


def geom(sk, uuid_):
    return entity(sk, uuid_)["geom"]


def pt(sk, uuid_, sub):
    """点引用 → 整数纳米坐标（与求解器同一口径）。"""
    g = geom(sk, uuid_)
    if sub == "start":
        return (g["x1"], g["y1"])
    if sub == "end":
        return (g["x2"], g["y2"])
    if sub == "center":
        return (g["cx"], g["cy"])
    raise KeyError(sub)


def endpoints(sk, uuid_):
    return pt(sk, uuid_, "start"), pt(sk, uuid_, "end")


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def line_distance(point, p1, p2):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    return abs(dx * (point[1] - p1[1]) - dy * (point[0] - p1[0])) / math.hypot(dx, dy)


def snapshot(sk):
    """全部实体的几何快照（比较"源有没有被动过"用）。"""
    return {item["uuid"]: copy.deepcopy(dict(item["geom"])) for item in sk["entities"]}


class FixtureSelfProofTests(unittest.TestCase):
    """夹具自证：几何夹具测的是真件，且不产生副作用。"""

    def test_fixtures_are_real_documents(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        b = cmd.add_circle(sk, center=(0, 0), radius_nm=MM)
        self.assertEqual(len(sk["entities"]), 2)
        self.assertNotEqual(a, b)
        self.assertEqual([item["kind"] for item in sk["entities"]], ["line", "circle"])
        self.assertIs(entity(sk, a), sk["entities"][0], "find_entity must return the stored entity, not a copy")

    def test_helpers_agree_with_solver_geometry(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(3 * MM, 4 * MM))
        self.assertEqual(endpoints(sk, line), ((0, 0), (3 * MM, 4 * MM)))
        self.assertAlmostEqual(dist((0, 0), pt(sk, line, "end")), 5 * MM, places=6)
        self.assertAlmostEqual(line_distance((0, 5 * MM), (0, 0), (10 * MM, 0)), 5 * MM, places=6)


class TrimAndExtendTests(unittest.TestCase):
    """剪裁与延伸：端点必须**恰好**落在交点（整数纳米精确相等）。"""

    def test_trim_puts_target_end_on_intersection(self):
        sk = new_sketch()
        target = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cutter = cmd.add_line(sk, p1=(4 * MM, -3 * MM), p2=(4 * MM, 3 * MM))
        out = ed.trim(sk, target, cutter_refs=[cutter], keep="start")
        self.assertEqual(out, [target])
        self.assertEqual(pt(sk, target, "start"), (0, 0))
        self.assertEqual(pt(sk, target, "end"), (4 * MM, 0))
        self.assertEqual(len(sk["entities"]), 2, "trim must rewrite in place, not add entities")

    def test_trim_keep_end_keeps_the_other_side(self):
        sk = new_sketch()
        target = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cutter = cmd.add_line(sk, p1=(6 * MM, -3 * MM), p2=(6 * MM, 3 * MM))
        ed.trim(sk, target, cutter_refs=[cutter], keep="end")
        self.assertEqual(pt(sk, target, "start"), (6 * MM, 0))
        self.assertEqual(pt(sk, target, "end"), (10 * MM, 0))

    def test_trim_rejects_cutter_that_misses_the_span(self):
        sk = new_sketch()
        target = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cutter = cmd.add_line(sk, p1=(20 * MM, -3 * MM), p2=(20 * MM, 3 * MM))
        with self.assertRaises(Exception) as ctx:
            ed.trim(sk, target, cutter_refs=[cutter], keep="start")
        self.assertEqual(getattr(ctx.exception, "reason", ""), "no_intersection")
        self.assertEqual(pt(sk, target, "end"), (10 * MM, 0), "a refused trim must not touch the target")

    def test_extend_reaches_boundary_and_holds_the_anchor(self):
        sk = new_sketch()
        target = cmd.add_line(sk, p1=(0, 0), p2=(3 * MM, 0))
        boundary = cmd.add_line(sk, p1=(8 * MM, -2 * MM), p2=(8 * MM, 2 * MM))
        out = ed.extend(sk, target, boundary_refs=[boundary], end="end")
        self.assertEqual(out, [target])
        self.assertEqual(pt(sk, target, "start"), (0, 0))
        self.assertEqual(pt(sk, target, "end"), (8 * MM, 0))

    def test_extend_rejects_when_nothing_is_ahead(self):
        sk = new_sketch()
        target = cmd.add_line(sk, p1=(0, 0), p2=(3 * MM, 0))
        behind = cmd.add_line(sk, p1=(-5 * MM, -2 * MM), p2=(-5 * MM, 2 * MM))
        with self.assertRaises(Exception) as ctx:
            ed.extend(sk, target, boundary_refs=[behind], end="end")
        self.assertEqual(getattr(ctx.exception, "reason", ""), "no_intersection")


class OffsetTests(unittest.TestCase):
    """等距实体（偏移）：新曲线与原曲线处处等距，源一个字节不动。"""

    def test_offset_line_is_parallel_at_exact_distance(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        before = snapshot(sk)
        out = ed.offset(sk, line, distance_nm=3 * MM, side=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(sk["entities"]), 2)
        self.assertEqual(snapshot(sk)[line], before[line], "the source line must not move")
        offset_line = out[0]
        self.assertEqual(pt(sk, offset_line, "start"), (0, 3 * MM))
        self.assertAlmostEqual(line_distance(pt(sk, line, "start"), *endpoints(sk, offset_line)), 3 * MM, places=6)
        self.assertAlmostEqual(line_distance(pt(sk, line, "end"), *endpoints(sk, offset_line)), 3 * MM, places=6)

    def test_offset_circle_grows_and_arc_keeps_angles(self):
        sk = new_sketch()
        circle = cmd.add_circle(sk, center=(1 * MM, 2 * MM), radius_nm=5 * MM)
        arc = cmd.add_arc(sk, center=(0, 0), radius_nm=4 * MM, start_deg=0.0, end_deg=90.0)
        grown = ed.offset(sk, circle, distance_nm=2 * MM, side=1)[0]
        self.assertEqual(geom(sk, grown)["r"], 7 * MM)
        self.assertEqual(pt(sk, grown, "center"), (1 * MM, 2 * MM))
        wider = ed.offset(sk, arc, distance_nm=MM, side=1)[0]
        self.assertEqual(geom(sk, wider)["r"], 5 * MM)
        self.assertEqual((geom(sk, wider)["start_deg"], geom(sk, wider)["end_deg"]), (0.0, 90.0))

    def test_offset_refuses_collapsing_inward_and_zero_distance(self):
        sk = new_sketch()
        circle = cmd.add_circle(sk, center=(0, 0), radius_nm=5 * MM)
        with self.assertRaises(Exception) as ctx:
            ed.offset(sk, circle, distance_nm=9 * MM, side=-1)
        self.assertEqual(getattr(ctx.exception, "reason", ""), "degenerate_offset")
        with self.assertRaises(Exception):
            ed.offset(sk, circle, distance_nm=0, side=1)
        self.assertEqual(len(sk["entities"]), 1, "refused offsets must not leave geometry behind")


class MirrorTests(unittest.TestCase):
    """镜像实体：关于给定轴真做反射（坐标精确），原件按开关保留或原地翻转。"""

    def test_mirror_adds_a_reflected_copy(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(1 * MM, 2 * MM), p2=(4 * MM, 2 * MM))
        before = snapshot(sk)
        out = ed.mirror(sk, [line], p1_nm=(5 * MM, 0), p2_nm=(5 * MM, 10 * MM), keep_original=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(sk["entities"]), 2)
        self.assertEqual(snapshot(sk)[line], before[line], "the original must not move")
        self.assertEqual(set(endpoints(sk, out[0])), {(9 * MM, 2 * MM), (6 * MM, 2 * MM)})

    def test_mirror_in_place_keeps_the_uuid(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(1 * MM, 2 * MM), p2=(4 * MM, 2 * MM))
        out = ed.mirror(sk, [line], p1_nm=(0, 0), p2_nm=(0, 10 * MM), keep_original=False)
        self.assertEqual(out, [line])
        self.assertEqual(set(endpoints(sk, line)), {(-1 * MM, 2 * MM), (-4 * MM, 2 * MM)})
        self.assertEqual(len(sk["entities"]), 1)

    def test_mirror_preserves_length_and_axis_distance(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(2 * MM, 1 * MM), p2=(7 * MM, 4 * MM))
        out = ed.mirror(sk, [line], p1_nm=(0, 0), p2_nm=(0, 10 * MM), keep_original=True)[0]
        self.assertAlmostEqual(dist(*endpoints(sk, line)), dist(*endpoints(sk, out)), places=6)
        for point, mirrored in zip(endpoints(sk, line), endpoints(sk, out)):
            self.assertEqual(mirrored[1], point[1], "mirroring about x=0 keeps y")
            self.assertEqual(mirrored[0], -point[0], "mirroring about x=0 negates x")


class LinearPatternTests(unittest.TestCase):
    """线性草图阵列：每个副本落在精确的整数纳米位置上。"""

    def test_array_linear_positions_copies(self):
        sk = new_sketch()
        hole = cmd.add_circle(sk, center=(0, 0), radius_nm=MM)
        before = snapshot(sk)
        out = ed.array_linear(sk, [hole], rows=3, cols=1, dx_nm=0, dy_nm=4 * MM)
        self.assertEqual(len(out), 2)
        self.assertEqual([pt(sk, uid, "center") for uid in out], [(0, 4 * MM), (0, 8 * MM)])
        self.assertEqual(snapshot(sk)[hole], before[hole], "the seed must not move")
        self.assertTrue(all(geom(sk, uid)["r"] == MM for uid in out))

    def test_array_linear_single_position_is_true_zero(self):
        sk = new_sketch()
        hole = cmd.add_circle(sk, center=(0, 0), radius_nm=MM)
        self.assertEqual(ed.array_linear(sk, [hole], rows=1, cols=1, dx_nm=0, dy_nm=0), [])
        self.assertEqual(len(sk["entities"]), 1)

    def test_array_linear_rejects_stacked_copies(self):
        sk = new_sketch()
        hole = cmd.add_circle(sk, center=(0, 0), radius_nm=MM)
        with self.assertRaises(Exception) as ctx:
            ed.array_linear(sk, [hole], rows=2, cols=2, dx_nm=0, dy_nm=0)
        self.assertEqual(getattr(ctx.exception, "reason", ""), "invalid_argument")
        self.assertEqual(len(sk["entities"]), 1)


class FilletChamferTests(unittest.TestCase):
    """草图圆角与倒角：切点/倒角点到角点的距离精确，圆弧真的与两线相切。"""

    def _corner(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        b = cmd.add_line(sk, p1=(0, 0), p2=(0, 10 * MM))
        return sk, a, b

    def test_fillet_arc_is_tangent_to_both_lines(self):
        sk, a, b = self._corner()
        out = ed.fillet(sk, a, b, radius_nm=2 * MM)
        self.assertEqual(len(out), 1)
        arc = out[0]
        self.assertEqual(entity(sk, arc)["kind"], "arc")
        center = pt(sk, arc, "center")
        radius = geom(sk, arc)["r"]
        self.assertAlmostEqual(line_distance(center, *endpoints(sk, a)), radius, places=6)
        self.assertAlmostEqual(line_distance(center, *endpoints(sk, b)), radius, places=6)
        self.assertIn((2 * MM, 0), endpoints(sk, a))
        self.assertIn((0, 2 * MM), endpoints(sk, b))
        self.assertEqual(dist(center, (2 * MM, 0)), 2 * MM)
        self.assertEqual(dist(center, (0, 2 * MM)), 2 * MM)

    def test_chamfer_cuts_corner_at_exact_distance(self):
        sk, a, b = self._corner()
        out = ed.chamfer(sk, a, b, distance_nm=3 * MM)
        self.assertEqual(len(out), 1)
        self.assertEqual(set(endpoints(sk, out[0])), {(3 * MM, 0), (0, 3 * MM)})
        self.assertIn((3 * MM, 0), endpoints(sk, a))
        self.assertIn((0, 3 * MM), endpoints(sk, b))
        self.assertAlmostEqual(dist(*endpoints(sk, out[0])), 3 * MM * math.sqrt(2), places=3)

    def test_fillet_refuses_parallel_lines(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        with self.assertRaises(Exception) as ctx:
            ed.fillet(sk, a, b, radius_nm=MM)
        self.assertEqual(getattr(ctx.exception, "reason", ""), "parallel_lines")


class SnapPointTests(unittest.TestCase):
    """自动捕捉：只吸已经存在的几何点；吸不上就是 None（不许伪装成原点）。"""

    def _sketch(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        circle = cmd.add_circle(sk, center=(5 * MM, 5 * MM), radius_nm=2 * MM)
        return sk, line, circle

    def test_hits_every_snap_kind(self):
        sk, line, circle = self._sketch()
        endpoint = ed.snap_point(sk, point_nm=(10 * MM + 300, 200), tol_nm=1000)
        self.assertEqual((endpoint["kind"], endpoint["ref"]), ("endpoint", [line, "end"]))
        self.assertEqual(endpoint["point_nm"], (10 * MM, 0))
        midpoint = ed.snap_point(sk, point_nm=(5 * MM + 400, 300), tol_nm=1000)
        self.assertEqual(midpoint["kind"], "midpoint")
        self.assertEqual(midpoint["point_nm"], (5 * MM, 0))
        center = ed.snap_point(sk, point_nm=(5 * MM + 500, 5 * MM - 500), tol_nm=1000)
        self.assertEqual((center["kind"], center["ref"]), ("center", [circle, "center"]))
        self.assertEqual(center["point_nm"], (5 * MM, 5 * MM))
        quadrant = ed.snap_point(sk, point_nm=(7 * MM + 200, 5 * MM + 300), tol_nm=1000)
        self.assertEqual(quadrant["kind"], "quadrant")
        self.assertEqual(quadrant["point_nm"], (7 * MM, 5 * MM))

    def test_miss_returns_none_and_the_original_point(self):
        sk, _line, _circle = self._sketch()
        miss = ed.snap_point(sk, point_nm=(50 * MM, 50 * MM), tol_nm=1000)
        self.assertIsNone(miss["kind"])
        self.assertIsNone(miss["ref"])
        self.assertEqual(miss["point_nm"], (50 * MM, 50 * MM))
        self.assertEqual(miss["distance_nm"], None)

    def test_tolerance_is_inclusive_and_deterministic(self):
        sk, line, _circle = self._sketch()
        exact = ed.snap_point(sk, point_nm=(10 * MM + 1000, 0), tol_nm=1000)
        self.assertEqual(exact["kind"], "endpoint")
        outside = ed.snap_point(sk, point_nm=(10 * MM + 1001, 0), tol_nm=1000)
        self.assertIsNone(outside["kind"])

    def test_equal_distance_tie_breaks_by_kind_priority(self):
        # 真平局：探针点离"线端点"与"圆心"一样远（各 400 nm）⇒ 端点类别优先，且结果可复现。
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cmd.add_circle(sk, center=(10 * MM + 800, 0), radius_nm=MM)
        probe = (10 * MM + 400, 0)
        first = ed.snap_point(sk, point_nm=probe, tol_nm=1000)
        second = ed.snap_point(sk, point_nm=probe, tol_nm=1000)
        self.assertEqual(first["ref"], [line, "end"])
        self.assertEqual((first["kind"], first["point_nm"]), ("endpoint", (10 * MM, 0)))
        self.assertEqual(first, second)

    def test_kind_filter_and_no_side_effects(self):
        sk, _line, circle = self._sketch()
        filtered = ed.snap_point(sk, point_nm=(5 * MM + 500, 5 * MM - 500), tol_nm=1000, kinds=["endpoint"])
        self.assertIsNone(filtered["kind"], "center must be filtered out when only endpoints are wanted")
        self.assertEqual(len(sk["entities"]), 2)
        self.assertEqual(sk["constraints"], [])
        with self.assertRaises(cons.UnsupportedKind):
            ed.snap_point(sk, point_nm=(5 * MM, 5 * MM), tol_nm=1000, kinds=["no_such_kind"])


class InferRelationsTests(unittest.TestCase):
    """自动关系推断：只加**几何上真成立**的关系，且加进去的关系是承重的。"""

    def test_adds_only_relations_that_hold(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 0), p2=(5 * MM, 3 * MM))
        far = cmd.add_line(sk, p1=(2 * MM, 7 * MM + 40), p2=(7 * MM, 9 * MM + 40))
        report = ed.infer_relations(sk, tol_nm=1000)
        kinds = sorted(item["kind"] for item in sk["constraints"])
        self.assertEqual(report["n_added"], 2)
        self.assertEqual(kinds, ["coincident", "horizontal"])
        self.assertEqual(report["kinds"], {"coincident": 1, "horizontal": 1})
        refs = [item["refs"] for item in sk["constraints"] if item["kind"] == "coincident"]
        self.assertEqual(refs, [[[a, "start"], [b, "start"]]])
        for item in sk["constraints"]:
            for ref in item["refs"]:
                self.assertNotEqual(ref[0], far, "a free-floating line must not gain relations")

    def test_inferred_relation_is_load_bearing(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(0, 0), p2=(5 * MM, 3 * MM))
        ed.infer_relations(sk, tol_nm=1000)
        geom(sk, b)["x1"] += 1000
        geom(sk, b)["y1"] += 1000
        result = cons.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(dist(pt(sk, a, "start"), pt(sk, b, "start")), 0.0)

    def test_second_call_is_deduped(self):
        sk = new_sketch()
        cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        cmd.add_line(sk, p1=(0, 0), p2=(5 * MM, 3 * MM))
        first = ed.infer_relations(sk, tol_nm=1000)
        second = ed.infer_relations(sk, tol_nm=1000)
        self.assertGreater(first["n_added"], 0)
        self.assertEqual(second["n_added"], 0)
        self.assertGreaterEqual(second["skipped_existing"], first["n_added"])
        self.assertEqual(len(sk["constraints"]), first["n_added"])

    def test_anchored_pairs_with_a_gap_get_no_contradictory_tie(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), fixed=True)
        b = cmd.add_line(sk, p1=(500, 500), p2=(5 * MM, 3 * MM), fixed=True)
        report = ed.infer_relations(sk, tol_nm=1000)
        kinds = sorted(item["kind"] for item in sk["constraints"])
        self.assertEqual(kinds, ["horizontal"], "anchored endpoints 700 nm apart must not be tied together")
        self.assertNotIn("coincident", report["kinds"])
        self.assertEqual(report["n_added"], 1)
        self.assertEqual(pt(sk, b, "start"), (500, 500))
        self.assertEqual(pt(sk, a, "start"), (0, 0))
        result = cons.solve(sk)
        self.assertTrue(result.converged, "an inferred relation set must stay solvable")

    def test_concentric_and_axis_relations(self):
        sk = new_sketch()
        c1 = cmd.add_circle(sk, center=(0, 0), radius_nm=2 * MM)
        c2 = cmd.add_circle(sk, center=(700, 0), radius_nm=MM)
        vertical = cmd.add_line(sk, p1=(3 * MM, 0), p2=(3 * MM + 400, 6 * MM))
        report = ed.infer_relations(sk, tol_nm=1000)
        kinds = sorted(item["kind"] for item in sk["constraints"])
        self.assertEqual(kinds, ["concentric", "vertical"])
        self.assertEqual(report["kinds"], {"concentric": 1, "vertical": 1})
        result = cons.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(dist(pt(sk, c1, "center"), pt(sk, c2, "center")), 0.0)
        self.assertEqual(pt(sk, vertical, "start")[0], pt(sk, vertical, "end")[0])


class FullyDefineTests(unittest.TestCase):
    """Fully Define 自动补齐：DoF 真降到 0，且几何一个纳米都不漂。"""

    def _under_defined(self):
        sk = new_sketch()
        flat = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        slant = cmd.add_line(sk, p1=(0, 5 * MM), p2=(6 * MM, 8 * MM))
        hole = cmd.add_circle(sk, center=(20 * MM, 3 * MM), radius_nm=2 * MM)
        return sk, flat, slant, hole

    def test_reaches_zero_dof_without_drift(self):
        sk, flat, slant, hole = self._under_defined()
        before = snapshot(sk)
        first = cons.solve(sk)
        self.assertGreater(first.dof, 0)
        report = ed.fully_define(sk)
        after = cons.solve(sk)
        self.assertEqual(report["dof_before"], first.dof)
        self.assertEqual(report["dof_after"], 0)
        self.assertEqual(after.dof, 0)
        self.assertTrue(after.converged)
        self.assertTrue(report["ok"])
        self.assertGreater(report["n_added"], 0)
        self.assertEqual(report["geometry_drift_nm"], 0.0)
        self.assertEqual(snapshot(sk), before, "fully defining must not move the geometry")
        self.assertEqual(len(sk["constraints"]) + len(sk["dimensions"]), report["n_added"])
        for uid in report["added"]:
            found = any(item["uuid"] == uid for item in list(sk["constraints"]) + list(sk["dimensions"]))
            self.assertTrue(found, "every reported uuid must really live in the document")

    def test_already_defined_is_true_zero(self):
        empty = new_sketch()
        report = ed.fully_define(empty)
        self.assertEqual((report["status"], report["n_added"], report["dof_after"]), ("already", 0, 0))
        self.assertTrue(report["ok"])
        anchored = new_sketch()
        cmd.add_line(anchored, p1=(0, 0), p2=(MM, 0), fixed=True)
        report = ed.fully_define(anchored)
        self.assertEqual(report["dof_after"], 0)
        self.assertEqual(report["n_added"], 0)
        self.assertEqual(len(anchored["constraints"]), 0)

    def test_dimension_based_closure_snaps_the_geometry_back(self):
        sk = new_sketch()
        line = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        report = ed.fully_define(sk)
        self.assertTrue(report["ok"])
        self.assertEqual(report["redundant"], 0, "fully defining must not over-constrain the sketch")
        self.assertTrue(any(item["kind"] == "linear" for item in sk["dimensions"]),
                        "an axis-aligned line must be closed with a driving dimension, not only anchors")
        before = snapshot(sk)
        geom(sk, line)["x2"] += 3 * MM
        after = cons.solve(sk)
        self.assertTrue(after.converged)
        self.assertEqual(after.dof, 0)
        self.assertEqual(snapshot(sk), before, "the added driving dimension must pull the geometry back")

    def test_rejects_impossible_budget(self):
        sk = new_sketch()
        cmd.add_line(sk, p1=(0, 0), p2=(MM, 0))
        with self.assertRaises(cons.OutOfRange):
            ed.fully_define(sk, max_steps=0)
        with self.assertRaises(cons.OutOfRange):
            ed.fully_define(sk, tol_nm=0)


class ConvertEntitiesTests(unittest.TestCase):
    """转换实体引用：几何逐字段投影进来、换新 uuid、源一个字节不动。"""

    def _source(self):
        src = new_sketch()
        line = cmd.add_line(src, p1=(0, 0), p2=(4 * MM, 0), layer="OUTLINE")
        circle = cmd.add_circle(src, center=(2 * MM, 2 * MM), radius_nm=MM)
        arc = cmd.add_arc(src, center=(0, 0), radius_nm=3 * MM, start_deg=0.0, end_deg=90.0)
        return src, line, circle, arc

    def test_projects_geometry_with_new_uuids(self):
        src, line, circle, arc = self._source()
        before = snapshot(src)
        sk = new_sketch()
        out = ed.convert_entities(sk, src, [line, circle, arc])
        self.assertEqual(len(out), 3)
        self.assertEqual(len(set(out)), 3)
        self.assertEqual([item["kind"] for item in sk["entities"]], ["line", "circle", "arc"])
        self.assertEqual(snapshot(src), before, "the source sketch must not change")
        for new_uuid, source_uuid in zip(out, (line, circle, arc)):
            self.assertNotEqual(new_uuid, source_uuid)
            self.assertEqual(geom(sk, new_uuid), geom(src, source_uuid))
        self.assertEqual(entity(sk, out[0])["layer"], "OUTLINE", "layer is inherited unless overridden")

    def test_projects_everything_when_refs_are_omitted(self):
        src, _line, _circle, _arc = self._source()
        sk = new_sketch()
        out = ed.convert_entities(sk, src, layer="PROJECTED", construction=True)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(item["layer"] == "PROJECTED" for item in sk["entities"]))
        self.assertTrue(all(item["construction"] is True for item in sk["entities"]))

    def test_unknown_reference_is_refused(self):
        src, _line, _circle, _arc = self._source()
        sk = new_sketch()
        with self.assertRaises(cons.UnknownReference):
            ed.convert_entities(sk, src, ["00000000-0000-0000-0000-000000000000"])
        self.assertEqual(sk["entities"], [])

    def test_unsupported_kind_and_degenerate_geometry_are_refused(self):
        src, line, _circle, _arc = self._source()
        src["entities"].append({"uuid": "spline-1", "kind": "spline", "layer": "0",
                                "construction": False, "geom": {"points": [[0, 0], [1, 1]]}})
        degenerate = new_sketch()
        degenerate["entities"].append({"uuid": "flat-1", "kind": "line", "layer": "0",
                                       "construction": False, "geom": {"x1": 5, "y1": 5, "x2": 5, "y2": 5}})
        sk = new_sketch()
        with self.assertRaises(cons.UnsupportedKind):
            ed.convert_entities(sk, src, ["spline-1"])
        with self.assertRaises(cons.InvalidLength):
            ed.convert_entities(sk, degenerate, ["flat-1"])
        self.assertEqual(sk["entities"], [], "validation happens before anything is written")
        self.assertEqual(len(src["entities"]), 4)

    def test_all_or_nothing_when_a_later_ref_is_bad(self):
        src, line, _circle, _arc = self._source()
        sk = new_sketch()
        with self.assertRaises(cons.UnknownReference):
            ed.convert_entities(sk, src, [line, "00000000-0000-0000-0000-000000000000"])
        self.assertEqual(sk["entities"], [], "half-accepted projections are forbidden")


class AnnotationVisibilityTests(unittest.TestCase):
    """显示/隐藏几何关系符号与尺寸：过滤是真跑文档，不是写死。"""

    def _documented(self):
        sk = new_sketch()
        a = cmd.add_line(sk, p1=(0, 0), p2=(10 * MM, 0))
        b = cmd.add_line(sk, p1=(0, 5 * MM), p2=(10 * MM, 5 * MM))
        rel_a = cons.add_constraint(sk, kind="horizontal", refs=[(a, "")])
        rel_b = cons.add_constraint(sk, kind="horizontal", refs=[(b, "")])
        dim = cmd.add_dimension(sk, kind="linear", refs=[(b, "")], value_nm=10 * MM)
        return sk, rel_a, rel_b, dim

    def test_default_shows_everything(self):
        sk, rel_a, rel_b, dim = self._documented()
        flags = ed.annotation_visibility(sk)
        self.assertEqual(flags, {"relations": True, "dimensions": True, "hidden": []})
        visible = ed.visible_annotations(sk)
        self.assertEqual(visible["relations"], [rel_a, rel_b])
        self.assertEqual(visible["dimensions"], [dim])
        self.assertEqual(visible["counts"], {"relations": 2, "dimensions": 1})
        self.assertNotIn("extra", sk, "reading visibility must not write the document")

    def test_hiding_relations_keeps_dimensions(self):
        sk, rel_a, rel_b, dim = self._documented()
        flags = ed.set_annotation_visibility(sk, relations=False)
        self.assertEqual(flags["relations"], False)
        self.assertEqual(flags["dimensions"], True)
        visible = ed.visible_annotations(sk)
        self.assertEqual(visible["relations"], [])
        self.assertEqual(visible["dimensions"], [dim])
        ed.set_annotation_visibility(sk, relations=True)
        self.assertEqual(ed.visible_annotations(sk)["relations"], [rel_a, rel_b])
        ed.set_annotation_visibility(sk, dimensions=False)
        self.assertEqual(ed.visible_annotations(sk)["dimensions"], [])
        self.assertEqual(ed.visible_annotations(sk)["relations"], [rel_a, rel_b])

    def test_hiding_one_annotation_and_restoring_it(self):
        sk, rel_a, rel_b, dim = self._documented()
        self.assertEqual(ed.hide_annotation(sk, rel_a), rel_a)
        self.assertEqual(ed.visible_annotations(sk)["relations"], [rel_b])
        self.assertEqual(ed.visible_annotations(sk)["counts"]["relations"], 1)
        ed.hide_annotation(sk, dim)
        self.assertEqual(ed.visible_annotations(sk)["dimensions"], [])
        self.assertEqual(ed.show_annotation(sk, rel_a), rel_a)
        self.assertEqual(sorted(ed.visible_annotations(sk)["relations"]), sorted([rel_a, rel_b]))
        self.assertEqual(ed.annotation_visibility(sk)["hidden"], [dim])

    def test_unknown_annotation_is_refused(self):
        sk, _rel_a, _rel_b, _dim = self._documented()
        with self.assertRaises(cons.UnknownReference):
            ed.hide_annotation(sk, "00000000-0000-0000-0000-000000000000")
        with self.assertRaises(cons.GeometryError):
            ed.set_annotation_visibility(sk, relations="yes")

    def test_display_state_lives_in_the_registered_extension_container(self):
        sk, _rel_a, _rel_b, _dim = self._documented()
        ed.set_annotation_visibility(sk, relations=False)
        ed.hide_annotation(sk, _rel_a)
        self.assertIn("extra", sk)
        payload = {"schema_version": docmodel.SCHEMA_VERSION,
                   "sketches": [{"uuid": sk.get("uuid", "sk-1"), "plane": sk.get("plane", "XY"),
                                 "entities": sk["entities"], "constraints": sk["constraints"],
                                 "dimensions": sk["dimensions"], "extra": sk["extra"]}]}
        def unknown_paths(document):
            return [item.get("path") for item in document.get(docmodel.DIAGNOSTICS_KEY, [])
                    if item.get("code") == "unknown_field_preserved"]

        migrated = docmodel.migrate(copy.deepcopy(payload))
        self.assertEqual(unknown_paths(migrated), [],
                         "own display state must not be reported as an unknown field")
        restored = json.loads(json.dumps(sk))
        self.assertEqual(restored["extra"], sk["extra"], "display state must survive a JSON round trip")
        # 阴性对照：同一条尺子必须抓得住"草图顶层塞了没登记的键"。
        broken = copy.deepcopy(payload)
        broken["sketches"][0]["legacy_layer_note"] = "kept"
        self.assertIn("$.sketches[0].legacy_layer_note", unknown_paths(docmodel.migrate(broken)))


if __name__ == "__main__":
    unittest.main()
