"""test_edit_ops_ops —— 族 `edit_ops` 的独立单测（数值硬断言 / 边界 / 与已有命令组合）。

落盘时的状态（必须照实写，不许把没做的写成做了）
------------------------------------------------
本文件写盘时 **`cadcore/ops/` 整个包还不存在**（`cadcore/ops/edit_ops.py` 与
`ops/__init__.py` 均由收尾席统一创建）⇒ 没有"真件"可打。本文件因此：

1. **按冻结接口写**：`cadcore/ops/<module>.py` 暴露 `FAMILY="<id>"` /
   `OPERATIONS: dict[str, Callable]` / 每个操作 `op(sk_or_doc, **kwargs) -> list[str]`（返回 uuid 列表）；
   长度一律整数纳米；错误抛 `cadcore.errors.CadError(reason=...)`。
2. **真件优先、缺席才用双**：`importlib.util.find_spec` 先判 `cadcore.ops.edit_ops` **在不在**；
   - 在 ⇒ 直接 import 真件（**若真件存在却 import 失败，当场报错，绝不回落双**：那会把"真件坏了"伪装成"没落盘"）；
   - 不在 ⇒ 用 `cadcore.edit.modify`（冻结的复用目标）作**薄封装双**：`OPERATIONS` 的每个值
     **就是** `modify` 里的同名函数对象本身（`is` 断言，见 `TestFamilyContract`），本文件**不重写任何几何**。
   于是本批用例今天打的是 `modify` 的真几何，真件落盘后同一批用例**自动改打真件**，且多一条 provenance 断言。
3. **本文件编码的接缝假设**（真件与冻结接口不一致时应当红，而不是被猜着改绿）：操作名与关键字名
   一律照 `cadcore.edit.modify` 的公开签名 —— `refs` / `dx_nm` / `dy_nm` / `center_nm` / `angle_deg` /
   `factor` / `p1_nm` / `p2_nm` / `keep_original` / `ref` / `distance_nm` / `side` / `target_ref` /
   `cutter_refs` / `keep` / `boundary_refs` / `end` / `ref_a` / `ref_b` / `radius_nm` / `rows` / `cols`。

口径
----
- 断言一律打在**落库后的文档**上（uuid → 实体），不只看返回值；坐标/长度/角度/数量全部给具体数字。
- 真为零（`array_linear(rows=1, cols=1)` 返回 `[]`）与"没算"分开记，不静默当空。
- 闭合性用 `cadcore.sketch.constraints.closed_profiles(tol_nm=0)` 这把尺子量，并带**阴性对照**
  （故意把一条边挪 1 nm ⇒ 尺子必须变红），证明尺子看得见红。
- 非 docstring 的字符串字面量一律 ASCII（与包内 i18n 口径一致），中文只出现在 docstring/注释里。
"""

from __future__ import annotations

import copy
import importlib.util
import unittest

from cadcore.edit import modify as edit_modify
from cadcore.errors import CadError
from cadcore.sketch import constraints as cons
from cadcore.sketch import primitives as prim

#: 本族应有的操作名（= `cadcore.edit.modify` 的公开命令集）。
OP_NAMES = ("move", "copy", "rotate", "scale", "mirror", "offset",
            "trim", "extend", "fillet", "chamfer", "array_linear")

#: 本族 id（冻结接口里的 `FAMILY`）。
FAMILY_ID = "edit_ops"

#: 真件模块名。
REAL_MODULE = "cadcore.ops.edit_ops"


def _spec_exists(name: str) -> bool:
    """模块**真的在盘上**吗（不是"import 失败"）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _build_double():
    """缺席时的薄封装双：只包 `FAMILY` / `OPERATIONS`，每个值是 `modify` 的原函数对象。"""
    return {"FAMILY": FAMILY_ID, "OPERATIONS": {name: getattr(edit_modify, name) for name in OP_NAMES}}


class _FamilyDouble:
    """`cadcore/ops/edit_ops.py` 未落盘时顶替它的**接口壳**（几何全部来自 `cadcore.edit.modify`）。"""

    def __init__(self) -> None:
        payload = _build_double()
        self.FAMILY = payload["FAMILY"]
        self.OPERATIONS = payload["OPERATIONS"]


DOUBLE_FALLBACK = not _spec_exists(REAL_MODULE)
if DOUBLE_FALLBACK:
    FAMILY = _FamilyDouble()
    FAMILY_SOURCE = "DOUBLE:cadcore.edit.modify (because %s is not on disk)" % REAL_MODULE
else:  # 真件存在：import 失败一律向上抛，不许被上面的分支吞掉
    from cadcore.ops import edit_ops as FAMILY  # type: ignore[no-redef]

    FAMILY_SOURCE = "REAL:" + REAL_MODULE

OPS = FAMILY.OPERATIONS


# --------------------------------------------------------------------------------------
# 夹具（全部走冻结的 L1 结构层：entities / constraints / dimensions）
# --------------------------------------------------------------------------------------


def new_sketch() -> dict:
    """一张空草图（契约结构：entities / constraints / dimensions）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def add_line(sk, p1, p2):
    """一条 `line`（走已有命令 `cadcore.sketch.primitives.add_line`）。"""
    return prim.add_line(sk, p1=p1, p2=p2)


def add_raw(sk, kind, geom):
    """直接落一个实体（circle/arc 用；uuid 走 L1 真源 `constraints.new_uuid`）。"""
    uuid_ = cons.new_uuid()
    sk["entities"].append({"uuid": uuid_, "kind": kind, "layer": "0", "construction": False, "geom": geom})
    return uuid_


def add_circle(sk, cx, cy, r):
    return add_raw(sk, "circle", {"cx": cx, "cy": cy, "r": r})


def geom(sk, uuid_):
    return cons.find_entity(sk, uuid_)["geom"]


def kind_of(sk, uuid_):
    return cons.find_entity(sk, uuid_)["kind"]


def line_fields(sk, uuid_):
    """line 的四个几何字段，按下标顺序 `(x1, y1, x2, y2)`。"""
    g = geom(sk, uuid_)
    return (g["x1"], g["y1"], g["x2"], g["y2"])


def endpoints(sk, uuid_):
    g = geom(sk, uuid_)
    return ((g["x1"], g["y1"]), (g["x2"], g["y2"]))


def loops(sk):
    """闭合轮廓（整数纳米严格相等口径：`tol_nm=0`）。"""
    return cons.closed_profiles(sk, tol_nm=0.0)


class ReasonMixin:
    """边界断言：错误必须是 `cadcore.errors.CadError`，且 `reason` 精确相等。"""

    def reason_of(self, callable_, **kwargs) -> str:
        #: 冻结接口：第一个位置参数是 `sk_or_doc`（其余才走关键字）。
        #: 调用点写的是 `sk=sk`，必须在此处**按位置**转发，否则 `sk` 落进 `**kwargs`、
        #: `sk_or_doc` 缺位 ⇒ TypeError（那不是被测实现的缺陷，是调用方少传参数）。
        sk_or_doc = kwargs.pop("sk")
        with self.assertRaises(CadError) as ctx:
            callable_(sk_or_doc, **kwargs)
        self.assertIsInstance(ctx.exception, CadError)
        self.assertTrue(ctx.exception.reason, "reason must be a non-empty machine-readable string")
        return ctx.exception.reason


# --------------------------------------------------------------------------------------
# 1) 接口契约 + 夹具自证
# --------------------------------------------------------------------------------------


class TestFamilyContract(ReasonMixin, unittest.TestCase):
    def test_family_id_and_operation_map(self):
        self.assertEqual(FAMILY.FAMILY, FAMILY_ID)
        self.assertIsInstance(OPS, dict)
        self.assertTrue(OPS, "OPERATIONS must not be empty")
        missing = [name for name in OP_NAMES if name not in OPS]
        self.assertEqual(missing, [], "missing operations in OPERATIONS: %r (actual keys=%r)"
                         % (missing, sorted(OPS)))
        for name, fn in OPS.items():
            self.assertTrue(callable(fn), "%s is not callable" % name)
            self.assertIsInstance(name, str)

    def test_fixture_provenance_is_reported_and_not_masking_a_broken_real_module(self):
        """夹具自证：双只在"真件不在盘上"时使用；双里的几何必须是 `modify` 的原函数对象。"""
        self.assertTrue(FAMILY_SOURCE.startswith("REAL:") or FAMILY_SOURCE.startswith("DOUBLE:"))
        self.assertEqual(DOUBLE_FALLBACK, not _spec_exists(REAL_MODULE),
                         "fallback decision must follow module existence: %s" % FAMILY_SOURCE)
        if DOUBLE_FALLBACK:
            for name in OP_NAMES:
                self.assertIs(OPS[name], getattr(edit_modify, name),
                              "%s must be the real cadcore.edit.modify function, not a reimplementation" % name)
        else:
            self.assertEqual(FAMILY_SOURCE, "REAL:" + REAL_MODULE)
            self.assertIs(FAMILY.OPERATIONS, OPS)

    def test_closure_ruler_sees_red_when_a_rectangle_edge_moves_one_nanometre(self):
        """阴性对照：`closed_profiles(tol_nm=0)` 这把尺子必须看得见 1 nm 的缝。"""
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 500))
        self.assertEqual(len(loops(sk)), 1)
        self.assertEqual(set(loops(sk)[0]), set(rect))
        moved = OPS["move"](sk, refs=[rect[1]], dx_nm=1, dy_nm=0)
        self.assertEqual(moved, [rect[1]])
        self.assertEqual(loops(sk), [])  # 尺子看得见 1 nm 的红
        self.assertEqual(len(sk["entities"]), 4)  # 真为零个新实体（不是"没算"）

    def test_every_operation_returns_uuid_list_and_uuids_resolve(self):
        for name, sk, kwargs in self._smoke_cases():
            with self.subTest(operation=name):
                result = OPS[name](sk, **kwargs)
                self.assertIsInstance(result, list, "%s must return a list" % name)
                self.assertTrue(result, "%s returned no uuid (a real zero must be its own case)" % name)
                self.assertEqual(len(set(result)), len(result), "%s returned duplicate uuids" % name)
                for uuid_ in result:
                    self.assertIsInstance(uuid_, str)
                    self.assertTrue(uuid_)
                    cons.find_entity(sk, uuid_)  # 悬空 uuid 会当场抛 UnknownReference

    @staticmethod
    def _smoke_cases():
        cases = []

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("move", sk, {"refs": [line], "dx_nm": 10, "dy_nm": 20}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("copy", sk, {"refs": [line], "dx_nm": 10, "dy_nm": 20}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("rotate", sk, {"refs": [line], "center_nm": (0, 0), "angle_deg": 90}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("scale", sk, {"refs": [line], "center_nm": (0, 0), "factor": 2}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("mirror", sk, {"refs": [line], "p1_nm": (0, 0), "p2_nm": (0, 1000)}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        cases.append(("offset", sk, {"ref": line, "distance_nm": 100, "side": 1}))

        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        cutter = add_line(sk, (600, -100), (600, 100))
        cases.append(("trim", sk, {"target_ref": target, "cutter_refs": [cutter], "keep": "start"}))

        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        boundary = add_line(sk, (1500, -500), (1500, 500))
        cases.append(("extend", sk, {"target_ref": target, "boundary_refs": [boundary], "end": "end"}))

        sk = new_sketch()
        a = add_line(sk, (0, 0), (1000, 0))
        b = add_line(sk, (0, 0), (0, 1000))
        cases.append(("fillet", sk, {"ref_a": a, "ref_b": b, "radius_nm": 200}))

        sk = new_sketch()
        a = add_line(sk, (0, 0), (1000, 0))
        b = add_line(sk, (0, 0), (0, 1000))
        cases.append(("chamfer", sk, {"ref_a": a, "ref_b": b, "distance_nm": 100}))

        sk = new_sketch()
        line = add_line(sk, (0, 0), (100, 0))
        cases.append(("array_linear", sk, {"refs": [line], "rows": 2, "cols": 2, "dx_nm": 1000, "dy_nm": 1000}))
        return cases


# --------------------------------------------------------------------------------------
# 2) 变换类：坐标/半径/数量的硬数字
# --------------------------------------------------------------------------------------


class TestTransformNumerics(ReasonMixin, unittest.TestCase):
    def test_move_translates_by_exact_nanometres(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 2000))
        result = OPS["move"](sk, refs=[line], dx_nm=250, dy_nm=-500)
        self.assertEqual(result, [line])  # 原地改写 ⇒ 返回原 uuid
        self.assertEqual(line_fields(sk, line), (250, -500, 1250, 1500))
        self.assertEqual(len(sk["entities"]), 1)
        self.assertEqual(sk["constraints"], [])

    def test_copy_creates_new_uuid_and_leaves_source_byte_identical(self):
        sk = new_sketch()
        source = add_line(sk, (0, 0), (1000, 2000))
        before = copy.deepcopy(geom(sk, source))
        result = OPS["copy"](sk, refs=[source], dx_nm=100, dy_nm=200)
        self.assertEqual(len(result), 1)
        clone = result[0]
        self.assertNotEqual(clone, source)
        self.assertEqual(line_fields(sk, clone), (100, 200, 1100, 2200))
        self.assertEqual(geom(sk, source), before)  # 原实体一个字节都不动
        self.assertEqual(len(sk["entities"]), 2)

    def test_rotate_90_about_origin_is_exact(self):
        sk = new_sketch()
        line = add_line(sk, (1000, 0), (2000, 0))
        circle = add_circle(sk, 1000, 0, 500)
        result = OPS["rotate"](sk, refs=[line, circle], center_nm=(0, 0), angle_deg=90)
        self.assertEqual(result, [line, circle])
        self.assertEqual(line_fields(sk, line), (0, 1000, 0, 2000))
        g = geom(sk, circle)
        self.assertEqual((g["cx"], g["cy"], g["r"]), (0, 1000, 500))

    def test_scale_doubles_circle_radius_and_halves_line(self):
        sk = new_sketch()
        circle = add_circle(sk, 1000, 2000, 500)
        line = add_line(sk, (1000, 1000), (2000, 1000))
        result = OPS["scale"](sk, refs=[circle, line], center_nm=(0, 0), factor=2)
        self.assertEqual(result, [circle, line])
        g = geom(sk, circle)
        self.assertEqual((g["cx"], g["cy"], g["r"]), (2000, 4000, 1000))
        scaled = OPS["scale"](sk, refs=[line], center_nm=(0, 0), factor=0.5)
        self.assertEqual(scaled, [line])
        # 口径（裁决）：`factor` 是**乘数**，作用在实体**当前**坐标上（`scale(0.5)` = 所有坐标乘 0.5）。
        # line 此刻已是上一行 `factor=2` 缩过的 (2000,2000)-(4000,2000)，故再乘 0.5 应回到 (1000,1000)-(2000,1000)。
        # 原期望 (500,500,1000,500) 隐含"每次 scale 都对**原始**几何取 factor"（绝对缩放）这另一套语义，
        # 与同一测试里 factor=2 让圆半径翻倍的断言不能并存（那样 0.5 就等于对原几何取 1/4）。
        self.assertEqual(line_fields(sk, line), (1000, 1000, 2000, 1000))

    def test_mirror_in_place_negates_x_exactly(self):
        sk = new_sketch()
        line = add_line(sk, (1000, 0), (3000, 0))
        result = OPS["mirror"](sk, refs=[line], p1_nm=(0, 0), p2_nm=(0, 1000), keep_original=False)
        self.assertEqual(result, [line])
        self.assertEqual(line_fields(sk, line), (-1000, 0, -3000, 0))
        self.assertEqual(len(sk["entities"]), 1)

    def test_mirror_keep_original_adds_one_entity(self):
        sk = new_sketch()
        line = add_line(sk, (1000, 0), (3000, 0))
        result = OPS["mirror"](sk, refs=[line], p1_nm=(0, 0), p2_nm=(0, 1000))
        self.assertEqual(len(result), 1)
        clone = result[0]
        self.assertNotEqual(clone, line)
        self.assertEqual(line_fields(sk, clone), (-1000, 0, -3000, 0))
        self.assertEqual(line_fields(sk, line), (1000, 0, 3000, 0))
        self.assertEqual(len(sk["entities"]), 2)


# --------------------------------------------------------------------------------------
# 3) offset：距离/半径硬数字
# --------------------------------------------------------------------------------------


class TestOffsetNumerics(ReasonMixin, unittest.TestCase):
    def test_offset_line_left_and_right_normal_exact(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        plus = OPS["offset"](sk, ref=line, distance_nm=100, side=1)
        minus = OPS["offset"](sk, ref=line, distance_nm=100, side=-1)
        self.assertEqual(len(plus), 1)
        self.assertEqual(len(minus), 1)
        self.assertNotEqual(plus[0], minus[0])
        self.assertEqual(line_fields(sk, plus[0]), (0, 100, 1000, 100))
        self.assertEqual(line_fields(sk, minus[0]), (0, -100, 1000, -100))
        self.assertEqual(line_fields(sk, line), (0, 0, 1000, 0))  # 原件不动
        self.assertEqual(len(sk["entities"]), 3)

    def test_offset_circle_grows_and_shrinks_radius(self):
        sk = new_sketch()
        circle = add_circle(sk, 0, 0, 1000)
        grown = OPS["offset"](sk, ref=circle, distance_nm=250, side=1)
        shrunk = OPS["offset"](sk, ref=circle, distance_nm=250, side=-1)
        self.assertEqual(geom(sk, grown[0])["r"], 1250)
        self.assertEqual(geom(sk, shrunk[0])["r"], 750)
        g = geom(sk, circle)
        self.assertEqual((g["cx"], g["cy"], g["r"]), (0, 0, 1000))

    def test_offset_inward_collapse_is_degenerate_offset(self):
        sk = new_sketch()
        circle = add_circle(sk, 0, 0, 1000)
        self.assertEqual(self.reason_of(OPS["offset"], sk=sk, ref=circle, distance_nm=1000, side=-1),
                         "degenerate_offset")


# --------------------------------------------------------------------------------------
# 4) trim / extend：端点必须精确落在交点上
# --------------------------------------------------------------------------------------


class TestTrimExtendNumerics(ReasonMixin, unittest.TestCase):
    def test_trim_snaps_end_to_exact_intersection(self):
        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        cutter = add_line(sk, (600, -100), (600, 100))
        result = OPS["trim"](sk, target_ref=target, cutter_refs=[cutter], keep="start")
        self.assertEqual(result, [target])
        self.assertEqual(line_fields(sk, target), (0, 0, 600, 0))  # x2/y2 恰好落在交点
        self.assertEqual(line_fields(sk, cutter), (600, -100, 600, 100))

    def test_trim_keep_end_moves_start(self):
        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        cutter = add_line(sk, (600, -100), (600, 100))
        result = OPS["trim"](sk, target_ref=target, cutter_refs=[cutter], keep="end")
        self.assertEqual(result, [target])
        self.assertEqual(line_fields(sk, target), (600, 0, 1000, 0))

    def test_extend_end_lands_on_boundary_and_keeps_other_end(self):
        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        boundary = add_line(sk, (1500, -500), (1500, 500))
        result = OPS["extend"](sk, target_ref=target, boundary_refs=[boundary], end="end")
        self.assertEqual(result, [target])
        self.assertEqual(line_fields(sk, target), (0, 0, 1500, 0))  # 另一端一个纳米都不动


# --------------------------------------------------------------------------------------
# 5) fillet / chamfer / array_linear：相切、端点、数量
# --------------------------------------------------------------------------------------


class TestCornerAndArrayNumerics(ReasonMixin, unittest.TestCase):
    def test_fillet_arc_tangent_to_both_lines_exact(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (1000, 0))
        b = add_line(sk, (0, 0), (0, 1000))
        result = OPS["fillet"](sk, ref_a=a, ref_b=b, radius_nm=200)
        self.assertEqual(len(result), 1)
        arc = result[0]
        self.assertEqual(kind_of(sk, arc), "arc")
        g = geom(sk, arc)
        self.assertEqual((g["cx"], g["cy"], g["r"]), (200, 200, 200))
        self.assertAlmostEqual(g["start_deg"], 180.0, places=6)
        self.assertAlmostEqual(g["end_deg"], 270.0, places=6)
        self.assertEqual(line_fields(sk, a), (200, 0, 1000, 0))  # 近端被裁到切点
        self.assertEqual(line_fields(sk, b), (0, 200, 0, 1000))
        # 相切是这把尺子的验收口径：圆心到两条直线的距离 == 弧半径（两条线轴对齐，退化为 |cy| / |cx|）
        self.assertEqual(abs(g["cy"] - 0), g["r"])
        self.assertEqual(abs(g["cx"] - 0), g["r"])
        self.assertEqual(len(sk["entities"]), 3)

    def test_chamfer_segment_endpoints_exact(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (1000, 0))
        b = add_line(sk, (0, 0), (0, 1000))
        result = OPS["chamfer"](sk, ref_a=a, ref_b=b, distance_nm=100)
        self.assertEqual(len(result), 1)
        chamfer = result[0]
        self.assertEqual(kind_of(sk, chamfer), "line")
        self.assertEqual(line_fields(sk, chamfer), (100, 0, 0, 100))
        self.assertEqual(line_fields(sk, a), (100, 0, 1000, 0))
        self.assertEqual(line_fields(sk, b), (0, 100, 0, 1000))
        self.assertEqual(len(sk["entities"]), 3)

    def test_array_linear_offsets_and_count(self):
        sk = new_sketch()
        source = add_line(sk, (0, 0), (100, 0))
        result = OPS["array_linear"](sk, refs=[source], rows=2, cols=3, dx_nm=1000, dy_nm=500)
        self.assertEqual(len(result), 2 * 3 - 1)  # 跳过原位 (0,0)
        self.assertEqual(len(set(result)), 5)
        self.assertEqual(len(sk["entities"]), 6)
        starts = sorted(endpoints(sk, uuid_)[0] for uuid_ in result)
        self.assertEqual(starts, sorted([(1000, 0), (2000, 0), (0, 500), (1000, 500), (2000, 500)]))
        for uuid_ in result:
            self.assertEqual(line_fields(sk, uuid_), (endpoints(sk, uuid_)[0][0], endpoints(sk, uuid_)[0][1],
                                                      endpoints(sk, uuid_)[0][0] + 100, endpoints(sk, uuid_)[0][1]))
        self.assertEqual(line_fields(sk, source), (0, 0, 100, 0))  # 源实体不动

    def test_array_linear_one_by_one_is_a_real_zero(self):
        sk = new_sketch()
        source = add_line(sk, (0, 0), (100, 0))
        result = OPS["array_linear"](sk, refs=[source], rows=1, cols=1, dx_nm=0, dy_nm=0)
        self.assertEqual(result, [])  # 真为零（不是"没算"）
        self.assertEqual(len(sk["entities"]), 1)


# --------------------------------------------------------------------------------------
# 6) 边界：非法参数一律 CadError + reason
# --------------------------------------------------------------------------------------


class TestBoundaries(ReasonMixin, unittest.TestCase):
    def test_invalid_length_rejected(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        for bad in (1.5, True, "10", None):
            with self.subTest(dx_nm=repr(bad)):
                self.assertEqual(self.reason_of(OPS["move"], sk=sk, refs=[line], dx_nm=bad, dy_nm=0),
                                 "invalid_length")
        self.assertEqual(line_fields(sk, line), (0, 0, 1000, 0))  # 坏入参不留半成品

    def test_empty_selection_and_unknown_reference(self):
        sk = new_sketch()
        add_line(sk, (0, 0), (1000, 0))
        self.assertEqual(self.reason_of(OPS["move"], sk=sk, refs=[], dx_nm=1, dy_nm=1), "empty_selection")
        self.assertEqual(self.reason_of(OPS["move"], sk=sk, refs=["not-a-uuid"], dx_nm=1, dy_nm=1),
                         "unknown_reference")

    def test_unsupported_geometry_rejected(self):
        sk = new_sketch()
        circle = add_circle(sk, 0, 0, 500)
        line = add_line(sk, (600, -100), (600, 100))
        self.assertEqual(self.reason_of(OPS["trim"], sk=sk, target_ref=circle, cutter_refs=[line]),
                         "unsupported_geometry")
        self.assertEqual(self.reason_of(OPS["offset"], sk=sk, ref=line, distance_nm=10, side=2),
                         "invalid_argument")

    def test_parallel_lines_and_no_intersection_rejected(self):
        sk = new_sketch()
        a = add_line(sk, (0, 0), (1000, 0))
        b = add_line(sk, (0, 500), (1000, 500))
        self.assertEqual(self.reason_of(OPS["fillet"], sk=sk, ref_a=a, ref_b=b, radius_nm=100), "parallel_lines")
        self.assertEqual(self.reason_of(OPS["chamfer"], sk=sk, ref_a=a, ref_b=b, distance_nm=100), "parallel_lines")

        sk = new_sketch()
        target = add_line(sk, (0, 0), (1000, 0))
        outside = add_line(sk, (2000, -100), (2000, 100))  # 交点 t=2 落在 target 之外
        self.assertEqual(self.reason_of(OPS["trim"], sk=sk, target_ref=target, cutter_refs=[outside]),
                         "no_intersection")
        behind = add_line(sk, (-500, -100), (-500, 100))  # 反向交点 ⇒ 没有前向延伸
        self.assertEqual(self.reason_of(OPS["extend"], sk=sk, target_ref=target, boundary_refs=[behind],
                                        end="end"), "no_intersection")

    def test_invalid_argument_matrix(self):
        sk = new_sketch()
        line = add_line(sk, (0, 0), (1000, 0))
        self.assertEqual(self.reason_of(OPS["scale"], sk=sk, refs=[line], center_nm=(0, 0), factor=0),
                         "invalid_argument")
        self.assertEqual(self.reason_of(OPS["scale"], sk=sk, refs=[line], center_nm=(0, 0), factor=-1),
                         "invalid_argument")
        self.assertEqual(self.reason_of(OPS["mirror"], sk=sk, refs=[line], p1_nm=(5, 5), p2_nm=(5, 5)),
                         "degenerate_axis")
        self.assertEqual(self.reason_of(OPS["rotate"], sk=sk, refs=[line], center_nm=(0, 0, 0), angle_deg=90),
                         "invalid_argument")
        self.assertEqual(self.reason_of(OPS["array_linear"], sk=sk, refs=[line], rows=2, cols=2,
                                        dx_nm=0, dy_nm=0), "invalid_argument")
        self.assertEqual(self.reason_of(OPS["array_linear"], sk=sk, refs=[line], rows=0, cols=1,
                                        dx_nm=10, dy_nm=10), "invalid_argument")
        self.assertEqual(self.reason_of(OPS["trim"], sk=sk, target_ref=line,
                                        cutter_refs=[line], keep="middle"), "invalid_argument")
        self.assertEqual(line_fields(sk, line), (0, 0, 1000, 0))


# --------------------------------------------------------------------------------------
# 7) 与已有命令组合（2 例：primitives.add_rectangle + 约束账 + 闭合尺子）
# --------------------------------------------------------------------------------------


class TestCombinationsWithExistingCommands(ReasonMixin, unittest.TestCase):
    def test_rectangle_move_preserves_uuid_constraints_and_closure(self):
        """组合 1：`primitives.add_rectangle` 造矩形 ⇒ `edit_ops.move` 整块平移。

        原地改写的意义在这里可验证：摘要的约束账按 uuid 引用实体，若移动是"删旧建新"，
        已有的 4 条约束当场变悬空引用 ⇒ 本用例断言约束账**逐字节不变**且每个 ref 仍解析得到。
        """
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 500))
        self.assertEqual(len(rect), 4)
        constraints_before = copy.deepcopy(sk["constraints"])
        self.assertEqual(len(constraints_before), 4)
        self.assertEqual(len(loops(sk)), 1)

        result = OPS["move"](sk, refs=rect, dx_nm=100, dy_nm=200)
        self.assertEqual(result, rect)  # 原 uuid、原顺序
        self.assertEqual(sk["constraints"], constraints_before)  # 约束账一个字节都不动
        for item in sk["constraints"]:
            for ref_uuid, _sub in item["refs"]:
                cons.find_entity(sk, ref_uuid)  # 悬空引用当场抛错
        self.assertEqual(len(sk["entities"]), 4)
        self.assertEqual(
            sorted(endpoints(sk, uuid_) for uuid_ in rect),
            sorted([((100, 200), (1100, 200)), ((1100, 200), (1100, 700)),
                    ((1100, 700), (100, 700)), ((100, 700), (100, 200))]),
        )
        after = loops(sk)
        self.assertEqual(len(after), 1)  # 平移后仍是同一条闭合轮廓
        self.assertEqual(set(after[0]), set(rect))

    def test_rectangle_fillet_opens_profile_and_reuses_uuid_in_place(self):
        """组合 2：`primitives.add_rectangle` 造矩形 ⇒ `edit_ops.fillet` 在一个角上倒圆。

        倒圆把两条相邻边的近端裁到切点、再用切弧把缺口补上 ⇒ 轮廓**仍然闭合**（4 线 + 1 弧，环长 5），
        两条边的坐标是**原地改写**（uuid 复用、实体数 4→5）—— 这是"真改写而不是复制"的对照。
        （方法名保留原名以便按名引用；`loops(sk) == []` 的原期望是错的，推导见下方断言处。）
        """
        sk = new_sketch()
        rect = prim.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(1000, 500))
        bottom, right = rect[0], rect[1]
        self.assertEqual(line_fields(sk, bottom), (0, 0, 1000, 0))
        self.assertEqual(line_fields(sk, right), (1000, 0, 1000, 500))

        result = OPS["fillet"](sk, ref_a=bottom, ref_b=right, radius_nm=100)
        self.assertEqual(len(result), 1)
        arc = result[0]
        self.assertEqual(kind_of(sk, arc), "arc")
        g = geom(sk, arc)
        self.assertEqual((g["cx"], g["cy"], g["r"]), (900, 100, 100))
        self.assertAlmostEqual(g["start_deg"], 270.0, places=6)
        self.assertAlmostEqual(g["end_deg"], 0.0, places=6)
        self.assertEqual(line_fields(sk, bottom), (0, 0, 900, 0))
        self.assertEqual(line_fields(sk, right), (1000, 100, 1000, 500))
        self.assertEqual(line_fields(sk, rect[2]), (1000, 500, 0, 500))  # 另两条边不动
        self.assertEqual(line_fields(sk, rect[3]), (0, 500, 0, 0))
        self.assertEqual(len(sk["entities"]), 5)
        self.assertEqual(len(sk["constraints"]), 4)  # 倒圆不改约束账
        # 根因（测试侧，非实现侧）：上面 566-578 行**自己断言**的几何就是——两线裁到切点 (900,0)/(1000,100)，
        # 新弧 cx=900,cy=100,r=100,start=270°,end=0° ⇒ 弧两端点 = (900+100·cos270°, 100+100·sin270°) = (900,0)
        # 与 (900+100·cos0°, 100+100·sin0°) = (1000,100)，与两条线的新端点逐位相等。
        # 因此 4 线 + 1 弧本来就构成闭合环；`closed_profiles` 看见闭合是**正确**的（倒圆=把尖角换成切弧，不是开口）。
        # 要让 loops 返回空，只能去改坏公共尺子（如让它不认弧）或让弧端点不合切点——两者都违反本模块的后置硬断言。
        loop = loops(sk)
        self.assertEqual(len(loop), 1)  # 倒圆后轮廓仍闭合，只是环成员换了
        self.assertEqual(set(loop[0]), {bottom, arc, right, rect[2], rect[3]})
        self.assertEqual(len(loop[0]), 5)


if __name__ == "__main__":  # pragma: no cover - 手工跑单文件时的入口
    unittest.main(verbosity=2)
