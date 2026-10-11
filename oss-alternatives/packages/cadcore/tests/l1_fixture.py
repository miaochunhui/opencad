"""tests.l1_fixture —— 真 L1 夹具：一份**完整可用**的 `cadcore` L1 文档。

为什么有这份文件（背景，不写在别处）
------------------------------------
空壳片点名席的诚实结论是 `live_ops=47/176 (26.7%)`，但它自己标注了证据不足：探针喂的是 **dict**，
而很多 op 明确要求真 `cadcore.docmodel.Document` / L1 容器（reason 里出现
`l1_document_required` / `no_base_solid` / `no_mates` / `no_sheet_collection` / `no_layout`）。
那些 reason 只能证明"**探针下没有成功路径**"，不能证明"该 op 是空壳"。本文件把这两种结论分开：
先用 cadcore 自己的公开构造 API 造一份真文档，再让 `tests/test_l1_fixture.py` 逐条现算校验夹具本身。

构造口径（**不手搓 dict 冒充**）
--------------------------------
* 文档壳 = `docmodel.new_document()` 返回的真 `Document`（不是 dict、不是 `SimpleNamespace`）；
* 草图里**每条实体**都过 `cadcore.sketch.commands` 的公开几何命令（`add_line` / `add_circle`），
  **每条约束**都过 `cadcore.sketch.constraints.add_constraint`（公开写入器，内部先跑 `validate_constraint`）；
* 拉伸体 = `Document.features[]` 的 L1 载荷（字段与 `docmodel._sample_document()` 同源），
  它的**体积由 `docmodel.recompute(doc, nano_kernel())` 现算**，不写死、不声明；
* 图纸与视口 = `cadcore.draw.sheet.make_sheet()` + `add_view()` + `sheet_to_l1()`；
* 图层 / 块 / 插入 / 外部参照 = `cadcore.ops.organization` 的公开操作（表落 `doc.extra["organization"]`）；
* 零部件 / 配合 = `cadcore.ops.sw_assembly.insert_component()` / `mate_add()`
  （表落 `doc.extra["sw_assembly"]`，与本仓所有族模块同一约定）。

上下文扩容（本轮新增，**全部走公开写入口**，逐条落 `context_report`）
------------------------------------------------------------------
勘察单（`cadcore/ops/features/QUARANTINE-TRIAGE.md`）把大量 `reject` 归因于"尺子搭不出上下文"，
本模块据此补出下面几类上下文（每类都记了"是哪次公开调用造出来的"，失败也如实记，不静默）：

===============  ==============================================================  ==================
上下文            公开构造调用                                                    对应 reject 原因
===============  ==============================================================  ==================
栅格/捕捉已配置     `precision_ops.grid_set` / `snap_enable` / `snap_kinds_set`      `grid_not_configured`
                 / `snap_tolerance_set`                                          （precision 族）
保护层 + 带特性图层  `organization.layer_create` / `layer_set`                       `protected_layer`
                                                                                 `no_properties`
多视图图纸          `draw.sheet.add_view()` ×2（同一 sheet，两个视口）                `source_view_required`
标注（尺寸）        `annotation.add_dimension()`                                     `bad_uuid`（dim_*）
对齐已标注的配合     `sw_assembly.mate_add()` 后跟公开的 `mate_align`（若该入口存在）  `alignment_unlabelled`
===============  ==============================================================  ==================

**没有补**的上下文（故意留红，当"没放宽"的阴性对照）：GDT 帧 / BOM 行 / 图纸模板记录 /
装配 component 的 L1 实体 —— 这些 op 在本夹具下**必须仍然 reject**。
`tests/test_features_live.py` 的 `RcVerdictSelfTest.test_fixture_context_does_not_relax_reject`
拿尺子自己的 `_variants` + `_run_ladder` 现跑一遍，证明它们没被放宽成绿。

`body` 在哪：L1 冻结字段里**没有** `body` 字段 —— 本仓口径（`sw_features._resolve_base`）是
"基体 = 未抑制的 `kind == "extrude"` 特征"，所以 `features[]` 里那条拉伸**就是** body；
本模块把它以 `feature_uuid` 暴露出来，别处不许再发明第二个 body 字段。

确定性与副作用
--------------
* 文档 `meta.doc_uuid` / `created_at` 被钉成常量 ⇒ 图纸 uuid（`uuid5(doc_uuid, …)` 派生）可复现；
* 实体 uuid 由 `cadcore.sketch.constraints.new_uuid()`（uuid4）生成，**不**钉死（不篡改上游 API 行为）
  ⇒ "跨两次构造逐字节相同"不是本夹具的承诺，`test_l1_fixture.py` 只断言**规范形态的固定点**；
* 本模块**不写任何文件**（`test_08` 用前后快照证明），不碰别人的文件。

用法
----
    cd oss-alternatives/packages/cadcore
    python3 tests/l1_fixture.py            # 打印一行判定行（构造 + 重建现算）
    python3 -m unittest tests.test_l1_fixture -v
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:  # 直跑本文件时的包路径兜底（discover 路径已带）
    sys.path.insert(0, _ROOT)

from cadcore.docmodel import (  # noqa: E402
    Document,
    document_to_l1,
    new_document,
    recompute,
)
from cadcore.draw.sheet import (  # noqa: E402
    DEFAULT_SCALE,
    DEFAULT_SIZE,
    add_view,
    make_sheet,
    sheet_to_l1,
)
from cadcore.errors import CadError  # noqa: E402
from cadcore.kernel.nano import nano_kernel  # noqa: E402
from cadcore.ops import annotation as annotation_ops  # noqa: E402
from cadcore.ops import organization, precision_ops, sw_assembly  # noqa: E402
from cadcore.sketch import commands as sketch_commands  # noqa: E402
from cadcore.sketch import constraints as sketch_constraints  # noqa: E402

# --------------------------------------------------------------------------------------
# 夹具参数（长度一律整数纳米；改这里就等于改夹具，别在调用点现编）
# --------------------------------------------------------------------------------------

NM_PER_MM = 1_000_000
DOC_UNIT = "mm"
DOC_STANDARD = "iso"
DOC_LANGUAGE = "en"
DOC_TITLE_KEY = "doc.plate_bracket"
FIXTURE_CREATED_AT = "2026-09-30T00:00:00Z"

PLATE_LENGTH_NM = 60 * NM_PER_MM
PLATE_WIDTH_NM = 40 * NM_PER_MM
PLATE_THICKNESS_NM = 10 * NM_PER_MM
HOLE_RADIUS_NM = 5 * NM_PER_MM

LAYER_PLATE = "PLATE"
LAYER_HOLES = "HOLES"
#: AutoCAD 语义里层 `"0"` 是**受保护层**（`layer_rename` 拒改 ⇒ `protected_layer`）。
LAYER_DEFAULT = "0"
BLOCK_NAME = "HOLE_PROFILE"
BLOCK_INSERT_AT_NM = (30 * NM_PER_MM, 0)
XREF_NAME = "FRAME_SHEET"
XREF_PATH = "xrefs/frame.dxf"

PART_BASE = "base_plate"
PART_TOP = "top_plate"
#: 配合参考名：`sw_assembly._refs()` 把入参与缺省 `origin` 合并，本夹具显式给出同一个名字。
ASSEMBLY_REF_NAME = "origin"
MATE_KIND = "concentric"
#: 对齐标注名（`alignment_unlabelled` 的对侧：已标注的配合记录）。
ALIGNMENT_NAME = "aligned"

SHEET_VIEW = "front"
SHEET_VIEW_AT_NM = (20 * NM_PER_MM, 20 * NM_PER_MM)
#: 同一张图纸上的**第二个视口**（多视图上下文；`source_view_required` 那类 op 要的源视图）。
SHEET_VIEW_ALT = "top"
SHEET_VIEW_ALT_AT_NM = (90 * NM_PER_MM, 20 * NM_PER_MM)

#: 已配置的栅格/捕捉（`grid_not_configured` 的对侧）。
GRID_SPACING_NM = 10 * NM_PER_MM
GRID_MAJOR_EVERY = 5
SNAP_TOLERANCE_NM = 1 * NM_PER_MM
SNAP_KINDS = ("endpoint", "midpoint", "center", "quadrant", "intersection")

#: 尺寸标注的标称值（真值由 plate 几何给出，不另编）。
DIMENSION_VALUE_NM = PLATE_LENGTH_NM

#: 夹具自己的 uuid5 命名空间（本模块内所有"需要稳定"的 uuid 都从它派生）。
_NAMESPACE = uuid.UUID("6f1d0d5e-1f00-4000-8000-000000000001")


def stable_uuid(*parts: object) -> str:
    """夹具内稳定 uuid（同一串 parts ⇒ 同一 uuid）。只用于**夹具命名**，不改上游行为。"""
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(item) for item in parts)))


STABLE_DOC_UUID = stable_uuid("doc", "plate-bracket")
PLATE_SKETCH_UUID = stable_uuid("sketch", "plate_profile")
HOLE_SKETCH_UUID = stable_uuid("sketch", "hole_profile")
EXTRUDE_FEATURE_UUID = stable_uuid("feature", "extrude", "plate")


def _step(context: dict[str, Any], label: str, fn: Callable[[], Any]) -> Any:
    """跑一次公开构造调用并**如实记账**（成功记 `ok`，失败记异常类型+原因；不吞、不假绿）。

    夹具的构造失败**不许**让整份夹具消失（那样尺子会退回兜底形状、把"夹具缺席"误记成 op 缺口），
    所以这里捕获后写进 `context_report`，由尺子与 `tests/test_l1_fixture.py` 现读；失败即缺那一类上下文。
    """
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001 - 构造失败要留痕，不许静默
        context[label] = "FAIL:%s:%s" % (type(exc).__name__,
                                         str(getattr(exc, "reason", "") or exc)[:60])
        return None
    context[label] = "ok"
    return value


# --------------------------------------------------------------------------------------
# 构造结果
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class L1Fixture:
    """夹具构造结果：真文档 + **现算出来的 uuid 账**（测试据此断言，禁硬编码 uuid）。"""

    doc: Document
    kernel: Any = field(repr=False)
    plate_sketch_uuid: str = ""
    hole_sketch_uuid: str = ""
    plate_line_uuids: tuple[str, ...] = ()
    hole_circle_uuid: str = ""
    feature_uuid: str = ""
    sheet_uuid: str = ""
    view_uuid: str = ""
    view_alt_uuid: str = ""
    dimension_uuid: str = ""
    component_uuids: tuple[str, ...] = ()
    mate_uuid: str = ""
    layer_uuids: tuple[str, ...] = ()
    block_uuid: str = ""
    insert_uuid: str = ""
    xref_uuid: str = ""
    # -- 上下文扩容账（测试与取值器都读这里，不靠猜）------------------------------------
    protected_layer: str = LAYER_DEFAULT
    grid_spacing_nm: int = 0
    snap_kinds: tuple[str, ...] = ()
    snap_tolerance_nm: int = 0
    alignment_name: str = ""
    entity_properties: dict[str, Any] = field(default_factory=dict)
    context_report: dict[str, str] = field(default_factory=dict)

    # -- 便捷读法（测试与下游都用这些，不直接翻 extra）--------------------------------
    @property
    def plate_sketch(self) -> dict[str, Any]:
        return self.doc.sketches[0]

    @property
    def hole_sketch(self) -> dict[str, Any]:
        return self.doc.sketches[1]

    @property
    def organization_store(self) -> dict[str, Any]:
        return self.doc.extra["organization"]

    @property
    def assembly_store(self) -> dict[str, Any]:
        return self.doc.extra["sw_assembly"]

    @property
    def layer_names(self) -> tuple[str, ...]:
        """组织表里的真层名（取值器按名字取"合法层"时用它，不用硬编码字符串）。"""
        names = [str(row.get("name")) for row in self.organization_store.get("layers", [])
                 if isinstance(row, dict) and row.get("name")]
        return tuple(dict.fromkeys(names))

    @property
    def labelled_mate_uuid(self) -> str:
        """已标注对齐的配合 uuid（没有则空串 ⇒ 取值器不许拿它冒充合法值）。"""
        store = self.assembly_store
        for row in store.get("mates", []) or []:
            if isinstance(row, dict) and str(row.get("alignment") or ""):
                return str(row.get("uuid") or "")
        return ""


# --------------------------------------------------------------------------------------
# 各部件构造（每个都在公开 API 上跑，不落手写实体/约束/表格行）
# --------------------------------------------------------------------------------------


def _new_sketch_payload(*, sketch_uuid: str, plane: str = "XY") -> dict[str, Any]:
    """空草图画布：字段集照 L1 草图形状（`uuid/plane/entities/constraints/dimensions/solved`）。

    实体与约束**不在这里写** —— 全部由 `sketch_commands` / `sketch_constraints` 落进来；
    本函数只提供 `check_invariants()` 认得的容器形状（画布不是实体，别把两者混为一谈）。
    """
    return {
        "uuid": sketch_uuid,
        "plane": plane,
        "entities": [],
        "constraints": [],
        "dimensions": [],
        "solved": {"dof": -1, "converged": False, "cache": True},
    }


def _add_rectangular_profile(sk: dict[str, Any], *, layer: str,
                             size_nm: tuple[int, int] = (PLATE_LENGTH_NM, PLATE_WIDTH_NM),
                             origin_nm: tuple[int, int] = (0, 0)) -> list[str]:
    """四条边 + 四条 `coincident` 约束 ⇒ 闭合轮廓（闭合性由 `closed_profiles()` 现算证明）。"""
    x0, y0 = origin_nm
    x1, y1 = x0 + int(size_nm[0]), y0 + int(size_nm[1])
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    lines = [sketch_commands.add_line(sk, p1=corners[index], p2=corners[(index + 1) % 4],
                                      layer=layer)
             for index in range(4)]
    for index in range(4):
        sketch_constraints.add_constraint(
            sk, kind="coincident",
            refs=[(lines[index], "end"), (lines[(index + 1) % 4], "start")])
    return lines


def _extrude_feature(*, feature_uuid: str, sketch_uuid: str,
                     distance_nm: int = PLATE_THICKNESS_NM) -> dict[str, Any]:
    """拉伸体（= 本仓口径的 body）：L1 `features[]` 载荷，字段与 `docmodel._sample_document()` 同源。"""
    return {
        "uuid": feature_uuid,
        "kind": "extrude",
        "inputs": [sketch_uuid],
        "params": {"distance_nm": int(distance_nm), "symmetric": False, "angle_deg": "0",
                   "op": "new"},
        "suppressed": False,
    }


def _assign_layer(doc: Document, *, refs: list[str], layer_uuid: str, layer_name: str) -> list[str]:
    """`layer_assign` 的 `layer` 引用口径（uuid / name）在模块里没写死，两种公开入参各试一次。

    两种都失败 ⇒ 第二次的 `CadError` 原样抛出（**不吞**）；这里不为通过而放宽任何东西。
    """
    try:
        return organization.layer_assign(doc, refs=list(refs), layer=layer_uuid)
    except CadError:
        return organization.layer_assign(doc, refs=list(refs), layer=layer_name)


def _add_sheet(doc: Document, *, kernel: Any, shape: Any,
               context: dict[str, Any]) -> tuple[str, str, str]:
    """图纸 + **两个**视口（多视图上下文），返回 `(sheet_uuid, view_uuid, view_alt_uuid)`。

    L1 条目由 `sheet_to_l1()` 给出；第二个视口走同一个公开 `add_view()`，不是手改 L1 载荷。
    """
    sheet = make_sheet(doc, standard=DOC_STANDARD, language=DOC_LANGUAGE,
                       size=DEFAULT_SIZE, scale=DEFAULT_SCALE)
    view_uuid = _step(context, "sheet.view", lambda: add_view(
        sheet, shape, view=SHEET_VIEW, at_nm=SHEET_VIEW_AT_NM, kernel=kernel, hidden=True))
    view_alt_uuid = _step(context, "sheet.view_alt", lambda: add_view(
        sheet, shape, view=SHEET_VIEW_ALT, at_nm=SHEET_VIEW_ALT_AT_NM, kernel=kernel, hidden=True))
    doc.sheets.append(sheet_to_l1(sheet))
    return str(sheet.uuid), str(view_uuid or ""), str(view_alt_uuid or "")


def _add_assembly(doc: Document) -> tuple[tuple[str, ...], str]:
    """两个零部件 + 一条配合（同轴），返回 `(component_uuids, mate_uuid)`。"""
    refs = {ASSEMBLY_REF_NAME: {"point_nm": [0, 0, 0], "dir": [0.0, 0.0, 1.0]}}
    size_nm = (PLATE_LENGTH_NM, PLATE_WIDTH_NM, PLATE_THICKNESS_NM)
    base = sw_assembly.insert_component(doc, part_key=PART_BASE, at_nm=(0, 0, 0),
                                        size_nm=size_nm, refs=refs)[0]
    top = sw_assembly.insert_component(doc, part_key=PART_TOP,
                                       at_nm=(0, 0, PLATE_THICKNESS_NM),
                                       size_nm=size_nm, refs=refs)[0]
    mate = sw_assembly.mate_add(doc, kind=MATE_KIND,
                                refs=[[base, ASSEMBLY_REF_NAME], [top, ASSEMBLY_REF_NAME]])[0]
    return (str(base), str(top)), str(mate)


def _label_alignment(doc: Document, mate: str, context: dict[str, Any]) -> str:
    """把配合标上对齐名（`alignment_unlabelled` 的对侧）。

    入口若不在本仓公开面里（`mate_align` 缺席）⇒ 如实记 `absent`，**不手搓记录冒充**。
    """
    entry = getattr(sw_assembly, "mate_align", None)
    if not callable(entry):
        context["assembly.alignment"] = "absent:sw_assembly.mate_align"
        return ""
    rows = _step(context, "assembly.alignment",
                 lambda: entry(doc, mates=[mate], alignment=ALIGNMENT_NAME))
    return ALIGNMENT_NAME if rows else ""


def _configure_drafting(doc: Document, context: dict[str, Any]) -> dict[str, Any]:
    """栅格 + 捕捉**已配置**（`grid_not_configured` 的对侧），全部走 `precision_ops` 公开写入器。"""
    _step(context, "precision.grid", lambda: precision_ops.grid_set(
        doc, enabled=True, spacing_nm=GRID_SPACING_NM, snap_enabled=True,
        major_every=GRID_MAJOR_EVERY))
    _step(context, "precision.snap_enable", lambda: precision_ops.snap_enable(doc, enabled=True))
    _step(context, "precision.snap_kinds",
          lambda: precision_ops.snap_kinds_set(doc, kinds=list(SNAP_KINDS)))
    _step(context, "precision.snap_tolerance",
          lambda: precision_ops.snap_tolerance_set(doc, tolerance_nm=SNAP_TOLERANCE_NM))
    return {"grid_spacing_nm": GRID_SPACING_NM, "snap_kinds": tuple(SNAP_KINDS),
            "snap_tolerance_nm": SNAP_TOLERANCE_NM}


def _configure_layers(doc: Document, plate_lines: list[str], hole_circle: str,
                      context: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """受保护层 `"0"` + 两个**带特性**的普通层（线宽/线型/打印），并挂实体。

    受保护层 `"0"` 由 `new_document()` **预置** ⇒ 旧口径的 `layer_create(name="0")` 必抛
    `duplicate_name`，夹具于是把这一步记成构造失败、连该层 uuid 都没登记（`layer_uuids[0] == ""`
    ⇒ `_FIXTURE_UUIDS["layer"]` 缺席 ⇒ 取值器按名字取"合法层"时只能回落）。这里改成"先试建、
    已存在就取现成行"：上下文一样到手（层仍是那个预先存在的受保护层），**判据一字不改**。
    """
    default = ""
    try:
        default = str(organization.layer_create(doc, name=LAYER_DEFAULT, color=7)[0])
    except CadError:
        rows = [row for row in doc.extra.get("organization", {}).get("layers", [])
                if isinstance(row, dict) and str(row.get("name")) == LAYER_DEFAULT]
        default = str(rows[0].get("uuid") or "") if rows else ""
    context["layer.default"] = "ok" if default else "FAIL:layer_default_unavailable"
    props = _step(context, "layer.default_props", lambda: organization.layer_set(
        doc, layer=LAYER_DEFAULT, lineweight_nm=250_000, printable=True, visible=True,
        locked=False))
    plate = organization.layer_create(doc, name=LAYER_PLATE, color="#FF0000",
                                      linetype="CONTINUOUS", lineweight_nm=350_000,
                                      printable=True)[0]
    holes = organization.layer_create(doc, name=LAYER_HOLES, color=3, linetype="DASHED",
                                      lineweight_nm=180_000, printable=False)[0]
    _assign_layer(doc, refs=plate_lines, layer_uuid=plate, layer_name=LAYER_PLATE)
    _assign_layer(doc, refs=[hole_circle], layer_uuid=holes, layer_name=LAYER_HOLES)
    _step(context, "layer.current", lambda: organization.current_layer(doc, name=LAYER_PLATE))
    rows = [row for row in doc.extra["organization"]["layers"] if isinstance(row, dict)]
    entity_props = next((dict(row) for row in rows if row.get("name") == LAYER_PLATE), {})
    return [str(default or ""), str(plate), str(holes)], entity_props


def _add_annotation(doc: Document, plate_lines: list[str], context: dict[str, Any]) -> str:
    """尺寸标注（`dim_*` 那类 op 要的 annotation uuid），走 `cadcore.ops.annotation` 公开写入器。

    `add_dimension(target=...)` 要的是**一对** `(uuid, sub_shape_name)`：`annotation._ref` 只在
    "不是 pair"时抛 `bad_reference`。旧口径多套了一层 list ⇒ 夹具自称给得出尺寸注记、实际造不出来
    （`context_report` 记 `FAIL:bad_reference`，`_FIXTURE_CONTEXT["dimension"]` 恒空）。
    """
    if not plate_lines:
        context["annotation.dimension"] = "skipped:no_line"
        return ""
    rows = _step(context, "annotation.dimension", lambda: annotation_ops.add_dimension(
        doc, target=(plate_lines[0], "start"), kind="linear", value_nm=DIMENSION_VALUE_NM))
    return str(rows[0]) if rows else ""


# --------------------------------------------------------------------------------------
# 唯一入口
# --------------------------------------------------------------------------------------


def build_fixture() -> L1Fixture:
    """构造完整 L1 文档 + 上下文扩容（每次调用都新建一份；纯内存、不写盘、不改全局状态）。"""
    context: dict[str, Any] = {}
    kernel = nano_kernel()
    doc = new_document(unit=DOC_UNIT, standard_profile=DOC_STANDARD)
    doc.meta["doc_uuid"] = STABLE_DOC_UUID
    doc.meta["created_at"] = FIXTURE_CREATED_AT
    doc.meta["title_key"] = DOC_TITLE_KEY

    plate_sketch = _new_sketch_payload(sketch_uuid=PLATE_SKETCH_UUID)
    plate_lines = _add_rectangular_profile(plate_sketch, layer=LAYER_PLATE)
    hole_sketch = _new_sketch_payload(sketch_uuid=HOLE_SKETCH_UUID)
    hole_circle = sketch_commands.add_circle(hole_sketch, center=(0, 0), radius_nm=HOLE_RADIUS_NM,
                                             layer=LAYER_HOLES)
    doc.sketches.extend([plate_sketch, hole_sketch])

    doc.features.append(_extrude_feature(feature_uuid=EXTRUDE_FEATURE_UUID,
                                         sketch_uuid=PLATE_SKETCH_UUID))

    layer_uuids, entity_properties = _configure_layers(doc, plate_lines, hole_circle, context)

    block = organization.block_define(doc, name=BLOCK_NAME, refs=[hole_circle],
                                      base_nm=(0, 0))[0]
    insert = organization.block_insert(doc, block=block, at_nm=BLOCK_INSERT_AT_NM,
                                       rotation_deg=0.0, sketch=HOLE_SKETCH_UUID)[0]
    xref = organization.xref_attach(doc, name=XREF_NAME, path=XREF_PATH, at_nm=(0, 0))[0]

    drawing = _configure_drafting(doc, context)
    dimension_uuid = _add_annotation(doc, plate_lines, context)

    components, mate = _add_assembly(doc)
    alignment_name = _label_alignment(doc, mate, context)

    shape = kernel.extrude(plate_sketch, distance_nm=PLATE_THICKNESS_NM, symmetric=False)
    sheet_uuid, view_uuid, view_alt_uuid = _add_sheet(doc, kernel=kernel, shape=shape,
                                                      context=context)

    return L1Fixture(
        doc=doc,
        kernel=kernel,
        plate_sketch_uuid=PLATE_SKETCH_UUID,
        hole_sketch_uuid=HOLE_SKETCH_UUID,
        plate_line_uuids=tuple(plate_lines),
        hole_circle_uuid=str(hole_circle),
        feature_uuid=EXTRUDE_FEATURE_UUID,
        sheet_uuid=sheet_uuid,
        view_uuid=view_uuid,
        view_alt_uuid=view_alt_uuid,
        dimension_uuid=dimension_uuid,
        component_uuids=components,
        mate_uuid=mate,
        layer_uuids=tuple(layer_uuids),
        block_uuid=str(block),
        insert_uuid=str(insert),
        xref_uuid=str(xref),
        protected_layer=LAYER_DEFAULT,
        grid_spacing_nm=int(drawing["grid_spacing_nm"]),
        snap_kinds=tuple(drawing["snap_kinds"]),
        snap_tolerance_nm=int(drawing["snap_tolerance_nm"]),
        alignment_name=alignment_name,
        entity_properties=entity_properties,
        context_report=context,
    )


def build_document() -> Document:
    """只要文档（下游 op 探针的入参就是它）。"""
    return build_fixture().doc


def main(argv: list[str] | None = None) -> int:
    """打印一行判定行：构造 + 重建**都真跑**，数字全部现算。"""
    fixture = build_fixture()
    doc = fixture.doc
    store = fixture.organization_store
    assembly = fixture.assembly_store
    rebuild = recompute(doc, fixture.kernel)
    mass = rebuild.shape_refs[0].get("mass") if rebuild.shape_refs else None
    volume = next((value for key, value in (mass or {}).items()
                   if "volume" in str(key).lower() and isinstance(value, (int, float))), None)
    view_count = sum(len(entry.get("views") or []) for entry in doc.sheets)
    contexts_ok = sum(1 for value in fixture.context_report.values() if value == "ok")
    contexts_absent = sum(1 for value in fixture.context_report.values()
                          if str(value).startswith(("absent:", "skipped:")))
    contexts_fail = len(fixture.context_report) - contexts_ok - contexts_absent
    # 逐类上下文**把账摊开**（造到什么程度、哪一步失败，读的人不用猜；判定行仍押在最后一行）。
    for label, value in sorted(fixture.context_report.items()):
        print("L1_FIXTURE_CONTEXT %s=%s" % (label, value))
    print("L1_FIXTURE verdict=%s doc_uuid=%s sketches=%d lines=%d constraints=%d features=%d "
          "rebuild_ok=%s shape_refs=%d volume_nm3=%s sheets=%d views=%d components=%d mates=%d "
          "layers=%d blocks=%d inserts=%d xrefs=%d dimensions=%d grid_spacing_nm=%d snap_kinds=%d "
          "alignment=%s labelled_mate=%s contexts_ok=%d contexts_absent=%d contexts_fail=%d"
          % ("PASS" if (rebuild.ok and view_count and len(assembly["mates"]) == 1) else "FAIL",
             doc.meta.get("doc_uuid"), len(doc.sketches), len(fixture.plate_line_uuids),
             len(sketch_constraints.sketch_constraints(fixture.plate_sketch)),
             len(doc.features), rebuild.ok, len(rebuild.shape_refs),
             "NA" if volume is None else int(volume), len(doc.sheets), view_count,
             len(assembly["components"]), len(assembly["mates"]),
             len(store["layers"]), len(store["blocks"]), len(store["inserts"]),
             len(store["xrefs"]), 1 if fixture.dimension_uuid else 0,
             fixture.grid_spacing_nm, len(fixture.snap_kinds),
             fixture.alignment_name or "NA",
             "yes" if fixture.labelled_mate_uuid else "no",
             contexts_ok, contexts_absent, contexts_fail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
