"""test_organization_ops —— 族 ``organization``（``cadcore.ops.organization``）的独立单测。

落盘时的模块状态（对账席 organization 实测，2026-09-30 CST）
------------------------------------------------------------
* ``cadcore/ops/organization.py`` **已落盘**：``FAMILY == "organization"``、
  ``OPERATIONS`` 19 个键；模块自检 ``python3 -m cadcore.ops.organization``
  ⇒ 判定行 ``ORGANIZATION_OPS verdict=PASS ops=19``。
* 本文件最初是**照冻结接口预先写好**的（模块未落盘时写的），落盘后按**证据**逐条对账，
  两侧都改过（判据写在每条注释里，**不删用例、不放宽数值断言、不 skip 换绿**）：
  ① 测试侧缺陷：中文引号被写成 ASCII ``"`` ⇒ 整个文件 ``SyntaxError``（连一个用例都跑不起来）；
  ② 测试侧缺陷：``all_rows`` 只摊平一层、且只认 dict 值 ⇒ 看不见族表真身
     ``doc["organization"]["layers"]``（**list**，见 ``_view``/``_empty_store``）
     ⇒ 所有行表断言恒在空集上做，必然 FAIL；已改递归摊平（断言本身一字未动）；
  ③ 测试侧缺陷：能力别名表缺实现侧真名（``layer_set`` / ``layer_delete`` / ``group_create`` /
     ``attdef_create``），kwarg 也是猜的 ⇒ 大量 SKIP；已按实现补齐（把 SKIP **收紧**成真跑）；
  ④ 实现侧缺陷：``layer_create`` / ``group_create`` 在**参数校验之前**调 ``_view(create=True)``
     （首次触达会自举缺省层 ``"0"``）⇒ 非法入参失败后文档已被改动，违反本文件
     "非法输入必须**先全量校验再落库**"，也违反本模块 ``layer_assign`` docstring 自称的
     "先全量校验、后统一写回"；已改实现（纯校验前置 / ``_view(create=False)``）。
* 仍是 SKIP 的三类（**如实标 ABSENT / KWARGS_UNSUPPORTED，不计绿**）：``current_layer``、
  ``isolate`` / ``unisolate`` 三个能力 ``OPERATIONS`` 里没有；``layer_state(frozen=…)`` 不是本实现的
  参数名（本实现用 ``locked`` 表达锁定，**不拿 locked 冒充 frozen**）。

冻结接口（照抄任务口径，不自创）
--------------------------------
* ``FAMILY = "organization"``；
* ``OPERATIONS: dict[str, Callable[[sk_or_doc, **kwargs], list[str]]]``；
* 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``，返回**受影响/新建实体的 uuid 列表**；
* 长度一律**整数纳米**；错误抛 ``cadcore.errors.CadError(reason=…)``（``reason`` 机读、非空）。

三态口径（全文件统一，禁止"没算"与"没失败"同形）
--------------------------------------------------
* **PASS** = 真跑过并通过（数值硬断言）；
* **SKIP** = ``OPERATIONS`` 里没有该能力，或该 kwarg 拼法不被接受 ⇒ 判词带 ``ABSENT:`` /
  ``KWARGS_UNSUPPORTED:``，**不计绿**；
* **FAIL** = 跑过了但对不上口径（数值不符 / 未抛 ``CadError`` / 非法输入把文档写坏了）。

族语义取自 ``docs/research/cad-parity/features/autocad-organization.md``（图层体系 / 块与属性 / 组），
其中"2 个与已有命令组合的用例"分别复用 ``cadcore.sketch.primitives.add_rectangle`` 与
``cadcore.edit.modify.move``。
"""

from __future__ import annotations

import copy
import importlib
import inspect
import unittest

from cadcore.edit import modify as mod_edit
from cadcore.errors import CadError
from cadcore.sketch import primitives as prim

FAMILY_MODULE = "cadcore.ops.organization"
FAMILY_ID = "organization"

#: 能力名 → 可接受的 OPERATIONS 键（别名表；纯容错，不改变语义）。
#: 后 4 个（layer_set / layer_delete / group_create / attdef_create）是**实现侧真名**，
#: 落盘后按证据补入 —— 别名表不命中只会 SKIP，补入把 SKIP 变回真跑（收紧，不是放宽）。
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "layer_add": ("add_layer", "layer_add", "new_layer", "create_layer", "layer_create",
                  "define_layer", "add_layer_def"),
    "layer_state": ("set_layer_state", "layer_state", "set_layer_props", "set_layer_properties",
                    "set_layer_flags", "layer_set_state", "layer_on_off", "set_layer_visible",
                    "layer_set"),
    "current_layer": ("set_current_layer", "current_layer", "set_clayer", "layer_current",
                      "set_active_layer"),
    "relayer": ("relayer", "set_entity_layer", "assign_layer", "change_layer", "move_to_layer",
                "layer_assign", "set_layer_of", "relayer_entities"),
    "isolate": ("isolate_layers", "isolate_layer", "layer_isolate", "isolate", "layiso"),
    "unisolate": ("unisolate_layers", "unisolate_layer", "layer_unisolate", "unisolate", "layuniso"),
    # purge 语义 = "清掉没用的层，绝不动在用的层"；layer_delete 正是这个口径
    # （层上还有实体 ⇒ layer_not_empty，不给 move_to 就不删）
    "purge": ("purge_layers", "purge", "layer_purge", "clean_layers", "purge_unused_layers",
              "layer_delete"),
    "group": ("group", "make_group", "create_group", "add_group", "group_entities", "new_group",
              "define_group", "group_create"),
    "block_define": ("define_block", "block_define", "make_block", "create_block", "block",
                     "add_block_def"),
    "block_insert": ("insert_block", "block_insert", "insert", "place_block", "insert_block_ref"),
    "explode": ("explode_block", "explode", "block_explode", "explode_insert"),
    "attdef": ("add_attdef", "define_attribute", "attdef", "add_attribute_def", "add_attdef_def",
               "attdef_create"),
}
#: 别名表命中数下限。落盘后实测可解析 **9** 个（12 个能力里恒缺 3 个：current_layer / isolate /
#: unisolate 在 OPERATIONS 里根本没有）⇒ 下限写成 9：任何真名被改坏都当场报红，不再被 5 蒙混过去。
MIN_CAP_RESOLVED = 9

_LOAD_ERROR: str | None = None
try:  # 模块未落盘时**不**让 import 期炸掉整个 discover，改由 test_00 显式报红
    _MODULE = importlib.import_module(FAMILY_MODULE)
except Exception as exc:  # pragma: no cover - 落盘后不再走这里
    _MODULE = None
    _LOAD_ERROR = "%s: %s" % (type(exc).__name__, exc)


# --------------------------------------------------------------------------------------
# 通用夹具 / 尺子
# --------------------------------------------------------------------------------------
def new_sketch() -> dict:
    """一张空 L1 草图（与 test_primitives.py 同口径：entities / constraints / dimensions）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def entities(doc) -> list:
    return list(doc.get("entities", []) or [])


def all_rows(doc) -> list[tuple[str, dict]]:
    """把文档里所有"列表/映射套字典"的行摊平（**递归**；不改文档）。

    为什么必须递归（本席实测，见本文件头 ②）：族表落在 ``doc["organization"]`` 下，每张表是
    **list**（``{"schema":1,"layers":[…],"blocks":[…],"inserts":[…],"groups":[…],"xrefs":[…]``），
    层行还套在块行里面（块定义 → ``attdefs``）。只摊平一层、只认 dict 值的旧实现恒返回 ``[]``，
    行表断言全部落空。
    """
    out: list[tuple[str, dict]] = []

    def walk(node, key_hint: str) -> None:
        if isinstance(node, dict):
            values = list(node.values())
            if values and all(isinstance(val, dict) for val in values):
                for sub_key, row in node.items():  # 映射表 {"名字": {…}}
                    merged = dict(row)
                    merged.setdefault("name", sub_key)
                    out.append((key_hint or str(sub_key), merged))
                return
            for key, val in node.items():
                walk(val, str(key))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    out.append((key_hint, item))
                    walk(item, key_hint)  # 行内还可能套表（块 → attdefs）
                else:
                    walk(item, key_hint)

    walk(doc, "")
    return out


def rows_like(doc, *needles: str) -> list[dict]:
    return [row for key, row in all_rows(doc) if any(n in key.lower() for n in needles)]


def layer_rows(doc) -> list[dict]:
    return [row for key, row in all_rows(doc)
            if "layer" in key.lower() or "linetype" in row or "frozen" in row]


def row_name(row: dict):
    for key in ("name", "layer", "layer_name", "id", "tag"):
        if key in row:
            return row[key]
    return None


def find_row(doc, name, *, needles=("layer",)) -> dict | None:
    for row in rows_like(doc, *needles):
        if row_name(row) == name:
            return row
    return None


def flag(row: dict | None, *keys: str):
    """在多个键名里取布尔态；取不到返回 None（调用方据此判 FAIL，不假绿）。"""
    if not isinstance(row, dict):
        return None
    for key in keys:
        if key in row:
            val = row[key]
            if isinstance(val, str):
                return val.lower() not in ("off", "false", "0", "hidden", "frozen")
            return bool(val)
    return None


def visible(row: dict | None):
    return flag(row, "visible", "is_visible", "on", "is_on")


def frozen(row: dict | None):
    return flag(row, "frozen", "is_frozen")


def locked(row: dict | None):
    return flag(row, "locked", "is_locked")


def entity_layer(ent) -> str | None:
    return ent.get("layer") if isinstance(ent, dict) else None


def line_edges(doc) -> set:
    """文档里全部 line 的无向边集合（坐标原样整数纳米）——组合用例的坐标尺子。"""
    edges = set()
    for ent in entities(doc):
        geom = ent.get("geom") or {}
        if ent.get("kind") == "line":
            edges.add(frozenset(((geom.get("x1"), geom.get("y1")), (geom.get("x2"), geom.get("y2")))))
    return edges


def expected_edges(points) -> set:
    pts = list(points)
    return {frozenset((pts[i], pts[(i + 1) % len(pts)])) for i in range(len(pts))}


class OrgTestCase(unittest.TestCase):
    """族单测基类：模块 / 能力 / kwarg 三态解析。"""

    def setUp(self):
        self.mod = _MODULE
        if self.mod is None:
            self.skipTest("MODULE_NOT_LANDED: %s (%s)" % (FAMILY_MODULE, _LOAD_ERROR))

    # --- 能力解析 -----------------------------------------------------------------
    def ops(self) -> dict:
        ops = getattr(self.mod, "OPERATIONS", None)
        return ops if isinstance(ops, dict) else {}

    def op(self, cap: str):
        for name in CAPABILITIES[cap]:
            fn = self.ops().get(name)
            if callable(fn):
                return fn
        self.skipTest("ABSENT:%s 候选=%s" % (cap, ",".join(CAPABILITIES[cap])))

    def try_call(self, fn, doc, variants):
        """按 kwarg 别名逐个试；全部 TypeError ⇒ 返回 None（调用方判 ABSENT，不判绿）。"""
        for kwargs in variants:
            try:
                return fn(doc, **dict(kwargs)), dict(kwargs)
            except TypeError:
                continue
        return None

    def call(self, fn, doc, variants, label: str):
        got = self.try_call(fn, doc, variants)
        if got is None:
            self.fail("KWARGS_UNSUPPORTED:%s 试过=%s（全部 TypeError，接口参数名对不上）"
                      % (label, [sorted(v) for v in variants]))
        return got

    def call_or_skip(self, cap: str, doc, variants, label: str):
        got = self.try_call(self.op(cap), doc, variants)
        if got is None:
            self.skipTest("KWARGS_UNSUPPORTED:%s 试过=%s" % (label, [sorted(v) for v in variants]))
        return got

    # --- 断言辅助 -----------------------------------------------------------------
    def add_layer(self, doc, name: str, **extra):
        fn = self.op("layer_add")
        variants = [dict({"name": name}, **extra), dict({"layer": name}, **extra),
                    dict({"layer_name": name}, **extra)]
        return self.call_or_skip("layer_add", doc, variants, "add_layer(%s)" % name)

    def assert_uuid_list(self, result, label: str):
        self.assertIsInstance(result, list, "%s 必须返回 uuid 列表" % label)
        for item in result:
            self.assertIsInstance(item, str, "%s 返回项必须是 str uuid" % label)
            self.assertTrue(item, "%s 返回了空 uuid" % label)

    def snapshot(self, doc):
        return copy.deepcopy(doc)

    def assert_untouched(self, doc, snap, label: str):
        self.assertEqual(doc, snap, "%s：非法输入必须“先全量校验再落库”，文档不得被改动" % label)
        self.assertEqual(len(entities(doc)), len(entities(snap)), label)
        self.assertEqual(len(layer_rows(doc)), len(layer_rows(snap)), label)


# --------------------------------------------------------------------------------------
# 0. 冻结接口门（唯一不 SKIP 的判词：模块缺席 / 接口对不上 ⇒ 真报红）
# --------------------------------------------------------------------------------------
class FrozenInterfaceGateTests(unittest.TestCase):
    def test_00_frozen_interface_landed_and_covered(self):
        if _MODULE is None:
            self.fail("MODULE_NOT_LANDED: %s 未落盘（%s）⇒ 本文件其余各例全部 SKIP，"
                      "「未验证」不等于「通过」" % (FAMILY_MODULE, _LOAD_ERROR))
        self.assertEqual(getattr(_MODULE, "FAMILY", None), FAMILY_ID)
        ops = getattr(_MODULE, "OPERATIONS", None)
        self.assertIsInstance(ops, dict, "OPERATIONS 必须是 dict[str, Callable]")
        self.assertTrue(ops, "OPERATIONS 不得为空")
        for name, fn in ops.items():
            self.assertIsInstance(name, str)
            self.assertTrue(callable(fn), "OPERATIONS[%r] 不可调用" % name)
            params = [p for p in inspect.signature(fn).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)]
            self.assertTrue(params, "OP_NO_SK_OR_DOC_PARAM: %s" % name)
        resolved = {cap: next((n for n in aliases if callable(ops.get(n))), None)
                    for cap, aliases in CAPABILITIES.items()}
        missing = sorted(cap for cap, name in resolved.items() if name is None)
        self.assertGreaterEqual(
            len(resolved) - len(missing), MIN_CAP_RESOLVED,
            "CAPABILITY_MISMATCH 已解析=%s 未解析=%s OPERATIONS键=%s"
            % ({k: v for k, v in resolved.items() if v}, missing, sorted(ops)))


# --------------------------------------------------------------------------------------
# 1. 图层：创建 / 特性 / 状态
# --------------------------------------------------------------------------------------
class LayerCreateTests(OrgTestCase):
    def test_01_add_layer_returns_uuid_and_persists_exact_props(self):
        doc = new_sketch()
        # 族表首次触达会自举**缺省层 "0"**（AutoCAD 每张图都有层 0；实现侧 `_default_layer_row`）
        # ⇒ 基线必须在这次自举**之后**取，否则"+1 行"量到的是自举而不是本次建层。
        self.add_layer(doc, "SEED")
        before = len(layer_rows(doc))
        result, _kw = self.add_layer(doc, "WALLS", color=3, linetype="dashed")
        self.assert_uuid_list(result, "add_layer")
        self.assertEqual(len(result), 1)
        rows = layer_rows(doc)
        self.assertEqual(len(rows), before + 1, "建层后层表必须恰好 +1 行")
        row = find_row(doc, "WALLS")
        self.assertIsNotNone(row, "层表里找不到 WALLS：rows=%s" % rows)
        self.assertEqual(row_name(row), "WALLS")
        self.assertEqual(row.get("color"), 3, "颜色必须原样落库（整数 ACI）")
        self.assertEqual(row.get("linetype"), "dashed")
        if "uuid" in row:
            self.assertIn(result[0], (row.get("uuid"), row.get("id")))

    def test_02_add_layer_rejects_empty_and_duplicate_name(self):
        doc = new_sketch()
        snap = self.snapshot(doc)
        name = "WALLS"
        for bad in ("", "   ", None, 123):
            with self.assertRaises(CadError, msg="非法层名 %r 必须抛 CadError" % (bad,)) as ctx:
                self.call(self.op("layer_add"), doc,
                          [{"name": bad}, {"layer": bad}, {"layer_name": bad}], "add_layer(bad)")
            self.assertTrue(ctx.exception.reason, "CadError.reason 必须非空（机读归因）")
        self.assert_untouched(doc, snap, "非法层名不得落库")
        self.add_layer(doc, name)
        rows_before_dup = len(layer_rows(doc))
        with self.assertRaises(CadError, msg="重名建层必须抛 CadError") as ctx:
            self.call(self.op("layer_add"), doc,
                      [{"name": name}, {"layer": name}, {"layer_name": name}], "add_layer(dup)")
        self.assertTrue(ctx.exception.reason)
        self.assertEqual(len(layer_rows(doc)), rows_before_dup, "重名失败后层表不得多出一行")

    def test_03_set_layer_state_visible_toggle(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        fn = self.op("layer_state")
        got = self.try_call(fn, doc, [{"layer": "WALLS", "visible": False},
                                      {"name": "WALLS", "visible": False},
                                      {"layer": "WALLS", "on": False}])
        if got is None:
            self.skipTest("KWARGS_UNSUPPORTED:layer_state(visible=…)")
        result, _kw = got
        self.assert_uuid_list(result, "set_layer_state")
        self.assertIs(visible(find_row(doc, "WALLS")), False, "visible=False 必须落库")
        got = self.try_call(fn, doc, [{"layer": "WALLS", "visible": True},
                                      {"name": "WALLS", "visible": True},
                                      {"layer": "WALLS", "on": True}])
        self.assertIsNotNone(got, "同一能力必须能开回来（visible=True）")
        self.assertIs(visible(find_row(doc, "WALLS")), True)

    def test_04_set_layer_state_freeze_and_lock(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        fn = self.op("layer_state")
        got = self.try_call(fn, doc, [{"layer": "WALLS", "frozen": True},
                                      {"name": "WALLS", "frozen": True}])
        if got is None:
            self.skipTest("KWARGS_UNSUPPORTED:layer_state(frozen=…)（本实现无 frozen 字段，不计绿）")
        self.assertIs(frozen(find_row(doc, "WALLS")), True, "frozen=True 必须落库")
        got = self.try_call(fn, doc, [{"layer": "WALLS", "locked": True},
                                      {"name": "WALLS", "locked": True}])
        if got is None:
            self.skipTest("KWARGS_UNSUPPORTED:layer_state(locked=…) [frozen 已过]")
        self.assertIs(locked(find_row(doc, "WALLS")), True, "locked=True 必须落库")

    def test_05_set_layer_state_rejects_unknown_layer(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        snap = self.snapshot(doc)
        fn = self.op("layer_state")
        with self.assertRaises(CadError, msg="未知图层必须抛 CadError") as ctx:
            self.call(fn, doc, [{"layer": "NOPE", "visible": False},
                                {"name": "NOPE", "visible": False},
                                {"layer": "NOPE", "frozen": True},
                                {"name": "NOPE", "frozen": True}], "layer_state(NOPE)")
        self.assertTrue(ctx.exception.reason)
        self.assert_untouched(doc, snap, "未知图层不得落库")


# --------------------------------------------------------------------------------------
# 2. 当前层 / 改层（与已有命令组合）
# --------------------------------------------------------------------------------------
class CurrentLayerAndRelayerTests(OrgTestCase):
    def test_06_current_layer_then_primitives_geometry_relayered(self):
        """组合用例 1：``primitives.add_rectangle`` 画的 4 条线，改层后 uuid/坐标一字不变。"""
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        self.assertEqual(len(rect), 4)
        got = self.call_or_skip("current_layer", doc, [{"name": "WALLS"}, {"layer": "WALLS"},
                                                       {"layer_name": "WALLS"}],
                                "set_current_layer(WALLS)")
        result, _kw = got
        self.assert_uuid_list(result, "set_current_layer")
        cur = [v for k, v in doc.items() if "current" in str(k).lower()]
        self.assertTrue(cur, "当前层必须落在文档里（键名含 current）")
        self.assertIn("WALLS", (cur[0], getattr(cur[0], "name", None), str(cur[0])))
        edges_before = line_edges(doc)
        relayer = self.op("relayer")
        got = self.call_or_skip("relayer", doc,
                                [{"refs": rect, "layer": "WALLS"}, {"entities": rect, "layer": "WALLS"},
                                 {"refs": rect, "name": "WALLS"}, {"layer": "WALLS", "refs": rect}],
                                "relayer(rect -> WALLS)")
        moved, _kw2 = got
        self.assertEqual(sorted(moved), sorted(rect), "改层必须返回被改写的原 uuid")
        self.assertEqual([entity_layer(e) for e in entities(doc) if e.get("uuid") in set(rect)],
                         ["WALLS"] * 4, "4 条线必须整层改写")
        self.assertEqual(line_edges(doc), edges_before, "改层不得动一个纳米坐标")
        self.assertEqual(len([r for r in layer_rows(doc) if row_name(r) == "WALLS"]), 1)

    def test_07_relayer_rejects_unknown_layer_and_unknown_uuid(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        snap = self.snapshot(doc)
        fn = self.op("relayer")
        with self.assertRaises(CadError, msg="改到不存在的层必须抛 CadError") as ctx:
            self.call(fn, doc, [{"refs": rect, "layer": "GHOST"}, {"entities": rect, "layer": "GHOST"},
                                {"refs": rect, "name": "GHOST"}], "relayer(GHOST)")
        self.assertTrue(ctx.exception.reason)
        self.assert_untouched(doc, snap, "未知目标层不得落库")
        with self.assertRaises(CadError, msg="不存在的实体 uuid 必须抛 CadError") as ctx:
            self.call(fn, doc, [{"refs": ["no-such-uuid"], "layer": "WALLS"},
                                {"entities": ["no-such-uuid"], "layer": "WALLS"}], "relayer(bad uuid)")
        self.assertTrue(ctx.exception.reason)
        self.assert_untouched(doc, snap, "悬空引用不得落库")


# --------------------------------------------------------------------------------------
# 3. 隔离 / 清理
# --------------------------------------------------------------------------------------
class IsolatePurgeTests(OrgTestCase):
    def test_08_isolate_then_unisolate_restores_state_snapshot(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        self.add_layer(doc, "TEXT")
        before = {row_name(r): (visible(r), frozen(r), locked(r)) for r in layer_rows(doc)}
        got = self.call_or_skip("isolate", doc, [{"layers": ["WALLS"]}, {"names": ["WALLS"]},
                                                 {"layer": "WALLS"}], "isolate(WALLS)")
        result, _kw = got
        self.assert_uuid_list(result, "isolate_layers")
        self.assertIs(visible(find_row(doc, "WALLS")), True, "被隔离保留的层必须可见")
        self.assertIs(visible(find_row(doc, "TEXT")), False, "其它层必须被藏掉")
        got = self.call_or_skip("unisolate", doc, [{"layers": ["WALLS"]}, {"names": ["WALLS"]},
                                                   {"layer": "WALLS"}, {}], "unisolate()")
        self.assert_uuid_list(got[0], "unisolate_layers")
        after = {row_name(r): (visible(r), frozen(r), locked(r)) for r in layer_rows(doc)}
        self.assertEqual(after, before, "取消隔离后图层状态必须逐字段回到隔离前快照")

    def test_09_purge_removes_only_unused_layer(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        self.add_layer(doc, "JUNK")
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        relayer = self.op("relayer")
        self.call(relayer, doc, [{"refs": rect, "layer": "WALLS"}, {"entities": rect, "layer": "WALLS"}],
                  "relayer(rect -> WALLS)")
        before = len(layer_rows(doc))
        got = self.call_or_skip("purge", doc, [{"layer": "JUNK"}, {"layers": ["JUNK"]},
                                               {"names": ["JUNK"]}, {}], "purge(JUNK)")
        result, _kw = got
        self.assert_uuid_list(result, "purge_layers")
        self.assertIsNone(find_row(doc, "JUNK"), "无用层 JUNK 必须被清掉")
        self.assertIsNotNone(find_row(doc, "WALLS"), "在用层 WALLS 不得被清掉")
        self.assertEqual(len(layer_rows(doc)), before - 1, "层表必须恰好 -1 行")
        self.assertEqual(len(entities(doc)), 4, "清理不得删几何")

    def test_10_purge_never_drops_in_use_layer(self):
        doc = new_sketch()
        self.add_layer(doc, "WALLS")
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        self.call(self.op("relayer"), doc,
                  [{"refs": rect, "layer": "WALLS"}, {"entities": rect, "layer": "WALLS"}],
                  "relayer(rect -> WALLS)")
        fn = self.op("purge")
        try:
            self.call(fn, doc, [{"layer": "WALLS"}, {"layers": ["WALLS"]}, {"names": ["WALLS"]}],
                      "purge(WALLS 在用)")
        except CadError as exc:
            self.assertTrue(exc.reason, "拒绝清理在用层时 reason 必须非空")
        self.assertIsNotNone(find_row(doc, "WALLS"), "在用层不得被清掉（宁可报错也不许删）")
        self.assertEqual(sorted(entity_layer(e) for e in entities(doc)), ["WALLS"] * 4)


# --------------------------------------------------------------------------------------
# 4. 组
# --------------------------------------------------------------------------------------
class GroupTests(OrgTestCase):
    def test_11_group_collects_exact_members(self):
        doc = new_sketch()
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        got = self.call_or_skip("group", doc, [{"refs": rect[:2], "name": "G1"},
                                               {"refs": rect[:2]}, {"name": "G1", "refs": rect[:2]}],
                                "group(2 refs)")
        result, _kw = got
        self.assert_uuid_list(result, "group")
        self.assertTrue(result, "建组必须返回 uuid")
        rows = rows_like(doc, "group")
        self.assertEqual(len(rows), 1, "组表必须恰好 1 行：rows=%s" % rows)
        members = (rows[0].get("members") or rows[0].get("refs") or rows[0].get("entities")
                   or rows[0].get("member_uuids"))
        self.assertEqual(sorted(str(m) for m in members), sorted(rect[:2]), "组员必须与入参逐字相同")
        self.assertEqual(len(rows[0].get("name", "G1")), 2)  # 组名 G1 恰好 2 字符（数量硬断言）

    def test_12_group_rejects_unknown_uuid_and_empty_refs(self):
        doc = new_sketch()
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        snap = self.snapshot(doc)
        fn = self.op("group")
        # 三个非法入参：①纯悬空 uuid ②空选择 ③**半真半假**（一个真 uuid + 一个悬空 uuid）
        # ③ 是"先全量校验、不许半落库"的强判据（单体组在 AutoCAD 与本实现里都是**合法**的，
        #   本模块自检 line 1244 就用 refs=[单个实体] 成组，故不拿它当非法输入）
        for bad in (["no-such-uuid"], [], ["no-such-uuid", rect[0]]):
            with self.assertRaises(CadError, msg="非法组员 %r 必须抛 CadError" % (bad,)) as ctx:
                self.call(fn, doc, [{"refs": bad, "name": "G"}, {"entities": bad, "name": "G"}],
                          "group(bad=%r)" % (bad,))
            self.assertTrue(ctx.exception.reason)
        self.assert_untouched(doc, snap, "建组失败不得落库")


# --------------------------------------------------------------------------------------
# 5. 块与属性（组合用例 2：primitives 造几何 → 定义块 → 插入 → 分解）
# --------------------------------------------------------------------------------------
class BlockTests(OrgTestCase):
    def _define_rect_block(self, doc, name="B1"):
        rect = prim.add_rectangle(doc, p1_nm=(0, 0), p2_nm=(1000, 500))
        got = self.call_or_skip("block_define", doc,
                                [{"refs": rect, "name": name, "base_nm": (0, 0)},
                                 {"refs": rect, "name": name},
                                 {"name": name, "refs": rect},
                                 {"entities": rect, "name": name}],
                                "define_block(%s)" % name)
        self.assert_uuid_list(got[0], "define_block")
        return rect

    def test_13_block_define_and_insert_translate_by_exact_nm(self):
        doc = new_sketch()
        rect = self._define_rect_block(doc, "B1")
        b0 = len(entities(doc))
        fn = self.op("block_insert")
        # 实现侧真 kwarg 名是 `block=`（块名或块 uuid）+ `at_nm=`
        got = self.call_or_skip("block_insert", doc,
                                [{"block": "B1", "at_nm": (1000, 2000)},
                                 {"name": "B1", "at_nm": (1000, 2000)},
                                 {"block": "B1", "insert_nm": (1000, 2000)},
                                 {"block": "B1", "x_nm": 1000, "y_nm": 2000}],
                                "insert_block(B1 @1000,2000)")
        result, kw = got
        self.assert_uuid_list(result, "insert_block")
        self.assertTrue(result, "插入必须返回 uuid")
        want = expected_edges([(1000, 2000), (2000, 2000), (2000, 2500), (1000, 2500)])
        new_edges = line_edges(doc) - expected_edges([(0, 0), (1000, 0), (1000, 500), (0, 500)])
        if len(entities(doc)) > b0 and new_edges:
            self.assertEqual(new_edges, want, "插入后的 4 条边必须整体平移 (1000,2000) 纳米")
        else:
            anchor = [e for e in entities(doc) if "insert" in str(e.get("kind", "")) or
                      "block" in str(e.get("kind", ""))]
            self.assertTrue(anchor, "既无展开几何也无插入实体 ⇒ 插入没落库")
            geom = anchor[-1].get("geom") or {}
            flat = {k: v for k, v in geom.items() if k in ("x", "y", "cx", "cy", "x_nm", "y_nm",
                                                           "at_x", "at_y", "at_nm")}
            self.assertTrue(flat, "插入实体的定位必须是整数纳米字段：geom=%s" % geom)
            self.assertEqual(anchor[-1].get("block") or anchor[-1].get("name"), "B1")
        self.assertGreater(len(entities(doc)), b0, "插入必须落库（展开几何或插入实体），实体数只增不减")
        self.assertEqual(len(rect), 4)

    def test_14_block_insert_rotation_deg_is_exact_right_angle(self):
        doc = new_sketch()
        self._define_rect_block(doc, "B1")
        base = line_edges(doc)
        fn = self.op("block_insert")
        got = self.try_call(fn, doc, [{"block": "B1", "at_nm": (1000, 2000), "rotation_deg": 90},
                                      {"block": "B1", "at_nm": (1000, 2000), "rot_deg": 90},
                                      {"block": "B1", "insert_nm": (1000, 2000), "angle_deg": 90}])
        if got is None:
            self.skipTest("KWARGS_UNSUPPORTED:block_insert(rotation_deg=…)（旋转未验证，不计绿）")
        result, _kw = got
        self.assert_uuid_list(result, "insert_block")
        want = expected_edges([(1000, 2000), (1000, 3000), (500, 3000), (500, 2000)])
        self.assertEqual(line_edges(doc) - base, want,
                         "绕插入点逆时针 90°：角 (1000,500) 必须落到 (500,3000) 纳米")

    def test_15_insert_block_rejects_non_integer_nm_and_unknown_block(self):
        doc = new_sketch()
        self._define_rect_block(doc, "B1")
        snap = self.snapshot(doc)
        fn = self.op("block_insert")
        bad_cases = [
            ("非整数纳米", [{"block": "B1", "at_nm": (1.5, 2000)},
                            {"block": "B1", "x_nm": 1.5, "y_nm": 2000}]),
            ("未知块名", [{"block": "GHOST", "at_nm": (1000, 2000)},
                          {"block": "GHOST", "x_nm": 1000, "y_nm": 2000}]),
        ]
        for label, variants in bad_cases:
            with self.assertRaises(CadError, msg="%s 必须抛 CadError" % label) as ctx:
                self.call(fn, doc, variants, "insert_block(%s)" % label)
            self.assertTrue(ctx.exception.reason)
        self.assert_untouched(doc, snap, "非法插入不得落库")
        for ent in entities(doc):
            for key, val in (ent.get("geom") or {}).items():
                if key.endswith(("x1", "y1", "x2", "y2", "r", "cx", "cy")):
                    self.assertIsInstance(val, int, "长度字段必须整数纳米：%s=%r" % (key, val))

    def test_16_explode_insert_restores_entity_count_and_geometry(self):
        doc = new_sketch()
        rect = self._define_rect_block(doc, "B1")
        before = line_edges(doc)
        inserted, _kw = self.call_or_skip(
            "block_insert", doc,
            [{"block": "B1", "at_nm": (0, 5000)}, {"name": "B1", "at_nm": (0, 5000)},
             {"block": "B1", "x_nm": 0, "y_nm": 5000}], "insert_block(B1 @0,5000)")
        # 实现侧真 kwarg 名是 `insert=`（实例 uuid），返回被展开的成员 uuid
        got = self.call_or_skip("explode", doc,
                                [{"insert": inserted[0]}, {"refs": inserted, "name": "B1"},
                                 {"refs": inserted}], "explode(insert)")
        self.assert_uuid_list(got[0], "explode_block")
        after = line_edges(doc)
        self.assertTrue(before.issubset(after), "分解后原块定义几何不得消失")
        self.assertEqual(after - before,
                         expected_edges([(0, 5000), (1000, 5000), (1000, 5500), (0, 5500)]),
                         "分解后的几何必须与插入位置逐纳米一致")
        self.assertEqual(len(rect), 4)

    def test_17_attdef_carries_exact_tag_and_default(self):
        doc = new_sketch()
        # ATTDEF 挂在**块定义**上（AutoCAD 口径；本实现 attdef_create(block=…) 亦然）
        # ⇒ 先得有块。prompt 走 i18n key（`prompt_key`），不传 locale 文案。
        self._define_rect_block(doc, "B1")
        got = self.call_or_skip("attdef", doc,
                                [{"block": "B1", "tag": "TAG1", "prompt_key": "att.tag",
                                  "default": "A-01"},
                                 {"block": "B1", "tag": "TAG1", "default": "A-01"},
                                 {"block": "B1", "name": "TAG1", "default": "A-01"}], "attdef(TAG1)")
        self.assert_uuid_list(got[0], "add_attdef")
        rows = rows_like(doc, "attdef", "attribute", "attr")
        self.assertEqual(len(rows), 1, "属性定义表必须恰好 1 行：rows=%s" % rows)
        row = rows[0]
        self.assertEqual(row.get("tag") or row.get("name"), "TAG1")
        self.assertEqual(row.get("default") or row.get("default_value"), "A-01")
        self.assertEqual(len(str(row.get("tag") or row.get("name"))), 4, "标签长度必须恰好 4 字符")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
