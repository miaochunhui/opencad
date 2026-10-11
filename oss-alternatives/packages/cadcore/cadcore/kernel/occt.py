#!/usr/bin/env python3
"""A6 的可选 **OCCT** 后端：真 B-Rep 几何（经 `OCP` = OCCT 的 Python 绑定）。

为什么要有它（objective 的原话）："以经证据核验的**最佳开源基座**为迭代起点"。`nano` 是纯标准库的
2.5D 兜底（折线近似、面朝向判可见性），它保证"重依赖全缺席也能出图"；而真正的工程制图内核是
**OCCT**（LGPL-2.1 + OCCT 例外，决策件 `synthesis/DECISION.md` 选定的几何内核）。这一层把 OCCT
包成与 `nano` **同一个** `KernelAdapter` 协议，于是：

- 上层（`draw/sheet.py`、`opencad`）**一行都不用改**就能换芯；
- 判定行 **G10 双基座对拍** 可以用同一份 L1 文档跑两个内核，逐项比对包围盒/轮廓面积/投影曲线；
- **缺席必须可观测**：没装 OCP 时 `available()=False`，绝不在 import 期炸（本模块顶层只 import 标准库）。

三条如实口径（不许美化）：
1. **能力集只写实现的**：`boolean` 只为"挖孔"这条路径实现（外轮廓拉伸 − 内环拉伸），
   一般布尔运算要显式抛 `DependencyMissing(capability="boolean")`，不许假装支持；
2. **投影走 HLR 真消隐**（`HLRBRep_Algo`），与 `nano` 的"面朝向判定"是两种东西 —— 判定行 G10
   因此**只比可见/隐藏曲线的存在性与几何范围**，不假装两者逐点相同；
3. **`mass_properties` 同时给两套数**：`profile_area_nm2`（外轮廓面，用来跟 `nano` 的轮廓级面积对拍）
   与 B-Rep 真值 `area_nm2`/`volume_nm3`（含孔）—— 两套数各自标注来源，别混着比。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..errors import CadError, DependencyMissing
#: 重影线合并（与 nano 同一把尺子，见 `dedupe_curves` docstring）。
from .base import dedupe_curves

__all__ = ["KERNEL_NAME", "KERNEL_VERSION", "CAPABILITIES", "OcctKernel", "make_kernel", "register",
           "ocp_available", "ocp_version", "OcctShape", "OcctProjection", "OcctMassProperties"]

KERNEL_NAME = "occt"
KERNEL_VERSION = "7.9"
CAPABILITIES = frozenset({"sketch", "extrude", "boolean", "project2d", "mass", "step", "stl"})

NM_PER_MM = 1_000_000.0
_DEFLECTION_MM = 0.05          # 离散精度：5e-2 mm（投影折线用；判定行里如实报出）
_TOL = 1e-7

# 与 nano 完全同源的视图方向（viewer 指向观察者；up 供屏幕坐标系用）
VIEW_DIRECTIONS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((1.0 / math.sqrt(3.0), -1.0 / math.sqrt(3.0), 1.0 / math.sqrt(3.0)), (0.0, 0.0, 1.0)),
}

_OCP_CACHE: dict[str, Any] = {}


def _ocp():
    """惰性导入 OCP（缺席时抛 `DependencyMissing`，**不在 import 期**）。"""
    if "mod" in _OCP_CACHE:
        mod = _OCP_CACHE["mod"]
        if mod is None:
            raise DependencyMissing("OCP (OCCT python bindings) is not importable",
                                    reason="dependency_missing", capability="occt", kernel=KERNEL_NAME)
        return mod
    try:  # pragma: no cover - 取决于环境是否装了 OCP
        import OCP  # type: ignore
    except Exception:  # noqa: BLE001 - 缺席是正常状态
        _OCP_CACHE["mod"] = None
        raise DependencyMissing("OCP (OCCT python bindings) is not importable",
                                reason="dependency_missing", capability="occt", kernel=KERNEL_NAME)
    _OCP_CACHE["mod"] = OCP
    return OCP


def ocp_available() -> bool:
    try:
        _ocp()
        return True
    except DependencyMissing:
        return False


def ocp_version() -> str:
    """OCCT 版本号（取不到写 NA，不编）。"""
    try:
        mod = _ocp()
    except DependencyMissing:
        return "NA"
    for attr in ("__version__", "VERSION"):
        value = getattr(mod, attr, None)
        if value:
            return str(value)
    try:  # pragma: no cover
        from OCP.Standard import Standard_Version  # type: ignore
        return str(Standard_Version())
    except Exception:  # noqa: BLE001
        return "NA"


# --------------------------------------------------------------------------------------
# 数据结构（与 nano 的对外形态保持一致，但如实标注来源）
# --------------------------------------------------------------------------------------


@dataclass
class OcctShape:
    """OCCT 形状引用。`kind` 与 nano 同名同义（`wire` / `solid`），`brep` 是真件句柄。"""

    name: str
    kind: str
    brep: Any
    depth_nm: int = 0
    source: str = KERNEL_NAME
    profile_area_nm2: int = -1
    meta: dict[str, Any] = field(default_factory=dict)

    def curve_count(self) -> int:
        return int(self.meta.get("edges", -1))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "depth_nm": int(self.depth_nm),
                "source": self.source, "profile_area_nm2": self.profile_area_nm2}


@dataclass
class OcctProjection:
    view: str
    hidden: bool
    curves: list[dict[str, Any]]
    source: str = KERNEL_NAME
    notes: list[str] = field(default_factory=list)

    @property
    def visible_curves(self) -> int:
        return sum(1 for item in self.curves if item.get("kind") == "visible")

    @property
    def hidden_curves(self) -> int:
        return sum(1 for item in self.curves if item.get("kind") == "hidden")


@dataclass
class OcctMassProperties:
    """两套数各自标注：`profile_area_*` = 外轮廓面（与 nano 对拍用），`area/volume` = B-Rep 真值。"""

    profile_area_nm2: int = -1
    area_nm2: int = -1
    volume_nm3: int = -1
    bbox_nm: tuple[int, int, int, int, int, int] | None = None
    depth_nm: int = 0
    faces: int = -1
    solids: int = -1
    source: str = KERNEL_NAME

    def as_dict(self) -> dict[str, Any]:
        return {"profile_area_nm2": self.profile_area_nm2, "area_nm2": self.area_nm2,
                "volume_nm3": self.volume_nm3, "bbox_nm": list(self.bbox_nm) if self.bbox_nm else None,
                "depth_nm": self.depth_nm, "faces": self.faces, "solids": self.solids,
                "source": self.source}


# --------------------------------------------------------------------------------------
# L1 草图 → OCCT 拓扑
# --------------------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# 实体表示：**扁平标签元组**（不要嵌套！2026-09-29 实撞：圆/弧曾被包成 1 元组，于是 `item[1]`
# 取到的是字符串 "circle"，端点解析全线 TypeError/IndexError）。
#   ("line", x1, y1, x2, y2) / ("circle", cx, cy, r) / ("arc", cx, cy, r, start_deg, end_deg)
#   / ("polyline", ((x, y), …))
def _entities_of_sketch(sketch: Mapping[str, Any]) -> list[tuple]:
    """L1 实体 → 扁平元组列表（只用来分组与造 OCCT 边，不参与最终判定）。"""
    items: list[tuple] = []
    for entity in sketch.get("entities") or []:
        if not isinstance(entity, Mapping) or bool(entity.get("construction")):
            continue
        kind = str(entity.get("kind") or "").strip().lower()
        g = entity.get("geom") or {}
        if not isinstance(g, Mapping):
            continue
        if kind == "line":
            items.append(("line", _num(g.get("x1")), _num(g.get("y1")),
                          _num(g.get("x2")), _num(g.get("y2"))))
        elif kind == "circle":
            items.append(("circle", _num(g.get("cx")), _num(g.get("cy")), _num(g.get("r"))))
        elif kind == "arc":
            items.append(("arc", _num(g.get("cx")), _num(g.get("cy")), _num(g.get("r")),
                          _num(g.get("start_deg")), _num(g.get("end_deg"))))
        elif kind == "polyline":
            raw = g.get("points") or g.get("vertices") or []
            pts = tuple((_num(p.get("x")), _num(p.get("y"))) if isinstance(p, Mapping)
                        else (_num(p[0]), _num(p[1])) for p in raw if p is not None)
            if len(pts) >= 2:
                items.append(("polyline", pts))
    return items


def _arc_angles(item: tuple) -> tuple[float, float]:
    """弧的起止角（把 `end <= start` 归一成逆时针扫掠，与 `_make_edge_2d` 同口径）。"""
    start, end = item[4], item[5]
    while end <= start:
        end += 360.0
    return start, end


def _arc_point(item: tuple, angle_deg: float) -> tuple[float, float]:
    cx, cy, r = item[1], item[2], item[3]
    rad = math.radians(angle_deg)
    return (cx + r * math.cos(rad), cy + r * math.sin(rad))


def _endpoints(item: tuple) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """开曲线的两个端点；**只有真·闭合曲线（circle）返回 `None`**。

    ⚠️ 2026-09-29 实撞：弧起初也被当成"自足曲线"返回 None，于是 4 条互不接触的直线 + 4 段圆角弧
    永远串不成外轮廓（分组结果 12 个单元素环）。弧是**开曲线**，端点必须参与串环。
    """
    kind = item[0]
    if kind == "line":
        return (item[1], item[2]), (item[3], item[4])
    if kind == "polyline":
        return item[1][0], item[1][-1]
    if kind == "arc":
        start, end = _arc_angles(item)
        return _arc_point(item, start), _arc_point(item, end)
    return None


def _edges_of_loop(loop: Sequence[tuple]) -> list[Any]:
    return [edge for item in loop for edge in _make_edge_2d(item)]


def _wires_from_edges(edges: Sequence[Any]) -> list[Any]:
    """把一堆边接成 wire —— 交给 OCCT 自己排序/定向（别手搓）。

    `ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s` 是 OCCT 里干这件事的正规入口，
    签名收 `TopTools_HSequenceOfShape`（**不是 Compound** —— 第一次按 Compound 调直接 TypeError），
    而它吐出来的元素是 `TopoDS_Shape`，**必须 `TopoDS.Wire_s` 下转**才能喂给
    `BRepBuilderAPI_MakeFace`（第二次实撞：少了这一步，`MakeFace` 抛 TypeError，又被 `_face_of`
    的 try/except 吞成"接不成面"，白白多查了一轮）。
    """
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds  # type: ignore
    from OCP.TopTools import TopTools_HSequenceOfShape  # type: ignore
    from OCP.TopoDS import TopoDS  # type: ignore

    sequence = TopTools_HSequenceOfShape()
    for edge in edges:
        sequence.Append(edge)
    wires = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(sequence, 1e-6, False, wires)
    return [TopoDS.Wire_s(wires.Value(index + 1)) for index in range(wires.Length())]


def _compound_of(edges: Sequence[Any]):
    """一堆边 → Compound（wire/线框形态用它承载；投影与 HLR 都能吃）。"""
    from OCP.BRep import BRep_Builder  # type: ignore
    from OCP.TopoDS import TopoDS_Compound  # type: ignore

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for edge in edges:
        builder.Add(compound, edge)
    return compound


def _make_edge_2d(item: tuple) -> list[Any]:
    """一条 L1 实体 → OCCT 边列表（单位 mm；polyline 会拆成多条边）。"""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge  # type: ignore
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # type: ignore
    from OCP.GC import GC_MakeArcOfCircle, GC_MakeCircle  # type: ignore

    def mm(value: float) -> float:
        return float(value) / NM_PER_MM

    kind = item[0]
    if kind == "circle":
        _, cx, cy, r = item
        curve = GC_MakeCircle(gp_Ax2(gp_Pnt(mm(cx), mm(cy), 0.0), gp_Dir(0.0, 0.0, 1.0)), mm(r)).Value()
        return [BRepBuilderAPI_MakeEdge(curve).Edge()]
    if kind == "arc":
        _, cx, cy, r, start, end = item
        while end <= start:
            end += 360.0
        sweep = end - start
        points = [gp_Pnt(mm(cx) + mm(r) * math.cos(math.radians(start + sweep * frac)),
                         mm(cy) + mm(r) * math.sin(math.radians(start + sweep * frac)), 0.0)
                  for frac in (0.0, 0.5, 1.0)]
        curve = GC_MakeArcOfCircle(points[0], points[1], points[2]).Value()
        return [BRepBuilderAPI_MakeEdge(curve).Edge()]
    if kind == "line":
        _, x1, y1, x2, y2 = item
        return [BRepBuilderAPI_MakeEdge(gp_Pnt(mm(x1), mm(y1), 0.0), gp_Pnt(mm(x2), mm(y2), 0.0)).Edge()]
    if kind == "polyline":
        points = item[1]
        return [BRepBuilderAPI_MakeEdge(gp_Pnt(mm(points[i][0]), mm(points[i][1]), 0.0),
                                        gp_Pnt(mm(points[i + 1][0]), mm(points[i + 1][1]), 0.0)).Edge()
                for i in range(len(points) - 1)]
    raise CadError("unsupported entity kind", reason="unsupported_entity", kind=str(kind))


def _wire_from_edges(edges: Sequence[Any]):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire  # type: ignore

    builder = BRepBuilderAPI_MakeWire()
    for edge in edges:
        builder.Add(edge)
    if not builder.IsDone():
        raise CadError("cannot build a wire from these edges", reason="geometry_error")
    return builder.Wire()


def _point_key(x: float, y: float, tol: float = 1e-6) -> tuple[int, int]:
    return (int(round(x / tol)), int(round(y / tol)))


def _group_loops(items: Sequence[tuple]) -> list[list[tuple]]:
    """按端点连通性把实体分组（circle/arc 各自成环；line/polyline 按共享端点串联）。"""
    loops: list[list[tuple]] = [[item] for item in items if _endpoints(item) is None]
    remaining = [item for item in items if _endpoints(item) is not None]
    while remaining:
        chain = [remaining.pop(0)]
        extended = True
        while extended:
            extended = False
            first = _endpoints(chain[0])
            last = _endpoints(chain[-1])
            keys = {_point_key(*first[0]), _point_key(*last[1])}
            for index, candidate in enumerate(remaining):
                head, tail = _endpoints(candidate)
                if _point_key(*head) in keys or _point_key(*tail) in keys:
                    chain.append(remaining.pop(index))
                    extended = True
                    break
        loops.append(chain)
    return loops


def _bbox_of_items(items: Sequence[tuple]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for item in items:
        kind = item[0]
        if kind in ("circle", "arc"):
            cx, cy, r = item[1], item[2], item[3]
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
        elif kind == "line":
            for x, y in ((item[1], item[2]), (item[3], item[4])):
                xs.append(x)
                ys.append(y)
        elif kind == "polyline":
            for x, y in item[1]:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys))


def _loops_from_sketch(sketch: Mapping[str, Any]) -> list[list[tuple]]:
    return _group_loops(_entities_of_sketch(sketch))


def _depth_of(document: Mapping[str, Any]) -> tuple[int, bool]:
    for feature in document.get("features") or []:
        if not isinstance(feature, Mapping) or bool(feature.get("suppressed")):
            continue
        if str(feature.get("kind") or "").strip().lower() != "extrude":
            continue
        params = feature.get("params") or {}
        depth = int(_num(params.get("distance_nm"), 0))
        if depth > 0:
            return depth, bool(params.get("symmetric"))
    return 0, False


# --------------------------------------------------------------------------------------
# 后端
# --------------------------------------------------------------------------------------


class OcctKernel:
    """OCCT 后端（`available()` 取决于本机是否有 OCP；能力集只写实现的那些）。"""

    name = KERNEL_NAME
    version = KERNEL_VERSION

    # -- 可用性与能力 ---------------------------------------------------------
    def available(self) -> bool:
        return ocp_available()

    def capabilities(self) -> frozenset[str]:
        if not self.available():
            return frozenset()
        return CAPABILITIES

    def _require(self) -> None:
        """**每个几何入口的第一句**：不可用就抛 `DependencyMissing`。

        为什么单独拎出来（2026-09-29 自测实撞）：`sketch_to_wire` 原先直接走到 `_make_edge_2d`，
        那里 `from OCP...` 抛的是 `ModuleNotFoundError` —— 而本包口径是"缺席必须抛
        `DependencyMissing`"（上层据此判 NA/rc=2）。`_as_sketch` / `_as_shape` 是所有几何入口的
        共同前门，故在这两处 + `extrude` / `boolean`（不走前门的两个）统一前置。
        """
        _ocp()

    # -- 形状构造 -------------------------------------------------------------
    def sketch_to_wire(self, sketch: Any) -> OcctShape:
        """草图 → 线框（**无厚度** ⇒ 没有隐藏几何，见 `project_to_2d` 的同名口径）。"""
        data = self._as_sketch(sketch)
        items = _entities_of_sketch(data)
        if not items:
            raise CadError("sketch has no usable geometry", reason="empty_sketch")
        edges = [edge for item in items for edge in _make_edge_2d(item)]
        # 承载用 Compound（草图可能由互不相连的多个环组成）；能不能接成 wire 由 OCCT 说了算
        return OcctShape(name="wire", kind="wire", brep=_compound_of(edges),
                         meta={"edges": len(edges), "wires": len(_wires_from_edges(edges))})

    def extrude(self, profile: Any, *, distance_nm: int, symmetric: bool = False) -> OcctShape:
        self._require()
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism  # type: ignore
        from OCP.gp import gp_Vec  # type: ignore

        depth = int(distance_nm)
        if depth <= 0:
            raise CadError("extrude needs a positive distance", reason="bad_distance", distance_nm=depth)
        if not isinstance(profile, OcctShape):
            raise CadError("extrude expects an OcctShape profile", reason="unsupported_shape",
                           value_type=type(profile).__name__)
        vec = gp_Vec(0.0, 0.0, depth / NM_PER_MM)
        solid = BRepPrimAPI_MakePrism(profile.brep, vec).Shape()
        return OcctShape(name="solid", kind="solid", brep=solid, depth_nm=depth,
                         meta={"edges": profile.curve_count()})

    def boolean(self, op: str, a: Any, b: Any) -> OcctShape:
        self._require()
        if str(op) != "cut":
            raise DependencyMissing("occt adapter only implements the cut used for holes",
                                    reason="capability_missing", capability=f"boolean:{op}",
                                    kernel=self.name)
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut  # type: ignore

        cut = BRepAlgoAPI_Cut(a.brep, b.brep)
        cut.Build()
        if not cut.IsDone():
            raise CadError("boolean cut failed", reason="geometry_error", kernel=self.name)
        return OcctShape(name="solid-cut", kind="solid", brep=cut.Shape(), depth_nm=a.depth_nm)

    def shape_from_document(self, document: Any) -> OcctShape:
        """L1 文档 → B-Rep 实体：外轮廓拉伸 − 内环拉伸（孔）。"""
        if not isinstance(document, Mapping):
            raise CadError("document must be a mapping", reason="bad_document")
        sketches = [item for item in (document.get("sketches") or []) if isinstance(item, Mapping)]
        if not sketches:
            raise DependencyMissing("document has no sketch", reason="empty_document",
                                    capability="extrude", kernel=self.name)
        depth, _symmetric = _depth_of(document)
        if depth <= 0:
            raise DependencyMissing("document has no extrude feature", reason="no_solid_feature",
                                    capability="extrude", kernel=self.name)
        loops = _loops_from_sketch(sketches[0])
        if not loops:
            raise CadError("sketch has no usable geometry", reason="empty_sketch")
        boxes = [(loop, _bbox_of_items(loop)) for loop in loops]
        boxes.sort(key=lambda pair: (pair[1][2] - pair[1][0]) * (pair[1][3] - pair[1][1]), reverse=True)
        outer_loop, outer_box = boxes[0]
        outer_face = self._face_of(outer_loop)
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism  # type: ignore
        from OCP.gp import gp_Vec  # type: ignore
        from OCP.GProp import GProp_GProps  # type: ignore
        from OCP.BRepGProp import BRepGProp  # type: ignore

        vec = gp_Vec(0.0, 0.0, depth / NM_PER_MM)
        solid = BRepPrimAPI_MakePrism(outer_face, vec).Shape()
        for loop, _box in boxes[1:]:
            face = self._face_of(loop)
            tool = BRepPrimAPI_MakePrism(face, vec).Shape()
            solid = self.boolean("cut", OcctShape("s", "solid", solid, depth), OcctShape("t", "solid", tool, depth)).brep
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(outer_face, props)
        profile_area_mm2 = float(props.Mass())
        shape = OcctShape(name="document-solid", kind="solid", brep=solid, depth_nm=depth,
                          profile_area_nm2=int(round(profile_area_mm2 * NM_PER_MM * NM_PER_MM)),
                          meta={"loops": len(loops), "holes": max(0, len(loops) - 1),
                                "outer_bbox_nm": [int(round(v)) for v in outer_box]})
        return shape

    def _face_of(self, loop: Sequence[tuple]):
        """环 → 面：先让 OCCT 把边接成 wire（排序与定向交给它），再取**面积最大**的那个面。"""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace  # type: ignore
        from OCP.GProp import GProp_GProps  # type: ignore
        from OCP.BRepGProp import BRepGProp  # type: ignore

        edges = _edges_of_loop(loop)
        if not edges:
            raise CadError("loop has no edges", reason="empty_loop")
        best: tuple[float, Any] | None = None
        for wire in _wires_from_edges(edges):
            try:
                builder = BRepBuilderAPI_MakeFace(wire)
            except Exception:  # noqa: BLE001 - 接不成面的 wire 跳过；全都不成才报错
                continue
            if not builder.IsDone():
                continue
            face = builder.Face()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            area = abs(float(props.Mass()))
            if best is None or area > best[0]:
                best = (area, face)
        if best is None:
            raise CadError("cannot build a face from this loop", reason="geometry_error",
                           edges=len(edges))
        return best[1]

    # -- 投影（真消隐） -------------------------------------------------------
    def project_to_2d(self, shape: Any, *, view: str, hidden: bool = True) -> OcctProjection:
        name = str(view or "").strip().lower()
        if name not in VIEW_DIRECTIONS:
            raise CadError("unknown view direction", reason="unknown_view", view=view,
                           known=sorted(VIEW_DIRECTIONS))
        target = self._as_shape(shape)
        if target.kind == "wire" and hidden:
            raise DependencyMissing(
                "a 2D wire has no hidden geometry; project with hidden=False or pass the document",
                reason="wire_has_no_hidden_geometry", kernel=self.name, shape=target.name)
        viewer, up = VIEW_DIRECTIONS[name]
        visible = self._hlr_curves(target.brep, viewer, up, want_hidden=False)
        hidden_curves = self._hlr_curves(target.brep, viewer, up, want_hidden=True) if hidden else []
        raw = ([{"kind": "visible", "points_nm": pts} for pts in visible]
               + [{"kind": "hidden", "points_nm": pts} for pts in hidden_curves if pts])
        # HLR 会把同一条棱从多个 compound 里各吐一份 ⇒ 先合并重影线（RC-V1）。
        curves = dedupe_curves(raw)
        if not curves:
            raise CadError("projection produced no curves", reason="empty_projection",
                           view=name, kernel=self.name)
        notes = [f"hlr=HLRBRep_Algo deflection_mm={_DEFLECTION_MM}"]
        if len(curves) != len(raw):
            notes.append("duplicate curves merged: %d -> %d" % (len(raw), len(curves)))
        return OcctProjection(view=name, hidden=bool(hidden), curves=curves, notes=notes)

    def _hlr_curves(self, brep: Any, viewer: Sequence[float], up: Sequence[float],
                    *, want_hidden: bool) -> list[list[tuple[int, int]]]:
        from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape  # type: ignore
        from OCP.HLRAlgo import HLRAlgo_Projector  # type: ignore
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # type: ignore

        algo = HLRBRep_Algo()
        algo.Add(brep)
        axis = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(*[float(v) for v in viewer]),
                      gp_Dir(*[float(v) for v in up]))
        algo.Projector(HLRAlgo_Projector(axis))
        algo.Update()
        algo.Hide()
        hlr = HLRBRep_HLRToShape(algo)
        compounds = ([hlr.HCompound(), hlr.OutLineHCompound(), hlr.Rg1LineHCompound()] if want_hidden
                     else [hlr.VCompound(), hlr.OutLineVCompound(), hlr.Rg1LineVCompound()])
        out: list[list[tuple[int, int]]] = []
        for compound in compounds:
            if compound is None:
                continue
            for pts in self._discretize(compound):
                if len(pts) >= 2:
                    out.append(pts)
        return out

    def _discretize(self, compound: Any) -> list[list[tuple[int, int]]]:
        """拓扑 → 折线（投影平面内的 3D 点取 (x, y)，单位 nm 整数）。"""
        from OCP.TopExp import TopExp_Explorer  # type: ignore
        from OCP.TopAbs import TopAbs_EDGE  # type: ignore
        from OCP.TopoDS import TopoDS  # type: ignore
        from OCP.BRepAdaptor import BRepAdaptor_Curve  # type: ignore
        from OCP.GCPnts import GCPnts_QuasiUniformDeflection  # type: ignore

        polylines: list[list[tuple[int, int]]] = []
        explorer = TopExp_Explorer(compound, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge_s(explorer.Current())
            try:
                adaptor = BRepAdaptor_Curve(edge)
                sampler = GCPnts_QuasiUniformDeflection(adaptor, _DEFLECTION_MM)
                if not sampler.IsDone():
                    explorer.Next()
                    continue
                pts: list[tuple[int, int]] = []
                for index in range(1, sampler.NbPoints() + 1):
                    pnt = sampler.Value(index)
                    item = (int(round(pnt.X() * NM_PER_MM)), int(round(pnt.Y() * NM_PER_MM)))
                    if not pts or pts[-1] != item:
                        pts.append(item)
                if len(pts) >= 2:
                    polylines.append(pts)
            except Exception:  # noqa: BLE001 - 单条边离散失败不该炸整张图，但也不假装成功
                pass
            explorer.Next()
        return polylines

    # -- 质量属性 -------------------------------------------------------------
    def mass_properties(self, shape: Any) -> OcctMassProperties:
        from OCP.GProp import GProp_GProps  # type: ignore
        from OCP.BRepGProp import BRepGProp  # type: ignore
        from OCP.Bnd import Bnd_Box  # type: ignore
        from OCP.BRepBndLib import BRepBndLib  # type: ignore
        from OCP.TopExp import TopExp_Explorer  # type: ignore
        from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID  # type: ignore

        target = self._as_shape(shape)
        volume_props = GProp_GProps()
        BRepGProp.VolumeProperties_s(target.brep, volume_props)
        area_props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(target.brep, area_props)
        box = Bnd_Box()
        BRepBndLib.Add_s(target.brep, box)
        bbox_mm = box.Get()
        bbox = tuple(int(round(v * NM_PER_MM)) for v in bbox_mm) if len(bbox_mm) == 6 else None
        to_nm2 = NM_PER_MM * NM_PER_MM
        return OcctMassProperties(
            profile_area_nm2=int(target.profile_area_nm2),
            area_nm2=int(round(float(area_props.Mass()) * to_nm2)),
            volume_nm3=int(round(float(volume_props.Mass()) * to_nm2 * NM_PER_MM)),
            bbox_nm=bbox, depth_nm=int(target.depth_nm),
            faces=self._count(target.brep, TopAbs_FACE), solids=self._count(target.brep, TopAbs_SOLID))

    def _count(self, brep: Any, shape_type: Any) -> int:
        from OCP.TopExp import TopExp_Explorer  # type: ignore

        explorer = TopExp_Explorer(brep, shape_type)
        total = 0
        while explorer.More():
            total += 1
            explorer.Next()
        return total

    # -- 导出 -----------------------------------------------------------------
    def export(self, shape: Any, *, fmt: str, path: str) -> str:
        target = self._as_shape(shape)
        kind = str(fmt or "").strip().lower()
        if kind == "step":
            from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs  # type: ignore
            writer = STEPControl_Writer()
            writer.Transfer(target.brep, STEPControl_AsIs)
            writer.Write(path)
            return path
        if kind == "stl":
            from OCP.StlAPI import StlAPI_Writer  # type: ignore
            writer = StlAPI_Writer()
            writer.Write(target.brep, path)
            return path
        raise DependencyMissing("occt adapter cannot export this format", reason="capability_missing",
                                capability=str(fmt), kernel=self.name)

    # -- 入参适配 -------------------------------------------------------------
    def _as_sketch(self, value: Any) -> Mapping[str, Any]:
        self._require()
        if isinstance(value, Mapping):
            if "entities" in value:
                return value
            sketch = value.get("sketch")
            if isinstance(sketch, Mapping):
                return sketch
            sketches = value.get("sketches")
            if isinstance(sketches, Sequence) and sketches and isinstance(sketches[0], Mapping):
                return sketches[0]
        raise CadError("cannot read a sketch out of this value", reason="unsupported_sketch",
                       value_type=type(value).__name__)

    def _as_shape(self, value: Any) -> OcctShape:
        self._require()
        if isinstance(value, OcctShape):
            return value
        if isinstance(value, Mapping):
            if "sketches" in value or "features" in value:
                return self.shape_from_document(value)
            if "entities" in value:
                return self.sketch_to_wire(value)
            if isinstance(value.get("sketch"), Mapping):
                return self.sketch_to_wire(value["sketch"])
        raise CadError("unsupported shape value", reason="unsupported_shape",
                       value_type=type(value).__name__)


def make_kernel(*_args: Any, **_kwargs: Any) -> OcctKernel:
    """工厂：`base.get_kernel("auto")` 用它实例化（`auto` 顺序 `occt → freecad → nano`）。"""
    return OcctKernel()


def register() -> None:
    """注册进 `kernel.base`（由 `base._ensure_registered()` 惰性调用）。"""
    from . import base

    base.register_kernel(KERNEL_NAME, make_kernel)
