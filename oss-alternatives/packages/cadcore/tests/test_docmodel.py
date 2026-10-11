"""tests.test_docmodel —— A5 `cadcore/docmodel.py` 独立单测（28 例，纯标准库 unittest）。

跑法（契约 D 段 G6）::

    cd oss-alternatives/packages/cadcore
    python3 -m unittest discover -s tests -t .        # 契约原命令
    python3 tests/test_docmodel.py -v                 # 直接跑本文件

覆盖矩阵（对应派单硬要求 ①~⑤）
------------------------------
① 逐字节确定性：`test_l1_is_byte_identical_on_repeated_serialization`（同文档两跑）
   + `test_l1_is_byte_identical_after_save_load_roundtrip`（`save_l1`/`load_l1` 往返后仍相同）。
② `migrate` 旧 schema 升 v1 不丢字段：v0 样例**自己造**，缺 `schema_version` 与显式 `=0` 两种形态
   都有；逐键比对 + 未知字段原样保留 + 往返后再序列化逐字节稳定。
③ `check_invariants` 四类违规各一条：浮点长度 / 下标引用 / 文本未存 key / uuid 非法（含"键整个
   缺席"这一实现缺口，见下）。
④ 内核缺席 fail-soft：`available()=False` 的桩 ⇒ `ok=False` 且 diagnostics 里有错误条目、
   **不抛异常**；另有 `available()=True` 但操作抛 `DependencyMissing` 的桩 ⇒ `code=dependency_missing`；
   外加"有特征没草图"这条缺席路径（集成席已补 fail-soft 守卫 ⇒ 直接断言，见缺口 4）。
⑤ nano 内核 + `tests/data/plate.json` 真算出 `shape_refs`（不是空表、不是 NA）。

已知缺口（记 blocker，**本席只写测试，不改实现**）
--------------------------------------------------
1. **④ 的措辞 vs 实现映射**：契约口径是"diagnostics 里有 DependencyMissing 条目"。实现里两条路径
   的 `code` 不同 —— `available()=False` ⇒ `kernel_unavailable`；`available()=True` 但几何操作抛
   `DependencyMissing` ⇒ `dependency_missing`（后者才是字面口径）。本文件两条路径都测，且不做
   "只认一个 code"的假断言：断言 `code ∈ {dependency_missing, kernel_unavailable}` 并显式钉住当次
   实际观测值（`_observed` 断言），实现若改口径会立刻报红。
2. **③ 的 "uuid 缺失"**：节点**整个缺 `uuid` 键**时 `check_invariants` 当前**一条都不报**
   （遍历只检查"存在的 `uuid` 键是否合法"）。所以本文件把"非法 uuid"当真红线测，
   另用 `test_check_invariants_does_not_yet_flag_absent_uuid_key` 把当前行为**钉住**并标注为缺口。
3. **`literal` 不传进 `args`**：`annotation.literal=True`（"这条注记就是人话"）时，`args.text`
   仍被判 `text_not_key`（`_check_text` 在每个映射节点各自取 `literal`，`args` 自己没有该键）。
   同样只钉桩不改实现，见 `test_check_invariants_flags_free_text_nested_in_args`。
4. **④ 的 fail-soft 有洞（真崩，不是措辞）**：文档**有特征但没有草图**时 `recompute` 原抛
   `IndexError`（旧 `docmodel.py:828` 的 `sketch = sketches[0]`，`sketches` 为空）。契约要求这种
   "缺席"进 diagnostics 并 `ok=False`。**已由集成席补守卫修掉**（无草图可回落 ⇒ `no_sketch`
   诊断 + `continue`，不再索引空表）⇒ 该条改为直接断言（`expected failures=0`），实现若回归立刻报红。

夹具自证
--------
`plate.json` 是**盘上真件**（断言文件存在 + `schema_version==1` + 不变量为空，非空即 `fail`）；
`available()=False` 与 `DependencyMissing` 两个内核桩都自带"真被调用过"的计数（`calls`），
内核桩若从未被问到，测试自己报红 —— 避免"尺子没装却判绿"（AGENTS.md E2.3/E2.5）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:  # 直跑本文件时的包路径兜底（discover 路径已带）
    sys.path.insert(0, _ROOT)

from cadcore.docmodel import (  # noqa: E402
    DIAGNOSTICS_KEY,
    SCHEMA_VERSION,
    check_invariants,
    document_from_l1,
    document_to_l1,
    load_l1,
    migrate,
    recompute,
    save_l1,
)
from cadcore.errors import DependencyMissing, DocumentError  # noqa: E402

FIXTURE = os.path.join(_HERE, "data", "plate.json")

DOC_UUID = "a11ce000-0000-4000-8000-000000000001"
SKETCH_UUID = "a11ce000-0000-4000-8000-000000000010"
ENTITY_UUID = "a11ce000-0000-4000-8000-000000000101"
FEATURE_UUID = "a11ce000-0000-4000-8000-000000000601"


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _meta(**overrides):
    meta = {"doc_uuid": DOC_UUID, "created_at": "2026-09-29T08:00:00Z", "app_version": "0.1.0",
            "title_key": "doc.untitled"}
    meta.update(overrides)
    return meta


def _entity(uuid=ENTITY_UUID, **geom_overrides):
    geom = {"x1": 0, "y1": 0, "x2": 1000000, "y2": 0}
    geom.update(geom_overrides)
    return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False, "geom": geom}


def _sketch(uuid=SKETCH_UUID, entities=None, constraints=None, dimensions=None):
    return {"uuid": uuid, "plane": "XY",
            "entities": [_entity()] if entities is None else entities,
            "constraints": [] if constraints is None else constraints,
            "dimensions": [] if dimensions is None else dimensions,
            "solved": {"dof": -1, "converged": False, "cache": True}}


def _extrude(uuid=FEATURE_UUID, inputs=None, **params_overrides):
    params = {"distance_nm": 1000000, "symmetric": False, "angle_deg": "0", "op": "new"}
    params.update(params_overrides)
    return {"uuid": uuid, "kind": "extrude",
            "inputs": [SKETCH_UUID] if inputs is None else inputs,
            "params": params, "suppressed": False}


def _payload(**overrides):
    """合规最小文档（一份草图 + 一条未抑制拉伸）；override 用于逐项做坏。"""
    payload = {"schema_version": SCHEMA_VERSION, "meta": _meta(), "unit": "mm",
               "standard_profile": "iso", "projection_angle": None,
               "sketches": [_sketch()], "features": [_extrude()], "annotations": [], "sheets": []}
    payload.update(overrides)
    return payload


def _v0_legacy_payload():
    """自造 v0 样例：**没有** `schema_version` 键（legacy 视作 0），且带未知字段。"""
    return {
        "meta": _meta(), "unit": "inch", "standard_profile": "ansi", "projection_angle": "third",
        "sketches": [dict(_sketch(), legacy_layer_note="kept from v0", x_vendor_ext={"a": 1})],
        "features": [_extrude()],
        "annotations": [{"uuid": "a11ce000-0000-4000-8000-000000000501",
                         "target": [FEATURE_UUID, "edge.outer.width"], "kind": "note",
                         "value_nm": None, "text_key": "doc.untitled", "literal": False, "args": {}}],
        "sheets": [],
        "legacy_camera": {"pan": [1, 2], "zoom": 3},
        "x_vendor_top": "exempt",
    }


class _UnavailableKernel:
    """桩：`available()=False`；`calls` 证明它真被问过（否则本席的尺子没装）。"""

    name = "stub-unavailable"
    version = "0.0.1"

    def __init__(self):
        self.calls = 0

    def available(self):
        self.calls += 1
        return False

    def capabilities(self):  # pragma: no cover - 不可用路径不该走到这里
        self.calls += 1
        return frozenset({"sketch", "extrude"})

    def extrude(self, profile, *, distance_nm, symmetric=False):  # pragma: no cover
        raise AssertionError("unavailable kernel must not be asked to do geometry")


class _DependencyMissingKernel:
    """桩：自称可用，但几何操作抛 `DependencyMissing`（能力缺席的真实形态）。"""

    name = "stub-capability-missing"
    version = "0.0.2"

    def __init__(self):
        self.calls = []

    def available(self):
        self.calls.append("available")
        return True

    def capabilities(self):
        self.calls.append("capabilities")
        return frozenset({"sketch", "extrude"})

    def extrude(self, profile, *, distance_nm, symmetric=False):
        self.calls.append("extrude")
        raise DependencyMissing("backend is unavailable for this capability",
                                reason="capability_missing", capability="extrude")


class _NeverCalledKernel:
    """桩：走到任何几何操作就是测试自身失败（证明诊断路径真没调内核）。"""

    name = "stub-never-called"
    version = "0.0.3"

    def available(self):
        return True

    def capabilities(self):
        return frozenset({"sketch", "extrude"})

    def extrude(self, profile, *, distance_nm, symmetric=False):  # pragma: no cover
        raise AssertionError("extrude must not be called on this path")


class L1DeterminismTests(unittest.TestCase):
    """① 逐字节确定性（唯一权威文本形态）。"""

    def test_l1_is_byte_identical_on_repeated_serialization(self):
        payload = _payload(sketches=[_sketch()], features=[_extrude()])
        doc = document_from_l1(json.dumps(payload))
        text_a = document_to_l1(doc)
        text_b = document_to_l1(doc)
        self.assertEqual(text_a, text_b)
        self.assertEqual(hashlib.sha256(text_a.encode("utf-8")).hexdigest(),
                         hashlib.sha256(text_b.encode("utf-8")).hexdigest())
        self.assertTrue(text_a.endswith("\n"))
        self.assertNotIn("\r", text_a)
        self.assertEqual(list(json.loads(text_a)), sorted(json.loads(text_a)))
        # 映射与 Document 两条入口必须给出同一份文本（否则"真源"有两个）
        self.assertEqual(text_a, document_to_l1(payload))

    def test_l1_is_byte_identical_after_save_load_roundtrip(self):
        doc = document_from_l1(json.dumps(_payload()))
        text_a = document_to_l1(doc)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "plate.l1.json")
            returned = save_l1(doc, path)
            self.assertEqual(returned, path)
            self.assertTrue(os.path.isfile(path))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), text_a.encode("utf-8"))
            reloaded = load_l1(path)
        text_b = document_to_l1(reloaded)
        text_c = document_to_l1(reloaded)
        self.assertEqual(text_a, text_b)
        self.assertEqual(text_b, text_c)
        self.assertEqual(_sha256(text_a), _sha256(text_b))
        self.assertEqual(document_to_l1(document_from_l1(text_a.encode("utf-8"))), text_a)

    def test_l1_normalizes_negative_zero_and_sorts_keys(self):
        payload = _payload(x_vendor={"offset": -0.0, "tilt_deg": -0.5})
        text = document_to_l1(payload)
        self.assertNotIn("-0.0", text)
        parsed = json.loads(text)
        self.assertEqual(math.copysign(1.0, parsed["x_vendor"]["offset"]), 1.0)
        self.assertEqual(parsed["x_vendor"]["tilt_deg"], -0.5)
        self.assertEqual(text, document_to_l1(document_from_l1(text)))

    def test_l1_rejects_non_finite_float(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(DocumentError) as ctx:
                document_to_l1(_payload(x_vendor={"v": bad}))
            self.assertEqual(ctx.exception.reason, "non_finite_number")

    def test_l1_rejects_non_string_key_and_unordered_value(self):
        with self.assertRaises(DocumentError) as ctx:
            document_to_l1({"meta": _meta(), 1: "int key"})
        self.assertEqual(ctx.exception.reason, "non_string_key")
        with self.assertRaises(DocumentError) as ctx:
            document_to_l1(_payload(x_vendor={"v": {1, 2, 3}}))
        self.assertEqual(ctx.exception.reason, "unordered_value")


class MigrationTests(unittest.TestCase):
    """② 旧 schema → v1：单调升版、禁跳版、不丢字段。"""

    def test_migrate_v0_without_schema_version_reaches_v1_without_field_loss(self):
        legacy = _v0_legacy_payload()
        original = copy.deepcopy(legacy)
        migrated = migrate(legacy)
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 1)
        for key, value in original.items():  # 逐个旧字段比对：一个都不许丢
            self.assertIn(key, migrated)
            self.assertEqual(migrated[key], value, "field lost or changed: %s" % key)
        codes = {item["code"] for item in migrated[DIAGNOSTICS_KEY]}
        self.assertIn("legacy_version_stamped", codes)
        stamps = [item for item in migrated[DIAGNOSTICS_KEY]
                  if item["code"] == "legacy_version_stamped"]
        self.assertEqual(stamps[0]["from"], 0)
        unknown = [item["path"] for item in migrated[DIAGNOSTICS_KEY]
                   if item["code"] == "unknown_field_preserved"]
        self.assertIn("$.legacy_camera", unknown)
        self.assertIn("$.sketches[0].legacy_layer_note", unknown)
        self.assertFalse([path for path in unknown if "x_vendor" in path],  # x_ 前缀豁免
                         "vendor extension fields must not be reported: %s" % unknown)
        # 迁移结果必须还能被 Document 读回，且未知字段/诊断都活着
        doc = document_from_l1(json.dumps(migrated))
        self.assertEqual(doc.schema_version, SCHEMA_VERSION)
        self.assertEqual(doc.extra.get("legacy_camera"), {"pan": [1, 2], "zoom": 3})
        self.assertEqual(doc.unit, "inch")
        self.assertEqual(doc.standard_profile, "ansi")
        self.assertEqual(doc.projection_angle, "third")
        self.assertEqual(doc.sketches[0]["legacy_layer_note"], "kept from v0")

    def test_migrate_explicit_zero_matches_missing_version(self):
        legacy = _v0_legacy_payload()
        explicit = dict(copy.deepcopy(legacy), schema_version=0)
        self.assertEqual(document_to_l1(migrate(explicit)), document_to_l1(migrate(legacy)))

    def test_migrate_v0_survives_l1_roundtrip_and_stays_stable(self):
        migrated = migrate(_v0_legacy_payload())
        text_a = document_to_l1(document_from_l1(json.dumps(migrated)))
        text_b = document_to_l1(document_from_l1(text_a))
        self.assertEqual(text_a, text_b)
        doc = document_from_l1(text_a)
        self.assertEqual(doc.extra.get("legacy_camera"), {"pan": [1, 2], "zoom": 3})
        self.assertEqual(doc.sketches[0]["x_vendor_ext"], {"a": 1})

    def test_migrate_rejects_future_downgrade_and_too_new_target(self):
        with self.assertRaises(DocumentError) as ctx:
            migrate({"schema_version": SCHEMA_VERSION + 1, "meta": _meta()})
        self.assertEqual(ctx.exception.reason, "schema_too_new")
        with self.assertRaises(DocumentError) as ctx:
            migrate({"meta": _meta()}, target=SCHEMA_VERSION + 1)
        self.assertEqual(ctx.exception.reason, "target_too_new")
        with self.assertRaises(DocumentError) as ctx:
            migrate({"schema_version": SCHEMA_VERSION, "meta": _meta()}, target=0)
        self.assertEqual(ctx.exception.reason, "downgrade_unsupported")

    def test_migrate_rejects_version_skip(self):
        import cadcore.docmodel as docmodel

        saved = docmodel._MIGRATIONS[0]

        def _skipping(payload, _diagnostics):
            out = dict(payload)
            out["schema_version"] = 2  # 一次跳两版
            return out

        docmodel._MIGRATIONS[0] = _skipping
        try:
            with self.assertRaises(DocumentError) as ctx:
                migrate({"meta": _meta()}, target=1)
            self.assertEqual(ctx.exception.reason, "bad_migration_step")
        finally:
            docmodel._MIGRATIONS[0] = saved
        self.assertIs(docmodel._MIGRATIONS[0], saved)

    def test_migrate_reports_missing_step_instead_of_guessing(self):
        import cadcore.docmodel as docmodel

        saved = docmodel._MIGRATIONS.pop(0)
        try:
            with self.assertRaises(DocumentError) as ctx:
                migrate({"meta": _meta()}, target=1)
            self.assertEqual(ctx.exception.reason, "no_migration_step")
        finally:
            docmodel._MIGRATIONS[0] = saved

    def test_migrate_fills_missing_scaffold_with_diagnostics(self):
        migrated = migrate({"meta": _meta()})
        paths = {item["path"] for item in migrated[DIAGNOSTICS_KEY] if item["code"] == "field_filled"}
        for expected in ("unit", "standard_profile", "projection_angle", "sketches", "features",
                         "annotations", "sheets"):
            self.assertIn(expected, paths)
        doc = document_from_l1(json.dumps(migrated))
        self.assertEqual(doc.unit, "mm")
        self.assertEqual(doc.sketches, [])
        self.assertTrue(doc.diagnostics)

    def test_document_from_l1_refuses_to_invent_doc_uuid(self):
        payload = _payload()
        payload["meta"] = {"title_key": "doc.untitled"}
        with self.assertRaises(DocumentError) as ctx:
            document_from_l1(json.dumps(payload))
        self.assertEqual(ctx.exception.reason, "missing_doc_uuid")


class InvariantTests(unittest.TestCase):
    """③ 不变量体检：四类违规各报一条 + 阴性对照。"""

    def test_plate_fixture_is_clean_and_really_on_disk(self):
        self.assertTrue(os.path.isfile(FIXTURE), "fixture missing: %s" % FIXTURE)
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            raw = handle.read()
        payload = json.loads(raw)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        doc = document_from_l1(raw)
        self.assertEqual(check_invariants(doc), [])
        self.assertEqual(check_invariants(payload), [])
        self.assertGreaterEqual(len(doc.sketches[0]["entities"]), 4)

    def test_check_invariants_flags_float_lengths(self):
        payload = _payload(sketches=[_sketch(entities=[_entity(x1=0.5)])])
        problems = check_invariants(payload)
        self.assertTrue([p for p in problems if p.startswith("geom_length_not_integer:$.sketches[0]"
                                                             ".entities[0].geom.x1")], problems)
        payload = _payload(features=[_extrude(distance_nm=1.5)])
        self.assertTrue(any(p.startswith("nm_not_integer:$.features[0].params.distance_nm")
                            for p in check_invariants(payload)))
        payload = _payload(features=[_extrude(distance_nm=None)])
        self.assertTrue(any(p.startswith("nm_is_null:$.features[0].params.distance_nm")
                            for p in check_invariants(payload)))
        # 阴性对照：角度是浮点但**允许**（*_deg 不是长度），不得出现在违规清单里
        payload = _payload(features=[_extrude(angle_deg=30.5)])
        self.assertEqual(check_invariants(payload), [])

    def test_check_invariants_flags_index_references(self):
        constraint = {"uuid": "a11ce000-0000-4000-8000-000000000401", "kind": "horizontal",
                      "refs": [[ENTITY_UUID, "0"]], "value_nm": None}
        payload = _payload(sketches=[_sketch(constraints=[constraint])])
        self.assertTrue(any(p.startswith("ref_index_used:$.sketches[0].constraints[0].refs[0]")
                            for p in check_invariants(payload)))
        constraint = dict(constraint, refs=[[ENTITY_UUID, "[1]"]])
        payload = _payload(sketches=[_sketch(constraints=[constraint])])
        self.assertTrue(any(p.startswith("ref_index_used:") for p in check_invariants(payload)))
        constraint = dict(constraint, refs=[[ENTITY_UUID, "edge.outer"]])
        self.assertEqual(check_invariants(_payload(sketches=[_sketch(constraints=[constraint])])), [])
        # 引用必须是 (uuid, sub_shape_name) 二元组；下标型 inputs 同样报红
        payload = _payload(sketches=[_sketch(constraints=[dict(constraint, refs=[ENTITY_UUID])])])
        self.assertTrue(any(p.startswith("ref_not_pair:") for p in check_invariants(payload)))
        payload = _payload(features=[_extrude(inputs=["0"])])
        self.assertTrue(any(p.startswith("uuid_list_bad_ref:$.features[0].inputs[0]")
                            for p in check_invariants(payload)))

    def test_check_invariants_flags_free_text_without_key(self):
        annotation = {"uuid": "a11ce000-0000-4000-8000-000000000502",
                      "target": [FEATURE_UUID, "edge.outer.width"], "kind": "note",
                      "value_nm": None, "text_key": "Width is 120 mm", "literal": False, "args": {}}
        problems = check_invariants(_payload(annotations=[annotation]))
        self.assertTrue(any(p.startswith("text_not_key:$.annotations[0].text_key")
                            for p in problems), problems)
        sheet = {"uuid": "a11ce000-0000-4000-8000-000000000701", "standard_profile": "iso",
                 "size": "A3", "scale": "1:1", "views": [], "dimensions": [],
                 "title_block": {"title": "Plate drawing"}}
        problems = check_invariants(_payload(sheets=[sheet]))
        self.assertTrue(any(p.startswith("text_not_key:$.sheets[0].title_block.title")
                            for p in problems), problems)

    def test_check_invariants_accepts_literal_free_text(self):
        annotation = {"uuid": "a11ce000-0000-4000-8000-000000000503",
                      "target": [FEATURE_UUID, "edge.outer.width"], "kind": "note",
                      "value_nm": None, "text_key": None, "literal": True,
                      "literal_text": "as machined", "args": {}}
        payload = _payload(annotations=[annotation])
        self.assertEqual(check_invariants(payload), [])
        # 阴性对照：同一份内容把 literal 翻成 False ⇒ 同一把尺子必须报红（不是恒绿）
        annotation = dict(annotation, literal=False)
        problems = check_invariants(_payload(annotations=[annotation]))
        self.assertTrue(any(p.startswith("text_not_key:$.annotations[0].literal_text")
                            for p in problems), problems)

    def test_check_invariants_flags_free_text_nested_in_args(self):
        """缺口钉桩（blocker）：`literal` 不传进 `args` 子节点，父节点 literal=True 也照样报红。

        观测：`_check_text` 在每个映射节点上各自取 `literal`，`args` 自己没有这个键 ⇒ 落 False；
        于是 `annotation.literal=True`（"这条注记就是人话"）时，`args.text` 仍被判 `text_not_key`。
        本席不得改实现 ⇒ 本条把当前行为钉住，实现改口径会立刻报红。
        """
        annotation = {"uuid": "a11ce000-0000-4000-8000-000000000504",
                      "target": [FEATURE_UUID, "edge.outer.width"], "kind": "note",
                      "value_nm": None, "text_key": None, "literal": True,
                      "args": {"text": "as machined"}}
        problems = check_invariants(_payload(annotations=[annotation]))
        self.assertTrue(any(p.startswith("text_not_key:$.annotations[0].args.text")
                            for p in problems), problems)

    def test_check_invariants_flags_missing_or_invalid_uuid(self):
        for bad in (None, "not-a-uuid", 123):
            payload = _payload(sketches=[_sketch(entities=[_entity(uuid=bad)])])
            problems = check_invariants(payload)
            self.assertTrue(any(p.startswith("uuid_bad:$.sketches[0].entities[0].uuid")
                                for p in problems), (bad, problems))
        payload = _payload(features=[_extrude(uuid="")])
        self.assertTrue(any(p.startswith("uuid_bad:$.features[0].uuid")
                            for p in check_invariants(payload)))

    def test_check_invariants_does_not_yet_flag_absent_uuid_key(self):
        """缺口钉桩（blocker，不是认可）：节点**整个缺** `uuid` 键时当前不报违规。

        契约 §C 要求"引用一律 (uuid, sub_shape_name)"、"uuid 缺失"属违规；实现只检查"存在的
        `uuid` 键是否合法"，所以缺席静默。本席不得改实现 ⇒ 用本条把当前行为钉住：哪天实现补上，
        这条会报红，届时把断言改成 `uuid_bad`/`uuid_missing` 即可。
        """
        entity = _entity()
        del entity["uuid"]
        payload = _payload(sketches=[_sketch(entities=[entity])])
        problems = check_invariants(payload)
        self.assertEqual([p for p in problems if p.startswith("uuid_bad")], [], problems)
        self.assertEqual(problems, [], "behavior changed: an absent uuid key is now reported")

    def test_check_invariants_flags_duplicate_uuid_and_unknown_unit(self):
        payload = _payload(sketches=[_sketch(entities=[_entity(), _entity()])])
        self.assertTrue(any(p.startswith("uuid_duplicate:sketches[0].entities[1]")
                            for p in check_invariants(payload)),
                        check_invariants(payload))
        problems = check_invariants(_payload(unit="furlong"))
        self.assertTrue(any(p.startswith("unit_unknown:") for p in problems), problems)
        problems = check_invariants(_payload(unit=""))
        self.assertTrue(any(p.startswith("unit_missing:") for p in problems), problems)


class RecomputeTests(unittest.TestCase):
    """④ 内核缺席 fail-soft + ⑤ nano 真算 shape_refs。"""

    def test_recompute_with_unavailable_kernel_is_fail_soft(self):
        kernel = _UnavailableKernel()
        payload = _payload()
        before = document_to_l1(payload)
        result = recompute(payload, kernel)  # 不许抛
        self.assertGreater(kernel.calls, 0, "the stub was never asked: ruler not installed")
        self.assertIs(result.ok, False)
        self.assertEqual(result.shape_refs, [])
        self.assertTrue(result.errors())
        codes = {item["code"] for item in result.diagnostics}
        self.assertTrue(codes and codes <= {"dependency_missing", "kernel_unavailable"}, codes)
        self.assertIn("kernel_unavailable", codes)  # 当次实现的实际口径，见模块 docstring 缺口 1
        self.assertEqual(result.kernel, "stub-unavailable")
        self.assertEqual(result.kernel_version, "0.0.1")
        report = result.as_dict()
        self.assertIs(report["ok"], False)
        self.assertGreaterEqual(report["error_count"], 1)
        self.assertEqual(report["shape_count"], 0)
        self.assertEqual(document_to_l1(payload), before)  # 纯函数：入参逐字节未变

    def test_recompute_captures_dependency_missing_as_diagnostic(self):
        kernel = _DependencyMissingKernel()
        result = recompute(_payload(), kernel)
        self.assertIn("extrude", kernel.calls, "the stub extrude was never called")
        self.assertIs(result.ok, False)
        self.assertIn("dependency_missing", {item["code"] for item in result.diagnostics})
        entry = [item for item in result.diagnostics if item["code"] == "dependency_missing"][0]
        self.assertEqual(entry["severity"], "error")
        self.assertEqual(entry["reason"], "capability_missing")
        self.assertEqual(entry["capability"], "extrude")
        self.assertEqual(entry["kernel"], "stub-capability-missing")
        self.assertEqual(entry["feature_uuid"], FEATURE_UUID)
        self.assertEqual(result.shape_refs, [])

    def test_recompute_nano_kernel_plate_fixture_produces_shape_refs(self):
        self.assertTrue(os.path.isfile(FIXTURE), "fixture missing: %s" % FIXTURE)
        try:
            from cadcore.kernel.base import get_kernel

            kernel = get_kernel("nano")
        except (DependencyMissing, ImportError) as exc:  # NA 与 PASS 不同形
            self.skipTest("NA: nano kernel unavailable: %s: %s" % (type(exc).__name__, exc))
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        result = recompute(payload, kernel)
        self.assertTrue(result.ok, "diagnostics=%s" % result.diagnostics)
        self.assertEqual(len(result.shape_refs), len(payload["features"]))
        self.assertEqual(result.kernel, "nano")
        ref = result.shape_refs[0]
        self.assertEqual(ref["feature_uuid"], payload["features"][0]["uuid"])
        self.assertEqual(ref["sketch_uuid"], payload["sketches"][0]["uuid"])
        self.assertEqual(ref["sub_shape_name"], "extrude")
        self.assertGreater(ref["edge_count"], 0)
        self.assertFalse([item for item in result.diagnostics if item["severity"] == "error"],
                         result.diagnostics)
        self.assertEqual(result.as_dict()["shape_count"], len(result.shape_refs))

    def test_recompute_is_pure_and_deterministic(self):
        with open(FIXTURE, "r", encoding="utf-8") as handle:
            raw = handle.read()
        doc = document_from_l1(raw)
        before = document_to_l1(doc)
        first = recompute(doc, _DependencyMissingKernel())
        second = recompute(doc, _DependencyMissingKernel())
        self.assertEqual(document_to_l1(doc), before)  # 纯函数：入参逐字节未变
        self.assertEqual(first.shape_refs, [])
        self.assertEqual(document_to_l1(first.as_dict()), document_to_l1(second.as_dict()))
        self.assertEqual(first.kernel, second.kernel)

        missing = recompute(doc, None)
        self.assertIs(missing.ok, False)
        self.assertIn("kernel_missing", {item["code"] for item in missing.diagnostics})
        self.assertEqual(document_to_l1(doc), before)

    def test_recompute_reports_no_sketch_suppressed_and_unknown_kinds(self):
        no_feature = recompute(_payload(features=[]), _NeverCalledKernel())
        self.assertIs(no_feature.ok, False)
        self.assertIn("no_solid_feature", {item["code"] for item in no_feature.diagnostics})

        suppressed = _extrude()
        suppressed["suppressed"] = True
        result = recompute(_payload(features=[suppressed]), _NeverCalledKernel())
        self.assertIs(result.ok, False)
        self.assertIn("no_solid_feature", {item["code"] for item in result.diagnostics})

        unknown_kind = _extrude()
        unknown_kind["kind"] = "pocket"
        result = recompute(_payload(features=[unknown_kind]), _NeverCalledKernel())
        self.assertIn("feature_kind_unknown", {item["code"] for item in result.diagnostics})

        boolean = _extrude()
        boolean["kind"] = "boolean"
        result = recompute(_payload(features=[boolean]), _NeverCalledKernel())
        self.assertIn("boolean_needs_shape_inputs", {item["code"] for item in result.diagnostics})

    def test_recompute_feature_without_sketch_is_fail_soft_not_indexerror(self):
        """契约要求 fail-soft：文档有特征但**没有草图** ⇒ `ok=False` + `no_sketch` 诊断，不抛异常。

        回归守护（原 blocker，集成席 2026-09-29 修）：旧实现 `docmodel.py:828` 的
        `sketch = sketches[0]` 在 `sketches == []` 时抛 `IndexError: list index out of range`。
        现改为"无草图可回落 ⇒ 补 `no_sketch` 诊断 + `continue`"，`ok=False` 由错误级诊断推出。
        """
        result = recompute(_payload(sketches=[], features=[_extrude()]), _NeverCalledKernel())
        self.assertIs(result.ok, False)
        self.assertIn("no_sketch", {item["code"] for item in result.diagnostics})


if __name__ == "__main__":
    unittest.main()
