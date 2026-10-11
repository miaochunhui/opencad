#!/usr/bin/env python3
"""OCCT 后端（`kernel/occt.py`）的回归用例 —— **两条路径都要测**。

口径（与本包其它测试一致）：
- **缺席路径必须可测**：本机没装 OCP 时，`available()=False`、`capabilities()` 是空集、
  任何几何调用抛 `DependencyMissing`（**不是** ImportError、不是静默返回空）；
- **在位路径必须真算**：装了 OCP 才跑的那几条用 `skipUnless`，跳过时如实打印 `skipped`，
  **不许用"跳过了"冒充"通过了"**（判定行里 `skipped` 是可见的）；
- 数值断言钉"算出来的几何"：包围盒随草图平移而变、体积随孔数变、HLR 投影同时有可见与隐藏线。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from cadcore.errors import CadError, DependencyMissing
from cadcore.kernel import base as kernel_base
from cadcore.kernel import occt as occt_mod

PLATE = Path(__file__).resolve().parent / "data" / "plate.json"


def _plate() -> dict:
    with PLATE.open(encoding="utf-8") as fh:
        return json.load(fh)


class OcctAbsenceTests(unittest.TestCase):
    """缺席路径：**无论本机装没装 OCP 都必须成立**（装了就跳过，跳过是可见的）。"""

    def test_ocp_availability_is_a_bool(self) -> None:
        self.assertIsInstance(occt_mod.ocp_available(), bool)

    @unittest.skipIf(occt_mod.ocp_available(), "OCP 已安装：缺席路径不适用（skipped 可见）")
    def test_absent_backend_reports_unavailable_and_raises(self) -> None:
        kernel = occt_mod.OcctKernel()
        self.assertFalse(kernel.available())
        self.assertEqual(kernel.capabilities(), frozenset())
        with self.assertRaises(DependencyMissing) as ctx:
            kernel.sketch_to_wire(_plate()["sketches"][0])
        self.assertEqual(ctx.exception.reason, "dependency_missing")

    def test_registry_never_picks_an_unavailable_kernel(self) -> None:
        """`auto` 必须落在一个真的可用后肩上：装没装 OCP 都不许返回不可用内核。"""
        kernel = kernel_base.get_kernel("auto")
        self.assertTrue(bool(kernel.available()))
        self.assertIn(getattr(kernel, "name", ""), ("nano", "occt", "freecad"))
        self.assertTrue(kernel_base.available_kernels().get("nano"))


@unittest.skipUnless(occt_mod.ocp_available(), "OCP 未安装：在位路径跳过（skipped 可见，不当通过）")
class OcctPresenceTests(unittest.TestCase):
    """在位路径：真 B-Rep 几何。"""

    def setUp(self) -> None:
        self.kernel = occt_mod.OcctKernel()
        self.doc = _plate()

    def test_capabilities_are_concrete(self) -> None:
        caps = self.kernel.capabilities()
        for name in ("sketch", "extrude", "project2d", "mass", "step"):
            self.assertIn(name, caps)
        self.assertNotIn("revolve", caps)          # 没实现的不许写进能力集

    def test_document_builds_a_solid_with_holes(self) -> None:
        shape = self.kernel.shape_from_document(self.doc)
        self.assertEqual(shape.kind, "solid")
        self.assertEqual(shape.depth_nm, 6_000_000)
        self.assertEqual(shape.meta.get("holes"), 4)          # plate.json 四个圆孔
        self.assertGreater(shape.profile_area_nm2, 0)

    def test_mass_properties_separate_profile_and_brep(self) -> None:
        props = self.kernel.mass_properties(self.doc)
        self.assertGreater(props.profile_area_nm2, 0)
        self.assertGreater(props.area_nm2, 0)
        self.assertGreater(props.volume_nm3, 0)
        # 挖了 4 个通孔 ⇒ B-Rep 体积必须**小于**"外轮廓面积 × 厚度"
        self.assertLess(props.volume_nm3, props.profile_area_nm2 * props.depth_nm)
        self.assertGreaterEqual(props.solids, 1)
        self.assertIsNotNone(props.bbox_nm)

    def test_bbox_follows_geometry_not_constants(self) -> None:
        moved = json.loads(json.dumps(self.doc))
        for entity in moved["sketches"][0]["entities"]:
            geom = entity.get("geom") or {}
            for key in ("x1", "x2", "cx"):
                if key in geom:
                    geom[key] += 10_000_000
        base_box = self.kernel.mass_properties(self.doc).bbox_nm
        moved_box = self.kernel.mass_properties(moved).bbox_nm
        self.assertNotEqual(base_box, moved_box)

    def test_hlr_projection_has_visible_and_hidden(self) -> None:
        projection = self.kernel.project_to_2d(self.doc, view="top", hidden=True)
        self.assertGreater(projection.visible_curves, 0)
        self.assertGreater(projection.hidden_curves, 0)
        for curve in projection.curves:
            self.assertIn(curve["kind"], ("visible", "hidden"))
            self.assertGreaterEqual(len(curve["points_nm"]), 2)

    def test_wire_without_depth_cannot_give_hidden_lines(self) -> None:
        wire = self.kernel.sketch_to_wire(self.doc["sketches"][0])
        self.assertEqual(wire.kind, "wire")
        with self.assertRaises(DependencyMissing) as ctx:
            self.kernel.project_to_2d(wire, view="front", hidden=True)
        self.assertEqual(ctx.exception.reason, "wire_has_no_hidden_geometry")

    def test_general_boolean_is_honestly_missing(self) -> None:
        with self.assertRaises(DependencyMissing) as ctx:
            self.kernel.boolean("union", None, None)
        self.assertEqual(ctx.exception.capability if hasattr(ctx.exception, "capability") else
                         ctx.exception.details.get("capability"), "boolean:union")

    def test_step_export_writes_a_file(self) -> None:
        import tempfile

        shape = self.kernel.shape_from_document(self.doc)
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "plate.step")
            self.kernel.export(shape, fmt="step", path=out)
            path = Path(out)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 512)

    def test_unknown_view_raises(self) -> None:
        with self.assertRaises(CadError) as ctx:
            self.kernel.project_to_2d(self.doc, view="sideways", hidden=True)
        self.assertEqual(ctx.exception.reason, "unknown_view")


if __name__ == "__main__":
    unittest.main()
