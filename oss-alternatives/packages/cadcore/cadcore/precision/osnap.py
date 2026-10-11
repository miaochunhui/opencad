"""cadcore.precision.osnap —— E3 精度辅助：对象捕捉 / 正交锁定 / 极轴追踪 / 步进栅格。

一句话用途
----------
把"光标附近的点"吸附到草图里已有的几何特征（端点 / 中点 / 圆心 / 象限点 / 交点 / 垂足 / 最近点），
外加三条制图辅助（正交锁定 / 极轴追踪 / 栅格步进）。**纯几何、无状态**：不写回草图、不改任何实体、
无副作用、无 IO；同一输入必然得到同一输出（无随机、无时间依赖）。

口径（冻死，下游照抄，本文件不新增 L1 字段）
--------------------------------------------
- 坐标一律**整数纳米**；`point_nm` 形如 `(x_nm, y_nm)`，入参与返回值都是 `int`。
- `distance_nm` 也是整数纳米（由实算距离按"四舍五入、远离零"取整）。
- 实体 kind ∈ `line` / `arc` / `circle` / `polyline` / `point`；geom 字段：
  `line{x1,y1,x2,y2}`、`circle{cx,cy,r}`、`arc{cx,cy,r,start_deg,end_deg}`、`polyline{points:[[x,y],...]}`、
  `point{x,y}`（L1 里**唯一**的点型实体，落库见 `cadcore.ops.draw_extra.point`）。
  **只认这 5 类**：L1 里没有 `node` / `insert` / `blockref` 这类实体 kind（本席实读 `cadcore/` 全量
  `kind=` 写入点），所以不发明"点类捕捉"去匹配不存在的实体；未知 kind 照旧跳过（`_parse` 返回 `None`）。
- 圆弧口径与 `cadcore.sketch` 一致：**逆时针**从 `start_deg` 扫到 `end_deg`，
  扫角 `sweep = (end_deg - start_deg) mod 360`。
- **草图按结构接受**（映射或对象），与 A5/A9/A10 的解耦口径一致：本文件不 import 任何**业务**兄弟模块
  （几何 / 求解 / 文档层），只依赖 `cadcore.units` 的纯数值叶子（`finite_number`），
  因此兄弟席的改动不会让这里连测都跑不了。

命中优先级
----------
`endpoint > point > midpoint > center > quadrant > intersection > perpendicular > nearest`（见 `SNAP_KINDS`）。
新增的 `point` 插在 `endpoint` **之后**（`SNAP_KINDS[1]`），口径三条写清不藏：
①既有 7 类之间的**相对顺序与各自候选口径一字未改** —— 原先由 `endpoint` 命中的输入结论不变；
②原先由 `midpoint` 及更低类命中的输入，若容差内同时有 `point` 实体，改判 `point`（"点实体进捕捉面"
的必然结果，不是回归）；③`point` 实体只有一个特征（自身位置），因此**只**参与 `point` 这一类，
**不**冒充 `endpoint`/`nearest`（点不是端点，也不是线段的最近点）。
`kinds=` 只做**过滤**，不改变优先级顺序：先看优先级最高的那类里有没有落在 `tolerance_nm` 内的候选，
有就取该类里最近的；该类一个都没命中才降级看下一类。全部落空 ⇒ 返回 `None`（**不是**返回原点的假命中）。

`ref` 的形态
------------
命中出处一律是元组，便于下游精确定位（禁下标引用）：
- 单实体特征：`(entity_uuid, sub)`；`sub` 取值 `start` / `end` / `midpoint` / `midpoint:<i>` /
  `vertex:<i>` / `center` / `quadrant:<deg>` / `perpendicular[:<i>]` / `nearest` / `point`；
- 交点（跨实体）：`(uuid_a, "intersection", uuid_b, "intersection")`；
- 非实体来源（栅格）：`(None, "grid")`。

已知边界（显式声明，不静默）
----------------------------
- `intersection` **只做 line×line 与 line×circle**：line×arc、circle×circle、polyline 参与的求交
  一律**不返回候选**（返回 `None`）；无解（平行 / 相离 / 交点在段外）也返回 `None`。
  本函数**绝不抛异常**（文档里的坏几何按"跳过"处理）。
- `perpendicular` 对面是**整条直线**（line 的延长线也认，与 AutoCAD 的垂足捕捉一致）；
  对圆/弧是"圆上与给定点连线沿半径方向"的点（近远两个候选，取容差内更近者）。
- **入参**错（非整数纳米、容差为负、未知 kind 名）**必须炸**（`ValueError`）：
  这是调用方的 bug，静默兜底会把它藏起来。
- **整数纳米入参必须可表示成 `float`**：`float(10**309)` 抛内建 `OverflowError`，
  因此在入参把关层统一转成 `ValueError`（`_as_finite` / `_as_float_pair` / `_angle_deg`），
  调用方拿到的永远是"你参数不对"，不是裸内建异常名（B6 缺陷 RC5）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..units import finite_number

__all__ = [
    "SNAP_KINDS",
    "SnapResult",
    "grid_snap",
    "ortho_lock",
    "polar_track",
    "snap",
]

#: 捕捉类型全集，**顺序即优先级**（高 → 低）。改顺序 = 改语义，别顺手改。
#: `point` 是新增项、固定在第 2 位：既有 7 类的相对顺序一字未改（见模块 docstring 的"命中优先级"）。
SNAP_KINDS: tuple[str, ...] = (
    "endpoint",
    "point",
    "midpoint",
    "center",
    "quadrant",
    "intersection",
    "perpendicular",
    "nearest",
)

#: 象限点的固定角度集（0/90/180/270）。
QUADRANT_DEG: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0)

SUB_START = "start"
SUB_END = "end"
SUB_MIDPOINT = "midpoint"
SUB_VERTEX = "vertex"
SUB_CENTER = "center"
SUB_QUADRANT = "quadrant"
SUB_PERPENDICULAR = "perpendicular"
SUB_NEAREST = "nearest"
SUB_INTERSECTION = "intersection"
SUB_GRID = "grid"
SUB_POINT = "point"

_PARAM_EPS = 1e-9
_ANG_EPS = 1e-9


# --------------------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapResult:
    """一次捕捉命中：`kind` 类型、`point_nm` 吸附后的整数纳米点、`ref` 出处、`distance_nm` 原始距离。"""

    kind: str
    point_nm: tuple[int, int]
    ref: tuple[Any, ...]
    distance_nm: int

    def as_dict(self) -> dict[str, Any]:
        """→ 可 JSON 落盘的普通字典（`ref` 转 list，便于写进 L1 文档或测试夹具）。"""
        return {
            "kind": self.kind,
            "point_nm": [int(self.point_nm[0]), int(self.point_nm[1])],
            "ref": list(self.ref),
            "distance_nm": int(self.distance_nm),
        }


# --------------------------------------------------------------------------------------
# 入参把关（坏输入必须炸）
# --------------------------------------------------------------------------------------


def _as_int_nm(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be an int nanometer value, got {value!r}")
    return int(value)


def _as_point_nm(point: Any, what: str = "point_nm") -> tuple[int, int]:
    if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
        raise ValueError(f"{what} must be a (x_nm, y_nm) pair, got {point!r}")
    return (_as_int_nm(point[0], f"{what}.x"), _as_int_nm(point[1], f"{what}.y"))


def _as_float_pair(point: tuple[int, int], what: str = "point_nm") -> tuple[float, float]:
    """已校验的整数纳米点 → 浮点对；**不可表示**（如 309 位整数）⇒ `ValueError`。

    单列这一步的原因（B6 缺陷 RC5）：`float(10**309)` 抛的是内建 `OverflowError`，而这条路径上
    它是"入参不可表示"、不是"服务失败" —— 必须在**入参把关层**转成 `ValueError`（与 `_as_finite` 同口径），
    否则调用方拿到裸内建异常名。`snap` / `ortho_lock` / `polar_track` 的浮点通路统一走这里。
    """
    out: list[float] = []
    for index, value in enumerate(point):
        number = finite_number(value)
        if number is None:
            raise ValueError(f"{what}[{index}] is not representable as a float, got {value!r}")
        out.append(number)
    return (out[0], out[1])


def _as_finite(value: Any, what: str) -> float:
    number = finite_number(value)
    if number is None:
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    return number


def _as_tolerance_nm(value: Any) -> float:
    tol = _as_finite(value, "tolerance_nm")
    if tol < 0.0:
        raise ValueError(f"tolerance_nm must be >= 0, got {value!r}")
    return tol


def _as_tolerance_deg(value: Any) -> float:
    tol = _as_finite(value, "tolerance_deg")
    if tol < 0.0:
        raise ValueError(f"tolerance_deg must be >= 0, got {value!r}")
    return tol


def _as_kinds(kinds: Any) -> tuple[str, ...]:
    if isinstance(kinds, str):
        kinds = (kinds,)
    if isinstance(kinds, (bytes, bytearray)) or not isinstance(kinds, Iterable):
        raise ValueError(f"kinds must be an iterable of snap kind names, got {kinds!r}")
    out: list[str] = []
    for name in kinds:
        if name not in SNAP_KINDS:
            raise ValueError(f"unknown snap kind {name!r}; known kinds: {list(SNAP_KINDS)}")
        if name not in out:
            out.append(name)
    return tuple(out)


def _round_half_away(value: float) -> int:
    """浮点值 → 整数纳米：四舍五入、远离零（对称，无 +0/-0 抖动）。"""
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


# --------------------------------------------------------------------------------------
# 结构访问（映射或对象；坏几何 ⇒ None，由调用方跳过）
# --------------------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _entities(sk: Any) -> list[Any]:
    items = _get(sk, "entities", None)
    if items is None or isinstance(items, (str, bytes)):
        return []
    try:
        return list(items)
    except TypeError:
        return []


def _parse(entity: Any) -> tuple[str, tuple[Any, ...]] | None:
    """实体 → `(kind, 数值元组)`；形态不对/非有限/退化 ⇒ `None`（跳过，不抛异常）。"""
    kind = _get(entity, "kind", None)
    if not isinstance(kind, str):
        return None
    geom = _get(entity, "geom", None)
    if geom is None:
        return None
    fields = {
        "line": ("x1", "y1", "x2", "y2"),
        "circle": ("cx", "cy", "r"),
        "arc": ("cx", "cy", "r", "start_deg", "end_deg"),
        # 点实体（L1 唯一的点型实体，geom={x,y}）：此前不在这张表里 ⇒ 被 `return None` 跳过，
        # 落库的点永远进不了捕捉候选。加一支即可，`kind in ("circle","arc")` 的半径守卫不会误伤它。
        "point": ("x", "y"),
    }.get(kind)
    if fields is not None:
        values: list[float] = []
        for name in fields:
            number = _num(_get(geom, name, None))
            if number is None:
                return None
            values.append(number)
        if kind in ("circle", "arc") and values[2] <= 0.0:
            return None  # 半径非正：不是几何，跳过
        if kind == "line" and values[0] == values[2] and values[1] == values[3]:
            return None  # 零长线：方向未定义，跳过
        return (kind, tuple(values))
    if kind == "polyline":
        raw = _get(geom, "points", None)
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
            return None
        points: list[tuple[float, float]] = []
        for item in raw:
            if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)) or len(item) != 2:
                return None
            x = _num(item[0])
            y = _num(item[1])
            if x is None or y is None:
                return None
            points.append((x, y))
        if len(points) < 2:
            return None
        return ("polyline", tuple(points))
    return None


def _uuid_of(entity: Any) -> str:
    value = _get(entity, "uuid", "")
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------------------
# 基础几何
# --------------------------------------------------------------------------------------


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _point_on_circle(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return (cx + r * math.cos(rad), cy + r * math.sin(rad))


def _sweep_deg(start_deg: float, end_deg: float) -> float:
    sweep = (end_deg - start_deg) % 360.0
    return 360.0 if sweep <= _ANG_EPS else sweep  # 退化弧按整圆处理（文档不该这样，但不许炸）


def _angle_on_arc(deg: float, start_deg: float, end_deg: float) -> bool:
    offset = (deg - start_deg) % 360.0
    return offset <= _sweep_deg(start_deg, end_deg) + 1e-9


def _angle_deg(base: tuple[int, int], point: tuple[int, int]) -> float | None:
    """`base → point` 的方位角（度，`[0, 360)`）；两点重合 ⇒ `None`（方向未定义）。"""
    dx = point[0] - base[0]
    dy = point[1] - base[1]
    if dx == 0 and dy == 0:
        return None
    fx, fy = finite_number(dx), finite_number(dy)  # 差是不可表示的巨整数 ⇒ ValueError（不裸 OverflowError）
    if fx is None or fy is None:
        raise ValueError(f"point offset from base is not representable as a float: dx={dx!r}, dy={dy!r}")
    return math.degrees(math.atan2(fy, fx)) % 360.0


def _ang_diff(a_deg: float, b_deg: float) -> float:
    """两方位角的最小夹角（度，`[0, 180]`）。"""
    d = abs((a_deg - b_deg) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def _clamped_foot(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> tuple[float, float]:
    """点 `P` 到**线段** `AB` 的最近点（参数夹在 `[0,1]`）。"""
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return (ax, ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (ax + t * dx, ay + t * dy)


def _infinite_foot(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> tuple[float, float]:
    """点 `P` 到**直线** `AB` 的垂足（延长线也认）。"""
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 0.0:
        return (ax, ay)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    return (ax + t * dx, ay + t * dy)


# --------------------------------------------------------------------------------------
# 候选点生成（每个 kind × 每种实体）
# --------------------------------------------------------------------------------------


def _entity_candidates(
    parsed: tuple[str, tuple[Any, ...]], uuid: str, kind: str, px: float, py: float
) -> list[tuple[float, float, tuple[Any, ...]]]:
    """某实体在某捕捉类型下的候选 `(x, y, ref)` 列表；不适用 ⇒ 空列表（不是错误）。"""
    ekind, values = parsed
    out: list[tuple[float, float, tuple[Any, ...]]] = []

    if ekind == "line":
        x1, y1, x2, y2 = values
        if kind == "endpoint":
            out.append((x1, y1, (uuid, SUB_START)))
            out.append((x2, y2, (uuid, SUB_END)))
        elif kind == "midpoint":
            out.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0, (uuid, SUB_MIDPOINT)))
        elif kind == "nearest":
            fx, fy = _clamped_foot(px, py, x1, y1, x2, y2)
            out.append((fx, fy, (uuid, SUB_NEAREST)))
        elif kind == "perpendicular":
            fx, fy = _infinite_foot(px, py, x1, y1, x2, y2)
            out.append((fx, fy, (uuid, SUB_PERPENDICULAR)))

    elif ekind == "circle":
        cx, cy, r = values
        if kind == "center":
            out.append((cx, cy, (uuid, SUB_CENTER)))
        elif kind == "quadrant":
            for deg in QUADRANT_DEG:
                qx, qy = _point_on_circle(cx, cy, r, deg)
                out.append((qx, qy, (uuid, f"{SUB_QUADRANT}:{int(deg)}")))
        elif kind == "nearest":
            radial = _radial_feet(px, py, cx, cy, r)
            if radial is not None:
                out.append((radial[0][0], radial[0][1], (uuid, SUB_NEAREST)))
        elif kind == "perpendicular":
            radial = _radial_feet(px, py, cx, cy, r)
            if radial is not None:
                out.append((radial[0][0], radial[0][1], (uuid, SUB_PERPENDICULAR)))
                out.append((radial[1][0], radial[1][1], (uuid, SUB_PERPENDICULAR)))

    elif ekind == "arc":
        cx, cy, r, start_deg, end_deg = values
        sweep = _sweep_deg(start_deg, end_deg)
        if kind == "endpoint":
            sx, sy = _point_on_circle(cx, cy, r, start_deg)
            ex, ey = _point_on_circle(cx, cy, r, end_deg)
            out.append((sx, sy, (uuid, SUB_START)))
            out.append((ex, ey, (uuid, SUB_END)))
        elif kind == "midpoint":
            mx, my = _point_on_circle(cx, cy, r, start_deg + sweep / 2.0)
            out.append((mx, my, (uuid, SUB_MIDPOINT)))
        elif kind == "center":
            out.append((cx, cy, (uuid, SUB_CENTER)))
        elif kind == "quadrant":
            for deg in QUADRANT_DEG:
                if _angle_on_arc(deg, start_deg, end_deg):
                    qx, qy = _point_on_circle(cx, cy, r, deg)
                    out.append((qx, qy, (uuid, f"{SUB_QUADRANT}:{int(deg)}")))
        elif kind == "nearest":
            radial = _radial_feet(px, py, cx, cy, r)
            picked = None
            if radial is not None:
                for cand in radial:
                    cdeg = math.degrees(math.atan2(cand[1] - cy, cand[0] - cx)) % 360.0
                    if _angle_on_arc(cdeg, start_deg, end_deg):
                        picked = cand
                        break
            if picked is None:
                # 垂足落在弧外 ⇒ 弧上最近点就是较近的那个端点。
                sx, sy = _point_on_circle(cx, cy, r, start_deg)
                ex, ey = _point_on_circle(cx, cy, r, end_deg)
                picked = (sx, sy) if _dist(px, py, sx, sy) <= _dist(px, py, ex, ey) else (ex, ey)
            out.append((picked[0], picked[1], (uuid, SUB_NEAREST)))
        elif kind == "perpendicular":
            radial = _radial_feet(px, py, cx, cy, r)
            if radial is not None:
                for cand in radial:
                    cdeg = math.degrees(math.atan2(cand[1] - cy, cand[0] - cx)) % 360.0
                    if _angle_on_arc(cdeg, start_deg, end_deg):
                        out.append((cand[0], cand[1], (uuid, SUB_PERPENDICULAR)))

    elif ekind == "point":
        x, y = values
        if kind == "point":
            # 点实体的唯一特征就是自身位置：只出 `point` 类候选，不冒充 endpoint/nearest。
            out.append((x, y, (uuid, SUB_POINT)))

    elif ekind == "polyline":
        points: tuple[tuple[float, float], ...] = values  # type: ignore[assignment]
        if kind == "endpoint":
            for index, (vx, vy) in enumerate(points):
                if index == 0:
                    sub = SUB_START
                elif index == len(points) - 1:
                    sub = SUB_END
                else:
                    sub = f"{SUB_VERTEX}:{index}"
                out.append((vx, vy, (uuid, sub)))
        elif kind == "midpoint":
            for index in range(len(points) - 1):
                ax, ay = points[index]
                bx, by = points[index + 1]
                out.append(((ax + bx) / 2.0, (ay + by) / 2.0, (uuid, f"{SUB_MIDPOINT}:{index}")))
        elif kind == "nearest":
            best: tuple[float, float] | None = None
            best_d = math.inf
            for index in range(len(points) - 1):
                ax, ay = points[index]
                bx, by = points[index + 1]
                fx, fy = _clamped_foot(px, py, ax, ay, bx, by)
                d = _dist(px, py, fx, fy)
                if d < best_d:
                    best, best_d = (fx, fy), d
            if best is not None:
                out.append((best[0], best[1], (uuid, SUB_NEAREST)))
        elif kind == "perpendicular":
            for index in range(len(points) - 1):
                ax, ay = points[index]
                bx, by = points[index + 1]
                fx, fy = _infinite_foot(px, py, ax, ay, bx, by)
                out.append((fx, fy, (uuid, f"{SUB_PERPENDICULAR}:{index}")))

    return out


def _radial_feet(
    px: float, py: float, cx: float, cy: float, r: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """圆上沿半径方向的两个点（近端、远端）；给定点与圆心重合 ⇒ `None`（方向未定义）。"""
    dx = px - cx
    dy = py - cy
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return None
    ux = dx / length
    uy = dy / length
    return ((cx + r * ux, cy + r * uy), (cx - r * ux, cy - r * uy))


# --------------------------------------------------------------------------------------
# 交点（line×line 与 line×circle；无解返回空，绝不抛异常）
# --------------------------------------------------------------------------------------


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _line_line_intersection(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float] | None:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    rx, ry = x2 - x1, y2 - y1
    sx, sy = x4 - x3, y4 - y3
    den = _cross(rx, ry, sx, sy)
    scale = math.hypot(rx, ry) * math.hypot(sx, sy)
    if scale <= 0.0 or abs(den) <= 1e-12 * scale:
        return None  # 平行或共线：无数值稳定的唯一解
    qx, qy = x3 - x1, y3 - y1
    t = _cross(qx, qy, sx, sy) / den
    u = _cross(qx, qy, rx, ry) / den
    if not (-_PARAM_EPS <= t <= 1.0 + _PARAM_EPS) or not (-_PARAM_EPS <= u <= 1.0 + _PARAM_EPS):
        return None  # 交点在**线段**之外（延长线交点不算命中）
    return (x1 + t * rx, y1 + t * ry)


def _line_circle_intersections(
    line: tuple[float, ...], circle: tuple[float, ...]
) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = line
    cx, cy, r = circle
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - cx, y1 - cy
    qa = dx * dx + dy * dy
    if qa <= 0.0 or r <= 0.0:
        return []
    qb = 2.0 * (fx * dx + fy * dy)
    qc = fx * fx + fy * fy - r * r
    disc = qb * qb - 4.0 * qa * qc
    disc_tol = 1e-12 * (qb * qb + abs(4.0 * qa * qc) + 1.0)
    if disc < -disc_tol:
        return []
    if disc <= disc_tol:
        roots = [-qb / (2.0 * qa)]
    else:
        root = math.sqrt(disc)
        roots = [(-qb - root) / (2.0 * qa), (-qb + root) / (2.0 * qa)]
    out: list[tuple[float, float]] = []
    for t in roots:
        if not (-_PARAM_EPS <= t <= 1.0 + _PARAM_EPS):
            continue
        px = x1 + t * dx
        py = y1 + t * dy
        if any(_dist(px, py, ox, oy) <= 1e-6 for ox, oy in out):
            continue  # 切点去重（两个根收敛到同一点）
        out.append((px, py))
    return out


def _intersections(entities: list[Any]) -> list[tuple[float, float, tuple[Any, ...]]]:
    """全对求交：仅 `line×line` 与 `line×circle`；任何异常都吞掉（交点捕捉不许把画图搞崩）。"""
    out: list[tuple[float, float, tuple[Any, ...]]] = []
    try:
        parsed = [(entity, _parse(entity), _uuid_of(entity)) for entity in entities]
        curves = [(e, p, u) for (e, p, u) in parsed if p is not None]
        for i in range(len(curves)):
            for j in range(i + 1, len(curves)):
                _, pa, ua = curves[i]
                _, pb, ub = curves[j]
                if pa is None or pb is None:
                    continue
                ref = (ua, SUB_INTERSECTION, ub, SUB_INTERSECTION)
                if pa[0] == "line" and pb[0] == "line":
                    hit = _line_line_intersection(pa[1], pb[1])
                    if hit is not None:
                        out.append((hit[0], hit[1], ref))
                elif pa[0] == "line" and pb[0] == "circle":
                    for hit in _line_circle_intersections(pa[1], pb[1]):
                        out.append((hit[0], hit[1], ref))
                elif pa[0] == "circle" and pb[0] == "line":
                    for hit in _line_circle_intersections(pb[1], pa[1]):
                        out.append((hit[0], hit[1], ref))
    except Exception:  # noqa: BLE001 —— 明确口径：交点捕捉永不上抛
        return []
    return out


# --------------------------------------------------------------------------------------
# 对外入口：对象捕捉
# --------------------------------------------------------------------------------------


def snap(
    sk: Any,
    point_nm: tuple[int, int],
    *,
    kinds: Sequence[str] = SNAP_KINDS,
    tolerance_nm: int | float,
) -> SnapResult | None:
    """把 `point_nm` 吸附到草图 `sk` 上的最近特征点；无命中返回 `None`。

    - `kinds`：允许命中的类型集合（默认全集）；顺序不影响优先级，优先级写死在 `SNAP_KINDS`；
    - `tolerance_nm`：命中半径（纳米），严格 `<=` 判命中；负数报 `ValueError`；
    - 返回 `SnapResult(kind, point_nm, ref, distance_nm)`，其中 `distance_nm` 是**原始点与命中点**的距离。
    """
    point = _as_point_nm(point_nm)
    tolerance = _as_tolerance_nm(tolerance_nm)
    allowed = _as_kinds(kinds)
    entities = _entities(sk)
    if not entities or not allowed:
        return None
    px, py = _as_float_pair(point)

    intersections: list[tuple[float, float, tuple[Any, ...]]] | None = None
    for kind in SNAP_KINDS:  # 顺序 = 优先级，先命中的类直接胜出
        if kind not in allowed:
            continue
        candidates: list[tuple[float, float, tuple[Any, ...]]] = []
        if kind == "intersection":
            if intersections is None:
                intersections = _intersections(entities)
            candidates = intersections
        else:
            for entity, parsed in ((e, _parse(e)) for e in entities):
                if parsed is None:
                    continue
                candidates.extend(_entity_candidates(parsed, _uuid_of(entity), kind, px, py))
        best: tuple[float, float, tuple[Any, ...]] | None = None
        best_key: tuple[Any, ...] | None = None
        for cx, cy, ref in candidates:
            dist = _dist(px, py, cx, cy)
            if dist > tolerance:
                continue
            key = (dist, tuple(str(part) for part in ref))
            if best_key is None or key < best_key:
                best, best_key = (cx, cy, ref), key
        if best is not None:
            hit_x, hit_y, ref = best
            return SnapResult(
                kind=kind,
                point_nm=(_round_half_away(hit_x), _round_half_away(hit_y)),
                ref=ref,
                distance_nm=_round_half_away(_dist(px, py, hit_x, hit_y)),
            )
    return None


# --------------------------------------------------------------------------------------
# 对外入口：正交锁定 / 极轴追踪 / 栅格步进
# --------------------------------------------------------------------------------------


def ortho_lock(
    base_nm: tuple[int, int], point_nm: tuple[int, int], *, tolerance_deg: float = 3.0
) -> tuple[int, int]:
    """把点锁到过 `base_nm` 的**水平/垂直轴**上；不在容差内 ⇒ 原样返回。

    判据：`base → point` 方位角与最近轴（0/180 水平、90/270 垂直）的夹角 `<= tolerance_deg`。
    只动一个坐标（水平锁 y、垂直锁 x），另一个坐标原样保留 —— 这是"锁轴不缩距"的口径。
    """
    base = _as_point_nm(base_nm, "base_nm")
    point = _as_point_nm(point_nm)
    tolerance = _as_tolerance_deg(tolerance_deg)
    angle = _angle_deg(base, point)
    if angle is None:
        return point  # 与基点重合：方向未定义，不做任何事
    horizontal = min(_ang_diff(angle, 0.0), _ang_diff(angle, 180.0))
    vertical = min(_ang_diff(angle, 90.0), _ang_diff(angle, 270.0))
    if horizontal <= tolerance and horizontal <= vertical:
        return (point[0], base[1])
    if vertical <= tolerance:
        return (base[0], point[1])
    return point


def polar_track(
    base_nm: tuple[int, int],
    point_nm: tuple[int, int],
    *,
    increments_deg: Sequence[float] = (15, 30, 45, 90),
    tolerance_deg: float = 3.0,
) -> tuple[int, int]:
    """把点吸附到过 `base_nm` 的**极轴增量**射线；不在容差内 ⇒ 原样返回。

    口径：候选方位角 = 各增量的整数倍（`[0, 360)` 去重升序）；命中后**保留原距离**（只改方向，不改半径）。
    `base` 与 `point` 重合 ⇒ 原样返回（方向未定义）。`increments_deg` 为空 ⇒ `ValueError`（调用方 bug）。
    """
    base = _as_point_nm(base_nm, "base_nm")
    point = _as_point_nm(point_nm)
    tolerance = _as_tolerance_deg(tolerance_deg)
    candidates = _polar_angles(increments_deg)
    angle = _angle_deg(base, point)
    if angle is None:
        return point
    base_f, point_f = _as_float_pair(base, "base_nm"), _as_float_pair(point)
    radius = _dist(base_f[0], base_f[1], point_f[0], point_f[1])
    best_deg = min(candidates, key=lambda deg: (_ang_diff(angle, deg), deg))
    if _ang_diff(angle, best_deg) > tolerance:
        return point
    rad = math.radians(best_deg)
    return (
        _round_half_away(base[0] + radius * math.cos(rad)),
        _round_half_away(base[1] + radius * math.sin(rad)),
    )


def _polar_angles(increments_deg: Sequence[float]) -> list[float]:
    if isinstance(increments_deg, (str, bytes)) or not isinstance(increments_deg, Iterable):
        raise ValueError(f"increments_deg must be an iterable of angles, got {increments_deg!r}")
    steps: list[float] = []
    for raw in increments_deg:
        step = _as_finite(raw, "increments_deg item")
        if step <= 0.0 or step > 360.0:
            raise ValueError(f"increments_deg items must be in (0, 360], got {raw!r}")
        if step not in steps:
            steps.append(step)
    if not steps:
        raise ValueError("increments_deg must contain at least one angle in (0, 360]")
    angles: set[float] = set()
    for step in steps:
        count = int(round(360.0 / step))
        for index in range(max(count, 1)):
            angles.add(round((index * step) % 360.0, 9))
    return sorted(angles)


def _snap_scalar(value: int, spacing: int) -> int:
    """整数纳米步进：`divmod` 判定（不经过浮点），半格向外（远离零）。"""
    sign = -1 if value < 0 else 1
    quotient, remainder = divmod(abs(value), spacing)
    if 2 * remainder >= spacing:
        quotient += 1
    return sign * quotient * spacing


def grid_snap(point_nm: tuple[int, int], *, spacing_nm: int) -> tuple[int, int]:
    """步进（栅格）捕捉：每个坐标吸到最近的 `spacing_nm` 整数倍；半格向外取整（整数运算，无浮点误差）。"""
    point = _as_point_nm(point_nm)
    spacing = _as_int_nm(spacing_nm, "spacing_nm")
    if spacing <= 0:
        raise ValueError(f"spacing_nm must be a positive int nanometer value, got {spacing_nm!r}")
    return (_snap_scalar(point[0], spacing), _snap_scalar(point[1], spacing))
