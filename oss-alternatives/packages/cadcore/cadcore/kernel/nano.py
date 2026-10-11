#!/usr/bin/env python3
"""A7 `nano` —— 纯 Python 的 2.5D 几何后端（**零重依赖也必须能出图**）。

它存在的一条理由：判定行 G2 要在"OCCT/FreeCAD 全都不在"的机器上仍然画出一张带尺寸、带隐藏线、
带图框的图。所以这一层只用标准库，几何**现算**（不返回常量、不查表）。

几何表示（有意做得很小）：
- 形状 = 一组三维折线（`Edge3`），每条折线带一个**面法向**，用来判可见性；
- 拉伸体 = 底面轮廓（法向 `-Z`）+ 顶面轮廓（法向 `+Z`）+ 侧面竖边（法向取该环的径向外向，
  内环按有向面积取反向）；
- 投影 = 把三维点投到视图平面，再按 `法向 · 视线` 分可见/隐藏。

**已知简化（写在这里，不许当成没发生）**：
1. 隐藏线判定是"**面朝向**"，不是真正的遮挡消隐（HLR）：不看前后遮挡、不合并重影线。所以正视图里
   背面竖边会作为隐藏线输出，而商业 CAD 会把与可见线重合的部分裁掉。
2. 轮廓离散成折线（圆 64 段、弧按角度 24 段/90°）；没有 NURBS、没有精确圆。
3. `mass_properties` 是"轮廓级"的：面积按最大环的有向面积、体积 = 面积 × 厚度，不是 B-Rep 积分。
4. `revolve` / `boolean` / `step` / `iges` 能力**不在** `capabilities()` 里，调用即抛
   `DependencyMissing`（说得出"没有"，而不是悄悄返回个空壳）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..errors import CadError, DependencyMissing
#: 重影线合并（与 occt 同一把尺子，见 `dedupe_curves` docstring）。
from .base import dedupe_curves

__all__ = ["KERNEL_NAME", "KERNEL_VERSION", "CAPABILITIES", "NanoKernel", "Shape", "Edge3",
           "Projection", "MassProperties", "make_kernel", "nano_kernel", "register"]

KERNEL_NAME = "nano"
KERNEL_VERSION = "0.1.0"
CAPABILITIES = frozenset({"sketch", "extrude", "project2d", "mass"})

_CIRCLE_SEGMENTS = 64
_ARC_SEGMENTS_PER_QUARTER = 24
_EPS = 1e-9


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge3:
    """一条三维折线 + 它所属面的法向（可见性判据的输入）。"""

    points: tuple[tuple[int, int, int], ...]
    normal: tuple[float, float, float] = (0.0, 0.0, 1.0)
    role: str = "curve"

    def bounds(self) -> tuple[int, int, int, int, int, int]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        zs = [p[2] for p in self.points]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


@dataclass
class Shape:
    """形状引用：内核自己的轻量表示（不假装是 B-Rep）。"""

    name: str
    kind: str
    edges: tuple[Edge3, ...]
    depth_nm: int = 0
    symmetric: bool = False
    source: str = "nano"
    meta: dict[str, Any] = field(default_factory=dict)

    def curve_count(self) -> int:
        return len(self.edges)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "depth_nm": int(self.depth_nm),
                "symmetric": bool(self.symmetric), "edges": len(self.edges), "source": self.source}


@dataclass
class Projection:
    """投影结果：`curves=[{kind: visible|hidden, points_nm: [...]}, ...]`（A6/A8 约定形态）。"""

    view: str
    hidden: bool
    curves: list[dict[str, Any]]
    source: str = "nano"
    notes: list[str] = field(default_factory=list)

    @property
    def visible_curves(self) -> int:
        return sum(1 for item in self.curves if item.get("kind") == "visible")

    @property
    def hidden_curves(self) -> int:
        return sum(1 for item in self.curves if item.get("kind") == "hidden")


@dataclass
class MassProperties:
    """轮廓级质量属性（见模块 docstring 的简化 3）。"""

    area_nm2: int = -1
    perimeter_nm: int = -1
    centroid_nm: tuple[int, int] = (-1, -1)
    volume_nm3: int = -1
    depth_nm: int = 0
    loops: int = -1

    def as_dict(self) -> dict[str, Any]:
        return {"area_nm2": self.area_nm2, "perimeter_nm": self.perimeter_nm,
                "centroid_nm": list(self.centroid_nm), "volume_nm3": self.volume_nm3,
                "depth_nm": self.depth_nm, "loops": self.loops}


# --------------------------------------------------------------------------------------
# 草图 → 折线环
# --------------------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _circle_points(cx: float, cy: float, r: float, segments: int = _CIRCLE_SEGMENTS):
    step = 2.0 * math.pi / max(8, int(segments))
    return [(cx + r * math.cos(i * step), cy + r * math.sin(i * step)) for i in range(max(8, int(segments)) + 1)]


def _arc_points(cx: float, cy: float, r: float, start_deg: float, end_deg: float):
    start, end = _num(start_deg), _num(end_deg)
    while end <= start:
        end += 360.0
    sweep = end - start
    segments = max(4, int(_ARC_SEGMENTS_PER_QUARTER * sweep / 90.0) + 1)
    return [(cx + r * math.cos(math.radians(start + sweep * i / segments)),
             cy + r * math.sin(math.radians(start + sweep * i / segments))) for i in range(segments + 1)]


def _entity_loop(entity: Mapping[str, Any]):
    """一个 L1 实体 → 二维点列（`None` = 不参与几何，如 construction 线）。"""
    if bool(entity.get("construction")):
        return None
    kind = str(entity.get("kind") or "").strip().lower()
    geom = entity.get("geom") or {}
    if not isinstance(geom, Mapping):
        return None
    if kind == "line":
        return [(_num(geom.get("x1")), _num(geom.get("y1"))), (_num(geom.get("x2")), _num(geom.get("y2")))]
    if kind == "circle":
        return _circle_points(_num(geom.get("cx")), _num(geom.get("cy")), _num(geom.get("r")))
    if kind == "arc":
        return _arc_points(_num(geom.get("cx")), _num(geom.get("cy")), _num(geom.get("r")),
                           _num(geom.get("start_deg")), _num(geom.get("end_deg")))
    if kind == "polyline":
        raw = geom.get("points") or geom.get("vertices") or []
        points = []
        for item in raw:
            if isinstance(item, Mapping):
                points.append((_num(item.get("x")), _num(item.get("y"))))
            elif isinstance(item, Sequence) and len(item) >= 2:
                points.append((_num(item[0]), _num(item[1])))
        return points or None
    return None


def _sketch_loops(sketch: Mapping[str, Any]) -> list[list[tuple[float, float]]]:
    loops = []
    for entity in sketch.get("entities") or []:
        if not isinstance(entity, Mapping):
            continue
        loop = _entity_loop(entity)
        if loop and len(loop) >= 2:
            loops.append(loop)
    return loops


def _signed_area(loop: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(len(loop) - 1):
        x1, y1 = loop[index]
        x2, y2 = loop[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _pt_key(point: tuple[float, float]) -> tuple[int, int]:
    return (int(round(point[0])), int(round(point[1])))


def _chain_loops(segments: Sequence[Sequence[tuple[float, float]]],
                 closed_flags: Sequence[bool] | None = None) -> list[tuple[list[tuple[float, float]], bool]]:
    """把首尾相接的折线段串成环；返回 `(链, 该链是否为单段自闭环)`。

    调用方**必须先把人为补的闭环点去掉**（见 `mass_properties`）：`_prism` 给每条实体都补了
    "回到起点"的点，于是 4 条线段各自成了零面积闭环、串环永远连不起来 —— 这就是 G10 双基座对拍
    抓到的那次（板子报出 50.185 mm² = 一个 Ø8 圆孔的面积）。自闭环标记用来把"真圆"与"被补成闭环的
    开弧线"区分开：真圆即使去掉重复点也应按闭合环算面积。
    """
    flags = list(closed_flags) if closed_flags is not None else [False] * len(segments)
    remaining = [(list(seg), bool(flags[index]) if index < len(flags) else False, [index])
                 for index, seg in enumerate(segments) if len(seg) >= 2]
    loops: list[tuple[list[tuple[float, float]], bool]] = []
    while remaining:
        chain, was_closed, members = remaining.pop(0)
        extended = True
        while extended and _pt_key(chain[0]) != _pt_key(chain[-1]):
            extended = False
            for index, (seg, seg_closed, seg_members) in enumerate(remaining):
                if _pt_key(seg[0]) == _pt_key(chain[-1]):
                    chain.extend(seg[1:])
                elif _pt_key(seg[-1]) == _pt_key(chain[-1]):
                    chain.extend(list(reversed(seg))[1:])
                elif _pt_key(seg[-1]) == _pt_key(chain[0]):
                    chain = seg[:-1] + chain
                elif _pt_key(seg[0]) == _pt_key(chain[0]):
                    chain = list(reversed(seg))[:-1] + chain
                else:
                    continue
                remaining.pop(index)
                was_closed = was_closed or seg_closed
                members = members + seg_members
                extended = True
                break
        single_closed = len(members) == 1 and was_closed
        loops.append((chain, single_closed))
    return loops


def _centroid(loop: Sequence[tuple[float, float]]) -> tuple[float, float]:
    area = _signed_area(loop)
    if abs(area) < _EPS:
        xs = [p[0] for p in loop]
        ys = [p[1] for p in loop]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    for index in range(len(loop) - 1):
        x1, y1 = loop[index]
        x2, y2 = loop[index + 1]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * area), cy / (6.0 * area))


def _rounded(points: Iterable[tuple[float, float]], z: int = 0):
    out = []
    for x, y in points:
        item = (int(round(x)), int(round(y)), int(z))
        if not out or out[-1] != item:
            out.append(item)
    return tuple(out)


# --------------------------------------------------------------------------------------
# 形状构造
# --------------------------------------------------------------------------------------


def _prism(loops: Sequence[Sequence[tuple[float, float]]], *, depth_nm: int, symmetric: bool,
           name: str) -> Shape:
    """闭合轮廓 + 厚度 → 拉伸体（底面/顶面/侧面竖边，各带面法向）。"""
    depth = int(depth_nm)
    z_bottom, z_top = (-depth // 2, depth // 2) if symmetric else (0, depth)
    edges: list[Edge3] = []
    for loop in loops:
        closed = list(loop)
        if closed[0] != closed[-1]:
            closed.append(closed[0])
        area = _signed_area(closed)
        center = _centroid(closed)
        edges.append(Edge3(points=_rounded(closed, z_bottom), normal=(0.0, 0.0, -1.0), role="face-bottom"))
        edges.append(Edge3(points=_rounded(closed, z_top), normal=(0.0, 0.0, 1.0), role="face-top"))
        # 侧面：只在"折点"处立竖边（离散后的每个顶点都立会让边数爆炸，也没必要）
        sign = 1.0 if area >= 0 else -1.0
        for index in range(len(closed) - 1):
            x1, y1 = closed[index]
            x2, y2 = closed[index + 1]
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            nx, ny = mx - center[0], my - center[1]
            norm = math.hypot(nx, ny) or 1.0
            # 一圈取 4 条代表性竖边即可（0°/90°/180°/270° 处），避免 O(顶点数) 条边
            if index % max(1, (len(closed) - 1) // 4) != 0:
                continue
            edges.append(Edge3(points=((int(round(x1)), int(round(y1)), z_bottom),
                                       (int(round(x1)), int(round(y1)), z_top)),
                               normal=(sign * nx / norm, sign * ny / norm, 0.0), role="side"))
    return Shape(name=name, kind="solid", edges=tuple(edges), depth_nm=depth, symmetric=symmetric)


def _wire(loops: Sequence[Sequence[tuple[float, float]]], *, name: str) -> Shape:
    edges = tuple(Edge3(points=_rounded(loop), normal=(0.0, 0.0, 1.0), role="wire")
                  for loop in loops if len(loop) >= 2)
    return Shape(name=name, kind="wire", edges=edges, depth_nm=0)


def _depth_of(document: Mapping[str, Any]) -> tuple[int, bool, str]:
    """文档里第一个未抑制的拉伸特征 → `(厚度, 是否对称, 特征 uuid)`。"""
    for feature in document.get("features") or []:
        if not isinstance(feature, Mapping) or bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() != "extrude":
            continue
        params = feature.get("params") or {}
        depth = int(_num(params.get("distance_nm"), 0))
        if depth <= 0:
            continue
        return depth, bool(params.get("symmetric")), str(feature.get("uuid") or "")
    return 0, False, ""


# --------------------------------------------------------------------------------------
# 视图
# --------------------------------------------------------------------------------------

_ISO_VIEWER = (1.0 / math.sqrt(3.0), -1.0 / math.sqrt(3.0), 1.0 / math.sqrt(3.0))


def _orthonormal(viewer: tuple[float, float, float], up_hint: tuple[float, float, float]):
    vx, vy, vz = viewer
    norm = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
    viewer = (vx / norm, vy / norm, vz / norm)
    dot = sum(viewer[i] * up_hint[i] for i in range(3))
    up = tuple(up_hint[i] - dot * viewer[i] for i in range(3))
    unorm = math.sqrt(sum(component * component for component in up)) or 1.0
    up = tuple(component / unorm for component in up)
    right = (viewer[1] * up[2] - viewer[2] * up[1],
             viewer[2] * up[0] - viewer[0] * up[2],
             viewer[0] * up[1] - viewer[1] * up[0])
    return viewer, right, up


VIEW_BASIS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": _orthonormal((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": _orthonormal((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": _orthonormal((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": _orthonormal((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "right": _orthonormal((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": _orthonormal((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": _orthonormal(_ISO_VIEWER, (0.0, 0.0, 1.0)),
}


def _project_edge(edge: Edge3, view: str, viewer) -> tuple[tuple[float, float], ...]:
    _, right, up = VIEW_BASIS[view]
    return tuple((p[0] * right[0] + p[1] * right[1] + p[2] * right[2],
                  p[0] * up[0] + p[1] * up[1] + p[2] * up[2]) for p in edge.points)


def _classify(edge: Edge3, viewer) -> str:
    """面朝向判可见性：`n·视线 > 0` 可见、`< 0` 隐藏、`≈ 0`（边正对视线）按可见处理（简化 1）。"""
    dot = sum(edge.normal[i] * viewer[i] for i in range(3))
    return "hidden" if dot < -_EPS else "visible"


# --------------------------------------------------------------------------------------
# 后端
# --------------------------------------------------------------------------------------


class NanoKernel:
    """纯标准库 2.5D 后端（见模块 docstring 的能力与简化清单）。"""

    name = KERNEL_NAME
    version = KERNEL_VERSION

    def available(self) -> bool:
        return True

    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES

    # -- 形状构造 ----------------------------------------------------------------
    def sketch_to_wire(self, sketch: Any) -> Shape:
        """草图 → 线框（**只是线框**：没有厚度，所以没有隐藏几何，见 `project_to_2d`）。"""
        loops = _sketch_loops(self._as_sketch(sketch))
        if not loops:
            raise CadError("sketch has no usable geometry", reason="empty_sketch")
        return _wire(loops, name="wire")

    def extrude(self, profile: Any, *, distance_nm: int, symmetric: bool = False) -> Shape:
        depth = int(distance_nm)
        if depth <= 0:
            raise GeometryErrorCompat("extrude needs a positive distance", reason="bad_distance",
                                      distance_nm=distance_nm)
        if isinstance(profile, Shape):
            loops = [list((p[0], p[1]) for p in edge.points) for edge in profile.edges]
        else:
            loops = _sketch_loops(self._as_sketch(profile))
        if not loops:
            raise GeometryErrorCompat("profile has no usable geometry", reason="empty_profile")
        return _prism(loops, depth_nm=depth, symmetric=bool(symmetric), name="solid")

    def revolve(self, profile: Any, *, axis: str, angle_deg: float) -> Shape:
        raise DependencyMissing("nano backend cannot revolve", reason="capability_missing",
                                capability="revolve", kernel=self.name)

    def boolean(self, op: str, a: Any, b: Any) -> Shape:
        raise DependencyMissing("nano backend cannot do booleans", reason="capability_missing",
                                capability="boolean", kernel=self.name, op=str(op))

    # -- 文档级入口（G2 的 shape 来源）-------------------------------------------
    def shape_from_document(self, document: Any) -> Shape:
        """L1 文档 → 实体：轮廓取第一张草图，厚度取第一个未抑制的拉伸特征。

        这是"二维草图 + 拉伸"这条最小链路的**唯一入口**：只有知道厚度才可能有隐藏线，
        所以裸草图（`sketch_to_wire`）不承担出图职责。
        """
        if not isinstance(document, Mapping):
            raise CadError("document must be a mapping", reason="bad_document")
        sketches = [item for item in (document.get("sketches") or []) if isinstance(item, Mapping)]
        if not sketches:
            raise DependencyMissing("document has no sketch", reason="empty_document",
                                    capability="extrude", kernel=self.name)
        loops = _sketch_loops(sketches[0])
        if not loops:
            raise CadError("sketch has no usable geometry", reason="empty_sketch")
        depth, symmetric, feature_uuid = _depth_of(document)
        if depth <= 0:
            raise DependencyMissing("document has no extrude feature", reason="no_solid_feature",
                                    capability="extrude", kernel=self.name,
                                    hint="add a feature of kind=extrude with params.distance_nm>0")
        shape = _prism(loops, depth_nm=depth, symmetric=symmetric, name="document-solid")
        shape.meta.update({"sketch_uuid": str(sketches[0].get("uuid") or ""),
                           "feature_uuid": feature_uuid, "loops": len(loops)})
        return shape

    # -- 投影 --------------------------------------------------------------------
    def project_to_2d(self, shape: Any, *, view: str, hidden: bool = True) -> Projection:
        name = str(view or "").strip().lower()
        if name not in VIEW_BASIS:
            raise CadError("unknown view direction", reason="unknown_view", view=view,
                           known=sorted(VIEW_BASIS))
        target = self._as_shape(shape)
        if target.kind == "wire" and hidden:
            raise DependencyMissing(
                "a 2D wire has no hidden geometry; project with hidden=False or pass the document",
                reason="wire_has_no_hidden_geometry", kernel=self.name, shape=target.name)
        viewer = VIEW_BASIS[name][0]
        curves: list[dict[str, Any]] = []
        for edge in target.edges:
            flat = _project_edge(edge, name, viewer)
            points = []
            for x, y in flat:
                item = (int(round(x)), int(round(y)))
                if not points or points[-1] != item:
                    points.append(item)
            if len(points) < 2:
                continue  # 正对视线 ⇒ 投影成点，不是曲线
            curves.append({"kind": _classify(edge, viewer), "points_nm": points})
        if not curves:
            raise GeometryErrorCompat("projection produced no curves", reason="empty_projection",
                                      view=name, shape=target.name)
        # 重复边（同一位置画了两条棱）在投影里就是重影线 ⇒ 与 occt 同一口径合并（RC-V1）。
        merged = dedupe_curves(curves)
        notes = []
        if target.kind == "wire":
            notes.append("wire projection: visible curves only (no solid, no hidden lines)")
        if len(merged) != len(curves):
            notes.append("duplicate curves merged: %d -> %d" % (len(curves), len(merged)))
        return Projection(view=name, hidden=bool(hidden), curves=merged, notes=notes)

    # -- 质量属性 ----------------------------------------------------------------
    def mass_properties(self, shape: Any) -> MassProperties:
        """轮廓级质量属性：**去掉人为闭环点 → 串环 → 取最大闭合环**当外轮廓（见 `_chain_loops`）。"""
        target = self._as_shape(shape)
        segments: list[list[tuple[float, float]]] = []
        flags: list[bool] = []
        for edge in target.edges:
            if edge.role != "face-bottom":
                continue
            points = [(float(p[0]), float(p[1])) for p in edge.points]
            was_closed = len(points) > 2 and _pt_key(points[0]) == _pt_key(points[-1])
            if was_closed:
                points = points[:-1]           # `_prism`/`_wire` 补的"回到起点"点，必须去掉再串环
            segments.append(points)
            flags.append(was_closed)
        chains = _chain_loops(segments, flags)
        closed = [chain for chain, single_closed in chains
                  if len(chain) >= 3 and (_pt_key(chain[0]) == _pt_key(chain[-1]) or single_closed)]
        perimeter = int(round(sum(math.dist(chain[i], chain[i + 1]) for chain in closed
                                  for i in range(len(chain) - 1))))
        if not closed:
            return MassProperties(depth_nm=int(target.depth_nm), loops=len(chains))
        best = max(closed, key=lambda chain: abs(_signed_area(chain)))
        area_nm2 = int(round(abs(_signed_area(best))))
        cx, cy = _centroid(best)
        return MassProperties(area_nm2=area_nm2, perimeter_nm=perimeter,
                              centroid_nm=(int(round(cx)), int(round(cy))),
                              volume_nm3=area_nm2 * int(target.depth_nm),
                              depth_nm=int(target.depth_nm), loops=len(chains))

    def export(self, shape: Any, *, fmt: str, path: str) -> str:
        raise DependencyMissing("nano backend cannot export solid formats",
                                reason="capability_missing", capability=str(fmt), kernel=self.name)

    # -- 入参适配 ----------------------------------------------------------------
    def _as_sketch(self, value: Any) -> Mapping[str, Any]:
        if isinstance(value, Shape):
            return {"entities": []}
        if isinstance(value, Mapping):
            if "entities" in value:
                return value
            sketch = value.get("sketch")
            if isinstance(sketch, Mapping):
                return sketch
            sketches = value.get("sketches")
            if isinstance(sketches, Sequence) and sketches and isinstance(sketches[0], Mapping):
                return sketches[0]
            # 文档级：轮廓 + 拉伸 → 退化成"轮廓即环"，由 shape_from_document 负责厚度
            if "features" in value:
                return {"entities": []}
        raise CadError("cannot read a sketch out of this value", reason="unsupported_sketch",
                       value_type=type(value).__name__)

    def _as_shape(self, value: Any) -> Shape:
        if isinstance(value, Shape):
            return value
        if isinstance(value, Mapping):
            if "sketches" in value or "features" in value:
                return self.shape_from_document(value)
            if "entities" in value:
                loops = _sketch_loops(value)
                if not loops:
                    raise CadError("sketch has no usable geometry", reason="empty_sketch")
                return _wire(loops, name="wire")
            if isinstance(value.get("sketch"), Mapping):
                loops = _sketch_loops(value["sketch"])
                if not loops:
                    raise CadError("sketch has no usable geometry", reason="empty_sketch")
                return _wire(loops, name="wire")
        raise CadError("unsupported shape value", reason="unsupported_shape",
                       value_type=type(value).__name__)


class GeometryErrorCompat(CadError):
    """几何错误（沿用 `errors.GeometryError` 的语义；单独命名以免与兜底类同名冲突）。"""

    default_reason = "geometry_error"


def make_kernel(*_args: Any, **_kwargs: Any) -> NanoKernel:
    """工厂：`base.get_kernel("auto")` 用它实例化。"""
    return NanoKernel()


def nano_kernel() -> NanoKernel:
    """别名工厂（判定行脚本按多个候选名探测）。"""
    return NanoKernel()


def register() -> None:
    """把本后端注册进 `kernel.base`（由 `base._ensure_registered()` 惰性调用）。"""
    from . import base

    base.register_kernel(KERNEL_NAME, make_kernel)
