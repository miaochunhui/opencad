#!/usr/bin/env python3
"""cadcore.ops.features.organization__w9 —— organization 族实现片（席位 w9）。

本片负责哪一条（口径先写清）
--------------------------
真源：`docs/research/cad-parity/features/autocad-organization.json` 的 `must_have`（21 条）
逐条对照 `docs/research/cad-parity/status/autocad-organization.json` 的 `items[i].status`，
把**仍 missing/partial** 的条目按清单顺序编号 0..N-1（N=14；implemented 的 7 条不计入）：

  0 图层创建与图层特性管理（颜色/线型/线宽/打印）   <-- 本片
  1 图层开/关、冻结/解冻、锁定/解锁与当前图层切换
  2 对象图层指定与改层（特性面板、特性匹配）
  3 无用图层清理、图层合并/删除
  4 动态块与块编辑器
  5 块属性编辑与批量改属性
  6 属性提取成表或文件（门窗表/设备表）
  7 写块（导出为独立 dwg）
  8 外部参照附着与覆盖（附加/覆盖两种方式）
  9 参照状态管理：卸载/重载/拆离/绑定
 10 参照路径与相对路径管理
 11 在位编辑参照（REFEDIT/XOPEN）
 12 图纸集与图纸列表管理（DST 数据文件）
 13 图纸集自定义特性与字段联动（图号/图名/日期）

歧义登记（不藏）：席位名是 w9，但任务书正文明确写「你负责**第 0 号**那一条」，本片按**正文取 0 号**
（图层创建与图层特性管理）；末行 verdict 显式登记 `item=0`。若上游实际按 `w9 -> 第 9 号`
（参照状态管理）分派，请复用本文件的骨架换 OPERATIONS，别把这个 0 号实现当成 9 号交付。

本片实现的能力（真数据后果，L1 装得下 ⇒ 不留 missing）
----------------------------------------------------
状态档缺的是三件：**打印特性（plot/plotstyle，DXF 组码 290 对应物）全无**、颜色只能取固定映射、
没有用户侧建层/改名入口。本片把「图层特性表」做成 L1 文档上的**真表**并给出写路径：

- 表落点与 `cadcore.ops.organization` 同键同形（只加键不改键）：文档形态落
  `doc.extra["organization"]["layers"]`，草图形态落 `sk["organization"]["layers"]`；
- `layer_create`：真建行，写 color（None=ByLayer / ACI 1..255 / "#RRGGBB"）、linetype、
  lineweight_nm（整数纳米；-1=未采、0=真为零）、**plot**、**plotstyle**、description；重名报错；
- `layer_props_set`：改任一子集（含 plot/plotstyle）；未知层报 `unknown_layer`；
- `layer_rename`：改层名并**同步改写全部用该层的实体的 `layer` 字段**（真数据后果，不是只改表）；
  层 `"0"` 拒改（`protected_layer`），撞名报 `duplicate_name`；
- `layer_entities`：现算某层上的实体 uuid（可按 print 状态过滤），供下游核对。

L1 持久性是硬证据：`document_to_l1` → `document_from_l1` 往返后，图层行的 plot/plotstyle 等字段仍在
（自测里是真断言，不是说明文字）。

仍缺（如实留 missing，且**不在本片文件可写范围内**）
--------------------------------------------------
- DXF 导出侧的打印特性组码 290 写出行：属 io 层 `cadcore.io.dxf_bridge`，归别席；
- Web/服务端图层管理路由与特性面板：属 `apps/opencad`，归别席。

口径：长度一律整数纳米；错误一律 `CadError(reason=...)`；非 docstring 字符串字面量一律 ASCII。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, Callable

from cadcore.errors import CadError

try:  # 复用仓内既有实现（结构访问/取 uuid），不抄第二份
    from cadcore.sketch.constraints import new_uuid as _repo_new_uuid
    from cadcore.sketch.constraints import sketch_entities as _repo_sketch_entities
except Exception:  # pragma: no cover - 兜底，保持本片可独立执行
    _repo_new_uuid = None
    _repo_sketch_entities = None

FAMILY = "organization"

__all__ = ["FAMILY", "OPERATIONS"]

_STORE_KEY = "organization"
_LAYERS_KEY = "layers"
_ORG_SCHEMA = 1
_DEFAULT_LAYER = "0"
_MAX_NAME = 255
_MAX_DESCRIPTION = 1024
_INVALID_NAME_CHARS = frozenset('<>/\\":;?*|=,`')
_HEX = frozenset("0123456789abcdefABCDEF")
_UNSET = object()


# --------------------------------------------------------------------------------------
# 入参/结构helpers
# --------------------------------------------------------------------------------------


def _fail(reason: str, detail: str, **details: Any) -> CadError:
    """所有可预期错误走这里：机器可读 reason，文案保持 ASCII。"""
    return CadError(detail, reason=reason, **details)


def _get(holder: Any, key: str, default: Any = None) -> Any:
    if isinstance(holder, Mapping):
        return holder.get(key, default)
    return getattr(holder, key, default)


def _set(holder: Any, key: str, value: Any) -> None:
    if isinstance(holder, MutableMapping):
        holder[key] = value
        return
    setattr(holder, key, value)


def new_uuid() -> str:
    """实体/表行 uuid（优先用仓内实现，缺席时本地兜底）。"""
    if _repo_new_uuid is not None:
        return str(_repo_new_uuid())
    import uuid as _uuid

    return _uuid.uuid4().hex


def sketch_entities(sketch: Any) -> list[Any]:
    """草图的实体列表（优先用仓内实现）。"""
    if _repo_sketch_entities is not None:
        return _repo_sketch_entities(sketch)
    value = _get(sketch, "entities", None)
    if value is None:
        if not isinstance(sketch, MutableMapping):
            raise _fail("bad_target", "sketch target has no mutable entities list",
                        value_type=type(sketch).__name__)
        value = []
        sketch["entities"] = value
    if not isinstance(value, list):
        raise _fail("corrupt_store", "sketch entities is not a list", value_type=type(value).__name__)
    return value


def _store(target: Any, *, create: bool = True) -> dict[str, Any] | None:
    """`sk_or_doc` -> 组织表（文档落 `extra`，草图落自身；与既有模块同键）。"""
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        holder = _get(target, "extra", None)
        if holder is None:
            if not isinstance(target, MutableMapping):
                raise _fail("bad_target", "document target has no mutable extra container",
                            value_type=type(target).__name__)
            holder = target
    else:
        holder = target
    value = _get(holder, _STORE_KEY, None)
    if isinstance(value, MutableMapping):
        return value
    if not create:
        return None
    if not isinstance(holder, MutableMapping):
        raise _fail("bad_target", "target cannot hold the organization table",
                    value_type=type(holder).__name__)
    value = {"schema": _ORG_SCHEMA, _LAYERS_KEY: []}
    holder[_STORE_KEY] = value
    return value


def _layers(store: dict[str, Any]) -> list[Any]:
    value = store.get(_LAYERS_KEY)
    if value is None:
        value = []
        store[_LAYERS_KEY] = value
    if not isinstance(value, list):
        raise _fail("corrupt_store", "layers table is not a list", value_type=type(value).__name__)
    return value


def _entity_lists(target: Any) -> list[list[Any]]:
    sketches = _get(target, "sketches", None)
    if isinstance(sketches, list):
        return [sketch_entities(sketch) for sketch in sketches]
    return [sketch_entities(target)]


def _row_name(row: Any) -> str:
    value = _get(row, "name", None)
    return value if isinstance(value, str) else ""


def _row_uuid(row: Any) -> str:
    value = _get(row, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("corrupt_store", "layer row has no usable uuid")
    return value


def _entity_uuid(entity: Any) -> str:
    value = _get(entity, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("geometry_invalid", "entity has no usable uuid")
    return value


def _find_layer(store: dict[str, Any], ref: Any, *, create: bool = True) -> dict[str, Any]:
    """按名字或 uuid 找层行；`"0"` 缺席时按需补建（AutoCAD 层 0 恒在）。"""
    if not isinstance(ref, str) or not ref.strip():
        raise _fail("invalid_argument", "layer must be a non-empty name or uuid string",
                    value=repr(ref))
    wanted = ref.strip()
    rows = _layers(store)
    for row in rows:
        if not isinstance(row, MutableMapping):
            raise _fail("corrupt_store", "layer row is not a mapping",
                        value_type=type(row).__name__)
        if _row_uuid(row) == wanted or _row_name(row).lower() == wanted.lower():
            return row
    if create and wanted == _DEFAULT_LAYER:
        row = {"uuid": new_uuid(), "name": _DEFAULT_LAYER, "color": None,
               "linetype": "continuous", "lineweight_nm": -1, "plot": True,
               "plotstyle": "", "description": ""}
        rows.append(row)
        return row
    raise _fail("unknown_layer", "no layer with this name or uuid", reference=wanted)


# --------------------------------------------------------------------------------------
# 入参校验（与全仓口径一致：0=真为零；-1=未采；NA=字段缺席）
# --------------------------------------------------------------------------------------


def _name(value: Any, *, what: str = "name") -> str:
    if not isinstance(value, str):
        raise _fail("invalid_name", "layer name must be a string", field=what,
                    value_type=type(value).__name__)
    text = value.strip()
    if not text or len(text) > _MAX_NAME:
        raise _fail("invalid_name", "layer name must be 1..%d characters" % _MAX_NAME,
                    field=what, length=len(text))
    if any(ch in _INVALID_NAME_CHARS or ord(ch) < 32 for ch in text):
        raise _fail("invalid_name", "layer name contains a character AutoCAD forbids", field=what)
    return text


def _color(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _fail("invalid_color", "boolean is not a color", value=repr(value))
    if isinstance(value, int):
        if not 1 <= value <= 255:
            raise _fail("invalid_color", "ACI color must be 1..255", value=value)
        return int(value)
    if isinstance(value, str) and len(value) == 7 and value[0] == "#" and all(c in _HEX for c in value[1:]):
        return "#" + value[1:].upper()
    raise _fail("invalid_color", "color must be None (ByLayer), ACI 1..255 or #RRGGBB",
                value=repr(value))


def _linetype(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_linetype", "linetype must be a non-empty token", value=repr(value))
    text = value.strip()
    if len(text) > _MAX_NAME or any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in text):
        raise _fail("invalid_linetype", "linetype must be a short ASCII token", value=repr(value))
    return text


def _lineweight_nm(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_length", "lineweight_nm must be an integer number of nanometres",
                    value=repr(value))
    if value < -1:
        raise _fail("invalid_length", "lineweight_nm must be >= -1 (-1 means unset)", value=value)
    return int(value)


def _plot(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _fail("invalid_flag", "plot must be a bool", value=repr(value))
    return bool(value)


def _plotstyle(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail("invalid_plotstyle", "plotstyle must be a string", value=repr(value))
    text = value.strip()
    if len(text) > _MAX_NAME or any(ord(ch) < 33 or ord(ch) > 126 for ch in text):
        raise _fail("invalid_plotstyle", "plotstyle must be a short ASCII token (empty = default)",
                    value=repr(value))
    return text


def _description(value: Any) -> str:
    if not isinstance(value, str):
        raise _fail("invalid_argument", "description must be a string", value=repr(value))
    if len(value) > _MAX_DESCRIPTION:
        raise _fail("invalid_argument", "description is too long", length=len(value))
    return value


# --------------------------------------------------------------------------------------
# 操作（冻结接口：op(sk_or_doc, **kwargs) -> list[str]，长度整数纳米）
# --------------------------------------------------------------------------------------


def layer_create(
    target: Any,
    *,
    name: str,
    color: Any = None,
    linetype: str = "continuous",
    lineweight_nm: int = -1,
    plot: bool = True,
    plotstyle: str = "",
    description: str = "",
) -> list[str]:
    """建图层行（含**打印特性** plot/plotstyle），返回 `[新层 uuid]`。

    重名（大小写不敏感，AutoCAD 层名语义）⇒ `CadError(reason="duplicate_name")`；
    颜色/线型/线宽/打印样式非法 ⇒ `invalid_color` / `invalid_linetype` / `invalid_length` /
    `invalid_plotstyle`。`lineweight_nm=-1` 表示未显式设置（ByLayer 继承），`0` 表示真为零。
    """
    store = _store(target)
    clean = _name(name)
    rows = _layers(store)
    for row in rows:
        if _row_name(row).lower() == clean.lower():
            raise _fail("duplicate_name", "layer name already exists", name=clean)
    row = {
        "uuid": new_uuid(),
        "name": clean,
        "color": _color(color),
        "linetype": _linetype(linetype),
        "lineweight_nm": _lineweight_nm(lineweight_nm),
        "plot": _plot(plot),
        "plotstyle": _plotstyle(plotstyle),
        "description": _description(description),
    }
    rows.append(row)
    return [_row_uuid(row)]


def layer_props_set(
    target: Any,
    *,
    layer: Any,
    color: Any = _UNSET,
    linetype: Any = _UNSET,
    lineweight_nm: Any = _UNSET,
    plot: Any = _UNSET,
    plotstyle: Any = _UNSET,
    description: Any = _UNSET,
) -> list[str]:
    """改图层的任一特性子集（未给的关键字保持原值），返回 `[层 uuid]`。

    层不存在 ⇒ `CadError(reason="unknown_layer")`；`layer="0"` 且表里还没有该行时按 AutoCAD 语义补建。
    一个特性都没给 ⇒ `CadError(reason="no_properties")` —— 空调用零改动，不返回行 uuid 冒充"已改"。
    """
    store = _store(target)
    if (color is _UNSET and linetype is _UNSET and lineweight_nm is _UNSET
            and plot is _UNSET and plotstyle is _UNSET and description is _UNSET):
        raise _fail("no_properties",
                    "layer_props_set needs at least one property keyword; an empty call "
                    "changes nothing and must not claim an affected layer uuid")
    row = _find_layer(store, layer)
    if color is not _UNSET:
        row["color"] = _color(color)
    if linetype is not _UNSET:
        row["linetype"] = _linetype(linetype)
    if lineweight_nm is not _UNSET:
        row["lineweight_nm"] = _lineweight_nm(lineweight_nm)
    if plot is not _UNSET:
        row["plot"] = _plot(plot)
    if plotstyle is not _UNSET:
        row["plotstyle"] = _plotstyle(plotstyle)
    if description is not _UNSET:
        row["description"] = _description(description)
    return [_row_uuid(row)]


def layer_rename(target: Any, *, layer: Any, name: str) -> list[str]:
    """改层名并**同步改写用该层的实体的 `layer` 字段**，返回 `[层 uuid, *被改写实体 uuid]`。

    层 `"0"` ⇒ `protected_layer`（AutoCAD 层 0 不可改名）；目标名已被别的层占用 ⇒ `duplicate_name`。
    """
    store = _store(target)
    row = _find_layer(store, layer)
    old = _row_name(row)
    if old == _DEFAULT_LAYER:
        raise _fail("protected_layer", "layer 0 cannot be renamed", name=old)
    clean = _name(name)
    if clean.lower() != old.lower():
        for other in _layers(store):
            if other is not row and _row_name(other).lower() == clean.lower():
                raise _fail("duplicate_name", "layer name already exists", name=clean)
    row["name"] = clean
    touched: list[str] = []
    if clean != old:
        for entities in _entity_lists(target):
            for entity in entities:
                if _get(entity, "layer", None) == old:
                    _set(entity, "layer", clean)
                    touched.append(_entity_uuid(entity))
    return [_row_uuid(row)] + touched


def layer_entities(target: Any, *, layer: Any, plot: Any = _UNSET) -> list[str]:
    """现算某层上的实体 uuid 列表（按草图顺序），只读查询，不写表。

    `plot` 给出时按该层的打印开关过滤（`plot=False` 的层给 `plot=True` 即返回 `[]`），
    让"打印特性"有可核对的消费者；层不存在 ⇒ `unknown_layer`。
    """
    store = _store(target)
    row = _find_layer(store, layer)
    if plot is not _UNSET and bool(_plot(plot)) is not bool(_get(row, "plot", True)):
        return []
    wanted = _row_name(row)
    found: list[str] = []
    for entities in _entity_lists(target):
        for entity in entities:
            if _get(entity, "layer", None) == wanted:
                found.append(_entity_uuid(entity))
    return found


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "layer_create": layer_create,
    "layer_props_set": layer_props_set,
    "layer_rename": layer_rename,
    "layer_entities": layer_entities,
}


#: 只读声明（**逐 op**）：``layer_entities`` 只读文档、现算返回 uuid 列表，不写表、不落文件
#: （实现见上：只有 ``_store`` / ``_find_layer`` / ``_get`` 三个读操作 + ``list.append``）。
#: ⚠️ **不许**改成模块级 ``READONLY = True``：本片 ``layer_create`` / ``layer_props_set`` /
#: ``layer_rename`` 都是写 op，整片声明会被尺子按"声明只读却改了 L1"判 phantom。
OPERATIONS_META = {
    "layer_entities": {"tier": "query", "readonly": True, "returns": "data"},
}


# --------------------------------------------------------------------------------------
# 自测（≥2 条硬断言；含 L1 往返持久化与错误 reason 断言）
# --------------------------------------------------------------------------------------


def _selftest() -> int:
    from cadcore import docmodel
    from cadcore.sketch.commands import add_line

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    def reason_of(fn: Callable[[], Any]) -> str:
        try:
            fn()
        except CadError as exc:
            return str(exc.reason)
        except Exception as exc:  # 非 CadError 也是失败，但要看得见类型
            return "unexpected:" + type(exc).__name__
        return "no_error"

    doc = docmodel.new_document()
    sketch = {"uuid": new_uuid(), "entities": [], "constraints": [], "dimensions": []}
    doc.sketches.append(sketch)
    u1 = add_line(sketch, p1=(0, 0), p2=(1000, 0), layer="WALL")
    u2 = add_line(sketch, p1=(0, 0), p2=(0, 500), layer="WALL")
    u3 = add_line(sketch, p1=(1000, 0), p2=(1000, 500), layer="0")

    created = layer_create(doc, name="WALL", color=3, linetype="dashed",
                           lineweight_nm=250000, plot=False, plotstyle="MONO")
    rows = doc.extra["organization"]["layers"]
    check("layer_create_returns_one_uuid", len(created) == 1 and created[0] == rows[0]["uuid"],
          f"created={created!r}")
    check("layer_create_wrote_plot_props",
          rows[0]["plot"] is False and rows[0]["plotstyle"] == "MONO"
          and rows[0]["color"] == 3 and rows[0]["lineweight_nm"] == 250000,
          f"row={rows[0]!r}")

    got = layer_props_set(doc, layer="WALL", color="#00ff00", lineweight_nm=0, plot=False,
                          plotstyle="MONO", description="exterior wall")
    check("layer_props_set_returns_layer_uuid", got == [rows[0]["uuid"]], f"got={got!r}")
    check("layer_props_set_updated_fields",
          rows[0]["color"] == "#00FF00" and rows[0]["lineweight_nm"] == 0
          and rows[0]["description"] == "exterior wall",
          f"row={rows[0]!r}")

    l1_text = docmodel.document_to_l1(doc)
    rt = docmodel.document_from_l1(l1_text)
    rt_rows = rt.extra["organization"]["layers"]
    check("l1_roundtrip_keeps_layer_props",
          len(rt_rows) == 1 and rt_rows[0]["name"] == "WALL" and rt_rows[0]["plot"] is False
          and rt_rows[0]["plotstyle"] == "MONO" and rt_rows[0]["lineweight_nm"] == 0
          and rt_rows[0]["color"] == "#00FF00",
          f"rt_rows={rt_rows!r}")
    check("l1_invariants_not_polluted_by_layer_table",
          not any("organization" in problem for problem in docmodel.check_invariants(doc)))

    check("layer_entities_on_layer", layer_entities(doc, layer="WALL") == [u1, u2],
          f"got={layer_entities(doc, layer='WALL')!r}")
    check("layer_entities_plot_filter", layer_entities(doc, layer="WALL", plot=True) == []
          and layer_entities(doc, layer="WALL", plot=False) == [u1, u2])

    touched = layer_rename(doc, layer="WALL", name="WALL-P")
    check("layer_rename_rewrites_entity_layer_field",
          touched == [rows[0]["uuid"], u1, u2]
          and [e["layer"] for e in sketch["entities"]] == ["WALL-P", "WALL-P", "0"]
          and layer_entities(doc, layer="0") == [u3],
          f"touched={touched!r} entities={[e['layer'] for e in sketch['entities']]!r}")

    check("duplicate_name_rejected",
          reason_of(lambda: layer_create(doc, name="wall-p")) == "duplicate_name")
    check("unknown_layer_rejected",
          reason_of(lambda: layer_props_set(doc, layer="NOPE", plot=True)) == "unknown_layer")
    check("layer_zero_rename_rejected",
          reason_of(lambda: layer_rename(doc, layer="0", name="BASE")) == "protected_layer")
    check("invalid_name_rejected",
          reason_of(lambda: layer_create(doc, name="bad,name")) == "invalid_name")
    check("invalid_color_rejected",
          reason_of(lambda: layer_props_set(doc, layer="WALL-P", color=0)) == "invalid_color")
    check("invalid_lineweight_rejected",
          reason_of(lambda: layer_props_set(doc, layer="WALL-P", lineweight_nm=-2)) == "invalid_length")

    failed = [item for item in checks if not item[1]]
    for name, _ok, detail in failed:
        print(f"FAIL check={name} detail={detail}")
    print("ORGANIZATION_W9 item=0 ops=%d checks=%d failed=%d still_missing=%s"
          % (len(OPERATIONS), len(checks), len(failed), "ui_routes,dxf_plot_group_290"))
    print("FEATURE_OK family=organization op=layer_create verdict=" + ("PASS" if not failed else "FAIL"))
    return 0 if not failed else 1


def main(argv: Any = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("--selftest", "-s"):
        print("usage: organization__w9.py [--selftest]")
        return 2
    return _selftest()


if __name__ == "__main__":
    raise SystemExit(main())
