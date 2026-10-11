#!/usr/bin/env python3
"""test_annotation_ops —— annotation 族模块（`cadcore/ops/annotation.py`）的独立单测。

**落盘状态（2026-09-30 撰写当刻）**：`cadcore/ops/annotation.py` **尚未落盘**
（`cadcore/ops/` 目录不存在）⇒ 本文件按**冻结接口**写，运行期模块缺席一律记 `NA`（SkipTest），
不把"没测"伪装成"测过"：

    FAMILY = "<id>"                       # 本族 id 必须是 "annotation"
    OPERATIONS: dict[str, Callable]       # 操作名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]  # 返回 uuid 列表；长度一律整数纳米
    非法参数 -> cadcore.errors.CadError(reason=...)

接口适配层（为什么有 `_bind`）
------------------------------
冻结接口只冻了 `FAMILY` / `OPERATIONS` / `op(sk_or_doc, **kwargs) -> list[str]` / 整数纳米 / `CadError`，
**没有冻操作名与 kwargs 名**。因此本文件用别名表（`_OP_ALIASES` / `_PARAM_ALIASES`）把"能力"映射到
实际声明：能绑上就按硬断言跑；绑不上（操作名或参数名不同）**当场 SkipTest 并打印它声明了什么** ——
是显式 `NA`，不是静默通过。模块真正落盘后，这里会立刻变成可执行的真实断言面。

断言的硬口径（与冻结接口同源，不依赖实现的内部键名）
--------------------------------------------------
1. 返回 uuid 列表：每个元素是 `uuid.UUID` 可解析的字符串、**互不重复**，且新增操作的每个 uuid
   都能在 `doc["annotations"]` 里找到、注解条数**恰好**增加 `len(uuids)`（数量硬断言）；
2. 数值硬断言：夹具几何取整纳米（line 400000 nm、circle r=125000 nm、两线夹角 90°），
   声明值与几何实测值**设计成相等** ⇒ 注解里必须能按**精确数值**找到该长度/角度
   （`_find_number`：整数纳米精确相等；角度接受 90 / 90e6 微度 / π/2 弧度三种落盘口径）；
3. 坐标硬断言：`at_nm=(x, y)` 这类坐标必须能在注解树里按**精确整数对**找到（`_find_pair`）；
4. 引用硬断言：`target` 必须是 `(uuid, sub_shape_name)` 二元组（L1 §C 口径），且 uuid 能在草图里解析到实体；
5. 边界：非整数纳米 / bool / 未知 uuid / 空选择 / 负半径 / NaN 角度 ⇒ `CadError`，且 `reason`
   必须是**具体**的机器可读 token（不是基类缺省 `cad_error`）。

组合用例（2 个以上，与**已有**命令组合）
------------------------------------
- `cadcore.edit.modify.move`：移动实体后注解 target 仍指向同一 uuid、几何逐字段精确平移；
- `cadcore.edit.modify.mirror(keep_original=True)`：镜像出的新实体可再挂一条注解，两条 target 互异；
- `cadcore.docmodel`：注解文档过 `check_invariants`（0 违规）且 L1 文本两次序列化逐字节相同。

自证（夹具与尺子先自证，见 AGENTS.md E2.5）
------------------------------------------
`FixtureTests` 与 `HarnessTests` **不依赖族模块**，任何时刻都跑：前者自证夹具是合法 L1（uuid 合法、
长度整数纳米），后者用一个人造的错误签名操作做**阴性对照**，证明 `_bind` 这把尺子真看得见"接口不符"。
"""

from __future__ import annotations

import inspect
import pathlib
import subprocess
import sys
import unittest
import uuid as _uuid

from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1
from cadcore.edit import modify
from cadcore.errors import CadError
from cadcore.sketch import constraints as cons

try:  # 族模块未落盘 = NA（不是失败，也绝不静默当通过）
    from cadcore.ops import annotation as ann

    _MODULE_STATUS = "landed"
    _IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - 缺席原因必须留证
    ann = None
    _MODULE_STATUS = "NA"
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)


# --------------------------------------------------------------------------------------
# 夹具（L1 文档：一草图两直线 + 一圆，一拉伸特征，annotations 为空）
# --------------------------------------------------------------------------------------


def _uid(index: int) -> str:
    return "%08x-0000-4000-8000-%012x" % (index, index)


SKETCH_UUID = _uid(1)
LINE_H = _uid(2)      # (0,0) -> (400000,0)   长度恰好 400000 nm
LINE_V = _uid(3)      # (0,0) -> (0,300000)   与 LINE_H 夹角恰好 90 度
CIRCLE = _uid(4)      # 圆心 (1000000,250000) 半径 125000 nm
FEATURE_UUID = _uid(5)

LINE_H_LEN_NM = 400000
LINE_V_LEN_NM = 300000
CIRCLE_R_NM = 125000
CIRCLE_D_NM = 2 * CIRCLE_R_NM
ANGLE_DEG = 90.0


def new_document() -> dict:
    """一页合法 L1 文档（长度全整数纳米；uuid 全是合法 uuid4 形态）。"""
    return {
        "schema_version": 1,
        "meta": {"doc_uuid": _uid(100), "created_at": "2026-09-30T00:00:00Z",
                 "app_version": "0.1.0", "title_key": "doc.untitled"},
        "unit": "mm",
        "standard_profile": "iso",
        "projection_angle": None,
        "sketches": [{
            "uuid": SKETCH_UUID,
            "plane": "XY",
            "entities": [
                {"uuid": LINE_H, "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": LINE_H_LEN_NM, "y2": 0}},
                {"uuid": LINE_V, "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": LINE_V_LEN_NM}},
                {"uuid": CIRCLE, "kind": "circle", "layer": "0", "construction": False,
                 "geom": {"cx": 1000000, "cy": 250000, "r": CIRCLE_R_NM}},
            ],
            "constraints": [], "dimensions": [],
            "solved": {"dof": -1, "converged": False, "cache": True},
        }],
        "features": [{"uuid": FEATURE_UUID, "kind": "extrude", "inputs": [SKETCH_UUID],
                      "params": {"distance_nm": 1000000, "symmetric": False, "angle_deg": 0.0,
                                 "op": "new"}, "suppressed": False}],
        "annotations": [],
        "sheets": [],
    }


def sketch(doc: dict) -> dict:
    return doc["sketches"][0]


def entity_geom(doc: dict, entity_uuid: str) -> dict:
    found = cons.find_entity(sketch(doc), entity_uuid)
    if found is None:
        raise AssertionError("fixture lost entity %s" % entity_uuid)
    return found["geom"]


def _walk(node):
    """深度遍历任意 JSON 值（映射/序列），产出所有叶子。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)
    else:
        yield node


def _find_number(node, wanted, *, rel_tol=0.0) -> bool:
    """注解树里是否存在数值 `wanted`（整数纳米精确相等；角度允许给相对容差）。"""
    for leaf in _walk(node):
        if isinstance(leaf, bool) or not isinstance(leaf, (int, float)):
            continue
        if rel_tol <= 0.0:
            if leaf == wanted:
                return True
        elif wanted != 0 and abs(leaf - wanted) <= abs(wanted) * rel_tol:
            return True
    return False


def _find_pair(node, wanted) -> bool:
    """注解树里是否存在 `[x, y]` 或 `(x, y)` 这个精确整数对（坐标硬断言用）。"""
    if isinstance(node, (list, tuple)) and len(node) == 2 \
            and tuple(node) == tuple(wanted):
        return True
    if isinstance(node, dict):
        return any(_find_pair(value, wanted) for value in node.values())
    if isinstance(node, (list, tuple)):
        return any(_find_pair(item, wanted) for item in node)
    return False


# --------------------------------------------------------------------------------------
# 接口适配层（别名绑定；绑不上 = NA，绝不静默通过）
# --------------------------------------------------------------------------------------

#: 能力 -> 候选操作名（冻结接口未冻操作名，故按候选集解析）。
_OP_ALIASES: dict[str, tuple[str, ...]] = {
    "dimension": ("add_dimension", "dimension", "add_linear_dimension", "add_dim"),
    "aligned": ("add_aligned_dimension", "aligned_dimension", "add_dimension_aligned"),
    "angular": ("add_angular_dimension", "angular_dimension", "add_dimension_angular"),
    "radial": ("add_radial_dimension", "radial_dimension", "add_dimension_radial"),
    "diameter": ("add_diameter_dimension", "diameter_dimension", "add_dimension_diameter"),
    "text": ("add_text", "text", "add_note", "add_mtext", "add_single_text"),
    "leader": ("add_leader", "leader", "add_multileader", "add_mleader"),
    "center_mark": ("add_center_mark", "center_mark", "add_centerline", "add_center_line"),
    "update": ("update_value", "set_value", "update_annotation", "edit_annotation"),
    "remove": ("remove", "remove_annotation", "delete", "delete_annotation"),
}

#: 规范参数名 -> 实现可能用的参数名（别名集）。
_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "doc": ("sk_or_doc", "doc", "document", "sk", "sketch", "model", "target_doc"),
    "target": ("target", "ref", "refs", "entity", "entities", "uuid", "uuids", "targets"),
    "kind": ("kind", "dim_kind", "dimension_kind", "type"),
    "value_nm": ("value_nm", "length_nm", "distance_nm", "size_nm", "radius_nm",
                 "diameter_nm", "angle_deg", "value"),
    "at_nm": ("at_nm", "at", "point_nm", "position_nm", "insert_nm", "origin_nm", "base_nm",
              "leader_nm", "text_at_nm"),
    "text_key": ("text_key", "key", "label_key", "message_key"),
    "literal": ("literal", "is_literal"),
    "text": ("text", "literal_text", "content", "value_text"),
    "side": ("side", "direction", "normal"),
    "min_count": ("min_count", "count", "n"),
}
_PARAM_BY_NAME: dict[str, str] = {alias: canon
                                  for canon, aliases in _PARAM_ALIASES.items()
                                  for alias in aliases}


def _resolve(capability: str):
    """能力 -> (操作名, 可调用)；模块缺席或能力缺席一律 SkipTest（显式 NA）。"""
    if ann is None:
        raise unittest.SkipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
    ops = getattr(ann, "OPERATIONS", None)
    if not isinstance(ops, dict):
        raise AssertionError("OPERATIONS must be a dict, got %r" % type(ops).__name__)
    for name in _OP_ALIASES.get(capability, (capability,)):
        if name in ops and callable(ops[name]):
            return name, ops[name]
    raise unittest.SkipTest("NA: no operation for capability %r; OPERATIONS=%s"
                            % (capability, sorted(ops)))


def _bind(op, canonical: dict) -> dict:
    """规范参数 -> 该 op 实际声明的 kwargs；声明的必需参数绑不上就 SkipTest（把接口差异说出来）。"""
    try:
        signature = inspect.signature(op)
    except (TypeError, ValueError) as exc:
        raise unittest.SkipTest("NA: op signature is not introspectable (%s)" % exc)
    params = [item for item in signature.parameters.values() if item.name != "self"]
    if not params:
        raise unittest.SkipTest("NA: op declares no parameter at all")
    head = params[0]
    if head.kind not in (head.POSITIONAL_ONLY, head.POSITIONAL_OR_KEYWORD) \
            or _PARAM_BY_NAME.get(head.name) != "doc":
        raise unittest.SkipTest("NA: first parameter %r is not the sketch/document" % head.name)
    kwargs: dict = {}
    unmatched: list[str] = []
    for item in params[1:]:
        if item.kind in (item.VAR_POSITIONAL, item.VAR_KEYWORD):
            continue
        canon = _PARAM_BY_NAME.get(item.name)
        if canon is not None and canon in canonical:
            kwargs[item.name] = canonical[canon]
        elif item.default is item.empty:
            unmatched.append(item.name)
    if unmatched:
        raise unittest.SkipTest("NA: op %s declares required parameter(s) %s not covered by the "
                                "frozen kwarg table (declared=%s)"
                                % (getattr(op, "__name__", "?"), unmatched,
                                   [item.name for item in params]))
    return kwargs


def _check_uuid_list(result) -> list:
    """冻结接口的返回值尺子：必须是 uuid 字符串列表且互不重复。"""
    if not isinstance(result, list):
        raise AssertionError("op must return list[str], got %r" % type(result).__name__)
    seen: set[str] = set()
    for item in result:
        if not isinstance(item, str) or not item:
            raise AssertionError("op returned a non-uuid item: %r" % (item,))
        try:
            _uuid.UUID(item)  # 非法 uuid 必须当场判红：AssertionError，不许以 ValueError 逃逸出尺子
        except ValueError as exc:
            raise AssertionError("op returned a malformed uuid: %r (%s)" % (item, exc)) from exc
        if item in seen:
            raise AssertionError("op returned a duplicate uuid: %s" % item)
        seen.add(item)
    return result


def _call(capability: str, doc: dict, **canonical) -> list:
    """调用一个能力（别名解析 + 参数绑定 + 返回值体检），返回 uuid 列表。"""
    _name, op = _resolve(capability)
    kwargs = _bind(op, canonical)
    return _check_uuid_list(op(doc, **kwargs))


def _add(capability: str, doc: dict, **canonical) -> list:
    """新增类操作：uuid 列表必须每个都能在 `doc["annotations"]` 里找到，且条数恰好 +len。"""
    before = len(doc["annotations"])
    result = _call(capability, doc, **canonical)
    uuids = {item.get("uuid") for item in doc["annotations"] if isinstance(item, dict)}
    for item in result:
        if item not in uuids:
            raise AssertionError("returned uuid %s is not in doc['annotations'] (uuids=%s)"
                                 % (item, sorted(uuids)))
    if len(doc["annotations"]) != before + len(result):
        raise AssertionError("annotation count moved by %d but op returned %d uuid(s)"
                             % (len(doc["annotations"]) - before, len(result)))
    return result


def _by_uuid(doc: dict, annotation_uuid: str) -> dict:
    for item in doc["annotations"]:
        if isinstance(item, dict) and item.get("uuid") == annotation_uuid:
            return item
    raise AssertionError("annotation %s not found in document" % annotation_uuid)


def _expect_cad_error(capability: str, doc: dict, **canonical) -> CadError:
    """边界：必须抛 CadError，且 `reason` 是具体的机器可读 token（不是缺省 cad_error）。"""
    try:
        _call(capability, doc, **canonical)
    except CadError as exc:
        if not exc.reason or exc.reason == CadError.default_reason:
            raise AssertionError("CadError.reason must name the concrete cause, got %r" % exc.reason)
        if not all(ch.islower() or ch.isdigit() or ch == "_" for ch in exc.reason):
            raise AssertionError("CadError.reason must be a machine-readable ascii token, got %r"
                                 % exc.reason)
        return exc
    raise AssertionError("expected CadError from capability %r, but the call returned normally"
                         % capability)


def _expect_cad_error_direct(op, doc: dict, label: str, **kwargs) -> CadError:
    """边界：**直连** op 调用（不经 `_bind` 的别名表），必须抛具体 `reason` 的 `CadError`。

    为什么这些用例不能走 `_expect_cad_error`：它经 `_call` → `_bind`，而 `_PARAM_ALIASES`
    只覆盖 `doc`/`target`/`kind`/`value_nm`/`at_nm`/`text_key`/`literal`/`text`/`side`/
    `min_count` —— `name`/`subject`/`search`/`replacement`/`scope` 等新参数在表外会被
    **静默丢弃**：op 按缺省成功返回，尺子把"参数根本没送到"记成"没抛 CadError"（伪红）。
    直连调用用的就是实现自己声明的 kwargs 名 ⇒ 参数一定送到，断言才落在真边界上。
    """
    try:
        op(doc, **kwargs)
    except CadError as exc:
        if not exc.reason or exc.reason == CadError.default_reason:
            raise AssertionError("CadError.reason must name the concrete cause, got %r" % exc.reason)
        if not all(ch.islower() or ch.isdigit() or ch == "_" for ch in exc.reason):
            raise AssertionError("CadError.reason must be a machine-readable ascii token, got %r"
                                 % exc.reason)
        return exc
    raise AssertionError("expected CadError from %s(%r), but the call returned normally"
                         % (label, sorted(kwargs)))


# --------------------------------------------------------------------------------------
# 夹具自证 + 尺子阴性对照（不依赖族模块，任何时刻都跑）
# --------------------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def test_fixture_is_invariant_clean_l1(self):
        problems = check_invariants(new_document())
        self.assertEqual([], problems, "fixture must be a legal L1 document: %s" % problems[:5])

    def test_fixture_geometry_is_exact_integer_nm(self):
        doc = new_document()
        self.assertEqual({"x1": 0, "y1": 0, "x2": LINE_H_LEN_NM, "y2": 0},
                         entity_geom(doc, LINE_H))
        self.assertEqual({"cx": 1000000, "cy": 250000, "r": CIRCLE_R_NM},
                         entity_geom(doc, CIRCLE))
        for leaf in _walk(doc["sketches"]):
            if isinstance(leaf, float):
                self.assertEqual(int(leaf), leaf, "fixture lengths must be integer nm: %r" % leaf)

    def test_fixture_uuid_and_scan_helpers_are_not_vacuous(self):
        doc = new_document()
        _uuid.UUID(SKETCH_UUID)
        self.assertTrue(_find_number(_by_x := {"v": CIRCLE_R_NM}, CIRCLE_R_NM))
        self.assertFalse(_find_number(_by_x, CIRCLE_R_NM + 1))
        self.assertTrue(_find_pair({"args": {"at": [7, 9]}}, (7, 9)))
        self.assertFalse(_find_pair({"args": {"at": [7, 9]}}, (7, 8)))
        self.assertIsNotNone(cons.find_entity(sketch(doc), LINE_H))

    def test_module_absence_is_reported_not_hidden(self):
        self.assertIn(_MODULE_STATUS, ("landed", "NA"))
        if ann is None:
            self.assertTrue(_IMPORT_ERROR, "absence must carry a reason, got an empty string")
        else:
            self.assertEqual("annotation", getattr(ann, "FAMILY", None))


class HarnessTests(unittest.TestCase):
    """尺子的阴性对照：接口不符必须被判成 NA/失败，而不是静默通过。"""

    def test_bind_rejects_a_wrong_first_parameter(self):
        def wrong_first(not_a_doc, *, value_nm):
            return []

        with self.assertRaises(unittest.SkipTest):
            _bind(wrong_first, {"doc": {}, "value_nm": 1})

    def test_bind_reports_uncovered_required_parameters(self):
        def op(doc, *, mystery_nm):
            return []

        with self.assertRaises(unittest.SkipTest) as ctx:
            _bind(op, {"doc": {}, "value_nm": 1})
        self.assertIn("mystery_nm", str(ctx.exception))

    def test_bind_maps_aliases_and_ignores_optional_defaults(self):
        def op(sk_or_doc, *, length_nm=None, refs=(), kind="linear"):
            return []

        bound = _bind(op, {"doc": {}, "value_nm": 42, "target": ["u"], "kind": "linear"})
        self.assertEqual({"length_nm": 42, "refs": ["u"], "kind": "linear"}, bound)

    def test_uuid_list_ruler_rejects_bad_shapes(self):
        with self.assertRaises(AssertionError):
            _check_uuid_list("not-a-list")
        with self.assertRaises(AssertionError):
            _check_uuid_list(["not-a-uuid"])
        good = _uid(7)
        self.assertEqual([good], _check_uuid_list([good]))


class AngularUpdateAndSelftestTests(unittest.TestCase):
    """事后可改的角度单位口径 + 族模块自带 selftest（本族此前是唯一没有 selftest 的 ops 模块）。"""

    def test_update_angular_value_takes_degrees_and_refreshes_args(self):
        doc = new_document()
        uuid_ = _add("angular", doc, target=[(LINE_H, "edge"), (LINE_V, "edge")],
                     kind="angular", value_nm=ANGLE_DEG, text_key="cmd.dimension.angular")[0]
        before = _by_uuid(doc, uuid_)
        self.assertEqual(90 * 1000000, before["value_nm"], "90 deg must land as micro-degrees")
        result = _call("update", doc, target=uuid_, value_nm=45.0)
        self.assertEqual([uuid_], result)
        after = _by_uuid(doc, uuid_)
        self.assertIs(type(after["value_nm"]), int, "value_nm must stay an integer nanometre-family int")
        self.assertEqual(45 * 1000000, after["value_nm"],
                         "an angular update takes DEGREES (same unit as add_angular_dimension)")
        self.assertEqual(1, len(doc["annotations"]), "update must not add or drop annotations")
        self.assertEqual(before.get("target"), after.get("target"), "target reference must not move")
        args = after.get("args", {})
        self.assertEqual(45.0, args.get("angle_deg"),
                         "args.angle_deg must not go stale after an angular update")
        self.assertEqual("micro_deg", args.get("angle_unit"))
        self.assertEqual([], check_invariants(doc))

    def test_update_linear_still_requires_integer_nm(self):
        doc = new_document()
        uuid_ = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        _expect_cad_error("update", doc, target=uuid_, value_nm=LINE_H_LEN_NM + 0.5)
        self.assertEqual(LINE_H_LEN_NM, _by_uuid(doc, uuid_)["value_nm"],
                         "a rejected update must not move the stored value")

    def test_module_selftest_prints_a_pass_verdict_with_a_red_control(self):
        if ann is None:
            self.skipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
        package_root = pathlib.Path(__file__).resolve().parents[1]
        proc = subprocess.run([sys.executable, "-m", "cadcore.ops.annotation"],
                              cwd=str(package_root), capture_output=True, text=True, timeout=180)
        output = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(0, proc.returncode,
                         "module selftest must exit 0; tail:\n%s" % output[-2000:])
        self.assertRegex(output, r"ANNOTATION_OPS verdict=PASS ops=\d+ checks=\d+")
        self.assertNotIn("verdict=FAIL", output)
        self.assertIn("CHECK count_ruler_sees_red ok=1", output,
                      "the selftest must carry its own negative control (尺子看得见红)")
        self.assertIn("CHECK angular_micro_deg_add_and_update ok=1", output)
        self.assertIn("CHECK errors_and_atomicity ok=1", output)
        self.assertIn("CHECK all_operations_smoke ok=1", output)


# --------------------------------------------------------------------------------------
# 契约（冻结接口本身）
# --------------------------------------------------------------------------------------


class ContractTests(unittest.TestCase):
    def test_family_id_is_annotation(self):
        if ann is None:
            self.skipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
        self.assertEqual("annotation", getattr(ann, "FAMILY", None))

    def test_operations_registry_shape(self):
        if ann is None:
            self.skipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
        ops = getattr(ann, "OPERATIONS", None)
        self.assertIsInstance(ops, dict)
        self.assertGreaterEqual(len(ops), 1, "OPERATIONS must expose at least one operation")
        for name, op in ops.items():
            self.assertIsInstance(name, str)
            self.assertTrue(name and not name.startswith("_"), "bad operation name %r" % name)
            self.assertTrue(all(ch.islower() or ch.isdigit() or ch == "_" for ch in name),
                            "operation names are machine-readable ascii tokens: %r" % name)
            self.assertTrue(callable(op), "OPERATIONS[%r] is not callable" % name)
            self.assertIs(op, getattr(ann, name, None),
                          "operation %r must be exported as a module attribute" % name)

    def test_every_operation_matches_the_frozen_signature(self):
        if ann is None:
            self.skipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
        for name, op in ann.OPERATIONS.items():
            with self.subTest(op=name):
                params = [item for item in inspect.signature(op).parameters.values()
                          if item.name != "self"]
                self.assertTrue(params, "op %s declares no parameter" % name)
                head = params[0]
                self.assertIn(head.kind, (head.POSITIONAL_ONLY, head.POSITIONAL_OR_KEYWORD),
                              "op %s must take the sketch/document positionally" % name)
                self.assertEqual("doc", _PARAM_BY_NAME.get(head.name),
                                 "op %s first parameter %r must be the sketch/document" % (name, head.name))
                for item in params[1:]:
                    if item.kind is item.VAR_POSITIONAL:
                        continue
                    self.assertTrue(item.kind is item.KEYWORD_ONLY or item.kind is item.VAR_KEYWORD,
                                    "op %s parameter %r must be keyword-only (frozen: **kwargs)"
                                    % (name, item.name))

    def test_capability_coverage(self):
        if ann is None:
            self.skipTest("NA: cadcore.ops.annotation not landed (%s)" % _IMPORT_ERROR)
        missing = [cap for cap in ("dimension", "text") if not any(
            name in ann.OPERATIONS for name in _OP_ALIASES[cap])]
        self.assertEqual([], missing, "annotation family lacks core capabilities: %s" % missing)


# --------------------------------------------------------------------------------------
# 数值硬断言：尺寸（长度/半径/直径/角度/条数）
# --------------------------------------------------------------------------------------


class DimensionTests(unittest.TestCase):
    def test_linear_dimension_records_exact_length_nm(self):
        doc = new_document()
        uuids = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")
        self.assertEqual(1, len(uuids))
        item = _by_uuid(doc, uuids[0])
        self.assertTrue(_find_number(item, LINE_H_LEN_NM),
                        "annotation must carry the exact length %d nm: %r" % (LINE_H_LEN_NM, item))
        self.assertFalse(_find_number(item, LINE_H_LEN_NM + 1))

    def test_dimension_target_is_a_uuid_pair_that_resolves(self):
        doc = new_document()
        uuid_ = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        item = _by_uuid(doc, uuid_)
        self.assertIn("target", item, "top-level annotation must carry the L1 'target' field")
        target = item["target"]
        self.assertIsInstance(target, (list, tuple))
        self.assertEqual(2, len(target))
        self.assertEqual((LINE_H, "edge"), tuple(target))
        self.assertIsNotNone(cons.find_entity(sketch(doc), target[0]))
        self.assertEqual([], check_invariants(doc))

    def test_three_calls_give_three_distinct_annotations(self):
        doc = new_document()
        first = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")
        second = _add("dimension", doc, target=(LINE_V, "edge"), kind="linear",
                      value_nm=LINE_V_LEN_NM, text_key="cmd.dimension.linear")
        third = _add("dimension", doc, target=(CIRCLE, "edge"), kind="linear",
                     value_nm=CIRCLE_D_NM, text_key="cmd.dimension.linear")
        self.assertEqual(3, len(doc["annotations"]), "exactly three annotations, no extras")
        all_uuids = first + second + third
        self.assertEqual(3, len(set(all_uuids)), "annotation uuids must be unique")
        values = [item for item in (_by_uuid(doc, u) for u in all_uuids)]
        self.assertTrue(_find_number(values[0], LINE_H_LEN_NM))
        self.assertTrue(_find_number(values[1], LINE_V_LEN_NM))
        self.assertTrue(_find_number(values[2], CIRCLE_D_NM))
        self.assertEqual([], check_invariants(doc))

    def test_radial_dimension_records_exact_radius_nm(self):
        doc = new_document()
        uuid_ = _add("radial", doc, target=(CIRCLE, "edge"), kind="radial",
                     value_nm=CIRCLE_R_NM, text_key="cmd.dimension.radial")[0]
        self.assertTrue(_find_number(_by_uuid(doc, uuid_), CIRCLE_R_NM))

    def test_diameter_dimension_records_exact_diameter_nm(self):
        doc = new_document()
        uuid_ = _add("diameter", doc, target=(CIRCLE, "edge"), kind="diameter",
                     value_nm=CIRCLE_D_NM, text_key="cmd.dimension.diameter")[0]
        self.assertTrue(_find_number(_by_uuid(doc, uuid_), CIRCLE_D_NM))

    def test_angular_dimension_records_90_degrees(self):
        doc = new_document()
        uuid_ = _add("angular", doc, target=[(LINE_H, "edge"), (LINE_V, "edge")], kind="angular",
                     value_nm=ANGLE_DEG, text_key="cmd.dimension.angular")[0]
        item = _by_uuid(doc, uuid_)
        found = (_find_number(item, ANGLE_DEG)                      # degrees
                 or _find_number(item, int(ANGLE_DEG) * 1000000)    # micro-degrees
                 or _find_number(item, 1.5707963267948966, rel_tol=1e-9))  # radians
        self.assertTrue(found, "angular annotation must record 90 deg (deg/micro-deg/rad): %r" % item)

    def test_boundary_non_integer_length_raises(self):
        doc = new_document()
        exc = _expect_cad_error("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                                value_nm=LINE_H_LEN_NM + 0.5, text_key="cmd.dimension.linear")
        self.assertTrue(exc.reason)
        self.assertEqual([], doc["annotations"], "a rejected call must not half-write the document")

    def test_boundary_bool_length_raises(self):
        doc = new_document()
        _expect_cad_error("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                          value_nm=True, text_key="cmd.dimension.linear")
        self.assertEqual([], doc["annotations"])

    def test_boundary_unknown_target_raises(self):
        doc = new_document()
        exc = _expect_cad_error("dimension", doc, target=(_uid(999), "edge"), kind="linear",
                                value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")
        self.assertIn("reference", exc.reason,
                      "reason must name the reference failure, got %r" % exc.reason)
        self.assertEqual([], doc["annotations"])

    def test_boundary_empty_selection_raises(self):
        doc = new_document()
        _expect_cad_error("dimension", doc, target=[], kind="linear",
                          value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")
        self.assertEqual([], doc["annotations"])

    def test_boundary_negative_radius_raises(self):
        doc = new_document()
        _expect_cad_error("radial", doc, target=(CIRCLE, "edge"), kind="radial",
                          value_nm=-CIRCLE_R_NM, text_key="cmd.dimension.radial")
        self.assertEqual([], doc["annotations"])

    def test_boundary_nan_angle_raises(self):
        doc = new_document()
        _expect_cad_error("angular", doc, target=[(LINE_H, "edge"), (LINE_V, "edge")],
                          kind="angular", value_nm=float("nan"), text_key="cmd.dimension.angular")
        self.assertEqual([], doc["annotations"])


# --------------------------------------------------------------------------------------
# 数值硬断言：位置（文字/引线/中心标记）
# --------------------------------------------------------------------------------------


class PlacementTests(unittest.TestCase):
    AT_NM = (12000, 34000)

    def test_text_keeps_exact_position_and_i18n_key(self):
        doc = new_document()
        uuid_ = _add("text", doc, at_nm=self.AT_NM, text_key="cmd.dimension.linear")[0]
        item = _by_uuid(doc, uuid_)
        self.assertTrue(_find_pair(item, self.AT_NM),
                        "annotation must carry the exact position pair %r: %r" % (self.AT_NM, item))
        self.assertFalse(_find_pair(item, (self.AT_NM[0], self.AT_NM[1] + 1)))
        self.assertEqual("cmd.dimension.linear", item.get("text_key"))
        self.assertFalse(item.get("literal"))
        self.assertEqual([], check_invariants(doc))

    def test_literal_note_keeps_human_text_without_a_key(self):
        doc = new_document()
        uuid_ = _add("text", doc, at_nm=self.AT_NM, literal=True, text="NOTE-1")[0]
        item = _by_uuid(doc, uuid_)
        self.assertIs(True, item.get("literal"))
        self.assertTrue(_find_pair(item, self.AT_NM))
        self.assertIn("NOTE-1", [leaf for leaf in _walk(item) if isinstance(leaf, str)])
        self.assertEqual([], check_invariants(doc))

    def test_leader_carries_position_and_target(self):
        doc = new_document()
        uuid_ = _add("leader", doc, target=(CIRCLE, "edge"), at_nm=self.AT_NM,
                     text_key="cmd.dimension.radial")[0]
        item = _by_uuid(doc, uuid_)
        self.assertTrue(_find_pair(item, self.AT_NM))
        self.assertEqual((CIRCLE, "edge"), tuple(item["target"]))
        self.assertEqual([], check_invariants(doc))

    def test_center_mark_position_is_exact(self):
        doc = new_document()
        uuid_ = _add("center_mark", doc, target=(CIRCLE, "edge"), at_nm=(1000000, 250000))[0]
        self.assertTrue(_find_pair(_by_uuid(doc, uuid_), (1000000, 250000)))

    def test_boundary_position_must_be_integer_nm_pair(self):
        doc = new_document()
        _expect_cad_error("text", doc, at_nm=(12000, 34000.5), text_key="cmd.dimension.linear")
        _expect_cad_error("text", doc, at_nm=(12000,), text_key="cmd.dimension.linear")
        self.assertEqual([], doc["annotations"])


# --------------------------------------------------------------------------------------
# 事后可改（更新 / 删除）
# --------------------------------------------------------------------------------------


class MutationTests(unittest.TestCase):
    def test_remove_deletes_exactly_the_named_annotation(self):
        doc = new_document()
        keep = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                    value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        drop = _add("dimension", doc, target=(LINE_V, "edge"), kind="linear",
                    value_nm=LINE_V_LEN_NM, text_key="cmd.dimension.linear")[0]
        self.assertEqual(2, len(doc["annotations"]))
        removed = _call("remove", doc, target=drop)
        self.assertEqual([drop], removed)
        self.assertEqual(1, len(doc["annotations"]), "exactly one annotation must remain")
        self.assertEqual(keep, doc["annotations"][0]["uuid"])
        self.assertEqual([], check_invariants(doc))

    def test_update_value_changes_only_the_value(self):
        doc = new_document()
        uuid_ = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        before = dict(_by_uuid(doc, uuid_))
        new_value = LINE_H_LEN_NM + 25000
        result = _call("update", doc, target=uuid_, value_nm=new_value)
        self.assertEqual([uuid_], result)
        after = _by_uuid(doc, uuid_)
        self.assertEqual(1, len(doc["annotations"]), "update must not add or drop annotations")
        self.assertTrue(_find_number(after, new_value),
                        "updated annotation must carry the new exact value %d: %r" % (new_value, after))
        self.assertEqual(before.get("target"), after.get("target"), "target reference must not move")
        self.assertEqual([], check_invariants(doc))

    def test_boundary_update_unknown_uuid_raises(self):
        doc = new_document()
        _expect_cad_error("update", doc, target=_uid(999), value_nm=1000)

    def test_boundary_remove_unknown_uuid_raises(self):
        doc = new_document()
        _expect_cad_error("remove", doc, target=_uid(999))
        self.assertEqual([], doc["annotations"])


# --------------------------------------------------------------------------------------
# 组合用例：与已有命令（edit.modify / docmodel）组合
# --------------------------------------------------------------------------------------


class CombinationTests(unittest.TestCase):
    def test_with_modify_move_target_survives_and_geometry_is_exact(self):
        doc = new_document()
        uuid_ = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        moved = modify.move(sketch(doc), [LINE_H], dx_nm=1000, dy_nm=-2000)
        self.assertEqual([LINE_H], moved, "move rewrites in place: the entity uuid must not change")
        self.assertEqual({"x1": 1000, "y1": -2000, "x2": LINE_H_LEN_NM + 1000, "y2": -2000},
                         entity_geom(doc, LINE_H))
        target = _by_uuid(doc, uuid_)["target"]
        self.assertEqual(LINE_H, target[0], "annotation must still reference the moved entity")
        self.assertIsNotNone(cons.find_entity(sketch(doc), target[0]))
        self.assertTrue(_find_number(_by_uuid(doc, uuid_), LINE_H_LEN_NM),
                        "a translation does not change the measured length")
        self.assertEqual([], check_invariants(doc))

    def test_with_modify_mirror_two_annotations_have_distinct_targets(self):
        doc = new_document()
        first = _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                     value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        mirrored = modify.mirror(sketch(doc), [LINE_H], p1_nm=(0, 0), p2_nm=(0, 1000000),
                                 keep_original=True)
        self.assertEqual(1, len(mirrored))
        self.assertNotIn(mirrored[0], (LINE_H,))
        self.assertEqual({"x1": 0, "y1": 0, "x2": -LINE_H_LEN_NM, "y2": 0},
                         entity_geom(doc, mirrored[0]))
        second = _add("dimension", doc, target=(mirrored[0], "edge"), kind="linear",
                      value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]
        self.assertEqual(2, len(doc["annotations"]))
        targets = [tuple(_by_uuid(doc, u)["target"]) for u in (first, second)]
        self.assertEqual([(LINE_H, "edge"), (mirrored[0], "edge")], targets)
        self.assertEqual(2, len({item[0] for item in targets}), "targets must be distinct uuids")
        self.assertEqual([], check_invariants(doc))

    def test_with_docmodel_l1_roundtrip_is_byte_identical(self):
        doc = new_document()
        _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
             value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")
        _add("radial", doc, target=(CIRCLE, "edge"), kind="radial",
             value_nm=CIRCLE_R_NM, text_key="cmd.dimension.radial")
        text_a = document_to_l1(doc)
        self.assertEqual(text_a, document_to_l1(doc), "serialization must be deterministic")
        reloaded = document_from_l1(text_a)
        self.assertEqual(2, len(reloaded.annotations))
        self.assertEqual(text_a, document_to_l1(reloaded),
                         "annotations must survive the L1 roundtrip byte-identically")
        values = reloaded.annotations
        self.assertTrue(_find_number(values[0], LINE_H_LEN_NM))
        self.assertTrue(_find_number(values[1], CIRCLE_R_NM))
        for item in values:
            self.assertIs(type(item["value_nm"]), int,
                          "value_nm must stay an integer nanometre after roundtrip")


class StyleEditTableScaleTests(unittest.TestCase):
    """DIMSTYLE / DIMOVERRIDE / DIMEDIT·DIMTEDIT / STYLE / DDEDIT·FIND / MLEADERSTYLE /
    TABLE / TABLESTYLE / annotation scale（族门 must_have 里此前记 missing 的九条）。

    每项**两条都断言**：①落一条带 uuid 的记录（可回读）②真作用到注解 `args[...]`
    —— 只落记录不作用、或只作用不落记录，各是各的假绿。
    另记：样式/表格/编辑事务记录是**文档级附加容器**（`dim_styles[]` 等），本文件不对这类文档
    调 `check_invariants`（它只覆盖既有 L1 字段），不把"没覆盖"伪装成"覆盖了"。
    """

    AT_NM = (12000, 34000)
    POS_NM = (1500, 2500)
    ARROW_NM = (3500, 4500)

    def _op(self, name: str):
        ops = getattr(ann, "OPERATIONS", None)
        if not isinstance(ops, dict) or not callable(ops.get(name)):
            raise unittest.SkipTest("NA: OPERATIONS has no callable %r" % name)
        return ops[name]

    def _dim(self, doc: dict) -> str:
        return _add("dimension", doc, target=(LINE_H, "edge"), kind="linear",
                    value_nm=LINE_H_LEN_NM, text_key="cmd.dimension.linear")[0]

    @staticmethod
    def _records(doc: dict, key: str) -> list:
        return [item for item in doc.get(key, []) if isinstance(item, dict)]

    @staticmethod
    def _named(doc: dict, key: str, name: str) -> dict:
        for item in doc.get(key, []):
            if isinstance(item, dict) and item.get("name") == name:
                return item
        raise AssertionError("no record named %r in %s: %r" % (name, key, doc.get(key)))

    def test_dim_style_lands_a_record_and_assigns_only_unstyled_dims(self):
        doc = new_document()
        dim = self._dim(doc)
        first = self._op("dim_style")(doc, name="ISO-25", height_nm=2_500_000,
                                      arrow_size_nm=1_800_000, decimals=3)[0]
        records = self._records(doc, "dim_styles")
        self.assertEqual(1, len(records))
        self.assertEqual(first, records[0]["uuid"])
        self.assertEqual({"name": "ISO-25", "height_nm": 2_500_000, "arrow_size_nm": 1_800_000,
                          "decimals": 3},
                         {key: records[0][key] for key in
                          ("name", "height_nm", "arrow_size_nm", "decimals")})
        self.assertIs(True, records[0]["is_current"])
        self.assertEqual("ISO-25", _by_uuid(doc, dim)["args"]["dim_style"],
                         "the current style must really land on the dimension")
        second = self._op("dim_style")(doc, name="ASME", height_nm=3_000_000)[0]
        self.assertNotEqual(first, second)
        self.assertIs(False, records[0]["is_current"], "only one style is current")
        self.assertEqual("ISO-25", _by_uuid(doc, dim)["args"]["dim_style"],
                         "an explicitly styled dimension must not be silently rewritten")
        style = ann.dim_style_of(doc, subject=dim)
        self.assertEqual("ISO-25", style["name"])
        self.assertEqual(2_500_000, style["height_nm"])

    def test_dim_override_merges_onto_the_named_dimension(self):
        doc = new_document()
        dim = self._dim(doc)
        first = self._op("dim_override")(doc, subject=dim, height_nm=3_000_000,
                                         override_text="2x")[0]
        record = self._records(doc, "dim_overrides")[0]
        self.assertEqual(first, record["uuid"])
        self.assertEqual([dim], record["applies_to"])
        self.assertEqual({"height_nm": 3_000_000, "text": "2x"},
                         _by_uuid(doc, dim)["args"]["override"],
                         "the override must really land on the dimension")
        self._op("dim_override")(doc, subject=dim, arrow_size_nm=1_500_000)
        self.assertEqual({"height_nm": 3_000_000, "text": "2x", "arrow_size_nm": 1_500_000},
                         _by_uuid(doc, dim)["args"]["override"],
                         "a second override merges per item, it does not wipe the first")
        self.assertEqual(2, len(self._records(doc, "dim_overrides")))

    def test_dim_edit_sets_text_override_and_positions(self):
        doc = new_document()
        dim = self._dim(doc)
        uuid_ = self._op("dim_edit")(doc, subject=dim, override_text="EQ", pos_nm=self.POS_NM,
                                     arrow_pos_nm=self.ARROW_NM)[0]
        args = _by_uuid(doc, dim)["args"]
        self.assertEqual("EQ", args["text_override"])
        self.assertEqual(list(self.POS_NM), args["text_pos_nm"])
        self.assertEqual(list(self.ARROW_NM), args["arrow_pos_nm"])
        record = self._records(doc, "dim_edits")[0]
        self.assertEqual(uuid_, record["uuid"])
        self.assertEqual([dim], record["applies_to"])
        self.assertEqual({"text_override": "EQ", "text_pos_nm": list(self.POS_NM),
                          "arrow_pos_nm": list(self.ARROW_NM)}, record["edit"])

    def test_text_style_lands_a_record_and_assigns_only_unstyled_notes(self):
        doc = new_document()
        note = _add("text", doc, at_nm=self.AT_NM, literal=True, text="NOTE-1")[0]
        body = _add("text", doc, at_nm=self.AT_NM, text_key="cmd.dimension.linear")[0]
        self._op("text_style")(doc, name="NOTES", font="simplex", width_factor=0.8,
                               slant_micro=15_000, height_nm=2_500_000)
        record = self._named(doc, "text_styles", "NOTES")
        self.assertEqual(0.8, record["width_factor"])
        self.assertEqual(15_000, record["slant_micro"])
        self.assertEqual(2_500_000, record["height_nm"])
        self.assertEqual("NOTES", _by_uuid(doc, note)["args"]["text_style"])
        self.assertEqual("NOTES", _by_uuid(doc, body)["args"]["text_style"],
                         "mtext and text share the style assignment")

    def test_find_replace_rewrites_literal_text_only_and_journals(self):
        doc = new_document()
        literal = _add("text", doc, at_nm=self.AT_NM, literal=True, text="NOTE-1")[0]
        second = _add("text", doc, at_nm=self.AT_NM, literal=True, text="NOTE-1 tail")[0]
        keyed = _add("text", doc, at_nm=self.AT_NM, text_key="cmd.dimension.linear")[0]
        uuid_ = self._op("find_replace")(doc, search="NOTE-1", replacement="NOTE-2")[0]
        record = self._records(doc, "text_edits")[0]
        self.assertEqual(uuid_, record["uuid"])
        self.assertEqual("NOTE-2", _by_uuid(doc, literal)["args"]["text"])
        self.assertEqual("NOTE-2 tail", _by_uuid(doc, second)["args"]["text"])
        self.assertEqual([literal, second], record["matched"])
        self.assertEqual({"uuid": literal, "before": "NOTE-1", "after": "NOTE-2"},
                         record["replaced"][0])
        self.assertNotIn("text", _by_uuid(doc, keyed)["args"],
                         "key-based i18n text must not be rewritten by FIND")
        self._op("find_replace")(doc, search="ZZZ", replacement="X")
        self.assertEqual("NOTE-2", _by_uuid(doc, literal)["args"]["text"])
        self.assertEqual([], self._records(doc, "text_edits")[1]["matched"],
                         "a find with no match changes nothing and journals matched=[]")

    def test_mleader_style_lands_a_record_and_assigns_to_leaders(self):
        doc = new_document()
        leader = _add("leader", doc, target=(CIRCLE, "edge"), at_nm=self.AT_NM,
                      text_key="cmd.dimension.radial")[0]
        self._op("mleader_style")(doc, name="ML-ISO", arrow_size_nm=2_000_000,
                                  landing_gap_nm=800_000, char_height_nm=2_500_000)
        record = self._named(doc, "mleader_styles", "ML-ISO")
        self.assertEqual(2_000_000, record["arrow_size_nm"])
        self.assertEqual(800_000, record["landing_gap_nm"])
        self.assertEqual("ML-ISO", _by_uuid(doc, leader)["args"]["mleader_style"])

    def test_table_builds_rows_times_cols_cells_and_table_style_assigns(self):
        doc = new_document()
        uuid_ = self._op("table")(doc, at_nm=self.AT_NM, rows=3, cols=4,
                                  row_height_nm=8_000_000, col_width_nm=25_000_000,
                                  title_key="cmd.dimension.linear")[0]
        node = self._records(doc, "tables")[0]
        self.assertEqual(uuid_, node["uuid"])
        self.assertEqual("table", node["kind"])
        self.assertEqual([list(self.AT_NM)[0], list(self.AT_NM)[1]], node["at_nm"])
        cells = node["cells"]
        self.assertEqual(12, len(cells), "a 3x4 table has exactly 12 cells")
        self.assertEqual(12, len({cell["uuid"] for cell in cells}), "cell uuids must be unique")
        self.assertEqual({(row, col) for row in range(3) for col in range(4)},
                         {(cell["row"], cell["col"]) for cell in cells})
        self.assertEqual("cmd.dimension.linear", node["args"]["title_key"])
        self._op("table_style")(doc, name="TB-ISO", font_style="NOTES", grid_line_nm=350_000,
                                header_height_nm=9_000_000, data_height_nm=7_000_000)
        style = self._named(doc, "table_styles", "TB-ISO")
        self.assertEqual(350_000, style["grid_line_nm"])
        self.assertEqual("NOTES", style["font_style"])
        self.assertEqual("TB-ISO", node["args"]["table_style"],
                         "the table style must really land on the table")

    def test_annotation_scale_sets_flag_scales_and_model_height(self):
        doc = new_document()
        dim = self._dim(doc)
        uuid_ = self._op("annotation_scale")(doc, name="1:2", paper_height_nm=2_500_000)[0]
        record = self._records(doc, "annotation_scales")[0]
        self.assertEqual(uuid_, record["uuid"])
        self.assertEqual((1, 2, 5_000_000), (record["scale_num"], record["scale_den"],
                                             record["model_height_nm"]),
                         "2.5 mm paper height at 1:2 is 5 mm in model space")
        args = _by_uuid(doc, dim)["args"]
        self.assertIs(True, args["annotative"])
        self.assertEqual(["1:2"], args["annotation_scales"])
        self.assertEqual(5_000_000, args["model_height_nm"])
        self._op("annotation_scale")(doc, name="2:1", paper_height_nm=2_500_000)
        self.assertEqual(["1:2", "2:1"], _by_uuid(doc, dim)["args"]["annotation_scales"],
                         "an extra scale appends to the annotative set, sorted and deduped")
        self.assertEqual(1_250_000, _by_uuid(doc, dim)["args"]["model_height_nm"])
        reading = ann.annotation_scale_of(doc, subject=dim)
        self.assertEqual(1_250_000, reading["model_height_nm"])
        self.assertEqual(["1:2", "2:1"], reading["scales"])
        self._op("annotation_scale")(doc, name="1:2", paper_height_nm=2_500_000,
                                     annotative=False)
        self.assertIs(False, _by_uuid(doc, dim)["args"]["annotative"])
        self.assertEqual([], _by_uuid(doc, dim)["args"]["annotation_scales"])

    def test_boundaries_raise_specific_reasons_and_do_not_half_write(self):
        doc = new_document()
        dim = self._dim(doc)
        text = _add("text", doc, at_nm=self.AT_NM, literal=True, text="NOTE-1")[0]
        buckets = ("dim_styles", "dim_overrides", "dim_edits", "text_styles", "text_edits",
                   "mleader_styles", "tables", "table_styles", "annotation_scales")
        depth_before = {key: len(self._records(doc, key)) for key in buckets}
        cases = (
            ("dim_override", {"subject": _uid(999), "height_nm": 1_000}, "unknown_annotation"),
            ("dim_override", {"subject": dim}, "empty_override"),
            ("dim_override", {"subject": text, "height_nm": 1_000}, "wrong_annotation_kind"),
            ("dim_edit", {"subject": dim, "pos_nm": (1, 2.5)}, "bad_position"),
            ("dim_style", {"name": "  "}, "bad_style_name"),
            ("text_style", {"name": "NOTES", "width_factor": 0}, "bad_ratio"),
            ("text_style", {"name": "NOTES", "slant_micro": 90_000_000}, "out_of_range_angle"),
            ("find_replace", {"search": 7}, "bad_pattern"),
            ("find_replace", {"scope": "viewport"}, "bad_scope"),
            ("table", {"rows": 0, "cols": 2}, "invalid_count"),
            ("table", {"rows": 2, "cols": 2, "title_key": "人话文案"}, "bad_text_key"),
            ("table_style", {"name": "TB", "subject": _uid(999)}, "unknown_table"),
            ("annotation_scale", {"name": "one-to-two"}, "bad_scale_name"),
            ("annotation_scale", {"name": "1:2", "paper_height_nm": 0}, "non_positive_length"),
        )
        reasons = []
        for name, kwargs, expected in cases:
            with self.subTest(op=name, expect=expected):
                exc = _expect_cad_error_direct(self._op(name), doc, name, **kwargs)
                reasons.append(exc.reason)
                self.assertEqual(expected, exc.reason)
        depth_after = {key: len(self._records(doc, key)) for key in buckets}
        self.assertEqual(depth_before, depth_after,
                         "a rejected call must not half-write any record bucket")
        self.assertEqual([dim, text], [item["uuid"] for item in doc["annotations"]])


class DirectErrorRulerControlTests(unittest.TestCase):
    """阴性对照：证明直连边界尺子 `_expect_cad_error_direct` 不是空转 ——
    op 正常返回必须当场叫红，且 kwargs **原样**送到 op（不再被别名表吞掉）。"""

    def test_normal_return_is_reported_and_kwargs_are_passed_through(self):
        seen = []

        def _returns_normally(doc, **kwargs):
            seen.append((doc, kwargs))
            return []

        with self.assertRaises(AssertionError):
            _expect_cad_error_direct(_returns_normally, {"k": 1}, "selftest", name="ISO-25")
        self.assertEqual([({"k": 1}, {"name": "ISO-25"})], seen,
                         "the direct ruler must hand the declared kwargs to the op unchanged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
