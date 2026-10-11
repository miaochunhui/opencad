"""tests.test_l1_fixture —— `tests/l1_fixture.py` 的自证单测（纯标准库 unittest）。

设计一句话
----------
**夹具最容易骗人的地方是"没人真读过它"** —— 所以本文件不看"能不能构造出来"，而是拿 cadcore 自己的
只读尺子对夹具**逐条现算**：轮廓闭合（`constraints.closed_profiles`）、实体有体积
（`docmodel.recompute` + `nano_kernel`）、图纸有视口（L1 `sheets[].views[]`）、零部件与配合数正确
（`doc.extra["sw_assembly"]` 表 + 公开查询）、L1 载荷往返一致（`document_to_l1` / `document_from_l1`）、
不变量为空（`check_invariants`）、构造过程**不写盘**（前后文件快照比对）。

为什么不在探针里喂 dict（背景）
--------------------------------
空壳席的 `live_ops=47/176` 是"探针下无成功路径"，不是"实现没有路径"：`l1_document_required` /
`no_base_solid` / `no_mates` / `no_sheet_collection` / `no_layout` 这些 reason 的触发条件正是
"入参不是真 `Document` / 没有草图 / 没有未抑制拉伸 / 没有 mates 表 / 没有 sheets[].views[]"。
本夹具把这几样**都造齐**（且都能被公开 API 现算复核），才能真正区分两种结论。

已知缺口（记 blocker，**本席只写测试，不改实现**）
--------------------------------------------------
1. `sw_assembly` 的**零部件行**用 `refs` 存"参考名 → 局部几何"的**映射**，而 `docmodel.check_invariants()`
   的通用规则把任何 `refs` 键当 L1 引用列表读（`_REF_KEYS = ("refs", "target")`）⇒ 报
   `refs_not_a_list:$.sw_assembly.components[N].refs`。这是上游两片口径不一致，不是夹具造错；
   本文件把 L1 **冻结面**（`unit/meta/sketches/features/annotations/sheets`）的不变量判成红线，
   把 `extra` 命名空间里**仅限已登记形态**的发现单列并计入判定（`KNOWN_EXTRA_PROBLEMS`），
   出现任何别的形态立刻报红 —— 不静默、不放宽。
2. 配合行里"未采"的容差可能以 `None` 落进 `*_nm` 键（`nm_is_null`，`value_nm` 被实现显式豁免）。
   同样只登记这一种形态。
3. 实体 uuid 由上游 `new_uuid()`（uuid4）生成 ⇒ **不**断言"两次构造逐字节相同"（那是实现没承诺的事）。
   断言的是：规范形态是固定点（`document_to_l1 ∘ document_from_l1` 一次归一后逐字节稳定）+ 载荷全等。

夹具自证与副作用
----------------
每个测试都从 `l1_fixture.build_fixture()` 现场构造（`setUpClass` 一次，全程只读）；最后一个测试
（按名字排序）比对**构造前后**的包内文件快照（排除 `__pycache__`）证明夹具不写盘。
断言消息一律英文（与兄弟测试文件的源码纪律一致），中文只出现在 docstring / 注释里。
"""

from __future__ import annotations

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:  # 直跑本文件时的包路径兜底（discover 路径已带）
    sys.path.insert(0, _ROOT)

from cadcore.docmodel import (  # noqa: E402
    Document,
    check_invariants,
    document_from_l1,
    document_to_l1,
    recompute,
)
from cadcore.draw.sheet import VIEW_DIRECTIONS  # noqa: E402
from cadcore.ops import organization  # noqa: E402
from cadcore.sketch import constraints as sketch_constraints  # noqa: E402
from tests import l1_fixture  # noqa: E402

#: `extra` 命名空间里**已登记**的问题形态（见 docstring「已知缺口」；出现别的形态即报红）。
KNOWN_EXTRA_PROBLEMS: tuple[str, ...] = ("refs_not_a_list", "nm_is_null")
#: 从问题串里抠出路径（`kind:$.<field>...`；实现会在路径后追加 ` type=…` 之类的取证后缀）。
_PATH_RE = re.compile(r"\$(\.[A-Za-z_][A-Za-z0-9_]*)+")

#: L1 冻结面（顶层字段）：这些路径上的不变量违规是红线，一条都不许有。
FROZEN_KEYS: tuple[str, ...] = ("unit", "meta", "sketches", "features", "annotations", "sheets")
#: `extra` 命名空间（未知顶层字段容器）：本夹具用了这两个。
EXTRA_NAMESPACES: tuple[str, ...] = ("organization", "sw_assembly")


def _problem_path(problem: str) -> str:
    """`refs_not_a_list:$.sw_assembly.components[0].refs type=dict` → `$.sw_assembly.components[0].refs`。"""
    match = _PATH_RE.search(str(problem))
    return match.group(0) if match else ""


def _problem_namespace(problem: str) -> str:
    """`$.sw_assembly.mates[0].refs` → `sw_assembly`（取顶层字段名，去掉下标）。"""
    path = _problem_path(problem)
    parts = path.lstrip("$").lstrip(".").split(".")
    return parts[0].split("[")[0] if parts and parts[0] else ""


def _without_uuids(rows: list[dict]) -> list[dict]:
    """剥掉上游 uuid4 生成的 `uuid` 键：剩下的字段才是夹具参数驱动的可复现面。"""
    return [{key: value for key, value in row.items() if key != "uuid"} for row in rows]


def _tree_snapshot(root: str) -> dict[str, tuple[int, int]]:
    """包内文件快照（排除 `__pycache__` / `*.pyc`）：相对路径 → (字节数, mtime_ns)。"""
    out: dict[str, tuple[int, int]] = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            stat = os.stat(path)
            out[os.path.relpath(path, root)] = (int(stat.st_size), int(stat.st_mtime_ns))
    return out


class L1FixtureSelfProof(unittest.TestCase):
    """夹具自证：每一条都用 cadcore 的只读 API 现算，不抄夹具自己的声明。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = l1_fixture.build_fixture()
        cls.doc = cls.fx.doc
        cls.rebuild = recompute(cls.doc, cls.fx.kernel)
        cls.snapshot_before = _tree_snapshot(_ROOT)

    # -- 01 真件 ------------------------------------------------------------------------
    def test_01_document_is_a_real_document_and_l1_shaped(self) -> None:
        """夹具必须是真 `Document`（不是 dict 伪装），且 L1 顶层字段齐、数量对。"""
        doc = self.doc
        self.assertIsInstance(doc, Document, "fixture must build a real cadcore.docmodel.Document")
        self.assertEqual(doc.meta["doc_uuid"], l1_fixture.STABLE_DOC_UUID)
        self.assertEqual(doc.unit, l1_fixture.DOC_UNIT)
        self.assertEqual(doc.standard_profile, l1_fixture.DOC_STANDARD)
        self.assertEqual(len(doc.sketches), 2)
        self.assertEqual(len(doc.features), 1)
        self.assertEqual(len(doc.sheets), 1)
        # L1 文本是可解析的规范形态（真源是它，不是内存对象）
        again = document_from_l1(document_to_l1(doc))
        self.assertEqual(again.meta["doc_uuid"], doc.meta["doc_uuid"])
        # uuid 账必须与文档里的真 uuid 一致（防止测试拿常量自嗨）
        self.assertEqual(self.fx.plate_sketch_uuid, doc.sketches[0]["uuid"])
        self.assertEqual(self.fx.hole_sketch_uuid, doc.sketches[1]["uuid"])
        self.assertEqual(self.fx.feature_uuid, doc.features[0]["uuid"])
        for uuid_ in self.fx.plate_line_uuids:
            self.assertIn(uuid_, [entity["uuid"] for entity in doc.sketches[0]["entities"]])

    # -- 02 轮廓闭合 --------------------------------------------------------------------
    def test_02_sketch_profile_is_closed_with_constraints(self) -> None:
        """轮廓闭合由 `closed_profiles()` 现算；约束由公开写入器落库且引用成对。"""
        sketch = self.fx.plate_sketch
        entities = sketch_constraints.sketch_entities(sketch)
        self.assertEqual(len(entities), 4, "plate profile must be four lines")
        self.assertEqual({entity["kind"] for entity in entities}, {"line"})
        self.assertEqual({entity["layer"] for entity in entities}, {l1_fixture.LAYER_PLATE})
        geometries = [tuple(entity["geom"][key] for key in ("x1", "y1", "x2", "y2"))
                      for entity in entities]
        for geom in geometries:
            for value in geom:
                self.assertIsInstance(value, int)
        profiles = sketch_constraints.closed_profiles(sketch)
        self.assertEqual(len(profiles), 1, "plate profile must close into exactly one loop")
        self.assertEqual(set(profiles[0]), set(self.fx.plate_line_uuids))
        constraints = sketch_constraints.sketch_constraints(sketch)
        self.assertEqual(len(constraints), 4, "four coincident constraints close the rectangle")
        for row in constraints:
            self.assertEqual(row["kind"], "coincident")
            self.assertEqual(len(row["refs"]), 2)
            for ref in row["refs"]:
                self.assertIsInstance(ref, (list, tuple))
                self.assertEqual(len(ref), 2)
                self.assertIn(ref[0], set(self.fx.plate_line_uuids))
                self.assertIn(ref[1], ("start", "end"))

    # -- 03 body 有体积 -----------------------------------------------------------------
    def test_03_body_has_positive_volume_from_recompute(self) -> None:
        """拉伸体重建成功、无 error 诊断，且 mass 里的体积 = 长×宽×厚（现算，不写死）。"""
        result = self.rebuild
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.errors(), [])
        self.assertEqual(len(result.shape_refs), 1)
        shape_ref = result.shape_refs[0]
        self.assertEqual(shape_ref["feature_uuid"], self.fx.feature_uuid)
        self.assertEqual(shape_ref["sketch_uuid"], self.fx.plate_sketch_uuid)
        mass = shape_ref.get("mass")
        self.assertIsInstance(mass, dict, "nano kernel must report mass properties for a solid")
        key = next((name for name in mass if "volume" in str(name).lower()), None)
        self.assertIsNotNone(key, "mass properties carry no volume field: %s" % sorted(mass))
        volume = float(mass[key])
        self.assertGreater(volume, 0.0, "extruded body must have positive volume")
        expected_nm3 = (l1_fixture.PLATE_LENGTH_NM * l1_fixture.PLATE_WIDTH_NM
                        * l1_fixture.PLATE_THICKNESS_NM)
        if str(key).endswith("_nm3"):
            self.assertAlmostEqual(volume / expected_nm3, 1.0, places=9,
                                   msg="volume %r != L*W*T %r" % (volume, expected_nm3))

    # -- 04 图纸有视口 ------------------------------------------------------------------
    def test_04_sheet_has_a_viewport(self) -> None:
        """`sheets[]` 里有真视口：**恰好两个**（多视图上下文，见夹具 docstring 第「上下文扩容」表），
        两个视口各自的 uuid / direction / at_nm / hidden 都与公开常量对得上，且互不是同一个视口。"""
        self.assertEqual(len(self.doc.sheets), 1)
        entry = self.doc.sheets[0]
        self.assertEqual(entry["uuid"], self.fx.sheet_uuid)
        self.assertEqual(entry["standard_profile"], l1_fixture.DOC_STANDARD)
        self.assertIn("title_block", entry)
        views = entry.get("views")
        self.assertIsInstance(views, list)
        self.assertEqual(len(views), 2,
                         "sheet must carry exactly the two viewports the fixture places "
                         "(multi-view context: SHEET_VIEW + SHEET_VIEW_ALT)")
        self.assertTrue(self.fx.view_uuid, "fixture must expose the first viewport uuid")
        self.assertTrue(self.fx.view_alt_uuid, "fixture must expose the second viewport uuid")
        self.assertEqual([view["uuid"] for view in views],
                         [self.fx.view_uuid, self.fx.view_alt_uuid])
        placements = ((l1_fixture.SHEET_VIEW, l1_fixture.SHEET_VIEW_AT_NM),
                      (l1_fixture.SHEET_VIEW_ALT, l1_fixture.SHEET_VIEW_ALT_AT_NM))
        for index, (view, (name, at_nm)) in enumerate(zip(views, placements)):
            self.assertEqual(list(view["direction"]), list(VIEW_DIRECTIONS[name]),
                             "viewport %d direction must match VIEW_DIRECTIONS[%r]" % (index, name))
            self.assertEqual([int(item) for item in view["at_nm"]], list(at_nm),
                             "viewport %d at_nm must match the fixture constant %r"
                             % (index, (name, at_nm)))
            self.assertTrue(view["hidden"], "viewport %d must be built hidden=True" % index)
        # 多视图不许退化成"同一个视口放两次"：uuid / 方向 / 落点三项都得分开。
        self.assertNotEqual(self.fx.view_uuid, self.fx.view_alt_uuid)
        self.assertNotEqual(list(views[0]["direction"]), list(views[1]["direction"]),
                            "the two viewports must be distinct projections (front vs top)")
        self.assertNotEqual([int(i) for i in views[0]["at_nm"]],
                            [int(i) for i in views[1]["at_nm"]],
                            "the two viewports must be placed at distinct positions")

    # -- 05 零部件与配合 -----------------------------------------------------------------
    def test_05_assembly_components_and_mates(self) -> None:
        """两个零部件 + 一条配合，配合指向这两个零部件；数由表现读，不抄声明。"""
        store = self.fx.assembly_store
        components = store["components"]
        mates = store["mates"]
        self.assertEqual(len(components), 2)
        self.assertEqual(len(mates), 1)
        self.assertEqual([row["part_key"] for row in components],
                         [l1_fixture.PART_BASE, l1_fixture.PART_TOP])
        self.assertEqual(tuple(row["uuid"] for row in components), self.fx.component_uuids)
        mate = mates[0]
        self.assertEqual(mate["uuid"], self.fx.mate_uuid)
        self.assertEqual(mate["kind"], l1_fixture.MATE_KIND)
        referenced = [ref[0] for ref in mate["refs"]]
        self.assertEqual(referenced, list(self.fx.component_uuids))
        self.assertEqual([ref[1] for ref in mate["refs"]],
                         [l1_fixture.ASSEMBLY_REF_NAME, l1_fixture.ASSEMBLY_REF_NAME])
        for row in components:
            self.assertIn(l1_fixture.ASSEMBLY_REF_NAME, row["refs"])

    # -- 06 图层 / 块 / 外部参照 ----------------------------------------------------------
    def test_06_layers_blocks_and_xrefs_are_real_table_rows(self) -> None:
        """组织表（`doc.extra["organization"]`）有图层/块/插入/外部参照，且公开查询看得见。"""
        store = self.fx.organization_store
        self.assertEqual(store["schema"], 1)
        names = {row["name"] for row in store["layers"]}
        self.assertTrue({l1_fixture.LAYER_PLATE, l1_fixture.LAYER_HOLES} <= names,
                        "fixture layers missing: %s" % sorted(names))
        self.assertEqual(len(store["blocks"]), 1)
        self.assertEqual(store["blocks"][0]["name"], l1_fixture.BLOCK_NAME)
        self.assertEqual(len(store["inserts"]), 1)
        self.assertEqual(len(store["xrefs"]), 1)
        xref = store["xrefs"][0]
        self.assertEqual(xref["uuid"], self.fx.xref_uuid)
        self.assertEqual(xref["path"], l1_fixture.XREF_PATH)
        self.assertFalse(xref["loaded"], "xref is a placeholder: loaded must be False (0 = real zero)")
        # 公开查询与表内容一致（不只看本席写进去的 dict）
        self.assertTrue(organization.xref_filter(self.doc, name=l1_fixture.XREF_NAME))
        self.assertTrue(organization.layer_filter(self.doc, name=l1_fixture.LAYER_PLATE))
        # 实体真的落在对应图层上（图层不是"只建表没接线"）
        assigned = {entity["layer"] for entity in self.fx.plate_sketch["entities"]}
        self.assertEqual(assigned, {l1_fixture.LAYER_PLATE})

    # -- 07 L1 不变量 -------------------------------------------------------------------
    def test_07_l1_invariants_clean_on_frozen_surface(self) -> None:
        """`check_invariants()` 在 L1 冻结面上必须为空；`extra` 命名空间只容忍已登记形态。"""
        problems = check_invariants(self.doc)
        frozen = [item for item in problems if _problem_namespace(item) in FROZEN_KEYS]
        self.assertEqual(frozen, [], "L1 frozen surface must be invariant-clean: %s" % frozen)
        extra = [item for item in problems if _problem_namespace(item) in EXTRA_NAMESPACES]
        unexpected = [item for item in extra
                      if item.split(":", 1)[0] not in KNOWN_EXTRA_PROBLEMS]
        self.assertEqual(unexpected, [], "unregistered extra-namespace findings: %s" % unexpected)
        outside = [item for item in problems
                   if _problem_namespace(item) not in FROZEN_KEYS + EXTRA_NAMESPACES]
        self.assertEqual(outside, [], "findings outside known namespaces: %s" % outside)

    # -- 08 载荷往返 --------------------------------------------------------------------
    def test_08_payload_roundtrip_is_lossless(self) -> None:
        """L1 载荷往返无损：冻结字段与 `extra` 全等；规范形态是固定点。"""
        text = document_to_l1(self.doc)
        doc2 = document_from_l1(text)
        text2 = document_to_l1(doc2)
        doc3 = document_from_l1(text2)
        self.assertEqual(document_to_l1(doc3), text2,
                         "canonical L1 form must be a fixed point after one normalisation")
        self.assertEqual(doc3.to_payload(), doc2.to_payload(),
                         "second hop must not change the payload")
        for key in ("sketches", "features", "annotations", "sheets"):
            self.assertEqual(getattr(doc2, key), getattr(self.doc, key),
                             "L1 field %r did not survive the round trip" % key)
        self.assertEqual(doc2.unit, self.doc.unit)
        self.assertEqual(doc2.meta, self.doc.meta)
        self.assertEqual(doc2.extra, self.doc.extra,
                         "extra namespaces (organization / sw_assembly) did not survive the trip")
        self.assertEqual(doc2.extra["sw_assembly"]["mates"], self.fx.assembly_store["mates"])

    # -- 09 夹具自证：不写盘 + 结构可复现 --------------------------------------------------
    def test_09_fixture_has_no_side_effects_and_is_structurally_reproducible(self) -> None:
        """构造夹具不写盘（前后快照相等），且两次构造的结构与稳定 uuid 一致。"""
        before = _tree_snapshot(_ROOT)
        second = l1_fixture.build_fixture()
        after = _tree_snapshot(_ROOT)
        self.assertEqual(before, after, "building the fixture must not touch any file on disk")
        self.assertEqual(before, self.snapshot_before,
                         "class-level baseline drifted before this test ran")
        self.assertIsNot(second.doc, self.doc)
        self.assertEqual(len(second.doc.sketches), len(self.doc.sketches))
        self.assertEqual(len(second.doc.features), len(self.doc.features))
        self.assertEqual(len(second.doc.sheets), len(self.doc.sheets))
        self.assertEqual(second.sheet_uuid, self.fx.sheet_uuid,
                         "sheet uuid is derived from the pinned doc_uuid and must be reproducible")
        self.assertEqual(second.feature_uuid, self.fx.feature_uuid)
        self.assertEqual(_without_uuids(second.doc.extra["organization"]["layers"]),
                         _without_uuids(self.fx.organization_store["layers"]),
                         "layer table is parameter-driven and must be reproducible "
                         "(upstream layer uuid is uuid4, so it is excluded on both sides)")
        second_rebuild = recompute(second.doc, second.kernel)
        self.assertTrue(second_rebuild.ok, second_rebuild.as_dict())
        self.assertEqual(len(second_rebuild.shape_refs), len(self.rebuild.shape_refs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
