#!/usr/bin/env python3
"""SW-DRAWINGS 族实现片 w11：**尺寸公差与配合**（tolerance / fit）在 L1 文档模型上的真实现。

分工与清单定位
--------------
族清单 `docs/research/cad-parity/features/sw-drawings.json` 的 must_have 共 21 条，逐条状态见
`docs/research/cad-parity/status/sw-drawings.json`。仍 missing/partial 的 must_have 按清单顺序编号：

    0 视图调色板拖放视图 / 投影视图          partial
    1 尺寸标注：导入模型尺寸 + 手工智能尺寸   partial
    2 尺寸公差与配合                        partial   <-- 本片负责（片号 w11，取模后落在第 2 号）
    3 形位公差（GD&T）符号与基准             missing
    4 表面粗糙度符号                        partial
    5 孔标注（异型孔向导孔自动标注）         missing
    6 零件序号（气球）标注                   partial
    7 工程图模板（.drwdot）                  missing
    8 打印与导出（PDF / DWG / DXF）          partial

第 2 号原状态是 partial：`cadcore/ops/annotation_extra.py:302 add_tolerance` / `:339 add_fit_tolerance`
确有实现，但**不在 sw_drawings 族的探针面里**（族门只装载 sw_drawings*.py），且那两条只写注记、
不落在工程图文档的视图几何上。本片把这条能力做进本族：**公差从图纸上量到的真几何出发**，
上下极限、公差带宽度、配合间隙全部是整数纳米的真算术结果，并落成 L1 文档里可消费的 annotation 与图元。

族接口（冻结，与 `cadcore.ops.sw_drawings` 同一套口径）
----------------------------------------------------
    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    每个操作 `op(sk_or_doc, **kwargs) -> list[str]`；第一个位置参一律叫 `sk_or_doc`，其余一律关键字；
    返回值 = **本次新建对象**的 uuid 列表；长度一律整数纳米；错误一律 `cadcore.errors.CadError(reason=...)`。

两条操作各做什么（都是真数据后果，不做"看起来像"）
------------------------------------------------
1. `tolerance_dimension`：给图上一条尺寸加**公差**。名义尺寸缺省**从真几何量取**（视图 `bounds_model_nm`
   沿 `axis` 的跨度；没有视图就退到草图实体点的外包框，逐实体把 `radius_nm` 计入），也可由 `nominal_nm`
   显式给定。公差规格支持 `"+/-0.1"`（对称）、`"+0.025/0"`（上/下偏差）、`"50.025/50.000"`（极限尺寸，
   两个都大于名义即按极限解释）三种字面量，也支持等价的 mapping。随后按整数纳米算出
   `upper_nm = nominal + es`、`lower_nm = nominal + ei`、`width_nm = es - ei`，并造一条**真尺寸几何**：
   `offset_nm` 处的尺寸线 + 两条尺寸界线（都落进视图的 `entities[]`）。
2. `fit_dimension`：**配合**。`fit="H7/f7"` 这类配合代号按 ISO 286 的**标准公差因子公式**现算
   （`i = 0.45 * D^(1/3) + 0.001 * D`，µm；`D` = 尺寸段几何平均，`IT5..IT9` 的因子 7/10/16/25/40），
   基本偏差只实现有表格实证的一支：孔 `H / JS / F / G`、轴 `h / js / f / g`（`F/f` 用 `-5.5*D^0.41`、
   `G/g` 用 `-2.5*D^0.34`，µm）。算术全部走 `decimal`（40 位精度、µm 四舍五入到整微米再乘 1000 得纳米），
   不碰 float。结果给出孔的 ES/EI、轴的 es/ei、**最大/最小间隙**与配合类别
   （clearance / transition / interference），并造两个**真圆图元**：孔径取孔的上极限、轴径取轴的下极限。

显式边界（**不静默近似**，越界一律抛 CadError）
--------------------------------------------
* `fit` 只支持 6 mm < D <= 120 mm、IT5..IT9、上述字母集；越界 ⇒ `fit_out_of_scope`（不算、不猜表值）。
  这是**已知窄口径**：ISO 286 的成品表在 IT10 以上按优先数系取整，公式外推会与表值不符，故不声称覆盖。
* 公差值比 1 nm 更细、上限尺寸小于下限尺寸、未知 mode ⇒ `bad_tolerance`；名义尺寸非整数纳米或 <= 0 ⇒
  `bad_nominal`；量不到几何 ⇒ `tolerance_no_geometry`；裸草图没有 `annotations[]` ⇒ `tolerance_needs_document`。
* 视图 uuid 不在文档里 ⇒ `bad_view_reference`；配合代号不合法 ⇒ `bad_fit`；多余的关键字参数 ⇒
  `unexpected_kwargs`（不吞、不当没看见）。

三态口径（与 AGENTS.md E2.3 一致）：`0` = 真为零；`-1`（`UNMEASURED`）= 未采；`"NA"`（`NA`）= 不适用。
本片不 import 内核、不做布尔，故 `kernel = "NA"`、`kernel_capabilities = -1`。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/features/sw_drawings__w11.py

中文只出现在 docstring 与注释里（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import copy as _copy
import json as _json
import uuid as _uuid
from collections.abc import Mapping, MutableMapping
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Callable

if __package__ in (None, ""):  # 直跑本文件（见 docstring 自检命令）：补包搜索路径
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

from cadcore.errors import CadError  # 绝对导入：本文件在 _quarantine/ 下深一层，`...errors` 会解析成 cadcore.ops.errors

__all__ = [
    "FAMILY", "ALIASES", "OPERATIONS", "tolerance_dimension", "fit_dimension",
    "module_facts", "selftest",
]

FAMILY = "sw_drawings"
ALIASES = ("sw_drawings",)

NM_PER_MM = 1_000_000
NM_PER_UM = 1_000

NA = "NA"
UNMEASURED = -1
ZERO = 0

TOLERANCE_KIND = "tolerance_dimension"
FIT_KIND = "tolerance_fit"
DIMENSION_LAYER = "DIMENSION"
DEFAULT_TOLERANCE = "+/-0.1"
DEFAULT_OFFSET_NM = 12 * NM_PER_MM
DEFAULT_AXIS = "x"

# ISO 286：IT5..IT9 的标准公差因子倍数（k，ITn = k * i）。
GRADE_FACTORS = {5: Decimal(7), 6: Decimal(10), 7: Decimal(16), 8: Decimal(25), 9: Decimal(40)}
# ISO 286：本片已实证的尺寸段（mm，(low, high]，D 取几何平均）。
FIT_RANGES_MM = ((6, 10), (10, 18), (18, 30), (30, 50), (50, 80), (80, 120))
HOLE_LETTERS = ("H", "JS", "F", "G")
SHAFT_LETTERS = ("h", "js", "f", "g")
FAMILY_KWARGS = ("sheet", "kernel", "standard", "language", "size", "scale")
BOUND_KEYS = ("x_min_nm", "y_min_nm", "x_max_nm", "y_max_nm")


# --------------------------------------------------------------------------------------
# 通用小件
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """错误一律带机器可读 reason（调用方按 reason 归因，不去解析文案）。"""
    return CadError(message, reason=reason, **details)


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_int(value: Any, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length_nm", "%s must be an integer nanometer count" % what, what=what,
                    value=repr(value))
    return int(value)


def _nm_to_mm_text(nm: int) -> str:
    sign = "-" if nm < 0 else ""
    whole, rest = divmod(abs(int(nm)), NM_PER_MM)
    if rest == 0:
        return "%s%d" % (sign, whole)
    return "%s%d.%s" % (sign, whole, ("%06d" % rest).rstrip("0"))


def _signed_mm_text(nm: int) -> str:
    return ("+" if nm >= 0 else "-") + _nm_to_mm_text(abs(int(nm)))


def _mm_text_to_nm(text: Any) -> int:
    """十进制毫米文本 -> 整数纳米（Decimal 精确解析；比纳米细的一律报错，不四舍五入）。"""
    try:
        value = Decimal(str(text).strip())
    except Exception:
        raise _fail("bad_tolerance", "tolerance literal is not a decimal number in mm", text=str(text))
    if not value.is_finite():
        raise _fail("bad_tolerance", "tolerance literal must be finite", text=str(text))
    nm = value * NM_PER_MM
    if nm != nm.to_integral_value():
        raise _fail("bad_tolerance", "tolerance literal is finer than one nanometer", text=str(text))
    return int(nm)


def _check_kwargs(extra: Mapping[str, Any]) -> None:
    if extra:
        raise _fail("unexpected_kwargs", "unsupported keyword argument(s) were passed",
                    unexpected=sorted(str(key) for key in extra))


# --------------------------------------------------------------------------------------
# 公差规格（对称 / 上下偏差 / 极限尺寸 三种口径）
# --------------------------------------------------------------------------------------


def _tolerance_text(mode: str, es_nm: int, ei_nm: int) -> str:
    return "tol(mode=%s es=%s ei=%s)" % (mode, _signed_mm_text(es_nm), _signed_mm_text(ei_nm))


def _parse_tolerance(spec: Any, nominal_nm: int) -> dict[str, Any]:
    """公差规格 -> 整数纳米的上/下偏差。字面量：`+/-t`、`es/ei`、`limits_hi/limits_lo`。"""
    if isinstance(spec, Mapping):
        mode = str(spec.get("mode", "")).strip().lower()
        if mode in ("symmetric", "sym"):
            half = _mm_text_to_nm(spec.get("value_mm", spec.get("half_mm", "0")))
            if half < 0:
                raise _fail("bad_tolerance", "symmetric half width must not be negative", half_nm=half)
            es_nm, ei_nm = half, -half
        elif mode in ("deviation", "dev"):
            es_nm = _mm_text_to_nm(spec.get("upper_mm", spec.get("es_mm", "0")))
            ei_nm = _mm_text_to_nm(spec.get("lower_mm", spec.get("ei_mm", "0")))
        elif mode in ("limits", "limit"):
            upper = _mm_text_to_nm(spec.get("upper_mm", spec.get("max_mm", "0")))
            lower = _mm_text_to_nm(spec.get("lower_mm", spec.get("min_mm", "0")))
            es_nm, ei_nm = upper - nominal_nm, lower - nominal_nm
        else:
            raise _fail("bad_tolerance", "unknown tolerance mode", mode=mode or "none")
        source = "mapping"
    else:
        raw = str(spec).strip().replace("\u00b1", "+/-")
        if "+/-" in raw:
            half = _mm_text_to_nm(raw.split("+/-", 1)[1])
            if half < 0:
                raise _fail("bad_tolerance", "symmetric half width must not be negative", half_nm=half)
            es_nm, ei_nm, mode = half, -half, "symmetric"
        elif "/" in raw:
            head, tail = raw.split("/", 1)
            first = _mm_text_to_nm(head)
            second = _mm_text_to_nm(tail)
            if first > nominal_nm and second > nominal_nm:
                es_nm, ei_nm, mode = first - nominal_nm, second - nominal_nm, "limits"
            else:
                es_nm, ei_nm, mode = first, second, "deviation"
        else:
            raise _fail("bad_tolerance", "tolerance literal must be '+/-t' or 'es/ei'", text=raw)
        source = "literal"
    if es_nm < ei_nm:
        raise _fail("bad_tolerance", "upper deviation is below lower deviation",
                    es_nm=es_nm, ei_nm=ei_nm)
    return {"mode": mode, "es_nm": es_nm, "ei_nm": ei_nm, "width_nm": es_nm - ei_nm,
            "text": _tolerance_text(mode, es_nm, ei_nm), "source": source}


# --------------------------------------------------------------------------------------
# ISO 286 计算（公式现算，窄口径显式）
# --------------------------------------------------------------------------------------


def _size_range(nominal_nm: int) -> tuple[Decimal, Decimal]:
    """名义尺寸（整数纳米）-> ISO 286 尺寸段 (low, high]，超出实现范围即报错。"""
    with localcontext() as ctx:
        ctx.prec = 40
        size = Decimal(nominal_nm) / Decimal(NM_PER_MM)
        for low, high in FIT_RANGES_MM:
            if Decimal(low) < size <= Decimal(high):
                return Decimal(low), Decimal(high)
    raise _fail("fit_out_of_scope",
                "nominal size is outside the implemented ISO 286 range 6 < D <= 120 mm",
                nominal_nm=nominal_nm, allowed_mm=[list(pair) for pair in FIT_RANGES_MM])


def _standard_factor_um(low: Decimal, high: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        mid = (low * high).sqrt()
        return Decimal("0.45") * (mid ** (Decimal(1) / Decimal(3))) + Decimal("0.001") * mid


def _it_nm(grade: int, low: Decimal, high: Decimal) -> int:
    """IT 公差值（纳米）：k * i，µm 级四舍五入到整微米（与 ISO 286 表值口径一致）。"""
    if grade not in GRADE_FACTORS:
        raise _fail("fit_out_of_scope", "only IT5..IT9 are implemented", grade=grade,
                    allowed=sorted(GRADE_FACTORS))
    with localcontext() as ctx:
        ctx.prec = 40
        value_um = GRADE_FACTORS[grade] * _standard_factor_um(low, high)
        return int(value_um.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * NM_PER_UM


def _fundamental_nm(letter: str, low: Decimal, high: Decimal) -> int:
    """基本偏差幅值（纳米，非负）：F/f 用 5.5*D^0.41，G/g 用 2.5*D^0.34（µm，四舍五入）。"""
    with localcontext() as ctx:
        ctx.prec = 40
        mid = (low * high).sqrt()
        if letter in ("f", "F"):
            value_um = Decimal("5.5") * (mid ** Decimal("0.41"))
        elif letter in ("g", "G"):
            value_um = Decimal("2.5") * (mid ** Decimal("0.34"))
        elif letter in ("h", "H", "js", "JS"):
            value_um = Decimal(0)
        else:
            raise _fail("fit_out_of_scope", "fundamental deviation letter is not implemented",
                        letter=letter, allowed={"hole": list(HOLE_LETTERS), "shaft": list(SHAFT_LETTERS)})
        return int(value_um.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * NM_PER_UM


def _split_code(code: Any) -> tuple[str, int]:
    text = str(code).strip()
    index = 0
    letters = ""
    while index < len(text) and text[index].isalpha():
        letters += text[index]
        index += 1
    digits = text[index:]
    if not letters or not digits.isdigit():
        raise _fail("bad_fit", "fit code must be letter+grade, for example H7 or f7", code=text)
    return letters, int(digits)


def _limits(role: str, code: Any, low: Decimal, high: Decimal) -> dict[str, Any]:
    letter, grade = _split_code(code)
    allowed = HOLE_LETTERS if role == "hole" else SHAFT_LETTERS
    if letter not in allowed:
        raise _fail("fit_out_of_scope", "%s letter is not implemented" % role,
                    letter=letter, allowed=list(allowed))
    it_nm = _it_nm(grade, low, high)
    with localcontext() as ctx:
        ctx.prec = 40
        half_nm = int((Decimal(it_nm) / 2).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    fund_nm = _fundamental_nm(letter, low, high)
    if role == "hole":
        if letter == "H":
            ei_nm, es_nm = ZERO, it_nm
        elif letter == "JS":
            ei_nm, es_nm = -half_nm, half_nm
        else:
            ei_nm, es_nm = fund_nm, fund_nm + it_nm
    else:
        if letter == "h":
            es_nm, ei_nm = ZERO, -it_nm
        elif letter == "js":
            es_nm, ei_nm = half_nm, -half_nm
        else:
            es_nm, ei_nm = -fund_nm, -fund_nm - it_nm
    return {"role": role, "code": str(code).strip(), "letter": letter, "grade": grade,
            "it_nm": it_nm, "es_nm": es_nm, "ei_nm": ei_nm}


def resolve_fit(fit: Any, nominal_nm: int) -> dict[str, Any]:
    """配合代号 -> 孔/轴极限 + 最大/最小间隙 + 配合类别（真算术，非表值抄录）。"""
    text = str(fit).strip()
    if "/" not in text:
        raise _fail("bad_fit", "fit must be '<hole>/<shaft>', for example H7/f7", fit=text)
    hole_code, shaft_code = text.split("/", 1)
    low, high = _size_range(nominal_nm)
    hole = _limits("hole", hole_code, low, high)
    shaft = _limits("shaft", shaft_code, low, high)
    max_clearance_nm = hole["es_nm"] - shaft["ei_nm"]
    min_clearance_nm = hole["ei_nm"] - shaft["es_nm"]
    if min_clearance_nm >= 0:
        fit_type = "clearance"
    elif max_clearance_nm <= 0:
        fit_type = "interference"
    else:
        fit_type = "transition"
    return {"fit": text, "nominal_nm": int(nominal_nm), "hole": hole, "shaft": shaft,
            "max_clearance_nm": max_clearance_nm, "min_clearance_nm": min_clearance_nm,
            "fit_type": fit_type,
            "size_range_mm": [_nm_to_mm_text(int(low * NM_PER_MM)), _nm_to_mm_text(int(high * NM_PER_MM))]}


# --------------------------------------------------------------------------------------
# L1 文档接入（只吃文档映射，裸草图显式报错）
# --------------------------------------------------------------------------------------


def _document_host(sk_or_doc: Any) -> tuple[Any, list]:
    if isinstance(sk_or_doc, MutableMapping):
        annotations = sk_or_doc.get("annotations")
        if isinstance(annotations, list):
            return sk_or_doc, annotations
        if any(key in sk_or_doc for key in ("views", "sheets")):
            annotations = []
            sk_or_doc["annotations"] = annotations
            return sk_or_doc, annotations
        raise _fail("tolerance_needs_document",
                    "a bare sketch has no annotations[] -- pass the L1 drawing document")
    annotations = getattr(sk_or_doc, "annotations", None)
    if isinstance(annotations, list):
        return sk_or_doc, annotations
    raise _fail("tolerance_needs_document",
                "host has no annotations[] -- pass the L1 drawing document")


def _views_of(host: Any) -> list[Mapping]:
    found: list[Mapping] = []
    direct = _get(host, "views")
    if isinstance(direct, (list, tuple)):
        found.extend([view for view in direct if isinstance(view, Mapping)])
    sheets = _get(host, "sheets")
    if isinstance(sheets, (list, tuple)):
        for sheet in sheets:
            views = _get(sheet, "views")
            if isinstance(views, (list, tuple)):
                found.extend([view for view in views if isinstance(view, Mapping)])
    return found


def _bounds_of(view: Mapping) -> dict[str, int] | None:
    raw = _get(view, "bounds_model_nm")
    if isinstance(raw, Mapping):
        if all(isinstance(raw.get(key), int) for key in BOUND_KEYS):
            return {key: int(raw[key]) for key in BOUND_KEYS}
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(isinstance(v, int) for v in raw):
        return {key: int(value) for key, value in zip(BOUND_KEYS, raw)}
    return None


def _sketch_bounds(host: Any) -> dict[str, int] | None:
    xs: list[int] = []
    ys: list[int] = []
    sketches = _get(host, "sketches")
    if not isinstance(sketches, (list, tuple)):
        return None
    for sketch in sketches:
        entities = _get(sketch, "entities")
        if not isinstance(entities, (list, tuple)):
            continue
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            points: list[tuple[int, int]] = []
            for key in ("p1_nm", "p2_nm", "start_nm", "end_nm", "center_nm", "position_nm"):
                point = entity.get(key)
                if (isinstance(point, (list, tuple)) and len(point) >= 2
                        and isinstance(point[0], int) and isinstance(point[1], int)):
                    points.append((int(point[0]), int(point[1])))
            radius = entity.get("radius_nm")
            if isinstance(radius, int) and radius > 0:
                for px, py in points:
                    xs.extend([px - radius, px + radius])
                    ys.extend([py - radius, py + radius])
            else:
                for px, py in points:
                    xs.append(px)
                    ys.append(py)
    if not xs or not ys:
        return None
    return {"x_min_nm": min(xs), "y_min_nm": min(ys), "x_max_nm": max(xs), "y_max_nm": max(ys)}


def _measure(host: Any, view_ref: Any, axis: str) -> tuple[dict[str, int], str, Mapping | None]:
    """量真几何：优先视图 bounds，其次草图实体外包框；量不到就报错（不编数）。"""
    if axis not in ("x", "y"):
        raise _fail("bad_axis", "axis must be 'x' or 'y'", axis=str(axis))
    views = _views_of(host)
    chosen: Mapping | None = None
    if view_ref is not None:
        if isinstance(view_ref, Mapping):
            chosen = view_ref
        else:
            wanted = str(view_ref)
            for view in views:
                if str(view.get("uuid", "")) == wanted:
                    chosen = view
                    break
            if chosen is None:
                raise _fail("bad_view_reference", "view uuid is not in this document", view=wanted)
    candidates = [chosen] if chosen is not None else views
    for view in candidates:
        bounds = _bounds_of(view)
        if bounds is None:
            continue
        span = bounds["%s_max_nm" % axis] - bounds["%s_min_nm" % axis]
        if span > 0:
            return bounds, "view.bounds_model_nm", view
    bounds = _sketch_bounds(host)
    if bounds is not None:
        span = bounds["%s_max_nm" % axis] - bounds["%s_min_nm" % axis]
        if span > 0:
            return bounds, "sketch.entities", None
    raise _fail("tolerance_no_geometry",
                "no measurable geometry on the requested axis", axis=axis,
                views=len(views))


def _attach_view(host: Any, chosen: Mapping | None) -> tuple[MutableMapping, str]:
    if isinstance(chosen, MutableMapping):
        return chosen, str(chosen.get("uuid", ""))
    views = _get(host, "views")
    if isinstance(views, list):
        for view in views:
            if isinstance(view, MutableMapping):
                return view, str(view.get("uuid", ""))
        record: dict[str, Any] = {"uuid": _new_uuid(), "kind": TOLERANCE_KIND, "entities": []}
        views.append(record)
        return record, record["uuid"]
    if isinstance(host, MutableMapping):
        record = {"uuid": _new_uuid(), "kind": TOLERANCE_KIND, "entities": []}
        host["views"] = [record]
        return record, record["uuid"]
    raise _fail("tolerance_needs_document", "host has no views[] to carry dimension geometry")


def _append_entity(view_map: MutableMapping, entity: dict[str, Any]) -> None:
    entities = view_map.get("entities")
    if not isinstance(entities, list):
        entities = []
        view_map["entities"] = entities
    entities.append(entity)


# --------------------------------------------------------------------------------------
# 操作 1：尺寸公差
# --------------------------------------------------------------------------------------


def tolerance_dimension(sk_or_doc: Any, *, view: Any = None, axis: str = DEFAULT_AXIS,
                        nominal_nm: Any = None, tolerance: Any = DEFAULT_TOLERANCE,
                        offset_nm: int = DEFAULT_OFFSET_NM, sheet: Any = None, kernel: Any = None,
                        standard: Any = None, language: Any = None, size: Any = None,
                        scale: Any = None, **extra: Any) -> list[str]:
    """给图上一条尺寸加公差：名义尺寸可显式给，也可由真几何量取；返回新建对象的 uuid 列表。

    参数
    ----
    view          目标视图（uuid 或视图映射）；缺省取文档里第一个能量出跨度的视图
    axis          "x"（量宽）或 "y"（量高）
    nominal_nm    名义尺寸（整数纳米）；缺省 = 量到的跨度
    tolerance     公差规格：`"+/-0.1"` / `"+0.025/0"` / `"50.025/50.000"`，或等价 mapping
    offset_nm     尺寸线相对几何边缘的偏移（整数纳米，缺省 12 mm）

    真后果：annotation（`kind="tolerance_dimension"`，含名义/上下极限/公差带宽度/来源）+ 尺寸线
    与两条尺寸界线图元（落进目标视图 `entities[]`）。量不到几何、规格不合法一律抛 CadError。
    """
    _check_kwargs(extra)
    host, annotations = _document_host(sk_or_doc)
    bounds, measured_from, chosen = _measure(host, view, axis)
    if nominal_nm is None:
        nominal = bounds["%s_max_nm" % axis] - bounds["%s_min_nm" % axis]
        nominal_source = measured_from
    else:
        nominal = _as_int(nominal_nm, what="nominal_nm")
        nominal_source = "nominal_nm"
    if nominal <= 0:
        raise _fail("bad_nominal", "nominal size must be positive", nominal_nm=nominal)
    offset = _as_int(offset_nm, what="offset_nm")
    if offset <= 0:
        raise _fail("bad_length_nm", "offset_nm must be positive", offset_nm=offset)
    tol = _parse_tolerance(tolerance, nominal)
    upper_nm = nominal + tol["es_nm"]
    lower_nm = nominal + tol["ei_nm"]

    view_map, view_uuid = _attach_view(host, chosen)
    if axis == "x":
        y_base = bounds["y_min_nm"] - offset
        first = (bounds["x_min_nm"], bounds["y_min_nm"])
        second = (bounds["x_max_nm"], bounds["y_min_nm"])
        line = [(bounds["x_min_nm"], y_base), (bounds["x_max_nm"], y_base)]
    else:
        x_base = bounds["x_max_nm"] + offset
        first = (bounds["x_max_nm"], bounds["y_min_nm"])
        second = (bounds["x_max_nm"], bounds["y_max_nm"])
        line = [(x_base, bounds["y_min_nm"]), (x_base, bounds["y_max_nm"])]
    points_nm = [list(first), list(second), list(line[0]), list(line[1])]

    geometry_uuid = _new_uuid()
    entity = {"uuid": geometry_uuid, "kind": TOLERANCE_KIND, "layer": DIMENSION_LAYER,
              "axis": axis, "offset_nm": offset, "points_nm": points_nm,
              "dimension_line_nm": [list(line[0]), list(line[1])],
              "text": "%s %s/%s" % (_nm_to_mm_text(nominal),
                                    _signed_mm_text(tol["es_nm"]), _signed_mm_text(tol["ei_nm"]))}
    _append_entity(view_map, entity)

    record_uuid = _new_uuid()
    record = {"uuid": record_uuid, "kind": TOLERANCE_KIND, "view_uuid": view_uuid,
              "entity_uuid": geometry_uuid,
              "args": {"op": TOLERANCE_KIND, "tolerance": tol, "nominal_nm": nominal,
                       "upper_nm": upper_nm, "lower_nm": lower_nm, "width_nm": tol["width_nm"],
                       "axis": axis, "offset_nm": offset, "measured_from": nominal_source,
                       "nominal_from": nominal_source, "fit": NA,
                       "text": entity["text"]}}
    annotations.append(record)
    return [record_uuid, geometry_uuid]


# --------------------------------------------------------------------------------------
# 操作 2：配合
# --------------------------------------------------------------------------------------


def fit_dimension(sk_or_doc: Any, *, fit: str = "H7/f7", nominal_nm: Any = None, view: Any = None,
                  axis: str = DEFAULT_AXIS, offset_nm: int = DEFAULT_OFFSET_NM, sheet: Any = None,
                  kernel: Any = None, standard: Any = None, language: Any = None, size: Any = None,
                  scale: Any = None, **extra: Any) -> list[str]:
    """在图纸上做一条**配合**：孔/轴极限、最大最小间隙、配合类别，并落两个真圆图元。

    `fit` 形如 `"H7/f7"`（孔在前、轴在后）。名义尺寸缺省由真几何量取（同 `tolerance_dimension`）。
    孔圆半径取**孔的上极限**、轴圆半径取**轴的下极限**，即最不利装配状态的真实几何后果。
    返回 `[annotation_uuid, hole_circle_uuid, shaft_circle_uuid]`。
    """
    _check_kwargs(extra)
    host, annotations = _document_host(sk_or_doc)
    bounds, measured_from, chosen = _measure(host, view, axis)
    if nominal_nm is None:
        nominal = bounds["%s_max_nm" % axis] - bounds["%s_min_nm" % axis]
        nominal_source = measured_from
    else:
        nominal = _as_int(nominal_nm, what="nominal_nm")
        nominal_source = "nominal_nm"
    if nominal <= 0:
        raise _fail("bad_nominal", "nominal size must be positive", nominal_nm=nominal)
    resolved = resolve_fit(fit, nominal)

    center_x = (bounds["x_min_nm"] + bounds["x_max_nm"]) // 2
    center_y = (bounds["y_min_nm"] + bounds["y_max_nm"]) // 2
    hole_radius_nm = (nominal + resolved["hole"]["es_nm"]) // 2
    shaft_radius_nm = (nominal + resolved["shaft"]["ei_nm"]) // 2
    if hole_radius_nm <= 0 or shaft_radius_nm <= 0:
        raise _fail("bad_nominal", "limit diameter collapsed to zero or below", nominal_nm=nominal,
                    hole_radius_nm=hole_radius_nm, shaft_radius_nm=shaft_radius_nm)

    view_map, view_uuid = _attach_view(host, chosen)
    hole_uuid = _new_uuid()
    shaft_uuid = _new_uuid()
    _append_entity(view_map, {"uuid": hole_uuid, "kind": FIT_KIND, "layer": DIMENSION_LAYER,
                              "role": "hole_limit_upper", "center_nm": [center_x, center_y],
                              "radius_nm": hole_radius_nm, "it_nm": resolved["hole"]["it_nm"],
                              "es_nm": resolved["hole"]["es_nm"], "ei_nm": resolved["hole"]["ei_nm"],
                              "fit": resolved["fit"]})
    _append_entity(view_map, {"uuid": shaft_uuid, "kind": FIT_KIND, "layer": DIMENSION_LAYER,
                              "role": "shaft_limit_lower", "center_nm": [center_x, center_y],
                              "radius_nm": shaft_radius_nm, "it_nm": resolved["shaft"]["it_nm"],
                              "es_nm": resolved["shaft"]["es_nm"], "ei_nm": resolved["shaft"]["ei_nm"],
                              "fit": resolved["fit"]})
    text = "%s %s hole=%s/%s shaft=%s/%s" % (
        _nm_to_mm_text(nominal), resolved["fit"],
        _signed_mm_text(resolved["hole"]["es_nm"]), _signed_mm_text(resolved["hole"]["ei_nm"]),
        _signed_mm_text(resolved["shaft"]["es_nm"]), _signed_mm_text(resolved["shaft"]["ei_nm"]))

    record_uuid = _new_uuid()
    annotations.append({"uuid": record_uuid, "kind": FIT_KIND, "view_uuid": view_uuid,
                        "entity_uuids": [hole_uuid, shaft_uuid],
                        "args": {"op": FIT_KIND, "fit": resolved["fit"],
                                 "nominal_nm": nominal, "nominal_from": nominal_source,
                                 "size_range_mm": resolved["size_range_mm"],
                                 "hole_es_nm": resolved["hole"]["es_nm"],
                                 "hole_ei_nm": resolved["hole"]["ei_nm"],
                                 "shaft_es_nm": resolved["shaft"]["es_nm"],
                                 "shaft_ei_nm": resolved["shaft"]["ei_nm"],
                                 "max_clearance_nm": resolved["max_clearance_nm"],
                                 "min_clearance_nm": resolved["min_clearance_nm"],
                                 "fit_type": resolved["fit_type"],
                                 "hole_radius_nm": hole_radius_nm,
                                 "shaft_radius_nm": shaft_radius_nm,
                                 "tolerance": _tolerance_text("fit", resolved["hole"]["es_nm"],
                                                              resolved["hole"]["ei_nm"]),
                                 "text": text}})
    return [record_uuid, hole_uuid, shaft_uuid]


# --------------------------------------------------------------------------------------
# 机读事实与自检
# --------------------------------------------------------------------------------------


def module_facts() -> dict[str, Any]:
    """族模块机读事实（三态可辨：`0` 真为零 / `-1` 未采 / `"NA"` 不适用）。"""
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "tolerance_kind": TOLERANCE_KIND,
        "fit_kind": FIT_KIND,
        "dimension_layer": DIMENSION_LAYER,
        "default_tolerance": DEFAULT_TOLERANCE,
        "grades": sorted(GRADE_FACTORS),
        "fit_ranges_mm": [list(pair) for pair in FIT_RANGES_MM],
        "hole_letters": list(HOLE_LETTERS),
        "shaft_letters": list(SHAFT_LETTERS),
        "kernel": NA,
        "kernel_capabilities": UNMEASURED,
        "module_ops": len(OPERATIONS),
    }


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "tolerance_dimension": tolerance_dimension,
    "fit_dimension": fit_dimension,
}


def _selftest_document() -> dict[str, Any]:
    """自检夹具：60x40 mm 板（真草图实体 + 真视图外包框）的 L1 文档映射，不依赖夹具文件。"""
    sketch = {
        "uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.sketch")),
        "plane": "XY",
        "entities": [
            {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.l1")),
             "kind": "line", "p1_nm": [0, 0], "p2_nm": [60 * NM_PER_MM, 0]},
            {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.l2")),
             "kind": "line", "p1_nm": [60 * NM_PER_MM, 0], "p2_nm": [60 * NM_PER_MM, 40 * NM_PER_MM]},
        ],
        "constraints": [], "dimensions": [],
    }
    view = {
        "uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.view")),
        "kind": "standard_view", "view": "front", "entities": [],
        "bounds_model_nm": {"x_min_nm": 0, "y_min_nm": 0,
                            "x_max_nm": 60 * NM_PER_MM, "y_max_nm": 40 * NM_PER_MM},
    }
    sheet = {"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.sheet")),
             "views": [view]}
    return {"meta": {"doc_uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings_w11.selftest.doc"))},
            "sketches": [sketch], "features": [], "views": [view], "sheets": [sheet],
            "annotations": []}


def _reason_of(op: Callable[..., list[str]], *args: Any, **kwargs: Any) -> str:
    try:
        op(*args, **kwargs)
    except CadError as exc:
        return str(exc.reason)
    except Exception as exc:  # 非 CadError 一律暴露，不当成"预期报错"
        return "unexpected:%s" % type(exc).__name__
    return "no_error"


def _last_args(doc: Mapping[str, Any], kind: str) -> dict[str, Any]:
    found = [entry for entry in doc["annotations"] if entry.get("kind") == kind]
    return dict(found[-1]["args"]) if found else {}


def selftest() -> int:
    """文件自带自检：接口 + 两条操作的真后果 + ISO 286 表值锚点 + 边界 reason，逐条记账。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    check("family", FAMILY == "sw_drawings", FAMILY)
    check("operations", sorted(OPERATIONS) == ["fit_dimension", "tolerance_dimension"],
          ",".join(sorted(OPERATIONS)))

    # --- ISO 286 公式 vs 公布表值（外部锚点，不是自证） ---
    low, high = _size_range(50 * NM_PER_MM)
    check("iso.it7_50", _it_nm(7, low, high) == 25_000, "it7=%s" % _it_nm(7, low, high))
    check("iso.it6_50", _it_nm(6, low, high) == 16_000, "it6=%s" % _it_nm(6, low, high))
    check("iso.it8_50", _it_nm(8, low, high) == 39_000, "it8=%s" % _it_nm(8, low, high))
    check("iso.f_es_50", _fundamental_nm("f", low, high) == 25_000,
          "f=%s" % _fundamental_nm("f", low, high))
    check("iso.g_es_50", _fundamental_nm("g", low, high) == 9_000,
          "g=%s" % _fundamental_nm("g", low, high))

    # --- 操作 1：名义尺寸由真几何量取（60 mm），对称公差 ---
    doc = _selftest_document()
    before = _json.dumps(doc, sort_keys=True)
    out = OPERATIONS["tolerance_dimension"](doc)
    args = _last_args(doc, TOLERANCE_KIND)
    annos = {str(entry.get("uuid")) for entry in doc["annotations"]}
    check("tol.measured_nominal", args.get("nominal_nm") == 60 * NM_PER_MM
          and args.get("measured_from") == "view.bounds_model_nm",
          "nominal=%s from=%s" % (args.get("nominal_nm"), args.get("measured_from")))
    check("tol.limits", args.get("upper_nm") == 60_100_000 and args.get("lower_nm") == 59_900_000
          and args.get("width_nm") == 200_000,
          "upper=%s lower=%s width=%s" % (args.get("upper_nm"), args.get("lower_nm"), args.get("width_nm")))
    check("tol.doc_changed", _json.dumps(doc, sort_keys=True) != before, "changed=%s" % (before != _json.dumps(doc, sort_keys=True)))
    check("tol.uuids", len(out) == 2 and all(str(uid) in annos for uid in out[:1])
          and out[0] in annos, "returned=%d" % len(out))
    check("tol.geometry", len(_last_entity(doc)) == 4
          and _last_entity(doc)[0] == [0, 0], "points=%d" % len(_last_entity(doc)))
    check("tol.token", "tolerance_dimension" in _json.dumps(doc), "token=tolerance_dimension")

    # --- 操作 1：显式名义 + 非对称偏差 / 极限尺寸两种口径 ---
    asym = _selftest_document()
    OPERATIONS["tolerance_dimension"](asym, nominal_nm=50 * NM_PER_MM, tolerance="+0.025/0")
    asym_args = _last_args(asym, TOLERANCE_KIND)
    check("tol.deviation", asym_args.get("upper_nm") == 50_025_000 and asym_args.get("lower_nm") == 50_000_000,
          "upper=%s lower=%s" % (asym_args.get("upper_nm"), asym_args.get("lower_nm")))
    lim = _selftest_document()
    OPERATIONS["tolerance_dimension"](lim, nominal_nm=50 * NM_PER_MM, tolerance="50.025/50.000")
    lim_args = _last_args(lim, TOLERANCE_KIND)
    check("tol.limits_mode", lim_args.get("tolerance", {}).get("mode") == "limits"
          and lim_args.get("width_nm") == 25_000,
          "mode=%s width=%s" % (lim_args.get("tolerance", {}).get("mode"), lim_args.get("width_nm")))

    # --- 操作 1：确定性（同一输入两次得到同一批数） ---
    first, second = _selftest_document(), _selftest_document()
    OPERATIONS["tolerance_dimension"](first, nominal_nm=50 * NM_PER_MM, tolerance="+0.025/0")
    OPERATIONS["tolerance_dimension"](second, nominal_nm=50 * NM_PER_MM, tolerance="+0.025/0")
    check("tol.deterministic", _last_args(first, TOLERANCE_KIND) == _last_args(second, TOLERANCE_KIND),
          "args_equal=%s" % (_last_args(first, TOLERANCE_KIND) == _last_args(second, TOLERANCE_KIND)))

    # --- 操作 2：四条配合的真算术（教材口径） ---
    fit_doc = _selftest_document()
    fit_out = OPERATIONS["fit_dimension"](fit_doc, fit="H7/f7", nominal_nm=50 * NM_PER_MM)
    fit_args = _last_args(fit_doc, FIT_KIND)
    check("fit.H7f7", (fit_args.get("hole_es_nm"), fit_args.get("hole_ei_nm"),
                       fit_args.get("shaft_es_nm"), fit_args.get("shaft_ei_nm"))
          == (25_000, 0, -25_000, -50_000),
          "hole=%s/%s shaft=%s/%s" % (fit_args.get("hole_es_nm"), fit_args.get("hole_ei_nm"),
                                      fit_args.get("shaft_es_nm"), fit_args.get("shaft_ei_nm")))
    check("fit.H7f7.clearance", fit_args.get("max_clearance_nm") == 75_000
          and fit_args.get("min_clearance_nm") == 25_000 and fit_args.get("fit_type") == "clearance",
          "max=%s min=%s type=%s" % (fit_args.get("max_clearance_nm"), fit_args.get("min_clearance_nm"),
                                     fit_args.get("fit_type")))
    g_doc = _selftest_document()
    OPERATIONS["fit_dimension"](g_doc, fit="H7/g6", nominal_nm=50 * NM_PER_MM)
    g_args = _last_args(g_doc, FIT_KIND)
    check("fit.H7g6", g_args.get("max_clearance_nm") == 50_000 and g_args.get("min_clearance_nm") == 9_000,
          "max=%s min=%s" % (g_args.get("max_clearance_nm"), g_args.get("min_clearance_nm")))
    js_doc = _selftest_document()
    OPERATIONS["fit_dimension"](js_doc, fit="H7/js6", nominal_nm=50 * NM_PER_MM)
    js_args = _last_args(js_doc, FIT_KIND)
    check("fit.H7js6.transition", js_args.get("fit_type") == "transition"
          and js_args.get("min_clearance_nm") == -8_000 and js_args.get("max_clearance_nm") == 33_000,
          "type=%s max=%s min=%s" % (js_args.get("fit_type"), js_args.get("max_clearance_nm"),
                                     js_args.get("min_clearance_nm")))
    h_doc = _selftest_document()
    OPERATIONS["fit_dimension"](h_doc, fit="H7/h6", nominal_nm=50 * NM_PER_MM)
    h_args = _last_args(h_doc, FIT_KIND)
    check("fit.H7h6", h_args.get("max_clearance_nm") == 41_000 and h_args.get("min_clearance_nm") == 0
          and h_args.get("fit_type") == "clearance",
          "max=%s min=%s" % (h_args.get("max_clearance_nm"), h_args.get("min_clearance_nm")))
    check("fit.geometry", len(fit_out) == 3 and "tolerance_fit" in _json.dumps(fit_doc),
          "returned=%d" % len(fit_out))

    # --- 边界：一律显式 reason，不静默 ---
    empty = {"meta": {}, "sketches": [], "features": [], "views": [], "sheets": [], "annotations": []}
    check("err.no_geometry", _reason_of(OPERATIONS["tolerance_dimension"], empty) == "tolerance_no_geometry",
          _reason_of(OPERATIONS["tolerance_dimension"], empty))
    check("err.needs_document",
          _reason_of(OPERATIONS["tolerance_dimension"], {"meta": {}}) == "tolerance_needs_document",
          _reason_of(OPERATIONS["tolerance_dimension"], {"meta": {}}))
    check("err.bad_tolerance",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(),
                     nominal_nm=50 * NM_PER_MM, tolerance="+0.01/-0.02") == "bad_tolerance",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(),
                     nominal_nm=50 * NM_PER_MM, tolerance="+0.01/-0.02"))
    check("err.fit_out_of_scope",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H11/f7", nominal_nm=50 * NM_PER_MM) == "fit_out_of_scope",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H11/f7", nominal_nm=50 * NM_PER_MM))
    check("err.fit_range",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H7/f7", nominal_nm=200 * NM_PER_MM) == "fit_out_of_scope",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H7/f7", nominal_nm=200 * NM_PER_MM))
    check("err.bad_fit",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H7", nominal_nm=50 * NM_PER_MM) == "bad_fit",
          _reason_of(OPERATIONS["fit_dimension"], _selftest_document(),
                     fit="H7", nominal_nm=50 * NM_PER_MM))
    check("err.unexpected_kwargs",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(), magic=1) == "unexpected_kwargs",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(), magic=1))
    check("err.bad_view_reference",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(), view="nope") == "bad_view_reference",
          _reason_of(OPERATIONS["tolerance_dimension"], _selftest_document(), view="nope"))

    # --- 真件自证：夹具确实是文档映射、且自检不产生外部副作用 ---
    probe = _selftest_document()
    check("fixture.shape", isinstance(probe, dict) and probe["annotations"] == []
          and _bounds_of(probe["views"][0]) is not None, "views=%d" % len(probe["views"]))
    check("fixture.no_side_effect", _sketch_bounds(probe) is not None
          and _sketch_bounds(_copy.deepcopy(probe)) == _sketch_bounds(probe),
          "sketch_bounds=%s" % (_sketch_bounds(probe),))

    passed = sum(1 for _name, ok, _detail in checks if ok)
    for name, ok, detail in checks:
        if not ok:
            print("CHECK_FAIL %s %s" % (name, detail))
    verdict = "PASS" if passed == len(checks) else "FAIL"
    print("FEATURE_OK family=sw_drawings op=tolerance_dimension verdict=%s" % verdict)
    print("SW_DRAWINGS_W11 verdict=%s checks=%d/%d ops=%d" % (verdict, passed, len(checks), len(OPERATIONS)))
    return 0 if passed == len(checks) else 1


def _last_entity(doc: Mapping[str, Any]) -> list:
    """自检用：取目标视图 `entities[]` 里最后一条图元的点串。"""
    views = _views_of(doc)
    for view in views:
        entities = view.get("entities")
        if isinstance(entities, list) and entities:
            return list(entities[-1].get("points_nm", []))
    return []


if __name__ == "__main__":
    raise SystemExit(selftest())
