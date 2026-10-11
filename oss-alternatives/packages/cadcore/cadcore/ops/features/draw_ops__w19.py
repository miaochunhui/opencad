#!/usr/bin/env python3
"""draw_ops 族 wave-19 实现片：must_have 第 **10** 号「对象捕捉」（OSNAP / DSETTINGS）。

选条依据（清单顺序 = `features/autocad-draw.json` 的 `must_have`，逐条状态取
`status/autocad-draw.json` 的 `items[].status`；任务书里写的 `features/draw_ops.json`
在本仓不存在，等价件是同族 `spec/draw_ops.json`（id=autocad-draw）+ `features/autocad-draw.json`）：

    0 直线 / 1 多段线 / 2 圆 / 3 圆弧 / 4 矩形 / 5 正多边形 / 6 点与点样式 /
    7 图案填充与渐变色 / 8 边界创建 / 9 修订云线 / **10 对象捕捉** / 11 正交与极轴追踪 /
    12 对象捕捉追踪与临时追踪点 / 13 动态输入 / 14 当前图层与对象特性

（该族 20 条 must_have 中 implemented=5：椭圆与椭圆弧、样条曲线、构造线与射线、面域、圆环；
其余 15 条 partial/missing ⇒ 0..14 编号，本片负责第 10 号。）

实现口径（真几何、真数据后果、L1 装得下）：

- 捕捉候选**实时**从 L1 草图实体几何算出（line / polyline / circle / arc / point / xline / ray），
  覆盖 AutoCAD 的八类对象捕捉：endpoint / midpoint / center / quadrant / intersection /
  perpendicular / tangent / extension，另加 nearest（最近点）。
- 落点后果：`osnap_probe` 把命中点**落成真 `point` 实体**（`geom = {x, y, snap_kind}`，
  坐标为捕捉后的精确整数纳米），返回 `[uuid]`；`osnap_candidates` 只读地返回**给出候选项的
  源实体 uuid**（按 距离 → 类型优先级 → uuid 排序）。
- 长度一律整数纳米；交点/切点/垂足这类非整数结果按「四舍五入、半数远离零」取整（`_round_nm`）。
- 失误一律抛 `cadcore.errors.CadError(reason=...)`：`no_sketch` / `invalid_length` /
  `invalid_argument` / `unknown_snap_kind` / `osnap_no_kinds` / `osnap_no_hit` /
  `sketch_too_large`，**不返回空表冒充成功**（"没测"与"没失败"不同形）。

L1 装不下的部分（**不**在本片、故该 must_have 端到端仍 partial）：状态栏 F3 开关与 DSETTINGS
对话框、捕捉标记/极轴虚线的画布渲染、`apps/opencad` 取点路径接线、以及 running-osnap 的
持续追踪状态机。本片只声明**库级几何捕捉能力**（每次调用显式传 kinds/容差），
这点如实写在这里，不冒充端到端完成。

自检：`python3 cadcore/ops/features/draw_ops__w19.py`（≥2 条硬断言，含阴性对照与 L1 往返）。
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Mapping, Sequence

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PKG_ROOT not in sys.path:  # 直接以脚本方式运行时也能 import cadcore.*
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError
from cadcore.sketch.constraints import new_uuid, sketch_entities

__all__ = ["FAMILY", "OPERATIONS", "SNAP_KINDS", "DEFAULT_TOLERANCE_NM",
           "osnap_probe", "osnap_candidates", "selftest"]

FAMILY = "draw_ops"

#: 捕捉类型（顺序 = 同距时的优先级：越靠前越优先）。
SNAP_KINDS: tuple[str, ...] = ("endpoint", "midpoint", "center", "quadrant", "intersection",
                               "perpendicular", "tangent", "extension", "nearest")

#: 缺省捕捉容差：5 mm（与族内其它探针量级一致）。
DEFAULT_TOLERANCE_NM: int = 5_000_000

#: 容差上限（防"用一个巨大容差换命中"把捕捉变成乱抓）。
_MAX_TOLERANCE_NM: int = 1_000_000_000_000

#: 求交的图元上限（笛卡尔对 O(n^2)；超上限显式报错，不静默跳过交集捕捉）。
_MAX_INTERSECTION_PRIMITIVES: int = 512

_EPS: float = 1e-9


# --------------------------------------------------------------------------------------
# 入参校验（全部在落库之前跑完：绝不半接受）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造一条带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(str(message), reason=str(reason), **details)


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """结构取值（映射 / 对象双兼容）。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sketch_of(target: Any, *, what: str = "sk_or_doc") -> Any:
    """`sk_or_doc` → 草图：①含 `entities` 的映射/对象本身；②文档（`sketches[0]`）。

    解析不出来抛 `CadError(reason="no_sketch")` —— 不凭空造一张空草图。
    """
    entities = _field(target, "entities")
    if isinstance(entities, list):
        return target
    sketches = _field(target, "sketches")
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)) and len(sketches) > 0:
        first = sketches[0]
        if isinstance(_field(first, "entities"), list):
            return first
        raise _fail("no_sketch", f"{what}.sketches[0] has no entity list", what=what)
    raise _fail("no_sketch", f"{what} is neither a sketch (entities) nor a document (sketches)", what=what)


def _num(value: Any) -> float | None:
    """任意值 → 有限浮点（bool 不算数；不是数返回 None）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if not math.isfinite(out):
        return None
    return out


def _nm(value: Any, *, what: str, strictly_positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool / 非数 / 非整数 / 非正一律报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_length", f"{what} must be an integer number of nanometres",
                    what=what, value=repr(value))
    number = float(value)
    if not math.isfinite(number) or number != int(number):
        raise _fail("invalid_length", f"{what} must be an integer number of nanometres",
                    what=what, value=repr(value))
    out = int(number)
    if strictly_positive and out <= 0:
        raise _fail("invalid_length", f"{what} must be > 0", what=what, value=out)
    return out


def _tolerance_nm(value: Any) -> int:
    """容差 → 正整数纳米（None = 缺省 5 mm）。"""
    if value is None:
        return DEFAULT_TOLERANCE_NM
    out = _nm(value, what="tolerance_nm", strictly_positive=True)
    if out > _MAX_TOLERANCE_NM:
        raise _fail("invalid_length", "tolerance_nm is absurdly large",
                    tolerance_nm=out, max_tolerance_nm=_MAX_TOLERANCE_NM)
    return out


def _point_nm(value: Any, *, what: str = "point_nm") -> tuple[int, int]:
    """点入参 → (x_nm, y_nm)：支持 `{"x","y"}` 映射或长度 ≥2 的序列。"""
    if isinstance(value, Mapping):
        x, y = value.get("x"), value.get("y")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        x, y = value[0], value[1]
    else:
        raise _fail("invalid_argument",
                    f"{what} must be a point (mapping with x/y, or a sequence of 2)", what=what,
                    value=repr(value))
    return (_nm(x, what=f"{what}.x"), _nm(y, what=f"{what}.y"))


def _kinds(value: Any) -> tuple[str, ...]:
    """捕捉类型入参 → 去重后的白名单子集（None = 全开；空集 / 未知类型报错）。"""
    if value is None:
        return tuple(SNAP_KINDS)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_argument", "kinds must be a sequence of snap kind names", value=repr(value))
    wanted: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in SNAP_KINDS:
            raise _fail("unknown_snap_kind", f"unknown snap kind {item!r}",
                        kind=repr(item), allowed=list(SNAP_KINDS))
        if item in wanted:
            raise _fail("invalid_argument", "kinds must not repeat", kind=item)
        wanted.append(item)
    if not wanted:
        raise _fail("osnap_no_kinds", "no snap kind is enabled")
    return tuple(wanted)


def _flag(value: Any, *, what: str) -> bool:
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))
    return value


def _layer(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "layer must be a non-empty string", value=repr(value))
    return value.strip()


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米：四舍五入、半数远离零（与 `precision/osnap.py` 同口径）。"""
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


# --------------------------------------------------------------------------------------
# 实体 → 捕捉几何（无法解析的实体不贡献候选：不猜几何，也不报错）
# --------------------------------------------------------------------------------------


def _pair(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        x, y = _num(value.get("x")), _num(value.get("y"))
        if x is not None and y is not None:
            return (x, y)
        return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        x, y = _num(value[0]), _num(value[1])
        if x is not None and y is not None:
            return (x, y)
    return None


def _segments(points: Sequence[tuple[float, float]], closed: bool) -> list[tuple[float, float, float, float]]:
    """顶点列 → 有限线段列（`closed` 且首末未重合时补闭合段）。"""
    segs: list[tuple[float, float, float, float]] = []
    for index in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[index], points[index + 1]
        segs.append((x1, y1, x2, y2))
    if closed and len(points) > 2 and points[0] != points[-1]:
        (x1, y1), (x2, y2) = points[-1], points[0]
        segs.append((x1, y1, x2, y2))
    return segs


def _describe(entity: Any) -> dict[str, Any]:
    """实体 → 捕捉几何描述：segs（线段）/ circles / arcs / pts（端点型）/ infs（构造线、射线）。"""
    desc: dict[str, Any] = {"uuid": "", "kind": "", "segs": [], "circles": [], "arcs": [],
                            "pts": [], "infs": []}
    uuid_ = _field(entity, "uuid", "")
    if isinstance(uuid_, str):
        desc["uuid"] = uuid_
    kind = _field(entity, "kind", "")
    desc["kind"] = kind if isinstance(kind, str) else ""
    geom = _field(entity, "geom", None)
    if not isinstance(geom, Mapping):
        return desc

    if desc["kind"] == "line":
        x1, y1 = _num(geom.get("x1")), _num(geom.get("y1"))
        x2, y2 = _num(geom.get("x2")), _num(geom.get("y2"))
        if None not in (x1, y1, x2, y2):
            desc["segs"].append((x1, y1, x2, y2))
    elif desc["kind"] == "polyline":
        raw = geom.get("points") or []
        points = [p for p in (_pair(item) for item in raw) if p is not None]
        desc["pts"].extend(points)
        desc["segs"].extend(_segments(points, bool(geom.get("closed"))))
    elif desc["kind"] == "circle":
        cx, cy, r = _num(geom.get("cx")), _num(geom.get("cy")), _num(geom.get("r"))
        if None not in (cx, cy, r) and r > 0.0:
            desc["circles"].append((cx, cy, r))
    elif desc["kind"] == "arc":
        cx, cy, r = _num(geom.get("cx")), _num(geom.get("cy")), _num(geom.get("r"))
        start, end = _num(geom.get("start_deg")), _num(geom.get("end_deg"))
        if None not in (cx, cy, r, start, end) and r > 0.0:
            desc["arcs"].append((cx, cy, r, start, end))
    elif desc["kind"] == "point":
        x, y = _num(geom.get("x")), _num(geom.get("y"))
        if x is not None and y is not None:
            desc["pts"].append((x, y))
    elif desc["kind"] in ("xline", "ray"):
        anchor = _pair(geom.get("anchor"))
        if anchor is None:
            anchor = _pair({"x": geom.get("anchor_x", geom.get("ax")),
                            "y": geom.get("anchor_y", geom.get("ay"))})
        if anchor is None:
            anchor = _pair({"x": geom.get("x"), "y": geom.get("y")})
        direction = _pair(geom.get("dir"))
        if direction is None:
            direction = _pair({"x": geom.get("dir_x", geom.get("dx")),
                               "y": geom.get("dir_y", geom.get("dy"))})
        if anchor is not None and direction is not None and (direction[0] or direction[1]):
            desc["infs"].append((anchor[0], anchor[1], direction[0], direction[1],
                                 desc["kind"] == "ray"))
    return desc


def _descriptors(sketch: Any) -> list[dict[str, Any]]:
    """草图 → 有几何可捕捉的实体描述列表（空描述的实体直接不参与）。"""
    out: list[dict[str, Any]] = []
    for entity in sketch_entities(sketch):
        desc = _describe(entity)
        if desc["segs"] or desc["circles"] or desc["arcs"] or desc["pts"] or desc["infs"]:
            out.append(desc)
    return out


# --------------------------------------------------------------------------------------
# 捕捉几何（全部按纳米量级算浮点，落点时再取整）
# --------------------------------------------------------------------------------------


def _point_on_circle(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return (cx + r * math.cos(rad), cy + r * math.sin(rad))


def _angle_on_arc(deg: float, start_deg: float, end_deg: float) -> bool:
    """角度是否落在**逆时针** `start → end` 的弧段上（首尾同角按整圆处理）。"""
    sweep = (end_deg - start_deg) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    return ((deg - start_deg) % 360.0) <= sweep + 1e-9


def _clamped_foot(probe: tuple[float, float], x1: float, y1: float, x2: float,
                  y2: float) -> tuple[tuple[float, float], float]:
    """probe 在**线段**上的垂足（夹在两端之间）与未夹参数 t（t 越界 = 落在线段外）。"""
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return ((x1, y1), 0.0)
    t = ((probe[0] - x1) * dx + (probe[1] - y1) * dy) / length2
    clamped = min(1.0, max(0.0, t))
    return ((x1 + clamped * dx, y1 + clamped * dy), t)


def _radial_foot(probe: tuple[float, float], cx: float, cy: float, r: float) -> tuple[float, float]:
    """probe 在**整圆**上的径向最近点（同时是「垂足」与「最近点」）。"""
    dx, dy = probe[0] - cx, probe[1] - cy
    dist = math.hypot(dx, dy)
    if dist <= 0.0:
        return (cx + r, cy)
    return (cx + dx / dist * r, cy + dy / dist * r)


def _tangent_points(probe: tuple[float, float], cx: float, cy: float,
                    r: float) -> list[tuple[float, float]]:
    """从 probe 向整圆作切的**两个**切点（probe 在圆内 → 无解，返回空表）。"""
    dx, dy = probe[0] - cx, probe[1] - cy
    dist = math.hypot(dx, dy)
    if dist <= 0.0 or dist < r - 1e-9:
        return []
    if abs(dist - r) <= 1e-9:
        return [(probe[0], probe[1])]
    base = math.atan2(dy, dx)
    theta = math.acos(min(1.0, max(-1.0, r / dist)))
    out: list[tuple[float, float]] = []
    for sign in (1.0, -1.0):
        angle = base + sign * theta
        out.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return out


def _seg_seg(a: tuple[float, float, float, float],
             b: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) <= 1e-12:
        return []
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return [(x1 + t * (x2 - x1), y1 + t * (y2 - y1))]
    return []


def _seg_circ(seg: tuple[float, float, float, float],
              circ: tuple[float, float, float, float | None, float | None]) -> list[tuple[float, float]]:
    """线段 × 圆/弧（`s_deg`/`e_deg` 为 None = 整圆；否则按弧段扫角过滤）。"""
    x1, y1, x2, y2 = seg
    cx, cy, r, start_deg, end_deg = circ
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - cx, y1 - cy
    a = dx * dx + dy * dy
    if a <= 0.0:
        return []
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return []
    root = math.sqrt(disc)
    out: list[tuple[float, float]] = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if t < -1e-9 or t > 1.0 + 1e-9:
            continue
        x, y = x1 + t * dx, y1 + t * dy
        if start_deg is not None and end_deg is not None:
            deg = math.degrees(math.atan2(y - cy, x - cx))
            if not _angle_on_arc(deg, start_deg, end_deg):
                continue
        out.append((x, y))
    return out


def _circ_circ(a: tuple[float, float, float, float | None, float | None],
               b: tuple[float, float, float, float | None, float | None]) -> list[tuple[float, float]]:
    """圆/弧 × 圆/弧（两个弧段都要各自扫角命中）。"""
    x1, y1, r1, s1, e1 = a
    x2, y2, r2, s2, e2 = b
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist <= 1e-12 or dist > r1 + r2 + 1e-9 or dist < abs(r1 - r2) - 1e-9:
        return []
    k = (r1 * r1 - r2 * r2 + dist * dist) / (2.0 * dist)
    h = math.sqrt(max(0.0, r1 * r1 - k * k))
    xm, ym = x1 + k * (x2 - x1) / dist, y1 + k * (y2 - y1) / dist
    if h <= 1e-12:
        pts = [(xm, ym)]
    else:
        ox, oy = -(y2 - y1) * h / dist, (x2 - x1) * h / dist
        pts = [(xm + ox, ym + oy), (xm - ox, ym - oy)]
    out: list[tuple[float, float]] = []
    for (x, y) in pts:
        ok = True
        for (cx, cy, _r, start_deg, end_deg) in ((x1, y1, r1, s1, e1), (x2, y2, r2, s2, e2)):
            if start_deg is not None and end_deg is not None:
                deg = math.degrees(math.atan2(y - cy, x - cx))
                if not _angle_on_arc(deg, start_deg, end_deg):
                    ok = False
        if ok:
            out.append((x, y))
    return out


def _intersections(descs: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, float, str]]:
    """图元两两求交（有限线段 / 圆 / 弧；构造线与射线不参与——口径写在 docstring）。"""
    prims: list[tuple[str, str, tuple[Any, ...]]] = []
    for desc in descs:
        uuid_ = str(desc["uuid"])
        for seg in desc["segs"]:
            prims.append((uuid_, "seg", tuple(seg)))
        for (cx, cy, r) in desc["circles"]:
            prims.append((uuid_, "circ", (cx, cy, r, None, None)))
        for arc in desc["arcs"]:
            prims.append((uuid_, "circ", tuple(arc)))
    if len(prims) > _MAX_INTERSECTION_PRIMITIVES:
        raise _fail("sketch_too_large", "too many primitives for intersection snapping",
                    primitives=len(prims), max_primitives=_MAX_INTERSECTION_PRIMITIVES)
    out: list[tuple[str, float, float, str]] = []
    for i in range(len(prims)):
        for j in range(i + 1, len(prims)):
            uuid_i, kind_i, geom_i = prims[i]
            uuid_j, kind_j, geom_j = prims[j]
            if kind_i == "seg" and kind_j == "seg":
                pts = _seg_seg(geom_i, geom_j)  # type: ignore[arg-type]
            elif kind_i == "seg" and kind_j == "circ":
                pts = _seg_circ(geom_i, geom_j)  # type: ignore[arg-type]
            elif kind_i == "circ" and kind_j == "seg":
                pts = _seg_circ(geom_j, geom_i)  # type: ignore[arg-type]
            else:
                pts = _circ_circ(geom_i, geom_j)  # type: ignore[arg-type]
            for (x, y) in pts:
                out.append(("intersection", x, y, uuid_i))
    return out


def _candidates(descs: Sequence[Mapping[str, Any]], probe: tuple[int, int],
                kinds: Sequence[str]) -> list[tuple[str, float, float, str]]:
    """候选表：`(snap_kind, x, y, 源实体 uuid)`；不含容差过滤（选择时统一过）。"""
    px, py = float(probe[0]), float(probe[1])
    out: list[tuple[str, float, float, str]] = []
    for desc in descs:
        uuid_ = str(desc["uuid"])
        if "endpoint" in kinds:
            for (x, y) in desc["pts"]:
                out.append(("endpoint", x, y, uuid_))
            for (x1, y1, x2, y2) in desc["segs"]:
                out.append(("endpoint", x1, y1, uuid_))
                out.append(("endpoint", x2, y2, uuid_))
            for (cx, cy, r, start, end) in desc["arcs"]:
                out.append(("endpoint",) + _point_on_circle(cx, cy, r, start) + (uuid_,))
                out.append(("endpoint",) + _point_on_circle(cx, cy, r, end) + (uuid_,))
        if "midpoint" in kinds:
            for (x1, y1, x2, y2) in desc["segs"]:
                out.append(("midpoint", (x1 + x2) / 2.0, (y1 + y2) / 2.0, uuid_))
            for (cx, cy, r, start, end) in desc["arcs"]:
                sweep = (end - start) % 360.0
                if sweep == 0.0:
                    sweep = 360.0
                out.append(("midpoint",) + _point_on_circle(cx, cy, r, start + sweep / 2.0) + (uuid_,))
        if "center" in kinds:
            for (cx, cy, _r) in desc["circles"]:
                out.append(("center", cx, cy, uuid_))
            for (cx, cy, _r, _s, _e) in desc["arcs"]:
                out.append(("center", cx, cy, uuid_))
        if "quadrant" in kinds:
            for (cx, cy, r) in desc["circles"]:
                for deg in (0.0, 90.0, 180.0, 270.0):
                    out.append(("quadrant",) + _point_on_circle(cx, cy, r, deg) + (uuid_,))
            for (cx, cy, r, start, end) in desc["arcs"]:
                for deg in (0.0, 90.0, 180.0, 270.0):
                    if _angle_on_arc(deg, start, end):
                        out.append(("quadrant",) + _point_on_circle(cx, cy, r, deg) + (uuid_,))
        if "perpendicular" in kinds or "extension" in kinds or "nearest" in kinds:
            for (x1, y1, x2, y2) in desc["segs"]:
                foot, t = _clamped_foot((px, py), x1, y1, x2, y2)
                if "perpendicular" in kinds:
                    out.append(("perpendicular", foot[0], foot[1], uuid_))
                if "extension" in kinds and (t < -1e-9 or t > 1.0 + 1e-9):
                    out.append(("extension", foot[0], foot[1], uuid_))
                if "nearest" in kinds:
                    out.append(("nearest", foot[0], foot[1], uuid_))
            for (cx, cy, r) in desc["circles"]:
                foot = _radial_foot((px, py), cx, cy, r)
                if "perpendicular" in kinds:
                    out.append(("perpendicular", foot[0], foot[1], uuid_))
                if "nearest" in kinds:
                    out.append(("nearest", foot[0], foot[1], uuid_))
            for (cx, cy, r, start, end) in desc["arcs"]:
                foot = _radial_foot((px, py), cx, cy, r)
                deg = math.degrees(math.atan2(foot[1] - cy, foot[0] - cx))
                if not _angle_on_arc(deg, start, end):
                    continue
                if "perpendicular" in kinds:
                    out.append(("perpendicular", foot[0], foot[1], uuid_))
                if "nearest" in kinds:
                    out.append(("nearest", foot[0], foot[1], uuid_))
            for (ax, ay, dx, dy, half) in desc["infs"]:
                length2 = dx * dx + dy * dy
                if length2 <= 0.0:
                    continue
                t = ((px - ax) * dx + (py - ay) * dy) / length2
                if half and t < 0.0:
                    continue
                foot = (ax + t * dx, ay + t * dy)
                if "perpendicular" in kinds:
                    out.append(("perpendicular", foot[0], foot[1], uuid_))
                if "nearest" in kinds:
                    out.append(("nearest", foot[0], foot[1], uuid_))
        if "tangent" in kinds:
            for (cx, cy, r) in desc["circles"]:
                for (tx, ty) in _tangent_points((px, py), cx, cy, r):
                    out.append(("tangent", tx, ty, uuid_))
            for (cx, cy, r, start, end) in desc["arcs"]:
                for (tx, ty) in _tangent_points((px, py), cx, cy, r):
                    deg = math.degrees(math.atan2(ty - cy, tx - cx))
                    if _angle_on_arc(deg, start, end):
                        out.append(("tangent", tx, ty, uuid_))
    if "intersection" in kinds:
        out.extend(_intersections(descs))
    return out


def _best_candidate(candidates: Sequence[tuple[str, float, float, str]], probe: tuple[int, int],
                    tolerance_nm: int) -> tuple[str, float, float, str, float] | None:
    """容差内的最优候选：距离最小，同距按 `SNAP_KINDS` 优先级，再按 uuid 稳定排序。"""
    px, py = float(probe[0]), float(probe[1])
    best_key: tuple[Any, ...] | None = None
    best: tuple[str, float, float, str, float] | None = None
    for (kind, x, y, uuid_) in candidates:
        distance = math.hypot(x - px, y - py)
        if distance > tolerance_nm + _EPS:
            continue
        key = (round(distance, 9), SNAP_KINDS.index(kind), uuid_, x, y)
        if best_key is None or key < best_key:
            best_key = key
            best = (kind, x, y, uuid_, distance)
    return best


# --------------------------------------------------------------------------------------
# 操作（族接口：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------


def osnap_probe(sk_or_doc: Any, *, point_nm: Any, kinds: Any = None, tolerance_nm: Any = None,
                layer: Any = "0", construction: Any = False) -> list[str]:
    """做一次对象捕捉并把命中点**落成真 `point` 实体**，返回 `[新实体 uuid]`（长度恒为 1）。

    候选实时从 L1 草图实体几何算出：endpoint / midpoint / center / quadrant /
    intersection（有限线段×线段、线段×圆/弧、圆/弧×圆/弧）/ perpendicular / tangent /
    extension（线段延长线上的垂足，只取落在线段外的那支）/ nearest。
    命中判据：候选到 `point_nm` 的距离 ≤ `tolerance_nm`（缺省 5 mm），取最近的；
    同距按 `SNAP_KINDS` 优先级。落点实体 `geom = {x, y, snap_kind}`，x/y 为捕捉后的
    **精确整数纳米**（非整数结果四舍五入、半数远离零），`snap_kind` 便于机读归因。
    没有任何候选命中 ⇒ 抛 `CadError(reason="osnap_no_hit")`（**不落几何、不返空表**）。
    `construction` 为 True 时落点是构造点（`construction=True`）。
    """
    sketch = _sketch_of(sk_or_doc)
    probe = _point_nm(point_nm, what="point_nm")
    wanted = _kinds(kinds)
    tolerance = _tolerance_nm(tolerance_nm)
    layer_name = _layer(layer)
    construction_flag = _flag(construction, what="construction")
    descs = _descriptors(sketch)
    candidates = _candidates(descs, probe, wanted)
    best = _best_candidate(candidates, probe, tolerance)
    if best is None:
        raise _fail("osnap_no_hit", "no snap candidate within tolerance", point_nm=list(probe),
                    tolerance_nm=int(tolerance), kinds=list(wanted), candidate_count=len(candidates))
    kind, x, y, _source_uuid, _distance = best
    entity = {"uuid": new_uuid(), "kind": "point", "layer": layer_name,
              "construction": bool(construction_flag),
              "geom": {"x": _round_nm(x), "y": _round_nm(y), "snap_kind": kind}}
    sketch_entities(sketch).append(entity)
    return [str(entity["uuid"])]


def osnap_candidates(sk_or_doc: Any, *, point_nm: Any, kinds: Any = None,
                     tolerance_nm: Any = None) -> list[str]:
    """**只读**查询：返回给出容差内候选的源实体 uuid（按 距离 → 类型优先级 → uuid 排序，去重）。

    不新建、不修改任何实体（用实体计数不变来自证只读）。一个候选都没有 ⇒
    抛 `CadError(reason="osnap_no_hit")`，不返回空表。
    """
    sketch = _sketch_of(sk_or_doc)
    probe = _point_nm(point_nm, what="point_nm")
    wanted = _kinds(kinds)
    tolerance = _tolerance_nm(tolerance_nm)
    descs = _descriptors(sketch)
    candidates = _candidates(descs, probe, wanted)
    px, py = float(probe[0]), float(probe[1])
    ranked: dict[str, tuple[float, int]] = {}
    for (kind, x, y, uuid_) in candidates:
        distance = math.hypot(x - px, y - py)
        if distance > tolerance + _EPS:
            continue
        key = (distance, SNAP_KINDS.index(kind))
        if uuid_ not in ranked or key < ranked[uuid_]:
            ranked[uuid_] = key
    if not ranked:
        raise _fail("osnap_no_hit", "no snap candidate within tolerance", point_nm=list(probe),
                    tolerance_nm=int(tolerance), kinds=list(wanted), candidate_count=len(candidates))
    ordered = sorted(ranked.items(), key=lambda item: (item[1][0], item[1][1], item[0]))
    return [uuid_ for uuid_, _key in ordered]


#: 族接口的操作表（键 = 操作名，值 = 可调用对象；每个操作 `op(sk_or_doc, **kwargs) -> list[str]`）。
OPERATIONS: dict[str, Any] = {
    "osnap_probe": osnap_probe,
    "osnap_candidates": osnap_candidates,
}


# --------------------------------------------------------------------------------------
# 自检（正向硬断言 + 阴性对照 + L1 文档往返；失败即 rc=1，绝不假绿）
# --------------------------------------------------------------------------------------


def _point_geom(sketch: Any, uuid_: str) -> dict[str, Any]:
    """自检用：按 uuid 取实体 geom（取不到返回空 dict，让断言自己红）。"""
    for entity in sketch_entities(sketch):
        if _field(entity, "uuid") == uuid_:
            geom = _field(entity, "geom")
            return dict(geom) if isinstance(geom, Mapping) else {}
    return {}


def _reason_of(callable_: Any, *args: Any, **kwargs: Any) -> str:
    """自检用：调用并把 `CadError.reason` 取出来（没抛错返回空串）。"""
    try:
        callable_(*args, **kwargs)
    except CadError as exc:
        return str(exc.reason)
    return ""


def _fixture() -> tuple[dict[str, Any], Any, Any, Any, Any, Any]:
    """自检夹具：一张 L1 草图（line / 两条对角 line / circle / arc），不依赖任何外部文件。"""
    mm = 1_000_000
    sketch: dict[str, Any] = {"entities": [], "constraints": [], "dimensions": []}

    def add(kind: str, geom: dict[str, Any]) -> Any:
        entity = {"uuid": new_uuid(), "kind": kind, "layer": "0", "construction": False,
                  "geom": geom}
        sketch["entities"].append(entity)
        return entity

    line = add("line", {"x1": 0, "y1": 0, "x2": 40 * mm, "y2": 30 * mm})
    diag_a = add("line", {"x1": 0, "y1": 0, "x2": 40 * mm, "y2": 40 * mm})
    diag_b = add("line", {"x1": 0, "y1": 40 * mm, "x2": 40 * mm, "y2": 0})
    circle = add("circle", {"cx": 10 * mm, "cy": 20 * mm, "r": 5 * mm})
    arc = add("arc", {"cx": 100 * mm, "cy": 0, "r": 10 * mm, "start_deg": 0.0, "end_deg": 90.0})
    return sketch, line, diag_a, diag_b, circle, arc


def selftest() -> int:
    """自检：正向几何硬断言 + 阴性对照 + L1 文档往返；返回 rc（0 = 全绿）。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), str(detail)))

    mm = 1_000_000
    sketch, line, diag_a, diag_b, circle, arc = _fixture()
    baseline_count = len(sketch["entities"])

    # 1) 中点：探针偏离 1 mm，落点必须是精确中点（整数纳米，无浮点尾巴）
    uuid_mid = osnap_probe(sketch, point_nm=(20 * mm, 16 * mm), kinds=["midpoint"])[0]
    geom_mid = _point_geom(sketch, uuid_mid)
    check("midpoint_exact_nm",
          geom_mid.get("x") == 20 * mm and geom_mid.get("y") == 15 * mm
          and geom_mid.get("snap_kind") == "midpoint" and isinstance(geom_mid.get("x"), int),
          repr(geom_mid))

    # 2) 端点：探针偏 0.4 mm ⇒ 精确落在 (40,30) mm
    uuid_end = osnap_probe(sketch, point_nm=(40 * mm + 400_000, 30 * mm), kinds=["endpoint"])[0]
    geom_end = _point_geom(sketch, uuid_end)
    check("endpoint_exact_nm",
          geom_end.get("x") == 40 * mm and geom_end.get("y") == 30 * mm
          and geom_end.get("snap_kind") == "endpoint", repr(geom_end))

    # 3) 圆心
    uuid_center = osnap_probe(sketch, point_nm=(11 * mm, 21 * mm), kinds=["center"])[0]
    geom_center = _point_geom(sketch, uuid_center)
    check("center_exact_nm",
          geom_center.get("x") == 10 * mm and geom_center.get("y") == 20 * mm
          and geom_center.get("snap_kind") == "center", repr(geom_center))

    # 4) 象限点（圆右侧）
    uuid_quad = osnap_probe(sketch, point_nm=(15 * mm + 200_000, 20 * mm), kinds=["quadrant"])[0]
    geom_quad = _point_geom(sketch, uuid_quad)
    check("quadrant_exact_nm",
          geom_quad.get("x") == 15 * mm and geom_quad.get("y") == 20 * mm
          and geom_quad.get("snap_kind") == "quadrant", repr(geom_quad))

    # 5) 交点：两条对角线交于 (20,20) mm
    uuid_cross = osnap_probe(sketch, point_nm=(20 * mm + 200_000, 20 * mm), kinds=["intersection"])[0]
    geom_cross = _point_geom(sketch, uuid_cross)
    check("intersection_exact_nm",
          geom_cross.get("x") == 20 * mm and geom_cross.get("y") == 20 * mm
          and geom_cross.get("snap_kind") == "intersection", repr(geom_cross))

    # 6) 垂足：探针 (20,10) mm 到 line 的垂足 = (17.6, 13.2) mm（t=0.44，纳米级精确）
    uuid_perp = osnap_probe(sketch, point_nm=(20 * mm, 10 * mm), kinds=["perpendicular"])[0]
    geom_perp = _point_geom(sketch, uuid_perp)
    check("perpendicular_exact_nm",
          geom_perp.get("x") == 17_600_000 and geom_perp.get("y") == 13_200_000
          and geom_perp.get("snap_kind") == "perpendicular", repr(geom_perp))

    # 7) 切点：探针在圆的切点附近 ⇒ 落点满足 |T-C| = r 且 (T-P) ⟂ (T-C)（几何不变量，不比对公式）
    probe_tan = (14_841_229, 21_550_000)
    uuid_tan = osnap_probe(sketch, point_nm=probe_tan, kinds=["tangent"])[0]
    geom_tan = _point_geom(sketch, uuid_tan)
    tx, ty = float(geom_tan.get("x", 0)), float(geom_tan.get("y", 0))
    cx_t, cy_t, r_t = float(10 * mm), float(20 * mm), float(5 * mm)
    dist_tc = math.hypot(tx - cx_t, ty - cy_t)
    dot_tp_tc = (tx - probe_tan[0]) * (tx - cx_t) + (ty - probe_tan[1]) * (ty - cy_t)
    rel_dot = abs(dot_tp_tc) / max(1.0, math.hypot(tx - probe_tan[0], ty - probe_tan[1]) * r_t)
    check("tangent_geometry_invariants",
          geom_tan.get("snap_kind") == "tangent" and abs(dist_tc - r_t) <= 1.0 and rel_dot <= 1e-6,
          f"geom={geom_tan} dist_tc={dist_tc} rel_dot={rel_dot}")

    # 8) 延伸：探针在线段延长线上 (48,36) mm（t=1.2 在线段外）⇒ 精确落点
    uuid_ext = osnap_probe(sketch, point_nm=(48 * mm, 36 * mm), kinds=["extension"])[0]
    geom_ext = _point_geom(sketch, uuid_ext)
    check("extension_exact_nm",
          geom_ext.get("x") == 48 * mm and geom_ext.get("y") == 36 * mm
          and geom_ext.get("snap_kind") == "extension", repr(geom_ext))

    # 9) 只读查询：给出候选的源实体 uuid，且不改动草图
    hits = osnap_candidates(sketch, point_nm=(20 * mm, 15 * mm + 100_000), kinds=["midpoint"],
                            tolerance_nm=500_000)
    check("candidates_source_uuid_and_readonly",
          hits == [line["uuid"]] and len(sketch["entities"]) == baseline_count + 6,
          f"hits={hits} entities={len(sketch['entities'])}")

    # 10) 阴性对照 A：远处探针 ⇒ osnap_no_hit
    check("negative_no_hit",
          _reason_of(osnap_probe, sketch, point_nm=(900 * mm, 900 * mm), tolerance_nm=1 * mm)
          == "osnap_no_hit", "")

    # 11) 阴性对照 B：只开 center 时线中点附近必须无命中（尺子分得清类型）
    check("negative_kind_isolation",
          _reason_of(osnap_probe, sketch, point_nm=(20 * mm, 15 * mm), kinds=["center"])
          == "osnap_no_hit", "")

    # 12) 入参/环境错误的 reason 必须机器可读
    check("error_unknown_kind",
          _reason_of(osnap_probe, sketch, point_nm=(0, 0), kinds=["vertex"]) == "unknown_snap_kind", "")
    check("error_no_kinds",
          _reason_of(osnap_probe, sketch, point_nm=(0, 0), kinds=[]) == "osnap_no_kinds", "")
    check("error_bad_tolerance",
          _reason_of(osnap_probe, sketch, point_nm=(0, 0), tolerance_nm=0) == "invalid_length"
          and _reason_of(osnap_probe, sketch, point_nm=(0, 0), tolerance_nm=1.5) == "invalid_length", "")
    check("error_bad_point",
          _reason_of(osnap_probe, sketch, point_nm=(1.5, 0)) == "invalid_length", "")
    check("error_no_sketch",
          _reason_of(osnap_probe, {}, point_nm=(0, 0)) == "no_sketch", "")

    # 13) 族接口形状：OPERATIONS 键与返回类型
    probe_result = osnap_probe(sketch, point_nm=(40 * mm + 400_000, 30 * mm), kinds=["endpoint"],
                               construction=True)
    check("operations_interface",
          sorted(OPERATIONS) == ["osnap_candidates", "osnap_probe"]
          and all(callable(fn) for fn in OPERATIONS.values())
          and isinstance(probe_result, list) and len(probe_result) == 1
          and all(isinstance(item, str) and item for item in probe_result),
          f"keys={sorted(OPERATIONS)} result={probe_result}")

    # 14) L1 文档路径：文档对象（doc.sketches[0]）+ 不变量不因新增实体变差 + L1 文本往返
    from cadcore.docmodel import check_invariants, document_from_l1, document_to_l1, new_document

    doc = new_document()
    doc.sketches.append(sketch)
    problems_before = list(check_invariants(doc))
    uuid_doc = osnap_probe(doc, point_nm=(40 * mm + 100_000, 30 * mm), kinds=["endpoint"])[0]
    problems_after = list(check_invariants(doc))
    l1_text = document_to_l1(doc)
    back = document_from_l1(l1_text)
    back_entities = _field(back.sketches[0], "entities", [])
    check("l1_document_roundtrip",
          len(problems_after) <= len(problems_before)
          and len(back_entities) == len(sketch["entities"])
          and any(_field(entity, "uuid") == uuid_doc for entity in back_entities),
          f"before={problems_before} after={problems_after} back_entities={len(back_entities)}")

    failures = [name for name, ok, _detail in checks if not ok]
    for (name, ok, detail) in checks:
        print(f"CHECK {name} {'ok' if ok else 'FAIL'}{(' ' + detail) if detail else ''}")
    print(f"OSNAP_SELFTEST checks={len(checks)} passed={len(checks) - len(failures)} "
          f"failed={len(failures)} rc={1 if failures else 0}")
    if failures:
        print(f"FEATURE_OK family=draw_ops op=osnap_probe verdict=FAIL failed={len(failures)}")
        return 1
    print("FEATURE_OK family=draw_ops op=osnap_probe verdict=PASS")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """入口：只跑自检（无参数）。"""
    del argv
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "osnap_candidates": {"tier": "query", "readonly": True, "returns": "data"},
}
