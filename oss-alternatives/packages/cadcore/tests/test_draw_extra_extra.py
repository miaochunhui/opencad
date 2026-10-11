"""tests.test_draw_extra_extra —— DRAW-EXTRA-EXTRA 族（`cadcore.ops.draw_extra_extra`）单测。

本文件是**新文件侧的族接口尺子**：断言只打冻结接口与真几何行为，不 import 实现内部 helper
（只 import 冻结面 `FAMILY` / `OPERATIONS` / 两个公开消费口径 `point_on_xline` / `point_on_ray`）。

判分项（逐条对应用户要求）
--------------------------
1. 族接口冻结：`FAMILY` 非空、`OPERATIONS` 是 `dict[str, Callable]`；
   每个操作首个**位置**参叫 `sk_or_doc`，其余 KEYWORD_ONLY，返回 `list[str]`；
2. **构造线真几何**：`kind="xline"`，`geom` 无端点字段，`point_on_xline(geom, ±10^12 nm)` 都在几何上
   （双向无限）；反例：零方向向量报错；
3. **射线真几何**：`kind="ray"`，正向任意远可达，**反向抛 `behind_ray_origin`**（不静默）；
4. **样条近似**：`mode="arc"` 真的出弧（控制点在已知圆上 ⇒ 弧半径复算对得上量级）、共线输入
   **只出直线**（阴性对照：拟合不造假弧）、且明显压缩（真拟合不是逐点堆砌）；`mode="line"` 出单条折线；
   端点严格落控制点；超差**显式抛 `tolerance_unreachable`** 且**不留半个实体**（失败原子性）；
5. 每个返回 uuid 都能在草图里解析到；`docmodel.check_invariants()` 对本族全部新实体返回空；
6. 文件自带自检的判定行必须命中 `EXTRA_OPS verdict=PASS ops=N`（真跑子进程，不看文档）。

夹具自证：`test_fixture_is_real_module_not_stub` 证明被测对象就是该文件本身；
全程内存操作（临时字典/文档），不落盘、不碰别人的文件。
"""

from __future__ import annotations

import inspect
import math
import statistics
import subprocess
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore import docmodel  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import draw_extra_extra as mod  # noqa: E402
from cadcore.sketch import constraints  # noqa: E402

MM = 1_000_000
MODULE_PATH = _PKG_ROOT / "cadcore" / "ops" / "draw_extra_extra.py"
OPS = ("construction_line", "ray", "spline_approx")
END_POINT_KEYS = {"x1", "y1", "x2", "y2", "start", "end", "length", "length_nm"}


def _sketch() -> dict:
    """空草图（L1 字段齐备，uuid 合法）。"""
    return {"uuid": constraints.new_uuid(), "plane": "XY", "entities": [], "constraints": [],
            "dimensions": []}


def _doc_with(sketch: dict):
    """把草图装进真文档（不变量体检用）。"""
    doc = docmodel.new_document()
    doc.sketches = [sketch]
    return doc


def _geom(sketch: dict, uuid: str) -> dict:
    for entity in sketch["entities"]:
        if entity["uuid"] == uuid:
            return entity["geom"]
    raise AssertionError("uuid not found in sketch: %s" % uuid)


def _kind(sketch: dict, uuid: str) -> str:
    for entity in sketch["entities"]:
        if entity["uuid"] == uuid:
            return entity["kind"]
    raise AssertionError("uuid not found in sketch: %s" % uuid)


def _point_of(kind: str, geom: dict, which: str) -> tuple[float, float]:
    """实体端点：line 用 x1/y1|x2/y2；arc 由 start_deg/end_deg 现算（**独立复算，不抄模块**）。"""
    if kind == "line":
        return (float(geom["x1"]), float(geom["y1"])) if which == "start" \
            else (float(geom["x2"]), float(geom["y2"]))
    deg = float(geom["start_deg"] if which == "start" else geom["end_deg"])
    return (geom["cx"] + geom["r"] * math.cos(math.radians(deg)),
            geom["cy"] + geom["r"] * math.sin(math.radians(deg)))


def _seg_dist(point, a, b) -> float:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(point[0] - ax, point[1] - ay)
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length2))
    return math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))


def _arc_dist(point, geom) -> float:
    cx, cy, r = float(geom["cx"]), float(geom["cy"]), float(geom["r"])
    deg = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360.0
    if deg < float(geom["start_deg"]):
        deg += 360.0
    if float(geom["start_deg"]) <= deg <= float(geom["end_deg"]):
        return abs(math.hypot(point[0] - cx, point[1] - cy) - r)
    ends = [(cx + r * math.cos(math.radians(d)), cy + r * math.sin(math.radians(d)))
            for d in (float(geom["start_deg"]), float(geom["end_deg"]))]
    return min(math.hypot(point[0] - ex, point[1] - ey) for ex, ey in ends)


def _dist_to_chain(point, sketch: dict, uuids) -> float:
    best = math.inf
    for uuid in uuids:
        geom = _geom(sketch, uuid)
        kind = _kind(sketch, uuid)
        if kind == "line":
            best = min(best, _seg_dist(point, (geom["x1"], geom["y1"]), (geom["x2"], geom["y2"])))
        elif kind == "arc":
            best = min(best, _arc_dist(point, geom))
        else:
            raise AssertionError("unexpected kind %r" % kind)
    return best


class InterfaceTest(unittest.TestCase):
    """冻结接口。"""

    def test_fixture_is_real_module_not_stub(self) -> None:
        self.assertTrue(MODULE_PATH.is_file(), "module file missing: %s" % MODULE_PATH)
        self.assertEqual(Path(mod.__file__).resolve(), MODULE_PATH.resolve())

    def test_family_manifest(self) -> None:
        self.assertIsInstance(mod.FAMILY, str)
        self.assertTrue(mod.FAMILY.strip())
        self.assertIsInstance(mod.OPERATIONS, dict)
        for name, fn in mod.OPERATIONS.items():
            self.assertIsInstance(name, str)
            self.assertTrue(callable(fn), "%s is not callable" % name)
        self.assertEqual(set(mod.OPERATIONS), set(OPS))

    def test_signatures_are_frozen_shape(self) -> None:
        for name in OPS:
            fn = mod.OPERATIONS[name]
            params = list(inspect.signature(fn).parameters.values())
            self.assertTrue(params, name)
            self.assertEqual(params[0].name, "sk_or_doc", name)
            self.assertIn(params[0].kind, (params[0].POSITIONAL_ONLY,
                                           params[0].POSITIONAL_OR_KEYWORD), name)
            for extra in params[1:]:
                self.assertEqual(extra.kind, extra.KEYWORD_ONLY, "%s.%s" % (name, extra.name))

    def test_ops_callable_positionally_and_return_uuid_lists(self) -> None:
        sk = _sketch()
        out = mod.OPERATIONS["construction_line"](sk, point_nm=(0, 0), dir_deg=0.0)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 1)
        self.assertEqual(_geom(sk, out[0])["px"], 0)  # 位置参真传进去了
        second = mod.OPERATIONS["construction_line"](sk, point_nm=(0, 0), dir_deg=0.0)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second[0], out[0], "each call must create a new entity")
        self.assertEqual(len(sk["entities"]), 2)


class ConstructionLineTest(unittest.TestCase):
    """构造线：双向无限真几何。"""

    def test_bidirectional_no_endpoints(self) -> None:
        sk = _sketch()
        uuid = mod.construction_line(sk, point_nm=(3 * MM, -4 * MM), dir_deg=30.0)[0]
        geom = _geom(sk, uuid)
        self.assertEqual(_kind(sk, uuid), "xline")
        self.assertEqual(set(geom), {"px", "py", "dir_deg"})
        self.assertFalse(END_POINT_KEYS & set(geom), "infinite geometry must not carry endpoints")
        self.assertEqual((geom["px"], geom["py"]), (3 * MM, -4 * MM))
        near = mod.point_on_xline(geom, 10 ** 12)
        far = mod.point_on_xline(geom, -10 ** 12)
        self.assertNotEqual(near, far)
        self.assertNotEqual(near, (geom["px"], geom["py"]))
        # 双向：两个方向的任意远点都在几何上（方向角复算，独立于模块内部）
        for distance in (10 ** 9, -10 ** 9, 10 ** 12, -10 ** 12):
            px, py = mod.point_on_xline(geom, distance)
            dx = px - geom["px"]
            dy = py - geom["py"]
            cross = dx * math.sin(math.radians(30.0)) - dy * math.cos(math.radians(30.0))
            self.assertLess(abs(cross), 2.0, "distance=%d off-line by %r" % (distance, cross))

    def test_direction_vector_and_guard(self) -> None:
        sk = _sketch()
        uuid = mod.construction_line(sk, point_nm=(0, 0), direction=(1, 1))[0]
        self.assertAlmostEqual(_geom(sk, uuid)["dir_deg"], 45.0, places=9)
        with self.assertRaises(CadError) as ctx:
            mod.construction_line(sk, point_nm=(0, 0), direction=(0, 0))
        self.assertEqual(ctx.exception.reason, "direction_degenerate")
        with self.assertRaises(CadError) as ctx2:
            mod.construction_line(sk, point_nm=(0, 0))  # 两个方向键都不给
        self.assertEqual(ctx2.exception.reason, "direction_ambiguous")


class RayTest(unittest.TestCase):
    """射线：单方向无限真几何。"""

    def test_forward_infinite_backward_is_error(self) -> None:
        sk = _sketch()
        uuid = mod.ray(sk, start_nm=(1 * MM, 1 * MM), direction=(0, 1))[0]
        geom = _geom(sk, uuid)
        self.assertEqual(_kind(sk, uuid), "ray")
        self.assertEqual(set(geom), {"px", "py", "dir_deg"})
        self.assertFalse(END_POINT_KEYS & set(geom))
        self.assertEqual(mod.point_on_ray(geom, 10 ** 12)[1], 1 * MM + 10 ** 12)
        with self.assertRaises(CadError) as ctx:
            mod.point_on_ray(geom, -1)
        self.assertEqual(ctx.exception.reason, "behind_ray_origin")

    def test_invariants_clean_for_infinite_entities(self) -> None:
        sk = _sketch()
        mod.construction_line(sk, point_nm=(0, 0), dir_deg=17.5)
        mod.ray(sk, start_nm=(2 * MM, 0), dir_deg=90.0)
        self.assertEqual(docmodel.check_invariants(_doc_with(sk)), [])


class SplineApproxTest(unittest.TestCase):
    """样条近似：弧拟合 / 折线 / 公差与失败路径。"""

    CONTROL = [(0, 0), (10 * MM, 5 * MM), (20 * MM, -5 * MM), (30 * MM, 0)]

    def test_arc_mode_chain_endpoints_and_arcs(self) -> None:
        sk = _sketch()
        uuids = mod.spline_approx(sk, points_nm=self.CONTROL, tolerance_nm=20_000,
                                  samples_per_span=8)
        self.assertEqual(len(uuids), len(sk["entities"]))
        self.assertEqual(len(set(uuids)), len(uuids), "uuid must be resolvable and unique")
        self.assertGreaterEqual(len(uuids), 2)
        kinds = [_kind(sk, uuid) for uuid in uuids]
        self.assertTrue(set(kinds) <= {"line", "arc"}, kinds)
        self.assertIn("arc", kinds, "curved input must really produce arcs")
        # 真拟合的证据：实体条数远少于拟合点（3 段 × 32 点 = 97），不是逐点堆砌
        self.assertLessEqual(len(uuids), 40, "fit must compress, kinds=%s" % kinds)
        # 首末端点严格落控制点（Catmull-Rom 过点；取整误差 ≤ 3 nm）
        start = _point_of(kinds[0], _geom(sk, uuids[0]), "start")
        end = _point_of(kinds[-1], _geom(sk, uuids[-1]), "end")
        self.assertLessEqual(math.hypot(start[0] - self.CONTROL[0][0],
                                        start[1] - self.CONTROL[0][1]), 3.0)
        self.assertLessEqual(math.hypot(end[0] - self.CONTROL[-1][0],
                                        end[1] - self.CONTROL[-1][1]), 3.0)
        # 逐段首尾相接（链不裂）
        for index in range(len(uuids) - 1):
            tail = _point_of(kinds[index], _geom(sk, uuids[index]), "end")
            head = _point_of(kinds[index + 1], _geom(sk, uuids[index + 1]), "start")
            self.assertLessEqual(math.hypot(tail[0] - head[0], tail[1] - head[1]), 4.0,
                                 "gap at run %d" % index)
        # 每个控制点都落在几何链上（≤ 公差）
        for control in self.CONTROL:
            self.assertLessEqual(_dist_to_chain(control, sk, uuids), 20_000,
                                 "control=%s not covered" % (control,))
        # 弧的扫角必须在 (0, 180]
        for uuid, kind in zip(uuids, kinds):
            if kind == "arc":
                geom = _geom(sk, uuid)
                self.assertGreater(geom["r"], 0)
                self.assertGreater(geom["end_deg"] - geom["start_deg"], 0.0)
                self.assertLessEqual(geom["end_deg"] - geom["start_deg"], 180.0)

    def test_arc_mode_recovers_known_circle(self) -> None:
        """控制点取自已知圆 ⇒ 弧半径必须复算对得上量级（拟合不是装饰），且弧真覆盖这段圆。"""
        radius = 100 * MM
        control = [(round(radius * math.cos(math.radians(deg))),
                    round(radius * math.sin(math.radians(deg)))) for deg in range(0, 91, 15)]
        sk = _sketch()
        uuids = mod.spline_approx(sk, points_nm=control, tolerance_nm=50_000, samples_per_span=6)
        arcs = [_geom(sk, uuid) for uuid in uuids if _kind(sk, uuid) == "arc"]
        self.assertGreaterEqual(len(arcs), 2, "expected multiple arcs, got %d" % len(arcs))
        for geom in arcs:
            self.assertGreater(geom["r"], 0.75 * radius, "arc r=%r" % geom["r"])
            self.assertLess(geom["r"], 1.25 * radius, "arc r=%r" % geom["r"])
        median = statistics.median(geom["r"] for geom in arcs)
        self.assertLess(abs(median - radius) / radius, 0.15, "median r=%r" % median)
        sweep = sum(geom["end_deg"] - geom["start_deg"] for geom in arcs)
        self.assertGreaterEqual(sweep, 60.0, "arcs must cover the curved run, sweep=%r" % sweep)

    def test_collinear_input_yields_lines_only(self) -> None:
        """阴性对照：共线输入不许冒出假弧。"""
        control = [(0, 0), (10 * MM, 0), (25 * MM, 0), (40 * MM, 0)]
        sk = _sketch()
        uuids = mod.spline_approx(sk, points_nm=control, tolerance_nm=1, samples_per_span=8)
        self.assertTrue(uuids)
        self.assertEqual({_kind(sk, uuid) for uuid in uuids}, {"line"})

    def test_line_mode_single_polyline_through_control_points(self) -> None:
        sk = _sketch()
        uuids = mod.spline_approx(sk, points_nm=self.CONTROL, tolerance_nm=1_000_000,
                                  samples_per_span=4, mode="line")
        self.assertEqual(len(uuids), 1)
        self.assertEqual(_kind(sk, uuids[0]), "polyline")
        points = _geom(sk, uuids[0])["points"]
        self.assertEqual(len(points), 4 * (len(self.CONTROL) - 1) + 1)
        for index, control in enumerate(self.CONTROL):
            self.assertEqual(tuple(points[index * 4]), control)

    def test_tolerance_unreachable_is_explicit_and_atomic(self) -> None:
        control = [(0, 0), (50 * MM, 50 * MM), (100 * MM, 0)]
        for mode in ("line", "arc"):
            sk = _sketch()
            before = len(sk["entities"])
            with self.assertRaises(CadError) as ctx:
                mod.spline_approx(sk, points_nm=control, tolerance_nm=1,
                                  samples_per_span=1, mode=mode)
            self.assertEqual(ctx.exception.reason, "tolerance_unreachable", mode)
            self.assertIn("measured_nm", ctx.exception.details, mode)
            self.assertGreater(ctx.exception.details["measured_nm"], 1.0, mode)
            self.assertEqual(len(sk["entities"]), before, "failed call must not leave entities")

    def test_invalid_inputs_raise_cad_error_with_reason(self) -> None:
        sk = _sketch()
        cases = [
            (dict(points_nm=[(0, 0)], tolerance_nm=1), "spline_too_few_points"),
            (dict(points_nm=self.CONTROL, tolerance_nm=0), "invalid_length"),
            (dict(points_nm=self.CONTROL, tolerance_nm=0.5), "invalid_length"),
            (dict(points_nm=self.CONTROL, mode="bezier"), "unsupported_mode"),
            (dict(points_nm=self.CONTROL, samples_per_span=0), "invalid_integer"),
            (dict(points_nm=[(0, 0), (1.5, 0)]), "invalid_length"),
            (dict(points_nm=self.CONTROL, layer=""), "invalid_layer"),
        ]
        for kwargs, reason in cases:
            with self.assertRaises(CadError, msg=str(kwargs)) as ctx:
                mod.spline_approx(sk, **kwargs)
            self.assertEqual(ctx.exception.reason, reason, str(kwargs))
        self.assertEqual(len(sk["entities"]), 0, "no partial writes from rejected calls")

    def test_accepts_document_and_bare_mapping(self) -> None:
        sk = _sketch()
        doc = _doc_with(sk)
        uuid = mod.ray(doc, start_nm=(0, 0), dir_deg=0.0)[0]
        self.assertIn(uuid, [entity["uuid"] for entity in sk["entities"]])
        uuid2 = mod.construction_line({"sketches": [sk]}, point_nm=(0, 0), dir_deg=0.0)[0]
        self.assertEqual(_kind(sk, uuid2), "xline")
        with self.assertRaises(CadError) as ctx:
            mod.ray(sk, start_nm=(0, 0), dir_deg=0.0, sketch_index=5)
        self.assertEqual(ctx.exception.reason, "no_sketch")

    def test_invariants_clean_after_all_ops(self) -> None:
        sk = _sketch()
        mod.spline_approx(sk, points_nm=self.CONTROL, tolerance_nm=20_000)
        mod.spline_approx(sk, points_nm=self.CONTROL, tolerance_nm=1_000_000, mode="line")
        mod.construction_line(sk, point_nm=(0, 0), dir_deg=10.0)
        mod.ray(sk, start_nm=(0, 0), dir_deg=10.0)
        self.assertEqual(docmodel.check_invariants(_doc_with(sk)), [])


class ModuleSelftestTest(unittest.TestCase):
    """文件自带自检的判定行（真跑子进程）。"""

    def test_selftest_verdict_line(self) -> None:
        proc = subprocess.run([sys.executable, str(MODULE_PATH)], capture_output=True, text=True,
                              cwd=str(_PKG_ROOT), timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, proc.stdout)
        self.assertTrue(lines[-1].startswith("EXTRA_OPS verdict=PASS ops=%d" % len(mod.OPERATIONS)),
                        lines[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
