# SPDX-License-Identifier: Apache-2.0
"""test_partials_batch3_ops -- 第三批「partial → implemented」的静态 + 行为回归尺子（毫秒级）。

为什么要有这个文件
-----------------
端到端尺子是 `oss-alternatives/tools/cad/parity_partials_batch3_gate.py`（真 Document/真草图 + 8 个变异体），
族门是 `oss-alternatives/tools/cad/parity_organization_gate.py`（多模块装载 + 9 个变异体）。
本文件是同一批事实的**日常回归快尺子**：任何一条被改回去（op 从 OPERATIONS 消失 / 隔离片被搬回
`_quarantine/` / status 台账被退回 partial）当场报红，不用起门。

覆盖的四层
----------
1. 实现层：本批 8 个承载 op 在对应模块的 `OPERATIONS` 里且签名关键字齐全；
2. 落点层：三条隔离片已放回**主集合**（`ops/features/<stem>.py` 在位、`_quarantine/<stem>.py` 已不在）；
3. 行为层：`attrib_batch` 跨实例统改（两实例同改 + 与 `attrib_set` 同址 + 空选择负向）；
4. 台账层：8 条 status 条目当刻都是 `implemented` 且条目名逐字一致。

反向自证（阴性对照）：`test_registry_mutant_is_visible` 在内存里删掉一个 op 再跑同一个检查函数，
必须看得见红 —— 否则这四层断言只是"永远绿"。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_THIS = Path(__file__).resolve()
OSS = _THIS.parents[3]                     # tests -> cadcore -> packages -> oss-alternatives
PKG = OSS / "packages" / "cadcore"
FEATURES = PKG / "cadcore" / "ops" / "features"
STATUS = OSS / "docs" / "research" / "cad-parity" / "status"

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

#: 台账条目：(族, 下标, 条目名)。
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

    def test_status_ledger_says_implemented(self):
        for family, index, label in LEDGER:
            data = json.loads((STATUS / f"{family}.json").read_text(encoding="utf-8"))
            item = data["items"][index]
            self.assertEqual(item["name"], label, f"{family}#{index} 条目名漂移")
            self.assertEqual(item["status"], "implemented", f"{family}#{index} 台账被退回")

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
