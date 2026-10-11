#!/usr/bin/env python3
"""tests.test_sw_assembly_ops -- family sw_assembly 的独立单测（测试席自持，新文件）。

落盘状态（先读这一段，它决定今天跑出来的是什么）
================================================
本席开工时（2026-09-30）``cadcore/ops/`` 目录里**没有** ``sw_assembly.py``，实测只有
``organization.py`` / ``sw_drawings.py``（``ls cadcore/ops/`` 原样输出见收尾 raw_proof）。
所以本文件是照**冻结接口**写的独立单测，不是对已有实现的复述：

    FAMILY = "<id>"                        # 本文件期望 sw_assembly / sw-assembly
    OPERATIONS: dict[str, Callable]        # 算子名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 一律返回"本次新建对象"的 uuid 列表
    长度一律整数纳米（int，bool/float 不算整数）；错误抛 cadcore.errors.CadError(reason=...)
    不建 ops/__init__.py；可复用 edit/modify.py、sketch/primitives.py、precision/osnap.py、
    sketch/constraints.py、draw/sheet.py、kernel/*

三态记账（禁静默假绿；口径 0=真为零 / -1=未采 / NA=不适用）
----------------------------------------------------------
1. 族模块缺席 ⇒ 行为用例**显式 skip**，reason 里带 ``NA(uncollected)``；本席**没有**跑出任何族行为判定行，
   收尾 raw_proof 里族行为部分记 ``NA(未采)``，不是 PASS。
2. ``PrerequisiteTest`` 今天**真跑**：它只依赖已落盘的兄弟模块（errors / docmodel / edit.modify /
   sketch.primitives / precision.osnap）⇒ 证明本文件读的是真件，不是自造方言（E2.5 夹具自证），
   并给"数值落在记录里"这把尺子做**阳/阴双证**（尺子看得见有、也看得见无）。
3. 族模块在场后，``ContractTest`` 钉结构契约；数值用例做**精确整数纳米**断言；边界用例断言抛
   ``CadError`` 且 ``reason`` 非空，且**先证明合法调用能过**（否则边界用例可能因"算子整体拒收"而假绿）。
4. 族模块在场但连 ``component`` / ``mate`` 两个核心算子的候选名都没有 ⇒ **真报红**（不许 skip 掩盖）。

未落盘期的唤醒层（不放松任何断言）
----------------------------------
冻结接口规定 ``op(sk_or_doc, **kwargs)``，**没规定算子名与关键字名**。本文件把候选集中在
``_OP_ALIASES`` 与 ``_*_variants`` 两组表里：每个用例**真调用**算子，第一个被接受的候选签名生效。
候选被拒的判据 = 抛 ``TypeError``（签名不收）或抛 ``CadError``（例如该形态的引用不成立）；
**所有候选都被拒 ⇒ 报红**，不会因为"参数不认识"独自变成绿。生效候选记进 ``self._last_kwargs``，
失败信息里带前几条被拒原因，便于族模块席对齐命名。

语义不确定的地方（**写成问题，不写成硬断言**）
----------------------------------------------
Q1 「位置」是 2D 还是 3D 点？本文件按 2D ``(x_nm, y_nm)`` 与 3D ``(x_nm, y_nm, z_nm)`` 两种候选都试，
   数值断言只钉 x/y 的**精确整数纳米**是否落进目标树，不钉字段名/嵌套层。
Q2 零件定义（definition）是"文档里的实体 uuid"还是"库里的名字"？两种候选都试；断言只要求定义几何
   （矩形边长 40 mm = 40000000 nm）在装配操作后仍留在文档里。
Q3 ``add_component`` 的返回是否可能包含自动派生的子对象（如同名坐标系）？本文件只断言"非空、去重、
   与既有 uuid 不重叠"，并把**每次调用返回的条数**记为硬断言（每次 1 条）——若实现会顺带返回派生对象，
   这条会红，属**需与族模块席对齐**的口径，不是本文件放宽的余地。
Q4 距离配合（distance mate）是纯记录还是**会真的移动零件**（求解器）？本文件不假定它会移动位置：
   数值断言只要求距离整数落进目标树，不断言零件坐标被改写。
Q5 阵列（component pattern）返回 count-1 条（只算新增）还是 count 条（含原位）？本文件不钉条数，
   只钉"非空、去重、间距整数落进树"，条数口径见这条问题。
Q6 ``bool`` 是否被当作非法长度拒绝（``sw_drawings`` 明确拒绝）？本文件**不**拿它当硬断言，
   只要求"非整数 float"被拒（Q6 留给族模块席回答）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Iterator

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.docmodel import new_document  # noqa: E402
from cadcore.edit import modify as edit_modify  # noqa: E402
from cadcore.errors import CadError as BaseCadError  # noqa: E402
from cadcore.precision import osnap as precision_osnap  # noqa: E402
from cadcore.sketch import primitives as sketch_primitives  # noqa: E402

MM = 1_000_000  # 1 毫米 = 1e6 纳米；本文件所有长度都是整数纳米


def _load_family() -> tuple[Any, str]:
    """导入族模块；缺席时返回 ``(None, 诊断串)``（不吞异常，也不伪造替身模块）。"""
    try:
        from cadcore.ops import sw_assembly as module

        return module, ""
    except Exception as exc:  # noqa: BLE001 -- 缺席就是 NA，诊断串必须原样带出去
        return None, f"{type(exc).__name__}: {exc}"


_FAMILY_MODULE, _IMPORT_DIAG = _load_family()
_FAMILY_PRESENT = _FAMILY_MODULE is not None

#: FAMILY 的两种写法在仓内都在用（门用连字符，模块名用下划线）⇒ 两者都认。
_FAMILY_IDS: tuple[str, ...] = ("sw_assembly", "sw-assembly")

NA_FAMILY_REASON = (
    "NA(uncollected): cadcore/ops/sw_assembly.py is not on disk yet "
    f"[{_IMPORT_DIAG}] -- family behaviour cases stay uncollected, never rewritten as PASS"
)
NA_OP_REASON = (
    "NA(uncollected): family exposes no %r operation; candidate names tried: %s -- "
    "this case stays uncollected, never rewritten as PASS"
)

requires_family = unittest.skipUnless(_FAMILY_PRESENT, NA_FAMILY_REASON)

#: 逻辑算子 -> 候选名（第一个在 OPERATIONS 里可调用的生效）。
_OP_ALIASES: dict[str, tuple[str, ...]] = {
    "component": ("add_component", "insert_component", "place_component", "component_add",
                  "add_instance", "insert_instance", "add_part", "place_part", "new_component"),
    "mate": ("add_mate", "mate", "create_mate", "mate_add", "add_constraint_mate",
             "add_distance_mate", "distance_mate", "mate_distance", "add_mate_distance"),
    "subassembly": ("add_subassembly", "subassembly", "create_subassembly", "new_subassembly",
                    "add_assembly", "assembly_add", "group_components"),
    "pattern": ("component_pattern", "pattern_components", "add_component_pattern",
                "array_components", "pattern", "array_linear_components"),
    "measure": ("measure", "measure_distance", "measure_components", "distance",
                "add_measure", "measure_gap"),
}

#: 必测核心算子（族名 sw_assembly 若连"插零件"和"加配合"都没有 ⇒ 真报红，不 skip）。
_REQUIRED_LOGICAL: tuple[str, ...] = ("component", "mate")


# --------------------------------------------------------------------------------------
# 夹具：L1 目标（既当 doc 读 components/mates，也当 sketch 读 entities），无环
# --------------------------------------------------------------------------------------


def _assembly_target() -> dict[str, Any]:
    """装配目标：同一份映射同时具备 doc 读法（components/mates/assemblies）与 sketch 读法（entities）。

    无自引用（避免实现内部 deepcopy/json 化时炸环）；两种读法的键都在，算子写哪边由实现决定，
    断言只看"返回的 uuid 在整棵树里 + 精确整数纳米在整棵树里"。
    """
    return {
        "components": [], "mates": [], "assemblies": [], "instances": [],
        "entities": [], "constraints": [], "dimensions": [],
        "sketches": [], "features": [],
    }


def _rect_definition(target: dict[str, Any], *, side_nm: int = 40 * MM) -> tuple[str, ...]:
    """真件夹具：用兄弟模块 ``sketch.primitives`` 在目标里放一个 40mm 见方的矩形当零件定义。"""
    return tuple(sketch_primitives.add_rectangle(target, p1_nm=(0, 0), p2_nm=(side_nm, side_nm)))


def _walk_scalars(node: Any, seen: set[int] | None = None) -> Iterator[Any]:
    """深度遍历所有标量（字典 key 不算）；带 id 环保护。"""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node.values():
            yield from _walk_scalars(value, seen)
    elif isinstance(node, (list, tuple, set)):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node:
            yield from _walk_scalars(value, seen)
    else:
        yield node


def _records(node: Any, seen: set[int] | None = None) -> Iterator[dict[str, Any]]:
    """深度遍历所有带 ``uuid`` 的映射记录；带 id 环保护。"""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        if id(node) in seen:
            return
        seen.add(id(node))
        if "uuid" in node:
            yield node
        for value in node.values():
            yield from _records(value, seen)
    elif isinstance(node, (list, tuple)):
        if id(node) in seen:
            return
        seen.add(id(node))
        for value in node:
            yield from _records(value, seen)


def _record_for(target: Any, uuid: str) -> dict[str, Any] | None:
    for record in _records(target):
        if record.get("uuid") == uuid:
            return record
    return None


def _uuid_present(target: Any, uuid: str) -> bool:
    return any(value == uuid for value in _walk_scalars(target))


def _contains_int(target: Any, uuid: str | None, expected: int) -> bool:
    """``uuid`` 所属记录里（找不到记录则整棵树里）是否出现**精确整数** ``expected``。

    "落点字段名"未冻结 ⇒ 用"这个整数必须出现在这条记录里"当硬断言：既不给实现强加字段名，
    也不放宽数值（``bool`` 按 Python 的 int 子类排除，避免 True == 1 造成假绿）。
    """
    scope = _record_for(target, uuid) if uuid else None
    for value in _walk_scalars(scope if scope is not None else target):
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value == expected:
            return True
    return False


def _contains_uuid(target: Any, uuid: str | None, other: str) -> bool:
    """``uuid`` 所属记录里（找不到记录则整棵树里）是否引用了 ``other``（关系型硬断言）。"""
    scope = _record_for(target, uuid) if uuid else None
    return any(value == other for value in _walk_scalars(scope if scope is not None else target))


# --------------------------------------------------------------------------------------
# 候选签名（唤醒层；全部是真调用，不做任何本地模拟）
# --------------------------------------------------------------------------------------


def _point_forms(point_nm: tuple[int, ...]) -> tuple[dict[str, Any], ...]:
    """位置关键字 = 族实现的冻结关键字 ``at_nm``（3 元整数纳米）。"""
    forms = [dict(at_nm=point_nm)]
    if len(point_nm) == 2:
        forms.append(dict(at_nm=(point_nm[0], point_nm[1], 0)))
    return tuple(forms)


def _component_variants(*, name: str, point_nm: tuple[int, ...], definition_name: str,
                        definition_uuid: str) -> tuple[dict[str, Any], ...]:
    """候选关键字 = 族实现**实际**的关键字（2026-09-30 对账判据）。

    判据（``cadcore/ops/sw_assembly.py:543``）：``insert_component(sk_or_doc, *, part_key,
    at_nm=(0,0,0), rotation_deg=..., bbox_nm=None, size_nm=None, refs=None, count=1,
    step_nm=(0,0,0), fixed=False, source=None)`` —— 第一个位置参数就是冻结的 ``sk_or_doc``，
    其余全关键字；零件引用只有 ``part_key`` 一个文本入参，位置只有 ``at_nm``。

    上一版这张表试的是 ``definition`` / ``def_uuid`` / ``position_nm`` / ``name`` / ``part``
    等自造名（22 条候选），实测全部 ``TypeError: unexpected keyword argument``：根因是**本表**
    与冻结接口分歧，不是实现"缺参数"（实现的位置参数名与返回类型都对）。
    ``name`` / ``definition_uuid`` 仍保留在签名里，供用例写标签、做"实例 uuid != 定义 uuid"的
    关系断言；实现没有对应字段，就不硬塞关键字（塞了只会再造一批假红）。
    """
    return tuple(dict(part_key=definition_name, **point) for point in _point_forms(point_nm)) + (
        dict(part_key=definition_name, at_nm=point_nm, count=1, fixed=False),
    )


def _mate_variants(first: str, second: str, *, kind: str | None = None,
                   length_nm: int | None = None) -> tuple[dict[str, Any], ...]:
    """候选关键字 = 族实现**实际**的关键字（2026-09-30 对账判据）。

    判据（``cadcore/ops/sw_assembly.py:712``）：``mate_add(sk_or_doc, *, kind, refs,
    value_nm=None, value_deg=None, flip=False, tolerance_nm=None, tolerance_deg=None,
    label=None)``；``refs`` 是 ``[[component_uuid, ref_name], [component_uuid, ref_name]]``
    （``_ref_pair`` 只收 2 条二元组，裸 uuid 一律 ``invalid_argument``）。``kind`` 在实现里是
    **必填**关键字且必须落在冻结枚举 ``MATE_KINDS`` ⇒ 这里的候选一律显式给 ``kind``；
    ``kind=None`` 的用例（只断言记录身份与引用关系，不断言配合语义）缺省走 ``coincident``。
    """
    refs_forms: tuple[dict[str, Any], ...] = (
        dict(refs=[[first, "origin"], [second, "origin"]]),
        dict(refs=((first, "origin"), (second, "origin"))),
    )
    type_form: dict[str, Any] = dict(kind=kind or "coincident")
    value_form: dict[str, Any] = {} if length_nm is None else dict(value_nm=length_nm)
    return tuple({**refs, **type_form, **value_form} for refs in refs_forms)


def _subassembly_variants(refs: list[str], *, name: str) -> tuple[dict[str, Any], ...]:
    return (
        dict(name=name, refs=list(refs)),
        dict(name=name, components=list(refs)),
        dict(name=name, members=list(refs)),
        dict(name=name, children=list(refs)),
        dict(refs=list(refs)),
    )


def _pattern_variants(ref: str, *, count: int, spacing_nm: int) -> tuple[dict[str, Any], ...]:
    return (
        dict(refs=[ref], count=count, dx_nm=spacing_nm, dy_nm=0),
        dict(refs=[ref], count=count, spacing_nm=(spacing_nm, 0)),
        dict(refs=[ref], count=count, spacing_nm=spacing_nm, axis="x"),
        dict(components=[ref], count=count, dx_nm=spacing_nm),
    )


def _measure_variants(first: str, second: str) -> tuple[dict[str, Any], ...]:
    return (
        dict(refs=[first, second], kind="distance"),
        dict(refs=[first, second]),
        dict(refs=(first, second), kind="distance"),
        dict(components=[first, second]),
        dict(ref_a=first, ref_b=second),
    )


# --------------------------------------------------------------------------------------
# 公共夹具基类
# --------------------------------------------------------------------------------------


class FamilyCase(unittest.TestCase):
    """解析算子 + 三类断言（返回值形态 / 精确整数 / 抛错）。"""

    def setUp(self) -> None:
        self._last_kwargs: dict[str, Any] = {}

    # -- 算子解析 ---------------------------------------------------------------

    def _ops(self) -> dict[str, Any]:
        ops = getattr(_FAMILY_MODULE, "OPERATIONS", None)
        self.assertIsInstance(ops, dict, f"OPERATIONS must be dict[str, Callable], got {type(ops).__name__}")
        return ops

    def _op(self, logical: str):
        ops = self._ops()
        for name in _OP_ALIASES[logical]:
            candidate = ops.get(name)
            if callable(candidate):
                return candidate
        self.fail(f"no {logical!r} operation in OPERATIONS: expected one of {_OP_ALIASES[logical]}, "
                  f"got {sorted(ops)}")

    def _op_optional(self, logical: str):
        ops = getattr(_FAMILY_MODULE, "OPERATIONS", None) or {}
        for name in _OP_ALIASES[logical]:
            candidate = ops.get(name)
            if callable(candidate):
                return candidate
        return None

    def _require_optional(self, logical: str):
        fn = self._op_optional(logical)
        if fn is None:
            self.skipTest(NA_OP_REASON % (logical, ", ".join(_OP_ALIASES[logical])))
        return fn

    # -- 真调用 -----------------------------------------------------------------

    def _assert_uuid_list(self, result: Any, label: str) -> None:
        self.assertIsInstance(result, list, f"{label}: op must return list[str], got {type(result).__name__}")
        for uuid in result:
            self.assertIsInstance(uuid, str, f"{label}: returned uuid must be str, got {uuid!r}")
            self.assertTrue(uuid, f"{label}: returned uuid must be non-empty")
        self.assertEqual(len(set(result)), len(result), f"{label}: returned uuids must be unique, got {result}")

    def _call_fn(self, fn, target: Any, variants: tuple[dict[str, Any], ...], *, label: str) -> list[str]:
        rejected: list[str] = []
        for kwargs in variants:
            try:
                result = fn(target, **kwargs)
            except (TypeError, BaseCadError) as exc:
                reason = getattr(exc, "reason", "")
                rejected.append(f"{sorted(kwargs)} -> {type(exc).__name__}({reason})")
                continue
            self._assert_uuid_list(result, label)
            self._last_kwargs = dict(kwargs)
            return result
        self.fail(f"{label}: no candidate signature accepted by the family op; tried {len(variants)}: "
                  + " || ".join(rejected[:6]))

    def _call(self, logical: str, target: Any, variants: tuple[dict[str, Any], ...],
              *, label: str = "") -> list[str]:
        return self._call_fn(self._op(logical), target, variants, label=label or logical)

    def _call_optional(self, logical: str, target: Any, variants: tuple[dict[str, Any], ...],
                       *, label: str = "") -> list[str]:
        return self._call_fn(self._require_optional(logical), target, variants, label=label or logical)

    def _expect_cad_error(self, fn, target: Any, variants: tuple[dict[str, Any], ...], *, label: str) -> None:
        """非法入参必须抛 ``CadError`` 且 ``reason`` 非空；返回正常 == 真报红。"""
        type_errors: list[str] = []
        for kwargs in variants:
            try:
                fn(target, **kwargs)
            except BaseCadError as exc:
                self.assertIsInstance(exc.reason, str,
                                      f"{label}: CadError.reason must be str, got {exc.reason!r}")
                self.assertTrue(exc.reason,
                                f"{label}: CadError.reason must be a non-empty machine string")
                self._last_kwargs = dict(kwargs)
                return
            except TypeError as exc:
                type_errors.append(f"{sorted(kwargs)} -> TypeError({exc})")
                continue
            else:
                self.fail(f"{label}: expected CadError for kwargs={sorted(kwargs)}, op returned normally")
        self.fail(f"{label}: no candidate signature accepted; tried: " + " || ".join(type_errors[:6]))

    # -- 组合夹具 ---------------------------------------------------------------

    def _two_components(self, target: dict[str, Any]) -> tuple[str, str]:
        """插两个零件：A 在 (7mm, 13mm)，B 在 (37mm, 13mm)（间距 30mm 整，方便数值钉死）。"""
        first = self._call("component", target,
                           _component_variants(name="part-a", point_nm=(7 * MM, 13 * MM),
                                               definition_name="block-40x40",
                                               definition_uuid=_rect_definition(target)[0]),
                           label="add_component(part-a)")[0]
        second = self._call("component", target,
                            _component_variants(name="part-b", point_nm=(37 * MM, 13 * MM),
                                                definition_name="block-40x40",
                                                definition_uuid=_rect_definition(target)[0]),
                            label="add_component(part-b)")[0]
        return first, second


# --------------------------------------------------------------------------------------
# 今天就能真跑的用例：兄弟模块是真件 + 尺子阴阳双证（E2.5 夹具自证）
# --------------------------------------------------------------------------------------


class PrerequisiteTest(FamilyCase):
    """不依赖族模块；证明组合面（兄弟模块）是真件，且本文件的尺子既看得见"有"也看得见"无"。"""

    def test_composition_surface_is_real(self) -> None:
        surfaces = (
            (edit_modify, ("move", "copy", "rotate", "scale", "mirror", "array_linear")),
            (sketch_primitives, ("add_rectangle", "add_polygon", "add_slot", "closed_profiles")),
            (precision_osnap, ("snap", "ortho_lock", "polar_track", "grid_snap")),
            (new_document, ("__call__",)),
        )
        for module, names in surfaces:
            for name in names:
                self.assertTrue(callable(getattr(module, name, None)),
                                f"{getattr(module, '__name__', module)}.{name} must be callable")

    def test_cad_error_carries_machine_readable_reason(self) -> None:
        exc = BaseCadError("probe", reason="probe_reason", field="x")
        self.assertEqual(exc.reason, "probe_reason")
        self.assertEqual(exc.as_dict()["type"], "CadError")
        self.assertIsInstance(exc, Exception)

    def test_definition_fixture_is_a_real_closed_profile(self) -> None:
        """夹具自证：40mm 见方矩形是真件，落在目标树里且闭合（不是自造方言）。"""
        target = _assembly_target()
        uuids = _rect_definition(target, side_nm=40 * MM)
        self.assertEqual(len(uuids), 4)
        self.assertEqual(len(sketch_primitives.closed_profiles(target)), 1)
        for uuid in uuids:
            self.assertTrue(_uuid_present(target, uuid))

    def test_ruler_sees_present_and_absent_values(self) -> None:
        """尺子阴阳双证：``_contains_int`` 对真值说"有"、对一个没写进去的数说"无"。"""
        target = _assembly_target()
        record_uuid = _rect_definition(target)[0]
        self.assertTrue(_contains_int(target, record_uuid, 40 * MM))
        self.assertFalse(_contains_int(target, record_uuid, 41 * MM))
        # 关系尺子同样双证：矩形记录引用的是它自己的 4 条边，不是凭空的 uuid。
        self.assertTrue(_contains_uuid(target, None, record_uuid))
        self.assertFalse(_contains_uuid(target, None, "00000000-0000-4000-8000-000000000000"))


# --------------------------------------------------------------------------------------
# 结构契约
# --------------------------------------------------------------------------------------


@requires_family
class ContractTest(FamilyCase):

    def test_family_id_is_the_frozen_identifier(self) -> None:
        family = getattr(_FAMILY_MODULE, "FAMILY", None)
        self.assertIsInstance(family, str)
        self.assertIn(family, _FAMILY_IDS)

    def test_operations_are_public_callables(self) -> None:
        ops = self._ops()
        self.assertGreaterEqual(len(ops), 3, f"an assembly family needs at least 3 operations, got {sorted(ops)}")
        for name, fn in ops.items():
            self.assertIsInstance(name, str)
            self.assertTrue(name and not name.startswith("_"), f"operation name {name!r} must be public")
            self.assertTrue(callable(fn), f"OPERATIONS[{name!r}] must be callable")

    def test_core_operations_are_present(self) -> None:
        """核心算子缺一 ⇒ 报红（不是 skip）：族名 sw_assembly 至少要能插零件、加配合。"""
        for logical in _REQUIRED_LOGICAL:
            self.assertTrue(callable(self._op(logical)), f"family must expose {logical!r}")


# --------------------------------------------------------------------------------------
# 数值硬断言：插零件
# --------------------------------------------------------------------------------------


@requires_family
class ComponentNumericTest(FamilyCase):

    def test_insert_component_returns_exactly_one_uuid_at_exact_nm(self) -> None:
        """一次插入 = 1 条新 uuid；x=7mm / y=13mm 的**精确整数纳米**必须落进该记录（Q1/Q3）。"""
        target = _assembly_target()
        created = self._call("component", target,
                             _component_variants(name="part-a", point_nm=(7 * MM, 13 * MM),
                                                 definition_name="block-40x40",
                                                 definition_uuid=_rect_definition(target)[0]),
                             label="add_component")
        self.assertEqual(len(created), 1, f"one insert must create exactly one object, got {created}")
        uuid = created[0]
        self.assertTrue(_uuid_present(target, uuid), f"created uuid {uuid!r} must be recorded in the target")
        self.assertTrue(_contains_int(target, uuid, 7 * MM), "x = 7000000 nm must appear in the created record")
        self.assertTrue(_contains_int(target, uuid, 13 * MM), "y = 13000000 nm must appear in the created record")
        self.assertFalse(_contains_int(target, uuid, 8 * MM), "x must be exact: 8000000 nm must NOT appear")

    def test_three_inserts_yield_three_distinct_uuids(self) -> None:
        target = _assembly_target()
        definition_uuid = _rect_definition(target)[0]
        created: list[str] = []
        for index, x_nm in enumerate((0, 25 * MM, 50 * MM), start=1):
            out = self._call("component", target,
                             _component_variants(name=f"part-{index}", point_nm=(x_nm, 0),
                                                 definition_name="block-40x40",
                                                 definition_uuid=definition_uuid),
                             label=f"add_component(#{index})")
            self.assertEqual(len(out), 1, f"insert #{index} must return exactly 1 uuid, got {out}")
            created.extend(out)
        self.assertEqual(len(created), 3)
        self.assertEqual(len(set(created)), 3, f"three inserts must yield three distinct uuids, got {created}")
        for uuid in created:
            self.assertTrue(_uuid_present(target, uuid), f"uuid {uuid!r} must be recorded")
        self.assertTrue(_contains_int(target, created[1], 25 * MM), "x = 25000000 nm must be recorded")
        self.assertTrue(_contains_int(target, created[2], 50 * MM), "x = 50000000 nm must be recorded")

    def test_insert_does_not_reuse_definition_uuid_as_component_uuid(self) -> None:
        """零件实例与它的定义不是同一个对象：返回的 uuid 不许等于定义几何的 uuid。"""
        target = _assembly_target()
        definition = _rect_definition(target)
        created = self._call("component", target,
                             _component_variants(name="part-a", point_nm=(0, 0),
                                                 definition_name="block-40x40",
                                                 definition_uuid=definition[0]),
                             label="add_component")
        for uuid in created:
            self.assertNotIn(uuid, definition, "a component instance must not reuse its definition uuid")
        self.assertEqual(len(definition), 4, "the 40mm rectangle definition must stay intact (4 edges)")


# --------------------------------------------------------------------------------------
# 数值硬断言：配合（mate）
# --------------------------------------------------------------------------------------


@requires_family
class MateNumericTest(FamilyCase):

    def test_distance_mate_records_exact_integer_nm(self) -> None:
        """距离配合的 12.5mm = 12500000 nm 必须**精确**落进目标树（Q2 不管字段名，只管数值）。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        created = self._call("mate", target,
                             _mate_variants(first, second, kind="distance", length_nm=12_500_000),
                             label="add_mate(distance)")
        self.assertEqual(len(created), 1, f"one mate must create exactly one object, got {created}")
        self.assertTrue(_contains_int(target, created[0], 12_500_000),
                        "12500000 nm must appear in the mate record")
        self.assertFalse(_contains_int(target, created[0], 12_500_001),
                         "distance must be exact: 12500001 nm must NOT appear")

    def test_mate_is_referenced_to_both_components(self) -> None:
        """关系型硬断言：配合记录必须同时引用两个零件 uuid（否则它约束的是谁？）。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        created = self._call("mate", target, _mate_variants(first, second), label="add_mate")
        self.assertEqual(len(created), 1)
        self.assertTrue(_contains_uuid(target, created[0], first), f"mate must reference {first!r}")
        self.assertTrue(_contains_uuid(target, created[0], second), f"mate must reference {second!r}")

    def test_mate_uuid_is_disjoint_from_component_uuids(self) -> None:
        target = _assembly_target()
        first, second = self._two_components(target)
        created = self._call("mate", target, _mate_variants(first, second), label="add_mate")
        self.assertEqual(len(created), 1)
        self.assertNotIn(created[0], (first, second), "a mate must be its own object, not a component uuid")
        self.assertTrue(_uuid_present(target, created[0]))

    def test_two_mates_are_two_distinct_records(self) -> None:
        target = _assembly_target()
        first, second = self._two_components(target)
        third = self._call("component", target,
                           _component_variants(name="part-c", point_nm=(67 * MM, 13 * MM),
                                               definition_name="block-40x40",
                                               definition_uuid=_rect_definition(target)[0]),
                           label="add_component(part-c)")[0]
        mate_a = self._call("mate", target, _mate_variants(first, second), label="mate(a,b)")
        mate_b = self._call("mate", target, _mate_variants(second, third, kind="distance",
                                                           length_nm=30 * MM), label="mate(b,c)")
        self.assertEqual((len(mate_a), len(mate_b)), (1, 1))
        self.assertNotEqual(mate_a[0], mate_b[0], "two mates must be two distinct objects")
        self.assertTrue(_contains_uuid(target, mate_a[0], first))
        self.assertTrue(_contains_uuid(target, mate_b[0], third))
        self.assertTrue(_contains_int(target, mate_b[0], 30 * MM), "30000000 nm must appear in mate(b,c)")


# --------------------------------------------------------------------------------------
# 数值硬断言：子装配
# --------------------------------------------------------------------------------------


@requires_family
class SubassemblyNumericTest(FamilyCase):

    def test_subassembly_returns_one_uuid_and_covers_children(self) -> None:
        """子装配 = 1 条新 uuid，且两个子件都被它收进去（可选算子：缺席记 NA，不假绿）。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        created = self._call_optional("subassembly", target,
                                      _subassembly_variants([first, second], name="sub-1"),
                                      label="add_subassembly")
        self.assertEqual(len(created), 1, f"one subassembly call must create exactly one object, got {created}")
        self.assertNotIn(created[0], (first, second), "the subassembly is its own object")
        self.assertTrue(_contains_uuid(target, created[0], first), f"subassembly must cover {first!r}")
        self.assertTrue(_contains_uuid(target, created[0], second), f"subassembly must cover {second!r}")


# --------------------------------------------------------------------------------------
# 可选算子：阵列 / 测量（缺席 ⇒ NA；在场 ⇒ 数值硬断言）
# --------------------------------------------------------------------------------------


@requires_family
class OptionalOperationTest(FamilyCase):

    def test_component_pattern_places_instances_at_exact_spacing(self) -> None:
        """阵列间距 25mm = 25000000 nm 必须落进新建记录；条数口径见 Q5（不硬断言条数）。"""
        target = _assembly_target()
        base = self._call("component", target,
                          _component_variants(name="part-a", point_nm=(0, 0),
                                              definition_name="block-40x40",
                                              definition_uuid=_rect_definition(target)[0]),
                          label="add_component(base)")[0]
        created = self._call_optional("pattern", target,
                                      _pattern_variants(base, count=4, spacing_nm=25 * MM),
                                      label="component_pattern")
        self.assertGreaterEqual(len(created), 1, f"a 4-up pattern must create at least one object, got {created}")
        self.assertEqual(len(set(created)), len(created), "pattern uuids must be distinct")
        self.assertNotIn(base, created, "the base instance is not newly created (see Q5)")
        self.assertTrue(_contains_int(target, created[0], 25 * MM),
                        "spacing 25000000 nm must appear in the created pattern record")

    def test_measure_reports_exact_distance_between_components(self) -> None:
        """3-4-5 夹具：两零件相距 50mm = 50000000 nm，测量记录的数值必须**精确**等于它。"""
        target = _assembly_target()
        definition_uuid = _rect_definition(target)[0]
        first = self._call("component", target,
                           _component_variants(name="part-a", point_nm=(0, 0),
                                               definition_name="block-40x40",
                                               definition_uuid=definition_uuid),
                           label="add_component(a)")[0]
        second = self._call("component", target,
                            _component_variants(name="part-b", point_nm=(30 * MM, 40 * MM),
                                                definition_name="block-40x40",
                                                definition_uuid=definition_uuid),
                            label="add_component(b)")[0]
        created = self._call_optional("measure", target, _measure_variants(first, second), label="measure")
        self.assertGreaterEqual(len(created), 1, f"a measurement must create at least one object, got {created}")
        self.assertTrue(_contains_int(target, created[0], 50 * MM),
                        "50000000 nm must appear in the measurement record")
        self.assertFalse(_contains_int(target, created[0], 50 * MM + 1),
                         "the measured distance must be exact")


# --------------------------------------------------------------------------------------
# 边界：非法参数必须抛 CadError(reason=...)
# --------------------------------------------------------------------------------------


@requires_family
class BoundaryTest(FamilyCase):

    def test_mate_unknown_component_raises_cad_error(self) -> None:
        """引用一个不在目标里的 uuid ⇒ CadError。先证合法调用能过，避免"算子整体拒收"式假绿。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        self._call("mate", target, _mate_variants(first, second), label="valid mate (control)")
        ghost = "00000000-0000-4000-8000-000000000000"
        self._expect_cad_error(self._op("mate"), target,
                               _mate_variants(first, ghost) + _mate_variants(ghost, first),
                               label="mate(unknown ref)")

    def test_mate_empty_refs_raises_cad_error(self) -> None:
        target = _assembly_target()
        first, _second = self._two_components(target)
        # 判据（`mate_add`）：`kind` 必填且 `refs` 只收 2 条 [uuid, ref_name]；空/少/多/空串一律
        # `invalid_argument`；旧表的 `components=`/`ref_a=` 只是自造关键字（纯 TypeError，不是被测行为）。
        variants: tuple[dict[str, Any], ...] = (
            dict(kind="coincident", refs=[]),
            dict(kind="coincident", refs=[first]),
            dict(kind="coincident", refs=[[first, "origin"]]),
            dict(kind="coincident", refs=[[first, "origin"], [first, "origin"], [first, "origin"]]),
            dict(kind="coincident", refs=[["", "origin"], ["", "origin"]]),
            dict(kind="coincident", refs=[[first, ""], [_second, "origin"]]),
        )
        self._expect_cad_error(self._op("mate"), target, variants, label="mate(empty refs)")

    def test_mate_self_reference_raises_cad_error(self) -> None:
        """同一零件与自己配合（退化）⇒ CadError。"""
        target = _assembly_target()
        first, _second = self._two_components(target)
        self._expect_cad_error(self._op("mate"), target, _mate_variants(first, first),
                               label="mate(self)")

    def test_mate_negative_distance_raises_cad_error(self) -> None:
        target = _assembly_target()
        first, second = self._two_components(target)
        variants = tuple(dict(item, **value)
                         for item in _mate_variants(first, second, kind="distance")
                         for value in (dict(distance_nm=-5 * MM), dict(offset_nm=-5 * MM), dict(value_nm=-5 * MM)))
        self._expect_cad_error(self._op("mate"), target, variants, label="mate(negative distance)")

    def test_mate_non_integer_length_raises_cad_error(self) -> None:
        """长度是**整数**纳米：非整数 float（12.5 nm）必须被拒（Q6 的 bool 问题不在此列）。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        variants = tuple(dict(item, **value)
                         for item in _mate_variants(first, second, kind="distance")
                         for value in (dict(distance_nm=12.5), dict(offset_nm=12.5), dict(value_nm=12.5)))
        self._expect_cad_error(self._op("mate"), target, variants, label="mate(float length)")

    def test_component_blank_definition_raises_cad_error(self) -> None:
        """空白零件定义引用 ⇒ CadError（不许静默造一个没有形状的零件）。"""
        target = _assembly_target()
        self._call("component", target,
                   _component_variants(name="part-ok", point_nm=(0, 0), definition_name="block-40x40",
                                       definition_uuid=_rect_definition(target)[0]),
                   label="valid add_component (control)")
        # 判据（`insert_component`）：零件引用只有 `part_key` 一个文本入参，`_text` 拒空串/空白串/None
        # ⇒ 空定义与空名字都走同一个 `invalid_argument`；旧表的 `position_nm=`/`definition=` 是自造关键字。
        variants: tuple[dict[str, Any], ...] = (
            dict(part_key="", at_nm=(0, 0)),
            dict(part_key="   ", at_nm=(0, 0)),
            dict(part_key=None, at_nm=(0, 0)),
        )
        self._expect_cad_error(self._op("component"), target, variants, label="add_component(blank def)")

    def test_subassembly_cycle_raises_cad_error(self) -> None:
        """子装配套自己（环）⇒ CadError；可选算子缺席时记 NA。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        sub = self._call_optional("subassembly", target,
                                  _subassembly_variants([first, second], name="sub-1"),
                                  label="add_subassembly")
        self.assertEqual(len(sub), 1)
        self._expect_cad_error(self._require_optional("subassembly"), target,
                               _subassembly_variants([sub[0]], name="sub-cycle"),
                               label="add_subassembly(self-cycle)")


# --------------------------------------------------------------------------------------
# 组合用例（2 个，硬要求）
# --------------------------------------------------------------------------------------


@requires_family
class CompositionTest(FamilyCase):

    def test_composition_insert_mate_pipeline_keeps_identity(self) -> None:
        """组合①：插 2 件 → 距离配合 → 再插第 3 件；每步条数与 id 稳定性都硬断言。"""
        target = _assembly_target()
        first, second = self._two_components(target)
        mate = self._call("mate", target,
                          _mate_variants(first, second, kind="distance", length_nm=12_500_000),
                          label="mate(a,b)")
        third = self._call("component", target,
                           _component_variants(name="part-c", point_nm=(67 * MM, 13 * MM),
                                               definition_name="block-40x40",
                                               definition_uuid=_rect_definition(target)[0]),
                           label="add_component(c)")
        self.assertEqual((len(mate), len(third)), (1, 1))
        all_uuids = [first, second, mate[0], third[0]]
        self.assertEqual(len(set(all_uuids)), 4, f"four operations must yield four distinct uuids, got {all_uuids}")
        # 后续操作不得抹掉先前对象（id 稳定）
        for uuid in (first, second, mate[0], third[0]):
            self.assertTrue(_uuid_present(target, uuid), f"{uuid!r} must survive later operations")
        # 配合记录仍然引用最初两件，且距离精确
        self.assertTrue(_contains_uuid(target, mate[0], first))
        self.assertTrue(_contains_uuid(target, mate[0], second))
        self.assertTrue(_contains_int(target, mate[0], 12_500_000))

    def test_composition_definition_reuse_across_two_instances(self) -> None:
        """组合②：兄弟模块造定义（40mm 矩形）→ 两个实例复用同一定义 → 配合；定义不许被装配改动。"""
        target = _assembly_target()
        definition = _rect_definition(target, side_nm=40 * MM)
        self.assertEqual(len(definition), 4)
        first = self._call("component", target,
                           _component_variants(name="part-a", point_nm=(7 * MM, 13 * MM),
                                               definition_name="block-40x40",
                                               definition_uuid=definition[0]),
                           label="add_component(a)")[0]
        second = self._call("component", target,
                            _component_variants(name="part-b", point_nm=(7 * MM, 53 * MM),
                                                definition_name="block-40x40",
                                                definition_uuid=definition[0]),
                            label="add_component(b)")[0]
        mate = self._call("mate", target,
                          _mate_variants(first, second, kind="distance", length_nm=40 * MM),
                          label="mate(a,b)")[0]
        self.assertEqual(len({first, second, mate}), 3)
        for uuid in definition:
            self.assertTrue(_uuid_present(target, uuid), f"definition edge {uuid!r} must survive assembly")
        self.assertTrue(_contains_int(target, None, 40 * MM),
                        "the 40mm = 40000000 nm definition edge must still be in the document")
        self.assertTrue(_contains_uuid(target, mate, first))
        self.assertTrue(_contains_uuid(target, mate, second))
        self.assertTrue(_contains_int(target, mate, 40 * MM))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
