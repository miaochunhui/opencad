"""test_b6_param_boundary —— B6 参数边界：**拒绝不留半截状态** + **计数先拒后循环** + **响应不泄漏内建异常名**。

缺陷源（实测，逐条有原样证据）
------------------------------
1. `partial_state_on_reject`（扫描席 `/hf3fs-jd/data/tmp/cad-scan3/logs/probe3-20261001-035348.log:177`）：
   `POST /api/command {"command":"array_linear","args":{"rows":<10^309>,...}}` 客户端 20 s 超时（`transport_error`），
   而 L1 真源**已经变了**（`doc_changed=true`）。本席重放（`replay_transport.py`）复现同一形态：
   `status=0 transport=TimeoutError: timed out elapsed_ms=20019.8 partial_on_reject=True alive=True`。
2. `transport_error` ×5（上条日志 178–182 行）全是**同一次挂住的连带**：`rows_huge309` 把工作区锁占死，
   其后 `rows1_cols1` / `same_place_2x2` / `dx_inf` / `dx_huge309` / `rect_degenerate_same_point`
   的每一个请求都只能超时（本席重放实测：`rows1_cols1` 也 20 s 超时，且服务进程仍活着、RSS 涨到 2.5 GB）。
3. `defects_A_contract_leak=9`（`probe3-20261001-035314.log:46`）：`snap_query` 的 `tolerance_nm` 非法值
   回的是 `{"error":"command_failed","detail":"ValueError: tolerance_nm must be …"}` —— 内建异常名泄漏进响应。

判据（本文件全部真起服务、真打 HTTP，现算）
--------------------------------------------
* **原子性**：任何被拒命令之后，`/api/state` 的 L1 文本必须与命令前**逐字节相同**（含 9 条 leak 用例）；
* **有界拒绝**：`rows=10^309` / `cols=10^309` / `rows*cols` 超限 / `sides=10^12` 都必须在
  墙钟预算内回结构化拒绝（不是超时），且**此后服务仍可用**（`/api/state` 立刻回）；
* **不泄漏**：响应原文里不得出现内建异常名（`ValueError` / `TypeError` / `OverflowError` …）；
* **阴性对照**：合法调用照旧成功（`2×2` 阵列、合法 `snap_query`、`rows=cols=1` 的真为零）。

跑法::

    cd oss-alternatives/apps/opencad
    python3 -m unittest discover -s tests -t .      # 契约命令
    python3 tests/test_b6_param_boundary.py -v      # 单跑本文件

产物：只写自己 `tempfile.mkdtemp()` 出来的 out-root，收尾 `rmtree`，**仓内不留产物**。
"""

from __future__ import annotations

import http.client as http_client
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_TESTS_DIR)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

try:  # `-m unittest discover -t .` 时包名是 tests.*
    from tests.test_b6_product_defects import _BASE_DOC, ServerProcess
except ImportError:  # 单跑本文件（sys.path 上是 tests/ 自己）
    from test_b6_product_defects import _BASE_DOC, ServerProcess  # type: ignore[no-redef]

MM = 1_000_000
HUGE = int("9" * 309)
#: 拒绝的墙钟预算：挂住的实现给不出响应（客户端只能超时）。
REJECT_BUDGET_S = 10.0

#: 响应体里**不许**出现的内建异常名（与扫描尺子 `find_exceptions` 同一族的口径子集）。
BUILTIN_EXCEPTION_NAMES = (
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "OverflowError",
    "ZeroDivisionError", "UnicodeEncodeError", "UnicodeDecodeError", "MemoryError",
    "RecursionError", "RuntimeError", "StopIteration", "ArithmeticError", "AssertionError",
)


class _Client(ServerProcess):
    """`ServerProcess` + 可指定超时（挂住的用例必须**短超时**，否则测试自己会等 20 s）。"""

    def request_with_timeout(self, method: str, path: str, body: object = None,
                             timeout: float = 5.0):
        conn = http_client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            payload = None
            headers = {}
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers = {"Content-Type": "application/json",
                           "Content-Length": str(len(payload))}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, raw.decode("utf-8", "replace")
        finally:
            conn.close()

    def post_raw(self, path: str, body: object, timeout: float = 10.0):
        """POST 并**拿响应原文**（判"有没有泄漏内建异常名"必须看原文，不能只看解析后的字段）。"""
        return self.request_with_timeout("POST", path, body, timeout=timeout)


class ParamBoundaryCase(unittest.TestCase):
    """每个测试自己起一个真服务器 + 一个自己的 out-root（顺序无关，收尾即删）。"""

    def setUp(self) -> None:
        self.out_root = tempfile.mkdtemp(prefix="opencad-b6pb-")
        self.addCleanup(shutil.rmtree, self.out_root, True)
        os.makedirs(os.path.join(self.out_root, "cases"), exist_ok=True)
        with open(os.path.join(self.out_root, "cases", "_base_empty.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(_BASE_DOC, handle, ensure_ascii=False, indent=2, sort_keys=True)
        self.server = _Client(self.out_root).start()
        self.addCleanup(self.server.stop)

    # ── 夹具 ────────────────────────────────────────────────────────────────────────
    def open_empty(self) -> dict:
        status, body = self.server.get("/api/open?path=cases/_base_empty.json")
        self.assertEqual(status, 200, body)
        return body

    def command(self, verb: str, args: dict | None = None) -> dict:
        status, body = self.server.post("/api/command", {"command": verb, "args": args or {}})
        self.assertEqual(status, 200, body)
        return body

    def seed_line(self) -> str:
        body = self.command("add_line", {"p1": [0, 0], "p2": [10 * MM, 0]})
        self.assertIs(body.get("ok"), True, body)
        return str(body.get("uuid") or "")

    def doc_text(self) -> str:
        status, body = self.server.get("/api/state")
        self.assertEqual(status, 200, body)
        return str(body.get("doc") or "")


class RejectAtomicityTests(ParamBoundaryCase):
    """`partial_state_on_reject`：被拒请求**不许**改 L1 真源（逐字节）。"""

    def assert_reject_is_atomic(self, verb: str, args: dict, label: str) -> dict:
        before = self.doc_text()
        status, body = self.server.post("/api/command", {"command": verb, "args": args})
        after = self.doc_text()
        self.assertIn(status, (200, 400), f"{label}: {body}")
        self.assertIs(body.get("ok"), False, f"{label}: 该拒的没收下 -> {body}")
        self.assertTrue(str(body.get("error") or ""), f"{label}: 要有机读错误码 -> {body}")
        self.assertEqual(before, after, f"{label}: 拒绝后真源变了（半截状态）")
        return body

    def test_huge_array_rows_reject_is_atomic(self) -> None:
        self.open_empty()
        line = self.seed_line()
        body = self.assert_reject_is_atomic(
            "array_linear", {"refs": [line], "rows": HUGE, "cols": 2, "dx_nm": MM, "dy_nm": MM},
            "rows=10^309")
        self.assertIn("rows", json.dumps(body, ensure_ascii=False))

    def test_huge_array_cols_reject_is_atomic(self) -> None:
        self.open_empty()
        line = self.seed_line()
        self.assert_reject_is_atomic(
            "array_linear", {"refs": [line], "rows": 2, "cols": 10 ** 12, "dx_nm": MM, "dy_nm": MM},
            "cols=10^12")

    def test_array_product_over_limit_reject_is_atomic(self) -> None:
        self.open_empty()
        line = self.seed_line()
        self.assert_reject_is_atomic(
            "array_linear",
            {"refs": [line], "rows": 100_000, "cols": 100_000, "dx_nm": MM, "dy_nm": MM},
            "rows*cols 超限")

    def test_out_of_range_dx_reject_is_atomic(self) -> None:
        self.open_empty()
        line = self.seed_line()
        self.assert_reject_is_atomic(
            "array_linear", {"refs": [line], "rows": 2, "cols": 2, "dx_nm": HUGE, "dy_nm": 0},
            "dx=10^309")

    def test_infinite_dx_reject_is_atomic(self) -> None:
        self.open_empty()
        line = self.seed_line()
        # JSON 里 inf 不是合法字面量（扫描席用的是 Python 客户端直发）⇒ 这里用同一路的 1e309 形态。
        status, body = self.server.post(
            "/api/command", {"command": "array_linear",
                             "args": {"refs": [line], "rows": 2, "cols": 1,
                                      "dx_nm": 10 ** 309, "dy_nm": 0}})
        self.assertIs(body.get("ok"), False, body)

    def test_degenerate_rectangle_reject_is_atomic(self) -> None:
        self.open_empty()
        self.assert_reject_is_atomic("add_rectangle", {"p1_nm": [0, 0], "p2_nm": [0, 0]},
                                     "退化矩形")

    def test_huge_polygon_sides_reject_is_atomic(self) -> None:
        self.open_empty()
        self.assert_reject_is_atomic("add_polygon",
                                     {"center_nm": [0, 0], "radius_nm": 10 * MM, "sides": 10 ** 12},
                                     "sides=10^12")

    def test_reject_list_is_atomic_for_all_snap_tolerances(self) -> None:
        """9 条 `contract_leak` 用例的**共同前提**：它们都是"拒绝" ⇒ 都不许动真源。"""
        self.open_empty()
        self.seed_line()
        bad_values = [-5 * MM, "10", "abc", None, True]
        for value in bad_values:
            self.assert_reject_is_atomic(
                "snap_query",
                {"kinds": ["quadrant"], "point_nm": [10 * MM, 0], "tolerance_nm": value},
                f"snap tolerance={value!r}")


class BoundedRejectTests(ParamBoundaryCase):
    """`transport_error` 根因：计数型入参越界必须**立刻拒**，不许进循环把服务挂住。"""

    def assert_bounded_reject(self, verb: str, args: dict, label: str) -> dict:
        started = time.monotonic()
        status, raw = self.server.request_with_timeout(
            "POST", "/api/command", {"command": verb, "args": args}, timeout=REJECT_BUDGET_S)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, REJECT_BUDGET_S, f"{label}: {elapsed:.1f}s 才回 —— 这不是拒绝，是挂住")
        body = json.loads(raw)
        self.assertIs(body.get("ok"), False, f"{label}: {body}")
        return body

    def test_huge_rows_rejected_quickly_and_server_stays_usable(self) -> None:
        """扫描席的原始触发用例：修前 20 s 超时 + 其后**所有**请求超时。"""
        self.open_empty()
        line = self.seed_line()
        self.assert_bounded_reject(
            "array_linear", {"refs": [line], "rows": HUGE, "cols": 2, "dx_nm": MM, "dy_nm": MM},
            "rows=10^309")
        # 关键：服务必须**还能用**（修前工作区锁被无限循环占死，这里会超时）。
        started = time.monotonic()
        self.assertIs(self.command("add_line", {"p1": [0, 0], "p2": [MM, MM]}).get("ok"), True)
        self.assertLess(time.monotonic() - started, REJECT_BUDGET_S, "拒绝之后服务不可用了")

    def test_huge_cols_rejected_quickly(self) -> None:
        self.open_empty()
        line = self.seed_line()
        self.assert_bounded_reject(
            "array_linear", {"refs": [line], "rows": 2, "cols": HUGE, "dx_nm": MM, "dy_nm": MM},
            "cols=10^309")

    def test_product_over_limit_rejected_quickly(self) -> None:
        self.open_empty()
        line = self.seed_line()
        self.assert_bounded_reject(
            "array_linear",
            {"refs": [line], "rows": 100_000, "cols": 100_000, "dx_nm": MM, "dy_nm": MM},
            "rows*cols 超限")

    def test_huge_polygon_sides_rejected_quickly(self) -> None:
        self.open_empty()
        self.assert_bounded_reject("add_polygon",
                                   {"center_nm": [0, 0], "radius_nm": 10 * MM, "sides": 10 ** 12},
                                   "sides=10^12")

    def test_control_two_by_two_still_creates(self) -> None:
        """阴性对照：合法阵列照旧（服务没被"一刀切"地锁死）。"""
        self.open_empty()
        line = self.seed_line()
        body = self.command("array_linear",
                            {"refs": [line], "rows": 2, "cols": 2, "dx_nm": MM, "dy_nm": MM})
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body.get("created"), 3, body)

    def test_control_one_by_one_is_truly_zero(self) -> None:
        self.open_empty()
        line = self.seed_line()
        body = self.command("array_linear",
                            {"refs": [line], "rows": 1, "cols": 1, "dx_nm": MM, "dy_nm": MM})
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body.get("created"), 0, body)


class ContractLeakTests(ParamBoundaryCase):
    """`defects_A_contract_leak=9`：响应原文不许出现内建异常名。"""

    def assert_no_builtin_leak(self, verb: str, args: dict, label: str) -> dict:
        status, raw = self.server.request_with_timeout(
            "POST", "/api/command", {"command": verb, "args": args}, timeout=REJECT_BUDGET_S)
        body = json.loads(raw)
        self.assertIs(body.get("ok"), False, f"{label}: 期望被拒 -> {raw}")
        for name in BUILTIN_EXCEPTION_NAMES:
            self.assertNotIn(name, raw, f"{label}: 响应里泄漏了内建异常名 {name} -> {raw}")
        self.assertTrue(str(body.get("detail") or ""), f"{label}: 拒绝必须带 detail -> {raw}")
        self.assertNotEqual(body.get("error"), "internal_error", f"{label}: {raw}")
        return body

    def test_snap_tolerance_rejects_do_not_leak_builtin_names(self) -> None:
        """扫描席的 9 条 leak 用例逐条重放（负值 / 字符串 / null / bool + inf / nan 形态）。"""
        self.open_empty()
        self.seed_line()
        inf = float("inf")
        nan = float("nan")
        # inf/nan 在 JSON 里非标准字面量：用 Python 客户端直发（与扫描席同路）。
        for value in [-5 * MM, "10", "abc", "inf", None, True, inf, -inf, nan]:
            status, raw = self.server.post_raw(
                "/api/command",
                {"command": "snap_query",
                 "args": {"kinds": ["quadrant"], "point_nm": [10 * MM, 0], "tolerance_nm": value}})
            body = json.loads(raw)
            self.assertIs(body.get("ok"), False, f"tolerance={value!r}: {raw}")
            for name in BUILTIN_EXCEPTION_NAMES:
                self.assertNotIn(name, raw, f"tolerance={value!r} 泄漏 {name} -> {raw}")
            self.assertNotEqual(body.get("error"), "internal_error", raw)
            self.assertTrue(str(body.get("detail") or ""), raw)

    def test_snap_tolerance_reject_is_structured(self) -> None:
        self.open_empty()
        self.seed_line()
        body = self.assert_no_builtin_leak(
            "snap_query",
            {"kinds": ["quadrant"], "point_nm": [10 * MM, 0], "tolerance_nm": -5 * MM},
            "tolerance=-5mm")
        self.assertTrue(str(body.get("error") or ""), body)
        self.assertIn("tolerance_nm", str(body.get("detail")), body)

    def test_control_valid_snap_query_still_works(self) -> None:
        """阴性对照：合法 `tolerance_nm` 照旧成功（这条尺子不是恒红）。"""
        self.open_empty()
        self.seed_line()
        body = self.command("snap_query",
                            {"kinds": ["quadrant"], "point_nm": [10 * MM, 0], "tolerance_nm": MM})
        self.assertIs(body.get("ok"), True, body)
        self.assertEqual(body.get("tolerance_nm"), MM, body)


class MutateRollbackMechanismTests(unittest.TestCase):
    """白盒：`Workspace._mutate` 的**机制**本身必须回滚（黑盒用例只覆盖"校验先于改动"的路径）。

    为什么要有这一条：只要有一天某条命令是"改到一半才抛"，黑盒那批用例（都在校验期就拒）就抓不住它 ——
    而 `_mutate` 是**所有**文档改动的唯一入口，回滚只能在这里做。
    """

    def setUp(self) -> None:
        import server as opencad_server  # noqa: PLC0415 - 真件（app 目录已在 sys.path）

        self.mod = opencad_server
        core = opencad_server._load_cadcore()
        self.ws = opencad_server.Workspace(core, locale="zh-CN", standard="gbt",
                                           out_root=tempfile.mkdtemp(prefix="opencad-b6pb-ws-"))
        self.addCleanup(shutil.rmtree, self.ws.out_root, True)

    def test_failed_action_rolls_back_the_live_document(self) -> None:
        before = self.ws.l1_text()
        self.assertNotEqual(before, "")

        def halfway_failure() -> None:
            # 真的改共享文档，然后才抛（模拟"改到一半失败"的命令）。
            self.ws.commands.add_line(self.ws.sketch, p1=[0, 0], p2=[10 * MM, 0])
            raise RuntimeError("injected failure after mutation")

        with self.assertRaises(RuntimeError):
            self.ws._mutate(halfway_failure)
        self.assertEqual(self.ws.l1_text(), before, "失败的动作没被回滚（半截状态留在真源里）")
        self.assertEqual(len(self.ws.constraints.sketch_entities(self.ws.sketch)), 0)
        self.assertEqual(self.ws.undo_stack, [], "失败不许压撤销栈")

    def test_successful_action_still_pushes_undo(self) -> None:
        before = self.ws.l1_text()
        self.ws._mutate(lambda: self.ws.commands.add_line(self.ws.sketch, p1=[0, 0],
                                                          p2=[10 * MM, 0]))
        self.assertNotEqual(self.ws.l1_text(), before)
        self.assertEqual(len(self.ws.undo_stack), 1)

    def test_rollback_keeps_undo_stack_usable(self) -> None:
        """回滚之后撤销栈必须还是**干净可用**的（回滚不许把栈搅乱）。"""
        self.ws._mutate(lambda: self.ws.commands.add_line(self.ws.sketch, p1=[0, 0],
                                                          p2=[10 * MM, 0]))
        after_first = self.ws.l1_text()
        with self.assertRaises(RuntimeError):
            self.ws._mutate(self._fail_after_mutation)
        self.assertEqual(self.ws.l1_text(), after_first)
        self.assertEqual(len(self.ws.undo_stack), 1)

    @staticmethod
    def _fail_after_mutation() -> None:
        raise RuntimeError("injected")


if __name__ == "__main__":
    unittest.main()
