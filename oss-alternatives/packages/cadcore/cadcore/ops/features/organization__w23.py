#!/usr/bin/env python3
"""cadcore.ops.features.organization__w23 —— 组织族第 23 号切片（w23）。

本片负责哪一条（编号口径，照族清单算）
--------------------------------------
族 = `autocad-organization`（文档 `docs/research/cad-parity/features/autocad-organization.json`，
状态 `.../status/autocad-organization.json`）。把**仍 missing/partial 的 must_have** 按清单顺序编号 0..N-1：

    0  图层创建与图层特性管理（颜色/线型/线宽/打印）      partial
    1  图层开/关、冻结/解冻、锁定/解锁与当前图层切换        partial
    2  对象图层指定与改层（特性面板、特性匹配）            partial
    3  无用图层清理、图层合并/删除                        partial
    4  动态块与块编辑器                                   missing
    5  块属性编辑与批量改属性                             partial
    6  属性提取成表或文件（门窗表/设备表）                 missing
    7  写块（导出为独立 dwg）                             missing
    8  外部参照附着与覆盖（附加/覆盖两种方式）             partial
    9  参照状态管理：卸载/重载/拆离/绑定                   partial
    10 参照路径与相对路径管理                             partial
    11 在位编辑参照（REFEDIT/XOPEN）                      missing
    12 图纸集与图纸列表管理（DST 数据文件）                missing
    13 图纸集自定义特性与字段联动（图号/图名/日期）         partial

N = 14（`implemented` 的 7 条不编号：#4 隔离、#5 过滤器、#7 块定义、#8 块插入、#10 属性定义、
#13 块分解、#15 编组）。指名第 **14** 号 ⇒ 越界 ⇒ 顺延取模 ``14 % 14 = 0`` ⇒ 本片实现 **第 0 号
「图层创建与图层特性管理（颜色/线型/线宽/打印）」**。

做的是什么（真实现，不是桩）
----------------------------
在 L1 文档/草图模型上建**可写的图层表**（`doc.extra["organization"].layers`，草图形态落
`sk["organization"].layers`，与 `cadcore.ops.organization` 同键同 schema，行结构向后兼容地**只加键**）：

- ``layer_create``：按名建层，一次带上颜色（ACI 1..255 或 ``"#RRGGBB"``）/线型/线宽（整数纳米）/
  打印开关/可见性/打印样式名/说明；重名、坏颜色、坏线宽、非 bool 开关一律当场报错。
- ``layer_set``：**部分更新**（未给的属性一个都不动）；`color=None` 是"显式改回随层"，与"没给"分开。
- ``layer_rename``：真级联 —— 把该层名下**每个实体的 `layer` 字段**改写成新名（DXF 组码 8 的语义），
  这是"改层名"在 L1 上的真实数据后果，不是只动一行账。
- ``layer_apply``：把图层特性**按 ByLayer 解析后落到该层实体字段**（`color` / `linetype` /
  `lineweight_nm` / `printable`）。现状是渲染与导出只认实体、不认图层表（见族状态：SVG 渲染只按层分组、
  颜色取自固定映射），所以这一步才是"按层指定颜色/线宽"真正生效的落点；缺省 ``override=False``
  ——**实体已有的显式值优先**（AutoCAD 的 ByLayer 优先级），要强推用 ``override=True``。

同时给出非操作（不进 `OPERATIONS`）的机读产物 ``layer_dxf_rows``：按 DXF LAYER 表的组码形态导出行
（``62`` 颜色，**图层关闭时为负**；``6`` 线型；``370`` 线宽，仅显式设置时出现；``290`` 打印开关
—— 族状态里点名的"打印特性组码 290 全无"就补在这里；真彩再补 ``420``）。本层**不做文件 I/O**：
写出 DXF 由 io 层消费这张行表。

有意限定的边界（写下来，防止下游靠猜）
--------------------------------------
1. **不做文件 I/O**：本模块只维护 L1 上的表与字段，不读也不写 DWG/DXF/STB。
2. **打印样式表不解析**：``plot_style`` 只作为**名字数据**随层携带并出行（组码 390 属 io 层的活），
   命名打印样式表（STB/CTB）的解析与应用**未实现**，别把"存了名字"当成"打印样式生效"。
3. **线宽不做标准值吸附**：按整数纳米原样存；AutoCAD 的 24 档离散线宽属 UI 约束，这里不替它决定。
4. 非 docstring 的字符串字面量一律 ASCII：面向用户文案由语言层按 key 取词；错误一律
   ``CadError(reason=...)``，reason 是下游唯一归因入口。

冻结接口：``FAMILY = "organization"``、``OPERATIONS: dict[str, Callable]``，
每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``（返回受影响 uuid；每个操作 docstring 写清顺序）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _PKG_ROOT not in sys.path:  # 直接 `python3 cadcore/ops/features/<file>.py` 时也能 import 本包
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError  # noqa: E402

try:  # 复用全仓 uuid 发生器；缺席时退回标准库（不静默换成计数器）
    from cadcore.sketch.constraints import new_uuid as _new_uuid
except Exception:  # pragma: no cover - 仅在包外裸跑时触发
    import uuid as _uuid_mod

    def _new_uuid() -> str:
        return _uuid_mod.uuid4().hex


FAMILY = "organization"

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "layer_apply",
    "layer_create",
    "layer_dxf_rows",
    "layer_rename",
    "layer_rows",
    "layer_set",
    "selftest",
]

_ORG_KEY = "organization"
_ORG_SCHEMA = 1
_DEFAULT_LAYER = "0"
_MAX_LINEWEIGHT_NM = 5_000_000  # 5 mm 上限，与全仓口径一致
_MISSING = object()  # "没给这个参数"，与"显式给了 None（随层）"严格区分
_RESERVED_NAME_CHARS = '<>/\\":;?*|=`,'
_LINETYPE_EXTRA_CHARS = "_-$ "
_ROW_DEFAULTS = {
    "color": None,
    "linetype": "continuous",
    "lineweight_nm": -1,
    "visible": True,
    "locked": False,
    "printable": True,
    "plot_style": "",
    "description": "",
}


# ======================================================================================
# 结构访问（本文件自带一份最小的 _get/_set，不依赖别的席的在途文件）
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """失败信号：reason 机器可读，message 保持 ASCII。"""
    return CadError(message, reason=reason, **details)


def _get(obj: Any, name: str, default: Any = _MISSING) -> Any:
    """字段读取：映射看键、对象看属性；`default` 为哨兵时缺失即报错（禁静默 None）。"""
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
    elif hasattr(obj, name):
        return getattr(obj, name)
    if default is _MISSING:
        raise _fail("bad_target", "target has no such field", field=name, value_type=type(obj).__name__)
    return default


def _set(obj: Any, name: str, value: Any) -> None:
    """字段写入：映射写键、对象写属性；写不进去报 `bad_target`（不静默丢弃）。"""
    if isinstance(obj, MutableMapping):
        obj[name] = value
        return
    try:
        setattr(obj, name, value)
    except Exception as exc:  # noqa: BLE001 - 统一转成可归因的 CadError
        raise _fail("bad_target", "target field is not writable", field=name,
                    value_type=type(obj).__name__, error=type(exc).__name__) from exc


def _sketches(target: Any) -> list[Any] | None:
    """文档形态的草图列表；草图形态返回 None。"""
    value = _get(target, "sketches", None)
    return value if isinstance(value, list) else None


def _entities_of(sketch: Any) -> list[Any]:
    """草图实体列表（缺 `entities` 报 `bad_target`，不假装空图）。"""
    value = _get(sketch, "entities", None)
    if not isinstance(value, list):
        raise _fail("bad_target", "sketch has no entities list", value_type=type(sketch).__name__)
    return value


def _entity_groups(target: Any) -> list[list[Any]]:
    """`sk_or_doc` → 每个草图一份实体列表（文档多草图时全部计入）。"""
    groups = _sketches(target)
    if groups is None:
        return [_entities_of(target)]
    return [_entities_of(sketch) for sketch in groups]


def _store(target: Any, *, create: bool) -> dict[str, Any] | None:
    """组织表：文档在 `extra`，草图在自身；与 `ops.organization` 同键同 schema。"""
    holder = target
    if _sketches(target) is not None:
        extra = _get(target, "extra", None)
        if isinstance(extra, MutableMapping):
            holder = extra
        elif not isinstance(target, MutableMapping):
            raise _fail("bad_target", "document target has no mutable extra container",
                        value_type=type(target).__name__)
    value = _get(holder, _ORG_KEY, None)
    if isinstance(value, MutableMapping):
        return dict.__len__ and value  # type: ignore[return-value]
    if not create:
        return None
    value = {"schema": int(_ORG_SCHEMA), "layers": [], "blocks": [], "inserts": [],
             "groups": [], "xrefs": []}
    _set(holder, _ORG_KEY, value)
    return value


def _layers(store: MutableMapping, *, bootstrap: bool = True) -> list[Any]:
    """图层表；写路径首次触达时空表自举缺省层 `"0"`（只自举一次，查询路径不自举）。"""
    rows = store.get("layers")
    if rows is None:
        rows = []
        store["layers"] = rows
    if not isinstance(rows, list):
        raise _fail("corrupt_store", "organization.layers is not a list",
                    value_type=type(rows).__name__)
    if bootstrap and not rows:
        rows.append(_default_row(_DEFAULT_LAYER))
    return rows


def _default_row(name: str) -> dict[str, Any]:
    """缺省层行：只加键不改键，向后兼容 `ops.organization` 的历史行。"""
    row = {"uuid": str(_new_uuid()), "name": str(name)}
    row.update(dict(_ROW_DEFAULTS))
    return row


def _normalize_row(row: Any, index: int) -> dict[str, Any]:
    """历史行补缺键（不改已有键、不删未知键）；类型坏了报 `corrupt_store`。"""
    if not isinstance(row, MutableMapping):
        raise _fail("corrupt_store", "layer row is not a mapping", index=index,
                    value_type=type(row).__name__)
    for key, value in _ROW_DEFAULTS.items():
        row.setdefault(key, value)
    if not isinstance(row.get("name"), str) or not row.get("name"):
        raise _fail("corrupt_store", "layer row has no usable name", index=index)
    if not isinstance(row.get("uuid"), str) or not row.get("uuid"):
        row["uuid"] = str(_new_uuid())
    return row


def _layer_rows(store: MutableMapping, *, bootstrap: bool) -> list[dict[str, Any]]:
    """规范化后的图层行列表（读侧也做一次补键，但不写回假值）。"""
    rows = _layers(dict(store), bootstrap=bootstrap) if False else _layers(store, bootstrap=bootstrap)
    return [_normalize_row(row, index) for index, row in enumerate(rows)]


def _find(store: MutableMapping, ref: Any) -> dict[str, Any]:
    """按 uuid 或**大小写不敏感**的层名找行；找不到报 `unknown_layer`，同名报 `ambiguous_layer`。"""
    text = _name(ref, what="layer")
    rows = _layer_rows(store, bootstrap=True)
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


def _uuid_of(entity: Any) -> str:
    """实体 uuid；读不出来报 `geometry_invalid`（禁静默跳过）。"""
    value = _get(entity, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("geometry_invalid", "entity has no usable uuid")
    return value


# ======================================================================================
# 入参把关
# ======================================================================================


def _name(value: Any, *, what: str = "name") -> str:
    """图层名：1..255 字符、不含保留字符、控制字符一律拒收。"""
    if not isinstance(value, str):
        raise _fail("invalid_argument", "name must be a string", field=what,
                    value_type=type(value).__name__)
    text = value.strip()
    if not text or len(text) > 255:
        raise _fail("invalid_layer_name", "layer name must be 1..255 characters", field=what,
                    length=len(text))
    if any(ch in _RESERVED_NAME_CHARS for ch in text) or any(ord(ch) < 32 for ch in text):
        raise _fail("invalid_layer_name", "layer name contains reserved characters", field=what)
    return text


def _color(value: Any) -> Any:
    """颜色：None（随层）/ ACI int 1..255 / `"#RRGGBB"`（规范成大写）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _fail("invalid_color", "color must not be a bool", value=repr(value))
    if isinstance(value, int):
        if not 1 <= value <= 255:
            raise _fail("invalid_color", "ACI color must be 1..255", value=int(value))
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 7 and text[0] == "#":
            try:
                rgb = int(text[1:], 16)
            except ValueError as exc:
                raise _fail("invalid_color", "true color must be #RRGGBB", value=text) from exc
            return "#%06X" % rgb
    raise _fail("invalid_color", "color must be None, ACI 1..255 or #RRGGBB", value=repr(value))


def _linetype(value: Any) -> str:
    """线型名：1..31 字符，字符集 [A-Za-z0-9_-$ ]（不查线型定义表 —— 那是 io 层的事）。"""
    if not isinstance(value, str):
        raise _fail("invalid_linetype", "linetype must be a string", value=repr(value))
    text = value.strip()
    if not text or len(text) > 31:
        raise _fail("invalid_linetype", "linetype name must be 1..31 characters", value=text)
    if not all(ch.isalnum() or ch in _LINETYPE_EXTRA_CHARS for ch in text):
        raise _fail("invalid_linetype", "linetype name has characters outside [A-Za-z0-9_-$ ]",
                    value=text)
    return text


def _lineweight(value: Any) -> int:
    """线宽：整数纳米；-1 = 未显式设置；0..5mm 之内。bool 与浮点一律拒收。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_lineweight", "lineweight_nm must be an int (nm)", value=repr(value))
    if value == -1:
        return -1
    if value < 0 or value > _MAX_LINEWEIGHT_NM:
        raise _fail("invalid_lineweight", "lineweight_nm must be -1 or 0..5000000",
                    value=int(value))
    return int(value)


def _flag(value: Any, what: str) -> bool:
    """开关：严格 bool（0/1 冒充一律拒收 —— 免得"看起来设了"）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", "flag must be a bool", field=what, value=repr(value))
    return bool(value)


def _note(value: Any, what: str, *, limit: int = 255) -> str:
    """自由文本字段（打印样式名/说明）：None → 空串，超长截断到 limit。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail("invalid_argument", "text field must be a string", field=what,
                    value_type=type(value).__name__)
    return value.strip()[:limit]


# ======================================================================================
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]）
# ======================================================================================


def layer_create(sk_or_doc: Any, name: Any, *, color: Any = None, linetype: Any = "continuous",
                 lineweight_nm: Any = -1, printable: Any = True, visible: Any = True,
                 plot_style: Any = "", description: Any = "") -> list[str]:
    """建层并一次带上全部特性。

    返回 ``[layer_uuid]``（只返回新建层自己的 uuid；本操作不碰任何实体）。
    错误：重名 ``duplicate_layer``、名字非法 ``invalid_layer_name``、颜色 ``invalid_color``、
    线型 ``invalid_linetype``、线宽 ``invalid_lineweight``、开关非 bool ``invalid_argument``。
    """
    text = _name(name)
    spec = {
        "color": _color(color),
        "linetype": _linetype(linetype),
        "lineweight_nm": _lineweight(lineweight_nm),
        "printable": _flag(printable, "printable"),
        "visible": _flag(visible, "visible"),
        "plot_style": _note(plot_style, "plot_style"),
        "description": _note(description, "description"),
    }
    store = _store(sk_or_doc, create=True)
    assert store is not None  # create=True 不可能返回 None
    rows = _layer_rows(store, bootstrap=True)
    key = text.casefold()
    for row in rows:
        if str(row.get("name", "")).casefold() == key:
            raise _fail("duplicate_layer", "layer name already exists", layer=text)
    row = _default_row(text)
    row.update(spec)
    _layers(store, bootstrap=True).append(row)
    return [str(row["uuid"])]


def layer_set(sk_or_doc: Any, layer: Any, *, color: Any = _MISSING, linetype: Any = _MISSING,
              lineweight_nm: Any = _MISSING, printable: Any = _MISSING, visible: Any = _MISSING,
              plot_style: Any = _MISSING, description: Any = _MISSING) -> list[str]:
    """**部分**更新图层特性：没给的参数一个都不动（``color=None`` = 显式改回随层）。

    返回 ``[layer_uuid]``。一个属性都没给 ⇒ ``invalid_argument``（拒绝静默空操作）。
    """
    spec: dict[str, Any] = {}
    if color is not _MISSING:
        spec["color"] = _color(color)
    if linetype is not _MISSING:
        spec["linetype"] = _linetype(linetype)
    if lineweight_nm is not _MISSING:
        spec["lineweight_nm"] = _lineweight(lineweight_nm)
    if printable is not _MISSING:
        spec["printable"] = _flag(printable, "printable")
    if visible is not _MISSING:
        spec["visible"] = _flag(visible, "visible")
    if plot_style is not _MISSING:
        spec["plot_style"] = _note(plot_style, "plot_style")
    if description is not _MISSING:
        spec["description"] = _note(description, "description")
    if not spec:
        raise _fail("invalid_argument", "layer_set needs at least one property")
    store = _store(sk_or_doc, create=True)
    assert store is not None
    row = _find(store, layer)
    row.update(spec)
    return [str(row["uuid"])]


def layer_rename(sk_or_doc: Any, layer: Any, name: Any) -> list[str]:
    """改层名，并把该层名下**每个实体的 `layer` 字段**级联改成新名（大小写不敏感匹配）。

    返回 ``[layer_uuid, *被改写实体的 uuid]``（实体按草图表/实体表顺序，保序去重）。
    错误：新名非法 ``invalid_layer_name``、与其他层重名 ``duplicate_layer``、找不到层 ``unknown_layer``。
    """
    new_name = _name(name)
    store = _store(sk_or_doc, create=True)
    assert store is not None
    row = _find(store, layer)
    key = new_name.casefold()
    for other in _layer_rows(store, bootstrap=True):
        if other is row:
            continue
        if str(other.get("name", "")).casefold() == key:
            raise _fail("duplicate_layer", "layer name already exists", layer=new_name)
    old_name = str(row.get("name", ""))
    old_key = old_name.casefold()
    row["name"] = new_name

    touched: list[str] = []
    seen: set[str] = set()
    for group in _entity_groups(sk_or_doc):
        for entity in group:
            current = _get(entity, "layer", _DEFAULT_LAYER)
            if not isinstance(current, str) or current.casefold() != old_key:
                continue
            if current != new_name:
                _set(entity, "layer", new_name)
            ref = _uuid_of(entity)
            if ref not in seen:
                seen.add(ref)
                touched.append(ref)
    return [str(row["uuid"]), *touched]


def layer_apply(sk_or_doc: Any, layer: Any, *, override: Any = False) -> list[str]:
    """把该层的特性**落到该层每个实体的字段上**（ByLayer 解析的物化）。

    写 ``color`` / ``linetype`` / ``lineweight_nm`` / ``printable`` 四个实体键；返回被物化的实体 uuid
    （实体表顺序，保序去重）。``override=False``（缺省）时**实体已有的显式值优先** —— 只补空缺；
    ``override=True`` 时按图层值强推全部四个键。返回空列表 = 该层上没有实体（不是错误）。
    """
    force = _flag(override, "override")
    store = _store(sk_or_doc, create=True)
    assert store is not None
    row = _find(store, layer)
    key = str(row.get("name", "")).casefold()
    color = row.get("color")
    linetype = row.get("linetype") or "continuous"
    lineweight = int(row.get("lineweight_nm", -1))
    printable = bool(row.get("printable", True))

    touched: list[str] = []
    seen: set[str] = set()
    for group in _entity_groups(sk_or_doc):
        for entity in group:
            current = _get(entity, "layer", _DEFAULT_LAYER)
            if not isinstance(current, str) or current.casefold() != key:
                continue
            if color is not None and (force or _get(entity, "color", None) is None):
                _set(entity, "color", color)
            if force or _get(entity, "linetype", None) is None:
                _set(entity, "linetype", linetype)
            if force or _get(entity, "lineweight_nm", None) is None:
                _set(entity, "lineweight_nm", lineweight)
            if force or _get(entity, "printable", None) is None:
                _set(entity, "printable", printable)
            ref = _uuid_of(entity)
            if ref not in seen:
                seen.add(ref)
                touched.append(ref)
    return touched


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "layer_create": layer_create,
    "layer_set": layer_set,
    "layer_rename": layer_rename,
    "layer_apply": layer_apply,
}


# ======================================================================================
# 只读产物（不进 OPERATIONS：给 UI/导出层的机读视图与 DXF LAYER 行）
# ======================================================================================


def layer_rows(sk_or_doc: Any) -> list[dict[str, Any]]:
    """图层行副本（含补缺键后的字段；纯查询，不建表、不自举）。"""
    store = _store(sk_or_doc, create=False)
    if store is None:
        return []
    return [dict(row) for row in _layer_rows(store, bootstrap=False)]


def layer_dxf_rows(sk_or_doc: Any) -> list[dict[str, Any]]:
    """按 DXF LAYER 表组码形态导出行（io 层直接消费；本层不写文件）。

    - ``code_62``：ACI 颜色；**图层关闭时取负**（DXF 语义）；未显式设色按 7。
    - ``code_6``：线型名；``code_370``：线宽纳米（**仅显式设置时出现**，-1 不出行）。
    - ``code_290``：打印开关（1/0）—— 族状态点名的缺失项就补在这里。
    - ``code_420``：真彩（``"#RRGGBB"`` 时出现）。
    """
    out: list[dict[str, Any]] = []
    for row in layer_rows(sk_or_doc):
        color = row.get("color")
        aci = int(color) if isinstance(color, int) and not isinstance(color, bool) else 7
        visible = bool(row.get("visible", True))
        if not visible:
            aci = -abs(aci)
        entry: dict[str, Any] = {
            "name": str(row.get("name", "")),
            "code_62": aci,
            "code_6": str(row.get("linetype") or "continuous"),
            "code_290": 1 if bool(row.get("printable", True)) else 0,
            "visible": visible,
        }
        lineweight = row.get("lineweight_nm", -1)
        if isinstance(lineweight, int) and not isinstance(lineweight, bool) and lineweight >= 0:
            entry["code_370"] = int(lineweight)
        if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
            entry["code_420"] = int(color[1:], 16)
        out.append(entry)
    return out


# ======================================================================================
# 自测（≥2 条硬断言；返回码 0 = 全绿）
# ======================================================================================


def _fixture() -> dict[str, Any]:
    """最小 L1 文档形态夹具：两个草图外的实体挂 `0` 层，第三个实体预置显式颜色。"""
    return {
        "extra": {},
        "sketches": [
            {
                "uuid": "sk-1",
                "entities": [
                    {"uuid": "e1", "kind": "line", "layer": "0",
                     "geom": {"start_nm": [0, 0], "end_nm": [1000, 0]}},
                    {"uuid": "e2", "kind": "circle", "layer": "0",
                     "geom": {"center_nm": [0, 0], "radius_nm": 500}},
                    {"uuid": "e3", "kind": "line", "layer": "WALL", "color": 5,
                     "geom": {"start_nm": [0, 0], "end_nm": [0, 1000]}},
                ],
                "constraints": [],
                "dimensions": [],
            }
        ],
    }


def _expect_reason(fn: Callable[[], Any], reason: str) -> bool:
    """调用必须抛 CadError 且 reason 命中（顺带证明错误面是机器可读的）。"""
    try:
        fn()
    except CadError as exc:
        return exc.reason == reason
    except Exception:  # noqa: BLE001 - 非 CadError 一律算不合格
        return False
    return False


def selftest() -> int:
    """本片自测：建层/改特性/级联改名/ByLayer 物化/DXF 行/错误面，共 12 条断言。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    doc = _fixture()
    geom_before = dict(doc["sketches"][0]["entities"][0]["geom"])

    # 1) 建层：一次带全部特性，返回自身 uuid
    created = layer_create(doc, "WALL", color=1, linetype="dashed", lineweight_nm=350000,
                           printable=False, visible=True, plot_style="monochrome")
    check("create_returns_uuid", len(created) == 1 and isinstance(created[0], str), repr(created))
    check("create_row_fields",
          layer_rows(doc)[1]["name"] == "WALL" and layer_rows(doc)[1]["color"] == 1
          and layer_rows(doc)[1]["linetype"] == "dashed"
          and layer_rows(doc)[1]["lineweight_nm"] == 350000
          and layer_rows(doc)[1]["printable"] is False
          and layer_rows(doc)[1]["plot_style"] == "monochrome", repr(layer_rows(doc)))
    check("default_layer_bootstrapped", layer_rows(doc)[0]["name"] == "0", repr(layer_rows(doc)))

    # 2) 错误面：重名/坏颜色/坏线宽/非 bool 开关/空更新/未知层
    check("dup_layer_rejected", _expect_reason(lambda: layer_create(doc, "wall"), "duplicate_layer"))
    check("bad_color_rejected", _expect_reason(lambda: layer_create(doc, "C1", color=0), "invalid_color"))
    check("bad_lineweight_rejected",
          _expect_reason(lambda: layer_create(doc, "C2", lineweight_nm=350.0), "invalid_lineweight"))
    check("non_bool_flag_rejected",
          _expect_reason(lambda: layer_create(doc, "C3", printable=1), "invalid_argument"))
    check("empty_update_rejected", _expect_reason(lambda: layer_set(doc, "WALL"), "invalid_argument"))
    check("unknown_layer_rejected", _expect_reason(lambda: layer_set(doc, "NOPE", color=3), "unknown_layer"))
    check("created_and_rejected_isolated",
          sorted(row["name"] for row in layer_rows(doc)) == ["0", "WALL"], repr(layer_rows(doc)))

    # 3) 改特性：真彩 + 关闭层的 DXF 组码（62 取负、290 打印开关、420 真彩）
    layer_set(doc, "WALL", color="#00FF00", visible=False, printable=False)
    rows = layer_dxf_rows(doc)
    wall = [row for row in rows if row["name"] == "WALL"][0]
    check("dxf_rows_truecolor", wall.get("code_420") == 0x00FF00 and wall["code_62"] == -7, repr(wall))
    check("dxf_rows_print_flag", wall["code_290"] == 0 and wall["code_6"] == "dashed"
          and wall["code_370"] == 350000, repr(wall))

    # 4) 级联改名：实体 `layer` 字段真被改写（真数据后果）
    renamed = layer_rename(doc, "WALL", "A-WALL")
    check("rename_cascades_entities",
          renamed[0] == created[0] and renamed[1:] == ["e3"]
          and doc["sketches"][0]["entities"][2]["layer"] == "A-WALL", repr(renamed))
    check("rename_updates_layer_row", layer_rows(doc)[1]["name"] == "A-WALL", repr(layer_rows(doc)))

    # 5) ByLayer 物化：显式值优先；override=True 才强推
    applied = layer_apply(doc, "A-WALL")
    e3 = doc["sketches"][0]["entities"][2]
    check("apply_layers_on_entities", applied == ["e3"] and e3["color"] == 5
          and e3["linetype"] == "dashed" and e3["lineweight_nm"] == 350000
          and e3["printable"] is False, repr(e3))
    layer_apply(doc, "A-WALL", override=True)
    check("apply_override_forces_layer_values", e3["color"] == "#00FF00", repr(e3))

    # 6) 几何与实体数一个纳米都没动；缺省层实体不受影响
    entities = doc["sketches"][0]["entities"]
    check("geometry_untouched",
          len(entities) == 3 and entities[0]["geom"] == geom_before
          and entities[0]["layer"] == "0" and "color" not in entities[0], repr(entities[0]))

    # 7) 草图形态目标（无 sketches）同样可用
    sketch = {"uuid": "sk-2", "entities": [{"uuid": "s1", "kind": "line", "layer": "0",
                                            "geom": {"start_nm": [0, 0], "end_nm": [1, 0]}}]}
    layer_create(sketch, "TEXT", color=3)
    layer_rename(sketch, "0", "BASE")
    check("sketch_form_target_works",
          sketch["organization"]["layers"][1]["name"] == "TEXT"
          and sketch["entities"][0]["layer"] == "BASE", repr(sketch.get("organization")))

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print("check=%-32s ok=%d%s" % (name, 1 if ok else 0, "" if ok else " detail=" + detail))
    print("SELFTEST family=%s ops=%s checks=%d failed=%d"
          % (FAMILY, ",".join(sorted(OPERATIONS)), len(checks), len(failed)))
    if failed:
        print("FEATURE_FAIL family=%s op=layer_create verdict=FAIL failed=%s"
              % (FAMILY, ",".join(failed)))
        return 1
    print("FEATURE_OK family=%s op=layer_create verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
