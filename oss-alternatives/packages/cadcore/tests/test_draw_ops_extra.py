"""tests.test_draw_ops_extra —— DRAW-OPS-EXTRA 族（`cadcore.ops.draw_ops_extra`）单测。

本文件是**族接口 + 四条 unprobed 能力**的尺子，不是实现的复印件：断言只打冻结接口与
几何/状态行为，不 import 实现内部 helper。

被补的 4 条 unprobed（证据：`oss-alternatives/docs/research/cad-parity/spec/draw_ops.json`
的 `must_have` 里 `api="NA"` 的条目）：修订云线 / 对象捕捉开关 / 正交与极轴追踪 / 动态输入。

冻结接口（逐条打分）
--------------------
1. `FAMILY` 非空字符串、`OPERATIONS` 是 `dict[str, Callable]`（9 个操作，名字集合相等断言）；
2. 每个操作的**第一个位置参数**必须叫 `sk_or_doc` 且可位置传递，其余参数必须 KEYWORD_ONLY
   （即 `op(sk_or_doc, **kwargs)` 是唯一合法调用形态）；
3. 返回值是 uuid 列表：状态助手 → 状态记录 uuid（能在状态表里解析到）；
   几何助手 → **本次新建实体**的 uuid（能在草图里解析到）；
4. 错误一律 `cadcore.errors.CadError`，带机器可读 `reason`（不靠解析文案）。

三类事故是这把尺子专门盯的（都留了阴性对照）
--------------------------------------------
① **接不上接口**：签名与冻结接口不一致 ⇒ 签名检查器 + 两个行为变异体反证检查器看得见违规；
② **静默不做事**：该抛错的地方返回空列表 ⇒ 每条边界都断言 `reason`（osnap 关/没命中、
   追踪不上、dyninput 关、非法纳米）；
③ **假绿尺子**：`test_interface_checker_sees_violations` 用变异体证明检查器会说"违规"。

捕捉探针的容差口径（为什么不用一个很大/很小的容差）
--------------------------------------------------
`osnap_probe` 的命中判据是「距离 ≤ 容差里**最近**的候选，同距比 `SNAP_KINDS` 优先级」，
所以每条断言都把容差收在该候选够得着、别的候选够不着的范围里：比端点更近的
"最近点"（最近点必然 ≤ 端点距离）用 `kinds=` 显式排除，而不是靠容差去碰运气。

夹具自证
--------
* 夹具只依赖 `cadcore.sketch.constraints`（实体字段名与 `new_uuid()` 的唯一真源）与 `cadcore.docmodel`
  （不变量），所以断言打的是真件；
* `ModuleContractTests.test_module_is_the_real_file` 证明被测对象就是
  `cadcore/ops/draw_ops_extra.py` 本身、且 `FAMILY` 未冒充 `draw_ops`（不动既有族门的作用域）；
* 全程内存操作（临时字典），不落盘、不碰别人的文件。
"""

from __future__ import annotations

import inspect
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore import docmodel  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import draw_ops_extra  # noqa: E402
from cadcore.sketch import constraints  # noqa: E402

MM = 1_000_000
L1_FIELDS = {"uuid", "kind", "layer", "construction", "geom"}
#: 冻结的 OPERATIONS 键集（四条 unprobed 覆盖 + osnap 几何腿；相等断言不放宽）。
OPS = ("dyninput_point", "dyninput_set", "ortho_polar_apply", "ortho_polar_set", "osnap_kinds_set",
       "osnap_probe", "osnap_toggle", "revcloud", "revcloud_rect")
STATE_MODES = ("dyninput", "ortho", "osnap", "polar")


# --------------------------------------------------------------------------------------
# 接口检查器 + 行为变异体（证明检查器看得见红）
# --------------------------------------------------------------------------------------


def interface_violations(op) -> list[str]:
    """冻结接口的机检：首参名/首参可位置传 + 其余全 KEYWORD_ONLY。返回违规描述清单。"""
    violations: list[str] = []
    params = list(inspect.signature(op).parameters.values())
    if not params:
        return ["no_parameters"]
    first = params[0]
    if first.name != "sk_or_doc":
        violations.append("first_param=%s" % first.name)
    if first.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY):
        violations.append("first_param_kind=%s" % first.kind.name)
    for param in params[1:]:
        if param.kind not in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD):
            violations.append("tail_param=%s:%s" % (param.name, param.kind.name))
    return violations


def _mutant_first_param_misnamed(context=None, *, point_nm=None):  # noqa: ANN001 - 变异体①
    return []


def _mutant_tail_positional(sk_or_doc, point_nm):  # noqa: ANN001 - 变异体②
    return []


# --------------------------------------------------------------------------------------
# 夹具（只用真件：constraints 的 uuid 真源 + L1 冻结字段）
# --------------------------------------------------------------------------------------


def new_sketch() -> dict:
    return {"uuid": constraints.new_uuid(), "entities": []}


def new_doc(sk: dict) -> dict:
    return {"unit": "mm", "sketches": [sk]}


def entities(sk: dict) -> list:
    return constraints.sketch_entities(sk)


def entity_of(sk: dict, uuid_: str) -> dict:
    for entity in entities(sk):
        if str(entity.get("uuid")) == uuid_:
            return entity
    raise AssertionError("uuid not found in sketch: %s" % uuid_)


def add_line(sk: dict, x1: int, y1: int, x2: int, y2: int, *, layer: str = "0") -> str:
    uuid_ = constraints.new_uuid()
    entities(sk).append({"uuid": uuid_, "kind": "line", "layer": layer, "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return uuid_


def add_circle(sk: dict, cx: int, cy: int, r: int, *, layer: str = "0") -> str:
    uuid_ = constraints.new_uuid()
    entities(sk).append({"uuid": uuid_, "kind": "circle", "layer": layer, "construction": False,
                         "geom": {"cx": cx, "cy": cy, "r": r}})
    return uuid_


def state_of(sk: dict) -> dict:
    """状态表（断言用；路径与模块 docstring 冻结一致）。"""
    return sk["extra"]["draw_ops_extra"]


def call(name: str, sk_or_doc, **kwargs) -> list:
    return draw_ops_extra.OPERATIONS[name](sk_or_doc, **kwargs)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


class FamilyTestCase(unittest.TestCase):
    def assert_reason(self, reason: str, func, *args, **kwargs) -> CadError:
        with self.assertRaises(CadError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.reason, reason, "wrong CadError.reason")
        return ctx.exception


# --------------------------------------------------------------------------------------
# 一、冻结接口
# --------------------------------------------------------------------------------------


class FrozenInterfaceTests(FamilyTestCase):
    def test_family_and_operations(self):
        self.assertIsInstance(draw_ops_extra.FAMILY, str)
        self.assertTrue(draw_ops_extra.FAMILY)
        self.assertIsInstance(draw_ops_extra.OPERATIONS, dict)
        self.assertEqual(sorted(draw_ops_extra.OPERATIONS), list(OPS))
        for name, op in draw_ops_extra.OPERATIONS.items():
            self.assertTrue(callable(op), name)

    def test_every_op_matches_frozen_signature(self):
        for name, op in draw_ops_extra.OPERATIONS.items():
            self.assertEqual(interface_violations(op), [], "signature drifted: %s" % name)

    def test_first_param_is_positional_named_sk_or_doc(self):
        for name, op in draw_ops_extra.OPERATIONS.items():
            params = list(inspect.signature(op).parameters.values())
            self.assertEqual(params[0].name, "sk_or_doc", name)
            self.assertIn(params[0].kind, (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                           inspect.Parameter.POSITIONAL_ONLY), name)
            for param in params[1:]:
                self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY, name)

    def test_interface_checker_sees_violations(self):
        """变异体反证：检查器不是假绿（两个方向都试）。"""
        self.assertNotEqual(interface_violations(_mutant_first_param_misnamed), [])
        self.assertNotEqual(interface_violations(_mutant_tail_positional), [])
        self.assertEqual(interface_violations(draw_ops_extra.revcloud), [])  # 干净对照

    def test_module_facts_agree_with_module(self):
        facts = draw_ops_extra.module_facts()
        self.assertEqual(facts["family"], draw_ops_extra.FAMILY)
        self.assertEqual(facts["op_count"], len(draw_ops_extra.OPERATIONS))
        self.assertEqual(facts["operations"], sorted(draw_ops_extra.OPERATIONS))
        self.assertEqual(facts["unprobed_covered"], ["revcloud", "osnap_switch", "ortho_polar", "dyninput"])
        self.assertTrue(facts["integer_nm"])
        for name in draw_ops_extra.OPERATIONS:
            self.assertIn(name, facts["state_ops"] + facts["geometry_ops"])


# --------------------------------------------------------------------------------------
# 二、修订云线（几何）
# --------------------------------------------------------------------------------------


class RevcloudTests(FamilyTestCase):
    def test_rect_cloud_geometry_and_arc_length(self):
        sk = new_sketch()
        side, arc = 40 * MM, 10 * MM
        out = call("revcloud_rect", sk, center_nm=(0, 0), width_nm=side, height_nm=side,
                   arc_length_nm=arc)
        self.assertEqual(len(out), 1)
        entity = entity_of(sk, out[0])
        self.assertEqual(set(entity), L1_FIELDS, "must not add a sixth L1 field")
        self.assertEqual(entity["kind"], "polyline")
        self.assertTrue(entity["geom"]["closed"])
        pts = [(int(p[0]), int(p[1])) for p in entity["geom"]["points"]]
        self.assertEqual(len(pts), 128, "40mm side / 10mm arc / 8 samples -> 4*32 vertices")
        self.assertNotEqual(pts[0], pts[-1], "closed ring must not repeat the first vertex")
        chord_max = max(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        self.assertLessEqual(chord_max, arc, "sampled chords stay under arc_length_nm")
        extent = max(max(abs(x), abs(y)) for x, y in pts)
        self.assertEqual(extent, side // 2 + arc // 2,
                         "scallop radius = step/2 = arc_length_nm/2 -> apex at half + 5mm")
        apexes = sum(1 for x, y in pts if max(abs(x), abs(y)) == extent)
        self.assertEqual(apexes, 16, "4 sides x 4 chunks of 10mm => 16 scallop apexes")
        self.assertTrue(any(max(abs(x), abs(y)) > side // 2 for x, y in pts),
                        "scallops must bulge outward from the rectangle")

    def test_open_path_cloud_keeps_direction(self):
        sk = new_sketch()
        out = call("revcloud", sk, points_nm=[(0, 0), (30 * MM, 0)], arc_length_nm=10 * MM,
                   closed=False, bulge_samples=4)
        entity = entity_of(sk, out[0])
        self.assertFalse(entity["geom"]["closed"])
        ys = [int(p[1]) for p in entity["geom"]["points"]]
        self.assertTrue(all(y >= 0 for y in ys), "open path bulges left of travel (dy >= 0)")
        self.assertTrue(any(y > 0 for y in ys))

    def test_revcloud_errors(self):
        sk = new_sketch()
        self.assert_reason("not_integer_nm", call, "revcloud", sk,
                           points_nm=[(0, 0), (10 * MM, 0)], arc_length_nm=1.5)
        self.assert_reason("non_positive_nm", call, "revcloud", sk,
                           points_nm=[(0, 0), (10 * MM, 0)], arc_length_nm=0)
        self.assert_reason("bad_revcloud_path", call, "revcloud", sk,
                           points_nm=[(0, 0), (10 * MM, 0)], arc_length_nm=MM, closed=True)
        self.assert_reason("not_integer_nm", call, "revcloud", sk,
                           points_nm=[(0, 0), (10 * MM, 0)], arc_length_nm=True)
        self.assertEqual(entities(sk), [], "failed calls must not leave geometry behind")


# --------------------------------------------------------------------------------------
# 三、对象捕捉开关 + 真几何
# --------------------------------------------------------------------------------------


class OsnapTests(FamilyTestCase):
    def setUp(self):
        self.sk = new_sketch()
        add_line(self.sk, 0, 0, 10 * MM, 0)
        add_circle(self.sk, 20 * MM, 0, 10 * MM)

    def test_default_off_and_toggle_state(self):
        self.assert_reason("osnap_disabled", call, "osnap_probe", self.sk,
                           point_nm=(1 * MM, 1 * MM))
        out = call("osnap_toggle", self.sk, enabled=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], state_of(self.sk)["modes"]["osnap"]["uuid"],
                         "state op must return the state record uuid")
        call("osnap_toggle", self.sk, enabled=False)
        self.assert_reason("osnap_disabled", call, "osnap_probe", self.sk,
                           point_nm=(1 * MM, 1 * MM))

    def test_endpoint_midpoint_center_nearest(self):
        call("osnap_toggle", self.sk, enabled=True)
        hit = call("osnap_probe", self.sk, point_nm=(1 * MM, 1 * MM), tolerance_nm=2 * MM,
                   kinds=["endpoint", "midpoint", "center"])
        geom = entity_of(self.sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"], geom["snap_kind"]), (0, 0, "endpoint"))
        hit = call("osnap_probe", self.sk, point_nm=(5 * MM, 100_000), tolerance_nm=2 * MM)
        geom = entity_of(self.sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"], geom["snap_kind"]), (5 * MM, 0, "midpoint"),
                         "midpoint ties with nearest (same point) -> priority wins")
        hit = call("osnap_probe", self.sk, point_nm=(20 * MM + 100_000, 0), tolerance_nm=2 * MM)
        geom = entity_of(self.sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"], geom["snap_kind"]), (20 * MM, 0, "center"))
        hit = call("osnap_probe", self.sk, point_nm=(31 * MM, 0), tolerance_nm=MM,
                   kinds=["nearest"])
        geom = entity_of(self.sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"], geom["snap_kind"]), (30 * MM, 0, "nearest"))

    def test_kinds_filter_and_no_hit(self):
        call("osnap_toggle", self.sk, enabled=True)
        call("osnap_kinds_set", self.sk, kinds=["midpoint"])
        hit = call("osnap_probe", self.sk, point_nm=(1 * MM, 1 * MM), tolerance_nm=5 * MM)
        geom = entity_of(self.sk, hit[0])["geom"]
        self.assertEqual(geom["snap_kind"], "midpoint",
                         "endpoint disabled -> midpoint takes over (negative control)")
        self.assert_reason("osnap_no_hit", call, "osnap_probe", self.sk,
                           point_nm=(90 * MM, 90 * MM), tolerance_nm=MM)
        self.assert_reason("unknown_token", call, "osnap_kinds_set", self.sk, kinds=["nope"])

    def test_kind_flags_survive_master_switch(self):
        call("osnap_toggle", self.sk, enabled=True)
        call("osnap_kinds_set", self.sk, kinds=["quadrant"])
        call("osnap_toggle", self.sk, enabled=False)
        call("osnap_toggle", self.sk, enabled=True)
        hit = call("osnap_probe", self.sk, point_nm=(30 * MM, 100_000), tolerance_nm=MM)
        self.assertEqual(entity_of(self.sk, hit[0])["geom"]["snap_kind"], "quadrant")


# --------------------------------------------------------------------------------------
# 四、正交与极轴追踪
# --------------------------------------------------------------------------------------


class OrthoPolarTests(FamilyTestCase):
    def test_ortho_locks_and_returns_record_uuid(self):
        sk = new_sketch()
        out = call("ortho_polar_set", sk, mode="ortho", enabled=True)
        self.assertEqual(out[0], state_of(sk)["modes"]["ortho"]["uuid"])
        hit = call("ortho_polar_apply", sk, base_nm=(0, 0), point_nm=(10 * MM, 100_000))
        geom = entity_of(sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"], geom["track_mode"]), (10 * MM, 0, "ortho"))

    def test_polar_takes_over_when_ortho_off(self):
        sk = new_sketch()
        call("ortho_polar_set", sk, mode="polar", enabled=True, increments_deg=(90.0,))
        hit = call("ortho_polar_apply", sk, base_nm=(0, 0), point_nm=(10 * MM, 100_000))
        geom = entity_of(sk, hit[0])["geom"]
        self.assertEqual(geom["track_mode"], "polar")
        self.assertEqual(geom["y"], 0)
        self.assertEqual(geom["x"], 10_000_500, "projected onto the 0° ray at the same radius")

    def test_ortho_priority_and_not_tracked(self):
        sk = new_sketch()
        call("ortho_polar_set", sk, mode="ortho", enabled=True)
        call("ortho_polar_set", sk, mode="polar", enabled=True, increments_deg=(90.0,))
        hit = call("ortho_polar_apply", sk, base_nm=(0, 0), point_nm=(10 * MM, 100_000))
        self.assertEqual(entity_of(sk, hit[0])["geom"]["track_mode"], "ortho",
                         "ortho has priority when both are enabled")
        call("ortho_polar_set", sk, mode="ortho", enabled=False)
        self.assert_reason("not_tracked", call, "ortho_polar_apply", sk,
                           base_nm=(0, 0), point_nm=(10 * MM, 10 * MM))
        self.assert_reason("tracking_disabled", call, "ortho_polar_apply", sk,
                           base_nm=(0, 0), point_nm=(10 * MM, 0), mode="ortho")
        fresh = new_sketch()
        self.assert_reason("no_tracking_mode", call, "ortho_polar_apply", fresh,
                           base_nm=(0, 0), point_nm=(10 * MM, 0))

    def test_bad_increments_and_mode(self):
        sk = new_sketch()
        self.assert_reason("unknown_token", call, "ortho_polar_set", sk, mode="otrack")
        self.assert_reason("bad_increments", call, "ortho_polar_set", sk, mode="ortho",
                           increments_deg=(45.0,))
        self.assert_reason("bad_increments", call, "ortho_polar_set", sk, mode="polar",
                           increments_deg=(0.0,))
        self.assert_reason("bad_increments", call, "ortho_polar_set", sk, mode="polar",
                           increments_deg=(45.0, 45.0))


# --------------------------------------------------------------------------------------
# 五、动态输入
# --------------------------------------------------------------------------------------


class DyninputTests(FamilyTestCase):
    def test_length_angle_to_point(self):
        sk = new_sketch()
        out = call("dyninput_set", sk, enabled=True, fields=["length", "angle"])
        self.assertEqual(out[0], state_of(sk)["modes"]["dyninput"]["uuid"])
        hit = call("dyninput_point", sk, start_nm=(0, 0), length_nm=10 * MM, angle_deg=90.0)
        geom = entity_of(sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"]), (0, 10 * MM), "10mm @ 90deg == (0, 10000000) nm")
        self.assertEqual(geom["length_nm"], 10 * MM)
        self.assertEqual(entity_of(sk, hit[0])["kind"], "point")

    def test_offset_start_and_disabled(self):
        sk = new_sketch()
        hit = call("dyninput_point", sk, start_nm=(5 * MM, 5 * MM), length_nm=3 * MM, angle_deg=0.0)
        geom = entity_of(sk, hit[0])["geom"]
        self.assertEqual((geom["x"], geom["y"]), (8 * MM, 5 * MM))
        call("dyninput_set", sk, enabled=False)
        self.assert_reason("dyninput_disabled", call, "dyninput_point", sk,
                           start_nm=(0, 0), length_nm=MM, angle_deg=0.0)
        self.assert_reason("unknown_token", call, "dyninput_set", sk, fields=["length", "nope"])
        self.assert_reason("negative_length", call, "dyninput_point", sk,
                           start_nm=(0, 0), length_nm=-1, angle_deg=0.0)


# --------------------------------------------------------------------------------------
# 六、模块契约：真件、L1 不变量、状态可解析、__main__ 判定行
# --------------------------------------------------------------------------------------


class ModuleContractTests(FamilyTestCase):
    def test_module_is_the_real_file(self):
        path = Path(draw_ops_extra.__file__)
        self.assertEqual(path.name, "draw_ops_extra.py")
        self.assertEqual(path.parent.name, "ops")
        self.assertEqual(draw_ops_extra.FAMILY, "draw_ops_extra")
        self.assertNotEqual(draw_ops_extra.FAMILY, "draw_ops",
                            "must not hijack the existing draw_ops family gate scope")
        for name, op in draw_ops_extra.OPERATIONS.items():
            self.assertEqual(op.__module__, "cadcore.ops.draw_ops_extra",
                             "%s must be implemented here, not an alias stub" % name)

    def test_l1_invariants_hold_with_state_and_geometry(self):
        sk = new_sketch()
        call("revcloud_rect", sk, center_nm=(0, 0), width_nm=40 * MM, height_nm=20 * MM,
             arc_length_nm=10 * MM)
        call("osnap_toggle", sk, enabled=True)
        call("osnap_kinds_set", sk, kinds=["endpoint"])
        add_line(sk, 0, 0, 10 * MM, 0)
        call("osnap_probe", sk, point_nm=(1 * MM, 1 * MM), tolerance_nm=2 * MM)
        call("ortho_polar_set", sk, mode="ortho", enabled=True)
        call("ortho_polar_apply", sk, base_nm=(0, 0), point_nm=(10 * MM, 100_000))
        call("dyninput_point", sk, start_nm=(0, 0), length_nm=10 * MM, angle_deg=90.0)
        problems = docmodel.check_invariants(new_doc(sk))
        self.assertEqual(problems, [], "L1 invariants must stay clean")
        for entity in entities(sk):
            self.assertEqual(set(entity), L1_FIELDS)
        self.assertEqual(sorted(state_of(sk)["modes"]), list(STATE_MODES))
        for name, record in state_of(sk)["modes"].items():
            self.assertIsInstance(record["uuid"], str, name)
            self.assertIsInstance(record["enabled"], bool, name)

    def test_document_input_form(self):
        sk = new_sketch()
        doc = new_doc(sk)
        out = call("revcloud", doc, points_nm=[(0, 0), (20 * MM, 0), (20 * MM, 20 * MM)],
                   arc_length_nm=5 * MM)
        self.assertEqual(len(out), 1)
        self.assertEqual(entity_of(sk, out[0])["kind"], "polyline")
        self.assert_reason("no_sketch", call, "revcloud", {"unit": "mm", "sketches": []},
                           points_nm=[(0, 0), (1, 1)], arc_length_nm=MM)

    def test_selftest_and_main_verdict_line(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = draw_ops_extra.selftest()
        self.assertEqual(rc, 0, stream.getvalue())
        self.assertNotIn("problem=", stream.getvalue())
        proc = subprocess.run([sys.executable, str(Path(draw_ops_extra.__file__))],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertRegex(proc.stdout, r"EXTRA_OPS verdict=PASS ops=9\b")
        self.assertNotIn("FAIL", proc.stdout)


if __name__ == "__main__":
    unittest.main()
