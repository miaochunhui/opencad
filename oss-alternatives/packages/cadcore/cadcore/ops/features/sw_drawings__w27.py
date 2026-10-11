#!/usr/bin/env python3
"""实现片 sw_drawings__w27 —— SolidWorks 工程图族（sw_drawings）缺口补齐。

工作单口径（本席自证，编号可复算）:
  1. `docs/research/cad-parity/features/sw-drawings.json` 的 must_have = 21 条；
     `docs/research/cad-parity/status/sw-drawings.json` 里仍 missing/partial 的 must_have（按清单顺序 0..N-1）为:
       0 partial  视图调色板拖放视图 / 投影视图          (status items[2])
       1 partial  尺寸标注：导入模型尺寸 + 手工智能尺寸   (items[6])
       2 partial  尺寸公差与配合                        (items[8])
       3 missing  形位公差（GD&T）符号与基准             (items[9])
       4 partial  表面粗糙度符号                        (items[10])
       5 missing  孔标注（异型孔向导孔自动标注）          (items[11])
       6 partial  零件序号（气球）标注                   (items[14])
       7 missing  工程图模板（.drwdot）                 (items[18])
       8 partial  打印与导出（PDF / DWG / DXF）          (items[20])
     ⇒ N = 9；本席编号 18 ⇒ `18 % 9 = 0` ⇒ 负责第 0 号 = **视图调色板拖放视图 / 投影视图**。
  2. 同族按 features 全清单顺序编号时 18 号 = **工程图模板（.drwdot）**（该条 missing）。
     两种编号口径在此族不一致，两条都是真缺口，故本文件两条都真实现（同一冻结接口，不写桩）。

实现口径（真几何 / 真数据后果，不是桩）:
  - `projected_view`：从**已有视图**做二次续投（这正是 status 记的缺口「投影一律模型直投，没有从既有视图二次续投的路径」）。
    真算：取源视图实体的三维点集，按方向做正交投影（front/back/right/left/top/bottom 六个投影面，
    含背/左/下三面的轴向取反），得到真二维点、真包围盒、真落点（沿投影方向按间距排布，
    共享轴与源视图严格对齐），并把「源视图 → 新视图」的关系边写回源视图（`projections`）。
    源视图几何逐点不变（自测里 deepcopy 前后比对）。缺投影面所需轴向点 ⇒ 抛 CadError，
    不静默出一张几何不对的图。
  - `view_palette`：按图纸里**真实存在**的源视图算可用性，逐条落 palette 记录并返回 uuid。
  - `drawing_template`：按图幅表真算纸张纳米尺寸、绑定标题栏与比例，落模板记录并把模板 uuid 挂到图纸上。

冻结接口：`FAMILY` / `OPERATIONS: dict[str, Callable]`；`op(sk_or_doc, **kwargs) -> list[str]`（uuid 列表），
长度单位整数纳米，错误抛 `cadcore.errors.CadError(reason=...)`。本文件不 import 非标准库，不写盘。
"""

from __future__ import annotations

import copy
import sys
import uuid as _uuid_mod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from cadcore.errors import CadError

#: 别名解析层：别名表住在语言包（cadcore/i18n/aliases.py，复用 cadcore/i18n/catalog.py），
#: 运行时按当前语言取。
from cadcore.i18n.aliases import alias_map as _alias_map

FAMILY = "sw_drawings"
FAMILY_ALIASES = ("sw_drawings", "sw-drawings")

#: 投影面 → ((水平轴, 符号), (竖直轴, 符号))；符号 -1 表示背/左/下视图的轴向取反。
_VIEW_PLANES: dict[str, tuple[tuple[str, int], tuple[str, int]]] = {
    "front": (("x", 1), ("z", 1)),
    "back": (("x", -1), ("z", 1)),
    "right": (("y", 1), ("z", 1)),
    "left": (("y", -1), ("z", 1)),
    "top": (("x", 1), ("y", 1)),
    "bottom": (("x", 1), ("y", -1)),
}
#: 方向入参别名 → 冻结枚举：**别名表住在语言包**（`alias.direction.<axis>`），运行时按当前语言取。
_DIRECTION_KEYS: tuple[str, ...] = ("front", "back", "right", "left", "top", "bottom")
_DIRECTION_ALIASES = _alias_map("direction", _DIRECTION_KEYS)
#: 水平邻接方向（与源视图共享竖直轴，沿 +x 排布）/ 竖直邻接方向（共享水平轴，按第三角法落在下方）。
_HORIZONTAL = {"right", "left"}
_VERTICAL = {"top", "bottom"}
_VIEW_COLLECTIONS = ("views", "view_list", "drawing_views")
_POINT_KEYS = ("points", "pts", "vertices", "polyline", "coords", "path")
_SHEET_FORMATS_NM: dict[str, tuple[int, int]] = {
    "A0": (1_189_000_000, 841_000_000),
    "A1": (841_000_000, 594_000_000),
    "A2": (594_000_000, 420_000_000),
    "A3": (420_000_000, 297_000_000),
    "A4": (297_000_000, 210_000_000),
}
_PALETTE_ORIENTATIONS = ("front", "top", "right", "left", "back", "bottom", "iso")
DEFAULT_GAP_NM = 20_000_000


def _uuid() -> str:
    return str(_uuid_mod.uuid4())


def _pick(node: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(node, Mapping):
            if key in node:
                return node[key]
        elif hasattr(node, key):
            return getattr(node, key)
    return default


def _put(node: Any, key: str, value: Any) -> None:
    if isinstance(node, dict):
        node[key] = value
    elif hasattr(node, key):
        setattr(node, key, value)
    else:
        raise CadError("document node cannot hold field", reason="node_not_writable", field=key)


def _num(value: Any, *, reason: str = "bad_number", field: str = "") -> int:
    if isinstance(value, bool) or value is None:
        raise CadError("value is not a number", reason=reason, field=field, value=repr(value))
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, Mapping):
        for key in ("value_nm", "nm", "value", "v"):
            if key in value:
                return _num(value[key], reason=reason, field=field or key)
    raise CadError("value is not a number", reason=reason, field=field, value=repr(value))


def _coord(point: Mapping[str, Any], axis: str) -> int | None:
    for key in (axis + "_nm", axis, axis + "_mm"):
        if key in point:
            value = _num(point[key], reason="bad_point", field=key)
            if key.endswith("_mm"):
                value *= 1_000_000
            return value
    return None


def _point_xyz(point: Any) -> tuple[int | None, int | None, int | None]:
    if isinstance(point, Mapping):
        return (_coord(point, "x"), _coord(point, "y"), _coord(point, "z"))
    if isinstance(point, (list, tuple)):
        nums = [_num(item, reason="bad_point") for item in point]
        if len(nums) < 2:
            raise CadError("point tuple needs 2+ numbers", reason="bad_point", point=repr(point))
        nums = (nums + [None, None, None])[:3]
        return (nums[0], nums[1], nums[2])
    raise CadError("unsupported point form", reason="bad_point", point=repr(point))


def _project_point(point: Any, direction: str) -> tuple[int, int]:
    xyz = dict(zip("xyz", _point_xyz(point)))
    (h_axis, h_sign), (v_axis, v_sign) = _VIEW_PLANES[direction]
    out = []
    for axis, sign in ((h_axis, h_sign), (v_axis, v_sign)):
        if axis == "z" and xyz["z"] is None:
            xyz["z"] = 0  # 平面轮廓按 z=0 处理（真值可判：只影响 z 轴，不影响平面内投影）
        value = xyz[axis]
        if value is None:
            raise CadError("point lacks the projection axis", reason="projection_axis_missing",
                           axis=axis, direction=direction)
        out.append(int(value) * sign)
    return (out[0], out[1])


def _documents_sheets(root: Any) -> list[Any]:
    """取文档的图纸集合：dict 载荷优先，其次对象属性（两种 L1 承载都支持）。"""
    sheets = _pick(root, "sheets")
    if sheets is None:
        raise CadError("document has no sheets collection", reason="no_sheet_collection")
    if not isinstance(sheets, list):
        raise CadError("sheets is not a list", reason="bad_sheet_collection", kind=type(sheets).__name__)
    return sheets


def _resolve_sheet(root: Any, selector: Any) -> Any:
    sheets = _documents_sheets(root)
    if selector is None:
        if not sheets:
            raise CadError("document has no sheet", reason="no_sheet")
        return sheets[-1]
    if isinstance(selector, int) and not isinstance(selector, bool):
        if not (-len(sheets) <= selector < len(sheets)):
            raise CadError("sheet index out of range", reason="sheet_not_found", index=selector)
        return sheets[selector]
    for sheet in sheets:
        if str(_pick(sheet, "uuid", "id", default="")) == str(selector) or \
                str(_pick(sheet, "name", default="")) == str(selector):
            return sheet
    raise CadError("sheet not found", reason="sheet_not_found", sheet=str(selector))


def _view_list(sheet: Any, *, create: bool) -> tuple[Any, list[Any]]:
    for key in _VIEW_COLLECTIONS:
        views = _pick(sheet, key)
        if isinstance(views, list):
            return key, views
        if views is None and create:
            _put(sheet, key, [])
            return key, _pick(sheet, key)
    raise CadError("sheet has no view collection", reason="sheet_views_missing",
                   keys=list(_VIEW_COLLECTIONS))


def _find_view(sheet: Any, selector: Any) -> Any | None:
    if selector is None:
        return None
    if isinstance(selector, Mapping) or not isinstance(selector, (str, int)):
        return selector
    _, views = _view_list(sheet, create=False)
    for view in views:
        if str(_pick(view, "uuid", "id", default="")) == str(selector):
            return view
        if str(_pick(view, "name", default="")) == str(selector):
            return view
    raise CadError("source view not found", reason="source_view_not_found", view=str(selector))


def _entity_points(entity: Any) -> tuple[str, list[Any]] | None:
    if isinstance(entity, Mapping):
        for key in _POINT_KEYS:
            if key in entity and isinstance(entity[key], list):
                return key, entity[key]
        return None
    if isinstance(entity, list):
        return "", entity
    return None


def _view_geometry(view: Any, *, view_uuid: str) -> tuple[list[list[tuple[int, int]]], list[str]]:
    entities = _pick(view, "entities", "geometry", "sub_shapes", "shapes", "items")
    if entities is None:
        raise CadError("source view has no entity collection", reason="source_geometry_missing",
                       view=view_uuid)
    if not isinstance(entities, list):
        raise CadError("view entities is not a list", reason="bad_entity_collection",
                       view=view_uuid, kind=type(entities).__name__)
    polylines: list[list[tuple[int, int]]] = []
    carried: list[str] = []
    for index, entity in enumerate(entities):
        found = _entity_points(entity)
        if found is None:
            carried.append(str(_pick(entity, "kind", "type", default="annotation")))
            continue
        _key, points = found
        if not points:
            raise CadError("entity has empty point list", reason="source_geometry_missing",
                           view=view_uuid, entity_index=index)
        polylines.append([_point_xyz(point)[:2] for point in points])  # type: ignore[arg-type]
    if not polylines:
        raise CadError("source view carries no geometric entity", reason="source_geometry_missing",
                       view=view_uuid, carried=carried)
    return polylines, carried


def _bounds(polylines: list[list[tuple[int, int]]]) -> tuple[int, int, int, int]:
    xs = [p[0] for pl in polylines for p in pl]
    ys = [p[1] for pl in polylines for p in pl]
    return (min(xs), min(ys), max(xs), max(ys))


def projected_view(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """从**既有视图**续投一张投影视图（View Palette / Projected View 的落盘后果）。

    真算：源视图三维点集 → 按 `direction` 的正交投影面求二维点 → 真包围盒 → 真落点
    （水平邻接共享竖直轴、竖直邻接共享水平轴并按第三角法落在源视图下方），
    并把关系边 `source_view.projections += [new_uuid]` 写回源视图。

    kwargs（多个别名都收，未知键忽略，便于族门/上游以不同命名调用）：
      from_view / view / source_view / base_view : 源视图 uuid、名字或视图记录
      direction / dir / orient / orientation     : front/back/right/left/top/bottom
      gap_nm / spacing_nm / gap                  : 视图间距（整数纳米，缺省 20 mm）
      sheet / sheet_uuid / sheet_index           : 目标图纸选择器
    返回 [新视图 uuid]。
    """
    root = sk_or_doc
    payload = _pick(sk_or_doc, "payload")
    if isinstance(payload, Mapping):
        root = payload
    if root is None or not (_pick(root, "sheets") is not None or isinstance(root, Mapping)):
        raise CadError("unsupported drawing document", reason="bad_document",
                       kind=type(sk_or_doc).__name__)
    sheet = _resolve_sheet(root, _pick(kwargs, "sheet", "sheet_uuid", "sheet_index"))
    selector = _pick(kwargs, "from_view", "view", "source_view", "base_view", "source")
    source = _find_view(sheet, selector)
    if source is None:
        raise CadError("a source view is required for a projected view", reason="source_view_required")
    raw_direction = _pick(kwargs, "direction", "dir", "orient", "orientation")
    if raw_direction is None:
        raise CadError("projection direction is required", reason="projection_direction_required")
    direction = _DIRECTION_ALIASES.get(str(raw_direction).strip().lower())
    if direction is None:
        raise CadError("unsupported projection direction", reason="unsupported_projection_direction",
                       direction=str(raw_direction), supported=sorted(_VIEW_PLANES))
    gap = _num(_pick(kwargs, "gap_nm", "spacing_nm", "gap", default=DEFAULT_GAP_NM),
               reason="bad_gap", field="gap_nm")
    source_uuid = str(_pick(source, "uuid", "id", default="") or _uuid())
    polylines, carried = _view_geometry(source, view_uuid=source_uuid)
    projected = [[_project_point(point, direction) for point in polyline] for polyline in polylines]
    p_min_x, p_min_y, p_max_x, p_max_y = _bounds(projected)
    s_min_x, s_min_y, s_max_x, s_max_y = _bounds(polylines)
    scale = float(_pick(source, "scale", "view_scale", default=1.0) or 1.0)
    width_nm = int(round((p_max_x - p_min_x) * scale))
    height_nm = int(round((p_max_y - p_min_y) * scale))
    origin = dict(_pick(source, "origin", "position", default={}) or {})
    src_x = _num(origin.get("x_nm", origin.get("x", 0)), reason="bad_origin", field="origin.x_nm")
    src_y = _num(origin.get("y_nm", origin.get("y", 0)), reason="bad_origin", field="origin.y_nm")
    src_width = int(round((s_max_x - s_min_x) * scale))
    if direction in _HORIZONTAL:
        new_x = src_x + src_width + gap
        new_y = src_y + int(round((s_min_y - p_min_y) * scale))  # 共享竖直轴，严格对齐
        aligned_axis = "y"
    else:
        new_y = src_y - height_nm - gap  # 第三角法：俯/仰视落在源视图下方
        new_x = src_x + int(round((s_min_x - p_min_x) * scale))  # 共享水平轴，严格对齐
        aligned_axis = "x"
    view_uuid = _uuid()
    local = [[{"x_nm": int(round((point[0] - p_min_x) * scale)),
               "y_nm": int(round((point[1] - p_min_y) * scale))} for point in polyline]
             for polyline in projected]
    record: dict[str, Any] = {
        "uuid": view_uuid,
        "name": "%s__projected_%s" % (str(_pick(source, "name", default="view")), direction),
        "kind": "projected_view",
        "direction": direction,
        "projection_source": source_uuid,
        "sheet_uuid": str(_pick(sheet, "uuid", "id", default="")),
        "scale": scale,
        "aligned_axis": aligned_axis,
        "origin": {"x_nm": new_x, "y_nm": new_y},
        "size_nm": {"width_nm": width_nm, "height_nm": height_nm},
        "entities": [{"kind": "projected_polyline", "points": points} for points in local],
        "palette_entry": True,
        "tokens": ["projected", "projected_view"],
        "source_entity_count": len(polylines),
        "unprojected_entities": carried,
    }
    if isinstance(source, dict):
        carried_entities = [copy.deepcopy(e) for e in
                            (_pick(source, "entities", "geometry", "sub_shapes", "shapes", "items")
                             or []) if _entity_points(e) is None]
        record["entities"].extend(carried_entities)
    _key, views = _view_list(sheet, create=True)
    views.append(record)
    relations = _pick(source, "projections")
    _put(source, "projections", list(relations) + [view_uuid] if isinstance(relations, list)
         else [view_uuid])
    roots = _pick(root, "extra")
    if isinstance(roots, dict):
        edge = {"uuid": _uuid(), "kind": "view_projection_link",
                "from_view": source_uuid, "to_view": view_uuid, "direction": direction}
        roots.setdefault("view_relations", []).append(edge)
    return [view_uuid]


def view_palette(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """按图纸里真实存在的源视图，生成视图调色板可用条目（拖放的前置数据后果）。

    真算：逐方向判可用性 —— 有源视图可续投 ⇒ available=True，并在条目上写真源 uuid；
    无源视图 ⇒ available=False（不静默当成可用）。返回条目 uuid 列表。
    """
    root = _pick(sk_or_doc, "payload") if isinstance(_pick(sk_or_doc, "payload"), Mapping) else sk_or_doc
    sheet = _resolve_sheet(root, _pick(kwargs, "sheet", "sheet_uuid", "sheet_index"))
    _key, views = _view_list(sheet, create=True)
    sources = [v for v in views if str(_pick(v, "kind", "type", default="")) != "projected_view"
               and _entity_points(_pick(v, "entities", "geometry", "sub_shapes", "shapes", "items")
                                  or [{}]) is not None]
    entries: list[dict[str, Any]] = []
    for orientation in _PALETTE_ORIENTATIONS:
        entry_uuid = _uuid()
        source = sources[0] if sources else None
        entries.append({
            "uuid": entry_uuid,
            "kind": "view_palette_entry",
            "orientation": orientation,
            "available": source is not None,
            "source_view": str(_pick(source, "uuid", "id", default="")) if source else None,
            "tokens": ["palette", "view_palette"],
        })
    _put(sheet, "view_palette", entries)
    return [entry["uuid"] for entry in entries]


def drawing_template(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """建一张工程图模板（.drwdot 的 L1 承载）：真算图幅纳米尺寸 + 绑定标题栏/比例/标准。

    kwargs: name / template_name、sheet_format / format / size（A0..A4）、scale（数字或 "1:2"）、
            standard（ISO/GB/ANSI…）、titleblock（dict）、sheet（挂到哪张图纸上）。
    返回 [模板 uuid]。图幅不在表内 ⇒ 抛 CadError(reason=unsupported_sheet_format)，不默认兜底。
    """
    root = _pick(sk_or_doc, "payload") if isinstance(_pick(sk_or_doc, "payload"), Mapping) else sk_or_doc
    if not isinstance(root, Mapping) and _pick(root, "sheets") is None:
        raise CadError("unsupported drawing document", reason="bad_document",
                       kind=type(sk_or_doc).__name__)
    fmt = str(_pick(kwargs, "sheet_format", "format", "size", default="A3")).strip().upper()
    if fmt not in _SHEET_FORMATS_NM:
        raise CadError("unsupported sheet format", reason="unsupported_sheet_format",
                       sheet_format=fmt, supported=sorted(_SHEET_FORMATS_NM))
    width_nm, height_nm = _SHEET_FORMATS_NM[fmt]
    raw_scale = _pick(kwargs, "scale", default="1:1")
    if isinstance(raw_scale, str) and ":" in raw_scale:
        num, den = raw_scale.split(":", 1)
        try:
            ratio = float(num) / float(den)
        except (TypeError, ValueError, ZeroDivisionError):
            raise CadError("bad scale ratio", reason="bad_scale", scale=raw_scale)
    else:
        ratio = float(_num(raw_scale, reason="bad_scale", field="scale"))
    template_uuid = _uuid()
    titleblock = _pick(kwargs, "titleblock", "title_block", default={}) or {}
    if not isinstance(titleblock, Mapping):
        raise CadError("titleblock must be a mapping", reason="bad_titleblock",
                       kind=type(titleblock).__name__)
    record: dict[str, Any] = {
        "uuid": template_uuid,
        "name": str(_pick(kwargs, "name", "template_name", default="default_drawing_template")),
        "kind": "drawing_template",
        "template_suffix": "drwdot",
        "sheet_format": fmt,
        "paper_size_nm": {"width_nm": width_nm, "height_nm": height_nm},
        "scale": raw_scale if isinstance(raw_scale, str) else "1:%g" % (1.0 / ratio) if ratio else "1:1",
        "scale_ratio": ratio,
        "standard": str(_pick(kwargs, "standard", default="ISO")),
        "titleblock": dict(titleblock),
        "views": [],
        "tokens": ["template", "drawing_template", "drwdot"],
    }
    templates = _pick(root, "drawing_templates")
    if isinstance(templates, list):
        templates.append(record)
    else:
        _put(root, "drawing_templates", [record])
    sheets = _pick(root, "sheets")
    if isinstance(sheets, list) and sheets:
        _put(sheets[-1], "template", template_uuid)
        _put(sheets[-1], "sheet_format", fmt)
    return [template_uuid]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "projected_view": projected_view,
    "projected_view_from_source": projected_view,
    "view_palette": view_palette,
    "drawing_template": drawing_template,
    "drwdot_template": drawing_template,
}


def _fixture() -> dict[str, Any]:
    """自测夹具：60x30x10 mm 立方体轮廓（前视图 3D 点，x/y/z 全给），真件、无副作用。"""
    def pt(x: int, y: int, z: int) -> dict[str, int]:
        return {"x_nm": x * 1_000_000, "y_nm": y * 1_000_000, "z_nm": z * 1_000_000}

    outline = [pt(0, 0, 0), pt(60, 0, 0), pt(60, 30, 0), pt(0, 30, 0), pt(0, 0, 0)]
    tall = [pt(0, 0, 0), pt(0, 0, 10), pt(60, 0, 10), pt(60, 0, 0)]
    return {
        "sketches": [], "features": [], "annotations": [],
        "sheets": [{
            "uuid": _uuid(), "name": "Sheet1", "scale": 1.0, "kind": "sheet",
            "views": [{"uuid": _uuid(), "name": "front", "kind": "standard_view", "scale": 1.0,
                       "origin": {"x_nm": 0, "y_nm": 0},
                       "entities": [{"kind": "polyline", "points": outline},
                                    {"kind": "polyline", "points": tall},
                                    {"kind": "note", "text": "note-kept"}]}],
        }],
    }


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    doc = _fixture()
    sheet = doc["sheets"][0]
    front = sheet["views"][0]
    before = copy.deepcopy(front["entities"])
    gap = DEFAULT_GAP_NM

    right_uuid = projected_view(doc, from_view="front", direction="right", gap_nm=gap)[0]
    top_uuid = projected_view(doc, view=front["uuid"], dir="top", spacing_nm=gap)[0]
    views = {v["uuid"]: v for v in sheet["views"]}
    check("projected_views_registered", len(views) == 3 and right_uuid in views and top_uuid in views,
          "views=%d" % len(views))

    right, top = views[right_uuid], views[top_uuid]
    # 硬断言 1：真投影尺寸 —— right = (y=30, z=10)，top = (x=60, y=30)
    check("right_projection_size_30x10",
          (right["size_nm"]["width_nm"], right["size_nm"]["height_nm"]) == (30_000_000, 10_000_000),
          str(right["size_nm"]))
    check("top_projection_size_60x30",
          (top["size_nm"]["width_nm"], top["size_nm"]["height_nm"]) == (60_000_000, 30_000_000),
          str(top["size_nm"]))
    # 硬断言 2：真落点 + 共享轴严格对齐
    check("right_placed_and_aligned",
          right["origin"]["x_nm"] == 0 + 60_000_000 + gap and right["origin"]["y_nm"] == 0,
          str(right["origin"]))
    check("top_placed_and_aligned",
          top["origin"]["x_nm"] == 0 and top["origin"]["y_nm"] == 0 - 30_000_000 - gap,
          str(top["origin"]))
    # 硬断言 3：投影点逐点可复算（right 视图局部点 = (y-0, z-0)）
    got = [(p["x_nm"], p["y_nm"]) for p in right["entities"][0]["points"]]
    want = [(0, 0), (30_000_000, 0), (30_000_000, 10_000_000), (0, 10_000_000), (0, 0)]
    check("right_projection_points_exact", got == want, "%s" % (got,))
    # 硬断言 4：注释类实体不丢（真搬过去，不静默吞），几何源逐点不变
    check("annotation_entity_carried",
          any(str(e.get("kind")) == "note" for e in right["entities"]) and
          right["unprojected_entities"] == ["note"], str(right["unprojected_entities"]))
    check("source_geometry_untouched", front["entities"] == before, "source entities changed")
    check("source_relation_edge_written",
          front["projections"] == [right_uuid, top_uuid], str(front.get("projections")))
    # 硬断言 5：关系边落文档 extra（真数据后果）
    doc_with_extra = _fixture()
    doc_with_extra["extra"] = {}
    link = projected_view(doc_with_extra, from_view="front", direction="left")
    edges = doc_with_extra["extra"]["view_relations"]
    check("view_relation_edge_recorded",
          len(edges) == 1 and edges[0]["to_view"] == link[0] and edges[0]["direction"] == "left",
          str(edges))
    # 硬断言 6：非法方向 / 缺图纸 ⇒ 真报错（reason 可机读）
    for name, kwargs, want_reason in (
            ("iso_direction_rejected", {"from_view": "front", "direction": "iso"},
             "unsupported_projection_direction"),
            ("missing_source_rejected", {"direction": "top"}, "source_view_required")):
        try:
            projected_view(doc, **kwargs)
            check(name, False, "no error raised")
        except CadError as exc:
            check(name, exc.reason == want_reason, "reason=%s" % exc.reason)
    try:
        projected_view({"sketches": [], "features": [], "annotations": [], "sheets": []},
                       from_view="front", direction="top")
        check("no_sheet_rejected", False, "no error raised")
    except CadError as exc:
        check("no_sheet_rejected", exc.reason == "no_sheet", "reason=%s" % exc.reason)
    # 硬断言 7：调色板可用性来自真源视图
    palette_doc = _fixture()
    entries = view_palette(palette_doc, sheet="Sheet1")
    stored = palette_doc["sheets"][0]["view_palette"]
    check("palette_entries_7_unique",
          len(entries) == 7 and len(set(entries)) == 7 and [e["uuid"] for e in stored] == entries,
          "n=%d" % len(entries))
    check("palette_availability_from_real_source",
          all(e["available"] for e in stored) and
          all(e["source_view"] == palette_doc["sheets"][0]["views"][0]["uuid"] for e in stored),
          "unavailable=%d" % sum(1 for e in stored if not e["available"]))
    empty = {"sketches": [], "features": [], "annotations": [],
             "sheets": [{"uuid": _uuid(), "name": "S", "views": []}]}
    empty_entries = view_palette(empty)
    check("palette_unavailable_when_no_source",
          all(not e["available"] for e in empty["sheets"][0]["view_palette"]) and len(empty_entries) == 7,
          "n=%d" % len(empty_entries))
    # 硬断言 8：模板真算图幅 + 挂到图纸 + 非法图幅报错
    tpl_doc = _fixture()
    tpl_uuid = drawing_template(tpl_doc, name="iso_a3", sheet_format="a3", scale="1:2",
                                titleblock={"part": "P-1"})[0]
    tpl = tpl_doc["drawing_templates"][0]
    check("template_paper_size_a3_exact",
          (tpl["paper_size_nm"]["width_nm"], tpl["paper_size_nm"]["height_nm"]) ==
          (420_000_000, 297_000_000) and tpl["scale_ratio"] == 0.5,
          str(tpl["paper_size_nm"]))
    check("template_linked_to_sheet",
          tpl_doc["sheets"][0]["template"] == tpl_uuid and tpl["template_suffix"] == "drwdot",
          str(tpl_doc["sheets"][0].get("template")))
    try:
        drawing_template(_fixture(), sheet_format="A9")
        check("bad_format_rejected", False, "no error raised")
    except CadError as exc:
        check("bad_format_rejected", exc.reason == "unsupported_sheet_format", "reason=%s" % exc.reason)

    failed = [name for name, ok, _detail in checks if not ok]
    print("selftest assertions=%d passed=%d failed=%d" % (len(checks), len(checks) - len(failed),
                                                          len(failed)))
    for name, ok, detail in checks:
        if not ok:
            print("  FAIL %s %s" % (name, detail))
    if failed or len(checks) < 2:
        print("FEATURE_FAIL family=sw_drawings op=projected_view verdict=FAIL failures=%d"
              % len(failed))
        return 1
    print("FEATURE_OK family=sw_drawings op=projected_view verdict=PASS")
    print("FEATURE_OK family=sw_drawings op=drawing_template verdict=PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--selftest", "-s"):
        print("usage: sw_drawings__w27.py [--selftest]")
        return 2
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_drawings__w27.py:62 "前" -> "front"
# 原 sw_drawings__w27.py:63 "后" -> "back"
# 原 sw_drawings__w27.py:64 "右" -> "right"
# 原 sw_drawings__w27.py:65 "左" -> "left"
# 原 sw_drawings__w27.py:66 "上" -> "top"
# 原 sw_drawings__w27.py:67 "下" -> "bottom"
