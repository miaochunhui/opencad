#!/usr/bin/env python3
"""tests.test_sw_pattern_refgeom_ops -- family sw_pattern_refgeom 的独立单测（测试席自持，新文件）。

落盘状态（先读这一段，它决定今天跑出来的是什么）
------------------------------------------------
本席开工时 ``cadcore/ops/`` **目录不存在**，``cadcore/ops/sw_pattern_refgeom.py`` **未落盘**
（属主 = 族模块席）。所以本文件是照**冻结接口**写的独立单测，不是对已有实现的复述：

    FAMILY = "<id>"                        # 本文件期望 sw_pattern_refgeom / sw-pattern-refgeom
    OPERATIONS: dict[str, Callable]        # 算子名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 一律返回 uuid 列表
    长度一律整数纳米；错误抛 cadcore.errors.CadError(reason=...)

三态记账（禁静默假绿；口径 0=真为零 / -1=未采 / NA=不适用）
----------------------------------------------------------
1. 族模块缺席 ⇒ 全族行为用例**显式 skip**，reason 里带 ``NA``；本席**没有**跑出任何通过的行为判定行，
   收尾 raw_proof 里的判定行是 ``NA(未采)``，不是 PASS。
2. ``PrerequisiteTest`` 今天**真跑**：它只依赖已落盘的兄弟模块（errors / edit.modify / sketch.primitives /
   precision.osnap）⇒ 证明这组夹具读的是**真件**，不是自造方言（E2.5 夹具自证）。
3. 族模块在场后，``ContractTest`` 先钉死结构契约；行为用例做**精确整数纳米**断言（坐标 / 长度 / 角度 /
   数量）；边界用例断言抛 ``CadError`` 且 ``reason`` 非空。
4. 族模块在场但 OPERATIONS 里连候选算子名都没有 ⇒ **真报红**（不许 skip 掩盖）。

未落盘期的唤醒层（唯一可改点，断言一个字都没放宽）
--------------------------------------------------
冻结接口只规定 ``OPERATIONS`` 与 ``**kwargs`` 形态，**没规定算子名与关键字名**。本文件把候选清单集中在
``_OP_ALIASES`` / ``_*_variants`` 两张表里：每个用例都**真调用**算子，第一个不抛 ``TypeError`` 的候选签名生效。
边界用例与同文件的绿用例**共用同一份候选签名** ⇒ 若某个签名其实不被算子接受，绿用例会先红，
边界用例不会靠"参数不认识"独自假绿（假绿自检）。
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from typing import Any, Iterator

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.edit import modify as edit_modify  # noqa: E402
from cadcore.errors import CadError as BaseCadError  # noqa: E402
from cadcore.precision import osnap as precision_osnap  # noqa: E402
from cadcore.sketch import primitives as sketch_primitives  # noqa: E402

MM = 1_000_000  # 1 毫米 = 1e6 纳米；本文件所有长度都是整数纳米


def _load_family() -> tuple[Any, str]:
    """导入族模块；缺席时返回 ``(None, 诊断串)``（不吞异常，也不伪造一个替身模块）。"""
    try:
        from cadcore.ops import sw_pattern_refgeom as module

        return module, ""
    except Exception as exc:  # noqa: BLE001 -- 缺席就是 NA，诊断串必须原样带出去
        return None, f"{type(exc).__name__}: {exc}"


_FAMILY_MODULE, _IMPORT_DIAG = _load_family()
_FAMILY_PRESENT = _FAMILY_MODULE is not None

NA_REASON = (
    "NA(uncollected): cadcore/ops/sw_pattern_refgeom.py is not on disk yet "
    f"[{_IMPORT_DIAG}] -- behaviour cases stay uncollected, never rewritten as PASS"
)

requires_family = unittest.skipUnless(_FAMILY_PRESENT, NA_REASON)

_OP_ALIASES: dict[str, tuple[str, ...]] = {
    "linear": ("array_linear", "linear_pattern", "pattern_linear"),
    "circular": ("array_circular", "circular_pattern", "pattern_circular"),
    "mirror": ("mirror_feature", "mirror", "pattern_mirror"),
    "plane": ("datum_plane", "add_datum_plane", "create_datum_plane"),
    "axis": ("datum_axis", "add_datum_axis", "create_datum_axis"),
    "point": ("datum_point", "add_datum_point", "create_datum_point"),
    "measure": ("measure", "measure_distance", "add_measure"),
}

#: FAMILY 的两种写法在仓内都在用（门用连字符 `sw-pattern-refgeom`，模块名用下划线）⇒ 两者都认。
_FAMILY_IDS: tuple[str, ...] = ("sw_pattern_refgeom", "sw-pattern-refgeom")


# --------------------------------------------------------------------------------------
# 夹具：L1 草图 / 文档两读音（算子签名是 sk_or_doc，两种读法都要能被接住）
# --------------------------------------------------------------------------------------


def _sketch_doc(*entities: dict[str, Any]) -> dict[str, Any]:
    """一个内存里的 L1 目标：既当 sketch 读（``entities``），也当 doc 读（``sketches``）。

    自引用是**有意**的（同一个对象两读）⇒ 下面的遍历器全部带环保护。
    """
    sk: dict[str, Any] = {"entities": list(entities), "constraints": [], "dimensions": []}
    sk["sketches"] = [sk]
    return sk


def _line(uuid: str, p1: tuple[int, int], p2: tuple[int, int], layer: str = "0") -> dict[str, Any]:
    return {
        "uuid": uuid,
        "kind": "line",
        "layer": layer,
        "construction": False,
        "geom": {"x1": int(p1[0]), "y1": int(p1[1]), "x2": int(p2[0]), "y2": int(p2[1])},
    }


def _walk_scalars(node: Any, seen: set[int] | None = None) -> Iterator[Any]:
    """深度遍历所有标量（字典的 key 不算）；带 id 环保护。"""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node.values():
            yield from _walk_scalars(value, seen)
    elif isinstance(node, (list, tuple, set)):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node:
            yield from _walk_scalars(value, seen)
    else:
        yield node


def _records(node: Any, seen: set[int] | None = None) -> Iterator[dict[str, Any]]:
    """深度遍历所有带 ``uuid`` 的字典记录；带 id 环保护。"""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        if "uuid" in node:
            yield node
        for value in node.values():
            yield from _records(value, seen)
    elif isinstance(node, (list, tuple)):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node:
            yield from _records(value, seen)


def _uuid_present(target: Any, uuid: str) -> bool:
    return any(value == uuid for value in _walk_scalars(target))


def _record_for(target: Any, uuid: str) -> dict[str, Any] | None:
    for record in _records(target):
        if record.get("uuid") == uuid:
            return record
    return None


def _contains_number(target: Any, uuid: str, expected: int | float) -> bool:
    """``uuid`` 所属记录里（找不到记录则整棵树里）是否出现数值 ``expected``（逐位相等）。

    "落点字段名"未冻结 ⇒ 用"这个数必须出现在这条记录里"当硬断言，既不给实现强加字段名，也不放宽数值。
    """
    record = _record_for(target, uuid)
    for value in _walk_scalars(record if record is not None else target):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if float(value) == float(expected):
            return True
    return False


def _by_uuid(target: Any, uuid: str) -> dict[str, Any] | None:
    return _record_for(target, uuid)


def _endpoints(target: Any, uuid: str) -> tuple[tuple[int, int], tuple[int, int]]:
    entity = _by_uuid(target, uuid)
    assert entity is not None, f"uuid {uuid!r} is not in the target document"
    geom = entity.get("geom") or {}
    return ((int(geom["x1"]), int(geom["y1"])), (int(geom["x2"]), int(geom["y2"])))


def _start_points(target: Any, uuids: list[str]) -> list[tuple[int, int]]:
    return [_endpoints(target, uuid)[0] for uuid in uuids]


def _angle_deg(point: tuple[int, int]) -> float:
    return round(math.degrees(math.atan2(point[1], point[0])) % 360.0, 6)


# --------------------------------------------------------------------------------------
# 唤醒层：候选签名（每族算子一张表；全部是真调用，不做任何本地模拟）
# --------------------------------------------------------------------------------------


def _linear_variants(refs: list[str], *, dx_nm: int, dy_nm: int = 0, count: int, **extra: Any) -> tuple[dict, ...]:
    out = [
        dict(refs=list(refs), dx_nm=dx_nm, dy_nm=dy_nm, count=count, **extra),
        dict(refs=list(refs), spacing_nm=(dx_nm, dy_nm), count=count, **extra),
    ]
    if dy_nm == 0:
        out.append(dict(refs=list(refs), spacing_nm=dx_nm, count=count, axis="x", **extra))
    return tuple(out)


def _circular_variants(refs: list[str], *, center_nm: tuple[int, int], count: int, angle_deg: float) -> tuple[dict, ...]:
    return (
        dict(refs=list(refs), center_nm=center_nm, count=count, angle_deg=angle_deg),
        dict(refs=list(refs), center_nm=center_nm, count=count, total_angle_deg=angle_deg),
        dict(refs=list(refs), axis="z", center_nm=center_nm, count=count, angle_deg=angle_deg),
    )


def _mirror_variants(refs: list[str], *, p1_nm: tuple[int, int], p2_nm: tuple[int, int],
                     keep_original: bool = True) -> tuple[dict, ...]:
    return (
        dict(refs=list(refs), p1_nm=p1_nm, p2_nm=p2_nm, keep_original=keep_original),
        dict(refs=list(refs), plane_p1_nm=p1_nm, plane_p2_nm=p2_nm, keep_original=keep_original),
    )


def _point_variants(point_nm: tuple[int, int]) -> tuple[dict, ...]:
    return (dict(point_nm=point_nm), dict(at_nm=point_nm), dict(p1_nm=point_nm))


def _axis_variants(p1_nm: tuple[int, int], p2_nm: tuple[int, int]) -> tuple[dict, ...]:
    return (dict(p1_nm=p1_nm, p2_nm=p2_nm), dict(points_nm=(p1_nm, p2_nm)))


def _plane_variants(offset_nm: int) -> tuple[dict, ...]:
    return (dict(offset_nm=offset_nm), dict(distance_nm=offset_nm),
            dict(offset_nm=offset_nm, normal_nm=(0, 1)))


def _measure_variants(refs: list[str]) -> tuple[dict, ...]:
    first, second = refs[0], refs[1]
    return (dict(refs=[first, second]), dict(ref_a=first, ref_b=second), dict(refs=[first], ref_b=second))


# --------------------------------------------------------------------------------------
# 公共夹具基类
# --------------------------------------------------------------------------------------


class FamilyCase(unittest.TestCase):
    """解析算子 + 三类断言（返回值形态 / 精确数值 / 异常）。"""

    def _op(self, logical: str):
        ops = getattr(_FAMILY_MODULE, "OPERATIONS", None)
        self.assertIsInstance(ops, dict, f"OPERATIONS must be dict[str, Callable], got {type(ops).__name__}")
        for name in _OP_ALIASES[logical]:
            candidate = ops.get(name)
            if callable(candidate):
                return candidate
        self.fail(f"no {logical!r} operation in OPERATIONS: expected one of {_OP_ALIASES[logical]}, got {sorted(ops)}")

    def _invoke(self, logical: str, target: Any, variants: tuple[dict, ...], *, label: str = "") -> list[str]:
        """真调用；候选签名全部 ``TypeError`` ⇒ 报红（冻结接口是 ``**kwargs``，不是"没有这个算子"）。"""
        fn = self._op(logical)
        type_errors: list[str] = []
        for kwargs in variants:
            try:
                result = fn(target, **kwargs)
            except TypeError as exc:
                type_errors.append(f"{sorted(kwargs)} -> {exc}")
                continue
            self.assertIsInstance(result, list, f"{label or logical}: op must return list[str], got {type(result).__name__}")
            for uuid in result:
                self.assertIsInstance(uuid, str, f"{label or logical}: returned uuid must be str, got {uuid!r}")
                self.assertTrue(uuid, f"{label or logical}: returned uuid must be non-empty")
            return result
        self.fail(f"{label or logical}: no candidate signature accepted by the family op; tried: "
                  + " || ".join(type_errors))
        raise AssertionError("unreachable")

    def _invoke_optional(self, logical: str, target: Any, variants: tuple[dict, ...]) -> list[str] | None:
        """可选的模式开关（如 mirror 的原地模式）：签名都不接受 ⇒ ``None``（调用方记 NA，不假绿）。"""
        fn = self._op(logical)
        for kwargs in variants:
            try:
                return fn(target, **kwargs)
            except TypeError:
                continue
        return None

    def _expect_cad_error(self, logical: str, target: Any, variants: tuple[dict, ...], *, label: str = "") -> None:
        """非法入参必须抛 ``CadError`` 且 ``reason`` 非空；返回正常 == 真报红。"""
        fn = self._op(logical)
        type_errors: list[str] = []
        for kwargs in variants:
            try:
                fn(target, **kwargs)
            except BaseCadError as exc:
                self.assertIsInstance(exc.reason, str)
                self.assertTrue(exc.reason, f"{label or logical}: CadError.reason must be a non-empty machine string")
                return
            except TypeError as exc:
                type_errors.append(f"{sorted(kwargs)} -> {exc}")
                continue
            else:
                self.fail(f"{label or logical}: expected CadError for kwargs={sorted(kwargs)}, op returned normally")
        self.fail(f"{label or logical}: no candidate signature accepted; tried: " + " || ".join(type_errors))


# --------------------------------------------------------------------------------------
# 今天就能真跑的用例：组合面是真件（E2.5 夹具自证）
# --------------------------------------------------------------------------------------


class PrerequisiteTest(FamilyCase):
    """不依赖族模块；证明组合面（兄弟模块）与错误族是真件，本组用例考的不是方言。"""

    def test_composition_surface_is_real(self) -> None:
        surfaces = (
            (edit_modify, ("move", "copy", "rotate", "scale", "mirror", "array_linear")),
            (sketch_primitives, ("add_rectangle", "add_polygon", "add_slot", "closed_profiles")),
            (precision_osnap, ("snap", "ortho_lock", "polar_track", "grid_snap")),
        )
        for module, names in surfaces:
            for name in names:
                self.assertTrue(callable(getattr(module, name, None)),
                                f"{module.__name__}.{name} must be callable (composition surface)")

    def test_cad_error_carries_machine_readable_reason(self) -> None:
        exc = BaseCadError("probe", reason="probe_reason", field="x")
        self.assertEqual(exc.reason, "probe_reason")
        self.assertEqual(exc.as_dict()["type"], "CadError")
        self.assertIsInstance(exc, Exception)

    def test_primitives_rectangle_is_a_closed_profile(self) -> None:
        sk = _sketch_doc()
        uuids = sketch_primitives.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(2 * MM, MM))
        self.assertEqual(len(uuids), 4)
        loops = sketch_primitives.closed_profiles(sk)
        self.assertEqual(len(loops), 1)
        self.assertEqual(len(loops[0]), 4)

    def test_modify_array_linear_precedent_returns_count_minus_one_new_uuids(self) -> None:
        """本文件对"阵列只返回新 uuid、原位实例不返回"的假定，出自这条既有先例（真跑得出来）。"""
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = edit_modify.array_linear(sk, ["base"], rows=1, cols=3, dx_nm=10 * MM, dy_nm=0)
        self.assertEqual(len(created), 2)
        self.assertNotIn("base", created)
        self.assertEqual(sorted(point[0] for point in _start_points(sk, created)), [10 * MM, 20 * MM])
        self.assertEqual(_endpoints(sk, "base"), ((0, 0), (MM, 0)))


# --------------------------------------------------------------------------------------
# 结构契约
# --------------------------------------------------------------------------------------


@requires_family
class ContractTest(FamilyCase):

    def test_family_id_is_the_frozen_identifier(self) -> None:
        family = getattr(_FAMILY_MODULE, "FAMILY", None)
        self.assertIsInstance(family, str)
        self.assertIn(family, _FAMILY_IDS)

    def test_operations_are_callables_and_family_surface_is_complete(self) -> None:
        ops = _FAMILY_MODULE.OPERATIONS
        self.assertIsInstance(ops, dict)
        self.assertGreaterEqual(len(ops), 5)
        for name, fn in ops.items():
            self.assertIsInstance(name, str)
            self.assertTrue(name and not name.startswith("_"), f"operation name {name!r} must be public")
            self.assertTrue(callable(fn), f"OPERATIONS[{name!r}] must be callable")
        for logical in ("linear", "circular", "mirror", "point"):
            self.assertIsNotNone(self._op(logical), f"family must expose {logical!r}")


# --------------------------------------------------------------------------------------
# 线性阵列
# --------------------------------------------------------------------------------------


@requires_family
class LinearPatternTest(FamilyCase):

    def test_single_direction_count_and_exact_positions(self) -> None:
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = self._invoke("linear", sk, _linear_variants(["base"], dx_nm=10 * MM, count=3), label="linear.count3")
        self.assertEqual(len(created), 2, "count=3 keeps the source and returns the 2 new instances")
        self.assertEqual(sorted(point[0] for point in _start_points(sk, created)), [10 * MM, 20 * MM])
        for uuid in created:
            (x1, y1), (x2, y2) = _endpoints(sk, uuid)
            self.assertEqual((y1, y2), (0, 0))
            self.assertEqual(x2 - x1, MM, "spacing must not scale the instance")
        self.assertEqual(_endpoints(sk, "base"), ((0, 0), (MM, 0)), "source geometry must stay untouched")
        self.assertEqual(len(sk["entities"]), 3)

    def test_offset_vector_applies_per_instance(self) -> None:
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = self._invoke("linear", sk,
                               _linear_variants(["base"], dx_nm=10 * MM, dy_nm=5 * MM, count=3),
                               label="linear.2d")
        self.assertEqual(len(created), 2)
        self.assertEqual(sorted(_start_points(sk, created)), sorted([(10 * MM, 5 * MM), (20 * MM, 10 * MM)]))

    def test_returned_uuids_are_new_and_resolvable(self) -> None:
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = self._invoke("linear", sk, _linear_variants(["base"], dx_nm=7 * MM, count=4), label="linear.uuids")
        self.assertEqual(len(created), 3)
        self.assertNotIn("base", created)
        self.assertEqual(len(set(created)), len(created), "instance uuids must be distinct")
        for uuid in created:
            self.assertTrue(_uuid_present(sk, uuid), f"returned uuid {uuid!r} must be in the document")

    def test_count_below_two_and_zero_spacing_are_rejected(self) -> None:
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        self._expect_cad_error("linear", sk, _linear_variants(["base"], dx_nm=10 * MM, count=1), label="linear.count1")
        self._expect_cad_error("linear", sk, _linear_variants(["base"], dx_nm=10 * MM, count=0), label="linear.count0")
        self._expect_cad_error("linear", sk, _linear_variants(["base"], dx_nm=0, dy_nm=0, count=3),
                               label="linear.zero_spacing")

    def test_unknown_reference_and_non_integer_nanometre_are_rejected(self) -> None:
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        self._expect_cad_error("linear", sk, _linear_variants(["no-such-uuid"], dx_nm=10 * MM, count=3),
                               label="linear.unknown_ref")
        self._expect_cad_error("linear", sk, _linear_variants(["base"], dx_nm=1.5, count=3),
                               label="linear.float_nm")


# --------------------------------------------------------------------------------------
# 圆周阵列
# --------------------------------------------------------------------------------------


@requires_family
class CircularPatternTest(FamilyCase):

    def _expected_angle_sets(self, count: int, angle_deg: float) -> tuple[set[float], set[float]]:
        inclusive = {round(k * angle_deg / (count - 1), 6) % 360.0 for k in range(1, count)}
        exclusive = {round(k * angle_deg / count, 6) % 360.0 for k in range(1, count)}
        return inclusive, exclusive

    def test_full_turn_count_positions_and_radius(self) -> None:
        sk = _sketch_doc(_line("base", (10 * MM, 0), (12 * MM, 0)))
        created = self._invoke("circular", sk,
                               _circular_variants(["base"], center_nm=(0, 0), count=4, angle_deg=360.0),
                               label="circular.360")
        self.assertEqual(len(created), 3)
        starts = _start_points(sk, created)
        self.assertEqual(sorted(starts), sorted([(0, 10 * MM), (-10 * MM, 0), (0, -10 * MM)]))
        for point in starts:
            self.assertAlmostEqual(math.hypot(point[0], point[1]), 10 * MM, delta=1,
                                   msg="circular pattern must preserve the radius")
        self.assertEqual(_endpoints(sk, "base"), ((10 * MM, 0), (12 * MM, 0)))

    def test_limited_angle_equal_spacing(self) -> None:
        sk = _sketch_doc(_line("base", (10 * MM, 0), (12 * MM, 0)))
        created = self._invoke("circular", sk,
                               _circular_variants(["base"], center_nm=(0, 0), count=3, angle_deg=180.0),
                               label="circular.180")
        self.assertEqual(len(created), 2)
        got = {_angle_deg(point) for point in _start_points(sk, created)}
        inclusive, exclusive = self._expected_angle_sets(3, 180.0)
        self.assertIn(got, (inclusive, exclusive),
                      f"instances must be equally spaced over the swept angle; got {sorted(got)}")

    def test_angle_and_count_boundaries(self) -> None:
        sk = _sketch_doc(_line("base", (10 * MM, 0), (12 * MM, 0)))
        self._expect_cad_error("circular", sk,
                               _circular_variants(["base"], center_nm=(0, 0), count=3, angle_deg=0.0),
                               label="circular.angle0")
        self._expect_cad_error("circular", sk,
                               _circular_variants(["base"], center_nm=(0, 0), count=1, angle_deg=360.0),
                               label="circular.count1")

    def test_unknown_reference_is_rejected(self) -> None:
        sk = _sketch_doc(_line("base", (10 * MM, 0), (12 * MM, 0)))
        self._expect_cad_error("circular", sk,
                               _circular_variants(["ghost"], center_nm=(0, 0), count=4, angle_deg=360.0),
                               label="circular.unknown_ref")


# --------------------------------------------------------------------------------------
# 镜像
# --------------------------------------------------------------------------------------


@requires_family
class MirrorTest(FamilyCase):

    def test_mirror_about_x_axis_flips_y_exactly(self) -> None:
        sk = _sketch_doc(_line("base", (MM, 2 * MM), (3 * MM, 5 * MM)))
        created = self._invoke("mirror", sk,
                               _mirror_variants(["base"], p1_nm=(0, 0), p2_nm=(MM, 0), keep_original=True),
                               label="mirror.keep")
        self.assertEqual(len(created), 1)
        self.assertEqual(sorted(_endpoints(sk, created[0])),
                         sorted([(MM, -2 * MM), (3 * MM, -5 * MM)]))
        self.assertEqual(_endpoints(sk, "base"), ((MM, 2 * MM), (3 * MM, 5 * MM)),
                         "keep_original=True must not touch the source")

    def test_mirror_in_place_mode_returns_source_uuid(self) -> None:
        sk = _sketch_doc(_line("base", (MM, 2 * MM), (3 * MM, 5 * MM)))
        result = self._invoke_optional("mirror", sk,
                                       _mirror_variants(["base"], p1_nm=(0, 0), p2_nm=(MM, 0),
                                                        keep_original=False))
        if result is None:
            self.skipTest("NA: mirror op exposes no in-place mode in its frozen kwargs")
        self.assertEqual(result, ["base"])
        self.assertEqual(sorted(_endpoints(sk, "base")), sorted([(MM, -2 * MM), (3 * MM, -5 * MM)]))

    def test_degenerate_axis_is_rejected(self) -> None:
        sk = _sketch_doc(_line("base", (MM, 2 * MM), (3 * MM, 5 * MM)))
        self._expect_cad_error("mirror", sk,
                               _mirror_variants(["base"], p1_nm=(MM, MM), p2_nm=(MM, MM)),
                               label="mirror.degenerate_axis")

    def test_unknown_reference_is_rejected(self) -> None:
        sk = _sketch_doc(_line("base", (MM, 2 * MM), (3 * MM, 5 * MM)))
        self._expect_cad_error("mirror", sk,
                               _mirror_variants(["ghost"], p1_nm=(0, 0), p2_nm=(MM, 0)),
                               label="mirror.unknown_ref")


# --------------------------------------------------------------------------------------
# 参考几何（基准点 / 基准轴 / 基准面）
# --------------------------------------------------------------------------------------


@requires_family
class DatumTest(FamilyCase):

    def test_datum_point_returns_uuid_and_exact_coordinates(self) -> None:
        sk = _sketch_doc()
        created = self._invoke("point", sk, _point_variants((5 * MM, 7 * MM)), label="datum.point")
        self.assertEqual(len(created), 1)
        self.assertTrue(_uuid_present(sk, created[0]))
        self.assertTrue(_contains_number(sk, created[0], 5 * MM), "datum point x must be stored as 5e6 nm")
        self.assertTrue(_contains_number(sk, created[0], 7 * MM), "datum point y must be stored as 7e6 nm")

    def test_datum_axis_returns_uuid_for_two_points(self) -> None:
        sk = _sketch_doc()
        created = self._invoke("axis", sk, _axis_variants((0, 0), (0, 10 * MM)), label="datum.axis")
        self.assertEqual(len(created), 1)
        self.assertTrue(_uuid_present(sk, created[0]))
        self.assertTrue(_contains_number(sk, created[0], 10 * MM))

    def test_datum_plane_returns_uuid_with_exact_offset(self) -> None:
        sk = _sketch_doc()
        created = self._invoke("plane", sk, _plane_variants(5 * MM), label="datum.plane")
        self.assertEqual(len(created), 1)
        self.assertTrue(_uuid_present(sk, created[0]))
        self.assertTrue(_contains_number(sk, created[0], 5 * MM), "datum plane offset must be 5e6 nm")

    def test_non_integer_nanometre_is_rejected(self) -> None:
        sk = _sketch_doc()
        self._expect_cad_error("point", sk, _point_variants((1.5, 0)), label="datum.point_float_nm")
        self._expect_cad_error("plane", sk, _plane_variants(2.25), label="datum.plane_float_nm")


# --------------------------------------------------------------------------------------
# 测量
# --------------------------------------------------------------------------------------


@requires_family
class MeasureTest(FamilyCase):

    def test_measure_distance_is_exact_nanometres(self) -> None:
        sk = _sketch_doc(_line("a", (0, 0), (MM, 0)), _line("b", (30 * MM, 0), (31 * MM, 0)))
        created = self._invoke("measure", sk, _measure_variants(["a", "b"]), label="measure.distance")
        self.assertEqual(len(created), 1)
        self.assertTrue(_uuid_present(sk, created[0]))
        self.assertTrue(_contains_number(sk, created[0], 30 * MM),
                        "the measured 30 mm gap must land in the measurement record as 3e7 nm")

    def test_unknown_reference_is_rejected(self) -> None:
        sk = _sketch_doc(_line("a", (0, 0), (MM, 0)))
        self._expect_cad_error("measure", sk, _measure_variants(["a", "ghost"]), label="measure.unknown_ref")


# --------------------------------------------------------------------------------------
# 与已有命令组合（≥2 例）
# --------------------------------------------------------------------------------------


@requires_family
class CompositionTest(FamilyCase):

    def test_rectangle_then_pattern_stays_a_closed_profile(self) -> None:
        """组合 1：真件 add_rectangle 造闭合轮廓 ⇒ 族线性阵列 ⇒ 冻结的 closed_profiles 仍认得出 2 个闭环。"""
        sk = _sketch_doc()
        uuids = sketch_primitives.add_rectangle(sk, p1_nm=(0, 0), p2_nm=(MM, MM))
        self.assertEqual(len(uuids), 4)
        created = self._invoke("linear", sk, _linear_variants(uuids, dx_nm=10 * MM, count=2), label="compose.rect")
        self.assertEqual(len(created), 4, "patterning 4 edges by count=2 must yield 4 new edges")
        loops = sketch_primitives.closed_profiles(sk)
        self.assertEqual(len(loops), 2, "the patterned copy must still be a closed profile")
        self.assertEqual(sorted(len(loop) for loop in loops), [4, 4])

    def test_pattern_output_is_snappable_by_osnap(self) -> None:
        """组合 2：族阵列 ⇒ 真件 osnap 在 1 nm 容差内命中新实例端点（证明落盘坐标是整数纳米且精确）。"""
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = self._invoke("linear", sk, _linear_variants(["base"], dx_nm=10 * MM, count=2), label="compose.snap")
        self.assertEqual(len(created), 1)
        hit = precision_osnap.snap(sk, (10 * MM + 1, 0), kinds=("endpoint",), tolerance_nm=2)
        self.assertIsNotNone(hit, "patterned endpoint must be snappable")
        self.assertEqual(hit.kind, "endpoint")
        self.assertEqual(hit.point_nm, (10 * MM, 0))
        self.assertIn(created[0], hit.ref)

    def test_modify_array_then_family_measure(self) -> None:
        """组合 3：真件 edit.modify.array_linear 造 3 列 ⇒ 族 measure 量出 60 mm（数值硬断言）。"""
        sk = _sketch_doc(_line("base", (0, 0), (MM, 0)))
        created = edit_modify.array_linear(sk, ["base"], rows=1, cols=3, dx_nm=30 * MM, dy_nm=0)
        self.assertEqual(len(created), 2)
        far = max(created, key=lambda uuid: _endpoints(sk, uuid)[0][0])
        self.assertEqual(_endpoints(sk, far)[0][0], 60 * MM)
        measured = self._invoke("measure", sk, _measure_variants(["base", far]), label="compose.measure")
        self.assertEqual(len(measured), 1)
        self.assertTrue(_contains_number(sk, measured[0], 60 * MM),
                        "the gap between the source and the far instance is 60e6 nm")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
