#!/usr/bin/env python3
"""tests.test_precision_ops_ops —— precision 族对账测试（对外操作层）。

被测对象
--------
``cadcore/ops/precision_ops.py``：精度的命令层 —— 捕捉总开关/参与类型/优先级/容差、正交、极轴、
捕捉追踪、动态输入、栅格步进、``precision_apply`` 解析链、``precision_filter`` 查询、``precision_reset`` 复位。

冻结接口（本文件按它判红绿；每条断言旁写清判据）
----------------------------------------------
- ``FAMILY == "precision"``；
- ``OPERATIONS: dict[str, Callable]``，13 个操作；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：**首参按位置传**（草图 dict 或文档对象），
  **其余参数一律关键字**（`inspect` 断言「除首参外全是 KEYWORD_ONLY」；多传位置参数必须 TypeError）；
- 长度一律整数纳米；错误一律抛 ``cadcore.errors.CadError(reason=...)``；
- 口径：``0 = 真为零`` / ``-1 = 未采`` / ``NA = 键缺席``。

夹具自证（E2.5）
--------------
1. ``OPERATIONS[name] is getattr(precision_ops, name)``、``inspect.getsourcefile`` 指向本仓 ``cadcore/ops/precision_ops.py``
   ⇒ 证明本文件断言的是**真件**，不是自造方言；
2. 几何夹具（L1 线段/圆）与族模块自检 ``main()`` 用的是同一形状 ⇒ 证明夹具喂的是真 L1 记录；
3. 本文件不写盘、不建 ``ops/__init__.py``、不引入第三方库。
"""

from __future__ import annotations

import ast
import contextlib
import copy
import inspect
import io
import math
import os
import re
import sys
import unittest

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_ROOT not in sys.path:  # 直跑 python3 tests/test_precision_ops_ops.py 时的引导
    sys.path.insert(0, PKG_ROOT)

from cadcore import docmodel  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import precision_ops as P  # noqa: E402
from cadcore.sketch.constraints import UDEG_PER_DEG, deg_to_udeg  # noqa: E402

OPS = P.OPERATIONS
MODE_NAMES = P.MODE_NAMES
SNAP_KINDS = P.SNAP_KINDS

EXPECTED_OPS = {
    "snap_enable", "snap_kinds_set", "snap_priority_set", "snap_tolerance_set", "snap_probe",
    "ortho_set", "polar_set", "otrack_set", "dyninput_set", "grid_set",
    "precision_apply", "precision_filter", "precision_reset",
}


# ======================================================================================
# 夹具：真 L1 记录（与族模块自检 _line/_circle 同形）
# ======================================================================================


def line(uuid: str, x1: int, y1: int, x2: int, y2: int) -> dict:
    return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


def circle(uuid: str, cx: int, cy: int, r: int) -> dict:
    return {"uuid": uuid, "kind": "circle", "layer": "0", "construction": False,
            "geom": {"cx": cx, "cy": cy, "r": r}}


def new_sketch() -> dict:
    """一条 (0,0)-(1000,0) 线段 + 一个圆心 (500,0) 半径 100 的圆。"""
    return {"entities": [line("L1", 0, 0, 1000, 0), circle("C1", 500, 0, 100)],
            "constraints": [], "dimensions": []}


def quiet_osnap(sk: dict) -> None:
    """把对象捕捉整个关掉（解析链后面几步的对照臂用）。"""
    P.snap_enable(sk, enabled=False)


class CadErrorCase(unittest.TestCase):
    """共用的「必须以这个 reason 报 CadError」断言。"""

    def assert_reason(self, reason: str, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except CadError as exc:
            self.assertEqual(exc.reason, reason,
                             f"expected CadError(reason={reason!r}), got reason={exc.reason!r} ({exc})")
            return exc
        raise AssertionError(f"expected CadError(reason={reason!r}), but the call succeeded")


# ======================================================================================
# 1. 冻结接口形状
# ======================================================================================


class FrozenInterfaceTests(CadErrorCase):

    #: spec/precision_ops.json 的 spec 面操作（settings.drafting + doc['_ops'] 结果通道）。
    #: 与上面的族内面 13 个合起来才是 OPERATIONS 的冻结全集（13 + 17 = 30）。
    SPEC_OPS = {
        "osnap_set", "osnap_resolve", "osnap_apparent", "osnap_tangent", "osnap_extension",
        "osnap_parallel", "osnap_feedback", "ortho_lock", "polar_track", "otrack_resolve",
        "snap_step", "grid_snap", "polar_snap", "dyn_input_parse", "coord_input_parse",
        "drafting_status", "drafting_set",
    }
    #: spec 冻结的**纯几何助手**三个：spec 的 api 行把它们的 base_nm / point_nm 明写成位置参数
    #: （spec/precision_ops.json『正交模式』item：``ortho_lock(base_nm, point_nm, *, tolerance_deg=3.0)``；
    #: 『极轴追踪』与『极轴捕捉 PolarSnap』同形）⇒ 它们是 OPERATIONS 里仅有的「第二参也按位置传」的 op。
    POSITIONAL_PAIR_OPS = {"ortho_lock", "polar_track", "polar_snap"}

    def test_family_and_operation_table(self):
        self.assertEqual(P.FAMILY, "precision_ops",
                         "FAMILY 是冻结接口之一（spec/precision_ops.json 冻结值 \"precision_ops\"）")
        self.assertEqual(set(OPS), EXPECTED_OPS | self.SPEC_OPS,
                         "OPERATIONS 必须恰好暴露 30 个操作（族内面 13 + spec 面 17）")
        for name, fn in OPS.items():
            self.assertTrue(callable(fn), f"{name} 必须是可调用对象")
            # 夹具自证：表里的操作就是模块里的真件（不是包装出来的替身）
            self.assertIs(fn, getattr(P, name), f"OPERATIONS[{name!r}] 必须就是模块里的同名函数")
        self.assertEqual(inspect.getsourcefile(P.snap_enable),
                         os.path.join(PKG_ROOT, "cadcore", "ops", "precision_ops.py"),
                         "断言的必须是本仓 cadcore/ops/precision_ops.py 真件")

    def test_first_positional_and_keyword_only_rest(self):
        """判据：首参可位置传、无默认值；其余参数**全是** KEYWORD_ONLY（冻结接口「其余走关键字」）。

        **spec 冻结的三个例外**：``ortho_lock`` / ``polar_track`` / ``polar_snap`` 是 spec 的 api 行
        明写的纯几何助手（``ortho_lock(base_nm, point_nm, *, tolerance_deg=3.0) -> tuple[int,int]``），
        第二个点参数按位置传是 **spec 的要求**、不是走漏 ⇒ 对这三个 op 反过来断言「恰好两个位置参、
        都没有默认值」，其后的参数仍然必须全部 KEYWORD_ONLY（比原断言更严，不是放宽）。
        """
        for name, fn in OPS.items():
            params = list(inspect.signature(fn).parameters.values())
            self.assertTrue(params, f"{name} 至少要有首参 sk_or_doc")
            first = params[0]
            self.assertIn(first.kind, (inspect.Parameter.POSITIONAL_ONLY,
                                       inspect.Parameter.POSITIONAL_OR_KEYWORD),
                          f"{name} 首参必须能按位置传，实际 kind={first.kind}")
            self.assertIs(first.default, inspect.Parameter.empty, f"{name} 首参不能有默认值")
            head = 1
            if name in self.POSITIONAL_PAIR_OPS:
                head = 2
                second = params[1]
                self.assertIn(second.kind, (inspect.Parameter.POSITIONAL_ONLY,
                                            inspect.Parameter.POSITIONAL_OR_KEYWORD),
                              f"{name} 第二个点参数按 spec 冻结签名必须能位置传，实际 kind={second.kind}")
                self.assertIs(second.default, inspect.Parameter.empty,
                              f"{name} 第二个点参数不能有默认值")
            for extra in params[head:]:
                self.assertIs(extra.kind, inspect.Parameter.KEYWORD_ONLY,
                              f"{name}({extra.name}) 必须只走关键字，实际 kind={extra.kind}")

    def test_ops_reject_second_positional_and_unknown_kwarg(self):
        sk = new_sketch()
        with self.assertRaises(TypeError, msg="其余参数走关键字：第二个位置参数必须 TypeError"):
            OPS["snap_enable"](sk, True)
        with self.assertRaises(TypeError, msg="未声明的关键字必须 TypeError，不许被静默吞掉"):
            OPS["snap_enable"](sk, nope=1)
        with self.assertRaises(TypeError, msg="未声明的关键字必须 TypeError"):
            OPS["grid_set"](sk, spacing=10)

    def test_every_op_takes_sketch_positionally_and_returns_uuid_list(self):
        """判据：13 个操作都能 ``op(sk_or_doc, **kwargs)`` 调用，且返回非空字符串组成的 list。"""
        calls = {
            "snap_enable": {"enabled": True},
            "snap_kinds_set": {"kinds": ["endpoint"]},
            "snap_priority_set": {"priority": list(SNAP_KINDS)},
            "snap_tolerance_set": {"tolerance_nm": 1_000_000},
            "snap_probe": {"point_nm": (0, 0)},
            "ortho_set": {"enabled": True},
            "polar_set": {"enabled": True, "increments_deg": [45.0]},
            "otrack_set": {"enabled": True, "points_nm": [(0, 0)]},
            "dyninput_set": {"enabled": True, "fields": ["pointer"]},
            "grid_set": {"enabled": True, "spacing_nm": 1_000_000},
            "precision_apply": {"point_nm": (1, 2)},
            "precision_filter": {},
            "precision_reset": {},
        }
        self.assertEqual(set(calls), EXPECTED_OPS, "调用表必须覆盖每个操作（不许漏测）")
        sk = new_sketch()
        for name, kwargs in calls.items():
            out = OPS[name](sk, **kwargs)  # 首参按位置传 = 冻结接口的调用形态
            self.assertIsInstance(out, list, f"{name} 必须返回 list")
            for item in out:
                self.assertIsInstance(item, str, f"{name} 的返回值必须是 uuid 字符串")
                self.assertTrue(item, f"{name} 不许返回空字符串 uuid")
            self.assertEqual(len(set(out)), len(out), f"{name} 的 uuid 不许重复")


# ======================================================================================
# 2. 捕捉：开关 / 参与类型 / 优先级 / 容差 / 纯查询
# ======================================================================================


class SnapTests(CadErrorCase):

    def test_kinds_set_returns_enabled_in_priority_order(self):
        sk = new_sketch()
        on = P.snap_kinds_set(sk, kinds=["endpoint", "nearest"])
        self.assertEqual(len(on), 2, "列出的两个类型应当被打开")
        self.assertEqual(P.snap_kinds_set(sk, kinds=["nearest", "endpoint"]), on,
                         "返回值按当前优先级排序，不随入参顺序变化")
        self.assertEqual(set(on), {P.precision_filter(sk, kind="endpoint")[0],
                                   P.precision_filter(sk, kind="nearest")[0]})
        self.assertEqual(P.snap_kinds_set(sk, kinds=[]), [], "空集合合法：全部关掉并返回空表")

    def test_priority_set_reorders_and_validates(self):
        sk = new_sketch()
        wanted = ["nearest"] + [k for k in SNAP_KINDS if k != "nearest"]
        order = P.snap_priority_set(sk, priority=wanted)
        self.assertEqual(len(order), len(SNAP_KINDS), "优先级必须覆盖每个类型恰好一次")
        self.assertEqual(order, [P.precision_filter(sk, kind=k)[0] for k in wanted],
                         "返回顺序 = 新优先级顺序")
        self.assert_reason("incomplete_priority", P.snap_priority_set, sk, priority=["endpoint"])
        self.assert_reason("incomplete_priority", P.snap_priority_set, sk,
                           priority=["endpoint"] * len(SNAP_KINDS))
        self.assert_reason("unknown_snap_kind", P.snap_kinds_set, sk, kinds=["tangent"])
        self.assert_reason("invalid_argument", P.snap_kinds_set, sk, kinds="endpoint")

    def test_probe_hits_real_geometry_and_never_fakes_a_hit(self):
        sk = new_sketch()
        P.snap_kinds_set(sk, kinds=["endpoint"])
        P.snap_priority_set(sk, priority=list(SNAP_KINDS))
        self.assertEqual(P.snap_probe(sk, point_nm=(1000, 5)), P.precision_filter(sk, kind="endpoint"),
                         "线段端点附近应命中 endpoint 类型记录")
        self.assertEqual(P.snap_probe(sk, point_nm=(50_000_000, 50_000_000)), [],
                         "远处不许返回假命中")
        u_off = P.snap_enable(sk, enabled=False)
        self.assertEqual(P.snap_probe(sk, point_nm=(1000, 5)), [],
                         "总开关关掉 ⇒ 空表（不是当作全开）")
        self.assertEqual(P.precision_filter(sk, name="osnap", enabled=False), u_off,
                         "关掉的开关必须能按 enabled=False 查回来")
        self.assertEqual(P.precision_filter(sk, name="osnap", enabled=True), [],
                         "阴性对照：关掉之后 enabled=True 查询必须为空")

    def test_probe_and_filter_are_pure_queries(self):
        """判据：表还没建出来 ⇒ []，且**不建表**（不读缺省值冒充已设置）。"""
        fresh = new_sketch()
        self.assertEqual(P.snap_probe(fresh, point_nm=(1000, 5)), [])
        self.assertNotIn("precision", fresh, "snap_probe 不许建表")
        self.assertEqual(P.precision_filter(fresh), [])
        self.assertNotIn("precision", fresh, "precision_filter 不许建表")

    def test_tolerance_bounds(self):
        sk = new_sketch()
        u_osnap = P.snap_tolerance_set(sk, tolerance_nm=0)
        self.assertEqual(P.precision_filter(sk, name="osnap"), u_osnap, "0 是合法容差（真为零，不是未设）")
        self.assert_reason("out_of_range", P.snap_tolerance_set, sk, tolerance_nm=-1)
        self.assert_reason("invalid_argument", P.snap_enable, sk, enabled="yes")


# ======================================================================================
# 3. precision_apply 解析链：顺序 / 生效表 / 读数口径（0 / -1 / NA）
# ======================================================================================


class ResolveChainTests(CadErrorCase):

    def test_ortho_overrides_polar_and_reports_the_fired_record(self):
        sk = new_sketch()
        quiet_osnap(sk)
        u_ortho = P.ortho_set(sk, enabled=True, tolerance_deg=3.0)[0]
        u_polar = P.polar_set(sk, enabled=True, increments_deg=[45.0], tolerance_deg=3.0)[0]
        out = P.precision_apply(sk, point_nm=(1000, 20), base_nm=(0, 0))
        readout = P.cursor_readout(sk)
        self.assertEqual(readout["uuid"], out[0], "cursor 记录 uuid 是返回表的第一项")
        self.assertEqual(readout["source"], out[1:], "source = 生效表（与返回表尾部一致）")
        self.assertEqual(readout["raw_nm"], [1000, 20], "raw_nm 保留原始光标点")
        self.assertEqual(readout["point_nm"], [1000, 0], "正交把离轴坐标锁到 0")
        self.assertIn(u_ortho, out, "正交生效 ⇒ 正交记录进生效表")
        self.assertNotIn(u_polar, out, "正交优先：极轴不得同时生效")
        self.assertEqual(readout["distance_nm"], 1000, "锁轴后距离取投影距离")
        self.assertEqual(readout["angle_udeg"], 0, "0 微度 = 真在 +x 方向（真为零）")
        self.assertIsInstance(readout["distance_nm"], int, "读数一律整数纳米")

        P.ortho_set(sk, enabled=False)
        out = P.precision_apply(sk, point_nm=(1000, 20), base_nm=(0, 0))
        self.assertIn(u_polar, out, "正交关掉后极轴接管（阴性对照：证明尺子看得见 '谁生效'）")

    def test_grid_step_only_when_osnap_missed(self):
        sk = new_sketch()
        quiet_osnap(sk)
        u_grid = P.grid_set(sk, enabled=True, spacing_nm=10_000_000, snap_enabled=True, major_every=-1)[0]
        out = P.precision_apply(sk, point_nm=(12_000_000, 4_000_000))
        readout = P.cursor_readout(sk)
        self.assertEqual(readout["point_nm"], [10_000_000, 0], "吸到步长的整数倍")
        self.assertIn(u_grid, out, "栅格生效 ⇒ grid 记录进生效表")
        self.assertNotIn("distance_nm", readout, "NA：没给基点 ⇒ 相对读数键整个缺席")
        self.assertNotIn("angle_udeg", readout, "NA：没给基点 ⇒ 相对读数键整个缺席")

        out = P.precision_apply(sk, point_nm=(10_000_000, 0))
        self.assertEqual(out[1:], [], "点本来就在格点上 ⇒ 不算生效")

        # 对象捕捉命中时，栅格步进不得接管（解析链第 2 步只在第 1 步没命中时做）
        P.snap_enable(sk, enabled=True)
        P.snap_kinds_set(sk, kinds=["endpoint"])
        out = P.precision_apply(sk, point_nm=(1000, 5))
        self.assertEqual(P.cursor_readout(sk)["point_nm"], [1000, 0], "端点捕捉赢过栅格")
        self.assertNotIn(u_grid, out, "栅格不得越权接管已命中的点")

    def test_grid_major_every_unset_sentinel_is_minus_one(self):
        """判据：``-1 = 未采``（用户从未设置）与 ``>= 1``（真设过）必须可区分。"""
        sk = new_sketch()
        P.grid_set(sk, enabled=True, spacing_nm=1_000_000)
        record = [r for r in sk["precision"]["modes"] if r["name"] == "grid"][0]
        self.assertEqual(record["config"]["major_every"], -1, "没设过 ⇒ -1（未采），不许写 None/0")
        self.assertIsInstance(record["config"]["spacing_nm"], int, "步长整数纳米")
        P.grid_set(sk, major_every=5)
        record = [r for r in sk["precision"]["modes"] if r["name"] == "grid"][0]
        self.assertEqual(record["config"]["major_every"], 5, "设过 ⇒ 真值")
        self.assert_reason("out_of_range", P.grid_set, sk, major_every=0)
        self.assert_reason("invalid_length", P.grid_set, sk, spacing_nm=0)
        P.grid_set(sk, major_every=-1)
        self.assertEqual([r for r in sk["precision"]["modes"] if r["name"] == "grid"][0]["config"]["major_every"],
                         -1, "-1 可以显式写回未采")

    def test_grid_participates_only_when_snap_enabled(self):
        sk = new_sketch()
        quiet_osnap(sk)
        P.grid_set(sk, enabled=True, spacing_nm=10_000_000, snap_enabled=False)
        P.precision_apply(sk, point_nm=(12_000_000, 4_000_000))
        self.assertEqual(P.cursor_readout(sk)["point_nm"], [12_000_000, 4_000_000],
                         "snap_enabled=False ⇒ 栅格只显示不捕捉")

    def test_otrack_aligns_but_never_fights_ortho(self):
        sk = new_sketch()
        quiet_osnap(sk)
        u_otrack = P.otrack_set(sk, enabled=True, points_nm=[(3_000_000, 7_000_000)],
                                tolerance_nm=1_000_000)[0]
        out = P.precision_apply(sk, point_nm=(3_400_000, 9_000_000), base_nm=(0, 0))
        readout = P.cursor_readout(sk)
        self.assertEqual(readout["point_nm"], [3_000_000, 9_000_000], "对齐到过追踪点的竖直路径")
        self.assertIn(u_otrack, out, "追踪生效 ⇒ otrack 记录进生效表")
        self.assertGreater(readout["distance_nm"], 9_000_000, "对齐后距离仍按结果点算")
        # 角度是**微度**：真源 cadcore.sketch.constraints.deg_to_udeg（UDEG_PER_DEG = 1e6）。
        # 结果点 (3e6, 9e6) 相对基点 (0,0) 是 71.565° ≈ 71_565_000 微度；旧断言写成 700_000..720_000
        # 是**期望值的单位写错**（1e-4 度尺度，差 100 倍），不是实现的角度算错。
        self.assertEqual(UDEG_PER_DEG, 1_000_000, "微度口径来自 sketch 层（单一真源）")
        expected_udeg = deg_to_udeg(math.degrees(math.atan2(9_000_000, 3_000_000)))
        self.assertLessEqual(abs(readout["angle_udeg"] - expected_udeg), 1_000,
                             "角度按结果点算（微度，容差 1e-3 度）")

        u_ortho = P.ortho_set(sk, enabled=True, tolerance_deg=3.0)[0]
        out = P.precision_apply(sk, point_nm=(3_400_000, 100_000), base_nm=(0, 0))
        self.assertIn(u_ortho, out, "正交锁住 ⇒ 正交记录进生效表")
        self.assertNotIn(u_otrack, out, "已锁住时追踪不得再抢方向")

    def test_dyninput_fields_and_readout_na_semantics(self):
        sk = new_sketch()
        quiet_osnap(sk)
        P.precision_apply(sk, point_nm=(1, 2), base_nm=(0, 0))
        self.assertEqual(P.cursor_readout(sk)["fields"], ["pointer", "dimension", "dynamic_prompt"],
                         "动态输入缺省字段")
        u_dyn = P.dyninput_set(sk, enabled=False)[0]
        self.assertEqual(P.precision_filter(sk, name="dyninput", enabled=False), [u_dyn])
        P.precision_apply(sk, point_nm=(1, 2), base_nm=(0, 0))
        self.assertEqual(P.cursor_readout(sk)["fields"], [], "关掉动态输入 ⇒ 读数不带字段")
        P.dyninput_set(sk, enabled=True, fields=["pointer"])
        P.precision_apply(sk, point_nm=(0, 0), base_nm=(0, 0))
        readout = P.cursor_readout(sk)
        self.assertEqual(readout["fields"], ["pointer"], "读数反映持久化的字段集合")
        self.assertEqual(readout["distance_nm"], 0, "与基点重合 ⇒ 真为零（不是未采）")
        self.assertNotIn("angle_udeg", readout, "NA：重合时方向未定义 ⇒ 键缺席")
        self.assertEqual(readout["position_nm"], readout["point_nm"], "动态输入位置 = 结果点")
        self.assert_reason("unknown_field", P.dyninput_set, sk, fields=["nope"])

    def test_apply_argument_validation(self):
        sk = new_sketch()
        self.assert_reason("invalid_argument", P.precision_apply, sk, point_nm=(1, 2, 3))
        self.assert_reason("invalid_argument", P.precision_apply, sk, point_nm=(1, 2), base_nm=(3,))


# ======================================================================================
# 4. 持久化容器 / 文档往返 / 复位 / 坏表
# ======================================================================================


class PersistenceTests(CadErrorCase):

    def test_sketch_store_shape_and_untouched_l1_fields(self):
        sk = new_sketch()
        before = copy.deepcopy(sk)
        P.snap_enable(sk, enabled=True)
        store = sk["precision"]
        self.assertEqual(store["schema"], 1, "表带 schema 版本")
        self.assertEqual(len(store["modes"]), len(MODE_NAMES), "六条模式记录，一条不少")
        names = [r["name"] for r in store["modes"]]
        self.assertEqual(set(names), set(MODE_NAMES), "模式名必须都在 MODE_NAMES 里")
        self.assertEqual(len(set(names)), len(names), "模式名不许重复")
        self.assertEqual([r["kind"] for r in store["snap_kinds"]], list(SNAP_KINDS),
                         "七条捕捉类型记录（列表顺序 = 优先级，冻结在 docstring 里）")
        self.assertEqual(sk["entities"], before["entities"], "设置不改 L1 实体")
        self.assertEqual(sk["constraints"], before["constraints"])
        self.assertEqual(sk["dimensions"], before["dimensions"])
        P.precision_apply(sk, point_nm=(1, 2))
        self.assertIn("cursor", sk["precision"], "precision_apply 写下 cursor 读数")

    def test_document_target_round_trip_and_invariants(self):
        doc = docmodel.new_document()
        doc.sketches.append({"uuid": P.new_uuid(), "plane": "XY",
                             "entities": [line(P.new_uuid(), 0, 0, 1000, 0)],
                             "constraints": [], "dimensions": []})
        u_doc = P.grid_set(doc, enabled=True, spacing_nm=1_000_000, snap_enabled=True, major_every=5)[0]
        P.ortho_set(doc, enabled=True, tolerance_deg=2.0)
        self.assertEqual(P.precision_filter(doc, name="grid", enabled=True), [u_doc])
        self.assertEqual(docmodel.check_invariants(doc), [], "precision 表不得破坏 L1 不变量")
        again = docmodel.document_from_l1(docmodel.document_to_l1(doc))
        grid = [r for r in again.extra["precision"]["modes"] if r["name"] == "grid"][0]
        self.assertEqual(grid["config"]["spacing_nm"], 1_000_000, "步长跨 L1 往返不丢")
        self.assertEqual(grid["config"]["major_every"], 5, "-1/真值口径跨往返不丢")
        ortho = [r for r in again.extra["precision"]["modes"] if r["name"] == "ortho"][0]
        self.assertTrue(ortho["enabled"] and ortho["config"]["tolerance_deg"] == 2.0)

    def test_document_with_multiple_sketches_needs_explicit_sketch(self):
        doc = docmodel.new_document()
        sk_uuid = P.new_uuid()
        doc.sketches.append({"uuid": sk_uuid, "plane": "XY",
                             "entities": [line(P.new_uuid(), 0, 0, 1000, 0)],
                             "constraints": [], "dimensions": []})
        doc.sketches.append({"uuid": P.new_uuid(), "plane": "XY", "entities": [], "constraints": [],
                             "dimensions": []})
        P.snap_kinds_set(doc, kinds=["endpoint"])
        P.snap_priority_set(doc, priority=list(SNAP_KINDS))
        self.assert_reason("ambiguous_target", P.snap_probe, doc, point_nm=(1000, 5))
        self.assertEqual(P.snap_probe(doc, point_nm=(1000, 5), sketch=sk_uuid),
                         P.precision_filter(doc, kind="endpoint"),
                         "给出 sketch uuid ⇒ 定位到那一个草图并命中")
        self.assert_reason("unknown_reference", P.snap_probe, doc, point_nm=(1000, 5),
                           sketch=P.new_uuid())

    def test_filter_argument_validation(self):
        sk = new_sketch()
        P.snap_enable(sk, enabled=True)
        P.snap_kinds_set(sk, kinds=["endpoint"])
        self.assert_reason("unknown_mode", P.precision_filter, sk, name="nope")
        self.assert_reason("unknown_snap_kind", P.precision_filter, sk, kind="nope")
        self.assert_reason("invalid_argument", P.precision_filter, sk, name="grid", kind="endpoint")
        all_modes = P.precision_filter(sk)
        self.assertEqual(len(all_modes), len(MODE_NAMES), "不传过滤条件 ⇒ 全部模式记录")
        self.assertEqual(all_modes, [P.precision_filter(sk, name=m)[0] for m in MODE_NAMES],
                         "顺序按 MODE_NAMES")
        self.assertEqual(P.precision_filter(sk, kind="endpoint", enabled=True),
                         P.precision_filter(sk, kind="endpoint"), "enabled=True 过滤不改变已开的记录")

    def test_reset_restores_defaults_and_drops_cursor(self):
        sk = new_sketch()
        P.grid_set(sk, enabled=True, spacing_nm=1_000_000, snap_enabled=True)
        P.precision_apply(sk, point_nm=(12_000_000, 0))
        self.assertIsNotNone(P.cursor_readout(sk))
        fresh = P.precision_reset(sk)
        self.assertEqual(len(fresh), len(MODE_NAMES) + len(SNAP_KINDS), "复位返回全部记录 uuid")
        self.assertEqual(len(set(fresh)), len(fresh))
        self.assertEqual(P.precision_filter(sk, name="grid", enabled=True), [], "可选辅助一律关掉")
        self.assertNotEqual(P.precision_filter(sk, name="osnap", enabled=True), [], "osnap 缺省是开")
        self.assertIsNone(P.cursor_readout(sk), "复位连 cursor 读数一起丢")

    def test_corrupt_store_fails_loud(self):
        sk = new_sketch()
        sk["precision"] = {"schema": 99}
        self.assert_reason("unsupported_schema", P.snap_enable, sk, enabled=True)
        sk["precision"] = {"schema": 1, "modes": "nope"}
        self.assert_reason("corrupt_store", P.snap_enable, sk, enabled=True)


# ======================================================================================
# 5. 族模块自带自检 + 源码卫生（locale 字面量 / 非 docstring 字面量 ASCII）
# ======================================================================================


class ModuleHygieneTests(unittest.TestCase):

    SRC = ""
    TREE = None

    @classmethod
    def setUpClass(cls):
        with open(P.__file__, encoding="utf-8") as handle:
            cls.SRC = handle.read()
        cls.TREE = ast.parse(cls.SRC)

    def test_module_selfcheck_passes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = P.main()
        self.assertEqual(rc, 0, "族模块自带自检必须通过")
        self.assertIn("verdict=PASS", buf.getvalue(),
                      f"自检必须打印判定行，实际输出：{buf.getvalue()!r}")

    def test_no_locale_literals_in_family_module(self):
        hits = re.findall(r"""["'](?:zh-CN|zh_CN|en|ja|en-US|ja-JP)["']""", self.SRC)
        self.assertEqual(hits, [], f"族模块不得出现 locale 字面量（文案走 key），命中：{hits}")
        hits = re.findall(r"""["'](?:zh-CN|en|ja)["']""", self.SRC)
        self.assertEqual(hits, [], f"机检口径 引号内(zh-CN|en|ja) 必须为 0，命中：{hits}")

    def test_non_docstring_string_literals_are_ascii(self):
        docstrings = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        offenders = [(node.lineno, node.value) for node in ast.walk(self.TREE)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and id(node) not in docstrings and not node.value.isascii()]
        self.assertEqual(offenders, [], f"非 docstring 字面量必须 ASCII，违规：{offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
