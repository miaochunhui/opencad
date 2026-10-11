"""test_b6_param_boundary_limits —— B6 参数边界：**计数型入参必须先拒后循环**（cadcore 侧）。

缺陷（实测，源：`/hf3fs-jd/data/tmp/cad-scan3/logs/` + 本席 HTTP 重放 `replay_transport.py`）
------------------------------------------------------------------------------------------
`POST /api/command {"command":"array_linear","args":{"rows":<10^309>,"cols":2,...}}`：
20 s 不返回（客户端超时 ⇒ 扫描器记 `transport_error`），此后**每一个** API 请求都超时
（工作区锁被 `for i in range(row_count)` 占死），服务端日志 0 traceback（不是崩溃，是挂住）。
同形：`add_polygon(sides=10^12)`（每个顶点建 line + coincident 约束）。

根因
----
计数入参（`rows` / `cols` / `sides` / `count`）是无界整数，**没有上限校验**就直接驱动
`range()` + 逐个 clone：有限时间跑不完 = 挂住，而不是"报错拒绝"。

判据（本文件，全部现算）
------------------------
1. 越界计数 ⇒ 立刻 `CadError(reason="invalid_argument")`，且**一个实体都不许建**（不是"建一半再报错"）；
2. 拒绝要**快**（墙钟预算 2 s）—— 挂住与拒绝在返回值上同形，所以必须量墙钟；
3. 上限内行为逐字不变：`rows==cols==1` ⇒ `[]`（真为零）、`2×2` ⇒ 3 个新 uuid、`sides=6` ⇒ 6 条线；
4. 上限是**唯一真源**（`sketch.constraints.MAX_ITEMS_PER_OPERATION` + `items_in_range`）：
   所有计数入口在 `MAX+1` 上同红、在 `MAX` 上不因这一条而红。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .            # 契约命令
    python3 tests/test_b6_param_boundary_limits.py -v     # 单跑本文件

本文件不写盘、不起服务、不改产品码。
"""

from __future__ import annotations

import time
import unittest

from cadcore.edit import modify as edit_modify
from cadcore.errors import CadError
from cadcore.ops import draw_ops
from cadcore.sketch import commands as cmds
from cadcore.sketch import constraints as cons
from cadcore.sketch import primitives as prim

MM = 1_000_000
HUGE = int("9" * 309)
#: 拒绝的墙钟预算：挂住的实现跑不完这个预算（不是"慢"，是"不返回"）。
REJECT_BUDGET_S = 2.0


def _fresh():
    """空的 L1 草图（三张表齐全，不借真件夹具）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def _seeded():
    sk = _fresh()
    line = cmds.add_line(sk, p1=(0, 0), p2=(10 * MM, 0), layer="0")
    return sk, line


class CountLimitTruthSourceTests(unittest.TestCase):
    """上限必须是**唯一真源**：一处改，所有计数入口一起变。"""

    def test_limit_constant_is_exported_and_positive(self) -> None:
        limit = cons.MAX_ITEMS_PER_OPERATION
        self.assertIsInstance(limit, int)
        self.assertGreater(limit, 0)
        self.assertTrue(cons.items_in_range(limit))
        self.assertFalse(cons.items_in_range(limit + 1))
        self.assertFalse(cons.items_in_range(-1))

    def test_edit_modify_count_uses_shared_ruler(self) -> None:
        limit = cons.MAX_ITEMS_PER_OPERATION
        self.assertEqual(edit_modify._as_count(limit, what="count"), limit)
        with self.assertRaises(CadError) as ctx:
            edit_modify._as_count(limit + 1, what="count")
        self.assertEqual(ctx.exception.reason, "invalid_argument")

    def test_draw_ops_count_uses_shared_ruler(self) -> None:
        limit = cons.MAX_ITEMS_PER_OPERATION
        self.assertEqual(draw_ops._count(limit, what="count", minimum=1, maximum=limit), limit)
        with self.assertRaises(CadError) as ctx:
            draw_ops._count(limit + 1, what="count", minimum=1, maximum=limit)
        self.assertEqual(ctx.exception.reason, "invalid_argument")


class ArrayLinearCountBoundaryTests(unittest.TestCase):
    """`array_linear`：无界 `rows` / `cols` / 乘积都必须**先拒后循环**。"""

    def assert_rejected_without_mutation(self, label: str, **kwargs) -> None:
        sk, line = _seeded()
        before = len(cons.sketch_entities(sk))
        started = time.monotonic()
        with self.assertRaises(CadError) as ctx:
            edit_modify.array_linear(sk, [line], **kwargs)
        elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.reason, "invalid_argument", label)
        self.assertLess(elapsed, REJECT_BUDGET_S,
                        f"{label}: 拒绝用了 {elapsed:.2f}s（超预算 ⇒ 说明它进了循环，不是拒绝）")
        self.assertEqual(len(cons.sketch_entities(sk)), before,
                         f"{label}: 被拒的调用**不许**留下任何实体")

    def test_rows_huge309_is_rejected(self) -> None:
        """扫描席的原始触发用例：rows=10^309（309 个 9）—— 修前请求永不返回。"""
        self.assert_rejected_without_mutation("rows_huge309", rows=HUGE, cols=2,
                                              dx_nm=MM, dy_nm=MM)

    def test_cols_huge309_is_rejected(self) -> None:
        self.assert_rejected_without_mutation("cols_huge309", rows=2, cols=HUGE,
                                              dx_nm=MM, dy_nm=MM)

    def test_rows_and_cols_huge309_is_rejected(self) -> None:
        self.assert_rejected_without_mutation("both_huge309", rows=HUGE, cols=HUGE,
                                              dx_nm=MM, dy_nm=MM)

    def test_product_over_limit_is_rejected(self) -> None:
        """每个入参都在上限内、但**乘积**超限 ⇒ 同样必须先拒（否则 10^10 次循环）。"""
        limit = cons.MAX_ITEMS_PER_OPERATION
        self.assert_rejected_without_mutation("product_over_limit",
                                              rows=limit, cols=limit, dx_nm=MM, dy_nm=MM)

    def test_limit_minus_one_product_still_accepted(self) -> None:
        """阳性对照：乘积正好在上限内 ⇒ 不因这一条被拒（上限不是"一律拒"）。"""
        sk, line = _seeded()
        created = edit_modify.array_linear(sk, [line], rows=1, cols=cons.MAX_ITEMS_PER_OPERATION,
                                           dx_nm=MM, dy_nm=0)
        self.assertEqual(len(created), cons.MAX_ITEMS_PER_OPERATION - 1)
        self.assertEqual(len(cons.sketch_entities(sk)), cons.MAX_ITEMS_PER_OPERATION)

    def test_one_by_one_is_still_truly_zero(self) -> None:
        """口径不变：`rows==cols==1` 是**真为零**，不是"没算"。"""
        sk, line = _seeded()
        self.assertEqual(edit_modify.array_linear(sk, [line], rows=1, cols=1, dx_nm=MM, dy_nm=MM), [])
        self.assertEqual(len(cons.sketch_entities(sk)), 1)

    def test_two_by_two_still_creates_three(self) -> None:
        sk, line = _seeded()
        created = edit_modify.array_linear(sk, [line], rows=2, cols=2, dx_nm=MM, dy_nm=MM)
        self.assertEqual(len(created), 3)
        self.assertEqual(len(cons.sketch_entities(sk)), 4)

    def test_family_wrapper_shares_the_same_ruler(self) -> None:
        """族入口（`ops.draw_ops.array_rect`，HTTP 面直通的真件）同红同绿。"""
        sk, line = _seeded()
        before = len(cons.sketch_entities(sk))
        started = time.monotonic()
        with self.assertRaises(CadError) as ctx:
            draw_ops.array_rect(sk, refs=[line], rows=HUGE, cols=2, dx_nm=MM, dy_nm=MM)
        elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.reason, "invalid_argument")
        self.assertLess(elapsed, REJECT_BUDGET_S)
        self.assertEqual(len(cons.sketch_entities(sk)), before)
        self.assertEqual(len(draw_ops.array_rect(sk, refs=[line], rows=2, cols=2,
                                                dx_nm=MM, dy_nm=MM)), 3)


class PolygonSidesBoundaryTests(unittest.TestCase):
    """`add_polygon`：边数同属计数型入参，同一条上限（每个顶点建 line + coincident）。

    注意异常类型：图元层抛的是 `sketch.primitives.CadError`（`GeometryError` 一族，
    **不是** `cadcore.errors.CadError` 的子类，见 primitives 的类注释）⇒ 这里按图元层接。
    """

    def test_sides_huge_is_rejected_before_any_line(self) -> None:
        sk = _fresh()
        started = time.monotonic()
        with self.assertRaises(prim.CadError) as ctx:
            prim.add_polygon(sk, center_nm=(0, 0), radius_nm=10 * MM, sides=10 ** 12)
        elapsed = time.monotonic() - started
        self.assertIn("sides", str(ctx.exception))
        self.assertIn(str(cons.MAX_ITEMS_PER_OPERATION), str(ctx.exception))
        self.assertLess(elapsed, REJECT_BUDGET_S)
        self.assertEqual(len(cons.sketch_entities(sk)), 0)

    def test_sides_huge309_is_rejected(self) -> None:
        sk = _fresh()
        with self.assertRaises(prim.CadError) as ctx:
            prim.add_polygon(sk, center_nm=(0, 0), radius_nm=10 * MM, sides=HUGE)
        self.assertIn("sides", str(ctx.exception))
        self.assertEqual(len(cons.sketch_entities(sk)), 0)

    def test_sides_over_limit_is_rejected(self) -> None:
        sk = _fresh()
        with self.assertRaises(prim.CadError):
            prim.add_polygon(sk, center_nm=(0, 0), radius_nm=10 * MM,
                             sides=cons.MAX_ITEMS_PER_OPERATION + 1)

    def test_hexagon_still_works(self) -> None:
        """阳性对照：合法边数一个字节都不变（6 条线 + 6 条 coincident）。"""
        sk = _fresh()
        uuids = prim.add_polygon(sk, center_nm=(0, 0), radius_nm=10 * MM, sides=6)
        self.assertEqual(len(uuids), 6)
        self.assertEqual(len(cons.sketch_entities(sk)), 6)
        self.assertEqual(len(cons.sketch_constraints(sk)), 6)

    def test_family_wrapper_polygon_shares_the_ruler(self) -> None:
        sk = _fresh()
        with self.assertRaises(CadError) as ctx:
            draw_ops.polygon_params(sk, center_nm=(0, 0), radius_nm=10 * MM, sides=10 ** 12)
        self.assertEqual(ctx.exception.reason, "invalid_argument")
        self.assertEqual(len(cons.sketch_entities(sk)), 0)

class SiblingCountEntryPointsTests(unittest.TestCase):
    """同族计数入口（圆周阵列 / 参考几何阵列）也吃同一条上限 —— 不许一条腿绿一条腿红。"""

    def test_circular_array_count_is_bounded(self) -> None:
        sk, line = _seeded()
        before = len(cons.sketch_entities(sk))
        started = time.monotonic()
        with self.assertRaises(CadError) as ctx:
            draw_ops.array_polar(sk, refs=[line], count=HUGE, center_nm=(0, 0),
                                 total_angle_deg=360.0)
        elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.reason, "invalid_argument")
        self.assertLess(elapsed, REJECT_BUDGET_S)
        self.assertEqual(len(cons.sketch_entities(sk)), before)

    def test_sw_pattern_count_is_bounded(self) -> None:
        from cadcore.ops import sw_pattern_refgeom as pat  # noqa: PLC0415 - 同族入口

        sk, line = _seeded()
        started = time.monotonic()
        with self.assertRaises(CadError) as ctx:
            pat.array_linear(sk, refs=[line], count=HUGE, dx_nm=MM)
        elapsed = time.monotonic() - started
        self.assertEqual(ctx.exception.reason, "invalid_argument")
        self.assertLess(elapsed, REJECT_BUDGET_S)


if __name__ == "__main__":
    unittest.main()
