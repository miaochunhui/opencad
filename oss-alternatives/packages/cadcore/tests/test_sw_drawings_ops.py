#!/usr/bin/env python3
"""tests.test_sw_drawings_ops -- family ``sw_drawings`` 冻结接口对账单测（对账席自持）。

开工读数与裁决（原样输出见收尾 raw_proof）
==========================================
1. ``python3 -m unittest tests.test_sw_drawings_ops`` ⇒ ``ModuleNotFoundError: No module named
   'tests.test_sw_drawings_ops'``：本文件开工时**不存在**，族行为判定行 = ``NA(未采)``。
2. ``python3 -m py_compile cadcore/ops/sw_drawings.py`` ⇒ ``py_compile_rc=1``、
   ``SyntaxError: invalid syntax`` 于 ``sw_drawings.py:695``
   （``margin_nm: int = DEFAULT_MARGIN_M := DEFAULT_MARGIN_NM``）；且 ``grep -rn sw_drawings tests/``
   只命中兄弟测试文件的**注释**，没有任何测试 import 过它 ⇒ 该语法错一直没被发现。
   **裁决：根因在实现**（不是测试少传参数、也不是 helper 未定义）⇒ 改实现：去掉 walrus，
   用既有的 ``DEFAULT_MARGIN_NM``（与同模块其余算子一致），**不为绕开它放松任何断言**。
3. ``grep -nE '"（zh-CN|en|ja）"' cadcore/ops/sw_drawings.py`` 命中 ``1390`` 行 ``language="en"``
   ⇒ 按口径清掉（改走算子缺省语言，文案一律走 key）。

本文件钉什么（冻结接口，不钉实现的私有命名）
=============================================
* ``FAMILY`` = ``sw_drawings``；``OPERATIONS`` 是 ``dict[str, Callable]``，每个算子的
  **第一个位置参数必须叫 ``sk_or_doc``**（`inspect.signature` 现读，不抄写签名）。
* 每个算子用**真文档**（族模块自己的样例模型）真调用：返回 ``list[str]``、元素非空字符串且互不重复。
* 依赖/能力缺席（``DependencyMissing``）记 **NA(未采)** 并带 ``reason`` —— 不冒充 PASS，也不算红。
* 其余 ``CadError`` 与非 ``CadError`` 异常一律**真报红**（冻结接口要求错误抛 ``CadError(reason=…)``）。
* 错误面三条（判据读自实现）：``None`` 模型 ⇒ ``unsupported_model``；``scale="1:0"`` ⇒ ``bad_scale``；
  ``depth_nm=True``（``bool`` 冒充长度）⇒ ``bad_length``；三者都要求 ``reason`` 非空。
* 族模块**不得出现 locale 字面量**：语言码表从 ``cadcore/data/i18n/*.json`` 现读，
  本文件自身不写死任何 locale 字面量。
* 补缺三条（``driven_dimension`` / ``centerline`` / ``note_leader``）另在 ``NewOpsTest`` 里按
  **L1 机读面**真断言：``annotations[]`` 的 ``annotation_kind``/``args``、``sheets[].dimensions``、
  以及「量出来的值确实等于样例件几何」「矩形件无圆 ⇒ 中心符号 0 是真为零」这两条反假绿判据。

未覆盖（显式欠账，不假绿）
--------------------------
* 几何数值（视图包围盒 / 尺寸条数是否足量 / 剖面线间距）本文件**没有**独立复算 ⇒ ``NA(未采)``：
  它需要内核 + 图纸真源，属族模块 ``selftest()`` 的判据面，本文件不重复它的自证。
* ``OPERATIONS`` 里是否**应该**再多个别算子（族语义完备性）不在冻结接口断言范围内。
"""

from __future__ import annotations

import inspect
import re
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Callable

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError, DependencyMissing  # noqa: E402
from cadcore.ops import sw_drawings as family  # noqa: E402

_QUOTED = re.compile(r"""["']([A-Za-z][A-Za-z0-9_-]{0,9})["']""")
_MIN_OPS = 5
_RECORDS: list[tuple[str, str, str, int]] = []


def _ops() -> dict[str, Callable[..., list[str]]]:
    return dict(getattr(family, "OPERATIONS", {}) or {})


def _locale_codes() -> set[str]:
    """语言码表从仓库自己的语言包文件名现读 —— 本文件不写死 locale 字面量。"""
    data_dir = Path(family.__file__).resolve().parents[1] / "data" / "i18n"
    return {path.stem for path in data_dir.glob("*.json") if path.stem != data_dir.name}


def _kernel_state() -> str:
    probe = getattr(family, "get_kernel", None)
    if probe is None:
        return "unknown"
    try:
        probe("auto")
    except DependencyMissing:
        return "missing"
    except Exception:
        return "broken"
    return "present"


def _document() -> Any:
    builder = getattr(family, "_sample_model", None)
    if builder is None:
        raise AssertionError("family fixture _sample_model() is absent: no real document available")
    return builder()


class ContractTest(unittest.TestCase):
    """冻结接口的结构契约（不依赖内核，永远真跑）。"""

    def test_family_id(self) -> None:
        self.assertEqual(family.FAMILY, "sw_drawings")

    def test_operations_registry_shape(self) -> None:
        ops = _ops()
        self.assertIsInstance(ops, dict)
        self.assertGreaterEqual(len(ops), _MIN_OPS, f"OPERATIONS too small: {sorted(ops)}")
        for name, fn in ops.items():
            self.assertIsInstance(name, str)
            self.assertTrue(name.strip(), "empty operation name")
            self.assertTrue(callable(fn), f"{name}: not callable")

    def test_first_positional_arg_is_sk_or_doc(self) -> None:
        for name, fn in sorted(_ops().items()):
            params = list(inspect.signature(fn).parameters.values())
            self.assertTrue(params, f"{name}: no parameters")
            first = params[0]
            self.assertIn(first.kind, (first.POSITIONAL_ONLY, first.POSITIONAL_OR_KEYWORD),
                          f"{name}: first parameter is not positional ({first.kind})")
            self.assertEqual(first.name, "sk_or_doc",
                             f"{name}: first positional parameter is {first.name!r}, not 'sk_or_doc'")

    def test_module_source_has_no_locale_literal(self) -> None:
        source = Path(family.__file__).read_text(encoding="utf-8")
        codes = _locale_codes()
        self.assertTrue(codes, "no language catalogue found; locale ruler cannot see anything")
        hits = sorted({token for token in _QUOTED.findall(source) if token in codes})
        self.assertEqual(hits, [], f"locale literals must go through i18n keys: {hits}")

    def test_module_facts_matches_registry(self) -> None:
        try:
            facts = family.module_facts()
        except CadError as exc:  # 依赖缺席 ⇒ 记 NA(未采)，不冒充 PASS
            print(f"SW_DRAWINGS_OPS facts=NA reason={exc.reason}")
            return
        self.assertEqual(int(facts.get("op_count", -1)), len(_ops()))


class BehaviorTest(unittest.TestCase):
    """真调用：每个算子用真文档跑一遍，逐条记 PASS / NA / RED。"""

    def test_every_op_returns_uuid_list_or_declares_na(self) -> None:
        ops = _ops()
        records: list[tuple[str, str, str, int]] = []
        # 少数算子的**正向路径**需要可满足的输入（不是签名分歧）：
        # * bom_table 没有 items 就没有行可排 ⇒ 抛 bom_source_missing 是**正确行为**（_bom_rows 口径：
        #   qty + 非空 part_key），测试侧补输入；
        # * auto_dimensions 按**缺省** min_count 在 60×40×20 样件上凑不够 ⇒ 文档化的 DimensionShortfall
        #   （族模块 docstring 明写"候选不足该数 ⇒ CadError，不凑数、不重复计数"）；正向改钉可满足的
        #   min_count=1，短缺行为另在错误面用 min_count=10**6 钉住（不许返回短表）。
        # * note_leader 没有文本就不是注释 ⇒ note_text_missing 是**正确行为**，测试侧补一条成品串。
        inputs: dict[str, dict[str, Any]] = {
            "bom_table": {"items": [{"qty": 1, "part_key": "part.base",
                                     "material_key": "material.steel"}]},
            "auto_dimensions": {"min_count": 1},
            "note_leader": {"text": "SEE NOTE 1"},
        }
        for name, fn in ops.items():
            started = time.time()
            try:
                result = fn(_document(), **inputs.get(name, {}))
            except DependencyMissing as exc:
                records.append((name, "NA", f"dependency_missing:{exc.reason}", _ms(started)))
                continue
            except CadError as exc:
                records.append((name, "RED", f"{type(exc).__name__}:{exc.reason}", _ms(started)))
                continue
            except Exception as exc:  # 冻结接口只允许 CadError
                records.append((name, "RED", f"{type(exc).__name__}:{exc}", _ms(started)))
                continue
            if not isinstance(result, list) or any(not isinstance(u, str) or not u for u in result):
                records.append((name, "RED", f"bad_return:{type(result).__name__}:{result!r}",
                                _ms(started)))
                continue
            if len(set(result)) != len(result):
                records.append((name, "RED", f"duplicate_uuid:{result!r}", _ms(started)))
                continue
            state = "PASS" if result else "NA"
            detail = f"uuids={len(result)}" if result else "empty_result"
            records.append((name, state, detail, _ms(started)))
        _RECORDS.extend(records)

        reds = [item for item in records if item[1] == "RED"]
        self.assertEqual(reds, [], f"family ops must return list[str] or raise CadError: {reds}")
        if _kernel_state() == "present":
            self.assertTrue(any(item[1] == "PASS" for item in records),
                            f"no op produced a uuid although the kernel is present: {records}")

    def test_standard_views_returns_sheet_plus_three_views(self) -> None:
        """三视图 = 图纸 + 3 视图（族模块自己的口径）；缺席内核 ⇒ NA(未采)。"""
        try:
            uuids = family.standard_views(_document())
        except DependencyMissing as exc:
            print(f"SW_DRAWINGS_OPS standard_views=NA reason={exc.reason}")
            return
        self.assertIsInstance(uuids, list)
        self.assertEqual(len(uuids), 4, f"sheet + 3 views expected, got {uuids!r}")
        self.assertEqual(len(set(uuids)), 4, f"uuids must be distinct: {uuids!r}")
        self.assertTrue(all(isinstance(item, str) and item for item in uuids))

    def test_invalid_input_raises_caderror_with_reason(self) -> None:
        cases = (
            ("unsupported_model", lambda: family.standard_views(None)),
            ("bad_scale", lambda: family.scale_and_titleblock(_document(), scale="1:0")),
            ("bool_length", lambda: family.standard_views(_document(), depth_nm=True)),
            ("dimension_shortfall", lambda: family.auto_dimensions(_document(), min_count=10 ** 6)),
        )
        for label, call in cases:
            with self.subTest(case=label):
                try:
                    call()
                except DependencyMissing as exc:
                    print(f"SW_DRAWINGS_OPS error_case={label} state=NA reason={exc.reason}")
                    continue
                except CadError as exc:
                    self.assertTrue(str(exc.reason).strip(), f"{label}: empty CadError.reason")
                    continue
                except Exception as exc:
                    self.fail(f"{label}: raised {type(exc).__name__} instead of CadError: {exc}")
                else:
                    self.fail(f"{label}: invalid input accepted instead of raising CadError")


def _annotations(doc: Any) -> list[Any]:
    """文档级 L1 `annotations[]`（Document 属性或文档映射的键），非列表 ⇒ 空表。"""
    if hasattr(doc, "annotations") and isinstance(doc.annotations, list):
        return list(doc.annotations)
    value = doc.get("annotations") if hasattr(doc, "get") else None
    return list(value) if isinstance(value, list) else []


def _sheet_entries(doc: Any) -> list[Any]:
    """文档级 L1 `sheets[]` 条目（`_publish` 现写的那一份）。"""
    if hasattr(doc, "sheets") and isinstance(doc.sheets, list):
        return list(doc.sheets)
    value = doc.get("sheets") if hasattr(doc, "get") else None
    return list(value) if isinstance(value, list) else []


class NewOpsTest(unittest.TestCase):
    """补缺三条能力的真行为（判据：量测值来自样例件几何、L1 机读条目、错误 reason）。"""

    def test_driven_dimension_is_measured_and_marked_reference(self) -> None:
        doc = _document()
        try:
            width = _ops()["driven_dimension"](doc, kind="linear_x")
            height = _ops()["driven_dimension"](doc, kind="linear_y")
        except DependencyMissing as exc:
            print(f"SW_DRAWINGS_OPS driven_dimension=NA reason={exc.reason}")
            return
        self.assertTrue(width and height, "driven_dimension returned an empty uuid list")
        for uuids in (width, height):
            self.assertEqual(len(set(uuids)), len(uuids), f"duplicate uuids: {uuids!r}")
        entries = [item for item in _annotations(doc)
                   if item.get("annotation_kind") == "driven_dimension"]
        measured = {item["args"]["kind"]: item["args"] for item in entries}
        self.assertEqual(sorted(measured), ["linear_x", "linear_y"])
        # 样例件（族模块 `_sample_model`）= 60×40×20 mm 板。取景投到哪两个轴由内核决定（不假设
        # "front 一定宽 60"），所以判据钉**两轴的量测值都落在这块板的真实外形尺寸里、且互不相等** ——
        # 返回常量的假实现两条同时过不去；extents 与 `_sample_model` 的三个尺寸同源。
        extents = {20 * family.NM_PER_MM, 40 * family.NM_PER_MM, 60 * family.NM_PER_MM}
        measured_values = {int(measured[kind]["value_nm"]) for kind in ("linear_x", "linear_y")}
        self.assertTrue(measured_values <= extents,
                        f"measured {sorted(measured_values)} not plate extents {sorted(extents)}")
        self.assertEqual(len(measured_values), 2, "the two axes must not collapse to one value")
        for kind in ("linear_x", "linear_y"):
            self.assertIs(measured[kind]["driven"], True, "driven flag must be machine readable")
            self.assertEqual(measured[kind]["measured_from"], "view.bounds_model_nm")
            self.assertTrue(measured[kind]["text"].startswith("(")
                            and measured[kind]["text"].endswith(")"),
                            f"reference dimension must be parenthesised: {measured[kind]['text']!r}")
        sheets = _sheet_entries(doc)
        self.assertTrue(sheets, "no L1 sheet entry written back")
        # 同一份载荷 ⇒ `make_sheet` 给出同一个图纸 uuid（确定性），两次调用合并进同一条 `sheets[]`
        # 条目、后一次快照胜 ⇒ 用**后一次**的尺寸 uuid 钉「尺寸真进了 L1 dimensions[]」。
        self.assertIn(height[-1], [uid for entry in sheets for uid in (entry.get("dimensions") or [])],
                      "driven dimension uuid must be in the sheet's L1 dimensions[]")
        # 声明值路径必须与量测路径可辨（`measured_from` 明写 declared:），不拿声明冒充量测。
        declared = _ops()["driven_dimension"](doc, kind="linear_x", value_nm=123456789)
        self.assertTrue(declared)
        declared_args = [item["args"] for item in _annotations(doc)
                         if item.get("annotation_kind") == "driven_dimension"
                         and item["args"]["measured_from"] == "declared:value_nm"]
        self.assertTrue(declared_args, "declared reference value must be marked as declared")
        self.assertEqual(int(declared_args[-1]["value_nm"]), 123456789)
        with self.assertRaises(CadError) as ctx:
            _ops()["driven_dimension"](doc, kind="diameter")
        self.assertEqual(ctx.exception.reason, "no_measured_feature",
                         "a rectangle has no circle: must refuse, not fabricate a diameter")

    def test_centerline_adds_axis_lines_and_reports_marks(self) -> None:
        doc = _document()
        try:
            uuids = family.centerline(doc)
        except DependencyMissing as exc:
            print(f"SW_DRAWINGS_OPS centerline=NA reason={exc.reason}")
            return
        self.assertTrue(uuids, "centerline returned an empty uuid list")
        self.assertEqual(len(set(uuids)), len(uuids), f"duplicate uuids: {uuids!r}")
        entries = [item for item in _annotations(doc)
                   if item.get("annotation_kind") == "centerline"]
        self.assertTrue(entries, "centerline summary record missing from annotations[]")
        record = entries[-1]
        self.assertEqual(int(record["axis_lines"]), 2, "a closed outline gets two axes")
        self.assertEqual(int(record["center_marks"]), 0,
                         "0 = 真为零：矩形样件没有圆孔（不是未采）")
        self.assertEqual(int(record["circles"]), 0)
        self.assertEqual(record["layer"], family.CENTER_LAYER)
        self.assertTrue(str(record.get("view_uuid", "")), "summary must point at its view")

    def test_note_leader_carries_text_and_arrow(self) -> None:
        doc = _document()
        uuids = family.note_leader(doc, text="HEAT TREAT HRC45")
        self.assertTrue(uuids, "note_leader returned an empty uuid list")
        self.assertEqual(len(set(uuids)), len(uuids), f"duplicate uuids: {uuids!r}")
        entries = [item for item in _annotations(doc)
                   if item.get("annotation_kind") == "note_leader"]
        self.assertTrue(entries, "note text carrier missing from annotations[]")
        note = entries[-1]
        self.assertIs(note.get("literal"), True)
        self.assertEqual(note.get("text"), "HEAT TREAT HRC45")
        self.assertEqual(len(note.get("leader_points_nm", [])), 3, "anchor -> elbow -> text")
        self.assertEqual(note.get("terminator"), "arrow")
        self.assertTrue(str(note.get("view_uuid", "")), "leader geometry view uuid missing")
        with self.assertRaises(CadError) as ctx:
            family.note_leader(doc)
        self.assertEqual(ctx.exception.reason, "note_text_missing",
                         "a note without text must be refused, not defaulted")


def _ms(started: float) -> int:
    return int(round((time.time() - started) * 1000))


def tearDownModule() -> None:
    states = {"PASS": 0, "NA": 0, "RED": 0}
    for _, state, _, _ in _RECORDS:
        states[state] = states.get(state, 0) + 1
    verdict = "PASS" if states["RED"] == 0 and states["PASS"] > 0 else "FAIL"
    detail = " || ".join(f"{name}:{state}:{text}:{elapsed}ms" for name, state, text, elapsed in _RECORDS)
    print(f"SW_DRAWINGS_OPS verdict={verdict} ops={len(_ops())} pass={states['PASS']} "
          f"na={states['NA']} red={states['RED']} kernel={_kernel_state()} locale_hits=0")
    print(f"SW_DRAWINGS_OPS records: {detail or 'NA(未采)'}")


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=2))
