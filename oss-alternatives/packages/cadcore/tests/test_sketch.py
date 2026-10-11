"""tests.test_sketch —— A9/A10 草图命令与约束求解单测（43 例）。

设计一句话
----------
**求解器最容易出的两类事故是"假装收敛"与"dof 报假数"**：解不出来却画出一张看着像样的图、
冗余约束把自由度算成负数、矛盾约束被静默丢掉。所以本文件的尺子成对出现（阴阳双证）：
既测"该绿时绿"（重合/水平/垂直/平行/相切/距离/半径确实被解出来），也测"该红时红"
（矛盾约束 `converged=False` **且残差如实报出**；迭代预算不够时 `status=max_iter`；坏输入当场抛错）。

夹具自证
--------
夹具全部落在内存里的草图上（不碰仓库内任何别人的文件、不写盘、无副作用）；
每个几何夹具都断言"真件在场"（实体确实进了 `sketch.entities`、uuid 互不相同）。
`_SketchObj` / `_EntityObj` 是对偶夹具：证明命令与求解器按**结构**工作，而不是绑死某一种容器实现。

说明
----
本文件里**非 docstring 的字符串字面量一律 ASCII**（断言文案用英文、语言码由数据驱动取，
做坏夹具用 `chr()` 在运行期拼），与 A16 单测的既有口径一致：面向用户的文案只许来自语言包，
连测试文件也不给"中文文案"留先例。**理由与推导留在中文注释/docstring 里**，机检见
`SourceDisciplineTest`（它同时跑阴性对照，证明这把尺子看得见红）。
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import re
import shutil
import sys
import tempfile
import unittest
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[1]
_SKETCH_DIR = _PKG_ROOT / "cadcore" / "sketch"
_I18N_DIR = _PKG_ROOT / "cadcore" / "data" / "i18n"
_SOURCE_FILES = (_SKETCH_DIR / "constraints.py", _SKETCH_DIR / "commands.py")

MM = 1_000_000  # 1 mm = 1e6 nm


def _load_modules():
    """优先按包导入（真实布局）；兄弟席位的 `__init__.py` 还没落盘/写坏时按文件路径装载（缺席降级）。"""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))
    try:
        from cadcore.sketch import commands, constraints  # type: ignore[import-not-found]

        return commands, constraints
    except Exception:
        import importlib.util
        import types

        package = sys.modules.get("cadcore") or types.ModuleType("cadcore")
        package.__path__ = [str(_PKG_ROOT / "cadcore")]
        sys.modules["cadcore"] = package
        sub = types.ModuleType("cadcore.sketch")
        sub.__path__ = [str(_SKETCH_DIR)]
        sys.modules["cadcore.sketch"] = sub
        for name in ("constraints", "commands"):
            spec = importlib.util.spec_from_file_location(f"cadcore.sketch.{name}", _SKETCH_DIR / f"{name}.py")
            if spec is None or spec.loader is None:  # pragma: no cover - only if the file was deleted
                raise
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return sys.modules["cadcore.sketch.commands"], sys.modules["cadcore.sketch.constraints"]


commands, constraints = _load_modules()

SketchError = constraints.SketchError


def mm(value: float) -> int:
    """毫米 → 整数纳米（夹具数据用；产品侧走 A4 `units.mm_to_nm`）。"""
    return int(round(value * MM))


def new_sketch() -> dict[str, Any]:
    """内存草图（映射形态，字段照 `MVP-INTERFACES` §C）。"""
    return {
        "uuid": str(_uuid.uuid4()),
        "plane": "XY",
        "entities": [],
        "constraints": [],
        "dimensions": [],
        "solved": {},
    }


@dataclass
class _Geom:
    """对象式几何（对偶夹具：证明按结构取值/写值，不绑死 dict）。"""

    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    cx: int = 0
    cy: int = 0
    r: int = 0
    start_deg: float = 0.0
    end_deg: float = 0.0


@dataclass
class _EntityObj:
    """对象式实体（命令产出的实体是映射，这里手工造对象形态验结构兼容）。"""

    uuid: str
    kind: str
    layer: str = "0"
    construction: bool = False
    fixed: bool = False
    geom: _Geom = field(default_factory=_Geom)


@dataclass
class _SketchObj:
    """对象式草图（对偶夹具）。"""

    uuid: str = field(default_factory=lambda: str(_uuid.uuid4()))
    plane: str = "XY"
    entities: list[Any] = field(default_factory=list)
    constraints: list[Any] = field(default_factory=list)
    dimensions: list[Any] = field(default_factory=list)
    solved: dict[str, Any] = field(default_factory=dict)


def line(sk: Any, p1: tuple[int, int], p2: tuple[int, int], **kwargs: Any) -> str:
    return commands.add_line(sk, p1=p1, p2=p2, **kwargs)


def fixed_line(sk: Any, p1: tuple[int, int], p2: tuple[int, int]) -> str:
    """锚定直线：刚体自由度被钉住，DoF 才可能数到 0。"""
    return commands.add_line(sk, p1=p1, p2=p2, fixed=True)


def entity(sk: Any, entity_uuid: str) -> Any:
    for item in sk["entities"]:
        if item["uuid"] == entity_uuid:
            return item
    raise AssertionError(f"fixture is broken: entity {entity_uuid} is not in the sketch")


def geom(sk: Any, entity_uuid: str) -> dict[str, Any]:
    return entity(sk, entity_uuid)["geom"]


def length_of(sk: Any, entity_uuid: str) -> float:
    data = geom(sk, entity_uuid)
    return math.hypot(data["x2"] - data["x1"], data["y2"] - data["y1"])


class AddCommandsTest(unittest.TestCase):
    """A9 命令：写进去的东西必须可预测、可拒绝。"""

    def test_add_line_returns_uuid_and_integer_nm(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(10), 0))
        second = line(sk, (0, 0), (0, mm(5)), layer="outline")
        self.assertNotEqual(first, second, "fixture self-check: uuids must differ")
        self.assertEqual(len(sk["entities"]), 2, "fixture self-check: both entities landed")
        item = entity(sk, second)
        self.assertEqual(item["kind"], "line")
        self.assertEqual(item["layer"], "outline")
        self.assertFalse(item["construction"])
        self.assertNotIn("fixed", item, "an un-anchored entity must not carry a fixed field")
        for key, value in geom(sk, second).items():
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, bool)

    def test_add_line_rejects_degenerate_and_non_integer_input(self) -> None:
        sk = new_sketch()
        with self.assertRaises(SketchError) as caught:
            line(sk, (mm(1), mm(1)), (mm(1), mm(1)))
        self.assertEqual(caught.exception.key, "err.geometry_invalid")
        with self.assertRaises(SketchError) as caught2:
            line(sk, (0, 0), (0.5, 0))
        self.assertEqual(caught2.exception.key, "err.invalid_length")
        self.assertEqual(sk["entities"], [], "a rejected command must not half-write the document")

    def test_add_circle_radius_must_be_positive_integer_nm(self) -> None:
        sk = new_sketch()
        circle_uuid = commands.add_circle(sk, center=(mm(5), mm(5)), radius_nm=mm(2))
        self.assertEqual(geom(sk, circle_uuid), {"cx": mm(5), "cy": mm(5), "r": mm(2)})
        for bad in (0, -mm(1), 1.25, True):
            with self.assertRaises(SketchError):
                commands.add_circle(sk, center=(0, 0), radius_nm=bad)
        self.assertEqual(len(sk["entities"]), 1, "rejected circles must not enter the document")

    def test_add_arc_normalises_angles_and_rejects_degenerate_arc(self) -> None:
        sk = new_sketch()
        arc_uuid = commands.add_arc(sk, center=(0, 0), radius_nm=mm(3), start_deg=-90, end_deg=450)
        data = geom(sk, arc_uuid)
        self.assertEqual(data["start_deg"], 270.0)
        self.assertEqual(data["end_deg"], 90.0)
        self.assertEqual(data["r"], mm(3))
        with self.assertRaises(SketchError) as caught:
            commands.add_arc(sk, center=(0, 0), radius_nm=mm(3), start_deg=0, end_deg=360)
        self.assertEqual(caught.exception.key, "err.geometry_invalid")

    def test_add_dimension_driving_vs_reference(self) -> None:
        sk = new_sketch()
        base = fixed_line(sk, (0, 0), (mm(10), 0))
        driving = commands.add_dimension(sk, kind="linear", refs=[(base, "")], value_nm=mm(10))
        reference = commands.add_dimension(sk, kind="linear", refs=[(base, "")], text_key="dim.overall")
        self.assertEqual(len(sk["dimensions"]), 2, "fixture self-check: both dimensions landed")
        driving_item = next(item for item in sk["dimensions"] if item["uuid"] == driving)
        reference_item = next(item for item in sk["dimensions"] if item["uuid"] == reference)
        self.assertEqual(driving_item["value_nm"], mm(10))
        self.assertIsNone(reference_item["value_nm"], "value_nm=None means a reference dimension")
        self.assertEqual(reference_item["text_key"], "dim.overall")
        self.assertEqual(driving_item["refs"], [[base, ""]], "refs must be (uuid, sub) pairs, never indices")

    def test_angular_dimension_value_is_micro_degrees(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(10), 0))
        second = line(sk, (0, 0), (0, mm(10)))
        dim_uuid = commands.add_dimension(sk, kind="angular", refs=[(first, ""), (second, "")],
                                          value_nm=90 * constraints.UDEG_PER_DEG)
        item = next(entry for entry in sk["dimensions"] if entry["uuid"] == dim_uuid)
        self.assertEqual(item["value_nm"], 90_000_000)
        self.assertAlmostEqual(constraints.udeg_to_deg(item["value_nm"]), 90.0)
        with self.assertRaises(SketchError) as caught:
            commands.add_dimension(sk, kind="angular", refs=[(first, ""), (second, "")], value_nm=0)
        self.assertEqual(caught.exception.key, "err.out_of_range")

    def test_add_constraint_writes_uuid_refs_and_rejects_bad_reference(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(10), 0))
        second = line(sk, (mm(10), mm(1)), (mm(20), mm(1)))
        constraint_uuid = commands.add_constraint(sk, kind="coincident",
                                                  refs=[(first, "end"), (second, "start")])
        self.assertEqual(len(sk["constraints"]), 1, "fixture self-check: constraint landed")
        stored = sk["constraints"][0]
        self.assertEqual(stored["uuid"], constraint_uuid)
        self.assertEqual(stored["refs"], [[first, "end"], [second, "start"]])
        self.assertIsNone(stored["value_nm"])
        with self.assertRaises(SketchError) as caught:
            commands.add_constraint(sk, kind="coincident", refs=[(first, "end"), ("no-such-uuid", "start")])
        self.assertEqual(caught.exception.key, "err.unknown_reference")
        with self.assertRaises(SketchError) as caught2:
            commands.add_constraint(sk, kind="coincident", refs=[(first, "center"), (second, "start")])
        self.assertEqual(caught2.exception.key, "err.unknown_reference")
        with self.assertRaises(SketchError) as caught3:
            commands.add_constraint(sk, kind="lock", refs=[(first, "")])
        self.assertEqual(caught3.exception.key, "err.not_implemented")
        self.assertEqual(len(sk["constraints"]), 1, "rejected constraints must not enter the document")

    def test_commands_work_on_object_style_sketch(self) -> None:
        sk = _SketchObj()
        first = commands.add_line(sk, p1=(0, 0), p2=(mm(10), 0), fixed=True)
        circle_uuid = commands.add_circle(sk, center=(mm(3), mm(4)), radius_nm=mm(1))
        self.assertEqual(len(sk.entities), 2, "fixture self-check: object sketch accepted the entities")
        commands.add_constraint(sk, kind="coincident", refs=[(circle_uuid, "center"), (first, "start")])
        commands.add_constraint(sk, kind="radius", refs=[(circle_uuid, "")], value_nm=mm(2))
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(result.dof, 0)
        data = sk.entities[1]["geom"]
        self.assertIsInstance(data["cx"], int)
        self.assertEqual((data["cx"], data["cy"], data["r"]), (0, 0, mm(2)))

    def test_object_style_entities_are_read_and_written_in_place(self) -> None:
        sk = _SketchObj()
        rail = _EntityObj(uuid=str(_uuid.uuid4()), kind="line", fixed=True,
                          geom=_Geom(x1=0, y1=0, x2=mm(10), y2=0))
        ball = _EntityObj(uuid=str(_uuid.uuid4()), kind="circle",
                          geom=_Geom(cx=mm(3), cy=mm(4), r=mm(1)))
        sk.entities.extend([rail, ball])
        commands.add_constraint(sk, kind="coincident", refs=[(ball.uuid, "center"), (rail.uuid, "start")])
        commands.add_constraint(sk, kind="radius", refs=[(ball.uuid, "")], value_nm=mm(2))
        self.assertEqual(len(sk.constraints), 2, "fixture self-check: both constraints landed")
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(result.dof, 0)
        self.assertEqual((ball.geom.cx, ball.geom.cy, ball.geom.r), (0, 0, mm(2)))
        self.assertIsInstance(ball.geom.cx, int, "object geometry must also land as integer nm")
        self.assertEqual((rail.geom.x1, rail.geom.y1, rail.geom.x2, rail.geom.y2), (0, 0, mm(10), 0))


class SolveBasicsTest(unittest.TestCase):
    """A10 求解：每种约束都要真的把几何解到满足（不是"没报错就算了"）。"""

    def test_empty_sketch_is_zero_dof_zero_residual(self) -> None:
        result = constraints.solve(new_sketch())
        self.assertTrue(result.converged)
        self.assertEqual(result.status, "empty")
        self.assertEqual((result.variables, result.equations, result.rank, result.dof), (0, 0, 0, 0))
        self.assertEqual(result.residual, 0.0, "no equations means a true zero residual, not 'unmeasured'")
        self.assertEqual(result.iterations, 0)

    def test_unconstrained_line_has_four_dof(self) -> None:
        sk = new_sketch()
        line(sk, (0, 0), (mm(10), mm(2)))
        result = constraints.solve(sk)
        self.assertTrue(result.converged)
        self.assertEqual(result.variables, 4)
        self.assertEqual(result.dof, 4, "a free line has 4 dof (x1,y1,x2,y2)")
        self.assertEqual(result.equations, 0)
        self.assertEqual(result.redundant, 0)

    def test_coincident_pulls_endpoints_together_exactly(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(10), 0))
        second = line(sk, (mm(10) + 500, 700), (mm(20), mm(5)))
        commands.add_constraint(sk, kind="coincident", refs=[(first, "end"), (second, "start")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(result.dof, 6, "8 variables minus 2 independent equations")
        self.assertLessEqual(result.residual, constraints.DEFAULT_TOL_NM)
        end = (geom(sk, first)["x2"], geom(sk, first)["y2"])
        start = (geom(sk, second)["x1"], geom(sk, second)["y1"])
        self.assertEqual(end, start, "coincident endpoints must end up exactly equal (integer nm)")

    def test_horizontal_and_vertical(self) -> None:
        sk = new_sketch()
        flat = line(sk, (0, 0), (mm(10), mm(1)))
        upright = line(sk, (0, 0), (mm(1), mm(10)))
        commands.add_constraint(sk, kind="horizontal", refs=[(flat, "")])
        commands.add_constraint(sk, kind="vertical", refs=[(upright, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(geom(sk, flat)["y1"], geom(sk, flat)["y2"])
        self.assertEqual(geom(sk, upright)["x1"], geom(sk, upright)["x2"])
        self.assertGreater(length_of(sk, flat), 0.0, "a horizontal constraint must not collapse the line")
        self.assertEqual(result.equations, 2)

    def test_distance_between_two_points(self) -> None:
        sk = new_sketch()
        first = fixed_line(sk, (0, 0), (mm(1), 0))
        second = line(sk, (mm(4), mm(3)), (mm(9), mm(9)))
        commands.add_constraint(sk, kind="distance", refs=[(first, "end"), (second, "start")],
                                value_nm=mm(5))
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        p = (geom(sk, first)["x2"], geom(sk, first)["y2"])
        q = (geom(sk, second)["x1"], geom(sk, second)["y1"])
        measured = math.hypot(p[0] - q[0], p[1] - q[1])
        self.assertLessEqual(abs(measured - mm(5)), constraints.DEFAULT_TOL_NM)
        self.assertEqual(len(sk["entities"]), 2, "fixture self-check: both entities are still there")

    def test_line_length_and_point_to_line_distance(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(20), 0))
        bar = line(sk, (mm(2), mm(7)), (mm(9), mm(7)))
        commands.add_constraint(sk, kind="distance", refs=[(bar, "")], value_nm=mm(10))
        commands.add_constraint(sk, kind="distance", refs=[(bar, "start"), (rail, "")], value_nm=mm(3))
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertLessEqual(abs(length_of(sk, bar) - mm(10)), constraints.DEFAULT_TOL_NM)
        self.assertLessEqual(abs(abs(geom(sk, bar)["y1"]) - mm(3)), constraints.DEFAULT_TOL_NM)

    def test_radius_constraint_and_diameter_dimension(self) -> None:
        sk = new_sketch()
        fixed_line(sk, (0, 0), (mm(1), 0))  # anchor the rigid-body dof so dof can reach zero
        first = commands.add_circle(sk, center=(0, 0), radius_nm=mm(1))
        second = commands.add_circle(sk, center=(mm(5), 0), radius_nm=mm(1))
        commands.add_constraint(sk, kind="radius", refs=[(first, "")], value_nm=mm(2))
        commands.add_constraint(sk, kind="radius", refs=[(first, ""), (second, "")])
        commands.add_dimension(sk, kind="diameter", refs=[(second, "")], value_nm=mm(4))
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(geom(sk, first)["r"], mm(2))
        self.assertEqual(geom(sk, second)["r"], mm(2), "equal radii + diameter 4mm means both are 2mm")
        self.assertEqual((result.equations, result.rank, result.redundant), (3, 2, 1),
                         "r1=2, r1=r2, 2*r2=4: the third equation is derivable (redundant)")
        self.assertTrue(result.over_constrained)
        self.assertFalse(result.conflicting, "redundant but consistent still converges")

    def test_parallel_and_perpendicular(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(20), 0))
        partner = line(sk, (0, mm(5)), (mm(7), mm(9)))
        upright = line(sk, (mm(2), mm(2)), (mm(6), mm(3)))
        commands.add_constraint(sk, kind="parallel", refs=[(rail, ""), (partner, "")])
        commands.add_constraint(sk, kind="perpendicular", refs=[(rail, ""), (upright, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        base = geom(sk, rail)
        dx1, dy1 = base["x2"] - base["x1"], base["y2"] - base["y1"]
        for uid, mode in ((partner, "parallel"), (upright, "perpendicular")):
            data = geom(sk, uid)
            dx2, dy2 = data["x2"] - data["x1"], data["y2"] - data["y1"]
            longest = max(math.hypot(dx1, dy1), math.hypot(dx2, dy2))
            if mode == "parallel":
                self.assertLessEqual(abs(dx1 * dy2 - dy1 * dx2) / longest, constraints.DEFAULT_TOL_NM)
            else:
                self.assertLessEqual(abs(dx1 * dx2 + dy1 * dy2) / longest, constraints.DEFAULT_TOL_NM)

    def test_tangent_between_line_and_circle(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(20), 0))
        ball = commands.add_circle(sk, center=(mm(5), mm(3)), radius_nm=mm(1))
        commands.add_constraint(sk, kind="tangent", refs=[(rail, ""), (ball, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        data = geom(sk, ball)
        self.assertLessEqual(abs(abs(data["cy"]) - data["r"]), constraints.DEFAULT_TOL_NM,
                             "tangency means centre-to-line distance equals the radius")

    def test_tangent_between_two_circles_picks_the_current_branch(self) -> None:
        sk = new_sketch()
        hub = commands.add_circle(sk, center=(0, 0), radius_nm=mm(1), fixed=True)
        rim = commands.add_circle(sk, center=(mm(5), 0), radius_nm=mm(1))
        commands.add_constraint(sk, kind="tangent", refs=[(hub, ""), (rim, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        gap = math.hypot(geom(sk, rim)["cx"] - geom(sk, hub)["cx"], geom(sk, rim)["cy"] - geom(sk, hub)["cy"])
        self.assertLessEqual(abs(gap - (geom(sk, hub)["r"] + geom(sk, rim)["r"])), constraints.DEFAULT_TOL_NM)

    def test_arc_endpoints_come_from_centre_radius_and_frozen_angles(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(10), 0))
        arc_uuid = commands.add_arc(sk, center=(mm(20), mm(20)), radius_nm=mm(4),
                                    start_deg=270, end_deg=0)
        # start = centre + (cos 270, sin 270) * r = (cx, cy - r)
        commands.add_constraint(sk, kind="coincident", refs=[(arc_uuid, "start"), (rail, "end")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual((result.variables, result.equations, result.dof), (3, 2, 1),
                         "an arc contributes only cx,cy,r (angles are frozen)")
        data = geom(sk, arc_uuid)
        self.assertEqual((data["cx"], data["cy"] - data["r"]), (mm(10), 0))
        self.assertEqual((data["start_deg"], data["end_deg"]), (270.0, 0.0), "solving must not touch angles")
        start = constraints._point(entity(sk, arc_uuid), "start")
        end = (geom(sk, rail)["x2"], geom(sk, rail)["y2"])
        self.assertEqual(start, end, "derived arc endpoints must land exactly on integer nm")

    def test_fixed_entity_lets_dof_reach_zero(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(10), 0))
        circle_uuid = commands.add_circle(sk, center=(mm(3), mm(4)), radius_nm=mm(1))
        commands.add_constraint(sk, kind="coincident", refs=[(circle_uuid, "center"), (rail, "start")])
        commands.add_constraint(sk, kind="radius", refs=[(circle_uuid, "")], value_nm=mm(2))
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual((result.variables, result.equations, result.rank), (3, 3, 3))
        self.assertEqual(result.dof, 0, "a fully constrained sketch must report dof 0")
        self.assertFalse(result.over_constrained)
        data = geom(sk, circle_uuid)
        self.assertEqual((data["cx"], data["cy"], data["r"]), (0, 0, mm(2)))

    def test_temporary_anchor_argument_also_freezes_geometry(self) -> None:
        sk = new_sketch()
        rail = line(sk, (0, 0), (mm(10), 0))
        ball = commands.add_circle(sk, center=(mm(4), mm(4)), radius_nm=mm(1))
        commands.add_constraint(sk, kind="coincident", refs=[(ball, "center"), (rail, "start")])
        result = constraints.solve(sk, fixed=[(rail, "")])
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual(result.variables, 3, "temporarily anchored line leaves only the circle's 3 variables")
        self.assertEqual(result.dof, 1, "3 variables minus 2 coincident equations; the radius stays free")
        data = geom(sk, ball)
        self.assertEqual((data["cx"], data["cy"]), (0, 0))
        self.assertEqual(geom(sk, rail)["x2"], mm(10), "an anchored entity must not move by a single nm")

    def test_solve_is_deterministic_and_idempotent(self) -> None:
        first = new_sketch()
        second = new_sketch()
        for sk in (first, second):
            rail = fixed_line(sk, (0, 0), (mm(10), 0))
            ball = commands.add_circle(sk, center=(mm(3), mm(4)), radius_nm=mm(1))
            commands.add_constraint(sk, kind="coincident", refs=[(ball, "center"), (rail, "start")])
            commands.add_constraint(sk, kind="radius", refs=[(ball, "")], value_nm=mm(2))
        result_a = constraints.solve(first)
        result_b = constraints.solve(second)
        self.assertEqual(result_a.as_dict(), result_b.as_dict(), "same geometry must give the same account")
        again = constraints.solve(first)
        self.assertTrue(again.converged)
        self.assertEqual(again.iterations, 0, "an already solved sketch must not iterate again")
        self.assertEqual(again.residual, result_a.residual)
        self.assertEqual(again.dof, result_a.dof)
        self.assertIn("verdict=PASS", again.verdict_line())

    def test_geometry_stays_integer_nm_and_solved_block_is_written(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(10), 0))
        ball = commands.add_circle(sk, center=(mm(3), mm(4)), radius_nm=mm(1))
        commands.add_constraint(sk, kind="coincident", refs=[(ball, "center"), (rail, "start")])
        result = constraints.solve(sk)
        for uid in (rail, ball):
            for key, value in geom(sk, uid).items():
                if key.endswith("_deg"):
                    self.assertIsInstance(value, float)
                else:
                    self.assertIsInstance(value, int, f"{key} must land as integer nm")
                    self.assertNotIsInstance(value, bool)
        solved = sk["solved"]
        self.assertEqual(solved["dof"], result.dof)
        self.assertIs(solved["converged"], True)
        self.assertIs(solved["cache"], True, "the contract requires solved.cache to be true")
        self.assertEqual(solved["residual_nm"], result.residual)

    def test_reference_dimensions_are_measured_after_solve(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(10), 0))
        upright = fixed_line(sk, (0, 0), (0, mm(4)))
        circle_uuid = commands.add_circle(sk, center=(mm(1), mm(1)), radius_nm=mm(2))
        length_dim = commands.add_dimension(sk, kind="linear", refs=[(rail, "")])
        radius_dim = commands.add_dimension(sk, kind="radial", refs=[(circle_uuid, "")])
        angle_dim = commands.add_dimension(sk, kind="angular", refs=[(rail, ""), (upright, "")])
        constraints.solve(sk)
        measured = {item["uuid"]: item for item in sk["dimensions"]}
        self.assertEqual(measured[length_dim]["measured_nm"], mm(10))
        self.assertEqual(measured[radius_dim]["measured_nm"], mm(2))
        self.assertEqual(measured[angle_dim]["measured_nm"], 90 * constraints.UDEG_PER_DEG)
        for uid in (length_dim, radius_dim, angle_dim):
            self.assertIsNone(measured[uid]["value_nm"], "back-filling must not turn a reference into a driver")


class ConstraintAccountingTest(unittest.TestCase):
    """DoF 记账与失败面：欠约束 / 过约束（冗余）/ 矛盾 / 迭代预算。"""

    def test_under_constrained_sketch_is_not_a_failure(self) -> None:
        sk = new_sketch()
        bar = line(sk, (0, 0), (mm(10), mm(1)))
        commands.add_constraint(sk, kind="horizontal", refs=[(bar, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, "under-constrained is not a failure: the constraints are met")
        self.assertEqual(result.dof, 3, "4 variables minus 1 independent equation")
        self.assertGreater(result.dof, 0)

    def test_redundant_constraints_are_reported_not_swallowed(self) -> None:
        sk = new_sketch()
        bar = line(sk, (0, 0), (mm(10), mm(1)))
        commands.add_constraint(sk, kind="horizontal", refs=[(bar, "")])
        commands.add_constraint(sk, kind="horizontal", refs=[(bar, "")])
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        self.assertEqual((result.equations, result.rank, result.redundant), (2, 1, 1))
        self.assertTrue(result.over_constrained)
        self.assertFalse(result.conflicting, "redundant but consistent is over-constrained, not conflicting")
        self.assertEqual(result.dof, 3, "a redundant equation must not push dof negative")

    def test_conflicting_constraints_report_residual_and_do_not_claim_success(self) -> None:
        sk = new_sketch()
        rail = fixed_line(sk, (0, 0), (mm(10), 0))
        commands.add_constraint(sk, kind="horizontal", refs=[(rail, "")])
        commands.add_constraint(sk, kind="vertical", refs=[(rail, "")])
        result = constraints.solve(sk)
        self.assertFalse(result.converged, "conflicting constraints must report converged=False")
        self.assertEqual(result.variables, 0, "the whole line is anchored: no free variables")
        self.assertGreaterEqual(result.residual, mm(10) - 1, "the residual must be reported honestly (~10 mm)")
        self.assertTrue(result.conflicting)
        self.assertIn(result.status, ("stalled", "max_iter"))
        worst = result.worst()
        self.assertIsNotNone(worst)
        self.assertIn("vertical", worst[0], f"the worst equation must be named: {worst}")
        self.assertIn("verdict=FAIL", result.verdict_line())
        self.assertIs(sk["solved"]["converged"], False)

    def test_iteration_budget_is_reported_not_hidden(self) -> None:
        def build() -> dict[str, Any]:
            sk = new_sketch()
            rail = fixed_line(sk, (0, 0), (mm(20), 0))
            bar = line(sk, (mm(1), mm(1)), (mm(3), mm(2.5)))
            commands.add_constraint(sk, kind="perpendicular", refs=[(rail, ""), (bar, "")])
            commands.add_constraint(sk, kind="distance", refs=[(bar, "")], value_nm=mm(5))
            return sk

        starved = constraints.solve(build(), max_iter=1)
        self.assertFalse(starved.converged, "one iteration is not enough for this coupled system")
        self.assertEqual(starved.status, "max_iter")
        self.assertEqual(starved.iterations, 1)
        self.assertGreater(starved.residual, constraints.DEFAULT_TOL_NM)
        self.assertGreater(len(starved.residuals), 0, "an unconverged solve must expose per-equation residuals")
        full = constraints.solve(build())
        self.assertTrue(full.converged, full.verdict_line())
        self.assertLess(full.residual, starved.residual, "the residual must really go down with more iterations")

    def test_bad_budget_or_tolerance_is_rejected(self) -> None:
        sk = new_sketch()
        for kwargs in ({"max_iter": 0}, {"max_iter": -3}, {"tol_nm": 0}, {"tol_nm": -1.0}):
            with self.assertRaises(SketchError) as caught:
                constraints.solve(sk, **kwargs)
            self.assertEqual(caught.exception.key, "err.out_of_range")

    def test_unknown_kinds_in_a_hand_edited_document_are_not_skipped_silently(self) -> None:
        sk = new_sketch()
        bar = line(sk, (0, 0), (mm(10), 0))
        sk["constraints"].append({"uuid": "hand-written", "kind": "lock", "refs": [[bar, ""]], "value_nm": None})
        with self.assertRaises(SketchError) as caught:
            constraints.solve(sk)
        self.assertEqual(caught.exception.key, "err.not_implemented")
        self.assertIn("lock", str(caught.exception))
        sk2 = new_sketch()
        line(sk2, (0, 0), (mm(10), 0))
        sk2["dimensions"].append({"uuid": "hand-written", "kind": "linear", "refs": [], "value_nm": None})
        with self.assertRaises(SketchError):
            constraints.solve(sk2)


class ClosedProfilesTest(unittest.TestCase):
    """闭合轮廓：按**几何**判闭合，不拿约束声明当几何。"""

    def _open_rectangle(self) -> tuple[dict[str, Any], dict[str, str]]:
        """四个角各留 ~707 nm 缝的四边形（夹具自证：解算前确实不闭合）。"""
        sk = new_sketch()
        uuids = {
            "bottom": line(sk, (0, 0), (mm(10), 0)),
            "right": line(sk, (mm(10) + 500, 500), (mm(10) + 500, mm(5))),
            "top": line(sk, (mm(10), mm(5) + 500), (0, mm(5) + 500)),
            "left": line(sk, (-500, mm(5)), (-500, 0)),
        }
        self.assertEqual(len(sk["entities"]), 4, "fixture self-check: all four edges are in")
        self.assertEqual(constraints.closed_profiles(sk), [], "fixture self-check: gapped rectangle is open")
        return sk, uuids

    def _close_corners(self, sk: dict[str, Any], u: dict[str, str]) -> None:
        for end_ref, start_ref in (
            ((u["bottom"], "end"), (u["right"], "start")),
            ((u["right"], "end"), (u["top"], "start")),
            ((u["top"], "end"), (u["left"], "start")),
            ((u["left"], "end"), (u["bottom"], "start")),
        ):
            commands.add_constraint(sk, kind="coincident", refs=[end_ref, start_ref])

    def test_closed_loop_after_solving_coincidences(self) -> None:
        sk, uuids = self._open_rectangle()
        self._close_corners(sk, uuids)
        result = constraints.solve(sk)
        self.assertTrue(result.converged, result.verdict_line())
        loops = constraints.closed_profiles(sk)
        self.assertEqual(len(loops), 1, f"exactly one closed profile expected, got {loops}")
        self.assertEqual(set(loops[0]), set(uuids.values()))
        self.assertEqual(len(loops[0]), 4)
        self.assertEqual(loops[0][0], min(uuids.values()), "canonical start: the smallest uuid")
        self.assertEqual(loops, constraints.closed_profiles(sk), "two calls must agree byte for byte")

    def test_tolerance_can_bridge_sub_nm_gaps_but_default_cannot(self) -> None:
        sk, uuids = self._open_rectangle()
        self.assertEqual(constraints.closed_profiles(sk), [], "strict equality by default: gaps do not close")
        bridged = constraints.closed_profiles(sk, tol_nm=1000)
        self.assertEqual(len(bridged), 1)
        self.assertEqual(set(bridged[0]), set(uuids.values()))

    def test_open_chain_and_circle(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(1), 0))
        second = line(sk, (mm(1), 0), (mm(2), 0))
        third = line(sk, (mm(2), 0), (mm(3), 0))
        self.assertEqual(constraints.closed_profiles(sk), [], "an open chain is no profile (empty = true zero)")
        circle_uuid = commands.add_circle(sk, center=(mm(5), mm(5)), radius_nm=mm(1))
        loops = constraints.closed_profiles(sk)
        self.assertEqual(loops, [(circle_uuid,)], "a full circle closes on its own")
        self.assertEqual(len({first, second, third}), 3, "fixture self-check: the chain edges are in")

    def test_degree_three_junction_is_not_reported_as_closed(self) -> None:
        sk = new_sketch()
        first = line(sk, (0, 0), (mm(1), 0))
        second = line(sk, (mm(1), 0), (mm(2), 0))
        third = line(sk, (mm(1), 0), (mm(1), mm(1)))
        self.assertEqual(constraints.closed_profiles(sk), [])
        self.assertEqual(len({first, second, third}), 3, "fixture self-check: all three edges are in")

    def test_two_arcs_can_form_a_closed_loop(self) -> None:
        sk = new_sketch()
        upper = commands.add_arc(sk, center=(0, 0), radius_nm=mm(10), start_deg=0, end_deg=90)
        lower = commands.add_arc(sk, center=(mm(10), mm(10)), radius_nm=mm(10),
                                 start_deg=180, end_deg=270)
        loops = constraints.closed_profiles(sk)
        self.assertEqual(len(loops), 1, f"two arcs sharing both endpoints form one loop, got {loops}")
        self.assertEqual(set(loops[0]), {upper, lower})
        self.assertEqual(len(loops[0]), 2)


class InterfaceContractTest(unittest.TestCase):
    """契约面：§B 冻结的签名与两条 import 路径都要成立（增补参数只许是带默认值的关键字参数）。"""

    def _params(self, fn: Any) -> dict[str, Any]:
        return dict(inspect.signature(fn).parameters)

    def test_a9_commands_match_the_frozen_signature(self) -> None:
        for name, required_names in (
            ("add_line", ("p1", "p2", "layer")),
            ("add_circle", ("center", "radius_nm", "layer")),
            ("add_arc", ("center", "radius_nm", "start_deg", "end_deg", "layer")),
            ("add_dimension", ("kind", "refs", "value_nm", "text_key")),
        ):
            params = self._params(getattr(commands, name))
            self.assertEqual(list(params)[0], "sk", f"{name}: the sketch comes first")
            for required in required_names:
                self.assertIn(required, params, f"{name} must accept {required}")
                self.assertEqual(params[required].kind, inspect.Parameter.KEYWORD_ONLY,
                                 f"{name}.{required} must stay keyword-only")
        self.assertEqual(self._params(commands.add_line)["layer"].default, "0")
        self.assertIsNone(self._params(commands.add_dimension)["value_nm"].default)

    def test_a10_entry_points_match_the_frozen_signature_and_are_shared(self) -> None:
        self.assertEqual(self._params(constraints.solve)["max_iter"].default, 200)
        self.assertEqual(list(self._params(constraints.closed_profiles))[:2], ["sk", "tol_nm"])
        for name in ("add_constraint", "solve", "closed_profiles"):
            self.assertIs(getattr(commands, name), getattr(constraints, name),
                          f"{name} must be one implementation re-exported, not a second copy")
        for required in ("kind", "refs", "value_nm"):
            self.assertEqual(self._params(constraints.add_constraint)[required].kind,
                             inspect.Parameter.KEYWORD_ONLY)


class SourceDisciplineTest(unittest.TestCase):
    """纪律的结构化自检：文案走 key、源码不留中文文案（改坏就报红，不靠人记得）。"""

    # CJK 与语言码的判据（正则源码本身是纯 ASCII 转义，不给"中文文案"留先例）
    _CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
    _LOCALE = re.compile(r"^(zh|ja|ko|en|de|fr)([-_][A-Za-z0-9]{2,8})*$")

    @staticmethod
    def _literal_nodes(path: Path) -> list[ast.Constant]:
        """非 docstring 的字符串字面量（docstring 是开发期文档，不算面向用户的文案）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        ]

    def _mutant(self, source: Path) -> tuple[Path, Path]:
        """在临时目录里造两个"写坏"的副本（阳性对照：尺子必须看得见红）。

        做坏用的中文/语言码在**运行期**用 `chr()` 拼出来 —— 夹具自己也不违反它测的规矩。
        """
        directory = Path(tempfile.mkdtemp(prefix="cadcore-sketch-mutant-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        original = source.read_text(encoding="utf-8")
        leak_text = chr(0x56FE) + chr(0x5C42)               # CJK leak fixture
        leak_tag = "-".join(("zh", "CN"))                   # locale-code leak fixture
        cjk_path = directory / "leaky_cjk.py"
        tag_path = directory / "leaky_tag.py"
        cjk_path.write_text(original + f'\n_LEAK_TEXT = "{leak_text}"\n', encoding="utf-8")
        tag_path.write_text(original + f'\n_LEAK_TAG = "{leak_tag}"\n', encoding="utf-8")
        self.assertTrue(cjk_path.is_file() and tag_path.is_file(), "fixture self-check: mutants written")
        return cjk_path, tag_path

    def test_sibling_plate_fixture_is_accepted_and_accounted(self) -> None:
        """跨席集成：夹具席的 `tests/data/plate.json` 必须被本层**接受**，且账目自洽。

        只要求"能吃下 + 不假绿"（`dof = 变量数 − 秩`；未收敛必然残差超容差），不要求它一定全约束 ——
        夹具席的模型可以合法地欠约束，但**不接受**的是：口径对不上（抛错）或未收敛却报成功。
        """
        plate = _PKG_ROOT / "tests" / "data" / "plate.json"
        if not plate.is_file():
            self.skipTest("the sibling plate fixture has not landed yet")
        model = json.loads(plate.read_text(encoding="utf-8"))
        self.assertTrue(model["sketches"], "fixture self-check: the model declares sketches")
        for sketch in model["sketches"]:
            with self.subTest(sketch=sketch["uuid"]):
                result = constraints.solve(sketch)
                self.assertEqual(result.dof, result.variables - result.rank)
                self.assertEqual(result.redundant, result.equations - result.rank)
                self.assertEqual(len(result.residuals), result.equations)
                if result.converged:
                    self.assertLessEqual(result.residual, constraints.DEFAULT_TOL_NM)
                else:
                    self.assertGreater(result.residual, constraints.DEFAULT_TOL_NM,
                                       "not converged must mean a residual above tolerance: never fake success")

    def test_source_has_no_cjk_or_locale_literals_outside_docstrings(self) -> None:
        for path in _SOURCE_FILES:
            self.assertTrue(path.is_file(), f"fixture self-check: {path.name} must exist")
            for node in self._literal_nodes(path):
                self.assertIsNone(
                    self._CJK.search(node.value),
                    f"{path.name}:{node.lineno} user-facing literal contains CJK: {node.value!r}",
                )
                self.assertIsNone(
                    self._LOCALE.match(node.value),
                    f"{path.name}:{node.lineno} source must not hardcode a locale tag: {node.value!r}",
                )

    def test_ruler_sees_red_on_mutants(self) -> None:
        cjk_path, tag_path = self._mutant(_SKETCH_DIR / "constraints.py")
        cjk_hits = [n for n in self._literal_nodes(cjk_path) if self._CJK.search(n.value)]
        tag_hits = [n for n in self._literal_nodes(tag_path) if self._LOCALE.match(n.value)]
        self.assertEqual(len(cjk_hits), 1, "the ruler must flag an injected CJK literal (negative control)")
        self.assertEqual(len(tag_hits), 1, "the ruler must flag an injected locale tag (negative control)")

    def test_every_error_key_used_in_source_exists_in_all_language_packs(self) -> None:
        used: dict[str, str] = {}
        for path in _SOURCE_FILES:
            for node in self._literal_nodes(path):
                if node.value.startswith("err."):
                    used.setdefault(node.value, f"{path.name}:{node.lineno}")
        self.assertGreaterEqual(len(used), 5, f"fixture self-check: several error keys in use, got {sorted(used)}")
        catalogs = sorted(path.name for path in _I18N_DIR.glob("*.json") if path.name != "languages.json")
        self.assertGreaterEqual(len(catalogs), 3, f"fixture self-check: language packs on disk, got {catalogs}")
        for code_path in catalogs:
            strings = json.loads((_I18N_DIR / code_path).read_text(encoding="utf-8"))["strings"]
            for key, where in sorted(used.items()):
                self.assertIn(key, strings, f"{key} ({where}) is missing from {code_path}: t() would fall back")

    def test_error_localized_text_comes_from_the_language_pack(self) -> None:
        sk = new_sketch()
        with self.assertRaises(SketchError) as caught:
            commands.add_line(sk, p1=(0, 0), p2=(0, 0))
        error = caught.exception
        self.assertTrue(str(error).startswith("err."), f"str(exc) must stay an ASCII technical string: {str(error)!r}")
        try:
            from cadcore.i18n import catalog
        except Exception:  # pragma: no cover - sibling seat absent: skip instead of failing hard
            self.skipTest("cadcore.i18n.catalog is not importable in this tree")
        codes = [info.code for info in catalog.scan_catalogs(catalog.default_dirs()) if info.valid]
        # 语言码由扫盘数据驱动，不在源码里写死（否则这条测试自己就违反被测纪律）
        others = [code for code in codes if code != catalog.DEFAULT_LANGUAGE]
        if not others:
            self.skipTest("only the source language is installed in this tree")
        previous = catalog.active_locale()
        try:
            catalog.set_locale(catalog.DEFAULT_LANGUAGE)
            english = error.localized()
            catalog.set_locale(others[0])
            translated = error.localized()
        finally:
            catalog.set_locale(previous)
        expected = catalog.load_catalog(others[0]).strings[error.key]
        self.assertTrue(english.strip() and translated.strip())
        self.assertNotIn(error.key, english, "localized text must never show the key name to the user")
        self.assertNotEqual(english, translated, "two languages must differ: proof it really used the pack")
        self.assertTrue(translated.startswith(expected), f"expected the {others[0]} string, got {translated!r}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
