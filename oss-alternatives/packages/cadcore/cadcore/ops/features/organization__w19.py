#!/usr/bin/env python3
"""cadcore.ops.features.organization__w19 —— 组织族（organization）第 10 号缺口：外部参照路径与相对路径管理。

来源与编号（怎么定到这一条的，写死在这里，免得下游靠猜）
--------------------------------------------------------
清单按 ``docs/research/cad-parity/status/autocad-organization.json`` 的 ``items`` 顺序取
**status != implemented** 的 must_have，编号 0..N-1：

    0 图层创建与图层特性管理            partial
    1 图层开/关冻结锁定与当前图层切换    partial
    2 对象图层指定与改层                partial
    3 无用图层清理、图层合并/删除        partial
    4 动态块与块编辑器                  missing
    5 块属性编辑与批量改属性            partial
    6 属性提取成表或文件                missing
    7 写块（导出为独立 dwg）            missing
    8 外部参照附着与覆盖                partial
    9 参照状态管理：卸载/重载/拆离/绑定  partial
   10 参照路径与相对路径管理  <-- 本片  partial
   11 在位编辑参照                      missing
   12 图纸集与图纸列表管理              missing
   13 图纸集自定义特性与字段联动        partial

本片负责第 10 号：**参照路径的绝对/相对形态管理**。注：任务书点名的
``docs/research/cad-parity/features/organization.json`` **在仓里不存在**，同族真源是
``features/autocad-organization.json``（本片未逐项展开，编号以上面 status 清单为准）。

接口（冻结，只有这三样是契约）
-----------------------------
- ``FAMILY = "organization"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``，返回**受影响的 xref uuid 列表**（顺序见各 op docstring）；
- 错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

L1 装得下吗 —— 装得下，而且这一条本来就是数据面
------------------------------------------------
参照路径是**纯数据**（一串路径 + 宿主图档目录），不需要任何几何。落点与 `cadcore.ops.organization`
的参照占位**同一张表**：文档 → ``doc.extra["organization"]``；草图 dict/对象 → ``sk["organization"]``；
表结构 ``{"schema": 1, "layers": [], "blocks": [], "inserts": [], "groups": [], "xrefs": []}``。
本模块**只给 xrefs 行加键**（``path_mode`` / ``path_base`` / ``path_abs``），不改 L1 实体字段、
不改行上既有键（``name`` / ``at_nm`` / ``scale`` / ``rotation_deg`` / ``loaded`` / ``entity_uuids``）。

一条参照的路径按"三件套"存（这是本片的核心数据后果）
---------------------------------------------------
- ``path``      ：**写进图档的形态**（相对或绝对，POSIX 分隔符）—— AutoCAD 里就是这一串说了算；
- ``path_mode`` ：``"relative"`` | ``"absolute"``，记录 ``path`` 是哪一种；
- ``path_base`` ：写下这一串时宿主图档所在目录（相对形态的**解释锚点**；绝对形态下只是记账）；
- ``path_abs``  ：由 ``path`` + ``path_base`` 解出的目标绝对路径（**派生量，用于查漂移**）。

判定取绝对目标时**只用 ``path`` + ``path_base`` 现算**，不拿 ``path_abs`` 当输入 —— 否则上游手改一串
就会静默改图；``path_abs`` 与现算不一致 ⇒ ``xref_path_audit`` 报出来（漂移）。

语义口径（与全仓一致，别自己发明）
---------------------------------
- 路径分隔符：一律 POSIX ``/``；入参里的 ``\\`` 转成 ``/``（DXF 的 XREF 路径常是 Windows 形态）。
- 相对模式**要求目标与 ``base_dir`` 同为 POSIX 绝对或同为相对**：混用会让
  ``join(base, rel)`` 解不回原目标 = 静默错图 ⇒ ``CadError(reason="invalid_argument")``。
- 盘符/UNC 路径（``C:/...``、``//host/share``）**不做相对化**：``posixpath`` 解不了，
  硬转会得到一串走了当前工作目录的假相对路径 ⇒ ``CadError(reason="unsupported_path")`（显式降级，
  不是"再想想"）。绝对模式下原样保留（只统一分隔符）。
- ``[] = 真为零``：本模块的返回列表都是 uuid 列表，"没有行被改"就是 ``[]``，不拿 ``0`` 冒充。
- 只做**路径代数**，**零文件 I/O**：不打开外部文件、不校验存在性、不读内容 ⇒ 不产生 ``loaded=True``
  （重载/绑定是 io 层的事，见 ``organization.xref_set`` 的同款降级）。
- 不改 L1 实体：``sk["entities"]`` 与实体的五个字段一个都不碰（自检里有一条断言验它）。

有意限定的边界（写下来，防止下游靠猜）
-------------------------------------
1. 本模块**不猜**空路径行：``path`` 缺失/为空的行在批量操作里**跳过**（跳过次数不伪装成"已管理"），
   它们由 ``xref_path_audit`` 报出 uuid。
2. 批量操作**两阶段**：先算完整批计划（可能抛错），再落盘 ⇒ 中途报错时表**一个键都没改**
   （自检里有一条断言专门验原子性）。
3. ``xref_path_set`` 只在显式传 ``name=`` 时**登记**一条只做路径管理的参照记录（与 ``xref_attach``
   同表同形）；不给 ``name`` 而参照不存在 ⇒ ``unknown_reference``。
4. ``xref_path_audit`` 是只读查询：不建表、不写入；**空表与全部合规都返回 ``[]``** —— 要区分请先用
   ``organization.xref_filter`` 看表是否为空（返回 ``[]`` 时不得当成"表里没有参照"）。
5. 非 docstring 的字符串字面量一律 ASCII：错误文案由上层按 key 取词，本模块只给机器可读 ``reason``。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/organization__w19.py 时的引导
    import os as _os
    import sys as _sys

    _root = _os.path.abspath(__file__)
    for _ in range(4):  # features -> ops -> cadcore(包) -> packages/cadcore（sys.path 根）
        _root = _os.path.dirname(_root)
    _sys.path.insert(0, _root)
    __package__ = "cadcore.ops.features"

import posixpath
import re
import uuid as _uuid
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from cadcore.errors import CadError

FAMILY = "organization"

__all__ = ["FAMILY", "OPERATIONS", "xref_path_set", "xref_path_mode", "xref_path_rebase", "xref_path_audit"]

_SCHEMA = 1
_TABLES = ("layers", "blocks", "inserts", "groups", "xrefs")
_DRIVE = re.compile(r"^[A-Za-z]:/")
_MODES = ("relative", "absolute")


# ======================================================================================
# 表访问与入参把关（自足实现：与 organization 的落点同款，避免私有名耦合）
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """造一个带机器可读 ``reason`` 的 ``CadError``（调用点一律 ``raise _fail(...)``）。"""
    return CadError(message, reason=reason, **details)


def _holder(target: Any) -> Any:
    """组织表的宿主：文档 → ``extra``；草图/同形映射 → 自身；草图对象 → 自身（属性挂载）。"""
    if isinstance(target, _MutableMappingABC):
        extra = target.get("extra")
        if isinstance(extra, _MutableMappingABC):
            return extra
        return target
    extra = getattr(target, "extra", None)
    if isinstance(extra, _MutableMappingABC):
        return extra
    if extra is None and hasattr(target, "extra"):
        try:
            setattr(target, "extra", {})
        except Exception:  # 冻结对象/只读属性：回落自身，不硬改别人的模型
            pass
        else:
            return getattr(target, "extra")
    if hasattr(target, "entities") or hasattr(target, "organization"):
        return target
    return None


def _store(target: Any, *, create: bool = True) -> dict[str, Any] | None:
    """取（必要时建）组织表 dict；``create=False`` 且表不存在 ⇒ ``None``（只读侧不建表）。"""
    holder = _holder(target)
    if holder is None:
        if create:
            raise _fail("invalid_target", "organization tables need a document or sketch target",
                        target_type=type(target).__name__)
        return None
    mapping = isinstance(holder, _MutableMappingABC)
    store = holder.get("organization") if mapping else getattr(holder, "organization", None)
    if isinstance(store, dict):
        return store
    if not create:
        return None
    store = {"schema": _SCHEMA}
    for key in _TABLES:
        store[key] = []
    if mapping:
        holder["organization"] = store
    else:
        setattr(holder, "organization", store)
    return store


def _table(store: dict[str, Any], key: str) -> list[Any]:
    """取表（缺失/类型不对就补一张空表；只加键不改别人的键）。"""
    rows = store.get(key)
    if not isinstance(rows, list):
        rows = []
        store[key] = rows
    return rows


def _text(value: Any, what: str) -> str:
    """非空字符串把关（``invalid_argument``）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "must be a non-empty string", what=what,
                    value_type=type(value).__name__)
    return value.strip()


def _find_xref(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 或大小写不敏感名字找参照行（``unknown_reference``）。"""
    text = _text(ref, what="xref")
    for row in _table(store, "xrefs"):
        if isinstance(row, dict) and row.get("uuid") == text:
            return row
    hits = [row for row in _table(store, "xrefs")
            if isinstance(row, dict) and str(row.get("name", "")).casefold() == text.casefold()]
    if not hits:
        raise _fail("unknown_reference", "no external reference with this name or uuid", reference=text)
    return hits[0]


def new_uuid() -> str:
    """新记录 uuid（本模块只在 ``xref_path_set(name=...)`` 登记时用一次）。"""
    return _uuid.uuid4().hex


# ======================================================================================
# 路径代数（纯字符串运算，零文件 I/O）
# ======================================================================================


def _posix(text: str) -> str:
    """分隔符归一：``\\`` → ``/``（DXF 里 XREF 路径常是 Windows 形态）。"""
    return text.replace("\\", "/")


def _is_posix_abs(path: str) -> bool:
    """POSIX 绝对（``/a``）；``//host/share`` 不算（归 UNC/foreign）。"""
    return path.startswith("/") and not path.startswith("//")


def _is_foreign_abs(path: str) -> bool:
    """盘符或 UNC 绝对（``C:/a``、``//host/share``）：posixpath 解不了，不做相对化。"""
    return bool(_DRIVE.match(path)) or path.startswith("//")


def _norm(value: Any, what: str) -> str:
    return posixpath.normpath(_posix(_text(value, what)))


def _base_dir(value: Any) -> str:
    return posixpath.normpath(_posix(_text(value, "base_dir")))


def _mode(value: Any) -> str:
    text = _text(value, "mode")
    if text not in _MODES:
        raise _fail("invalid_argument", "mode must be 'relative' or 'absolute'", mode=text)
    return text


def _abs_of(stored: str, base: str) -> str:
    """存的那串 → 目标绝对形态（绝对原样归一；相对按 ``base`` 解）。"""
    if _is_posix_abs(stored) or _is_foreign_abs(stored):
        return posixpath.normpath(stored)
    return posixpath.normpath(posixpath.join(base, stored))


def _stored_of(target_abs: str, base: str, mode: str) -> str:
    """目标绝对形态 → 要写进图档的那串；解不回去的形态**显式降级**，不静默错图。"""
    if mode == "absolute":
        return target_abs
    if _is_foreign_abs(target_abs) or _is_foreign_abs(base):
        raise _fail("unsupported_path", "relative mode is unavailable for drive/UNC paths",
                    path=target_abs, base_dir=base)
    if _is_posix_abs(target_abs) != _is_posix_abs(base):
        raise _fail("invalid_argument", "relative mode needs target and base_dir to share absolute/relative kind",
                    path=target_abs, base_dir=base)
    return posixpath.relpath(target_abs, base)


def _row_stored(row: dict[str, Any]) -> str | None:
    """行上"写进图档的那串"；缺失/空白 ⇒ ``None``（跳过，不猜）。"""
    value = row.get("path")
    if isinstance(value, str) and value.strip():
        return _posix(value.strip())
    return None


def _row_anchor(row: dict[str, Any], fallback_base: str) -> str:
    """解释锚点：行上的 ``path_base`` 优先（写下时的宿主目录），否则用调用方给的 ``base_dir``。"""
    value = row.get("path_base")
    if isinstance(value, str) and value.strip():
        return _posix(value.strip())
    return fallback_base


def _snapshot(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """路径四件套快照（判断"这一行真的被改了没有"）。"""
    return (row.get("path"), row.get("path_mode"), row.get("path_base"), row.get("path_abs"))


def _sorted_rows(store: dict[str, Any]) -> list[dict[str, Any]]:
    """按名字（大小写不敏感）+ uuid 排序的参照行：返回列表顺序**可复现**。"""
    rows = [row for row in _table(store, "xrefs") if isinstance(row, dict)]
    rows.sort(key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("uuid", ""))))
    return rows


# ======================================================================================
# 操作（每个 op(sk_or_doc, **kwargs) -> list[str]）
# ======================================================================================


def xref_path_set(target: Any, *, xref: Any, path: Any, base_dir: Any,
                  mode: str = "relative", name: Any = None) -> list[str]:
    """把一条参照的路径按指定形态写进图档 → ``[xref_uuid]``（**真数据后果**：四件套一起落）。

    参数：``xref`` uuid 或名字（大小写不敏感）；``path`` 目标路径（``\\`` 会转 ``/``）；
    ``base_dir`` 宿主图档目录（相对形态的锚点，也写进 ``path_base``）；``mode``
    ``"relative"``（默认，AutoCAD 的"相对宿主图"）或 ``"absolute"``；``name`` 只在参照不存在时
    用来**登记**一条只做路径管理的记录（省略则抛 ``unknown_reference``）。

    边界：``mode="relative"`` 遇盘符/UNC 目标 ⇒ ``unsupported_path``（posixpath 解不回原目标）；
    目标与 ``base_dir`` 绝对/相对形态混用 ⇒ ``invalid_argument``；空 ``path`` ⇒ ``invalid_argument``。
    本层零文件 I/O：**不校验文件是否存在**，也不把 ``loaded`` 改成 ``True``。
    """
    store = _store(target)
    text = _text(xref, what="xref")
    row: dict[str, Any] | None = None
    for candidate in _table(store, "xrefs"):
        if isinstance(candidate, dict) and candidate.get("uuid") == text:
            row = candidate
            break
    if row is None:
        hits = [c for c in _table(store, "xrefs")
                if isinstance(c, dict) and str(c.get("name", "")).casefold() == text.casefold()]
        row = hits[0] if hits else None
    if row is None:
        if name is None:
            raise _fail("unknown_reference", "no external reference with this name or uuid", reference=text)
        row = {"uuid": new_uuid(), "name": _text(name, what="name"), "path": "",
               "at_nm": [0, 0], "scale": [1.0, 1.0], "rotation_deg": 0.0,
               "loaded": False, "entity_uuids": []}
        _table(store, "xrefs").append(row)
    mode_text = _mode(mode)
    base = _base_dir(base_dir)
    target_abs = _abs_of(_norm(path, "path"), base)
    row["path"] = _stored_of(target_abs, base, mode_text)
    row["path_mode"] = mode_text
    row["path_base"] = base
    row["path_abs"] = target_abs
    return [str(row["uuid"])]


def xref_path_mode(target: Any, *, base_dir: Any, mode: str = "relative") -> list[str]:
    """把**所有**参照统一成指定路径形态 → 被改动的 ``[xref_uuid, ...]``（按名字排序）。

    语义：行上 ``path_base``（写下时的宿主目录）是**解释锚点**，``base_dir`` 只对"还没被本模块管过"
    的行生效；因此把一批参照从相对切到绝对（或反向）**不会改变目标绝对路径**，只改
    ``path`` / ``path_mode`` / ``path_base`` / ``path_abs`` 四个键的写法。

    边界：两阶段执行（先算全批计划，任一行解不回原目标就抛 ``unsupported_path`` /
    ``invalid_argument``，此时表**一个键都没改**）；``path`` 缺失/为空的行跳过（它们由
    ``xref_path_audit`` 报出）；本操作**幂等** —— 再调一次同一 ``base_dir``/``mode`` 返回 ``[]``。
    """
    mode_text = _mode(mode)
    base = _base_dir(base_dir)
    store = _store(target, create=False)
    if store is None:
        return []
    plan: list[tuple[dict[str, Any], tuple[Any, Any, Any, Any], str, str]] = []
    for row in _sorted_rows(store):
        stored = _row_stored(row)
        if stored is None:
            continue
        anchor = _row_anchor(row, base)
        target_abs = _abs_of(stored, anchor)
        new_stored = _stored_of(target_abs, base, mode_text)  # 先算：抛错时尚未落盘
        plan.append((row, _snapshot(row), new_stored, target_abs))
    changed: list[str] = []
    for row, before, new_stored, target_abs in plan:
        row["path"] = new_stored
        row["path_mode"] = mode_text
        row["path_base"] = base
        row["path_abs"] = target_abs
        if _snapshot(row) != before:
            changed.append(str(row.get("uuid")))
    return changed


def xref_path_rebase(target: Any, *, old_base: Any, new_base: Any) -> list[str]:
    """宿主图档换目录后重写相对路径 → 被重写的 ``[xref_uuid, ...]``（按名字排序）。

    语义：``old_base`` 是写相对路径时的宿主目录，``new_base`` 是移动后的宿主目录；对每条
    **相对形态**的参照，重算 ``relpath`` 使目标绝对路径**保持不变**（这正是"图档连同图一起搬"
    的反面：参照指向别处时，相对串必须跟着变）。绝对形态的行与 ``new_base`` 无关，不碰、不返回。

    边界：两阶段执行（先算全批，抛错时表不动）；没有行需要重写 ⇒ ``[]``（真为零）；
    盘符/UNC 形态的行按绝对处理（跳过）；``new_base`` 与目标绝对/相对形态混用 ⇒ ``invalid_argument``。
    """
    old = _base_dir(old_base)
    new = _base_dir(new_base)
    store = _store(target, create=False)
    if store is None:
        return []
    plan: list[tuple[dict[str, Any], tuple[Any, Any, Any, Any], str, str]] = []
    for row in _sorted_rows(store):
        stored = _row_stored(row)
        if stored is None:
            continue
        if _is_posix_abs(stored) or _is_foreign_abs(stored):
            continue
        target_abs = _abs_of(stored, _row_anchor(row, old))
        new_stored = _stored_of(target_abs, new, "relative")
        if new_stored == stored and row.get("path_base") == new:
            continue
        plan.append((row, _snapshot(row), new_stored, target_abs))
    changed: list[str] = []
    for row, before, new_stored, target_abs in plan:
        row["path"] = new_stored
        row["path_mode"] = "relative"
        row["path_base"] = new
        row["path_abs"] = target_abs
        if _snapshot(row) != before:
            changed.append(str(row.get("uuid")))
    return changed


def xref_path_audit(target: Any, *, base_dir: Any, root: Any = None,
                    mode: str = "relative") -> list[str]:
    """只读体检：报出**路径需要管/前后不一致**的参照 → ``[xref_uuid, ...]``（按名字排序）。

    判为"要管"的任一情形：``path`` 缺失/为空；``path`` 是不便携的绝对形态而 ``mode="relative"``；
    没被本模块管过（无合法 ``path_mode``）；``path_mode`` 与实际书写形态不符；
    ``path_abs`` 与按 ``path`` + ``path_base`` 现算的绝对目标**漂移**；给了 ``root`` 时解出的目标
    落在 ``root`` 之外（相对串跑出工程目录）。

    边界：纯查询，不建表、不写入；**空表与全部合规都返回 ``[]``** —— 要区分请先用
    ``organization.xref_filter`` 判断表里有没有参照（返回 ``[]`` 不得当成"图里没有参照"）。
    """
    mode_text = _mode(mode)
    base = _base_dir(base_dir)
    store = _store(target, create=False)
    if store is None:
        return []
    root_text = posixpath.normpath(_posix(_text(root, "root"))) if root is not None else None
    flagged: list[str] = []
    for row in _sorted_rows(store):
        stored = _row_stored(row)
        recorded_mode = row.get("path_mode")
        if stored is None:
            flagged.append(str(row.get("uuid")))
            continue
        bad = False
        absolute = _is_posix_abs(stored) or _is_foreign_abs(stored)
        if mode_text == "relative" and absolute:
            bad = True
        if not isinstance(recorded_mode, str) or recorded_mode not in _MODES:
            bad = True
        elif recorded_mode != ("absolute" if absolute else "relative"):
            bad = True
        resolved = _abs_of(stored, _row_anchor(row, base))
        recorded_abs = row.get("path_abs")
        if isinstance(recorded_abs, str) and recorded_abs.strip():
            if posixpath.normpath(_posix(recorded_abs.strip())) != resolved:
                bad = True
        if root_text is not None:
            inside = resolved == root_text or resolved.startswith(posixpath.join(root_text, ""))
            if not inside:
                bad = True
        if bad:
            flagged.append(str(row.get("uuid")))
    return flagged


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "xref_path_set": xref_path_set,
    "xref_path_mode": xref_path_mode,
    "xref_path_rebase": xref_path_rebase,
    "xref_path_audit": xref_path_audit,
}


# ======================================================================================
# 自测（真跑真断言；红就是红，绝不留假绿）
# ======================================================================================


def _selftest() -> int:
    """硬断言：相对/绝对互转、换目录保目标、盘符显式降级、失败批原子性、幂等、L1 未被污染。"""
    checks = 0

    def _ok(condition: bool, label: str) -> None:
        nonlocal checks
        if not condition:
            raise AssertionError("selftest failed: " + label)
        checks += 1

    sk: dict[str, Any] = {"entities": [], "constraints": [], "dimensions": []}

    # 1) 登记 + 相对形态落盘（真数据后果）
    u1 = xref_path_set(sk, xref="site-plan", name="site-plan", path="refs/site.dwg",
                       base_dir="/proj/a", mode="relative")[0]
    row1 = sk["organization"]["xrefs"][0]
    _ok(isinstance(u1, str) and bool(u1), "set returns an xref uuid")
    _ok(row1["path"] == "refs/site.dwg", "relative form stored as written")
    _ok(row1["path_abs"] == "/proj/a/refs/site.dwg", "absolute target derived")
    _ok(row1["path_mode"] == "relative" and row1["path_base"] == "/proj/a", "mode and base recorded")

    # 2) 换目录：相对串重写，但目标绝对路径不变（本片的核心不变量）
    moved = xref_path_rebase(sk, old_base="/proj/a", new_base="/proj/b")
    _ok(moved == [u1], "rebase reported the moved row")
    _ok(row1["path"] == "../a/refs/site.dwg", "rebase rewrote the relative form")
    _ok(_abs_of(row1["path"], row1["path_base"]) == "/proj/a/refs/site.dwg", "rebase preserved the target")

    # 3) 盘符路径：绝对模式下只归一分隔符；相对模式显式降级
    u2 = xref_path_set(sk, xref="title", name="title", path="C:\\shared\\title.dwg",
                       base_dir="/proj/a", mode="absolute")[0]
    row2 = sk["organization"]["xrefs"][1]
    _ok(row2["path"] == "C:/shared/title.dwg", "backslash normalized to posix")
    _ok(u2 != u1 and len(sk["organization"]["xrefs"]) == 2, "second reference registered separately")

    # 4) 体检：便携的相对行不报，不可便携的盘符绝对行报出
    flagged = xref_path_audit(sk, base_dir="/proj/b", root="/proj")
    _ok(u2 in flagged and u1 not in flagged, "audit flags the non-portable row only")

    # 5) 批量切相对：盘符行解不回原目标 ⇒ 报错，且表一个键都没改（两阶段原子性）
    before = [dict(item) for item in sk["organization"]["xrefs"]]
    try:
        xref_path_mode(sk, base_dir="/proj/b", mode="relative")
    except CadError as exc:
        _ok(exc.reason == "unsupported_path", "drive path relative mode downgrades explicitly")
    else:
        raise AssertionError("selftest failed: drive path must not be silently relativised")
    _ok(sk["organization"]["xrefs"] == before, "failed batch left every row untouched")

    # 6) 批量切绝对：两行都改；同一调用再跑一次返回 []（幂等 = 真为零）
    switched = xref_path_mode(sk, base_dir="/proj/b", mode="absolute")
    _ok(set(switched) == {u1, u2}, "mode switch touched both rows")
    _ok(row1["path"] == "/proj/a/refs/site.dwg" and _is_posix_abs(row2["path"]) is False,
        "stored forms follow the requested mode")
    _ok(xref_path_mode(sk, base_dir="/proj/b", mode="absolute") == [], "identical second call changes nothing")

    # 7) 未知参照：机器可读 reason，不是默默建一条
    try:
        xref_path_set(sk, xref="ghost", path="/x.dwg", base_dir="/proj/b")
    except CadError as exc:
        _ok(exc.reason == "unknown_reference", "unknown xref rejected with reason")
    else:
        raise AssertionError("selftest failed: unknown xref must raise CadError")

    # 8) L1 未被污染：实体字段/键集原样
    _ok(sk["entities"] == [] and sk["constraints"] == [] and sk["dimensions"] == [],
        "L1 entity containers untouched")
    _ok(set(sk["organization"]) == {"schema", "layers", "blocks", "inserts", "groups", "xrefs"},
        "organization tables keep the frozen layout")
    return checks


if __name__ == "__main__":
    _passed = _selftest()
    print("SELFTEST family=organization assertions=%d passed=%d ops=%s"
          % (_passed, _passed, ",".join(OPERATIONS)))
    print("FEATURE_OK family=organization op=xref_path_set verdict=PASS")


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "xref_path_audit": {"tier": "query", "readonly": True, "returns": "data"},
}
