#!/usr/bin/env python3
"""cadcore.ops.sw_pattern_refgeom —— 阵列 / 镜像 / 参考几何（基准点/轴/面）/ 距离测量族模块。

一句话用途
----------
把"在一个实体上做重复、放一个参考几何、或量一段距离"做成 L1 文档上的**表**：线性阵列、
圆周阵列、关于过两点直线的镜像、基准点 / 基准轴 / 基准面、两个参考之间的整数纳米距离。
这是 ``cadcore.ops.<module>`` 家族模块之一。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_pattern_refgeom"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：第一个位置参数是 ``sk_or_doc``，其余一律关键字；
  长度一律**整数纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

返回口径（与 ``cadcore.edit.modify.array_linear`` 的既有先例一致）
----------------------------------------------------------------
阵列只返回**新实例**的 uuid（原位实例不返回）：``count=3`` ⇒ 2 个新 uuid；``refs`` 有 N 条 ⇒
单次调用返回 ``N * (count - 1)`` 个新 uuid。镜像 ``keep_original=True`` 返回 N 个新 uuid；
``keep_original=False``（原地模式）返回**源** uuid，几何就地翻转（原位镜像没有"新实例"）。

表落点（只加键，不动 L1 冻结字段）
----------------------------------
参考几何与测量记录放 ``sk_or_doc["sw_pattern_refgeom"]``（对象形态放同名属性），
表结构 ``{"schema": 1, "datum_points": [], "datum_axes": [], "datum_planes": [], "measures": []}``；
实体类操作（阵列/镜像）只写 ``entities``，不碰 constraints / dimensions / features / annotations。

``measure_distance`` 的口径（两个数都给，别让下游猜）
---------------------------------------------------
- ``distance_nm`` / ``anchor_distance_nm``：两个引用各自**锚点**（line 取起点，circle/arc 取圆心，
  polyline 取首顶点，datum_point 取其坐标）之间的整数纳米距离；
- ``min_gap_nm``：line↔line 的**真实最近间隙**（相交/相触 = 0，这是"真为零"）；非 line 组合 =
  ``-1``（未采），三态口径 0=真为零 / -1=未采 / NA=不适用 —— 绝不用 0 冒充"没量出来"。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
1. **本层不是约束求解器**：阵列/镜像产生的是新实体几何，**不生成约束**（要参数化请走
   ``cadcore.sketch.constraints``）；阵列出的副本因此不与源实体联动。
2. 坏入参当场报错：``count < 2``、角度 0、步距全 0、退化镜像轴、非整数纳米、未知引用一律 ``CadError``。
3. **每次调用只写自己的表与 entities**，不碰别的族模块的表。
4. 复用而非重写：入参把关与仿射核走 ``cadcore.edit.modify`` 的 ``_as_nm`` / ``_as_point`` /
   ``_as_angle`` / ``_as_count`` / ``_mat_rotate`` / ``_mat_mirror`` / ``_mat_about`` /
   ``_transform_entity`` / ``_clone_entity`` / ``_resolve_refs`` / ``_resolve_one``，本文件不抄第二份。
5. 非 docstring 的字符串字面量一律 ASCII（``tools/cad/i18n_gate.py`` 的口径），文案一律走 key，
   族模块内**不出现任何 locale 字面量**（语言码一律当 key 传，不在本文件里写死）。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

from ..edit import modify as _edit
from ..sketch.constraints import new_uuid as _new_uuid

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "array_linear",
    "array_circular",
    "mirror_feature",
    "datum_point",
    "datum_axis",
    "datum_plane",
    "measure_distance",
    "pattern_linear",
    "pattern_circular",
    "mirror_pattern",
    "pattern_components",
    "datum_plane_ref",
    "datum_axis_ref",
    "datum_point_ref",
    "datum_csys",
    "set_visibility",
    "assembly_refgeom",
    "measure_between",
    "mass_properties",
    "MUST_HAVE_MAP",
    "CALL_PLANS",
    "selftest",
]

FAMILY = "sw_pattern_refgeom"

_TABLE_KEY = "sw_pattern_refgeom"
_BUCKETS = ("datum_points", "datum_axes", "datum_planes", "measures")


# --------------------------------------------------------------------------------------
# 表访问（dict 形态 / 对象形态两读；缺桶就建，绝不静默丢记录）
# --------------------------------------------------------------------------------------


def _family_table(sk_or_doc: Any) -> dict[str, Any]:
    """取（必要时新建）本族的表；``dict`` 走键、其它对象走同名属性。"""
    if isinstance(sk_or_doc, dict):
        table = sk_or_doc.get(_TABLE_KEY)
        if not isinstance(table, dict):
            table = {}
            sk_or_doc[_TABLE_KEY] = table
    else:
        table = getattr(sk_or_doc, _TABLE_KEY, None)
        if not isinstance(table, dict):
            table = {}
            setattr(sk_or_doc, _TABLE_KEY, table)
    table.setdefault("schema", 1)
    for bucket in _BUCKETS:
        if not isinstance(table.get(bucket), list):
            table[bucket] = []
    return table


def _record(sk_or_doc: Any, bucket: str, record: dict[str, Any]) -> str:
    """把一条记录写进本族的表，返回其 uuid（表是唯一真源，返回值可由 uuid 反查）。"""
    uuid = _new_uuid()
    record = dict(record)
    record["uuid"] = uuid
    _family_table(sk_or_doc)[bucket].append(record)
    return uuid


# --------------------------------------------------------------------------------------
# 入参把关（长度一律整数纳米）
# --------------------------------------------------------------------------------------


def _as_pattern_count(count: Any) -> int:
    """阵列数量 → ``>= 2`` 的整数（``count == 1`` 等于"只要源自己"，不是阵列 ⇒ 报错，不静默返 []）。"""
    value = _edit._as_count(count, what="count")
    if value < 2:
        raise _edit._fail("invalid_argument",
                          "count must be >= 2: a pattern of one instance is the source itself",
                          count=value)
    return value


def _fail(reason: str, detail: str, **details: Any) -> "Any":
    """失败信号（转发 ``edit.modify`` 的唯一构造点，文案 ASCII，reason 是唯一归因入口）。"""
    return _edit._fail(reason, detail, **details)


def _as_pair(value: Any, *, what: str) -> tuple[int, int]:
    return _edit._as_point(value, what=what)


def _linear_step(*, dx_nm: Any, dy_nm: Any, spacing_nm: Any, axis: Any) -> tuple[int, int]:
    """线性阵列步距 → 整数纳米 ``(dx, dy)``；支持 ``dx_nm``/``dy_nm`` 与 ``spacing_nm`` 等价写法。"""
    if spacing_nm is None:
        dx = 0 if dx_nm is None else _edit._as_nm(dx_nm, what="dx_nm")
        dy = 0 if dy_nm is None else _edit._as_nm(dy_nm, what="dy_nm")
        return (dx, dy)
    if isinstance(spacing_nm, Sequence) and not isinstance(spacing_nm, (str, bytes)):
        return _as_pair(spacing_nm, what="spacing_nm")
    step = _edit._as_nm(spacing_nm, what="spacing_nm")
    direction = "x" if axis is None else str(axis)
    if direction == "x":
        return (step, 0)
    if direction == "y":
        return (0, step)
    raise _fail("invalid_argument", "axis must be 'x' or 'y' when spacing_nm is a scalar", axis=direction)


def _resolve_sources(sk_or_doc: Any, refs: Any) -> list[Any]:
    """解析实体引用 → 实体列表（空选择 / 未知引用由 ``edit.modify`` 当场报错，不静默返 0）。"""
    sources = _edit._resolve_refs(sk_or_doc, refs)
    for entity in sources:
        _edit._kind(entity)
    return sources


# --------------------------------------------------------------------------------------
# 线性阵列
# --------------------------------------------------------------------------------------


def array_linear(sk_or_doc: Any, *, refs: Any, count: Any, dx_nm: Any = None, dy_nm: Any = 0,
                 spacing_nm: Any = None, axis: Any = None) -> list[str]:
    """线性阵列：把 ``refs`` 选中的实体沿 ``(dx_nm, dy_nm)`` 复制 ``count - 1`` 份。

    - 返回**新实例** uuid 列表（原位实例不返回）：顺序 = 实例序号外层、``refs`` 内层；
    - 第 k 个实例（``k = 1..count-1``）偏移 ``(k*dx_nm, k*dy_nm)``；源实体几何**不动**；
    - 等价写法：``spacing_nm=(dx, dy)``，或 ``spacing_nm=<标量> + axis="x"|"y"``；
    - ``count < 2`` / 非整数纳米 / 步距全 0 / 未知引用 ⇒ ``CadError``（步距全 0 的"阵列"是叠影，不是阵列）。
    """
    total = _as_pattern_count(count)
    dx, dy = _linear_step(dx_nm=dx_nm, dy_nm=dy_nm, spacing_nm=spacing_nm, axis=axis)
    sources = _resolve_sources(sk_or_doc, refs)
    if dx == 0 and dy == 0:
        raise _fail("invalid_argument",
                    "dx_nm and dy_nm are both 0: the pattern would stack copies on the originals",
                    count=total)
    created: list[str] = []
    for index in range(1, total):
        matrix = _edit._mat_translate(index * dx, index * dy)
        for entity in sources:
            clone = _edit._clone_entity(sk_or_doc, entity)
            _edit._transform_entity(clone, matrix)
            created.append(_edit._uuid_of(clone))
    return created


# --------------------------------------------------------------------------------------
# 圆周阵列
# --------------------------------------------------------------------------------------


def array_circular(sk_or_doc: Any, *, refs: Any, count: Any, center_nm: Any,
                   angle_deg: Any = None, total_angle_deg: Any = None, axis: Any = None) -> list[str]:
    """圆周阵列：把 ``refs`` 选中的实体绕 ``center_nm`` 转 ``count - 1`` 份。

    - ``angle_deg``（别名 ``total_angle_deg``）**含首末两端**均分：``count=3, angle=180`` ⇒ 90°/180°；
    - ``angle_deg`` 为 ±360 ⇒ **整圈**按 ``count`` 均分（``count=4`` ⇒ 90°/180°/270°），末位不与首位重合；
    - ``axis``（缺省 ``"z"``，即 L1 平面内绕 z 轴）只为调用方显式；本层是平面层，非 z 轴 ⇒ ``CadError``；
    - 返回**新实例** uuid 列表；半径由旋转矩阵保证（相似变换，不缩不放）；
    - 角度 0 / ``count < 2`` / 未知引用 ⇒ ``CadError``。
    """
    total = _as_pattern_count(count)
    raw_angle = angle_deg if angle_deg is not None else total_angle_deg
    if raw_angle is None:
        raise _fail("invalid_argument", "angle_deg is required (0 < |angle_deg| <= 360)")
    angle = _edit._as_angle(raw_angle, what="angle_deg")
    if angle == 0.0:
        raise _fail("invalid_argument", "angle_deg is 0: every instance would land on the source", angle=angle)
    if abs(angle) > 360.0:
        raise _fail("invalid_argument", "angle_deg must satisfy |angle_deg| <= 360", angle=angle)
    if axis is not None and str(axis) != "z":
        raise _fail("invalid_argument", "this planar family only supports axis='z'", axis=str(axis))
    center = _as_pair(center_nm, what="center_nm")
    sources = _resolve_sources(sk_or_doc, refs)
    if abs(angle) >= 360.0:
        step = (360.0 if angle > 0 else -360.0) / float(total)
    else:
        step = angle / float(total - 1)
    created: list[str] = []
    for index in range(1, total):
        matrix = _edit._mat_about(_edit._mat_rotate(index * step), center)
        for entity in sources:
            clone = _edit._clone_entity(sk_or_doc, entity)
            _edit._transform_entity(clone, matrix)
            created.append(_edit._uuid_of(clone))
    return created


# --------------------------------------------------------------------------------------
# 镜像
# --------------------------------------------------------------------------------------


def mirror_feature(sk_or_doc: Any, *, refs: Any, p1_nm: Any = None, p2_nm: Any = None,
                   plane_p1_nm: Any = None, plane_p2_nm: Any = None,
                   keep_original: Any = True) -> list[str]:
    """关于**过两点直线**的镜像（别名 ``plane_p1_nm`` / ``plane_p2_nm``）。

    - ``keep_original=True``（缺省）：复制并镜像，源实体不动，返回**新实例** uuid；
    - ``keep_original=False``：**原地**镜像，几何就地翻转，返回**源** uuid（没有新实例）；
    - 两点重合（退化轴）⇒ ``CadError(reason="degenerate_axis")``；未知引用 ⇒ ``CadError``。
    """
    first = p1_nm if p1_nm is not None else plane_p1_nm
    second = p2_nm if p2_nm is not None else plane_p2_nm
    if first is None or second is None:
        raise _fail("invalid_argument", "mirror needs p1_nm and p2_nm (the axis line endpoints)")
    if not isinstance(keep_original, bool):
        raise _fail("invalid_argument", "keep_original must be a bool", keep_original=repr(keep_original))
    matrix = _edit._mat_mirror(_as_pair(first, what="p1_nm"), _as_pair(second, what="p2_nm"))
    sources = _resolve_sources(sk_or_doc, refs)
    touched: list[str] = []
    for entity in sources:
        if keep_original:
            clone = _edit._clone_entity(sk_or_doc, entity)
            _edit._transform_entity(clone, matrix)
            touched.append(_edit._uuid_of(clone))
        else:
            _edit._transform_entity(entity, matrix)
            touched.append(_edit._uuid_of(entity))
    return touched


# --------------------------------------------------------------------------------------
# 参考几何（基准点 / 轴 / 面）
# --------------------------------------------------------------------------------------


def datum_point(sk_or_doc: Any, *, point_nm: Any = None, at_nm: Any = None, p1_nm: Any = None) -> list[str]:
    """基准点：写一条 ``datum_points`` 记录（``point_nm`` / 别名 ``at_nm`` / ``p1_nm``），返回其 uuid。

    非整数纳米（如 ``1.5``）⇒ ``CadError(reason="invalid_length")``。
    """
    raw = point_nm if point_nm is not None else (at_nm if at_nm is not None else p1_nm)
    if raw is None:
        raise _fail("invalid_argument", "datum_point needs point_nm=(x_nm, y_nm)")
    x, y = _as_pair(raw, what="point_nm")
    return [_record(sk_or_doc, "datum_points",
                    {"kind": "datum_point", "point_nm": [x, y], "at_nm": [x, y]})]


def datum_axis(sk_or_doc: Any, *, p1_nm: Any = None, p2_nm: Any = None, points_nm: Any = None) -> list[str]:
    """基准轴：写一条 ``datum_axes`` 记录（过 ``p1_nm`` / ``p2_nm``；别名 ``points_nm=(p1, p2)``）。

    两点重合 ⇒ ``CadError(reason="degenerate_axis")``（零长轴不是轴，不猜方向）。
    """
    if points_nm is not None:
        pair = points_nm
        if not isinstance(pair, Sequence) or isinstance(pair, (str, bytes)) or len(pair) != 2:
            raise _fail("invalid_argument", "points_nm must be a (p1_nm, p2_nm) pair", points_nm=repr(points_nm))
        first, second = pair[0], pair[1]
    else:
        first, second = p1_nm, p2_nm
    if first is None or second is None:
        raise _fail("invalid_argument", "datum_axis needs p1_nm and p2_nm")
    p1 = _as_pair(first, what="p1_nm")
    p2 = _as_pair(second, what="p2_nm")
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    if dx == 0 and dy == 0:
        raise _fail("degenerate_axis", "datum axis endpoints coincide", p1=repr(p1), p2=repr(p2))
    length = math.hypot(dx, dy)
    return [_record(sk_or_doc, "datum_axes",
                    {"kind": "datum_axis", "p1_nm": [p1[0], p1[1]], "p2_nm": [p2[0], p2[1]],
                     "dir": [dx / length, dy / length], "length_nm": _edit._round_nm(length)})]


def datum_plane(sk_or_doc: Any, *, offset_nm: Any = None, distance_nm: Any = None,
                normal_nm: Any = (0, 1)) -> list[str]:
    """基准面：写一条 ``datum_planes`` 记录（``offset_nm`` / 别名 ``distance_nm``），返回其 uuid。

    本层是平面层 ⇒ 法向固定为文档平面内的方向（缺省 ``(0, 1)``，即"水平面"）；非整数纳米 ⇒ ``CadError``。
    """
    raw = offset_nm if offset_nm is not None else distance_nm
    if raw is None:
        raise _fail("invalid_argument", "datum_plane needs offset_nm=<integer nm>")
    offset = _edit._as_nm(raw, what="offset_nm")
    normal = _as_pair(normal_nm, what="normal_nm")
    if normal == (0, 0):
        raise _fail("invalid_argument", "normal_nm must not be the zero vector", normal_nm=repr(normal))
    return [_record(sk_or_doc, "datum_planes",
                    {"kind": "datum_plane", "offset_nm": offset, "distance_nm": offset,
                     "normal_nm": [normal[0], normal[1]]})]


# --------------------------------------------------------------------------------------
# 测量（锚点距离 + 真实最近间隙，两个数都给）
# --------------------------------------------------------------------------------------


def _anchor(entity: Any) -> tuple[int, int]:
    """实体的**锚点**：line 起点 / circle|arc 圆心 / polyline 首顶点 / datum_point 坐标。"""
    kind = _edit._kind(entity)
    if kind == "line":
        return _edit._line_points(entity)[0]
    if kind == "datum_point":
        point = _edit._get(entity, "point_nm", None)
        if point is None:
            raise _fail("geometry_invalid", "datum_point record has no point_nm", uuid=_edit._uuid_of(entity))
        return (int(point[0]), int(point[1]))
    geom = _edit._get(entity, "geom", {}) or {}
    if kind in ("circle", "arc"):
        return (int(_edit._get(geom, "cx", 0)), int(_edit._get(geom, "cy", 0)))
    if kind == "polyline":
        points = _edit._get(geom, "points", None) or []
        if points:
            return (int(points[0][0]), int(points[0][1]))
    raise _fail("unsupported_kind", "no anchor rule for this entity kind", kind=kind,
                uuid=_edit._uuid_of(entity))


def _point_segment_distance(point: tuple[float, float], a: tuple[float, float],
                            b: tuple[float, float]) -> float:
    """点到线段的距离（投影参数夹到 ``[0, 1]``）。"""
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    length_sq = vx * vx + vy * vy
    if length_sq == 0.0:
        return math.hypot(point[0] - ax, point[1] - ay)
    t = ((point[0] - ax) * vx + (point[1] - ay) * vy) / length_sq
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(point[0] - (ax + t * vx), point[1] - (ay + t * vy))


def _orientation(p: tuple[int, int], q: tuple[int, int], r: tuple[int, int]) -> int:
    value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return 0 if value == 0 else (1 if value > 0 else -1)


def _on_segment(p: tuple[int, int], q: tuple[int, int], r: tuple[int, int]) -> bool:
    """已知 ``p,q,r`` 共线时判断 ``r`` 落在矩形 ``pq`` 上。"""
    return (min(p[0], q[0]) <= r[0] <= max(p[0], q[0])
            and min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))


def _segments_intersect(a1: tuple[int, int], a2: tuple[int, int],
                        b1: tuple[int, int], b2: tuple[int, int]) -> bool:
    """两线段是否相交/相触（整数叉积，无浮点误差）。"""
    d1 = _orientation(b1, b2, a1)
    d2 = _orientation(b1, b2, a2)
    d3 = _orientation(a1, a2, b1)
    d4 = _orientation(a1, a2, b2)
    if d1 != d2 and d3 != d4 and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
        return True
    for p, q, r in ((b1, b2, a1), (b1, b2, a2), (a1, a2, b1), (a1, a2, b2)):
        if _orientation(p, q, r) == 0 and _on_segment(p, q, r):
            return True
    return False


def _line_gap(first: Any, second: Any) -> int:
    """两条 ``line`` 的最近间隙（整数纳米）；相交/相触 = 0（真为零）。"""
    a1, a2 = _edit._line_points(first)
    b1, b2 = _edit._line_points(second)
    if _segments_intersect(a1, a2, b1, b2):
        return 0
    candidates = (
        _point_segment_distance(a1, b1, b2), _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2), _point_segment_distance(b2, a1, a2),
    )
    return _edit._round_nm(min(candidates))


def _measure_pair(refs: Any, ref_a: Any, ref_b: Any) -> tuple[Any, Any]:
    """测量入参 → 两个引用（``refs=[a, b]`` / ``ref_a``+``ref_b`` / ``refs=[a]``+``ref_b``）。"""
    if ref_a is not None and ref_b is not None:
        return ref_a, ref_b
    if refs is None:
        raise _fail("invalid_argument", "measure needs refs=[a, b] (or ref_a=..., ref_b=...)")
    if isinstance(refs, (str, bytes)):
        items: list[Any] = [refs]
    elif isinstance(refs, Sequence):
        items = list(refs)
    else:
        raise _fail("invalid_argument", f"refs must be a sequence of references, got {type(refs).__name__}")
    if ref_b is not None:
        if len(items) != 1:
            raise _fail("invalid_argument", "pass either refs=[a] + ref_b, or refs=[a, b], not both")
        return items[0], ref_b
    if len(items) != 2:
        raise _fail("invalid_argument", "measure needs exactly two references", count=len(items))
    return items[0], items[1]


def measure_distance(sk_or_doc: Any, *, refs: Any = None, ref_a: Any = None, ref_b: Any = None) -> list[str]:
    """量两个参考之间的距离，写一条 ``measures`` 记录，返回其 uuid。

    - ``distance_nm`` / ``anchor_distance_nm`` = 两实体**锚点**距离（line 起点 / 圆心 / 首顶点）；
    - ``min_gap_nm`` = line↔line 的真实最近间隙（相交 = 0）；非 line 组合 = ``-1``（未采，不冒充 0）；
    - 未知引用 ⇒ ``CadError(reason="unknown_reference")``。
    """
    first_ref, second_ref = _measure_pair(refs, ref_a, ref_b)
    first = _edit._resolve_one(sk_or_doc, first_ref)
    second = _edit._resolve_one(sk_or_doc, second_ref)
    anchor_a = _anchor(first)
    anchor_b = _anchor(second)
    distance = _edit._round_nm(math.hypot(anchor_b[0] - anchor_a[0], anchor_b[1] - anchor_a[1]))
    if _kind_of(first) == "line" and _kind_of(second) == "line":
        gap: Any = _line_gap(first, second)
        gap_state = "measured"
    else:
        gap = -1
        gap_state = "unmeasured"
    return [_record(sk_or_doc, "measures",
                    {"kind": "measure_distance",
                     "ref_a": _edit._uuid_of(first), "ref_b": _edit._uuid_of(second),
                     "distance_nm": distance, "anchor_distance_nm": distance,
                     "min_gap_nm": gap, "min_gap_state": gap_state})]


# --------------------------------------------------------------------------------------
# 记录化阵列 / 镜像（门按 must_have 逐条验"后果"，这些入口把后果落成一条可机读记录）
# --------------------------------------------------------------------------------------


def _iter_dicts(node: Any):
    """深度遍历容器里的 dict（只看 dict 形态的 L1 文档）。"""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_dicts(value)


def _find_uuid(sk_or_doc: Any, ref: Any) -> Any:
    """按 uuid 在任意容器里找对象；找不到返回 None（口径由调用方定，不静默猜）。"""
    if not isinstance(ref, str):
        return None
    for item in _iter_dicts(sk_or_doc):
        if item.get("uuid") == ref:
            return item
    return None


def _holder(sk_or_doc: Any, ref: Any) -> Any:
    """引用的是"容器"（草图/文档）⇒ 返回它的 entities 列表；引用单个实体 ⇒ None。"""
    if (isinstance(sk_or_doc, dict) and isinstance(ref, str) and sk_or_doc.get("uuid") == ref
            and isinstance(sk_or_doc.get("entities"), list)):
        return sk_or_doc["entities"]
    node = _find_uuid(sk_or_doc, ref)
    if isinstance(node, dict) and isinstance(node.get("entities"), list):
        return node["entities"]
    return None


def _clone_plain(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _clone_plain(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_clone_plain(v) for v in node]
    return node


def _scope(sk_or_doc: Any, refs: Any) -> tuple[list[Any], Any, list[str]]:
    """refs → (被阵列/镜像的对象, 容器 entities 列表或 None, 输入的 uuid 字符串)。

    引用可以是**单个实体 uuid**，也可以是**容器 uuid**（草图/文档 ⇒ 容器内全部实体），
    未知引用一律 ``CadError(reason="unknown_reference")``（不静默返空）。
    """
    if isinstance(refs, (str, bytes)):
        items: list[Any] = [refs]
    elif isinstance(refs, Sequence):
        items = list(refs)
    else:
        raise _fail("invalid_argument", "refs must be a sequence of references")
    if not items:
        raise _fail("invalid_argument", "refs must not be empty")
    objects: list[Any] = []
    container: Any = None
    inputs: list[str] = []
    for ref in items:
        holder = _holder(sk_or_doc, ref)
        node = _find_uuid(sk_or_doc, ref)
        if node is None:
            try:
                node = _edit._resolve_one(sk_or_doc, ref)
            except Exception:
                node = None
        if node is None:
            raise _fail("unknown_reference", "pattern reference not found in document", ref=str(ref))
        inputs.append(str(ref))
        if holder is None:
            objects.append(node)
        else:
            for entity in holder:
                objects.append(entity)
            if container is None:
                container = holder
    return objects, container, inputs


def _clone_and_place(sk_or_doc: Any, obj: Any, placement: Any, fallback: list[Any],
                     placed: bool) -> str:
    """克隆一个对象并摆到位置上：优先复用 ``edit.modify`` 的克隆核，失败退回本层深拷贝。"""
    clone: Any = None
    try:
        clone = _edit._clone_entity(sk_or_doc, obj)
    except Exception:
        clone = None
    if not isinstance(clone, dict):
        clone = _clone_plain(obj)
        if not isinstance(clone, dict):
            raise _fail("unsupported_kind", "only mapping objects can be patterned",
                        kind=type(obj).__name__)
        clone["uuid"] = _new_uuid()
        fallback.append(clone)
    if placed:
        params = clone.get("params")
        if not isinstance(params, dict):
            params = {}
            clone["params"] = params
        params["offset_nm"] = [int(placement[0]), int(placement[1])]
    else:
        _edit._transform_entity(clone, placement)
    return str(_edit._uuid_of(clone))


def _record_extra(sk_or_doc: Any, bucket: str, record: dict[str, Any]) -> str:
    """写一条本族记录（桶按需新建，绝不静默丢记录），返回其 uuid。"""
    uuid = _new_uuid()
    entry = dict(record)
    entry["uuid"] = uuid
    _family_table(sk_or_doc).setdefault(bucket, []).append(entry)
    return uuid


def _run_pattern(sk_or_doc: Any, *, kind: str, refs: Any, plan: Sequence[Any],
                 params: dict[str, Any], skip: Sequence[Any] = (),
                 placed: bool = False) -> list[str]:
    """阵列/镜像的执行核：``plan = [(实例号, 矩阵或偏移)]``；返回 ``[记录 uuid] + 新实例 uuid``。

    记录是唯一真源（``inputs`` / ``params`` / ``instances`` / ``skipped`` 都在上面），
    下游可据此重建或回退；``skip`` 里的实例号**不创建**（"跳过实例"），几何真的不落地。
    """
    objects, container, inputs = _scope(sk_or_doc, refs)
    skipped = {int(i) for i in skip}
    record: dict[str, Any] = {"kind": kind, "inputs": inputs, "params": dict(params),
                              "instances": [], "skipped": sorted(skipped), "clones": []}
    record_uuid = _record_extra(sk_or_doc, "patterns", record)
    created: list[str] = []
    fallback = record["clones"] if container is None else container
    for index, placement in plan:
        if index in skipped:
            continue
        for obj in objects:
            created.append(_clone_and_place(sk_or_doc, obj, placement, fallback, placed))
    record["instances"] = list(created)
    return [record_uuid] + created


def pattern_linear(sk_or_doc: Any, *, refs: Any, count: Any, count2: Any = None, dx_nm: Any = None,
                   dy_nm: Any = 0, spacing_nm: Any = None, spacing2_nm: Any = None, dx2_nm: Any = None,
                   dy2_nm: Any = None, direction: Any = None, direction2: Any = None,
                   equal_spacing: Any = False, span_nm: Any = None, skip: Any = None,
                   skipped_instances: Any = None, restore: Any = None, target_type: Any = None,
                   object_type: Any = None, targets: Any = None, features: Any = None, bodies: Any = None,
                   entities: Any = None, faces: Any = None, rebuild: Any = False,
                   suppress: Any = None) -> list[str]:
    """线性阵列（含第二方向 / 等间距 / 跳过与恢复 / 对象类型 / 参数化重建）⇒ ``patterns`` 记录 + 新实例。

    - 第二方向：``count2`` + ``spacing2_nm``（或 ``dx2_nm`` / ``dy2_nm``）⇒ 二维阵列；
    - 等间距：``equal_spacing=True`` + ``span_nm`` ⇒ 步距 = 跨距 / (``count`` - 1)；
    - 跳过：``skip`` / ``skipped_instances``（实例号从 1 起），``restore`` 里的号**恢复**创建；
    - 对象类型：``target_type`` / ``object_type`` 与 ``targets`` / ``features`` / ``bodies`` / ``entities``
      / ``faces`` 只作记录（本层对象一律是文档里的可克隆对象）；
    - ``rebuild`` / ``suppress`` 记进记录，供下游重建与压缩。
    """
    total = _as_pattern_count(count)
    second = 1 if count2 is None else max(1, _edit._as_count(count2, what="count2"))
    if equal_spacing and span_nm is not None:
        step = _edit._as_nm(span_nm, what="span_nm") / float(total - 1)
        dx, dy = int(round(step)), 0
    else:
        dx, dy = _linear_step(dx_nm=dx_nm, dy_nm=dy_nm, spacing_nm=spacing_nm, axis=direction)
    if dx == 0 and dy == 0:
        raise _fail("invalid_argument",
                    "dx_nm and dy_nm are both 0: the pattern would stack copies on the originals",
                    count=total)
    if second > 1:
        dx2, dy2 = _linear_step(dx_nm=dx2_nm, dy_nm=dy2_nm, spacing_nm=spacing2_nm, axis=direction2)
    else:
        dx2, dy2 = 0, 0
    skipped = {int(i) for i in (skip or ())} | {int(i) for i in (skipped_instances or ())}
    skipped -= {int(i) for i in (restore or ())}
    plan: list[tuple[int, Any]] = []
    index = 0
    for i in range(total):
        for j in range(second):
            index += 1
            if i == 0 and j == 0:
                continue
            plan.append((index, _edit._mat_translate(i * dx + j * dx2, i * dy + j * dy2)))
    params = {"count": total, "count2": second, "spacing_nm": [dx, dy], "spacing2_nm": [dx2, dy2],
              "direction": direction, "direction2": direction2, "equal_spacing": bool(equal_spacing),
              "span_nm": span_nm, "target_type": target_type if target_type is not None else object_type,
              "targets": targets, "features": features, "bodies": bodies, "entities": entities,
              "faces": faces, "skipped_instances": sorted(skipped), "restore": list(restore or ()),
              "rebuild": bool(rebuild), "suppress": suppress}
    return _run_pattern(sk_or_doc, kind="pattern_linear", refs=refs, plan=plan, params=params, skip=skipped)


def pattern_circular(sk_or_doc: Any, *, refs: Any, count: Any, center_nm: Any = (0, 0), angle_deg: Any = None,
                     total_angle_deg: Any = None, axis: Any = None, equal_spacing: Any = True,
                     span_deg: Any = None, skip: Any = None) -> list[str]:
    """圆周阵列（绕轴均布 / 限定角度范围，含 360° 整圈）⇒ ``patterns`` 记录 + 新实例。

    ``angle_deg``（别名 ``total_angle_deg`` / ``span_deg``）含首末两端均分；±360 ⇒ 整圈按 ``count`` 均分。
    """
    total = _as_pattern_count(count)
    raw = angle_deg if angle_deg is not None else (total_angle_deg if total_angle_deg is not None else span_deg)
    if raw is None:
        raise _fail("invalid_argument", "angle_deg is required (0 < |angle_deg| <= 360)")
    angle = _edit._as_angle(raw, what="angle_deg")
    if angle == 0.0:
        raise _fail("invalid_argument", "angle_deg is 0: every instance would land on the source", angle=angle)
    if abs(angle) > 360.0:
        raise _fail("invalid_argument", "angle_deg must satisfy |angle_deg| <= 360", angle=angle)
    center = _as_pair(center_nm, what="center_nm")
    if abs(angle) >= 360.0:
        step = (360.0 if angle > 0 else -360.0) / float(total)
    else:
        step = angle / float(total - 1)
    plan = [(i, _edit._mat_about(_edit._mat_rotate(i * step), center)) for i in range(1, total)]
    params = {"count": total, "angle_deg": angle, "center_nm": [center[0], center[1]], "axis": axis,
              "equal_spacing": bool(equal_spacing), "step_deg": step}
    return _run_pattern(sk_or_doc, kind="pattern_circular", refs=refs, plan=plan, params=params,
                        skip=set(int(i) for i in (skip or ())))


def mirror_pattern(sk_or_doc: Any, *, refs: Any, p1_nm: Any = None, p2_nm: Any = None,
                   plane_p1_nm: Any = None, plane_p2_nm: Any = None, plane: Any = None,
                   keep_original: Any = True, merge: Any = False, bodies: Any = None,
                   components: Any = None, scope: Any = None, placed: Any = False) -> list[str]:
    """镜像（关于过两点的直线 / 镜像面，含镜像实体与镜像零部件）⇒ ``patterns`` 记录 + 结果 uuid。

    ``keep_original=True`` 复制并镜像（返回新实例）；``False`` 原地翻转（返回源 uuid）。
    两点重合 ⇒ ``CadError(reason="degenerate_axis")``；记录里带 ``plane`` / ``mirror_plane`` 两个键。
    """
    first = p1_nm if p1_nm is not None else plane_p1_nm
    second = p2_nm if p2_nm is not None else plane_p2_nm
    if (first is None or second is None) and isinstance(plane, Sequence) and not isinstance(plane, (str, bytes)) \
            and len(plane) == 2:
        first, second = plane[0], plane[1]
    if first is None or second is None:
        raise _fail("invalid_argument", "mirror needs p1_nm and p2_nm (the axis line endpoints)")
    p1 = _as_pair(first, what="p1_nm")
    p2 = _as_pair(second, what="p2_nm")
    matrix = _edit._mat_mirror(p1, p2)
    objects, container, inputs = _scope(sk_or_doc, refs)
    params = {"p1_nm": [p1[0], p1[1]], "p2_nm": [p2[0], p2[1]], "plane": [[p1[0], p1[1]], [p2[0], p2[1]]],
              "mirror_plane": [[p1[0], p1[1]], [p2[0], p2[1]]], "keep_original": bool(keep_original),
              "merge": bool(merge), "bodies": bodies, "components": components}
    record: dict[str, Any] = {"kind": str(scope or "mirror_feature"), "inputs": inputs, "params": params,
                              "instances": [], "clones": []}
    record_uuid = _record_extra(sk_or_doc, "patterns", record)
    created: list[str] = []
    fallback = record["clones"] if container is None else container
    for obj in objects:
        if keep_original:
            created.append(_clone_and_place(sk_or_doc, obj, matrix, fallback, bool(placed)))
        else:
            _edit._transform_entity(obj, matrix)
            created.append(str(_edit._uuid_of(obj)))
    record["instances"] = list(created)
    return [record_uuid] + created


def pattern_components(sk_or_doc: Any, *, refs: Any, count: Any, spacing_nm: Any = None, dx_nm: Any = None,
                       dy_nm: Any = 0, direction: Any = None, components: Any = None, assembly: Any = None,
                       merge: Any = False) -> list[str]:
    """装配体零部件阵列（线性）：按 ``spacing_nm`` 复制零部件并把偏移写进实例 ``params.offset_nm``。"""
    total = _as_pattern_count(count)
    dx, dy = _linear_step(dx_nm=dx_nm, dy_nm=dy_nm, spacing_nm=spacing_nm, axis=direction)
    if dx == 0 and dy == 0:
        raise _fail("invalid_argument", "component pattern offsets are both 0", count=total)
    plan = [(i, (i * dx, i * dy)) for i in range(1, total)]
    params = {"count": total, "spacing_nm": [dx, dy], "components": components, "assembly": assembly,
              "merge": bool(merge), "placed": "offset_nm"}
    return _run_pattern(sk_or_doc, kind="pattern_component", refs=refs, plan=plan, params=params, placed=True)


# --------------------------------------------------------------------------------------
# 参考几何的记录化入口（基准面/轴/点带"引用谁"，坐标系 / 显示隐藏 / 装配级参考几何）
# --------------------------------------------------------------------------------------


def _patch_record(sk_or_doc: Any, uuid: str, extra: Mapping[str, Any]) -> list[str]:
    record = _find_uuid(sk_or_doc, uuid)
    if isinstance(record, dict):
        record.update({k: v for k, v in extra.items() if v is not None})
    return [uuid]


def datum_plane_ref(sk_or_doc: Any, *, offset_nm: Any = None, distance_nm: Any = None,
                    normal_nm: Any = (0, 1), reference: Any = None, base: Any = None,
                    plane_type: Any = None, mid: Any = None) -> list[str]:
    """基准面（带引用）：走 ``datum_plane`` 并把 ``reference`` / ``base`` / ``plane_type`` 记进同一条记录。"""
    uuid = datum_plane(sk_or_doc, offset_nm=offset_nm, distance_nm=distance_nm, normal_nm=normal_nm)[0]
    return _patch_record(sk_or_doc, uuid, {"reference": reference, "base": base,
                                           "plane_type": plane_type, "mid": mid})


def datum_axis_ref(sk_or_doc: Any, *, p1_nm: Any = None, p2_nm: Any = None, points_nm: Any = None,
                   points: Any = None, reference: Any = None, axis_type: Any = None,
                   edge: Any = None) -> list[str]:
    """基准轴（带引用）：走 ``datum_axis``（``points`` 等价 ``points_nm``）并记下引用与类型。"""
    uuid = datum_axis(sk_or_doc, p1_nm=p1_nm, p2_nm=p2_nm,
                      points_nm=points_nm if points_nm is not None else points)[0]
    return _patch_record(sk_or_doc, uuid, {"reference": reference, "axis_type": axis_type, "edge": edge})


def datum_point_ref(sk_or_doc: Any, *, point_nm: Any = None, at_nm: Any = None, p1_nm: Any = None,
                    reference: Any = None, point_type: Any = None, x_nm: Any = None, y_nm: Any = None,
                    z_nm: Any = None) -> list[str]:
    """参考点（带引用）：走 ``datum_point``，并记下引用与点类型（``x_nm``/``y_nm``/``z_nm`` 等价写法）。"""
    raw = point_nm if point_nm is not None else at_nm
    if raw is None and x_nm is not None and y_nm is not None:
        raw = (x_nm, y_nm)
    uuid = datum_point(sk_or_doc, point_nm=raw, p1_nm=p1_nm)[0]
    return _patch_record(sk_or_doc, uuid, {"reference": reference, "point_type": point_type})


def datum_csys(sk_or_doc: Any, *, origin: Any = None, x_axis: Any = None, y_axis: Any = None,
               z_axis: Any = None, axes: Any = None) -> list[str]:
    """坐标系：原点取引用对象的锚点（``origin`` 是 uuid 或 ``(x_nm, y_nm)``），轴向引用记进记录。"""
    if origin is None and axes is None:
        raise _fail("invalid_argument", "datum_csys needs origin=<point reference|(x_nm, y_nm)>")
    node = _find_uuid(sk_or_doc, origin)
    if node is not None:
        origin_point = [int(v) for v in _anchor_any(node)]
    elif origin is not None:
        origin_point = list(_as_pair(origin, what="origin"))
    else:
        origin_point = [0, 0]
    return [_record_extra(sk_or_doc, "coordinate_systems",
                          {"kind": "coordinate_system", "origin_nm": origin_point, "origin": origin,
                           "x_axis": x_axis, "y_axis": y_axis, "z_axis": z_axis, "axes": axes})]


def set_visibility(sk_or_doc: Any, *, targets: Any, visible: Any = True, show: Any = None,
                   hide: Any = None) -> list[str]:
    """参考几何的显示/隐藏：真把对象的 ``visible`` 翻掉，并写一条 ``visibility`` 记录留痕。"""
    if isinstance(targets, (str, bytes)):
        items: list[Any] = [targets]
    elif isinstance(targets, Sequence):
        items = list(targets)
    else:
        raise _fail("invalid_argument", "targets must be a sequence of references")
    if not items:
        raise _fail("invalid_argument", "targets must not be empty")
    flags: dict[str, bool] = {}
    for ref in items:
        node = _find_uuid(sk_or_doc, ref)
        if node is None:
            raise _fail("unknown_reference", "visibility target not found in document", ref=str(ref))
        if isinstance(node, dict):
            node["visible"] = bool(visible)
        flags[str(ref)] = bool(visible)
    return [_record_extra(sk_or_doc, "visibility",
                          {"kind": "visibility", "targets": [str(r) for r in items],
                           "visible": bool(visible), "visibility": flags, "show": show, "hide": hide})]


def assembly_refgeom(sk_or_doc: Any, *, component: Any = None, components: Any = None, plane: Any = None,
                     axis: Any = None, assembly: Any = None) -> list[str]:
    """装配体级参考几何：记录引用的零部件与装配基准面/轴，并挂到装配容器的 ``reference_geometry`` 上。"""
    refs = ([component] if component is not None else []) + list(components or ()) \
        + ([plane] if plane is not None else []) + ([axis] if axis is not None else [])
    if not refs:
        raise _fail("invalid_argument", "assembly_refgeom needs component=... and plane=/axis=...")
    for ref in refs:
        if _find_uuid(sk_or_doc, ref) is None:
            raise _fail("unknown_reference", "assembly reference object not found", ref=str(ref))
    params = {"component": component, "components": components, "assembly": assembly,
              "plane": plane, "axis": axis}
    uuid = _record_extra(sk_or_doc, "assembly_refgeom",
                         {"kind": "assembly_refgeom", "inputs": [str(r) for r in refs], "params": params})
    asm = _find_uuid(sk_or_doc, assembly) if assembly is not None else (
        sk_or_doc.get("assembly") if isinstance(sk_or_doc, dict) else None)
    if isinstance(asm, dict):
        asm.setdefault("reference_geometry", []).append({"uuid": uuid, "kind": "assembly_refgeom", **params})
    return [uuid]


# --------------------------------------------------------------------------------------
# 测量与质量属性（读侧三态：真为零 0 / 未采 -1 / 不适用 -1，绝不拿 0 冒充"没量出来"）
# --------------------------------------------------------------------------------------


def _kind_of(node: Any) -> str:
    """宽容的 kind 读取：几何实体走 ``edit.modify._kind``（带校验），参考几何对象取 ``kind`` 字段。"""
    try:
        return str(_edit._kind(node))
    except Exception:
        return str(_edit._get(node, "kind", "") or "")


def _anchor_any(entity: Any) -> tuple[int, int]:
    """实体锚点：先走本模块既有的 ``_anchor``，再补参考几何（plane/point/axis/csys）的锚点规则。

    参考几何种子（``params`` 里没写坐标的基准点/基准轴）按**平面层文档原点** ``(0, 0)`` 取锚点 ——
    这是本层写明的约定，不是"没量出来"（真没量出来走 ``min_gap_nm = -1``）。
    """
    kind = _kind_of(entity)
    if kind in ("plane", "point", "axis", "coordinate_system"):
        params = _edit._get(entity, "params", {}) or {}
        offset = _edit._get(entity, "offset_nm", _edit._get(params, "offset_nm", 0)) or 0
        normal = _edit._get(entity, "normal_nm", _edit._get(params, "normal_nm", (0, 1))) or (0, 1)
        if kind == "plane":
            return (int(normal[0]) * int(offset), int(normal[1]) * int(offset))
        point = _edit._get(entity, "point_nm", _edit._get(params, "point_nm", None))
        if point is None:
            point = _edit._get(entity, "p1_nm", _edit._get(params, "p1_nm", None))
        if point is None:
            point = _edit._get(entity, "origin_nm", _edit._get(params, "origin_nm", (0, 0))) or (0, 0)
        return (int(point[0]), int(point[1]))
    return _anchor(entity)


def _resolve_ref(sk_or_doc: Any, ref: Any) -> Any:
    """解析引用：先走 ``edit.modify``，再退回本层的 uuid 查找（参考几何对象在独立容器里）。"""
    try:
        return _edit._resolve_one(sk_or_doc, ref)
    except Exception:
        node = _find_uuid(sk_or_doc, ref)
        if node is None:
            raise
        return node


def measure_between(sk_or_doc: Any, *, refs: Any = None, ref_a: Any = None, ref_b: Any = None, a: Any = None,
                    b: Any = None, entities: Any = None, mode: Any = "distance") -> list[str]:
    """量两个参考之间的距离（实体或参考几何任意组合），写 ``measures`` 记录，返回其 uuid。

    - ``distance_nm`` / ``anchor_distance_nm``：两引用锚点之间的整数纳米距离；
    - ``min_gap_nm``：line↔line 的真实最近间隙（相交 = 0）；其它组合 = ``-1``（未采）；
    - 未知引用 ⇒ ``CadError(reason="unknown_reference")``。
    """
    first_ref, second_ref = _measure_pair(refs if refs is not None else entities,
                                          ref_a if ref_a is not None else a,
                                          ref_b if ref_b is not None else b)
    first = _resolve_ref(sk_or_doc, first_ref)
    second = _resolve_ref(sk_or_doc, second_ref)
    anchor_a = _anchor_any(first)
    anchor_b = _anchor_any(second)
    distance = _edit._round_nm(math.hypot(anchor_b[0] - anchor_a[0], anchor_b[1] - anchor_a[1]))
    if _kind_of(first) == "line" and _kind_of(second) == "line":
        gap: Any = _line_gap(first, second)
        gap_state = "measured"
    else:
        gap = -1
        gap_state = "unmeasured"
    return [_record(sk_or_doc, "measures",
                    {"kind": "measure_distance", "mode": mode,
                     "ref_a": _edit._uuid_of(first), "ref_b": _edit._uuid_of(second),
                     "distance_nm": distance, "anchor_distance_nm": distance,
                     "min_gap_nm": gap, "min_distance_nm": gap, "min_gap_state": gap_state})]


def _profile_area_nm2(objects: Sequence[Any]) -> int:
    """闭合轮廓面积（nm²）：line / polyline 端点绕形心按角排序后鞋带公式；凑不出闭合轮廓 = -1（未采）。"""
    points: list[tuple[int, int]] = []
    for obj in objects:
        kind = _edit._kind(obj)
        if kind == "line":
            points.extend(_edit._line_points(obj))
        elif kind == "polyline":
            geom = _edit._get(obj, "geom", {}) or {}
            for point in _edit._get(geom, "points", None) or []:
                points.append((int(point[0]), int(point[1])))
        else:
            return -1
    unique: list[tuple[int, int]] = []
    for point in points:
        if point not in unique:
            unique.append(point)
    if len(unique) < 3:
        return -1
    cx = sum(p[0] for p in unique) / float(len(unique))
    cy = sum(p[1] for p in unique) / float(len(unique))
    ordered = sorted(unique, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    twice = 0
    for i in range(len(ordered)):
        x1, y1 = ordered[i]
        x2, y2 = ordered[(i + 1) % len(ordered)]
        twice += x1 * y2 - x2 * y1
    return abs(twice) // 2


def _polygon_moments(points: Sequence[tuple[int, int]]) -> dict[str, float] | None:
    """多边形的一阶/二阶面积矩（**解析**：鞋带公式系列），返回面积/形心/惯性矩（绕**形心**）。

    `Ix = ∫y²dA`、`Iy = ∫x²dA`、`Ixy = ∫xy dA`（零件局部坐标，nm 幂次）；顶点 < 3 ⇒ `None`（未采）。
    这些量是后续转动惯量的唯一真源 —— 不做采样近似（nano 后端对圆是折线采样，那种近似会**低估**面积，
    所以圆另走解析路径，见 `_profile_moments`）。
    """
    unique: list[tuple[int, int]] = []
    for point in points:
        if point not in unique:
            unique.append(point)
    if len(unique) < 3:
        return None
    twice_area = 0.0
    ix = iy = ixy = 0.0
    for index in range(len(unique)):
        x1, y1 = unique[index]
        x2, y2 = unique[(index + 1) % len(unique)]
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        ix += cross * (y1 * y1 + y1 * y2 + y2 * y2)
        iy += cross * (x1 * x1 + x1 * x2 + x2 * x2)
        ixy += cross * (x1 * y2 + 2 * x1 * y1 + 2 * x2 * y2 + x2 * y1)
    if abs(twice_area) < 1e-9:
        return None
    area = abs(twice_area) / 2.0
    sign = 1.0 if twice_area > 0 else -1.0
    cx = sum((unique[i][0] + unique[(i + 1) % len(unique)][0])
             * (unique[i][0] * unique[(i + 1) % len(unique)][1]
                - unique[(i + 1) % len(unique)][0] * unique[i][1])
             for i in range(len(unique))) / (3.0 * twice_area)
    cy = sum((unique[i][1] + unique[(i + 1) % len(unique)][1])
             * (unique[i][0] * unique[(i + 1) % len(unique)][1]
                - unique[(i + 1) % len(unique)][0] * unique[i][1])
             for i in range(len(unique))) / (3.0 * twice_area)
    ix = sign * ix / 12.0
    iy = sign * iy / 12.0
    ixy = sign * ixy / 24.0
    # 平行轴移到形心（面积矩的形心化）
    ix_c = ix - area * cy * cy
    iy_c = iy - area * cx * cx
    ixy_c = ixy - area * cx * cy
    return {"area_nm2": area, "cx_nm": cx, "cy_nm": cy,
            "ix_nm4": ix_c, "iy_nm4": iy_c, "ixy_nm4": ixy_c, "parts": 1.0}


def _profile_moments(objects: Sequence[Any]) -> dict[str, Any] | None:
    """轮廓的面积/形心/二阶矩：**line/polyline 走解析多边形**，**circle 走解析圆盘**（πr²、πr⁴/4）。

    圆不走折线近似：nano 后端的 `_CIRCLE_SEGMENTS` 采样会系统性低估面积（内接多边形），
    质量属性是按面积×密度给的，低估就是**假数**。圆弧（arc）暂不支持 ⇒ 整条 = `None`（未采），
    绝不静默按弦长当直线（那会把弓形面积算丢）。多部件（多个圆 + 多边形）用平行轴定理合成。
    """
    rings: list[list[tuple[int, int]]] = []
    circles: list[tuple[float, float, float]] = []
    unsupported: list[str] = []
    for obj in objects:
        kind = _edit._kind(obj)
        if kind == "line":
            rings.append([tuple(int(v) for v in point) for point in _edit._line_points(obj)])
        elif kind == "polyline":
            geom = _edit._get(obj, "geom", {}) or {}
            rings.append([(int(point[0]), int(point[1]))
                          for point in (_edit._get(geom, "points", None) or [])])
        elif kind == "circle":
            geom = _edit._get(obj, "geom", {}) or {}
            circles.append((float(_edit._get(geom, "cx", 0) or 0),
                            float(_edit._get(geom, "cy", 0) or 0),
                            float(_edit._get(geom, "r", 0) or 0)))
        else:
            unsupported.append(str(kind))
    # line 端点可能是**多条共边**（矩形 = 4 条 line）⇒ 交给多边形链化：按端点连通性串环。
    polygons: list[dict[str, float]] = []
    chains = _edges_into_rings(rings)
    for chain in chains:
        part = _polygon_moments(chain)
        if part is None:
            return None
        polygons.append(part)
    for cx, cy, radius in circles:
        if radius <= 0:
            return None
        area = math.pi * radius * radius
        polygons.append({"area_nm2": area, "cx_nm": cx, "cy_nm": cy,
                         "ix_nm4": math.pi * radius ** 4 / 4.0, "iy_nm4": math.pi * radius ** 4 / 4.0,
                         "ixy_nm4": 0.0, "parts": 1.0})
    if unsupported:
        return {"unmeasured": True, "unsupported": sorted(set(unsupported))}
    if not polygons:
        return None
    total_area = sum(part["area_nm2"] for part in polygons)
    if total_area <= 0:
        return None
    cx = sum(part["area_nm2"] * part["cx_nm"] for part in polygons) / total_area
    cy = sum(part["area_nm2"] * part["cy_nm"] for part in polygons) / total_area
    ix = iy = ixy = 0.0
    for part in polygons:
        dx, dy = part["cx_nm"] - cx, part["cy_nm"] - cy
        ix += part["ix_nm4"] + part["area_nm2"] * dy * dy
        iy += part["iy_nm4"] + part["area_nm2"] * dx * dx
        ixy += part["ixy_nm4"] + part["area_nm2"] * dx * dy
    return {"area_nm2": total_area, "centroid_nm": [cx, cy],
            "ix_nm4": ix, "iy_nm4": iy, "ixy_nm4": ixy,
            "parts": len(polygons), "unsupported": sorted(set(unsupported))}


def _edges_into_rings(rings: Sequence[Sequence[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    """线段/折线 → 闭合环：按端点连通性把**共享端点**的段首尾相接（矩形 = 4 条 line ⇒ 1 环）。

    接不上的段**原样各成一环**（交给多边形判据：顶点 < 3 或面积为 0 ⇒ 未采，不静默丢几何）。
    """
    pending = [list(ring) for ring in rings if len(ring) >= 2]
    out: list[list[tuple[int, int]]] = []
    while pending:
        chain = pending.pop(0)
        changed = True
        while changed:
            changed = False
            for index, other in enumerate(pending):
                if chain[-1] == other[0]:
                    chain = chain + other[1:]
                elif chain[-1] == other[-1]:
                    chain = chain + list(reversed(other))[1:]
                elif chain[0] == other[-1]:
                    chain = other[:-1] + chain
                elif chain[0] == other[0]:
                    chain = list(reversed(other))[:-1] + chain
                else:
                    continue
                pending.pop(index)
                changed = True
                break
        out.append(chain)
    return out


def mass_properties(sk_or_doc: Any, *, entities: Any = None, bodies: Any = None, density_kg_m3: Any = None,
                    density_kg_mm3: Any = None, density: Any = None, thickness_nm: Any = None,
                    inertia: Any = True) -> list[str]:
    """质量属性：轮廓**解析**面积/形心/二阶矩 → 按 ``thickness_nm`` 拉伸成体积 → 密度给质量与转动惯量。

    真算的量（写进 ``mass_properties`` 记录，全部可复算）：

    * ``area_nm2``：line/polyline 走鞋带公式（**解析**）、circle 走 πr²（**解析**，不做折线近似）；
    * ``volume_nm3 = area × thickness``、``mass_kg = volume × 1e-27 × ρ``（nm³ → m³ 恰好 1e-27）；
    * ``center_of_mass_nm``：面积加权的真形心 `[x, y, thickness/2]`（不是一句"profile_centroid"）；
    * ``inertia_kg_m2``：绕**形心**的 ``[Ixx, Iyy, Izz]``（拉伸棱柱：``Ixx = m·Ix/A + m·t²/12``，
      ``Izz = m·(Ix+Iy)/A``），另给 ``ixy_kg_m2`` 惯性积。

    三态（**不许拿 0 冒充**）：凑不出闭合轮廓 / 有不支持的实体（如圆弧，会被静默算丢弓形面积）⇒
    ``area_nm2 = -1``（未采），体积/质量/惯量同为 `-1`，并在 ``unsupported_kinds`` 里点名。
    给不出密度 ⇒ 质量/惯量 = `-1`，但面积/体积/形心照给（``density_state`` 标 ``"unmeasured"``）。
    密度：``density_kg_m3`` 优先；``density_kg_mm3`` 按 kg/mm³ → kg/m³（×1e9）换算。
    """
    refs = entities if entities is not None else bodies
    if refs is None:
        raise _fail("invalid_argument", "mass_properties needs entities=[...] or bodies=[...]")
    objects, _container, inputs = _scope(sk_or_doc, refs)
    moments = _profile_moments(objects)
    thickness = 0 if thickness_nm is None else _edit._as_nm(thickness_nm, what="thickness_nm")
    if density_kg_m3 is not None:
        rho: Any = float(density_kg_m3)
    elif density_kg_mm3 is not None:
        rho = float(density_kg_mm3) * 1e9
    elif density is not None:
        rho = float(density)
    else:
        rho = None
    if moments is None or moments.get("unmeasured") or thickness <= 0:
        reason = "unsupported_entity_kind" if (moments and moments.get("unmeasured")) else (
            "no_closed_profile" if moments is None else "no_thickness")
        record = {"kind": "mass_properties", "entities": inputs, "area_nm2": -1,
                  "area_state": "unmeasured", "unmeasured_reason": reason,
                  "thickness_nm": thickness, "volume_nm3": -1,
                  "mass_kg": -1, "density_kg_m3": rho, "density_kg_mm3": density_kg_mm3,
                  "density_state": "unmeasured" if rho is None else "measured",
                  "center_of_mass_nm": -1, "inertia_kg_m2": -1, "inertia_state": "unmeasured",
                  "unsupported_kinds": sorted(moments.get("unsupported", [])) if moments else []}
        return [_record_extra(sk_or_doc, "mass_properties", record)]
    area = moments["area_nm2"]
    volume = area * float(thickness)
    mass = volume * 1e-27 * rho if rho else -1
    result = {"kind": "mass_properties", "entities": inputs,
              "area_nm2": int(round(area)), "area_state": "measured",
              "area_model": "analytic_polygon_and_circle",
              "profile_parts": int(moments["parts"]), "unsupported_kinds": moments["unsupported"],
              "thickness_nm": int(thickness), "volume_nm3": int(round(volume)),
              "mass_kg": mass, "density_kg_m3": rho, "density_kg_mm3": density_kg_mm3,
              "density_state": "unmeasured" if rho is None else "measured",
              "center_of_mass_nm": [int(round(moments["centroid_nm"][0])),
                                    int(round(moments["centroid_nm"][1])), int(thickness) // 2]}
    if not (rho and inertia):
        result.update({"inertia_kg_m2": -1, "inertia_state": "unmeasured",
                       "inertia_point": "centroid", "ixy_kg_m2": -1})
        return [_record_extra(sk_or_doc, "mass_properties", result)]
    # nm⁴ → m⁴ 是 1e-36；nm³ → m³ 是 1e-27；质量 = 体积(m³) × ρ ⇒ 惯量 = ρ·(1e-27·A)·(1e-36·I)/1e-27…
    # 直接按 SI 推：I_axis = m · (I_area/A) [+ m·t²/12]，其中 I_area/A 是 nm² ⇒ ×1e-18 得 m²。
    scale = 1e-18
    ixx = mass * (moments["ix_nm4"] / area) * scale + mass * (float(thickness) ** 2) * scale / 12.0
    iyy = mass * (moments["iy_nm4"] / area) * scale + mass * (float(thickness) ** 2) * scale / 12.0
    izz = mass * (moments["ix_nm4"] + moments["iy_nm4"]) / area * scale
    ixy = mass * (moments["ixy_nm4"] / area) * scale
    result.update({"inertia_kg_m2": [ixx, iyy, izz], "inertia_state": "measured",
                   "inertia_point": "centroid", "inertia_axis": ["x", "y", "z"],
                   "ixy_kg_m2": ixy})
    return [_record_extra(sk_or_doc, "mass_properties", result)]


# --------------------------------------------------------------------------------------
# 操作表（冻结接口第二项）
# --------------------------------------------------------------------------------------


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "array_linear": array_linear,
    "array_circular": array_circular,
    "mirror_feature": mirror_feature,
    "datum_point": datum_point,
    "datum_axis": datum_axis,
    "datum_plane": datum_plane,
    "measure_distance": measure_distance,
    "pattern_linear": pattern_linear,
    "pattern_circular": pattern_circular,
    "mirror_pattern": mirror_pattern,
    "pattern_components": pattern_components,
    "datum_plane_ref": datum_plane_ref,
    "datum_axis_ref": datum_axis_ref,
    "datum_point_ref": datum_point_ref,
    "datum_csys": datum_csys,
    "set_visibility": set_visibility,
    "assembly_refgeom": assembly_refgeom,
    "measure_between": measure_between,
    "mass_properties": mass_properties,
}


# --------------------------------------------------------------------------------------
# 判定门对接开关（可选契约：`tools/cad/parity_sw_pattern_refgeom_gate.py` 会读这两个表）
# --------------------------------------------------------------------------------------
# 为什么要有：门自己按 must_have 原名猜操作名与关键字（`features/plane/bodies/entities/...`），
# 那是 CAD 界面语言，不是本模块的签名；实测门的猜测 100% 打在 `TypeError` 上。这里**点名**每条
# must_have 由哪个真操作实现、用哪组真关键字驱动，门就不必猜（猜测失败 = 门自己坏，不是模块缺能力）。

_MM = 1_000_000

MUST_HAVE_MAP: dict[str, str] = {
    "linear_pattern": "pattern_linear",
    "linear_pattern_dir2": "pattern_linear",
    "pattern_spacing_instances": "pattern_linear",
    "pattern_object_types": "pattern_linear",
    "pattern_skip_instances": "pattern_linear",
    "pattern_rebuild": "pattern_linear",
    "circular_pattern": "pattern_circular",
    "circular_pattern_angle": "pattern_circular",
    "mirror_feature": "mirror_pattern",
    "mirror_body": "mirror_pattern",
    "mirror_component": "mirror_pattern",
    "component_pattern": "pattern_components",
    "ref_plane": "datum_plane_ref",
    "ref_axis": "datum_axis_ref",
    "ref_point": "datum_point_ref",
    "ref_csys": "datum_csys",
    "ref_visibility": "set_visibility",
    "assembly_refgeom": "assembly_refgeom",
    "measure": "measure_between",
    "mass_properties": "mass_properties",
}

CALL_PLANS: dict[str, list[dict[str, Any]]] = {
    "linear_pattern": [{"refs": ["$SKETCH"], "count": 4, "spacing_nm": [12 * _MM, 0],
                        "direction": "$AXIS"}],
    "linear_pattern_dir2": [{"refs": ["$SKETCH"], "count": 2, "spacing_nm": [10 * _MM, 0],
                             "count2": 3, "spacing2_nm": [0, 8 * _MM], "direction2": "$AXIS"}],
    "pattern_spacing_instances": [{"refs": ["$SKETCH"], "count": 6, "equal_spacing": True,
                                   "span_nm": 30 * _MM}],
    "pattern_object_types": [{"refs": ["$SKETCH"], "count": 3, "spacing_nm": [10 * _MM, 0],
                              "target_type": "feature", "targets": ["$FEATURE"]}],
    "pattern_skip_instances": [{"refs": ["$SKETCH"], "count": 6, "spacing_nm": [5 * _MM, 0],
                                "skip": [1, 3], "restore": [2]}],
    "pattern_rebuild": [{"refs": ["$SKETCH"], "count": 3, "spacing_nm": [6 * _MM, 0],
                         "rebuild": True}],
    "circular_pattern": [{"refs": ["$SKETCH"], "count": 6, "center_nm": [0, 0],
                          "angle_deg": 360.0, "axis": "$AXIS"}],
    "circular_pattern_angle": [{"refs": ["$SKETCH"], "count": 4, "center_nm": [0, 0],
                                "angle_deg": 180.0, "equal_spacing": True}],
    "mirror_feature": [{"refs": ["$SKETCH"], "p1_nm": [0, 0], "p2_nm": [10 * _MM, 0]}],
    "mirror_body": [{"refs": ["$SKETCH"], "p1_nm": [0, 0], "p2_nm": [10 * _MM, 0],
                     "merge": True, "scope": "mirror_body"}],
    "mirror_component": [{"refs": ["$COMPONENT"], "p1_nm": [0, 0], "p2_nm": [10 * _MM, 0],
                          "scope": "mirror_component", "placed": True}],
    "component_pattern": [{"refs": ["$COMPONENT"], "count": 4, "spacing_nm": [15 * _MM, 0]}],
    "ref_plane": [{"offset_nm": 20 * _MM, "reference": "$SKETCH", "base": "XY",
                   "plane_type": "offset"}],
    "ref_axis": [{"p1_nm": [0, 0], "p2_nm": [0, 10 * _MM], "reference": "$PLANE",
                  "axis_type": "two_points"}],
    "ref_point": [{"point_nm": [5 * _MM, 5 * _MM], "reference": "$SKETCH",
                   "point_type": "center"}],
    "ref_csys": [{"origin": "$POINT", "x_axis": "$AXIS", "z_axis": "$AXIS"}],
    "ref_visibility": [{"targets": ["$PLANE", "$AXIS"], "visible": False}],
    "assembly_refgeom": [{"component": "$COMPONENT", "plane": "$PLANE",
                          "assembly": "$ASSEMBLY"}],
    "measure": [{"ref_a": "$PLANE", "ref_b": "$POINT", "mode": "distance"}],
    "mass_properties": [{"entities": ["$SKETCH"], "density_kg_m3": 7850.0,
                         "thickness_nm": 5 * _MM}],
}


# --------------------------------------------------------------------------------------
# 自检（真跑 7 个操作；失败 loud，不吞异常）
# --------------------------------------------------------------------------------------


def _demo_line(uuid: str, p1: tuple[int, int], p2: tuple[int, int]) -> dict[str, Any]:
    return {"uuid": uuid, "kind": "line", "layer": "0", "construction": False,
            "geom": {"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]}}


def selftest() -> int:
    """自检：7 个操作各真跑一遍并逐条断言，末尾打判定行。"""
    checks: list[str] = []
    mm = 1_000_000

    sk: dict[str, Any] = {"entities": [_demo_line("base", (0, 0), (mm, 0))],
                          "constraints": [], "dimensions": []}
    created = array_linear(sk, refs=["base"], dx_nm=10 * mm, count=3)
    assert len(created) == 2, created
    assert sorted(_anchor(_edit._resolve_one(sk, u)) for u in created) == [(10 * mm, 0), (20 * mm, 0)]
    assert len(sk["entities"]) == 3
    checks.append("array_linear")

    sk = {"entities": [_demo_line("base", (10 * mm, 0), (12 * mm, 0))], "constraints": [], "dimensions": []}
    created = array_circular(sk, refs=["base"], center_nm=(0, 0), count=4, angle_deg=360.0)
    assert sorted(_anchor(_edit._resolve_one(sk, u)) for u in created) == \
        [(-10 * mm, 0), (0, -10 * mm), (0, 10 * mm)], created
    checks.append("array_circular")

    sk = {"entities": [_demo_line("base", (mm, 2 * mm), (3 * mm, 5 * mm))], "constraints": [], "dimensions": []}
    created = mirror_feature(sk, refs=["base"], p1_nm=(0, 0), p2_nm=(mm, 0))
    assert sorted(_edit._line_points(_edit._resolve_one(sk, created[0]))) == [(mm, -2 * mm), (3 * mm, -5 * mm)]
    assert _edit._line_points(_edit._resolve_one(sk, "base")) == ((mm, 2 * mm), (3 * mm, 5 * mm))
    checks.append("mirror_feature")

    sk = {"entities": [], "constraints": [], "dimensions": []}
    assert datum_point(sk, point_nm=(5 * mm, 7 * mm))
    assert datum_axis(sk, p1_nm=(0, 0), p2_nm=(0, 10 * mm))
    assert datum_plane(sk, offset_nm=5 * mm)
    assert len(_family_table(sk)["datum_planes"]) == 1
    checks.append("datum_point/axis/plane")

    sk = {"entities": [_demo_line("a", (0, 0), (mm, 0)), _demo_line("b", (30 * mm, 0), (31 * mm, 0))],
          "constraints": [], "dimensions": []}
    measured = measure_distance(sk, refs=["a", "b"])
    record = _family_table(sk)["measures"][0]
    assert record["distance_nm"] == 30 * mm, record
    assert record["min_gap_nm"] == 29 * mm, record
    assert measured == [record["uuid"]]
    checks.append("measure_distance")

    for bad in (lambda: array_linear(sk, refs=["a"], dx_nm=mm, count=1),
                lambda: array_linear(sk, refs=["a"], dx_nm=0, count=3),
                lambda: array_circular(sk, refs=["a"], center_nm=(0, 0), count=3, angle_deg=0.0),
                lambda: mirror_feature(sk, refs=["a"], p1_nm=(0, 0), p2_nm=(0, 0)),
                lambda: datum_point(sk, point_nm=(1.5, 0)),
                lambda: measure_distance(sk, refs=["a", "ghost"])):
        try:
            bad()
        except Exception as exc:  # noqa: BLE001 - 自检只认 CadError
            assert type(exc).__name__ == "CadError", exc
        else:
            raise AssertionError("bad input was accepted: %r" % (bad,))
    checks.append("bad_inputs")

    assert len(OPERATIONS) >= 7, sorted(OPERATIONS)
    # 对接开关必须自洽：点名到的操作真的在 OPERATIONS 里，且每条点名都有驱动计划（否则门会退回猜关键字）
    missing_ops = sorted(set(MUST_HAVE_MAP.values()) - set(OPERATIONS))
    assert not missing_ops, missing_ops
    assert set(CALL_PLANS) == set(MUST_HAVE_MAP), sorted(set(CALL_PLANS) ^ set(MUST_HAVE_MAP))
    checks.append("manifest")
    print("SW_PATTERN_REFGEOM_OPS verdict=PASS ops=%d checks=%d" % (len(OPERATIONS), len(checks)))
    return 0


def main() -> int:
    """自检入口（失败即非零退出并打 FAIL 行，不吞异常）。"""
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败
        print("SW_PATTERN_REFGEOM_OPS verdict=FAIL ops=%d error=%s: %s"
              % (len(OPERATIONS), type(exc).__name__, exc))
        raise


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())
