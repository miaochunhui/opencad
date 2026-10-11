#!/usr/bin/env python3
"""param_boundary_gate.py —— G27 参数边界门：**接受 or 结构化拒绝**，绝不允许
裸异常 / 越界落库 / 静默接受非法值；**报了成功就必须真改**（假成功 = 缺陷）。

口径（Lead 裁决，写进门里当判据，不许放宽）
-------------------------------------------
产品自述可存范围是 ``|v| <= 2**53``；实测行为 = **边界内接受、越界结构化拒绝**
（``N.circle.radius.i2p53`` / ``i2p53m1`` ok=1；``i2p53p1`` / ``i2p63`` ok=0，均为 A=0 无缺陷）。
所以本门的判据是「**接受 or 结构化拒绝**」，**不是**「必须接受」—— 把"必须接受"写成判据是错的期望。

五条判据（每条一个计数器，互不重叠；每条都能追到具体用例）
------------------------------------------------------------
* `naked`（criterion a）—— 响应**原文**里出现裸 Python 异常名（TypeError / ValueError /
  OverflowError / AttributeError / IndexError / ZeroDivisionError / RecursionError / UnicodeEncodeError …）。
  实测根因 RC1a：309 位整数在判据执行**之前**就让 `float(v)` 抛 `OverflowError`。
* `silent_illegal`（criterion b）—— 被判为**非法**的入参被**接受**了。非法 = 类型/形状/枚举/单位非法
  （bool 不是数值：Python 里 `bool` 是 `int` 子类，必须显式拒；`"10"` / `null` / 非整数浮点 / 非 i18n key
  的 text_key / 未知单位 / 非法枚举）。实测根因 RC1b/RC9：`/api/open` 把布尔 `value_nm` 静默收下并落库。
* `persisted`（criterion c）—— 被**拒绝**的请求在文档里留下了状态（拒绝必须原子：`/api/state` 的 L1 文本
  与请求前逐字节相同；读不到 after 状态时**按违规记**，不许把"没采到"当"没落库"）。
  另含白盒腿：`Workspace._mutate` 注入"改完再抛"的动作必须逐字节回滚（黑盒用例都在校验期就拒，
  只有这条腿能证明**失败路径**的原子性）。实测根因 RC2：`_mutate` 失败不回滚 ⇒ `ok:false` 与真源已改同时发生。
* `structured`（criterion d）—— 越界/不可表示数值**未被结构化拒绝**（被接受），或处理方式**根本没结构化**
  （传输超时 / 服务崩溃 / 非 JSON / 5xx / 兜底 `internal_error` / 缺 `error` 或 `detail`），
  或**阳性对照被误拒**（防"一律拒绝也算绿"）。实测根因 RC2/R11：越界变换结果静默落库。
  口径边界（**不许收紧**）：`|v| <= 2**53` 是**落库**字段的量程；纯查询参数（`snap_query.tolerance_nm`）
  不落库 ⇒ 有限的大值被接受是正确行为（扫描席 `accept_tol` 同口径），只有**不可表示**（`nan`/`inf`/309 位）
  才算"必须结构化拒绝"。判据一句话：**越界落库**与**裸异常**才是缺陷，"查询参数接受大值"不是。
* `no_effect`（criterion e）—— **报成功（`ok:true`）但文档真源逐字节没变**（`/api/state` 的 L1 文本
  做摘要；这就是本会话 B 线 `plot`/`define_drawing_template` 与 C 线 5 条 phantom 上反复出现的"假成功"）。
  只对**声称会改文档**的调用判：用例自己声明 `mutates=True`（画 `add_line`/`add_circle`、
  改 `scale`/`array_linear`/`add_dimension`、样式档 `/api/standard`），**三类各≥1 条**；
  查询/只读调用（`snap_query`）不改文档是正确行为，**不在这条腿里**（声明为 `mutates=False`）。
  与前四条互补：`silent_illegal`/`structured` 抓"不该成功却成功"，这条抓"**成功了却什么也没做**"——
  下游拿 `ok:true` 当"已生效"，真源里查无此事。
  判据的**可分辨性**由判定行的 `no_effect_checked=`（这把尺子真量过几条）与 `no_effect_dims=`（量过几类）
  背书：**空集不许假绿**（`no_effect_checked=0` 时 PASS 不成立）。读不到状态（before/after 任一读失败）
  ⇒ **按违规记**，不许把"没采到"当成"没变"（同 `persisted` 的读侧口径）。

第 6 条（产品面契约，独立于五计数器，见 `PARAM_BOUNDARY_DETAIL` 的 `artifact_ok`）
----------------------------------------------------------------------------------
* 被接受的标注文本必须**进产物**：草图 DXF（999 注释）与图纸 PDF（NOTES 层）**两条承载路各自**都要能
  grep 到；没加过的文本一个都不许出现（阴性对照）。为什么不是"任一命中"——实测变异体
  `no_annotation_text` 只掐掉 DXF 那条路时，"任一命中"口径把它判绿（文本已经半丢还看不见）。
  实测根因 RC4：`doc.annotations` 文本一个字都不进产物。

跑哪条通道（选型理由，成本更低的那条）
--------------------------------------
**起真服务、打真 HTTP**（`apps/opencad/server.py --port 0`），不走域 API 直调。理由：
四判据里有三条是**产品面口径** —— `error`/`detail` 的响应形状、`/api/state` 的落库状态、HTTP 状态码；
域 API 直调既量不到这些，也复制不出"持锁挂死"这一实测失效模式（`array_linear(rows=10**309)` 让
其后**所有**请求超时）。成本：一个服务进程 + 每用例 ~4 次请求（~5 ms/次），全矩阵 ~10 s。
字高（`annotations[].args.height_nm`）与网格间距（`meta.precision_grid.spacing_nm`）**没有**
`/api/command` 动词，就走它们真正的产品面入口 —— `/api/open` 的 **L1 装载闸**
（`docmodel.load_blocking_problems`）；每个用例都记 `channel`，不说"测过了"而不说测的是哪条面。

用例矩阵（≥40；实际条数见判定行 `cases=N`，逐条只以本文件的表为准）
------------------------------------------------------------------
数值矩阵：`0` / 负 / `1e-9` / `1e308` / `inf` / `-inf` / `nan` / 字符串 / `null` / 布尔 /
`2**53` / `2**53-1` / `2**53+1` / `2**63` / 309 位整数；维度：半径（`add_circle.radius_nm`）、
字高（`height_nm`）、比例（`scale.factor`）、网格间距（`spacing_nm`）、捕捉容差（`snap_query.tolerance_nm`）、
标注 `value_nm`、标注文本（空/超长/换行/中文/emoji/LaTeX/孤立代理对）、`text_key`（空/空白/换行/中文/emoji/
LaTeX/引号/非字符串）、非法枚举（`add_dimension.kind` / `snap_query.kinds` / `/api/standard`）、
单位名（`furlong` / 空 / `null` / 数字 / `"MM"` / `" mm "`）、计数型（`rows`/`cols`/`sides`）。

判定行（唯一口径；`cases=0` 记 NA 不记 PASS）
-------------------------------------------
    PARAM_BOUNDARY verdict=PASS|FAIL|NA cases=N naked=N silent_illegal=N persisted=N structured=N \
        no_effect=N no_effect_checked=M no_effect_dims=K rc=0|1|2

* PASS 的充分条件：`cases>0` ∧ 五计数器全 0 ∧ 阳性对照全绿 ∧ `artifact_ok=1` ∧ 白盒原子性腿绿
  ∧ `no_effect_dims>=3`（这条腿真在 ≥3 类调用上量过，不是空集假绿）。
* `no_effect_checked` / `no_effect_dims` 是**尺子自身的取证**（不是判据）：前者=被这条腿判过的用例数，
  后者=其中覆盖的调用类别数；干净树上还要求这些用例**真的改了摘要**（`PARAM_BOUNDARY_DETAIL` 的
  `no_effect_changed=`），否则"看得见绿"就没有证据。
* 明细一律打在 `PARAM_BOUNDARY_DETAIL` / `PARAM_BOUNDARY_CASE` 行（判定行本体只此一条）。

用法
----
    python3 oss-alternatives/tools/cad/param_boundary_gate.py              # 检查模式（裸跑，run-gates 用）
    python3 oss-alternatives/tools/cad/param_boundary_gate.py --check      # 同上（显式写法）
    python3 oss-alternatives/tools/cad/param_boundary_gate.py --json       # 机读（单行 JSON）
    python3 oss-alternatives/tools/cad/param_boundary_gate.py --selftest   # 尺子自证：每个变异体各自被抓 + 干净对照绿（条数见判定行 mutants=N）
    python3 oss-alternatives/tools/cad/param_boundary_gate.py --tree <dir> # 对另一棵树跑（--selftest 内部用）
    python3 oss-alternatives/tools/cad/param_boundary_gate.py --log-dir <dir>   # 全量逐用例日志（缺省落 3FS tmp）

安全与只读
----------
* 只在 `tempfile.mkdtemp()` 里造 out-root；`--selftest` 把产品树复制到临时目录再**在副本上**打变异补丁，
  **不碰真树、不改产品码**。
* 挂住的实现会一直吃内存（实测 RSS 涨到 GB 级）⇒ 对服务进程装 RSS watchdog（超 `--rss-cap-mb` 即 SIGKILL）；
  任一条用例超时/传输失败即**重启服务再继续**（挂死会持锁，不重启后面全是连带超时）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

RC_PASS, RC_FAIL, RC_NA = 0, 1, 2
HEAD = "PARAM_BOUNDARY"
SELFTEST_HEAD = "PARAM_BOUNDARY_SELFTEST"
MM = 1_000_000
#: 产品自述可存上界（`cadcore.sketch.constraints.MAX_ABS_NM`，唯一真源，含端点）。
MAX_NM = 1 << 53
HUGE = int("9" * 309)
#: 单条 HTTP 用例的墙钟预算（秒）：挂住的实现给不出响应，只能超时。
BUDGET_S = 6.0
#: 服务进程 RSS 上限（MB）：变异体会真的无限分配 ⇒ 到顶就 SIGKILL（模拟 OOM，保整机）。
RSS_CAP_MB = 2048
#: D8：跑得久的脚本要自己把执行日志落盘（缺省 3FS tmp；写不进去只告警，不改判定）。
LOG_DIR_DEFAULT = "/hf3fs-jd/data/tmp/cad-param-boundary/logs"
#: 响应原文里**不许**出现的内建异常名（扫描尺子 `find_exceptions` 的常用子集，含任务点名的七个）。
BUILTIN_EXCEPTION_NAMES = (
    "TypeError", "ValueError", "OverflowError", "AttributeError", "IndexError",
    "ZeroDivisionError", "RecursionError", "KeyError", "UnicodeEncodeError", "UnicodeDecodeError",
    "MemoryError", "RuntimeError", "StopIteration", "ArithmeticError", "AssertionError",
)
#: 产物里的阳性/阴性标记（root cause RC4：标注文本必须逐字进 DXF/PDF）。
MARKER = "PARAM_BOUNDARY_MARKER_XYZ"
NEG_MARKER = "PARAM_BOUNDARY_NOT_ADDED_XYZ"
#: `no_effect` 腿要求覆盖的**调用类别数下限**（画 / 改 / 样式各≥1）：
#: 少于这个数就说明这条腿被缩成空集或单类 ⇒ PASS 不成立（空集不许假绿，E2）。
NO_EFFECT_MIN_DIMS = 3

DOC_UUID = "b6000000-0000-4000-8000-0000000000aa"
SKETCH_UUID = "b6000000-0000-4000-8000-0000000000ab"
LINE_UUID = "bbbb2222-0000-4000-8000-000000000011"
ANN_UUID = "aaaa1111-0000-4000-8000-000000000001"

BASE_DOC: dict[str, Any] = {
    "schema_version": 1,
    "meta": {"doc_uuid": DOC_UUID, "created_at": "2026-10-01T00:00:00Z",
             "app_version": "0.1.0", "title_key": "doc.untitled"},
    "unit": "mm", "standard_profile": "gbt", "projection_angle": "first",
    "sketches": [{"uuid": SKETCH_UUID, "plane": "XY", "entities": [], "constraints": [],
                  "dimensions": [], "solved": None}],
    "features": [], "annotations": [], "sheets": [],
}


# --------------------------------------------------------------------------------------
# 树布局
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tree:
    """一棵产品树：`root/oss-alternatives/{apps/opencad,packages/cadcore}`。"""

    root: Path

    @property
    def server_py(self) -> Path:
        return self.root / "oss-alternatives" / "apps" / "opencad" / "server.py"

    @property
    def constraints_py(self) -> Path:
        return self.root / "oss-alternatives" / "packages" / "cadcore" / "cadcore" / "sketch" / "constraints.py"

    @property
    def docmodel_py(self) -> Path:
        return self.root / "oss-alternatives" / "packages" / "cadcore" / "cadcore" / "docmodel.py"

    @property
    def units_py(self) -> Path:
        return self.root / "oss-alternatives" / "packages" / "cadcore" / "cadcore" / "units.py"

    @property
    def dxf_py(self) -> Path:
        return self.root / "oss-alternatives" / "packages" / "cadcore" / "cadcore" / "io" / "dxf_bridge.py"

    def exists(self) -> bool:
        return all(path.is_file() for path in
                   (self.server_py, self.constraints_py, self.docmodel_py, self.units_py, self.dxf_py))


# --------------------------------------------------------------------------------------
# L1 夹具
# --------------------------------------------------------------------------------------


def _line() -> dict[str, Any]:
    return {"uuid": LINE_UUID, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": 0, "y1": 0, "x2": 10 * MM, "y2": 0}}


def _annotation(**over: Any) -> dict[str, Any]:
    ann: dict[str, Any] = {"uuid": ANN_UUID, "target": [LINE_UUID, "start"], "kind": "linear",
                           "value_nm": 5 * MM, "text_key": None, "literal": True,
                           "text": MARKER, "args": {}}
    ann.update(over)
    return ann


def _doc(*, unit: Any = "mm", with_line: bool = True, annotations: Sequence[Any] = (),
         grid: Any = None) -> dict[str, Any]:
    doc = json.loads(json.dumps(BASE_DOC))
    doc["unit"] = unit
    if with_line:
        doc["sketches"][0]["entities"] = [_line()]
    doc["annotations"] = list(annotations)
    if grid is not None:
        doc["meta"]["precision_grid"] = dict(grid)
    return doc


#: 数值矩阵的行：`(label, value, illegal, oor)`。
#: `illegal` = 产品自己的尺子判它非法（类型/形状/正性/枚举）；被**接受** ⇒ `silent_illegal`。
#: `oor` = **落库字段**越界（`|v| > 2**53`）或**不可表示**（`nan` / `inf` / 309 位整数）；
#: 被**接受** ⇒ `structured` 违规。查询参数（`snap_query.tolerance_nm`）另按"不可表示"收窄，见该处注释。
#: 两项都 False 的行 = "接受 or 结构化拒绝"都对（例：恰好落在边界上的 `2**53`、有限的大容差）。
NUM_MATRIX: tuple[tuple[str, Any, bool, bool], ...] = (
    ("zero", 0, True, False),
    ("neg5mm", -5 * MM, True, False),
    ("tiny1e-9", 1e-9, True, False),
    ("f1e308", 1e308, False, True),
    ("inf", float("inf"), False, True),
    ("neginf", float("-inf"), False, True),
    ("nan", float("nan"), False, True),
    ("str10", "10", True, False),
    ("strabc", "abc", True, False),
    ("strinf", "inf", True, False),
    ("null", None, True, False),
    ("true", True, True, False),
    ("false", False, True, False),
    ("i2p53m1", MAX_NM - 1, False, False),
    ("i2p53", MAX_NM, False, False),
    ("i2p53p1", MAX_NM + 1, False, True),
    ("i2p63", 1 << 63, False, True),
    ("huge309", HUGE, False, True),
)


@dataclass(frozen=True)
class Case:
    """一条参数边界用例。

    `probe` / `setup` 是 HTTP 步骤 `(method, path, body)`；体里的 `{line}` / `{circle}` 在运行时
    替换成播种出来的 uuid。`control=True` 的用例**必须被接受**（阳性对照，防"一律拒绝也算绿"）。
    `mutates=True` 的用例**声称会改文档**：报了 `ok:true` 却让真源摘要逐字节不变 ⇒ `no_effect` 违规
    （查询/只读调用必须留 `mutates=False`，否则会把"只读是正确的"误判成缺陷）。
    """

    cid: str
    dim: str
    probe: tuple[str, str, Any]
    seed: str = ""          # "" | "line" | "circle"
    illegal: bool = False
    oor: bool = False
    control: bool = False
    mutates: bool = False
    note: str = ""


def _cmd(cid: str, dim: str, command: str, args: Mapping[str, Any], **kw: Any) -> Case:
    return Case(cid, dim, ("POST", "/api/command", {"command": command, "args": dict(args)}), **kw)


def _open(cid: str, dim: str, fixture: str, **kw: Any) -> Case:
    # `/api/open` 是 **GET** 路由（`?path=`），不是 POST —— 实测 404 会把整条腿记成"未结构化"。
    return Case(cid, dim, ("GET", "/api/open?path=" + quote("cases/" + fixture), None), **kw)


def _is_open(case: Case) -> bool:
    return case.probe[1].startswith("/api/open")


def _fixtures() -> dict[str, Any]:
    """所有 `/api/open` 用例的真源夹具（写进临时 out-root 的 `cases/`，**仓内不留产物**）。"""
    fx: dict[str, Any] = {"_base_empty.json": _doc(with_line=False)}

    # 标注 value_nm（RC1b：布尔/字符串/浮点/309 位整数静默落库）
    for label, value in (("bool", True), ("str", "5"), ("float", 1.5), ("huge309", HUGE),
                         ("i2p53", MAX_NM), ("i2p53p1", MAX_NM + 1), ("null", None)):
        fx["ann_value_%s.json" % label] = _doc(annotations=[_annotation(value_nm=value)])

    # 标注文本（literal 自由文本；RC4：文本必须进产物）
    for label, text in (("empty", ""), ("overlong", "L" * 100_000), ("newline", "a\nb"),
                        ("cjk", "中文标注"), ("emoji", "标注😀✅"), ("latex", r"\frac{a}{b}$%&_{}"),
                        ("surrogate", "\ud800")):
        fx["ann_text_%s.json" % label] = _doc(annotations=[_annotation(literal=True, text=text)])
    fx["ann_text_marker.json"] = _doc(annotations=[_annotation()])

    # i18n key（RC9：写侧/读侧必须同一把尺子 `cadcore.i18n.keys.is_text_key`）
    for label, key in (("empty", ""), ("spaces", "   "), ("newline", "a\nb"),
                       ("cjk", "中文标注 😀"), ("quotes", 'he said "hi"'),
                       ("latex", r"\frac{a}{b}"), ("nonstr", 5)):
        fx["ann_key_%s.json" % label] = _doc(
            annotations=[_annotation(literal=False, text=None, text_key=key)])
    fx["ann_key_valid.json"] = _doc(
        annotations=[_annotation(literal=False, text=None, text_key="dim.length.overall")])

    # 字高（annotations[].args.height_nm —— 没有 /api/command 动词，走 L1 装载闸）
    for label, value, _illegal, _oor in NUM_MATRIX:
        fx["ann_height_%s.json" % label] = _doc(
            annotations=[_annotation(args={"height_nm": value})])
    fx["ann_height_valid.json"] = _doc(annotations=[_annotation(args={"height_nm": 2_500_000})])

    # 网格间距（doc.meta.precision_grid.spacing_nm —— 同上）
    for label, value, _illegal, _oor in NUM_MATRIX:
        fx["grid_%s.json" % label] = _doc(grid={"enabled": True, "spacing_nm": value})
    fx["grid_valid.json"] = _doc(grid={"enabled": True, "spacing_nm": 10 * MM})

    # 单位名（doc.unit；`unit_unknown` / `unit_missing` 是装载闸判据）
    for label, value in (("furlong", "furlong"), ("empty", ""), ("null", None), ("int0", 0),
                         ("upper", "MM"), ("spaced", " mm ")):
        fx["unit_%s.json" % label] = _doc(unit=value)
    fx["unit_valid.json"] = _doc(unit="mm")
    return fx


def _cases() -> tuple[Case, ...]:
    cases: list[Case] = []

    # ── 半径（add_circle.radius_nm） ────────────────────────────────────────────────
    for label, value, illegal, oor in NUM_MATRIX:
        cases.append(_cmd("cmd.circle.radius.%s" % label, "radius", "add_circle",
                          {"center": [0, 0], "radius_nm": value},
                          illegal=illegal, oor=oor, note=repr(value)[:40]))

    # ── 比例（scale.factor）：factor 的越界判据在**结果**上（2**53 倍的半径必然超量程）──
    for label, value in (("zero", 0), ("neg2", -2.0), ("tiny1e-9", 1e-9), ("f1e308", 1e308),
                         ("inf", float("inf")), ("nan", float("nan")), ("str10", "10"),
                         ("null", None), ("true", True), ("f0p5", 0.5),
                         ("i2p53", MAX_NM), ("i2p53p1", MAX_NM + 1), ("huge309", HUGE)):
        illegal = isinstance(value, bool) or value is None or isinstance(value, str) \
            or (isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0)
        oor = label in ("f1e308", "inf", "nan", "i2p53", "i2p53p1", "huge309")
        cases.append(_cmd("cmd.scale.factor.%s" % label, "scale", "scale",
                          {"refs": ["{circle}"], "center_nm": [0, 0], "factor": value},
                          seed="circle", illegal=illegal, oor=oor, note=repr(value)[:40]))

    # ── 捕捉容差（snap_query.tolerance_nm） ─────────────────────────────────────────
    # 口径（**不许收紧**）：这是**查询**参数，不落库 ⇒ 产品自述的 `|v| <= 2**53` 存范围**不适用**；
    # 有限的大值（`1e308` / `2**53+1` / `2**63`）被接受是**正确行为**（扫描席 `accept_tol` 同口径，
    # 修后复扫 `defects_A=0`）。这里只有**不可表示**（nan / inf / 309 位整数）才算"必须结构化拒绝"——
    # 它们要么进不了 `float()`，要么连半径都算不出来。
    for label, value, illegal, oor in (("neg5mm", -5 * MM, True, False), ("zero", 0, False, False),
                                       ("tiny1e-9", 1e-9, False, False), ("f1e308", 1e308, False, False),
                                       ("inf", float("inf"), False, True), ("nan", float("nan"), False, True),
                                       ("str10", "10", True, False), ("null", None, True, False),
                                       ("true", True, True, False), ("i2p53", MAX_NM, False, False),
                                       ("i2p53p1", MAX_NM + 1, False, False),
                                       ("i2p63", 1 << 63, False, False),
                                       ("neginf", float("-inf"), False, True),
                                       ("huge309", HUGE, False, True)):
        cases.append(_cmd("cmd.snap.tolerance.%s" % label, "snap_tolerance", "snap_query",
                          {"kinds": ["quadrant"], "point_nm": [0, 0], "tolerance_nm": value},
                          seed="line", illegal=illegal, oor=oor, note=repr(value)[:40]))

    # ── 标注 value_nm（L1 装载闸） ──────────────────────────────────────────────────
    for label, illegal, oor in (("bool", True, False), ("str", True, False), ("float", True, False),
                                ("huge309", False, True), ("i2p53", False, False),
                                ("i2p53p1", False, True), ("null", False, False)):
        cases.append(_open("open.ann.value_nm.%s" % label, "ann_value_nm",
                           "ann_value_%s.json" % label, illegal=illegal, oor=oor))

    # ── 标注文本：literal 自由文本（空/超长/换行/中文/emoji/LaTeX/孤立代理对） ─────────
    for label, illegal in (("empty", False), ("overlong", False), ("newline", False), ("cjk", False),
                           ("emoji", False), ("latex", False), ("surrogate", True)):
        cases.append(_open("open.ann.text.%s" % label, "ann_text_literal",
                           "ann_text_%s.json" % label, illegal=illegal))

    # ── 标注 text_key（i18n key 尺子；非 key 形状必须结构化拒） ──────────────────────
    for label in ("empty", "spaces", "newline", "cjk", "quotes", "latex", "nonstr"):
        cases.append(_open("open.ann.text_key.%s" % label, "ann_text_key",
                           "ann_key_%s.json" % label, illegal=True))

    # ── 字高（annotations[].args.height_nm，装载闸） ────────────────────────────────
    for label, value, illegal, oor in NUM_MATRIX:
        # 正性不是装载闸的判据（`nm_is_null/bool/not_integer/not_a_number/out_of_range` 才是）：
        # 0 / 负值只算"接受 or 拒绝都对"，不记违规 —— 判据只认产品自己的尺子。
        cases.append(_open("open.ann.height.%s" % label, "text_height",
                           "ann_height_%s.json" % label,
                           illegal=illegal and label in ("str10", "strabc", "strinf", "null",
                                                         "true", "false"),
                           oor=oor))

    # ── 网格间距（meta.precision_grid.spacing_nm，装载闸） ──────────────────────────
    for label, value, illegal, oor in NUM_MATRIX:
        cases.append(_open("open.grid.spacing.%s" % label, "grid_spacing",
                           "grid_%s.json" % label,
                           illegal=illegal and label in ("str10", "strabc", "strinf", "null",
                                                         "true", "false"),
                           oor=oor))

    # ── 单位名（doc.unit，装载闸的 unit_missing / unit_unknown） ─────────────────────
    for label in ("furlong", "empty", "null", "int0"):
        cases.append(_open("open.unit.%s" % label, "unit", "unit_%s.json" % label, illegal=True))
    for label in ("upper", "spaced"):
        cases.append(_open("open.unit.%s" % label, "unit", "unit_%s.json" % label))

    # ── 非法枚举（kind / kinds / standard） ────────────────────────────────────────
    for label, value in (("empty", ""), ("bogus", "bogus"), ("null", None), ("int123", 123),
                         ("list", ["linear"])):
        cases.append(_cmd("cmd.dim.kind.%s" % label, "enum_dim_kind", "add_dimension",
                          {"kind": value, "refs": [["{line}", "start"], ["{line}", "end"]],
                           "value_nm": 10 * MM},
                          seed="line", illegal=True, note=repr(value)[:40]))
    for label, value in (("bogus", ["no-such-kind"]), ("bare_string", "quadrant"),
                         ("bool_item", [True])):
        cases.append(_cmd("cmd.snap.kinds.%s" % label, "enum_snap_kinds", "snap_query",
                          {"kinds": value, "point_nm": [0, 0], "tolerance_nm": MM},
                          seed="line", illegal=True, note=repr(value)[:40]))
    cases.append(_cmd("cmd.snap.kinds.null", "enum_snap_kinds", "snap_query",
                      {"kinds": None, "point_nm": [0, 0], "tolerance_nm": MM}, seed="line"))
    for label, value in (("bogus", "bogus"), ("null", None), ("int123", 123)):
        cases.append(Case("api.standard.%s" % label, "enum_standard",
                          ("POST", "/api/standard", {"standard": value}), illegal=True,
                          note=repr(value)[:40]))

    # ── 计数型入参（RC3：计数先拒后循环；无界计数会让服务持锁挂死） ───────────────────
    for label, args in (("rows_huge309", {"rows": HUGE, "cols": 2, "dx_nm": MM, "dy_nm": MM}),
                        ("cols_huge309", {"rows": 2, "cols": HUGE, "dx_nm": MM, "dy_nm": MM}),
                        ("product_over_limit", {"rows": 100_000, "cols": 100_000,
                                                "dx_nm": MM, "dy_nm": MM}),
                        ("rows_zero", {"rows": 0, "cols": 2, "dx_nm": MM, "dy_nm": MM}),
                        ("rows_neg", {"rows": -1, "cols": 2, "dx_nm": MM, "dy_nm": MM}),
                        ("rows_str", {"rows": "2", "cols": 2, "dx_nm": MM, "dy_nm": MM}),
                        ("rows_true", {"rows": True, "cols": 2, "dx_nm": MM, "dy_nm": MM})):
        cases.append(_cmd("cmd.array.%s" % label, "count", "array_linear",
                          dict(args, refs=["{line}"]), seed="line", illegal=True))
    cases.append(_cmd("cmd.polygon.sides_huge", "count", "add_polygon",
                      {"center_nm": [0, 0], "radius_nm": 10 * MM, "sides": 10 ** 12}, illegal=True))

    # ── 阳性对照：合法调用必须被**接受**（防"一律拒绝也算绿"） ────────────────────────
    # `mutates=True` 的那几条同时是 **no_effect 腿（第 5 判据）的阳性对照**：它们声称会改文档，
    # 报告 ok=1 就必须让真源摘要真的变。三类调用各有覆盖（画/改/样式），少于 3 类 ⇒ PASS 不成立。
    cases.append(_cmd("control.add_line", "no_effect_draw", "add_line",
                      {"p1": [0, 0], "p2": [10 * MM, 0]}, control=True, mutates=True,
                      note="画：线段必须真的落进草图实体表"))
    cases.append(_cmd("control.add_circle", "no_effect_draw", "add_circle",
                      {"center": [0, 0], "radius_nm": 10 * MM}, control=True, mutates=True,
                      note="画：圆必须真的落进草图实体表"))
    cases.append(_cmd("control.scale", "no_effect_modify", "scale",
                      {"refs": ["{circle}"], "center_nm": [0, 0], "factor": 0.5},
                      seed="circle", control=True, mutates=True,
                      note="改：半径 5mm 按 0.5 缩放必须真的落到几何上"))
    cases.append(_cmd("control.array_linear", "no_effect_modify", "array_linear",
                      {"refs": ["{line}"], "rows": 2, "cols": 2, "dx_nm": 10 * MM, "dy_nm": 10 * MM},
                      seed="line", control=True, mutates=True,
                      note="改：阵列必须真的生出新实体（实体数变、摘要变）"))
    cases.append(_cmd("control.add_dimension_key", "no_effect_modify", "add_dimension",
                      {"kind": "linear", "refs": [["{line}", "start"], ["{line}", "end"]],
                       "value_nm": 10 * MM, "text_key": "dim.length.overall"},
                      seed="line", control=True, mutates=True,
                      note="改：尺寸标注必须真的落进尺寸表"))
    # 样式档（`/api/standard`）是产品面上唯一的**样式口径**写入口：`gbt → iso` 必须真的改
    # `standard_profile`/`projection_angle`（不是幂等 no-op：缺省档是 `gbt`）。
    cases.append(Case("control.standard_iso", "no_effect_style",
                      ("POST", "/api/standard", {"standard": "iso"}), control=True, mutates=True,
                      note="样式：切标准档必须真的落 standard_profile/projection_angle"))
    # 阴性对照（**不在 no_effect 腿内**）：只读查询报 ok=1 且文档不变是**正确行为**，
    # 声明 `mutates=False` ⇒ 一条都不许被这条腿判违规（防"把只读误判成假成功"）。
    cases.append(_cmd("control.snap_query", "control", "snap_query",
                      {"kinds": ["quadrant"], "point_nm": [0, 0], "tolerance_nm": MM},
                      seed="line", control=True,
                      note="只读查询：ok=1 且真源不变是正确行为（no_effect 腿的阴性对照）"))
    cases.append(_open("control.open.ann", "control", "ann_text_marker.json", control=True))
    cases.append(_open("control.open.ann_key", "control", "ann_key_valid.json", control=True))
    cases.append(_open("control.open.ann_height", "control", "ann_height_valid.json", control=True))
    cases.append(_open("control.open.grid", "control", "grid_valid.json", control=True))
    cases.append(_open("control.open.unit", "control", "unit_valid.json", control=True))
    return tuple(cases)


# --------------------------------------------------------------------------------------
# 服务进程（带 RSS watchdog）
# --------------------------------------------------------------------------------------


class Server:
    def __init__(self, *, server_py: Path, out_root: Path, log_path: Path,
                 rss_cap_mb: int = RSS_CAP_MB) -> None:
        self.server_py = server_py
        self.out_root = out_root
        self.log_path = log_path
        self.rss_cap_mb = rss_cap_mb
        self.rss_peak_mb = 0.0
        self.watchdog_killed = False
        self.port = 0
        self.proc: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._log: Any = None
        self.log_text = ""

    def start(self, timeout: float = 30.0) -> "Server":
        self._stop = threading.Event()
        self.watchdog_killed = False
        self.port = 0
        self._log = open(self.log_path, "ab")
        cmd = [sys.executable, str(self.server_py), "--host", "127.0.0.1", "--port", "0",
               "--out-root", str(self.out_root), "--locale", "zh-CN", "--standard", "gbt"]
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early rc={self.proc.returncode} log={self.log_path}")
            line = self._startup_line()
            if line:
                self.port = int(line.split("port=")[1].split()[0])
                self._thread = threading.Thread(target=self._watch, daemon=True)
                self._thread.start()
                return self
            time.sleep(0.05)
        raise RuntimeError(f"no startup verdict within {timeout}s (rc={self.proc.poll()})")

    def _startup_line(self) -> str:
        try:
            self.log_text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        for line in self.log_text.splitlines():
            if line.startswith("OPENCAD_SERVER verdict=PASS"):
                return line
        return ""

    def _rss_mb(self) -> float:
        if self.proc is None:
            return 0.0
        try:
            for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
        except OSError:
            return 0.0
        return 0.0

    def _watch(self) -> None:
        while not self._stop.is_set():
            rss = self._rss_mb()
            self.rss_peak_mb = max(self.rss_peak_mb, rss)
            if rss > self.rss_cap_mb and self.proc and self.proc.poll() is None:
                self.watchdog_killed = True
                self.proc.kill()
                return
            time.sleep(0.2)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        self._stop.set()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self._log is not None:
            self._log.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def restart(self) -> None:
        self.stop()
        try:
            self._startup_line()
        except OSError:
            pass
        self.start()


def _call(port: int, method: str, path: str, body: Any = None, timeout: float = BUDGET_S
          ) -> tuple[int, str, str, float]:
    """返回 `(status, raw_text, transport_error, elapsed_s)`；传输层失败**原样记**，不许吞。"""
    raw = None if body is None else json.dumps(body)
    started = time.monotonic()
    status, text, transport = 0, "", ""
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
        headers = {"Content-Type": "application/json"} if raw is not None else {}
        conn.request(method, path, body=(raw.encode("utf-8") if raw is not None else None),
                     headers=headers)
        resp = conn.getresponse()
        status = int(resp.status)
        text = resp.read().decode("utf-8", "replace")
        conn.close()
    except Exception as exc:  # noqa: BLE001 - 传输层失败是判据的一部分（超时 = 挂住）
        transport = f"{type(exc).__name__}: {exc}"
    return status, text, transport, time.monotonic() - started


def _subst(body: Any, mapping: Mapping[str, str]) -> Any:
    """把体里的 `{line}` / `{circle}` 占位符换成播种出来的 uuid（先序列化再替换，深拷贝）。"""
    text = json.dumps(body)
    for key, value in mapping.items():
        text = text.replace("{%s}" % key, value)
    return json.loads(text)


def _json_or_empty(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------------------
# 白盒腿：`Workspace._mutate` 的原子性（机制本身）
# --------------------------------------------------------------------------------------


def check_mutate_rollback(tree: Tree, workdir: Path) -> dict[str, Any]:
    """注入"改完再抛"的动作：真源必须逐字节回滚、撤销栈不许被压；成功路径必须照旧。

    这条腿是**承重**的：黑盒那批拒绝用例都在"校验期就拒"（还没改文档），只有这里能证明
    `_mutate` 真的回了滚 —— 变异体 `no_rollback` 正是冲它来的。
    """
    app_dir = str(tree.server_py.parent)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import importlib  # noqa: PLC0415

    server_mod = importlib.import_module("server")
    importlib.reload(server_mod)  # 同一进程里换树（selftest 的子进程也走这里，幂等）
    core = server_mod._load_cadcore()
    out_root = workdir / "ws-out"
    out_root.mkdir(parents=True, exist_ok=True)
    ws = server_mod.Workspace(core, locale="zh-CN", standard="gbt", out_root=str(out_root))
    before = ws.l1_text()

    def mutate_then_fail() -> None:
        ws.commands.add_line(ws.sketch, p1=[0, 0], p2=[10 * MM, 0])
        raise RuntimeError("injected: mutation before failure")

    failures: list[str] = []
    try:
        ws._mutate(mutate_then_fail)
        failures.append("注入的失败动作没有抛异常（夹具失效）")
    except RuntimeError:
        pass
    if ws.l1_text() != before:
        failures.append("失败动作改动了真源（没回滚）")
    if ws.undo_stack:
        failures.append("失败动作压了撤销栈")
    if ws.constraints.sketch_entities(ws.sketch):
        failures.append("失败动作留下了实体")

    # 阳性对照：成功动作必须照旧改文档 + 压栈
    ws._mutate(lambda: ws.commands.add_line(ws.sketch, p1=[0, 0], p2=[MM, MM]))
    if ws.l1_text() == before:
        failures.append("成功动作没改文档（对照失效）")
    if len(ws.undo_stack) != 1:
        failures.append(f"成功动作没有正确压栈 depth={len(ws.undo_stack)}")
    return {"ok": not failures, "detail": "; ".join(failures) or "rollback_ok"}


# --------------------------------------------------------------------------------------
# 黑盒腿：真 HTTP 参数边界矩阵
# --------------------------------------------------------------------------------------


def _state_doc(port: int) -> tuple[str, str]:
    """`/api/state` 的 L1 文本；读不到时返回 `("", 原因)` —— 由调用方按**违规**记，不许当"没变"。"""
    status, text, transport, _elapsed = _call(port, "GET", "/api/state", timeout=BUDGET_S)
    if transport:
        return "", "state_transport:%s" % transport
    payload = _json_or_empty(text)
    if status != 200 or payload.get("ok") is not True:
        return "", "state_not_ok:status=%d" % status
    return str(payload.get("doc") or ""), ""


def _seed(port: int, seed: str, uuids: dict[str, str]) -> str:
    """按用例声明播种工作区；返回错误文本（空 = 成功）。"""
    status, text, transport, _elapsed = _call(
        port, "GET", "/api/open?path=" + quote("cases/_base_empty.json"))
    if transport or status != 200 or _json_or_empty(text).get("ok") is not True:
        return "setup_open_failed transport=%s status=%d" % (transport, status)
    if seed == "line":
        status, text, transport, _elapsed = _call(
            port, "POST", "/api/command",
            {"command": "add_line", "args": {"p1": [0, 0], "p2": [10 * MM, 0]}})
        payload = _json_or_empty(text)
        if transport or payload.get("ok") is not True:
            return "setup_line_failed transport=%s status=%d" % (transport, status)
        uuids["line"] = str(payload.get("uuid") or "")
    elif seed == "circle":
        status, text, transport, _elapsed = _call(
            port, "POST", "/api/command",
            {"command": "add_circle", "args": {"center": [0, 0], "radius_nm": 5 * MM}})
        payload = _json_or_empty(text)
        if transport or payload.get("ok") is not True:
            return "setup_circle_failed transport=%s status=%d" % (transport, status)
        uuids["circle"] = str(payload.get("uuid") or "")
    return ""


def _classify(case: Case, *, status: int, text: str, transport: str, elapsed: float,
              doc_before: str, doc_after: str, after_error: str, before_error: str = ""
              ) -> dict[str, Any]:
    """把一条用例归一成 `(accepted / structured_reject / 违规清单)`；判据见模块 docstring。"""
    payload = _json_or_empty(text)
    okay = payload.get("ok")
    error = payload.get("error")
    detail = payload.get("detail")
    accepted = (not transport) and status == 200 and okay is True
    structured_reject = ((not transport) and status in (200, 400, 422) and okay is False
                         and isinstance(error, str) and error != "" and error != "internal_error"
                         and isinstance(detail, str) and detail != "")
    leaked = [name for name in BUILTIN_EXCEPTION_NAMES if name in text]
    # `no_effect` 腿的读数：**两侧都读到了**才算"真为零"；任一侧读不到就是"没采到"⇒ 按违规记（fail-closed）。
    doc_readable = bool(doc_before) and not before_error and not after_error
    doc_changed = doc_readable and doc_before != doc_after

    violations: list[str] = []
    if leaked:
        violations.append("naked")
    if accepted and case.illegal:
        violations.append("silent_illegal")
    if (not accepted) and (after_error or doc_before != doc_after):
        violations.append("persisted")
    if accepted and case.oor:
        violations.append("structured")
    if (not accepted) and not structured_reject:
        violations.append("structured")
    if case.control and not accepted:
        violations.append("structured")
    # 报了成功就必须真改：只对声明 `mutates=True`（声称会改文档）的调用判；查询类不在此腿内。
    if accepted and case.mutates and not doc_changed:
        violations.append("no_effect")
    detail_bits = []
    if transport:
        detail_bits.append("transport=%s" % transport)
    if elapsed >= BUDGET_S:
        detail_bits.append("elapsed=%.1fs>=budget" % elapsed)
    if leaked:
        detail_bits.append("leaked=%s" % ",".join(leaked))
    if error == "internal_error":
        detail_bits.append("internal_error")
    if after_error:
        detail_bits.append(after_error)
    if case.mutates:
        detail_bits.append("mutates=1 doc_readable=%d doc_changed=%d"
                           % (int(doc_readable), int(doc_changed)))
        if not doc_readable:
            detail_bits.append("doc_state_unreadable(按违规记)")
    if case.control and not accepted:
        detail_bits.append("control_rejected")
    return {"cid": case.cid, "dim": case.dim, "channel": "http", "status": status,
            "elapsed_s": round(elapsed, 3), "accepted": accepted,
            "structured_reject": structured_reject, "violations": violations,
            "mutates": bool(case.mutates), "doc_readable": doc_readable,
            "doc_changed": doc_changed,
            "error": error if isinstance(error, str) else "", "detail": "; ".join(detail_bits)}


def check_http(tree: Tree, workdir: Path, cases: Sequence[Case]) -> dict[str, Any]:
    out_root = workdir / "out"
    (out_root / "cases").mkdir(parents=True, exist_ok=True)
    fixtures = _fixtures()
    for name, payload in fixtures.items():
        # `ensure_ascii=True`：孤立代理对（"\ud800"）必须写成 \\ud800 转义，否则夹具自己先炸编码。
        (out_root / "cases" / name).write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    restarts = 0
    server = Server(server_py=tree.server_py, out_root=out_root, log_path=workdir / "server.log",
                    rss_cap_mb=RSS_CAP_MB)
    try:
        server.start()
        uuids: dict[str, str] = {}
        for case in cases:
            if not server.alive():
                server.restart()
                restarts += 1
            setup_error = "" if _is_open(case) else _seed(server.port, case.seed, uuids)
            doc_before, before_error = _state_doc(server.port)
            probe = _subst(case.probe[2], uuids) if case.probe[2] is not None else None
            status, text, transport, elapsed = _call(server.port, case.probe[0], case.probe[1], probe)
            doc_after, after_error = _state_doc(server.port)
            row = _classify(case, status=status, text=text, transport=transport, elapsed=elapsed,
                            doc_before=doc_before, doc_after=doc_after, after_error=after_error,
                            before_error=before_error)
            row["note"] = case.note
            if setup_error:
                row["violations"] = sorted(set(row["violations"]) | {"structured"})
                row["detail"] = (row["detail"] + "; " + setup_error).strip("; ")
            if before_error:
                row["violations"] = sorted(set(row["violations"]) | {"structured"})
                row["detail"] = (row["detail"] + "; " + before_error).strip("; ")
            rows.append(row)
            if transport or elapsed >= BUDGET_S or not server.alive():
                # 挂死的实现持锁 ⇒ 后续请求只会连带超时：重启服务，别把连带记成 N 条独立缺陷。
                server.restart()
                restarts += 1
                uuids = {}
        artifact = _check_artifact(server, out_root)
    finally:
        server.stop()
    return {"rows": rows, "artifact": artifact, "restarts": restarts,
            "rss_peak_mb": round(server.rss_peak_mb, 1),
            "watchdog_killed": server.watchdog_killed}


def _check_artifact(server: Server, out_root: Path) -> dict[str, Any]:
    """RC4：被接受的标注文本必须进产物；没加过的文本一个都不许出现（阴性对照）。"""
    status, text, transport, _elapsed = _call(
        server.port, "GET", "/api/open?path=" + quote("cases/ann_text_marker.json"))
    payload = _json_or_empty(text)
    if transport or status != 200 or payload.get("ok") is not True:
        return {"ok": False, "detail": "open_marker_failed transport=%s status=%d" % (transport, status)}
    status, text, transport, _elapsed = _call(
        server.port, "POST", "/api/export/dxf", {"path": "dxf/ann.dxf"})
    payload = _json_or_empty(text)
    if transport or payload.get("ok") is not True:
        return {"ok": False, "annotation_texts": payload.get("annotation_texts"),
                "error": payload.get("error"), "response": text[:300],
                "detail": "export_failed transport=%s status=%d" % (transport, status)}
    count = payload.get("annotation_texts")
    dxf_path = out_root / "dxf" / "ann.dxf"
    blob = dxf_path.read_bytes() if dxf_path.is_file() else b""
    status, text, transport, _elapsed = _call(
        server.port, "POST", "/api/command", {"command": "export_pdf", "args": {"out": "pdf/ann.pdf"}})
    pdf_path = out_root / "pdf" / "ann.pdf"
    pdf_blob = pdf_path.read_bytes() if pdf_path.is_file() else b""
    # **每个产物面各自都要有**（不是"任一命中"）：实测变异体 `no_annotation_text` 只掐掉草图 DXF 那一条
    # 承载路，PDF 那条仍在 ⇒ `or` 口径会把它判绿（文本已经半丢还看不见）。两条承载路都要能 grep 到。
    in_dxf = MARKER.encode("utf-8") in blob
    in_pdf = MARKER.encode("utf-8") in pdf_blob
    negative = (NEG_MARKER.encode("utf-8") in blob) or (NEG_MARKER.encode("utf-8") in pdf_blob)
    ok = bool(in_dxf) and bool(in_pdf) and not negative and isinstance(count, int) and count >= 1
    return {"ok": ok, "annotation_texts": count, "marker_in_dxf": in_dxf, "marker_in_pdf": in_pdf,
            "marker_in_artifact": bool(in_dxf and in_pdf), "negative_marker_absent": not negative,
            "dxf_bytes": len(blob), "pdf_bytes": len(pdf_blob),
            "detail": "" if ok else "annotation_text_missing_from_artifact"}


# --------------------------------------------------------------------------------------
# 汇总 / 判定行
# --------------------------------------------------------------------------------------


def _summarize(rows: Sequence[Mapping[str, Any]], mechanism: Mapping[str, Any],
               artifact: Mapping[str, Any]) -> dict[str, Any]:
    counts = {key: 0 for key in ("naked", "silent_illegal", "persisted", "structured", "no_effect")}
    for row in rows:
        for violation in row.get("violations") or []:
            if violation in counts:
                counts[violation] += 1
    mechanism_ok = bool(mechanism.get("ok"))
    if not mechanism_ok:
        counts["persisted"] += 1  # 白盒腿失败 = 拒绝路径留状态的**机制**违规（不是黑盒用例）
    controls = [row for row in rows if str(row.get("cid", "")).startswith("control.")]
    controls_ok = sum(1 for row in controls if row.get("accepted"))
    cases = len(rows)
    # `no_effect` 腿的**尺子取证**（不是判据）：这把尺子真判过几条 accepted 的"声称会改文档"用例、
    # 覆盖几类调用、其中真看到几次摘要变化。三类以下的空集/单类 ⇒ 不许 PASS（E2：空集不许假绿）。
    mutating = [row for row in rows if row.get("mutates")]
    no_effect_checked = sum(1 for row in mutating if row.get("accepted"))
    no_effect_changed = sum(1 for row in mutating if row.get("accepted") and row.get("doc_changed"))
    no_effect_dims = len({str(row.get("dim")) for row in mutating if row.get("accepted")})
    ok = (cases > 0 and all(value == 0 for value in counts.values())
          and controls_ok == len(controls) and bool(artifact.get("ok")) and mechanism_ok
          and no_effect_dims >= NO_EFFECT_MIN_DIMS)
    return {"cases": cases, "counts": counts, "mechanism_ok": mechanism_ok,
            "artifact_ok": bool(artifact.get("ok")), "controls_ok": controls_ok,
            "controls_total": len(controls), "no_effect_checked": no_effect_checked,
            "no_effect_changed": no_effect_changed, "no_effect_dims": no_effect_dims,
            "ok": ok}


def _verdict(summary: Mapping[str, Any]) -> tuple[str, int]:
    if int(summary["cases"]) == 0:
        return "NA", RC_NA
    return ("PASS", RC_PASS) if summary["ok"] else ("FAIL", RC_FAIL)


def _verdict_line(summary: Mapping[str, Any], verdict: str, rc: int) -> str:
    counts = summary["counts"]
    return (f"{HEAD} verdict={verdict} cases={summary['cases']} naked={counts['naked']} "
            f"silent_illegal={counts['silent_illegal']} persisted={counts['persisted']} "
            f"structured={counts['structured']} no_effect={counts['no_effect']} "
            f"no_effect_checked={summary['no_effect_checked']} "
            f"no_effect_dims={summary['no_effect_dims']} rc={rc}")


def _write_log(log_dir: str, payload: Mapping[str, Any]) -> str:
    """D8：把本次运行的全量逐用例记录落盘（写不进去只告警，**不改判定**）。"""
    if not log_dir:
        return ""
    try:
        target = Path(log_dir)
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S%z")
        path = target / f"param-boundary-{stamp}.log"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
        return str(path)
    except OSError as exc:
        print(f"{HEAD}_WARN log_write_failed {type(exc).__name__}: {exc}")
        return ""


def run_check(tree: Tree, *, json_out: bool = False, log_dir: str = LOG_DIR_DEFAULT,
              verbose: bool = False) -> int:
    # `cases=0` 一律记 NA（未采）：不记 PASS，也不冒充实测到的红；三种终态各有独立 reason。
    if not tree.exists():
        print(f"{HEAD} verdict=NA reason=tree_incomplete root={tree.root} cases=0 naked=0 "
              f"silent_illegal=0 persisted=0 structured=0 no_effect=0 no_effect_checked=0 "
              f"no_effect_dims=0 rc={RC_NA}")
        return RC_NA
    cases = _cases()
    with tempfile.TemporaryDirectory(prefix="param-boundary-gate-") as tmp:
        workdir = Path(tmp)
        try:
            mechanism = check_mutate_rollback(tree, workdir)
        except Exception as exc:  # noqa: BLE001 - 白盒腿跑不起来 = 未采，不许当绿
            print(f"{HEAD} verdict=NA reason=mechanism_error detail={type(exc).__name__}: {exc} "
                  f"cases=0 naked=0 silent_illegal=0 persisted=0 structured=0 no_effect=0 "
                  f"no_effect_checked=0 no_effect_dims=0 rc={RC_NA}")
            return RC_NA
        try:
            black = check_http(tree, workdir, cases)
        except Exception as exc:  # noqa: BLE001
            print(f"{HEAD} verdict=NA reason=http_leg_error detail={type(exc).__name__}: {exc} "
                  f"cases=0 naked=0 silent_illegal=0 persisted=0 structured=0 no_effect=0 "
                  f"no_effect_checked=0 no_effect_dims=0 rc={RC_NA}")
            return RC_NA
    rows = black["rows"]
    summary = _summarize(rows, mechanism, black["artifact"])
    verdict, rc = _verdict(summary)
    line = _verdict_line(summary, verdict, rc)
    failures = [row for row in rows if row.get("violations")]

    if json_out:
        print(json.dumps({"verdict": verdict, "rc": rc, "line": line, "cases": summary["cases"],
                          "counts": summary["counts"], "controls_ok": summary["controls_ok"],
                          "controls_total": summary["controls_total"],
                          "artifact_ok": summary["artifact_ok"], "mechanism_ok": summary["mechanism_ok"],
                          "no_effect_checked": summary["no_effect_checked"],
                          "no_effect_changed": summary["no_effect_changed"],
                          "no_effect_dims": summary["no_effect_dims"],
                          "no_effect_min_dims": NO_EFFECT_MIN_DIMS,
                          "mechanism": mechanism, "artifact": black["artifact"],
                          "rss_peak_mb": black["rss_peak_mb"], "restarts": black["restarts"],
                          "failures": failures, "rows": rows},
                         ensure_ascii=False, sort_keys=True))
        return rc

    print(line)
    print(f"{HEAD}_DETAIL summary controls={summary['controls_ok']}/{summary['controls_total']} "
          f"artifact_ok={int(summary['artifact_ok'])} mechanism_ok={int(summary['mechanism_ok'])} "
          f"no_effect_checked={summary['no_effect_checked']} "
          f"no_effect_changed={summary['no_effect_changed']} "
          f"no_effect_dims={summary['no_effect_dims']}/{NO_EFFECT_MIN_DIMS} "
          f"restarts={black['restarts']} rss_peak_mb={black['rss_peak_mb']} "
          f"watchdog_killed={int(bool(black['watchdog_killed']))} log={_write_log(log_dir, black)}")
    print(f"{HEAD}_DETAIL artifact annotation_texts={black['artifact'].get('annotation_texts')} "
          f"marker_in_artifact={int(bool(black['artifact'].get('marker_in_artifact')))} "
          f"negative_marker_absent={int(bool(black['artifact'].get('negative_marker_absent')))} "
          f"{black['artifact'].get('detail') or ''}".rstrip())
    if not summary["mechanism_ok"]:
        print(f"{HEAD}_DETAIL mechanism ok=0 {mechanism.get('detail')}")
    for row in (rows if verbose else failures):
        print(f"{HEAD}_CASE {row['cid']} dim={row['dim']} channel={row['channel']} "
              f"violations={','.join(row['violations']) or '-'} "
              f"accepted={int(bool(row['accepted']))} status={row['status']} {row['detail']}".rstrip())
    print(f"{HEAD}_DETAIL cases={summary['cases']} violations={len(failures)} "
          f"dims={len({row['dim'] for row in rows})}")
    return rc


# --------------------------------------------------------------------------------------
# 尺子自证（变异体写死在文件里；只改**临时副本**，不碰真树）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mutant:
    """一个变异体：在临时副本上做一处**文本替换**，必须由 `judge` 那条判据抓住。"""

    name: str
    rel_path: str
    old: str
    new: str
    judge: str
    detail: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant("no_load_gate",
           "oss-alternatives/packages/cadcore/cadcore/docmodel.py",
           "    return sorted(set(problems))",
           "    return []  # mutant: 装载闸恒放行",
           "silent_illegal", "L1 装载闸恒放行 ⇒ 布尔 value_nm / 非 key 的 text_key 静默落库"),
    Mutant("out_of_range_accepted",
           "oss-alternatives/packages/cadcore/cadcore/sketch/constraints.py",
           "    return MIN_ABS_NM <= int(value) <= MAX_ABS_NM",
           "    return True  # mutant: 量程恒真",
           "structured", "量程判据恒真 ⇒ 2**53+1 的半径被接受（越界未被结构化拒绝）"),
    Mutant("no_rollback",
           "oss-alternatives/apps/opencad/server.py",
           "                self._restore(before)",
           "                pass  # mutant: 不回滚",
           "persisted", "`_mutate` 失败分支不回滚 ⇒ 半截状态留在真源里"),
    Mutant("bool_accepted",
           "oss-alternatives/packages/cadcore/cadcore/units.py",
           "    if isinstance(value, bool) or not isinstance(value, (int, float)):",
           "    if not isinstance(value, (int, float)):  # mutant: bool 当数",
           "silent_illegal", "`finite_number` 不再排除 bool ⇒ 布尔被当成数值静默接受"),
    Mutant("no_snap_kinds_validation",
           "oss-alternatives/apps/opencad/server.py",
           "        # `None` / 缺席 = 未指定 ⇒ 用持久化设置；其余一律过 `_query_kinds` 把关：\n"
           "        # 未知类型 / 裸字符串 / 非字符串项必须 400，**不许**静默过滤成空集合（G27 silent_illegal）。\n"
           "        raw_kinds = args.get(\"kinds\")\n"
           "        if raw_kinds is None:\n"
           "            wanted = self._precision_enabled_kinds() or list(osnap.SNAP_KINDS)\n"
           "        else:\n"
           "            wanted = self._query_kinds(raw_kinds)\n"
           "        base = args.get(\"base_nm\")\n"
           "        base_point = (int(base[0]), int(base[1])) if isinstance(base, Sequence) and len(base) == 2 else None\n"
           "        ref = args.get(\"ref\")\n"
           "        ignore = {str(kind) for kind in (args.get(\"ignore_kinds\") or [])}\n"
           "        priority = self._merged_priority()\n"
           "        index = {kind: position for position, kind in enumerate(priority)}\n"
           "        # 入参 `kinds` 是**允许集合**（与 osnap.snap 同口径）；真正的先后由 `priority` 决定，\n"
           "        # 这样\"优先级可设\"才是真的（osnap.snap 内部优先级是冻结的，见 `_precision_priority`）。\n"
           "        # 允许集合与优先级全序同源（都从 SNAP_KINDS + SNAP_EXTRA_KINDS 派生）：到这里还不在\n"
           "        # index 里 = 新类型只加了一边（产品自相矛盾）⇒ 显式炸，**不许**再静默丢弃请求里的类型。\n"
           "        unsortable = [kind for kind in wanted if kind not in index]\n"
           "        if unsortable:\n"
           "            raise ValueError(\"snap kinds missing from priority order: %r\" % (unsortable,))\n"
           "        wanted.sort(key=lambda kind: index[kind])",
           "        raw_kinds = args.get(\"kinds\")\n"
           "        if raw_kinds:\n"
           "            wanted = [str(kind) for kind in raw_kinds]  # mutant: 退回静默强转\n"
           "        else:\n"
           "            wanted = self._precision_enabled_kinds() or list(osnap.SNAP_KINDS)\n"
           "        base = args.get(\"base_nm\")\n"
           "        base_point = (int(base[0]), int(base[1])) if isinstance(base, Sequence) and len(base) == 2 else None\n"
           "        ref = args.get(\"ref\")\n"
           "        ignore = {str(kind) for kind in (args.get(\"ignore_kinds\") or [])}\n"
           "        priority = self._merged_priority()\n"
           "        index = {kind: position for position, kind in enumerate(priority)}\n"
           "        wanted = [kind for kind in wanted if kind in index]  # mutant: 静默过滤未知类型\n"
           "        wanted.sort(key=lambda kind: index[kind])",
           "silent_illegal",
           "snap_query.kinds 退回 str() 强转 + 静默成员过滤 ⇒ 未知类型/裸字符串/布尔项被 200 收下"),
    Mutant("leak_builtin_name",
           "oss-alternatives/apps/opencad/server.py",
           "            except (TypeError, ValueError) as exc:",
           "            except (UnicodeError, ZeroDivisionError) as exc:",
           "naked", "osnap 的内建 ValueError 直通响应 ⇒ detail 里带 `ValueError:`"),
    Mutant("no_annotation_text",
           "oss-alternatives/packages/cadcore/cadcore/io/dxf_bridge.py",
           "    comments = list(annotation_text_lines(annotations))",
           "    comments = []  # mutant: 标注文本不进产物",
           "artifact", "标注文本被丢出 DXF ⇒ 文本进了文档却不进产物（RC4 回归）"),
    # ── no_effect 腿（第 5 判据）的变异体：**报成功但什么都没做**，画/改/样式三类各一条 ──────────
    Mutant("no_effect_add_line",
           "oss-alternatives/packages/cadcore/cadcore/sketch/commands.py",
           '    start = _as_point(p1, what="p1")',
           '    return "00000000-0000-4000-8000-000000000000"  # mutant: 画类假成功（不落实体）\n'
           '    start = _as_point(p1, what="p1")',
           "no_effect", "`add_line` 回 ok 却不落实体 ⇒ 报成功但真源摘要不变（画类假成功）"),
    Mutant("no_effect_scale",
           "oss-alternatives/packages/cadcore/cadcore/edit/modify.py",
           "    matrix = _mat_about(_mat_scale(ratio), center)\n"
           "    for entity in entities:\n"
           "        _transform_entity(entity, matrix)\n"
           "    return [_uuid_of(entity) for entity in entities]",
           "    return [_uuid_of(entity) for entity in entities]  # mutant: 改类假成功（不改几何）",
           "no_effect", "`scale` 回 ok 却不改几何 ⇒ 报成功但真源摘要不变（改类假成功）"),
    Mutant("no_effect_standard",
           "oss-alternatives/apps/opencad/server.py",
           '        self.doc.standard_profile = str(standard)\n'
           '        self.doc.projection_angle = None if projection == "NA" else projection',
           "        pass  # mutant: 样式类假成功（不落文档）",
           "no_effect", "`/api/standard` 回 ok 却不落 standard_profile ⇒ 报成功但真源摘要不变（样式类假成功）"),
)

_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "build", ".git", "node_modules",
                                      "tests", "*.orig")

#: 复制哪几棵子树：`apps/opencad` + `cadcore` + **`packages/dxf`**。
#: `dxf` 是 `cadcore.io.dxf_bridge` 按 `<packages>/dxf` 解析的**同族依赖**，缺了它导出会报
#: `DependencyMissing`（实测：只复制前两棵 ⇒ artifact 腿假红）。`packages/vault-rs` 有 2.1 GB，
#: 与本门无关，一律不复制。
_COPY_RELS = ("apps/opencad", "packages/cadcore", "packages/dxf")


def _copy_tree(src: Path, dst: Path) -> None:
    for rel in _COPY_RELS:
        shutil.copytree(src / "oss-alternatives" / rel, dst / "oss-alternatives" / rel,
                        ignore=_COPY_IGNORE)


def _apply_mutant(root: Path, mutant: Mutant) -> None:
    path = root / mutant.rel_path
    text = path.read_text(encoding="utf-8")
    if text.count(mutant.old) != 1:
        raise RuntimeError(f"mutant {mutant.name}: anchor count={text.count(mutant.old)} (want 1)")
    path.write_text(text.replace(mutant.old, mutant.new), encoding="utf-8")


def _run_tree(script: Path, root: Path, timeout: float = 300.0) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, str(script), "--tree", str(root), "--json",
                           "--log-dir", ""],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                          errors="replace", timeout=timeout)
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError("no output rc=%s stderr=%s" % (proc.returncode, (proc.stderr or "")[-300:]))
    return json.loads(line[-1])


def _judge_hit(report: Mapping[str, Any], judge: str) -> tuple[bool, str]:
    """变异体是否被**指定**判据抓住（不是"随便红了一下"）。"""
    counts = report.get("counts") or {}
    if judge == "artifact":
        return (report.get("artifact_ok") is False), f"artifact_ok={report.get('artifact_ok')}"
    value = int(counts.get(judge) or 0)
    return value > 0, f"{judge}={value}"


def run_selftest(tree: Tree, *, verbose: bool = False) -> int:
    """干净对照必须 PASS；每个变异体必须被**对应**判据抓住（红）。"""
    if not tree.exists():
        print(f"{SELFTEST_HEAD} verdict=FAIL mutants={len(MUTANTS)} detected=0 baseline=NA "
              f"reasons=tree_incomplete rc={RC_NA}")
        return RC_NA
    script = Path(__file__).resolve()
    lines: list[str] = []
    detected = 0
    baseline_ok = False
    with tempfile.TemporaryDirectory(prefix="param-boundary-selftest-") as tmp:
        base = Path(tmp).resolve()
        # ① 干净对照：未打补丁的副本必须绿（否则"变异体被抓"证明不了尺子有分辨力）
        clean_root = base / "clean"
        _copy_tree(tree.root, clean_root)
        fixture_ok = (clean_root / "oss-alternatives" / "apps" / "opencad" / "server.py").is_file()
        try:
            clean = _run_tree(script, clean_root)
        except Exception as exc:  # noqa: BLE001
            print(f"{SELFTEST_HEAD} verdict=FAIL mutants={len(MUTANTS)} detected=0 baseline=NA "
                  f"reasons=baseline_unparsable:{type(exc).__name__} rc={RC_FAIL}")
            return RC_FAIL
        baseline_ok = fixture_ok and clean.get("verdict") == "PASS"
        lines.append(f"  baseline clean_copy fixture={int(fixture_ok)} verdict={clean.get('verdict')} "
                     f"cases={clean.get('cases')} counts={clean.get('counts')} "
                     f"controls={clean.get('controls_ok')}/{clean.get('controls_total')} "
                     f"artifact_ok={clean.get('artifact_ok')} mechanism_ok={clean.get('mechanism_ok')} "
                     f"line={clean.get('line')}")
        if not baseline_ok:
            lines.append(f"  baseline artifact={clean.get('artifact')}")
        if not baseline_ok:
            lines.append(f"  baseline failures={clean.get('failures')}")

        # ② 逐变异体
        for mutant in MUTANTS:
            root = base / mutant.name
            _copy_tree(tree.root, root)
            try:
                _apply_mutant(root, mutant)
            except Exception as exc:  # noqa: BLE001 - 锚点对不上 = 夹具失效，算未捕获（红）
                lines.append(f"  mutant {mutant.name}: anchor_error {type(exc).__name__}: {exc}")
                continue
            try:
                report = _run_tree(script, root)
            except Exception as exc:  # noqa: BLE001
                lines.append(f"  mutant {mutant.name}: unparsable output ({type(exc).__name__})")
                continue
            fired, evidence = _judge_hit(report, mutant.judge)
            detected += 1 if fired else 0
            lines.append(f"  mutant {mutant.name} ({mutant.detail}) -> verdict={report.get('verdict')} "
                         f"judge={mutant.judge} {evidence} "
                         f"{'CAUGHT' if fired else 'MISSED'}")
    ok = baseline_ok and detected == len(MUTANTS)
    print(f"{SELFTEST_HEAD} verdict={'PASS' if ok else 'FAIL'} mutants={len(MUTANTS)} "
          f"detected={detected} baseline={'PASS' if baseline_ok else 'FAIL'} rc={RC_PASS if ok else RC_FAIL}")
    for line in lines:
        print(line)
    return RC_PASS if ok else RC_FAIL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="G27 参数边界门：接受 or 结构化拒绝；禁裸异常 / 越界落库 / 静默接受非法类型")
    parser.add_argument("--tree", default="", help="产品树根（缺省 = 本脚本上溯三级）")
    parser.add_argument("--json", action="store_true", help="机读输出（单行 JSON）")
    parser.add_argument("--check", action="store_true",
                        help="检查模式（与裸跑同义；显式接口，便于照抄命令）")
    parser.add_argument("--selftest", action="store_true",
                        help="尺子自证：每个变异体各自被抓 + 干净对照绿（条数见判定行 mutants=N）")
    parser.add_argument("--log-dir", default=LOG_DIR_DEFAULT,
                        help="全量逐用例日志目录（缺省 3FS tmp；给空串即不落盘）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.tree).resolve() if args.tree else Path(__file__).resolve().parents[3]
    tree = Tree(root=root)
    if args.selftest:
        return run_selftest(tree, verbose=args.verbose)
    return run_check(tree, json_out=args.json, log_dir=args.log_dir, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
