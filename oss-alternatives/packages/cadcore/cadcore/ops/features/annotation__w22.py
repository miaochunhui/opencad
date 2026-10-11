#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""annotation 族切片 w22：**第 13 号**缺口 = 「关联标注、重关联与更新」的真实现。

清单编号（真源 = docs/research/cad-parity/status/autocad-annotation.json；本族 must_have 22 条里
仍 missing/partial 的只有 4 条，按清单顺序编号 0..N-1，N=4）：

    0. 线性标注（水平/垂直/旋转）        partial  —— 「旋转」子型缺
    1. 关联标注、重关联与更新            partial  —— 引用在、但几何变动后无重关联/重解/重生成入口
    2. 多行文字                          partial  —— 无段落/堆叠/符号字段
    3. 多重引线                          partial  —— 无多引线集合/内容块承载

取模：13 % 4 = 1 ⇒ **本席做第 1 号「关联标注、重关联与更新」**（不是第 0 号线性、也不是第 2 号多行文字）。
注：features/annotation.json 在本仓不存在（`docs/research/cad-parity/features/` 下无该件），
故真源只取 status 件；此点已在收尾报告里如实写明，未编造第二份清单。

真实现（不是桩）
----------------
本模块在 L1 文档模型上补三个 op，全部有真几何后果：

* `dim_regen(sk_or_doc, *, target)`：按注解 uuid 找尺寸注解，**从活几何重新求解**数值并回写
  `value_nm` 与来源标记（`args["value_source"]="solved"`、`args["solved_from"]="geometry"`；
  角度尺寸同步刷 `args["angle_deg"]`/`args["angle_source"]="solved"`）。求不出（悬空引用、
  单引用的角度尺寸、无半径的实体、零长）**一律抛 CadError**，绝不写半截。
* `dim_reassociate(sk_or_doc, *, target, reference)`：把既有尺寸改绑到新引用 `(uuid, sub_shape_name)`
  或引用列表，**先解析新引用 + 先解出数值，再落盘**（校验失败时文档零改动），落盘同时记
  `args["reassociated_from"]`（旧主引用 uuid）。
* `dim_regen_all(sk_or_doc)`：批量重解全族尺寸；返回**数值真发生变化**的注解 uuid（只刷来源标记
  不算变化），不可解的条目原值保留且**不进返回列表**（"没算"不许冒充"算过"）。

求解口径（逐 kind，全部读活实体几何）
------------------------------------
* `radial` → 被引实体半径；`diameter` → 半径 × 2（认 `geom` 的 `r`/`radius`/`radius_nm`/`rad`，
  或 `d`/`diameter` 折半）；
* `linear`/`aligned` → 单引用直线实体的长度（`x1,y1→x2,y2`），或两条点引用（`start`/`end`/`mid`/
  `center`）之间的距离；
* `angular` → 两条直线引用求方向夹角，或三个点引用求顶点夹角（与族模块同口径：单引用不定角）。

冻结接口
--------
    FAMILY = "annotation"
    OPERATIONS: dict[str, Callable]      # op(sk_or_doc, **kwargs) -> list[str]
    非法参数 / 悬空引用 / 不可解 -> cadcore.errors.CadError(reason=<机器可读 token>)

口径纪律：长度一律整数纳米（`value_nm` 为正整数），角度入参按度、落盘整数微度；
本文件**不含任何 locale 字面量**（面向用户的文案走 `cadcore.i18n.catalog`），中文只在 docstring 里。

自检：`python3 cadcore/ops/features/annotation__w22.py`（真建文档 + 真改几何 + 真断言）。
"""

from __future__ import annotations

import math
import os
import sys
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

if __package__ in (None, ""):  # 以脚本方式直接运行时补包上下文（本目录没有 __init__.py，不越权写）
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = _here
    while True:
        if os.path.isfile(os.path.join(_root, "cadcore", "__init__.py")):
            break
        _parent = os.path.dirname(_root)
        if _parent == _root:
            break
        _root = _parent
    if _root not in sys.path:
        sys.path.insert(0, _root)

from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "dim_regen", "dim_reassociate", "dim_regen_all"]

FAMILY = "annotation"

#: 带数值的尺寸 kind（与族模块 annotation.py 的 `_VALUE_KINDS` 同口径）。
_VALUE_KINDS = ("linear", "aligned", "angular", "radial", "diameter")

#: 半径字段的认读顺序（L1 真源是整数纳米；认不出 ⇒ 由调用方收敛成 unsolvable_dimension）。
_RADIUS_KEYS = ("r", "radius", "radius_nm", "rad")
_DIAMETER_KEYS = ("d", "diameter", "diameter_nm")

#: 圆几何的直径字段（半径兜底）；点引用的子形状别名（与族模块 `_point_of` 同口径）。
_CENTER_NAMES = ("center", "centre", "mid", "midpoint", "point")


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """族接口错误契约：机器可读 `reason` + 人话 message（下游不许解析文案）。"""
    return CadError(message, reason=reason, **details)


def _get(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        return node.get(key, default)
    return getattr(node, key, default)


def _uuid_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("bad_uuid", "%s must be a non-empty uuid string" % what)
    return value


def _as_ref(value: Any, what: str) -> list[str]:
    """单个引用 → `[uuid, sub_shape_name]`（L1 §C：禁下标引用）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_reference", "%s must be a (uuid, sub_shape_name) pair" % what)
    entity_uuid = _uuid_str(value[0], "%s uuid" % what)
    name = value[1]
    if not isinstance(name, str) or not name:
        raise _fail("bad_reference", "%s sub_shape_name must be a non-empty string" % what)
    return [entity_uuid, name]


def _as_refs(value: Any, what: str) -> list[list[str]]:
    """引用入参 → 引用列表（接受单个二元组或二元组列表；空选择一律拒）。"""
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
        return [_as_ref(value, what)]
    if not isinstance(value, (list, tuple)) or not value:
        raise _fail("bad_reference", "%s must be a non-empty reference list" % what)
    return [_as_ref(item, "%s[%d]" % (what, index)) for index, item in enumerate(value)]


def _annotations(sk_or_doc: Any) -> list:
    """L1 文档的 `annotations[]`（裸草图没有这个面 ⇒ 显式报错，不静默建一份）。"""
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


def _sketches(sk_or_doc: Any) -> list:
    if isinstance(sk_or_doc, Mapping):
        sketches = sk_or_doc.get("sketches")
        if isinstance(sketches, list) and sketches:
            return sketches
        if "entities" in sk_or_doc:
            return [sk_or_doc]
        return []
    sketches = getattr(sk_or_doc, "sketches", None)
    return list(sketches) if isinstance(sketches, list) else []


def _entities(sketch: Any) -> list:
    entities = _get(sketch, "entities")
    if isinstance(entities, list):
        return entities
    if isinstance(entities, Mapping):
        return list(entities.values())
    return []


def _find_entity(sk_or_doc: Any, entity_uuid: str) -> Any:
    """按 uuid 解析实体（悬空 ⇒ `unknown_reference`；禁"猜一个"）。"""
    for sketch in _sketches(sk_or_doc):
        for entity in _entities(sketch):
            if _get(entity, "uuid") == entity_uuid:
                return entity
    raise _fail("unknown_reference", "reference %s does not resolve to an entity" % entity_uuid)


def _find_annotation(annotations: list, annotation_uuid: str) -> dict:
    for item in annotations:
        if isinstance(item, Mapping) and item.get("uuid") == annotation_uuid:
            if not isinstance(item, dict):
                raise _fail("bad_document", "annotation %s is not a mutable mapping"
                            % annotation_uuid)
            return item
    raise _fail("unknown_annotation", "no annotation with uuid %s" % annotation_uuid)


def _geom(entity: Any) -> Mapping:
    geom = _get(entity, "geom")
    return geom if isinstance(geom, Mapping) else {}


def _num(value: Any) -> bool:
    """数值且有限（`bool` 不算数）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _radius_nm(entity: Any) -> int | None:
    """活实体半径（整数纳米）；认不出 ⇒ `None`（不静默给 0）。"""
    geom = _geom(entity)
    for key in _RADIUS_KEYS:
        value = geom.get(key)
        if _num(value) and float(value) > 0:
            return int(round(float(value)))
    for key in _DIAMETER_KEYS:
        value = geom.get(key)
        if _num(value) and float(value) > 0:
            return int(round(float(value) / 2.0))
    return None


def _point_of(entity: Any, sub_shape: str) -> tuple[float, float] | None:
    """点引用 → 坐标：line 的 `start`/`end`/`mid`、circle 的 `center`、point 实体的 `(x, y)`。"""
    geom = _geom(entity)
    name = str(sub_shape).lower()
    if all(_num(geom.get(key)) for key in ("x1", "y1", "x2", "y2")):
        if name in ("start", "p1", "begin", "vertex0"):
            return (float(geom["x1"]), float(geom["y1"]))
        if name in ("end", "p2", "finish", "vertex1"):
            return (float(geom["x2"]), float(geom["y2"]))
        if name in ("mid", "midpoint", "center", "centre"):
            return ((float(geom["x1"]) + float(geom["x2"])) / 2.0,
                    (float(geom["y1"]) + float(geom["y2"])) / 2.0)
        return None
    if _num(geom.get("cx")) and _num(geom.get("cy")):
        if name in _CENTER_NAMES:
            return (float(geom["cx"]), float(geom["cy"]))
        return None
    if _num(geom.get("x")) and _num(geom.get("y")):
        return (float(geom["x"]), float(geom["y"]))
    return None


def _direction_of(entity: Any) -> tuple[float, float] | None:
    """直线实体 → 方向向量；非直线 / 零长 ⇒ `None`。"""
    geom = _geom(entity)
    if not all(_num(geom.get(key)) for key in ("x1", "y1", "x2", "y2")):
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


def _target_refs(node: Mapping) -> list[list[str]]:
    return _as_refs(node.get("target"), "target")


def _solve_angle_deg(sk_or_doc: Any, refs: list[list[str]],
                     entities: list[Any]) -> float | None:
    """真求角：两条直线（方向夹角）或三个点（顶点 = `refs[1]`）；单引用 ⇒ `None`。"""
    if len(refs) > 3:
        raise _fail("bad_reference", "an angular dimension takes at most three references")
    keys = [(ref[0], ref[1].lower()) for ref in refs]
    if len(set(keys)) != len(keys):
        raise _fail("bad_reference", "duplicate references do not define an angle")
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


def _positive_nm(value: float, what: str) -> int:
    rounded = int(round(value))
    if rounded <= 0:
        raise _fail("unsolvable_dimension",
                    "%s solved to a non-positive length from the live geometry" % what)
    return rounded


def _solve_value(sk_or_doc: Any, node: Mapping) -> dict[str, Any]:
    """按注解现引用**从活几何**求解数值 → `{"value_nm": int, "args": {...}}`。

    任何不可解情形抛 `CadError`（不返回 None、不写半截），
    reason ∈ {not_a_dimension, unknown_reference, unsolvable_dimension, bad_reference}。
    """
    kind = node.get("kind")
    if kind not in _VALUE_KINDS:
        raise _fail("not_a_dimension",
                    "annotation kind %r carries no dimension value" % (kind,))
    refs = _target_refs(node)
    entities = [_find_entity(sk_or_doc, ref[0]) for ref in refs]

    if kind in ("radial", "diameter"):
        if len(refs) != 1:
            raise _fail("bad_reference", "a %s dimension takes exactly one reference" % kind)
        radius = _radius_nm(entities[0])
        if radius is None:
            raise _fail("unsolvable_dimension",
                        "the referenced entity exposes no radius (kind=%s)" % kind, kind=kind)
        value = radius if kind == "radial" else 2 * radius
        return {"value_nm": value,
                "args": {"value_source": "solved", "solved_from": "geometry"}}

    if kind == "angular":
        degrees = _solve_angle_deg(sk_or_doc, refs, entities)
        if degrees is None:
            raise _fail("unsolvable_dimension",
                        "a single reference does not define an angle; keep the declared value",
                        kind=kind)
        micro = int(round(degrees * 1000000.0))
        if micro <= 0 or micro >= 360 * 1000000:
            raise _fail("bad_solved_angle", "solved angle is out of (0, 360) degrees")
        return {"value_nm": micro,
                "args": {"angle_deg": degrees, "angle_unit": "micro_deg",
                         "angle_source": "solved", "value_source": "solved",
                         "solved_from": "geometry"}}

    # linear / aligned：单引用直线长度，或两点间距离
    if len(refs) == 1:
        geom = _geom(entities[0])
        if not all(_num(geom.get(key)) for key in ("x1", "y1", "x2", "y2")):
            raise _fail("unsolvable_dimension",
                        "a single-reference %s dimension needs a straight entity" % kind,
                        kind=kind)
        length = math.hypot(float(geom["x2"]) - float(geom["x1"]),
                            float(geom["y2"]) - float(geom["y1"]))
    elif len(refs) == 2:
        points = [_point_of(entities[index], refs[index][1]) for index in (0, 1)]
        if any(point is None for point in points):
            raise _fail("unsolvable_dimension",
                        "two references must resolve to points (start/end/mid/center)", kind=kind)
        length = math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1])
    else:
        raise _fail("bad_reference", "a %s dimension takes one or two references" % kind)
    return {"value_nm": _positive_nm(length, kind),
            "args": {"value_source": "solved", "solved_from": "geometry"}}


def _apply(node: dict, update: Mapping) -> None:
    """落盘求解结果（`args` 合并刷新，不丢已有键）。"""
    args = node.get("args")
    if not isinstance(args, dict):
        args = {}
        node["args"] = args
    args.update(update["args"])
    node["value_nm"] = int(update["value_nm"])


# --------------------------------------------------------------------------------------
# 操作：重生成 / 重关联 / 批量重生成
# --------------------------------------------------------------------------------------


def dim_regen(sk_or_doc, *, target):
    """**重生成一条尺寸**：`target` 是注解 uuid；按现引用从活几何重解数值并回写。

    写入 `args["value_source"]="solved"`（角度尺寸另刷 `angle_deg`/`angle_source`），
    使"几何真解出来的值"与调用方声明值可分辨。不可解 / 引用悬空 ⇒ `CadError`，文档零改动。
    返回 `[annotation_uuid]`（数值恰好不变也算重解成功，来源标记已刷新）。
    """
    annotation_uuid = _uuid_str(target, "target")
    annotations = _annotations(sk_or_doc)
    node = _find_annotation(annotations, annotation_uuid)
    update = _solve_value(sk_or_doc, node)  # 先解后写：解不出就一行不落
    _apply(node, update)
    return [annotation_uuid]


def dim_reassociate(sk_or_doc, *, target, reference):
    """**重关联一条尺寸**：把注解 uuid 改绑到新引用（单个二元组或二元组列表）。

    顺序是**先校验、先求解、后落盘**：新引用解析不到（`unknown_reference`）或新引用解不出值
    （`unsolvable_dimension` / `bad_reference`）时，`target` 与 `value_nm` 都保持原样，绝不半写。
    落盘时记 `args["reassociated_from"]` = 旧主引用 uuid，并刷 `value_source="solved"`。
    返回 `[annotation_uuid]`。
    """
    annotation_uuid = _uuid_str(target, "target")
    annotations = _annotations(sk_or_doc)
    node = _find_annotation(annotations, annotation_uuid)
    kind = node.get("kind")
    if kind not in _VALUE_KINDS:
        raise _fail("not_a_dimension", "annotation kind %r carries no dimension value" % (kind,))
    refs = _as_refs(reference, "reference")
    for ref in refs:  # 新引用必须先能解析（悬空 ⇒ unknown_reference，且尚未改动文档）
        _find_entity(sk_or_doc, ref[0])
    stored = refs[0] if len(refs) == 1 else refs
    probe = dict(node)
    probe["target"] = stored
    update = _solve_value(sk_or_doc, probe)  # 用新引用先解出来，再落盘
    previous = _target_refs(node)[0][0]
    _apply(node, update)
    node["target"] = stored
    args = node.get("args")
    if isinstance(args, dict):
        args["reassociated_from"] = previous
    return [annotation_uuid]


def dim_regen_all(sk_or_doc):
    """**批量重生成**：逐个重解全族尺寸，返回**数值真发生变化**的注解 uuid 列表。

    不可解的条目（悬空引用、单引用角度、无半径实体等）原值保留且**不进返回列表** ——
    返回值只声明"确实改过的那些"，不把"没算"伪装成"算过"。返回 `[]` 表示本次无一条数值变化。
    """
    changed: list[str] = []
    for node in list(_annotations(sk_or_doc)):
        if not isinstance(node, dict) or node.get("kind") not in _VALUE_KINDS:
            continue
        try:
            update = _solve_value(sk_or_doc, node)
        except CadError:
            continue
        if node.get("value_nm") != int(update["value_nm"]):
            changed.append(str(node.get("uuid")))
        _apply(node, update)
    return changed


#: 操作名 -> 可调用（冻结接口：第一个位置参数是 `sk_or_doc`，返回 uuid 列表）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "dim_regen": dim_regen,
    "dim_reassociate": dim_reassociate,
    "dim_regen_all": dim_regen_all,
}


# --------------------------------------------------------------------------------------
# 自带 selftest（真文档 + 真改几何 + 真断言）
# --------------------------------------------------------------------------------------


def _selftest_document() -> dict:
    """自证夹具：一页合法形状的 L1 文档（两圆 + 两直线，annotations 空，长度全整数纳米）。

    两个圆**故意用两种半径字段拼写**（`r` 与 `radius_nm`），证明求解器不是只认一种键。
    """
    return {
        "sketches": [
            {
                "uuid": str(_uuid.uuid4()),
                "entities": [
                    # 半径 20 mm；几何变动测试会把它改成 27 mm
                    {"uuid": str(_uuid.uuid4()), "kind": "circle",
                     "geom": {"cx": 0, "cy": 0, "r": 20_000_000}},
                    # 半径 35 mm（另一种拼写）
                    {"uuid": str(_uuid.uuid4()), "kind": "circle",
                     "geom": {"cx": 100_000_000, "cy": 0, "radius_nm": 35_000_000}},
                    # 3-4-5 直线：长度 50 mm
                    {"uuid": str(_uuid.uuid4()), "kind": "line",
                     "geom": {"x1": 0, "y1": 0, "x2": 30_000_000, "y2": 40_000_000}},
                    # 竖直直线：与上面成 90°
                    {"uuid": str(_uuid.uuid4()), "kind": "line",
                     "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": 10_000_000}},
                ],
            }
        ],
        "annotations": [],
    }


def _selftest() -> int:
    from cadcore.ops.annotation import (add_aligned_dimension, add_angular_dimension,
                                        add_diameter_dimension, add_radial_dimension)

    doc = _selftest_document()
    circle_a, circle_b, line_ab, line_v = (entity["uuid"] for entity in
                                           doc["sketches"][0]["entities"])
    failures: list[str] = []
    checks = 0

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not passed:
            failures.append(name)
        print("check %-46s %s %s" % (name, "PASS" if passed else "FAIL", detail))

    # 1) 半径重生成：真几何变动（20 mm → 27 mm）后重解
    radial = add_radial_dimension(doc, target=(circle_a, "edge"), value_nm=20_000_000)[0]
    doc["sketches"][0]["entities"][0]["geom"]["r"] = 27_000_000
    returned = dim_regen(doc, target=radial)
    node = doc["annotations"][0]
    check("radial_regen_tracks_geometry",
          returned == [radial] and node["value_nm"] == 27_000_000
          and node["args"].get("value_source") == "solved",
          "value_nm=%s source=%s" % (node["value_nm"], node["args"].get("value_source")))

    # 2) 重关联：改绑到圆 B（另一种半径字段拼写，35 mm），引用与数值同时更新
    dim_reassociate(doc, target=radial, reference=(circle_b, "edge"))
    check("reassociate_rebinds_and_resolves",
          node["target"] == [circle_b, "edge"] and node["value_nm"] == 35_000_000
          and node["args"].get("reassociated_from") == circle_a,
          "target=%s value_nm=%s from=%s" % (node["target"], node["value_nm"],
                                             node["args"].get("reassociated_from")))

    # 3) 对齐尺寸：调用方声明 1 nm 是错的，重生成必须按 3-4-5 真长 50 mm 纠回
    aligned = add_aligned_dimension(doc, target=(line_ab, "edge"), value_nm=1)[0]
    dim_regen(doc, target=aligned)
    check("aligned_regen_uses_true_length",
          doc["annotations"][1]["value_nm"] == 50_000_000,
          "value_nm=%s" % doc["annotations"][1]["value_nm"])

    # 4) 角度：建时 90°（解出），几何改成 45° 后重生成必须跟着变
    angular = add_angular_dimension(doc, target=[(line_ab, "dir"), (line_v, "dir")],
                                    value_nm=90)[0]
    angular_node = [item for item in doc["annotations"] if item["uuid"] == angular][0]
    solved_at_add = angular_node["value_nm"] == 90_000_000
    doc["sketches"][0]["entities"][3]["geom"] = {"x1": 0, "y1": 0,
                                                 "x2": 10_000_000, "y2": 10_000_000}
    dim_regen(doc, target=angular)
    check("angular_regen_resolves_new_angle",
          solved_at_add and angular_node["value_nm"] == 45_000_000
          and abs(angular_node["args"].get("angle_deg", 0.0) - 45.0) < 1e-9
          and angular_node["args"].get("angle_source") == "solved",
          "at_add_90=%s value_nm=%s angle_deg=%s" % (solved_at_add,
                                                     angular_node["value_nm"],
                                                     angular_node["args"].get("angle_deg")))

    # 5) 直径：半径 × 2
    diameter = add_diameter_dimension(doc, target=(circle_a, "edge"), value_nm=1)[0]
    dim_regen(doc, target=diameter)
    dia_node = [item for item in doc["annotations"] if item["uuid"] == diameter][0]
    check("diameter_regen_is_twice_radius", dia_node["value_nm"] == 54_000_000,
          "value_nm=%s" % dia_node["value_nm"])

    # 6) 批量：再把圆 A 半径改成 31 mm，regen_all 必须只报"真变了"的那条（直径条）
    doc["sketches"][0]["entities"][0]["geom"]["r"] = 31_000_000
    changed = dim_regen_all(doc)
    check("regen_all_reports_only_changed", changed == [diameter] and dia_node["value_nm"] == 62_000_000,
          "changed=%s value_nm=%s" % (changed, dia_node["value_nm"]))

    # 7) 悬空引用：先解析失败，且不许半写（值/引用都保持原样）
    before = (list(node["target"]), node["value_nm"])
    raised = ""
    try:
        dim_reassociate(doc, target=radial, reference=("no-such-uuid", "edge"))
    except CadError as error:
        raised = error.reason
    after = (list(node["target"]), node["value_nm"])
    check("dangling_reassociate_fails_without_write",
          raised == "unknown_reference" and before == after,
          "reason=%s before=%s after=%s" % (raised, before, after))

    # 8) 不可解：半径尺寸绑到直线 ⇒ 显式 unsolvable_dimension（不静默给 0）
    raised = ""
    try:
        dim_reassociate(doc, target=radial, reference=(line_ab, "edge"))
    except CadError as error:
        raised = error.reason
    check("unresolvable_radius_rejected", raised == "unsolvable_dimension", "reason=%s" % raised)

    # 9) 非尺寸注解：显式 not_a_dimension
    from cadcore.ops.annotation import add_text
    note = add_text(doc, at_nm=(0, 0), literal=True, text="note")[0]
    raised = ""
    try:
        dim_regen(doc, target=note)
    except CadError as error:
        raised = error.reason
    check("non_dimension_regen_rejected", raised == "not_a_dimension", "reason=%s" % raised)

    print("FEATURE_SELFTEST family=%s checks=%d failed=%d"
          % (FAMILY, checks, len(failures)))
    return 1 if failures else 0


def main() -> int:
    code = _selftest()
    if code:
        print("FEATURE_FAIL family=%s verdict=FAIL failed_checks_see_above" % FAMILY)
        return code
    print("FEATURE_OPS family=%s ops=%s count=%d"
          % (FAMILY, ",".join(sorted(OPERATIONS)), len(OPERATIONS)))
    print("FEATURE_OK family=annotation op=dim_regen verdict=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
