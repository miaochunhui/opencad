#!/usr/bin/env python3
"""output 族 · 第 2 号仍红 must_have：视口比例设定与锁定（Viewport Scale & Lock）。

冻结接口（族片契约）：
    FAMILY = "output"
    OPERATIONS: dict[str, Callable]
    每个操作 `op(sk_or_doc, **kwargs) -> list[str]`（返回被改动的 uuid 列表）；
    长度一律**整数纳米**；错误一律抛 `cadcore.errors.CadError(reason=...)`。

── ① 该族仍 missing/partial 的 must_have（清单顺序取自
`docs/research/cad-parity/features/autocad-output.json` 的 must_have；状态取自
`docs/research/cad-parity/status/autocad-output.json`；末条 DXF 已 implemented 故剔除，余 18 条编号 0..17）：

    0  图纸空间布局（Layout / Paper Space）                partial
    1  视口创建与排布（Viewport）                           partial
    2  视口比例设定与锁定（Viewport Scale & Lock）★ 本片    partial
    3  视口内图层替代（VP Freeze / Layer Override）         partial
    4  页面设置（Page Setup）                               partial
    5  打印主命令 PLOT                                      partial
    6  打印到 PDF（DWG To PDF.pc3）                         partial
    7  绘图仪/打印机配置（.pc3）                            missing
    8  打印样式表 CTB/STB                                   partial
    9  线宽与打印线宽（LWEIGHT）                            partial
    10 打印区域选择                                         partial
    11 打印比例与布满图纸                                   partial
    12 打印预览（PREVIEW）                                  partial
    13 图层与对象的可打印控制（Plot / No Plot）             partial
    14 模型空间直接出图                                     partial
    15 布局管理（新建/复制/重命名/删除）                    partial
    16 批量发布多布局出图（PUBLISH）                        partial
    17 DWG 读写与版本选择                                   missing
    18 DXF 导入导出与精度（DXFOOT/DXFIN）= implemented，不计入仍红清单

── ② 本片负责第 2 号；③ 在 L1 文档模型上真实现的能力（依据 status 第 2 条 note 列的三处缺口
「无标准比例列表 / 无属性选项板标准比例 / 注释比例；locked 目前只是数据字段，没有锁定强制」）：

  * 标准比例列表 `STANDARD_SCALES`（公制档 1:n 与 n:1），比例语义 = 纸上长度 : 模型长度；
  * `set_viewport_scale_locked`：比例解析（`n:m` / `n/m` / 小数，非法 ⇒ `parse_failed`）、
    非标准档 `snap=True` 就近吸附 / `snap=False` 显式 `non_standard_scale` 拒绝、
    `fit_to_paper=True` 取**能装下的最大标准档**（真几何选档）、注释比例（`auto` 跟随视口比例）；
  * **真几何后果**（全部整数纳米，余数单独记账，绝不静默取整）：
    `paper_span_nm = model_span_nm × 比例`；与图纸可绘区（`width_nm/height_nm − 2×margin_nm`）
    逐边比对，装不下 ⇒ `does_not_fit_paper`（**写之前**就抛，不留半写）；
  * **锁定强制**（不是数据字段）：锁定态改比例 ⇒ `scale_locked`；锁定态缩放 ⇒ `scale_locked`；
    解锁后缩放，**视口纸面框是不变量**、模型窗口与比例随之改变（1:100 放大 2× ⇒ 1:50、模型窗口减半）；
  * `audit_viewport_scales`：一致性审计（非标比例 / 纸面足尺陈旧 / 锁定但无比例 / 注释比例失配），
    逐视口写回 `scale_audit` 读数，返回违规 uuid 列表（合法 ⇒ 空表）。

── ④ 如实说明（不是桩，是本片不做，L1 也装不下）：
  * 无 AutoCAD 属性选项板 / 命令行 UI、无比例列表图形界面（接口层与 UI 不属本席写面）；
  * 注释比例只是每视口一个比例文本字段 + 跟随标志，**不是**注释对象/注释缩放对象；
  * 无 .pc3 / CTB-STB 关联（那些是第 7/8 条，本片不代做）；
  * 本片**不改** `tools/cad/parity_output_gate.py`（不属本席写面）⇒ 族门仍红是预期的，
    本片只保证本片 OPERATIONS 自测 PASS，绝不按绿记账。

自测（≥2 条硬断言，实为 17 条）覆盖：真值比例与足尺、锁定拒绝 + 零副作用、解锁缩放不变量、
非标拒绝/吸附、装不下拒绝、fit_to_paper 选档、审计检出与合法空表。
运行：`python3 cadcore/ops/features/output__w11.py` ⇒ 末行 `FEATURE_OK ... verdict=PASS`。
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from fractions import Fraction
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑自测：脚本目录是 cadcore/ops/features，需把包根塞进 sys.path
    _PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if _PKG_ROOT not in sys.path:
        sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError

__all__ = ["FAMILY", "OPERATIONS", "STANDARD_SCALES", "set_viewport_scale_locked",
           "zoom_viewport", "audit_viewport_scales"]

FAMILY = "output"

# AutoCAD 标准比例列表（公制档：常用 1:n 与放大 n:1）。比例 = 纸上长度 : 模型长度。
STANDARD_SCALES: tuple[str, ...] = (
    "1:1", "1:2", "1:4", "1:5", "1:10", "1:20", "1:25", "1:50", "1:100",
    "1:200", "1:250", "1:500", "1:1000", "1:2000", "1:5000", "1:10000",
    "2:1", "4:1", "5:1", "10:1",
)
_STANDARD: dict[str, Fraction] = {
    text: Fraction(int(text.split(":")[0]), int(text.split(":")[1])) for text in STANDARD_SCALES
}

_VIEW_WINDOW_KEYS = ("model_window_nm", "window_nm", "model_bounds_nm", "bounds_model_nm")
_VIEW_PAPER_KEYS = ("bounds_sheet_nm", "paper_bounds_nm", "frame_sheet_nm")


# ── 基础工具（L1 既可能是 Mapping（payload）也可能是 Document/View 对象，两侧都认） ──
def _field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _put(obj: Any, **fields: Any) -> None:
    for key, value in fields.items():
        if isinstance(obj, MutableMapping):
            obj[key] = value
        else:
            setattr(obj, key, value)


def _bad(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_box(value: Any) -> tuple[int, int, int, int] | None:
    """接受 4 元序列，或 {x0,y0,x1,y1} / {min_x,min_y,max_x,max_y} 映射。"""
    if isinstance(value, Mapping):
        keys = ("x0", "y0", "x1", "y1") if "x0" in value else ("min_x", "min_y", "max_x", "max_y")
        if all(key in value for key in keys):
            value = [value[key] for key in keys]
        else:
            return None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 4:
        items = [_as_int(item) for item in value]
        if all(item is not None for item in items):
            x0, y0, x1, y1 = (int(item) for item in items)  # type: ignore[misc]
            if x1 > x0 and y1 > y0:
                return (x0, y0, x1, y1)
    return None


def _parse_scale(spec: Any) -> Fraction:
    """解析比例/倍率：`1:100`、`1/100`、`0.01`、`2`、`0.5`。非法 ⇒ parse_failed（不静默回落 1:1）。"""
    if isinstance(spec, bool) or spec is None:
        raise _bad("parse_failed", "scale must be a positive ratio", scale=repr(spec))
    if isinstance(spec, Fraction):
        ratio = spec
    elif isinstance(spec, int):
        ratio = Fraction(spec, 1)
    elif isinstance(spec, float):
        try:
            ratio = Fraction(str(spec))
        except (ValueError, ZeroDivisionError) as exc:
            raise _bad("parse_failed", "scale is not a usable number", scale=repr(spec)) from exc
    else:
        text = str(spec).strip()
        try:
            if ":" in text:
                left, _, right = text.partition(":")
                ratio = Fraction(int(left.strip()), int(right.strip()))
            else:
                ratio = Fraction(text)
        except (ValueError, ZeroDivisionError) as exc:
            raise _bad("parse_failed", "scale must look like n:m (paper:model)",
                       scale=text, detail=str(exc)) from exc
    if ratio <= 0:
        raise _bad("parse_failed", "scale must be positive", scale=str(spec))
    return ratio


def _ratio_text(ratio: Fraction) -> str:
    return "%d:%d" % (ratio.numerator, ratio.denominator)


def _nearest_standard(ratio: Fraction) -> Fraction:
    ref = math.log(float(ratio))
    return min(_STANDARD.values(),
               key=lambda item: (abs(math.log(float(item)) - ref), abs(item - ratio)))


def _largest_standard_fitting(limit: Fraction) -> Fraction | None:
    fitting = [item for item in _STANDARD.values() if item <= limit]
    return max(fitting) if fitting else None


def _sheets_of(doc: Any) -> list[Any]:
    sheets = _field(doc, "sheets", None)
    if not isinstance(sheets, list):
        raise _bad("l1_document_required",
                   "output scale ops need an L1 document exposing sheets[].views[]",
                   got=type(doc).__name__)
    if not sheets:
        raise _bad("unknown_layout", "document has no layout")
    return sheets


def _resolve(doc: Any, sheet_uuid: Any, view_uuid: Any) -> tuple[Any, Any]:
    sheets = _sheets_of(doc)
    if sheet_uuid is None:
        sheet = sheets[-1]
    else:
        target = str(sheet_uuid)
        sheet = next((item for item in sheets if str(_field(item, "uuid", "")) == target), None)
        if sheet is None:
            raise _bad("unknown_layout", "no sheet matches sheet_uuid", sheet_uuid=target)
    views = _field(sheet, "views", None)
    if not isinstance(views, list) or not views:
        raise _bad("viewport_not_found", "layout has no viewport",
                   sheet_uuid=str(_field(sheet, "uuid", "")))
    if view_uuid is None:
        return sheet, views[-1]
    target = str(view_uuid)
    view = next((item for item in views if str(_field(item, "uuid", "")) == target), None)
    if view is None:
        raise _bad("viewport_not_found", "no viewport matches view_uuid", view_uuid=target)
    return sheet, view


def _view_uuid(view: Any) -> str:
    uuid = _field(view, "uuid", None)
    if not isinstance(uuid, str) or not uuid:
        raise _bad("view_identity_missing", "viewport has no uuid; refusing to mutate an anonymous view")
    return uuid


def _current_ratio(view: Any) -> Fraction | None:
    text = _field(view, "scale_text", None)
    if isinstance(text, str) and text.strip():
        try:
            return _parse_scale(text)
        except CadError:
            pass
    raw = _field(view, "scale", None)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and len(raw) == 2:
        try:
            return _parse_scale("%d:%d" % (int(raw[0]), int(raw[1])))
        except (CadError, TypeError, ValueError):
            return None
    if raw is None:
        return None
    try:
        return _parse_scale(raw)
    except CadError:
        return None


def _model_box(view: Any) -> tuple[int, int, int, int]:
    """模型窗口（nm）：显式窗口 → span/height → 由纸面框按比例反算。都给不出 ⇒ 显式报错。"""
    for key in _VIEW_WINDOW_KEYS:
        box = _as_box(_field(view, key, None))
        if box is not None:
            return box
    span = _as_int(_field(view, "model_span_nm", None))
    height = _as_int(_field(view, "model_height_nm", None))
    if height is None:
        height = _as_int(_field(view, "height_nm", None))
    if span and height and span > 0 and height > 0:
        return (0, 0, span, height)
    ratio = _current_ratio(view)
    if ratio is not None:
        for key in _VIEW_PAPER_KEYS:
            paper = _as_box(_field(view, key, None))
            if paper is None:
                continue
            width = int(Fraction(paper[2] - paper[0]) / ratio)
            tall = int(Fraction(paper[3] - paper[1]) / ratio)
            if width > 0 and tall > 0:
                return (0, 0, width, tall)
    raise _bad("view_geometry_missing",
               "viewport has neither a model window nor an invertible paper frame",
               view_uuid=str(_field(view, "uuid", "")))


def _drawable_nm(sheet: Any) -> tuple[int, int] | None:
    width = _as_int(_field(sheet, "width_nm", None))
    height = _as_int(_field(sheet, "height_nm", None))
    if not width or not height or width <= 0 or height <= 0:
        return None
    margin = _field(sheet, "margin_nm", 0)
    if isinstance(margin, Sequence) and not isinstance(margin, (str, bytes)) and margin:
        mx = _as_int(margin[0]) or 0
        my = _as_int(margin[1]) if len(margin) > 1 else mx
        my = my or 0
    else:
        mx = _as_int(margin) or 0
        my = mx
    usable_w = width - 2 * max(mx, 0)
    usable_h = height - 2 * max(my, 0)
    if usable_w <= 0 or usable_h <= 0:
        return None
    return usable_w, usable_h


def _paper_span_nm(model_w: int, model_h: int, ratio: Fraction) -> tuple[int, int, int, int]:
    """纸上足尺 = 模型尺寸 × 比例（整数纳米；除不尽的余数单独返回，绝不静默抹平）。"""
    width = Fraction(model_w) * ratio
    height = Fraction(model_h) * ratio
    qw, rw = divmod(width.numerator, width.denominator)
    qh, rh = divmod(height.numerator, height.denominator)
    return int(qw), int(qh), int(rw), int(rh)


# ── 操作 1：设定视口比例 + 锁定（真值 + 真几何 + 锁定强制） ──
def set_viewport_scale_locked(sk_or_doc: Any, *, sheet_uuid: Any = None, view_uuid: Any = None,
                              scale: Any = "1:1", locked: bool = True, snap: bool = True,
                              allow_custom: bool = False, fit_to_paper: bool = False,
                              annotation_scale: Any = None,
                              override_lock: bool = False) -> list[str]:
    """给布局视口设比例并锁定：返回 `[view_uuid]`。

    真后果：`scale/scale_num/scale_den/scale_text/scale_locked` + 纸上足尺
    `paper_span_nm/paper_height_nm`（含余数）写回 L1；装不下图纸可绘区 ⇒ `does_not_fit_paper`。
    锁定语义：**已锁定且请求比例不同 ⇒ `scale_locked`**（要改先解锁，或显式 `override_lock=True` 并留痕）。
    所有校验都在写之前完成 ⇒ 失败路径零副作用。
    """
    sheet, view = _resolve(sk_or_doc, sheet_uuid, view_uuid)
    uuid = _view_uuid(view)
    box = _model_box(view)
    model_w, model_h = box[2] - box[0], box[3] - box[1]
    current = _current_ratio(view)

    if fit_to_paper:
        drawable = _drawable_nm(sheet)
        if drawable is None:
            raise _bad("paper_size_missing", "fit_to_paper needs sheet width_nm/height_nm",
                       sheet_uuid=str(_field(sheet, "uuid", "")))
        limit = min(Fraction(drawable[0], model_w), Fraction(drawable[1], model_h))
        chosen = _largest_standard_fitting(limit)
        if chosen is None:
            raise _bad("does_not_fit_paper", "model window does not fit even at the smallest standard scale",
                       model_w_nm=model_w, model_h_nm=model_h,
                       drawable_w_nm=drawable[0], drawable_h_nm=drawable[1],
                       smallest_standard=_ratio_text(min(_STANDARD.values())))
        ratio = chosen
    else:
        ratio = _parse_scale(scale)
        text = _ratio_text(ratio)
        if text not in _STANDARD:
            if allow_custom:
                pass
            elif snap:
                ratio = _nearest_standard(ratio)
            else:
                raise _bad("non_standard_scale", "scale is not in the standard scale list",
                           scale=text, standard=list(STANDARD_SCALES))
    ratio_text = _ratio_text(ratio)

    if bool(_field(view, "scale_locked", False)) and not override_lock and current != ratio:
        raise _bad("scale_locked", "viewport scale is locked; unlock it before changing the scale",
                   view_uuid=uuid, current=_ratio_text(current) if current else None,
                   requested=ratio_text)

    paper_w, paper_h, rem_w, rem_h = _paper_span_nm(model_w, model_h, ratio)
    drawable = _drawable_nm(sheet)
    fits = drawable is None or (paper_w <= drawable[0] and paper_h <= drawable[1])
    if not fits:
        raise _bad("does_not_fit_paper", "scaled viewport exceeds the drawable paper area",
                   view_uuid=uuid, scale=ratio_text, paper_span_nm=paper_w, paper_height_nm=paper_h,
                   drawable_w_nm=drawable[0], drawable_h_nm=drawable[1])

    follows = False
    if annotation_scale is None:
        annotation = _field(view, "annotation_scale", None)
        follows = bool(_field(view, "annotation_follows_viewport", False))
    elif isinstance(annotation_scale, str) and annotation_scale.strip().lower() == "auto":
        annotation, follows = ratio_text, True
    else:
        annotation, follows = _ratio_text(_parse_scale(annotation_scale)), False

    _put(view,
         scale=[ratio.numerator, ratio.denominator],
         scale_num=int(ratio.numerator), scale_den=int(ratio.denominator), scale_text=ratio_text,
         scale_locked=bool(locked), scale_standard=ratio_text in _STANDARD,
         model_span_nm=int(model_w), model_height_nm=int(model_h),
         paper_span_nm=int(paper_w), paper_height_nm=int(paper_h),
         paper_span_remainder_nm=int(rem_w), paper_height_remainder_nm=int(rem_h),
         fits_on_paper=bool(fits), annotation_scale=annotation,
         annotation_follows_viewport=bool(follows),
         lock_override_count=int(_field(view, "lock_override_count", 0) or 0) + (1 if override_lock else 0),
         scale_source="set_viewport_scale_locked")
    return [uuid]


# ── 操作 2：视口内缩放（锁定 ⇒ 拒绝；解锁 ⇒ 纸面框不变、模型窗口与比例随动） ──
def zoom_viewport(sk_or_doc: Any, *, sheet_uuid: Any = None, view_uuid: Any = None,
                  factor: Any = 2, force: bool = False) -> list[str]:
    """缩放视口：倍率 `factor`（`2`/`2:1` 放大，`0.5`/`1:2` 缩小）。返回 `[view_uuid]`。

    锁定语义：`scale_locked=True` ⇒ `CadError(reason="scale_locked")`，**文档零改动**
    （锁定视口内缩放会毁掉 1:100 这类比例，这正是 Display Locked 要防的事）。
    解锁语义：视口纸面框 `paper_span_nm` 是**不变量**，模型窗口按新比例反算并保持中心；
    视口比例随之变化（1:100 放大 2× ⇒ 1:50）。`force=True` 只给显式越权用，并计数留痕。
    """
    sheet, view = _resolve(sk_or_doc, sheet_uuid, view_uuid)
    uuid = _view_uuid(view)
    zoom = _parse_scale(factor)
    if bool(_field(view, "scale_locked", False)) and not force:
        raise _bad("scale_locked", "viewport is locked; zooming would destroy the viewport scale",
                   view_uuid=uuid, scale_text=_field(view, "scale_text", None),
                   hint="unlock with set_viewport_scale_locked(..., locked=False)")
    ratio = _current_ratio(view)
    if ratio is None:
        raise _bad("unknown_scale", "viewport has no parsable scale; set one before zooming", view_uuid=uuid)
    box = _model_box(view)
    model_w, model_h = box[2] - box[0], box[3] - box[1]
    paper_w, paper_h, rem_w, rem_h = _paper_span_nm(model_w, model_h, ratio)
    new_ratio = ratio * zoom
    shrink_w = Fraction(paper_w) / new_ratio
    shrink_h = Fraction(paper_h) / new_ratio
    new_w, _ = divmod(shrink_w.numerator, shrink_w.denominator)
    new_h, _ = divmod(shrink_h.numerator, shrink_h.denominator)
    if new_w <= 0 or new_h <= 0:
        raise _bad("zoom_out_of_range", "zoom would collapse the model window",
                   view_uuid=uuid, factor=_ratio_text(zoom))
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    x0, y0 = cx - int(new_w) // 2, cy - int(new_h) // 2
    new_text = _ratio_text(new_ratio)
    old_text = _ratio_text(ratio)
    annotation = _field(view, "annotation_scale", None)
    if bool(_field(view, "annotation_follows_viewport", False)) and annotation == old_text:
        annotation = new_text
    _put(view,
         model_window_nm=[x0, y0, x0 + int(new_w), y0 + int(new_h)],
         model_span_nm=int(new_w), model_height_nm=int(new_h),
         scale=[new_ratio.numerator, new_ratio.denominator],
         scale_num=int(new_ratio.numerator), scale_den=int(new_ratio.denominator),
         scale_text=new_text, scale_standard=new_text in _STANDARD, annotation_scale=annotation,
         paper_span_nm=int(paper_w), paper_height_nm=int(paper_h),
         paper_span_remainder_nm=int(rem_w), paper_height_remainder_nm=int(rem_h),
         zoom_steps=int(_field(view, "zoom_steps", 0) or 0) + 1, last_zoom_factor=_ratio_text(zoom),
         scale_source="zoom_viewport")
    return [uuid]


# ── 操作 3：比例一致性审计（读 + 写回读数；返回违规 uuid 列表） ──
def audit_viewport_scales(sk_or_doc: Any, *, sheet_uuid: Any = None,
                          apply_fix: bool = False) -> list[str]:
    """审计布局视口比例：返回**违规**视口 uuid 列表（全合法 ⇒ 空表）。

    检查项：`scale_missing` / `non_standard_scale` / `paper_span_stale` / `paper_height_stale` /
    `annotation_scale_mismatch`（仅在 `annotation_follows_viewport=True` 时算违规）/
    `lock_without_scale`。每个被审视口写回 `scale_audit` 读数；`apply_fix=True` 时把陈旧足尺改正。
    """
    sheets = _sheets_of(sk_or_doc)
    if sheet_uuid is not None:
        target = str(sheet_uuid)
        sheets = [item for item in sheets if str(_field(item, "uuid", "")) == target]
        if not sheets:
            raise _bad("unknown_layout", "no sheet matches sheet_uuid", sheet_uuid=target)
    offenders: list[str] = []
    inspected = 0
    for sheet in sheets:
        for view in list(_field(sheet, "views", None) or []):
            inspected += 1
            issues: list[str] = []
            try:
                ratio = _parse_scale(_field(view, "scale_text", None))
            except CadError:
                ratio = None
                issues.append("scale_missing")
            expected_w: int | None = None
            expected_h: int | None = None
            if ratio is not None:
                if _ratio_text(ratio) not in _STANDARD:
                    issues.append("non_standard_scale")
                try:
                    box = _model_box(view)
                    expected_w, expected_h, exp_rw, exp_rh = _paper_span_nm(
                        box[2] - box[0], box[3] - box[1], ratio)
                except CadError as exc:
                    issues.append("geometry_missing:%s" % exc.reason)
                    expected_w = expected_h = exp_rw = exp_rh = None
                if expected_w is not None:
                    stored_w = _as_int(_field(view, "paper_span_nm", None))
                    stored_h = _as_int(_field(view, "paper_height_nm", None))
                    if stored_w is not None and stored_w != expected_w:
                        issues.append("paper_span_stale")
                    if stored_h is not None and stored_h != expected_h:
                        issues.append("paper_height_stale")
                    if apply_fix:
                        _put(view, paper_span_nm=expected_w, paper_height_nm=expected_h,
                             paper_span_remainder_nm=exp_rw, paper_height_remainder_nm=exp_rh,
                             scale_audit_fixed=True)
            annotation = _field(view, "annotation_scale", None)
            if (bool(_field(view, "annotation_follows_viewport", False))
                    and isinstance(annotation, str) and ratio is not None
                    and annotation.strip() != _ratio_text(ratio)):
                issues.append("annotation_scale_mismatch")
            if bool(_field(view, "scale_locked", False)) and ratio is None:
                issues.append("lock_without_scale")
            _put(view, scale_audit={"checked": True, "issues": list(issues),
                                    "expected_paper_span_nm": expected_w,
                                    "expected_paper_height_nm": expected_h,
                                    "applied_fix": bool(apply_fix and issues)})
            if issues:
                offenders.append(str(_field(view, "uuid", "")))
    if inspected == 0:
        raise _bad("viewport_not_found", "no viewport to audit",
                   sheet_uuid=None if sheet_uuid is None else str(sheet_uuid))
    return offenders


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "set_viewport_scale_locked": set_viewport_scale_locked,
    "zoom_viewport": zoom_viewport,
    "audit_viewport_scales": audit_viewport_scales,
}


# ── 自测（硬断言；失败不静默） ──
class _Checker:
    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: Any, detail: str = "") -> bool:
        self.total += 1
        if not ok:
            self.failures.append("%s (%s)" % (name, detail) if detail else name)
        return bool(ok)

    def expect_error(self, name: str, reason: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        try:
            func(*args, **kwargs)
        except CadError as exc:
            return self.check(name, exc.reason == reason, "reason=%s want=%s" % (exc.reason, reason))
        except Exception as exc:  # noqa: BLE001 - 自测要把非契约异常也照实报红
            return self.check(name, False, "wrong exception %s: %s" % (type(exc).__name__, exc))
        return self.check(name, False, "no error raised, want reason=%s" % reason)


def _fixture_document() -> dict[str, Any]:
    """A4 横放（297×210mm，边距 10mm ⇒ 可绘区 277×190mm）+ 一张 12m×8m 的模型窗口视口。"""
    return {
        "schema_version": 1,
        "unit": "mm",
        "sheets": [{
            "uuid": "11111111-1111-5111-8111-111111111111",
            "name": "Layout1", "kind": "sheet",
            "width_nm": 297_000_000, "height_nm": 210_000_000, "margin_nm": 10_000_000,
            "views": [{
                "uuid": "22222222-2222-5222-8222-222222222222",
                "kind": "viewport", "name": "Viewport1",
                "model_window_nm": [0, 0, 12_000_000_000, 8_000_000_000],
                "scale_text": "1:1", "scale_locked": False,
            }],
        }],
    }


def _canonical(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, default=str)


def selftest() -> _Checker:
    chk = _Checker()
    doc = _fixture_document()
    view_uuid = doc["sheets"][0]["views"][0]["uuid"]
    setter = OPERATIONS["set_viewport_scale_locked"]
    zoomer = OPERATIONS["zoom_viewport"]
    auditor = OPERATIONS["audit_viewport_scales"]

    # A1 真值比例 + 真几何：12m 模型窗口 @1:100 ⇒ 纸上 120mm，余数 0
    got = setter(doc, scale="1:100", locked=True)
    view = doc["sheets"][0]["views"][0]
    chk.check("A1a_returns_view_uuid", got == [view_uuid], "got=%r" % (got,))
    chk.check("A1b_scale_text_is_exact_ratio", view["scale_text"] == "1:100" and view["scale"] == [1, 100],
              "text=%r scale=%r" % (view.get("scale_text"), view.get("scale")))
    chk.check("A1c_paper_span_exact_120mm", view["paper_span_nm"] == 120_000_000
              and view["paper_span_remainder_nm"] == 0 and view["paper_height_nm"] == 80_000_000,
              "w=%r rem=%r h=%r" % (view.get("paper_span_nm"), view.get("paper_span_remainder_nm"),
                                    view.get("paper_height_nm")))
    chk.check("A1d_locked_flag_and_fits", view["scale_locked"] is True and view["fits_on_paper"] is True)

    # A2 锁定强制：锁定态缩放被拒，且文档逐字节零改动（无半写）
    before = _canonical(doc)
    chk.expect_error("A2a_locked_zoom_refused", "scale_locked", zoomer, doc, factor=2)
    chk.check("A2b_locked_zoom_no_partial_write", _canonical(doc) == before)
    chk.expect_error("A2c_locked_rescale_refused", "scale_locked", setter, doc, scale="1:50", locked=True)

    # A3 解锁后缩放：纸面框不变、模型窗口减半、比例随动 1:100 ⇒ 1:50
    chk.check("A3a_unlock_ok", setter(doc, scale="1:100", locked=False) == [view_uuid])
    zoomer(doc, factor=2)
    chk.check("A3b_zoom_scale_becomes_1_50", view["scale_text"] == "1:50", "text=%r" % (view.get("scale_text"),))
    chk.check("A3c_model_window_halved", view["model_span_nm"] == 6_000_000_000
              and view["model_window_nm"][2] - view["model_window_nm"][0] == 6_000_000_000,
              "span=%r" % (view.get("model_span_nm"),))
    chk.check("A3d_paper_frame_invariant", view["paper_span_nm"] == 120_000_000
              and view["paper_height_nm"] == 80_000_000)

    # A4 非标比例：不吸附 ⇒ non_standard_scale；吸附 ⇒ 就近 1:99 → 1:100
    doc2 = _fixture_document()
    chk.expect_error("A4a_non_standard_refused", "non_standard_scale", setter, doc2,
                     scale="1:99", snap=False)
    chk.check("A4b_non_standard_reject_leaves_view_untouched",
              _canonical(doc2) == _canonical(_fixture_document()))
    setter(doc2, scale="1:99", snap=True)
    chk.check("A4c_snap_to_nearest_standard", doc2["sheets"][0]["views"][0]["scale_text"] == "1:100",
              "text=%r" % (doc2["sheets"][0]["views"][0].get("scale_text"),))

    # A5 装不下：12m @1:10 = 1200mm > 可绘区 277mm ⇒ 写之前就拒
    doc3 = _fixture_document()
    chk.expect_error("A5a_does_not_fit_paper", "does_not_fit_paper", setter, doc3, scale="1:10")
    chk.check("A5b_overflow_leaves_view_untouched",
              _canonical(doc3) == _canonical(_fixture_document()))

    # A6 fit_to_paper：真几何选档 ⇒ 限值 min(277/12000, 190/8000)=0.02308 ⇒ 最大标准档 1:50
    doc4 = _fixture_document()
    setter(doc4, fit_to_paper=True)
    chk.check("A6_fit_to_paper_picks_1_50", doc4["sheets"][0]["views"][0]["scale_text"] == "1:50",
              "text=%r" % (doc4["sheets"][0]["views"][0].get("scale_text"),))

    # A7 审计：合法 ⇒ 空表；把足尺做旧 + 改成非标 ⇒ 两条违规 + 读数落盘
    doc5 = _fixture_document()
    setter(doc5, scale="1:100", locked=True, annotation_scale="auto")
    chk.check("A7a_clean_audit_empty", auditor(doc5) == [])
    view5 = doc5["sheets"][0]["views"][0]
    view5["scale_text"] = "1:99"
    view5["paper_span_nm"] = 1
    view5["annotation_scale"] = "1:1"
    bad = auditor(doc5)
    issues = view5["scale_audit"]["issues"]
    chk.check("A7b_audit_flags_view", bad == [view_uuid], "bad=%r" % (bad,))
    chk.check("A7c_audit_issue_kinds",
              {"non_standard_scale", "paper_span_stale", "annotation_scale_mismatch"} <= set(issues),
              "issues=%r" % (issues,))
    chk.check("A7d_audit_apply_fix_rewrites_span", auditor(doc5, apply_fix=True) is not None
              and view5["paper_span_nm"] == int(Fraction(12_000_000_000) * Fraction(99, 100)),
              "span=%r" % (view5.get("paper_span_nm"),))

    # A8 契约面：FAMILY / OPERATIONS 形状 + 非法比例 reason
    chk.check("A8a_contract_shape", FAMILY == "output" and set(OPERATIONS) ==
              {"set_viewport_scale_locked", "zoom_viewport", "audit_viewport_scales"}
              and all(callable(item) for item in OPERATIONS.values()))
    chk.expect_error("A8b_bad_scale_parse_failed", "parse_failed", setter, _fixture_document(), scale="fit")
    return chk


def main(argv: Sequence[str] | None = None) -> int:
    chk = selftest()
    failed = len(chk.failures)
    print("SELFTEST asserts=%d passed=%d failed=%d%s" % (
        chk.total, chk.total - failed, failed,
        "" if not failed else " failures=" + "; ".join(chk.failures)))
    if failed:
        print("FEATURE_OK family=output op=set_viewport_scale_locked verdict=FAIL")
        return 1
    print("FEATURE_OK family=output op=set_viewport_scale_locked verdict=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
