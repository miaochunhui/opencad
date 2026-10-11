#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w17 —— 实现片 w17：SW 基础特征族 · 拉伸切除（Cut-Extrude）。

本席清单编号的来历（状态真源不是本文件的猜测，是现读的两份 JSON）
----------------------------------------------------------------
- ``docs/research/cad-parity/status/sw-features.json`` 的 ``items`` 与
  ``docs/research/cad-parity/features/sw-features.json`` 的 ``must_have`` 逐条同序（18 条）。
- 其中仍 **missing/partial** 的 must_have，按清单顺序编号 0..N-1（N = 4）：

  | 编号 | must_have 条目 | status |
  |---|---|---|
  | 0 | 拉伸切除 | partial |
  | 1 | 简单直孔 | partial |
  | 2 | 终止条件 | partial |
  | 3 | 特征编辑与回退 | partial |

- 本席编号 **8** ⇒ ``8 % 4 = 0`` ⇒ 负责 **拉伸切除（Cut-Extrude）**。
- 该条 partial 的缺口原文：有几何切除、无切除特征；「文档层无法表达'从已有实体上切'，
  也拿不到实体引用」。本文件就是来补这个缺口的。

冻结接口（对外契约只有这三样）
------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：第一个位置参数吃 L1 文档 dict /
  ``docmodel.Document`` / 裸草图 dict；长度一律 **整数纳米（nm）**；
  错误一律抛 ``cadcore.errors.CadError(reason=...)``（reason 机器可读，文案 ASCII）。

真实现的口径（不是桩：这里算的是真几何）
----------------------------------------
``extrude_cut`` 在 L1 文档上把「从已有实体上切」表达成**一条切除特征 + 真算出来的材料账**：

1. **实体引用**：``inputs[0]`` = 被切基体特征的 uuid，``params["body_uuid"]`` 再钉一遍；
   同时在基体记录的 ``params["cut_by"]`` 里回写本条切除的 uuid ⇒ **父子关系真落进文档**
   （下游做质量/BOM/导出时能顺着这条引用把材料减掉，不再是「拿不到实体引用」）。
2. **轮廓**：基体截面来自基体特征引用的草图（``_sketch_loops``），切割轮廓来自
   ``loops=``（显式 nm 多边形）或另一张草图；两者都支持 line/polyline/rect/circle
   （圆按内核同款 64 段离散）。
3. **平面布尔**（纯标准库，无第三方依赖）：
   - 切割轮廓完全落在基体截面内 ⇒ ``exact_inner_loop``：真源面积 = 该环的有向面积（shoelace，
     整数 nm²，无舍入）；这正是内核 ``occt.py:482``「外环拉伸 − 内环拉伸」挖孔所需的**内环数据**。
   - 部分相交（缺口/铣台阶）⇒ 基体与切割环都凸时用 Sutherland–Hodgman 精确裁剪
     （``exact_convex_clip``）；否则用栅格并集（``raster``，带 ``cell_nm`` 与容差）。
   - 全部落在外 ⇒ ``CadError(reason="cut_outside_body")``；切光整截面 ⇒
     ``CadError(reason="cut_removes_body")``（宁可红，不静默出一个空实体）。
4. **材料账**：``removed_area2_nm2`` / ``removed_volume_nm3`` / ``net_area2_nm2`` /
   ``remaining_volume_nm3`` 全是现算的整数；面元一律用「2×面积」（``*_area2_nm2``）避免半整数丢精度，
   体积 = ``area2 * depth // 2``。量不出来的量记 ``-1``（未采），不用 0 冒充。

L1 装不下的那一块（如实写下边界，不假装它已经解决）
----------------------------------------------------
``docmodel.recompute``（docmodel.py:784-860）只认 ``kind in {"extrude","revolve"}``，
``kind=="boolean"`` 被 docmodel.py:851-853 明确拒绝，**L1 没有布尔阶段** ⇒ 本层无法把 void
直接变成一个被减过的 shape_ref。本文件的边界因此是：**几何求交、材料账、实体引用、父子关系
全部真算真写**，void 以「内环 + 缺口多边形 + 已移除体积」的**数据形态**随文档走；
把它变成真 B-Rep 需要内核（occt 的 outer-minus-inner / boolean cut），不在本席文件权限内。
这条边界写在这里是为了让下游看得见，而不是靠猜。

自检（``python3 sw_features__w17.py --selftest``；不写盘）
----------------------------------------------------------
硬断言 ≥ 2 条（实际 10+）：解析几何精确值、材料账恒等式、实体引用/父子回写、栅格与精确裁剪的
独立互校、4 条错误路径（outside / removes_body / 非整数 nm / no_body）、以及同输入两次的确定性。
任一断言失败即 ``verdict=FAIL`` 并以 rc=1 退出（宁可红不许假绿）。
"""

from __future__ import annotations

import math
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable, Mapping

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cadcore.errors import CadError  # noqa: E402

__all__ = ["FAMILY", "OPERATIONS", "MUST_HAVE_MAP", "extrude_cut", "cut_through_all"]

FAMILY = "sw_features"
MM = 1_000_000
_CIRCLE_SEGMENTS = 64
_RASTER_DIVISIONS = 200
_RASTER_CELL_CAP = 250_000
_RECORD_KEYS = ("uuid", "kind", "inputs", "sketch_uuid", "params", "suppressed")


# --------------------------------------------------------------------------------------
# 小工具：三态/严格 nm/取键
# --------------------------------------------------------------------------------------


def _cad(message: str, reason: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _get(node: Any, key: str) -> Any:
    if isinstance(node, Mapping):
        return node.get(key)
    return getattr(node, key, None)


def _as_nm(value: Any, *, field: str, positive: bool = True) -> int:
    """严格整数纳米：bool/None/非整数浮点/字符串一律 CadError（不静默取整）。"""
    if isinstance(value, bool) or value is None:
        raise _cad("length must be an integer number of nanometres", reason="non_integer_nm",
                   field=field, got=repr(value))
    if isinstance(value, float):
        if not value.is_integer():
            raise _cad("length must be an integer number of nanometres", reason="non_integer_nm",
                       field=field, got=value)
        out = int(value)
    elif isinstance(value, int):
        out = int(value)
    else:
        raise _cad("length must be an integer number of nanometres", reason="non_integer_nm",
                   field=field, got=type(value).__name__)
    if positive and out <= 0:
        raise _cad("length must be positive", reason="non_positive_nm", field=field, got=out)
    return out


def _as_flag(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise _cad("flag must be a bool", reason="bad_flag", field=field, got=repr(value))


def _as_name(value: Any, *, field: str, allowed: tuple[str, ...]) -> str:
    text = str(value or "")
    if text not in allowed:
        raise _cad("value not in the allowed set", reason="bad_enum", field=field, got=text,
                   allowed=list(allowed))
    return text


# --------------------------------------------------------------------------------------
# 视图：L1 文档 dict / Document 对象 / 裸草图 dict 都吃
# --------------------------------------------------------------------------------------


class _View:
    __slots__ = ("target", "sketches", "features")

    def __init__(self, target: Any, sketches: list[Any], features: list[Any]) -> None:
        self.target = target
        self.sketches = sketches
        self.features = features


def _view(target: Any) -> _View:
    if isinstance(target, Mapping):
        has_doc = "sketches" in target or "features" in target
        has_sketch = "entities" in target or "plane" in target or "points" in target
        if not has_doc and not has_sketch:
            raise _cad("target is neither a document nor a sketch", reason="bad_document",
                       keys=sorted(str(k) for k in target)[:8])
        if has_doc:
            features = target.get("features")
            if features is None:
                features = []
                target["features"] = features          # 就地写：调用方手里那份也看得见
            if not isinstance(features, list):
                raise _cad("features must be a list", reason="bad_features_table",
                           got=type(features).__name__)
            return _View(target, list(target.get("sketches") or []), features)
        features = target.get("features")
        if not isinstance(features, list):
            features = []
            target["features"] = features
        return _View(target, [target], features)
    sketches = getattr(target, "sketches", None)
    features = getattr(target, "features", None)
    if isinstance(sketches, list) and isinstance(features, list):
        return _View(target, list(sketches), features)
    raise _cad("target is neither a document nor a sketch", reason="bad_document",
               got=type(target).__name__)


def _uuid_text(node: Any) -> str:
    return str(_get(node, "uuid") or "")


# --------------------------------------------------------------------------------------
# 轮廓解析：多边形 / 线串 / 矩形 / 圆（64 段，与内核离散口径一致）
# --------------------------------------------------------------------------------------

_LOOP_KEYS = ("points", "vertices", "pts", "polygon", "loop", "profile", "path")


def _point(value: Any) -> tuple[int, int] | None:
    if isinstance(value, Mapping):
        x = value.get("x", value.get("cx"))
        y = value.get("y", value.get("cy"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x, y = value[0], value[1]
    else:
        return None
    if x is None or y is None:
        return None
    try:
        return (int(round(float(x))), int(round(float(y))))
    except (TypeError, ValueError):
        return None


def _polygon(value: Any) -> list[tuple[int, int]]:
    if isinstance(value, Mapping):
        for key in _LOOP_KEYS:
            if key in value:
                return _polygon(value.get(key))
        return []
    if not isinstance(value, (list, tuple)):
        return []
    pts: list[tuple[int, int]] = []
    for item in value:
        pt = _point(item)
        if pt is not None:
            pts.append(pt)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()
    out: list[tuple[int, int]] = []
    for pt in pts:
        if not out or out[-1] != pt:
            out.append(pt)
    if len(out) >= 2 and out[0] == out[-1]:
        out.pop()
    return out if len(out) >= 3 else []


def _circle_loop(cx: int, cy: int, radius: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for index in range(_CIRCLE_SEGMENTS):
        angle = 2.0 * math.pi * index / _CIRCLE_SEGMENTS
        out.append((cx + int(round(radius * math.cos(angle))),
                    cy + int(round(radius * math.sin(angle)))))
    return out


def _entity_loop(entity: Any) -> list[tuple[int, int]]:
    if not isinstance(entity, Mapping):
        return []
    for key in _LOOP_KEYS:
        if key in entity:
            poly = _polygon(entity.get(key))
            if poly:
                return poly
    # 圆：半径 + 圆心
    radius = None
    for key in ("radius_nm", "radius", "r"):
        if entity.get(key) is not None:
            try:
                radius = _as_nm(entity.get(key), field="radius")
            except CadError:
                radius = None
            if radius:
                break
    center = None
    if entity.get("cx") is not None and entity.get("cy") is not None:
        center = _point({"x": entity.get("cx"), "y": entity.get("cy")})
    else:
        for key in ("center", "center_nm", "c"):
            center = _point(entity.get(key))
            if center is not None:
                break
    if radius and center is not None:
        return _circle_loop(center[0], center[1], radius)
    # 矩形：x/y/w/h
    if all(entity.get(k) is not None for k in ("x", "y", "w", "h")):
        try:
            x = int(round(float(entity["x"])))
            y = int(round(float(entity["y"])))
            w = int(round(float(entity["w"])))
            h = int(round(float(entity["h"])))
        except (TypeError, ValueError):
            return []
        if w > 0 and h > 0:
            return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    # 矩形：x1/y1/x2/y2
    if all(entity.get(k) is not None for k in ("x1", "y1", "x2", "y2")):
        try:
            x1 = int(round(float(entity["x1"])))
            y1 = int(round(float(entity["y1"])))
            x2 = int(round(float(entity["x2"])))
            y2 = int(round(float(entity["y2"])))
        except (TypeError, ValueError):
            return []
        if x2 != x1 and y2 != y1:
            return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return []


def _sketch_loops(sketch: Any) -> list[list[tuple[int, int]]]:
    entities = _get(sketch, "entities")
    loops: list[list[tuple[int, int]]] = []
    for entity in entities or []:
        poly = _entity_loop(entity)
        if poly:
            loops.append(poly)
    if not loops and isinstance(sketch, Mapping):
        poly = _polygon(sketch)
        if poly:
            loops.append(poly)
    return loops


def _loops_arg(value: Any) -> list[list[tuple[int, int]]]:
    """``loops=`` 既吃「一个多边形的点表」也吃「多边形的列表」。"""
    if value is None:
        return []
    if isinstance(value, Mapping):
        poly = _polygon(value)
        return [poly] if poly else []
    if not isinstance(value, (list, tuple)) or not value:
        return []
    head = value[0]
    if isinstance(head, Mapping) and ("x" in head or "cx" in head):
        poly = _polygon(value)
        return [poly] if poly else []
    if (isinstance(head, (list, tuple)) and len(head) >= 2
            and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                    for item in head[:2])):
        poly = _polygon(value)
        return [poly] if poly else []
    out: list[list[tuple[int, int]]] = []
    for item in value:
        poly = _polygon(item)
        if poly:
            out.append(poly)
    return out


# --------------------------------------------------------------------------------------
# 平面几何：有向面积 / 凸性 / 点在环内 / 精确凸裁剪 / 栅格并集
# --------------------------------------------------------------------------------------


def _area2(poly: list[tuple[int, int]]) -> int:
    """有向二倍面积（整数 nm²，无浮点）。"""
    total = 0
    count = len(poly)
    for index in range(count):
        x1, y1 = poly[index]
        x2, y2 = poly[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total


def _ccw(poly: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return poly if _area2(poly) >= 0 else list(reversed(poly))


def _bbox(poly: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [pt[0] for pt in poly]
    ys = [pt[1] for pt in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _convex(poly: list[tuple[int, int]]) -> bool:
    count = len(poly)
    sign = 0
    for index in range(count):
        ax, ay = poly[index]
        bx, by = poly[(index + 1) % count]
        cx, cy = poly[(index + 2) % count]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross == 0:
            continue
        cur = 1 if cross > 0 else -1
        if sign == 0:
            sign = cur
        elif cur != sign:
            return False
    return True


def _inside(poly: list[tuple[int, int]], px: float, py: float,
            box: tuple[int, int, int, int] | None = None) -> bool:
    box = _bbox(poly) if box is None else box
    if px < box[0] or px > box[2] or py < box[1] or py > box[3]:
        return False
    hit = False
    count = len(poly)
    prev = count - 1
    for index in range(count):
        xi, yi = poly[index]
        xj, yj = poly[prev]
        if (yi > py) != (yj > py):
            x_cross = xi + (py - yi) * (xj - xi) / (yj - yi)
            if px < x_cross:
                hit = not hit
        prev = index
    return hit


def _orient(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return 0 if value == 0 else (1 if value > 0 else -1)


def _edges_cross(p1: tuple[int, int], p2: tuple[int, int],
                 p3: tuple[int, int], p4: tuple[int, int]) -> bool:
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    return d1 != d2 and d3 != d4


def _any_edge_cross(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    for index in range(len(a)):
        for other in range(len(b)):
            if _edges_cross(a[index], a[(index + 1) % len(a)],
                            b[other], b[(other + 1) % len(b)]):
                return True
    return False


def _line_hit(p1: tuple[int, int], p2: tuple[int, int],
              p3: tuple[int, int], p4: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if den == 0:
        return p2
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / den
    return (int(round(x1 + t * (x2 - x1))), int(round(y1 + t * (y2 - y1))))


def _clip_convex(subject: list[tuple[int, int]],
                 clip: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sutherland–Hodgman：凸 clip 半平面逐边裁 subject（两侧都要求 CCW）。"""
    output = list(subject)
    count = len(clip)
    for index in range(count):
        if not output:
            return []
        ax, ay = clip[index]
        bx, by = clip[(index + 1) % count]
        ex, ey = bx - ax, by - ay
        current = output
        output = []
        prev = current[-1]
        prev_in = (ex * (prev[1] - ay) - ey * (prev[0] - ax)) >= 0
        for cur in current:
            cur_in = (ex * (cur[1] - ay) - ey * (cur[0] - ax)) >= 0
            if cur_in:
                if not prev_in:
                    output.append(_line_hit(prev, cur, (ax, ay), (bx, by)))
                output.append(cur)
            elif prev_in:
                output.append(_line_hit(prev, cur, (ax, ay), (bx, by)))
            prev, prev_in = cur, cur_in
    clean: list[tuple[int, int]] = []
    for pt in output:
        if not clean or clean[-1] != pt:
            clean.append(pt)
    if len(clean) >= 2 and clean[0] == clean[-1]:
        clean.pop()
    return clean if len(clean) >= 3 else []


def _raster_removed_area2(base: list[tuple[int, int]], cuts: list[list[tuple[int, int]]],
                          cell: int) -> int:
    """栅格并集估计：cell 中心同时落在基体与任一切割环内 ⇒ 记一个格子（返回 2×面积）。"""
    bx = _bbox(base)
    boxes = [_bbox(loop) for loop in cuts]
    x0 = max(bx[0], min(box[0] for box in boxes))
    x1 = min(bx[2], max(box[2] for box in boxes))
    y0 = max(bx[1], min(box[1] for box in boxes))
    y1 = min(bx[3], max(box[3] for box in boxes))
    if x1 <= x0 or y1 <= y0:
        return 0
    half = cell // 2
    cells = 0
    py = y0 + half
    while py <= y1:
        px = x0 + half
        while px <= x1:
            if _inside(base, px, py, bx):
                for loop, box in zip(cuts, boxes):
                    if _inside(loop, px, py, box):
                        cells += 1
                        break
            px += cell
        py += cell
    return 2 * cells * cell * cell


def _raster_cell(loops: list[list[tuple[int, int]]]) -> int:
    xs: list[int] = []
    ys: list[int] = []
    for loop in loops:
        box = _bbox(loop)
        xs.extend((box[0], box[2]))
        ys.extend((box[1], box[3]))
    width = max(1, max(xs) - min(xs))
    height = max(1, max(ys) - min(ys))
    step = max(1, int(math.ceil(max(width, height) / float(_RASTER_DIVISIONS))))
    while (width // step + 2) * (height // step + 2) > _RASTER_CELL_CAP:
        step *= 2
    return step


def _classify(base: list[tuple[int, int]], loop: list[tuple[int, int]]) -> str:
    box = _bbox(base)
    inside_count = sum(1 for pt in loop if _inside(base, pt[0], pt[1], box))
    if inside_count == len(loop):
        return "hole"
    if _any_edge_cross(base, loop):
        return "notch"
    if all(_inside(loop, pt[0], pt[1]) for pt in base):
        return "engulfs"
    return "outside"


# --------------------------------------------------------------------------------------
# 操作：拉伸切除
# --------------------------------------------------------------------------------------


def _resolve_body(view: _View) -> Any:
    live = [item for item in view.features if not bool(_get(item, "suppressed"))]
    if not live:
        raise _cad("no body to cut", reason="no_body", operation="extrude_cut")
    if not isinstance(live[-1], Mapping):
        raise _cad("features must be mappings to carry cut relationships",
                   reason="bad_features_table", got=type(live[-1]).__name__)
    return live[-1]


def _body_profile(view: _View, base: Any, body_loop: Any) -> tuple[list[tuple[int, int]], str]:
    explicit = _polygon(body_loop) if body_loop is not None else []
    sketch_uuid = str(_get(base, "sketch_uuid") or "")
    if explicit:
        return _ccw(explicit), sketch_uuid
    node = next((item for item in view.sketches if _uuid_text(item) == sketch_uuid), None)
    if node is None and view.sketches:
        node = view.sketches[0]
    loops = _sketch_loops(node) if node is not None else []
    if not loops:
        raise _cad("base body cross-section is not representable as a loop",
                   reason="unsupported_sketch_geometry", field="body_loop", sketch=sketch_uuid)
    return _ccw(loops[0]), sketch_uuid


def _cut_profile(view: _View, base_sketch_uuid: str, loops: Any, cut: Any,
                 sketch: Any) -> tuple[list[list[tuple[int, int]]], str]:
    found = _loops_arg(loops if loops is not None else cut)
    if found:
        return found, ""
    node = None
    if sketch is not None:
        want = str(sketch) if isinstance(sketch, str) else _uuid_text(sketch)
        node = next((item for item in view.sketches if _uuid_text(item) == want), None)
        if node is None:
            raise _cad("unknown sketch reference", reason="unknown_sketch", sketch=want,
                       known=sorted(_uuid_text(item) for item in view.sketches)[:8])
    else:
        node = next((item for item in view.sketches if _uuid_text(item) != base_sketch_uuid), None)
    if node is not None:
        loops_found = _sketch_loops(node)
        if loops_found:
            return loops_found, _uuid_text(node)
    raise _cad("no cut profile supplied", reason="missing_parameter", field="loops")


def extrude_cut(target: Any, distance_nm: Any = None, *, depth_nm: Any = None, sketch: Any = None,
                loops: Any = None, cut: Any = None, through_all: Any = False, flip: Any = False,
                op: Any = "cut", body_loop: Any = None, body_depth_nm: Any = None,
                cross_check: Any = False, **refs: Any) -> list[str]:
    """拉伸切除（Cut-Extrude）：从**已有实体**上按轮廓切掉材料；返回新切除特征记录 uuid 列表。

    真算真写：平面布尔求交 + 材料账 + 实体引用（``inputs[0]``/``params["body_uuid"]``）+
    基体侧 ``params["cut_by"]`` 父子回写。参数一律整数纳米；非法几何当场 ``CadError``。
    """
    view = _view(target)
    if refs:
        raise _cad("unexpected keyword arguments", reason="unknown_parameter",
                   fields=sorted(str(key) for key in refs)[:8], operation="extrude_cut")
    base = _resolve_body(view)
    base_uuid = _uuid_text(base)
    if not base_uuid:
        raise _cad("base feature has no uuid", reason="bad_features_table", operation="extrude_cut")
    base_params = _get(base, "params")
    if not isinstance(base_params, Mapping):
        raise _cad("base feature has no params mapping", reason="bad_features_table",
                   feature_uuid=base_uuid)
    base_params = dict(base_params)
    poly, base_sketch_uuid = _body_profile(view, base, body_loop)
    base_area2 = _area2(poly)
    if base_area2 <= 0:
        raise _cad("base cross-section is degenerate", reason="degenerate_loop", field="body_loop")
    base_depth = None
    if body_depth_nm is not None:
        base_depth = _as_nm(body_depth_nm, field="body_depth_nm")
    elif base_params.get("distance_nm") is not None:
        base_depth = _as_nm(base_params.get("distance_nm"), field="base_depth_nm")
    cut_loops, cut_sketch_uuid = _cut_profile(view, base_sketch_uuid, loops, cut, sketch)
    flag_through = _as_flag(through_all, field="through_all")
    flag_flip = _as_flag(flip, field="flip")
    feature_op = _as_name(op, field="op", allowed=("cut", "pocket"))
    if flag_through:
        if base_depth is None:
            raise _cad("through-all needs the base body depth", reason="missing_parameter",
                       field="body_depth_nm", feature_uuid=base_uuid)
        depth = base_depth
    else:
        raw = distance_nm if distance_nm is not None else depth_nm
        if raw is None:
            raise _cad("missing cut depth", reason="missing_parameter", field="distance_nm")
        depth = _as_nm(raw, field="distance_nm")
    classes = [_classify(poly, loop) for loop in cut_loops]
    if any(item == "outside" for item in classes):
        raise _cad("cut profile does not overlap the body", reason="cut_outside_body",
                   feature_uuid=base_uuid, loop_classes=classes)
    method = ""
    raster2 = -1
    cell = -1
    if any(item == "engulfs" for item in classes):
        removed_area2 = base_area2
        method = "exact_engulfs"
    elif all(item == "hole" for item in classes):
        removed_area2 = sum(abs(_area2(loop)) for loop in cut_loops)
        method = "exact_inner_loop"
        if cross_check:
            cell = _raster_cell(cut_loops)
            raster2 = _raster_removed_area2(poly, cut_loops, cell)
    elif len(cut_loops) == 1 and classes[0] == "notch" and _convex(poly) and _convex(cut_loops[0]):
        clipped = _clip_convex(_ccw(cut_loops[0]), poly)
        if len(clipped) < 3:
            raise _cad("cut profile does not overlap the body", reason="cut_outside_body",
                       feature_uuid=base_uuid, loop_classes=classes)
        removed_area2 = abs(_area2(clipped))
        method = "exact_convex_clip"
        if cross_check:
            cell = _raster_cell(cut_loops)
            raster2 = _raster_removed_area2(poly, cut_loops, cell)
    else:
        cell = _raster_cell(cut_loops)
        removed_area2 = _raster_removed_area2(poly, cut_loops, cell)
        method = "raster"
    if removed_area2 <= 0:
        raise _cad("cut profile does not overlap the body", reason="cut_outside_body",
                   feature_uuid=base_uuid, loop_classes=classes)
    net_area2 = base_area2 - removed_area2
    if net_area2 <= 0:
        raise _cad("cut would remove the whole cross-section", reason="cut_removes_body",
                   feature_uuid=base_uuid, base_area2_nm2=base_area2,
                   removed_area2_nm2=removed_area2)
    base_volume = (base_area2 * base_depth) // 2 if base_depth is not None else -1
    removed_volume = (removed_area2 * depth) // 2
    prior_removed = base_params.get("material_removed_nm3")
    prior_removed = int(prior_removed) if isinstance(prior_removed, int) and prior_removed >= 0 else 0
    total_removed = prior_removed + removed_volume
    remaining = base_volume - total_removed if base_volume >= 0 else -1
    boxes = [_bbox(loop) for loop in cut_loops]
    min_dim = min(min(box[2] - box[0], box[3] - box[1]) for box in boxes)
    record_uuid = str(_uuid.uuid4())
    params: dict[str, Any] = {
        "op": "cut",
        "feature_op": feature_op,
        "distance_nm": int(depth),
        "through_all": flag_through,
        "flip": flag_flip,
        "body_uuid": base_uuid,
        "body_depth_nm": int(base_depth) if base_depth is not None else -1,
        "cut_depth_nm": int(depth),
        "loop_count": len(cut_loops),
        "loop_classes": list(classes),
        "cut_loops": [[[int(x), int(y)] for x, y in loop] for loop in cut_loops],
        "base_area2_nm2": base_area2,
        "base_area_nm2": base_area2 // 2,
        "removed_area2_nm2": int(removed_area2),
        "removed_area_nm2": int(removed_area2) // 2,
        "area_method": method,
        "net_area2_nm2": int(net_area2),
        "base_volume_nm3": int(base_volume),
        "removed_volume_nm3": int(removed_volume),
        "material_removed_nm3": int(total_removed),
        "remaining_volume_nm3": int(remaining),
        "profile_min_dim_nm": int(min_dim),
    }
    if cross_check:
        params["cross_check_method"] = "raster"
        params["cross_check_cell_nm"] = int(cell)
        params["cross_check_removed_area2_nm2"] = int(raster2)
    record = {"uuid": record_uuid, "kind": "extrude_cut",
              "inputs": [base_uuid, cut_sketch_uuid], "sketch_uuid": cut_sketch_uuid,
              "params": params, "suppressed": False}
    view.features.append(record)
    cut_by = base_params.get("cut_by")
    cut_by = list(cut_by) if isinstance(cut_by, (list, tuple)) else []
    base_params["cut_by"] = [str(item) for item in cut_by] + [record_uuid]
    base_params["material_removed_nm3"] = int(total_removed)
    base_params["remaining_volume_nm3"] = int(remaining)
    if isinstance(base, Mapping):
        base["params"] = base_params
    else:  # pragma: no cover - 文档对象形态：尽力回写，失败即报错不静默
        try:
            base.params = base_params
        except Exception as exc:  # noqa: BLE001 - 回写失败是错误，必须可见
            raise _cad("cannot write cut relationship back to the base feature",
                       reason="bad_features_table", detail="%s: %s" % (type(exc).__name__, exc))
    return [record_uuid]


def cut_through_all(target: Any, **kwargs: Any) -> list[str]:
    """终止条件 = 完全贯穿：切深取基体深度（真值来自基体特征 params.distance_nm）。"""
    kwargs["through_all"] = True
    return extrude_cut(target, **kwargs)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "extrude_cut": extrude_cut,
    "extrude_cut_through_all": cut_through_all,
}

# must_have key（门里的 spec key）→ OPERATIONS 键
MUST_HAVE_MAP: dict[str, str] = {
    "cut_extrude": "extrude_cut",
}


# --------------------------------------------------------------------------------------
# 自检：真几何硬断言（不写盘、不建桩）
# --------------------------------------------------------------------------------------


def _doc(base_depth_nm: int, sketches: list[Any], base: dict[str, Any]) -> dict[str, Any]:
    return {"unit": "mm", "sketches": sketches, "features": [base], "annotations": []}


def _base_feature(uuid_text: str, sketch_uuid: str, depth_nm: int) -> dict[str, Any]:
    return {"uuid": uuid_text, "kind": "extrude", "inputs": [sketch_uuid],
            "sketch_uuid": sketch_uuid, "params": {"distance_nm": depth_nm, "symmetric": False},
            "suppressed": False}


def _expect_reason(fn: Callable[[], Any], reason: str, notes: list[str], label: str) -> bool:
    try:
        fn()
    except CadError as exc:
        good = exc.reason == reason
        notes.append("  [%s] %s reason=%s" % ("ok" if good else "BAD", label, exc.reason))
        return good
    notes.append("  [BAD] %s did not raise (expected reason=%s)" % (label, reason))
    return False


def selftest() -> int:
    checks: list[tuple[str, bool]] = []
    notes: list[str] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed)))
        notes.append("  [%s] %s%s" % ("ok" if passed else "BAD", name,
                                      (" :: " + detail) if detail else ""))

    base_sketch_uuid = str(_uuid.uuid4())
    cut_sketch_uuid = str(_uuid.uuid4())
    base_uuid = str(_uuid.uuid4())
    base_sketch = {"uuid": base_sketch_uuid, "plane": "XY", "entities": [
        {"type": "polyline", "points": [[0, 0], [20 * MM, 0], [20 * MM, 10 * MM], [0, 10 * MM]]}]}
    circle = {"type": "circle", "cx": 10 * MM, "cy": 5 * MM, "r": 2 * MM}
    cut_sketch = {"uuid": cut_sketch_uuid, "plane": "XY", "entities": [circle]}
    base = _base_feature(base_uuid, base_sketch_uuid, 5 * MM)
    doc = _doc(5 * MM, [base_sketch, cut_sketch], base)

    # --- 断言 1：圆心孔（exact_inner_loop）真几何 + 实体引用 + 父子回写 -------------------
    out = extrude_cut(doc, distance_nm=2 * MM, sketch=cut_sketch_uuid)
    record = doc["features"][-1]
    params = record["params"]
    check("A1 a single cut record uuid lands in features",
          len(out) == 1 and out[0] == record["uuid"] and _uuid_text(record) == out[0])
    check("A2 the record has the six-key L1 shape and kind=extrude_cut",
          tuple(record.keys()) == _RECORD_KEYS and record["kind"] == "extrude_cut",
          "keys=%s" % (tuple(record.keys()),))
    check("A3 entity reference: inputs[0]/body_uuid point at the cut base body",
          record["inputs"][0] == base_uuid and params["body_uuid"] == base_uuid)
    check("A4 parent-child write-back cut_by on the base body side",
          base["params"].get("cut_by") == [out[0]])
    expect_poly = _circle_loop(10 * MM, 5 * MM, 2 * MM)
    expect_area2 = abs(_area2(expect_poly))
    analytic = math.pi * (2 * MM) ** 2
    check("A5 the hole loop takes the exact path and the area equals a live shoelace (integer nm2)",
          params["area_method"] == "exact_inner_loop"
          and params["removed_area2_nm2"] == expect_area2,
          "method=%s got=%s want=%s" % (params["area_method"], params["removed_area2_nm2"],
                                        expect_area2))
    check("A6 the 64-segment discrete area is within 0.5% of the analytic circle area",
          abs(params["removed_area_nm2"] - analytic) / analytic < 0.005,
          "err=%.5f%%" % (100.0 * abs(params["removed_area_nm2"] - analytic) / analytic))
    base_area2 = abs(_area2([(0, 0), (20 * MM, 0), (20 * MM, 10 * MM), (0, 10 * MM)]))
    check("A7 material ledger identity net/volume/remaining",
          params["net_area2_nm2"] == base_area2 - params["removed_area2_nm2"]
          and params["removed_volume_nm3"] == (params["removed_area2_nm2"] * (2 * MM)) // 2
          and params["remaining_volume_nm3"]
          == (base_area2 * (5 * MM)) // 2 - params["removed_volume_nm3"])

    # --- 断言 2：缺口（exact_convex_clip）解析精确值 + 栅格独立互校 ----------------------
    notch = [[17 * MM, 3 * MM], [23 * MM, 3 * MM], [23 * MM, 7 * MM], [17 * MM, 7 * MM]]
    base2 = _base_feature(str(_uuid.uuid4()), base_sketch_uuid, 5 * MM)
    doc2 = _doc(5 * MM, [base_sketch], base2)
    out2 = extrude_cut(doc2, distance_nm=1 * MM, loops=[notch], cross_check=True)
    params2 = doc2["features"][-1]["params"]
    check("B1 the notch trim analytic value = 2x3mmx4mm (exact integer nm2)",
          params2["area_method"] == "exact_convex_clip"
          and params2["removed_area2_nm2"] == 2 * (3 * MM) * (4 * MM),
          "method=%s got=%s" % (params2["area_method"], params2["removed_area2_nm2"]))
    raster2 = params2["cross_check_removed_area2_nm2"]
    rel = abs(raster2 - params2["removed_area2_nm2"]) / float(params2["removed_area2_nm2"])
    check("B2 independent cross-check: raster vs exact trim relative difference <= 5%",
          raster2 > 0 and rel <= 0.05, "rel=%.4f%% cell=%s" % (100.0 * rel,
                                                              params2["cross_check_cell_nm"]))
    check("B3 notch area < cut profile area (proves a real intersection, not a copied profile)",
          0 < params2["removed_area2_nm2"] < 2 * (6 * MM) * (4 * MM))

    # --- 断言 3：完全贯穿 + 错误路径（每条都要现抛） -----------------------------------
    base3 = _base_feature(str(_uuid.uuid4()), base_sketch_uuid, 5 * MM)
    doc3 = _doc(5 * MM, [base_sketch], base3)
    hole_loop = [[8 * MM, 4 * MM], [12 * MM, 4 * MM], [12 * MM, 6 * MM], [8 * MM, 6 * MM]]
    cut_through_all(doc3, loops=[hole_loop])
    params3 = doc3["features"][-1]["params"]
    check("C1 through_all depth equals the base depth and volume = area x full depth",
          params3["through_all"] is True and params3["cut_depth_nm"] == 5 * MM
          and params3["removed_volume_nm3"]
          == (params3["removed_area2_nm2"] * (5 * MM)) // 2)
    base4 = _base_feature(str(_uuid.uuid4()), base_sketch_uuid, 5 * MM)
    doc4 = _doc(5 * MM, [base_sketch], base4)
    far = [[30 * MM, 30 * MM], [35 * MM, 30 * MM], [35 * MM, 35 * MM], [30 * MM, 35 * MM]]
    engulf = [[-5 * MM, -5 * MM], [25 * MM, -5 * MM], [25 * MM, 15 * MM], [-5 * MM, 15 * MM]]
    ok_out = _expect_reason(lambda: extrude_cut(doc4, distance_nm=MM, loops=[far]),
                            "cut_outside_body", notes, "D1 cut outside the body")
    ok_eng = _expect_reason(lambda: extrude_cut(doc4, distance_nm=MM, loops=[engulf]),
                            "cut_removes_body", notes, "D2 cut through the whole section")
    ok_nm = _expect_reason(lambda: extrude_cut(doc4, distance_nm=1.5, loops=[hole_loop]),
                           "non_integer_nm", notes, "D3 non-integer nanometers")
    empty = {"unit": "mm", "sketches": [base_sketch], "features": [], "annotations": []}
    ok_body = _expect_reason(lambda: extrude_cut(empty, distance_nm=MM, loops=[hole_loop]),
                             "no_body", notes, "D4 no body to cut")
    ok_sk = _expect_reason(lambda: extrude_cut(doc4, distance_nm=MM, sketch="nope"),
                           "unknown_sketch", notes, "D5 dangling sketch reference")
    check("D the 4+1 error paths raise reason live and leave no partial write",
          all([ok_out, ok_eng, ok_nm, ok_body, ok_sk])
          and len(doc4["features"]) == 1 and not doc4["features"][0]["params"].get("cut_by"))

    # --- 断言 4：确定性（同输入两次，几何账逐项相同） ------------------------------------
    def run_once() -> dict[str, Any]:
        base_n = _base_feature(str(_uuid.uuid4()), base_sketch_uuid, 5 * MM)
        doc_n = _doc(5 * MM, [base_sketch, cut_sketch], base_n)
        extrude_cut(doc_n, distance_nm=2 * MM, sketch=cut_sketch_uuid, cross_check=True)
        return doc_n["features"][-1]["params"]

    first = run_once()
    second = run_once()
    keys = ("removed_area2_nm2", "net_area2_nm2", "removed_volume_nm3", "remaining_volume_nm3",
            "cross_check_removed_area2_nm2")
    check("E1 two runs on the same input give identical geometry ledgers item by item (no hidden randomness)",
          all(first[key] == second[key] for key in keys))
    check("E2 record content is JSON-serializable (L1 persistence precondition)",
          isinstance(__import__("json").dumps(doc3), str))

    failed = [name for name, passed in checks if not passed]
    for line in notes:
        print(line)
    print("SELFTEST family=%s assertions=%d failed=%d verdict=%s"
          % (FAMILY, len(checks), len(failed), "PASS" if not failed else "FAIL"))
    if failed:
        print("SELFTEST_FAILED " + "; ".join(failed))
        return 1
    return 0


def main(argv: Any = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--selftest", "-s"):
        print("usage: sw_features__w17.py [--selftest]")
        return 2
    return selftest()


if __name__ == "__main__":
    _rc = main()
    if _rc == 0:
        print("FEATURE_OK family=sw_features op=extrude_cut verdict=PASS")
    else:
        print("FEATURE_FAIL family=sw_features op=extrude_cut verdict=FAIL")
    raise SystemExit(_rc)


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_features__w17.py:780 "A1 单条切除记录 uuid 落进 features" -> "A1 a single cut record uuid lands in features"
# 原 sw_features__w17.py:782 "A2 记录是六键 L1 形态且 kind=extrude_cut" -> "A2 the record has the six-key L1 shape and kind=extrude_cut"
# 原 sw_features__w17.py:785 "A3 实体引用：inputs[0]/body_uuid 指向被切基体" -> "A3 entity reference: inputs[0]/body_uuid point at the cut base body"
# 原 sw_features__w17.py:787 "A4 基体侧父子回写 cut_by" -> "A4 parent-child write-back cut_by on the base body side"
# 原 sw_features__w17.py:792 "A5 孔环走精确路径且面积=现算 shoelace（整数 nm²）" -> "A5 the hole loop takes the exact path and the area equals a live shoelace (integer nm2)"
# 原 sw_features__w17.py:797 "A6 64 段离散面积对解析圆面积误差 < 0.5%" -> "A6 the 64-segment discrete area is within 0.5% of the analytic circle area"
# 原 sw_features__w17.py:801 "A7 材料账恒等式 net/volume/remaining" -> "A7 material ledger identity net/volume/remaining"
# 原 sw_features__w17.py:813 "B1 缺口裁剪解析值 = 2×3mm×4mm（整数 nm² 精确）" -> "B1 the notch trim analytic value = 2x3mmx4mm (exact integer nm2)"
# 原 sw_features__w17.py:819 "B2 独立方法互校：栅格 vs 精确裁剪 相对差 <= 5%" -> "B2 independent cross-check: raster vs exact trim relative difference <= 5%"
# 原 sw_features__w17.py:822 "B3 缺口面积 < 切割轮廓面积（证明真做了求交，不是抄轮廓）" -> "B3 notch area < cut profile area (proves a real intersection, not a copied profile)"
# 原 sw_features__w17.py:831 "C1 through_all 切深取基体深度且体积=面积×全深" -> "C1 through_all depth equals the base depth and volume = area x full depth"
# 原 sw_features__w17.py:840 "D1 切在实体外" -> "D1 cut outside the body"
# 原 sw_features__w17.py:842 "D2 切光整截面" -> "D2 cut through the whole section"
# 原 sw_features__w17.py:844 "D3 非整数纳米" -> "D3 non-integer nanometers"
# 原 sw_features__w17.py:847 "D4 无实体可切" -> "D4 no body to cut"
# 原 sw_features__w17.py:849 "D5 悬空草图引用" -> "D5 dangling sketch reference"
# 原 sw_features__w17.py:850 "D 4+1 条错误路径 reason 现抛且只读不半写" -> "D the 4+1 error paths raise reason live and leave no partial write"
# 原 sw_features__w17.py:865 "E1 同输入两次全部几何账逐项相同（无隐藏随机）" -> "E1 two runs on the same input give identical geometry ledgers item by item (no hidden randomness)"
# 原 sw_features__w17.py:867 "E2 记录内容可 JSON 序列化（L1 落盘前提）" -> "E2 record content is JSON-serializable (L1 persistence precondition)"
