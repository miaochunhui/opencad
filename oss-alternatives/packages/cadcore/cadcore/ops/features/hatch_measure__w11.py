#!/usr/bin/env python3
"""cadcore.ops.features.hatch_measure__w11 —— hatch_measure 族实现片（席位 w11）。

本片负责哪一条（口径先写死，防下游靠猜）
----------------------------------------
输入清单 = `docs/research/cad-parity/status/autocad-hatch-measure.json` 的 `items`（20 条 must_have）
∩ `status != implemented`，**按清单顺序**编号 0..N-1：

    index 0 : 渐变色填充（partial；L1 实体白名单无颜色字段 ⇒ 只有声明形态）
    index 1 : 通用测量（含悬停快速读数）（partial）  <-- **本片负责的第 2 号**

族内可做条目剩 2 条 ⇒ 第 2 号 = `index 1`（未触发"不足就取模"的分支）。status 文件里
`missing=0 / partial=2 / implemented=18`，族门判定行另见该文件 `gate` 字段。

旧审计点名的两个缺口，与本片交付
--------------------------------
1. **没有 MEASUREGEOM 统一入口**：族里只有 `measure_area` / `measure_perimeter` / `measure_distance`
   三条分列 op（`cadcore/ops/hatch_measure.py:1067/:1092/:1112`）。
   ⇒ 本片 `measure_geom(sk_or_doc, mode=..., **operands)`：统一入口 + 模式分派 + **歧义拒答**
   （光标点与距离操作数同时给 ⇒ `ambiguous_input`，不静默挑一条）。数值**全部转发**族内既有三条 op，
   **不抄第二份算法**（同源：同一张图两次问法逐位一致，自检 #2 直接对拍 `measure_area` 的输出 token）。
   另加 `mode="radius"`（MEASUREGEOM 的半径子命令）：圆/圆弧给 `center_nm` / `radius_nm` / `diameter_nm`，
   非圆实体按族口径给 `NA`（"不适用"不是错误、也不是 0）。
2. **没有悬停/实时快速读数**：apps 侧 `grep hover|悬停|MEASUREGEOM` 0 命中（`status` 的 `top_gaps`）。
   ⇒ 本片 `measure_hover(sk_or_doc, at_nm=(x_nm, y_nm), pick_radius_nm=None)`：把"光标下有什么、离多远、
   贴到哪个捕捉点"做成**L1 上的纯函数**（无事件源也能真跑、真断言）：
   `hit`（真实实体 uuid）/`snap_nm`（捕捉点）/`offset_nm`（光标到捕捉点距离）/`dx_nm`/`dy_nm`/
   `hit_kind`/`hit_layer`/`snap_method`/`curve_length_nm`/`radius_nm`/`diameter_nm`/
   `area_nm2`（**光标所在闭合区域的净面积**，最小包含环，与 `boundary_from_point` 同一判据）/`loop_members`。
   捕捉点算法：`line`/`polyline` 走**线段解析投影**（比族里 `measure_distance` 的离散点更准，
   故有 `snap_method=analytic|sampled` 明示口径）；`circle` 走**解析投影**；`arc` 用家族离散采样
   （`snap_method=sampled`）—— 弧的起止角语义不重猜，复用族内 `_entity_points` 的既有口径。

L1 装得下 / 装不下（诚实边界，不许用假几何冒充）
------------------------------------------------
- 装得下：**读数**是纯函数（不给文档加一个字节）：本片两个 op 都**只读**、不建表、不落实体；
  返回 `"key=value"` 结果 token 列表，与族内测量 op 同形。
- 装不下（仍 missing，明说）：**事件源与 UI**。L1 是静态文档（无光标、无鼠标事件、无视图刷新），
  "悬停即实时刷新"里的**实时**那一半需要 apps 侧交互层（点击/悬停事件 + 视图重绘）；
  本片在**内核侧**把读数函数做齐并做真（任何调用方给一个 `at_nm` 就能拿到读数），
  **apps 侧消费方仍然 0**（`status` 的 `top_gaps` 第 2 条原样成立）——本片**不声称**该条已在 UI 上可达。
- 不做的：不改 `cadcore/ops/hatch_measure.py`（族门默认发现的模块，别席边界），不写 `__init__.py`。

三态纪律（与全仓一致，别自己发明）
----------------------------------
`NONE` = 光标下**真的**没有实体（`candidates=0` 佐证），`NA` = 该问法对这次命中**不适用**
（例：光标没落在任何闭合区域内 ⇒ `area_nm2=NA`；直线上问半径 ⇒ `radius_nm=NA`）。
`0` 是真零（`candidates=0`、`dx_nm=0`）。三者不同形。

冻结接口（对外的唯一契约）
--------------------------
``FAMILY = "hatch_measure"``；``OPERATIONS: dict[str, Callable]``；每个 op ``op(sk_or_doc, **kwargs) -> list[str]``，
长度整数纳米，错误一律 ``cadcore.errors.CadError(reason=...)``（reason 取值见各 op docstring）。

直跑自检：``python3 cadcore/ops/features/hatch_measure__w11.py``（打印 CHECK 行 + FEATURE_OK 判定行）。
中文只出现在 docstring 里（i18n 门把非 docstring 的含汉字字符串常量计为 hardcoded_literals）。
"""

from __future__ import annotations

import math
import os as _os
import sys as _sys
from collections.abc import Mapping as _MappingABC
from typing import Any, Callable

#: 本文件在 cadcore/ops/features/ 下，而 ops/ 与 features/ **都没有** __init__.py（命名空间包，
#: 且本目录的 __init__.py 归别席）⇒ 不靠相对导入：把 cadcore 包的父目录（packages/cadcore）压进
#: sys.path 后一律**绝对导入**。这样"包内导入"与"直跑脚本"两条路径都能工作（直跑时 __package__ 为空，
#: 相对导入会直接失败）。
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_HERE)))  # .../packages/cadcore
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from cadcore.errors import CadError
from cadcore.ops.hatch_measure import (
    _closed_loops,
    _curve_length,
    _entities,
    _entity_points,
    _fail,
    _kind_of,
    _loop_metrics,
    _nm,
    _num_field,
    _point,
    _point_in_loop,
    _region,
    _round_nm,
    _sketch_of,
    _subset,
    _uuid_of,
    measure_area,
    measure_distance,
    measure_perimeter,
)
from cadcore.sketch.constraints import new_uuid

__all__ = ["FAMILY", "OPERATIONS", "measure_geom", "measure_hover", "selftest"]

FAMILY = "hatch_measure"

#: 统一入口的**冻结模式枚举**（AutoCAD MEASUREGEOM 的子命令子集）。
_MODES: tuple[str, ...] = ("distance", "area", "perimeter", "radius", "hover")

#: 悬停拾取孔径缺省值 = 5 mm（光标到实体的距离超过它 ⇒ `hit=NONE`；显式传 0 表示"必须正好压线"）。
_PICK_APERTURE_NM = 5_000_000

#: 自检用：1 mm = 1e6 nm。
_NM_PER_MM = 1_000_000

#: `hit=NONE` 之后按固定顺序补齐的 token 名（避免两条返回路径 token 集合不同形）。
_NO_HIT_TAIL: tuple[str, ...] = ("hit", "hit_kind", "hit_layer", "snap_nm", "offset_nm", "dx_nm",
                                 "dy_nm", "snap_method", "curve_length_nm", "radius_nm",
                                 "diameter_nm", "area_nm2", "loop_members")


# ======================================================================================
# 局部取值/几何小件（不重写族内已有算法：成环、区域、弧长、面积一律复用 hatch_measure）
# ======================================================================================


def _field(entity: Any, key: str, default: Any = None) -> Any:
    """实体字段取值（dict 与对象两种承载都吃；族内实体是 dict，但别假设死）。"""
    if isinstance(entity, _MappingABC):
        return entity.get(key, default)
    return getattr(entity, key, default)


def _layer_of(entity: Any) -> str:
    value = _field(entity, "layer", None)
    return value if isinstance(value, str) and value else "0"


def _project_on_segment(point: Any, start: Any, end: Any) -> tuple[float, float]:
    """点到**线段**的最近点（解析式；不是只比端点 —— 顶点取样会把中段偏移量算大）。"""
    px, py = float(point[0]), float(point[1])
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        return (x1, y1)
    factor = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    factor = 0.0 if factor < 0.0 else (1.0 if factor > 1.0 else factor)
    return (x1 + factor * dx, y1 + factor * dy)


def _polyline_snap(points: list[Any], closed: bool, at: Any) -> tuple[float, tuple[float, float]] | None:
    """折线（含闭合）逐段解析投影 → `(距离, 最近点)`；段数 < 1 ⇒ None。"""
    count = len(points)
    if count < 2:
        return None
    last = count if closed else count - 1
    best: tuple[float, tuple[float, float]] | None = None
    for index in range(last):
        snap = _project_on_segment(at, points[index], points[(index + 1) % count])
        distance = math.dist((float(at[0]), float(at[1])), snap)
        if best is None or distance < best[0]:
            best = (distance, snap)
    return best


def _snap_of(entity: Any, at: Any) -> tuple[Any, Any, Any]:
    """光标 → `(捕捉点, 距离, 方法)`；无几何 ⇒ `(None, None, None)`。

    `analytic` = 解析投影（line / polyline 线段 / circle）；`sampled` = 族内离散采样（arc 及其它）。
    **口径写在 token 里**（`snap_method`），免得下游以为两条路径同精度。
    """
    kind = _kind_of(entity)
    if kind == "circle":
        cx, cy, radius = _num_field(entity, "cx"), _num_field(entity, "cy"), _num_field(entity, "r")
        dx, dy = float(at[0]) - cx, float(at[1]) - cy
        span = math.hypot(dx, dy)
        # 光标正好在圆心：方向未定义 ⇒ 固定取 +X 方向（写死，保证同一输入两次逐位一致）
        snap = (cx + radius, cy) if span == 0.0 else (cx + radius * dx / span, cy + radius * dy / span)
        return snap, abs(span - radius), "analytic"
    points, closed = _entity_points(entity)
    if kind in ("line", "polyline") and len(points) >= 2:
        found = _polyline_snap(list(points), bool(closed), at)
        if found is not None:
            return found[1], found[0], "analytic"
    if not points:
        return None, None, None
    best = None
    for point in points:
        distance = math.dist((float(at[0]), float(at[1])), (float(point[0]), float(point[1])))
        if best is None or distance < best[0]:
            best = (distance, point)
    return best[1], best[0], "sampled"


def _selected_region(entity_list: list[Any], at: tuple[int, int], tol_nm: int,
                     by_uuid: dict[str, Any]) -> dict[str, Any] | None:
    """光标所在的**最小闭合环**（含其内部各环当洞）→ `_region` 结果；不在任何环内 ⇒ None。

    判据与族内 `boundary_from_point` 完全一致（按 |面积| 取最小包含环，不猜最近环）；
    洞的挑法 = "所有落在该环内部的其它环"（不要求洞也包含光标，否则面积会算大）。
    """
    loops = _closed_loops(entity_list, tol_nm)
    containing = [loop for loop in loops if _point_in_loop(at, loop["points"])]
    if not containing:
        return None
    containing.sort(key=lambda loop: (abs(_loop_metrics(loop, by_uuid)[0]), loop["members"]))
    chosen = containing[0]
    nested = [loop for loop in loops
              if loop is not chosen and _point_in_loop(loop["points"][0], chosen["points"])]
    return _region([chosen] + nested, by_uuid)


# ======================================================================================
# 操作 1：MEASUREGEOM 统一入口
# ======================================================================================


def _resolve_mode(mode: Any, *, at_nm: Any, p1_nm: Any, p2_nm: Any, ref_a: Any, ref_b: Any) -> str:
    """模式分派 + **歧义拒答**（不静默挑一条，也不静默忽略多余操作数）。"""
    if mode is None:
        resolved = ""
    elif isinstance(mode, str) and mode.strip().lower() in _MODES:
        resolved = mode.strip().lower()
    else:
        raise _fail("invalid_argument", "mode is not in the frozen enumeration",
                    mode=repr(mode), known=list(_MODES))
    has_cursor = at_nm is not None
    has_distance = any(value is not None for value in (p1_nm, p2_nm, ref_a, ref_b))
    if has_cursor and has_distance:
        raise _fail("ambiguous_input", "at_nm (hover) cannot be combined with distance operands",
                    at_nm=repr(at_nm), p1_nm=repr(p1_nm), p2_nm=repr(p2_nm),
                    ref_a=repr(ref_a), ref_b=repr(ref_b))
    if not resolved:
        if has_cursor:
            resolved = "hover"
        elif has_distance:
            resolved = "distance"
        else:
            resolved = "area"
    if resolved == "hover" and not has_cursor:
        raise _fail("invalid_argument", "mode=hover needs at_nm=(x_nm, y_nm)")
    if resolved == "distance" and not has_distance:
        raise _fail("invalid_argument",
                    "mode=distance needs two operands: p1_nm/p2_nm, p1_nm+ref_b, ref_a+p2_nm or ref_a+ref_b")
    if resolved in ("area", "perimeter") and has_distance:
        raise _fail("invalid_argument", "distance operands are not valid for this mode", mode=resolved)
    if resolved == "radius":
        if (ref_a is None) == (ref_b is None):
            raise _fail("invalid_argument", "mode=radius needs exactly one entity operand (ref_a)")
        if p1_nm is not None or p2_nm is not None:
            raise _fail("invalid_argument", "mode=radius does not take point operands")
    return resolved


def _measure_radius(sk_or_doc: Any, *, ref_a: Any, ref_b: Any) -> list[str]:
    """圆/圆弧的半径读数；非圆实体给 `NA`（问法不适用，**不报错**，与 prop_query 同口径）。"""
    if ref_b is not None:
        raise _fail("invalid_argument", "mode=radius needs exactly one entity operand (ref_a)")
    if not isinstance(ref_a, str) or not ref_a.strip():
        raise _fail("invalid_argument", "mode=radius needs ref_a = entity uuid string", ref_a=repr(ref_a))
    sketch, _doc = _sketch_of(sk_or_doc)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    key = ref_a.strip()
    entity = by_uuid.get(key)
    if entity is None:
        raise _fail("unknown_entity", "ref_a is not an entity of this sketch/document", ref=key)
    kind = _kind_of(entity)
    if kind not in ("circle", "arc"):
        return ["ref=%s" % key, "kind=%s" % kind, "center_nm=NA", "radius_nm=NA", "diameter_nm=NA"]
    radius = _num_field(entity, "r")
    return ["ref=%s" % key, "kind=%s" % kind,
            "center_nm=%d,%d" % (_round_nm(_num_field(entity, "cx")), _round_nm(_num_field(entity, "cy"))),
            "radius_nm=%d" % _round_nm(radius),
            "diameter_nm=%d" % _round_nm(2.0 * radius)]


def measure_geom(sk_or_doc: Any, *, mode: Any = None, p1_nm: Any = None, p2_nm: Any = None,
                 ref_a: Any = None, ref_b: Any = None, boundary: Any = None, tol_nm: Any = 0,
                 at_nm: Any = None, pick_radius_nm: Any = None) -> list[str]:
    """**MEASUREGEOM 统一入口（只读）**：`mode` 分派到族内三条测量 op + 半径 + 悬停读数。

    `mode` ∈ `distance | area | perimeter | radius | hover`（冻结枚举，见模块 `_MODES`）；
    `mode=None` 时按**唯一可用操作数**推断：给了 `at_nm` ⇒ `hover`；给了距离操作数 ⇒ `distance`；
    都没有 ⇒ `area`（量整张草图）。**歧义一律报错**：`at_nm` 与距离操作数同时给 ⇒
    ``CadError(reason="ambiguous_input")``；`area`/`perimeter` 混入距离操作数、
    `hover` 缺 `at_nm`、`radius` 给 0 个或 2 个实体 ⇒ ``CadError(reason="invalid_argument")``。

    **返回**：第 1 个 token 固定 ``measure_geom=<mode>``，随后是**被分派 op 的原样 token**
    （`distance` = 5 个、`area` = 6 个、`perimeter` = 3 个、`radius` = 5 个、`hover` = 16 个），
    末尾追加 1 个本次入口自己的 token：
    `distance` ⇒ ``operands=point|entity:point|entity``；`area`/`perimeter` ⇒
    ``scope=boundary|all``（`boundary=None` = 整张草图）；`radius`/`hover` 不追加。

    **同源保证**：数值不在这里重算 —— `area`/`perimeter`/`distance` 直接调用族内
    `measure_area`/`measure_perimeter`/`measure_distance`，返回值 `[1:-1]` 与它们逐字符相等（自检 #2 对拍）。
    """
    resolved = _resolve_mode(mode, at_nm=at_nm, p1_nm=p1_nm, p2_nm=p2_nm, ref_a=ref_a, ref_b=ref_b)
    if resolved == "hover":
        return ["measure_geom=hover"] + measure_hover(sk_or_doc, at_nm=at_nm,
                                                      pick_radius_nm=pick_radius_nm,
                                                      boundary=boundary, tol_nm=tol_nm)
    if resolved == "radius":
        return ["measure_geom=radius"] + _measure_radius(sk_or_doc, ref_a=ref_a, ref_b=ref_b)
    if resolved == "area":
        tokens = measure_area(sk_or_doc, boundary=boundary, tol_nm=tol_nm)
        return ["measure_geom=area"] + tokens + ["scope=%s" % ("boundary" if boundary is not None else "all")]
    if resolved == "perimeter":
        tokens = measure_perimeter(sk_or_doc, boundary=boundary)
        return (["measure_geom=perimeter"] + tokens
                + ["scope=%s" % ("boundary" if boundary is not None else "all")])
    tokens = measure_distance(sk_or_doc, p1_nm=p1_nm, p2_nm=p2_nm, ref_a=ref_a, ref_b=ref_b)
    return (["measure_geom=distance"] + tokens
            + ["operands=%s:%s" % ("point" if p1_nm is not None else "entity",
                                   "point" if p2_nm is not None else "entity")])


# ======================================================================================
# 操作 2：悬停快速读数
# ======================================================================================


def measure_hover(sk_or_doc: Any, *, at_nm: Any = None, pick_radius_nm: Any = None,
                  boundary: Any = None, tol_nm: Any = 0) -> list[str]:
    """**悬停快速读数（只读）**：给一个光标点，返回"光标下有什么 + 相关读数"。

    `at_nm` = 光标 `(x_nm, y_nm)`（必给，缺 ⇒ ``CadError(reason="invalid_argument")``）；
    `pick_radius_nm` = 拾取孔径（纳米，≥0；缺省 5 mm，见 `_PICK_APERTURE_NM`；0 = 只有正好压线才命中）；
    `boundary` = 只在这些实体里找（uuid / uuid 序列 / None=整张草图）；`tol_nm` = 成环端点公差。

    **返回**（固定 16 个 token、顺序固定）：

    ``at_nm`` / ``aperture_nm`` / ``candidates``（孔径内实体数，真零 = 0）/ ``hit``（真实实体 uuid，
    没命中 = ``NONE``）/ ``hit_kind`` / ``hit_layer`` / ``snap_nm``（捕捉点）/ ``offset_nm``（光标→捕捉点距离）/
    ``dx_nm`` / ``dy_nm`` / ``snap_method``（``analytic|sampled``）/ ``curve_length_nm``（命中实体自身长度/
    周长）/ ``radius_nm`` / ``diameter_nm``（仅 circle/arc，否则 ``NA``）/
    ``area_nm2``（**光标所在闭合区域的净面积** = 外环 − 洞；不在任何闭合环内 ⇒ ``NA``）/
    ``loop_members``（该区域外环的成员 uuid 列表，用 ``,`` 连接；无 ⇒ ``NA``）。

    命中多个实体时取**最近**的一个（距离相同则按 uuid 字符串排序取小 —— 写死以保证两次调用逐位一致）。
    """
    if at_nm is None:
        raise _fail("invalid_argument", "measure_hover needs at_nm=(x_nm, y_nm)")
    sketch, _doc = _sketch_of(sk_or_doc)
    at = _point(at_nm, what="at_nm")
    aperture = _PICK_APERTURE_NM if pick_radius_nm is None else _nm(pick_radius_nm, what="pick_radius_nm")
    if aperture < 0:
        raise _fail("invalid_length", "pick_radius_nm must be >= 0", pick_radius_nm=aperture)
    tolerance = _nm(tol_nm, what="tol_nm")
    candidates = _subset(sketch, boundary)
    by_uuid = {_uuid_of(entity): entity for entity in _entities(sketch)}
    hits = []
    for entity in candidates:
        if not _entity_points(entity)[0]:
            continue
        snap, distance, method = _snap_of(entity, at)
        if snap is None or distance is None or distance > aperture:
            continue
        hits.append((float(distance), _uuid_of(entity), snap, method))
    hits.sort(key=lambda item: (item[0], item[1]))
    tokens = ["at_nm=%d,%d" % (_round_nm(at[0]), _round_nm(at[1])),
              "aperture_nm=%d" % aperture,
              "candidates=%d" % len(hits)]
    if not hits:
        values = {"hit": "NONE", "hit_kind": "NA", "hit_layer": "NA", "snap_nm": "NA",
                  "offset_nm": "NA", "dx_nm": "NA", "dy_nm": "NA", "snap_method": "NA",
                  "curve_length_nm": "NA", "radius_nm": "NA", "diameter_nm": "NA",
                  "area_nm2": "NA", "loop_members": "NA"}
        return tokens + ["%s=%s" % (key, values[key]) for key in _NO_HIT_TAIL]
    distance, hit_uuid, snap, method = hits[0]
    entity = by_uuid.get(hit_uuid)
    kind = _kind_of(entity)
    tokens += ["hit=%s" % hit_uuid, "hit_kind=%s" % kind, "hit_layer=%s" % _layer_of(entity),
               "snap_nm=%d,%d" % (_round_nm(snap[0]), _round_nm(snap[1])),
               "offset_nm=%d" % _round_nm(distance),
               "dx_nm=%d" % _round_nm(snap[0] - at[0]),
               "dy_nm=%d" % _round_nm(snap[1] - at[1]),
               "snap_method=%s" % method,
               "curve_length_nm=%d" % _round_nm(_curve_length(entity))]
    if kind in ("circle", "arc"):
        radius = _num_field(entity, "r")
        tokens += ["radius_nm=%d" % _round_nm(radius), "diameter_nm=%d" % _round_nm(2.0 * radius)]
    else:
        tokens += ["radius_nm=NA", "diameter_nm=NA"]
    region = _selected_region(candidates, at, tolerance, by_uuid)
    if region is None:
        tokens += ["area_nm2=NA", "loop_members=NA"]
        return tokens
    chosen = [loop for loop in _closed_loops(candidates, tolerance)
              if _point_in_loop(at, loop["points"])]
    chosen.sort(key=lambda loop: (abs(_loop_metrics(loop, by_uuid)[0]), loop["members"]))
    tokens += ["area_nm2=%d" % _round_nm(region["net_area_nm2"]),
               "loop_members=%s" % ",".join(chosen[0]["members"])]
    return tokens


# ======================================================================================
# 冻结接口表（别名与主名指向同一函数对象）
# ======================================================================================

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "measure_geom": measure_geom,
    "measuregeom": measure_geom,
    "measure_hover": measure_hover,
    "hover_measure": measure_hover,
    "measure_at": measure_hover,
}


# ======================================================================================
# 自检：4 条检查 / 硬断言若干（`if __name__ == "__main__"` 跑）
# ======================================================================================


def _token_value(tokens: list[str], key: str) -> str:
    """取 `key=value` token 的 value（没有 ⇒ 空串，调用方据此报红）。"""
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def _check(name: str, conditions: list[tuple[str, bool]], detail: str) -> tuple[str, bool, str, int]:
    failed = [key for key, ok in conditions if not ok]
    text = detail if not failed else (detail + " failed=" + ",".join(failed))
    return (name, not failed, text, len(conditions))


def _rect_sketch(width_mm: float = 100.0, height_mm: float = 50.0) -> dict[str, Any]:
    """100mm x 50mm 闭合矩形（4 条真实 line）。"""
    width, height = int(width_mm * _NM_PER_MM), int(height_mm * _NM_PER_MM)
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"entities": entities, "constraints": [], "dimensions": []}


def _circle_sketch(radius_mm: float = 10.0) -> dict[str, Any]:
    radius = int(radius_mm * _NM_PER_MM)
    return {"entities": [{"uuid": new_uuid(), "kind": "circle", "layer": "OUTLINE",
                          "construction": False, "geom": {"cx": 0, "cy": 0, "r": radius}}],
            "constraints": [], "dimensions": []}


def selftest() -> list[tuple[str, bool, str, int]]:
    """自检 #1..#4；每条给 `(名字, 通过?, 细节, 硬断言数)`。"""
    results: list[tuple[str, bool, str, int]] = []
    sketch = _rect_sketch()
    circle = _circle_sketch(10.0)

    # ---- #1 冻结接口（FAMILY / OPERATIONS / 别名同对象 / 返回值是 str 列表）--------------------
    required = {"measure_geom", "measuregeom", "measure_hover", "hover_measure", "measure_at"}
    interface = measure_geom(sketch)
    results.append(_check("interface", [
        ("family", FAMILY == "hatch_measure"),
        ("ops_dict", isinstance(OPERATIONS, dict) and required.issubset(set(OPERATIONS))),
        ("ops_callable", all(callable(function) for function in OPERATIONS.values())),
        ("alias_same", OPERATIONS["hover_measure"] is measure_hover
                       and OPERATIONS["measure_at"] is measure_hover
                       and OPERATIONS["measuregeom"] is measure_geom),
        ("read_only", len(sketch["entities"]) == 4),          # 查询不得动文档一个实体
        ("token_types", all(isinstance(token, str) for token in interface)),
        ("prefix", interface[0] == "measure_geom=area" and interface[-1] == "scope=all"),
    ], "family=%s ops=%d tokens=%d" % (FAMILY, len(OPERATIONS), len(interface))))

    # ---- #2 统一入口：分派 + 与族内三条 op **同源**（同图两种问法逐字符相等）------------------
    area_tokens = measure_geom(sketch, mode="area")
    delegated_area = measure_area(sketch)
    distance_tokens = measure_geom(sketch, p1_nm=(0, 0), p2_nm=(3 * _NM_PER_MM, 4 * _NM_PER_MM))
    perimeter_tokens = measure_geom(sketch, mode="perimeter")
    radius_tokens = measure_geom(circle, mode="radius", ref_a=circle["entities"][0]["uuid"])
    line_radius = measure_geom(sketch, mode="radius", ref_a=sketch["entities"][0]["uuid"])
    results.append(_check("unified_entry", [
        ("area_inferred", area_tokens[0] == "measure_geom=area" and area_tokens[-1] == "scope=all"),
        ("area_same_source", area_tokens[1:-1] == delegated_area),
        ("area_value", _token_value(area_tokens, "area_nm2") == str(100 * _NM_PER_MM * 50 * _NM_PER_MM)),
        ("area_hole_zero", _token_value(area_tokens, "hole_area_nm2") == "0"),
        ("perimeter_value", _token_value(perimeter_tokens, "perimeter_nm") == str(2 * (100 + 50) * _NM_PER_MM)
                            and _token_value(perimeter_tokens, "closed") == "1"),
        ("distance_inferred", distance_tokens[0] == "measure_geom=distance"
                              and distance_tokens[-1] == "operands=point:point"),
        ("distance_345", _token_value(distance_tokens, "distance_nm") == str(5 * _NM_PER_MM)
                         and _token_value(distance_tokens, "dx_nm") == str(3 * _NM_PER_MM)
                         and _token_value(distance_tokens, "dy_nm") == str(4 * _NM_PER_MM)),
        ("radius_circle", radius_tokens[0] == "measure_geom=radius"
                          and _token_value(radius_tokens, "radius_nm") == str(10 * _NM_PER_MM)
                          and _token_value(radius_tokens, "diameter_nm") == str(20 * _NM_PER_MM)
                          and _token_value(radius_tokens, "center_nm") == "0,0"),
        ("radius_line_na", _token_value(line_radius, "radius_nm") == "NA"
                           and _token_value(line_radius, "kind") == "line"),
    ], "area=%s distance=%s perimeter=%s" % (_token_value(area_tokens, "area_nm2"),
                                             _token_value(distance_tokens, "distance_nm"),
                                             _token_value(perimeter_tokens, "perimeter_nm"))))

    # ---- #3 悬停读数：真几何后果（捕捉点/偏移/增量/区域净面积/解析 vs 采样口径）---------------
    hover = measure_hover(sketch, at_nm=(50 * _NM_PER_MM, 5 * _NM_PER_MM), pick_radius_nm=20 * _NM_PER_MM)
    real_uuids = {entity["uuid"] for entity in sketch["entities"]}
    circle_out = measure_hover(circle, at_nm=(20 * _NM_PER_MM, 0), pick_radius_nm=20 * _NM_PER_MM)
    circle_in = measure_hover(circle, at_nm=(0, 0), pick_radius_nm=20 * _NM_PER_MM)
    results.append(_check("hover_readout", [
        ("hit_is_real_entity", _token_value(hover, "hit") in real_uuids),
        ("candidates", _token_value(hover, "candidates") == "1"),
        ("snap_exact", _token_value(hover, "snap_nm") == "50000000,0"),
        ("offset_exact", _token_value(hover, "offset_nm") == str(5 * _NM_PER_MM)),
        ("delta", _token_value(hover, "dx_nm") == "0" and _token_value(hover, "dy_nm") == str(-5 * _NM_PER_MM)),
        ("kind_layer", _token_value(hover, "hit_kind") == "line" and _token_value(hover, "hit_layer") == "0"),
        ("method_analytic", _token_value(hover, "snap_method") == "analytic"),
        ("curve_length", _token_value(hover, "curve_length_nm") == str(100 * _NM_PER_MM)),
        ("region_net_area", _token_value(hover, "area_nm2") == str(100 * _NM_PER_MM * 50 * _NM_PER_MM)),
        ("loop_members", len(_token_value(hover, "loop_members").split(",")) == 4),
        ("circle_analytic", _token_value(circle_out, "snap_nm") == "10000000,0"
                            and _token_value(circle_out, "offset_nm") == str(10 * _NM_PER_MM)
                            and _token_value(circle_out, "snap_method") == "analytic"),
        ("circle_radius", _token_value(circle_out, "radius_nm") == str(10 * _NM_PER_MM)
                          and _token_value(circle_out, "diameter_nm") == str(20 * _NM_PER_MM)),
        ("outside_area_na", _token_value(circle_out, "area_nm2") == "NA"),
        ("center_offset_radius", _token_value(circle_in, "offset_nm") == str(10 * _NM_PER_MM)),
        ("center_area_pi_r2", _token_value(circle_in, "area_nm2")
                              == str(_round_nm(math.pi * (10 * _NM_PER_MM) ** 2))),
        ("circle_layer", _token_value(circle_in, "hit_layer") == "OUTLINE"),
    ], "hit=%s snap=%s offset=%s area=%s" % (_token_value(hover, "hit"), _token_value(hover, "snap_nm"),
                                             _token_value(hover, "offset_nm"),
                                             _token_value(hover, "area_nm2"))))

    # ---- #4 三态与错误路径：NONE / NA / 0 不同形；歧义与非法入参全报 CadError ---------------
    far = measure_hover(sketch, at_nm=(-50 * _NM_PER_MM, -50 * _NM_PER_MM), pick_radius_nm=1 * _NM_PER_MM)
    empty = measure_hover({"entities": [], "constraints": [], "dimensions": []}, at_nm=(0, 0))
    attempts = [
        ("unknown_mode", lambda: measure_geom(sketch, mode="volume")),
        ("cursor_plus_distance", lambda: measure_geom(sketch, at_nm=(0, 0), p1_nm=(0, 0))),
        ("hover_without_cursor", lambda: measure_hover(sketch)),
        ("radius_without_ref", lambda: measure_geom(sketch, mode="radius")),
        ("radius_two_refs", lambda: measure_geom(sketch, mode="radius", ref_a="a", ref_b="b")),
        ("radius_unknown_ref", lambda: measure_geom(sketch, mode="radius", ref_a="no-such-uuid")),
        ("area_with_distance", lambda: measure_geom(sketch, mode="area", p1_nm=(0, 0))),
    ]
    reasons = []
    for _name, call in attempts:
        try:
            call()
            reasons.append("NO_ERROR")
        except CadError as exc:
            reasons.append(str(exc.reason))
    results.append(_check("states_and_errors", [
        ("no_hit_none", _token_value(far, "hit") == "NONE"),
        ("no_hit_zero_candidates", _token_value(far, "candidates") == "0"),
        ("no_hit_offset_na", _token_value(far, "offset_nm") == "NA"),
        ("no_hit_area_na", _token_value(far, "area_nm2") == "NA"),
        ("empty_none", _token_value(empty, "hit") == "NONE"
                       and _token_value(empty, "candidates") == "0"
                       and _token_value(empty, "loop_members") == "NA"),
        ("reasons", reasons == ["invalid_argument", "ambiguous_input", "invalid_argument",
                                "invalid_argument", "invalid_argument", "unknown_entity",
                                "invalid_argument"]),
    ], "reasons=%s far_hit=%s" % (",".join(reasons), _token_value(far, "hit"))))
    return results


def _main() -> int:
    results = selftest()
    assertions = 0
    for name, ok, detail, count in results:
        assertions += count
        print("CHECK %s ok=%d assertions=%d %s" % (name, 1 if ok else 0, count, detail))
    failed = [name for name, ok, _detail, _count in results if not ok]
    if failed:
        print("FEATURE_FAIL family=hatch_measure op=measure_geom verdict=FAIL assertions=%d failed=%s"
              % (assertions, ",".join(failed)))
        return 1
    print("REMAINING family=hatch_measure n=2 picked_index=1 picked=measure_geom+hover_readout")
    print("FEATURE_OK family=hatch_measure op=measure_geom verdict=PASS assertions=%d" % assertions)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "hover_measure": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_at": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_geom": {"tier": "query", "readonly": True, "returns": "data"},
    "measure_hover": {"tier": "query", "readonly": True, "returns": "data"},
    "measuregeom": {"tier": "query", "readonly": True, "returns": "data"},
}
