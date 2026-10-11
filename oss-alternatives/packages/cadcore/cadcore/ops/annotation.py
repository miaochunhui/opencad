#!/usr/bin/env python3
"""annotation 族模块：尺寸 / 文字 / 引线 / 中心标记 → L1 `annotations[]`。

冻结接口（与 `cadcore/ops` 其余族同源）
--------------------------------------
    FAMILY = "annotation"
    OPERATIONS: dict[str, Callable]        # 操作名 -> 可调用
    op(sk_or_doc, **kwargs) -> list[str]   # 第一个位置参数是草图/文档，返回 uuid 列表
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)

口径
----
1. **长度一律整数纳米**：落 `*_nm` 键的值只允许 `int`（`bool` 不算数，浮点一律拒），
   拒绝时抛 `invalid_length` / `non_positive_length`；任何调用**先校验后写入**，不半写文档。
2. **角度不是长度**：入参按度给（允许实数），落盘 `value_nm` 存**整数微度**
   （`*_nm` 键的不变量要求整数），原文度数另存 `args["angle_deg"]`，单位写明
   `args["angle_unit"]="micro_deg"`。NaN / 无穷 / 越界 ⇒ `invalid_angle` / `out_of_range_angle`。
3. **引用一律 `(uuid, sub_shape_name)`**（L1 §C）：单引用落 `target`（二元组），
   角度尺寸是多引用 ⇒ `target` 存引用列表（`docmodel._check_refs` 同时接受这两种形状）。
   引用必须能在草图里解析到实体，悬空 ⇒ `unknown_reference`。
4. **文案一律走 key**：`text_key` 必须是点分 ASCII 标识符（人话文案不许当 key）；
   只有 `literal=True` 的注记才带人话文本，且按 L1 口径把 `text` 与它自己的 `literal`
   放在同一层（`args` 内），这样文本不变量尺子看见的是"这是字面量"而不是"漏了 key"。
5. 事后可改：`update_value` 只改值不动引用与条数；`remove` 只删点名的那一条。

本文件**不含任何 locale 字面量**（文案真源在 `cadcore.i18n.catalog`，本族只存 key）。
"""

from __future__ import annotations

import math
import re
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch.constraints import find_entity

__all__ = [
    "FAMILY", "OPERATIONS",
    "add_dimension", "add_aligned_dimension", "add_angular_dimension",
    "add_radial_dimension", "add_diameter_dimension",
    "add_text", "add_mtext", "add_leader", "add_center_mark",
    "add_baseline_dimension", "add_continue_dimension",
    "update_value", "remove",
]

FAMILY = "annotation"

#: i18n key 形状（与 `docmodel._KEY_LIKE_RE` 同源口径：点分 ASCII 标识符）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

#: `add_dimension` 的 `kind` 白名单（线尺寸的两种落盘口径）。
_LINEAR_KINDS = ("linear", "aligned")

#: 带数值的注解 kind（`update_value` 只对这些有意义）。
_VALUE_KINDS = ("linear", "aligned", "angular", "radial", "diameter")


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


def _micro_deg(value: Any, what: str) -> tuple[int, float]:
    """角度入参（度，允许实数）→ `(整数微度, 原度数)`。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_angle", "%s must be a finite number of degrees" % what)
    degrees = float(value)
    if not math.isfinite(degrees):
        raise _fail("invalid_angle", "%s must be finite" % what)
    micro = int(round(degrees * 1000000.0))
    if micro <= 0 or micro >= 360 * 1000000:
        raise _fail("out_of_range_angle", "%s must be in (0, 360) degrees" % what)
    return micro, degrees


def _ref(value: Any, what: str) -> list[str]:
    """单个引用 → `[uuid, sub_shape_name]`（L1 §C）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_reference", "%s must be a (uuid, sub_shape_name) pair" % what)
    entity_uuid = _uuid_str(value[0], "%s uuid" % what)
    name = value[1]
    if not isinstance(name, str) or not name:
        raise _fail("bad_reference", "%s sub-shape name must be a non-empty string" % what)
    return [entity_uuid, name]


def _ref_list(value: Any, what: str) -> list[list[str]]:
    """引用入参 → 引用列表（接受单个二元组或二元组的列表；空选择一律拒）。"""
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
        return [_ref(value, what)]
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("bad_reference", "%s must be a non-empty list of references" % what)
    return [_ref(item, "%s[%d]" % (what, index)) for index, item in enumerate(value)]


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
# 载荷访问（裸草图没有 annotations[] ⇒ 明确报错，不静默丢弃）
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
    """每个引用都要能在草图里解析到实体（悬空引用 ⇒ `unknown_reference`）。

    上游解析器 `find_entity` 对未知 uuid **抛异常**（`UnknownReference`，不是 `CadError`）
    ⇒ 这里必须收敛成族接口的 `CadError(reason="unknown_reference")`，
    否则上游异常会穿透冻结接口的错误契约。
    """
    from cadcore.sketch.constraints import UnknownReference

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


def _append(sk_or_doc: Any, node: dict) -> list[str]:
    """写入一条注解并返回 `[uuid]`（调用前所有校验必须已经通过）。"""
    _live_annotations(sk_or_doc).append(node)
    return [node["uuid"]]


def _new_uuid() -> str:
    return str(_uuid.uuid4())


# --------------------------------------------------------------------------------------
# 新增：尺寸
# --------------------------------------------------------------------------------------


def add_dimension(sk_or_doc, *, target, kind="linear", value_nm, text_key=None):
    """**线尺寸**：`target=(uuid, sub_shape_name)`，`value_nm` 是整数纳米长度。

    返回 `[annotation_uuid]`；`text_key` 缺省时注解只有数值（下游按 key 缺省表补文案）。
    """
    if kind not in _LINEAR_KINDS:
        raise _fail("bad_dimension_kind", "kind must be one of %s" % (", ".join(_LINEAR_KINDS),))
    ref = _ref(target, "target")
    length = _nm(value_nm, "value_nm", positive=True)
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": kind, "value_nm": length, "args": {}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


def add_aligned_dimension(sk_or_doc, *, target, value_nm, text_key=None):
    """**对齐尺寸**（与线尺寸同口径，`kind="aligned"`）。"""
    return add_dimension(sk_or_doc, target=target, kind="aligned", value_nm=value_nm,
                         text_key=text_key)


def _finite(value: Any) -> bool:
    """数值且有限（`bool` 不算数）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _ref_entity(sk_or_doc: Any, ref: list[str]) -> Any:
    """单个引用 → 草图实体（悬空 ⇒ `unknown_reference`，与 `_require_targets` 同一判据）。"""
    from cadcore.sketch.constraints import UnknownReference

    for sketch in _sketch_list(sk_or_doc):
        if isinstance(sketch, Mapping):
            try:
                found = find_entity(sketch, ref[0])
            except UnknownReference:
                found = None
            if found is not None:
                return found
    raise _fail("unknown_reference", "reference %s does not resolve to an entity" % ref[0])


def _geom_of(entity: Any) -> Mapping:
    """实体几何（认不出 ⇒ 空映射，由调用方判可否求角）。"""
    geom = entity.get("geom") if isinstance(entity, Mapping) else None
    return geom if isinstance(geom, Mapping) else {}


def _point_of(entity: Any, sub_shape: str) -> tuple[float, float] | None:
    """点引用 → 坐标：line 的 `start`/`end`/`mid`、circle 的 `center`、point 实体的 `(x, y)`。

    子形状名与实体种类对不上 ⇒ `None`，由调用方收敛成 `bad_reference`，
    不做"猜一个点"的静默降级。
    """
    geom = _geom_of(entity)
    name = str(sub_shape).lower()
    if all(_finite(geom.get(key)) for key in ("x1", "y1", "x2", "y2")):
        if name in ("start", "p1", "begin", "vertex0"):
            return (float(geom["x1"]), float(geom["y1"]))
        if name in ("end", "p2", "finish", "vertex1"):
            return (float(geom["x2"]), float(geom["y2"]))
        if name in ("mid", "midpoint", "center", "centre"):
            return ((float(geom["x1"]) + float(geom["x2"])) / 2.0,
                    (float(geom["y1"]) + float(geom["y2"])) / 2.0)
        return None
    if _finite(geom.get("cx")) and _finite(geom.get("cy")):
        if name in ("center", "centre", "mid", "midpoint", "point"):
            return (float(geom["cx"]), float(geom["cy"]))
        return None
    if _finite(geom.get("x")) and _finite(geom.get("y")):
        return (float(geom["x"]), float(geom["y"]))
    return None


def _direction_of(entity: Any) -> tuple[float, float] | None:
    """直线实体 → 方向向量；非直线 / 零长 ⇒ `None`。"""
    geom = _geom_of(entity)
    if not all(_finite(geom.get(key)) for key in ("x1", "y1", "x2", "y2")):
        return None
    dx = float(geom["x2"]) - float(geom["x1"])
    dy = float(geom["y2"]) - float(geom["y1"])
    if math.hypot(dx, dy) <= 1e-9:
        return None
    return (dx, dy)


def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """两向量夹角（度，取值 (0, 180)）。"""
    cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    return math.degrees(math.atan2(cross, dot))


def _solve_angle_deg(sk_or_doc: Any, refs: list[list[str]]) -> float | None:
    """**真求角**：两条直线（方向夹角）或三个点（顶点 = `refs[1]`）⇒ 度数。

    * 只有一条引用 ⇒ 返回 `None`：这组引用在几何上不定角，调用方保留声明度数；
    * 引用自相矛盾 / 退化（同一条引用两次、三点共点、三点共线、两线平行、引用多于三条、
      非直线实体参与两线夹角）⇒ `bad_reference` —— 这是本操作**唯一**该抛它的场合。
    """
    if len(refs) > 3:
        raise _fail("bad_reference", "an angular dimension takes at most three references")
    keys = [(ref[0], ref[1].lower()) for ref in refs]
    if len(set(keys)) != len(keys):
        raise _fail("bad_reference", "duplicate references do not define an angle")
    entities = [_ref_entity(sk_or_doc, ref) for ref in refs]
    if len(entities) == 1:
        return None
    if len(entities) == 2:
        directions = [_direction_of(entity) for entity in entities]
        if any(direction is None for direction in directions):
            raise _fail("bad_reference",
                        "two references must be straight entities to define an angle")
        degrees = _angle_between(directions[0], directions[1])
        if degrees <= 1e-9 or degrees >= 180.0 - 1e-9:
            raise _fail("bad_reference", "the two references are parallel; they define no angle")
        return degrees
    points = [_point_of(entity, refs[index][1]) for index, entity in enumerate(entities)]
    if any(point is None for point in points):
        raise _fail("bad_reference",
                    "three references must resolve to points (start/end/mid/center)")
    vertex = points[1]
    v1 = (points[0][0] - vertex[0], points[0][1] - vertex[1])
    v2 = (points[2][0] - vertex[0], points[2][1] - vertex[1])
    if math.hypot(v1[0], v1[1]) <= 1e-9 or math.hypot(v2[0], v2[1]) <= 1e-9:
        raise _fail("bad_reference", "the three-point angle has a zero-length ray")
    degrees = _angle_between(v1, v2)
    if degrees <= 1e-9 or degrees >= 180.0 - 1e-9:
        raise _fail("bad_reference", "the three references are collinear; they define no angle")
    return degrees


def add_angular_dimension(sk_or_doc, *, target, value_nm, text_key=None):
    """**角度标注**：`target` 是**两条直线**引用或**三个点**引用（`(uuid, sub_shape_name)`）。

    * **两条直线**：真求方向夹角（`atan2(|cross|, dot)`，取值 (0, 180)）；
    * **三个点**：`refs[1]` 是顶点，真求 `refs[0]`-顶点-`refs[2]` 的夹角；点引用口径
      = line 的 `start`/`end`/`mid`、circle 的 `center`、point 实体的 `(x, y)`；
    * **单条引用**：几何上不定角 ⇒ 采用调用方声明的度数；
    * 树上记 `args["angle_source"]`：`"solved"` = 几何真求、`"declared"` = 调用方声明 ——
      "声明"与"真求"可分辨，**声明值不许冒充求出来的角**；
    * 引用自相矛盾 / 退化 ⇒ `bad_reference`（同一条引用两次、三点共点、三点共线、两线平行、
      圆参与两线夹角、引用多于三条）。

    `value_nm` 按**度**给（实数；NaN / 越界先失败），落盘整数微度；单位口径与
    `update_value` 同为 `_micro_deg`（同一处实现）。
    """
    refs = _ref_list(target, "target")
    _require_targets(sk_or_doc, refs)
    micro, degrees = _micro_deg(value_nm, "value_nm")
    solved = _solve_angle_deg(sk_or_doc, refs)
    if solved is None:
        effective, source = degrees, "declared"
    else:
        micro, effective = _micro_deg(solved, "solved_angle")
        source = "solved"
    node = {"uuid": _new_uuid(), "target": refs, "kind": "angular", "value_nm": micro,
            "args": {"angle_deg": effective, "angle_unit": "micro_deg",
                     "angle_source": source}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


def add_radial_dimension(sk_or_doc, *, target, value_nm, text_key=None):
    """**半径尺寸**：`value_nm` 是整数纳米半径（必须为正）。"""
    ref = _ref(target, "target")
    radius = _nm(value_nm, "value_nm", positive=True)
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": "radial", "value_nm": radius, "args": {}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


def add_diameter_dimension(sk_or_doc, *, target, value_nm, text_key=None):
    """**直径尺寸**：`value_nm` 是整数纳米直径（必须为正）。"""
    ref = _ref(target, "target")
    diameter = _nm(value_nm, "value_nm", positive=True)
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": "diameter", "value_nm": diameter,
            "args": {}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


# --------------------------------------------------------------------------------------
# 新增：文字 / 引线 / 中心标记
# --------------------------------------------------------------------------------------


def add_text(sk_or_doc, *, at_nm, text_key=None, literal=False, text=None):
    """**文字注记**：`at_nm=(x_nm, y_nm)` 整数纳米坐标。

    * `literal=False`（缺省）：必须给 `text_key`（面向用户的文案走语言包）；
    * `literal=True`：给 `text`（人话字面量），并把 `literal` 与 `text` 同层落进 `args`，
      使文本不变量尺子判定为"显式字面量"而不是"漏了 key"。
    """
    position = _position(at_nm, "at_nm")
    if not isinstance(literal, bool):
        raise _fail("bad_flag", "literal must be a bool")
    node: dict[str, Any] = {"uuid": _new_uuid(), "kind": "text"}
    if literal:
        if not isinstance(text, str) or not text.strip():
            raise _fail("bad_literal_text", "literal=True needs a non-empty text")
        node["literal"] = True
        node["args"] = {"at_nm": position, "text": text, "literal": True}
    else:
        if text_key is None:
            raise _fail("missing_text_key", "a non-literal note needs an i18n text_key")
        node["text_key"] = _key(text_key, "text_key")
        node["literal"] = False
        node["args"] = {"at_nm": position}
    return _append(sk_or_doc, node)


def add_leader(sk_or_doc, *, target, at_nm, text_key=None):
    """**引线注记**：指向 `target`，箭头落点 `at_nm`。"""
    ref = _ref(target, "target")
    position = _position(at_nm, "at_nm")
    _require_targets(sk_or_doc, [ref])
    node = {"uuid": _new_uuid(), "target": ref, "kind": "leader",
            "args": {"at_nm": position}}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
    return _append(sk_or_doc, node)


def add_center_mark(sk_or_doc, *, target, at_nm):
    """**中心标记**：`at_nm` 是圆心坐标（整数纳米对）。"""
    ref = _ref(target, "target")
    position = _position(at_nm, "at_nm")
    _require_targets(sk_or_doc, [ref])
    return _append(sk_or_doc, {"uuid": _new_uuid(), "target": ref, "kind": "center_mark",
                               "args": {"at_nm": position}})


# --------------------------------------------------------------------------------------
# 事后可改（更新 / 删除）
# --------------------------------------------------------------------------------------


def _find_annotation(annotations: list, annotation_uuid: str) -> tuple[int, Any]:
    for index, item in enumerate(annotations):
        if isinstance(item, Mapping) and item.get("uuid") == annotation_uuid:
            return index, item
    raise _fail("unknown_annotation", "no annotation with uuid %s" % annotation_uuid)


def update_value(sk_or_doc, *, target, value_nm):
    """**只改数值**：`target` 是注解 uuid；引用与注解条数都不许动。

    单位口径与新增路径**逐字一致**（本模块口径 1/2 条）：线性/半径/直径的 `value_nm`
    是整数纳米；角度尺寸的入参按**度**给（允许实数），落盘仍是整数微度，且
    `args["angle_deg"]` / `args["angle_unit"]` **同步刷新** —— 只改 `value_nm`
    会把过期的度数原值留在树上（`add_angular_dimension` 写 90 度、`update_value(45)`
    之后 `angle_deg` 还写 90 的假账）。
    """
    annotation_uuid = _uuid_str(target, "target")
    annotations = _live_annotations(sk_or_doc)
    _index, item = _find_annotation(annotations, annotation_uuid)
    kind = item.get("kind")
    if kind not in _VALUE_KINDS:
        raise _fail("not_a_dimension",
                    "annotation %s (kind=%r) carries no dimension value"
                    % (annotation_uuid, kind))
    if kind == "angular":
        value, degrees = _micro_deg(value_nm, "value_nm")
        args = item.get("args")
        if not isinstance(args, dict):
            args = {}
            item["args"] = args
        args["angle_deg"] = degrees
        args["angle_unit"] = "micro_deg"
        # 事后改值是"声明值"，几何求解结果已被覆盖 ⇒ 来源标记同步刷成 declared；否则 add 时
        # 写的 "solved" 会继续冒充"这个角是求出来的"（与新增路径的 solved/declared 同一口径）。
        args["angle_source"] = "declared"
    else:
        value = _nm(value_nm, "value_nm", positive=True)
    item["value_nm"] = value
    return [annotation_uuid]


def remove(sk_or_doc, *, target):
    """**删除一条注解**：`target` 是注解 uuid；只删点名的这一条。"""
    annotation_uuid = _uuid_str(target, "target")
    annotations = _live_annotations(sk_or_doc)
    index, _item = _find_annotation(annotations, annotation_uuid)
    del annotations[index]
    return [annotation_uuid]


def add_mtext(sk_or_doc, *, at_nm, text_key=None, literal=False, text=None, height_nm=None):
    """**多行文字**：与 `add_text` 同口径（key 或 `literal=True`），另可给整数纳米字高 `height_nm`。

    多行由 `text` / 文案 key 自身表达；`height_nm` 走长度不变量（整数纳米、必须为正）。
    """
    position = _position(at_nm, "at_nm")
    if not isinstance(literal, bool):
        raise _fail("bad_flag", "literal must be a bool")
    args: dict[str, Any] = {"at_nm": position}
    if height_nm is not None:
        args["height_nm"] = _nm(height_nm, "height_nm", positive=True)
    node: dict[str, Any] = {"uuid": _new_uuid(), "kind": "mtext"}
    if literal:
        if not isinstance(text, str) or not text.strip():
            raise _fail("bad_literal_text", "literal=True needs a non-empty text")
        node["literal"] = True
        args["text"] = text
        args["literal"] = True
    else:
        if text_key is None:
            raise _fail("missing_text_key", "a non-literal note needs an i18n text_key")
        node["text_key"] = _key(text_key, "text_key")
        node["literal"] = False
    node["args"] = args
    return _append(sk_or_doc, node)


def _nm_list(value: Any, what: str) -> list[int]:
    """长度序列入参 → 整数纳米列表（非空、逐项整数、逐项为正；`bool`/浮点一律拒）。"""
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("invalid_length", "%s must be a non-empty list of nanometre integers" % what)
    return [_nm(item, "%s[%d]" % (what, index), positive=True)
            for index, item in enumerate(value)]


def _dimension_chain(sk_or_doc, target, values_nm, role, text_key):
    """基线/连续标注的共同真实现：逐段各落一条线性注解，全部引用**同一个**基准实体。"""
    ref = _ref(target, "target")
    values = _nm_list(values_nm, "values_nm")
    if role == "baseline":
        for previous, current in zip(values, values[1:]):
            if current <= previous:
                raise _fail("bad_dimension_order",
                            "baseline values_nm must be strictly increasing")
    _require_targets(sk_or_doc, [ref])
    uuids: list[str] = []
    for value in values:
        node = {"uuid": _new_uuid(), "target": ref, "kind": "linear", "value_nm": value,
                "args": {"dim_role": role}}
        if text_key is not None:
            node["text_key"] = _key(text_key, "text_key")
        uuids.extend(_append(sk_or_doc, node))
    return uuids


def add_baseline_dimension(sk_or_doc, *, target, values_nm, text_key=None):
    """**基线标注**：`values_nm` 是从同一基准量起的尺寸序列（整数纳米，严格递增）。"""
    return _dimension_chain(sk_or_doc, target, values_nm, "baseline", text_key)


def add_continue_dimension(sk_or_doc, *, target, values_nm, text_key=None):
    """**连续标注**：`values_nm` 是相邻段长序列（整数纳米，逐段一条链式注解）。"""
    return _dimension_chain(sk_or_doc, target, values_nm, "continue", text_key)


# ---- 关联标注：DIMREASSOCIATE / DIMREGEN（重关联 + 逐条重算） --------------------------------

_REASSOC_KINDS = ("linear", "aligned", "angular", "radial", "diameter")


def _assoc_target(value):
    """几何引用归一：实体 uuid 字符串，或 `[uuid, 子形状]` 引用对（取第一项）。"""
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_target", "target must be a uuid string or [uuid, sub_shape] reference")
    return value.strip()


def _reassoc_measure(sk_or_doc, target_uuid: str) -> int:
    """按目标实体的**当前几何**重算标注值（整数纳米）：line / circle / arc / polyline 各按各自口径。"""
    entities: list = []
    for sketch in _sketch_list(sk_or_doc):
        if isinstance(sketch, Mapping):
            found = sketch.get("entities")
            if isinstance(found, list):
                entities.extend(found)
    if not entities:
        raise _fail("no_entities", "reassociation needs a sketch/document carrying entities")
    node = next((e for e in entities
                 if isinstance(e, Mapping) and str(e.get("uuid")) == target_uuid), None)
    if node is None:
        raise _fail("unknown_target", "target is not a live entity")
    geom = node.get("geom") if isinstance(node.get("geom"), dict) else {}
    kind = str(node.get("kind") or "")
    if kind == "line":
        dx = int(geom.get("x2", 0)) - int(geom.get("x1", 0))
        dy = int(geom.get("y2", 0)) - int(geom.get("y1", 0))
        return int(round((dx * dx + dy * dy) ** 0.5))
    if kind == "circle":
        return 2 * int(geom.get("r", 0))
    if kind == "arc":
        radius = int(geom.get("r", 0))
        span = abs(float(geom.get("end_deg", 0.0)) - float(geom.get("start_deg", 0.0)))
        return int(round(radius * span * 0.017453292519943295))
    if kind == "polyline":
        points = geom.get("points") or []
        total = 0.0
        for index in range(1, len(points)):
            dx = int(points[index][0]) - int(points[index - 1][0])
            dy = int(points[index][1]) - int(points[index - 1][1])
            total += (dx * dx + dy * dy) ** 0.5
        return int(round(total))
    raise _fail("unmeasurable_target", "no measurement rule for this entity kind", kind=kind)


def dim_reassociate(sk_or_doc, *, target, kind="linear", value_nm=None, text_key=None):
    """**DIMREASSOCIATE / DIMREGEN：把关联标注重绑到几何引用并重算测量值。**

    - `target`：新的几何引用（实体 uuid，或 `[uuid, 子形状]` 引用对）；
    - `value_nm`：显式覆盖值（整数纳米）。缺省 ⇒ **按目标的当前几何重算**（见 `_reassoc_measure`）
      —— 这就是 DIMREGEN 的逐条重算语义：对每条关联标注调一次即全量重生；
    - 落盘一条**新的关联标注**（沿用本模块 `add_*` 的追加语义，不原地改旧节点；旧记录由调用方
      `remove` 显式删除 ⇒ 重关联既可审计也可回退），标注↔几何的关联关系写在节点的 `target` 上；
    - 返回新注解 uuid 列表；引用形状不对 / 重算路径上目标不是活实体、几何量不出来 ⇒ `CadError`。
    """
    target_uuid = _assoc_target(target)
    if not isinstance(kind, str) or kind not in _REASSOC_KINDS:
        raise _fail("bad_kind", "kind must be one of %s" % (_REASSOC_KINDS,))
    if value_nm is None:
        measured = _reassoc_measure(sk_or_doc, target_uuid)
    else:
        measured = _nm(value_nm, "value_nm", positive=True)
    args = {"target": target_uuid, "value_nm": measured}
    node = {"uuid": _new_uuid(), "kind": kind, "target": target_uuid,
            "value_nm": measured, "args": args}
    if text_key is not None:
        node["text_key"] = _key(text_key, "text_key")
        args["text_key"] = node["text_key"]
    return _append(sk_or_doc, node)


#: 操作名 -> 可调用（冻结接口：每个操作第一个位置参数是 `sk_or_doc`，其余走关键字）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "dim_reassociate": dim_reassociate,
    "add_dimension": add_dimension,
    "add_aligned_dimension": add_aligned_dimension,
    "add_angular_dimension": add_angular_dimension,
    "add_radial_dimension": add_radial_dimension,
    "add_diameter_dimension": add_diameter_dimension,
    "add_baseline_dimension": add_baseline_dimension,
    "add_continue_dimension": add_continue_dimension,
    "add_text": add_text,
    "add_mtext": add_mtext,
    "add_leader": add_leader,
    "add_center_mark": add_center_mark,
    "update_value": update_value,
    "remove": remove,
}


# --------------------------------------------------------------------------------------
# 自带 selftest（`python3 -m cadcore.ops.annotation`；与同族 ops 模块同口径）
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


def _selftest_refs(doc: dict) -> tuple[str, str, str]:
    entities = doc["sketches"][0]["entities"]
    return entities[0]["uuid"], entities[1]["uuid"], entities[2]["uuid"]


def _selftest_count_ruler(op, doc: dict, **kwargs) -> bool:
    """计数尺子：返回的 uuid 个数必须等于 `annotations[]` 的实际增量（**每次现读**，不用旧值）。"""
    before = len(doc["annotations"])
    out = op(doc, **kwargs)
    return len(doc["annotations"]) - before == len(out)


# --------------------------------------------------------------------------------------
# 样式 / 替代 / 编辑 / 表格 / 注释性比例（DIMSTYLE / DIMOVERRIDE / DIMEDIT·DIMTEDIT /
# STYLE / DDEDIT·FIND / MLEADERSTYLE / TABLE / TABLESTYLE / annotation scale）
#
# L1 承载：文档级**附加容器**（一律是列表，元素带 `uuid` ⇒ 每个操作都产出可探针、可回读的
# 操作实体）：`dim_styles[]` / `dim_overrides[]` / `dim_edits[]` / `text_styles[]` /
# `text_edits[]` / `mleader_styles[]` / `tables[]` / `table_styles[]` / `annotation_scales[]`。
#
# 每个操作**两条都做，缺一条就是假绿**：①落一条带 uuid 的记录（可追溯/可回读）；
# ②把记录真作用到已有注记的 `args[...]`（没有作用对象时只落记录，不假装改过谁）。
# 每条的 docstring 都写明 L1 里**装不下的部分（unsupported）**，不假装支持。
# --------------------------------------------------------------------------------------

#: 尺寸类注解 kind（标注样式/替代/文字编辑的作用域）。
_DIM_KINDS = _VALUE_KINDS
#: 文字类注解 kind（文字样式/查找替换的作用域）。
_TEXT_KINDS = ("text", "mtext")
#: 注释性比例名口径：只认 `num:den` 整数比（分数/英尺英寸口径 L1 不表达）。
_SCALE_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _doc_bucket(sk_or_doc: Any, key: str, *, create: bool = True) -> list:
    """文档级附加容器（`create=True` 时缺省新建）：页级 Mapping 文档或带该 list 字段的对象文档。"""
    if isinstance(sk_or_doc, Mapping):
        bucket = sk_or_doc.get(key)
        if isinstance(bucket, list):
            return bucket
        if create and bucket is None and "annotations" in sk_or_doc:
            created: list = []
            sk_or_doc[key] = created
            return created
        raise _fail("bad_document", "document has no writable %s list" % key)
    bucket = getattr(sk_or_doc, key, None)
    if isinstance(bucket, list):
        return bucket
    raise _fail("bad_document", "%s is not a writable list on %s"
                % (key, type(sk_or_doc).__name__))


def _bucket_or_empty(sk_or_doc: Any, key: str) -> list:
    """只读容器（缺席 ⇒ 空列表；读取侧绝不建键）。"""
    bucket = sk_or_doc.get(key) if isinstance(sk_or_doc, Mapping) else getattr(sk_or_doc, key, None)
    return bucket if isinstance(bucket, list) else []


def _args_of(node: dict) -> dict:
    """注解的 `args` 真源（缺省新建，**返回可写引用**）。"""
    args = node.get("args")
    if not isinstance(args, dict):
        args = {}
        node["args"] = args
    return args


def _style_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("bad_style_name", "%s must be a non-empty style name" % what)
    return value.strip()


def _count(value: Any, what: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_count", "%s must be an integer" % what)
    if value < minimum or (maximum is not None and value > maximum):
        raise _fail("invalid_count", "%s is out of range" % what)
    return value


def _ratio(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("bad_ratio", "%s must be a finite positive number" % what)
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise _fail("bad_ratio", "%s must be a finite positive number" % what)
    return number


def _text_value(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("bad_text", "%s must be a non-empty string" % what)
    return value


def _micro_angle(value: Any, what: str) -> int:
    """倾斜角入参 → 整数微度（与角度标注同一单位口径；范围 (-90°, 90°)，端点不含）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_angle", "%s must be an integer number of micro-degrees" % what)
    if not (-90 * 1000000 < value < 90 * 1000000):
        raise _fail("out_of_range_angle", "%s must be in (-90, 90) degrees" % what)
    return value


def _scale_name(value: Any) -> tuple[str, int, int]:
    """比例名 → `(规范名, 分子, 分母)`；只认 `num:den` 整数比。"""
    match = _SCALE_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise _fail("bad_scale_name", "scale name must look like '1:2' (integer num:den)")
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if numerator <= 0 or denominator <= 0:
        raise _fail("bad_scale_name", "scale name parts must be positive")
    return "%d:%d" % (numerator, denominator), numerator, denominator


def _require_annotations(sk_or_doc: Any, subject: Any, kinds: tuple | None, what: str) -> list:
    """`subject` → 注解节点列表（`None` = 文档里该 kind 的全部；单个 uuid / uuid 列表也可）。

    kinds 不匹配 ⇒ `wrong_annotation_kind`；uuid 不在文档里 ⇒ `unknown_annotation`。
    """
    annotations = _live_annotations(sk_or_doc)
    if subject is None:
        return [item for item in annotations
                if isinstance(item, Mapping) and (kinds is None or item.get("kind") in kinds)]
    wanted = subject if isinstance(subject, (list, tuple)) else [subject]
    if not wanted:
        raise _fail("bad_subject", "%s must name at least one annotation" % what)
    targets: list = []
    for item in wanted:
        target = _uuid_str(item, "%s uuid" % what)
        _index, node = _find_annotation(annotations, target)
        if kinds is not None and node.get("kind") not in kinds:
            raise _fail("wrong_annotation_kind",
                        "annotation %s (kind=%r) is outside %s"
                        % (target, node.get("kind"), what))
        targets.append(node)
    return targets


def _record(bucket: list, node: dict) -> list[str]:
    """落一条操作实体并返回 `[uuid]`（调用前所有校验必须已经通过）。"""
    bucket.append(node)
    return [node["uuid"]]


def dim_style(sk_or_doc, *, name="STANDARD", height_nm=2_500_000, arrow_size_nm=2_500_000,
              decimals=2, ext_gap_nm=625_000, subject=None):
    """**DIMSTYLE（标注样式管理器）**：定义/覆盖一个具名标注样式，并把它作用到尺寸注解。

    * 落盘：`dim_styles[]` 追加 `{uuid, kind:"dim_style", name, height_nm, arrow_size_nm,
      decimals, ext_gap_nm, is_current}`；此前**所有**旧记录（含同名覆盖）`is_current` 置
      `False` —— 样式表里只有一条 current（最近定义的那条），同名是覆盖、异名是切当前；
    * 作用：`subject` 点名的尺寸注解（`None` = 文档里全部尺寸注解）写 `args["dim_style"]=name`，
      **只写没有显式样式的注解** —— 已点名样式的注解不被静默改写；
    * 回读：`dim_style_of(sk_or_doc, subject=<尺寸注解 uuid>)` 按名字回读整条样式记录。

    L1 限制（unsupported）：标注线落点/箭头几何不在 L1 注记字段里（只有
    `target` / `kind` / `value_nm` / `args`）⇒ 样式只承载数值口径（字高/箭头尺寸/小数位/
    尺寸界线间隙），不重排图面几何。
    """
    style_name = _style_name(name, "name")
    height = _nm(height_nm, "height_nm", positive=True)
    arrow = _nm(arrow_size_nm, "arrow_size_nm", positive=True)
    gap = _nm(ext_gap_nm, "ext_gap_nm", positive=True)
    digits = _count(decimals, "decimals", minimum=0, maximum=8)
    targets = _require_annotations(sk_or_doc, subject, _DIM_KINDS, "subject")
    bucket = _doc_bucket(sk_or_doc, "dim_styles")
    for item in bucket:
        if isinstance(item, dict):
            item["is_current"] = False
    record = {"uuid": _new_uuid(), "kind": "dim_style", "name": style_name,
              "height_nm": height, "arrow_size_nm": arrow, "decimals": digits,
              "ext_gap_nm": gap, "is_current": True}
    for node in targets:
        _args_of(node).setdefault("dim_style", style_name)
    return _record(bucket, record)


def dim_override(sk_or_doc, *, subject=None, height_nm=None, arrow_size_nm=None,
                 override_text=None, decimals=None):
    """**DIMOVERRIDE（标注样式替代）**：把替代项真合并进尺寸注解的 `args["override"]`，
    并落一条 `dim_overrides[]` 记录（`applies_to` = 被替代的注解 uuid 列表、`overrides` = 替代项）。

    * 至少要给一个替代项，否则 `empty_override`；`subject=None` = 文档里全部尺寸注解；
    * 替代项：`height_nm` / `arrow_size_nm`（正整数纳米）、`decimals`（0–8）、
      `override_text`（非空文本）；重复调用按项**合并**（后写覆盖同名项），不整包替换。

    L1 限制（unsupported）：替代只做数据层合并，**不重排标注线/箭头几何**（L1 注记没有落点字段）；
    也不做"替代随样式改名自动重解"（无 DIMREGEN 语义）。
    """
    overrides: dict[str, Any] = {}
    if height_nm is not None:
        overrides["height_nm"] = _nm(height_nm, "height_nm", positive=True)
    if arrow_size_nm is not None:
        overrides["arrow_size_nm"] = _nm(arrow_size_nm, "arrow_size_nm", positive=True)
    if decimals is not None:
        overrides["decimals"] = _count(decimals, "decimals", minimum=0, maximum=8)
    if override_text is not None:
        overrides["text"] = _text_value(override_text, "override_text")
    if not overrides:
        raise _fail("empty_override", "dim_override needs at least one override value")
    targets = _require_annotations(sk_or_doc, subject, _DIM_KINDS, "subject")
    bucket = _doc_bucket(sk_or_doc, "dim_overrides")
    record = {"uuid": _new_uuid(), "kind": "dim_override", "applies_to": [],
              "overrides": dict(overrides)}
    for node in targets:
        args = _args_of(node)
        merged = dict(args.get("override") or {})
        merged.update(overrides)
        args["override"] = merged
        record["applies_to"].append(node["uuid"])
    return _record(bucket, record)


def dim_edit(sk_or_doc, *, subject=None, override_text=None, pos_nm=None, arrow_pos_nm=None):
    """**DIMEDIT / DIMTEDIT**：标注文字替代与文字/箭头位置（整数纳米坐标）真落到尺寸注解。

    * 落盘：`dim_edits[]` 追加 `{uuid, kind:"dim_edit", applies_to:[...], edit:{...}}`；
    * 作用：`args["text_override"]` / `args["text_pos_nm"]` / `args["arrow_pos_nm"]` 原地刷新；
    * 至少要给一项，否则 `empty_edit`；`subject=None` = 文档里全部尺寸注解。

    L1 限制（unsupported）：位置是**数据层坐标**（纳米对）；L1 没有标注线/引线几何求解器 ⇒
    不重算"文字沿标注线的对齐方式"，也不做夹点拖拽交互。
    """
    edit: dict[str, Any] = {}
    if override_text is not None:
        edit["text_override"] = _text_value(override_text, "override_text")
    if pos_nm is not None:
        edit["text_pos_nm"] = _position(pos_nm, "pos_nm")
    if arrow_pos_nm is not None:
        edit["arrow_pos_nm"] = _position(arrow_pos_nm, "arrow_pos_nm")
    if not edit:
        raise _fail("empty_edit", "dim_edit needs at least one edit value")
    targets = _require_annotations(sk_or_doc, subject, _DIM_KINDS, "subject")
    bucket = _doc_bucket(sk_or_doc, "dim_edits")
    record = {"uuid": _new_uuid(), "kind": "dim_edit", "applies_to": [], "edit": dict(edit)}
    for node in targets:
        _args_of(node).update(edit)
        record["applies_to"].append(node["uuid"])
    return _record(bucket, record)


def text_style(sk_or_doc, *, name="STANDARD", font="txt", width_factor=1.0, slant_micro=0,
               height_nm=None, subject=None):
    """**STYLE（文字样式）**：定义具名文字样式（字体/宽度因子/倾斜角/可选字高），作用到文字注解。

    * 落盘：`text_styles[]` 追加 `{uuid, kind:"text_style", name, font, width_factor,
      slant_micro, [height_nm], is_current}`；同名旧记录 `is_current` 置 `False`；
    * 作用：`subject` 点名的文字/多行文字注解（`None` = 全部）写 `args["text_style"]=name`，
      **只写没有显式样式的注解**；
    * 单位口径：`height_nm` 整数纳米；`slant_micro` 整数微度（与角度标注同一口径，
      范围 (-90°, 90°)）；`width_factor` 是**比值**（有限正数，不参与纳米整数不变量）。

    L1 限制（unsupported）：字体只落**名字**，不做字体文件解析/字形度量/缺字回退；
    字高不参与图面排版（L1 无出图排版层）。
    """
    style_name = _style_name(name, "name")
    font_name = _style_name(font, "font")
    factor = _ratio(width_factor, "width_factor")
    slant = _micro_angle(slant_micro, "slant_micro")
    height = _nm(height_nm, "height_nm", positive=True) if height_nm is not None else None
    targets = _require_annotations(sk_or_doc, subject, _TEXT_KINDS, "subject")
    bucket = _doc_bucket(sk_or_doc, "text_styles")
    for item in bucket:
        if isinstance(item, dict) and item.get("name") == style_name:
            item["is_current"] = False
    record = {"uuid": _new_uuid(), "kind": "text_style", "name": style_name, "font": font_name,
              "width_factor": factor, "slant_micro": slant, "is_current": True}
    if height is not None:
        record["height_nm"] = height
    for node in targets:
        _args_of(node).setdefault("text_style", style_name)
    return _record(bucket, record)


def find_replace(sk_or_doc, *, search=None, replacement="", scope="all"):
    """**DDEDIT / FIND（文字编辑与查找替换）**：对**显式字面量**文本做查找替换，并落事务记录。

    * 作用：`scope` ∈ `text` / `mtext` / `all` 限定的注解里，凡是 `args["text"]` 含 `search`
      的**原地改写**成替换后的文本（`str.replace`，全量替换）；
    * 落盘：`text_edits[]` 追加 `{uuid, kind:"text_edit", pattern, replacement, scope,
      matched:[注解 uuid...], replaced:[{uuid, before, after}...]}` ⇒ 每次 FIND 可追溯/可回读；
    * `search=None` 或空串：不匹配任何文本、**不改写任何注解**，但事务记录照落（`matched=[]`）；
    * **只改字面量**：走 i18n key 的注解（只有 `text_key`、没有 `args["text"]`）一律不动 ——
      人话文案的真源在语言包（`cadcore.i18n.catalog`），从这里改写会写脏语言包。

    L1 限制（unsupported）：不做正则/大小写/全词匹配选项，也不做"只替换当前视口/选择集"的范围语义。
    """
    if search is not None and not isinstance(search, str):
        raise _fail("bad_pattern", "search must be a string or None")
    if not isinstance(replacement, str):
        raise _fail("bad_pattern", "replacement must be a string")
    if scope not in ("text", "mtext", "all"):
        raise _fail("bad_scope", "scope must be one of text, mtext, all")
    pattern = search or ""
    kinds = {"text": ("text",), "mtext": ("mtext",), "all": _TEXT_KINDS}[scope]
    replaced: list[dict[str, Any]] = []
    for node in _live_annotations(sk_or_doc):
        if not isinstance(node, dict) or node.get("kind") not in kinds:
            continue
        args = node.get("args")
        if not isinstance(args, dict):
            continue
        text = args.get("text")
        if not pattern or not isinstance(text, str) or pattern not in text:
            continue
        after = text.replace(pattern, replacement)
        args["text"] = after
        replaced.append({"uuid": node["uuid"], "before": text, "after": after})
    bucket = _doc_bucket(sk_or_doc, "text_edits")
    record = {"uuid": _new_uuid(), "kind": "text_edit", "pattern": pattern or None,
              "replacement": replacement, "scope": scope,
              "matched": [item["uuid"] for item in replaced], "replaced": replaced}
    return _record(bucket, record)


def mleader_style(sk_or_doc, *, name="STANDARD", arrow_size_nm=2_500_000,
                  landing_gap_nm=1_000_000, char_height_nm=2_500_000, subject=None):
    """**MLEADERSTYLE（引线样式）**：定义具名引线样式（箭头尺寸/着陆间隙/字符高），作用到引线注解。

    * 落盘：`mleader_styles[]` 追加 `{uuid, kind:"mleader_style", name, arrow_size_nm,
      landing_gap_nm, char_height_nm, is_current}`；同名旧记录 `is_current` 置 `False`；
    * 作用：引线注解（`subject=None` = 全部）写 `args["mleader_style"]=name`，只写没有显式样式的。

    L1 限制（unsupported）：多引线段/内容块（MLEADER 的文字或块承载、多条引线集合）**没有承载对象** ——
    L1 只有单段 `leader`（`add_leader`：一个 `target` + 一个箭头落点）⇒ 样式只落上面三个数值口径。
    """
    style_name = _style_name(name, "name")
    arrow = _nm(arrow_size_nm, "arrow_size_nm", positive=True)
    gap = _nm(landing_gap_nm, "landing_gap_nm", positive=True)
    height = _nm(char_height_nm, "char_height_nm", positive=True)
    targets = _require_annotations(sk_or_doc, subject, ("leader",), "subject")
    bucket = _doc_bucket(sk_or_doc, "mleader_styles")
    for item in bucket:
        if isinstance(item, dict) and item.get("name") == style_name:
            item["is_current"] = False
    record = {"uuid": _new_uuid(), "kind": "mleader_style", "name": style_name,
              "arrow_size_nm": arrow, "landing_gap_nm": gap, "char_height_nm": height,
              "is_current": True}
    for node in targets:
        _args_of(node).setdefault("mleader_style", style_name)
    return _record(bucket, record)


def table(sk_or_doc, *, at_nm=None, rows=2, cols=2, row_height_nm=8_000_000,
          col_width_nm=25_000_000, title_key=None):
    """**TABLE（图纸表格/明细栏）**：在文档里真建一张表格实体（`tables[]`）。

    * 表实体：`{uuid, kind:"table", at_nm:[x_nm,y_nm], rows, cols, row_height_nm, col_width_nm,
      cells:[{uuid,row,col,text}...], args:{[title_key], [table_style]}}`；
    * `cells` 恰好 `rows × cols` 个、每格一个 uuid（明细栏/参数表的填值面）、`(row, col)` 全覆盖；
    * `title_key` 走 i18n key 口径（点分 ASCII 标识符），人话文案不许当 key。

    L1 限制（unsupported）：单元格文本内容/合并单元格/自动填充（BOM 取数）不在本 op ——
    本 op 只建表结构；表格只落数据层，不出图纸（L1 无图面实体）。
    """
    position = _position(at_nm, "at_nm") if at_nm is not None else [0, 0]
    n_rows = _count(rows, "rows")
    n_cols = _count(cols, "cols")
    row_h = _nm(row_height_nm, "row_height_nm", positive=True)
    col_w = _nm(col_width_nm, "col_width_nm", positive=True)
    args: dict[str, Any] = {}
    if title_key is not None:
        args["title_key"] = _key(title_key, "title_key")
    cells = [{"uuid": _new_uuid(), "row": row, "col": col, "text": None}
             for row in range(n_rows) for col in range(n_cols)]
    node = {"uuid": _new_uuid(), "kind": "table", "at_nm": position, "rows": n_rows,
            "cols": n_cols, "row_height_nm": row_h, "col_width_nm": col_w, "cells": cells,
            "args": args}
    return _record(_doc_bucket(sk_or_doc, "tables"), node)


def table_style(sk_or_doc, *, name="STANDARD", font_style="STANDARD", grid_line_nm=250_000,
                header_height_nm=8_000_000, data_height_nm=6_000_000, subject=None):
    """**TABLESTYLE（表格样式）**：定义具名表格样式（字体样式引用/网格线宽/表头与数据行高），
    作用到表格实体 `args["table_style"]`。

    * 落盘：`table_styles[]` 追加 `{uuid, kind:"table_style", name, font_style, grid_line_nm,
      header_height_nm, data_height_nm, is_current}`；同名旧记录 `is_current` 置 `False`；
    * 作用：`subject` 点名的表格（`None` = `tables[]` 全部）写 `args["table_style"]=name`，
      只写没有显式样式的表格；点名的 uuid 不在 `tables[]` ⇒ `unknown_table`；
    * `font_style` 是**文字样式名引用**（读取侧按名字解析；本 op 不做存在性校验 ——
      文字样式允许在表格之后定义）。

    L1 限制（unsupported）：单元格边框逐格样式/背景填充/文字对齐没有承载字段。
    """
    style_name = _style_name(name, "name")
    font_name = _style_name(font_style, "font_style")
    grid = _nm(grid_line_nm, "grid_line_nm", positive=True)
    header_h = _nm(header_height_nm, "header_height_nm", positive=True)
    data_h = _nm(data_height_nm, "data_height_nm", positive=True)
    tables = [item for item in _bucket_or_empty(sk_or_doc, "tables") if isinstance(item, dict)]
    if subject is None:
        targets = list(tables)
    else:
        wanted = subject if isinstance(subject, (list, tuple)) else [subject]
        if not wanted:
            raise _fail("bad_subject", "subject must name at least one table")
        targets = []
        for item in wanted:
            target = _uuid_str(item, "subject uuid")
            node = next((t for t in tables if t.get("uuid") == target), None)
            if node is None:
                raise _fail("unknown_table", "no table with uuid %s" % target)
            targets.append(node)
    bucket = _doc_bucket(sk_or_doc, "table_styles")
    for item in bucket:
        if isinstance(item, dict) and item.get("name") == style_name:
            item["is_current"] = False
    record = {"uuid": _new_uuid(), "kind": "table_style", "name": style_name,
              "font_style": font_name, "grid_line_nm": grid,
              "header_height_nm": header_h, "data_height_nm": data_h, "is_current": True}
    for node in targets:
        _args_of(node).setdefault("table_style", style_name)
    return _record(bucket, record)


def annotation_scale(sk_or_doc, *, name="1:1", subject=None, paper_height_nm=2_500_000,
                     annotative=True):
    """**注释性比例（annotation scale）—— L1 数据层真实现；渲染层 unsupported（见下）**。

    * 落盘：`annotation_scales[]` 追加 `{uuid, kind:"annotation_scale", name:"1:2", scale_num,
      scale_den, paper_height_nm, model_height_nm, annotative, is_current}`；
      同名旧记录 `is_current` 置 `False`；
    * 作用：`subject` 点名的注解（`None` = 文档里全部注解）写 `args["annotative"]`、
      `args["annotation_scales"]`（比例名有序去重集合）、`args["model_height_nm"]`
      （= `paper_height_nm × den / num`，**向下取整到整数纳米**）；
      `annotative=False` 时只写 `annotative=False` 并清空比例集合；
    * 回读：`annotation_scale_of(sk_or_doc, subject=<注解 uuid>)`。

    L1 限制（unsupported，**不假装支持**）：图纸空间视口下"按视口比例自动缩放/自动显隐、
    同一对象多比例重复显示"需要**图纸空间视口对象**，L1 文档模型没有这种对象
    （`ops/output.py` 的 `viewport_scale` 是布局视口比例，与注释性对象比例不是一件事）
    ⇒ 本 op 只落数据层（annotative 标记 + 比例集合 + 模型/图纸尺寸换算），不做图面重排与显隐。
    """
    scale_label, numerator, denominator = _scale_name(name)
    paper = _nm(paper_height_nm, "paper_height_nm", positive=True)
    if not isinstance(annotative, bool):
        raise _fail("bad_flag", "annotative must be a bool")
    model_height = (paper * denominator) // numerator
    targets = _require_annotations(sk_or_doc, subject, None, "subject")
    bucket = _doc_bucket(sk_or_doc, "annotation_scales")
    for item in bucket:
        if isinstance(item, dict) and item.get("name") == scale_label:
            item["is_current"] = False
    record = {"uuid": _new_uuid(), "kind": "annotation_scale", "name": scale_label,
              "scale_num": numerator, "scale_den": denominator, "paper_height_nm": paper,
              "model_height_nm": model_height, "annotative": annotative, "is_current": True}
    for node in targets:
        args = _args_of(node)
        args["annotative"] = annotative
        if annotative:
            names = {item for item in (args.get("annotation_scales") or [])
                     if isinstance(item, str)}
            names.add(scale_label)
            args["annotation_scales"] = sorted(names)
            args["model_height_nm"] = model_height
        else:
            args["annotation_scales"] = []
    return _record(bucket, record)


def style_record(sk_or_doc, *, bucket, name):
    """按名字回读一条样式记录（`dim_styles` / `text_styles` / `mleader_styles` / `table_styles`）。

    **同名是覆盖**（族里所有样式的同一个口径，见 `dim_style` docstring）：桶里可以有同一名字的
    多条历史记录，回读必须给**生效的那条**（`is_current=True`），而不是桶里第一条 —— 否则
    "重定义后回读旧定义"（B6 缺陷 RC-S1）。`is_current` 都没有（外来/手写文档）时取**最后一条**
    （与"后写覆盖先写"同序），仍取不到才返回 `None`（读侧不编一条默认样式出来）。
    """
    matches = [item for item in _bucket_or_empty(sk_or_doc, bucket)
               if isinstance(item, Mapping) and item.get("name") == name]
    if not matches:
        return None
    current = [item for item in matches if item.get("is_current")]
    return dict((current or matches)[-1])


def dim_style_of(sk_or_doc, *, subject):
    """尺寸注解的生效样式记录（`args["dim_style"]` 名字 → `dim_styles[]`）；没点名样式 ⇒ None。"""
    _index, node = _find_annotation(_live_annotations(sk_or_doc), _uuid_str(subject, "subject"))
    args = node.get("args")
    name = args.get("dim_style") if isinstance(args, Mapping) else None
    if not isinstance(name, str):
        return None
    return style_record(sk_or_doc, bucket="dim_styles", name=name)


def annotation_scale_of(sk_or_doc, *, subject):
    """注解的注释性口径回读：`{annotative, scales, model_height_nm}`；未标注释性 ⇒ None。"""
    _index, node = _find_annotation(_live_annotations(sk_or_doc), _uuid_str(subject, "subject"))
    args = node.get("args")
    if not isinstance(args, Mapping) or not args.get("annotative"):
        return None
    return {"annotative": True, "scales": list(args.get("annotation_scales") or []),
            "model_height_nm": args.get("model_height_nm")}


__all__ = __all__ + ["dim_style", "dim_override", "dim_edit", "text_style", "find_replace",
                     "mleader_style", "table", "table_style", "annotation_scale",
                     "style_record", "dim_style_of", "annotation_scale_of"]

OPERATIONS.update({
    "dim_style": dim_style,
    "dim_override": dim_override,
    "dim_edit": dim_edit,
    "text_style": text_style,
    "find_replace": find_replace,
    "mleader_style": mleader_style,
    "table": table,
    "table_style": table_style,
    "annotation_scale": annotation_scale,
})


# --------------------------------------------------------------------------------------
# 自带 selftest（`python3 -m cadcore.ops.annotation`；与同族 ops 模块同口径）
# --------------------------------------------------------------------------------------


def _selftest_style_table_scale_ops() -> list[tuple[str, bool, str]]:
    """第 7 组自证：样式/替代/编辑/表格/注释性比例九条新操作 —— **记录 ∧ 作用**两条都量。"""
    checks: list[tuple[str, bool, str]] = []
    doc = _selftest_document()
    line_h, _line_v, circle = _selftest_refs(doc)
    dim = add_dimension(doc, target=(line_h, "edge"), value_nm=400000)[0]
    note = add_text(doc, at_nm=(1000, 2000), literal=True, text="NOTE-1")[0]
    body = add_mtext(doc, at_nm=(3000, 4000), literal=True, text="NOTE-1 body", height_nm=2500)[0]
    keyed = add_text(doc, at_nm=(5000, 6000), text_key="cmd.dimension.linear")[0]
    leader = add_leader(doc, target=(circle, "edge"), at_nm=(7000, 8000))[0]
    nodes = {item["uuid"]: item for item in doc["annotations"]}

    style_uuid = dim_style(doc, name="ISO-25", height_nm=2500000, arrow_size_nm=1800000,
                           decimals=3)[0]
    record = {item["uuid"]: item for item in doc["dim_styles"]}[style_uuid]
    checks.append(("dim_style_record_and_assignment",
                   record["height_nm"] == 2500000 and record["decimals"] == 3
                   and record["is_current"] is True
                   and nodes[dim]["args"]["dim_style"] == "ISO-25"
                   and dim_style_of(doc, subject=dim)["name"] == "ISO-25",
                   "name=%s height_nm=%s assigned=%s"
                   % (record["name"], record["height_nm"], nodes[dim]["args"].get("dim_style"))))

    override_uuid = dim_override(doc, subject=dim, height_nm=3000000, override_text="2x")[0]
    over = {item["uuid"]: item for item in doc["dim_overrides"]}[override_uuid]
    checks.append(("dim_override_record_and_merge",
                   over["applies_to"] == [dim]
                   and nodes[dim]["args"]["override"] == {"height_nm": 3000000, "text": "2x"},
                   "applies_to=%s override=%s"
                   % (over["applies_to"], nodes[dim]["args"]["override"])))

    edit_uuid = dim_edit(doc, subject=dim, override_text="EQ", pos_nm=(1500, 2500),
                         arrow_pos_nm=(3500, 4500))[0]
    edit = {item["uuid"]: item for item in doc["dim_edits"]}[edit_uuid]
    checks.append(("dim_edit_record_and_positions",
                   edit["applies_to"] == [dim]
                   and nodes[dim]["args"]["text_pos_nm"] == [1500, 2500]
                   and nodes[dim]["args"]["arrow_pos_nm"] == [3500, 4500]
                   and nodes[dim]["args"]["text_override"] == "EQ",
                   "pos=%s arrow=%s"
                   % (nodes[dim]["args"]["text_pos_nm"], nodes[dim]["args"]["arrow_pos_nm"])))

    text_style(doc, name="NOTES", font="simplex", width_factor=0.8, slant_micro=15000)
    ts = style_record(doc, bucket="text_styles", name="NOTES")
    checks.append(("text_style_record_and_assignment",
                   ts is not None and ts["width_factor"] == 0.8 and ts["slant_micro"] == 15000
                   and nodes[note]["args"]["text_style"] == "NOTES"
                   and nodes[body]["args"]["text_style"] == "NOTES"
                   and "text_style" not in nodes[dim]["args"],
                   "record=%s note=%s"
                   % (ts is not None, nodes[note]["args"].get("text_style"))))

    find_uuid = find_replace(doc, search="NOTE-1", replacement="NOTE-2")[0]
    tx = {item["uuid"]: item for item in doc["text_edits"]}[find_uuid]
    checks.append(("find_replace_rewrites_literals_only",
                   nodes[note]["args"]["text"] == "NOTE-2"
                   and nodes[body]["args"]["text"] == "NOTE-2 body"
                   and tx["matched"] == [note, body]
                   and tx["replaced"][0] == {"uuid": note, "before": "NOTE-1", "after": "NOTE-2"}
                   and "text" not in nodes[keyed]["args"],
                   "matched=%d keyed_untouched=%d"
                   % (len(tx["matched"]), "text" not in nodes[keyed]["args"])))

    mleader_style(doc, name="ML-ISO", char_height_nm=2500000)
    checks.append(("mleader_style_record_and_assignment",
                   style_record(doc, bucket="mleader_styles", name="ML-ISO") is not None
                   and nodes[leader]["args"]["mleader_style"] == "ML-ISO",
                   "leader_style=%s" % nodes[leader]["args"].get("mleader_style")))

    table_uuid = table(doc, at_nm=(0, 0), rows=3, cols=4, title_key="cmd.dimension.linear")[0]
    table_node = {item["uuid"]: item for item in doc["tables"]}[table_uuid]
    cells = table_node["cells"]
    checks.append(("table_builds_rows_times_cols_cells",
                   table_node["kind"] == "table" and table_node["rows"] == 3
                   and table_node["cols"] == 4 and len(cells) == 12
                   and len({cell["uuid"] for cell in cells}) == 12
                   and {(cell["row"], cell["col"]) for cell in cells}
                   == {(row, col) for row in range(3) for col in range(4)},
                   "rows=%s cols=%s cells=%d"
                   % (table_node["rows"], table_node["cols"], len(cells))))

    table_style(doc, name="TB-ISO", font_style="NOTES")
    checks.append(("table_style_record_and_assignment",
                   style_record(doc, bucket="table_styles", name="TB-ISO") is not None
                   and table_node["args"]["table_style"] == "TB-ISO",
                   "table_style=%s" % table_node["args"].get("table_style")))

    scale_uuid = annotation_scale(doc, name="1:2", paper_height_nm=2500000)[0]
    scale = {item["uuid"]: item for item in doc["annotation_scales"]}[scale_uuid]
    reading = annotation_scale_of(doc, subject=dim)
    checks.append(("annotation_scale_data_layer",
                   scale["scale_num"] == 1 and scale["scale_den"] == 2
                   and scale["model_height_nm"] == 5000000
                   and nodes[dim]["args"]["annotative"] is True
                   and nodes[dim]["args"]["annotation_scales"] == ["1:2"]
                   and reading["model_height_nm"] == 5000000,
                   "model_height_nm=%s scales=%s"
                   % (scale["model_height_nm"], nodes[dim]["args"]["annotation_scales"])))

    # 边界：具体 reason + 失败不许半写（先记桶深，失败后必须逐桶不变）
    buckets = ("dim_overrides", "dim_edits", "tables", "annotation_scales", "text_edits")
    depth_before = {key: len(_bucket_or_empty(doc, key)) for key in buckets}
    reasons: list[str] = []
    for bad in (
        lambda: dim_override(doc, subject="00000000-0000-4000-8000-0000000009ff", height_nm=1000),
        lambda: dim_override(doc, subject=dim),
        lambda: dim_edit(doc, subject=dim),
        lambda: table(doc, rows=0, cols=2),
        lambda: annotation_scale(doc, name="one-to-two"),
        lambda: annotation_scale(doc, name="1:2", paper_height_nm=0),
        lambda: find_replace(doc, scope="viewport"),
        lambda: find_replace(doc, search=7),
    ):
        try:
            bad()
            reasons.append("NO_ERROR")
        except CadError as exc:
            reasons.append(exc.reason)
    expected = ["unknown_annotation", "empty_override", "empty_edit", "invalid_count",
                "bad_scale_name", "non_positive_length", "bad_scope", "bad_pattern"]
    depth_after = {key: len(_bucket_or_empty(doc, key)) for key in buckets}
    checks.append(("style_op_errors_and_atomicity",
                   reasons == expected and depth_before == depth_after,
                   "reasons=%s buckets=%s" % (",".join(reasons), depth_after)))

    # 阴性对照：只落记录、不作用的变异体必须被"作用"这把尺子判红（对照件同时判绿）
    def mutant_dim_style(sk_or_doc, *, name="MUTANT"):
        bucket = _doc_bucket(sk_or_doc, "dim_styles")
        node = {"uuid": _new_uuid(), "kind": "dim_style", "name": name, "is_current": True}
        bucket.append(node)
        return [node["uuid"]]

    doc_m = _selftest_document()
    dim_m = add_dimension(doc_m, target=(_selftest_refs(doc_m)[0], "edge"), value_nm=400000)[0]
    mutant_dim_style(doc_m, name="ISO-25")
    mutant_node = {item["uuid"]: item for item in doc_m["annotations"]}[dim_m]
    mutant_red = mutant_node["args"].get("dim_style") != "ISO-25"
    real_green = nodes[dim]["args"].get("dim_style") == "ISO-25"
    checks.append(("style_assignment_ruler_sees_red", bool(mutant_red and real_green),
                   "mutant_red=%d real_green=%d" % (mutant_red, real_green)))
    return checks


def selftest() -> list[tuple[str, bool, str]]:
    """族自证：注册表形状 / 计数语义 / 数值与单位口径 / 全操作烟测 / 错误与原子性 / 尺子反证
    / 样式·替代·编辑·表格·注释性比例（记录 ∧ 作用）。"""
    results: list[tuple[str, bool, str]] = []

    # 1) 注册表形状：名字是机器可读 token、可调用、且以模块属性导出（契约同源）
    exported = all(globals().get(name) is op for name, op in OPERATIONS.items())
    names_ok = all(name and not name.startswith("_")
                   and all(ch.islower() or ch.isdigit() or ch == "_" for ch in name)
                   for name in OPERATIONS)
    results.append(("registry_shape",
                    bool(OPERATIONS) and names_ok and exported
                    and all(callable(op) for op in OPERATIONS.values()),
                    "ops=%d" % len(OPERATIONS)))

    # 2) 线性尺寸：精确整数纳米 + `(uuid, sub_shape_name)` 引用 + **每次现读**的条数增量
    doc = _selftest_document()
    line_h, line_v, circle = _selftest_refs(doc)
    counts: list[int] = []
    uuids: list[str] = []
    for ref, value in ((line_h, 400000), (line_v, 300000), (circle, 250000)):
        before = len(doc["annotations"])
        uuids.extend(add_dimension(doc, target=(ref, "edge"), kind="linear", value_nm=value,
                                   text_key="cmd.dimension.linear"))
        counts.append(len(doc["annotations"]) - before)  # 现读，绝不复用 before 当总数
    by_uuid = {item["uuid"]: item for item in doc["annotations"]}
    head = by_uuid[uuids[0]]
    results.append(("linear_counts_and_values",
                    counts == [1, 1, 1] and len(doc["annotations"]) == 3
                    and len(set(uuids)) == 3 and type(head["value_nm"]) is int
                    and head["value_nm"] == 400000 and head["target"] == [line_h, "edge"],
                    "counts=%s value_nm=%s target=%s"
                    % (counts, head["value_nm"], head["target"])))

    # 3) 角度尺寸：入参按度给、落整数微度；事后更新走同一单位口径且 args 不许留旧账
    angular = add_angular_dimension(doc, target=[(line_h, "edge"), (line_v, "edge")],
                                    value_nm=90.0, text_key="cmd.dimension.angular")[0]
    node = {item["uuid"]: item for item in doc["annotations"]}[angular]
    added = (type(node["value_nm"]) is int and node["value_nm"] == 90 * 1000000
             and node["args"]["angle_deg"] == 90.0 and node["args"]["angle_unit"] == "micro_deg")
    update_value(doc, target=angular, value_nm=45.0)
    node = {item["uuid"]: item for item in doc["annotations"]}[angular]
    updated = (type(node["value_nm"]) is int and node["value_nm"] == 45 * 1000000
               and node["args"]["angle_deg"] == 45.0 and node["args"]["angle_unit"] == "micro_deg"
               and len(doc["annotations"]) == 4)
    results.append(("angular_micro_deg_add_and_update", bool(added and updated),
                    "add_ok=%d update_ok=%d value_nm=%s angle_deg=%s"
                    % (added, updated, node["value_nm"], node["args"]["angle_deg"])))

    # 4) 全操作烟测：OPERATIONS 每个操作都被真调用过一次，且条数/唯一性在更新与删除后自洽
    doc2 = _selftest_document()
    h2, v2, c2 = _selftest_refs(doc2)
    calls = (
        ("add_dimension", lambda: add_dimension(doc2, target=(h2, "edge"), value_nm=400000)),
        ("add_aligned_dimension",
         lambda: add_aligned_dimension(doc2, target=(h2, "edge"), value_nm=400000)),
        ("add_angular_dimension",
         lambda: add_angular_dimension(doc2, target=[(h2, "edge"), (v2, "edge")], value_nm=90.0)),
        ("add_radial_dimension",
         lambda: add_radial_dimension(doc2, target=(c2, "edge"), value_nm=125000)),
        ("add_diameter_dimension",
         lambda: add_diameter_dimension(doc2, target=(c2, "edge"), value_nm=250000)),
        ("add_baseline_dimension",
         lambda: add_baseline_dimension(doc2, target=(h2, "edge"), values_nm=[10, 20, 30])),
        ("add_continue_dimension",
         lambda: add_continue_dimension(doc2, target=(h2, "edge"), values_nm=[10, 20, 30])),
        ("add_text", lambda: add_text(doc2, at_nm=(12000, 34000),
                                      text_key="cmd.dimension.linear")),
        ("add_mtext", lambda: add_mtext(doc2, at_nm=(12000, 34000), literal=True,
                                        text="NOTE-1", height_nm=2500)),
        ("add_leader", lambda: add_leader(doc2, target=(c2, "edge"), at_nm=(12000, 34000),
                                          text_key="cmd.dimension.radial")),
        ("add_center_mark",
         lambda: add_center_mark(doc2, target=(c2, "edge"), at_nm=(1000000, 250000))),
    )
    smoke: list[str] = []
    for name, fn in calls:
        smoke.extend(fn())
    # 后落的九条样式/替代/编辑/表格/注释性比例操作也必须被这次烟测真调用
    # （不上表 = 漏测；`exercised == set(OPERATIONS)` 这条断言会当场报红，不是静默放过）
    extra_calls = (
        ("dim_style", lambda: dim_style(doc2, name="ISO-25", height_nm=2500000, decimals=3)),
        ("dim_override", lambda: dim_override(doc2, subject=smoke[0], height_nm=3000000,
                                              override_text="2x")),
        ("dim_edit", lambda: dim_edit(doc2, subject=smoke[0], pos_nm=(1000, 2000),
                                      arrow_pos_nm=(3000, 4000))),
        ("text_style", lambda: text_style(doc2, name="NOTES", font="simplex",
                                          width_factor=0.8, slant_micro=15000)),
        ("find_replace", lambda: find_replace(doc2, search="NOTE-1", replacement="NOTE-2")),
        ("mleader_style", lambda: mleader_style(doc2, name="ML-ISO", char_height_nm=2500000)),
        ("table", lambda: table(doc2, at_nm=(0, 0), rows=3, cols=4)),
        ("table_style", lambda: table_style(doc2, name="TB-ISO", font_style="NOTES")),
        ("annotation_scale", lambda: annotation_scale(doc2, name="1:2",
                                                      paper_height_nm=2500000)),
        # 重关联走**缺省重算**路径（不给 value_nm）：量到的必须是那条 40 万 nm 直线的当前长度
        ("dim_reassociate", lambda: dim_reassociate(doc2, target=(h2, "edge"))),
    )
    for name, fn in extra_calls:
        smoke.extend(fn())
    calls = calls + extra_calls
    update_value(doc2, target=smoke[0], value_nm=400001)
    removed = remove(doc2, target=smoke[1])
    exercised = {name for name, _fn in calls} | {"update_value", "remove"}
    results.append(("all_operations_smoke",
                    exercised == set(OPERATIONS) and len(smoke) == 25
                    and len(set(smoke)) == 25 and removed == [smoke[1]]
                    and len(doc2["annotations"]) == 15,
                    "called=%d/%d annotations=%d"
                    % (len(exercised), len(OPERATIONS), len(doc2["annotations"]))))

    # 5) 错误与原子性：具体 reason + 失败的调用不许半写文档
    doc3 = _selftest_document()
    h3, _v3, c3 = _selftest_refs(doc3)
    reasons: list[str] = []
    for bad in (
        lambda: add_dimension(doc3, target=(h3, "edge"), value_nm=400000.5),
        lambda: add_angular_dimension(doc3, target=[(h3, "edge"), (h3, "edge")],
                                     value_nm=float("nan")),
        lambda: add_dimension(doc3, target=("00000000-0000-4000-8000-0000000009ff", "edge"),
                              value_nm=1),
        lambda: add_radial_dimension(doc3, target=(c3, "edge"), value_nm=-125000),
        lambda: add_text(doc3, at_nm=(1, 2)),
    ):
        try:
            bad()
            reasons.append("NO_ERROR")
        except CadError as exc:
            reasons.append(exc.reason)
    results.append(("errors_and_atomicity",
                    reasons == ["invalid_length", "invalid_angle", "unknown_reference",
                                "non_positive_length", "missing_text_key"]
                    and len(doc3["annotations"]) == 0,
                    "reasons=%s annotations=%d" % (",".join(reasons), len(doc3["annotations"]))))

    # 6) 尺子反证（变异体）：同一个计数尺子量变异体必须报红、量真件必须通过
    def mutant(sk_or_doc, *, target, value_nm):  # 变异体：写两条却只报一条 uuid
        node = {"uuid": _new_uuid(), "target": _ref(target, "target"), "kind": "linear",
                "value_nm": value_nm, "args": {}}
        _live_annotations(sk_or_doc).append(node)
        _live_annotations(sk_or_doc).append(dict(node, uuid=_new_uuid()))
        return [node["uuid"]]

    doc_m = _selftest_document()
    doc_r = _selftest_document()
    mutant_red = not _selftest_count_ruler(mutant, doc_m,
                                           target=(_selftest_refs(doc_m)[0], "edge"),
                                           value_nm=400000)
    real_green = _selftest_count_ruler(add_dimension, doc_r,
                                       target=(_selftest_refs(doc_r)[0], "edge"), value_nm=400000)
    results.append(("count_ruler_sees_red", bool(mutant_red and real_green),
                    "mutant_red=%d real_green=%d" % (mutant_red, real_green)))

    # 7) 样式/替代/编辑/表格/注释性比例：记录 ∧ 作用 两条都量 + 边界 + 阴性对照
    results.extend(_selftest_style_table_scale_ops())

    return results


def _main() -> int:
    results = selftest()
    for name, ok, detail in results:
        print("CHECK %s ok=%d %s" % (name, 1 if ok else 0, detail))
    failed = [name for name, ok, _detail in results if not ok]
    if failed:
        print("ANNOTATION_OPS verdict=FAIL ops=%d failed=%s"
              % (len(OPERATIONS), ",".join(failed)))
        return 1
    print("ANNOTATION_OPS verdict=PASS ops=%d checks=%d" % (len(OPERATIONS), len(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
