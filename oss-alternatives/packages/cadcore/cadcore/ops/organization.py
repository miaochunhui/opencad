#!/usr/bin/env python3
"""cadcore.ops.organization —— 组织与复用族模块：图层体系 / 块定义与插入 / 属性 / 组 / 外部参照占位。

一句话用途
----------
把"几何之外的组织层"做成 L1 文档/草图上的**可复用表**：图层（颜色/线型/线宽/开关/锁定/过滤器）、
块定义与插入（含基点的毫米级整数纳米重定位）、块属性（ATTDEF / ATTRIB）、编组（GROUP）、
外部参照占位（XREF）。这是 `cadcore.ops.<module>` 家族模块之一。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "organization"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回**受影响的 uuid 列表**（每个操作各自
  在 docstring 里写清"返回的是谁的 uuid、顺序如何"）。
- 长度一律**整数纳米（nm）**；错误一律抛 ``cadcore.errors.CadError(reason=...)``（机器可读 reason）。

落盘形态（组织表放哪，为什么）
------------------------------
组织表**不改 L1 的实体/草图字段**（实体的 `layer` 字段本来就在契约里，图层表则是新东西）：

- 目标是**文档**（`cadcore.docmodel.Document` 或同形映射）⇒ 表放 ``doc.extra["organization"]``；
  `extra` 是 A5 文档层明写的"未知顶层字段可往返"容器，`document_to_l1` → `document_from_l1`
  一圈之后原样还在（自检里有一条断言专门验它）。
- 目标是**草图**（`{entities, constraints, dimensions}` 或对象）⇒ 表放 ``sk["organization"]``。
- 表结构（**只加键不改键**）：``{"schema": 1, "layers": [], "blocks": [], "inserts": [],
  "groups": [], "xrefs": []}``。
- **成员关系不写进实体**：块实例的成员、组的成员都记在表里的 uuid 列表（`entity_uuids` / `members`），
  这样实体的字段集仍是 L1 冻结的那五个，`docmodel.check_invariants()` 不会被本模块污染（自检断言）。

口径（与全仓一致，别自己发明）
------------------------------
- 长度字段一律整数纳米；`*_nm` 键**只能是 int**（`check_invariants` 会把浮点当违规）。
- ``0 = 真为零``：例如 ``lineweight_nm=0`` 表示"显式为 0 纳米（不打印/最细）"。
- ``-1 = 未采``：``lineweight_nm=-1`` 表示**没有显式设置**（继承缺省，AutoCAD 的 ByLayer 语义）。
- ``NA = 不适用``：不适用时**该字段缺席**，不写 `None`、不写 0 冒充（`*_nm` 键出现 `None` 亦属违规）。
- ``color=None`` 表示 ByLayer（随层）；`color` 是 int（ACI 1..255）或 ``"#RRGGBB"`` 字符串。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
1. **本层不做任何文件 I/O**：外部参照只建**占位**记录（`loaded=False`）。要真正读外部文件、
   绑定/重载（BIND / XREF RELOAD）属于 io 层；`xref_set(..., loaded=True)` 显式抛
   ``CadError(reason="dependency_missing")``，把"没做"变成能看见的降级信号，而不是假装加载成功。
2. **`block_define` 不删源实体**（与 AutoCAD BLOCK 的一步替换不同）：定义只做**深拷贝 + 按基点重定位**，
   源几何一个纳米都不动；要落"实例"请显式 `block_insert`。这样"定义块"永远不会意外改图。
3. **`block_insert` 直接落几何**（插入即展开成普通 L1 实体，成员 uuid 记在实例记录里）。
   于是 `block_explode` 的语义是"解除实例标记 + 丢掉属性"，几何一个纳米都不动 —— 不是近似实现。
4. **非等比缩放（`sx != sy`）遇到 `circle` / `arc` 成员 ⇒ `CadError(reason="unsupported_geometry")`**：
   L1 没有椭圆，`sqrt(|det|)` 会把椭圆偷偷当圆存，那是静默错图，宁可显式报不支持。
5. **锁定层保护**：`layer_assign` 拒绝把实体改到锁定层，也拒绝把**已在锁定层**的实体挪走
   （`reason="layer_locked"`）；`layer_delete` 拒绝删锁定层与层 `"0"`。
6. **`layer_filter` / `xref_filter` 是纯查询**：不建表、不写入；空表返回 `[]`（"表里还没有记录"），
   不假装图上没有层。
7. 复用而非重写：入参把关复用 `cadcore.edit.modify` 的 `_as_nm` / `_as_point` / `_as_angle` / `_as_factor`，
   仿射核复用它的 `transform_entity`（含 `arc` 镜像换向等已冻结语义），结构访问走
   `cadcore.sketch.constraints` 的 `_get` / `_set` / `sketch_entities` / `new_uuid`。本文件不抄第二份。
8. 非 docstring 的字符串字面量一律 ASCII：面向用户的文案由语言层按 key 取词（`prompt_key` 存 key）。
"""

from __future__ import annotations

if __package__ in (None, ""):  # 直跑 python3 cadcore/ops/organization.py 时的引导（见文件末 __main__）
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    __package__ = "cadcore.ops"

import copy
import re
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from ..edit.modify import _as_angle, _as_factor, _as_nm, _as_point
from ..edit.modify import _mat_compose, _mat_rotate, _mat_translate, transform_entity
from ..errors import CadError
from ..sketch.constraints import _get, _set, new_uuid, sketch_entities

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "attdef_create",
    "attdef_delete",
    "attrib_batch",
    "attrib_set",
    "block_define",
    "block_delete",
    "block_explode",
    "block_insert",
    "group_add",
    "group_create",
    "group_delete",
    "group_remove",
    "layer_assign",
    "layer_create",
    "layer_delete",
    "layer_filter",
    "layer_set",
    "xref_attach",
    "xref_detach",
    "xref_filter",
    "xref_set",
]

#: 族 id（冻结）。
FAMILY = "organization"

#: 组织表在文档 `extra` / 草图里的键名。
_ORG_KEY = "organization"
#: 组织表自身的 schema 版本（只加不改；将来迁移从这里分叉）。
_ORG_SCHEMA = 1
#: AutoCAD 缺省层名：实体 `layer` 字段的缺省值就是它。
_DEFAULT_LAYER = "0"
#: "未传参"哨兵（`None` 是合法取值，不能拿它当缺省）。
_UNSET: Any = object()
#: 本层认识的线型名（枚举是冻结面；未知线型报 `unknown_linetype`，不静默按 continuous 处理）。
_LINETYPES = frozenset({"continuous", "dashed", "dotted", "dashdot", "center", "hidden",
                        "phantom", "divide", "bylayer"})
#: 线宽上界 5 mm（整数纳米）。超过即为"人填错了"，报 `out_of_range`。
_MAX_LINEWEIGHT_NM = 5_000_000
#: ACI 颜色索引上界。
_MAX_ACI = 255
#: 名字里禁用的字符（AutoCAD 图层/块名同名集；`.` 单独另判）。
_BAD_NAME_CHARS = ("*", "?", ",", "=", "<", ">", "/", "\\", '"', ":", ";", "|", "`", "\n", "\t")
#: i18n key 形状（与 `docmodel._KEY_LIKE_RE` 同一口径：ASCII 点分，首段以字母开头）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
#: L1 实体种类（块成员只接受这些；未知 kind 报 `unsupported_geometry`）。
_ENTITY_KINDS = ("line", "arc", "circle", "polyline")


# ======================================================================================
# 错误与入参把关（复用 modify 的校验器；错误统一收敛到 cadcore.errors.CadError）
# ======================================================================================


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """构造失败信号：`reason` 是下游唯一的归因入口（文案一律 ASCII）。"""
    return CadError(detail, reason=reason, **details)


def _rw(exc: CadError, *, what: str, value: Any, fallback: str) -> CadError:
    """把 modify 校验器的错误**换成基类 CadError**（保留 reason/文案，不丢归因）。"""
    return _fail(str(exc.reason or fallback), str(exc.message), what=what, value=repr(value))


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（坏入参当场报 `invalid_length`）。"""
    try:
        return _as_nm(value, what=what, positive=positive)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_length") from exc


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    try:
        return _as_point(value, what=what)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_argument") from exc


def _angle(value: Any, *, what: str = "angle_deg") -> float:
    """角度入参 → 有限浮点度（角度不是长度，允许实数）。"""
    try:
        return _as_angle(value, what=what)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_argument") from exc


def _scale_pair(value: Any) -> tuple[float, float]:
    """缩放入参 → `(sx, sy)` 两个正有限实数；接受单个数字（等比）或二元序列（可非等比）。"""
    if isinstance(value, bool):
        raise _fail("invalid_argument", "scale must be a positive number or a (sx, sy) pair",
                    value=repr(value))
    if isinstance(value, (int, float)):
        factor = _factor(value, what="scale")
        return (factor, factor)
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail("invalid_argument", "scale must be a positive number or a (sx, sy) pair",
                    value=repr(value))
    return (_factor(value[0], what="scale.x"), _factor(value[1], what="scale.y"))


def _factor(value: Any, *, what: str) -> float:
    """缩放因子 → 正有限实数。"""
    try:
        return _as_factor(value, what=what)
    except CadError as exc:
        raise _rw(exc, what=what, value=value, fallback="invalid_argument") from exc


def _text(value: Any, *, what: str) -> str:
    """文本入参 → 非空字符串（空白串不算数）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", f"{what} must be a non-empty string", what=what, value=repr(value))
    return value


def _name(value: Any, *, what: str) -> str:
    """图层/块/组/参照名 → 非空且不含保留字符的字符串。"""
    text = _text(value, what=what)
    if any(char in text for char in _BAD_NAME_CHARS) or text in (".", ".."):
        raise _fail("invalid_name", f"{what} contains a reserved character", what=what, value=text)
    return text


def _key(value: Any, *, what: str) -> str:
    """i18n key 入参 → 形状校验（文案不进源码，只进语言包）。"""
    text = _text(value, what=what)
    if not _KEY_RE.match(text):
        raise _fail("invalid_argument", f"{what} must be an i18n key (dotted ASCII)", what=what, value=text)
    return text


def _color(value: Any) -> Any:
    """颜色入参 → `None`（ByLayer）/ int（ACI 1..255）/ `"#RRGGBB"`。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _fail("invalid_argument", "color must be None, an ACI int or '#RRGGBB'", value=repr(value))
    if isinstance(value, int):
        if not 1 <= value <= _MAX_ACI:
            raise _fail("out_of_range", "ACI color index must be within 1..255", value=int(value))
        return int(value)
    if isinstance(value, str) and re.match(r"^#[0-9A-Fa-f]{6}$", value):
        return value.upper()
    raise _fail("invalid_argument", "color must be None, an ACI int or '#RRGGBB'", value=repr(value))


def _linetype(value: Any) -> str:
    """线型入参 → 本层枚举内的名字（未知线型报 `unknown_linetype`，不静默降级）。"""
    text = _text(value, what="linetype").strip().lower()
    if text not in _LINETYPES:
        raise _fail("unknown_linetype", "linetype is not in this layer's frozen enum", linetype=text,
                    known=sorted(_LINETYPES))
    return text


def _lineweight(value: Any) -> int:
    """线宽入参 → 整数纳米；`-1` = 未显式设置（ByLayer 缺省），`0` = 真为零，`>=0` 且 ≤5mm。"""
    if value is None:
        raise _fail("invalid_argument", "lineweight_nm must be an int (-1 = unset)", value=repr(value))
    weight = _nm(value, what="lineweight_nm")
    if weight < -1:
        raise _fail("invalid_argument", "lineweight_nm must be -1 (unset) or >= 0", value=int(weight))
    if weight > _MAX_LINEWEIGHT_NM:
        raise _fail("out_of_range", "lineweight_nm exceeds 5 mm", value=int(weight),
                    max_nm=int(_MAX_LINEWEIGHT_NM))
    return int(weight)


def _flag(value: Any, *, what: str) -> bool:
    """开关入参 → 严格 bool（不收 0/1 冒充，避免"看起来设了"）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool", what=what, value=repr(value))
    return bool(value)


# ======================================================================================
# 组织表定位（文档 → extra["organization"]；草图 → sk["organization"]）
# ======================================================================================


def _empty_store() -> dict[str, Any]:
    """空组织表（新表只长这里，别在调用点手搓键）。"""
    return {"schema": int(_ORG_SCHEMA), "layers": [], "blocks": [], "inserts": [],
            "groups": [], "xrefs": []}


def _child(holder: Any, create: bool) -> dict[str, Any] | None:
    """取（必要时创建）`holder["organization"]` 表。"""
    value = holder.get(_ORG_KEY) if isinstance(holder, _MutableMappingABC) else None
    if isinstance(value, _MutableMappingABC):
        return value
    if not create:
        return None
    value = _empty_store()
    holder[_ORG_KEY] = value
    return value


def _view(target: Any, *, create: bool = True) -> tuple[dict[str, Any] | None, list[list[Any]]]:
    """`sk_or_doc` → `(组织表, 全部实体列表)`。

    - 文档形态（有 `sketches` 列表）：表在 `extra`（A5 的未知顶层字段容器）；实体列表 = 每个草图一份；
    - 草图形态：表在 `sk["organization"]`；实体列表 = 该草图一份。
    """
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        extra = _get(target, "extra", None)
        if isinstance(extra, _MutableMappingABC):
            holder = extra
        elif isinstance(target, _MutableMappingABC):
            holder = target
        else:
            raise _fail("bad_target", "document target has no mutable extra container",
                        value_type=type(target).__name__)
        store = _child(holder, create)
        if store is None:
            return None, []
        return store, [sketch_entities(sketch) for sketch in sketches]
    if isinstance(target, _MutableMappingABC):
        store = _child(target, create)
        return store, ([] if store is None else [sketch_entities(target)])
    existing = _get(target, _ORG_KEY, None)
    if isinstance(existing, _MutableMappingABC):
        return existing, [sketch_entities(target)]
    if not create:
        return None, []
    store = _empty_store()
    _set(target, _ORG_KEY, store)
    return store, [sketch_entities(target)]


def _table(store: dict[str, Any], key: str) -> list[Any]:
    """取表（缺则建空表；类型坏了报 `corrupt_store`，不静默重置）。"""
    value = store.get(key)
    if value is None:
        value = []
        store[key] = value
    if not isinstance(value, list):
        raise _fail("corrupt_store", "organization table is not a list", table=key,
                    value_type=type(value).__name__)
    return value


def _entity_list(target: Any, *, sketch: Any = None) -> list[Any]:
    """选一个**接收新几何**的实体列表：文档要多草图时用 `sketch=<uuid>` 点名（禁下标引用）。"""
    sketches = _get(target, "sketches", None)
    if not isinstance(sketches, list):
        return sketch_entities(target)
    if sketch is None:
        if len(sketches) != 1:
            raise _fail("ambiguous_target", "document has multiple sketches: pass sketch=<uuid>",
                        sketch_count=len(sketches))
        return sketch_entities(sketches[0])
    if not isinstance(sketch, str) or not sketch:
        raise _fail("invalid_argument", "sketch must be a sketch uuid string", value=repr(sketch))
    for item in sketches:
        if _get(item, "uuid", None) == sketch:
            return sketch_entities(item)
    raise _fail("unknown_reference", "no sketch with this uuid", reference=sketch)


def _uuid_of(entity: Any) -> str:
    """实体 uuid（读不出来即报 `geometry_invalid`，禁静默跳过）。"""
    value = _get(entity, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("geometry_invalid", "entity has no usable uuid")
    return value


def _entity_kind(entity: Any) -> str:
    """实体 kind（不在 L1 冻结集内报 `unsupported_geometry`）。"""
    kind = _get(entity, "kind", None)
    if kind not in _ENTITY_KINDS:
        raise _fail("unsupported_geometry", "entity kind is out of the L1 frozen set",
                    kind=str(kind), uuid=_uuid_of(entity))
    return str(kind)


def _geom_of(entity: Any) -> Any:
    """实体 geom（映射或对象；块定义与实例都按它深拷贝）。"""
    geom = _get(entity, "geom", None)
    if not isinstance(geom, _MappingABC) or not geom:
        raise _fail("geometry_invalid", "entity geom must be a non-empty mapping", uuid=_uuid_of(entity))
    return geom


def _resolve(lists: list[list[Any]], refs: Any) -> list[Any]:
    """引用列表 → 实体列表（保序去重；空选择报 `empty_selection`，未知 uuid 报 `unknown_reference`）。"""
    if isinstance(refs, (str, bytes)):
        items: list[Any] = [refs]
    elif isinstance(refs, (list, tuple)):
        items = list(refs)
    else:
        raise _fail("invalid_argument", "refs must be a sequence of entity uuids",
                    value_type=type(refs).__name__)
    if not items:
        raise _fail("empty_selection", "refs is empty: refusing to no-op silently")
    found: list[Any] = []
    seen: set[str] = set()
    for ref in items:
        if not isinstance(ref, str) or not ref:
            raise _fail("invalid_argument", "entity reference must be a non-empty uuid string",
                        value=repr(ref))
        hits = [entity for group in lists for entity in group if _get(entity, "uuid", None) == ref]
        if not hits:
            raise _fail("unknown_reference", "no entity with this uuid in the target", reference=ref)
        if ref in seen:
            continue
        seen.add(ref)
        found.append(hits[0])
    return found


# ======================================================================================
# 图层
# ======================================================================================


def _default_layer_row() -> dict[str, Any]:
    """缺省层 `"0"` 的行（与 L1 实体 `layer` 字段的缺省值同名）。"""
    return {"uuid": new_uuid(), "name": _DEFAULT_LAYER, "color": None, "linetype": "continuous",
            "lineweight_nm": -1, "visible": True, "locked": False, "printable": True,
            "frozen": False}


def _layers(store: dict[str, Any], *, bootstrap: bool = True) -> list[Any]:
    """图层表；写操作首次触达时空表自举缺省层 `"0"`（只自举一次，查询路径不自举）。"""
    rows = _table(store, "layers")
    if bootstrap and not rows:
        rows.append(_default_layer_row())
    return rows


def _find_layer(store: dict[str, Any], ref: Any, *, bootstrap: bool = True) -> dict[str, Any]:
    """按 uuid 或**大小写不敏感**的层名找图层行（找不到报 `unknown_layer`，同名报 `ambiguous_layer`）。"""
    rows = _layers(store, bootstrap=bootstrap)
    text = _text(ref, what="layer")
    for row in rows:
        if row.get("uuid") == text:
            return row
    key = text.casefold()
    hits = [row for row in rows if str(row.get("name", "")).casefold() == key]
    if not hits:
        raise _fail("unknown_layer", "no layer with this name or uuid", layer=text,
                    known=sorted(str(row.get("name", "")) for row in rows))
    if len(hits) > 1:
        raise _fail("ambiguous_layer", "layer name is not unique", layer=text, matches=len(hits))
    return hits[0]


def _layer_name(store: dict[str, Any], entity: Any) -> str:
    """实体所属层名（实体缺 `layer` 字段时按 L1 缺省 `"0"`）。"""
    value = _get(entity, "layer", _DEFAULT_LAYER)
    return value if isinstance(value, str) and value else _DEFAULT_LAYER


def _require_unlocked(store: dict[str, Any], entity: Any) -> None:
    """锁定层上的实体不许改（`layer_locked`）；层记录不存在时按未锁定处理（组织表只是账，不是门）。"""
    name = _layer_name(store, entity)
    for row in _layers(store, bootstrap=False):
        if str(row.get("name", "")).casefold() == name.casefold() and row.get("locked"):
            raise _fail("layer_locked", "entity sits on a locked layer", uuid=_uuid_of(entity), layer=name)


def layer_create(target: Any, *, name: str, color: Any = None, linetype: str = "continuous",
                 lineweight_nm: int = -1, visible: bool = True, locked: bool = False,
                 printable: bool = True, frozen: bool = False) -> list[str]:
    """新建图层 → `[layer_uuid]`。

    参数（长度一律整数纳米）：`name` 非空、不含保留字符、**大小写不敏感唯一**（与 AutoCAD 一致）；
    `color` = `None`（ByLayer，缺省）/ ACI int(1..255) / `"#RRGGBB"`；`linetype` ∈ 本层枚举；
    `lineweight_nm` = `-1` 未显式设置（缺省，ByLayer）/ `0` 真为零 / `>0..5mm`；
    `visible` 开关、`locked` 锁定、`printable` 是否打印（三者必须是严格 bool）。

    边界：首次触达空表时自举缺省层 `"0"`（本次创建的层是**第二个**）；重名抛
    `CadError(reason="duplicate_name")`；坏参数抛 `invalid_argument` / `invalid_name` /
    `unknown_linetype` / `out_of_range` / `invalid_length`。
    """
    # 纯校验**前置**：`_view(create=True)` 会建组织表并自举缺省层 "0"，先校验才能兑现
    # "非法入参失败后文档一字未动"（与 `layer_assign` docstring 的"先全量校验、后统一写回"同一条口径）。
    text = _name(name, what="layer.name")
    color = _color(color)
    linetype = _linetype(linetype)
    lineweight_nm = _lineweight(lineweight_nm)
    visible = _flag(visible, what="visible")
    locked = _flag(locked, what="locked")
    printable = _flag(printable, what="printable")
    frozen = _flag(frozen, what="frozen")
    store, _ = _view(target)
    rows = _layers(store)
    key = text.casefold()
    for row in rows:
        if str(row.get("name", "")).casefold() == key:
            raise _fail("duplicate_name", "layer name already exists", layer=text)
    row = {"uuid": new_uuid(), "name": text, "color": color, "linetype": linetype,
           "lineweight_nm": lineweight_nm, "visible": visible, "locked": locked,
           "printable": printable, "frozen": frozen}
    rows.append(row)
    return [str(row["uuid"])]


def layer_set(target: Any, *, layer: Any, name: Any = _UNSET, color: Any = _UNSET,
              linetype: Any = _UNSET, lineweight_nm: Any = _UNSET, visible: Any = _UNSET,
              locked: Any = _UNSET, printable: Any = _UNSET, frozen: Any = _UNSET) -> list[str]:
    """改图层属性（未传的关键字**不改**）→ `[layer_uuid]`。

    参数：`layer` = 层名（大小写不敏感）或 layer_uuid；其余同名参数语义与 `layer_create` 一致
    （`color=None` 是"设成 ByLayer"这个合法取值，不是"别改" —— "别改"用**不传**表示）。

    边界：层 `"0"` **不许改名**（`reason="reserved_layer"`），但颜色/线型/线宽/开关/锁定随便改；
    改名要重新过唯一性与字符校验；坏参数抛 `unknown_layer` / `duplicate_name` / `invalid_*`。
    """
    store, _ = _view(target, create=False)
    if store is None:
        raise _fail("unknown_layer", "organization table is empty", layer=str(layer))
    row = _find_layer(store, layer)
    updates = {}
    if name is not _UNSET:
        text = _name(name, what="layer.name")
        if str(row.get("name", "")) == _DEFAULT_LAYER and text != _DEFAULT_LAYER:
            raise _fail("reserved_layer", "layer '0' cannot be renamed", layer=_DEFAULT_LAYER)
        for other in _layers(store):
            if other is not row and str(other.get("name", "")).casefold() == text.casefold():
                raise _fail("duplicate_name", "layer name already exists", layer=text)
        updates["name"] = text
    if color is not _UNSET:
        updates["color"] = _color(color)
    if linetype is not _UNSET:
        updates["linetype"] = _linetype(linetype)
    if lineweight_nm is not _UNSET:
        updates["lineweight_nm"] = _lineweight(lineweight_nm)
    for key, value, what in (("visible", visible, "visible"), ("locked", locked, "locked"),
                             ("printable", printable, "printable")):
        if value is not _UNSET:
            updates[key] = _flag(value, what=what)
    if frozen is not _UNSET:
        from .features.organization__w10 import layer_state_set

        layer_state_set(target, layer=row["uuid"], frozen=_flag(frozen, what="frozen"))
    row.update(updates)
    return [str(row["uuid"])]


def layer_delete(target: Any, *, layer: Any, move_to: Any = None) -> list[str]:
    """删除图层 → `[deleted_layer_uuid]`。

    语义：层上有实体时必须给 `move_to`（层名或 uuid），把这些实体**改到目标层**后再删；
    目标层自身不能是被删的层，且不能是锁定层（`reason="layer_locked"`）。

    边界：层 `"0"` 不可删（`reserved_layer`）；锁定层不可删（`layer_locked`，先 `layer_set(locked=False)`）；
    层上有实体却没给 `move_to` ⇒ `CadError(reason="layer_not_empty")`（**不**静默把实体扔进层 `"0"`）；
    本操作**只删图层记录**，一个实体都不会被删。
    """
    store, lists = _view(target)
    row = _find_layer(store, layer)
    name = str(row.get("name", ""))
    if name == _DEFAULT_LAYER:
        raise _fail("reserved_layer", "layer '0' cannot be deleted", layer=_DEFAULT_LAYER)
    if row.get("locked"):
        raise _fail("layer_locked", "unlock the layer before deleting it", layer=name)
    members = [entity for group in lists for entity in group if _layer_name(store, entity).casefold() == name.casefold()]
    if members:
        if move_to is None:
            raise _fail("layer_not_empty", "layer still has entities: pass move_to", layer=name,
                        entity_count=len(members))
        destination = _find_layer(store, move_to)
        if destination is row:
            raise _fail("invalid_argument", "move_to must differ from the deleted layer", layer=name)
        if destination.get("locked"):
            raise _fail("layer_locked", "destination layer is locked", layer=str(destination.get("name", "")))
        for entity in members:
            _set(entity, "layer", str(destination.get("name", _DEFAULT_LAYER)))
    _layers(store).remove(row)
    return [str(row["uuid"])]


def layer_filter(target: Any, *, name: Any = None, name_prefix: Any = None, color: Any = _UNSET,
                 linetype: Any = None, visible: Any = None, locked: Any = None,
                 printable: Any = None, frozen: Any = None) -> list[str]:
    """按条件过滤图层 → **按层名（大小写不敏感）排序**的 `[layer_uuid, ...]`。

    参数：`name` 精确名（大小写不敏感）；`name_prefix` 前缀（大小写不敏感）；`color` = `None`
    表示**筛 ByLayer 层**（想表达"不筛颜色"就**别传**这个名字 —— 用哨兵区分，不用 None 冒充）；
    `linetype` / `visible` / `locked` / `printable` 均为精确匹配；一个都不传 = 列出全部层。

    边界（口径 `-1`/`NA`）：本操作是**纯查询**，不建表、不自举层 `"0"`、不写任何字段；
    组织表里还没有层记录时返回 `[]`（"未采：表里还没有记录"），**不代表**图上没有层。
    """
    store, _ = _view(target, create=False)
    if store is None:
        return []
    rows = list(_layers(store, bootstrap=False))
    if name is not None:
        key = _text(name, what="name").casefold()
        rows = [row for row in rows if str(row.get("name", "")).casefold() == key]
    if name_prefix is not None:
        prefix = _text(name_prefix, what="name_prefix").casefold()
        rows = [row for row in rows if str(row.get("name", "")).casefold().startswith(prefix)]
    if color is not _UNSET:
        wanted = _color(color)
        rows = [row for row in rows if row.get("color") == wanted]
    if linetype is not None:
        wanted_type = _linetype(linetype)
        rows = [row for row in rows if row.get("linetype") == wanted_type]
    for key, value in (("visible", visible), ("locked", locked), ("printable", printable),
                       ("frozen", frozen)):
        if value is not None:
            wanted_flag = _flag(value, what=key)
            rows = [row for row in rows if bool(row.get(key)) is wanted_flag]
    rows.sort(key=lambda row: str(row.get("name", "")).casefold())
    return [str(row.get("uuid")) for row in rows]


def layer_assign(target: Any, *, refs: Any, layer: Any, create_layer: bool = False) -> list[str]:
    """把实体改到指定图层 → **入参顺序、去重后**的 `[entity_uuid, ...]`。

    参数：`refs` = 实体 uuid 的序列（空选择报 `empty_selection`）；`layer` = 层名或 layer_uuid；
    `create_layer=True` 时目标层不存在就按缺省属性建（层名单独校验），缺省 `False` ⇒ `unknown_layer`。

    边界：实体**当前所在层锁定** ⇒ `CadError(reason="layer_locked")`（不许从锁定层挪走）；
    目标层锁定 ⇒ 同样 `layer_locked`；**先全量校验、后统一写回**（半路失败时一个实体都不动）。
    """
    store, lists = _view(target)
    entities = _resolve(lists, refs)
    if create_layer is True and not isinstance(layer, str):
        raise _fail("invalid_argument", "create_layer=True needs a layer name", value=repr(layer))
    if create_layer:
        text = _name(layer, what="layer")
        existing = [row for row in _layers(store) if str(row.get("name", "")).casefold() == text.casefold()]
        row = existing[0] if existing else None
        if row is None:
            layer_create(target, name=text)
            row = _find_layer(store, text)
    else:
        row = _find_layer(store, layer)
    if row.get("locked"):
        raise _fail("layer_locked", "destination layer is locked", layer=str(row.get("name", "")))
    for entity in entities:
        _require_unlocked(store, entity)
    for entity in entities:
        _set(entity, "layer", str(row.get("name", _DEFAULT_LAYER)))
    return [_uuid_of(entity) for entity in entities]


# ======================================================================================
# 块定义与插入
# ======================================================================================


def _find_block(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 或大小写不敏感的块名找块定义（`unknown_block`）。"""
    text = _text(ref, what="block")
    for row in _table(store, "blocks"):
        if row.get("uuid") == text:
            return row
    hits = [row for row in _table(store, "blocks") if str(row.get("name", "")).casefold() == text.casefold()]
    if not hits:
        raise _fail("unknown_block", "no block definition with this name or uuid", block=text)
    return hits[0]


def _find_insert(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 找块实例（`unknown_reference`；实例没有名字，只认 uuid）。"""
    text = _text(ref, what="insert")
    for row in _table(store, "inserts"):
        if row.get("uuid") == text:
            return row
    raise _fail("unknown_reference", "no block insert with this uuid", reference=text)


def _copy_geom(entity: Any) -> dict[str, Any]:
    """深拷贝实体 geom 并**逐字段校验为整数纳米**（块定义存的是几何真源，必须干净）。"""
    kind = _entity_kind(entity)
    geom = _geom_of(entity)
    out: dict[str, Any] = {}
    if kind == "line":
        for field in ("x1", "y1", "x2", "y2"):
            out[field] = _nm(_get(geom, field, None), what=f"geom.{field}")
        return out
    if kind in ("circle", "arc"):
        out["cx"] = _nm(_get(geom, "cx", None), what="geom.cx")
        out["cy"] = _nm(_get(geom, "cy", None), what="geom.cy")
        out["r"] = _nm(_get(geom, "r", None), what="geom.r", positive=True)
        if kind == "arc":
            out["start_deg"] = _angle(_get(geom, "start_deg", None), what="geom.start_deg")
            out["end_deg"] = _angle(_get(geom, "end_deg", None), what="geom.end_deg")
        return out
    points = _get(geom, "points", None)
    if isinstance(points, (str, bytes)) or not isinstance(points, (list, tuple)) or len(points) < 2:
        raise _fail("geometry_invalid", "polyline needs at least 2 points", uuid=_uuid_of(entity))
    out["points"] = [[_nm(point[0], what="geom.points.x"), _nm(point[1], what="geom.points.y")]
                     for point in points]
    return out


def _shift_geom(kind: str, geom: dict[str, Any], dx: int, dy: int) -> dict[str, Any]:
    """几何整体平移 `(dx, dy)` 纳米（块定义的基点重定位用；角度与半径不动）。"""
    out = copy.deepcopy(geom)
    if kind == "line":
        for key, delta in (("x1", dx), ("x2", dx), ("y1", dy), ("y2", dy)):
            out[key] = int(out[key]) + int(delta)
    elif kind in ("circle", "arc"):
        out["cx"] = int(out["cx"]) + int(dx)
        out["cy"] = int(out["cy"]) + int(dy)
    elif kind == "polyline":
        out["points"] = [[int(point[0]) + int(dx), int(point[1]) + int(dy)] for point in out["points"]]
    else:
        raise _fail("unsupported_geometry", "cannot shift this entity kind", kind=str(kind))
    return out


def block_define(target: Any, *, name: str, refs: Any = None, base_nm: Any = (0, 0),
                 description: str = "", sketch: Any = None) -> list[str]:
    """定义块 → `[block_uuid]`。

    语义：`refs` 选中的实体被**深拷贝**进块定义，几何按 `base_nm`（整数纳米基点）**整体平移
    `-base_nm`** ⇒ 定义里的坐标是"以基点为原点"的局部坐标（插入时再摆回去）。
    `refs=None` = 空块定义（合法：先建块再加属性 / 先建块再插空实例）。

    参数：`name` 非空、大小写不敏感唯一；`base_nm` 是 `(x_nm, y_nm)` 整数对；`description` 是
    自由文本（**面向用户的文案走 `prompt_key`，不塞源码**，这里只做备注）；`sketch` 仅在目标是
    多草图文档且 `refs` 非空时用来点名源草图（禁下标引用）。

    边界（有意与 AutoCAD BLOCK 不同）：**源实体一个字节都不动**，不会把源几何"转成实例"；
    重名抛 `duplicate_name`；`refs` 里有未知 uuid 抛 `unknown_reference`；非 L1 种类抛
    `unsupported_geometry`；坏基点抛 `invalid_length`。先全量校验再写表（半路失败不留半成品）。
    """
    store, lists = _view(target)
    text = _name(name, what="block.name")
    for row in _table(store, "blocks"):
        if str(row.get("name", "")).casefold() == text.casefold():
            raise _fail("duplicate_name", "block name already exists", block=text)
    base = _point(base_nm, what="base_nm")
    if not isinstance(description, str):
        raise _fail("invalid_argument", "description must be a string", value_type=type(description).__name__)
    members: list[dict[str, Any]] = []
    if refs is not None:
        if sketch is not None:
            lists = [_entity_list(target, sketch=sketch)]
        for entity in _resolve(lists, refs):
            kind = _entity_kind(entity)
            geom = _shift_geom(kind, _copy_geom(entity), -base[0], -base[1])
            members.append({"uuid": new_uuid(), "kind": kind, "layer": _layer_name(store, entity),
                            "construction": bool(_get(entity, "construction", False)), "geom": geom})
    row = {"uuid": new_uuid(), "name": text, "base_nm": [int(base[0]), int(base[1])],
           "description": description, "entities": members, "attdefs": []}
    _table(store, "blocks").append(row)
    return [str(row["uuid"])]


def block_insert(target: Any, *, block: Any, at_nm: Any = (0, 0), rotation_deg: float = 0.0,
                 scale: Any = 1.0, layer: Any = None, attributes: Any = None,
                 sketch: Any = None) -> list[str]:
    """插入块实例 → `[insert_uuid, *member_entity_uuid]`（实例 uuid 在**第一个**）。

    语义：把定义里的局部几何按 `translate(at_nm) ∘ rotate(rotation_deg) ∘ scale(scale)`
    变换后作为**普通 L1 实体**落进目标草图（成员 uuid 记在实例记录 `entity_uuids` 里）；
    定义里每个 ATTDEF 生成一条 ATTRIB（值取 `attributes[tag]`，未给就用 attdef 的 `default`）。

    参数（长度整数纳米）：`at_nm` 插入点；`rotation_deg` 逆时针角度（浮点度）；`scale` = 正数
    （等比）或 `(sx, sy)` 二元正数对；`layer=None` 时成员沿用定义里的层，给名字/uuid 则整体覆盖
    （层必须已存在，否则 `unknown_layer`）；`attributes` = `{tag: value}`（未知 tag ⇒
    `CadError(reason="unknown_attribute")`，不静默丢弃、不静默新建）；`sketch` 见 `_entity_list`。

    边界：`sx != sy` 且成员含 `circle`/`arc` ⇒ `CadError(reason="unsupported_geometry")`
    （L1 没有椭圆，绝不把椭圆当圆存）；块名为**外部参照占位**时会 `unknown_block`（两张表不混用，
    BIND 需真读文件，本层不做）；坏参数抛 `invalid_argument` / `invalid_length` / `unknown_block`。
    """
    store, _ = _view(target)
    definition = _find_block(store, block)
    at = _point(at_nm, what="at_nm")
    rotation = _angle(rotation_deg)
    sx, sy = _scale_pair(scale)
    members = definition.get("entities")
    if not isinstance(members, list):
        raise _fail("corrupt_store", "block definition has no entity list", block=str(definition.get("name")))
    if sx != sy:
        for member in members:
            if _get(member, "kind", None) in ("circle", "arc"):
                raise _fail("unsupported_geometry", "non-uniform scale cannot represent a circle/arc",
                            block=str(definition.get("name")), kind=str(_get(member, "kind", None)),
                            scale=[sx, sy])
    override = None
    if layer is not None:
        override = str(_find_layer(store, layer).get("name", _DEFAULT_LAYER))
    wanted: dict[str, Any] = {}
    if attributes is not None:
        if not isinstance(attributes, _MappingABC):
            raise _fail("invalid_argument", "attributes must be a mapping of tag -> value",
                        value_type=type(attributes).__name__)
        wanted = {str(key): value for key, value in attributes.items()}
    attdefs = definition.get("attdefs")
    if not isinstance(attdefs, list):
        raise _fail("corrupt_store", "block definition has no attdef table",
                    block=str(definition.get("name")))
    known = {str(item.get("tag", "")) for item in attdefs}
    for tag in wanted:
        if tag not in known:
            raise _fail("unknown_attribute", "tag is not defined on this block", tag=tag,
                        block=str(definition.get("name")), known=sorted(known))

    matrix = _mat_compose(_mat_translate(at[0], at[1]),
                          _mat_compose(_mat_rotate(rotation), (sx, 0.0, 0.0, 0.0, sy, 0.0)))
    sink = _entity_list(target, sketch=sketch)
    created: list[str] = []
    for member in members:
        clone = {"uuid": new_uuid(), "kind": str(_get(member, "kind", "")),
                 "layer": override or str(_get(member, "layer", _DEFAULT_LAYER)),
                 "construction": bool(_get(member, "construction", False)),
                 "geom": copy.deepcopy(_geom_of(member))}
        _entity_kind(clone)
        transform_entity(clone, matrix)
        sink.append(clone)
        created.append(str(clone["uuid"]))
    attribs: list[dict[str, Any]] = []
    for item in attdefs:
        tag = str(item.get("tag", ""))
        value = wanted[tag] if tag in wanted else str(item.get("default", ""))
        attribs.append({"uuid": new_uuid(), "tag": tag, "value": str(value),
                        "attdef": str(item.get("uuid", ""))})
    row = {"uuid": new_uuid(), "block": str(definition.get("uuid", "")),
           "block_name": str(definition.get("name", "")), "at_nm": [int(at[0]), int(at[1])],
           "rotation_deg": float(rotation), "scale": [float(sx), float(sy)],
           "layer": override, "entity_uuids": created, "attributes": attribs}
    _table(store, "inserts").append(row)
    return [str(row["uuid"])] + created


def block_explode(target: Any, *, insert: Any) -> list[str]:
    """炸开块实例 → **定义顺序的** `[member_entity_uuid, ...]`（几何一个纳米都不动）。

    语义：本层"插入即落几何"，所以炸开 = **解除实例标记 + 丢掉该实例的 ATTRIB**；成员实体本身
    原样留在草图里（返回它们的 uuid），实例记录被移除。

    边界：实例 uuid 未知 ⇒ `unknown_reference`；实例记录里 `entity_uuids` 坏掉 ⇒ `corrupt_store`
    （先查再写，不半接受）；**不删实体**，要删几何请显式用自己的编辑命令。
    """
    store, _ = _view(target)
    row = _find_insert(store, insert)
    members = row.get("entity_uuids")
    if not isinstance(members, list):
        raise _fail("corrupt_store", "insert record has no entity_uuids list", insert=str(row.get("uuid", "")))
    _table(store, "inserts").remove(row)
    return [str(item) for item in members]


def block_delete(target: Any, *, block: Any, purge_instances: bool = False) -> list[str]:
    """删除块定义 → `[block_uuid]`（`purge_instances=True` 时连同其全部实例一起删）。

    语义：没有实例时直接删定义；有实例且 `purge_instances=False` ⇒ `CadError(reason="block_in_use")`
    （不静默删别人的几何）；`purge_instances=True` 时删除该块的全部实例记录**及其成员实体几何**
    （显式请求才动图）。

    边界：只删**本块**的实例（其它块不受影响）；返回的是块 uuid，被删掉的成员实体 uuid 不再返回
    （要逐个保留请先 `block_explode` 或自行查 `inserts`）。
    """
    store, lists = _view(target)
    row = _find_block(store, block)
    block_uuid = str(row.get("uuid", ""))
    inserts = [item for item in _table(store, "inserts") if item.get("block") == block_uuid]
    if inserts and purge_instances is not True:
        raise _fail("block_in_use", "block still has inserts: pass purge_instances=True to remove them",
                    block=str(row.get("name", "")), insert_count=len(inserts))
    if purge_instances is not True and purge_instances is not False:
        raise _fail("invalid_argument", "purge_instances must be a bool",
                    value=repr(purge_instances))
    doomed: set[str] = set()
    for item in inserts:
        for member_uuid in item.get("entity_uuids", []) or []:
            doomed.add(str(member_uuid))
        _table(store, "inserts").remove(item)
    if doomed:
        for group in lists:
            group[:] = [entity for entity in group if _uuid_of(entity) not in doomed]
    _table(store, "blocks").remove(row)
    return [block_uuid]


# ======================================================================================
# 块属性（ATTDEF / ATTRIB）
# ======================================================================================


def _find_attdef(definition: dict[str, Any], tag: Any) -> dict[str, Any]:
    """按 tag（大小写不敏感）在块定义里找属性定义（`unknown_attribute`）。"""
    text = _text(tag, what="tag")
    for item in definition.get("attdefs", []) or []:
        if str(item.get("tag", "")).casefold() == text.casefold():
            return item
    raise _fail("unknown_attribute", "tag is not defined on this block", tag=text,
                block=str(definition.get("name", "")))


def attdef_create(target: Any, *, block: Any, tag: str, prompt_key: Any = None, default: str = "",
                  visible: bool = True) -> list[str]:
    """给块定义加属性定义（ATTDEF）→ `[attdef_uuid]`。

    参数：`tag` 非空、大小写不敏感唯一（重名 ⇒ `duplicate_name`）；`prompt_key` 是**可选**的
    i18n key（点分 ASCII，形状不对抛 `invalid_argument`）；`default` 是新实例的缺省值；
    `visible` 是否可见（严格 bool）。

    边界：**不回溯既有实例**（不隐式改图）；要给已插入的实例补这条属性，请显式
    `attrib_set(insert=..., tag=..., value=...)` —— 它会按 attdef 补建缺失的 ATTRIB。
    块不存在 ⇒ `unknown_block`。
    """
    store, _ = _view(target)
    definition = _find_block(store, block)
    text = _text(tag, what="tag")
    for item in definition.get("attdefs", []) or []:
        if str(item.get("tag", "")).casefold() == text.casefold():
            raise _fail("duplicate_name", "attribute tag already defined on this block", tag=text)
    if not isinstance(default, str):
        raise _fail("invalid_argument", "default must be a string", value_type=type(default).__name__)
    row = {"uuid": new_uuid(), "tag": text, "default": default,
           "prompt_key": None if prompt_key is None else _key(prompt_key, what="prompt_key"),
           "visible": _flag(visible, what="visible")}
    attdefs = definition.get("attdefs")
    if not isinstance(attdefs, list):
        raise _fail("corrupt_store", "block definition has no attdef table")
    attdefs.append(row)
    return [str(row["uuid"])]


def attdef_delete(target: Any, *, block: Any, tag: str) -> list[str]:
    """删除块定义的属性定义 → `[attdef_uuid]`。

    语义：同时删除该块**全部实例**上对应的 ATTRIB 记录（否则实例上会留下悬空属性）。
    边界：不回溯其它块；tag 未定义 ⇒ `unknown_attribute`；返回被删的 attdef uuid（被删的 ATTRIB
    uuid 不再返回）。
    """
    store, _ = _view(target)
    definition = _find_block(store, block)
    row = _find_attdef(definition, tag)
    tag_text = str(row.get("tag", ""))
    definition.get("attdefs", []).remove(row)
    for insert_row in _table(store, "inserts"):
        if insert_row.get("block") != str(definition.get("uuid", "")):
            continue
        attribs = insert_row.get("attributes")
        if isinstance(attribs, list):
            insert_row["attributes"] = [item for item in attribs
                                        if str(item.get("tag", "")).casefold() != tag_text.casefold()]
    return [str(row["uuid"])]


def attrib_set(target: Any, *, insert: Any, tag: str, value: str) -> list[str]:
    """设置块实例的属性值（ATTRIB）→ `[attrib_uuid]`。

    语义：`tag` 必须在该块的 ATTDEF 里（否则 `unknown_attribute`）；实例上还没有这条 ATTRIB 时
    **按需补建**（显式调用就补，不隐式）；已有则改值。

    参数：`insert` = 实例 uuid（实例没有名字）；`value` 必须是字符串（数字/键值请先自己格式化，
    本层不做隐式转换）；`tag` 大小写不敏感匹配。
    边界：实例与定义必须对得上（实例的 `block` 指向的 block uuid 不存在 ⇒ `unknown_block`）。
    """
    store, _ = _view(target)
    row = _find_insert(store, insert)
    definition = _find_block(store, str(row.get("block", "")))
    attdef = _find_attdef(definition, tag)
    if not isinstance(value, str):
        raise _fail("invalid_argument", "value must be a string", value_type=type(value).__name__)
    tag_text = str(attdef.get("tag", ""))
    attribs = row.get("attributes")
    if not isinstance(attribs, list):
        raise _fail("corrupt_store", "insert record has no attributes list", insert=str(row.get("uuid", "")))
    for item in attribs:
        if str(item.get("tag", "")).casefold() == tag_text.casefold():
            item["value"] = value
            return [str(item.get("uuid", ""))]
    item = {"uuid": new_uuid(), "tag": tag_text, "value": value, "attdef": str(attdef.get("uuid", ""))}
    attribs.append(item)
    return [str(new_uuid() if False else item["uuid"])]


def attrib_batch(target: Any, *, block: Any, tag: str, value: str, inserts: Any = None) -> list[str]:
    """按块定义**批量**改属性值（跨实例 ATTRIB）→ 被改/新建的 ATTRIB uuid 列表。

    语义：把该块**全部实例**（或 `inserts` 点名的那些实例）上 `tag` 对应的 ATTRIB 值统一改成
    `value`；实例上还没有这条 ATTRIB 时按需补建（口径同 `attrib_set` 的"显式调用就补，不隐式"）。
    返回顺序 = 实例在实例表里的出现顺序；每个实例恰好一个 uuid（改值返回原 ATTRIB uuid，
    补建返回新 ATTRIB uuid）。

    参数：`block` = 块名（大小写不敏感）或 block uuid；`tag` 必须在该块 ATTDEF 里（否则
    `unknown_attribute`，大小写不敏感）；`value` 必须是字符串（本层不做隐式转换）；
    `inserts` = 可选实例 uuid 序列（单个 uuid 也行），缺省 = 该块全部实例。

    边界（**先全量校验、后统一写回**：半路失败时一条 ATTRIB 都不动）：
    `inserts` 点名了不属于该块的实例 ⇒ `unknown_reference`；`inserts` 是空序列 ⇒ `empty_selection`
    （不静默 no-op）；该块一个实例都没有（或点名后为空）⇒ `no_instances`；未知块 ⇒ `unknown_block`；
    实例记录没有属性表 ⇒ `corrupt_store`。本操作**只改属性表**，不动几何、不动实体。
    """
    store, _ = _view(target)
    definition = _find_block(store, block)
    attdef = _find_attdef(definition, tag)
    if not isinstance(value, str):
        raise _fail("invalid_argument", "value must be a string", value_type=type(value).__name__)
    tag_text = str(attdef.get("tag", ""))
    block_uuid = str(definition.get("uuid", ""))
    rows = [row for row in _table(store, "inserts")
            if isinstance(row, _MappingABC) and row.get("block") == block_uuid]
    if inserts is not None:
        if isinstance(inserts, (str, bytes)):
            refs: list[Any] = [inserts]
        elif isinstance(inserts, (list, tuple)):
            refs = list(inserts)
        else:
            raise _fail("invalid_argument", "inserts must be an insert uuid or a sequence of them",
                        value_type=type(inserts).__name__)
        if not refs:
            raise _fail("empty_selection", "inserts is empty: refusing to no-op silently")
        chosen: list[Any] = []
        for ref in refs:
            row = _find_insert(store, ref)
            if str(row.get("block", "")) != block_uuid:
                raise _fail("unknown_reference", "insert does not belong to this block",
                            insert=str(ref), block=str(definition.get("name", "")))
            if not any(row is seen for seen in chosen):
                chosen.append(row)
        rows = chosen
    if not rows:
        raise _fail("no_instances", "this block has no instances to update",
                    block=str(definition.get("name", "")))
    for row in rows:  # 先全量校验：属性表形状不对时一条都不写
        if not isinstance(row.get("attributes"), list):
            raise _fail("corrupt_store", "insert record has no attributes list",
                        insert=str(row.get("uuid", "")))
    changed: list[str] = []
    for row in rows:
        attribs = row["attributes"]
        hit = None
        for item in attribs:
            if isinstance(item, _MappingABC) and str(item.get("tag", "")).casefold() == tag_text.casefold():
                hit = item
                break
        if hit is None:
            hit = {"uuid": new_uuid(), "tag": tag_text, "value": value,
                   "attdef": str(attdef.get("uuid", ""))}
            attribs.append(hit)
        else:
            _set(hit, "value", value)
        changed.append(str(_get(hit, "uuid", "")))
    return changed

# ======================================================================================
# 编组
# ======================================================================================


def _find_group(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 或大小写不敏感的组名找组（`unknown_group`）。"""
    text = _text(ref, what="group")
    for row in _table(store, "groups"):
        if row.get("uuid") == text:
            return row
    hits = [row for row in _table(store, "groups") if str(row.get("name", "")).casefold() == text.casefold()]
    if not hits:
        raise _fail("unknown_group", "no group with this name or uuid", group=text)
    return hits[0]


def group_create(target: Any, *, name: str, refs: Any, description: str = "") -> list[str]:
    """新建编组 → `[group_uuid]`。

    参数：`name` 非空、大小写不敏感唯一（重名 ⇒ `duplicate_name`）；`refs` = 成员实体 uuid 序列
    （**不能为空**，空报 `empty_selection`）；`description` 自由文本。

    边界：成员 uuid 必须能在目标里找到（否则 `unknown_reference`）；成员重复自动去重（保序）；
    组是"账"不是"锁" —— **不**因为组员在锁定层就拒绝成组（成组不改几何）。
    """
    # 校验**前置**（`_view(create=True)` 会给空目标建组织表 ⇒ 非法入参就会把文档写坏）：
    # `_view(create=False)` 在建表前会把 lists 丢掉，故这里只读取一次实体列表；悬空引用 /
    # 空选择必须在落库之前报出来。全部校验过了才建表、才追加组行。
    sketches = _get(target, "sketches", None)
    lists = ([sketch_entities(item) for item in sketches] if isinstance(sketches, list)
             else [sketch_entities(target)])
    members = [_uuid_of(entity) for entity in _resolve(lists, refs)]
    text = _name(name, what="group.name")
    if not isinstance(description, str):
        raise _fail("invalid_argument", "description must be a string", value_type=type(description).__name__)
    store, _ = _view(target, create=False)
    if store is not None:
        for row in _table(store, "groups"):
            if str(row.get("name", "")).casefold() == text.casefold():
                raise _fail("duplicate_name", "group name already exists", group=text)
    else:  # 校验全过 ⇒ 这时才落库
        store, _ = _view(target)
    row = {"uuid": new_uuid(), "name": text, "description": description, "members": members}
    _table(store, "groups").append(row)
    return [str(row["uuid"])]


def group_add(target: Any, *, group: Any, refs: Any) -> list[str]:
    """把实体加进组 → `[group_uuid]`（已在组里的成员自动跳过，不报错、不重复计数）。

    边界：`refs` 为空 ⇒ `empty_selection`；实体 uuid 未知 ⇒ `unknown_reference`；组未知 ⇒
    `unknown_group`；**先全量校验再改成员表**（半路失败时组员一个都不动）。
    """
    store, lists = _view(target)
    row = _find_group(store, group)
    members = row.get("members")
    if not isinstance(members, list):
        raise _fail("corrupt_store", "group record has no members list", group=str(row.get("name", "")))
    for entity in _resolve(lists, refs):
        entity_uuid = _uuid_of(entity)
        if entity_uuid not in members:
            members.append(entity_uuid)
    return [str(row["uuid"])]


def group_remove(target: Any, *, group: Any, refs: Any) -> list[str]:
    """把实体移出组 → `[group_uuid]`。

    边界：`refs` 为空 ⇒ `empty_selection`；成员**不在组里** ⇒ `CadError(reason="not_a_member")`
    （不静默忽略"我明明点了它"）；成员全部移光后组仍然存在（空组合法，要删组请 `group_delete`）；
    **不删实体**。
    """
    store, _ = _view(target)
    row = _find_group(store, group)
    members = row.get("members")
    if not isinstance(members, list):
        raise _fail("corrupt_store", "group record has no members list", group=str(row.get("name", "")))
    wanted: list[str] = []
    if isinstance(refs, (str, bytes)):
        items: list[Any] = [refs]
    elif isinstance(refs, (list, tuple)):
        items = list(refs)
    else:
        raise _fail("invalid_argument", "refs must be a sequence of entity uuids",
                    value_type=type(refs).__name__)
    if not items:
        raise _fail("empty_selection", "refs is empty: refusing to no-op silently")
    for ref in items:
        text = _text(ref, what="entity")
        if text not in members:
            raise _fail("not_a_member", "entity is not a member of this group", reference=text,
                        group=str(row.get("name", "")))
        if text not in wanted:
            wanted.append(text)
    for entity_uuid in wanted:
        members.remove(entity_uuid)
    return [str(row["uuid"])]


def group_delete(target: Any, *, group: Any) -> list[str]:
    """删除编组 → `[group_uuid]`（**不删任何实体**，只是解散这张账）。"""
    store, _ = _view(target)
    row = _find_group(store, group)
    _table(store, "groups").remove(row)
    return [str(row["uuid"])]


# ======================================================================================
# 外部参照占位（XREF）
# ======================================================================================


def _find_xref(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 或大小写不敏感的名字找参照记录（`unknown_reference`）。"""
    text = _text(ref, what="xref")
    for row in _table(store, "xrefs"):
        if row.get("uuid") == text:
            return row
    hits = [row for row in _table(store, "xrefs") if str(row.get("name", "")).casefold() == text.casefold()]
    if not hits:
        raise _fail("unknown_reference", "no external reference with this name or uuid", reference=text)
    return hits[0]


def xref_attach(target: Any, *, name: str, path: str, at_nm: Any = (0, 0), scale: Any = 1.0,
                rotation_deg: float = 0.0) -> list[str]:
    """挂一个外部参照**占位** → `[xref_uuid]`（`loaded=False`，本层不读文件）。

    参数：`name` 非空、大小写不敏感唯一（重名 ⇒ `duplicate_name`）；`path` 非空字符串（**只存路径，
    不打开、不校验存在性** —— 本层零文件 I/O）；`at_nm` 插入点（整数纳米）；`scale` 正数或
    `(sx, sy)` 正数对；`rotation_deg` 逆时针角度。

    边界：占位记录**不带几何**（成员实体为 0 条，`0` 是真为零）；真正加载/绑定属于 io 层，
    见 `xref_set(loaded=True)` 的显式降级；坏参数抛 `invalid_argument` / `invalid_name` /
    `duplicate_name` / `unknown_layer`（不涉及）。
    """
    store, _ = _view(target)
    text = _name(name, what="xref.name")
    for row in _table(store, "xrefs"):
        if str(row.get("name", "")).casefold() == text.casefold():
            raise _fail("duplicate_name", "external reference name already exists", name=text)
    at = _point(at_nm, what="at_nm")
    sx, sy = _scale_pair(scale)
    row = {"uuid": new_uuid(), "name": text, "path": _text(path, what="path"),
           "at_nm": [int(at[0]), int(at[1])], "scale": [float(sx), float(sy)],
           "rotation_deg": _angle(rotation_deg), "loaded": False, "entity_uuids": []}
    _table(store, "xrefs").append(row)
    return [str(row["uuid"])]


def xref_set(target: Any, *, xref: Any, path: Any = _UNSET, at_nm: Any = _UNSET,
             scale: Any = _UNSET, rotation_deg: Any = _UNSET, loaded: Any = _UNSET) -> list[str]:
    """改外部参照占位记录 → `[xref_uuid]`（未传的关键字不改）。

    边界（**显式降级**）：`loaded=True` 一律抛 ``CadError(reason="dependency_missing")`` ——
    本层没有文件读取器，宁可让上层看见 `NA`，也不假装"加载成功"；`loaded=False` 是合法写入
    （把记录标回"未加载"，几何不动，因为占位本来就没有几何）。占位记录**不产几何**，
    所以改 `at_nm` / `scale` / `rotation_deg` 只改记录，不改图。
    """
    store, _ = _view(target)
    row = _find_xref(store, xref)
    if loaded is not _UNSET:
        if _flag(loaded, what="loaded"):
            raise _fail("dependency_missing", "this layer performs no file I/O: xref cannot be loaded",
                        what="xref_loader", xref=str(row.get("name", "")))
        row["loaded"] = False
    if path is not _UNSET:
        row["path"] = _text(path, what="path")
    if at_nm is not _UNSET:
        at = _point(at_nm, what="at_nm")
        row["at_nm"] = [int(at[0]), int(at[1])]
    if scale is not _UNSET:
        sx, sy = _scale_pair(scale)
        row["scale"] = [float(sx), float(sy)]
    if rotation_deg is not _UNSET:
        row["rotation_deg"] = _angle(rotation_deg)
    return [str(row["uuid"])]


def xref_detach(target: Any, *, xref: Any) -> list[str]:
    """摘掉外部参照占位 → `[xref_uuid]`。

    边界：占位**没有宿主几何**，因此本操作一个实体都不删（`entity_uuids` 恒为 `[]`，
    `0` 是真为零）；未知参照 ⇒ `unknown_reference`。
    """
    store, _ = _view(target)
    row = _find_xref(store, xref)
    _table(store, "xrefs").remove(row)
    return [str(row["uuid"])]


def xref_filter(target: Any, *, name: Any = None, name_prefix: Any = None, path_prefix: Any = None,
                loaded: Any = None) -> list[str]:
    """过滤外部参照占位 → **按名字（大小写不敏感）排序**的 `[xref_uuid, ...]`。

    参数：`name` 精确名；`name_prefix` 名字前缀；`path_prefix` 路径前缀（原样前缀匹配，不做
    路径规范化 —— 本层不碰文件系统）；`loaded` 精确匹配（本层只会是 `False`）。

    边界：纯查询，不建表、不写入；没有记录返回 `[]`（未采），不代表图上没有参照。
    """
    store, _ = _view(target, create=False)
    if store is None:
        return []
    rows = list(_table(store, "xrefs"))
    if name is not None:
        key = _text(name, what="name").casefold()
        rows = [row for row in rows if str(row.get("name", "")).casefold() == key]
    if name_prefix is not None:
        prefix = _text(name_prefix, what="name_prefix").casefold()
        rows = [row for row in rows if str(row.get("name", "")).casefold().startswith(prefix)]
    if path_prefix is not None:
        prefix = _text(path_prefix, what="path_prefix")
        rows = [row for row in rows if str(row.get("path", "")).startswith(prefix)]
    if loaded is not None:
        wanted = _flag(loaded, what="loaded")
        rows = [row for row in rows if bool(row.get("loaded")) is wanted]
    rows.sort(key=lambda row: str(row.get("name", "")).casefold())
    return [str(row.get("uuid")) for row in rows]


# ======================================================================================
# 冻结注册表
# ======================================================================================

#: 隔离快照在组织表里的键（只加键，不改冻结的六张表）。
_ISOLATION_KEY = "isolation"

#: 当前层（AutoCAD CLAYER 同口径）是**图档级设置**：草图写自身、文档写 ``extra``，不改 L1 实体字段。
_CURRENT_LAYER_KEY = "current_layer"

# 本轮新增操作的导出名：``__all__`` 主清单按字母序维护在文件头，新增项在此追加，避免两处漂移。
__all__ += ["current_layer", "isolate_layers", "unisolate_layers"]


def _settings_holder(target: Any) -> Any:
    """图档级设置（当前层等）的落点：文档 → ``doc.extra``；草图/映射 → 目标自身。"""
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        extra = _get(target, "extra", None)
        if isinstance(extra, _MutableMappingABC):
            return extra
        if isinstance(target, _MutableMappingABC):
            return target
        return None
    if isinstance(target, _MutableMappingABC):
        return target
    return None


def _layer_refs(layers: Any, names: Any, layer: Any) -> list[Any]:
    """把 ``layers=`` / ``names=`` / ``layer=`` 三种点名拼成一个引用列表（一个都不给 ⇒ invalid_argument）。"""
    refs: list[Any] = []
    for value in (layers, names):
        if value is None:
            continue
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, (list, tuple)):
            refs.extend(value)
        else:
            raise _fail("invalid_argument", "layer list must be a name/uuid or a list of them",
                        value_type=type(value).__name__)
    if layer is not None:
        refs.append(layer)
    if not refs:
        raise _fail("invalid_argument", "pass at least one of layers= / names= / layer=")
    return refs


def current_layer(target: Any, *, name: Any = None, layer: Any = None) -> list[str]:
    """设置当前层（AutoCAD CLAYER 同口径）→ `[layer_uuid]`。

    语义：当前层是**图档级设置**（新几何默认落在哪一层），所以写 `"current_layer"` 键本身、
    **不动** `organization` 表结构：草图目标写 `sk["current_layer"]`，文档目标写
    `doc.extra["current_layer"]`（A5 的未知顶层字段容器，随 L1 往返存活）。

    边界：层必须已在组织表里（否则 `unknown_layer`）；`name=` / `layer=` 至少给一个，都给且不同名 ⇒
    `invalid_argument`；先全量校验后落库，非法输入一个键都不写；本层**不替** `cadcore.sketch.primitives`
    决定层 —— 画新几何仍要显式传 `layer=`（prim 的缺省是层 `"0"`，本模块不越界改它的签名）。
    """
    if name is None and layer is None:
        raise _fail("invalid_argument", "current_layer needs name= or layer=")
    if name is not None and layer is not None and str(name) != str(layer):
        raise _fail("invalid_argument", "name= and layer= disagree", name=str(name), layer=str(layer))
    holder = _settings_holder(target)
    if holder is None:
        raise _fail("bad_target", "target has no mutable container for drawing settings",
                    value_type=type(target).__name__)
    store = _child(holder, create=False)
    wanted = str(name if name is not None else layer)
    if store is None:
        raise _fail("unknown_layer", "organization table is empty", layer=wanted, known=[])
    row = _find_layer(store, wanted, bootstrap=False)
    from .features.organization__w10 import layer_state_set

    # Freeze/display consumers read the organization store, not the legacy
    # drawing setting. Keep both entries coherent when switching layers.
    layer_state_set(target, layer=row["uuid"], current=True)
    holder[_CURRENT_LAYER_KEY] = str(row.get("name", _DEFAULT_LAYER))
    return [str(row["uuid"])]


def _layer_state(row: dict[str, Any]) -> dict[str, Any]:
    """一层的可见态快照（只取隔离会改的字段；恢复时逐字段写回，锁定/颜色等一个都不动）。"""
    return {"uuid": str(row.get("uuid", "")), "name": str(row.get("name", "")),
            "shown": bool(row.get("visible", True))}


def isolate_layers(target: Any, *, layers: Any = None, names: Any = None,
                   layer: Any = None) -> list[str]:
    """隔离图层（AutoCAD LAYISO 同口径）→ **被保留（可见）层**的 `[layer_uuid, ...]`（按层名排序）。

    语义：点名的层置可见、其余层置不可见；隔离前的可见态**整体**存进组织表 `store["isolation"]`
    （只加键，不改冻结的六张表），供 `unisolate_layers` 一次还原；再次隔离覆盖旧快照（同 LAYISO 再执行）。
    点名：`layers=["A","B"]` / `names=[...]` / `layer="A"`（层名或 uuid，大小写不敏感）。

    边界：点名为空 ⇒ `invalid_argument`；未知层 ⇒ `unknown_layer`（**先全量校验再落库**，校验失败
    一层都不改）；只改 `visible`，冻结/锁定/颜色一个字段都不动；组织表还没建 ⇒ `unknown_layer`。
    """
    refs = _layer_refs(layers, names, layer)
    store, _ = _view(target, create=False)
    if store is None:
        raise _fail("unknown_layer", "organization table is empty: nothing to isolate",
                    layer=str(refs[0]), known=[])
    rows = _layers(store, bootstrap=False)
    if not rows:
        raise _fail("unknown_layer", "organization table has no layer rows",
                    layer=str(refs[0]), known=[])
    kept: list[dict[str, Any]] = []
    for ref in refs:
        row = _find_layer(store, ref, bootstrap=False)
        if all(str(other.get("uuid", "")) != str(row.get("uuid", "")) for other in kept):
            kept.append(row)
    kept_uuids = {str(row.get("uuid", "")) for row in kept}
    store[_ISOLATION_KEY] = {"schema": 1, "kept": sorted(kept_uuids),
                             "saved": [_layer_state(row) for row in rows]}
    for row in rows:
        row["visible"] = str(row.get("uuid", "")) in kept_uuids
    return [str(row.get("uuid", ""))
            for row in sorted(kept, key=lambda item: str(item.get("name", "")))]


def unisolate_layers(target: Any, *, layers: Any = None, names: Any = None,
                     layer: Any = None) -> list[str]:
    """取消隔离（AutoCAD LAYUNISO 同口径）→ **被写回快照态的** `[layer_uuid, ...]`（按层名排序）。

    语义：把 `isolate_layers` 存下的可见态快照逐字段写回（只写 `visible`），然后**移除**快照
    （还原一次即失效）。`layers=` / `names=` / `layer=` 是 LAYUNISO 的兼容入参（只做类型把关），
    **不改变"全量还原"语义** —— AutoCAD 的 LAYUNISO 就是一次还原全部被隔离的层。

    边界：没有快照（没隔离过、或已还原过）⇒ `CadError(reason="invalid_argument")`，不假装成功；
    快照里的层若已被删除，跳过它（不复活、不报错），返回值里也就没有它。
    """
    if layers is not None or names is not None or layer is not None:
        _layer_refs(layers, names, layer)  # 兼容入参只做类型把关，语义仍是"全量还原"
    store, _ = _view(target, create=False)
    snapshot = None if store is None else store.get(_ISOLATION_KEY)
    if not isinstance(snapshot, _MappingABC):
        raise _fail("invalid_argument", "no isolation snapshot: call isolate_layers first")
    saved = snapshot.get("saved")
    if not isinstance(saved, list):
        raise _fail("corrupt_store", "isolation snapshot has no saved rows")
    restored: list[dict[str, Any]] = []
    for item in saved:
        if not isinstance(item, _MappingABC):
            continue
        uuid = str(item.get("uuid", ""))
        row = next((candidate for candidate in _layers(store, bootstrap=False)
                    if str(candidate.get("uuid", "")) == uuid), None)
        if row is None:
            continue
        row["visible"] = bool(item.get("shown", True))
        restored.append(row)
    store.pop(_ISOLATION_KEY, None)
    return [str(row.get("uuid", ""))
            for row in sorted(restored, key=lambda item: str(item.get("name", "")))]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    # 图层体系（颜色/线型/线宽/开关/锁定/过滤器 + 当前层/隔离）
    "layer_create": layer_create,
    "layer_set": layer_set,
    "layer_delete": layer_delete,
    "layer_filter": layer_filter,
    "layer_assign": layer_assign,
    # 当前层 / 隔离 / 取消隔离（CLAYER / LAYISO / LAYUNISO 同口径）
    "current_layer": current_layer,
    "isolate_layers": isolate_layers,
    "unisolate_layers": unisolate_layers,
    # 块定义与插入
    "block_define": block_define,
    "block_insert": block_insert,
    "block_explode": block_explode,
    "block_delete": block_delete,
    # 属性（ATTDEF / ATTRIB）
    "attdef_create": attdef_create,
    "attdef_delete": attdef_delete,
    "attrib_set": attrib_set,
    # 跨实例批量改属性（BATTMAN 的"统改"半边：一次调用改全块实例）
    "attrib_batch": attrib_batch,
    # 组
    "group_create": group_create,
    "group_add": group_add,
    "group_remove": group_remove,
    "group_delete": group_delete,
    # 外部参照占位
    "xref_attach": xref_attach,
    "xref_set": xref_set,
    "xref_detach": xref_detach,
    "xref_filter": xref_filter,
}


# ======================================================================================
# 自检（python3 cadcore/ops/organization.py 或 python3 -m cadcore.ops.organization）
# ======================================================================================


def _expect_error(call: Callable[[], Any], reason: str, *, label: str) -> None:
    """断言"这个调用必须以这个 reason 报 CadError"（阳性对照：异常类型与 reason 都要对）。"""
    try:
        call()
    except CadError as exc:
        assert exc.reason == reason, f"{label}: expected reason={reason!r}, got {exc.reason!r}"
        return
    raise AssertionError(f"{label}: expected CadError(reason={reason!r}), but the call succeeded")


def main(argv: Any = None) -> int:
    """自检：5 组断言（图层 / 块 / 属性 / 组 / 文档不变量与 L1 往返），末行打判定行。"""
    # ---- 1. 图层体系 ----------------------------------------------------------------
    sketch = {"entities": [{"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
                            "geom": {"x1": 0, "y1": 0, "x2": 1000, "y2": 0}},
                           {"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
                            "geom": {"x1": 0, "y1": 0, "x2": 0, "y2": 2000}}],
              "constraints": [], "dimensions": []}
    line_a, line_b = sketch["entities"][0]["uuid"], sketch["entities"][1]["uuid"]
    wall = layer_create(sketch, name="WALL", color=1, linetype="dashed", lineweight_nm=350000)[0]
    assert len(layer_filter(sketch)) == 2, "bootstrap layer '0' + WALL"
    assert layer_filter(sketch, color=1) == [wall], "color filter must select exactly WALL"
    assert layer_assign(sketch, refs=[line_a], layer="wall") == [line_a], "layer name is case-insensitive"
    layer_set(sketch, layer="WALL", locked=True)
    _expect_error(lambda: layer_assign(sketch, refs=[line_b], layer="WALL"), "layer_locked", label="locked target")
    _expect_error(lambda: layer_assign(sketch, refs=[line_a], layer="0"), "layer_locked", label="locked source")
    layer_set(sketch, layer="WALL", locked=False)  # 先解锁：layer_delete 的边界顺序是 锁定 > 非空
    _expect_error(lambda: layer_delete(sketch, layer="WALL"), "layer_not_empty", label="non-empty layer")
    assert layer_delete(sketch, layer="WALL", move_to="0") == [wall], "delete moves entities first"
    assert sketch["entities"][0]["layer"] == "0", "entity moved back to layer 0"
    _expect_error(lambda: layer_delete(sketch, layer="0"), "reserved_layer", label="layer 0")

    # ---- 2. 块定义与插入 ------------------------------------------------------------
    block = block_define(sketch, name="BOLT", refs=[line_a], base_nm=(1000, 0))[0]
    inserted = block_insert(sketch, block=block, at_nm=(5000, 7000))
    assert len(inserted) == 2, "insert returns [insert_uuid, *members]"
    member = [e for e in sketch["entities"] if e["uuid"] == inserted[1]][0]
    assert (member["geom"]["x1"], member["geom"]["y1"]) == (4000, 7000), "base-relative insert lands exactly"
    assert (member["geom"]["x2"], member["geom"]["y2"]) == (5000, 7000), "second endpoint shifted too"
    # 非等比缩放的可表示性边界（docstring 边界 4）：线成员能精确表示 ⇒ 必须成功；
    # 含 circle/arc 的成员不可表示 ⇒ 才报 unsupported_geometry。旧自检拿"纯线成员"的块断言报错，
    # 与实现语义/docstring 不符，属**自检侧缺陷**；此处按证据拆成阳性 + 阴性两条（断言只收紧不放宽）。
    stretched = block_insert(sketch, block=block, at_nm=(2000, 2000), scale=(1.0, 2.0))
    assert len(stretched) == 2, "line-only block must accept a non-uniform scale"
    stretched_member = [e for e in sketch["entities"] if e["uuid"] == stretched[1]][0]
    assert (stretched_member["geom"]["x2"] - stretched_member["geom"]["x1"]) == 1000, "sx=1 keeps x extent"
    ring_source = {"uuid": new_uuid(), "kind": "circle", "layer": "0", "construction": False,
                   "geom": {"cx": 0, "cy": 0, "r": 500}}
    sketch["entities"].append(ring_source)
    ring = block_define(sketch, name="RING", refs=[ring_source["uuid"]])[0]
    _expect_error(lambda: block_insert(sketch, block=ring, scale=(1.0, 2.0)), "unsupported_geometry",
                  label="non-uniform scale on a circle member")
    assert block_explode(sketch, insert=inserted[0]) == [inserted[1]], "explode returns members, keeps geometry"
    _expect_error(lambda: block_explode(sketch, insert=inserted[0]), "unknown_reference", label="exploded once")

    # ---- 3. 属性 --------------------------------------------------------------------
    attdef_create(sketch, block=block, tag="PART", prompt_key="att.part", default="X")
    live = block_insert(sketch, block=block, at_nm=(0, 0), attributes={"PART": "A1"})
    assert len(live) == 2, "one member entity + insert"
    assert attrib_set(sketch, insert=live[0], tag="part", value="A2"), "attrib lookup is case-insensitive"
    # 跨实例批量改属性（同一块两个实例，一次调用两个都改到；点名不属于本块的实例要拒）
    live_b = block_insert(sketch, block=block, at_nm=(1000, 1000), attributes={"PART": "B1"})
    batched = attrib_batch(sketch, block="bolt", tag="part", value="C1")
    assert len(batched) >= 3, "attrib_batch must touch one ATTRIB per instance of the block"
    assert attrib_set(sketch, insert=live_b[0], tag="PART", value="C1") == [batched[-1]], \
        "attrib_batch wrote the very ATTRIB that attrib_set addresses (same uuid)"
    _expect_error(lambda: attrib_batch(sketch, block=block, tag="NOPE", value="x"), "unknown_attribute",
                  label="attrib_batch unknown tag")
    _expect_error(lambda: attrib_batch(sketch, block=block, tag="PART", value="x", inserts=[]),
                  "empty_selection", label="attrib_batch empty selection")
    _expect_error(lambda: block_insert(sketch, block=block, attributes={"NOPE": "1"}), "unknown_attribute",
                  label="unknown attribute tag")
    attdef_delete(sketch, block=block, tag="PART")
    _expect_error(lambda: attrib_set(sketch, insert=live[0], tag="PART", value="A3"), "unknown_attribute",
                  label="deleted attdef")

    # ---- 4. 组 ----------------------------------------------------------------------
    group = group_create(sketch, name="G1", refs=[line_a])[0]
    assert group_add(sketch, group="g1", refs=[line_b]) == [group], "group name is case-insensitive"
    _expect_error(lambda: group_create(sketch, name="G1", refs=[line_a]), "duplicate_name", label="dup group")
    _expect_error(lambda: group_remove(sketch, group="G1", refs=[inserted[1]]), "not_a_member",
                  label="removing a non-member")
    assert group_remove(sketch, group="G1", refs=[line_b]) == [group], "member removed"
    assert group_delete(sketch, group="G1") == [group], "group deleted, entities untouched"

    # ---- 5. 文档目标：参照占位 + L1 不变量 + 往返 ------------------------------------
    from .. import docmodel  # 自检专用（族模块本身不依赖文档层）

    doc = docmodel.new_document()
    doc.sketches.append({"uuid": new_uuid(), "plane": "XY", "entities": [
        {"uuid": new_uuid(), "kind": "line", "layer": "0", "construction": False,
         "geom": {"x1": 0, "y1": 0, "x2": 3000, "y2": 4000}}], "constraints": [], "dimensions": []})
    layer_create(doc, name="ANNO", color="#00FF00", lineweight_nm=0, linetype="dotted")
    xref = xref_attach(doc, name="BASE", path="/mnt/ref/base.dwg", at_nm=(10, 20))[0]
    assert xref_filter(doc, name="base") == [xref], "xref filter is case-insensitive"
    assert xref_filter(doc, path_prefix="/mnt/") == [xref], "path prefix filter"
    _expect_error(lambda: xref_set(doc, xref=xref, loaded=True), "dependency_missing", label="no file I/O")
    assert docmodel.check_invariants(doc) == [], "organization tables must not break L1 invariants"
    text = docmodel.document_to_l1(doc)
    again = docmodel.document_from_l1(text)
    assert len(again.extra["organization"]["layers"]) == 2, "layer table survives the L1 round trip"
    assert xref_detach(doc, xref=xref) == [xref], "xref detach"

    print(f"ORGANIZATION_OPS verdict=PASS ops={len(OPERATIONS)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(main())
