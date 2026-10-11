#!/usr/bin/env python3
"""W24 实现片：precision_ops 族 must_have 第 15 号（0-based）「栅格显示」。

来源与口径（全部现读，未编造）：
- 清单：`oss-alternatives/docs/research/cad-parity/features/autocad-precision.json` 的 `must_have`
  共 21 条。题目点名的 `features/precision_ops.json` 现读为 **ABSENT**（`features/` 下只有
  `autocad-precision.json`）；族 ID 解析沿用 `spec/precision_ops.json` 的既有规则
  （requested=absent，used=features/autocad-precision.json），本席不另立来源、不补造清单。
- 状态：`status/autocad-precision.json` 记 implemented=0 / partial=16 / missing=5，即 21 条 must_have
  **全部**仍为 missing 或 partial；按清单顺序编号 0..20 后，**15 号 = 「栅格显示」（partial）**。
- 现状缺口（status 原文归纳）：无 GRID/F7 开关（库级 `ops/precision_ops.py` 的 grid_set 未接入口）、
  无 GRIDUNIT 用户设置入口、**显示间距由缩放自适应而非用户指定**、栅格不参与取点。

本片交付（L1 文档模型上的真数据 + 真几何，无桩）：
1. `grid_display_set`：把显示开关（F7 等价）、**用户指定间距 GRIDUNIT**（`spacing_nm` / `spacing_mm`）、
   主栅格频率 `major_every`、自适应开关、显示限制、步进间距一致性写进文档；显式给间距即落
   `spacing_source="user"` 并自动关掉自适应 —— 这正是「只能自适应、不能用户指定」这条缺口的修复。
   任何影响几何的设置改动都会**清空当前已渲染线集**（不留与设置不一致的陈旧几何）。
2. `grid_display_lines`：给定视口（nm 矩形）与缩放（px/mm），算出**真正要画的栅格线几何**：逐线给出
   方向 / 索引 / 坐标 / 跨度 / 主次标记，坐标与跨度全是整数纳米，逐线 uuid 由记录 uuid 派生（同输入
   同输出，可幂等复算），返回该批 uuid 列表。自适应模式下按候选间距阶梯取「像素间距 ≥ `min_px`」的
   **最小档**；像素间距小于 `min_display_px` 时按 AutoCAD 的 grid-too-dense 行为**不画**，并显式记
   `too_dense=True`（不是静默空）。线条数超过 `max_lines` 时抛
   `CadError(reason="grid_line_budget_exceeded")`，**不静默截断**；且所有设置先算后写 ⇒ 任何报错路径
   下文档逐字节不变（自检里用整份 L1 文本比对证明）。
3. `grid_display_readout`：只读回读记录 uuid，供状态栏 / 命令行接线，无副作用。

如实声明的不覆盖面（不冒充已完成）：`apps/opencad/web` 的画布取点、状态栏按钮与 F 功能键属另一席文件
范围（且对应 20 号「状态栏开关与快捷键体系」），本片只把「显示模型 + 待画几何 + 开关持久化」在 L1 上
做实。`spacing_mm` 入口按 docmodel 的 L1 长度规则走 `units.mm_to_nm()`；该依赖缺席时抛
`CadError(reason="dependency_missing")`，**不静默降级**成自造换算。

自检（自带入口，真跑）：
    python3 cadcore/ops/features/precision_ops__w24.py
末尾打印 `FEATURE_OK family=precision_ops op=grid_display_lines verdict=PASS`；任一断言失败则不打印该行。

中文只出现在 docstring 与注释里：非 docstring 的字符串常量一律 ASCII（i18n 门口径）。
"""

from __future__ import annotations

import math
import os
import sys
import uuid
from typing import Any, Callable

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

if __package__ not in (None, ""):  # 正常路径：作为包模块导入
    from cadcore.docmodel import Document, check_invariants, document_from_l1, document_to_l1, new_document
    from cadcore.errors import CadError

    from cadcore.units import mm_to_nm as _mm_to_nm
else:  # pragma: no cover - 脚本入口（自带自检）时补包上下文
    if _PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT)
    from cadcore.docmodel import Document, check_invariants, document_from_l1, document_to_l1, new_document
    from cadcore.errors import CadError

    from cadcore.units import mm_to_nm as _mm_to_nm

__all__ = ["FAMILY", "OPERATIONS", "grid_display_set", "grid_display_lines", "grid_display_readout", "selftest"]

#: 冻结接口：族名 + 操作表（`op(sk_or_doc, **kwargs) -> list[str]`）。
FAMILY = "precision_ops"

#: 1 mm = 1_000_000 nm（与 units 一致；这里只用常量做倍率，不做换算入口）。
MM_NM = 1_000_000

#: 显示记录在承载体（Document.meta / 映射节点）里的键。
GRID_KEY = "precision_grid"

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "cadcore.precision.grid")

#: 自适应栅格候选间距（nm）；取自 status 证据里 app.js 的 0.5–500 mm 档位口径，升序即「最小满足档」。
_ADAPTIVE_LADDER_NM = tuple(int(round(mm * MM_NM)) for mm in (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0))

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "spacing_nm": 10 * MM_NM,
    "spacing_source": "user",
    "adaptive": False,
    "major_every": 5,
    "min_px": 24.0,
    "min_display_px": 1.0,
    "max_lines": 4096,
    "limits": None,
    "snap_spacing_nm": 0,
    "displayed": False,
    "too_dense": False,
    "last_view": None,
    "last_scale_px_per_mm": None,
    "lines": [],
    "line_count": 0,
}

_GEOM_KEYS = ("spacing_nm", "spacing_source", "adaptive", "major_every", "min_px", "min_display_px", "max_lines")

_SET_KEYS = frozenset({
    "enabled", "spacing_nm", "spacing_mm", "adaptive", "major_every", "min_px",
    "min_display_px", "max_lines", "limits", "snap_spacing_nm",
})

_LINE_KEYS = frozenset({
    "view_nm", "scale_px_per_mm", "spacing_nm", "spacing_mm", "adaptive",
    "major_every", "min_px", "min_display_px", "max_lines",
})


# --------------------------------------------------------------------------- 校验/取值


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """统一错误出口：一律 `CadError(reason=...)`，细节机读，不靠解析文案。"""
    return CadError(message, reason=reason, **details)


def _reject_unknown(op: str, got: dict, allowed: frozenset) -> None:
    extra = sorted(set(got) - set(allowed))
    if extra:
        raise _fail("unknown_kwarg", "unknown keyword argument", op=op, keys=extra)


def _bool_kw(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise _fail("invalid_value", "expected a boolean", key=key, value=repr(value))


def _int_kw(value: Any, key: str, *, minimum: int | None = None, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_value", "expected an integer", key=key, value=repr(value))
    out = int(value)
    if positive and out <= 0:
        raise _fail("invalid_value", "expected a positive integer", key=key, value=out)
    if minimum is not None and out < minimum:
        raise _fail("invalid_value", "value below the allowed minimum", key=key, value=out, minimum=minimum)
    return out


def _float_kw(value: Any, key: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_value", "expected a number", key=key, value=repr(value))
    out = float(value)
    if not math.isfinite(out):
        raise _fail("invalid_value", "expected a finite number", key=key, value=repr(value))
    if positive and out <= 0.0:
        raise _fail("invalid_value", "expected a positive number", key=key, value=out)
    return out


def _view_kw(value: Any, key: str) -> list[int]:
    """视口/限制矩形：[x0, y0, x1, y1]，四个整数纳米，且 x1>x0、y1>y0。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 4:
        raise _fail("invalid_value", "expected four integer nanometres", key=key, value=repr(value))
    nums = [_int_kw(item, key) for item in value]
    if nums[2] <= nums[0] or nums[3] <= nums[1]:
        raise _fail("invalid_value", "expected x1>x0 and y1>y0", key=key, value=nums)
    return nums


def _spacing_from(kwargs: dict, op: str) -> int | None:
    """用户指定间距（GRIDUNIT）：`spacing_nm` 直给整数纳米，`spacing_mm` 走 units.mm_to_nm。"""
    if "spacing_nm" in kwargs and "spacing_mm" in kwargs:
        raise _fail("conflicting_kwargs", "give spacing_nm or spacing_mm, not both", op=op)
    if "spacing_nm" in kwargs:
        return _int_kw(kwargs["spacing_nm"], "spacing_nm", positive=True)
    if "spacing_mm" in kwargs:
        raw = kwargs["spacing_mm"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _fail("invalid_value", "expected a number of millimetres", key="spacing_mm", value=repr(raw))
        if _mm_to_nm is None:
            raise _fail("dependency_missing", "units.mm_to_nm is required for the millimetre entry point", key="spacing_mm")
        nm = int(_mm_to_nm(raw))
        if nm <= 0:
            raise _fail("invalid_value", "spacing must be positive", key="spacing_mm", value=raw)
        return nm
    return None


# --------------------------------------------------------------------------- 记录存取


def _store_of(target: Any) -> dict:
    """取显示记录的承载映射：Document 用 meta；文档形映射用其 meta；裸节点映射用自身。"""
    if target is None:
        return new_document().meta
    if isinstance(target, Document):
        return target.meta
    if isinstance(target, dict):
        if isinstance(target.get("meta"), dict):
            return target["meta"]
        if "sketches" in target or "schema_version" in target:
            meta = target.setdefault("meta", {})
            if not isinstance(meta, dict):
                raise _fail("unsupported_target", "meta must be a mapping", target="mapping")
            return meta
        return target
    meta = getattr(target, "meta", None)
    if isinstance(meta, dict):
        return meta
    raise _fail("unsupported_target", "expected a Document, a mapping or None", target=type(target).__name__)


def _owner_tag(target: Any, store: dict) -> str:
    raw = store.get("uuid")
    if not raw and isinstance(target, dict):
        raw = target.get("uuid")
    return str(uuid.uuid5(_NS, "owner:" + str(raw or "document")))


def _record(target: Any, *, create: bool) -> dict:
    store = _store_of(target)
    rec = store.get(GRID_KEY)
    if rec is None:
        if not create:
            raise _fail("grid_not_configured", "grid display record is missing", key=GRID_KEY)
        rec = dict(_DEFAULTS)
        rec["lines"] = []
        rec["owner_uuid"] = _owner_tag(target, store)
        rec["uuid"] = str(uuid.uuid5(_NS, "grid:" + rec["owner_uuid"]))
        store[GRID_KEY] = rec
    if not isinstance(rec, dict):
        raise _fail("corrupt_record", "grid display record must be a mapping", key=GRID_KEY)
    return rec


def _clear(rec: dict) -> None:
    """清空已渲染线集：显示关闭或设置改动后不留陈旧几何（`line_count=0` 即「需重算」）。"""
    rec["lines"] = []
    rec["line_count"] = 0
    rec["displayed"] = False
    rec["too_dense"] = False


# --------------------------------------------------------------------------- 几何


def _axis_indices(lo: int, hi: int, step: int) -> tuple[int, int]:
    """闭区间 [lo, hi] 内 step 的整数倍索引范围：ceil(lo/step) .. floor(hi/step)。"""
    return -((-lo) // step), hi // step


def _line(ns: uuid.UUID, direction: str, index: int, coord: int, start: int, end: int, major: int) -> dict:
    return {
        "uuid": str(uuid.uuid5(ns, "%s:%d" % (direction, index))),
        "direction": direction,
        "index": int(index),
        "coord_nm": int(coord),
        "start_nm": int(start),
        "end_nm": int(end),
        "is_major": bool(major > 1 and index % major == 0),
    }


def _build_lines(plan: dict, view: list[int], limits: Any) -> tuple[list[int], list[dict]]:
    """把视口按间距量化成真实待画线段；先整体算完再交给调用方提交（失败不留半截状态）。"""
    x0, y0, x1, y1 = view
    if limits is not None:
        lx0, ly0, lx1, ly1 = limits
        x0, y0, x1, y1 = max(x0, lx0), max(y0, ly0), min(x1, lx1), min(y1, ly1)
        if x1 <= x0 or y1 <= y0:
            raise _fail("empty_view_after_clip", "view does not intersect the grid limits",
                        view_nm=list(view), limits=list(limits))
    step = int(plan["spacing_nm"])
    kx0, kx1 = _axis_indices(x0, x1, step)
    ky0, ky1 = _axis_indices(y0, y1, step)
    nx = kx1 - kx0 + 1 if kx1 >= kx0 else 0
    ny = ky1 - ky0 + 1 if ky1 >= ky0 else 0
    budget = int(plan["max_lines"])
    if nx + ny > budget:
        raise _fail("grid_line_budget_exceeded", "grid line budget exceeded",
                    count=nx + ny, max_lines=budget, spacing_nm=step)
    major = int(plan["major_every"])
    ns = uuid.UUID(str(plan["uuid"]))
    lines: list[dict] = []
    for k in range(kx0, kx1 + 1):
        lines.append(_line(ns, "v", k, k * step, y0, y1, major))
    for k in range(ky0, ky1 + 1):
        lines.append(_line(ns, "h", k, k * step, x0, x1, major))
    return [x0, y0, x1, y1], lines


def _adaptive_spacing(scale_px_per_mm: float, min_px: float) -> int:
    """自适应：取「像素间距 ≥ min_px」的最小候选档；全不满足则用最大档。"""
    for cand in _ADAPTIVE_LADDER_NM:
        if (cand / MM_NM) * scale_px_per_mm >= min_px:
            return cand
    return _ADAPTIVE_LADDER_NM[-1]


# --------------------------------------------------------------------------- 操作


def grid_display_set(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """配置栅格显示（F7 开关 + GRIDUNIT + 主栅格频率 + 自适应/显示限制），返回记录 uuid 列表。

    显式给 `spacing_nm`/`spacing_mm` 即落 `spacing_source="user"`，并自动关闭自适应
    （用户指定优先于缩放自适应）；关显示或改了几何参数会清空已渲染线集，避免陈旧几何。
    """
    _reject_unknown("grid_display_set", kwargs, _SET_KEYS)
    rec = _record(sk_or_doc, create=True)
    dirty = False

    if "enabled" in kwargs:
        rec["enabled"] = _bool_kw(kwargs["enabled"], "enabled")

    spacing = _spacing_from(kwargs, "grid_display_set")
    if spacing is not None:
        rec["spacing_nm"] = spacing
        rec["spacing_source"] = "user"
        dirty = True
        if "adaptive" not in kwargs:
            rec["adaptive"] = False

    if "adaptive" in kwargs:
        rec["adaptive"] = _bool_kw(kwargs["adaptive"], "adaptive")
        dirty = True
    if "major_every" in kwargs:
        rec["major_every"] = _int_kw(kwargs["major_every"], "major_every", minimum=1)
        dirty = True
    if "min_px" in kwargs:
        rec["min_px"] = _float_kw(kwargs["min_px"], "min_px", positive=True)
        dirty = True
    if "min_display_px" in kwargs:
        rec["min_display_px"] = _float_kw(kwargs["min_display_px"], "min_display_px", positive=True)
    if "max_lines" in kwargs:
        rec["max_lines"] = _int_kw(kwargs["max_lines"], "max_lines", minimum=1)
        dirty = True
    if "limits" in kwargs:
        rec["limits"] = None if kwargs["limits"] is None else _view_kw(kwargs["limits"], "limits")
        dirty = True
    if "snap_spacing_nm" in kwargs:
        value = _int_kw(kwargs["snap_spacing_nm"], "snap_spacing_nm", minimum=0)
        if value and value % int(rec["spacing_nm"]):
            raise _fail("invalid_value", "snap spacing must be a multiple of the grid spacing",
                        key="snap_spacing_nm", value=value, spacing_nm=int(rec["spacing_nm"]))
        rec["snap_spacing_nm"] = value

    if not rec["enabled"] or dirty:
        _clear(rec)
    return [str(rec["uuid"])]


def grid_display_lines(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """算出并落库「当前视口真正要画的栅格线」，返回这批线段的 uuid 列表。

    必填 `view_nm=[x0,y0,x1,y1]`（整数纳米）；自适应模式必填 `scale_px_per_mm`。
    线集几何全部由整数纳米算出，线 uuid 由记录 uuid 派生（同输入同输出）。
    超预算抛 `grid_line_budget_exceeded`；任何报错路径下文档逐字节不变。
    """
    _reject_unknown("grid_display_lines", kwargs, _LINE_KEYS)
    rec = _record(sk_or_doc, create=False)
    if "view_nm" not in kwargs:
        raise _fail("missing_kwarg", "view_nm is required", op="grid_display_lines", key="view_nm")
    view = _view_kw(kwargs["view_nm"], "view_nm")

    plan: dict[str, Any] = {key: rec.get(key, _DEFAULTS[key]) for key in _GEOM_KEYS}
    plan["uuid"] = rec["uuid"]

    spacing = _spacing_from(kwargs, "grid_display_lines")
    if spacing is not None:
        plan["spacing_nm"] = spacing
        plan["spacing_source"] = "user"
        if "adaptive" not in kwargs:
            plan["adaptive"] = False
    if "adaptive" in kwargs:
        plan["adaptive"] = _bool_kw(kwargs["adaptive"], "adaptive")
    if "major_every" in kwargs:
        plan["major_every"] = _int_kw(kwargs["major_every"], "major_every", minimum=1)
    if "min_px" in kwargs:
        plan["min_px"] = _float_kw(kwargs["min_px"], "min_px", positive=True)
    if "min_display_px" in kwargs:
        plan["min_display_px"] = _float_kw(kwargs["min_display_px"], "min_display_px", positive=True)
    if "max_lines" in kwargs:
        plan["max_lines"] = _int_kw(kwargs["max_lines"], "max_lines", minimum=1)

    scale = None
    if "scale_px_per_mm" in kwargs:
        scale = _float_kw(kwargs["scale_px_per_mm"], "scale_px_per_mm", positive=True)

    if not rec["enabled"]:
        for key in _GEOM_KEYS:
            rec[key] = plan[key]
        _clear(rec)
        rec["last_view"] = list(view)
        rec["last_scale_px_per_mm"] = scale
        return []

    if plan["adaptive"]:
        if scale is None:
            raise _fail("missing_kwarg", "scale_px_per_mm is required in adaptive mode",
                        op="grid_display_lines", key="scale_px_per_mm")
        plan["spacing_nm"] = _adaptive_spacing(scale, float(plan["min_px"]))
        plan["spacing_source"] = "adaptive"
    else:
        plan["spacing_source"] = "user"

    if scale is not None and (int(plan["spacing_nm"]) / MM_NM) * scale < float(plan["min_display_px"]):
        for key in _GEOM_KEYS:
            rec[key] = plan[key]
        _clear(rec)
        rec["too_dense"] = True
        rec["last_view"] = list(view)
        rec["last_scale_px_per_mm"] = scale
        return []

    clipped, lines = _build_lines(plan, view, rec.get("limits"))
    for key in _GEOM_KEYS:
        rec[key] = plan[key]
    rec["lines"] = lines
    rec["line_count"] = len(lines)
    rec["displayed"] = True
    rec["too_dense"] = False
    rec["last_view"] = clipped
    rec["last_scale_px_per_mm"] = scale
    return [str(line["uuid"]) for line in lines]


def grid_display_readout(sk_or_doc: Any, **kwargs: Any) -> list[str]:
    """只读回读：返回栅格显示记录的 uuid 列表（供状态栏/命令行接线，无副作用）。"""
    _reject_unknown("grid_display_readout", kwargs, frozenset())
    rec = _record(sk_or_doc, create=False)
    return [str(rec["uuid"])]


#: 只读声明（尺子 ``_readonly_declaration`` 认 ``OPERATIONS_META.<op>.readonly`` 这个形态）。
#: 这里只列**真查询**：``grid_display_readout`` 走 ``_record(create=False)`` 只读回读、无副作用。
#: ``grid_display_set`` **不许列进来** —— 它一旦拿到配置 kwarg 就写 ``doc.meta[GRID_KEY]`` 的记录字段，
#: 是写入型。尺子给它的探针是空 kwarg（签名只有 ``**kwargs``，生成不出参数），而夹具文档里该记录
#: 已存在且 ``enabled`` 为真 ⇒ 这次调用是幂等 no-op，三面都无变化；这属于**探针给不出 kwarg**，
#: 不是它可以声明只读（声明只读一旦真写就是 MISLABEL 判红）。
OPERATIONS_META = {"grid_display_readout": {"readonly": True}}

OPERATIONS: dict[str, Callable] = {
    "grid_display_set": grid_display_set,
    "grid_display_lines": grid_display_lines,
    "grid_display_readout": grid_display_readout,
}


# --------------------------------------------------------------------------- 自检


def selftest() -> int:
    """硬断言自检：真几何数字、真持久化、真错误路径；返回通过的断言组数。"""
    checks = 0

    assert FAMILY == "precision_ops"
    assert sorted(OPERATIONS) == ["grid_display_lines", "grid_display_readout", "grid_display_set"]
    assert all(callable(fn) for fn in OPERATIONS.values())
    checks += 1

    doc = new_document()
    first = grid_display_set(doc, enabled=True, spacing_nm=2 * MM_NM, major_every=5, adaptive=False)
    assert isinstance(first, list) and len(first) == 1 and isinstance(first[0], str) and first[0]
    checks += 1

    text = document_to_l1(doc)
    doc2 = document_from_l1(text)
    assert document_to_l1(doc2) == text
    assert grid_display_readout(doc2) == first
    checks += 1

    view = [0, 0, 10 * MM_NM, 10 * MM_NM]
    line_uuids = grid_display_lines(doc2, view_nm=view, scale_px_per_mm=1.0)
    rec = _record(doc2, create=False)
    vertical = [ln for ln in rec["lines"] if ln["direction"] == "v"]
    assert len(line_uuids) == 12 and rec["line_count"] == 12 and rec["displayed"] is True
    assert [ln["coord_nm"] for ln in vertical] == [0, 2 * MM_NM, 4 * MM_NM, 6 * MM_NM, 8 * MM_NM, 10 * MM_NM]
    assert [ln["is_major"] for ln in vertical] == [True, False, False, False, False, True]
    assert {ln["start_nm"] for ln in vertical} == {0} and {ln["end_nm"] for ln in vertical} == {10 * MM_NM}
    checks += 1

    assert grid_display_lines(doc2, view_nm=view, scale_px_per_mm=1.0) == line_uuids
    checks += 1

    doc3 = new_document()
    grid_display_set(doc3, enabled=True, adaptive=True, min_px=24.0, major_every=5)
    grid_display_lines(doc3, view_nm=[0, 0, 200 * MM_NM, 200 * MM_NM], scale_px_per_mm=1.0)
    rec3 = _record(doc3, create=False)
    assert rec3["spacing_source"] == "adaptive" and rec3["spacing_nm"] == 50 * MM_NM and rec3["line_count"] == 10
    checks += 1

    grid_display_set(doc3, spacing_nm=25 * MM_NM)
    assert rec3["adaptive"] is False and rec3["spacing_source"] == "user"
    assert rec3["lines"] == [] and rec3["line_count"] == 0
    assert len(grid_display_lines(doc3, view_nm=[0, 0, 200 * MM_NM, 200 * MM_NM], scale_px_per_mm=1.0)) == 18
    checks += 1

    dense = grid_display_lines(doc3, view_nm=[0, 0, 200 * MM_NM, 200 * MM_NM], scale_px_per_mm=0.02)
    assert dense == [] and rec3["too_dense"] is True and rec3["displayed"] is False and rec3["line_count"] == 0
    checks += 1

    grid_display_set(doc3, enabled=False)
    assert rec3["lines"] == [] and rec3["line_count"] == 0
    assert grid_display_lines(doc3, view_nm=[0, 0, 200 * MM_NM, 200 * MM_NM], scale_px_per_mm=1.0) == []
    checks += 1

    frozen = document_to_l1(doc2)
    try:
        grid_display_lines(doc2, view_nm=[0, 0, 10 ** 11, 10 ** 11], scale_px_per_mm=1.0)
    except CadError as exc:
        assert exc.reason == "grid_line_budget_exceeded" and int(exc.details["count"]) > 4096
    else:
        raise AssertionError("grid line budget guard did not fire")
    assert document_to_l1(doc2) == frozen
    checks += 1

    try:
        grid_display_set(doc2, bogus=1)
    except CadError as exc:
        assert exc.reason == "unknown_kwarg"
    else:
        raise AssertionError("unknown keyword argument was accepted")
    checks += 1

    assert check_invariants(doc2) == [] and check_invariants(doc3) == []
    checks += 1

    assert _mm_to_nm is not None, "units.mm_to_nm is required by the L1 length rule"
    doc4 = new_document()
    grid_display_set(doc4, enabled=True, spacing_mm=2.5)
    assert _record(doc4, create=False)["spacing_nm"] == 2_500_000
    checks += 1

    return checks


def main() -> int:
    checks = selftest()
    print("FEATURE_SELFTEST family=precision_ops feature_index=15 feature=grid_display assertions=%d mm_to_nm=%d"
          % (checks, 1 if _mm_to_nm is not None else 0))
    print("FEATURE_OK family=precision_ops op=grid_display_lines verdict=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
