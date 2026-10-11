"""cadcore.sketch.editing —— 草图命令面补全：编辑 / 推断 / 补齐 / 投影 / 显隐。

一句话用途
----------
补齐 A9/A10（`commands.py` / `constraints.py` / `primitives.py`）之外那批"画完之后"的草图能力：
剪裁与延伸、等距（偏移）、镜像实体、线性阵列、圆角与倒角，以及 cadcore 原本没有的四项：
**自动几何关系推断与捕捉**、**Fully Define 自动补齐**、**转换实体引用**、
**关系符号与尺寸的显示/隐藏**。返回值一律 uuid（禁下标引用），坐标一律整数纳米。

本文件做什么、不做什么
----------------------
1. **几何算法不写第二份**：`trim` / `extend` / `offset` / `mirror` / `array_linear` / `fillet` /
   `chamfer` 直接复用 `cadcore.edit.modify` 的精确实现（参数方程 + `fractions.Fraction` 有理数求交、
   仿射核），本模块只把它们接到草图命令面上（`__all__` 里可发现，调用计数与探针走同一条路）。
   重写一份会立刻产生"两处读侧口径"，改一处忘一处就是 B6 级缺陷。
2. **四项新能力是真算，不是桩**：推断要按**几何条件**（点距 / 轴向 / 圆心距，且不移动被锚定实体）
   才加约束；补齐要**贪心地把 DoF 降到 0 且几何不漂**；投影要**逐字段校验 + 换新 uuid + 源不动**；
   显隐要**真按开关过滤文档里的约束与尺寸**。
3. **文案走 key**：非 docstring 的字符串字面量一律 ASCII（i18n key + ASCII 细节），面向用户的文案
   由语言层按 key 取词。
4. **状态落在已登记容器里**：显隐开关写 `sk["extra"]["annotation_display"]`
   （`docmodel.PRODUCT_EXTENSION_KEYS["sketch"]` 已登记 `extra`）—— 不新增草图顶层键，
   否则自家 save→open 会凭空多出 `unknown_field_preserved` 诊断并让 L1 文本不再逐字节稳定。

口径（写下来防止下游靠猜）
--------------------------
- `snap_point` 的容差是**文档单位（纳米）**：命中就返回那个**已存在的**捕捉点（不新造点、不回落原点）；
  没命中 `kind=None`（"没吸上"不是"吸到了 (0,0)"）。
- `infer_relations` 只落库不求解（与 `add_constraint` 同一取舍）；**不移动被锚定（`fixed`）实体**：
  会与锚定冲突的关系（点距不为 0、线不是严格轴向）不加。
- `fully_define` 只加**不改变几何**的约束/尺寸（`fix` 锚在当前坐标、尺寸锚在实测值），
  接受判据 = 秩真涨（dof 严格下降）**且不增加冗余方程**（过定义不是"补齐"）**且**几何漂移 ≤ `tol_nm`；
  做不到就如实 `status="stuck"`，不硬说完成。补齐后 `fix` 的锚点跟着文档当前坐标走（Fix 关系的语义），
  所以"完全定义"不等于"随便改坐标还能自动弹回"——只有尺寸钉住的那部分会弹回。
- `convert_entities` 在二维核心里是**恒等投影**（源边与草图共面）：换新 uuid、逐字段白名单拷贝、
  拒绝退化几何与未知 kind/uuid；源草图一个字节都不动。三维边→草图面的投影在 kernel 层，不在这里假装。
- `visible_annotations` 的缺省是"关系符号与尺寸都显示"；`sk["extra"]` 缺席即缺省，
  没有显隐记录 ≠ 全隐藏。

用法
----
    from cadcore.sketch import editing as ed
    ed.trim(sk, line_uuid, cutter_refs=[cutter_uuid], keep="start")
    ed.offset(sk, circle_uuid, distance_nm=2_000_000, side=1)
    ed.infer_relations(sk, tol_nm=1_000)          # 画完线后把已经成立的几何关系补成约束
    rep = ed.fully_define(sk)                      # rep["dof_after"] == 0 才算补齐
    ed.convert_entities(sk, model_sketch, [edge_uuid])
    ed.set_annotation_visibility(sk, relations=False)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Iterator

from .commands import add_dimension
from .constraints import (
    DEFAULT_TOL_NM,
    LENGTH_FIELDS,
    MAX_ITEMS_PER_OPERATION,
    GeometryError,
    InvalidLength,
    OutOfRange,
    SketchError,
    UnknownReference,
    UnsupportedKind,
    _as_nm,
    _geom,
    _geom_set,
    _get,
    _set,
    _point,
    add_constraint,
    closed_profiles,
    entity_kind,
    entity_uuid,
    geom_get,
    items_in_range,
    new_uuid,
    sketch_constraints,
    sketch_dimensions,
    sketch_entities,
    sketch_state,
    solve,
)
# 几何算法唯一真源：本层只接线，不重写（见模块 docstring 第 1 条）。
from ..edit.modify import (
    array_linear as array_linear,
    chamfer as chamfer,
    extend as extend,
    fillet as fillet,
    mirror as mirror,
    offset as offset,
    trim as trim,
)

__all__ = [
    "ANNOTATION_DISPLAY_KEY",
    "CONVERTIBLE_KINDS",
    "CONVERT_GEOM_FIELDS",
    "DEFAULT_SNAP_TOL_NM",
    "SNAP_KINDS",
    "annotation_visibility",
    "array_linear",
    "chamfer",
    "check_sketch",
    "convert_entities",
    "drag_entity",
    "extend",
    "fillet",
    "fully_define",
    "hide_annotation",
    "infer_relations",
    "mirror",
    "offset",
    "set_annotation_visibility",
    "show_annotation",
    "snap_point",
    "trim",
    "visible_annotations",
]

#: 捕捉缺省孔径（纳米）：0.1 mm —— 与"屏幕上几个像素"同量级，不是几何精度。
DEFAULT_SNAP_TOL_NM: int = 100_000

#: 捕捉类别（缺省全开；`snap_point(kinds=...)` 可逐类过滤，与二维捕捉的既有口径同名）。
SNAP_KINDS: tuple[str, ...] = ("endpoint", "midpoint", "center", "quadrant")

#: 可被"转换实体引用"投影进草图的实体种类（二维核心的冻结集）。
CONVERTIBLE_KINDS: tuple[str, ...] = ("line", "circle", "arc", "polyline")

#: 各实体种类进草图时要拷的几何字段（白名单：源上多出来的键一律不带，免得把外来字段搬进真源）。
CONVERT_GEOM_FIELDS: dict[str, tuple[str, ...]] = {
    "line": ("x1", "y1", "x2", "y2"),
    "circle": ("cx", "cy", "r"),
    "arc": ("cx", "cy", "r", "start_deg", "end_deg"),
    "polyline": ("points",),
}

#: 显隐记录在 `sk["extra"]` 里的命名空间（`extra` 是 docmodel 已登记的草图级扩展容器）。
ANNOTATION_DISPLAY_KEY: str = "annotation_display"

#: 捕捉的类别优先级：同距离时 endpoint 先于 midpoint，依次类推。
_SNAP_PRIORITY: dict[str, int] = {name: index for index, name in enumerate(SNAP_KINDS)}


# --------------------------------------------------------------------------------------
# 入参把关
# --------------------------------------------------------------------------------------


def _as_uuid(value: Any, *, what: str) -> str:
    """uuid 入参：非空字符串（L1 里引用一律 uuid，禁下标）。"""
    if not isinstance(value, str) or not value:
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a non-empty uuid string, got {value!r}")
    return value


def _as_tol(value: Any, *, what: str = "tol_nm") -> float:
    """容差入参：有限正数（纳米）。"没给容差"不许被解释成"容差无限大"。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a positive number of nm, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise OutOfRange("err.out_of_range", value=repr(value), min=1, max="NA",
                         detail=f"{what} must be a positive finite number of nanometres")
    return number


def _as_point(point: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a (x_nm, y_nm) pair, got {point!r}")
    return (_as_nm(point[0], positive=False, what=f"{what}.x"), _as_nm(point[1], positive=False, what=f"{what}.y"))


def _as_flag(value: Any, *, what: str) -> bool:
    """开关入参：严格 bool（`0/1` 不算 —— 静默强转会把"没传"和"传了 False"混成一种）。"""
    if not isinstance(value, bool):
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a bool, got {value!r}")
    return value


def _entity_by_uuid(items: Sequence[Any], wanted: str) -> Any:
    for item in items:
        if _get(item, "uuid", None) == wanted:
            return item
    raise UnknownReference("err.unknown_reference", reference=str(wanted))


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _line_distance(point: Sequence[float], p1: Sequence[float], p2: Sequence[float]) -> float:
    """点到直线（无限延长）的垂距。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    norm = math.hypot(dx, dy)
    if norm == 0.0:
        raise GeometryError("err.geometry_invalid", detail="degenerate line: both endpoints coincide")
    return abs(dx * (point[1] - p1[1]) - dy * (point[0] - p1[0])) / norm


def _selected_entities(sk: Any, entity_uuids: Sequence[str] | None, *, what: str) -> list[Any]:
    """挑出要处理的实体（`None` = 全选）；未知 uuid ⇒ `UnknownReference`（不静默跳过）。"""
    if entity_uuids is None:
        return list(sketch_entities(sk))
    if isinstance(entity_uuids, (str, bytes)) or not isinstance(entity_uuids, Sequence):
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a sequence of uuids, got {entity_uuids!r}")
    return [_entity_by_uuid(sketch_entities(sk), _as_uuid(ref, what=f"{what}[{index}]"))
            for index, ref in enumerate(entity_uuids)]


# --------------------------------------------------------------------------------------
# 捕捉：只剩"已经存在的几何点"可吸，吸不上就是 None
# --------------------------------------------------------------------------------------


def _arc_sweep_contains(angle_deg: float, start_deg: float, end_deg: float) -> bool:
    """角度是否落在圆弧的**逆时针**扫掠区间 `[start, end]`（与 `add_arc` 口径一致）。"""
    span = (end_deg - start_deg) % 360.0
    offset = (angle_deg - start_deg) % 360.0
    return offset <= span + 1e-9


def _snap_candidates(entities: Sequence[Any]) -> list[tuple[str, str, str, tuple[int, int]]]:
    """全部候选捕捉点：`(kind, uuid, sub, point_nm)`，顺序确定（uuid → sub → kind）。"""
    found: list[tuple[str, str, str, tuple[int, int]]] = []
    for entity in entities:
        kind = entity_kind(entity)
        uid = entity_uuid(entity)
        if kind == "line":
            start = _point(entity, "start")
            end = _point(entity, "end")
            found.append(("endpoint", uid, "start", (int(start[0]), int(start[1]))))
            found.append(("endpoint", uid, "end", (int(end[0]), int(end[1]))))
            found.append(("midpoint", uid, "", (int(round((start[0] + end[0]) / 2.0)),
                                               int(round((start[1] + end[1]) / 2.0)))))
        elif kind in ("circle", "arc"):
            cx, cy, radius = int(geom_get(entity, "cx")), int(geom_get(entity, "cy")), int(geom_get(entity, "r"))
            found.append(("center", uid, "center", (cx, cy)))
            quadrants = ((cx + radius, cy), (cx, cy + radius), (cx - radius, cy), (cx, cy - radius))
            if kind == "circle":
                for index, point in enumerate(quadrants):
                    found.append(("quadrant", uid, f"quadrant{index}", point))
            else:
                start_deg = float(geom_get(entity, "start_deg"))
                end_deg = float(geom_get(entity, "end_deg"))
                angles = (0.0, 90.0, 180.0, 270.0)
                for index, (point, angle) in enumerate(zip(quadrants, angles)):
                    if _arc_sweep_contains(angle, start_deg, end_deg):
                        found.append(("quadrant", uid, f"quadrant{index}", point))
                mid_angle = (start_deg + ((end_deg - start_deg) % 360.0) / 2.0) % 360.0
                rad = math.radians(mid_angle)
                found.append(("midpoint", uid, "", (int(round(cx + radius * math.cos(rad))),
                                                    int(round(cy + radius * math.sin(rad))))))
    return sorted(found, key=lambda item: (item[1], item[2], item[0]))


def snap_point(
    sk: Any,
    *,
    point_nm: Sequence[float],
    tol_nm: float = DEFAULT_SNAP_TOL_NM,
    entity_uuids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    """把一个候选点吸到最近的**已存在**捕捉点上；吸不上返回 `kind=None`。

    - 候选点来自文档几何本身：`line` 的两端点与中点、圆/弧的圆心、圆/弧的象限点、弧的扫掠中点；
    - 判据 = 距离 ≤ `tol_nm`，同距离按 `SNAP_KINDS` 优先级（endpoint → midpoint → center → quadrant），
      再按 `(uuid, sub)` 定序 ⇒ 同一输入两次调用结果逐字节一致；
    - **不新造点、不移动几何**：返回的是已有几何上的那个点（不是投影到某条线上的近似点）；
    - 没命中：`point_nm` 原样返回、`kind=None`、`ref=None` —— "没吸上"不许伪装成"吸到了原点"。
    """
    target = _as_point(point_nm, what="point_nm")
    tol = _as_tol(tol_nm)
    wanted = tuple(SNAP_KINDS) if kinds is None else tuple(kinds)
    for name in wanted:
        if name not in _SNAP_PRIORITY:
            raise UnsupportedKind("err.not_implemented", detail=f"snap kind {name!r}", feature=str(name))
    entities = _selected_entities(sk, entity_uuids, what="entity_uuids")
    candidates = _snap_candidates(entities)
    best: tuple[float, int, str, str, str, tuple[int, int]] | None = None
    for kind, uid, sub, point in candidates:
        if kind not in wanted:
            continue
        distance = _dist(target, point)
        if distance > tol:
            continue
        key = (distance, _SNAP_PRIORITY[kind], uid, sub, kind, point)
        if best is None or key[:5] < best[:5]:
            best = key
    if best is None:
        return {"point_nm": target, "kind": None, "ref": None, "distance_nm": None,
                "n_candidates": len(candidates)}
    return {"point_nm": best[5], "kind": best[4], "ref": [best[2], best[3]],
            "distance_nm": best[0], "n_candidates": len(candidates)}


# --------------------------------------------------------------------------------------
# 自动几何关系推断（把"看着已经成立"的几何关系补成真约束）
# --------------------------------------------------------------------------------------


def _point_refs(entity: Any) -> list[tuple[str, str, tuple[float, float]]]:
    """实体上可被关系引用的点：`(uuid, sub, 坐标)`（线段两端点 / 圆与弧的圆心 / 弧的两端点）。"""
    kind = entity_kind(entity)
    uid = entity_uuid(entity)
    if kind == "line":
        return [(uid, "start", _point(entity, "start")), (uid, "end", _point(entity, "end"))]
    if kind == "circle":
        return [(uid, "center", _point(entity, "center"))]
    if kind == "arc":
        return [(uid, "center", _point(entity, "center")),
                (uid, "start", _point(entity, "start")),
                (uid, "end", _point(entity, "end"))]
    return []


def _anchored(entity: Any) -> bool:
    return bool(_get(entity, "fixed", False))


def _existing_relation_keys(sk: Any) -> set[tuple[str, tuple[Any, ...]]]:
    """文档里已有的关系键（用于去重）：`(kind, 规范化 refs)`。"""
    keys: set[tuple[str, tuple[Any, ...]]] = set()
    for item in sketch_constraints(sk):
        kind = str(_get(item, "kind", ""))
        refs = tuple(_get(item, "refs", ()) or ())
        normalized = tuple(sorted((str(pair[0]), str(pair[1])) for pair in refs))
        keys.add((kind, normalized))
    return keys


def infer_relations(
    sk: Any,
    *,
    tol_nm: float = DEFAULT_SNAP_TOL_NM,
    entity_uuids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """按**几何条件**推断并落库几何关系（重合 / 水平 / 竖直 / 同心），返回机读报告。

    - 判据是量出来的：点-点距离 ≤ `tol_nm` ⇒ `coincident`；线段 `|dy| <= tol_nm` ⇒ `horizontal`、
      `|dx| <= tol_nm` ⇒ `vertical`；圆/弧圆心距 ≤ `tol_nm` ⇒ `concentric`；
    - **不移动锚定实体**：`fixed` 实体上的关系只有在该关系**已经精确成立**时才加
      （否则就是凭空造一条自相矛盾的约束）；
    - 同一个实体的两个点之间不加 `coincident`（那会把线段压成零长）；已有同形关系不重复加；
    - 只落库不求解（与 `add_constraint` 同一取舍），调用方自己调 `solve()`；
    - 返回 `{"added": [...], "kinds": {...}, "n_added": n, "skipped_existing": n, ...}`。
    """
    tol = _as_tol(tol_nm)
    entities = _selected_entities(sk, entity_uuids, what="entity_uuids")
    existing = _existing_relation_keys(sk)
    added: list[str] = []
    kinds: dict[str, int] = {}
    skipped = 0

    def add(kind: str, refs: Sequence[tuple[str, str]]) -> None:
        nonlocal skipped
        key = (kind, tuple(sorted((str(uid), str(sub)) for uid, sub in refs)))
        if key in existing:
            skipped += 1
            return
        added.append(add_constraint(sk, kind=kind, refs=[(uid, sub) for uid, sub in refs]))
        existing.add(key)
        kinds[kind] = kinds.get(kind, 0) + 1

    # ① 点-点重合
    points: list[tuple[Any, str, str, tuple[float, float]]] = []
    for entity in entities:
        for uid, sub, coord in _point_refs(entity):
            points.append((entity, uid, sub, coord))
    for index, (entity_a, uid_a, sub_a, point_a) in enumerate(points):
        for entity_b, uid_b, sub_b, point_b in points[index + 1:]:
            if uid_a == uid_b:
                continue
            if (sub_a == "center" and sub_b == "center"
                    and entity_kind(entity_a) in ("circle", "arc")
                    and entity_kind(entity_b) in ("circle", "arc")):
                continue  # 圆心对归 `concentric` 管：同一个几何事实不落两条约束
            distance = _dist(point_a, point_b)
            if distance > tol:
                continue
            if _anchored(entity_a) and _anchored(entity_b) and distance > 0.0:
                continue  # 两个都锚死又没精确重合 ⇒ 加了就是矛盾
            add("coincident", [(uid_a, sub_a), (uid_b, sub_b)])

    # ② 水平 / 竖直
    for entity in entities:
        if entity_kind(entity) != "line":
            continue
        start, end = _point(entity, "start"), _point(entity, "end")
        dx, dy = end[0] - start[0], end[1] - start[1]
        uid = entity_uuid(entity)
        if abs(dy) <= tol and abs(dx) > tol and (dy == 0.0 or not _anchored(entity)):
            add("horizontal", [(uid, "")])
        elif abs(dx) <= tol and abs(dy) > tol and (dx == 0.0 or not _anchored(entity)):
            add("vertical", [(uid, "")])

    # ③ 同心
    round_entities = [entity for entity in entities if entity_kind(entity) in ("circle", "arc")]
    for index, entity_a in enumerate(round_entities):
        for entity_b in round_entities[index + 1:]:
            center_a, center_b = _point(entity_a, "center"), _point(entity_b, "center")
            distance = _dist(center_a, center_b)
            if distance > tol:
                continue
            if _anchored(entity_a) and _anchored(entity_b) and distance > 0.0:
                continue
            add("concentric", [(entity_uuid(entity_a), "center"), (entity_uuid(entity_b), "center")])

    return {"added": added, "n_added": len(added), "kinds": kinds, "skipped_existing": skipped,
            "tol_nm": tol, "n_entities": len(entities)}


# --------------------------------------------------------------------------------------
# Fully Define 自动补齐：贪心消自由度，且几何不许漂
# --------------------------------------------------------------------------------------


def _snapshot_geometry(sk: Any) -> dict[str, dict[str, Any]]:
    """快照所有实体参与求解的长度字段（回滚用）。"""
    snap: dict[str, dict[str, Any]] = {}
    for entity in sketch_entities(sk):
        fields = LENGTH_FIELDS.get(entity_kind(entity), ())
        geom = _geom(entity)
        snap[entity_uuid(entity)] = {field: geom[field] for field in fields if field in geom}
    return snap


def _restore_geometry(sk: Any, snap: Mapping[str, Mapping[str, Any]]) -> None:
    for entity in sketch_entities(sk):
        for field, value in snap.get(entity_uuid(entity), {}).items():
            _geom_set(entity, field, value)


def _geometry_drift(sk: Any, snap: Mapping[str, Mapping[str, Any]]) -> float:
    """当前几何与快照的最大字段差（纳米）。"""
    drift = 0.0
    for entity in sketch_entities(sk):
        for field, value in snap.get(entity_uuid(entity), {}).items():
            drift = max(drift, abs(float(_geom(entity).get(field, value)) - float(value)))
    return drift


def _fully_define_actions(sk: Any) -> Iterator[tuple[str, str, tuple[tuple[str, str], ...], int | None]]:
    """按偏好生成"消自由度"候选：`(容器, kind, refs, value_nm)`。

    顺序（每条都**不改变当前几何**，后面的候选留给"前面被拒时"用）：
    锚起点 → 严格轴向则补 `horizontal` / `vertical` → 锚终点 → 用尺寸钉住长度/半径。
    把 2 条方程的 `fix` 排在 1 条方程的尺寸**前面**是有意的：完全定义的最后 1 个自由度常常
    只能靠 `fix` 消（没有基准/原点可量角度），先试 `fix` 就能在"锚两端"与"锚一端+尺寸"
    之间按**方程数恰好**的那条收敛 —— `fully_define` 的接受判据里带"不增加冗余方程"，正是这条顺序的闸。
    """
    # 按**文档顺序**（不是 uuid 顺序）：同一份文档两次调用的步骤数逐字节一致（可复现）。
    for entity in sketch_entities(sk):
        kind = entity_kind(entity)
        uid = entity_uuid(entity)
        if kind == "line":
            start, end = _point(entity, "start"), _point(entity, "end")
            yield ("constraints", "fix", ((uid, "start"),), None)
            if start[1] == end[1] and start[0] != end[0]:
                yield ("constraints", "horizontal", ((uid, ""),), None)
            elif start[0] == end[0] and start[1] != end[1]:
                yield ("constraints", "vertical", ((uid, ""),), None)
            yield ("constraints", "fix", ((uid, "end"),), None)
            length = int(round(_dist(start, end)))
            if length > 0:
                yield ("dimensions", "linear", ((uid, ""),), length)
        elif kind in ("circle", "arc"):
            yield ("constraints", "fix", ((uid, "center"),), None)
            radius = int(round(geom_get(entity, "r")))
            if radius > 0:
                yield ("dimensions", "radial", ((uid, "radius"),), radius)
        # polyline：`LENGTH_FIELDS["polyline"] == ()` ⇒ 没有自由度可消（真为零，不是没看见）


def _apply_action(sk: Any, action: tuple[str, str, tuple[tuple[str, str], ...], int | None]) -> str:
    container, kind, refs, value = action
    if container == "constraints":
        return add_constraint(sk, kind=kind, refs=list(refs), value_nm=value)
    return add_dimension(sk, kind=kind, refs=list(refs), value_nm=value)


def _drop_action(sk: Any, container: str, item_uuid: str) -> None:
    items = sketch_constraints(sk) if container == "constraints" else sketch_dimensions(sk)
    for index, item in enumerate(items):
        if _get(item, "uuid", None) == item_uuid:
            del items[index]
            return
    raise UnknownReference("err.unknown_reference", reference=str(item_uuid))


def fully_define(sk: Any, *, tol_nm: float = DEFAULT_TOL_NM, max_steps: int = 512) -> dict[str, Any]:
    """自动补齐到**完全定义**（DoF = 0），且**几何一个纳米都不漂**；返回机读报告。

    做法（贪心 + 回滚，每步都真跑求解器验秩）：
    对每个实体按偏好试一条候选（锚位置 → 轴向关系 → 远端锚点 → 尺寸），加进去重解；
    **只接受 dof 严格下降、不增加冗余方程、且几何漂移 ≤ `tol_nm` 的那条**，
    否则当场回滚（约束移除 + 几何恢复）——"补齐"不是"过定义"。
    没有候选能再降 DoF ⇒ `status="stuck"` 并如实给出剩余 DoF（不硬说"已完全定义"）。

    报告字段：`status` / `dof_before` / `dof_after` / `converged` / `n_added` / `added` /
    `steps` / `geometry_drift_nm` / `redundant` / `ok`（`ok` 才是"补齐成功"的判据）。
    """
    tol = _as_tol(tol_nm)
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise OutOfRange("err.out_of_range", value=repr(max_steps), min=1, max="NA",
                         detail="max_steps must be a positive integer")
    if not items_in_range(max_steps):
        raise OutOfRange("err.out_of_range", value=max_steps, min=1, max=MAX_ITEMS_PER_OPERATION,
                         detail="max_steps is above the per-operation limit")

    first = solve(sk)
    dof = first.dof
    dof_before = dof
    redundant = first.redundant
    redundant_before = redundant
    snapshot = _snapshot_geometry(sk)
    added: list[str] = []
    steps = 0
    result = first
    status = "already" if dof == 0 else "stuck"
    while dof > 0:
        if steps >= max_steps:
            status = "budget"
            break
        accepted = False
        for action in _fully_define_actions(sk):
            if steps >= max_steps:
                break
            steps += 1
            item_uuid = _apply_action(sk, action)
            trial = solve(sk)
            if trial.dof < dof and trial.redundant <= redundant and _geometry_drift(sk, snapshot) <= tol:
                dof = trial.dof
                redundant = trial.redundant
                added.append(item_uuid)
                snapshot = _snapshot_geometry(sk)
                result = trial
                accepted = True
                break
            _drop_action(sk, action[0], item_uuid)
            _restore_geometry(sk, snapshot)
            solve(sk)  # 文档回到上一个已接受状态（几何与剩余约束自洽）
        if not accepted:
            status = "stuck"
            break
        status = "ok" if dof == 0 else "stuck"
    drift = _geometry_drift(sk, snapshot)
    return {"status": status, "dof_before": dof_before, "dof_after": dof, "converged": bool(result.converged),
            "n_added": len(added), "added": added, "steps": steps, "geometry_drift_nm": drift,
            "redundant_before": int(redundant_before), "redundant": int(redundant),
            "ok": bool(dof == 0 and result.converged and drift <= tol and redundant <= redundant_before)}


# --------------------------------------------------------------------------------------
# 转换实体引用：把源几何投影进本草图（二维核心里是恒等投影，源一个字节都不动）
# --------------------------------------------------------------------------------------


def _source_entities(source: Any) -> list[Any]:
    """源几何：草图（有 `entities`）或直接的实体序列。"""
    if isinstance(source, (str, bytes)):
        raise GeometryError("err.geometry_invalid", detail="source must be a sketch or a sequence of entities")
    if isinstance(source, Mapping) or not isinstance(source, Sequence):
        entities = _get(source, "entities", None)
    else:
        entities = list(source)
    if isinstance(entities, (str, bytes)) or not isinstance(entities, Sequence):
        raise GeometryError("err.geometry_invalid",
                            detail=f"source entities must be a sequence, got {type(entities).__name__}")
    return list(entities)


def _convert_geom(kind: str, geom: Any) -> dict[str, Any]:
    """按种类白名单拷几何字段并逐字段把关（退化几何当场拒绝，不搬进真源）。"""
    if not isinstance(geom, Mapping):
        raise GeometryError("err.geometry_invalid", detail=f"{kind} entity has no geom mapping")
    copied: dict[str, Any] = {}
    for field in CONVERT_GEOM_FIELDS[kind]:
        if field not in geom:
            raise GeometryError("err.geometry_invalid", detail=f"{kind} geom is missing field {field!r}")
        value = geom[field]
        if field in ("x1", "y1", "x2", "y2", "cx", "cy"):
            copied[field] = _as_nm(value, positive=False, what=f"{kind}.{field}")
        elif field == "r":
            copied[field] = _as_nm(value, positive=True, what=f"{kind}.r")
        elif field in ("start_deg", "end_deg"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise GeometryError("err.geometry_invalid", detail=f"{kind}.{field} must be a finite degree value")
            copied[field] = float(value)
        else:  # polyline.points
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) < 2:
                raise GeometryError("err.geometry_invalid", detail="polyline needs at least 2 points")
            points = [_as_point(point, what=f"points[{index}]") for index, point in enumerate(value)]
            for previous, current in zip(points, points[1:]):
                if previous == current:
                    raise GeometryError("err.geometry_invalid",
                                        detail=f"polyline has a zero-length segment at {current!r}")
            copied[field] = [[x, y] for x, y in points]
    if kind == "line" and (copied["x1"], copied["y1"]) == (copied["x2"], copied["y2"]):
        raise InvalidLength("err.invalid_length", text=str(kind), detail="line has zero length")
    if kind == "arc" and copied["start_deg"] == copied["end_deg"]:
        raise GeometryError("err.geometry_invalid", detail="degenerate arc: start_deg == end_deg")
    return copied


def convert_entities(
    sk: Any,
    source: Any,
    refs: Sequence[str] | None = None,
    *,
    layer: str | None = None,
    construction: bool = False,
) -> list[str]:
    """把 `source` 上的边/环**投影**进本草图，返回新实体 uuid 列表（源不动）。

    - `source`：源草图（有 `entities` 的映射/对象）或直接的实体序列；`refs=None` = 全部；
    - 二维核心里源边与草图共面 ⇒ 投影是**恒等**：几何逐字段白名单拷贝、**新 uuid**、
      源实体一个字节都不改（这正是"转换实体引用"与"重新画一遍"的差别：引用的是已有几何）；
    - `layer=None` 继承源图层；`construction=True` 落成参考几何；
    - 未知 uuid ⇒ `UnknownReference`；非冻结种类 ⇒ `UnsupportedKind`；退化几何/坏字段 ⇒ `GeometryError`；
    - **先全量校验再落库**：任何一条不合格，草图里一条新实体都不会多（禁半接受）。
    """
    selected = _source_entities(source)
    if refs is None:
        wanted = [str(_get(entity, "uuid", "")) for entity in selected]
    else:
        wanted = [_as_uuid(ref, what=f"refs[{index}]") for index, ref in enumerate(refs)]
    plans: list[dict[str, Any]] = []
    for wanted_uuid in wanted:
        entity = _entity_by_uuid(selected, wanted_uuid)
        kind = entity_kind(entity)
        if kind not in CONVERTIBLE_KINDS:
            raise UnsupportedKind("err.not_implemented", detail=f"cannot convert entity kind {kind!r}", feature=str(kind))
        source_layer = _get(entity, "layer", "0")
        target_layer = source_layer if layer is None else layer
        if not isinstance(target_layer, str) or not target_layer.strip():
            raise GeometryError("err.geometry_invalid",
                                detail=f"layer must be a non-empty string, got {target_layer!r}")
        plans.append({"kind": kind, "layer": target_layer,
                      "construction": bool(construction) or bool(_get(entity, "construction", False)),
                      "geom": _convert_geom(kind, _get(entity, "geom", None))})

    created: list[str] = []
    for plan in plans:
        entity_uuid = new_uuid()
        sketch_entities(sk).append({"uuid": entity_uuid, "kind": plan["kind"], "layer": plan["layer"],
                                    "construction": plan["construction"], "geom": plan["geom"]})
        created.append(entity_uuid)
    return created


# --------------------------------------------------------------------------------------
# 关系符号 / 尺寸的显示与隐藏（状态落 sk["extra"]，过滤是真跑文档）
# --------------------------------------------------------------------------------------


def _annotation_table(sk: Any, *, create: bool) -> dict[str, Any]:
    """显隐表 `sk["extra"]["annotation_display"]`；`create=False` 时缺席返回 `{}`（缺省=全显示）。"""
    extra = _get(sk, "extra", None)
    if extra is None:
        if not create:
            return {}
        _set(sk, "extra", {})
        extra = _get(sk, "extra", None)
    if not isinstance(extra, Mapping):
        raise GeometryError("err.geometry_invalid",
                            detail=f"sketch 'extra' must be a mapping, got {type(extra).__name__}")
    table = extra.get(ANNOTATION_DISPLAY_KEY)
    if table is None:
        if not create:
            return {}
        table = {"relations": True, "dimensions": True, "hidden": []}
        if not isinstance(extra, MutableMapping):
            raise GeometryError("err.geometry_invalid", detail="sketch 'extra' is immutable, cannot store flags")
        extra[ANNOTATION_DISPLAY_KEY] = table
    if not isinstance(table, MutableMapping):
        raise GeometryError("err.geometry_invalid",
                            detail=f"'{ANNOTATION_DISPLAY_KEY}' must be a mutable mapping, got {type(table).__name__}")
    if create:  # 读路径不写文档（读侧不留痕 = 文档字节稳定）
        table.setdefault("relations", True)
        table.setdefault("dimensions", True)
        if not isinstance(table.get("hidden"), list):
            table["hidden"] = list(table.get("hidden") or [])
    return table


def annotation_visibility(sk: Any) -> dict[str, Any]:
    """当前显隐开关：`{"relations": bool, "dimensions": bool, "hidden": [uuid, ...]}`（缺省全显示）。"""
    table = _annotation_table(sk, create=False)
    return {"relations": bool(table.get("relations", True)),
            "dimensions": bool(table.get("dimensions", True)),
            "hidden": sorted(str(item) for item in table.get("hidden", ()) or ())}


def set_annotation_visibility(sk: Any, *, relations: bool | None = None,
                              dimensions: bool | None = None) -> dict[str, Any]:
    """翻开/关闭"关系符号"与"尺寸"两类标注的显示；返回生效后的开关（`None` = 该项不动）。"""
    for name, value in (("relations", relations), ("dimensions", dimensions)):
        if value is not None:
            _as_flag(value, what=name)
    table = _annotation_table(sk, create=True)
    if relations is not None:
        table["relations"] = bool(relations)
    if dimensions is not None:
        table["dimensions"] = bool(dimensions)
    return annotation_visibility(sk)


def _annotation_exists(sk: Any, annotation_uuid: str) -> bool:
    for item in list(sketch_constraints(sk)) + list(sketch_dimensions(sk)):
        if _get(item, "uuid", None) == annotation_uuid:
            return True
    return False


def hide_annotation(sk: Any, annotation_uuid: str, *, hidden: bool = True) -> str:
    """把**单条**关系/尺寸标为隐藏（或 `hidden=False` 恢复显示）；未知 uuid ⇒ `UnknownReference`。"""
    wanted = _as_uuid(annotation_uuid, what="annotation_uuid")
    flag = _as_flag(hidden, what="hidden")
    if not _annotation_exists(sk, wanted):
        raise UnknownReference("err.unknown_reference", reference=wanted)
    table = _annotation_table(sk, create=True)
    items = [str(item) for item in table.get("hidden", []) or []]
    if flag and wanted not in items:
        items.append(wanted)
    if not flag:
        items = [item for item in items if item != wanted]
    table["hidden"] = items
    return wanted


def show_annotation(sk: Any, annotation_uuid: str) -> str:
    """恢复单条标注的显示（`hide_annotation(..., hidden=False)` 的同义词）。"""
    return hide_annotation(sk, annotation_uuid, hidden=False)


def visible_annotations(sk: Any) -> dict[str, Any]:
    """按显隐开关**真过滤**文档，返回当前应显示的 `{"relations": [...], "dimensions": [...]}`。"""
    flags = annotation_visibility(sk)
    hidden = set(flags["hidden"])
    relations = [] if not flags["relations"] else [
        str(_get(item, "uuid", "")) for item in sketch_constraints(sk)
        if str(_get(item, "uuid", "")) not in hidden]
    dimensions = [] if not flags["dimensions"] else [
        str(_get(item, "uuid", "")) for item in sketch_dimensions(sk)
        if str(_get(item, "uuid", "")) not in hidden]
    return {"relations": relations, "dimensions": dimensions, "hidden": sorted(hidden),
            "counts": {"relations": len(relations), "dimensions": len(dimensions)}}


# --------------------------------------------------------------------------------------
# 特征前草图体检（Check Sketch for Feature 等价物）
# --------------------------------------------------------------------------------------


def _contour_degrees(sk: Any) -> dict[tuple[int, int], int]:
    """非构造 line/arc 的端点度数（按整数纳米节点合并）；悬空点 = 度数为 1 的节点。"""
    degrees: dict[tuple[int, int], int] = {}
    for entity in sketch_entities(sk):
        if bool(_get(entity, "construction", False)):
            continue
        if entity_kind(entity) not in ("line", "arc"):
            continue
        for sub in ("start", "end"):
            point = _point(entity, sub)
            node = (int(round(point[0])), int(round(point[1])))
            degrees[node] = degrees.get(node, 0) + 1
    return degrees


def check_sketch(sk: Any, *, tol_nm: float = DEFAULT_TOL_NM) -> dict[str, Any]:
    """特征前草图体检：返回 `errors` / `warnings` 清单与 `ready` 判定（每条都真算）。

    检查项
    ------
    1. `empty`：一条实体都没有 ⇒ error（特征没有东西可吃）；
    2. `degenerate_entity`：零长线 / 非正半径 ⇒ error（手改过的文档会出现，命令层本来拒收）；
    3. `open_contour`：非构造 line/arc 里存在悬空端点（度数 1）⇒ error；
    4. `no_closed_contour`：没有任何闭合轮廓 ⇒ error（拉伸/旋转吃不进轮廓）；
    5. `conflicting_constraints` / `not_converged`：求解矛盾 / 不收敛 ⇒ error；
    6. `under_defined`：`dof > 0` ⇒ **warning**（欠定义草图仍可出特征，只是会漂）。

    三态口径：`dof` / `converged` / `conflicting` 在求解失败时保持 `-1` / `None`（**未采**），
    绝不把"没算出来"写成 `0` / `False`；`n_errors=0` 才是真的没有 error。
    """
    tol = _as_tol(tol_nm)
    entities = list(sketch_entities(sk))
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not entities:
        errors.append({"code": "err.sketch.empty", "detail": "the sketch has no entities", "refs": []})

    for entity in entities:
        kind = entity_kind(entity)
        uid = entity_uuid(entity)
        if kind == "line" and _point(entity, "start") == _point(entity, "end"):
            errors.append({"code": "err.sketch.degenerate_entity", "detail": "zero-length line", "refs": [uid]})
        elif kind in ("circle", "arc") and float(geom_get(entity, "r")) <= 0.0:
            errors.append({"code": "err.sketch.degenerate_entity", "detail": "non-positive radius", "refs": [uid]})

    dangling = sorted(node for node, degree in _contour_degrees(sk).items() if degree == 1)
    if dangling:
        errors.append({"code": "err.sketch.open_contour",
                       "detail": f"{len(dangling)} dangling endpoints",
                       "refs": [f"{x}:{y}" for x, y in dangling]})

    profiles = closed_profiles(sk)
    if entities and not profiles:
        errors.append({"code": "err.sketch.no_closed_contour",
                       "detail": "no closed contour for a feature to consume", "refs": []})

    dof = -1
    converged: bool | None = None
    conflicting: bool | None = None
    state = "NA"
    try:
        result = solve(sk, tol_nm=tol)
    except SketchError as exc:  # 结构性坏引用/坏种类：如实记 error，不让体检自己炸掉
        errors.append({"code": "err.sketch.solve_failed",
                       "detail": f"{type(exc).__name__}: {exc}", "refs": []})
    else:
        dof = int(result.dof)
        converged = bool(result.converged)
        conflicting = bool(result.conflicting)
        state = sketch_state(sk, result=result)
        if conflicting:
            errors.append({"code": "err.sketch.conflicting_constraints",
                           "detail": f"redundant={result.redundant} residual_nm={result.residual:.6g}", "refs": []})
        elif not result.converged:
            errors.append({"code": "err.sketch.not_converged",
                           "detail": f"status={result.status} residual_nm={result.residual:.6g}", "refs": []})
        if dof > 0:
            warnings.append({"code": "err.sketch.under_defined",
                             "detail": f"dof={dof}", "refs": []})

    return {
        "ready": not errors,
        "n_errors": len(errors),
        "n_warnings": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "entities": len(entities),
        "closed_profiles": len(profiles),
        "dangling_endpoints": len(dangling),
        "dof": dof,
        "converged": converged,
        "conflicting": conflicting,
        "state": state,
        "tol_nm": tol,
    }


# --------------------------------------------------------------------------------------
# 拖动实体查看剩余自由度
# --------------------------------------------------------------------------------------

#: 实体长度字段 → 平移方向的系数（圆/弧只平移圆心，半径不动）。
_DRAG_FIELD_DELTA: dict[str, tuple[int, int]] = {
    "x1": (1, 0), "x2": (1, 0), "cx": (1, 0),
    "y1": (0, 1), "y2": (0, 1), "cy": (0, 1),
    "r": (0, 0),
}


def drag_entity(sk: Any, entity_uuid: str, *, delta_nm: Sequence[float],
                tol_nm: float = DEFAULT_TOL_NM) -> dict[str, Any]:
    """拖动实体查看剩余自由度：按 `delta_nm` 平移该实体几何 → 重解 → 报实际位移与残余 dof。

    - 只动该实体自己的长度字段（圆/弧只动圆心，半径与角度不动）；`polyline` 无长度字段 ⇒ `UnsupportedKind`；
    - `fixed: true` 的实体被锚定 ⇒ 不施加位移，如实返回 `anchored=True`、`moved_nm=0`；
    - 参照点：线取 `start`，圆/弧取 `center`；
    - **完全定义的草图**：约束会把几何拉回原位 ⇒ `moved_nm` 趋近 0（这是判据，不是失败）；
      欠定义草图则真能动 —— 用 `dof` 与 `moved_nm` 两个数一起说明"还有多少自由度"。
    """
    tol = _as_tol(tol_nm)
    uid = _as_uuid(entity_uuid, what="entity_uuid")
    entity = _entity_by_uuid(sketch_entities(sk), uid)
    dx, dy = _as_point(delta_nm, what="delta_nm")
    kind = entity_kind(entity)
    if kind == "polyline":
        raise UnsupportedKind("err.not_implemented", detail="drag of a polyline entity", feature="polyline")
    anchor_sub = "center" if kind in ("circle", "arc") else "start"
    before = _point(entity, anchor_sub)

    if not _anchored(entity):
        for field in LENGTH_FIELDS[kind]:
            fx, fy = _DRAG_FIELD_DELTA[field]
            _geom_set(entity, field, int(geom_get(entity, field)) + fx * dx + fy * dy)

    result = solve(sk, tol_nm=tol)
    after = _point(entity, anchor_sub)
    requested = math.hypot(float(dx), float(dy))
    return {
        "uuid": uid,
        "requested_nm": requested,
        "moved_nm": _dist(before, after),
        "anchored": _anchored(entity),
        "dof": int(result.dof),
        "converged": bool(result.converged),
        "status": str(result.status),
        "state": sketch_state(sk, result=result),
    }
