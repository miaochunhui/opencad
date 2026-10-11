#!/usr/bin/env python3
"""cadcore.ops.features.edit_ops__w9 —— edit_ops 族 W9 片：对象选择（窗选 / 交叉窗选 / 栏选 / 加减选 / 单击拾取）。

用途
----
把 ``docs/research/cad-parity/features/autocad-edit.json`` 的 must_have **第 0 号**
「对象选择（窗选/交叉窗选/栏选/加减选）」在 L1 文档模型上做成**真几何**实现：精确整数判定、
整数纳米、错误一律抛 ``cadcore.errors.CadError``。

清单来源（写清楚，别让下游去猜）
--------------------------------
- 冻结接口点名读 ``features/edit_ops.json``，**该文件不存在**；同族实际清单件是
  ``features/autocad-edit.json``（``id="autocad-edit"``，本片 ``FAMILY="edit_ops"`` 与之同族）。
  本片按它的 ``must_have`` 顺序编号 0..20，逐条比对 ``status/autocad-edit.json``：
  仍为 missing/partial 的有 19 条（0..17、19；18「放弃与重做」implemented，20「对齐」在
  status 里键名是「对齐」故按 missing 计），故第 0 号 = 「对象选择」。
- 第 0 号在 status 里的 note 指的是 **GUI 不可达**（app.js 只有单击单选、无橡皮筋框选）；
  本片只负责**库级 / L1 数据面**，不碰 apps/opencad（不在本席写面内）。

落到哪个容器上（``sk_or_doc`` 解析口径，与本族既有模块同一套）
------------------------------------------------------------
1. **L1 草图**：映射 ``{"entities": [...], ...}`` 或同形对象 —— 直接用；
2. **L1 文档**：``cadcore.docmodel.Document`` 或同形对象/映射（带 ``sketches``）⇒ 取 ``sketches[0]``；
   ``sketches`` 为空 ⇒ ``CadError(reason="no_target_sketch")``，**不静默造空草图**。

几何口径（真几何，不是采样近似）
--------------------------------
- ``line``：``geom={x1,y1,x2,y2}``；``polyline``：``geom={points:[[x,y],...]}``（≥2 点，否则报错）；
  ``circle``：``geom={cx,cy,r}``；``arc``：``geom={cx,cy,r,start_deg,end_deg}``。
  兼容键面（只在主键缺席时启用，避免"猜第三种"）：圆心 ``cx/cy`` → ``x/y``；半径 ``r`` → ``radius``/``radius_nm``；
  角度 ``start_deg/end_deg`` → ``a1_deg/a2_deg`` → ``start/end``；折线点 ``points`` → ``pts``/``vertices``。
- **弧的扫掠方向**：从 ``start_deg`` **逆时针**扫到 ``end_deg``（DXF 口径）；
  ``(end-start) mod 360`` 落在 ``1e-9`` 度内视为**整圆**（sweep=360）。
- ``select_window``（窗选）= **完全包含**：判据是实体的**精确包围盒**落在矩形内。
  圆弧包围盒按「两端点 + 落在扫掠内的象限点」精确算（这是弧的真实极值，不是采样）。
- ``select_crossing``（交叉窗选）= **曲线 ∩ 闭矩形 ≠ ∅**：
  - ``line``/``polyline``：整数叉积的精确线段-线段/线段-矩形判定，零浮点；
  - ``circle``：``min_dist(C,rect) ≤ r ≤ max_dist(C,rect)``（两者都用**整数平方**比较，精确；
    矩形上的距离函数连续且凸集上的极值在角点 ⇒ 该判据是充要的，不是近似）；
  - ``arc``：先判端点是否在矩形内（覆盖"整弧落在矩形内"），再逐条矩形边做
    **边 ∩ 圆** 求交（判别式是整数，精确）并检查交点角度落在扫掠内。凸区域 + 连通曲线 ⇒
    「有端点在区域内」或「与边界相交」二者必居其一，故该判据完备。
    角度用 ``atan2`` + ``1e-9`` 度容差（纳米尺度上 1e-9 度 ≈ 亚纳米，见下方"写明的近似"）。
- ``select_fence``（栏选）= 曲线 ∩ 围栏折线 ≠ ∅：按围栏逐段做精确判定；
  圆/弧用「点到线段的最近距离 ≤ r ≤ 到两端点的最远距离」，最近距离走 ``Fraction`` **精确有理数**。
- ``pick``（单击拾取）= 点到实体曲线的距离 ≤ 容差：线段走精确有理数；圆走
  ``|d - r| ≤ tol`` 的**平方区间精确判定**；弧先看拾取方向的角是否落在扫掠内，不在则退化为到两端点的距离。
  排序键在圆/弧分支上是浮点（只影响等距并列时的先后），判定本身是精确的。
- 所有长度入参必须是**整数纳米**（``bool`` 与浮点一律报错，不四舍五入）；出参一律 ``list[str]``（uuid），
  **按 entities 文档顺序**；``[]`` 一律是**真为零**（真的没有匹配实体），不是"没算"。

有意写明的近似（不是"没做"）
----------------------------
1. 弧极值点 / 端点坐标由 ``cx + round(r*cos θ)`` 得到（半纳米进位对称取整），量级 ≤ 0.5 nm；
2. 弧的角度比较容差 ``1e-9`` 度（``_ANG_EPS_DEG``），线段的圆交点参数容差 ``1e-12``（``_PARAM_EPS``）；
3. 本片不认识的 ``kind``（如 ``spline``）在窗选/交叉/栏选/拾取里**当场报 ``unsupported_geometry``**，
   **不静默跳过**（跳过会把"没算的实体"伪装成"没选中"）；``select_all`` 不读几何，故任何 kind 都能列出来。

有意不做的边界
--------------
- 不做栅格化 / 像素容差（那属于 GUI 视口，本片只吃纳米）；
- 不做"选择集写入 L1"：L1 是**唯一权威**且选择是会话态，写进去会污染真源 ⇒ 选择集由调用方持有
  （``selection_update`` 的入参 ``selection`` + 返回值就是那份状态）。所有选择操作**不改文档一个字节**。

自检
----
``python3 cadcore/ops/features/edit_ops__w9.py`` ⇒ 末行判定行
``FEATURE_OK family=edit_ops op=select_window verdict=PASS``。
"""

from __future__ import annotations

import copy
import math
import os
import sys
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
#: 包根（``.../packages/cadcore``）：保证「直接跑脚本」时 ``import cadcore.*`` 也能解析。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if os.path.isdir(os.path.join(_ROOT, "cadcore")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cadcore.errors import CadError  # noqa: E402  （与冻结接口 cadcore.errors.CadError 同一份定义）

__all__ = [
    "FAMILY", "OPERATIONS", "SUPPORTED_KINDS", "SELECTION_MODES", "PRIMARY_OP",
    "select_all", "select_window", "select_crossing", "select_fence", "selection_update", "pick",
]

#: 冻结接口①：族名。
FAMILY = "edit_ops"

#: 本片实现的实体面（几何判定只对这四种成立；别的 kind 显式报错）。
SUPPORTED_KINDS: tuple[str, ...] = ("line", "circle", "arc", "polyline")

#: 加减选的三种语义（别的值报 invalid_argument，不静默当 add）。
SELECTION_MODES: tuple[str, ...] = ("add", "remove", "toggle")

#: 本片主操作（must_have 第 0 号「窗选」的正面实现）——判定行里的 op 名。
PRIMARY_OP = "select_window"

_ANG_EPS_DEG = 1e-9
_PARAM_EPS = 1e-12


# --------------------------------------------------------------------------------------
# 失败与入参
# --------------------------------------------------------------------------------------


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """机器可读失败：``reason`` 稳定，细节进 ``details``（调用方不解析文案）。"""
    return CadError(detail, reason=reason, **details)


def _as_nm(value: Any, *, what: str) -> int:
    """整数纳米。``bool`` 不是长度；浮点一律拒绝（不四舍五入、不静默截断）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "%s must be an integer number of nanometres" % what,
                    got=repr(value))
    return int(value)


def _as_point(value: Any, *, what: str) -> tuple[int, int]:
    if value is None:
        raise _fail("invalid_argument", "%s is required" % what)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_argument", "%s must be a pair of integer nanometres" % what,
                    got=repr(value))
    return (_as_nm(value[0], what="%s[0]" % what), _as_nm(value[1], what="%s[1]" % what))


def _as_points(value: Any, *, what: str, minimum: int) -> list[tuple[int, int]]:
    if value is None:
        raise _fail("invalid_argument", "%s is required" % what)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_argument", "%s must be a sequence of points" % what, got=repr(value))
    points = [_as_point(item, what="%s[%d]" % (what, idx)) for idx, item in enumerate(value)]
    if len(points) < minimum:
        raise _fail("invalid_argument", "%s needs at least %d points" % (what, minimum),
                    got=len(points))
    return points


def _as_angle(value: Any, *, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_argument", "%s must be a finite angle in degrees" % what, got=repr(value))
    out = float(value)
    if not math.isfinite(out):
        raise _fail("invalid_argument", "%s must be finite" % what, got=repr(value))
    return out


def _as_rect(p1_nm: Any, p2_nm: Any) -> tuple[int, int, int, int]:
    """两点 → 规范化矩形；零宽/零高报错（点选不是窗选，不许悄悄当零面积窗口用）。"""
    a = _as_point(p1_nm, what="p1_nm")
    b = _as_point(p2_nm, what="p2_nm")
    rect = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
    if rect[0] == rect[2] or rect[1] == rect[3]:
        raise _fail("invalid_argument", "selection window must have non-zero width and height",
                    p1_nm=list(a), p2_nm=list(b))
    return rect


def _as_uuid_seq(value: Any, *, what: str) -> list[str]:
    """uuid 序列（单个字符串等价于长度 1 的序列，写明的便利，不是静默失败）。"""
    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, (bytes, bytearray)) or not isinstance(value, Sequence):
        raise _fail("invalid_argument", "%s must be a uuid or a sequence of uuids" % what,
                    got=repr(value))
    else:
        items = list(value)
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise _fail("invalid_argument", "%s entries must be non-empty uuid strings" % what,
                        got=repr(item))
        out.append(item)
    return out


def _filter_kinds(kinds: Any) -> tuple[str, ...] | None:
    if kinds is None:
        return None
    if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence):
        raise _fail("invalid_argument", "kinds must be a sequence of kind names or None",
                    got=repr(kinds))
    out: list[str] = []
    for kind in kinds:
        if kind not in SUPPORTED_KINDS:
            raise _fail("invalid_argument", "unknown kind filter", got=repr(kind),
                        supported=list(SUPPORTED_KINDS))
        out.append(str(kind))
    return tuple(out)


def _filter_layer(layer: Any) -> str | None:
    if layer is None:
        return None
    if not isinstance(layer, str) or not layer:
        raise _fail("invalid_argument", "layer must be a non-empty string or None", got=repr(layer))
    return layer


def _filter_construction(construction: Any) -> bool | None:
    if construction is None:
        return None
    if not isinstance(construction, bool):
        raise _fail("invalid_argument", "construction must be a bool or None", got=repr(construction))
    return bool(construction)


# --------------------------------------------------------------------------------------
# 容器与实体解析
# --------------------------------------------------------------------------------------


def _field(entity: Any, key: str) -> Any:
    if isinstance(entity, Mapping):
        return entity.get(key)
    return getattr(entity, key, None)


def _is_sketch_like(target: Any) -> bool:
    if isinstance(target, Mapping):
        return "entities" in target
    return hasattr(target, "entities")


def _sketch(target: Any) -> Any:
    """解析编辑面：L1 草图原样返回；文档取 ``sketches[0]``；其余报 ``no_target_sketch``。"""
    if _is_sketch_like(target):
        return target
    sketches = target.get("sketches") if isinstance(target, Mapping) else getattr(target, "sketches", None)
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)) and sketches:
        first = sketches[0]
        if _is_sketch_like(first):
            return first
        raise _fail("no_target_sketch", "sketches[0] is not a sketch-like container",
                    got=type(first).__name__)
    raise _fail("no_target_sketch", "target is neither a sketch nor a document with a sketch",
                got=type(target).__name__)


def _entities_of(sketch: Any) -> list[Any]:
    entities = sketch.get("entities") if isinstance(sketch, Mapping) else getattr(sketch, "entities", None)
    if isinstance(entities, (str, bytes)) or not isinstance(entities, Sequence):
        raise _fail("invalid_entity", "sketch.entities must be a sequence",
                    got=type(entities).__name__)
    return list(entities)


def _raw_uuid(entity: Any) -> str:
    raw = _field(entity, "uuid")
    return raw if isinstance(raw, str) else ""


def _uuid_of(entity: Any) -> str:
    uid = _raw_uuid(entity)
    if not uid:
        raise _fail("invalid_entity", "entity is missing a uuid string", got=type(entity).__name__)
    return uid


def _kind_of(entity: Any) -> str:
    raw = _field(entity, "kind")
    if not isinstance(raw, str) or not raw:
        raw = _field(entity, "type")
    kind = raw.strip() if isinstance(raw, str) else ""
    if kind not in SUPPORTED_KINDS:
        raise _fail("unsupported_geometry", "unsupported entity kind for selection",
                    entity=_raw_uuid(entity), kind=kind, supported=list(SUPPORTED_KINDS))
    return kind


def _geom_of(entity: Any, uid: str) -> Mapping[str, Any]:
    geom = _field(entity, "geom")
    if geom is None:
        raise _fail("unsupported_geometry", "entity has no geom", entity=uid)
    if not isinstance(geom, Mapping):
        raise _fail("invalid_entity", "entity geom must be a mapping", entity=uid,
                    got=type(geom).__name__)
    return geom


def _geom_int(geom: Mapping[str, Any], keys: Sequence[str], *, what: str, uid: str) -> int:
    for key in keys:
        if key in geom:
            return _as_nm(geom[key], what="geom.%s" % what)
    raise _fail("unsupported_geometry", "entity geom is missing a value", entity=uid,
                what=what, keys=list(keys))


def _geom_angle(geom: Mapping[str, Any], keys: Sequence[str], *, what: str, uid: str) -> float:
    for key in keys:
        if key in geom:
            return _as_angle(geom[key], what="geom.%s" % what)
    raise _fail("unsupported_geometry", "entity geom is missing an angle", entity=uid,
                what=what, keys=list(keys))


def _geom_points(geom: Mapping[str, Any], *, uid: str) -> tuple[tuple[int, int], ...]:
    for key in ("points", "pts", "vertices"):
        if key in geom:
            raw = geom[key]
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
                raise _fail("invalid_entity", "polyline points must be a sequence", entity=uid,
                            got=type(raw).__name__)
            points = [_as_point(item, what="geom.%s[%d]" % (key, idx)) for idx, item in enumerate(raw)]
            if len(points) < 2:
                raise _fail("unsupported_geometry", "polyline needs at least 2 points",
                            entity=uid, points=len(points))
            return tuple(points)
    raise _fail("unsupported_geometry", "polyline geom is missing points", entity=uid)


def _shape(entity: Any) -> tuple[str, tuple[Any, ...]]:
    """实体 → 冻结几何元组。不认识的 kind / 缺键 ⇒ 当场报错（不跳过）。"""
    kind = _kind_of(entity)
    uid = _uuid_of(entity)
    geom = _geom_of(entity, uid)
    if kind == "line":
        return ("line", (_geom_int(geom, ("x1",), what="x1", uid=uid),
                         _geom_int(geom, ("y1",), what="y1", uid=uid),
                         _geom_int(geom, ("x2",), what="x2", uid=uid),
                         _geom_int(geom, ("y2",), what="y2", uid=uid)))
    if kind in ("circle", "arc"):
        cx = _geom_int(geom, ("cx", "x"), what="cx", uid=uid)
        cy = _geom_int(geom, ("cy", "y"), what="cy", uid=uid)
        r = _geom_int(geom, ("r", "radius", "radius_nm"), what="r", uid=uid)
        if r <= 0:
            raise _fail("unsupported_geometry", "circle/arc radius must be positive",
                        entity=uid, r=r)
        if kind == "circle":
            return ("circle", (cx, cy, r))
        return ("arc", (cx, cy, r,
                        _geom_angle(geom, ("start_deg", "a1_deg", "start"), what="start_deg", uid=uid),
                        _geom_angle(geom, ("end_deg", "a2_deg", "end"), what="end_deg", uid=uid)))
    return ("polyline", _geom_points(geom, uid=uid))


def _filtered(sketch: Any, *, kinds: Any = None, layer: Any = None,
              construction: Any = None) -> list[Any]:
    """按属性过滤后的实体列表（**文档顺序**）。kind 过滤词先校验：错词报错，不静默返回空集。"""
    want_kinds = _filter_kinds(kinds)
    want_layer = _filter_layer(layer)
    want_construction = _filter_construction(construction)
    out: list[Any] = []
    for entity in _entities_of(sketch):
        if want_layer is not None:
            got_layer = _field(entity, "layer")
            if got_layer != want_layer:
                continue
        if want_construction is not None:
            got_flag = _field(entity, "construction")
            if bool(got_flag) is not want_construction:
                continue
        if want_kinds is not None and _kind_of(entity) not in want_kinds:
            continue
        out.append(entity)
    return out


def _uuids(entities: Sequence[Any]) -> list[str]:
    """uuid 列表（文档顺序）；同一草图里 uuid 重复 ⇒ 报错（顺序引用会指向别的东西）。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for entity in entities:
        uid = _uuid_of(entity)
        if uid in seen:
            raise _fail("invalid_entity", "duplicate entity uuid in sketch",
                        uuid=uid, first_index=seen[uid])
        seen[uid] = len(out)
        out.append(uid)
    return out


# --------------------------------------------------------------------------------------
# 精确几何谓词
# --------------------------------------------------------------------------------------


def _sgn(value: Any) -> int:
    return (value > 0) - (value < 0)


def _orient(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    """叉积符号（整数精确，无浮点噪声）。"""
    return _sgn((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _on_segment(a: tuple[int, int], b: tuple[int, int], p: tuple[int, int]) -> bool:
    if _orient(a, b, p) != 0:
        return False
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _seg_seg_hit(a: tuple[int, int], b: tuple[int, int],
                 c: tuple[int, int], d: tuple[int, int]) -> bool:
    """闭线段相交（含共线重叠、端点相切）—— 全整数精确判定。"""
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    if o1 == 0 and _on_segment(a, b, c):
        return True
    if o2 == 0 and _on_segment(a, b, d):
        return True
    if o3 == 0 and _on_segment(c, d, a):
        return True
    if o4 == 0 and _on_segment(c, d, b):
        return True
    return False


def _rect_edges(rect: tuple[int, int, int, int]) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    x1, y1, x2, y2 = rect
    return (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1)))


def _in_rect(point: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = rect
    return x1 <= point[0] <= x2 and y1 <= point[1] <= y2


def _seg_rect_hit(a: tuple[int, int], b: tuple[int, int],
                  rect: tuple[int, int, int, int]) -> bool:
    """线段 ∩ 闭矩形 ≠ ∅（精确）。"""
    if _in_rect(a, rect) or _in_rect(b, rect):
        return True
    for p, q in _rect_edges(rect):
        if _seg_seg_hit(a, b, p, q):
            return True
    return False


def _round_nm(value: float) -> int:
    """纳米取整：半纳米处对称进位（不用银行家舍入，跨版本可复现）。"""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def _sweep_deg(start_deg: float, end_deg: float) -> float:
    """逆时针扫掠角：``(end-start) mod 360``；落在 ``1e-9`` 度内视为整圆（360）。"""
    sweep = (float(end_deg) - float(start_deg)) % 360.0
    if sweep <= _ANG_EPS_DEG or sweep >= 360.0 - _ANG_EPS_DEG:
        return 360.0
    return sweep


def _angle_in_sweep(deg: float, start_deg: float, end_deg: float) -> bool:
    sweep = _sweep_deg(start_deg, end_deg)
    if sweep >= 360.0 - _ANG_EPS_DEG:
        return True
    delta = (float(deg) - float(start_deg)) % 360.0
    return delta <= sweep + _ANG_EPS_DEG or delta >= 360.0 - _ANG_EPS_DEG


def _point_on_arc(cx: int, cy: int, r: int, deg: float) -> tuple[int, int]:
    rad = math.radians(float(deg))
    return (cx + _round_nm(r * math.cos(rad)), cy + _round_nm(r * math.sin(rad)))


def _arc_extremes(cx: int, cy: int, r: int, start_deg: float,
                  end_deg: float) -> list[tuple[int, int]]:
    """弧的真实极值点集合：两端点 + 落在扫掠内的四个象限点。"""
    points = [_point_on_arc(cx, cy, r, start_deg), _point_on_arc(cx, cy, r, end_deg)]
    for quadrant in (0.0, 90.0, 180.0, 270.0):
        if _angle_in_sweep(quadrant, start_deg, end_deg):
            points.append(_point_on_arc(cx, cy, r, quadrant))
    return points


def _bbox(shape: tuple[str, tuple[Any, ...]]) -> tuple[int, int, int, int]:
    kind, geom = shape
    if kind == "line":
        x1, y1, x2, y2 = geom
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if kind == "polyline":
        xs = [p[0] for p in geom]
        ys = [p[1] for p in geom]
        return (min(xs), min(ys), max(xs), max(ys))
    if kind == "circle":
        cx, cy, r = geom
        return (cx - r, cy - r, cx + r, cy + r)
    cx, cy, r, start_deg, end_deg = geom
    pts = _arc_extremes(cx, cy, r, start_deg, end_deg)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _seg_dist2(point: tuple[int, int], a: tuple[int, int], b: tuple[int, int]) -> Fraction:
    """点到闭线段的距离平方（**精确有理数**，无浮点）。"""
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return Fraction((px - ax) ** 2 + (py - ay) ** 2)
    t = Fraction((px - ax) * dx + (py - ay) * dy, dx * dx + dy * dy)
    if t < 0:
        return Fraction((px - ax) ** 2 + (py - ay) ** 2)
    if t > 1:
        return Fraction((px - bx) ** 2 + (py - by) ** 2)
    fx = Fraction(ax) + t * dx
    fy = Fraction(ay) + t * dy
    return (fx - px) ** 2 + (fy - py) ** 2


def _min_dist2_polyline(point: tuple[int, int], points: Sequence[tuple[int, int]]) -> Fraction:
    best = _seg_dist2(point, points[0], points[1])
    for idx in range(1, len(points) - 1):
        candidate = _seg_dist2(point, points[idx], points[idx + 1])
        if candidate < best:
            best = candidate
    return best


def _circle_rect_hit(cx: int, cy: int, r: int, rect: tuple[int, int, int, int]) -> bool:
    """圆曲线 ∩ 闭矩形 ≠ ∅ ⟺ 最近距离 ≤ r ≤ 最远距离（两端都用整数平方比较，精确）。"""
    x1, y1, x2, y2 = rect
    dx = 0 if x1 <= cx <= x2 else min(abs(cx - x1), abs(cx - x2))
    dy = 0 if y1 <= cy <= y2 else min(abs(cy - y1), abs(cy - y2))
    near2 = dx * dx + dy * dy
    far2 = max((px - cx) ** 2 + (py - cy) ** 2 for px, py in ((x1, y1), (x1, y2), (x2, y1), (x2, y2)))
    return near2 <= r * r <= far2


def _circle_seg_hit(cx: int, cy: int, r: int, a: tuple[int, int], b: tuple[int, int]) -> bool:
    """圆曲线 ∩ 闭线段 ≠ ∅（最近距离精确有理数，最远距离取端点）。"""
    near2 = _seg_dist2((cx, cy), a, b)
    far2 = max((a[0] - cx) ** 2 + (a[1] - cy) ** 2, (b[0] - cx) ** 2 + (b[1] - cy) ** 2)
    return near2 <= r * r <= far2


def _seg_circle_points(cx: int, cy: int, r: int, a: tuple[int, int],
                       b: tuple[int, int]) -> list[tuple[float, float]]:
    """线段 ∩ 圆 的交点（判别式是整数，精确；开根号后才引入浮点）。"""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    aa = dx * dx + dy * dy
    if aa == 0:
        if (ax - cx) ** 2 + (ay - cy) ** 2 == r * r:
            return [(float(ax), float(ay))]
        return []
    bb = 2 * ((ax - cx) * dx + (ay - cy) * dy)
    cc = (ax - cx) ** 2 + (ay - cy) ** 2 - r * r
    disc = bb * bb - 4 * aa * cc
    if disc < 0:
        return []
    root = math.sqrt(disc)
    out: list[tuple[float, float]] = []
    for t in ((-bb - root) / (2 * aa), (-bb + root) / (2 * aa)):
        if -_PARAM_EPS <= t <= 1.0 + _PARAM_EPS:
            t_clamped = min(1.0, max(0.0, t))
            out.append((ax + t_clamped * dx, ay + t_clamped * dy))
    return out


def _arc_seg_hit(cx: int, cy: int, r: int, start_deg: float, end_deg: float,
                 a: tuple[int, int], b: tuple[int, int]) -> bool:
    """弧 ∩ 闭线段 ≠ ∅：先线段 ∩ 圆，再要求交点方向落在扫掠内（否则不算命中）。"""
    for px, py in _seg_circle_points(cx, cy, r, a, b):
        ang = math.degrees(math.atan2(py - cy, px - cx))
        if _angle_in_sweep(ang, start_deg, end_deg):
            return True
    return False


def _shape_rect_hit(shape: tuple[str, tuple[Any, ...]], rect: tuple[int, int, int, int]) -> bool:
    """实体曲线 ∩ 闭矩形 ≠ ∅（交叉窗选口径）。"""
    kind, geom = shape
    if kind == "line":
        x1, y1, x2, y2 = geom
        return _seg_rect_hit((x1, y1), (x2, y2), rect)
    if kind == "polyline":
        for idx in range(len(geom) - 1):
            if _seg_rect_hit(geom[idx], geom[idx + 1], rect):
                return True
        return False
    if kind == "circle":
        cx, cy, r = geom
        return _circle_rect_hit(cx, cy, r, rect)
    cx, cy, r, start_deg, end_deg = geom
    # 端点在矩形内（含整弧落在矩形内）+ 与矩形边界相交 ⇒ 完备（凸区域 + 连通曲线）。
    if _in_rect(_point_on_arc(cx, cy, r, start_deg), rect):
        return True
    if _in_rect(_point_on_arc(cx, cy, r, end_deg), rect):
        return True
    for p, q in _rect_edges(rect):
        if _arc_seg_hit(cx, cy, r, start_deg, end_deg, p, q):
            return True
    return False


def _shape_polyline_hit(shape: tuple[str, tuple[Any, ...]],
                        fence: Sequence[tuple[int, int]]) -> bool:
    """实体曲线 ∩ 围栏折线 ≠ ∅（栏选口径）：逐围栏段判定，任一段命中即命中。"""
    kind, geom = shape
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    if kind == "line":
        x1, y1, x2, y2 = geom
        segments.append(((x1, y1), (x2, y2)))
    elif kind == "polyline":
        segments.extend((geom[idx], geom[idx + 1]) for idx in range(len(geom) - 1))
    elif kind == "circle":
        cx, cy, r = geom
        return any(_circle_seg_hit(cx, cy, r, fence[idx], fence[idx + 1])
                   for idx in range(len(fence) - 1))
    else:
        cx, cy, r, start_deg, end_deg = geom
        return any(_arc_seg_hit(cx, cy, r, start_deg, end_deg, fence[idx], fence[idx + 1])
                   for idx in range(len(fence) - 1))
    for a, b in segments:
        for idx in range(len(fence) - 1):
            if _seg_seg_hit(a, b, fence[idx], fence[idx + 1]):
                return True
    return False


def _shape_pick(shape: tuple[str, tuple[Any, ...]], point: tuple[int, int],
                tolerance_nm: int) -> float | None:
    """点在容差内命中实体曲线 ⇒ 返回可排序的（平方）距离键；未命中 ⇒ ``None``。

    线段类走精确有理数；圆/弧用 ``|d - r| ≤ tol`` 的平方区间精确判定；
    弧额外要求方向角落在扫掠内，否则退化为「到两端点的距离」（不假装整圆）。
    """
    kind, geom = shape
    tol2 = tolerance_nm * tolerance_nm
    if kind == "line":
        x1, y1, x2, y2 = geom
        d2 = _seg_dist2(point, (x1, y1), (x2, y2))
        return float(d2) if d2 <= tol2 else None
    if kind == "polyline":
        d2 = _min_dist2_polyline(point, geom)
        return float(d2) if d2 <= tol2 else None

    cx, cy, r = geom[0], geom[1], geom[2]
    dx, dy = point[0] - cx, point[1] - cy
    dist2 = dx * dx + dy * dy
    if kind == "arc":
        start_deg, end_deg = geom[3], geom[4]
        if dx == 0 and dy == 0:
            return float(r * r) if r <= tolerance_nm else None
        ang = math.degrees(math.atan2(dy, dx))
        if not _angle_in_sweep(ang, start_deg, end_deg):
            ends = (_point_on_arc(cx, cy, r, start_deg), _point_on_arc(cx, cy, r, end_deg))
            best = min((point[0] - px) ** 2 + (point[1] - py) ** 2 for px, py in ends)
            return float(best) if best <= tol2 else None
    if dx == 0 and dy == 0:
        return float(r * r) if r <= tolerance_nm else None
    if r > tolerance_nm:
        if not ((r - tolerance_nm) ** 2 <= dist2 <= (r + tolerance_nm) ** 2):
            return None
    elif dist2 > (r + tolerance_nm) ** 2:
        return None
    return (math.sqrt(dist2) - r) ** 2


# --------------------------------------------------------------------------------------
# 操作（冻结接口③：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------


def select_all(sk_or_doc: Any, *, kinds: Any = None, layer: Any = None,
               construction: Any = None) -> list[str]:
    """全选：返回按文档顺序的全部实体 uuid（可按 kind/layer/construction 过滤）。

    不读几何 ⇒ 任何 kind（含本片不认识的）都能列出来；过滤词 ``kinds`` 里出现未知名字 ⇒
    ``CadError(reason="invalid_argument")``（**不静默返回空集**）。``[]`` = 真为零（草图里真没有实体）。
    """
    return _uuids(_filtered(_sketch(sk_or_doc), kinds=kinds, layer=layer,
                            construction=construction))


def select_window(sk_or_doc: Any, *, p1_nm: Any = None, p2_nm: Any = None, kinds: Any = None,
                  layer: Any = None, construction: Any = None) -> list[str]:
    """**窗选**：返回**完全落在矩形内**的实体 uuid（文档顺序）。

    判据 = 实体的精确包围盒 ⊆ 矩形（弧的包围盒按「端点 + 扫掠内的象限点」精确算）。
    矩形零宽/零高 ⇒ ``invalid_argument``；长度非整数纳米 ⇒ ``invalid_argument``；
    遇到本片不支持的 kind ⇒ ``unsupported_geometry``（不跳过）。``[]`` = 真为零。
    """
    sketch = _sketch(sk_or_doc)
    rect = _as_rect(p1_nm, p2_nm)
    out: list[str] = []
    for entity in _filtered(sketch, kinds=kinds, layer=layer, construction=construction):
        bx1, by1, bx2, by2 = _bbox(_shape(entity))
        if bx1 >= rect[0] and by1 >= rect[1] and bx2 <= rect[2] and by2 <= rect[3]:
            out.append(_uuid_of(entity))
    return out


def select_crossing(sk_or_doc: Any, *, p1_nm: Any = None, p2_nm: Any = None, kinds: Any = None,
                    layer: Any = None, construction: Any = None) -> list[str]:
    """**交叉窗选**：返回**曲线与矩形相交或相切**（含完全包含）的实体 uuid（文档顺序）。

    判定是解析式的（不是采样折线）：line/polyline 整数叉积精确；circle 用「最近 ≤ r ≤ 最远」
    整数平方判据；arc 用「端点在矩形内 ∨ 与矩形某条边相交且交点角在扫掠内」。
    ``[]`` = 真为零。
    """
    sketch = _sketch(sk_or_doc)
    rect = _as_rect(p1_nm, p2_nm)
    return [_uuid_of(entity)
            for entity in _filtered(sketch, kinds=kinds, layer=layer, construction=construction)
            if _shape_rect_hit(_shape(entity), rect)]


def select_fence(sk_or_doc: Any, *, points_nm: Any = None, kinds: Any = None, layer: Any = None,
                 construction: Any = None) -> list[str]:
    """**栏选**：返回与围栏折线相交或相切的实体 uuid（文档顺序）。

    围栏少于 2 点 ⇒ ``invalid_argument``。圆/弧用「到线段最近距离 ≤ r ≤ 到端点最远距离」
    （最近距离是 ``Fraction`` 精确有理数），弧另加扫掠角检查。``[]`` = 真为零。
    """
    sketch = _sketch(sk_or_doc)
    fence = _as_points(points_nm, what="points_nm", minimum=2)
    return [_uuid_of(entity)
            for entity in _filtered(sketch, kinds=kinds, layer=layer, construction=construction)
            if _shape_polyline_hit(_shape(entity), fence)]


def selection_update(sk_or_doc: Any, *, selection: Any = None, refs: Any = None,
                     mode: str = "add") -> list[str]:
    """**加减选**：``add`` / ``remove`` / ``toggle`` 三种语义，返回**新的选择集**（文档顺序）。

    - ``selection`` = 现选择集（uuid 序列，单个字符串等价于长度 1）；``refs`` = 本次操作的 uuid；
    - 返回值**按 entities 文档顺序**去重排列（与入参顺序无关，下游拿到的顺序稳定）；
    - ``mode`` 不在 ``SELECTION_MODES`` ⇒ ``invalid_argument``（不静默当 add）；
    - ``selection`` / ``refs`` 里出现草图中不存在的 uuid ⇒ ``unknown_entity``（不静默丢弃）；
    - **不写文档**：选择集是会话态，L1 真源一个字节都不动。``[]`` = 真为零（新选择集为空）。
    """
    sketch = _sketch(sk_or_doc)
    order = {uid: idx for idx, uid in enumerate(_uuids(_entities_of(sketch)))}
    if not isinstance(mode, str) or mode not in SELECTION_MODES:
        raise _fail("invalid_argument", "unknown selection mode", got=repr(mode),
                    supported=list(SELECTION_MODES))
    current = set(_as_uuid_seq(selection, what="selection"))
    refs_seq = _as_uuid_seq(refs, what="refs")
    for uid in sorted(current) + refs_seq:
        if uid not in order:
            raise _fail("unknown_entity", "uuid is not an entity of this sketch", uuid=uid)
    if mode == "add":
        current |= set(refs_seq)
    elif mode == "remove":
        current -= set(refs_seq)
    else:
        current ^= set(refs_seq)
    return [uid for uid, _ in sorted(order.items(), key=lambda item: item[1]) if uid in current]


def pick(sk_or_doc: Any, *, point_nm: Any = None, tolerance_nm: Any = None, limit: int = 1,
         kinds: Any = None, layer: Any = None, construction: Any = None) -> list[str]:
    """**单击拾取**：返回距拾取点不超过 ``tolerance_nm`` 的实体 uuid，按（距离, 文档顺序）取前 ``limit`` 个。

    这是「对象选择」里的点选面：没有命中 ⇒ ``[]``（真为零）。``tolerance_nm`` 必须 ≥ 0 的整数纳米；
    ``limit`` 必须是 ≥ 1 的整数；弧只在扫掠范围内才算命中（不在扫掠内则退化为到两端点的距离）。
    """
    sketch = _sketch(sk_or_doc)
    point = _as_point(point_nm, what="point_nm")
    tolerance = _as_nm(tolerance_nm, what="tolerance_nm")
    if tolerance < 0:
        raise _fail("invalid_argument", "tolerance_nm must be >= 0", got=tolerance)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise _fail("invalid_argument", "limit must be an integer >= 1", got=repr(limit))
    hits: list[tuple[float, int, str]] = []
    for idx, entity in enumerate(_filtered(sketch, kinds=kinds, layer=layer,
                                           construction=construction)):
        key = _shape_pick(_shape(entity), point, tolerance)
        if key is not None:
            hits.append((key, idx, _uuid_of(entity)))
    hits.sort(key=lambda item: (item[0], item[1]))
    return [uid for _, _, uid in hits[:limit]]


#: 冻结接口②：操作名 → 函数（全部满足 ``op(sk_or_doc, **kwargs) -> list[str]``）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "select_all": select_all,
    "select_window": select_window,
    "select_crossing": select_crossing,
    "select_fence": select_fence,
    "selection_update": selection_update,
    "pick": pick,
}


# --------------------------------------------------------------------------------------
# 自检（至少 2 条硬断言；全部是「真几何 + 真数据后果」的可复跑判定）
# --------------------------------------------------------------------------------------


def _u(tag: str) -> str:
    return "33333333-3333-4333-8333-00000000%04d" % (int(tag),)


_U_DIAG = _u(1)      # line (0,0)-(100000,100000)
_U_LINE_IN = _u(2)   # line 完全落在窗选矩形内
_U_LINE_OUT = _u(3)  # line 远在矩形外
_U_CIRCLE_IN = _u(4)  # circle 完全落在矩形内
_U_CIRCLE_BIG = _u(5)  # 巨大的圆：把矩形整个包在圆盘内，但曲线不碰矩形
_U_CIRCLE_CROSS = _u(6)  # 圆曲线穿过矩形
_U_ARC = _u(7)       # 圆心原点 r=100000，0°→90° 逆时针
_U_POLY = _u(8)      # polyline (0,0)-(100000,0)
_U_POLY_OUT = _u(9)


def _fixture_sketch() -> dict[str, Any]:
    """L1 草图形态的最小夹具（真几何，不是桩：坐标全部是整数纳米）。"""
    return {
        "uuid": "33333333-3333-4333-8333-0000000000ff",
        "plane": "XY",
        "entities": [
            {"uuid": _U_DIAG, "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 0, "x2": 100000, "y2": 100000}},
            {"uuid": _U_LINE_IN, "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 1000, "y1": 1000, "x2": 5000, "y2": 1000}},
            {"uuid": _U_LINE_OUT, "kind": "line", "layer": "walls", "construction": True,
             "geom": {"x1": 500000, "y1": 500000, "x2": 600000, "y2": 600000}},
            {"uuid": _U_CIRCLE_IN, "kind": "circle", "layer": "0", "construction": False,
             "geom": {"cx": 5000, "cy": 5000, "r": 1000}},
            {"uuid": _U_CIRCLE_BIG, "kind": "circle", "layer": "0", "construction": False,
             "geom": {"cx": 5000, "cy": 5000, "r": 1000000}},
            {"uuid": _U_CIRCLE_CROSS, "kind": "circle", "layer": "0", "construction": False,
             "geom": {"cx": 5000, "cy": 5000, "r": 6000}},
            {"uuid": _U_ARC, "kind": "arc", "layer": "0", "construction": False,
             "geom": {"cx": 0, "cy": 0, "r": 100000, "start_deg": 0, "end_deg": 90}},
            {"uuid": _U_POLY, "kind": "polyline", "layer": "0", "construction": False,
             "geom": {"points": [[0, 0], [100000, 0]]}},
            {"uuid": _U_POLY_OUT, "kind": "polyline", "layer": "0", "construction": False,
             "geom": {"points": [[200000, 0], [300000, 0]]}},
        ],
        "constraints": [],
        "dimensions": [],
    }


_WINDOW = ((0, 0), (10000, 10000))
_FENCE = [(4000, 4500), (6000, 4500)]


def _selftest() -> list[tuple[str, bool, str]]:
    """硬断言（真几何 / 真数据后果 / 错误面）；每条都能独立复跑，不看环境变量。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: Any, detail: str = "") -> bool:
        checks.append((name, bool(ok), detail))
        return bool(ok)

    def reason_of(fn: Callable[[], Any]) -> str:
        try:
            fn()
        except CadError as exc:
            return exc.reason
        return "no_error"

    sketch = _fixture_sketch()
    before = copy.deepcopy(sketch)

    # --- 窗选 vs 交叉窗选：同一矩形、同一实体集，结果必须不同（"完全包含" 是真判据） ---
    window = select_window(sketch, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1])
    crossing = select_crossing(sketch, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1])
    check("window_exact_containment", window == [_U_LINE_IN, _U_CIRCLE_IN], repr(window))
    check("crossing_superset_of_window", crossing == [_U_DIAG, _U_LINE_IN, _U_CIRCLE_IN,
                                                      _U_CIRCLE_CROSS, _U_POLY], repr(crossing))
    check("window_is_strict_subset", len(window) < len(crossing), "%d < %d" % (len(window), len(crossing)))
    # 巨大的圆把矩形整个包住：圆盘含矩形，但圆**曲线**不碰矩形 ⇒ 交叉窗选必须排除它。
    check("circle_enclosing_rect_is_not_crossing", _U_CIRCLE_BIG not in crossing, repr(crossing))

    # --- 弧的扫掠必须被尊重：同一个圆上 45° 命中、225° 不命中（圆级判据两次都为真） ---
    hit_45 = select_crossing(sketch, p1_nm=(70000, 70000), p2_nm=(72000, 72000))
    miss_225 = select_crossing(sketch, p1_nm=(-72000, -72000), p2_nm=(-70000, -70000))
    check("arc_sweep_interior_crossing", hit_45 == [_U_DIAG, _U_ARC], repr(hit_45))
    check("arc_sweep_excludes_other_quadrant", miss_225 == [], repr(miss_225))

    # --- 栏选：围栏段与对角线和圆相交，与其余实体不相交 ---
    fence = select_fence(sketch, points_nm=_FENCE)
    check("fence_hits_exactly_two", fence == [_U_DIAG, _U_CIRCLE_IN], repr(fence))

    # --- 单击拾取（含"零容差命中端点"与"扫掠外的弧不算命中"） ---
    pick_hit = pick(sketch, point_nm=(5000, 1000), tolerance_nm=0)
    pick_tol = pick(sketch, point_nm=(5000, 1100), tolerance_nm=100)
    pick_miss = pick(sketch, point_nm=(50000, 80000), tolerance_nm=100)
    pick_arc_out_of_sweep = pick(sketch, point_nm=(50000, -80000), tolerance_nm=6000)
    check("pick_zero_tolerance_endpoint", pick_hit == [_U_LINE_IN], repr(pick_hit))
    check("pick_inside_tolerance", pick_tol == [_U_LINE_IN], repr(pick_tol))
    check("pick_miss_is_true_zero", pick_miss == [], repr(pick_miss))
    check("pick_arc_out_of_sweep_misses", pick_arc_out_of_sweep == [], repr(pick_arc_out_of_sweep))

    # --- 加减选：并/差/异或 + 文档顺序归一（与入参顺序无关）+ 真数据后果（选择集本身） ---
    step1 = selection_update(sketch, selection=None, refs=[_U_CIRCLE_IN, _U_LINE_IN], mode="add")
    step2 = selection_update(sketch, selection=step1, refs=[_U_LINE_IN], mode="remove")
    step3 = selection_update(sketch, selection=step2, refs=[_U_CIRCLE_IN, _U_ARC], mode="toggle")
    check("selection_add_document_order", step1 == [_U_LINE_IN, _U_CIRCLE_IN], repr(step1))
    check("selection_remove", step2 == [_U_CIRCLE_IN], repr(step2))
    check("selection_toggle", step3 == [_U_ARC], repr(step3))
    check("selection_empty_is_true_zero", selection_update(sketch, selection=step3, refs=[_U_ARC],
                                                           mode="remove") == [], "")

    # --- 错误面：一律 CadError + 机器可读 reason（不静默、不返回空集） ---
    check("error_degenerate_window",
          reason_of(lambda: select_window(sketch, p1_nm=(0, 0), p2_nm=(0, 10000))) == "invalid_argument")
    check("error_non_integer_nm",
          reason_of(lambda: select_window(sketch, p1_nm=(0, 0), p2_nm=(10000.5, 10000))) == "invalid_argument")
    check("error_unknown_kind_filter",
          reason_of(lambda: select_all(sketch, kinds=["spline"])) == "invalid_argument")
    check("error_short_fence",
          reason_of(lambda: select_fence(sketch, points_nm=[(0, 0)])) == "invalid_argument")
    check("error_unknown_mode",
          reason_of(lambda: selection_update(sketch, refs=[_U_ARC], mode="xor")) == "invalid_argument")
    check("error_unknown_ref",
          reason_of(lambda: selection_update(sketch, refs=[_u(99)], mode="add")) == "unknown_entity")
    check("error_unknown_selection_uuid",
          reason_of(lambda: selection_update(sketch, selection=[_u(99)], mode="add")) == "unknown_entity")
    check("error_not_a_sketch", reason_of(lambda: select_all(42)) == "no_target_sketch")

    bad = {"entities": [{"uuid": _u(50), "kind": "polyline", "layer": "0", "construction": False,
                         "geom": {"points": [[0, 0]]}},
                        {"uuid": _u(51), "kind": "spline", "layer": "0", "construction": False,
                         "geom": {"cx": 0, "cy": 0}}],
           "constraints": [], "dimensions": []}
    check("error_polyline_needs_two_points",
          reason_of(lambda: select_window(bad, p1_nm=(0, 0), p2_nm=(10, 10))) == "unsupported_geometry")
    check("error_unknown_kind_is_loud",
          reason_of(lambda: select_crossing({"entities": [bad["entities"][1]]},
                                            p1_nm=(0, 0), p2_nm=(10, 10))) == "unsupported_geometry")

    # --- 过滤面：过滤词是硬校验，过滤结果按文档顺序 ---
    check("filter_layer_only", select_all(sketch, layer="walls") == [_U_LINE_OUT], "")
    check("filter_construction_only", select_all(sketch, construction=True) == [_U_LINE_OUT], "")
    check("filter_kinds_only", select_crossing(sketch, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1],
                                               kinds=["line"]) == [_U_DIAG, _U_LINE_IN], "")

    # --- 真数据后果：选择操作只读，草图/L1 真源一个字节都不动 ---
    check("sketch_bytes_untouched", sketch == before, "selection ops must not mutate L1")

    from cadcore.docmodel import document_to_l1, new_document  # 局部导入：只有自检需要文档面

    doc = new_document()
    doc.sketches = [copy.deepcopy(_fixture_sketch())]
    l1_before = document_to_l1(doc)
    doc_window = select_window(doc, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1])
    doc_crossing = select_crossing(doc, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1])
    doc_pick = pick(doc, point_nm=(5000, 1000), tolerance_nm=0)
    l1_after = document_to_l1(doc)
    check("doc_reaches_sketches0", doc_window == window and doc_crossing == crossing,
          "%r / %r" % (doc_window, doc_crossing))
    check("doc_pick_reaches_sketches0", doc_pick == [_U_LINE_IN], repr(doc_pick))
    check("l1_text_byte_identical", l1_before == l1_after, "L1 must be byte-stable after selection")
    check("empty_doc_raises",
          reason_of(lambda: select_all(new_document())) == "no_target_sketch")

    # --- 兼容键面（只在主键缺席时启用）：x/y/radius_nm 的圆与 a1/a2 的弧 ---
    alias = {"entities": [
        {"uuid": _u(60), "kind": "circle", "layer": "0", "construction": False,
         "geom": {"x": 5000, "y": 5000, "radius_nm": 1000}},
        {"uuid": _u(61), "kind": "arc", "layer": "0", "construction": False,
         "geom": {"cx": 0, "cy": 0, "r": 100000, "a1_deg": 0, "a2_deg": 90}},
    ], "constraints": [], "dimensions": []}
    alias_hit = select_crossing(alias, p1_nm=(70000, 70000), p2_nm=(72000, 72000))
    check("alias_keys_resolve", alias_hit == [_u(61)], repr(alias_hit))
    check("alias_window", select_window(alias, p1_nm=_WINDOW[0], p2_nm=_WINDOW[1]) == [_u(60)], "")

    # --- 冻结接口自检：签名与返回类型（下游按契约调用） ---
    check("operations_contract", all(callable(fn) for fn in OPERATIONS.values())
          and set(OPERATIONS) == {"select_all", "select_window", "select_crossing",
                                  "select_fence", "selection_update", "pick"}, repr(sorted(OPERATIONS)))
    check("returns_uuid_lists", all(isinstance(item, str) and item == item.strip()
                                    for item in window + crossing + fence + step1), "")
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    checks = _selftest()
    failed = [item for item in checks if not item[1]]
    for name, ok, detail in checks:
        print("CHECK %s %s%s" % ("ok" if ok else "FAIL", name,
                                 (" (%s)" % detail) if detail and not ok else ""))
    print("SELFTEST_SUMMARY family=%s op=%s asserts=%d failed=%d"
          % (FAMILY, PRIMARY_OP, len(checks), len(failed)))
    print("FEATURE_OK family=%s op=%s verdict=%s"
          % (FAMILY, PRIMARY_OP, "PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "select_all": {"tier": "query", "readonly": True, "returns": "data"},
    "select_fence": {"tier": "query", "readonly": True, "returns": "data"},
}
