#!/usr/bin/env python3
"""SW-DRAWINGS 片模块 w19：尺寸标注（导入模型尺寸 Insert Model Items + 手工智能尺寸 Smart Dimension）。

族接口（冻结）
--------------
    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    op(sk_or_doc, **kwargs) -> list[str]   # 本次新建对象的 uuid 列表
    长度一律整数纳米；错误一律 cadcore.errors.CadError(reason=...)；本文件不写 __init__.py。

本片负责哪一条 must_have
------------------------
`docs/research/cad-parity/features/sw-drawings.json` 的 21 条 must_have 里，仍 missing/partial 的 9 条
按清单顺序编号 0..8：0 视图调色板/投影视图、1 尺寸标注（导入模型尺寸 + 手工智能尺寸）、2 尺寸公差与配合、
3 形位公差（GD&T）、4 表面粗糙度符号、5 孔标注、6 零件序号（气球）、7 工程图模板（.drwdot）、
8 打印与导出（PDF/DWG/DXF）。本席（w19）取第 10 号 ⇒ 条目不足、顺延取模 10 mod 9 = 1 ⇒ 第 1 号
= **尺寸标注：导入模型尺寸 + 手工智能尺寸**（`status/sw-drawings.json` 记 partial，缺的正是
"导入模型尺寸 Insert Model Items"与"图纸上手工智能尺寸入口"两半）。

两条操作做什么（L1 文档模型上的真动作，真数据后果）
------------------------------------------------
1. `insert_model_dimensions`（Insert Model Items）：从**模型侧**的 L1 尺寸节点
   （`sketches[*].dimensions[*]`，字段口径 = `cadcore/docmodel.py` 的
   `_NODE_FIELDS["dimension"]`：`uuid/kind/refs/value_nm/text_key`）读数，逐条落成图纸尺寸注记。
   `value_nm` **原样取自模型**（不重算、不改写），模型尺寸 uuid 与出处写进 `args`；
   注记 uuid 由 `uuid5(图纸 uuid, 模型尺寸身份)` 现算 ⇒ **同一条模型尺寸重跑得到同一 uuid**（幂等，
   不会越标越多）。`dimensions=`（节点或草图 uuid）与 `source=`（草图 uuid）可显式点名导入范围。
   图上同时画真几何：尺寸线长度 = 模型值 × 该视图自己的比例（比例由视图两端外包框现算，不写死）。
2. `smart_dimension`（Smart Dimension）：在模型空间给两点 `p1_nm`/`p2_nm`，按 `kind`
   （aligned / horizontal / vertical）**当场量出整数纳米值**，再用视图的外包框
   （`bounds_model_nm` ↔ `bounds_sheet_nm()`）把两点映射到图纸空间，画真几何（两条尺寸界线 + 一条
   尺寸线 + 两端箭头，`DIM` 图层），并落一条 `kind="dimension"` 的 L1 注记（`value_nm` = 量测值，
   `args.measured_from` 写明量测出处）。`at_nm` 可显式给注记落点。

边界（显式抛错，或带机读标志的降级；绝不静默近似）
------------------------------------------------
* 模型侧一条尺寸都没有 ⇒ `CadError(reason="model_dimensions_missing")`，**不**返回空列表冒充成功。
* 尺寸节点非法 ⇒ `bad_model_dimension`；`kind` 不认识 ⇒ `unsupported_dimension_kind`；
  `source=`/`dimensions=` 点名的草图 uuid 在文档里找不到 ⇒ `model_item_source_unknown`。
* `smart_dimension`：缺 `p1_nm`/`p2_nm` ⇒ `missing_point`；点非法 ⇒ `bad_point`；两点重合（量出 0）
  ⇒ `zero_dimension`；`kind` 不是 aligned/horizontal/vertical ⇒ `unsupported_dimension_kind`；
  图纸上没有视图 ⇒ `no_view`；点名的视图不存在 ⇒ `view_missing`。
* 视图量不出外包框（两个 bounds 任一缺席/退化）⇒ 尺寸注记照落**真值**，但
  `args.placement="anchor_only"` 且 `Sheet.note()` 留痕，**不**画一段长度靠猜的假几何。
* 本模块**不**注册 `MUST_HAVE_MAP`/`CALL_PLANS`：族门的"条目 → 操作"派发不由本片改写
  （重名首见者赢，抢注会改掉别的操作的探针面），接线留给族门/调用方。
"""

from __future__ import annotations

import math
import pathlib
import re
import sys
import uuid as _uuid
from collections.abc import Mapping as _Mapping
from collections.abc import Sequence as _Seq
from typing import Any, Callable

from cadcore.draw import sheet as _sheet
from cadcore.errors import CadError
from cadcore.ops import sw_drawings as _base


FAMILY = "sw_drawings"
BASE_FAMILY = str(getattr(_base, "FAMILY", FAMILY))
OP_INSERT = "insert_model_dimensions"
OP_SMART = "smart_dimension"
SHEET_RECORD = "dimension"

NM_PER_MM = int(getattr(_base, "NM_PER_MM", 1_000_000))
DEFAULT_STANDARD = str(getattr(_base, "DEFAULT_STANDARD", "iso"))
DEFAULT_LANGUAGE = str(getattr(_base, "DEFAULT_LANGUAGE", "en"))
DEFAULT_SIZE = getattr(_base, "DEFAULT_SIZE", None)
DEFAULT_SCALE = str(getattr(_base, "DEFAULT_SCALE", "1:1"))
DEFAULT_MARGIN_NM = int(getattr(_base, "DEFAULT_MARGIN_NM", 20 * NM_PER_MM))
DEFAULT_GAP_NM = int(getattr(_base, "DEFAULT_GAP_NM", 25 * NM_PER_MM))
DIM_LAYER = str(getattr(_sheet, "DIM_LAYER", "") or getattr(_sheet, "LAYER_DIM", "") or "DIM")
DEFAULT_ARROW_NM = 3 * NM_PER_MM
DEFAULT_EXT_NM = 2 * NM_PER_MM
DEFAULT_OFFSET_NM = 8 * NM_PER_MM
DEFAULT_STACK_NM = 12 * NM_PER_MM
ARROW_SPREAD_DEG = 12.0
DIMENSION_KINDS = ("aligned", "horizontal", "vertical")
MODEL_KIND_MAP = {
    "aligned": "aligned", "linear": "aligned", "distance": "aligned", "length": "aligned",
    "horizontal": "horizontal", "linear_x": "horizontal", "dx": "horizontal", "width": "horizontal",
    "vertical": "vertical", "linear_y": "vertical", "dy": "vertical", "height": "vertical",
    "diameter": "diameter", "radius": "radius", "angular": "angular", "angle": "angular",
}
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NM_MIN = -(1 << 62)


# --------------------------------------------------------------------------------------
# 校验与几何小工具（返回值一律整数纳米；错误一律带机器可读 reason）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII，面向用户的串走语言包）。"""
    return CadError(str(message), reason=str(reason), **details)


def _int_nm(value: Any, *, what: str, reason: str = "bad_nm", minimum: int = 0) -> int:
    """整数纳米校验：`bool`/`float`/字符串一律拒（不替调用方猜单位或四舍五入）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(reason, "value must be an integer number of nanometres", field=what,
                    value_type=type(value).__name__)
    if int(value) < int(minimum):
        raise _fail(reason, "value below the allowed minimum", field=what, value=int(value),
                    minimum=int(minimum))
    return int(value)


def _point_nm(value: Any, *, what: str, reason: str = "bad_point") -> tuple[int, int]:
    """二维点校验：长度恰为 2 的整数纳米序列。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _Seq) or len(value) != 2:
        raise _fail(reason, "point must be a 2-element sequence of integer nanometres", field=what,
                    value_type=type(value).__name__)
    return (_int_nm(value[0], what=what + "[0]", reason=reason, minimum=_NM_MIN),
            _int_nm(value[1], what=what + "[1]", reason=reason, minimum=_NM_MIN))


def _flag(value: Any, *, what: str) -> bool:
    """bool 入参校验：`0/1` 不算，`True/False` 才算。"""
    if not isinstance(value, bool):
        raise _fail("bad_flag", "flag must be a bool", field=what, value_type=type(value).__name__)
    return value


def _dimension_kind(kind: Any) -> str:
    """手工智能尺寸支持的三种量法；其它值显式拒（不悄悄退化成 aligned）。"""
    key = str(kind if kind is not None else "aligned").strip().lower()
    if key not in DIMENSION_KINDS:
        raise _fail("unsupported_dimension_kind",
                    "smart dimension supports aligned|horizontal|vertical",
                    kind=repr(kind), known=list(DIMENSION_KINDS))
    return key


def measure_nm(p1_nm: Any, p2_nm: Any, kind: str = "aligned") -> int:
    """两点间的真值（整数纳米）：aligned = 欧氏距离四舍五入，horizontal = |dx|，vertical = |dy|。"""
    key = _dimension_kind(kind)
    p1 = _point_nm(p1_nm, what="p1_nm")
    p2 = _point_nm(p2_nm, what="p2_nm")
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    if key == "horizontal":
        return abs(dx)
    if key == "vertical":
        return abs(dy)
    return int(round(math.hypot(float(dx), float(dy))))


def _bounds(value: Any) -> tuple[int, int, int, int] | None:
    """4 元整数纳米外包框；形状不对就返回 `None`（让调用方按"量不出"处理，而不是猜）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _Seq) or len(value) != 4:
        return None
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        out.append(int(item))
    return (out[0], out[1], out[2], out[3])


def view_transform(view: Any) -> tuple[float, float, int, int, int, int] | None:
    """视图自己的映射（模型空间 nm → 图纸空间 nm）：两端各用视图现成的外包框现算，不猜比例。"""
    model = _bounds(getattr(view, "bounds_model_nm", None))
    getter = getattr(view, "bounds_sheet_nm", None)
    sheet_box = None
    if callable(getter):
        try:
            sheet_box = _bounds(getter())
        except Exception:  # 量不出就是量不出：显式走 anchor_only 降级，不吞成假比例
            sheet_box = None
    if model is None or sheet_box is None:
        return None
    mw = model[2] - model[0]
    mh = model[3] - model[1]
    if mw <= 0 or mh <= 0:
        return None
    sx = (sheet_box[2] - sheet_box[0]) / float(mw)
    sy = (sheet_box[3] - sheet_box[1]) / float(mh)
    if sx <= 0.0 or sy <= 0.0:
        return None
    return (sx, sy, model[0], model[1], sheet_box[0], sheet_box[1])


def _map_point(tf: Any, point: tuple[int, int]) -> tuple[int, int]:
    """模型点 → 图纸点（真仿射：只由 `view_transform` 现算的比例与原点决定）。"""
    sx, sy, mx, my, bx, by = tf
    return (int(round(bx + (point[0] - mx) * sx)), int(round(by + (point[1] - my) * sy)))


def _line(p1: Any, p2: Any, name: str, source: str) -> Any:
    """图纸上的一条真线（`draw.sheet.Curve.line`；`DIM` 图层）。"""
    maker = getattr(_sheet.Curve, "line", None)
    if not callable(maker):
        raise _fail("curve_api_missing", "cadcore.draw.sheet.Curve.line is unavailable")
    first = _point_nm(p1, what="curve.p1")
    second = _point_nm(p2, what="curve.p2")
    try:
        return maker(first, second, layer=DIM_LAYER, name=name, source=source)
    except TypeError:  # 旧签名（layer 位置传参）→ 显式降级，不静默丢这条线
        return maker(first, second, DIM_LAYER, name)


def _arrow(tip: tuple[int, int], toward: tuple[int, int], length_nm: int, source: str) -> list[Any]:
    """箭头：优先复用基族 `_terminator_arrow` 的同一口径，缺席时本地同式两条翼线。"""
    helper = getattr(_base, "_terminator_arrow", None)
    if callable(helper):
        try:
            return list(helper(tip, toward, int(length_nm), DIM_LAYER))
        except (TypeError, AttributeError):
            pass
    dx = float(toward[0] - tip[0])
    dy = float(toward[1] - tip[1])
    norm = math.hypot(dx, dy)
    if norm <= 0.0:
        return []
    ux, uy = dx / norm, dy / norm
    wings: list[Any] = []
    for sign in (1.0, -1.0):
        angle = math.radians(ARROW_SPREAD_DEG) * sign
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        vx, vy = ux * cos_a - uy * sin_a, ux * sin_a + uy * cos_a
        wing = (int(round(tip[0] + vx * length_nm)), int(round(tip[1] + vy * length_nm)))
        wings.append(_line(tip, wing, "dimension/arrow", source))
    return wings


def dimension_curves(tf: Any, p1_nm: Any, p2_nm: Any, kind: str = "aligned", *, at_nm: Any = None,
                     arrow_nm: Any = DEFAULT_ARROW_NM, offset_nm: Any = DEFAULT_OFFSET_NM,
                     ext_nm: Any = DEFAULT_EXT_NM, source: str = OP_SMART) -> tuple[list[Any], int, str]:
    """手工智能尺寸的真几何：两条尺寸界线 + 一条尺寸线 + 两端箭头。

    返回 `(curves, value_nm, placement)`。`tf=None`（视图量不出外包框）⇒ 返回
    `([], value_nm, "anchor_only")`：**值是真的，几何不画**（不拿猜的比例糊一段线）。
    """
    key = _dimension_kind(kind)
    value = measure_nm(p1_nm, p2_nm, key)
    if value == 0:
        raise _fail("zero_dimension", "the two picked points coincide -- no dimension to place")
    arrow = _int_nm(arrow_nm, what="arrow_nm", minimum=1)
    offset = _int_nm(offset_nm, what="offset_nm", minimum=0)
    ext = _int_nm(ext_nm, what="ext_nm", minimum=1)
    if tf is None:
        return ([], value, "anchor_only")
    p1 = _point_nm(p1_nm, what="p1_nm")
    p2 = _point_nm(p2_nm, what="p2_nm")
    a = _map_point(tf, p1)
    b = _map_point(tf, p2)
    if key == "horizontal":
        y = max(a[1], b[1]) + offset
        line_a, line_b = (a[0], y), (b[0], y)
        ext_a = (a, (a[0], y + ext))
        ext_b = (b, (b[0], y + ext))
    elif key == "vertical":
        x = max(a[0], b[0]) + offset
        line_a, line_b = (x, a[1]), (x, b[1])
        ext_a = (a, (x + ext, a[1]))
        ext_b = (b, (x + ext, b[1]))
    else:  # aligned：沿两点连线的法向偏移，取"朝外"的一侧
        dxv = float(b[0] - a[0])
        dyv = float(b[1] - a[1])
        norm = math.hypot(dxv, dyv)
        if norm <= 0.0:
            raise _fail("zero_dimension", "the two picked points project onto one another")
        nx, ny = -dyv / norm, dxv / norm
        if ny < 0.0 or (ny == 0.0 and nx < 0.0):
            nx, ny = -nx, -ny
        line_a = (int(round(a[0] + nx * offset)), int(round(a[1] + ny * offset)))
        line_b = (int(round(b[0] + nx * offset)), int(round(b[1] + ny * offset)))
        reach = offset + ext
        ext_a = (a, (int(round(a[0] + nx * reach)), int(round(a[1] + ny * reach))))
        ext_b = (b, (int(round(b[0] + nx * reach)), int(round(b[1] + ny * reach))))
    if at_nm is not None:
        target = _point_nm(at_nm, what="at_nm")
        mid = ((line_a[0] + line_b[0]) // 2, (line_a[1] + line_b[1]) // 2)
        ddx = target[0] - mid[0]
        ddy = target[1] - mid[1]
        line_a = (line_a[0] + ddx, line_a[1] + ddy)
        line_b = (line_b[0] + ddx, line_b[1] + ddy)
        ext_a = ((ext_a[0][0] + ddx, ext_a[0][1] + ddy), (ext_a[1][0] + ddx, ext_a[1][1] + ddy))
        ext_b = ((ext_b[0][0] + ddx, ext_b[0][1] + ddy), (ext_b[1][0] + ddx, ext_b[1][1] + ddy))
    curves = [_line(ext_a[0], ext_a[1], "dimension/witness", source),
              _line(ext_b[0], ext_b[1], "dimension/witness", source),
              _line(line_a, line_b, "dimension/line", source)]
    curves.extend(_arrow(line_a, line_b, arrow, source))
    curves.extend(_arrow(line_b, line_a, arrow, source))
    return (curves, value, "view_transform")


def _value_curves(tf: Any, anchor: tuple[int, int], value_nm: int, kind: str, *,
                  arrow_nm: int = DEFAULT_ARROW_NM, source: str = OP_INSERT) -> list[Any]:
    """导入模型尺寸的图上几何：长度 = **模型值 × 该视图比例**（比例由视图外包框现算）。"""
    if tf is None:
        return []
    sx, sy = float(tf[0]), float(tf[1])
    if kind == "vertical":
        length = int(round(value_nm * sy))
        a, b = anchor, (anchor[0], anchor[1] + length)
    elif kind in ("diameter", "radius"):
        raw = value_nm if kind == "diameter" else 2 * value_nm
        length = int(round(raw * ((sx + sy) / 2.0)))
        a, b = anchor, (anchor[0] + length, anchor[1])
    else:
        length = int(round(value_nm * sx))
        a, b = anchor, (anchor[0] + length, anchor[1])
    if length <= 0:
        return []
    curves = [_line(a, b, "dimension/line", source)]
    curves.extend(_arrow(a, b, arrow_nm, source))
    curves.extend(_arrow(b, a, arrow_nm, source))
    return curves


def _attach_curves(sheet_obj: Any, view: Any, curves: list[Any]) -> str | None:
    """把真几何挂到既有列表容器上（沿用运行期既有桶，不另立一套几何真源）。"""
    if not curves:
        return None
    for holder, attr in ((sheet_obj, "curves"), (sheet_obj, "geometry"), (view, "visible"),
                         (view, "curves")):
        bucket = getattr(holder, attr, None)
        if isinstance(bucket, list):
            bucket.extend(curves)
            return "%s.%s" % (type(holder).__name__, attr)
    return None


def _anchor(view: Any, offset_nm: int) -> tuple[int, int]:
    """注记落点（图纸空间）：视图外包框上方居中；量不出就左下留白（与基族同一口径）。"""
    helper = getattr(_base, "_dimension_point", None)
    if callable(helper):
        point = helper(view)
        return (int(point[0]), int(point[1]) + int(offset_nm))
    getter = getattr(view, "bounds_sheet_nm", None)
    box = _bounds(getter()) if callable(getter) else None
    if box is None:
        return (int(DEFAULT_MARGIN_NM), int(DEFAULT_MARGIN_NM) + int(offset_nm))
    return (int((box[0] + box[2]) // 2), int(box[3]) + int(DEFAULT_MARGIN_NM) + int(offset_nm))


def _target_view(sheet_obj: Any, view_uuid: Any, view_name: Any) -> Any:
    """按 uuid 或视图名定位视图；找不到即抛（不退回"随便挑一个"）。"""
    views = list(getattr(sheet_obj, "views", ()) or ())
    if view_uuid is not None:
        wanted_uuid = str(view_uuid)
        for view in views:
            if str(getattr(view, "uuid", "") or "") == wanted_uuid:
                return view
        raise _fail("view_missing", "no view with that uuid on this sheet", view_uuid=wanted_uuid,
                    known=[str(getattr(item, "uuid", "") or "") for item in views])
    wanted = str(view_name if view_name is not None else "front").strip().lower()
    for view in views:
        if str(getattr(view, "name", "") or "") == wanted:
            return view
    raise _fail("view_missing", "no view with that name on this sheet", view=wanted,
                known=[str(getattr(item, "name", "") or "") for item in views])


def _payload_of(sk_or_doc: Any) -> _Mapping:
    """L1 payload：走基族 `_as_payload`；缺席时退到映射本体 / `to_dict()`（不静默给空文档）。"""
    helper = getattr(_base, "_as_payload", None)
    if callable(helper):
        payload = helper(sk_or_doc)
        if isinstance(payload, _Mapping):
            return payload
    if isinstance(sk_or_doc, _Mapping):
        return sk_or_doc
    to_dict = getattr(sk_or_doc, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, _Mapping):
            return payload
    raise _fail("bad_document", "cannot read an L1 document payload from this object",
                value_type=type(sk_or_doc).__name__)


def _annotations_of(payload: _Mapping) -> list[Any] | None:
    """文档顶层 `annotations[]`（裸草图没有 ⇒ `None`，调用方据此显式降级）。"""
    node = payload.get("annotations") if isinstance(payload, _Mapping) else None
    return node if isinstance(node, list) else None


def _record_uuid(tag: str, key: str) -> str:
    """确定性 uuid5：同一条模型尺寸/同一对点重跑得到同一 uuid（幂等，不越标越多）。"""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                           "cadcore.sw_drawings.features.w19.%s|%s" % (tag, key)))


def _register_record(annotations: list[Any] | None, record: dict[str, Any]) -> bool:
    """登记 L1 注记：优先走基族 `_register`（同一套归一化），签名不符则直接追加同形记录。"""
    if annotations is None:
        return False
    helper = getattr(_base, "_register", None)
    if callable(helper):
        try:
            helper(annotations, record)
            return True
        except (TypeError, AttributeError):
            pass
    annotations.append(dict(record))
    return True


# --------------------------------------------------------------------------------------
# 模型尺寸读取（L1 `sketches[*].dimensions[*]`）
# --------------------------------------------------------------------------------------


def _sketch_by_uuid(payload: _Mapping, uuid_text: Any) -> _Mapping | None:
    target = str(uuid_text)
    for sketch in payload.get("sketches") or []:
        if isinstance(sketch, _Mapping) and str(sketch.get("uuid") or "") == target:
            return sketch
    return None


def _normalize_model_dimension(entry: Any, origin: str) -> dict[str, Any]:
    """一条模型尺寸 → 图纸尺寸注记的输入（值原样保留；非法即抛，不改写、不兜底成 0）。"""
    if not isinstance(entry, _Mapping):
        raise _fail("bad_model_dimension", "model dimension must be a mapping", origin=origin,
                    value_type=type(entry).__name__)
    value_nm = _int_nm(entry.get("value_nm"), what="value_nm", reason="bad_model_dimension")
    raw_kind = str(entry.get("kind") or entry.get("dimension_kind") or "aligned").strip().lower()
    kind = MODEL_KIND_MAP.get(raw_kind)
    if kind is None:
        raise _fail("unsupported_dimension_kind", "unknown model dimension kind", kind=raw_kind,
                    origin=origin, known=sorted(set(MODEL_KIND_MAP.values())))
    raw_uuid = entry.get("uuid")
    text = str(raw_uuid) if isinstance(raw_uuid, str) and _UUID_RE.match(raw_uuid) else None
    return {"uuid": text, "kind": kind, "value_nm": value_nm,
            "model_text_key": str(entry.get("text_key") or ""), "origin": origin}


def _dimension_nodes(payload: _Mapping, dimensions: Any, source: Any) -> list[tuple[Any, str]]:
    """要导入的模型尺寸节点清单：`dimensions=` 优先 → `source=` 草图 → 全文档草图（+顶层 dimensions）。"""
    if dimensions is not None:
        if isinstance(dimensions, (str, bytes)) or not isinstance(dimensions, _Seq):
            raise _fail("bad_model_dimension",
                        "dimensions must be a sequence of nodes or sketch uuids",
                        value_type=type(dimensions).__name__)
        candidates = [(entry, "dimensions") for entry in dimensions]
    elif source is not None:
        sketch = _sketch_by_uuid(payload, source)
        if sketch is None:
            raise _fail("model_item_source_unknown", "no sketch with that uuid in this document",
                        source=str(source))
        candidates = [(entry, "sketch:" + str(source)) for entry in (sketch.get("dimensions") or [])]
    else:
        candidates = []
        for sketch in payload.get("sketches") or []:
            if isinstance(sketch, _Mapping):
                candidates.extend((entry, "sketch:" + str(sketch.get("uuid") or ""))
                                  for entry in (sketch.get("dimensions") or []))
        candidates.extend((entry, "document") for entry in (payload.get("dimensions") or []))
    nodes: list[tuple[Any, str]] = []
    for entry, origin in candidates:
        if isinstance(entry, (str, bytes)):  # 显式给的是草图 uuid ⇒ 展开该草图的尺寸
            sketch = _sketch_by_uuid(payload, entry)
            if sketch is None:
                raise _fail("model_item_source_unknown", "no sketch with that uuid in this document",
                            source=str(entry))
            nodes.extend((node, "sketch:" + str(entry)) for node in (sketch.get("dimensions") or []))
        else:
            nodes.append((entry, origin))
    return nodes


def _views_for(sk_or_doc: Any, payload: _Mapping, **kwargs: Any) -> tuple[Any, list[str]]:
    """建/取图纸与视图：一律走基族同一条链路（`_views_for`），口径只有一份。"""
    helper = getattr(_base, "_views_for", None)
    if not callable(helper):
        raise _fail("views_api_missing", "cadcore.ops.sw_drawings._views_for is unavailable")
    sheet_obj, created = helper(sk_or_doc, payload, **kwargs)
    return (sheet_obj, list(created or []))


def _view_kwargs(sheet: Any, kernel: Any, standard: Any, language: Any, size: Any, scale: Any,
                 views: Any, layout: Any, hidden: Any, margin_nm: Any, gap_nm: Any,
                 depth_nm: Any) -> dict[str, Any]:
    """建图参数（与基族 `driven_dimension` 同一组关键字，逐字对齐，不另立口径）。"""
    return {"sheet": sheet, "kernel": kernel,
            "standard": str(standard or DEFAULT_STANDARD),
            "language": str(language or DEFAULT_LANGUAGE),
            "size": size, "scale": str(scale or DEFAULT_SCALE),
            "views": views, "layout": layout, "hidden": _flag(hidden, what="hidden"),
            "margin_nm": _int_nm(margin_nm, what="margin_nm"),
            "gap_nm": _int_nm(gap_nm, what="gap_nm"),
            "depth_nm": _int_nm(depth_nm, what="depth_nm")}


# --------------------------------------------------------------------------------------
# 操作 1/2：导入模型尺寸 / 手工智能尺寸
# --------------------------------------------------------------------------------------


def insert_model_dimensions(sk_or_doc: Any, *, sheet: Any = None, kernel: Any = None,
                            standard: Any = None, language: Any = None, size: Any = None,
                            scale: Any = None, views: Any = ("front", "top", "right"),
                            layout: Any = "auto", hidden: bool = True,
                            margin_nm: int = DEFAULT_MARGIN_NM, gap_nm: int = DEFAULT_GAP_NM,
                            depth_nm: int = 0, dimensions: Any = None, source: Any = None,
                            view: Any = "front", view_uuid: Any = None,
                            offset_nm: int = DEFAULT_OFFSET_NM,
                            stack_nm: int = DEFAULT_STACK_NM,
                            arrow_nm: int = DEFAULT_ARROW_NM) -> list[str]:
    """**导入模型尺寸（Insert Model Items）**：把模型侧尺寸搬到图纸视图上，值原样照搬。

    语义：读文档 `sketches[*].dimensions[*]` 的 L1 尺寸节点（`uuid/kind/refs/value_nm/text_key`），
    逐条落一条 `kind="dimension"` 的注记：`value_nm` = **模型值本身**（本操作不做任何求解/重算），
    `args.model_dimension_uuid` 与 `args.origin` 写明出处，`args.source="insert_model_dimensions"`；
    落点按视图外包框上方居中、逐条按 `stack_nm` 向下错开。注记 uuid 由
    `uuid5(图纸 uuid | 模型尺寸身份)` 现算 ⇒ **同一条模型尺寸重跑得到同一 uuid**（幂等，不重复标注）。
    图上同时画真几何：尺寸线长度 = 模型值 × 视图比例（`view_transform` 现算）。

    参数：`dimensions=` 显式给尺寸节点或草图 uuid 列表；`source=` 给某个草图 uuid（只导该草图）；
    两者都不给 ⇒ 扫全文档草图（+ 顶层 `dimensions`）；`view`/`view_uuid` 指定落在哪个视图上。

    边界：一条模型尺寸都没有 ⇒ `CadError(reason="model_dimensions_missing")`；节点非法 ⇒
    `bad_model_dimension`；`kind` 不认识 ⇒ `unsupported_dimension_kind`；草图 uuid 找不到 ⇒
    `model_item_source_unknown`；图纸没有视图 ⇒ `no_view`；视图量不出外包框 ⇒ 仍落真值注记，
    但 `args.placement="anchor_only"` 且 `Sheet.note()` 留痕（不画假几何）。

    返回：`[图纸 uuid（若本操作新建）] + [视图 uuid（若本操作新建）…] + [尺寸注记 uuid …]`。
    """
    payload = _payload_of(sk_or_doc)
    nodes = _dimension_nodes(payload, dimensions, source)
    if not nodes:
        raise _fail("model_dimensions_missing",
                    "no model dimension to insert (sketches carry no dimensions)",
                    source=str(source) if source is not None else "document")
    items = [_normalize_model_dimension(entry, origin) for entry, origin in nodes]
    stack = _int_nm(stack_nm, what="stack_nm", minimum=1)
    offset = _int_nm(offset_nm, what="offset_nm")
    arrow = _int_nm(arrow_nm, what="arrow_nm", minimum=1)
    sheet_obj, created_ids = _views_for(sk_or_doc, payload,
                                        **_view_kwargs(sheet, kernel, standard, language, size,
                                                       scale, views, layout, hidden, margin_nm,
                                                       gap_nm, depth_nm))
    if not getattr(sheet_obj, "views", None):
        raise _fail("no_view", "inserting model dimensions needs at least one view on the sheet",
                    hint="pass sheet=None to auto-build standard views")
    target = _target_view(sheet_obj, view_uuid, view)
    transform = view_transform(target)
    base_point = _anchor(target, offset)
    annotations = _annotations_of(payload)
    record_ids: list[str] = []
    placed_curves = 0
    for index, item in enumerate(items):
        identity = item["uuid"] or "%s|%d|%d" % (item["origin"], index, item["value_nm"])
        record_uuid = _record_uuid("model_item", "%s|%s" % (str(sheet_obj.uuid), identity))
        anchor = (int(base_point[0]), int(base_point[1]) - index * stack)
        curves = _value_curves(transform, anchor, int(item["value_nm"]), str(item["kind"]),
                               arrow_nm=arrow, source=OP_INSERT)
        bucket = _attach_curves(sheet_obj, target, curves)
        placed_curves += len(curves)
        record = {
            "uuid": record_uuid,
            "target": [str(sheet_obj.uuid), "sheet"],
            "kind": SHEET_RECORD,
            "value_nm": int(item["value_nm"]),
            "text_key": "",
            "literal": True,
            "args": {"source": OP_INSERT, "view": str(getattr(target, "name", "") or ""),
                     "model_dimension_uuid": item["uuid"], "origin": item["origin"],
                     "model_text_key": item["model_text_key"], "kind": item["kind"],
                     "value_nm": int(item["value_nm"]),
                     "measured_from": "model_dimension.value_nm",
                     "placement": "view_transform" if transform is not None else "anchor_only",
                     "at_nm": [int(anchor[0]), int(anchor[1])],
                     "geometry_curves": len(curves), "geometry_bucket": bucket},
        }
        if _register_record(annotations, record):
            record_ids.append(record_uuid)
        if transform is None:
            _note(sheet_obj, "insert_model_dimensions: view %s has no usable bounds -- dimension"
                             " value kept, geometry not drawn (%s)" % (record["args"]["view"],
                                                                       record_uuid))
    _note(sheet_obj, "insert_model_dimensions: model_items=%d curves=%d placement=%s records=%d"
          % (len(items), placed_curves, "view_transform" if transform is not None else "anchor_only",
             len(record_ids)))
    return created_ids + record_ids


def smart_dimension(sk_or_doc: Any, *, sheet: Any = None, kernel: Any = None,
                    standard: Any = None, language: Any = None, size: Any = None, scale: Any = None,
                    views: Any = ("front", "top", "right"), layout: Any = "auto",
                    hidden: bool = True, margin_nm: int = DEFAULT_MARGIN_NM,
                    gap_nm: int = DEFAULT_GAP_NM, depth_nm: int = 0,
                    view: Any = "front", view_uuid: Any = None, p1_nm: Any = None, p2_nm: Any = None,
                    kind: Any = "aligned", at_nm: Any = None,
                    offset_nm: int = DEFAULT_OFFSET_NM, ext_nm: int = DEFAULT_EXT_NM,
                    arrow_nm: int = DEFAULT_ARROW_NM) -> list[str]:
    """**手工智能尺寸（Smart Dimension）**：在模型空间点两个点，图纸上当场量、当场标。

    语义：`p1_nm`/`p2_nm` 是模型空间的整数纳米点，`kind` ∈ aligned/horizontal/vertical；
    值 = 两点间真值（`measure_nm`，`args.measured_from="p1_nm..p2_nm"`，**不是**从模型尺寸抄来的）。
    图上画真几何：视图外包框现算模型→图纸映射，两点映射后画两条尺寸界线 + 一条尺寸线 + 两端箭头
    （`DIM` 图层）；注记 uuid 由 `uuid5(图纸 uuid | 两点 | kind)` 现算，重跑同一点对得到同一 uuid。

    边界：缺 `p1_nm`/`p2_nm` ⇒ `missing_point`；点非法 ⇒ `bad_point`；两点重合 ⇒ `zero_dimension`；
    `kind` 不支持 ⇒ `unsupported_dimension_kind`；无视图 ⇒ `no_view`；量不出外包框 ⇒ 值照落、
    `args.placement="anchor_only"` 并留痕（不画假几何）。

    返回：`[图纸 uuid（若本操作新建）] + [视图 uuid（若本操作新建）…] + [尺寸注记 uuid]`。
    """
    if p1_nm is None or p2_nm is None:
        raise _fail("missing_point", "smart_dimension needs both p1_nm and p2_nm")
    key = _dimension_kind(kind)
    first = _point_nm(p1_nm, what="p1_nm")
    second = _point_nm(p2_nm, what="p2_nm")
    arrow = _int_nm(arrow_nm, what="arrow_nm", minimum=1)
    offset = _int_nm(offset_nm, what="offset_nm")
    ext = _int_nm(ext_nm, what="ext_nm", minimum=1)
    payload = _payload_of(sk_or_doc)
    sheet_obj, created_ids = _views_for(sk_or_doc, payload,
                                        **_view_kwargs(sheet, kernel, standard, language, size,
                                                       scale, views, layout, hidden, margin_nm,
                                                       gap_nm, depth_nm))
    if not getattr(sheet_obj, "views", None):
        raise _fail("no_view", "smart dimensioning needs at least one view on the sheet",
                    hint="pass sheet=None to auto-build standard views")
    target = _target_view(sheet_obj, view_uuid, view)
    transform = view_transform(target)
    anchor_point = _point_nm(at_nm, what="at_nm") if at_nm is not None else None
    curves, value_nm, placement = dimension_curves(transform, first, second, key, at_nm=anchor_point,
                                                   arrow_nm=arrow, offset_nm=offset, ext_nm=ext,
                                                   source=OP_SMART)
    bucket = _attach_curves(sheet_obj, target, curves)
    identity = "%d,%d|%d,%d|%s" % (first[0], first[1], second[0], second[1], key)
    record_uuid = _record_uuid("smart", "%s|%s" % (str(sheet_obj.uuid), identity))
    annotations = _annotations_of(payload)
    record = {
        "uuid": record_uuid,
        "target": [str(sheet_obj.uuid), "sheet"],
        "kind": SHEET_RECORD,
        "value_nm": int(value_nm),
        "text_key": "",
        "literal": True,
        "args": {"source": OP_SMART, "view": str(getattr(target, "name", "") or ""), "kind": key,
                 "p1_nm": [first[0], first[1]], "p2_nm": [second[0], second[1]],
                 "value_nm": int(value_nm), "measured_from": "p1_nm..p2_nm",
                 "placement": placement, "geometry_curves": len(curves), "geometry_bucket": bucket},
    }
    registered = _register_record(annotations, record)
    if placement != "view_transform":
        _note(sheet_obj, "smart_dimension: view %s has no usable bounds -- value kept, geometry not"
                         " drawn (%s)" % (record["args"]["view"], record_uuid))
    _note(sheet_obj, "smart_dimension: kind=%s value_nm=%d curves=%d placement=%s record=%s"
          % (key, value_nm, len(curves), placement, record_uuid))
    return created_ids + ([record_uuid] if registered else [])


def _note(sheet_obj: Any, text: str) -> None:
    """`Sheet.note()` 留痕（有就写；没有就退到 stderr，绝不静默丢诊断）。"""
    writer = getattr(sheet_obj, "note", None)
    if callable(writer):
        writer(str(text))
    elif str(text):
        sys.stderr.write("sw_drawings__w19: %s\n" % text)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    OP_INSERT: insert_model_dimensions,
    OP_SMART: smart_dimension,
}


def module_facts() -> dict[str, Any]:
    """族模块机读事实（三态可辨：`0` 真为零 / `-1` 未采 / `"NA"` 不适用）。"""
    return {
        "family": FAMILY,
        "base_family": BASE_FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "dimension_layer": DIM_LAYER,
        "dimension_kinds": list(DIMENSION_KINDS),
        "model_kind_map": dict(MODEL_KIND_MAP),
        "base_operations": sorted(getattr(_base, "OPERATIONS", {}) or {}),
        "must_have_map_registered": False,
        "reasons": ["model_dimensions_missing", "bad_model_dimension", "unsupported_dimension_kind",
                    "model_item_source_unknown", "missing_point", "bad_point", "zero_dimension",
                    "no_view", "view_missing", "bad_document", "curve_api_missing",
                    "views_api_missing"],
    }


# --------------------------------------------------------------------------------------
# 自检：硬断言（真值量测 / 真 L1 文档读尺寸 / 坏数据必须 loud / 端到端落注记与几何）
# --------------------------------------------------------------------------------------


class _SelftestView:
    """自测替身：只提供真视图被本模块读到的两个接口（模型/图纸两端外包框）与 `visible` 容器。"""

    def __init__(self, name: str, model_box: Any, sheet_box: Any) -> None:
        self.name = str(name)
        self.uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                                    "cadcore.sw_drawings.features.w19.view|" + str(name)))
        self.bounds_model_nm = model_box
        self.bounds_sheet_nm = lambda: sheet_box  # noqa: E731 - 与真视图同名同返回形状
        self.visible: list[Any] = []
        self.hidden: list[Any] = []


class _SelftestSheet:
    """自测替身容器：真 `Sheet` 造不出来时兜底（只实现本模块读到的 uuid/views/note/curves）。"""

    def __init__(self, name: str = "selftest") -> None:
        self.uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                                    "cadcore.sw_drawings.features.w19.sheet|" + str(name)))
        self.views: list[Any] = []
        self.curves: list[Any] = []
        self.notes: list[str] = []
        self.width_nm = 420 * NM_PER_MM
        self.height_nm = 297 * NM_PER_MM

    def note(self, text: str) -> None:
        self.notes.append(str(text))


def _selftest() -> int:
    checks = 0
    notes: list[str] = []

    # ① 真几何量测（硬断言）：整数纳米，三种量法各一条
    assert measure_nm((0, 0), (3_000_000, 4_000_000), "aligned") == 5_000_000, "aligned measure"
    assert measure_nm((0, 0), (7_000_000, 1_000_000), "horizontal") == 7_000_000, "horizontal"
    assert measure_nm((0, 0), (1_000_000, -2_500_000), "vertical") == 2_500_000, "vertical"
    checks += 3

    # ② 真 L1 文档：从 sketches[*].dimensions[*] 读模型尺寸（值原样保留、kind 归一）
    sketch_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "w19.selftest.sketch"))
    width_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "w19.selftest.dim.width"))
    height_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "w19.selftest.dim.height"))
    payload: dict[str, Any] = {
        "meta": {"doc_uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "w19.selftest.doc")),
                 "name": "w19-selftest"},
        "sketches": [{"uuid": sketch_uuid, "plane": "XY", "entities": [], "constraints": [],
                      "dimensions": [{"uuid": width_uuid, "kind": "linear_x",
                                      "refs": [["e1", "start"], ["e1", "end"]],
                                      "value_nm": 12_000_000, "text_key": "dim.width"},
                                     {"uuid": height_uuid, "kind": "linear_y",
                                      "refs": [["e2", "start"], ["e2", "end"]],
                                      "value_nm": 8_000_000, "text_key": "dim.height"}]}],
        "features": [], "annotations": [], "sheets": [],
    }
    nodes = _dimension_nodes(payload, None, None)
    assert len(nodes) == 2, "document must expose both sketch dimensions"
    items = [_normalize_model_dimension(entry, origin) for entry, origin in nodes]
    assert [item["value_nm"] for item in items] == [12_000_000, 8_000_000], "model values verbatim"
    assert [item["kind"] for item in items] == ["horizontal", "vertical"], "model kinds normalised"
    assert items[0]["uuid"] == width_uuid, "model dimension uuid kept for provenance"
    checks += 4

    # ③ 坏数据必须 loud（不许静默改成 0 或跳过）
    try:
        _normalize_model_dimension({"kind": "aligned", "value_nm": "12"}, "selftest")
        raise AssertionError("non-integer value_nm must be rejected")
    except CadError as exc:
        assert exc.reason == "bad_model_dimension", exc.reason
    try:
        _dimension_nodes(payload, None, "not-a-sketch-uuid")
        raise AssertionError("unknown sketch uuid must be rejected")
    except CadError as exc:
        assert exc.reason == "model_item_source_unknown", exc.reason
    checks += 2

    # ④ 端到端：真 Sheet（造得出就用真的）+ 真 L1 文档 + 真几何曲线
    sheet_source = "real"
    try:
        sheet_obj = _sheet.make_sheet(payload, standard=DEFAULT_STANDARD, language=DEFAULT_LANGUAGE,
                                      size=str(DEFAULT_SIZE or "A3"), scale=DEFAULT_SCALE)
        assert isinstance(getattr(sheet_obj, "views", None), list), "real sheet must expose views list"
    except Exception as exc:  # 容器造不出 ⇒ 显式换成同接口替身，并把这件事打出来（不假绿）
        sheet_source = "fixture:%s" % type(exc).__name__
        sheet_obj = _SelftestSheet()
        notes.append("sheet=fixture reason=%s: %s" % (type(exc).__name__, exc))
    view = _SelftestView("front", (0, 0, 100_000_000, 60_000_000),
                         (100_000_000, 200_000_000, 300_000_000, 320_000_000))
    sheet_obj.views.append(view)

    smart_ids = smart_dimension(payload, sheet=sheet_obj, view="front", p1_nm=(0, 0),
                                p2_nm=(3_000_000, 4_000_000))
    assert len(smart_ids) == 1, "smart_dimension must return exactly one record uuid"
    smart = [a for a in payload["annotations"] if a.get("uuid") == smart_ids[0]][0]
    assert smart["kind"] == "dimension" and smart["value_nm"] == 5_000_000, "measured value on sheet"
    assert smart["args"]["measured_from"] == "p1_nm..p2_nm", "measurement provenance recorded"
    assert smart["args"]["placement"] == "view_transform", "real transform path taken"
    assert smart["args"]["geometry_curves"] == 7, "2 witness + 1 line + 2x2 arrow wings"
    assert smart["args"]["geometry_bucket"] is not None, "curves must land in a real bucket"
    checks += 6

    insert_ids = insert_model_dimensions(payload, sheet=sheet_obj, view="front")
    assert len(insert_ids) == 2, "both model dimensions must be inserted"
    again = insert_model_dimensions(payload, sheet=sheet_obj, view="front")
    assert again == insert_ids, "re-inserting the same model dimensions is idempotent"
    inserted = [a for a in payload["annotations"]
                if a.get("kind") == "dimension" and a["args"].get("source") == OP_INSERT]
    assert sorted(a["value_nm"] for a in inserted) == [8_000_000, 12_000_000], "model values verbatim"
    assert {a["args"]["model_dimension_uuid"] for a in inserted} == {width_uuid, height_uuid}, \
        "provenance points back at the model dimensions"
    assert all(a["args"]["geometry_curves"] == 5 for a in inserted), "1 line + 2x2 arrow wings each"
    checks += 5

    print("SELFTEST sw_drawings__w19 checks=%d sheet_source=%s records=%d model_items=%d ops=%s"
          % (checks, sheet_source, len(payload["annotations"]), len(items), sorted(OPERATIONS)))
    for note in notes:
        print("WARN sw_drawings__w19 %s" % note)
    return checks


if __name__ == "__main__":
    try:
        _checks = _selftest()
    except Exception as _exc:  # 自测失败必须红着出去（不吞成 PASS）
        import traceback
        traceback.print_exc()
        print("FEATURE_FAIL family=sw_drawings op=%s verdict=FAIL reason=%s"
              % (OP_INSERT, type(_exc).__name__))
        raise SystemExit(1)
    print("FEATURE_OK family=sw_drawings op=%s verdict=PASS" % OP_INSERT)
    raise SystemExit(0)
