#!/usr/bin/env python3
"""SW-DRAWINGS 族实现片 w10：**导入模型尺寸（Insert Model Items）+ 手工智能尺寸（Smart Dimension）**。

对应 must_have 第 6 条「尺寸标注：导入模型尺寸 + 手工智能尺寸」（status `sw-drawings.json` 记 partial：
旧实现只有 `auto_dimensions` 的**整批自动标注**，既没有"把模型侧尺寸搬进图纸"的入口，也没有"图纸上手工选点现量现标"的入口）。

冻结接口（与全族一致，本片不改口径）：

    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]      # 本次**新建对象**的 uuid，顺序 = 图纸(若新建) → 标注
    长度一律**整数纳米**；错误抛 `cadcore.errors.CadError(reason=...)`；"没做"落 `Sheet.note()`

本片两条操作的真语义（都是**真几何 / 真数据后果**，不是桩）：

1. `import_model_dimensions`（导入模型尺寸）
   从 L1 文档**现读**模型侧尺寸来源：`sketches[].dimensions[]`（schema：`{uuid, kind, refs, value_nm, text_key}`）
   与（可选）`features[].params.distance_nm`。逐条落到图纸上，并且**每条都拿图纸几何现量一遍做交叉核对**：
   `measured_nm` 由目标视图的 `bounds_model_nm` / `visible` 圆现算，`delta_nm = measured_nm - value_nm`
   一并写进标注 `args`。量不到就写 `-1`（未采）/`"NA"`（不适用），**绝不用 0 冒充"量到 0"**（AGENTS.md E2.3）。
   `matched` 字段只在真量到且 `delta_nm == 0` 时为 `True`，否则 `False`/`"NA"` —— 不粉饰。
2. `smart_dimension`（手工智能尺寸）
   调用方给图纸空间选中的两点（模型坐标，整数纳米），本片**现算**真值：`aligned` 用欧氏距离、
   `linear_x`/`linear_y` 取单轴分量、`diameter`/`radius` 从目标视图的可见圆现量半径。
   同时生成**真图元**：两条尺寸界线 + 尺寸线 + 两个箭头（`draw.sheet.Curve.line`，DIMENSIONS 图层），
   端点由视图 `to_sheet_nm` 真投影得到；没有视图就不假装投影过（`projected=False` 落 `args`）。

L1 边界（如实声明，不冒充原生能力）：

* 图纸仍是 L1 JSON（`annotations[]` 承载标注），**不是** `.slddrw` 原生格式；尺寸文字走 `text_key`
  （本片只写 key，不在代码里写死面向用户的文案 —— 中文只出现在 docstring）。
* 视图必须来自 `cadcore.draw.sheet` 的既有视图；`sheet=None` 时本片按公共入口 `draw.sheet.make_sheet`
  现建一张图，并在 `auto_views=True` 时**探测**族内 `standard_views` 的签名后再补三视图（探测失败/能力不足
  只落 `Sheet.note(...)`，不静默、不猜）。
* 圆（`diameter`/`radius`）只在视图曲线真暴露 `radius_nm`(+可选 `center_nm`) 时出图元，否则
  `geometry_note="circle_geometry_unavailable"` 且图元为空 —— 不拿包围盒宽度冒充直径。

自检（文件自带，不依赖 pytest）：

    python3 cadcore/ops/features/sw_drawings__w10.py

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径：非 docstring 的含汉字字符串常量计违规）。
"""

from __future__ import annotations

import inspect
import math
import os
import sys
import uuid as _uuid
from typing import Any, Callable, Mapping, Sequence

from cadcore.errors import CadError
from cadcore.draw import sheet as _sheet
from .. import sw_drawings as _family

__all__ = ["FAMILY", "OPERATIONS", "import_model_dimensions", "smart_dimension", "selftest"]

#: 族 id（冻结接口）。
FAMILY = "sw_drawings"

NM_PER_MM = 1_000_000

#: 缺省标准档 / 语言 / 幅面 / 比例全部取自 `draw.sheet` 的唯一真源，本片不另立一套、不写 locale 字面量。
DEFAULT_STANDARD = "gbt"
DEFAULT_LANGUAGE = _sheet.DEFAULT_LANGUAGE_FALLBACK
DEFAULT_SIZE = _sheet.DEFAULT_SIZE
DEFAULT_SCALE = _sheet.DEFAULT_SCALE

#: 尺寸线相对视图的缺省偏移（图面常数，不是标准档取值）。
DEFAULT_OFFSET_NM = 8 * NM_PER_MM

#: 箭头长度下限/上限（图面常数）。
_MIN_ARROW_NM = 2 * NM_PER_MM
_MAX_ARROW_NM = 5 * NM_PER_MM

#: 箭头两翼张角（度）。
_ARROW_SPREAD_DEG = 12.0

#: 草图基准面 → 标准视图名（SW 的一/三视角习惯里，XY 面的法向视图记 top）。
PLANE_VIEWS: dict[str, str] = {"XY": "top", "XZ": "front", "YZ": "right"}

#: 本片能出图元的尺寸类别（其余类别只搬数值，不出假图元）。
DIMENSIONED_KINDS: tuple[str, ...] = ("linear_x", "linear_y", "diameter", "radius")

#: 标注 uuid 的命名空间（固定字面量 ⇒ 同一份输入得到同一批 uuid）。
_NS = _uuid.uuid5(_uuid.NAMESPACE_DNS, "cadcore.ops.features.sw_drawings__w10")
_UUID_RE = _uuid.UUID


# --------------------------------------------------------------------------------------
# 入参校验 / 模型适配（唯一解释入口）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """带机器可读 `reason` 的 `CadError`；文案一律 ASCII（面向用户的串走语言包 key）。"""
    return CadError(str(message), reason=str(reason), **details)


def _nm(value: Any, what: str) -> int:
    """长度一律整数纳米；`bool`/`float`/字符串一律拒绝（不做隐式取整）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length", "length must be an int nanometre value",
                    param=str(what), value_type=type(value).__name__)
    return int(value)


def _point(value: Any, what: str) -> tuple[int, ...]:
    """点：长度 ≥2 的整数纳米序列（2D/3D 都收，3D 只影响真值计算，投影取前两轴）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) < 2:
        raise _fail("bad_point", "point must be a sequence of >=2 int nanometre coordinates",
                    param=str(what), value_type=type(value).__name__)
    return tuple(_nm(component, "%s[%d]" % (what, index)) for index, component in enumerate(value))


def _payload_of(model: Any) -> Mapping[str, Any]:
    """L1 文档映射直接用；`Document` 交给族模块的既有转换入口（不在这里另写一份解释器）。"""
    if isinstance(model, Mapping):
        return model
    helper = getattr(_family, "_as_payload", None)
    if not callable(helper):
        raise _fail("family_helper_missing", "cadcore.ops.sw_drawings._as_payload is unavailable")
    payload = helper(model)
    if not isinstance(payload, Mapping):
        raise _fail("unsupported_model", "model payload must be a mapping",
                    value_type=type(payload).__name__)
    return payload


def _annotations_of(model: Any) -> list[Any]:
    """可写的 `annotations[]`（L1 真源那一份）；裸草图不行 —— 尺寸标注需要文档承载。"""
    helper = getattr(_family, "_live_annotations", None)
    if not callable(helper):
        raise _fail("family_helper_missing", "cadcore.ops.sw_drawings._live_annotations is unavailable")
    try:
        live = helper(model)
    except CadError as exc:
        raise _fail("dimension_needs_document",
                    "a drawing dimension needs a document with annotations[]; a bare sketch cannot carry it",
                    cause=str(exc.reason))
    if not isinstance(live, list):
        raise _fail("bad_annotations", "document annotations must be a list",
                    value_type=type(live).__name__)
    return live


def _make_sheet(model: Any, *, standard: str, language: str, size: Any, scale: Any) -> Any:
    """按 `draw.sheet` 的公共入口现建图纸；`size`/`scale` 只在调用方真的给了时才透传（不吃 None 语义）。"""
    kwargs: dict[str, Any] = {}
    if size is not None:
        kwargs["size"] = size
    if scale is not None:
        kwargs["scale"] = scale
    return _sheet.make_sheet(model, standard=standard, language=language, **kwargs)


def _views_of(sheet_obj: Any) -> tuple[Any, ...]:
    views = getattr(sheet_obj, "views", None)
    return tuple(views) if isinstance(views, (list, tuple)) else ()


def _pick_view(sheet_obj: Any, *, name: Any = None, view_uuid: Any = None) -> Any:
    """按 uuid 优先、其次名字、最后首视图取目标视图；取不到返回 `None`（调用方据实记未投影）。"""
    views = _views_of(sheet_obj)
    if view_uuid is not None:
        for view in views:
            if str(getattr(view, "uuid", "")) == str(view_uuid):
                return view
        return None
    if name is not None:
        for view in views:
            if str(getattr(view, "name", "")) == str(name):
                return view
        return None
    return views[0] if views else None


def _ensure_views(model: Any, sheet_obj: Any, *, depth_nm: int, auto_views: bool) -> str | None:
    """`auto_views=True` 时**先探测签名**再补三视图；补不上返回一句机读 note 文案（ASCII），不算静默降级。"""
    if sheet_obj is None or not auto_views or _views_of(sheet_obj):
        return None
    builder = getattr(_family, "standard_views", None)
    if not callable(builder):
        return "standard_views_unavailable"
    try:
        params = inspect.signature(builder).parameters
    except (TypeError, ValueError):  # pragma: no cover - 内建/装饰器包过才会走到
        return "standard_views_signature_unreadable"
    if "sheet" not in params:
        return "standard_views_has_no_sheet_param"
    kwargs: dict[str, Any] = {"sheet": sheet_obj}
    if "depth_nm" in params:
        kwargs["depth_nm"] = _nm(depth_nm, "depth_nm")
    try:
        builder(model, **kwargs)
    except CadError as exc:
        return "auto_views_failed:" + str(exc.reason)
    return None


def _to_sheet(view: Any, point: tuple[int, ...]) -> tuple[int, int] | None:
    """模型空间点 → 图纸空间点（真投影）；视图没有该能力就返回 `None`，由调用方记 `projected=False`。"""
    fn = getattr(view, "to_sheet_nm", None)
    if not callable(fn):
        return None
    try:
        projected = tuple(int(value) for value in fn(point[:2]))
    except (TypeError, ValueError, CadError):
        return None
    return (projected[0], projected[1]) if len(projected) >= 2 else None


def _measure_view(view: Any, kind: str) -> tuple[int, str]:
    """只读测量：返回 `(实测纳米 | UNMEASURED | NA, 出处)`。量不到就是量不到，不返回 0 顶替。"""
    if view is None:
        return (_sheet.UNMEASURED, "no_view")
    box = getattr(view, "bounds_model_nm", None)
    if kind in ("linear_x", "linear_y"):
        if box and len(box) >= 4:
            span = (int(box[2]) - int(box[0])) if kind == "linear_x" else (int(box[3]) - int(box[1]))
            return (span, "view.bounds_model_nm")
        return (_sheet.UNMEASURED, "unmeasured")
    if kind in ("diameter", "radius"):
        for curve in getattr(view, "visible", ()) or ():
            if str(getattr(curve, "kind", "") or "") == "circle" and getattr(curve, "radius_nm", None):
                radius = int(curve.radius_nm)
                return (2 * radius if kind == "diameter" else radius, "view.visible.circle")
        return (_sheet.UNMEASURED, "unmeasured")
    return (_sheet.NA, "not_applicable")


def _arrow_wings(tip: tuple[int, int], toward: tuple[int, int], length_nm: int,
                 layer: str, source: str) -> list[Any]:
    """箭头两翼：从尖端沿尺寸线方向 ±`_ARROW_SPREAD_DEG` 的两条真线段（不是文本符号）。"""
    dx, dy = float(toward[0] - tip[0]), float(toward[1] - tip[1])
    norm = math.hypot(dx, dy)
    if norm <= 0.0:
        return []
    ux, uy = dx / norm, dy / norm
    wings: list[Any] = []
    for sign in (1.0, -1.0):
        angle = math.radians(_ARROW_SPREAD_DEG) * sign
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        vx, vy = ux * cos_a - uy * sin_a, ux * sin_a + uy * cos_a
        wing = (int(round(tip[0] + vx * length_nm)), int(round(tip[1] + vy * length_nm)))
        wings.append(_sheet.Curve.line(tip, wing, layer=layer, name="arrow", source=source))
    return wings


def _dimension_geometry(point_a: tuple[int, int], point_b: tuple[int, int], *,
                        offset_nm: int, layer: str, source: str,
                        arrow_nm: int | None = None) -> tuple[tuple[int, int], tuple[int, int], list[Any]]:
    """由两点生成一套尺寸图元：两条尺寸界线 + 尺寸线 + 两个箭头；返回尺寸线两端点与图元。"""
    if point_a == point_b:
        raise _fail("zero_distance", "dimension endpoints coincide -- nothing to dimension")
    span = max(abs(int(point_a[0]) - int(point_b[0])), abs(int(point_a[1]) - int(point_b[1])))
    length = arrow_nm if arrow_nm is not None else min(max(span // 6, _MIN_ARROW_NM), _MAX_ARROW_NM)
    delta = int(offset_nm)
    anchored_a = (int(point_a[0]), int(point_a[1]) + delta)
    anchored_b = (int(point_b[0]), int(point_b[1]) + delta)
    curves = [
        _sheet.Curve.line(point_a, anchored_a, layer=layer, name="extension", source=source),
        _sheet.Curve.line(point_b, anchored_b, layer=layer, name="extension", source=source),
        _sheet.Curve.line(anchored_a, anchored_b, layer=layer, name="dimension_line", source=source),
    ]
    curves.extend(_arrow_wings(anchored_a, anchored_b, length, layer, source))
    curves.extend(_arrow_wings(anchored_b, anchored_a, length, layer, source))
    return anchored_a, anchored_b, curves


def _circle_endpoints(view: Any, kind: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """圆类尺寸的模型空间端点：只在曲线真给半径（且给圆心）时出，否则 `None`（不拿包围盒冒充直径）。"""
    for curve in getattr(view, "visible", ()) or ():
        if str(getattr(curve, "kind", "") or "") != "circle":
            continue
        radius = getattr(curve, "radius_nm", None)
        center = getattr(curve, "center_nm", None) or getattr(curve, "center", None)
        if not radius or not isinstance(center, Sequence) or len(center) < 2:
            continue
        cx, cy = int(center[0]), int(center[1])
        if kind == "diameter":
            return ((cx - int(radius), cy), (cx + int(radius), cy))
        return ((cx, cy), (cx + int(radius), cy))
    return None


def _new_uuid(annotations: Sequence[Any], kind: str, stable: str) -> str:
    """确定性 uuid（同一份输入同一批 uuid），并用列表长度错开同内容重复调用。"""
    return str(_uuid.uuid5(_NS, "%s|%d|%s" % (kind, len(annotations), stable)))


def _append_dimension(annotations: list[Any], *, kind: str, value_nm: int, args: dict[str, Any],
                      text_key: str, layer: str) -> str:
    """落一条 L1 尺寸标注（真源 = `annotations[]`）；返回其 uuid。"""
    record: dict[str, Any] = {
        "uuid": _new_uuid(annotations, kind, str(args.get("stable_key") or text_key)),
        "annotation_kind": kind,
        "kind": "dimension",
        "value_nm": int(value_nm),
        "text_key": str(text_key),
        "layer": str(layer),
        "args": args,
    }
    args.setdefault("text_key", str(text_key))
    annotations.append(record)
    return str(record["uuid"])


# --------------------------------------------------------------------------------------
# 操作 1：导入模型尺寸（Insert Model Items）
# --------------------------------------------------------------------------------------


def _model_dimension_sources(payload: Mapping[str, Any], *, include_feature_dims: bool,
                             kinds: Sequence[str] | None) -> list[dict[str, Any]]:
    """现读模型侧尺寸来源：草图尺寸（真源）+ 可选特征参数（`features[].params.distance_nm`）。"""
    sources: list[dict[str, Any]] = []
    sketches = payload.get("sketches")
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)):
        for sketch in sketches:
            if not isinstance(sketch, Mapping):
                continue
            plane = str(sketch.get("plane") or "")
            sketch_uuid = str(sketch.get("uuid") or "")
            dimensions = sketch.get("dimensions")
            if not isinstance(dimensions, Sequence) or isinstance(dimensions, (str, bytes)):
                continue
            for dimension in dimensions:
                if not isinstance(dimension, Mapping):
                    continue
                kind = str(dimension.get("kind") or "")
                value = dimension.get("value_nm")
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                sources.append({
                    "kind": kind, "value_nm": int(value), "plane": plane, "sketch_uuid": sketch_uuid,
                    "model_ref": str(dimension.get("uuid") or ""),
                    "text_key": str(dimension.get("text_key") or ("dim.model." + (kind or "unknown"))),
                    "source": "sketch.dimension",
                })
    if include_feature_dims:
        features = payload.get("features")
        if isinstance(features, Sequence) and not isinstance(features, (str, bytes)):
            for feature in features:
                if not isinstance(feature, Mapping) or feature.get("suppressed"):
                    continue
                params = feature.get("params")
                distance = params.get("distance_nm") if isinstance(params, Mapping) else None
                if isinstance(distance, bool) or not isinstance(distance, int) or int(distance) <= 0:
                    continue
                sources.append({
                    "kind": "linear_z", "value_nm": int(distance), "plane": "",
                    "sketch_uuid": "", "model_ref": str(feature.get("uuid") or ""),
                    "text_key": "dim.model.feature_depth", "source": "feature.params.distance_nm",
                })
    if kinds is not None:
        allowed = {str(item) for item in kinds}
        sources = [item for item in sources if item["kind"] in allowed]
    if not sources:
        raise _fail("no_model_dimension", "the model exposes no dimension to import",
                    hint="sketches[].dimensions[] with int value_nm, or features[].params.distance_nm")
    return sources


def import_model_dimensions(sk_or_doc: Any, *, sheet: Any = None, standard: str = DEFAULT_STANDARD,
                            language: str = DEFAULT_LANGUAGE, size: Any = None, scale: Any = None,
                            view: Any = None, view_uuid: Any = None,
                            plane_views: Mapping[str, str] | None = None, kinds: Any = None,
                            include_feature_dims: bool = True, auto_views: bool = True,
                            offset_nm: int = DEFAULT_OFFSET_NM, depth_nm: int = 0) -> list[str]:
    """**导入模型尺寸**：把模型侧尺寸搬到图纸上，并逐条用图纸几何现量做交叉核对。

    数据后果（都在 L1 机读面上，可现读复核）：文档 `annotations[]` 各追加一条
    `annotation_kind="model_item_dimension"` 的记录，带 `value_nm`（搬运的模型真值）、
    `measured_nm`（图纸几何现量）、`delta_nm`、`matched`、`source_model_ref` 与真图元
    （`geometry`，`draw.sheet.Curve` 的 `as_dict()`：两条尺寸界线 + 尺寸线 + 两个箭头）。

    行为边界（如实记录，不猜不粉饰）：

    * `measured_nm`：`linear_x/linear_y` 取目标视图 `bounds_model_nm` 的宽/高；`diameter/radius` 取
      视图可见圆半径；其余类别记 `"NA"`（不适用）；没有视图记 `-1`（未采）。
    * `matched` 只在真量到且 `delta_nm == 0` 时为 `True`；未采/不适用分别是 `False` / `"NA"`。
    * 目标视图选择：`view_uuid` > `view`（视图名）> 草图基准面映射（`plane_views`，缺省
      `{"XY": "top", "XZ": "front", "YZ": "right"}`）> 首视图；一张视图都没有则 `projected=False`
      且尺寸按模型真值出图元（尺寸线长度 = `value_nm`），**不假装投影过**。
    * `sheet=None` 时按 `draw.sheet.make_sheet` 现建一张图（其 uuid 排返回值首位）；建不出来只记
      `placement="unplaced"` + `Sheet.note(...)`，不抛错也不静默。

    参数：`kinds` = 只搬这些类别（序列，缺省全收）；`include_feature_dims` = 是否连
    `features[].params.distance_nm` 一起搬（缺省 True）；`offset_nm` = 尺寸线偏移（整数纳米，图面常数）。

    返回：`[图纸 uuid（若本操作新建）] + [每条尺寸标注的 uuid …]`，按模型清单顺序。
    """
    payload = _payload_of(sk_or_doc)
    annotations = _annotations_of(sk_or_doc)
    offset = _nm(offset_nm, "offset_nm")
    depth = _nm(depth_nm, "depth_nm")
    sources = _model_dimension_sources(payload, include_feature_dims=bool(include_feature_dims),
                                       kinds=kinds if kinds is None else tuple(kinds))

    created: list[str] = []
    sheet_obj = sheet
    notes: list[str] = []
    if sheet_obj is None:
        try:
            sheet_obj = _make_sheet(sk_or_doc, standard=standard, language=language, size=size, scale=scale)
        except CadError as exc:
            sheet_obj = None
            notes.append("sheet_unavailable:" + str(exc.reason))
        else:
            created.append(str(getattr(sheet_obj, "uuid", "")))
    elif not isinstance(sheet_obj, _sheet.Sheet):
        raise _fail("bad_sheet", "sheet must be a cadcore.draw.sheet.Sheet",
                    value_type=type(sheet_obj).__name__)

    if isinstance(sheet_obj, _sheet.Sheet):
        auto_note = _ensure_views(sk_or_doc, sheet_obj, depth_nm=depth, auto_views=bool(auto_views))
        if auto_note:
            notes.append(auto_note)

    plane_map = dict(PLANE_VIEWS)
    if plane_views is not None:
        if not isinstance(plane_views, Mapping):
            raise _fail("bad_plane_views", "plane_views must be a mapping of plane -> view name",
                        value_type=type(plane_views).__name__)
        plane_map.update({str(key): str(value) for key, value in plane_views.items()})

    for source in sources:
        target = _pick_view(sheet_obj, name=view, view_uuid=view_uuid)
        if isinstance(sheet_obj, _sheet.Sheet) and (view is None and view_uuid is None):
            target = _pick_view(sheet_obj, name=plane_map.get(source["plane"], "")) or _pick_view(sheet_obj)
        measured, measured_from = _measure_view(target, str(source["kind"]))
        delta: Any = _sheet.UNMEASURED if measured in (_sheet.UNMEASURED, _sheet.NA) \
            else int(measured) - int(source["value_nm"])
        matched: Any = "NA" if delta == _sheet.UNMEASURED else (delta == 0)

        kind = str(source["kind"])
        geometry: list[Any] = []
        anchor_a = anchor_b = None
        projected = False
        geometry_note = ""
        if kind in DIMENSIONED_KINDS:
            if kind in ("diameter", "radius"):
                endpoints = _circle_endpoints(target, kind) if target is not None else None
                if endpoints is None:
                    geometry_note = "circle_geometry_unavailable"
            else:
                box = getattr(target, "bounds_model_nm", None) if target is not None else None
                if box and len(box) >= 4:
                    x0, y0, x1, y1 = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
                    mid_x, mid_y = (x0 + x1) // 2, (y0 + y1) // 2
                    endpoints = ((x0, mid_y), (x1, mid_y)) if kind == "linear_x" \
                        else ((mid_x, y0), (mid_x, y1))
                else:  # 无视图：按模型真值出等长图元（不假装是投影结果）
                    endpoints = ((0, 0), (int(source["value_nm"]), 0)) if kind == "linear_x" \
                        else ((0, 0), (0, int(source["value_nm"])))
            if endpoints is not None:
                point_a, point_b = endpoints
                sheet_a = _to_sheet(target, point_a) if target is not None else None
                sheet_b = _to_sheet(target, point_b) if target is not None else None
                if sheet_a is not None and sheet_b is not None:
                    projected = True
                    model_a, model_b = sheet_a, sheet_b
                else:
                    model_a, model_b = point_a[:2], point_b[:2]
                anchor_a, anchor_b, geometry = _dimension_geometry(
                    model_a, model_b, offset_nm=offset, layer=_sheet.LAYER_DIMENSION,
                    source="model_item_dimension")
        else:
            geometry_note = "no_2d_projection_for_kind"

        args: dict[str, Any] = {
            "stable_key": "%s|%s" % (source["source"], source["model_ref"]),
            "source": str(source["source"]),
            "source_model_ref": str(source["model_ref"]),
            "source_sketch_uuid": str(source["sketch_uuid"]),
            "model_value_nm": int(source["value_nm"]),
            "measured_nm": measured,
            "measured_from": str(measured_from),
            "delta_nm": delta,
            "matched": matched,
            "view_uuid": str(getattr(target, "uuid", "") or _sheet.NA),
            "view_name": str(getattr(target, "name", "") or _sheet.NA),
            "projected": bool(projected),
            "placement": "sheet_view" if projected else "unplaced",
            "point_a_nm": list(anchor_a) if anchor_a else [],
            "point_b_nm": list(anchor_b) if anchor_b else [],
            "geometry_curves": len(geometry),
            "geometry_note": geometry_note or _sheet.NA,
            "notes": list(notes),
        }
        if geometry:
            args["geometry"] = [curve.as_dict() for curve in geometry]
        created.append(_append_dimension(annotations, kind="model_item_dimension",
                                         value_nm=int(source["value_nm"]),
                                         args=args, text_key=str(source["text_key"]),
                                         layer=_sheet.LAYER_DIMENSION))

    if isinstance(sheet_obj, _sheet.Sheet):
        for note in notes:
            sheet_obj.note(note)
    return created


# --------------------------------------------------------------------------------------
# 操作 2：手工智能尺寸（Smart Dimension）
# --------------------------------------------------------------------------------------


def _smart_value(kind: str, point_a: tuple[int, ...], point_b: tuple[int, ...]) -> int:
    """现算真值（整数纳米）：`aligned` 欧氏距离（整数平方根，不引入浮点误差）、`linear_*` 单轴分量。"""
    deltas = [int(point_b[index]) - int(point_a[index]) for index in range(min(len(point_a), len(point_b)))]
    if kind == "linear_x":
        return abs(deltas[0])
    if kind == "linear_y":
        return abs(deltas[1]) if len(deltas) > 1 else 0
    if kind == "aligned":
        return math.isqrt(sum(component * component for component in deltas))
    raise _fail("unsupported_kind", "smart dimension kind is not supported", kind=str(kind),
                supported=["aligned", "linear_x", "linear_y", "diameter", "radius"])


def smart_dimension(sk_or_doc: Any, *, sheet: Any = None, point_a_nm: Any = None, point_b_nm: Any = None,
                    kind: str = "aligned", view: Any = None, view_uuid: Any = None,
                    standard: str = DEFAULT_STANDARD, language: str = DEFAULT_LANGUAGE,
                    size: Any = None, scale: Any = None, auto_views: bool = True,
                    offset_nm: int = DEFAULT_OFFSET_NM, text_key: str = "dim.smart",
                    depth_nm: int = 0) -> list[str]:
    """**手工智能尺寸**：调用方在图纸上选两点，本片现算真值并生成真图元。

    与 `import_model_dimensions` 的分工：那条搬**模型侧既有尺寸**，这条量**调用方现场选的两点**，
    数值由本函数按整数纳米现算（`aligned` 走 `math.isqrt(Σd²)` ⇒ 无浮点漂移），并逐条与可选视图的
    真投影几何对齐。

    数据后果：文档 `annotations[]` 追加 `annotation_kind="smart_dimension"` 的记录，带
    `value_nm`（现算真值）、`kind`、`point_a_nm`/`point_b_nm`（模型空间）、`geometry`（真图元：
    两条尺寸界线 + 尺寸线 + 两个箭头，DIMENSIONS 图层）、`projected`。

    行为边界：

    * `kind` ∈ {`aligned`, `linear_x`, `linear_y`, `diameter`, `radius`}；`diameter`/`radius` 忽略
      传入两点、改从目标视图可见圆现量（无圆 ⇒ `CadError(reason="no_measured_feature")`，不编数）。
    * 两点重合 ⇒ `CadError(reason="zero_distance")`，**且不写任何标注**（不产生半条脏数据）。
    * 图元在图纸空间，端点由视图 `to_sheet_nm` 真投影；没有视图时端点即模型空间点、`projected=False`。
    * 长度必须是整数纳米（`bool`/`float` ⇒ `CadError(reason="bad_length")`）。

    返回：`[图纸 uuid（若本操作新建）] + [标注 uuid]`。
    """
    if kind not in ("aligned", "linear_x", "linear_y", "diameter", "radius"):
        raise _fail("unsupported_kind", "smart dimension kind is not supported", kind=str(kind),
                    supported=["aligned", "linear_x", "linear_y", "diameter", "radius"])
    payload = _payload_of(sk_or_doc)  # noqa: F841 - 走同一套模型适配（Document/映射都要能被解释）
    annotations = _annotations_of(sk_or_doc)
    offset = _nm(offset_nm, "offset_nm")
    depth = _nm(depth_nm, "depth_nm")
    point_a = _point(point_a_nm, "point_a_nm") if point_a_nm is not None else None
    point_b = _point(point_b_nm, "point_b_nm") if point_b_nm is not None else None
    if kind in ("aligned", "linear_x", "linear_y") and (point_a is None or point_b is None):
        raise _fail("bad_point", "smart_dimension needs both point_a_nm and point_b_nm",
                    kind=str(kind))
    if point_a is not None and point_b is not None:
        value_nm = _smart_value(kind, point_a, point_b)
        if value_nm == 0:
            raise _fail("zero_distance", "the two picked points coincide -- nothing to dimension",
                        point_a_nm=list(point_a), point_b_nm=list(point_b))
        measured_from = "picked_points"
    else:
        value_nm, measured_from = 0, ""

    created: list[str] = []
    sheet_obj = sheet
    if sheet_obj is None:
        try:
            sheet_obj = _make_sheet(sk_or_doc, standard=standard, language=language, size=size, scale=scale)
        except CadError:
            sheet_obj = None
        else:
            created.append(str(getattr(sheet_obj, "uuid", "")))
    elif not isinstance(sheet_obj, _sheet.Sheet):
        raise _fail("bad_sheet", "sheet must be a cadcore.draw.sheet.Sheet",
                    value_type=type(sheet_obj).__name__)
    if isinstance(sheet_obj, _sheet.Sheet):
        _ensure_views(sk_or_doc, sheet_obj, depth_nm=depth, auto_views=bool(auto_views))
    target = _pick_view(sheet_obj, name=view, view_uuid=view_uuid)

    geometry: list[Any] = []
    anchor_a = anchor_b = None
    projected = False
    note = ""
    if kind in ("diameter", "radius"):
        measured, measured_from = _measure_view(target, kind)
        if measured in (_sheet.UNMEASURED, _sheet.NA):
            raise _fail("no_measured_feature", "the target view exposes no measurable circle",
                        view=str(getattr(target, "name", "") or _sheet.NA), kind=str(kind))
        value_nm = int(measured)
        endpoints = _circle_endpoints(target, kind)
        if endpoints is None:
            note = "circle_geometry_unavailable"
            endpoints = ((0, 0), (value_nm, 0))
    else:
        endpoints = (point_a[:2], point_b[:2])

    model_a, model_b = endpoints[0], endpoints[1]
    sheet_a = _to_sheet(target, model_a) if target is not None else None
    sheet_b = _to_sheet(target, model_b) if target is not None else None
    if sheet_a is not None and sheet_b is not None:
        projected = True
        draw_a, draw_b = sheet_a, sheet_b
    else:
        draw_a, draw_b = model_a, model_b
    try:
        anchor_a, anchor_b, geometry = _dimension_geometry(
            draw_a, draw_b, offset_nm=offset, layer=_sheet.LAYER_DIMENSION, source="smart_dimension")
    except CadError as exc:
        if exc.reason != "zero_distance" or kind == "diameter":
            raise
        raise _fail("zero_distance", "the two picked points project onto the same drawing point",
                    point_a_nm=list(model_a), point_b_nm=list(model_b))

    args: dict[str, Any] = {
        "stable_key": "%s|%s|%s" % (kind, model_a, model_b),
        "kind": str(kind),
        "value_nm": int(value_nm),
        "measured_from": str(measured_from),
        "point_a_nm": list(model_a),
        "point_b_nm": list(model_b),
        "view_uuid": str(getattr(target, "uuid", "") or _sheet.NA),
        "view_name": str(getattr(target, "name", "") or _sheet.NA),
        "projected": bool(projected),
        "drawing_space": bool(projected),
        "geometry_curves": len(geometry),
        "geometry_note": note or _sheet.NA,
        "offset_nm": int(offset),
    }
    if geometry:
        args["geometry"] = [curve.as_dict() for curve in geometry]
    created.append(_append_dimension(annotations, kind="smart_dimension", value_nm=int(value_nm),
                                     args=args, text_key=str(text_key),
                                     layer=_sheet.LAYER_DIMENSION))
    if isinstance(sheet_obj, _sheet.Sheet) and note:
        sheet_obj.note(note)
    return created


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "import_model_dimensions": import_model_dimensions,
    "smart_dimension": smart_dimension,
}


# --------------------------------------------------------------------------------------
# 自检（不依赖 pytest / 不依赖任何夹具文件）
# --------------------------------------------------------------------------------------


def _sample_document() -> dict[str, Any]:
    """自检夹具：60×40 mm 草图（两条驱动尺寸）+ 20 mm 拉伸（一条特征尺寸），**自带不读盘**。"""
    def tag(name: str) -> str:
        return str(_uuid.uuid5(_NS, name))

    return {
        "meta": {"doc_uuid": tag("doc.plate")},
        "sketches": [{
            "uuid": tag("sketch.plate"), "plane": "XY", "entities": [], "constraints": [],
            "dimensions": [
                {"uuid": tag("dim.width"), "kind": "linear_x", "refs": [],
                 "value_nm": 60 * NM_PER_MM, "text_key": "dim.plate.width"},
                {"uuid": tag("dim.height"), "kind": "linear_y", "refs": [],
                 "value_nm": 40 * NM_PER_MM, "text_key": "dim.plate.height"},
            ],
        }],
        "features": [{"uuid": tag("feat.extrude"), "type": "extrude", "suppressed": False,
                      "params": {"distance_nm": 20 * NM_PER_MM}}],
        "annotations": [],
    }


def selftest() -> dict[str, Any]:
    """硬断言自检：真调用两条操作 + 反假绿（错误路径不得留脏数据）。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append((str(name), bool(ok), str(detail)))
        return bool(ok)

    doc = _sample_document()
    dim_uuid = str(doc["sketches"][0]["dimensions"][0]["uuid"])
    before = len(doc["annotations"])
    ids = import_model_dimensions(doc, sheet=None, auto_views=False)
    after = len(doc["annotations"])
    check("import.returns_list", isinstance(ids, list) and all(isinstance(item, str) and item for item in ids),
          "n=%d" % len(ids))
    check("import.uuids_distinct", len(set(ids)) == len(ids), "distinct=%d" % len(set(ids)))
    check("import.doc_mutated", after == before + len(ids), "before=%d after=%d returned=%d"
          % (before, after, len(ids)))
    check("import.count_ge_3", len(ids) >= 3, "imported=%d" % len(ids))

    first = doc["annotations"][0]
    check("import.value_from_model", int(first.get("value_nm", -1)) == 60 * NM_PER_MM,
          "value_nm=%s" % first.get("value_nm"))
    check("import.model_ref_traced", str(first.get("args", {}).get("source_model_ref", "")) == dim_uuid,
          "ref=%s" % first.get("args", {}).get("source_model_ref"))
    check("import.real_geometry", int(first.get("args", {}).get("geometry_curves", 0)) >= 5,
          "curves=%s" % first.get("args", {}).get("geometry_curves"))
    if first.get("args", {}).get("projected") is False:
        span = abs(int(first["args"]["point_a_nm"][0]) - int(first["args"]["point_b_nm"][0]))
        check("import.unplaced_span_true", span == 60 * NM_PER_MM, "span_nm=%d" % span)
    geom_points = first.get("args", {}).get("geometry") or []
    check("import.geometry_serializable", bool(geom_points) and all(isinstance(item, dict) for item in geom_points),
          "records=%d" % len(geom_points))

    before_smart = len(doc["annotations"])
    smart_ids = smart_dimension(doc, sheet=None, point_a_nm=(0, 0), point_b_nm=(30 * NM_PER_MM, 40 * NM_PER_MM),
                                kind="aligned")
    check("smart.345_exact", int(doc["annotations"][-1].get("value_nm", -1)) == 50 * NM_PER_MM,
          "value_nm=%s" % doc["annotations"][-1].get("value_nm"))
    check("smart.doc_mutated", len(doc["annotations"]) == before_smart + len(smart_ids),
          "before=%d after=%d" % (before_smart, len(doc["annotations"])))
    check("smart.real_geometry", int(doc["annotations"][-1].get("args", {}).get("geometry_curves", 0)) >= 5,
          "curves=%s" % doc["annotations"][-1].get("args", {}).get("geometry_curves"))
    check("smart.measured_from_picked", str(doc["annotations"][-1].get("args", {}).get("measured_from", ""))
          == "picked_points", "measured_from=%s" % doc["annotations"][-1].get("args", {}).get("measured_from"))

    frozen = len(doc["annotations"])
    for name, kwargs, reason in (
        ("zero_distance", {"point_a_nm": (5, 5), "point_b_nm": (5, 5)}, "zero_distance"),
        ("bad_length_bool", {"point_a_nm": (True, 0), "point_b_nm": (10, 0)}, "bad_length"),
        ("bad_kind", {"point_a_nm": (0, 0), "point_b_nm": (10, 0), "kind": "exploded"}, "unsupported_kind"),
    ):
        try:
            smart_dimension(doc, sheet=None, **kwargs)
        except CadError as exc:
            check("reject." + name, str(exc.reason) == reason, "reason=%s" % exc.reason)
        else:
            check("reject." + name, False, "no_error")
    check("reject.no_partial_write", len(doc["annotations"]) == frozen, "annotations=%d" % len(doc["annotations"]))

    failed = [name for name, ok, _ in checks if not ok]
    return {"family": FAMILY, "ops": sorted(OPERATIONS), "assertions": len(checks),
            "failed": failed, "checks": checks}


if __name__ == "__main__":
    report = selftest()
    for check_name, ok, detail in report["checks"]:
        print("check=%s ok=%d %s" % (check_name, 1 if ok else 0, detail))
    if report["failed"]:
        print("FEATURE_FAIL family=%s failed=%s" % (FAMILY, ",".join(report["failed"])))
        raise SystemExit(1)
    print("assertions=%d all_passed=1" % report["assertions"])
    print("FEATURE_OK family=sw_drawings op=import_model_dimensions verdict=PASS")
    print("FEATURE_OK family=sw_drawings op=smart_dimension verdict=PASS")
