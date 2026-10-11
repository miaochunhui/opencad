"""cadcore.draw.titleblock —— A12 图框/标题栏**自绘**：版面几何 + 逐字段查语言包的标题栏文案。

一句话用途
----------
给 `draw.sheet` 提供图框与标题栏：`frame_curves()` 出**图纸空间**的框线（外框 + 标题栏分格），
`frame_texts()` 出标签与字段值两列文字，`title_block()` 按 `{字段名: 成品文案}` 渲染标题栏。

为什么这样设计
--------------
1. **自绘，不搬第三方图库**（`ARCHITECTURE.md` §5）：线条与文字都由本模块按自己的版面常数算出来，
   不引入任何图形库；`MVP-INTERFACES.md` §B A12 的"读标准档，不搬第三方图库"就落在这里。
2. **标准档只当数据读、缺字段一律 NA**：五份标准档的 `title_block_template` / `sheet_sizes` /
   `text_height_series` / `line_widths` 当前都是 `null`（未取到标准正文）⇒ 本模块用**自己的版面常数**兜底，
   并在 `sheet.text_height_source` / `sheet.line_width_source` / `sheet_facts()['standard_na_fields']`
   里如实标注来源；**不声称符合任何标准档的标题栏**，也绝不往数据里编一个值。
3. **逐字段查语言包 key**：字段清单是数据（`DEFAULT_TITLE_BLOCK_FIELDS`），每个字段一个**取值 key**
   （`std.<标准>` / `std.projection.<角>` / `unit.<单位>` / `doc.<标题>` …）与一个**标签 key**（`tb.<字段>`）；
   渲染时全部走 `sheet.tr(language, key)`，源码里没有任何面向用户的文案字面量。
4. **缺数据记 NA，不留空串**：文档里没有的字段（材料/质量/设计/审核…）渲染成语言包里的 `status.na`，
   而不是空字符串 —— 空串在图纸上与"忘了填"无法区分，NA 才说得清"这里没有数据"。
5. **标题栏语言 ≠ 图纸语言 ≠ 界面语言**：`title_block(language=…)` 的 `language` 是**入参**，
   改它不动标准档、不动图纸尺寸、不动进程级活动语言（`drawing-generation.md` §4 的"图纸内容语言"）。

对外契约（照抄 `MVP-INTERFACES.md` §B A12）
-----------------------------------------
`title_block(sheet, *, doc, language) -> dict[str, str]`；另附 `title_block_keys` / `frame_curves` /
`frame_texts` 三个只读辅助（`draw.sheet.iter_entities` 用它们拼实体流）。
"""

from __future__ import annotations

from typing import Any, Mapping

from .sheet import (
    NA,
    NM_PER_MM,
    UNMEASURED,
    Curve,
    LAYER_FRAME,
    LAYER_TEXT,
    Sheet,
    TextItem,
    lookup_translation,
    tr,
)

__all__ = [
    "DEFAULT_TITLE_BLOCK_FIELDS",
    "FRAME_MARGIN_NM",
    "TITLE_BLOCK_WIDTH_NM",
    "TITLE_ROW_HEIGHT_NM",
    "TITLE_HEADER_HEIGHT_NM",
    "LABEL_COLUMN_WIDTH_NM",
    "frame_curves",
    "frame_texts",
    "title_block",
    "title_block_keys",
]

# ── 版面常数（**本模块自己的版面**，不是标准档取值；标准档对应字段当前为 null/NA）──────────
FRAME_MARGIN_NM: int = 10 * NM_PER_MM
TITLE_BLOCK_WIDTH_NM: int = 180 * NM_PER_MM
TITLE_ROW_HEIGHT_NM: int = 8 * NM_PER_MM
TITLE_HEADER_HEIGHT_NM: int = 12 * NM_PER_MM
LABEL_COLUMN_WIDTH_NM: int = 35 * NM_PER_MM
TITLE_TEXT_HEIGHT_NM: int = 5 * NM_PER_MM
LABEL_TEXT_HEIGHT_NM: int = 2_500_000
VALUE_TEXT_HEIGHT_NM: int = 3_500_000
CELL_PADDING_NM: int = 1_500_000
FIELDS_PER_ROW: int = 2
EMPTY_CELL: str = NA

#: 标题栏字段清单：`(字段名, 取值 key 模板)`。模板里的 `{standard}` / `{angle}` / `{unit}` 由
#: `title_block_keys()` 从**数据**（标准档 / 文档）填进去 —— 标准代号与语言码都不出现在代码分支里。
#: 标签 key 一律是 `tb.<字段名>`（与语言包键空间约定 `<前缀>.<名>` 一致，见 `data/i18n/*.json` 的 `_doc`）。
DEFAULT_TITLE_BLOCK_FIELDS: tuple[tuple[str, str], ...] = (
    ("title", "tb.title"),
    ("drawing_no", "tb.drawing_no"),
    ("standard", "std.{standard}"),
    ("projection", "std.projection.{angle}"),
    ("scale", "tb.scale"),
    ("unit", "unit.{unit}"),
    ("sheet", "tb.sheet_of"),
    ("material", "tb.material"),
    ("mass", "tb.mass"),
    ("designer", "tb.designer"),
    ("checker", "tb.checker"),
    ("date", "tb.date"),
    ("revision", "tb.revision"),
    ("company", "tb.company"),
)

#: 字段值来源：文档 `meta` 里的同名键（有就用，没有就 NA）。
_META_FIELDS: frozenset[str] = frozenset({"material", "mass", "designer", "checker",
                                          "date", "revision", "company"})


# --------------------------------------------------------------------------------------
# 取值 key（数据 → key，**不是**数据 → 文案）
# --------------------------------------------------------------------------------------


def title_block_keys(sheet: Sheet, doc: Any = None) -> dict[str, str]:
    """`{字段名: 语言包 key}`（L1 `sheet.title_block` 存的就是它，**文本存 key 不存译文**）。

    key 的选择完全由数据决定：标准档 id → `std.<id>`（该键不存在时退 `std.<id>.full`，再退裸 id）；
    投影角 → `std.projection.<角>`（标准档未核标 ⇒ 该字段记 `NA`，不给 key）；单位 → `unit.<单位>`；
    名称 → 文档 `meta.title_key`（**图纸名称的文本真源在文档里**，没有才退字段标签 key）。
    """
    source = doc if doc is not None else sheet.document
    title_key = _meta_get(_doc_meta(source), "title_key", None)
    keys: dict[str, str] = {}
    for field_id, template in DEFAULT_TITLE_BLOCK_FIELDS:
        if template == "tb.title":
            has_title_key = isinstance(title_key, str) and bool(title_key.strip())
            keys[field_id] = str(title_key) if has_title_key else template
        elif template == "std.{standard}":
            keys[field_id] = _standard_key(sheet.standard_profile)
        elif template == "std.projection.{angle}":
            angle = sheet.projection_angle
            keys[field_id] = f"std.projection.{angle}" if angle else NA
        elif template == "unit.{unit}":
            keys[field_id] = f"unit.{sheet.unit}" if sheet.unit else NA
        else:
            keys[field_id] = template
    return keys


def _standard_key(standard_id: str) -> str:
    """标准字段用哪个 key：短名优先，短名不存在才用全名，再不行退回裸 id（**不编文案**）。"""
    short = f"std.{standard_id}"
    full = f"{short}.full"
    for candidate in (short, full):
        text, _code = lookup_translation("en", candidate)
        if text is not None:
            return candidate
    return short


# --------------------------------------------------------------------------------------
# 标题栏渲染
# --------------------------------------------------------------------------------------


def title_block(sheet: Sheet, *, doc: Any, language: str) -> dict[str, str]:
    """渲染标题栏 → `{字段名: 该语言下的成品文案}`，并把结果与出处记回 `sheet`。

    每个字段三步：①定 key（`title_block_keys`）②取值（文档真值 / 图纸真值）③查语言包；
    取不到值 ⇒ `status.na`（**NA = 不适用/无数据**），语言包整条链都没有该 key ⇒ 由 key 派生的人读串。
    """
    keys = title_block_keys(sheet, doc)
    values: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}
    for field_id, _template in DEFAULT_TITLE_BLOCK_FIELDS:
        key = keys.get(field_id, NA)
        text, source, resolved = _field_value(sheet, doc, language, field_id, key)
        values[field_id] = text
        meta[field_id] = {"key": key, "label_key": f"tb.{field_id}", "source": source,
                          "lang": resolved if resolved else UNMEASURED}
    sheet.title_block_keys = keys
    sheet.title_fields = values
    sheet.title_block_meta = meta
    sheet.title_block_lang = next((entry["lang"] for entry in meta.values()
                                   if isinstance(entry["lang"], str)), None)
    sheet.title_block_lang_requested = str(language)
    return values


def _field_value(sheet: Sheet, doc: Any, language: str, field_id: str,
                 key: str) -> tuple[str, str, str | None]:
    """一个字段的 `(文案, 出处, 命中的语言码)`；**取不到值一律 NA**，不留空串。"""
    meta = _doc_meta(doc)
    if field_id == "title":
        title_key = _meta_get(meta, "title_key", None)
        if isinstance(title_key, str) and title_key.strip():
            text, code = lookup_translation(language, title_key)
            if text is not None:
                return (text, f"document:meta.title_key={title_key}", code)
        literal = _meta_get(meta, "title", None)
        if isinstance(literal, str) and literal.strip():
            return (literal, "document:meta.title", None)
        return (_na(language), "na:no title in document meta", _na_lang(language))
    if field_id == "drawing_no":
        doc_uuid = _meta_get(meta, "doc_uuid", None)
        if isinstance(doc_uuid, str) and doc_uuid.strip():
            return (doc_uuid, "document:meta.doc_uuid", None)
        return (_na(language), "na:no doc_uuid", _na_lang(language))
    if field_id == "standard":
        if key and key != NA:
            text, code = lookup_translation(language, key)
            if text is not None:
                return (text, f"standard_profile:{sheet.standard_profile}", code)
        return (sheet.standard_profile or _na(language),
                "raw:standard id (no language pack key)", None)
    if field_id == "projection":
        if sheet.projection_angle:
            text, code = lookup_translation(language, key)
            if text is not None:
                return (text, "standard_profile:projection_angle", code)
            return (sheet.projection_angle, "raw:projection angle", None)
        return (_na(language), "na:projection_angle is null in the standard profile", _na_lang(language))
    if field_id == "scale":
        return (sheet.scale, "sheet:scale", None)
    if field_id == "unit":
        text, code = lookup_translation(language, key)
        if text is not None:
            return (text, "document:unit", code)
        return (sheet.unit or _na(language), "raw:unit", None)
    if field_id == "sheet":
        text, code = lookup_translation(language, "tb.sheet_of", current=1, total=1)
        if text is not None:
            return (text, "sheet:single sheet", code)
        return (_na(language), "na:no tb.sheet_of key", _na_lang(language))
    if field_id == "date":
        for candidate in ("date", "created_at"):
            value = _meta_get(meta, candidate, None)
            if isinstance(value, str) and value.strip():
                return (value, f"document:meta.{candidate}", None)
        return (_na(language), "na:no date in document meta", _na_lang(language))
    if field_id in _META_FIELDS:
        value = _meta_get(meta, field_id, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (str(value), f"document:meta.{field_id}", None)
        if isinstance(value, str) and value.strip():
            return (value, f"document:meta.{field_id}", None)
        return (_na(language), f"na:no meta.{field_id}", _na_lang(language))
    return (_na(language), "na:unknown field", _na_lang(language))


def _na(language: str) -> str:
    """`NA` 的**语言包**写法（`status.na`）；连这个键都没有才退回裸 `NA` 字面量。"""
    text, _code = lookup_translation(language, "status.na")
    return text if text is not None else NA


def _na_lang(language: str) -> str | None:
    """NA 值命中的语言码（用于 `titleblock_lang`：值本身来自语言包 ⇒ 语言确实是这套）。"""
    _text, code = lookup_translation(language, "status.na")
    return code


def _doc_meta(doc: Any) -> Any:
    if doc is None:
        return None
    if isinstance(doc, Mapping):
        return doc.get("meta")
    return getattr(doc, "meta", None)


def _meta_get(meta: Any, name: str, default: Any = None) -> Any:
    if meta is None:
        return default
    if isinstance(meta, Mapping):
        value = meta.get(name, default)
    else:
        value = getattr(meta, name, default)
    return default if value is None else value


# --------------------------------------------------------------------------------------
# 版面几何（图纸空间，整数纳米）
# --------------------------------------------------------------------------------------


def _title_block_box(sheet: Sheet) -> tuple[int, int, int, int]:
    """标题栏外框 `(x0, y0, x1, y1)`：贴图纸内框的**右下角**。"""
    x1 = max(FRAME_MARGIN_NM, sheet.width_nm - FRAME_MARGIN_NM)
    y0 = FRAME_MARGIN_NM
    x0 = max(FRAME_MARGIN_NM, x1 - TITLE_BLOCK_WIDTH_NM)
    y1 = y0 + _title_block_height_nm()
    return (x0, y0, x1, y1)


def _title_block_height_nm() -> int:
    rows = _field_rows()
    return TITLE_HEADER_HEIGHT_NM + rows * TITLE_ROW_HEIGHT_NM


def _field_rows() -> int:
    fields = max(0, len(DEFAULT_TITLE_BLOCK_FIELDS) - 1)  # 第一格（名称）单独占表头行
    return max(1, (fields + FIELDS_PER_ROW - 1) // FIELDS_PER_ROW)


def frame_curves(sheet: Sheet) -> list[Curve]:
    """图框 + 标题栏的框线（图纸空间）。全部落在 `FRAME` 图层，线型 CONTINUOUS。"""
    curves: list[Curve] = []
    left, bottom = FRAME_MARGIN_NM, FRAME_MARGIN_NM
    right = max(FRAME_MARGIN_NM, sheet.width_nm - FRAME_MARGIN_NM)
    top = max(FRAME_MARGIN_NM, sheet.height_nm - FRAME_MARGIN_NM)
    curves.extend(_rect(left, bottom, right, top))

    x0, y0, x1, y1 = _title_block_box(sheet)
    curves.extend(_rect(x0, y0, x1, y1))
    header_y = y1 - TITLE_HEADER_HEIGHT_NM
    curves.append(_hline(x0, x1, header_y))
    cell_width = (x1 - x0) // FIELDS_PER_ROW
    rows = _field_rows()
    for row in range(rows + 1):
        y = header_y - row * TITLE_ROW_HEIGHT_NM
        curves.append(_hline(x0, x1, y))
    for column in range(1, FIELDS_PER_ROW):
        curves.append(_vline(x0 + column * cell_width, header_y, y0))
    for row in range(rows):
        row_top = header_y - row * TITLE_ROW_HEIGHT_NM
        row_bottom = row_top - TITLE_ROW_HEIGHT_NM
        for column in range(FIELDS_PER_ROW):
            cell_x = x0 + column * cell_width
            curves.append(_vline(cell_x + LABEL_COLUMN_WIDTH_NM, row_top, row_bottom))
    return curves


def _rect(x0: int, y0: int, x1: int, y1: int) -> list[Curve]:
    return [_hline(x0, x1, y0), _hline(x0, x1, y1), _vline(x0, y0, y1), _vline(x1, y0, y1)]


def _hline(x0: int, x1: int, y: int) -> Curve:
    return Curve.line((x0, y), (x1, y), layer=LAYER_FRAME, name=f"H{abs(x0)}_{abs(y)}",
                      source="titleblock")


def _vline(x: int, y0: int, y1: int) -> Curve:
    return Curve.line((x, y0), (x, y1), layer=LAYER_FRAME, name=f"V{abs(x)}_{abs(y0)}",
                      source="titleblock")


def frame_texts(sheet: Sheet) -> list[TextItem]:
    """标题栏文字：表头行是**名称字段的值**，其余每个字段画**标签**（`tb.<字段>` key）与**值**两列。

    还没渲染过（`sheet.title_fields` 为空）时，值一律显示语言包里的 `status.na` ——
    "没渲染"与"有数据"在图上必须看得出区别。
    """
    language = sheet.title_block_lang or sheet.language
    x0, y0, x1, y1 = _title_block_box(sheet)
    header_y = y1 - TITLE_HEADER_HEIGHT_NM
    items: list[TextItem] = []
    title_value = sheet.title_fields.get("title") or _na(language)
    items.append(TextItem(
        text=title_value,
        at_nm=(x0 + CELL_PADDING_NM, header_y + (TITLE_HEADER_HEIGHT_NM - TITLE_TEXT_HEIGHT_NM) // 2),
        height_nm=TITLE_TEXT_HEIGHT_NM, layer=LAYER_TEXT, field_key="title", lang=language,
        anchor="start", role="title",
    ))
    cell_width = (x1 - x0) // FIELDS_PER_ROW
    rows = _field_rows()
    field_index = 1
    for row in range(rows):
        row_top = header_y - row * TITLE_ROW_HEIGHT_NM
        for column in range(FIELDS_PER_ROW):
            if field_index >= len(DEFAULT_TITLE_BLOCK_FIELDS):
                break
            field_id = DEFAULT_TITLE_BLOCK_FIELDS[field_index][0]
            field_index += 1
            cell_x = x0 + column * cell_width
            baseline = row_top - TITLE_ROW_HEIGHT_NM + (TITLE_ROW_HEIGHT_NM - VALUE_TEXT_HEIGHT_NM) // 2
            items.append(TextItem(
                text=tr(language, f"tb.{field_id}"),
                at_nm=(cell_x + CELL_PADDING_NM, baseline),
                height_nm=LABEL_TEXT_HEIGHT_NM, layer=LAYER_TEXT, field_key=field_id,
                lang=language, anchor="start", role="label",
            ))
            items.append(TextItem(
                text=sheet.title_fields.get(field_id) or _na(language),
                at_nm=(cell_x + LABEL_COLUMN_WIDTH_NM + CELL_PADDING_NM, baseline),
                height_nm=VALUE_TEXT_HEIGHT_NM, layer=LAYER_TEXT, field_key=field_id,
                lang=language, anchor="start", role="value",
            ))
    return items


def layout_facts(sheet: Sheet) -> dict[str, Any]:
    """版面读数（判定行取证用）：标题栏外框与行数，全部由上面的常数算出来。"""
    box = _title_block_box(sheet)
    return {
        "frame_margin_nm": FRAME_MARGIN_NM,
        "title_block_box_nm": list(box),
        "title_block_rows": _field_rows(),
        "title_block_fields": [field for field, _key in DEFAULT_TITLE_BLOCK_FIELDS],
        "title_block_source": "builtin_layout(standard profile field 'title_block_template' is null)",
        "frame_curves": len(frame_curves(sheet)),
        "frame_texts": len(frame_texts(sheet)),
        "empty_cell_marker": EMPTY_CELL,
    }
