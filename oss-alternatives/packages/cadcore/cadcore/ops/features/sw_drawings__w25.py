#!/usr/bin/env python3
"""SW-DRAWINGS 片模块 w25：工程图模板（.drwdot）——企业标准的可复用出图起点。

族接口（冻结）：

    FAMILY = "sw_drawings"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]    # 返回**本次新建对象**的 uuid 列表

本片负责 must_have 里仍 missing 的「工程图模板（.drwdot）」（该族 missing/partial 清单第 7 号）。
SolidWorks 的 .drwdot 是一份**可复用的出图起点**：图幅、比例、标准档/投影角、标题栏字段、
自定义属性、图层与线型、标注缺省值在模板里固化，新建图纸时一次套用。本仓 L1 文档模型里对应两件事：

* ``define_drawing_template``：把一份模板规格**校验并登记**成文档级可复用对象
  （L1 键 ``x_drawing_templates``，字段：uuid / kind / format="drwdot" / name / standard / language /
  size / scale / projection_angle / title_block_fields / properties / layers / annotation_defaults /
  revision）；同名模板**原地更新**（uuid 不变、revision+1、条目不重复）；
* ``apply_drawing_template``：按模板**真建一张图纸**（``draw.sheet.make_sheet``，标准档/语言/幅面/比例
  直接来自模板），逐个把模板图层登记到该图纸上（复用 ``sw_drawings._register_extra_layer``），
  把标题栏字段与模板出处写进该图纸的 L1 条目（``sheets[]``），并登记一条可按 uuid 解析的绑定对象
  （``annotations[]``，kind=``drawing_template_binding``）—— 返回的每个 uuid 都能在文档里解析到，不悬空。

不支持的组合一律抛 ``cadcore.errors.CadError(reason=...)``（选摘）：

* 模板缺 ``name`` / 非字符串 / 空白 ⇒ ``bad_template_name``；
* ``scale`` 不合 ``num:den`` 或非正 ⇒ ``bad_scale``；``projection_angle`` 非 first|third ⇒ ``bad_projection_angle``；
* ``layers`` 某条缺 name、重名或 role 非法 ⇒ ``bad_layer``；标注缺省值非正整数纳米 ⇒ ``bad_length``；
* ``make_sheet`` 拒收该图幅/比例 ⇒ ``bad_sheet_format``（**不降级**成别的图幅）；
* 按名字套用而文档里没有该模板 ⇒ ``template_not_found``；没有文档宿主（裸草图/只读映射）⇒ ``no_document_host``；
* 图层登记设施不可用（``sw_drawings._register_extra_layer`` 缺失）⇒ ``template_layers_unsupported``，
  **不静默跳过图层**。

口径：长度一律整数纳米（``*_nm``，``bool``/``float`` 一律拒绝）；比例是 ``"num:den"`` 文本；
``layers_verified_on_sheet`` 是三态读数（True=量到图层数增长；False=没量到，不冒充已验证）。
自检（文件自带，不依赖 pytest）：

    python3 cadcore/ops/features/sw_drawings__w25.py

中文只出现在 docstring 里（``tools/cad/i18n_gate.py`` 的口径）。
"""

from __future__ import annotations

import os
import re
import sys
import uuid as _uuid
from typing import Any, Callable, Mapping, Sequence

if __package__ in (None, ""):  # 直跑 / 按路径装载（隔离区 family gate）时的引导，同 sw_assembly__w22
    sys.path.insert(
        0,
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ),
    )
    __package__ = "cadcore.ops.features"

# 隔离区比 ops/ 深一层：`..errors` 会解析成 cadcore.ops.features.errors ⇒ 一律绝对导入（同 w22）。
from cadcore.errors import CadError
from cadcore.draw import sheet as _sheet
from cadcore.docmodel import Document

from cadcore.ops import sw_drawings as _swd

__all__ = [
    "FAMILY", "OPERATIONS", "define_drawing_template", "apply_drawing_template",
    "module_facts", "selftest",
]

#: family id (frozen interface).
FAMILY = "sw_drawings"

NM_PER_MM = 1_000_000

#: template file format token (the SolidWorks drawing template extension).
FORMAT = "drwdot"

#: L1 buckets owned by this piece module.
TEMPLATE_BUCKET = "x_drawing_templates"
DEFAULT_KEY = "x_default_drawing_template"
SHEET_BUCKET = "sheets"
BINDING_BUCKET = "annotations"
BINDING_KIND = "drawing_template_binding"

#: layer roles a template may declare (mirrors the sheet layer families).
LAYER_ROLES: tuple[str, ...] = ("visible", "hidden", "dimension", "center", "hatch", "text", "bom", "frame")

#: defaults come from draw.sheet (single source of truth); never re-declare a second table here.
DEFAULT_STANDARD = "gbt"
DEFAULT_LANGUAGE = str(getattr(_sheet, "DEFAULT_LANGUAGE_FALLBACK", "en"))
DEFAULT_SIZE = str(getattr(_sheet, "DEFAULT_SIZE", "A3"))
DEFAULT_SCALE = str(getattr(_sheet, "DEFAULT_SCALE", "1:1"))
ANNOTATION_DEFAULT_KEYS: tuple[str, ...] = ("text_height_nm", "arrow_length_nm", "line_weight_nm")
DEFAULT_ANNOTATION_DEFAULTS: dict[str, int] = {"text_height_nm": 3 * NM_PER_MM}

_SCALE_RE = re.compile(r"^\s*(\d+)\s*[:/]\s*(\d+)\s*$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# --------------------------------------------------------------------------------------
# validation helpers (explicit, ASCII messages, machine readable reason)
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """Build a CadError carrying a machine readable ``reason`` (messages stay ASCII)."""
    return CadError(str(message), reason=str(reason), **details)


def _text(value: Any, *, what: str, reason: str = "bad_template_field") -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(reason, "non-empty string required", field=what, value=repr(value))
    return value.strip()


def _canonical_scale(value: Any) -> str:
    """``"1:2"`` / ``"1/2"`` -> ``"1:2"``; anything else raises ``bad_scale``."""
    match = _SCALE_RE.match(str(value if value is not None else ""))
    if match is None:
        raise _fail("bad_scale", "scale must look like 'num:den'", scale=repr(value))
    num, den = int(match.group(1)), int(match.group(2))
    if num <= 0 or den <= 0:
        raise _fail("bad_scale", "scale terms must be positive", scale=str(value))
    return "%d:%d" % (num, den)


def _nm(value: Any, *, what: str) -> int:
    """Integer nanometre check: bool and float are rejected (length truth is int only)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("bad_length", "integer nanometres required", field=what, value=repr(value))
    if int(value) <= 0:
        raise _fail("bad_length", "value must be a positive integer in nanometres",
                    field=what, value_nm=int(value))
    return int(value)


def _string_map(value: Any, *, what: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _fail("bad_template_field", "mapping required", field=what,
                    value_type=type(value).__name__)
    out: dict[str, str] = {}
    for key, item in value.items():
        field = _text(key, what="%s.key" % what)
        if isinstance(item, bool) or isinstance(item, (Mapping, list, tuple, set)):
            raise _fail("bad_template_field", "scalar value required", field="%s.%s" % (what, field),
                        value_type=type(item).__name__)
        out[field] = str(item)
    return out


def _annotation_defaults(value: Any) -> dict[str, int]:
    if value is None:
        return dict(DEFAULT_ANNOTATION_DEFAULTS)
    if not isinstance(value, Mapping):
        raise _fail("bad_template_field", "mapping required", field="annotation_defaults",
                    value_type=type(value).__name__)
    out: dict[str, int] = dict(DEFAULT_ANNOTATION_DEFAULTS)
    for key, item in value.items():
        field = _text(key, what="annotation_defaults.key")
        if field not in ANNOTATION_DEFAULT_KEYS:
            raise _fail("bad_template_field", "unknown annotation default", field=field,
                        allowed=list(ANNOTATION_DEFAULT_KEYS))
        out[field] = _nm(item, what="annotation_defaults.%s" % field)
    return out


def _layer_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("bad_layer", "layers must be a sequence of names or mappings",
                    value_type=type(value).__name__)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, str):
            entry: Mapping[str, Any] = {"name": item}
        elif isinstance(item, Mapping):
            entry = item
        else:
            raise _fail("bad_layer", "layer entry must be a name or a mapping", index=index,
                        value_type=type(item).__name__)
        name = _text(entry.get("name"), what="layers[%d].name" % index, reason="bad_layer")
        if name in seen:
            raise _fail("bad_layer", "duplicate layer name in template", layer=name)
        seen.add(name)
        role = entry.get("role", "visible")
        if role not in LAYER_ROLES:
            raise _fail("bad_layer", "unknown layer role", layer=name, role=repr(role),
                        allowed=list(LAYER_ROLES))
        row: dict[str, Any] = {"name": name, "role": str(role)}
        if entry.get("linetype") is not None:
            row["linetype"] = _text(entry["linetype"], what="layers[%d].linetype" % index)
        if entry.get("line_weight_nm") is not None:
            row["line_weight_nm"] = _nm(entry["line_weight_nm"],
                                        what="layers[%d].line_weight_nm" % index)
        out.append(row)
    return out


def _normalize(spec: Mapping[str, Any], *, uuid_value: Any = None, revision: int = 1) -> dict[str, Any]:
    """Template spec -> canonical L1 record (all validation happens here, once)."""
    name = _text(spec.get("name"), what="name", reason="bad_template_name")
    raw_angle = spec.get("projection_angle")
    angle: str | None
    if raw_angle is None or str(raw_angle).strip() == "":
        angle = None
    else:
        token = str(raw_angle).strip().lower()
        if token in ("first", "first_angle"):
            angle = "first"
        elif token in ("third", "third_angle"):
            angle = "third"
        else:
            raise _fail("bad_projection_angle", "projection_angle must be first|third",
                        projection_angle=repr(raw_angle))
    fields = spec.get("title_block_fields")
    if fields is None:
        fields = spec.get("title_block")
    return {
        "uuid": str(uuid_value or _uuid.uuid4()),
        "kind": "drawing_template",
        "format": FORMAT,
        "name": name,
        "standard": _text(spec.get("standard", DEFAULT_STANDARD), what="standard"),
        "language": _text(spec.get("language", DEFAULT_LANGUAGE), what="language"),
        "size": _text(spec.get("size", DEFAULT_SIZE), what="size").upper(),
        "scale": _canonical_scale(spec.get("scale", DEFAULT_SCALE)),
        "projection_angle": angle,
        "title_block_fields": _string_map(fields, what="title_block_fields"),
        "properties": _string_map(spec.get("properties"), what="properties"),
        "layers": _layer_list(spec.get("layers")),
        "annotation_defaults": _annotation_defaults(spec.get("annotation_defaults")),
        "revision": int(revision),
        "engine": "cadcore.l1-drawing-template",
    }


# --------------------------------------------------------------------------------------
# document host / L1 buckets
# --------------------------------------------------------------------------------------


def _host_for(model: Any) -> Any:
    """Writable document host: Document or a document level L1 mapping (bare sketch -> None)."""
    if isinstance(model, Document):
        return model
    if isinstance(model, Mapping) and not isinstance(model, (str, bytes)):
        if "entities" in model and "sketches" not in model:
            return None
        return model if callable(getattr(model, "__setitem__", None)) else None
    return None


def _require_host(model: Any) -> Any:
    host = _host_for(model)
    if host is None:
        raise _fail("no_document_host",
                    "drawing templates are document scoped: pass a Document or a document level L1 mapping",
                    value_type=type(model).__name__)
    return host


def _bucket(host: Any, key: str) -> list[Any]:
    """Get (or create) one L1 container list on the host document."""
    if isinstance(host, Document):
        value = getattr(host, key, None)
        if not isinstance(value, list):
            value = []
            try:
                setattr(host, key, value)
            except Exception as exc:  # noqa: BLE001 - refuse to fake a registry we cannot store
                raise _fail("template_registry_unsupported", "document cannot hold template data",
                            key=key, detail="%s: %s" % (type(exc).__name__, exc)) from exc
        return value
    value = host.get(key)
    if not isinstance(value, list):
        value = []
        host[key] = value
    return value


def _register(container: list[Any], entry: Mapping[str, Any]) -> None:
    """Idempotent registration by uuid: same uuid overwrites, never appends twice."""
    uid = str(entry.get("uuid") or "")
    for index, item in enumerate(container):
        if isinstance(item, Mapping) and str(item.get("uuid") or "") == uid:
            container[index] = dict(entry)
            return
    container.append(dict(entry))


def _find_template(registry: Sequence[Any], name: str) -> Mapping[str, Any] | None:
    for item in registry:
        if isinstance(item, Mapping) and str(item.get("name") or "") == name:
            return item
    return None


def _default_name(host: Any) -> str:
    value = getattr(host, DEFAULT_KEY, None) if isinstance(host, Document) else host.get(DEFAULT_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _fail("template_not_found", "no template name given and the document has no default template",
                key=DEFAULT_KEY)


def _set_default(host: Any, name: str) -> None:
    if isinstance(host, Document):
        setattr(host, DEFAULT_KEY, name)
    else:
        host[DEFAULT_KEY] = name


def _payload(model: Any) -> dict[str, Any]:
    if isinstance(model, Document):
        data = dict(model.to_payload())
    elif isinstance(model, Mapping):
        data = dict(model)
        if "entities" in data and "sketches" not in data:
            data = {"sketches": [dict(data)], "features": []}
    else:
        raise _fail("unsupported_model", "model must be a Document or an L1 mapping",
                    value_type=type(model).__name__)
    data.setdefault("sketches", [])
    data.setdefault("features", [])
    return data


# --------------------------------------------------------------------------------------
# layers on a real sheet (delegated to the family module, never re-implemented)
# --------------------------------------------------------------------------------------


def _layer_names(sheet_obj: Any) -> list[str]:
    layers = getattr(sheet_obj, "layers", None)
    names: list[str] = []
    if isinstance(layers, Mapping):
        for key, value in layers.items():
            names.append(str(getattr(value, "name", "") or key))
    elif isinstance(layers, Sequence) and not isinstance(layers, (str, bytes)):
        for item in layers:
            if isinstance(item, Mapping):
                names.append(str(item.get("name") or ""))
            else:
                names.append(str(getattr(item, "name", "") or ""))
    return [name for name in names if name]


def _layer_count(sheet_obj: Any) -> int:
    """Number of layer slots on the sheet; ``-1`` means "not measurable" (never faked as 0)."""
    layers = getattr(sheet_obj, "layers", None)
    try:
        return int(len(layers))  # type: ignore[arg-type]
    except TypeError:
        return -1


def _apply_layers(sheet_obj: Any, layers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Register every template layer on the sheet; verify the sheet layer count really grew."""
    registrar = getattr(_swd, "_register_extra_layer", None) if _swd is not None else None
    if layers and not callable(registrar):
        raise _fail("template_layers_unsupported",
                    "sw_drawings._register_extra_layer is unavailable: refusing to fake template layers")
    before = _layer_count(sheet_obj)
    applied: list[str] = []
    for layer in layers:
        name, role = str(layer["name"]), str(layer["role"])
        try:
            registrar(sheet_obj, name, role)  # type: ignore[misc]
        except CadError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface the real failure with context
            raise _fail("template_layer_failed", "could not register template layer",
                        layer=name, role=role,
                        detail="%s: %s" % (type(exc).__name__, exc)) from exc
        applied.append(name)
    after = _layer_count(sheet_obj)
    names = _layer_names(sheet_obj)
    lowered = [name.lower() for name in names]
    missing = [name for name in applied if name.lower() not in lowered]
    if applied and missing and names:
        raise _fail("template_layer_missing", "template layer did not land on the sheet", layer=missing)
    verified = bool(applied) and before >= 0 and after >= before + len(applied)
    return {"applied": applied, "layers_applied": len(applied), "verified_on_sheet": verified,
            "sheet_layers_before": before, "sheet_layers_after": after}


# --------------------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------------------


def define_drawing_template(sk_or_doc: Any, *, name: Any = None, template: Any = None, standard: Any = None,
                            language: Any = None, size: Any = None, scale: Any = None, title_block: Any = None,
                            properties: Any = None, layers: Any = None, annotation_defaults: Any = None,
                            projection_angle: Any = None, make_default: bool = False) -> list[str]:
    """定义/覆盖一份工程图模板（``.drwdot``）并登记进文档 ⇒ 返回 ``[template_uuid]``。

    ``template=`` 给整份规格（mapping），显式关键字覆盖其中的同名项。同名模板**原地更新**：
    uuid 保持不变、``revision`` 加一，``x_drawing_templates`` 里始终只有一条。文档里第一份模板
    （或 ``make_default=True``）自动成为 ``x_default_drawing_template``。
    """
    host = _require_host(sk_or_doc)
    if template is not None and not isinstance(template, Mapping):
        raise _fail("bad_template", "template must be a mapping", value_type=type(template).__name__)
    spec: dict[str, Any] = dict(template) if isinstance(template, Mapping) else {}
    overrides = {"name": name, "standard": standard, "language": language, "size": size, "scale": scale,
                 "title_block": title_block, "properties": properties, "layers": layers,
                 "annotation_defaults": annotation_defaults, "projection_angle": projection_angle}
    for key, value in overrides.items():
        if value is not None:
            spec[key] = value
    record = _normalize(spec)
    registry = _bucket(host, TEMPLATE_BUCKET)
    existing = _find_template(registry, record["name"])
    if existing is not None:
        record["uuid"] = str(existing.get("uuid") or record["uuid"])
        record["revision"] = int(existing.get("revision") or 0) + 1
    _register(registry, record)
    if make_default or len(registry) == 1:
        _set_default(host, record["name"])
    return [str(record["uuid"])]


def apply_drawing_template(sk_or_doc: Any, *, name: Any = None, template: Any = None, sheet: Any = None,
                           title_block: Any = None, make_default: bool = False) -> list[str]:
    """按模板出图：真建一张图纸 + 真套用模板图层 + 落模板出处 ⇒ 返回 ``[sheet_uuid, binding_uuid]``。

    ``name=`` 按名字取文档里已登记的模板（缺省取 ``x_default_drawing_template``）；``template=`` 直接给
    内联规格（会顺带登记，其 uuid 排在最前）。``title_block=`` 可覆盖/补充标题栏字段（写进图纸 L1 条目
    与绑定对象）。传 ``sheet=`` 则在既有图纸上套用（该图纸 uuid 不重复返回）。
    """
    host = _require_host(sk_or_doc)
    payload = _payload(sk_or_doc)
    registry = _bucket(host, TEMPLATE_BUCKET)
    created: list[str] = []
    if template is not None:
        if not isinstance(template, Mapping):
            raise _fail("bad_template", "template must be a mapping", value_type=type(template).__name__)
        spec = dict(template)
        if name is not None:
            spec["name"] = name
        record = _normalize(spec)
        existing = _find_template(registry, record["name"])
        if existing is not None:
            record["uuid"] = str(existing.get("uuid") or record["uuid"])
            record["revision"] = int(existing.get("revision") or 0) + 1
        _register(registry, record)
        created.append(str(record["uuid"]))
    else:
        key = _default_name(host) if name is None else _text(name, what="name", reason="bad_template_name")
        found = _find_template(registry, key)
        if found is None:
            raise _fail("template_not_found", "no drawing template with that name in the document",
                        template=key,
                        available=[str(item.get("name")) for item in registry if isinstance(item, Mapping)])
        record = dict(found)

    fields = dict(record.get("title_block_fields") or {})
    if title_block is not None:
        fields.update(_string_map(title_block, what="title_block"))

    if sheet is None:
        try:
            made = _sheet.make_sheet(payload,
                                     standard=str(record.get("standard") or DEFAULT_STANDARD),
                                     language=str(record.get("language") or DEFAULT_LANGUAGE),
                                     size=str(record.get("size") or DEFAULT_SIZE),
                                     scale=str(record.get("scale") or DEFAULT_SCALE))
        except CadError:
            raise
        except Exception as exc:  # noqa: BLE001 - a rejected sheet format must not degrade silently
            raise _fail("bad_sheet_format", "draw.sheet.make_sheet rejected the template sheet format",
                        detail="%s: %s" % (type(exc).__name__, exc),
                        size=record.get("size"), scale=record.get("scale")) from exc
        try:
            made.document = sk_or_doc  # provenance link back to the source document
        except Exception:  # noqa: BLE001 - the sheet itself is already real; the link is best effort
            pass
        sheet_uuid = str(getattr(made, "uuid", "") or "")
        if not sheet_uuid or _UUID_RE.match(sheet_uuid) is None:
            raise _fail("bad_sheet", "make_sheet returned a sheet without a usable uuid",
                        sheet_uuid=repr(sheet_uuid))
        created.append(sheet_uuid)
    else:
        made = sheet
        sheet_uuid = str(getattr(sheet, "uuid", "") or "")
        if not sheet_uuid:
            raise _fail("bad_sheet", "sheet has no uuid", value_type=type(sheet).__name__)

    report = _apply_layers(made, _layer_list(record.get("layers")))

    if sheet is None:
        entry = dict(_sheet.sheet_to_l1(made))
        entry.update({
            "template": str(record.get("name")),
            "template_uuid": str(record.get("uuid")),
            "template_format": FORMAT,
            "template_revision": int(record.get("revision") or 0),
            "standard": str(record.get("standard") or DEFAULT_STANDARD),
            "language": str(record.get("language") or DEFAULT_LANGUAGE),
            "size": str(record.get("size") or DEFAULT_SIZE),
            "scale": str(record.get("scale") or DEFAULT_SCALE),
            "title_block_fields": fields,
            "template_layers": [str(item.get("name")) for item in record.get("layers") or []],
        })
        if record.get("projection_angle"):
            entry["projection_angle"] = str(record["projection_angle"])
        _register(_bucket(host, SHEET_BUCKET), entry)

    binding_uuid = str(_uuid.uuid4())
    _register(_bucket(host, BINDING_BUCKET), {
        "uuid": binding_uuid,
        "kind": BINDING_KIND,
        "format": FORMAT,
        "op": "apply_drawing_template",
        "template": str(record.get("name")),
        "template_uuid": str(record.get("uuid")),
        "sheet": sheet_uuid,
        "drawing": sheet_uuid,
        "associative": True,
        "target": [str(record.get("uuid"))],
        "title_block_fields": fields,
        "custom_properties": dict(record.get("properties") or {}),
        "annotation_defaults": dict(record.get("annotation_defaults") or {}),
        "layers": report["applied"],
        "layers_applied": report["layers_applied"],
        "layers_verified_on_sheet": report["verified_on_sheet"],
        "sheet_layers_before": report["sheet_layers_before"],
        "sheet_layers_after": report["sheet_layers_after"],
    })
    created.append(binding_uuid)
    if make_default:
        _set_default(host, str(record.get("name")))
    return created


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "define_drawing_template": define_drawing_template,
    "apply_drawing_template": apply_drawing_template,
}


def module_facts() -> dict[str, Any]:
    """Machine readable facts about this piece module (no geometry, no side effects)."""
    return {
        "family": FAMILY,
        "module": "sw_drawings__w25",
        "feature": "engineering drawing template (drwdot): reusable sheet format, title block, layers",
        "format": FORMAT,
        "template_bucket": TEMPLATE_BUCKET,
        "binding_kind": BINDING_KIND,
        "ops": sorted(OPERATIONS),
        "ops_total": len(OPERATIONS),
        "layer_roles": list(LAYER_ROLES),
        "layer_registrar": "sw_drawings._register_extra_layer" if _swd is not None else "unavailable",
    }


# --------------------------------------------------------------------------------------
# self test (hard assertions, no pytest, no external files)
# --------------------------------------------------------------------------------------


def _raises(fn: Callable[[], Any], expected_reason: str) -> bool:
    try:
        fn()
    except CadError as exc:
        for attr in ("reason", "code"):
            if str(getattr(exc, attr, "") or "") == expected_reason:
                return True
        return False
    return False


def selftest() -> int:
    checks: list[str] = []

    def check(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError("SELFTEST FAILED: %s" % label)
        checks.append(label)

    doc: dict[str, Any] = {"sketches": [], "features": []}
    spec = {
        "name": "acme-a3-first",
        "standard": "gbt",
        "size": "A3",
        "scale": "1:2",
        "projection_angle": "first",
        "title_block": {"drawing_no": "D-001", "material": "45", "title": "bracket"},
        "properties": {"company": "ACME"},
        "layers": [{"name": "DIM", "role": "dimension"}, {"name": "HIDDEN_X", "role": "visible"}],
        "annotation_defaults": {"text_height_nm": 4 * NM_PER_MM},
    }

    out = define_drawing_template(doc, **spec)
    check(len(out) == 1 and _UUID_RE.match(out[0]) is not None, "define returns one valid template uuid")
    registry = doc[TEMPLATE_BUCKET]
    check(len(registry) == 1 and registry[0]["uuid"] == out[0], "template registered exactly once")
    check(registry[0]["format"] == FORMAT and registry[0]["scale"] == "1:2"
          and registry[0]["size"] == "A3" and registry[0]["title_block_fields"]["drawing_no"] == "D-001",
          "record carries drwdot format, canonical scale and title block fields")
    check(doc[DEFAULT_KEY] == "acme-a3-first", "first template becomes the document default")

    again = define_drawing_template(doc, **spec)
    check(again == out and len(doc[TEMPLATE_BUCKET]) == 1 and doc[TEMPLATE_BUCKET][0]["revision"] == 2,
          "same-name redefine updates in place (uuid stable, revision bumped, no duplicate)")

    applied = apply_drawing_template(doc, name="acme-a3-first")
    check(len(applied) == 2 and applied[0] == doc["sheets"][-1]["uuid"],
          "apply returns [sheet_uuid, binding_uuid] and registers the sheet in the document")
    entry = doc["sheets"][-1]
    check(entry["template"] == "acme-a3-first" and entry["template_uuid"] == out[0]
          and entry["size"] == "A3" and entry["scale"] == "1:2"
          and entry["title_block_fields"]["material"] == "45",
          "sheet L1 entry carries template provenance and title block data")
    binding = doc["annotations"][-1]
    check(binding["uuid"] == applied[1] and binding["kind"] == BINDING_KIND
          and binding["layers_applied"] == 2 and binding["custom_properties"]["company"] == "ACME",
          "binding object is resolvable and records the two applied layers")

    auto = apply_drawing_template(doc)
    check(len(auto) == 2 and len(doc["sheets"]) == 2 and auto[0] == doc["sheets"][-1]["uuid"],
          "apply without a name uses the document default template")

    check(_raises(lambda: define_drawing_template(doc, name="bad", scale="1:0"), "bad_scale"),
          "non-positive scale is rejected with bad_scale")
    check(_raises(lambda: define_drawing_template(doc, size="A3"), "bad_template_name"),
          "template without a name is rejected with bad_template_name")
    check(_raises(lambda: define_drawing_template(doc, name="bad", layers=[{"role": "dimension"}]),
                  "bad_layer"), "layer without a name is rejected with bad_layer")
    check(_raises(lambda: apply_drawing_template(doc, name="does-not-exist"), "template_not_found"),
          "unknown template name is rejected with template_not_found")
    check(_raises(lambda: define_drawing_template({"entities": []}, name="x"), "no_document_host"),
          "bare sketch host is rejected with no_document_host")
    check(module_facts()["ops_total"] == 2 and "x_drawing_templates" in str(module_facts()["template_bucket"]),
          "module_facts reports the two operations and the template bucket")

    print("SELFTEST sw_drawings__w25 asserts=%d passed=%d all_passed=True" % (len(checks), len(checks)))
    print("FEATURE_OK family=sw_drawings op=define_drawing_template verdict=PASS")
    print("FEATURE_OK family=sw_drawings op=apply_drawing_template verdict=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(selftest())


# --- READONLY / query 档声明（放回席第三批 2026-09-30，见 tests/test_features_query_declare.py）---
# `define_drawing_template` 会真写文档桶 `x_drawing_templates`（同名模板原地更新 ⇒ revision+1），
# 该后果落在文档对象上、不改 L1 文本 ⇒ 本片不是 readonly：如实去掉 readonly 声明（与 plot 同款处置）。
READONLY = False
OPERATIONS_META = {
    "define_drawing_template": {"tier": "write", "returns": "data"},
}
