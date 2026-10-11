#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cadcore.ops.features.organization__w26 —— organization 族实现片 w26：无用图层清理 + 图层合并/删除。

一句话用途
----------
补上 organization（组织与复用）族 must_have 里"无用图层清理、图层合并/删除"这一条：
**未使用图层判定（PURGE 的判定面）+ 批量清理 + 图层合并（LAYMRG 同口径）**。

为什么是这一条（编号口径，可复算）
----------------------------------
`docs/research/cad-parity/features/autocad-organization.json` 的 `must_have` 共 **21** 条（另有
`nice_to_have` 8 条，不算）；`docs/research/cad-parity/status/autocad-organization.json` 的 item 级
status 里仍 missing/partial 的共 **14** 条（按清单顺序 0..13）：
0 图层创建与图层特性管理(p) / 1 图层开关冻结锁定与当前层(p) / 2 对象指定图层与改层(p) /
**3 无用图层清理、图层合并/删除(p)** / 4 动态块与块编辑器(m) / 5 块属性编辑与批量改属性(p) /
6 属性提取成表或文件(m) / 7 写块(m) / 8 外部参照附着与覆盖(p) / 9 参照状态管理(p) /
10 参照路径与相对路径管理(p) / 11 在位编辑参照(m) / 12 图纸集与图纸列表(m) /
13 图纸集自定义特性与字段联动(p)。
本片序号 w26 → 取第 17 号：17 mod 14 = **3** ⇒ 本条。清单里其余 13 条**仍未实现**，
本文件不碰、也不假装覆盖（status JSON 的顶层计数器 implemented=0/partial=4/missing=17 与 item 级
status 不一致，本文件以 **item 级 status** 为准，因为它才是逐条可复核的那份）。

现状缺口（status JSON 的 note 原文口径）
----------------------------------------
`cadcore.ops.organization` 只有 `layer_delete`（单层删除，层上有实体还必须给 `move_to`）：
**没有**"未使用图层"判定、**没有**批量清理、**没有**图层合并 —— `layer_merge` 在代码里不存在。

真实现（不是桩）：真数据后果，几何零改动
----------------------------------------
- ``layer_unused(target, *, include_reserved=False)`` —— 纯查询：返回**层上实体数为 0** 的层 uuid
  （缺省排除"永不可清"的保留层：层 ``"0"``、锁定层、当前层；`include_reserved=True` 连它们一起列）。
- ``layer_purge(target)`` —— 把未使用层（判定同 `layer_unused` 缺省口径）**成批删掉**，
  返回**按层名（大小写不敏感）排序**的被删层 uuid。只删层表记录：未使用 = 层上本来就没有实体，
  所以**实体列表逐字节不变**（自检硬断言，不是口头保证）。
- ``layer_merge(target, *, sources, into, create=False)`` —— LAYMRG 同口径：把 `sources` 各层上的实体
  **改到 `into` 层**（`geom` / `kind` / `uuid` / `construction` 一个纳米都不动），再删掉各源层记录；
  若源层正是**当前层**，当前层设置跟村落 `into`（不留指向已删层的悬挂设置）。
  返回 = **源层 uuid（`sources` 入参顺序、去重）** + **被改层实体 uuid（在实体表中的出现顺序）**。
  源层为空也照样删记录（AutoCAD LAYMRG 允许合并空层），此时返回里只有层 uuid。

边界（写下来，防止下游靠猜；错误一律 `cadcore.errors.CadError(reason=...)`）
----------------------------------------------------------------------------
- ``layer_merge``：源层含层 ``"0"`` ⇒ ``reserved_layer``；`into` 等于任一源层 ⇒ ``invalid_argument``；
  源层或 `into` 锁定 ⇒ ``layer_locked``；`into` 不存在且 `create=False` ⇒ ``unknown_layer``；
  `sources` 为空 ⇒ ``empty_selection``；未知层 ⇒ ``unknown_layer``。
  **先全量校验、后统一写回**：半路失败时一个实体、一个层记录都不动（自检用"锁定源层"验这一条）。
- ``layer_purge`` / ``layer_unused``：**永不**触碰层 ``"0"``、锁定层、当前层。查询路径不自举层 ``"0"``；
  组织表里还没有层记录时返回 ``[]``，口径同 `layer_filter`：这是**"表里还没有记录"（未采）**，
  **不是**"图上没有未使用层"（不许把未采读成 0）。
- 长度字段整数纳米；本文件的三个操作**没有任何长度字段**（只动层名与层表记录）。
- 复用而非重写：组织表定位与层表访问一律走 `cadcore.ops.organization` 的 `_view` / `_layers` /
  `_find_layer` / `_layer_name` / `_name` / `_fail` / `layer_create` / `current_layer`，结构访问走
  `cadcore.sketch.constraints` 的 `_get` / `_set` / `sketch_entities` / `new_uuid` —— 本文件不抄第二份，
  否则"组织表放哪"会有两个真源。
- 本片只写这一份文件（**不写 `__init__.py`**，归别的席；不改族模块 `ops/organization.py`）。

冻结接口
--------
``FAMILY = "organization"``；``OPERATIONS: dict[str, Callable]``；每个操作
``op(sk_or_doc, **kwargs) -> list[str]``（返回受影响 uuid 列表，顺序见各自 docstring）。
非 docstring 的字符串字面量一律 ASCII（面向用户文案走语言层 key，见 `tools/cad/i18n_gate.py`）。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/features/organization__w26.py 时的引导
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    __package__ = "cadcore.ops.features"  # noqa: A001 - 与族模块同款引导，供相对 import 用

from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from cadcore.errors import CadError  # 绝对导入（隔离区深一层，`...errors` 会解析成 cadcore.ops.errors）
from cadcore.sketch.constraints import _get, _set, new_uuid, sketch_entities
from cadcore.ops.organization import (_DEFAULT_LAYER, _fail, _find_layer, _layer_name, _layers, _name,
                            _view, current_layer, layer_create)

__all__ = ["FAMILY", "OPERATIONS", "layer_merge", "layer_purge", "layer_unused"]

#: 族 id（冻结，与族模块同名）。
FAMILY = "organization"

#: 当前层设置在文档 `extra` / 草图里的键（与 `ops/organization.py` 的 CLAYER 口径一致）。
_CURRENT_LAYER_KEY = "current_layer"


# ======================================================================================
# 层占用账（判定"未使用"的唯一口径）
# ======================================================================================


def _current_layer_name(target: Any) -> str | None:
    """当前层名（读 `doc.extra["current_layer"]` / `sk["current_layer"]`；没设过 ⇒ ``None``）。

    为什么本文件自己读而不调 `current_layer`：那个操作是**写**入口（会改状态），判定路径必须是纯读。
    """
    for holder in (_get(target, "extra", None), target):
        if isinstance(holder, _MutableMappingABC):
            value = holder.get(_CURRENT_LAYER_KEY)
            if isinstance(value, str) and value:
                return value
    return None


def _settings_holder(target: Any) -> Any:
    """当前层设置的落点容器（文档 → `extra`；其余 → 目标自身），读不到 ⇒ ``None``。"""
    extra = _get(target, "extra", None)
    if isinstance(extra, _MutableMappingABC):
        return extra
    if isinstance(target, _MutableMappingABC):
        return target
    return None


def _usage_counts(store: dict[str, Any], lists: list[list[Any]]) -> dict[str, int]:
    """层名（casefold）→ 该层上的实体数（含所有草图/图纸；块实例的成员本来就是普通实体）。"""
    counts: dict[str, int] = {}
    for group in lists:
        for entity in group:
            key = _layer_name(store, entity).casefold()
            counts[key] = counts.get(key, 0) + 1
    return counts


def _is_reserved(row: dict[str, Any], name: str, current: str | None) -> bool:
    """保留层 = 永不可清的层：层 ``"0"``、锁定层、当前层（AutoCAD 连当前层也不让 PURGE）。"""
    if name == _DEFAULT_LAYER:
        return True
    if row.get("locked"):
        return True
    return current is not None and name.casefold() == current.casefold()


def _layer_uuids(rows: list[Any]) -> list[str]:
    """层行 → uuid（按层名大小写不敏感排序；uuid 读不出来就报 `corrupt_store`，不静默跳过）。"""
    ordered = sorted(rows, key=lambda row: str(row.get("name", "")).casefold())
    out: list[str] = []
    for row in ordered:
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise _fail("corrupt_store", "layer row has no usable uuid", layer=str(row.get("name", "")))
        out.append(uuid)
    return out


def _source_refs(sources: Any) -> list[str]:
    """`sources` 入参把关 → 层名字符串列表（空选择报 `empty_selection`，不静默 no-op）。"""
    if isinstance(sources, (str, bytes)):
        items: list[Any] = [sources]
    elif isinstance(sources, (list, tuple)):
        items = list(sources)
    else:
        raise _fail("invalid_argument", "sources must be a layer name or a sequence of layer names",
                    value_type=type(sources).__name__)
    if not items:
        raise _fail("empty_selection", "sources is empty: refusing to no-op silently")
    refs: list[str] = []
    for ref in items:
        if not isinstance(ref, str) or not ref:
            raise _fail("invalid_argument", "layer reference must be a non-empty string", value=repr(ref))
        refs.append(ref)
    return refs


# ======================================================================================
# 操作
# ======================================================================================


def layer_unused(target: Any, *, include_reserved: bool = False) -> list[str]:
    """未使用图层判定 → **按层名（大小写不敏感）排序**的 `[layer_uuid, ...]`。

    判据只有一条、机器可复核：**该层上的实体数 = 0**（跨目标里全部草图/图纸统计）。

    参数：`include_reserved=True` 时连保留层（层 `"0"`、锁定层、当前层）一起列 —— 它们"未使用"
    但不"可清"，两个概念分开；缺省只给 `layer_purge` 真会删掉的那些。

    边界：**纯查询**，不建表、不自举层 `"0"`、不写任何字段；组织表里还没有层记录时返回 `[]`
    （口径同 `layer_filter`：**"表里还没有记录"（未采）**，不代表图上没有未使用层）。
    """
    store, lists = _view(target, create=False)
    if store is None:
        return []
    counts = _usage_counts(store, lists)
    current = _current_layer_name(target)
    rows: list[Any] = []
    for row in _layers(store, bootstrap=False):
        name = str(row.get("name", ""))
        if counts.get(name.casefold(), 0) > 0:
            continue
        if not include_reserved and _is_reserved(row, name, current):
            continue
        rows.append(row)
    return _layer_uuids(rows)


def layer_purge(target: Any) -> list[str]:
    """清理未使用图层（PURGE 的图层面）→ **按层名排序**的被删层 `[layer_uuid, ...]`。

    语义：把 `layer_unused` 判定出来的层**成批删掉**（层 `"0"` / 锁定层 / 当前层除外，判定同源）。

    边界：**只删层表记录，一个实体都不删** —— "未使用"的定义就是层上没有实体，所以实体数与实体
    内容逐字节不变（自检硬断言）；没有可清的层时返回 `[]`（幂等，第二次调用必然 `[]`）。
    层表为空时先自举缺省层 `"0"`（写路径口径同族模块），而 `"0"` 永不入清理名单。
    """
    store, lists = _view(target)
    counts = _usage_counts(store, lists)
    current = _current_layer_name(target)
    rows = _layers(store)
    victims: list[Any] = []
    for row in rows:
        name = str(row.get("name", ""))
        if counts.get(name.casefold(), 0) > 0:
            continue
        if _is_reserved(row, name, current):
            continue
        victims.append(row)
    for row in victims:
        rows.remove(row)
    return _layer_uuids(victims)


def layer_merge(target: Any, *, sources: Any, into: Any, create: bool = False) -> list[str]:
    """图层合并（LAYMRG 同口径）→ `[源层 uuid（入参顺序、去重）, 被改层实体 uuid（出现顺序）]`。

    语义：`sources` 各层上的实体**改到 `into` 层**（`kind` / `geom` / `uuid` / `construction` 一个
    纳米都不动），随后**删掉各源层记录**。源层为空也照样删记录。源层正好是当前层时，当前层设置
    跟着落到 `into`（不留指向已删层的悬挂设置）。

    参数：`sources` = 层名或层名序列（层名大小写不敏感，可给层 uuid）；`into` = 目标层；
    `create=True` 时目标层不存在就按缺省属性建（层名单独校验），缺省 `False` ⇒ `unknown_layer`。

    边界（全部先校验、后写回，半路失败一个实体都不动）：
    源层含层 `"0"` ⇒ `reserved_layer`；`into` 等于任一源层 ⇒ `invalid_argument`；源层或 `into`
    锁定 ⇒ `layer_locked`；`sources` 为空 ⇒ `empty_selection`；未知层 ⇒ `unknown_layer`。
    本操作**不删任何实体**，也不动几何。
    """
    refs = _source_refs(sources)
    store, lists = _view(target)
    rows = _layers(store)

    src_rows: list[Any] = []
    for ref in refs:
        row = _find_layer(store, ref)
        if not any(row is seen for seen in src_rows):
            src_rows.append(row)

    if create is True and not isinstance(into, str):
        raise _fail("invalid_argument", "create=True needs a layer name for into=", value=repr(into))
    if create:
        text = _name(into, what="into")
        hits = [row for row in rows if str(row.get("name", "")).casefold() == text.casefold()]
        if hits:
            dest = hits[0]
        else:
            layer_create(target, name=text)
            dest = _find_layer(store, text)
    else:
        dest = _find_layer(store, into)

    src_names = [str(row.get("name", "")) for row in src_rows]
    dest_name = str(dest.get("name", _DEFAULT_LAYER))
    keys = {item.casefold() for item in src_names}
    if _DEFAULT_LAYER in src_names:
        raise _fail("reserved_layer", "layer '0' cannot be merged away", layer=_DEFAULT_LAYER)
    if any(dest is row for row in src_rows) or dest_name.casefold() in keys:
        raise _fail("invalid_argument", "into= must differ from every source layer", layer=dest_name)
    if dest.get("locked"):
        raise _fail("layer_locked", "destination layer is locked", layer=dest_name)
    for row in src_rows:
        if row.get("locked"):
            raise _fail("layer_locked", "unlock the source layer before merging it",
                        layer=str(row.get("name", "")))

    moved: list[Any] = []
    for group in lists:
        for entity in group:
            if _layer_name(store, entity).casefold() in keys:
                _set(entity, "layer", dest_name)
                moved.append(entity)
    for row in src_rows:
        rows.remove(row)

    current = _current_layer_name(target)
    if current is not None and current.casefold() in keys:
        holder = _settings_holder(target)
        if isinstance(holder, _MutableMappingABC):
            _set(holder, _CURRENT_LAYER_KEY, dest_name)

    return [str(row.get("uuid")) for row in src_rows] + [str(_get(entity, "uuid", "")) for entity in moved]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "layer_unused": layer_unused,
    "layer_purge": layer_purge,
    "layer_merge": layer_merge,
}


# ======================================================================================
# 自检（真跑；>=2 条硬断言；任一失败即 AssertionError，绝不吞）
# ======================================================================================

_CHECKS: list[str] = []


def _ok(cond: Any, label: str) -> None:
    """硬断言（失败 loud：直接 AssertionError，带可定位的 label）。"""
    if not cond:
        raise AssertionError(label)
    _CHECKS.append(label)


def _expect_error(call: Callable[[], Any], reason: str, *, label: str) -> None:
    """断言"这个调用必须以这个 reason 报 CadError"（异常类型与 reason 两个都要对）。"""
    try:
        call()
    except CadError as exc:
        _ok(exc.reason == reason, f"{label}: reason={exc.reason!r} expected={reason!r}")
        return
    raise AssertionError(f"{label}: expected CadError(reason={reason!r}), but no error was raised")


def _line(uuid: str, layer: str, x: int) -> dict[str, Any]:
    """L1 line 实体（整数纳米；geom 只在合并前后对比用）。"""
    return {"uuid": uuid, "kind": "line", "layer": layer, "construction": False,
            "geom": {"x1": x, "y1": 0, "x2": x + 1000, "y2": 1000}}


def _geom_snapshot(sketch: Any) -> dict[str, dict[str, Any]]:
    """实体几何快照（uuid → geom 副本），用来证明"改层不动几何"。"""
    return {str(_get(entity, "uuid", "")): dict(_get(entity, "geom", {}))
            for entity in sketch_entities(sketch)}


def _uuid_named(target: Any, name: str) -> str:
    """按层名取层 uuid（找不到即 AssertionError —— 自检里出现"找不到"就是夹具坏了）。"""
    store, _ = _view(target, create=False)
    if store is None:
        raise AssertionError(f"no organization table for layer {name!r}")
    for row in _layers(store, bootstrap=False):
        if str(row.get("name", "")).casefold() == name.casefold():
            return str(row.get("uuid"))
    raise AssertionError(f"layer {name!r} not found")


def main() -> int:
    from cadcore import docmodel  # 自检专用（族模块本身不依赖文档层；绝对导入：隔离区深一层）

    sketch: dict[str, Any] = {"uuid": "sk-w26", "plane": "XY", "entities": [
        _line("e1", "A", 0), _line("e2", "A", 2000), _line("e3", "B", 4000), _line("e4", "0", 6000)],
        "constraints": [], "dimensions": []}
    layer_create(sketch, name="A")
    layer_create(sketch, name="B")
    layer_create(sketch, name="EMPTY")
    layer_create(sketch, name="LOCKED", locked=True)
    layer_create(sketch, name="CUR")
    layer_create(sketch, name="C")
    sketch[_CURRENT_LAYER_KEY] = "CUR"  # 当前层设置（与 current_layer 写的是同一个键）
    table = sketch["organization"]["layers"]
    uuid_of = {str(row.get("name")): str(row.get("uuid")) for row in table}

    # ---- 1. 未使用图层判定 ------------------------------------------------------------
    _ok(layer_unused(sketch) == [uuid_of["EMPTY"]],
        f"unused layers: {layer_unused(sketch)} != [EMPTY]")
    _ok(set(layer_unused(sketch, include_reserved=True)) ==
        {uuid_of["EMPTY"], uuid_of["LOCKED"], uuid_of["CUR"], uuid_of["0"]},
        "include_reserved must add layer 0 / locked / current")
    _ok(layer_unused({}) == [], "target without an organization table reads as unmeasured []")

    # ---- 2. 清理：只删层记录，实体与几何逐字节不动 --------------------------------------
    geom_before = _geom_snapshot(sketch)
    _ok(layer_purge(sketch) == [uuid_of["EMPTY"]], f"purge: {layer_purge(sketch)} != [EMPTY]")
    names_after = [str(row.get("name")) for row in table]
    _ok("EMPTY" not in names_after and {"0", "A", "B", "CUR", "LOCKED", "C"} <= set(names_after),
        f"reserved layers must survive purge: {names_after}")
    _ok(len(sketch_entities(sketch)) == 4 and _geom_snapshot(sketch) == geom_before,
        "purge must not delete or move any entity")
    _ok(layer_purge(sketch) == [], "purge is idempotent once nothing is unused")

    # ---- 3. 合并：改层真发生，几何零改动，源层记录消失 ----------------------------------
    merged = layer_merge(sketch, sources="A", into="C")
    _ok(merged == [uuid_of["A"], "e1", "e2"], f"merge return: {merged}")
    _ok([_get(entity, "layer") for entity in sketch_entities(sketch)] == ["C", "C", "B", "0"],
        f"entities must land on C: {[_get(e, 'layer') for e in sketch_entities(sketch)]}")
    _ok("A" not in [str(row.get("name")) for row in table], "source layer record must be gone")
    _ok(_geom_snapshot(sketch) == geom_before, "merge must not touch geometry")
    _ok(sketch[_CURRENT_LAYER_KEY] == "CUR", "current layer must not move when it is not a source")

    # ---- 4. 先全量校验后写回 + 各种拒绝 ------------------------------------------------
    layer_create(sketch, name="LOCKSRC", locked=True)
    sketch_entities(sketch).append(_line("e5", "LOCKSRC", 8000))
    before_layers = [_get(entity, "layer") for entity in sketch_entities(sketch)]
    _expect_error(lambda: layer_merge(sketch, sources=["LOCKSRC"], into="C"), "layer_locked",
                  label="locked source layer")
    _ok([_get(entity, "layer") for entity in sketch_entities(sketch)] == before_layers and
        "LOCKSRC" in [str(row.get("name")) for row in table],
        "a failed merge must not write anything (validate-then-write)")
    _expect_error(lambda: layer_merge(sketch, sources=["0"], into="C"), "reserved_layer",
                  label="layer 0 as source")
    _expect_error(lambda: layer_merge(sketch, sources=["B"], into="B"), "invalid_argument",
                  label="merge into itself")
    _expect_error(lambda: layer_merge(sketch, sources=["NOPE"], into="C"), "unknown_layer",
                  label="unknown source layer")
    _expect_error(lambda: layer_merge(sketch, sources=[], into="C"), "empty_selection",
                  label="empty sources")
    _expect_error(lambda: layer_merge(sketch, sources=["B"], into="LOCKED"), "layer_locked",
                  label="locked destination layer")
    _expect_error(lambda: layer_merge(sketch, sources=["CUR"], into="NOPE"), "unknown_layer",
                  label="unknown into without create")
    _ok(layer_merge(sketch, sources=["CUR"], into="NEW", create=True) == [uuid_of["CUR"]],
        "empty source layer is still removed and create=True builds the destination")
    _ok(sketch[_CURRENT_LAYER_KEY] == "NEW", "current layer follows the merge destination")

    # ---- 5. 文档目标：当前层跟随 + L1 不变量 + 往返回来 ---------------------------------
    doc = docmodel.new_document()
    doc.sketches.append({"uuid": new_uuid(), "plane": "XY", "entities": [
        {"uuid": new_uuid(), "kind": "line", "layer": "P", "construction": False,
         "geom": {"x1": 0, "y1": 0, "x2": 3000, "y2": 4000}},
        {"uuid": new_uuid(), "kind": "line", "layer": "Q", "construction": False,
         "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": 5000}}], "constraints": [], "dimensions": []})
    layer_create(doc, name="P")
    layer_create(doc, name="Q")
    layer_create(doc, name="R")
    _ok(layer_unused(doc) == [_uuid_named(doc, "R")], f"doc unused: {layer_unused(doc)}")
    _ok(current_layer(doc, name="P") == [_uuid_named(doc, "P")], "current_layer on a document")
    _ok(doc.extra[_CURRENT_LAYER_KEY] == "P", "current layer lives in doc.extra")
    _ok(len(layer_merge(doc, sources=["P"], into="R")) == 2, "merge return = 1 layer + 1 entity")
    _ok(doc.extra[_CURRENT_LAYER_KEY] == "R", "current layer follows the merge destination (doc)")
    _ok(docmodel.check_invariants(doc) == [], f"L1 invariants: {docmodel.check_invariants(doc)}")
    again = docmodel.document_from_l1(docmodel.document_to_l1(doc))
    _ok(sorted(str(row.get("name")) for row in again.extra["organization"]["layers"]) == ["0", "Q", "R"],
        "layer table survives the L1 round trip")
    _ok([_get(entity, "layer", "0") for entity in sketch_entities(again.sketches[0])] == ["R", "Q"],
        "merged layer assignment survives the L1 round trip")
    _ok(again.extra[_CURRENT_LAYER_KEY] == "R", "current layer survives the L1 round trip")

    print(f"W26_ORGANIZATION checks={len(_CHECKS)} ops={','.join(sorted(OPERATIONS))} "
          f"geometry_untouched=1")
    print("FEATURE_OK family=organization op=layer_purge verdict=PASS")
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())
