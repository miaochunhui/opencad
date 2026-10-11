#!/usr/bin/env python3
"""cadcore.ops.hatch_measure_extra 族测试：填充边界识别 + 面积/周长/距离测量的**批量接口**。

判据（每条断言对应冻结接口或模块 docstring 里的一句话，不是"能过就行"的冒烟）
----------------------------------------------------------------------------
1. **冻结接口按调用形态验**：`FAMILY` / `OPERATIONS`；每个操作**第一个位置参就叫 `sk_or_doc`**、
   其余参数一律 KEYWORD_ONLY；别名与主名指向**同一函数对象**；只读 op 返回 `list[str]`，
   写 op `hatch_fill_batch` 返回受影响实体 uuid 列表。
2. **自洽性用 AST 数出来**：本模块引用的每个 `_hm.<name>` 都必须在 base 族里真实存在
   （防"import 尚不存在的名字"）。
3. **三态不许同形**：`loops=0`（真为零）/ `area_nm2=NA`（不适用）/ `loop=-1`（不存在）各验一次。
4. **整数纳米精确相等**（面积 nm²）：只有 π 参与处容 1 nm² 的取整差。
5. **只读 op 不写盘**（输入逐字节不变，含 doc 形态不建表）；批量填充**预检失败整批不落**，
   边界实体一个纳米不动。
6. **错误路径给机器可读 `CadError.reason`** 且逐项失败带 `details["item"]`，不给"静默 0 / 静默 NA"。

直跑本文件（不经 `unittest discover`）打印判定行：
    HATCH_MEASURE_EXTRA_TESTS verdict=PASS tests=N failures=0 errors=0
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 tests/test_hatch_measure_extra.py 时的引导（同族模块写法）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import ast
import copy
import inspect
import math
import pathlib
import subprocess
import sys
import unittest

from cadcore.errors import CadError
from cadcore.ops import hatch_measure as HM
from cadcore.ops import hatch_measure_extra as EX

#: 本族 id（冻结接口里的 `FAMILY`）。
FAMILY_ID = "hatch_measure_extra"

#: 主操作名（别名另算；`OPERATIONS` 是唯一发现入口）。
MAIN_OPS = ("boundary_detect_batch", "measure_area_batch", "measure_perimeter_batch",
            "measure_distance_batch", "region_report_batch", "hatch_fill_batch")

#: 别名 → 主名（别名必须与主名指向**同一个函数对象**）。
ALIASES = {"boundary_batch": "boundary_detect_batch", "area_batch": "measure_area_batch",
           "perimeter_batch": "measure_perimeter_batch", "distance_batch": "measure_distance_batch",
           "region_report": "region_report_batch", "hatch_batch": "hatch_fill_batch"}

#: 包根（跑 `__main__` 自检子进程时的 cwd）。
ROOT = pathlib.Path(__file__).resolve().parents[1]

MM = 1_000_000                       # 1 mm = 1e6 nm
RECT_W = 100 * MM                    # 矩形 100 mm x 50 mm
RECT_H = 50 * MM
RECT_AREA = RECT_W * RECT_H          # 5e15 nm²
RECT_PERIMETER = 2 * (RECT_W + RECT_H)   # 3e8 nm
CIRCLE_R = 10 * MM
CIRCLE_AREA = int(round(math.pi * CIRCLE_R * CIRCLE_R))


# ======================================================================================
# 夹具（uuid 固定 ⇒ 同一夹具两次调用逐字节相同）
# ======================================================================================


def _line(uuid: str, x1: int, y1: int, x2: int, y2: int, layer: str = "0") -> dict:
    return {"uuid": uuid, "kind": "line", "layer": layer, "construction": False,
            "geom": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}}


def _circle(uuid: str, cx: int, cy: int, r: int, layer: str = "0") -> dict:
    return {"uuid": uuid, "kind": "circle", "layer": layer, "construction": False,
            "geom": {"cx": int(cx), "cy": int(cy), "r": int(r)}}


def _rect_entities(prefix: str = "bg") -> list:
    corners = [(0, 0), (RECT_W, 0), (RECT_W, RECT_H), (0, RECT_H)]
    return [_line("%s%d" % (prefix, index), corners[index][0], corners[index][1],
                  corners[(index + 1) % 4][0], corners[(index + 1) % 4][1])
            for index in range(4)]


def _open_entities(prefix: str = "oc", origin: tuple = (0, 0)) -> list:
    x0, y0 = origin
    side = 10 * MM
    points = [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    return [_line("%s%d" % (prefix, index), points[index][0], points[index][1],
                  points[index + 1][0], points[index + 1][1]) for index in range(3)]


def _rect_sketch() -> dict:
    return {"entities": _rect_entities(), "constraints": [], "dimensions": []}


def _holed_sketch() -> dict:
    entities = _rect_entities()
    entities.append(_circle("bgc", RECT_W // 2, RECT_H // 2, CIRCLE_R))
    return {"entities": entities, "constraints": [], "dimensions": []}


def _open_sketch() -> dict:
    return {"entities": _open_entities(), "constraints": [], "dimensions": []}


# ======================================================================================
# token 解析（与模块 `_blocks` 同一口径：先按空格归一，再按块首 item= 切块）
# ======================================================================================


def _blocks(tokens: list) -> list:
    out: list = []
    for piece in " ".join(tokens).split(" "):
        if piece.startswith("item="):
            out.append([piece])
        elif out:
            out[-1].append(piece)
    return out


def _value(tokens: list, key: str) -> str:
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


# ======================================================================================
# 用例
# ======================================================================================


class HatchMeasureExtraTests(unittest.TestCase):
    """批量补全层：接口冻结 / 三态 / 精确整数 / 只读 / fail-fast 归因 / 自检判定行。"""

    def test_frozen_interface(self) -> None:
        self.assertEqual(EX.FAMILY, FAMILY_ID)
        self.assertNotEqual(EX.FAMILY, HM.FAMILY)
        self.assertIsInstance(EX.OPERATIONS, dict)
        for name, function in sorted(EX.OPERATIONS.items()):
            self.assertTrue(callable(function), name)
            parameters = list(inspect.signature(function).parameters.values())
            self.assertEqual(parameters[0].name, "sk_or_doc", name)
            self.assertIn(parameters[0].kind,
                          (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD), name)
            for parameter in parameters[1:]:
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY,
                                 "%s.%s" % (name, parameter.name))
        for name in MAIN_OPS:
            self.assertIn(name, EX.OPERATIONS)
        for alias, target in sorted(ALIASES.items()):
            self.assertIs(EX.OPERATIONS[alias], EX.OPERATIONS[target], alias)
        read = EX.boundary_detect_batch(_rect_sketch(), regions=[None])
        self.assertIsInstance(read, list)
        self.assertTrue(read and all(isinstance(token, str) for token in read))
        self.assertEqual(_blocks(read)[0][0], "item=0")
        self.assertEqual(read[-1], "batch_count=1")
        written = EX.hatch_fill_batch(_rect_sketch(), regions=[None], spacing_nm=10 * MM)
        self.assertIsInstance(written, list)
        self.assertTrue(written and all(isinstance(token, str) for token in written))

    def test_module_only_uses_names_that_exist_in_base(self) -> None:
        source = pathlib.Path(EX.__file__).read_text(encoding="utf-8")
        referenced = sorted({node.attr for node in ast.walk(ast.parse(source))
                             if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                             and node.value.id == "_hm"})
        self.assertTrue(referenced)
        self.assertEqual([name for name in referenced if not hasattr(HM, name)], [])
        self.assertIn("hatch_create", referenced)

    def test_boundary_detect_batch_two_loops(self) -> None:
        sketch = _holed_sketch()
        blocks = _blocks(EX.boundary_detect_batch(sketch, regions=[None]))
        self.assertEqual(len(blocks), 2)
        self.assertEqual(_value(blocks[0], "spec"), "all")
        self.assertEqual(_value(blocks[0], "loops"), "2")
        self.assertEqual(_value(blocks[0], "closed"), "1")
        self.assertEqual(_value(blocks[0], "area_nm2"), str(RECT_AREA))
        self.assertEqual(set(_value(blocks[0], "boundary").split(",")), {"bg0", "bg1", "bg2", "bg3"})
        self.assertEqual(EX.boundary_detect_batch(sketch, regions=[None])[-1], "batch_count=1")
        self.assertEqual(_blocks(EX.boundary_detect_batch(sketch, regions=[None]))[0][0], "item=0")
        self.assertLessEqual(abs(int(_value(blocks[1], "area_nm2")) - CIRCLE_AREA), 1)
        self.assertEqual(_value(blocks[1], "loop"), "1")

    def test_boundary_detect_batch_region_shapes(self) -> None:
        sketch = _holed_sketch()
        implicit = _blocks(EX.boundary_detect_batch(sketch, regions=None))
        explicit = _blocks(EX.boundary_detect_batch(sketch, regions=[None]))
        self.assertEqual(implicit, explicit)
        one = _blocks(EX.boundary_detect_batch(sketch, regions=[["bg0", "bg1", "bg2", "bg3"]]))
        self.assertEqual(len(one), 1)
        self.assertEqual(_value(one[0], "spec"), "refs:4")
        self.assertEqual(_value(one[0], "area_nm2"), str(RECT_AREA))
        two = _blocks(EX.boundary_detect_batch(sketch, regions=["bg0", "bg1"]))
        self.assertEqual([_value(block, "spec") for block in two], ["uuid:bg0", "uuid:bg1"])
        self.assertEqual([_value(block, "loop") for block in two], ["-1", "-1"])
        indexed = _blocks(EX.boundary_detect_batch(sketch, regions=[0, 1]))
        self.assertEqual([_value(block, "spec") for block in indexed], ["loop:0", "loop:1"])
        self.assertEqual(_value(indexed[0], "area_nm2"), str(RECT_AREA))

    def test_boundary_detect_batch_no_loop_three_state(self) -> None:
        sketch = _open_sketch()
        before = copy.deepcopy(sketch)
        blocks = _blocks(EX.boundary_detect_batch(sketch, regions=[None]))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(_value(blocks[0], "loop"), "-1")
        self.assertEqual(_value(blocks[0], "loops"), "0")
        self.assertEqual(_value(blocks[0], "boundary"), "NA")
        self.assertEqual(_value(blocks[0], "area_nm2"), "NA")
        self.assertEqual(_value(blocks[0], "perimeter_nm"), "NA")
        self.assertEqual(_value(blocks[0], "closed"), "0")
        self.assertEqual(sketch, before)

    def test_measure_area_batch_exact_and_na(self) -> None:
        block = _blocks(EX.measure_area_batch(_rect_sketch(), regions=[None]))[0]
        self.assertEqual(_value(block, "area_nm2"), str(RECT_AREA))
        self.assertEqual(_value(block, "outer_area_nm2"), str(RECT_AREA))
        self.assertEqual(_value(block, "hole_area_nm2"), "0")
        self.assertEqual(_value(block, "perimeter_nm"), str(RECT_PERIMETER))
        self.assertEqual(_value(block, "loop_count"), "1")
        self.assertEqual(_value(block, "closed"), "1")
        holed = _blocks(EX.measure_area_batch(_holed_sketch(), regions=[None]))[0]
        outer = int(_value(holed, "outer_area_nm2"))
        hole = int(_value(holed, "hole_area_nm2"))
        self.assertEqual(outer, RECT_AREA)
        self.assertLessEqual(abs(hole - CIRCLE_AREA), 1)
        self.assertEqual(int(_value(holed, "area_nm2")), outer - hole)
        two = _blocks(EX.measure_area_batch(_holed_sketch(), regions=[0, 1]))
        self.assertEqual([_value(block, "spec") for block in two], ["loop:0", "loop:1"])
        areas = sorted(int(_value(block, "area_nm2")) for block in two)
        self.assertLessEqual(abs(areas[0] - CIRCLE_AREA), 1)
        self.assertEqual(areas[1], RECT_AREA)
        open_block = _blocks(EX.measure_area_batch(_open_sketch(), regions=[None]))[0]
        for key in ("area_nm2", "outer_area_nm2", "hole_area_nm2", "perimeter_nm"):
            self.assertEqual(_value(open_block, key), "NA", key)
        self.assertEqual(_value(open_block, "loop_count"), "0")
        self.assertEqual(_value(open_block, "closed"), "0")

    def test_measure_perimeter_batch_exact_and_open(self) -> None:
        block = _blocks(EX.measure_perimeter_batch(_rect_sketch(), regions=[None]))[0]
        self.assertEqual(_value(block, "perimeter_nm"), str(RECT_PERIMETER))
        self.assertEqual(_value(block, "curve_count"), "4")
        self.assertEqual(_value(block, "closed"), "1")
        open_block = _blocks(EX.measure_perimeter_batch(_open_sketch(), regions=[None]))[0]
        self.assertEqual(_value(open_block, "perimeter_nm"), str(3 * 10 * MM))
        self.assertEqual(_value(open_block, "curve_count"), "3")
        self.assertEqual(_value(open_block, "closed"), "0")

    def test_measure_distance_batch_forms(self) -> None:
        sketch = _rect_sketch()
        blocks = _blocks(EX.measure_distance_batch(
            sketch, pairs=[[(0, 0), (3 * MM, 4 * MM)], {"p1_nm": (0, 0), "ref_b": "bg0"}, ("bg0", "bg1")]))
        self.assertEqual(len(blocks), 3)
        self.assertEqual(_value(blocks[0], "distance_nm"), str(5 * MM))
        self.assertEqual(_value(blocks[0], "dx_nm"), str(3 * MM))
        self.assertEqual(_value(blocks[0], "dy_nm"), str(4 * MM))
        self.assertEqual(_value(blocks[0], "a"), "pt=0,0")
        self.assertEqual(_value(blocks[0], "b"), "pt=3000000,4000000")
        self.assertEqual(_value(blocks[1], "distance_nm"), "0")
        self.assertEqual(_value(blocks[1], "b"), "ref=bg0")
        self.assertEqual(_value(blocks[2], "distance_nm"), "0")
        single = _blocks(EX.measure_distance_batch(sketch, pairs=("bg0", "bg2")))
        self.assertEqual(_value(single[0], "distance_nm"), str(RECT_H))
        self.assertEqual(EX.measure_distance_batch(sketch, pairs=("bg0", "bg2"))[-1], "batch_count=1")
        mapping = _blocks(EX.measure_distance_batch(sketch, pairs={"ref_a": "bg0", "ref_b": "bg2"}))
        self.assertEqual(_value(mapping[0], "distance_nm"), str(RECT_H))
        self.assertEqual(_value(mapping[0], "a"), "ref=bg0")

    def test_region_report_batch_combines(self) -> None:
        sketch = _holed_sketch()
        report = _blocks(EX.region_report_batch(sketch, regions=[None]))
        area = _blocks(EX.measure_area_batch(sketch, regions=[None]))
        self.assertEqual(len(report), 1)
        self.assertEqual(_value(report[0], "loops"), "2")
        self.assertEqual(_value(report[0], "area_nm2"), _value(area[0], "area_nm2"))
        self.assertEqual(_value(report[0], "perimeter_nm"), _value(area[0], "perimeter_nm"))
        self.assertEqual(set(_value(report[0], "boundaries").split(",")),
                         {"bg0", "bg1", "bg2", "bg3", "bgc"})
        empty = _blocks(EX.region_report_batch(_open_sketch(), regions=[None]))[0]
        self.assertEqual(_value(empty, "loops"), "0")
        self.assertEqual(_value(empty, "boundaries"), "NA")
        self.assertEqual(_value(empty, "area_nm2"), "NA")

    def test_read_only_sketch_and_doc(self) -> None:
        sketch = _holed_sketch()
        before = copy.deepcopy(sketch)
        EX.boundary_detect_batch(sketch, regions=[None])
        EX.measure_area_batch(sketch, regions=[0, 1])
        EX.measure_perimeter_batch(sketch, regions=[None])
        EX.measure_distance_batch(sketch, pairs=[("bg0", "bgc")])
        EX.region_report_batch(sketch, regions=[None])
        self.assertEqual(sketch, before)
        doc = {"sketch": _holed_sketch(), "extra": {}}
        doc_before = copy.deepcopy(doc)
        EX.boundary_detect_batch(doc, regions=[None])
        EX.measure_area_batch(doc, regions=[None])
        EX.region_report_batch(doc, regions=[None])
        self.assertEqual(doc, doc_before)
        self.assertNotIn(HM._HM_KEY, doc["extra"])

    def test_error_paths_reasons_and_item_detail(self) -> None:
        probes = ((lambda: EX.measure_area_batch(_rect_sketch(), regions=[5]), "index_out_of_range", 0),
                  (lambda: EX.measure_area_batch(_rect_sketch(), regions=[]), "empty_selection", None),
                  (lambda: EX.measure_area_batch(_rect_sketch(), regions=2.5), "invalid_argument", None),
                  (lambda: EX.measure_distance_batch(_rect_sketch(), pairs=None), "invalid_argument", None),
                  (lambda: EX.measure_distance_batch(_rect_sketch(), pairs=[]), "empty_selection", None),
                  (lambda: EX.measure_distance_batch(_rect_sketch(), pairs=[(1, 2, 3)]), "invalid_argument", 0),
                  (lambda: EX.boundary_detect_batch(_rect_sketch(), regions=[["nope"]]),
                   "unknown_reference", 0),
                  (lambda: EX.boundary_detect_batch(_rect_sketch(), regions=[None, []]),
                   "empty_selection", 1))
        for probe, reason, item in probes:
            with self.assertRaises(CadError) as caught:
                probe()
            self.assertEqual(caught.exception.reason, reason)
            if item is not None:
                self.assertEqual(caught.exception.details.get("item"), item, reason)
        with self.assertRaises(CadError) as caught:
            EX.boundary_detect_batch(_rect_sketch(), regions=[None], tol_nm="not-a-length")
        self.assertTrue(caught.exception.reason)
        with self.assertRaises(CadError) as caught:
            EX.measure_area_batch(_rect_sketch(), regions=[None] * 4097)
        self.assertEqual(caught.exception.reason, "too_many_items")

    def test_hatch_fill_batch_writes_lines_and_keeps_boundary(self) -> None:
        sketch = _holed_sketch()
        boundary_before = copy.deepcopy(sketch["entities"][:5])
        created = EX.hatch_fill_batch(sketch, regions=[0, 1], spacing_nm=5 * MM)
        self.assertTrue(created)
        self.assertEqual(len(set(created)), len(created))
        self.assertEqual(sketch["entities"][:5], boundary_before)
        kinds = {entity["uuid"]: entity["kind"] for entity in sketch["entities"]}
        layers = {entity["uuid"]: entity["layer"] for entity in sketch["entities"]}
        for uuid in created:
            self.assertEqual(kinds[uuid], "line")
            self.assertEqual(layers[uuid], HM.DEFAULT_HATCH_LAYER)
        records = HM.hatch_list(sketch)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record.startswith("hatch=") for record in records))

    def test_hatch_fill_batch_doc_target_lands_in_extra(self) -> None:
        sketch = _rect_sketch()
        doc = {"sketch": sketch, "extra": {}}
        created = EX.hatch_fill_batch(doc, regions=[None], spacing_nm=10 * MM)
        self.assertTrue(created)
        self.assertIn(HM._HM_KEY, doc["extra"])
        records = doc["extra"][HM._HM_KEY]["hatches"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lines"], list(created))
        self.assertEqual(set(records[0]["boundary"]), {"bg0", "bg1", "bg2", "bg3"})
        self.assertNotIn(HM._HM_KEY, sketch)

    def test_hatch_fill_batch_precheck_no_partial(self) -> None:
        open_sketch = _open_sketch()
        open_before = copy.deepcopy(open_sketch)
        with self.assertRaises(CadError) as caught:
            EX.hatch_fill_batch(open_sketch, regions=[None])
        self.assertEqual(caught.exception.reason, "no_boundary")
        self.assertEqual(caught.exception.details.get("item"), 0)
        self.assertEqual(open_sketch, open_before)
        mixed = _holed_sketch()
        mixed["entities"].extend(_open_entities(origin=(200 * MM, 0)))
        mixed_before = copy.deepcopy(mixed)
        with self.assertRaises(CadError) as caught:
            EX.hatch_fill_batch(mixed, regions=[0, ["oc0", "oc1", "oc2"]])
        self.assertEqual(caught.exception.reason, "no_boundary")
        self.assertEqual(caught.exception.details.get("item"), 1)
        self.assertEqual(mixed, mixed_before)

    def test_module_main_prints_extra_ops_verdict(self) -> None:
        module_path = pathlib.Path(EX.__file__)
        proc = subprocess.run([sys.executable, str(module_path)], cwd=str(ROOT),
                              capture_output=True, text=True)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertGreaterEqual(len(lines), 2)
        self.assertTrue(any(line.startswith("check ") for line in lines))
        self.assertEqual(lines[-1], "EXTRA_OPS verdict=PASS ops=%d" % len(EX.OPERATIONS))


def _main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful()
    print("HATCH_MEASURE_EXTRA_TESTS verdict=%s tests=%d failures=%d errors=%d"
          % ("PASS" if passed else "FAIL", result.testsRun, len(result.failures), len(result.errors)))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(_main())
