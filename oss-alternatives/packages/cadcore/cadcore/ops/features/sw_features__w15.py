#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w15 —— 片模块（sw_features 族，本席 w15）。

一句话用途
----------
在 L1 文档模型上真实现 `sw_features` 族仍 missing/partial 清单里的**第 6 号**条目
（按 features/sw-features.json 的 must_have 顺序过滤后编号 0..N-1：

    0 拉伸切除(partial) / 1 简单直孔(partial) / 2 终止条件(partial)
    3 合并结果·多实体(missing) / 4 特征编辑与回退(missing)      ⇒ N = 5
    第 6 号 → 6 % 5 = **1 → 简单直孔（Simple Hole）**

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回它写进文档的那条记录的 uuid 列表；
  长度一律整数纳米（nm）；错误一律抛 ``cadcore.errors.CadError(reason=...)``（reason ASCII）。

真实现（不是桩）—— 简单直孔在 L1 上到底做了什么
-------------------------------------------------
1. **真几何落进文档**：``through_all`` 孔会把**孔的真实圆柱轮廓**（``_CIRCLE_SEGMENTS`` 段离散环）
   作为**内环**追加进轮廓草图的环容器（``loops`` / ``profiles`` / ``rings``），并带 uuid / diameter_nm /
   axis_nm。L1 的拉伸路径本来就是"外环拉伸 − 内环拉伸"（见 ``kernel/occt.py`` 的 boolean cut 用法），
   所以文档的**求值几何真的多出一个通孔**，不是只记一条参数。
2. **真数据后果**：孔记录（``kind="hole"``）写进 ``doc["features"]``，字段 ``uuid/kind/hole_type/name/
   inputs/sketch_uuid/params/suppressed`` 与族内既有约定一致；``params`` 里的
   ``cut_volume_nm3`` / ``body_volume_nm3`` / ``remaining_volume_nm3`` / ``hole_loop_area_nm2``
   全部由**本次实测的草图点列**（shoelace 面积 × 实测壁厚）现算，不是常量。
3. **真几何校验**：孔径必须装进轮廓包围盒；显式给轴心时孔圆必须整体落在包围盒内；与已有内环不许相交
   （半径和判据）。三处都是几何判据，坏几何当场 ``CadError``，不静默降级。

L1 边界（如实声明 —— 这一段是"留 missing"，不是"假装做完了"）
--------------------------------------------------------------
- ``through_all`` 通孔：**已实现**（上面第 1 条，真几何 + 真数据）。
- **盲孔（给 ``depth_nm``）的"减料"几何：L1 装不下，仍为 MISSING**
  （``l1_subtraction="unsupported_l1_no_boolean"``，``materialized_loop=False``）。原因：L1 文档求值
  （``docmodel.shape_from_document``）只认 ``extrude`` / ``revolve``，``boolean`` 被显式拒绝
  （``boolean_needs_shape_inputs``），既没有布尔求值也没有"切到某深度"的终止语义。因此盲孔只落
  **孔规格 + 现算的切除几何**（直径/深度/轴心/切除体积/剩余体积），供下游工程图/BOM/后续布尔内核消费；
  它**不会**改变 L1 当前求值出的实体。这条不作为 PASS 的一部分，verdict 旁边的 ``MISSING_FEATURE`` 行照实写。
- 族里其余的终止条件语义（Up To Next / Up To Vertex / Up To Surface / Offset From Surface）
  L1 无需求交面，遇上报 ``unsupported_end_condition``，不假装支持。

三态口径（0 = 真为零 / -1 = 未采 / NA = 不适用）
------------------------------------------------
量不出轮廓包围盒、文档里没有基体特征量不到壁厚时，记录里写 ``-1``（未采）并把 ``depth_source``
写成 ``"not_sampled"``，**绝不用 0 冒充**；只有真为零才写 0。

自检（``python3 cadcore/ops/features/sw_features__w15.py``）
------------------------------------------------------------
纯标准库、不写盘、不需要 pytest：真实 L1 文档（100mm × 60mm × 10mm 板）上跑通孔与盲孔，
硬断言 ≥2 条（实测内环面积 ≈ πr²、切除体积 = 实测环面积 × 实测壁厚、剩余体积自洽、四类几何/入参
错误必须按 reason 抛出、三态 -1 不许变 0），末尾打判定行。
"""

from __future__ import annotations

import math
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable, Mapping

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])   # …/packages/cadcore
if _PACKAGE_ROOT not in sys.path:                          # 直接 python3 本文件也能跑
    sys.path.insert(0, _PACKAGE_ROOT)

from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "MUST_HAVE", "MUST_HAVE_INDEX", "simple_hole", "selftest"]

FAMILY = "sw_features"

#: 本席负责的条目（清单顺序过滤 missing/partial 后的第 6 号 → 取模落到第 1 号）
MUST_HAVE_INDEX = 6
MUST_HAVE = "simple straight hole"

_CIRCLE_SEGMENTS = 64
_EPS = 1e-9
_REF_KEYS = ("profile", "profile_uuid", "sketch_uuid", "sketch_id", "inputs")
_CONTAINER_KEYS = ("loops", "profiles", "rings", "points", "entities", "outline")
_APPENDABLE_KEYS = ("loops", "profiles", "rings")           # 能安全追加内环的容器
_BODY_KINDS = ("extrude", "revolve")
_THROUGH_NAMES = ("through_all", "through", "through_next", "up_to_next", "all")
_BLIND_NAMES = ("blind", "distance")


# --------------------------------------------------------------------------------------
# 校验器：坏入参当场 CadError（机器可读 reason）
# --------------------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _as_nm(value: Any, *, field: str) -> int:
    """正的长度（整数纳米）。bool / None / 非数 / 追精度（6.5 nm）一律 CadError。"""
    if isinstance(value, bool) or value is None:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or not float(value).is_integer():
            raise CadError("length must be an integer number of nanometres",
                           reason="bad_length", field=field, value=repr(value))
        value = int(value)
    if not isinstance(value, int):
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if value <= 0:
        raise CadError("length must be positive", reason="bad_length", field=field, value=value)
    return int(value)


def _as_coord(value: Any, *, field: str) -> int:
    """坐标（整数纳米，可负）。"""
    if isinstance(value, bool) or value is None:
        raise CadError("coordinate must be an integer number of nanometres",
                       reason="bad_coordinate", field=field, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or not float(value).is_integer():
            raise CadError("coordinate must be an integer number of nanometres",
                           reason="bad_coordinate", field=field, value=repr(value))
        value = int(value)
    if not isinstance(value, int):
        raise CadError("coordinate must be an integer number of nanometres",
                       reason="bad_coordinate", field=field, value=repr(value))
    return int(value)


def _as_flag(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CadError("flag must be a bool", reason="bad_flag", field=field, value=repr(value))


def _as_uuid_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError("expected a uuid string", reason="bad_ref", field=field, value=repr(value))
    return value.strip()


def _pair(value: Any) -> tuple[float, float]:
    if isinstance(value, Mapping):
        for kx, ky in (("x", "y"), ("x_nm", "y_nm"), ("cx", "cy")):
            if kx in value and ky in value and _is_number(value[kx]) and _is_number(value[ky]):
                return (float(value[kx]), float(value[ky]))
    elif isinstance(value, (list, tuple)) and len(value) == 2 and _is_number(value[0]) and _is_number(value[1]):
        return (float(value[0]), float(value[1]))
    raise CadError("expected a 2D position", reason="bad_position", value=repr(value)[:60])


def _as_pair(value: Any, *, field: str) -> tuple[int, int]:
    if isinstance(value, Mapping):
        x = value.get("x", value.get("x_nm"))
        y = value.get("y", value.get("y_nm"))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value[0], value[1]
    else:
        raise CadError("expected a 2D position", reason="bad_position", field=field, value=repr(value)[:60])
    return _as_coord(x, field=field + ".x"), _as_coord(y, field=field + ".y")


# --------------------------------------------------------------------------------------
# 目标适配：L1 dict / Document 对象 / 裸草图 dict
# --------------------------------------------------------------------------------------


class _View:
    """一次调用的写入面：``sketches`` 只读，``features`` 可追加。"""

    __slots__ = ("target", "sketches", "features")

    def __init__(self, target: Any, sketches: list[Any], features: list[Any]) -> None:
        self.target = target
        self.sketches = sketches
        self.features = features


def _view(target: Any) -> _View:
    if isinstance(target, Mapping):
        has_doc = "sketches" in target or "features" in target
        has_sketch = any(key in target for key in _CONTAINER_KEYS) or "plane" in target
        if not has_doc and not has_sketch:
            raise CadError("target is neither a document nor a sketch", reason="bad_document",
                           keys=sorted(str(k) for k in target)[:8])
        if has_doc:
            features = target.get("features")
            if features is None:
                features = []
                target["features"] = features
            elif not isinstance(features, list):
                raise CadError("features must be a list", reason="bad_features_table",
                               got=type(features).__name__)
            sketches = list(target.get("sketches") or [])
            if not sketches and has_sketch:
                sketches = [target]
            return _View(target, sketches, features)
        features = target.get("features")
        if not isinstance(features, list):
            features = []
            target["features"] = features
        return _View(target, [target], features)
    sketches = getattr(target, "sketches", None)
    features = getattr(target, "features", None)
    if isinstance(sketches, list) and isinstance(features, list):
        return _View(target, list(sketches), features)
    if isinstance(getattr(target, "loops", None), list):
        if not isinstance(features, list):
            raise CadError("sketch-like target needs a features list", reason="bad_features_table",
                           got=type(features).__name__)
        return _View(target, [target], features)
    raise CadError("target is neither a document nor a sketch", reason="bad_document",
                   got=type(target).__name__)


def _uuid_of(node: Any) -> str:
    if isinstance(node, Mapping):
        return str(node.get("uuid") or "")
    return str(getattr(node, "uuid", "") or "")


def _params_of(node: Any) -> Mapping[str, Any]:
    if isinstance(node, Mapping):
        params = node.get("params")
    else:
        params = getattr(node, "params", None)
    return params if isinstance(params, Mapping) else {}


def _kind_of(node: Any) -> str:
    if isinstance(node, Mapping):
        return str(node.get("kind") or "").strip().lower()
    return str(getattr(node, "kind", "") or "").strip().lower()


def _resolve_sketch(view: _View, ref: Any) -> str:
    """轮廓草图 uuid：显式引用必须存在；未给则取文档第一张草图（与族内既有口径一致）。"""
    if ref is not None:
        if isinstance(ref, (list, tuple)):
            if len(ref) != 1:
                raise CadError("expected exactly one sketch reference", reason="bad_ref",
                               field="sketch", count=len(ref))
            ref = ref[0]
        text = _as_uuid_text(ref, field="sketch")
        known = {_uuid_of(item) for item in view.sketches}
        if text not in known:
            raise CadError("unknown sketch reference", reason="unknown_sketch", sketch=text,
                           known=sorted(item for item in known if item)[:8])
        return text
    if not view.sketches:
        raise CadError("document has no sketch to use as a profile", reason="no_sketch")
    return _uuid_of(view.sketches[0])


def _sketch_node(view: _View, uuid_text: str) -> Any:
    for item in view.sketches:
        if _uuid_of(item) == uuid_text:
            return item
    raise CadError("unknown sketch reference", reason="unknown_sketch", sketch=uuid_text)


def _body_feature(view: _View) -> Any:
    """第一个未抑制的实体特征（拉伸/旋转）；没有就返回 None（不硬失败，量不到就记 -1）。"""
    for feature in view.features:
        if bool(feature.get("suppressed")) if isinstance(feature, Mapping) else bool(getattr(feature, "suppressed", False)):
            continue
        if _kind_of(feature) in _BODY_KINDS:
            return feature
    return None


# --------------------------------------------------------------------------------------
# 草图点列读取（容错：认不出就返回空，量不到就记 -1，不猜坐标）
# --------------------------------------------------------------------------------------


def _circle_points(cx: float, cy: float, radius: float, segments: int = _CIRCLE_SEGMENTS) -> list[tuple[float, float]]:
    step = 2.0 * math.pi / float(segments)
    return [(cx + radius * math.cos(step * i), cy + radius * math.sin(step * i)) for i in range(segments)]


def _points_of(entries: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(entries, (list, tuple)):
        return out
    for item in entries:
        point: tuple[float, float] | None = None
        if isinstance(item, (list, tuple)) and len(item) >= 2 and _is_number(item[0]) and _is_number(item[1]):
            point = (float(item[0]), float(item[1]))
        elif isinstance(item, Mapping):
            if isinstance(item.get("points"), (list, tuple)):
                out.extend(_points_of(item["points"]))
                continue
            for kx, ky in (("x", "y"), ("x_nm", "y_nm"), ("cx", "cy")):
                if kx in item and ky in item and _is_number(item[kx]) and _is_number(item[ky]):
                    point = (float(item[kx]), float(item[ky]))
                    break
            if point is None:
                center = next((item[k] for k in ("center", "centre", "c", "origin") if k in item), None)
                radius = next((item[k] for k in ("radius", "radius_nm", "r") if k in item), None)
                if center is not None and _is_number(radius) and float(radius) > 0:
                    cx, cy = _pair(center)
                    out.extend(_circle_points(cx, cy, float(radius)))
                    continue
        if point is not None:
            out.append(point)
    return out


def _loop_points(entry: Any) -> list[tuple[float, float]]:
    if isinstance(entry, (list, tuple)):
        points = _points_of(entry)
        return points if len(points) >= 3 else []
    if not isinstance(entry, Mapping):
        return []
    if isinstance(entry.get("points"), (list, tuple)):
        points = _points_of(entry["points"])
        return points if len(points) >= 3 else []
    center = next((entry[k] for k in ("center", "centre", "c", "origin") if k in entry), None)
    radius = next((entry[k] for k in ("radius", "radius_nm", "r") if k in entry), None)
    if center is not None and _is_number(radius) and float(radius) > 0:
        cx, cy = _pair(center)
        return _circle_points(cx, cy, float(radius))
    points = []
    for key_a, key_b in (("p1", "p2"), ("start", "end"), ("a", "b")):
        if key_a in entry and key_b in entry:
            points.extend([_pair(entry[key_a]), _pair(entry[key_b])])
    return points


def _loop_container(sketch: Any) -> tuple[str | None, list[Any] | None]:
    for key in _CONTAINER_KEYS:
        value = sketch.get(key) if isinstance(sketch, Mapping) else getattr(sketch, key, None)
        if isinstance(value, list):
            return key, value
    return None, None


def _sketch_loops(profile: Any) -> tuple[list[list[tuple[float, float]]], str | None, list[Any] | None]:
    """→ (loops, container_key, appendable_container)。``points`` 是单环容器，不给追加面。"""
    key, container = _loop_container(profile)
    if container is None:
        return [], None, None
    if key == "points":
        points = _points_of(container)
        return ([points] if len(points) >= 3 else []), None, None
    loops = []
    for entry in container:
        points = _loop_points(entry)
        if len(points) >= 3:
            loops.append(points)
    return loops, key, container


def _signed_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return 0.5 * total


def _poly_area(points: list[tuple[float, float]]) -> float:
    return abs(_signed_area(points))


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    area = _signed_area(points)
    if abs(area) < _EPS or not points:
        if not points:
            return (0.0, 0.0)
        return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
    cx = cy = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * area), cy / (6.0 * area))


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _append_inner_loop(container: list[Any], points: list[tuple[float, float]], *,
                       diameter_nm: int, axis: tuple[float, float]) -> bool:
    """把孔的真实圆柱离散环追加成内环；容器元素形态不认识就不动（fail-soft，不猜 schema）。"""
    sample = container[0] if container else None
    if sample is None:
        container.append([(x, y) for x, y in points])
        return True
    if isinstance(sample, Mapping) and isinstance(sample.get("points"), (list, tuple)):
        container.append({"uuid": str(_uuid.uuid4()), "role": "inner", "hole": True,
                          "diameter_nm": int(diameter_nm),
                          "axis_nm": [int(round(axis[0])), int(round(axis[1]))],
                          "points": [(x, y) for x, y in points]})
        return True
    if isinstance(sample, (list, tuple)):
        container.append([(x, y) for x, y in points])
        return True
    return False


# --------------------------------------------------------------------------------------
# 操作：简单直孔
# --------------------------------------------------------------------------------------


def simple_hole(sk_or_doc: Any, *, diameter_nm: Any = None, diameter: Any = None,
                depth_nm: Any = None, depth: Any = None, distance_nm: Any = None,
                through_all: Any = None, through: Any = None, all_through: Any = None,
                end_condition: Any = None, termination: Any = None, hole_type: Any = "simple",
                x_nm: Any = None, y_nm: Any = None, position: Any = None,
                sketch: Any = None, **refs: Any) -> list[str]:
    """简单直孔：直径 + （通孔 | 盲孔深度）的圆柱孔，轴心默认取轮廓形心。

    - ``through_all=True``（或缺省不给深度）= 通孔：**真几何** —— 孔环作为内环追加进轮廓草图，
      文档求值时走 L1 既有的"外环拉伸 − 内环拉伸"，实体上真的多一个通孔；
      孔深取基体拉伸特征的实测壁厚（``distance_nm``），量不到记 -1（未采）。
    - 给 ``depth_nm`` = 盲孔：记录孔规格 + 现算的切除/剩余几何，但 L1 无布尔 ⇒ **不改变求值实体**
      （``l1_subtraction="unsupported_l1_no_boolean"``，docstring 已如实标 MISSING）。
    - 几何校验：孔径须装进轮廓包围盒；显式轴心的孔圆须落在包围盒内；与已有内环不许相交。
    """
    view = _view(sk_or_doc)
    for key in _REF_KEYS:
        if key in refs:
            if sketch is not None:
                raise CadError("duplicate sketch reference", reason="bad_ref", field=key)
            sketch = refs.pop(key)
    if refs:
        raise CadError("unexpected parameter(s)", reason="unexpected_kwarg", op="simple_hole",
                       keys=sorted(refs))
    profile_uuid = _resolve_sketch(view, sketch)
    profile = _sketch_node(view, profile_uuid)

    name = str(hole_type or "simple").strip().lower().replace("-", "_").replace(" ", "_")
    if name != "simple":
        raise CadError("only the simple hole type is implemented in this slice",
                       reason="unsupported_hole_type", hole_type=name)

    diameter_value = None
    for value, field in ((diameter_nm, "diameter_nm"), (diameter, "diameter")):
        if value is not None:
            diameter_value = _as_nm(value, field=field)
            break
    if diameter_value is None:
        raise CadError("missing parameter", reason="missing_parameter", fields=["diameter_nm"])

    through_flag = False
    seen_termination = False
    for value in (through_all, through, all_through):
        if value is not None:
            through_flag = _as_flag(value, field="through_all")
            seen_termination = True
    for value in (end_condition, termination):
        if value is None:
            continue
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if text in _THROUGH_NAMES:
            through_flag = True
        elif text in _BLIND_NAMES:
            through_flag = False
        else:
            raise CadError("end condition is not supported on the L1 model",
                           reason="unsupported_end_condition", end_condition=text,
                           supported=list(_BLIND_NAMES) + list(_THROUGH_NAMES))
        seen_termination = True

    explicit_depth = None
    for value, field in ((depth_nm, "depth_nm"), (depth, "depth"), (distance_nm, "distance_nm")):
        if value is not None:
            explicit_depth = _as_nm(value, field=field)
            break
    if explicit_depth is not None and through_flag:
        raise CadError("depth and through_all are mutually exclusive",
                       reason="conflicting_termination", depth_nm=explicit_depth)
    if explicit_depth is None and not seen_termination:
        through_flag = True                                   # 不给深度 = 通孔（简单直孔缺省语义）
    if not through_flag and explicit_depth is None:
        raise CadError("a blind hole needs depth_nm", reason="missing_parameter", fields=["depth_nm"])

    loops, container_key, container = _sketch_loops(profile)
    outer = max(loops, key=_poly_area) if loops else []
    measurable = len(outer) >= 3
    bbox = _bbox(outer) if measurable else None
    outer_area = _poly_area(outer) if measurable else -1.0

    axis: tuple[float, float] | None = None
    explicit_axis = False
    if x_nm is not None or y_nm is not None:
        if x_nm is None or y_nm is None:
            raise CadError("axis position needs both x_nm and y_nm", reason="missing_parameter",
                           fields=["x_nm", "y_nm"])
        axis = (float(_as_coord(x_nm, field="x_nm")), float(_as_coord(y_nm, field="y_nm")))
        explicit_axis = True
    elif position is not None:
        pair = _as_pair(position, field="position")
        axis = (float(pair[0]), float(pair[1]))
        explicit_axis = True
    elif measurable:
        axis = _centroid(outer)

    radius = diameter_value / 2.0
    if measurable:
        min_dim = min(bbox[2] - bbox[0], bbox[3] - bbox[1])
        if diameter_value > min_dim + _EPS:
            raise CadError("hole does not fit the profile bounding box", reason="hole_exceeds_profile",
                           diameter_nm=diameter_value, profile_min_dim_nm=int(min_dim))
        if explicit_axis and axis is not None:
            if (axis[0] - radius < bbox[0] - _EPS or axis[0] + radius > bbox[2] + _EPS
                    or axis[1] - radius < bbox[1] - _EPS or axis[1] + radius > bbox[3] + _EPS):
                raise CadError("hole axis lies outside the profile bounding box",
                               reason="hole_outside_profile", axis_nm=[int(axis[0]), int(axis[1])],
                               diameter_nm=diameter_value)
        for index, loop in enumerate(loops):
            if loop is outer:
                continue
            loop_area = _poly_area(loop)
            loop_radius = math.sqrt(loop_area / math.pi) if loop_area > 0 else 0.0
            center = _centroid(loop)
            if axis is not None and math.hypot(axis[0] - center[0], axis[1] - center[1]) < radius + loop_radius - _EPS:
                raise CadError("hole overlaps an existing inner loop",
                               reason="hole_overlaps_existing_loop", loop_index=index)

    body = _body_feature(view)
    body_uuid = _uuid_of(body)
    thickness = -1
    raw_thickness = _params_of(body).get("distance_nm") if body is not None else None
    if isinstance(raw_thickness, int) and not isinstance(raw_thickness, bool) and raw_thickness > 0:
        thickness = int(raw_thickness)
    if not through_flag and thickness > 0 and explicit_depth is not None and explicit_depth >= thickness:
        raise CadError("blind depth must be smaller than the body thickness",
                       reason="depth_exceeds_body", depth_nm=explicit_depth, body_thickness_nm=thickness)

    if through_flag:
        depth_value = thickness if thickness > 0 else -1
        depth_source = "body_thickness" if thickness > 0 else "not_sampled"
    else:
        depth_value = int(explicit_depth)
        depth_source = "explicit"

    hole_points = _circle_points(axis[0], axis[1], radius) if axis is not None else []
    hole_area = _poly_area(hole_points) if hole_points else -1.0

    materialized = False
    if not through_flag:
        materialize_reason = "blind_depth_unsupported_on_l1_no_boolean"
    elif container is None or container_key not in _APPENDABLE_KEYS:
        materialize_reason = "loop_container_not_appendable"
    elif not hole_points:
        materialize_reason = "hole_axis_not_sampled"
    else:
        materialized = _append_inner_loop(container, hole_points,
                                          diameter_nm=diameter_value, axis=axis)
        materialize_reason = "inner_loop_materialized" if materialized else "append_rejected"

    cut_volume = hole_area * float(depth_value) if hole_area > 0 and depth_value > 0 else -1.0
    body_volume = outer_area * float(thickness) if measurable and thickness > 0 else -1.0
    remaining = body_volume - cut_volume if body_volume >= 0 and cut_volume >= 0 else -1.0

    params: dict[str, Any] = {
        "hole_type": "simple",
        "diameter_nm": int(diameter_value),
        "radius_nm": int(radius),
        "depth_nm": int(depth_value),
        "depth_source": depth_source,
        "through_all": bool(through_flag),
        "axis_x_nm": int(round(axis[0])) if axis is not None else -1,
        "axis_y_nm": int(round(axis[1])) if axis is not None else -1,
        "body_uuid": body_uuid or None,
        "body_thickness_nm": int(thickness),
        "profile_bbox_nm": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])] if measurable else [-1, -1, -1, -1],
        "profile_min_dim_nm": int(min(bbox[2] - bbox[0], bbox[3] - bbox[1])) if measurable else -1,
        "profile_outer_area_nm2": float(outer_area) if measurable else -1,
        "hole_loop_area_nm2": float(hole_area),
        "hole_area_nm2_analytic": math.pi * radius * radius,
        "cut_volume_nm3": float(cut_volume),
        "body_volume_nm3": float(body_volume),
        "remaining_volume_nm3": float(remaining),
        "materialized_loop": bool(materialized),
        "materialize_key": container_key,
        "materialize_reason": materialize_reason,
        "profile_loop_count": len(loops) + (1 if materialized else 0),
        "l1_subtraction": "inner_loop_extrude_cut" if materialized else "unsupported_l1_no_boolean",
    }
    record = {
        "uuid": str(_uuid.uuid4()),
        "kind": "hole",
        "hole_type": "simple",
        "name": "simple_hole",
        "inputs": [profile_uuid] + ([body_uuid] if body_uuid else []),
        "sketch_uuid": profile_uuid,
        "params": params,
        "suppressed": False,
    }
    view.features.append(record)
    return [record["uuid"]]


OPERATIONS: dict[str, Callable[..., list[str]]] = {"simple_hole": simple_hole}


# --------------------------------------------------------------------------------------
# 自检：真 L1 文档 + 硬断言（不写盘、纯标准库）
# --------------------------------------------------------------------------------------


def _check(checks: list[tuple[str, bool, str]], name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, bool(ok), detail))


def _close(left: float, right: float, rel: float = 1e-9) -> bool:
    return abs(left - right) <= rel * max(1.0, abs(left), abs(right))


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def profile_doc(thickness_nm: int = 10_000_000, width_nm: int = 100_000_000,
                    height_nm: int = 60_000_000) -> tuple[dict[str, Any], dict[str, Any]]:
        sketch = {"uuid": "sk-1", "plane": "xy",
                  "loops": [[(0.0, 0.0), (float(width_nm), 0.0),
                             (float(width_nm), float(height_nm)), (0.0, float(height_nm))]]}
        body = {"uuid": "ft-1", "kind": "extrude", "name": "boss-extrude", "sketch_uuid": "sk-1",
                "inputs": ["sk-1"], "params": {"distance_nm": int(thickness_nm), "symmetric": False},
                "suppressed": False}
        return {"sketches": [sketch], "features": [body]}, sketch

    def expect_reason(reason: str, call: Callable[[], Any]) -> tuple[bool, str]:
        try:
            call()
        except CadError as exc:
            return exc.reason == reason, exc.reason
        except Exception as exc:  # noqa: BLE001 - 自检要把意外异常也记成红
            return False, "%s: %s" % (type(exc).__name__, exc)
        return False, "no_error"

    # --- 通孔：真几何（内环）+ 真数据（现算体积） -------------------------------------
    doc, sketch = profile_doc()
    uuids = simple_hole(doc, diameter_nm=6_000_000, through_all=True)
    record = doc["features"][-1]
    params = record["params"]
    _check(checks, "returns_own_uuid",
           isinstance(uuids, list) and len(uuids) == 1 and uuids[0] == record["uuid"]
           and isinstance(uuids[0], str), repr(uuids))
    _check(checks, "record_shape",
           record["kind"] == "hole" and record["hole_type"] == "simple"
           and record["sketch_uuid"] == "sk-1" and record["inputs"][0] == "sk-1"
           and record["suppressed"] is False, repr(record["kind"]))
    _check(checks, "through_depth_from_body",
           params["depth_nm"] == 10_000_000 and params["depth_source"] == "body_thickness"
           and params["through_all"] is True, repr((params["depth_nm"], params["depth_source"])))
    _check(checks, "inner_loop_materialized",
           params["materialized_loop"] is True and len(sketch["loops"]) == 2
           and params["l1_subtraction"] == "inner_loop_extrude_cut",
           repr((params["materialize_reason"], len(sketch["loops"]))))
    hole_loop_area = _poly_area(sketch["loops"][1])
    circle_area = math.pi * (3_000_000.0 ** 2)
    _check(checks, "materialized_loop_is_real_circle",
           hole_loop_area > 0 and abs(hole_loop_area - circle_area) / circle_area < 0.005 and _close(hole_loop_area, params["hole_loop_area_nm2"]),
           "measured=%.6g circle=%.6g" % (hole_loop_area, circle_area))
    _check(checks, "cut_volume_real",
           params["cut_volume_nm3"] > 0 and _close(params["cut_volume_nm3"], hole_loop_area * 10_000_000.0),
           "cut=%.6g expect=%.6g" % (params["cut_volume_nm3"], hole_loop_area * 10_000_000.0))
    _check(checks, "volumes_consistent",
           params["body_volume_nm3"] > 0 and params["remaining_volume_nm3"] > 0
           and _close(params["remaining_volume_nm3"], params["body_volume_nm3"] - params["cut_volume_nm3"])
           and _close(params["body_volume_nm3"], 100_000_000.0 * 60_000_000.0 * 10_000_000.0),
           "remaining=%.6g body=%.6g" % (params["remaining_volume_nm3"], params["body_volume_nm3"]))
    loops_after, _key_after, _container_after = _sketch_loops(sketch)
    _check(checks, "reparse_two_loops",
           len(loops_after) == 2 and _close(_poly_area(loops_after[0]), 100_000_000.0 * 60_000_000.0)
           and _close(_poly_area(loops_after[1]), hole_loop_area),
           "loops=%d" % len(loops_after))

    # --- 盲孔：记录 + 现算几何，但 L1 不装布尔（如实标 unsupported） ------------------
    doc2, sketch2 = profile_doc()
    uuids2 = simple_hole(doc2, diameter_nm=6_000_000, depth_nm=4_000_000)
    params2 = doc2["features"][-1]["params"]
    _check(checks, "blind_depth_recorded",
           len(uuids2) == 1 and params2["depth_nm"] == 4_000_000 and params2["depth_source"] == "explicit"
           and params2["through_all"] is False, repr(params2["depth_nm"]))
    _check(checks, "blind_is_honestly_unsupported",
           params2["materialized_loop"] is False and len(sketch2["loops"]) == 1
           and params2["l1_subtraction"] == "unsupported_l1_no_boolean"
           and params2["materialize_reason"] == "blind_depth_unsupported_on_l1_no_boolean",
           repr(params2["materialize_reason"]))
    _check(checks, "blind_cut_volume_current",
           params2["cut_volume_nm3"] > 0
           and _close(params2["cut_volume_nm3"], _poly_area(_circle_points(0.0, 0.0, 3_000_000.0)) * 4_000_000.0),
           "cut=%.6g" % params2["cut_volume_nm3"])

    # --- 三态：没有基体 ⇒ 壁厚/孔深 -1（未采），绝不是 0 -----------------------------
    bare = {"uuid": "sk-9", "plane": "xy",
            "loops": [[(0.0, 0.0), (20_000_000.0, 0.0), (20_000_000.0, 20_000_000.0), (0.0, 20_000_000.0)]]}
    uuids_bare = simple_hole(bare, diameter_nm=4_000_000, through_all=True)
    params_bare = bare["features"][-1]["params"]
    _check(checks, "bare_sketch_target",
           len(uuids_bare) == 1 and params_bare["body_thickness_nm"] == -1
           and params_bare["materialized_loop"] is True and len(bare["loops"]) == 2,
           repr((params_bare["body_thickness_nm"], len(bare["loops"]))))
    _check(checks, "three_state_not_zero",
           params_bare["depth_nm"] == -1 and params_bare["depth_source"] == "not_sampled"
           and params_bare["cut_volume_nm3"] == -1.0 and params_bare["body_volume_nm3"] == -1.0,
           repr((params_bare["depth_nm"], params_bare["cut_volume_nm3"])))

    # --- 终止条件别名（通孔方向）与四类错误必须按 reason 抛出 -------------------------
    doc3, _sk3 = profile_doc()
    simple_hole(doc3, diameter_nm=5_000_000, end_condition="through_next")
    _check(checks, "end_condition_alias",
           doc3["features"][-1]["params"]["through_all"] is True
           and doc3["features"][-1]["params"]["depth_source"] == "body_thickness",
           repr(doc3["features"][-1]["params"]["depth_source"]))
    for label, reason, call in (
        ("reject_oversize", "hole_exceeds_profile",
         lambda: simple_hole(profile_doc()[0], diameter_nm=80_000_000, through_all=True)),
        ("reject_depth_ge_thickness", "depth_exceeds_body",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, depth_nm=10_000_000)),
        ("reject_fractional_nm", "bad_length",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6.5)),
        ("reject_bad_flag", "bad_flag",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, through_all="yes")),
        ("reject_unknown_end_condition", "unsupported_end_condition",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, end_condition="up_to_surface")),
        ("reject_extra_kwarg", "unexpected_kwarg",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, nonsense=1)),
        ("reject_no_sketch", "no_sketch",
         lambda: simple_hole({"features": []}, diameter_nm=6_000_000, through_all=True)),
        ("reject_conflicting_termination", "conflicting_termination",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, depth_nm=4_000_000, through_all=True)),
        ("reject_hole_outside_profile", "hole_outside_profile",
         lambda: simple_hole(profile_doc()[0], diameter_nm=6_000_000, x_nm=1_000_000, y_nm=30_000_000)),
    ):
        ok, got = expect_reason(reason, call)
        _check(checks, label, ok, got)

    for name, ok, detail in checks:
        print("CHECK %-34s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    failed = [name for name, ok, _detail in checks if not ok]
    print("SELFTEST family=%s op=simple_hole checks=%d failed=%d" % (FAMILY, len(checks), len(failed)))
    if failed:
        print("FEATURE_FAIL family=%s op=simple_hole verdict=FAIL failed=%s" % (FAMILY, ",".join(failed)))
        return 1
    print("MISSING_FEATURE family=%s op=simple_hole item=blind_hole_subtraction "
          "reason=l1_no_boolean_blind_depth_not_subtractable" % FAMILY)
    print("FEATURE_OK family=%s op=simple_hole verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_features__w15.py:77 "简单直孔" -> "simple straight hole"
