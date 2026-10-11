"""tests.test_constraints_ext —— A10-E4 约束扩展单测（同心 / 等长 / 共线 / 中点 / 定点 / 删除入口）。

设计一句话
----------
新关系最容易出的两类事故是"关系只记了一笔声明、几何纹丝不动"（没进方程）与"删除留下残影"
（条目删了但方程还在 / DoF 没变）。所以每条新关系都配**几何断言**（圆心相等 / 等长 / 共线残差 /
中点坐标 / 定点不动）与**记账断言**（`equations` / `rank` / `dof` 三项）成对出现；
`fix` 另配一条"该红时红"的矛盾夹具（定点撞上另一个锚点 ⇒ 必须 `converged=False` 且残差如实报出），
证明它不是装饰性声明。既有 8 类关系的回归断言也在本文件里（老路径不许被新分支带坏）。

夹具自证
--------
夹具全部落在内存里的 L1 dict 草图上（不写盘、无副作用）；每个夹具都断言实体确实进了
`sketch.entities`。断言文案一律 ASCII（面向用户的文案只许来自语言包）。
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.sketch import commands, constraints  # noqa: E402

MM = 1_000_000


# --------------------------------------------------------------------------------------
# 夹具（L1 dict 形态：{"entities": [...], "constraints": [...], "dimensions": [...]}）
# --------------------------------------------------------------------------------------


def new_sketch() -> dict[str, Any]:
    """空草图（L1 结构；与 docmodel 解耦，本文件只按结构走）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def _landed(sk: dict[str, Any], uid: str) -> str:
    assert any(entity["uuid"] == uid for entity in sk["entities"]), "fixture self-check: entity landed"
    return uid


def add_line(sk: dict[str, Any], p1: tuple[int, int], p2: tuple[int, int], **kw: Any) -> str:
    return _landed(sk, commands.add_line(sk, p1=p1, p2=p2, **kw))


def add_circle(sk: dict[str, Any], center: tuple[int, int], radius_nm: int, **kw: Any) -> str:
    return _landed(sk, commands.add_circle(sk, center=center, radius_nm=radius_nm, **kw))


def add_arc(sk: dict[str, Any], center: tuple[int, int], radius_nm: int, start_deg: float, end_deg: float,
            **kw: Any) -> str:
    return _landed(
        sk, commands.add_arc(sk, center=center, radius_nm=radius_nm, start_deg=start_deg, end_deg=end_deg, **kw)
    )


def geom(sk: dict[str, Any], uid: str) -> dict[str, Any]:
    for entity in sk["entities"]:
        if entity["uuid"] == uid:
            return entity["geom"]
    raise AssertionError(f"fixture self-check: entity {uid} must exist")


def add(sk: dict[str, Any], kind: str, refs: list[tuple[str, str]], value_nm: int | None = None) -> str:
    uid = constraints.add_constraint(sk, kind=kind, refs=refs, value_nm=value_nm)
    assert isinstance(uid, str) and uid, "fixture self-check: add_constraint returns a uuid"
    return uid


def line_length(sk: dict[str, Any], uid: str) -> float:
    g = geom(sk, uid)
    return math.hypot(g["x2"] - g["x1"], g["y2"] - g["y1"])


def cross_of(sk: dict[str, Any], anchor: str, mover: str) -> int:
    """`(anchor.x2-anchor.x1, anchor.y2-anchor.y1) x (mover.start - anchor.start)`，整数纳米²。"""
    a, m = geom(sk, anchor), geom(sk, mover)
    return (a["x2"] - a["x1"]) * (m["y1"] - a["y1"]) - (a["y2"] - a["y1"]) * (m["x1"] - a["x1"])


# --------------------------------------------------------------------------------------
# concentric
# --------------------------------------------------------------------------------------


class ConcentricTest(unittest.TestCase):
    def test_two_circles_centres_become_equal(self) -> None:
        sk = new_sketch()
        c1 = add_circle(sk, (0, 0), MM)
        c2 = add_circle(sk, (5 * MM, 7 * MM), 3 * MM)
        add(sk, "concentric", [(c1, ""), (c2, "center")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(geom(sk, c1)["cx"] - geom(sk, c2)["cx"]), 1)
        self.assertLessEqual(abs(geom(sk, c1)["cy"] - geom(sk, c2)["cy"]), 1)
        self.assertEqual((result.equations, result.rank), (2, 2))
        self.assertEqual(result.dof, result.variables - result.rank)

    def test_circle_and_arc_share_a_centre(self) -> None:
        sk = new_sketch()
        circle = add_circle(sk, (0, 0), 2 * MM)
        arc = add_arc(sk, (3 * MM, -MM), MM, 0.0, 90.0)
        add(sk, "concentric", [(arc, "center"), (circle, "")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(geom(sk, circle)["cx"] - geom(sk, arc)["cx"]), 1)
        self.assertLessEqual(abs(geom(sk, circle)["cy"] - geom(sk, arc)["cy"]), 1)
        self.assertEqual((result.equations, result.rank), (2, 2))

    def test_concentric_rejects_a_line_reference(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (MM, 0))
        circle = add_circle(sk, (0, 0), MM)
        with self.assertRaises(constraints.SketchError):
            constraints.add_constraint(sk, kind="concentric", refs=[(line, ""), (circle, "")])
        self.assertEqual(sk["constraints"], [], "a rejected constraint must not be half-accepted")


# --------------------------------------------------------------------------------------
# equal
# --------------------------------------------------------------------------------------


class EqualTest(unittest.TestCase):
    def test_two_lines_become_equal_length(self) -> None:
        sk = new_sketch()
        first = add_line(sk, (0, 0), (MM, 0))
        second = add_line(sk, (0, 5 * MM), (2 * MM + 500_000, 5 * MM))
        add(sk, "equal", [(first, ""), (second, "")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(line_length(sk, first) - line_length(sk, second)), 1.0)
        self.assertEqual((result.equations, result.rank), (1, 1))

    def test_circle_and_arc_become_equal_radius(self) -> None:
        sk = new_sketch()
        circle = add_circle(sk, (0, 0), 500_000)
        arc = add_arc(sk, (5 * MM, 5 * MM), 800_000, 0.0, 90.0)
        add(sk, "equal", [(circle, "radius"), (arc, "")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(geom(sk, circle)["r"] - geom(sk, arc)["r"]), 1)
        self.assertEqual((result.equations, result.rank), (1, 1))

    def test_equal_rejects_mixed_line_and_circle(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (MM, 0))
        circle = add_circle(sk, (0, 0), MM)
        with self.assertRaises(constraints.SketchError):
            constraints.add_constraint(sk, kind="equal", refs=[(line, ""), (circle, "")])
        self.assertEqual(sk["constraints"], [])


# --------------------------------------------------------------------------------------
# collinear
# --------------------------------------------------------------------------------------


class CollinearTest(unittest.TestCase):
    def test_free_point_is_projected_onto_the_lattice_line(self) -> None:
        sk = new_sketch()
        anchors = add_line(sk, (0, 0), (2000, 1000), fixed=True)  # 方向 (2, 1)：整数格点上必有精确解
        mover = add_line(sk, (3000, 1400), (3000, 1600))
        add(sk, "collinear", [(anchors, "start"), (anchors, "end"), (mover, "start")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLess(result.residual, 1e-6)
        self.assertEqual(cross_of(sk, anchors, mover), 0, "three points must be exactly collinear in integer nm")
        g = geom(sk, anchors)
        self.assertEqual((g["x1"], g["y1"], g["x2"], g["y2"]), (0, 0, 2000, 1000), "anchors must not move")
        self.assertEqual((result.equations, result.rank), (1, 1))

    def test_point_on_a_fixed_horizontal_axis_reaches_exact_collinearity(self) -> None:
        sk = new_sketch()
        base = add_line(sk, (0, 0), (1000, 0), fixed=True)
        mover = add_line(sk, (1000, 480), (3000, 480))
        add(sk, "collinear", [(base, "start"), (base, "end"), (mover, "start")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLess(result.residual, 1e-6)
        self.assertEqual(geom(sk, mover)["y1"], 0)
        self.assertEqual(cross_of(sk, base, mover), 0)

    def test_collinear_requires_exactly_three_points(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (MM, 0))
        with self.assertRaises(constraints.SketchError):
            constraints.add_constraint(sk, kind="collinear", refs=[(line, "start"), (line, "end")])
        self.assertEqual(sk["constraints"], [])


# --------------------------------------------------------------------------------------
# midpoint
# --------------------------------------------------------------------------------------


class MidpointTest(unittest.TestCase):
    def test_circle_center_lands_on_the_midpoint(self) -> None:
        sk = new_sketch()
        base = add_line(sk, (0, 0), (1000, 400), fixed=True)
        marker = add_circle(sk, (100, 900), 50_000)
        add(sk, "midpoint", [(marker, "center"), (base, "")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(geom(sk, marker)["cx"] - 500), 1)
        self.assertLessEqual(abs(geom(sk, marker)["cy"] - 200), 1)
        self.assertEqual((result.equations, result.rank), (2, 2))

    def test_line_start_lands_on_the_midpoint(self) -> None:
        sk = new_sketch()
        base = add_line(sk, (0, 0), (2000, 0), fixed=True)
        mover = add_line(sk, (900, 100), (900, 3000))
        add(sk, "midpoint", [(mover, "start"), (base, "")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(geom(sk, mover)["x1"] - 1000), 1)
        self.assertLessEqual(abs(geom(sk, mover)["y1"] - 0), 1)

    def test_midpoint_rejects_two_points(self) -> None:
        sk = new_sketch()
        first = add_line(sk, (0, 0), (MM, 0))
        second = add_line(sk, (0, MM), (MM, MM))
        with self.assertRaises(constraints.SketchError):
            constraints.add_constraint(sk, kind="midpoint", refs=[(first, "start"), (second, "start")])
        self.assertEqual(sk["constraints"], [])


# --------------------------------------------------------------------------------------
# fix（定点）
# --------------------------------------------------------------------------------------


class FixTest(unittest.TestCase):
    def test_fix_adds_two_equations_and_two_degrees_of_freedom_less(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        before = constraints.solve(sk)
        self.assertEqual((before.equations, before.rank, before.dof), (0, 0, 4))

        add(sk, "fix", [(line, "start")])
        after = constraints.solve(sk)

        self.assertEqual((after.equations, after.rank, after.dof), (2, 2, 2))
        self.assertEqual((geom(sk, line)["x1"], geom(sk, line)["y1"]), (0, 0))

    def test_fixed_point_stays_while_its_partner_moves_onto_it(self) -> None:
        sk = new_sketch()
        pinned = add_line(sk, (0, 0), (1000, 0))
        free = add_line(sk, (5 * MM, 5 * MM), (6 * MM, 5 * MM))
        add(sk, "fix", [(pinned, "start")])
        add(sk, "coincident", [(pinned, "start"), (free, "start")])

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        # 没有 fix 的方程，最小二乘会把两点对半拉；定点必须原地不动，由对方走过来。
        self.assertEqual((geom(sk, pinned)["x1"], geom(sk, pinned)["y1"]), (0, 0))
        self.assertLessEqual(abs(geom(sk, free)["x1"] - geom(sk, pinned)["x1"]), 1)
        self.assertLessEqual(abs(geom(sk, free)["y1"] - geom(sk, pinned)["y1"]), 1)

    def test_fix_conflicting_with_another_anchor_reports_red(self) -> None:
        sk = new_sketch()
        mover = add_line(sk, (0, 0), (1000, 0))
        anchor = add_line(sk, (9000, 9000), (9000, 10000), fixed=True)
        add(sk, "fix", [(mover, "start")])
        add(sk, "coincident", [(mover, "start"), (anchor, "start")])

        result = constraints.solve(sk)

        self.assertFalse(result.converged, "fix must really enter the equations, not decorate them")
        self.assertGreater(result.residual, constraints.DEFAULT_TOL_NM)
        self.assertTrue(result.conflicting, result.verdict_line())


# --------------------------------------------------------------------------------------
# delete_constraint
# --------------------------------------------------------------------------------------


class DeleteConstraintTest(unittest.TestCase):
    def test_delete_removes_the_row_resolves_and_raises_dof(self) -> None:
        sk = new_sketch()
        first = add_line(sk, (0, 0), (1000, 0))
        second = add_line(sk, (5 * MM, 5 * MM), (6 * MM, 5 * MM))
        uid = add(sk, "coincident", [(first, "start"), (second, "start")])
        before = constraints.solve(sk)

        after = constraints.delete_constraint(sk, uid)

        self.assertEqual(sk["constraints"], [], "the deleted row must be gone from the document")
        self.assertEqual(after.equations, before.equations - 2)
        self.assertEqual(after.dof, before.dof + 2)
        self.assertEqual(after.dof, after.variables - after.rank)

    def test_delete_unknown_uuid_raises_and_keeps_the_table(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        add(sk, "fix", [(line, "start")])
        with self.assertRaises(constraints.UnknownReference):
            constraints.delete_constraint(sk, "no-such-constraint")
        self.assertEqual(len(sk["constraints"]), 1, "a failed delete must not remove anything")

    def test_delete_fix_lets_the_point_move_again(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        uid = add(sk, "fix", [(line, "start")])
        pinned = constraints.solve(sk)
        self.assertEqual(pinned.dof, 2)

        freed = constraints.delete_constraint(sk, uid)

        self.assertGreater(freed.dof, pinned.dof)
        self.assertEqual((freed.equations, freed.dof), (0, 4))


# --------------------------------------------------------------------------------------
# 回归：既有 8 类关系与共用入口不许被新分支带坏
# --------------------------------------------------------------------------------------


class LegacyRegressionTest(unittest.TestCase):
    _LEGACY: tuple[str, ...] = (
        "coincident",
        "horizontal",
        "vertical",
        "parallel",
        "perpendicular",
        "tangent",
        "distance",
        "radius",
    )
    _NEW: tuple[str, ...] = ("concentric", "equal", "collinear", "midpoint", "fix")

    def test_the_eight_legacy_kinds_are_still_accepted(self) -> None:
        sk = new_sketch()
        first = add_line(sk, (0, 0), (MM, 0))
        second = add_line(sk, (0, 2 * MM), (MM, 3 * MM))
        circle = add_circle(sk, (0, 0), 10_000)
        specs = (
            ("coincident", [(first, "start"), (second, "start")], None),
            ("horizontal", [(second, "")], None),
            ("vertical", [(first, "end"), (second, "end")], None),
            ("parallel", [(first, ""), (second, "")], None),
            ("perpendicular", [(first, ""), (second, "")], None),
            ("tangent", [(first, ""), (circle, "")], None),
            ("distance", [(first, "")], MM),
            ("radius", [(circle, "")], 10_000),
        )
        for kind, refs, value in specs:
            add(sk, kind, refs, value)

        self.assertEqual(len(sk["constraints"]), 8)
        for kind in self._LEGACY:
            self.assertIn(kind, constraints.CONSTRAINT_KINDS)
        self.assertIs(commands.CONSTRAINT_KINDS, constraints.CONSTRAINT_KINDS,
                      "the command layer re-exports the one whitelist, not a second copy")

    def test_parallel_and_distance_still_solve_on_the_legacy_path(self) -> None:
        sk = new_sketch()
        base = add_line(sk, (0, 0), (MM, 0), fixed=True)
        other = add_line(sk, (0, 5 * MM), (MM, 5 * MM + 3000))
        add(sk, "parallel", [(base, ""), (other, "")])
        add(sk, "distance", [(other, "")], 2 * MM)

        result = constraints.solve(sk)

        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(line_length(sk, other) - 2 * MM), 1.0)
        a, b = geom(sk, base), geom(sk, other)
        cross = (a["x2"] - a["x1"]) * (b["y2"] - b["y1"]) - (a["y2"] - a["y1"]) * (b["x2"] - b["x1"])
        self.assertEqual(cross, 0, "parallel must still mean exactly collinear directions for integer nm")

    def test_unknown_kind_is_still_rejected(self) -> None:
        sk = new_sketch()
        line = add_line(sk, (0, 0), (MM, 0))
        with self.assertRaises(constraints.UnsupportedKind):
            constraints.add_constraint(sk, kind="welded", refs=[(line, "")])
        self.assertEqual(sk["constraints"], [])

    def test_new_kinds_and_the_delete_entry_are_declared_publicly(self) -> None:
        for kind in self._NEW:
            self.assertIn(kind, constraints.CONSTRAINT_KINDS)
            self.assertIn(kind, constraints._EQUATION_BUILDERS,
                          "every new relation must own a residual builder, not just a whitelist entry")
        self.assertIn("delete_constraint", constraints.__all__)
        self.assertTrue(callable(constraints.delete_constraint))


if __name__ == "__main__":  # pragma: no cover - 直接跑本文件时的入口
    unittest.main()
