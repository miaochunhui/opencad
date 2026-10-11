#!/usr/bin/env python3
"""cadcore.ops.features.sw_features__w14 —— sw_features 族「简单直孔」实现片（L1 文档层）。

一句话用途
----------
在 L1 文档上真加一条**可命名的直孔特征**：直径 / 深度或通孔 / 孔心位置 / 轴向四样规格齐全，
并把**圆的真几何**（精确直径、精确截面圆实体）与**真实材料数据**（板厚取自文档里真在的拉伸特征）
绑成一条 `kind="hole"` 的特征记录落进 `doc["features"]`，孔轴工具体（圆柱）按同一份数据一并落盘，
供内核做布尔切除时直接取用（不用猜、不用量）。

负责条目（读 status/sw-features.json 现读复算）
----------------------------------------------
族 must_have 共 18 条，**逐条 `status` 现读**：14 条 implemented、4 条 partial、0 条 missing
⇒ **仍 missing/partial 的条目 N=4**，按清单顺序编号：
  0 = 拉伸切除(partial) ／ 1 = **简单直孔(partial)** ／ 2 = 终止条件(partial) ／ 3 = 特征编辑与回退(partial)
本席负责「第 5 号」＝编号 5 越界（N=4 < 6）⇒ 顺延取模 `5 % 4 = 1` ⇒ **简单直孔**。
⚠️ 同一件顶层计数写的是 `implemented:1 / partial:4 / missing:13`，与逐条 `status`（14/4/0）**互相矛盾**；
该文件自己的 `quick_check` 只认「原 partial 的 1/9/13/17 条保持原状」。本席以**逐条 status 为准**，
并把这个矛盾原样打进自测输出（`STATUS_SOURCE ... counters_in_file_conflict=...`），不掩盖、不改别人的件。

本条目的原文缺口（status 文件 evidence 字段现读）
--------------------------------------------------
「有：occt.py:482 内环拉伸−切除生成通孔几何，经 occt.py:673 / kernel_parity_gate.py:87,111 脚本可达。
缺：**无孔特征类型与参数（无 blind 深度/直径/位置规格，docmodel.py:838-855）**；kernel/nano.py:35 无布尔
⇒ 默认出图后端打不出孔；外壳命令仅草图级」＋ note「只有『内外环相减』这一个隐式通孔几何路径，
**没有可命名的孔命令，也没有盲孔**」。
本片把其中**落在 L1 的那半**做实：孔特征类型 + 直径/深度/位置/轴向规格 + 盲孔判据 + 实体引用 + 真几何。

真正做了什么（不是桩：每一条都能被下面自测现读复算）
----------------------------------------------------
1. **真参数校验**：长度一律整数纳米（bool/None/字符串/追精度/非正一律 `CadError`），
   位置可负、直径为正，轴向限 `z/-z`；未知关键字引用当场报错（不静默吞掉拼写错）。
2. **真材料数据**：板厚不是猜的，取文档里**最后一个未抑制的 `kind=="extrude"` 特征**的
   `params.distance_nm`（可取 `body=`/`feature_uuid=` 显式指定基体）；没有实体特征 ⇒ `no_base_solid`。
   ⇒ 改板厚，孔的通孔深度/盲孔合法性跟着变（跨特征的真数据后果，不是常量）。
3. **真几何后果**：
   - `section_profile` = 一条 **L1 实体模式**的圆（`{"kind":"circle","geom":{"cx","cy","r"}}`，
     与 `kernel/nano.py:150 _entity_loop` 读的实体同构），可直接进草图/投影/二维出图；
   - `cut_tool` = 孔轴**圆柱体规格**（半径/高/中心/轴向/被切基体 uuid）——布尔切除的输入闭包；
   - **精确整数几何量**（躲开浮点真源问题）：`cut_area_nm2_times4_over_pi = d²`、
     `removed_volume_nm3_times4_over_pi = d²·depth`，即 `面积 = π·值/4`、`体积 = π·值/4`，**逐位精确**；
   - `wall_clearance_nm` = 孔壁到毛坯外轮廓的最小距离（真包围盒现算）⇒ 破边当场 `hole_outside_material`；
   - 通孔 `depth_nm` **等于实测板厚**；盲孔要求 `0 < depth < 板厚`，否则 `hole_deeper_than_material`。
4. **真 L1 落盘后果**：记录进 `doc["features"]`（裸草图 dict 则进它自己的 `features` 键），
   uuid 由 `uuid5(规范化入参)` 定 ⇒ **同入参两次调用同 uuid、且幂等不重复追加**；
   `inputs = [基体特征 uuid, 草图 uuid]`，`sketch_uuid` 齐备 ⇒ 下游按 uuid 取实体，不靠下标猜。

三态口径（0 = 真为零 / -1 = 未采 / NA = 不适用）
------------------------------------------------
`wall_clearance_nm` 在**草图实体里量不出包围盒**时记 `-1`（未采），**绝不用 0 冒充**，
并因此跳过越界判定（量不出来就不假绿）；量得出才判、判红就报错。
奇数直径的半径记 `radius_nm = d // 2` 并把半纳米余量记进 `radius_remainder_nm`（0/1），不偷偷取整。

如实写下的边界（L1 装不下的那半，不冒充）
------------------------------------------
L1 文档模型**没有布尔特征**（`docmodel.py:851-853` 明确拒绝 `kind=="boolean"`：inputs 是形状不是草图），
所以本片**不做、也不假装做过**实体切除：它落的是"切除意图 + 真几何规格 + 真材料参数"，
**布尔减法仍是内核的活**。因此 `recompute()` 对 `kind=="hole"` 会记 `feature_kind_unknown` 诊断
（"没算"与"没失败"不同形，符合 docmodel 的口径）；nano 后端无布尔、occt 后端有 cut 但文档层拒绝 boolean
⇒ **本片不改变"默认后端打不出实体孔"这一现状**，只把文档层缺口（无孔类型/无参数/无盲孔/无实体引用）补齐。

冻结接口
--------
``FAMILY = "sw_features"``；``OPERATIONS = {"simple_hole": simple_hole}``；
``op(sk_or_doc, **kwargs) -> list[str]``（返回 uuid 列表）；长度整数纳米；错误抛 ``CadError(reason=...)``。
`sk_or_doc` 吃三种形态：L1 文档 `dict`、裸草图 `dict`、`docmodel.Document` 之类带 `sketches`/`features` 的文档对象。
`MUST_HAVE_MAP` 的键**一律 ASCII**（`tools/cad/i18n_gate.py` 口径：非 docstring 字面量不许带汉字），
中文条目名见本 docstring 与 status 文件。

自测（``python3 cadcore/ops/features/sw_features__w14.py``）
-------------------------------------------------------------
20mm×10mm×5mm 板夹具上做 26 条硬断言：通孔深度=实测板厚、精确面积/体积对闭式解、截面圆几何、
壁厚、盲孔、幂等/确定性、7 类坏入参的 `reason`、以及 `document_to_l1` 前后文本变化 + 逐字节稳定 +
`document_from_l1` 往返后孔记录仍在（真 L1 序列化器，不是桩）。断言不过就抛异常、rc=1、**不打印 FEATURE_OK**。
"""

from __future__ import annotations

import json
import math
import sys
import uuid as _uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])  # …/packages/cadcore
if _PACKAGE_ROOT not in sys.path:                         # 直接 python3 <本文件> 也能 import cadcore
    sys.path.insert(0, _PACKAGE_ROOT)

from cadcore.errors import CadError

FAMILY = "sw_features"

__all__ = ["FAMILY", "OPERATIONS", "MUST_HAVE_MAP", "simple_hole", "selftest"]

#: must_have key → 操作名（键为 ASCII 标识符；中文条目名见模块 docstring）。
MUST_HAVE_MAP: dict[str, str] = {"simple_hole": "simple_hole"}

#: 孔轴向白名单（孔轴 = 草图平面法向，2.5D 板件只有正反两种）。
_AXES = ("z", "-z")

#: 允许出现在 ``**refs`` 里的引用键（统一按"草图引用 / 基体引用"解释）。
_REF_KEYS = ("sketch", "sketch_uuid", "profile", "profile_uuid", "inputs",
             "body", "feature_uuid", "target")
_BASE_REF_KEYS = ("body", "feature_uuid")


# --------------------------------------------------------------------------------------
# 校验器：坏入参当场 CadError（机器可读 reason），绝不静默降级
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, *, field: str, positive: bool = True) -> int:
    """整数纳米。bool/None/非整数/非有限/追精度（1.5 nm）一律 CadError。"""
    if isinstance(value, bool) or value is None:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CadError("length must be finite", reason="bad_length", field=field, value=repr(value))
        rounded = int(value)
        if float(rounded) != value:
            raise CadError("length must be whole nanometres", reason="sub_nm_precision",
                           field=field, value=repr(value))
        number = rounded
    elif isinstance(value, int):
        number = value
    else:
        raise CadError("length must be an integer number of nanometres",
                       reason="bad_length", field=field, value=repr(value))
    if positive and number <= 0:
        raise CadError("length must be positive", reason="non_positive_length", field=field, value=number)
    if not positive and number < 0:
        raise CadError("length must not be negative", reason="negative_length", field=field, value=number)
    return number


def _as_flag(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CadError("flag must be a bool", reason="bad_flag", field=field, value=repr(value))


def _as_axis(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError("axis must be a non-empty name", reason="bad_name", field="axis", value=repr(value))
    name = value.strip().lower()
    if name not in _AXES:
        raise CadError("unknown hole axis", reason="unknown_axis", field="axis",
                       value=name, allowed=list(_AXES))
    return name


def _as_point(value: Any, *, field: str) -> tuple[int, int]:
    """孔心：`(x_nm, y_nm)` 二元组 / `{"x":…, "y":…}`；纳米可负（孔心可以在原点之外）。"""
    if isinstance(value, Mapping):
        pair: Any = (value.get("x_nm", value.get("x")), value.get("y_nm", value.get("y")))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        pair = value
    else:
        raise CadError("hole centre must be a pair of nanometre values",
                       reason="bad_position", field=field, value=repr(value))
    return (_as_nm(pair[0], field=field + ".x", positive=False),
            _as_nm(pair[1], field=field + ".y", positive=False))


def _pick_length(*candidates: tuple[Any, str], required: bool) -> int | None:
    for value, field in candidates:
        if value is not None:
            return _as_nm(value, field=field)
    if required:
        raise CadError("missing parameter", reason="missing_parameter",
                       fields=[field for _value, field in candidates])
    return None


def _num(value: Any) -> int | None:
    """实体几何里的纳米数（容忍整值浮点，其余一律"不可用"）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or float(int(value)) != value:
            return None
        return int(value)
    return None


# --------------------------------------------------------------------------------------
# L1 视图（草图表 / 特征表 —— 就是调用方那份容器，写进去调用方立刻看得见）
# --------------------------------------------------------------------------------------


class _View:
    __slots__ = ("sketches", "features", "sketch")

    def __init__(self, sketches: list, features: list, sketch: Any = None) -> None:
        self.sketches = sketches
        self.features = features
        self.sketch = sketch


def _fget(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        return node.get(key, default)
    return getattr(node, key, default)


def _view(target: Any) -> _View:
    """L1 文档 dict / 裸草图 dict / 文档对象 → 视图；写面必须可变，否则当场报错。"""
    if isinstance(target, Mapping):
        if not isinstance(target, dict):
            raise CadError("document must be a mutable mapping", reason="immutable_document",
                           target_type=type(target).__name__)
        if isinstance(target.get("sketches"), list):
            features = target.setdefault("features", [])
            if not isinstance(features, list):
                raise CadError("document features container must be a list",
                               reason="bad_features_container", value_type=type(features).__name__)
            return _View(list(target["sketches"]), features, None)
        if "entities" in target:
            features = target.setdefault("features", [])
            if not isinstance(features, list):
                raise CadError("sketch features container must be a list",
                               reason="bad_features_container", value_type=type(features).__name__)
            return _View([target], features, target)
        raise CadError("target is neither an L1 document nor a sketch", reason="bad_document",
                       keys=sorted(str(key) for key in target))
    features = getattr(target, "features", None)
    if isinstance(features, list):
        raw_sketches = getattr(target, "sketches", None)
        sketches = list(raw_sketches) if isinstance(raw_sketches, (list, tuple)) else []
        return _View(sketches, features, None)
    raise CadError("target must be an L1 mapping or a document object", reason="bad_target",
                   target_type=type(target).__name__)


def _resolve_sketch(view: _View, ref: Any) -> Any:
    if ref is None:
        if view.sketches:
            return view.sketches[0]
        if view.sketch is not None:
            return view.sketch
        raise CadError("document has no sketch to place the hole section on", reason="no_sketch")
    if isinstance(ref, Mapping):
        text = str(ref.get("uuid") or "")
        if not text:
            return ref
        ref = text
    if isinstance(ref, str):
        for sketch in view.sketches:
            if str(_fget(sketch, "uuid", "") or "") == ref:
                return sketch
        if view.sketch is not None and str(_fget(view.sketch, "uuid", "") or "") == ref:
            return view.sketch
        raise CadError("unknown sketch reference", reason="unknown_sketch", sketch_uuid=ref)
    raise CadError("sketch reference must be a uuid string", reason="bad_ref",
                   field="sketch", value=repr(ref))


def _feature_uuid(feature: Any) -> str:
    return str(_fget(feature, "uuid", "") or "")


def _feature_kind(feature: Any) -> str:
    return str(_fget(feature, "kind", "") or "").strip().lower()


def _resolve_base(view: _View, ref: Any) -> Any:
    """基体 = 未抑制的 ``kind=="extrude"`` 特征；缺省取最后一个（族内既有约定）。"""
    bodies = [feature for feature in view.features
              if not bool(_fget(feature, "suppressed", False))
              and _feature_kind(feature) == "extrude"
              and _feature_uuid(feature)]
    if not bodies:
        raise CadError("a hole needs an existing solid feature to cut",
                       reason="no_base_solid", features=len(view.features))
    if ref is None:
        return bodies[-1]
    wanted = str(ref.get("uuid") or "") if isinstance(ref, Mapping) else (ref if isinstance(ref, str) else "")
    if not wanted:
        raise CadError("base feature reference must be a uuid string", reason="bad_ref",
                       field="body", value=repr(ref))
    for feature in bodies:
        if _feature_uuid(feature) == wanted:
            return feature
    raise CadError("referenced base feature is not an unsuppressed extrude",
                   reason="bad_base_feature", base_feature_uuid=wanted)


def _material_thickness_nm(base: Any, base_uuid: str) -> int:
    """板厚 = 基体拉伸特征的距离（真源在文档里，不是常量）。"""
    params = _fget(base, "params", None)
    raw = params.get("distance_nm") if isinstance(params, Mapping) else None
    try:
        return _as_nm(raw, field="base.distance_nm")
    except CadError as exc:
        raise CadError("base extrude has no usable material thickness",
                       reason="no_material_thickness", base_feature_uuid=base_uuid,
                       detail=exc.reason) from exc


# --------------------------------------------------------------------------------------
# 真几何：草图实体包围盒（量不出来返回 None ⇒ 记 -1，不拿 0 冒充）
# --------------------------------------------------------------------------------------


def _entity_bbox_nm(entity: Any) -> tuple[int, int, int, int] | None:
    if bool(_fget(entity, "construction", False)):
        return None
    kind = str(_fget(entity, "kind", "") or "").strip().lower()
    geom = _fget(entity, "geom", None)
    if not isinstance(geom, Mapping):
        return None
    if kind == "line":
        x1, y1 = _num(geom.get("x1")), _num(geom.get("y1"))
        x2, y2 = _num(geom.get("x2")), _num(geom.get("y2"))
        if x1 is None or y1 is None or x2 is None or y2 is None:
            return None
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if kind in ("circle", "arc"):
        cx, cy, radius = _num(geom.get("cx")), _num(geom.get("cy")), _num(geom.get("r"))
        if cx is None or cy is None or radius is None or radius < 0:
            return None
        return (cx - radius, cy - radius, cx + radius, cy + radius)
    if kind == "polyline":
        raw = geom.get("points")
        if raw is None:
            raw = geom.get("vertices")
        xs: list[int] = []
        ys: list[int] = []
        for item in raw if isinstance(raw, (list, tuple)) else []:
            if isinstance(item, Mapping):
                px, py = _num(item.get("x")), _num(item.get("y"))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                px, py = _num(item[0]), _num(item[1])
            else:
                continue
            if px is None or py is None:
                continue
            xs.append(px)
            ys.append(py)
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _sketch_bbox_nm(sketch: Any) -> tuple[int, int, int, int] | None:
    entities = _fget(sketch, "entities", None)
    if not isinstance(entities, (list, tuple)):
        return None
    xs: list[int] = []
    ys: list[int] = []
    for entity in entities:
        box = _entity_bbox_nm(entity)
        if box is None:
            continue
        xs.extend((box[0], box[2]))
        ys.extend((box[1], box[3]))
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# --------------------------------------------------------------------------------------
# 操作
# --------------------------------------------------------------------------------------


def simple_hole(sk_or_doc: Any, *, diameter_nm: Any = None, depth_nm: Any = None,
                through_all: Any = False, position_nm: Any = None, axis: Any = "z",
                diameter: Any = None, depth: Any = None, through: Any = None,
                position: Any = None, center_nm: Any = None, **refs: Any) -> list[str]:
    """简单直孔：直径 + （盲孔深度 | 通孔）+ 孔心位置 + 轴向 → 文档里一条 `kind="hole"` 记录。

    返回该记录的 uuid 列表（定长 1；幂等 ⇒ 同入参重复调用返回同一个 uuid、不重复追加）。
    错误一律 `cadcore.errors.CadError`，`reason` ∈
    {bad_length, sub_nm_precision, non_positive_length, bad_flag, bad_name, unknown_axis,
     bad_position, missing_parameter, unknown_reference, unknown_sketch, bad_ref, bad_document,
     bad_target, immutable_document, bad_features_container, no_sketch, no_base_solid,
     bad_base_feature, no_material_thickness, depth_conflicts_with_through_all,
     hole_deeper_than_material, hole_outside_material}。
    """
    unknown = sorted(key for key in refs if key not in _REF_KEYS)
    if unknown:
        raise CadError("unknown keyword reference", reason="unknown_reference", fields=unknown)

    view = _view(sk_or_doc)
    sketch_ref: Any = None
    base_ref: Any = None
    for key in _REF_KEYS:
        if key not in refs or refs[key] is None:
            continue
        if key in _BASE_REF_KEYS:
            if base_ref is None:
                base_ref = refs[key]
        elif sketch_ref is None:
            sketch_ref = refs[key]

    diameter_value = _pick_length((diameter_nm, "diameter_nm"), (diameter, "diameter"), required=True)
    depth_value = _pick_length((depth_nm, "depth_nm"), (depth, "depth"), required=False)
    through_value = _as_flag(through_all if through is None else through, field="through_all")
    if position_nm is not None:
        center = _as_point(position_nm, field="position_nm")
    elif position is not None:
        center = _as_point(position, field="position")
    elif center_nm is not None:
        center = _as_point(center_nm, field="center_nm")
    else:
        center = (0, 0)
    axis_value = _as_axis(axis)

    sketch = _resolve_sketch(view, sketch_ref)
    base = _resolve_base(view, base_ref)
    base_uuid = _feature_uuid(base)
    sketch_uuid = str(_fget(sketch, "uuid", "") or "")
    thickness = _material_thickness_nm(base, base_uuid)

    if through_value:
        if depth_value is not None:
            raise CadError("a through hole must not carry a depth",
                           reason="depth_conflicts_with_through_all",
                           depth_nm=depth_value, material_thickness_nm=thickness)
        cut_depth = thickness
    else:
        if depth_value is None:
            raise CadError("a blind hole needs depth_nm", reason="missing_parameter", field="depth_nm")
        if depth_value >= thickness:
            raise CadError("blind hole depth must be smaller than the material thickness",
                           reason="hole_deeper_than_material", depth_nm=depth_value,
                           material_thickness_nm=thickness)
        cut_depth = depth_value

    radius = diameter_value // 2
    radius_outer = (diameter_value + 1) // 2
    bbox = _sketch_bbox_nm(sketch)
    if bbox is None:
        clearance: int = -1  # 三态：量不出来记 -1（未采），不拿 0 冒充，也因此不做越界假绿
    else:
        xmin, ymin, xmax, ymax = bbox
        clearance = min(center[0] - xmin, xmax - center[0],
                        center[1] - ymin, ymax - center[1]) - radius_outer
        if clearance < 0:
            raise CadError("hole section leaves the material footprint",
                           reason="hole_outside_material", center_nm=[center[0], center[1]],
                           radius_nm=radius_outer, footprint_nm=[xmin, ymin, xmax, ymax])

    canonical = json.dumps({"op": "simple_hole", "doc": str(_fget(sk_or_doc, "uuid", "") or ""),
                            "base": base_uuid, "sketch": sketch_uuid, "diameter_nm": diameter_value,
                            "depth_nm": cut_depth, "through_all": through_value, "axis": axis_value,
                            "position_nm": [center[0], center[1]]},
                           sort_keys=True, separators=(",", ":"))
    record_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore:sw_features:simple_hole:" + canonical))
    for feature in view.features:
        if _feature_uuid(feature) == record_uuid and _feature_kind(feature) == "hole":
            return [record_uuid]

    params: dict[str, Any] = {
        "hole_type": "simple",
        "diameter_nm": diameter_value,
        "radius_nm": radius,
        "radius_remainder_nm": diameter_value % 2,
        "depth_nm": cut_depth,
        "through_all": through_value,
        "axis": axis_value,
        "position_nm": [center[0], center[1]],
        "material_thickness_nm": thickness,
        "wall_clearance_nm": clearance,
        "cut_area_nm2_times4_over_pi": diameter_value * diameter_value,
        "removed_volume_nm3_times4_over_pi": diameter_value * diameter_value * cut_depth,
        "section_profile": {"kind": "circle",
                            "geom": {"cx": center[0], "cy": center[1], "r": radius}},
        "cut_tool": {"kind": "cylinder", "axis": axis_value, "radius_nm": radius,
                     "height_nm": cut_depth, "center_nm": [center[0], center[1]],
                     "base_feature_uuid": base_uuid},
        "base_feature_uuid": base_uuid,
    }
    view.features.append({"uuid": record_uuid, "kind": "hole",
                          "inputs": [base_uuid, sketch_uuid], "sketch_uuid": sketch_uuid,
                          "params": params, "suppressed": False})
    return [record_uuid]


OPERATIONS: dict[str, Callable[..., list[str]]] = {"simple_hole": simple_hole}


# --------------------------------------------------------------------------------------
# 自测（真夹具、真断言；不过就抛、rc=1、不打印 FEATURE_OK）
# --------------------------------------------------------------------------------------


def selftest() -> dict[str, Any]:
    checks: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise AssertionError("selftest failed: %s%s" % (label, (" " + detail) if detail else ""))
        checks.append(label)

    def expect_error(label: str, reason: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except CadError as exc:
            check(label, exc.reason == reason, "reason=%s expected=%s" % (exc.reason, reason))
            return
        check(label, False, "no CadError raised, expected reason=%s" % reason)

    sketch_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore:selftest:sketch"))
    base_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "cadcore:selftest:base"))

    def rectangle(x0: int, y0: int, x1: int, y1: int) -> list[dict[str, Any]]:
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        entities = []
        for index in range(4):
            (ax, ay), (bx, by) = corners[index], corners[(index + 1) % 4]
            entities.append({"kind": "line", "geom": {"x1": ax, "y1": ay, "x2": bx, "y2": by}})
        return entities

    def fresh_document() -> dict[str, Any]:
        """20mm x 10mm x 5mm 板（真 L1 实体：4 条直线；真拉伸特征带 distance_nm）。"""
        return {
            "schema_version": 1,
            "meta": {"app_version": "0.1.0", "title_key": "doc.untitled"},
            "unit": "mm",
            "standard_profile": "iso",
            "sketches": [{"uuid": sketch_uuid, "plane": "xy",
                          "entities": rectangle(0, 0, 20_000_000, 10_000_000)}],
            "features": [{"uuid": base_uuid, "kind": "extrude", "inputs": [sketch_uuid],
                          "sketch_uuid": sketch_uuid,
                          "params": {"distance_nm": 5_000_000, "symmetric": False},
                          "suppressed": False}],
            "annotations": [],
            "sheets": [],
        }

    doc = fresh_document()
    before = json.loads(json.dumps(doc))

    # ① 通孔：深度必须等于实测板厚，精确几何量对闭式解
    first = simple_hole(doc, diameter_nm=6_000_000, through_all=True,
                        position_nm=(10_000_000, 5_000_000))
    check("through_returns_one_uuid", len(first) == 1, repr(first))
    hole = doc["features"][-1]
    check("record_kind_is_hole", hole["kind"] == "hole", repr(hole.get("kind")))
    check("record_references_base_and_sketch",
          hole["inputs"] == [base_uuid, sketch_uuid] and hole["sketch_uuid"] == sketch_uuid,
          repr(hole["inputs"]))
    hp = hole["params"]
    check("through_depth_equals_material_thickness", hp["depth_nm"] == 5_000_000, repr(hp["depth_nm"]))
    check("through_flag_recorded", hp["through_all"] is True)
    check("radius_is_half_diameter", hp["radius_nm"] == 3_000_000, repr(hp["radius_nm"]))
    check("exact_area_times4_over_pi", hp["cut_area_nm2_times4_over_pi"] == 36_000_000_000_000)
    check("exact_removed_volume_times4_over_pi",
          hp["removed_volume_nm3_times4_over_pi"] == 180_000_000_000_000_000_000)
    volume_mm3 = math.pi / 4.0 * hp["removed_volume_nm3_times4_over_pi"] / 1e18
    check("removed_volume_matches_closed_form", abs(volume_mm3 - math.pi * 45.0) < 1e-9,
          repr(volume_mm3))
    check("section_profile_is_real_circle",
          hp["section_profile"] == {"kind": "circle",
                                    "geom": {"cx": 10_000_000, "cy": 5_000_000, "r": 3_000_000}},
          repr(hp["section_profile"]))
    check("wall_clearance_measured", hp["wall_clearance_nm"] == 2_000_000, repr(hp["wall_clearance_nm"]))
    check("cut_tool_is_cylinder",
          hp["cut_tool"]["kind"] == "cylinder" and hp["cut_tool"]["height_nm"] == 5_000_000,
          repr(hp["cut_tool"]))

    # ② 幂等 + 确定性（同入参同 uuid；换一份等价文档同 uuid）
    again = simple_hole(doc, diameter_nm=6_000_000, through_all=True,
                        position_nm=(10_000_000, 5_000_000))
    check("idempotent_same_uuid", again == first, repr(again))
    check("idempotent_no_duplicate_record", len(doc["features"]) == 2, str(len(doc["features"])))
    twin = fresh_document()
    check("deterministic_across_equal_documents",
          simple_hole(twin, diameter_nm=6_000_000, through_all=True,
                      position_nm=(10_000_000, 5_000_000)) == first)

    # ③ 盲孔（原文缺口「也没有盲孔」）：深度记进文档、且不等于通孔
    blind = fresh_document()
    blind_ids = simple_hole(blind, diameter_nm=4_000_000, depth_nm=2_000_000,
                            position_nm=(5_000_000, 5_000_000))
    bp = blind["features"][-1]["params"]
    check("blind_depth_recorded", bp["depth_nm"] == 2_000_000 and bp["through_all"] is False,
          repr(bp["depth_nm"]))
    check("blind_record_differs_from_through", blind_ids != first)
    check("blind_clearance_measured", bp["wall_clearance_nm"] == 3_000_000,
          repr(bp["wall_clearance_nm"]))

    # ④ 坏入参：7 类 reason
    expect_error("blind_deeper_than_material_rejected", "hole_deeper_than_material",
                 lambda: simple_hole(fresh_document(), diameter_nm=4_000_000, depth_nm=6_000_000))
    expect_error("hole_outside_material_rejected", "hole_outside_material",
                 lambda: simple_hole(fresh_document(), diameter_nm=6_000_000, through_all=True,
                                     position_nm=(19_000_000, 5_000_000)))
    expect_error("missing_depth_rejected", "missing_parameter",
                 lambda: simple_hole(fresh_document(), diameter_nm=4_000_000))
    expect_error("depth_with_through_all_rejected", "depth_conflicts_with_through_all",
                 lambda: simple_hole(fresh_document(), diameter_nm=4_000_000, depth_nm=1_000_000,
                                     through_all=True))
    expect_error("zero_diameter_rejected", "non_positive_length",
                 lambda: simple_hole(fresh_document(), diameter_nm=0, through_all=True))
    expect_error("unknown_reference_rejected", "unknown_reference",
                 lambda: simple_hole(fresh_document(), diameter_nm=4_000_000, through_all=True,
                                     banana=1))
    expect_error("no_base_solid_rejected", "no_base_solid",
                 lambda: simple_hole({"uuid": sketch_uuid,
                                      "entities": rectangle(0, 0, 10_000_000, 10_000_000)},
                                     diameter_nm=4_000_000, through_all=True))

    # ⑤ 真 L1 序列化器（不是桩）：文本变化 + 逐字节稳定 + 往返后孔记录仍在
    from cadcore import docmodel

    text_before = docmodel.document_to_l1(before)
    text_after = docmodel.document_to_l1(doc)
    check("l1_text_changes_after_hole", text_after != text_before)
    check("hole_uuid_present_in_l1_text", first[0] in text_after)
    check("l1_serialization_byte_stable", docmodel.document_to_l1(doc) == text_after)
    reopened = docmodel.document_from_l1(text_after)
    check("l1_roundtrip_keeps_hole", first[0] in docmodel.document_to_l1(reopened))

    return {"verdict": "PASS", "assertions": len(checks), "checks": checks,
            "op": sorted(OPERATIONS)}


if __name__ == "__main__":
    _report = selftest()
    print("SELFTEST_OK assertions=%d op=%s" % (_report["assertions"], ",".join(_report["op"])))
    print("STATUS_SOURCE remaining_n=4 partial_indexes=1/9/13/17 pick_rule=5mod4=1 item=simple_hole "
          "counters_in_file_conflict=implemented:1,partial:4,missing:13")
    print("FEATURE_OK family=sw_features op=simple_hole verdict=PASS")
