#!/usr/bin/env python3
"""parity_output_gate.py —— 判定行 CAD_PARITY_OUTPUT：出图族（autocad-output）几何后果门。

一句话用途
----------
一条命令回答「本仓**出图族**（`cadcore/ops/` 下 FAMILY 别名集命中的全部模块）现在到底达标没有」：
**多模块加载**（`output.py` + `output_extra.py` + 任何声明本族别名的模块），合并它们的 `OPERATIONS`
作为**可探针集合**，构造一份 L1 草图/文档作靶子，按
`docs/research/cad-parity/features/autocad-output.json` 的 **must_have** 逐条跑族操作，
验**几何后果**（不是"调用没抛异常"），最后打一行机读判定行：

    CAD_PARITY_OUTPUT verdict=PASS|FAIL|NA ops_ok=… ops_total=… external_ops=… missing=… rc=…

三态 `rc`：`0=PASS` / `1=真不达标` / `2=未采（NA，不当绿）`。

口径（与全仓 E2 一致）
----------------------
1. **族内一个模块都没落盘 ⇒ `verdict=NA` `rc=2` `reasons=module_absent`**。
   没得测就是没得测，绝不当绿、也不当红。**多模块族的加载 = 扫描 + 别名命中**：
   `ops/*.py` 里 `FAMILY` 声明 ∈ `FAMILY_ALIASES`（`output` / `output_extra` / `autocad-output`）
   或文件名 stem ∈ 别名集的模块**全部**纳入（本波新增的 `output_extra.py` 由此进分母、进可探针集合）。
   单个模块 import 失败 ⇒ 记 `module_import_failed:*` 进 reasons，**不静默丢**；
   模块间操作重名 ⇒ 记 `op_name_collision:*`，先加载的（主模块）优先。
2. **分母 = 可探针集合（合并后的 OPERATIONS 数）**，`ops_ok` 只数**真被调用且后果验到**的操作；
   must_have（19 条）是**能力的判定分母**，单独报 `must_have_ok=`。**不许把没验到的算绿**：
   每个操作的结论有**四态**：`ok`（调用 ∧ 契约绿 ∧ 后果验到）/ `partial`（调用了但后果没验到）/
   `unprobed`（没跑到）/ `external`（**操作级**外部依赖诚实缺席：`dependency_missing` 一类，不进
   `ops_ok`，也**不**当门腿断的 `partial`），照实记、不当绿（判定行外的 `partial=`/`unprobed=`/
   `external_ops=` 可辨）。
3. **三态不许同形**：must_have 探针结论只有 `ok` / `missing`（族里找不到承担该能力的操作，或候选
   全部"诚实缺席"⇒ FAIL）/ `unverified`（操作跑了但后果读不到 ⇒ NA）。`0` 一律是**真为零**；
   读不到写 `-1`/`unverified`，不写 0。
4. **契约是冻结接口**：`FAMILY` 非空字符串、`OPERATIONS` 是非空 `dict[str, Callable]`、每个操作
   `op(sk_or_doc, **kwargs) -> list[str]`（uuid 列表）。返回非 list / 非 str / 非 uuid ⇒ FAIL（契约红）。
5. **几何后果，不是"没抛异常"**：每次调用前后各取一次 L1 载荷，要求 ①返回的 uuid 能在文档里解引用
   （或文档真的变了）②靶子草图的基础几何**逐字节不变**（出图操作不许吃掉模型几何）；
   再按该 must_have 声明的事实逐条读证，**并且**按操作级 `OP_PLANS` 深验产物本身：
   `style_table_create` 真落 `.ctb` 记录、`style_map_*` 真落映射条目、`export_svg_text` 的 SVG
   真含 `entities=4` 与来自映射表的 `stroke-width`、`export_pdf_text` 的 PDF 真以 `%PDF-1.4` 起 /
   `%%EOF` 止且 **xref 回读复验通过**、文件字节与记录 sha256 逐字节相符。
   `sheets_delta_ge:N`（调用前后 `document.sheets[]` 条数差）与 `sheet_name_has:NAME`（L1
   `sheets[]` 里有没有叫这个名字的布局）都是**载荷级**判据：`layout_manage` 的实参是
   `action="new"`，用动作动词（`rename|copy|delete|manage`）当判据在载荷里永远读不到、只会恒记
   unverified ⇒ 该条改判 `sheet_name_has`（`Sheet.uuid` 是 uuid5(内容)，同内容布局复用同一 L1
   条目，判条数增量会假红；模块内账不在 `sheets[]` 里，也冒充不了这个判据）。
6. **候选操作按「精确 → 前缀/后缀 → 包含」排序，逐个真跑**：族里同一能力可能有多个模块实现
   （`output.py` 的 `set_plot_style_table` 是**诚实缺席**的 stub，`output_extra.py` 的
   `style_table_create` 是真实现）；**诚实缺席让位，但不隐藏**（缺席操作在 detail 里点名）。
7. **实参由 `OP_PLANS` 给全**（`@table` / `@sheet` / `@tmp:` 占位符在调用时现解析）：注册层
   `set_page_setup` / `plot` / `preview` / `publish` / `manage_layout` 要求显式关键字实参，
   **"没给实参"不是族缺陷** ⇒ 必须先给合法实参再判后果，不许让 `TypeError` 冒充红票。
8. **`--selftest` 用进程内注入的假族模块做阴阳双证**：干净对照必须 PASS，变异体
   （非 uuid 返回 / 空转无后果 / 抛错 / 孤儿 uuid / 删除靶子几何 / 契约空 / 只跑不落后果）
   必须**全红**（verdict ≠ PASS）。变异体是**行为变体**，写死在文件里，不靠环境变量传。
9. **只写真件树之外的临时目录**：`OP_PLANS` 里带 `@tmp:` 的路径落在进程内 `tempfile.mkdtemp()`
   目录里，`evaluate` 返回前整目录删掉；本门**不写任何产物到真件树**（`--json-out` 由调用方显式
   指定路径，缺省不写）。不创建、不修改 `cadcore/ops/__init__.py`（缺包时只在内存里注册命名空间包）。

用法
----
    python3 oss-alternatives/tools/cad/parity_output_gate.py            # 裸跑 = --check
    python3 oss-alternatives/tools/cad/parity_output_gate.py --check
    python3 oss-alternatives/tools/cad/parity_output_gate.py --scope a8      # A8 点名面 7 操作 + 真 PDF 钉死
    python3 oss-alternatives/tools/cad/parity_output_gate.py --selftest
    python3 oss-alternatives/tools/cad/parity_output_gate.py --check --quiet

A8 真 PDF 判据（SCOPE §1）
--------------------------
`--check` 与 `--scope a8` 都会真跑 `publish(fmt="pdf")`（逐页）与 `publish(single_file=True)`
（多布局→单张多页）并**现读产物字节**打一行 `A8_PDF_PROBE`：`%PDF-` 魔数 ∧ `/Type /Page`
页对象数 == 布局数 ∧ `/Count` 字段一致 ∧ 逐页产物不得是 svg（`actual` 记录面同步核）。
判据失败 ⇒ 主判定行一并转红（不许被"族操作全绿"掩盖）；变异体（页数造假 / `/Count` 造假 /
svg 改名冒充 pdf）在 `--selftest` 里必须全红。

**边界（显式，不当绿）**：族里有两个 PDF 写出器 —— ① `cadcore/draw/sheet.py` 的**按布局分页**
写出器（`render_pdf_pages`/`export_pdf_pages`，`publish` 走它，页数 = 布局数）；②
`cadcore/ops/output_extra.py` 的 `export_pdf_text`（**model-space 单页**，`/Count` 恒 1，与布局数
无关）。A8 判据量的是 ①：产物 `%PDF-` ∧ 页数=布局数 ∧ `actual` 不得是 svg。② 的读数在 detail 行里
点名标为边界，不参与本判据，也不冒充布局页数。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import shutil
import sys
import tempfile
import types
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

GATE = "CAD_PARITY_OUTPUT"
SELFTEST_GATE = "CAD_PARITY_OUTPUT_SELFTEST"
FAMILY_ID = "autocad-output"
PRIMARY_MODULE = "cadcore.ops.output"
OPS_DIR_REL = "packages/cadcore/cadcore/ops"
FEATURES_REL = "docs/research/cad-parity/features/autocad-output.json"

#: 本族 FAMILY 别名集：本门主模块 + 本波新增的补全面 + 门里已有的族别名。
#: 命中规则 =（模块 `FAMILY` 声明 ∈ 别名集）∨（文件名 stem ∈ 别名集）。
FAMILY_ALIASES = ("output", "output_extra", FAMILY_ID)
FAMILY_DECL_RE = re.compile(r"""^\s*FAMILY\s*(?::[^=\n]+)?=\s*["']([^"']+)["']""", re.M)

#: OP_PLANS 占位符：进程内临时目录 / 本操作自建样式表名 / 文档里第一张图纸 uuid。
TMP_PREFIX = "@tmp:"
TABLE_TOKEN = "@table"
SHEET_TOKEN = "@sheet"
#: "诚实缺席"（本构建没有该依赖）的 CadError reason：不算缺陷，但必须让位给族内真实现。
ABSENT_REASONS = frozenset({"dependency_missing", "not_implemented", "unimplemented",
                            "unsupported", "not_supported"})
#: 靶子草图里的实体条数（深验导出产物必须真含这些几何）。
TARGET_ENTITY_COUNT = 4

#: 三态：真为零 / 未采 / 不适用（与全包口径一致，判定行里可辨）。
OK, UNMEASURED, NA = 0, -1, "NA"
#: 操作级状态：验到后果 / 调用了但后果没验到 / 根本没跑到。
ST_OK, ST_PARTIAL, ST_UNPROBED = "ok", "partial", "unprobed"

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_TABLE_RE = re.compile(r"[^a-z0-9.]")


# ======================================================================================
# 探针表：must_have（19 条）× 操作候选名 × 要验的后果事实
# ======================================================================================


@dataclass(frozen=True)
class Probe:
    """一条 must_have 的几何后果探针。

    `match`：用来在 must_have 的 name+alias 里认领这一条（大小写不敏感，命中即认领）。
    `ops`：族模块 `OPERATIONS` 里可能承担该能力的操作名候选（归一化后按精确 → 前缀/后缀 → 包含排序，
           候选**全部**真跑，取第一个验到后果的）。
    `wants`：要读到的后果事实，见 `_verify`；读不到 = unverified（NA），被反证 = FAIL。
    """

    key: str
    match: tuple[str, ...]
    ops: tuple[str, ...]
    wants: tuple[str, ...]
    scope: str = "doc"  # doc | sketch


PROBES: tuple[Probe, ...] = (
    Probe("layout", ("LAYOUT", "Paper Space", "布局选项", "TILEMODE"),
          ("layout_create", "create_layout", "add_layout", "layout"),
          ("sheets_ge:1", "text_has:layout")),
    Probe("viewport_create", ("VPORTS", "MVIEW", "-VPORTS", "视口边框"),
          ("viewport_create", "create_viewport", "add_viewport", "vport_create", "add_view", "mview"),
          ("views_ge:1", "text_has:viewport")),
    Probe("viewport_scale", ("标准比例", "Display Locked", "视口锁定", "视口比例"),
          ("viewport_scale", "set_viewport_scale", "viewport_lock", "lock_viewport", "vport_scale"),
          ("key_has:scale", "keys_ge:lock:1")),
    Probe("vp_override", ("VPLAYER", "视口冻结", "视口替代", "LAYERSTATE"),
          ("layer_override", "viewport_freeze", "vp_freeze", "vplayer", "set_layer_override"),
          ("key_has:freeze", "key_has:override")),
    Probe("page_setup", ("PAGESETUP", "页面设置"),
          ("page_setup", "pagesetup", "set_page_setup", "apply_page_setup"),
          ("key_has:paper", "key_has:setup")),
    Probe("plot", ("PLOT、Ctrl+P", "打印对话框", "-PLOT"),
          ("plot", "plot_run", "do_plot", "plot_document"),
          ("key_has:plot", "text_has:plot")),
    Probe("plot_pdf", ("DWG To PDF", "打印到 PDF", "输出为 PDF"),
          ("plot_pdf", "export_pdf", "to_pdf", "pdf_export", "print_pdf"),
          ("text_has:pdf",)),
    Probe("plotter_config", ("PLOTTERMANAGER", ".pc3", "绘图仪配置"),
          ("plotter_config", "configure_plotter", "plotter", "pc3", "pc3_config"),
          ("text_has:pc3", "any_of:plotter|device")),
    Probe("plot_style_table", ("STYLESMANAGER", ".ctb", ".stb", "打印样式"),
          ("plot_style_table", "plot_style", "style_table", "ctb", "stb"),
          ("any_of:ctb|stb",)),
    Probe("lineweight", ("LWEIGHT", "线宽"),
          ("lineweight", "lweight", "set_lineweight", "set_lweight"),
          ("any_of:lineweight|lweight",)),
    Probe("plot_area", ("打印区域",),
          ("plot_area", "set_plot_area", "plot_window", "set_plot_extents"),
          ("key_has:area", "any_of:window|extents|limits|viewport")),
    Probe("plot_scale", ("打印比例", "布满图纸", "Fit to paper"),
          ("plot_scale", "set_plot_scale", "fit_to_paper"),
          ("any_of:scale|fit",)),
    Probe("preview", ("PREVIEW", "打印预览"),
          ("preview", "plot_preview", "preview_plot"),
          ("text_has:preview",)),
    Probe("plot_flag", ("Plot / No Plot", "打印列", "打印=否"),
          ("plot_flag", "set_plottable", "no_plot", "plottable", "set_plot_flag"),
          ("any_of:plottable|no_plot|plot_flag|print_flag",)),
    Probe("model_space_plot", ("模型选项", "打印模型空间", "模型空间"),
          ("model_space_plot", "plot_model_space", "modelspace_plot", "plot_model"),
          ("any_of:model_space|modelspace|model", "key_has:plot")),
    # ★ `layout_manage` 判据（2026-09-30 修）：原判据是 `any_of:rename|copy|delete|manage` ——
    #   那是**动作动词**，而本探针的实参是 `action="new"`，L1 载荷里永远读不到这四个词 ⇒
    #   判据不可满足、只能恒记 unverified（这不是"没跑"，是尺子读错了地方）。
    #   改判 **L1 `sheets[]` 里有没有这次新建的布局名**（`sheet_name_has`）：布局管理真把
    #   布局写进 `document.sheets[]` 才可能绿；模块内账（`_LAYOUTS`/`_output_layouts`）不在
    #   `sheets[]` 里，冒充不了。**不判条数增量**：`Sheet.uuid` 是 uuid5(内容) ⇒ 同内容布局
    #   复用同一 L1 条目，条数差恒为 0（判增量会假红）。
    #   配合下面 `manage_layout` 的"改名腾名再新建"计划：**改名若不真改账，主调用会撞
    #   duplicate_name 直接红**（新建 / 改名两条路都进了判据，见 OP_PLANS）。
    Probe("layout_manage", ("New/Copy/Rename/Delete", "布局选项卡右键", "布局管理"),
          ("layout_manage", "manage_layout", "layout_copy", "layout_rename", "layout_delete"),
          ("sheet_name_has:gateseed",)),
    Probe("publish", ("PUBLISH", "发布"),
          ("publish", "batch_plot", "publish_layouts", "publish_sheets"),
          ("text_has:publish", "any_of:pdf|sheet|layout")),
    Probe("dwg_io", ("DWG 读写", "SAVEAS", "DWG 2018"),
          ("dwg_save", "save_dwg", "dwg_read", "read_dwg", "dwg_export", "dwg"),
          ("text_has:dwg", "any_of:version|acadver|r2018|2018")),
    Probe("dxf_io", ("DXFOUT", "DXFIN", "DXF 导入导出", "SAVEAS 选 DXF"),
          ("dxf_export", "dxf_import", "dxf_out", "dxf_in", "export_dxf", "import_dxf", "dxf"),
          ("text_has:dxf",)),
)


# ======================================================================================
# 操作级探针计划：族内工作流（前置）+ 调用实参 + 产物深验
# ======================================================================================


@dataclass(frozen=True)
class Plan:
    """一个操作的探针计划。

    `setup`：主调用前要跑的族内前置（操作名, kwargs），**幂等**（`duplicate_table` 视为已就绪）。
    `kwargs`：主调用实参（`@table`/`@sheet`/`@tmp:` 占位符在调用前现解析）。
    `deep`：产物深验种类（见 `_deep_check`）；空串 = 不做深验。
    """

    setup: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    deep: str = ""


_CREATE_TABLE: tuple[tuple[str, Mapping[str, Any]], ...] = (
    ("style_table_create", {"name": TABLE_TOKEN}),)
_SET_MAP: tuple[tuple[str, Mapping[str, Any]], ...] = _CREATE_TABLE + (
    ("style_map_set", {"table": TABLE_TOKEN, "color": "by_layer", "lineweight_mm": 0.5,
                       "linetype": "continuous", "screening": 100, "plot": True}),)
_EXPORTS: tuple[tuple[str, Mapping[str, Any]], ...] = _SET_MAP + (
    ("export_svg_text", {"style_table": TABLE_TOKEN, "out_path": TMP_PREFIX + "probe.svg",
                         "title": "PARITY"}),
    ("export_pdf_text", {"style_table": TABLE_TOKEN, "out_path": TMP_PREFIX + "probe.pdf",
                         "title": "PARITY"}),)

#: 操作名 -> 计划。缺省计划 = 裸调 `op(doc)`。
OP_PLANS: dict[str, Plan] = {
    # --- output_extra：样式映射表 + SVG/PDF 文本导出（前置 = 族内工作流；深验 = 产物字节）---
    "style_table_create": Plan(kwargs={"name": TABLE_TOKEN}, deep="style_table"),
    "style_table_list": Plan(setup=_CREATE_TABLE, deep="style_list"),
    "style_map_set": Plan(setup=_CREATE_TABLE,
                          kwargs={"table": TABLE_TOKEN, "color": "by_layer", "lineweight_mm": 0.5},
                          deep="style_map"),
    "style_map_bulk": Plan(setup=_CREATE_TABLE,
                           kwargs={"table": TABLE_TOKEN,
                                   "mappings": [{"color": "by_layer", "lineweight_mm": 0.5}]},
                           deep="style_map"),
    "style_map_lookup": Plan(setup=_SET_MAP,
                             kwargs={"table": TABLE_TOKEN, "color": "by_layer"},
                             deep="style_lookup"),
    "style_map_delete": Plan(setup=_SET_MAP,
                             kwargs={"table": TABLE_TOKEN, "color": "by_layer"},
                             deep="style_delete"),
    "style_map_apply": Plan(setup=_SET_MAP, kwargs={"table": TABLE_TOKEN}, deep="style_apply"),
    "export_svg_text": Plan(setup=_SET_MAP,
                            kwargs={"style_table": TABLE_TOKEN, "title": "PARITY",
                                    "out_path": TMP_PREFIX + "probe.svg"}, deep="export_svg"),
    "export_pdf_text": Plan(setup=_SET_MAP,
                            kwargs={"style_table": TABLE_TOKEN, "title": "PARITY",
                                    "out_path": TMP_PREFIX + "probe.pdf"}, deep="export_pdf"),
    "export_manifest": Plan(setup=_EXPORTS, deep="manifest"),
    # --- output 注册层：这些操作要求显式关键字实参，必须给全再判后果 ---
    "set_page_setup": Plan(kwargs={"sheet_uuid": SHEET_TOKEN, "size": "A4", "scale": "1:1"}),
    "plot": Plan(kwargs={"sheet_uuid": SHEET_TOKEN, "fmt": "svg",
                         "out": TMP_PREFIX + "plot.svg"}),
    # 打印到 PDF 的真 PDF 半边（内建写出器）：计划与 `plot` 同形，但 fmt 固定 pdf。
    "plot_pdf": Plan(kwargs={"sheet_uuid": SHEET_TOKEN, "out": TMP_PREFIX + "plot.pdf"}),
    "preview": Plan(kwargs={"sheet_uuid": SHEET_TOKEN, "out": TMP_PREFIX + "preview.svg"}),
    "publish": Plan(kwargs={"sheet_uuids": [SHEET_TOKEN], "fmt": "svg",
                            "out_dir": TMP_PREFIX + "publish"}),
    # `manage_layout` 走**生命周期**计划：先建 GateSeed，再改名把该名腾出来，最后用同名新建。
    #   命名冲突语义是"名字在账上就拒"⇒ 改名没真改账 ⇒ 主调用 `duplicate_name`（红）；
    #   新建没落 L1 `sheets[]` ⇒ 判据 `sheet_name_has:gateseed` 读不到（unverified）。
    "manage_layout": Plan(setup=(("manage_layout", {"action": "new", "name": "GateSeed"}),
                                 ("manage_layout", {"action": "rename", "name": "GateSeed",
                                                    "new_name": "GateRenamed"})),
                          kwargs={"action": "new", "name": "GateSeed"}),
}


def _norm(name: str) -> str:
    return _TOKEN_RE.sub("", str(name).lower())


def _probe_table_name(op_name: str) -> str:
    """每个操作自建一张 `.ctb` 表（名字按操作名确定性派生）⇒ 多次探针不撞 `duplicate_table`。"""
    stem = _TABLE_RE.sub("", _norm(op_name))[:32] or "op"
    return "probe-%s.ctb" % stem


def _match_ops(operations: Mapping[str, Any], candidates: Sequence[str]) -> list[tuple[str, Any]]:
    """在合并后的 OPERATIONS 里找承担该能力的操作，按「精确 → 前缀/后缀 → 包含」排序返回**全部**命中。

    返回列表而不是单个：族里同一能力可能被多个模块实现（stub 与真实现并存），调用方逐个真跑。
    """
    norm_keys = {_norm(k): k for k in operations}
    out: list[tuple[str, Any]] = []
    seen: set[str] = set()

    def add(orig: str) -> None:
        if orig not in seen:
            seen.add(orig)
            out.append((orig, operations[orig]))

    for stage in ("exact", "edge", "contains"):
        for cand in candidates:
            c = _norm(cand)
            if not c:
                continue
            for nk, orig in norm_keys.items():
                if stage == "exact" and nk == c:
                    add(orig)
                elif stage == "edge" and (nk.startswith(c) or nk.endswith(c)):
                    add(orig)
                elif stage == "contains" and c in nk:
                    add(orig)
    return out


# ======================================================================================
# 靶子世界：L1 草图 + 文档（整数纳米）
# ======================================================================================


@dataclass
class Ctx:
    """靶子世界：一份 L1 文档 + 一个含闭合矩形的草图；`tmp` = 进程内临时目录（可空）。"""

    doc: Any
    sketch: dict[str, Any]
    base_entity_uuids: list[str] = field(default_factory=list)
    built_with: str = "fallback-dict"
    tmp: Path | None = None
    #: 开局基线几何指纹：靶子构造时定死，之后**任何**操作都不许改它
    #: （跨调用锚定 ⇒ 防"先破坏几何、后续调用就看不见了"）。
    base_ents: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_ents = _entity_fingerprint(self.sketch)

    def payload(self) -> dict[str, Any]:
        if hasattr(self.doc, "to_payload"):
            return self.doc.to_payload()
        return self.doc


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def build_ctx() -> Ctx:
    """构造 L1 靶子：草图 = 4 条 line 组成的闭合矩形（整数纳米），文档 = 新建 L1 文档。"""
    sketch: dict[str, Any] = {"uuid": _new_uuid(), "plane": "XY", "entities": [],
                              "constraints": [], "dimensions": [], "solved": {}}
    corners = [(0, 0), (60_000_000, 0), (60_000_000, 40_000_000), (0, 40_000_000)]
    uuids: list[str] = []
    for i in range(4):
        x1, y1 = corners[i]
        x2, y2 = corners[(i + 1) % 4]
        ent = {"uuid": _new_uuid(), "kind": "line", "layer": "0", "construction": False,
               "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}
        sketch["entities"].append(ent)
        uuids.append(ent["uuid"])
    try:
        _pkg = _ensure_cadcore()
        docmodel = importlib.import_module("cadcore.docmodel")
        doc = docmodel.new_document()
        doc.sketches.append(sketch)
        return Ctx(doc=doc, sketch=sketch, base_entity_uuids=uuids, built_with="cadcore.docmodel")
    except Exception:  # 靶子构造降级也要可见（built_with 记进证据）
        doc = {"schema_version": 1, "unit": "mm", "standard_profile": "iso", "projection_angle": None,
               "sketches": [sketch], "features": [], "annotations": [], "sheets": []}
        return Ctx(doc=doc, sketch=sketch, base_entity_uuids=uuids, built_with="fallback-dict")


def _oss_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_cadcore() -> str:
    """把 `packages/cadcore` 挂进 sys.path（可复用件与族模块都从这里 import）。"""
    root = str(_oss_root() / "packages" / "cadcore")
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _ensure_ops_namespace(ops_dir: Path) -> None:
    """`cadcore.ops` 还不可 import 时，在内存里注册命名空间包（**不落盘**）。"""
    try:
        importlib.import_module("cadcore.ops")
        return
    except Exception:
        pass
    pkg_name = PRIMARY_MODULE.rsplit(".", 1)[0]
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ops_dir)]  # type: ignore[attr-defined]
    pkg.__package__ = pkg_name
    sys.modules.setdefault(pkg_name, pkg)


# ======================================================================================
# 族模块加载（多模块：扫描 + FAMILY 别名命中）
# ======================================================================================


@dataclass
class FamilyModules:
    modules: list[Any] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    reason: str = ""


def _scan_family_files(oss_root: Path) -> list[Path]:
    """扫 `ops/*.py`，返回 FAMILY 别名命中的文件；主模块 `output.py` 排第一。"""
    ops_dir = oss_root / OPS_DIR_REL
    if not ops_dir.is_dir():
        return []
    hits: list[Path] = []
    for path in sorted(ops_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        found = FAMILY_DECL_RE.search(text)
        family = found.group(1) if found else ""
        if family in FAMILY_ALIASES or path.stem in FAMILY_ALIASES:
            hits.append(path)
    hits.sort(key=lambda p: (p.stem != PRIMARY_MODULE.rsplit(".", 1)[-1], p.name))
    return hits


def _load_family_modules(oss_root: Path, injected: Any = None) -> FamilyModules:
    """加载族模块：`injected` 优先（selftest 的进程内假模块）；否则扫描 + import。"""
    if injected is not None:
        mods = list(injected) if isinstance(injected, (list, tuple)) else [injected]
        return FamilyModules(modules=mods,
                             names=[getattr(m, "__name__", "injected") for m in mods])
    files = _scan_family_files(oss_root)
    if not files:
        return FamilyModules(reason="module_absent")
    _ensure_cadcore()
    _ensure_ops_namespace(files[0].parent)
    fam = FamilyModules()
    for path in files:
        name = "%s.%s" % (PRIMARY_MODULE.rsplit(".", 1)[0], path.stem)
        try:
            fam.modules.append(importlib.import_module(name))
            fam.names.append(name)
        except Exception as exc:
            fam.problems.append("module_import_failed:%s:%s" % (path.name, type(exc).__name__))
    if not fam.modules:
        fam.reason = "module_import_failed_all"
    return fam


def _contract_problems(modules: Sequence[Any]) -> list[str]:
    """逐模块校验冻结契约；返回带模块名的红票（空 = 全绿）。"""
    problems: list[str] = []
    for module in modules:
        where = getattr(module, "__name__", "?")
        family = getattr(module, "FAMILY", None)
        if not isinstance(family, str) or not family.strip():
            problems.append("bad_contract:FAMILY:%s" % where)
        ops = getattr(module, "OPERATIONS", None)
        if not isinstance(ops, Mapping) or not ops:
            problems.append("bad_contract:OPERATIONS:%s" % where)
            continue
        for name, fn in ops.items():
            if not isinstance(name, str) or not callable(fn):
                problems.append("bad_contract:op:%s:%s" % (where, name))
    return problems


def _merge_operations(modules: Sequence[Any]) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """合并各模块 OPERATIONS（主模块优先）；重名冲突**不静默**，记进 problems。"""
    merged: dict[str, Any] = {}
    problems: list[str] = []
    origin: dict[str, str] = {}
    for module in modules:
        where = getattr(module, "__name__", "?")
        for name, fn in dict(getattr(module, "OPERATIONS", {})).items():
            if name in merged:
                if merged[name] is fn:
                    continue
                problems.append("op_name_collision:%s:%s" % (name, where))
                continue
            merged[name] = fn
            origin[name] = where
    return merged, problems, origin


# ======================================================================================
# 载荷读取（三态）
# ======================================================================================


def _walk(node: Any):
    yield node
    if isinstance(node, Mapping):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v)


def _uuid_set(payload: Any) -> set[str]:
    out: set[str] = set()
    for node in _walk(payload):
        if isinstance(node, Mapping):
            for key, val in node.items():
                if str(key).lower().endswith("uuid") and isinstance(val, str):
                    out.add(val)
    return out


def _payload_text(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).lower()


def _keys(payload: Any) -> list[str]:
    return [str(k).lower() for node in _walk(payload) if isinstance(node, Mapping) for k in node.keys()]


def _count_key(payload: Any, token: str) -> int:
    token = token.lower()
    return sum(1 for k in _keys(payload) if token in k)


def _entity_fingerprint(sketch: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ent in sketch.get("entities") or []:
        if isinstance(ent, Mapping):
            out[str(ent.get("uuid"))] = json.dumps(ent.get("geom"), sort_keys=True)
    return out


def _sheets(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        val = payload.get("sheets")
        return list(val) if isinstance(val, list) else []
    return []


def _views(payload: Any) -> list[Any]:
    out: list[Any] = []
    for sheet in _sheets(payload):
        if isinstance(sheet, Mapping):
            val = sheet.get("views")
            if isinstance(val, list):
                out.extend(val)
    return out


def _first_sheet_uuid(ctx: Ctx) -> str:
    for sheet in _sheets(ctx.payload()):
        if isinstance(sheet, Mapping) and sheet.get("uuid"):
            return str(sheet["uuid"])
    return ""


#: 三态结论：True=验到 / False=被反证 / None=读不到
def _verify(wants: Sequence[str], before: Mapping[str, Any], after: Mapping[str, Any],
            before_ents: Mapping[str, str], after_ents: Mapping[str, str],
            result: Sequence[str]) -> tuple[str, list[str], list[str]]:
    """返回 (status, evidence, unmet)；status ∈ {"ok","falsified","unverified"}。"""
    evidence: list[str] = []
    unmet: list[str] = []

    if after_ents != before_ents:
        lost = sorted(set(before_ents) - set(after_ents))
        return "falsified", [f"target_geometry_lost={len(lost)}"], []

    before_sheets, after_sheets = len(_sheets(before)), len(_sheets(after))
    before_views, after_views = len(_views(before)), len(_views(after))
    if after_sheets < before_sheets or after_views < before_views:
        return "falsified", [f"sheets={before_sheets}->{after_sheets}", f"views={before_views}->{after_views}"], []

    text = _payload_text(after)
    changed = _payload_text(before) != text
    if not result and not changed:
        return "falsified", ["no_consequence:doc_unchanged_and_empty_result"], []

    for want in wants:
        kind, _, arg = want.partition(":")
        if kind == "sheets_ge":
            n = int(arg)
            if after_sheets >= n:
                evidence.append(f"sheets={after_sheets}")
            else:
                unmet.append(f"sheets_ge:{n}(got {after_sheets})")
        elif kind == "sheets_delta_ge":
            # 增量判据：靶子文档可能已被前面的探针建过布局，绝对条数会假绿 ⇒ 必须判**这次调用的差**。
            n = int(arg)
            delta = after_sheets - before_sheets
            if delta >= n:
                evidence.append(f"sheets_delta={delta}")
            else:
                unmet.append(f"sheets_delta_ge:{n}(got {delta})")
        elif kind == "sheet_name_has":
            # 只读 **L1 载荷** `payload["sheets"][*]["name"]`：模块内账（`_LAYOUTS` /
            # `doc.extra["_output_layouts"]`）不在 `sheets[]` 里 ⇒ 本判据不可能被账本冒充。
            names = [str(item.get("name")) for item in _sheets(after) if isinstance(item, Mapping)]
            if any(arg.lower() == item.lower() for item in names):
                evidence.append(f"sheet_name:{arg}")
            else:
                unmet.append(f"sheet_name_has:{arg}(sheets={len(names)})")
        elif kind == "views_ge":
            n = int(arg)
            if after_views >= n:
                evidence.append(f"views={after_views}")
            else:
                unmet.append(f"views_ge:{n}(got {after_views})")
        elif kind == "text_has":
            if arg.lower() in text:
                evidence.append(f"text:{arg}")
            else:
                unmet.append(f"text_has:{arg}")
        elif kind == "key_has":
            if _count_key(after, arg) > 0:
                evidence.append(f"key:{arg}")
            else:
                unmet.append(f"key_has:{arg}")
        elif kind == "keys_ge":
            tok, _, num = arg.partition(":")
            got = _count_key(after, tok)
            if got >= int(num):
                evidence.append(f"keys:{tok}={got}")
            else:
                unmet.append(f"keys_ge:{tok}:{num}(got {got})")
        elif kind == "any_of":
            toks = arg.split("|")
            hit = next((t for t in toks if t.lower() in text or _count_key(after, t) > 0), None)
            if hit:
                evidence.append(f"any_of:{hit}")
            else:
                unmet.append(f"any_of:{arg}")
        else:  # pragma: no cover - 探针表写错时不许静默
            unmet.append(f"unknown_want:{want}")

    if unmet:
        return "unverified", evidence, unmet
    return "ok", evidence, []


# ======================================================================================
# 操作级深验：产物必须真与 L1 几何/映射相关（读不到 ⇒ 不 ok）
# ======================================================================================


def _records(payload: Any, kind: str) -> list[Mapping[str, Any]]:
    return [n for n in _walk(payload) if isinstance(n, Mapping) and n.get("kind") == kind]


def _mapping_entries(payload: Any) -> list[Mapping[str, Any]]:
    return [n for n in _walk(payload) if isinstance(n, Mapping)
            and n.get("kind") == "plot style mapping" and "key" in n]


def _style_tables(payload: Any) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for node in _walk(payload):
        if not isinstance(node, Mapping):
            continue
        name = str(node.get("name") or "")
        if str(node.get("format") or "") in ("ctb", "stb") and name.lower().endswith((".ctb", ".stb")):
            out.append(node)
    return out


def _deep_check(kind: str, after: Any, result: Sequence[str], kwargs: Mapping[str, Any],
                table: str) -> tuple[bool, list[str]]:
    """返回 (ok, evidence)；ok=False ⇒ 该次调用记 falsified（尺子看见红）。"""
    if kind == "style_table":
        hit = [n for n in _style_tables(after) if n.get("name") == table]
        if not hit:
            return False, ["deep:style_table_record_absent"]
        if not result or str(hit[0].get("uuid")) != str(result[0]):
            return False, ["deep:style_table_uuid_mismatch"]
        return True, ["deep:table=%s" % table, "deep:format=%s" % hit[0].get("format")]
    if kind == "style_map":
        hit = [m for m in _mapping_entries(after) if str(m.get("uuid")) == str(result[0])] if result else []
        if len(hit) != 1 or hit[0].get("key") != "by_layer:by_layer":
            return False, ["deep:mapping_entry_not_found_for_returned_uuid"]
        return True, ["deep:key=by_layer:by_layer", "deep:lw=%s" % hit[0].get("lineweight_mm")]
    if kind == "style_list":
        tables = _style_tables(after)
        known = {str(n.get("uuid")) for n in tables}
        if not result or not set(str(u) for u in result) <= known:
            return False, ["deep:listed_uuid_not_a_style_table"]
        if str(_style_tables(after)[0].get("uuid")) and table not in {str(n.get("name")) for n in tables}:
            return False, ["deep:own_table_not_listed"]
        return True, ["deep:listed=%d" % len(result)]
    if kind == "style_lookup":
        hit = [m for m in _mapping_entries(after) if str(m.get("uuid")) == str(result[0])] if result else []
        if len(hit) != 1 or hit[0].get("key") != "by_layer:by_layer":
            return False, ["deep:lookup_not_the_mapping_entry"]
        return True, ["deep:lookup_resolved=by_layer:by_layer"]
    if kind == "style_delete":
        left = {str(m.get("uuid")) for m in _mapping_entries(after)}
        if not result or str(result[0]) in left:
            return False, ["deep:entry_still_present"]
        return True, ["deep:entry_removed", "deep:mappings_left=%d" % len(left)]
    if kind == "style_apply":
        eff = [r for r in _records(after, "effective plot style")
               if int(r.get("entity_count", 0) or 0) == TARGET_ENTITY_COUNT]
        if not eff:
            return False, ["deep:no_effective_style_covering_target_geometry"]
        return True, ["deep:entity_count=%d" % TARGET_ENTITY_COUNT]
    if kind in ("export_svg", "export_pdf"):
        want_kind = "svg export" if kind == "export_svg" else "pdf export"
        recs = {str(r.get("uuid")): r for r in _records(after, want_kind)}
        if not result or str(result[0]) not in recs:
            return False, ["deep:%s_record_absent" % want_kind]
        rec = recs[str(result[0])]
        path = str(kwargs.get("out_path") or "")
        if not path or not rec.get("written"):
            return False, ["deep:artifact_not_written"]
        try:
            blob = Path(path).read_bytes()
        except Exception as exc:
            return False, ["deep:artifact_unreadable:%s" % type(exc).__name__]
        if len(blob) != int(rec.get("bytes", -1)):
            return False, ["deep:bytes_mismatch:%d!=%s" % (len(blob), rec.get("bytes"))]
        if hashlib.sha256(blob).hexdigest() != str(rec.get("sha256")):
            return False, ["deep:sha256_mismatch"]
        text = blob.decode("latin-1")
        if kind == "export_svg":
            must = ["<?xml", "<svg", "entities=%d" % TARGET_ENTITY_COUNT, 'stroke-width="0.5"']
            missing = [tok for tok in must if tok not in text]
            if missing:
                return False, ["deep:missing_tokens=%s" % ",".join(missing)]
        else:
            must = ["%PDF-1.4", "%%EOF", "/MediaBox [0 0 841.8898 595.2756]", "1.4173 w"]
            if not rec.get("xref_verified"):
                return False, ["deep:xref_not_verified"]
            missing = [tok for tok in must if tok not in text]
            if missing:
                return False, ["deep:missing_tokens=%s" % ",".join(missing)]
        return True, ["deep:bytes=%d" % len(blob), "deep:sha256_ok",
                      "deep:geometry=%d_entities" % TARGET_ENTITY_COUNT]
    if kind == "manifest":
        exports = _records(after, "svg export") + _records(after, "pdf export")
        known = {str(r.get("uuid")) for r in exports}
        if len(exports) < 2 or set(str(u) for u in result) != known:
            return False, ["deep:manifest_mismatch:%d_vs_%d" % (len(result), len(exports))]
        return True, ["deep:manifest=%d" % len(result)]
    return True, []


# ======================================================================================
# 一次调用 = 前置 + 调用 + 契约 + 后果 + 深验
# ======================================================================================


@dataclass
class Attempt:
    status: str                      # ok | falsified | unverified | absent
    evidence: list[str] = field(default_factory=list)
    detail: str = ""
    called: list[str] = field(default_factory=list)


_RANK = {"ok": 0, "falsified": 1, "unverified": 2, "absent": 3}


def _kwargs_need_sheet(kwargs: Mapping[str, Any]) -> bool:
    for value in kwargs.values():
        if value == SHEET_TOKEN:
            return True
        if isinstance(value, (list, tuple)) and SHEET_TOKEN in value:
            return True
    return False


def _needs_sheet(plan: Plan) -> bool:
    if _kwargs_need_sheet(plan.kwargs):
        return True
    return any(_kwargs_need_sheet(kw) for _name, kw in plan.setup)


def _resolve_value(value: Any, tmp: Path | None, table: str, sheet: str) -> Any:
    if isinstance(value, str):
        if value.startswith(TMP_PREFIX):
            return "" if tmp is None else str(tmp / value[len(TMP_PREFIX):])
        if value == TABLE_TOKEN:
            return table
        if value == SHEET_TOKEN:
            return sheet
        return value
    if isinstance(value, (list, tuple)):
        return [_resolve_value(item, tmp, table, sheet) for item in value]
    return value


def _resolve_kwargs(kwargs: Mapping[str, Any], tmp: Path | None, table: str = "",
                    sheet: str = "") -> dict[str, Any]:
    return {key: _resolve_value(value, tmp, table, sheet) for key, value in kwargs.items()}


def _run_setup(ctx: Ctx, plan: Plan, operations: Mapping[str, Any], tmp: Path | None,
               table: str, sheet: str) -> tuple[list[str], list[str]]:
    """跑族内前置；`duplicate_table` 视为已就绪（幂等），其余失败照实记。"""
    called: list[str] = []
    notes: list[str] = []
    for name, kwargs in plan.setup:
        fn = operations.get(name)
        if fn is None:
            notes.append("setup_missing:%s" % name)
            return called, notes
        called.append(name)
        try:
            fn(ctx.doc, **_resolve_kwargs(kwargs, tmp, table, sheet))
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or type(exc).__name__)
            if reason == "duplicate_table":
                notes.append("setup_exists:%s" % name)
                continue
            notes.append("setup_failed:%s:%s" % (name, reason))
    return called, notes


def _attempt(op_name: str, op: Callable[..., Any], ctx: Ctx, *, wants: Sequence[str],
             plan: Plan | None, scope: str, operations: Mapping[str, Any]) -> Attempt:
    plan = plan or Plan()
    table = _probe_table_name(op_name)
    called: list[str] = []
    notes: list[str] = []
    if _needs_sheet(plan) and not _first_sheet_uuid(ctx):
        creator = operations.get("create_layout")  # 前置：先有一张图纸才能调这些注册层操作
        if creator is not None:
            called.append("create_layout")
            try:
                creator(ctx.doc)
            except Exception as exc:
                notes.append("setup_failed:create_layout:%s"
                             % (getattr(exc, "reason", "") or type(exc).__name__))
    sheet = _first_sheet_uuid(ctx)
    setup_called, setup_notes = _run_setup(ctx, plan, operations, ctx.tmp, table, sheet)
    called += setup_called
    notes += setup_notes
    kwargs = _resolve_kwargs(plan.kwargs, ctx.tmp, table, sheet)
    target = ctx.sketch if scope == "sketch" else ctx.doc
    before, before_ents = ctx.payload(), _entity_fingerprint(ctx.sketch)
    note_text = (" setup=" + ",".join(notes)) if notes else ""
    try:
        result = op(target, **kwargs)
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        status = "absent" if reason in ABSENT_REASONS else "falsified"
        return Attempt(status, [f"cad_error:{reason}"],
                       f"op={op_name} CadError reason={reason}{note_text}", called)
    after, after_ents = ctx.payload(), _entity_fingerprint(ctx.sketch)

    if not isinstance(result, list) or not all(isinstance(u, str) for u in result):
        return Attempt("falsified", [f"result_not_uuid_list:{type(result).__name__}"],
                       f"op={op_name} result={type(result).__name__}{note_text}", called)
    bad = [u for u in result if not UUID_RE.match(u)]
    known = _uuid_set(after)
    orphan = [u for u in result if u not in known]
    if bad or (orphan and _payload_text(before) == _payload_text(after)):
        return Attempt("falsified", [f"bad_uuid={len(bad)}", f"orphan={len(orphan)}"],
                       f"op={op_name} bad={len(bad)} orphan={len(orphan)}{note_text}", called)

    deep_ev: list[str] = []
    if plan.deep:
        deep_ok, deep_ev = _deep_check(plan.deep, after, result, kwargs, table)
        if not deep_ok:
            return Attempt("falsified", deep_ev,
                           f"op={op_name} deep={plan.deep} {' '.join(deep_ev)}{note_text}", called)

    # ★ 几何指纹对着**开局基线**比，不是对着"上一次调用前的快照"比：否则操作 A 删掉几何之后，
    #   操作 B 的调用前快照已经是空的 ⇒ 变异体 `drop_geometry` 逃逸（尺子看不见红）。
    status, evidence, unmet = _verify(wants, before, after,
                                      ctx.base_ents or before_ents, after_ents, result)
    if status == "unverified":
        return Attempt(status, evidence, f"op={op_name} unmet={' '.join(unmet)}{note_text}", called)
    return Attempt(status, evidence + deep_ev,
                   f"op={op_name} uuids={len(result)} {' '.join(evidence + deep_ev)}{note_text}",
                   called)


# ======================================================================================
# 判定
# ======================================================================================


@dataclass
class Report:
    verdict: str
    rc: int
    ops_ok: int
    ops_total: int
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    must_have_total: int = 0
    matched: int = 0
    ok: int = 0
    unverified: int = 0
    falsified: int = 0
    ops_probed: int = 0
    unprobed_ops: int = 0
    ops_partial: int = 0
    absent_ops: int = 0
    external_ops: int = 0
    external_must_have: int = 0
    family: str = ""
    module: str = ""
    #: 判定范围（空 = 全族；`a8` = SCOPE §1 A8 点名面 7 操作子集）——**判定行里可辨**，
    #: 免得子集的 7/7 被误读成全族的 7/7。
    scope: str = ""
    op_state: dict[str, str] = field(default_factory=dict)
    detail: list[str] = field(default_factory=list)

    def missing_field(self) -> str:
        return ",".join(self.missing) if self.missing else "-"

    def reasons_field(self) -> str:
        return ",".join(self.reasons) if self.reasons else "-"

    def line(self) -> str:
        # ★ 判定行必须能分辨「单 op 外部依赖缺席」与「整门 NA」：`external_ops=N` 点名**操作级**
        #   外部欠账（这些 op 不进 `ops_ok`），此时 rc 仍可为 0；整门 NA（rc=2）只留给
        #   「门自己的腿跑不起来 / 读不到」（unverified / unmatched_must_have）。
        scope = f" scope={self.scope}" if self.scope else ""
        return (f"{GATE} verdict={self.verdict} ops_ok={self.ops_ok} ops_total={self.ops_total} "
                f"external_ops={self.external_ops} missing={self.missing_field()}{scope} rc={self.rc}")


def _report_na(reason: str, must_have_total: int = 0, detail: Sequence[str] = ()) -> Report:
    return Report(verdict="NA", rc=2, ops_ok=0, ops_total=0, missing=[reason], reasons=[reason],
                  must_have_total=must_have_total, module=OPS_DIR_REL, detail=list(detail))


#: 操作级第四态（E2：三态不许同形；`0（真为零）`/`-1（未采）`/`NA` 同理）：
#: **外部依赖缺席**（`dependency_missing` 一类诚实缺席，见 `ABSENT_REASONS`）。
#: 它既**不算绿**（该 op 不进 `ops_ok`），也**不等于「门的腿断了」** ⇒ 不把整门判 NA；
#: 但若某个 must_have 能力**整条**只能由外部依赖承担且缺席 ⇒ 能力没验到，仍判红（rc=1）。
ST_EXTERNAL = "external"


def evaluate(module: Any, features: Mapping[str, Any], *, probes: Sequence[Probe] = PROBES,
             ctx: Ctx | None = None,
             setup_operations: Mapping[str, Any] | None = None) -> Report:
    """跑判定：族模块（单个或列表）× must_have 清单 → Report（不写真件树）。

    `setup_operations`：探针**前置**（`OP_PLANS[*].setup`，如 `export_pdf_text` 需要先建样式表）的
    取用面。缺省 = 参与计数的操作面；`--scope a8` 用它把"7 个计分操作"与"全族前置"分开
    （前置不进口径，也不许因为看不见前置而把真操作判假红）。
    """
    modules = list(module) if isinstance(module, (list, tuple)) else [module]
    must = list(features.get("must_have") or [])
    module_name = ",".join(getattr(m, "__name__", "?") for m in modules)
    problems = _contract_problems(modules)
    if problems:
        rep = Report(verdict="FAIL", rc=1, ops_ok=0, ops_total=0, missing=problems, reasons=problems,
                     must_have_total=len(must), module=module_name)
        rep.detail.append("contract_red=" + ",".join(problems))
        return rep

    operations, collisions, _origin = _merge_operations(modules)
    setups = operations if setup_operations is None else setup_operations
    ctx = ctx or build_ctx()

    # must_have → 探针认领（token 命中优先，剩下按顺序兜底；认领数必须可辨）
    claims: list[Probe | None] = []
    free = list(probes)
    for item in must:
        hay = f"{item.get('name', '')} {item.get('alias', '')}".lower()
        hit = next((p for p in free if any(tok.lower() in hay for tok in p.match)), None)
        if hit is not None:
            free.remove(hit)
        claims.append(hit)
    for idx, claim in enumerate(claims):
        if claim is None and free:
            claims[idx] = free.pop(0)

    rep = Report(verdict="NA", rc=2, ops_ok=0, ops_total=len(operations), must_have_total=len(must),
                 matched=sum(1 for c in claims if c is not None), module=module_name,
                 reasons=list(collisions))
    green_ops: set[str] = set()
    probed_ops: set[str] = set()
    #: 外部依赖缺席（诚实 `absent`：`dependency_missing` 一类）的操作 ⇒ 记 `ST_EXTERNAL`。
    external_ops: set[str] = set()

    for idx, (item, probe) in enumerate(zip(must, claims)):
        name = str(item.get("name", f"#{idx}"))
        if probe is None:
            rep.missing.append(f"unmatched_must_have:{idx}")
            rep.unverified += 1
            rep.detail.append(f"[{idx}] {name} :: unverified(no_probe)")
            continue
        candidates = _match_ops(operations, probe.ops)
        if not candidates:
            rep.missing.append(probe.key)
            rep.detail.append(f"[{idx}] {name} :: missing(op_not_found:{probe.ops[0]})")
            continue
        attempts: list[tuple[str, Attempt]] = []
        for op_name, op in candidates:
            attempt = _attempt(op_name, op, ctx, wants=probe.wants,
                               plan=OP_PLANS.get(op_name), scope=probe.scope,
                               operations=setups)
            probed_ops.add(op_name)
            probed_ops.update(attempt.called)
            if attempt.status == "absent":
                external_ops.add(op_name)
            attempts.append((op_name, attempt))
            if attempt.status == "ok":
                break
        op_name, best = min(attempts, key=lambda pair: _RANK[pair[1].status])
        tried = "|".join((f"{n}:external" if a.status == "absent" else f"{n}:{a.status}")
                         for n, a in attempts)
        rep.detail.append(f"[{idx}] {name} :: {best.status}(op={op_name} tried={tried} "
                          f"{' '.join(best.evidence)})".rstrip())
        if best.status == "ok":
            rep.ok += 1
            green_ops.add(op_name)
        elif best.status == "falsified":
            rep.falsified += 1
            rep.missing.append(probe.key)
        elif best.status == "absent":
            # 诚实外部缺席（dependency_missing 一类）：能力没验到 ⇒ 仍判红；但归因记 external，
            # **不**进 NA（NA 只留给"门自己的腿跑不起来/读不到"）。
            rep.external_must_have += 1
            rep.missing.append(f"{probe.key}:external")
        else:
            rep.unverified += 1
            rep.missing.append(f"{probe.key}:unverified")

    # 操作级普查：可探针集合里每个**还没验到后果**的操作按计划真跑一次；
    # 跑不到就照实记：外部依赖缺席 ⇒ external（不进 ops_ok，也不当门腿断的 partial）。
    for op_name, op in operations.items():
        if op_name in green_ops:
            continue
        try:
            attempt = _attempt(op_name, op, ctx, wants=(), plan=OP_PLANS.get(op_name),
                               scope="doc", operations=setups)
        except Exception as exc:  # pragma: no cover - 兜底：普查不许把门带崩
            attempt = Attempt("falsified", [f"probe_crashed:{type(exc).__name__}"],
                              f"op={op_name} crashed={type(exc).__name__}")
        probed_ops.add(op_name)
        probed_ops.update(attempt.called)
        if attempt.status == "ok":
            green_ops.add(op_name)
            rep.detail.append(f"[sweep] {op_name} :: ok({' '.join(attempt.evidence)})")
        else:
            if attempt.status == "absent":
                external_ops.add(op_name)
            shown = "external" if attempt.status == "absent" else attempt.status
            rep.detail.append(f"[sweep] {op_name} :: {shown} {attempt.detail}")

    op_state: dict[str, str] = {}
    for op_name in operations:
        if op_name in green_ops:
            op_state[op_name] = ST_OK
        elif op_name in external_ops:
            op_state[op_name] = ST_EXTERNAL
        elif op_name in probed_ops:
            op_state[op_name] = ST_PARTIAL
        else:
            op_state[op_name] = ST_UNPROBED

    rep.ops_probed = len(probed_ops)
    rep.ops_ok = len(green_ops)
    rep.ops_partial = sum(1 for s in op_state.values() if s == ST_PARTIAL)
    rep.unprobed_ops = sum(1 for s in op_state.values() if s == ST_UNPROBED)
    rep.external_ops = len(external_ops)
    rep.absent_ops = len(external_ops)   # 向后兼容别名：同一批 op，明细行/JSON 仍读得到
    rep.op_state = op_state
    if rep.matched < len(must):
        rep.reasons.append(f"unmatched_must_have={len(must) - rep.matched}")
    if rep.unverified:
        rep.reasons.append(f"unverified={rep.unverified}")
    if rep.falsified:
        rep.reasons.append(f"falsified={rep.falsified}")
    if rep.ops_partial:
        rep.reasons.append(f"ops_partial={rep.ops_partial}")
    if rep.unprobed_ops:
        rep.reasons.append(f"unprobed_ops={rep.unprobed_ops}")
    if rep.external_ops:
        rep.reasons.append(f"external_ops={rep.external_ops}")
    if rep.external_must_have:
        rep.reasons.append(f"external_must_have={rep.external_must_have}")

    if rep.falsified:
        rep.verdict, rep.rc = "FAIL", 1
    elif rep.unverified or rep.matched < len(must):
        rep.verdict, rep.rc = "NA", 2
    elif rep.external_must_have:
        # 能力**整条**只由外部依赖承担且缺席 ⇒ 能力没验到（红，归因写清是外部欠账）；
        # 这**不**进 NA：NA 只表示「门自己的腿跑不起来」。
        rep.verdict, rep.rc = "FAIL", 1
    elif rep.ok < len(must):
        rep.verdict, rep.rc = "FAIL", 1
    elif rep.ops_partial or rep.unprobed_ops:
        rep.verdict, rep.rc = "NA", 2
    else:
        rep.verdict, rep.rc = "PASS", 0
    return rep


# ======================================================================================
# A8「真 PDF」钉死：pages == layouts ∧ actual 不得是 svg（SCOPE §1 A8 判据原文）
# --------------------------------------------------------------------------------------
# 判据量的是**产物字节**：`%PDF-` 魔数 ∧ `/Type /Page` 页对象数 == 布局数 ∧ `/Count` 字段一致
# ∧ 逐页产物不得是 svg（`actual` 记录面也不得是 svg）。三个**行为变异体**（页数造假 / `/Count` 字段
# 造假 / svg 改名冒充 pdf）与判据同笔落在这里，`--selftest` 里必须全红；干净对照必须 PASS。
# ======================================================================================

A8_PROBE_GATE = "A8_PDF_PROBE"
A8_LAYOUTS = 3
#: A8 点名面（SCOPE §1：布局/视口/页面设置/PLOT/预览 + 真 PDF + 批量发布）→ must_have 标题 token。
A8_MUST_HAVE_TOKENS: tuple[str, ...] = ("图纸空间布局", "视口创建与排布", "页面设置",
                                        "打印主命令", "打印到 PDF", "打印预览", "批量发布")
#: 上面 7 条 must_have 各自要落到的操作名（顺序与 must-have 一一对应；evaluate 的认领顺序同此）。
A8_OP_NAMES: tuple[str, ...] = ("create_layout", "create_viewport", "page_setup", "plot",
                                "export_pdf_text", "preview", "publish")
#: 与 A8_OP_NAMES 一一对应的探针 key（认领顺序 = 这个顺序）。
A8_PROBE_KEYS: tuple[str, ...] = ("layout", "viewport_create", "page_setup", "plot",
                                  "plot_pdf", "preview", "publish")
A8_MUTANTS: tuple[str, ...] = ("a8_page_count_faked", "a8_count_field_lie", "a8_svg_as_pdf")
_SVG_STUB = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
             b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"></svg>\n')


@dataclass
class A8Result:
    """A8 真 PDF 判据的现读结论（字段与 SCOPE/探针判定行一一对应）。"""

    verdict: str
    rc: int
    magic: int
    pages: int
    layouts: int
    degraded: int
    total: int
    single_file_ok: int
    actual_pdf: int = -1
    failed: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"{A8_PROBE_GATE} verdict={self.verdict} magic={self.magic} pages={self.pages} "
                f"layouts={self.layouts} degraded={self.degraded}/{self.total} "
                f"single_file_ok={self.single_file_ok} rc={self.rc}")


def a8_features(features: Mapping[str, Any]) -> list[Any]:
    """A8 点名面的 must_have 条目（SCOPE §1 的 7 条；数量不符 = 判据漂移，调用侧记 NA）。"""
    must = list(features.get("must_have") or [])
    return [item for item in must
            if any(tok in str(item.get("name", "")) for tok in A8_MUST_HAVE_TOKENS)]


def a8_view_module(modules: Sequence[Any]) -> tuple[Any, dict[str, Any]]:
    """只留 A8 面 7 个操作的**只读视图**模块（真实现对象，不复制逻辑）。"""
    operations, _collisions, _origin = _merge_operations(modules)
    picked = {name: operations[name] for name in A8_OP_NAMES if name in operations}
    module = types.ModuleType("cadcore_ops_output_a8_view")
    module.FAMILY = FAMILY_ID
    module.OPERATIONS = picked
    return module, picked


def _sheet_lookup(modules: Sequence[Any]) -> Any:
    """族模块里 `sheet_by_uuid` 现取（读 Sheet.exports 的 actual 面）；取不到返回 None。"""
    for module in modules:
        getter = getattr(module, "sheet_by_uuid", None)
        if callable(getter):
            return getter
    return None


def _pdf_facts(path: Path) -> dict[str, int]:
    """产物字节现读：魔数 / `/Type /Page` 页对象数 / `/Count` 字段（读不到记 -1，不记 0）。"""
    try:
        blob = path.read_bytes()
    except OSError:
        return {"magic": 0, "pages": -1, "count_field": -1, "bytes": -1}
    counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", blob)]
    return {"magic": int(blob[:5] == b"%PDF-"),
            "pages": len(re.findall(rb"/Type\s*/Page(?![s])", blob)),
            "count_field": counts[0] if counts else -1, "bytes": len(blob)}


def a8_pdf_audit(operations: Mapping[str, Any], *, tmp: Path, layouts: int = A8_LAYOUTS,
                 sheet_of: Any = None) -> A8Result:
    """真跑生产路径并**现读产物字节**：逐页 publish（actual 不得是 svg）+ single_file 合并（页数=布局数）。"""
    ctx = build_ctx()
    ctx.tmp = tmp
    doc = ctx.doc
    try:
        ids = [str(operations["create_layout"](doc, name="A8L%d" % index)[0])
               for index in range(1, layouts + 1)]
        page_dir = tmp / "pages"
        single_dir = tmp / "single"
        operations["publish"](doc, sheet_uuids=ids, fmt="pdf", out_dir=str(page_dir))
        operations["publish"](doc, sheet_uuids=ids, fmt="pdf", out_dir=str(single_dir),
                              single_file=True)
    except Exception as exc:  # noqa: BLE001 - 探针崩了也要出判定行，不许把门带崩
        return A8Result("FAIL", 1, 0, -1, layouts, -1, 0, 0, -1,
                        ["probe_crashed:" + type(exc).__name__], [str(exc)[:200]])
    pages = sorted(p for p in page_dir.iterdir() if p.is_file()) if page_dir.is_dir() else []
    singles = sorted(p for p in single_dir.iterdir() if p.is_file()) if single_dir.is_dir() else []
    per_page = [_pdf_facts(path) for path in pages]
    merged = _pdf_facts(singles[0]) if len(singles) == 1 else {"magic": 0, "pages": -1,
                                                               "count_field": -1, "bytes": -1}
    degraded = sum(1 for item in per_page if item["magic"] != 1)
    magic = int(bool(per_page) and all(item["magic"] == 1 for item in per_page)
                and merged["magic"] == 1)
    single_ok = int(len(singles) == 1 and merged["magic"] == 1 and merged["pages"] == layouts
                    and merged["count_field"] == layouts)
    actuals: list[str] = []
    for sheet_uuid in ids:
        sheet = sheet_of(sheet_uuid) if callable(sheet_of) else None
        exports = getattr(sheet, "exports", None) if sheet is not None else None
        if isinstance(exports, list) and exports:
            actuals.append(str(getattr(exports[-1], "actual", "?")))
    actual_pdf = int(bool(actuals) and all(item == "pdf" for item in actuals))
    checks = (("magic", magic == 1),
              ("pages==layouts", merged["pages"] == layouts),
              ("count==layouts", merged["count_field"] == layouts),
              ("degraded==0", degraded == 0),
              ("single_file", single_ok == 1),
              ("actual!=svg", actual_pdf == 1))
    failed = [name for name, ok in checks if not ok]
    verdict, rc = ("PASS", 0) if not failed else ("FAIL", 1)
    detail = ([("failed=" + ",".join(failed))] if failed else []) + [
        # 边界（显式写出来，免得两个写出器被当成一个）：`export_pdf_text` 是 **model-space 单页**
        # 写出器（cadcore/ops/output_extra.py，`/Count` 恒 1，与布局数无关）；本判据量的页数是
        # `publish()` 走的**按布局分页**写出器（cadcore/draw/sheet.py 的 render_pdf_pages）。
        "boundary=export_pdf_text(model-space,single-page) vs publish(layout-paginated)",
        "page_files=%s" % [path.name for path in pages],
        "page_heads=%s" % ["pdf" if item["magic"] == 1 else "not-pdf" for item in per_page],
        "merged=%s" % merged, "merged_file=%s" % [path.name for path in singles],
        "actuals=%s" % actuals]
    return A8Result(verdict, rc, magic, merged["pages"], layouts, degraded, len(per_page),
                    single_ok, actual_pdf, failed, detail)


def _a8_mutant_operations(real: Mapping[str, Any], mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A8 判据的行为变异体（写死在文件里，不靠环境变量）：真 publish + 事后**真篡改产物**。

    每个变异体都留 `tampered` 自证（E2.5 夹具先自证）：产物没被真改掉 ⇒ 夹具自我报红。
    """
    ops = dict(real)
    real_publish = ops["publish"]
    state: dict[str, Any] = {"tampered": False, "evidence": "-"}

    def publish(doc: Any, **kwargs: Any) -> Any:
        result = real_publish(doc, **kwargs)
        out_dir = Path(str(kwargs.get("out_dir") or ""))
        if mode == "a8_svg_as_pdf" and not kwargs.get("single_file"):
            wrote = sorted(out_dir.glob("*.pdf")) if out_dir.is_dir() else []
            for path in wrote:
                path.write_bytes(_SVG_STUB)
            heads = [path.read_bytes()[:5] for path in wrote]
            state["tampered"] = bool(wrote) and all(head != b"%PDF-" for head in heads)
            state["evidence"] = "svg_heads=%d/%d" % (sum(1 for h in heads if h != b"%PDF-"),
                                                    len(heads))
            return result
        if kwargs.get("single_file") and out_dir.is_dir():
            target = next(iter(sorted(out_dir.glob("*.pdf"))), None)
            if target is None:
                return result
            if mode == "a8_count_field_lie":
                blob = target.read_bytes()
                lied = blob.replace(b"/Count %d" % len(kwargs["sheet_uuids"]), b"/Count 1")
                target.write_bytes(lied)
                facts = _pdf_facts(target)
                state["tampered"] = lied != blob and facts["count_field"] == 1
                state["evidence"] = "count_field=%d pages=%d" % (facts["count_field"],
                                                                 facts["pages"])
            elif mode == "a8_page_count_faked":
                real_publish(doc, sheet_uuids=list(kwargs["sheet_uuids"])[:1], fmt="pdf",
                             out_dir=str(out_dir), single_file=True)
                facts = _pdf_facts(target)
                state["tampered"] = facts["pages"] == 1 and facts["count_field"] == 1
                state["evidence"] = "pages=%d count_field=%d" % (facts["pages"],
                                                                 facts["count_field"])
        return result

    ops["publish"] = publish
    return ops, state


def a8_selftest() -> tuple[bool, list[str], str]:
    """A8 判据的阴阳双证：干净对照（真族模块）必须 PASS；三个变异体必须红且**自证改到了产物**。"""
    fam = _load_family_modules(_oss_root(), injected=None)
    if not fam.modules:
        return False, [], "a8_modules_absent"
    _view, picked = a8_view_module(fam.modules)
    lookup = _sheet_lookup(fam.modules)
    root = Path(tempfile.mkdtemp(prefix="cad-a8-selftest-"))
    try:
        control = a8_pdf_audit(picked, tmp=root / "control", sheet_of=lookup)
        if control.verdict != "PASS" or control.rc != 0:
            return False, [], "a8_control_failed:" + control.line()
        red: list[str] = []
        problems: list[str] = []
        for mode in A8_MUTANTS:
            ops, state = _a8_mutant_operations(picked, mode)
            report = a8_pdf_audit(ops, tmp=root / mode, sheet_of=lookup)
            if report.verdict == "PASS" or report.rc == 0:
                problems.append(mode + ":not_red")
                continue
            if not state.get("tampered"):
                problems.append(mode + ":fixture_not_effective")
                continue
            red.append("%s:%s/%s(%s)" % (mode, report.verdict, report.rc,
                                         state.get("evidence", "-")))
        return (not problems), red, ",".join(problems) if problems else "-"
    finally:
        shutil.rmtree(root, ignore_errors=True)

# ======================================================================================
# --selftest：进程内假族模块（阴阳双证；不落盘）
# ======================================================================================


def _satisfy(ctx: Ctx, probe: Probe) -> list[str]:
    """干净对照：按探针声明的事实造出真后果（夹具用；真族模块不走这里）。"""
    created: list[str] = []
    for want in probe.wants:
        kind, _, arg = want.partition(":")
        if kind == "sheets_ge":
            # ★ 必须每次现取载荷：`Document.to_payload()` 给的是**快照**（`list(self.sheets)`），
            #   拿调用前那份旧快照数图纸数 ⇒ 永远数不到新增的那张 ⇒ `--selftest` 死循环。
            while len(_sheets(ctx.payload())) < int(arg):
                sheet_uuid = _new_uuid()
                ctx.doc.sheets.append({"uuid": sheet_uuid, "standard_profile": "iso",
                                       "size": {"w_nm": 297_000_000, "h_nm": 210_000_000},
                                       "scale": "1:1", "views": [], "dimensions": [],
                                       "title_block": {"key": "doc.untitled"}})
                created.append(sheet_uuid)
        elif kind == "sheets_delta_ge":
            # 干净对照：主调用真往 `sheets[]` 里加 n 张（否则这条判据在夹具里就是"永远红"）。
            for _ in range(int(arg)):
                sheet_uuid = _new_uuid()
                ctx.doc.sheets.append({"uuid": sheet_uuid, "standard_profile": "iso",
                                       "size": {"w_nm": 297_000_000, "h_nm": 210_000_000},
                                       "scale": "1:1", "views": [], "dimensions": [],
                                       "title_block": {"key": "doc.untitled"}})
                created.append(sheet_uuid)
        elif kind == "sheet_name_has":
            # 干净对照：主调用真在 `sheets[]` 里留下一张**叫这个名字**的布局。
            sheet_uuid = _new_uuid()
            ctx.doc.sheets.append({"uuid": sheet_uuid, "name": arg, "standard_profile": "iso",
                                   "size": {"w_nm": 297_000_000, "h_nm": 210_000_000},
                                   "scale": "1:1", "views": [], "dimensions": [],
                                   "title_block": {"key": "doc.untitled"}})
            created.append(sheet_uuid)
        elif kind == "views_ge":
            if not ctx.doc.sheets:
                ctx.doc.sheets.append({"uuid": _new_uuid(), "standard_profile": "iso",
                                       "size": {"w_nm": 297_000_000, "h_nm": 210_000_000},
                                       "scale": "1:1", "views": [], "dimensions": [],
                                       "title_block": {}})
            sheet = ctx.doc.sheets[0]
            while len(sheet.get("views") or []) < int(arg):
                view_uuid = _new_uuid()
                sheet.setdefault("views", []).append({"uuid": view_uuid, "direction": "front",
                                                      "at_nm": [0, 0], "hidden": False, "cache": {}})
                created.append(view_uuid)
        else:
            tok = arg.split("|")[0]
            rec_uuid = _new_uuid()
            ctx.doc.extra[f"{tok}_record"] = {"uuid": rec_uuid, "kind": tok, "value_nm": 25_400_000,
                                              "note": tok, "layer": "0", "lweight_mm": 0.25}
            created.append(rec_uuid)
    if not created:
        created = [ctx.base_entity_uuids[0]]
    return created


def _fixture_module(mode: str) -> Any:
    """行为写死的假族模块：`clean` + 变异体（全部进程内，绝不落盘）。"""
    ops: dict[str, Callable[..., list[str]]] = {}

    def make(probe: Probe) -> Callable[..., list[str]]:
        def op(target: Any, **kwargs: Any) -> list[str]:
            if mode == "raises":
                raise _cad_error()("fixture mutant", reason="mutant_raises")
            if mode == "nonuuid":
                return [1, 2]  # type: ignore[list-item]
            if mode == "noop":
                return []
            if mode == "orphan":
                return [_uuid.uuid4().hex]
            if mode == "unverified":
                return [_new_uuid()]  # 只跑不落后果
            created = _satisfy(_CTX_HOLDER[0], probe)
            if mode == "drop_geometry":
                _CTX_HOLDER[0].sketch["entities"] = []
            return list(created)

        return op

    for probe in PROBES:
        ops[probe.ops[0]] = make(probe)
    module = types.ModuleType("cadcore_ops_output_fixture")
    if mode == "bad_contract":
        module.FAMILY = ""            # 契约红：FAMILY 空
        module.OPERATIONS = {}        # 契约红：OPERATIONS 空
        return module
    module.FAMILY = FAMILY_ID
    module.OPERATIONS = ops
    return module


_CTX_HOLDER: list[Ctx] = []


def _cad_error() -> Any:
    try:
        _ensure_cadcore()
        return importlib.import_module("cadcore.errors").CadError
    except Exception:  # pragma: no cover - 兜底：仍抛一个真异常，红照样看得见
        return RuntimeError


def selftest(features: Mapping[str, Any]) -> int:
    """阴阳双证：干净对照必须 PASS；每个变异体必须红（verdict ≠ PASS）。"""
    must = list(features.get("must_have") or [])
    matched, _free = 0, list(PROBES)
    for item in must:
        hay = f"{item.get('name', '')} {item.get('alias', '')}".lower()
        hit = next((p for p in _free if any(tok.lower() in hay for tok in p.match)), None)
        if hit is not None:
            _free.remove(hit)
            matched += 1
    if len(PROBES) < 3 or len(must) < 3 or matched < len(must):
        print(f"{SELFTEST_GATE}: verdict=NA mutants_red=0/0 control=NA control_rc=2 "
              f"probes={len(PROBES)} must_have={len(must)} matched={matched} "
              f"reasons=fixture_invalid rc=2")
        return 2

    _CTX_HOLDER.clear()
    _CTX_HOLDER.append(build_ctx())
    control = evaluate(_fixture_module("clean"), features, ctx=_CTX_HOLDER[0])
    clean_fingerprint = json.dumps(control.line(), sort_keys=True)

    mutants = ("nonuuid", "noop", "raises", "orphan", "drop_geometry", "bad_contract", "unverified")
    red: list[str] = []
    stuck: list[str] = []
    for mode in mutants:
        _CTX_HOLDER[0] = build_ctx()
        rep = evaluate(_fixture_module(mode), features, ctx=_CTX_HOLDER[0])
        if rep.verdict != "PASS" and rep.rc != 0:
            red.append(f"{mode}:{rep.verdict}/{rep.rc}")
            if json.dumps(rep.line(), sort_keys=True) == clean_fingerprint:
                stuck.append(mode)  # 变异体没生效 = 尺子没看见红
        else:
            stuck.append(f"{mode}:not_red")

    # A8 真 PDF 判据的阴阳双证（同笔件：判据 + 变异体都在本文件里）
    a8_control_ok, a8_red, a8_problems = a8_selftest()
    clean_body = control.verdict == "PASS" and control.rc == 0 and control.ok == len(must)
    # 夹具自证：干净世界真的产出了后果（不是空夹具），且靶子在判定里被读过
    fixture_proof = control.ok == len(must) and control.ops_ok > 0
    ok = (clean_body and fixture_proof and not stuck and len(red) == len(mutants)
          and a8_control_ok and len(a8_red) == len(A8_MUTANTS))
    verdict = "PASS" if ok else "FAIL"
    rc = 0 if ok else 1
    detail = ",".join(sorted(stuck)) if stuck else "-"
    print(f"{SELFTEST_GATE}: verdict={verdict} mutants_red={len(red) - len(stuck)}/{len(mutants)} "
          f"control={control.verdict} control_rc={control.rc} control_ok={control.ok} "
          f"probes={len(PROBES)} must_have={len(must)} matched={matched} "
          f"fixture_selfcheck={'ok' if fixture_proof else 'fail'} stuck={detail} "
          f"a8_control={'PASS' if a8_control_ok else 'FAIL'} "
          f"a8_mutants_red={len(a8_red)}/{len(A8_MUTANTS)} a8_detail={a8_problems} rc={rc}")
    print("  " + " ".join(red))
    print("  a8 " + " ".join(a8_red))
    return rc


# ======================================================================================
# CLI
# ======================================================================================


def _load_features(oss_root: Path) -> tuple[dict[str, Any] | None, str]:
    path = oss_root / FEATURES_REL
    if not path.is_file():
        return None, "features_absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return None, f"features_unreadable:{type(exc).__name__}"



def _run_a8(fam: FamilyModules, features: Mapping[str, Any], tmp_dir: Path, *,
            quiet: bool, json_out: str) -> int:
    """`--scope a8`：只跑 A8 点名面 7 个操作 + 真 PDF 钉死（判定行带 `scope=a8`，子集可辨）。"""
    must = a8_features(features)
    view, picked = a8_view_module(fam.modules)
    if len(must) != len(A8_OP_NAMES) or len(picked) != len(A8_OP_NAMES):
        print(f"{GATE} verdict=NA ops_ok=0 ops_total={len(picked)} scope=a8 "
              f"missing=a8_scope_incomplete must_have={len(must)} ops={len(picked)} rc=2")
        return 2
    probes = tuple(probe for key in A8_PROBE_KEYS for probe in PROBES if probe.key == key)
    setups, _collisions, _origin = _merge_operations(fam.modules)
    ctx = build_ctx()
    ctx.tmp = tmp_dir
    rep = evaluate(view, {"must_have": must}, probes=probes, ctx=ctx, setup_operations=setups)
    rep.scope = "a8"
    audit = a8_pdf_audit(picked, tmp=tmp_dir / "a8", sheet_of=_sheet_lookup(fam.modules))
    if audit.rc != 0:
        rep.verdict, rep.rc = "FAIL", 1
        rep.reasons.append("a8_pdf_failed=" + ",".join(audit.failed))
    green = [name for name in A8_OP_NAMES if rep.op_state.get(name) == ST_OK]
    if rep.verdict == "PASS" and len(green) != len(A8_OP_NAMES):
        # 操作认领漂移（探针没落到点名的 7 个操作上）⇒ 子集判定不作数
        rep.verdict, rep.rc = "NA", 2
        rep.reasons.append("a8_op_claim_mismatch")
    rep.reasons.extend(fam.problems)
    print(rep.line())
    print(audit.line())
    if not quiet:
        print(f"  family={getattr(fam.modules[0], 'FAMILY', '?')} modules={rep.module} scope=a8 "
              f"ops={rep.ops_total} ops_ok={rep.ops_ok} green={','.join(green)} "
              f"must_have={rep.must_have_total} matched={rep.matched} "
              f"unverified={rep.unverified} falsified={rep.falsified} "
              f"reasons={rep.reasons_field()}")
        for line in rep.detail:
            print("  " + line)
        for line in audit.detail:
            print("  a8 " + line)
    if json_out:
        payload = {"gate": GATE, "scope": "a8", "verdict": rep.verdict, "rc": rep.rc,
                   "ops_ok": rep.ops_ok, "ops_total": rep.ops_total, "reasons": rep.reasons,
                   "modules": rep.module, "detail": rep.detail,
                   "a8": {"verdict": audit.verdict, "rc": audit.rc, "magic": audit.magic,
                          "pages": audit.pages, "layouts": audit.layouts,
                          "degraded": audit.degraded, "total": audit.total,
                          "single_file_ok": audit.single_file_ok, "actual_pdf": audit.actual_pdf,
                          "failed": audit.failed, "detail": audit.detail}}
        Path(json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    return rep.rc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="出图族（autocad-output）几何后果门：判定行 CAD_PARITY_OUTPUT")
    parser.add_argument("--check", action="store_true", help="跑判定（裸跑即此模式）")
    parser.add_argument("--selftest", action="store_true", help="尺子的阴阳双证（变异体必须全红）")
    parser.add_argument("--scope", default="",
                        help="判定范围：a8 = 只跑 A8 点名面 7 个操作 + 真 PDF 钉死（缺省=全族）")
    parser.add_argument("--oss-root", default="", help="oss-alternatives 根目录（缺省按脚本位置推断）")
    parser.add_argument("--json-out", default="", help="判定结果机读落盘路径（缺省不写；本门默认不写盘）")
    parser.add_argument("--quiet", action="store_true", help="不打逐条证据行")
    args = parser.parse_args(list(argv) if argv is not None else None)

    oss_root = Path(args.oss_root).resolve() if args.oss_root else _oss_root()
    features, ferr = _load_features(oss_root)

    if args.selftest:
        if features is None:
            print(f"{SELFTEST_GATE}: verdict=NA mutants_red=0/0 control=NA control_rc=2 "
                  f"reasons={ferr} rc=2")
            return 2
        return selftest(features)

    if features is None:
        rep = _report_na(ferr)
        print(rep.line())
        return rep.rc

    fam = _load_family_modules(oss_root, injected=None)
    if not fam.modules:
        rep = _report_na(fam.reason or "module_absent",
                         must_have_total=len(features.get("must_have") or []), detail=fam.problems)
        print(rep.line())
        if not args.quiet:
            print(f"  modules={fam.reason or '-'} must_have={rep.must_have_total} matched=0 "
                  f"measured=0 problems={','.join(fam.problems) or '-'} reasons={rep.reasons_field()}")
        return rep.rc

    tmp_dir = Path(tempfile.mkdtemp(prefix="cad-parity-output-gate-"))
    audit: A8Result | None = None
    try:
        if args.scope == "a8":
            return _run_a8(fam, features, tmp_dir, quiet=args.quiet, json_out=args.json_out)
        ctx = build_ctx()
        ctx.tmp = tmp_dir
        rep = evaluate(fam.modules, features, ctx=ctx)
        operations, _collisions, _origin = _merge_operations(fam.modules)
        audit = a8_pdf_audit(operations, tmp=tmp_dir / "a8", sheet_of=_sheet_lookup(fam.modules))
        if audit.rc != 0:
            # ③ 门侧钉死：A8 真 PDF 判据（pages==layouts ∧ actual!=svg）不许被"族操作全绿"掩盖
            rep.verdict, rep.rc = "FAIL", 1
            rep.reasons.append("a8_pdf_failed=" + ",".join(audit.failed))
            rep.missing.append("a8:" + audit.verdict)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    rep.reasons.extend(fam.problems)
    print(rep.line())
    if audit is not None:
        print(audit.line())
    if not args.quiet:
        partial = sorted(k for k, v in rep.op_state.items() if v == ST_PARTIAL)
        unprobed = sorted(k for k, v in rep.op_state.items() if v == ST_UNPROBED)
        print(f"  family={getattr(fam.modules[0], 'FAMILY', '?')} modules={rep.module} "
              f"ops={rep.ops_total} probed={rep.ops_probed} ops_ok={rep.ops_ok} "
              f"partial={rep.ops_partial} unprobed={rep.unprobed_ops} absent={rep.absent_ops} "
              f"must_have={rep.must_have_total} must_have_ok={rep.ok} matched={rep.matched} "
              f"unverified={rep.unverified} falsified={rep.falsified} "
              f"reasons={rep.reasons_field()}")
        if partial:
            print(f"  partial_ops={','.join(partial[:12])}")
        if unprobed:
            print(f"  unprobed_ops={','.join(unprobed[:12])}")
        for line in rep.detail[:32]:
            print("  " + line)
        if len(rep.detail) > 32:
            print(f"  … detail_truncated={len(rep.detail) - 32}")
        if audit is not None:
            for line in audit.detail:
                print("  a8 " + line)
    if args.json_out:
        payload = {"gate": GATE, "verdict": rep.verdict, "rc": rep.rc, "ops_ok": rep.ops_ok,
                   "ops_total": rep.ops_total, "missing": rep.missing, "reasons": rep.reasons,
                   "modules": rep.module, "op_state": rep.op_state, "detail": rep.detail,
                   "scope": rep.scope,
                   "a8": None if audit is None else {
                       "verdict": audit.verdict, "rc": audit.rc, "magic": audit.magic,
                       "pages": audit.pages, "layouts": audit.layouts,
                       "degraded": audit.degraded, "total": audit.total,
                       "single_file_ok": audit.single_file_ok, "actual_pdf": audit.actual_pdf,
                       "failed": audit.failed, "detail": audit.detail}}
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    return rep.rc


if __name__ == "__main__":
    sys.exit(main())
