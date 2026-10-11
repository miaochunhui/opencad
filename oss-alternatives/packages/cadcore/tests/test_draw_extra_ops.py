"""tests.test_draw_extra_ops —— DRAW-EXTRA 族（`cadcore.ops.draw_extra`）单测。

本文件是**族接口的尺子**，不是实现的复印件：断言只打冻结接口与几何行为，不 import 实现内部 helper。

冻结接口（逐条打分）
--------------------
1. `FAMILY` 非空字符串、`OPERATIONS` 是 `dict[str, Callable]`；
2. 每个操作的**第一个位置参数**必须叫 `sk_or_doc` 且可位置传递，其余参数必须 KEYWORD_ONLY
   （即 `op(sk_or_doc, **kwargs)` 是唯一合法调用形态）；
3. 返回值是**本次真正改动/新建**的对象 uuid 列表，且每个 uuid 都能在草图里解析到；
4. 错误一律 `cadcore.errors.CadError`，带机器可读 `reason`（不靠解析文案）。

三类事故是这把尺子专门盯的（都留了阴性对照）
--------------------------------------------
① **接不上接口**：关键字名/参数顺序与冻结接口不一致 ⇒ 签名检查器 + 直接按位置调用；
② **静默改几何**：该抛错的地方返回空列表或静默近似 ⇒ 每个边界都断言 `reason`；
③ **假绿尺子**：检查器自己看不见红 ⇒ 两个行为变异体反证检查器会说"违规"
   （`test_interface_checker_sees_violations`）。

夹具自证
--------
* 夹具只依赖 `cadcore.sketch.constraints`（实体字段名的唯一真源）与 `cadcore.docmodel`（不变量），
  所以断言打的是真件；
* `test_fixture_is_real_module_not_stub` 证明被测对象就是 `cadcore/ops/draw_extra.py` 本身；
* 全程内存操作（临时字典/文档），不落盘、不碰别人的文件。
"""

from __future__ import annotations

import inspect
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore import docmodel  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import draw_extra  # noqa: E402
from cadcore.sketch import constraints  # noqa: E402

MM = 1_000_000
L1_FIELDS = {"uuid", "kind", "layer", "construction", "geom"}
#: 规格冻结的公开操作名。证据：`oss-alternatives/docs/research/cad-parity/spec/draw_extra.json`
#: 的 `interface.OPERATIONS` = 「键 = 下表 api 首段的操作函数名（add_ellipse/add_spline/
#: add_construction_line/add_ray/add_point/add_donut/add_region）」——逐条 api 字符串点名的关键字
#: 见下面的 `SPEC_REQUIRED_KEYWORDS`。模块 `draw_extra` 的 §9 段是这批规格名的**真实现**
#: （`add_donut`/`add_region` 等是此前真缺的能力，不是别名桩，也不是为绕门而改门）。
SPEC_FROZEN_OPS = ("add_construction_line", "add_donut", "add_ellipse", "add_point", "add_ray",
                   "add_region", "add_spline")

#: 本族自己的操作名（模块 docstring 的族语义面：椭圆 / 样条近似 / 构造线与射线 / 点 /
#: 打断 / 合并 / 对齐 / 拉伸）；规格 api 表未覆盖这 4 类编辑操作，但它们同样是冻结的操作形态。
FAMILY_OPS = ("align", "break_at", "construction_line", "ellipse", "merge", "point", "ray",
              "spline", "stretch")

#: 冻结的 `OPERATIONS` 键集 = 规格公开名 ∪ 本族操作名（两层都是真实现；相等断言不放宽）。
OPS = tuple(sorted(SPEC_FROZEN_OPS + FAMILY_OPS))

#: 规格 `api` 字符串里点名必有的关键字（缺一个 = 接不上规格冻结接口，报红不 skip）。
SPEC_REQUIRED_KEYWORDS = {
    "add_ellipse": ("center", "major_radius", "minor_radius"),
    "add_spline": ("points_nm", "degree", "closed"),
    "add_construction_line": ("point", "direction"),
    "add_ray": ("start", "direction"),
    "add_point": ("position",),
    "add_donut": ("center", "radius", "width"),
    "add_region": ("boundary_refs",),
}


# ── 冻结接口检查器（含自证变异体） ──────────────────────────────────────────────────────

def interface_violations(op) -> list[str]:
    """按冻结接口检查一个操作的签名，返回违规说明（空列表 = 合规）。

    规则只有两条，不掺实现内部知识：① 第一个参数叫 `sk_or_doc` 且可位置传递；
    ② 其余参数必须 KEYWORD_ONLY（"其余走关键字"）。
    """
    problems: list[str] = []
    parameters = list(inspect.signature(op).parameters.values())
    if not parameters:
        return ["operation takes no parameters"]
    head = parameters[0]
    if head.name != "sk_or_doc":
        problems.append("first parameter is %r, expected 'sk_or_doc'" % (head.name,))
    if head.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY):
        problems.append("first parameter %r is not positionally passable (%s)"
                        % (head.name, head.kind.name))
    for parameter in parameters[1:]:
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            problems.append("parameter %r is not keyword-only (%s)"
                            % (parameter.name, parameter.kind.name))
    return problems


def _mutant_first_param_misnamed(doc, *, refs):  # 变异体①：首参名与冻结接口不符
    return []


def _mutant_tail_positional(sk_or_doc, refs):  # 变异体②：其余参数没有走关键字
    return []


# ── 夹具（只建 L1 实体，不碰实现内部） ──────────────────────────────────────────────────

def new_sketch() -> dict:
    """一张空草图（L1 字段齐备；uuid 走 `constraints.new_uuid`，不手搓）。"""
    return {"uuid": constraints.new_uuid(), "plane": "XY", "entities": [], "constraints": [],
            "dimensions": []}


def entities(sk) -> list:
    """草图实体表（字段名真源 = `constraints.sketch_entities`）。"""
    return constraints.sketch_entities(sk)


def entity_of(sk, uuid):
    for entity in entities(sk):
        if entity["uuid"] == uuid:
            return entity
    raise AssertionError("uuid %r not found in sketch" % (uuid,))


def add_line(sk, start, end, *, layer="0", construction=False) -> str:
    """夹具：直接落一条 L1 直线（模拟"别的族先画了线"，供 break/merge/align/stretch 消费）。"""
    uuid = constraints.new_uuid()
    entities(sk).append({"uuid": uuid, "kind": "line", "layer": layer,
                         "construction": construction,
                         "geom": {"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]}})
    return uuid


def add_polyline(sk, points, *, closed=False, layer="0", construction=False) -> str:
    """夹具：直接落一条 L1 折线（同上）。"""
    uuid = constraints.new_uuid()
    geom = {"points": [[x, y] for x, y in points]}
    if closed:
        geom["closed"] = True
    entities(sk).append({"uuid": uuid, "kind": "polyline", "layer": layer,
                         "construction": construction, "geom": geom})
    return uuid


def call(name, sk_or_doc, **kwargs):
    """**按冻结接口调用**：第一个位置参数是 `sk_or_doc`，其余全走关键字。"""
    return draw_extra.OPERATIONS[name](sk_or_doc, **kwargs)


class FamilyTestCase(unittest.TestCase):
    """公共断言：返回值必须是 sketch 里真实存在的 uuid 列表（不许凭空造 id）。"""

    def assert_uuid_list(self, refs, sk, *, expect=None):
        self.assertIsInstance(refs, list)
        if expect is not None:
            self.assertEqual(len(refs), expect)
        known = {entity["uuid"] for entity in entities(sk)}
        for ref in refs:
            self.assertIsInstance(ref, str)
            self.assertIn(ref, known)
        return refs

    def assert_reason(self, reason, func, *args, **kwargs):
        with self.assertRaises(CadError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason)
        return ctx.exception


# ── 1) 冻结接口 ────────────────────────────────────────────────────────────────────────

class FrozenInterfaceTests(FamilyTestCase):

    def test_family_and_operations_shape(self):
        self.assertIsInstance(draw_extra.FAMILY, str)
        self.assertTrue(draw_extra.FAMILY)
        self.assertIsInstance(draw_extra.OPERATIONS, dict)
        self.assertEqual(sorted(draw_extra.OPERATIONS), list(OPS))
        self.assertEqual(set(OPS), set(SPEC_FROZEN_OPS) | set(FAMILY_OPS))
        for name in SPEC_FROZEN_OPS:  # 规格公开面必须在表里（不靠别名/属性兜底）
            with self.subTest(spec_op=name):
                self.assertIn(name, draw_extra.OPERATIONS)
        for name, required in sorted(SPEC_REQUIRED_KEYWORDS.items()):  # 规格 api 点名的关键字
            with self.subTest(spec_kwargs=name):
                params = set(inspect.signature(draw_extra.OPERATIONS[name]).parameters)
                missing = set(required) - params
                self.assertEqual(missing, set(),
                                 "%s missing spec keywords %r (has %r)"
                                 % (name, sorted(missing), sorted(params)))
        for name, op in draw_extra.OPERATIONS.items():
            self.assertTrue(callable(op), name)
        facts = draw_extra.module_facts()
        self.assertEqual(facts["family"], draw_extra.FAMILY)
        self.assertEqual(facts["op_count"], len(draw_extra.OPERATIONS))
        self.assertEqual(sorted(facts["operations"]), list(OPS))

    def test_signature_is_sk_or_doc_plus_keyword_only(self):
        for name, op in draw_extra.OPERATIONS.items():
            with self.subTest(op=name):
                self.assertEqual(interface_violations(op), [])

    def test_interface_checker_sees_violations(self):
        """尺子自证：变异体必须被判违规（否则"签名全绿"是假绿）。"""
        self.assertEqual(interface_violations(draw_extra.ellipse), [])
        misnamed = interface_violations(_mutant_first_param_misnamed)
        self.assertTrue(misnamed, "checker missed a misnamed first parameter")
        self.assertIn("sk_or_doc", misnamed[0])
        positional = interface_violations(_mutant_tail_positional)
        self.assertTrue(positional, "checker missed a non-keyword-only tail parameter")
        self.assertIn("refs", positional[0])

    def test_fixture_is_real_module_not_stub(self):
        self.assertIs(inspect.getmodule(draw_extra.ellipse), draw_extra)
        self.assertTrue(inspect.getsourcefile(draw_extra).endswith("cadcore/ops/draw_extra.py"))

    def test_creator_ops_return_uuid_lists(self):
        cases = {
            "ellipse": dict(center_nm=(0, 0), rx_nm=10 * MM, ry_nm=5 * MM),
            "spline": dict(points_nm=[(0, 0), (5 * MM, 5 * MM), (10 * MM, 0)]),
            "construction_line": dict(start_nm=(0, 0), end_nm=(1 * MM, 0)),
            "ray": dict(start_nm=(0, 0), through_nm=(1 * MM, 0), length_nm=2 * MM),
            "point": dict(point_nm=(1 * MM, 2 * MM)),
        }
        for name, kwargs in cases.items():
            with self.subTest(op=name):
                sk = new_sketch()
                refs = call(name, sk, **kwargs)
                self.assert_uuid_list(refs, sk, expect=1)
                self.assertEqual(len(entities(sk)), 1)

    def test_transform_ops_return_changed_uuids(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (100 * MM, 0))
        parts = call("break_at", sk, ref=line, t=0.25)
        self.assert_uuid_list(parts, sk, expect=2)
        merged = call("merge", sk, refs=parts)
        self.assert_uuid_list(merged, sk, expect=1)
        moved = call("align", sk, refs=[merged[0]], axis="y", key="min", value_nm=3 * MM)
        self.assert_uuid_list(moved, sk, expect=1)
        stretched = call("stretch", sk, refs=[merged[0]], window_nm=(-1, 2 * MM, 1, 4 * MM),
                         dx_nm=1 * MM, dy_nm=1 * MM)
        self.assert_uuid_list(stretched, sk, expect=1)

    def test_sk_or_doc_accepts_three_forms(self):
        bare = new_sketch()
        call("point", bare, point_nm=(0, 0))
        self.assertEqual(len(entities(bare)), 1)

        wrapped = {"sketches": [new_sketch(), new_sketch()]}
        call("point", wrapped, point_nm=(0, 0), sketch_index=1)
        self.assertEqual(len(entities(wrapped["sketches"][0])), 0)
        self.assertEqual(len(entities(wrapped["sketches"][1])), 1)

        doc = docmodel.new_document()
        doc.sketches = [new_sketch()]
        call("point", doc, point_nm=(0, 0))
        self.assertEqual(len(entities(doc.sketches[0])), 1)

        self.assert_reason("no_sketch", call, "point", {"sketches": []}, point_nm=(0, 0))
        self.assert_reason("no_sketch", call, "point", {"sketches": [new_sketch()]},
                           point_nm=(0, 0), sketch_index=1)


# ── 2) 椭圆 / 点 ───────────────────────────────────────────────────────────────────────

class EllipsePointTests(FamilyTestCase):

    def test_ellipse_native_geometry_exact(self):
        sk = new_sketch()
        refs = call("ellipse", sk, center_nm=(10 * MM, 20 * MM), rx_nm=30 * MM, ry_nm=10 * MM,
                    rotation_deg=30.0)
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "ellipse")
        self.assertEqual(entity["layer"], "0")
        self.assertIs(entity["construction"], False)
        self.assertEqual(entity["geom"], {"cx": 10 * MM, "cy": 20 * MM, "rx": 30 * MM,
                                          "ry": 10 * MM, "rotation_deg": 30.0})

    def test_ellipse_polyline_approximation_stays_on_ellipse(self):
        sk = new_sketch()
        center, rx, ry = (3 * MM, -2 * MM), 10 * MM, 4 * MM
        refs = call("ellipse", sk, center_nm=center, rx_nm=rx, ry_nm=ry, segments=16)
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "polyline")
        self.assertIs(entity["geom"]["closed"], True)
        points = entity["geom"]["points"]
        self.assertEqual(len(points), 16)
        self.assertEqual(points[0], [center[0] + rx, center[1]])  # theta=0 的精确采样点
        tolerance = 2.0 * (0.5 / rx + 0.5 / ry)  # 每轴 ≤0.5 nm 取整误差换算到归一化半径
        for x, y in points:
            radius = ((x - center[0]) / rx) ** 2 + ((y - center[1]) / ry) ** 2
            self.assertLessEqual(abs(radius - 1.0), tolerance, "point off ellipse: %r" % ((x, y),))

    def test_ellipse_guards(self):
        for bad in (1, 2):
            self.assert_reason("too_few_segments", call, "ellipse", new_sketch(),
                               center_nm=(0, 0), rx_nm=MM, ry_nm=MM, segments=bad)
        self.assert_reason("invalid_length", call, "ellipse", new_sketch(),
                           center_nm=(0, 0), rx_nm=0, ry_nm=MM)
        self.assert_reason("invalid_length", call, "ellipse", new_sketch(),
                           center_nm=(0, 0), rx_nm=1.5, ry_nm=MM)
        self.assert_reason("invalid_length", call, "ellipse", new_sketch(),
                           center_nm=(0, 0), rx_nm=True, ry_nm=MM)
        self.assert_reason("invalid_point", call, "ellipse", new_sketch(),
                           center_nm=(0, 0, 0), rx_nm=MM, ry_nm=MM)

    def test_point_entity_is_l1_clean(self):
        sk = new_sketch()
        refs = call("point", sk, point_nm=(1 * MM, -2 * MM), construction=True)
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "point")
        self.assertEqual(entity["geom"], {"x": 1 * MM, "y": -2 * MM})
        self.assertIs(entity["construction"], True)
        self.assertEqual(set(entity), L1_FIELDS)  # 不自造第六个字段


# ── 3) 样条 / 构造线 / 射线 ─────────────────────────────────────────────────────────────

class SplineConstructionRayTests(FamilyTestCase):

    CONTROL = [(0, 0), (10 * MM, 5 * MM), (20 * MM, -5 * MM), (30 * MM, 0)]
    SAMPLES = 8

    def test_spline_interpolates_every_control_point(self):
        sk = new_sketch()
        refs = call("spline", sk, points_nm=self.CONTROL, samples_per_span=self.SAMPLES)
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "polyline")
        points = entity["geom"]["points"]
        self.assertEqual(len(points), (len(self.CONTROL) - 1) * self.SAMPLES + 1)
        for index, control in enumerate(self.CONTROL):
            self.assertEqual(points[index * self.SAMPLES], [control[0], control[1]],
                             "control point %d not interpolated" % index)

    def test_spline_closed_does_not_duplicate_first_point(self):
        sk = new_sketch()
        refs = call("spline", sk, points_nm=self.CONTROL, samples_per_span=self.SAMPLES,
                    closed=True)
        points = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])["geom"]["points"]
        self.assertEqual(len(points), len(self.CONTROL) * self.SAMPLES)
        self.assertEqual(points[0], [self.CONTROL[0][0], self.CONTROL[0][1]])
        self.assertNotEqual(points[-1], points[0])

    def test_spline_guards(self):
        for degree in (2, 4):
            self.assert_reason("unsupported_degree", call, "spline", new_sketch(),
                               points_nm=self.CONTROL, degree=degree)
        self.assert_reason("too_few_points", call, "spline", new_sketch(),
                           points_nm=[(0, 0), (MM, 0)][:1])
        self.assert_reason("invalid_argument", call, "spline", new_sketch(),
                           points_nm=self.CONTROL, samples_per_span=0)

    def test_construction_line_geometry_and_guards(self):
        sk = new_sketch()
        refs = call("construction_line", sk, start_nm=(0, 0), end_nm=(1 * MM, 2 * MM))
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "line")
        self.assertIs(entity["construction"], True)
        self.assertEqual(entity["geom"], {"x1": 0, "y1": 0, "x2": 1 * MM, "y2": 2 * MM})
        self.assert_reason("degenerate_geometry", call, "construction_line", new_sketch(),
                           start_nm=(5, 5), end_nm=(5, 5))

    def test_ray_is_a_finite_segment_not_an_infinite_one(self):
        sk = new_sketch()
        refs = call("ray", sk, start_nm=(0, 0), through_nm=(3, 4), length_nm=10)
        entity = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(entity["kind"], "line")
        self.assertIs(entity["construction"], True)
        self.assertEqual((entity["geom"]["x2"], entity["geom"]["y2"]), (6, 8))
        self.assertEqual(set(entity["geom"]), {"x1", "y1", "x2", "y2"})  # 无"无限几何"字段
        self.assert_reason("invalid_length", call, "ray", new_sketch(), start_nm=(0, 0),
                           through_nm=(1, 0), length_nm=0)
        self.assert_reason("degenerate_geometry", call, "ray", new_sketch(), start_nm=(0, 0),
                           through_nm=(0, 0), length_nm=MM)


# ── 4) 打断 / 合并 ─────────────────────────────────────────────────────────────────────

class BreakMergeTests(FamilyTestCase):

    def test_break_line_at_point_and_at_t_agree(self):
        for kwargs in ({"at_nm": (40 * MM, 7 * MM)}, {"t": 0.4}):
            with self.subTest(kwargs=kwargs):
                sk = new_sketch()
                line = add_line(sk, (0, 0), (100 * MM, 0), layer="L1", construction=True)
                parts = call("break_at", sk, ref=line, **kwargs)
                self.assert_uuid_list(parts, sk, expect=2)
                self.assertEqual(len(entities(sk)), 2)  # 原实体被移除
                head, tail = (entities(sk)[0], entities(sk)[1])
                self.assertEqual((head["geom"]["x1"], head["geom"]["x2"]), (0, 40 * MM))
                self.assertEqual((tail["geom"]["x1"], tail["geom"]["x2"]), (40 * MM, 100 * MM))
                self.assertEqual({head["layer"], tail["layer"]}, {"L1"})
                self.assertEqual({head["construction"], tail["construction"]}, {True})

    def test_break_line_guards(self):
        for kwargs in ({"t": 0.0}, {"t": 1.0}, {"at_nm": (0, 0)}, {"at_nm": (100 * MM, 0)}):
            sk = self._sketch_with_line()
            self.assert_reason("outside_entity", call, "break_at", sk,
                               ref=entities(sk)[0]["uuid"], **kwargs)

    def test_break_rejects_unrepresentable_point(self):
        """阴性对照：垂足在 1 nm 分辨率下不落在线 µ 上 ⇒ not_on_entity（不硬掰几何）。"""
        sk = new_sketch()
        line = add_line(sk, (0, 0), (2, 1))
        self.assert_reason("not_on_entity", call, "break_at", sk, ref=line, at_nm=(1, 0))
        self.assertEqual(len(entities(sk)), 1)  # 报错不产生副作用

    def test_break_rejects_curves_and_unknown_refs(self):
        sk = new_sketch()
        ellipse_ref = call("ellipse", sk, center_nm=(0, 0), rx_nm=10 * MM, ry_nm=10 * MM)[0]
        self.assert_reason("unsupported_kind", call, "break_at", sk, ref=ellipse_ref,
                           at_nm=(0, 0))
        self.assert_reason("unknown_reference", call, "break_at", sk, ref="no-such-uuid",
                           at_nm=(0, 0))

    def test_break_polyline_at_interior_vertex(self):
        sk = new_sketch()
        poly = add_polyline(sk, [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM), (0, 10 * MM)])
        parts = call("break_at", sk, ref=poly, vertex=1)
        self.assert_uuid_list(parts, sk, expect=2)
        head, tail = entities(sk)[0], entities(sk)[1]
        self.assertEqual(head["geom"]["points"], [[0, 0], [10 * MM, 0]])
        self.assertEqual(tail["geom"]["points"], [[10 * MM, 0], [10 * MM, 10 * MM], [0, 10 * MM]])
        for vertex in (0, 3):
            self.assert_reason("outside_entity", call, "break_at", self._sketch_with_polyline(),
                               ref=self._last_uuid, vertex=vertex)
        closed = new_sketch()
        closed_poly = add_polyline(closed, [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)], closed=True)
        self.assert_reason("unsupported_kind", call, "break_at", closed, ref=closed_poly, vertex=1)

    def test_merge_collinear_becomes_one_line(self):
        sk = new_sketch()
        first = add_line(sk, (0, 0), (10 * MM, 0), layer="L1", construction=True)
        second = add_line(sk, (30 * MM, 0), (10 * MM, 0), layer="L1")  # 反向也给链上
        refs = call("merge", sk, refs=[second, first])
        merged = entity_of(sk, self.assert_uuid_list(refs, sk, expect=1)[0])
        self.assertEqual(merged["kind"], "line")
        geom = merged["geom"]
        # 合并保留链的行进方向（冻结接口未规定朝向）⇒ 断言"这条线段"本身，不钉死端点顺序
        self.assertEqual({(geom["x1"], geom["y1"]), (geom["x2"], geom["y2"])},
                         {(0, 0), (30 * MM, 0)})
        self.assertEqual(merged["layer"], "L1")
        self.assertIs(merged["construction"], True)
        self.assertEqual(len(entities(sk)), 1)

    def test_merge_non_collinear_becomes_polyline(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (10 * MM, 0))
        b = add_line(sk, (10 * MM, 0), (10 * MM, 10 * MM))
        merged = entity_of(sk, self.assert_uuid_list(call("merge", sk, refs=[a, b]), sk,
                                                     expect=1)[0])
        self.assertEqual(merged["kind"], "polyline")
        self.assertEqual(merged["geom"]["points"],
                         [[0, 0], [10 * MM, 0], [10 * MM, 10 * MM]])
        self.assertNotIn("closed", merged["geom"])

    def test_merge_tolerance_and_guards(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (10 * MM, 0))
        b = add_line(sk, (10 * MM + 1, 0), (20 * MM, 0))
        self.assert_reason("not_connected", call, "merge", sk, refs=[a, b])
        merged = entity_of(sk, self.assert_uuid_list(call("merge", sk, refs=[a, b], tol_nm=1), sk,
                                                     expect=1)[0])
        self.assertEqual(merged["geom"], {"x1": 0, "y1": 0, "x2": 20 * MM, "y2": 0})
        sk2 = new_sketch()
        c = add_line(sk2, (0, 0), (MM, 0))
        d = add_line(sk2, (20 * MM, 0), (30 * MM, 0))
        self.assert_reason("not_connected", call, "merge", sk2, refs=[c, d])
        self.assert_reason("too_few_refs", call, "merge", sk2, refs=[c])
        self.assert_reason("duplicate_reference", call, "merge", sk2, refs=[c, c])
        self.assert_reason("invalid_argument", call, "merge", sk2, refs="ab")

    # 夹具工厂：给上面的循环用（每次新草图，避免跨例污染）
    def _sketch_with_line(self):
        sk = new_sketch()
        add_line(sk, (0, 0), (100 * MM, 0))
        return sk

    def _sketch_with_polyline(self):
        sk = new_sketch()
        self._last_uuid = add_polyline(sk, [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM),
                                            (0, 10 * MM)])
        return sk


# ── 5) 对齐 / 拉伸 ─────────────────────────────────────────────────────────────────────

class AlignStretchTests(FamilyTestCase):

    def test_align_moves_only_the_misaligned(self):
        sk = new_sketch()
        base = add_line(sk, (0, 0), (10 * MM, 0))
        mid = add_line(sk, (0, 5 * MM), (10 * MM, 5 * MM))
        top = add_line(sk, (0, 10 * MM), (10 * MM, 10 * MM))
        moved = call("align", sk, refs=[base, mid, top], axis="y", key="mid", to="min")
        self.assert_uuid_list(moved, sk, expect=2)
        self.assertNotIn(base, moved)  # 已经在位的不动、也不返回
        self.assertEqual({entity_of(sk, ref)["geom"]["y1"] for ref in (base, mid, top)}, {0})
        for ref, expected in ((mid, 0), (top, 0)):
            entity = entity_of(sk, ref)
            self.assertEqual((entity["geom"]["y1"] + entity["geom"]["y2"]) // 2, expected)

    def test_align_value_axis_and_guards(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (10 * MM, 0))
        b = add_line(sk, (3 * MM, 0), (13 * MM, 0))
        moved = call("align", sk, refs=[a, b], axis="x", key="start", value_nm=7 * MM)
        self.assert_uuid_list(moved, sk, expect=2)
        self.assertEqual(entity_of(sk, a)["geom"]["x1"], 7 * MM)
        self.assertEqual(entity_of(sk, b)["geom"]["x1"], 7 * MM)
        self.assert_reason("invalid_argument", call, "align", sk, refs=[a], axis="z")
        self.assert_reason("empty_selection", call, "align", sk, refs=[])
        self.assert_reason("unknown_reference", call, "align", sk, refs=["no-such-uuid"])

    def test_align_handles_family_new_kinds(self):
        """`point`/`ellipse` 不在 `modify.move` 支持面内 ⇒ 必须走本族自己的平移。"""
        sk = new_sketch()
        ellipse_ref = call("ellipse", sk, center_nm=(10 * MM, 0), rx_nm=2 * MM, ry_nm=MM)[0]
        point_ref = call("point", sk, point_nm=(0, 0))[0]
        moved = call("align", sk, refs=[ellipse_ref, point_ref], axis="x", key="center",
                     to="first")
        self.assert_uuid_list(moved, sk, expect=1)
        self.assertEqual(moved, [point_ref])
        self.assertEqual(entity_of(sk, point_ref)["geom"]["x"], 10 * MM)
        self.assertEqual(entity_of(sk, ellipse_ref)["geom"]["cx"], 10 * MM)

    def test_stretch_line_moves_only_the_selected_endpoint(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (10 * MM, 0))
        moved = call("stretch", sk, refs=[line], window_nm=(-1, -1, 1 * MM, 1 * MM),
                     dx_nm=2 * MM, dy_nm=3 * MM)
        self.assertEqual(moved, [line])
        self.assertEqual(entity_of(sk, line)["geom"],
                         {"x1": 2 * MM, "y1": 3 * MM, "x2": 10 * MM, "y2": 0})

    def test_stretch_line_wholly_inside_window_is_translated(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (10 * MM, 0))
        moved = call("stretch", sk, refs=[line], window_nm=(-MM, -MM, 20 * MM, MM),
                     dx_nm=MM, dy_nm=MM)
        self.assertEqual(moved, [line])
        self.assertEqual(entity_of(sk, line)["geom"],
                         {"x1": MM, "y1": MM, "x2": 11 * MM, "y2": MM})

    def test_stretch_polyline_moves_only_vertices_in_window(self):
        sk = new_sketch()
        poly = add_polyline(sk, [(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)])
        moved = call("stretch", sk, refs=[poly], window_nm=(-1, -1, 1, 1),
                     dx_nm=5 * MM, dy_nm=6 * MM)
        self.assertEqual(moved, [poly])
        self.assertEqual(entity_of(sk, poly)["geom"]["points"],
                         [[5 * MM, 6 * MM], [10 * MM, 0], [10 * MM, 10 * MM]])

    def test_stretch_curved_entity_needs_the_whole_window(self):
        sk = new_sketch()
        circle = call("ellipse", sk, center_nm=(50 * MM, 50 * MM), rx_nm=10 * MM, ry_nm=10 * MM)[0]
        # ① 窗口切到一部分（在包络盒内、但没框住整个圆）⇒ 显式报错，不假装能拉伸
        self.assert_reason("partial_stretch_unsupported", call, "stretch", sk, refs=[circle],
                           window_nm=(45 * MM, 45 * MM, 55 * MM, 55 * MM), dx_nm=MM)
        self.assertEqual(entity_of(sk, circle)["geom"]["cx"], 50 * MM)  # 报错不产生副作用
        # ② 完全框住 ⇒ 整体平移
        moved = call("stretch", sk, refs=[circle], window_nm=(30 * MM, 30 * MM, 70 * MM, 70 * MM),
                     dx_nm=MM, dy_nm=2 * MM)
        self.assertEqual(moved, [circle])
        self.assertEqual((entity_of(sk, circle)["geom"]["cx"], entity_of(sk, circle)["geom"]["cy"]),
                         (51 * MM, 52 * MM))
        # ③ 窗口完全不相交 ⇒ 不动、不返回 ⇒ 整批没命中就报 empty_selection（不许空列表冒充成功）
        self.assert_reason("empty_selection", call, "stretch", sk, refs=[circle],
                           window_nm=(0, 0, MM, MM), dx_nm=MM)

    def test_stretch_guards(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (10 * MM, 0))
        self.assert_reason("no_displacement", call, "stretch", sk, refs=[line],
                           window_nm=(-1, -1, 1, 1))
        self.assert_reason("empty_selection", call, "stretch", sk, refs=[],
                           window_nm=(-1, -1, 1, 1), dx_nm=MM)
        self.assert_reason("degenerate_window", call, "stretch", sk, refs=[line],
                           window_nm=(0, 0, 0, 5), dx_nm=MM)


# ── 6) 模块契约（三态口径 / 无 locale 字面量 / L1 不变量 / 自带自检） ─────────────────────────

class ModuleContractTests(FamilyTestCase):

    def test_three_state_contract(self):
        self.assertEqual(draw_extra.module_facts()["three_state"],
                         {"zero": 0, "unmeasured": -1, "not_applicable": "NA"})

    def test_source_has_no_locale_literal(self):
        source = Path(inspect.getsourcefile(draw_extra)).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'"(zh-CN|en|ja)"', source),
                          "locale literal found in cadcore/ops/draw_extra.py")
        self.assertEqual(re.findall(r'"\s*(?:zh-CN|en|ja)\s*"', source), [])

    def test_entities_stay_l1_clean_after_mixed_batch(self):
        sk = new_sketch()
        call("ellipse", sk, center_nm=(0, 0), rx_nm=MM, ry_nm=MM)
        call("spline", sk, points_nm=[(0, 0), (MM, MM), (2 * MM, 0)])
        call("construction_line", sk, start_nm=(0, 0), end_nm=(MM, 0))
        call("ray", sk, start_nm=(0, 0), through_nm=(1, 0), length_nm=MM)
        call("point", sk, point_nm=(0, 0))
        doc = docmodel.new_document()
        doc.sketches = [sk]
        self.assertEqual(docmodel.check_invariants(doc), [])
        for entity in entities(sk):
            self.assertEqual(set(entity), L1_FIELDS)

    def test_selftest_is_green(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = draw_extra.selftest()
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertIn("DRAW_EXTRA_OPS verdict=PASS", output)
        self.assertIn("checks=6/6", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
