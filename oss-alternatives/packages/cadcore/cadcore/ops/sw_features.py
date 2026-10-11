#!/usr/bin/env python3
"""cadcore.ops.sw_features —— SolidWorks 基础特征族（拉伸/旋转/扫描/放样/圆角/倒角/孔/抽壳/拔模/筋…）。

一句话用途
----------
把"在一个 L1 文档上加一个基础特征、或改一个已有特征"做成**文档里的一条特征记录**（真源仍是
`docmodel.document_to_l1` 的文本）：18 个操作各返回它落盘的那条记录的 uuid 列表（`list[str]`），
几何后果由 `cadcore.kernel` 从文档现算（拉伸凸台那条门是数值级断言：10mm × 6mm × 5mm）。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_features"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：第一个位置参数是 ``sk_or_doc``（L1 文档 dict、
  ``docmodel.Document`` 对象、或**裸草图 dict** 都吃），其余一律关键字；长度一律**整数纳米（nm）**；
  错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason，文案 ASCII）。

三个可选的对接开关（门按这三个走，不靠别名猜）
-----------------------------------------------
- ``MUST_HAVE_MAP``：must_have key → ``OPERATIONS`` 里的操作名（可读性 + 门不猜）；
- ``CALL_PLANS``：must_have key → 该操作的 kwargs 候选表。**本表只给"自动候选表喂不全参数"的两条**
  （薄壁与所选轮廓都需要深度，而深度不是它们的主参数），其余条目的驱动方式即门里的自动候选表。
  ⚠️ 注意：门对 ``CALL_PLANS`` 的值**不做 ``$SKETCH`` 占位符展开**（展开只发生在自动候选表那条路上），
  所以这里只放**字面量**参数；引用一律靠操作自己的缺省（文档里第一张草图 / 第一个未抑制特征）。

记录口径（写进 ``doc["features"]``，只加键，不动 L1 冻结字段）
--------------------------------------------------------------
每条记录 ``{"uuid", "kind", "inputs", "sketch_uuid", "params", "suppressed"}``：
- ``inputs``：被引用的草图/特征 uuid（拉伸类第一条就是轮廓草图）；
- ``sketch_uuid``：该特征所依据的轮廓草图（切割/圆角/抽壳这类"修饰已有实体"的特征取基体草图）；
- ``params``：决定形状的参数（``distance_nm`` / ``angle_deg`` / ``radius_nm`` / ``thickness_nm`` /
  ``end_condition`` …），非盲写：每条都过本文件的校验器。
- **裸草图 dict** 作为目标时，记录写进该草图自己的 ``features`` 键（就地提升为"单草图文档"），
  这样调用方不需要先造文档。

有界圆角真几何（A9 第 2 阶段：fillet geometry）
--------------------------------------------
``fillet`` 不只是一条参数记录：它逐角调用 ``cadcore.geom.fillet``（裁剪 + 圆弧过渡，
几何算法唯一真源是 ``cadcore.edit.modify.fillet``），并把**算出来的几何**落进
``params.fillet_geometry``：裁剪后的直线 + 真实 ``arc`` 实体、圆心 / 切点 / 扫掠区间、
解析面积减少量与体积减少量、``max_radius_nm``（该选边下的有界半径）与 ``dropped_edges``。
- **有界半径**：切线长之和不得超过被吃掉的边长；超界抛
  ``CadError(reason="radius_exceeds_bound")``（带 ``max_radius_nm`` / ``bound_edge``），
  **绝不静默夹紧**；半径恰等于边界时该边退化为一点，显式记进 ``dropped_edges``。
- **缺省选边**只取"两相邻侧面的竖直棱"（轮廓角）；显式传入上下端面的棱抛
  ``CadError(reason="unsupported_edge_kind")``，被忽略的引用记进 ``unused_refs``。
- 真实体入口：``cadcore.geom.fillet.solid_from_document``（记录里几何未采时抛错，
  **不回落成父实体**）。

持久拓扑引用（A9 第 1 阶段：topo naming）
-----------------------------------------
``fillet`` / ``chamfer`` 的 ``edges`` 与 ``shell`` / ``draft`` 的 ``faces`` **只落持久拓扑引用**
（``cadcore.topo`` 的 ``topo:<kind>:<feature_uuid>:<role>``）：
- 缺省 = 最近一次可求值实体的该 kind 全部引用（**不再回落草图 uuid**）；
- 显式给定必须是能解析的持久引用；草图 / 特征 uuid 一律 ``CadError(reason="not_a_topo_ref")``；
- 记录里同时落 ``edge_refs`` / ``face_refs``（同一组引用，读侧按名取）与 ``topo_status`` /
  ``topo_base_feature``（这组引用来自哪次可求值实体；实体已被后续特征改动时 ``topo_status`` 为
  ``unmeasured``，不隐瞒）。引用与草图 uuid 无关 ⇒ 重建 / 重算 / 改参数后不变。

三态口径（0 = 真为零 / -1 = 未采 / NA = 不适用）
------------------------------------------------
``profile_min_dim_nm`` 这类**量不出来**的量记 ``-1``（未采），绝不用 0 冒充；只有真为零才写 0。
量不出包围盒时只做正性校验，并把 ``-1`` 带进记录，让下游看得见"这条没量"。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
1. **本层不是通用几何求解器**：记录的是设计意图 + 参数；实体由 ``cadcore.kernel`` 现算。
   ``fillet`` 是**唯一例外**：它的裁剪 + 圆弧过渡由 ``cadcore.geom.fillet`` 解析算出并落进
   记录（A9 第 2 阶段），但**不在这里做布尔运算**、也不写回草图；倒角/抽壳/拔模仍只记参数。
2. 坏入参当场 ``CadError``：长度非整数纳米 / 非正、角度越界、未知名引用、没有可修饰的实体、
   厚度不小于轮廓最小边……一律报错，不静默降级。
3. **每次调用只写自己的 features 表**，不碰 sketches / constraints / dimensions / annotations。
4. 非 docstring 的字符串字面量一律 ASCII（``tools/cad/i18n_gate.py`` 口径），文案一律走 key。

自检（``python3 sw_features.py --selftest``）
---------------------------------------------
不写盘：把 ``parity_sw_features_gate.py`` 现装进来，用**本文件的真实现**当干净对照（必须 PASS），
再对真实现的 ``OPERATIONS`` 注入 4 个内存变异体（返回空列表 / 删 manifest 键 / 返回悬空 uuid /
fillet 的 edges 回落草图 uuid），
要求**全红**（E2.5：尺子看得见红，且测的是真件不是桩）。
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])      # …/packages/cadcore
if _PACKAGE_ROOT not in sys.path:                             # 直接 python3 sw_features.py 也能跑
    sys.path.insert(0, _PACKAGE_ROOT)

from cadcore import topo as _topo
from cadcore.errors import CadError
from cadcore.geom import fillet as _fillet_geom
from cadcore.sketch.constraints import new_uuid as _new_uuid

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "MUST_HAVE_MAP",
    "CALL_PLANS",
    "extrude_boss",
    "extrude_cut",
    "revolve_boss",
    "revolve_cut",
    "sweep_boss",
    "loft_boss",
    "fillet",
    "chamfer",
    "hole_wizard",
    "simple_hole",
    "shell",
    "draft",
    "rib",
    "set_end_condition",
    "thin_extrude",
    "select_contours",
    "merge_result",
    "edit_feature",
    "selftest",
]

FAMILY = "sw_features"

# 终止条件白名单（规范化后的键）；`end_condition` / `termination` 两个形参名都收。
END_CONDITIONS = ("blind", "through_all", "through_next", "up_to_next", "up_to_vertex",
                  "up_to_surface", "mid_plane", "offset")
# 孔标准白名单（只做大小写规范化，不校验编号格式）。
HOLE_STANDARDS = ("iso", "ansi", "din", "jis", "gb", "bs", "en")
# 门里的自动候选表会用这些键名塞轮廓/引用；统一按"草图引用"解释。
_REF_KEYS = ("sketch", "sketch_uuid", "profile", "profile_uuid", "sketch_id", "inputs")


# --------------------------------------------------------------------------------------
# 校验器：坏入参当场报错（机器可读 reason）
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, *, field: str, positive: bool = True) -> int:
    """整数纳米。bool / None / 非整数 / 非有限 / 追精度（1.5 nm）一律 CadError。"""
    if isinstance(value, bool) or value is None:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CadError("length must be finite", reason="bad_length", field=field, value=repr(value))
        rounded = int(value)
        if float(rounded) != value:
            raise CadError("length must be whole nanometres", reason="sub_nm_precision",
                           field=field, value=repr(value))
        number = rounded
    elif isinstance(value, int):
        number = value
    else:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if positive and number <= 0:
        raise CadError("length must be positive", reason="non_positive_length", field=field, value=number)
    if not positive and number < 0:
        raise CadError("length must not be negative", reason="negative_length", field=field, value=number)
    return number


def _as_angle(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    """角度（度）：必须有限、``minimum < v <= maximum``。"""
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float)):
        raise CadError("angle must be a number of degrees", reason="bad_angle", field=field, value=repr(value))
    number = float(value)
    if not math.isfinite(number):
        raise CadError("angle must be finite", reason="bad_angle", field=field, value=repr(value))
    if not (minimum < number <= maximum):
        raise CadError("angle out of range", reason="angle_out_of_range", field=field,
                       value=number, minimum=minimum, maximum=maximum)
    return number


def _as_draft_angle(value: Any, *, field: str) -> float:
    """拔模角：``|v| < 89``（90° 是垂直壁，不是拔模）。"""
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float)):
        raise CadError("angle must be a number of degrees", reason="bad_angle", field=field, value=repr(value))
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 89.0:
        raise CadError("draft angle must satisfy |angle| < 89 degrees",
                       reason="angle_out_of_range", field=field, value=repr(value))
    return number


def _as_flag(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    raise CadError("flag must be a bool", reason="bad_flag", field=field, value=repr(value))


def _as_name(value: Any, *, field: str, allowed: Sequence[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError("expected a non-empty name", reason="bad_name", field=field, value=repr(value))
    name = value.strip().lower().replace("-", "_").replace(" ", "_")
    if allowed is not None and name not in allowed:
        raise CadError("unknown name", reason="unknown_name", field=field, value=name, allowed=list(allowed))
    return name


def _as_uuid_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError("expected a uuid string", reason="bad_ref", field=field, value=repr(value))
    return value.strip()


def _first(*pairs: tuple[Any, str], required: bool = False, kind: str = "length",
           positive: bool = True) -> Any:
    """取第一个非 None 的候选（形参别名），全 None 时按 ``required`` 报错。"""
    for value, field in pairs:
        if value is not None:
            if kind == "length":
                return _as_nm(value, field=field, positive=positive)
            raise CadError("internal: unknown kind", reason="internal", kind=kind)
    if required:
        raise CadError("missing parameter", reason="missing_parameter", fields=[f for _v, f in pairs])
    return None


def _no_extra(refs: Mapping[str, Any], *, op: str) -> None:
    if refs:
        raise CadError("unexpected parameter(s)", reason="unexpected_kwarg", op=op, keys=sorted(refs))


# --------------------------------------------------------------------------------------
# 目标适配：L1 dict / Document 对象 / 裸草图 dict
# --------------------------------------------------------------------------------------


class _View:
    """一次调用的写入面：``sketches`` 只读、``features`` 可追加。"""

    __slots__ = ("target", "sketches", "features")

    def __init__(self, target: Any, sketches: list[Any], features: list[Any]) -> None:
        self.target = target
        self.sketches = sketches
        self.features = features


def _view(target: Any) -> _View:
    if isinstance(target, Mapping):
        has_doc = "sketches" in target or "features" in target
        has_sketch = "entities" in target or "plane" in target
        if not has_doc and not has_sketch:
            raise CadError("target is neither a document nor a sketch", reason="bad_document",
                           keys=sorted(str(k) for k in target)[:8])
        features = target.get("features")
        if features is None:
            features = []
            target["features"] = features          # 就地写：调用方手里那份也看得见
        elif not isinstance(features, list):
            raise CadError("features must be a list", reason="bad_features_table",
                           got=type(features).__name__)
        sketches = list(target.get("sketches") or []) if has_doc else [target]
        if not has_doc:
            sketches = [target]
        return _View(target, sketches, features)
    sketches = getattr(target, "sketches", None)
    features = getattr(target, "features", None)
    if isinstance(sketches, list) and isinstance(features, list):
        return _View(target, list(sketches), features)
    raise CadError("target is neither a document nor a sketch", reason="bad_document",
                   got=type(target).__name__)


def _uuid_of(node: Any) -> str:
    if isinstance(node, Mapping):
        return str(node.get("uuid") or "")
    return str(getattr(node, "uuid", "") or "")


def _sketch_index(view: _View) -> dict[str, Any]:
    return {_uuid_of(item): item for item in view.sketches if _uuid_of(item)}


def _feature_index(view: _View) -> dict[str, Any]:
    return {_uuid_of(item): item for item in view.features if _uuid_of(item)}


def _pop_ref(refs: dict[str, Any], sketch: Any) -> Any:
    if sketch is not None:
        return sketch
    for key in _REF_KEYS:
        if key in refs:
            return refs.pop(key)
    return None


def _resolve_sketch(view: _View, ref: Any = None) -> str:
    """轮廓草图 uuid：显式引用必须存在；未给则取文档第一张草图。"""
    index = _sketch_index(view)
    if ref is None:
        for item in view.sketches:
            return _uuid_of(item)
        raise CadError("document has no sketch to use as a profile", reason="no_sketch")
    if isinstance(ref, (list, tuple)):
        if len(ref) != 1:
            raise CadError("expected exactly one sketch reference", reason="bad_ref",
                           field="sketch", count=len(ref))
        ref = ref[0]
    text = _as_uuid_text(ref, field="sketch")
    if text not in index:
        raise CadError("unknown sketch reference", reason="unknown_sketch", sketch=text,
                       known=sorted(index)[:8])
    return text


def _topo_refs(view: _View, *, kind: str, explicit: Any, field: str, what: str) -> list[str]:
    """一组**持久拓扑引用**（cadcore.topo 的 ref）。

    缺省（explicit 为 None）取"最近一次可求值实体"的该 kind 全部引用；显式给定则逐条必须
    解析得到。草图 uuid / 特征 uuid **不是**拓扑引用：传它们一律 CadError（reason=not_a_topo_ref），
    不做"看起来能用"的静默回落（A9 的原始缺陷正是 edges 落成草图 uuid）。
    """
    if explicit is None:
        body = _topo.known_refs(view.target, kind=kind)
        if not body.refs:
            raise CadError("no evaluable body topology to reference", reason="topo_unavailable",
                           field=field, operation=what, body_status=body.status, body_reason=body.reason)
        return list(body.refs)
    values = [explicit] if isinstance(explicit, str) else explicit
    if not isinstance(values, (list, tuple)) or not values:
        raise CadError("expected a list of persistent topology references", reason="bad_ref",
                       field=field, got=repr(explicit)[:80])
    topology = _topo.rebuild(view.target)
    out: list[str] = []
    for item in values:
        text = item if isinstance(item, str) else repr(item)
        if not _topo.is_kind_ref(text, kind):
            raise CadError("reference is not a persistent topology reference", reason="not_a_topo_ref",
                           field=field, ref=text[:80])
        if topology.resolve(text) is None:
            raise CadError("unknown topology reference", reason="unknown_ref", field=field, ref=text)
        out.append(text)
    return out


def _resolve_refs(view: _View, values: Any, *, field: str, minimum: int = 1) -> list[str]:
    """一组引用（草图或特征 uuid）：逐条必须存在，否则 CadError。"""
    if values is None:
        raise CadError("missing references", reason="missing_parameter", fields=[field])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or len(values) < minimum:
        raise CadError("expected a list of references", reason="bad_ref", field=field,
                       got=repr(values)[:80], minimum=minimum)
    known = set(_sketch_index(view)) | set(_feature_index(view))
    out: list[str] = []
    for item in values:
        text = _as_uuid_text(item, field=field)
        if text not in known:
            raise CadError("unknown reference", reason="unknown_ref", field=field, ref=text)
        out.append(text)
    return out


# --------------------------------------------------------------------------------------
# 轮廓量测：包围盒最小/最大边（nm）；量不出来记 -1（未采），不用 0 冒充
# --------------------------------------------------------------------------------------


def _point_of(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        x, y = value.get("x"), value.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return float(x), float(y)
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value[0], value[1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                and not isinstance(x, bool) and not isinstance(y, bool):
            return float(x), float(y)
    return None


_POINT_KEYS = ("p1", "p2", "p1_nm", "p2_nm", "center", "center_nm", "start", "end", "a", "b")


def _num_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _polar(cx: float, cy: float, r: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    return cx + r * math.cos(radians), cy + r * math.sin(radians)


def _arc_extent_points(cx: float, cy: float, r: float, start_deg: float,
                       end_deg: float) -> list[tuple[float, float]]:
    """弧的极值候选点：两端点 + 落在扫掠范围内的四个象限点（够定包围盒，不用离散采样）。"""
    span = (end_deg - start_deg) % 360.0
    if span == 0.0:
        span = 360.0
    points = [_polar(cx, cy, r, start_deg), _polar(cx, cy, r, end_deg)]
    for quadrant in range(4):
        angle = 90.0 * quadrant
        if ((angle - start_deg) % 360.0) <= span:
            points.append(_polar(cx, cy, r, angle))
    return points


def _entity_geometry_points(entity: Mapping[str, Any]) -> list[tuple[float, float]]:
    """L1 实体（``kind`` + ``geom``）→ 参与包围盒的点；不认识的实体返回空表。

    L1 的几何是 ``geom.{x1,y1,x2,y2} / {cx,cy,r} / {points}``（与 ``tests/data/plate.json``
    同构），**不是** ``p1``/``p2`` 点对。不按实体类型取点，整份 L1 文档的轮廓都会被记成
    "量不出"（-1），于是半径/壁厚/筋厚/孔径的轮廓校验会集体变成死代码（静默不校验）。
    """
    if bool(entity.get("construction")):
        return []
    kind = str(entity.get("kind") or "").strip().lower()
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        return []
    if kind == "line":
        x1, y1 = _num_or_none(geom.get("x1")), _num_or_none(geom.get("y1"))
        x2, y2 = _num_or_none(geom.get("x2")), _num_or_none(geom.get("y2"))
        if None in (x1, y1, x2, y2):
            return []
        return [(x1, y1), (x2, y2)]
    if kind == "circle":
        cx, cy, r = _num_or_none(geom.get("cx")), _num_or_none(geom.get("cy")), _num_or_none(geom.get("r"))
        if None in (cx, cy, r) or r <= 0:
            return []
        return [_polar(cx, cy, r, angle) for angle in (0.0, 90.0, 180.0, 270.0)]
    if kind == "arc":
        cx, cy, r = _num_or_none(geom.get("cx")), _num_or_none(geom.get("cy")), _num_or_none(geom.get("r"))
        start, end = _num_or_none(geom.get("start_deg")), _num_or_none(geom.get("end_deg"))
        if None in (cx, cy, r) or r <= 0:
            return []
        return _arc_extent_points(cx, cy, r, start or 0.0, end or 0.0)
    if kind == "polyline":
        raw = geom.get("points") or geom.get("vertices") or []
        points: list[tuple[float, float]] = []
        for item in raw:
            point = _point_of(item)
            if point is not None:
                points.append(point)
        return points
    return []


def _bbox_of(node: Any) -> tuple[float, float, float, float] | None:
    """点集包围盒：既认 ``p1``/``p2`` 这类点对，也认 L1 实体的 ``geom``。"""
    points: list[tuple[float, float]] = []
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key in _POINT_KEYS:
                point = _point_of(current.get(key))
                if point is not None:
                    points.append(point)
            points.extend(_entity_geometry_points(current))
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _profile_dims_nm(view: _View, sketch_uuid: str) -> tuple[int, int]:
    """``(最小边, 最大边)``；量不出来 ``(-1, -1)``（未采，不是 0）。"""
    for item in view.sketches:
        if _uuid_of(item) != sketch_uuid:
            continue
        box = _bbox_of(item)
        if box is None:
            return -1, -1
        width_nm = int(round(box[2] - box[0]))
        height_nm = int(round(box[3] - box[1]))
        dims = sorted(d for d in (width_nm, height_nm) if d > 0)
        if not dims:
            return -1, -1
        return dims[0], dims[-1]
    return -1, -1


def _guard_dimension(value_nm: int, dims: tuple[int, int], *, field: str, note: str) -> dict[str, Any]:
    """尺寸必须小于轮廓对应边；量不出（-1）时只留痕、不假装验过。"""
    smallest, largest = dims
    if smallest <= 0:
        return {f"{field}_profile_check": "unmeasured", "profile_min_dim_nm": -1}
    if value_nm >= smallest:
        raise CadError(note, reason="dimension_exceeds_profile", field=field,
                       value_nm=value_nm, profile_min_dim_nm=smallest)
    return {f"{field}_profile_check": "ok", "profile_min_dim_nm": smallest,
            "profile_max_dim_nm": largest}


# --------------------------------------------------------------------------------------
# 记录落地
# --------------------------------------------------------------------------------------


def _emit(view: _View, *, kind: str, sketch_uuid: str, inputs: Iterable[Any],
          params: Mapping[str, Any]) -> list[str]:
    record = {"uuid": _new_uuid(), "kind": kind,
              "inputs": [str(item) for item in inputs if item],
              "sketch_uuid": str(sketch_uuid or ""), "params": dict(params), "suppressed": False}
    view.features.append(record)
    return [record["uuid"]]


def _body_features(view: _View, *, required: bool, what: str) -> list[Any]:
    live = [item for item in view.features if not bool(
        item.get("suppressed") if isinstance(item, Mapping) else getattr(item, "suppressed", False))]
    if required and not live:
        raise CadError("no body to modify", reason="no_body", operation=what)
    return live


# --------------------------------------------------------------------------------------
# 18 个操作
# --------------------------------------------------------------------------------------


def extrude_boss(target: Any, distance_nm: Any = None, *, depth_nm: Any = None, sketch: Any = None,
                 symmetric: Any = False, end_condition: Any = "blind", merge: Any = True,
                 **refs: Any) -> list[str]:
    """拉伸凸台/基体：以轮廓草图拉出实体；返回新特征记录 uuid。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="extrude_boss")
    depth = _first((distance_nm, "distance_nm"), (depth_nm, "depth_nm"), required=True)
    condition = _as_name(end_condition, field="end_condition", allowed=END_CONDITIONS)
    params: dict[str, Any] = {"distance_nm": depth, "symmetric": _as_flag(symmetric, field="symmetric"),
                              "end_condition": condition, "op": "new",
                              "merge": _as_flag(merge, field="merge"),
                              "profile_min_dim_nm": _profile_dims_nm(view, profile)[0]}
    return _emit(view, kind="extrude", sketch_uuid=profile, inputs=[profile], params=params)


def extrude_cut(target: Any, distance_nm: Any = None, *, depth_nm: Any = None, sketch: Any = None,
                op: Any = "cut", through_all: Any = False, flip: Any = False, **refs: Any) -> list[str]:
    """拉伸切除：必须已有实体才切；返回新特征记录 uuid（params.op="cut"）。"""
    view = _view(target)
    body = _body_features(view, required=True, what="extrude_cut")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="extrude_cut")
    depth = _first((distance_nm, "distance_nm"), (depth_nm, "depth_nm"), required=True)
    params = {"distance_nm": depth, "op": "cut", "feature_op": _as_name(op, field="op", allowed=("cut", "pocket")),
              "through_all": _as_flag(through_all, field="through_all"),
              "flip": _as_flag(flip, field="flip"),
              "profile_min_dim_nm": _profile_dims_nm(view, profile)[0]}
    return _emit(view, kind="extrude", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def revolve_boss(target: Any, angle_deg: Any = 360.0, *, angle: Any = None, axis: Any = "x",
                 sketch: Any = None, merge: Any = True, **refs: Any) -> list[str]:
    """旋转凸台/基体：轮廓绕轴旋转 ``0 < angle_deg <= 360``。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="revolve_boss")
    value = angle_deg if angle_deg is not None else angle
    degrees = _as_angle(value, field="angle_deg", minimum=0.0, maximum=360.0)
    if axis is None:
        axis_name = "x"
    else:
        text = _as_uuid_text(axis, field="axis")
        axis_name = text.lower() if text.lower() in ("x", "y", "z") else text
    params = {"angle_deg": degrees, "axis": axis_name, "op": "new",
              "merge": _as_flag(merge, field="merge")}
    return _emit(view, kind="revolve", sketch_uuid=profile, inputs=[profile], params=params)


def revolve_cut(target: Any, angle_deg: Any = 360.0, *, angle: Any = None, axis: Any = "x",
                sketch: Any = None, groove: Any = False, **refs: Any) -> list[str]:
    """旋转切除：必须已有实体；``groove=True`` 记成滚花槽。"""
    view = _view(target)
    body = _body_features(view, required=True, what="revolve_cut")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="revolve_cut")
    value = angle_deg if angle_deg is not None else angle
    degrees = _as_angle(value, field="angle_deg", minimum=0.0, maximum=360.0)
    axis_name = _as_name(axis, field="axis", allowed=("x", "y", "z"))
    params = {"angle_deg": degrees, "axis": axis_name, "op": "cut",
              "groove": _as_flag(groove, field="groove")}
    return _emit(view, kind="revolve", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def sweep_boss(target: Any, path: Any = None, *, path_uuid: Any = None, sweep_path: Any = None,
               sketch: Any = None, twist_deg: Any = 0.0, **refs: Any) -> list[str]:
    """扫描凸台/基体：轮廓沿路径扫出；``path`` 与轮廓都必须是文档里已有的引用。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="sweep_boss")
    raw = path if path is not None else (path_uuid if path_uuid is not None else sweep_path)
    guide = _resolve_refs(view, raw, field="path")[0]
    if not isinstance(twist_deg, (int, float)) or isinstance(twist_deg, bool) or not math.isfinite(float(twist_deg)):
        raise CadError("twist must be a finite number of degrees", reason="bad_angle",
                       field="twist_deg", value=repr(twist_deg))
    params = {"path": guide, "sweep_path": guide, "twist_deg": float(twist_deg), "op": "new"}
    return _emit(view, kind="sweep", sketch_uuid=profile, inputs=[profile, guide], params=params)


def loft_boss(target: Any, sections: Any = None, *, profiles: Any = None, section_uuids: Any = None,
              sketch: Any = None, closed: Any = False, **refs: Any) -> list[str]:
    """放样凸台/基体：``sections`` 至少 2 个轮廓，逐条必须是文档里已有的引用。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="loft_boss")
    raw = sections if sections is not None else (profiles if profiles is not None else section_uuids)
    guides = _resolve_refs(view, raw, field="sections", minimum=2)
    params = {"sections": guides, "profiles": guides, "closed": _as_flag(closed, field="closed"), "op": "new"}
    return _emit(view, kind="loft", sketch_uuid=profile, inputs=guides, params=params)


def fillet(target: Any, radius_nm: Any = None, *, radius: Any = None, edges: Any = None,
           feature_uuid: Any = None, **refs: Any) -> list[str]:
    """圆角：在选中的实体角边上做半径 ``radius_nm`` 的圆角 —— **真几何**（裁剪 + 圆弧过渡）。

    缺省选边 = 该实体全部"两相邻侧面的竖直棱"（对应的轮廓角）；显式 ``edges`` 里出现上下
    端面的棱一律 ``CadError(reason="unsupported_edge_kind")``（本层只做轮廓角，不近似）。
    半径受**有界半径**约束（相邻两角的切线长之和不超过被吃掉的边长）：超界抛
    ``CadError(reason="radius_exceeds_bound")``（带 ``max_radius_nm``/``bound_edge``），
    **绝不静默夹紧**；半径恰好等于边界时被吃光的边显式记进 ``dropped_edges``。
    记录里落 ``fillet_geometry``（圆心 / 切点 / 真实 arc 实体 / 面积与体积变化），可由
    ``cadcore.geom.fillet.solid_from_document`` 现算出**真实体**。
    """
    view = _view(target)
    body = _body_features(view, required=True, what="fillet")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="fillet")
    value = _first((radius_nm, "radius_nm"), (radius, "radius"), required=True)
    picked = _topo_refs(view, kind="edge", explicit=edges, field="edges", what="fillet")
    body_topo = _topo.known_refs(view.target, kind="edge")
    guarded = _guard_dimension(value, _profile_dims_nm(view, profile), field="radius_nm",
                               note="fillet radius must be smaller than the profile edge")
    plan = _fillet_geom.plan_from_document(view.target, refs=picked, radius_nm=value,
                                           sketch_uuid=profile, require_all_refs=edges is not None)
    params: dict[str, Any] = {"radius_nm": value, "edges": list(plan.refs_used),
                              "edge_refs": list(plan.refs_used),
                              "unused_refs": list(plan.ignored_refs),
                              "topo_status": body_topo.status,
                              "topo_base_feature": body_topo.feature_uuid,
                              "feature_uuid": str(feature_uuid or _uuid_of(body[-1])),
                              "fillet_geometry": plan.as_dict(),
                              "fillet_max_radius_nm": plan.max_radius_nm,
                              "fillet_arc_count": plan.arc_count,
                              "fillet_area_removed_nm2": plan.area_removed_nm2,
                              "fillet_volume_removed_nm3": plan.volume_removed_nm3}
    params.update(guarded)
    return _emit(view, kind="fillet", sketch_uuid=profile, inputs=[params["feature_uuid"], profile], params=params)


def chamfer(target: Any, distance_nm: Any = None, *, distance: Any = None, chamfer_nm: Any = None,
            edges: Any = None, feature_uuid: Any = None, **refs: Any) -> list[str]:
    """倒角：``distance_nm`` 必须小于轮廓最小边（量得出时）。"""
    view = _view(target)
    body = _body_features(view, required=True, what="chamfer")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="chamfer")
    value = _first((distance_nm, "distance_nm"), (distance, "distance"), (chamfer_nm, "chamfer_nm"),
                   required=True)
    picked = _topo_refs(view, kind="edge", explicit=edges, field="edges", what="chamfer")
    body_topo = _topo.known_refs(view.target, kind="edge")
    params: dict[str, Any] = {"distance_nm": value, "edges": picked, "edge_refs": list(picked),
                              "topo_status": body_topo.status,
                              "topo_base_feature": body_topo.feature_uuid,
                              "feature_uuid": str(feature_uuid or _uuid_of(body[-1]))}
    params.update(_guard_dimension(value, _profile_dims_nm(view, profile), field="distance_nm",
                                   note="chamfer distance must be smaller than the profile edge"))
    return _emit(view, kind="chamfer", sketch_uuid=profile, inputs=[params["feature_uuid"], profile], params=params)


def hole_wizard(target: Any, *, standard: Any = "iso", diameter_nm: Any = None, depth_nm: Any = None,
                hole_type: Any = "counterbore", thread: Any = None, drill_depth_nm: Any = None,
                sketch: Any = None, **refs: Any) -> list[str]:
    """异型孔向导：带标准/规格/螺纹的孔（柱形沉孔 / 锥形沉孔 / 螺纹孔）。"""
    view = _view(target)
    body = _body_features(view, required=True, what="hole_wizard")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="hole_wizard")
    diameter = _first((diameter_nm, "diameter_nm"), required=True)
    depth = _first((depth_nm, "depth_nm"), required=True)
    kind = _as_name(hole_type, field="hole_type",
                    allowed=("simple", "counterbore", "countersink", "tapped", "threaded"))
    dims = _profile_dims_nm(view, profile)
    if dims[1] > 0 and (diameter > dims[1] or depth > dims[1]):
        raise CadError("hole does not fit the profile bounding box", reason="dimension_exceeds_profile",
                       diameter_nm=diameter, depth_nm=depth, profile_max_dim_nm=dims[1])
    params = {"standard": _as_name(standard, field="standard", allowed=HOLE_STANDARDS), "spec": kind,
              "hole_type": kind, "diameter_nm": diameter, "depth_nm": depth,
              "drill_depth_nm": _first((drill_depth_nm, "drill_depth_nm")) if drill_depth_nm is not None else depth,
              "thread": None if thread is None else _as_name(thread, field="thread"),
              "feature_uuid": _uuid_of(body[-1]),
              "profile_max_dim_nm": dims[1]}
    return _emit(view, kind="hole", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def simple_hole(target: Any, *, diameter_nm: Any = None, depth_nm: Any = None, through_all: Any = False,
                sketch: Any = None, **refs: Any) -> list[str]:
    """简单直孔：只用直径 + 深度（或通孔）的圆柱孔。"""
    view = _view(target)
    body = _body_features(view, required=True, what="simple_hole")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="simple_hole")
    diameter = _first((diameter_nm, "diameter_nm"), required=True)
    through = _as_flag(through_all, field="through_all")
    depth = _first((depth_nm, "depth_nm"), required=not through)
    dims = _profile_dims_nm(view, profile)
    if dims[1] > 0 and diameter > dims[1]:
        raise CadError("hole does not fit the profile bounding box", reason="dimension_exceeds_profile",
                       diameter_nm=diameter, profile_max_dim_nm=dims[1])
    params = {"diameter_nm": diameter, "depth_nm": depth, "hole_type": "simple",
              "through_all": through, "feature_uuid": _uuid_of(body[-1]),
              "profile_max_dim_nm": dims[1]}
    return _emit(view, kind="hole", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def shell(target: Any, thickness_nm: Any = None, *, thickness: Any = None, faces: Any = None,
          outward: Any = False, **refs: Any) -> list[str]:
    """抽壳：壁厚必须小于轮廓最小边；``faces`` 缺省 = 全部面（闭合抽壳）。"""
    view = _view(target)
    body = _body_features(view, required=True, what="shell")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="shell")
    value = _first((thickness_nm, "thickness_nm"), (thickness, "thickness"), required=True)
    picked = _topo_refs(view, kind="face", explicit=faces, field="faces", what="shell")
    body_topo = _topo.known_refs(view.target, kind="face")
    params: dict[str, Any] = {"thickness_nm": value, "faces": picked, "face_refs": list(picked),
                              "topo_status": body_topo.status,
                              "topo_base_feature": body_topo.feature_uuid,
                              "outward": _as_flag(outward, field="outward"),
                              "feature_uuid": _uuid_of(body[-1])}
    params.update(_guard_dimension(value, _profile_dims_nm(view, profile), field="thickness_nm",
                                   note="wall thickness must be smaller than the profile edge"))
    return _emit(view, kind="shell", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def draft(target: Any, angle_deg: Any = None, *, angle: Any = None, neutral_plane: Any = "XY",
          faces: Any = None, feature_uuid: Any = None, **refs: Any) -> list[str]:
    """拔模：``|angle_deg| < 89``，中性面缺省 XY。"""
    view = _view(target)
    body = _body_features(view, required=True, what="draft")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="draft")
    value = angle_deg if angle_deg is not None else angle
    degrees = _as_draft_angle(value, field="angle_deg")
    plane = "XY" if neutral_plane is None else _as_uuid_text(neutral_plane, field="neutral_plane")
    picked = _topo_refs(view, kind="face", explicit=faces, field="faces", what="draft")
    body_topo = _topo.known_refs(view.target, kind="face")
    params = {"angle_deg": degrees, "draft_angle_deg": degrees, "neutral_plane": plane, "faces": picked,
              "face_refs": list(picked), "topo_status": body_topo.status,
              "topo_base_feature": body_topo.feature_uuid,
              "feature_uuid": str(feature_uuid or _uuid_of(body[-1]))}
    return _emit(view, kind="draft", sketch_uuid=profile, inputs=[params["feature_uuid"], profile], params=params)


def rib(target: Any, thickness_nm: Any = None, *, thickness: Any = None, direction: Any = "normal",
        sketch: Any = None, **refs: Any) -> list[str]:
    """加强筋：筋厚必须小于轮廓最小边（量得出时）。"""
    view = _view(target)
    body = _body_features(view, required=True, what="rib")
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="rib")
    value = _first((thickness_nm, "thickness_nm"), (thickness, "thickness"), required=True)
    params: dict[str, Any] = {"thickness_nm": value, "rib_thickness_nm": value,
                              "direction": _as_name(direction, field="direction",
                                                    allowed=("normal", "parallel", "tangent")),
                              "feature_uuid": _uuid_of(body[-1])}
    params.update(_guard_dimension(value, _profile_dims_nm(view, profile), field="thickness_nm",
                                   note="rib thickness must be smaller than the profile edge"))
    return _emit(view, kind="rib", sketch_uuid=profile, inputs=[_uuid_of(body[-1]), profile], params=params)


def set_end_condition(target: Any, end_condition: Any = None, *, termination: Any = None,
                      feature_uuid: Any = None, distance_nm: Any = None, sketch: Any = None,
                      **refs: Any) -> list[str]:
    """终止条件：套在已有拉伸特征上（返回该特征 uuid）；没有可套的特征时就地落一条拉伸记录。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="set_end_condition")
    raw = end_condition if end_condition is not None else termination
    condition = _as_name(raw, field="end_condition", allowed=END_CONDITIONS)
    depth = _first((distance_nm, "distance_nm"), required=condition == "blind")
    index = _feature_index(view)
    target_uuid = _as_uuid_text(feature_uuid, field="feature_uuid") if feature_uuid is not None else ""
    if target_uuid and target_uuid not in index:
        raise CadError("unknown feature reference", reason="unknown_feature", feature_uuid=target_uuid)
    if not target_uuid:
        live = _body_features(view, required=False, what="set_end_condition")
        target_uuid = _uuid_of(live[-1]) if live else ""
    if target_uuid:
        record = index[target_uuid]
        if not isinstance(record, Mapping):
            raise CadError("feature record is not a mapping", reason="bad_features_table", uuid=target_uuid)
        params = record.setdefault("params", {})
        params["end_condition"] = condition
        params["termination"] = condition
        if depth is not None:
            params["distance_nm"] = depth
        params["extent"] = condition
        return [target_uuid]
    params = {"end_condition": condition, "termination": condition, "extent": condition,
              "distance_nm": depth, "op": "new", "end_condition_materialised": True}
    return _emit(view, kind="extrude", sketch_uuid=profile, inputs=[profile], params=params)


def thin_extrude(target: Any, thickness_nm: Any = None, *, thickness: Any = None, distance_nm: Any = None,
                 depth_nm: Any = None, sketch: Any = None, wall_side: Any = "inside",
                 **refs: Any) -> list[str]:
    """薄壁拉伸：壁厚 + 深度都给；壁厚必须小于轮廓最小边（量得出时）。"""
    view = _view(target)
    profile = _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="thin_extrude")
    wall = _first((thickness_nm, "thickness_nm"), (thickness, "thickness"), required=True)
    depth = _first((distance_nm, "distance_nm"), (depth_nm, "depth_nm"), required=True)
    params: dict[str, Any] = {"thin": True, "thickness_nm": wall, "distance_nm": depth,
                              "wall_side": _as_name(wall_side, field="wall_side",
                                                    allowed=("inside", "outside", "mid")),
                              "op": "new"}
    params.update(_guard_dimension(wall, _profile_dims_nm(view, profile), field="thickness_nm",
                                   note="thin wall must be smaller than the profile edge"))
    return _emit(view, kind="extrude", sketch_uuid=profile, inputs=[profile], params=params)


def select_contours(target: Any, contours: Any = None, *, contour_uuids: Any = None, selected: Any = None,
                    profile_uuids: Any = None, distance_nm: Any = None, depth_nm: Any = None,
                    sketch: Any = None, **refs: Any) -> list[str]:
    """所选轮廓：用调用方点名的轮廓集（缺省 = 文档全部草图）做一次拉伸。"""
    view = _view(target)
    _resolve_sketch(view, _pop_ref(refs, sketch))
    _no_extra(refs, op="select_contours")
    raw = contours if contours is not None else (contour_uuids if contour_uuids is not None
                                                 else (selected if selected is not None else profile_uuids))
    if raw is None:
        raw = [_uuid_of(item) for item in view.sketches]
    picked = _resolve_refs(view, raw, field="contours")
    depth = _first((distance_nm, "distance_nm"), (depth_nm, "depth_nm"), required=True)
    params = {"contours": picked, "contour_uuids": picked, "selected": picked, "distance_nm": depth,
              "op": "new"}
    return _emit(view, kind="extrude", sketch_uuid=picked[0], inputs=picked, params=params)


def merge_result(target: Any, merge: Any = True, *, merge_result: Any = None, multibody: Any = False,
                 new_body: Any = False, feature_uuid: Any = None, **refs: Any) -> list[str]:
    """合并结果/多实体：把已有实体特征并成一条布尔记录（``new_body=True`` = 不合并、留多实体）。"""
    view = _view(target)
    live = _body_features(view, required=True, what="merge_result")
    index = _feature_index(view)
    if feature_uuid is not None:
        wanted = _as_uuid_text(feature_uuid, field="feature_uuid")
        if wanted not in index:
            raise CadError("unknown feature reference", reason="unknown_feature", feature_uuid=wanted)
        sources = [wanted] + [_uuid_of(item) for item in live if _uuid_of(item) != wanted]
    else:
        sources = [_uuid_of(item) for item in live]
    profile = _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="merge_result")
    flags = {"merge": _as_flag(merge, field="merge"), "multibody": _as_flag(multibody, field="multibody"),
             "new_body": _as_flag(new_body, field="new_body")}
    flags["merge_result"] = flags["merge"] if merge_result is None else _as_flag(merge_result, field="merge_result")
    params = dict(flags, sources=sources, source_count=len(sources), op="boolean")
    return _emit(view, kind="boolean", sketch_uuid=profile, inputs=sources, params=params)


def edit_feature(target: Any, feature_uuid: Any = None, *, uuid: Any = None, distance_nm: Any = None,
                 depth_nm: Any = None, suppress: Any = None, reorder_to: Any = None,
                 rename: Any = None, **refs: Any) -> list[str]:
    """特征编辑与回退：改已有特征的参数 / 抑制 / 重排；返回**被改的那条**记录 uuid。"""
    view = _view(target)
    _resolve_sketch(view, _pop_ref(refs, sketch=None))
    _no_extra(refs, op="edit_feature")
    index = _feature_index(view)
    wanted = feature_uuid if feature_uuid is not None else uuid
    if wanted is not None:
        target_uuid = _as_uuid_text(wanted, field="feature_uuid")
        if target_uuid not in index:
            raise CadError("unknown feature reference", reason="unknown_feature", feature_uuid=target_uuid)
    else:
        live = _body_features(view, required=True, what="edit_feature")
        target_uuid = _uuid_of(live[-1])
    record = index[target_uuid]
    if not isinstance(record, Mapping):
        raise CadError("feature record is not a mapping", reason="bad_features_table", uuid=target_uuid)
    changes: dict[str, Any] = {}
    depth = _first((distance_nm, "distance_nm"), (depth_nm, "depth_nm"))
    if depth is not None:
        changes["distance_nm"] = depth
    if suppress is not None:
        record["suppressed"] = _as_flag(suppress, field="suppress")
        changes["suppressed"] = record["suppressed"]
    if reorder_to is not None:
        if isinstance(reorder_to, bool) or not isinstance(reorder_to, int) or reorder_to < 0:
            raise CadError("reorder_to must be a non-negative int", reason="bad_index", value=repr(reorder_to))
        position = min(int(reorder_to), len(view.features) - 1)
        if isinstance(view.features[position], Mapping) and _uuid_of(view.features[position]) == target_uuid:
            changes["reorder_to"] = position
        else:
            view.features.remove(record)
            view.features.insert(position, record)
            changes["reorder_to"] = position
    if rename is not None:
        changes["name"] = _as_name(rename, field="rename")
    if not changes:
        raise CadError("nothing to edit: give one of distance_nm/suppress/reorder_to/rename",
                       reason="nothing_to_edit", feature_uuid=target_uuid)
    params = record.setdefault("params", {})
    params.update(changes)
    params["edited"] = True
    params["edit_count"] = int(params.get("edit_count") or 0) + 1
    params.setdefault("edit_log", []).append(dict(changes))
    return [target_uuid]


# --------------------------------------------------------------------------------------
# 清单（门按这两个表驱动，不猜别名）
# --------------------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "extrude_boss": extrude_boss,
    "extrude_cut": extrude_cut,
    "revolve_boss": revolve_boss,
    "revolve_cut": revolve_cut,
    "sweep_boss": sweep_boss,
    "loft_boss": loft_boss,
    "fillet": fillet,
    "chamfer": chamfer,
    "hole_wizard": hole_wizard,
    "simple_hole": simple_hole,
    "shell": shell,
    "draft": draft,
    "rib": rib,
    "set_end_condition": set_end_condition,
    "thin_extrude": thin_extrude,
    "select_contours": select_contours,
    "merge_result": merge_result,
    "edit_feature": edit_feature,
}

# must_have key（门里的 spec key）→ OPERATIONS 键
MUST_HAVE_MAP: dict[str, str] = {
    "boss_extrude": "extrude_boss",
    "cut_extrude": "extrude_cut",
    "boss_revolve": "revolve_boss",
    "cut_revolve": "revolve_cut",
    "boss_sweep": "sweep_boss",
    "boss_loft": "loft_boss",
    "fillet": "fillet",
    "chamfer": "chamfer",
    "hole_wizard": "hole_wizard",
    "simple_hole": "simple_hole",
    "shell": "shell",
    "draft": "draft",
    "rib": "rib",
    "end_condition": "set_end_condition",
    "thin_feature": "thin_extrude",
    "selected_contours": "select_contours",
    "merge_result": "merge_result",
    "edit_feature": "edit_feature",
}

# 只给"自动候选表喂不全参数"的两条补齐 kwargs（深度不是它们的主参数）；值必须是字面量。
CALL_PLANS: dict[str, list[dict[str, Any]]] = {
    "thin_feature": [{"thickness_nm": 1_000_000, "distance_nm": 4_000_000}],
    "selected_contours": [{"distance_nm": 5_000_000}],
}


# --------------------------------------------------------------------------------------
# 自检：真实现当干净对照 + 3 个内存变异体必须全红（不写盘）
# --------------------------------------------------------------------------------------


def _gate_path() -> Path:
    return Path(__file__).resolve().parents[4] / "tools" / "cad" / "parity_sw_features_gate.py"


def _load_gate() -> Any:
    path = _gate_path()
    if not path.is_file():
        raise CadError("gate script is absent", reason="gate_absent", path=str(path))
    spec = importlib.util.spec_from_file_location("parity_sw_features_gate_selftest", path)
    if spec is None or spec.loader is None:
        raise CadError("gate script is not loadable", reason="gate_not_loadable", path=str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _namespace() -> Any:
    return types.SimpleNamespace(FAMILY=FAMILY, OPERATIONS=dict(OPERATIONS),
                                 MUST_HAVE_MAP=dict(MUST_HAVE_MAP),
                                 CALL_PLANS={key: [dict(plan) for plan in value]
                                             for key, value in CALL_PLANS.items()})


def _wrap(fn: Callable[..., Any], transform: Callable[[Any], Any]) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return transform(fn(*args, **kwargs))

    return wrapper


def _mut_empty_return(ns: Any, gate: Any) -> bool:
    ns.OPERATIONS["extrude_boss"] = _wrap(ns.OPERATIONS["extrude_boss"], lambda _out: [])
    return True


def _mut_drop_manifest_key(ns: Any, gate: Any) -> bool:
    """删 manifest 键：``OPERATIONS["extrude_boss"]``（操作名）与 ``MUST_HAVE_MAP["boss_extrude"]``
    （must_have key）两处都删，并用门的解析器**自证真解析不到**（不复用别名兜底）。"""
    dropped = ns.OPERATIONS.pop("extrude_boss", None) is not None
    unmapped = ns.MUST_HAVE_MAP.pop("boss_extrude", None) is not None
    spec = next(item for item in gate.ITEM_SPECS if item["key"] == "boss_extrude")
    resolved, source = gate._resolve_op(ns, ns.OPERATIONS, spec, set())
    print(f"    mutant=drop_manifest_key selfcheck dropped={dropped} unmapped={unmapped} "
          f"resolved_op={resolved!r} via={source or '-'}")
    return bool(dropped and unmapped and resolved is None)


def _mut_dangling_uuid(ns: Any, gate: Any) -> bool:
    ns.OPERATIONS["fillet"] = _wrap(ns.OPERATIONS["fillet"], lambda _out: [_new_uuid()])
    return True


def _mut_sketch_uuid_edges(ns: Any, gate: Any) -> bool:
    """阴性对照：fillet 的 edges 回落到草图 uuid（A9 的原始缺陷）—— 门必须报红。"""
    original = ns.OPERATIONS["fillet"]

    def op(target: Any, **kwargs: Any) -> Any:
        out = original(target, **kwargs)
        if isinstance(target, Mapping):
            sketches = target.get("sketches")
            if not isinstance(sketches, list) or not sketches:
                sketches = [target] if "entities" in target else []
            features = target.get("features")
        else:                                     # docmodel.Document 形态也要能变异，别只挑 dict 变
            sketches = getattr(target, "sketches", None)
            features = getattr(target, "features", None)
        sketch_uuid = ""
        if isinstance(sketches, list) and sketches:
            first = sketches[0]
            sketch_uuid = str((first.get("uuid") if isinstance(first, Mapping)
                               else getattr(first, "uuid", "")) or "")
        for item in features or []:
            if not isinstance(item, dict) or item.get("uuid") not in out:
                continue
            params = item.setdefault("params", {})
            if not isinstance(params, dict):
                continue
            params["edges"] = [sketch_uuid]
            params.pop("edge_refs", None)
        return out

    ns.OPERATIONS["fillet"] = op
    return True


MUTANTS: tuple[tuple[str, Callable[[Any], bool]], ...] = (
    ("empty_return", _mut_empty_return),
    ("drop_manifest_key", _mut_drop_manifest_key),
    ("dangling_uuid", _mut_dangling_uuid),
    ("sketch_uuid_edges", _mut_sketch_uuid_edges),
)


def selftest() -> int:
    package_root = str(Path(__file__).resolve().parents[2])
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    try:
        gate = _load_gate()
    except CadError as exc:
        print(f"CAD_PARITY_SW_FEATURES_MODULE_SELFTEST verdict=FAIL reason={exc.reason} rc=2")
        return 2
    baseline = gate.run_check(_namespace)
    base_ok = baseline["verdict"] == "PASS" and baseline["rc"] == 0 and baseline["n_calls"] > 0
    print(f"CAD_PARITY_SW_FEATURES_MODULE_SELFTEST_BASELINE verdict={baseline['verdict']} "
          f"ops_ok={baseline['ops_ok']} ops_total={baseline['ops_total']} missing={baseline['missing']} "
          f"unverified={baseline['unverified']} n_calls={baseline['n_calls']} rc={baseline['rc']}")
    detected = 0
    for name, mutate in MUTANTS:
        ns = _namespace()
        applied = bool(mutate(ns, gate))
        report = gate.run_check(lambda n=ns: n)
        red = applied and report["verdict"] in ("FAIL", "NA") and report["rc"] in (1, 2)
        detected += 1 if red else 0
        print(f"  mutant={name} applied={applied} verdict={report['verdict']} rc={report['rc']} "
              f"ops_ok={report['ops_ok']} missing={report['missing']} unverified={report['unverified']} "
              f"detected={red}")
    passed = bool(base_ok and detected == len(MUTANTS))
    print(f"CAD_PARITY_SW_FEATURES_MODULE_SELFTEST verdict={'PASS' if passed else 'FAIL'} "
          f"baseline={'PASS' if base_ok else 'FAIL'} mutants={len(MUTANTS)} detected={detected} "
          f"rc={0 if passed else 1}")
    return 0 if passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    print("cadcore.ops.sw_features: 18 operations; use --selftest to prove the gate sees red")
    return 0


if __name__ == "__main__":
    sys.exit(main())
