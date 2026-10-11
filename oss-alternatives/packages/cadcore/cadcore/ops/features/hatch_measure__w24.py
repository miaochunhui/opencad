#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w24 —— 填充与查询族切片：通用测量统一入口 + 悬停快速读数。

一句话用途
----------
本切片负责 ``autocad-hatch-measure`` 族 must_have 清单里**仍 partial 的两条中的第 2 条**
（清单序 #16 / 剩余序 #1，本席编号 15 ⇒ 15 % 2 = 1）："通用测量（含悬停快速读数）"。
旧审计（``docs/research/cad-parity/status/autocad-hatch-measure.json``）点名它缺两样：

1. **内核侧没有统一入口** ``measure_geom`` —— 只有 ``hatch_measure.measure_area`` /
   ``measure_perimeter`` / ``measure_distance`` 三条分列 op，没有"一个入口按操作数分发"；
2. **"悬停/实时快速读数"没有任何产生方** —— apps 侧 grep ``hover|悬停|MEASUREGEOM`` 0 命中，
   内核侧也没有"给一个光标点、返回即时读数"的 op。

本文件补**第 1 条整条 + 第 2 条的内核侧产生方**：``measure_geom``（统一分发）与
``hover_readout``（光标命中 → 即时读数）。

真几何 / 真数据后果（不是桩）
----------------------------
- ``hover_readout`` 做的是**真命中测试**：光标点到每条实体折线段的最近距离（线段投影 + 端点夹取）、
  到最近点的坐标、闭实体（circle / 首末闭合 polyline）的**区域包含判定**（``_point_in_loop`` 射线法）；
- 读数取**解析式**：长度复用族模块 ``_curve_length``（圆 2πr、圆弧 r·sweep、折线/直线精确端点），
  圆的面积/半径由长度解析反推（``r = L / 2π``、``A = πr²`` —— 不数离散多边形），
  闭合折线面积用精确 ``_shoelace``，直线/折线方向角用端点 ``atan2``；
- 统一入口的 distance / area 两条腿**直接复用族模块的 measure_distance / measure_area**（单一真源，
  不抄第二份算法），只是加上 ``mode=`` 前缀并统一入参把关。

口径与三态（沿用全仓约定，别自己发明）
------------------------------------
- ``0 = 真为零``（``distance_nm=0`` = 光标正落在实体上；``hit=0`` = 没命中）；
- ``-1 = 未采``（本切片不采任何量 ⇒ 不产生 -1）；
- ``NA = 不适用``（``area_nm2=NA`` = 该实体没有面积，例如 line）；
- 三种状态**不许同形**。空草图 ⇒ ``hit=0`` 且 ``distance_nm=NA``（真为零与不适用分开表达），
  不是"静默返回 0"。

L1 表达能力的边界（如实写清，做不到的不许用假几何冒充）
----------------------------------------------------
- **L1 实体字段白名单没有"当前光标 / UI 状态"** ⇒ 本层交付的是**读数产生方**（纯函数：点 + 文档 → 数值
  token），**不是** UI 悬停交互本身。apps 侧那个"鼠标移到实体上就冒提示框"的消费方**不在本切片**
  （也不在本仓内核的范围），该项在族清单里因此**只到 partial 的内核半边**：谁要判"悬停"整条
  implemented，必须另找 apps 侧现读证据 —— 本文件不替它背书。
- **不做跨实体成环的区域悬停**：光标落在"4 条独立 line 围出的矩形内部"时，``inside=0``（那不是一个
  闭实体）。区域级"点拾取"由族模块 ``boundary_from_point`` 负责，本切片不重复实现、也不猜。
- **不做文本 readout**：i18n 门把"非 docstring 的含汉字字符串常量"计为 hardcoded_literals，且
  面向用户文案走语言包 ⇒ 本层只产机读 token，展示文案由消费方按语言包拼。

冻结接口
--------
- ``FAMILY = "hatch_measure"``；
- ``OPERATIONS: dict[str, Callable]``：``measure_geom``（统一入口，别名 ``measure``）、
  ``hover_readout``（悬停读数）；
- 每个 op ``op(sk_or_doc, **kwargs) -> list[str]``；测量/查询是**只读**的（不建表、不写入、不落实体），
  一律返回 ``"key=value"`` 结果 token；错误抛 ``cadcore.errors.CadError(reason=...)``。

token 顺序（逐 op 冻结，下游可按下标取值）
----------------------------------------
``measure_geom``：
- ``mode=hover``：与 ``hover_readout`` 同形（见下）；
- ``mode=distance``：``mode`` + 族模块 ``measure_distance`` 的 5 个 token
  （``distance_nm`` / ``dx_nm`` / ``dy_nm`` / ``nearest_a_nm=x,y`` / ``nearest_b_nm=x,y``）；
- ``mode=area``：``mode`` + 族模块 ``measure_area`` 的 6 个 token
  （``area_nm2`` / ``outer_area_nm2`` / ``hole_area_nm2`` / ``perimeter_nm`` / ``loop_count`` / ``closed``）。

``hover_readout``（12 个 token，固定顺序）::

    mode=hover  at_nm=x,y  tol_nm=none|<int>  hit=0|1  ref=<uuid>|NA  kind=<kind>|NA
    layer=<name>|NA  distance_nm=<int>|NA  nearest_nm=x,y|NA  inside=0|1
    measure=area|length|none  length_nm=<int>|NA  area_nm2=<int>|NA
    radius_nm=<int>|NA  angle_deg=<float>|NA

``hit`` 是**容差判据**：``tol_nm=none`` ⇒ 有实体即 hit（取最近），``tol_nm=<int>`` ⇒ 最近距离 ≤ tol 才算命中。
**容差未命中时 ``hit=0``、``ref/kind/layer/measure`` 全 ``NA``，但 ``distance_nm`` / ``nearest_nm`` 仍是
最近实体的真实数字**（消费方可据此显示"太远了"），这是有意设计、不是漏采。
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Callable

# 直跑引导（本包没有 ops/__init__.py 与 ops/features/__init__.py，靠命名空间包被发现；
# 直跑本文件时把 packages/cadcore 塞进 sys.path 再用**绝对导入**，不依赖 __package__ 自举）。
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError
from cadcore.ops.hatch_measure import (
    _curve_length,
    _entities,
    _entity_points,
    _fail,
    _kind_of,
    _looks_like_sketch,
    _nm,
    _point,
    _point_in_loop,
    _selftest_circle,
    _selftest_rectangle,
    _shoelace,
    _sketch_of,
    _uuid_of,
    measure_area,
    measure_distance,
)

FAMILY = "hatch_measure"

_MODES = ("auto", "hover", "distance", "area")
_NA = "NA"


# --------------------------------------------------------------------------------------
# 目标解析（草图 / 文档都收，与族模块同一套归一化入口）
# --------------------------------------------------------------------------------------
def _entities_of(target: Any) -> tuple[Any, list[Any]]:
    """``(sketch, entities)``：草图直接用；文档走族模块 ``_sketch_of`` 归一化。

    ``_sketch_of`` 的返回形态不在本文件的契约里 ⇒ 只取第 0 个元素，不做元组解包假设。
    """
    if _looks_like_sketch(target):
        return target, list(_entities(target))
    resolved = _sketch_of(target)
    sketch = resolved[0] if isinstance(resolved, (tuple, list)) else resolved
    return sketch, list(_entities(sketch))


# --------------------------------------------------------------------------------------
# 悬停几何：线段最近点 + 闭实体包含判定
# --------------------------------------------------------------------------------------
def _segment_nearest(px: float, py: float, ax: float, ay: float,
                     bx: float, by: float) -> tuple[float, float, float]:
    """光标点到线段 ab 的最近距离与最近点（投影参数夹取到 [0,1]，真几何不是包围盒近似）。"""
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom <= 0.0:
        return math.hypot(px - ax, py - ay), ax, ay
    t = ((px - ax) * vx + (py - ay) * vy) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    nx, ny = ax + t * vx, ay + t * vy
    return math.hypot(px - nx, py - ny), nx, ny


def _entity_hit(entity: Any, px: float, py: float) -> tuple[float, float, float, bool] | None:
    """``(最近距离, 最近点x, 最近点y, 是否被该闭实体包含)``；实体无采样点 ⇒ None。"""
    points, closed = _entity_points(entity)
    ring = [(float(p[0]), float(p[1])) for p in points]
    if not ring:
        return None
    count = len(ring)
    segment_count = count if (closed and count >= 2) else max(0, count - 1)
    best: tuple[float, float, float] | None = None
    for index in range(segment_count):
        ax, ay = ring[index]
        bx, by = ring[(index + 1) % count]
        distance, nx, ny = _segment_nearest(px, py, ax, ay, bx, by)
        if best is None or distance < best[0]:
            best = (distance, nx, ny)
    if best is None:  # 单点实体：退化成点距（真值，不假造成 0）
        best = (math.hypot(px - ring[0][0], py - ring[0][1]), ring[0][0], ring[0][1])
    inside = False
    if closed and count >= 3:
        inside = bool(_point_in_loop((int(round(px)), int(round(py))), list(points)))
    return best[0], best[1], best[2], inside


def _area_scalar(entity: Any) -> float | None:
    """解析式面积标量（用于"被包含时取最小区域"的排序键）；不适用 ⇒ None。"""
    kind = _kind_of(entity)
    if kind == "circle":
        radius = _curve_length(entity) / (2.0 * math.pi)
        return math.pi * radius * radius
    points, closed = _entity_points(entity)
    if closed and len(points) >= 3:
        return abs(float(_shoelace(list(points))))
    return None


def _hover_pick(entities: list[Any], px: float,
                py: float) -> tuple[Any, float, float, float, bool] | None:
    """命中排序：**被闭实体包含**优先（其中最小区域优先），否则取最近实体。一个都没 ⇒ None。"""
    contained: tuple[tuple[float, float], Any, float, float, float, bool] | None = None
    nearest: tuple[tuple[float, float], Any, float, float, float, bool] | None = None
    for entity in entities:
        hit = _entity_hit(entity, px, py)
        if hit is None:
            continue
        distance, nx, ny, inside = hit
        if nearest is None or distance < nearest[0][0]:
            nearest = ((distance, 0.0), entity, distance, nx, ny, inside)
        if inside:
            area = _area_scalar(entity)
            key = (area if area is not None else float("inf"), distance)
            if contained is None or key < contained[0]:
                contained = (key, entity, distance, nx, ny, inside)
    picked = contained or nearest
    if picked is None:
        return None
    _key, entity, distance, nx, ny, inside = picked
    return entity, distance, nx, ny, inside


def _tolerance(value: Any) -> int | None:
    """``None`` = 不限容差（取最近即命中）；否则整数纳米且不许为负。"""
    if value is None:
        return None
    tol = _nm(value, what="tol_nm")
    if tol < 0:
        raise _fail("invalid_length", "tol_nm must not be negative")
    return tol


def _hover_tokens(target: Any, at: tuple[int, int], tol: int | None) -> list[str]:
    """核心：光标点 → 即时读数（只读；空草图也走同一条三态口径）。"""
    px, py = int(at[0]), int(at[1])
    _sketch, entities = _entities_of(target)
    tokens = ["mode=hover", "at_nm=%d,%d" % (px, py),
              "tol_nm=%s" % ("none" if tol is None else str(tol))]
    if not entities:
        tokens += ["hit=0", "ref=NA", "kind=NA", "layer=NA", "distance_nm=NA",
                   "nearest_nm=NA", "inside=0", "measure=none", "length_nm=NA",
                   "area_nm2=NA", "radius_nm=NA", "angle_deg=NA"]
        return tokens
    picked = _hover_pick(entities, px, py)
    if picked is None:
        tokens += ["hit=0", "ref=NA", "kind=NA", "layer=NA", "distance_nm=NA",
                   "nearest_nm=NA", "inside=0", "measure=none", "length_nm=NA",
                   "area_nm2=NA", "radius_nm=NA", "angle_deg=NA"]
        return tokens

    entity, distance, nx, ny, inside = picked
    distance_nm = int(round(distance))
    hit = 1 if (tol is None or distance <= float(tol)) else 0
    tokens += ["hit=%d" % hit,
               "ref=%s" % (_uuid_of(entity) if hit else "NA"),
               "kind=%s" % (_kind_of(entity) if hit else "NA"),
               "layer=%s" % (str(getattr(entity, "layer", "") or "0") if hit else "NA"),
               "distance_nm=%d" % distance_nm,
               "nearest_nm=%d,%d" % (int(round(nx)), int(round(ny))),
               "inside=%d" % (1 if inside else 0)]
    if not hit:
        tokens += ["measure=none", "length_nm=NA", "area_nm2=NA", "radius_nm=NA", "angle_deg=NA"]
        return tokens

    kind = _kind_of(entity)
    length_nm = int(round(_curve_length(entity)))
    points, closed = _entity_points(entity)
    area_value: Any = _NA
    radius_value: Any = _NA
    angle_value: Any = _NA
    if kind == "circle":
        radius = length_nm / (2.0 * math.pi) if length_nm else 0.0
        radius_value = int(round(radius))
        area_value = int(round(math.pi * radius * radius))
    elif closed and len(points) >= 3:
        area_value = int(round(abs(float(_shoelace(list(points))))))
    if kind in ("line", "polyline") and len(points) >= 2:
        dx = float(points[-1][0]) - float(points[0][0])
        dy = float(points[-1][1]) - float(points[0][1])
        if dx or dy:
            angle_value = "%g" % (math.degrees(math.atan2(dy, dx)) % 360.0)
    measure = "area" if area_value != _NA else ("length" if length_nm else "none")
    tokens += ["measure=%s" % measure, "length_nm=%d" % length_nm,
               "area_nm2=%s" % area_value, "radius_nm=%s" % radius_value,
               "angle_deg=%s" % angle_value]
    return tokens


# --------------------------------------------------------------------------------------
# 公开 op（冻结接口）
# --------------------------------------------------------------------------------------
def _resolve_mode(mode: Any, *, p1_nm: Any, p2_nm: Any, at_nm: Any, boundary: Any) -> str:
    chosen = "auto" if mode is None else mode
    if not isinstance(chosen, str) or chosen not in _MODES:
        raise _fail("invalid_argument", "mode must be one of auto/hover/distance/area")
    if chosen != "auto":
        return chosen
    if at_nm is not None:
        return "hover"
    if p1_nm is not None or p2_nm is not None:
        return "distance"
    if boundary is not None:
        return "area"
    raise _fail("invalid_argument", "no measurement operand (need at_nm / p1_nm+p2_nm / boundary)")


def measure_geom(sk_or_doc: Any, *, mode: Any = None, boundary: Any = None, p1_nm: Any = None,
                 p2_nm: Any = None, at_nm: Any = None, tol_nm: Any = 0,
                 hover_tol_nm: Any = None) -> list[str]:
    """统一测量入口：按操作数（或显式 ``mode``）分发到 hover / distance / area 三条腿。

    参数
    ----
    - ``mode``：``auto|hover|distance|area``；``auto``（缺省）按 ``at_nm`` > ``p1_nm/p2_nm`` > ``boundary``
      的优先级判定；一个操作数都没有 ⇒ ``CadError(reason="invalid_argument")``（不猜、不返回空结果）。
    - ``at_nm``：光标点（``(x,y)`` / 映射 / 族模块 ``_point`` 认得的形态），触发悬停读数。
    - ``p1_nm`` / ``p2_nm``：距离的两个操作数（点）；**缺一个** ⇒ ``invalid_argument``（不猜原点）。
    - ``boundary``：区域测量的边界（uuid / uuid 序列 / ``None`` = 全图），语义与族模块 ``measure_area`` 一致。
    - ``tol_nm``：成环网格吸附容差（区域腿，整数纳米，不许为负）。
    - ``hover_tol_nm``：悬停容差（``None`` = 不限，取最近即命中）。

    返回：见模块 docstring 的 token 顺序表；``mode=`` 恒为第 0 个 token。
    """
    chosen = _resolve_mode(mode, p1_nm=p1_nm, p2_nm=p2_nm, at_nm=at_nm, boundary=boundary)
    if chosen == "hover":
        return _hover_tokens(sk_or_doc, _point(at_nm, what="at_nm"), _tolerance(hover_tol_nm))
    if chosen == "distance":
        if p1_nm is None or p2_nm is None:
            raise _fail("invalid_argument", "distance mode needs both p1_nm and p2_nm")
        return ["mode=distance"] + list(measure_distance(sk_or_doc, p1_nm=p1_nm, p2_nm=p2_nm))
    snap = _nm(tol_nm, what="tol_nm")
    if snap < 0:
        raise _fail("invalid_length", "tol_nm must not be negative")
    return ["mode=area"] + list(measure_area(sk_or_doc, boundary=boundary, tol_nm=snap))


def hover_readout(sk_or_doc: Any, *, at_nm: Any = None, tol_nm: Any = None) -> list[str]:
    """悬停快速读数：给一个光标点，返回该点下的实体与其即时测量读数（只读，不落任何实体）。

    - ``at_nm``：**必给**；``None`` ⇒ ``CadError(reason="invalid_argument")``。
    - ``tol_nm``：``None`` = 不限（有实体即 ``hit=1``）；整数 = 只认最近距离 ≤ tol 的命中。
    - 空草图 ⇒ ``hit=0`` + 各项 ``NA``（"真为零"与"不适用"不同形，不静默回报 0）。
    """
    if at_nm is None:
        raise _fail("invalid_argument", "hover_readout needs at_nm (cursor point)")
    return _hover_tokens(sk_or_doc, _point(at_nm, what="at_nm"), _tolerance(tol_nm))


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "measure_geom": measure_geom,
    "measure": measure_geom,
    "hover_readout": hover_readout,
}


# --------------------------------------------------------------------------------------
# 自测（真跑：真几何 + 硬断言；失败即 rc=1，不许假绿）
# --------------------------------------------------------------------------------------
def _token(tokens: list[str], key: str) -> str | None:
    prefix = key + "="
    for item in tokens:
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def _bind_fixture(built: Any) -> tuple[Any, list[Any]]:
    """把族模块自测夹具的返回（草图 / 文档 / 元组）绑成 ``(target, entities)``。

    只给自测用：逐个候选试，取第一个能解析出非空实体表的；试不出来就返回 ``(built, [])``，
    由调用方的断言报红（不静默跳过）。
    """
    candidates = [built]
    if isinstance(built, (tuple, list)):
        candidates.extend(list(built))
    for candidate in candidates:
        try:
            _sketch, entities = _entities_of(candidate)
        except Exception:
            continue
        if entities:
            return candidate, entities
    return built, []


def _selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    # ---- 夹具：族模块自己的 100mm x 50mm 矩形（真 L1 line 实体） ----
    target, entities = _bind_fixture(_selftest_rectangle())
    check("fixture_entities", len(entities) > 0, "n=%d" % len(entities))
    uuids = {_uuid_of(entity) for entity in entities}

    # 硬断言 1：统一入口 area 腿 = 100mm x 50mm = 5e15 nm^2、周长 3e8 nm、单闭合环
    area_tokens = measure_geom(target, mode="area")
    check("area_nm2", _token(area_tokens, "area_nm2") == "5000000000000000",
          "got=%s" % _token(area_tokens, "area_nm2"))
    check("perimeter_nm", _token(area_tokens, "perimeter_nm") == "300000000",
          "got=%s" % _token(area_tokens, "perimeter_nm"))
    check("area_closed_loops", _token(area_tokens, "closed") == "1"
          and _token(area_tokens, "loop_count") == "1",
          "closed=%s loop_count=%s" % (_token(area_tokens, "closed"), _token(area_tokens, "loop_count")))

    # 硬断言 2：distance 腿 3-4-5 直角三角形 ⇒ 50mm，增量与距离同源
    dist_tokens = measure_geom(target, mode="distance", p1_nm=(0, 0),
                               p2_nm=(30_000_000, 40_000_000))
    check("distance_nm", _token(dist_tokens, "distance_nm") == "50000000",
          "got=%s" % _token(dist_tokens, "distance_nm"))
    check("dx_nm", _token(dist_tokens, "dx_nm") == "30000000", "got=%s" % _token(dist_tokens, "dx_nm"))
    check("dy_nm", _token(dist_tokens, "dy_nm") == "40000000", "got=%s" % _token(dist_tokens, "dy_nm"))

    # 硬断言 3：悬停 — 取第一条 line 实体两端点的中点（必然落在实体上）⇒ hit=1 / kind=line / 距离 ~0
    line = None
    for entity in entities:
        points, _closed = _entity_points(entity)
        if _kind_of(entity) == "line" and len(points) >= 2:
            line = entity
            break
    check("fixture_has_line", line is not None, "line=%s" % (line is not None))
    if line is not None:
        points, _closed = _entity_points(line)
        mid = ((int(points[0][0]) + int(points[-1][0])) // 2,
               (int(points[0][1]) + int(points[-1][1])) // 2)
        hover = hover_readout(target, at_nm=mid)
        check("hover_hit", _token(hover, "hit") == "1", "got=%s" % _token(hover, "hit"))
        check("hover_kind", _token(hover, "kind") == "line", "got=%s" % _token(hover, "kind"))
        check("hover_ref_real", _token(hover, "ref") in uuids, "ref=%s" % _token(hover, "ref"))
        got_distance = _token(hover, "distance_nm")
        check("hover_on_entity_distance_le_2nm",
              got_distance is not None and got_distance != "NA" and int(got_distance) <= 2,
              "got=%s" % got_distance)
        check("hover_measure_length", _token(hover, "measure") == "length",
              "got=%s" % _token(hover, "measure"))

        # 硬断言 4：容差未命中 —— 光标挪开 1m、tol=1000nm ⇒ hit=0，但距离仍是真实数字（不是 NA/0）
        far = (mid[0] + 1_000_000_000, mid[1])
        far_tokens = hover_readout(target, at_nm=far, tol_nm=1000)
        check("hover_tol_miss", _token(far_tokens, "hit") == "0", "got=%s" % _token(far_tokens, "hit"))
        check("hover_tol_miss_ref_na", _token(far_tokens, "ref") == "NA",
              "got=%s" % _token(far_tokens, "ref"))
        far_distance = _token(far_tokens, "distance_nm")
        check("hover_tol_miss_distance_real",
              far_distance is not None and far_distance != "NA" and 999_000_000 <= int(far_distance) <= 1_001_000_000,
              "got=%s" % far_distance)

    # 硬断言 5：没有操作数 ⇒ CadError(invalid_argument)，不猜、不返回空结果
    try:
        measure_geom(target)
        check("no_operand_reason", False, "no error raised")
    except CadError as exc:
        check("no_operand_reason", exc.reason == "invalid_argument", "reason=%s" % exc.reason)

    # 硬断言 6（圆，夹具在就给）：圆心悬停 ⇒ inside=1 且到圆周距离 = 半径（解析式，5% 带内）
    note = "circle_entity=absent"
    circle_target, circle_entities = _bind_fixture(_selftest_circle())
    circles = [entity for entity in circle_entities if _kind_of(entity) == "circle"]
    if circles:
        circle = circles[0]
        points, _closed = _entity_points(circle)
        cx = int(round(sum(float(p[0]) for p in points) / len(points)))
        cy = int(round(sum(float(p[1]) for p in points) / len(points)))
        circle_tokens = hover_readout(circle_target, at_nm=(cx, cy))
        radius_nm = int(round(_curve_length(circle) / (2.0 * math.pi)))
        check("circle_inside", _token(circle_tokens, "inside") == "1",
              "got=%s" % _token(circle_tokens, "inside"))
        circle_distance = _token(circle_tokens, "distance_nm")
        band = max(1000, radius_nm // 20)
        check("circle_distance_eq_radius",
              circle_distance is not None and circle_distance != "NA"
              and abs(int(circle_distance) - radius_nm) <= band,
              "d=%s r=%d band=%d" % (circle_distance, radius_nm, band))
        note = "circle_entity=present radius_nm=%d" % radius_nm
    else:
        check("circle_entity_present", False, "family fixture gave no circle entity")

    failed = [name for name, ok, _detail in checks if not ok]
    for name, ok, detail in checks:
        print("SELFTEST check=%s ok=%d %s" % (name, 1 if ok else 0, detail))
    print("SELFTEST %s checks=%d failed=%d" % (note, len(checks), len(failed)))
    if failed:
        print("FEATURE_OK family=%s op=measure_geom verdict=FAIL failed=%s" % (FAMILY, ",".join(failed)))
        return 1
    print("FEATURE_OK family=%s op=measure_geom verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "hover_readout": {"tier": "query", "readonly": True, "returns": "data"},
}
