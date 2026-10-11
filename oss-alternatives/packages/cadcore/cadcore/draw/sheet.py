"""cadcore.draw.sheet —— A11 出图装配层：把内核投影装配成图纸（视图 + 尺寸 + 图层/线型 + 导出）。

一句话用途
----------
`make_sheet` 按**制图标准档**与**图纸语言**两个独立入参建一张图纸，`add_view` 把内核投影出来的
2D 几何按可见/隐藏分图层放上图纸，`add_dimensions` 从模型几何**现算**尺寸标注，`export_sheet`
把它导出成 DXF / SVG / PDF（PDF：外部后端优先，缺席时内建写出器可出真 PDF，`pdf_builtin=False` 缺省仍显式降级 SVG），
`sheet_facts` 给判定行提供口径一致的读数。

为什么这样设计
--------------
1. **标准与语言是两条互不推导的轴**（`MVP-SPEC` §M4、`I18N-DESIGN` §6）：`standard` 只从
   `data/standards/<id>.json` 取版面/投影角等数据，`language` 只从语言包取文案；**标准代号只许出现在数据里**
   —— 本模块没有任何按标准代号分支的写法，也没有把语言映射成标准的代码路径（选中文不会自动切标准）。
2. **尺寸值只许现算**：每个标注的数值都由该视图的几何（必要时重新向内核投影一次）当场量出来，
   标注对象里存的是量出来的 `value_nm`，没有任何写死的尺寸数字；数量不足 `min_count` 就抛错，不凑数、不静默。
3. **不许静默出空图**：内核缺席、`available()=False`、没有 `project2d` 能力、投影结果为空 —— 四种情况
   全部抛异常（`DependencyMissing` / `DrawingError`），绝不返回一张空视图。
4. **降级必须可观测**：DXF 优先走 A13 的 `io.dxf_bridge.sheet_to_dxf`（唯一权威出口），它没落盘时用本模块
   内置的极简 ASCII 写出器，`sheet_facts()["dxf_writer"]` 与导出记录里写明用的哪一个；PDF 走**外部后端优先**
   （`register_pdf_backend` / `CADCORE_PDF_BACKEND`），外部后端缺席时 `pdf_builtin=True` 用本模块内建写出器出真 PDF、
   缺省 `pdf_builtin=False` 仍如实降级 SVG —— **实际格式**同时出现在返回值扩展名、`ExportResult` 与 `sheet_facts()` 三处。
   写出件的长度单位是**文档单位（毫米/英寸）**而不是纳米 —— 纳米只是内存里的整数真源，一张以纳米为
   图纸单位的 DXF 在任何 CAD 里都是废图；DXF 头里的单位变量（`$INSUNITS` 等）映射未核标 ⇒ 一律不写并记进诊断。
5. **三态记账**（全包统一口径）：`0` = 真为零 / `-1` = 未采（`UNMEASURED`）/ `"NA"` = 不适用。
   没有视图时 `hidden_layer` 记 `NA` 而不是 `absent`；标题栏还没渲染时 `titleblock_lang` 记 `-1` 而不是套用图纸语言。
6. **确定性**：实体 uuid 一律由 `uuid5(命名空间, 内容)` 生成，导出件里不含时间戳 ⇒ 同一份输入两次生成
   逐字节相同（缓存可全删重建这条判据靠它）。
7. **文案全部走语言包 key**，源码内不出现任何面向用户的中文字面量；标准档未取到标准正文的字段一律按 `NA`
   处理并登记进 `standard_na_fields`（**不许编数**，见 `I18N-DESIGN` §6）。

对外契约（照抄 `MVP-INTERFACES.md` §B A11/A12）
---------------------------------------------
`make_sheet` · `add_view` · `add_dimensions` · `title_block`（实现落在 `draw/titleblock.py`，此处转出）·
`export_sheet` · `sheet_facts`；另附 `sheet_to_l1` / `iter_entities` / `tr` / `register_pdf_backend` /
`render_pdf_pages`（按布局分页）/ `export_pdf_pages`（多页合一个文件）等辅助。
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
import uuid as _uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cadcore.io.write_guard import guard_output

LOG = logging.getLogger("cadcore.draw.sheet")

__all__ = [
    "NA",
    "UNMEASURED",
    "NM_PER_MM",
    "DEFAULT_SIZE",
    "DEFAULT_SCALE",
    "DEFAULT_MIN_DIMENSIONS",
    "DIM_TEXT_DECIMALS",
    "LAYER_VISIBLE",
    "LAYER_HIDDEN",
    "LAYER_DIMENSION",
    "LAYER_FRAME",
    "LAYER_TEXT",
    "LINETYPE_CONTINUOUS",
    "LINETYPE_HIDDEN",
    "VIEW_NAMES",
    "VIEW_DIRECTIONS",
    "VIEW_LABEL_KEYS",
    "PROJECTION_ANGLE_LABELS",
    "BUILTIN_SHEET_SIZES_MM",
    "DXF_ACADVER",
    "CadError",
    "DependencyMissing",
    "DrawingError",
    "DimensionShortfall",
    "Curve",
    "TextItem",
    "View",
    "Dimension",
    "ExportResult",
    "Sheet",
    "add_dimensions",
    "add_view",
    "clear_caches",
    "error_types",
    "export_pdf_pages",
    "export_sheet",
    "iter_entities",
    "load_profile",
    "make_sheet",
    "pdf_backend_available",
    "pdf_builtin_available",
    "register_pdf_backend",
    "render_pdf_page",
    "render_pdf_pages",
    "sheet_facts",
    "sheet_to_l1",
    "standard_ids",
    "title_block",
    "tr",
]

# ── 全包统一的三态口径（与 `kernel/subproc.py` 的 `UNMEASURED`/`NA` 同值同义）──────────────
#: 未采：没测到（≠ 测到 0）。
UNMEASURED: int = -1

#: 不适用：该字段对这个对象没有意义（≠ 未采、≠ 0）。
NA: str = "NA"

#: 唯一长度真源 = 整数纳米（`MVP-INTERFACES.md` §C）。A4 `units.py` 落盘后以其常量为准，这里只是兜底。
NM_PER_MM: int = 1_000_000

#: 源串语言（语言包回退链的链尾）。标准档/文件名之类的错误发生在"还没有图纸语言"时，用它出文案。
DEFAULT_LANGUAGE_FALLBACK: str = "en"

DEFAULT_SIZE: str = "A3"
DEFAULT_SCALE: str = "1:1"

#: `add_dimensions` 的缺省下限（判定行 G2 要求 `dim_entities>=8`）。
DEFAULT_MIN_DIMENSIONS: int = 8

#: 尺寸文字的小数位 —— **显示精度**（不是标准档取值，标准档精度字段为 NA；出处记进 facts）。
DIM_TEXT_DECIMALS: int = 2

# ── 图层与线型（本模块的版面约定；DXF 词汇表里的名字，不是标准档取值）────────────────────
LAYER_VISIBLE: str = "VISIBLE"
LAYER_HIDDEN: str = "HIDDEN"
LAYER_DIMENSION: str = "DIMENSIONS"
LAYER_FRAME: str = "FRAME"
LAYER_TEXT: str = "TITLEBLOCK"
LAYER_ANNOTATION: str = "NOTES"
LINETYPE_CONTINUOUS: str = "CONTINUOUS"
LINETYPE_HIDDEN: str = "DASHED"

#: 标注文本区（`doc.annotations` 的无位置文本落在这块**派生版面**上）：左下角起、逐行向上。
#: 为什么是派生版面：L1 标注只有 `target` 引用、**没有位置字段**，画到引用几何的哪个点是编的
#: （与 `sketch.dimensions` 同一条"不伪造坐标"口径）；所以给一块固定文本区，文本逐字进图纸。
ANNOTATION_MARGIN_NM: int = 5 * NM_PER_MM
ANNOTATION_LINE_STEP_NM: int = 6 * NM_PER_MM
#: 标注文本缺省字高（标准档 `text_height_series` 为 NA 时的内置标称值，与标题栏同源）。
ANNOTATION_TEXT_HEIGHT_NM: int = 3_500_000

#: 视图名（契约字面量的唯一副本）→ 投影方向向量（与 `tests/data/plate.json` 的 `direction` 一致）。
VIEW_NAMES: tuple[str, ...] = ("front", "top", "right", "iso")
VIEW_DIRECTIONS: dict[str, tuple[int, int, int]] = {
    "front": (0, -1, 0),
    "top": (0, 0, 1),
    "right": (1, 0, 0),
    "iso": (1, -1, 1),
}
#: 视图名 → 语言包 key（视图标签只在 UI 里出现，图纸几何本身不带文字）。
VIEW_LABEL_KEYS: dict[str, str] = {
    "front": "cmd.view.front",
    "top": "cmd.view.top",
    "right": "cmd.view.right",
    "iso": "cmd.view.iso",
}
#: 标准档 `projection_angle` 取值 → 语言包 key（数据值 → key 的映射，**不含任何标准代号**）。
PROJECTION_ANGLE_LABELS: dict[str, str] = {
    "first": "std.projection.first",
    "third": "std.projection.third",
}
#: 判定行口径：`first_angle` / `third_angle`（`MVP-SPEC` §M4 与 `drawing-generation.md` §5 的期望判词）。
PROJECTION_ANGLE_FACTS: dict[str, str] = {"first": "first_angle", "third": "third_angle"}

#: 内置纸幅（A 系列公称尺寸，毫米）。**来源标注为内置公开常数**：五份标准档的 `sheet_sizes` 当前都是
#: `null`（未取到标准正文）⇒ 这里只在标准档缺席时兜底，并把来源写进 facts（`sheet_size_source`），
#: 绝不声称"符合某标准档的幅面"。
BUILTIN_SHEET_SIZES_MM: dict[str, tuple[int, int]] = {
    "A0": (1189, 841),
    "A1": (841, 594),
    "A2": (594, 420),
    "A3": (420, 297),
    "A4": (297, 210),
}

#: DXF 版本名 → `$ACADVER`（DXF 格式自身的版本登记，与制图标准无关）。未知版本名 ⇒ 抛错，不猜。
DXF_ACADVER: dict[str, str] = {
    "R12": "AC1009",
    "R13": "AC1012",
    "R14": "AC1014",
    "R2000": "AC1015",
    "R2004": "AC1018",
    "R2007": "AC1021",
    "R2010": "AC1024",
    "R2013": "AC1027",
    "R2018": "AC1032",
}

#: PDF 是**可选后端**：本模块从不 import 任何第三方包，只认显式注册的渲染回调。
_PDF_BACKENDS: list[Any] = []
ENV_PDF_BACKEND: str = "CADCORE_PDF_BACKEND"

#: 尺寸类别 → 语言包 key（图面上的类型标签；数值本身是数据，不是文案）。
DIMENSION_LABEL_KEYS: dict[str, str] = {
    "linear": "cmd.dimension.linear",
    "radial": "cmd.dimension.radial",
    "diameter": "cmd.dimension.diameter",
    "angular": "cmd.dimension.angular",
}

#: 本模块的 uuid5 命名空间（固定字面量 ⇒ 同一份输入得到同一批 uuid）。
_uuid.NAMESPACE_CADCORE_DRAW = _uuid.UUID("6f1d3b2a-9c47-5e11-8a30-0d5b7c9e4f21")


# ── A3 显式降级信号（真件优先；A3 尚未落盘时用同名同语义的兜底，落盘后自动让位）──────────────
from ..errors import CadError, DependencyMissing
#: 标注文本的**唯一读法**真源（`args["text"]` 与顶层 `text` 两种写侧形态都认下来）。
from ..docmodel import annotation_text


class DrawingError(CadError):
    """出图装配层的失败信号（几何读不懂、尺寸不够、档案名不合法等）。"""


class DimensionShortfall(DrawingError):
    """尺寸数量不达 `min_count`。**单独成类**：判定行要能把它和别的出图失败区分开。"""


def error_types() -> dict[str, type]:
    """当前生效的错误类（真件优先）。测试与判定行用它核对"抛的是哪一类"。"""
    return {
        "CadError": CadError,
        "DependencyMissing": DependencyMissing,
        "DrawingError": DrawingError,
        "DimensionShortfall": DimensionShortfall,
    }


# --------------------------------------------------------------------------------------
# 可选兄弟模块：按需导入、缺席降级、**绝不在 import 期硬失败**
# --------------------------------------------------------------------------------------

_OPTIONAL_CACHE: dict[str, Any] = {}
_OPTIONAL_ERRORS: dict[str, str] = {}


def _optional_module(dotted: str) -> Any | None:
    """按需导入同包兄弟模块；**只有"这个模块本身不在"才算缺席**，它内部缺依赖一律上抛。

    结果进 `_OPTIONAL_ERRORS`（`sheet_facts()["dependencies"]` 会原样带出去）⇒ 降级可观测，不静默。
    """
    if dotted in _OPTIONAL_CACHE:
        return _OPTIONAL_CACHE[dotted]
    name = dotted
    if not name.startswith("cadcore."):
        name = "cadcore." + name
    module: Any | None = None
    try:
        module = importlib.import_module(name)
        _OPTIONAL_ERRORS.pop(dotted, None)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing not in (name, name.rsplit(".", 1)[0]):
            # 兄弟模块**在里面**缺依赖 ⇒ 那是真故障，不许被我们伪装成"它没落盘"
            _OPTIONAL_ERRORS[dotted] = f"import failed inside module: {exc}"
            LOG.warning("optional sibling %s failed to import: %s", dotted, exc)
            raise
        _OPTIONAL_ERRORS[dotted] = "absent"
    except Exception as exc:  # noqa: BLE001 - 兄弟模块坏了也不该让出图层 import 期崩掉
        _OPTIONAL_ERRORS[dotted] = f"{type(exc).__name__}: {exc}"
        LOG.warning("optional sibling %s is unusable: %s", dotted, exc)
    _OPTIONAL_CACHE[dotted] = module
    return module


def clear_caches() -> None:
    """清空可选模块与语言包缓存（测试/热重载用；不影响已建好的 Sheet）。"""
    _OPTIONAL_CACHE.clear()
    _OPTIONAL_ERRORS.clear()
    _CATALOG_CACHE.clear()


def dependency_state() -> dict[str, str]:
    """可选依赖的当前状态（`loaded` / `absent` / 失败原因），判定行取证用。"""
    out: dict[str, str] = {}
    for key in ("cadcore.errors", "cadcore.units", "cadcore.i18n.catalog",
                "cadcore.i18n.standards", "cadcore.io.dxf_bridge"):
        module = _OPTIONAL_CACHE.get(key)
        out[key] = "loaded" if module is not None else _OPTIONAL_ERRORS.get(key, "not_loaded_yet")
    return out


def _fail(kind: str, message: str, **attrs: Any) -> Any:
    """抛当前生效的错误类；`attrs` 挂到异常对象上（判定行可机读取证）。"""
    exc_type = error_types().get(kind, DrawingError)
    exc = exc_type(message)
    for name, value in attrs.items():
        try:
            setattr(exc, name, value)
        except Exception:  # noqa: BLE001 - 挂观测属性失败不许影响抛错本身
            pass
    raise exc


# --------------------------------------------------------------------------------------
# 语言包取词（图纸语言独立于标准档，也独立于进程级活动语言）
# --------------------------------------------------------------------------------------

_CATALOG_CACHE: dict[str, tuple[tuple[str, Any], ...]] = {}


def _chain_catalogs(language: str) -> tuple[tuple[str, Any], ...]:
    """`language` 回退链上**精确命中**的语言包（逐级），结果缓存。

    刻意不用 `catalog.t()`：那个查的是进程级活动语言，而图纸语言是**每次调用显式给的入参**
    （一张中文界面的机器完全可能出英文图纸，见 `drawing-generation.md` §4）。
    """
    key = str(language or "")
    if key in _CATALOG_CACHE:
        return _CATALOG_CACHE[key]
    module = _optional_module("cadcore.i18n.catalog")
    found: list[tuple[str, Any]] = []
    if module is not None:
        try:
            chain = list(module.resolve_chain(key))
        except Exception as exc:  # noqa: BLE001 - 语言层坏了不该让出图崩掉
            LOG.warning("resolve_chain(%r) failed: %s", key, exc)
            chain = [key]
        for code in chain:
            try:
                catalog = module.load_catalog(code)
            except Exception as exc:  # noqa: BLE001 - 该级没有/坏掉 ⇒ 继续往链下走
                LOG.debug("catalog %r not usable at level %r: %s", key, code, exc)
                continue
            if catalog is not None and getattr(catalog, "code", None) == code:
                found.append((code, catalog))
    result = tuple(found)
    _CATALOG_CACHE[key] = result
    return result


def _derive_source_string(key: str) -> str:
    """由 key 派生人读兜底串（语言包整条链都没有时用；**绝不把 key 名摆给用户**）。"""
    module = _optional_module("cadcore.i18n.catalog")
    derive = getattr(module, "_derive_source_string", None) if module is not None else None
    if callable(derive):
        try:
            return str(derive(key))
        except Exception:  # noqa: BLE001 - 兜底串生成失败也要有输出
            pass
    tail = key.rsplit(".", 1)[-1] if key else ""
    text = re.sub(r"\s+", " ", tail.replace("_", " ").replace("-", " ")).strip()
    return (text[:1].upper() + text[1:]) if text else (key.strip() or "?")


def _format_text(template: str, key: str, args: Mapping[str, Any]) -> str:
    if not args:
        return template
    try:
        return template.format(**args)
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        LOG.warning("i18n: placeholder substitution failed for %r (%s) -- raw text returned", key, exc)
        return template


def lookup_translation(language: str, key: str, **args: Any) -> tuple[str | None, str | None]:
    """沿 `language` 的回退链逐级查 key；返回 `(译文, 命中的语言码)`，整条链都没有 ⇒ `(None, None)`。

    `0` 与空串的区分在这里很重要：语言包里的空串等同"没有"（继续往链下走），
    所以调用方拿到的 `None` 只表示**真没有**，不会把"空译文"伪装成命中。
    """
    for code, catalog in _chain_catalogs(language):
        value = getattr(catalog, "strings", {}).get(key)
        if isinstance(value, str) and value.strip():
            return (_format_text(value, key, args), code)
    return (None, None)


def tr(language: str, key: str, /, **args: Any) -> str:
    """取图纸语言下的文案：查不到 ⇒ 回落 `en` 源串 ⇒ 仍无 ⇒ 由 key 派生的人读串（**绝不返回空串/key 名**）。"""
    text, code = lookup_translation(language, key, **args)
    if text is not None:
        return text
    derived = _derive_source_string(key)
    LOG.warning(
        "draw: no translation for %r in language %r (chain=%s) -- served derived string %r",
        key, language, [code for code, _ in _chain_catalogs(language)], derived,
    )
    return derived


def language_resolved(language: str) -> str | None:
    """`language` 在当前语言包目录里**实际**解析到的语言码（None ⇒ 未采）。"""
    catalogs = _chain_catalogs(language)
    return catalogs[0][0] if catalogs else None


# --------------------------------------------------------------------------------------
# 制图标准档：**只读数据**，标准代号只出现在数据文件名里
# --------------------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_STANDARDS_DIR = _DATA_DIR / "standards"


def standard_ids() -> tuple[str, ...]:
    """可用标准档 id：优先 A17 `i18n.standards.STANDARD_IDS`，缺席则扫 `data/standards/*.json`。

    **两处都不写死标准代号** —— 加一档标准 = 往目录丢一个 JSON。
    """
    module = _optional_module("cadcore.i18n.standards")
    if module is not None:
        declared = getattr(module, "STANDARD_IDS", None)
        if declared:
            return tuple(str(item) for item in declared)
    if _STANDARDS_DIR.is_dir():
        return tuple(sorted(path.stem for path in _STANDARDS_DIR.glob("*.json")
                            if not path.name.startswith("_")))
    return ()


def load_profile(standard_id: str) -> tuple[dict[str, Any], str]:
    """读一档标准档 → `(数据, 来源)`。id 不在数据清单里 ⇒ 抛错（**不猜、不回落**）。

    优先 A17 `i18n.standards.get_profile`（同一批数据文件的正式入口），它缺席时本模块直接读同一份
    `data/standards/<id>.json`（字段级契约见 `MVP-INTERFACES.md` §C，两处读的是**同一真源**）。
    """
    known = standard_ids()
    if str(standard_id) not in known:
        _fail(
            "DrawingError",
            tr(DEFAULT_LANGUAGE_FALLBACK, "err.unknown_standard", standard=str(standard_id)),
            standard=str(standard_id), known=list(known),
        )
    module = _optional_module("cadcore.i18n.standards")
    getter = getattr(module, "get_profile", None) if module is not None else None
    if callable(getter):
        try:
            profile = getter(str(standard_id))
        except Exception as exc:  # noqa: BLE001 - A17 坏了 ⇒ 退回读同一份数据文件
            LOG.warning("i18n.standards.get_profile(%r) failed: %s", standard_id, exc)
            profile = None
        if profile is not None:
            return (_profile_to_dict(profile), "cadcore.i18n.standards.get_profile")
    path = _STANDARDS_DIR / f"{standard_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(
            "DrawingError",
            tr(DEFAULT_LANGUAGE_FALLBACK, "err.parse_failed", source=str(path), detail=str(exc)),
            standard=str(standard_id),
        )
    if not isinstance(payload, Mapping):
        _fail(
            "DrawingError",
            tr(DEFAULT_LANGUAGE_FALLBACK, "err.parse_failed", source=str(path),
               detail="standard profile root is not a JSON object"),
            standard=str(standard_id),
        )
    return (dict(payload), f"data/standards/{standard_id}.json")


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    """A17 的 `StandardProfile` → 普通 dict（认 `as_dict()` / Mapping / dataclass 三种形态）。"""
    as_dict = getattr(profile, "as_dict", None)
    if callable(as_dict):
        try:
            payload = as_dict()
            if isinstance(payload, Mapping):
                return dict(payload)
        except Exception:  # noqa: BLE001 - 转换失败就退回属性表
            pass
    if isinstance(profile, Mapping):
        return dict(profile)
    data = getattr(profile, "__dict__", None)
    if isinstance(data, Mapping):
        return {key: value for key, value in data.items() if not key.startswith("_")}
    return {}


def _field(source: Any, name: str, default: Any = None) -> Any:
    """从 dict / 对象里取一个字段（两种形态都要认，A17 与裸 JSON 的字段名一致）。"""
    if source is None:
        return default
    if isinstance(source, Mapping):
        value = source.get(name, default)
    else:
        value = getattr(source, name, default)
    return default if value is None else value


def profile_na_fields(profile: Mapping[str, Any]) -> list[str]:
    """标准档自己登记的 `na_fields`（未取到标准正文的字段）；没有就返回空列表（不是"全都核过了"）。"""
    raw = profile.get("na_fields")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [str(item) for item in raw]
    return []


# --------------------------------------------------------------------------------------
# 几何：2D 曲线（模型空间，一律整数纳米）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Curve:
    """一条 2D 几何。`points` 是折线点串（line=2 点、polyline=n 点），圆弧另存心/半径/起止角。

    坐标一律**模型空间整数纳米**；落到图纸上的位置由 `View.at_nm` + `View.scale` 现算
    （图纸空间不是真源 ⇒ 换比例不需要改几何）。
    """

    kind: str
    points: tuple[tuple[int, int], ...] = ()
    center: tuple[int, int] | None = None
    radius_nm: int | None = None
    start_deg: float | None = None
    end_deg: float | None = None
    layer: str = LAYER_VISIBLE
    name: str = ""
    source: str = "projection"

    # ── 构造 ────────────────────────────────────────────────────────────────────────
    @classmethod
    def line(cls, p1: Sequence[int], p2: Sequence[int], **kw: Any) -> "Curve":
        return cls(kind="line", points=(_as_point(p1), _as_point(p2)), **kw)

    @classmethod
    def polyline(cls, points: Iterable[Sequence[int]], **kw: Any) -> "Curve":
        return cls(kind="polyline", points=tuple(_as_point(p) for p in points), **kw)

    @classmethod
    def circle(cls, center: Sequence[int], radius_nm: int, **kw: Any) -> "Curve":
        return cls(kind="circle", center=_as_point(center), radius_nm=int(radius_nm), **kw)

    @classmethod
    def arc(cls, center: Sequence[int], radius_nm: int, start_deg: float, end_deg: float,
            **kw: Any) -> "Curve":
        return cls(kind="arc", center=_as_point(center), radius_nm=int(radius_nm),
                   start_deg=float(start_deg), end_deg=float(end_deg), **kw)

    # ── 量测 ────────────────────────────────────────────────────────────────────────
    def all_points(self) -> tuple[tuple[int, int], ...]:
        """该曲线参与包围盒计算的点集（圆/圆弧用极值四点近似，避免引入浮点真源）。"""
        if self.kind == "circle" and self.center is not None and self.radius_nm is not None:
            cx, cy = self.center
            r = self.radius_nm
            return ((cx - r, cy - r), (cx + r, cy + r))
        if self.kind == "arc" and self.center is not None and self.radius_nm is not None:
            return _arc_extreme_points(self.center, self.radius_nm, self.start_deg, self.end_deg)
        return self.points

    def bounds_nm(self) -> tuple[int, int, int, int] | None:
        """`(xmin, ymin, xmax, ymax)`；没有点 ⇒ `None`（不返回全 0 冒充）。"""
        pts = self.all_points()
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def length_nm(self) -> int | None:
        """长度（纳米，整数四舍五入）；圆弧按弧长，圆按周长。量不出来 ⇒ `None`。"""
        if self.kind == "circle" and self.radius_nm is not None:
            return int(round(2.0 * math.pi * self.radius_nm))
        if self.kind == "arc" and self.radius_nm is not None:
            sweep = _sweep_deg(self.start_deg, self.end_deg)
            return int(round(math.radians(sweep) * self.radius_nm))
        if len(self.points) < 2:
            return None
        total = 0.0
        for (x1, y1), (x2, y2) in zip(self.points, self.points[1:]):
            total += math.hypot(x2 - x1, y2 - y1)
        return int(round(total))

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "layer": self.layer, "name": self.name,
                               "source": self.source}
        if self.points:
            out["points_nm"] = [[int(x), int(y)] for x, y in self.points]
        if self.center is not None:
            out["center_nm"] = [int(self.center[0]), int(self.center[1])]
        if self.radius_nm is not None:
            out["radius_nm"] = int(self.radius_nm)
        if self.start_deg is not None:
            out["start_deg"] = float(self.start_deg)
        if self.end_deg is not None:
            out["end_deg"] = float(self.end_deg)
        return out


@dataclass(frozen=True)
class TextItem:
    """图面上的一段文字（标签/字段值）。`text` 已是**图纸语言下的成品串**，不再二次翻译。"""

    text: str
    at_nm: tuple[int, int]
    height_nm: int
    layer: str = LAYER_TEXT
    field_key: str = ""
    lang: str = ""
    anchor: str = "start"
    role: str = "value"

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "at_nm": [int(self.at_nm[0]), int(self.at_nm[1])],
                "height_nm": int(self.height_nm), "layer": self.layer, "field": self.field_key,
                "lang": self.lang, "anchor": self.anchor, "role": self.role}


def _as_point(value: Any) -> tuple[int, int]:
    """任意"点"表示 → `(x, y)` 整数纳米；认 2 元序列与带 `x`/`y` 的对象。"""
    if isinstance(value, Mapping):
        return (int(value.get("x", value.get("x_nm", 0))), int(value.get("y", value.get("y_nm", 0))))
    if isinstance(value, (str, bytes)) or value is None:
        return (0, 0)
    try:
        items = list(value)
    except TypeError:
        return (int(getattr(value, "x", 0)), int(getattr(value, "y", 0)))
    if len(items) >= 2:
        return (int(items[0]), int(items[1]))
    if len(items) == 1:
        inner = items[0]
        if isinstance(inner, (list, tuple)) and len(inner) >= 2:
            return (int(inner[0]), int(inner[1]))
    return (0, 0)


def _arc_extreme_points(center: tuple[int, int], radius: int, start_deg: float | None,
                        end_deg: float | None) -> tuple[tuple[int, int], ...]:
    """圆弧包围盒的极值点：两端点 + 落在弧内的四个象限点。"""
    cx, cy = center
    s = float(start_deg or 0.0)
    e = float(end_deg if end_deg is not None else s + 360.0)
    pts: list[tuple[int, int]] = []
    for angle in (s, e):
        rad = math.radians(angle)
        pts.append((int(round(cx + radius * math.cos(rad))), int(round(cy + radius * math.sin(rad)))))
    for quadrant in (0.0, 90.0, 180.0, 270.0):
        if _angle_within(quadrant, s, e):
            rad = math.radians(quadrant)
            pts.append((int(round(cx + radius * math.cos(rad))), int(round(cy + radius * math.sin(rad)))))
    return tuple(pts) if pts else ((cx - radius, cy - radius), (cx + radius, cy + radius))


def _sweep_deg(start_deg: float | None, end_deg: float | None) -> float:
    s = float(start_deg or 0.0)
    e = float(end_deg if end_deg is not None else s + 360.0)
    sweep = (e - s) % 360.0
    return 360.0 if abs(sweep) < 1e-9 else sweep


def _angle_within(angle: float, start: float, end: float) -> bool:
    span = (end - start) % 360.0
    if span <= 1e-9:
        span = 360.0
    return ((angle - start) % 360.0) <= span + 1e-9


def _scale_nm(value: int, num: int, den: int) -> int:
    """整数纳米按比例 `num/den` 缩放（四舍五入，向远离 0 的方向）——**不引入浮点真源**。"""
    scaled = int(value) * int(num)
    if scaled >= 0:
        return (scaled + den // 2) // den
    return -((-scaled + den // 2) // den)


def _parse_scale(scale: str) -> tuple[int, int]:
    """`"1:1"` / `"1:2"` / `"2:1"` → `(num, den)`；不合法 ⇒ 抛错（不回落成 1:1 假装没事）。"""
    text = str(scale or "").strip().replace("/", ":")
    parts = [part.strip() for part in text.split(":")]
    if len(parts) != 2 or not all(parts):
        _fail("DrawingError", tr(DEFAULT_LANGUAGE_FALLBACK, "err.parse_failed",
                                 source="scale", detail=str(scale)), scale=str(scale))
    try:
        num, den = int(parts[0]), int(parts[1])
    except ValueError:
        _fail("DrawingError", tr(DEFAULT_LANGUAGE_FALLBACK, "err.parse_failed",
                                 source="scale", detail=str(scale)), scale=str(scale))
    if num <= 0 or den <= 0:
        _fail("DrawingError", tr(DEFAULT_LANGUAGE_FALLBACK, "err.out_of_range", value=text,
                                 min=1, max="+inf"), scale=str(scale))
    return (num, den)


# --------------------------------------------------------------------------------------
# 投影结果归一化（认 A6/A8 的 Projection 与通用映射/序列两种形态）
# --------------------------------------------------------------------------------------

_VISIBLE_BUCKET_KEYS = ("visible", "visible_edges", "visibleEdges", "visible_curves_geom",
                        "edges", "outline", "solid")
_HIDDEN_BUCKET_KEYS = ("hidden", "hidden_edges", "hiddenEdges", "hidden_curves_geom", "dashed")
_HIDDEN_KINDS = ("hidden", "invisible", "dashed", "hidden_line")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_bucket(value: Any) -> list[Any] | None:
    """一个值能不能当"几何桶"：必须可枚举，且**不是** bool/str/Mapping。

    `Projection.hidden` 是个 **bool 标志**（"要不要隐藏线"），不是几何桶 —— 不挡住它就会被
    `list(True)` 打成 TypeError。这条判据就是为那次实撞写的。
    """
    if value is None or isinstance(value, (bool, str, bytes, Mapping)):
        return None
    try:
        return list(value)
    except TypeError:
        return None


def _first_bucket(source: Any, names: Sequence[str]) -> tuple[str | None, list[Any] | None]:
    for name in names:
        bucket = _as_bucket(_get(source, name, None))
        if bucket is not None:
            return (name, bucket)
    return (None, None)


def _split_projection(projection: Any) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """投影结果 → `(可见项, 隐藏项, 元信息)`。

    认三种形态（**同一份几何、三种来源，读法都在这里，别处不许再解一遍**）：
    ①A8/A6 的 `Projection`：`curves=[{kind: visible|hidden, points_nm: [...]}, …]`；
    ②通用映射/对象：`visible`/`hidden`（或 `visible_edges`/`hidden_edges` 等别名）两个桶；
    ③裸序列：整串都当可见几何。
    """
    if projection is None:
        return ([], [], {"shape": "none"})
    visible_key, visible = _first_bucket(projection, _VISIBLE_BUCKET_KEYS)
    hidden_key, hidden = _first_bucket(projection, _HIDDEN_BUCKET_KEYS)
    if visible is not None or hidden is not None:
        return (visible or [], hidden or [],
                {"shape": "buckets", "visible_key": visible_key, "hidden_key": hidden_key})
    curves = _as_bucket(_get(projection, "curves", None))
    if curves is not None:
        visible_items, hidden_items = [], []
        for item in curves:
            kind = str(_get(item, "kind", "") or _get(item, "type", "")).strip().lower()
            (hidden_items if kind in _HIDDEN_KINDS else visible_items).append(item)
        return (visible_items, hidden_items,
                {"shape": "curves+kind", "hidden_key": "curves" if hidden_items else None})
    if isinstance(projection, Sequence) and not isinstance(projection, (str, bytes)):
        return (list(projection), [], {"shape": "sequence"})
    raise _unsupported_projection(projection)


def _unsupported_projection(projection: Any) -> CadError:
    """构造"投影结果读不懂"的错误（单独成函数：错误文案只有一处）。"""
    detail = f"unsupported projection result of type {type(projection).__name__}"
    exc_type = error_types().get("DrawingError", DrawingError)
    exc = exc_type(detail)
    setattr(exc, "detail", detail)
    return exc


def _curve_from_item(item: Any, *, layer: str, name: str, source: str) -> Curve:
    """一项投影几何 → `Curve`（读不懂就抛错，**不静默丢几何**）。"""
    if isinstance(item, Curve):
        return replace(item, layer=layer, name=item.name or name, source=item.source or source)
    kind = str(_get(item, "kind", "") or _get(item, "type", "")).strip().lower()
    if kind in _HIDDEN_KINDS:
        kind = ""
    points = _get(item, "points_nm", None)
    if points is None:
        points = _get(item, "points", None)
    if points is None:
        p1 = _get(item, "p1", None) or _get(item, "start", None)
        p2 = _get(item, "p2", None) or _get(item, "end", None)
        if p1 is not None and p2 is not None:
            points = [p1, p2]
    if points is None:
        x1, y1 = _get(item, "x1", None), _get(item, "y1", None)
        x2, y2 = _get(item, "x2", None), _get(item, "y2", None)
        if None not in (x1, y1, x2, y2):
            points = [[x1, y1], [x2, y2]]
    center = _get(item, "center_nm", None) or _get(item, "center", None)
    radius = _get(item, "radius_nm", None)
    if radius is None:
        radius = _get(item, "radius", None)
    start_deg = _get(item, "start_deg", None)
    end_deg = _get(item, "end_deg", None)

    if kind == "circle" and center is not None and radius is not None:
        return Curve.circle(center, int(radius), layer=layer, name=name, source=source)
    if kind == "arc" and center is not None and radius is not None:
        return Curve.arc(center, int(radius), float(start_deg or 0.0), float(end_deg or 0.0),
                         layer=layer, name=name, source=source)
    if points is not None:
        pts = tuple(_as_point(point) for point in points)
        if len(pts) < 2:
            raise _unsupported_projection(item)
        # 内核若**直接给了**圆心/半径就原样留着（拟合是兜底，不是首选：能不猜就不猜）
        shape_kwargs: dict[str, Any] = {"layer": layer, "name": name, "source": source}
        if center is not None:
            shape_kwargs["center"] = _as_point(center)
        if radius is not None:
            shape_kwargs["radius_nm"] = int(radius)
        if kind == "line" or (len(pts) == 2 and kind in ("", "polyline", "segment")):
            return Curve(kind="line", points=pts, **shape_kwargs)
        return Curve(kind="polyline", points=pts, **shape_kwargs)
    if center is not None and radius is not None:
        return Curve.circle(center, int(radius), layer=layer, name=name, source=source)
    raise _unsupported_projection(item)


def _name_curves(items: Sequence[Any]) -> list[str]:
    """按种类给曲线起**拓扑命名**（`Edge1` / `Circle2` / `Arc1` / `Polyline3`）。

    引用一律 `(uuid, sub_shape_name)`（`MVP-INTERFACES.md` §C 禁下标引用）；这里给的就是子形状名，
    形制与主流 CAD 的 `Edge1`/`Face2` 一致。
    """
    counters: dict[str, int] = {}
    names: list[str] = []
    for item in items:
        kind = str(_get(item, "kind", "") or _get(item, "type", "")).strip().lower()
        if kind == "line":
            prefix = "Edge"
        elif kind == "circle":
            prefix = "Circle"
        elif kind == "arc":
            prefix = "Arc"
        else:
            prefix = "Edge" if (_get(item, "points_nm", None) or _get(item, "points", None)) else "Curve"
        counters[prefix] = counters.get(prefix, 0) + 1
        names.append(f"{prefix}{counters[prefix]}")
    return names


# --------------------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------------------


@dataclass
class View:
    """图纸上的一个视图。

    `visible` / `hidden` 存的是**模型空间**几何（整数纳米），图纸位置由 `at_nm`（外包框左下角落点）与
    `scale` 现算 ⇒ 换比例、挪位置都不动几何，尺寸数值也永远是模型真值。
    """

    uuid: str
    name: str
    at_nm: tuple[int, int]
    scale: tuple[int, int]
    scale_text: str
    hidden: bool
    visible: tuple[Curve, ...] = ()
    hidden_curves: tuple[Curve, ...] = ()
    visible_layer: str = LAYER_VISIBLE
    hidden_layer: str = LAYER_HIDDEN
    shape: Any = None
    shape_repr: str = ""
    projection_source: str = ""
    projection_shape: str = ""
    bounds_model_nm: tuple[int, int, int, int] | None = None

    def curves(self, *, include_hidden: bool = True) -> tuple[Curve, ...]:
        if include_hidden:
            return tuple(self.visible) + tuple(self.hidden_curves)
        return tuple(self.visible)

    def to_sheet_nm(self, point: Sequence[int]) -> tuple[int, int]:
        """模型空间点 → 图纸空间点（`at_nm` 是外包框左下角的落点）。"""
        ox, oy = self.bounds_model_nm[:2] if self.bounds_model_nm else (0, 0)
        num, den = self.scale
        return (self.at_nm[0] + _scale_nm(int(point[0]) - ox, num, den),
                self.at_nm[1] + _scale_nm(int(point[1]) - oy, num, den))

    def bounds_sheet_nm(self) -> tuple[int, int, int, int] | None:
        if self.bounds_model_nm is None:
            return None
        x0, y0, x1, y1 = self.bounds_model_nm
        p0 = self.to_sheet_nm((x0, y0))
        p1 = self.to_sheet_nm((x1, y1))
        return (min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))

    def as_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "direction": list(VIEW_DIRECTIONS.get(self.name, (0, 0, 0))),
            "at_nm": [int(self.at_nm[0]), int(self.at_nm[1])],
            "scale": self.scale_text,
            "hidden": bool(self.hidden),
            "visible_layer": self.visible_layer,
            "hidden_layer": self.hidden_layer,
            "visible_entities": len(self.visible),
            "hidden_entities": len(self.hidden_curves),
            "bounds_model_nm": list(self.bounds_model_nm) if self.bounds_model_nm else None,
            "bounds_sheet_nm": list(self.bounds_sheet_nm() or ()),
            "shape_repr": self.shape_repr,
            "projection_source": self.projection_source,
            "curves": [curve.as_dict() for curve in self.curves()],
        }


@dataclass(frozen=True)
class Dimension:
    """一个尺寸标注实体。`value_nm` 是**当场量出来的模型真值**（与图纸比例无关）。"""

    uuid: str
    kind: str
    value_nm: int
    text: str
    text_key: str
    view_uuid: str
    at_nm: tuple[int, int]
    refs: tuple[tuple[str, str], ...] = ()
    layer: str = LAYER_DIMENSION
    terminator: str | None = None
    measured_from: str = ""
    label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "kind": self.kind,
            "value_nm": int(self.value_nm),
            "text": self.text,
            "text_key": self.text_key,
            "view_uuid": self.view_uuid,
            "at_nm": [int(self.at_nm[0]), int(self.at_nm[1])],
            "refs": [[str(uuid_part), str(sub)] for uuid_part, sub in self.refs],
            "layer": self.layer,
            "terminator": self.terminator,
            "measured_from": self.measured_from,
        }


@dataclass(frozen=True)
class ExportResult:
    """一次导出的取证记录。**降级要看得见**：`requested != actual` 就是降级。"""

    requested: str
    actual: str
    path: str
    downgraded: bool
    reason: str = ""
    writer: str = ""
    entities: int = 0
    bytes: int = 0
    titleblock_rendered: bool = False

    @property
    def format(self) -> str:
        return self.actual

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "actual": self.actual,
            "path": self.path,
            "downgraded": bool(self.downgraded),
            "reason": self.reason,
            "writer": self.writer,
            "entities": int(self.entities),
            "bytes": int(self.bytes),
            "titleblock_rendered": bool(self.titleblock_rendered),
        }


@dataclass
class Sheet:
    """一张图纸（L1 `sheets[]` 的绘制期形态；`sheet_to_l1()` 给出可落库的映射）。"""

    uuid: str
    doc_uuid: str | None
    standard_profile: str
    size: str
    scale: str
    scale_ratio: tuple[int, int]
    language: str
    unit: str
    width_nm: int
    height_nm: int
    standard_source: str = ""
    sheet_size_source: str = ""
    projection_angle: str | None = None
    projection_angle_source: str = ""
    decimal_separator: str | None = None
    decimal_separator_source: str = ""
    text_height_nm: int = 0
    text_height_source: str = ""
    line_width_source: str = ""
    standard_na_fields: tuple[str, ...] = ()
    views: list[View] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    layers: dict[str, dict[str, Any]] = field(default_factory=dict)
    title_block_keys: dict[str, str] = field(default_factory=dict)
    title_fields: dict[str, str] = field(default_factory=dict)
    title_block_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    title_block_lang: str | None = None
    title_block_lang_requested: str | None = None
    document: Any = None
    exports: list[ExportResult] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    _seq: int = 0

    # ── 内部：确定性 uuid（同一份输入 ⇒ 同一批 uuid）────────────────────────────────────
    def next_uuid(self, kind: str, *parts: Any) -> str:
        self._seq += 1
        payload = "|".join([self.uuid, kind, str(self._seq), *[str(item) for item in parts]])
        return str(_uuid.uuid5(_uuid.NAMESPACE_CADCORE_DRAW, payload))

    def note(self, message: str) -> None:
        """记一条**可机读**的诊断（降级/异常都要留痕，不许静默）。"""
        text = str(message)
        if text not in self.diagnostics:
            self.diagnostics.append(text)
        LOG.warning("draw: %s", text)

    # ── 只读聚合 ────────────────────────────────────────────────────────────────────
    def visible_curves(self, *, include_hidden: bool = False) -> tuple[Curve, ...]:
        out: list[Curve] = []
        for view in self.views:
            out.extend(view.curves(include_hidden=include_hidden))
        return tuple(out)

    def hidden_curve_count(self) -> int:
        return sum(len(view.hidden_curves) for view in self.views)

    def hidden_layer_declared(self) -> bool:
        return any(view.hidden for view in self.views) or self.hidden_curve_count() > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "standard_profile": self.standard_profile,
            "language": self.language,
            "size": self.size,
            "scale": self.scale,
            "unit": self.unit,
            "width_nm": self.width_nm,
            "height_nm": self.height_nm,
            "views": [view.as_dict() for view in self.views],
            "dimensions": [dim.as_dict() for dim in self.dimensions],
            "diagnostics": list(self.diagnostics),
            "exports": [item.as_dict() for item in self.exports],
        }


# --------------------------------------------------------------------------------------
# A11: make_sheet
# --------------------------------------------------------------------------------------


def make_sheet(doc: Any, *, standard: str, language: str, size: str = DEFAULT_SIZE,
               scale: str = DEFAULT_SCALE) -> Sheet:
    """建一张图纸：标准档与图纸语言**独立入参，禁互相推导**。

    - `standard` 只决定标准档数据（投影角、幅面若已核标、…）；`language` 只决定文案来自哪套语言包。
      **换语言不改标准、换标准不改语言**（`I18N-DESIGN` §6 的三轴+第四轴解耦）。
    - 幅面：优先标准档 `sheet_sizes`（当前五档均为 `null` ⇒ 未核标），否则用内置 A 系列公称尺寸兜底，
      来源写进 `sheet_size_source`，**不声称符合任何标准档**。
    - 投影角取标准档 `projection_angle`；该档为 `null`（未核标）时记 `NA`，**不编一个值出来**。
    - 语言解析不到时**不阻断出图**（语言只影响标题栏文案），但记诊断 + `language_resolved=None` 留痕。
    """
    standard_id = str(standard or "").strip()
    profile, standard_source = load_profile(standard_id)
    num, den = _parse_scale(scale)
    size_token = str(size or DEFAULT_SIZE).strip()

    width_nm, height_nm, size_source = _resolve_sheet_size(profile, size_token)
    angle_raw = _field(profile, "projection_angle", None)
    projection_angle = str(angle_raw) if angle_raw else None
    na_fields = profile_na_fields(profile)
    separator = _field(profile, "decimal_separator", None)
    decimal_separator = str(separator) if separator else None
    text_height_nm, text_height_source = _resolve_text_height(profile)

    unit = str(_doc_get(doc, "unit", None) or "mm")
    doc_uuid = _doc_get(_doc_get(doc, "meta", None), "doc_uuid", None)
    sheet = Sheet(
        uuid="",  # 下面按内容补（uuid5 ⇒ 确定性）
        doc_uuid=str(doc_uuid) if doc_uuid else None,
        standard_profile=standard_id,
        size=size_token,
        scale=str(scale),
        scale_ratio=(num, den),
        language=str(language or DEFAULT_LANGUAGE_FALLBACK),
        unit=unit,
        width_nm=width_nm,
        height_nm=height_nm,
        standard_source=standard_source,
        sheet_size_source=size_source,
        projection_angle=projection_angle,
        projection_angle_source="standard_profile:projection_angle" if projection_angle else NA,
        decimal_separator=decimal_separator,
        decimal_separator_source=("standard_profile:decimal_separator" if decimal_separator
                                  else f"builtin_default({NA}: standard profile field is null)"),
        text_height_nm=text_height_nm,
        text_height_source=text_height_source,
        line_width_source=f"builtin_default({NA}: standard profile field is null)",
        standard_na_fields=tuple(na_fields),
        document=doc,
    )
    sheet.uuid = str(_uuid.uuid5(
        _uuid.NAMESPACE_CADCORE_DRAW,
        "|".join(["sheet", sheet.doc_uuid or "no-doc", standard_id, sheet.language,
                  size_token, str(scale)]),
    ))
    if projection_angle is None:
        sheet.note("standard profile field 'projection_angle' is null (not verified) -- recorded as NA")
    if decimal_separator is None:
        sheet.note("standard profile field 'decimal_separator' is null -- dimension text uses '.' (builtin default)")
    if unit != "mm":
        sheet.note(f"document unit is {unit!r} -- dimension text is rendered in that unit")
    resolved = language_resolved(sheet.language)
    if resolved is None:
        sheet.note(f"no language catalog resolved for {sheet.language!r} -- "
                   "title block text falls back to derived source strings")
    elif resolved != sheet.language:
        sheet.note(f"language {sheet.language!r} resolved to {resolved!r} through the fallback chain")
    _register_layer(sheet, LAYER_VISIBLE, LINETYPE_CONTINUOUS, "visible geometry")
    _register_layer(sheet, LAYER_DIMENSION, LINETYPE_CONTINUOUS, "dimensions")
    _register_layer(sheet, LAYER_FRAME, LINETYPE_CONTINUOUS, "sheet frame")
    _register_layer(sheet, LAYER_TEXT, LINETYPE_CONTINUOUS, "title block text")
    _register_layer(sheet, LAYER_ANNOTATION, LINETYPE_CONTINUOUS, "annotation / notes text")
    titleblock = _titleblock_module()
    if titleblock is not None:
        sheet.title_block_keys = dict(titleblock.title_block_keys(sheet))
    else:  # pragma: no cover - 本模块与 titleblock 同笔提交；分支只为"文件被删"时仍可解释
        sheet.note("draw/titleblock.py is unavailable -- title block fields are unmeasured")
    return sheet


def _register_layer(sheet: Sheet, name: str, linetype: str, role: str) -> None:
    if name not in sheet.layers:
        sheet.layers[name] = {"name": name, "linetype": linetype, "role": role,
                              "linewidth_source": sheet.line_width_source}


def _resolve_sheet_size(profile: Mapping[str, Any], size_token: str) -> tuple[int, int, str]:
    """幅面 → `(宽, 高, 来源)`：标准档 `sheet_sizes` 优先，缺席用内置公称尺寸（来源如实标注）。"""
    declared = profile.get("sheet_sizes")
    parsed = _sheet_size_from_profile(declared, size_token)
    if parsed is not None:
        return (parsed[0], parsed[1], "standard_profile:sheet_sizes")
    builtin = BUILTIN_SHEET_SIZES_MM.get(size_token.upper())
    if builtin is not None:
        return (builtin[0] * NM_PER_MM, builtin[1] * NM_PER_MM,
                f"builtin_public_nominal({NA}: standard profile field 'sheet_sizes' is null)")
    _fail("DrawingError", tr(DEFAULT_LANGUAGE_FALLBACK, "err.out_of_range", value=size_token,
                             min=", ".join(sorted(BUILTIN_SHEET_SIZES_MM)), max=""), size=size_token)


def _sheet_size_from_profile(declared: Any, size_token: str) -> tuple[int, int] | None:
    """标准档 `sheet_sizes` → 纳米尺寸；字段形态未冻结 ⇒ 认映射与条目列表两种（都读不懂就返回 None）。"""
    entry: Any = None
    if isinstance(declared, Mapping):
        for key, value in declared.items():
            if str(key).strip().upper() == size_token.upper():
                entry = value
                break
    elif isinstance(declared, Sequence) and not isinstance(declared, (str, bytes)):
        for item in declared:
            name = str(_get(item, "name", "") or _get(item, "id", "") or _get(item, "size", "")).strip()
            if name.upper() == size_token.upper():
                entry = item
                break
    if entry is None:
        return None
    if isinstance(entry, Mapping):
        width = entry.get("width_nm", entry.get("width"))
        height = entry.get("height_nm", entry.get("height"))
        if width is None or height is None:
            width_mm = entry.get("width_mm")
            height_mm = entry.get("height_mm")
            if width_mm is None or height_mm is None:
                return None
            return (int(round(float(width_mm) * NM_PER_MM)), int(round(float(height_mm) * NM_PER_MM)))
        scale = 1 if "width_nm" in entry else NM_PER_MM
        return (int(round(float(width) * scale)), int(round(float(height) * scale)))
    if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)) and len(entry) >= 2:
        return (int(round(float(entry[0]) * NM_PER_MM)), int(round(float(entry[1]) * NM_PER_MM)))
    return None


def _resolve_text_height(profile: Mapping[str, Any]) -> tuple[int, str]:
    """字高：标准档 `text_height_series` 命中就**贴到系列里**，否则用内置标称值并记来源。"""
    nominal_nm = 3_500_000
    series = profile.get("text_height_series")
    values: list[float] = []
    if isinstance(series, (int, float)) and not isinstance(series, bool):
        values = [float(series)]
    elif isinstance(series, Sequence) and not isinstance(series, (str, bytes)):
        values = [float(item) for item in series
                  if isinstance(item, (int, float)) and not isinstance(item, bool)]
    if not values:
        return (nominal_nm, f"builtin_default({NA}: standard profile field 'text_height_series' is null)")
    nominal_mm = nominal_nm / NM_PER_MM
    picked = min((value for value in values if value >= nominal_mm), default=max(values))
    return (int(round(picked * NM_PER_MM)), "standard_profile:text_height_series")


def _doc_get(doc: Any, name: str, default: Any = None) -> Any:
    """从 Document（对象或 L1 dict）取字段；取不到返回 default（不猜）。"""
    if doc is None:
        return default
    return _field(doc, name, default)


def _titleblock_module() -> Any | None:
    return _optional_module("cadcore.draw.titleblock")


# --------------------------------------------------------------------------------------
# A11: add_view
# --------------------------------------------------------------------------------------


def add_view(sheet: Sheet, shape: Any, *, view: str, at_nm: tuple[int, int],
             kernel: Any, hidden: bool = True) -> str:
    """把一个视图放上图纸：调 `kernel.project_to_2d`，可见线与隐藏线**分图层**，返回视图 uuid。

    - `at_nm` = 该视图几何**外包框左下角**在图纸上的落点（图纸坐标系，原点在纸左下角，整数纳米）；
    - 内核缺席 / `available()=False` / 无 `project2d` 能力 / 投影为空 ⇒ **抛错**，绝不静默出空图；
    - 隐藏线进独立图层（`LINETYPE_HIDDEN` 的线型），与可见线图层名不同（判据 `hidden_layer=present`）。
    """
    name = str(view or "").strip()
    if name not in VIEW_DIRECTIONS:
        _fail("DrawingError", tr(sheet.language, "err.unknown_reference", reference=f"view:{name}"),
              view=name, known=list(VIEW_NAMES))
    _require_projector(sheet, kernel)
    try:
        projection = kernel.project_to_2d(shape, view=name, hidden=bool(hidden))
    except Exception as exc:  # noqa: BLE001 - 内核失败要带上下文重新抛出，不许静默吞掉
        _fail("DrawingError", tr(sheet.language, "err.geometry_invalid",
                                 detail=f"{type(exc).__name__}: {exc}"),
              view=name, kernel=str(_get(kernel, "name", "?")))
    visible_items, hidden_items, meta = _split_projection(projection)
    if not visible_items and not hidden_items:
        _fail("DrawingError", tr(sheet.language, "err.geometry_invalid",
                                 detail="projection produced no curves"), view=name)
    visible_names = _name_curves(visible_items)
    hidden_names = _name_curves(hidden_items)
    visible = tuple(_curve_from_item(item, layer=LAYER_VISIBLE, name=visible_names[index],
                                     source=f"kernel:{_get(kernel, 'name', '?')}")
                    for index, item in enumerate(visible_items))
    hidden_curves = tuple(_curve_from_item(item, layer=LAYER_HIDDEN, name=hidden_names[index],
                                           source=f"kernel:{_get(kernel, 'name', '?')}")
                          for index, item in enumerate(hidden_items))
    if hidden_curves:
        _register_layer(sheet, LAYER_HIDDEN, LINETYPE_HIDDEN, "hidden geometry")
    if not bool(hidden) and hidden_curves:
        sheet.note(f"kernel returned {len(hidden_curves)} hidden curve(s) although hidden=False "
                   "-- kept on the hidden layer (no geometry dropped)")
    view_uuid = sheet.next_uuid("view", name, tuple(at_nm), bool(hidden))
    placed = View(
        uuid=view_uuid,
        name=name,
        at_nm=(int(at_nm[0]), int(at_nm[1])),
        scale=sheet.scale_ratio,
        scale_text=sheet.scale,
        hidden=bool(hidden),
        visible=visible,
        hidden_curves=hidden_curves,
        shape=shape,
        shape_repr=_shape_repr(shape),
        projection_source=f"kernel:{_get(kernel, 'name', '?')}",
        projection_shape=str(meta.get("shape", "")),
    )
    placed.bounds_model_nm = _bounds_of(placed.curves())
    if placed.bounds_model_nm is None:
        _fail("DrawingError", tr(sheet.language, "err.geometry_invalid",
                                 detail="projected curves carry no measurable points"), view=name)
    sheet.views.append(placed)
    return view_uuid


def _shape_repr(shape: Any) -> str:
    """形状引用的**只读**短描述（只作取证，不解释内核对象）。"""
    text = _get(shape, "name", None)
    if text:
        return str(text)
    recipe = _get(shape, "recipe", None)
    if recipe is not None:
        try:
            return f"recipe[{len(list(recipe))}]"
        except TypeError:  # pragma: no cover - 不可枚举的 recipe
            return "recipe[?]"
    return f"{type(shape).__name__}"


def _require_projector(sheet: Sheet, kernel: Any) -> None:
    """内核必须**真的能投影**；四种缺席形态各给一条可判定行的错误（禁静默出空图）。"""
    if kernel is None:
        _fail("DependencyMissing", tr(sheet.language, "err.kernel_unavailable", kernel=NA),
              reason="kernel_absent")
    available = getattr(kernel, "available", None)
    if callable(available):
        try:
            ok = bool(available())
        except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理，但要留痕
            ok = False
            sheet.note(f"kernel.available() raised {type(exc).__name__}: {exc}")
        if not ok:
            _fail("DependencyMissing",
                  tr(sheet.language, "err.kernel_unavailable", kernel=str(_get(kernel, "name", "?"))),
                  reason="kernel_unavailable", kernel=str(_get(kernel, "name", "?")))
    projector = getattr(kernel, "project_to_2d", None)
    capabilities = _capabilities(kernel)
    if not callable(projector) or (capabilities is not None and "project2d" not in capabilities):
        _fail("DependencyMissing",
              tr(sheet.language, "err.capability_missing", capability="project2d"),
              reason="capability_missing", kernel=str(_get(kernel, "name", "?")))


def _capabilities(kernel: Any) -> frozenset[str] | None:
    """`kernel.capabilities()` → 能力集；取不到返回 `None`（**不等于空集**：空集会被当成"什么都不支持"）。"""
    getter = getattr(kernel, "capabilities", None)
    if not callable(getter):
        return None
    try:
        raw = getter()
    except Exception:  # noqa: BLE001 - 探测失败 ⇒ 未采（不阻断，也不假装支持）
        return None
    try:
        return frozenset(str(item) for item in raw)
    except TypeError:
        return None


def _bounds_of(curves: Iterable[Curve]) -> tuple[int, int, int, int] | None:
    boxes = [box for box in (curve.bounds_nm() for curve in curves) if box is not None]
    if not boxes:
        return None
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


# --------------------------------------------------------------------------------------
# A11: add_dimensions —— 数值一律现算
# --------------------------------------------------------------------------------------


def add_dimensions(sheet: Sheet, *, min_count: int = DEFAULT_MIN_DIMENSIONS,
                   kernel: Any = None) -> list[str]:
    """从视图几何**现算**尺寸标注，返回新加的标注 uuid 列表。

    - **数值不写死**：每个候选都由几何当场量出来（内核可用时**重新投影一次**再量，见 `measured_from`）；
    - **数量不达标即抛错**：可用候选少于 `min_count` ⇒ `DimensionShortfall`（不凑数、不重复计数）；
    - 候选按 `(视图顺序, 类别优先级)` 确定性排序，取前 `min_count` 条 ⇒ 同一份输入两次得到同一批标注；
    - `kernel` 缺席/不可用 ⇒ 回落视图里已有的几何量（**降级可观测**：`measured_from` 与 facts 会写明）。
      契约里 `kernel` 是必填关键字参数；本实现额外允许 `None`，把它当作"内核缺席"这条**显式降级路径**
      （否则调用方只能拿到一个 TypeError，而不是一条可判定的降级记录）。
    """
    if not isinstance(min_count, int) or isinstance(min_count, bool) or min_count < 0:
        _fail("DrawingError", tr(sheet.language, "err.out_of_range", value=repr(min_count), min=0, max=""),
              min_count=min_count)
    if not sheet.views:
        _fail("DrawingError", tr(sheet.language, "err.geometry_invalid",
                                 detail="no view on the sheet -- add a view before dimensioning"),
              min_count=min_count)
    candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for index, view in enumerate(sheet.views):
        curves, measured_from, note = _measurement_curves(view, kernel)
        if note:
            sheet.note(note)
        candidates.extend(_dimension_candidates(sheet, view, curves, index, measured_from))
    if len(candidates) < min_count:
        _fail("DimensionShortfall",
              tr(sheet.language, "err.dimension_shortfall", actual=len(candidates), required=min_count),
              actual=len(candidates), required=min_count)
    candidates.sort(key=lambda item: item[0])
    style = dimension_style(sheet)
    if style.unmapped_records:
        # 覆盖不全的样式记录只在数据层生效 ⇒ 出图侧不许静默：条数 + 口径一起留痕。
        sheet.note("dimension style: %d record(s) in dim_overrides/dim_edits do not cover every "
                   "same-kind annotation -- they apply to the L1 data layer only and are NOT "
                   "mapped onto the derived sheet dimensions" % int(style.unmapped_records))
    created: list[str] = []
    for order, (_, payload) in enumerate(candidates[:min_count]):
        dim_uuid = sheet.next_uuid("dimension", payload["kind"], payload["value_nm"],
                                   payload["view_uuid"], order)
        dimension = Dimension(
            uuid=dim_uuid,
            kind=payload["kind"],
            value_nm=int(payload["value_nm"]),
            text=_dimension_text(sheet, payload["kind"], int(payload["value_nm"]), style=style),
            text_key=DIMENSION_LABEL_KEYS.get(payload["kind"], "cmd.dimension.linear"),
            view_uuid=payload["view_uuid"],
            at_nm=payload["at_nm"],
            refs=payload["refs"],
            terminator=payload.get("terminator"),
            measured_from=payload["measured_from"],
            label=tr(sheet.language, DIMENSION_LABEL_KEYS.get(payload["kind"], "cmd.dimension.linear")),
        )
        sheet.dimensions.append(dimension)
        created.append(dim_uuid)
    return created


def _measurement_curves(view: View, kernel: Any) -> tuple[tuple[Curve, ...], str, str]:
    """量尺寸用的几何：内核可用就**重新投影一次**（真·现算），否则用视图里存的那份（降级可见）。

    返回 `(几何, measured_from, 诊断)`。
    """
    stored = view.curves(include_hidden=False)
    if kernel is None or view.shape is None:
        return (stored, "view_geometry",
                f"dimension values measured from the stored view geometry of {view.name!r} "
                f"(kernel={NA if kernel is None else 'no shape'})")
    available = getattr(kernel, "available", None)
    try:
        if callable(available) and not bool(available()):
            return (stored, "view_geometry",
                    f"dimension values measured from the stored view geometry of {view.name!r} "
                    "(kernel not available)")
    except Exception:  # noqa: BLE001 - 探测失败按不可用处理
        return (stored, "view_geometry",
                f"kernel.available() failed while dimensioning {view.name!r} -- stored geometry used")
    capabilities = _capabilities(kernel)
    if capabilities is not None and "project2d" not in capabilities:
        return (stored, "view_geometry",
                f"kernel lacks the project2d capability -- stored geometry used for {view.name!r}")
    projector = getattr(kernel, "project_to_2d", None)
    if not callable(projector):
        return (stored, "view_geometry",
                f"kernel has no project_to_2d -- stored geometry used for {view.name!r}")
    try:
        projection = projector(view.shape, view=view.name, hidden=False)
        visible_items, hidden_items, _meta = _split_projection(projection)
    except Exception as exc:  # noqa: BLE001 - 重投影失败不许阻断出图，但要留痕
        return (stored, "view_geometry",
                f"kernel re-projection failed for view {view.name!r} "
                f"({type(exc).__name__}: {exc}) -- stored geometry used")
    if not visible_items and not hidden_items:
        return (stored, "view_geometry",
                f"kernel re-projection returned no curves for view {view.name!r} -- stored geometry used")
    names = _name_curves(visible_items)
    fresh = tuple(_curve_from_item(item, layer=LAYER_VISIBLE, name=names[index],
                                   source=f"kernel:{_get(kernel, 'name', '?')}")
                  for index, item in enumerate(visible_items))
    fresh_bounds = _bounds_of(fresh)
    note = ""
    if fresh_bounds is not None and view.bounds_model_nm is not None:
        if not _bounds_close(fresh_bounds, view.bounds_model_nm):
            note = (f"view {view.name!r} geometry is stale: stored bounds "
                    f"{list(view.bounds_model_nm)} != re-projected {list(fresh_bounds)} "
                    "-- dimensions were measured from the re-projection")
    return (fresh, "kernel_reprojection", note)


def _bounds_close(a: Sequence[int], b: Sequence[int], *, tolerance_nm: int = 1_000) -> bool:
    return all(abs(int(x) - int(y)) <= tolerance_nm for x, y in zip(a, b))


def _dimension_candidates(sheet: Sheet, view: View, curves: Sequence[Curve], view_index: int,
                          measured_from: str) -> list[tuple[tuple[int, int, str], dict[str, Any]]]:
    """一个视图上的候选尺寸（**每一条都是量出来的**）。

    优先级：①整体宽/高 ②圆直径 ③圆弧半径 ④圆心相对基准的位移 ⑤其余曲线长度。
    排序键 = `(优先级, 量值, 子形状名)` ⇒ 与集合遍历顺序无关，同一份输入稳定同序。
    """
    out: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    bounds = _bounds_of(curves)
    if bounds is None:
        return out
    x0, y0, x1, y1 = bounds
    terminator = _field(_profile_cache(sheet), "dimension_terminator", None)
    terminator_text = str(terminator) if terminator else None

    def add(priority: int, kind: str, value_nm: int, refs: Sequence[str], at_nm: tuple[int, int],
            key: str) -> None:
        out.append(((priority, int(value_nm), key), {
            "kind": kind, "value_nm": int(value_nm), "view_uuid": view.uuid, "at_nm": at_nm,
            "refs": tuple((view.uuid, ref) for ref in refs), "measured_from": measured_from,
            "terminator": terminator_text,
        }))

    width = x1 - x0
    height = y1 - y0
    left = _curve_touching(curves, "x", x0)
    right = _curve_touching(curves, "x", x1)
    bottom = _curve_touching(curves, "y", y0)
    top = _curve_touching(curves, "y", y1)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    if width > 0:
        add(0, "linear", width, [left, right], (cx, y0 - _DIM_OFFSET_NM), f"width:{width}")
    if height > 0:
        add(0, "linear", height, [bottom, top], (x0 - _DIM_OFFSET_NM, cy), f"height:{height}")

    circles: list[tuple[str, tuple[int, int], int]] = []
    arcs: list[tuple[str, tuple[int, int], int]] = []
    others: list[tuple[str, int]] = []
    for curve in curves:
        fitted = _fit_round(curve)
        if fitted is not None:
            center, radius, closed = fitted
            if closed and curve.kind != "arc":
                circles.append((curve.name, center, radius))
            else:
                arcs.append((curve.name, center, radius))
            continue
        length = curve.length_nm()
        if length is not None and length > 0:
            others.append((curve.name, length))

    for name, center, radius in sorted(circles, key=lambda item: (item[1][1], item[1][0], item[0])):
        at = view.to_sheet_nm((center[0] + radius, center[1] + radius))
        add(1, "diameter", 2 * radius, [name], at, f"diameter:{name}")
    for name, center, radius in sorted(arcs, key=lambda item: (item[1][1], item[1][0], item[0])):
        at = view.to_sheet_nm((center[0], center[1] + radius))
        add(2, "radial", radius, [name], at, f"radius:{name}")
    for name, center, _radius in sorted(circles, key=lambda item: (item[1][1], item[1][0], item[0])):
        add(3, "linear", int(center[0] - x0), [name], view.to_sheet_nm((center[0], y0 - _DIM_OFFSET_NM)),
            f"offset_x:{name}")
        add(4, "linear", int(center[1] - y0), [name], view.to_sheet_nm((x0 - _DIM_OFFSET_NM, center[1])),
            f"offset_y:{name}")
    seen_lengths: set[int] = {width, height}
    for name, length in sorted(others, key=lambda item: (item[1], item[0])):
        if length in seen_lengths:
            continue
        seen_lengths.add(length)
        add(5, "linear", length, [name], view.to_sheet_nm((cx, cy)), f"length:{name}")
    return out


#: 尺寸线相对视图外包框的偏移（版面常数，不是标准档取值）。
_DIM_OFFSET_NM: int = 12 * NM_PER_MM


def _curve_touching(curves: Sequence[Curve], axis: str, value: int) -> str:
    """找出包围盒该侧是哪条曲线贡献的（尺寸的 `refs` 要指向真实子形状）。"""
    index = 0 if axis == "x" else 1
    for curve in curves:
        box = curve.bounds_nm()
        if box is None:
            continue
        if box[index] == value or box[index + 2] == value:
            return curve.name
    return curves[0].name if curves else "Bounds"


def _fit_round(curve: Curve) -> tuple[tuple[int, int], int, bool] | None:
    """曲线是不是"圆/圆弧"：内核给了心/半径就直接认；只给点串才按三点定圆 + 全点校验。

    返回 `((cx, cy), radius_nm, closed)`；不是圆弧形态 ⇒ `None`（**不硬凑**：拟合不上就不产生尺寸）。
    闭/开（圆 vs 圆弧）由点串首尾是否重合判定，`kind="circle"` 视为闭。
    """
    if curve.center is not None and curve.radius_nm:
        closed = (curve.kind == "circle"
                  or (len(curve.points) >= 3 and _points_close(curve.points[0], curve.points[-1])))
        return (curve.center, int(curve.radius_nm), closed)
    pts = list(curve.points)
    if len(pts) < 5:
        return None
    closed = _points_close(pts[0], pts[-1])
    sample = pts[:-1] if closed else pts
    if len(sample) < 4:
        return None
    first, middle, last = sample[0], sample[len(sample) // 2], sample[-1]
    center = _circumcenter(first, middle, last)
    if center is None:
        return None
    cx, cy = center
    radii = [math.hypot(x - cx, y - cy) for x, y in sample]
    radius = sum(radii) / len(radii)
    if radius <= 0:
        return None
    tolerance = max(1.0, radius * 1e-3)
    if any(abs(item - radius) > tolerance for item in radii):
        return None
    return ((int(round(cx)), int(round(cy))), int(round(radius)), closed)


def _circumcenter(p1: Sequence[int], p2: Sequence[int], p3: Sequence[int]) -> tuple[float, float] | None:
    ax, ay = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]), float(p2[1])
    cx, cy = float(p3[0]), float(p3[1])
    det = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(det) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / det
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / det
    return (ux, uy)


def _points_close(a: Sequence[int], b: Sequence[int], *, tolerance: int = 1_000) -> bool:
    return abs(int(a[0]) - int(b[0])) <= tolerance and abs(int(a[1]) - int(b[1])) <= tolerance


#: 文档级标注样式在出图侧的解析结果（**唯一真源**：图面尺寸文字只从这里取口径）。
@dataclass(frozen=True)
class DimensionStyle:
    """`doc.dim_styles` 当前样式 + 文档级 `dim_overrides` / `dim_edits` 的合并结果。

    `text` 非空 = 文档里**同类尺寸全都**显示同一个替代文本（见 `dimension_style`）。
    `unmapped_records` = 存在、但覆盖不到同类全部注解、因此**没有**映射到图面的样式记录条数
    （数据层照旧生效；出图侧不静默 —— 调用方把条数写进 `sheet.note`）。
    """

    decimals: int = DIM_TEXT_DECIMALS
    text: str = ""
    height_nm: int = 0
    arrow_size_nm: int = 0
    ext_gap_nm: int = 0
    style_name: str = ""
    sources: tuple[str, ...] = ()
    unmapped_records: int = 0


def _document_annotations(document: Any) -> list[Any]:
    """`doc.annotations` → 节点列表（映射/序列两种形态都认；其它形态 ⇒ 空表，不猜）。"""
    annotations = _doc_get(document, "annotations", None)
    if isinstance(annotations, Mapping):
        return list(annotations.values())
    if isinstance(annotations, Sequence) and not isinstance(annotations, (str, bytes)):
        return list(annotations)
    return []


def _positive_int(value: Any) -> int:
    """正整数纳米出参（`bool` 不算整数，浮点不算 —— 与 L1 长度不变量同口径）；非法 ⇒ `0`（= 未给）。"""
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _style_digits(value: Any) -> int | None:
    """样式里的 `decimals`（0–8 整数）；非法/未给 ⇒ `None`（调用方保留原值）。"""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 8:
        return int(value)
    return None


def _document_wide_records(document: Any, bucket: str, field: str) -> list[dict[str, Any]]:
    """样式桶里**覆盖到同类全部注解**的记录 → 生效项列表（按桶序，后写覆盖先写）。

    判据：记录点名的注解集合 == 文档里**与它们同类**的全部注解集合（同类 = 被点名注解自身的
    `kind`）。这样不必另抄一份"哪些 kind 是尺寸注解"的常量 —— 尺子由文档自己给，抄一份就会漂。
    覆盖不全的记录**不**进这个列表（它们只该在数据层生效），由调用方计入 `unmapped_records`。
    """
    records = _doc_get(document, bucket, None)
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return []
    annotations = [item for item in _document_annotations(document) if isinstance(item, Mapping)]
    by_uuid = {str(item.get("uuid")): item for item in annotations}
    out: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        named = {str(item) for item in (record.get("applies_to") or [])}
        kinds = {str(by_uuid[key].get("kind")) for key in named if key in by_uuid}
        if not named or not kinds:
            continue
        same_kind = {str(item.get("uuid")) for item in annotations
                     if str(item.get("kind")) in kinds}
        if named != same_kind:
            continue
        payload = record.get(field)
        out.append(dict(payload) if isinstance(payload, Mapping) else {})
    return out


def _unmapped_style_records(document: Any, bucket: str, field: str) -> int:
    """样式桶里**没能**映射到图面的记录条数（覆盖不全 / 空 `applies_to`）。"""
    records = _doc_get(document, bucket, None)
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    total = sum(1 for item in records if isinstance(item, Mapping))
    return total - len(_document_wide_records(document, bucket, field))


def dimension_style(sheet: Sheet) -> DimensionStyle:
    """图纸所属文档的标注样式 → **图面渲染参数**（读侧唯一入口，`add_dimensions`/`iter_entities` 共用）。

    为什么必须有它（B6 缺陷 RC-S3）：`ops/annotation.dim_style` / `dim_override` / `dim_edit` 改完 L1
    都报 `ok=true`，但出图链路此前**一个样式字段都不读** ⇒ 精度/字高/替代文字改完，图纸与 DXF
    **逐字节不变**：最危险的一类 phantom（"操作报成功但文档没变"的产物版）。

    口径（全部写在这里，别处不许再解一遍）：
    * `dim_styles`：取**当前**样式（`is_current=True` 的最后一条；没有 `is_current` 取最后一条，
      与 `ops.annotation.style_record` 同一口径）。`decimals` 决定尺寸文字小数位，
      `height_nm` / `arrow_size_nm` / `ext_gap_nm` 随 `DimensionStyle` 带出去供图面元数据使用 ——
      L1 注记没有落点字段 ⇒ **不重排标注线/箭头几何**，这条 unsupported 限制照旧，不假装支持。
    * `dim_overrides` / `dim_edits`：图面尺寸是 `add_dimensions` 从视图几何**现算派生**的，
      与 L1 注解 uuid 没有一一对应 ⇒ 只有**覆盖到同类全部注解**的那条（= "文档里这类尺寸全用这个
      替代"）才映射到图面；覆盖不全的在数据层照旧生效，但**不静默**（计入 `unmapped_records`，
      调用方记 note）。
    """
    document = sheet.document
    sources: list[str] = []
    style_name = ""
    values: dict[str, int] = {"height_nm": 0, "arrow_size_nm": 0, "ext_gap_nm": 0}
    decimals = DIM_TEXT_DECIMALS
    records = _doc_get(document, "dim_styles", None)
    if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
        rows = [item for item in records if isinstance(item, Mapping)]
        current = [item for item in rows if item.get("is_current")] or rows
        if current:
            record = current[-1]
            style_name = str(record.get("name") or "")
            digits = _style_digits(record.get("decimals"))
            if digits is not None:
                decimals = digits
            for field_name in values:
                values[field_name] = _positive_int(record.get(field_name))
            sources.append("dim_styles:%s" % (style_name or NA))
    override_text = ""
    for effective in _document_wide_records(document, "dim_overrides", "overrides"):
        digits = _style_digits(effective.get("decimals"))
        if digits is not None:
            decimals = digits
            sources.append("dim_overrides.decimals")
        for field_name in values:
            value = _positive_int(effective.get(field_name))
            if value:
                values[field_name] = value
        text = effective.get("text")
        if isinstance(text, str) and text:
            override_text = text
            sources.append("dim_overrides.text")
    for effective in _document_wide_records(document, "dim_edits", "edit"):
        text = effective.get("text_override")
        if isinstance(text, str) and text:
            override_text = text
            sources.append("dim_edits.text_override")
    unmapped = _unmapped_style_records(document, "dim_overrides", "overrides") \
        + _unmapped_style_records(document, "dim_edits", "edit")
    return DimensionStyle(decimals=decimals, text=override_text,
                          height_nm=values["height_nm"], arrow_size_nm=values["arrow_size_nm"],
                          ext_gap_nm=values["ext_gap_nm"], style_name=style_name,
                          sources=tuple(sources), unmapped_records=unmapped)


def _dimension_text(sheet: Sheet, kind: str, value_nm: int, *,
                    style: DimensionStyle | None = None) -> str:
    """渲染尺寸文字：数值按文档单位与标准档小数分隔符渲染（分隔符缺席 ⇒ 内置 `.`，来源记进 facts）。

    小数位与整条替代文本来自 `dimension_style(sheet)`（文档级样式真值）；文档没给样式时回落
    模块常量 `DIM_TEXT_DECIMALS` —— "没有样式" 与 "样式说 2 位" 在结果上同形，但 `sources` 里分得开。
    """
    resolved = style if style is not None else dimension_style(sheet)
    if resolved.text:
        return resolved.text
    body = _format_length(value_nm, unit=sheet.unit, decimals=resolved.decimals,
                          decimal_separator=sheet.decimal_separator or ".")
    if kind == "diameter":
        return DIM_DIAMETER_PREFIX + body
    if kind == "radial":
        return DIM_RADIUS_PREFIX + body
    if kind == "angular":
        return f"{body}{DIM_ANGLE_SUFFIX}"
    return body


#: 标注前缀/后缀符号（图形符号，不是可翻译文案；标准档 `gdt_symbol_set` 为 NA ⇒ 只记 NA 不编数据）。
DIM_DIAMETER_PREFIX: str = "\u2300"
DIM_RADIUS_PREFIX: str = "R"
DIM_ANGLE_SUFFIX: str = "\u00b0"

#: 尺寸文字的图面字高（版面常数，不是标准档取值）。
DIM_TEXT_HEIGHT_NM: int = 3_500_000


def _format_length(nm: int, *, unit: str, decimals: int, decimal_separator: str) -> str:
    """整数纳米 → 单位文本。A4 `units.py` 落盘后走它（唯一真源），缺席时本地兜底（同语义）。"""
    module = _optional_module("cadcore.units")
    formatter = getattr(module, "format_length", None) if module is not None else None
    if callable(formatter):
        try:
            return str(formatter(int(nm), unit=unit, decimals=int(decimals),
                                 decimal_separator=decimal_separator))
        except Exception as exc:  # noqa: BLE001 - A4 坏了不该让出图崩掉，但要留痕
            LOG.warning("cadcore.units.format_length failed (%s: %s) -- builtin fallback used",
                        type(exc).__name__, exc)
    per_unit = NM_PER_MM if str(unit) != "inch" else int(round(25.4 * NM_PER_MM))
    value = int(nm) / per_unit
    text = f"{value:.{int(decimals)}f}"
    if decimal_separator and decimal_separator != ".":
        text = text.replace(".", decimal_separator)
    return text


_PROFILE_CACHE: dict[str, dict[str, Any]] = {}


def _profile_cache(sheet: Sheet) -> dict[str, Any]:
    """按 sheet 缓存标准档数据（同一张图纸只读一次盘）。"""
    key = sheet.standard_profile
    if key not in _PROFILE_CACHE:
        try:
            profile, _source = load_profile(key)
        except Exception:  # noqa: BLE001 - 已经建过图纸 ⇒ 这里不该再失败；失败就退化为空档
            profile = {}
        _PROFILE_CACHE[key] = profile
    return _PROFILE_CACHE[key]


def clear_profile_cache() -> None:
    """清标准档缓存（测试用）。"""
    _PROFILE_CACHE.clear()


# --------------------------------------------------------------------------------------
# A12: title_block（实现落在 draw/titleblock.py，这里转出）
# --------------------------------------------------------------------------------------


def title_block(sheet: Sheet, *, doc: Any, language: str) -> dict[str, str]:
    """渲染标题栏：`{字段名: 图纸语言下的成品文案}`，逐字段查语言包 key（实现见 `draw/titleblock.py`）。

    - 字段清单与每个字段的 i18n key 记在 `sheet.title_block_keys` / `sheet.title_block_meta`；
    - 取值优先级：文档真值 → 图纸自身真值（比例/投影角/单位…）→ `status.na`（**缺数据记 NA，不编、不留空串**）；
    - 这里的 `language` 与 `make_sheet(language=…)`、与进程级活动语言**三者独立**：传什么用什么。
    """
    module = _titleblock_module()
    if module is None:  # pragma: no cover - 同笔提交，正常路径不会走到
        _fail("DependencyMissing", tr(language, "err.dependency_missing", name="cadcore.draw.titleblock"),
              reason="titleblock_module_absent")
    return module.title_block(sheet, doc=doc, language=language)


# --------------------------------------------------------------------------------------
# 实体流：DXF / SVG 共用的唯一一份"图纸上有哪些实体"
# --------------------------------------------------------------------------------------


def iter_entities(sheet: Sheet) -> list[dict[str, Any]]:
    """图纸上的全部实体（**图纸空间整数纳米**），DXF/SVG/判定行共用这一份。

    每条形如 `{"type": "LINE"|"CIRCLE"|"ARC"|"LWPOLYLINE"|"TEXT"|"DIMENSION", "layer": …, …}`。
    坐标真源是纳米；写出成 DXF 时按 `sheet.unit` 换算成**文档单位**（毫米/英寸），
    SVG 则用纳米当 `viewBox` 单位、毫米当物理尺寸 —— 两处换算都只发生在写出器里。
    A13 的 `io.dxf_bridge.sheet_to_dxf` 可以直接吃这份结构（避免两处各解一遍图纸）。
    """
    entities: list[dict[str, Any]] = []
    font = _font_family(sheet)
    titleblock = _titleblock_module()
    if titleblock is not None:
        for curve in titleblock.frame_curves(sheet):
            entities.extend(_curve_entities(curve, origin="frame"))
        for item in titleblock.frame_texts(sheet):
            entities.append({
                "type": "TEXT", "layer": item.layer, "at": [int(item.at_nm[0]), int(item.at_nm[1])],
                "text": item.text, "height_nm": int(item.height_nm), "field": item.field_key,
                "lang": item.lang, "anchor": item.anchor, "role": item.role, "font": font,
                "origin": "titleblock",
            })
    for view in sheet.views:
        for curve in view.visible:
            entities.extend(_curve_entities(curve, origin=f"view:{view.uuid}", view=view))
        for curve in view.hidden_curves:
            entities.extend(_curve_entities(curve, origin=f"view:{view.uuid}", view=view))
    # 尺寸文字在**导出时**按文档的当前样式重解一次（`Dimension.text` 是建标注时的快照；
    # 两者走的是同一个 `_dimension_text`）：样式在建图之后才改，产物也必须跟着变 ——
    # 否则"改样式"又会退回"操作报成功、产物没变"的 phantom。
    dimension_style_now = dimension_style(sheet)
    for dimension in sheet.dimensions:
        entities.append({
            "type": "DIMENSION", "layer": dimension.layer, "kind": dimension.kind,
            "value_nm": int(dimension.value_nm),
            "text": _dimension_text(sheet, dimension.kind, int(dimension.value_nm),
                                    style=dimension_style_now),
            "at": [int(dimension.at_nm[0]), int(dimension.at_nm[1])],
            "refs": [[u, s] for u, s in dimension.refs], "view": dimension.view_uuid,
            "terminator": dimension.terminator, "uuid": dimension.uuid,
            "text_key": dimension.text_key, "origin": "dimension",
        })
    entities.extend(_annotation_text_entities(sheet, font=font))
    return entities


def annotation_texts(sheet: Sheet) -> list[str]:
    """`doc.annotations` 里**真有文本**的那些 → 文本清单（顺序即文档顺序）。

    取值口径唯一真源 = `docmodel.annotation_text`（与 `io.dxf_bridge.annotation_text_lines` 同一份读法）：
    `literal=True` 的自由文本优先（`args["text"]` 与顶层 `text` 两种写侧形态都认），否则用 `text_key`
    （真源存的就是 key，翻译在语言层）；两者都没有 ⇒ 不出条目（不编空文本）。
    """
    annotations = _doc_get(sheet.document, "annotations", None)
    if isinstance(annotations, Mapping):
        items: Iterable[Any] = list(annotations.values())
    elif annotations is None or isinstance(annotations, (str, bytes)):
        items = ()
    elif isinstance(annotations, Sequence):
        items = annotations
    else:
        items = ()
    out: list[str] = []
    for item in items:
        text = annotation_text(item)
        if text:
            out.append(text)
    return out


def _annotation_text_entities(sheet: Sheet, *, font: str) -> list[dict[str, Any]]:
    """标注文本 → 图纸文本实体（落在 `LAYER_ANNOTATION` 的派生版面区，左下起逐行向上）。

    B6 缺陷 RC7 的落点：`doc.annotations` 原先被 `/api/open` 收下并往返保留，但**出图链路从不消费它**
    —— 产物里一个字都没有、也没有诊断（静默丢数据）。位置仍是**派生**的（标注没有位置字段，
    画到引用几何上是伪造），文本则逐字进图纸（PDF 文本操作 / 图纸 DXF 的 TEXT 组码 / SVG `<text>`）。
    """
    height = int(sheet.text_height_nm or ANNOTATION_TEXT_HEIGHT_NM)
    out: list[dict[str, Any]] = []
    for index, text in enumerate(annotation_texts(sheet)):
        out.append({
            "type": "TEXT", "layer": LAYER_ANNOTATION,
            "at": [ANNOTATION_MARGIN_NM, ANNOTATION_MARGIN_NM + index * ANNOTATION_LINE_STEP_NM],
            "text": text, "height_nm": height, "field": "", "anchor": "start",
            "role": "annotation", "font": font, "origin": "annotation",
        })
    return out


def _curve_entities(curve: Curve, *, origin: str, view: View | None = None) -> list[dict[str, Any]]:
    """一条曲线 → 图纸空间实体（视图曲线要过一次比例变换，图框曲线本来就是图纸空间）。"""
    if view is None:
        if curve.kind == "line" and len(curve.points) == 2:
            return [{"type": "LINE", "layer": curve.layer, "start": [int(curve.points[0][0]), int(curve.points[0][1])],
                     "end": [int(curve.points[1][0]), int(curve.points[1][1])], "origin": origin}]
        if curve.kind == "circle" and curve.center is not None and curve.radius_nm is not None:
            return [{"type": "CIRCLE", "layer": curve.layer,
                     "center": [int(curve.center[0]), int(curve.center[1])],
                     "radius_nm": int(curve.radius_nm), "origin": origin}]
        if curve.kind == "arc" and curve.center is not None and curve.radius_nm is not None:
            return [{"type": "ARC", "layer": curve.layer,
                     "center": [int(curve.center[0]), int(curve.center[1])],
                     "radius_nm": int(curve.radius_nm), "start_deg": float(curve.start_deg or 0.0),
                     "end_deg": float(curve.end_deg or 0.0), "origin": origin}]
        return [{"type": "LWPOLYLINE", "layer": curve.layer,
                 "points": [[int(x), int(y)] for x, y in curve.points], "closed": False,
                 "origin": origin}]
    num, den = view.scale
    if curve.kind == "line" and len(curve.points) == 2:
        start = view.to_sheet_nm(curve.points[0])
        end = view.to_sheet_nm(curve.points[1])
        return [{"type": "LINE", "layer": curve.layer, "start": [start[0], start[1]],
                 "end": [end[0], end[1]], "origin": origin}]
    if curve.kind == "circle" and curve.center is not None and curve.radius_nm is not None:
        center = view.to_sheet_nm(curve.center)
        return [{"type": "CIRCLE", "layer": curve.layer, "center": [center[0], center[1]],
                 "radius_nm": _scale_nm(int(curve.radius_nm), num, den), "origin": origin}]
    if curve.kind == "arc" and curve.center is not None and curve.radius_nm is not None:
        center = view.to_sheet_nm(curve.center)
        return [{"type": "ARC", "layer": curve.layer, "center": [center[0], center[1]],
                 "radius_nm": _scale_nm(int(curve.radius_nm), num, den),
                 "start_deg": float(curve.start_deg or 0.0), "end_deg": float(curve.end_deg or 0.0),
                 "origin": origin}]
    points = [view.to_sheet_nm(point) for point in curve.points]
    return [{"type": "LWPOLYLINE", "layer": curve.layer,
             "points": [[x, y] for x, y in points], "closed": False, "origin": origin}]


# --------------------------------------------------------------------------------------
# A11: export_sheet
# --------------------------------------------------------------------------------------


def export_sheet(sheet: Sheet, *, fmt: str, out: str, dxf_version: str = "R2018",
                pdf_builtin: bool = False) -> str:
    """导出图纸，返回**实际落盘路径**。

    - `fmt="dxf"`：优先 A13 `io.dxf_bridge.sheet_to_dxf`（唯一权威出口），缺席则用内置极简 ASCII 写出器
      （`facts.dxf_writer` / `ExportResult.writer` 写明是哪一个，**不冒充**）；
    - `fmt="svg"`：本模块内置写出器（纯标准库，文本走语言包）；
    - `fmt="pdf"`：外部后端（`register_pdf_backend` 或 `CADCORE_PDF_BACKEND`）优先；**没有外部后端时**
      `pdf_builtin=False`（缺省）仍如实降级 SVG，`pdf_builtin=True` 走本模块**内建** PDF 写出器
      （`PDF_BUILTIN_WRITER`，真 PDF、不降级）—— 批量发布/A8 判据要的是后者；
      返回值、`ExportResult`（`requested` vs `actual`）与 `sheet_facts()["export_format"]` 三处都写明实际格式。
    """
    target = str(fmt or "").strip().lower()
    if target not in ("dxf", "svg", "pdf"):
        _fail("DrawingError", tr(sheet.language, "err.out_of_range", value=str(fmt), min="dxf", max="pdf"),
              fmt=str(fmt))
    # 唯一 choke point：空 out 旧行为经 _with_extension("") 变成 .svg/.dxf/.pdf 落进进程 CWD
    # （C7 残留两件的成因）⇒ 在此显式拒绝，任何调用方都绕不过去。
    if not str(out).strip():
        _fail("CadError", "export_sheet needs a non-empty out path (empty string is not the CWD)",
              reason="bad_reference", out=str(out))
    actual = target
    reason = ""
    writer = ""
    payload: bytes | None = None
    if target == "dxf":
        payload, writer = _render_dxf(sheet, version=dxf_version)
    elif target == "svg":
        payload = _render_svg(sheet)
        writer = "builtin_svg"
    else:
        backend = _pdf_backend()
        if backend is None and pdf_builtin:
            # A8：显式要求真 PDF ⇒ 走内建写出器（纯标准库；外部后端仍优先）。
            # 与外部后端同口径：这一支**自己落盘**（下面 `actual != "pdf"` 的通用落盘只服务降级支路）。
            payload = render_pdf_page(sheet)
            writer = PDF_BUILTIN_WRITER
            _pdf_write_file(payload, _with_extension(str(out), "pdf"))
        elif backend is None:
            actual, reason = "svg", "pdf_backend_absent"
            payload = _render_svg(sheet)
            writer = "builtin_svg"
            sheet.note("pdf backend is absent -- exported SVG instead "
                       "(actual format is reported in the return value, ExportResult and sheet_facts)")
        else:
            writer = f"pdf_backend:{getattr(backend, '__name__', type(backend).__name__)}"
            payload = _render_pdf(sheet, backend, _with_extension(str(out), "pdf"))
    out_path = guard_output(_with_extension(str(out), actual))
    directory = Path(out_path).parent
    if str(directory):
        directory.mkdir(parents=True, exist_ok=True)
    if payload is None:  # pragma: no cover - 三条分支都必须产出内容，走到这里就是本模块的 bug
        _fail("DrawingError", tr(sheet.language, "err.export_failed", format=target,
                                 detail="renderer produced no payload"), fmt=target)
    if actual != "pdf":
        Path(out_path).write_bytes(payload)
    result = ExportResult(
        requested=target, actual=actual, path=out_path, downgraded=actual != target, reason=reason,
        writer=writer, entities=len(iter_entities(sheet)), bytes=len(payload),
        titleblock_rendered=bool(sheet.title_fields),
    )
    sheet.exports.append(result)
    return out_path


def _with_extension(path: str, extension: str) -> str:
    """把扩展名换成**实际格式**（降级后路径扩展名必须跟着变，否则调用方看不出来）。"""
    if path.lower().endswith("." + extension):
        return path
    stem = path
    for known in (".dxf", ".svg", ".pdf"):
        if stem.lower().endswith(known):
            stem = stem[: -len(known)]
            break
    return f"{stem}.{extension}"


def _render_pdf(sheet: Sheet, backend: Any, out_path: str) -> bytes:
    """调用已注册的 PDF 后端渲染（SVG 源文本 + 目标路径），再读回落盘字节。

    后端**没写出文件** ⇒ 抛错（不假装成功）；后端抛错 ⇒ 原样上抛（带上下文）。
    """
    out_path = guard_output(out_path)
    directory = Path(out_path).parent
    if str(directory):
        directory.mkdir(parents=True, exist_ok=True)
    svg_text = _render_svg(sheet).decode("utf-8")
    produced = backend(svg_text, out_path)
    if produced is not None and str(produced) != out_path:
        Path(guard_output(produced)).replace(out_path)
    target = Path(out_path)
    if not target.is_file() or target.stat().st_size == 0:
        _fail("DrawingError", tr(sheet.language, "err.export_failed", format="pdf",
                                 detail="the pdf backend wrote no file"), fmt="pdf")
    return target.read_bytes()


# ── 内建 PDF 写出（纯标准库；一布局一页；多页合成一个文件）────────────────────────────────
#
# A8「出图含真 PDF」判据的落点：`%PDF-` 魔数 ∧ 页数=布局数 ∧ `actual` 不得是 svg。
# 本写出器**不依赖任何第三方包**（与内置 SVG/DXF 写出器同口径），所以"外部 PDF 后端缺席"只影响
# `export_sheet(fmt="pdf")` 的**可选**后端优先路径，不再让批量发布的成品降级成 svg：
#   `export_sheet(..., fmt="pdf", pdf_builtin=True)` → 单页真 PDF（一布局一页）；
#   `export_pdf_pages([sheet, ...], out=...)`        → 多布局合成**一个**多页 PDF（页数=布局数）。
# 三条硬保证（`_pdf_verify` 落盘后回读复验，不靠调用方相信）：① `/Type /Page` 页对象数 == 图纸数；
# ② `/Count` 字段与页对象数一致；③ 无时间戳/无随机 id ⇒ 同输入逐字节相同。

PDF_BUILTIN_WRITER = "builtin_pdf_1.4"
_PDF_PT_PER_NM = 72.0 / 25_400_000.0


def pdf_builtin_available() -> bool:
    """内建 PDF 写出器是否可用（恒 True：纯标准库）。**外部**后端缺席 ≠ 没有 PDF 能力。"""
    return True


def _pdf_num(value: Any) -> str:
    """PDF 数字字面量（4 位定点 + 去尾零；禁科学计数/`repr` ⇒ 产物确定）。"""
    text = "%.4f" % float(value)
    text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def _pdf_pt(value_nm: Any) -> float:
    """整数纳米 → PDF 点（1 pt = 1/72 in = 25 400 000/72 nm）。"""
    return float(value_nm) * _PDF_PT_PER_NM


def _pdf_ascii(value: Any) -> str | None:
    """PDF 标准字体（Helvetica）只写 ASCII：非 ASCII / 控制字符 ⇒ None（调用侧计数，不静默丢）。"""
    text = str(value)
    if any(ord(char) > 126 or ord(char) < 32 for char in text):
        return None
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf_arc_ops(cx: float, cy: float, radius: float, start_deg: float, span_deg: float) -> list[str]:
    """圆/圆弧 → 三次 Bezier 段链（每段 ≤ 90°）的 PDF 路径算子（整圆 = 4 段）。"""
    span = span_deg % 360.0 or 360.0
    count = max(1, int(math.ceil(abs(span) / 90.0)))
    step = span / count
    alpha = 4.0 / 3.0 * math.tan(math.radians(step / 4.0))
    ops: list[str] = []
    for index in range(count):
        a0 = math.radians(start_deg + index * step)
        a1 = math.radians(start_deg + (index + 1) * step)
        p0 = (cx + radius * math.cos(a0), cy + radius * math.sin(a0))
        p1 = (cx + radius * math.cos(a1), cy + radius * math.sin(a1))
        c1 = (p0[0] - alpha * radius * math.sin(a0), p0[1] + alpha * radius * math.cos(a0))
        c2 = (p1[0] + alpha * radius * math.sin(a1), p1[1] - alpha * radius * math.cos(a1))
        if index == 0:
            ops.append("%s %s m" % (_pdf_num(p0[0]), _pdf_num(p0[1])))
        ops.append("c " + " ".join(_pdf_num(item) for item in
                                   (c1[0], c1[1], c2[0], c2[1], p1[0], p1[1])))
    ops.append("S")
    return ops


def _pdf_page_stream(sheet: Sheet) -> tuple[bytes, dict[str, Any]]:
    """一张图纸 → PDF 内容流 + 事实。

    坐标口径：图纸空间整数纳米（y 轴向上，与 DXF/图纸一致）→ PDF 点；**不翻转 y**（SVG 才翻）。
    线宽按图层取 `_line_widths_nm` 并换算成点；隐藏线用**点阵**虚线（SVG 那支的 `5,2.5` 是 nm 用户单位，
    换算成点后肉眼等同实线；PDF 侧取同一数值当点用 ⇒ 虚线在产物里真的可辨，口径写进 docstring）。
    文本只写 ASCII（Helvetica 无 CJK 字形）：非 ASCII 逐条计数进 `text_skipped_non_ascii`，不冒充已画。
    """
    widths = _line_widths_nm(sheet)
    default_width = int(widths.get("_default", 350_000))
    fallback_height = int(sheet.text_height_nm or ANNOTATION_TEXT_HEIGHT_NM)
    ops: list[str] = ["q", "1 J", "1 j"]
    facts: dict[str, Any] = {"entities": 0, "curves": 0, "texts": 0, "text_skipped_non_ascii": 0}
    for item in iter_entities(sheet):
        kind = str(item.get("type") or "")
        layer = str(item.get("layer") or "")
        entry = sheet.layers.get(layer)
        entry = entry if isinstance(entry, Mapping) else {}
        facts["entities"] += 1
        if kind in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE"):
            ops.append("%s w" % _pdf_num(_pdf_pt(int(widths.get(layer, default_width)))))
            ops.append("[5 2.5] 0 d" if str(entry.get("linetype") or "") == LINETYPE_HIDDEN
                       else "[] 0 d")
            ops.append("0 G")
            facts["curves"] += 1
            if kind == "LINE":
                start, end = item["start"], item["end"]
                ops.append("%s %s m %s %s l S"
                           % (_pdf_num(_pdf_pt(start[0])), _pdf_num(_pdf_pt(start[1])),
                              _pdf_num(_pdf_pt(end[0])), _pdf_num(_pdf_pt(end[1]))))
            elif kind == "LWPOLYLINE":
                points = list(item.get("points") or [])
                if not points:
                    continue
                ops.append("%s %s m" % (_pdf_num(_pdf_pt(points[0][0])),
                                        _pdf_num(_pdf_pt(points[0][1]))))
                for point in points[1:]:
                    ops.append("%s %s l" % (_pdf_num(_pdf_pt(point[0])),
                                            _pdf_num(_pdf_pt(point[1]))))
                if item.get("closed"):
                    ops.append("h")
                ops.append("S")
            else:
                cx, cy = _pdf_pt(item["center"][0]), _pdf_pt(item["center"][1])
                radius = _pdf_pt(item["radius_nm"])
                if kind == "CIRCLE":
                    ops.extend(_pdf_arc_ops(cx, cy, radius, 0.0, 360.0))
                else:
                    start_deg = float(item.get("start_deg") or 0.0)
                    end_deg = float(item.get("end_deg") or 0.0)
                    ops.extend(_pdf_arc_ops(cx, cy, radius, start_deg,
                                            (end_deg - start_deg) % 360.0 or 360.0))
        elif kind in ("TEXT", "DIMENSION"):
            raw = str(item.get("text") or "")
            escaped = _pdf_ascii(raw)
            if not raw.strip():
                continue
            if escaped is None:
                facts["text_skipped_non_ascii"] += 1
                continue
            at = item.get("at") or (0, 0)
            size = _pdf_pt(item.get("height_nm") or fallback_height)
            ops += ["BT", "0 g", "/F1 %s Tf" % _pdf_num(size),
                    "%s %s Td" % (_pdf_num(_pdf_pt(at[0])), _pdf_num(_pdf_pt(at[1]))),
                    "(%s) Tj" % escaped, "ET"]
            facts["texts"] += 1
    ops.append("Q")
    return ("\n".join(ops) + "\n").encode("latin-1"), facts


def _pdf_write_file(payload: bytes, out_path: str) -> None:
    """把 PDF 字节落到 `out_path`（建父目录；落盘后回读复算字节数，不一致即抛错）。"""
    out_path = guard_output(out_path)
    directory = Path(out_path).parent
    if str(directory):
        directory.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(payload)
    if Path(out_path).stat().st_size != len(payload):
        _fail("DrawingError", "pdf write truncated", path=out_path,
              expected=len(payload), written=Path(out_path).stat().st_size)


def _pdf_container(objects: Sequence[bytes]) -> bytes:
    """PDF 1.4 容器：对象表 + 逐字节正确的 xref（无时间戳 ⇒ 同输入逐字节相同）。"""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_at))
    return bytes(out)


def render_pdf_pages(sheets: Sequence[Sheet]) -> bytes:
    """多个布局 → **一个**真 PDF（一布局一页）：`/Count == len(sheets)` ∧ 页对象数 == len(sheets)。"""
    pages = list(sheets)
    if not pages:
        _fail("DrawingError", "render_pdf_pages needs at least one sheet", sheets=0)
    objects: list[bytes] = [b"", b"",
                            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids: list[int] = []
    for sheet in pages:
        stream, facts = _pdf_page_stream(sheet)
        if int(facts["text_skipped_non_ascii"]) > 0:
            # 降级要看得见（本模块的既有口径）：Helvetica 没有这些字形，PDF 侧跳过它们；
            # SVG/DXF 两支仍然带这些文本 ⇒ 差异写进 diagnostics，不静默丢。
            sheet.note("pdf: %d text item(s) text_skipped_non_ascii -- the PDF builtin writer only "
                       "has WinAnsi glyphs (SVG/DXF exports keep them)"
                       % int(facts["text_skipped_non_ascii"]))
        content_id = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
        page_id = len(objects) + 1
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Contents %d 0 R "
                        "/Resources << /Font << /F1 3 0 R >> >> >>"
                        % (_pdf_num(_pdf_pt(sheet.width_nm)), _pdf_num(_pdf_pt(sheet.height_nm)),
                           content_id)).encode("ascii"))
        kids.append(page_id)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = ("<< /Type /Pages /Count %d /Kids [%s] >>"
                  % (len(kids), " ".join("%d 0 R" % kid for kid in kids))).encode("ascii")
    return _pdf_container(objects)


def render_pdf_page(sheet: Sheet) -> bytes:
    """一张图纸（= 一个布局）→ 一页真 PDF（`/Count 1`；`render_pdf_pages` 的单页形态）。"""
    return render_pdf_pages([sheet])


def _pdf_verify(payload: bytes) -> dict[str, Any]:
    """回读复验：魔数 / 页对象数 / `/Count` 字段（读不到记 -1，不记 0 —— 三态不许同形）。"""
    counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", payload)]
    return {"magic": int(payload[:5] == b"%PDF-"),
            "pages": len(re.findall(rb"/Type\s*/Page(?![s])", payload)),
            "count_field": counts[0] if counts else -1, "count_fields": counts,
            "bytes": len(payload)}


def export_pdf_pages(sheets: Sequence[Sheet], *, out: str) -> str:
    """多布局 → **单张多页 PDF**（A8 的 single_file 合并器），返回实际落盘路径。

    落盘后**回读复验**（`_pdf_verify`）：魔数 / 页对象数 / `/Count` 三者不一致 ⇒ 抛 `DrawingError`
    （绝不把一个页数对不上的产物当成功返回）。
    """
    pages = list(sheets)
    payload = render_pdf_pages(pages)
    out_path = _with_extension(str(out), "pdf")
    _pdf_write_file(payload, out_path)
    facts = _pdf_verify(Path(out_path).read_bytes())
    if (facts["magic"] != 1 or facts["pages"] != len(pages)
            or facts["count_field"] != len(pages) or facts["bytes"] != len(payload)):
        _fail("DrawingError", "written pdf failed its own page-count verification",
              path=out_path, sheets=len(pages), **facts)
    return out_path

def register_pdf_backend(render: Any) -> Any:
    """注册 PDF 渲染后端：`render(svg_text, out_path) -> None|str`（写不写得出由它负责）。

    返回**注销闭包**。本模块不 import 任何第三方包，PDF 能力一律走这个显式入口（缺席即降级 SVG）。
    """
    if not callable(render):
        raise TypeError("register_pdf_backend expects a callable")
    _PDF_BACKENDS.append(render)

    def unregister() -> None:
        try:
            _PDF_BACKENDS.remove(render)
        except ValueError:
            pass

    return unregister


def _pdf_backend() -> Any | None:
    """取 PDF 后端：显式注册优先；否则看 `CADCORE_PDF_BACKEND="module:function"`；都没有 ⇒ None。"""
    if _PDF_BACKENDS:
        return _PDF_BACKENDS[-1]
    spec = os.environ.get(ENV_PDF_BACKEND, "").strip()
    if not spec:
        return None
    module_name, _, attribute = spec.partition(":")
    attribute = attribute or "render"
    try:
        module = importlib.import_module(module_name)
        render = getattr(module, attribute)
    except Exception as exc:  # noqa: BLE001 - 配错了要报出来，不许静默降级成"没有后端"
        LOG.warning("CADCORE_PDF_BACKEND=%r is unusable: %s", spec, exc)
        return None
    return render if callable(render) else None


def pdf_backend_available() -> bool:
    """PDF 后端是否可用（判定行用；不可用 ⇒ `export_sheet(fmt="pdf")` 会降级成 SVG）。"""
    return _pdf_backend() is not None


# ── DXF 写出（A13 缺席时的显式降级路径）────────────────────────────────────────────────

_LAYER_COLORS: dict[str, int] = {
    LAYER_VISIBLE: 7,
    LAYER_HIDDEN: 3,
    LAYER_DIMENSION: 4,
    LAYER_FRAME: 1,
    LAYER_TEXT: 2,
}


def _render_dxf(sheet: Sheet, *, version: str) -> tuple[bytes, str]:
    """DXF 字节：优先 A13 桥（`sheet_to_dxf`），缺席用内置写出器（两者都在 facts 里标出来）。"""
    bridge = _optional_module("cadcore.io.dxf_bridge")
    exporter = getattr(bridge, "sheet_to_dxf", None) if bridge is not None else None
    if callable(exporter):
        try:
            payload = exporter(sheet, version=str(version))
        except TypeError:
            payload = exporter(sheet)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if isinstance(payload, (bytes, bytearray)):
            return (bytes(payload), "cadcore.io.dxf_bridge.sheet_to_dxf")
        sheet.note("io.dxf_bridge.sheet_to_dxf returned a non-bytes payload -- builtin writer used")
    elif bridge is None:
        sheet.note("io.dxf_bridge is absent -- builtin minimal DXF writer used "
                   "(declared in sheet_facts()['dxf_writer'])")
    return (_builtin_dxf(sheet, version=version), "builtin_minimal_ascii")


def _builtin_dxf(sheet: Sheet, *, version: str) -> bytes:
    """极简 ASCII DXF 写出器（R12 起可读的结构；**不是** A13 的替代品，只是它缺席时的降级）。"""
    acadver = DXF_ACADVER.get(str(version).upper())
    if acadver is None:
        _fail("DrawingError", tr(sheet.language, "err.dxf_version_unsupported", version=str(version)),
              version=str(version), known=sorted(DXF_ACADVER))
    entities = iter_entities(sheet)
    widths = _line_widths_nm(sheet)
    per_unit = _nm_per_unit(sheet.unit)
    lines: list[str] = []
    #: R12（AC1009）没有 LWPOLYLINE（R14+）与组码 370（R2000+）⇒ 声明 R12 就写 R12 合法的结构。
    r12 = acadver == DXF_ACADVER["R12"]
    sheet.note("dxf: header unit variables ($INSUNITS/$MEASUREMENT/$LUNITS/$LUPREC) are omitted "
               "-- that mapping is not verified (I18N-DESIGN 4); coordinates are written in the "
               f"document unit {sheet.unit!r}")

    def single_line(value: Any, what: str, code: int) -> str:
        """图层名/线型名必须单行：含 CR/LF 或控制字符的名字**结构化拒绝**（不静默截断）。

        A3 缺陷 RC：`"A\\n999\\ninjected"` 曾被静默截成 `A` 并在产物里注入一条 `999` 组码 ——
        名字是键，折行等于改字，而 DXF 的组码 2/6/8 没有多行语义 ⇒ 只有拒绝一条路。
        """
        text = str(value)
        if not text.strip() or any(ord(char) < 32 or ord(char) == 127 for char in text):
            _fail("DrawingError", f"DXF {what} must be a single-line name (group code {code})",
                  reason="dxf_illegal_symbol_name", field=what, group_code=code, value=repr(text))
        return text

    for _item in entities:
        single_line(_item.get("layer"), "entity layer", 8)
    for _name, _entry in sheet.layers.items():
        single_line(_name, "layer name", 2)
        single_line(_entry.get("linetype", LINETYPE_CONTINUOUS), "linetype name", 6)

    import decimal  # noqa: PLC0415 - 只依赖标准库；局部 import 免得顶层再加一个名字

    def coord(value_nm: Any) -> str:
        """纳米 → **文档单位**的十进制原文（**精确除法，不过 float**；与 `io.dxf_bridge` 同口径）。

        mm 的分母只含 2/5 因子 ⇒ 可表示范围内逐位精确；**inch** 的 1 inch = 25 400 000 nm 含 127
        因子 ⇒ 十进制不终止，取 17 位有效数字（相对偏差 <= 5e-18，远小于真源的 1 nm）。旧口径
        `round(nm / per_unit, 6)` 在 inch 下的量化步长是 25.4 nm（实测 1248 分量只有 4 个命中）。
        """
        with decimal.localcontext() as context:
            context.prec = 17
            text = format(decimal.Decimal(int(value_nm)) / decimal.Decimal(per_unit), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text if text not in ("", "-", "-0") else "0"

    def tag(code: Any, value: Any) -> None:
        lines.append(str(code))
        lines.append(_dxf_value(value))

    tag(0, "SECTION")
    tag(2, "HEADER")
    tag(9, "$ACADVER")
    tag(1, acadver)
    bounds = _sheet_bounds_nm(sheet, entities)
    if bounds is not None:
        tag(9, "$EXTMIN")
        tag(10, coord(bounds[0]))
        tag(20, coord(bounds[1]))
        tag(30, 0)
        tag(9, "$EXTMAX")
        tag(10, coord(bounds[2]))
        tag(20, coord(bounds[3]))
        tag(30, 0)
    tag(0, "ENDSEC")

    tag(0, "SECTION")
    tag(2, "TABLES")
    tag(0, "TABLE")
    tag(2, "LTYPE")
    tag(70, 2)
    #: 线型图案（DXF 的 `DASHED` 惯用定义：总长 0.75、实段 0.5、空段 0.25，单位=图纸单位）。
    for name, description, pattern in ((LINETYPE_CONTINUOUS, "Solid line", []),
                                       (LINETYPE_HIDDEN, "Dashed __ __ __", [0.5, -0.25])):
        tag(0, "LTYPE")
        tag(2, name)
        tag(70, 0)
        tag(3, description)
        tag(72, 65)
        tag(73, len(pattern))
        tag(40, 0.75 if pattern else 0.0)
        for item in pattern:
            tag(49, item)
    tag(0, "ENDTAB")
    tag(0, "TABLE")
    tag(2, "STYLE")
    tag(70, 1)
    tag(0, "STYLE")
    tag(2, "STANDARD")
    tag(70, 0)
    tag(40, 0.0)
    tag(41, 1.0)
    tag(50, 0.0)
    tag(71, 0)
    tag(42, 2.5)
    tag(3, "txt")
    tag(4, "")
    tag(0, "ENDTAB")
    tag(0, "TABLE")
    tag(2, "LAYER")
    tag(70, max(1, len(sheet.layers)))
    for name in sorted(sheet.layers):
        entry = sheet.layers[name]
        tag(0, "LAYER")
        tag(2, name)
        tag(70, 0)
        tag(62, _LAYER_COLORS.get(name, 7))
        tag(6, str(entry.get("linetype", LINETYPE_CONTINUOUS)))
        if not r12:
            # 组码 370 的单位是 1/100 毫米（**不是**纳米，也不是图纸单位）；它是 R2000+ 结构，
            # 写进 R12 产物就是"老 reader 打不开/丢图层"的非法件 ⇒ R12 不写（R12 无线宽载体）。
            tag(370, int(round(int(widths.get(name, widths["_default"])) / 10_000)))
    tag(0, "ENDTAB")
    tag(0, "ENDSEC")

    dimensions = [item for item in entities if item["type"] == "DIMENSION"]
    if dimensions:
        tag(0, "SECTION")
        tag(2, "BLOCKS")
        for index in range(1, len(dimensions) + 1):
            block = f"*D{index}"
            tag(0, "BLOCK")
            tag(8, LAYER_DIMENSION)
            tag(2, block)
            tag(70, 0)
            tag(10, 0)
            tag(20, 0)
            tag(30, 0)
            tag(3, block)
            tag(1, "")
            tag(0, "ENDBLK")
            tag(8, LAYER_DIMENSION)
        tag(0, "ENDSEC")

    tag(0, "SECTION")
    tag(2, "ENTITIES")
    dimension_index = 0
    for item in entities:
        kind = item["type"]
        if kind == "LINE":
            tag(0, "LINE")
            tag(8, item["layer"])
            tag(10, coord(item["start"][0]))
            tag(20, coord(item["start"][1]))
            tag(30, 0)
            tag(11, coord(item["end"][0]))
            tag(21, coord(item["end"][1]))
            tag(31, 0)
        elif kind == "CIRCLE":
            tag(0, "CIRCLE")
            tag(8, item["layer"])
            tag(10, coord(item["center"][0]))
            tag(20, coord(item["center"][1]))
            tag(30, 0)
            tag(40, coord(item["radius_nm"]))
        elif kind == "ARC":
            tag(0, "ARC")
            tag(8, item["layer"])
            tag(10, coord(item["center"][0]))
            tag(20, coord(item["center"][1]))
            tag(30, 0)
            tag(40, coord(item["radius_nm"]))
            tag(50, item["start_deg"])
            tag(51, item["end_deg"])
        elif kind == "LWPOLYLINE":
            if r12:
                # R12（AC1009）没有 LWPOLYLINE（R14+ 才有）⇒ 降级成 R12 合法的 POLYLINE + 顶点表
                tag(0, "POLYLINE")
                tag(8, item["layer"])
                tag(66, 1)
                tag(70, 1 if item.get("closed") else 0)
                for x, y in item["points"]:
                    tag(0, "VERTEX")
                    tag(8, item["layer"])
                    tag(10, coord(x))
                    tag(20, coord(y))
                    tag(30, 0)
                tag(0, "SEQEND")
            else:
                tag(0, "LWPOLYLINE")
                tag(8, item["layer"])
                tag(90, len(item["points"]))
                tag(70, 1 if item.get("closed") else 0)
                for x, y in item["points"]:
                    tag(10, coord(x))
                    tag(20, coord(y))
        elif kind == "TEXT":
            tag(0, "TEXT")
            tag(8, item["layer"])
            tag(10, coord(item["at"][0]))
            tag(20, coord(item["at"][1]))
            tag(30, 0)
            tag(40, coord(item["height_nm"]))
            tag(1, item["text"])
            tag(7, "STANDARD")
            tag(72, 0)
            tag(11, coord(item["at"][0]))
            tag(21, coord(item["at"][1]))
            tag(31, 0)
        elif kind == "DIMENSION":
            dimension_index += 1
            tag(0, "DIMENSION")
            tag(8, item["layer"])
            tag(2, f"*D{dimension_index}")
            tag(3, "STANDARD")
            tag(70, _dxf_dimension_type(item["kind"]))
            tag(1, item["text"])
            tag(10, coord(item["at"][0]))
            tag(20, coord(item["at"][1]))
            tag(30, 0)
            tag(11, coord(item["at"][0]))
            tag(21, coord(item["at"][1]))
            tag(31, 0)
            tag(42, coord(item["value_nm"]))
            refs = item.get("refs") or []
            tag(13, coord(item["at"][0]))
            tag(23, coord(item["at"][1]))
            tag(33, 0)
            tag(14, coord(item["at"][0] + (1 if refs else 0)))
            tag(24, coord(item["at"][1]))
            tag(34, 0)
    tag(0, "ENDSEC")
    tag(0, "EOF")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _dxf_dimension_type(kind: str) -> int:
    """DXF 组码 70：0=线性/旋转、2=角度、3=直径、4=半径（DXF 自身的类型登记）。"""
    return {"linear": 0, "angular": 2, "diameter": 3, "radial": 4}.get(str(kind), 0)


def _nm_per_unit(unit: str) -> int:
    """文档单位 → 每单位多少纳米（导出件用**文档单位**，真源仍是整数纳米）。"""
    return int(round(25.4 * NM_PER_MM)) if str(unit) == "inch" else NM_PER_MM


def _dxf_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-") else "0"
    return str(value)


def _line_widths_nm(sheet: Sheet) -> dict[str, int]:
    """线宽：标准档 `line_widths` 有值就用它，否则内置标称值（来源已在 `sheet.line_width_source` 记 NA）。"""
    default_nm = 350_000
    widths = {"_default": default_nm, LAYER_VISIBLE: default_nm, LAYER_HIDDEN: 180_000,
              LAYER_DIMENSION: 180_000, LAYER_FRAME: 700_000, LAYER_TEXT: 180_000}
    declared = _profile_cache(sheet).get("line_widths")
    if isinstance(declared, Mapping):
        for key, value in declared.items():
            try:
                widths[str(key)] = int(float(value) * NM_PER_MM)
            except (TypeError, ValueError):
                continue
    return widths


def _sheet_bounds_nm(sheet: Sheet, entities: Sequence[Mapping[str, Any]]) -> tuple[int, int, int, int] | None:
    points: list[tuple[int, int]] = []
    for item in entities:
        if item["type"] == "LINE":
            points.append((item["start"][0], item["start"][1]))
            points.append((item["end"][0], item["end"][1]))
        elif item["type"] in ("CIRCLE", "ARC"):
            cx, cy = item["center"]
            radius = int(item["radius_nm"])
            points.extend([(cx - radius, cy - radius), (cx + radius, cy + radius)])
        elif item["type"] == "LWPOLYLINE":
            points.extend((int(x), int(y)) for x, y in item["points"])
        elif item["type"] in ("TEXT", "DIMENSION"):
            points.append((int(item["at"][0]), int(item["at"][1])))
    if not points:
        return (0, 0, sheet.width_nm, sheet.height_nm)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


# ── SVG 写出（纯标准库；文本走语言包 ⇒ 无中文字面量）───────────────────────────────────


def _render_svg(sheet: Sheet) -> bytes:
    """图纸 → SVG 文本（viewBox 用整数纳米，`width`/`height` 用毫米；y 轴按 SVG 习惯翻转）。"""
    height = sheet.height_nm
    widths = _line_widths_nm(sheet)
    entities = iter_entities(sheet)
    font = _font_family(sheet)
    title = tr(sheet.language, "app.title")
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
         f'width="{_mm_text(sheet.width_nm)}mm" height="{_mm_text(sheet.height_nm)}mm" '
         f'viewBox="0 0 {sheet.width_nm} {sheet.height_nm}">'),
        f"<title>{_xml_escape(title)}</title>",
        f'<desc>{_xml_escape(f"{sheet.size} {sheet.scale}")}</desc>',
        f'<rect x="0" y="0" width="{sheet.width_nm}" height="{height}" fill="#ffffff"/>',
    ]
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in entities:
        grouped.setdefault(str(item["layer"]), []).append(item)
    for layer in [name for name in (LAYER_FRAME, LAYER_VISIBLE, LAYER_HIDDEN, LAYER_DIMENSION, LAYER_TEXT)
                  if name in grouped] + [name for name in grouped if name not in
                                         (LAYER_FRAME, LAYER_VISIBLE, LAYER_HIDDEN,
                                          LAYER_DIMENSION, LAYER_TEXT)]:
        entry = sheet.layers.get(layer, {})
        stroke = "#000000"
        width = int(widths.get(layer, widths["_default"]))
        dash = ' stroke-dasharray="5,2.5"' if entry.get("linetype") == LINETYPE_HIDDEN else ""
        parts.append(f'<g id="layer-{_xml_escape(layer)}" data-layer="{_xml_escape(layer)}" '
                     f'fill="none" stroke="{stroke}" stroke-width="{width}"{dash}>')
        for item in grouped[layer]:
            parts.append(_svg_entity(item, height, widths))
        parts.append("</g>")
    parts.append("</svg>")
    return ("\n".join(parts) + "\n").encode("utf-8")


def _svg_entity(item: Mapping[str, Any], height: int, widths: Mapping[str, int]) -> str:
    kind = item["type"]
    if kind == "LINE":
        x1, y1 = item["start"]
        x2, y2 = item["end"]
        return (f'<line x1="{x1}" y1="{height - y1}" x2="{x2}" y2="{height - y2}" '
                f'data-entity="LINE"/>')
    if kind == "CIRCLE":
        cx, cy = item["center"]
        return (f'<circle cx="{cx}" cy="{height - cy}" r="{item["radius_nm"]}" '
                f'data-entity="CIRCLE"/>')
    if kind == "ARC":
        cx, cy = item["center"]
        radius = int(item["radius_nm"])
        start = float(item["start_deg"])
        end = float(item["end_deg"])
        sweep = (end - start) % 360.0 or 360.0
        x0 = cx + radius * math.cos(math.radians(start))
        y0 = height - (cy + radius * math.sin(math.radians(start)))
        x1 = cx + radius * math.cos(math.radians(start + sweep))
        y1 = height - (cy + radius * math.sin(math.radians(start + sweep)))
        large = 1 if sweep > 180.0 else 0
        return (f'<path d="M {x0:.3f} {y0:.3f} A {radius} {radius} 0 {large} 0 {x1:.3f} {y1:.3f}" '
                f'data-entity="ARC"/>')
    if kind == "LWPOLYLINE":
        points = " ".join(f"{int(x)},{height - int(y)}" for x, y in item["points"])
        return f'<polyline points="{points}" data-entity="LWPOLYLINE"/>'
    if kind == "TEXT":
        x, y = item["at"]
        anchor = {"start": "start", "middle": "middle", "end": "end"}.get(str(item.get("anchor")), "start")
        return (f'<text x="{x}" y="{height - y}" font-size="{item["height_nm"]}" '
                f'font-family="{_xml_escape(str(item.get("font", "sans-serif")))}" '
                f'text-anchor="{anchor}" fill="#000000" stroke="none" '
                f'data-field="{_xml_escape(str(item.get("field", "")))}" '
                f'data-role="{_xml_escape(str(item.get("role", "")))}">{_xml_escape(str(item["text"]))}</text>')
    if kind == "DIMENSION":
        x, y = item["at"]
        marker = 500_000
        text_height = DIM_TEXT_HEIGHT_NM
        return (f'<g data-entity="DIMENSION" data-kind="{_xml_escape(str(item["kind"]))}" '
                f'data-value-nm="{item["value_nm"]}">'
                f'<circle cx="{x}" cy="{height - y}" r="{marker}" fill="#000000" stroke="none"/>'
                f'<text x="{x}" y="{height - y - 2 * marker}" font-size="{text_height}" '
                f'font-family="sans-serif" fill="#000000" stroke="none">{_xml_escape(str(item["text"]))}</text>'
                f'</g>')
    return ""


def _mm_text(value_nm: int) -> str:
    return f"{int(value_nm) / NM_PER_MM:.3f}"


def _font_family(sheet: Sheet) -> str:
    """字体族：标准档 `default_font_family` 优先，否则用语言包 `_meta.font_fallback`（**都是数据**）。"""
    declared = _field(_profile_cache(sheet), "default_font_family", None)
    if declared:
        return str(declared)
    catalogs = _chain_catalogs(sheet.language)
    for _code, catalog in catalogs:
        try:
            families = tuple(catalog.font_fallback())
        except Exception:  # noqa: BLE001 - 语言包结构异常不该阻断出图
            families = ()
        if families:
            return ", ".join(str(item) for item in families)
    return "sans-serif"


def _xml_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# --------------------------------------------------------------------------------------
# A11: sheet_facts
# --------------------------------------------------------------------------------------


def sheet_facts(sheet: Sheet) -> dict[str, Any]:
    """判定行读的那份事实（口径统一，三态可辨）：

    - `dim_entities`：图纸上的 `DIMENSION` 实体数（**0 是真为零**，因为计数永远采得到）；
    - `projection`：`first_angle` / `third_angle` / `NA`（标准档未核标 ⇒ NA，不编值）；
    - `hidden_layer`：`present` / `absent` / `NA`（没有视图 ⇒ NA，不是 absent）；
    - `titleblock_lang`：标题栏实际使用的语言码；**还没渲染过 ⇒ -1（未采）**，不套用图纸语言。
    """
    angles = sorted({dim.kind for dim in sheet.dimensions})
    hidden_count = sheet.hidden_curve_count()
    if not sheet.views:
        hidden_layer = NA
    elif sheet.hidden_layer_declared():
        hidden_layer = "present"
    else:
        hidden_layer = "absent"
    last_export = sheet.exports[-1] if sheet.exports else None
    dim_style = dimension_style(sheet)
    facts: dict[str, Any] = {
        "sheet_uuid": sheet.uuid,
        "doc_uuid": sheet.doc_uuid if sheet.doc_uuid else NA,
        "standard": sheet.standard_profile,
        "language": sheet.language,
        "language_resolved": language_resolved(sheet.language) or UNMEASURED,
        "size": sheet.size,
        "scale": sheet.scale,
        "unit": sheet.unit,
        "projection": PROJECTION_ANGLE_FACTS.get(sheet.projection_angle or "", NA),
        "projection_angle": sheet.projection_angle if sheet.projection_angle else NA,
        "projection_source": sheet.projection_angle_source,
        "sheet_width_nm": sheet.width_nm,
        "sheet_height_nm": sheet.height_nm,
        "sheet_size_source": sheet.sheet_size_source,
        "standard_source": sheet.standard_source,
        "standard_na_fields": list(sheet.standard_na_fields),
        "views": len(sheet.views),
        "visible_entities": sum(len(view.visible) for view in sheet.views),
        "hidden_entities": hidden_count,
        "hidden_layer": hidden_layer,
        "hidden_layer_name": LAYER_HIDDEN,
        "hidden_layer_separate_from_visible": LAYER_HIDDEN != LAYER_VISIBLE,
        "dim_entities": len(sheet.dimensions),
        "dim_kinds": angles,
        "dim_values_nm": [int(dim.value_nm) for dim in sheet.dimensions],
        "dim_texts": [dim.text for dim in sheet.dimensions],
        # 文档级标注样式的出图侧解析（B6 RC-S3 的落点，三态可辨）：
        # `style_name` 为 NA = 文档没有当前样式（**不是**"有样式但名叫 NA"）；
        # `sources` 为空 = 一个样式字段都没消费；`unmapped_records` = 存在但没映射到图面的记录条数。
        "dim_style_name": dim_style.style_name or NA,
        "dim_style_decimals": dim_style.decimals,
        "dim_style_text": dim_style.text or NA,
        "dim_style_sources": list(dim_style.sources),
        "dim_style_unmapped_records": int(dim_style.unmapped_records),
        "dim_value_source": sorted({dim.measured_from for dim in sheet.dimensions}),
        "dim_terminator": next((dim.terminator for dim in sheet.dimensions), NA),
        "titleblock_lang": sheet.title_block_lang if sheet.title_block_lang else UNMEASURED,
        "titleblock_lang_requested": sheet.title_block_lang_requested or UNMEASURED,
        "titleblock_rendered": bool(sheet.title_fields),
        "title_block": dict(sheet.title_fields),
        "title_block_keys": dict(sheet.title_block_keys),
        "decimal_separator": sheet.decimal_separator or ".",
        "decimal_separator_source": sheet.decimal_separator_source,
        "text_height_nm": sheet.text_height_nm,
        "text_height_source": sheet.text_height_source,
        "line_width_source": sheet.line_width_source,
        "layers": sorted(sheet.layers),
        "layer_table": {name: dict(entry) for name, entry in sheet.layers.items()},
        "export_writer": last_export.writer if last_export else UNMEASURED,
        "dxf_writer": next((item.writer for item in reversed(sheet.exports)
                            if item.actual == "dxf"), UNMEASURED),
        "export_unit": sheet.unit if last_export else NA,
        "export_format": last_export.actual if last_export else NA,
        "export_requested_format": last_export.requested if last_export else NA,
        "export_downgraded": bool(last_export.downgraded) if last_export else NA,
        "export_path": last_export.path if last_export else NA,
        "export_count": len(sheet.exports),
        "pdf_backend": pdf_backend_available(),
        "pdf_builtin": PDF_BUILTIN_WRITER,
        "dependencies": dependency_state(),
        "diagnostics": list(sheet.diagnostics),
    }
    return facts


def sheet_to_l1(sheet: Sheet) -> dict[str, Any]:
    """图纸 → L1 `sheets[]` 条目（字段级照 `MVP-INTERFACES.md` §C：`views` / `dimensions` / `title_block`）。

    `title_block` 存的是 `{字段名: 语言包 key}`（**文本存 key，不存已翻好的串**），
    渲染后的成品文案在 `sheet.title_fields`（不落库，按 `title_block(language=…)` 现取）。
    """
    return {
        "uuid": sheet.uuid,
        "standard_profile": sheet.standard_profile,
        "size": sheet.size,
        "scale": sheet.scale,
        "views": [
            {
                "uuid": view.uuid,
                "direction": list(VIEW_DIRECTIONS.get(view.name, (0, 0, 0))),
                "at_nm": [int(view.at_nm[0]), int(view.at_nm[1])],
                "hidden": bool(view.hidden),
                "cache": True,
            }
            for view in sheet.views
        ],
        "dimensions": [dim.uuid for dim in sheet.dimensions],
        "title_block": dict(sheet.title_block_keys),
    }
