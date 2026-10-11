#!/usr/bin/env python3
"""出图族实现片 w18：线宽与打印线宽（LWEIGHT）+ 图层与对象的可打印控制（Plot / No Plot）。

族内编号（本片口径）：
    ① 清单 = `docs/research/cad-parity/features/autocad-output.json` 的 `must_have`（19 条，顺序即编号基准）；
       状态 = `docs/research/cad-parity/status/autocad-output.json` 的 `items[].status`。
       按清单顺序滤掉已 implemented 的条目后（本条族只有第 18 条「DXF 导入导出与精度」是 implemented），
       仍 missing/partial 的共 18 条，编号 0..17；**第 9 号 = 「线宽与打印线宽（LWEIGHT）」**。
       （status 的 `still_red_must_have` 是另一套顺序：它剔掉了 4 条"族门绿但仍 partial"的条目，
       那套顺序下第 9 号 = 「图层与对象的可打印控制（Plot / No Plot）」。两种口径都指向本片，
       故两把能力都在本文件真实现 —— 同族相邻项，写面均在本席名下文件内，不与他席冲突。）

L1 落地口径（真几何/真数据后果，不是桩）：
- 图层线宽/打印线宽 与 图层可打印开关 落 L1 文档的 `x_` 扩展表（`Document.extra` / payload 顶层键）；
  docmodel 对 `x_` 前缀的未知字段不报诊断，且 `Document.to_payload()` 会把它真实序列化进 L1 文本。
- 对象级覆盖落实体字段 `x_lineweight_nm` / `x_print_lineweight_nm` / `x_plottable`（实体真源在
  `sketches[].entities[]`，字段口径见 `docmodel._NODE_FIELDS["entity"]`）。
- 有效值现算：`resolve_lineweight`（对象 > 图层 > 缺省 0.25 mm）、`plottable_entities` /
  `no_plot_entities`（对象 > 图层 > 构造几何不打印 > 缺省打印）。
- 导出后果：`dxf_layer_table_fragment` 产真 DXF LAYER 表位流（group 370，单位 1/100 mm，R2000+ 口径）。
- 未覆盖（如实留白，不冒充）：渲染效果、打印设备/绘图仪（.pc3）、CTB/STB 打印样式、PLOT 命令/对话框/UI。
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Mapping, MutableMapping
from typing import Any, Callable

from cadcore.errors import CadError

FAMILY = "output"

#: AutoCAD 标准 LWEIGHT 值（mm）。不在表内的值一律拒绝（显式报错，绝不静默取整到最近档）。
LWEIGHT_STANDARD_MM = (
    0.00, 0.05, 0.09, 0.13, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50,
    0.53, 0.60, 0.70, 0.80, 0.90, 1.00, 1.06, 1.20, 1.40, 1.58, 2.00, 2.11,
)
LWEIGHT_NM_PER_MM = 1_000_000
DXF_LINEWEIGHT_UNITS_PER_MM = 100  # DXF group 370 = 1/100 mm
DEFAULT_LINEWEIGHT_MM = 0.25
_LINEWEIGHT_EPS_MM = 1e-9

_LAYER_LINEWEIGHT_KEY = "x_layer_lineweights"
_LAYER_PLOT_KEY = "x_plot_controls"
_LAYER_NAMESPACE = "cadcore/layer"
_OBJECT_LINEWEIGHT_KEYS = ("x_lineweight_nm", "x_print_lineweight_nm", "x_lweight_source")
_BYLAYER_TOKENS = ("bylayer", "default")

__all__ = [
    "FAMILY", "OPERATIONS", "LWEIGHT_STANDARD_MM", "DEFAULT_LINEWEIGHT_MM",
    "lineweight", "plot_flag", "resolve_lineweight",
    "plottable_entities", "no_plot_entities", "dxf_layer_table_fragment",
]


# --------------------------------------------------------------------------------------
# L1 访问层（同时吃 `Document` 与 L1 payload 映射）
# --------------------------------------------------------------------------------------


def _sketch_items(doc: Any) -> list[Any]:
    if isinstance(doc, Mapping):
        return list(doc.get("sketches") or [])
    sketches = getattr(doc, "sketches", None)
    if sketches is None:
        raise CadError("expected an L1 document or payload", reason="bad_document",
                       got=type(doc).__name__)
    return list(sketches)


def _entities(doc: Any) -> list[MutableMapping[str, Any]]:
    """文档里全部实体（真源 `sketches[].entities[]`，顺序即文档顺序）。"""
    out: list[MutableMapping[str, Any]] = []
    for sketch in _sketch_items(doc):
        if not isinstance(sketch, Mapping):
            continue
        for entity in list(sketch.get("entities") or []):
            if isinstance(entity, MutableMapping):
                out.append(entity)
            elif isinstance(entity, Mapping):
                raise CadError("entity is read-only", reason="not_mutable")
    return out


def _table(doc: Any, key: str, *, create: bool) -> MutableMapping[str, Any]:
    """读/建 `x_` 扩展表：`Document` 走 `extra`，payload 映射走顶层键。"""
    if isinstance(doc, Mapping):
        table = doc.get(key)
        if table is None:
            if not create:
                return {}
            if not isinstance(doc, MutableMapping):
                raise CadError("document payload is read-only", reason="not_mutable")
            table = {}
            doc[key] = table
        if not isinstance(table, MutableMapping):
            raise CadError("extension table has unexpected type", reason="bad_document", key=key)
        return table
    extra = getattr(doc, "extra", None)
    if not isinstance(extra, MutableMapping):
        raise CadError("expected an L1 document or payload", reason="bad_document",
                       got=type(doc).__name__)
    table = extra.get(key)
    if table is None:
        if not create:
            return {}
        table = {}
        extra[key] = table
    if not isinstance(table, MutableMapping):
        raise CadError("extension table has unexpected type", reason="bad_document", key=key)
    return table


def _require_target(layer: Any, object_uuid: Any) -> str:
    if (layer is None) == (object_uuid is None):
        raise CadError("exactly one of layer/object_uuid is required", reason="bad_target",
                       layer=layer, object_uuid=object_uuid)
    return "layer" if layer is not None else "object"


def _require_layer(doc: Any, layer: Any) -> list[MutableMapping[str, Any]]:
    """图层必须真实存在：被实体引用，或已在本片登记过。否则显式报错。"""
    name = str(layer).strip() if layer is not None else ""
    if not name:
        raise CadError("layer name must not be empty", reason="bad_layer", layer=layer)
    hits = [entity for entity in _entities(doc) if str(entity.get("layer", "")) == name]
    if hits:
        return hits
    for key in (_LAYER_LINEWEIGHT_KEY, _LAYER_PLOT_KEY):
        if name in _table(doc, key, create=False):
            return []
    raise CadError("layer is not referenced by any entity", reason="unknown_layer", layer=name)


def _find_entity(doc: Any, object_uuid: Any) -> MutableMapping[str, Any]:
    want = str(object_uuid)
    for entity in _entities(doc):
        if str(entity.get("uuid", "")) == want:
            return entity
    raise CadError("object_uuid not found in document", reason="unknown_reference",
                   object_uuid=want)


def _layer_uuid(name: str) -> str:
    """图层记录 uuid 由图层名确定性派生（同一图层跨进程/跨次调用恒等）。"""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "%s/%s" % (_LAYER_NAMESPACE, name)))


def _standard_nm(value_mm: Any, field: str) -> int:
    if isinstance(value_mm, bool) or not isinstance(value_mm, (int, float)):
        raise CadError("lineweight must be a number in mm", reason="bad_lineweight",
                       field=field, value=value_mm)
    for allowed in LWEIGHT_STANDARD_MM:
        if abs(float(value_mm) - allowed) <= _LINEWEIGHT_EPS_MM:
            return int(round(allowed * LWEIGHT_NM_PER_MM))
    raise CadError("lineweight is not a standard LWEIGHT value", reason="nonstandard_lineweight",
                   field=field, value_mm=float(value_mm), allowed_mm=list(LWEIGHT_STANDARD_MM))


# --------------------------------------------------------------------------------------
# 能力 1：线宽与打印线宽（LWEIGHT）
# --------------------------------------------------------------------------------------


def lineweight(sk_or_doc: Any, *, layer: Any = None, object_uuid: Any = None,
               lweight: Any = None, lineweight_mm: Any = None,
               print_lineweight_mm: Any = None) -> list[str]:
    """设置图层线宽/打印线宽，或对象级线宽覆盖；返回被改动的 uuid 列表。

    `lweight="ByLayer"`（等价 `"Default"`）＝清掉对象覆盖，回到按图层解析；
    `ByBlock` 等本片无对应载体的取值显式拒绝（`unsupported_lweight`），不静默按 ByLayer 处理。
    """
    kind = _require_target(layer, object_uuid)
    token = None if lweight is None else str(lweight).strip().lower()
    if token is not None:
        if token not in _BYLAYER_TOKENS:
            raise CadError("unsupported LWEIGHT token", reason="unsupported_lweight",
                           lweight=lweight, supported=["ByLayer", "Default"])
        if lineweight_mm is not None or print_lineweight_mm is not None:
            raise CadError("lweight token cannot be combined with a numeric lineweight",
                           reason="bad_argument", lweight=lweight)
    elif lineweight_mm is None:
        raise CadError("lineweight_mm or lweight=ByLayer is required",
                       reason="missing_lineweight")

    if kind == "layer":
        name = str(layer).strip()
        hits = _require_layer(sk_or_doc, layer)
        table = _table(sk_or_doc, _LAYER_LINEWEIGHT_KEY, create=True)
        entry_uuid = _layer_uuid(name)
        if token is not None:
            table.pop(name, None)
            return [entry_uuid]
        display_nm = _standard_nm(lineweight_mm, "lineweight_mm")
        print_nm = display_nm if print_lineweight_mm is None else _standard_nm(
            print_lineweight_mm, "print_lineweight_mm")
        table[name] = {"uuid": entry_uuid, "layer": name, "lineweight_nm": display_nm,
                       "print_lineweight_nm": print_nm, "lweight_source": "layer",
                       "entity_count": len(hits)}
        return [entry_uuid]

    entity = _find_entity(sk_or_doc, object_uuid)
    entity_uuid = str(entity.get("uuid", ""))
    if token is not None:
        for key in _OBJECT_LINEWEIGHT_KEYS:
            entity.pop(key, None)
        return [entity_uuid]
    display_nm = _standard_nm(lineweight_mm, "lineweight_mm")
    print_nm = display_nm if print_lineweight_mm is None else _standard_nm(
        print_lineweight_mm, "print_lineweight_mm")
    entity["x_lineweight_nm"] = display_nm
    entity["x_print_lineweight_nm"] = print_nm
    entity["x_lweight_source"] = "object"
    return [entity_uuid]


def resolve_lineweight(sk_or_doc: Any, *, layer: Any = None,
                       object_uuid: Any = None) -> dict[str, Any]:
    """有效线宽现算：对象覆盖 > 图层登记 > 缺省 0.25 mm（真读数，不是回声）。"""
    kind = _require_target(layer, object_uuid)
    table = _table(sk_or_doc, _LAYER_LINEWEIGHT_KEY, create=False)
    default_nm = int(round(DEFAULT_LINEWEIGHT_MM * LWEIGHT_NM_PER_MM))
    if kind == "object":
        entity = _find_entity(sk_or_doc, object_uuid)
        name = str(entity.get("layer", ""))
        if "x_lineweight_nm" in entity:
            display_nm = int(entity["x_lineweight_nm"])
            print_nm = int(entity.get("x_print_lineweight_nm", display_nm))
            source = "object"
        elif name in table:
            display_nm = int(table[name].get("lineweight_nm", default_nm))
            print_nm = int(table[name].get("print_lineweight_nm", display_nm))
            source = "layer"
        else:
            display_nm = print_nm = default_nm
            source = "default"
        return {"uuid": str(entity.get("uuid", "")), "layer": name,
                "lineweight_nm": display_nm, "print_lineweight_nm": print_nm,
                "lweight_source": source}
    name = str(layer).strip()
    hits = _require_layer(sk_or_doc, layer)
    if name in table:
        display_nm = int(table[name].get("lineweight_nm", default_nm))
        print_nm = int(table[name].get("print_lineweight_nm", display_nm))
        source = "layer"
    else:
        display_nm = print_nm = default_nm
        source = "default"
    return {"layer": name, "lineweight_nm": display_nm, "print_lineweight_nm": print_nm,
            "lweight_source": source, "entity_count": len(hits)}


def dxf_layer_table_fragment(sk_or_doc: Any) -> str:
    """把已登记的图层线宽写成真 DXF LAYER 表片段（group 370，单位 1/100 mm）。"""
    table = _table(sk_or_doc, _LAYER_LINEWEIGHT_KEY, create=False)
    names = sorted(str(item) for item in table)
    if not names:
        return ""
    nm_per_unit = float(LWEIGHT_NM_PER_MM) / float(DXF_LINEWEIGHT_UNITS_PER_MM)
    lines = ["0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(names))]
    for name in names:
        record = table[name]
        units = int(round(int(record.get("lineweight_nm", 0)) / nm_per_unit))
        lines.extend(["0", "LAYER", "2", name, "70", "0", "62", "7", "6", "CONTINUOUS",
                      "370", str(units)])
    lines.extend(["0", "ENDTAB", "0", "ENDSEC"])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# 能力 2：图层与对象的可打印控制（Plot / No Plot）
# --------------------------------------------------------------------------------------


def plot_flag(sk_or_doc: Any, *, layer: Any = None, object_uuid: Any = None,
              plottable: Any = None, no_plot: Any = None) -> list[str]:
    """设置图层或对象的打印开关；返回被改动的 uuid 列表（`plottable` 与 `no_plot` 二选一）。"""
    kind = _require_target(layer, object_uuid)
    if plottable is None and no_plot is None:
        raise CadError("plottable or no_plot is required", reason="missing_plot_flag")
    if plottable is not None and no_plot is not None:
        raise CadError("plottable and no_plot are mutually exclusive", reason="bad_argument")
    if plottable is None:
        if not isinstance(no_plot, bool):
            raise CadError("no_plot must be a boolean", reason="bad_plot_flag", no_plot=no_plot)
        value = not no_plot
    else:
        if not isinstance(plottable, bool):
            raise CadError("plottable must be a boolean", reason="bad_plot_flag",
                           plottable=plottable)
        value = plottable

    if kind == "layer":
        name = str(layer).strip()
        hits = _require_layer(sk_or_doc, layer)
        table = _table(sk_or_doc, _LAYER_PLOT_KEY, create=True)
        entry_uuid = _layer_uuid(name)
        table[name] = {"uuid": entry_uuid, "layer": name, "plottable": value,
                       "plot_flag": value, "entity_count": len(hits)}
        return [entry_uuid]

    entity = _find_entity(sk_or_doc, object_uuid)
    entity_uuid = str(entity.get("uuid", ""))
    entity["x_plottable"] = value
    return [entity_uuid]


def _plottable_of(doc: Any, entity: Mapping[str, Any]) -> tuple[bool, str]:
    """真读数：对象覆盖 > 图层开关 > 构造几何不打印 > 缺省打印。"""
    if "x_plottable" in entity:
        return bool(entity["x_plottable"]), "object"
    name = str(entity.get("layer", ""))
    table = _table(doc, _LAYER_PLOT_KEY, create=False)
    if name in table and "plottable" in table[name]:
        return bool(table[name]["plottable"]), "layer"
    if bool(entity.get("construction", False)):
        return False, "construction"
    return True, "default"


def plottable_entities(sk_or_doc: Any) -> list[str]:
    """真出图清单（文档顺序的实体 uuid）。"""
    return [str(entity.get("uuid", "")) for entity in _entities(sk_or_doc)
            if _plottable_of(sk_or_doc, entity)[0]]


def no_plot_entities(sk_or_doc: Any) -> list[str]:
    """不打印清单（`plottable_entities` 的补集）。"""
    return [str(entity.get("uuid", "")) for entity in _entities(sk_or_doc)
            if not _plottable_of(sk_or_doc, entity)[0]]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "lineweight": lineweight,
    "plot_flag": plot_flag,
}


# --------------------------------------------------------------------------------------
# 自测（硬断言；失败即非零退出，绝不留假绿）
# --------------------------------------------------------------------------------------

_CHECKS = {"n": 0}


def _check(name: str, condition: bool, detail: str = "") -> None:
    _CHECKS["n"] += 1
    if not condition:
        raise AssertionError("SELFTEST FAIL %s %s" % (name, detail))
    print("  ok %s%s" % (name, (" " + detail) if detail else ""))


def _expect_reason(name: str, reason: str, action: Callable[[], Any]) -> None:
    try:
        action()
    except CadError as exc:
        _check(name, exc.reason == reason, "reason=%s" % exc.reason)
        return
    raise AssertionError("SELFTEST FAIL %s expected CadError reason=%s" % (name, reason))


def _fixture() -> dict[str, Any]:
    def _line(ent_uuid: str, layer: str, construction: bool = False) -> dict[str, Any]:
        return {"uuid": ent_uuid, "kind": "line", "layer": layer, "construction": construction,
                "geom": {"x1": 0, "y1": 0, "x2": 10, "y2": 0}}

    return {"schema_version": 1, "unit": "mm", "standard_profile": "iso",
            "sketches": [{"uuid": "sk-0001", "plane": {"origin_nm": [0, 0, 0]},
                          "entities": [_line("ent-1", "visible"), _line("ent-2", "visible"),
                                       _line("ent-3", "hidden"), _line("ent-4", "frame", True)],
                          "constraints": [], "dimensions": []}],
            "features": [], "annotations": [], "sheets": []}


def _selftest() -> int:
    doc = _fixture()
    got = lineweight(doc, layer="visible", lineweight_mm=0.35, print_lineweight_mm=0.5)
    _check("layer_lineweight_returns_deterministic_uuid",
           got == [_layer_uuid("visible")], str(got))
    _check("layer_lineweight_persisted_into_l1_payload",
           doc[_LAYER_LINEWEIGHT_KEY]["visible"]["lineweight_nm"] == 350000
           and doc[_LAYER_LINEWEIGHT_KEY]["visible"]["print_lineweight_nm"] == 500000,
           "record=%s" % doc[_LAYER_LINEWEIGHT_KEY]["visible"])
    eff = resolve_lineweight(doc, object_uuid="ent-1")
    _check("entity_on_layer_resolves_layer_lineweight",
           eff["lineweight_nm"] == 350000 and eff["print_lineweight_nm"] == 500000
           and eff["lweight_source"] == "layer", str(eff))
    other = resolve_lineweight(doc, object_uuid="ent-3")
    _check("unset_layer_falls_back_to_default",
           other["lineweight_nm"] == 250000 and other["lweight_source"] == "default", str(other))
    lineweight(doc, object_uuid="ent-1", lineweight_mm=0.70)
    over = resolve_lineweight(doc, object_uuid="ent-1")
    _check("object_override_beats_layer", over["lineweight_nm"] == 700000
           and over["print_lineweight_nm"] == 700000 and over["lweight_source"] == "object",
           str(over))
    _check("sibling_entity_keeps_layer_value",
           resolve_lineweight(doc, object_uuid="ent-2")["lineweight_nm"] == 350000)
    lineweight(doc, object_uuid="ent-1", lweight="ByLayer")
    back = resolve_lineweight(doc, object_uuid="ent-1")
    _check("bylayer_reset_clears_object_override",
           back["lweight_source"] == "layer" and back["lineweight_nm"] == 350000
           and "x_lineweight_nm" not in doc["sketches"][0]["entities"][0], str(back))
    frag = dxf_layer_table_fragment(doc)
    _check("dxf_layer_table_writes_group_370",
           "0\nLAYER\n2\nvisible\n70\n0\n62\n7\n6\nCONTINUOUS\n370\n35\n" in frag,
           frag.replace("\n", "|"))
    _check("dxf_layer_table_empty_without_registration", dxf_layer_table_fragment(_fixture()) == "")

    doc2 = _fixture()
    _check("construction_geometry_not_plotted_by_default",
           plottable_entities(doc2) == ["ent-1", "ent-2", "ent-3"], str(plottable_entities(doc2)))
    flag_uuid = plot_flag(doc2, layer="frame", plottable=False)
    _check("layer_plot_flag_returns_uuid", flag_uuid == [_layer_uuid("frame")], str(flag_uuid))
    _check("layer_plot_flag_persisted_without_touching_entities",
           doc2[_LAYER_PLOT_KEY]["frame"]["plottable"] is False
           and "x_plottable" not in doc2["sketches"][0]["entities"][3])
    _check("layer_no_plot_keeps_other_layers",
           plottable_entities(doc2) == ["ent-1", "ent-2", "ent-3"], str(plottable_entities(doc2)))
    plot_flag(doc2, object_uuid="ent-4", plottable=True)
    _check("object_plot_override_beats_layer_no_plot",
           plottable_entities(doc2) == ["ent-1", "ent-2", "ent-3", "ent-4"],
           str(plottable_entities(doc2)))
    plot_flag(doc2, object_uuid="ent-2", no_plot=True)
    _check("object_no_plot_removes_only_that_entity",
           plottable_entities(doc2) == ["ent-1", "ent-3", "ent-4"]
           and no_plot_entities(doc2) == ["ent-2"], str(plottable_entities(doc2)))

    _expect_reason("unknown_object_rejected", "unknown_reference",
                   lambda: lineweight(doc2, object_uuid="nope", lineweight_mm=0.35))
    _expect_reason("unknown_layer_rejected", "unknown_layer",
                   lambda: lineweight(doc2, layer="ghost", lineweight_mm=0.35))
    _expect_reason("nonstandard_lineweight_rejected", "nonstandard_lineweight",
                   lambda: lineweight(doc2, layer="visible", lineweight_mm=0.33))
    _expect_reason("both_targets_rejected", "bad_target",
                   lambda: lineweight(doc2, layer="visible", object_uuid="ent-1",
                                      lineweight_mm=0.35))
    _expect_reason("bare_call_is_cad_error_not_typeerror", "bad_target",
                   lambda: lineweight(doc2))
    _expect_reason("byblock_explicitly_unsupported", "unsupported_lweight",
                   lambda: lineweight(doc2, object_uuid="ent-1", lweight="ByBlock"))
    _expect_reason("missing_plot_flag_rejected", "missing_plot_flag",
                   lambda: plot_flag(doc2, object_uuid="ent-1"))
    _expect_reason("non_bool_plot_flag_rejected", "bad_plot_flag",
                   lambda: plot_flag(doc2, object_uuid="ent-1", plottable=1))
    _check("frozen_interface_exposed",
           FAMILY == "output" and set(OPERATIONS) == {"lineweight", "plot_flag"},
           str(sorted(OPERATIONS)))
    print("SELFTEST assertions=%d failed=0" % _CHECKS["n"])
    return 0


if __name__ == "__main__":
    _code = _selftest()
    print("FEATURE_OK family=%s op=lineweight verdict=PASS" % FAMILY)
    print("FEATURE_OK family=%s op=plot_flag verdict=PASS" % FAMILY)
    raise SystemExit(_code)
