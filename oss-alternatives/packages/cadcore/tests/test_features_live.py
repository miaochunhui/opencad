#!/usr/bin/env python3
"""tests.test_features_live —— 活操作测量席（live_ops）：**逐片操作真跑**的七态测量。

本席只回答一件事：对 ``cadcore/ops/features/*_w*.py`` 里的**每一个操作**，喂**合法参数**
（真 L1 文档 + 真 uuid + 真几何），现场实测它属于哪一态（**七态互斥、判定行逐档报数**）：

* ``live``    —— 返回非空 ``list[str]`` **且 L1 载荷逐字节真变**（尺子读 ``document_to_l1`` 文本）；
* ``query``   —— **声明式只读**的操作真返回了**非空数据**，且**三面全静**（L1 文本 / doc 级指纹 /
  文件系统）。专给"只读查询型"片（``osnap_candidates`` / ``xref_path_audit`` / ``measure_geom``
  之类）留的正解：它们**按定义就不改文档**，返回的是查询结果，不是写入凭据；
* ``write``   —— **有副作用证据**（文件系统新增/修改/删除 **或** doc 桶指纹变化）**且 L1 逐字节未变
  且未声明只读**。专给"导出/落盘型"操作（``plot`` 落 PDF/SVG、导出器写文件之类）留的正解：
  它们**真干活**却不在 L1 里留痕 ⇒ 从前被冤枉成 ``phantom``、没有绿档。**可见档、不判红**
  （判定行 ``write=N``，逐条证据见 ``WRITE_SIDE_EFFECT``）；自证跑法
  ``python3 tests/test_features_live.py --selftest-write``（两具各出一行判定）；
* ``noop``    —— 返回空列表，或声明只读却返回空数据（合法"无事可做"）；
* ``reject``   —— 抛 :class:`cadcore.errors.CadError`（fail-closed，**不算 live**）；
* ``broken``   —— 抛非 ``CadError`` 的异常（TypeError / NameError / AttributeError / …），或返回值连
  uuid 列表都不是且**没有**只读声明（``SliceContractError`` ⇒ 契约违规），**必须让该片的 ``subTest`` 变红**；
* ``phantom``  —— **既无副作用也无 L1 变更**：无只读声明 + 返回非空 list + L1 逐字节没变且三面
  （fs / doc 指纹）也无证据（假活）；或 **声明只读却偷改文档/偷写盘**（声明与行为不符）。两者都计入
  判定行的 ``broken``（保守口径），DETAIL 行另给 ``phantom=N``。

``query`` 的**唯一**入口是"**声明**"，``write`` 的**唯一**入口是"**副作用证据**" —— 都不兴"返回非空
就算过"（那样真 ``phantom`` 也会逃掉）。**声明只读 ∧ 有副作用 ⇒ 仍 ``phantom``/MISLABEL（判红）**：
声明不是免死金牌，也**不许**被洗成 ``write``。

**只读声明的识别口径（判定行按这个口径复核，唯二约定）**：

1. ``OPERATIONS_META``：片模块级 ``OPERATIONS_META = {"<op>": {"readonly": True}}``，**按 op 名取值**
   （也认同一字典里的 ``read_only`` / ``is_readonly`` 为真值，或 ``mode="readonly"`` / ``"query"``）；
2. **docstring 约定**：op 的 **docstring 首行**含 ``READONLY``（大小写不敏感；**只认首行**，
   正文里偶然提到 ``READONLY`` 不算声明）。

命中哪条写进 ``query_by_decl=``（形如 ``OPERATIONS_META.readonly:N`` / ``docstring.READONLY:N``）供复核。
另有**第三依据（非声明，只是返回类型回执，仅作兜底）**：op 的**返回类型注解**声明的是**数据**而不是
uuid 列表（``dict[...]`` / ``int`` / ``float`` / ``bool`` / ``str`` / ``tuple[...]`` /
``list[int|float|dict|tuple]`` …）。``Any``、裸 ``list``、``list[Any]``、``list[str]``、``None``
**一律不算依据** —— 没有依据就不给 query。

反放宽四闸（``test_ruler_sees_red`` + ``--selftest-write`` 逐条自证）：①无声明 + 非空 list + L1 未变 +
三面无证据 ⇒ ``phantom``；②**声明只读却改了 L1 ⇒ ``phantom``（声明与行为不符；照样计入 ``broken``
判红，声明不是免死金牌，防"用声明逃逸"）**；③无声明且返回值不是 uuid 列表 ⇒ ``broken``；
④**声明只读却写盘 ⇒ 仍 ``phantom`` + 点名 ``MISLABEL_READONLY``（判红；不许洗成 ``write``）**。
声明只读但返回空数据 ⇒ ``noop``；有副作用 + L1 未变 + **未**声明只读 ⇒ ``write``（可见档）。

判定行（**判定 → 退出码三态**：``FAIL``⇒``rc=1``、``NA``（未采）⇒``rc=2``、``PASS``⇒``rc=0``；
整体 ``rc`` = 「两组 + unittest」里**最严重**的那档；**明细行先出、判定行落在最后一行**，
``tail -1`` 必见判定行）::

    # 缺省：只扫主集合 ``features/*_w*.py``
    FEATURES_LIVE verdict=PASS|FAIL|NA live=N query=N write=N noop=N reject=N broken=N total=N rejects_by_reason=… non_slice_count=N non_slice_files=[…] main_verdict=… unittest=PASS|FAIL rc=R
    # 加 --include-quarantine：两组**分列**（main_* / quarantined_*），绝不合并成一个数
    FEATURES_LIVE verdict=PASS|FAIL|NA scope=separate_groups groups=2 main_verdict=… main_slices=… main_ops=…
    main_live=N main_query=N main_write=N main_query_by_decl=… main_noop=N main_reject=N main_broken=N main_phantom=N
    main_total=N main_rejects_by_reason=… main_non_slice_count=N main_non_slice_files=[…] quarantined_verdict=… quarantined_live=N quarantined_query=N …
    quarantined_rejects_by_reason=… quarantined_non_slice_count=N quarantined_non_slice_files=[…] main_rc=R quarantined_rc=R unittest=PASS|FAIL rc=R

``--include-quarantine``（跑法：``python3 tests/test_features_live.py --include-quarantine``）额外扫
``cadcore/ops/features/_quarantine/*_w*.py`` —— 真正的只读查询 op（``osnap_candidates`` /
``xref_path_audit`` / ``plot`` / ``measure_geom`` 等）都在隔离区，主集合里一片都没有，所以缺省档
``query=0`` 是"没扫到"而不是"没有 query 型 op"。**两组各出自己的 live/query/write/noop/reject/broken/
total 与 verdict，口径不许相加**（隔离区本就装着已知红片，它的 FAIL 不污染主集合的判定）；末行
``verdict=`` 只表示"整次运行的 rc 对齐"（两组任一 FAIL 即 FAIL），**不是**两组合并计数。两组的片按目录
互斥扫描，同 stem 出现在两边时会打 ``STEM_OVERLAP`` 行显式披露（那里是两个不同的文件，不许当同一个片记账）。

``verdict=PASS``（**每组各自比**）的判据（四条全中）：``broken==0``（``phantom``/``unmeasured`` 保守
计入）、``phantom==0``、``live>0``、``unmeasured==0``。``query``/``write`` 都是**合法正解**：不扣分，
也不冒充 ``live``。每组三态：``FAIL``（测得红）/ ``NA``（**未采**：该组一个 op 都没进账 ⇒ ``rc=2``，
"没算"与"没失败"不许同形）/ ``PASS``。``rc`` 由**判定**算出、不再只取 unittest 退出码：隔离区红片以前
``verdict=FAIL`` 而 ``rc=0``（假绿），现在 ``FAIL``⇒1；整体 ``rc`` 取最严重档（``FAIL`` > ``NA`` > ``PASS``）。

夹具来源（本轮改成**优先真 L1 夹具**）
--------------------------------------
``tests/l1_fixture.py`` 已落盘且自证 ``L1_FIXTURE verdict=PASS``。先读它的**公开入口名**再接线：

* ``build_fixture() -> L1Fixture``（主入口；``L1Fixture.doc`` 是真 ``docmodel.Document``，
  另带 ``plate_sketch_uuid`` / ``hole_sketch_uuid`` / ``feature_uuid`` / ``sheet_uuid`` /
  ``view_uuid`` / ``component_uuids`` / ``mate_uuid`` / ``layer_uuids`` / ``block_uuid`` /
  ``insert_uuid`` / ``xref_uuid`` / ``hole_circle_uuid`` / ``plate_line_uuids`` 的**现算 uuid 账**）；
* ``build_document() -> Document``（兜底入口，只要文档）。

探针形状（按序升级，全部是合法输入；reject 才升级，live/noop/phantom 就地停）：

1. ``l1_doc``     —— 真 ``Document``（主形状，严格判：非 ``CadError`` 一律 broken）；
2. ``l1_payload`` —— 同一文档的 L1 载荷 dict（``document_to_l1`` 反解）；
3. ``sketch`` / ``sketch_alt`` —— 夹具的两张草图节点（对应首个位置参的 sk 一侧）。

参数合成按 op 自己的签名/注解走，uuid 一律从真夹具的 uuid 账取（不是现编的假串），几何取夹具真纳米值。

reject 归类与"缺上下文 vs 缺能力"判据
-------------------------------------
每个 reject 的 ``CadError.reason`` 归一成**类**（``sketch_not_found`` / ``unsupported_target`` /
``invalid_argument`` / ``l1_document_required`` / ``missing_context`` / ``entity_not_found`` / …）。
判定行给 ``rejects_by_reason``（每个最终 reject 的 op 记一次，按它**首个观测到的 reason 类**）。

逐类判据（``REJECT_CLASS`` 行）用的是**真文档形状**上的 reject 账：

* ``doc_reject`` = 在 ``l1_doc``（真 Document）上抛该类的 op 数；
* ``flipped_to_live`` = 其中**补上别的合法上下文（L1 dict / 草图节点 + 真 uuid）后转 live** 的 op 数
  ⇒ 这类属**夹具/形状缺上下文**，不是 op 缺能力（判词 ``fixture_context_gap(recoverable)``）；
* ``stuck`` = 在夹具全部上下文下仍拒的 op 数 ⇒ 判词
  ``op_capability_gap(no_live_path_in_fixture_context)``，只说"本夹具上下文里没有成功路径"，
  **不**外推成"该 op 是空壳"。

跑法::

    cd oss-alternatives/packages/cadcore
    python3 tests/test_features_live.py          # 真跑 + 判定行 + REJECT_CLASS + 逐片摘要
    python3 -m pytest tests/test_features_live.py -q
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import types
import typing
import unittest
import uuid
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve()
_PKG_ROOT = _HERE.parent.parent  # packages/cadcore
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore import docmodel  # noqa: E402
from cadcore.errors import CadError  # noqa: E402
from cadcore.ops import features as FEATURES  # noqa: E402

_L1_FIXTURE = _HERE.parent / "l1_fixture.py"
_FIXTURE_SOURCE = "on_the_fly:l1_fixture.py_absent"
_FIXTURE_UUIDS: dict[str, str] = {}
_PROBE_TMP = tempfile.mkdtemp(prefix="cadcore_live_")
_MAX_ATTEMPTS_PER_OP = 8
_SECONDARY_ATTEMPTS = 4
_FATAL_TOKENS = ("NameError", "UnboundLocalError", "TypeError", "AttributeError",
                 "SliceContractError")

# --------------------------------------------------------------------------- 真 L1 夹具

_NS = uuid.UUID("8f14e45f-ceea-467a-9c1c-1a2b3c4d5e6f")


def _uid(name: str) -> str:
    """兜底 uuid（只有真夹具缺席时才会用到；uuid5，确定性、可复跑）。"""
    return str(uuid.uuid5(_NS, "cadcore.live." + name))


LINE_UUID = _uid("line")
CIRCLE_UUID = _uid("circle")
SKETCH_UUID = _uid("sketch")
FEATURE_UUID = _uid("feature")
BODY_UUID = _uid("body")
SHEET_UUID = _uid("sheet")
PART_UUID = _uid("part")

LINE_GEOM = {"x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}          # 10 mm（兜底）
CIRCLE_GEOM = {"cx": 5_000_000, "cy": 0, "r": 1_000_000}          # r=1 mm @(5 mm,0)（兜底）


def _line_entity() -> dict:
    return {"uuid": LINE_UUID, "kind": "line", "layer": "0", "construction": False,
            "geom": dict(LINE_GEOM)}


def _circle_entity() -> dict:
    return {"uuid": CIRCLE_UUID, "kind": "circle", "layer": "0", "construction": False,
            "geom": dict(CIRCLE_GEOM)}


def _load_l1_fixture():
    """真夹具的公开入口（先读名）：``build_fixture()`` 优先，``build_document()`` 兜底。

    返回 ``{"doc": Document, "fixture": L1Fixture|None, "entry": str}``；不可用返回 ``None``
    并把原因写进 ``_FIXTURE_SOURCE``（不静默退回）。
    """
    global _FIXTURE_SOURCE, _FIXTURE_UUIDS
    if not _L1_FIXTURE.is_file():
        _FIXTURE_SOURCE = "on_the_fly:l1_fixture.py_absent"
        return None
    module = None
    err = ""
    try:  # 走**包导入**：模块进 sys.modules，dataclass/typing 的运行期语义与真跑一致
        import tests.l1_fixture as module  # type: ignore[no-redef]
    except BaseException as exc:  # noqa: BLE001 - 夹具不可用要留痕，不许静默
        err = f"{type(exc).__name__}: {exc}"[:120]
        module = None
    if module is None:  # 兜底：按文件路径加载，并登记进 sys.modules（spec 直载的常见坑）
        try:
            spec = importlib.util.spec_from_file_location("tests_l1_fixture", _L1_FIXTURE)
            module = importlib.util.module_from_spec(spec)
            sys.modules["tests_l1_fixture"] = module
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except BaseException as exc2:  # noqa: BLE001
            err = f"{err} | spec:{type(exc2).__name__}: {exc2}"[:200]
            module = None
    if module is None:
        _FIXTURE_SOURCE = f"on_the_fly:l1_fixture.py_unusable:{err}"
        return None

    ctx = None
    builder = getattr(module, "build_fixture", None)
    if callable(builder):
        try:
            built = builder()
        except BaseException as exc:  # noqa: BLE001
            _FIXTURE_SOURCE = f"on_the_fly:l1_fixture.build_fixture_raised:{type(exc).__name__}"
            return None
        doc = getattr(built, "doc", None)
        if isinstance(doc, docmodel.Document):
            ctx = {"doc": doc, "fixture": built, "entry": "l1_fixture.build_fixture"}
    if ctx is None:
        builder = getattr(module, "build_document", None)
        if callable(builder):
            try:
                doc = builder()
            except BaseException as exc:  # noqa: BLE001
                _FIXTURE_SOURCE = \
                    f"on_the_fly:l1_fixture.build_document_raised:{type(exc).__name__}"
                return None
            if isinstance(doc, docmodel.Document):
                ctx = {"doc": doc, "fixture": None, "entry": "l1_fixture.build_document"}
    if ctx is None:
        _FIXTURE_SOURCE = "on_the_fly:l1_fixture.py_no_document_entry"
        return None

    _FIXTURE_SOURCE = ctx["entry"]
    fixture = ctx["fixture"]
    table = {
        "sketch": "plate_sketch_uuid", "sketch_alt": "hole_sketch_uuid",
        "feature": "feature_uuid", "sheet": "sheet_uuid", "view": "view_uuid",
        "view_alt": "view_alt_uuid", "dimension": "dimension_uuid",
        "mate": "mate_uuid", "layer": "layer_uuids", "block": "block_uuid",
        "insert": "insert_uuid", "xref": "xref_uuid", "component": "component_uuids",
        "circle": "hole_circle_uuid", "line": "plate_line_uuids",
    }
    # 上下文扩容账：**只登记真夹具真造出来的值**（缺席的键留空 ⇒ 取值器取不到就只能回落旧口径，
    # 不许把空值当"合法值"发出去）。键名与 `l1_fixture.L1Fixture` 的字段一一对应。
    if fixture is not None:
        globals()["_FIXTURE_CONTEXT"] = {
            "view": getattr(fixture, "view_uuid", "") or "",
            "view_alt": getattr(fixture, "view_alt_uuid", "") or "",
            "dimension": getattr(fixture, "dimension_uuid", "") or "",
            "grid_spacing_nm": int(getattr(fixture, "grid_spacing_nm", 0) or 0),
            "snap_kinds": list(getattr(fixture, "snap_kinds", ()) or ()),
            "snap_tolerance_nm": int(getattr(fixture, "snap_tolerance_nm", 0) or 0),
            "alignment_name": getattr(fixture, "alignment_name", "") or "",
            "labelled_mate": getattr(fixture, "labelled_mate_uuid", "") or "",
            "protected_layer": getattr(fixture, "protected_layer", "0") or "0",
            "layer_name": next((name for name in getattr(fixture, "layer_names", ())
                                if name and name != "0"), ""),
            # 复数账：layer_merge 的 sources/into 要**两个互异真层名**（只给单数会把两者喂成
            # 同一个层 ⇒ invalid_argument，那是尺子取值域缺口，不是被测件的实现缺口）。
            "layer_names": [name for name in getattr(fixture, "layer_names", ())
                            if name and name != "0"],
            "layer_properties": dict(getattr(fixture, "entity_properties", {}) or {}),
            "context_report": dict(getattr(fixture, "context_report", {}) or {}),
        }
    for key, attr in table.items():
        value = getattr(fixture, attr, None) if fixture is not None else None
        if isinstance(value, (tuple, list)) and value:
            value = value[0]
        if isinstance(value, str) and value:
            _FIXTURE_UUIDS[key] = value
    sketches = [item for item in getattr(ctx["doc"], "sketches", []) if isinstance(item, dict)]
    for key, idx in (("sketch", 0), ("sketch_alt", 1)):
        if key not in _FIXTURE_UUIDS and len(sketches) > idx:
            value = sketches[idx].get("uuid")
            if isinstance(value, str) and value:
                _FIXTURE_UUIDS[key] = value
    if "line" not in _FIXTURE_UUIDS and sketches:
        for entity in sketches[0].get("entities", []) or []:
            if isinstance(entity, dict) and isinstance(entity.get("uuid"), str):
                _FIXTURE_UUIDS["line"] = entity["uuid"]
                break
    return ctx


_FIXTURE_CTX = _load_l1_fixture()


def _fixture_payload() -> dict:
    """兜底最小 L1 载荷（**只在真夹具缺席时**用；来源由 ``_FIXTURE_SOURCE`` 如实交代）。"""
    payload = docmodel.new_document(unit="mm", standard_profile="iso").to_payload()
    sketch = {"uuid": SKETCH_UUID, "name": "Sketch1", "layer": "0",
              "entities": [_line_entity(), _circle_entity()]}
    feature = {"uuid": FEATURE_UUID, "kind": "extrude", "name": "Extrude1",
               "params": {"sketch": SKETCH_UUID, "sketch_uuid": SKETCH_UUID,
                          "depth_nm": 10_000_000, "depth": 10.0}}
    payload.update({
        "sketches": [sketch],
        "features": [feature],
        "annotations": [],
        "sheets": [{"uuid": SHEET_UUID, "name": "Sheet1", "size": "A3"}],
    })
    payload["body"] = {"uuid": BODY_UUID, "name": "Body1", "features": [FEATURE_UUID]}
    payload["bom"] = [{"uuid": PART_UUID, "part": "P1", "qty": 1}]
    payload["templates"] = {}
    return payload


def _doc_shapes() -> list[tuple[str, object]]:
    """探针形状（按序升级）：真 Document → L1 载荷 dict → 草图节点（两张）。"""
    if _FIXTURE_CTX is not None:
        doc = _FIXTURE_CTX["doc"]
        shapes: list[tuple[str, object]] = [("l1_doc", doc)]
        try:
            payload = json.loads(docmodel.document_to_l1(doc))
        except BaseException:  # noqa: BLE001 - 序列化不了就退回 to_payload，形状名仍如实
            payload = doc.to_payload()
        shapes.append(("l1_payload", payload))
        sketches = [item for item in getattr(doc, "sketches", []) if isinstance(item, dict)]
        for idx, tag in ((0, "sketch"), (1, "sketch_alt")):
            if len(sketches) > idx:
                shapes.append((tag, sketches[idx]))
        return shapes
    full = _fixture_payload()
    shapes = [("l1_payload", full)]
    if full.get("sketches"):
        shapes.append(("sketch", full["sketches"][0]))
    return shapes


_SHAPES_CACHE: list | None = None


def _shapes() -> list:
    global _SHAPES_CACHE
    if _SHAPES_CACHE is None:
        _SHAPES_CACHE = _doc_shapes()
    return _SHAPES_CACHE


# --------------------------------------------------------------------------- 参数合成

_EMPTY = inspect.Parameter.empty
_UUID_LIST_NAMES = {"uuids", "refs", "entities", "items", "feature_uuids", "entity_uuids",
                    "selected", "out_uuids"}
_UUID_SCALAR_TOKENS = ("uuid", "xref")
_PATH_NAMES = {"out", "path", "file", "dir", "dest", "filename", "out_dir", "base_dir", "root",
               "directory", "dest_path", "out_path", "target_path"}
_POINT_PREFIX = ("p1", "p2", "at", "base", "point", "target", "start", "end", "center", "origin",
                 "ref_a", "ref_b", "corner", "from", "to")
_STR_VALUES = {
    "layer": "0", "name": "probe", "mode": "auto", "fmt": "pdf", "format": "pdf",
    "kind": "line", "type": "auto", "text": "probe", "label": "probe", "lang": "en",
    "locale": "en-US", "unit": "mm", "units": "mm", "style": "solid", "axis": "x",
    "direction": "x", "op": "probe", "reason": "probe", "token": "probe", "value": "probe",
    "family": "draw_ops", "snap": "endpoint", "snap_kind": "endpoint", "size": "A3",
    # 枚举动作口（布局管理 / 折线编辑）：合法值域里挑一个**必成功**的动作（`manage_layout(action="new")`
    # 与夹具无关，实测真建一张布局并改 L1）；旧口径给 "probe"/1000 ⇒ 被测件只能回 unknown_target。
    "action": "new",
    # i18n key（多引线/文字/尺寸类 op 的内容块）：合法形状 = 点分 ASCII 标识符（族模块 _KEY_RE）。
    "text_key": "probe.text",
}
_INT_TOKENS = ("_nm", "count", "index", "steps", "position", "rows", "cols", "segments",
               "radius", "diameter", "width", "height", "spacing", "tolerance", "limit",
               "size", "thickness", "depth")
_BOOL_TOKENS = ("enabled", "visible", "close", "closed", "force", "allow", "strict",
                "construction", "suppress", "suppressed", "overwrite", "recursive", "reverse",
                "filled", "dashed", "on", "off", "flag")
_SCALAR_TYPES = (int, float, str, bool, list, dict, tuple, set)


def _annotation_kind(annotation):
    if annotation is _EMPTY or annotation is None:
        return None
    if isinstance(annotation, str):
        low = annotation.lower()
        for name, kind in (("bool", bool), ("int", int), ("float", float), ("str", str),
                           ("dict", dict), ("list", list), ("tuple", tuple), ("set", set)):
            if name in low:
                return kind
        return None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is getattr(types, "UnionType", None):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _annotation_kind(args[0]) if len(args) == 1 else None
    if origin in (list, tuple, dict, set):
        return origin
    return annotation if annotation in _SCALAR_TYPES else None


def _uuid_for(name: str) -> str:
    """uuid 一律从**真夹具的 uuid 账**取（没有才退回兜底 uuid5 串）。"""
    low = name.lower()

    def pick(*keys: str) -> str:
        for key in keys:
            value = _FIXTURE_UUIDS.get(key)
            if value:
                return value
        return ""

    if "sketch" in low:
        return pick("sketch") or SKETCH_UUID
    if "feature" in low:
        return pick("feature") or FEATURE_UUID
    if "sheet" in low:
        return pick("sheet") or SHEET_UUID
    if "view" in low:
        return pick("view") or pick("sheet") or SHEET_UUID
    if "body" in low:  # L1 无 body 字段：基体 = 未抑制的 extrude 特征
        return pick("feature") or FEATURE_UUID
    if "circle" in low:
        return pick("circle") or CIRCLE_UUID
    if "layer" in low:
        return pick("layer") or pick("line") or LINE_UUID
    if "block" in low:
        return pick("block") or pick("line") or LINE_UUID
    if "xref" in low:
        return pick("xref") or pick("line") or LINE_UUID
    if "mate" in low:
        return pick("mate") or pick("component") or LINE_UUID
    if "part" in low or "bom" in low or "component" in low:
        return pick("component") or PART_UUID
    if "line" in low or "entity" in low:
        return pick("line") or LINE_UUID
    return pick("feature", "line", "sketch") or LINE_UUID


def _is_pathish(low: str) -> bool:
    return low in _PATH_NAMES or low.endswith(("_dir", "_path", "_file", "_root"))


def _is_pointish(low: str) -> bool:
    """点位参数判定（**只扩点值域**，不改判据）。

    旧口径 `low.startswith(tok + "_")` 认不出带序号的 `origin1_`（E5）⇒ 它落进 `_INT_TOKENS`
    拿标量 1000，op 判 `bad_position`。这里先剥掉尾部单位后缀、再剥掉序号，然后比前缀。
    """
    body = low
    for suffix in ("_nm", "_mm", "_pt", "_deg"):
        if body.endswith(suffix):
            body = body[: -len(suffix)]
            break
    head = body.rstrip("0123456789")
    return any(low == tok or low.startswith(tok + "_") or body == tok or head == tok
               for tok in _POINT_PREFIX)


#: 量级族 token：`*_nm` 给 mm 级、`*_mm` 给个位/十位（1 mm = 1e6 nm 同源换算）。
#: 旧口径一律落 `_INT_TOKENS` 的 1000 nm（=1 µm），与 `*_mm` 的 1000 mm 自相矛盾（E3）。
#: 线宽/公差另有真实档次（0.25 mm 级），不走 mm 级几何那档。
_DIM_NM_TOKENS = ("size", "nominal", "diameter", "radius", "width", "height", "length",
                  "thickness", "depth", "spacing", "distance", "gap", "offset", "chamfer",
                  "fillet", "value", "height")
_NM_PER_MM = 1_000_000


def _dict_value(low: str, idx: int):
    return [{"uuid": LINE_UUID, "kind": "line", "layer": "0", "geom": dict(LINE_GEOM)},
            {"x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}][idx % 2]


def _kw_value(name: str, annotation, idx: int, hint: str = ""):
    """按参数名/注解给一个"合法"的夹具值（uuid 与几何一律取自真夹具）。

    本轮扩容只做一件事：**把真夹具已经构造出来的上下文按参数名取出来**（栅格间距 / 已标注对齐 /
    真实层名 / 带特性层行 / 源视图 / 尺寸注记 / 量级族长度）。取不到就**原样回落**到旧口径 ——
    缺席的上下文不许冒充"合法值"，这正是下面 `RcVerdictSelfTest.test_fixture_context_does_not_relax_reject`
    能当阴性对照的前提。
    """
    low = name.lower()
    line_uuid = _uuid_for("line")
    ctx = globals().get("_FIXTURE_CONTEXT") or {}
    # ── 取值域补档（2026-10-01·输出族收敛）：**只补真夹具已有/真档位**，取不到原样回落 ────────
    # ① 图纸 uuid 列表（publish / 发布类）：夹具里真有一张 sheet（`_FIXTURE_UUIDS["sheet"]`），
    #    旧口径按 `_uuids` 后缀给**实体** uuid ⇒ 被测件只能判 `bad_reference`（尺子造出来的非法调用）。
    if low in {"sheet_uuids", "sheets", "sheet_list", "layouts", "layout_uuids"}:
        sheet_uid = ctx.get("sheet") or _FIXTURE_UUIDS.get("sheet") or ""
        if sheet_uid:
            return [[sheet_uid], [sheet_uid]][idx % 2]
    # ② 线宽档（LWEIGHT / 打印线宽）：0.25/0.35/0.5 mm 是标准档；旧口径走 `_mm` 给 10 mm
    #    ⇒ 被测件如实回 `nonstandard_lineweight`（合法值域缺失，不是实现缺陷）。
    if "lineweight" in low and low.endswith("_mm"):
        return [0.25, 0.35, 0.5][idx % 3]
    if "lineweight" in low and low.endswith("_nm"):
        return [250_000, 350_000, 500_000][idx % 3]
    # ③ `lweight` 是**替代**给值口（`lweight="ByLayer"` 与数值线宽互斥）⇒ 缺省不喂：
    #    喂了会把"数值线宽"这条合法调用判成 `bad_argument`。
    if low == "lweight":
        return None
    # ④ 打印开关（Plot / No Plot）：`plottable`/`no_plot` 都是**严格 bool**，旧口径给 1000
    #    ⇒ `bad_plot_flag`（同 ②：缺合法值，不是缺能力）。
    if low in {"plottable", "no_plot"}:
        return [True, False, True][idx % 3]
    # ⑤ 枚举动作口（`action`）：`_BOOL_TOKENS` 里有个 token `on`，而 `"on" in "action"` 为真
    #    （**子串**匹配，不是整词）⇒ `action` 被喂成 True/False，被测件只能回 `unknown_target`。
    #    这里按**整名**优先给合法动作（`manage_layout(action="new")` 实测真建布局并改 L1）。
    #    注：同一个子串缺陷还挂在别的参数名上（如 `position` 含 `on`）—— 那是既有尺子口径，
    #    本席只补本族用到的这一个名，不顺手改全局分类（改判据要同笔改判据自证，另立一刀）。
    if low == "action" and isinstance(_STR_VALUES.get("action"), str):
        return _STR_VALUES["action"]
    # 文档驱动提示（`_param_semantic_hints`）：只在 op 自己写明参数语义时才改值域。
    if hint == "annotation_uuid" and ctx.get("dimension"):
        return ctx["dimension"]
    if hint == "annotation_ref" and ctx.get("dimension"):
        first = [_uuid_for("line"), "start"]
        second = [_uuid_for("circle"), "center"]
        return [first, [first, second]][idx % 2]
    if hint == "polyline_groups":
        # 多引线：两条分支**必须汇聚到同一锚点**（末点相同），否则 bad_leader_set。
        return [[[(0, 0), (10_000_000, 10_000_000)],
                 [(0, 10_000_000), (10_000_000, 10_000_000)]],
                [[(0, 0), (5_000_000, 5_000_000), (10_000_000, 10_000_000)]]][idx % 2]
    if low in {"from_view", "source_view", "source_view_uuid", "base_view"} and ctx.get("view_alt"):
        return [ctx["view_alt"], ctx.get("view", "")][idx % 2]
    if low in {"view", "view_uuid", "target_view"} and ctx.get("view_alt"):
        return [ctx.get("view", ""), ctx["view_alt"]][idx % 2]
    if low in {"direction", "dir", "orientation", "orient", "view_direction", "projection"}:
        return ["front", "top"][idx % 2]
    if low in {"alignment", "align", "alignment_name"} and ctx.get("alignment_name"):
        return ctx["alignment_name"]
    if low in {"mates", "mate_uuids", "mate_refs"} and ctx.get("labelled_mate"):
        return [[ctx["labelled_mate"]], [ctx["labelled_mate"], line_uuid]][idx % 2]
    if low in {"grid_spacing_nm", "step_nm"} and ctx.get("grid_spacing_nm"):
        return [ctx["grid_spacing_nm"], 2 * int(ctx["grid_spacing_nm"])][idx % 2]
    if low in {"snap_kinds", "kinds", "modes", "osnap_modes"} and ctx.get("snap_kinds"):
        return [list(ctx["snap_kinds"]), list(ctx["snap_kinds"][:1])][idx % 2]
    if low in {"properties", "props", "layer_props", "layer_properties"} and ctx.get("layer_properties"):
        return dict(ctx["layer_properties"])
    if low in {"layer", "layer_name", "layer_ref", "target_layer"} and ctx.get("layer_name"):
        return [ctx["layer_name"], ctx.get("protected_layer", "0")][idx % 2]
    # 图层合并（LAYMRG）：sources 要层名序列、into 要层名，两者必须互异 ⇒ 按位次错开取真层名。
    if low in {"sources", "source_layers", "src_layers", "merge_sources"} and ctx.get("layer_names"):
        picks = list(ctx["layer_names"])
        return [picks[idx % len(picks)]]
    if low in {"into", "merge_into", "destination_layer", "dest_layer"} and ctx.get("layer_names"):
        picks = list(ctx["layer_names"])
        return picks[idx % len(picks)]
    if low in {"dimension", "dimension_uuid", "annotation", "annotation_uuid", "target_annotation"} \
            and ctx.get("dimension"):
        return ctx["dimension"]
    if "points" in low or low in {"pts", "polyline", "vertices"}:
        pool = [[(0, 0), (10_000_000, 0)],
                [(0, 0), (10_000_000, 0), (10_000_000, 10_000_000)]]
        return copy.deepcopy(pool[idx % len(pool)])
    if low in _UUID_LIST_NAMES or low.endswith("_uuids"):
        return [[line_uuid], [line_uuid, _uuid_for("circle")]][idx % 2]
    if _is_pathish(low):
        return [_PROBE_TMP, os.path.join(_PROBE_TMP, "probe.out")][idx % 2]
    if _is_pointish(low):
        return [(0, 0), (10_000_000, 0), (5_000_000, 0)][idx % 3]
    if low.endswith("_nm") and ("lineweight" in low or "tolerance" in low):
        return [250_000, 500_000, 100_000][idx % 3]   # 真实线宽/公差档（0.1~0.5 mm）
    if low.endswith("_mm"):
        return [10, 20, 5][idx % 3]
    if low.endswith("_nm") and any(tok in low for tok in _DIM_NM_TOKENS):
        return [10 * _NM_PER_MM, 20 * _NM_PER_MM, 5 * _NM_PER_MM][idx % 3]
    kind = _annotation_kind(annotation)
    if kind is int:
        return [10_000_000, 1, 0][idx % 3] if "depth" in low else [1000, 1, 0][idx % 3]
    if kind is float:
        return [45.0, 0.0, 10.0][idx % 3]
    if kind is str:
        if any(tok in low for tok in _UUID_SCALAR_TOKENS):
            return _uuid_for(low)
        return _STR_VALUES.get(low, ["probe", "0"][idx % 2])
    if kind is bool:
        return [True, False][idx % 2]
    if kind in (list, set):
        return [[], [line_uuid]][idx % 2]
    if kind is tuple:
        return [(0, 0), (10_000_000, 0)][idx % 2]
    if kind is dict:
        return _dict_value(low, idx)
    if any(tok in low for tok in _UUID_SCALAR_TOKENS) or low.endswith("_id"):
        return _uuid_for(low)
    if low.endswith("_deg") or "angle" in low or low in {"scale", "rotation"}:
        return [45.0, 0.0, 90.0][idx % 3]
    if any(tok in low for tok in _BOOL_TOKENS) or low.startswith(("is_", "has_")):
        return [True, False, True][idx % 3]
    if any(tok in low for tok in _INT_TOKENS):
        return [1000, 1, 0][idx % 3]
    if low in _STR_VALUES:
        return _STR_VALUES[low]
    return ["probe", "0", 1000][idx % 3]


#: 逐参数契约行：``* `name`：…``（只认**该参数自己那一行**，不跨行猜、不从正文里捡）。
_CONTRACT_PARAM_RE = re.compile(r"^\s*[*\-·]?\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*[:：=]")
#: 记录序列形状：``{k1, k2, k3?}``（**≥2 个键**才算"记录"，单键不算 —— 免得与既有标量口径抢参数名）。
_CONTRACT_RECORD_RE = re.compile(
    r"\{\s*([A-Za-z_][A-Za-z0-9_]*\??(?:\s*[,\s，]+\s*[A-Za-z_][A-Za-z0-9_]*\??)*)\s*\}")


def _contract_record_value(key: str, idx: int):
    """记录里单个字段的值：**字段名取自 op 契约行**，uuid 取自真夹具账，其余按公开量级口径给。

    只用公开面：不调用被测片、不调用隔离片、不读它们的私有常量；量级沿用本文件既有的
    ``_NM_PER_MM`` / ``_DIM_NM_TOKENS`` 口径（契约里 ``qty:int≥1`` 明写 ≥1 ⇒ 绝不给 0）。
    """
    low = key.lower()
    if any(tok in low for tok in _UUID_SCALAR_TOKENS) or low.endswith("_id"):
        return _uuid_for(low)
    if low.endswith("_nm"):
        if _is_pointish(low) or "at" in low or "position" in low:
            return (0, 0)
        if "tolerance" in low or "lineweight" in low:
            return 250_000
        return 10 * _NM_PER_MM
    if low.endswith("_mm"):
        return 10
    if low == "qty" or "count" in low:      # 契约：`qty:int≥1`
        return 1 + idx % 2
    if "mass" in low or any(tok in low for tok in _INT_TOKENS):
        return 1000
    if any(tok in low for tok in _BOOL_TOKENS) or low.startswith(("is_", "has_")):
        return [True, False][idx % 2]
    return ["probe", "probe-2"][idx % 2]     # 无更强依据的键：非空字符串（`part_key` 必须非空）


def _contract_variants(fn) -> list:
    """按**目标 op 自己的 docstring 契约**造值：只认"逐参数写明 `{键集}` 记录序列"那类参数。

    与既有取值器的关系：**不改任何判据**，只在契约明写形状时**另加**合法变体 —— 每条变体
    **只喂该参数本身**（其余保持签名缺省）。为什么不像 `_kw_value` 那样塞进按名猜值：那样会把
    `sheet`/`positions_nm` 之类无关参数的猜测值一并带进来，反而组成非法调用（实测把真拒因从
    `bom_source_missing` 换成 `bad_bom_row`/`sheet_not_found`，等于没解决）。
    """
    doc = getattr(fn, "__doc__", None) or ""
    if not doc:
        return []
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return []
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    out: list = []
    for line in doc.splitlines():
        head = _CONTRACT_PARAM_RE.match(line)
        if head is None:
            continue
        name = head.group(1)
        if name not in params and not accepts_kwargs:
            continue                      # 注不进签名 ⇒ 只会 TypeError，不许造
        spec = _CONTRACT_RECORD_RE.search(line)
        if spec is None:
            continue
        keys = [item.strip().rstrip("?") for item in re.split(r"[,\s，]+", spec.group(1))
                if item.strip()]
        if len(keys) < 2:                 # 单键 `{uuid}` 之类不算"记录序列"
            continue
        for rows in (1, 2):
            out.append({name: [{key: _contract_record_value(key, idx) for key in keys}
                               for idx in range(rows)]})
        break                             # 一次只改一类参数名：同一个 op 只认第一条契约行
    return out


#: 逐类扩容档位（**只选"多造哪些合法值变体"，不碰任何判据/门槛**）：A=契约形状（记录序列 /
#: 折线组 / uuid 组）、B=带 `alignment` 的配合记录、C=点位↔uuid 按目标 op 契约判类。
#: 每次只加一类便于逐类归因；某类实测不满足验收就地摘掉（改档位串，不删判据）。
_EXT_LEVEL = "B"


def _ext_on(tag: str) -> bool:
    return tag in _EXT_LEVEL


def _contract_lines(fn) -> dict:
    """参数名 → 该参数**自己那一行**契约原文（只认逐参数行，不跨行猜、不从正文里捡）。"""
    out: dict = {}
    for line in (getattr(fn, "__doc__", None) or "").splitlines():
        head = _CONTRACT_PARAM_RE.match(line)
        if head is not None:
            out.setdefault(head.group(1), line)
    return out


#: 行内参数语义：`` `param` 是/为/：/=/:= <描述> ``（读 op **自己** docstring 的契约原文）。
#: 为什么按文档而不是按参数名：`target` 在点位面是坐标、在图层/打印面是层名，按名一刀切成注记
#: uuid 会污染其它族；只有 op 自己写下"这是注解 uuid"时，注记 uuid 才是它的合法值域。
_INLINE_PARAM_RE = re.compile(r"`([a-z_][a-z0-9_]*)`\s*(?:是|为|：|:|=)\s*([^\n；;。]{0,60})")
_ANN_UUID_DESC_RE = re.compile(r"注解\s*uuid|annotation\s*uuid|注记\s*uuid", re.IGNORECASE)
_ANN_REF_DESC_RE = re.compile(r"二元组|two-tuple", re.IGNORECASE)
_ANN_UUID_DOC_RE = re.compile(r"注解\s*uuid|annotation\s*uuid")
_ANN_PAIR_DOC_RE = re.compile(r"二元组")


def _param_semantic_hints(fn) -> dict:
    """参数名 → 取值域提示（只认目标 op 自己的文档；认不出 ⇒ 空表，取值器照旧回落旧口径）。

    只做三件事：①逐参数行内语义（`` `target` 是注解 uuid ``）；②op 级口径（docstring 明写
    "注解 uuid"且签名有 `target`；明写"二元组"且有 `reference`）；③契约行明写折线组的参数
    （`` `leaders`：… 折线 … ``）⇒ 喂汇聚到同一锚点的折线组。缺夹具上下文（没有真注记 uuid）
    时前两类不提示 —— 缺席不许冒充合法值。
    """
    doc = getattr(fn, "__doc__", None) or ""
    try:
        params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return {}
    ctx = globals().get("_FIXTURE_CONTEXT") or {}
    out: dict = {}
    for line in doc.splitlines():
        head = _CONTRACT_PARAM_RE.match(line)
        if head is not None and head.group(1) in params and _CONTRACT_POLYLINE_RE.search(line):
            out.setdefault(head.group(1), "polyline_groups")
        for match in _INLINE_PARAM_RE.finditer(line):
            name, desc = match.group(1), match.group(2)
            if name not in params:
                continue
            if _ANN_UUID_DESC_RE.search(desc):
                out.setdefault(name, "annotation_uuid")
            if _ANN_REF_DESC_RE.search(desc):
                out[name] = "annotation_ref"
    if ctx.get("dimension"):
        if _ANN_UUID_DOC_RE.search(doc):
            for name in ("target", "target_uuid", "annotation", "annotation_uuid"):
                if name in params:
                    out.setdefault(name, "annotation_uuid")
        if _ANN_PAIR_DOC_RE.search(doc):
            for name in ("reference", "references", "refs", "target_ref"):
                if name in params:
                    out.setdefault(name, "annotation_ref")
    return out


#: A 类·非记录形状：折线组 / uuid 组（记录形状 `{k1, k2?}` 已由 `_contract_variants` 覆盖）。
_CONTRACT_POLYLINE_RE = re.compile(r"折线|多段线|polyline|\(\(?\s*x[_a-z]*\s*,", re.IGNORECASE)
_CONTRACT_IDLIST_RE = re.compile(r"(uuid|_id|id\b|帧|标识)[^\n]{0,14}?(列表|序列|数组|list\b)",
                                 re.IGNORECASE)


def _contract_shape_variants(fn) -> list:
    """A 类扩容：契约里**除第一条记录行以外**的形状（后续记录行 / 折线组 / uuid 组）。

    只用公开面：uuid 取真夹具账（`_uuid_for`），点坐标沿用本文件既有 mm 级点位池；不调被测片。
    每条变体**只喂该参数本身**（其余保持签名缺省），与 `_contract_variants` 同口径。
    """
    if not _ext_on("A"):
        return []
    doc = getattr(fn, "__doc__", None) or ""
    if not doc:
        return []
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return []
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    first_record_done = False
    out: list = []
    for line in doc.splitlines():
        head = _CONTRACT_PARAM_RE.match(line)
        if head is None:
            continue
        name = head.group(1)
        if name not in params and not accepts_kwargs:
            continue                      # 注不进签名 ⇒ 只会 TypeError，不许造
        spec = _CONTRACT_RECORD_RE.search(line)
        if spec is not None and not first_record_done:
            first_record_done = True      # 第一条记录行已由 `_contract_variants` 造过，不重复
            continue
        if spec is not None:              # 后续记录行（同 op 第二个记录参数）
            keys = [item.strip().rstrip("?") for item in re.split(r"[,\s，]+", spec.group(1))
                    if item.strip()]
            if len(keys) >= 2:
                out.append({name: [{key: _contract_record_value(key, idx) for key in keys}
                                   for idx in range(3)]})   # 契约是"行序列" ⇒ 至少 3 行
                continue
        if _CONTRACT_POLYLINE_RE.search(line):              # 引线/折线组：每条分支 ≥2 点
            out.append({name: [[(0, 0), (10_000_000, 0)],
                               [(0, 10_000_000), (10_000_000, 10_000_000)]]})
            continue
        if _CONTRACT_IDLIST_RE.search(line):                # 帧/标识列表
            out.append({name: [[_uuid_for(name)]]})
            out.append({name: [_uuid_for(name), _uuid_for("circle")]})
    return out[:6]


#: C 类·契约判类：契约行明写坐标/点 ⇒ 点位；明写 uuid/实体/注记 ⇒ uuid。只在这两类之间改判。
_CONTRACT_POINTISH_RE = re.compile(r"坐标|点位|点对|端点|圆心|折线|\(x|（x|x_nm|y_nm", re.IGNORECASE)
_CONTRACT_UUIDISH_RE = re.compile(r"uuid|标识|注记|图元|实体|引用|annotation|dimension|multileader"
                                  r"|frame|datum|component|mate|part", re.IGNORECASE)
_UUID_TEXT_RE = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}")
_POINT_POOL3 = ((0, 0), (10_000_000, 0), (5_000_000, 5_000_000))


def _mate_aware_value(name: str, idx: int, default, text: str, ctx: dict):
    """B 类扩容：`mate*` 参数一律喂**真夹具账里的配合 uuid**；契约明写记录/alignment 时喂配合记录。"""
    if not _ext_on("B") or "mate" not in name.lower():
        return default
    mate = ctx.get("labelled_mate") or _FIXTURE_UUIDS.get("mate") or ""
    if not mate:
        return default                    # 夹具没这条上下文 ⇒ 原样回落（不冒充合法值）
    if re.search(r"记录|record|alignment|对齐", text or "", re.IGNORECASE):
        return [{"uuid": mate, "alignment": ctx.get("alignment_name") or "aligned"}]
    low = name.lower()
    return [mate] if (low.endswith("s") or low.endswith(("_uuids", "_refs"))) else mate


def _kind_aware_value(name: str, idx: int, default, text: str):
    """C 类扩容：`target`/`position` 这类**既可能点位又可能 uuid** 的参数按**目标 op 契约**判类。"""
    if not _ext_on("C") or not text:
        return default
    if _CONTRACT_POINTISH_RE.search(text):
        if isinstance(default, tuple) and len(default) == 2 and all(
                isinstance(item, int) for item in default):
            return default                # 已是点位：不动
        return _POINT_POOL3[idx % len(_POINT_POOL3)]
    if _CONTRACT_UUIDISH_RE.search(text) and not (
            isinstance(default, str) and _UUID_TEXT_RE.search(default)):
        return _uuid_for(name)            # 契约要 uuid 而当前拿到点位/占位串 ⇒ 换成真夹具 uuid
    return default


#: **互斥**参数对：两个都给值 ⇒ 被测件只能如实拒（`bad_target` / `bad_argument`），
#: 那是**尺子自己造出来的非法调用**，不是实现缺陷。只喂**先出现的**那一支，另一支保持签名缺省
#: （只对"有缺省值"的那支生效，必填参数一支都不丢）；判据一条不改，只是不再自造矛盾入参。
_EXCLUSIVE_PAIRS: tuple[tuple[str, str], ...] = (
    ("layer", "object_uuid"),     # 目标二选一：图层 or 对象
    ("plottable", "no_plot"),     # 打印开关二选一
)


def _drop_exclusive_alternatives(combo: list) -> list:
    """丢掉互斥对里**后出现且带缺省值**的那一支（见 `_EXCLUSIVE_PAIRS`）。"""
    names = {param.name for param in combo}
    drop: set = set()
    for first, second in _EXCLUSIVE_PAIRS:
        if first in names and second in names:
            for param in combo:
                if param.name == second and param.default is not inspect.Parameter.empty:
                    drop.add(second)
    return [param for param in combo if param.name not in drop]


def _variants(fn):
    """按签名生成参数组合：必填 / 必填+首个 None 缺省 / 必填+全部 None 缺省，各 2 档取值。"""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    positional = [p for p in signature.parameters.values()
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if not positional:
        return None
    first = positional[0].name
    keyed = [p for p in signature.parameters.values()
             if p.name != first and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                               inspect.Parameter.KEYWORD_ONLY)]
    required = [p for p in keyed if p.default is inspect.Parameter.empty]
    optional = [p for p in keyed if p.default is None]
    # 特性类关键字（`color`/`linetype`/`lineweight_nm`/`plot`/...）**不许再零 kwarg 打空**：
    # 旧口径只收"缺省为 None"的可选参数（E1）⇒ `layer_props_set(...)` 全常量缺省时拿 0 个 kwarg，
    # 必抛 `no_properties` —— 那不是被测件的缺口，是尺子没给出合法特性值。只扩值域、不改判据。
    props = [p for p in keyed if p.default is not inspect.Parameter.empty
             and p.default is not None
             and p.name.lower() in {"color", "linetype", "lineweight_nm", "plot", "plotstyle",
                                    "description", "visible", "locked", "printable", "enabled"}]
    combos = [required]
    if optional:
        combos.extend([required + optional[:1], required + optional])
    if props:
        combos.append(required + props[:2])
    combos = [_drop_exclusive_alternatives(combo) for combo in combos]
    ctx = globals().get("_FIXTURE_CONTEXT") or {}
    contract_text = _contract_lines(fn)
    hints = _param_semantic_hints(fn)
    out, seen = [], set()
    for combo_idx, combo in enumerate(combos):
        names = tuple(p.name for p in combo)
        if names in seen:
            continue
        seen.add(names)
        for idx in (combo_idx % 2, (combo_idx + 1) % 2):
            # 同组参数按**位次**错开取值：旧口径把同一个 idx 喂给所有参数（E2）⇒ p1==p2==p3，
            # 点位/两点几何退化（`degenerate_geometry` / `invalid_point` / `bad_point`）。
            row = {}
            for pos, p in enumerate(combo):
                text = contract_text.get(p.name, "")
                value = _kw_value(p.name, p.annotation, idx + pos, hint=hints.get(p.name, ""))
                value = _kind_aware_value(p.name, idx + pos, value, text)   # C 类：契约判点位/uuid
                value = _mate_aware_value(p.name, idx + pos, value, text, ctx)   # B 类：配合记录
                row[p.name] = value
            out.append(row)
    contract = _contract_variants(fn)     # docstring 契约变体**排最前**：先试"按契约造的值"
    contract = contract + _contract_shape_variants(fn)      # A 类扩容：追加在已验证形状之后
    kw_variants = _kwargs_setter_variants(fn)
    if kw_variants:   # 纯 kwargs 型 setter：旧口径只有空 kwarg ⇒ 补**非幂等**变体（判据一字不改）
        return contract + kw_variants + [{}]
    return contract + (out or [{}])


#: kwargs 型 setter 的两轮取值档：**布尔取反档优先**（夹具 `enabled=True` ⇒ 先试 `False`）、
#: **量值换档优先**（与夹具现值不同的那档先试）、其它键两档。
_KWARG_ROUNDS = {"bool": (1, 0), "num": (1, 2), "other": (0, 1)}
_KWARG_RANK = {"bool": 0, "num": 1, "other": 2}


def _kwarg_kind(name: str) -> str:
    """按参数名归类（布尔开关 / 量值 / 其它）——只影响**取值与排序**，不影响任何判据。"""
    low = (name or "").lower()
    if any(tok in low for tok in _BOOL_TOKENS) or low.startswith(("is_", "has_")):
        return "bool"
    if low.endswith(("_mm", "_nm", "_deg")) or any(tok in low for tok in _INT_TOKENS):
        return "num"
    return "other"


def _kwarg_annotation(name: str):
    kind = _kwarg_kind(name)
    if kind == "bool":
        return bool
    if kind == "num":
        return int
    return None


def _kwargs_setter_variants(fn) -> list:
    """纯 kwargs 型 setter（``(sk_or_doc, **kwargs)``）的**非幂等**参数变体。

    旧口径对这类签名只生成空 kwarg；夹具里那条记录**已存在且值相同** ⇒ 调用可证为幂等 no-op
    ⇒ 尺子只能如实记 ``phantom``（探针构造能力不足，不是实现缺陷）。这里只补"**能构造的参数
    变体**"，判据一字不改：键名**一律取自本 op 自己模块**的 ``*KEY*`` 键域常量（规格自带 ⇒ 不瞎猜
    参数名；取不到就**不造**）；值按布尔取反/量值换档给两轮，**第一轮先每个键各一档** ——
    限额截断时也能覆盖到更多键。
    """
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return []
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return []
    named = [p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                             inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                             inspect.Parameter.KEYWORD_ONLY)]
    if len(named) != 1:      # 有具名关键字的仍走原 combos，不在这里重复生成
        return []
    globs = getattr(fn, "__globals__", None) or {}
    domain = set()
    for var, value in globs.items():
        if "KEY" not in var.upper():
            continue
        if isinstance(value, (frozenset, set, tuple, list)) and value \
                and all(isinstance(item, str) for item in value):
            domain.update(value)
    if not domain:
        return []
    keys = sorted(domain, key=lambda name: (_KWARG_RANK[_kwarg_kind(name)], name))
    rounds = [_KWARG_ROUNDS[_kwarg_kind(name)] for name in keys]
    variants = []
    for turn in (0, 1):
        for name, (idx_a, idx_b) in zip(keys, rounds):
            variants.append({name: _kw_value(name, _kwarg_annotation(name),
                                             idx_a if turn == 0 else idx_b)})
    return variants[:12]


# --------------------------------------------------------------------------- reject 归类

_REASON_RULES = (
    ("l1_document_required", ("l1_document", "l1_required", "document_required", "doc_required",
                              "not_a_document", "not_document", "expected_document")),
    ("sketch_not_found", ("sketch_not_found", "unknown_sketch", "missing_sketch", "no_sketch",
                          "sketch_missing", "invalid_sketch")),
    ("missing_context", ("no_base_solid", "no_mates", "no_sheet_collection", "no_layout",
                         "missing_context", "no_components", "requires_context", "no_layer",
                         "no_block", "no_xref", "empty_selection")),
    ("entity_not_found", ("entity_not_found", "unknown_entity", "no_entity", "missing_entity",
                          "not_found", "unknown_ref", "missing_ref", "no_such")),
    ("unsupported_target", ("unsupported", "not_supported", "unimplemented", "not_implemented",
                            "target_type", "unsupported_kind", "unsupported_op")),
    ("invalid_argument", ("invalid", "bad_", "illegal", "out_of_range", "negative", "degenerate",
                          "wrong_", "mismatch", "requires_")),
)


def _classify_reason(reason: str) -> str:
    low = (reason or "").strip().lower()
    if not low:
        return "unspecified"
    for name, tokens in _REASON_RULES:
        if any(token in low for token in tokens):
            return name
    return "other:" + low[:40]


# --------------------------------------------------------------------------- 单操作测量

def _canonical_text(value) -> str:
    """L1 载荷的确定性文本（"L1 真变"的尺子）：真 Document 走 ``document_to_l1``。"""
    if isinstance(value, docmodel.Document):
        try:
            return json.dumps(json.loads(docmodel.document_to_l1(value)), sort_keys=True,
                              ensure_ascii=False, default=repr)
        except BaseException:  # noqa: BLE001 - 序列化不了就退回 repr（不静默）
            return repr(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)


# --------------------------------------------------------------------------- 只读声明

_READONLY_TOKENS = ("readonly", "read-only", "read_only", "read only")
_DATA_SCALARS = {"int", "float", "bool", "str", "fraction", "decimal", "bytes", "bytearray"}
_SEQ_WRAPPERS = {"list", "tuple", "set", "frozenset", "sequence", "iterable", "collection",
                 "mutablelist", "mutablesequence", "abstractset", "deque"}
_MAP_WRAPPERS = {"dict", "mapping", "mutablemapping", "ordereddict", "defaultdict", "counter"}
#: 这些一律**不算声明**（没有判别力）：空 / Any / object / None / CadError 与"裸容器/Any 元素"。
_NOT_A_DECLARATION = {"", "any", "object", "none", "nonetype", "caderror"}


def _annotation_text(annotation) -> str:
    """注解文本（``from __future__ import annotations`` 下注解本身就是字符串）。"""
    if annotation is None or annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", None) or str(annotation)


def _strip_optional(text: str) -> str:
    """去掉 ``Optional[...]`` / ``X | None`` 外壳（"可为空"不影响只读声明）。"""
    low = text.replace(" ", "").replace("\t", "").replace("\n", "")
    for _ in range(3):
        changed = False
        if low.startswith("optional[") and low.endswith("]"):
            low, changed = low[len("optional["):-1], True
        elif low.endswith("|none"):
            low, changed = low[:-len("|none")], True
        elif "|" in low:
            parts = [item for item in low.split("|") if item not in ("none", "nonetype")]
            if len(parts) == 1:
                low, changed = parts[0], True
        if not changed:
            break
    return low


def _readonly_by_annotation(annotation) -> tuple[bool, str]:
    """依据③：返回类型注解声明的是**数据**（而不是 uuid 列表）⇒ 算只读声明。

    保守：``Any`` / 裸容器 / ``list[Any]`` / ``list[str]`` / ``None`` 都**不算**声明 —— 没有依据不给 query。
    """
    text = _annotation_text(annotation)
    if not text:
        return False, ""
    low = _strip_optional(text).lower()
    if low in _NOT_A_DECLARATION:
        return False, ""
    head = low.split("[", 1)[0]
    inner = low[len(head) + 1:-1] if low.startswith(head + "[") and low.endswith("]") else ""
    if head in _MAP_WRAPPERS:
        return True, f"rettype:{text}"
    if head in _SEQ_WRAPPERS:
        element = _strip_optional(inner.split(",", 1)[0]).lower()
        if element in _NOT_A_DECLARATION or element in ("str", "builtins.str"):
            return False, ""
        return True, f"rettype:{text}"
    if head in _DATA_SCALARS:
        return True, f"rettype:{text}"
    return False, ""


def _readonly_declaration(module, name: str, fn) -> tuple[bool, str]:
    """只读声明（三依据任一命中即算）：OPERATIONS_META → docstring 首行 ``READONLY`` → 返回类型是数据。

    只回答"**代码里声明了什么**"，不回答"这次调用做了什么" —— 声明与行为不符由 ``_run_ladder`` 判 phantom。
    """
    meta = getattr(module, "OPERATIONS_META", None) if module is not None else None
    entry = meta.get(name) if isinstance(meta, dict) else None
    if isinstance(entry, dict):
        for key in ("readonly", "read_only", "is_readonly"):
            if entry.get(key) is True:
                return True, f"OPERATIONS_META.{key}"
        if str(entry.get("mode", "")).strip().lower() in ("readonly", "read-only", "query"):
            return True, "OPERATIONS_META.mode"
    elif isinstance(entry, str) and entry.strip().lower() in ("readonly", "read-only", "query"):
        return True, "OPERATIONS_META.str"
    first = ""
    for line in (getattr(fn, "__doc__", None) or "").strip().splitlines():
        line = line.strip()
        if line:
            first = line.lower()
            break
    if any(token in first for token in _READONLY_TOKENS):
        return True, "docstring:READONLY"
    try:
        annotation = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return False, ""
    return _readonly_by_annotation(annotation)


def _is_data_result(result) -> bool:
    """声明只读的 op 是否**真返回了非空数据**（None / 空容器 / 空串 ⇒ 不算；标量算数据）。"""
    if result is None:
        return False
    if isinstance(result, (str, bytes, bytearray, list, tuple, set, frozenset, dict)):
        return len(result) > 0
    return True


def _result_size(result) -> object:
    try:
        return len(result)
    except TypeError:
        return "NA"


# --------------------------------------------------------------- 副作用证据（只读声明的对证）
#: 只读声明只回答"代码里怎么写的"；**有没有副作用**必须现场取证 —— op 在**只读沙箱**里真调一次
#: （``cwd`` 指到空沙箱 + 从 op 自己源码里取到的"路径型关键字"指向沙箱），比对调用前后的
#: **文件系统快照**（路径 → ``(size, mtime_ns)``）。**有新增/修改/删除文件 ⇒ 判 ``phantom``**
#: （``declared_readonly_but_fs_changed``），**不认 readonly 声明** —— 声明与行为不符本身就是缺陷。
#: 为什么必须单独一条：L1 只看得见"文档变没变"，写文件 / 写全局 / 起子进程都不在 L1 里。
import re  # noqa: E402 - 仅用于从 op 自己的源码里取"路径型关键字"名字（不靠猜）

_SIDE_EFFECT_ROOT = Path(tempfile.mkdtemp(prefix="cadcore_live_sandbox_"))
_SIDE_EFFECT_SUFFIXES = (".pdf", ".svg", ".out")
_SIDE_EFFECT_MAX_NAMES = 3
_SIDE_EFFECT_SNAPSHOT_CAP = 5000
_PATH_KWARG_NAMES = {"path", "file", "filename", "out", "output", "dest", "target", "save"}


def _fs_snapshot(root) -> dict:
    """文件系统快照 ``{绝对路径: (size, mtime_ns)}`` —— 只 walk 沙箱/探针目录，不碰工作树。"""
    snap: dict = {}
    for dirpath, _dirnames, filenames in os.walk(str(root)):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            snap[path] = (stat.st_size, stat.st_mtime_ns)
            if len(snap) >= _SIDE_EFFECT_SNAPSHOT_CAP:
                return snap
    return snap


def _fs_delta(label: str, root, before: dict, after: dict) -> list:
    """两次快照的差集（NEW / TOUCHED / REMOVED；路径相对 root 打印 ⇒ 可复跑、不含随机前缀）。"""
    out: list = []
    for path, meta in sorted(after.items()):
        tag = "NEW" if path not in before else ("TOUCHED" if before[path] != meta else "")
        if tag:
            out.append(f"{tag}@{label}:{os.path.relpath(path, str(root))}")
    for path in sorted(before):
        if path not in after:
            out.append(f"REMOVED@{label}:{os.path.relpath(path, str(root))}")
    return out


def _pathish_kwarg_names(fn) -> list:
    """路径型关键字名：从 op **所在整个模块**的源码字面量里取，不靠猜。

    只看 op 函数体会漏 —— ``plot`` 自己只有三行，真写入（``open(out_path, "wb")``）在私有 helper
    ``_run`` 里；所以把模块源码一并扫。名字只用来生成沙箱路径，不作为判据本身。
    """
    chunks = [inspect.getsource(fn)]
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    if module is not None:
        try:
            chunks.append(inspect.getsource(module))
        except (OSError, TypeError):
            pass
    names: list = []
    for chunk in chunks:
        for token in re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", chunk):
            low = token.lower()
            if low in _PATH_KWARG_NAMES or low.endswith(("_path", "_file", "_dir")):
                if low not in names:
                    names.append(low)
    # 只注**签名真能接受**的名字：op 不吃 ``**kwargs`` 时注错名字只会 TypeError（被吞掉），
    # 那不是"没有副作用"的证据 —— 实测：本尺子第一版正是栽在这里（变异体反证判红）。
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        names = [name for name in names if name in params]
    # 注入优先级：``*_path/*_file/*_dir`` 复合名先注（``plot`` 的 ``out_path`` 必须进前几名），
    # 泛化的 ``path``/``file``/``out`` 靠后 —— 注错名字只会让 op 拒绝（不产生假红），成本很低。
    names.sort(key=lambda item: 0 if item.endswith(("_path", "_file", "_dir")) else 1)
    return names[:_SIDE_EFFECT_MAX_NAMES]


def _side_effect_probe(fn, doc) -> list:
    """只读沙箱里真调一次，返回副作用证据（空列表 = 干净）。

    证据只认"真落了文件"这一条硬证据：op 拒绝（``CadError``）或抛别的异常 ⇒ 不动判定
    （异常本身由梯子记账，这里不重复扣分）。快照面两处：沙箱 ``cwd``（捕获相对路径写入）
    + ``_PROBE_TMP``（路径型夹具值的落点）。
    """
    os.makedirs(str(_SIDE_EFFECT_ROOT), exist_ok=True)
    os.makedirs(_PROBE_TMP, exist_ok=True)
    roots = (("cwd", _SIDE_EFFECT_ROOT), ("probe_tmp", Path(_PROBE_TMP)))
    names = _pathish_kwarg_names(fn)
    cwd_before = os.getcwd()
    hits: list = []
    try:
        for suffix in _SIDE_EFFECT_SUFFIXES:
            kwargs = {name: os.path.join(str(_SIDE_EFFECT_ROOT), "side_effect_probe" + suffix)
                      for name in names}
            before = {label: _fs_snapshot(root) for label, root in roots}
            os.chdir(str(_SIDE_EFFECT_ROOT))
            try:
                fn(copy.deepcopy(doc), **copy.deepcopy(kwargs))
            except BaseException:  # noqa: BLE001 - 拒绝/异常不是副作用证据
                pass
            finally:
                os.chdir(cwd_before)
            delta: list = []
            for label, root in roots:
                delta.extend(_fs_delta(label, root, before[label], _fs_snapshot(root)))
            if delta:
                hits.extend(delta)
                break
    finally:
        os.chdir(cwd_before)  # 沙箱是临时的，进程 cwd 必须还原（不许留在沙箱里跑后续）
    return hits


import dataclasses  # noqa: E402 - 第三证据面（doc 级指纹）的依赖，紧跟其使用者
import hashlib  # noqa: E402

#: 第三证据面 = **doc 级指纹**。L1 投影（``document_to_l1``）只看得见"被投影的桶"，而声明 readonly
#: 的 op 可能往**文档对象自己**的桶/属性上写（``document_to_l1`` 不投影它们）⇒ 只看 L1 会把这种
#: 写入洗成 query。指纹按**整份文档对象**算：dataclass 字段 + 实例 ``__dict__`` 里的额外属性
#: （L1 不投影的那些）+ 所有 dict/list 递归，键排序（dict 按 key、set 按规范化文本）、
#: 成环/超深/超预算显式标记，再取 sha256 ⇒ **变了就是变了**，与投影无关。
_DOC_FP_MAX_DEPTH = 16
_DOC_FP_MAX_NODES = 50000
_DOC_FP_OWNED_ROOT = "cadcore"


def _doc_fp_safe_repr(value) -> str:
    """``repr`` 兜底：摘要器不许因为某个怪对象把测量本身弄崩。"""
    try:
        return repr(value)
    except BaseException:  # noqa: BLE001
        return "<unreprable:%s>" % type(value).__name__


def _doc_fp_owned(value) -> bool:
    """只有本仓文档模型自己的对象才继续展开 ``__dict__``（防把外部对象图拖进来）。"""
    module = getattr(type(value), "__module__", "") or ""
    return module.split(".", 1)[0] == _DOC_FP_OWNED_ROOT


def _doc_normalize(value, depth=0, seen=None, budget=None):
    """任意文档值 → 确定性规范化结构（**不经过 L1 投影**，未知属性照样看得见）。"""
    if budget is None:
        budget = [_DOC_FP_MAX_NODES]
    budget[0] -= 1
    if budget[0] <= 0:
        return "<budget>"
    if depth > _DOC_FP_MAX_DEPTH:
        return "<depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"__kind__": type(value).__name__, "hex": bytes(value).hex()}
    expandable = (isinstance(value, (dict, list, tuple, set, frozenset))
                  or dataclasses.is_dataclass(value)
                  or isinstance(getattr(value, "__dict__", None), dict))
    if expandable:
        if seen is None:
            seen = set()
        if id(value) in seen:
            return "<cycle>"
        seen = seen | {id(value)}
    if isinstance(value, dict):
        items = sorted(((str(key), _doc_normalize(item, depth + 1, seen, budget))
                        for key, item in value.items()), key=lambda kv: kv[0])
        return {"__kind__": type(value).__name__, "__items__": items}
    if isinstance(value, (list, tuple)):
        return {"__kind__": type(value).__name__,
                "__items__": [_doc_normalize(item, depth + 1, seen, budget) for item in value]}
    if isinstance(value, (set, frozenset)):
        rendered = sorted(_doc_fp_safe_repr(_doc_normalize(item, depth + 1, seen, budget))
                          for item in value)
        return {"__kind__": type(value).__name__, "__items__": rendered}
    fields: list = []
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = [(str(f.name), getattr(value, f.name, "<missing>")) for f in dataclasses.fields(value)]
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict) and (fields or _doc_fp_owned(value)):
        known = {name for name, _value in fields}
        extra = sorted(((str(key), item) for key, item in attrs.items() if str(key) not in known),
                       key=lambda kv: kv[0])
        merged = sorted(fields, key=lambda kv: kv[0]) + extra
        return {"__kind__": type(value).__name__,
                "__items__": [[name, _doc_normalize(item, depth + 1, seen, budget)]
                              for name, item in merged]}
    return {"__kind__": type(value).__name__, "__repr__": _doc_fp_safe_repr(value)}


def _doc_fingerprint(value) -> str:
    """整份文档对象的规范化摘要（sha256）：**L1 投影之外**的桶/属性也在这里。"""
    try:
        text = json.dumps(_doc_normalize(value), sort_keys=True, ensure_ascii=False, default=repr)
    except BaseException:  # noqa: BLE001 - 摘要器不许把测量弄崩（降级但留痕）
        text = "<unfingerprintable>" + _doc_fp_safe_repr(value)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _run_ladder(fn, doc, variants, tag, limit=_MAX_ATTEMPTS_PER_OP, declared=(False, "")):
    """单个上下文形状上的梯子。

    未声明只读 ⇒ **原口径**（必须返回 uuid 列表；非空且 L1 变 = live；非空但 L1 没变 = phantom）。
    声明只读 ⇒ 不走 uuid 列表契约：**三面**（L1 文本 / doc 级指纹 / 文件系统）全部无变化且返回
    非空数据 = query；L1 变了 ⇒ phantom（``declared_readonly_but_l1_changed``）；**L1 没变但 doc
    级指纹变了**（写的是 L1 不投影的文档桶）⇒ 同样 phantom（``declared_readonly_but_doc_changed``）
    —— 声明不能当免死金牌，投影看不见不等于没写。
    """
    out = {"attempts": 0, "live": 0, "query": 0, "noop": 0, "reject": 0, "phantom": 0,
           "mismatch": 0, "doc_mismatch": 0, "unexpected": [], "caderror": [], "detail": ""}
    is_readonly, decl_evidence = declared
    for idx, kwargs in enumerate(variants):
        if out["attempts"] >= limit:
            break
        out["attempts"] += 1
        work = copy.deepcopy(doc)
        before = _canonical_text(work)
        before_doc = _doc_fingerprint(work)
        try:
            result = fn(work, **copy.deepcopy(kwargs))
        except CadError as exc:
            out["reject"] += 1
            out["caderror"].append(str(getattr(exc, "reason", "") or str(exc)))
            continue
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - 非 CadError ⇒ 真违约，必须记账
            out["unexpected"].append(f"{type(exc).__name__}@{tag}#{idx}")
            if not out["detail"]:
                out["detail"] = f"unexpected:{type(exc).__name__}@{tag}#{idx}: {exc}"
            continue
        changed = _canonical_text(work) != before
        doc_after = _doc_fingerprint(work)
        doc_changed = doc_after != before_doc
        if is_readonly:
            if changed:  # 声明只读却写了文档 ⇒ 声明与行为不符，仍红
                out["mismatch"] += 1
                out["phantom"] += 1
                if not out["detail"]:
                    out["detail"] = (f"phantom@{tag}#{idx} decl_mismatch={decl_evidence} "
                                     f"declared_readonly_but_l1_changed")
                continue
            if doc_changed:  # 第 1 面看不见、第 3 面（doc 级指纹）看见了 ⇒ 也是偷写，照样红
                out["mismatch"] += 1
                out["doc_mismatch"] += 1
                out["phantom"] += 1
                if not out["detail"]:
                    out["detail"] = (f"phantom@{tag}#{idx} decl_mismatch={decl_evidence} "
                                     f"declared_readonly_but_doc_changed "
                                     f"doc_fp={before_doc[:12]}->{doc_after[:12]}")
                continue
            if _is_data_result(result):
                out["query"] += 1
                out["detail"] = (f"query@{tag}#{idx} decl={decl_evidence} "
                                 f"faces=l1_unchanged+doc_unchanged+fs_pending "
                                 f"data={type(result).__name__} n={_result_size(result)}")
                return out
            out["noop"] += 1
            if not out["detail"]:
                out["detail"] = f"noop@{tag}#{idx} declared_readonly_empty_data"
            continue
        try:
            uuids = FEATURES.validate_result(result, op=getattr(fn, "__name__", "?"))
        except FEATURES.SliceContractError as exc:
            out["unexpected"].append(f"SliceContractError@{tag}#{idx}")
            if not out["detail"]:
                out["detail"] = f"bad_result@{tag}#{idx}: {exc}"
            continue
        except BaseException as exc:  # noqa: BLE001
            out["unexpected"].append(f"{type(exc).__name__}@{tag}#{idx}")
            continue
        if uuids and changed:
            out["live"] += 1
            out["detail"] = f"live@{tag}#{idx} n={len(uuids)}"
            return out
        if not uuids:
            out["noop"] += 1
            if not out["detail"]:
                out["detail"] = f"noop@{tag}#{idx}"
            continue
        if doc_changed:  # 第三证据面：写的是 L1 不投影的文档桶 ⇒ 有副作用且 L1 未变 ⇒ write
            out["write"] = out.get("write", 0) + 1
            out["write_evidence"] = [f"doc_fingerprint_changed:{before_doc[:12]}->{doc_after[:12]}"]
            if not out["detail"]:
                out["detail"] = (f"write@{tag}#{idx} n={len(uuids)} l1_unchanged "
                                 f"doc_fp_changed undeclared")
            return out
        out["phantom"] += 1
        if not out["detail"]:
            out["detail"] = f"phantom@{tag}#{idx} n={len(uuids)} l1_unchanged undeclared"
    return out


def _measure_op(name, fn, shapes, module=None):
    """测一个 op：真 Document 主形状严格判；全 reject 才按序升级到别的合法上下文。

    状态口径（**互斥**）：``live``（L1 真变）/ ``query``（声明只读 ∧ 三面全静 ∧ 非空数据）/
    ``write``（有副作用证据（fs 新增/修改 或 doc 桶指纹变化）∧ L1 未变 ∧ 未声明只读 ⇒ 可见档、不判红）/
    ``noop`` / ``reject`` / ``broken``（异常 ⇒ 红）/ ``phantom``（**既无副作用也无 L1 变更** ⇒ 红；
    或声明只读却偷写 ⇒ 红）。
    """
    declared = _readonly_declaration(module, name, fn)
    rec = {"op": name, "state": "unmeasured", "attempts": 0, "detail": "", "unexpected": [],
           "reasons": [], "doc_reasons": [], "live_shape": "", "shape_states": {},
           "reject_class": "", "decl": declared[1] if declared[0] else "",
           "write_evidence": []}
    variants = _variants(fn)
    if variants is None:
        rec["state"] = "broken"
        rec["detail"] = "signature:no_first_positional"
        return rec
    state = "unmeasured"
    primary = None
    for tag, shape in shapes:
        limit = _MAX_ATTEMPTS_PER_OP if tag == "l1_doc" else _SECONDARY_ATTEMPTS
        ladder = _run_ladder(fn, shape, variants, tag, limit=limit, declared=declared)
        rec["attempts"] += ladder["attempts"]
        rec["reasons"].extend(ladder["caderror"])
        if tag == "l1_doc":
            rec["doc_reasons"].extend(ladder["caderror"])
        if primary is None:
            primary = ladder
        if ladder["unexpected"]:
            rec["unexpected"].extend(f"{tag}:{item}" for item in ladder["unexpected"])
        shape_state = ("live" if ladder["live"] else "query" if ladder["query"]
                       else "write" if ladder.get("write") else "phantom" if ladder["phantom"]
                       else "noop" if ladder["noop"] else "reject")
        rec["shape_states"][tag] = shape_state
        if shape_state == "write":
            rec["write_evidence"] = list(ladder.get("write_evidence") or [])
        if shape_state in ("live", "query", "write", "noop", "phantom"):
            state = shape_state
            if shape_state == "live":
                rec["live_shape"] = tag
            break
    # 主形状（真 Document）上任何非 CadError ⇒ broken；其它形状上的致命类型同样红。
    doc_unexpected = [item for item in rec["unexpected"] if item.startswith("l1_doc:")]
    fatal_any = [item for item in rec["unexpected"]
                 if item.split("@")[0].split(":")[-1] in _FATAL_TOKENS]
    if doc_unexpected or fatal_any:
        rec["state"] = "broken"
        rec["detail"] = "fatal_exception:" + ",".join((doc_unexpected or fatal_any)[:3]) + "; " + \
            (primary["detail"] if primary else "")
        return rec
    if state == "unmeasured":
        state = "reject"
    seen: list[str] = []
    for reason in rec["reasons"]:
        if reason not in seen:
            seen.append(reason)
    rec["reasons"] = seen
    if state == "reject":
        rec["reject_class"] = _classify_reason(seen[0]) if seen else "unspecified"
    rec["state"] = state
    parts = []
    if declared[0]:
        parts.append(f"declared_readonly={declared[1]}")
    if primary and primary["detail"]:
        parts.append(primary["detail"])
    parts.append("shapes=" + ",".join(f"{tag}:{rec['shape_states'].get(tag, 'NA')}"
                                      for tag, _shape in shapes))
    if seen:
        parts.append("caderror=" + ",".join(seen[:3]))
    rec["detail"] = "; ".join(parts) or "no_attempt"
    # 副作用对证（L1 之外的证据面）：沙箱里真调过、真落了文件 ⇒ 要么判 phantom（**声明只读**却写盘，
    # 该片照样红、声明不是免死金牌），要么判 **write**（**未**声明只读 ⇒ 导出/落盘型操作的正解）。
    # 证据进 ``side_effect_hits``/``side_effect_evidence``（write 档另进 ``write_evidence``）。
    rec["side_effect_hits"] = 0
    rec["side_effect_evidence"] = []
    if state in ("query", "noop", "reject", "phantom", "write"):
        hits = _side_effect_probe(fn, shapes[0][1])
        rec["side_effect_hits"] = len(hits)
        rec["side_effect_evidence"] = hits[:4]
        if hits and declared[0]:
            # 闸④：声明只读却写盘 ⇒ 仍 phantom（点名 MISLABEL_READONLY）；**不许**洗成 write
            state = "phantom"
            rec["state"] = state
            rec["detail"] = (f"phantom@side_effect decl_mismatch={declared[1]} "
                             f"declared_readonly_but_fs_changed side_effect_hits={len(hits)} "
                             f"evidence={';'.join(hits[:3])}")
        elif hits and state != "write":
            # 四档 write：有副作用证据（FS 新增/修改）∧ L1 未变 ∧ 未声明只读 ⇒ 可见档，不判红
            state = "write"
            rec["state"] = state
            rec["write_evidence"] = hits[:4]
            rec["detail"] = (f"write@side_effect n={len(hits)} l1_unchanged undeclared "
                             f"evidence={';'.join(hits[:3])}")
    if state == "write" and not rec["write_evidence"]:
        rec["write_evidence"] = ["unspecified"]  # 不许"记了 write 却拿不出证据"
    return rec


# --------------------------------------------------------------------------- 逐片扫描

#: 两组口径（判定行前缀）：主集合 = ``FEATURES.iter_slice_paths()``（``features/*_w*.py``，不递归）；
#: 隔离区 = ``features/_quarantine/*_w*.py``。两组**分开扫、分开记账**，绝不合并成一个数。
SCOPE_MAIN = "main"
SCOPE_QUARANTINE = "quarantined"

#: 隔离区目录（真源 = ``FEATURES.QUARANTINE_DIRNAME``，不另写目录名字面量）。
QUARANTINE_DIR = Path(FEATURES.FEATURES_DIR) / FEATURES.QUARANTINE_DIRNAME

#: 隔离区的**合成包名**：隔离目录没有 ``__init__.py``，但给它一个带 ``__path__`` 的合成包，
#: 片就能按 ``<pkg>.<stem>`` 加载（相对 import 可用），且**不与主集合同 stem 的模块共用槽位**。
_QUARANTINE_PKG = "cadcore.ops.features._quarantine"

_RESULTS: dict[str, list] = {}


def slice_paths(scope: str = SCOPE_MAIN) -> list[Path]:
    """按 scope 取片路径：``main`` 只给主目录，``quarantined`` 只给 ``_quarantine/``（互斥）。"""
    root = QUARANTINE_DIR if scope == SCOPE_QUARANTINE else None
    return FEATURES.iter_slice_paths(root)


def _ensure_quarantine_pkg() -> str:
    """登记隔离区合成包（幂等）；返回包名，供片按 ``<pkg>.<stem>`` 加载。"""
    pkg = sys.modules.get(_QUARANTINE_PKG)
    if pkg is None:
        pkg = types.ModuleType(_QUARANTINE_PKG)
        pkg.__path__ = [str(QUARANTINE_DIR)]  # type: ignore[attr-defined]
        pkg.__package__ = _QUARANTINE_PKG
        sys.modules[_QUARANTINE_PKG] = pkg
        parent = sys.modules.get("cadcore.ops.features")
        if parent is not None:
            setattr(parent, "_quarantine", pkg)
    return _QUARANTINE_PKG


def _load_quarantine_one(path: Path):
    """按文件路径加载隔离片：模块名 = ``cadcore.ops.features._quarantine.<stem>``（独享槽位）。"""
    name = f"{_ensure_quarantine_pkg()}.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"spec_unavailable:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except BaseException:
        sys.modules.pop(name, None)  # 加载失败不留半成品，免得污染后续 import
        raise
    return module


def _scan(scope: str = SCOPE_MAIN) -> list:
    """扫**一组**片并逐 op 实测；每片记 ``scope`` 标签，供"两栏不许互相污染"复核。"""
    loader = _load_quarantine_one if scope == SCOPE_QUARANTINE else FEATURES.load_one
    entries = []
    shapes = _shapes()
    for path in slice_paths(scope):
        stem = path.stem
        try:
            module = loader(path)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001
            entries.append({"stem": stem, "scope": scope, "family": FEATURES.family_of_path(path),
                            "ops": [], "problems": [f"import_failed:{type(exc).__name__}: {exc}"],
                            "verdict": "FAIL"})
            continue
        ops, problems = FEATURES.check_interface(module)
        family = FEATURES.canon_family(getattr(module, "FAMILY", "")) or FEATURES.family_of_path(path)
        recs = [_measure_op(op_name, ops[op_name], shapes, module) for op_name in sorted(ops)]
        bad = [r for r in recs if r["state"] in ("broken", "phantom")]
        entries.append({
            "stem": stem, "scope": scope, "family": family, "ops": recs,
            "problems": list(problems),
            "verdict": "OK" if (not problems and recs and not bad) else "FAIL",
        })
    return entries


def _results(scope: str = SCOPE_MAIN) -> list:
    if scope not in _RESULTS:
        _RESULTS[scope] = _scan(scope)
    return _RESULTS[scope]


def _counts(entries) -> dict:
    c = {"live": 0, "query": 0, "write": 0, "noop": 0, "reject": 0, "broken": 0, "phantom": 0,
         "total": 0, "unmeasured": 0}
    for entry in entries:
        for _ in entry["problems"]:
            c["broken"] += 1
            c["total"] += 1
        for rec in entry["ops"]:
            c["total"] += 1
            state = rec["state"]
            if state in ("live", "query", "write", "noop", "reject", "broken"):
                c[state] += 1
            else:  # phantom / unmeasured 计入 broken（保守口径，DETAIL 行另列）
                c["broken"] += 1
            if state == "phantom":
                c["phantom"] += 1
            if state == "unmeasured":
                c["unmeasured"] += 1
    return c


def _query_basis_counts(entries) -> Counter:
    """query 的**声明依据**分布（OPERATIONS_META / docstring / rettype），供复核"凭什么放回"。"""
    counter: Counter = Counter()
    for entry in entries:
        for rec in entry["ops"]:
            if rec["state"] == "query":
                basis = (rec.get("decl") or "?").split(":", 1)[0].split(".", 1)[0]
                counter[basis] += 1
    return counter


def _reject_class_counts(entries) -> Counter:
    """判定行口径：每个最终 reject 的 op 记一次（按它首个观测到的 reason 类）。"""
    counter: Counter = Counter()
    for entry in entries:
        for rec in entry["ops"]:
            if rec["state"] == "reject":
                counter[rec["reject_class"] or "unspecified"] += 1
    return counter


def _doc_reject_stats(entries) -> dict:
    """逐类判据：真文档形状上的 reject 账 + 补上下文后能否转 live。"""
    stats: dict[str, dict] = {}
    for entry in entries:
        for rec in entry["ops"]:
            classes: list[str] = []
            for raw in rec["doc_reasons"]:
                cls = _classify_reason(raw)
                if cls not in classes:
                    classes.append(cls)
            for cls in classes:
                bucket = stats.setdefault(cls, {"doc_reject": 0, "flipped": 0, "stuck": 0,
                                                "raws": [], "live_examples": [],
                                                "stuck_examples": []})
                bucket["doc_reject"] += 1
                for raw in rec["doc_reasons"]:
                    if _classify_reason(raw) == cls and raw not in bucket["raws"] \
                            and len(bucket["raws"]) < 3:
                        bucket["raws"].append(raw)
                if rec["live_shape"] and rec["live_shape"] != "l1_doc":
                    bucket["flipped"] += 1
                    if len(bucket["live_examples"]) < 3:
                        bucket["live_examples"].append(f"{rec['op']}->live@{rec['live_shape']}")
                else:
                    bucket["stuck"] += 1
                    if len(bucket["stuck_examples"]) < 3:
                        bucket["stuck_examples"].append(rec["op"])
    return stats


def _reject_class_lines(entries, limit=8) -> list:
    stats = _doc_reject_stats(entries)
    ordered = sorted(stats.items(), key=lambda kv: (-kv[1]["doc_reject"], kv[0]))
    lines = []
    for cls, bucket in ordered[:limit]:
        if bucket["flipped"]:
            verdict = "fixture_context_gap(recoverable)"
            evidence = "flip=" + ",".join(bucket["live_examples"])
        else:
            verdict = "op_capability_gap(no_live_path_in_fixture_context)"
            evidence = "stuck=" + ",".join(bucket["stuck_examples"])
        lines.append(
            f"REJECT_CLASS {cls} doc_reject={bucket['doc_reject']} "
            f"flipped_to_live={bucket['flipped']} stuck={bucket['stuck']} verdict={verdict} "
            f"raw={','.join(bucket['raws'])[:60]} {evidence}"
        )
    if len(ordered) > limit:
        rest = ordered[limit:]
        lines.append(f"REJECT_CLASS_OTHERS classes={len(rest)} "
                     f"doc_reject={sum(b['doc_reject'] for _c, b in rest)} "
                     f"names={','.join(c for c, _b in rest)[:80]}")
    return lines


def _summary_lines(entries) -> list:
    lines = []
    # 副作用/误标点名（放在最前：``main()`` 的 18 行截断只砍后面的 SLICE_FAMILY/BAD_OP）
    side_hits = 0
    doc_hits = 0
    mislabeled: list = []
    mislabel_lines: list = []
    write_ops: list = []
    for entry in entries:
        for rec in entry["ops"]:
            hits = int(rec.get("side_effect_hits") or 0)
            decl = rec.get("decl") or ""
            detail = rec.get("detail") or ""
            doc_changed = "declared_readonly_but_doc_changed" in detail
            if hits and decl:
                side_hits += hits
                mislabeled.append(f"{entry['stem']}::{rec['op']}")
                mislabel_lines.append(
                    f"MISLABEL_READONLY {entry['stem']}::{rec['op']} decl={decl or '?'} "
                    f"reason=fs_side_effect hits={hits} "
                    f"evidence={';'.join(rec.get('side_effect_evidence') or [])[:120]}")
            elif rec["state"] == "write":
                # 四档 write：有副作用且**未**声明只读 ⇒ 点名（可见）但**不是** MISLABEL、不判红
                write_ops.append(f"{entry['stem']}::{rec['op']}")
                if len(write_ops) <= 3:
                    lines.append(
                        f"WRITE_SIDE_EFFECT {entry['stem']}::{rec['op']} decl=none "
                        f"l1_unchanged evidence="
                        f"{';'.join(rec.get('write_evidence') or rec.get('side_effect_evidence') or [])[:120]}")
            elif rec["state"] == "phantom" and decl:
                mislabeled.append(f"{entry['stem']}::{rec['op']}")
                if doc_changed:
                    doc_hits += 1
                    reason = "doc_bucket_changed(l1_invisible)"
                else:
                    reason = "l1_changed_by_readonly_decl"
                mislabel_lines.append(
                    f"MISLABEL_READONLY {entry['stem']}::{rec['op']} decl={decl} "
                    f"reason={reason} hits=0 detail={detail[:120]}")
    # 判定行必须在最前（``main()`` 的 18 行截断只砍后面的 SLICE_FAMILY/BAD_OP）
    lines.insert(0, f"SIDE_EFFECT_JUDGE side_effect_hits={side_hits} mislabel={len(mislabeled)} "
                    f"mislabeled_readonly={len(mislabeled)} doc_fingerprint_hits={doc_hits} "
                    f"write_ops={len(write_ops)} "
                    f"judge=l1_text+fs_snapshot_cwd+probe_tmp+doc_fingerprint "
                    f"ops={','.join(mislabeled)[:120] or 'none'}")
    lines[1:1] = mislabel_lines
    agg: dict[str, dict] = {}
    for entry in entries:
        bucket = agg.setdefault(entry["family"] or "?", {"slices": 0, "ops": 0, "live": 0,
                                                         "query": 0, "write": 0, "noop": 0,
                                                         "reject": 0, "broken": 0, "phantom": 0})
        bucket["slices"] += 1
        for _ in entry["problems"]:
            bucket["ops"] += 1
            bucket["broken"] += 1
        for rec in entry["ops"]:
            bucket["ops"] += 1
            state = (rec["state"] if rec["state"] in ("live", "query", "write", "noop", "reject")
                     else "broken")
            bucket[state] += 1
            if rec["state"] == "phantom":
                bucket["phantom"] += 1
    for family in sorted(agg):
        b = agg[family]
        lines.append(
            f"SLICE_FAMILY {family} slices={b['slices']} ops={b['ops']} live={b['live']} "
            f"query={b['query']} write={b['write']} noop={b['noop']} reject={b['reject']} "
            f"broken={b['broken']} phantom={b['phantom']}"
        )
    for entry in entries:
        for rec in entry["ops"]:
            if rec["state"] in ("broken", "phantom"):
                lines.append(f"BAD_OP {entry['stem']}::{rec['op']} state={rec['state']} "
                             f"detail={rec['detail'][:140]}")
        for problem in entry["problems"]:
            lines.append(f"BAD_SLICE {entry['stem']} {problem[:140]}")
    return lines


#: 第四档 ``write`` 的两个夹具（**写死在文件里**，不靠环境变量；``test_ruler_sees_red`` 与
#: ``--selftest-write`` 共用同一份）：A = 写盘但不改 L1 且**未**声明只读 ⇒ ``write``；
#: B = **声明只读却写盘** ⇒ 仍 ``phantom`` + 点名 ``MISLABEL_READONLY``（判红）。
def _fixture_write_undeclared(doc, *, out_path=None):
    target = (out_path if isinstance(out_path, str) and out_path and not os.path.isdir(out_path)
              else os.path.join(_PROBE_TMP, "ruler_write_undeclared.out"))
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("write-state\n")
    return [LINE_UUID]  # 非空 uuid 列表 + L1 未变 + 有副作用 + 无只读声明 ⇒ write（不是 phantom）


def _fixture_write_declared_readonly(doc, *, out_path=None):
    """READONLY: 声明只读却写盘 ⇒ 副作用对证必须判 phantom（点名 MISLABEL_READONLY，判红）。"""
    target = (out_path if isinstance(out_path, str) and out_path and not os.path.isdir(out_path)
              else os.path.join(_PROBE_TMP, "ruler_write_declared_readonly.out"))
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("readonly-mismatch\n")
    return {"written": os.path.basename(target), "count": 1}


def _fixture_gate(name: str, fn) -> tuple[dict, list]:
    """把一个夹具按"真片"走完整判定（``_measure_op`` + ``_summary_lines``），返回 ``(rec, 行)``。

    用于对证"点名"与档位一致：``write`` ⇒ 点名 ``WRITE_SIDE_EFFECT`` 且**不**点名 MISLABEL；
    ``phantom``（声明只读却写盘）⇒ 点名 ``MISLABEL_READONLY``。
    """
    rec = _measure_op(name, fn, _shapes(), None)
    entry = {"stem": "ruler_fixture", "scope": SCOPE_MAIN, "family": "ruler",
             "problems": [], "ops": [rec], "verdict": "OK"}
    return rec, _summary_lines([entry])


def selftest_write_state() -> int:
    """第四档 ``write`` 的**两具自证**：逐具一行判定，``rc``：0 = 两具都落进期望档。

    * 具 A ``_fixture_write_undeclared``：写盘但不改 L1 且**未**声明只读 ⇒ ``write``（可见档、不判红）；
    * 具 B ``_fixture_write_declared_readonly``：**声明只读却写盘** ⇒ ``phantom`` **且点名
      MISLABEL_READONLY（判红）** —— 声明不是免死金牌，也不许被洗成 ``write``。
    """
    gates = (("write_undeclared_fs", _fixture_write_undeclared, "write", 0, 1),
             ("readonly_declared_fs_write", _fixture_write_declared_readonly, "phantom", 1, 0))
    ok = True
    for name, fn, expect, expect_mislabel, expect_write in gates:
        rec, lines = _fixture_gate(name, fn)
        mislabel = [ln for ln in lines if ln.startswith("MISLABEL_READONLY") and name in ln]
        write_named = [ln for ln in lines if ln.startswith("WRITE_SIDE_EFFECT") and name in ln]
        passed = (rec["state"] == expect and len(mislabel) == expect_mislabel
                  and len(write_named) == expect_write
                  and (int(rec.get("side_effect_hits") or 0) >= 1))
        ok = ok and passed
        print(f"RULER_WRITE_SELFTEST gate={name} state={rec['state']} expect={expect} "
              f"decl={rec['decl'] or 'none'} side_effect_hits={rec['side_effect_hits']} "
              f"mislabel_named={len(mislabel)} write_named={len(write_named)} "
              f"evidence={';'.join(rec.get('write_evidence') or rec.get('side_effect_evidence') or [])[:100]} "
              f"verdict={'PASS' if passed else 'FAIL'}")
    verdict = "PASS" if ok else "FAIL"
    print(f"RULER_WRITE_STATE verdict={verdict} gates={len(gates)} write=1 mislabel=1 "
          f"judge=l1_text+doc_fingerprint+fs_snapshot rc={0 if ok else 1}")
    return 0 if ok else 1


class FeaturesLiveMeasurement(unittest.TestCase):
    """逐片 ``subTest``：任何 broken/phantom ⇒ 该片红；判定行由 ``main()`` 打印。"""

    def test_every_slice_live_ops(self):
        entries = _results()
        self.assertTrue(entries, "no feature slices discovered")
        for entry in entries:
            with self.subTest(slice=entry["stem"]):
                bad = [r for r in entry["ops"] if r["state"] in ("broken", "phantom")]
                detail = "; ".join(
                    list(entry["problems"])
                    + [f"{r['op']}:{r['state']}:{r['detail']}" for r in bad]
                )
                self.assertEqual(entry["verdict"], "OK", detail or "slice_not_ok")

    def test_ruler_sees_red(self):
        """尺子自证：合成 op 必须逐条落进 live/query/noop/reject/broken/phantom。

        重点两闸（禁止"返回非空就算过"）：
        * ``query`` 只在**有声明依据**（OPERATIONS_META / docstring 首行 READONLY / 返回注解是数据）
          且**真返回非空数据**且 **L1 逐字节未变**时给出；
        * 无声明 + 非空 list + L1 未变 ⇒ ``phantom``；声明只读却偷写 ⇒ ``phantom``（声明与行为不符）。
        """
        def _live(doc, **_kw):
            entry = {"uuid": LINE_UUID, "kind": "text", "text": "live-probe", "layer": "0",
                     "at": {"x": 0, "y": 0}}
            if isinstance(doc, dict):
                doc.setdefault("annotations", []).append(entry)
            else:
                doc.annotations.append(entry)  # 真 Document：annotations 是 L1 冻结字段
            return [LINE_UUID]

        def _noop(doc, *, mode=None):
            return []

        def _reject(doc, *, mode=None):
            raise CadError("probe_reject", reason="bad_position")

        def _typed(doc):
            raise TypeError("probe_typeerror")

        def _badval(doc):
            return 42

        def _query_declared(doc, *, mode=None):
            """READONLY: 只读查询夹具（docstring 首行声明）—— 返回候选数据，一个字节都不写。"""
            return {"candidates": [{"uuid": LINE_UUID, "kind": "endpoint"}], "count": 1}

        def _query_rettype(doc) -> dict[str, int]:
            return {"measured_nm": 10_000_000}          # 依据③：返回注解声明的是数据

        def _query_empty(doc, *, mode=None):
            """READONLY: 声明只读但返回空数据 ⇒ noop（空数据不算 query）。"""
            return {}

        def _phantom_undeclared(doc, *, mode=None):
            return [LINE_UUID]                          # 非空 uuid 列表却不动文档，且无只读声明

        def _phantom_mismatch(doc, *, mode=None):
            """READONLY: 声明只读却偷写文档 ⇒ 声明与行为不符 ⇒ 仍 phantom。"""
            _live(doc)                                  # 复用"真写一条 annotation"的形状
            return {"count": 1}

        def _query_meta(doc, *, mode=None):
            return [{"uuid": LINE_UUID, "kind": "endpoint"}]   # 依据①：片模块 OPERATIONS_META

        def _query_side_effect_file(doc, *, mode=None):
            """READONLY: 声明只读**却往 _PROBE_TMP 落一个文件** ⇒ 副作用对证必须判 phantom。"""
            target = os.path.join(_PROBE_TMP, "ruler_se_%d.txt" % len(os.listdir(_PROBE_TMP)))
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("side-effect\n")
            return {"written": os.path.basename(target), "count": 1}

        def _query_three_faces_clean(doc, *, mode=None):
            """READONLY: 真只读查询夹具（三面全静）—— L1 文本 / doc 级指纹 / 文件系统一个都不动。"""
            return {"candidates": [LINE_UUID], "count": 1}

        def _query_doc_bucket_write(doc, *, mode=None):
            """READONLY: 声明只读却往**文档桶**里登记一条模板 —— ``document_to_l1`` 不投影这个属性，
            所以第 1 面（L1 文本）看不见，只有第 3 面（doc 级指纹）能抓 ⇒ 必须判 phantom/MISLABEL。

            这正是 ``define_drawing_template`` 那类 op 的形状：不落盘、写的是文档对象自己的桶。
            """
            bucket = {"drawing_templates": [{"name": "A3", "scale": "1:1"}]}
            if isinstance(doc, dict):
                doc["drawing_templates"] = bucket          # dict 形状：L1 会变，不是本夹具的靶子
            else:
                setattr(doc, "drawing_templates", bucket)  # Document 新属性：to_payload() 不投影
            return {"registered": 1, "count": 1}

        _meta_module = types.SimpleNamespace(OPERATIONS_META={"query_meta": {"readonly": True}})
        shapes = _shapes()
        cases = [("live", _live, "live", None), ("noop", _noop, "noop", None),
                 ("reject", _reject, "reject", None), ("TypeError", _typed, "broken", None),
                 ("bad_return", _badval, "broken", None),
                 ("query_declared", _query_declared, "query", None),
                 ("query_rettype", _query_rettype, "query", None),
                 ("query_meta", _query_meta, "query", _meta_module),
                 ("query_three_faces_clean", _query_three_faces_clean, "query", None),
                 ("query_empty_data", _query_empty, "noop", None),
                 ("query_side_effect_file", _query_side_effect_file, "phantom", None),
                 ("query_doc_bucket_write", _query_doc_bucket_write, "phantom", None),
                 ("phantom_undeclared", _phantom_undeclared, "phantom", None),
                 ("phantom_decl_mismatch", _phantom_mismatch, "phantom", None),
                 ("write_undeclared_fs", _fixture_write_undeclared, "write", None),
                 ("readonly_declared_fs_write", _fixture_write_declared_readonly, "phantom", None)]
        for name, fn, expect, module in cases:
            with self.subTest(ruler=name):
                rec = _measure_op(name, fn, shapes, module)
                print(f"RULER_CASE {name} state={rec['state']} expect={expect} "
                      f"decl={rec['decl'] or 'none'} detail={rec['detail'][:120]}")
                self.assertEqual(rec["state"], expect, rec["detail"])
                if name == "query_side_effect_file":
                    # 变异体反证：尺子必须**看见**那个新文件并记账（不是碰巧判红）
                    self.assertGreaterEqual(rec["side_effect_hits"], 1,
                                            "副作用对证没记账：fs 快照必须看见新增文件")
                if name in ("write_undeclared_fs", "readonly_declared_fs_write"):
                    # 第四档 write 的两具自证（行为写死在文件里）：**点名**必须与档位一致 ——
                    # 未声明只读的写盘点名 WRITE_SIDE_EFFECT 且**不**判 MISLABEL；声明只读却写盘
                    # 反之（仍 MISLABEL 判红，不许洗成 write）。
                    gate_rec, gate_lines = _fixture_gate(name, fn)
                    mislabel = [ln for ln in gate_lines
                                if ln.startswith("MISLABEL_READONLY") and name in ln]
                    write_named = [ln for ln in gate_lines
                                   if ln.startswith("WRITE_SIDE_EFFECT") and name in ln]
                    evidence = (gate_rec.get("write_evidence")
                                or gate_rec.get("side_effect_evidence") or [""])
                    print(f"RULER_WRITE_GATE {name} state={gate_rec['state']} "
                          f"side_effect_hits={gate_rec['side_effect_hits']} "
                          f"decl={gate_rec['decl'] or 'none'} mislabel_named={len(mislabel)} "
                          f"write_named={len(write_named)} evidence={evidence[0][:80]}")
                    if name == "write_undeclared_fs":
                        self.assertEqual(gate_rec["state"], "write", gate_rec["detail"])
                        self.assertGreaterEqual(int(gate_rec["side_effect_hits"]), 1,
                                                "write 档必须有硬副作用证据（fs 新增/修改）")
                        self.assertTrue(gate_rec.get("write_evidence"),
                                        "write 档必须带可复核证据")
                        self.assertEqual(mislabel, [], "未声明只读的写盘被误判成 MISLABEL")
                        self.assertEqual(len(write_named), 1, "write 档没在判定行点名")
                    else:
                        self.assertEqual(gate_rec["state"], "phantom", gate_rec["detail"])
                        self.assertGreaterEqual(int(gate_rec["side_effect_hits"]), 1,
                                                "声明只读却写盘的夹具必须有 fs 证据")
                        self.assertEqual(len(mislabel), 1,
                                         "声明只读却写盘必须点名 MISLABEL_READONLY（判红）")
                        self.assertEqual(write_named, [], "声明只读的写盘不许洗成 write")
                if name == "query_doc_bucket_write":
                    # 第三证据面自证（夹具先自证：它测的是真件、靶子成立）：这笔写**必须**是 L1 看不见的
                    self.assertIn("declared_readonly_but_doc_changed", rec["detail"],
                                  "doc 级指纹没记账：L1 不投影的桶写入被漏掉（仍会被洗成 query）")
                    self.assertEqual(rec["side_effect_hits"], 0,
                                     "这是 doc 面夹具，不该同时有文件系统副作用")
                    probe = copy.deepcopy(shapes[0][1])
                    l1_before, fp_before = _canonical_text(probe), _doc_fingerprint(probe)
                    fn(probe)
                    self.assertEqual(_canonical_text(probe), l1_before,
                                     "夹具靶子不成立：这笔写被 L1 投影看见了（那就不是第 3 面在抓）")
                    self.assertNotEqual(_doc_fingerprint(probe), fp_before,
                                        "夹具靶子不成立：doc 级指纹没动（判红是碰巧）")
                if expect == "query":
                    # 夹具自证：①有声明依据；②真件真返回数据；③**三面**（L1 / doc 指纹 / FS）全静
                    self.assertTrue(rec["decl"], f"query fixture {name} 没有声明依据")
                    probe = copy.deepcopy(shapes[0][1])
                    before = _canonical_text(probe)
                    fp_before = _doc_fingerprint(probe)
                    produced = fn(probe)
                    self.assertEqual(_canonical_text(probe), before,
                                     f"query fixture {name} 有副作用（第 1 面 L1 变了）")
                    self.assertEqual(_doc_fingerprint(probe), fp_before,
                                     f"query fixture {name} 有副作用（第 3 面 doc 指纹变了）")
                    self.assertEqual(int(rec.get("side_effect_hits") or 0), 0,
                                     f"query fixture {name} 第 2 面 FS 不干净")
                    self.assertTrue(_is_data_result(produced),
                                    f"query fixture {name} 没返回非空数据")


class QuarantineScanMeasurement(unittest.TestCase):
    """``--include-quarantine`` 的**测量完整性**自检（**不**冒充"隔离区全绿"）。

    隔离区本就装着已知红片（逐片原因见 ``features/QUARANTINE.md``），所以这里**不**断言隔离片
    ``verdict=OK`` —— 那会变成"为了绿而放宽"。只断言测量本身没漏账、两组没串味：
    ①两组各自扫到了片（空集不许假绿）；②组内 stem 不重复（防同一个片被记两次）；
    ③每条隔离记录都带 ``scope=quarantined`` 标签；④每个 op 落在七态之一（含第四档 ``write``），
    且逐片求和 == ``_counts(total)``（有 op 没进账就会让判定行的口径失真）。
    """

    def test_quarantine_scope_measured(self):
        main_entries = _results(SCOPE_MAIN)
        q_entries = _results(SCOPE_QUARANTINE)
        main_stems = [entry["stem"] for entry in main_entries]
        q_stems = [entry["stem"] for entry in q_entries]
        self.assertTrue(main_stems, "主集合空集：iter_slice_paths 没扫到片")
        self.assertTrue(q_stems, "隔离区空集：--include-quarantine 没扫到片")
        self.assertEqual(len(set(main_stems)), len(main_stems), "主集合有重复 stem（被记两次）")
        self.assertEqual(len(set(q_stems)), len(q_stems), "隔离区有重复 stem（被记两次）")
        self.assertTrue(all(entry["scope"] == SCOPE_QUARANTINE for entry in q_entries),
                        "隔离区记录带了别组 scope 标签（两栏会互相污染）")
        states = {"live", "query", "write", "noop", "reject", "broken", "phantom", "unmeasured"}
        counted = 0
        for entry in q_entries:
            counted += len(entry["problems"])
            for rec in entry["ops"]:
                self.assertIn(rec["state"], states, f"{entry['stem']}::{rec['op']} 状态越界")
                counted += 1
        self.assertEqual(counted, _counts(q_entries)["total"],
                         "逐片求和与 _counts(total) 不一致：有 op 没进账（口径会失真）")


#: 判定 → 退出码（**三态**）：``FAIL``⇒1 / ``NA``（**未采**）⇒2 / ``PASS``⇒0。
#: 整体 rc 取「两组 + unittest」里**最严重**的那档；严重度按**语义**排（不是按 rc 数值）：
#: 测出来的失败(FAIL) > 没算(NA) > 绿(PASS)。``NA`` 的意义 = "没算"与"没失败"**不同形**。
RC_PASS, RC_FAIL, RC_NA = 0, 1, 2
_SEVERITY = {"PASS": 0, "NA": 1, "FAIL": 2}
_RC_BY_VERDICT = {"PASS": RC_PASS, "NA": RC_NA, "FAIL": RC_FAIL}


def _severity_verdict(*verdicts: str) -> str:
    """取最严重的一档（``FAIL`` > ``NA`` > ``PASS``）；无输入 ⇒ ``NA``（"没算"不许当绿）。"""
    if not verdicts:
        return "NA"
    return max(verdicts, key=lambda v: _SEVERITY[v])


def _rc_for_verdict(verdict: str) -> int:
    """判定 → 退出码：``FAIL``→1 / ``NA``→2 / ``PASS``→0（未采也给非 0，防假绿）。"""
    return _RC_BY_VERDICT[verdict]


def _group_verdict(counts: dict) -> tuple[str, str]:
    """单组判定（**组内自比**，不与另一组相加）：返回 ``(verdict, reason)``。

    三态：``FAIL``（测得红）/ ``NA``（**未采**：该组一个 op 都没进账 ⇒ rc=2，仍非 0）/ ``PASS``。
    """
    if counts["total"] == 0:
        return "NA", "no_ops_measured"
    if counts["broken"]:
        return "FAIL", "broken_ops"
    if counts["phantom"]:
        return "FAIL", "phantom_lists"
    if counts["live"] == 0:
        return "FAIL", "no_live_ops"
    return "PASS", "ok"


def _rejects_text(entries) -> str:
    class_counts = _reject_class_counts(entries)
    text = ",".join(f"{name}:{n}" for name, n in class_counts.most_common(6)) or "none"
    extra = sum(n for _name, n in class_counts.most_common()[6:])
    if extra:
        text += f",rest:{extra}"
    return text


# ------------------------------------------------- 非片文件可见化（**只看不判**，不改任何判据）
#: 判据、五档定义、片识别规则（``FEATURES.iter_slice_paths``：非递归 ``*_w*.py``、排除
#: ``__init__.py``）**一字不改**；本节只新增一件事：把"目录里没被认作片的 ``*.py``"**报出来**，
#: 让「.py 数 − 认作片数」的差额不再是靠人推断的隐账。


def _dir_py_files(scope: str = SCOPE_MAIN) -> list[Path]:
    """本组目录**顶层**的全部 ``*.py``（片 + 非片；不递归，与片识别同面）。"""
    root = QUARANTINE_DIR if scope == SCOPE_QUARANTINE else Path(FEATURES.FEATURES_DIR)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.py") if p.is_file())


def _non_slice_report(scope: str = SCOPE_MAIN) -> tuple[int, list[str]]:
    """本组目录里**没被认作片**的 ``*.py``（返回 ``(count, [文件名…])``）。

    恒等式 ``count == 目录 .py 数 − 认作片数``（逐位相符由 ``_non_slice_account_line`` 现算给出算式）。
    目录缺席 ⇒ ``count=-1``（**未采**），不许与"真为零(0)"同形（E2：没算与没失败不同形）。
    """
    root = QUARANTINE_DIR if scope == SCOPE_QUARANTINE else Path(FEATURES.FEATURES_DIR)
    if not root.is_dir():
        return -1, [f"DIR_ABSENT:{root}"]
    slice_names = {p.name for p in slice_paths(scope)}
    names = [p.name for p in _dir_py_files(scope) if p.name not in slice_names]
    return len(names), names


def _non_slice_text(prefix: str, scope: str) -> str:
    """判定行字段（带前缀）：``<prefix>non_slice_count=N <prefix>non_slice_files=[…]``。"""
    count, names = _non_slice_report(scope)
    return f"{prefix}non_slice_count={count} {prefix}non_slice_files=[{','.join(names)}]"


def _non_slice_account_line(scope: str) -> str:
    """自证行：把算式打出来 —— ``non_slice_count`` vs 「目录 .py 数 − 认作片数」逐位对账。"""
    root = QUARANTINE_DIR if scope == SCOPE_QUARANTINE else Path(FEATURES.FEATURES_DIR)
    count, names = _non_slice_report(scope)
    listed = ",".join(names)
    if count < 0:
        return (f"NON_SLICE_ACCOUNT group={scope} dir={root} dir_py=NA slices=NA "
                f"non_slice_count=-1 non_slice_files=[{listed}] identity=not_sampled verdict=NA")
    py_total = len(_dir_py_files(scope))
    slice_n = len(slice_paths(scope))
    same = "PASS" if count == py_total - slice_n else "FAIL"
    return (f"NON_SLICE_ACCOUNT group={scope} dir_py={py_total} slices={slice_n} "
            f"non_slice_count={count} non_slice_files=[{listed}] "
            f"identity={py_total}-{slice_n}={py_total - slice_n} verdict={same}")


def _scope_field_text(prefix: str, entries) -> str:
    """一组的**带前缀**记账字段（``main_*`` / ``quarantined_*``），两组各一份、绝不合并。"""
    counts = _counts(entries)
    verdict, _reason = _group_verdict(counts)
    basis = _query_basis_counts(entries)
    query_by_decl = ",".join(f"{name}:{n}" for name, n in sorted(basis.items())) or "none"
    scope = next((e.get("scope") for e in entries if e.get("scope")), SCOPE_MAIN)
    return (f"{prefix}_verdict={verdict} {prefix}_slices={len(entries)} {prefix}_ops={counts['total']} "
            f"{prefix}_live={counts['live']} {prefix}_query={counts['query']} "
            f"{prefix}_write={counts['write']} "
            f"{prefix}_query_by_decl={query_by_decl} {prefix}_noop={counts['noop']} "
            f"{prefix}_reject={counts['reject']} {prefix}_broken={counts['broken']} "
            f"{prefix}_phantom={counts['phantom']} {prefix}_unmeasured={counts['unmeasured']} "
            f"{prefix}_rejects_by_reason={_rejects_text(entries)} "
            f"{_non_slice_text(prefix + '_', scope)}")


# --------------------------------------------------------------------------- 入口

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    include_quarantine = "--include-quarantine" in argv
    write_selftest = "--selftest-write" in argv
    allowed = {"--include-quarantine", "--selftest-write"}
    unknown = [arg for arg in argv if arg not in allowed]
    if unknown:
        print("FEATURES_LIVE_USAGE unknown_args=" + ",".join(unknown) +
              " usage=python3 tests/test_features_live.py [--include-quarantine|--selftest-write]")
        return 2
    if write_selftest:  # 第四档 write 的两具自证（不进逐片扫描，单独一条腿、单独 rc）
        return selftest_write_state()
    main_entries = _results(SCOPE_MAIN)
    q_entries = _results(SCOPE_QUARANTINE) if include_quarantine else []
    scopes = [(SCOPE_MAIN, main_entries)] + ([(SCOPE_QUARANTINE, q_entries)]
                                             if include_quarantine else [])
    cases = [FeaturesLiveMeasurement, RcVerdictSelfTest] + ([QuarantineScanMeasurement]
                                                            if include_quarantine else [])
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in cases)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    # unittest 也是一条腿：它红了就是 FAIL（防"组判绿、尺子自检红"被算成绿）。
    unittest_verdict = "PASS" if result.wasSuccessful() else "FAIL"
    unexpected = Counter()
    for _label, entries in scopes:
        for entry in entries:
            for rec in entry["ops"]:
                for item in rec["unexpected"]:
                    unexpected[item.split("@")[0].split(":")[-1]] += 1
    unexpected_text = dict(sorted(unexpected.items())) or "{}"
    if include_quarantine:
        # 同 stem 出现在两边 = 两个不同的文件（各扫各自目录）；显式披露，不许当同一个片记账。
        overlap = sorted({e["stem"] for e in main_entries} & {e["stem"] for e in q_entries})
        if overlap:
            print(f"STEM_OVERLAP main_vs_quarantined n={len(overlap)} "
                  f"stems={','.join(overlap)[:120]}")
    # 明细行先出、判定行押到最后一行：`tail -1` 必见判定行
    for label, entries in scopes:
        for line in _reject_class_lines(entries, limit=8 if label == SCOPE_MAIN else 4):
            print(line)
        lines = _summary_lines(entries)
        cap = 18
        if len(lines) > cap:
            lines = lines[:cap - 1] + [f"SUMMARY_TRUNCATED group={label} "
                                       f"shown={cap - 1} all={len(lines)}"]
        for line in lines:
            print(line)
        counts = _counts(entries)
        verdict, reason = _group_verdict(counts)
        basis = _query_basis_counts(entries)
        query_by_decl = ",".join(f"{name}:{n}" for name, n in sorted(basis.items())) or "none"
        print(f"FEATURES_LIVE_DETAIL group={label} fixture={_FIXTURE_SOURCE} shapes="
              f"{','.join(tag for tag, _shape in _shapes())} slices={len(entries)} "
              f"ops={counts['total']} live={counts['live']} query={counts['query']} "
              f"write={counts['write']} query_by_decl={query_by_decl} "
              f"phantom={counts['phantom']} "
              f"unmeasured={counts['unmeasured']} unexpected_types={unexpected_text} "
              f"verdict={verdict} reason={reason} unittest_failures={len(result.failures)} "
              f"unittest_errors={len(result.errors)}")
        print(_non_slice_account_line(label))
    main_counts = _counts(main_entries)
    main_verdict, _main_reason = _group_verdict(main_counts)
    if not include_quarantine:
        overall = _severity_verdict(main_verdict, unittest_verdict)
        rc = _rc_for_verdict(overall)
        print(f"FEATURES_LIVE verdict={overall} live={main_counts['live']} "
              f"query={main_counts['query']} write={main_counts['write']} "
              f"noop={main_counts['noop']} "
              f"reject={main_counts['reject']} broken={main_counts['broken']} "
              f"total={main_counts['total']} rejects_by_reason={_rejects_text(main_entries)} "
              f"{_non_slice_text('', SCOPE_MAIN)} "
              f"main_verdict={main_verdict} unittest={unittest_verdict} rc={rc}")
        return rc
    q_counts = _counts(q_entries)
    q_verdict, _q_reason = _group_verdict(q_counts)
    overall = _severity_verdict(main_verdict, q_verdict, unittest_verdict)
    rc = _rc_for_verdict(overall)
    main_rc = _rc_for_verdict(main_verdict)
    q_rc = _rc_for_verdict(q_verdict)
    print(f"FEATURES_LIVE verdict={overall} scope=separate_groups groups=2 "
          f"{_scope_field_text(SCOPE_MAIN, main_entries)} "
          f"{_scope_field_text(SCOPE_QUARANTINE, q_entries)} "
          f"main_rc={main_rc} quarantined_rc={q_rc} unittest={unittest_verdict} rc={rc}")
    return rc


class RcVerdictSelfTest(unittest.TestCase):
    """尺子自证②：判定 → 退出码的**承重二值判据**（``FAIL``⇒1 / ``NA``⇒2 / ``PASS``⇒0）。

    反证"假绿"那条路：``NA``（一个 op 都没采到）**必须**非 0，不许与"没失败"同形；
    两组里只要有一组 FAIL/NA，整体 rc 就不许是 0（原缺陷：隔离区 ``verdict=FAIL`` 却 ``rc=0``）。
    """

    def test_verdict_to_rc_mapping(self):
        self.assertEqual(_rc_for_verdict("PASS"), 0)
        self.assertEqual(_rc_for_verdict("FAIL"), 1)
        self.assertEqual(_rc_for_verdict("NA"), 2, "未采(NA)必须非 0：不许与没失败同形")

    def test_group_verdict_three_states(self):
        clean = {"total": 3, "live": 2, "query": 1, "noop": 0, "reject": 0,
                 "broken": 0, "phantom": 0, "unmeasured": 0}
        self.assertEqual(_group_verdict(clean)[0], "PASS")
        self.assertEqual(_group_verdict(dict(clean, broken=1))[0], "FAIL")
        self.assertEqual(_group_verdict(dict(clean, total=0, live=0, query=0))[0], "NA",
                         "零 op 进账 = 未采，不是绿")

    def test_overall_rc_is_worst_group(self):
        for main_v, q_v, expect_rc in (("PASS", "PASS", 0), ("PASS", "FAIL", 1),
                                       ("FAIL", "FAIL", 1), ("PASS", "NA", 2),
                                       ("NA", "PASS", 2), ("NA", "FAIL", 1)):
            with self.subTest(main=main_v, quarantined=q_v):
                overall = _severity_verdict(main_v, q_v)
                self.assertEqual(_rc_for_verdict(overall), expect_rc,
                                 f"整体 rc 不是最严重档：{main_v}+{q_v}={overall}")

    def test_fixture_context_does_not_relax_reject(self):
        """夹具扩容席的**阴性对照**：夹具故意没有的上下文，op 必须**照旧 reject**（不许被放宽成绿）。

        用尺子自己的取值器（`_variants` + `_kw_value`）与自己的梯子（`_run_ladder`）现跑：
        GDT 帧这类上下文本夹具**没有构造**（`l1_fixture.context_report` 里也没有它），
        所以这几个 op 只许落 `reject`；`live/query/phantom` 任何一个 > 0 就等于"取值器被放宽了"。
        """
        doc = (_FIXTURE_CTX or {}).get("doc")
        if doc is None:
            self.skipTest("真夹具缺席：没有上下文可做阴性对照")
        # 对照物优先取**隔离片**：它的 `gdt_*` 在夹具没有 GDT 帧时显式抛 `gdt_frames_missing`；
        # 主模块同名族缺帧时按空结果返回（那是 noop，不是 reject）⇒ 只有隔离片验得到"照旧 reject"。
        path = (Path(__file__).resolve().parents[1] / "cadcore" / "ops" / "features"
                / "_quarantine" / "sw_drawings__w12.py")
        spec = importlib.util.spec_from_file_location("no_relax_control_slice", path)
        self.assertIsNotNone(spec, "对照物缺席：隔离片路径不对")
        module = importlib.util.module_from_spec(spec)
        sys.modules["no_relax_control_slice"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        names = sorted(name for name in dir(module)
                       if "gdt" in name.lower() and callable(getattr(module, name, None)))
        if not names:  # 隔离片没有该族 ⇒ 退回主模块（只验"没被放宽成 live/query/phantom"）
            module = importlib.import_module("cadcore.ops.sw_drawings")
            names = sorted(name for name in dir(module)
                           if "gdt" in name.lower() and callable(getattr(module, name, None)))
        self.assertTrue(names, "对照物缺席：找不到 gdt 族 op（阴性对照不成立，必须报红）")
        measured = 0
        total = {"reject": 0, "live": 0, "query": 0, "phantom": 0}
        report = []
        for name in names:
            fn = getattr(module, name)
            out = _run_ladder(fn, doc, _variants(fn) or [], tag="no_relax_control:" + name,
                              declared=_readonly_declaration(module, name, fn))
            if out["attempts"] == 0:
                continue
            measured += 1
            for key in total:
                total[key] += out[key]
            report.append("%s(a=%d,r=%d,l=%d,q=%d,p=%d,reasons=%s)"
                          % (name, out["attempts"], out["reject"], out["live"], out["query"],
                             out["phantom"], ",".join(out["caderror"][:1]) or "none"))
        self.assertGreater(measured, 0, "一个对照 op 都没量到")
        self.assertGreater(total["reject"], 0, "夹具没有该上下文，却连 reject 都没采到")
        self.assertEqual(total["live"], 0, "被放宽成 live 了：" + " | ".join(report))
        self.assertEqual(total["query"], 0, "被放宽成 query 了：" + " | ".join(report))
        self.assertEqual(total["phantom"], 0, "被放宽成 phantom 了：" + " | ".join(report))
        print("NO_RELAX_CONTROL fixture_context=absent n_ops=%d reject=%d live=%d query=%d "
              "phantom=%d ops=%s" % (measured, total["reject"], total["live"], total["query"],
                                     total["phantom"], " | ".join(report)))

    def test_kwarg_variants_prove_write_for_real_setter(self):
        """③(a) 扩容**正证**：真 setter ``grid_display_set`` 必须由 ``phantom`` 转 ``write``/``live``。"""
        hits = [(entry["stem"], rec) for entry in _results(SCOPE_MAIN)
                for rec in entry["ops"] if rec["op"] == "grid_display_set"]
        self.assertTrue(hits, "对照物缺席：主集合里没有 grid_display_set 的测量记录")
        print("KWARG_VARIANT_EXPANSION op=grid_display_set n=%d states=%s details=%s"
              % (len(hits), ",".join("%s:%s" % (stem, rec["state"]) for stem, rec in hits),
                 " | ".join(rec["detail"][:160] for _stem, rec in hits[:2])))
        bad = ["%s:%s" % (stem, rec["state"]) for stem, rec in hits
               if rec["state"] not in ("write", "live")]
        self.assertEqual(bad, [], "扩容后 grid_display_set 仍未转 write/live（没造出非幂等变体）")

    def test_noop_kwargs_setter_still_phantom(self):
        """③(b) 扩容**反证**：真 no-op 的 kwargs setter（临时夹具，跑完删）**仍判 phantom**。

        临时片自带同一套键域、**接受**同样的 kwarg，却一个字段也不改 ⇒ 必须照旧 ``phantom``；
        并断言这次真喂了**非空** kwargs（否则反证是空的）。证明"探针变强"≠"判据变松"。
        """
        doc = (_FIXTURE_CTX or {}).get("doc")
        if doc is None:
            self.skipTest("真夹具缺席：没有上下文可做反证")
        path = Path(_PROBE_TMP) / "dead_display_set__w99.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '"""临时夹具（跑完删）：接受键域 kwarg，却一个字段也不改 ⇒ 真 no-op。"""\n'
            '_SET_KEYS = frozenset({"enabled", "spacing_mm", "spacing_nm", "adaptive",\n'
            '                      "major_every", "min_px", "max_lines"})\n'
            'def dead_display_set(sk_or_doc, **kwargs):\n'
            '    del sk_or_doc, kwargs\n'
            '    return ["' + LINE_UUID + '"]\n', encoding="utf-8")
        try:
            spec = importlib.util.spec_from_file_location("dead_display_fixture", path)
            self.assertIsNotNone(spec, "临时夹具加载器缺席")
            module = importlib.util.module_from_spec(spec)
            sys.modules["dead_display_fixture"] = module
            spec.loader.exec_module(module)
            fn = module.dead_display_set
            variants = _variants(fn) or []
            self.assertTrue(any(v for v in variants), "探针一个 kwarg 变体都没造出来：反证不成立")
            out = _run_ladder(fn, doc, variants, tag="dead_display_fixture")
            print("KWARG_NOOP_CONTROL variants=%d nonempty=%d attempts=%d phantom=%d live=%d "
                  "query=%d write=%d"
                  % (len(variants), sum(1 for v in variants if v), out["attempts"], out["phantom"],
                     out["live"], out["query"], out.get("write", 0)))
            self.assertGreater(out["attempts"], 0, "一个变体都没跑到")
            self.assertGreater(out["phantom"], 0, "真 no-op setter 没被判 phantom")
            self.assertEqual(out["live"], 0, "真 no-op setter 被判 live：判据被放宽了")
            self.assertEqual(out.get("write", 0), 0, "真 no-op setter 被判 write：判据被放宽了")
        finally:
            path.unlink()

    def test_unittest_failure_forces_nonzero(self):
        for group_v in ("PASS", "NA", "FAIL"):
            with self.subTest(group=group_v):
                overall = _severity_verdict(group_v, "FAIL")   # unittest 红 ⇒ 至少 FAIL
                self.assertNotEqual(_rc_for_verdict(overall), 0)


if __name__ == "__main__":
    raise SystemExit(main())
