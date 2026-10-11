"""tests.test_b6_assembly_atomic_store —— B6 装配面缺陷的回归尺子（RC-A1 / RC-A2）。

两条缺陷
--------
* **RC-A1（partial_state_on_reject）**：`mate_delete` 边删边校验 ⇒ 混合列表（存在的 + 不存在的
  uuid）抛 `unknown_mate` **但前面的配合已经删掉**：调用方看到失败、真源却变了半截。
  钉子：被拒时真源**逐字节不变**；阴性对照：全存在时正常删净（证明"摘要不变"不是尺子失灵）。
* **RC-A2（slice_store_disagreement）**：族模块 `sw_assembly` 与功能片 `sw_assembly__w9`
  对"装配表放哪"口径不一致 —— dict 形态 L1 文档（没有 `extra` 键）时，族模块放 `doc["sw_assembly"]`，
  w9 片却**凭空建一个 `doc["extra"]`** 把表放到里面。后果三条：w9 的 7 个对齐 op 全部
  `unknown_mate`；w9 碰过一次文档后族模块自己也读不到（`component_pose` → `unknown_component`）；
  L1 根上多出一个非法键 `extra`。钉子 + 阴性对照（真没有配合时读侧仍要能说清）。

另外钉住读侧纪律：`mate_check` **不许**把"这个目标没有装配表"记成"装配没问题"（禁静默 `[]`）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import features, sw_assembly as asm  # noqa: E402

MM = 1_000_000
MISSING = "00000000-0000-0000-0000-000000000000"


def _fixture() -> tuple[dict, list[str]]:
    """探针 `asm_fixture()` 的同一份：base(固定) / pin / plate，dict 形态 L1（**没有 `extra`**）。"""
    doc: dict = {"schema_version": 1, "unit": "mm", "standard_profile": "iso",
                 "sketches": [], "features": [], "annotations": [], "sheets": [],
                 "meta": {"doc_uuid": "11111111-1111-1111-1111-111111111111"}}
    ids = asm.insert_component(
        doc, part_key="base", at_nm=(0, 0, 0), size_nm=(60 * MM, 40 * MM, 10 * MM),
        refs={"axis": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]},
              "face": {"point_nm": [0, 0, 10 * MM], "dir": [0, 0, 1]},
              "side": {"point_nm": [60 * MM, 0, 0], "dir": [1, 0, 0]},
              "east": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]}}, fixed=True)
    ids += asm.insert_component(
        doc, part_key="pin", at_nm=(0, 0, 10 * MM), size_nm=(10 * MM, 10 * MM, 20 * MM),
        refs={"axis": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]},
              "face": {"point_nm": [0, 0, 0], "dir": [0, 1, 0]},
              "side": {"point_nm": [10 * MM, 0, 0], "dir": [1, 0, 0]}})
    ids += asm.insert_component(
        doc, part_key="plate", at_nm=(30 * MM, 0, 10 * MM), size_nm=(20 * MM, 20 * MM, 5 * MM),
        refs={"axis": {"point_nm": [0, 0, 0], "dir": [1, 0, 0]},
              "origin2": {"point_nm": [20 * MM, 20 * MM, 5 * MM], "dir": [0, 1, 0]}})
    assert "extra" not in doc, "RC-A2 的形状就是'dict 文档没有 extra 容器'"
    return doc, ids


def _digest(doc) -> str:
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)


def _mates(doc) -> list:
    return list((doc.get("sw_assembly") or {}).get("mates") or [])


def _alignment_ops() -> dict:
    return dict(features.load_all().get("sw-assembly", {}))


class MateDeleteAtomicityTests(unittest.TestCase):
    """RC-A1：被拒 ⇒ 一个字节都不许改；全命中 ⇒ 正常删净（阴性对照）。"""

    def test_mixed_uuid_rejects_without_touching_the_source(self) -> None:
        doc, ids = _fixture()
        first = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])
        asm.mate_concentric(doc, refs=[[ids[0], "face"], [ids[2], "axis"]])
        before = _digest(doc)
        with self.assertRaises(CadError) as caught:
            asm.mate_delete(doc, mates=[first[0], MISSING])
        self.assertEqual(str(getattr(caught.exception, "reason", "")), "unknown_mate")
        self.assertEqual(_digest(doc), before, "被拒的 mate_delete 不许改真源（半截状态）")
        self.assertEqual(len(_mates(doc)), 2, "两条配合都要还在")

    def test_unknown_first_also_leaves_the_source_alone(self) -> None:
        """顺序反过来（不存在在前）同样不许改 —— 不许只修"存在的前缀"这一种排法。"""
        doc, ids = _fixture()
        first = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])
        before = _digest(doc)
        with self.assertRaises(CadError):
            asm.mate_delete(doc, mates=[MISSING, first[0]])
        self.assertEqual(_digest(doc), before)
        self.assertEqual(len(_mates(doc)), 1)

    def test_duplicate_uuid_is_rejected_atomically(self) -> None:
        doc, ids = _fixture()
        first = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])
        before = _digest(doc)
        with self.assertRaises(CadError) as caught:
            asm.mate_delete(doc, mates=[first[0], first[0]])
        self.assertEqual(str(getattr(caught.exception, "reason", "")), "duplicate_mate")
        self.assertEqual(_digest(doc), before)

    def test_negative_control_all_present_deletes_cleanly(self) -> None:
        """尺子看得见绿（也是"摘要没变"不是因为摘要函数失灵的反证）。"""
        doc, ids = _fixture()
        first = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])
        second = asm.mate_concentric(doc, refs=[[ids[0], "face"], [ids[2], "axis"]])
        before = _digest(doc)
        out = asm.mate_delete(doc, mates=[first[0], second[0]])
        self.assertEqual(out, [first[0], second[0]], "返回顺序 = 给定顺序")
        self.assertEqual(_mates(doc), [])
        self.assertNotEqual(_digest(doc), before, "删除必须真的改真源")


class SliceStoreAgreementTests(unittest.TestCase):
    """RC-A2：族模块与 w9 片必须认同一张表；读侧不许把"没表"记成"没配合"。"""

    def setUp(self) -> None:
        ops = _alignment_ops()
        self.align_get = ops.get("mate_alignment_get")
        self.align_set = ops.get("mate_alignment_set")
        self.align_audit = ops.get("mate_alignment_audit")
        if self.align_get is None or self.align_set is None:
            self.skipTest("sw-assembly feature slice registry has no alignment ops")

    def test_slice_sees_the_family_table_and_does_not_shadow_it(self) -> None:
        doc, ids = _fixture()
        mate = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])[0]
        self.assertEqual(self.align_set(doc, mates=[mate], alignment="anti_aligned"), [mate])
        self.assertNotIn("extra", doc, "w9 片不许在 L1 根上凭空建非法键 'extra'")
        self.assertTrue(_mates(doc), "族模块写下的配合必须还在 doc['sw_assembly'] 里")
        got = self.align_get(doc, mates=[mate], alignment=None)
        self.assertTrue(got, "w9 片必须读得到族模块写的同一条配合：%r" % (got,))

    def test_family_keeps_seeing_its_table_after_a_slice_call(self) -> None:
        doc, ids = _fixture()
        mate = asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])[0]
        before = asm.component_pose(doc, component=ids[2])
        self.align_get(doc, mates=[mate], alignment=None)
        after = asm.component_pose(doc, component=ids[2])
        self.assertEqual(before, after, "切片调用后族模块必须还看得见原表（位姿不变）")

    def test_every_alignment_op_reads_the_family_table(self) -> None:
        """探针 A27 的同一判据：7 个对齐 op 在 dict 文档上全部可用。"""
        doc, ids = _fixture()
        mate = asm.mate_distance(doc, refs=[[ids[0], "origin"], [ids[1], "origin"]],
                                 value_nm=10 * MM)[0]
        ops = _alignment_ops()
        names = ("mate_alignment_get", "mate_alignment_check", "mate_alignment_set",
                 "mate_alignment_toggle", "mate_align", "mate_flip_alignment",
                 "mate_alignment_audit")
        failures = []
        for name in names:
            func = ops.get(name)
            if func is None:
                failures.append("%s: missing" % name)
                continue
            kwargs: dict = {"mates": [mate]}
            if name in ("mate_alignment_get", "mate_alignment_set"):
                kwargs["alignment"] = "anti_aligned" if name.endswith("set") else None
            try:
                func(doc, **kwargs)
            except CadError as exc:
                failures.append("%s: %s(%s)" % (name, type(exc).__name__,
                                                getattr(exc, "reason", "")))
        self.assertEqual(failures, [], "对齐 op 在 dict 文档上必须全部读得到族表")

    def test_mate_check_distinguishes_no_table_from_no_mates(self) -> None:
        """读侧纪律：没有装配表 ⇒ 结构化拒绝，**不许**静默返回 `[]`。"""
        bare: dict = {"schema_version": 1, "unit": "mm", "sketches": [], "features": [],
                      "annotations": [], "sheets": []}
        with self.assertRaises(CadError) as caught:
            asm.mate_check(bare)
        self.assertEqual(str(getattr(caught.exception, "reason", "")), "no_assembly")
        self.assertNotIn("sw_assembly", bare, "读侧不许凭空建表")

    def test_negative_control_empty_table_returns_empty_list(self) -> None:
        """阴性对照：表在但一条配合都没有 ⇒ 老老实实返回 `[]`（与"没表"可辨）。"""
        doc, _ids = _fixture()
        self.assertEqual(asm.mate_check(doc), [])


class StoreLocationIsSingleSourceTests(unittest.TestCase):
    """表定位只有**一处**实现：三个片与族模块必须给出同一个容器。"""

    def test_slices_delegate_to_the_family_holder(self) -> None:
        from cadcore.ops.features import sw_assembly__w9 as w9

        doc, ids = _fixture()
        asm.mate_concentric(doc, refs=[[ids[0], "axis"], [ids[1], "axis"]])
        self.assertIs(w9._store(doc), asm._store(doc),
                      "w9 片的 _store 必须是族模块同一张表（同一对象）")
        self.assertIs(w9._document_extra(doc), asm._holder(doc))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
