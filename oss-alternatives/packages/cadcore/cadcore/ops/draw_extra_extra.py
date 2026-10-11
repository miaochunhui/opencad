#!/usr/bin/env python3
"""DRAW-EXTRA-EXTRA 族模块（补缺席席 B）：补「样条近似（折线 + 弧拟合）/ 构造线 / 射线」的真几何。

族接口（与冻结口径**逐条一致**，本文件不 import 任何不存在的名字）：

    FAMILY = "draw_extra_extra"
    OPERATIONS: dict[str, Callable]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]      # 首个位置参 = sk_or_doc，其余 KEYWORD_ONLY
    长度一律整数纳米（int；bool 与非整值浮点一律拒绝）；错误抛 cadcore.errors.CadError(reason=...)

本文件新增的三条能力（都是**真几何**，不是占位）：

* `spline_approx` —— 三次 Catmull-Rom 插值曲线的**折线 + 圆弧拟合**近似：
  `mode="arc"` 把曲线切成 `line` / `arc` 实体；`mode="line"` 落**一条 `polyline`**。
  **公差尺子打在曲线本身（不是拟合点集）**：每条实体在自己的**参数区间**内按 `CURVE_PROBES` 个
  曲线子样本量偏差（拟合与验收同尺子，且样本比拟合点密）⇒ "每两点一段微线段、拟合点恰在端点上"
  这种假绿过不了门（参数区间中段会露馅）。超差抛 `tolerance_unreachable`，`details` 给实测偏差
  （**不静默给一条超差的近似线**）。弧拟合用**最小二乘圆**（Kåsa；短弧上三点外接圆半径病态，
  实测可差 49%，不用）；这是**离散子样本口径**，不冒充连续曲线的解析上界（`module_facts()` 写明）。
* `construction_line` —— **双向无限**构造线：`kind="xline"`，`geom` 只有锚点 + 方向，
  **没有端点字段**（有限段用 `line`，语义不同不混淆）。
* `ray` —— **单方向无限**射线：`kind="ray"`，`geom` 只有锚点 + 方向；沿反方向取点是**错误**
  （`reason="behind_ray_origin"`），不是"静默取到别处"。

几何落库口径（**必须过 `docmodel.check_invariants()`**，这是本文件与"裸 float 方向向量"方案的差别）：

* 方向用 `dir_deg`（float 度，逆时针、0 = +x）。L1 不变量只允许 `*_deg` 键是浮点，
  `px/py` 与 `arc` 的 `cx/cy/r` 一律整数纳米；
* 于是 `xline`/`ray` 的 `geom = {px, py, dir_deg}` 是**可入文档**的（新测试对 `check_invariants` 有断言）；
* 真几何的消费口径由两个纯函数给出：`point_on_xline(geom, t_nm)`（t 可正可负 = 双向）
  与 `point_on_ray(geom, t_nm)`（t < 0 抛错 = 单方向）。

输入口径：`sk_or_doc` 接受 ① `docmodel.Document`；② L1 文档映射（含 `sketches`）；
③ 裸草图映射（含 `entities`）。①② 用 `sketch_index`（缺省 0）选草图。

三态口径（AGENTS.md E2.3）：`0` = 真为零；`-1` = 未采；`"NA"` = 不适用。本模块不产用户可见文案，
运行期字符串常量一律 ASCII（i18n 门口径）。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/draw_extra_extra.py
    # 末行固定：EXTRA_OPS verdict=PASS ops=3
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Callable, Mapping, Sequence

import os as _os
import sys as _sys

_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:  # 直跑脚本与包内导入共用同一条绝对导入路径
    _sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError
from cadcore.sketch import constraints as _constraints

__all__ = [
    "FAMILY", "OPERATIONS", "NM_PER_MM", "VERIFY_FACTOR", "CURVE_PROBES",
    "DEFAULT_TOLERANCE_NM", "spline_approx", "construction_line", "ray",
    "point_on_xline", "point_on_ray", "module_facts", "selftest",
]

#: 族 id。
FAMILY = "draw_extra_extra"

NM_PER_MM = 1_000_000

#: 缺省图层（与包内其它族一致）。
DEFAULT_LAYER = "0"

#: 拟合点密度 = `samples_per_span × VERIFY_FACTOR`。
VERIFY_FACTOR = 4

#: 每条实体的公差尺子：其参数区间内取 `CURVE_PROBES + 1` 个**曲线**子样本（比拟合点密）。
CURVE_PROBES = 12

#: 圆弧的最小有意义扫角：更小的"弧"等价于直线（短弧上 r = 弦/扫角，扫角噪声直接放大成半径噪声，
#: 实测 2° 下限时拟合出的半径可差 63%）。10° 起步保证曲率可测、半径良态。
MIN_ARC_SWEEP_DEG = 10.0

#: 落库取整分辨率是 1 nm，验收给 2 nm 余量（**不是**放宽公差，是把取整误差与拟合误差分开）。
ROUND_SLACK_NM = 2.0

#: 缺省公差（0.1 mm）：mm 级曲线在缺省密度下够用；不够就抛错让调用方加密度，不静默放水。
DEFAULT_TOLERANCE_NM = 100_000

#: 本族新增/使用的实体 kind。
XLINE_KIND = "xline"
RAY_KIND = "ray"
ARC_KIND = "arc"
LINE_KIND = "line"
POLYLINE_KIND = "polyline"

#: `spline_approx` 的两种落库形态。
SPLINE_MODES = ("arc", "line")

#: L1 冻结字段集（与 `edit/modify.py` / `sketch/constraints.py` 逐字一致，不自造第六个字段）。
L1_FIELDS = ("uuid", "kind", "layer", "construction", "geom")

#: 三态口径（本模块自身不产测量值，只把它带进机读面）。
ZERO, UNMEASURED, NA = 0, -1, "NA"


# --------------------------------------------------------------------------------------
# 入参校验（唯一解释入口；长度一律整数纳米）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(str(message), reason=str(reason), **details)


def _as_nm(value: Any, what: str, *, positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点 / 非数一律报错，不静默取整）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_length", f"{what} must be an integer nanometre value",
                    what=what, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise _fail("invalid_length", f"{what} must be an integer nanometre value",
                        what=what, value=repr(value))
        value = int(value)
    if positive and value <= 0:
        raise _fail("invalid_length", f"{what} must be > 0", what=what, value=value)
    return int(value)


def _as_point(value: Any, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_point", f"{what} must be a (x_nm, y_nm) pair",
                    what=what, value=repr(value))
    return (_as_nm(value[0], f"{what}[0]"), _as_nm(value[1], f"{what}[1]"))


def _as_points(value: Any, what: str) -> list[tuple[int, int]]:
    """点串入参 → `[(x_nm, y_nm), ...]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise _fail("invalid_point", f"{what} must be a non-empty point sequence",
                    what=what, value=repr(value))
    return [_as_point(item, f"{what}[{index}]") for index, item in enumerate(value)]


def _as_int(value: Any, what: str, *, minimum: int) -> int:
    """整数入参（bool 不算数）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail("invalid_integer", f"{what} must be an integer >= {minimum}",
                    what=what, value=repr(value), minimum=minimum)
    return int(value)


def _as_flag(value: Any, what: str) -> bool:
    """布尔入参（严格 bool，不接受 0/1）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_flag", f"{what} must be a bool", what=what, value=repr(value))
    return value


def _as_layer(value: Any) -> str:
    """图层名（非空字符串）。"""
    if not isinstance(value, str) or not value:
        raise _fail("invalid_layer", "layer must be a non-empty string", value=repr(value))
    return value


def _as_deg(value: Any, what: str) -> float:
    """角度入参 → float（非有限值报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise _fail("invalid_angle", f"{what} must be a finite degree value",
                    what=what, value=repr(value))
    return float(value) % 360.0


def _direction_deg(dir_deg: Any, direction: Any) -> float:
    """方向入参 → `dir_deg`（度）。`dir_deg` 与 `direction` 二者恰给一个，零向量报错。"""
    if (dir_deg is None) == (direction is None):
        raise _fail("direction_ambiguous", "give exactly one of dir_deg / direction",
                    has_dir_deg=dir_deg is not None, has_direction=direction is not None)
    if dir_deg is not None:
        return _as_deg(dir_deg, "dir_deg")
    if (isinstance(direction, (str, bytes)) or not isinstance(direction, Sequence)
            or len(direction) != 2):
        raise _fail("direction_degenerate", "direction must be a (dx, dy) pair",
                    value=repr(direction))
    parts: list[float] = []
    for item in direction:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise _fail("direction_degenerate", "direction components must be finite numbers",
                        value=repr(direction))
        parts.append(float(item))
    if parts[0] == 0.0 and parts[1] == 0.0:
        raise _fail("direction_degenerate", "direction must be a non-zero vector",
                    value=repr(direction))
    return math.degrees(math.atan2(parts[1], parts[0])) % 360.0


def _sketch(sk_or_doc: Any, *, sketch_index: int) -> Any:
    """`sk_or_doc` → 草图对象（三种形态见模块 docstring；取不到抛 `no_sketch`）。"""
    if hasattr(sk_or_doc, "sketches"):
        sketches = list(getattr(sk_or_doc, "sketches") or [])
    elif isinstance(sk_or_doc, Mapping) and "sketches" in sk_or_doc:
        sketches = list(sk_or_doc.get("sketches") or [])
    elif isinstance(sk_or_doc, Mapping) and "entities" in sk_or_doc:
        sketches = [sk_or_doc]
    else:
        raise _fail("no_sketch", "sk_or_doc must be a Document, document mapping or sketch mapping",
                    value=type(sk_or_doc).__name__)
    index = _as_int(sketch_index, "sketch_index", minimum=0)
    if index >= len(sketches):
        raise _fail("no_sketch", "sketch_index out of range", index=index, count=len(sketches))
    return sketches[index]


def _append(sk: Any, *, kind: str, geom: dict[str, Any], layer: str, construction: bool) -> str:
    """按 L1 冻结字段追加实体并返回 uuid（字段集与包内其它模块逐字一致）。"""
    entity = {"uuid": _constraints.new_uuid(), "kind": str(kind), "layer": str(layer),
              "construction": bool(construction), "geom": dict(geom)}
    sk["entities"].append(entity)
    return str(entity["uuid"])


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米（就近取整，真源分辨率就是 1 nm）。"""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def _unit(dir_deg: float) -> tuple[float, float]:
    """方向角 → 单位向量。"""
    rad = math.radians(float(dir_deg))
    return (math.cos(rad), math.sin(rad))


# --------------------------------------------------------------------------------------
# 真几何的消费口径（纯函数，可现读复现）
# --------------------------------------------------------------------------------------


def point_on_xline(geom: Mapping[str, Any], distance_nm: Any) -> tuple[int, int]:
    """构造线上的点：沿方向走**带符号**距离（正负都合法 = 双向无限）。"""
    t = _as_nm(distance_nm, "distance_nm")
    ux, uy = _unit(float(geom["dir_deg"]))
    return (_round_nm(float(geom["px"]) + ux * t), _round_nm(float(geom["py"]) + uy * t))


def point_on_ray(geom: Mapping[str, Any], distance_nm: Any) -> tuple[int, int]:
    """射线上的点：`distance_nm >= 0`；反方向（< 0）抛 `behind_ray_origin`（不静默取到别处）。"""
    t = _as_nm(distance_nm, "distance_nm")
    if t < 0:
        raise _fail("behind_ray_origin", "ray has no geometry behind its origin", value=t)
    ux, uy = _unit(float(geom["dir_deg"]))
    return (_round_nm(float(geom["px"]) + ux * t), _round_nm(float(geom["py"]) + uy * t))


# --------------------------------------------------------------------------------------
# 样条采样与 line/arc 拟合（纯函数；不落库）
# --------------------------------------------------------------------------------------


def _cr_point(floats: Sequence[tuple[float, float]], u: float) -> tuple[float, float]:
    """全局参数 `u`（以"控制点跨度"为单位，∈ [0, n-1]）处的三次 Catmull-Rom 点。

    开放曲线两端用端点自身补齐切向；`n == 2` 退化为直线插值。
    """
    pts = [(float(point[0]), float(point[1])) for point in floats]
    count = len(pts)
    if count < 2:
        raise _fail("spline_too_few_points", "spline needs at least 2 control points",
                    required=2, actual=count)
    if count == 2:
        t = min(1.0, max(0.0, float(u)))
        return (pts[0][0] + (pts[1][0] - pts[0][0]) * t,
                pts[0][1] + (pts[1][1] - pts[0][1]) * t)
    u = min(float(count - 1), max(0.0, float(u)))
    seg = min(int(math.floor(u)), count - 2)
    t = u - float(seg)
    t2, t3 = t * t, t * t * t
    ext = [pts[0]] + pts + [pts[-1]]
    p0, p1, p2, p3 = ext[seg], ext[seg + 1], ext[seg + 2], ext[seg + 3]
    return (0.5 * ((2.0 * p1[0]) + (-p0[0] + p2[0]) * t
                   + (2.0 * p0[0] - 5.0 * p1[0] + 4.0 * p2[0] - p3[0]) * t2
                   + (-p0[0] + 3.0 * p1[0] - 3.0 * p2[0] + p3[0]) * t3),
            0.5 * ((2.0 * p1[1]) + (-p0[1] + p2[1]) * t
                   + (2.0 * p0[1] - 5.0 * p1[1] + 4.0 * p2[1] - p3[1]) * t2
                   + (-p0[1] + 3.0 * p1[1] - 3.0 * p2[1] + p3[1]) * t3))


def _catmull_rom(floats: Sequence[Sequence[float]], samples_per_span: int
                 ) -> list[tuple[float, float]]:
    """曲线的**均匀参数采样**（`samples_per_span` 点/跨度，末点补齐；下标 k ↔ 参数 `k/per_span`）。"""
    pts = [(float(point[0]), float(point[1])) for point in floats]
    count = len(pts)
    if count < 2:
        raise _fail("spline_too_few_points", "spline needs at least 2 control points",
                    required=2, actual=count)
    if count == 2:
        return [pts[0], pts[1]]
    out = [_cr_point(pts, index / float(samples_per_span))
           for index in range((count - 1) * samples_per_span)]
    out.append(pts[-1])
    return out


def _curve_interval(floats: Sequence[tuple[float, float]], first: int, last: int, per_span: int,
                    probes: int = CURVE_PROBES) -> list[tuple[float, float]]:
    """拟合下标区间 `[first, last]` 对应的**曲线**子样本（参数空间，比拟合点密 `probes` 倍）。"""
    u0 = float(first) / float(per_span)
    u1 = float(last) / float(per_span)
    return [_cr_point(floats, u0 + (u1 - u0) * step / float(probes))
            for step in range(probes + 1)]


def _solve3(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """3×3 线性方程组（列主元高斯消元）；奇异/非有限 → `None`。"""
    aug = [list(matrix[row]) + [rhs[row]] for row in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for row in range(col + 1, 3):
            factor = aug[row][col] / aug[col][col]
            for k in range(col, 4):
                aug[row][k] -= factor * aug[col][k]
    out = [0.0, 0.0, 0.0]
    for row in (2, 1, 0):
        total = aug[row][3] - sum(aug[row][k] * out[k] for k in range(row + 1, 3))
        out[row] = total / aug[row][row]
    return out if all(math.isfinite(value) for value in out) else None


def _circle_lsq(point_list: Sequence[tuple[float, float]]) -> tuple[float, float, float] | None:
    """最小二乘圆（Kåsa：`x²+y² + a·x + b·y + c = 0`；先平移到质心并归一化尺度）→ `(cx, cy, r)`。

    为什么不用"三点外接圆"：短弧上三点几乎共线，半径病态（首版实测差 49%）；最小二乘用整段点，
    半径才稳。法方程取 `-Sxz/-Syz/-Sz` 右端（符号错会让圆心翻到另一侧 ⇒ 全靠不上弧）。
    """
    count = float(len(point_list))
    if count < 3:
        return None
    mx = sum(point[0] for point in point_list) / count
    my = sum(point[1] for point in point_list) / count
    scale = max((math.hypot(point[0] - mx, point[1] - my) for point in point_list), default=0.0)
    if scale <= 0.0:
        return None
    pts = [((point[0] - mx) / scale, (point[1] - my) / scale) for point in point_list]
    sxx = sum(p[0] * p[0] for p in pts)
    syy = sum(p[1] * p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxz = sum(p[0] * (p[0] * p[0] + p[1] * p[1]) for p in pts)
    syz = sum(p[1] * (p[0] * p[0] + p[1] * p[1]) for p in pts)
    sz = sum(p[0] * p[0] + p[1] * p[1] for p in pts)
    solution = _solve3([[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, count]], [-sxz, -syz, -sz])
    if solution is None:
        return None
    ucx, ucy = -solution[0] / 2.0, -solution[1] / 2.0
    radius2 = ucx * ucx + ucy * ucy - solution[2]
    if not math.isfinite(radius2) or radius2 <= 0.0:
        return None
    cx = mx + ucx * scale
    cy = my + ucy * scale
    radius = math.sqrt(radius2) * scale
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(radius)) or radius <= 0.0:
        return None
    return (cx, cy, radius)


def _arc_run(floats: Sequence[tuple[float, float]], points: list[tuple[float, float]],
             first: int, last: int, tol: float, per_span: int
             ) -> tuple[float, float, float, float, float] | None:
    """`[first..last]` 能否用一段圆弧（扫角 ∈ [2°, 180°]、逆时针）在 `tol` 内拟合。

    两步走，各有理由：
    ① **LSQ 定形**（Kåsa 最小二乘圆）——短弧上三点外接圆半径病态，不能用；
    ② **端点锚定**——把圆心投到"段首末点弦的中垂线"上最近处，得到**过段首末点**的圆，
       于是每段实体的端点严格落在曲线端点上 ⇒ 相邻段首尾相接（链不裂），端点差只剩 1 nm 取整误差。
       尺子打在**曲线**子样本（参数区间内 `CURVE_PROBES` 个点）+ 拟合点上：两者都要过。
    """
    circle = _circle_lsq(points[first:last + 1])
    if circle is None:
        return None
    lsq_x, lsq_y = circle[0], circle[1]
    ax, ay = points[first]
    bx, by = points[last]
    ex, ey = bx - ax, by - ay
    chord = math.hypot(ex, ey)
    if chord <= 0.0:
        return None
    nx, ny = -ey / chord, ex / chord
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    offset = (lsq_x - mx) * nx + (lsq_y - my) * ny
    cx, cy = mx + offset * nx, my + offset * ny
    radius = math.hypot(ax - cx, ay - cy)
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(radius)) or radius <= tol:
        return None
    start = math.degrees(math.atan2(ay - cy, ax - cx)) % 360.0
    sweep = 0.0
    previous = start
    for point in points[first + 1:last + 1]:
        angle = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360.0
        delta = (angle - previous) % 360.0
        if delta > 180.0:
            return None
        sweep += delta
        if sweep > 180.0:
            return None
        previous = angle
    if sweep < MIN_ARC_SWEEP_DEG:
        return None
    for point in _curve_interval(floats, first, last, per_span) + points[first:last + 1]:
        if abs(math.hypot(point[0] - cx, point[1] - cy) - radius) > tol:
            return None
    return (cx, cy, radius, start, start + sweep)


def _line_ok(floats: Sequence[tuple[float, float]], points: list[tuple[float, float]],
             first: int, last: int, tol: float, per_span: int) -> bool:
    """`[first..last]` 是否落在首末连线的 `tol` 内（投影单调不折返；曲线子样本也要过）。"""
    if last <= first + 1:
        return True
    ax, ay = points[first]
    bx, by = points[last]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return False
    previous = -math.inf
    for point in points[first:last + 1]:
        along = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length
        if along < previous - 1e-9:
            return False
        previous = along
    for point in _curve_interval(floats, first, last, per_span):
        if abs((point[0] - ax) * dy - (point[1] - ay) * dx) / length > tol:
            return False
    return True


def _fit_runs(floats: Sequence[tuple[float, float]], points: list[tuple[float, float]], tol: float,
              mode: str, per_span: int) -> list[dict[str, Any]]:
    """对曲线贪心切段（`arc` 优先，装不下则退 `line`）；返回段描述表（含 `first/last` 拟合下标）。"""
    runs: list[dict[str, Any]] = []
    index, count = 0, len(points)
    while index < count - 1:
        best_arc: tuple[int, tuple[float, float, float, float, float]] | None = None
        if mode == "arc":
            cursor = index + 2
            while cursor <= count - 1:
                fit = _arc_run(floats, points, index, cursor, tol, per_span)
                if fit is not None:  # 不因一次失败就停：取**最长**合格窗口（半径才良态）
                    best_arc = (cursor, fit)
                cursor += 1
        best_line = index + 1
        cursor = index + 1
        while cursor <= count - 1 and _line_ok(floats, points, index, cursor, tol, per_span):
            best_line = cursor
            cursor += 1
        if best_arc is not None and best_arc[0] >= best_line:
            last, fit = best_arc
            runs.append({"kind": ARC_KIND, "first": index, "last": last, "arc": fit})
            index = last
        else:
            runs.append({"kind": LINE_KIND, "first": index, "last": best_line})
            index = best_line
    return runs


def _point_segment_distance(point: tuple[float, float], a: tuple[float, float],
                            b: tuple[float, float]) -> float:
    """点到线段距离。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2
    t = min(1.0, max(0.0, t))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def _arc_deviation(point: tuple[float, float], cx: float, cy: float, radius: float,
                   start_deg: float, end_deg: float) -> float:
    """点到圆弧（逆时针 start→end）的距离；角度不在扫角内则取端点距离。"""
    angle = math.degrees(math.atan2(point[1] - cy, point[0] - cx)) % 360.0
    if angle < start_deg:  # 扫角跨 360° 时把角度展开到 [start, end]
        angle += 360.0
    if start_deg <= angle <= end_deg:
        return abs(math.hypot(point[0] - cx, point[1] - cy) - radius)
    ends = [(cx + radius * math.cos(math.radians(deg)), cy + radius * math.sin(math.radians(deg)))
            for deg in (start_deg, end_deg)]
    return min(math.hypot(point[0] - ex, point[1] - ey) for ex, ey in ends)


def _geom_deviation(point: tuple[float, float], kind: str, geom: Mapping[str, Any]) -> float:
    """点到**单条**落库几何的距离。"""
    if kind == LINE_KIND:
        return _point_segment_distance(point, (float(geom["x1"]), float(geom["y1"])),
                                       (float(geom["x2"]), float(geom["y2"])))
    return _arc_deviation(point, float(geom["cx"]), float(geom["cy"]), float(geom["r"]),
                          float(geom["start_deg"]), float(geom["end_deg"]))


def _chain_curve_deviation(runs: list[dict[str, Any]], floats: Sequence[tuple[float, float]],
                           per_span: int) -> float:
    """**落库几何**对曲线子样本的最大偏差（每条实体只看自己的参数区间；不按下标切片）。"""
    worst = 0.0
    for run in runs:
        for point in _curve_interval(floats, run["first"], run["last"], per_span):
            worst = max(worst, _geom_deviation(point, run["kind"], run["geom"]))
    return worst


def _entity_geom(run: dict[str, Any]) -> dict[str, Any]:
    """段描述 → L1 `geom`（坐标/半径整数纳米；角度 `*_deg` 为 float）。"""
    if run["kind"] == LINE_KIND:
        start, end = run["start"], run["end"]
        return {"x1": _round_nm(start[0]), "y1": _round_nm(start[1]),
                "x2": _round_nm(end[0]), "y2": _round_nm(end[1])}
    cx, cy, radius, start_deg, end_deg = run["arc"]
    return {"cx": _round_nm(cx), "cy": _round_nm(cy), "r": _round_nm(radius),
            "start_deg": float(start_deg), "end_deg": float(end_deg)}


# --------------------------------------------------------------------------------------
# 冻结操作 1：样条近似（折线 + 弧拟合）
# --------------------------------------------------------------------------------------


def spline_approx(sk_or_doc: Any, *, points_nm: Sequence[tuple[int, int]],
                  tolerance_nm: int = DEFAULT_TOLERANCE_NM, samples_per_span: int = 8,
                  mode: str = "arc", layer: str = DEFAULT_LAYER, construction: bool = False,
                  sketch_index: int = 0) -> list[str]:
    """过控制点的三次 Catmull-Rom 曲线 → `line`/`arc`（`mode="arc"`）或单条 `polyline`（`"line"`）。

    * `tolerance_nm`：整数纳米、> 0；尺子 = 每条实体**参数区间内 `CURVE_PROBES + 1` 个曲线子样本**
      到该实体几何的最大距离（+ `ROUND_SLACK_NM` 的 1 nm 取整余量）；超差抛
      `CadError(reason="tolerance_unreachable")`，`details` 给实测偏差（**不静默给超差的近似**）；
    * `mode="arc"`：对 `samples_per_span × VERIFY_FACTOR` 密度的曲线点贪心切段，弧优先
      （最小二乘圆、扫角 ∈ [2°, 180°]、逆时针），装不下退直线；
    * `mode="line"`：落**一条** `polyline`（顶点 = `samples_per_span` 密度采样点），同样按公差验；
    * 返回**本次新建**的实体 uuid，按路径顺序；失败时**不留半个实体**（先验后写）。
    """
    control = _as_points(points_nm, "points_nm")
    if len(control) < 2:
        raise _fail("spline_too_few_points", "spline needs at least 2 control points",
                    required=2, actual=len(control))
    tolerance = _as_nm(tolerance_nm, "tolerance_nm", positive=True)
    span = _as_int(samples_per_span, "samples_per_span", minimum=1)
    if mode not in SPLINE_MODES:
        raise _fail("unsupported_mode", "mode must be 'arc' or 'line'",
                    value=repr(mode), supported=list(SPLINE_MODES))
    layer_name = _as_layer(layer)
    flag = _as_flag(construction, "construction")
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    floats = [(float(point[0]), float(point[1])) for point in control]
    tol = float(tolerance)
    per_span = span * VERIFY_FACTOR

    if mode == "line":
        vertices = _catmull_rom(floats, span)
        worst = 0.0
        for point in _curve_interval(floats, 0, (len(control) - 1) * per_span, per_span,
                                     probes=span * VERIFY_FACTOR * 2):
            worst = max(worst, min(_point_segment_distance(point, vertices[k], vertices[k + 1])
                                   for k in range(len(vertices) - 1)))
        if worst > tol + ROUND_SLACK_NM:
            raise _fail("tolerance_unreachable",
                        "polyline approximation exceeds tolerance_nm; raise samples_per_span",
                        measured_nm=worst, tolerance_nm=tolerance, samples_per_span=span)
        geom: dict[str, Any] = {"points": [[_round_nm(x), _round_nm(y)] for x, y in vertices],
                                "closed": False, "scheme": "catmull_rom_polyline_approximation"}
        return [_append(sk, kind=POLYLINE_KIND, geom=geom, layer=layer_name, construction=flag)]

    fit_points = _catmull_rom(floats, per_span)
    runs = _fit_runs(floats, fit_points, tol, mode, per_span)
    for run in runs:
        run["start"] = fit_points[run["first"]]
        run["end"] = fit_points[run["last"]]
        run["geom"] = _entity_geom(run)
    worst = _chain_curve_deviation(runs, floats, per_span)
    if worst > tol + ROUND_SLACK_NM:
        raise _fail("tolerance_unreachable",
                    "arc/line fit exceeds tolerance_nm; raise samples_per_span",
                    measured_nm=worst, tolerance_nm=tolerance, samples_per_span=span)
    return [_append(sk, kind=run["kind"], geom=run["geom"], layer=layer_name, construction=flag)
            for run in runs]


# --------------------------------------------------------------------------------------
# 冻结操作 2/3：双向无限构造线 / 单方向无限射线（真几何：只有锚点 + 方向）
# --------------------------------------------------------------------------------------


def _infinite(sk_or_doc: Any, *, kind: str, anchor: tuple[int, int], dir_deg: float,
              layer: str, construction: Any, sketch_index: int) -> list[str]:
    """无限几何落库：`geom = {px, py, dir_deg}`，**无端点字段**（有限段走 `line`，语义不混）。"""
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    geom = {"px": int(anchor[0]), "py": int(anchor[1]), "dir_deg": float(dir_deg)}
    return [_append(sk, kind=kind, geom=geom, layer=_as_layer(layer),
                    construction=_as_flag(construction, "construction"))]


def construction_line(sk_or_doc: Any, *, point_nm: tuple[int, int] = None, dir_deg: float = None,
                      direction: Sequence[float] = None, layer: str = DEFAULT_LAYER,
                      construction: bool = True, sketch_index: int = 0) -> list[str]:
    """**双向无限**构造线（`kind="xline"`）：过 `point_nm`、方向 `dir_deg` 或 `direction`（二选一）。

    真几何判据：`point_on_xline(geom, t)` 对**任意带符号** `t`（含 ±10^12 nm 级）都在几何上，
    且 `geom` 没有 `x2/y2` 这类端点字段 —— 有限段是另一条操作，不冒充。
    """
    anchor = _as_point(point_nm, "point_nm")
    return _infinite(sk_or_doc, kind=XLINE_KIND, anchor=anchor,
                     dir_deg=_direction_deg(dir_deg, direction), layer=layer,
                     construction=construction, sketch_index=sketch_index)


def ray(sk_or_doc: Any, *, start_nm: tuple[int, int] = None, dir_deg: float = None,
        direction: Sequence[float] = None, layer: str = DEFAULT_LAYER,
        construction: bool = True, sketch_index: int = 0) -> list[str]:
    """**单方向无限**射线（`kind="ray"`）：端点 `start_nm`、方向 `dir_deg`/`direction`（二选一）。

    真几何判据：`point_on_ray(geom, t)` 对 `t >= 0` 有定义，`t < 0` 抛
    `CadError(reason="behind_ray_origin")`；`geom` 无端点字段。
    """
    anchor = _as_point(start_nm, "start_nm")
    return _infinite(sk_or_doc, kind=RAY_KIND, anchor=anchor,
                     dir_deg=_direction_deg(dir_deg, direction), layer=layer,
                     construction=construction, sketch_index=sketch_index)


#: 冻结 `OPERATIONS`（键 = 操作名，值 = 可调用；每个首个位置参都是 `sk_or_doc`）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "spline_approx": spline_approx,
    "construction_line": construction_line,
    "ray": ray,
}


def module_facts() -> dict[str, Any]:
    """族模块的机读判定面（不产用户文案）。"""
    return {
        "family": FAMILY,
        "op_count": len(OPERATIONS),
        "operations": sorted(OPERATIONS),
        "three_state": {"zero": ZERO, "unmeasured": UNMEASURED, "not_applicable": NA},
        "entity_kinds": [XLINE_KIND, RAY_KIND, ARC_KIND, LINE_KIND, POLYLINE_KIND],
        "length_unit": "integer_nanometre",
        "infinite_geometry": "anchor_plus_dir_deg_no_endpoints",
        "spline_scheme": "cubic_catmull_rom_then_least_squares_line_arc_fit",
        "tolerance_ruler": ("per-entity curve probes = %d per parameter interval; fit points = "
                            "samples_per_span * %d; slack = %s nm rounding"
                            % (CURVE_PROBES + 1, VERIFY_FACTOR, ROUND_SLACK_NM)),
        "reused_modules": ["cadcore.errors", "cadcore.sketch.constraints"],
    }


# --------------------------------------------------------------------------------------
# 自检（不依赖 pytest；末行固定 `EXTRA_OPS verdict=PASS ops=N`）
# --------------------------------------------------------------------------------------


def _sample_sketch() -> dict[str, Any]:
    """自检夹具：一张空草图（L1 字段齐备，uuid 合法；不依赖任何夹具文件）。"""
    return {"uuid": _constraints.new_uuid(), "plane": "XY", "entities": [], "constraints": [],
            "dimensions": []}


def selftest() -> int:
    """6 项自检（族接口 / 构造线双向 / 射线单向 + 阴性对照 / 弧拟合 / 折线模式 / 负例），返回 rc。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((str(name), bool(passed), str(detail)))

    facts = module_facts()
    check("ops_registered",
          isinstance(facts["op_count"], int) and facts["op_count"] == len(OPERATIONS)
          and set(OPERATIONS) == {"spline_approx", "construction_line", "ray"}
          and all(callable(fn) for fn in OPERATIONS.values()),
          "ops=%s" % facts["operations"])

    sk = _sample_sketch()
    line_uuid = construction_line(sk, point_nm=(1 * NM_PER_MM, 2 * NM_PER_MM), dir_deg=30.0)
    line_geom = sk["entities"][0]["geom"]
    far_a = point_on_xline(line_geom, -10 ** 12)
    far_b = point_on_xline(line_geom, 10 ** 12)
    check("xline_bidirectional",
          len(line_uuid) == 1 and sk["entities"][0]["kind"] == XLINE_KIND
          and set(line_geom) == {"px", "py", "dir_deg"} and far_a != far_b
          and far_a != (line_geom["px"], line_geom["py"]),
          "geom_keys=%s" % sorted(line_geom))

    ray_uuid = ray(sk, start_nm=(0, 0), direction=(0, 1))
    ray_geom = sk["entities"][1]["geom"]
    behind = False
    try:
        point_on_ray(ray_geom, -1)
    except CadError as exc:
        behind = exc.reason == "behind_ray_origin"
    check("ray_single_direction",
          len(ray_uuid) == 1 and sk["entities"][1]["kind"] == RAY_KIND
          and point_on_ray(ray_geom, 10 ** 12) == (0, 10 ** 12) and behind,
          "dir_deg=%.3f behind=%s" % (ray_geom["dir_deg"], behind))

    control = [(0, 0), (10 * NM_PER_MM, 5 * NM_PER_MM), (20 * NM_PER_MM, -5 * NM_PER_MM),
               (30 * NM_PER_MM, 0)]
    sk2 = _sample_sketch()
    ids = spline_approx(sk2, points_nm=control, tolerance_nm=20_000, samples_per_span=8)
    kinds = [entity["kind"] for entity in sk2["entities"]]

    def _endpoint(entity: Mapping[str, Any], which: str) -> tuple[int, int]:
        geom = entity["geom"]
        if entity["kind"] == LINE_KIND:
            return ((geom["x1"], geom["y1"]) if which == "start" else (geom["x2"], geom["y2"]))
        deg = float(geom["start_deg"] if which == "start" else geom["end_deg"])
        return (_round_nm(float(geom["cx"]) + float(geom["r"]) * math.cos(math.radians(deg))),
                _round_nm(float(geom["cy"]) + float(geom["r"]) * math.sin(math.radians(deg))))

    def _close(a: tuple[int, int], b: tuple[int, int], tol: int = 3) -> bool:
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    ends_ok = (_close(_endpoint(sk2["entities"][0], "start"), control[0])
               and _close(_endpoint(sk2["entities"][-1], "end"), control[-1]))
    check("spline_arc_fit",
          len(ids) == len(kinds) and ARC_KIND in kinds and len(kinds) >= 2
          and ends_ok and len(set(ids)) == len(ids),
          "runs=%d kinds=%s" % (len(kinds), kinds))

    sk3 = _sample_sketch()
    poly_ids = spline_approx(sk3, points_nm=control, tolerance_nm=1_000_000, samples_per_span=8,
                             mode="line")
    points = sk3["entities"][0]["geom"]["points"]
    check("spline_polyline_mode",
          len(poly_ids) == 1 and sk3["entities"][0]["kind"] == POLYLINE_KIND
          and [tuple(points[8 * k]) for k in range(len(control))] == list(control)
          and tuple(points[-1]) == control[-1],
          "vertices=%d" % len(points))

    bad_mode = False
    try:
        spline_approx(sk3, points_nm=control, mode="bezier")
    except CadError as exc:
        bad_mode = exc.reason == "unsupported_mode"
    check("negative_controls", bad_mode, "bad_mode=%s" % bad_mode)

    failed = [name for name, passed, _ in checks if not passed]
    for name, passed, detail in checks:
        print("EXTRA_OPS_CHECK name=%s ok=%d %s" % (name, 1 if passed else 0, detail))
    print("EXTRA_OPS verdict=%s ops=%d checks=%d failed=%d" % (
        "FAIL" if failed else "PASS", len(OPERATIONS), len(checks), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(selftest())
