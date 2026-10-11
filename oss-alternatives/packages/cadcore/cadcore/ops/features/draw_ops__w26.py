#!/usr/bin/env python3
"""DRAW_OPS 族 w26 片：CIRCLE 的三种构造画法（2P 两点直径 / 3P 三点 / TTR 相切相切半径）。

片身份（冻结接口）
------------------
::

    FAMILY = "draw_ops"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    op(sk_or_doc, **kwargs) -> list[str]        # 本次新建对象的 uuid 列表
    长度一律整数纳米（bool / 非整数浮点拒收）；可预期错误一律抛 cadcore.errors.CadError(reason=...)

本片负责哪一条（编号怎么来的，写死在这里防串号）
------------------------------------------------
本次现读的两个数据源：
* `docs/research/cad-parity/features/autocad-draw.json` → `must_have` 共 **20** 条（清单顺序）；
* `docs/research/cad-parity/status/autocad-draw.json` → 这 20 条的状态是 implemented 5 / partial 15 / missing 0。
仍 `missing|partial` 的 must_have 按清单顺序编号 `0..N-1`（N=15）：
0 直线 / 1 多段线 / **2 圆** / 3 圆弧 / 4 矩形 / 5 正多边形 / 6 点与点样式 / 7 图案填充与渐变色 /
8 边界创建 / 9 修订云线 / 10 对象捕捉 / 11 正交与极轴追踪 / 12 对象捕捉追踪与临时追踪点 /
13 动态输入 / 14 当前图层与对象特性。
第 17 号 ≥ N ⇒ 顺延取模 `17 % 15 = 2` ⇒ 本片实现 **圆**。status 里该条的缺口原文：
「无 2P（两点直径）、无 3P（三点）、无 TTR（相切相切半径）—— 全仓无切点捕捉与相切构造」。
（若按 must_have 全清单 0 基编号读成第 17 条，会落到「对象捕捉追踪与临时追踪点」；
本片按任务书第 ① 步的定义 —— 编号是对 **remaining 清单** 编的 —— 取「圆」，理由即上面的取模算式。）

在 L1 文档模型上真做了什么（真几何，不是桩）
--------------------------------------------
三个操作都把**真算出来的圆心/半径**交给 `cadcore.sketch.commands.add_circle`（L1 实体写入的唯一入口），
返回落在文档里的实体 uuid；自测从实体表**读回 geom** 复算等距/切距，不信内存里的中间量。
* `circle_2p`：两点定直径 ⇒ 圆心取中点、半径取半距（整数纳米取整残差 ≤ 1 nm，见 `_round_nm`）；
* `circle_3p`：三点外接圆（行列式解圆心 + 外接半径），三点共线/重合或半径超限 ⇒
  `CadError(reason="degenerate_geometry")`；取整后三点等距残差超 `tol_nm` ⇒ `rounding_error`；
* `circle_ttr`：与两个既有对象相切、半径给定的圆。**候选解按类型解析求出**（不是采样不是试凑）：
  线-线 = 两条 ±r 偏移线的交点；线-圆 = 偏移线与「距圆心 `rc±r`」圆的交点（一元二次）；
  圆-圆 = 「距圆心 `r1±r` 与 `r2±r`」两圆交点。候选按两处**切距残差**过滤，再取离拾取点最近者
  （与 AutoCAD TTR「点在哪个象限就出哪个解」同口径）；解不出来 ⇒ `no_solution`，
  没有拾取点且有多个解 ⇒ `ambiguous_solution`（禁「随便挑一个」）。

有意写下的边界（防下游靠猜）
----------------------------
* 相切对象只认 `line` / `circle` / `arc` 三种实体；`arc` 按**其整圆**参与求解（切点允许落在外，
  与 AutoCAD 延伸相切口径一致）；相切于折线/样条/其它 ⇒ `unsupported_reference_kind`。
* 相切按**无限长直线**算（线段延伸相切合法），本片不裁剪也不改被切对象 —— 只新增一个圆。
* 平行两线（偏移线不相交或重合）⇒ `degenerate_configuration`；无实数解 ⇒ `no_solution`。
* 取整成整数纳米后**再复算一遍残差**，超 `tol_nm`（缺省 50 nm，与族门 TOL_NM 同尺）⇒ `no_solution`。
* 容器口径：既吃草图容器（含 `entities` 的映射/对象），也吃 L1 文档载荷（含 `sketches`）；
  后者用 `sketch_uuid=` 选草图，不给且不止一张 ⇒ `ambiguous_sketch`（**不猜**）。
* 本片不写约束/尺寸（约束写入有唯一真源 `sketch/primitives.py`），也不做修剪。
* 上游异常（`SketchError` 系）统一经 `_delegate` 翻译成 `CadError` 并保留上游 reason，不让别的异常类型漏出族接口。

自测（文件自带，不依赖 pytest，硬断言都从实体表读回 geom 复算）
----------------------------------------------------------------

    cd oss-alternatives/packages/cadcore && python3 cadcore/ops/features/draw_ops__w26.py

末行判定行：`FEATURE_OK family=draw_ops op=circle_ttr verdict=PASS`（FAIL 时 rc=1，逐条 CHECK 行给 detail）。
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch import commands as _commands
from cadcore.sketch.constraints import sketch_entities as _sketch_entities

__all__ = ["FAMILY", "OPERATIONS", "circle_2p", "circle_3p", "circle_ttr", "selftest"]

#: 族名（冻结）。
FAMILY = "draw_ops"

#: 缺省相切残差容差（纳米）：与族门 `parity_draw_ops_gate.TOL_NM` 同一把尺子。
_TOL_NM: int = 50

#: 外接圆半径上限（1e15 nm = 1000 km）：超了就是「三点近乎共线」，不许把天文数字写进 L1。
_MAX_RADIUS_NM: int = 1_000_000_000_000_000

#: 共线判据的相对阈值：`|det| <= _COLLINEAR_REL * scale^2` 即认为无唯一外接圆。
_COLLINEAR_REL: float = 1e-9

#: 浮点零阈（单位与量纲无关的小量，仅用于「是否平行 / 判别式是否真负」）。
_EPS: float = 1e-9


# --------------------------------------------------------------------------------------
# 错误与入参把关（错误消息一律 ASCII；中文只出现在 docstring / 注释里）
# --------------------------------------------------------------------------------------


def _err(reason: str, message: str, **details: Any) -> CadError:
    """构造族接口错误：机器可读 `reason` 在第一位，`details` 供判定行归因。"""
    return CadError(message, reason=reason, **details)


def _as_nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米；bool 与非整数浮点一律拒（契约：长度就是整数纳米）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _err("invalid_length", f"{what} must be an integer number of nanometres, got {value!r}", field=what)
    if positive and int(value) <= 0:
        raise _err("invalid_length", f"{what} must be > 0 nm, got {int(value)}", field=what)
    return int(value)


def _as_point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)`；不是长度 2 的序列当场报错。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _err("invalid_point", f"{what} must be a (x_nm, y_nm) pair, got {value!r}", field=what)
    return (_as_nm(value[0], what=f"{what}.x"), _as_nm(value[1], what=f"{what}.y"))


def _as_tol(tol_nm: Any) -> int:
    tol = _as_nm(tol_nm, what="tol_nm")
    if tol < 0:
        raise _err("invalid_length", f"tol_nm must be >= 0, got {tol}", field="tol_nm")
    return tol


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米（四舍五入、远离零；不用 `round()` 以避开银行家舍入的不确定口径）。"""
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def _get(node: Any, name: str, default: Any = None) -> Any:
    """映射/对象两态读字段（L1 草图两种承载形态都吃）。"""
    if isinstance(node, Mapping):
        return node.get(name, default)
    return getattr(node, name, default)


# --------------------------------------------------------------------------------------
# 容器与实体（草图容器 / L1 文档载荷）
# --------------------------------------------------------------------------------------


def _sketch_of(sk_or_doc: Any, *, sketch_uuid: Any = None) -> Any:
    """解析目标草图：草图容器原样返回；含 `sketches` 的文档载荷按 `sketch_uuid` 选草。"""
    payload = sk_or_doc.to_payload() if hasattr(sk_or_doc, "to_payload") else sk_or_doc
    if isinstance(payload, Mapping) and "sketches" in payload:
        sketches = list(payload.get("sketches") or [])
        if sketch_uuid is not None:
            for item in sketches:
                if str(_get(item, "uuid", "")) == str(sketch_uuid):
                    return item
            raise _err("unknown_sketch", f"no sketch with uuid {sketch_uuid!r} in this document",
                       sketch_uuid=str(sketch_uuid))
        if len(sketches) == 1:
            return sketches[0]
        if not sketches:
            raise _err("empty_document", "document has no sketches to draw into")
        raise _err("ambiguous_sketch", f"document has {len(sketches)} sketches; pass sketch_uuid=",
                   sketches=len(sketches))
    return sk_or_doc


def _entities_of(sk: Any) -> list[Any]:
    """草图实体表（唯一读法来自 `sketch/constraints.sketch_entities`），上游异常翻译成 CadError。"""
    try:
        return _sketch_entities(sk)
    except CadError:
        raise
    except Exception as exc:  # 上游另立的异常类型
        raise _err("invalid_container", f"not a sketch container: {exc}") from exc


def _reference_of(sk: Any, ref: Any) -> Any:
    """按 uuid 取实体（禁下标引用）；找不到就是 `unknown_reference`，不静默返回空。"""
    if not isinstance(ref, str) or not ref:
        raise _err("invalid_reference", f"reference must be a non-empty entity uuid, got {ref!r}")
    for entity in _entities_of(sk):
        if _get(entity, "uuid", None) == ref:
            return entity
    raise _err("unknown_reference", f"no entity with uuid {ref!r} in this sketch", reference=ref)


def _delegate(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """转发给上游实现，把任何非 CadError 异常翻译成 CadError 并保留上游 reason。"""
    try:
        return call(*args, **kwargs)
    except CadError:
        raise
    except Exception as exc:
        reason = getattr(exc, "reason", None) or getattr(exc, "key", None) or "upstream_error"
        raise _err(str(reason), f"upstream rejected the call: {exc}") from exc


def _new_circle(sk: Any, center: tuple[int, int], radius_nm: int, layer: Any) -> list[str]:
    """新建一个整圆实体并返回 `[uuid]`（L1 写入的唯一真源仍是 `commands.add_circle`）。"""
    uuid_ = _delegate(_commands.add_circle, sk,
                      center=(int(center[0]), int(center[1])), radius_nm=int(radius_nm), layer=layer)
    return [str(uuid_)]


# --------------------------------------------------------------------------------------
# 几何内核（全部在纳米尺度算，最后一步才取整）
# --------------------------------------------------------------------------------------


def _line_params(geom: Mapping[str, Any], ref: str) -> tuple[float, float, float, float]:
    """线段 → `(x1, y1, ux, uy)`（过 (x1,y1) 的单位方向向量；切距按无限长直线算）。"""
    try:
        x1 = float(geom["x1"])
        y1 = float(geom["y1"])
        x2 = float(geom["x2"])
        y2 = float(geom["y2"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _err("invalid_reference", f"line {ref!r} geom is not (x1,y1,x2,y2): {exc}", reference=ref) from exc
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length <= 0.0:
        raise _err("degenerate_reference", f"line {ref!r} has zero length", reference=ref)
    return (x1, y1, dx / length, dy / length)


def _circle_params(geom: Mapping[str, Any], ref: str) -> tuple[float, float, float]:
    """圆/弧 → `(cx, cy, r)`（`arc` 只取其整圆，扫掠范围不参与相切求解）。"""
    try:
        cx = float(geom["cx"])
        cy = float(geom["cy"])
        r = float(geom["r"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _err("invalid_reference", f"circle/arc {ref!r} geom is not (cx,cy,r): {exc}", reference=ref) from exc
    if not all(math.isfinite(v) for v in (cx, cy, r)) or r <= 0.0:
        raise _err("degenerate_reference", f"circle/arc {ref!r} has a non-usable radius", reference=ref, r=r)
    return (cx, cy, r)


def _parse_reference(entity: Any, ref: str) -> tuple[str, tuple[float, ...]]:
    kind = str(_get(entity, "kind", ""))
    geom = _get(entity, "geom", None)
    if not isinstance(geom, Mapping):
        raise _err("invalid_reference", f"reference {ref!r} has no geom mapping", reference=ref)
    if kind == "line":
        return ("line", _line_params(geom, ref))
    if kind in ("circle", "arc"):
        return ("circle", _circle_params(geom, ref))
    raise _err("unsupported_reference_kind",
               f"reference {ref!r} has kind {kind!r}; only line/circle/arc can be tangent targets",
               reference=ref, kind=kind)


def _cand_line_line(l1: tuple[float, float, float, float], l2: tuple[float, float, float, float],
                    r: float) -> list[tuple[float, float]]:
    """线-线：两条 ±r 偏移线的 4 个交点（平行 ⇒ `degenerate_configuration`）。"""
    x1, y1, ux1, uy1 = l1
    x2, y2, ux2, uy2 = l2
    den = ux1 * uy2 - uy1 * ux2
    if abs(den) <= _EPS:
        raise _err("degenerate_configuration",
                   "the two references are parallel: their offsets do not meet in a unique centre")
    out: list[tuple[float, float]] = []
    for s1 in (1.0, -1.0):
        ax = x1 + s1 * r * (-uy1)
        ay = y1 + s1 * r * ux1
        for s2 in (1.0, -1.0):
            bx = x2 + s2 * r * (-uy2)
            by = y2 + s2 * r * ux2
            t = ((bx - ax) * uy2 - (by - ay) * ux2) / den
            out.append((ax + t * ux1, ay + t * uy1))
    return out


def _cand_line_circle(line: tuple[float, float, float, float], circ: tuple[float, float, float],
                      r: float) -> list[tuple[float, float]]:
    """线-圆：偏移线与「距圆心 `rc+r`（外切）/`|rc-r|`（内切）」圆的交点（一元二次）。"""
    x1, y1, ux, uy = line
    cx, cy, rc = circ
    out: list[tuple[float, float]] = []
    for s in (1.0, -1.0):
        ax = x1 + s * r * (-uy)
        ay = y1 + s * r * ux
        fx = ax - cx
        fy = ay - cy
        for d in (rc + r, abs(rc - r)):
            if d <= _EPS:
                continue
            b = 2.0 * (fx * ux + fy * uy)
            c = fx * fx + fy * fy - d * d
            disc = b * b - 4.0 * c
            if disc < -_EPS * max(1.0, abs(b * b)):
                continue
            root = math.sqrt(max(disc, 0.0))
            for t in ((-b + root) / 2.0, (-b - root) / 2.0):
                out.append((ax + t * ux, ay + t * uy))
    return out


def _cand_circle_circle(c1: tuple[float, float, float], c2: tuple[float, float, float],
                        r: float) -> list[tuple[float, float]]:
    """圆-圆：`(r1±r)` 与 `(r2±r)` 组合的两圆交点（三角形不等式不成立即无解）。"""
    x1, y1, r1 = c1
    x2, y2, r2 = c2
    dx = x2 - x1
    dy = y2 - y1
    dist = math.hypot(dx, dy)
    if dist <= _EPS:
        return []
    ux = dx / dist
    uy = dy / dist
    out: list[tuple[float, float]] = []
    for d1 in (r1 + r, abs(r1 - r)):
        for d2 in (r2 + r, abs(r2 - r)):
            if d1 <= _EPS or d2 <= _EPS:
                continue
            if dist > d1 + d2 + _EPS or dist < abs(d1 - d2) - _EPS:
                continue
            a = (d1 * d1 - d2 * d2 + dist * dist) / (2.0 * dist)
            h = math.sqrt(max(d1 * d1 - a * a, 0.0))
            bx = x1 + a * ux
            by = y1 + a * uy
            for s in (1.0, -1.0):
                out.append((bx - s * h * uy, by + s * h * ux))
    return out


def _residual(ref: tuple[str, tuple[float, ...]], x: float, y: float, r: float) -> float:
    """候选圆心到该参考对象的「切距残差」= |实际距离 - 应有距离|（纳米，越小越真）。"""
    kind, params = ref
    if kind == "line":
        x1, y1, ux, uy = params
        return abs(abs(-(x - x1) * uy + (y - y1) * ux) - r)
    cx, cy, rc = params
    dist = math.hypot(x - cx, y - cy)
    return min(abs(dist - (rc + r)), abs(dist - abs(rc - r)))


def _pick_point(*, near_nm: Any, pick1_nm: Any, pick2_nm: Any) -> tuple[int, int] | None:
    """拾取点口径：优先 `near_nm`；否则取 `pick1_nm`/`pick2_nm`（两个都给取中点）。"""
    if near_nm is not None:
        return _as_point(near_nm, what="near_nm")
    first = None if pick1_nm is None else _as_point(pick1_nm, what="pick1_nm")
    second = None if pick2_nm is None else _as_point(pick2_nm, what="pick2_nm")
    if first is not None and second is not None:
        return (_round_nm((first[0] + second[0]) / 2.0), _round_nm((first[1] + second[1]) / 2.0))
    return first if first is not None else second


# --------------------------------------------------------------------------------------
# 操作（族接口）
# --------------------------------------------------------------------------------------


def circle_2p(sk_or_doc: Any, *, p1: Any, p2: Any, layer: Any = "0", sketch_uuid: Any = None) -> list[str]:
    """**两点（2P）画圆**：`p1`/`p2` 是直径两端 ⇒ 圆心 = 中点、半径 = 半距。返回 `[新建圆 uuid]`。

    取整口径：圆心与半径各自四舍五入到整数纳米，故圆过两端点的残差 ≤ 1 nm（半径取整的半个 ulp
    加中点取整的半个 ulp），这是 L1「整数纳米」存储的必然结果，写在这里以免下游拿浮点尺子量出假红。
    两端点重合 ⇒ `CadError(reason="degenerate_geometry")`。
    """
    a = _as_point(p1, what="p1")
    b = _as_point(p2, what="p2")
    if a == b:
        raise _err("degenerate_geometry", "two-point circle needs two distinct endpoints", p1=a, p2=b)
    cx = _round_nm((a[0] + b[0]) / 2.0)
    cy = _round_nm((a[1] + b[1]) / 2.0)
    radius = _round_nm(math.hypot(b[0] - a[0], b[1] - a[1]) / 2.0)
    if radius <= 0:
        raise _err("degenerate_geometry", "two-point circle would have zero radius", p1=a, p2=b)
    sketch = _sketch_of(sk_or_doc, sketch_uuid=sketch_uuid)
    return _new_circle(sketch, (cx, cy), radius, layer)


def circle_3p(sk_or_doc: Any, *, p1: Any, p2: Any, p3: Any, layer: Any = "0",
              tol_nm: Any = _TOL_NM, sketch_uuid: Any = None) -> list[str]:
    """**三点（3P）画圆**：三点外接圆，返回 `[新建圆 uuid]`。

    圆心用外接圆行列式解（`det = 2*|...|`），共线/重合（`|det| <= 1e-9 * scale^2`）或半径 > 1e15 nm
    ⇒ `CadError(reason="degenerate_geometry")`。取整到整数纳米后**复算**三点等距残差，超 `tol_nm`
    ⇒ `CadError(reason="rounding_error")`（宁可红也不写一个过不了点的圆）。
    """
    a = _as_point(p1, what="p1")
    b = _as_point(p2, what="p2")
    c = _as_point(p3, what="p3")
    tol = _as_tol(tol_nm)
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    cx_, cy_ = float(c[0]), float(c[1])
    det = 2.0 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    scale = max(math.hypot(bx - ax, by - ay), math.hypot(cx_ - ax, cy_ - ay), math.hypot(cx_ - bx, cy_ - by))
    if scale <= 0.0 or abs(det) <= _COLLINEAR_REL * scale * scale:
        raise _err("degenerate_geometry",
                   "the three points are collinear or coincident: no unique circumcircle", det=det)
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx_ * cx_ + cy_ * cy_
    ux = (a2 * (by - cy_) + b2 * (cy_ - ay) + c2 * (ay - by)) / det
    uy = (a2 * (cx_ - bx) + b2 * (ax - cx_) + c2 * (bx - ax)) / det
    radius_f = math.hypot(ax - ux, ay - uy)
    if not math.isfinite(radius_f) or radius_f <= 0.0 or radius_f > float(_MAX_RADIUS_NM):
        raise _err("degenerate_geometry", "circumradius is not usable for the L1 model", radius_nm=radius_f)
    ccx = _round_nm(ux)
    ccy = _round_nm(uy)
    radius = _round_nm(radius_f)
    if radius <= 0:
        raise _err("degenerate_geometry", "circumcircle would have zero radius after rounding")
    worst = max(abs(math.hypot(px - ccx, py - ccy) - radius)
                for px, py in ((a[0], a[1]), (b[0], b[1]), (c[0], c[1])))
    if worst > float(tol):
        raise _err("rounding_error", "rounded circumcircle misses a defining point beyond tol_nm",
                   residual_nm=worst, tol_nm=tol)
    sketch = _sketch_of(sk_or_doc, sketch_uuid=sketch_uuid)
    return _new_circle(sketch, (ccx, ccy), radius, layer)


def circle_ttr(sk_or_doc: Any, *, ref1: Any, ref2: Any, radius_nm: Any, near_nm: Any = None,
               pick1_nm: Any = None, pick2_nm: Any = None, layer: Any = "0",
               tol_nm: Any = _TOL_NM, sketch_uuid: Any = None) -> list[str]:
    """**TTR 画圆**：与 `ref1`/`ref2` 两个既有对象相切、半径 `radius_nm`，返回 `[新建圆 uuid]`。

    多解选择：候选解按两处切距残差过滤后，取**离拾取点最近**的那个 —— 与 AutoCAD TTR 的
    「你点在哪一侧就出哪个解」同口径。拾取点给 `near_nm`，或给 `pick1_nm`/`pick2_nm`（两个都给取中点）；
    一个都不给而候选多于 1 个 ⇒ `ambiguous_solution`（不猜）。无实数解 / 取整后残差超 `tol_nm`
    ⇒ `no_solution`；两参考平行 ⇒ `degenerate_configuration`；参考不是 line/circle/arc
    ⇒ `unsupported_reference_kind`。被切对象**不被改写**（TTR 不修剪），只新增一个圆。
    """
    r = _as_nm(radius_nm, what="radius_nm", positive=True)
    tol = _as_tol(tol_nm)
    near = _pick_point(near_nm=near_nm, pick1_nm=pick1_nm, pick2_nm=pick2_nm)
    sketch = _sketch_of(sk_or_doc, sketch_uuid=sketch_uuid)
    g1 = _parse_reference(_reference_of(sketch, ref1), ref1)
    g2 = _parse_reference(_reference_of(sketch, ref2), ref2)
    rf = float(r)
    kind1, p1_ = g1
    kind2, p2_ = g2
    if kind1 == "line" and kind2 == "line":
        raw = _cand_line_line(p1_, p2_, rf)
    elif kind1 == "line" and kind2 == "circle":
        raw = _cand_line_circle(p1_, p2_, rf)
    elif kind1 == "circle" and kind2 == "line":
        raw = _cand_line_circle(p2_, p1_, rf)
    else:
        raw = _cand_circle_circle(p1_, p2_, rf)
    slack = float(tol) + 1e-6
    best: dict[tuple[int, int], tuple[float, float, float]] = {}
    for x, y in raw:
        res = max(_residual(g1, x, y, rf), _residual(g2, x, y, rf))
        if res > slack:
            continue
        key = (_round_nm(x), _round_nm(y))
        if key not in best or res < best[key][0]:
            best[key] = (res, x, y)
    if not best:
        raise _err("no_solution",
                   "no circle of the given radius is tangent to both references", candidates=len(raw))
    pool = sorted(best.values(), key=lambda item: (item[0], item[1], item[2]))
    if near is None:
        if len(pool) != 1:
            raise _err("ambiguous_solution",
                       f"{len(pool)} tangent circles match; pass near_nm= to pick a side", candidates=len(pool))
        chosen = pool[0]
    else:
        chosen = min(pool, key=lambda item: (math.hypot(item[1] - near[0], item[2] - near[1]), item[0]))
    ccx = _round_nm(chosen[1])
    ccy = _round_nm(chosen[2])
    final = max(_residual(g1, float(ccx), float(ccy), rf), _residual(g2, float(ccx), float(ccy), rf))
    if final > float(tol):
        raise _err("no_solution", "rounded centre leaves a tangency residual over tol_nm",
                   residual_nm=final, tol_nm=tol)
    return _new_circle(sketch, (ccx, ccy), r, layer)


#: 族接口注册表（冻结：名 → 可调用；每个 op 返回 uuid 列表）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "circle_2p": circle_2p,
    "circle_3p": circle_3p,
    "circle_ttr": circle_ttr,
}


# --------------------------------------------------------------------------------------
# 自测（硬断言：全部从实体表读回 geom 复算，不看内存中间量）
# --------------------------------------------------------------------------------------


def _new_sketch(uuid_: str = "s1") -> dict[str, Any]:
    return {"uuid": uuid_, "entities": []}


def _entity_geom(sk: Any, uuid_: str) -> tuple[str, dict[str, Any]]:
    for entity in _sketch_entities(sk):
        if _get(entity, "uuid", None) == uuid_:
            return (str(_get(entity, "kind", "")), dict(_get(entity, "geom", {}) or {}))
    raise AssertionError("fixture broken: entity %s not in sketch" % uuid_)


def _reason_of(fn: Callable[..., Any], /, **kwargs: Any) -> str | None:
    """调用 fn 并返回 CadError.reason；没报错返回 None（阴性对照的尺子）。"""
    try:
        fn(**kwargs)
    except CadError as exc:
        return exc.reason
    except Exception as exc:  # 夹具自证：漏出来的非 CadError 也要看得见
        return "non_cad_error:" + type(exc).__name__
    return None


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), str(detail)))

    # -- 1) 2P：两点定直径（精确值）+ 重合点阴性 ----------------------------------------
    try:
        sk = _new_sketch()
        uuids = circle_2p(sk, p1=(-10_000_000, 0), p2=(10_000_000, 0))
        kind, geom = _entity_geom(sk, uuids[0])
        exact = (geom.get("cx"), geom.get("cy"), geom.get("r")) == (0, 0, 10_000_000)
        dup = _reason_of(circle_2p, sk_or_doc=sk, p1=(7, 8), p2=(7, 8))
        record("circle_2p", kind == "circle" and exact and len(uuids) == 1 and dup == "degenerate_geometry",
               "kind=%s geom=%s dup_reason=%s" % (kind, geom, dup))
    except Exception as exc:
        record("circle_2p", False, "raised %s: %s" % (type(exc).__name__, exc))

    # -- 2) 3P：外接圆过三点（读回 geom 复算等距）+ 共线阴性 ---------------------------
    try:
        sk = _new_sketch()
        pts = ((0, 0), (10_000_000, 0), (0, 10_000_000))
        uuids = circle_3p(sk, p1=pts[0], p2=pts[1], p3=pts[2])
        kind, geom = _entity_geom(sk, uuids[0])
        ccx, ccy, rad = geom["cx"], geom["cy"], geom["r"]
        near_centre = abs(ccx - 5_000_000) <= 50 and abs(ccy - 5_000_000) <= 50
        equidistant = all(abs(math.hypot(px - ccx, py - ccy) - rad) <= 50 for px, py in pts)
        collinear = _reason_of(circle_3p, sk_or_doc=sk, p1=(0, 0), p2=(1_000_000, 1_000_000),
                               p3=(2_000_000, 2_000_000))
        record("circle_3p", kind == "circle" and near_centre and equidistant and collinear == "degenerate_geometry",
               "centre=(%s,%s) r=%s equidistant=%s collinear_reason=%s" % (ccx, ccy, rad, equidistant, collinear))
    except Exception as exc:
        record("circle_3p", False, "raised %s: %s" % (type(exc).__name__, exc))

    # -- 3) TTR 线-线：圆心 (5mm,5mm)、两处切距逐纳米等于 r + 平行两线阴性 --------------
    try:
        sk = _new_sketch()
        vertical = _commands.add_line(sk, p1=(0, 0), p2=(0, 100_000_000))
        horizontal = _commands.add_line(sk, p1=(0, 0), p2=(100_000_000, 0))
        uuids = circle_ttr(sk, ref1=vertical, ref2=horizontal, radius_nm=5_000_000,
                           near_nm=(10_000_000, 10_000_000))
        kind, geom = _entity_geom(sk, uuids[0])
        exact = (geom.get("cx"), geom.get("cy"), geom.get("r")) == (5_000_000, 5_000_000, 5_000_000)
        dist_x = abs(geom["cx"] - 0)   # 到竖线 x=0
        dist_y = abs(geom["cy"] - 0)   # 到横线 y=0
        tangent = dist_x == geom["r"] and dist_y == geom["r"]
        par1 = _commands.add_line(sk, p1=(0, 0), p2=(0, 100_000_000))
        par2 = _commands.add_line(sk, p1=(10_000_000, 0), p2=(10_000_000, 100_000_000))
        parallel = _reason_of(circle_ttr, sk_or_doc=sk, ref1=par1, ref2=par2, radius_nm=5_000_000,
                              near_nm=(5_000_000, 5_000_000))
        record("circle_ttr_line_line", kind == "circle" and exact and tangent
               and parallel == "degenerate_configuration" and len(sk["entities"]) == 5,
               "geom=%s tangent=%s parallel_reason=%s entities=%d" % (geom, tangent, parallel, len(sk["entities"])))
    except Exception as exc:
        record("circle_ttr_line_line", False, "raised %s: %s" % (type(exc).__name__, exc))

    # -- 4) TTR 线-圆：外切解（复算到圆心距离 = rc+r）+ 无解阴性 ------------------------
    try:
        sk = _new_sketch()
        line = _commands.add_line(sk, p1=(0, 0), p2=(100_000_000, 0))
        circle_ref = _commands.add_circle(sk, center=(0, 4_000_000), radius_nm=2_000_000)
        uuids = circle_ttr(sk, ref1=line, ref2=circle_ref, radius_nm=3_000_000,
                           near_nm=(4_900_000, 3_000_000), tol_nm=200)
        kind, geom = _entity_geom(sk, uuids[0])
        on_y = geom.get("cy") == 3_000_000            # 距直线 y=0 恰好 r=3mm
        reach = abs(math.hypot(geom["cx"], geom["cy"] - 4_000_000) - 5_000_000) <= 200
        far = _commands.add_circle(sk, center=(0, 10_000_000), radius_nm=2_000_000)
        miss = _reason_of(circle_ttr, sk_or_doc=sk, ref1=line, ref2=far, radius_nm=3_000_000,
                          near_nm=(0, 3_000_000))
        record("circle_ttr_line_circle", kind == "circle" and on_y and reach and miss == "no_solution",
               "geom=%s on_line=%s centre_distance_ok=%s miss_reason=%s" % (geom, on_y, reach, miss))
    except Exception as exc:
        record("circle_ttr_line_circle", False, "raised %s: %s" % (type(exc).__name__, exc))

    # -- 5) 入参把关：非整数纳米 / bool / 未知引用 一律 CadError 且带正确 reason ---------
    try:
        sk = _new_sketch()
        unknown = _reason_of(circle_ttr, sk_or_doc=sk, ref1="nope", ref2="nope", radius_nm=1_000_000)
        float_pt = _reason_of(circle_2p, sk_or_doc=sk, p1=(0.5, 0), p2=(1, 0))
        bool_tol = _reason_of(circle_3p, sk_or_doc=sk, p1=(0, 0), p2=(1_000_000, 0), p3=(0, 1_000_000),
                              tol_nm=True)
        bad_r = _reason_of(circle_ttr, sk_or_doc=sk, ref1="x", ref2="y", radius_nm=-5)
        record("argument_guards", unknown == "unknown_reference" and float_pt == "invalid_length"
               and bool_tol == "invalid_length" and bad_r == "invalid_length",
               "unknown=%s float_point=%s bool_tol=%s negative_radius=%s"
               % (unknown, float_pt, bool_tol, bad_r))
    except Exception as exc:
        record("argument_guards", False, "raised %s: %s" % (type(exc).__name__, exc))

    # -- 6) 容器：L1 文档载荷按 sketch_uuid 落草；多草图不给 uuid ⇒ ambiguous_sketch ----
    try:
        s1 = _new_sketch("s1")
        s2 = _new_sketch("s2")
        doc = {"sketches": [s1, s2]}
        ambiguous = _reason_of(circle_2p, sk_or_doc=doc, p1=(0, 0), p2=(2_000_000, 0))
        uuids = circle_2p(doc, p1=(0, 0), p2=(2_000_000, 0), sketch_uuid="s2")
        landed = (len(s2["entities"]) == 1 and not s1["entities"]
                  and uuids[0] == s2["entities"][0]["uuid"])
        record("document_container", ambiguous == "ambiguous_sketch" and landed,
               "ambiguous_reason=%s landed_in_s2=%s uuid=%s" % (ambiguous, landed, uuids[0]))
    except Exception as exc:
        record("document_container", False, "raised %s: %s" % (type(exc).__name__, exc))

    for name, passed, detail in checks:
        print("DRAW_OPS_W26_CHECK name=%s status=%s detail=%s"
              % (name, "PASS" if passed else "FAIL", detail))
    ok = bool(checks) and all(passed for _, passed, _ in checks)
    print("DRAW_OPS_W26 ops=%d checks=%d checks_ok=%d" % (len(OPERATIONS), len(checks),
                                                          sum(1 for _, p, _ in checks if p)))
    print("FEATURE_OK family=draw_ops op=circle_ttr verdict=%s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
