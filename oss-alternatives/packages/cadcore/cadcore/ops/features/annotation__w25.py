#!/usr/bin/env python3
"""annotation 族 feature 片（席 w25）：**线性标注的旋转子型**（DIMLINEAR > Rotated）。

本片补哪一条（清单顺序编号，来源见下）
------------------------------------
* 清单：`docs/research/cad-parity/features/autocad-annotation.json` 的 `must_have`（22 条）。
* 状态：`docs/research/cad-parity/status/autocad-annotation.json`（2026-09-30 12:0x 回写）
  判定 `implemented=18 / partial=4 / missing=0` ⇒ 仍 missing/partial 的 must_have 按清单顺序编号：

      0  线性标注（水平/垂直/旋转）   partial —— 旋转子型缺（无测量方向 / 标注线角度参数）
      1  关联标注、重关联与更新       partial
      2  多行文字                     partial
      3  多重引线                     partial

* 本席对象：第 **16** 号 ⇒ `16 mod 4 = 0` ⇒ **第 0 号**，即线性标注的旋转子型。

实现口径（真几何 / 真数据后果，非桩）
----------------------------------
AutoCAD DIMLINEAR-Rotated 的语义是：给两条尺寸界线的原点，再给尺寸线角度，
**量到的是沿该方向的一维投影**（不是两点直线距离，那是 DIMALIGNED）。
本 op 就按这个语义算：

1. 两个原点 `origin1_nm` / `origin2_nm`（整数纳米对，`bool`/浮点一律拒）；
2. `rotation_deg`（度，实数）定义尺寸线方向 `u=(cosθ, sinθ)`；θ 与 θ+180 等价 ⇒ 归一化到 `[0,180)`；
3. `value_nm = round(|(p2-p1)·u|)`，**由几何现算**（`args.value_source="solved"`），不接受调用方声明值；
4. 被旋转标注**忽略**的垂直分量 `(p2-p1)·n` 单独落 `args.span_perp_nm`（负例证据：同一对点换角度值就变）；
5. 尺寸线两端点 `args.dim_line_nm` 也现算：把两端点投到 `t1 + offset_nm` 那条偏移线上
   （`offset_nm` 是尺寸线相对第一原点的垂直偏移，缺省 0）。

落盘形状沿用族口径：`kind="linear"`（⇒ 与 `annotation.py` 的 `_VALUE_KINDS` 白名单兼容，
`update_value` 仍认这条注解），旋转语义全部放 `args`：
`dim_subkind="rotated"` / `rotation_deg` / `rotation_micro_deg`（整数微度）/ `measure_dir` /
`origins_nm` / `dim_line_nm` / `dim_line_offset_nm` / `span_axis_nm` / `span_perp_nm`。

L1 装得下：文档 `annotations[]` 可直接承载（与族内其余注解节点同形，可选挂 `target` 引用）
⇒ **本片不留 missing、不写 unsupported**。

冻结接口
--------
    FAMILY = "annotation"
    OPERATIONS: dict[str, Callable]      # "add_rotated_dimension" -> 可调用
    op(sk_or_doc, **kwargs) -> list[str] # 返回 [annotation_uuid]
    非法参数 -> cadcore.errors.CadError(reason=<机器可读 token>)；任何调用**先全量校验后写入**，不半写文档。
"""

from __future__ import annotations

import math
import os
import re
import sys
import uuid as _uuid
from collections.abc import Mapping
from typing import Any, Callable

#: 直接 `python3 cadcore/ops/features/annotation__w25.py` 时 sys.path[0] 是脚本目录
#: ⇒ 把包根 `packages/cadcore` 补进 sys.path，脚本方式与 import 方式都能跑通（不依赖 cwd）。
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError  # noqa: E402  (bootstrap 之后才能 import)

__all__ = ["FAMILY", "OPERATIONS", "add_rotated_dimension"]

FAMILY = "annotation"

#: i18n key 形状（与 `annotation.py` / `docmodel._KEY_LIKE_RE` 同源口径）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

#: 一个尺寸线方向只要半圈就够（θ 与 θ+180 量到同一个值）。
_HALF_TURN_DEG = 180.0


# --------------------------------------------------------------------------------------
# 校验器（全部在写入之前跑完）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(message, reason=reason, **details)


def _nm(value: Any, what: str) -> int:
    """长度入参 → 整数纳米（`bool` 与浮点一律拒；本 op 的 `offset_nm` 允许负）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "%s must be an integer number of nanometres" % what)
    return value


def _point(value: Any, what: str) -> list[int]:
    """坐标入参 → `[x_nm, y_nm]`（整数纳米对）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("bad_position", "%s must be an (x_nm, y_nm) pair" % what)
    coords: list[int] = []
    for axis, item in zip(("x", "y"), value):
        if isinstance(item, bool) or not isinstance(item, int):
            raise _fail("bad_position",
                        "%s.%s must be an integer number of nanometres" % (what, axis))
        coords.append(item)
    return coords


def _angle(value: Any, what: str) -> tuple[int, float]:
    """角度入参（度，实数）→ `(整数微度, 归一化到 [0,180) 的度数)`。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_angle", "%s must be a finite number of degrees" % what)
    degrees = float(value)
    if not math.isfinite(degrees):
        raise _fail("invalid_angle", "%s must be finite" % what)
    normalised = math.fmod(degrees, _HALF_TURN_DEG)
    if normalised < 0.0:
        normalised += _HALF_TURN_DEG
    if normalised >= _HALF_TURN_DEG - 1e-12:  # -1e-15 与 180 是同一个方向
        normalised = 0.0
    micro = int(round(normalised * 1000000.0)) % int(_HALF_TURN_DEG * 1000000.0)
    return micro, normalised


def _key(value: Any, what: str) -> str:
    """i18n key 入参 → 合法 key（人话文案不是 key）。"""
    if not isinstance(value, str) or not _KEY_RE.match(value):
        raise _fail("bad_text_key", "%s must be an i18n key (dotted ascii identifier)" % what)
    return value


def _live_annotations(sk_or_doc: Any) -> list:
    """可写的 `annotations[]` 真源那一份（**不是**副本）。"""
    if isinstance(sk_or_doc, Mapping):
        annotations = sk_or_doc.get("annotations")
        if isinstance(annotations, list):
            return annotations
        if "entities" in sk_or_doc:
            raise _fail("annotation_needs_document",
                        "a bare sketch has no annotations[] to write; pass the document")
        raise _fail("bad_document", "document mapping has no annotations list")
    annotations = getattr(sk_or_doc, "annotations", None)
    if isinstance(annotations, list):
        return annotations
    raise _fail("bad_document", "unsupported sk_or_doc: %s" % type(sk_or_doc).__name__)


def _sketch_list(sk_or_doc: Any) -> list:
    """文档里的草图列表（用于解析可选 `target` 引用）。"""
    if isinstance(sk_or_doc, Mapping):
        sketches = sk_or_doc.get("sketches")
        return list(sketches) if isinstance(sketches, list) else []
    sketches = getattr(sk_or_doc, "sketches", None)
    return list(sketches) if isinstance(sketches, list) else []


def _require_target(sk_or_doc: Any, target: Any) -> list[str]:
    """可选引用 `(uuid, sub_shape_name)`：必须解析到实体（悬空 ⇒ `unknown_reference`）。"""
    if not isinstance(target, (list, tuple)) or len(target) != 2:
        raise _fail("bad_reference", "target must be a (uuid, sub_shape_name) pair")
    entity_uuid, name = target
    if not isinstance(entity_uuid, str) or not entity_uuid:
        raise _fail("bad_uuid", "target uuid must be a uuid string")
    try:
        _uuid.UUID(entity_uuid)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _fail("bad_uuid", "target uuid is not a uuid: %r" % (entity_uuid,)) from exc
    if not isinstance(name, str) or not name:
        raise _fail("bad_reference", "target sub-shape name must be a non-empty string")

    from cadcore.sketch.constraints import UnknownReference, find_entity

    for sketch in _sketch_list(sk_or_doc):
        if not isinstance(sketch, Mapping):
            continue
        try:
            found = find_entity(sketch, entity_uuid)
        except UnknownReference:
            found = None
        if found is not None:
            return [entity_uuid, name]
    raise _fail("unknown_reference", "target %s does not resolve to an entity" % entity_uuid)


# --------------------------------------------------------------------------------------
# 操作：线性标注的旋转子型
# --------------------------------------------------------------------------------------


def add_rotated_dimension(sk_or_doc, *, origin1_nm, origin2_nm, rotation_deg,
                          offset_nm=0, text_key=None, target=None):
    """**旋转线性标注**：沿 `rotation_deg` 方向量两原点之间的投影长度，落一条 `kind="linear"` 注解。

    参数
    ----
    origin1_nm, origin2_nm : (x_nm, y_nm) 整数纳米对（两条尺寸界线的原点，必须不同点）。
    rotation_deg           : 尺寸线角度（度，实数；θ 与 θ+180 等价，归一化到 [0,180)）。
    offset_nm              : 尺寸线相对第一原点的垂直偏移（整数纳米，可负，缺省 0）——
                             只改尺寸线位置，不改测量值。
    text_key               : 可选 i18n key（点分 ASCII 标识符）。
    target                 : 可选 `(uuid, sub_shape_name)` 关联引用（悬空 ⇒ `unknown_reference`）。

    返回
    ----
    `[annotation_uuid]`；测量值 `value_nm` 是沿 `rotation_deg` 的投影（几何现算），
    垂直分量被忽略并记进 `args.span_perp_nm`。
    """
    origin1 = _point(origin1_nm, "origin1_nm")
    origin2 = _point(origin2_nm, "origin2_nm")
    if origin1 == origin2:
        raise _fail("degenerate_dimension",
                    "the two dimension origins coincide; a rotated dimension needs a span")
    micro_deg, normalised_deg = _angle(rotation_deg, "rotation_deg")
    offset = _nm(offset_nm, "offset_nm")
    ref = _require_target(sk_or_doc, target) if target is not None else None
    key = _key(text_key, "text_key") if text_key is not None else None

    theta = math.radians(normalised_deg)
    axis_x, axis_y = math.cos(theta), math.sin(theta)      # 测量方向（尺寸线方向）u
    norm_x, norm_y = -axis_y, axis_x                       # 垂直方向 n（被忽略的分量）
    delta_x = float(origin2[0] - origin1[0])
    delta_y = float(origin2[1] - origin1[1])
    span_axis = delta_x * axis_x + delta_y * axis_y        # 沿 u 的投影（有符号，nm）
    span_perp = delta_x * norm_x + delta_y * norm_y        # 沿 n 的分量（被旋转标注忽略，nm）

    value_nm = int(round(abs(span_axis)))
    if value_nm <= 0:
        raise _fail("degenerate_dimension",
                    "the projection along rotation_deg is zero; pick another rotation")

    # 尺寸线：两端点投到「第一原点的垂向坐标 + offset」那条线上（先全量算完再写）。
    start_s = origin1[0] * axis_x + origin1[1] * axis_y
    end_s = origin2[0] * axis_x + origin2[1] * axis_y
    line_t = origin1[0] * norm_x + origin1[1] * norm_y + float(offset)
    line_start = [int(round(start_s * axis_x + line_t * norm_x)),
                  int(round(start_s * axis_y + line_t * norm_y))]
    line_end = [int(round(end_s * axis_x + line_t * norm_x)),
                int(round(end_s * axis_y + line_t * norm_y))]

    node = {
        "uuid": str(_uuid.uuid4()),
        "kind": "linear",          # 与族口径一致：旋转是线性标注的子型，不是新 kind
        "value_nm": value_nm,      # 整数纳米（沿 rotation_deg 的投影）
        "args": {
            "dim_subkind": "rotated",
            "measure_unit": "nm",
            "angle_unit": "micro_deg",
            "rotation_input_deg": float(rotation_deg),
            "rotation_deg": normalised_deg,
            "rotation_micro_deg": micro_deg,
            "measure_dir": [round(axis_x, 12), round(axis_y, 12)],
            "measure_normal": [round(norm_x, 12), round(norm_y, 12)],
            "origins_nm": [list(origin1), list(origin2)],
            "dim_line_offset_nm": offset,
            "dim_line_nm": [line_start, line_end],
            "span_axis_nm": span_axis,
            "span_perp_nm": span_perp,
            "value_source": "solved",
        },
    }
    if ref is not None:
        node["target"] = ref
    if key is not None:
        node["text_key"] = key
    _live_annotations(sk_or_doc).append(node)
    return [node["uuid"]]


OPERATIONS: dict[str, Callable] = {
    "add_rotated_dimension": add_rotated_dimension,
}


# --------------------------------------------------------------------------------------
# 自测（硬断言：真几何、真落盘、失败不半写）
# --------------------------------------------------------------------------------------


def _selftest() -> tuple[int, list]:
    checks = 0
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(label)

    mm = 1_000_000
    origin1 = (0, 0)
    origin2 = (30 * mm, 40 * mm)   # 3-4-5：水平 30、竖直 40、斜边 50
    doc: dict = {"annotations": []}

    # 1) rotation=0 ⇒ 量水平投影 30 mm；垂直的 40 mm 必须被忽略（负例：同点对换角度就变）
    returned = add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=0)
    node = doc["annotations"][-1]
    check(returned == [node["uuid"]], "return_is_the_written_uuid")
    check(isinstance(node["value_nm"], int) and node["kind"] == "linear",
          "node_is_integer_nanometre_linear")
    check(node["value_nm"] == 30 * mm, "rot0_measures_horizontal_projection")
    check(node["args"]["span_perp_nm"] == 40.0 * mm, "rot0_records_ignored_perpendicular")
    check(node["args"]["dim_line_nm"] == [[0, 0], [30 * mm, 0]], "rot0_dim_line_geometry")
    check(node["args"]["value_source"] == "solved", "value_is_solved_not_declared")

    # 2) rotation=90 ⇒ 竖直投影 40 mm；rotation=180 与 0 等价（归一化）
    add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=90)
    check(doc["annotations"][-1]["value_nm"] == 40 * mm, "rot90_measures_vertical_projection")
    add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=180)
    check(doc["annotations"][-1]["value_nm"] == 30 * mm, "rot180_normalised_to_rot0")
    check(doc["annotations"][-1]["args"]["rotation_micro_deg"] == 0,
          "rot180_micro_deg_normalised")

    # 3) 与斜边同向 ⇒ 真投影 50 mm（真几何，非调用方给的数）
    diagonal_deg = math.degrees(math.atan2(40 * mm, 30 * mm))
    add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=diagonal_deg)
    check(doc["annotations"][-1]["value_nm"] == 50 * mm, "diagonal_rotation_measures_true_span")

    # 4) offset 只平移尺寸线、不改测量值
    add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=0,
                          offset_nm=5 * mm, text_key="dim.rotated.length")
    offset_node = doc["annotations"][-1]
    check(offset_node["args"]["dim_line_nm"] == [[0, 5 * mm], [30 * mm, 5 * mm]],
          "offset_shifts_dim_line_only")
    check(offset_node["value_nm"] == 30 * mm and offset_node["text_key"] == "dim.rotated.length",
          "offset_keeps_value_and_key")

    # 5) 失败路径：正确 reason + **不半写文档**
    before = len(doc["annotations"])
    try:
        add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin1, rotation_deg=0)
        check(False, "coincident_origins_rejected")
    except CadError as exc:
        check(exc.reason == "degenerate_dimension", "coincident_origins_reason")
    try:
        add_rotated_dimension(doc, origin1_nm=origin1, origin2_nm=origin2, rotation_deg=0,
                              target=(str(_uuid.uuid4()), "edge"))
        check(False, "dangling_target_rejected")
    except CadError as exc:
        check(exc.reason == "unknown_reference", "dangling_target_reason")
    try:
        add_rotated_dimension(doc, origin1_nm=(0.0, 0), origin2_nm=origin2, rotation_deg=0)
        check(False, "float_length_rejected")
    except CadError as exc:
        check(exc.reason == "invalid_length", "float_length_reason")
    try:
        add_rotated_dimension({"entities": []}, origin1_nm=origin1, origin2_nm=origin2,
                              rotation_deg=0)
        check(False, "bare_sketch_rejected")
    except CadError as exc:
        check(exc.reason == "annotation_needs_document", "bare_sketch_reason")
    check(len(doc["annotations"]) == before, "failed_calls_do_not_write")
    return checks, failures


if __name__ == "__main__":
    _checks, _failures = _selftest()
    print("SELFTEST annotation__w25 assertions=%d failed=%d" % (_checks, len(_failures)))
    for _label in _failures:
        print("  FAILED %s" % _label)
    if _failures:
        print("FEATURE_MISSING family=annotation op=add_rotated_dimension verdict=FAIL")
        sys.exit(1)
    print("FEATURE_OK family=annotation op=add_rotated_dimension verdict=PASS")
    sys.exit(0)
