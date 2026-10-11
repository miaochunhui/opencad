#!/usr/bin/env python3
"""DRAW-OPS 族片模块 w11 —— 补第 2 号 must_have「圆」（2P / 3P / TTR 三条缺环）。

一、本片负责哪一条（清单顺序可复算）
------------------------------------
真源两件（本席现读，命令见文末）：

* `oss-alternatives/docs/research/cad-parity/features/autocad-draw.json` —— `must_have` 清单顺序
  （20 条，与 `spec/draw_ops.json` 的 `must_have` 逐条同序；`features/draw_ops.json` 这个名字**不存在**，
  族规格实际落在 `spec/draw_ops.json`）；
* `oss-alternatives/docs/research/cad-parity/status/autocad-draw.json` —— 逐条 `status`。

把 `status != "implemented"` 的条目按清单顺序编号 0..N-1（N=15；5 条 implemented 已剔除：
椭圆与椭圆弧 / 样条曲线 / 构造线与射线 / 面域 / 圆环）：

    0 直线(partial)              1 多段线(partial)            2 圆(partial)  <== 本片
    3 圆弧(partial)              4 矩形(partial)              5 正多边形(partial)
    6 点与点样式(partial)        7 图案填充与渐变色(partial)   8 边界创建(partial)
    9 修订云线(partial)         10 对象捕捉(partial)         11 正交与极轴追踪(partial)
   12 对象捕捉追踪与临时追踪点(partial)  13 动态输入(partial)  14 当前图层与对象特性(partial)

第 2 号 = **圆**。状态文档对该条点名的缺环是：「无 2P（两点直径）、无 3P（三点）、无 TTR
（相切相切半径）—— 全仓无切点捕捉与相切构造」。本片**只补这三条缺环的库级真几何**，
圆心+半径那条既有路子在本片同时提供（作为一致性对照），不重写兄弟模块。

二、本片真做了什么（真几何 / 真数据后果，无桩）
----------------------------------------------
`OPERATIONS` 的每个操作都往 L1 草图里**真落一个 `circle` 实体**并按参数解出真实几何：

* `circle_create`（主入口，`mode` 可为 `auto` / `center_radius` / `2p` / `3p` / `ttr`）
* `circle_2p`  两点直径：圆心 = 两端点中点，半径 = 端点距离的一半（半值向上取整到纳米）
* `circle_3p`  三点定圆：用 `fractions.Fraction` 解**外接圆心**（精确有理数，不做浮点近似），
                 三点共线（行列式 0）⇒ `CadError(reason="collinear_points")`，且**一条几何都不落库**
* `circle_ttr` 相切相切半径：对两条既有线段各做**双侧法向偏移 r**，四条偏移线两两求交得候选圆心；
                 逐候选**反算切距**（点到直线距离）必须等于 r（容差 `TOL_NM`）、切点须落在线段内
                 （`within_segment=True`，缺省）；候选 >1 且未给 `hint_nm`/`pick` ⇒
                 `CadError(reason="ambiguous_tangent_solution")`，**不替用户猜**

真数据后果：返回的 uuid 能在草图的 `entities` 里解析到 `kind="circle"` 的实体，字段严格是 L1 冻结五件
`{uuid, kind, layer, construction, geom}`，`geom={"cx","cy","r"}` 全为整数纳米
（`docmodel.check_invariants()` 第 1 条即「`*_nm` 与 geom 坐标不得是浮点」）。

三、口径（与 `draw_ops.py` / `draw_ops_extra.py` 逐字一致）
----------------------------------------------------------
* `sk_or_doc` 接三种形态：① `docmodel.Document`（探 `payload`/`data`/`doc`/`document` 或 `as_dict()`）；
  ② 含 `sketches` 键的 L1 文档映射；③ 含 `entities` 键的裸草图映射。①② 用 `sketch_index`（缺省 0）。
* 长度一律**整数纳米**：`bool` / 非整数值浮点一律 `CadError(reason="not_integer_nm")`；角度用 `*_deg`。
* 操作签名统一 `op(sk_or_doc, **kwargs) -> list[str]`，返回**本次新建实体 uuid 列表**（圆恒 1 个）。
* 「没做」绝不写进返回值：参数非法 / 无解 / 歧义一律抛 `CadError(reason=…)`，不返回空列表冒充成功。
* **先全量校验再落库**：任一路径校验失败时文档里一条几何都不会多（自检第 4 条硬断言）。
* 本模块只 import `cadcore.errors` 与 `cadcore.sketch.constraints.new_uuid`，**不写 `__init__.py`**
  （`ops/` 与 `ops/features/` 都是命名空间包）。

四、已知边界（显式记账，不静默近似）
------------------------------------
* **取整**：2P / 3P / TTR 的半径与圆心可能落在非整数纳米上，一律「半值向上」取整到最近纳米，
  单轴误差 ≤ 0.5 nm；落库后按 `TOL_NM`（=5 nm）自证贴合，超出即抛
  `reason="circle_not_through_points"` / `"tangent_check_failed"`，**不放过**。
* **TTR 只认线段**（`kind="line"` 的既有实体 uuid，或直接给两点）；对圆/弧的相切构造**本层未实现**，
  传圆/弧引用 ⇒ `CadError(reason="unsupported_tangent_target")`（如实留缺，不假装支持）。
* **TTR 的线段延伸**：`within_segment=False` 时切点可落在线段延长线上（与 AutoCAD「相切」语义一致），
  缺省 `True` 只收段内解。
* **UI / 命令白名单仍缺**（`apps/opencad/server.py` 的 `/api/command` 与 `app.js` 未接本片 op）⇒
  族条目在 app 层仍是 partial；本片只把**库级几何缺环**补齐。这条写在 `REMAINING_GAPS` 里，不假绿。
* L1 无 bulge / 无相切约束类型：本片不写 `constraints`（不 monkeypatch 别的文件）。

五、自检（文件自带，不依赖 pytest）

    python3 oss-alternatives/packages/cadcore/cadcore/ops/features/draw_ops__w11.py

文末打印 `FEATURE_OK family=draw_ops op=circle_create verdict=PASS`。中文只出现在 docstring/注释里。
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from fractions import Fraction
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch.constraints import new_uuid

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "PRIMARY_OP",
    "REMAINING_GAPS",
    "TOL_NM",
    "circle_2p",
    "circle_3p",
    "circle_create",
    "circle_ttr",
]

FAMILY: str = "draw_ops"

#: 主操作名（`FEATURE_OK op=` 打的也是它）。
PRIMARY_OP: str = "circle_create"

#: 落库取整后允许的贴合/相切容差（整数纳米；族门 chk_circle 的 TOL_NM=50 是它的大号近亲）。
TOL_NM: int = 5

NM_PER_MM: int = 1_000_000

DEFAULT_LAYER: str = "0"

_ENTITY_FIELDS: tuple[str, ...] = ("uuid", "kind", "layer", "construction", "geom")

#: 本片之后**仍然缺**的东西（如实记账，不写成就已闭环）。
REMAINING_GAPS: tuple[str, ...] = (
    "app integration: apps/opencad/server.py /api/command and app.js do not expose any circle mode of this sheet",
    "TTR against circles/arcs (only line segments are supported here; unsupported_tangent_target)",
    "tangent snap / osnap tangency and a tangent constraint type do not exist in the frozen constraints.py",
)


# --------------------------------------------------------------------------------------
# 参数解析（整数纳米纪律）
# --------------------------------------------------------------------------------------
def _as_mapping(node: Any) -> Mapping[str, Any]:
    """把 Document / 文档映射 / 裸草图统一成映射；认不出就 loud 抛错。"""
    if isinstance(node, Mapping):
        return node
    for attr in ("payload", "data", "doc", "document"):
        cand = getattr(node, attr, None)
        if isinstance(cand, Mapping):
            return cand
    for attr in ("as_dict", "to_dict"):
        func = getattr(node, attr, None)
        if callable(func):
            try:
                out = func()
            except (AttributeError, TypeError, ValueError, CadError):
                continue
            if isinstance(out, Mapping):
                return out
    sketches = getattr(node, "sketches", None)
    if sketches is not None:
        return {"sketches": sketches}
    raise CadError("sketch or document mapping required", reason="bad_document",
                   node=type(node).__name__)


def _select_sketch(sk_or_doc: Any, sketch_index: Any = 0) -> MutableMapping[str, Any]:
    """取草图映射（含 `entities` 列表）；文档形态用 `sketch_index` 选。"""
    node = _as_mapping(sk_or_doc)
    if "entities" in node:
        sketch: Any = node
    else:
        sketches = node.get("sketches")
        if isinstance(sketches, (str, bytes)) or not isinstance(sketches, Sequence) or not sketches:
            raise CadError("document carries no sketch", reason="no_sketch")
        index = _nm(sketch_index, "sketch_index")
        if index < 0 or index >= len(sketches):
            raise CadError("sketch_index out of range", reason="sketch_index_out_of_range",
                           index=index, count=len(sketches))
        sketch = sketches[index]
    if not isinstance(sketch, MutableMapping):
        raise CadError("sketch must be a mutable mapping", reason="sketch_not_mutable",
                       node=type(sketch).__name__)
    entities = sketch.get("entities")
    if entities is None:
        sketch["entities"] = []
    elif not isinstance(entities, list):
        raise CadError("sketch entities must be a list", reason="entities_not_list",
                       node=type(entities).__name__)
    return sketch


def _nm(value: Any, field: str) -> int:
    """整数纳米（`bool` 与非整数值浮点一律拒绝）。"""
    if isinstance(value, bool):
        raise CadError("length must be an integer number of nanometres",
                       reason="not_integer_nm", field=field, value=repr(value))
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise CadError("length must be an integer number of nanometres",
                   reason="not_integer_nm", field=field, value=repr(value))


def _radius_nm(value: Any, field: str = "radius_nm") -> int:
    radius = _nm(value, field)
    if radius <= 0:
        raise CadError("radius must be a positive integer number of nanometres",
                       reason="radius_not_positive", field=field, value=radius)
    return radius


def _point(value: Any, field: str) -> tuple[int, int]:
    """两维点：`(x, y)` / `[x, y]` / `{"x_nm","y_nm"}`，全整数纳米。"""
    if isinstance(value, Mapping):
        x = value.get("x_nm", value.get("x"))
        y = value.get("y_nm", value.get("y"))
        return _nm(x, field + ".x"), _nm(y, field + ".y")
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CadError("point must be a 2-item sequence", reason="bad_point",
                       field=field, value=repr(value))
    items = list(value)
    if len(items) != 2:
        raise CadError("point must have exactly 2 coordinates", reason="bad_point",
                       field=field, count=len(items))
    return _nm(items[0], field + ".x"), _nm(items[1], field + ".y")


def _pick(kwargs: Mapping[str, Any], *names: str) -> tuple[Any, str]:
    """按别名顺序取第一个非 None 实参（认不出返回 (None, "")）。"""
    for name in names:
        if name in kwargs and kwargs[name] is not None:
            return kwargs[name], name
    return None, ""


def _take_point(kwargs: Mapping[str, Any], *names: str) -> tuple[int, int]:
    raw, used = _pick(kwargs, *names)
    if used == "":
        raise CadError("missing point argument", reason="missing_argument",
                       expected=list(names))
    return _point(raw, used)


def _round_nm(value: Fraction) -> int:
    """半值向上取整到整数纳米。"""
    if value < 0:
        return -_round_nm(-value)
    return int(math.floor(value + Fraction(1, 2)))


def _round_sqrt_nm(dist_sq: int, divisor: int = 1) -> int:
    """round(sqrt(dist_sq) / divisor)，整数运算，不走浮点。"""
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    scaled = dist_sq * divisor * divisor
    return _round_nm(Fraction(math.isqrt(scaled), divisor))


def _dist_sq(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _append_circle(sketch: MutableMapping[str, Any], geom: Mapping[str, int],
                   layer: Any) -> str:
    """按 L1 冻结五字段落一个 circle 实体，返回 uuid（落库前不再改 geom）。"""
    if not isinstance(layer, str) or not layer.strip():
        raise CadError("layer must be a non-empty string", reason="bad_layer",
                       value=repr(layer))
    entity = {
        "uuid": new_uuid(),
        "kind": "circle",
        "layer": layer,
        "construction": False,
        "geom": {"cx": int(geom["cx"]), "cy": int(geom["cy"]), "r": int(geom["r"])},
    }
    sketch["entities"].append(entity)
    return str(entity["uuid"])


def _check_on_circle(center: tuple[int, int], radius: int,
                     points: Sequence[tuple[int, int]], reason: str) -> None:
    """硬核验：给定点必须落在圆上（TOL_NM 内），否则 loud 抛错（不静默放过）。"""
    for index, point in enumerate(points):
        measured = _round_sqrt_nm(_dist_sq(center, point))
        if abs(measured - radius) > TOL_NM:
            raise CadError("point does not lie on the solved circle", reason=reason,
                           point_index=index, point=list(point),
                           center=list(center), radius=radius, measured=measured,
                           tol_nm=TOL_NM)


# --------------------------------------------------------------------------------------
# 几何求解（真算，不查表）
# --------------------------------------------------------------------------------------
def _geom_center_radius(center: tuple[int, int], radius: int) -> dict[str, int]:
    if radius <= 0:
        raise CadError("radius must be a positive integer number of nanometres",
                       reason="radius_not_positive", value=radius)
    return {"cx": center[0], "cy": center[1], "r": radius}


def _geom_2p(p1: tuple[int, int], p2: tuple[int, int]) -> dict[str, int]:
    if p1 == p2:
        raise CadError("two-point diameter needs two distinct points",
                       reason="coincident_points", point=list(p1))
    cx = _round_nm(Fraction(p1[0] + p2[0], 2))
    cy = _round_nm(Fraction(p1[1] + p2[1], 2))
    radius = _round_sqrt_nm(_dist_sq(p1, p2), 2)
    if radius <= 0:
        raise CadError("solved radius is not positive", reason="radius_not_positive",
                       radius=radius)
    geom = {"cx": cx, "cy": cy, "r": radius}
    _check_on_circle((cx, cy), radius, (p1, p2), "circle_not_through_points")
    return geom


def _geom_3p(p1: tuple[int, int], p2: tuple[int, int],
             p3: tuple[int, int]) -> dict[str, int]:
    if len({p1, p2, p3}) < 3:
        raise CadError("three-point circle needs three distinct points",
                       reason="coincident_points", points=[list(p1), list(p2), list(p3)])
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    det = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if det == 0:
        raise CadError("three points are collinear: no finite circumcircle",
                       reason="collinear_points", points=[list(p1), list(p2), list(p3)])
    s1 = x1 * x1 + y1 * y1
    s2 = x2 * x2 + y2 * y2
    s3 = x3 * x3 + y3 * y3
    ux = Fraction(s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2), det)
    uy = Fraction(s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1), det)
    cx = _round_nm(ux)
    cy = _round_nm(uy)
    radius = _round_sqrt_nm(_dist_sq((cx, cy), p1))
    if radius <= 0:
        raise CadError("solved radius is not positive", reason="radius_not_positive",
                       radius=radius)
    geom = {"cx": cx, "cy": cy, "r": radius}
    _check_on_circle((cx, cy), radius, (p1, p2, p3), "circle_not_through_points")
    return geom


def _line_endpoints(sketch: MutableMapping[str, Any], spec: Any,
                    field: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """线段来源：既有 line 实体 uuid，或直接给两点。"""
    if isinstance(spec, str):
        for entity in sketch["entities"]:
            if entity.get("uuid") == spec:
                if entity.get("kind") != "line":
                    raise CadError("TTR target must be a line segment",
                                   reason="unsupported_tangent_target",
                                   field=field, kind=entity.get("kind"))
                geom = entity.get("geom") or {}
                return ((float(geom["x1"]), float(geom["y1"])),
                        (float(geom["x2"]), float(geom["y2"])))
        raise CadError("line reference not found", reason="unknown_reference",
                       field=field, ref=spec)
    if isinstance(spec, Mapping):
        p1 = _point(spec.get("p1_nm", spec.get("p1")), field + ".p1")
        p2 = _point(spec.get("p2_nm", spec.get("p2")), field + ".p2")
        return ((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))
    if isinstance(spec, (str, bytes)) or not isinstance(spec, Sequence) or len(list(spec)) != 2:
        raise CadError("line spec must be a uuid or a pair of points",
                       reason="bad_line_spec", field=field, value=repr(spec))
    p1 = _point(list(spec)[0], field + ".p1")
    p2 = _point(list(spec)[1], field + ".p2")
    return ((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])))


def _intersect(p: tuple[float, float], d1: tuple[float, float],
               q: tuple[float, float], d2: tuple[float, float]):
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q[0] - p[0]) * d2[1] - (q[1] - p[1]) * d2[0]) / den
    return (p[0] + t * d1[0], p[1] + t * d1[1])


def _geom_ttr(sketch: MutableMapping[str, Any], line1: Any, line2: Any, radius: int,
              hint_nm: Any = None, pick: Any = None,
              within_segment: bool = True) -> dict[str, int]:
    """相切相切半径：两条线段各做双侧法向偏移 r，偏移线交点即候选圆心；逐候选反算切距。"""
    if not isinstance(within_segment, bool):
        raise CadError("within_segment must be a bool", reason="bad_argument",
                       field="within_segment", value=repr(within_segment))
    (a1, a2) = _line_endpoints(sketch, line1, "line1")
    (b1, b2) = _line_endpoints(sketch, line2, "line2")
    d1 = (a2[0] - a1[0], a2[1] - a1[1])
    d2 = (b2[0] - b1[0], b2[1] - b1[1])
    len1 = math.hypot(*d1)
    len2 = math.hypot(*d2)
    if len1 <= 0.0 or len2 <= 0.0:
        raise CadError("tangent target is degenerate", reason="degenerate_line",
                       lens=[len1, len2])
    n1 = (-d1[1] / len1, d1[0] / len1)
    n2 = (-d2[1] / len2, d2[0] / len2)
    radius_f = float(radius)

    candidates: list[tuple[int, int]] = []
    for sign1 in (1.0, -1.0):
        base1 = (a1[0] + sign1 * radius_f * n1[0], a1[1] + sign1 * radius_f * n1[1])
        for sign2 in (1.0, -1.0):
            base2 = (b1[0] + sign2 * radius_f * n2[0], b1[1] + sign2 * radius_f * n2[1])
            hit = _intersect(base1, d1, base2, d2)
            if hit is None:
                continue
            center = (_round_nm(Fraction(hit[0])), _round_nm(Fraction(hit[1])))
            if within_segment:
                t1 = ((center[0] - a1[0]) * d1[0] + (center[1] - a1[1]) * d1[1]) / (len1 * len1)
                t2 = ((center[0] - b1[0]) * d2[0] + (center[1] - b1[1]) * d2[1]) / (len2 * len2)
                if not (-1e-9 <= t1 <= 1.0 + 1e-9 and -1e-9 <= t2 <= 1.0 + 1e-9):
                    continue
            dist1 = abs(d1[0] * (center[1] - a1[1]) - d1[1] * (center[0] - a1[0])) / len1
            dist2 = abs(d2[0] * (center[1] - b1[1]) - d2[1] * (center[0] - b1[0])) / len2
            if abs(dist1 - radius_f) > TOL_NM or abs(dist2 - radius_f) > TOL_NM:
                raise CadError("tangency self-check failed", reason="tangent_check_failed",
                               center=list(center), radius=radius,
                               measured=[round(dist1, 6), round(dist2, 6)], tol_nm=TOL_NM)
            if center not in candidates:
                candidates.append(center)
    if not candidates:
        raise CadError("no circle of this radius is tangent to both targets",
                       reason="no_tangent_solution", radius=radius,
                       within_segment=within_segment)

    chosen: tuple[int, int]
    if hint_nm is not None:
        hint = _point(hint_nm, "hint_nm")
        chosen = min(candidates, key=lambda c: _dist_sq(c, hint))
    elif pick is not None:
        index = _nm(pick, "pick")
        if index < 0 or index >= len(candidates):
            raise CadError("pick index out of range", reason="pick_out_of_range",
                           pick=index, count=len(candidates))
        chosen = candidates[index]
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        raise CadError("multiple tangent solutions: pass hint_nm or pick",
                       reason="ambiguous_tangent_solution",
                       count=len(candidates), candidates=[list(c) for c in candidates])
    return {"cx": chosen[0], "cy": chosen[1], "r": radius}


# --------------------------------------------------------------------------------------
# 操作入口（冻结签名：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------
_MODE_ALIASES: dict[str, str] = {
    "auto": "auto",
    "center_radius": "center_radius",
    "center": "center_radius",
    "cr": "center_radius",
    "2p": "2p",
    "two_point": "2p",
    "diameter": "2p",
    "3p": "3p",
    "three_point": "3p",
    "ttr": "ttr",
    "tangent_tangent_radius": "ttr",
}


def circle_create(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """画圆主入口：`mode` ∈ auto / center_radius / 2p / 3p / ttr（见模块 docstring）。"""
    sketch = _select_sketch(sk_or_doc, kwargs.get("sketch_index", 0))
    layer = kwargs.get("layer", DEFAULT_LAYER)
    raw_mode, used = _pick(kwargs, "mode", "method", "how")
    mode = "auto" if used == "" else str(raw_mode).strip().lower()
    if mode not in _MODE_ALIASES:
        raise CadError("unknown circle mode", reason="unknown_mode",
                       mode=str(raw_mode), known=sorted(set(_MODE_ALIASES.values())))
    mode = _MODE_ALIASES[mode]

    has_center = _pick(kwargs, "center_nm", "center", "cx_nm")[1] != ""
    has_radius = _pick(kwargs, "radius_nm", "r_nm", "r", "radius")[1] != ""
    has_p2 = _pick(kwargs, "p2_nm", "p2", "second_nm")[1] != ""
    has_p3 = _pick(kwargs, "p3_nm", "p3", "third_nm")[1] != ""
    has_lines = _pick(kwargs, "line1", "line1_ref")[1] != ""

    if mode == "auto":
        if has_lines:
            mode = "ttr"
        elif has_p3:
            mode = "3p"
        elif has_p2 and not has_radius:
            mode = "2p"
        elif has_center and has_radius:
            mode = "center_radius"
        else:
            raise CadError("cannot infer circle mode from the given arguments",
                           reason="mode_required")

    if mode == "center_radius":
        raw_center, center_name = _pick(kwargs, "center_nm", "center")
        if center_name == "":
            cx, _ = _take_point(kwargs, "cx_nm", "cx")
            raw_center = None
            center = (cx[0], cx[1])
            raw_center, center_name = _pick(kwargs, "cx_nm", "cx")
            cy_raw, cy_name = _pick(kwargs, "cy_nm", "cy")
            if cy_name == "":
                raise CadError("missing center y", reason="missing_argument", expected=["cy_nm", "cy"])
            center = (_point([raw_center, cy_raw], "center")[0], _nm(cy_raw, cy_name))
        else:
            center = _point(raw_center, center_name)
        raw_radius, radius_name = _pick(kwargs, "radius_nm", "r_nm", "r", "radius")
        if radius_name == "":
            raise CadError("missing radius", reason="missing_argument",
                           expected=["radius_nm", "r_nm", "r"])
        geom = _geom_center_radius(center, _radius_nm(raw_radius, radius_name))
    elif mode == "2p":
        p1 = _take_point(kwargs, "p1_nm", "p1", "first_nm")
        p2 = _take_point(kwargs, "p2_nm", "p2", "second_nm")
        geom = _geom_2p(p1, p2)
    elif mode == "3p":
        p1 = _take_point(kwargs, "p1_nm", "p1", "first_nm")
        p2 = _take_point(kwargs, "p2_nm", "p2", "second_nm")
        p3 = _take_point(kwargs, "p3_nm", "p3", "third_nm")
        geom = _geom_3p(p1, p2, p3)
    else:  # ttr
        line1, name1 = _pick(kwargs, "line1", "line1_ref")
        line2, name2 = _pick(kwargs, "line2", "line2_ref")
        if name1 == "" or name2 == "":
            raise CadError("TTR needs two tangent targets", reason="missing_argument",
                           expected=["line1", "line2"])
        raw_radius, radius_name = _pick(kwargs, "radius_nm", "r_nm", "r", "radius")
        if radius_name == "":
            raise CadError("missing radius", reason="missing_argument",
                           expected=["radius_nm", "r_nm", "r"])
        geom = _geom_ttr(sketch, line1, line2, _radius_nm(raw_radius, radius_name),
                         hint_nm=kwargs.get("hint_nm", kwargs.get("near_nm")),
                         pick=kwargs.get("pick"),
                         within_segment=kwargs.get("within_segment", True))
    return [_append_circle(sketch, geom, layer)]


def circle_2p(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """两点直径画圆：`p1_nm` / `p2_nm`（别名 p1/p2/first_nm/second_nm）。"""
    kwargs["mode"] = "2p"
    return circle_create(sk_or_doc, **kwargs)


def circle_3p(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """三点定圆：`p1_nm` / `p2_nm` / `p3_nm`；共线 ⇒ CadError(reason="collinear_points")。"""
    kwargs["mode"] = "3p"
    return circle_create(sk_or_doc, **kwargs)


def circle_ttr(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """相切相切半径画圆：`line1` / `line2`（uuid 或两点）+ `radius_nm`，可选 `hint_nm`/`pick`。"""
    kwargs["mode"] = "ttr"
    return circle_create(sk_or_doc, **kwargs)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "circle_create": circle_create,
    "circle": circle_create,
    "add_circle": circle_create,
    "circle_2p": circle_2p,
    "circle_3p": circle_3p,
    "circle_ttr": circle_ttr,
}


# --------------------------------------------------------------------------------------
# 自检（硬断言，不依赖 pytest）
# --------------------------------------------------------------------------------------
def _selftest_sketch() -> dict[str, Any]:
    return {"entities": [], "constraints": [], "dimensions": []}


def _entity_of(sketch: Mapping[str, Any], uuid: str) -> Mapping[str, Any]:
    for entity in sketch["entities"]:
        if entity.get("uuid") == uuid:
            return entity
    raise AssertionError("returned uuid %r is not resolvable in the sketch" % (uuid,))


def selftest() -> int:
    problems: list[str] = []
    checks = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        if not ok:
            problems.append("%s detail=%s" % (name, detail))

    def expect_error(name: str, reason: str, func: Callable[[], Any]) -> None:
        nonlocal checks
        checks += 1
        try:
            func()
        except CadError as exc:
            if exc.reason != reason:
                problems.append("%s expected reason=%s got=%s" % (name, reason, exc.reason))
        else:
            problems.append("%s expected CadError(reason=%s) but nothing was raised" % (name, reason))

    mm = NM_PER_MM

    # 1. 两点直径：直径端点 (0,0)-(40mm,30mm) ⇒ 圆心 (20mm,15mm)、半径 25mm（硬断言）
    sk = _selftest_sketch()
    uuids = circle_2p(sk, p1_nm=(0, 0), p2_nm=(40 * mm, 30 * mm))
    entity = _entity_of(sk, uuids[0]) if len(uuids) == 1 else {}
    geom = entity.get("geom", {}) if entity else {}
    check("two_point_diameter", len(uuids) == 1 and entity.get("kind") == "circle"
          and (geom.get("cx"), geom.get("cy"), geom.get("r")) == (20 * mm, 15 * mm, 25 * mm)
          and set(entity.keys()) == set(_ENTITY_FIELDS),
          "entity=%r" % (dict(entity),))

    # 2. 三点定圆：直角边 40/30 ⇒ 斜边中点 (20mm,15mm) 为外心、r=25mm；三点全贴合（硬断言）
    sk = _selftest_sketch()
    uuids = circle_3p(sk, p1_nm=(0, 0), p2_nm=(40 * mm, 0), p3_nm=(0, 30 * mm))
    geom = _entity_of(sk, uuids[0])["geom"]
    fitted = all(abs(_round_sqrt_nm(_dist_sq((geom["cx"], geom["cy"]), p)) - geom["r"]) <= TOL_NM
                 for p in ((0, 0), (40 * mm, 0), (0, 30 * mm)))
    check("three_point_circle", len(uuids) == 1
          and (geom["cx"], geom["cy"], geom["r"]) == (20 * mm, 15 * mm, 25 * mm) and fitted,
          "geom=%r fitted=%s" % (dict(geom), fitted))

    # 3. 共线三点：必须报 collinear_points 且一条几何都不落库（先校验后落库）
    sk = _selftest_sketch()
    expect_error("collinear_points", "collinear_points",
                 lambda: circle_3p(sk, p1_nm=(0, 0), p2_nm=(10 * mm, 10 * mm),
                                   p3_nm=(20 * mm, 20 * mm)))
    check("collinear_leaves_document_clean", sk["entities"] == [] and sk["constraints"] == [],
          "entities=%r" % (sk["entities"],))

    # 4. TTR：两条互相垂直的线段 (y=0)/(x=0)，r=10mm，hint 取第一象限 ⇒ 圆心 (10mm,10mm)
    sk = _selftest_sketch()
    l1 = [{"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
           "geom": {"x1": -100 * mm, "y1": 0, "x2": 100 * mm, "y2": 0}}]
    l2 = [{"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
           "geom": {"x1": 0, "y1": -100 * mm, "x2": 0, "y2": 100 * mm}}]
    sk["entities"].extend(l1 + l2)
    uuids = circle_ttr(sk, line1=l1[0]["uuid"], line2=l2[0]["uuid"], radius_nm=10 * mm,
                       hint_nm=(10 * mm, 10 * mm))
    geom = _entity_of(sk, uuids[0])["geom"]
    check("ttr_first_quadrant", len(uuids) == 1
          and (geom["cx"], geom["cy"], geom["r"]) == (10 * mm, 10 * mm, 10 * mm),
          "geom=%r" % (dict(geom),))
    # 歧义不猜：同一输入不给 hint ⇒ 4 个候选，必须 loud 抛错
    expect_error("ttr_ambiguous", "ambiguous_tangent_solution",
                 lambda: circle_ttr(sk, line1=l1[0]["uuid"], line2=l2[0]["uuid"],
                                    radius_nm=10 * mm))
    # 相切目标必须是线段：拿圆当目标 ⇒ unsupported_tangent_target（如实留缺）
    sk["entities"].append({"uuid": new_uuid(), "kind": "circle", "layer": "0",
                           "construction": False,
                           "geom": {"cx": 0, "cy": 0, "r": 50 * mm}})
    expect_error("ttr_unsupported_target", "unsupported_tangent_target",
                 lambda: circle_ttr(sk, line1=sk["entities"][-1]["uuid"],
                                    line2=l2[0]["uuid"], radius_nm=10 * mm))

    # 5. 圆心+半径对照路径
    sk = _selftest_sketch()
    uuids = circle_create(sk, center_nm=(10 * mm, 20 * mm), radius_nm=5 * mm)
    geom = _entity_of(sk, uuids[0])["geom"]
    check("center_radius", (geom["cx"], geom["cy"], geom["r"]) == (10 * mm, 20 * mm, 5 * mm),
          "geom=%r" % (dict(geom),))

    # 6. 参数纪律：零/负半径、非整数纳米、bool、重合两点
    expect_error("radius_zero", "radius_not_positive",
                 lambda: circle_create(_selftest_sketch(), center_nm=(0, 0), radius_nm=0))
    expect_error("reject_float_nm", "not_integer_nm",
                 lambda: circle_create(_selftest_sketch(), center_nm=(0, 0), radius_nm=1.5))
    expect_error("reject_bool_nm", "not_integer_nm",
                 lambda: circle_create(_selftest_sketch(), center_nm=(True, 0), radius_nm=10))
    expect_error("coincident_2p", "coincident_points",
                 lambda: circle_2p(_selftest_sketch(), p1_nm=(0, 0), p2_nm=(0, 0)))

    if problems:
        for item in problems:
            print("DRAW_OPS_W11_SELFTEST problem=%s" % item)
        print("DRAW_OPS_W11_SELFTEST checks=%d failed=%d" % (checks, len(problems)))
        return 1
    print("DRAW_OPS_W11_SELFTEST checks=%d failed=0 ops=%d" % (checks, len(OPERATIONS)))
    return 0


if __name__ == "__main__":
    _rc = selftest()
    print("FEATURE_OK family=%s op=%s verdict=%s" % (FAMILY, PRIMARY_OP,
                                                     "PASS" if _rc == 0 else "FAIL"))
    raise SystemExit(_rc)
