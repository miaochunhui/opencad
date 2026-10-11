#!/usr/bin/env python3
"""B6-ROUND2 席：另两条 DXF 写出路径的三项修复 —— 回归断言 + 证据读数（同一台尺子）。

被测（真调用，不落盘、不改树）：
  1. `cadcore.draw.sheet._builtin_dxf`：R12（AC1009）产物**不得**含 `LWPOLYLINE`（R14+）与
     组码 370（R2000+）；inch 坐标用 `Decimal` 精确除法写 17 位有效数字（旧口径 6 位定点
     的量化步长是 25.4 nm）。
  2. `cadcore.ops.output._dxf_render`：inch + 缺省 `precision=6` 的坐标同办（mm 产物逐字节不变）。
  3. 图层名/线型名含 CR/LF/控制字符 ⇒ **结构化拒绝**（`reason=dxf_illegal_symbol_name`），
     不得静默截断/组码注入（A3 缺陷 RC：`"A\\n999\\ninjected"`）。

判据（与上一席 `b6_secondary_writers_probe.py` 同一把尺子）：真源 `Fraction(nm, per_unit)`；
inch 的 1 inch = 25 400 000 nm **含 127 因子 ⇒ 十进制不终止**，所以"精确命中"存在数学上限
（只有 `127 | nm` 的分量才可能命中），本席另报 **nm 往返无损**（`round(text * per_unit) == nm`）。

用法::

    python3 tests/test_b6_round2_secondary_writers.py     # 证据读数 + 本文件全部断言
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from decimal import Decimal
from fractions import Fraction

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cadcore.draw import sheet as dsheet                      # noqa: E402
from cadcore.errors import CadError                           # noqa: E402
from cadcore.ops import output as output_ops                  # noqa: E402

MM = 1_000_000
PLATE = os.path.join(_HERE, "data", "plate.json")
PRIOR_PROBE = "/hf3fs-jd/data/tmp/dxf-scan/b6_secondary_writers_probe.py"


# --------------------------------------------------------------------------------------
# 与上一席探针同口径的夹具/读数（逐行同源，便于两组数字直接对比）
# --------------------------------------------------------------------------------------
def _pairs(payload: str) -> list[tuple[int, str]]:
    lines = payload.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [(int(lines[i].strip()), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


def _values(payload: str) -> list[str]:
    return [value for code, value in _pairs(payload) if code in (10, 20, 40)]


def _sheet_for(unit: str, polyline_layer: str = "0", linetype: str = "continuous"):
    """plate.json + 一条显式 L1 多段线（`make_sheet` 只给图框/标题栏，不投视图量不到多段线）。"""
    with open(PLATE, encoding="utf-8") as handle:
        document = json.load(handle)
    document["unit"] = unit
    sketch = document["sketches"][0]
    sketch["entities"].append({"uuid": "b6b-poly", "kind": "polyline", "layer": polyline_layer,
                               "construction": False,
                               "geom": {"points": [[0, 0], [10 * MM, 0], [10 * MM, 5 * MM]],
                                        "closed": False}})
    sheet = dsheet.make_sheet(document, standard="gbt", language="zh-CN")
    if linetype != "continuous":
        sheet.layers[polyline_layer if polyline_layer in sheet.layers else "0"] = {
            "name": polyline_layer if polyline_layer in sheet.layers else "0", "linetype": linetype}
    from cadcore.kernel import nano as _nano
    dsheet.add_view(sheet, document, view="front", at_nm=(20 * MM, 20 * MM),
                    kernel=_nano.make_kernel(), hidden=True)
    return document, sheet


def _builtin_truths(sheet) -> list[int]:
    truths: list[int] = []
    for item in dsheet.iter_entities(sheet):
        kind = item.get("type")
        if kind == "LINE":
            truths += [item["start"][0], item["start"][1], item["end"][0], item["end"][1]]
        elif kind in ("CIRCLE", "ARC"):
            truths += [item["center"][0], item["center"][1], item["radius_nm"]]
        elif kind == "LWPOLYLINE":
            for x, y in item["points"]:
                truths += [x, y]
    return truths


def _render_truths(document) -> list[int]:
    truths: list[int] = []
    for entity in output_ops._iter_entities(document):
        geom = entity.get("geom") or {}
        if all(key in geom for key in ("x1", "y1", "x2", "y2")):
            truths += [geom["x1"], geom["y1"], geom["x2"], geom["y2"]]
        elif "cx" in geom and "cy" in geom:
            radius = geom.get("r", geom.get("radius"))
            truths += [geom["cx"], geom["cy"]] + ([radius] if radius is not None else [])
        else:
            for pair in geom.get("points_nm") or []:
                truths += [pair[0], pair[1]]
    return truths


def _parsed(values) -> list[Fraction]:
    out = []
    for value in values:
        try:
            out.append(Fraction(Decimal(str(value).strip())))
        except Exception:                                       # noqa: BLE001
            continue
    return out


def _nearest(parsed, reference: Fraction) -> Fraction:
    return min(parsed, key=lambda item: abs(item - reference))


def nm_roundtrip(truths, values, per_unit) -> tuple[int, int, float]:
    """`(nm 往返无损命中数, 比对条数, 最大相对漂移)` —— 与上一席同尺子的相对差。"""
    parsed = _parsed(values)
    hits, worst = 0, 0.0
    for nm in truths:
        reference = Fraction(int(nm), per_unit)
        nearest = _nearest(parsed, reference)
        if int(round(nearest * per_unit)) == int(nm):
            hits += 1
        if reference != 0:
            worst = max(worst, float(abs(nearest - reference) / abs(reference)))
    return hits, len(truths), worst


def exact_ceiling(truths, per_unit: int) -> int:
    """精确命中的**数学上限**：十进制有限位等于 `nm/per_unit` ⇒ 分母（约分后）只含 2/5。

    inch 的 `25400000 = 2**6 * 5**5 * 127` ⇒ 只有 `127 | nm` 的分量可能被十进制原文精确表示。
    """
    denominator = per_unit
    while denominator % 2 == 0:
        denominator //= 2
    while denominator % 5 == 0:
        denominator //= 5
    return sum(1 for nm in truths if int(nm) % max(denominator, 1) == 0)


def _code_present(payload: str, code: int) -> bool:
    return any(item == code for item, _ in _pairs(payload))


class BuiltinDxfRound2Test(unittest.TestCase):
    """修复 1：降级写出器（`draw.sheet._builtin_dxf`）。"""

    def test_r12_has_no_r14_plus_structures(self):
        _document, sheet = _sheet_for("mm")
        payload = dsheet._builtin_dxf(sheet, version="R12").decode("utf-8")
        self.assertNotIn("LWPOLYLINE", payload, "R12（AC1009）不得出现 R14+ 的 LWPOLYLINE")
        self.assertIn("POLYLINE", payload, "降级后必须有 R12 合法的 POLYLINE")
        self.assertGreaterEqual(payload.count("0\nVERTEX\n"), 3, "降级后必须有 R12 的顶点表")
        self.assertIn("0\nSEQEND\n", payload)
        self.assertFalse(_code_present(payload, 370), "R12 不得出现 R2000+ 的组码 370")
        # 阳性对照：R2000 仍必须写 LWPOLYLINE + 370（不是把功能删了）
        recent = dsheet._builtin_dxf(sheet, version="R2000").decode("utf-8")
        self.assertIn("LWPOLYLINE", recent)
        self.assertTrue(_code_present(recent, 370))

    def test_inch_coordinates_lossless_to_nanometre(self):
        for unit, per_unit in (("inch", 25_400_000), ("mm", MM)):
            _document, sheet = _sheet_for(unit)
            payload = dsheet._builtin_dxf(sheet, version="R12").decode("utf-8")
            truths = _builtin_truths(sheet)
            hits, checked, worst = nm_roundtrip(truths, _values(payload), per_unit)
            self.assertEqual(checked, len(truths))
            self.assertGreater(len(truths), 100)
            self.assertEqual(hits, checked, f"{unit}: 坐标必须 nm 往返无损")
            self.assertLess(worst, 1e-15, f"{unit}: 相对漂移必须 << 1 nm（实测 {worst}）")
            if unit == "mm":                       # 阳性对照：mm 的 6 位定点逐位精确
                parsed = set(_parsed(_values(payload)))
                exact = sum(1 for nm in truths if Fraction(int(nm), MM) in parsed)
                self.assertEqual(exact, checked)
                self.assertEqual(worst, 0.0)


class RenderDxfRound2Test(unittest.TestCase):
    """修复 2：`ops.output._dxf_render`。"""

    def test_render_inch_lossless_and_mm_unchanged(self):
        for unit, plugin_unit, per_unit in (("inch", "in", 25_400_000), ("mm", "mm", MM)):
            with open(PLATE, encoding="utf-8") as handle:
                document = json.load(handle)
            document["unit"] = plugin_unit
            text, _count = output_ops._dxf_render(document, version="R12", precision=6,
                                                  unit=plugin_unit)
            truths = _render_truths(document)
            hits, checked, worst = nm_roundtrip(truths, _values(text), per_unit)
            self.assertEqual(checked, len(truths))
            self.assertGreater(len(truths), 10)
            self.assertEqual(hits, checked, f"{unit}: 坐标必须 nm 往返无损")
            self.assertLess(worst, 1e-15, f"{unit}: 相对漂移必须 << 1 nm（实测 {worst}）")
            if unit == "mm":                       # 旧口径产物逐字节不变（6 位定点）
                self.assertIn("\n10.000000\n", text)

    def test_render_r12_uses_polyline(self):
        with open(PLATE, encoding="utf-8") as handle:
            document = json.load(handle)
        text, _count = output_ops._dxf_render(document, version="R12", precision=6, unit="mm")
        self.assertNotIn("LWPOLYLINE", text)


class LayerNameInjectionTest(unittest.TestCase):
    """修复 3：图层名/线型名/组码值不得跨行（结构化拒绝，不静默截断）。"""

    INJECTED = "A\n999\ninjected"

    def test_builtin_rejects_multiline_layer_name(self):
        _document, sheet = _sheet_for("mm")
        sheet.layers[self.INJECTED] = {"name": self.INJECTED, "linetype": "continuous"}
        with self.assertRaises(CadError) as caught:
            dsheet._builtin_dxf(sheet, version="R12")
        self.assertEqual(getattr(caught.exception, "reason", None), "dxf_illegal_symbol_name")
        self.assertEqual(getattr(caught.exception, "group_code", None), 2)

    def test_builtin_rejects_multiline_linetype_name(self):
        _document, sheet = _sheet_for("mm")
        sheet.layers["bad-ltype"] = {"name": "bad-ltype", "linetype": "X\r999"}
        with self.assertRaises(CadError) as caught:
            dsheet._builtin_dxf(sheet, version="R12")
        self.assertEqual(getattr(caught.exception, "reason", None), "dxf_illegal_symbol_name")
        self.assertEqual(getattr(caught.exception, "group_code", None), 6)

    def test_render_rejects_multiline_layer_name(self):
        with open(PLATE, encoding="utf-8") as handle:
            document = json.load(handle)
        entities = document["sketches"][0]["entities"]
        entities[0]["layer"] = self.INJECTED
        with self.assertRaises(CadError) as caught:
            output_ops._dxf_render(document, version="R12", precision=6, unit="mm")
        self.assertEqual(getattr(caught.exception, "reason", None), "dxf_illegal_symbol_name")


# --------------------------------------------------------------------------------------
# 证据模式（`python3 tests/test_b6_round2_secondary_writers.py`）：原样重跑上一席探针 + 扩展读数
# --------------------------------------------------------------------------------------
def _load_prior():
    import importlib.util
    if not os.path.exists(PRIOR_PROBE):
        return None
    spec = importlib.util.spec_from_file_location("b6_prior_probe", PRIOR_PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence() -> None:
    prior = _load_prior()
    if prior is None:
        print("ROUND2 evidence: prior probe missing -> NA")
        return
    for unit in ("inch", "mm"):
        per_unit = 25_400_000 if unit == "inch" else MM
        headline = prior.probe_builtin(unit)
        _document, sheet = _sheet_for(unit)
        payload = dsheet._builtin_dxf(sheet, version="R12").decode("utf-8")
        truths = _builtin_truths(sheet)
        hits, checked, worst = nm_roundtrip(truths, _values(payload), per_unit)
        print(json.dumps({
            "writer": "draw.sheet._builtin_dxf", "unit": unit, "version": "R12",
            "prior_ruler_exact_hits": headline["exact_hits"], "checked": headline["checked_components"],
            "prior_ruler_max_rel_drift": headline["max_rel_drift"],
            "nm_roundtrip_hits": f"{hits}/{checked}", "nm_roundtrip_max_rel_drift": worst,
            "exact_ceiling_127_divisible": f"{exact_ceiling(truths, per_unit)}/{checked}",
            "lwpolyline_in_r12": headline["lwpolyline_in_r12"],
            "group370_in_r12": _code_present(payload, 370)}, ensure_ascii=False, sort_keys=True))

        rendered = prior.probe_output_plugin(unit)
        with open(PLATE, encoding="utf-8") as handle:
            document = json.load(handle)
        document["unit"] = "in" if unit == "inch" else unit
        text, _count = output_ops._dxf_render(document, version="R12", precision=6,
                                               unit=document["unit"])
        r_truths = _render_truths(document)
        r_hits, r_checked, r_worst = nm_roundtrip(r_truths, _values(text), per_unit)
        print(json.dumps({
            "writer": "ops.output._dxf_render", "unit": unit, "version": "R12", "precision": 6,
            "prior_ruler_exact_hits": rendered["exact_hits"], "checked": rendered["checked_components"],
            "prior_ruler_max_rel_drift": rendered["max_rel_drift"],
            "nm_roundtrip_hits": f"{r_hits}/{r_checked}", "nm_roundtrip_max_rel_drift": r_worst,
            "exact_ceiling_127_divisible": f"{exact_ceiling(r_truths, per_unit)}/{r_checked}",
            "lwpolyline_in_r12": "LWPOLYLINE" in text}, ensure_ascii=False, sort_keys=True))

    rejected = []
    for label, call in (("sheet.layer", lambda: _layer_case()),
                        ("sheet.linetype", lambda: _linetype_case()),
                        ("render.layer", lambda: _render_layer_case())):
        try:
            call()
            rejected.append(f"{label}=NOT_REJECTED")
        except CadError as exc:
            rejected.append(f"{label}={getattr(exc, 'reason', 'no_reason')}")
    print("ROUND2 layer-name-injection " + " ".join(rejected))


def _layer_case():
    _document, sheet = _sheet_for("mm")
    sheet.layers["A\n999\ninjected"] = {"name": "A\n999\ninjected", "linetype": "continuous"}
    dsheet._builtin_dxf(sheet, version="R12")


def _linetype_case():
    _document, sheet = _sheet_for("mm")
    sheet.layers["bad-ltype"] = {"name": "bad-ltype", "linetype": "X\r999"}
    dsheet._builtin_dxf(sheet, version="R12")


def _render_layer_case():
    with open(PLATE, encoding="utf-8") as handle:
        document = json.load(handle)
    document["sketches"][0]["entities"][0]["layer"] = "A\n999\ninjected"
    output_ops._dxf_render(document, version="R12", precision=6, unit="mm")


if __name__ == "__main__":
    _evidence()
    _program = unittest.main(module=__name__, argv=[sys.argv[0]], exit=False, verbosity=2)
    raise SystemExit(0 if _program.result.wasSuccessful() else 1)
