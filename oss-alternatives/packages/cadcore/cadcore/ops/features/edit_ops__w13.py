#!/usr/bin/env python3
"""cadcore.ops.features.edit_ops__w13 —— edit_ops 族实现片 w13：第 4 号 must_have「删除（ERASE）」。

本族仍 missing/partial 的 must_have（按 ``docs/research/cad-parity/status/autocad-edit.json`` 的
``items`` 顺序编号 0..N-1；已 implemented 的「放弃与重做」不占号）
------------------------------------------------------------------
    0  对象选择（窗选/交叉窗选/栏选/加减选）   partial
    1  夹点编辑                                partial
    2  对象捕捉与追踪                          partial
    3  精确坐标输入                            partial
    4  删除                                    partial   <== 本实现片负责的那一条
    5  移动                                    partial
    6  复制                                    partial
    7  旋转                                    partial
    8  缩放                                    partial
    9  镜像                                    partial
    10 偏移                                    partial
    11 阵列                                    partial
    12 修剪                                    partial
    13 延伸                                    partial
    14 圆角                                    partial
    15 倒角                                    partial
    16 拉伸                                    partial
    17 分解                                    partial
    18 特性匹配                                partial
    19 对齐                                    missing

取模说明：N=20 ⇒ ``4 % 20 == 4``，故本片做第 4 号「删除」。清单来源与 api 均现读
``docs/research/cad-parity/spec/edit_ops.json`` 的 ``items[4]``：
``erase(sk_or_doc, *, refs: Sequence[str]) -> list[str]``。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "edit_ops"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回**受影响实体的 uuid 列表**；
- 长度一律整数纳米（nm）；错误一律抛 ``cadcore.errors.CadError``（带机器可读 ``reason``）。

做了什么（真几何 / 真数据后果，不是桩）
--------------------------------------
``erase`` 在 L1 草图/文档上**真的移除实体**：从 ``entities`` 表摘掉 ``refs`` 点到的实体，并**级联清掉**
引用它们的约束（``constraints``）、尺寸（``dimensions``）与标注类实体，避免留下悬空引用。
删除与清理是**一次原子写**：先把新容器全部算好 → 校验清理彻底 → 才落到对象上；任何一步失败都
抛 ``CadError(reason="geometry_invalid")`` 并把改过的容器恢复原值（一个字节都不动）。
返回**被删实体的原 uuid**（顺序 = ``refs`` 顺序，重复 uuid 去重）。

口径（与冻结接口逐条对齐）
--------------------------
- ``refs`` 为空 ⇒ ``CadError(reason="empty_selection")``。注意：``[]`` 作为**返回值**表示"真零，
  不是失败"；但作为**入参**表示选择集为空 ⇒ 抛错，不静默返回 ``[]``（静默会把没做伪装成真零）。
- 任一 uuid 不存在 ⇒ ``CadError(reason="unknown_reference")``，**一个实体都不删**（不许部分删除后
  静默返回）。
- 清理后仍有引用指向被删实体、或落盘失败 ⇒ ``CadError(reason="geometry_invalid")``，删除整体不生效。
- 解析不到草图 ⇒ ``CadError(reason="sketch_missing")``（不静默造空草图）。
- 非 ``CadError`` 的入参错误（TypeError/ValueError/KeyError）就地转 ``CadError(reason="bad_arg")``。

落点与边界（写明白，别让下游猜）
--------------------------------
- ``sk_or_doc`` 两种形态：含 ``entities`` ⇒ 视为草图；含 ``sketches`` ⇒ 取 ``active_sketch`` 指向的
  草图（int 下标 / 草图 uuid 字符串都认），缺该键取 ``sketches[0]``；都缺 ⇒ ``sketch_missing``。
  映射与同形对象（属性访问）都支持。
- 本 op **不做**窗口/交叉/围栏选择（那是第 0 号 must_have「对象选择」的活）；``refs`` 只收 uuid
  字符串，或 ``[uuid, sub]`` 形态的 ref（取第 0 段，与 ``cadcore.sketch.constraints`` 的 ref 口径一致）。
- 级联只作用于**引用键**（``refs`` / ``entities`` / ``ref`` / ``target`` / ``targets`` / ``anchors`` /
  ``associates`` / ``assoc`` / ``boundary``），**不**把实体自己的 ``uuid`` 当引用（否则误删自己）。
- 实体级联只清**标注类**（``kind`` ∈ {dimension, dim, leader, mleader, annotation}）且引用被删实体的
  那一条；其它 kind 即便带 ``refs`` 键也不动（宁可留下可疑键，也不误删用户几何）。
  约束/尺寸表里的条目只要引用被删实体就整条清掉（AutoCAD 关联尺寸随对象删除的语义）。
- 长度参数：本 op 无（``params_nm = NA``）；无 ``-1`` 未采语义 —— ``refs`` 缺席即 ``bad_arg``。
- 级联清掉的 uuid **不**进返回值（返回契约只承诺 ``refs`` 顺序的原 uuid）；级联结果读文档即可见。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # 直接以脚本运行（cwd / sys.path 需能看见 cadcore 包）
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from cadcore.errors import CadError
else:  # 作为包导入：cadcore.ops.features.edit_ops__w13
    from cadcore.errors import CadError

FAMILY = "edit_ops"

__all__ = ["FAMILY", "OPERATIONS", "erase"]

_MISSING = object()

#: 引用键：只扫这些键，实体自己的 ``uuid`` 不算引用。
_REF_KEYS: tuple[str, ...] = ("refs", "entities", "ref", "target", "targets",
                              "anchors", "associates", "assoc", "boundary")
#: 会被级联清掉的标注类实体 kind（其余 kind 不因带 refs 键而被删）。
_ANNOTATION_KINDS = frozenset({"dimension", "dim", "leader", "mleader", "annotation"})
#: 参与级联清理的草图容器。
_CASCADE_FIELDS: tuple[str, ...] = ("constraints", "dimensions")


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """统一构造带机器可读 reason 的 CadError。"""
    return CadError(detail, reason=reason, **details)


def _get(obj: Any, key: str, default: Any = _MISSING) -> Any:
    """映射按 key 取；同形对象按属性取（缺省返回 ``default``，不抛）。"""
    if isinstance(obj, Mapping):
        return obj[key] if key in obj else default
    return getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    """写回草图容器；只读映射 / 只读对象会抛 TypeError ⇒ 调用方转 geometry_invalid。"""
    if isinstance(obj, Mapping):
        obj[key] = value  # type: ignore[index]
    else:
        setattr(obj, key, value)


def _sketch_of(sk_or_doc: Any) -> Any:
    """按冻结口径把 ``sk_or_doc`` 解析成草图容器；解析不到抛 sketch_missing。"""
    if sk_or_doc is None:
        raise _fail("sketch_missing", "sk_or_doc is None")
    if _get(sk_or_doc, "entities", _MISSING) is not _MISSING:
        return sk_or_doc
    sketches = _get(sk_or_doc, "sketches", _MISSING)
    if sketches is _MISSING or sketches is None:
        raise _fail("sketch_missing", "sk_or_doc has neither 'entities' nor 'sketches'")
    try:
        sketches = list(sketches)
    except TypeError as exc:
        raise _fail("sketch_missing", "'sketches' is not a sequence", error=str(exc)) from exc
    if not sketches:
        raise _fail("sketch_missing", "document has no sketches")
    active = _get(sk_or_doc, "active_sketch", _MISSING)
    if active is _MISSING or active is None:
        return sketches[0]
    if isinstance(active, bool):
        raise _fail("sketch_missing", "unsupported active_sketch form", active_sketch=repr(active))
    if isinstance(active, int):
        if -len(sketches) <= active < len(sketches):
            return sketches[active]
        raise _fail("sketch_missing", "active_sketch index out of range", active_sketch=active)
    if isinstance(active, str):
        for sketch in sketches:
            if _get(sketch, "uuid", None) == active:
                return sketch
        raise _fail("sketch_missing", "active_sketch uuid not found", active_sketch=active)
    raise _fail("sketch_missing", "unsupported active_sketch form", active_sketch=repr(active))


def _entities_of(sketch: Any) -> list[Any]:
    """实体表转成 list（本 op 只做"算新表 + 整体写回"，不改原 list 对象）。"""
    value = _get(sketch, "entities", _MISSING)
    if value is _MISSING or value is None:
        raise _fail("sketch_missing", "sketch has no 'entities' table")
    try:
        return list(value)
    except TypeError as exc:
        raise _fail("sketch_missing", "'entities' is not a sequence", error=str(exc)) from exc


def _optional_entries(sketch: Any, key: str) -> list[Any] | None:
    """可选容器（constraints/dimensions）：键缺席或为 None ⇒ ``None``（= NA，不是空集）。"""
    value = _get(sketch, key, _MISSING)
    if value is _MISSING or value is None:
        return None
    try:
        return list(value)
    except TypeError as exc:
        raise _fail("geometry_invalid", "sketch field %r is not a sequence" % key,
                    field=key, error=str(exc)) from exc


def _uuid_of(entity: Any) -> str | None:
    value = _get(entity, "uuid", _MISSING)
    return value if isinstance(value, str) and value else None


def _uuid_of_ref(item: Any) -> str:
    """单个 ref → uuid 字符串；认 ``"uuid"`` / ``["uuid", sub]`` / ``{"uuid": ...}`` 三种形态。"""
    if isinstance(item, str):
        value: Any = item
    elif isinstance(item, Mapping):
        value = _get(item, "uuid", _MISSING)
    elif (isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray))
          and len(item) > 0):
        value = item[0]
    else:
        raise _fail("bad_arg", "each ref must be a uuid string, a [uuid, sub] pair or a mapping",
                    got=repr(item)[:80])
    if not isinstance(value, str) or not value:
        raise _fail("bad_arg", "ref uuid must be a non-empty string", got=repr(value)[:80])
    return value


def _ref_uuids(refs: Any) -> list[str]:
    """``refs`` 归一成 uuid 列表（保持入参顺序，重复去重）。"""
    if refs is None:
        raise _fail("bad_arg", "refs is required and must be a sequence of uuids", param="refs")
    if isinstance(refs, (str, bytes, bytearray)):
        items: Iterable[Any] = [refs]
    elif isinstance(refs, Iterable):
        items = refs
    else:
        raise _fail("bad_arg", "refs must be a sequence of uuids", got=type(refs).__name__)
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        uuid_ = _uuid_of_ref(item)
        if uuid_ in seen:
            continue
        seen.add(uuid_)
        out.append(uuid_)
    return out


def _iter_ref_strings(value: Any, depth: int = 0) -> Iterable[str]:
    """从任意引用结构里捞出 uuid 字符串（嵌套列表/映射，深度封顶 3）。"""
    if depth > 3:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key in ("uuid", "ref", "target"):
            inner = _get(value, key, _MISSING)
            if inner is not _MISSING and inner is not None:
                yield from _iter_ref_strings(inner, depth + 1)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _iter_ref_strings(item, depth + 1)


def _referenced_uuids(entry: Any) -> list[str]:
    """条目（约束/尺寸/实体）在**引用键**上点名了哪些 uuid —— 不含它自己的 uuid。"""
    out: list[str] = []
    for key in _REF_KEYS:
        value = _get(entry, key, _MISSING)
        if value is _MISSING or value is None:
            continue
        out.extend(_iter_ref_strings(value))
    return out


def _cascade_entity(entity: Any, doomed: set[str]) -> bool:
    """标注类实体引用被删实体 ⇒ True（该实体一并清掉）。"""
    kind = _get(entity, "kind", _MISSING)
    if not isinstance(kind, str) or kind.lower() not in _ANNOTATION_KINDS:
        return False
    return bool(set(_referenced_uuids(entity)) & doomed)


def _filter_dangling(entries: list[Any] | None, doomed: set[str]) -> list[Any] | None:
    """整条丢掉引用被删实体的约束/尺寸条目（``None`` = 该容器 NA，原样返回 None）。"""
    if entries is None:
        return None
    return [entry for entry in entries if not (set(_referenced_uuids(entry)) & doomed)]


def erase(sk_or_doc: Any, *, refs: Sequence[str]) -> list[str]:
    """删除 ``refs`` 点到的实体（AutoCAD ERASE 的库级等价物）。

    行为（全部是文档上的**真后果**，不是桩）：

    - 从 ``entities`` 表摘掉 ``refs`` 点到的实体，**原地改**草图/文档，返回被删实体的原 uuid，
      顺序 = ``refs`` 顺序（重复 uuid 只算一次）；
    - **级联清理**：``constraints`` / ``dimensions`` 里任何引用被删实体的条目整条清掉；标注类实体
      （kind ∈ {dimension, dim, leader, mleader, annotation}）引用被删实体时一并清掉 ⇒ 不留悬空引用；
    - **原子**：先算新容器 → 校验清理彻底（仍有悬空引用即 geometry_invalid）→ 才写回；写回失败则
      恢复原值并抛 geometry_invalid，文档保持改前状态。

    抛错（``reason`` 枚举）：

    - ``empty_selection``：``refs`` 为空（空选择集不是"真零"，不静默返回 ``[]``）；
    - ``unknown_reference``：``refs`` 里有 uuid 不在实体表里（**不部分删除**）；
    - ``geometry_invalid``：清理不彻底 / 容器不可写 / 写回失败（整体不生效）；
    - ``sketch_missing``：``sk_or_doc`` 解析不到草图；
    - ``bad_arg``：``refs`` 缺席或元素形态不对（非 CadError 的入参错误就地转为此 reason）。
    """
    sketch = _sketch_of(sk_or_doc)
    wanted = _ref_uuids(refs)
    if not wanted:
        raise _fail("empty_selection", "refs is empty: nothing to erase")

    entities = _entities_of(sketch)
    present: set[str] = set()
    for entity in entities:
        uuid_ = _uuid_of(entity)
        if uuid_ is not None:
            present.add(uuid_)
    unknown = [uuid_ for uuid_ in wanted if uuid_ not in present]
    if unknown:
        raise _fail("unknown_reference", "refs contains uuid(s) absent from the sketch",
                    unknown=unknown[:8], unknown_count=len(unknown))

    doomed = set(wanted)
    new_entities: list[Any] = []
    for entity in entities:
        if _uuid_of(entity) in doomed:
            continue
        if _cascade_entity(entity, doomed):
            continue
        new_entities.append(entity)

    constraints = _optional_entries(sketch, "constraints")
    dimensions = _optional_entries(sketch, "dimensions")
    new_constraints = _filter_dangling(constraints, doomed)
    new_dimensions = _filter_dangling(dimensions, doomed)

    # 先算后校验：清理不彻底就一个字节都不动。
    for field, kept in (("constraints", new_constraints), ("dimensions", new_dimensions)):
        if kept is None:
            continue
        for entry in kept:
            if set(_referenced_uuids(entry)) & doomed:
                raise _fail("geometry_invalid", "dangling reference survives cleanup",
                            field=field, uuid=_uuid_of(entry))
    for entity in new_entities:
        if _cascade_entity(entity, doomed):
            raise _fail("geometry_invalid", "dangling annotation reference survives cleanup",
                        uuid=_uuid_of(entity))

    writes: list[tuple[str, Any]] = [("entities", new_entities)]
    if new_constraints is not None:
        writes.append(("constraints", new_constraints))
    if new_dimensions is not None:
        writes.append(("dimensions", new_dimensions))

    originals = [(key, _get(sketch, key, _MISSING)) for key, _ in writes]
    try:
        for key, value in writes:
            _set(sketch, key, value)
    except Exception as exc:  # 落盘失败 ⇒ 恢复改前的容器，整体不生效
        for key, original in originals:
            if original is _MISSING:
                continue
            try:
                _set(sketch, key, original)
            except Exception:
                pass
        raise _fail("geometry_invalid", "erase could not write the sketch; document left unchanged",
                    error=str(exc)) from exc

    return list(wanted)


OPERATIONS: dict[str, Any] = {"erase": erase}


# --------------------------------------------------------------------------------------
# 自测（真跑真断言；不做 skip，失败即 rc=1 并把 verdict 打成 FAIL）
# --------------------------------------------------------------------------------------

def _sample_sketch() -> dict[str, Any]:
    """L1 草图夹具：L1/L2 两条线 + 一条引用 L1 的标注实体 + 约束/尺寸各两条。"""
    return {
        "entities": [
            {"uuid": "L1", "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}},
            {"uuid": "L2", "kind": "line", "layer": "0", "construction": False,
             "geom": {"x1": 0, "y1": 5_000_000, "x2": 10_000_000, "y2": 5_000_000}},
            {"uuid": "D1", "kind": "dimension", "layer": "DIM", "construction": False,
             "geom": {"x1": 0, "y1": 0, "x2": 10_000_000, "y2": 0}, "refs": ["L1"],
             "value_nm": 10_000_000},
        ],
        "constraints": [
            {"uuid": "C1", "kind": "distance", "refs": [["L1", "start"], ["L2", "start"]],
             "value_nm": 5_000_000},
            {"uuid": "C2", "kind": "horizontal", "refs": ["L2"]},
        ],
        "dimensions": [
            {"uuid": "DM1", "kind": "linear", "refs": ["L1"], "value_nm": 10_000_000},
            {"uuid": "DM2", "kind": "linear", "refs": ["L2"], "value_nm": 10_000_000},
        ],
    }


def _uids(entries: Any) -> list[str]:
    return [entry["uuid"] for entry in entries]


def _dangling_fields(sketch: Any, doomed: set[str]) -> list[str]:
    """全容器扫一遍：还有谁在引用 doomed（用于验证"没留悬空引用"）。"""
    found: list[str] = []
    for field in _CASCADE_FIELDS:
        for entry in _optional_entries(sketch, field) or []:
            if set(_referenced_uuids(entry)) & doomed:
                found.append(field)
    for entity in sketch["entities"]:
        if set(_referenced_uuids(entity)) & doomed:
            found.append("entities")
    return found


def _reason(func: Any, *args: Any, **kwargs: Any) -> str | None:
    try:
        func(*args, **kwargs)
    except CadError as exc:
        return exc.reason
    return None


class _SketchObject:
    """同形对象（属性访问）形态的草图 —— 验证解析口径不止认映射。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.entities = data["entities"]
        self.constraints = data["constraints"]
        self.dimensions = data["dimensions"]


def selftest() -> int:
    import copy
    import json
    import types

    checks: list[tuple[str, bool, str]] = []

    def record(name: str, passed: Any, detail: str = "") -> bool:
        ok = bool(passed)
        checks.append((name, ok, detail))
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + detail) if detail else ""))
        return ok

    base = _sample_sketch()
    doomed = {"L1"}

    # --- 断言 1：删除真发生 + 级联清理 + 其余几何零改动 -------------------------------
    sk = copy.deepcopy(base)
    returned = erase(sk, refs=["L1"])
    record("erase_returns_requested_uuid_in_refs_order", returned == ["L1"], "returned=%r" % (returned,))
    record("entity_removed_from_entities_table", _uids(sk["entities"]) == ["L2"],
           "entities=%r" % (_uids(sk["entities"]),))
    record("dimension_entity_referencing_deleted_is_cascaded",
           "D1" not in _uids(sk["entities"]) and sk["entities"][0] == base["entities"][1],
           "kept_entity_untouched=%s" % (sk["entities"][0] == base["entities"][1]))
    record("constraints_referencing_deleted_are_dropped", _uids(sk["constraints"]) == ["C2"],
           "constraints=%r" % (_uids(sk["constraints"]),))
    record("dimensions_referencing_deleted_are_dropped", _uids(sk["dimensions"]) == ["DM2"],
           "dimensions=%r" % (_uids(sk["dimensions"]),))
    record("no_dangling_reference_survives", _dangling_fields(sk, doomed) == [],
           "dangling=%r" % (_dangling_fields(sk, doomed),))
    record("non_referencing_entries_untouched",
           sk["constraints"][0] == base["constraints"][1]
           and sk["dimensions"][0] == base["dimensions"][1])

    # --- 断言 2：空选择集抛 empty_selection，且文档一个实体都不少 ----------------------
    sk = copy.deepcopy(base)
    reason = _reason(erase, sk, refs=[])
    record("empty_refs_raises_empty_selection", reason == "empty_selection", "reason=%s" % reason)
    record("empty_refs_leaves_sketch_unchanged", sk == base)

    # --- 断言 3：未知 uuid 抛 unknown_reference 且不部分删除 ---------------------------
    sk = copy.deepcopy(base)
    reason = _reason(erase, sk, refs=["L2", "NOPE"])
    record("unknown_uuid_raises_unknown_reference", reason == "unknown_reference", "reason=%s" % reason)
    record("unknown_uuid_deletes_nothing", sk == base)

    # --- 断言 4：解析不到草图 ---------------------------------------------------------
    r_empty_doc = _reason(erase, {"sketches": []}, refs=["L1"])
    r_no_sketch = _reason(erase, {"unit": "mm"}, refs=["L1"])
    record("no_sketch_raises_sketch_missing",
           r_empty_doc == "sketch_missing" and r_no_sketch == "sketch_missing",
           "empty_doc=%s no_sketch=%s" % (r_empty_doc, r_no_sketch))

    # --- 断言 5：入参把关 -------------------------------------------------------------
    r_none = _reason(erase, copy.deepcopy(base), refs=None)
    r_bad = _reason(erase, copy.deepcopy(base), refs=[123])
    record("bad_arg_for_missing_or_malformed_refs",
           r_none == "bad_arg" and r_bad == "bad_arg", "none=%s bad=%s" % (r_none, r_bad))

    # --- 断言 6：文档形态 + L1 序列化往返后删除仍在（真数据后果） ----------------------
    doc = {"unit": "mm", "active_sketch": 0, "sketches": [copy.deepcopy(base)]}
    returned = erase(doc, refs=["L1"])
    doc2 = json.loads(json.dumps(doc, sort_keys=True))
    record("document_form_resolves_active_sketch",
           returned == ["L1"] and _uids(doc2["sketches"][0]["entities"]) == ["L2"],
           "returned=%r roundtrip_entities=%r" % (returned, _uids(doc2["sketches"][0]["entities"])))
    record("deletion_survives_l1_json_roundtrip",
           _uids(doc2["sketches"][0]["dimensions"]) == ["DM2"]
           and doc2["sketches"][0]["entities"][0]["geom"] == base["entities"][1]["geom"])
    doc3 = {"active_sketch": "SK-B", "sketches": [copy.deepcopy(base), dict(copy.deepcopy(base),
            **{"uuid": "SK-B"})]}
    record("active_sketch_uuid_form_resolves",
           erase(doc3, refs=["L1"]) == ["L1"]
           and _uids(doc3["sketches"][1]["entities"]) == ["L2"]
           and _uids(doc3["sketches"][0]["entities"]) == ["L1", "L2", "D1"])

    # --- 断言 7：重复 uuid 去重 -------------------------------------------------------
    sk = copy.deepcopy(base)
    record("duplicate_refs_are_deduped",
           erase(sk, refs=["L1", "L1", "L2"]) == ["L1", "L2"]
           and _uids(sk["entities"]) == ["D1"]
           and _uids(sk["dimensions"]) == [],
           "entities=%r" % (_uids(sk["entities"]),))

    # --- 断言 8：同形对象（属性访问） -------------------------------------------------
    obj = _SketchObject(copy.deepcopy(base))
    record("object_form_sketch_is_supported",
           erase(obj, refs=["L1"]) == ["L1"] and _uids(obj.entities) == ["L2"]
           and _uids(obj.dimensions) == ["DM2"])

    # --- 断言 9：容器不可写 ⇒ geometry_invalid 且底层数据零改动（原子性真跑） ----------
    holder = copy.deepcopy(base)
    reason = _reason(erase, types.MappingProxyType(holder), refs=["L1"])
    record("readonly_sketch_raises_geometry_invalid_and_rolls_back",
           reason == "geometry_invalid" and holder == base, "reason=%s" % reason)

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print("SELFTEST family=edit_ops op=erase checks=%d/%d verdict=%s"
          % (passed, total, "PASS" if passed == total else "FAIL"))
    if passed != total:
        for name, ok, detail in checks:
            if not ok:
                print("  FAILED_CHECK %s %s" % (name, detail))
        print("FEATURE_OK family=edit_ops op=erase verdict=FAIL")
        return 1
    print("FEATURE_OK family=edit_ops op=erase verdict=PASS")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--selftest", "-s"):
        print("usage: python3 edit_ops__w13.py [--selftest]")
        return 2
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())
