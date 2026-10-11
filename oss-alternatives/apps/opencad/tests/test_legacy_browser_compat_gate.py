#!/usr/bin/env python3
"""test_legacy_browser_compat_gate.py -- 把旧浏览器兼容尺子接进 pytest（E1② 结构性抓手）。

为什么要有这一条：光有 legacy_browser_compat_gate.py 只是个"要记得跑"的脚本（AGENTS.md E1：
纪律不算交付）。这条测试让它变成**改坏就报红**的门：谁往 web/** 里塞了目标浏览器没有的特性，
或者把尺子自己的规则改坏（阳性对照/变异体对照不过），pytest 当场红。
真旧引擎那一段（--browser）不在单测里跑（CI 上不一定有 M86 二进制），
用环境变量 OPENCAD_LEGACY_CHROME 给了才跑。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
GATE_PATH = HERE.parent / "legacy_browser_compat_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("legacy_browser_compat_gate", GATE_PATH)
    assert spec and spec.loader, "cannot load " + str(GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_selftest_positive_and_mutants() -> None:
    """阳性对照 + 变异体阴性对照：尺子自己必须先自证（看不见红 = 不合格）。"""
    gate = _load_gate()
    ok, checks, mutants_red, notes = gate.selftest(gate.EDGE_TARGETS["edge86"], False)
    assert ok, "gate selftest failed: " + "; ".join(notes)
    assert mutants_red == len(gate.INJECTIONS)
    assert checks == len(gate.INJECTIONS) + 1


def test_shipped_web_is_legacy_clean_for_edge86() -> None:
    """真件必须对 Edge 86 线零命中（有命中的话判定行会给出 文件:行 + 特性 + 最低版本）。"""
    gate = _load_gate()
    findings = gate.scan_sources(gate.WEB_DIR, gate.EDGE_TARGETS["edge86"])
    detail = "; ".join(f.path + ":" + str(f.line) + " " + f.feature for f in findings)
    assert not findings, "web/** 用了 Edge 86 没有的特性: " + detail


def test_browser_leg_when_a_target_browser_is_available() -> None:
    """给了目标版本浏览器就跑真腿；没给则跳过（NA，不冒充绿）。"""
    gate = _load_gate()
    browser = os.environ.get("OPENCAD_LEGACY_CHROME", "").strip()
    if not browser:
        return
    res = gate.run_browser_leg(browser, None, 60)
    assert res["lang_options"] >= 3, "bootstrap 没跑完: " + str(res)
    assert res["ok"], "bootstrap 断言失败: " + str(res)
