"""tests.test_draw_ops_parity_new —— 族 `draw_ops` parity 门新绑 8 条后果探针的回归尺子。

为什么要这个文件
----------------
门 `oss-alternatives/tools/cad/parity_draw_ops_gate.py` 对本族 8 条 must_have
（构造线与射线 / 点与点样式 / 图案填充与渐变色 / 边界创建 / 面域 / 圆环 / 对象捕捉追踪与临时追踪点 /
当前图层与对象特性）此前只有**命名级证据**（记 `partial`：op 存在且可调用，门没绑定后果探针）。
本波给每条绑了真探针，本文件是**实现侧**的回归尺子：把每条探针赖以成立的真实后果逐条钉死
（落库几何 + 文档模型字段 + DXF 落库），门坏了或实现退化了两边都能看见红。

尺子怎么保证看得见红（不删断言换绿）
------------------------------------
1. **每条都有证伪腿**：锁定/未锁定、两个不同面积的边界、两个不同尺寸的环、坏 ref 不许半写 ——
   断言不是恒真式（例：OTRACK 必须能区分"锁到路径上"与"原样落点"；填充换间距必须换条数）；
2. **落库后取量**：所有断言打在 uuid → 实体上，不只看返回值；
3. **DXF 真写一遍**：`sketch_to_dxf` 支持的四类（line/circle/arc/polyline）真写 + 真解析回坐标；
   不支持的 kind 用 `DocumentError(reason="unsupported_sketch_entity_kind")` **钉住缺口** ——
   缺口是当次实测的，不是推断的；哪天桥支持了，这条会红，提醒改口径；
4. **真件不替身**：`cadcore/ops/draw_ops.py` 不在盘上 / import 失败 ⇒ 当场红。

口径
----
- 长度一律**整数纳米**（nm）；DXF 里是**文档单位 mm**，断言处显式除 `MM`；
- 错误比 `CadError.reason`（族接口冻结口径）；比不了的断"抛错 ∧ 草图一字未动"，不静默放宽。
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import unittest

from cadcore.errors import CadError, DocumentError
from cadcore.io.dxf_bridge import sketch_to_dxf
from cadcore.sketch import constraints as cons

#: 1 mm = 1e6 nm（与 cadcore 冻结口径一致）。
MM = 1_000_000

#: 真件模块名（不在盘上就当场红，绝不回落替身）。
REAL_MODULE = "cadcore.ops.draw_ops"

#: 本波 parity 门新绑后果探针的 8 个操作名（与门里 `_PROBE_BINDINGS`/`_EXTRA_CAPS` 同名）。
PARITY_NEW_OPS = ("xline_create", "point_create", "hatch_create", "boundary", "region", "donut",
                  "otrack", "layer_apply")


def _real_ops():
    """import 真件；缺席/失败都当场红（"没有实现"不许伪装成"没测"）。"""
    if importlib.util.find_spec(REAL_MODULE) is None:
        raise AssertionError("family module is not on disk: %s" % REAL_MODULE)
    return importlib.import_module(REAL_MODULE)


ops = _real_ops()


# --------------------------------------------------------------------------------------
# 量取助手（测试侧独立实现：映射 / 对象双兼容，不借真件的私有 helper）
# --------------------------------------------------------------------------------------


def _fresh():
    """空的 L1 草图（三张表齐全）。"""
    return {"uuid": "parity-new-sketch", "entities": [], "constraints": [], "dimensions": []}


def _entities(sk):
    return list(cons.sketch_entities(sk))


def _by_uuid(sk, uid):
    for entity in _entities(sk):
        if entity.get("uuid") == uid:
            return entity
    return None


def _geom(sk, uid):
    entity = _by_uuid(sk, uid)
    if entity is None:
        raise AssertionError("uuid %r not landed in sketch" % (uid,))
    return entity["geom"]


def _snapshot(sk):
    """实体表快照（uuid + geom 的 repr）——证明失败时一字未动。"""
    return [(entity.get("uuid"), repr(entity.get("geom"))) for entity in _entities(sk)]


def _dxf_records(data):
    """DXF 字节 → ENTITIES 段里的记录 `(type, {组码: [值...]})`。

    自己解析（不借 `packages/dxf` 的内部结构）：只认"组码行 + 值行"两行一对，只取 ENTITIES 段。
    """
    lines = [line.strip() for line in data.decode("utf-8").splitlines()]
    records = []
    section = ""
    current_type = None
    current = {}
    index = 0
    while index + 1 < len(lines):
        code, value = lines[index], lines[index + 1]
        index += 2
        if code == "0":
            if value == "SECTION":
                if index + 1 < len(lines) and lines[index] == "2":
                    section = lines[index + 1]
                    index += 2
                continue
            if value == "ENDSEC":
                if current_type is not None and section == "ENTITIES":
                    records.append((current_type, current))
                current_type, current, section = None, {}, ""
                continue
            if section == "ENTITIES":
                if current_type is not None:
                    records.append((current_type, current))
                current_type, current = value, {}
            continue
        if section == "ENTITIES" and current_type is not None:
            current.setdefault(code, []).append(value)
    if current_type is not None and section == "ENTITIES":
        records.append((current_type, current))
    return records


def _dxf_layer(record):
    return record[1].get("8", [""])[0]


def _dxf_vertices(record):
    xs = [float(value) for value in record[1].get("10", [])]
    ys = [float(value) for value in record[1].get("20", [])]
    return list(zip(xs, ys))


def _dxf_line_points(record):
    """LINE 的两个端点：起点 (10,20) 与终点 (11,21)。"""
    return [(float(record[1]["10"][0]), float(record[1]["20"][0])),
            (float(record[1]["11"][0]), float(record[1]["21"][0]))]


class ParityNewOpsCase(unittest.TestCase):
    """共用断言：`CadError.reason` 精确命中；失败时草图一字未动。"""

    def assert_cad_error(self, reason, callable_, *args, **kwargs):
        with self.assertRaises(CadError) as ctx:
            callable_(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason,
                         "reason mismatch: got %r want %r" % (ctx.exception.reason, reason))
        return ctx.exception

    def assert_cad_error_frozen(self, sk, callable_, *args, **kwargs):
        before = _snapshot(sk)
        with self.assertRaises(CadError) as ctx:
            callable_(*args, **kwargs)
        self.assertIsInstance(ctx.exception.reason, str)
        self.assertTrue(ctx.exception.reason)
        self.assertEqual(_snapshot(sk), before, "failed call must not touch the sketch")
        return ctx.exception


class TestParityOperands(ParityNewOpsCase):
    def test_probe_operands_exist_in_frozen_operations(self):
        """8 条新探针的操作名必须在冻结 `OPERATIONS` 里且可调用（门绑的就是这些键）。"""
        for name in PARITY_NEW_OPS:
            self.assertIn(name, ops.OPERATIONS, "%s missing from OPERATIONS" % name)
            self.assertTrue(callable(ops.OPERATIONS[name]), "%s not callable" % name)


class TestXlineProbe(ParityNewOpsCase):
    #: 探针入参（与门 `_PROBE_BINDINGS['xline']` 同源）。
    ORIGIN = (3 * MM, 4 * MM)
    ANGLE_DEG = 30.0
    RAY_NM = 1_000_000_000

    def test_xline_is_infinite_with_exact_origin_and_direction(self):
        sk = _fresh()
        (ref,) = ops.xline_create(sk, origin_nm=self.ORIGIN, angle_deg=self.ANGLE_DEG)
        entity, geom = _by_uuid(sk, ref), _geom(sk, ref)
        self.assertEqual(entity["kind"], "xline")
        self.assertIs(entity["construction"], True, "构造线必须带 construction 位")
        self.assertEqual((geom["ox"], geom["oy"]), self.ORIGIN)
        self.assertIs(geom["infinite"], True, "构造线没有几何终点，不许偷偷截成有限段")
        self.assertEqual(geom["ray_len_nm"], self.RAY_NM)
        self.assertAlmostEqual(geom["angle_deg"], self.ANGLE_DEG, places=9)
        length = math.hypot(geom["dx_nm"], geom["dy_nm"])
        self.assertLessEqual(abs(length - self.RAY_NM), 2.0, "方向必须归一化到载体长度")
        theta = math.radians(self.ANGLE_DEG)
        self.assertAlmostEqual(geom["dx_nm"] / length, math.cos(theta), places=6)
        self.assertAlmostEqual(geom["dy_nm"] / length, math.sin(theta), places=6)
        for key in ("x1", "y1", "x2", "y2"):
            self.assertNotIn(key, geom, "构造线不许以有限段字段冒充")

    def test_xline_rejections_leave_sketch_untouched(self):
        sk = _fresh()
        (ref,) = ops.xline_create(sk, origin_nm=(0, 0), angle_deg=0.0)
        self.assert_cad_error_frozen(sk, ops.xline_create, sk, origin_nm=(0, 0), direction_nm=(0, 0))
        self.assert_cad_error_frozen(sk, ops.xline_create, sk, origin_nm=(0, 0), origin=(0, 0),
                                     angle_deg=0.0)
        self.assert_cad_error_frozen(sk, ops.xline_create, sk, origin_nm=(0, 0))
        self.assertEqual([e["uuid"] for e in _entities(sk)], [ref], "失败的调用不许留下几何")


class TestPointProbe(ParityNewOpsCase):
    AT = (12 * MM, -3 * MM)
    PDMODE = 35
    PDSIZE_NM = 2 * MM

    def test_point_lands_zero_extent_position_and_point_style(self):
        sk = _fresh()
        (ref,) = ops.point_create(sk, point_nm=self.AT, pdmode=self.PDMODE, pdsize_nm=self.PDSIZE_NM)
        entity, geom = _by_uuid(sk, ref), _geom(sk, ref)
        self.assertEqual(entity["kind"], "point")
        self.assertEqual((geom["x"], geom["y"]), self.AT)
        self.assertEqual(geom["pdmode"], self.PDMODE)
        self.assertEqual(geom["pdsize_nm"], self.PDSIZE_NM)

    def test_point_style_out_of_range_and_bad_length_are_refused(self):
        sk = _fresh()
        (ref,) = ops.point_create(sk, point_nm=(0, 0))
        self.assert_cad_error("invalid_argument", ops.point_create, sk, point_nm=(0, 0), pdmode=3000)
        self.assert_cad_error("invalid_length", ops.point_create, sk, point_nm=(0, 0), pdsize_nm=1.5)
        self.assertEqual([e["uuid"] for e in _entities(sk)], [ref])


class TestHatchProbe(ParityNewOpsCase):
    RING_30x20 = [(0, 0), (30 * MM, 0), (30 * MM, 20 * MM), (0, 20 * MM)]
    RING_40x10 = [(0, 0), (40 * MM, 0), (40 * MM, 10 * MM), (0, 10 * MM)]

    def test_hatch_scanlines_are_real_and_area_is_computed(self):
        sk = _fresh()
        (ref,) = ops.hatch_create(sk, boundary_nm=self.RING_30x20, pitch_nm=2 * MM, angle_deg=0.0)
        geom = _geom(sk, ref)
        self.assertEqual(_by_uuid(sk, ref)["kind"], "hatch")
        self.assertEqual(geom["area_nm2"], 30 * MM * 20 * MM, "面积必须是鞋带现算，不是入参回声")
        lines = geom["lines"]
        self.assertEqual(geom["line_count"], len(lines))
        expected = (20 * MM - 1) // (2 * MM)
        self.assertEqual(len(lines), expected, "0° 扫描线数 = 严格落在 (0,20mm) 内的高度/间距")
        ys = []
        for x1, y1, x2, y2 in lines:
            self.assertEqual(y1, y2, "0° 剖面线必须水平")
            self.assertEqual((x1, x2), (0, 30 * MM), "线段端点必须贴到边界")
            self.assertTrue(0 < y1 < 20 * MM, "线段不许落在边界外")
            ys.append(y1)
        ys.sort()
        self.assertEqual({b - a for a, b in zip(ys, ys[1:])}, {2 * MM}, "剖面线间距必须等于 pitch")

    def test_hatch_pitch_and_boundary_really_drive_the_scanlines(self):
        """换边界 + 换间距必须换出不同后果（证明不是桩值/常量）。"""
        sk = _fresh()
        (ref,) = ops.hatch_create(sk, boundary_nm=self.RING_40x10, pitch_nm=5 * MM, angle_deg=90.0)
        geom = _geom(sk, ref)
        self.assertEqual(geom["area_nm2"], 40 * MM * 10 * MM)
        xs = []
        for x1, y1, x2, y2 in geom["lines"]:
            self.assertEqual(x1, x2, "90° 剖面线必须竖直")
            self.assertEqual((y1, y2), (0, 10 * MM))
            self.assertTrue(0 <= x1 <= 40 * MM)
            xs.append(x1)
        xs.sort()
        self.assertEqual({b - a for a, b in zip(xs, xs[1:])}, {5 * MM}, "竖直剖面线间距必须等于 pitch")
        self.assertGreaterEqual(len(xs), 7)

    def test_hatch_requires_exactly_one_boundary_source(self):
        sk = _fresh()
        self.assert_cad_error_frozen(sk, ops.hatch_create, sk, pitch_nm=2 * MM)
        self.assert_cad_error_frozen(sk, ops.hatch_create, sk, boundary_nm=self.RING_30x20,
                                     refs=["x"], pitch_nm=2 * MM)
        self.assertEqual(_entities(sk), [])


class TestBoundaryProbe(ParityNewOpsCase):
    OUTER = [(0, 0), (30 * MM, 0), (30 * MM, 20 * MM), (0, 20 * MM)]
    INNER = [(5 * MM, 5 * MM), (25 * MM, 5 * MM), (25 * MM, 15 * MM), (5 * MM, 15 * MM)]
    PICK = (10 * MM, 10 * MM)

    def test_boundary_returns_smallest_ring_as_a_new_entity(self):
        sk = _fresh()
        (outer,) = ops.polyline_create(sk, points_nm=self.OUTER, closed=True, layer="0")
        (inner,) = ops.polyline_create(sk, points_nm=self.INNER, closed=True, layer="0")
        outer_geom_before = repr(_geom(sk, outer))
        (ref,) = ops.boundary(sk, point_nm=self.PICK, layer="BND")
        self.assertNotIn(ref, (outer, inner), "边界必须是**新建**对象，不许回显既有环")
        self.assertEqual(len(_entities(sk)), 3, "既有图元一个不动，只多一个边界")
        entity = _by_uuid(sk, ref)
        self.assertEqual(entity["kind"], "polyline")
        self.assertEqual(entity["layer"], "BND")
        points = [tuple(pair) for pair in entity["geom"]["points"]]
        self.assertEqual(points, self.INNER + [self.INNER[0]], "拾取点在两个嵌套环内 ⇒ 取面积最小的环")
        self.assertEqual(repr(_geom(sk, outer)), outer_geom_before, "既有环不许被改")

    def test_boundary_outside_every_ring_is_not_found_and_frozen(self):
        sk = _fresh()
        ops.polyline_create(sk, points_nm=self.OUTER, closed=True, layer="0")
        before = _snapshot(sk)
        self.assert_cad_error("draw_ops_boundary_not_found", ops.boundary, sk, point_nm=(100 * MM, 0))
        self.assertEqual(_snapshot(sk), before)


class TestRegionProbe(ParityNewOpsCase):
    RING_A = [(0, 0), (30 * MM, 0), (30 * MM, 20 * MM), (0, 20 * MM)]
    RING_B = [(0, 0), (40 * MM, 0), (40 * MM, 20 * MM), (0, 20 * MM)]

    def _region_of(self, ring):
        sk = _fresh()
        (ring_ref,) = ops.polyline_create(sk, points_nm=ring, closed=True, layer="0")
        (ref,) = ops.region(sk, boundary_refs=[ring_ref], layer="REG")
        return sk, ref

    def test_region_area_and_perimeter_are_computed_from_the_ring(self):
        sk_a, ref_a = self._region_of(self.RING_A)
        geom_a = _geom(sk_a, ref_a)
        self.assertEqual(_by_uuid(sk_a, ref_a)["kind"], "region")
        self.assertEqual(geom_a["area_nm2"], 30 * MM * 20 * MM)
        self.assertEqual(geom_a["perimeter_nm"], 2 * (30 * MM + 20 * MM))
        self.assertIs(geom_a["closed"], True)
        self.assertEqual([tuple(pair) for pair in geom_a["boundary"]], self.RING_A)
        sk_b, ref_b = self._region_of(self.RING_B)
        geom_b = _geom(sk_b, ref_b)
        self.assertEqual(geom_b["area_nm2"], 40 * MM * 20 * MM, "换环必须换面积（现算，不是常量）")
        self.assertEqual(geom_b["perimeter_nm"], 2 * (40 * MM + 20 * MM))

    def test_region_refuses_self_intersecting_ring_and_bad_refs(self):
        bowtie = [(0, 0), (10 * MM, 0), (0, 10 * MM), (10 * MM, 10 * MM)]
        sk = _fresh()
        (bow,) = ops.polyline_create(sk, points_nm=bowtie, closed=True, layer="0")
        before = _snapshot(sk)
        self.assert_cad_error("draw_ops_self_intersecting", ops.region, sk, boundary_refs=[bow])
        self.assertEqual(_snapshot(sk), before)
        self.assert_cad_error("unknown_reference", ops.region, sk, boundary_refs=["nope-uuid"])
        self.assert_cad_error("empty_selection", ops.region, sk, boundary_refs=[])
        self.assertEqual(_snapshot(sk), before)


class TestDonutProbe(ParityNewOpsCase):
    CENTER = (10 * MM, 10 * MM)

    def test_donut_lands_two_concentric_circles_outer_then_inner(self):
        sk = _fresh()
        refs = ops.donut(sk, center_nm=self.CENTER, outer_diameter_nm=20 * MM,
                         inner_diameter_nm=10 * MM, layer="DON")
        self.assertEqual(len(refs), 2)
        outer, inner = _by_uuid(sk, refs[0]), _by_uuid(sk, refs[1])
        self.assertEqual((outer["kind"], inner["kind"]), ("circle", "circle"))
        self.assertEqual((outer["geom"]["r"], inner["geom"]["r"]), (10 * MM, 5 * MM),
                         "返回序 = [外圆, 内圆]")
        for entity in (outer, inner):
            self.assertEqual((entity["geom"]["cx"], entity["geom"]["cy"]), self.CENTER)
            self.assertEqual(entity["layer"], "DON")
        self.assertEqual(len(_entities(sk)), 2, "圆环就是一个外圆 + 一个内圆，不许炸成别的")

    def test_donut_rejects_degenerate_and_odd_diameters(self):
        sk = _fresh()
        self.assert_cad_error_frozen(sk, ops.donut, sk, center_nm=(0, 0), outer_diameter_nm=10 * MM,
                                     inner_diameter_nm=10 * MM)
        # 偶数判据是**纳米整数**层面的：20mm+1nm 才是奇数直径（21mm = 21000000nm 是偶数，不拒）。
        self.assert_cad_error_frozen(sk, ops.donut, sk, center_nm=(0, 0),
                                     outer_diameter_nm=20 * MM + 1, inner_diameter_nm=10 * MM)
        self.assertEqual(_entities(sk), [])


class TestOtrackProbe(ParityNewOpsCase):
    CURSOR = (10 * MM, 150_000)
    BASE = (0, 0)
    LOCKED = (10 * MM, 0)

    def test_otrack_locks_cursor_onto_alignment_path_and_records_source(self):
        sk = _fresh()
        (ref,) = ops.otrack(sk, point_nm=self.CURSOR, base_nm=self.BASE,
                            polar_increments_deg=(90.0,), tolerance_nm=1 * MM)
        geom = _geom(sk, ref)
        self.assertEqual(_by_uuid(sk, ref)["kind"], "point")
        self.assertIs(geom["tracked"], True)
        self.assertEqual((geom["x"], geom["y"]), self.LOCKED)
        self.assertEqual((geom["source_x"], geom["source_y"]), self.CURSOR)
        self.assertEqual(geom["axis_deg"], 0.0)
        self.assertEqual(geom["tool"], "otrack")

    def test_otrack_leaves_cursor_alone_when_no_path_is_within_tolerance(self):
        """证伪腿：光标离任何对齐路径都超出容差 ⇒ 必须**不锁**（原样落点）。"""
        sk = _fresh()
        cursor = (10 * MM, 5 * MM)
        (ref,) = ops.otrack(sk, point_nm=cursor, base_nm=self.BASE, polar_increments_deg=(90.0,),
                            tolerance_nm=1 * MM)
        geom = _geom(sk, ref)
        self.assertIs(geom["tracked"], False)
        self.assertEqual((geom["x"], geom["y"]), cursor)

    def test_otrack_mark_false_is_true_zero_not_uncomputed(self):
        sk = _fresh()
        self.assertEqual(ops.otrack(sk, point_nm=self.CURSOR, base_nm=self.BASE, mark=False), [])
        self.assertEqual(_entities(sk), [], "mark=False 只算不落库")
        self.assert_cad_error_frozen(sk, ops.otrack, sk, point_nm=self.CURSOR, base_nm=self.BASE,
                                     bases_nm=[self.BASE])


class TestLayerApplyProbe(ParityNewOpsCase):
    LINE = ((0, 0), (40 * MM, 30 * MM))

    def test_layer_apply_is_in_place_property_edit(self):
        sk = _fresh()
        (ref,) = ops.line_create(sk, p1_nm=self.LINE[0], p2_nm=self.LINE[1], layer="0")
        geom_before = repr(_geom(sk, ref))
        returned = ops.layer_apply(sk, refs=[ref], layer="DIM", color="BYLAYER", linetype="DASHED",
                                   lineweight_nm=-1)
        self.assertEqual(returned, [ref], "原地改写返回被改写实体自身的 uuid")
        entity = _by_uuid(sk, ref)
        self.assertEqual(entity["layer"], "DIM")
        self.assertEqual(entity["color"], "BYLAYER")
        self.assertEqual(entity["linetype"], "DASHED")
        self.assertEqual(entity["lineweight_nm"], -1)
        self.assertEqual(repr(_geom(sk, ref)), geom_before, "属性赋值不许碰几何")
        self.assertEqual(len(_entities(sk)), 1, "原地改写不许新建对象")

    def test_layer_apply_validates_every_ref_before_writing_anything(self):
        sk = _fresh()
        (good,) = ops.line_create(sk, p1_nm=self.LINE[0], p2_nm=self.LINE[1], layer="0")
        before = _snapshot(sk)
        self.assert_cad_error("unknown_reference", ops.layer_apply, sk, refs=[good, "nope-uuid"],
                              layer="DIM")
        self.assertEqual(_snapshot(sk), before, "任一 ref 坏 ⇒ 一个字段都不许落（不做半写）")
        self.assert_cad_error("invalid_argument", ops.layer_apply, sk, refs=[good])
        self.assert_cad_error("invalid_argument", ops.layer_apply, sk, refs=[good], color="PANTONE")


class TestDxfLanding(ParityNewOpsCase):
    def test_boundary_donut_and_layer_land_in_dxf_when_bridge_supports_the_kind(self):
        """DXF 桥只认 line/circle/arc/polyline：这三条真写出去，再从 DXF 文本解析回坐标核对。"""
        sk = _fresh()
        ops.polyline_create(sk, points_nm=[(0, 0), (30 * MM, 0), (30 * MM, 20 * MM), (0, 20 * MM)],
                            closed=True, layer="0")
        ops.boundary(sk, point_nm=(10 * MM, 10 * MM), layer="BND")
        ops.donut(sk, center_nm=(50 * MM, 50 * MM), outer_diameter_nm=20 * MM,
                  inner_diameter_nm=10 * MM, layer="DON")
        (line,) = ops.line_create(sk, p1_nm=(0, 0), p2_nm=(40 * MM, 30 * MM), layer="0")
        ops.layer_apply(sk, refs=[line], layer="DIM", color="BYLAYER", linetype="DASHED",
                        lineweight_nm=-1)

        records = _dxf_records(sketch_to_dxf(sk))

        boundary_polys = [r for r in records if r[0] == "LWPOLYLINE" and _dxf_layer(r) == "BND"]
        self.assertEqual(len(boundary_polys), 1)
        vertices = _dxf_vertices(boundary_polys[0])
        self.assertEqual(set(vertices), {(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0)})
        self.assertEqual(vertices[0], vertices[-1], "DXF 里的边界也必须是闭合环")

        circles = [r for r in records if r[0] == "CIRCLE" and _dxf_layer(r) == "DON"]
        self.assertEqual(len(circles), 2)
        self.assertEqual(sorted(float(r[1]["40"][0]) for r in circles), [5.0, 10.0])
        for record in circles:
            self.assertEqual(_dxf_vertices(record), [(50.0, 50.0)])

        lines = [r for r in records if r[0] == "LINE" and _dxf_layer(r) == "DIM"]
        self.assertEqual(len(lines), 1, "属性赋值后的图层名必须跟着实体写出 DXF")
        self.assertEqual(_dxf_line_points(lines[0]), [(0.0, 0.0), (40.0, 30.0)])

    def test_dxf_bridge_gap_for_new_kinds_is_pinned(self):
        """四个 kind 桥不支持 —— 如实钉住缺口（当次实测，不是推断）。"""
        builders = {
            "xline": lambda sk: ops.xline_create(sk, origin_nm=(0, 0), angle_deg=30.0),
            "point": lambda sk: ops.point_create(sk, point_nm=(1 * MM, 2 * MM)),
            "hatch": lambda sk: ops.hatch_create(
                sk, boundary_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM), (0, 10 * MM)],
                pitch_nm=2 * MM),
            "region": lambda sk: ops.region(
                sk, boundary_refs=ops.polyline_create(
                    sk, points_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM), (0, 10 * MM)],
                    closed=True, layer="0")),
        }
        for kind, build in builders.items():
            sk = _fresh()
            build(sk)
            with self.assertRaises(DocumentError) as ctx:
                sketch_to_dxf(sk)
            self.assertEqual(ctx.exception.reason, "unsupported_sketch_entity_kind",
                             "kind=%s 的 DXF 缺口口径变了，请更新本测试与门" % kind)
            self.assertEqual(ctx.exception.details.get("kind"), kind)


if __name__ == "__main__":
    unittest.main()
