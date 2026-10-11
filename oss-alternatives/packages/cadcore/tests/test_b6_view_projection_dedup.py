"""tests.test_b6_view_projection_dedup —— B6 三视图面缺陷的回归尺子（RC-V1）。

缺陷形状：真消隐（OCCT HLR）把同一条棱从 `VCompound` / `OutLineVCompound` /
`Rg1LineVCompound` 各吐一份，投影结果里出现**逐点完全相同**的曲线；它一路进视图、
进图纸实体、进 DXF —— 用户看到"同一条线画了两遍"，按实体去重统计的下游还会算错数。

钉子（每条都配阴性/变异对照，防止尺子自己假绿）
----------------------------------------------
1. **投影出参无重影**：真实 box / hole 形体三视图，每个视图里不许有 `(kind, 点串)` 全同的曲线；
2. **重影真的被合并**：合并后的条数 < 合并前（否则"没有重复"可能只是因为内核本来就不重复）；
3. **不许顺手丢几何**：去重是**只去完全相同的项** —— 不重复的输入经 `dedupe_curves` 后
   逐字节不变（变异体：造一条共线但端点不同的线，它必须留下）；
4. **两个内核同一把尺子**：`nano` 与 `occt` 都走 `kernel.base.dedupe_curves`；
5. **产物面**：图纸实体流里 VISIBLE/HIDDEN 图层不许有逐字段相同的几何实体（DXF 同源）。

夹具不依赖 OCCT：需要真内核的断言在 `occt` 缺席时 `skipTest`（不假装通过），
纯逻辑判据一律用真件（`dedupe_curves` / `nano` 内核）跑。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.kernel import base as kbase  # noqa: E402
from cadcore.kernel import nano as nano_kernel  # noqa: E402

MM = 1_000_000


def _curve(kind: str, points) -> dict:
    return {"kind": kind, "points_nm": [list(point) for point in points]}


class DedupeRulerTests(unittest.TestCase):
    """纯逻辑：尺子本身的三条判据（真件 = `kernel.base.dedupe_curves`）。"""

    def test_exact_duplicates_are_merged_keeping_first(self) -> None:
        first = _curve("visible", [(0, 0), (20_000_000, 0)])
        other = _curve("visible", [(0, 0), (0, 60_000_000)])
        out = kbase.dedupe_curves([first, other, dict(first)])
        self.assertEqual(len(out), 2, "逐点完全相同的曲线只留第一份")
        self.assertEqual(out[0], first)
        self.assertEqual(out[1], other)

    def test_same_points_different_kind_both_kept(self) -> None:
        """可见线与隐藏线在同一位置**不是**重影：kind 不同 ⇒ 两条都要留。"""
        visible = _curve("visible", [(0, 0), (20_000_000, 0)])
        hidden = _curve("hidden", [(0, 0), (20_000_000, 0)])
        self.assertEqual(len(kbase.dedupe_curves([visible, hidden])), 2)

    def test_unique_input_is_returned_untouched(self) -> None:
        rows = [_curve("visible", [(0, 0), (10, 0)]),
                _curve("visible", [(10, 0), (10, 10)]),
                _curve("hidden", [(0, 10), (10, 10)])]
        out = kbase.dedupe_curves(rows)
        self.assertEqual(out, rows, "去重不许顺手化简/丢几何")

    def test_collinear_but_distinct_is_not_deduped(self) -> None:
        """变异体反证：共线但端点不同 ⇒ 必须留下（防"顺手做了几何化简"）。"""
        long_run = _curve("visible", [(0, 0), (20_000_000, 0)])
        short_run = _curve("visible", [(0, 0), (10_000_000, 0)])
        self.assertEqual(len(kbase.dedupe_curves([long_run, short_run])), 2)

    def test_unreadable_item_is_kept(self) -> None:
        """读不出点串的项一律保留：宁可多留，也不许静默丢几何。"""
        rows = [{"kind": "custom", "note": "no points"}, _curve("visible", [(0, 0), (1, 1)])]
        self.assertEqual(len(kbase.dedupe_curves(rows)), 2)

    def test_ruler_sees_red_on_a_duplicate_free_mutation(self) -> None:
        """尺子看得见红：对空输入与单元素输入不许造出重复，也不许报错。"""
        self.assertEqual(kbase.dedupe_curves([]), [])
        one = _curve("visible", [(0, 0), (5, 5)])
        self.assertEqual(kbase.dedupe_curves([one]), [one])


class NanoProjectionDedupTests(unittest.TestCase):
    """真件（纯标准库内核）：重复边投影后不许出现重影线。"""

    def _cube_edges(self):
        nano = nano_kernel
        corners = [(x, y, z) for x in (0, 10) for y in (0, 20) for z in (0, 30)]
        pairs = []
        for i, a in enumerate(corners):
            for b in corners[i + 1:]:
                if sum(1 for k in range(3) if a[k] != b[k]) == 1:
                    pairs.append((a, b))
        edges = []
        for a, b in pairs:
            for _ in range(2):  # 每条棱**故意加两遍**（模拟上游重复边）
                edges.append(nano.Edge3(points=(a, b)))
        shape = nano.Shape(name="fixture", kind="solid", edges=tuple(edges))
        return shape, len(pairs)

    def test_nano_projection_has_no_duplicate_curves(self) -> None:
        shape, unique_edges = self._cube_edges()
        kernel = nano_kernel.make_kernel()
        projection = kernel.project_to_2d(shape, view="front", hidden=False)
        rows = [(str(item.get("kind")), tuple(tuple(int(v) for v in p) for p in item["points_nm"]))
                for item in projection.curves]
        self.assertEqual(len(rows), len(set(rows)), "投影出参里不许有逐点重复的曲线")
        self.assertLessEqual(len(rows), unique_edges)
        self.assertTrue(any("duplicate curves merged" in note for note in projection.notes),
                        "合并发生了就必须留痕：%r" % (projection.notes,))


class OcctProjectionDedupTests(unittest.TestCase):
    """真件（OCCT 内核，缺席即 skip）：探针 V07/V08/V21 的同一形体。"""

    def _fixture(self, hole: bool):
        from cadcore import docmodel
        from cadcore.kernel import base
        from cadcore.ops import sw_drawings as draw

        kernel = base.get_kernel("auto")
        if kernel.name != "occt":
            self.skipTest("occt kernel unavailable (got %r)" % kernel.name)
        payload = json.loads(docmodel.document_to_l1(_shape_doc(hole=hole)))
        shape = draw._shape_for(payload, kernel, None)[0]
        return kernel, shape, payload

    def _check(self, hole: bool) -> None:
        kernel, shape, _payload = self._fixture(hole)
        for view in ("front", "top", "right"):
            projection = kernel.project_to_2d(shape, view=view, hidden=True)
            rows = [(str(item.get("kind")),
                     tuple(tuple(int(v) for v in p) for p in item["points_nm"]))
                    for item in projection.curves]
            self.assertEqual(len(rows), len(set(rows)),
                             "%s 视图投影里有 %d 组逐点重复曲线" % (view, len(rows) - len(set(rows))))

    def test_box_views_have_no_duplicate_curves(self) -> None:
        self._check(hole=False)

    def test_hole_views_have_no_duplicate_curves(self) -> None:
        self._check(hole=True)

    def test_sheet_entities_have_no_duplicate_view_geometry(self) -> None:
        """产物面（探针 V21 的同一判据）：图纸实体里 VISIBLE/HIDDEN 图层不许有全同实体。"""
        from cadcore.draw import sheet as dsheet
        from cadcore.ops import sw_drawings as draw

        kernel, _shape, payload = self._fixture(hole=True)
        draw.compose_sheet(payload, kernel=kernel, standard="gbt")
        sheet = draw._sheet_objects(payload)[-1]
        counts: dict = {}
        samples: dict = {}
        for entity in dsheet.iter_entities(sheet):
            if str(entity.get("type")) not in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE"):
                continue
            key = json.dumps({k: v for k, v in entity.items() if k not in ("uuid", "origin")},
                             sort_keys=True, default=str)
            counts[key] = counts.get(key, 0) + 1
            samples.setdefault(key, entity)
        dupes = [key for key, n in counts.items()
                 if n > 1 and (samples.get(key) or {}).get("layer") in ("VISIBLE", "HIDDEN")]
        self.assertEqual(dupes, [], "视图图层里有 %d 组重复实体" % len(dupes))


def _shape_doc(*, hole: bool):
    """探针 `views_fixture` 的同一夹具：60×40 拉伸 20mm，可选 φ16 通孔。"""
    from cadcore import docmodel
    from cadcore.sketch import commands as sketch_commands
    from cadcore.sketch import primitives

    doc = docmodel.new_document()
    sketch: dict = {"uuid": "22222222-2222-2222-2222-222222222222", "plane": "XY",
                    "entities": [], "constraints": [], "dimensions": []}
    primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=(60 * MM, 40 * MM))
    if hole:
        sketch_commands.add_circle(sketch, center=(30 * MM, 20 * MM), radius_nm=8 * MM)
    doc.sketches = [sketch]
    doc.features = [{"uuid": "33333333-3333-3333-3333-333333333333", "kind": "extrude",
                     "inputs": [sketch["uuid"]],
                     "params": {"distance_nm": 20 * MM, "symmetric": False, "op": "new"},
                     "suppressed": False}]
    return doc


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
