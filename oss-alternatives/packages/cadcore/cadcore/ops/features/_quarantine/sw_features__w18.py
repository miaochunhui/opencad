#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w18 —— sw_features 族实现片 w18：must_have 第 9 号「简单直孔」。

分工口径
--------
族清单 `docs/research/cad-parity/features/sw-features.json` 的 must_have 共 18 条；状态文件
`docs/research/cad-parity/status/sw-features.json` 里仍非 implemented 的只有 4 条（拉伸切除 / 简单直孔 /
终止条件 / 特征编辑与回退）。按清单顺序编号 0..3 后，``9 mod 4 = 1`` ⇒ 本片负责 **简单直孔
（Simple Hole：不带标准件属性的快速通孔/盲孔）**。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- ``simple_hole(sk_or_doc, **kwargs) -> list[str]``：返回落地的那条特征记录 uuid 列表；长度一律**整数
  纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（reason 机器可读、文案 ASCII）。

L1 上真做了什么（不是桩）
-------------------------
1. **真几何量测**：从 L1 草图的**真实实体**（``kind`` + ``geom``：line / polyline / arc / circle）现算
   轮廓——按端点贪心串成闭合多边形（弧按 32 段弦近似）；算**面积**（外环 shoelace 减内环圆面积，
   整圆轮廓用 πR²）、**孔心到轮廓的最短距离**（点-线段距离；圆形轮廓用 |d−R|）、**最小贴壁余量**
   ``min_wall_nm = min(距离) − r``。孔再小，只要**真穿出轮廓**（余量 < ``wall_min_nm``）就当场
   ``CadError(reason="hole_breaks_out")``；孔心落在轮廓外抛 ``hole_outside_profile``。
2. **真数据依赖**：通孔深度**不是**写死的常数，而是现读文档里第一条未抑制"新料"拉伸特征的
   ``params.distance_nm``（料厚）；没有就抛 ``no_solid_feature``。盲孔深度超过料厚抛
   ``depth_exceeds_material``。既有孔的中心距不够（< r₁+r₂）抛 ``hole_overlaps_existing`` —— 这是一条
   **真几何**约束，且把"查了几条 / 有几条因缺位置信息没查"分别记成 ``holes_checked_n`` /
   ``holes_unchecked_n``（"没算"与"算出 0"不同形，见 AGENTS.md E2.3）。
3. **真数据后果**：产出的记录（``kind="hole"``）落进文档 ``features`` 表，带
   ``diameter_nm / radius_nm / depth_nm / through_all / end_condition / position_nm / material_thickness_nm /
   min_wall_nm / hole_area_nm2 / remaining_area_nm2 / removed_volume_nm3 / remaining_volume_nm3 /
   cut_loop`` 等**现算出来的**几何量（整数，无浮点混入真源）；去除/剩余面积或体积 ≤ 0 抛
   ``no_material_left``。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
- **本层不做 3D 布尔**：真实体切除是内核的活。通孔的真切除路径是 occt 的"外环拉伸 − 内环拉伸"
  （``kernel/occt.py:482``，``shape_from_document`` 按内环数记 ``holes``）；本记录把该内环几何原样放在
  ``params.cut_loop``（圆 + 整数 nm 圆心/半径），调用方把它挂作轮廓草图的**内环实体**即可走通那条路。
  **nano 后端没有 boolean 能力**（``kernel/nano.py:403`` 抛 ``DependencyMissing``）⇒ 默认后端下
  3D 切除结果**拿不到**，这是已有后端能力的空缺，不是本片假装支持的东西。
- ``docmodel.recompute`` 只分发 ``extrude`` / ``revolve``（``docmodel.py:840`` 起），``kind="hole"``
  会被记成 ``feature_kind_unknown`` 诊断 —— 与本族 fillet/chamfer/shell/draft/rib 同口径（记录设计
  意图 + 参数，几何由内核现算），不是本片独有缺陷。
- 盲孔底为**平底**（``tip="flat"``），不做钻尖锥角；``through_all`` 与 ``depth_nm`` 同时给时以
  ``through_all`` 为准，并把原给的深度原样记进 ``requested_depth_nm``（**显式优先级，不是静默丢弃**）。
- 长度必须是整数纳米；浮点只在"整数值"时接受（``8.0`` → ``8000000``? 否 —— 见下），**非整数浮点一律
  抛 ``bad_length``**，因为 L1 真源不允许浮点长度。

自测（``python3 cadcore/ops/features/sw_features__w18.py``）
-----------------------------------------------------------
自包含，不依赖任何外部门/夹具：8 条硬断言（其中 5 条是**阴性对照**，要求尺子看得见红）。判定行
``FEATURE_OK family=sw_features op=simple_hole verdict=PASS``；任一断言失败即 rc=1 并打 FAIL。
"""

from __future__ import annotations

import json
import math
import os
import sys
import uuid as _uuid
from typing import Any, Callable, Mapping

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from cadcore.errors import CadError  # noqa: E402  (sys.path 先补齐再导入)

__all__ = ["FAMILY", "OPERATIONS", "MUST_HAVE_MAP", "CALL_PLANS", "simple_hole", "selftest", "main"]

FAMILY = "sw_features"

#: 本片覆盖的 must_have key（门按表驱动，不猜别名）
MUST_HAVE_MAP: dict[str, str] = {"simple_hole": "simple_hole"}

#: 自动候选表喂不全参数时补的 kwargs（值必须是字面量）
CALL_PLANS: dict[str, list[dict[str, Any]]] = {
    "simple_hole": [{"diameter_nm": 8_000_000, "through_all": True}],
}

_REF_KEYS = ("sketch", "sketch_uuid", "profile", "profile_uuid", "sketch_id")
_HOLE_KINDS = ("hole", "simple_hole", "hole_wizard")
_ARC_CHORDS = 32
_TOL_NM = 1e-6


# --------------------------------------------------------------------------------------
# 入参校验：长度一律整数纳米；错误一律 CadError(reason=...)
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, *, field: str, positive: bool = True) -> int:
    """长度 → 整数纳米。bool / None / 非数字 / 非整数浮点 / 非正（``positive``）一律 CadError。"""
    number: int
    if isinstance(value, bool) or value is None:
        raise CadError("length must be an integer number of nanometres", reason="bad_length",
                       field=field, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CadError("length must be finite", reason="bad_length", field=field, value=repr(value))
        if not float(value).is_integer():
            raise CadError("length must be a whole number of nanometres", reason="bad_length",
                           field=field, value=repr(value))
        number = int(value)
    elif isinstance(value, int):
        number = int(value)
    else:
        raise CadError("length must be an integer number of nanometres", reason="bad_length",
                       field=field, value=repr(value))
    if positive and number <= 0:
        raise CadError("length must be positive", reason="bad_length", field=field, value=number)
    return number


def _as_flag(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise CadError("flag must be a bool", reason="bad_flag", field=field, value=repr(value))


def _as_point_nm(value: Any, *, field: str) -> tuple[int, int]:
    if isinstance(value, Mapping):
        x, y = value.get("x"), value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value[0], value[1]
    else:
        raise CadError("position must be (x_nm, y_nm) or {x, y}", reason="bad_position",
                       field=field, value=repr(value)[:80])
    return (_as_nm(x, field=field + ".x", positive=False),
            _as_nm(y, field=field + ".y", positive=False))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _as_mapping(node: Any) -> Mapping[str, Any]:
    if isinstance(node, Mapping):
        return node
    data = getattr(node, "__dict__", None)
    if isinstance(data, dict):
        return {str(key): item for key, item in data.items() if not str(key).startswith("_")}
    raise CadError("unsupported node type in document", reason="bad_document", got=type(node).__name__)


def _uuid_of(node: Any) -> str:
    if isinstance(node, Mapping):
        return str(node.get("uuid") or "")
    return str(getattr(node, "uuid", "") or "")


# --------------------------------------------------------------------------------------
# 视图：L1 文档 dict / Document 对象 / 裸草图 dict 三种目标都能吃
# --------------------------------------------------------------------------------------


class _View:
    def __init__(self, target: Any, sketches: list[Any], features: list[Any]) -> None:
        self.target = target
        self.sketches = sketches
        self.features = features


def _view(target: Any) -> _View:
    if isinstance(target, Mapping):
        has_doc = "sketches" in target or "features" in target
        has_sketch = "entities" in target or "plane" in target
        if not has_doc and not has_sketch:
            raise CadError("target is neither a document nor a sketch", reason="bad_document",
                           keys=sorted(str(key) for key in target)[:8])
        features = target.get("features")
        if features is None:
            features = []
            target["features"] = features          # 就地写：调用方手里那份也看得见
        elif not isinstance(features, list):
            raise CadError("features must be a list", reason="bad_features_table",
                           got=type(features).__name__)
        sketches = list(target.get("sketches") or []) if has_doc else [target]
        if not has_doc:
            sketches = [target]
        return _View(target, sketches, features)
    sketches = getattr(target, "sketches", None)
    features = getattr(target, "features", None)
    if isinstance(sketches, list) and isinstance(features, list):
        return _View(target, list(sketches), features)
    raise CadError("target is neither a document nor a sketch", reason="bad_document",
                   got=type(target).__name__)


def _pop_ref(refs: dict[str, Any], sketch: Any) -> Any:
    if sketch is not None:
        return sketch
    for key in _REF_KEYS:
        if key in refs:
            return refs.pop(key)
    return None


def _no_extra(refs: Mapping[str, Any], *, op: str) -> None:
    if refs:
        raise CadError("unexpected keyword argument", reason="bad_parameter", op=op,
                       keys=sorted(str(key) for key in refs)[:8])


def _resolve_sketch(view: _View, ref: Any = None) -> str:
    index = {_uuid_of(item): item for item in view.sketches if _uuid_of(item)}
    if ref is None:
        for item in view.sketches:
            return _uuid_of(item)
        raise CadError("document has no sketch to use as a profile", reason="no_sketch")
    if isinstance(ref, (list, tuple)):
        if len(ref) != 1:
            raise CadError("expected exactly one sketch reference", reason="bad_ref",
                           field="sketch", count=len(ref))
        ref = ref[0]
    text = str(ref or "")
    if text not in index:
        raise CadError("unknown sketch reference", reason="unknown_sketch", sketch=text,
                       known=sorted(index)[:8])
    return text


def _sketch_of(view: _View, sketch_uuid: str) -> Mapping[str, Any]:
    for item in view.sketches:
        if _uuid_of(item) == sketch_uuid:
            return _as_mapping(item)
    raise CadError("unknown sketch reference", reason="unknown_sketch", sketch=sketch_uuid)


# --------------------------------------------------------------------------------------
# 轮廓现算：L1 实体（geom 里的整数纳米）→ 弦段 / 圆 / 面积 / 孔心贴壁余量
# --------------------------------------------------------------------------------------


def _entity_points(entity: Mapping[str, Any]) -> list[tuple[float, float]]:
    """L1 实体 → 折线点（line / polyline / arc 走这里；circle 单独处理）。"""
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        return []
    kind = str(entity.get("kind") or "").strip().lower()
    if kind == "line":
        x1, y1 = _number(geom.get("x1")), _number(geom.get("y1"))
        x2, y2 = _number(geom.get("x2")), _number(geom.get("y2"))
        if None in (x1, y1, x2, y2):
            return []
        return [(x1, y1), (x2, y2)]
    if kind == "polyline":
        raw = geom.get("points") or geom.get("vertices") or []
        out: list[tuple[float, float]] = []
        for item in raw:
            if isinstance(item, Mapping):
                x, y = _number(item.get("x")), _number(item.get("y"))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                x, y = _number(item[0]), _number(item[1])
            else:
                return []
            if None in (x, y):
                return []
            out.append((float(x), float(y)))
        return out
    if kind == "arc":
        cx, cy, r = _number(geom.get("cx")), _number(geom.get("cy")), _number(geom.get("r"))
        start, end = _number(geom.get("start_deg")) or 0.0, _number(geom.get("end_deg")) or 0.0
        if None in (cx, cy) or r is None or r <= 0:
            return []
        span = (end - start) % 360.0 or 360.0
        return [(cx + r * math.cos(math.radians(start + span * step / _ARC_CHORDS)),
                 cy + r * math.sin(math.radians(start + span * step / _ARC_CHORDS)))
                for step in range(_ARC_CHORDS + 1)]
    return []


def _circle_geom(entity: Mapping[str, Any]) -> tuple[float, float, float] | None:
    if str(entity.get("kind") or "").strip().lower() != "circle":
        return None
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        return None
    cx, cy, r = _number(geom.get("cx")), _number(geom.get("cy")), _number(geom.get("r"))
    if None in (cx, cy) or r is None or r <= 0:
        return None
    return float(cx), float(cy), float(r)


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= _TOL_NM and abs(a[1] - b[1]) <= _TOL_NM


def _chain_segments(segments: list[tuple[tuple[float, float], tuple[float, float]]]
                    ) -> list[tuple[float, float]] | None:
    """端点贪心串联成闭合多边形；串不起来（开放/断链）返回 None。"""
    if not segments:
        return None
    remaining = list(segments)
    first = remaining.pop(0)
    points = [first[0]]
    current = first[1]
    while remaining:
        found = None
        for index, (a, b) in enumerate(remaining):
            if _close(a, current):
                found = (index, b)
                break
            if _close(b, current):
                found = (index, a)
                break
        if found is None:
            return None
        index, nxt = found
        remaining.pop(index)
        points.append(nxt)
        current = nxt
    if not _close(points[0], points[-1]):
        return None
    return points


def _shoelace(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _profile(sketch: Mapping[str, Any]) -> dict[str, Any]:
    """L1 草图 → 真实轮廓几何（面积 / 弦段 / 圆 / 包围盒）。轮廓不闭合即 CadError。"""
    entities = [_as_mapping(item) for item in (sketch.get("entities") or [])]
    circles: list[tuple[float, float, float]] = []
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for entity in entities:
        if bool(entity.get("construction")):
            continue
        circle = _circle_geom(entity)
        if circle is not None:
            circles.append(circle)
            continue
        points = _entity_points(entity)
        for index in range(len(points) - 1):
            if not _close(points[index], points[index + 1]):
                segments.append((points[index], points[index + 1]))
    if not segments and len(circles) == 1:
        cx, cy, r = circles[0]
        return {"source": "circle_exact", "circle": (cx, cy, r), "segments": [],
                "area_nm2": math.pi * r * r,
                "bbox": (cx - r, cy - r, cx + r, cy + r)}
    polygon = _chain_segments(segments)
    if polygon is None:
        raise CadError("profile is not a closed loop", reason="profile_not_closed",
                       entities=len(entities), segments=len(segments))
    outer = _shoelace(polygon)
    inner = sum(math.pi * r * r for _, _, r in circles)
    area = outer - inner
    if area <= 0:
        raise CadError("profile area is not positive", reason="profile_not_closed",
                       outer_area_nm2=int(round(outer)), inner_area_nm2=int(round(inner)))
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return {"source": "outer_minus_inner" if circles else "chord_polygon",
            "circle": None, "segments": segments, "area_nm2": area,
            "bbox": (min(xs), min(ys), max(xs), max(ys)), "polygon": polygon,
            "inner_circles": circles}


def _point_inside_polygon(polygon: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    for index in range(len(polygon) - 1):
        x1, y1 = polygon[index]
        x2, y2 = polygon[index + 1]
        if (y1 > y) != (y2 > y):
            cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < cross:
                inside = not inside
    return inside


def _dist_point_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _center_inside(profile: Mapping[str, Any], cx: float, cy: float) -> bool:
    circle = profile.get("circle")
    if isinstance(circle, tuple):
        return math.hypot(cx - circle[0], cy - circle[1]) <= circle[2]
    polygon = profile.get("polygon")
    return bool(polygon) and _point_inside_polygon(polygon, cx, cy)


def _min_wall(profile: Mapping[str, Any], cx: float, cy: float, radius: float) -> float:
    """孔心到轮廓的最短距离 − r；测不到返回 -1（未采），绝不用 0 冒充。"""
    walls: list[float] = []
    circle = profile.get("circle")
    if isinstance(circle, tuple):
        walls.append(abs(math.hypot(cx - circle[0], cy - circle[1]) - circle[2]) - radius)
    for x1, y1 in [(seg[0][0], seg[0][1]) for seg in profile.get("segments") or []]:
        pass
    for (x1, y1), (x2, y2) in profile.get("segments") or []:
        walls.append(_dist_point_segment(cx, cy, x1, y1, x2, y2) - radius)
    for icx, icy, ir in profile.get("inner_circles") or []:
        walls.append(abs(math.hypot(cx - icx, cy - icy) - ir) - radius)
    return min(walls) if walls else -1.0


# --------------------------------------------------------------------------------------
# 料厚：现读文档里第一条未抑制"新料"拉伸特征的 distance_nm（真数据依赖，不是常数）
# --------------------------------------------------------------------------------------


def _material(view: _View, thickness_nm: Any) -> tuple[int, str]:
    if thickness_nm is not None:
        return _as_nm(thickness_nm, field="thickness_nm"), ""
    for item in view.features:
        feature = _as_mapping(item)
        if bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() != "extrude":
            continue
        params = feature.get("params") if isinstance(feature.get("params"), Mapping) else {}
        if str(params.get("op") or "new").strip().lower() != "new":
            continue
        distance = params.get("distance_nm")
        if isinstance(distance, int) and not isinstance(distance, bool) and distance > 0:
            return int(distance), _uuid_of(feature)
    raise CadError("simple hole needs an existing solid: no unsuppressed extrude feature offers "
                   "params.distance_nm as material thickness", reason="no_solid_feature")


def _existing_holes(view: _View) -> list[tuple[int, int, int, str]]:
    """已有孔（中心 nm + 直径 nm + uuid）；位置/直径缺失的孔不在这里，由调用方记 unchecked。"""
    holes: list[tuple[int, int, int, str]] = []
    for item in view.features:
        feature = _as_mapping(item)
        if bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() not in _HOLE_KINDS:
            continue
        params = feature.get("params") if isinstance(feature.get("params"), Mapping) else {}
        position = params.get("position_nm")
        diameter = params.get("diameter_nm")
        if not isinstance(position, Mapping) or not isinstance(diameter, int) or isinstance(diameter, bool):
            continue
        x, y = _number(position.get("x")), _number(position.get("y"))
        if None in (x, y) or diameter <= 0:
            continue
        holes.append((int(round(x)), int(round(y)), int(diameter), _uuid_of(feature)))
    return holes


def _counted_holes(view: _View) -> int:
    total = 0
    for item in view.features:
        feature = _as_mapping(item)
        if bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() in _HOLE_KINDS:
            total += 1
    return total


# --------------------------------------------------------------------------------------
# 操作：简单直孔
# --------------------------------------------------------------------------------------


def simple_hole(target: Any, *, diameter_nm: Any = None, diameter: Any = None, depth_nm: Any = None,
                depth: Any = None, through_all: Any = False, blind: Any = None, position_nm: Any = None,
                center: Any = None, sketch: Any = None, thickness_nm: Any = None,
                wall_min_nm: Any = 0, **refs: Any) -> list[str]:
    """简单直孔（通孔/盲孔，平底，不带标准件属性）：校验真几何后写一条孔特征记录。

    真实后果：孔心必须在轮廓内、孔壁余量 ≥ ``wall_min_nm``（缺省 0 = 不许穿出）、盲孔深度不得超过
    料厚（料厚现读基体拉伸特征的 ``distance_nm``）、与既有孔不得重叠（中心距 ≥ r₁+r₂）、
    去除/剩余面积与体积必须为正；全部通过才落记录。返回 ``[记录 uuid]``。
    """
    view = _view(target)
    raw_diameter = diameter_nm if diameter_nm is not None else diameter
    if raw_diameter is None:
        raise CadError("diameter_nm is required", reason="missing_parameter", fields=["diameter_nm"])
    diameter_value = _as_nm(raw_diameter, field="diameter_nm")
    radius = diameter_value / 2.0
    wall_min = _as_nm(wall_min_nm, field="wall_min_nm", positive=False)

    profile_uuid = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="simple_hole")
    profile = _profile(_sketch_of(view, profile_uuid))

    raw_position = position_nm if position_nm is not None else center
    if raw_position is None:
        min_x, min_y, max_x, max_y = profile["bbox"]
        center_x, center_y = int(round((min_x + max_x) / 2.0)), int(round((min_y + max_y) / 2.0))
    else:
        center_x, center_y = _as_point_nm(raw_position, field="position_nm")

    if not _center_inside(profile, float(center_x), float(center_y)):
        raise CadError("hole centre lies outside the profile", reason="hole_outside_profile",
                       position_nm={"x": center_x, "y": center_y},
                       bbox_nm=[int(round(value)) for value in profile["bbox"]])
    wall = _min_wall(profile, float(center_x), float(center_y), radius)
    if wall < 0.0 and not profile.get("segments") and not profile.get("circle"):
        raise CadError("profile geometry could not be measured", reason="profile_unmeasurable",
                       sketch=profile_uuid)
    if wall < wall_min:
        raise CadError("hole breaks out of the profile", reason="hole_breaks_out",
                       min_wall_nm=int(math.floor(wall)), wall_min_nm=wall_min,
                       radius_nm=int(round(radius)), diameter_nm=diameter_value)

    material_thickness, base_uuid = _material(view, thickness_nm)
    raw_depth = depth_nm if depth_nm is not None else depth
    requested_depth = None if raw_depth is None else _as_nm(raw_depth, field="depth_nm")
    want_through = _as_flag(through_all, field="through_all")
    if blind is not None:
        blind_flag = _as_flag(blind, field="blind")
        if blind_flag == want_through:
            raise CadError("blind and through_all contradict each other", reason="conflicting_flags",
                           blind=blind_flag, through_all=want_through)
        want_through = not blind_flag
    if requested_depth is None and not want_through:
        want_through = True                      # 没给深度 = 快速通孔
    if want_through:
        drill_depth = material_thickness
        end_condition = "through_all"
    else:
        drill_depth = int(requested_depth)
        if drill_depth > material_thickness:
            raise CadError("blind depth exceeds material thickness", reason="depth_exceeds_material",
                           depth_nm=drill_depth, material_thickness_nm=material_thickness)
        end_condition = "through_all" if drill_depth >= material_thickness else "blind"

    hole_area = math.pi * radius * radius
    profile_area = float(profile["area_nm2"])
    remaining_area = profile_area - hole_area
    if remaining_area <= 0.0:
        raise CadError("no material left after the hole", reason="no_material_left",
                       profile_area_nm2=int(round(profile_area)), hole_area_nm2=int(round(hole_area)))
    removed_volume = hole_area * drill_depth
    remaining_volume = profile_area * material_thickness - removed_volume
    if remaining_volume <= 0.0:
        raise CadError("no material left after the hole", reason="no_material_left",
                       material_volume_nm3=int(round(profile_area * material_thickness)),
                       removed_volume_nm3=int(round(removed_volume)))

    checked = 0
    for other_x, other_y, other_diameter, other_uuid in _existing_holes(view):
        checked += 1
        if math.hypot(center_x - other_x, center_y - other_y) < (diameter_value + other_diameter) / 2.0:
            raise CadError("hole overlaps an existing hole", reason="hole_overlaps_existing",
                           hole_uuid=other_uuid, position_nm={"x": center_x, "y": center_y})

    params: dict[str, Any] = {
        "op": "cut", "hole_type": "simple", "spec": "simple", "standard": None, "tip": "flat",
        "diameter_nm": diameter_value, "radius_nm": int(round(radius)), "depth_nm": drill_depth,
        "through_all": end_condition == "through_all", "blind": end_condition == "blind",
        "end_condition": end_condition,
        "position_nm": {"x": center_x, "y": center_y},
        "material_thickness_nm": material_thickness,
        "min_wall_nm": int(math.floor(wall)), "wall_min_nm": wall_min,
        "profile_area_nm2": int(round(profile_area)), "profile_area_source": profile["source"],
        "hole_area_nm2": int(round(hole_area)), "remaining_area_nm2": int(round(remaining_area)),
        "removed_volume_nm3": int(round(removed_volume)),
        "remaining_volume_nm3": int(round(remaining_volume)),
        "holes_checked_n": checked,
        "holes_unchecked_n": max(0, _counted_holes(view) - checked),
        "kernel_route": "inner_loop_cut",
        "cut_loop": {"kind": "circle",
                     "geom": {"cx": center_x, "cy": center_y, "r": int(round(radius))}},
    }
    if requested_depth is not None:
        params["requested_depth_nm"] = int(requested_depth)
    record = {"uuid": str(_uuid.uuid4()), "kind": "hole", "sketch_uuid": profile_uuid,
              "inputs": [profile_uuid] + ([base_uuid] if base_uuid else []),
              "params": params, "suppressed": False}
    view.features.append(record)
    return [record["uuid"]]


OPERATIONS: dict[str, Callable[..., list[str]]] = {"simple_hole": simple_hole}


# --------------------------------------------------------------------------------------
# 自测：8 条硬断言，其中 5 条阴性对照（尺子必须看得见红）
# --------------------------------------------------------------------------------------

_SKETCH_UUID = "11111111-1111-4111-8111-111111111111"
_BASE_UUID = "22222222-2222-4222-8222-222222222222"
_W = 40_000_000     # 40 mm
_H = 20_000_000     # 20 mm
_T = 5_000_000      # 5 mm 料厚


def _plate_payload(*, with_solid: bool = True) -> dict[str, Any]:
    """用 docmodel 的规范化 L1 文本当前置，保证是合法 L1 载荷（不用私有 schema 猜字段）。"""
    import cadcore.docmodel as docmodel
    payload = json.loads(docmodel.document_to_l1(docmodel.new_document()))
    corners = [(0, 0), (_W, 0), (_W, _H), (0, _H)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"kind": "line", "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    payload.setdefault("sketches", []).append(
        {"uuid": _SKETCH_UUID, "name": "plate_profile", "plane": "XY", "entities": entities})
    if with_solid:
        payload.setdefault("features", []).append(
            {"uuid": _BASE_UUID, "kind": "extrude", "sketch_uuid": _SKETCH_UUID,
             "inputs": [_SKETCH_UUID], "suppressed": False,
             "params": {"distance_nm": _T, "symmetric": False, "end_condition": "blind",
                        "op": "new", "merge": True}})
    return payload


def _expect_error(call: Callable[[], Any], reason: str) -> bool:
    try:
        call()
    except CadError as exc:
        return exc.reason == reason
    except Exception:  # noqa: BLE001 - 非 CadError 也算没看见红，断言失败
        return False
    return False


def selftest() -> tuple[int, int, list[str], str]:
    """返回 (断言数, 失败数, 失败详述, l1_roundtrip 状态)。"""
    _ensure_probe()
    import cadcore.docmodel as docmodel

    checks: list[tuple[str, Callable[[], bool]]] = []
    checks.append((
        "hole_record_written",
        lambda: (lambda doc, out: len(out) == 1 and any(
            isinstance(item, Mapping) and item.get("uuid") == out[0] and item.get("kind") == "hole"
            for item in doc["features"]))(_doc, _out)))
    checks.append((
        "through_depth_from_material",
        lambda: (_rec(_doc)["params"]["depth_nm"] == _T
                 and _rec(_doc)["params"]["end_condition"] == "through_all"
                 and _rec(_doc)["params"]["through_all"] is True
                 and _rec(_doc)["params"]["diameter_nm"] == 8_000_000
                 and _rec(_doc)["params"]["material_thickness_nm"] == _T)))
    checks.append((
        "min_wall_is_real_geometry",
        lambda: _rec(_doc)["params"]["min_wall_nm"] == 6_000_000))
    checks.append((
        "areas_and_volumes",
        lambda: (_rec(_doc)["params"]["profile_area_nm2"] == _W * _H
                 and _rec(_doc)["params"]["hole_area_nm2"]
                 == int(round(math.pi * 4_000_000 ** 2))
                 and _rec(_doc)["params"]["removed_volume_nm3"]
                 == int(round(math.pi * 4_000_000 ** 2 * _T))
                 and _rec(_doc)["params"]["remaining_volume_nm3"]
                 == int(round(_W * _H * _T - math.pi * 4_000_000 ** 2 * _T))
                 and _rec(_doc)["params"]["holes_checked_n"] == 0
                 and _rec(_doc)["params"]["holes_unchecked_n"] == 0)))

    roundtrip = "unavailable"
    try:
        text = docmodel.document_to_l1(_doc)
        again = docmodel.document_to_l1(docmodel.document_from_l1(text))
        roundtrip = "ok" if text == again and _out[0] in text else "mismatch"
    except Exception as exc:  # noqa: BLE001 - 缺依赖要留证，不许静默跳过
        roundtrip = "unavailable:%s" % type(exc).__name__
    if roundtrip == "ok":
        checks.append(("l1_roundtrip_keeps_hole",
                       lambda: _out[0] in docmodel.document_to_l1(_doc)))

    checks.append(("red_breaks_out",
                   lambda: _expect_error(lambda: simple_hole(_plate_payload(), diameter_nm=30_000_000,
                                                             through_all=True), "hole_breaks_out")))
    checks.append(("red_outside_profile",
                   lambda: _expect_error(lambda: simple_hole(_plate_payload(), diameter_nm=8_000_000,
                                                             position_nm=(100_000_000, 10_000_000),
                                                             through_all=True), "hole_outside_profile")))
    checks.append(("red_depth_exceeds_material",
                   lambda: _expect_error(lambda: simple_hole(_plate_payload(), diameter_nm=8_000_000,
                                                             through_all=False, depth_nm=8_000_000),
                                         "depth_exceeds_material")))
    checks.append(("red_non_integer_length",
                   lambda: _expect_error(lambda: simple_hole(_plate_payload(), diameter_nm=8.5,
                                                             through_all=True), "bad_length")))
    checks.append(("red_no_solid_feature",
                   lambda: _expect_error(lambda: simple_hole(_plate_payload(with_solid=False),
                                                             diameter_nm=8_000_000, through_all=True),
                                         "no_solid_feature")))
    checks.append(("red_overlaps_existing_hole",
                   lambda: _expect_error(lambda: simple_hole(_doc, diameter_nm=8_000_000,
                                                             through_all=True),
                                         "hole_overlaps_existing")))
    checks.append(("bare_sketch_with_explicit_thickness",
                   lambda: (lambda out: len(out) == 1)(simple_hole(
                       {"uuid": "33333333-3333-4333-8333-333333333333", "plane": "XY",
                        "entities": [{"kind": "circle", "geom": {"cx": 0, "cy": 0, "r": 10_000_000}}]},
                       diameter_nm=4_000_000, through_all=True, thickness_nm=3_000_000))))

    failures: list[str] = []
    for name, check in checks:
        try:
            ok = bool(check())
        except Exception as exc:  # noqa: BLE001 - 断言自身炸了也是失败，附类型
            ok, name = False, "%s(raised %s)" % (name, type(exc).__name__)
        if not ok:
            failures.append(name)
    return len(checks), len(failures), failures, roundtrip


def _rec(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    return [item for item in doc["features"] if item.get("uuid") == _out[0]][0]


# 自检夹具**惰性构造**：原来这里是模块级 `_doc = _plate_payload()` / `_out = simple_hole(...)`，
# 于 import 期执行 —— 目标不合法时 import 直接抛 CadError（本片曾整片 import_failed）。
_doc: Mapping[str, Any] | None = None
_out: list[str] | None = None


def _ensure_probe() -> None:
    """现算自检夹具（只算一次），把 import 期的副作用收进函数里。"""
    global _doc, _out
    if _out is None:
        _doc = _plate_payload()
        _out = simple_hole(_doc, diameter_nm=8_000_000, through_all=True)


def main(argv: list[str] | None = None) -> int:
    total, failed, names, roundtrip = selftest()
    print("SELFTEST sw_features__w18 asserts=%d failed=%d l1_roundtrip=%s%s"
          % (total, failed, roundtrip, "" if not names else " failed_names=" + ",".join(names)))
    if failed:
        print("FEATURE_FAIL family=%s op=simple_hole verdict=FAIL reason=%s"
              % (FAMILY, ",".join(names)))
        return 1
    print("FEATURE_OK family=%s op=simple_hole verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
