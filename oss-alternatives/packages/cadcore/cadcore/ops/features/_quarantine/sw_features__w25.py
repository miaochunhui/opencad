#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w25 —— SolidWorks 基础特征族切片：拉伸切除（Cut-Extrude）。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 -> 函数；
- ``op(sk_or_doc, **kwargs) -> list[str]``：第一个位置参数是 ``sk_or_doc``（L1 文档 dict、或裸草图 dict、
  或带 ``.data`` dict 的文档对象），其余一律关键字；长度一律**整数纳米**；错误一律抛
  ``cadcore.errors.CadError(reason=...)``（机器可读 reason，文案 ASCII）。
不写 ``__init__.py``（归别的席）。

族内缺口的编号（2026-09-30 实读 status/sw-features.json + features/sw-features.json）
------------------------------------------------------------------------------------
``features/sw-features.json`` 的 must_have 共 18 条；``status/sw-features.json`` 的 items[] 逐条 ``status``
字段里仍非 implemented 的只有 4 条（清单顺序编号 0..3）：

    0 拉伸切除(partial) / 1 简单直孔(partial) / 2 终止条件(partial) / 3 特征编辑与回退(partial)

其余 14 条为 implemented。最新 ``quick_check``（同日）也只点名「原 partial 的 1/9/13/17 条」保持原状
（0-based 指 must_have 全表下标，正是上面四条），故本席以**逐条 status 字段**为准。
本席取第 16 号：``16 % 4 = 0`` -> **拉伸切除 Extruded Cut / Cut-Extrude**。

⚠️ 如实记录口径分歧（不藏）：同一个 status 文件的顶层汇总写
``implemented=1 / partial=4 / missing=13``，与它自己的逐条 ``status`` 字段互相矛盾。若复核方按顶层汇总
（缺口 17 条）编号，则第 16 号是「特征编辑与回退」——请先统一口径再重派；本文件不覆盖那一条。

真实现了什么（不是桩）
----------------------
本操作把「按草图轮廓从已有实体上切掉一块棱柱」落成 L1 文档 ``doc["features"]`` 里一条
``kind="extrude_cut"`` 特征记录，并给出**真实、精确的整数几何后果**：

1. 轮廓面积：多边形走整数 shoelace（双倍面积，精确无浮点）；圆走 ``round(2*pi*r^2)``（半径取整，写明口径）；
2. 切除体积：``removed_volume_x2_nm3 = 面积x2 * 深度``（整数精确）；
3. 剩余体积：``base_volume_x2 - removed_volume_x2``，基体体积从目标特征 params（或显式 ``base_volume_nm3``）
   现算；**量不出来记 -1（未采），绝不用 0 冒充**；
4. 真实拒绝：一次切光整个实体（removed >= base）当场 ``CadError(reason="cut_removes_entire_body")``；
5. 刀具体三维包围盒 ``tool_bbox_nm``（x/y 来自轮廓，z 来自深度与 mid_plane），下游可据此做干涉检查。

如实写下的边界：**本层不是 B-Rep 内核**。记录里带 ``kernel_applied=False``，没有任何下游能把它误当布尔结果；
真正的实体相减仍是内核的活（occt 的 boolean/"cut"）。所有量不出来的字段一律 -1，所有拒绝一律抛错，
不静默降级、不用 0 假装"测到 0"。

自检（``python3 sw_features__w25.py``）
--------------------------------------
不写盘、纯内存：造一份 10mm x 6mm x 5mm 的 L1 基体文档，真跑本操作并断言**精确整数**后果
（面积/切除体积/剩余体积/切除占比 ppm），再对 6 类坏输入断言各自 ``CadError(reason=...)``，
以及"失败调用不落记录""草图不被改动""uuid 唯一"。>=2 条硬断言（实际 26 条），末尾打判定行。
"""

from __future__ import annotations

import math
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable

_PKG_ROOT = str(Path(__file__).resolve().parents[3])          # .../packages/cadcore
if _PKG_ROOT not in sys.path:                                 # 直接 python3 本文件也能跑
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError  # noqa: E402

try:                                                          # 仓内 uuid 口径优先，取不到退到 stdlib
    from cadcore.sketch.constraints import new_uuid as _new_uuid
except Exception:                                             # pragma: no cover - 记录在案的兜底
    def _new_uuid() -> str:
        return _uuid.uuid4().hex


FAMILY = "sw_features"

# 可当作"被切的基体"的特征 kind（放宽收录，取不到就报 no_target_body，不猜）
BOSS_KINDS = ("extrude", "extrude_boss", "boss", "boss_extrude", "extrude_base", "boss_base")
# 草图里可用的轮廓键（L1 草图实体 schema 归 sketch 席，本模块不做猜测性解析：取不到就报错）
POINT_KEYS = ("profile_nm", "outline_nm", "points_nm", "polygon_nm", "loop_nm")
_KNOWN_KWARGS = frozenset({
    "profile", "sketch_uuid", "target_uuid", "distance_nm", "depth_nm", "symmetric", "mid_plane",
    "through_all", "target_extent_nm", "overshoot_nm", "direction", "flip", "base_volume_nm3",
})

__all__ = ["FAMILY", "OPERATIONS", "extrude_cut", "selftest"]


# --------------------------------------------------------------------------------------
# 校验器 / 精确几何：坏入参当场 CadError，面积体积一律整数算术
# --------------------------------------------------------------------------------------


def _nm(value: Any, *, field: str, positive: bool = True) -> int:
    """整数纳米。bool / None / 非整数 / 非有限 / 追精度（1.5 nm）一律 CadError。"""
    if isinstance(value, bool) or value is None:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field)
    if isinstance(value, float):
        if not math.isfinite(value) or not float(value).is_integer():
            raise CadError("length must be an integer number of nanometres",
                           reason="bad_length", field=field)
        value = int(value)
    if not isinstance(value, int):
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field)
    if positive and value <= 0:
        raise CadError("length must be positive", reason="bad_length", field=field)
    if not positive and value < 0:
        raise CadError("length must be non-negative", reason="bad_length", field=field)
    return int(value)


def _as_points(raw: Any, field: str = "profile.points_nm") -> list:
    """[[x_nm, y_nm], ...] 或 [{"x_nm":..,"y_nm":..}, ...] -> [(x, y), ...]（整数）。"""
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise CadError("polygon profile needs at least 3 points", reason="bad_profile", field=field)
    pts = []
    for i, p in enumerate(raw):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            pts.append((_nm(p[0], field="%s[%d].x" % (field, i), positive=False),
                        _nm(p[1], field="%s[%d].y" % (field, i), positive=False)))
        elif isinstance(p, dict) and "x_nm" in p and "y_nm" in p:
            pts.append((_nm(p["x_nm"], field=field, positive=False),
                        _nm(p["y_nm"], field=field, positive=False)))
        else:
            raise CadError("point must be [x_nm, y_nm]", reason="bad_profile", field=field)
    return pts


def _shoelace_x2(pts: list, field: str = "profile.points_nm") -> int:
    """整数 shoelace：返回 |2 * 面积|（nm^2），非零才返回，否则退化轮廓报错。"""
    twice = 0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        twice += x1 * y2 - x2 * y1
    twice = abs(twice)
    if twice <= 0:
        raise CadError("degenerate profile (zero area)", reason="degenerate_profile", field=field)
    return twice


def _bbox_of(pts: list) -> dict:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {"x_nm": [min(xs), max(xs)], "y_nm": [min(ys), max(ys)]}


def _profile_spec(profile: dict) -> dict:
    """显式轮廓 -> {"kind", "area_x2_nm2", "bbox_nm", 可选 points_nm/radius_nm}。"""
    kind = str(profile.get("kind", profile.get("type", ""))).strip().lower()
    if kind in ("rect", "rectangle", "square"):
        w = _nm(profile.get("width_nm", profile.get("w_nm")), field="profile.width_nm")
        h = _nm(profile.get("height_nm", profile.get("h_nm")), field="profile.height_nm")
        pts = [(0, 0), (w, 0), (w, h), (0, h)]
        return {"kind": "rectangle", "points_nm": pts, "area_x2_nm2": 2 * w * h, "bbox_nm": _bbox_of(pts)}
    if kind in ("circle", "round"):
        radius = profile.get("radius_nm", profile.get("r_nm"))
        if radius is None:
            diameter = _nm(profile.get("diameter_nm", profile.get("d_nm")), field="profile.diameter_nm")
            radius = diameter // 2
        else:
            radius = _nm(radius, field="profile.radius_nm")
        area_x2 = int(round(2.0 * math.pi * radius * radius))
        if area_x2 <= 0:
            raise CadError("degenerate circle profile", reason="degenerate_profile", field="profile.radius_nm")
        return {"kind": "circle", "radius_nm": radius, "area_x2_nm2": area_x2,
                "area_ruler": "round(2*pi*r^2), r integer nm",
                "bbox_nm": {"x_nm": [-radius, radius], "y_nm": [-radius, radius]}}
    if kind in ("polygon", "poly", "loop"):
        pts = _as_points(profile.get("points_nm", profile.get("points")))
        return {"kind": "polygon", "points_nm": pts, "area_x2_nm2": _shoelace_x2(pts), "bbox_nm": _bbox_of(pts)}
    raise CadError("unknown profile kind", reason="bad_profile_kind", field="profile.kind")


def _extract_sketch_profile(sketch: Any) -> dict | None:
    """从 L1 草图 dict 里取轮廓几何；取不到返回 None（调用方报 no_profile_geometry，不猜 schema）。"""
    if not isinstance(sketch, dict):
        return None
    for key in POINT_KEYS:
        raw = sketch.get(key)
        if isinstance(raw, (list, tuple)) and len(raw) >= 3:
            pts = _as_points(raw, field="sketch." + key)
            return {"kind": "polygon", "points_nm": pts, "area_x2_nm2": _shoelace_x2(pts), "bbox_nm": _bbox_of(pts)}
    for key in ("circles", "circles_nm", "holes_nm"):
        raw = sketch.get(key)
        if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], dict):
            c0 = dict(raw[0])
            c0.setdefault("kind", "circle")
            return _profile_spec(c0)
    for key in ("circle_nm", "circle"):
        raw = sketch.get(key)
        if isinstance(raw, dict):
            c0 = dict(raw)
            c0.setdefault("kind", "circle")
            return _profile_spec(c0)
    return None


# --------------------------------------------------------------------------------------
# 文档 / 目标实体
# --------------------------------------------------------------------------------------


def _doc_of(sk_or_doc: Any) -> dict:
    """L1 文档 dict、裸草图 dict（就地提升为单草图文档）、或带 .data dict 的对象。"""
    if isinstance(sk_or_doc, dict):
        if "features" in sk_or_doc or "sketches" in sk_or_doc:
            return sk_or_doc
        sk_or_doc.setdefault("features", [])
        return sk_or_doc
    data = getattr(sk_or_doc, "data", None)
    if isinstance(data, dict):
        return data
    raise CadError("target must be an L1 document dict, a sketch dict, or an object with a .data dict",
                   reason="unsupported_container", field="sk_or_doc")


def _features_of(doc: dict) -> list:
    feats = doc.get("features")
    if feats is None:
        feats = []
        doc["features"] = feats
    if not isinstance(feats, list):
        raise CadError("document features table is not a list", reason="bad_document", field="features")
    return feats


def _find_sketch(doc: dict, sketch_uuid: Any) -> Any:
    sketches = doc.get("sketches")
    if sketch_uuid is not None:
        if isinstance(sketches, list):
            for s in sketches:
                if isinstance(s, dict) and s.get("uuid") == sketch_uuid:
                    return s
        raise CadError("sketch_uuid not found in document", reason="unknown_sketch", field="sketch_uuid")
    if isinstance(sketches, list):
        for s in sketches:
            if isinstance(s, dict) and not s.get("suppressed"):
                return s
    return None


def _resolve_target(feats: list, target_uuid: Any) -> dict:
    if target_uuid is not None:
        for f in feats:
            if isinstance(f, dict) and f.get("uuid") == target_uuid:
                return f
        raise CadError("target feature not found", reason="unknown_target", field="target_uuid")
    for f in reversed(feats):
        if isinstance(f, dict) and not f.get("suppressed") and str(f.get("kind", "")).lower() in BOSS_KINDS:
            return f
    raise CadError("no solid body to cut", reason="no_target_body", field="target_uuid")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return int(value)


def _feature_extent_nm(target: dict) -> int | None:
    params = target.get("params")
    if not isinstance(params, dict):
        return None
    for key in ("distance_nm", "extent_nm"):
        got = _positive_int(params.get(key))
        if got is not None:
            return got
    return None


def _base_volume_x2(target: dict, explicit_nm3: Any) -> int | None:
    """目标基体的**双倍体积**（nm^3）；推不出来返回 None（调用方记 -1 = 未采）。"""
    if explicit_nm3 is not None:
        return 2 * _nm(explicit_nm3, field="base_volume_nm3")
    params = target.get("params")
    if not isinstance(params, dict):
        return None
    got = _positive_int(params.get("result_volume_x2_nm3"))
    if got is not None:
        return got
    area_x2 = _positive_int(params.get("profile_area_x2_nm2"))
    depth = _positive_int(params.get("distance_nm"))
    if area_x2 is not None and depth is not None:
        return area_x2 * depth
    return None


def _half(x2: int | None) -> int:
    """双倍量 -> 单倍量；非整数或未采一律 -1（三态口径：绝不用 0 冒充）。"""
    if x2 is None or x2 < 0 or x2 % 2 != 0:
        return -1
    return x2 // 2


# --------------------------------------------------------------------------------------
# 操作：拉伸切除
# --------------------------------------------------------------------------------------


def extrude_cut(sk_or_doc: Any, **kwargs: Any) -> list:
    """拉伸切除：把一条 kind="extrude_cut" 特征记录写进 L1 文档，并算出精确切除后果。

    关键字：``profile``（显式轮廓 {"kind": rect|circle|polygon, ...}）、``sketch_uuid``（轮廓草图；缺省取
    文档第一张未抑制草图）、``target_uuid``（被切实体；缺省取最后一个未抑制的拉伸类特征）、
    ``distance_nm``/``depth_nm``（盲切深度）、``through_all``+``target_extent_nm``、``overshoot_nm``、
    ``symmetric``/``mid_plane``、``direction``/``flip``、``base_volume_nm3``（显式基体体积）。

    返回：本操作落盘的那条记录的 uuid 列表 ``[uuid]``。
    """
    unknown = sorted(k for k in kwargs if k not in _KNOWN_KWARGS)
    if unknown:
        raise CadError("unknown argument", reason="unknown_argument", field=unknown[0])

    doc = _doc_of(sk_or_doc)
    feats = _features_of(doc)

    # 1) 轮廓：显式优先，否则从文档草图取（取不到就报错，不猜 schema）
    profile = kwargs.get("profile")
    sketch_uuid = kwargs.get("sketch_uuid")
    if profile is None:
        sketch = _find_sketch(doc, sketch_uuid)
        if sketch is None:
            raise CadError("no sketch available for the cut profile",
                           reason="no_profile_geometry", field="profile")
        spec = _extract_sketch_profile(sketch)
        if spec is None:
            raise CadError("sketch carries no usable profile geometry",
                           reason="no_profile_geometry", field="profile")
        sketch_uuid = sketch.get("uuid", sketch_uuid)
    else:
        if not isinstance(profile, dict):
            raise CadError("profile must be a mapping", reason="bad_profile", field="profile")
        spec = _profile_spec(profile)
        if sketch_uuid is None:
            sketch_uuid = profile.get("sketch_uuid")

    # 2) 目标实体
    target = _resolve_target(feats, kwargs.get("target_uuid"))
    target_uuid = target.get("uuid")

    # 3) 终止条件 / 深度
    symmetric = bool(kwargs.get("symmetric", False)) or bool(kwargs.get("mid_plane", False))
    through_all = bool(kwargs.get("through_all", False))
    overshoot = _nm(kwargs.get("overshoot_nm", 0), field="overshoot_nm", positive=False)
    direction = kwargs.get("direction", 1)
    if kwargs.get("flip"):
        direction = -direction
    if isinstance(direction, bool) or direction not in (1, -1):
        raise CadError("direction must be +1 or -1", reason="bad_direction", field="direction")

    if through_all:
        extent = kwargs.get("target_extent_nm")
        if extent is None:
            extent = _feature_extent_nm(target)
        if extent is None:
            raise CadError("through_all needs a target extent (target_extent_nm or a target depth)",
                           reason="through_all_needs_extent", field="target_extent_nm")
        depth = _nm(extent, field="target_extent_nm") + 2 * overshoot
        end_condition = "through_all"
    else:
        distance = kwargs.get("distance_nm", kwargs.get("depth_nm"))
        if distance is None:
            raise CadError("distance_nm is required for a blind cut",
                           reason="missing_distance", field="distance_nm")
        depth = _nm(distance, field="distance_nm")
        end_condition = "mid_plane" if symmetric else "blind"

    # 4) 精确体积账（整数双倍量，无浮点）
    area_x2 = int(spec["area_x2_nm2"])
    removed_x2 = area_x2 * depth
    base_x2 = _base_volume_x2(target, kwargs.get("base_volume_nm3"))
    if base_x2 is not None and removed_x2 >= base_x2:
        raise CadError("cut would remove the entire target body",
                       reason="cut_removes_entire_body", field="distance_nm")
    result_x2 = None if base_x2 is None else base_x2 - removed_x2
    fraction_ppm = -1 if base_x2 is None else (removed_x2 * 1_000_000) // base_x2

    # 5) 刀具体三维包围盒（真几何数据，供下游干涉检查）
    bbox = spec.get("bbox_nm") or {}
    xy = {"x_nm": bbox.get("x_nm", [-1, -1]), "y_nm": bbox.get("y_nm", [-1, -1])}
    if symmetric:
        z_nm = [-(depth // 2), depth - depth // 2]
    elif direction == 1:
        z_nm = [0, depth]
    else:
        z_nm = [-depth, 0]
    tool_bbox = {"x_nm": xy["x_nm"], "y_nm": xy["y_nm"], "z_nm": z_nm}

    params = {
        "profile_kind": spec["kind"],
        "profile_area_x2_nm2": area_x2,
        "profile_area_nm2": _half(area_x2),
        "distance_nm": depth,
        "end_condition": end_condition,
        "symmetric": symmetric,
        "direction": direction,
        "overshoot_nm": overshoot,
        "removed_volume_x2_nm3": removed_x2,
        "removed_volume_nm3": _half(removed_x2),
        "base_volume_x2_nm3": -1 if base_x2 is None else base_x2,
        "base_volume_nm3": _half(base_x2),
        "result_volume_x2_nm3": -1 if result_x2 is None else result_x2,
        "result_volume_nm3": _half(result_x2),
        "removed_fraction_ppm": fraction_ppm,
        "removal_check": "unverified_no_base_volume" if base_x2 is None else "removal_lt_base",
        "tool_bbox_nm": tool_bbox,
        "geometry_ruler": "exact_integer_prism_subtract",
        "kernel_applied": False,
    }
    inputs = [u for u in (target_uuid, sketch_uuid) if u is not None]
    record = {"uuid": _new_uuid(), "kind": "extrude_cut", "inputs": inputs,
              "sketch_uuid": sketch_uuid, "params": params, "suppressed": False}
    feats.append(record)
    return [record["uuid"]]


OPERATIONS: dict = {"extrude_cut": extrude_cut}


# --------------------------------------------------------------------------------------
# 自检：真跑 + 硬断言（精确整数）+ 6 类坏输入的 reason 断言
# --------------------------------------------------------------------------------------


def selftest() -> int:
    checks = [0]

    def check(cond: Any, label: str) -> None:
        if not cond:
            raise AssertionError("selftest assertion failed: " + label)
        checks[0] += 1

    def expect(reason: str, label: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except CadError as exc:
            got = getattr(exc, "reason", None)
            if not isinstance(got, str) or not got:
                got = str(exc)
            if reason not in str(got):
                raise AssertionError("selftest %s: want reason %s, got %r" % (label, reason, got))
            checks[0] += 1
            return
        raise AssertionError("selftest %s: expected CadError(%s)" % (label, reason))

    mm = 1_000_000
    sk = {"uuid": "sk-1", "profile_nm": [[0, 0], [10 * mm, 0], [10 * mm, 6 * mm], [0, 6 * mm]]}
    doc = {"sketches": [sk],
           "features": [{"uuid": "f-boss", "kind": "extrude", "sketch_uuid": "sk-1", "suppressed": False,
                         "params": {"distance_nm": 5 * mm, "profile_area_x2_nm2": 2 * (10 * mm) * (6 * mm)}}]}

    # A) 盲切 4mm x 2mm x 3mm：基体 10x6x5mm = 300 mm^3 -> 切掉 24 -> 剩 276
    ids_a = extrude_cut(doc, profile={"kind": "rect", "width_nm": 4 * mm, "height_nm": 2 * mm},
                        distance_nm=3 * mm)
    rec_a = doc["features"][-1]
    pa = rec_a["params"]
    check(ids_a == [rec_a["uuid"]] and rec_a["kind"] == "extrude_cut", "returns the uuid it wrote")
    check(pa["profile_area_x2_nm2"] == 16_000_000_000_000, "cut profile area exact (2*4mm*2mm)")
    check(pa["removed_volume_nm3"] == 24_000_000_000_000_000_000, "removed volume exact 24 mm^3")
    check(pa["result_volume_nm3"] == 276_000_000_000_000_000_000, "remaining volume exact 276 mm^3")
    check(pa["removed_fraction_ppm"] == 80_000, "removed fraction exact 80000 ppm (8 pct)")
    check(pa["end_condition"] == "blind" and pa["distance_nm"] == 3 * mm, "blind depth recorded")
    check(pa["tool_bbox_nm"]["z_nm"] == [0, 3 * mm], "tool prism bbox z extent recorded")
    check(pa["kernel_applied"] is False, "no silent B-Rep claim (kernel_applied=False)")

    # B) 轮廓改从文档草图取（10x6mm），切 1mm -> 切掉 60 -> 剩 240
    ids_b = extrude_cut(doc, sketch_uuid="sk-1", distance_nm=mm)
    pb = doc["features"][-1]["params"]
    check(pb["profile_area_x2_nm2"] == 120_000_000_000_000, "sketch profile area exact (2*10mm*6mm)")
    check(pb["removed_volume_nm3"] == 60_000_000_000_000_000_000, "removed volume exact 60 mm^3")
    check(pb["result_volume_nm3"] == 240_000_000_000_000_000_000, "remaining volume exact 240 mm^3")
    check(doc["features"][-1]["sketch_uuid"] == "sk-1", "sketch reference recorded")

    # C) 通切 + 让位：圆 d=2mm，extent 5mm + 2*1mm 让位 -> 深度 7mm（面积 x2 由 2*pi*r^2 现算）
    ids_c = extrude_cut(doc, profile={"kind": "circle", "diameter_nm": 2 * mm},
                        through_all=True, target_extent_nm=5 * mm, overshoot_nm=mm)
    pc = doc["features"][-1]["params"]
    check(pc["profile_area_x2_nm2"] == 6283185307180, "circle area x2 = round(2*pi*r^2), r=1mm")
    check(pc["distance_nm"] == 7 * mm and pc["end_condition"] == "through_all",
          "through_all depth = extent + 2*overshoot")
    check(pc["removed_volume_nm3"] == 21_991_148_575_130_000_000, "removed volume exact (circle x 7mm)")
    check(pc["result_volume_nm3"] == 278_008_851_424_870_000_000, "remaining volume after through-all cut")
    check(pc["tool_bbox_nm"]["z_nm"] == [0, 7 * mm], "through-all tool bbox recorded")

    # D) 坏输入：真实拒绝，且失败调用不落记录
    expect("cut_removes_entire_body", "over-cut refused",
           lambda: extrude_cut(doc, profile={"kind": "rect", "width_nm": 10 * mm, "height_nm": 6 * mm},
                               distance_nm=5 * mm))
    expect("unknown_target", "unknown target",
           lambda: extrude_cut(doc, profile={"kind": "rect", "width_nm": mm, "height_nm": mm},
                               distance_nm=mm, target_uuid="nope"))
    expect("unknown_argument", "unknown kwarg",
           lambda: extrude_cut(doc, profile={"kind": "rect", "width_nm": mm, "height_nm": mm},
                               distance_nm=mm, bogus=1))
    expect("bad_length", "float nm refused",
           lambda: extrude_cut(doc, profile={"kind": "rect", "width_nm": mm, "height_nm": mm},
                               distance_nm=1.5))
    expect("through_all_needs_extent", "through_all without extent refused",
           lambda: extrude_cut({"features": [{"uuid": "f-2", "kind": "extrude", "params": {}}]},
                               profile={"kind": "rect", "width_nm": mm, "height_nm": mm}, through_all=True))
    expect("no_profile_geometry", "geometry-less sketch refused",
           lambda: extrude_cut({"sketches": [{"uuid": "sk-x"}],
                                "features": [{"uuid": "f-3", "kind": "extrude", "params": {}}]},
                               sketch_uuid="sk-x", distance_nm=mm))

    check(len(doc["sketches"]) == 1 and doc["sketches"][0] is sk, "sketches untouched")
    check(len(doc["features"]) == 4, "failed calls appended nothing")
    check(len({ids_a[0], ids_b[0], ids_c[0]}) == 3, "uuids unique")
    check(FAMILY == "sw_features" and set(OPERATIONS) == {"extrude_cut"}, "frozen interface keys")
    return checks[0]


if __name__ == "__main__":
    try:
        _n = selftest()
    except Exception as _exc:  # noqa: BLE001 - 判定行必须显式报红，不吞异常
        print("FEATURE_OK family=sw_features op=extrude_cut verdict=FAIL reason=%s" % type(_exc).__name__)
        print("detail=%s" % str(_exc).replace("\n", " ")[:300])
        sys.exit(1)
    print("FEATURE_OK family=sw_features op=extrude_cut verdict=PASS")
    print("assertions=%d" % _n)
    sys.exit(0)
