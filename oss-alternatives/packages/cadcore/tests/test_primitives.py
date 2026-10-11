"""test_primitives —— 成型图元命令的验收（矩形 / 正多边形 / 腰形槽 / 折线）。

尺度口径：断言一律打在**落库后的文档**上（uuid → 实体），不只看返回值；
闭合性用 A10 的 `constraints.closed_profiles()` 这把尺子量；polyline 的上游缺口**显式断言记下来**
（不静默返 0，也不假绿）。
"""

from __future__ import annotations

import math
import unittest

from cadcore.sketch import constraints as cons
from cadcore.sketch import primitives as prim


def new_sketch() -> dict:
    """一张空草图（契约结构：entities / constraints / dimensions）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def entity(sk, uuid_):
    return cons.find_entity(sk, uuid_)


def geom(sk, uuid_):
    return entity(sk, uuid_)["geom"]


def kind(sk, uuid_):
    return entity(sk, uuid_)["kind"]


def endpoints(sk, uuid_):
    """line 的两个端点（整数纳米）。"""
    g = geom(sk, uuid_)
    return (g["x1"], g["y1"]), (g["x2"], g["y2"])


class RectangleTests(unittest.TestCase):
    def test_returns_four_line_uuids_with_exact_corners(self):
        sk = new_sketch()
        uuids = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 500))
        self.assertEqual(len(uuids), 4)
        self.assertEqual(len(set(uuids)), 4)
        for uuid_ in uuids:
            self.assertIsInstance(uuid_, str)
            self.assertTrue(uuid_)
            self.assertEqual(kind(sk, uuid_), "line")
        self.assertEqual(
            [endpoints(sk, uuid_) for uuid_ in uuids],
            [((0, 0), (1000, 0)), ((1000, 0), (1000, 500)), ((1000, 500), (0, 500)), ((0, 500), (0, 0))],
        )
        self.assertEqual(len(sk["entities"]), 4)
        self.assertEqual(sk["dimensions"], [])

    def test_adds_two_horizontal_and_two_vertical_constraints(self):
        sk = new_sketch()
        uuids = set(prim.add_rectangle(sk, p1_nm=(-30, 7), p2_nm=(970, 507)))
        self.assertEqual(
            sorted(item["kind"] for item in sk["constraints"]),
            ["horizontal", "horizontal", "vertical", "vertical"],
        )
        for item in sk["constraints"]:
            self.assertEqual(len(item["refs"]), 1)
            ref_uuid, sub = item["refs"][0]
            self.assertIn(ref_uuid, uuids)
            self.assertEqual(sub, "")
            self.assertIsNone(item["value_nm"])

    def test_is_a_closed_profile_for_extrusion(self):
        sk = new_sketch()
        uuids = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 1000))
        loops = cons.closed_profiles(sk)
        self.assertEqual(len(loops), 1)
        self.assertEqual(len(loops[0]), 4)
        self.assertEqual(set(loops[0]), set(uuids))

    def test_solves_with_zero_residual(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 500))
        result = cons.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(result.residual, 0.0)
        self.assertEqual(result.dof, 12)  # 16 个变量 − 4 条独立方程
        self.assertEqual(result.status, "ok")

    def test_honours_layer(self):
        sk = new_sketch()
        for uuid_ in prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10, 10), layer="OUTLINE"):
            self.assertEqual(entity(sk, uuid_)["layer"], "OUTLINE")

    def test_rejects_degenerate_and_invalid_input(self):
        cases = [
            dict(p1_nm=(0, 0), p2_nm=(0, 0)),
            dict(p1_nm=(0, 0), p2_nm=(0, 500)),  # 零宽
            dict(p1_nm=(0, 0), p2_nm=(1000, 0)),  # 零高
            dict(p1_nm=(0, 0), p2_nm=(1000.5, 500)),  # 非整数纳米
            dict(p1_nm=(0, 0), p2_nm=(1000, True)),  # bool 不是长度
            dict(p1_nm=(0,), p2_nm=(1000, 500)),  # 不是点
            dict(p1_nm=(0, 0), p2_nm=(1000, 500), layer=""),
            dict(p1_nm=(0, 0), p2_nm=(1000, 500), layer=3),
        ]
        for case in cases:
            sk = new_sketch()
            with self.assertRaises(prim.CadError, msg=repr(case)):
                prim.add_rectangle(sk, **case)
            self.assertEqual(sk["entities"], [], msg=repr(case))  # 校验没过 ⇒ 一条几何都不落库
            self.assertEqual(sk["constraints"], [], msg=repr(case))


class PolygonTests(unittest.TestCase):
    def test_hexagon_vertices_lie_on_the_circle_and_chain_closes(self):
        sk = new_sketch()
        radius = 10_000_000
        uuids = prim.add_polygon(sk, center_nm=(1_000, -2_000), radius_nm=radius, sides=6)
        self.assertEqual(len(uuids), 6)
        self.assertEqual(len(set(uuids)), 6)
        for uuid_ in uuids:
            self.assertEqual(kind(sk, uuid_), "line")
        starts = [endpoints(sk, uuid_)[0] for uuid_ in uuids]
        self.assertEqual(len(set(starts)), 6)
        for x, y in starts:
            self.assertLessEqual(abs(math.hypot(x - 1_000, y + 2_000) - radius), 1.0)  # 取整误差 ≤ 1 nm
        for index, uuid_ in enumerate(uuids):  # 边首尾相接（几何闭合，不靠约束声明）
            self.assertEqual(endpoints(sk, uuid_)[1], endpoints(sk, uuids[(index + 1) % 6])[0])

    def test_adds_coincident_constraints_and_is_a_closed_profile(self):
        sk = new_sketch()
        uuids = prim.add_polygon(sk, center_nm=(0, 0), radius_nm=250_000, sides=5)
        self.assertEqual([item["kind"] for item in sk["constraints"]], ["coincident"] * 5)
        loops = cons.closed_profiles(sk)
        self.assertEqual(len(loops), 1)
        self.assertEqual(len(loops[0]), 5)
        self.assertEqual(set(loops[0]), set(uuids))
        result = cons.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(result.residual, 0.0)

    def test_inscribed_false_uses_the_circle_as_the_apothem(self):
        sk = new_sketch()
        radius = 1_000_000
        uuids = prim.add_polygon(sk, center_nm=(0, 0), radius_nm=radius, sides=4, inscribed=False)
        for uuid_ in uuids:  # 顶点距 = r / cos(45°)
            x, y = endpoints(sk, uuid_)[0]
            self.assertAlmostEqual(math.hypot(x, y), radius * math.sqrt(2.0), delta=2.0)
        for uuid_ in uuids:  # 边中点距 = 内切圆半径
            a, b = endpoints(sk, uuid_)
            middle = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            self.assertAlmostEqual(math.hypot(*middle), radius, delta=1.0)

    def test_rejects_bad_sides_and_radius(self):
        cases = [
            dict(center_nm=(0, 0), radius_nm=1000, sides=2),
            dict(center_nm=(0, 0), radius_nm=1000, sides=0),
            dict(center_nm=(0, 0), radius_nm=1000, sides=3.0),  # 整数口径：3.0 不算
            dict(center_nm=(0, 0), radius_nm=1000, sides=True),
            dict(center_nm=(0, 0), radius_nm=0, sides=4),
            dict(center_nm=(0, 0), radius_nm=-5, sides=4),
            dict(center_nm=(0, 0), radius_nm=1, sides=36),  # 1 nm 半径放不下 36 个不同顶点
            dict(center_nm=(0, 0), radius_nm=1000, sides=4, inscribed="yes"),
            dict(center_nm=(0, 0), radius_nm=1000, sides=4, layer=" "),
        ]
        for case in cases:
            sk = new_sketch()
            with self.assertRaises(prim.CadError, msg=repr(case)):
                prim.add_polygon(sk, **case)
            self.assertEqual(sk["entities"], [], msg=repr(case))


class SlotTests(unittest.TestCase):
    def test_shape_two_parallel_lines_and_two_semicircles(self):
        sk = new_sketch()
        uuids = prim.add_slot(sk, p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=20_000)
        self.assertEqual([kind(sk, uuid_) for uuid_ in uuids], ["line", "line", "arc", "arc"])
        self.assertEqual(endpoints(sk, uuids[0]), ((0, 10_000), (100_000, 10_000)))
        self.assertEqual(endpoints(sk, uuids[1]), ((100_000, -10_000), (0, -10_000)))
        for index in (2, 3):
            g = geom(sk, uuids[index])
            self.assertEqual(g["r"], 10_000)
            self.assertAlmostEqual((g["end_deg"] - g["start_deg"]) % 360.0, 180.0, places=6)
        self.assertEqual((geom(sk, uuids[2])["cx"], geom(sk, uuids[2])["cy"]), (100_000, 0))
        self.assertEqual((geom(sk, uuids[3])["cx"], geom(sk, uuids[3])["cy"]), (0, 0))

    def test_is_a_closed_profile_even_when_rotated(self):
        for p2 in ((100_000, 0), (30_000, 40_000), (-7_000, 24_000), (0, 50_000)):
            sk = new_sketch()
            uuids = prim.add_slot(sk, p1_nm=(0, 0), p2_nm=p2, width_nm=10_000)
            loops = cons.closed_profiles(sk)
            self.assertEqual(len(loops), 1, msg=repr(p2))
            self.assertEqual(set(loops[0]), set(uuids), msg=repr(p2))

    def test_tiny_slot_survives_integer_rounding(self):
        sk = new_sketch()
        uuids = prim.add_slot(sk, p1_nm=(0, 0), p2_nm=(3, 4), width_nm=2)
        self.assertEqual(len(uuids), 4)
        loops = cons.closed_profiles(sk)
        self.assertEqual(len(loops), 1)
        self.assertEqual(set(loops[0]), set(uuids))

    def test_rejects_degenerate_and_invalid_input(self):
        cases = [
            dict(p1_nm=(0, 0), p2_nm=(0, 0), width_nm=20_000),  # 零长槽
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=0),
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=-20_000),
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=15),  # 奇数 ⇒ 半圆半径非整数
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=10.5),
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=True),
            dict(p1_nm=(0, 0), p2_nm=(100_000, 0), width_nm=20_000, layer=""),
        ]
        for case in cases:
            sk = new_sketch()
            with self.assertRaises(prim.CadError, msg=repr(case)):
                prim.add_slot(sk, **case)
            self.assertEqual(sk["entities"], [], msg=repr(case))


class PolylineTests(unittest.TestCase):
    def test_is_one_entity_with_integer_nm_points(self):
        sk = new_sketch()
        uuids = prim.add_polyline(sk, [(0, 0), (1_000, 0), (1_000, 500)])
        self.assertEqual(len(uuids), 1)
        self.assertEqual(len(sk["entities"]), 1)  # 一个 polyline 实体，不是 N 条散线
        item = entity(sk, uuids[0])
        self.assertEqual(item["kind"], "polyline")
        self.assertEqual(item["layer"], "0")
        self.assertFalse(item["construction"])
        self.assertEqual(item["geom"], {"points": [[0, 0], [1_000, 0], [1_000, 500]]})
        self.assertTrue(
            all(isinstance(v, int) and not isinstance(v, bool) for p in item["geom"]["points"] for v in p)
        )
        self.assertEqual(cons.entity_kind(item), "polyline")  # 上游值域表认得这个 kind
        self.assertEqual(cons.LENGTH_FIELDS["polyline"], ())  # 它不参与求解变量（真为零，不是没看见）

    def test_closed_repeats_the_first_point(self):
        sk = new_sketch()
        uuid_ = prim.add_polyline(sk, [(0, 0), (1_000, 0), (1_000, 1_000)], closed=True)[0]
        points = geom(sk, uuid_)["points"]
        self.assertEqual(points[0], points[-1])  # 几何闭合：首点原样补在末点
        self.assertEqual(len(points), 4)
        already = new_sketch()
        uuid_2 = prim.add_polyline(already, [(0, 0), (1_000, 0), (1_000, 1_000), (0, 0)], closed=True)[0]
        self.assertEqual(geom(already, uuid_2)["points"], points)  # 首末点已相同 ⇒ 不重复补点

    def test_closed_profile_gap_is_recorded_not_faked(self):
        sk = new_sketch()
        uuid_ = prim.add_polyline(sk, [(0, 0), (1_000, 0), (1_000, 1_000)], closed=True)[0]
        self.assertEqual(prim.closed_profiles(sk), [(uuid_,)])  # 本席的超集：认
        self.assertEqual(prim.closed_profiles(sk, tol_nm=1.0), [(uuid_,)])
        self.assertEqual(cons.closed_profiles(sk), [])  # 上游冻结版不认 polyline（真为零，显式记账，不假绿）
        opened = new_sketch()
        prim.add_polyline(opened, [(0, 0), (1_000, 0), (1_000, 1_000)])
        self.assertEqual(prim.closed_profiles(opened), [])  # 未闭合的折线不是轮廓
        self.assertIn("polyline", prim.POLYLINE_CONSTRAINT_GAP)

    def test_does_not_break_solve_and_keeps_geometry(self):
        sk = new_sketch()
        prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1_000, 1_000))
        uuid_ = prim.add_polyline(sk, [(5_000, 0), (6_000, 0), (6_000, 1_000)], closed=True)[0]
        before = geom(sk, uuid_)["points"]
        result = cons.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(geom(sk, uuid_)["points"], before)  # 不在变量表里 ⇒ 求解不搬动它

    def test_rejects_bad_points(self):
        cases = [
            ([(0, 0)], {}),
            ([], {}),
            ([(0, 0), (0, 0)], {}),  # 零长段
            ([(0, 0), (1_000.5, 0)], {}),
            ([(0, 0), (1_000, 0)], {"closed": True}),  # 闭合折线至少 3 个不同顶点
            ([(0, 0), (1_000, 0), (1_000, 1_000)], {"closed": "yes"}),
            ("not points", {}),
            ([(0, 0), (True, 5)], {}),
            ([(0, 0), (1_000, 0)], {"layer": ""}),
        ]
        for points, kwargs in cases:
            sk = new_sketch()
            with self.assertRaises(prim.CadError, msg=repr((points, kwargs))):
                prim.add_polyline(sk, points, **kwargs)
            self.assertEqual(sk["entities"], [], msg=repr((points, kwargs)))


class ErrorFamilyTests(unittest.TestCase):
    def test_cad_error_is_catchable_by_every_existing_family(self):
        for base in (
            cons.SketchError,
            cons.GeometryError,
            cons.InvalidLength,
            cons.OutOfRange,
            cons.UnknownReference,
            cons.UnsupportedKind,
        ):
            self.assertTrue(issubclass(prim.CadError, base), base.__name__)
        with self.assertRaises(cons.SketchError) as caught:
            prim.add_polygon(new_sketch(), center_nm=(0, 0), radius_nm=0, sides=4)
        self.assertEqual(caught.exception.key, "err.invalid_length")
        self.assertTrue(caught.exception.detail)
        self.assertTrue(str(caught.exception).isascii())  # 源码里不留用户可见文案（只有 key + ASCII 细节）


class MixedSketchTests(unittest.TestCase):
    def test_rectangle_plus_polygon_yields_two_profiles_and_keeps_layers(self):
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(10_000, 10_000))
        triangle = prim.add_polygon(sk, center_nm=(50_000, 50_000), radius_nm=5_000, sides=3, layer="P2")
        loops = cons.closed_profiles(sk)
        self.assertEqual(len(loops), 2)
        self.assertEqual({frozenset(loop) for loop in loops}, {frozenset(rect), frozenset(triangle)})
        for uuid_ in triangle:
            self.assertEqual(entity(sk, uuid_)["layer"], "P2")
        self.assertTrue(cons.solve(sk).converged)


if __name__ == "__main__":  # pragma: no cover - 便于单独跑这一个文件
    unittest.main()
