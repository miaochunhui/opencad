#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cadcore.topo -- 持久拓扑命名（persistent topology naming）。

一句话用途
----------
把 L1 文档的**特征历史**重算成实体拓扑对象（面 / 边 / 顶点），给每个对象一个**持久引用**
（'topo:<kind>:<feature_uuid>:<role>'）：引用由「生成它的特征 uuid + 该特征内的规范角色」决定，
**与草图 uuid 无关** ⇒ 重建 / 重算 / 参数修改后引用不变（或按设计迁移）。

不变量（三条，全部由 --selftest 现证）
--------------------------------------
1. **草图 uuid 不是拓扑身份**：ref 里只有特征 uuid 与角色。把整份文档的草图 uuid 与实体 uuid
   全换掉（几何不变），ref 集合逐字节不变；反之 ref 文本里不含任何草图 uuid。
2. **纯函数**：rebuild 不改入参、不写盘、不用随机数；同一文档任意次重算给出逐字节相同的 ref
   集合，L1 文本往返（document_to_l1 -> document_from_l1）后仍然相同。
3. **三态**：算不出来的特征进 unevaluated（下游记 -1 未采）；"没有可求值实体"是
   body_status=unmeasured + 0 个当前对象，绝不把"没算"写成"算出 0 个"。真为零（文档里一个
   特征都没有）与未采（有特征但本层算不出）在判定行里可辨。

阶段边界（写下来，防止下游靠猜）
--------------------------------
本模块**只做拓扑命名与对象重建，不做几何布尔运算**：可求值的是"加法棱柱"（extrude 的
boss 路径：闭合线框轮廓 + 深度 ⇒ 棱柱面/边/顶点）。减材 / 修饰 / 回转 / 扫描等特征记进
unevaluated，并把**当前实体**置 unmeasured + body_reason（如 'modified_by:<uuid>'）——
它们的真实 BRep 要等内核圆角算子（第 2 阶段）。known_refs 返回**最近一次可求值实体**的引用
（status=unmeasured 时这些引用可能已被后续特征改动，故把 status/reason 一并交回，调用方自己决定
是否采用；本仓 sw_features 采它并把 status 记进特征记录，不隐瞒）。

用法
----
    python3 cadcore/topo.py --selftest     # 阴性对照：确定性 / 草图 uuid 无关 / 迁移 / 三态
    python3 cadcore/topo.py --report       # 打印标准夹具的拓扑对象与 ref 摘要

本模块只读输入、只打印，不写任何文件。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])          # .../packages/cadcore
if _PACKAGE_ROOT not in sys.path:                                 # 直接 python3 topo.py 也能跑
    sys.path.insert(0, _PACKAGE_ROOT)

from cadcore.errors import CadError

__all__ = [
    "REF_PREFIX",
    "KINDS",
    "BODY_EVALUATED",
    "BODY_UNMEASURED",
    "TopoRef",
    "Face",
    "Edge",
    "Vertex",
    "Topology",
    "BodyRefs",
    "rebuild",
    "known_refs",
    "ref_fingerprint",
    "is_ref",
    "is_kind_ref",
    "stability",
    "selftest",
]

REF_PREFIX = "topo"
KINDS = ("face", "edge", "vertex")
BODY_EVALUATED = "evaluated"
BODY_UNMEASURED = "unmeasured"
FACE, EDGE, VERTEX = KINDS
_SKETCH_PLANES = ("xy",)


# --------------------------------------------------------------------------------------
# 持久引用（TopoRef）：文本形态唯一真源
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class TopoRef:
    """一条持久拓扑引用：'topo:<kind>:<feature_uuid>:<role>'。

    feature_uuid 是**生成**这个拓扑对象的特征记录 uuid（参数化历史里的稳定身份）；
    role 是该特征内的规范角色（'cap_start' / 'lateral/2' / 'cap_end+lateral/2' ...）。
    **没有任何一段来自草图 uuid 或实体 uuid** —— 这是"草图 uuid 不是拓扑身份"的落点。
    """

    kind: str
    feature_uuid: str
    role: str

    def __str__(self) -> str:
        return f"{REF_PREFIX}:{self.kind}:{self.feature_uuid}:{self.role}"

    @property
    def text(self) -> str:
        return str(self)

    @classmethod
    def parse(cls, text: Any) -> "TopoRef | None":
        """文本 -> TopoRef；不是持久引用（含草图 uuid）一律 None（不猜）。"""
        if not isinstance(text, str):
            return None
        parts = text.split(":", 3)
        if len(parts) != 4 or parts[0] != REF_PREFIX or parts[1] not in KINDS:
            return None
        if not parts[2] or not parts[3]:
            return None
        return cls(parts[1], parts[2], parts[3])


def is_ref(text: Any) -> bool:
    """text 是否是一条持久拓扑引用。"""
    return TopoRef.parse(text) is not None


def is_kind_ref(text: Any, kind: str) -> bool:
    """text 是否是一条**指定 kind** 的持久拓扑引用。"""
    parsed = TopoRef.parse(text)
    return parsed is not None and parsed.kind == kind


def _role(*atoms: str) -> str:
    """角色 = 若干原子按字典序拼接（'+' 分隔）⇒ 与遍历顺序无关，重建后不变。"""
    return "+".join(sorted(str(atom) for atom in atoms if str(atom)))


# --------------------------------------------------------------------------------------
# 拓扑对象（不可变；ref 是身份，几何是当前值）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    ref: str
    role: str
    feature_uuid: str
    surface: str
    normal: tuple[int, int, int]
    origin_nm: tuple[int, int, int]


@dataclass(frozen=True)
class Edge:
    ref: str
    role: str
    feature_uuid: str
    curve: str
    start_nm: tuple[int, int, int]
    end_nm: tuple[int, int, int]
    face_refs: tuple[str, ...]


@dataclass(frozen=True)
class Vertex:
    ref: str
    role: str
    feature_uuid: str
    point_nm: tuple[int, int, int]
    face_refs: tuple[str, ...]


@dataclass(frozen=True)
class Topology:
    """一次重算的结果：对象并集 + 当前实体状态 + 未采特征清单。"""

    faces: tuple[Face, ...] = ()
    edges: tuple[Edge, ...] = ()
    vertices: tuple[Vertex, ...] = ()
    body_refs: tuple[str, ...] = ()          # 当前实体（evaluated 时非空）
    body_status: str = BODY_UNMEASURED
    body_reason: str = "no_body_feature"
    body_feature: str = ""                   # 生成最近一次可求值实体的特征 uuid
    last_refs: tuple[str, ...] = ()          # 最近一次可求值实体的 refs（可能已被后续特征改动）
    unevaluated: tuple[str, ...] = ()        # 本层算不出的特征 uuid（未采，-1 语义）

    @property
    def n_objects(self) -> int:
        return len(self.faces) + len(self.edges) + len(self.vertices)

    def counts(self) -> dict[str, int]:
        return {"faces": len(self.faces), "edges": len(self.edges),
                "vertices": len(self.vertices), "topo_objects": self.n_objects}

    def _index(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for item in (*self.faces, *self.edges, *self.vertices):
            out[item.ref] = item
        return out

    def resolve(self, ref: Any) -> Any | None:
        """ref -> 拓扑对象；解析不到（含不是 ref 的草图 uuid）一律 None。"""
        return self._index().get(ref) if isinstance(ref, str) else None

    def refs(self, kind: str | None = None) -> tuple[str, ...]:
        """全部已求值对象的 ref（字典序）；kind 给定时只回该 kind。"""
        pools: list[Any]
        if kind == FACE:
            pools = list(self.faces)
        elif kind == EDGE:
            pools = list(self.edges)
        elif kind == VERTEX:
            pools = list(self.vertices)
        elif kind is None:
            pools = [*self.faces, *self.edges, *self.vertices]
        else:
            raise CadError("unknown topology kind", reason="bad_kind", kind=repr(kind))
        return tuple(sorted(str(item.ref) for item in pools))

    def fingerprint(self) -> tuple[Any, ...]:
        return ref_fingerprint(self)


def ref_fingerprint(topology: Topology) -> tuple[Any, ...]:
    """重算稳定性的尺子：只由 ref 集合 / 实体状态 / 未采清单构成（不看几何值）。"""
    return (topology.body_status, topology.body_reason, topology.refs(),
            tuple(sorted(topology.unevaluated)))


@dataclass(frozen=True)
class BodyRefs:
    """known_refs 的返回：一组引用 + 它们的来路（三态可辨，不冒充"当前实体"）。"""

    refs: tuple[str, ...]
    status: str
    reason: str
    feature_uuid: str


# --------------------------------------------------------------------------------------
# 目标适配与轮廓提取
# --------------------------------------------------------------------------------------


def _payload(document: Any) -> dict[str, Any]:
    """L1 文档 dict / docmodel.Document / 裸草图 dict -> 载荷 dict（不改入参）。"""
    if hasattr(document, "to_payload") and callable(getattr(document, "to_payload")):
        payload = document.to_payload()
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}
    if isinstance(document, Mapping):
        if "entities" in document or "plane" in document:      # 裸草图（可能已被就地提升）
            return {"sketches": [document], "features": list(document.get("features") or [])}
        if "sketches" in document or "features" in document:
            return dict(document)
    raise CadError("target is neither a document nor a sketch", reason="bad_document",
                   got=type(document).__name__)


def _live_features(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    features = payload.get("features")
    if not isinstance(features, list):
        return []
    out: list[Mapping[str, Any]] = []
    for item in features:
        if isinstance(item, Mapping) and not bool(item.get("suppressed")):
            out.append(item)
    return out


def _sketch_index(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    sketches = payload.get("sketches")
    if isinstance(sketches, list):
        for item in sketches:
            if isinstance(item, Mapping) and isinstance(item.get("uuid"), str):
                out[str(item["uuid"])] = item
    return out


def _profile_sketch_uuid(feature: Mapping[str, Any], sketches: Mapping[str, Any]) -> str:
    """轮廓草图 uuid：显式 sketch_uuid -> inputs 里第一个草图 -> params 里的同类键。"""
    for key in ("sketch_uuid", "profile_uuid", "sketch"):
        value = feature.get(key)
        if isinstance(value, str) and value in sketches:
            return value
    inputs = feature.get("inputs")
    if isinstance(inputs, list):
        for item in inputs:
            if isinstance(item, str) and item in sketches:
                return item
    params = feature.get("params")
    if isinstance(params, Mapping):
        for key in ("sketch_uuid", "profile_uuid", "sketch"):
            value = params.get(key)
            if isinstance(value, str) and value in sketches:
                return value
    return ""


def _nm_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = _nm_int(value[0]), _nm_int(value[1])
        if x is not None and y is not None:
            return (x, y)
    return None


def _walk_loop(segments: Sequence[tuple[tuple[int, int], tuple[int, int]]]) -> tuple[tuple[int, int], ...] | None:
    """线段表 -> 闭合环点列（按端点接续；歧义 / 不闭合 / 自交一律 None，不猜）。"""
    remaining = list(segments)
    first = remaining.pop(0)
    loop: list[tuple[int, int]] = [first[0], first[1]]
    cursor = first[1]
    while remaining:
        matches = [(index, seg) for index, seg in enumerate(remaining) if cursor in (seg[0], seg[1])]
        if len(matches) != 1:
            return None
        index, seg = matches[0]
        remaining.pop(index)
        nxt = seg[1] if seg[0] == cursor else seg[0]
        if nxt == loop[0]:
            return tuple(loop) if not remaining else None
        if nxt in loop:
            return None
        loop.append(nxt)
        cursor = nxt
    return None


def _loop_of(sketch: Mapping[str, Any]) -> tuple[tuple[int, int], ...] | None:
    """草图 -> 闭合轮廓点列。只认 XY 面上的 line 链 / 单条 polyline（阶段一）。"""
    plane = str(sketch.get("plane") or "").strip().lower()
    if plane not in _SKETCH_PLANES:
        return None
    raw_entities = sketch.get("entities")
    if not isinstance(raw_entities, list):
        return None
    entities: list[Mapping[str, Any]] = []
    for item in raw_entities:
        if isinstance(item, Mapping) and not bool(item.get("construction")):
            entities.append(item)
    if not entities:
        return None
    if len(entities) == 1 and str(entities[0].get("kind") or "").lower() == "polyline":
        geom = entities[0].get("geom")
        if not isinstance(geom, Mapping):
            return None
        raw = geom.get("points")
        if not isinstance(raw, list):
            raw = geom.get("points_nm")
        if not isinstance(raw, list):
            return None
        points = [_pair(item) for item in raw]
        if any(item is None for item in points) or len(points) < 3:
            return None
        loop = [item for item in points if item is not None]
        if loop[0] == loop[-1]:
            loop = loop[:-1]
        return tuple(loop) if len(loop) >= 3 else None
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for entity in entities:
        if str(entity.get("kind") or "").lower() != "line":
            return None
        geom = entity.get("geom")
        if not isinstance(geom, Mapping):
            return None
        x1, y1 = _nm_int(geom.get("x1")), _nm_int(geom.get("y1"))
        x2, y2 = _nm_int(geom.get("x2")), _nm_int(geom.get("y2"))
        if None in (x1, y1, x2, y2):
            return None
        if (x1, y1) == (x2, y2):
            return None
        segments.append(((int(x1), int(y1)), (int(x2), int(y2))))
    if len(segments) < 3:
        return None
    return _walk_loop(segments)


def _extent_nm(params: Mapping[str, Any]) -> tuple[int, int] | None:
    """拉伸深度 -> (z0, z1) 整数纳米；缺深度 / 非正整数一律 None（未采，不用 0 冒充）。"""
    raw = params.get("distance_nm")
    if raw is None:
        raw = params.get("depth_nm")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    if bool(params.get("symmetric")):
        half = int(raw) // 2
        return (-half, int(raw) - half)
    return (0, int(raw))


# --------------------------------------------------------------------------------------
# 棱柱重建：特征 uuid + 轮廓环 + 深度 -> 面 / 边 / 顶点（角色命名，与几何值解耦）
# --------------------------------------------------------------------------------------


def _prism(feature_uuid: str, loop: Sequence[tuple[int, int]], z0: int, z1: int) -> Topology:
    n = len(loop)
    area2 = sum(loop[k][0] * loop[(k + 1) % n][1] - loop[(k + 1) % n][0] * loop[k][1] for k in range(n))
    ccw = area2 > 0
    faces: list[Face] = []
    edges: list[Edge] = []
    vertices: list[Vertex] = []

    def face_ref(role: str) -> str:
        return str(TopoRef(FACE, feature_uuid, role))

    def edge_ref(role: str) -> str:
        return str(TopoRef(EDGE, feature_uuid, role))

    def vertex_ref(role: str) -> str:
        return str(TopoRef(VERTEX, feature_uuid, role))

    for role, z, sign in (("cap_start", z0, -1), ("cap_end", z1, 1)):
        faces.append(Face(ref=face_ref(role), role=role, feature_uuid=feature_uuid, surface="planar",
                          normal=(0, 0, sign), origin_nm=(loop[0][0], loop[0][1], z)))
    for k in range(n):
        a, b = loop[k], loop[(k + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        normal = (dy, -dx, 0) if ccw else (-dy, dx, 0)
        role = f"lateral/{k}"
        faces.append(Face(ref=face_ref(role), role=role, feature_uuid=feature_uuid, surface="planar",
                          normal=normal, origin_nm=((a[0] + b[0]) // 2, (a[1] + b[1]) // 2, z0)))
    for k in range(n):
        j = (k + 1) % n
        prev = (k - 1) % n
        lateral = f"lateral/{k}"
        lateral_prev = f"lateral/{prev}"
        for cap, z in (("cap_start", z0), ("cap_end", z1)):
            role = _role(cap, lateral)
            edges.append(Edge(ref=edge_ref(role), role=role, feature_uuid=feature_uuid, curve="line",
                              start_nm=(loop[k][0], loop[k][1], z), end_nm=(loop[j][0], loop[j][1], z),
                              face_refs=tuple(sorted((face_ref(cap), face_ref(lateral))))))
        role = _role(lateral_prev, lateral)
        edges.append(Edge(ref=edge_ref(role), role=role, feature_uuid=feature_uuid, curve="line",
                          start_nm=(loop[k][0], loop[k][1], z0), end_nm=(loop[k][0], loop[k][1], z1),
                          face_refs=tuple(sorted((face_ref(lateral_prev), face_ref(lateral))))))
        for cap, z in (("cap_start", z0), ("cap_end", z1)):
            vrole = _role(cap, lateral_prev, lateral)
            vertices.append(Vertex(ref=vertex_ref(vrole), role=vrole, feature_uuid=feature_uuid,
                                   point_nm=(loop[k][0], loop[k][1], z),
                                   face_refs=tuple(sorted((face_ref(cap), face_ref(lateral_prev),
                                                           face_ref(lateral))))))
    return Topology(faces=tuple(faces), edges=tuple(edges), vertices=tuple(vertices))


# --------------------------------------------------------------------------------------
# 重算主入口
# --------------------------------------------------------------------------------------


def rebuild(document: Any) -> Topology:
    """文档 -> 当前拓扑（纯函数）。可求值特征并集 + 当前实体状态 + 未采清单。"""
    payload = _payload(document)
    sketches = _sketch_index(payload)
    faces: list[Face] = []
    edges: list[Edge] = []
    vertices: list[Vertex] = []
    unevaluated: list[str] = []
    body_status = BODY_UNMEASURED
    body_reason = "no_body_feature"
    body_feature = ""
    last_refs: tuple[str, ...] = ()
    for feature in _live_features(payload):
        uuid = feature.get("uuid")
        uuid = uuid if isinstance(uuid, str) else ""
        if not uuid:
            continue
        kind = str(feature.get("kind") or "").lower()
        params = feature.get("params")
        params = params if isinstance(params, Mapping) else {}
        op = str(params.get("op") or "new").lower()
        additive = kind == "extrude" and op in ("new", "boss") and not bool(params.get("thin"))
        if additive:
            sketch_uuid = _profile_sketch_uuid(feature, sketches)
            sketch = sketches.get(sketch_uuid)
            loop = _loop_of(sketch) if isinstance(sketch, Mapping) else None
            extent = _extent_nm(params)
            if loop is None or extent is None:
                unevaluated.append(uuid)
                body_status = BODY_UNMEASURED
                body_reason = f"unsupported_feature:{uuid}"
                continue
            solid = _prism(uuid, loop, extent[0], extent[1])
            faces.extend(solid.faces)
            edges.extend(solid.edges)
            vertices.extend(solid.vertices)
            last_refs = tuple([item.ref for item in solid.faces]
                              + [item.ref for item in solid.edges]
                              + [item.ref for item in solid.vertices])
            body_status = BODY_EVALUATED
            body_reason = ""
            body_feature = uuid
            continue
        # 修饰 / 减材 / 本层未支持：实体已被改动，但改后的拓扑本层算不出（三态：未采）
        unevaluated.append(uuid)
        body_status = BODY_UNMEASURED
        body_reason = f"modified_by:{uuid}" if last_refs else f"unsupported_feature:{uuid}"
    body_refs = last_refs if body_status == BODY_EVALUATED else ()
    return Topology(faces=tuple(faces), edges=tuple(edges), vertices=tuple(vertices),
                    body_refs=body_refs, body_status=body_status, body_reason=body_reason,
                    body_feature=body_feature, last_refs=last_refs,
                    unevaluated=tuple(unevaluated))


def known_refs(document: Any, *, kind: str | None = None) -> BodyRefs:
    """最近一次**可求值实体**的引用（status 说明它今天还算不算当前实体）。

    status=evaluated：这就是当前实体的引用；status=unmeasured：实体已被后续特征改动，
    引用可能过期（reason 指明是谁改的），调用方必须自己决定采用与否 —— 不静默当有效。
    """
    topology = rebuild(document)
    refs = tuple(item for item in topology.last_refs
                 if kind is None or is_kind_ref(item, kind))
    return BodyRefs(refs=refs, status=topology.body_status, reason=topology.body_reason,
                    feature_uuid=topology.body_feature)


def stability(document: Any, *, rebuilds: int = 5) -> dict[str, Any]:
    """稳定性实证：同文档重算 N 次 + L1 文本往返，ref 指纹是否逐字节相同。"""
    times = max(1, int(rebuilds))
    base = rebuild(document)
    fingerprints = [base.fingerprint()]
    for _ in range(times - 1):
        fingerprints.append(rebuild(document).fingerprint())
    stable = len(set(fingerprints)) == 1
    roundtrip: dict[str, Any] = {"ok": False, "detail": "not_attempted"}
    try:
        from cadcore import docmodel
        text = docmodel.document_to_l1(docmodel.document_from_l1(json.dumps(_payload(document), default=str)))
        other = rebuild(json.loads(text))
        same = other.fingerprint() == base.fingerprint()
        roundtrip = {"ok": same, "detail": "" if same else "fingerprint_changed"}
    except Exception as exc:  # noqa: BLE001 - 未采要带原因，不许当成 0
        roundtrip = {"ok": False, "detail": f"{type(exc).__name__}:{exc}"}
    return {"rebuilds": times, "stable": stable, "roundtrip": roundtrip,
            "refs": base.refs(), "refs_by_kind": {key: base.refs(key) for key in KINDS},
            "counts": base.counts(), "body_status": base.body_status,
            "body_reason": base.body_reason, "unevaluated": list(base.unevaluated)}


# --------------------------------------------------------------------------------------
# 自检（阴性对照 + 阳性对照；不写盘）
# --------------------------------------------------------------------------------------


def _fixture_document(depth_nm: int = 5_000_000) -> dict[str, Any]:
    """标准夹具：10 mm x 6 mm 矩形（XY）+ 拉伸 depth_nm 的 boss。"""
    corners = ((0, 0), (10_000_000, 0), (10_000_000, 6_000_000), (0, 6_000_000))
    entities = []
    for index in range(4):
        (x1, y1), (x2, y2) = corners[index], corners[(index + 1) % 4]
        entities.append({"uuid": f"seg-{index}", "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"schema_version": 1, "unit": "mm", "standard_profile": "iso",
            "meta": {"doc_uuid": "a11ce000-0000-4000-8000-0000000000f1"},
            "sketches": [{"uuid": "sk-fixture", "plane": "XY", "entities": entities}],
            "features": [{"uuid": "ft-extrude", "kind": "extrude", "inputs": ["sk-fixture"],
                          "params": {"distance_nm": depth_nm, "symmetric": False, "op": "new"},
                          "suppressed": False}]}


def _rename_sketch_uuids(document: Mapping[str, Any]) -> dict[str, Any]:
    """把文档里**所有草图与实体 uuid** 换成另一套（几何与特征 uuid 不变）—— 阴性对照用。"""
    payload = json.loads(json.dumps(document, default=str))
    for index, sketch in enumerate(payload.get("sketches") or []):
        old = str(sketch.get("uuid") or "")
        new = f"renamed-sketch-{index}"
        sketch["uuid"] = new
        for entity_index, entity in enumerate(sketch.get("entities") or []):
            entity["uuid"] = f"renamed-entity-{index}-{entity_index}"
        for feature in payload.get("features") or []:
            if feature.get("sketch_uuid") == old:
                feature["sketch_uuid"] = new
            feature["inputs"] = [new if item == old else item for item in (feature.get("inputs") or [])]
    return payload


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: Any, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    document = _fixture_document()
    topology = rebuild(document)
    check("positive_prism_counts", topology.counts() == {"faces": 6, "edges": 12, "vertices": 8,
                                                         "topo_objects": 26}, str(topology.counts()))
    check("positive_body_evaluated", topology.body_status == BODY_EVALUATED and len(topology.body_refs) == 26,
          f"status={topology.body_status} body_refs={len(topology.body_refs)}")

    renamed = _rename_sketch_uuids(document)
    renamed_topology = rebuild(renamed)
    check("sketch_uuid_not_identity", renamed_topology.fingerprint() == topology.fingerprint(),
          "renaming every sketch/entity uuid must not move a single ref")
    sketch_uuids = {str(item.get("uuid")) for item in document["sketches"]}
    check("no_sketch_uuid_in_refs", all(uid not in ref for ref in topology.refs() for uid in sketch_uuids),
          "refs must not embed a sketch uuid")

    check("rebuild_5x_identical", len({rebuild(document).fingerprint() for _ in range(5)}) == 1, "")
    stable = stability(document, rebuilds=5)
    check("l1_roundtrip_identical", stable["roundtrip"]["ok"], str(stable["roundtrip"]))
    check("all_edge_refs_resolve",
          all(topology.resolve(ref) is not None for ref in topology.refs(EDGE)), "")
    check("ghost_ref_is_none", topology.resolve("topo:edge:ghost:cap_start+lateral/0") is None, "")
    check("sketch_uuid_is_not_a_ref", topology.resolve("sk-fixture") is None, "")

    deeper = _fixture_document(depth_nm=8_000_000)
    deeper_topology = rebuild(deeper)
    check("migration_keeps_refs", deeper_topology.refs() == topology.refs(),
          "parameter change keeps identity (roles), geometry is the only thing that moved")
    edge_ref = topology.refs(EDGE)[0]
    check("migration_moves_geometry",
          deeper_topology.resolve(edge_ref).start_nm != topology.resolve(edge_ref).start_nm,
          "the same edge ref must carry new geometry after the depth change")

    empty = rebuild({"sketches": [], "features": []})
    check("empty_is_zero_and_unmeasured", empty.n_objects == 0 and empty.body_status == BODY_UNMEASURED,
          f"objects={empty.n_objects} status={empty.body_status}")
    check("unsupported_profile_is_unevaluated",
          rebuild({"sketches": [{"uuid": "sk", "plane": "XY", "entities": []}],
                   "features": [{"uuid": "ft", "kind": "extrude", "inputs": ["sk"],
                                 "params": {"distance_nm": 1_000_000}}]}).unevaluated == ("ft",), "")
    cut = _fixture_document()
    cut["features"].append({"uuid": "ft-cut", "kind": "extrude", "inputs": ["ft-extrude", "sk-fixture"],
                            "params": {"distance_nm": 1_000_000, "op": "cut"}, "suppressed": False})
    cut_topology = rebuild(cut)
    known = known_refs(cut, kind=EDGE)
    check("modifier_marks_unmeasured",
          cut_topology.body_status == BODY_UNMEASURED and cut_topology.body_reason == "modified_by:ft-cut"
          and cut_topology.body_refs == (), f"{cut_topology.body_status}/{cut_topology.body_reason}")
    check("known_refs_reports_staleness", len(known.refs) == 12 and known.status == BODY_UNMEASURED, "")

    failed = [item for item in checks if not item[1]]
    for name, ok, detail in checks:
        print(f"  topo_check={name} ok={int(ok)}" + (f" detail={detail}" if detail else ""))
    verdict = "PASS" if not failed else "FAIL"
    print(f"CADCORE_TOPO_SELFTEST verdict={verdict} asserts={len(checks)} failed={len(failed)} "
          f"rc={0 if not failed else 1}")
    return 0 if not failed else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    if "--report" in args:
        topology = rebuild(_fixture_document())
        refs = topology.refs()
        print(f"CADCORE_TOPO_REPORT objects={topology.n_objects} faces={len(topology.faces)} "
              f"edges={len(topology.edges)} vertices={len(topology.vertices)} "
              f"body_status={topology.body_status} refs={len(refs)}")
        for ref in refs:
            print(f"  ref={ref}")
        return 0
    print("cadcore.topo: persistent topology naming; use --selftest / --report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
