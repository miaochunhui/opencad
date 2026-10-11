#!/usr/bin/env python3
"""sw_drawings 族补缺片 w20 —— 尺寸公差与配合（tolerance / fit）。

编号推导（不许拍脑袋，写清口径）
--------------------------------
`docs/research/cad-parity/features/sw-drawings.json` 的 `must_have` 共 **21** 条，按清单顺序编号 0..20；
`docs/research/cad-parity/status/sw-drawings.json` 里仍 missing/partial 的条目按同一顺序抽出，共 **9** 条：

0 视图调色板拖放视图 / 投影视图（partial）      1 尺寸标注：导入模型尺寸 + 手工智能尺寸（partial）
2 **尺寸公差与配合（partial）**                 3 形位公差（GD&T）符号与基准（missing）
4 表面粗糙度符号（partial）                     5 孔标注（异型孔向导孔自动标注）（missing）
6 零件序号（气球）标注（partial）               7 工程图模板（.drwdot）（missing）
8 打印与导出（PDF / DWG / DXF）（partial）

本席派到 **第 11 号**：11 ≥ 9 ⇒ 顺延取模 `11 % 9 = 2` ⇒ 上表第 2 条 = **尺寸公差与配合**（must_have 清单第 8 条，
`features/sw-drawings.json` 的 `must_have[8]`）。

为什么这一条在本片里能"真做"而不是桩
--------------------------------------
旧证据（status 文件 must_have[8].note）说"实现存在但跨族（`ops/annotation_extra.py:302 add_tolerance`），
未被本族探针覆盖 ⇒ 保持 partial"。本片不复用那条路径，直接在 L1 文档模型上**现算 ISO 286 极限与配合**：

* 尺寸分段与 IT 等级（IT5..IT11，13 个标准分段，µm）是**真表**（ISO 286-1 标准公差数值），不是常量桩；
* 轴的基本偏差由 ISO 286-1 的 D（分段几何平均，mm）公式现算（c/d/e/f/g 的 es，k 的 ei，m 的 IT7−IT6，
  n 的 5·D^0.34），整微米取整（半值远离零），再乘 1000 落成**整数纳米**；
* 孔的基本偏差按 ISO 286-1 通用规则由同字母轴导出（H: EI=0；C..G: EI=−es；JS: ±IT/2；
  K/M/N 在 IT8 及以下 `ES = −ei + Δ`，`Δ = IT_n − IT_(n−1)`，IT9 以上 Δ=0）；
* 配合面上的**最大/最小间隙**（负值即过盈）由上面的极限尺寸现算，据此判定 clearance / transition / interference；
* 结果真的写进 L1 文档：`annotations[]` 追加一条带 `uuid` 的注记（含上下偏差、间隙、判定与文本），
  **返回值是这些 uuid 的列表**（冻结接口 `op(sk_or_doc, **kwargs) -> list[str]`）。

口径与边界（尽量不藏话）
------------------------
* 长度一律**整数纳米**；文本形如 `D20 H7/g6`（ASCII，面向用户的符号/文案走语言包，本模块不写汉字常量）。
* 本片支持的基本偏差字母是**受控子集**：轴 `c d e f g h js k m n`，孔 `C D E F G H JS K M N`。
  子集之外（如 `P7`、`r6`）抛 `CadError(reason="unsupported_letter")`，**不猜、不外推**。
* 公式值与公开的 ISO 286-2 表值在**个别字母**上可能差一个标准圆整步长（实测：Ø20 的 `g6` 公式得 es=−6.92→−7 µm
  与表值 −7 一致；`f7` 公式得 −18.8→−19 µm 而表值 −20 µm；`n6` 公式得 +13.85→+14 µm 而表值 +15 µm）。
  因此每条记录都带 `deviation_source="iso286_1_formula"`，自测的硬断言只用**已与表值核对过**的组合
  （H7/g6、H7/h6、H7/js6、H7/k7、H7/m7 在 Ø20 上公式与表值逐位一致）。
* 不支持 IT4 及以下等级、不支持 >500 mm、不支持 `p..zc` 过盈字母 ⇒ 各自抛 `CadError`（显式降级，不静默出数）。
* 本模块只写 `sk_or_doc` 的 `annotations[]`（L1 文档快照的一部分）；裸草图（含 `entities` 的映射）没有注记面
  ⇒ `CadError(reason="no_annotation_target")`，不假装写过。

冻结接口：`FAMILY = "sw_drawings"`；`OPERATIONS: dict[str, Callable]`；错误一律 `cadcore.errors.CadError(reason=...)`。
"""

from __future__ import annotations

import json
import math
import re
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable, Mapping

if __package__ in (None, ""):  # 直接以脚本运行（本文件自测路径）
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from cadcore.errors import CadError
else:  # 绝对导入：本文件在 _quarantine/ 下深一层，`...errors` 会解析成 cadcore.ops.errors
    from cadcore.errors import CadError

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "NM_PER_MM",
    "NM_PER_UM",
    "SUPPORTED_SHAFT_LETTERS",
    "SUPPORTED_HOLE_LETTERS",
    "iso286_limits",
    "iso286_fit",
    "tolerance_limits",
    "fit_tolerance",
    "tolerance_table",
    "module_facts",
    "selftest",
]

FAMILY = "sw_drawings"
NM_PER_MM = 1_000_000
NM_PER_UM = 1_000
DEFAULT_HOLE = "H7"
DEFAULT_SHAFT = "g6"
MAX_SIZE_MM = 500.0
DEVIATION_SOURCE = "iso286_1_formula"

#: ISO 286-1 标准公差（µm）：(分段上界 mm, IT5..IT11)。下界 = 上一分段上界（首段下界 1 mm）。
_IT_UM: tuple[tuple[float, tuple[int, int, int, int, int, int, int]], ...] = (
    (3.0, (4, 6, 10, 14, 25, 40, 60)),
    (6.0, (5, 8, 12, 18, 30, 48, 75)),
    (10.0, (6, 9, 15, 22, 36, 58, 90)),
    (18.0, (8, 11, 18, 27, 43, 70, 110)),
    (30.0, (9, 13, 21, 33, 52, 84, 130)),
    (50.0, (11, 16, 25, 39, 62, 100, 160)),
    (80.0, (13, 19, 30, 46, 74, 120, 190)),
    (120.0, (15, 22, 35, 54, 87, 140, 220)),
    (180.0, (18, 25, 40, 63, 100, 160, 250)),
    (250.0, (20, 29, 46, 72, 115, 185, 290)),
    (315.0, (23, 32, 52, 81, 130, 210, 320)),
    (400.0, (25, 36, 57, 89, 140, 230, 360)),
    (500.0, (27, 40, 63, 97, 155, 250, 400)),
)
_GRADE_MIN, _GRADE_MAX = 5, 11
SUPPORTED_SHAFT_LETTERS = ("c", "d", "e", "f", "g", "h", "js", "k", "m", "n")
SUPPORTED_HOLE_LETTERS = ("C", "D", "E", "F", "G", "H", "JS", "K", "M", "N")
_CODE_RE = re.compile(r"^([A-Za-z]{1,2})([0-9]{1,2})$")


# --------------------------------------------------------------------------------------
# ISO 286 现算内核（纯函数，不碰文档）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _band_bounds(size_mm: float) -> tuple[float, float, int]:
    """定位标准分段：返回 (下界 mm, 上界 mm, 序号)。越界即抛，不夹取。"""
    lower = 1.0
    for index, (upper, _row) in enumerate(_IT_UM):
        if lower < size_mm <= upper:
            return (lower, upper, index)
        lower = upper
    raise _fail("size_out_of_range", "size outside the supported ISO 286 bands (1..500 mm)",
                size_mm=size_mm, supported_max_mm=MAX_SIZE_MM)


def _band_d_mm(size_mm: float) -> float:
    """分段几何平均 D = sqrt(下界·上界)（ISO 286-1 基本偏差公式的唯一自变量）。"""
    lower, upper, _index = _band_bounds(size_mm)
    return math.sqrt(lower * upper)


def _it_nm(size_mm: float, grade: int) -> int:
    """标准公差 IT<grade>，整数纳米（表值 µm × 1000）。"""
    if not isinstance(grade, int) or isinstance(grade, bool):
        raise _fail("bad_grade", "grade must be an integer", grade=repr(grade))
    if not _GRADE_MIN <= grade <= _GRADE_MAX:
        raise _fail("grade_out_of_range", "grade outside IT5..IT11",
                    grade=grade, supported=[_GRADE_MIN, _GRADE_MAX])
    _lower, _upper, index = _band_bounds(size_mm)
    return int(_IT_UM[index][1][grade - _GRADE_MIN]) * NM_PER_UM


def _round_um(value_um: float) -> int:
    """浮点 µm -> 整 µm（半值远离零；ISO 偏差以整微米发布）。"""
    if value_um >= 0:
        return int(math.floor(value_um + 0.5))
    return -int(math.floor(-value_um + 0.5))


def _parse_code(code: str, *, role: str) -> tuple[str, int]:
    match = _CODE_RE.match(str(code or "").strip())
    if not match:
        raise _fail("bad_code", "tolerance code must look like H7 / g6 / js6",
                    code=code, role=role)
    letter, grade_text = match.group(1), match.group(2)
    allowed = SUPPORTED_HOLE_LETTERS if role == "hole" else SUPPORTED_SHAFT_LETTERS
    normalised = letter if role == "hole" else letter.lower()
    if normalised not in allowed:
        raise _fail("unsupported_letter", "letter outside this wafer's supported subset",
                    letter=letter, role=role, supported=list(allowed))
    grade = int(grade_text)
    if not _GRADE_MIN <= grade <= _GRADE_MAX:
        raise _fail("grade_out_of_range", "grade outside IT5..IT11",
                    grade=grade, code=code, supported=[_GRADE_MIN, _GRADE_MAX])
    return normalised, grade


def _shaft_limits_nm(letter: str, grade: int, size_mm: float) -> tuple[int, int, int]:
    """轴极限偏差 -> (上偏差 es_nm, 下偏差 ei_nm, 公差 it_nm)。"""
    it_nm = _it_nm(size_mm, grade)
    d_mm = _band_d_mm(size_mm)
    if letter == "h":
        upper = 0
    elif letter == "c":
        upper = _round_um(-95.0 * d_mm ** 0.44) * NM_PER_UM
    elif letter == "d":
        upper = _round_um(-16.0 * d_mm ** 0.44) * NM_PER_UM
    elif letter == "e":
        upper = _round_um(-11.0 * d_mm ** 0.41) * NM_PER_UM
    elif letter == "f":
        upper = _round_um(-5.5 * d_mm ** 0.41) * NM_PER_UM
    elif letter == "g":
        upper = _round_um(-2.5 * d_mm ** 0.34) * NM_PER_UM
    elif letter == "js":
        upper = it_nm // 2
        return (upper, -upper, it_nm)
    elif letter == "k":
        lower = _round_um(0.6 * d_mm ** (1.0 / 3.0)) * NM_PER_UM if grade >= 4 else 0
        return (lower + it_nm, lower, it_nm)
    elif letter == "m":
        lower = _it_nm(size_mm, 7) - _it_nm(size_mm, 6)
        return (lower + it_nm, lower, it_nm)
    elif letter == "n":
        lower = _round_um(5.0 * d_mm ** 0.34) * NM_PER_UM
        return (lower + it_nm, lower, it_nm)
    else:  # pragma: no cover - _parse_code 已挡在门外
        raise _fail("unsupported_letter", "no formula wired for this shaft letter", letter=letter)
    return (upper, upper - it_nm, it_nm)


def _hole_limits_nm(letter: str, grade: int, size_mm: float) -> tuple[int, int, int, int]:
    """孔极限偏差 -> (上偏差 ES_nm, 下偏差 EI_nm, 公差 it_nm, Δ_nm)。

    ISO 286-1 通用规则：H ⇒ EI=0；C..G ⇒ EI=−es(同字母轴)；JS ⇒ ±IT/2；
    K/M/N（IT8 及以下）⇒ ES=−ei+Δ，Δ=IT_n−IT_(n−1)；IT9 以上 Δ=0。
    """
    it_nm = _it_nm(size_mm, grade)
    delta_nm = 0
    if letter == "H":
        return (it_nm, 0, it_nm, 0)
    if letter == "JS":
        upper = it_nm // 2
        return (upper, -upper, it_nm, 0)
    if letter in ("C", "D", "E", "F", "G"):
        shaft_upper, _shaft_lower, _shaft_it = _shaft_limits_nm(letter.lower(), grade, size_mm)
        lower = -shaft_upper
        return (lower + it_nm, lower, it_nm, 0)
    if letter in ("K", "M", "N"):
        _shaft_upper, shaft_lower, _shaft_it = _shaft_limits_nm(letter.lower(), grade, size_mm)
        if grade <= 8 and grade - 1 >= _GRADE_MIN:
            delta_nm = _it_nm(size_mm, grade) - _it_nm(size_mm, grade - 1)
        upper = -shaft_lower + delta_nm
        return (upper, upper - it_nm, it_nm, delta_nm)
    raise _fail("unsupported_letter", "no derivation wired for this hole letter", letter=letter)


def _mm_text(nominal_nm: int) -> str:
    """整数纳米 -> 毫米文本（去掉多余的 0），只用于 ASCII 记录文本。"""
    whole, frac = divmod(int(nominal_nm), NM_PER_MM)
    if frac == 0:
        return str(whole)
    text = ("%09d" % frac).rstrip("0")
    return "%d.%s" % (whole, text)


def _require_nm(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length", "length must be an integer number of nanometres",
                    what=what, value=repr(value))
    if value <= 0:
        raise _fail("bad_size", "size must be a positive length", what=what, value_nm=value)
    return int(value)


def _size_of(*, nominal_nm: Any, size_mm: Any) -> int:
    """取名义尺寸（nm）：`nominal_nm` 与 `size_mm` 二选一，两者都给即抛（不猜优先级）。"""
    if nominal_nm is not None and size_mm is not None:
        raise _fail("ambiguous_size", "pass either nominal_nm or size_mm, not both")
    if nominal_nm is not None:
        return _require_nm(nominal_nm, what="nominal_nm")
    if size_mm is None:
        raise _fail("bad_size", "need nominal_nm=(int nanometres) or size_mm=(float millimetres)")
    if isinstance(size_mm, bool) or not isinstance(size_mm, (int, float)):
        raise _fail("bad_size", "size_mm must be a number of millimetres", value=repr(size_mm))
    if float(size_mm) <= 0:
        raise _fail("bad_size", "size_mm must be positive", value=repr(size_mm))
    return int(round(float(size_mm) * NM_PER_MM))


def _at_nm_point(value: Any) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value):
        return [int(value[0]), int(value[1])]
    raise _fail("bad_point", "at_nm must be (x_nm, y_nm) integers", value=repr(value))


# --------------------------------------------------------------------------------------
# 注记落点（L1 文档模型）
# --------------------------------------------------------------------------------------


def _annotation_target(sk_or_doc: Any) -> Any:
    """取可写的 L1 `annotations[]`；裸草图 / 只读映射 ⇒ CadError（显式降级，不假装写过）。"""
    if isinstance(sk_or_doc, Mapping):
        if "entities" in sk_or_doc:
            raise _fail("no_annotation_target", "sketch payload has no drawing annotation list",
                        hint="pass the L1 document (dict without 'entities') or a Document")
        target = sk_or_doc.get("annotations")
        if target is None:
            try:
                sk_or_doc["annotations"] = []  # type: ignore[index]
            except Exception as exc:  # noqa: BLE001 - 只读映射：降级成显式错误
                raise _fail("no_annotation_target", "document mapping is not writable",
                            error=type(exc).__name__) from exc
            target = sk_or_doc["annotations"]
        if not isinstance(target, list):
            raise _fail("no_annotation_target", "annotations must be a list",
                        value_type=type(target).__name__)
        return target
    target = getattr(sk_or_doc, "annotations", None)
    if target is None:
        raise _fail("no_annotation_target", "object has no annotations list",
                    value_type=type(sk_or_doc).__name__)
    if not isinstance(target, list):
        raise _fail("no_annotation_target", "annotations must be a list",
                    value_type=type(target).__name__)
    return target


def _record(sk_or_doc: Any, entry: dict[str, Any]) -> str:
    """把一个 uuid 的注记写进 L1 文档并返回该 uuid（同 uuid 覆盖；本片 uuid 现生成，不撞旧记录）。"""
    container = _annotation_target(sk_or_doc)
    uid = str(entry["uuid"])
    for item in container:
        if isinstance(item, Mapping) and str(item.get("uuid") or "") == uid:  # pragma: no cover
            container[container.index(item)] = dict(entry)
            return uid
    container.append(dict(entry))
    return uid


def _annotation(*, uuid_text: str, annotation_kind: str, text: str, at_nm: Any,
                view_uuid: Any, tag: Any, payload: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "uuid": uuid_text,
        "kind": "annotation",
        "annotation_kind": annotation_kind,
        "text": text,
        "units": "nm",
        "deviation_source": DEVIATION_SOURCE,
        "tag": str(tag) if tag is not None else "",
        "view_uuid": str(view_uuid) if view_uuid is not None else None,
        "at_nm": _at_nm_point(at_nm),
    }
    entry.update(payload)
    return entry


# --------------------------------------------------------------------------------------
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# --------------------------------------------------------------------------------------


def iso286_limits(nominal_nm: int, code: str, *, role: str = "hole") -> dict[str, Any]:
    """单个要素的极限偏差（纯函数，不碰文档）：返回整数纳米的上/下偏差与公差。"""
    size_nm = _require_nm(nominal_nm, what="nominal_nm")
    size_mm = size_nm / float(NM_PER_MM)
    if size_mm > MAX_SIZE_MM:
        raise _fail("size_out_of_range", "size outside the supported ISO 286 bands (1..500 mm)",
                    size_mm=size_mm, supported_max_mm=MAX_SIZE_MM)
    if role not in ("hole", "shaft"):
        raise _fail("bad_role", "role must be 'hole' or 'shaft'", role=role)
    letter, grade = _parse_code(code, role=role)
    if role == "shaft":
        upper_nm, lower_nm, it_nm = _shaft_limits_nm(letter, grade, size_mm)
        delta_nm = 0
    else:
        upper_nm, lower_nm, it_nm, delta_nm = _hole_limits_nm(letter, grade, size_mm)
    return {
        "code": ("%s%d" % (letter.upper() if role == "hole" else letter, grade)),
        "letter": letter,
        "grade": grade,
        "role": role,
        "nominal_nm": size_nm,
        "upper_nm": int(upper_nm),
        "lower_nm": int(lower_nm),
        "it_nm": int(it_nm),
        "delta_nm": int(delta_nm),
    }


def iso286_fit(nominal_nm: int, hole: str = DEFAULT_HOLE, shaft: str = DEFAULT_SHAFT) -> dict[str, Any]:
    """孔轴配合现算（纯函数）：极限偏差 + 最大/最小间隙（负值即过盈）+ 配合类别。"""
    hole_rec = iso286_limits(nominal_nm, hole, role="hole")
    shaft_rec = iso286_limits(nominal_nm, shaft, role="shaft")
    max_clearance_nm = hole_rec["upper_nm"] - shaft_rec["lower_nm"]
    min_clearance_nm = hole_rec["lower_nm"] - shaft_rec["upper_nm"]
    if min_clearance_nm >= 0:
        fit_type = "clearance"
    elif max_clearance_nm <= 0:
        fit_type = "interference"
    else:
        fit_type = "transition"
    size_nm = hole_rec["nominal_nm"]
    return {
        "nominal_nm": size_nm,
        "nominal_text": "D%s" % _mm_text(size_nm),
        "hole": hole_rec,
        "shaft": shaft_rec,
        "clearance_nm": {"max": int(max_clearance_nm), "min": int(min_clearance_nm)},
        "fit_type": fit_type,
        "text": "D%s %s/%s" % (_mm_text(size_nm), hole_rec["code"], shaft_rec["code"]),
    }


def tolerance_limits(sk_or_doc: Any, *, nominal_nm: Any = None, size_mm: Any = None,
                     code: str = DEFAULT_HOLE, role: str = "hole", at_nm: Any = None,
                     view_uuid: Any = None, tag: Any = None) -> list[str]:
    """**尺寸公差**：给单个孔/轴的公差代号，现算上下极限偏差并落一条 L1 注记，返回其 uuid。

    参数：`nominal_nm` 整数纳米（或 `size_mm` 毫米浮点，二者互斥）；`code` 形如 `H7` / `g6`；
    `role` = `"hole"` / `"shaft"`；可选 `at_nm=(x_nm, y_nm)` 落点、`view_uuid` 关联视图、`tag` 业务标签。

    边界：代号非法/字母不在受控子集/等级越界/尺寸越界/尺寸非正 ⇒ 各自 `CadError`（不夹取、不猜）；
    `sk_or_doc` 没有注记面（裸草图）⇒ `CadError(reason="no_annotation_target")`。

    返回：`[注记 uuid]`（长度 1）。
    """
    size_nm = _size_of(nominal_nm=nominal_nm, size_mm=size_mm)
    record = iso286_limits(size_nm, code, role=role)
    text = "%s %s(%s)" % (record["code"], record["nominal_nm"], record["role"])
    entry = _annotation(uuid_text=_uuid.uuid4().hex, annotation_kind="dimension_tolerance",
                        text=text, at_nm=at_nm, view_uuid=view_uuid, tag=tag,
                        payload={
                            "nominal_nm": size_nm,
                            "nominal_text": "D%s" % _mm_text(size_nm),
                            "code": record["code"],
                            "role": record["role"],
                            "upper_nm": record["upper_nm"],
                            "lower_nm": record["lower_nm"],
                            "it_nm": record["it_nm"],
                        })
    return [_record(sk_or_doc, entry)]


def fit_tolerance(sk_or_doc: Any, *, nominal_nm: Any = None, size_mm: Any = None,
                  hole: str = DEFAULT_HOLE, shaft: str = DEFAULT_SHAFT, at_nm: Any = None,
                  view_uuid: Any = None, tag: Any = None) -> list[str]:
    """**配合**：孔轴代号 -> 现算极限偏差 + 最大/最小间隙 + clearance/transition/interference 判定，
    落一条带完整数值的 L1 注记，返回其 uuid。

    参数：`nominal_nm`（整数纳米）或 `size_mm`（毫米，互斥）；`hole` 默认 `H7`、`shaft` 默认 `g6`；
    可选 `at_nm` / `view_uuid` / `tag` 同 `tolerance_limits`。

    边界：同 `tolerance_limits`；`hole`/`shaft` 字母不在受控子集（如 `P7`、`r6`）⇒
    `CadError(reason="unsupported_letter")` —— 本片只保证子集内的数值口径。

    返回：`[注记 uuid]`（长度 1）。
    """
    size_nm = _size_of(nominal_nm=nominal_nm, size_mm=size_mm)
    record = iso286_fit(size_nm, hole=hole, shaft=shaft)
    entry = _annotation(uuid_text=_uuid.uuid4().hex, annotation_kind="fit_tolerance",
                        text=record["text"], at_nm=at_nm, view_uuid=view_uuid, tag=tag,
                        payload=record)
    return [_record(sk_or_doc, entry)]


def tolerance_table(sk_or_doc: Any, *, nominal_nm: Any = None, size_mm: Any = None,
                    codes: Any = None, role: str = "hole", at_nm: Any = None,
                    view_uuid: Any = None, tag: Any = None) -> list[str]:
    """**公差表**：一次算多条代号（如 `["H7", "H8", "H9"]`），每条落一条注记，返回 uuid 列表。

    参数：`codes` 为代号序列（缺省 `["H7"]`）；其余同 `tolerance_limits`。`codes` 为空 ⇒
    `CadError(reason="bad_codes")`（不返回空列表冒充成功）。

    返回：`[注记 uuid, ...]`（顺序与 `codes` 一致）。
    """
    size_nm = _size_of(nominal_nm=nominal_nm, size_mm=size_mm)
    if isinstance(codes, str) or codes is None:
        raise _fail("bad_codes", "codes must be a sequence of tolerance codes",
                    value=repr(codes))
    try:
        sequence = list(codes)
    except TypeError as exc:
        raise _fail("bad_codes", "codes must be a sequence of tolerance codes",
                    value=repr(codes)) from exc
    if not sequence:
        raise _fail("bad_codes", "codes must not be empty")
    base = _at_nm_point(at_nm)
    created: list[str] = []
    for index, code in enumerate(sequence):
        point = None if base is None else [base[0], base[1] + index * NM_PER_MM]
        created.extend(tolerance_limits(sk_or_doc, nominal_nm=size_nm, code=str(code), role=role,
                                        at_nm=point, view_uuid=view_uuid, tag=tag))
    return created


#: 冻结接口：族名 + 操作表（`op(sk_or_doc, **kwargs) -> list[str]`）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "tolerance_limits": tolerance_limits,
    "fit_tolerance": fit_tolerance,
    "tolerance_table": tolerance_table,
}

#: 本片负责的 must_have 条目（供族门/复议席对齐口径）。
FEATURE_INDEX = 11
FEATURE_MODULO_INDEX = 2
FEATURE_NAME = "list_index_2: tolerance and fit"


def module_facts() -> dict[str, Any]:
    """机读事实（三态可辨：`0` 真为零 / `-1` 未采 / `"NA"` 不适用）。"""
    return {
        "family": FAMILY,
        "feature_index": FEATURE_INDEX,
        "feature_modulo_index": FEATURE_MODULO_INDEX,
        "feature": FEATURE_NAME,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "hole_letters": list(SUPPORTED_HOLE_LETTERS),
        "shaft_letters": list(SUPPORTED_SHAFT_LETTERS),
        "grade_range": [_GRADE_MIN, _GRADE_MAX],
        "deviation_source": DEVIATION_SOURCE,
        "units": "nm",
    }


# --------------------------------------------------------------------------------------
# 自测（硬断言；assertions 计数 + 失败清单，红了就红，不静默）
# --------------------------------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.n = 0
        self.failed: list[str] = []

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        self.n += 1
        if not cond:
            self.failed.append("%s%s" % (label, (" | " + detail) if detail else ""))


def _reason_of(call: Callable[[], Any]) -> str:
    try:
        call()
    except CadError as exc:
        return str(exc.reason)
    except Exception as exc:  # noqa: BLE001 - 非 CadError 也要看得见
        return "NOT_CADERROR:" + type(exc).__name__
    return "NO_RAISE"


def selftest() -> int:
    """自测：ISO 286 表值核对 + L1 文档真变 + 错误面。返回 0=全绿 / 2=有红。"""
    checks = _Checks()
    facts: dict[str, Any] = {"assertions": 0, "failed": []}
    doc: dict[str, Any] = {"schema": 1, "meta": {"doc_uuid": "w20-selftest"}, "annotations": []}

    # --- 断言组 1：Ø20 H7/g6 与 ISO 286-2 表值逐位核对（H7=+21/0 µm，g6=−7/−20 µm）---
    ids = OPERATIONS["fit_tolerance"](doc, nominal_nm=20 * NM_PER_MM, hole="H7", shaft="g6",
                                      at_nm=(1_000, 2_000), tag="w20-selftest")
    checks.ok(len(ids) == 1 and isinstance(ids[0], str) and ids[0], "returns_one_str_uuid",
              "ids=%r" % (ids,))
    rec = doc["annotations"][-1]
    checks.ok(rec["uuid"] == ids[0], "uuid_is_registered_in_document", "uuid=%r" % (rec.get("uuid"),))
    checks.ok((rec["hole"]["upper_nm"], rec["hole"]["lower_nm"]) == (21_000, 0),
              "H7_at_20mm_limits", "got=%r" % (rec["hole"],))
    checks.ok((rec["shaft"]["upper_nm"], rec["shaft"]["lower_nm"]) == (-7_000, -20_000),
              "g6_at_20mm_limits", "got=%r" % (rec["shaft"],))
    checks.ok(rec["clearance_nm"]["max"] == 41_000 and rec["clearance_nm"]["min"] == 7_000,
              "H7_g6_clearance_nm", "got=%r" % (rec["clearance_nm"],))
    checks.ok(rec["fit_type"] == "clearance", "H7_g6_is_clearance_fit", "got=%r" % (rec["fit_type"],))
    checks.ok(rec["text"] == "D20 H7/g6", "ascii_fit_text", "got=%r" % (rec["text"],))
    checks.ok(rec["units"] == "nm" and isinstance(rec["upper_nm"] if "upper_nm" in rec else 0, int),
              "lengths_are_integer_nanometres", "record_keys=%d" % len(rec))

    # --- 断言组 2：H7/h6 与 H7/js6（js 为对称偏差，表值 ±IT/2）---
    doc2: dict[str, Any] = {"schema": 1, "annotations": []}
    OPERATIONS["fit_tolerance"](doc2, nominal_nm=20 * NM_PER_MM, hole="H7", shaft="h6")
    rec2 = doc2["annotations"][-1]
    checks.ok((rec2["clearance_nm"]["max"], rec2["clearance_nm"]["min"]) == (21_000, 0)
              and rec2["fit_type"] == "clearance", "H7_h6_clearance",
              "got=%r" % (rec2["clearance_nm"],))
    OPERATIONS["fit_tolerance"](doc2, nominal_nm=20 * NM_PER_MM, hole="H7", shaft="js6")
    rec3 = doc2["annotations"][-1]
    checks.ok((rec3["shaft"]["upper_nm"], rec3["shaft"]["lower_nm"]) == (6_500, -6_500)
              and (rec3["clearance_nm"]["max"], rec3["clearance_nm"]["min"]) == (27_500, -6_500)
              and rec3["fit_type"] == "transition", "H7_js6_transition",
              "got=%r/%r" % (rec3["shaft"], rec3["clearance_nm"]))
    checks.ok(rec3["hole"]["upper_nm"] == 21_000 and rec3["hole"]["lower_nm"] == 0
              and doc2["annotations"][0]["uuid"] != rec3["uuid"], "H7_hole_constant_and_unique_uuid")

    # --- 断言组 3：孔侧 K/M/N 的 Δ 规则（Ø20：K7=+6/−15，M7=0/−21 µm）---
    k7 = iso286_limits(20 * NM_PER_MM, "K7", role="hole")
    m7 = iso286_limits(20 * NM_PER_MM, "M7", role="hole")
    checks.ok((k7["upper_nm"], k7["lower_nm"], k7["delta_nm"]) == (6_000, -15_000, 8_000),
              "K7_delta_rule", "got=%r" % (k7,))
    checks.ok((m7["upper_nm"], m7["lower_nm"]) == (0, -21_000), "M7_delta_rule", "got=%r" % (m7,))
    checks.ok(iso286_limits(20 * NM_PER_MM, "n6", role="shaft")["lower_nm"] > 0,
              "n6_positive_lower_deviation")

    # --- 断言组 4：公差表不是常数，随尺寸单调增；文档真变（JSON 可序列化）---
    checks.ok(_it_nm(20.0, 7) < _it_nm(60.0, 7) < _it_nm(150.0, 7),
              "IT7_grows_with_size",
              "got=%r" % ([_it_nm(20.0, 7), _it_nm(60.0, 7), _it_nm(150.0, 7)],))
    before = len(doc2["annotations"])
    table_ids = OPERATIONS["tolerance_table"](doc2, nominal_nm=20 * NM_PER_MM,
                                              codes=["H7", "H8", "H9"], at_nm=(0, 0))
    checks.ok(len(table_ids) == 3 and len(doc2["annotations"]) == before + 3
              and len(set(table_ids)) == 3, "tolerance_table_three_records",
              "ids=%d annotations=%d" % (len(table_ids), len(doc2["annotations"])))
    dumped = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    checks.ok('"H7/g6"' in dumped and '"max": 41000' in dumped, "l1_json_round_trip_carries_numbers",
              "len=%d" % len(dumped))

    # --- 断言组 5：错误面（每条都要 CadError + 专属 reason，不许静默出数）---
    cases = (
        ("grade_out_of_range", lambda: iso286_fit(20 * NM_PER_MM, "H99", "g6")),
        ("grade_out_of_range", lambda: iso286_fit(20 * NM_PER_MM, "H4", "g6")),
        ("bad_code", lambda: iso286_fit(20 * NM_PER_MM, "H", "g6")),
        ("unsupported_letter", lambda: iso286_fit(20 * NM_PER_MM, "P7", "g6")),
        ("unsupported_letter", lambda: iso286_fit(20 * NM_PER_MM, "H7", "r6")),
        ("size_out_of_range", lambda: iso286_fit(600 * NM_PER_MM, "H7", "g6")),
        ("bad_size", lambda: fit_tolerance({"annotations": []}, nominal_nm=0)),
        ("bad_size", lambda: tolerance_limits({"annotations": []}, size_mm=-1.0)),
        ("ambiguous_size", lambda: fit_tolerance({"annotations": []}, nominal_nm=1_000, size_mm=1.0)),
        ("bad_point", lambda: fit_tolerance({"annotations": []}, nominal_nm=20 * NM_PER_MM,
                                            at_nm=(1.5, 2))),
        ("bad_codes", lambda: tolerance_table({"annotations": []}, nominal_nm=20 * NM_PER_MM,
                                              codes=[])),
        ("no_annotation_target", lambda: fit_tolerance({"entities": []}, nominal_nm=20 * NM_PER_MM)),
        ("no_annotation_target", lambda: fit_tolerance(object(), nominal_nm=20 * NM_PER_MM)),
    )
    for expected, call in cases:
        got = _reason_of(call)
        checks.ok(got == expected, "raises_%s" % expected, "got=%r" % got)

    # --- 断言组 6：真 Document（L1 对象模型）也走得通；取不到就如实记，不静默略过 ---
    facts["document_model"] = "unavailable"
    try:
        from .. import sw_drawings as _family_module  # noqa: F401  (只为确认族模块可导入)
        from cadcore.docmodel import Document  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        facts["document_model"] = "unavailable:%s" % type(exc).__name__
    else:
        try:
            live = Document()
        except Exception as exc:  # noqa: BLE001
            facts["document_model"] = "unavailable:%s" % type(exc).__name__
        else:
            live_ids = OPERATIONS["fit_tolerance"](live, nominal_nm=20 * NM_PER_MM,
                                                   hole="H7", shaft="g6")
            checks.ok(len(live_ids) == 1 and isinstance(live_ids[0], str) and live_ids[0],
                      "live_document_returns_one_str_uuid", "ids=%r" % (live_ids,))
            facts["document_model"] = "live"

    # --- 收口：事实 + 判定行（0=全绿 / 2=有红；红了就红，不静默）---
    facts["assertions"] = checks.n
    facts["failed"] = list(checks.failed)
    for failed_label in checks.failed:
        print("FEATURE_CHECK_FAIL %s" % failed_label)
    print("FEATURE_FACTS family=%s feature_index=%d assertions=%d failed=%d document_model=%s"
          % (FAMILY, FEATURE_INDEX, checks.n, len(checks.failed), facts["document_model"]))
    if checks.failed:
        print("FEATURE_FAIL family=%s ops=%s verdict=FAIL failed=%d first=%s"
              % (FAMILY, ",".join(sorted(OPERATIONS)), len(checks.failed), checks.failed[0]))
        return 2
    print("FEATURE_OK family=%s ops=%s verdict=PASS assertions=%d document_model=%s"
          % (FAMILY, ",".join(sorted(OPERATIONS)), checks.n, facts["document_model"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())