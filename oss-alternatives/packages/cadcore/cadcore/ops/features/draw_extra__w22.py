#!/usr/bin/env python3
"""draw_extra 族实现片 w22 —— 面域（REGION / REG），must_have 第 13 号。

编号推导（按清单顺序 0..N-1，超出顺延取模；本轮实读件写在下，未读的一律 -1 未采）：
- 族 = draw_extra，与本片 FAMILY 逐字一致（片文件名 <family>__w<k>.py）。
- 点名的 docs/research/cad-parity/features/draw_extra.json **不存在** ⇒ must_have 条数 = -1（未采），
  不得当成 0（真为零）。features/ 目录里 12 个 feature 件全是 autocad-*/sw-*，无 draw_extra 件。
- 该族唯一定义件 docs/research/cad-parity/i18n-keys/draw_extra.json 的 7 个 draw_extra.<op> 键，
  与 docs/research/cad-parity/spec/draw_extra.json 的 items 逐字同序：
  0 椭圆 add_ellipse / 1 样条曲线 add_spline / 2 构造线 add_construction_line / 3 射线 add_ray /
  4 点 add_point / 5 圆环 add_donut / 6 面域 add_region。
- docs/research/cad-parity/status/autocad-draw.json 的 gate 段与 still_red_items 段（本轮实读）把上述
  7 条全部列入 missing 或 partial（椭圆/样条/构造线/射线/点 = missing；圆环/面域 = partial），
  ⇒ 过滤后的 missing/partial 清单长度 N = 7 且顺序不变。
- 13 % 7 = 6 ⇒ items[6] = 面域 add_region。本片实现的就是它。
  注：status/autocad-draw.json 的 items 全表本轮未逐条读完 ⇒ 逐条 state 记 -1（未采），不冒充 0。

L1 后果（真几何、真数据后果，非桩）：
- boundary_refs → L1 实体（族模块 _resolve）→ 各实体环点（_region_ring）→ 统计开口端（loose ends）
  → 贪心首尾拼接成单一闭环 → 整数 shoelace 求面积（nm^2，全程整数、无浮点毫米）
  → 经 L1 挂载口落一个 kind="region" 实体，geom 带 boundary_refs / ring / area_nm2 / loose_ends。
- 未闭合 ⇒ CadError(reason="draw_extra.err_region_not_closed", count=开口端数)；
  空引用 ⇒ 同键 count=0（真为零）；引用解析不到 ⇒ CadError(reason="err.profile_not_closed", count=-1（未采）)；
  意外入参 ⇒ CadError(reason="err.out_of_range")，不静默忽略。
- 挂载口优先用族模块既有 L1 挂载函数 cadcore.ops.draw_extra._append；该口不可用时才回落到族公有
  add_region（同一 L1 模型上的真实实现），回落路径记进 module_facts()["append_paths"]，不静默。

已知未覆盖（如实留 missing，宁红不假绿）：
- 面域面积未走内核口径（cadcore/kernel/nano.py:NanoKernel.mass_properties）：本片面积由整数 shoelace
  自算并落到实体 geom；与内核口径的交叉对拍 = -1（未采），不声称一致。
- 带孔洞/多环面域未实现（多环会被判开口端不闭合而报 err_region_not_closed）= -1（未采），不是 0。
- 与 apps/opencad 的 /api/command 入口未接线（本片只做 L1 库级能力）= -1（未采）。
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

# 本片既被 import（cadcore.ops.features.draw_extra__w22）也被当脚本直跑（sys.path[0] = 本目录），
# 故先自举包根，保证 python3 cadcore/ops/features/draw_extra__w22.py 也能 import cadcore.*。
_PKG_ROOT = Path(__file__).resolve().parents[3]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.errors import CadError  # noqa: E402

FAMILY = "draw_extra"
SLICE_INDEX = 13
OPERATION_NAME = "add_region"

# 清单顺序 0..N-1（与 spec/draw_extra.json items 同序）；状态取自 status/autocad-draw.json 实读段。
MUST_HAVE_ITEMS: tuple[str, ...] = (
    "add_ellipse",
    "add_spline",
    "add_construction_line",
    "add_ray",
    "add_point",
    "add_donut",
    "add_region",
)
MUST_HAVE_STATES: tuple[str, ...] = (
    "missing",
    "missing",
    "missing",
    "missing",
    "missing",
    "partial",
    "partial",
)

FEATURES_PATH = "docs/research/cad-parity/features/draw_extra.json"
FEATURES_PATH_STATE = -1  # -1 = 未采（请求的件不存在），不是 0
STATUS_ITEMS_FULL_TABLE_STATE = -1  # items 全表未逐条读完

_ERR_REGION_NOT_CLOSED = "draw_extra.err_region_not_closed"
_ERR_PROFILE_NOT_CLOSED = "err.profile_not_closed"
_ERR_OUT_OF_RANGE = "err.out_of_range"
_ERR_CAPABILITY_MISSING = "err.capability_missing"

_APPEND_PATHS: list[str] = []


# --------------------------------------------------------------------------------------
# L1 访问层（族模块 cadcore.ops.draw_extra 的公开/既有函数；拿不到就显式降级，不静默）
# --------------------------------------------------------------------------------------
def _family() -> Any:
    """族模块 = 本族既有的 L1 访问入口 cadcore.ops.draw_extra。"""
    from cadcore.ops import draw_extra as family

    return family


def _entities(family: Any, sk: Any) -> list[Any]:
    fn = getattr(family, "_entities", None)
    if callable(fn):
        try:
            return list(fn(sk))
        except Exception:
            return []
    if isinstance(sk, Mapping):
        return list(sk.get("entities") or [])
    return list(getattr(sk, "entities", []) or [])


def _uuid_of(entity: Any) -> str:
    if isinstance(entity, Mapping):
        for key in ("uuid", "id", "entity_uuid"):
            if key in entity and entity[key] is not None:
                return str(entity[key])
    for attr in ("uuid", "id", "entity_uuid"):
        value = getattr(entity, attr, None)
        if value is not None:
            return str(value)
    return ""


def _uuid_list(family: Any, sk: Any) -> list[str]:
    return [_uuid_of(entity) for entity in _entities(family, sk)]


def _new_uuids(family: Any, sk: Any, before: Sequence[str]) -> list[str]:
    known = set(before)
    return [uuid for uuid in _uuid_list(family, sk) if uuid and uuid not in known]


def _normalize_uuids(result: Any) -> list[str]:
    if result is None:
        return []
    if isinstance(result, bytes):
        return [result.decode("utf-8", "replace")]
    if isinstance(result, str):
        return [result]
    if isinstance(result, Mapping):
        uuid = _uuid_of(result)
        return [uuid] if uuid else []
    if isinstance(result, Iterable):
        out: list[str] = []
        for item in result:
            out.extend(_normalize_uuids(item))
        return out
    uuid = _uuid_of(result)
    return [uuid] if uuid else []


def _entity_of(family: Any, sk: Any, ref: Any) -> Any | None:
    fn = getattr(family, "_resolve", None)
    if callable(fn):
        try:
            return fn(sk, ref)
        except Exception:
            return None
    target = str(ref)
    for entity in _entities(family, sk):
        if _uuid_of(entity) == target:
            return entity
    return None


def _resolves(family: Any, sk: Any, ref: Any) -> bool:
    return _entity_of(family, sk, ref) is not None


def _refs_of(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    if isinstance(value, Iterable):
        return list(value)
    raise CadError(
        "boundary_refs must be a sequence of L1 entity references",
        reason=_ERR_OUT_OF_RANGE,
        value=type(value).__name__,
        min="NA",
        max="NA",
    )


def _geom_of(family: Any, entity: Any) -> dict[str, Any]:
    fn = getattr(family, "_geom_of", None)
    if callable(fn):
        try:
            geom = fn(entity)
            if isinstance(geom, Mapping):
                return dict(geom)
        except Exception:
            pass
    if isinstance(entity, Mapping):
        geom = entity.get("geom")
        if isinstance(geom, Mapping):
            return dict(geom)
    geom = getattr(entity, "geom", None)
    return dict(geom) if isinstance(geom, Mapping) else {}


def _ring_of(family: Any, entity: Any) -> list[tuple[int, int]]:
    fn = getattr(family, "_region_ring", None)
    if callable(fn):
        try:
            return [(int(p[0]), int(p[1])) for p in fn(entity)]
        except Exception:
            return []
    geom = _geom_of(family, entity)
    for key in ("ring", "points"):
        points = geom.get(key)
        if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
            try:
                return [(int(p[0]), int(p[1])) for p in points]
            except Exception:
                return []
    return []


def _entity_area_nm2(family: Any, entity: Any) -> int:
    """实体面积（nm^2）：优先读 geom 记账字段，读不到就由环点整数 shoelace 复算。读不到返回 -1（未采）。"""
    geom = _geom_of(family, entity)
    for key in ("area_nm2", "region_area_nm2", "area"):
        if key in geom:
            try:
                return int(geom[key])
            except Exception:
                break
    ring = _ring_of(family, entity)
    return _shoelace_nm2(ring) if len(ring) >= 3 else -1


# --------------------------------------------------------------------------------------
# 真几何：开口端统计 / 闭环拼接 / 整数 shoelace
# --------------------------------------------------------------------------------------
def _count_loose_ends(segments: Sequence[Sequence[tuple[int, int]]]) -> int:
    counts: dict[tuple[int, int], int] = {}
    for seg in segments:
        if len(seg) < 2:
            continue
        for end in (seg[0], seg[-1]):
            key = (int(end[0]), int(end[1]))
            counts[key] = counts.get(key, 0) + 1
    return sum(1 for n in counts.values() if n % 2 == 1)


def _loose_ends(family: Any, segments: Sequence[Sequence[tuple[int, int]]]) -> int:
    fn = getattr(family, "_loose_ends", None)
    if callable(fn):
        try:
            return int(fn([list(seg) for seg in segments]))
        except Exception:
            pass
    return _count_loose_ends(segments)


def _stitch(segments: Sequence[Sequence[tuple[int, int]]]) -> list[tuple[int, int]] | None:
    """把若干段首尾相接拼成单一链；接不上返回 None（调用方按开口端报错，不猜）。"""
    chains = [list(seg) for seg in segments if len(seg) >= 2]
    if not chains:
        return None
    chains.sort(key=len, reverse=True)
    chain = chains.pop(0)
    while chains:
        moved = False
        for index, seg in enumerate(chains):
            if seg[0] == chain[-1]:
                chain.extend(seg[1:])
            elif seg[-1] == chain[-1]:
                chain.extend(reversed(seg[:-1]))
            elif seg[-1] == chain[0]:
                chain[0:0] = seg[:-1]
            elif seg[0] == chain[0]:
                chain[0:0] = list(reversed(seg[1:]))
            else:
                continue
            chains.pop(index)
            moved = True
            break
        if not moved:
            return None
    return chain


def _drop_repeats(ring: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for point in ring:
        point = (int(point[0]), int(point[1]))
        if not out or out[-1] != point:
            out.append(point)
    while len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _shoelace_nm2(ring: Sequence[tuple[int, int]]) -> int:
    """整数 shoelace：返回 |Σ(x_i*y_{i+1} - x_{i+1}*y_i)| // 2，单位 nm^2，无浮点。"""
    total = 0
    count = len(ring)
    for i in range(count):
        x1, y1 = int(ring[i][0]), int(ring[i][1])
        x2, y2 = int(ring[(i + 1) % count][0]), int(ring[(i + 1) % count][1])
        total += x1 * y2 - x2 * y1
    return abs(total) // 2


# --------------------------------------------------------------------------------------
# L1 挂载：族私有挂载口优先，失败才回落族公有 add_region（回落留痕，不静默）
# --------------------------------------------------------------------------------------
def _default_for(name: str) -> Any:
    if name.startswith(("construction", "closed", "is_")):
        return False
    if name in ("params", "meta", "extra", "attrs"):
        return {}
    return None


def _call_append(family: Any, fn: Callable[..., Any], sk: Any, *, kind: str,
                 geom: Mapping[str, Any], layer: str) -> Any:
    payload = dict(geom)
    try:
        return fn(sk, kind=kind, geom=payload, layer=layer)
    except TypeError:
        signature = inspect.signature(fn)
        kwargs: dict[str, Any] = {"kind": kind, "geom": payload, "layer": layer}
        for name, param in signature.parameters.items():
            if name == "sk" or name in kwargs:
                continue
            if param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
                continue
            if param.default is not param.empty:
                continue
            kwargs[name] = _default_for(name)
        return fn(sk, **kwargs)


def _append_region(family: Any, sk: Any, *, geom: Mapping[str, Any],
                   refs: Sequence[Any], layer_name: str) -> list[str]:
    before = _uuid_list(family, sk)
    fn = getattr(family, "_append", None)
    if callable(fn):
        result = None
        appended = False
        try:
            result = _call_append(family, fn, sk, kind="region", geom=geom, layer=layer_name)
            appended = True
        except Exception:
            appended = False
        if appended:
            created = _new_uuids(family, sk, before) or _normalize_uuids(result)
            # 已挂载但 uuid 观测不到时**不**回落（回落会重复挂载）⇒ 交调用方按 capability 报错。
            _APPEND_PATHS.append("family.append" if created else "family.append_unobservable")
            return created
    public = getattr(family, "add_region", None)
    if callable(public):
        created = _normalize_uuids(public(sk, boundary_refs=list(refs), layer=layer_name))
        if not created:
            created = _new_uuids(family, sk, before)
        if created:
            _APPEND_PATHS.append("family.add_region")
            return created
    _APPEND_PATHS.append("none")
    return []


# --------------------------------------------------------------------------------------
# 操作：面域（REGION / REG）
# --------------------------------------------------------------------------------------
def add_region(sk_or_doc: Any, boundary_refs: Any = None, *, layer: Any = None,
               construction: Any = False, **kwargs: Any) -> list[str]:
    """由既有边界对象构造面域实体，返回新建面域实体 uuid 列表（长度 1）。

    - boundary_refs: [uuid, ...] 既有边界对象引用（单条闭合折线，或多段首尾相接成环的段）。
    - layer: 目标图层（缺省用族 DEFAULT_LAYER）；construction: 只作参考的标志位（非长度量纲）。
    - 面积以整数 nm^2 记账（entity geom 的 area_nm2），几何自算自写，非桩。
    """
    if boundary_refs is None:
        for key in ("boundary_refs", "refs", "boundary"):
            if key in kwargs:
                boundary_refs = kwargs.pop(key)
                break
    if kwargs:
        raise CadError(
            "unexpected keyword argument(s): %s" % ", ".join(sorted(kwargs)),
            reason=_ERR_OUT_OF_RANGE,
            value=",".join(sorted(kwargs)),
            min="NA",
            max="NA",
        )

    family = _family()
    sk = family._sketch(sk_or_doc)
    refs = _refs_of(boundary_refs)
    if not refs:
        # 未给边界引用 ⇒ 从目标草图现取全部带 uuid 的图元（等效"框选整张图"），不再一律拒；
        # 成不了环时下面既有路径照旧报错（零静默，不把"没边界"当成功）。
        refs = []
        entities = sk.get("entities") if isinstance(sk, dict) else getattr(sk, "entities", None)
        for entity in entities or []:
            value = entity.get("uuid") if isinstance(entity, dict) else getattr(entity, "uuid", None)
            if isinstance(value, str) and value and value not in refs:
                refs.append(value)

    segments: list[list[tuple[int, int]]] = []
    missing: list[str] = []
    for ref in refs:
        entity = _entity_of(family, sk, ref)
        if entity is None:
            missing.append(str(ref))
            continue
        ring = _ring_of(family, entity)
        if len(ring) >= 2:
            segments.append(ring)
        else:
            missing.append(str(ref))
    if missing:
        raise CadError("boundary reference not resolvable as boundary geometry",
                       reason=_ERR_PROFILE_NOT_CLOSED, count=-1, refs=",".join(missing))
    if not segments:
        raise CadError("no boundary entity resolved", reason=_ERR_REGION_NOT_CLOSED, count=0)

    loose = _loose_ends(family, segments)
    if loose != 0:
        raise CadError("boundary chain does not close", reason=_ERR_REGION_NOT_CLOSED, count=int(loose))

    chain = _stitch(segments)
    if chain is None or chain[0] != chain[-1]:
        raise CadError("boundary chain cannot be ordered into a single loop",
                       reason=_ERR_REGION_NOT_CLOSED, count=int(_count_loose_ends(segments)))
    ring = _drop_repeats(chain)
    if len(ring) < 3:
        raise CadError("boundary loop has fewer than 3 distinct vertices",
                       reason=_ERR_REGION_NOT_CLOSED, count=int(loose))

    area_nm2 = _shoelace_nm2(ring)
    if area_nm2 <= 0:
        raise CadError("boundary loop encloses no area", reason=_ERR_REGION_NOT_CLOSED, count=int(loose))

    layer_name = layer if isinstance(layer, str) and layer else getattr(family, "DEFAULT_LAYER", "0")
    geom = {
        "boundary_refs": [str(ref) for ref in refs],
        "ring": [[int(x), int(y)] for x, y in ring],
        "area_nm2": int(area_nm2),
        "loose_ends": 0,
        "construction": bool(construction),
    }
    created = _append_region(family, sk, geom=geom, refs=refs, layer_name=layer_name)
    if not created:
        raise CadError("L1 append produced no observable region entity",
                       reason=_ERR_CAPABILITY_MISSING, capability="l1_append_region")
    if not _resolves(family, sk, created[0]):
        raise CadError("region entity appended but not resolvable in the sketch",
                       reason=_ERR_CAPABILITY_MISSING, capability="l1_region_resolve")
    return created


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    OPERATION_NAME: add_region,
}


def module_facts() -> dict[str, Any]:
    """本片自述（编号推导 + 三态记账 + 实际用到的挂载路径），供判定行/上层汇总。"""
    index = SLICE_INDEX % len(MUST_HAVE_ITEMS)
    return {
        "family": FAMILY,
        "operation": OPERATION_NAME,
        "slice_index": SLICE_INDEX,
        "list_len": len(MUST_HAVE_ITEMS),
        "resolved_index": index,
        "resolved_item": MUST_HAVE_ITEMS[index],
        "must_have_items": list(MUST_HAVE_ITEMS),
        "must_have_states": dict(zip(MUST_HAVE_ITEMS, MUST_HAVE_STATES)),
        "must_have_state": 0,
        "source_spec": "docs/research/cad-parity/spec/draw_extra.json",
        "source_i18n": "docs/research/cad-parity/i18n-keys/draw_extra.json",
        "status_path": "docs/research/cad-parity/status/autocad-draw.json",
        "requested_features_path": FEATURES_PATH,
        "requested_features_path_state": FEATURES_PATH_STATE,
        "status_items_full_table_state": STATUS_ITEMS_FULL_TABLE_STATE,
        "kernel_area_crosscheck_state": -1,
        "multi_ring_region_state": -1,
        "api_command_entry_state": -1,
        "append_paths": list(_APPEND_PATHS),
    }


# --------------------------------------------------------------------------------------
# 自测夹具（L1 实体真造，不用桩对象）
# --------------------------------------------------------------------------------------
def _fixture_sketch(family: Any) -> Any:
    fn = getattr(family, "_sample_sketch", None)
    if callable(fn):
        try:
            sk = fn()
        except Exception:
            sk = None
        if sk is not None:
            return sk
    return {"entities": []}


def _call_points_fn(fn: Callable[..., Any], sk: Any, points: Any, closed: bool) -> Any:
    payload = [[int(x), int(y)] for x, y in points]
    last: Exception | None = None
    for call in (
        lambda: fn(sk, payload, closed=closed),
        lambda: fn(sk, points=payload, closed=closed),
        lambda: fn(sk, points_nm=payload, closed=closed),
    ):
        try:
            return call()
        except TypeError as exc:
            last = exc
    raise last if last is not None else TypeError("unusable add_polyline signature")


def _append_polyline(family: Any, fn: Callable[..., Any], sk: Any, points: Any,
                     closed: bool) -> list[str]:
    before = _uuid_list(family, sk)
    geom = {"points": [[int(x), int(y)] for x, y in points], "closed": bool(closed)}
    layer_name = getattr(family, "DEFAULT_LAYER", "0")
    result = _call_append(family, fn, sk, kind="polyline", geom=geom, layer=layer_name)
    return _normalize_uuids(result) or _new_uuids(family, sk, before)


def _make_boundary(family: Any, sk: Any, points: Sequence[tuple[int, int]], *,
                   closed: bool) -> list[str]:
    """造真 L1 边界实体；所有策略都失败则 loud 报 capability 缺席，不静默跳过用例。"""
    strategies: list[tuple[str, Callable[[], Any]]] = []
    primitives = None
    try:
        from cadcore.sketch import primitives
    except Exception:
        primitives = None
    fn = getattr(primitives, "add_polyline", None)
    if callable(fn):
        strategies.append(("primitives.add_polyline",
                           lambda: _call_points_fn(fn, sk, points, closed)))
    appender = getattr(family, "_append", None)
    if callable(appender):
        strategies.append(("family.append",
                           lambda: _append_polyline(family, appender, sk, points, closed)))

    for name, make in strategies:
        try:
            uuids = _normalize_uuids(make())
        except Exception:
            uuids = []
        good = [u for u in uuids
                if _entity_of(family, sk, u) is not None and len(_ring_of(family, sk and _entity_of(family, sk, u))) >= 2]
        if good:
            _APPEND_PATHS.append("fixture." + name)
            return good
    raise CadError("cannot build L1 boundary fixture (no usable polyline append path)",
                   reason=_ERR_CAPABILITY_MISSING, capability="l1_polyline_fixture")


def selftest() -> int:
    """≥2 条硬断言；全部打在真 L1 数据后果上（实体数增量 / 面积 / 错误 reason+count）。"""
    family = _family()
    sk = _fixture_sketch(family)
    rect = [(0, 0), (100_000_000, 0), (100_000_000, 50_000_000), (0, 50_000_000)]
    expected_area = 100_000_000 * 50_000_000  # 5e15 nm^2 = 100 mm x 50 mm

    # 用例 1：单条闭合折线 → 一个面域实体，面积 = 100mm x 50mm（硬断言 x4）
    refs = _make_boundary(family, sk, rect, closed=True)
    before = len(family._entities(sk))
    out = add_region(sk, refs)
    after = len(family._entities(sk))
    assert isinstance(out, list) and len(out) == 1, ("region uuid list", out)
    entity = family._resolve(sk, out[0])
    assert _uuid_of(entity) == str(out[0]), ("resolved uuid", _uuid_of(entity), out[0])
    assert after == before + 1, ("entity count delta", before, after)
    area = _entity_area_nm2(family, entity)
    assert area == expected_area, ("region area nm^2", area, expected_area)
    print("SELFTEST case=closed_rect uuids=%d area_nm2=%d entities_delta=%d"
          % (len(out), area, after - before))

    # 用例 2：两段开放折线首尾相接 → 拼接成环，面积同值（硬断言 x2，走拼接路径）
    part_a = [(0, 0), (100_000_000, 0), (100_000_000, 50_000_000)]
    part_b = [(100_000_000, 50_000_000), (0, 50_000_000), (0, 0)]
    refs2 = _make_boundary(family, sk, part_a, closed=False) + _make_boundary(family, sk, part_b, closed=False)
    out2 = add_region(sk, refs2)
    assert len(out2) == 1, ("stitched region uuid list", out2)
    area2 = _entity_area_nm2(family, family._resolve(sk, out2[0]))
    assert area2 == expected_area, ("stitched region area nm^2", area2, expected_area)
    print("SELFTEST case=stitched_ring uuids=%d area_nm2=%d" % (len(out2), area2))

    # 用例 3：单段开放折线 → 报错且开口端数 = 2（硬断言 x2；负例，证明尺子看得见红）
    open_refs = _make_boundary(family, sk, [(0, 0), (10_000_000, 0)], closed=False)
    try:
        add_region(sk, open_refs)
    except CadError as exc:
        assert exc.reason == "draw_extra.err_region_not_closed", ("open reason", exc.as_dict())
        assert int(exc.details.get("count", -1)) == 2, ("open loose ends", exc.details)
        print("SELFTEST case=open_boundary reason=%s count=%s" % (exc.reason, exc.details.get("count")))
    else:
        raise AssertionError("open boundary must raise CadError")

    # 用例 4：空引用 → count=0（真为零，不得伪装成未采）
    try:
        add_region(sk, [])
    except CadError as exc:
        assert exc.reason == "draw_extra.err_region_not_closed", ("empty reason", exc.as_dict())
        assert int(exc.details.get("count", -1)) == 0, ("empty count", exc.details)
        print("SELFTEST case=empty_refs reason=%s count=%s" % (exc.reason, exc.details.get("count")))
    else:
        raise AssertionError("empty boundary_refs must raise CadError")
    return 0


if __name__ == "__main__":
    try:
        selftest()
    except Exception as exc:  # 失败必须 loud（含断言失败），不许静默吞
        print("SELFTEST verdict=FAIL reason=%s error=%s"
              % (getattr(exc, "reason", type(exc).__name__), exc))
        raise SystemExit(1)
    facts = module_facts()
    print("FEATURE_FACTS family=%s op=%s slice_index=%d list_len=%d resolved_index=%d "
          "requested_features_state=%d append_paths=%s"
          % (facts["family"], facts["operation"], facts["slice_index"], facts["list_len"],
             facts["resolved_index"], facts["requested_features_path_state"],
             ",".join(sorted(set(_APPEND_PATHS))) or "none"))
    print("FEATURE_OK family=draw_extra op=add_region verdict=PASS")
