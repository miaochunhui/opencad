#!/usr/bin/env python3
"""test_ui_gate_contract.py —— 判定行 G9 的**尺子契约**（承重腿白名单 / 三态分形 / 夹具冒充拒绿）。

为什么有这个文件
----------------
本门假绿过一次：`tools/cad/ui_gate.py` 的 `Report.finalize()` 原先只由「`reasons` 为空」推出 PASS，
于是**任何一条腿静默跳过**都能拿到 `verdict=PASS`。实测路径：`/api/state` 不回 `entities` 字段
⇒ 实体数 / `POST /api/export/dxf` / 用 `packages/dxf` 回读 / `save`-`open` 往返这几条腿整体不跑，
判定行里留着 `-1`（未采）哨兵照样绿 —— 「没测」伪装成了「测了且合格」。

所以这里把**尺子本身**钉成契约（纯单测：不开服务、不走网络、毫秒级）：
1. **承重腿白名单**：PASS 只能由「每条承重腿都真测到且为真」推出；任一条未采 ⇒ 不许 PASS。
2. **三态分形**：未采（`-1`）⇒ `NA`(`rc=2`)；采到为假 ⇒ `FAIL`(`rc=1`)；真测到零（`0`）原样出现在判定行。
3. **阴阳对照**：全绿的报告必须真给 PASS，证明这把尺子不是「永远报红」。
4. **夹具冒充拒绿**：树里带内置夹具自曝标记、或服务启动行带 `fixture=` ⇒ `app_authentic=0` ⇒ FAIL。
5. **可选腿只有一条**：`render`（playwright 可能合法缺席）⇒ 未采时 `rc=2` 且行里
   `optional_unmeasured=render`（不许静默），其余腿一律承重。

跑法
----
    python3 oss-alternatives/apps/opencad/tests/test_ui_gate_contract.py -v
    python3 -m pytest oss-alternatives/apps/opencad/tests/test_ui_gate_contract.py -q
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

HERE = Path(__file__).resolve()
OSS = HERE.parents[3]                                   # oss-alternatives/
GATE_PATH = OSS / "tools" / "cad" / "ui_gate.py"
APP_DIR = OSS / "apps" / "opencad"


def _load_gate():
    """按文件路径加载被判定的门（不靠 sys.path/cwd，避免与 apps 包同名冲突）。"""
    spec = importlib.util.spec_from_file_location("ui_gate_under_test_g9", GATE_PATH)
    assert spec is not None and spec.loader is not None, f"gate_unloadable:{GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

# 全绿现场：每条承重腿都给「真测到且为真」的值（阴阳对照里的阳面）
GREEN_FIELDS = dict(
    routes_total=4, routes_ok=4, catalogs=3, standards=5, dom_ids=len(gate.DOM_IDS),
    toolbar_tools=len(gate.TOOLS), i18n_keys=17, i18n_missing=[], lang_switch=1, lang_restore=1,
    entities_before=0, entities_after=1, entities_roundtrip=1, dxf_entities=2, external_refs=0,
    traversal_blocked=1, human_literals=0, app_authentic=1, render="PASS(screenshot=cad.png)",
    # 2026-09-29 新增两条承重腿（各自对应一次实测假绿）：
    #   static_refs —— 页面自己声明的静态引用必须按浏览器口径逐个可达（`src=js/app.js` 那次 404 假绿）
    #   i18n_derived —— 服务端派生兜底 ≠ 译文（`tool.select/panel.layers` 缺键却显示"有字"那次假绿）
    static_refs_total=2, static_refs_ok=2, static_refs_bad=[],
    i18n_derived=0, i18n_derived_keys=[],
    render_bytes=39282, render_sha256="a" * 64, render_empty_i18n=0,
    render_canvas="1159x705", render_locales=1,
)
REQUIRED_LEGS = frozenset({
    "routes", "catalogs", "standards", "dom_ids", "toolbar", "i18n_keys", "lang_switch",
    "lang_restore", "entities_roundtrip", "dxf_entities", "external_refs", "traversal_blocked",
    "human_literals", "app_authentic", "static_refs", "i18n_derived",
})


def green_report() -> "gate.Report":
    rep = gate.Report(app_dir=str(APP_DIR))
    rep.app_exists, rep.server_started = 1, 1
    rep.app_origin, rep.app_sha256 = "canonical", "0" * 64
    for key, value in GREEN_FIELDS.items():
        setattr(rep, key, value)
    return rep


class LegWhitelistTest(unittest.TestCase):
    """① 承重腿白名单：没测到的腿不许被「没人抱怨」糊过去。"""

    def test_leg_whitelist_is_frozen(self):
        states = gate.evaluate_legs(gate.Report())
        self.assertEqual({leg.name for leg in states if leg.required}, set(REQUIRED_LEGS))
        self.assertEqual({leg.name for leg in states if not leg.required}, {"render"},
                         "render 是唯一的环境依赖腿；新增可选腿必须先改这条断言（防悄悄放宽）")

    def test_unmeasured_required_legs_cannot_pass(self):
        rep = gate.Report(app_dir=str(APP_DIR))
        rep.app_exists, rep.server_started, rep.routes_total, rep.routes_ok = 1, 1, 0, 0
        rep.finalize()
        self.assertNotEqual(rep.verdict, "PASS", rep.line())
        self.assertEqual((rep.verdict, rep.rc), ("NA", gate.RC_NA), rep.line())
        for leg in ("entities_roundtrip", "dxf_entities", "lang_switch"):
            self.assertIn(leg, rep.unmeasured_legs, rep.line())
        self.assertIn("unmeasured_legs=", rep.line())

    def test_falsified_required_leg_is_fail_not_na(self):
        rep = green_report()
        rep.traversal_blocked = 0
        rep.finalize()
        self.assertEqual((rep.verdict, rep.rc), ("FAIL", gate.RC_FAIL), rep.line())
        self.assertIn("traversal_blocked", rep.falsified_legs)

    def test_all_legs_true_passes(self):
        rep = green_report()
        rep.finalize()
        self.assertEqual((rep.verdict, rep.rc), ("PASS", gate.RC_PASS), rep.line())
        self.assertEqual(rep.unmeasured_legs, [], rep.line())
        self.assertEqual(rep.legs_pass, rep.legs_total, rep.line())

    def test_optional_unmeasured_leg_keeps_pass_but_rc2_and_stays_visible(self):
        rep = green_report()
        rep.render = "NA(playwright_absent)"
        rep.finalize()
        self.assertEqual(rep.verdict, "PASS", rep.line())
        self.assertEqual(rep.rc, gate.RC_NA, rep.line())
        self.assertIn("optional_unmeasured=render", rep.line())

    def test_line_exposes_leg_counters(self):
        rep = green_report()
        rep.external_refs = 3
        rep.finalize()
        line = rep.line()
        for token in ("legs=", "unmeasured=", "falsified=", "app_authentic=", "app_origin=", "app_sha256="):
            self.assertIn(token, line)
        self.assertIn("falsified_legs=external_refs", line)
        self.assertEqual(rep.as_dict()["unmeasured_legs"], [])


class FakeGreenReproTest(unittest.TestCase):
    """② 原样复现那次假绿的现场：`/api/state` 不回 `entities` ⇒ 实体/DXF/往返腿整体未采。"""

    def test_state_without_entities_cannot_be_green(self):
        rep = green_report()
        for field in ("entities_before", "entities_after", "entities_roundtrip", "dxf_entities"):
            setattr(rep, field, -1)                      # flow 腿静默跳过时留下的正是这些哨兵
        rep.finalize()
        self.assertNotEqual(rep.verdict, "PASS", rep.line())
        self.assertEqual(rep.verdict, "NA", rep.line())
        self.assertIn("entities_roundtrip", rep.unmeasured_legs, rep.line())
        self.assertIn("dxf_entities", rep.unmeasured_legs, rep.line())

    def test_reason_list_alone_can_turn_the_gate_red(self):
        """人话缺陷通道不许被腿表吃掉：腿全绿但有一条 `reason` ⇒ 仍必须 FAIL。

        实撞：`finalize()` 改成只消费腿表时，语言包少一个 key（`locale:ja:keys_missing=1`）不再判红
        —— 它不落在任何单条腿的阈值上。`--selftest` 的 `drop_catalog_key` 变异体正是踩着这条路复现的。
        """
        rep = green_report()
        rep.fail("locale:ja:keys_missing=1:keys_extra=0")
        rep.finalize()
        self.assertEqual((rep.verdict, rep.rc), ("FAIL", gate.RC_FAIL), rep.line())
        self.assertIn("first_fail=locale:ja:keys_missing=1", rep.line())


class SentinelDisciplineTest(unittest.TestCase):
    """③ 未采（-1）与真测到零（0）不许同形（E2 读侧纪律）。"""

    def test_unmeasured_and_measured_zero_look_different(self):
        unmeasured = gate.Report(app_dir=str(APP_DIR))
        unmeasured.finalize()
        self.assertIn("dxf_entities=-1", unmeasured.line())
        zero = green_report()
        zero.dxf_entities = 0
        zero.finalize()
        self.assertIn("dxf_entities=0", zero.line())
        self.assertNotIn("dxf_entities=-1", zero.line())
        self.assertEqual((zero.verdict, zero.rc), ("FAIL", gate.RC_FAIL), zero.line())


class FixtureImpersonationTest(unittest.TestCase):
    """④ 夹具冒充真件 = 拒绿（旧版把内置夹具当真件也照样给 PASS）。"""

    def test_builtin_fixture_tree_self_exposes_and_is_detected(self):
        scratch = Path(tempfile.mkdtemp(prefix="g9_contract_fixture_"))
        try:
            app = gate._write_builtin_fixture(scratch / "fx")
            marker = gate._fixture_marker(app)
            self.assertTrue(marker, "内置夹具树必须能自曝，否则冒充检测无从谈起")
            self.assertIn("README.md", marker)
            self.assertEqual(
                gate._server_fixture("OPENCAD_SERVER verdict=PASS port=1 fixture=builtin"), "builtin")
            self.assertEqual(gate._server_fixture("OPENCAD_SERVER verdict=PASS port=1"), "")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_fixture_leg_falsified_beats_unmeasured(self):
        rep = gate.Report(app_dir=str(APP_DIR))          # 其余腿全未采，冒充这一条已采到为假
        rep.app_authentic, rep.fixture_marker = 0, "README.md"
        rep.finalize()
        self.assertEqual((rep.verdict, rep.rc), ("FAIL", gate.RC_FAIL), rep.line())
        self.assertIn("app_authentic", rep.falsified_legs, rep.line())

    def test_real_app_is_not_flagged_as_fixture(self):
        if not (APP_DIR / "server.py").is_file():
            self.skipTest("apps/opencad 不在（真件缺席时本断言无对象）")
        self.assertEqual(gate._fixture_marker(APP_DIR), "", "真件不许被误判成夹具")
        self.assertTrue(gate._is_canonical_app(APP_DIR))
        self.assertRegex(gate._app_fingerprint(APP_DIR), r"^[0-9a-f]{64}$")


class CliSurfaceTest(unittest.TestCase):
    """⑤ 放行开关必须显式且可见：`--allow-fixture` 只给自检用，判定行会印 `fixture_allowed=1`。"""

    def test_allow_fixture_flag_exists(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                gate.main(["--help"])
        self.assertIn("--allow-fixture", buf.getvalue())

    def test_report_defaults_are_unmeasured_sentinels(self):
        rep = gate.Report()
        self.assertEqual(rep.app_authentic, -1)
        self.assertEqual(rep.legs_total, -1)
        self.assertEqual(rep.fixture_allowed, 0)


class RenderTitleTest(unittest.TestCase):
    def test_bound_title_is_valid_and_empty_title_stays_red(self):
        with tempfile.TemporaryDirectory(prefix="g9-title-") as directory:
            root = Path(directory)
            rep = gate.Report(locale="en")

            def capture(executable, base, out, shot):
                shot.write_bytes(b"fixture" * 2000)
                return ('<title data-i18n="app.title">Open Engineering CAD</title>'
                        '<canvas width="1200" height="800"></canvas>')

            with patch.object(gate, "_find_chrome", return_value="fixture-chrome"), \
                    patch("importlib.util.module_from_spec") as module:
                module.return_value.capture.side_effect = capture
                with patch("importlib.util.spec_from_file_location") as spec:
                    spec.return_value.loader.exec_module.return_value = None
                    gate._render_leg("http://fixture", root, rep)
                self.assertTrue(rep.render.startswith("PASS"), rep.render)
                module.return_value.capture.return_value = (
                    '<title data-i18n="app.title"></title>'
                    '<canvas width="1200" height="800"></canvas>')
                module.return_value.capture.side_effect = None
                with patch("importlib.util.spec_from_file_location") as spec:
                    spec.return_value.loader.exec_module.return_value = None
                    gate._render_leg("http://fixture", root, rep)
                self.assertIn("title_empty", rep.render)


class ExportIsolationTest(unittest.TestCase):
    def test_real_gate_server_exports_only_into_explicit_workspace(self):
        with tempfile.TemporaryDirectory(prefix="g9-export-") as directory:
            root = Path(directory)
            server = gate.ServerProc(APP_DIR, out_root=root)
            try:
                self.assertTrue(server.start(), server.error)
                base = f"http://127.0.0.1:{server.port}"
                created = gate.http(base + "/api/command", method="POST",
                                    payload={"command": "add_line",
                                             "args": {"p1": [0, 0], "p2": [100, 0]}})
                self.assertTrue(created.json["ok"], created)
                for endpoint, name in (("/api/save", "probe.json"),
                                       ("/api/export/dxf", "probe.dxf")):
                    response = gate.http(base + endpoint, method="POST",
                                         payload={"path": name})
                    self.assertTrue(response.json["ok"], response)
                    self.assertEqual(Path(response.json["path"]).resolve(), root.resolve() / name)
                    self.assertGreater((root / name).stat().st_size, 0)
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
