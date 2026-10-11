#!/usr/bin/env python3
"""sw_pattern_refgeom 家族实现片 w21：镜像实体（多实体零件）—— 显式降级 + L1 实体级真镜像核心。

一、家族缺口的枚举（features/sw-pattern-refgeom.json 的 must_have 顺序，0-based 编号）
    按 status/sw-pattern-refgeom.json 的**逐条** status 取仍 missing/partial 的 must_have，共 N=8 条：
      0 线性阵列第二方向 (missing)
      1 阵列对象类型（特征/面/实体） (missing)
      2 跳过与恢复实例 (missing)
      3 圆周阵列角度范围（含360°整圈） (missing)
      4 镜像实体（多实体零件） (missing)   ← 本席（w21）负责：12 mod 8 = 4
      5 装配体镜像零部件 (missing)
      6 阵列/镜像的参数化重建 (missing)
      7 质量属性 (partial)
    注：status 文件尾部的聚合字段（implemented=0 / partial=1 / missing=19）与逐条 status 自相矛盾 ——
    逐条里有 12 条 implemented 且带 file:line 证据，聚合字段是旧值；本片一律以**逐条** status 为准。

二、本片实现的能力与它的诚实边界（宁可红不许假绿）
    目标语义（spec 该条）：按实体（body）而非按特征镜像；merge_solids=True 时与源实体并成一个 body，
    False 时生成独立 body；返回新 body uuid。
    但今天的 L1 文档模型（cadcore/docmodel.py 节点字段无 body/solid 列表；kernel 单形状返回）
    **没有多实体承载**，因此分两条路：
      * 生产路径 —— 文档里没有实体表 ⇒ CadError(reason="unsupported_kind") 显式降级，绝不静默
        退化成按特征镜像（spec 该条 test_vector 的原文要求）。因此本族该条 status 仍如实记 missing，
        覆盖度不因本片变化。
      * L1 实体级路径 —— 调用方若在 L1 文档上携带显式实体表（"bodies"/"solids" 列表，元素形如
        {"uuid": str, "kind": "body", "entities": [L1 图元, ...]}），本片按**实体**真镜像：
        反射该实体全部图元（整数纳米精确，斜迹线不整除即 geometry_error，禁浮点进真源），
        圆弧扫掠方向按微度翻转，merge_solids=True 时并回源实体而不是新开实体。
        这是真几何、真数据后果，不是桩。该实体表约定由本片定义，今天没有生产路径创建它，
        所以家族 status 仍写 missing，不冒充已实现。

三、口径（与 spec/frozen_interface 一致）
    长度 = 整数纳米（int nm）；角度 = 整数微度（int udeg，1deg = 1000000udeg）。
    L1 图元 kind ∈ {point, line, circle, arc}；镜像面迹线取 L1 平面记录的 p1_nm/p2_nm
    （与 cadcore.ops.sw_pattern_refgeom 的镜像函数同口径），也可直接给 {"p1_nm","p2_nm"} 映射。
    0=真为零 / -1=未采 / NA=不适用；本片不把未实现写成已实现。
"""

from __future__ import annotations

import copy
import uuid as _uuid
from fractions import Fraction
from typing import Any, Callable, Mapping

from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "mirror_bodies", "selftest"]

FAMILY = "sw_pattern_refgeom"

BODY_BUCKETS = ("bodies", "solids")
PLANE_BUCKETS = ("datums", "planes")
FULL_CIRCLE_UDEG = 360000000


def _fail(reason: str, detail: str, **details: Any) -> Any:
    """统一出口：reason 机器可读，details 供判定行归因（不靠解析文案）。"""
    raise CadError(detail, reason=reason, **details)


def _bucket(doc: Any, keys: tuple[str, ...]) -> Any:
    """取 L1 文档上的表（list）。表不存在返回 None —— 与「空表 []」严格区分。"""
    for key in keys:
        value = doc.get(key) if isinstance(doc, Mapping) else getattr(doc, key, None)
        if isinstance(value, list):
            return value
    return None


def _kind_of(entity: Mapping[str, Any]) -> Any:
    return entity.get("kind") or entity.get("type")


def _as_nm2(value: Any, *, what: str) -> tuple[int, int]:
    """二维整数纳米点。bool 与浮点一律拒收（禁浮点回流成真源）。"""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        out: list[int] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                _fail("invalid_argument", "coordinate must be int nm (no float)",
                      field=what, got=item)
            out.append(int(item))
        return (out[0], out[1])
    _fail("invalid_argument", "expected 2d point in int nm", field=what, got=value)


def _as_entity_list(body: Mapping[str, Any]) -> list[Any]:
    entities = body.get("entities")
    if not isinstance(entities, list) or not entities:
        _fail("invalid_argument", "body has no L1 entities", body=body.get("uuid"))
    return entities


def _as_radius_nm(entity: Mapping[str, Any]) -> int:
    radius = entity.get("radius_nm")
    if isinstance(radius, bool) or not isinstance(radius, int) or radius <= 0:
        _fail("invalid_argument", "radius_nm must be a positive int nm", got=radius)
    return int(radius)


def _entity_points(entity: Mapping[str, Any]) -> list[tuple[int, int]]:
    """图元的保守包围点（圆/弧按 center±radius）。"""
    kind = _kind_of(entity)
    if kind == "point":
        return [_as_nm2(entity.get("point_nm"), what="point_nm")]
    if kind == "line":
        return [_as_nm2(entity.get("p1_nm"), what="p1_nm"),
                _as_nm2(entity.get("p2_nm"), what="p2_nm")]
    if kind in ("circle", "arc"):
        center = _as_nm2(entity.get("center_nm"), what="center_nm")
        radius = _as_radius_nm(entity)
        return [(center[0] - radius, center[1] - radius),
                (center[0] + radius, center[1] + radius)]
    _fail("unsupported_kind", "L1 entity kind not supported", kind=kind)


def _body_bbox(body: Mapping[str, Any]) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for entity in _as_entity_list(body):
        if not isinstance(entity, Mapping):
            _fail("invalid_argument", "L1 entity must be a mapping", got=entity)
        for point in _entity_points(entity):
            xs.append(point[0])
            ys.append(point[1])
    return (min(xs), min(ys), max(xs), max(ys))


def _plane_trace(doc: Any, mirror_plane_ref: Any) -> tuple[tuple[int, int], tuple[int, int]]:
    """解析镜像面在 L1 草图面上的迹线：给映射，或给基准面记录的 uuid。"""
    plane: Any = None
    if isinstance(mirror_plane_ref, Mapping):
        plane = mirror_plane_ref
    elif isinstance(mirror_plane_ref, str):
        table = _bucket(doc, PLANE_BUCKETS)
        if table is None:
            _fail("unknown_reference", "no datum/plane table in L1 document",
                  ref=mirror_plane_ref)
        for record in table:
            if isinstance(record, Mapping) and record.get("uuid") == mirror_plane_ref:
                plane = record
                break
        if plane is None:
            _fail("unknown_reference", "mirror plane not found", ref=mirror_plane_ref)
    else:
        _fail("invalid_argument", "mirror_plane_ref must be a plane record or uuid",
              got=mirror_plane_ref)
    p1 = _as_nm2(plane.get("p1_nm"), what="p1_nm")
    p2 = _as_nm2(plane.get("p2_nm"), what="p2_nm")
    if p1 == p2:
        _fail("invalid_argument", "mirror plane trace degenerate (p1 == p2)", p1_nm=p1)
    return p1, p2


def _reflect(point: tuple[int, int], p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int]:
    """关于过 p1/p2 的直线做整数纳米精确反射；斜迹线不整除即 geometry_error。"""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    if dx == 0:
        return (2 * p1[0] - point[0], point[1])
    if dy == 0:
        return (point[0], 2 * p1[1] - point[1])
    denom = dx * dx + dy * dy
    t = Fraction((point[0] - p1[0]) * dx + (point[1] - p1[1]) * dy, denom)
    px = 2 * (Fraction(p1[0]) + t * dx) - point[0]
    py = 2 * (Fraction(p1[1]) + t * dy) - point[1]
    if px.denominator != 1 or py.denominator != 1:
        _fail("geometry_error", "oblique trace yields sub-nm mirror coordinates",
              point_nm=point, p1_nm=p1, p2_nm=p2)
    return (int(px), int(py))


def _mirror_entity(entity: Any, p1: tuple[int, int], p2: tuple[int, int]) -> dict[str, Any]:
    if not isinstance(entity, Mapping):
        _fail("invalid_argument", "L1 entity must be a mapping", got=entity)
    kind = _kind_of(entity)
    out = copy.deepcopy(dict(entity))
    if kind == "point":
        out["point_nm"] = _reflect(_as_nm2(entity.get("point_nm"), what="point_nm"), p1, p2)
    elif kind == "line":
        out["p1_nm"] = _reflect(_as_nm2(entity.get("p1_nm"), what="p1_nm"), p1, p2)
        out["p2_nm"] = _reflect(_as_nm2(entity.get("p2_nm"), what="p2_nm"), p1, p2)
    elif kind == "circle":
        out["center_nm"] = _reflect(_as_nm2(entity.get("center_nm"), what="center_nm"), p1, p2)
    elif kind == "arc":
        out["center_nm"] = _reflect(_as_nm2(entity.get("center_nm"), what="center_nm"), p1, p2)
        start = entity.get("start_udeg")
        end = entity.get("end_udeg")
        if (isinstance(start, bool) or not isinstance(start, int)
                or isinstance(end, bool) or not isinstance(end, int)):
            _fail("invalid_argument", "arc angles must be int udeg", got=(start, end))
        out["start_udeg"] = (-end) % FULL_CIRCLE_UDEG
        out["end_udeg"] = (-start) % FULL_CIRCLE_UDEG
    else:
        _fail("unsupported_kind", "L1 entity kind not supported", kind=kind)
    return out


def _crosses_trace(body: Mapping[str, Any], p1: tuple[int, int],
                   d: tuple[int, int]) -> bool:
    """实体包围盒是否跨越镜像面迹线（决定 merge_solids 的并集是否成立）。"""
    x0, y0, x1, y1 = _body_bbox(body)
    values = [((cx - p1[0]) * d[1] - (cy - p1[1]) * d[0])
              for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    return min(values) <= 0 <= max(values)


def mirror_bodies(sk_or_doc: Any, *, body_refs: Any, mirror_plane_ref: Any = None,
                  merge_solids: Any = False) -> list[str]:
    """按实体（body）镜像：返回新 body uuid 列表（merge_solids=True 时返回源 body uuid）。

    生产路径：L1 无多实体表 ⇒ CadError(reason="unsupported_kind") 显式降级（不静默按特征镜像）。
    L1 实体级路径：文档携带 "bodies"/"solids" 表时按实体真镜像（整数纳米精确）。
    其它 reason：invalid_argument / unknown_reference / unsupported_kind / geometry_error。
    """
    if not isinstance(body_refs, (list, tuple)) or not body_refs:
        _fail("invalid_argument", "body_refs must be a non-empty list", got=body_refs)
    if not isinstance(merge_solids, bool):
        _fail("invalid_argument", "merge_solids must be a bool", got=merge_solids)
    if merge_solids and len(body_refs) != 1:
        _fail("invalid_argument", "merge_solids requires exactly one body_ref",
              got=list(body_refs))

    table = _bucket(sk_or_doc, BODY_BUCKETS)
    if table is None:
        # L1 装不下：显式降级，绝不退化成按特征镜像（spec 该条 test_vector 原文要求）
        _fail("unsupported_kind",
              "L1 document model has no multi-body (body/solid) table; degrade explicitly "
              "instead of falling back to feature mirroring",
              l1_bodies="absent")

    by_uuid: dict[str, dict[str, Any]] = {}
    for record in table:
        if isinstance(record, dict) and isinstance(record.get("uuid"), str):
            by_uuid[record["uuid"]] = record
    bodies: list[dict[str, Any]] = []
    for ref in body_refs:
        if not isinstance(ref, str):
            _fail("invalid_argument", "body ref must be a uuid string", got=ref)
        record = by_uuid.get(ref)
        if record is None:
            _fail("unknown_reference", "body not found in L1 body table", ref=ref)
        bodies.append(record)

    p1, p2 = _plane_trace(sk_or_doc, mirror_plane_ref)
    direction = (p2[0] - p1[0], p2[1] - p1[1])

    produced: list[str] = []
    for record in bodies:
        entities = _as_entity_list(record)
        mirrored = [_mirror_entity(entity, p1, p2) for entity in entities]
        if merge_solids:
            if not _crosses_trace(record, p1, direction):
                _fail("invalid_argument",
                      "merge_solids=True but the mirrored copy does not touch the source body",
                      ref=record.get("uuid"))
            record["entities"] = list(entities) + mirrored
            produced.append(str(record.get("uuid")))
        else:
            new_uuid = _uuid.uuid4().hex
            table.append({"uuid": new_uuid, "kind": "body", "entities": mirrored,
                          "mirrored_from": record.get("uuid"), "merge_solids": False})
            produced.append(new_uuid)
    return produced


def selftest() -> int:
    """硬断言自测：① 生产路径显式降级且零副作用 ② L1 实体级真几何 ③ 参数守卫。"""
    checks: list[tuple[str, bool]] = []
    x_plane = {"p1_nm": (0, 0), "p2_nm": (0, 10000000)}  # x = 0 的镜像面迹线

    # ① spec test_vector 原文：单实体文档（无 body 表）⇒ unsupported_kind，且不得静默退化成按特征镜像
    doc = {"name": "single-body",
           "entities": [{"kind": "circle", "center_nm": (10000000, 0), "radius_nm": 2000000}]}
    before = copy.deepcopy(doc)
    try:
        mirror_bodies(doc, body_refs=["b1"], mirror_plane_ref=x_plane)
        checks.append(("single_body_doc_unsupported_kind", False))
    except CadError as exc:
        checks.append(("single_body_doc_unsupported_kind", exc.reason == "unsupported_kind"))
    checks.append(("failed_path_leaves_doc_untouched", doc == before))

    # ② L1 实体级真几何：圆 (10mm,0) r=2mm 关于 x=0 镜像 ⇒ 新实体 (-10mm,0)，源实体不动
    body = {"uuid": "b1", "kind": "body",
            "entities": [{"kind": "circle", "center_nm": (10000000, 0), "radius_nm": 2000000}]}
    doc2 = {"bodies": [body]}
    new_uuids = mirror_bodies(doc2, body_refs=["b1"], mirror_plane_ref=x_plane)
    checks.append(("mirror_returns_one_new_body",
                   len(new_uuids) == 1 and new_uuids[0] != "b1"))
    mirrored = doc2["bodies"][-1]["entities"][0]
    checks.append(("mirrored_center_and_radius_exact",
                   mirrored["center_nm"] == (-10000000, 0) and mirrored["radius_nm"] == 2000000))
    checks.append(("source_body_untouched_and_two_bodies",
                   body["entities"][0]["center_nm"] == (10000000, 0)
                   and len(doc2["bodies"]) == 2))

    # ③ merge_solids=True：并回源实体（返回源 uuid、实体数翻倍、不新开 body）
    doc3 = {"bodies": [{"uuid": "b1", "kind": "body",
                        "entities": [{"kind": "line", "p1_nm": (-3000000, 0),
                                      "p2_nm": (1000000, 0)}]}]}
    merged = mirror_bodies(doc3, body_refs=["b1"], mirror_plane_ref=x_plane, merge_solids=True)
    checks.append(("merge_returns_source_uuid",
                   merged == ["b1"] and len(doc3["bodies"]) == 1))
    checks.append(("merge_appends_mirrored_entities",
                   len(doc3["bodies"][0]["entities"]) == 2
                   and doc3["bodies"][0]["entities"][1]["p1_nm"] == (3000000, 0)
                   and doc3["bodies"][0]["entities"][1]["p2_nm"] == (-1000000, 0)))

    # ④ 参数守卫：未知引用 / 不交叠的 merge / 退化迹线，三条都必须红
    try:
        mirror_bodies({"bodies": []}, body_refs=["nope"], mirror_plane_ref=x_plane)
        checks.append(("unknown_body_ref_raises", False))
    except CadError as exc:
        checks.append(("unknown_body_ref_raises", exc.reason == "unknown_reference"))

    doc4 = {"bodies": [{"uuid": "b1", "kind": "body",
                        "entities": [{"kind": "circle", "center_nm": (10000000, 0),
                                      "radius_nm": 2000000}]}]}
    try:
        mirror_bodies(doc4, body_refs=["b1"], mirror_plane_ref=x_plane, merge_solids=True)
        checks.append(("merge_without_overlap_raises", False))
    except CadError as exc:
        checks.append(("merge_without_overlap_raises", exc.reason == "invalid_argument"))

    try:
        mirror_bodies(doc2, body_refs=["b1"], mirror_plane_ref={"p1_nm": (5, 5), "p2_nm": (5, 5)})
        checks.append(("degenerate_trace_raises", False))
    except CadError as exc:
        checks.append(("degenerate_trace_raises", exc.reason == "invalid_argument"))

    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    failed = [name for name, passed in checks if not passed]
    print(f"  asserts={len(checks)} failed={len(failed)}")
    return 0 if not failed else 1


def main() -> int:
    """判定行：status 如实标 missing（L1 无多实体承载），PASS 只代表本片契约自测通过。"""
    print("SW_PATTERN_REFGEOM__W21 item=4 name=mirror-bodies status=missing "
          "l1_multi_body=absent path=explicit_unsupported_kind")
    rc = selftest()
    if rc != 0:
        print("FEATURE_FAIL family=sw_pattern_refgeom op=mirror_bodies verdict=FAIL")
        return rc
    print("FEATURE_OK family=sw_pattern_refgeom op=mirror_bodies verdict=PASS")
    return 0


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mirror_bodies": mirror_bodies,
}


if __name__ == "__main__":
    raise SystemExit(main())
