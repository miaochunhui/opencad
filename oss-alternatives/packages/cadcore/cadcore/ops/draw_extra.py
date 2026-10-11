#!/usr/bin/env python3
"""DRAW-EXTRA 族模块：绘图补全（椭圆 / 样条近似 / 构造线与射线 / 点 / 打断 / 合并 / 对齐 / 拉伸）。

族接口（冻结）：

    FAMILY = "draw_extra"
    OPERATIONS: dict[str, Callable]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]

返回值口径（全族统一，不含糊）：

* 返回**本次调用真正改动/新建**的对象 uuid（`break_at`/`merge` 返回新建实体的 uuid；
  `align`/`stretch` 返回被移动的实体 uuid）；
* 「没做」绝不写进返回值：跳过一律不返回，需要失败则抛 `cadcore.errors.CadError(reason=...)`；
  整批一个都没命中 ⇒ 抛错（`empty_selection`），**不返回空列表冒充成功**。

输入口径：

* `sk_or_doc` 接受三种形态：① `docmodel.Document`；② L1 文档映射（含 `sketches` 键）；
  ③ 裸草图（含 `entities` 键的映射）。①② 用 `sketch_index`（缺省 0）选草图。
* 长度一律**整数纳米**：所有 `*_nm` 参数都是 `int`（`bool` 与"非整数值"浮点一律拒绝，抛 `CadError`）；
  角度用 `*_deg`（float）。
* 实体按 L1 冻结字段落库：`{uuid, kind, layer, construction, geom}`（不自造第六个字段，
  因此 `docmodel.check_invariants()` 不被本模块污染，自检有断言）。

本模块**新引入**的两种实体 kind（L1 的 `entity` 字段集不限制 kind，`edit/modify.py` 的仿射核只认
line/arc/circle/polyline —— 这是**有意的分工**，不是缺口）：

* `ellipse`：`geom = {cx, cy, rx, ry, rotation_deg}`（`rotation_deg` 是绕中心逆时针旋转，长轴沿 x）；
* `point`：`geom = {x, y}`。

：:`modify.move()` 能直接搬的 kind（line/arc/circle/polyline）一律**复用** `cadcore.edit.modify`，
本模块只为 `ellipse`/`point` 自己写平移（同一个"整数纳米 + 最小改动"口径）。

已知边界（**显式抛错，不静默近似**，逐条见各操作 docstring）：

* `spline` 是**三次 Catmull-Rom 插值**（过控制点的近似，**不是** NURBS 精确 B 样条）；`degree != 3`
  ⇒ `CadError(reason="unsupported_degree")`；
* L1 没有无限几何 ⇒ `ray` 以显式 `length_nm` 的**有限段**落库（**不冒充无限射线**），语义写在
  `construction=True` 上；
* `break_at` 只支持 `line` / `polyline`；`circle`/`arc` 抛 `unsupported_kind`（圆破成两段弧要有
  "弧与圆的端点口径"配套，不在本模块草率实现）；
* `stretch` 对**曲线**（circle/arc/ellipse）只做整体平移：窗口只切到一部分 ⇒
  `CadError(reason="partial_stretch_unsupported")`（**不做**假拉伸）。

三态口径（与 AGENTS.md E2.3 一致）：`0` = 真为零；`-1` = 未采；`"NA"` = 不适用。本模块不产用户
可见文案（不写 i18n key 之外的字符串常量），错误一律 `reason` + ASCII detail。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/draw_extra.py

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import math
import os
import sys
from fractions import Fraction
from typing import Any, Callable, Mapping, Sequence

from ..errors import CadError
from .. import docmodel as _docmodel
from ..edit import modify as _modify
from ..sketch import constraints as _constraints

__all__ = [
    "FAMILY", "OPERATIONS", "NM_PER_MM",
    "ellipse", "spline", "construction_line", "ray", "point",
    "break_at", "merge", "align", "stretch",
    "module_facts", "selftest",
]

#: 族 id（冻结接口）。
FAMILY = "draw_extra"

NM_PER_MM = 1_000_000

#: 三态口径常量（0=真为零 / -1=未采 / "NA"=不适用）；本模块自身不产测量值，只把它带进机读面。
ZERO = 0
UNMEASURED = -1
NA = "NA"

#: 缺省图层（与 `edit/modify.py` / `sketch/primitives.py` 的缺省一致）。
DEFAULT_LAYER = "0"

#: 本模块新引入的实体 kind（见模块 docstring 的边界说明）。
NEW_KINDS: tuple[str, ...] = ("ellipse", "point")

#: `spline` 支持的阶（三次 Catmull-Rom；其它阶显式抛错）。
SPLINE_DEGREE = 3

_AXES = ("x", "y")
_ANCHOR_KEYS = ("start", "end", "mid", "center", "min", "max")
_ALIGN_TARGETS = ("min", "max", "center", "first")


# --------------------------------------------------------------------------------------
# 入参校验（唯一解释入口；长度一律整数纳米）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造一条带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(str(message), reason=str(reason), **details)


def _as_nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点 / 非数一律报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_length", f"{what} must be an integer nanometre value", what=what,
                    value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise _fail("invalid_length", f"{what} must be a whole number of nm", what=what,
                        value=repr(value))
        value = int(value)
    if positive and value <= 0:
        raise _fail("invalid_length", f"{what} must be > 0 nm", what=what, value=int(value))
    return int(value)


def _as_point(value: Any, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_point", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    return (_as_nm(value[0], f"{what}.x"), _as_nm(value[1], f"{what}.y"))


def _as_points(value: Any, what: str) -> list[tuple[int, int]]:
    """点串入参 → `[(x_nm, y_nm), ...]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_point", f"{what} must be a sequence of (x_nm, y_nm) pairs", what=what,
                    value=repr(value))
    return [_as_point(item, f"{what}[{index}]") for index, item in enumerate(value)]


def _as_window(value: Any) -> tuple[int, int, int, int]:
    """窗口入参 `(x1, y1, x2, y2)` → 规范化后的 `(xmin, ymin, xmax, ymax)`（零面积窗口报错）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise _fail("invalid_window", "window_nm must be (x1_nm, y1_nm, x2_nm, y2_nm)",
                    value=repr(value))
    x1, y1, x2, y2 = (_as_nm(item, f"window_nm[{index}]") for index, item in enumerate(value))
    if x1 == x2 or y1 == y2:
        raise _fail("degenerate_window", "window_nm must not be a zero-width/zero-height window",
                    window_nm=[x1, y1, x2, y2])
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _as_int(value: Any, what: str, *, minimum: int) -> int:
    """整数入参（bool 不算数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", f"{what} must be an integer", what=what, value=repr(value))
    if value < minimum:
        raise _fail("invalid_argument", f"{what} must be >= {minimum}", what=what, value=int(value))
    return int(value)


def _as_flag(value: Any, what: str) -> bool:
    """布尔入参（严格 bool，不接受 0/1）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))
    return bool(value)


def _as_deg(value: Any, what: str = "angle_deg") -> float:
    """角度入参 → float（`*_deg` 口径；非有限值报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_angle", f"{what} must be a number of degrees", what=what, value=repr(value))
    angle = float(value)
    if not math.isfinite(angle):
        raise _fail("invalid_angle", f"{what} must be finite", what=what, value=repr(value))
    return angle


def _as_layer(value: Any) -> str:
    """图层名（非空字符串）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "layer must be a non-empty string", value=repr(value))
    return value


def _as_uuid(ref: Any) -> str:
    """实体引用 → uuid 文本（接受 uuid 串或实体映射）。"""
    if isinstance(ref, Mapping):
        ref = ref.get("uuid")
    if not isinstance(ref, str) or not ref.strip():
        raise _fail("unknown_reference", "ref must be an entity uuid or an entity mapping",
                    ref=repr(ref))
    return ref


# --------------------------------------------------------------------------------------
# 草图/实体层适配（唯一的模型解释入口）
# --------------------------------------------------------------------------------------


def _sketch(sk_or_doc: Any, *, sketch_index: int = 0) -> Mapping[str, Any]:
    """`sk_or_doc` → 草图映射。三种入参形态见模块 docstring；取不到草图抛 `no_sketch`。"""
    if isinstance(sk_or_doc, Mapping):
        if "entities" in sk_or_doc:
            return sk_or_doc
        sketches: Any = sk_or_doc.get("sketches")
    else:
        sketches = getattr(sk_or_doc, "sketches", None)
        if sketches is None and hasattr(sk_or_doc, "entities"):
            return sk_or_doc
    if isinstance(sketches, (str, bytes)) or not isinstance(sketches, Sequence) or not sketches:
        raise _fail("no_sketch", "source exposes no sketch to draw into")
    index = _as_int(sketch_index, "sketch_index", minimum=0)
    if index >= len(sketches):
        raise _fail("no_sketch", "sketch_index out of range", sketch_index=index,
                    sketch_count=len(sketches))
    sketch = sketches[index]
    if not isinstance(sketch, Mapping):
        raise _fail("sketch_not_mapping", "sketch entry is not an L1 mapping",
                    sketch_index=index, type=type(sketch).__name__)
    return sketch


def _entities(sk: Any) -> list[Any]:
    """草图实体表（`sketch/constraints.py` 是这份字段名的唯一真源）。"""
    try:
        return _constraints.sketch_entities(sk)
    except CadError as exc:
        raise _fail("sketch_invalid", "sketch has no usable entity list", detail=exc.message) from exc


def _kind_of(entity: Any) -> str:
    """实体 kind（未知/缺失一律报错，不猜）。"""
    kind = str(entity.get("kind") or "") if isinstance(entity, Mapping) else ""
    if not kind:
        raise _fail("unsupported_geometry", "entity has no kind", entity=repr(entity)[:120])
    return kind


def _geom_of(entity: Any) -> dict[str, Any]:
    """实体 geom（映射，缺失报错）。"""
    geom = entity.get("geom") if isinstance(entity, Mapping) else None
    if not isinstance(geom, dict):
        raise _fail("unsupported_geometry", "entity has no geom mapping", kind=_kind_of(entity))
    return geom


def _resolve(sk: Any, ref: Any) -> Any:
    """引用 → 实体对象（找不到抛 `unknown_reference`，不静默跳过）。"""
    want = _as_uuid(ref)
    for entity in _entities(sk):
        if isinstance(entity, Mapping) and str(entity.get("uuid")) == want:
            return entity
    raise _fail("unknown_reference", "entity uuid not found in sketch", uuid=want)


def _append(sk: Any, *, kind: str, geom: dict[str, Any], layer: str,
            construction: bool) -> str:
    """按 L1 冻结字段追加实体并返回 uuid（字段集与 `edit/modify.py` 逐字一致）。"""
    entity = {
        "uuid": _constraints.new_uuid(),
        "kind": kind,
        "layer": layer,
        "construction": bool(construction),
        "geom": geom,
    }
    _entities(sk).append(entity)
    return str(entity["uuid"])


def _drop(sk: Any, entity: Any) -> None:
    """按**对象身份**移除实体（不按值比较：值相等的两个实体不许误删另一个）。"""
    entities = _entities(sk)
    for index, item in enumerate(entities):
        if item is entity:
            entities.pop(index)
            return
    raise _fail("unknown_reference", "entity is not in this sketch", uuid=repr(entity.get("uuid")))


def _is_inside(point: tuple[int, int], window: tuple[int, int, int, int]) -> bool:
    """点是否落在窗口内（含边界）。"""
    xmin, ymin, xmax, ymax = window
    return xmin <= point[0] <= xmax and ymin <= point[1] <= ymax


# --------------------------------------------------------------------------------------
# 几何基元（纯函数；全部按整数纳米取整，除法只用有理数/受控浮点）
# --------------------------------------------------------------------------------------


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米（就近取整；真源分辨率就是 1 nm）。"""
    return int(round(value))


def _ellipse_points(center: tuple[int, int], rx: int, ry: int, rotation_deg: float,
                    samples: int) -> list[tuple[int, int]]:
    """椭圆参数采样（`samples` 个点，逆时针；**不重复首点**）。"""
    rotation = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    points: list[tuple[int, int]] = []
    for index in range(samples):
        theta = 2.0 * math.pi * index / samples
        local_x, local_y = rx * math.cos(theta), ry * math.sin(theta)
        points.append((_round_nm(center[0] + local_x * cos_r - local_y * sin_r),
                       _round_nm(center[1] + local_x * sin_r + local_y * cos_r)))
    return points


def _ellipse_half_extents(rx: int, ry: int, rotation_deg: float) -> tuple[float, float]:
    """旋转椭圆的轴对齐半宽/半高（包络盒）。"""
    rotation = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    half_w = math.hypot(rx * cos_r, ry * sin_r)
    half_h = math.hypot(rx * sin_r, ry * cos_r)
    return (half_w, half_h)


def _catmull_rom(points: list[tuple[float, float]], samples_per_span: int,
                 closed: bool) -> list[tuple[float, float]]:
    """三次 Catmull-Rom 采样（过控制点；开放曲线两端用端点自身补齐切向）。"""
    count = len(points)
    spans = count if closed else count - 1
    samples: list[tuple[float, float]] = []

    def control(index: int) -> tuple[float, float]:
        if closed:
            return points[index % count]
        return points[min(max(index, 0), count - 1)]

    for span in range(spans):
        p0, p1, p2, p3 = (control(span - 1), control(span), control(span + 1), control(span + 2))
        for step in range(samples_per_span):
            t = step / float(samples_per_span)
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2.0 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2.0 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3)
            samples.append((x, y))
    if not closed:
        samples.append(points[-1])
    return samples


def _entity_points(entity: Any) -> list[tuple[int, int]]:
    """实体 → 关键点表（锚点/窗口选择/拉伸共用；不支持的种类抛 `unsupported_kind`）。"""
    kind = _kind_of(entity)
    geom = _geom_of(entity)
    if kind == "line":
        return [(int(geom["x1"]), int(geom["y1"])), (int(geom["x2"]), int(geom["y2"]))]
    if kind == "point":
        return [(int(geom["x"]), int(geom["y"]))]
    if kind == "polyline":
        raw = geom.get("points")
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 2:
            raise _fail("geometry_invalid", "polyline needs at least 2 points")
        return [(int(item[0]), int(item[1])) for item in raw]
    if kind in ("circle", "arc"):
        cx, cy, radius = int(geom["cx"]), int(geom["cy"]), int(geom["r"])
        return [(cx - radius, cy - radius), (cx + radius, cy + radius)]
    if kind == "ellipse":
        half_w, half_h = _ellipse_half_extents(int(geom["rx"]), int(geom["ry"]),
                                               float(geom.get("rotation_deg", 0.0)))
        cx, cy = int(geom["cx"]), int(geom["cy"])
        return [(_round_nm(cx - half_w), _round_nm(cy - half_h)),
                (_round_nm(cx + half_w), _round_nm(cy + half_h))]
    raise _fail("unsupported_kind", "entity kind is not supported by this family", kind=kind,
                uuid=str(entity.get("uuid")))


def _bbox(entity: Any) -> tuple[int, int, int, int]:
    """实体包络盒 `(xmin, ymin, xmax, ymax)`。"""
    points = _entity_points(entity)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _anchor(entity: Any, axis: str, key: str) -> int:
    """实体在 `axis` 上的锚点坐标（`key` 语义见 `align` docstring）。"""
    points = _entity_points(entity)
    xmin, ymin, xmax, ymax = _bbox(entity)
    if key == "min":
        return xmin if axis == "x" else ymin
    if key == "max":
        return xmax if axis == "x" else ymax
    if key == "center":
        return (xmin + xmax) // 2 if axis == "x" else (ymin + ymax) // 2
    if key == "start":
        return points[0][0] if axis == "x" else points[0][1]
    if key == "end":
        return points[-1][0] if axis == "x" else points[-1][1]
    if key == "mid":
        return (points[0][0] + points[-1][0]) // 2 if axis == "x" else (points[0][1] + points[-1][1]) // 2
    raise _fail("invalid_argument", "unsupported anchor key", key=str(key), known=list(_ANCHOR_KEYS))


def _translate_one(sk: Any, entity: Any, *, dx_nm: int, dy_nm: int) -> str:
    """平移单个实体：L1 冻结的四种 kind 复用 `edit/modify.move`，本族新 kind 自己搬。"""
    kind = _kind_of(entity)
    entity_uuid = str(entity.get("uuid"))
    if kind in _modify.SUPPORTED_KINDS:
        return str(_modify.move(sk, [entity_uuid], dx_nm=dx_nm, dy_nm=dy_nm)[0])
    geom = _geom_of(entity)
    if kind == "point":
        geom["x"] = int(geom["x"]) + dx_nm
        geom["y"] = int(geom["y"]) + dy_nm
        return entity_uuid
    if kind == "ellipse":
        geom["cx"] = int(geom["cx"]) + dx_nm
        geom["cy"] = int(geom["cy"]) + dy_nm
        return entity_uuid
    raise _fail("unsupported_kind", "entity kind cannot be translated by this family", kind=kind,
                uuid=entity_uuid)


# --------------------------------------------------------------------------------------
# 1) 椭圆
# --------------------------------------------------------------------------------------


def ellipse(sk_or_doc: Any, *, center_nm: tuple[int, int], rx_nm: int, ry_nm: int,
            rotation_deg: float = 0.0, segments: int = 0, layer: str = DEFAULT_LAYER,
            construction: bool = False, sketch_index: int = 0) -> list[str]:
    """画椭圆：`segments == 0` 落 `ellipse` 实体；`segments >= 3` 落**折线近似**（`polyline`, closed）。

    - `center_nm`：中心；`rx_nm`/`ry_nm`：长/短半轴（**必须 > 0**，零半径不是椭圆）；
    - `rotation_deg`：绕中心逆时针（长轴沿 x 为 0°）；`rx == ry` 时等价于圆（合法，不特殊处理）；
    - `segments`：折线近似的段数（`1`/`2` 抛 `too_few_segments`：两点/一条线冒充不了椭圆）；
    - 返回 `[新建实体 uuid]`（kind 由 `segments` 决定，见上）。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    center = _as_point(center_nm, "center_nm")
    rx = _as_nm(rx_nm, "rx_nm", positive=True)
    ry = _as_nm(ry_nm, "ry_nm", positive=True)
    rotation = _as_deg(rotation_deg, "rotation_deg")
    count = _as_int(segments, "segments", minimum=0)
    if 0 < count < 3:
        raise _fail("too_few_segments", "segments must be 0 (native ellipse) or >= 3", segments=count)
    layer_name = _as_layer(layer)
    flag = _as_flag(construction, "construction")
    if count == 0:
        geom: dict[str, Any] = {"cx": center[0], "cy": center[1], "rx": rx, "ry": ry,
                                "rotation_deg": rotation}
        return [_append(sk, kind="ellipse", geom=geom, layer=layer_name, construction=flag)]
    points = [[point[0], point[1]] for point in _ellipse_points(center, rx, ry, rotation, count)]
    return [_append(sk, kind="polyline", geom={"points": points, "closed": True},
                    layer=layer_name, construction=flag)]


# --------------------------------------------------------------------------------------
# 2) 样条近似
# --------------------------------------------------------------------------------------


def spline(sk_or_doc: Any, *, points_nm: Sequence[tuple[int, int]], samples_per_span: int = 8,
           degree: int = SPLINE_DEGREE, closed: bool = False, layer: str = DEFAULT_LAYER,
           construction: bool = False, sketch_index: int = 0) -> list[str]:
    """过控制点的**三次 Catmull-Rom 插值**折线近似（`degree` 只支持 3，其它阶抛 `unsupported_degree`）。

    - **诚实边界**：这是"过点的插值近似"，**不是** NURBS/精确 B 样条（本包没有节点矢量与权重的
      L1 落点）；结果落成 `polyline`，点数 = `spans × samples_per_span (+1)`；
    - `points_nm`：控制点（≥ 2；`closed=True` 时首尾自动相接，不再重复补点）；
    - `samples_per_span`：每跨采样数（≥ 1）；曲线**过**每个控制点（`t=0`/`t=1` 精确落点）；
    - 返回 `[新建 polyline uuid]`。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    control = _as_points(points_nm, "points_nm")
    if len(control) < 2:
        raise _fail("too_few_points", "spline needs at least 2 control points", count=len(control))
    order = _as_int(degree, "degree", minimum=1)
    if order != SPLINE_DEGREE:
        raise _fail("unsupported_degree", "only cubic (degree=3) Catmull-Rom is implemented",
                    degree=order, supported=SPLINE_DEGREE)
    samples = _as_int(samples_per_span, "samples_per_span", minimum=1)
    is_closed = _as_flag(closed, "closed")
    layer_name = _as_layer(layer)
    flag = _as_flag(construction, "construction")
    floats = [(float(point[0]), float(point[1])) for point in control]
    if is_closed:
        floats = floats + [floats[0]]  # 用首点作重复尾点定切向，采样时再绕回
        floats = floats[:-1]
    sampled = _catmull_rom(floats, samples, is_closed)
    points = [[_round_nm(x), _round_nm(y)] for x, y in sampled]
    geom: dict[str, Any] = {"points": points}
    if is_closed:
        geom["closed"] = True
    return [_append(sk, kind="polyline", geom=geom, layer=layer_name, construction=flag)]


# --------------------------------------------------------------------------------------
# 3) 构造线 / 射线
# --------------------------------------------------------------------------------------


def construction_line(sk_or_doc: Any, *, start_nm: tuple[int, int], end_nm: tuple[int, int],
                      layer: str = DEFAULT_LAYER, sketch_index: int = 0) -> list[str]:
    """画**构造线**（`construction=True` 的有限线段，参考几何用；两端点都要显式给）。

    退化（`start_nm == end_nm`）抛 `degenerate_geometry` —— 零长线不是构造线。返回 `[uuid]`。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    start = _as_point(start_nm, "start_nm")
    end = _as_point(end_nm, "end_nm")
    if start == end:
        raise _fail("degenerate_geometry", "construction line needs two distinct points",
                    start_nm=list(start), end_nm=list(end))
    layer_name = _as_layer(layer)
    geom = {"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]}
    return [_append(sk, kind="line", geom=geom, layer=layer_name, construction=True)]


def ray(sk_or_doc: Any, *, start_nm: tuple[int, int], through_nm: tuple[int, int],
        length_nm: int, layer: str = DEFAULT_LAYER, sketch_index: int = 0) -> list[str]:
    """画**射线**：从 `start_nm` 沿 `through_nm` 方向、长度 `length_nm`（**必须显式给，> 0**）。

    - **诚实边界**：L1 没有无限几何 ⇒ 这里落的是 `construction=True` 的**有限**线段，
      **不冒充无限射线**；方向由 `through_nm` 唯一确定（`through == start` 抛 `degenerate_geometry`）；
    - 返回 `[uuid]`；端点 = `start + unit(through - start) × length_nm`（就近取整到 1 nm）。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    start = _as_point(start_nm, "start_nm")
    through = _as_point(through_nm, "through_nm")
    length = _as_nm(length_nm, "length_nm", positive=True)
    if start == through:
        raise _fail("degenerate_geometry", "ray needs a direction: through_nm != start_nm",
                    start_nm=list(start), through_nm=list(through))
    layer_name = _as_layer(layer)
    dx, dy = float(through[0] - start[0]), float(through[1] - start[1])
    norm = math.hypot(dx, dy)
    end = (_round_nm(start[0] + dx / norm * length), _round_nm(start[1] + dy / norm * length))
    if end == start:
        raise _fail("degenerate_geometry", "ray collapsed to a point", start_nm=list(start),
                    length_nm=length)
    geom = {"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]}
    return [_append(sk, kind="line", geom=geom, layer=layer_name, construction=True)]


# --------------------------------------------------------------------------------------
# 4) 点
# --------------------------------------------------------------------------------------


def point(sk_or_doc: Any, *, point_nm: tuple[int, int], layer: str = DEFAULT_LAYER,
          construction: bool = False, sketch_index: int = 0) -> list[str]:
    """画**点**实体（`kind="point"`，`geom={x, y}` 整数纳米；参考点/定位点用）。

    返回 `[uuid]`。本包此前没有点实体，`edit/modify.py` 的仿射核不认这个 kind（有意分工）——
    `align`/`stretch` 已内建对它的搬运。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    position = _as_point(point_nm, "point_nm")
    layer_name = _as_layer(layer)
    flag = _as_flag(construction, "construction")
    return [_append(sk, kind="point", geom={"x": position[0], "y": position[1]},
                    layer=layer_name, construction=flag)]


# --------------------------------------------------------------------------------------
# 5) 打断
# --------------------------------------------------------------------------------------


def _split_line_at(sk: Any, entity: Any, *, at_nm: Any, t: Any) -> list[str]:
    """直线在一点处破成两段（点必须落在**线段内部**且是整数纳米可表示的线上点）。"""
    geom = _geom_of(entity)
    a = (int(geom["x1"]), int(geom["y1"]))
    b = (int(geom["x2"]), int(geom["y2"]))
    vx, vy = b[0] - a[0], b[1] - a[1]
    if t is not None:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(float(t)):
            raise _fail("invalid_argument", "t must be a finite number", value=repr(t))
        ratio = Fraction(str(float(t)))
    elif at_nm is not None:
        point_nm = _as_point(at_nm, "at_nm")
        wx, wy = point_nm[0] - a[0], point_nm[1] - a[1]
        denominator = vx * vx + vy * vy
        if denominator == 0:
            raise _fail("degenerate_geometry", "line has zero length", uuid=str(entity.get("uuid")))
        ratio = Fraction(wx * vx + wy * vy, denominator)  # 垂足参数（精确有理数）
    else:
        raise _fail("invalid_argument", "break_at needs either at_nm or t")
    if not (0 < ratio < 1):
        raise _fail("outside_entity", "break point is not strictly inside the entity",
                    uuid=str(entity.get("uuid")), t=str(ratio))
    split = (a[0] + ratio * vx, a[1] + ratio * vy)
    split_nm = (_round_nm(float(split[0])), _round_nm(float(split[1])))
    if split_nm == a or split_nm == b:
        raise _fail("outside_entity", "break point coincides with an endpoint",
                    uuid=str(entity.get("uuid")), point_nm=list(split_nm))
    if vx * (split_nm[1] - a[1]) - vy * (split_nm[0] - a[0]) != 0:
        raise _fail("not_on_entity",
                    "requested point cannot be represented on this line at 1 nm resolution; "
                    "pass a point returned by precision.osnap",
                    uuid=str(entity.get("uuid")), point_nm=list(split_nm))
    layer_name = str(entity.get("layer") or DEFAULT_LAYER)
    flag = bool(entity.get("construction", False))
    first = _append(sk, kind="line",
                    geom={"x1": a[0], "y1": a[1], "x2": split_nm[0], "y2": split_nm[1]},
                    layer=layer_name, construction=flag)
    second = _append(sk, kind="line",
                     geom={"x1": split_nm[0], "y1": split_nm[1], "x2": b[0], "y2": b[1]},
                     layer=layer_name, construction=flag)
    _drop(sk, entity)
    return [first, second]


def _split_polyline_at(sk: Any, entity: Any, *, at_nm: Any, vertex: Any) -> list[str]:
    """折线在**内部顶点**处破成两条折线（闭合折线不支持：破开后的 closed 语义不确定）。"""
    geom = _geom_of(entity)
    raw = geom.get("points")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 3:
        raise _fail("geometry_invalid", "polyline needs at least 3 points to be broken")
    if bool(geom.get("closed", False)):
        raise _fail("unsupported_kind", "closed polyline cannot be broken by this family",
                    uuid=str(entity.get("uuid")))
    points = [(int(item[0]), int(item[1])) for item in raw]
    if vertex is not None:
        index = _as_int(vertex, "vertex", minimum=0)
    elif at_nm is not None:
        target = _as_point(at_nm, "at_nm")
        index = min(range(len(points)),
                    key=lambda i: (points[i][0] - target[0]) ** 2 + (points[i][1] - target[1]) ** 2)
    else:
        raise _fail("invalid_argument", "break_at needs either at_nm or vertex for a polyline")
    if index <= 0 or index >= len(points) - 1:
        raise _fail("outside_entity", "break vertex must be strictly interior",
                    uuid=str(entity.get("uuid")), vertex=index, point_count=len(points))
    layer_name = str(entity.get("layer") or DEFAULT_LAYER)
    flag = bool(entity.get("construction", False))
    head = _append(sk, kind="polyline",
                   geom={"points": [[x, y] for x, y in points[:index + 1]]},
                   layer=layer_name, construction=flag)
    tail = _append(sk, kind="polyline",
                   geom={"points": [[x, y] for x, y in points[index:]]},
                   layer=layer_name, construction=flag)
    _drop(sk, entity)
    return [head, tail]


def break_at(sk_or_doc: Any, *, ref: Any, at_nm: Any = None, t: Any = None, vertex: Any = None,
             sketch_index: int = 0) -> list[str]:
    """**打断**：在一点/一参数处把实体破成两段，返回 `[前段 uuid, 后段 uuid]`（原实体被移除）。

    - `line`：给 `at_nm`（自动投影到直线，且垂足必须严格在线段内部）或 `t ∈ (0, 1)`（无单位参数）；
    - `polyline`（非闭合）：给 `vertex`（内部顶点下标）或 `at_nm`（取最近内部顶点）；
    - 落在端点/线段外 ⇒ `outside_entity`；请求点在 1 nm 分辨率下**不在**该线上 ⇒ `not_on_entity`
      （**不硬掰**：那是静默改几何）；`circle`/`arc` ⇒ `unsupported_kind`（本族边界，见模块 docstring）；
    - 新段沿用原实体的 `layer` 与 `construction`。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    target = _resolve(sk, ref)
    kind = _kind_of(target)
    if kind == "line":
        return _split_line_at(sk, target, at_nm=at_nm, t=t)
    if kind == "polyline":
        return _split_polyline_at(sk, target, at_nm=at_nm, vertex=vertex)
    raise _fail("unsupported_kind", "break_at supports line/polyline only", kind=kind,
                uuid=str(target.get("uuid")))


# --------------------------------------------------------------------------------------
# 6) 合并
# --------------------------------------------------------------------------------------


def _chain_segments(segments: list[list[tuple[int, int]]], tol: int) -> list[tuple[int, int]]:
    """把线段/折线首尾相接成一条点串（不连通 ⇒ `not_connected`；点序可正向也可反向）。"""
    def matches(left: tuple[int, int], right: tuple[int, int]) -> bool:
        return abs(left[0] - right[0]) <= tol and abs(left[1] - right[1]) <= tol

    remaining = [list(segment) for segment in segments]
    start_index = 0
    for index, segment in enumerate(remaining):
        if not any(matches(other[-1], segment[0])
                   for other_index, other in enumerate(remaining) if other_index != index):
            start_index = index
            break
    chain = list(remaining.pop(start_index))
    while remaining:
        picked = False
        for index, segment in enumerate(remaining):
            if matches(chain[-1], segment[0]):
                chain.extend(segment[1:])
            elif matches(chain[-1], segment[-1]):
                chain.extend(list(reversed(segment[:-1])))
            else:
                continue
            remaining.pop(index)
            picked = True
            break
        if not picked:
            raise _fail("not_connected", "entities do not form a single chain end to end",
                        remaining=len(remaining) + 1, tol_nm=tol)
    return chain


def _is_collinear(chain: list[tuple[int, int]]) -> bool:
    """点串是否共线（整数叉积严格为 0）。"""
    dx = chain[-1][0] - chain[0][0]
    dy = chain[-1][1] - chain[0][1]
    if dx == 0 and dy == 0:
        return False
    return all(dx * (point[1] - chain[0][1]) - dy * (point[0] - chain[0][0]) == 0
               for point in chain)


def merge(sk_or_doc: Any, *, refs: Sequence[Any], tol_nm: int = 0, layer: Any = None,
          sketch_index: int = 0) -> list[str]:
    """**合并**：把首尾相接的 `line`/`polyline` 合成一条（原实体被移除），返回 `[新实体 uuid]`。

    - 全部由直线构成且**严格共线** ⇒ 合成一条 `line`（两端点）；
    - 其余连通链 ⇒ 合成一条 `polyline`（链首尾重合 ⇒ `closed=True` 且去掉重复末点）；
    - `refs` < 2 ⇒ `too_few_refs`；接不上 ⇒ `not_connected`；`circle`/`arc`/`ellipse`/`point`
      ⇒ `unsupported_kind`（合并"未必共线"的曲线要有弧段口径，不在本模块草率实现）；
    - `layer` 缺省 `None` = 沿用**第一个**实体；`tol_nm` 是端点匹配容差（缺省 0 = 严格相等）。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise _fail("invalid_argument", "refs must be a sequence of entity references",
                    value=repr(refs))
    if len(refs) < 2:
        raise _fail("too_few_refs", "merge needs at least 2 entities", count=len(refs))
    tolerance = _as_nm(tol_nm, "tol_nm")
    if tolerance < 0:
        raise _fail("invalid_length", "tol_nm must be >= 0", value=tolerance)
    targets = [_resolve(sk, ref) for ref in refs]
    if len({str(entity.get("uuid")) for entity in targets}) != len(targets):
        raise _fail("duplicate_reference", "merge refs contain duplicates")
    segments: list[list[tuple[int, int]]] = []
    all_lines = True
    for entity in targets:
        kind = _kind_of(entity)
        if kind == "line":
            geom = _geom_of(entity)
            segments.append([(int(geom["x1"]), int(geom["y1"])), (int(geom["x2"]), int(geom["y2"]))])
        elif kind == "polyline":
            geom = _geom_of(entity)
            raw = geom.get("points")
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 2:
                raise _fail("geometry_invalid", "polyline needs at least 2 points",
                            uuid=str(entity.get("uuid")))
            if bool(geom.get("closed", False)):
                raise _fail("unsupported_kind", "closed polyline cannot be merged by this family",
                            uuid=str(entity.get("uuid")))
            segments.append([(int(item[0]), int(item[1])) for item in raw])
            all_lines = False
        else:
            raise _fail("unsupported_kind", "merge supports line/polyline only", kind=kind,
                        uuid=str(entity.get("uuid")))
    chain = _chain_segments(segments, tolerance)
    layer_name = _as_layer(layer if layer is not None
                           else str(targets[0].get("layer") or DEFAULT_LAYER))
    flag = any(bool(entity.get("construction", False)) for entity in targets)
    if all_lines and _is_collinear(chain):
        geom: dict[str, Any] = {"x1": chain[0][0], "y1": chain[0][1],
                                "x2": chain[-1][0], "y2": chain[-1][1]}
        kind = "line"
    else:
        geom = {"points": [[x, y] for x, y in chain]}
        kind = "polyline"
        if chain[0] == chain[-1] and len(chain) > 3:
            geom["points"] = geom["points"][:-1]
            geom["closed"] = True
    new_uuid = _append(sk, kind=kind, geom=geom, layer=layer_name, construction=flag)
    for entity in targets:
        _drop(sk, entity)
    return [new_uuid]


# --------------------------------------------------------------------------------------
# 7) 对齐
# --------------------------------------------------------------------------------------


def align(sk_or_doc: Any, *, refs: Sequence[Any], axis: str = "x", key: str = "center",
          to: str = "min", value_nm: Any = None, sketch_index: int = 0) -> list[str]:
    """**对齐**：把一批实体沿 `axis`（`"x"`/`"y"`）平移，使各自的锚点落到同一目标坐标。

    - `key`：锚点语义 —— `start`/`end`（首/末关键点）、`mid`（首末中点）、`center`（包络盒中心）、
      `min`/`max`（包络盒极值边）。折线的关键点 = 顶点表首末（不是包围盒）；
    - `to`：目标取自本批 —— `min`/`max`/`center`（各组锚点的最小/最大/中点）、`first`（第一个实体
      的锚点）；给了 `value_nm` 则直接用该坐标（`to` 被忽略）；
    - 只在 `axis` 方向平移（另一轴 dx/dy 为 0）；返回**被移动的实体 uuid**（锚点已相等者不动、
      也不返回）；`refs` 为空 ⇒ `empty_selection`；
    - 落库走 `edit/modify.move`（L1 四种 kind）与本地平移（`point`/`ellipse`）。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise _fail("invalid_argument", "refs must be a sequence of entity references",
                    value=repr(refs))
    if not refs:
        raise _fail("empty_selection", "align needs at least one entity")
    axis_name = str(axis).strip().lower()
    if axis_name not in _AXES:
        raise _fail("invalid_argument", "axis must be 'x' or 'y'", axis=str(axis))
    key_name = str(key).strip().lower()
    if key_name not in _ANCHOR_KEYS:
        raise _fail("invalid_argument", "unsupported anchor key", key=key_name,
                    known=list(_ANCHOR_KEYS))
    targets = [_resolve(sk, ref) for ref in refs]
    anchors = [_anchor(entity, axis_name, key_name) for entity in targets]
    if value_nm is not None:
        target_value = _as_nm(value_nm, "value_nm")
    else:
        mode = str(to).strip().lower()
        if mode not in _ALIGN_TARGETS:
            raise _fail("invalid_argument", "unsupported align target", to=str(to),
                        known=list(_ALIGN_TARGETS))
        if mode == "min":
            target_value = min(anchors)
        elif mode == "max":
            target_value = max(anchors)
        elif mode == "center":
            target_value = (min(anchors) + max(anchors)) // 2
        else:
            target_value = anchors[0]
    moved: list[str] = []
    for entity, current in zip(targets, anchors):
        delta = target_value - current
        if delta == 0:
            continue
        dx_nm, dy_nm = (delta, 0) if axis_name == "x" else (0, delta)
        moved.append(_translate_one(sk, entity, dx_nm=dx_nm, dy_nm=dy_nm))
    return moved


# --------------------------------------------------------------------------------------
# 8) 拉伸
# --------------------------------------------------------------------------------------


def stretch(sk_or_doc: Any, *, refs: Sequence[Any], window_nm: Sequence[int],
            dx_nm: int = 0, dy_nm: int = 0, sketch_index: int = 0) -> list[str]:
    """**拉伸**（AutoCAD STRETCH 口径）：把落在窗口内的**关键点**按 `(dx_nm, dy_nm)` 搬走。

    - `window_nm = (x1, y1, x2, y2)`（自动规范化；零面积窗口 ⇒ `degenerate_window`）；
    - `line`：窗口内的端点被搬（只搬一个 = 真拉伸，两个都搬 = 整体平移）；
      `polyline`：窗口内的顶点被搬（其余顶点不动）；`point`：点在窗口内即搬；
    - **曲线**（`circle`/`arc`/`ellipse`）只做整体平移：窗口没框住整个实体 ⇒
      `partial_stretch_unsupported`（**不假装能拉伸圆**）；框住了则整体搬；
    - 一个实体都没命中 ⇒ `empty_selection`（**不返回空列表冒充成功**）；`dx_nm == dy_nm == 0`
      ⇒ `no_displacement`；窗口外实体不动、也不返回；
    - 返回**被改动**的实体 uuid。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise _fail("invalid_argument", "refs must be a sequence of entity references",
                    value=repr(refs))
    if not refs:
        raise _fail("empty_selection", "stretch needs at least one entity")
    window = _as_window(window_nm)
    dx = _as_nm(dx_nm, "dx_nm")
    dy = _as_nm(dy_nm, "dy_nm")
    if dx == 0 and dy == 0:
        raise _fail("no_displacement", "stretch needs a non-zero displacement")
    targets = [_resolve(sk, ref) for ref in refs]
    moved: list[str] = []
    for entity in targets:
        kind = _kind_of(entity)
        geom = _geom_of(entity)
        entity_uuid = str(entity.get("uuid"))
        if kind == "line":
            start = (int(geom["x1"]), int(geom["y1"]))
            end = (int(geom["x2"]), int(geom["y2"]))
            inside = [point for point in (start, end) if _is_inside(point, window)]
            if not inside:
                continue
            if len(inside) == 2:
                moved.append(_translate_one(sk, entity, dx_nm=dx, dy_nm=dy))
                continue
            point = inside[0]
            geom["x1"], geom["y1"] = (point[0] + dx, point[1] + dy) if point == start else start
            geom["x2"], geom["y2"] = (point[0] + dx, point[1] + dy) if point == end else end
            if (geom["x1"], geom["y1"]) == (geom["x2"], geom["y2"]):
                raise _fail("degenerate_geometry", "stretch collapsed the line to a point",
                            uuid=entity_uuid)
            moved.append(entity_uuid)
        elif kind == "polyline":
            raw = geom.get("points")
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 2:
                raise _fail("geometry_invalid", "polyline needs at least 2 points", uuid=entity_uuid)
            points = [(int(item[0]), int(item[1])) for item in raw]
            hit = False
            for index, vertex in enumerate(points):
                if _is_inside(vertex, window):
                    points[index] = (vertex[0] + dx, vertex[1] + dy)
                    hit = True
            if not hit:
                continue
            geom["points"] = [[x, y] for x, y in points]
            moved.append(entity_uuid)
        elif kind == "point":
            if _is_inside((int(geom["x"]), int(geom["y"])), window):
                moved.append(_translate_one(sk, entity, dx_nm=dx, dy_nm=dy))
        elif kind in ("circle", "arc", "ellipse"):
            points = _entity_points(entity)
            if all(_is_inside(point, window) for point in points):
                moved.append(_translate_one(sk, entity, dx_nm=dx, dy_nm=dy))
            else:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                xmin_w, ymin_w, xmax_w, ymax_w = window
                overlaps = (min(xs) <= xmax_w and xmin_w <= max(xs)
                            and min(ys) <= ymax_w and ymin_w <= max(ys))
                if overlaps:
                    raise _fail("partial_stretch_unsupported",
                                "curved entities can only be stretched as a whole: window must "
                                "contain the entity bounding box",
                                kind=kind, uuid=entity_uuid, window_nm=list(window))
        else:
            raise _fail("unsupported_kind", "entity kind cannot be stretched by this family",
                        kind=kind, uuid=entity_uuid)
    if not moved:
        raise _fail("empty_selection", "no key point of the selection falls inside window_nm",
                    window_nm=list(window), ref_count=len(targets))
    return moved


# --------------------------------------------------------------------------------------
# 9) 规格冻结名（docs/research/cad-parity/spec/draw_extra.json）
# --------------------------------------------------------------------------------------
#
# 证据裁决（门的 `api` 首段 vs 本模块 OPERATIONS 键）：
#
# * 规格把 7 条操作的**公开名**定成 `add_*`，关键字也与前面裸名操作不同
#   （`major_radius`/`minor_radius`、`point`/`direction`、`position`、`radius`/`width`、
#   `boundary_refs`）⇒ 本段按规格名给出真实现（不是改门绕开：名字与关键字是规格冻结面）；
# * 规格的 `add_construction_line`/`add_ray` 要的是**无限几何**（双向/单方向无端点）。
#   L1 的 `line` 带 `x2`/`y2` 端点，用有限段冒充就是静默改语义 ⇒ 本段新增 `xline`/`ray`
#   两个 kind：`geom` 只有锚点 + 方向向量，**没有端点字段**（诚实表达"无限"）；
# * `add_donut`/`add_region` 是本族此前**真缺**的能力 ⇒ 真实现：圆环落内外半径；
#   面域用**整数鞋带公式现算** nm² 面积（不返回桩值）。
#
# 口径不变：长度整数纳米、失败抛 `CadError(reason=...)`、不返回空列表冒充成功。

SPEC_XLINE_KIND = "xline"
SPEC_RAY_KIND = "ray"
SPEC_DONUT_KIND = "donut"
SPEC_REGION_KIND = "region"

ERR_OUT_OF_RANGE = "err.out_of_range"
ERR_RADIUS_ORDER = "draw_extra.err_radius_order"
ERR_SPLINE_TOO_FEW = "draw_extra.err_spline_too_few_points"
ERR_DIRECTION_DEGENERATE = "draw_extra.err_ray_direction_degenerate"
ERR_REGION_NOT_CLOSED = "draw_extra.err_region_not_closed"

#: 规格引入/规格化的实体 kind（供 `module_facts()` 现读，不另立真源）。
NEW_KINDS = NEW_KINDS + (SPEC_DONUT_KIND, SPEC_REGION_KIND, SPEC_XLINE_KIND, SPEC_RAY_KIND)


def _spec_nm(value: Any, what: str, *, positive: bool = False) -> int:
    """规格键口径的整数纳米校验：非整数/非有限/非正 ⇒ `err.out_of_range`。"""
    try:
        return _as_nm(value, what, positive=positive)
    except CadError as exc:
        raise _fail(ERR_OUT_OF_RANGE, f"{what} must be an integer nanometre value",
                    what=what, value=repr(value)) from exc


def _spec_point(value: Any, what: str) -> tuple[int, int]:
    """规格键口径的点校验：非整数纳米点 ⇒ `err.out_of_range`。"""
    try:
        return _as_point(value, what)
    except CadError as exc:
        raise _fail(ERR_OUT_OF_RANGE, f"{what} must be a (x_nm, y_nm) integer pair",
                    what=what, value=repr(value)) from exc


def _spec_direction(value: Any, what: str = "direction") -> tuple[float, float]:
    """方向向量（无量纲，非纳米长度）：零向量/非有限/非二元 ⇒ 射线共用键。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail(ERR_DIRECTION_DEGENERATE, f"{what} must be a (dx, dy) pair",
                    detail=repr(value))
    parts: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise _fail(ERR_DIRECTION_DEGENERATE, f"{what} components must be finite numbers",
                        detail=repr(value))
        parts.append(float(item))
    if parts[0] == 0.0 and parts[1] == 0.0:
        raise _fail(ERR_DIRECTION_DEGENERATE, f"{what} must be a non-zero vector",
                    detail=repr(value))
    return (parts[0], parts[1])


def _infinite_entity(sk_or_doc: Any, *, kind: str, anchor: tuple[int, int],
                     direction: tuple[float, float], layer: str, construction: Any,
                     sketch_index: int = 0) -> list[str]:
    """无限几何落库：只有锚点 + 方向，**无端点字段**（`xline` 双向 / `ray` 单方向）。"""
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    geom: dict[str, Any] = {"px": anchor[0], "py": anchor[1],
                            "dx": direction[0], "dy": direction[1]}
    return [_append(sk, kind=kind, geom=geom, layer=_as_layer(layer),
                    construction=_as_flag(construction, "construction"))]


def add_ellipse(sk_or_doc: Any, *, center: Any, major_radius: Any, minor_radius: Any,
                rotation_deg: float = 0.0, layer: str = DEFAULT_LAYER,
                construction: bool = False, sketch_index: int = 0) -> list[str]:
    """规格冻结名：椭圆（`major_radius > minor_radius`；相等/倒置 ⇒ `err_radius_order`）。"""
    major = _spec_nm(major_radius, "major_radius", positive=True)
    minor = _spec_nm(minor_radius, "minor_radius", positive=True)
    if major <= minor:
        raise _fail(ERR_RADIUS_ORDER, "major_radius must be > minor_radius",
                    major=major, minor=minor)
    return ellipse(sk_or_doc, center_nm=_spec_point(center, "center"), rx_nm=major, ry_nm=minor,
                   rotation_deg=rotation_deg, layer=layer, construction=construction,
                   sketch_index=sketch_index)


def add_spline(sk_or_doc: Any, *, points_nm: Sequence[tuple[int, int]], degree: int = SPLINE_DEGREE,
               closed: bool = False, samples_per_span: int = 8, layer: str = DEFAULT_LAYER,
               construction: bool = False, sketch_index: int = 0) -> list[str]:
    """规格冻结名：样条实体（`kind="spline"`，`geom["points"]` **逐点等于入参**控制点）。

    `geom["samples"]` 另存三次 Catmull-Rom 采样（**诚实标注为近似**，不是 NURBS 精确 B 样条），
    两者都落库：控制点给下游精确复用，采样点给离散化消费。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    control = _as_points(points_nm, "points_nm")
    if len(control) < 2:
        raise _fail(ERR_SPLINE_TOO_FEW, "spline needs at least 2 control points",
                    required=2, actual=len(control))
    if isinstance(degree, bool) or not isinstance(degree, int) or degree != SPLINE_DEGREE:
        raise _fail(ERR_OUT_OF_RANGE, "degree must be 3 (cubic Catmull-Rom)",
                    value=repr(degree), min=SPLINE_DEGREE, max=SPLINE_DEGREE)
    samples = _as_int(samples_per_span, "samples_per_span", minimum=1)
    is_closed = _as_flag(closed, "closed")
    floats = [(float(point[0]), float(point[1])) for point in control]
    sampled = _catmull_rom(floats, samples, is_closed)
    geom: dict[str, Any] = {
        "points": [[point[0], point[1]] for point in control],
        "samples": [[_round_nm(x), _round_nm(y)] for x, y in sampled],
        "closed": is_closed,
        "scheme": "cubic_catmull_rom_approximation",
    }
    return [_append(sk, kind="spline", geom=geom, layer=_as_layer(layer),
                    construction=_as_flag(construction, "construction"))]


def add_point(sk_or_doc: Any, *, position: Any, layer: str = DEFAULT_LAYER,
              construction: bool = False, sketch_index: int = 0) -> list[str]:
    """规格冻结名：点实体（坐标非整数纳米 ⇒ `err.out_of_range`）。"""
    return point(sk_or_doc, point_nm=_spec_point(position, "position"), layer=layer,
                 construction=construction, sketch_index=sketch_index)


def add_construction_line(sk_or_doc: Any, *, point: Any, direction: Any,
                          layer: str = DEFAULT_LAYER, construction: bool = True,
                          sketch_index: int = 0) -> list[str]:
    """规格冻结名：构造线 —— 过 `point` 沿 `direction` **双向无限**（`kind="xline"`）。"""
    return _infinite_entity(sk_or_doc, kind=SPEC_XLINE_KIND, anchor=_spec_point(point, "point"),
                            direction=_spec_direction(direction, "direction"), layer=layer,
                            construction=construction, sketch_index=sketch_index)


def add_ray(sk_or_doc: Any, *, start: Any, direction: Any, layer: str = DEFAULT_LAYER,
            construction: bool = True, sketch_index: int = 0) -> list[str]:
    """规格冻结名：射线 —— 以 `start` 为端点沿 `direction` **单方向无限**（`kind="ray"`）。"""
    return _infinite_entity(sk_or_doc, kind=SPEC_RAY_KIND, anchor=_spec_point(start, "start"),
                            direction=_spec_direction(direction, "direction"), layer=layer,
                            construction=construction, sketch_index=sketch_index)


def add_donut(sk_or_doc: Any, *, center: Any, radius: Any, width: Any,
              layer: str = DEFAULT_LAYER, construction: bool = False,
              sketch_index: int = 0) -> list[str]:
    """规格冻结名：圆环 —— `radius` 中心线半径、`width` 环宽（整数纳米）。

    `width >= 2 × radius` 或 `width < 0` ⇒ `err.out_of_range`（**不静默改成实心圆**）；
    落库 `kind="donut"`，`inner_radius`/`outer_radius` 现算（`outer - inner == width` 精确）。
    """
    anchor = _spec_point(center, "center")
    r = _spec_nm(radius, "radius", positive=True)
    w = _spec_nm(width, "width")
    if w < 0 or w >= 2 * r:
        raise _fail(ERR_OUT_OF_RANGE, "width must satisfy 0 <= width < 2 * radius",
                    value=w, min=0, max=2 * r - 1)
    half = w // 2
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    geom: dict[str, Any] = {"cx": anchor[0], "cy": anchor[1], "radius": r, "width": w,
                            "inner_radius": r - half, "outer_radius": r + (w - half)}
    return [_append(sk, kind=SPEC_DONUT_KIND, geom=geom, layer=_as_layer(layer),
                    construction=_as_flag(construction, "construction"))]


def _region_ring(entity: Any) -> list[tuple[int, int]]:
    """面域边界实体 → 点串（闭合折线补回首点；其它 kind 显式抛错）。"""
    kind = _kind_of(entity)
    geom = _geom_of(entity)
    if kind == "line":
        return [(int(geom["x1"]), int(geom["y1"])), (int(geom["x2"]), int(geom["y2"]))]
    if kind == "polyline":
        raw = geom.get("points")
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 2:
            raise _fail("geometry_invalid", "polyline needs at least 2 points")
        points = [(int(item[0]), int(item[1])) for item in raw]
        if bool(geom.get("closed", False)):
            points = points + [points[0]]
        return points
    raise _fail("unsupported_kind", "region boundary must be line/polyline", kind=kind,
                uuid=str(entity.get("uuid")))


def _loose_ends(segments: list[list[tuple[int, int]]]) -> int:
    """开口端计数（端点出现奇数次的坐标个数；闭合环为 0，单条开口折线为 2）。"""
    counts: dict[tuple[int, int], int] = {}
    for segment in segments:
        for endpoint in (segment[0], segment[-1]):
            counts[endpoint] = counts.get(endpoint, 0) + 1
    return sum(1 for value in counts.values() if value % 2 == 1)


def add_region(sk_or_doc: Any, *, boundary_refs: Sequence[Any], layer: Any = None,
               construction: bool = False, sketch_index: int = 0) -> list[str]:
    """规格冻结名：面域 —— 由 `boundary_refs` 的既有边界对象造闭合环，面积现算。

    边界链接不上/不闭合 ⇒ `CadError(reason="draw_extra.err_region_not_closed", count=<开口端数>)`
    （`count` 真数出来，未采才传 -1）；面积 = 整数鞋带公式 `|Σ x_i·y_{i+1} − x_{i+1}·y_i| // 2`
    （nm²，精确整数，不返回桩值），落 `kind="region"`。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    if isinstance(boundary_refs, (str, bytes)) or not isinstance(boundary_refs, Sequence) \
            or not boundary_refs:
        raise _fail(ERR_REGION_NOT_CLOSED, "boundary_refs must be a non-empty sequence",
                    count=0)
    targets = [_resolve(sk, ref) for ref in boundary_refs]
    if len({str(entity.get("uuid")) for entity in targets}) != len(targets):
        raise _fail("duplicate_reference", "boundary_refs contain duplicates")
    segments = [_region_ring(entity) for entity in targets]
    try:
        chain = _chain_segments(segments, 0)
    except CadError as exc:
        raise _fail(ERR_REGION_NOT_CLOSED, "boundary_refs do not form a single chain",
                    count=_loose_ends(segments)) from exc
    if chain[0] != chain[-1] or len(chain) < 4:
        raise _fail(ERR_REGION_NOT_CLOSED, "boundary chain is not closed",
                    count=_loose_ends(segments))
    ring = chain[:-1]
    twice_area = 0
    for index, vertex in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        twice_area += vertex[0] * following[1] - following[0] * vertex[1]
    geom: dict[str, Any] = {"boundary": [[x, y] for x, y in ring],
                            "area_nm2": abs(twice_area) // 2, "closed": True}
    layer_name = _as_layer(layer if layer is not None
                           else str(targets[0].get("layer") or DEFAULT_LAYER))
    return [_append(sk, kind=SPEC_REGION_KIND, geom=geom, layer=layer_name,
                    construction=_as_flag(construction, "construction"))]


# --------------------------------------------------------------------------------------
# 族接口
# --------------------------------------------------------------------------------------


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "ellipse": ellipse,
    "spline": spline,
    "construction_line": construction_line,
    "ray": ray,
    "point": point,
    "break_at": break_at,
    "merge": merge,
    "align": align,
    "stretch": stretch,
    # 规格冻结名（docs/research/cad-parity/spec/draw_extra.json 的 api 首段）
    "add_ellipse": add_ellipse,
    "add_spline": add_spline,
    "add_construction_line": add_construction_line,
    "add_ray": add_ray,
    "add_point": add_point,
    "add_donut": add_donut,
    "add_region": add_region,
}


def module_facts() -> dict[str, Any]:
    """族模块的机读判定面（`ops` / 三态口径 / 新 kind / 复用面；不产用户文案）。"""
    return {
        "family": FAMILY,
        "op_count": len(OPERATIONS),
        "operations": sorted(OPERATIONS),
        "three_state": {"zero": ZERO, "unmeasured": UNMEASURED, "not_applicable": NA},
        "new_entity_kinds": list(NEW_KINDS),
        "reused_modules": ["cadcore.errors", "cadcore.edit.modify", "cadcore.sketch.constraints",
                           "cadcore.docmodel"],
        "length_unit": "integer_nanometre",
        "spline_scheme": "cubic_catmull_rom_interpolating_approximation",
        "ray_model": "finite_segment_with_construction_flag",
    }


# --------------------------------------------------------------------------------------
# 点实体 × 对象捕捉（与 `cadcore.precision.osnap` 的衔接面）
# --------------------------------------------------------------------------------------

#: 点实体的捕捉类型名。**不写进 `osnap.SNAP_KINDS`**：那是 precision 族的冻结清单，
#: 本模块不越权改它（改了就成偷偷扩别人的判据）。
POINT_SNAP_KIND = "point"

#: `kinds=` 里点实体的别名（`node` 是 CAD 惯用名）。
POINT_SNAP_ALIASES: tuple[str, ...] = (POINT_SNAP_KIND, "node")

#: 本席补的"加值能力"清单（**不在 `OPERATIONS` 里**：它们不是 `op(sk_or_doc, **kw) -> list[str]`
#: 形态，塞进去就破了族接口的冻结口径）。
EXTRA_CAPABILITIES: tuple[str, ...] = ("snap_point",)

#: 本段新增的公共名并入 `__all__`（本模块公共面的追加声明）。
__all__ = [*__all__, *EXTRA_CAPABILITIES, "POINT_SNAP_KIND", "POINT_SNAP_ALIASES",
           "EXTRA_CAPABILITIES"]


def _round_half_away_nm(value: float) -> int:
    """浮点纳米 → 整数纳米：四舍五入、远离零（与 `osnap` 的距离取整口径一致）。"""
    return int(math.floor(value + 0.5)) if value >= 0.0 else int(math.ceil(value - 0.5))


def snap_point(sk_or_doc: Any, *, point_nm: Any, tolerance_nm: Any,
               kinds: Sequence[str] | None = None, sketch_index: int = 0) -> Any:
    """**point-aware 捕捉助手**：`precision.osnap.snap` 的命中面 ∪ 本模块 `point` 实体。

    为什么需要它（根因 + 证据，本席实读）
    ------------------------------------
    `cadcore/precision/osnap.py` 的 `_parse()` 按实体 `kind` 分派，**只认** `line` / `circle` /
    `arc` / `polyline`，其它 kind 一律 `return None` 被跳过 ⇒ `add_point` 落的 `kind="point"`
    实体**永远进不了捕捉候选**。这**不是** `add_point` 落库格式的错：它落的
    `{uuid, kind, layer, construction, geom={x, y}}` 正是 L1 冻结字段集
    （`constraints.sketch_entities` 的真源字段），坐标也是整数纳米。根因修法是在 `osnap._parse`
    里加一支 `point`；**`osnap.py` 不在本席写面**（本席只准写 `ops/draw_extra.py` 与本族单测），
    因此按冻结口径把 point-aware 捕捉能力落在本模块：**`osnap.snap` 本体一行未改**，
    它的语义与冻结优先级不变。
    **2026-09-30 更新**：osnap 席已按同一根因修 `_parse()`（`SNAP_KINDS` 第 2 位插入 `point`），
    裸口径现在也能命中点实体 ⇒ 本助手不再是唯一通路；`selftest()` 第 5 段的对照口径
    已同步改为"裸口径与助手逐字段一致"（更新理由见该段注释，**不是放宽判据**）。

    口径
    ----
    * `point_nm` 必须是 `(x_nm, y_nm)` 整数纳米对（非整数/非有限 ⇒ `CadError(reason="err.out_of_range")`）；
    * `tolerance_nm` 有限且 `>= 0`（负数/非数 ⇒ `CadError(reason="err.out_of_range")`）；
      命中判据 `distance <= tolerance`，与 `osnap.snap` 的"严格 `<=`"一致；
    * `kinds=None` ⇒ 默认全集 = `"point"` + `osnap.SNAP_KINDS`；要**不**命中点实体就给不含
      `"point"`/`"node"` 的清单；未知类型名 ⇒ `CadError(reason="err.out_of_range")`（不静默忽略）；
    * 命中顺序：`"point"` 最优先（"就是这一个点"的精确锚点），其后**严格沿用**
      `osnap.SNAP_KINDS` 的冻结顺序；类内取最近、同距按 `ref` 文本定序（与 `osnap.snap` 同口径）；
    * 返回**复用** `osnap.SnapResult`：点实体的 `ref = (uuid, "point")`；全类落空 ⇒ `None`
      （不是返回原点的假命中）；
    * 坏几何（`geom` 缺 `x`/`y`、非整数坐标）按"跳过"处理 —— 与 `osnap` 对坏几何不抛的口径一致；
    * **已知边界（写清不藏）**：`kinds=("endpoint",)` 这类只含 osnap 类型的查询**不会**命中点实体
      （点不是端点）；要命中点实体必须显式给 `"point"`/`"node"` 或不传 `kinds`。
      本助手只覆盖"点实体进捕捉面"这一条；`osnap.snap` 本体**已按根因修复**（见上文
      2026-09-30 更新），故此处不再声称"本体仍看不见 `point`"。
    """
    from ..precision import osnap as _osnap

    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    query = _spec_point(point_nm, "point_nm")
    if (isinstance(tolerance_nm, bool) or not isinstance(tolerance_nm, (int, float))
            or not math.isfinite(float(tolerance_nm)) or float(tolerance_nm) < 0.0):
        raise _fail(ERR_OUT_OF_RANGE, "tolerance_nm must be a finite non-negative number",
                    what="tolerance_nm", value=repr(tolerance_nm))
    tolerance = float(tolerance_nm)
    if kinds is None:
        requested: list[str] = [POINT_SNAP_KIND, *_osnap.SNAP_KINDS]
    else:
        if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence):
            raise _fail(ERR_OUT_OF_RANGE, "kinds must be a sequence of snap kind names",
                        what="kinds", value=repr(kinds))
        requested = []
        for raw in kinds:
            name = POINT_SNAP_KIND if raw in POINT_SNAP_ALIASES else str(raw)
            if name != POINT_SNAP_KIND and name not in _osnap.SNAP_KINDS:
                raise _fail(ERR_OUT_OF_RANGE, "unknown snap kind", what="kinds", value=repr(raw))
            if name not in requested:
                requested.append(name)

    order = ([POINT_SNAP_KIND] if POINT_SNAP_KIND in requested else []) + [
        kind for kind in _osnap.SNAP_KINDS if kind in requested]
    for kind in order:
        if kind != POINT_SNAP_KIND:
            hit = _osnap.snap(sk, query, kinds=(kind,), tolerance_nm=tolerance)
            if hit is not None:
                return hit
            continue
        best_key: tuple[float, str] | None = None
        best: Any = None
        for entity in _entities(sk):
            if not isinstance(entity, Mapping) or str(entity.get("kind") or "") != POINT_SNAP_KIND:
                continue
            geom = entity.get("geom")
            if not isinstance(geom, Mapping):
                continue
            x, y = geom.get("x"), geom.get("y")
            if (isinstance(x, bool) or isinstance(y, bool)
                    or not isinstance(x, int) or not isinstance(y, int)):
                continue  # 坏几何：跳过，不抛（与 osnap 口径一致）
            distance = math.hypot(float(x - query[0]), float(y - query[1]))
            if distance > tolerance:
                continue
            uuid_text = str(entity.get("uuid") or "")
            key = (distance, uuid_text)
            if best_key is None or key < best_key:
                best_key = key
                best = _osnap.SnapResult(kind=POINT_SNAP_KIND, point_nm=(int(x), int(y)),
                                         ref=(uuid_text, POINT_SNAP_KIND),
                                         distance_nm=_round_half_away_nm(distance))
        if best is not None:
            return best
    return None


# --------------------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------------------


def _sample_sketch() -> dict[str, Any]:
    """自检夹具：一张空草图（L1 字段齐备，uuid 合法；**不依赖任何夹具文件**）。"""
    return {"uuid": _constraints.new_uuid(), "plane": "XY", "entities": [], "constraints": [],
            "dimensions": []}


def selftest() -> int:
    """自检：5 项（族接口 / 椭圆与点 / 样条与构造线射线 / 打断与合并 / 对齐与拉伸），自带阴性对照。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((str(name), bool(passed), str(detail)))

    facts = module_facts()
    required = {"ellipse", "spline", "construction_line", "ray", "point", "break_at", "merge",
                "align", "stretch"}
    check("ops_registered", required <= set(OPERATIONS) and facts["op_count"] == len(OPERATIONS),
          "ops=%d missing=%s" % (facts["op_count"], sorted(required - set(OPERATIONS))))
    check("three_state", (facts["three_state"]["zero"] == 0
                          and facts["three_state"]["unmeasured"] == -1
                          and facts["three_state"]["not_applicable"] == "NA"),
          "three_state=%s" % facts["three_state"])

    # 1) 椭圆（原生 + 折线近似）与点：几何值 + L1 不变量
    sk = _sample_sketch()
    native = ellipse(sk, center_nm=(10 * NM_PER_MM, 20 * NM_PER_MM),
                     rx_nm=30 * NM_PER_MM, ry_nm=10 * NM_PER_MM, rotation_deg=30.0)
    approx = ellipse(sk, center_nm=(0, 0), rx_nm=5 * NM_PER_MM, ry_nm=5 * NM_PER_MM, segments=16)
    dot = point(sk, point_nm=(1 * NM_PER_MM, -2 * NM_PER_MM))
    entities = _entities(sk)
    native_geom = entities[0]["geom"]
    approx_geom = entities[1]["geom"]
    dot_geom = entities[2]["geom"]
    doc = _docmodel.new_document()
    doc.sketches = [sk]
    invariants = _docmodel.check_invariants(doc)
    check("ellipse_and_point",
          native_geom == {"cx": 10 * NM_PER_MM, "cy": 20 * NM_PER_MM, "rx": 30 * NM_PER_MM,
                          "ry": 10 * NM_PER_MM, "rotation_deg": 30.0}
          and entities[0]["kind"] == "ellipse" and len(native) == 1
          and entities[1]["kind"] == "polyline" and len(approx_geom["points"]) == 16
          and approx_geom["closed"] is True
          and dot_geom == {"x": 1 * NM_PER_MM, "y": -2 * NM_PER_MM} and len(dot) == 1
          and invariants == [],
          "invariants=%s" % invariants[:2])

    # 2) 样条近似 + 构造线 + 射线（含"过控制点""射线端点"两条硬断言）
    sk2 = _sample_sketch()
    control = [(0, 0), (10 * NM_PER_MM, 5 * NM_PER_MM), (20 * NM_PER_MM, -5 * NM_PER_MM),
               (30 * NM_PER_MM, 0)]
    samples_per_span = 8
    spline_uuid = spline(sk2, points_nm=control, samples_per_span=samples_per_span)
    spline_points = _entities(sk2)[0]["geom"]["points"]
    line_uuid = construction_line(sk2, start_nm=(0, 0), end_nm=(1 * NM_PER_MM, 0))
    ray_uuid = ray(sk2, start_nm=(0, 0), through_nm=(0, 5 * NM_PER_MM), length_nm=4 * NM_PER_MM)
    ray_geom = _entities(sk2)[2]["geom"]
    degree_guard = False
    try:
        spline(sk2, points_nm=control, degree=2)
    except CadError as exc:
        degree_guard = exc.reason == "unsupported_degree"
    check("spline_and_construction",
          len(spline_uuid) == 1 and spline_points[0] == [0, 0]
          and spline_points[-1] == [30 * NM_PER_MM, 0]
          # 每个控制点都落在 index*span 的采样点上（t=0 过点），不是只看第 1 个采样点
          and all(tuple(spline_points[index * samples_per_span]) == control[index]
                  for index in range(len(control)))
          and _entities(sk2)[1]["construction"] is True and len(line_uuid) == 1
          and _entities(sk2)[2]["construction"] is True
          and (ray_geom["x2"], ray_geom["y2"]) == (0, 4 * NM_PER_MM) and len(ray_uuid) == 1
          and degree_guard,
          "spline_pts=%d ends=%s,%s ray_end=(%d,%d)" % (len(spline_points), spline_points[0],
                                                        spline_points[-1], ray_geom["x2"],
                                                        ray_geom["y2"]))

    # 3) 打断 + 合并（含"点不在线上"阴性对照）
    sk3 = _sample_sketch()
    base = construction_line(sk3, start_nm=(0, 0), end_nm=(100 * NM_PER_MM, 0))[0]
    parts = break_at(sk3, ref=base, at_nm=(40 * NM_PER_MM, 7 * NM_PER_MM))  # 垂足投影到 (40,0)
    geometries = [_geom_of(entity) for entity in _entities(sk3)]
    lengths = sorted(abs(geom["x2"] - geom["x1"]) for geom in geometries)
    merged = merge(sk3, refs=parts)
    merged_geom = _geom_of(_entities(sk3)[0])
    off_line_guard = False
    sk_guard = _sample_sketch()
    base_guard = construction_line(sk_guard, start_nm=(0, 0), end_nm=(2, 1))[0]
    try:
        # foot (0.8, 0.4) nm is not representable at 1 nm resolution -> not_on_entity
        break_at(sk_guard, ref=base_guard, at_nm=(1, 0))
    except CadError as exc:
        off_line_guard = exc.reason == "not_on_entity"
    check("break_and_merge",
          len(parts) == 2 and lengths == [40 * NM_PER_MM, 60 * NM_PER_MM]
          and len(merged) == 1 and len(_entities(sk3)) == 1
          and (merged_geom["x1"], merged_geom["y1"], merged_geom["x2"], merged_geom["y2"])
          == (0, 0, 100 * NM_PER_MM, 0)
          and off_line_guard,
          "parts_lengths=%s merged=%s" % (lengths, merged_geom))

    # 4) 对齐 + 拉伸（含"曲线部分框选"阴性对照）
    sk4 = _sample_sketch()
    low = construction_line(sk4, start_nm=(0, 0), end_nm=(10 * NM_PER_MM, 0))[0]
    high = construction_line(sk4, start_nm=(0, 5 * NM_PER_MM), end_nm=(10 * NM_PER_MM, 5 * NM_PER_MM))[0]
    aligned = align(sk4, refs=[low, high], axis="y", key="mid", to="min")
    anchors = {_anchor(_resolve(sk4, ref), "y", "mid") for ref in (low, high)}
    sk5 = _sample_sketch()
    stretch_ref = construction_line(sk5, start_nm=(0, 0), end_nm=(10 * NM_PER_MM, 0))[0]
    moved = stretch(sk5, refs=[stretch_ref], window_nm=(-1, -1, 1 * NM_PER_MM, 1 * NM_PER_MM),
                    dx_nm=2 * NM_PER_MM, dy_nm=3 * NM_PER_MM)
    stretched = _geom_of(_entities(sk5)[0])
    circle_guard = False
    sk6 = _sample_sketch()
    circle_uuid = ellipse(sk6, center_nm=(50 * NM_PER_MM, 50 * NM_PER_MM), rx_nm=10 * NM_PER_MM,
                          ry_nm=10 * NM_PER_MM)[0]
    try:
        stretch(sk6, refs=[circle_uuid], window_nm=(45 * NM_PER_MM, 45 * NM_PER_MM,
                                                    55 * NM_PER_MM, 55 * NM_PER_MM),
                dx_nm=NM_PER_MM)
    except CadError as exc:
        circle_guard = exc.reason == "partial_stretch_unsupported"
    check("align_and_stretch",
          len(aligned) == 1 and anchors == {0}
          and (stretched["x1"], stretched["y1"], stretched["x2"], stretched["y2"])
          == (2 * NM_PER_MM, 3 * NM_PER_MM, 10 * NM_PER_MM, 0) and len(moved) == 1
          and circle_guard,
          "anchors=%s stretched=%s" % (sorted(anchors), stretched))

    passed = all(item[1] for item in checks)

    # 5) 本席补的加值能力：点实体 × 捕捉（单独出舱，不混进上面 6 项的计数与断言）。
    #    **对照口径已于 2026-09-30 按根因修复更新 —— 这是换尺子，不是放宽判据。**
    #    旧负对照 = "同一个点实体在裸 `osnap.snap` 下必须落空（`raw is None`）"，它测的是
    #    当时的缺陷（`osnap._parse` 只认 line/circle/arc/polyline）；osnap 席已按同一根因在
    #    `_parse()` 补了 `point` 支（`SNAP_KINDS[1]`）⇒ 裸口径现在**看得见** point，旧断言变成
    #    "要求缺陷复现"，只能靠**回退 osnap 修复**来换绿（本席不做，那是拿回退换假绿）。
    #    新口径 = **一致性正对照**：裸 `osnap.snap` 与助手 `snap_point` 必须看到同一个点实体
    #    （同 kind=point / 同 point_nm / 同 ref / 同 distance），助手自身命中判据一条未减。
    #    强度对比：旧口径只钉"raw 失明"这一条事实，新口径钉"两条通路逐字段一致" ⇒
    #    raw 再失明、或两条通路分叉（含助手退化），本检查都仍然红，故不是放宽。
    extra_ok = False
    extra_detail = "probe_did_not_run"
    try:
        from ..precision import osnap as _osnap
        sk_extra = _sample_sketch()
        dot_uuid = point(sk_extra, point_nm=(3 * NM_PER_MM, 4 * NM_PER_MM))[0]
        hit = snap_point(sk_extra, point_nm=(3 * NM_PER_MM, 4 * NM_PER_MM), tolerance_nm=1)
        raw = _osnap.snap(sk_extra, (3 * NM_PER_MM, 4 * NM_PER_MM), tolerance_nm=1)
        extra_ok = bool(
            hit is not None and hit.kind == POINT_SNAP_KIND
            and hit.point_nm == (3 * NM_PER_MM, 4 * NM_PER_MM)
            and hit.distance_nm == 0
            and hit.ref == (dot_uuid, POINT_SNAP_KIND)
            # 根因修复后的新判据：裸口径必须也命中 point（不能再要求 `raw is None`）
            and raw is not None and raw.kind == POINT_SNAP_KIND
            and raw.point_nm == (3 * NM_PER_MM, 4 * NM_PER_MM)
            and raw.distance_nm == 0
            and raw.ref == (dot_uuid, POINT_SNAP_KIND)
            # 两条通路逐字段一致（助手与裸口径不许分叉）
            and (raw.kind, raw.point_nm, raw.ref, raw.distance_nm)
            == (hit.kind, hit.point_nm, hit.ref, hit.distance_nm))
        extra_detail = "hit=%s raw_osnap=%s agree=%s" % (
            None if hit is None else hit.kind,
            None if raw is None else raw.kind,
            extra_ok)
    except CadError as exc:
        extra_detail = "CadError:%s" % exc.reason
    print("check %-24s %s %s" % ("point_snap_extra", "PASS" if extra_ok else "FAIL", extra_detail))

    for name, ok, detail in checks:
        print("check %-24s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    print("DRAW_EXTRA_OPS verdict=%s ops=%d checks=%d/%d"
          % ("PASS" if (passed and extra_ok) else "FAIL", len(OPERATIONS),
             sum(1 for item in checks if item[1]), len(checks)))
    print("EXTRA_OPS verdict=%s ops=%d capabilities=%s detail=%s"
          % ("PASS" if extra_ok else "FAIL", len(EXTRA_CAPABILITIES),
             ",".join(EXTRA_CAPABILITIES), extra_detail))
    return 0 if (passed and extra_ok) else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
