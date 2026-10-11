#!/usr/bin/env python3
"""A7 `nano` 内核的回归用例（与判定行 G2 同笔：改判据的件与自检必须一起动）。

口径要点（对应 AGENTS E2「没算与没失败不许同形」）：
- 每个断言都钉"**算出**的数"而不是"函数被调过"：投影点数、隐藏线是否存在、质量属性是否随几何变；
- **阴性对照**必须真的报红：线框要隐藏线 ⇒ 必须抛 `DependencyMissing`（不是返回空结果）；
- 缺席路径可测：未注册的内核名必须抛错并带 `reason`。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from cadcore.errors import CadError, DependencyMissing
from cadcore.kernel import base as kernel_base
from cadcore.kernel.nano import NanoKernel, make_kernel

PLATE = Path(__file__).resolve().parent / "data" / "plate.json"


def _plate() -> dict:
    with PLATE.open(encoding="utf-8") as fh:
        return json.load(fh)


class NanoKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = NanoKernel()
        self.doc = _plate()

    # -- 可用性与能力 ---------------------------------------------------------
    def test_available_and_capabilities(self) -> None:
        self.assertTrue(self.kernel.available())
        caps = self.kernel.capabilities()
        self.assertIn("project2d", caps)
        self.assertIn("extrude", caps)
        self.assertNotIn("boolean", caps)  # 没有的能力不许写进能力集

    def test_unsupported_capability_raises_with_reason(self) -> None:
        with self.assertRaises(DependencyMissing) as ctx:
            self.kernel.boolean("cut", None, None)
        self.assertEqual(ctx.exception.reason, "capability_missing")
        self.assertEqual(ctx.exception.details.get("capability"), "boolean")

    # -- 注册表 ---------------------------------------------------------------
    def test_registry_auto_follows_documented_order(self) -> None:
        """`auto` 按契约顺序 `occt → freecad → nano` 取**第一个可用**后端，不是写死 nano。

        2026-09-29 实撞：本机装上 OCP 之后这条断言（原名 `test_registry_resolves_nano_for_auto`）
        当场变红 —— 那是**测试写死了实现细节**，不是实现坏了。现在按契约顺序现算期望值，
        并单独钉住"`nano` 永远能显式取到"（"重依赖全缺席也能出图"这条承诺靠它兜底）。
        """
        kernel = kernel_base.get_kernel("auto")
        available = kernel_base.available_kernels()
        expected = next(name for name in kernel_base.KERNEL_ORDER if available.get(name))
        self.assertEqual(getattr(kernel, "name", ""), expected, kernel_base.kernel_report())
        self.assertIn(expected, ("occt", "freecad", "nano"))
        self.assertEqual(getattr(kernel_base.get_kernel("nano"), "name", ""), "nano")
        self.assertTrue(kernel_base.available_kernels().get("nano"))

    def test_unknown_kernel_raises(self) -> None:
        with self.assertRaises(DependencyMissing) as ctx:
            kernel_base.get_kernel("no-such-kernel")
        self.assertEqual(ctx.exception.reason, "kernel_not_registered")

    def test_register_and_unregister_roundtrip(self) -> None:
        kernel_base.register_kernel("probe-kernel", make_kernel)
        self.assertIn("probe-kernel", kernel_base.registered_kernels())
        self.assertTrue(kernel_base.unregister_kernel("probe-kernel"))
        self.assertNotIn("probe-kernel", kernel_base.registered_kernels())

    # -- 线框 vs 实体 ----------------------------------------------------------
    def test_wire_has_no_hidden_geometry(self) -> None:
        wire = self.kernel.sketch_to_wire(self.doc["sketches"][0])
        self.assertEqual(wire.kind, "wire")
        with self.assertRaises(DependencyMissing) as ctx:
            self.kernel.project_to_2d(wire, view="front", hidden=True)
        self.assertEqual(ctx.exception.reason, "wire_has_no_hidden_geometry")
        visible_only = self.kernel.project_to_2d(wire, view="front", hidden=False)
        self.assertGreater(visible_only.visible_curves, 0)
        self.assertEqual(visible_only.hidden_curves, 0)

    def test_empty_sketch_raises(self) -> None:
        with self.assertRaises(CadError):
            self.kernel.sketch_to_wire({"entities": []})

    def test_extrude_needs_positive_distance(self) -> None:
        wire = self.kernel.sketch_to_wire(self.doc["sketches"][0])
        with self.assertRaises(CadError):
            self.kernel.extrude(wire, distance_nm=0)
        solid = self.kernel.extrude(wire, distance_nm=6_000_000)
        self.assertEqual(solid.kind, "solid")
        self.assertEqual(solid.depth_nm, 6_000_000)

    # -- 文档级投影（G2 的 shape 来源）-----------------------------------------
    def test_document_becomes_prism_with_feature_depth(self) -> None:
        shape = self.kernel.shape_from_document(self.doc)
        self.assertEqual(shape.depth_nm, 6_000_000)
        self.assertGreater(shape.curve_count(), 8)

    def test_document_without_extrude_is_dependency_missing(self) -> None:
        doc = dict(self.doc)
        doc["features"] = []
        with self.assertRaises(DependencyMissing) as ctx:
            self.kernel.shape_from_document(doc)
        self.assertEqual(ctx.exception.reason, "no_solid_feature")

    def test_projection_has_visible_and_hidden_curves(self) -> None:
        projection = self.kernel.project_to_2d(self.doc, view="front", hidden=True)
        self.assertGreater(projection.visible_curves, 0)
        self.assertGreater(projection.hidden_curves, 0)
        for curve in projection.curves:
            self.assertIn(curve["kind"], ("visible", "hidden"))
            self.assertGreaterEqual(len(curve["points_nm"]), 2)

    def test_different_views_give_different_geometry(self) -> None:
        front = self.kernel.project_to_2d(self.doc, view="front", hidden=True)
        top = self.kernel.project_to_2d(self.doc, view="top", hidden=True)
        front_points = [tuple(pt) for curve in front.curves for pt in curve["points_nm"]]
        top_points = [tuple(pt) for curve in top.curves for pt in curve["points_nm"]]
        self.assertNotEqual(front_points, top_points)

    def test_projection_follows_geometry_not_constants(self) -> None:
        """把文档整体平移 10 mm，投影点必须跟着动（防"返回常量也能过"）。"""
        moved = json.loads(json.dumps(self.doc))
        for entity in moved["sketches"][0]["entities"]:
            geom = entity.get("geom") or {}
            for key in ("x1", "x2", "cx"):
                if key in geom:
                    geom[key] += 10_000_000
        before = self.kernel.project_to_2d(self.doc, view="top", hidden=True)
        after = self.kernel.project_to_2d(moved, view="top", hidden=True)
        self.assertNotEqual(sorted(pt for c in before.curves for pt in c["points_nm"]),
                            sorted(pt for c in after.curves for pt in c["points_nm"]))

    def test_unknown_view_raises(self) -> None:
        with self.assertRaises(CadError) as ctx:
            self.kernel.project_to_2d(self.doc, view="sideways", hidden=True)
        self.assertEqual(ctx.exception.reason, "unknown_view")

    # -- 质量属性 -------------------------------------------------------------
    def test_mass_properties_scale_with_depth(self) -> None:
        props = self.kernel.mass_properties(self.doc)
        self.assertGreater(props.area_nm2, 0)
        self.assertEqual(props.volume_nm3, int(round(props.area_nm2 * props.depth_nm)))


class UnitsErrorTests(unittest.TestCase):
    """A3/A4 的两条最小口径（错误族与整数纳米真源）。"""

    def test_nanometre_roundtrip_and_separators(self) -> None:
        from cadcore.units import format_length, mm_to_nm, parse_length

        self.assertEqual(mm_to_nm(12.5), 12_500_000)
        self.assertEqual(parse_length("12,5"), 12_500_000)
        self.assertEqual(parse_length("12.5"), 12_500_000)
        self.assertEqual(parse_length("1,234.5"), 1_234_500_000)  # 逗号当千分位
        self.assertEqual(format_length(12_500_000, unit="mm", decimals=2, decimal_separator=","), "12,50")

    def test_dependency_missing_is_a_cad_error(self) -> None:
        self.assertTrue(issubclass(DependencyMissing, CadError))
        exc = DependencyMissing("kernel absent", reason="kernel_absent", kernel="occt")
        self.assertEqual(exc.as_dict()["reason"], "kernel_absent")


if __name__ == "__main__":
    unittest.main()
