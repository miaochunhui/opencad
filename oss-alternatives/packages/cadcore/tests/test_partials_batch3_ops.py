# SPDX-License-Identifier: Apache-2.0
"""test_partials_batch3_ops -- 第三批「partial → implemented」的静态 + 行为回归尺子（毫秒级）。

为什么要有这个文件
-----------------
端到端尺子是 `oss-alternatives/tools/cad/parity_partials_batch3_gate.py`（真 Document/真草图 + 8 个变异体），
族门是 `oss-alternatives/tools/cad/parity_organization_gate.py`（多模块装载 + 9 个变异体）。
本文件是同一批事实的**日常回归快尺子**：任何一条被改回去（op 从 OPERATIONS 消失 / 隔离片被搬回
`_quarantine/` / 真操作后果消失）当场报红，不用起门。

覆盖的四层
----------
1. 实现层：本批 8 个承载 op 在对应模块的 `OPERATIONS` 里且签名关键字齐全；
2. 落点层：三条隔离片已放回**主集合**（`ops/features/<stem>.py` 在位、`_quarantine/<stem>.py` 已不在）；
3. 行为层：`attrib_batch` 跨实例统改（两实例同改 + 与 `attrib_set` 同址 + 空选择负向）；
4. 独立契约：保留原 8 条 scope，以当前源码绑定和真实图层/参照/尺寸/引线/气球后果验收，
   不以 infra 历史状态台账当 oracle。

反向自证（阴性对照）：`test_registry_mutant_is_visible` 在内存里删掉一个 op 再跑同一个检查函数，
必须看得见红 —— 否则这四层断言只是"永远绿"。
"""

from __future__ import annotations

import sys
import inspect
import math
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
OSS = _THIS.parents[3]                     # tests -> cadcore -> packages -> oss-alternatives
PKG = OSS / "packages" / "cadcore"
FEATURES = PKG / "cadcore" / "ops" / "features"

if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

#: 承载 op -> 所在模块文件 stem（主集合文件）；签名关键字子集。
OPS = {
    "layer_unused": ("ops/features/organization__w26.py", ("include_reserved",)),
    "layer_purge": ("ops/features/organization__w26.py", ()),
    "layer_merge": ("ops/features/organization__w26.py", ("sources", "into", "create")),
    "attrib_batch": ("ops/organization.py", ("block", "tag", "value", "inserts")),
    "xref_path_set": ("ops/features/organization__w19.py", ("xref", "path", "base_dir", "mode", "name")),
    "xref_path_mode": ("ops/features/organization__w19.py", ("base_dir", "mode")),
    "xref_path_rebase": ("ops/features/organization__w19.py", ("old_base", "new_base")),
    "xref_path_audit": ("ops/features/organization__w19.py", ("base_dir", "root", "mode")),
    "dim_regen": ("ops/features/annotation__w22.py", ("target",)),
    "dim_reassociate": ("ops/features/annotation__w22.py", ("target", "reference")),
    "dim_regen_all": ("ops/features/annotation__w22.py", ()),
    "add_multileader": ("ops/features/annotation__w16.py", ("leaders", "text_key", "style", "target")),
    "tolerance_limits": ("ops/features/sw_drawings__w20.py", ("nominal_nm", "size_mm", "code", "role")),
    "fit_tolerance": ("ops/features/sw_drawings__w20.py", ("nominal_nm", "size_mm", "hole", "shaft")),
    "balloon": ("ops/features/sw_drawings__w15.py", ("sheet", "items", "view", "view_uuid", "radius_nm")),
}

#: 放回主集合的三条隔离片（stem）。
UNQUARANTINED = ("organization__w26", "annotation__w22", "annotation__w16")

#: Original scope coordinates, retained without reading historical verdicts.
LEDGER = (
    ("autocad-organization", 5, "无用图层清理、图层合并/删除"),
    ("autocad-organization", 10, "块属性编辑与批量改属性"),
    ("autocad-organization", 17, "参照路径与相对路径管理"),
    ("autocad-annotation", 0, "线性标注（水平/垂直/旋转）"),
    ("autocad-annotation", 11, "关联标注、重关联与更新"),
    ("autocad-annotation", 16, "多重引线"),
    ("sw-drawings", 8, "尺寸公差与配合"),
    ("sw-drawings", 14, "零件序号（气球）标注"),
)


def _load_ops(module_rel: str) -> dict:
    """顶层族模块走包导入（相对 import 才解析得到），功能片走本仓唯一切片装载器。"""
    parts = Path(module_rel).parts
    if len(parts) == 2 and parts[0] == "ops":  # 顶层族模块：包导入（相对 import 才解析得到）
        import importlib

        module = importlib.import_module("cadcore.ops." + Path(module_rel).stem)
        return dict(getattr(module, "OPERATIONS", {}) or {})
    from cadcore.ops import features as _features  # 功能片：本仓唯一切片装载器

    path = PKG / "cadcore" / module_rel
    return dict(getattr(_features.load_one(path, reload=True), "OPERATIONS", {}) or {})


def registry_problems() -> list[str]:
    """实现层检查（纯函数：变异体自证直接复用同一把尺子）。"""
    problems: list[str] = []
    for op, (module_rel, kwargs) in OPS.items():
        path = PKG / "cadcore" / module_rel
        if not path.is_file():
            problems.append(f"{op}: module {module_rel} missing")
            continue
        operations = _load_ops(module_rel)
        fn = operations.get(op)
        if fn is None:
            problems.append(f"{op}: not in OPERATIONS of {module_rel}")
            continue
        import inspect

        params = inspect.signature(fn).parameters
        missing = [key for key in kwargs if key not in params]
        if missing:
            problems.append(f"{op}: missing keyword(s) {missing}")
    return problems


class Batch3OpsTest(unittest.TestCase):
    def test_ops_registered_with_keywords(self):
        self.assertEqual(registry_problems(), [], "本批承载 op 的实现层回归")

    def test_quarantine_pieces_are_in_main_set(self):
        for stem in UNQUARANTINED:
            self.assertTrue((FEATURES / f"{stem}.py").is_file(), f"{stem} 不在主集合")
            self.assertFalse((FEATURES / "_quarantine" / f"{stem}.py").is_file(), f"{stem} 又被搬回隔离区")

    def test_attrib_batch_cross_instance(self):
        operations = _load_ops("ops/organization.py")
        sketch = {
            "entities": [
                {"uuid": "tb-line", "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 1000, "y2": 0}},
            ],
            "constraints": [], "dimensions": [],
        }
        block = operations["block_define"](sketch, name="BOLT")[0]
        operations["attdef_create"](sketch, block=block, tag="PART", default="X")
        first = operations["block_insert"](sketch, block=block, at_nm=(0, 0),
                                           attributes={"PART": "A1"})[0]
        second = operations["block_insert"](sketch, block=block, at_nm=(100, 0),
                                            attributes={"PART": "B1"})[0]
        changed = operations["attrib_batch"](sketch, block="bolt", tag="part", value="C1")
        self.assertEqual(len(changed), 2, "必须每个实例恰好一条 ATTRIB")
        for insert, attrib in ((first, changed[0]), (second, changed[1])):
            row = [item for item in sketch["organization"]["inserts"] if item["uuid"] == insert][0]
            values = [attrib_row["value"] for attrib_row in row["attributes"]
                      if attrib_row["tag"].casefold() == "part"]
            self.assertEqual(values, ["C1"], "批量改值必须落到每个实例")
            self.assertEqual(operations["attrib_set"](sketch, insert=insert, tag="PART", value="C1"),
                             [attrib], "批量写的就是 attrib_set 寻址的那条 ATTRIB")
        with self.assertRaises(Exception) as ctx:
            operations["attrib_batch"](sketch, block=block, tag="PART", value="x", inserts=[])
        self.assertEqual(getattr(ctx.exception, "reason", None), "empty_selection")

    def test_independent_contract_uses_real_operations_not_status_ledger(self):
        """The origin's implemented ledger is historical evidence, not an oracle."""
        self.assertEqual(len(LEDGER), 8, "original scope must not shrink")
        self.assertEqual(len(OPS), 15, "all original operation contracts remain")
        self.assertEqual(registry_problems(), [], "current source must implement the contract")
        for op, (module_rel, _keywords) in OPS.items():
            entry = _load_ops(module_rel)[op]
            self.assertTrue(callable(entry), op)
            self.assertEqual(Path(inspect.getsourcefile(entry)).resolve(),
                             (PKG / "cadcore" / module_rel).resolve())

    def operations(self, *modules):
        operations = {}
        for module in modules:
            operations.update(_load_ops(module))
        return operations

    def assert_layer_effects(self, operations):
        sketch = {"entities": [
            {"uuid": "scope-line", "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 0, "x2": 1000, "y2": 0}},
        ], "constraints": [], "dimensions": []}
        wall = operations["layer_create"](sketch, name="WALL")[0]
        unused = operations["layer_create"](sketch, name="TMP")[0]
        operations["layer_assign"](sketch, refs=["scope-line"], layer="WALL")
        geometry = dict(sketch["entities"][0]["geom"])
        self.assertEqual(operations["layer_unused"](sketch), [unused])
        self.assertEqual(operations["layer_merge"](sketch, sources=["wall"], into="SPARE", create=True),
                         [wall, "scope-line"])
        self.assertEqual(sketch["entities"][0]["layer"], "SPARE")
        self.assertEqual(sketch["entities"][0]["geom"], geometry)
        self.assertEqual(operations["layer_purge"](sketch), [unused])
        self.assertEqual(operations["layer_purge"](sketch), [])

    def test_layer_merge_and_purge_real_effects(self):
        self.assert_layer_effects(self.operations("ops/organization.py",
                                                 "ops/features/organization__w26.py"))

    def test_layer_noop_mutant_is_rejected(self):
        operations = self.operations("ops/organization.py", "ops/features/organization__w26.py")
        operations["layer_merge"] = lambda *_args, **_kwargs: []
        with self.assertRaises(AssertionError):
            self.assert_layer_effects(operations)

    def test_xref_path_management_real_effects(self):
        operations = self.operations("ops/organization.py", "ops/features/organization__w19.py")
        sketch = {"entities": [], "constraints": [], "dimensions": []}
        xref = operations["xref_attach"](sketch, name="BASE", path="refs/a.dxf")[0]
        self.assertEqual(operations["xref_path_set"](
            sketch, xref="base", path="refs/a.dxf", base_dir="/proj/dwg", mode="relative"), [xref])
        row = sketch["organization"]["xrefs"][0]
        self.assertEqual((row["path"], row["path_mode"], row["path_base"], row["path_abs"]),
                         ("refs/a.dxf", "relative", "/proj/dwg", "/proj/dwg/refs/a.dxf"))
        self.assertEqual(operations["xref_path_rebase"](
            sketch, old_base="/proj/dwg", new_base="/proj/dwg_v2"), [xref])
        self.assertEqual(row["path_abs"], "/proj/dwg/refs/a.dxf")
        self.assertEqual(row["path"], "../dwg/refs/a.dxf")
        self.assertEqual(operations["xref_path_mode"](
            sketch, base_dir="/proj/dwg_v2", mode="absolute"), [xref])
        self.assertEqual(row["path_mode"], "absolute")
        self.assertEqual(operations["xref_path_audit"](
            sketch, base_dir="/proj/dwg_v2", mode="absolute"), [])
        row["path_abs"] = "/proj/elsewhere/a.dxf"
        self.assertEqual(operations["xref_path_audit"](
            sketch, base_dir="/proj/dwg_v2", mode="absolute"), [xref])

    def fixture(self):
        from tests.l1_fixture import build_fixture
        from cadcore import docmodel
        value = build_fixture()
        self.assertTrue(value.dimension_uuid)
        self.assertEqual(Path(inspect.getsourcefile(docmodel)).resolve(),
                         PKG / "cadcore/docmodel.py")
        return value

    def test_dimension_regen_and_reassociate_live_geometry(self):
        operations = _load_ops("ops/features/annotation__w22.py")
        fixture = self.fixture()
        doc, target = fixture.doc, fixture.dimension_uuid
        node = next(row for row in doc.annotations if row["uuid"] == target)
        original = node["target"][0]
        lines = fixture.plate_sketch["entities"]

        def length(row):
            geom = row["geom"]
            return round(math.hypot(geom["x2"] - geom["x1"], geom["y2"] - geom["y1"]))

        old_line = next(row for row in lines if row["uuid"] == original)
        self.assertEqual(operations["dim_regen"](doc, target=target), [target])
        self.assertEqual(node["args"]["value_source"], "solved")
        self.assertEqual(node["value_nm"], length(old_line))
        other = next(row for row in lines
                     if row["kind"] == "line" and length(row) != length(old_line))
        self.assertEqual(operations["dim_reassociate"](
            doc, target=target, reference=[other["uuid"], "start"]), [target])
        self.assertEqual(node["target"], [other["uuid"], "start"])
        self.assertEqual(node["value_nm"], length(other))
        self.assertEqual(node["args"]["reassociated_from"], original)
        other["geom"]["x2"] += 10_000_000
        self.assertEqual(operations["dim_regen_all"](doc), [target])
        self.assertEqual(node["value_nm"], length(other))

    def test_multileader_branches_have_real_lengths(self):
        fixture = self.fixture()
        operations = _load_ops("ops/features/annotation__w16.py")
        before = len(fixture.doc.annotations)
        uuids = operations["add_multileader"](
            fixture.doc, leaders=[[[0, 0], [10_000_000, 10_000_000]],
                                  [[0, 10_000_000], [10_000_000, 10_000_000]]],
            text_key="probe.text")
        self.assertEqual(len(fixture.doc.annotations), before + 1)
        node = next(row for row in fixture.doc.annotations if row["uuid"] == uuids[0])
        self.assertEqual(node["kind"], "multileader")
        self.assertEqual(node["args"]["branch_count"], 2)
        self.assertGreater(node["args"]["total_length_nm"], 0)
        self.assertEqual(node["args"]["text_anchor_nm"], [10_000_000, 10_000_000])

    def test_tolerance_and_balloon_land_in_real_document(self):
        from cadcore import docmodel
        fixture = self.fixture()
        doc = fixture.doc
        before = docmodel.document_to_l1(doc)
        operations = _load_ops("ops/features/sw_drawings__w20.py")
        limits = operations["tolerance_limits"](doc, nominal_nm=25_000_000, code="H7", role="hole")
        fit = operations["fit_tolerance"](doc, nominal_nm=25_000_000, hole="H7", shaft="g6")
        hole = next(row for row in doc.annotations if row["uuid"] == limits[0])
        pair = next(row for row in doc.annotations if row["uuid"] == fit[0])
        self.assertEqual(hole["nominal_nm"], 25_000_000)
        self.assertEqual(set(pair["clearance_nm"]), {"min", "max"})
        self.assertIn(pair["fit_type"], ("clearance", "transition", "interference"))
        operations = _load_ops("ops/features/sw_drawings__w15.py")
        uuids = operations["balloon"](doc, items=[{"qty": 1, "part_key": "part.base"},
                                                {"qty": 2, "part_key": "part.top"}])
        after = docmodel.document_to_l1(doc)
        self.assertNotEqual(before, after)
        self.assertTrue(uuids)
        self.assertTrue(any(uid in after for uid in uuids))

    def test_registry_mutant_is_visible(self):
        """阴性对照：内存里删掉一个 op（真模块的 OPERATIONS），同一把尺子必须报红。"""
        import importlib

        module = importlib.import_module("cadcore.ops.organization")
        original = module.OPERATIONS.pop("attrib_batch")
        try:
            problems = registry_problems()
        finally:
            module.OPERATIONS["attrib_batch"] = original
        self.assertTrue(any("attrib_batch" in problem for problem in problems),
                        "删掉承载 op 后尺子没报红 ⇒ 尺子看不见红")
        self.assertEqual(registry_problems(), [], "恢复后必须回到全绿")


if __name__ == "__main__":  # pragma: no cover - 直跑入口
    unittest.main()
