#!/usr/bin/env python3
"""hatch_measure_extra —— 填充/测量族的**批量补全层**（多区域规格调度 + token 归因，不重算几何）。

一句话用途
----------
给 `cadcore.ops.hatch_measure`（base 族）的「填充边界识别 + 面积/周长/距离测量」补一批**批量接口**：
一次调用处理**多个区域规格 / 多对距离操作数**，逐项给出带 `item=` 归因的 token 块；
本层**不重写几何算法**，面积/周长/距离一律委托 base 族同名 op，自己只负责
「多规格调度 + 归因 + 无环/越界/空选的批量语义」。

族接口（冻结，与 base 族同一份契约）
------------------------------------
- ``FAMILY = "hatch_measure_extra"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数（含别名；别名与主名**同一函数对象**）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：**第一个位置参叫 `sk_or_doc`**，其余参数一律
  KEYWORD-ONLY。只读测量 op 返回 token 列表；唯一的写 op ``hatch_fill_batch`` 返回**受影响实体 uuid 列表**。
- 错误一律抛 ``cadcore.errors.CadError(reason=...)``，且**任何**逐项失败都带 `details["item"]` 与
  `details["spec"]`（机器可读归因，不靠解析文案）。

批量语义（写清，别让下游猜）
----------------------------
- **fail-fast**：批量里**任何一项**失败 ⇒ 整批当场抛错，**不静默跳过、不返回半批结果**。
- `regions` 的每个元素是一个"区域规格"：`None`（整图：外环 − 洞）/ `int`（第 i 个闭合环，按面积降序，
  同 `boundary_detect` 的顺序）/ `uuid` 字符串 / **uuid 序列**。规格本身是 uuid 序列时要写成
  **嵌套序列**（`regions=[["a", "b"]]` = 一个规格；`regions=["a", "b"]` = 两个规格）。
- `regions=None` ⇒ 单规格 `[None]`，**不是**"每个环一项"；要逐环请传 `regions=list(range(n))`，
  n 由 `boundary_detect_batch(regions=[None])` 的 `loops=` 读出。
- 批量条数上限 `_MAX_BATCH_ITEMS = 4096` ⇒ 超限报 `reason="too_many_items"`（不静默截断）。

三态口径（与 base 族/AGENTS.md E2.3 一致，三者不许同形）
-------------------------------------------------------
- `0` = **真为零**（例：区域里 `loops=0`；点-实体最近距离真的为 0）；
- `NA` = **不适用**（没有闭合环 ⇒ 面积/周长不适用；`boundary=NA` ⇒ 该项没有边界成员）；
- `-1` = **未采/不存在**（本层 `loop=-1` = "该规格下没有任何闭合环"，与 `loop=0` 的真实第一环不同形）。

批量 token 约定（逐字写清，下游照此切块）
----------------------------------------
- 每项一个块，块首 token 固定 ``item=<下标>``；整批末尾固定一个 ``batch_count=<项数>``。
- `boundary_detect_batch`：**每个闭合环一个块**
  ``item=<i> loop=<j> spec=<标签> loops=<该区域环数> boundary=<uuid,...> area_nm2=<int> perimeter_nm=<int> closed=1``；
  该区域无闭合环 ⇒ **恰好一个块**
  ``item=<i> loop=-1 spec=<标签> loops=0 boundary=NA area_nm2=NA perimeter_nm=NA closed=0``。
- `measure_area_batch` / `measure_perimeter_batch`：块 = ``item=<i> spec=<标签>`` + base op 的**原样 token**
  （数量与顺序都不变，本层不加料也不删料）。
- `measure_distance_batch`：块 = ``item=<i> a=<操作数A> b=<操作数B>`` + base `measure_distance` 原样 5 token；
  操作数标签为 ``pt=<x_nm>,<y_nm>`` 或 ``ref=<uuid>``。`pairs` 的每一项是：①操作数映射
  （键只能取 `p1_nm`/`p2_nm`/`ref_a`/`ref_b`）；②二元简写（元素是点对 `(x_nm, y_nm)` 或 uuid 字符串）。
  `pairs` 本身也可以直接给**单个** pair（映射或二元简写），本层按形状判定。
- `region_report_batch`：块 = ``item=<i> spec=<标签> loops=<n> boundaries=<uuid,...|NA>`` +
  base `measure_area` 原样 token（一次调用同时拿"边界成员 + 面积/周长"）。
- `spec=` 标签取值：``all``（None）/ ``loop:<i>``（整数下标）/ ``uuid:<uuid>`` / ``refs:<n>``。
- `hatch_fill_batch` **返回扁平 uuid 列表**（写 op 口径）：顺序 = 规格顺序 × 该规格内创建顺序；
  逐规格归属可由 `hatch_list` / `hatch_query` 记录里的 `boundary` 字段还原（不另造字段）。

有意限定的边界（写下来，不假装）
------------------------------
1. **不新增实体种类、不改 base 族一个字节**：只调用 base 的公开 op（`boundary_detect` / `measure_area` /
   `measure_perimeter` / `measure_distance` / `hatch_create`）与它自己的入参校验器（`_nm` / `_fail` /
   `_sketch_of` / `_subset` / `_closed_loops` / `_gap_tolerance` / `DEFAULT_HATCH_LAYER`）。
2. 参数形状是**强校验**的：形状不对 ⇒ `invalid_argument`；下标越界 ⇒ `index_out_of_range`；
   空序列 ⇒ `empty_selection`；未知 uuid ⇒ base 的 `unknown_reference`。**不静默 no-op**。
3. `hatch_fill_batch` **先预检**全部规格都有闭合环，**再**逐项创建 ⇒ 预检失败整批一条图案线都不落。
   残余风险只剩"创建阶段单项失败"（如 `too_many_lines`）：此时**该项之前的规格已落盘** —— base 的单次调用
   语义不跨项事务化，本层**不声称跨项原子**，只保证 `CadError.details["item"]` 指明失败项。
4. 本层**不做规格去重**：两个规格指向同一组边界 ⇒ 图案线叠加两次（规格是调用方给的，本层不替它决定）。

自检（不依赖 pytest；末行就是判定行）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/hatch_measure_extra.py
    # EXTRA_OPS verdict=PASS ops=N

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 的口径：非 docstring 的含汉字字符串常量计违规）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping as _MappingABC
from collections.abc import Sequence as _SequenceABC
from typing import Any, Callable

import os as _os
import sys as _sys

_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:  # 直跑脚本与包内导入共用同一条绝对导入路径
    _sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError
from cadcore.ops import hatch_measure as _hm

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "boundary_detect_batch",
    "measure_area_batch",
    "measure_perimeter_batch",
    "measure_distance_batch",
    "region_report_batch",
    "hatch_fill_batch",
    "selftest",
]

FAMILY = "hatch_measure_extra"

#: 悬空保护：单批条数上限（超限报 `too_many_items`，不静默截断）。
_MAX_BATCH_ITEMS = 4096

#: `measure_distance_batch` 操作数映射允许的键（与 base `measure_distance` 的 kwargs 同名）。
_OPERAND_KEYS = ("p1_nm", "p2_nm", "ref_a", "ref_b")

#: 自检夹具用：1 mm = 1e6 nm。
_NM_PER_MM = 1_000_000


# ======================================================================================
# 错误与归因（错误类型不新增、不降级：reason 原样保留，只补 item/spec）
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：复用 base 族的 `_fail`（reason 是下游唯一归因入口，文案 ASCII）。"""
    return _hm._fail(reason, detail, **details)


def _with_item(exc: CadError, index: int, label: str) -> CadError:
    """给逐项异常补 `item=`/`spec=` 归因；**异常子类与 reason 原样保留**（不降级成基类）。"""
    details = dict(exc.details)
    details.setdefault("item", int(index))
    details.setdefault("spec", str(label))
    return type(exc)(exc.message, reason=exc.reason, **details)


def _call_item(index: int, label: str, function: Any, *args: Any, **kwargs: Any) -> Any:
    """调 base op 并把逐项失败换成带归因的同类异常（fail-fast，不吞、不换成空结果）。"""
    try:
        return function(*args, **kwargs)
    except CadError as exc:
        raise _with_item(exc, index, label) from None


# ======================================================================================
# 规格解析（区域规格 / 距离操作数形状）
# ======================================================================================


def _token(tokens: Any, key: str) -> str:
    """按 `key=` 精确前缀取 token 值；没有该 token 返回 `""`（**不返回 0**，避免"没采"伪装成"真为零"）。"""
    prefix = key + "="
    for token in tokens:
        if token.startswith(prefix):
            return token[len(prefix):]
    return ""


def _blocks(tokens: Any) -> list[list[str]]:
    """把批量 token 流按块首 `item=` 切成块。

    块内再按空格归一：无论某个块是作为多个 token 返回、还是被空格拼成一个 token，
    切出来的块内容与顺序都相同（下游只认 `key=value`，不必猜物理分组）。
    """
    out: list[list[str]] = []
    for token in tokens:
        pieces = str(token).split(" ")
        if pieces and pieces[0].startswith("item="):
            out.append(list(pieces))
        elif out:
            out[-1].extend(pieces)
    return out


def _is_point_spec(value: Any) -> bool:
    """点对形状判据：二元序列且**元素是标量**（再深一层的是"pair 的序列"，不是点）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        return False
    return all(not isinstance(item, (str, bytes)) and not isinstance(item, _SequenceABC) for item in value)


def _looks_like_pair(value: Any) -> bool:
    """单个距离 pair 的形状：操作数映射，或"两个点对/uuid"的二元序列。"""
    if isinstance(value, _MappingABC):
        return True
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC) or len(value) != 2:
        return False
    return all(isinstance(element, str) or _is_point_spec(element) for element in value)


def _specs(value: Any, what: str) -> list[Any]:
    """`regions` → 规格列表；`None` ⇒ `[None]`（单规格：整图），空序列当场报错。"""
    if value is None:
        return [None]
    if isinstance(value, (str, bytes)) or not isinstance(value, _SequenceABC):
        raise _fail("invalid_argument",
                    "%s must be a sequence of region specs (None/index/uuid/uuid-sequence), "
                    "or None to mean the whole sketch" % what, value=repr(value))
    items = list(value)
    if not items:
        raise _fail("empty_selection",
                    "%s is empty: nothing to measure (refusing to no-op silently)" % what, what=what)
    if len(items) > _MAX_BATCH_ITEMS:
        raise _fail("too_many_items", "batch exceeds _MAX_BATCH_ITEMS",
                    what=what, count=len(items), limit=_MAX_BATCH_ITEMS)
    return items


def _resolve_region(sketch: Any, spec: Any, tolerance: int) -> tuple[Any, str]:
    """区域规格 → `(base boundary 入参, spec 标签)`；越界/形状错当场报错，不猜。"""
    if spec is None:
        return None, "all"
    if isinstance(spec, bool):
        raise _fail("invalid_argument", "region spec must not be a bool", value=repr(spec))
    if isinstance(spec, int):
        loops = _hm._closed_loops(_hm._subset(sketch, None), tolerance)
        if spec < 0 or spec >= len(loops):
            raise _fail("index_out_of_range", "region index is out of range",
                        index=spec, loops=len(loops))
        return list(loops[spec]["members"]), "loop:%d" % spec
    if isinstance(spec, str):
        if not spec:
            raise _fail("invalid_argument", "region uuid must be non-empty", value=repr(spec))
        return spec, "uuid:%s" % spec
    if isinstance(spec, _SequenceABC):
        references = list(spec)
        if not references:
            raise _fail("empty_selection", "region uuid sequence is empty (refusing to no-op silently)")
        return references, "refs:%d" % len(references)
    raise _fail("invalid_argument",
                "region spec must be None, an int index, a uuid, or a uuid sequence", value=repr(spec))


def _each_region(sk_or_doc: Any, regions: Any, tol_nm: Any, what: str) -> list[tuple[int, Any, str, int]]:
    """一次把 `regions` 解析成 `(下标, boundary 入参, 标签, 容差)` 列表（解析失败带 item 归因）。"""
    sketch, _doc = _hm._sketch_of(sk_or_doc)
    tolerance = _hm._nm(tol_nm, what="tol_nm")
    plan: list[tuple[int, Any, str, int]] = []
    for index, spec in enumerate(_specs(regions, what)):
        try:
            boundary_arg, label = _resolve_region(sketch, spec, tolerance)
        except CadError as exc:
            raise _with_item(exc, index, "spec") from None
        plan.append((index, boundary_arg, label, tolerance))
    return plan


def _pair_items(pairs: Any) -> list[Any]:
    """`pairs` → 距离项列表；单个 pair（映射/二元简写）也接受，空序列当场报错。"""
    if pairs is None:
        raise _fail("invalid_argument",
                    "pairs must be a non-empty sequence of operand pairs, or a single pair "
                    "(mapping with p1_nm/p2_nm/ref_a/ref_b, or a 2-item sequence of points/uuids)")
    if isinstance(pairs, _MappingABC) or _looks_like_pair(pairs):
        items = [pairs]
    elif isinstance(pairs, (str, bytes)) or not isinstance(pairs, _SequenceABC):
        raise _fail("invalid_argument", "pairs must be a sequence of distance pairs", value=repr(pairs))
    else:
        items = list(pairs)
    if not items:
        raise _fail("empty_selection", "pairs is empty: nothing to measure (refusing to no-op silently)")
    if len(items) > _MAX_BATCH_ITEMS:
        raise _fail("too_many_items", "batch exceeds _MAX_BATCH_ITEMS",
                    count=len(items), limit=_MAX_BATCH_ITEMS)
    return items


def _pair_kwargs(item: Any, index: int) -> dict[str, Any]:
    """距离项 → base `measure_distance` 的 kwargs；形状错当场报错（不猜"到原点"）。"""
    if isinstance(item, _MappingABC):
        kwargs: dict[str, Any] = {}
        for key in item:
            if key not in _OPERAND_KEYS:
                raise _fail("invalid_argument", "unknown distance operand key",
                            item=index, key=repr(key), allowed=list(_OPERAND_KEYS))
            kwargs[str(key)] = item[key]
        if not kwargs:
            raise _fail("invalid_argument", "distance operand mapping is empty", item=index)
        return kwargs
    if isinstance(item, (str, bytes)) or not isinstance(item, _SequenceABC) or len(item) != 2:
        raise _fail("invalid_argument",
                    "distance pair must be an operand mapping or a 2-item sequence of points/uuids",
                    item=index, value=repr(item))
    first, second = item
    kwargs = {}
    kwargs["p1_nm" if _is_point_spec(first) else "ref_a"] = first
    kwargs["p2_nm" if _is_point_spec(second) else "ref_b"] = second
    return kwargs


def _operand_label(kwargs: Any, point_key: str, ref_key: str) -> str:
    """操作数标签（只在 base op 成功后调用 ⇒ 值已过校验）。"""
    if point_key in kwargs:
        x_nm, y_nm = _hm._point(kwargs[point_key], what=point_key)
        return "pt=%d,%d" % (x_nm, y_nm)
    return "ref=%s" % str(kwargs[ref_key])


# ======================================================================================
# 只读批量接口
# ======================================================================================


def boundary_detect_batch(sk_or_doc: Any, *, regions: Any = None, tol_nm: Any = 0) -> list[str]:
    """批量识别闭合边界（只读，不建表不写入）：每个"区域规格"逐环给一块 token。

    `regions` 见模块 docstring；`tol_nm` 是端点配对网格（整数纳米，>0 时按该网格吸附）。
    **返回**：逐环一块 ``item=<i> loop=<j> spec=<标签> loops=<该区域环数> boundary=<uuid,...>
    area_nm2=<int> perimeter_nm=<int> closed=1``；该区域**无闭合环**（开放链不是边界，base 不猜）⇒
    恰好一块 ``item=<i> loop=-1 spec=... loops=0 boundary=NA area_nm2=NA perimeter_nm=NA closed=0``
    —— `loops=0` 是**真为零**、`area_nm2=NA` 是**不适用**，两者不同形；整批末尾 ``batch_count=<项数>``。
    环内成员按 base 族的走向顺序；块顺序 = 规格顺序 × 环（面积降序）顺序。
    """
    plan = _each_region(sk_or_doc, regions, tol_nm, "regions")
    tokens: list[str] = []
    for index, boundary_arg, label, tolerance in plan:
        detected = _call_item(index, label, _hm.boundary_detect,
                              sk_or_doc, boundary=boundary_arg, tol_nm=tolerance)
        if not detected:
            tokens.append("item=%d loop=-1 spec=%s loops=0 boundary=NA area_nm2=NA "
                          "perimeter_nm=NA closed=0" % (index, label))
            continue
        for position, block in enumerate(detected):
            pieces = block.split(" ")
            tokens.append("item=%d loop=%d spec=%s loops=%d boundary=%s area_nm2=%s "
                          "perimeter_nm=%s closed=%s"
                          % (index, position, label, len(detected), _token(pieces, "boundary"),
                             _token(pieces, "area_nm2"), _token(pieces, "perimeter_nm"),
                             _token(pieces, "closed")))
    tokens.append("batch_count=%d" % len(plan))
    return tokens


def measure_area_batch(sk_or_doc: Any, *, regions: Any = None, tol_nm: Any = 0) -> list[str]:
    """批量量面积/周长（只读）：每项一块 ``item=<i> spec=<标签>`` + base `measure_area` 原样 6 token。

    无闭合环的项 ⇒ 那些 token 原样为 `NA`（不适用），`loop_count=0`（真为零），**不抛错、也不返回 `[]`**。
    """
    plan = _each_region(sk_or_doc, regions, tol_nm, "regions")
    tokens: list[str] = []
    for index, boundary_arg, label, tolerance in plan:
        block = _call_item(index, label, _hm.measure_area,
                           sk_or_doc, boundary=boundary_arg, tol_nm=tolerance)
        tokens.append("item=%d" % index)
        tokens.append("spec=%s" % label)
        tokens.extend(block)
    tokens.append("batch_count=%d" % len(plan))
    return tokens


def measure_perimeter_batch(sk_or_doc: Any, *, regions: Any = None) -> list[str]:
    """批量量周长（只读）：每项一块 ``item=<i> spec=<标签>`` + base `measure_perimeter` 原样 3 token。"""
    plan = _each_region(sk_or_doc, regions, 0, "regions")
    tokens: list[str] = []
    for index, boundary_arg, label, _tolerance in plan:
        block = _call_item(index, label, _hm.measure_perimeter, sk_or_doc, boundary=boundary_arg)
        tokens.append("item=%d" % index)
        tokens.append("spec=%s" % label)
        tokens.extend(block)
    tokens.append("batch_count=%d" % len(plan))
    return tokens


def measure_distance_batch(sk_or_doc: Any, *, pairs: Any = None) -> list[str]:
    """批量量距离（只读）：每对操作数一块 ``item=<i> a=<操作数A> b=<操作数B>`` +
    base `measure_distance` 原样 5 token（`distance_nm`/`dx_nm`/`dy_nm`/`nearest_a_nm`/`nearest_b_nm`）。

    `pairs` 的形状见模块 docstring；缺一个操作数 ⇒ base 抛 `invalid_argument`（本层不猜"到原点"）。
    """
    items = _pair_items(pairs)
    tokens: list[str] = []
    for index, item in enumerate(items):
        kwargs = _pair_kwargs(item, index)
        block = _call_item(index, "pair", _hm.measure_distance, sk_or_doc, **kwargs)
        tokens.append("item=%d" % index)
        tokens.append("a=%s" % _operand_label(kwargs, "p1_nm", "ref_a"))
        tokens.append("b=%s" % _operand_label(kwargs, "p2_nm", "ref_b"))
        tokens.extend(block)
    tokens.append("batch_count=%d" % len(items))
    return tokens


def region_report_batch(sk_or_doc: Any, *, regions: Any = None, tol_nm: Any = 0) -> list[str]:
    """批量"边界 + 面积/周长"一次读数（只读）：每项一块 ``item=<i> spec=<标签> loops=<环数>
    boundaries=<uuid,...|NA>`` + base `measure_area` 原样 6 token。

    `boundaries` 是该区域**全部闭合环成员 uuid 的并集**（按环顺序拼接）；无环时是 `NA`（不适用），
    与 `loops=0`（真为零）不同形。本 op 的价值 = 一次调用同时拿到"边界成员 + 面积/周长"，
    少一次重复成环（份额由 base 保证，本层不重算）。
    """
    plan = _each_region(sk_or_doc, regions, tol_nm, "regions")
    tokens: list[str] = []
    for index, boundary_arg, label, tolerance in plan:
        detected = _call_item(index, label, _hm.boundary_detect,
                              sk_or_doc, boundary=boundary_arg, tol_nm=tolerance)
        members: list[str] = []
        for block in detected:
            members.extend(member for member in _token(block.split(" "), "boundary").split(",") if member)
        area_block = _call_item(index, label, _hm.measure_area,
                                sk_or_doc, boundary=boundary_arg, tol_nm=tolerance)
        tokens.append("item=%d" % index)
        tokens.append("spec=%s" % label)
        tokens.append("loops=%d" % len(detected))
        tokens.append("boundaries=%s" % (",".join(members) if members else "NA"))
        tokens.extend(area_block)
    tokens.append("batch_count=%d" % len(plan))
    return tokens


# ======================================================================================
# 写接口（批量填充）：返回受影响实体 uuid 列表（写 op 口径）
# ======================================================================================


def hatch_fill_batch(sk_or_doc: Any, *, regions: Any = None, pattern: Any = "lines", angle_deg: Any = 0.0,
                     spacing_nm: Any = 5_000_000, scale: Any = 1.0, linetype: Any = "continuous",
                     layer: Any = None, island: Any = "normal", assoc: Any = False, color: Any = None,
                     transparency: Any = None, tol_nm: Any = None,
                     max_lines: Any = None) -> list[str]:
    """按区域规格批量建图案填充（写 op）：每个规格一次 base `hatch_create`，落**真实图案线**。

    参数含义与 base `hatch_create` 同名参数一致（`None` = 用 base 缺省；`layer=None` ⇒ base 的
    `DEFAULT_HATCH_LAYER`；`tol_nm=None` ⇒ base 的缺省间隙容差，与预检用的是同一个值）。

    **返回**：新建图案线 uuid 的**扁平列表**，顺序 = 规格顺序 × 该规格内创建顺序（写 op 口径，记录 uuid
    不进返回值）。**失败信号**：预检发现任一规格没有闭合环 ⇒ `CadError(reason="no_boundary")` 且
    **整批一条图案线都不落**；创建阶段单项失败 ⇒ 异常 `details["item"]` 指明失败项（该项之前的规格已落盘，
    本层不声称跨项原子，见模块 docstring 边界第 3 条）。
    """
    sketch, doc = _hm._sketch_of(sk_or_doc)
    tolerance = _hm._gap_tolerance(sketch, doc, tol_nm)
    plan: list[tuple[int, Any, str]] = []
    for index, spec in enumerate(_specs(regions, "regions")):
        try:
            boundary_arg, label = _resolve_region(sketch, spec, tolerance)
        except CadError as exc:
            raise _with_item(exc, index, "spec") from None
        detected = _call_item(index, label, _hm.boundary_detect,
                              sk_or_doc, boundary=boundary_arg, tol_nm=tolerance)
        if not detected:
            raise _fail("no_boundary",
                        "region has no closed loop; nothing was hatched (fail-fast, no partial batch)",
                        item=index, spec=label, tol_nm=tolerance)
        plan.append((index, boundary_arg, label))

    kwargs: dict[str, Any] = {
        "pattern": pattern, "angle_deg": angle_deg, "spacing_nm": spacing_nm, "scale": scale,
        "linetype": linetype, "layer": _hm.DEFAULT_HATCH_LAYER if layer is None else layer,
        "island": island, "assoc": assoc, "color": color, "transparency": transparency, "tol_nm": tol_nm,
    }
    if max_lines is not None:
        kwargs["max_lines"] = max_lines

    created: list[str] = []
    for index, boundary_arg, label in plan:
        created.extend(_call_item(index, label, _hm.hatch_create,
                                  sk_or_doc, boundary=boundary_arg, **kwargs))
    return created


# ======================================================================================
# 操作表（主名 + 别名；别名与主名指向同一函数，`OPERATIONS` 是唯一发现入口）
# ======================================================================================

OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 边界识别（批量）
    "boundary_detect_batch": boundary_detect_batch,
    "boundary_batch": boundary_detect_batch,
    # 测量（批量）
    "measure_area_batch": measure_area_batch,
    "area_batch": measure_area_batch,
    "measure_perimeter_batch": measure_perimeter_batch,
    "perimeter_batch": measure_perimeter_batch,
    "measure_distance_batch": measure_distance_batch,
    "distance_batch": measure_distance_batch,
    "region_report_batch": region_report_batch,
    "region_report": region_report_batch,
    # 填充（批量写）
    "hatch_fill_batch": hatch_fill_batch,
    "hatch_batch": hatch_fill_batch,
}


# ======================================================================================
# 自检（`if __name__ == "__main__"` 跑；末行固定 EXTRA_OPS verdict=PASS|FAIL ops=N）
# ======================================================================================


def _sample_square(width_mm: float = 100.0, height_mm: float = 50.0) -> dict[str, Any]:
    """首尾相接成环的四条 `line`（uuid 固定 ⇒ 同一夹具两次调用逐字节相同）。"""
    width, height = int(width_mm * _NM_PER_MM), int(height_mm * _NM_PER_MM)
    corners = [(0, 0), (width, 0), (width, height), (0, height)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"uuid": "bg%d" % index, "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"entities": entities, "constraints": [], "dimensions": []}


def _sample_circle(center_mm: tuple[float, float] = (50.0, 25.0), radius_mm: float = 10.0) -> dict[str, Any]:
    """落在上面那个矩形内部的一个圆（uuid 固定为 `bgc`，用于"洞"）。"""
    return {"uuid": "bgc", "kind": "circle", "layer": "0", "construction": False,
            "geom": {"cx": int(center_mm[0] * _NM_PER_MM), "cy": int(center_mm[1] * _NM_PER_MM),
                     "r": int(radius_mm * _NM_PER_MM)}}


def _sample_open_chain() -> dict[str, Any]:
    """开口链（正方形的三条边）：**不是边界**，用来验 `loops=0` 与 `NA` 三态。"""
    side = 10 * _NM_PER_MM
    points = [(0, 0), (side, 0), (side, side), (0, side)]
    entities = []
    for index in range(3):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        entities.append({"uuid": "oc%d" % index, "kind": "line", "layer": "0", "construction": False,
                         "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {"entities": entities, "constraints": [], "dimensions": []}


def selftest() -> int:
    """文件自带自检（不依赖 pytest）；返回进程退出码，末行是判定行。"""
    import copy
    import math

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: Any, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    rect_area = 100 * _NM_PER_MM * 50 * _NM_PER_MM
    rect_perimeter = 2 * (100 * _NM_PER_MM + 50 * _NM_PER_MM)
    circle_radius = 10 * _NM_PER_MM
    circle_area = int(round(math.pi * circle_radius * circle_radius))

    # 1) 冻结接口：主名齐全、别名与主名同一函数对象
    main = ("boundary_detect_batch", "measure_area_batch", "measure_perimeter_batch",
            "measure_distance_batch", "region_report_batch", "hatch_fill_batch")
    aliases = {"boundary_batch": "boundary_detect_batch", "area_batch": "measure_area_batch",
               "perimeter_batch": "measure_perimeter_batch", "distance_batch": "measure_distance_batch",
               "region_report": "region_report_batch", "hatch_batch": "hatch_fill_batch"}
    check("interface", all(callable(OPERATIONS.get(name)) for name in main)
          and all(OPERATIONS.get(alias) is OPERATIONS.get(target) for alias, target in aliases.items()),
          "ops=%d main=%d" % (len(OPERATIONS), len(main)))

    # 2) 边界识别批量：矩形 + 内圆 ⇒ 2 环，最大环面积精确等于矩形面积
    holed = _sample_square()
    holed["entities"].append(_sample_circle())
    detected = boundary_detect_batch(holed, regions=[None])
    blocks = _blocks(detected)
    members = set(_token(blocks[0], "boundary").split(",")) if blocks else set()
    check("boundary_batch_loops",
          len(blocks) == 2 and _token(detected, "batch_count") == "1"
          and _token(blocks[0], "loops") == "2" and _token(blocks[0], "area_nm2") == str(rect_area)
          and members == {"bg0", "bg1", "bg2", "bg3"},
          "blocks=%d area=%s members=%d" % (len(blocks), _token(blocks[0], "area_nm2") if blocks else "NA",
                                            len(members)))

    # 3) 无环：0（真为零）与 NA（不适用）不同形，且不抛错
    open_block = _blocks(boundary_detect_batch(_sample_open_chain(), regions=[None]))[0]
    check("boundary_batch_no_loop",
          _token(open_block, "loop") == "-1" and _token(open_block, "loops") == "0"
          and _token(open_block, "area_nm2") == "NA" and _token(open_block, "perimeter_nm") == "NA"
          and _token(open_block, "boundary") == "NA" and _token(open_block, "closed") == "0",
          "block=%s" % " ".join(open_block))

    # 4) 面积批量：洞被减掉（净面积 = 外 − 洞），整数账自洽
    area_block = _blocks(measure_area_batch(holed, regions=[None]))[0]
    outer = int(_token(area_block, "outer_area_nm2"))
    hole = int(_token(area_block, "hole_area_nm2"))
    net = int(_token(area_block, "area_nm2"))
    check("area_batch_net",
          outer == rect_area and abs(hole - circle_area) <= 1 and net == outer - hole
          and _token(area_block, "loop_count") == "2" and _token(area_block, "closed") == "1",
          "outer=%d hole=%d net=%d" % (outer, hole, net))

    # 5) 周长批量：解析式精确值
    perimeter_block = _blocks(measure_perimeter_batch(_sample_square(), regions=[None]))[0]
    check("perimeter_batch_exact",
          _token(perimeter_block, "perimeter_nm") == str(rect_perimeter)
          and _token(perimeter_block, "curve_count") == "4" and _token(perimeter_block, "closed") == "1",
          "block=%s" % " ".join(perimeter_block))

    # 6) 距离批量：3-4-5 点对 + 点-实体"真为零"
    distance_blocks = _blocks(measure_distance_batch(
        _sample_square(), pairs=[[(0, 0), (3 * _NM_PER_MM, 4 * _NM_PER_MM)],
                                 {"p1_nm": (0, 0), "ref_b": "bg0"}]))
    check("distance_batch",
          len(distance_blocks) == 2
          and _token(distance_blocks[0], "distance_nm") == str(5 * _NM_PER_MM)
          and _token(distance_blocks[1], "distance_nm") == "0"
          and _token(distance_blocks[1], "a") == "pt=0,0"
          and _token(distance_blocks[1], "b") == "ref=bg0",
          "blocks=%d d0=%s d1=%s" % (len(distance_blocks), _token(distance_blocks[0], "distance_nm"),
                                     _token(distance_blocks[1], "distance_nm")))

    # 7) 合并读数：环数 + 边界成员并集 + 面积
    report_block = _blocks(region_report_batch(holed, regions=[None]))[0]
    check("region_report_batch",
          _token(report_block, "loops") == "2" and _token(report_block, "area_nm2") == str(net)
          and len(_token(report_block, "boundaries").split(",")) == 5,
          "loops=%s boundaries=%d" % (_token(report_block, "loops"),
                                      len(_token(report_block, "boundaries").split(","))))

    # 8) 只读：测量/识别前后草图逐字节相同
    before = copy.deepcopy(holed)
    measure_area_batch(holed, regions=[0, 1])
    measure_perimeter_batch(holed)
    measure_distance_batch(holed, pairs=[("bg0", "bgc")])
    boundary_detect_batch(holed, regions=[None])
    region_report_batch(holed, regions=[None])
    check("read_only", holed == before, "entities=%d" % len(holed["entities"]))

    # 9) 错误路径：reason 机器可读 + 逐项归因 details["item"]
    probes = [(lambda: measure_area_batch(_sample_square(), regions=[5]), "index_out_of_range"),
              (lambda: measure_area_batch(_sample_square(), regions=[]), "empty_selection"),
              (lambda: measure_area_batch(_sample_square(), regions=2.5), "invalid_argument"),
              (lambda: measure_distance_batch(_sample_square(), pairs=None), "invalid_argument"),
              (lambda: measure_distance_batch(_sample_square(), pairs=[]), "empty_selection"),
              (lambda: boundary_detect_batch(_sample_square(), regions=[["nope"]]), "unknown_reference")]
    reasons: list[str] = []
    items: list[Any] = []
    for probe, expected in probes:
        try:
            probe()
        except CadError as exc:
            reasons.append(exc.reason if exc.reason == expected else "%s!=%s" % (exc.reason, expected))
            items.append(exc.details.get("item"))
        else:
            reasons.append("NO_ERROR")
            items.append(None)
    check("error_reasons", all("!=" not in reason and reason != "NO_ERROR" for reason in reasons)
          and items[5] == 0 and items[0] == 0,
          "reasons=%s items=%s" % (",".join(reasons), items))

    # 10) 填充批量：两规格 ⇒ 两条记录；返回 uuid 全是不重复的 line；边界一个纳米不动
    fill_target = _sample_square()
    fill_target["entities"].append(_sample_circle())
    boundary_before = copy.deepcopy(fill_target["entities"][:5])
    created = hatch_fill_batch(fill_target, regions=[0, 1], spacing_nm=5 * _NM_PER_MM)
    kinds = {entity["uuid"]: entity["kind"] for entity in fill_target["entities"]}
    records = _hm.hatch_list(fill_target)
    check("hatch_fill_batch",
          len(created) > 0 and len(records) == 2 and len(set(created)) == len(created)
          and all(kinds.get(uuid) == "line" for uuid in created)
          and fill_target["entities"][:5] == boundary_before,
          "created=%d records=%d" % (len(created), len(records)))

    # 11) 预检失败整批不落：开口链 ⇒ no_boundary 且实体数不变
    open_sketch = _sample_open_chain()
    entities_before = len(open_sketch["entities"])
    reason = ""
    try:
        hatch_fill_batch(open_sketch, regions=[None])
    except CadError as exc:
        reason = exc.reason
    check("hatch_fill_batch_precheck",
          reason == "no_boundary" and len(open_sketch["entities"]) == entities_before,
          "reason=%s entities=%d" % (reason or "NO_ERROR", len(open_sketch["entities"])))

    passed = all(item[1] for item in checks)
    for name, ok, detail in checks:
        print("check %-28s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    print("EXTRA_OPS verdict=%s ops=%d" % ("PASS" if passed else "FAIL", len(OPERATIONS)))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
