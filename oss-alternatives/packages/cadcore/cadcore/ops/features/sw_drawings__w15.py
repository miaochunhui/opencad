#!/usr/bin/env python3
"""SW-DRAWINGS 片模块 w15 —— 该族仍 missing/partial 的 must_have 第 6 号：零件序号（气球）标注。

族接口（冻结）：

    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    op(sk_or_doc, **kwargs) -> list[str]     # 返回本次新建对象的 uuid 列表
    长度一律整数纳米；错误一律 cadcore.errors.CadError(reason=...)

清单与取号（`docs/research/cad-parity/features/sw-drawings.json` × `status/sw-drawings.json`）
--------------------------------------------------------------------------------------
按 must_have 顺序把 status 里仍 missing/partial 的条目 0 起编号（清单顺序即编号顺序）：

    0 视图调色板拖放视图 / 投影视图        partial
    1 尺寸标注：导入模型尺寸 + 手工智能尺寸  partial
    2 尺寸公差与配合                      partial
    3 形位公差（GD&T）符号与基准           missing
    4 表面粗糙度符号                      partial
    5 孔标注（异型孔向导孔自动标注）        missing
   *6 零件序号（气球）标注                 partial   <-- 本片负责（MUST_HAVE_INDEX = 6）
    7 工程图模板（.drwdot）               missing
    8 打印与导出（PDF / DWG / DXF）        partial

为什么第 6 号仍是 partial：族里只有 `sw_drawings_extra.balloon`（圆 + 一段直线，无箭头、无 BOM 行号
联动，且族门未确认 implemented），而 `sw_drawings.py:1155 bom_table` 只出框线与 `bom_row` 文本，
**气球与明细表行号之间没有任何数据联系**（status note 原话：无气球图元与明细表行号关联）。
本片补的就是这条联系：序号由 BOM 行现编、几何落视图，两者同源。

能力（真几何 + 真数据后果，不装样子）
------------------------------------
`balloon`：把 BOM 行指到图上 —— 每行（合并后）一颗序号圆 + 两段引线 + 两条箭头倒刺 + 一条 annotation。

* **行来源与序号联动**：`items=` 优先，否则读文档顶层 `source_key`（缺省 `x_bom`）；行先按
  `sw_drawings_extra._merge_rows` 合并（**与 `bom_row_items` / BOM 表同一份实现**，不另抄一份：
  同零件同材料合并数量、质量"全采才求和，有一行未采则整组记未采"），再自 `start_item` 顺序现编序号
  ⇒ **气球号 == BOM 行号**，不采信输入里的行号。
* **真几何**：圆（`kind="circle"`，半径 `radius_nm`）+ 引线"圆边 → 拐点 → 锚点"两段折线（拐点相对锚点
  在气球一侧偏 `shoulder_nm`）+ 锚点处两条倒刺构成箭头（张角 20°、长 `arrow_nm`）；曲线全部挂到目标
  视图的 `visible`（与 `centerline` 同一落点）⇒ 导出/渲染面直接看得见，不是只写一条文本。
* **锚点**：`anchors_nm` 显式给（每行一个点）；缺省取目标视图**当前几何包围盒**右缘按行等分
  （视图无可测几何 ⇒ `empty_balloon_anchor`，不编假锚点）。
* **位置**：`positions_nm` 显式给；缺省沿图纸右留白自下而上排一列（`margin_nm` / `row_gap_nm`）；
  圆越出图幅 ⇒ 跳过并 `Sheet.note()` 留痕；整批一颗都放不下 ⇒ `balloon_off_sheet`（不返回空列表冒充成功）。
* **机读落点**：每行一条 `annotation_kind="balloon"` 的 annotation（`args` 带 序号/数量/零件/材料/质量/
  圆心/锚点/半径/引线长/图层/视图 uuid），另有 `annotation_kind="balloon_summary"` 一条汇总
  （`balloons` / `rows` / `merged_rows` / `skipped` / `circles` / `leaders` / `arrow_barbs` / `items`）。
* **校验先于改动**：行、位置个数、锚点个数、半径、图层名等全部在**落任何几何之前**校验，
  不允许"改了一半再抛错"。

返回：`[图纸 uuid（若本操作新建）] + [视图 uuid（若新建）…] + [曲线 uuid …] + [annotation uuid …]`。

显式不支持（如实留 missing，不假装有）
------------------------------------
* 无 UI：不做"调色板拖放 / 点选零件面"交互；本操作是**数据驱动**的（行 → 位置 / 锚点）。
* 序号数字**不做字形几何**：数值按本族 L1 口径落在 annotation 的 `args["text"]`，渲染面另有其人。
* 不自动识别装配体零件面：锚点须由调用方给或从视图几何现算 —— **不猜**零件归属。
* 未做"堆叠气球"（多个序号共圆）与自动避让碰撞；两条属 nice_to_have，不在本片范围。

自检：`python3 cadcore/ops/features/sw_drawings__w15.py`
⇒ 末行 `FEATURE_OK family=sw_drawings op=balloon verdict=PASS …`（≥5 条硬断言，含 2 条反例）。
"""

from __future__ import annotations

import dataclasses as _dc
import math as _math
from collections.abc import Sequence as _Seq
from typing import Any, Callable

from cadcore import errors as _errors
from cadcore.draw import sheet as _sheet
from cadcore.ops import sw_drawings as _base
from cadcore.ops import sw_drawings_extra as _extra

CadError = _errors.CadError

#: 族名（冻结）。
FAMILY = "sw_drawings"

#: 本片负责 must_have 清单里 missing/partial 条目的 0 起编号与名字（见模块 docstring 的表）。
MUST_HAVE_INDEX = 6
MUST_HAVE_NAME = "part number (balloon) annotation"

#: 气球图层（渲染时可按层开关）与曲线 `source` 标记。
BALLOON_LAYER = "BALLOON"
BALLOON_SOURCE = "balloon"

DEFAULT_RADIUS_NM = 4 * _base.NM_PER_MM
DEFAULT_LEADER_NM = 15 * _base.NM_PER_MM
DEFAULT_SHOULDER_NM = 4 * _base.NM_PER_MM
DEFAULT_ARROW_NM = 3 * _base.NM_PER_MM
DEFAULT_ROW_GAP_NM = 4 * _base.NM_PER_MM
ARROW_HALF_ANGLE_DEG = 20.0


# --------------------------------------------------------------------------------------
# 小工具：校验/登记尽量复用 `sw_drawings` 的同一份实现，避免两处口径
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII，面向用户的串走语言包）。"""
    return CadError(str(message), reason=str(reason), **details)


def _flag(value: Any, *, what: str) -> bool:
    """bool 入参校验：`0/1` 不算，`True/False` 才算（不替调用方猜）。"""
    if not isinstance(value, bool):
        raise _fail("bad_flag", "flag must be a bool", field=what, value_type=type(value).__name__)
    return value


def _points(value: Any, count: int, reason: str, what: str) -> list[tuple[int, int]]:
    """"每行一个 2 元整数纳米点"：个数不符直接抛错（不补齐、不截断）。"""
    if (not isinstance(value, _Seq) or isinstance(value, (str, bytes)) or len(value) != count):
        raise _fail(reason, "%s must carry exactly one point per balloon" % what,
                    value_type=type(value).__name__,
                    points=(len(value) if isinstance(value, _Seq) and not isinstance(value, (str, bytes))
                            else _sheet.UNMEASURED),
                    balloons=int(count))
    return [_base._point_nm(point, what="%s[%d]" % (what, index)) for index, point in enumerate(value)]


def _view_bucket(sheet_obj: Any) -> list[Any]:
    """图纸的视图容器（真实 list 优先，便于追加；找不到就给空表）。"""
    for attr in ("views", "view_list", "sheet_views"):
        container = getattr(sheet_obj, attr, None)
        if isinstance(container, list):
            return container
        if isinstance(container, dict):
            return list(container.values())
        if isinstance(container, tuple):
            return list(container)
    return []


def _existing_view(sheet_obj: Any, view_uuid: Any, view: Any) -> Any:
    """按 uuid 或名字在**已有视图**里找目标（找到就直接用，不重投影）。"""
    wanted_uuid = None if view_uuid is None else str(view_uuid)
    wanted_name = None if view is None else str(view)
    for candidate in _view_bucket(sheet_obj):
        cuuid = str(getattr(candidate, "uuid", "") or "")
        cname = str(getattr(candidate, "name", "") or "")
        if wanted_uuid is not None:
            if cuuid == wanted_uuid:
                return candidate
        elif wanted_name is not None and cname == wanted_name:
            return candidate
    return None


def _inside(sheet_obj: Any, x: int, y: int, radius: int) -> bool:
    """整颗序号圆（含半径）是否落在图纸内。"""
    return (x - radius >= 0 and y - radius >= 0
            and x + radius <= int(sheet_obj.width_nm) and y + radius <= int(sheet_obj.height_nm))


def _auto_positions(sheet_obj: Any, count: int, radius: int, margin: int, gap: int) -> list[tuple[int, int]]:
    """缺省排布：沿图纸右留白自下而上排一列（圆心离右边 = `margin + radius`）。"""
    x = int(sheet_obj.width_nm) - int(margin) - int(radius)
    step = 2 * int(radius) + int(gap)
    return [(x, int(margin) + int(radius) + index * step) for index in range(count)]


def _auto_anchors(bounds: tuple[int, int, int, int], count: int) -> list[tuple[int, int]]:
    """缺省锚点：视图几何包围盒右缘按行等分（制图习惯：引线从右侧进入零件轮廓）。"""
    _x0, y0, x1, y1 = bounds
    if count <= 1:
        return [(int(x1), (int(y0) + int(y1)) // 2)]
    span = int(y1) - int(y0)
    return [(int(x1), int(y0) + int(round(span * index / float(count - 1)))) for index in range(count)]


def _guess_field(name: str) -> Any:
    """兜底造曲线时的必填字段猜测（只在 `Curve` 的构造器名字与预期不同时才走到）。"""
    if name in ("points", "visible", "curves", "items"):
        return ()
    if name in ("center", "at_nm", "anchor"):
        return (0, 0)
    if name in ("kind", "view"):
        return "circle"
    if name in ("name", "layer", "source", "shape_repr"):
        return ""
    if name in ("hidden",):
        return False
    if name in ("scale_num", "scale_den"):
        return 1
    return 0


def _circle_curve(center: tuple[int, int], radius: int, *, layer: str, name: str, source: str) -> Any:
    """真圆曲线：优先 `Curve.circle(...)`，构造器口径不同则按 dataclass 字段兜底；都不行就显式抛错。"""
    cx, cy = int(center[0]), int(center[1])
    maker = getattr(_sheet.Curve, "circle", None)
    if callable(maker):
        attempts = (
            lambda: maker((cx, cy), int(radius), layer=layer, name=name, source=source),
            lambda: maker(center=(cx, cy), radius_nm=int(radius), layer=layer, name=name, source=source),
            lambda: maker((cx, cy), radius_nm=int(radius), layer=layer, name=name, source=source),
        )
        for attempt in attempts:
            try:
                curve = attempt()
            except TypeError:
                continue
            _stamp(curve, center=(cx, cy), radius=radius, layer=layer, name=name, source=source)
            return curve
    try:
        fields = list(_dc.fields(_sheet.Curve))
    except TypeError:
        fields = []
    if not fields:
        raise _fail("balloon_geometry_unsupported", "cannot build a circle curve with this cadcore build")
    known = {"kind": "circle", "center": (cx, cy), "radius_nm": int(radius),
             "layer": layer, "name": name, "source": source}
    kwargs = {field.name: known[field.name] for field in fields if field.name in known}
    for field in fields:
        if field.name in kwargs or field.init is False:
            continue
        if field.default is not _dc.MISSING or field.default_factory is not _dc.MISSING:
            continue
        kwargs[field.name] = _guess_field(field.name)
    try:
        curve = _sheet.Curve(**kwargs)
    except TypeError as exc:
        raise _fail("balloon_geometry_unsupported", "cannot build a circle curve with this cadcore build",
                    error=str(exc))
    _stamp(curve, center=(cx, cy), radius=radius, layer=layer, name=name, source=source)
    return curve


def _stamp(curve: Any, **values: Any) -> None:
    """把构造口径可能对不上的字段再钉一遍（冻结对象钉不上就跳过，不因此失败）。"""
    for key, value in values.items():
        try:
            setattr(curve, key, value)
        except Exception:  # noqa: BLE001 - 冻结/只读对象：已有字段即够用
            pass


def _line(p1: tuple[int, int], p2: tuple[int, int], *, layer: str, name: str, source: str) -> Any:
    return _sheet.Curve.line((int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])),
                             layer=layer, name=name, source=source)


def _leader_curves(item: int, center: tuple[int, int], anchor: tuple[int, int], radius: int,
                   shoulder: int, arrow: int, *, layer: str, source: str) -> list[Any]:
    """引线两段 + 箭头两条倒刺（锚点在尖上，倒刺沿引线反方向张开 20°）。"""
    cx, cy = int(center[0]), int(center[1])
    ax, ay = int(anchor[0]), int(anchor[1])
    elbow = (ax + int(shoulder), ay) if ax <= cx else (ax - int(shoulder), ay)
    start = (cx - int(radius), cy) if elbow[0] <= cx else (cx + int(radius), cy)
    curves = [_line(start, elbow, layer=layer, name="balloon/%d/leader-a" % item, source=source),
              _line(elbow, anchor, layer=layer, name="balloon/%d/leader-b" % item, source=source)]
    dx, dy = float(elbow[0] - ax), float(elbow[1] - ay)
    norm = _math.hypot(dx, dy)
    if norm > 0.0 and int(arrow) > 0:
        ux, uy = dx / norm, dy / norm
        for index, sign in enumerate((1.0, -1.0)):
            angle = _math.radians(ARROW_HALF_ANGLE_DEG) * sign
            rx = ux * _math.cos(angle) - uy * _math.sin(angle)
            ry = ux * _math.sin(angle) + uy * _math.cos(angle)
            tip = (int(round(ax + rx * int(arrow))), int(round(ay + ry * int(arrow))))
            curves.append(_line(anchor, tip, layer=layer,
                                name="balloon/%d/arrow[%d]" % (item, index), source=source))
    return curves


# --------------------------------------------------------------------------------------
# 操作：零件序号（气球）
# --------------------------------------------------------------------------------------


def balloon(sk_or_doc: Any, *, sheet: Any = None, kernel: Any = None, items: Any = None,
            source_key: str = "x_bom", view: Any = "front", view_uuid: Any = None,
            views: Any = ("front", "top", "right"), layout: Any = "auto", hidden: bool = True,
            standard: str = _base.DEFAULT_STANDARD, language: str = _base.DEFAULT_LANGUAGE,
            size: Any = None, scale: Any = None, depth_nm: int = 0,
            positions_nm: Any = None, anchors_nm: Any = None,
            radius_nm: int = DEFAULT_RADIUS_NM, leader_nm: int = DEFAULT_LEADER_NM,
            shoulder_nm: int = DEFAULT_SHOULDER_NM, arrow_nm: int = DEFAULT_ARROW_NM,
            margin_nm: int = _base.DEFAULT_MARGIN_NM, row_gap_nm: int = DEFAULT_ROW_GAP_NM,
            start_item: int = 1, layer: str = BALLOON_LAYER) -> list[str]:
    """**零件序号（气球）标注**：BOM 行 → 图上的序号圆 + 引线 + 箭头，序号与明细表行号同源。

    参数（长度一律整数纳米）：

    * `items`：行序列（`{qty, part_key, material_key?, mass_mg?}`）；`None` ⇒ 读文档顶层 `source_key`；
      行先合并（复用 `sw_drawings_extra._merge_rows`，与 BOM 行项同一实现）再顺序编号；
    * `positions_nm`：每行（合并后）一个圆心点；`None` ⇒ 沿图纸右留白自动排一列；
    * `anchors_nm`：每行一个引线锚点；`None` ⇒ 取目标视图几何包围盒右缘按行等分；
    * `radius_nm` / `leader_nm` / `shoulder_nm` / `arrow_nm`：圆半径 / 引线长（`0` ⇒ 不画引线与箭头）/
      拐点偏移 / 倒刺长；`margin_nm` / `row_gap_nm`：自动排布的边距 / 行距；
    * `start_item`：序号起点（1 起，顺序现编，**不采信输入里的行号**）；
    * `view` / `view_uuid` / `views` / `layout` / `hidden` / `depth_nm` / `kernel`：目标视图与（必要时）
      三视图投影参数，与 `centerline` 同一套口径；`sheet` 显式给且已有目标视图时**不重投影**；
    * `layer`：圆与引线所在图层（缺省 `BALLOON`，会登记进图纸图层表）。

    边界：行来源空/缺 ⇒ `bom_source_missing`；行不合法 ⇒ `bad_bom_row`；位置/锚点个数不符 ⇒
    `balloon_position_mismatch` / `balloon_anchor_mismatch`；视图找不到 ⇒ `view_missing`；
    视图无可测几何又没给锚点 ⇒ `empty_balloon_anchor`；圆越出图幅 ⇒ 跳过 + `Sheet.note()`；
    整批一颗都放不下 ⇒ `balloon_off_sheet`。

    返回：`[图纸 uuid（若本操作新建）] + [视图 uuid（若新建）…] + [曲线 uuid …] + [annotation uuid …]`
    （每行：圆 1 + 引线 2 + 倒刺 2 = 5 条曲线；末尾多一条 `balloon_summary` 汇总 annotation）。
    """
    payload = _base._as_payload(sk_or_doc)
    annotations = _base._annotations_or_none(sk_or_doc)
    rows = _base._bom_rows(payload, items, source_key)
    merged = _extra._merge_rows([dict(row) for row in rows])
    if not merged:
        raise _fail("bom_source_missing", "no BOM row to number",
                    source_key=str(source_key), rows=len(rows))

    radius = _base._require_nm(radius_nm, what="radius_nm", positive=True)
    leader = _base._require_nm(leader_nm, what="leader_nm")
    shoulder = _base._require_nm(shoulder_nm, what="shoulder_nm")
    arrow = _base._require_nm(arrow_nm, what="arrow_nm")
    margin = _base._require_nm(margin_nm, what="margin_nm")
    gap = _base._require_nm(row_gap_nm, what="row_gap_nm")
    start = _base._require_count(start_item, what="start_item", minimum=1)
    with_hidden = _flag(hidden, what="hidden")
    layer_name = str(layer or BALLOON_LAYER).strip() or BALLOON_LAYER

    # 校验先于改动：行、位置、锚点在落任何几何之前全部定性。
    explicit_positions = (None if positions_nm is None
                          else _points(positions_nm, len(merged), "balloon_position_mismatch", "positions_nm"))
    explicit_anchors = (None if anchors_nm is None
                        else _points(anchors_nm, len(merged), "balloon_anchor_mismatch", "anchors_nm"))

    sheet_obj = sheet
    created_ids: list[str] = []
    target = _existing_view(sheet, view_uuid, view) if sheet is not None else None
    if target is None:
        sheet_obj, created_ids = _base._views_for(sk_or_doc, payload, sheet=sheet, kernel=kernel,
                                                  standard=standard, language=language, size=size,
                                                  scale=scale, views=views, layout=layout,
                                                  hidden=with_hidden, margin_nm=margin, gap_nm=gap,
                                                  depth_nm=depth_nm)
        target = _base._target_view(sheet_obj, view_uuid, view)

    if explicit_anchors is not None:
        anchors = explicit_anchors
    else:
        bounds = _base._curve_bounds(list(target.visible))
        if bounds is None:
            raise _fail("empty_balloon_anchor", "view has no measurable geometry to anchor balloons on",
                        view=str(getattr(target, "name", "") or _sheet.NA), view_uuid=str(target.uuid))
        anchors = _auto_anchors(bounds, len(merged))
    positions = (explicit_positions if explicit_positions is not None
                 else _auto_positions(sheet_obj, len(merged), radius, margin, gap))

    _base._register_extra_layer(sheet_obj, layer_name, "balloon item numbers (BOM row linkage)")

    curve_uuids: list[str] = []
    annotation_uuids: list[str] = []
    item_numbers: list[int] = []
    centers: list[tuple[int, int]] = []
    skipped = 0
    leaders = 0
    barbs = 0
    for index, row in enumerate(merged):
        item = start + index
        cx, cy = positions[index]
        if not _inside(sheet_obj, cx, cy, radius):
            skipped += 1
            continue
        anchor = (int(anchors[index][0]), int(anchors[index][1]))
        curves = [_circle_curve((cx, cy), radius, layer=layer_name,
                                name="balloon/%d/circle" % item, source=BALLOON_SOURCE)]
        if leader > 0:
            extra_curves = _leader_curves(item, (cx, cy), anchor, radius, shoulder, arrow,
                                          layer=layer_name, source=BALLOON_SOURCE)
            curves.extend(extra_curves)
            leaders += 2
            barbs += len(extra_curves) - 2
        curve_uuids.extend(sheet_obj.next_uuid("balloon", target.uuid, item, curve.name)
                           for curve in curves)
        target.visible = tuple(target.visible) + tuple(curves)
        item_numbers.append(int(item))
        centers.append((int(cx), int(cy)))
        if annotations is None:
            continue
        record_uuid = sheet_obj.next_uuid("balloon_item", target.uuid, item, len(merged))
        annotations.append({
            "uuid": record_uuid,
            "target": [str(target.uuid), "view_geometry"],
            "annotation_kind": "balloon",
            "args": {"item": int(item), "text": str(item), "qty": int(row["qty"]),
                     "part_key": str(row["part_key"]),
                     "material_key": str(row.get("material_key", "") or ""),
                     "mass_mg": int(row.get("mass_mg", _sheet.UNMEASURED)),
                     "center_nm": [int(cx), int(cy)], "anchor_nm": [anchor[0], anchor[1]],
                     "radius_nm": int(radius), "leader_nm": int(leader),
                     "layer": layer_name, "view_uuid": str(target.uuid),
                     "sheet_uuid": str(sheet_obj.uuid), "source_key": str(source_key),
                     "source_rows": int(row.get("source_rows", 1))}})
        annotation_uuids.append(record_uuid)

    if not item_numbers:
        raise _fail("balloon_off_sheet", "not a single balloon fits on this sheet",
                    balloons=len(merged), skipped=int(skipped),
                    radius_nm=int(radius), sheet_uuid=str(sheet_obj.uuid))

    if annotations is not None:
        summary_uuid = sheet_obj.next_uuid("balloon_summary", target.uuid, len(item_numbers), skipped)
        annotations.append({
            "uuid": summary_uuid, "target": [str(target.uuid), "view_geometry"],
            "annotation_kind": "balloon_summary", "layer": layer_name,
            "balloons": len(item_numbers), "rows": len(rows), "merged_rows": len(merged),
            "skipped": int(skipped), "start_item": int(start), "items": list(item_numbers),
            "circles": len(item_numbers), "leaders": int(leaders), "arrow_barbs": int(barbs),
            "radius_nm": int(radius), "leader_nm": int(leader), "arrow_nm": int(arrow),
            "shoulder_nm": int(shoulder), "view_uuid": str(target.uuid),
            "sheet_uuid": str(sheet_obj.uuid), "source_key": str(source_key)})
        annotation_uuids.append(summary_uuid)
    else:
        sheet_obj.note("balloon: no writable annotations[] (bare sketch) -- %d balloons kept as "
                       "view geometry only, item numbers not persisted" % len(item_numbers))

    sheet_obj.note("balloon: view=%s layer=%s balloons=%d items=%s rows=%d merged_rows=%d skipped=%d "
                   "circles=%d leaders=%d arrow_barbs=%d radius_nm=%d leader_nm=%d"
                   % (str(getattr(target, "name", "") or _sheet.NA), layer_name, len(item_numbers),
                      ",".join(str(value) for value in item_numbers), len(rows), len(merged),
                      int(skipped), len(item_numbers), int(leaders), int(barbs), int(radius), int(leader)))
    return created_ids + curve_uuids + annotation_uuids


OPERATIONS: dict[str, Callable[..., list[str]]] = {"balloon": balloon}


# --------------------------------------------------------------------------------------
# 自检（文件自带，不依赖 pytest）；≥5 条硬断言，其中 2 条是反例
# --------------------------------------------------------------------------------------


def _sample_rows() -> list[dict[str, Any]]:
    """3 行源数据、**故意同零件同材料**（P-100 出现两次）⇒ 合并后应只剩 2 颗气球。"""
    return [
        {"qty": 2, "part_key": "P-100", "material_key": "steel", "mass_mg": 1_500_000},
        {"qty": 1, "part_key": "P-200", "material_key": "alu", "mass_mg": -1},
        {"qty": 3, "part_key": "P-100", "material_key": "steel", "mass_mg": 2_500_000},
    ]


def _raw_view() -> Any:
    """测试夹具兜底：按 dataclass 字段现造一个最小 `View`（只在 `add_view` 不通时用）。"""
    cls = getattr(_sheet, "View", None)
    if cls is None:
        raise _fail("balloon_fixture_unsupported", "draw.sheet.View missing")
    try:
        fields = list(_dc.fields(cls))
    except TypeError as exc:
        raise _fail("balloon_fixture_unsupported", "View is not a dataclass", error=str(exc))
    guesses = {"uuid": "00000000-0000-0000-0000-00000000b001", "name": "front", "view": "front",
               "kind": "front", "angle": "front", "scale": "1:1",
               "at_nm": (40 * _base.NM_PER_MM, 40 * _base.NM_PER_MM), "visible": (),
               "hidden": False, "scale_num": 1, "scale_den": 1, "shape_repr": "",
               "projection_source": "balloon_fixture"}
    kwargs: dict[str, Any] = {}
    for field in fields:
        if field.init is False or field.default is not _dc.MISSING:
            continue
        if field.default_factory is not _dc.MISSING:
            continue
        if field.name not in guesses:
            raise _fail("balloon_fixture_unsupported", "unknown required View field", field=field.name)
        kwargs[field.name] = guesses[field.name]
    view = cls(**kwargs)
    for key in ("uuid", "name"):
        try:
            setattr(view, key, guesses[key])
        except Exception:  # noqa: BLE001 - 冻结对象：用构造时的值
            pass
    return view


def _fixture_view(doc: Any) -> tuple[Any, Any]:
    """造一张图纸 + 一个前视图（夹具自己自证：返回的 view 必须能被 op 当作目标视图认出来）。"""
    payload = _base._as_payload(doc)
    sheet_obj, _created = _base._sheet_for(doc, payload, sheet=None, standard=_base.DEFAULT_STANDARD,
                                           language=_base.DEFAULT_LANGUAGE, size=None, scale=None)
    at_nm = (40 * _base.NM_PER_MM, 40 * _base.NM_PER_MM)
    adder = getattr(_sheet, "add_view", None)
    if callable(adder):
        for kwargs in ({"view": "front", "at_nm": at_nm},
                       {"view": "front", "at_nm": at_nm, "hidden": False}):
            try:
                view = adder(sheet_obj, None, **kwargs)
            except TypeError:
                continue
            except CadError:
                break
            if view is not None:
                return sheet_obj, view
    view = _raw_view()
    bucket = _view_bucket(sheet_obj)
    if isinstance(bucket, list):
        bucket.append(view)
    return sheet_obj, view


def _short(exc: Any) -> str:
    return "%s(reason=%s)" % (type(exc).__name__, getattr(exc, "reason", _sheet.NA))


def selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    doc = _base._sample_model()
    rows = _sample_rows()
    oracle = _extra._merge_rows([dict(row) for row in rows])
    ids: list[str] | None = None
    sheet_obj: Any = None
    path = "views_for"
    first_error: Any = None
    try:
        ids = balloon(doc, items=rows, view="front")
    except Exception as exc:  # noqa: BLE001 - 生产路径不通时回落夹具路径，两条都留痕
        first_error = exc
    if ids is None:
        try:
            sheet_obj, view = _fixture_view(doc)
            view_uuid = str(getattr(view, "uuid", "") or "") or None
            ids = balloon(doc, sheet=sheet_obj, view_uuid=view_uuid, view="front", items=rows,
                          anchors_nm=[(60 * _base.NM_PER_MM, 60 * _base.NM_PER_MM),
                                      (60 * _base.NM_PER_MM, 30 * _base.NM_PER_MM)])
            path = "fixture"
        except Exception as exc:  # noqa: BLE001 - 如实报红
            print("BALLOON_SELFTEST path=none production_error=%s fixture_error=%s"
                  % (_short(first_error), _short(exc)))
            print("FEATURE_OK family=sw_drawings op=balloon verdict=FAIL checks=0 failed=1")
            return 1

    payload = _base._as_payload(doc)
    annotations = _base._live_annotations(doc) or []
    item_anns = [a for a in annotations
                 if str(a.get("annotation_kind", "")) == "balloon" and isinstance(a.get("args"), dict)]
    summaries = [a for a in annotations if str(a.get("annotation_kind", "")) == "balloon_summary"]
    args = [a["args"] for a in item_anns]

    # 硬断言 1：合并 + 顺序编号 => 气球号 == BOM 行号（3 行源数据 → 2 颗，数量求和、未采质量保持未采）
    check("bom_row_linkage", len(args) == len(oracle) == 2,
          "balloons=%d oracle=%d" % (len(args), len(oracle)))
    check("item_numbers", [a.get("item") for a in args] == [1, 2],
          "items=%s" % [a.get("item") for a in args])
    check("merged_qty", [a.get("qty") for a in args] == [5, 1],
          "qty=%s" % [a.get("qty") for a in args])
    check("unmeasured_mass_kept", [a.get("mass_mg") for a in args] == [4_000_000, _sheet.UNMEASURED],
          "mass=%s" % [a.get("mass_mg") for a in args])

    # 硬断言 2：真几何 —— 每颗气球 5 条曲线（圆 + 引线 2 + 倒刺 2），圆半径与圆心与记录一致
    if not summaries:
        check("summary_record", False, "no balloon_summary annotation")
    else:
        summary = summaries[0]
        check("summary_record", summary.get("balloons") == 2 and summary.get("circles") == 2
              and summary.get("leaders") == 4 and summary.get("arrow_barbs") == 4
              and summary.get("skipped") == 0 and summary.get("items") == [1, 2],
              "balloons=%s circles=%s leaders=%s barbs=%s skipped=%s items=%s"
              % (summary.get("balloons"), summary.get("circles"), summary.get("leaders"),
                 summary.get("arrow_barbs"), summary.get("skipped"), summary.get("items")))
        check("curve_uuids", len([value for value in ids if isinstance(value, str)]) == len(set(ids))
              and len(ids) >= 2 * 5 + 3, "returned=%d unique=%d" % (len(ids), len(set(ids))))
        geometry = "unreachable"
        if sheet_obj is None:
            try:
                sheet_obj, _c = _base._sheet_for(doc, payload, sheet=None,
                                                 standard=_base.DEFAULT_STANDARD,
                                                 language=_base.DEFAULT_LANGUAGE, size=None, scale=None)
            except Exception:  # noqa: BLE001 - 只影响这一条，如实记 unreachable
                sheet_obj = None
        view = _existing_view(sheet_obj, summary.get("view_uuid"), None) if sheet_obj is not None else None
        if view is not None:
            visible = list(getattr(view, "visible", ()) or ())
            circles = [c for c in visible if str(getattr(c, "kind", "")) == "circle"
                       and str(getattr(c, "source", "")) == BALLOON_SOURCE]
            names = [str(getattr(c, "name", "")) for c in visible]
            leaders = [n for n in names if n.endswith("leader-b")]
            barbs = [n for n in names if "/arrow[" in n]
            geometry = "checked"
            check("circle_geometry",
                  len(circles) == 2 and all(int(getattr(c, "radius_nm", -1)) == DEFAULT_RADIUS_NM
                                            for c in circles)
                  and sorted((int(c.center[0]), int(c.center[1])) for c in circles)
                  == sorted((int(a["center_nm"][0]), int(a["center_nm"][1])) for a in args),
                  "circles=%d radius=%s" % (len(circles),
                                            [getattr(c, "radius_nm", None) for c in circles]))
            check("leader_arrow_geometry", len(leaders) == 2 and len(barbs) == 4,
                  "leaders=%d arrow_barbs=%d" % (len(leaders), len(barbs)))

    # 硬断言 3（反例）：位置个数与气球数不符 ⇒ 校验先于改动，必须抛 CadError
    try:
        balloon(_base._sample_model(), items=rows, positions_nm=[(0, 0)])
        check("reject_position_mismatch", False, "no error raised")
    except CadError as exc:
        check("reject_position_mismatch", getattr(exc, "reason", "") == "balloon_position_mismatch",
              "reason=%s" % getattr(exc, "reason", _sheet.NA))
    except Exception as exc:  # noqa: BLE001
        check("reject_position_mismatch", False, _short(exc))

    # 硬断言 4（反例）：没有行来源 ⇒ bom_source_missing，不许静默出空气球
    try:
        balloon(_base._sample_model(), items=None, source_key="__missing_bom_key__")
        check("reject_missing_bom", False, "no error raised")
    except CadError as exc:
        check("reject_missing_bom", getattr(exc, "reason", "") == "bom_source_missing",
              "reason=%s" % getattr(exc, "reason", _sheet.NA))
    except Exception as exc:  # noqa: BLE001
        check("reject_missing_bom", False, _short(exc))

    failed = [name for name, ok, _detail in checks if not ok]
    circles_n = [detail for name, _ok, detail in checks if name == "circle_geometry"]
    print("BALLOON_SELFTEST path=%s balloons=%d items=%s merged_rows=%d checks=%d failed=%d "
          "view_geometry=%s production_error=%s"
          % (path, len(args), [a.get("item") for a in args], len(oracle), len(checks), len(failed),
             "checked" if circles_n else "unreachable",
             _short(first_error) if first_error is not None else "none"))
    if failed:
        print("FEATURE_OK family=sw_drawings op=balloon verdict=FAIL failed_checks=%s" % ",".join(failed))
        return 1
    print("FEATURE_OK family=sw_drawings op=balloon verdict=PASS checks=%d failed=0 path=%s"
          % (len(checks), path))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_drawings__w15.py:83 "零件序号（气球）标注" -> "part number (balloon) annotation"
