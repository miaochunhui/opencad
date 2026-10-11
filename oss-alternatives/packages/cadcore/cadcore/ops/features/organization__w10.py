#!/usr/bin/env python3
"""cadcore.ops.features.organization__w10 —— 组织与复用族第 n1 条实现片。

一句话用途
----------
在 L1 文档/草图上真实现「图层开/关、冻结/解冻、锁定/解锁与当前图层切换」，并补上全仓一直
缺席的**状态消费者**：按图层状态解析出「显示集 / 可编辑集」的实体 uuid。

本族仍 missing/partial 的 must_have（按 `features/autocad-organization.json` 的 must_have 顺序，
只列 status != implemented 的条目，编号 0..N-1）
--------------------------------------------------------------------------------------
    n0  图层创建与图层特性管理（颜色/线型/线宽/打印）                  partial
    n1  图层开/关、冻结/解冻、锁定/解锁与当前图层切换                  partial   <== 本片
    n2  对象图层指定与改层（特性面板、特性匹配）                        partial
    n3  无用图层清理、图层合并/删除                                     partial
    n4  动态块与块编辑器                                                missing
    n5  块属性编辑与批量改属性                                          partial
    n6  属性提取成表或文件（门窗表/设备表）                             missing
    n7  写块（导出为独立 dwg）                                          missing
    n8  外部参照附着与覆盖（附加/覆盖两种方式）                         partial
    n9  参照状态管理：卸载/重载/拆离/绑定                               partial
    n10 参照路径与相对路径管理                                          partial
    n11 在位编辑参照（REFEDIT/XOPEN）                                   missing
    n12 图纸集与图纸列表管理（DST 数据文件）                            missing
    n13 图纸集自定义特性与字段联动（图号/图名/日期）                    partial
（N = 14；编号取自 `docs/research/cad-parity/status/autocad-organization.json` 的逐条 status，
按 `features/autocad-organization.json` 的 must_have 原始顺序过滤后重编号。）

为什么 n1 在 L1 上装得下（真数据后果，不是桩）
---------------------------------------------
L1 已经有「实体带 `layer` 字段」+「文档层 `extra` 可往返承载未知顶层表」两件事，缺的**只是写路径
与消费者**：状态位没有任何 setter 被 UI/命令接线，且 frozen/off 没有任何读者（SVG 投影只按层分组、
不按可见性过滤）。本片把状态真写进组织表的层行（`visible` / `frozen` / `locked` + 当前层键），
并让 `layer_visible_entities` 成为唯一解析入口 —— 冻结一层之后，返回的实体集**真的少掉**那些实体，
解冻后**真的回来**（自检逐条断言，不是打桩）。

状态矩阵（AutoCAD 口径，三态互不干扰的位 + 各自后果）
-----------------------------------------------------
| 位            | 键         | 真值后果                                              |
|---------------|------------|-------------------------------------------------------|
| 关 / 开       | `visible`  | 关 ⇒ 不显示、不可选、不可编辑、不打印                  |
| 冻结 / 解冻   | `frozen`   | 冻结 ⇒ 不显示、不可选、不可编辑（且不参与重生成）      |
| 锁定 / 解锁   | `locked`   | 锁定 ⇒ **仍显示、仍可选**，但不可编辑（只读保护）      |
| 当前层        | `current_layer` | 当前层**不能被关闭、不能被冻结**；把冻结层切成当前层时**自动解冻** |

据此 `layer_visible_entities` 只有两种目的集（`purpose`）：`display` = 显示集（关/冻结都排除）、
`edit` = 可编辑集（显示集再排除锁定层）。**没有**单独的 `select` 目的集：锁定层在 AutoCAD 里仍可
选中（只是改不动），所以"可选集"恒等于"显示集"，多一个同形分支只会让人以为它俩不同。

边界（写下来防止下游靠猜；"没做"必须看得见，不许静默假绿）
----------------------------------------------------------
1. **未实现的写路径有意不重写**：图层**建/删/改名/颜色/线型/线宽/打印**与锁定写路径已由同族
   `cadcore.ops.organization` 的 `layer_create` / `layer_set` / `layer_delete` 提供，本片**只加"状态
   切换 + 消费者"**，不抄第二份实现（本片新增的层行键只有 `frozen`，其余键沿用同族 schema：
   `uuid` / `name` / `color` / `linetype` / `lineweight_nm` / `visible` / `locked` / `printable`；
   加键不改键，空表首次触达时自举缺省层 `"0"`）。
2. **不改实体字段**：本片一个实体字段都不写（只读 `layer`）。自检里有夹具自证断言：跑完全部操作后
   实体行逐字段不变、顶层键集合不变（只多出组织表内的 `current_layer`）。
3. **不碰文件 I/O / 渲染器 / 服务路由**：本片是纯 L1 层能力（`sk_or_doc` in → `list[str]` out）。
   把 `layer_visible_entities` 接进 SVG 投影与选择工具属别的层/别的席；**UI 与路由接线不在本片写入
   范围**（名下只准写本文件），这是本条的 residual，不作为"已完成"上报。
4. **`-1` / `NA` 口径**：本片只写布尔位与一个字符串键，不写任何长度字段，所以不存在用 0 冒充
   "未设置"的机会；层行里 `frozen` **缺席 = 解冻**（老表兼容，只加键不改键）。
5. **"没采"与"零"不同形**：`layer_state` 在组织表**缺席或没有层行**时抛
   `CadError(reason="no_layer_table")`，**不返回 `[]`**（空列表会让人把"没有状态账"读成"没有任何层
   处于该状态"）。唯一返回缺省态的是 `layer_visible_entities`：层表缺席时所有实体按缺省态
   （显示/解冻/未锁）解析 —— 那是"没有层说它要藏"，不是"没算"。
6. **原子校验**：所有写操作**先全量解析 + 校验，再统一写回**；批量里有一个未知层 ⇒ 抛
   `unknown_layer` 且一个字节都不落盘（自检有一条"被拒的批量写了零"的断言）。
7. **错误一律 `cadcore.errors.CadError(reason=...)`**，reason 机器可读：`unknown_layer` /
   `current_layer_off` / `current_layer_frozen` / `invalid_argument` / `invalid_name` /
   `unsupported_target` / `corrupt_store` / `invalid_entity` / `no_layer_table`。
8. 非 docstring 的字符串字面量一律 ASCII（`tools/cad/i18n_gate.py` 只放行 docstring 里的汉字）。

自检（文件自带，可直接跑；`python3 cadcore/ops/features/organization__w10.py`）
--------------------------------------------------------------------------
夹具直接落一份 L1 草图映射（三行层记录 + 4 实体 + 1 标注），逐条硬断言状态位与其后果，
并断言夹具自身没被污染。判定行：

    FEATURE_OK family=organization op=layer_state_set verdict=PASS
"""

from __future__ import annotations

import json
import sys
import uuid as _uuid
from collections.abc import Mapping as _MappingABC
from collections.abc import MutableMapping as _MutableMappingABC
from typing import Any, Callable

from cadcore.errors import CadError

__all__ = [
    "FAMILY", "OPERATIONS",
    "layer_state_set", "layer_off", "layer_freeze", "current_layer_set",
    "layer_state", "layer_visible_entities",
]

#: 冻结接口之一：族名（与 `cadcore/ops/organization.py` 同族，聚合器按这个名字并表）。
FAMILY = "organization"

_DEFAULT_LAYER = "0"
_STORE_KEY = "organization"
_CURRENT_KEY = "current_layer"
_LEGACY_CURRENT_KEY = "clayer"
_GROUPS = ("entities", "dimensions")
_STATES = ("all", "current", "off", "on", "frozen", "thawed", "locked", "unlocked")
_PURPOSES = ("display", "edit")
_EMPTY_STATE: dict[str, bool] = {"frozen": False, "off": False, "locked": False}


class _Unset:
    """哨兵：区分"没传这个关键字"与"传了 None/False"（`None` 在颜色语义里是合法取值）。"""

    def __repr__(self) -> str:  # pragma: no cover - 只为人读日志
        return "<unset>"


_UNSET = _Unset()


def _fail(reason: str, message: str, **details: Any) -> Any:
    """抛机器可读的 CadError（全文件唯一出口，reason 不许现场拼字符串）。"""
    raise CadError(message, reason=reason, **details)


def _flag(value: Any, *, what: str) -> bool:
    """严格布尔把关：`1` / `"true"` 这类近似值一律拒（避免静默当成 True）。"""
    if not isinstance(value, bool):
        _fail("invalid_argument", "expected a bool", field=what, got=type(value).__name__)
    return bool(value)


def _name(value: Any, *, what: str) -> str:
    """层名把关（非空、不含 AutoCAD 保留字符、长度上限）。"""
    if not isinstance(value, str):
        _fail("invalid_argument", "expected a str", field=what, got=type(value).__name__)
    text = value.strip()
    if not text:
        _fail("invalid_name", "layer name must not be empty", field=what)
    if len(text) > 255:
        _fail("invalid_name", "layer name is too long", field=what, length=len(text))
    for char in text:
        if ord(char) < 32 or char in '<>/\\":;?*|,=`':
            _fail("invalid_name", "layer name contains a reserved character", field=what, char=repr(char))
    return text


def _refs(*, layer: Any = _UNSET, layers: Any = None, names: Any = None) -> list[str]:
    """把 `layer=` / `layers=` / `names=` 三种入参归成非空的层引用列表（字符串）。"""
    refs: list[str] = []
    if layer is not _UNSET and layer is not None:
        refs.append(layer)
    for seq, label in ((layers, "layers"), (names, "names")):
        if seq is None:
            continue
        if isinstance(seq, str) or not isinstance(seq, (list, tuple)):
            _fail("invalid_argument", "expected a sequence of layer references",
                  field=label, got=type(seq).__name__)
        refs.extend(seq)
    if not refs:
        _fail("invalid_argument", "no layer given: pass layer= / layers= / names=")
    out: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            _fail("invalid_argument", "layer reference must be a non-empty string",
                  field="layer", got=type(ref).__name__)
        out.append(ref.strip())
    return out


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按对象身份去重并保持传入顺序（同一个层写两遍只写一次）。"""
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = id(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _store(target: Any, *, create: bool) -> Any:
    """定位组织表（`{schema, layers, blocks, inserts, groups, xrefs}`）并返回其可变持有者映射。

    目标是**文档**（有可变 `extra`）⇒ 表落 `doc.extra["organization"]`；
    目标是**草图映射**（含 `entities` / `dimensions`）⇒ 表落草图自身；
    目标是 **L1 根映射**（含 `sketch`）⇒ 表落根级 `organization` 键（由文档层的未知顶层字段/`extra`
    承接往返）。定位不到 ⇒ `CadError(reason="unsupported_target")`（显式降级，不静默当空表）。
    """
    holder: Any = None
    if isinstance(target, _MappingABC):
        if isinstance(target.get("sketch"), _MappingABC) or any(k in target for k in _GROUPS) \
                or _STORE_KEY in target:
            holder = target
    else:
        extra = getattr(target, "extra", None)
        if isinstance(extra, _MutableMappingABC):
            holder = extra
    if holder is None:
        _fail("unsupported_target", "cannot locate an organization-table holder on this target",
              got=type(target).__name__)
    if not isinstance(holder, _MutableMappingABC):
        _fail("unsupported_target", "organization-table holder is not mutable", got=type(holder).__name__)
    store = holder.get(_STORE_KEY)
    if store is None:
        if not create:
            return None
        store = {"schema": 1, "layers": [], "blocks": [], "inserts": [], "groups": [], "xrefs": []}
        holder[_STORE_KEY] = store
    if not isinstance(store, _MutableMappingABC):
        _fail("corrupt_store", "organization table is not a mapping", got=type(store).__name__)
    return store


def _rows(store: Any, *, bootstrap: bool) -> list[dict[str, Any]]:
    """取层行列表；`bootstrap=True` 时首次触达空表自举缺省层 `"0"`（与同族 module 同口径）。"""
    rows = store.get("layers")
    if rows is None:
        rows = []
        store["layers"] = rows
    if not isinstance(rows, list):
        _fail("corrupt_store", "organization['layers'] is not a list", got=type(rows).__name__)
    for row in rows:
        if not isinstance(row, _MutableMappingABC):
            _fail("corrupt_store", "layer row is not a mutable mapping", got=type(row).__name__)
    if bootstrap and not rows:
        rows.append({"uuid": _uuid.uuid4().hex, "name": _DEFAULT_LAYER, "color": None,
                     "linetype": "continuous", "lineweight_nm": -1, "visible": True,
                     "locked": False, "printable": True, "frozen": False})
    return rows


def _known(rows: list[dict[str, Any]]) -> list[str]:
    """表里已知层名（错误详情用；让失败现场自带"有哪些层"）。"""
    return sorted(str(row.get("name", "")) for row in rows)


def _find(rows: list[dict[str, Any]], ref: Any) -> dict[str, Any]:
    """按 layer_uuid（精确）或层名（大小写不敏感）定位层行；找不到抛 `unknown_layer`。"""
    if not isinstance(ref, str) or not ref.strip():
        _fail("invalid_argument", "layer reference must be a non-empty string",
              field="layer", got=type(ref).__name__)
    text = ref.strip()
    for row in rows:
        if str(row.get("uuid", "")) == text:
            return row
    key = text.casefold()
    for row in rows:
        if str(row.get("name", "")).casefold() == key:
            return row
    _fail("unknown_layer", "no such layer in the organization table", layer=text, known=_known(rows))


def _current_name(store: Any, rows: list[dict[str, Any]]) -> str:
    """当前层名：`current_layer` → 兼容读 `clayer` → 缺省层 `"0"`。"""
    raw = store.get(_CURRENT_KEY)
    if raw is None:
        raw = store.get(_LEGACY_CURRENT_KEY)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _DEFAULT_LAYER


def _is_current(store: Any, rows: list[dict[str, Any]], row: dict[str, Any]) -> bool:
    """这一行是不是当前层（按层名比较，因为当前层键存的是名字）。"""
    return str(row.get("name", "")).casefold() == _current_name(store, rows).casefold()


def _state_of(row: dict[str, Any]) -> dict[str, bool]:
    """层行的三态位（缺键按缺省：显示 / 解冻 / 未锁 —— 只加键不改键的老表兼容口径）。"""
    return {"frozen": bool(row.get("frozen", False)),
            "off": not bool(row.get("visible", True)),
            "locked": bool(row.get("locked", False))}


def _sorted_uuids(rows: list[dict[str, Any]]) -> list[str]:
    """按（层名，大小写不敏感）排序后取 uuid —— 返回顺序确定，跨进程可复现。"""
    ordered = sorted(rows, key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("name", ""))))
    return [str(row.get("uuid", "")) for row in ordered]


def _entity_field(entity: Any, key: str) -> Any:
    """实体字段读（映射或对象两态；L1 实体的字段集是冻结的，本片只读不写）。"""
    if isinstance(entity, _MappingABC):
        return entity.get(key)
    return getattr(entity, key, None)


def _entity_layer(entity: Any) -> str:
    """实体的层名；缺席/空白/None 一律按缺省层 `"0"`（与同族 module 同口径）。"""
    value = _entity_field(entity, "layer")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _DEFAULT_LAYER


def _entity_uuid(entity: Any) -> str:
    """实体的 uuid；缺失即抛 `invalid_entity`（返回列表里不许出现空串冒充实体）。"""
    value = _entity_field(entity, "uuid")
    if not isinstance(value, str) or not value:
        _fail("invalid_entity", "entity has no uuid", got=type(entity).__name__)
    return value


def _entity_groups(target: Any) -> list[list[Any]]:
    """取实体组（`entities` / `dimensions`）；一组都没有 ⇒ `unsupported_target`（不静默返回空集）。

    目标形态（与 `_store` 的三种目标一一对应）：
    * **L1 根映射**（含 `sketch` 映射、且本身不带实体组）⇒ 用根里那份草图；
    * **草图映射**（直接带 `entities`/`dimensions`）⇒ 用它自己；
    * **真文档对象**（`docmodel.Document`）⇒ 按 `doc.sketches` **列表顺序**展开，先把第一份草图
      的实体组按 `_GROUPS` 顺序铺开、再铺第二份（顺序确定、可复现）。
      旧口径只认 `getattr(target, "sketch")` —— `Document` 暴露的是 `sketches`（复数），
      于是**真文档上这条查询直接 `unsupported_target`**（实测：服务端 `layer_visible_entities`
      一律失败），而同一个 `_store` 明明是支持文档的。这里补齐文档分支，判据一条不改。
    """
    if isinstance(target, _MappingABC):
        sketch = target.get("sketch")
        source = sketch if isinstance(sketch, _MappingABC) and not any(k in target for k in _GROUPS) else target
        sources: list[Any] = [source]
    else:
        single = getattr(target, "sketch", None)
        if isinstance(single, _MappingABC):
            sources = [single]
        else:
            many = getattr(target, "sketches", None)
            if isinstance(many, (list, tuple)) and many:
                sources = [item for item in many if isinstance(item, _MappingABC)]
            else:
                sources = []
        if not sources:
            _fail("unsupported_target", "document target exposes no 'sketch'/'sketches' mapping",
                  got=type(target).__name__)
    groups: list[list[Any]] = []
    for source in sources:
        for key in _GROUPS:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, _MappingABC):
                value = list(value.values())
            elif not isinstance(value, (list, tuple)):
                _fail("unsupported_target", "entity group is neither a sequence nor a mapping",
                      group=key, got=type(value).__name__)
            groups.append(list(value))
    if not groups:
        _fail("unsupported_target", "target exposes no 'entities'/'dimensions' groups",
              keys=sorted(str(key) for key in sources[0].keys()))
    return groups


def _entities(target: Any) -> list[Any]:
    """文档顺序展开实体（组顺序 → 组内顺序），顺序确定、可复现。"""
    out: list[Any] = []
    for group in _entity_groups(target):
        out.extend(group)
    return out


# ======================================================================================
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# ======================================================================================


def layer_state_set(target: Any, *, layer: Any = _UNSET, layers: Any = None, names: Any = None,
                    off: Any = _UNSET, frozen: Any = _UNSET, locked: Any = _UNSET,
                    current: Any = _UNSET) -> list[str]:
    """一次设定位（关/冻结/锁定/当前层）→ **被写的层 uuid**，按层名排序。

    参数：`layer=`（层名或 layer_uuid，大小写不敏感）/ `layers=[...]` / `names=[...]` 三种点名方式；
    `off=True` 关层（写 `visible=False`）、`off=False` 开层；`frozen=True/False` 冻结/解冻（写 `frozen`）；
    `locked=True/False` 锁定/解锁（写 `locked`）；`current=True` 把**唯一**点名层切成当前层。
    同一层被点名多次只写一次；没传的位一个字节都不动。

    不变量（先全量校验后统一写回，失败时文档一字未动）：
    - 点名里出现未知层 ⇒ `CadError(reason="unknown_layer")`；
    - 关当前层 ⇒ `CadError(reason="current_layer_off")`（AutoCAD：当前层不能被关闭）；
    - 冻结当前层 ⇒ `CadError(reason="current_layer_frozen")`（AutoCAD：当前层不能被冻结）；
    - 同一次调用里同时 `frozen=True` 与 `current=True` ⇒ `invalid_argument`（语义自相矛盾）；
    - `current=True` 点名不止一层 ⇒ `invalid_argument`；
    - 一个位都没传 ⇒ `invalid_argument`；坏入参 ⇒ `invalid_argument` / `invalid_name`。

    真后果：把冻结层切成当前层时**自动解冻该层**（AutoCAD 口径），并更新组织表 `current_layer`
    键（若表里已有兼容键 `clayer` 也同步刷新，不新造它）。仅改状态位，**不写任何实体字段**。
    """
    refs = _refs(layer=layer, layers=layers, names=names)
    flags: dict[str, bool] = {}
    if off is not _UNSET:
        flags["off"] = _flag(off, what="off")
    if frozen is not _UNSET:
        flags["frozen"] = _flag(frozen, what="frozen")
    if locked is not _UNSET:
        flags["locked"] = _flag(locked, what="locked")
    if current is not _UNSET:
        flags["current"] = _flag(current, what="current")
    if not flags:
        _fail("invalid_argument", "nothing to set: pass off / frozen / locked / current")
    if flags.get("current") and len(set(refs)) != 1:
        _fail("invalid_argument", "the current-layer switch takes exactly one layer", given=len(set(refs)))
    if flags.get("current") and flags.get("frozen") is True:
        _fail("invalid_argument", "cannot freeze the current layer in the same call")
    store = _store(target, create=True)
    rows = _rows(store, bootstrap=True)
    targets = _dedupe([_find(rows, ref) for ref in refs])  # 先全量校验（未知层在这里就炸）
    if flags.get("off") is True:
        for row in targets:
            if _is_current(store, rows, row):
                _fail("current_layer_off", "the current layer cannot be turned off",
                      layer=str(row.get("name", "")), current=_current_name(store, rows))
    if flags.get("frozen") is True:
        for row in targets:
            if _is_current(store, rows, row):
                _fail("current_layer_frozen", "the current layer cannot be frozen",
                      layer=str(row.get("name", "")), current=_current_name(store, rows))
    for row in targets:  # 校验全过，才开始写
        if "off" in flags:
            row["visible"] = not flags["off"]
        if "frozen" in flags:
            row["frozen"] = flags["frozen"]
        if "locked" in flags:
            row["locked"] = flags["locked"]
    if flags.get("current"):
        row = targets[0]
        if bool(row.get("frozen", False)):
            row["frozen"] = False  # 切到冻结层 ⇒ 自动解冻（AutoCAD 口径）
        name = str(row.get("name", _DEFAULT_LAYER))
        store[_CURRENT_KEY] = name
        if _LEGACY_CURRENT_KEY in store:
            store[_LEGACY_CURRENT_KEY] = name
    return _sorted_uuids(targets)


def layer_off(target: Any, *, layer: Any = _UNSET, layers: Any = None, names: Any = None,
              off: bool = True) -> list[str]:
    """图层开/关（AutoCAD 图层面板的日/雪状态图标）→ **被写的层 uuid**，按层名排序。

    `off=True` 关层、`off=False` 开层；点名与不变量完全走 `layer_state_set`（当前层不能被关闭 ⇒
    `current_layer_off`；未知层 ⇒ `unknown_layer`；批量原子）。只改 `visible` 位，冻结/锁定不动。
    """
    return layer_state_set(target, layer=layer, layers=layers, names=names, off=off)


def layer_freeze(target: Any, *, layer: Any = _UNSET, layers: Any = None, names: Any = None,
                 frozen: bool = True) -> list[str]:
    """图层冻结/解冻 → **被写的层 uuid**，按层名排序。

    `frozen=True` 冻结、`frozen=False` 解冻；点名与不变量走 `layer_state_set`（当前层不能被冻结 ⇒
    `current_layer_frozen`）。只改 `frozen` 位（关/锁定不动）——关与冻结是**两个独立位**，
    解冻一层不等于把它打开（AutoCAD 同口径）。老表行没有 `frozen` 键时按"解冻"读。
    """
    return layer_state_set(target, layer=layer, layers=layers, names=names, frozen=frozen)


def current_layer_set(target: Any, *, layer: Any) -> list[str]:
    """切换当前层（CLAYER 同口径）→ `[layer_uuid]`。

    `layer` = 层名或 layer_uuid（大小写不敏感），**必须指向表里已存在的层**（未知 ⇒ `unknown_layer`）。
    若该层处于冻结态，切换时**自动解冻**（AutoCAD：把冻结层置为当前层会解冻它）；锁定层可以被置为
    当前层（锁定只挡编辑，不挡成为当前层）。当前层写入组织表 `current_layer` 键。
    """
    return layer_state_set(target, layer=layer, current=True)


def layer_state(target: Any, *, state: str) -> list[str]:
    """按状态列出层（图层面板/图标的数据源）→ **layer_uuid**，按层名排序。

    `state` ∈ `all` / `current` / `off` / `on` / `frozen` / `thawed` / `locked` / `unlocked`
    （大小写不敏感）；其它值 ⇒ `invalid_argument`。

    边界（**"没采"与"零"不同形**）：组织表缺席、或表里一行层记录都没有 ⇒
    `CadError(reason="no_layer_table")`，**不返回 `[]`** —— 空列表会让下游把"没有状态账"读成
    "没有任何层处于该状态"。`state="current"` 时若当前层名在表里没有对应行 ⇒ `unknown_layer`
    （层被删了却仍被指为当前层，是要修的状态，不是空集）。
    """
    wanted = str(state or "").strip().casefold()
    if wanted not in _STATES:
        _fail("invalid_argument", "unknown layer state", state=state, known=list(_STATES))
    store = _store(target, create=False)
    if store is None:
        _fail("no_layer_table", "organization table is absent: layer states were never recorded")
    rows = _rows(store, bootstrap=False)
    if not rows:
        _fail("no_layer_table", "organization table has no layer rows")
    if wanted == "all":
        hit = list(rows)
    elif wanted == "current":
        name = _current_name(store, rows)
        hit = [row for row in rows if str(row.get("name", "")).casefold() == name.casefold()]
        if not hit:
            _fail("unknown_layer", "the current layer has no row in the table", layer=name, known=_known(rows))
    elif wanted in ("off", "on"):
        hit = [row for row in rows if _state_of(row)["off"] == (wanted == "off")]
    elif wanted in ("frozen", "thawed"):
        hit = [row for row in rows if _state_of(row)["frozen"] == (wanted == "frozen")]
    else:
        hit = [row for row in rows if _state_of(row)["locked"] == (wanted == "locked")]
    return _sorted_uuids(hit)


def layer_visible_entities(target: Any, *, purpose: str = "display", layer: Any = _UNSET) -> list[str]:
    """按图层状态解析实体集（**本片为 frozen/off 补的消费者**）→ **实体 uuid，文档顺序**。

    `purpose="display"`：显示集 —— 关层与冻结层上的实体被排除（不可见 ⇒ 不可选）；
    `purpose="edit"`：可编辑集 —— 显示集再排除**锁定层**上的实体（锁定层仍显示、仍可选，只挡编辑）。
    没有 `select` 目的集：可选集恒等于显示集（见模块 docstring 的状态矩阵）。

    `layer=`（可选，层名或 uuid）把结果再收窄到该层；未知层 ⇒ `unknown_layer`。
    层名在组织表里查不到行的实体按缺省态（显示/解冻/未锁）解析 —— 层表缺席同理（那是"没有层说
    它要藏"，不是"没算"；需要区分"没有状态账"时用 `layer_state`，它会抛 `no_layer_table`）。

    边界：目标没有 `entities`/`dimensions` 组 ⇒ `unsupported_target`；实体缺 uuid ⇒ `invalid_entity`。
    本操作是**纯查询**：不建表、不自举层 `"0"`、不写任何字段。
    """
    purpose_key = str(purpose or "").strip().casefold()
    if purpose_key not in _PURPOSES:
        _fail("invalid_argument", "unknown purpose", purpose=purpose, known=list(_PURPOSES))
    store = _store(target, create=False)
    rows = [] if store is None else _rows(store, bootstrap=False)
    wanted_layer: str | None = None
    if layer is not _UNSET and layer is not None:
        if not rows:
            _fail("unknown_layer", "no layer rows: cannot resolve the layer filter",
                  layer=str(layer), known=[])
        wanted_layer = str(_find(rows, layer).get("name", _DEFAULT_LAYER))
    states = {str(row.get("name", "")).casefold(): _state_of(row) for row in rows}
    out: list[str] = []
    for entity in _entities(target):
        name = _entity_layer(entity)
        if wanted_layer is not None and name.casefold() != wanted_layer.casefold():
            continue
        flags = states.get(name.casefold(), _EMPTY_STATE)
        visible = not (flags["frozen"] or flags["off"])
        if not visible:
            continue  # 关与冻结：不显示、不可选，display 与 edit 都排除
        if purpose_key == "edit" and flags["locked"]:
            continue  # 锁定：仍显示可选，只是改不动
        out.append(_entity_uuid(entity))
    return out


#: 冻结接口之二：操作名 → 可调用。聚合器按本表把各片并成族级 OPERATIONS。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "layer_state_set": layer_state_set,
    "layer_off": layer_off,
    "layer_freeze": layer_freeze,
    "current_layer_set": current_layer_set,
    "layer_state": layer_state,
    "layer_visible_entities": layer_visible_entities,
}


# ======================================================================================
# 自检（python3 cadcore/ops/features/organization__w10.py）
# ======================================================================================


def _expect_error(call: Callable[[], Any], reason: str) -> bool:
    """阳性对照：这个调用必须以这个 reason 抛 CadError（类型与 reason 都要对，别的异常算失败）。"""
    try:
        call()
    except CadError as exc:
        return str(getattr(exc, "reason", "")) == reason
    except Exception:
        return False
    return False


def _fixture() -> dict[str, Any]:
    """夹具：一份 L1 草图映射（层表 3 行 + 4 实体 + 1 标注）。

    层记录是直接落的（等价于同族 `layer_create` 的产物）—— 本片不重写建层，夹具只提供"图层体系
    已建好"的输入状态；夹具自证见 `_selftest` 末尾两条（实体行与顶层键集合一个都没被改）。
    """
    return {
        "units": "mm",
        "entities": [
            {"uuid": "e-0", "type": "line", "layer": "0"},
            {"uuid": "e-a1", "type": "line", "layer": "A"},
            {"uuid": "e-a2", "type": "circle", "layer": "A", "radius_nm": 1000},
            {"uuid": "e-b1", "type": "line", "layer": "B"},
        ],
        "dimensions": [{"uuid": "d-a1", "type": "dimension", "layer": "A"}],
        "organization": {
            "schema": 1,
            "layers": [
                {"uuid": "L-0", "name": "0", "visible": True, "locked": False, "frozen": False},
                {"uuid": "L-A", "name": "A", "visible": True, "locked": False, "frozen": False},
                {"uuid": "L-B", "name": "B", "visible": True, "locked": True, "frozen": False},
            ],
            "blocks": [], "inserts": [], "groups": [], "xrefs": [],
        },
    }


def _selftest() -> tuple[int, list[str]]:
    """硬断言自检：返回 (通过条数, 失败标签列表)。"""
    ok = 0
    failed: list[str] = []

    def check(cond: Any, label: str) -> None:
        nonlocal ok
        if cond:
            ok += 1
        else:
            failed.append(label)

    sk = _fixture()
    entities_before = {e["uuid"]: dict(e) for key in ("entities", "dimensions") for e in sk[key]}
    keys_before = set(sk.keys())

    # --- 缺省态 -------------------------------------------------------------
    check(layer_state(sk, state="current") == ["L-0"], "default current layer is '0'")
    check(layer_state(sk, state="all") == ["L-0", "L-A", "L-B"], "state=all -> every layer uuid, sorted by name")
    check(layer_visible_entities(sk) == ["e-0", "e-a1", "e-a2", "e-b1", "d-a1"],
          "display set = every entity while all layers are on/thawed")
    check(layer_visible_entities(sk, purpose="edit") == ["e-0", "e-a1", "e-a2", "d-a1"],
          "locked layer B is displayed but not editable")

    # --- 关 / 开 ------------------------------------------------------------
    check(layer_off(sk, layer="A") == ["L-A"], "layer_off returns the affected layer uuid")
    check(layer_visible_entities(sk) == ["e-0", "e-b1"], "off layer A disappears from the display set")
    check(layer_state(sk, state="off") == ["L-A"], "state=off finds layer A")
    check(layer_state(sk, state="on") == ["L-0", "L-B"], "state=on excludes layer A")
    check(_expect_error(lambda: layer_off(sk, layer="0"), "current_layer_off"),
          "the current layer cannot be turned off (current_layer_off)")
    check(layer_off(sk, layer="A", off=False) == ["L-A"], "layer_off(off=False) turns A back on")
    check(layer_visible_entities(sk) == ["e-0", "e-a1", "e-a2", "e-b1", "d-a1"],
          "layer A entities come back after it is turned on")

    # --- 冻结 / 解冻（批 + 原子性）-----------------------------------------
    check(layer_freeze(sk, layers=["A", "B"]) == ["L-A", "L-B"], "batch freeze returns uuids sorted by name")
    check(layer_visible_entities(sk) == ["e-0"], "frozen layers A and B leave only layer 0 visible")
    check(layer_state(sk, state="frozen") == ["L-A", "L-B"], "state=frozen lists both frozen layers")
    check(_expect_error(lambda: layer_freeze(sk, layer="0"), "current_layer_frozen"),
          "the current layer cannot be frozen (current_layer_frozen)")
    check(_expect_error(lambda: layer_state_set(sk, layers=["A", "nope"], frozen=False), "unknown_layer"),
          "unknown layer in a batch is rejected")
    check(layer_state(sk, state="frozen") == ["L-A", "L-B"],
          "atomicity: the rejected batch wrote nothing (A/B stay frozen)")
    check(layer_freeze(sk, layer="B", frozen=False) == ["L-B"], "single-layer thaw writes the frozen bit")
    check(layer_visible_entities(sk, layer="B", purpose="display") == ["e-b1"],
          "thawed but locked layer B is visible again")

    # --- 当前层切换 ---------------------------------------------------------
    check(current_layer_set(sk, layer="A") == ["L-A"], "current_layer_set returns the layer uuid")
    check(layer_state(sk, state="current") == ["L-A"], "state=current follows the switch")
    check(layer_state(sk, state="frozen") == [], "switching to a frozen layer thaws it")
    check(layer_visible_entities(sk) == ["e-0", "e-a1", "e-a2", "e-b1", "d-a1"],
          "A is visible again right after it became current (auto-thaw)")
    check(_expect_error(lambda: layer_off(sk, layer="A"), "current_layer_off"),
          "the new current layer A cannot be turned off")
    check(layer_freeze(sk, layer="0") == ["L-0"], "layer '0' can be frozen once it is not current")
    check(_expect_error(lambda: layer_state_set(sk, layer="A", frozen=True, current=True), "invalid_argument"),
          "freeze + current in one call is rejected as contradictory")
    check(_expect_error(lambda: layer_state_set(sk, layers=["A", "B"], current=True), "invalid_argument"),
          "current switch takes exactly one layer")

    # --- 复合位 + 查询边界 --------------------------------------------------
    check(layer_state_set(sk, layer="B", off=True, locked=False) == ["L-B"],
          "composite call writes off + unlock on one layer")
    # 此刻 **层 "0" 已在上一条被冻结**（:656）⇒ e-0 既不显示也不可编辑；B 关层 ⇒ e-b1 同样出局。
    # 旧期望把 e-0 留在集合里（漏算了那次冻结）⇒ 这条自检**长期恒红**（实测 HEAD 同红）；
    # 按模块文档口径（"关层与冻结层上的实体被排除"）与真实现修正期望，不是放宽判据。
    check(layer_visible_entities(sk, purpose="edit") == ["e-a1", "e-a2", "d-a1"],
          "B is off -> excluded from the edit set as well (layer '0' is frozen since :656)")
    check(layer_visible_entities(sk, layer="B") == [], "layer filter narrows to B, which is off")
    check(_expect_error(lambda: layer_visible_entities(sk, layer="ghost"), "unknown_layer"),
          "unknown layer filter is rejected")
    check(_expect_error(lambda: layer_state(sk, state="nope"), "invalid_argument"),
          "unknown state name is rejected")
    check(_expect_error(lambda: layer_state({"entities": []}, state="off"), "no_layer_table"),
          "an absent layer table raises no_layer_table instead of returning []")
    check(layer_visible_entities({"entities": [{"uuid": "x", "type": "line"}]}) == ["x"],
          "absent layer table keeps the documented default state (visible)")
    check(layer_visible_entities({"entities": [{"uuid": "x", "type": "line", "layer": None}]}) == ["x"],
          "missing/null entity layer falls back to layer '0'")
    check(_expect_error(lambda: layer_visible_entities({"units": "mm"}), "unsupported_target"),
          "a target with no entity groups is an explicit unsupported_target")

    # --- 夹具自证（E2.5：证明夹具没被污染 & 尺子看得见红）------------------
    entity_after = {e["uuid"]: dict(e) for key in ("entities", "dimensions") for e in sk[key]}
    check(entity_after == entities_before, "fixture self-check: no entity row was modified")
    check(set(sk.keys()) == keys_before, "fixture self-check: no top-level key was added")
    check(set(sk["organization"].keys()) == {"schema", "layers", "blocks", "inserts", "groups",
                                             "xrefs", "current_layer"},
          "fixture self-check: the table only gained the current_layer key")
    check(json.dumps(sk["organization"]["layers"], sort_keys=True).count('"frozen"') == 3,
          "fixture self-check: every layer row carries the frozen bit")
    check(not _expect_error(lambda: layer_state(sk, state="current"), "unknown_layer"),
          "negative control: a healthy call is not reported as an error")
    return ok, failed


def main(argv: list[str] | None = None) -> int:
    """自检入口：判定行落 stdout；返回 0=PASS / 1=FAIL（失败必须看得见，不许静默）。"""
    _ = list(sys.argv[1:] if argv is None else argv)
    ok, failed = _selftest()
    if failed:
        print("SELFTEST family=organization shard=w10 assertions=%d failed=%d verdict=FAIL" % (ok, len(failed)))
        for label in failed:
            print("SELFTEST_FAILED check=%s" % label)
        print("FEATURE_FAIL family=organization op=layer_state_set verdict=FAIL")
        return 1
    print("SELFTEST family=organization shard=w10 assertions=%d failed=0 verdict=PASS" % ok)
    print("FEATURE_OK family=organization op=layer_state_set verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
READONLY = True
OPERATIONS_META = {
    "layer_visible_entities": {"tier": "query", "readonly": True, "returns": "data"},
}
