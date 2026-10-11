"""tests.test_draw_ops_ops —— 族 `draw_ops` 的独立单测（数值硬断言 / 边界 / 与真件对拍）。

对账判据（本文件写盘时的证据，照实写）
--------------------------------------
写盘前本族**没有**单测文件：`python3 -m unittest tests.test_draw_ops_ops` 报
`ModuleNotFoundError: No module named 'tests.test_draw_ops_ops'`（红点在**测试侧**：文件缺席，
不是实现侧的关键字分歧）。全量套件里也 grep 不到任何 `draw_ops` 红点 ⇒ 本族此前的"绿"是
**没被测**，不是被测过。因此本文件按**冻结接口**补齐尺子，实现侧只做接口取证、不按测试口味改。

本文件打的是**真件**：`cadcore/ops/draw_ops.py` 就在盘上（不是双/桩）。若它 import 失败 ⇒
当场红，绝不回落到替身（那会把"真件坏了"伪装成"没落盘"）。

尺子怎么保证看得见红（不删断言换绿）
------------------------------------
1. **闭合性尺子带阴性对照**：`primitives.closed_profiles(tol_nm=0)` 认得出槽的闭环，把其中一条
   边的端点挪 **1 nm** 后必须**认不出**（`[]`）——证明"闭环"这条断言不是恒真；
2. **源码口径尺子带阳性对照**：locale 字面量检测器对真源码报 0，喂它一行 `x = "zh-CN"` 必须报 1；
3. **真为零 vs 没算**分开判：`array_rect(rows=1, cols=1)` / `array_polar(count=1)` 返回 `[]` 且
   实体表**逐字节不变**（确实没有新对象）；非法入参一律抛 `CadError`（不是返回 `[]`）。

口径
----
- 长度一律**整数纳米**；断言打在**落库后的文档**上（uuid → 实体），不只看返回值；
- 错误比 `CadError.reason`（族接口冻结口径）；比不了的（`_nm` 长度校验）就断言
  「抛 CadError ∧ 草图一字未动」，不静默放宽；
- 非 docstring 的字符串字面量一律 ASCII（与包内 i18n 口径一致），中文只出现在 docstring/注释里。
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import re
import unittest
from collections.abc import Mapping
from pathlib import Path

from cadcore.errors import CadError
from cadcore.sketch import commands as cmds
from cadcore.sketch import constraints as cons
from cadcore.sketch import primitives as prim

#: 本族 id（冻结接口里的 `FAMILY`）。
FAMILY_ID = "draw_ops"

#: 本族应有的操作名（冻结接口里的 `OPERATIONS` 键集）。
#:
#: 对账（2026-09-30，本席裁决：改**测试侧**的漏列，不动实现）—— 初版只列了 7 个"参数化绘图"操作，
#: 漏掉三个基本图元，三处证据同向：
#:   (1) 冻结规格 `docs/research/cad-parity/spec/draw_ops.json` 的 `operations_index` 把
#:       line / circle / arc 明确列为本族 `must_have[0..3]`；本模块按冻结名口径记为
#:       `line_create` / `circle_create` / `arc_create`；
#:   (2) 真件 `cadcore/ops/draw_ops.py` 的 docstring 把三者写进族接口与"复用了什么"一节，
#:       `__all__` / `OPERATIONS` / 自带 selftest（判定行 `ops=10`）三处都是 10；
#:   (3) 三者都满足 `op(sk_or_doc, *, ...) -> list[str]` 的族签名（下面的签名尺子照样打它们）。
#: 断言没放宽：`op_count` 仍与 `len(OP_NAMES)` **精确**比等，只是把真有的三个操作数进来。
OP_NAMES = (
    "line_create",
    "circle_create",
    "arc_create",
    "polyline_create",
    "polyline_edit",
    "rectangle_params",
    "polygon_params",
    "slot_params",
    "array_rect",
    "array_polar",
    # 2026-09-30 本席补：`draw_ops.py` 同日新增的三类 L1 真几何操作（boundary / region / donut），
    # 三者都满足 `op(sk_or_doc, *, ...) -> list[str]`（下面的签名尺子照样打它们），
    # 断言未放宽：`op_count` 仍与 `len(OP_NAMES)` **精确**比等。
    "boundary",
    "region",
    "donut",
    # 2026-09-30 本席再补 7 项（族门实测 missing=7 的那七条）：
    #   椭圆 / 样条 ⇒ 一个闭合 `polyline` **逼近**（口径与误差上界写在各自 docstring）；
    #   构造线 / 点 ⇒ L1 真实体（无 extent 的 construction / 零延展定位点）；
    #   填充 ⇒ 真扫掠剖面线段；OTRACK ⇒ 真助手（对齐路径求解 + 落点）；图层 ⇒ **实体属性赋值**
    #   （L1 无图层表，"当前图层"归文档状态族，本族按缺失记）。
    # 断言仍不放宽：`op_count` 与 `len(OP_NAMES)` **精确**比等，签名尺子照样打这 7 条。
    "ellipse_create",
    "spline_create",
    "xline_create",
    "point_create",
    "hatch_create",
    "otrack",
    "layer_apply",
)
# 注：上方「三处都是 10」的历史对账行写于补 boundary/region/donut **之前**；补完后为 13 项。
# 2026-09-30 本席再补 7 项 ⇒ 现为 20 项；`__all__` / `OPERATIONS` / 自带 selftest 三处一致。

#: 真件模块名。
REAL_MODULE = "cadcore.ops.draw_ops"

MM = 1_000_000  #: 1 mm，整数纳米

_SOURCE = Path(__file__).resolve().parents[1] / "cadcore" / "ops" / "draw_ops.py"

#: locale 字面量检测器（族模块里不许出现语言码字面量，文案一律走 key）。
_LOCALE_RULER = re.compile(r"""["'](zh-CN|en|ja)["']""")


def _reload_real_module():
    """import 真件；真件不在盘上 / import 失败都当场红（不许回落替身）。"""
    spec = importlib.util.find_spec(REAL_MODULE)
    if spec is None:
        raise AssertionError("family module is not on disk: %s" % REAL_MODULE)
    return importlib.import_module(REAL_MODULE)


ops = _reload_real_module()


def _get(obj, name, default=None):
    """结构取值（映射 / 对象双兼容）——测试侧独立实现，不借真件的私有 helper。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _entities(sk):
    return list(cons.sketch_entities(sk))


def _by_uuid(sk, uuid_):
    for entity in _entities(sk):
        if _get(entity, "uuid") == uuid_:
            return entity
    return None


def _kind(entity):
    return _get(entity, "kind") or _get(entity, "type")


def _line_segment(entity):
    """line 实体 → 归一化线段（frozenset{(x1,y1),(x2,y2)}，不看方向）。"""
    geom = _get(entity, "geom") or {}
    return frozenset(((int(_get(geom, "x1")), int(_get(geom, "y1"))),
                      (int(_get(geom, "x2")), int(_get(geom, "y2")))))


def _line_points(entity):
    """line 实体 → ((x1,y1), (x2,y2))，保留方向（阵列偏移断言要看方向）。"""
    geom = _get(entity, "geom") or {}
    return ((int(_get(geom, "x1")), int(_get(geom, "y1"))),
            (int(_get(geom, "x2")), int(_get(geom, "y2"))))


def _points(entity):
    geom = _get(entity, "geom") or {}
    return [(int(pair[0]), int(pair[1])) for pair in _get(geom, "points")]


def _snapshot(sk):
    """实体表快照（uuid + geom 深拷贝的等价物）——用来证明"失败时一字未动"。"""
    return [(str(_get(entity, "uuid")), repr(_get(entity, "geom"))) for entity in _entities(sk)]


def _fresh():
    """空的 L1 草图（三张表齐全，不借真件夹具）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def _base_line(sk, p1=(0, 0), p2=(10 * MM, 0)):
    return cmds.add_line(sk, p1=p1, p2=p2, layer="0")


class CadErrorAssertions(unittest.TestCase):
    """共用断言：错误必须是 `CadError` 且 `reason` 精确命中。"""

    def assert_cad_error(self, reason, callable_, *args, **kwargs):
        with self.assertRaises(CadError) as ctx:
            callable_(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason,
                         "reason mismatch: got %r want %r" % (ctx.exception.reason, reason))
        return ctx.exception

    def assert_cad_error_and_frozen(self, sk, callable_, *args, reason_nonempty=True, **kwargs):
        """比不了 reason 的入参：断言抛 CadError ∧ 实体表一字未动（不许半接受）。"""
        before = _snapshot(sk)
        with self.assertRaises(CadError) as ctx:
            callable_(*args, **kwargs)
        if reason_nonempty:
            self.assertIsInstance(ctx.exception.reason, str)
            self.assertTrue(ctx.exception.reason)
        self.assertEqual(_snapshot(sk), before, "failed call must not touch the sketch")
        return ctx.exception


class TestNewL1Ops(CadErrorAssertions):
    """2026-09-30 本席补：椭圆/样条逼近、构造线、点、填充、OTRACK、图层属性（族门 missing=7 那七条）。"""

    @staticmethod
    def _geom(entity):
        return _get(entity, "geom") or {}

    def test_ellipse_is_one_closed_polyline_with_exact_bbox(self):
        sk = _fresh()
        (ref,) = ops.ellipse_create(sk, center_nm=(0, 0), rx_nm=20 * MM, ry_nm=10 * MM)
        entity = _by_uuid(sk, ref)
        self.assertEqual(_kind(entity), "polyline", "L1 无原生椭圆实体 ⇒ 折线逼近")
        points = _points(entity)
        self.assertEqual(points[0], points[-1], "逼近必须是闭合环")
        self.assertGreaterEqual(len(points), 8)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertEqual((min(xs), max(xs)), (-20 * MM, 20 * MM), "4 的倍数采样 ⇒ bbox 逐纳米精确")
        self.assertEqual((min(ys), max(ys)), (-10 * MM, 10 * MM))
        self.assertEqual(len(_entities(sk)), 1, "一个逼近实体，不许炸成 N 条")

    def test_ellipse_aliases_and_rejections_leave_sketch_untouched(self):
        sk = _fresh()
        (ref,) = ops.ellipse_create(sk, center=(1 * MM, 2 * MM), major_nm=5 * MM, minor_nm=3 * MM)
        self.assertEqual(len(_points(_by_uuid(sk, ref))), 257, "256 采样 + 首点重复闭合")
        self.assert_cad_error("invalid_argument", ops.ellipse_create, sk, center=(0, 0),
                              center_nm=(0, 0), rx_nm=5 * MM, ry_nm=3 * MM)
        self.assert_cad_error("invalid_argument", ops.ellipse_create, sk, center_nm=(0, 0),
                              rx_nm=5 * MM, ry_nm=3 * MM, samples=10)
        self.assert_cad_error("invalid_length", ops.ellipse_create, sk, center_nm=(0, 0),
                              rx_nm=0, ry_nm=3 * MM)
        self.assertEqual(len(_entities(sk)), 1, "失败的调用不许留下几何")

    def test_spline_interpolates_every_fit_point(self):
        sk = _fresh()
        fit = [(0, 0), (10 * MM, 10 * MM), (20 * MM, -5 * MM), (30 * MM, 5 * MM)]
        (ref,) = ops.spline_create(sk, points=fit)
        self.assertEqual(_kind(_by_uuid(sk, ref)), "polyline")
        points = _points(_by_uuid(sk, ref))
        self.assertGreater(len(points), len(fit), "段内采样必须加密")
        for point in fit:
            self.assertIn(point, points, "Catmull-Rom 必须逐纳米过每个拟合点")
        self.assert_cad_error("invalid_argument", ops.spline_create, sk, points=fit[:1])
        self.assertEqual(len(_entities(sk)), 1)

    def test_xline_point_and_hatch_are_real_entities(self):
        sk = _fresh()
        (xline,) = ops.xline_create(sk, origin_nm=(1 * MM, 2 * MM), angle_deg=30.0)
        x_entity = _by_uuid(sk, xline)
        self.assertEqual(_kind(x_entity), "xline")
        self.assertTrue(_get(x_entity, "construction"), "构造线是 construction 实体")
        self.assertTrue(self._geom(x_entity)["infinite"], "无限长：ray_len 只是方向载体")
        (point,) = ops.point_create(sk, point_nm=(3 * MM, 4 * MM), pdmode=35, pdsize_nm=2 * MM)
        p_geom = self._geom(_by_uuid(sk, point))
        self.assertEqual((p_geom["x"], p_geom["y"]), (3 * MM, 4 * MM))
        self.assertEqual(p_geom["pdmode"], 35)
        (hatch,) = ops.hatch_create(sk, boundary_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM),
                                                     (0, 10 * MM)],
                                    pattern="ANSI31", pitch_nm=1 * MM, angle_deg=0.0)
        h_geom = self._geom(_by_uuid(sk, hatch))
        self.assertEqual(h_geom["area_nm2"], 100 * MM * MM, "面积现算（鞋带公式）")
        self.assertGreater(h_geom["line_count"], 0, "图案填充必须真扫掠出线段")
        for xa, ya, xb, yb in h_geom["lines"]:
            for x, y in ((xa, ya), (xb, yb)):
                self.assertTrue(0 <= x <= 10 * MM and 0 <= y <= 10 * MM, "线段必须被边界夹住")
        self.assertEqual(len(_entities(sk)), 3)

    def test_otrack_locks_cursor_onto_alignment_path(self):
        sk = _fresh()
        (mark,) = ops.otrack(sk, base_nm=(0, 0), point_nm=(10 * MM, 150_000))
        geom = self._geom(_by_uuid(sk, mark))
        self.assertTrue(geom["tracked"], "0.86° 离轴点必须被正交对齐路径锁住")
        self.assertEqual((geom["x"], geom["y"]), (10 * MM, 0))
        self.assertEqual(ops.otrack(sk, base_nm=(0, 0), point_nm=(10 * MM, 150_000), mark=False), [],
                         "mark=False = 只算不落库（真为零，不是没算）")
        self.assert_cad_error("invalid_argument", ops.otrack, sk, point_nm=(0, 0))
        self.assertEqual(len(_entities(sk)), 1)

    def test_layer_apply_is_entity_attribute_edit_in_place(self):
        sk = _fresh()
        (ref,) = ops.line_create(sk, p1_nm=(0, 0), p2_nm=(1 * MM, 0))
        out = ops.layer_apply(sk, refs=[ref], layer="WALLS", color=7, linetype="DASHED",
                              lineweight_nm=350_000)
        self.assertEqual(out, [ref], "原地改写型：返回实体自身 uuid")
        entity = _by_uuid(sk, ref)
        self.assertEqual(_get(entity, "layer"), "WALLS", "L1 图层是实体属性")
        self.assertEqual(_get(entity, "color"), 7)
        self.assertEqual(_get(entity, "linetype"), "DASHED")
        self.assertEqual(_get(entity, "lineweight_nm"), 350_000)
        self.assert_cad_error("invalid_argument", ops.layer_apply, sk, refs=[ref])
        self.assert_cad_error("unknown_reference", ops.layer_apply, sk, refs=["nope"], layer="X")
        self.assertEqual(_get(_by_uuid(sk, ref), "layer"), "WALLS", "坏 ref ⇒ 一个字段都不落")


class TestFamilyContract(CadErrorAssertions):
    """冻结接口：FAMILY / OPERATIONS / 第一位置参数名 `sk_or_doc` / 其余皆关键字。"""

    def test_family_and_operation_names(self):
        self.assertEqual(ops.FAMILY, FAMILY_ID)
        self.assertIsInstance(ops.OPERATIONS, dict)
        self.assertEqual(tuple(sorted(ops.OPERATIONS)), tuple(sorted(OP_NAMES)))
        for name, fn in ops.OPERATIONS.items():
            self.assertTrue(callable(fn), name)

    def test_first_positional_param_is_sk_or_doc_and_rest_keyword_only(self):
        for name, fn in ops.OPERATIONS.items():
            params = list(inspect.signature(fn).parameters.values())
            self.assertTrue(params, name)
            first = params[0]
            self.assertEqual(first.name, "sk_or_doc", name)
            self.assertIn(first.kind,
                          (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD),
                          name)
            for param in params[1:]:
                self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY,
                                 "%s.%s must be keyword-only" % (name, param.name))

    def test_module_facts_matches_frozen_interface(self):
        facts = ops.module_facts()
        self.assertEqual(facts["family"], FAMILY_ID)
        self.assertEqual(facts["op_count"], len(OP_NAMES))
        self.assertEqual(sorted(facts["operations"]), sorted(OP_NAMES))
        self.assertEqual(facts["length_unit"], "nm")
        # 2026-09-30 本席补 layer_apply（原地写实体属性）⇒ 原地改写型现在是两个，断言照旧精确比等。
        self.assertEqual(facts["inplace_ops"], ["polyline_edit", "layer_apply"])
        self.assertEqual(sorted(facts["returns_new_uuids"]),
                         sorted(n for n in OP_NAMES if n not in ("polyline_edit", "layer_apply")))

    def test_selftest_is_green(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            rc = ops.selftest()
        verdict = [line for line in buffer.getvalue().splitlines() if "verdict=" in line]
        self.assertEqual(rc, 0, buffer.getvalue())
        self.assertTrue(verdict and "verdict=PASS" in verdict[-1], buffer.getvalue())
        self.assertIn("ops=%d" % len(OP_NAMES), verdict[-1])


class TestSourceHygiene(unittest.TestCase):
    """族模块不许出现 locale 字面量；尺子带阳性对照（证明它看得见红）。"""

    def test_no_locale_literals_with_positive_control(self):
        source = _SOURCE.read_text(encoding="utf-8")
        hits = _LOCALE_RULER.findall(source)
        self.assertEqual(hits, [], "locale literals found in %s: %r" % (_SOURCE, hits))
        # 阳性对照：同一条尺子喂一行突变体必须命中 —— 否则上面那个 0 是"尺子瞎了"。
        mutant = 'TITLE = "zh-CN"\n'
        self.assertEqual(_LOCALE_RULER.findall(mutant), ["zh-CN"],
                         "ruler is blind: it must flag a locale literal")


class TestPolyline(CadErrorAssertions):
    """折线创建 / 原地编辑：uuid 恒为 1，返回 `[ref]`，越界与坏几何一律拒且不半接受。"""

    def test_create_writes_exact_integer_nm_points(self):
        sk = _fresh()
        uuids = ops.polyline_create(sk, points_nm=[(0, 0), (10 * MM, 0), (10 * MM, 5 * MM)], layer="0")
        self.assertEqual(len(uuids), 1)
        entity = _by_uuid(sk, uuids[0])
        self.assertIsNotNone(entity)
        self.assertEqual(_kind(entity), "polyline")
        self.assertEqual(_points(entity), [(0, 0), (10 * MM, 0), (10 * MM, 5 * MM)])
        self.assertEqual(len(_entities(sk)), 1)

    def test_create_rejects_bad_input_and_leaves_sketch_empty(self):
        for bad_points in ([], [(0, 0)], [(0, 0), (0, 0)], [(0, 0), (1.5, 0)]):
            sk = _fresh()
            self.assert_cad_error_and_frozen(
                sk, ops.polyline_create, sk, points_nm=bad_points, layer="0")
            self.assertEqual(_entities(sk), [])
        sk = _fresh()
        self.assert_cad_error("invalid_argument", ops.polyline_create, sk,
                              points_nm=[(0, 0), (MM, 0), (MM, MM)], closed=1, layer="0")
        self.assert_cad_error("invalid_argument", ops.polyline_create, sk,
                              points_nm=[(0, 0), (MM, 0)], closed=True, layer="0")

    def test_edit_move_insert_delete_and_set_closed(self):
        sk = _fresh()
        ref = ops.polyline_create(sk, points_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)], layer="0")[0]

        moved = ops.polyline_edit(sk, ref=ref, action="move_vertex", index=0, to_nm=(0, MM))
        self.assertEqual(moved, [ref], "in-place edit must return the edited entity uuid")
        self.assertEqual(_points(_by_uuid(sk, ref)), [(0, MM), (10 * MM, 0), (10 * MM, 10 * MM)])

        ops.polyline_edit(sk, ref=ref, action="insert_vertex", index=1, point_nm=(5 * MM, 5 * MM))
        self.assertEqual(_points(_by_uuid(sk, ref)),
                         [(0, MM), (5 * MM, 5 * MM), (10 * MM, 0), (10 * MM, 10 * MM)])

        ops.polyline_edit(sk, ref=ref, action="delete_vertex", index=1)
        self.assertEqual(_points(_by_uuid(sk, ref)), [(0, MM), (10 * MM, 0), (10 * MM, 10 * MM)])

        ops.polyline_edit(sk, ref=ref, action="set_closed", closed=True)
        closed = _points(_by_uuid(sk, ref))
        self.assertEqual(len(closed), 4)
        self.assertEqual(closed[0], closed[-1])
        self.assertEqual(len(_entities(sk)), 1, "closure must not create a new entity")

        ops.polyline_edit(sk, ref=ref, action="set_closed", closed=False)
        self.assertEqual(_points(_by_uuid(sk, ref)), [(0, MM), (10 * MM, 0), (10 * MM, 10 * MM)])

    def test_closed_ring_index_zero_moves_both_stored_ends(self):
        sk = _fresh()
        ref = ops.polyline_create(sk, points_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)],
                                  closed=True, layer="0")[0]
        self.assertEqual(len(_points(_by_uuid(sk, ref))), 4)
        ops.polyline_edit(sk, ref=ref, action="move_vertex", index=0, to_nm=(-MM, -MM))
        points = _points(_by_uuid(sk, ref))
        self.assertEqual(points[0], (-MM, -MM))
        self.assertEqual(points[-1], (-MM, -MM), "stored closure point must follow vertex 0")

    def test_edit_errors_are_reasoned_and_do_not_half_accept(self):
        sk = _fresh()
        ref = ops.polyline_create(sk, points_nm=[(0, 0), (10 * MM, 0), (10 * MM, 10 * MM)], layer="0")[0]
        self.assert_cad_error("invalid_argument", ops.polyline_edit, sk,
                              ref=ref, action="move_vertex", index=3, to_nm=(0, 0))
        self.assert_cad_error("invalid_argument", ops.polyline_edit, sk,
                              ref=ref, action="explode", index=0)
        # 零长相邻段：把 0 号顶点挪到 1 号顶点上（坏几何必须被拒，草图不动）。
        self.assert_cad_error_and_frozen(sk, ops.polyline_edit, sk,
                                         ref=ref, action="move_vertex", index=0, to_nm=(10 * MM, 0))
        # 删到少于最少点数：2 点开放折线删 1 个只剩 1 点（必须被拒，草图不动）。
        short_sk = _fresh()
        short_ref = ops.polyline_create(short_sk, points_nm=[(0, 0), (MM, 0)], layer="0")[0]
        self.assert_cad_error_and_frozen(short_sk, ops.polyline_edit, short_sk,
                                         ref=short_ref, action="delete_vertex", index=0)
        self.assertEqual(len(_entities(sk)), 1)
        self.assertEqual(_points(_by_uuid(short_sk, short_ref)), [(0, 0), (MM, 0)])

    def test_editing_a_plain_line_is_refused_with_unsupported_geometry(self):
        sk = _fresh()
        line = _base_line(sk)
        self.assert_cad_error("unsupported_geometry", ops.polyline_edit, sk,
                              ref=line, action="move_vertex", index=0, to_nm=(MM, MM))
        self.assertEqual(len(_entities(sk)), 1)


class TestRectangle(CadErrorAssertions):
    """参数化矩形：轴对齐 = 4 line + 4 约束 + 1 闭环；斜置 = 1 条闭合 polyline。"""

    def test_axis_aligned_center_mode_is_four_lines_with_one_closed_profile(self):
        sk = _fresh()
        uuids = ops.rectangle_params(sk, center_nm=(0, 0), width_nm=40 * MM, height_nm=20 * MM,
                                     layer="0")
        self.assertEqual(len(uuids), 4)
        entities = _entities(sk)
        self.assertEqual(len(entities), 4)
        self.assertEqual({_kind(e) for e in entities}, {"line"})
        self.assertEqual(len(cons.sketch_constraints(sk)), 4)
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)
        x0, y0, x1, y1 = -20 * MM, -10 * MM, 20 * MM, 10 * MM
        self.assertEqual({_line_segment(e) for e in entities},
                         {frozenset({(x0, y0), (x1, y0)}), frozenset({(x1, y0), (x1, y1)}),
                          frozenset({(x1, y1), (x0, y1)}), frozenset({(x0, y1), (x0, y0)})})

    def test_diagonal_mode_uses_the_two_given_corners_exactly(self):
        sk = _fresh()
        uuids = ops.rectangle_params(sk, p1_nm=(MM, 2 * MM), p2_nm=(31 * MM, 22 * MM), layer="0")
        self.assertEqual(len(uuids), 4)
        x0, y0, x1, y1 = MM, 2 * MM, 31 * MM, 22 * MM
        self.assertEqual({_line_segment(e) for e in _entities(sk)},
                         {frozenset({(x0, y0), (x1, y0)}), frozenset({(x1, y0), (x1, y1)}),
                          frozenset({(x1, y1), (x0, y1)}), frozenset({(x0, y1), (x0, y0)})})
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)

    def test_rotated_rectangle_lands_on_one_closed_polyline_with_exact_corners(self):
        sk = _fresh()
        uuids = ops.rectangle_params(sk, center_nm=(0, 0), width_nm=40 * MM, height_nm=20 * MM,
                                     angle_deg=30.0, layer="0")
        self.assertEqual(len(uuids), 1)
        entity = _by_uuid(sk, uuids[0])
        self.assertEqual(_kind(entity), "polyline")
        points = _points(entity)
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0], points[-1])
        self.assertEqual(len(set(points[:-1])), 4)
        import math

        radians = math.radians(30.0)
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        expected = []
        for dx, dy in ((-20 * MM, -10 * MM), (20 * MM, -10 * MM), (20 * MM, 10 * MM), (-20 * MM, 10 * MM)):
            expected.append((int(round(dx * cos_a - dy * sin_a)), int(round(dx * sin_a + dy * cos_a))))
        self.assertEqual(points, expected + [expected[0]])
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)

    def test_rectangle_rejects_mixed_modes_and_degenerate_inputs(self):
        sk = _fresh()
        self.assert_cad_error("invalid_argument", ops.rectangle_params, sk,
                              p1_nm=(0, 0), p2_nm=(MM, MM), center_nm=(0, 0), width_nm=MM,
                              height_nm=MM)
        self.assert_cad_error("invalid_argument", ops.rectangle_params, sk,
                              p1_nm=(0, 0), p2_nm=(MM, MM), angle_deg=30.0)
        self.assert_cad_error("degenerate_rectangle", ops.rectangle_params, sk,
                              p1_nm=(0, 0), p2_nm=(0, MM))
        self.assert_cad_error("invalid_argument", ops.rectangle_params, sk, center_nm=(0, 0),
                              width_nm=MM)
        self.assert_cad_error_and_frozen(sk, ops.rectangle_params, sk,
                                         center_nm=(0, 0), width_nm=MM, height_nm=MM, layer="")
        self.assertEqual(_entities(sk), [])


class TestPolygonAndSlot(CadErrorAssertions):
    """正多边形三种尺寸口径 + 腰形槽两模式：条数 / 精确几何 / 闭环。"""

    def test_polygon_edge_spec_gives_six_lines_on_one_circle(self):
        sk = _fresh()
        uuids = ops.polygon_params(sk, sides=6, edge_nm=10 * MM, center_nm=(0, 0), layer="0")
        self.assertEqual(len(uuids), 6)
        self.assertEqual(len(_entities(sk)), 6)
        self.assertEqual(len(cons.sketch_constraints(sk)), 6)
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)
        radius = int(round((10 * MM) / (2.0 * math.sin(math.pi / 6))))
        self.assertEqual(radius, 10 * MM, "hexagon edge == circumradius")
        vertices = [point for entity in _entities(sk) for point in _line_points(entity)]
        self.assertEqual(len(set(vertices)), 6, "six distinct vertices")
        for x, y in set(vertices):
            # 顶点坐标各自取整到整数纳米 ⇒ 半径偏差 ≤1 nm（这不是放宽断言：是 nm 分辨率的物理上界，
            # 断言"必须落在圆上±1nm"仍能抓住换算口径写错，比如把外接圆当内切圆）。
            self.assertLessEqual(abs(math.hypot(x, y) - radius), 1.0,
                                 "vertex must sit on the circumcircle within 1 nm rounding")

    def test_polygon_across_flats_spec_is_exact_for_a_square(self):
        sk = _fresh()
        uuids = ops.polygon_params(sk, sides=4, across_flats_nm=20 * MM, center_nm=(0, 0), layer="0")
        self.assertEqual(len(uuids), 4)
        vertices = set(point for entity in _entities(sk) for point in _line_points(entity))
        radius = int(round((10 * MM) / math.cos(math.pi / 4)))
        self.assertEqual(vertices, {(0, radius), (-radius, 0), (0, -radius), (radius, 0)})

    def test_polygon_errors(self):
        sk = _fresh()
        self.assert_cad_error("invalid_argument", ops.polygon_params, sk, sides=2, radius_nm=MM)
        self.assert_cad_error("invalid_argument", ops.polygon_params, sk, sides=6,
                              radius_nm=MM, edge_nm=MM)
        self.assert_cad_error("invalid_argument", ops.polygon_params, sk, sides=6)
        self.assert_cad_error_and_frozen(sk, ops.polygon_params, sk, sides=True, radius_nm=MM)
        self.assertEqual(_entities(sk), [])

    def test_slot_axis_mode_is_four_edges_around_the_axis(self):
        sk = _fresh()
        uuids = ops.slot_params(sk, width_nm=10 * MM, p1_nm=(0, 0), p2_nm=(30 * MM, 0), layer="0")
        self.assertEqual(len(uuids), 4)
        entities = _entities(sk)
        lines = [e for e in entities if _kind(e) == "line"]
        arcs = [e for e in entities if _kind(e) == "arc"]
        self.assertEqual((len(lines), len(arcs)), (2, 2))
        self.assertEqual({_line_segment(e) for e in lines},
                         {frozenset({(0, 5 * MM), (30 * MM, 5 * MM)}),
                          frozenset({(0, -5 * MM), (30 * MM, -5 * MM)})})
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)

    def test_slot_center_mode_rounds_the_axis_endpoints_to_nm(self):
        sk = _fresh()
        ops.slot_params(sk, width_nm=10 * MM, center_nm=(0, 0), length_nm=30 * MM, angle_deg=90.0,
                        layer="0")
        lines = [e for e in _entities(sk) if _kind(e) == "line"]
        self.assertEqual(len(lines), 2)
        self.assertEqual({_line_segment(e) for e in lines},
                         {frozenset({(-5 * MM, -15 * MM), (-5 * MM, 15 * MM)}),
                          frozenset({(5 * MM, -15 * MM), (5 * MM, 15 * MM)})})
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)

    def test_closure_ruler_sees_red_on_a_1nm_perturbation(self):
        sk = _fresh()
        ops.slot_params(sk, width_nm=10 * MM, p1_nm=(0, 0), p2_nm=(30 * MM, 0), layer="0")
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 1)
        line = [e for e in _entities(sk) if _kind(e) == "line"][0]
        geom = _get(line, "geom")
        geom["x2"] = int(_get(geom, "x2")) + 1  # 1 nm 的缝：闭环必须碎
        self.assertEqual(prim.closed_profiles(sk, tol_nm=0), [],
                         "closure ruler must see a 1 nm gap (negative control)")

    def test_slot_errors(self):
        sk = _fresh()
        self.assert_cad_error("invalid_length", ops.slot_params, sk, width_nm=10 * MM + 1,
                              p1_nm=(0, 0), p2_nm=(30 * MM, 0))
        self.assert_cad_error("degenerate_slot", ops.slot_params, sk, width_nm=10 * MM,
                              p1_nm=(MM, MM), p2_nm=(MM, MM))
        self.assert_cad_error("invalid_argument", ops.slot_params, sk, width_nm=10 * MM,
                              p1_nm=(0, 0), p2_nm=(30 * MM, 0), center_nm=(0, 0), length_nm=MM)
        self.assert_cad_error_and_frozen(sk, ops.slot_params, sk, width_nm=0,
                                         p1_nm=(0, 0), p2_nm=(30 * MM, 0))
        self.assertEqual(_entities(sk), [])


class TestArrays(CadErrorAssertions):
    """阵列：矩形阵列转发真源；极坐标阵列 90° 精确、朝向保持靠 move；真为零与没算分开。"""

    def test_array_rect_places_rows_cols_minus_one_copies_at_exact_offsets(self):
        sk = _fresh()
        base = _base_line(sk)
        dx, dy = MM, 2 * MM
        uuids = ops.array_rect(sk, refs=[base], rows=2, cols=3, dx_nm=dx, dy_nm=dy)
        self.assertEqual(len(uuids), 2 * 3 - 1)
        self.assertEqual(len(set(uuids)), len(uuids), "each copy gets its own uuid")
        self.assertNotIn(base, uuids)
        for uuid_ in uuids:
            self.assertIsNotNone(_by_uuid(sk, uuid_), "returned uuid must resolve in the sketch")
        self.assertEqual(len(_entities(sk)), 6)
        # 源实体本身留在草图里（原位 (0,0)），新副本 = rows*cols-1 个非原位偏移。
        expected = {frozenset({(0, 0), (10 * MM, 0)})}
        for row in range(2):
            for col in range(3):
                if row == 0 and col == 0:
                    continue
                expected.add(frozenset({(col * dx, row * dy), (10 * MM + col * dx, row * dy)}))
        self.assertEqual({_line_segment(e) for e in _entities(sk)}, expected)
        self.assertEqual(len(prim.closed_profiles(sk, tol_nm=0)), 0, "open lines are not a profile")

    def test_array_rect_one_by_one_is_true_zero_not_uncomputed(self):
        sk = _fresh()
        base = _base_line(sk)
        before = _snapshot(sk)
        self.assertEqual(ops.array_rect(sk, refs=[base], rows=1, cols=1, dx_nm=0, dy_nm=0), [])
        self.assertEqual(_snapshot(sk), before, "true-zero array must not touch the sketch")
        # 同一条腿继续跑（真算了一次）：证明上一个 [] 是"确实没有新对象"，不是"没跑"。
        grown = ops.array_rect(sk, refs=[base], rows=1, cols=2, dx_nm=MM, dy_nm=0)
        self.assertEqual(len(grown), 1, "the same call with cols=2 must produce one new uuid")
        self.assertEqual(len(_entities(sk)), 2)

    def test_array_rect_errors(self):
        sk = _fresh()
        base = _base_line(sk)
        self.assert_cad_error("invalid_argument", ops.array_rect, sk, refs=[base], rows=2, cols=1,
                              dx_nm=0, dy_nm=0)
        self.assert_cad_error("empty_selection", ops.array_rect, sk, refs=base, rows=2, cols=1,
                              dx_nm=MM, dy_nm=0)
        self.assert_cad_error("empty_selection", ops.array_rect, sk, refs=[], rows=2, cols=1,
                              dx_nm=MM, dy_nm=0)
        self.assert_cad_error_and_frozen(sk, ops.array_rect, sk, refs=[base], rows=0, cols=1,
                                         dx_nm=MM, dy_nm=0)
        self.assertEqual(len(_entities(sk)), 1)

    def test_array_polar_quarter_turns_are_exact_and_count_one_is_true_zero(self):
        sk = _fresh()
        base = _base_line(sk)
        self.assertEqual(ops.array_polar(sk, refs=[base], center_nm=(0, 0), count=1), [])
        self.assertEqual(len(_entities(sk)), 1, "count=1 must add nothing")
        uuids = ops.array_polar(sk, refs=[base], center_nm=(0, 0), count=4)
        self.assertEqual(len(uuids), 3)
        first = _by_uuid(sk, uuids[0])
        self.assertEqual(_line_points(first), ((0, 0), (0, 10 * MM)), "90 deg rotation is exact")
        rotated = {_line_segment(_by_uuid(sk, uuid_)) for uuid_ in uuids}
        self.assertEqual(rotated, {frozenset({(0, 0), (0, 10 * MM)}),
                                   frozenset({(0, 0), (-10 * MM, 0)}),
                                   frozenset({(0, 0), (0, -10 * MM)})})

    def test_array_polar_keep_orientation_moves_without_rotating(self):
        sk = _fresh()
        base = _base_line(sk)
        uuids = ops.array_polar(sk, refs=[base], center_nm=(0, 0), count=2, total_angle_deg=180.0,
                                rotate_items=False)
        self.assertEqual(len(uuids), 1)
        self.assertEqual(_line_points(_by_uuid(sk, uuids[0])), ((-10 * MM, 0), (0, 0)),
                         "orientation preserved, only the anchor lands on the far side")

    def test_array_polar_errors(self):
        sk = _fresh()
        base = _base_line(sk)
        self.assert_cad_error("invalid_argument", ops.array_polar, sk, refs=[base], center_nm=(0, 0),
                              count=0)
        self.assert_cad_error("invalid_argument", ops.array_polar, sk, refs=[base], center_nm=(0, 0),
                              count=3, total_angle_deg=0.0)
        self.assert_cad_error("invalid_argument", ops.array_polar, sk, refs=[base], center_nm=(0, 0),
                              count=3, total_angle_deg=400.0)
        self.assert_cad_error("invalid_argument", ops.array_polar, sk, refs=[base], center_nm=(0, 0),
                              count=3, rotate_items=1)
        self.assertEqual(len(_entities(sk)), 1)


import math  # noqa: E402  (放在文件尾部避免遮蔽上面的用例阅读顺序)

if __name__ == "__main__":
    unittest.main()
