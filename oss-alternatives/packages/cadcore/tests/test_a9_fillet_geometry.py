#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""tests.test_a9_fillet_geometry -- A9 有界圆角几何算子的独立单测（对账席自持，新文件）。

测的是 cadcore/geom/fillet.py 与 cadcore/ops/sw_features.py 的 fillet **真件**：
圆角必须产生**真几何**（裁剪 + 圆弧过渡 + 真实体），半径超界必须报错而不是静默退化。

口径
----
- 长度整数纳米；几何断言用"圆心到切点距离 == 半径""切点处半径垂直于被裁直线"
  "倒圆后面积减少量 == 解析值"三条，全部由**独立计算**（不读被测模块的结论）给出。
- 夹具自证（E2.5）：先用 nano 内核把父实体量成 10x6x5mm（60mm^2 / 300mm^3），
  自证不成立就 rc=2 退出（fixture 自己也会骗人）。
"""

from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path

_PKG_ROOT = str(Path(__file__).resolve().parents[1])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError                      # noqa: E402
from cadcore.geom import fillet as fg                    # noqa: E402
from cadcore.kernel.nano import NanoKernel               # noqa: E402
from cadcore.ops import sw_features as sw                # noqa: E402
from cadcore.sketch import primitives                    # noqa: E402
from cadcore import topo as topo_mod                     # noqa: E402

MM = 1_000_000
DEPTH = 5 * MM
RECT = (10 * MM, 6 * MM)


def _rect_sketch(uuid: str = "sk-1") -> dict:
    sketch = {"uuid": uuid, "plane": "XY", "entities": [], "constraints": [], "dimensions": []}
    primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=RECT)
    return sketch


def _doc(sketch: dict | None = None, depth: int = DEPTH) -> dict:
    sk = sketch if sketch is not None else _rect_sketch()
    return {"sketches": [sk],
            "features": [{"uuid": "feat-1", "kind": "extrude", "inputs": [sk["uuid"]],
                          "params": {"distance_nm": depth, "symmetric": False, "op": "new"},
                          "suppressed": False}]}


def _corner_refs(doc: dict) -> list[str]:
    """文档里"两相邻侧面的竖直棱"（轮廓角边）引用，按角排序。"""
    body = topo_mod.known_refs(doc, kind="edge")
    loop_len = len(fg._ordered_lines(doc["sketches"][0]))
    picked, _unsupported = fg.corner_refs(list(body.refs), loop_len)
    return [ref for _, ref in picked]


def _corner_fit(edge_points, index_start: int, index_end: int):
    """三点定圆 -> (cx, cy, r)（独立于被测模块）。"""
    pts = [edge_points[index_start], edge_points[(index_start + index_end) // 2], edge_points[index_end]]
    (ax, ay), (bx, by), (cx, cy) = pts
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if d == 0:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


class FixtureSelfProofTests(unittest.TestCase):
    """夹具先自证：内核把夹具量成 10x6x5（不是"语法过了"）。"""

    def test_fixture_is_a_real_60mm2_300mm3_solid(self) -> None:
        kernel = NanoKernel()
        doc = _doc()
        shape = kernel.shape_from_document(doc)
        props = kernel.mass_properties(shape)
        self.assertEqual(props.area_nm2, 60 * MM * MM)
        self.assertEqual(props.volume_nm3, 300 * MM ** 3)
        self.assertEqual(len(_corner_refs(doc)), 4, "a 10x6 rectangle has exactly 4 corner edges")

    def test_fixture_geometry_module_sees_the_same_loop(self) -> None:
        """夹具与被测模块对"轮廓"的口径必须一致（同一条环、同一个角序）。"""
        doc = _doc()
        chain = fg._ordered_lines(doc["sketches"][0])
        loop = fg._loop_nm(chain)
        self.assertEqual(loop, ((0, 0), (10 * MM, 0), (10 * MM, 6 * MM), (0, 6 * MM)))
        refs = _corner_refs(doc)
        picked, unsupported = fg.corner_refs(refs, len(loop))
        self.assertEqual(unsupported, [])
        self.assertEqual([index for index, _ in picked], [0, 1, 2, 3])


class CornerGeometryTests(unittest.TestCase):
    """单角几何：裁剪 + 圆弧过渡，切点/圆心/半径全部独立复算。"""

    def setUp(self) -> None:
        self.doc = _doc()
        self.chain = fg._ordered_lines(self.doc["sketches"][0])

    def test_single_corner_trims_both_edges_and_is_tangent(self) -> None:
        radius = 2 * MM
        plan = fg.plan_from_entities(self.chain, [0], radius, depth_nm=DEPTH)
        self.assertEqual(plan.status, "measured")
        self.assertEqual(plan.arc_count, 1)
        corner = plan.corners[0]
        self.assertEqual(corner.corner_nm, (0, 0))
        self.assertEqual(corner.center_nm, (2 * MM, 2 * MM))
        self.assertEqual(corner.tangent_in_nm, (0, 2 * MM))
        self.assertEqual(corner.tangent_out_nm, (2 * MM, 0))
        self.assertEqual(corner.trim_in_nm, radius)
        self.assertEqual(corner.trim_out_nm, radius)
        # 相切：圆心到两个切点距离 == 半径（1 nm 取整容差）
        for point in (corner.tangent_in_nm, corner.tangent_out_nm):
            self.assertLessEqual(abs(math.dist(corner.center_nm, point) - radius), 2)
        # 相切：切点处半径方向与被裁直线方向垂直
        for point, direction in ((corner.tangent_in_nm, (0, 1)), (corner.tangent_out_nm, (1, 0))):
            vx = point[0] - corner.center_nm[0]
            vy = point[1] - corner.center_nm[1]
            norm = math.hypot(vx, vy)
            cos = (vx * direction[0] + vy * direction[1]) / norm
            self.assertLessEqual(abs(cos), 1e-4, "radius must be perpendicular to the trimmed edge")
        # 裁掉了角：两条相邻直线都不再含原角点
        for entity in plan.entities:
            if entity["kind"] == "line":
                self.assertNotEqual((entity["geom"]["x1"], entity["geom"]["y1"]), (0, 0))
                self.assertNotEqual((entity["geom"]["x2"], entity["geom"]["y2"]), (0, 0))
        self.assertGreater(plan.area_removed_nm2, 0)
        self.assertEqual(plan.area_before_nm2 - plan.area_after_nm2 > 0, True)

    def test_arc_entity_is_a_real_arc_with_the_requested_radius(self) -> None:
        plan = fg.plan_from_entities(self.chain, [1, 2], 2 * MM, depth_nm=DEPTH)
        arcs = [item for item in plan.entities if item["kind"] == "arc"]
        self.assertEqual(len(arcs), 2)
        for entity in arcs:
            self.assertAlmostEqual(float(entity["geom"]["r"]), float(2 * MM), delta=2)
            start = float(entity["geom"]["start_deg"])
            end = float(entity["geom"]["end_deg"])
            sweep = (end - start) % 360.0
            self.assertAlmostEqual(sweep, 90.0, delta=1e-6, msg="a right-angle corner sweeps 90 degrees")

    def test_plan_is_deterministic(self) -> None:
        first = fg.plan_from_entities(self.chain, [0, 1, 2, 3], 2 * MM, depth_nm=DEPTH)
        second = fg.plan_from_entities(self.chain, [0, 1, 2, 3], 2 * MM, depth_nm=DEPTH)
        self.assertEqual(first.entities, second.entities, "derived geometry must be reproducible")

    def test_analytic_removal_matches_r_squared_rule(self) -> None:
        radius = 2 * MM
        plan = fg.plan_from_entities(self.chain, [0, 1, 2, 3], radius, depth_nm=DEPTH)
        expected = 4 * int(round(radius * radius * (1.0 - math.pi / 4.0)))
        self.assertLessEqual(abs(plan.area_removed_nm2 - expected), 4 * 4,
                             "corner removal must be r^2 (cot45 - pi/4) per corner")


class BoundedRadiusTests(unittest.TestCase):
    """有界半径：超界必须报错，边界值允许（边被吃光要显式记账）。"""

    def setUp(self) -> None:
        self.doc = _doc()
        self.chain = fg._ordered_lines(self.doc["sketches"][0])

    def test_bound_is_the_small_edge_over_the_two_tangent_lengths(self) -> None:
        bound, edge = fg.corner_bound_nm(fg._loop_nm(self.chain), [0, 1, 2, 3])
        self.assertEqual(bound, 3 * MM, "6mm edge / (cot45 + cot45) = 3mm")
        self.assertIn(edge, (1, 3), "the limiting edge is one of the 6mm edges")

    def test_over_bound_radius_is_rejected_not_clamped(self) -> None:
        with self.assertRaises(CadError) as ctx:
            fg.plan_from_entities(self.chain, [0, 1, 2, 3], 3 * MM + 1, depth_nm=DEPTH)
        self.assertEqual(ctx.exception.reason, "radius_exceeds_bound")
        self.assertEqual(ctx.exception.details.get("max_radius_nm"), 3 * MM)
        self.assertEqual(ctx.exception.details.get("radius_nm"), 3 * MM + 1)

    def test_way_over_bound_radius_is_still_rejected(self) -> None:
        with self.assertRaises(CadError) as ctx:
            fg.plan_from_entities(self.chain, [0, 1, 2, 3], 50 * MM, depth_nm=DEPTH)
        self.assertEqual(ctx.exception.reason, "radius_exceeds_bound")

    def test_radius_equal_to_bound_collapses_the_short_edge_explicitly(self) -> None:
        plan = fg.plan_from_entities(self.chain, [0, 1, 2, 3], 3 * MM, depth_nm=DEPTH)
        self.assertEqual(sorted(plan.dropped_edges), [1, 3],
                         "the 6mm edges are consumed by the two neighbouring fillets")
        self.assertEqual(len([e for e in plan.entities if e["kind"] == "arc"]), 4)
        self.assertEqual(len([e for e in plan.entities if e["kind"] == "line"]), 2)
        kernel = NanoKernel()
        props = kernel.mass_properties(fg.shape_from_plan(plan))
        self.assertGreater(props.area_nm2, 0, "the collapsed profile must still be a closed loop")


class RealSolidTests(unittest.TestCase):
    """真几何：倒圆轮廓 -> 内核真实体 -> 面积/体积变化可测。"""

    def test_fillet_reduces_the_solid_volume_by_the_corner_removal(self) -> None:
        doc = _doc()
        chain = fg._ordered_lines(doc["sketches"][0])
        kernel = NanoKernel()
        before = kernel.mass_properties(kernel.shape_from_document(doc))
        plan = fg.plan_from_entities(chain, [0, 1, 2, 3], 2 * MM, depth_nm=DEPTH)
        shape = fg.shape_from_plan(plan, kernel=kernel)
        after = kernel.mass_properties(shape)
        self.assertGreater(before.area_nm2, after.area_nm2)
        measured_removed = before.area_nm2 - after.area_nm2
        self.assertLessEqual(abs(measured_removed - plan.area_removed_nm2),
                             0.01 * plan.area_removed_nm2 + 1,
                             "measured removal must match the analytic corner removal within 1%")
        self.assertEqual(after.volume_nm3, after.area_nm2 * DEPTH)

    def test_shape_really_contains_the_arc_at_the_requested_radius(self) -> None:
        doc = _doc()
        chain = fg._ordered_lines(doc["sketches"][0])
        plan = fg.plan_from_entities(chain, [0], 2 * MM, depth_nm=DEPTH)
        shape = fg.shape_from_plan(plan, kernel=NanoKernel())
        arcs = []
        for edge in shape.edges:
            if edge.role != "face-bottom":
                continue
            pts = [(int(p[0]), int(p[1])) for p in edge.points]
            if len(pts) > 4:
                closed = pts[0] == pts[-1]
                core = pts[:-1] if closed else pts
                if len(set(core)) > 3:
                    arcs.append(core)
        self.assertEqual(len(arcs), 1, "exactly one sampled arc must appear in the solid")
        fit = _corner_fit(arcs[0], 0, len(arcs[0]) - 1)
        self.assertIsNotNone(fit)
        self.assertLessEqual(abs(fit[2] - 2 * MM), 2.0, "fitted arc radius must equal the requested radius")
        self.assertLessEqual(math.dist((fit[0], fit[1]), (2 * MM, 2 * MM)), 2.0,
                             "fitted arc centre must be where the fillet centre is")

    def test_solid_from_document_uses_the_recorded_geometry(self) -> None:
        doc = _doc()
        created = sw.fillet(doc, radius_nm=2 * MM)
        record = [f for f in doc["features"] if f["uuid"] == created[0]][0]
        geometry = record["params"]["fillet_geometry"]
        self.assertEqual(geometry["status"], "measured")
        kernel = NanoKernel()
        before = kernel.mass_properties(kernel.shape_from_document(doc))
        after = kernel.mass_properties(fg.solid_from_document(doc, kernel=kernel))
        self.assertLess(after.area_nm2, before.area_nm2, "the document's fillet must cut real material")
        self.assertEqual(len(geometry["corners"]), 4)
        self.assertEqual(len(geometry["entities"]), 8)

    def test_unmeasured_fillet_does_not_fall_back_to_the_parent_solid(self) -> None:
        doc = _doc()
        doc["features"].append({"uuid": "f-2", "kind": "fillet", "inputs": [],
                                "params": {"radius_nm": 1 * MM}, "suppressed": False})
        with self.assertRaises(CadError) as ctx:
            fg.solid_from_document(doc)
        self.assertEqual(ctx.exception.reason, "fillet_geometry_unmeasured")


class FamilyWiringTests(unittest.TestCase):
    """族模块接线：fillet 记录里带的是真几何，不是"只有参数"。"""

    def test_family_records_measured_geometry_and_keeps_persistent_refs(self) -> None:
        doc = _doc()
        created = sw.fillet(doc, radius_nm=2 * MM)
        record = [f for f in doc["features"] if f["uuid"] == created[0]][0]
        params = record["params"]
        geometry = params["fillet_geometry"]
        self.assertEqual(geometry["status"], "measured")
        self.assertEqual(geometry["radius_nm"], 2 * MM)
        self.assertEqual(geometry["max_radius_nm"], 3 * MM)
        self.assertEqual(geometry["volume_removed_nm3"],
                         geometry["area_removed_nm2"] * DEPTH)
        self.assertTrue(params["edges"], "the record must keep persistent topology refs")
        for ref in params["edges"]:
            self.assertTrue(topo_mod.is_kind_ref(ref, "edge"), ref)
        self.assertEqual(len(params["edges"]), 4, "only corner edges are filleted")

    def test_family_rejects_over_bound_radius(self) -> None:
        doc = _doc()
        with self.assertRaises(CadError) as ctx:
            sw.fillet(doc, radius_nm=4 * MM)
        self.assertEqual(ctx.exception.reason, "radius_exceeds_bound")
        self.assertEqual(ctx.exception.details.get("max_radius_nm"), 3 * MM)

    def test_family_keeps_the_legacy_profile_guard_reason(self) -> None:
        doc = _doc()
        with self.assertRaises(CadError) as ctx:
            sw.fillet(doc, radius_nm=7 * MM)
        self.assertEqual(ctx.exception.reason, "dimension_exceeds_profile")

    def test_explicit_non_corner_edge_is_rejected(self) -> None:
        doc = _doc()
        body = topo_mod.known_refs(doc, kind="edge")
        loop_len = len(fg._ordered_lines(doc["sketches"][0]))
        cap = [ref for ref in body.refs if fg.corner_index_of_ref(ref, loop_len) is None]
        self.assertTrue(cap, "the fixture must expose cap edges")
        with self.assertRaises(CadError) as ctx:
            sw.fillet(doc, radius_nm=1 * MM, edges=[cap[0]])
        self.assertEqual(ctx.exception.reason, "unsupported_edge_kind")

    def test_fillet_still_refuses_a_sketch_uuid_as_an_edge(self) -> None:
        doc = _doc()
        with self.assertRaises(CadError) as ctx:
            sw.fillet(doc, radius_nm=1 * MM, edges=[doc["sketches"][0]["uuid"]])
        self.assertEqual(ctx.exception.reason, "not_a_topo_ref")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(verbosity=2).result.wasSuccessful() else 1)
