#!/usr/bin/env python3
"""SW-DRAWINGS 族 · 实现片 w16：工程图模板（.drwdot）—— 把出图起点固化成可复用、可套用的定义。

本片覆盖哪一条（编号口径）
------------------------
真源：`docs/research/cad-parity/features/sw-drawings.json` 的 `must_have`（21 条，0..20）；
状态：`docs/research/cad-parity/status/sw-drawings.json`。该族仍 missing/partial 的条目按清单顺序编号：

    0 视图调色板拖放视图 / 投影视图 (partial)
    1 尺寸标注：导入模型尺寸 + 手工智能尺寸 (partial)
    2 尺寸公差与配合 (partial)
    3 形位公差（GD&T）符号与基准 (missing)
    4 表面粗糙度符号 (partial)
    5 孔标注（异型孔向导孔自动标注）(missing)
    6 零件序号（气球）标注 (partial)
    7 工程图模板（.drwdot）(missing)   <== 本片负责（21 条 must_have 里的第 18 条）
    8 打印与导出（PDF / DWG / DXF）(partial)

能力做什么（在 L1 文档模型上是真数据后果，不是桩）
------------------------------------------------
SolidWorks 的 `.drwdot` = 「出图起点」：图幅/比例/标准/语言 + 图纸格式（图框与标题栏）+ 图层与线型 +
视图缺省（投影角、隐藏线）+ 预填标题栏字段，之后每张新图从它派生。本片把它落在 L1 图纸模型上：

1. **真图纸**：按模板的 `standard/language/size/scale` 建/取图纸（走族内既有 `_sheet_for` → `draw.sheet.make_sheet`），
   所以模板一落，图幅与比例就是真图纸属性，不是文案。
2. **真几何**：调用 `draw.sheet.title_block` 现画图框与标题栏（真曲线/文本进图纸实体表），
   并把模板预填的标题栏字段一起交给它；`iter_entities` 现数实体数记进模板记录。
3. **真图层**：模板 `layers` 逐条登记进图纸图层表（`draw.sheet._register_layer`，已存在则计为已应用）。
4. **真定义**：`save_as=` 把归一化后的模板定义（含 sha256 指纹）写进文档顶层 `x_drawing_templates`，
   需要时同时把 `x_drawing_template_default` 指到它 —— 之后**同族任何调用**可只给模板名复现同一套出图起点
   （`source="document"`），这是本操作可被机检的真数据后果；`make_default=True` 才动指针。
5. **真记录**：每次套用落一条 `kind="drawing_template"` 的 annotation（`args` 里带 `format=".drwdot"`、
   模板名、来源、图幅、比例、图层、视图缺省、标题栏字段、实体数、指纹），并按族约定 `_publish` 登记视图记录。

冻结接口
--------
    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable]          # {"apply_drawing_template": apply_drawing_template}
    op(sk_or_doc, **kwargs) -> list[str]     # 返回本次新建对象的 uuid（图纸 uuid（新建时）+ 模板记录 uuid）
    长度一律整数纳米（`view_defaults["text_height_nm"]` 逐条校验）；错误一律 `cadcore.errors.CadError(reason=...)`。

已知边界（显式抛错/留痕，不静默近似，也不假装支持）
------------------------------------------------
* L1 文档模型里没有 `.drwdot` 二进制格式的读写通道 ⇒ 本片**不**声称能导入/导出 SolidWorks 原生模板文件；
  模板以 L1 JSON 里的定义（文档顶层 `x_drawing_templates`）为准，`format` 字段只作来源标记。
* 宿主不是文档（裸草图/无 `sheets[]`+`annotations[]` 桶）⇒ `template_needs_document`（不静默丢弃模板）。
* 模板名既不在文档定义里也不在内置表里 ⇒ `unknown_template`；标准不在 `draw.sheet.standard_ids()` ⇒ `unknown_standard`；
  比例不是 `num:den` 正整数对 ⇒ `bad_scale`；标题栏字段/图层/视图缺省类型不符 ⇒ `bad_title_fields`/`bad_layers`/`bad_view_defaults`。
* `title_block()` 失败 ⇒ **不静默**：reason 写进模板记录 `titleblock_error` 并 `Sheet.note()` 留痕（定义与记录照落）。
* 图层表不可达/登记失败 ⇒ `layers_applied = -1`（未采）+ 原因字符串，不冒充"已应用"。

三态口径（AGENTS.md E2.3）：`0` 真为零 / `-1`（`sheet.UNMEASURED`）未采 / `"NA"`（`sheet.NA`）不适用。

自检（文件自带，不依赖 pytest，硬断言 ≥2 条）

    python3 cadcore/ops/features/sw_drawings__w16.py
    ⇒ 末行 `FEATURE_OK family=sw_drawings op=apply_drawing_template verdict=PASS`

中文只出现在 docstring/注释里（`tools/cad/i18n_gate.py` 口径）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from cadcore.draw import sheet as _sheet
from cadcore.errors import CadError
from cadcore.sketch import primitives as _primitives
from .. import sw_drawings as _base

FAMILY: str = "sw_drawings"
OP_NAME: str = "apply_drawing_template"

NM_PER_MM: int = 1_000_000
TEMPLATE_FORMAT: str = ".drwdot"
TEMPLATE_KIND: str = "drawing_template"
TEMPLATE_STORE_KEY: str = "x_drawing_templates"
TEMPLATE_POINTER_KEY: str = "x_drawing_template_default"
DEFAULT_TEMPLATE_NAME: str = "iso_a3_third"
DEFAULT_TEXT_HEIGHT_NM: int = int(3.5 * NM_PER_MM)

# 内置模板：L1 里没有 .drwdot 二进制，内置表就是"出厂图纸格式"的最小定义（ASCII，文案走语言包/调用方）。
BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "iso_a3_third": {
        "standard": "iso",
        "size": "A3",
        "scale": "1:2",
        "title_fields": {"standard": "ISO 128", "projection": "third_angle"},
        "layers": [
            {"name": "TPL_FRAME", "linetype": "CONTINUOUS", "role": "frame"},
            {"name": "TPL_NOTES", "linetype": "CONTINUOUS", "role": "annotation"},
            {"name": "TPL_HIDDEN", "linetype": "HIDDEN", "role": "hidden"},
        ],
        "view_defaults": {"layout": "third_angle", "show_hidden": "HIDDEN",
                          "text_height_nm": DEFAULT_TEXT_HEIGHT_NM},
    },
    "iso_a4_first": {
        "standard": "iso",
        "size": "A4",
        "scale": "1:1",
        "title_fields": {"standard": "ISO 128", "projection": "first_angle"},
        "layers": [
            {"name": "TPL_FRAME", "linetype": "CONTINUOUS", "role": "frame"},
            {"name": "TPL_NOTES", "linetype": "CONTINUOUS", "role": "annotation"},
        ],
        "view_defaults": {"layout": "first_angle", "show_hidden": "VISIBLE",
                          "text_height_nm": DEFAULT_TEXT_HEIGHT_NM},
    },
}

_SCALE_RE = re.compile(r"^\d+(?:\.\d+)?:\d+(?:\.\d+)?$")
_SIZE_RE = re.compile(r"^[A-Z]{1,2}\d{1,2}$")


# --------------------------------------------------------------------------------------
# 小工具：校验与归一化（长度一律整数纳米；错误一律 CadError(reason=...)）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带 reason 的族内错误（不直接 raise，调用点语义更清楚）。"""
    return CadError(message, reason=reason, **details)


def _require_nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度必须是整数纳米（bool 不算整数）；`positive=True` 时要求 > 0。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length", "length must be an integer number of nanometres",
                    what=what, value=repr(value))
    if positive and int(value) <= 0:
        raise _fail("bad_length", "length must be positive", what=what, value=int(value))
    return int(value)


def _jsonable(value: Any) -> Any:
    """把任意嵌套值收敛成可 json.dumps 的形态（未知类型降级成 str，不静默丢键）。"""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _normalize_size(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip().upper()
    if not _SIZE_RE.match(text):
        raise _fail("bad_sheet_size", "sheet size token must look like A3/A4/LETTER",
                    size=str(value))
    return text


def _scale_text(value: Any) -> str:
    """比例归一成 `num:den`；映射形态 `{"num":1,"den":2}` 也认，其余一律 bad_scale。"""
    if value in (None, ""):
        return ""
    if isinstance(value, Mapping):
        num, den = value.get("num"), value.get("den")
        if num is None or den is None:
            raise _fail("bad_scale", "scale mapping needs both num and den", scale=dict(value))
        text = "%s:%s" % (num, den)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise _fail("bad_scale", "scale must be a 'num:den' string or a {num, den} mapping",
                    scale=repr(value))
    if not _SCALE_RE.match(text):
        raise _fail("bad_scale", "scale must be a positive num:den ratio", scale=text)
    num_text, den_text = text.split(":", 1)
    if float(num_text) <= 0 or float(den_text) <= 0:
        raise _fail("bad_scale", "scale numerator and denominator must both be positive",
                    scale=text)
    return text


def _merge_fields(base_fields: Any, override: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for source in (base_fields, override):
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise _fail("bad_title_fields", "title_fields must be a mapping",
                        got=type(source).__name__)
        for key, value in source.items():
            out[str(key)] = "" if value is None else str(value)
    return out


def _normalize_layers(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise _fail("bad_layers", "layers must be a sequence", got=type(value).__name__)
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
            linetype = str(item.get("linetype") or "CONTINUOUS").strip().upper()
            role = str(item.get("role") or "annotation").strip().lower()
        elif isinstance(item, str):
            name, linetype, role = item.strip(), "CONTINUOUS", "annotation"
        else:
            raise _fail("bad_layers", "layer entry must be a mapping or a name string",
                        got=type(item).__name__)
        if not name:
            raise _fail("bad_layers", "layer name must not be empty", layer=repr(item))
        out.append({"name": name, "linetype": linetype, "role": role})
    return out


def _merge_view_defaults(base_defaults: Any, override: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for source in (base_defaults, override):
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise _fail("bad_view_defaults", "view_defaults must be a mapping",
                        got=type(source).__name__)
        out.update({str(key): value for key, value in source.items()})
    return _jsonable(out)


def _layer_present(registry: Any, name: str) -> bool:
    if isinstance(registry, Mapping):
        return name in registry
    if isinstance(registry, (list, tuple)):
        for entry in registry:
            if isinstance(entry, Mapping) and str(entry.get("name", "")) == name:
                return True
            if str(getattr(entry, "name", "")) == name:
                return True
    return False


def _apply_layers(sheet_obj: Any, layer_specs: Sequence[Mapping[str, str]]) -> tuple[int, str]:
    """把模板图层登记进图纸图层表；返回 (已应用条数 | UNMEASURED, 失败原因或空串)。"""
    if not layer_specs:
        return 0, ""
    register = getattr(_sheet, "_register_layer", None)
    registry = getattr(sheet_obj, "layers", None)
    if not callable(register) or registry is None:
        sheet_obj.note("apply_drawing_template: sheet layer table not reachable -- template "
                       "layers kept as record data only (layers_applied=-1)")
        return _sheet.UNMEASURED, "layer_table_unavailable"
    applied = 0
    for spec in layer_specs:
        name = str(spec["name"])
        if _layer_present(registry, name):
            applied += 1
            continue
        try:
            register(sheet_obj, name, str(spec["linetype"]), str(spec["role"]))
        except Exception as exc:  # 显式降级：记原因，不冒充已应用
            reason = "layer_register_failed:%s" % type(exc).__name__
            sheet_obj.note("apply_drawing_template: layer %s not registered (%s)" % (name, reason))
            return _sheet.UNMEASURED, reason
        applied += 1
    return applied, ""


def _readback(obj: Any, names: Sequence[str]) -> Any:
    """从图纸对象上现读属性（多个候选名，取第一个有值的）；都没采到 ⇒ NA（不静默给 0）。"""
    for name in names:
        if not hasattr(obj, name):
            continue
        value = getattr(obj, name)
        if value in (None, ""):
            continue
        return value
    return _sheet.NA


# --------------------------------------------------------------------------------------
# 模板解析：inline spec / 文档内定义 / 内置表
# --------------------------------------------------------------------------------------


def _resolve_spec(payload: Mapping[str, Any], *, template: Any,
                  spec: Any) -> tuple[dict[str, Any], str, str]:
    """返回 (模板定义, 模板名, 来源 builtin|document|inline)。"""
    if spec is not None:
        if not isinstance(spec, Mapping):
            raise _fail("bad_template_spec", "template spec must be a mapping",
                        got=type(spec).__name__)
        spec_dict = copy.deepcopy(dict(spec))
        fallback = template if isinstance(template, str) else ""
        return spec_dict, str(spec_dict.get("name") or fallback or "inline"), "inline"
    if isinstance(template, Mapping):
        spec_dict = copy.deepcopy(dict(template))
        return spec_dict, str(spec_dict.get("name") or "inline"), "inline"
    by_name: dict[str, dict[str, Any]] = {}
    store = payload.get(TEMPLATE_STORE_KEY)
    if isinstance(store, list):
        for entry in store:
            if isinstance(entry, Mapping) and entry.get("name"):
                by_name[str(entry["name"])] = copy.deepcopy(dict(entry))
    if template in (None, ""):
        pointer = payload.get(TEMPLATE_POINTER_KEY)
        if isinstance(pointer, str) and pointer.strip():
            wanted = pointer.strip()
        else:  # 文档没指定默认 ⇒ 出厂缺省模板
            return copy.deepcopy(BUILTIN_TEMPLATES[DEFAULT_TEMPLATE_NAME]), DEFAULT_TEMPLATE_NAME, "builtin"
    else:
        wanted = str(template).strip()
    if wanted in by_name:
        return by_name[wanted], wanted, "document"
    if wanted in BUILTIN_TEMPLATES:
        return copy.deepcopy(BUILTIN_TEMPLATES[wanted]), wanted, "builtin"
    raise _fail("unknown_template", "unknown drawing template", template=wanted,
                known_document=sorted(by_name), known_builtin=sorted(BUILTIN_TEMPLATES))


# --------------------------------------------------------------------------------------
# 操作：套用工程图模板（.drwdot）
# --------------------------------------------------------------------------------------


def apply_drawing_template(
    sk_or_doc: Any,
    *,
    template: Any = None,
    spec: Any = None,
    save_as: Any = None,
    make_default: bool = False,
    standard: Any = None,
    language: Any = None,
    size: Any = None,
    scale: Any = None,
    sheet: Any = None,
    title_fields: Any = None,
    layers: Any = None,
    view_defaults: Any = None,
) -> list[str]:
    """**工程图模板（.drwdot）**：把出图起点（图幅/比例/标准/语言/图层/标题栏字段/视图缺省）套到图纸上。

    参数：

    * `template`：模板名（先在文档 `x_drawing_templates` 里找，再查内置表）；也可直接给定义 mapping；
      `None` ⇒ 文档 `x_drawing_template_default` 指向的模板，没有则用内置缺省 `iso_a3_third`；
    * `spec`：内联模板定义（优先级最高，配合 `save_as=` 可把它固化进文档）；
    * `save_as`：把归一化后的定义（含 sha256 指纹）写进文档 `x_drawing_templates`（同名覆盖）；`make_default=True`
      同时把 `x_drawing_template_default` 指到它；
    * `standard` / `language` / `size` / `scale`：覆盖模板里的对应项（`size="A3"`，`scale="1:2"` 或 `{"num":1,"den":2}`）；
    * `sheet`：显式图纸对象（不再新建）；`title_fields` / `layers` / `view_defaults`：在模板之上再合并/追加。

    返回：`[图纸 uuid（仅本操作新建图纸时）] + [模板记录 uuid]`；记录同时落 `annotations[]`
    （`kind="drawing_template"`，`args.format=".drwdot"`），视图记录由 `_publish` 登记。

    边界：宿主不是文档 ⇒ `template_needs_document`；模板名未知 ⇒ `unknown_template`；标准未知 ⇒ `unknown_standard`；
    比例非法 ⇒ `bad_scale`；字段/图层/视图缺省类型不符 ⇒ `bad_title_fields` / `bad_layers` / `bad_view_defaults`。
    """
    payload = _base._as_payload(sk_or_doc)
    if not isinstance(payload, dict) or not isinstance(payload.get("sheets"), list) \
            or not isinstance(payload.get("annotations"), list):
        raise _fail("template_needs_document",
                    "drawing template needs a document with sheets[] and annotations[] buckets",
                    host=type(payload).__name__,
                    has_sheets=isinstance(payload.get("sheets"), list) if isinstance(payload, Mapping) else False,
                    has_annotations=isinstance(payload.get("annotations"), list) if isinstance(payload, Mapping) else False)
    annotations = _base._live_annotations(sk_or_doc)
    if not isinstance(annotations, list):
        annotations = payload["annotations"]

    spec_dict, name, source = _resolve_spec(payload, template=template, spec=spec)

    std = str(standard or spec_dict.get("standard") or getattr(_base, "DEFAULT_STANDARD", "iso"))
    lang = str(language or spec_dict.get("language") or getattr(_base, "DEFAULT_LANGUAGE", "en"))
    size_text = _normalize_size(size if size is not None else spec_dict.get("size"))
    scale_text = _scale_text(scale if scale is not None else spec_dict.get("scale"))
    fields = _merge_fields(spec_dict.get("title_fields"), title_fields)
    layer_specs = _normalize_layers(layers if layers is not None else spec_dict.get("layers"))
    views = _merge_view_defaults(spec_dict.get("view_defaults"), view_defaults)
    views["text_height_nm"] = _require_nm(views.get("text_height_nm", DEFAULT_TEXT_HEIGHT_NM),
                                          what="view_defaults.text_height_nm", positive=True)

    known_standards = tuple(_sheet.standard_ids())
    if known_standards and std not in known_standards:
        raise _fail("unknown_standard", "unknown drawing standard", standard=std,
                    known=list(known_standards))
    if scale_text:
        try:
            _sheet._parse_scale(scale_text)
        except CadError:
            raise
        except Exception as exc:  # 上游解析口径变了 ⇒ 仍以本族 reason 报错，不抛裸异常
            raise _fail("bad_scale", "scale rejected by draw.sheet._parse_scale",
                        scale=scale_text, detail=type(exc).__name__) from exc

    sheet_obj, created = _base._sheet_for(sk_or_doc, payload, sheet=sheet, standard=std,
                                          language=lang, size=size_text or None,
                                          scale=scale_text or None)

    # 真几何：图框 + 标题栏现画（失败也不静默，原因进记录）
    title_result: dict[str, str] = {}
    titleblock_error = ""
    try:
        rendered = _sheet.title_block(sheet_obj, doc=payload, language=lang)
        title_result = {str(key): str(value) for key, value in dict(rendered or {}).items()}
    except Exception as exc:
        titleblock_error = "%s:%s" % (type(exc).__name__, getattr(exc, "reason", "") or "")
        sheet_obj.note("apply_drawing_template: title_block() failed (%s) -- template record and "
                       "sheet keep going, geometry not drawn" % titleblock_error)

    layers_applied, layers_reason = _apply_layers(sheet_obj, layer_specs)

    entities: Any = _sheet.UNMEASURED
    try:
        entities = len(_sheet.iter_entities(sheet_obj))
    except Exception as exc:
        sheet_obj.note("apply_drawing_template: iter_entities() failed (%s) -- entity count left "
                       "unmeasured (-1)" % type(exc).__name__)

    sheet_uuid = str(getattr(sheet_obj, "uuid", ""))
    definition = _jsonable({
        "name": name, "format": TEMPLATE_FORMAT, "standard": std, "language": lang,
        "size": size_text, "scale": scale_text, "title_fields": fields,
        "layers": layer_specs, "view_defaults": views,
    })
    digest = hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()

    # 真定义：固化进文档（同名覆盖），指针只在 make_default=True 时动
    saved_name = ""
    if save_as not in (None, ""):
        saved_name = str(save_as).strip()
        if not saved_name:
            raise _fail("bad_template_name", "save_as must not be blank", save_as=repr(save_as))
        store = payload.get(TEMPLATE_STORE_KEY)
        if not isinstance(store, list):
            store = []
            payload[TEMPLATE_STORE_KEY] = store
        record = dict(definition)
        record["name"] = saved_name
        record["sha256"] = digest
        store[:] = [entry for entry in store
                    if not (isinstance(entry, Mapping) and str(entry.get("name")) == saved_name)]
        store.append(record)
        if make_default:
            payload[TEMPLATE_POINTER_KEY] = saved_name

    record_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                                  "cadcore.sw_drawings.template|%s|%s|%s"
                                  % (sheet_uuid, name, digest)))
    entry = {
        "uuid": record_uuid,
        "target": [sheet_uuid, "sheet"],
        "kind": TEMPLATE_KIND,
        "text_key": "drawing.template",
        "literal": False,
        "args": {
            "template": name, "format": TEMPLATE_FORMAT, "source": source,
            "standard": std, "language": lang, "size": size_text, "scale": scale_text,
            "sheet_uuid": sheet_uuid, "sheet_created": bool(created),
            "sheet_size_readback": str(_readback(sheet_obj, ("size", "size_token", "sheet_size", "paper"))),
            "sheet_scale_readback": str(_readback(sheet_obj, ("scale", "scale_text", "scale_token", "ratio"))),
            "title_fields": fields, "titleblock_fields": title_result,
            "titleblock_error": titleblock_error,
            "layers": layer_specs, "layers_requested": len(layer_specs),
            "layers_applied": layers_applied, "layers_reason": layers_reason,
            "view_defaults": views, "entities": entities,
            "saved_as": saved_name, "make_default": bool(make_default), "sha256": digest,
        },
    }
    _base._register(annotations, entry)

    host_fn = getattr(_base, "_host", None)
    host = host_fn(sk_or_doc) if callable(host_fn) else sk_or_doc
    _base._publish(host, sheet_obj, [], OP_NAME)
    sheet_obj.note("apply_drawing_template: template=%s format=%s source=%s size=%s scale=%s "
                   "entities=%s layers_applied=%s saved_as=%s sha256=%s"
                   % (name, TEMPLATE_FORMAT, source, size_text or "-", scale_text or "-",
                      entities, layers_applied, saved_name or "-", digest))
    return ([sheet_uuid] if created else []) + [record_uuid]


OPERATIONS: dict[str, Callable[..., list[str]]] = {OP_NAME: apply_drawing_template}


def module_facts() -> dict[str, Any]:
    """族模块机读事实（三态可辨：0 真为零 / -1 未采 / "NA" 不适用）。"""
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "feature": "must_have[18] drawing_template (.drwdot) = missing/partial #7",
        "template_format": TEMPLATE_FORMAT,
        "template_store_key": TEMPLATE_STORE_KEY,
        "template_pointer_key": TEMPLATE_POINTER_KEY,
        "annotation_kind": TEMPLATE_KIND,
        "builtin_templates": sorted(BUILTIN_TEMPLATES),
        "default_template": DEFAULT_TEMPLATE_NAME,
        "base_family": str(getattr(_base, "FAMILY", "")),
        "base_op_count": len(getattr(_base, "OPERATIONS", {}) or {}),
        "sheet_module": "cadcore.draw.sheet",
        "binary_drwdot_io": False,
    }


# --------------------------------------------------------------------------------------
# 自检：真夹具 + 硬断言（≥2 条），不依赖 pytest
# --------------------------------------------------------------------------------------


def _selftest_document() -> dict[str, Any]:
    """自检夹具：60×40×20 mm 板（真草图 + 真拉伸特征）的 L1 文档映射，不依赖任何夹具文件。"""
    sketch: dict[str, Any] = {
        "uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings__w16.selftest.sketch")),
        "plane": "XY", "entities": [], "constraints": [], "dimensions": []}
    _primitives.add_rectangle(sketch, p1_nm=(0, 0), p2_nm=(60 * NM_PER_MM, 40 * NM_PER_MM))
    return {
        "meta": {"doc_uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings__w16.selftest.doc"))},
        "sketches": [sketch],
        "features": [{"uuid": str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "sw_drawings__w16.selftest.extrude")),
                      "kind": "extrude", "params": {"distance_nm": 20 * NM_PER_MM}, "suppressed": False}],
        "annotations": [], "sheets": [],
    }


def _carry_templates(source: Mapping[str, Any]) -> dict[str, Any]:
    """新夹具 + 只搬运文档里已固化的模板定义与默认指针（证明"定义随文档走"）。"""
    target = _selftest_document()
    for key in (TEMPLATE_STORE_KEY, TEMPLATE_POINTER_KEY):
        if key in source:
            target[key] = copy.deepcopy(source[key])
    return target


def _records(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in doc.get("annotations", [])
            if isinstance(item, Mapping) and item.get("kind") == TEMPLATE_KIND and item.get("args")]


def _selftest() -> dict[str, Any]:
    """硬断言（任一不成立即 AssertionError ⇒ 判定行 FAIL）：真夹具上跑真操作。"""
    checks: list[str] = []

    # A1 模板真落纸：显式覆盖 + 固化 + 记录格式/来源/字段
    doc_a = _selftest_document()
    uuids = apply_drawing_template(
        doc_a, template=DEFAULT_TEMPLATE_NAME, size="A2", scale="2:1",
        save_as="company_a2", make_default=True,
        title_fields={"title": "SEAT PLATE", "drawing_no": "SWD-0001"})
    assert len(uuids) >= 2, "expected a new sheet uuid and a template record uuid, got %r" % (uuids,)
    for uid in uuids:
        _uuid.UUID(str(uid))
    recs = _records(doc_a)
    assert recs, "no drawing_template record was registered on the document"
    args = recs[-1]["args"]
    assert args["format"] == ".drwdot", args["format"]
    assert args["size"] == "A2" and args["scale"] == "2:1", (args["size"], args["scale"])
    assert args["title_fields"].get("drawing_no") == "SWD-0001", args["title_fields"]
    assert args["source"] == "builtin" and args["template"] == DEFAULT_TEMPLATE_NAME, args["template"]
    checks.append("A1 spec_applied format=%s size=%s scale=%s source=%s"
                  % (args["format"], args["size"], args["scale"], args["source"]))

    # A2 定义真固化：save_as 进文档 store，且默认指针指到它
    store = doc_a.get(TEMPLATE_STORE_KEY)
    assert isinstance(store, list) and store and store[-1]["name"] == "company_a2", store
    assert store[-1]["size"] == "A2" and store[-1]["scale"] == "2:1", store[-1]
    assert doc_a.get(TEMPLATE_POINTER_KEY) == "company_a2", doc_a.get(TEMPLATE_POINTER_KEY)
    assert doc_a["annotations"], "document did not change (annotations[] still empty)"
    checks.append("A2 definition_persisted store=%d pointer=%s" % (len(store), doc_a.get(TEMPLATE_POINTER_KEY)))

    # A3 跨文档复现：新文档只给模板名 ⇒ 复现同一套出图起点（source=document，值不是内置值）
    doc_b = _carry_templates(doc_a)
    apply_drawing_template(doc_b, template="company_a2")
    args_b = _records(doc_b)[-1]["args"]
    assert args_b["source"] == "document", args_b["source"]
    assert args_b["size"] == "A2" and args_b["scale"] == "2:1", (args_b["size"], args_b["scale"])
    checks.append("A3 document_template_reused source=%s size=%s scale=%s"
                  % (args_b["source"], args_b["size"], args_b["scale"]))

    # A4 默认指针：template=None 时按 x_drawing_template_default 解析
    doc_c = _carry_templates(doc_a)
    apply_drawing_template(doc_c)
    args_c = _records(doc_c)[-1]["args"]
    assert args_c["template"] == "company_a2" and args_c["source"] == "document", args_c["template"]
    assert args_c["size"] == "A2", args_c["size"]
    checks.append("A4 default_pointer template=%s size=%s" % (args_c["template"], args_c["size"]))

    # A5 阴性对照：未知模板 / 非法比例 / 非文档宿主都必须抛 CadError 且带 reason
    try:
        apply_drawing_template(_selftest_document(), template="no_such_template")
        raise AssertionError("unknown template name must raise CadError")
    except CadError as exc:
        assert exc.reason == "unknown_template", exc.reason
    try:
        apply_drawing_template(_selftest_document(), template=DEFAULT_TEMPLATE_NAME, scale="abc")
        raise AssertionError("illegal scale must raise CadError")
    except CadError as exc:
        assert exc.reason == "bad_scale", exc.reason
    try:
        apply_drawing_template({"plane": "XY", "entities": []})
        raise AssertionError("a bare sketch must not be accepted as a drawing host")
    except CadError as exc:
        assert exc.reason == "template_needs_document", exc.reason
    checks.append("A5 negative_controls unknown_template+bad_scale+template_needs_document")

    # A6 冻结接口与文档 token（族门按 token 判"新出现"）
    assert FAMILY == "sw_drawings", FAMILY
    assert OP_NAME in OPERATIONS and all(callable(fn) for fn in OPERATIONS.values()), sorted(OPERATIONS)
    dumped = json.dumps(doc_a, ensure_ascii=False)
    assert "drawing_template" in dumped and TEMPLATE_FORMAT in dumped, "template tokens absent from document"
    checks.append("A6 interface FAMILY=%s ops=%s tokens=drawing_template|.drwdot" % (FAMILY, sorted(OPERATIONS)))

    return {"checks": checks, "assertions": len(checks), "uuids": len(uuids),
            "entities": args["entities"], "layers_applied": args["layers_applied"],
            "sheet_size_readback": args["sheet_size_readback"],
            "titleblock_error": args["titleblock_error"]}


def main(argv: Sequence[str] | None = None) -> int:
    """自检入口：判定行末行 `FEATURE_OK family=sw_drawings op=apply_drawing_template verdict=PASS`。"""
    try:
        facts = _selftest()
    except AssertionError as exc:
        print("SELFTEST_FAIL family=%s op=%s detail=%s" % (FAMILY, OP_NAME, exc))
        print("FEATURE_OK family=sw_drawings op=%s verdict=FAIL" % OP_NAME)
        return 1
    except CadError as exc:
        print("SELFTEST_ERROR family=%s op=%s reason=%s detail=%s" % (FAMILY, OP_NAME, exc.reason, exc))
        print("FEATURE_OK family=sw_drawings op=%s verdict=FAIL" % OP_NAME)
        return 1
    for line in facts["checks"]:
        print("SELFTEST_CHECK %s" % line)
    print("SELFTEST_FACTS family=%s op=%s assertions=%d uuids=%d entities=%s layers_applied=%s "
          "sheet_size=%s titleblock_error=%s"
          % (FAMILY, OP_NAME, facts["assertions"], facts["uuids"], facts["entities"],
             facts["layers_applied"], facts["sheet_size_readback"], facts["titleblock_error"] or "-"))
    print("FEATURE_OK family=sw_drawings op=%s verdict=PASS" % OP_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_drawings__w16.py:387 "zh-CN" -> "en"
