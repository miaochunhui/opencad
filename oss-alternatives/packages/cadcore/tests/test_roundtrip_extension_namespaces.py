"""test_roundtrip_extension_namespaces —— B6 缺陷 RC4 回归：**读侧必须认识写侧写出的每一种记录形态**。

被测缺陷（G26 往返门 `tools/cad/roundtrip_gate.py --check` 抓出来的两条红 + 同类的其余形态）
--------------------------------------------------------------------------------------------
`docmodel` 的读侧审计把**本仓写侧自己写出来的扩展键**当成"未知外来字段"，于是
`save → open → 再 save` 凭空多出 `unknown_field_preserved` 诊断：L1 文本不再逐字节稳定
（`bytes_equal=0`），伪诊断还被写回文件里越积越多。门抓到的两个形态是
`layers_table`（`organization` 图层表 + 指派 + `current_layer`）与
`block_insert`（`organization.block_define` + `block_insert`），根因是同一个：
`docmodel._NODE_FIELDS` 只列 L1 冻结字段，没列各族模块写在 `extra` / 草图顶层 / `meta` / `sheet`
上的扩展键。这是 RC3（`geom.points`）的同类缺陷 —— 那一轮修的是 polyline 那条通路。

判据（每条断言改坏立刻报红）
--------------------------
1. **生产者 ↔ 登记表双向自证**：`docmodel.PRODUCT_EXTENSION_KEYS` 里每一条都必须有 `PRODUCERS`
   里的**真生产者**（真跑公开 API、真写出该键），反向 `PRODUCERS` 也不许有多余条目 ——
   两个方向一起才排掉"幽灵条目"与"漏登记"。
2. **往返零伪诊断 + 逐字节稳定**：每条生产者的文档 `to_l1 → from_l1 → to_l1` 必须逐字节相同，
   且 `from_l1` 后不得新增任何 `unknown_field_preserved`（改前红：`$.organization` /
   `$.current_layer` / `$.output` / `$.sketches[0].precision` / `$.meta.precision_grid` /
   `$.sheets[0].kind` …）。
3. **阴性对照（尺子看得见红）**：真未知字段在 root / sketch / meta / sheet 四个节点上都必须
   照旧报诊断；登记键的**形近拼错**（`organization_typo`）也必须照旧报 —— 登记表是精确匹配，
   不是前缀/通配豁免。`x_` 厂商前缀仍按既有口径豁免。
4. **数据不丢**：`root` 一档的扩展键与 `_KNOWN_TOP_LEVEL` 必须不相交（否则 `to_payload()`
   会把它们当冻结字段过滤掉 ⇒ 静默丢数据），且往返后表内容原样还在。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .                    # 契约命令
    python3 -m unittest tests.test_roundtrip_extension_namespaces -v

Standalone --source-package selects a read-only dependency; discovery keeps
the containing package and never silently falls back to a different tree.
"""

from __future__ import annotations

import argparse
import json
import importlib
import os
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from dataclasses import dataclass
from typing import Any, Callable

if __name__ == "__main__":
    source_parser = argparse.ArgumentParser(add_help=False)
    source_parser.add_argument("--source-package", type=Path,
                               default=Path(__file__).resolve().parents[1])
    source_args, unittest_args = source_parser.parse_known_args()
    source_package = source_args.source_package.resolve()
    sys.path.insert(0, str(source_package))

from cadcore import docmodel
from cadcore.ops import (draw_ops_extra, edit_ops, hatch_measure, organization, output,
                         output_extra, precision_ops, sw_assembly, sw_pattern_refgeom)
from cadcore.ops import annotation
from cadcore.ops import sw_features
from cadcore.ops.features import precision_ops__w26 as w26
from cadcore.ops.features import precision_ops__w28 as w28
from cadcore.sketch import commands, session as sketch_session

if __name__ == "__main__":
    for module in (docmodel, commands, sw_features):
        if not Path(module.__file__).resolve().is_relative_to(source_package):
            raise AssertionError("namespace tests imported the wrong package: " + module.__file__)

_FEATURE_KERNEL = None
_FEATURE_ROOT = None
_STEP_TEXT = None


def _feature_kernel():
    global _FEATURE_KERNEL, _FEATURE_ROOT
    if _FEATURE_KERNEL is None:
        from cadcore.kernel.subproc import SubprocessKernel

        root = Path(os.environ.get(
            "CADCORE_NAMESPACE_EVIDENCE",
            "/hf3fs-jd/data/tmp/opencad-features-20261010/seat-replay-review/namespaces",
        )) / str(uuid.uuid4())
        root.mkdir(parents=True, exist_ok=False)
        _FEATURE_ROOT = root
        executable = shutil.which("freecadcmd")
        if executable is None:
            raise AssertionError("real FreeCAD required: freecadcmd is not on PATH")
        _FEATURE_KERNEL = SubprocessKernel(
            executable=executable, work_dir=root, keep_work_dir=True,
            extra_env={"QT_QPA_PLATFORM": "offscreen"})
    return _FEATURE_KERNEL


def tearDownModule():
    global _FEATURE_KERNEL
    if _FEATURE_KERNEL is not None:
        _FEATURE_KERNEL.close()
        _FEATURE_KERNEL = None

#: 图层名（自由字符串，故意用中文 + 大写各一处：中文层名是 L1 冻结字段口径的一部分）。
PLATE = "PLATE"
CJK = "轮廓层"


def new_doc() -> Any:
    return docmodel.new_document(unit="mm", standard_profile="gbt", projection_angle="first")


def new_sketch(uuid: str = "b6000000-0000-4000-8000-0000000000a1") -> dict:
    return {"uuid": uuid, "plane": "XY", "entities": [], "constraints": [], "dimensions": []}


def new_pair(*, closed: bool = False):
    """一条实体 + 一个草图的最小文档（`closed=True` 画圆，供需要闭合边界的填充用）。"""
    doc = new_doc()
    sketch = new_sketch()
    doc.sketches.append(sketch)
    if closed:
        commands.add_circle(sketch, center=(0, 0), radius_nm=5_000_000)
    else:
        commands.add_line(sketch, p1=(0, 0), p2=(10_000_000, 0))
    return doc, sketch


# ======================================================================================
# 生产者探针：每条 = 一个登记键 + 一个**真跑公开 API** 的构造器
# ======================================================================================


@dataclass(frozen=True)
class Producer:
    """一条"写侧真写得出这个键"的探针。`build(doc, sketch)` 必须在文档里写出 `key`。

    `l1=True` = 该族只认 **L1 映射**形态的文档（`ops/annotation.py` 的样式族就是这种：产品主类型
    `Document` 上没有 `dim_styles` 槽位，`_doc_bucket` 对对象文档报 `bad_document`，这是**登记的
    产品行为**而不是待修缺陷）。这类探针在跑之前把 `Document` 转成 L1 载荷再调用 —— 探针量的是
    "写侧发得出这个键"，不是"这个键在哪种文档形态上可用"。
    """

    kind: str                     # 节点类型：root / sketch / meta / sheet
    key: str                      # `PRODUCT_EXTENSION_KEYS[kind]` 里的键
    build: Callable[[Any, Any], Any]
    note: str = ""
    l1: bool = False


def _as_l1(doc: Any, sketch: Any) -> tuple[Any, Any]:
    """`Producer.l1` 用的文档形态转换：`Document` → L1 载荷（同一个草图的映射）。"""
    payload = json.loads(docmodel.document_to_l1(doc))
    return payload, payload["sketches"][0]


def _style_tables(doc: Any, sketch: Any) -> None:
    """标注样式族六张表一次写全（同一个文档上真跑六个公开 op）。"""
    line = sketch["entities"][0]["uuid"]
    dim = annotation.add_dimension(doc, target=[line, "mid"], kind="linear",
                                   value_nm=10_000_000)[0]
    annotation.dim_style(doc, name="S1", height_nm=9_000_000, decimals=6)
    annotation.dim_override(doc, subject=dim, override_text="OVR-1")
    annotation.dim_edit(doc, subject=dim, override_text="EDIT-1")
    annotation.add_text(doc, at_nm=(0, 0), literal=True, text="NOTE-1")
    annotation.text_style(doc, name="T1")
    annotation.mleader_style(doc, name="M1")
    annotation.add_leader(doc, target=[line, "mid"], at_nm=(0, 0))
    annotation.table_style(doc, name="TB1")


def _layers_and_current_layer(doc: Any, sketch: Any) -> None:
    """G26 `layers_table` 形态：图层表 + 指派 + 当前层。"""
    first = commands.add_line(sketch, p1=(0, 0), p2=(20_000_000, 0), layer="0")
    commands.add_circle(sketch, center=(0, 0), radius_nm=5_000_000, layer="0")
    plate = organization.layer_create(doc, name=PLATE, color="#FF0000")[0]
    holes = organization.layer_create(doc, name=CJK, color=3, linetype="DASHED")[0]
    organization.layer_assign(doc, refs=[first], layer=plate)
    organization.layer_assign(doc, refs=[sketch["entities"][1]["uuid"]], layer=holes)
    organization.current_layer(doc, name=PLATE)


def _block_insert(doc: Any, sketch: Any) -> None:
    """G26 `block_insert` 形态：块定义 + 插入（插入即落几何）。"""
    block = organization.block_define(doc, name="HOLE", refs=[sketch["entities"][0]["uuid"]],
                                      base_nm=(0, 0))[0]
    organization.block_insert(doc, block=block, at_nm=(25_000_000, 0), rotation_deg=0.0,
                              sketch=sketch["uuid"])


def _history_change(doc: Any, sketch: Any) -> None:
    """撤销栈：先建层再改实体层（**真变化**才记账；没变化不写栈）。"""
    layer = organization.layer_create(doc, name="HIST")[0]
    edit_ops.properties_set(doc, refs=[sketch["entities"][0]["uuid"]], layer=layer)

def _solid_feature(doc: Any, sketch: Any) -> None:
    sw_features.extrude_boss(doc, sketch=sketch["uuid"], distance_nm=6_000_000)


def _edited_feature(doc: Any, sketch: Any) -> None:
    feature = sw_features.extrude_boss(doc, sketch=sketch["uuid"], distance_nm=6_000_000)[0]
    sw_features.edit_feature(doc, feature_uuid=feature, distance_nm=12_000_000,
                             rename="BODY", suppress=True, reorder_to=0)


def _extension_feature(doc: Any, sketch: Any, kind: str) -> dict:
    """Probe module producers; document params belong to app solid_commands.

    Kernel constructors emit replay recipes, not UUID-aware document features.
    This package-local probe mirrors the app's explicit recipe-to-params mapping
    without importing an app outside the package. Every geometry value comes
    from make_step; sketch refs come from real public sketch producers.
    """
    def module(name):
        return importlib.import_module("cadcore.kernel.features_" + name)

    sketch["entities"].clear()
    commands.add_circle(sketch, center=(0, 0), radius_nm=2_000_000)
    kernel = _feature_kernel()
    profile = kernel.sketch_to_wire(sketch).to_dict()
    refs = []
    if kind == "sweep":
        step = module("sweep").make_step(
            profile=profile, path_points_nm=[[0, 0, 0], [0, 0, 6_000_000]])
        params = {key: value for key, value in step.items() if key not in ("op", "profile")}
        params["sketch_uuid"] = sketch["uuid"]
        refs = [sketch["uuid"]]
    elif kind == "loft":
        other = new_sketch(str(uuid.uuid4()))
        commands.add_circle(other, center=(0, 0), radius_nm=3_000_000)
        doc.sketches.append(other)
        step = module("loft").make_step(
            profiles=[profile, kernel.sketch_to_wire(other).to_dict()],
            offsets_nm=[[0, 0, 0], [0, 0, 6_000_000]], ruled=True)
        params = {key: value for key, value in step.items() if key not in ("op", "profiles")}
        refs = [sketch["uuid"], other["uuid"]]
        params["profile_uuids"] = refs
    elif kind in ("fillet", "chamfer"):
        step = module("edges").make_step(kind=kind, size_nm=500_000, edge_indices=[1])
        params = {key: value for key, value in step.items() if key != "op"}
    elif kind == "shell":
        step = module("shell").make_step(thickness_nm=500_000, face_indices=[1], inward=True)
        params = {key: value for key, value in step.items() if key != "op"}
    elif kind == "pattern":
        step = module("pattern").make_pattern_step(
            translations_nm=[[0, 0, 0], [20_000_000, 0, 0]], fuse=False)
        params = {key: value for key, value in step.items() if key != "op"}
    elif kind == "mirror":
        step = module("pattern").make_mirror_step(
            normal_axis="x", offset_nm=0, keep_original=False)
        params = {key: value for key, value in step.items() if key != "op"}
    elif kind == "import_step":
        global _STEP_TEXT
        if _STEP_TEXT is None:
            solid = kernel.extrude(kernel.sketch_to_wire(sketch), distance_nm=6_000_000)
            properties = kernel.mass_properties(solid)
            if properties.source != "external" or properties.volume_nm3 <= 0:
                raise AssertionError("STEP producer requires a real positive-volume solid")
            target = _FEATURE_ROOT / "namespace-source.step"
            kernel.export(solid, fmt="step", path=str(target))
            _STEP_TEXT = target.read_text(encoding="utf-8")
        step = module("step").make_step(step_text=_STEP_TEXT)
        params = {key: value for key, value in step.items() if key != "op"}
    else:
        raise AssertionError("unknown extension producer: " + kind)
    feature = {"uuid": str(uuid.uuid4()), "kind": kind, "inputs": refs,
               "params": params, "suppressed": False}
    if kind == "sweep":
        feature["sketch_uuid"] = sketch["uuid"]
    doc.features.append(feature)
    return step


def _extension_producer(kind):
    return lambda doc, sketch: _extension_feature(doc, sketch, kind)


PRODUCERS: tuple[Producer, ...] = (
    # ── 文档根（`Document.extra[...]`）───────────────────────────────────────────────
    Producer("root", "organization", _layers_and_current_layer, "G26 layers_table 的图层表"),
    Producer("root", "current_layer", _layers_and_current_layer, "G26 layers_table 的当前层"),
    Producer("root", "organization", _block_insert, "G26 block_insert 的块表/插入记录"),
    Producer("root", "output", lambda doc, sk: output.plot(doc), "出图作业账"),
    Producer("root", "output_extra",
             lambda doc, sk: output_extra.style_table_create(doc, name="mono.ctb"), "样式表账"),
    Producer("root", "sw_assembly",
             lambda doc, sk: sw_assembly.insert_component(doc, part_key="PART-1"), "装配表"),
    Producer("root", "precision",
             lambda doc, sk: precision_ops.grid_set(doc, enabled=True), "精度模式表"),
    Producer("root", "precision_polar_snap",
             lambda doc, sk: (w26.polar_snap_step_set(doc, step_nm=5_000_000),
                              w26.polar_snap(doc, base_nm=(0, 0), point_nm=(10_000_000, 100_000))),
             "极轴捕捉结果表"),
    Producer("root", "coord_input",
             lambda doc, sk: w28.coordinate_input(doc, text="10,20"), "坐标输入历史"),
    Producer("root", "edit_ops_history", _history_change, "撤销栈（文档形态）"),
    # 标注样式族六张表（`ops/annotation.py`；只认 L1 映射形态 ⇒ `l1=True`）
    Producer("root", "dim_styles", _style_tables, "DIMSTYLE 样式定义表", l1=True),
    Producer("root", "dim_overrides", _style_tables, "DIMOVERRIDE 样式替代表", l1=True),
    Producer("root", "dim_edits", _style_tables, "DIMEDIT 文字编辑表", l1=True),
    Producer("root", "text_styles", _style_tables, "STYLE 文字样式表", l1=True),
    Producer("root", "mleader_styles", _style_tables, "MLEADERSTYLE 引线样式表", l1=True),
    Producer("root", "table_styles", _style_tables, "TABLESTYLE 表格样式表", l1=True),
    # ── 草图节点（`sk[...]`）───────────────────────────────────────────────────────
    Producer("sketch", "extra",
             lambda doc, sk: draw_ops_extra.ortho_polar_set(sk, mode="ortho"),
             "草图级扩展容器（`sk[\"extra\"][<ns>]`）"),
    Producer("sketch", "organization",
             lambda doc, sk: organization.layer_create(sk, name="SKPLATE"), "草图形态图层表"),
    Producer("sketch", "current_layer",
             lambda doc, sk: (organization.layer_create(sk, name="SKPLATE"),
                              organization.current_layer(sk, name="SKPLATE")),
             "草图形态当前层"),
    Producer("sketch", "precision",
             lambda doc, sk: precision_ops.grid_set(sk, enabled=True), "草图形态精度表"),
    Producer("sketch", "precision_grid",
             lambda doc, sk: precision_ops.grid_set(sk, enabled=True), "草图形态栅格记录"),
    Producer("sketch", "precision_polar_snap",
             lambda doc, sk: (w26.polar_snap_step_set(sk, step_nm=5_000_000),
                              w26.polar_snap(sk, base_nm=(0, 0), point_nm=(10_000_000, 100_000))),
             "草图形态极轴捕捉表"),
    Producer("sketch", "hatch_measure",
             lambda doc, sk: hatch_measure.hatch_create(sk), "填充表（需闭合边界）"),
    Producer("sketch", "sw_pattern_refgeom",
             lambda doc, sk: sw_pattern_refgeom.datum_plane(sk, offset_nm=10_000_000), "基准面表"),
    Producer("sketch", "edit_ops_history",
             lambda doc, sk: edit_ops.properties_set(
                 sk, refs=[sk["entities"][0]["uuid"]], layer="HIST"),
             "撤销栈（草图形态）"),
    # ── `meta` / `sheet` 节点上的扩展键 ────────────────────────────────────────────
    Producer("meta", "precision_grid",
             lambda doc, sk: precision_ops.grid_set(doc, enabled=True), "栅格显示记录（公开契约）"),
    Producer("meta", "sketch_session",
             lambda doc, sk: sketch_session.enter_sketch(doc, sketch=sk, plane="XZ"),
             "草图会话状态（当前草图 + 基准面）"),
    Producer("sheet", "name", lambda doc, sk: output.layout_create(doc, name="L1"), "布局记录"),
    Producer("sheet", "kind", lambda doc, sk: output.layout_create(doc, name="L1"), "布局记录"),
    Producer("sheet", "active", lambda doc, sk: output.layout_create(doc, name="L1"), "布局记录"),
    Producer("sheet", "tilemode", lambda doc, sk: output.layout_create(doc, name="L1"), "布局记录"),
    Producer("feature", "sketch_uuid", _solid_feature),
    Producer("params", "end_condition", _solid_feature),
    Producer("params", "merge", _solid_feature),
    Producer("params", "profile_min_dim_nm", _solid_feature),
    Producer("params", "edited", _edited_feature),
    Producer("params", "edit_count", _edited_feature),
    Producer("params", "edit_log", _edited_feature),
    Producer("params", "name", _edited_feature),
    Producer("params", "suppressed", _edited_feature),
    Producer("params", "reorder_to", _edited_feature),
    Producer("params", "sketch_uuid", _extension_producer("sweep"), "sweep make_step + live UUID"),
    Producer("params", "path_points_nm", _extension_producer("sweep"), "sweep make_step"),
    Producer("params", "profile_uuids", _extension_producer("loft"), "loft make_step + live UUIDs"),
    Producer("params", "offsets_nm", _extension_producer("loft"), "loft make_step"),
    Producer("params", "ruled", _extension_producer("loft"), "loft make_step"),
    Producer("params", "size_nm", _extension_producer("fillet"), "edge make_step"),
    Producer("params", "edge_indices", _extension_producer("chamfer"), "edge make_step"),
    Producer("params", "thickness_nm", _extension_producer("shell"), "shell make_step"),
    Producer("params", "face_indices", _extension_producer("shell"), "shell make_step"),
    Producer("params", "inward", _extension_producer("shell"), "shell make_step"),
    Producer("params", "translations_nm", _extension_producer("pattern"), "pattern make_pattern_step"),
    Producer("params", "fuse", _extension_producer("pattern"), "pattern make_pattern_step"),
    Producer("params", "normal_axis", _extension_producer("mirror"), "mirror make_mirror_step"),
    Producer("params", "offset_nm", _extension_producer("mirror"), "mirror make_mirror_step"),
    Producer("params", "keep_original", _extension_producer("mirror"), "mirror make_mirror_step"),
    Producer("params", "step_text", _extension_producer("import_step"), "real STEP export + make_step"),
)

#: 需要闭合边界的生产者（圆）：避免"夹具画了条线，填充建不出来"这种假红。
_CLOSED_ONLY = frozenset({"hatch_measure"})


def node_keys(payload: dict, kind: str) -> set[str]:
    """取某节点类型在载荷里的键集合（`root`/`sketch`/`meta`/`sheet` 各自的读法）。"""
    if kind == "root":
        return set(payload)
    if kind == "sketch":
        return set(payload["sketches"][0])
    if kind == "meta":
        return set(payload["meta"])
    if kind == "sheet":
        return set(payload["sheets"][0])
    if kind == "feature":
        return set(payload["features"][0])
    if kind == "params":
        return set(payload["features"][0]["params"])
    raise AssertionError("unknown node kind: %r" % (kind,))


def new_diags(text: str) -> list[dict]:
    """`from_l1` 后新增的 `unknown_field_preserved` 诊断（改前红的那一类）。"""
    again = docmodel.document_from_l1(text)
    return [item for item in (again.diagnostics or [])
            if item.get("code") == "unknown_field_preserved"]


class ProducerRegistryTests(unittest.TestCase):
    """判据 1：登记表与真生产者**双向**对齐（少一条、多一条都报红）。"""

    def test_every_registry_entry_has_a_producer(self) -> None:
        for kind, keys in docmodel.PRODUCT_EXTENSION_KEYS.items():
            probed = {p.key for p in PRODUCERS if p.kind == kind}
            self.assertEqual(probed, set(keys),
                             "登记表与生产者探针不一致（kind=%s）：缺探针=%s 多探针=%s"
                             % (kind, sorted(set(keys) - probed), sorted(probed - set(keys))))

    def test_root_extension_keys_are_disjoint_from_frozen_fields(self) -> None:
        overlap = docmodel.PRODUCT_EXTENSION_KEYS["root"] & docmodel._KNOWN_TOP_LEVEL
        self.assertEqual(overlap, frozenset(),
                         "根级扩展键不许进 _KNOWN_TOP_LEVEL（to_payload 会当冻结字段过滤 ⇒ 静默丢数据）")

    def test_extension_params_are_derived_from_module_recipes(self) -> None:
        for kind in ("sweep", "loft", "fillet", "chamfer", "shell", "pattern", "mirror", "import_step"):
            with self.subTest(kind=kind):
                doc, sketch = new_pair()
                step = _extension_feature(doc, sketch, kind)
                feature = doc.features[-1]
                self.assertEqual(step["op"], kind)
                omitted = {"op", "profile", "profiles"}
                generated = {key: value for key, value in step.items() if key not in omitted}
                persisted = {key: value for key, value in feature["params"].items()
                             if key not in ("sketch_uuid", "profile_uuids")}
                self.assertEqual(persisted, generated)
                if kind in ("sweep", "loft"):
                    self.assertTrue(feature["inputs"])
                    live = {item["uuid"] for item in doc.sketches}
                    self.assertTrue(set(feature["inputs"]).issubset(live))
                    self.assertNotIn("profile", feature["params"])
                    self.assertNotIn("profiles", feature["params"])

    def test_producers_are_callable_on_the_real_api(self) -> None:
        """夹具自证：每条生产者真写得出它声明的键（否则本文件的绿是假绿）。"""
        for producer in PRODUCERS:
            with self.subTest(kind=producer.kind, key=producer.key, note=producer.note):
                doc, sketch = new_pair(closed=producer.key in _CLOSED_ONLY)
                if producer.l1:
                    doc, sketch = _as_l1(doc, sketch)
                producer.build(doc, sketch)
                payload = json.loads(docmodel.document_to_l1(doc))
                self.assertIn(producer.key, node_keys(payload, producer.kind),
                              "生产者没写出声明的键 %s.%s（%s）" % (producer.kind, producer.key,
                                                                   producer.note))


class RoundTripExtensionTests(unittest.TestCase):
    """判据 2 + 4：每条生产者往返零伪诊断、逐字节稳定、数据原样还在。"""

    def test_every_producer_roundtrips_clean_and_byte_stable(self) -> None:
        for producer in PRODUCERS:
            with self.subTest(kind=producer.kind, key=producer.key, note=producer.note):
                doc, sketch = new_pair(closed=producer.key in _CLOSED_ONLY)
                if producer.l1:
                    doc, sketch = _as_l1(doc, sketch)
                producer.build(doc, sketch)
                first = docmodel.document_to_l1(doc)
                self.assertEqual(new_diags(first), [],
                                 "自家写出的 %s.%s 不得触发 unknown_field_preserved（%s）"
                                 % (producer.kind, producer.key, producer.note))
                again = docmodel.document_from_l1(first)
                second = docmodel.document_to_l1(again)
                self.assertEqual(second, first,
                                 "含 %s.%s 的 L1 文本往返后必须逐字节相同" % (producer.kind,
                                                                             producer.key))

    def test_extension_tables_survive_the_round_trip(self) -> None:
        """判据 4：扩展键是**数据**不是噪声 —— 往返后表内容逐字段还在。"""
        doc, sketch = new_pair()
        _layers_and_current_layer(doc, sketch)
        rows_before = json.loads(docmodel.document_to_l1(doc))["organization"]["layers"]
        again = docmodel.document_from_l1(docmodel.document_to_l1(doc))
        store = again.extra.get("organization")
        self.assertIsInstance(store, dict, "组织表必须活在 doc.extra['organization']")
        self.assertEqual(store.get("layers"), rows_before, "图层行往返后必须逐字段不变")
        self.assertEqual(again.extra.get("current_layer"), PLATE, "当前层往返后必须不变")


class GateCaseRoundTripTests(unittest.TestCase):
    """G26 抓出来的两条红：修前 `diag_added>=1 / bytes_equal=0`，修后必须两条都绿。"""

    def test_layers_table_case(self) -> None:
        doc, sketch = new_pair()
        _layers_and_current_layer(doc, sketch)
        first = docmodel.document_to_l1(doc)
        payload = json.loads(first)
        self.assertEqual(len(payload["organization"]["layers"]), 3,
                         "夹具自证：预置层 0 + PLATE + 中文层，共 3 行")
        diags = new_diags(first)
        self.assertEqual([d.get("path") for d in diags], [],
                         "layers_table：open 后不许多出 $.organization / $.current_layer 伪诊断")
        again = docmodel.document_from_l1(first)
        self.assertEqual(docmodel.document_to_l1(again), first,
                         "layers_table：两次 save 必须逐字节相同（修前 bytes_equal=0）")

    def test_block_insert_case(self) -> None:
        doc, sketch = new_pair()
        _block_insert(doc, sketch)
        first = docmodel.document_to_l1(doc)
        payload = json.loads(first)
        store = payload["organization"]
        self.assertTrue(store["blocks"] and store["inserts"],
                        "夹具自证：块定义 + 插入记录都要在（否则测的不是 block_insert 形态）")
        self.assertEqual(len(payload["sketches"][0]["entities"]), 2,
                         "夹具自证：插入必须落几何（源圆 + 插入副本）")
        diags = new_diags(first)
        self.assertEqual([d.get("path") for d in diags], [],
                         "block_insert：open 后不许多出 $.organization 伪诊断")
        again = docmodel.document_from_l1(first)
        self.assertEqual(docmodel.document_to_l1(again), first,
                         "block_insert：两次 save 必须逐字节相同（修前 bytes_equal=0）")


class NegativeControlTests(unittest.TestCase):
    """判据 3：尺子必须看得见**真的**未知字段（否则上面那些绿全是假绿）。"""

    def _paths(self, doc: Any) -> list[str]:
        again = docmodel.document_from_l1(docmodel.document_to_l1(doc))
        return [str(item.get("path")) for item in (again.diagnostics or [])
                if item.get("code") == "unknown_field_preserved"]

    def test_unknown_root_field_is_still_reported(self) -> None:
        doc, _sketch = new_pair()
        doc.extra["legacy_camera"] = {"pan": [1, 2]}
        self.assertIn("$.legacy_camera", self._paths(doc))

    def test_unknown_sketch_field_is_still_reported(self) -> None:
        doc, sketch = new_pair()
        sketch["legacy_layer_note"] = "kept"
        self.assertIn("$.sketches[0].legacy_layer_note", self._paths(doc))

    def test_unknown_meta_and_sheet_fields_are_still_reported(self) -> None:
        doc, _sketch = new_pair()
        organization.layer_create(doc, name=PLATE)
        output.layout_create(doc, name="L1")
        doc.meta["legacy_meta_note"] = 1
        doc.sheets[0]["legacy_sheet_note"] = 2
        paths = self._paths(doc)
        self.assertIn("$.meta.legacy_meta_note", paths)
        self.assertIn("$.sheets[0].legacy_sheet_note", paths)

    def test_near_miss_key_is_not_whitelisted(self) -> None:
        """登记表是**精确匹配**：形近拼错（`organization_typo`）必须照旧报。"""
        doc, _sketch = new_pair()
        doc.extra["organization_typo"] = {"layers": []}
        doc.extra["not_current_layer"] = PLATE
        paths = self._paths(doc)
        self.assertIn("$.organization_typo", paths)
        self.assertIn("$.not_current_layer", paths)

    def test_vendor_prefix_stays_exempt(self) -> None:
        """既有口径不许被本次改动带跑：`x_` 前缀仍按厂商扩展豁免。"""
        doc, _sketch = new_pair()
        doc.extra["x_vendor_blob"] = {"any": ["shape"]}
        self.assertEqual(self._paths(doc), [])

    def test_unknown_feature_and_parameter_are_still_reported(self) -> None:
        doc, sketch = new_pair()
        _solid_feature(doc, sketch)
        doc.features[0]["sketch_uuid_typo"] = sketch["uuid"]
        doc.features[0]["params"]["merge_typo"] = True
        paths = self._paths(doc)
        self.assertIn("$.features[0].sketch_uuid_typo", paths)
        self.assertIn("$.features[0].params.merge_typo", paths)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *unittest_args], verbosity=2)
