#!/usr/bin/env python3
"""annotation-extra 族模块：公差与配合标注 / 表面粗糙度符号 / 基线·连续标注批量入口。

族接口（与 `cadcore/ops/annotation.py` 冻结接口同源）
----------------------------------------------------
    FAMILY = "annotation_extra"
    OPERATIONS: dict[str, Callable]        # 操作名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 第一个位置参是草图/文档，其余关键字，返回 uuid 列表
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

本模块补的三块能力（**都是新增**，不改 `ops/annotation.py`）：

1. **公差与配合**：`add_tolerance`（±/上下偏差，整数纳米）、`add_fit_tolerance`
   （ISO 286 配合代号 `H7/g6`，由代号**确定性**推出配合类别 clearance/transition/interference）；
2. **表面粗糙度符号**：`add_surface_finish`（Ra 值整数纳米 + ISO 1302 符号形态 + 加工纹理 lay）；
3. **基线/连续标注批量入口**：`add_dimension_chain_batch` / `add_baseline_batch` /
   `add_continue_batch` —— 一次调用处理多组链，**整批先校验后写入**（任一条非法则一条都不写）。

口径（逐条与 `ops/annotation.py` 一致，不另立一套）
--------------------------------------------------
1. **长度一律整数纳米**：`upper_nm` / `lower_nm` / `value_nm` / `ra_nm` / `values_nm[]` 只允许
   `int`（`bool` 与浮点一律拒），拒绝时抛 `invalid_length` / `non_positive_length`；
2. **先校验后写入**：单个操作与批量操作都是"全部校验通过才动 `annotations[]`"，
   失败调用**不产生半条注解**（测试有原子性断言）；
3. **引用一律 `(uuid, sub_shape_name)`**：落 `target`，且必须能在草图里解析到实体，
   悬空 ⇒ `unknown_reference`；裸草图（只有 `entities`）没有 `annotations[]` ⇒ `annotation_needs_document`；
4. **落盘字段只用 L1 冻结的 annotation 字段集**（`uuid` / `target` / `kind` / `value_nm` /
   `text_key` / `literal` / `args`）：公差偏差、配合代号、符号形态、纹理都进 `args`，
   因此 `docmodel.check_invariants()` 不被本模块污染（自检与单测都有断言）；
5. **文案一律走 key**：`text_key` 必须是点分 ASCII 标识符；本模块**不含任何 locale 字面量**；
6. **不做猜测**：配合代号没有基准侧（既非孔 `H` 也非轴 `h`）⇒ `unsupported_fit` 显式报错，
   不静默当成某类配合；Ra 值超过 1 mm ⇒ `out_of_range_roughness`，不静默裁剪。

自检（文件自带，不依赖 pytest）：

    python3 -m cadcore.ops.annotation_extra
    # 末行：EXTRA_OPS verdict=PASS ops=6 checks=6/6

中文只出现在 docstring/注释里。
"""

from __future__ import annotations

import inspect
import os
import re
import sys
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

from ..errors import CadError
from ..sketch.constraints import UnknownReference, find_entity

__all__ = [
    "FAMILY", "OPERATIONS", "module_facts", "selftest", "main",
    "add_tolerance", "add_fit_tolerance", "add_surface_finish",
    "add_dimension_chain_batch", "add_baseline_batch", "add_continue_batch",
]

FAMILY = "annotation_extra"

#: i18n key 形状（与 `docmodel._KEY_LIKE_RE` 同源口径：点分 ASCII 标识符）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

#: ISO 286 公差等级 / 公差带代号：`IT7` 或 `<位置字母1~2><等级1~2位>`（如 `H7` / `g6` / `js6` / `zc8`）。
_TOLERANCE_CLASS_RE = re.compile(r"^(?:IT[0-9]{1,2}|[A-Za-z]{1,2}[0-9]{1,2})$")

#: 配合代号的一侧：位置字母（1~2 个）+ 等级（1~2 位）。
_FIT_SIDE_RE = re.compile(r"^([A-Za-z])([A-Za-z]?)([0-9]{1,2})$")

#: 配合代号整体：孔/轴（`H7/g6`）。
_FIT_RE = re.compile(r"^([A-Za-z]{1,2}[0-9]{1,2})/([A-Za-z]{1,2}[0-9]{1,2})$")

#: ISO 286 位置字母 -> 配合类别（未列入的字母 i/l/o/q/w 不是标准位置 ⇒ 显式报错）。
_POSITION_TYPES = (
    ("clearance", "abcdefgh"),
    ("transition", "jkmn"),
    ("interference", "prstuvxyz"),
)

#: ISO 1302 表面粗糙度符号形态。
_FINISH_SYMBOLS = ("basic", "removal_required", "removal_prohibited")

#: ISO 1302 加工纹理（lay）符号；`None` 落盘成 `"-"`（未指定）。
_LAY_SYMBOLS = ("none", "circular", "crossed", "multidirectional", "particulate", "radial",
                "pitch")

#: Ra 上限（1 mm = 1e6 nm）：超过即不是"粗糙度"量级 ⇒ 显式拒绝，不静默裁剪。
_RA_MAX_NM = 1000000

#: 链式标注角色（与 `ops/annotation.py` 的 `args["dim_role"]` 口径逐字一致）。
_CHAIN_ROLES = ("baseline", "continue")

#: 批量条目允许的键（多一个键就是拼错参数 ⇒ `bad_batch_entry`，不静默忽略）。
_CHAIN_ENTRY_KEYS = ("target", "values_nm", "text_key", "role")


# --------------------------------------------------------------------------------------
# 校验器（全部在写入之前跑完）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _uuid_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("bad_uuid", "%s must be a uuid string" % what)
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _fail("bad_uuid", "%s is not a uuid: %r" % (what, value)) from exc
    return value


def _nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（`bool` 与浮点一律拒；`positive` 时要求 > 0）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "%s must be an integer number of nanometres" % what)
    if positive and value <= 0:
        raise _fail("non_positive_length", "%s must be positive" % what)
    return value


def _nm_list(value: Any, what: str) -> list[int]:
    """长度序列入参 → 整数纳米列表（非空、逐项整数、逐项为正）。"""
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("invalid_length", "%s must be a non-empty list of nanometre integers" % what)
    return [_nm(item, "%s[%d]" % (what, index), positive=True)
            for index, item in enumerate(value)]


def _ref(value: Any, what: str) -> list[str]:
    """单个引用 → `[uuid, sub_shape_name]`（L1 §C）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_reference", "%s must be a (uuid, sub_shape_name) pair" % what)
    entity_uuid = _uuid_str(value[0], "%s uuid" % what)
    name = value[1]
    if not isinstance(name, str) or not name:
        raise _fail("bad_reference", "%s sub-shape name must be a non-empty string" % what)
    return [entity_uuid, name]


def _position(value: Any, what: str) -> list[int]:
    """坐标入参 → `[x_nm, y_nm]`（整数纳米对）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_position", "%s must be an (x_nm, y_nm) pair" % what)
    coords: list[int] = []
    for axis, item in zip(("x", "y"), value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("bad_position", "%s.%s must be an integer number of nanometres"
                        % (what, axis))
        coords.append(item)
    return coords


def _key(value: Any, what: str) -> str:
    """i18n key 入参 → 合法 key（人话文案不是 key）。"""
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise _fail("bad_text_key", "%s must be an i18n key (dotted ascii identifier)" % what)
    return value


# --------------------------------------------------------------------------------------
# 载荷访问（与 `ops/annotation.py` 同口径：裸草图明确报错，不静默丢弃）
# --------------------------------------------------------------------------------------


def _live_annotations(sk_or_doc: Any) -> list:
    """可写的 `annotations[]` 真源那一份（**不是**副本）。"""
    if isinstance(sk_or_doc, Mapping):
        annotations = sk_or_doc.get("annotations")
        if isinstance(annotations, list):
            return annotations
        if "entities" in sk_or_doc:
            raise _fail("annotation_needs_document",
                        "a bare sketch has no annotations[] to write; pass the document")
        raise _fail("bad_document", "document mapping has no annotations list")
    annotations = getattr(sk_or_doc, "annotations", None)
    if isinstance(annotations, list):
        return annotations
    raise _fail("bad_document", "unsupported sk_or_doc: %s" % type(sk_or_doc).__name__)


def _sketch_list(sk_or_doc: Any) -> list:
    if isinstance(sk_or_doc, Mapping):
        sketches = sk_or_doc.get("sketches")
        if isinstance(sketches, list) and sketches:
            return sketches
        if "entities" in sk_or_doc:
            return [sk_or_doc]
        return []
    sketches = getattr(sk_or_doc, "sketches", None)
    return list(sketches) if isinstance(sketches, list) else []


def _require_targets(sk_or_doc: Any, refs: list[list[str]]) -> list[list[str]]:
    """每个引用都要能在草图里解析到实体；悬空 ⇒ `CadError(reason="unknown_reference")`。"""
    sketches = _sketch_list(sk_or_doc)
    for ref in refs:
        resolved = None
        for sketch in sketches:
            if isinstance(sketch, Mapping):
                try:
                    resolved = find_entity(sketch, ref[0])
                except UnknownReference:
                    resolved = None
                if resolved is not None:
                    break
        if resolved is None:
            raise _fail("unknown_reference",
                        "reference %s does not resolve to an entity" % ref[0])
    return refs


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _append(sk_or_doc: Any, node: dict) -> list[str]:
    """写入一条注解并返回 `[uuid]`（调用前所有校验必须已经通过）。"""
    _live_annotations(sk_or_doc).append(node)
    return [node["uuid"]]


# --------------------------------------------------------------------------------------
# ISO 286 配合代号解析（纯函数：输入代号，输出确定性分类）
# --------------------------------------------------------------------------------------


def _position_type(letter: str) -> str | None:
    """位置字母 → 配合类别（大小写不敏感；非标准字母返回 `None`）。"""
    lowered = letter.lower()
    for name, letters in _POSITION_TYPES:
        if lowered in letters:
            return name
    return None


def _fit_side(token: str, label: str) -> dict[str, Any]:
    """配合代号的一侧 → `{position, letter, grade, grade_text}`（格式非法即报错）。"""
    match = _FIT_SIDE_RE.match(token)
    if match is None:
        raise _fail("bad_fit_code",
                    "%s side %r must look like <position letter(s)><grade>" % (label, token))
    first, second, grade_text = match.group(1), match.group(2), match.group(3)
    if _position_type(first) is None:
        raise _fail("bad_fit_code",
                    "%s position %r is not an ISO 286 position letter" % (label, first + second))
    return {"position": first + second, "letter": first, "grade": int(grade_text),
            "grade_text": grade_text}


def _parse_fit(fit: Any) -> dict[str, Any]:
    """`H7/g6` → 孔/轴代号 + **确定性**推出的配合类别与基准侧。

    判据（写死，不猜）：
    * 孔侧必须**大写**、轴侧必须**小写**（ISO 286 书写法），否则 `bad_fit_code`；
    * 必须有一侧是基准（孔 `H` 或轴 `h`），否则 `unsupported_fit`（不静默当成某类配合）；
    * 类别由**非基准侧**的位置字母决定；两侧都是基准（`H7/h6`）⇒ `clearance`。
    """
    if not isinstance(fit, str) or _FIT_RE.match(fit) is None:
        raise _fail("bad_fit_code", "fit must look like 'H7/g6' (hole/shaft)", got=repr(fit))
    hole_token, shaft_token = fit.split("/")
    hole = _fit_side(hole_token, "hole")
    shaft = _fit_side(shaft_token, "shaft")
    if not hole["letter"].isupper():
        raise _fail("bad_fit_code", "the hole side must use an upper-case position letter")
    if not shaft["letter"].islower():
        raise _fail("bad_fit_code", "the shaft side must use a lower-case position letter")
    hole_basis = hole["letter"] == "H"
    shaft_basis = shaft["letter"] == "h"
    if not (hole_basis or shaft_basis):
        raise _fail("unsupported_fit",
                    "a fit needs a basis side (hole 'H' or shaft 'h')")
    if hole_basis and shaft_basis:
        kind, basis, driver = "clearance", "both", "hole_and_shaft"
    elif hole_basis:
        kind, basis, driver = _position_type(shaft["letter"]), "hole", shaft_token
    else:
        kind, basis, driver = _position_type(hole["letter"]), "shaft", hole_token
    if kind is None:
        raise _fail("bad_fit_code", "position letter has no ISO 286 family")
    return {"fit": fit, "hole_code": hole_token, "shaft_code": shaft_token, "fit_kind": kind,
            "basis": basis, "driver": driver, "hole_grade": hole["grade"],
            "shaft_grade": shaft["grade"]}


# --------------------------------------------------------------------------------------
# 新增：公差与配合
# --------------------------------------------------------------------------------------


def add_tolerance(sk_or_doc, *, target, upper_nm, lower_nm, value_nm=None,
                  tolerance_class=None, text_key=None):
    """**尺寸公差注记**：`upper_nm >= 0`、`lower_nm <= 0`（整数纳米的上下偏差）。

    * `value_nm`（可选）是公称尺寸（正整数纳米）；不给就不落 `value_nm` 键（不写假 0）；
    * `tolerance_class`（可选）是代号 `IT7` / `H7` / `g6` / `js6`；
    * 上下偏差全零 ⇒ `degenerate_tolerance`（不含信息的注记不落库）；
      `upper_nm < 0` / `lower_nm > 0` / `upper_nm < lower_nm` ⇒ `bad_tolerance_range`。
    """
    ref = _ref(target, "target")
    upper = _nm(upper_nm, "upper_nm")
    lower = _nm(lower_nm, "lower_nm")
    if upper < 0 or lower > 0 or upper < lower:
        raise _fail("bad_tolerance_range",
                    "upper_nm must be >= 0, lower_nm <= 0 and upper_nm >= lower_nm")
    if upper == 0 and lower == 0:
        raise _fail("degenerate_tolerance",
                    "a tolerance with both deviations zero carries no information")
    nominal = None if value_nm is None else _nm(value_nm, "value_nm", positive=True)
    klass = "-"
    if tolerance_class is not None:
        if (not isinstance(tolerance_class, str)
                or _TOLERANCE_CLASS_RE.match(tolerance_class) is None):
            raise _fail("bad_tolerance_class",
                        "tolerance_class must look like 'IT7' or 'H7'")
        klass = tolerance_class
    _require_targets(sk_or_doc, [ref])
    node: dict[str, Any] = {"uuid": _new_uuid(), "target": ref, "kind": "tolerance",
                            "args": {"tol_upper_nm": upper, "tol_lower_nm": lower,
                                     "tol_class": klass}}
    if nominal is not None:
        node["value_nm"] = nominal
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


def add_fit_tolerance(sk_or_doc, *, target, fit, value_nm, text_key=None):
    """**配合标注**：`fit` 是 ISO 286 代号（`H7/g6`），`value_nm` 是公称尺寸（正整数纳米）。

    落盘 `args`：`fit` / `hole_code` / `shaft_code` / `fit_kind`
    （`clearance`|`transition`|`interference`）/ `basis`（`hole`|`shaft`|`both`）。
    """
    ref = _ref(target, "target")
    nominal = _nm(value_nm, "value_nm", positive=True)
    parsed = _parse_fit(fit)
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": "fit_tolerance", "value_nm": nominal,
            "args": {"fit": parsed["fit"], "hole_code": parsed["hole_code"],
                     "shaft_code": parsed["shaft_code"], "fit_kind": parsed["fit_kind"],
                     "basis": parsed["basis"]}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


# --------------------------------------------------------------------------------------
# 新增：表面粗糙度符号
# --------------------------------------------------------------------------------------


def add_surface_finish(sk_or_doc, *, target, ra_nm, symbol="basic", lay=None, at_nm=None,
                       text_key=None):
    """**表面粗糙度符号**（ISO 1302）：`ra_nm` 是 Ra 值（**整数纳米**，0 < ra_nm <= 1 mm）。

    * `symbol` ∈ `basic` / `removal_required` / `removal_prohibited`；
    * `lay` ∈ ISO 1302 纹理符号白名单，`None` 落盘成 `"-"`（未指定，不是"零"）；
    * `at_nm`（可选）是符号落点（整数纳米对）；
    * Ra 值落 `value_nm`（族口径：长度的量一律 `value_nm`），单位另记 `args["ra_unit"]="nm"`。
    """
    ref = _ref(target, "target")
    roughness = _nm(ra_nm, "ra_nm", positive=True)
    if roughness > _RA_MAX_NM:
        raise _fail("out_of_range_roughness",
                    "ra_nm must be <= 1 mm (1000000 nm)", ra_nm=ra_nm)
    if symbol not in _FINISH_SYMBOLS:
        raise _fail("bad_finish_symbol",
                    "symbol must be one of %s" % (", ".join(_FINISH_SYMBOLS),))
    if lay is not None and lay not in _LAY_SYMBOLS:
        raise _fail("bad_lay_symbol", "lay must be one of %s" % (", ".join(_LAY_SYMBOLS),))
    args: dict[str, Any] = {"symbol": symbol, "lay": lay if lay is not None else "-",
                            "ra_unit": "nm"}
    if at_nm is not None:
        args["at_nm"] = _position(at_nm, "at_nm")
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": "surface_finish",
            "value_nm": roughness, "args": args}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


# --------------------------------------------------------------------------------------
# 新增：基线 / 连续标注的批量入口（整批先校验后写入）
# --------------------------------------------------------------------------------------


def _chain_plan(entries: Any, default_role: str | None, what: str) -> list[tuple]:
    """批量条目 → 计划表（**只校验不写入**；任一条非法整批失败）。"""
    if not isinstance(entries, (list, tuple)) or not entries:
        raise _fail("empty_batch", "%s must be a non-empty list of entries" % what)
    plans: list[tuple] = []
    for index, entry in enumerate(entries):
        label = "%s[%d]" % (what, index)
        if not isinstance(entry, Mapping):
            raise _fail("bad_batch_entry", "%s must be a mapping" % label)
        unknown = sorted(str(key) for key in entry if key not in _CHAIN_ENTRY_KEYS)
        if unknown:
            raise _fail("bad_batch_entry",
                        "%s has unknown keys: %s" % (label, ",".join(unknown)))
        role = entry.get("role", default_role)
        if role not in _CHAIN_ROLES:
            raise _fail("bad_dimension_role",
                        "%s.role must be one of %s" % (label, ", ".join(_CHAIN_ROLES)))
        if default_role is not None and role != default_role:
            raise _fail("bad_dimension_role",
                        "%s.role=%r conflicts with %s" % (label, role, default_role))
        ref = _ref(entry.get("target"), "%s.target" % label)
        values = _nm_list(entry.get("values_nm"), "%s.values_nm" % label)
        if role == "baseline":
            for previous, current in zip(values, values[1:]):
                if current <= previous:
                    raise _fail("bad_dimension_order",
                                "%s.values_nm must be strictly increasing" % label)
        text_key = None
        if entry.get("text_key") is not None:
            text_key = _key(entry.get("text_key"), "%s.text_key" % label)
        plans.append((ref, values, role, text_key))
    return plans


def _commit_chain(sk_or_doc: Any, plans: list[tuple]) -> list[str]:
    """计划表 → 落盘（引用解析也在写入之前完成 ⇒ 失败不留半批）。"""
    _require_targets(sk_or_doc, [plan[0] for plan in plans])
    uuids: list[str] = []
    for batch_index, (ref, values, role, text_key) in enumerate(plans):
        for segment_index, value in enumerate(values):
            node: dict[str, Any] = {"uuid": _new_uuid(), "target": ref, "kind": "linear",
                                    "value_nm": value,
                                    "args": {"dim_role": role, "batch_index": batch_index,
                                             "segment_index": segment_index}}
            if text_key is not None:
                node["text_key"] = text_key
            uuids.extend(_append(sk_or_doc, node))
    return uuids


def add_dimension_chain_batch(sk_or_doc, *, entries):
    """**通用批量入口**：`entries` 是条目列表，每条自带 `role`（`baseline`|`continue`）。

    条目键：`target`（引用）/ `values_nm`（正整数纳米列表，baseline 要求严格递增）/
    `role` / 可选 `text_key`。返回**本次真正新建**的全部注解 uuid（按条目与段序）。
    """
    return _commit_chain(sk_or_doc, _chain_plan(entries, None, "entries"))


def add_baseline_batch(sk_or_doc, *, entries):
    """**基线标注批量入口**：每条 `values_nm` 是从同一基准量起的尺寸（严格递增）。

    条目可省略 `role`（强制 baseline）；显式写成别的角色 ⇒ `bad_dimension_role`。
    """
    return _commit_chain(sk_or_doc, _chain_plan(entries, "baseline", "entries"))


def add_continue_batch(sk_or_doc, *, entries):
    """**连续标注批量入口**：每条 `values_nm` 是相邻段长（逐项为正，不要求递增）。"""
    return _commit_chain(sk_or_doc, _chain_plan(entries, "continue", "entries"))


#: 操作名 -> 可调用（冻结接口：每个操作第一个位置参数是 `sk_or_doc`，其余走关键字）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "add_tolerance": add_tolerance,
    "add_fit_tolerance": add_fit_tolerance,
    "add_surface_finish": add_surface_finish,
    "add_dimension_chain_batch": add_dimension_chain_batch,
    "add_baseline_batch": add_baseline_batch,
    "add_continue_batch": add_continue_batch,
}


def module_facts() -> dict[str, Any]:
    """机读自述（测试与门用它，不解析 docstring）。"""
    return {"family": FAMILY, "ops": tuple(sorted(OPERATIONS)),
            "three_state": {"zero": 0, "unmeasured": -1, "not_applicable": "NA"}}


# --------------------------------------------------------------------------------------
# 自带 selftest（`python3 -m cadcore.ops.annotation_extra`）
# --------------------------------------------------------------------------------------


def _selftest_document() -> dict:
    """自证夹具：一页合法 L1（两直线 + 一圆 + 一拉伸特征，annotations 为空，长度全整数纳米）。"""
    def uid(index: int) -> str:
        return "%08x-0000-4000-8000-%012x" % (index, index)

    return {
        "schema_version": 1,
        "meta": {"doc_uuid": uid(100), "created_at": "2026-09-30T00:00:00Z",
                 "app_version": "0.1.0", "title_key": "doc.untitled"},
        "unit": "mm",
        "standard_profile": "iso",
        "projection_angle": None,
        "sketches": [{
            "uuid": uid(1),
            "plane": "XY",
            "entities": [
                {"uuid": uid(2), "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 400000, "y2": 0}},
                {"uuid": uid(3), "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": 300000}},
                {"uuid": uid(4), "kind": "circle", "layer": "0", "construction": False,
                 "geom": {"cx": 1000000, "cy": 250000, "r": 125000}},
            ],
            "constraints": [], "dimensions": [],
            "solved": {"dof": -1, "converged": False, "cache": True},
        }],
        "features": [{"uuid": uid(5), "kind": "extrude", "inputs": [uid(1)],
                      "params": {"distance_nm": 1000000, "symmetric": False, "angle_deg": 0.0,
                                 "op": "new"}, "suppressed": False}],
        "annotations": [],
        "sheets": [],
    }


def _selftest_refs(doc: dict) -> tuple[str, str]:
    entities = doc["sketches"][0]["entities"]
    return entities[0]["uuid"], entities[1]["uuid"]


def _node(doc: dict, annotation_uuid: str) -> dict:
    for item in doc["annotations"]:
        if item["uuid"] == annotation_uuid:
            return item
    raise AssertionError("annotation %s not found" % annotation_uuid)


def _count_ruler(op, doc: dict, **kwargs) -> bool:
    """计数尺子：返回的 uuid 个数必须等于 `annotations[]` 的实际增量（每次现读）。"""
    before = len(doc["annotations"])
    out = op(doc, **kwargs)
    return len(doc["annotations"]) - before == len(out)


def selftest() -> list[tuple[str, bool, str]]:
    """族自证：接口形状 / 公差数值 / 配合分类 / 粗糙度 / 批量与原子性 / 尺子反证。"""
    results: list[tuple[str, bool, str]] = []

    # 1) 接口形状：第一个位置参叫 sk_or_doc、其余 KEYWORD_ONLY、且以模块属性导出
    shape_ok = True
    exported = all(globals().get(name) is op for name, op in OPERATIONS.items())
    for name, op in OPERATIONS.items():
        params = list(inspect.signature(op).parameters.values())
        if (not params or params[0].name != "sk_or_doc"
                or params[0].kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD):
            shape_ok = False
        if any(param.kind is not inspect.Parameter.KEYWORD_ONLY for param in params[1:]):
            shape_ok = False
        if not name or not name.islower():
            shape_ok = False
    results.append(("interface_shape", bool(shape_ok and exported and FAMILY == "annotation_extra"),
                    "ops=%d exported=%d" % (len(OPERATIONS), exported)))

    # 2) 公差：整数纳米逐字落盘 + 计数语义
    doc1 = _selftest_document()
    line, _other = _selftest_refs(doc1)
    tol_uuid = add_tolerance(doc1, target=(line, "edge"), upper_nm=21000, lower_nm=-8000,
                             value_nm=400000, tolerance_class="IT7")[0]
    tol_node = _node(doc1, tol_uuid)
    results.append(("tolerance_values",
                    tol_node["value_nm"] == 400000
                    and tol_node["args"] == {"tol_upper_nm": 21000, "tol_lower_nm": -8000,
                                             "tol_class": "IT7"}
                    and tol_node["target"] == [line, "edge"]
                    and _count_ruler(add_tolerance, doc1, target=(line, "edge"),
                                     upper_nm=1000, lower_nm=-1000),
                    "value_nm=%r args=%r" % (tol_node.get("value_nm"), tol_node["args"])))

    # 3) 配合：代号 → 类别确定性映射（含阴性对照：非基准侧代号必须报错）
    doc2 = _selftest_document()
    ref2, _ = _selftest_refs(doc2)
    kinds = {}
    for code in ("H7/g6", "H7/k6", "H7/p6", "H7/h6", "P7/h6"):
        fit_uuid = add_fit_tolerance(doc2, target=(ref2, "edge"), fit=code, value_nm=30000000)[0]
        kinds[code] = _node(doc2, fit_uuid)["args"]["fit_kind"]
    unsupported_red = False
    try:
        add_fit_tolerance(doc2, target=(ref2, "edge"), fit="A7/g6", value_nm=30000000)
    except CadError as exc:
        unsupported_red = exc.reason == "unsupported_fit"
    results.append(("fit_classification",
                    kinds == {"H7/g6": "clearance", "H7/k6": "transition",
                              "H7/p6": "interference", "H7/h6": "clearance",
                              "P7/h6": "interference"} and unsupported_red,
                    "kinds=%s unsupported_red=%d" % (kinds, unsupported_red)))

    # 4) 粗糙度：Ra 整数纳米 + 符号/纹理 + 越界拒绝
    doc3 = _selftest_document()
    ref3, _ = _selftest_refs(doc3)
    finish_uuid = add_surface_finish(doc3, target=(ref3, "edge"), ra_nm=3200, lay="crossed",
                                     at_nm=(1000, -2000))[0]
    finish_node = _node(doc3, finish_uuid)
    out_of_range_red = False
    try:
        add_surface_finish(doc3, target=(ref3, "edge"), ra_nm=_RA_MAX_NM + 1)
    except CadError as exc:
        out_of_range_red = exc.reason == "out_of_range_roughness"
    results.append(("surface_finish_values",
                    finish_node["value_nm"] == 3200
                    and finish_node["args"] == {"symbol": "basic", "lay": "crossed",
                                                "ra_unit": "nm", "at_nm": [1000, -2000]}
                    and out_of_range_red,
                    "value_nm=%r args=%r out_of_range_red=%d"
                    % (finish_node["value_nm"], finish_node["args"], out_of_range_red)))

    # 5) 批量：条数/角色/序号 + 失败整批不写（原子性）
    doc4 = _selftest_document()
    ref4, other4 = _selftest_refs(doc4)
    batch = add_baseline_batch(doc4, entries=[
        {"target": (ref4, "edge"), "values_nm": [10000000, 25000000]},
        {"target": (other4, "edge"), "values_nm": [5000000, 15000000, 30000000]},
    ])
    roles = sorted({_node(doc4, item)["args"]["dim_role"] for item in batch})
    atomic_red = False
    before = len(doc4["annotations"])
    try:
        add_continue_batch(doc4, entries=[
            {"target": (ref4, "edge"), "values_nm": [1000000]},
            {"target": ("%08x-0000-4000-8000-%012x" % (999, 999), "edge"),
             "values_nm": [1000000]},
        ])
    except CadError as exc:
        atomic_red = exc.reason == "unknown_reference" and len(doc4["annotations"]) == before
    results.append(("batch_values_and_atomicity",
                    len(batch) == 5 and roles == ["baseline"]
                    and [item["args"]["segment_index"] for item in doc4["annotations"]] == [0, 1, 0, 1, 2]
                    and atomic_red,
                    "batch=%d roles=%s atomic_red=%d" % (len(batch), roles, atomic_red)))

    # 6) 尺子反证（变异体）：同一个计数尺子量变异体必须报红、量真件必须通过
    def mutant(sk_or_doc, *, target, values_nm):
        node = {"uuid": _new_uuid(), "target": _ref(target, "target"), "kind": "linear",
                "value_nm": values_nm[0], "args": {"dim_role": "continue"}}
        _live_annotations(sk_or_doc).append(node)
        _live_annotations(sk_or_doc).append(dict(node, uuid=_new_uuid()))
        return [node["uuid"]]

    doc_m = _selftest_document()
    doc_r = _selftest_document()
    ref_m, _ = _selftest_refs(doc_m)
    ref_r, _ = _selftest_refs(doc_r)
    mutant_red = not _count_ruler(mutant, doc_m, target=(ref_m, "edge"), values_nm=[1000000])
    real_green = _count_ruler(add_continue_batch, doc_r,
                              entries=[{"target": (ref_r, "edge"), "values_nm": [1000000]}])
    results.append(("count_ruler_sees_red", bool(mutant_red and real_green),
                    "mutant_red=%d real_green=%d" % (mutant_red, real_green)))

    return results


def main() -> int:
    """打印 CHECK 行与判定行 `EXTRA_OPS verdict=PASS ops=N`，返回进程退出码。"""
    results = selftest()
    for name, ok, detail in results:
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    failed = [name for name, ok, _detail in results if not ok]
    if failed:
        print("EXTRA_OPS verdict=FAIL ops=%d failed=%s"
              % (len(OPERATIONS), ",".join(failed)))
        return 1
    print("EXTRA_OPS verdict=PASS ops=%d checks=%d" % (len(OPERATIONS), len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
