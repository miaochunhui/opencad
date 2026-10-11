#!/usr/bin/env python3
"""ui_gate —— 判定行 G9：CAD GUI 外壳门（起真服务 / 真打 API / 真切语言 / 真导出回读）。

一句话用途
----------
一条命令回答「`apps/opencad` 这个本地 Web 外壳现在到底达标没有」：**起真服务**（子进程跑
`server.py`，等 `OPENCAD_SERVER verdict=PASS port=<n>`），用标准库 `urllib` 打**全部** HTTP API，
断言语言包数量 / 标准档数量 / HTML 里每一个 `data-i18n` key 都有译文、**逐语言真切换**（相邻语言
至少 5 个 key 译文不同且切回原语言能复原）、**命令 → 实体数 +1 → 导出 DXF → 用 `packages/dxf`
读侧当尺子数回实体**、静态面（DOM id 齐 / 无外链 / 路径穿越 404），可选真浏览器渲染腿，
最后打一行机读判定行。

为什么这样设计
--------------
1. **口径照抄冻结契约的 G9 行**（不自创）：
   `CAD_UI_VERDICT: verdict=PASS|FAIL|NA routes=… catalogs=3 dom_ids=7 i18n_keys=… lang_switch=1
   entities_roundtrip=1 dxf_entities=… external_refs=0 traversal_blocked=1 render=…`，
   三态 `rc`：`0=PASS`、`1=真不达标`、`2=未采（NA，不当绿）`。
2. **空集 / 依赖缺席不许假绿**：`apps/opencad` 不存在、`server.py` 起不来、20 s 内没等到
   `verdict=PASS` 行 ⇒ `rc=2` 且判定行里 `verdict=NA reason=…`，计数字段记 `-1`（未采），
   **绝不静默返 0、绝不伪造成功**。反之「服务起来了但某条断言不过」⇒ `rc=1` 真不达标 ——
   这两句的分界线就是「采不到」与「采到了但不合格」。
3. **尺子必须承重复用**：DXF 读回不自己写解析器，而是把 `packages/dxf` 挂进 `sys.path` 后
   `dxf.read(path).entity_count()` —— 导出侧与读侧是两个独立实现，读侧当尺子才有意义。
4. **切语言要真切**：只看源码里有没有 `set_locale` 字样是「看源码推因果」（E2.3）。这里逐个
   catalog 真 `POST /api/locale`，比对返回的 `strings` 与上一语言的**逐 key 差异数**（≥5 才算生效），
   最后切回第一门语言并要求 `strings` 与首份快照**逐 key 相等**（复原）。
5. **静态面用「不为空 + 双向可见」的口径**：DOM id / 工具栏按钮 / `data-i18n` key 集合都从
   `GET /` 的**真响应体**里现抠；外链计数扫 HTML/CSS/JS 三个真文件（**注释除外**，先剥注释）；
   路径穿越按冻结契约打 `GET /web/../server.py`，必须 404。
6. **渲染腿可选但 NA 必须可见**：`playwright` 在 ⇒ 真开浏览器、真截图、真 `select_option` 切语言，
   断言 canvas 之外的文案变了；不在 ⇒ 该腿记 `render=NA(playwright_absent)` 并让**整体 `rc=2`**，
   NA 明写在判定行里，不许隐藏、不许当绿；渲染腿也不影响其它腿的 PASS/FAIL。
7. **`--selftest` 是尺子的阴阳双证**：优先拿**真 `apps/opencad`** 整棵拷进临时目录（夹具自证：
   拷贝里真有 `server.py` 与 `web/index.html`，且真树在自检前后逐字节不变）；真件不在时退到
   **内置契约夹具**（同样走完整 HTTP 流程，只是 server 由本文件的夹具生成），并在判定行里
   写明 `fixture=builtin(real_app_absent)` —— 降级可见，不冒充真件。四个变异体（删一个语言包 key /
   把某语言译文克隆成 en / 去掉一个 DOM id / 打开一个外链）必须 **4/4 报红**，干净对照必须绿；
   尺子看不见红 ⇒ 本门不算数，`--selftest` 自己 `rc=1`。
8. **退出必杀进程树**：服务用 `start_new_session=True` 起，收尾对**整组** `killpg`（TERM → KILL），
   不许留孤儿监听端口。
9. **★「没人抱怨」不算绿（2026-09-29 结构性堵假绿）**：本门假绿过一次 —— `finalize()` 的 PASS 原先只由
   `reasons` 为空推出，于是**任何一条腿静默跳过**都能得到 `verdict=PASS`：实测路径是 `/api/state`
   不回 `entities` 字段 ⇒ 实体数 / `POST /api/export/dxf` / 用 `packages/dxf` 回读 / `save`-`open`
   往返**四条腿整体不跑**，判定行里留着 `-1`（未采）哨兵照样绿。现在改成**承重腿白名单**
   （`evaluate_legs()`，唯一真源）：PASS 必须由「每条承重腿都**真测到且为真**」推出，
   **未采 ⇒ NA(rc=2)**、**采到为假 ⇒ FAIL(rc=1)**，判定行里分形可见（`legs=a/b`、`unmeasured=<n>`、
   `unmeasured_legs=…`、`falsified_legs=…`），并保留 `-1（未采）≠ 0（真测到零）`。唯一 `required=False`
   的腿是 `render`（playwright 可能合法缺席）：它未采时 verdict 保持 PASS 但 `rc=2`，且行里
   `optional_unmeasured=render` 明写，不许静默。
10. **夹具冒充真件 = 拒绿**：判定对象必须自证不是内置夹具（树内 `FIXTURE_SENTINEL` 自曝标记，或服务
    启动行带 `fixture=`）⇒ 命中即 `app_authentic` 采到为假 ⇒ FAIL。只有 `--selftest` 内部才用
    `--allow-fixture` 放行降级件，且该开关**原样印在判定行**（`fixture_allowed=1`）⇒ 拿它换来的绿自曝其短。
    `app_origin=canonical|external` 与 `app_sha256`（冻结外壳逐字节指纹）同样印在行里，跑到别处的树不再隐形。
11. **`--selftest` 两族变异体**：内容族（4 个：删 key / 克隆 en 译文 / 去 DOM id / 开外链）必须 **FAIL**；
    **假绿族**必须**非绿**：`state_drop_entities`（抹掉 `/api/state` 的 `entities` ⇒ 应有 `NA`）、
   `fixture_impersonation`（拿夹具树、不给 `--allow-fixture` ⇒ 应有 `FAIL`）。尺子看不见这两族红 ⇒ 本门不算数。

用法
----
    python3 oss-alternatives/tools/cad/ui_gate.py --check
    python3 oss-alternatives/tools/cad/ui_gate.py --check --app oss-alternatives/apps/opencad --json-out /tmp/g9.json
    python3 oss-alternatives/tools/cad/ui_gate.py --selftest
    python3 oss-alternatives/apps/opencad/tests/test_ui_gate_contract.py -v   # 尺子契约单测（毫秒级）

**任何交付判定都不许带 `--allow-fixture`**（它只为自检的降级夹具存在）。

注：本文件自身也守「源码不留用户文案」的口径 —— 夹具里的译文一律用 ASCII + `\\uXXXX` 转义拼装，
不写裸的中日文字面量。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ── 契约常量 ────────────────────────────────────────────────────────────────────────────
GATE = "CAD_UI_VERDICT"
SELFTEST_GATE = "CAD_UI_SELFTEST"
RC_PASS, RC_FAIL, RC_NA = 0, 1, 2

DOM_IDS: tuple[str, ...] = ("canvas", "lang-select", "standard-select", "status",
                            "layers", "cmd-line", "toolbar")
TOOLS: tuple[str, ...] = ("select", "line", "circle", "arc", "dimension", "delete")
MIN_CATALOGS = 3
MIN_STANDARDS = 5
MIN_LANG_DIFF = 5          # 相邻语言至少 5 个 key 译文不同，否则「切了等于没切」
START_TIMEOUT_S = 20.0     # 冻结契约：等 OPENCAD_SERVER verdict=PASS 行 ≤20 s
HTTP_TIMEOUT_S = 15.0
SERVER_LINE_RE = re.compile(r"OPENCAD_SERVER\s+verdict=(?P<verdict>\w+)\s+port=(?P<port>\d+)")
VERDICT_RE = re.compile(rf"{GATE}:\s*verdict=(?P<verdict>[A-Z]+)")
_EXT_RE = re.compile(r"https?://", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_KEY_SHAPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+$")
_HTML_TEXT_TAGS = ("title", "label", "button", "span", "p", "h1", "h2", "h3", "li", "option",
                   "div", "td", "th", "caption", "summary", "legend", "a", "small", "strong")
_SKIP_HTML_TAGS = frozenset({"script", "style", "meta", "link", "br", "hr", "canvas", "input",
                             "img", "source", "head"})
_SKIP_DIRS = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache", "build"})
# 内置夹具在树内的自曝标记（写在夹具 README 里）；命中 ⇒ 这棵树不是真件，不许判绿。
FIXTURE_SENTINEL = "ui_gate builtin fixture"
_MAX_SCAN_BYTES = 262144   # 夹具自曝扫描只看小文本件，避免把大二进制读进内存


# ── 结果载体 ────────────────────────────────────────────────────────────────────────────
@dataclass
class Report:
    """一次 G9 判定的全部机读字段；`verdict` 由 `finalize()` 按三态口径收敛。"""

    app_dir: str = ""
    app_exists: int = 0
    server_started: int = 0
    port: int = -1
    routes_total: int = 0
    routes_ok: int = 0
    catalogs: int = -1
    standards: int = -1
    dom_ids: int = -1
    dom_ids_missing: list[str] = field(default_factory=list)
    toolbar_tools: int = -1
    i18n_keys: int = -1
    i18n_keys_js: int = -1
    i18n_missing: list[str] = field(default_factory=list)
    human_literals: int = -1
    human_literal_examples: list[str] = field(default_factory=list)
    lang_switch: int = -1
    lang_pairs: list[str] = field(default_factory=list)
    lang_restore: int = -1
    locale: str = ""
    standard: str = ""
    projection: str = ""
    entities_before: int = -1
    entities_after: int = -1
    entities_roundtrip: int = -1
    dxf_bytes: int = -1
    dxf_entities: int = -1
    dimension_entities: int = -1
    dxf_path: str = ""
    external_refs: int = -1
    external_examples: list[str] = field(default_factory=list)
    traversal_blocked: int = -1
    static_files: int = 0
    assets_missing: int = 0
    render: str = "NA(not_run)"
    render_text_changed: int = -1
    render_shot: str = ""
    render_bytes: int = -1
    render_sha256: str = ""
    render_empty_i18n: int = -1
    render_canvas: str = ""
    render_locales: int = 0
    # ── 假绿堵漏①：页面**自己声明**的静态引用必须逐个可达（浏览器按页面 URL 解析，
    #    门不许拿 `/web/` 前缀兜底 —— 那次实撞里 `src="js/app.js"` 浏览器 404、门却 200） ──
    static_refs_total: int = -1
    static_refs_ok: int = -1
    static_refs_bad: list[str] = field(default_factory=list)
    # ── 假绿堵漏②：语言包缺键时服务端会**派生兜底**（key 尾段转可读串），界面看着"有字"
    #    其实没有译文（实测：`tool.select`/`panel.layers` 缺键，界面显示英文 Select/Layers） ──
    i18n_derived: int = -1
    i18n_derived_keys: list[str] = field(default_factory=list)
    unmeasured: str = ""
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # ── 承重腿三态（堵假绿：PASS 必须由「每腿都真测到且为真」推出，不许由「没人抱怨」推出） ──
    legs_pass: int = -1
    legs_total: int = -1
    unmeasured_legs: list[str] = field(default_factory=list)
    optional_unmeasured: list[str] = field(default_factory=list)
    falsified_legs: list[str] = field(default_factory=list)
    # ── 判定对象自证（夹具冒充真件 ⇒ 拒绿） ──
    app_authentic: int = -1
    app_origin: str = ""
    app_sha256: str = ""
    fixture_marker: str = ""
    fixture_allowed: int = 0
    verdict: str = "NA"
    rc: int = RC_NA

    def fail(self, reason: str) -> None:
        self.reasons.append(reason)

    def finalize(self) -> "Report":
        """三态收敛（唯一真源）：承重腿全绿才 PASS，未采 ⇒ NA，采到为假 ⇒ FAIL。

        顺序有意为之：**采到为假优先于未采**（已知的缺陷不许躲在「我这条没采到」后面），
        硬未采（服务/应用都不在，什么都没测）最优先于「部分腿未采」。
        """
        states = evaluate_legs(self)
        self.legs_total = len(states)
        self.legs_pass = sum(1 for leg in states if leg.measured and leg.ok)
        self.unmeasured_legs = [leg.name for leg in states if leg.required and not leg.measured]
        self.optional_unmeasured = [leg.name for leg in states if not leg.required and not leg.measured]
        self.falsified_legs = [leg.name for leg in states if leg.measured and not leg.ok]
        # 两条独立通道都必须被消费：①腿表（结构上回答「测到了吗 / 合格吗」）②`reasons`（人话缺陷记录，
        # 例如某语言包少一个 key —— 它不落在任何单条腿的阈值上）。任一条红 ⇒ FAIL。
        if self.falsified_legs or self.reasons:
            self.verdict, self.rc = "FAIL", RC_FAIL
            return self
        if self.unmeasured:
            self.verdict, self.rc = "NA", RC_NA
            return self
        if self.unmeasured_legs:
            self.unmeasured = "unmeasured_legs:" + ",".join(self.unmeasured_legs)
            self.verdict, self.rc = "NA", RC_NA
            return self
        self.verdict = "PASS"
        self.rc = RC_NA if self.optional_unmeasured else RC_PASS
        return self

    def line(self) -> str:
        return (
            f"{GATE}: verdict={self.verdict} routes={self.routes_ok}/{self.routes_total} "
            f"catalogs={self.catalogs} dom_ids={len(DOM_IDS) - len(self.dom_ids_missing)} "
            f"i18n_keys={self.i18n_keys} lang_switch={self.lang_switch} "
            f"entities_roundtrip={self.entities_roundtrip} dxf_entities={self.dxf_entities} "
            f"external_refs={self.external_refs} traversal_blocked={self.traversal_blocked} "
            f"render={self.render} standards={self.standards} tools={self.toolbar_tools} "
            f"i18n_missing={len(self.i18n_missing)} human_literals={self.human_literals} "
            f"lang_restore={self.lang_restore} dim_entities={self.dimension_entities} "
            f"entities_delta={self.entities_after - self.entities_before if self.entities_after >= 0 and self.entities_before >= 0 else -1} "
            f"static_files={self.static_files} assets_missing={self.assets_missing} app_exists={self.app_exists} "
            f"static_refs={self.static_refs_ok}/{self.static_refs_total} i18n_derived={self.i18n_derived} "
            f"render_bytes={self.render_bytes} render_canvas={self.render_canvas or 'NA'} "
            f"render_empty_i18n={self.render_empty_i18n} render_locales={self.render_locales} "
            f"render_sha256={(self.render_sha256 or 'NA')[:12]} "
            f"legs={self.legs_pass}/{self.legs_total} unmeasured={len(self.unmeasured_legs)} "
            f"falsified={len(self.falsified_legs)} app_authentic={self.app_authentic} "
            f"app_origin={self.app_origin or 'unknown'} app_sha256={(self.app_sha256 or 'NA')[:12]} "
            f"fixture_allowed={self.fixture_allowed} rc={self.rc}"
            + (f" unmeasured_legs={','.join(self.unmeasured_legs)}" if self.unmeasured_legs else "")
            + (f" optional_unmeasured={','.join(self.optional_unmeasured)}" if self.optional_unmeasured else "")
            + (f" falsified_legs={','.join(self.falsified_legs)}" if self.falsified_legs else "")
            + (f" fixture={self.fixture_marker}" if self.fixture_marker else "")
            + (f" reason={self.unmeasured}" if self.unmeasured else "")
            + (f" first_fail={self.reasons[0]}" if self.reasons else "")
        )

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["gate"] = GATE
        out["line"] = self.line()
        return out


# ── 承重腿白名单（唯一真源）：PASS 只能由「每腿都真测到且为真」推出 ────────────────────────
@dataclass(frozen=True)
class Leg:
    """一条腿的三态：`measured=False` ⇒ 未采（NA）；`measured and not ok` ⇒ 采到为假（FAIL）。"""

    name: str
    measured: bool
    ok: bool
    required: bool = True
    detail: str = ""


def _measured(value: int) -> bool:
    """计数字段的「未采」哨兵是 `-1`；`0` 是**真测到零** —— 两者不许同形（E2 读侧纪律）。"""
    return value != -1


def evaluate_legs(rep: "Report") -> list[Leg]:
    """承重腿白名单：**唯一真源**，`Report.finalize()` 必须消费它。

    旧版的假绿就出在这里：PASS 只由「`reasons` 为空」推出 ⇒ 腿静默跳过（判定行留着 `-1`）照样绿。
    现在每条腿都要显式回答「测到了吗 / 合格吗」，任一条承重腿没测到就不许 PASS。
    `render` 是唯一 `required=False` 的腿（playwright 可能**合法缺席**）：未采时 verdict 保持 PASS，
    但 `rc=2` 且行里 `optional_unmeasured=render`，不许静默。
    """
    return [
        Leg("routes", rep.routes_total > 0,
            rep.routes_total > 0 and rep.routes_ok == rep.routes_total,
            detail=f"{rep.routes_ok}/{rep.routes_total}"),
        Leg("catalogs", _measured(rep.catalogs), rep.catalogs >= MIN_CATALOGS, detail=str(rep.catalogs)),
        Leg("standards", _measured(rep.standards), rep.standards == MIN_STANDARDS, detail=str(rep.standards)),
        Leg("dom_ids", _measured(rep.dom_ids), rep.dom_ids == len(DOM_IDS),
            detail=f"{rep.dom_ids}/{len(DOM_IDS)}"),
        Leg("toolbar", _measured(rep.toolbar_tools), rep.toolbar_tools == len(TOOLS),
            detail=f"{rep.toolbar_tools}/{len(TOOLS)}"),
        Leg("i18n_keys", _measured(rep.i18n_keys), rep.i18n_keys > 0 and not rep.i18n_missing,
            detail=str(rep.i18n_keys)),
        Leg("lang_switch", _measured(rep.lang_switch), rep.lang_switch == 1, detail=str(rep.lang_switch)),
        Leg("lang_restore", _measured(rep.lang_restore), rep.lang_restore == 1, detail=str(rep.lang_restore)),
        Leg("entities_roundtrip", _measured(rep.entities_roundtrip), rep.entities_roundtrip == 1,
            detail=str(rep.entities_roundtrip)),
        Leg("dxf_entities", _measured(rep.dxf_entities), rep.dxf_entities >= 1, detail=str(rep.dxf_entities)),
        Leg("external_refs", _measured(rep.external_refs), rep.external_refs == 0, detail=str(rep.external_refs)),
        Leg("traversal_blocked", _measured(rep.traversal_blocked), rep.traversal_blocked == 1,
            detail=str(rep.traversal_blocked)),
        Leg("human_literals", _measured(rep.human_literals), rep.human_literals == 0,
            detail=str(rep.human_literals)),
        Leg("app_authentic", _measured(rep.app_authentic), rep.app_authentic == 1,
            detail=rep.fixture_marker or "ok"),
        # 页面自己声明的静态引用必须**逐个**按浏览器口径可达（相对引用相对 "/" 解析，无兜底）
        Leg("static_refs", _measured(rep.static_refs_total),
            rep.static_refs_total > 0 and rep.static_refs_ok == rep.static_refs_total and not rep.static_refs_bad,
            detail=f"{rep.static_refs_ok}/{rep.static_refs_total}"),
        # 派生兜底 ≠ 译文：`/api/state` 的 strings_missing 非空即红（否则界面会显示英文 key 尾段）
        Leg("i18n_derived", _measured(rep.i18n_derived), rep.i18n_derived == 0, detail=str(rep.i18n_derived)),
        Leg("render", not rep.render.startswith("NA"), rep.render.startswith("PASS"),
            required=False, detail=rep.render),
    ]


def _canonical_app() -> Path:
    return (Path(__file__).resolve().parents[2] / "apps" / "opencad").resolve()


def _is_canonical_app(app_dir: Path) -> bool:
    try:
        return Path(app_dir).resolve() == _canonical_app()
    except Exception:                                            # pragma: no cover - 路径异常
        return False


def _app_fingerprint(app_dir: Path) -> str:
    """冻结外壳（`server.py` + `web/**`）逐字节 sha256；一个文件都没有 ⇒ 空串（未采，不静默给 0）。"""
    files: list[Path] = []
    server = app_dir / "server.py"
    if server.is_file():
        files.append(server)
    try:
        files.extend(p for p in sorted(app_dir.glob("web/**/*")) if p.is_file())
    except Exception:                                            # pragma: no cover - 权限/竞态
        pass
    if not files:
        return ""
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(app_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _fixture_marker(app_dir: Path) -> str:
    """树内自曝的内置夹具标记（命中即「这不是真件」，返回人话；未命中返回空串）。"""
    hits: list[str] = []
    try:
        candidates = sorted(p for p in app_dir.rglob("*") if p.is_file())
    except Exception:                                            # pragma: no cover - 权限/竞态
        return ""
    for path in candidates:
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in (".py", ".md", ".txt", ".json", ".html", ".js", ".css"):
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
            if FIXTURE_SENTINEL in path.read_text(encoding="utf-8", errors="replace"):
                hits.append(str(path.relative_to(app_dir)))
        except Exception:                                        # pragma: no cover - 读到一半被删
            continue
    return ",".join(hits[:3])


def _server_fixture(line: str) -> str:
    """服务启动行里的夹具自曝标记（`fixture=builtin`）；真件不带 ⇒ 返回空串。"""
    match = re.search(r"\bfixture=([A-Za-z0-9_.:-]+)", line or "")
    return match.group(1) if match else ""


# ── HTTP（纯标准库） ────────────────────────────────────────────────────────────────────
@dataclass
class Resp:
    status: int
    body: bytes
    json: Any = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def http(url: str, *, method: str = "GET", payload: Any = None,
         timeout: float = HTTP_TIMEOUT_S) -> Resp:
    data = None
    headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:                       # 4xx/5xx 也要拿到响应体
        try:
            body = exc.read()
        except Exception:                                       # pragma: no cover - 极端情况
            body = b""
        status = int(exc.code)
    except Exception as exc:                                    # 连不上 / 超时
        return Resp(0, b"", None, f"{type(exc).__name__}: {exc}")
    parsed: Any = None
    text = body.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    return Resp(status, body, parsed, "")


# ── 真服务子进程（退出必杀整棵进程树） ──────────────────────────────────────────────────
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerProc:
    """起 `apps/opencad/server.py`，等 `OPENCAD_SERVER verdict=PASS port=<n>`；收尾杀整组。"""

    def __init__(self, app_dir: Path, *, timeout: float = START_TIMEOUT_S,
                 out_root: Path | None = None) -> None:
        self.app_dir = Path(app_dir)
        self.out_root = out_root
        self.timeout = timeout
        self.port = -1
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.line = ""
        self.error = ""

    def start(self) -> bool:
        script = self.app_dir / "server.py"
        if not script.is_file():
            self.error = "server_py_absent"
            return False
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if self.out_root is not None:
            self.out_root.mkdir(parents=True, exist_ok=True)
            env["OPENCAD_OUT_ROOT"] = str(self.out_root.resolve())
        self.port = _free_port()
        cmd = [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(self.port)]
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.app_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, start_new_session=True)
        except Exception as exc:
            self.error = f"spawn_failed:{type(exc).__name__}"
            return False
        threading.Thread(target=self._drain, daemon=True).start()
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.line:
                match = SERVER_LINE_RE.search(self.line)
                if match and match.group("verdict") == "PASS":
                    self.port = int(match.group("port"))
                    return True
            if self.proc.poll() is not None:
                self.error = f"server_exited_rc={self.proc.returncode}"
                return False
            time.sleep(0.1)
        self.error = f"start_timeout>{self.timeout:g}s"
        return False

    def _drain(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for raw in self.proc.stdout:
            text = raw.rstrip("\n")
            self.lines.append(text)
            if not self.line and SERVER_LINE_RE.search(text):
                self.line = text

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        if self.proc.stdout is not None:
            self.proc.stdout.close()

    def tail(self, n: int = 6) -> list[str]:
        return self.lines[-n:]


# ── 静态扫描（剥注释后数外链 / 抠 DOM 契约 / 找人类语言字面量） ──────────────────────────
def _strip_comments(text: str, kind: str) -> str:
    if kind == "html":
        return re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", " ", text)


def _external_refs(files: dict[str, str]) -> tuple[int, list[str]]:
    count, examples = 0, []
    for name, text in sorted(files.items()):
        kind = "html" if name.endswith((".html", ".htm")) else "code"
        for match in _EXT_RE.finditer(_strip_comments(text, kind)):
            count += 1
            if len(examples) < 5:
                examples.append(f"{name}:{match.start()}")
    return count, examples


def _html_literals(html: str) -> list[str]:
    text = _strip_comments(html, "html")
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    out: list[str] = []
    for match in re.finditer(r"<([A-Za-z][A-Za-z0-9]*)\b([^>]*)>(.*?)</\1>", text, flags=re.S):
        tag, attrs, inner = match.group(1).lower(), match.group(2), match.group(3)
        if tag in _SKIP_HTML_TAGS or "data-i18n" in attrs:
            continue
        inner = re.sub(r"<[A-Za-z][^>]*data-i18n[^>]*>.*?</[A-Za-z]+>", " ", inner, flags=re.S)
        flat = re.sub(r"<[^>]*>", " ", inner).strip()
        if flat and re.search(r"[A-Za-z\u3400-\u9fff]", flat):
            out.append(f"<{tag}> {flat[:60]}")
    return out


def _js_literals(js: str) -> list[str]:
    stripped = _strip_comments(js, "code")
    return [m.group(0)[:40] for m in _CJK_RE.finditer(stripped)]


def _html_keys(html: str) -> set[str]:
    return set(re.findall(r'data-i18n\s*=\s*"([^"]+)"', html)) | \
        set(re.findall(r"data-i18n\s*=\s*'([^']+)'", html))


def _js_keys(js: str) -> set[str]:
    keys = set(re.findall(r'data-i18n["\']?\s*[:=]\s*["\']([^"\']+)["\']', js))
    keys |= set(re.findall(r'\b(?:t|tr|translate|_t|i18n\.t)\(\s*["\']([A-Za-z][\w.-]*)["\']', js))
    return {k for k in keys if _KEY_SHAPE_RE.match(k)}


def _assets(html: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html):
        url = match.group(1).strip()
        if url.startswith(("#", "data:", "mailto:", "javascript:")) or url in out:
            continue
        out.append(url)
    return out


# ── 判定主体 ────────────────────────────────────────────────────────────────────────────
def run_check(app_dir: Path, workdir: Path, *, quiet: bool = False,
              allow_fixture: bool = False) -> Report:
    report = Report(app_dir=str(app_dir))
    report.app_exists = 1 if app_dir.is_dir() else 0
    report.fixture_allowed = 1 if allow_fixture else 0
    report.app_origin = "canonical" if _is_canonical_app(app_dir) else "external"
    report.app_sha256 = _app_fingerprint(app_dir)
    marker = _fixture_marker(app_dir)
    if marker:                                   # 判定对象自证：夹具冒充真件 ⇒ 拒绿（只有自检能放行）
        report.fixture_marker = marker
        report.app_authentic = 1 if allow_fixture else 0
        if not allow_fixture:
            report.fail(f"fixture_impersonation:{marker}")
    workdir.mkdir(parents=True, exist_ok=True)
    server = ServerProc(app_dir, out_root=workdir / "workspace")
    started = server.start()
    try:
        if not started:
            report.unmeasured = f"app_absent:{server.error}" if not report.app_exists else f"server:{server.error}"
            report.notes.extend(server.tail())
            return report.finalize()
        report.server_started, report.port = 1, server.port
        line_marker = _server_fixture(server.line)   # 服务自己说它是夹具 ⇒ 同样拒绿
        if line_marker:
            report.fixture_marker = report.fixture_marker or line_marker
            if not allow_fixture:
                report.app_authentic = 0
                report.fail(f"server_fixture_marker:{line_marker}")
            elif report.app_authentic == -1:
                report.app_authentic = 1
        if report.app_authentic == -1:
            report.app_authentic = 1
        base = f"http://127.0.0.1:{server.port}"
        _check_api(base, report)
        _check_static(base, app_dir, report)
        # 各腿互相独立：某腿红了也要把其余腿真跑完，判定行里才有完整证据（不静默跳过）
        _check_flow(base, app_dir, report)
        _check_lang_switch(base, report)
        _render_leg(base, workdir, report)
        return report.finalize()
    finally:
        server.stop()


def _probe(report: Report, resp: Resp, what: str, *, need_ok_field: bool = True) -> bool:
    report.routes_total += 1
    if resp.status == 0:
        report.fail(f"route:{what}:{resp.error}")
        return False
    if resp.status != 200:
        report.fail(f"route:{what}:http_{resp.status}")
        return False
    if need_ok_field and not (isinstance(resp.json, dict) and "ok" in resp.json):
        report.fail(f"route:{what}:no_ok_field")
        return False
    if isinstance(resp.json, dict) and resp.json.get("ok") is False:
        report.fail(f"route:{what}:ok_false")
        return False
    report.routes_ok += 1
    return True


def _check_api(base: str, report: Report) -> None:
    state = http(f"{base}/api/state")
    if not _probe(report, state, "state"):
        return
    payload = state.json
    report.locale = str(payload.get("locale") or "")
    report.standard = str(payload.get("standard") or "")
    report.projection = str(payload.get("projection") or "")
    catalogs = payload.get("catalogs")
    standards = payload.get("standards")
    strings = payload.get("strings")
    report.catalogs = len(catalogs) if isinstance(catalogs, list) else -1
    report.standards = len(standards) if isinstance(standards, list) else -1
    report.entities_before = int(payload.get("entities", -1)) if isinstance(payload.get("entities"), int) else -1
    if report.catalogs < MIN_CATALOGS:
        report.fail(f"catalogs={report.catalogs}<{MIN_CATALOGS}")
    if report.standards != MIN_STANDARDS:
        report.fail(f"standards={report.standards}!={MIN_STANDARDS}")
    if not isinstance(strings, dict) or not strings:
        report.fail("state:strings_empty")
        strings = {}
    if not isinstance(payload.get("doc"), str) or not payload["doc"].strip():
        report.fail("state:doc_empty")
    else:
        try:
            json.loads(payload["doc"])
        except Exception:
            report.fail("state:doc_not_l1_json")

    cats = http(f"{base}/api/catalogs")
    if _probe(report, cats, "catalogs"):
        items = cats.json.get("catalogs") if isinstance(cats.json.get("catalogs"), list) else []
        report.notes.append(
            "catalog_meta=" + ",".join(
                f"{i.get('code')}:{i.get('keys')}" for i in items if isinstance(i, dict))[:160])
        if len(items) != report.catalogs:
            report.notes.append(f"catalogs_endpoint_diff={len(items)}")

    std = http(f"{base}/api/standard", method="POST", payload={"standard": "ansi"})
    if _probe(report, std, "standard"):
        got = str(std.json.get("standard") or "")
        proj = str(std.json.get("projection") or "")
        if got != "ansi":
            report.fail(f"standard_switch_ignored:{got}")
        if proj not in ("first", "third", "NA"):
            report.fail(f"standard:bad_projection:{proj}")
        elif proj == "NA":
            report.notes.append("standard_ansi_projection=NA(unverified)")
        back = http(f"{base}/api/standard", method="POST", payload={"standard": report.standard or "gbt"})
        _probe(report, back, "standard_restore")


def _check_static(base: str, app_dir: Path, report: Report) -> None:
    page = http(f"{base}/")
    report.routes_total += 1
    if page.status != 200:
        report.fail(f"route:root:http_{page.status}")
        return
    report.routes_ok += 1
    html = _strip_comments(page.body.decode("utf-8", "replace"), "html")
    report.static_files += 1
    # `(?<![\w-])` 必须锚住属性名：否则 `data-dropped-id="status"` 会因子串匹配被当成 `id="status"`
    # —— 这是 `--selftest` 的 drop_dom_id 变异体实测出来的一条假绿路径（改名叫"去掉 id"其实没去掉）。
    missing = [dom for dom in DOM_IDS
               if not re.search(rf'(?<![\w-])id\s*=\s*["\']{re.escape(dom)}["\']', html)]
    report.dom_ids_missing = missing
    report.dom_ids = len(DOM_IDS) - len(missing)
    if missing:
        report.fail("dom_ids_missing=" + ",".join(missing))
    tool_hits = 0
    for tool in TOOLS:
        if re.search(rf'<button[^>]*class\s*=\s*["\'][^"\']*\btool\b[^"\']*["\'][^>]*data-tool\s*=\s*["\']{tool}["\']', html) or \
           re.search(rf'<button[^>]*data-tool\s*=\s*["\']{tool}["\'][^>]*data-i18n', html):
            tool_hits += 1
    report.toolbar_tools = tool_hits
    if tool_hits != len(TOOLS):
        report.fail(f"toolbar_buttons={tool_hits}/{len(TOOLS)}")

    html_keys = _html_keys(html)
    files: dict[str, str] = {"index.html": html}
    report.static_refs_total = 0
    report.static_refs_ok = 0
    for asset in _assets(html):
        if _EXT_RE.search(asset) or asset.startswith("//") or asset.startswith("data:"):
            continue
        report.static_refs_total += 1
        # 浏览器按**页面 URL（"/"）**解析相对引用；这里不许再拿 `/web/` 前缀兜底 ——
        # 2026-09-29 实撞：`src="js/app.js"` 浏览器请求 `/js/app.js` 得 404（JS/CSS 全没加载、
        # 页面全塌、画布不存在），而旧代码 404 后自动改打 `/web/js/app.js` 拿到 200 ⇒ 假绿。
        url = base + (asset if asset.startswith("/") else "/" + asset)
        resp = http(url)
        if resp.ok:
            files[asset] = resp.body.decode("utf-8", "replace")
            report.static_files += 1
            report.static_refs_ok += 1
        else:
            report.assets_missing += 1
            report.static_refs_bad.append(f"{asset}->http_{resp.status}")
            report.fail(f"static_ref_unreachable:{asset}:http_{resp.status}")
            report.notes.append(f"asset_missing={asset}")
    js = "\n".join(text for name, text in files.items() if name.endswith(".js"))
    js_keys = _js_keys(js) if js else set()
    report.i18n_keys_js = len(js_keys)
    all_keys = html_keys | js_keys
    report.i18n_keys = len(all_keys)
    if not js_keys:
        report.notes.append("js_i18n_accessor_unknown(js_keys=0)")
    refs, examples = _external_refs(files)
    report.external_refs, report.external_examples = refs, examples
    if refs:
        report.fail(f"external_refs={refs}")
    literals = _html_literals(html)
    for name, text in files.items():
        if name.endswith((".js", ".css")):
            literals.extend(f"{name}: {item}" for item in _js_literals(text))
    report.human_literals = len(literals)
    report.human_literal_examples = literals[:5]
    if literals:
        report.fail(f"human_literals={len(literals)}")

    trav = http(f"{base}/web/../server.py")
    enc = http(f"{base}/web/%2e%2e/server.py")
    report.traversal_blocked = 1 if trav.status == 404 else 0
    if trav.status != 404:
        report.fail(f"traversal_not_blocked:http_{trav.status}")
    if enc.status != 404:
        report.notes.append(f"encoded_traversal=http_{enc.status}")
    report.notes.append(f"traversal_probe=/web/../server.py -> {trav.status}")

    # 静态面 key 覆盖：HTML/JS 里出现的每个 data-i18n key 必须有译文（缺失即红）
    state = http(f"{base}/api/state")
    strings = state.json.get("strings") if isinstance(state.json, dict) else None
    if not isinstance(strings, dict):
        report.fail("static:state_unreachable_for_key_coverage")
        return
    report.i18n_missing = sorted(k for k in all_keys if k not in strings)
    if report.i18n_missing:
        report.fail(f"i18n_keys_missing={len(report.i18n_missing)}:{report.i18n_missing[:3]}")
    # 派生兜底 ≠ 译文：服务端对"语言包里没有的 key"会派生一个可读串塞进 strings，
    # 于是上面那句 i18n_missing 检查**看不到**缺键（界面显示英文 Select/Layers 而门说 0 缺失）。
    # 唯一可信来源是服务端自己吐的 `strings_missing`（实测 7 条：tool.select/panel.layers/…）。
    derived = state.json.get("strings_missing") if isinstance(state.json, dict) else None
    report.i18n_derived_keys = [str(k) for k in derived] if isinstance(derived, list) else []
    report.i18n_derived = len(report.i18n_derived_keys)
    if report.i18n_derived:
        report.fail(f"i18n_derived_labels={report.i18n_derived}:{report.i18n_derived_keys[:3]}")
    else:
        report.notes.append(f"i18n_derived_keys=none strings={len(strings)}")


def _check_lang_switch(base: str, report: Report) -> None:
    cats = http(f"{base}/api/catalogs")
    codes = [str(i.get("code")) for i in (cats.json or {}).get("catalogs", [])
             if isinstance(i, dict) and i.get("code")]
    if not codes:
        report.fail("lang_switch:no_catalogs")
        return
    first: dict[str, str] | None = None
    previous: dict[str, str] | None = None
    ok_pairs = 0
    for code in codes:
        resp = http(f"{base}/api/locale", method="POST", payload={"locale": code})
        if not _probe(report, resp, f"locale:{code}"):
            return
        strings = resp.json.get("strings")
        if not isinstance(strings, dict) or not strings:
            report.fail(f"locale:{code}:strings_empty")
            return
        if str(resp.json.get("locale")) != code:
            report.fail(f"locale:{code}:echo_mismatch")
        changed = resp.json.get("changed")
        if not isinstance(changed, int):
            report.fail(f"locale:{code}:changed_not_int")
        if first is None:
            first = dict(strings)
        else:
            diff = sum(1 for key, value in strings.items() if previous.get(key) != value)
            report.lang_pairs.append(f"{code}:{diff}")
            if diff < MIN_LANG_DIFF:
                report.fail(f"locale:{code}:lang_diff={diff}<{MIN_LANG_DIFF}")
            else:
                ok_pairs += 1
            missing = sorted(set(first) - set(strings))
            extra = sorted(set(strings) - set(first))
            if missing or extra:
                report.fail(f"locale:{code}:keys_missing={len(missing)}:keys_extra={len(extra)}")
        previous = dict(strings)
    report.lang_switch = 1 if ok_pairs == max(0, len(codes) - 1) and ok_pairs > 0 else 0
    back = http(f"{base}/api/locale", method="POST", payload={"locale": codes[0]})
    if _probe(report, back, "locale:restore") and isinstance(back.json.get("strings"), dict):
        report.lang_restore = 1 if dict(back.json["strings"]) == (first or {}) else 0
        if not report.lang_restore:
            report.fail("locale:restore_mismatch")
    else:
        report.lang_restore = -1


def _resolve_artifact(app_dir: Path, rel: str) -> Path | None:
    candidates = [app_dir / rel, app_dir.parent / rel, app_dir.parent.parent / rel,
                  app_dir.parent.parent.parent / rel, Path.cwd() / rel]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _check_flow(base: str, app_dir: Path, report: Report) -> None:
    """命令 → 实体 +1 → 导出 DXF → 用 packages/dxf 读回当尺子 → save/open 往返。"""
    before = report.entities_before
    if before < 0:                                   # /api/state 都没给出实体数 ⇒ 本腿未采（finalize 会判 NA）
        report.notes.append("flow_leg=unmeasured(state_entities_absent)")
        return
    created = None
    # 参数名以冻结签名 `add_line(sk, *, p1, p2, layer="0")`（MVP-INTERFACES §B）为准：
    # 服务端 `server.py::_command_locked` 读的是 `p1/p2`，自创 `start/end`（或空 args）会被判缺参
    # ⇒ `ok=false`，本腿连同 DXF 回读一起记未采。两条候选都按契约给参，保留原有的重试结构。
    for args in ({"p1": [0, 0], "p2": [100, 0]},
                 {"p1": [0, 0], "p2": [100, 0], "layer": "0"}):
        resp = http(f"{base}/api/command", method="POST",
                    payload={"command": "add_line", "args": args})
        if _probe(report, resp, "command"):
            uid = str(resp.json.get("uuid") or "")
            if not uid:
                report.fail("command:empty_uuid")
            created = resp.json
            break
    # 命令腿红了也不早退：导出/存档腿照跑，判定行里才有完整证据（不静默跳过）
    if created is not None:
        state = http(f"{base}/api/state")
        if _probe(report, state, "state_after_command"):
            after = state.json.get("entities")
            report.entities_after = int(after) if isinstance(after, int) else -1
            if report.entities_after != before + 1:
                report.fail(f"entities_delta={report.entities_after - before}!=1")

    dxf = http(f"{base}/api/export/dxf", method="POST", payload={"path": "build/opencad.dxf"})
    if _probe(report, dxf, "export_dxf"):
        report.dxf_bytes = int(dxf.json.get("bytes") or -1)
        dims = dxf.json.get("dimension_entities")
        report.dimension_entities = int(dims) if isinstance(dims, int) else -1
        if report.dxf_bytes <= 0:
            report.fail(f"dxf_bytes={report.dxf_bytes}")
        rel = str(dxf.json.get("path") or "build/opencad.dxf")
        found = _resolve_artifact(app_dir, rel)
        if found is None:
            report.fail(f"dxf_file_not_found:{rel}")
        else:
            report.dxf_path = str(found)
            report.dxf_entities = _count_dxf_entities(found, report)

    saved = http(f"{base}/api/save", method="POST", payload={"path": "build/opencad.json"})
    save_bytes = -1
    if _probe(report, saved, "save"):
        save_bytes = int(saved.json.get("bytes") or -1)
        if save_bytes <= 0:
            report.fail(f"save_bytes={save_bytes}")
    opened = http(f"{base}/api/open?path=build/opencad.json")
    if _probe(report, opened, "open"):
        doc = opened.json.get("doc")
        ents = opened.json.get("entities")
        if not isinstance(doc, str) or not doc.strip():
            report.fail("open:doc_empty")
        if isinstance(ents, int) and report.entities_after >= 0 and ents != report.entities_after:
            report.fail(f"open:entities={ents}!={report.entities_after}")
        elif isinstance(ents, int) and report.entities_after >= 0:
            report.entities_roundtrip = 1 if (report.dxf_entities >= 1 and
                                              report.entities_after == before + 1) else 0
    if report.entities_roundtrip == 0:      # 采到了但为假 ⇒ 明确报因（未采是 -1，走 NA，两者不许同形）
        report.fail("entities_roundtrip=0")


def _count_dxf_entities(path: Path, report: Report) -> int:
    """承重复用 `packages/dxf` 读侧当尺子（不自写解析器）。"""
    root = Path(__file__).resolve().parents[2]           # oss-alternatives/
    pkg = root / "packages" / "dxf"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    try:
        import dxf                                       # type: ignore
    except Exception as exc:
        report.fail(f"dxf_reader_import_failed:{type(exc).__name__}")
        return -1
    try:
        doc = dxf.read(str(path))
        count = int(doc.entity_count())
        report.notes.append(f"dxf_sections={doc.section_names()}")
        report.notes.append(f"dxf_dims={len(doc.entities_of_type('DIMENSION'))}")
    except Exception as exc:
        report.fail(f"dxf_read_failed:{type(exc).__name__}:{exc}")
        return -1
    if count < 1:
        report.fail(f"dxf_entities={count}<1")
    return count


CHROME_CANDIDATES: tuple[str, ...] = (
    "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome",
    "/home/mch/.cache/ms-playwright/chromium-1187/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)
SHOT_DIR_REL = Path("oss-alternatives/apps/opencad/docs/verify/ui-render-20260929")


def _find_chrome() -> str:
    """找一个能用的 Chromium 可执行文件（`CAD_UI_CHROME` 可覆盖；找不到返回空串）。"""
    override = os.environ.get("CAD_UI_CHROME", "").strip()
    candidates = ([override] if override else []) + list(CHROME_CANDIDATES)
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return ""


def _render_leg(base: str, workdir: Path, report: Report) -> None:
    """真渲染腿：**优先用本机已有 Chromium CLI**（零 Python 依赖），playwright 不再是前提。

    为什么改（2026-09-29 实撞，两件事叠在一起）：
    ① 本机没有 playwright ⇒ 这条腿长期记 `NA`，而"NA 不等于绿"在汇报里极易被读成"没问题"；
    ② **真渲染恰恰是唯一抓得住那次事故的尺子** —— `web/index.html` 写了相对引用 `src="js/app.js"`，
       浏览器按页面 URL 解析成 `/js/app.js` 得 404（JS/CSS 全没加载、画布不存在、页面全塌），
       而 HTTP 门自己拼 `/web/js/app.js` 去打，拿到 200 报"资源齐全"。截图字节数 5,973 vs 修后 38,870
       就是这次假绿与真绿的分界。
    所以：找到 Chromium 就**真渲染 + 真断言**（且采到为假一律 FAIL，不许退化成 NA）；
    确实找不到才记 `NA(chrome_absent)`。
    """
    chrome = _find_chrome()
    if not chrome:
        report.render = "NA(chrome_absent)"
        return
    shot_dir = workdir
    # 截图默认落**本次运行的 workdir**（尺子不许往真件树里写东西：自检的 `tree_untouched` 会当场报红）。
    # 需要把渲染证据入库时，显式给环境变量 `CAD_UI_SHOT_DIR=<仓内目录>`（Lead 发布证据时用）。
    override = os.environ.get("CAD_UI_SHOT_DIR", "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = Path(__file__).resolve().parents[3] / candidate
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            shot_dir = candidate
        except OSError:
            shot_dir = workdir
    shot = shot_dir / f"opencad-{report.locale or 'default'}.png"
    try:
        import importlib.util

        renderer = Path(__file__).resolve().parents[1] / "cad3d/browser_render.py"
        spec = importlib.util.spec_from_file_location("cad_live_render", renderer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        dom = module.capture(chrome, base, workdir, shot)
    except Exception as exc:  # noqa: BLE001 - 渲染失败是**采到为假**，不是未采
        report.render = f"FAIL(chrome_error:{type(exc).__name__})"
        report.fail(f"render:chrome_error:{type(exc).__name__}:{exc}")
        return
    if not dom.strip():
        report.render = "FAIL(dump_dom_empty)"
        report.fail("render:dump_dom_empty")
        return
    # ① 空 i18n 节点 = JS 没跑（或没填文案）—— 本次事故里唯一的浏览器侧信号
    report.render_empty_i18n = len(re.findall(r'data-i18n\s*=\s*["\'][^"\']*["\'][^>]*>\s*<', dom))
    # ② canvas 必须有真实运行时尺寸（事故里 canvas 压根没渲染出来）
    canvas = re.search(r'<canvas[^>]*\bwidth\s*=\s*["\']?(\d+)["\']?[^>]*\bheight\s*=\s*["\']?(\d+)', dom)
    if canvas:
        report.render_canvas = f"{canvas.group(1)}x{canvas.group(2)}"
    # ③ 标题非空（文案来自语言包，不是 HTML 字面量）
    title = re.search(r"<title\b[^>]*>([^<]*)</title\s*>", dom, re.IGNORECASE)
    title_text = (title.group(1).strip() if title else "")
    problems: list[str] = []
    if report.render_empty_i18n != 0:
        problems.append(f"empty_i18n_nodes={report.render_empty_i18n}")
    if not canvas:
        problems.append("canvas_absent")
    elif int(canvas.group(1)) <= 200 or int(canvas.group(2)) <= 200:
        problems.append(f"canvas_too_small={report.render_canvas}")
    if not title_text:
        problems.append("title_empty")
    if not shot.is_file() or shot.stat().st_size <= 10240:
        problems.append(f"screenshot_bytes={shot.stat().st_size if shot.is_file() else -1}")
    if problems:
        report.render = "FAIL(" + ",".join(problems) + ")"
        for item in problems:
            report.fail(f"render:{item}")
        return
    report.render_bytes = shot.stat().st_size
    report.render_sha256 = hashlib.sha256(shot.read_bytes()).hexdigest()
    report.render_locales = 1
    report.render_shot = str(shot)
    report.render = f"PASS(shot={shot.name},bytes={report.render_bytes},canvas={report.render_canvas})"


# ── 内置契约夹具（真件缺席时的降级件；判定行里必须写明 fixture=builtin） ────────────────
_FIXTURE_EN: dict[str, str] = {
    "app.title": "Open CAD Shell",
    "tool.select": "Select",
    "tool.line": "Line",
    "tool.circle": "Circle",
    "tool.arc": "Arc",
    "tool.dimension": "Dimension",
    "tool.delete": "Delete",
    "panel.layers": "Layers",
    "ui.language": "Language",
    "ui.standard": "Standard",
    "status.ready": "Ready",
    "cmd.hint": "Command",
    "layer.default": "Default layer",
}
# 转义拼装的译文尾缀：避免本文件出现裸的中日文字面量。
_FIXTURE_LOCALES: tuple[tuple[str, str, str], ...] = (
    ("zh-CN", "\u7b80\u4f53\u4e2d\u6587", "\u4e2d\u6587"),
    ("ja", "\u65e5\u672c\u8a9e", "\u65e5\u672c\u8a9e"),
)
_FIXTURE_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title data-i18n="app.title"></title>
<link rel="stylesheet" href="/web/css/app.css">
</head>
<body>
<header id="toolbar">
<button class="tool" data-tool="select" data-i18n="tool.select"></button>
<button class="tool" data-tool="line" data-i18n="tool.line"></button>
<button class="tool" data-tool="circle" data-i18n="tool.circle"></button>
<button class="tool" data-tool="arc" data-i18n="tool.arc"></button>
<button class="tool" data-tool="dimension" data-i18n="tool.dimension"></button>
<button class="tool" data-tool="delete" data-i18n="tool.delete"></button>
</header>
<main>
<canvas id="canvas" width="800" height="520"></canvas>
<aside>
<h2 data-i18n="panel.layers"></h2>
<ul id="layers"></ul>
</aside>
</main>
<footer>
<label for="lang-select" data-i18n="ui.language"></label>
<select id="lang-select"></select>
<label for="standard-select" data-i18n="ui.standard"></label>
<select id="standard-select"></select>
</footer>
<div id="status" data-i18n="status.ready"></div>
<input id="cmd-line" type="text">
<script src="/web/js/app.js"></script>
</body>
</html>
"""
_FIXTURE_JS = """(function () {
  var state = { strings: {}, locale: "" };
  function apply(strings) {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (Object.prototype.hasOwnProperty.call(strings, key)) { el.textContent = strings[key]; }
    });
  }
  function fillSelect(sel, items, selected) {
    sel.textContent = "";
    items.forEach(function (item) {
      var opt = document.createElement("option");
      opt.value = item.value;
      opt.textContent = item.label;
      if (item.value === selected) { opt.selected = true; }
      sel.appendChild(opt);
    });
  }
  function refresh() {
    fetch("/api/state").then(function (r) { return r.json(); }).then(function (data) {
      state.strings = data.strings;
      state.locale = data.locale;
      apply(data.strings);
      document.getElementById("status").textContent = data.strings["status.ready"];
      fetch("/api/catalogs").then(function (r) { return r.json(); }).then(function (cats) {
        fillSelect(document.getElementById("lang-select"),
          cats.catalogs.map(function (c) { return { value: c.code, label: c.native_name }; }),
          state.locale);
        fillSelect(document.getElementById("standard-select"),
          data.standards.map(function (s) { return { value: s, label: s }; }), data.standard);
      });
    });
  }
  document.getElementById("lang-select").addEventListener("change", function (ev) {
    fetch("/api/locale", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locale: ev.target.value }) }).then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) { state.strings = data.strings; state.locale = data.locale; apply(data.strings); }
      });
  });
  refresh();
})();
"""
_FIXTURE_CSS = """html, body { margin: 0; padding: 0; font-family: sans-serif; }
#toolbar { display: flex; gap: 4px; padding: 6px; }
.tool { min-width: 84px; }
main { display: flex; gap: 8px; padding: 6px; }
#canvas { border: 1px solid #888; }
#status { padding: 6px; }
"""
_FIXTURE_SERVER = '''#!/usr/bin/env python3
# Built-in contract fixture for ui_gate --selftest (generated; not the real app).
import argparse
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
I18N = WEB / "i18n"
STANDARDS = ["iso", "gbt", "ansi", "din", "jis"]
PROJ = {"iso": "first", "gbt": "first", "ansi": "third", "din": "first", "jis": "first"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}
STATE = {"locale": "en", "standard": "iso", "entities": []}


def catalogs():
    out = {}
    for path in sorted(I18N.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[payload["_meta"]["code"]] = payload
    return out


def doc_text():
    payload = {"schema_version": 1, "meta": {"doc_uuid": "00000000-0000-4000-8000-000000000000"},
               "unit": "mm", "standard_profile": STATE["standard"],
               "projection_angle": PROJ[STATE["standard"]], "sketches": [], "features": [],
               "annotations": [], "sheets": [], "entities": STATE["entities"]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\\n"


def dxf_text():
    parts = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1015", "0", "ENDSEC",
             "0", "SECTION", "2", "ENTITIES"]
    for _ in STATE["entities"]:
        parts += ["0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "30", "0.0",
                  "11", "100.0", "21", "0.0", "31", "0.0"]
    parts += ["0", "DIMENSION", "8", "0", "2", "*D1", "10", "0.0", "20", "0.0", "30", "0.0",
              "11", "50.0", "21", "10.0", "31", "0.0", "70", "0"]
    parts += ["0", "ENDSEC", "0", "EOF"]
    return "\\n".join(parts) + "\\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def _body(self):
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            index = WEB / "index.html"
            if not index.is_file():
                return self._json({"ok": False, "error": "index_absent"}, 404)
            return self._send(200, index.read_bytes(), MIME[".html"])
        if path == "/api/state":
            cats = catalogs()
            cat = cats.get(STATE["locale"]) or cats["en"]
            return self._json({"ok": True, "doc": doc_text(), "locale": STATE["locale"],
                               "standard": STATE["standard"], "projection": PROJ[STATE["standard"]],
                               "catalogs": sorted(cats), "standards": STANDARDS,
                               "strings": cat["strings"], "entities": len(STATE["entities"])})
        if path == "/api/catalogs":
            cats = catalogs()
            return self._json({"ok": True, "catalogs": [
                {"code": code, "native_name": payload["_meta"]["native_name"],
                 "keys": len(payload["strings"])} for code, payload in sorted(cats.items())]})
        if path == "/api/open":
            query = parse_qs(parsed.query)
            rel = (query.get("path") or [""])[0]
            target = (HERE / rel) if rel else None
            if target is None or not target.is_file():
                return self._json({"ok": False, "error": "not_found"})
            text = target.read_text(encoding="utf-8")
            return self._json({"ok": True, "doc": text,
                               "entities": len(json.loads(text).get("entities", []))})
        if path.startswith("/web/"):
            target = (WEB / unquote(path[len("/web/"):])).resolve()
            if target != WEB.resolve() and WEB.resolve() not in target.parents:
                return self._json({"ok": False, "error": "not_found"}, 404)
            if not target.is_file():
                return self._json({"ok": False, "error": "not_found"}, 404)
            return self._send(200, target.read_bytes(),
                              MIME.get(target.suffix, "application/octet-stream"))
        return self._json({"ok": False, "error": "unknown_route", "path": path}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/locale":
            cats = catalogs()
            want = str(body.get("locale") or "")
            if want not in cats:
                return self._json({"ok": False, "error": "locale_unavailable"})
            prev = cats.get(STATE["locale"], {}).get("strings", {})
            STATE["locale"] = want
            new = cats[want]["strings"]
            changed = sum(1 for key, value in new.items() if prev.get(key) != value)
            return self._json({"ok": True, "locale": want, "changed": changed, "strings": new})
        if path == "/api/standard":
            want = str(body.get("standard") or "")
            if want not in STANDARDS:
                return self._json({"ok": False, "error": "standard_unavailable"})
            STATE["standard"] = want
            return self._json({"ok": True, "standard": want, "projection": PROJ[want]})
        if path == "/api/command":
            command = str(body.get("command") or "")
            if command != "add_line":
                return self._json({"ok": False, "error": "unknown_command"})
            uid = str(uuid.uuid4())
            STATE["entities"].append({"type": "line", "uuid": uid, "args": body.get("args") or {}})
            return self._json({"ok": True, "uuid": uid, "entities": len(STATE["entities"])})
        if path in ("/api/export/dxf", "/api/save"):
            rel = str(body.get("path") or ("build/opencad.dxf" if path.endswith("dxf")
                                           else "build/opencad.json"))
            target = HERE / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            data = dxf_text().encode("utf-8") if path.endswith("dxf") else doc_text().encode("utf-8")
            target.write_bytes(data)
            payload = {"ok": True, "path": rel, "bytes": len(data)}
            if path.endswith("dxf"):
                payload["dimension_entities"] = 1
            return self._json(payload)
        return self._json({"ok": False, "error": "unknown_route"}, 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("OPENCAD_SERVER verdict=PASS port=%d url=http://%s:%d fixture=builtin"
          % (server.server_address[1], args.host, server.server_address[1]), flush=True)
    server.serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
'''
_FIXTURE_TEST = '''#!/usr/bin/env python3
# Contract fixture smoke test: the tree must carry the frozen shell files.
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


class ShellFilesTest(unittest.TestCase):
    def test_frozen_files_exist(self):
        for rel in ("server.py", "web/index.html", "web/js/app.js", "web/css/app.css"):
            self.assertTrue((HERE / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
'''
_FIXTURE_README = """# opendcad (ui_gate builtin fixture)

This directory is generated by `tools/cad/ui_gate.py --selftest` when the real
`apps/opencad` shell is absent. It implements the frozen HTTP contract so the
G9 ruler can be shown to see red and green. It is not a product deliverable.
"""


def _write_builtin_fixture(root: Path) -> Path:
    app = root / "apps" / "opencad"
    (app / "web" / "js").mkdir(parents=True, exist_ok=True)
    (app / "web" / "css").mkdir(parents=True, exist_ok=True)
    (app / "web" / "i18n").mkdir(parents=True, exist_ok=True)
    (app / "tests").mkdir(parents=True, exist_ok=True)
    (app / "server.py").write_text(_FIXTURE_SERVER, encoding="utf-8")
    (app / "web" / "index.html").write_text(_FIXTURE_INDEX, encoding="utf-8")
    (app / "web" / "js" / "app.js").write_text(_FIXTURE_JS, encoding="utf-8")
    (app / "web" / "css" / "app.css").write_text(_FIXTURE_CSS, encoding="utf-8")
    (app / "tests" / "test_server.py").write_text(_FIXTURE_TEST, encoding="utf-8")
    (app / "README.md").write_text(_FIXTURE_README, encoding="utf-8")
    catalogs = [("en", "English", None)] + [(code, native, tag)
                                            for code, native, tag in _FIXTURE_LOCALES]
    for code, native, tag in catalogs:
        strings = {key: (value if tag is None else value + " / " + tag)
                   for key, value in _FIXTURE_EN.items()}
        payload = {"_meta": {"code": code, "native_name": native}, "strings": strings}
        (app / "web" / "i18n" / (code + ".json")).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return app


# ── --selftest：尺子的阴阳双证 ──────────────────────────────────────────────────────────
def _sha_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "absent"
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*_SKIP_DIRS))


def _find_catalog_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(root.rglob("*.json")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("strings"), dict):
            continue
        code = str((payload.get("_meta") or {}).get("code") or path.stem)
        out.setdefault(code, path)
    return out


def _mutate(kind: str, case_root: Path, app: Path) -> str:
    """四种做坏，返回人话描述；做不成就返回空串（自检会把它算成没红）。"""
    cats = _find_catalog_files(case_root)
    others = [code for code in cats if code != "en"]
    if kind in ("drop_catalog_key", "clone_en_text"):
        if not others or "en" not in cats:
            return ""
        code = others[0]
        payload = json.loads(cats[code].read_text(encoding="utf-8"))
        if kind == "drop_catalog_key":
            key = sorted(payload["strings"])[0]
            payload["strings"].pop(key)
            cats[code].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"{code}:drop_key={key}"
        en = json.loads(cats["en"].read_text(encoding="utf-8"))["strings"]
        payload["strings"] = {key: en.get(key, value) for key, value in payload["strings"].items()}
        cats[code].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"{code}:cloned_en_text"
    index = app / "web" / "index.html"
    if not index.is_file():
        return ""
    html = index.read_text(encoding="utf-8")
    if kind == "drop_dom_id":
        if 'id="status"' not in html:
            return ""
        index.write_text(html.replace('id="status"', 'data-dropped-id="status"', 1), encoding="utf-8")
        return "index.html:drop_id=status"
    if kind == "external_link":
        index.write_text(html.replace(
            "</body>", '<script src="https://example.com/remote.js"></script>\n</body>', 1),
            encoding="utf-8")
        return "index.html:external_link"
    return ""


# 假绿族变异体：**(kind, 期望 verdict)**；期望"非绿"，测的是尺子看不看得见「没测到 / 冒充」。
# 固定跑在内置夹具上（它测的是尺子，不是真件），所以与 mode 无关、结果可复现。
_FAKEGREEN: tuple[tuple[str, str], ...] = (
    ("state_drop_entities", (("NA", RC_NA), ("FAIL", RC_FAIL))),  # 抹掉 entities ⇒ 非绿即可（实测新腿加入后落 FAIL）
    ("fixture_impersonation", (("FAIL", RC_FAIL),)),      # 拿夹具树、不给 --allow-fixture ⇒ 冒充真件必须拒绿
)


def _mutate_fakegreen(kind: str, app: Path) -> str:
    """两种「绿得不该绿」的做坏，返回人话描述；做不成就返回空串（自检算成没抓住）。"""
    if kind == "fixture_impersonation":
        # 不用改任何东西：内置夹具树本身就带自曝标记，判据是「关掉 --allow-fixture 后敢不敢判绿」
        return "fixture_tree_without_allow_flag" if _fixture_marker(app) else ""
    if kind == "state_drop_entities":
        server = app / "server.py"
        text = server.read_text(encoding="utf-8")
        needle = '"strings": cat["strings"], "entities": len(STATE["entities"])'
        if needle not in text:
            return ""
        server.write_text(text.replace(needle, '"strings": cat["strings"]'), encoding="utf-8")
        return "server.py:/api/state:drop_entities"
    return ""


def _run_gate(app: Path, work: Path, out: Path, *,
              allow_fixture: bool = False) -> tuple[str, int, str]:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--check", "--app", str(app),
           "--workdir", str(work), "--json-out", str(out), "--quiet"]
    if allow_fixture:                      # 自检专用；交付判定一律不带（会印 fixture_allowed=1 自曝）
        cmd.append("--allow-fixture")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    verdict = ""
    if out.is_file():
        try:
            verdict = str(json.loads(out.read_text(encoding="utf-8")).get("verdict") or "")
        except Exception:
            verdict = ""
    if not verdict:
        match = VERDICT_RE.search(proc.stdout or "")
        verdict = match.group("verdict") if match else "NONE"
    return verdict, proc.returncode, (proc.stdout or "").strip().splitlines()[-1:] and \
        (proc.stdout or "").strip().splitlines()[-1] or ""


def run_selftest(app_dir: Path, *, quiet: bool = False) -> int:
    mutants = ("drop_catalog_key", "clone_en_text", "drop_dom_id", "external_link")
    real_present = (app_dir / "server.py").is_file() and (app_dir / "web" / "index.html").is_file()
    scratch = Path(tempfile.mkdtemp(prefix="cad_ui_gate_selftest_"))
    source = app_dir
    mode = "real"
    if not real_present:
        source = _write_builtin_fixture(scratch / "fixture")
        mode = "builtin(real_app_absent)"
    # 内置夹具模式下，内容族变异体必须带 --allow-fixture 跑：否则它们红是因为「夹具冒充」，
    # 而不是因为变异本身 ⇒ 自检会变成假绿（红了，但红错了原因）。
    allow = mode != "real"
    before_hash = _sha_tree(app_dir)
    base = scratch / "base"
    (base / "apps").mkdir(parents=True)
    _copy_tree(source, base / "apps" / "opencad")
    oss = Path(__file__).resolve().parents[2]
    for pkg in ("cadcore", "dxf"):
        if (oss / "packages" / pkg).is_dir():
            (base / "packages").mkdir(parents=True, exist_ok=True)
            _copy_tree(oss / "packages" / pkg, base / "packages" / pkg)

    # 夹具自证：拷贝里真有冻结点，且真树在自检前后逐字节不变
    self_proof = ((base / "apps" / "opencad" / "server.py").is_file()
                  and (base / "apps" / "opencad" / "web" / "index.html").is_file())
    if not self_proof:
        print(f"{SELFTEST_GATE}: verdict=NA reason=fixture_selfproof_failed "
              f"fixture={mode} rc={RC_NA}")
        return RC_NA

    control_app = base / "apps" / "opencad"
    control_verdict, control_rc, _ = _run_gate(control_app, scratch / "w_control",
                                               scratch / "control.json", allow_fixture=allow)
    control_ok = control_verdict == "PASS"
    fixture_control = "not_run"
    if not control_ok:
        # 真件自己就红时（比如它正在被另一席写），另起一份**内置契约夹具**的干净对照：
        # 正整数腿必须能在夹具上变绿，否则「变异体报红」可能只是因为真件本来就红（假红）。
        fx_src = _write_builtin_fixture(scratch / "fixture_control")
        fx_base = scratch / "fxbase"
        (fx_base / "apps").mkdir(parents=True)
        _copy_tree(fx_src, fx_base / "apps" / "opencad")
        if (base / "packages").is_dir():
            _copy_tree(base / "packages", fx_base / "packages")
        fixture_control, _, _ = _run_gate(fx_base / "apps" / "opencad", scratch / "w_fx",
                                          scratch / "fxcontrol.json", allow_fixture=True)
    reds, results = 0, []
    for kind in mutants:
        case = scratch / ("case_" + kind)
        (case / "apps").mkdir(parents=True)
        # 内容族**固定跑在内置契约夹具上**：真件的静态面未必来自 `web/**`（实测：真件语言包在
        # `packages/cadcore/i18n/catalog.py`，`web/i18n/` 根本不存在）⇒ 对真件做文件级变异会打在
        # **没被服务的那份副本**上，变异体不变红却与尺子无关 —— 这种"红了/绿了都不是因为变异"的自检
        # 等于假证。夹具是被真服务的那个实现，变异必然打在承重件上。真件另由 control 腿端到端跑。
        _copy_tree(_write_builtin_fixture(case / "fixture"), case / "apps" / "opencad")
        what = _mutate(kind, case, case / "apps" / "opencad")
        verdict, rc, _ = _run_gate(case / "apps" / "opencad", case / "w", case / "verdict.json",
                                   allow_fixture=True)
        hit = bool(what) and verdict == "FAIL" and rc == RC_FAIL
        reds += 1 if hit else 0
        results.append(f"{kind}={verdict if what else 'MUTATION_FAILED'}({rc})"
                       + ("" if hit else "!NOT_RED"))
    # ── 假绿族：内容没坏、但「该测的没测到」或「拿夹具冒充真件」时，判定行绝不许绿 ──
    fakegreen, fg_results = 0, []
    for kind, accepts in _FAKEGREEN:
        case = scratch / ("fg_" + kind)
        (case / "apps").mkdir(parents=True)
        _copy_tree(_write_builtin_fixture(case / "fixture"), case / "apps" / "opencad")
        what = _mutate_fakegreen(kind, case / "apps" / "opencad")
        verdict, rc, _ = _run_gate(case / "apps" / "opencad", case / "w", case / "verdict.json",
                                   allow_fixture=(kind != "fixture_impersonation"))
        # 假绿族的判据是「**非绿**」，不是某个特定色：`state_drop_entities` 早先落 NA(2)，
        # 本轮加了 static_refs / i18n_derived 两条承重腿后落 FAIL(1) —— 两者都算抓住，
        # 钉死单一期望值会让尺子因为"变得更严"而自判失败（这正是本轮实撞）。
        hit = bool(what) and any(verdict == v and rc == r for v, r in accepts)
        fakegreen += 1 if hit else 0
        fg_results.append(f"{kind}={verdict if what else 'MUTATION_FAILED'}({rc})"
                          + ("" if hit else "!NOT_CAUGHT(want="
                             + "|".join(f"{v}/{r}" for v, r in accepts) + ")"))
    after_hash = _sha_tree(app_dir)
    tree_untouched = 1 if before_hash == after_hash else 0
    ok = ((control_ok or fixture_control == "PASS") and reds == len(mutants)
          and fakegreen == len(_FAKEGREEN) and tree_untouched == 1)
    rc = RC_PASS if ok else RC_FAIL
    if not quiet:
        for item in results:
            print("  mutant " + item)
        for item in fg_results:
            print("  fakegreen " + item)
        print(f"  control verdict={control_verdict} rc={control_rc} fixture_control={fixture_control}")
    print(f"{SELFTEST_GATE}: verdict={'PASS' if ok else 'FAIL'} mutants_red={reds}/{len(mutants)} "
          f"fakegreen_caught={fakegreen}/{len(_FAKEGREEN)} "
          f"control={'PASS' if control_ok else 'FAIL'} control_fixture={fixture_control} "
          f"fixture={mode} app_present={1 if real_present else 0} "
          f"tree_untouched={tree_untouched} selfproof={1 if self_proof else 0} "
          f"mutants={'|'.join(results)} fakegreen={'|'.join(fg_results)} rc={rc}")
    return rc


# ── CLI ────────────────────────────────────────────────────────────────────────────────
def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ui_gate",
        description="G9：CAD GUI 外壳门（起真服务 → 真打 API → 真切语言 → 真导出回读）",
        epilog="判定行：CAD_UI_VERDICT: verdict=PASS|FAIL|NA …（三态 rc 0/1/2）")
    parser.add_argument("--check", action="store_true", help="跑一次判定（缺省动作）")
    parser.add_argument("--selftest", action="store_true", help="阴阳双证：4 个变异体 + 干净对照")
    parser.add_argument("--app", default=None, metavar="DIR", help="外壳目录（缺省 apps/opencad）")
    parser.add_argument("--workdir", default=None, metavar="DIR", help="落地目录（截图等）")
    parser.add_argument("--json-out", default=None, metavar="FILE", help="把判定结果落成 JSON")
    parser.add_argument("--quiet", action="store_true", help="只打判定行")
    parser.add_argument("--allow-fixture", action="store_true",
                        help="放行内置夹具（只给 --selftest 内部用；判定行会印 fixture_allowed=1 自曝）")
    args = parser.parse_args(argv)
    oss = Path(__file__).resolve().parents[2]
    app_dir = Path(args.app).resolve() if args.app else (oss / "apps" / "opencad")
    if args.selftest:
        return run_selftest(app_dir, quiet=args.quiet)
    workdir = Path(args.workdir).resolve() if args.workdir else \
        Path(tempfile.mkdtemp(prefix="cad_ui_gate_"))
    report = run_check(app_dir, workdir, quiet=args.quiet, allow_fixture=args.allow_fixture)
    if not args.quiet:
        for note in report.notes:
            print("  note " + note)
        for reason in report.reasons:
            print("  FAIL " + reason)
    print(report.line())
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report.rc


if __name__ == "__main__":
    sys.exit(main())
