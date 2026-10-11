#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w27 —— 装配族缺口「配合对齐方向控制」真实现。

本条是哪一条（可复核的编号）
----------------------------
`docs/research/cad-parity/status/sw-assembly.json` 里本族仍 missing/partial 的 must_have 只有 3 条
（按 `features/sw-assembly.json` 的 must_have 清单顺序编号 0..N-1，N=3）：

    0) 配合对齐方向控制（Aligned / Anti-Aligned）      —— status = missing
    1) 过定义与配合冲突诊断（MateXpert 类）             —— status = partial
    2) 干涉检查（真布尔求交，非 AABB 粗检）             —— status = partial

本席分到的是第 18 号：18 mod 3 = **0** ⇒ 落地 **「配合对齐方向控制」**（missing 的那条）。

一句话用途
----------
给装配配合加一个**显式命名的对齐状态**（`aligned` / `anti_aligned`），并让它**真的改几何判据**：
`mate_align` / `mate_flip_alignment` 改的是上游 `mate_check()` 现算残差时吃的那个 `flip` 位，
`mate_add_aligned` 在建配合时就按 SolidWorks「对齐 / 反转对齐」口径落状态；状态与几何位由一条
不变量绑定，`mate_alignment_audit` 能把它抓出来并（可选）修回去。

为什么这是"真实现"而不是再包一层
--------------------------------
上游 `cadcore.ops.sw_assembly.mate_add(..., flip=...)` 只在**创建那一刻**能定方向，创建之后没有任何
入口能改它；全仓也没有「对齐 / 反转对齐」这个**命名状态**（status/sw-assembly.json 记：严格词边界
grep `aligned|anti_aligned|antialigned` 命中 0）。本片补的正是"**创建之后的方向控制**"：
命名状态 + 改状态 + 未标注/不适用三态 + 不变式审计 + 建配合时的对齐入口。
后果是**可测的几何后果**（下面的自检就是拿 `mate_check()` 现算出来的残差验的），不是只写个字符串。

真源只有一份（不做两份真源）
----------------------------
- **几何真源仍是**配合记录里的 `flip` 布尔位 —— 本片**不引入第二个几何量**，只加一个**命名状态**
  `alignment ∈ {"aligned", "anti_aligned"}`；
- **权威方向**：`alignment` 是权威命名状态，`flip` 是它的**几何投影**，不变量 = 
  ``flip == (alignment == "anti_aligned")``；写入只在 `mate_align` / `mate_flip_alignment` /
  `mate_add_aligned` 三处发生，且每次都**同时**写两个键（不存在"只写一半"的窗口）；
- **三态（不许把"没采"伪装成"采到"）**：被上游创建、没经过本片标定的老记录**有 `flip`、没有
  `alignment` 键** ⇒ 记 **未标注**，读侧抛 `CadError(reason="alignment_unlabelled")`，
  **绝不**当成 `aligned`；作用域里一条配合都没有 ⇒ `CadError(reason="no_mates")`，
  与"审计通过、0 条违反"（返回 `[]`）**不同形**。

适用种类（写下来的边界，不是猜的）
----------------------------------
只对**方向有正反、且本层测量真的吃这个位**的种类开放：`coincident` / `parallel` / `concentric` /
`distance`（依据 = 上游 `sw_assembly._measure()` 里 `flip` 实际参与判据的分支）。
对 `perpendicular` / `angle` / `tangent` / `lock` 施对齐 ⇒ `CadError(reason="alignment_not_applicable")`，
**不静默写一个在本层不起作用的位**（要扩展这几个种类的对齐语义，得改上游测量，不属本片写面）。

冻结接口（对外契约只有这三样）
------------------------------
- ``FAMILY = "sw_assembly"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回本次**命中/改写**的配合 uuid 列表（表内顺序，
  语义逐条写在各自 docstring 里）；长度一律整数纳米；错误一律抛 ``cadcore.errors.CadError(reason=...)``。

写面与不动的东西
----------------
表仍放 ``doc.extra["sw_assembly"]``（上游 A5 "未知顶层字段可往返"容器）。本片**不碰**
`sketches` / `features` / `annotations` / `sheets`，不新增顶层键，不写别的族的表，也不写 `__init__.py`。

自检入口（≥2 条硬断言，实跑即判定行）

    python3 cadcore/ops/features/sw_assembly__w27.py

非 docstring 的字符串字面量一律 ASCII（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

if __package__ not in (None, ""):  # 正常路径：作为包模块导入
    from cadcore.errors import CadError
    from ..sw_assembly import (
        MATE_KINDS, _fail, _store, _table, insert_component, mate_add, mate_check, mate_perpendicular,
    )
else:  # pragma: no cover - 以脚本方式直接运行（自带自检入口）时补包上下文
    _PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if _PKG_ROOT not in sys.path:
        sys.path.insert(0, _PKG_ROOT)
    from cadcore.errors import CadError
    from cadcore.ops.sw_assembly import (
        MATE_KINDS, _fail, _store, _table, insert_component, mate_add, mate_check, mate_perpendicular,
    )

__all__ = [
    "FAMILY", "ALIGNED", "ANTI_ALIGNED", "ALIGNMENTS", "ALIGNMENT_KEY", "ALIGNMENT_KINDS",
    "OPERATIONS", "mate_align", "mate_flip_alignment", "mate_add_aligned", "mate_alignment",
    "mate_alignment_audit", "selftest",
]

FAMILY = "sw_assembly"

#: 命名对齐状态（SolidWorks 口径：Aligned / Anti-Aligned；注意是下划线，不是连字符）。
ALIGNED = "aligned"
ANTI_ALIGNED = "anti_aligned"
ALIGNMENTS = (ALIGNED, ANTI_ALIGNED)

#: 命名状态在配合记录里的键。**不改上游键名**，也不把 `flip` 换掉（那是上游测量的输入）。
ALIGNMENT_KEY = "alignment"

#: 允许施对齐的种类：方向有正反 **且** 上游 `_measure()` 真的用 `flip` 判满足。
ALIGNMENT_KINDS = frozenset({"coincident", "parallel", "concentric", "distance"})

#: 调用方可用的别名（只做归一，不是第二套状态名）。
_ALIASES = {
    "aligned": ALIGNED, "align": ALIGNED, "same": ALIGNED, "positive": ALIGNED,
    "anti_aligned": ANTI_ALIGNED, "antialigned": ANTI_ALIGNED, "anti": ANTI_ALIGNED,
    "flipped": ANTI_ALIGNED, "flip": ANTI_ALIGNED, "negative": ANTI_ALIGNED,
}


# ======================================================================================
# 内部：入参把关 / 作用域 / 不变量读写（三个写点共用，保证"命名状态与几何位同一次写"）
# ======================================================================================


def _alignment_name(value: Any) -> str:
    """把对齐入参归一成 `aligned` / `anti_aligned`；别的一律 `CadError(unknown_alignment)`。"""
    if not isinstance(value, str):
        raise _fail("unknown_alignment", "alignment must be the string aligned or anti_aligned",
                    alignment=repr(value), known=list(ALIGNMENTS))
    name = _ALIASES.get(value.strip().lower().replace("-", "_"))
    if name is None:
        raise _fail("unknown_alignment", "alignment must be the string aligned or anti_aligned",
                    alignment=value, known=list(ALIGNMENTS))
    return name


def _applicable_kind(kind: Any) -> str:
    """校验配合种类：不在冻结枚举 ⇒ `unknown_mate_kind`；枚举内但无方向语义 ⇒ `alignment_not_applicable`。"""
    key = str(kind).strip().lower()
    if key not in MATE_KINDS:
        raise _fail("unknown_mate_kind", "kind is not in this layer's frozen enum",
                    kind=kind, known=sorted(MATE_KINDS))
    if key not in ALIGNMENT_KINDS:
        raise _fail("alignment_not_applicable",
                    "this mate kind has no direction sense in this layer's measurement",
                    kind=key, applicable=sorted(ALIGNMENT_KINDS))
    return key


def _uuid_wanted(value: Any) -> list[str]:
    """把 `mates=` 归一成 uuid 列表（单个字符串 / 序列都收；空选择当场报，不静默当"全部"）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        raise _fail("bad_refs", "mates must be a mate uuid or a sequence of mate uuids",
                    value=repr(value))


def _select(store: dict[str, Any], mates: Any) -> list[dict[str, Any]]:
    """解析作用域：`mates=None` ⇒ 表内全部配合；给了 uuid ⇒ 逐个必须存在（缺一个即报 `unknown_mate`）。"""
    rows = _table(store, "mates")
    if mates is None:
        if not rows:
            raise _fail("no_mates", "this document has no mate to control (create one first)")
        return list(rows)
    wanted = _uuid_wanted(mates)
    if not wanted:
        raise _fail("no_mates", "empty mate selection: pass mate uuids or omit mates for all")
    index = {str(row.get("uuid")): row for row in rows}
    out: list[dict[str, Any]] = []
    for key in wanted:
        if key not in index:
            raise _fail("unknown_mate", "mate uuid is not in this document's mate table", mate=key)
        out.append(index[key])
    return out


def _labelled(store: dict[str, Any], row: dict[str, Any]) -> str | None:
    """读一条记录的命名状态；没有 `alignment` 键 ⇒ `None` = **未标注**（不是 aligned）。"""
    value = row.get(ALIGNMENT_KEY)
    return None if value is None else _alignment_name(value)


def _write_alignment(row: dict[str, Any], name: str) -> None:
    """**唯一写点**：同一次调用里写命名状态 + 重投影几何位（不变量在结构上不可能破）。"""
    row[ALIGNMENT_KEY] = name
    row["flip"] = (name == ANTI_ALIGNED)


def _row(store: dict[str, Any], mate: Any) -> dict[str, Any]:
    key = str(mate)
    for row in _table(store, "mates"):
        if str(row.get("uuid")) == key:
            return row
    raise _fail("unknown_mate", "mate uuid is not in this document's mate table", mate=key)


# ======================================================================================
# 操作：对齐方向控制（设置 / 翻转 / 建配合时带 / 读 / 审计）
# ======================================================================================


def mate_align(sk_or_doc: Any, *, mates: Any = None, alignment: Any, check: bool = True) -> list[str]:
    """**设置配合对齐方向**（`aligned` / `anti_aligned`）—— 创建之后的方向控制入口。

    `mates=None` ⇒ 表内全部配合；`mates=<uuid 或 uuid 列表>` ⇒ 只改点名的那些（缺一个即
    `CadError(reason="unknown_mate")`）。参数先全量校验再落笔：任何一条不适用（种类不在
    `ALIGNMENT_KINDS`）⇒ `CadError(reason="alignment_not_applicable")` 且**整批不改**（无半截写入）。
    `check=True`（缺省）⇒ 落笔后用组件**当前**位姿重跑 `mate_check(...)` 刷新 `status/measured_*`，
    所以"改方向"会**真的**改变几何判定结果（对齐 ⇄ 反转对齐会把同一距离配合在 `satisfied` 与
    `violated` 之间翻面）。

    返回：被改写的配合 uuid 列表（表内顺序）。
    """
    store = _store(sk_or_doc)
    rows = _select(store, mates)
    name = _alignment_name(alignment)
    for row in rows:  # 早失败：先全量校验，再写
        _applicable_kind(row.get("kind"))
    out = [str(row.get("uuid")) for row in rows]
    for row in rows:
        _write_alignment(row, name)
    if check and out:
        mate_check(sk_or_doc, mates=out, include_ok=True)
    return out


def mate_flip_alignment(sk_or_doc: Any, *, mates: Any = None, check: bool = True) -> list[str]:
    """**翻转对齐方向**（对齐 ⇄ 反转对齐，SolidWorks 的"反转对齐"按钮）。

    对**未标注**的老记录：先按几何位 `flip` 推出它当前实际的方向，再翻转，并**落上命名状态**
    （翻完之后这条记录就不再是"未标注"了）。不适用种类与不存在的 uuid 同 `mate_align` 一样当场报错、
    整批不改。`check=True` ⇒ 翻转后重跑 `mate_check(...)` 刷新残差。

    返回：被翻转的配合 uuid 列表（表内顺序）。
    """
    store = _store(sk_or_doc)
    rows = _select(store, mates)
    for row in rows:  # 早失败：先全量校验，再写
        _applicable_kind(row.get("kind"))
    out: list[str] = []
    for row in rows:
        current = _labelled(store, row)
        if current is None:  # 未标注：它的"当前方向"由几何位定义（不是当 aligned）
            current = ANTI_ALIGNED if bool(row.get("flip")) else ALIGNED
        _write_alignment(row, ALIGNED if current == ANTI_ALIGNED else ANTI_ALIGNED)
        out.append(str(row.get("uuid")))
    if check and out:
        mate_check(sk_or_doc, mates=out, include_ok=True)
    return out


def mate_add_aligned(sk_or_doc: Any, *, kind: Any, refs: Any, value_nm: Any = None,
                     value_deg: Any = None, alignment: Any = ALIGNED, tolerance_nm: Any = None,
                     tolerance_deg: Any = None, label: Any = None, check: bool = True) -> list[str]:
    """**建配合时就带对齐方向**：等价于上游 `mate_add(..., flip=...)` + 命名状态，一次落齐。

    `kind` 只收有方向语义的四种（`coincident` / `parallel` / `concentric` / `distance`），其余种类
    ⇒ `CadError(reason="alignment_not_applicable")`（先于任何写入，建不出半条记录）。
    `value_nm` / `value_deg` / `tolerance_*` / `label` 的语义与上游 `mate_add` 逐字相同（本片不另立一份）。
    `check=True` ⇒ 建完立即用当前位姿算一次残差（`status` 不会是 `recorded` 那种模糊态）。

    返回：`[新配合 uuid]`。
    """
    name = _alignment_name(alignment)
    key = _applicable_kind(kind)
    uuid = mate_add(sk_or_doc, kind=key, refs=refs, value_nm=value_nm, value_deg=value_deg,
                    flip=(name == ANTI_ALIGNED), tolerance_nm=tolerance_nm,
                    tolerance_deg=tolerance_deg, label=label)[0]
    row = _row(_store(sk_or_doc), uuid)
    _write_alignment(row, name)
    if check:
        mate_check(sk_or_doc, mates=[str(uuid)], include_ok=True)
    return [str(uuid)]


def mate_alignment(sk_or_doc: Any, *, mates: Any = None, alignment: Any = None) -> list[str]:
    """**读对齐状态**：返回命中的配合 uuid（表内顺序）。

    `alignment=None` ⇒ 作用域内**全部已标注**的配合；给了名字 ⇒ 只返回该命名状态的那些。
    三态不许互相冒充：作用域内出现**未标注**记录 ⇒ `CadError(reason="alignment_unlabelled")`
    （details 里带 `mates` 与 `remedy`），**不**把它当 `aligned`，也不静默漏掉它；
    作用域里一条配合都没有 ⇒ `CadError(reason="no_mates")`。返回 `[]` 只表示
    "采到了、且没有一条命中"（真为零）。
    """
    store = _store(sk_or_doc)
    rows = _select(store, mates)
    wanted = None if alignment is None else _alignment_name(alignment)
    unlabelled: list[str] = []
    out: list[str] = []
    for row in rows:
        name = _labelled(store, row)
        if name is None:
            unlabelled.append(str(row.get("uuid")))
            continue
        if wanted is None or name == wanted:
            out.append(str(row.get("uuid")))
    if unlabelled:
        raise _fail("alignment_unlabelled",
                    "mates created outside this layer carry no named alignment; label them with mate_align",
                    mates=unlabelled, remedy="mate_align")
    return out


def mate_alignment_audit(sk_or_doc: Any, *, mates: Any = None, repair: bool = False) -> list[str]:
    """**审计对齐不变量** ``flip == (alignment == "anti_aligned")`` —— 独立复核，不信写入方。

    发现命名状态与几何位打架（例如 L1 文档被手改过）⇒ 返回这些配合的 uuid；`repair=True` 时
    以命名状态为准把几何位重投影回去（返回的仍是"被改写"的那些 uuid）。全部一致 ⇒ 返回 `[]`
    （真为零）；作用域内**未标注**记录 ⇒ `CadError(reason="alignment_unlabelled")`；
    一条配合都没有 ⇒ `CadError(reason="no_mates")`。
    `repair` **只改几何位**，不动残差；要刷 `status/measured_*` 请再跑一次 `mate_check(...)`。
    """
    store = _store(sk_or_doc)
    rows = _select(store, mates)
    unlabelled = [str(row.get("uuid")) for row in rows if _labelled(store, row) is None]
    if unlabelled:
        raise _fail("alignment_unlabelled",
                    "mates created outside this layer carry no named alignment; label them with mate_align",
                    mates=unlabelled, remedy="mate_align")
    bad: list[str] = []
    for row in rows:
        name = _labelled(store, row)
        expected = (name == ANTI_ALIGNED)
        if bool(row.get("flip")) != expected:
            bad.append(str(row.get("uuid")))
            if repair:
                row["flip"] = expected
    return bad


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mate_align": mate_align,
    "mate_flip_alignment": mate_flip_alignment,
    "mate_add_aligned": mate_add_aligned,
    "mate_alignment": mate_alignment,
    "mate_alignment_audit": mate_alignment_audit,
}


# ======================================================================================
# 自检（真跑：真 Document + 真组件位姿 + 上游 mate_check 现算残差；宁红不假绿）
# ======================================================================================

MM = 1000000
_CHECKS = [0]


def _check(condition: bool, message: str, **details: Any) -> None:
    _CHECKS[0] += 1
    if not condition:
        raise AssertionError(f"selftest assertion #{_CHECKS[0]} failed: {message} {details}")


def _expect_reason(reason: str, call: Callable[[], Any]) -> None:
    _CHECKS[0] += 1
    try:
        call()
    except CadError as exc:
        if exc.reason != reason:
            raise AssertionError(
                f"selftest assertion #{_CHECKS[0]} failed: expected CadError(reason={reason}), "
                f"got reason={exc.reason} details={exc.details}")
        return
    raise AssertionError(
        f"selftest assertion #{_CHECKS[0]} failed: expected CadError(reason={reason}), nothing raised")


def _new_doc() -> Any:
    """夹具自证：必须是**真的** `cadcore.docmodel.Document`（本层写的是它的 `extra`）。"""
    from cadcore.docmodel import DEFAULT_TITLE_KEY, Document, new_document
    try:
        doc = new_document()
    except TypeError:  # 兼容"必须显式给 title_key"的签名；两条路都必须真建出 Document
        doc = new_document(title_key=DEFAULT_TITLE_KEY)
    _check(type(doc) is Document, "fixture must build a real cadcore.docmodel.Document",
           built=type(doc).__name__)
    _check(hasattr(doc, "extra") and hasattr(doc, "sketches"),
           "fixture document must carry the L1 shape this layer writes into")
    return doc


def selftest() -> int:
    doc = _new_doc()
    # 两个零部件：A 在原点，B 沿 A 的 origin 参考方向（局部 +z）正侧 50 mm。
    a = insert_component(doc, part_key="w27_plate", at_nm=(0, 0, 0))[0]
    b = insert_component(doc, part_key="w27_riser", at_nm=(0, 0, 50 * MM))[0]
    refs = [[a, "origin"], [b, "origin"]]
    store = _store(doc)

    # (1) 建配合时就带"对齐"：几何位必须是 +50mm 侧，且上游现算残差判 satisfied。
    mate = mate_add_aligned(doc, kind="distance", refs=refs, value_nm=50 * MM,
                            alignment="aligned")[0]
    row = _row(store, mate)
    _check(row[ALIGNMENT_KEY] == ALIGNED and row["flip"] is False,
           "aligned must project to flip=False", row=dict(row))
    _check(row["status"] == "satisfied" and row["measured_nm"] == 50 * MM,
           "an aligned distance mate must measure the +50mm side", row=dict(row))
    _check(mate_alignment(doc) == [mate], "read must return the labelled mate")
    _check(mate_alignment(doc, alignment=ANTI_ALIGNED) == [],
           "no anti-aligned mate yet: a real zero, not an unmeasured blank")

    # (2) 反转对齐 = 真几何后果：投影翻到负侧，同一条配合被判 violated。
    flipped = mate_flip_alignment(doc, mates=mate)
    _check(flipped == [mate], "flip must report the mate it rewrote", flipped=flipped)
    _check(row[ALIGNMENT_KEY] == ANTI_ALIGNED and row["flip"] is True,
           "flip must land the anti_aligned state", row=dict(row))
    _check(row["status"] == "violated" and row["measured_nm"] == -50 * MM,
           "an anti-aligned distance mate must measure the negative side and break", row=dict(row))
    _check(mate_alignment(doc, alignment=ALIGNED) == []
           and mate_alignment(doc, alignment=ANTI_ALIGNED) == [mate],
           "read must follow the flipped state")

    # (3) 再设回对齐：往返一致（方向控制可逆）。
    _check(mate_align(doc, mates=[mate], alignment="aligned") == [mate],
           "mate_align must report the mate it rewrote")
    _check(row[ALIGNMENT_KEY] == ALIGNED and row["flip"] is False
           and row["status"] == "satisfied" and row["measured_nm"] == 50 * MM,
           "an aligned round trip must restore the +50mm measurement", row=dict(row))

    # (4) 不变量审计：干净 ⇒ 真为零；手改几何位 ⇒ 抓得出来；repair ⇒ 以命名状态为准修回。
    _check(mate_alignment_audit(doc) == [], "a clean document must audit to zero violations")
    row["flip"] = True  # 手改文档：命名状态与几何位打架
    _check(mate_alignment_audit(doc, mates=[mate]) == [mate],
           "audit must catch a hand-edited geometric bit")
    _check(mate_alignment_audit(doc, mates=[mate], repair=True) == [mate],
           "repair must report what it rewrote")
    _check(row["flip"] is False and mate_alignment_audit(doc) == [],
           "repair must restore the invariant")

    # (5) 三态：上游老记录（只有 flip、没有命名状态）读作"未标注"，不当 aligned，也不静默漏掉。
    legacy = mate_add(doc, kind="parallel", refs=refs)[0]
    _expect_reason("alignment_unlabelled", lambda: mate_alignment(doc, mates=[legacy]))
    _check(mate_alignment(doc, mates=[mate]) == [mate],
           "a labelled mate must stay readable while a neighbour is unlabelled")
    mate_flip_alignment(doc, mates=[legacy])
    _check(_row(store, legacy)[ALIGNMENT_KEY] == ANTI_ALIGNED
           and _row(store, legacy)["flip"] is True,
           "flipping a legacy row must materialise its named state")
    _check(mate_alignment(doc) == [mate, legacy], "both mates are labelled now")

    # (6) 负例：未知对齐名 ⇒ unknown_alignment，且一个字节都不改。
    _expect_reason("unknown_alignment", lambda: mate_align(doc, mates=[mate], alignment="sideways"))
    _check(row[ALIGNMENT_KEY] == ALIGNED and row["flip"] is False,
           "a rejected alignment name must not touch the record")

    # (7) 负例：不适用种类 ⇒ 整批拒收（无半截写入）。
    perp = mate_perpendicular(doc, refs=refs)[0]
    perp_row = _row(store, perp)
    _expect_reason("alignment_not_applicable",
                   lambda: mate_align(doc, mates=[mate, perp], alignment=ANTI_ALIGNED))
    _check(row[ALIGNMENT_KEY] == ALIGNED and row["flip"] is False,
           "a rejected batch must leave the first mate untouched")
    _check(ALIGNMENT_KEY not in perp_row,
           "a rejected batch must not label the inapplicable mate")

    # (8) 三态：空装配体（一条配合都没有）≠ 审计通过。
    empty = _new_doc()
    _expect_reason("no_mates", lambda: mate_alignment_audit(empty))
    _expect_reason("no_mates", lambda: mate_alignment(empty))

    print(f"SELFTEST assertions={_CHECKS[0]} ops={len(OPERATIONS)}")
    print("FEATURE_OK family=sw_assembly op=mate_align verdict=PASS")
    return 0


def main() -> int:
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())
