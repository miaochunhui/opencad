#!/usr/bin/env python3
"""draw_extra 族补片（w16 席）：渐变填充 GRADIENT —— 「图案填充与渐变色」条目的渐变色半边。

编号推导（本席留痕，免得下游把清单口径搞混）：

* 任务点名的 `docs/research/cad-parity/features/draw_extra.json` **在本仓不存在**；同目录另有
  `spec/draw_extra.json`，它自己记着 `source.requested_path_exists=false` / `must_have.state=-1`
  ⇒ 该文件层面的 must_have 条数 = **-1（未采）**，不得当成 0（真为零）。
* 可读清单取 `docs/research/cad-parity/status/autocad-draw.json` 的 `items`（该族 20 条，
  implemented=5 / partial=15 / missing=0）。**仍 missing/partial 的按清单顺序编号 0..14**：

  0 直线 / 1 多段线 / 2 圆 / 3 圆弧 / 4 矩形 / 5 正多边形 / 6 点与点样式 /
  **7 图案填充与渐变色** / 8 边界创建 / 9 修订云线 / 10 对象捕捉 / 11 正交与极轴追踪 /
  12 对象捕捉追踪与临时追踪点 / 13 动态输入 / 14 当前图层与对象特性

* 本席负责 **第 7 号 = 图案填充与渐变色**。该条 status=partial 的原因（status 件 evidence/note）：
  填充本体已由 hatch_measure 族覆盖（其族门 verdict=PASS），缺的是**渐变色**这一半未被本族门验证
  ⇒ 本片实现**渐变色（渐变填充）**，不重复造 hatch 填充。
* 备选清单与差异（显式记账，不互相冒充）：若改用 `spec/draw_extra.json` 的 7 个 i18n 操作当清单，
  7 mod 7 = 0 = `add_ellipse`，但该能力已在 `cadcore/ops/draw_extra.py:1008` 落库且族门记
  implemented ⇒ 不满足「仍 missing/partial」前提，故不取。

族接口（冻结，与本仓既有族模块一致）：

    FAMILY = "draw_extra"
    OPERATIONS: dict[str, Callable]          # add_gradient / gradient_list
    每个操作  op(sk_or_doc, **kwargs) -> list[str]

长度一律**整数纳米**（1 mm = 1_000_000 nm）；角度按 spec 的族口径用**整数微度 udeg**
（`1 deg = 1_000_000 udeg`），不接受 float。失败一律 `raise cadcore.errors.CadError(reason=...)`。

L1 落库（只用冻结的 5 个实体字段 `{uuid, kind, layer, construction, geom}`，不自造第六个字段）：

    kind = "gradient"
    geom = {ring_points, point_count, area_nm2, boundary_count,
            color_start, color_end, stops, stop_count, angle_udeg,
            axis_start, axis_end, bands, scheme}

真几何/真后果（**没有桩**）：边界段先按端点**首尾接成环**（接不上 / 多环 ⇒ not_closed），
环点串用整数鞋带公式现算面积；渐变轴由 `angle_udeg` 的整数化方向余弦投影到环点取 `[pmin, pmax]`，
轴端点与 `stops-1` 个渐变带的轴向区间都是**现算整数纳米**；色带按 `0xRRGGBB` 三通道整数线性插值。

已知边界（显式抛错，不静默近似）：①边界只接受 line / polyline / circle（circle 按 `segments`
等分折线采样）；②`stops` 限 2..256；③颜色限 0..0xFFFFFF。本片只落本文件，**不写** `__init__.py`；
把它挂进族模块 `cadcore/ops/draw_extra.py` 的 OPERATIONS 属别的席位。
"""

from __future__ import annotations

import math
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[3]  # .../packages/cadcore（cadcore 包所在根）
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from cadcore.errors import CadError
except Exception as _exc:  # pragma: no cover - 引导失败必须 loud，不静默降级
    raise SystemExit("draw_extra__w16: cannot import cadcore.errors: %s" % _exc)

FAMILY = "draw_extra"
NM_PER_MM = 1_000_000
DEFAULT_LAYER = "0"
SCALE = 1_000_000  # 方向余弦的整数标度（1.0 记作 1_000_000）
GRADIENT_KIND = "gradient"
DEFAULT_STOPS = 2
MAX_STOPS = 256
DEFAULT_SEGMENTS = 64

ERR_OUT_OF_RANGE = "draw_extra.err_out_of_range"
ERR_GEOMETRY_INVALID = "draw_extra.err_geometry_invalid"
ERR_NOT_CLOSED = "draw_extra.err_not_closed"
ERR_UNSUPPORTED_KIND = "draw_extra.err_unsupported_kind"
ERR_EMPTY_SELECTION = "draw_extra.err_empty_selection"

__all__ = ["FAMILY", "OPERATIONS", "NM_PER_MM", "add_gradient", "gradient_list"]


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造族内统一的 CadError（reason 机读，message 全 ASCII，不含硬编码中文文案）。"""
    return CadError(message, reason=reason, **details)


def _as_int(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(ERR_OUT_OF_RANGE, "%s must be an integer" % name, field=name)
    if positive and value <= 0:
        raise _fail(ERR_OUT_OF_RANGE, "%s must be > 0" % name, field=name, value=value)
    return int(value)


def _as_udeg(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(ERR_OUT_OF_RANGE, "%s must be an integer micro-degree" % name, field=name)
    return int(value)


def _as_color(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(ERR_OUT_OF_RANGE, "%s must be an integer 0xRRGGBB" % name, field=name)
    if value < 0 or value > 0xFFFFFF:
        raise _fail(ERR_OUT_OF_RANGE, "%s out of range 0..0xFFFFFF" % name, field=name, value=value)
    return int(value)


def _as_layer(layer: Any) -> str:
    if layer is None:
        return DEFAULT_LAYER
    text = str(layer)
    if not text:
        raise _fail(ERR_OUT_OF_RANGE, "layer must be a non-empty string", field="layer")
    return text


def _entity_list(sk: Any) -> list:
    ents = sk.get("entities") if isinstance(sk, Mapping) else getattr(sk, "entities", None)
    if not isinstance(ents, list):
        raise _fail(ERR_GEOMETRY_INVALID, "sketch has no mutable entity list", key="entities")
    return ents


def _pick_sketch(sketches: Any, sketch_index: Any) -> Any:
    if isinstance(sketches, (str, bytes)) or not isinstance(sketches, Sequence) or not sketches:
        raise _fail(ERR_GEOMETRY_INVALID, "document has no sketches", count=0)
    if isinstance(sketch_index, bool) or not isinstance(sketch_index, int):
        raise _fail(ERR_OUT_OF_RANGE, "sketch_index must be an integer", field="sketch_index")
    if sketch_index < 0 or sketch_index >= len(sketches):
        raise _fail(ERR_OUT_OF_RANGE, "sketch_index out of range",
                    index=sketch_index, count=len(sketches))
    sk = sketches[sketch_index]
    if not isinstance(sk, Mapping):
        raise _fail(ERR_GEOMETRY_INVALID, "sketch is not a mapping", index=sketch_index)
    return sk


def _sketch_of(sk_or_doc: Any, sketch_index: int = 0) -> Any:
    """接受 L1 文档映射（sketches）/ 裸草图（entities）/ Document 形态（多种存取口径）。"""
    if isinstance(sk_or_doc, Mapping):
        if "entities" in sk_or_doc and "sketches" not in sk_or_doc:
            return sk_or_doc
        if "sketches" in sk_or_doc:
            return _pick_sketch(sk_or_doc["sketches"], sketch_index)
        if "entities" in sk_or_doc:
            return sk_or_doc
        raise _fail(ERR_GEOMETRY_INVALID, "mapping has neither 'entities' nor 'sketches'")
    sketches = getattr(sk_or_doc, "sketches", None)
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)) and sketches:
        return _pick_sketch(sketches, sketch_index)
    for attr in ("payload", "data", "_payload"):
        holder = getattr(sk_or_doc, attr, None)
        if isinstance(holder, Mapping):
            return _sketch_of(holder, sketch_index)
    for meth in ("to_payload", "as_dict"):
        fn = getattr(sk_or_doc, meth, None)
        if callable(fn):
            payload = fn()
            if isinstance(payload, Mapping):
                return _sketch_of(payload, sketch_index)
    raise _fail(ERR_GEOMETRY_INVALID, "unsupported sk_or_doc type",
                type=type(sk_or_doc).__name__)


def _uuid_index(sk: Any) -> dict:
    index: dict = {}
    for ent in _entity_list(sk):
        if isinstance(ent, Mapping) and isinstance(ent.get("uuid"), str):
            index.setdefault(ent["uuid"], ent)
    return index


def _resolve_ref(index: Mapping, ref: Any) -> Mapping:
    if isinstance(ref, str):
        ent = index.get(ref)
        if ent is None:
            raise _fail(ERR_EMPTY_SELECTION, "boundary reference uuid not found", uuid=ref)
        return ent
    if isinstance(ref, Mapping):
        return ref
    raise _fail(ERR_GEOMETRY_INVALID, "boundary reference must be uuid or entity mapping")


def _segment_points(entity: Mapping, segments: int) -> list:
    """边界实体 → 点串（line/polyline 原样，circle 按 segments 等分采样）。"""
    kind = str(entity.get("kind", ""))
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        raise _fail(ERR_GEOMETRY_INVALID, "entity has no geom mapping", kind=kind)
    try:
        if kind == "line":
            return [(int(geom["x1"]), int(geom["y1"])),
                    (int(geom["x2"]), int(geom["y2"]))]
        if kind == "polyline":
            raw = geom.get("points")
            if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or len(raw) < 2:
                raise _fail(ERR_GEOMETRY_INVALID, "polyline needs at least 2 points", kind=kind)
            pts = [(int(item[0]), int(item[1])) for item in raw]
            if bool(geom.get("closed", False)) and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            return pts
        if kind == "circle":
            cx = int(geom.get("cx"))
            cy = int(geom.get("cy"))
            radius = geom.get("radius", geom.get("r"))
            r = _as_int(radius, "radius", positive=True)
            n = _as_int(segments, "segments", positive=True)
            if n < 8:
                raise _fail(ERR_OUT_OF_RANGE, "segments must be >= 8", value=n, min=8)
            pts = []
            for i in range(n):
                ang = 2.0 * math.pi * i / n
                pts.append((cx + int(round(r * math.cos(ang))),
                            cy + int(round(r * math.sin(ang)))))
            pts.append(pts[0])
            return pts
    except CadError:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise _fail(ERR_GEOMETRY_INVALID, "malformed %s geometry" % kind,
                    kind=kind, error=type(exc).__name__)
    raise _fail(ERR_UNSUPPORTED_KIND, "gradient boundary must be line/polyline/circle", kind=kind)


def _assert_closed(segments: list, boundary_count: int) -> int:
    """开口端计数 = 端点出现奇数次的坐标个数（闭合环为 0，真为零）。"""
    counts: dict = {}
    for seg in segments:
        for endpoint in (seg[0], seg[-1]):
            counts[endpoint] = counts.get(endpoint, 0) + 1
    loose = sum(1 for value in counts.values() if value % 2)
    if loose:
        raise _fail(ERR_NOT_CLOSED, "boundary segments do not form a closed ring",
                    loose_ends=loose, boundary_count=boundary_count)
    return loose


def _chain(segments: list, boundary_count: int) -> list:
    """按端点把段首尾相接成单一环；接不上 / 出现第二个闭环 ⇒ not_closed。"""
    remaining = [list(seg) for seg in segments]
    chain = remaining.pop(0)
    while remaining:
        if len(chain) > 2 and chain[0] == chain[-1]:
            raise _fail(ERR_NOT_CLOSED, "boundary has more than one closed ring",
                        loose_ends=0, boundary_count=boundary_count)
        for i, seg in enumerate(remaining):
            if seg[0] == chain[-1]:
                chain.extend(seg[1:]); remaining.pop(i); break
            if seg[-1] == chain[-1]:
                chain.extend(list(reversed(seg[:-1]))); remaining.pop(i); break
            if seg[-1] == chain[0]:
                chain[:0] = seg[:-1]; remaining.pop(i); break
            if seg[0] == chain[0]:
                chain[:0] = list(reversed(seg[1:])); remaining.pop(i); break
        else:
            raise _fail(ERR_NOT_CLOSED, "boundary segments do not chain",
                        loose_ends=0, boundary_count=boundary_count)
    if chain[0] != chain[-1]:
        raise _fail(ERR_NOT_CLOSED, "boundary chain is not closed",
                    loose_ends=0, boundary_count=boundary_count)
    return chain


def _shoelace_area(points: list) -> int:
    """整数鞋带公式（nm^2，精确；闭合点重复一位只贡献零边）。"""
    total = 0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) // 2


def _direction(angle_udeg: int) -> tuple:
    """角度微度 → 整数化单位方向余弦（标度 SCALE）。"""
    rad = math.radians(angle_udeg / float(NM_PER_MM))
    ux = int(round(math.cos(rad) * SCALE))
    uy = int(round(math.sin(rad) * SCALE))
    if ux == 0 and uy == 0:
        raise _fail(ERR_GEOMETRY_INVALID, "degenerate gradient direction", angle_udeg=angle_udeg)
    return ux, uy


def _project(points: list, ux: int, uy: int) -> tuple:
    values = [(x * ux + y * uy) // SCALE for x, y in points]
    return min(values), max(values)


def _axis_point(t: int, ux: int, uy: int) -> list:
    return [t * ux // SCALE, t * uy // SCALE]


def _ramp(color_start: int, color_end: int, stops: int) -> list:
    """0xRRGGBB 三通道整数线性插值（首末位精确等于入参）。"""
    r0, g0, b0 = (color_start >> 16) & 0xFF, (color_start >> 8) & 0xFF, color_start & 0xFF
    r1, g1, b1 = (color_end >> 16) & 0xFF, (color_end >> 8) & 0xFF, color_end & 0xFF
    span = stops - 1
    out = []
    for i in range(stops):
        r = r0 + (r1 - r0) * i // span
        g = g0 + (g1 - g0) * i // span
        b = b0 + (b1 - b0) * i // span
        out.append((r << 16) | (g << 8) | b)
    return out


def _bands(pmin: int, pmax: int, stops: int) -> list:
    """stops-1 个渐变带的轴向区间（现算整数纳米）。"""
    span = pmax - pmin
    out = []
    for i in range(stops - 1):
        a = pmin + span * i // (stops - 1)
        b = pmin + span * (i + 1) // (stops - 1)
        out.append([a, b])
    return out


def add_gradient(sk_or_doc: Any, *, boundary_refs: Any, color_start: int = 0x000000,
                 color_end: int = 0xFFFFFF, angle_udeg: int = 0, stops: int = DEFAULT_STOPS,
                 layer: Any = None, construction: bool = False, sketch_index: int = 0,
                 segments: int = DEFAULT_SEGMENTS) -> list[str]:
    """渐变填充：闭合边界 + 线性渐变色带 → 一条 `kind="gradient"` 实体，返回其 uuid 列表。

    真后果：环点串（真接环）、面积（整数鞋带）、渐变轴端点与 `stops-1` 个渐变带的轴向区间
    （由 `angle_udeg` 投影现算，单位纳米）、`stops` 个 0xRRGGBB 插值色 —— 全是现算值，无桩。

    边界接不成单一闭合环 ⇒ `CadError(reason="draw_extra.err_not_closed", loose_ends=<真数或-1>,
    boundary_count=<条数>)`；颜色/`stops`/几何越界 ⇒ `draw_extra.err_out_of_range` /
    `draw_extra.err_geometry_invalid`；不支持的边界 kind ⇒ `draw_extra.err_unsupported_kind`。
    """
    sk = _sketch_of(sk_or_doc, sketch_index)
    if isinstance(boundary_refs, (str, bytes)) or not isinstance(boundary_refs, Sequence):
        raise _fail(ERR_GEOMETRY_INVALID, "boundary_refs must be a sequence of refs", count=-1)
    refs = list(boundary_refs)
    if not refs:
        raise _fail(ERR_EMPTY_SELECTION, "boundary_refs is empty", count=0)

    stop_count = _as_int(stops, "stops", positive=True)
    if stop_count < 2 or stop_count > MAX_STOPS:
        raise _fail(ERR_OUT_OF_RANGE, "stops must satisfy 2 <= stops <= %d" % MAX_STOPS,
                    field="stops", value=stop_count, min=2, max=MAX_STOPS)
    c_start = _as_color(color_start, "color_start")
    c_end = _as_color(color_end, "color_end")
    udeg = _as_udeg(angle_udeg, "angle_udeg")
    layer_name = _as_layer(layer)

    index = _uuid_index(sk)
    segment_points = [_segment_points(_resolve_ref(index, ref), segments) for ref in refs]
    _assert_closed(segment_points, len(refs))
    ring = _chain(segment_points, len(refs))
    if len(ring) < 4:
        raise _fail(ERR_GEOMETRY_INVALID, "boundary ring needs at least 3 distinct points",
                    point_count=len(ring))

    area = _shoelace_area(ring)
    if area <= 0:
        raise _fail(ERR_GEOMETRY_INVALID, "degenerate boundary (zero area)", area_nm2=area)

    ux, uy = _direction(udeg)
    pmin, pmax = _project(ring, ux, uy)
    if pmax <= pmin:
        raise _fail(ERR_GEOMETRY_INVALID, "degenerate gradient extent", span=pmax - pmin)

    geom: dict = {
        "ring_points": [[int(x), int(y)] for x, y in ring],
        "point_count": len(ring),
        "area_nm2": area,
        "boundary_count": len(refs),
        "color_start": c_start,
        "color_end": c_end,
        "stops": _ramp(c_start, c_end, stop_count),
        "stop_count": stop_count,
        "angle_udeg": udeg,
        "axis_start": _axis_point(pmin, ux, uy),
        "axis_end": _axis_point(pmax, ux, uy),
        "bands": _bands(pmin, pmax, stop_count),
        "scheme": "linear_gradient_axial_bands_l1",
    }
    entity = {
        "uuid": str(_uuid.uuid4()),
        "kind": GRADIENT_KIND,
        "layer": layer_name,
        "construction": bool(construction),
        "geom": geom,
    }
    _entity_list(sk).append(entity)
    return [entity["uuid"]]


def gradient_list(sk_or_doc: Any, *, layer: Any = None, sketch_index: int = 0) -> list[str]:
    """纯查询：返回草图内渐变实体 uuid 列表；一个都没有 ⇒ `[]`（真为零，不是未采）。"""
    sk = _sketch_of(sk_or_doc, sketch_index)
    want = None if layer is None else _as_layer(layer)
    out = []
    for ent in _entity_list(sk):
        if not isinstance(ent, Mapping) or str(ent.get("kind", "")) != GRADIENT_KIND:
            continue
        if not isinstance(ent.get("uuid"), str):
            continue
        if want is None or str(ent.get("layer", DEFAULT_LAYER)) == want:
            out.append(ent["uuid"])
    return out


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "add_gradient": add_gradient,
    "gradient_list": gradient_list,
}


def _rect_doc():
    """自测夹具：30mm x 20mm 矩形（4 条 line，逆时针）。"""
    mm = NM_PER_MM

    def line(uuid_text, x1, y1, x2, y2):
        return {"uuid": uuid_text, "kind": "line", "layer": "0", "construction": False,
                "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}

    refs = [str(_uuid.uuid4()) for _ in range(4)]
    entities = [
        line(refs[0], 0, 0, 30 * mm, 0),
        line(refs[1], 30 * mm, 0, 30 * mm, 20 * mm),
        line(refs[2], 30 * mm, 20 * mm, 0, 20 * mm),
        line(refs[3], 0, 20 * mm, 0, 0),
    ]
    doc = {"sketches": [{"uuid": str(_uuid.uuid4()), "entities": entities}]}
    return doc, refs


def selftest() -> int:
    """自测：≥2 条硬断言（真几何 + 真色带），外加阴性对照证明尺子看得见红。"""
    checks: list = []

    def check(name, passed, detail=""):
        checks.append((name, bool(passed), detail))
        return bool(passed)

    doc, refs = _rect_doc()
    created = add_gradient(doc, boundary_refs=list(reversed(refs)), color_start=0x000000,
                           color_end=0xFFFFFF, angle_udeg=0, stops=3)
    entities = doc["sketches"][0]["entities"]
    check("returns_one_uuid", len(created) == 1 and isinstance(created[0], str),
          "n=%d" % len(created))
    grad = None
    for ent in entities:
        if ent.get("uuid") == created[0]:
            grad = ent
    check("appended_kind_gradient", grad is not None and grad.get("kind") == "gradient",
          "kind=%s" % (None if grad is None else grad.get("kind")))
    geom = {} if grad is None else grad.get("geom", {})
    check("entity_field_set_frozen",
          grad is not None and set(grad) == {"uuid", "kind", "layer", "construction", "geom"},
          "fields=%s" % (None if grad is None else sorted(grad)))
    area = geom.get("area_nm2")
    check("area_nm2_shoelace", area == 600000000000000, "area_nm2=%r" % (area,))
    check("ring_reversed_refs_chained", geom.get("point_count") == 5,
          "point_count=%r" % (geom.get("point_count"),))
    check("stops_ramp_exact", geom.get("stops") == [0x000000, 0x7F7F7F, 0xFFFFFF],
          "stops=%r" % (geom.get("stops"),))
    check("axis_angle0", geom.get("axis_start") == [0, 0]
          and geom.get("axis_end") == [30000000, 0],
          "start=%r end=%r" % (geom.get("axis_start"), geom.get("axis_end")))
    check("bands_angle0", geom.get("bands") == [[0, 15000000], [15000000, 30000000]],
          "bands=%r" % (geom.get("bands"),))

    doc90, refs90 = _rect_doc()
    created90 = add_gradient(doc90, boundary_refs=refs90, angle_udeg=90000000, stops=3)
    geom90 = None
    for ent in doc90["sketches"][0]["entities"]:
        if ent.get("uuid") == created90[0]:
            geom90 = ent["geom"]
    check("axis_angle90_rotates", geom90 is not None
          and geom90.get("axis_start") == [0, 0] and geom90.get("axis_end") == [0, 20000000]
          and geom90.get("bands") == [[0, 10000000], [10000000, 20000000]],
          "end=%r bands=%r" % (None if geom90 is None else geom90.get("axis_end"),
                               None if geom90 is None else geom90.get("bands")))

    check("query_returns_created", gradient_list(doc) == created,
          "listed=%r" % (gradient_list(doc),))
    empty = {"sketches": [{"uuid": str(_uuid.uuid4()), "entities": []}]}
    check("query_empty_is_true_zero", gradient_list(empty) == [], "listed=%r" % (gradient_list(empty),))

    def raises(reason, fn):
        try:
            fn()
        except CadError as exc:
            return exc.reason == reason, "reason=%s" % exc.reason
        except Exception as exc:  # pragma: no cover - 夹具自证：非 CadError 也算红
            return False, "wrong_exc=%s" % type(exc).__name__
        return False, "no_exc"

    open_doc, open_refs = _rect_doc()
    open_doc["sketches"][0]["entities"] = open_doc["sketches"][0]["entities"][:3]
    ok_open, detail_open = raises(ERR_NOT_CLOSED, lambda: add_gradient(
        open_doc, boundary_refs=open_refs[:3]))
    check("negative_open_ring_red", ok_open, detail_open)
    ok_color, detail_color = raises(ERR_OUT_OF_RANGE, lambda: add_gradient(
        doc, boundary_refs=refs, color_start=0x1000000))
    check("negative_color_range_red", ok_color, detail_color)
    ok_stops, detail_stops = raises(ERR_OUT_OF_RANGE, lambda: add_gradient(
        doc, boundary_refs=refs, stops=1))
    check("negative_stops_range_red", ok_stops, detail_stops)
    ok_empty, detail_empty = raises(ERR_EMPTY_SELECTION, lambda: add_gradient(
        doc, boundary_refs=[]))
    check("negative_empty_refs_red", ok_empty, detail_empty)

    passed = sum(1 for _n, ok, _d in checks if ok)
    failed = [name for name, ok, _d in checks if not ok]
    verdict = "PASS" if passed == len(checks) else "FAIL"
    for name, ok, detail in checks:
        if not ok:
            print("FEATURE_CHECK_FAIL %s %s" % (name, detail))
    print("FEATURE_SELFTEST family=%s op=add_gradient assertions=%d passed=%d failed=%s"
          % (FAMILY, len(checks), passed, failed or "none"))
    print("FEATURE_OK family=%s op=add_gradient verdict=%s" % (FAMILY, verdict))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
