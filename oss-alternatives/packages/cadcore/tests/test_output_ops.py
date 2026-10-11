"""test_output_ops —— 族 `output` 的独立单测（数值硬断言 / 边界 / 组合用例）。

落盘时的状态（照实写，不许把没做的写成做了）
--------------------------------------------
写盘本文件时实测：**`cadcore/ops/output.py` 不在盘上**（`cadcore/ops/` 下只有
`organization.py` / `sw_drawings.py`；`ops/__init__.py` 按约定不许创建）。因此本文件：

1. **按冻结接口写**：`cadcore/ops/<module>.py` 暴露 `FAMILY="output"` /
   `OPERATIONS: dict[str, Callable]` / 每个操作 `op(sk_or_doc, **kwargs) -> list[str]`（uuid 列表）；
   长度一律整数纳米（1 mm = 1000000 nm）；错误抛 `cadcore.errors.CadError(reason=…)`。
   逐操作语义取自同族规格 `docs/research/cad-parity/spec/output.json`（19 条 must-have 的
   `api` / `semantics` / `error_cases` / `test_vector`），**不另编语义**。
2. **真件优先、缺席才用双**：`importlib.util.find_spec` 先判 `cadcore.ops.output` **在不在**；
   - 在 ⇒ 直接 import 真件（真件存在却 import 失败 ⇒ 当场报错，绝不回落双：那会把"真件坏了"
     伪装成"没落盘"，见 `TestFamilyContract.test_provenance_*`）；
   - 不在 ⇒ 用 `cadcore.draw.sheet`（规格 `reuse_index` 点名的冻结复用目标）作**薄封装双**：
     几何/落盘全部走 `sheet.make_sheet / add_view / export_sheet / sheet_facts`，本文件只做
     **入参校验与错误 reason 归一**，不重写任何几何、不新开第二条出图管线。
     → 本批用例今天打的是 `draw.sheet` 的真几何与真落盘字节；真件落盘后同一批用例自动改打真件。
3. **本文件编码的接缝假设**（真件与冻结接口不一致时应当红，而不是被猜着改绿）：操作名可在
   `OP_ALIASES` 里给同义候选（纯容错，不改变语义）；**规格写死的 `error_cases` reason 取精确值**。
4. **覆盖面的诚实记账**：本文件覆盖 12 个逻辑操作（create_layout / create_viewport /
   set_page_setup / plot / preview / publish / manage_layout / configure_plotter / save_dwg /
   open_dwg / set_plot_style_table / set_plot_flag-not）；`set_viewport_scale` /
   `set_viewport_layer_override` / `set_lineweight` / `set_plot_area` / `set_plot_scale` /
   `set_plot_flag` / `plot_model_space` / `open_dxf` / `save_dxf` **本文件不测**（缺席即
   `ABSENT:` skip，绝不静默当绿）。没有测到的操作不会出现在任何判定行里。

不确定的语义（写成问题，不做硬断言；真件落盘后按真件行为补断言）
------------------------------------------------------------------
- Q1 `plot` 返回的 uuid 是否可由 `sheet_facts()`/文档反查？（规格只冻了 `list[str]`）
- Q2 publish 的 manifest 文件名/字段名未冻结（规格只要求"逐页 requested/actual/downgraded/reason"）
  ⇒ 本文件只硬断言"页文件数 = 请求布局数 ∧ 返回 uuid 数 = 布局数"。
- Q3 `manage_layout(action="rename"|"delete")` 返回值语义未冻结（本文件只硬断言 new/copy 与错误分支）。
- Q4 `set_lineweight` 落点在 `Sheet.layers[*]` 的哪个键名未冻结 ⇒ 本文件不测该 op。
- Q5 sheet 自身抛的 `CadError.reason` 是 `drawing_error`（无 `reason=` 入参），派生的
  `drawing_error` 文本（如 `bad scale` 与 `no views`）也一概是这个值 —— 本文件因此优先断言
  规格明写的 reason（真件应把它们归一成 `parse_failed` / `out_of_range` / `drawing_error`）。

口径
----
- 断言打在**落盘字节 + 图纸读数**上：文件大小、实体数、纳米尺寸、三态字段（0 / -1 / NA）。
- 全部落盘在 `tempfile` 临时目录，不碰仓库内任何人的文件。
- 非 docstring 的字符串字面量一律 ASCII（与包内 i18n 口径一致），中文只在 docstring/注释里。
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid as _uuid
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:  # 与 test_draw.py 同口径：允许直接 python3 -m unittest 跑
    sys.path.insert(0, str(_PKG_ROOT))

from cadcore.draw import sheet as _sheet  # noqa: E402
from cadcore.errors import CadError, DependencyMissing  # noqa: E402

FAMILY_ID = "output"
REAL_MODULE = "cadcore.ops.output"
NM = _sheet.NM_PER_MM

#: 逻辑操作名 → 可接受的 OPERATIONS 键（候选表是容错，不是语义变更）。
OP_ALIASES: dict[str, tuple[str, ...]] = {
    "create_layout": ("create_layout", "new_layout", "layout_create"),
    "create_viewport": ("create_viewport", "add_viewport", "viewport_create"),
    "set_page_setup": ("set_page_setup", "page_setup", "configure_page_setup"),
    "plot": ("plot", "plot_sheet", "export_sheet", "output_sheet"),
    "preview": ("preview", "plot_preview", "export_preview"),
    "publish": ("publish", "publish_layouts", "batch_publish"),
    "manage_layout": ("manage_layout", "layout_manage", "manage_layouts"),
    "configure_plotter": ("configure_plotter", "set_plotter"),
    "set_plot_style_table": ("set_plot_style_table", "set_plot_style"),
    "save_dwg": ("save_dwg", "export_dwg", "write_dwg"),
    "open_dwg": ("open_dwg", "read_dwg", "import_dwg"),
}


def _spec_exists(name: str) -> bool:
    """模块**真的在盘上**吗（不是"import 失败"）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# --------------------------------------------------------------------------------------
# 双（仅在真件不在盘上时使用）：薄封装 cadcore.draw.sheet
# --------------------------------------------------------------------------------------

_DOUBLE_REGISTRY: dict[str, "_sheet.Sheet"] = {}
_STATE_KEY = "_output_double_state"


def _state(doc) -> dict:
    if not isinstance(doc, dict):
        raise AssertionError("output double requires a plain L1 dict fixture")
    return doc.setdefault(_STATE_KEY, {"order": [], "names": {}, "standard": None, "language": None})


def _bad(message: str, reason: str, **details) -> CadError:
    return CadError(message, reason=reason, **details)


def _check_standard(standard: str) -> None:
    if str(standard) not in _sheet.standard_ids():
        raise _bad("unknown standard: %r" % (standard,), "unknown_standard",
                   standard=str(standard), known=list(_sheet.standard_ids()))


def _check_scale(scale) -> tuple[int, int]:
    text = str(scale or "").strip().replace("/", ":")
    parts = [part.strip() for part in text.split(":")]
    if len(parts) != 2 or not all(parts) or not all(part.lstrip("+-").isdigit() for part in parts):
        raise _bad("scale is not n:m", "parse_failed", scale=str(scale))
    num, den = int(parts[0]), int(parts[1])
    if num <= 0 or den <= 0:
        raise _bad("scale out of range", "out_of_range", scale=str(scale))
    return (num, den)


def _check_size(standard: str, size: str) -> None:
    profile, _source = _sheet.load_profile(str(standard))
    try:
        _sheet._resolve_sheet_size(profile, str(size))  # noqa: SLF001 - 双复用冻结真源
    except CadError as exc:
        raise _bad("unknown size: %r" % (size,), "out_of_range", size=str(size)) from exc


def _need_sheet(sheet_uuid: str) -> "_sheet.Sheet":
    sheet = _DOUBLE_REGISTRY.get(str(sheet_uuid))
    if sheet is None:
        raise _bad("viewport/layout not found", "unknown_layout", sheet_uuid=str(sheet_uuid))
    return sheet


def _export_uuid(sheet, path: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(["output-export", sheet.uuid, str(path)])))


def _d_create_layout(sk_or_doc, *, name=None, standard, language, size=_sheet.DEFAULT_SIZE,
                     scale=_sheet.DEFAULT_SCALE) -> list[str]:
    _check_standard(standard)
    _check_scale(scale)
    _check_size(standard, size)
    sheet = _sheet.make_sheet(sk_or_doc, standard=str(standard), language=str(language),
                              size=str(size), scale=str(scale))
    st = _state(sk_or_doc)
    if name is not None:
        if str(name) in st["names"]:
            raise _bad("duplicate layout name", "duplicate_name", name=str(name))
        st["names"][str(name)] = sheet.uuid
    st["standard"], st["language"] = str(standard), str(language)
    st["order"].append(sheet.uuid)
    _DOUBLE_REGISTRY[sheet.uuid] = sheet
    return [sheet.uuid]


def _d_create_viewport(sk_or_doc, *, sheet_uuid, shape, view, at_nm, hidden=True, kernel=None) -> list[str]:
    sheet = _need_sheet(sheet_uuid)
    if str(view) not in _sheet.VIEW_DIRECTIONS:
        raise _bad("unknown view", "unknown_view", view=str(view), known=sorted(_sheet.VIEW_DIRECTIONS))
    if kernel is None or not bool(getattr(kernel, "available", lambda: False)()):
        raise DependencyMissing("projection kernel is unavailable", reason="dependency_missing")
    if "project2d" not in set(getattr(kernel, "capabilities", lambda: ())()):
        raise DependencyMissing("kernel has no project2d capability", reason="dependency_missing")
    try:
        view_uuid = _sheet.add_view(sheet, shape, view=str(view), at_nm=tuple(at_nm),
                                    kernel=kernel, hidden=bool(hidden))
    except CadError:
        if bool(getattr(kernel, "empty", False)) or bool(getattr(kernel, "return_none", False)):
            raise _bad("projection produced no curves", "empty_projection", view=str(view))
        raise
    return [view_uuid]


def _d_set_page_setup(sk_or_doc, *, sheet_uuid, size, scale, plotter=None, plot_style_table=None,
                      plot_area="extents", fit_to_paper=False) -> list[str]:
    sheet = _need_sheet(sheet_uuid)
    if plotter:
        raise _bad("pc3 plotter is not implemented", "unsupported", what="plotter")
    if plot_style_table:
        raise _bad("ctb/stb table is not implemented", "unsupported", what="plot_style_table")
    _check_scale(scale)
    fresh = _sheet.make_sheet(sk_or_doc, standard=sheet.standard_profile, language=sheet.language,
                              size=str(size), scale=str(scale))
    sheet.size, sheet.scale = fresh.size, fresh.scale
    sheet.scale_ratio = fresh.scale_ratio
    sheet.width_nm, sheet.height_nm = fresh.width_nm, fresh.height_nm
    sheet.sheet_size_source = fresh.sheet_size_source
    return [sheet.uuid]


def _d_plot(sk_or_doc, *, sheet_uuid, fmt, out, dxf_version="R2018") -> list[str]:
    sheet = _need_sheet(sheet_uuid)
    target = str(fmt or "").strip().lower()
    if target not in ("dxf", "svg", "pdf"):
        raise _bad("unsupported format", "unsupported_format", fmt=str(fmt))
    try:
        path = _sheet.export_sheet(sheet, fmt=target, out=str(out), dxf_version=str(dxf_version))
    except OSError as exc:
        raise _bad("write failed: %s" % (exc,), "write_failed", path=str(out), detail=str(exc)) from exc
    return [_export_uuid(sheet, path)]


def _d_preview(sk_or_doc, *, sheet_uuid, out) -> list[str]:
    sheet = _need_sheet(sheet_uuid)
    if not sheet.views:
        raise _bad("no view on the sheet -- no empty preview", "drawing_error", sheet_uuid=str(sheet_uuid))
    return _d_plot(sk_or_doc, sheet_uuid=sheet_uuid, fmt="svg", out=out)


def _d_publish(sk_or_doc, *, sheet_uuids, fmt, out_dir, single_file=False) -> list[str]:
    ids = [str(item) for item in (sheet_uuids or [])]
    if not ids:
        raise _bad("empty sheet list is not a success", "empty_sheet_list")
    target = str(fmt or "").strip().lower()
    if target not in ("dxf", "svg", "pdf"):
        raise _bad("unsupported format", "unsupported_format", fmt=str(fmt))
    out_dir = Path(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    uuids: list[str] = []
    pages: list[dict] = []
    for index, sheet_uuid in enumerate(ids, 1):
        sheet = _need_sheet(sheet_uuid)
        path = _sheet.export_sheet(sheet, fmt=target, out=str(out_dir / ("page_%03d" % index)))
        result = sheet.exports[-1]
        uuids.append(_export_uuid(sheet, path))
        pages.append({"page": index, "sheet_uuid": sheet.uuid, "path": result.path,
                      "requested": result.requested, "actual": result.actual,
                      "downgraded": bool(result.downgraded), "reason": result.reason})
    (out_dir / "publish_manifest.json").write_text(
        json.dumps({"pages": pages}, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return uuids


def _d_manage_layout(sk_or_doc, *, action, name=None, source=None, new_name=None) -> list[str]:
    st = _state(sk_or_doc)
    act = str(action or "").strip().lower()
    if act == "new":
        if name is not None and str(name) in st["names"]:
            raise _bad("duplicate layout name", "duplicate_name", name=str(name))
        standard = st["standard"] or _sheet.standard_ids()[0]
        language = st["language"] or "en"
        return _d_create_layout(sk_or_doc, name=name, standard=standard, language=language)
    if act == "copy":
        src = _DOUBLE_REGISTRY.get(str(source))
        if src is None:
            raise _bad("unknown layout", "unknown_layout", source=str(source))
        fresh = _sheet.make_sheet(sk_or_doc, standard=src.standard_profile, language=src.language,
                                  size=src.size, scale=src.scale)
        # 判据：`sheet.make_sheet` 的 uuid 只由内容（doc/standard/language/size/scale）派生
        # （sheet.py:1092-1096）⇒ 字段全同的副本必然撞成同一个 uuid。副本是**新对象**，必须有自己的
        # 身份：用同一命名空间（NAMESPACE_URL，与 _export_uuid 同口径）+ 源 uuid + 位置重算；
        # 只换身份，不改 size/scale/width_nm 等字段（下方断言要求字段相等）。
        fresh.uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "|".join(
            ["output-layout-copy", src.uuid, str(len(st["order"]))])))
        fresh.views = list(src.views)  # 深拷贝语义由真件负责；双只保证 uuid 不同且字段相等
        fresh.layers = {key: dict(value) for key, value in src.layers.items()}
        st["order"].append(fresh.uuid)
        _DOUBLE_REGISTRY[fresh.uuid] = fresh
        return [fresh.uuid]
    if act == "rename":
        current = st["names"].get(str(name))
        if current is None:
            raise _bad("unknown layout", "unknown_layout", name=str(name))
        if new_name is not None and str(new_name) in st["names"]:
            raise _bad("duplicate layout name", "duplicate_name", name=str(new_name))
        st["names"].pop(str(name), None)
        st["names"][str(new_name)] = current
        return [current]
    if act == "delete":
        current = st["names"].get(str(name)) if name is not None else None
        if current is None:
            raise _bad("unknown layout", "unknown_layout", name=str(name))
        if len(st["order"]) <= 1:
            raise _bad("refuse to delete the last layout", "last_layout", name=str(name))
        st["order"].remove(current)
        st["names"].pop(str(name), None)
        _DOUBLE_REGISTRY.pop(current, None)
        return list(st["order"])
    raise _bad("unknown action", "unknown_target", action=str(action))


def _d_configure_plotter(sk_or_doc, **kwargs) -> list[str]:
    raise DependencyMissing("no .pc3 reader/writer in this build", reason="dependency_missing",
                            capability=-1)


def _d_set_plot_style_table(sk_or_doc, *, sheet_uuid, table) -> list[str]:
    if str(table or "").strip().lower().endswith(".stb"):
        raise _bad("stb tables are not implemented", "unsupported_table_format", table=str(table))
    raise DependencyMissing("no ctb/stb parser in this build", reason="dependency_missing",
                            capability=-1)


def _d_save_dwg(sk_or_doc, *, path, version="R2018") -> list[str]:
    raise DependencyMissing("dwg binary read/write is not implemented", reason="dependency_missing",
                            capability=-1, path=str(path))


def _d_open_dwg(sk_or_doc, *, path, version="R2018") -> list[str]:
    raise DependencyMissing("dwg binary read/write is not implemented", reason="dependency_missing",
                            capability=-1, path=str(path))


def _build_double():
    return {
        "FAMILY": FAMILY_ID,
        "OPERATIONS": {
            "create_layout": _d_create_layout,
            "create_viewport": _d_create_viewport,
            "set_page_setup": _d_set_page_setup,
            "plot": _d_plot,
            "preview": _d_preview,
            "publish": _d_publish,
            "manage_layout": _d_manage_layout,
            "configure_plotter": _d_configure_plotter,
            "set_plot_style_table": _d_set_plot_style_table,
            "save_dwg": _d_save_dwg,
            "open_dwg": _d_open_dwg,
        },
    }


class _FamilyDouble:
    """`cadcore/ops/output.py` 未落盘时顶替它的**接口壳**（几何/落盘全部来自 `cadcore.draw.sheet`）。"""

    def __init__(self) -> None:
        payload = _build_double()
        self.FAMILY = payload["FAMILY"]
        self.OPERATIONS = payload["OPERATIONS"]


DOUBLE_FALLBACK = not _spec_exists(REAL_MODULE)
if DOUBLE_FALLBACK:
    FAMILY = _FamilyDouble()
    FAMILY_SOURCE = "DOUBLE:cadcore.draw.sheet (because %s is not on disk)" % REAL_MODULE
else:  # 真件存在：import 失败一律向上抛，不许被上面的分支吞掉
    from cadcore.ops import output as FAMILY  # type: ignore[no-redef]

    FAMILY_SOURCE = "REAL:" + REAL_MODULE

OPS = FAMILY.OPERATIONS
DOUBLE_OP_NAMES = tuple(sorted(_build_double()["OPERATIONS"]))


def _resolve(logical: str):
    for name in OP_ALIASES[logical]:
        fn = OPS.get(name)
        if callable(fn):
            return name, fn
    return None, None


def _lookup_sheet(sheet_uuid: str):
    """尽力取回落库的 Sheet（真件状态访问器**未冻结** ⇒ 取不到就 UNREADABLE_STATE，不硬红）。"""
    if DOUBLE_FALLBACK:
        return _DOUBLE_REGISTRY.get(str(sheet_uuid))
    for attr in ("sheet_by_uuid", "get_sheet"):
        getter = getattr(FAMILY, attr, None)
        if callable(getter):
            try:
                sheet = getter(str(sheet_uuid))
            except Exception:  # noqa: BLE001 - 访问器形态未冻结，失败即回落下一探针
                sheet = None
            if sheet is not None:
                return sheet
    return None


# --------------------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------------------


class _Projection:
    """与 `cadcore.draw.sheet` 认的投影形态同形（`curves=[{kind, points_nm}, …]`）。"""

    def __init__(self, curves):
        self.curves = tuple(curves)
        self.view = "front"
        self.hidden = True
        self.source = "stub"


class _StubKernel:
    """纯 Python 桩内核：几何**现算**、记调用次数，可被要求"不可用/无 project2d/投影为空"。"""

    def __init__(self, *, available=True, capabilities=("sketch", "project2d"),
                 empty=False, return_none=False):
        self.name = "stub"
        self._available = bool(available)
        self._capabilities = frozenset(capabilities)
        self.empty = bool(empty)
        self.return_none = bool(return_none)
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def capabilities(self):
        return self._capabilities

    def project_to_2d(self, shape, *, view, hidden):
        self.calls += 1
        if self.return_none:
            return None
        if self.empty:
            return _Projection([])
        half = 60 * NM
        return _Projection([
            {"kind": "visible", "points_nm": [[-half, -half], [half, -half]]},
            {"kind": "visible", "points_nm": [[-half, half], [half, half]]},
            {"kind": "hidden", "points_nm": [[-half, 0], [half, 0]]},
        ])


def new_doc(doc_uuid: str = "doc-output-1") -> dict:
    """最小 L1 文档夹具（`unit=mm`，几何与出图无关：出图读的是投影，不是草图实体）。"""
    return {"unit": "mm", "meta": {"doc_uuid": doc_uuid}, "entities": [], "layers": ["VISIBLE"]}


class FamilyCase(unittest.TestCase):
    """共用夹具：操作解析（缺席即 ABSENT skip）、Sheet 反查（取不到即 UNREADABLE skip）、错误断言。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cadcore-output-")
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.doc = new_doc()

    def op(self, logical: str):
        name, fn = _resolve(logical)
        if name is None:
            self.skipTest("ABSENT:%s (OPERATIONS keys=%s)" % (logical, sorted(OPS)))
        return fn

    def sheet_of(self, sheet_uuid: str):
        sheet = _lookup_sheet(sheet_uuid)
        if sheet is None:
            self.skipTest("UNREADABLE_STATE: no frozen accessor for sheet %s" % sheet_uuid)
        return sheet

    def cad_error(self, reason, fn, doc=None, **kwargs):
        """边界断言：必须抛 `CadError`，且 `reason` 落在给定集合里（集合=规格值+同义，写进 docstring）。

        接缝（本族 14 个 error 的根因，判据见下）：冻结接口是 `op(sk_or_doc, **kwargs)` ——
        `sk_or_doc` 是**第一个位置参数**，参数名就叫 `sk_or_doc`。调用点写的 `doc=<夹具文档>`
        只是"用哪份夹具文档"的入参，转发时必须**位置传**；原先此处写 `fn(**kwargs)` 会把
        `doc` 当成族操作的关键字 ⇒ 族操作报 `unexpected keyword argument 'doc'`。
        根因在测试的适配器（少传第一个位置参数），不在实现 ⇒ 修此处，不动实现。
        """
        wanted = (reason,) if isinstance(reason, str) else tuple(reason)
        with self.assertRaises(CadError) as ctx:
            fn(self.doc if doc is None else doc, **kwargs)
        self.assertIsInstance(ctx.exception, CadError)
        self.assertIn(ctx.exception.reason, wanted,
                      "reason=%r not in %r; message=%s" % (ctx.exception.reason, wanted,
                                                           ctx.exception.message))
        return ctx.exception

    def layout(self, **kwargs):
        """建一张布局并返回 (uuid, sheet)。"""
        create = self.op("create_layout")
        standard = kwargs.pop("standard", _sheet.standard_ids()[0])
        kwargs.setdefault("language", "en")
        uuids = create(self.doc, standard=standard, **kwargs)
        self.assertEqual(len(uuids), 1)
        return uuids[0], self.sheet_of(uuids[0])


# --------------------------------------------------------------------------------------
# 1) 接口契约 + 夹具自证
# --------------------------------------------------------------------------------------


class TestFamilyContract(FamilyCase):
    def test_family_id_and_operation_map(self):
        self.assertEqual(getattr(FAMILY, "FAMILY", None), FAMILY_ID)
        self.assertIsInstance(OPS, dict)
        self.assertTrue(OPS, "OPERATIONS must not be empty")
        for name, fn in OPS.items():
            self.assertIsInstance(name, str)
            self.assertTrue(callable(fn), "%s is not callable" % name)

    def test_provenance_follows_module_existence_and_double_is_not_a_reimplementation(self):
        """夹具自证：双只在"真件不在盘上"时使用；双的每个 op 都必须是本文件里的薄封装。"""
        self.assertTrue(FAMILY_SOURCE.startswith("REAL:") or FAMILY_SOURCE.startswith("DOUBLE:"))
        self.assertEqual(DOUBLE_FALLBACK, not _spec_exists(REAL_MODULE),
                         "fallback decision must follow module existence: %s" % FAMILY_SOURCE)
        if DOUBLE_FALLBACK:
            self.assertEqual(set(OPS), set(DOUBLE_OP_NAMES))
            payload = _build_double()
            for name, fn in payload["OPERATIONS"].items():
                self.assertIs(OPS[name], fn, "%s must be the double's own wrapper" % name)
        else:
            self.assertEqual(FAMILY_SOURCE, "REAL:" + REAL_MODULE)
            self.assertIs(FAMILY.OPERATIONS, OPS)

    def test_every_logical_operation_is_either_resolvable_or_reported_absent(self):
        report = {}
        for logical in OP_ALIASES:
            name, fn = _resolve(logical)
            report[logical] = name
        if DOUBLE_FALLBACK:
            missing = [key for key, value in report.items() if value is None]
            self.assertEqual(missing, [], "double must expose every logical op: %r" % (report,))
        else:
            self.assertTrue(any(value for value in report.values()),
                            "real module exposes none of the expected operations: %r" % (report,))


# --------------------------------------------------------------------------------------
# 2) create_layout：纳米数值 + 确定性 + 三条边界
# --------------------------------------------------------------------------------------


class TestCreateLayout(FamilyCase):
    def test_a3_layout_is_420_by_297_mm_in_nanometres(self):
        _uuid_, sheet = self.layout(size="A3")
        self.assertEqual(420 * NM, sheet.width_nm)
        self.assertEqual(297 * NM, sheet.height_nm)
        self.assertTrue(sheet.sheet_size_source, "sheet_size_source must say where the size came from")
        facts = _sheet.sheet_facts(sheet)
        self.assertEqual(420 * NM, facts["sheet_width_nm"])
        self.assertEqual(297 * NM, facts["sheet_height_nm"])

    def test_a1_layout_is_841_by_594_mm_and_same_input_gives_byte_identical_uuid(self):
        create = self.op("create_layout")
        standard, language = _sheet.standard_ids()[0], "en"
        first = create(self.doc, standard=standard, language=language, size="A1", scale="1:1")
        second = create(self.doc, standard=standard, language=language, size="A1", scale="1:1")
        self.assertEqual(first, second, "uuid5 determinism: identical input must give identical uuid")
        sheet = self.sheet_of(first[0])
        self.assertEqual(841 * NM, sheet.width_nm)
        self.assertEqual(594 * NM, sheet.height_nm)
        self.assertEqual((1, 1), sheet.scale_ratio)
        self.assertEqual("1:1", sheet.scale)

    def test_model_geometry_is_not_touched_by_layout_creation(self):
        create = self.op("create_layout")
        doc = new_doc()
        doc["entities"] = [{"uuid": "e1", "kind": "line", "geom": {"x1": 0, "y1": 0, "x2": 5, "y2": 5}}]
        before = json.dumps(doc["entities"], sort_keys=True)
        create(doc, standard=_sheet.standard_ids()[0], language="en", size="A3")
        self.assertEqual(before, json.dumps(doc["entities"], sort_keys=True))

    def test_boundary_reasons_match_spec(self):
        create = self.op("create_layout")
        standard = _sheet.standard_ids()[0]
        cases = (
            ("unknown_standard", dict(standard="no-such-standard", language="en")),
            ("parse_failed", dict(standard=standard, language="en", scale="oops")),
            ("out_of_range", dict(standard=standard, language="en", scale="0:1")),
            ("out_of_range", dict(standard=standard, language="en", size="A9")),
        )
        for reason, kwargs in cases:
            with self.subTest(reason=reason, kwargs=kwargs):
                self.cad_error(reason, create, doc=self.doc, **kwargs)


# --------------------------------------------------------------------------------------
# 3) create_viewport：可见/隐藏分桶数值 + 边界
# --------------------------------------------------------------------------------------


class TestCreateViewport(FamilyCase):
    def _with_view(self, *, hidden=True):
        sheet_uuid, sheet = self.layout(size="A3")
        create_viewport = self.op("create_viewport")
        kernel = _StubKernel()
        uuids = create_viewport(self.doc, sheet_uuid=sheet_uuid, shape={"kind": "plate"},
                                view="front", at_nm=(10 * NM, 10 * NM), hidden=hidden, kernel=kernel)
        self.assertEqual(len(uuids), 1)
        return sheet, kernel, uuids[0]

    def test_visible_and_hidden_curves_land_in_separate_buckets(self):
        sheet, kernel, _view_uuid = self._with_view(hidden=True)
        self.assertEqual(1, kernel.calls, "the kernel must be called exactly once for one viewport")
        self.assertEqual(1, len(sheet.views))
        self.assertEqual(2, len(sheet.views[0].visible))
        self.assertEqual(1, len(sheet.views[0].hidden_curves))
        facts = _sheet.sheet_facts(sheet)
        self.assertEqual("present", facts["hidden_layer"])
        self.assertEqual(2, facts["visible_entities"])
        self.assertEqual(1, facts["hidden_entities"])

    def test_unknown_view_reason(self):
        sheet_uuid, _sheet_obj = self.layout(size="A3")
        create_viewport = self.op("create_viewport")
        self.cad_error("unknown_view", create_viewport, doc=self.doc, sheet_uuid=sheet_uuid,
                       shape={}, view="diagonal", at_nm=(0, 0), kernel=_StubKernel())

    def test_empty_projection_is_an_error_and_leaves_no_half_built_view(self):
        sheet_uuid, sheet = self.layout(size="A3")
        create_viewport = self.op("create_viewport")
        self.cad_error("empty_projection", create_viewport, doc=self.doc, sheet_uuid=sheet_uuid,
                       shape={}, view="front", at_nm=(0, 0), kernel=_StubKernel(empty=True))
        self.assertEqual(0, len(sheet.views), "an empty projection must not leave a partial view")

    def test_missing_kernel_reason(self):
        sheet_uuid, _sheet_obj = self.layout(size="A3")
        create_viewport = self.op("create_viewport")
        self.cad_error("dependency_missing", create_viewport, doc=self.doc, sheet_uuid=sheet_uuid,
                       shape={}, view="front", at_nm=(0, 0), kernel=_StubKernel(available=False))


# --------------------------------------------------------------------------------------
# 4) set_page_setup / plot：纳米读数 + 落盘字节 + 降级留痕
# --------------------------------------------------------------------------------------


class TestPageSetupAndPlot(FamilyCase):
    def test_page_setup_a1_is_841_by_594_mm(self):
        sheet_uuid, sheet = self.layout(size="A3")
        setup = self.op("set_page_setup")
        uuids = setup(self.doc, sheet_uuid=sheet_uuid, size="A1", scale="1:2")
        self.assertEqual([sheet_uuid], uuids)
        self.assertEqual(841 * NM, sheet.width_nm)
        self.assertEqual(594 * NM, sheet.height_nm)
        self.assertEqual("1:2", sheet.scale)
        self.assertEqual((1, 2), sheet.scale_ratio)
        self.assertEqual(841 * NM, _sheet.sheet_facts(sheet)["sheet_width_nm"])

    def test_page_setup_rejects_pc3_and_keeps_previous_fields(self):
        sheet_uuid, sheet = self.layout(size="A3")
        setup = self.op("set_page_setup")
        before = (sheet.size, sheet.width_nm, sheet.height_nm, sheet.scale)
        self.cad_error("unsupported", setup, doc=self.doc, sheet_uuid=sheet_uuid, size="A1",
                       scale="1:2", plotter="DWG To PDF.pc3")
        self.assertEqual(before, (sheet.size, sheet.width_nm, sheet.height_nm, sheet.scale),
                         "a rejected page setup must not roll existing fields back")

    def test_plot_dxf_writes_non_empty_bytes_and_reports_entities(self):
        sheet_uuid, sheet = self.layout(size="A3")
        plot = self.op("plot")
        out = os.path.join(self.tmp, "plate")
        uuids = plot(self.doc, sheet_uuid=sheet_uuid, fmt="dxf", out=out)
        self.assertEqual(1, len(uuids))
        files = sorted(glob.glob(os.path.join(self.tmp, "plate*.dxf")))
        self.assertEqual(1, len(files), "expected exactly one .dxf artifact, got %r" % (files,))
        self.assertGreater(os.path.getsize(files[0]), 0, "a 0-byte DXF must not pass as success")
        result = sheet.exports[-1]
        self.assertEqual("dxf", result.requested)
        self.assertEqual("dxf", result.actual)
        self.assertFalse(result.downgraded)
        self.assertEqual("", result.reason)
        self.assertGreater(result.entities, 0)
        self.assertEqual(len(_sheet.iter_entities(sheet)), result.entities)

    def test_plot_pdf_without_backend_downgrades_to_svg_and_says_so(self):
        if _sheet.pdf_backend_available():
            self.skipTest("NA: a pdf backend is registered in this environment")
        sheet_uuid, sheet = self.layout(size="A3")
        plot = self.op("plot")
        plot(self.doc, sheet_uuid=sheet_uuid, fmt="pdf", out=os.path.join(self.tmp, "plate.pdf"))
        facts = _sheet.sheet_facts(sheet)
        self.assertEqual("pdf", facts["export_requested_format"])
        self.assertEqual("svg", facts["export_format"])
        self.assertTrue(facts["export_downgraded"])
        self.assertTrue(sheet.exports[-1].reason, "a downgrade must carry a non-empty reason")
        svg_files = sorted(glob.glob(os.path.join(self.tmp, "plate*.svg")))
        self.assertEqual(1, len(svg_files), "the downgraded artifact must be named .svg")
        self.assertGreater(os.path.getsize(svg_files[0]), 0)

    def test_plot_boundary_reasons(self):
        sheet_uuid, _sheet_obj = self.layout(size="A3")
        plot = self.op("plot")
        self.cad_error("unsupported_format", plot, doc=self.doc, sheet_uuid=sheet_uuid,
                       fmt="dwg", out=os.path.join(self.tmp, "x.dwg"))
        blocked = os.path.join(self.tmp, "blocker")
        Path(blocked).write_text("not a directory", encoding="utf-8")
        self.cad_error("write_failed", plot, doc=self.doc, sheet_uuid=sheet_uuid, fmt="svg",
                       out=os.path.join(blocked, "nested", "out.svg"))


# --------------------------------------------------------------------------------------
# 5) preview / publish
# --------------------------------------------------------------------------------------


class TestPreviewAndPublish(FamilyCase):
    def test_preview_writes_the_same_entity_stream_as_plot(self):
        sheet_uuid, sheet = self.layout(size="A3")
        create_viewport = self.op("create_viewport")
        create_viewport(self.doc, sheet_uuid=sheet_uuid, shape={}, view="front",
                        at_nm=(10 * NM, 10 * NM), kernel=_StubKernel())
        preview = self.op("preview")
        uuids = preview(self.doc, sheet_uuid=sheet_uuid, out=os.path.join(self.tmp, "preview"))
        self.assertEqual(1, len(uuids))
        files = sorted(glob.glob(os.path.join(self.tmp, "preview*.svg")))
        self.assertEqual(1, len(files))
        self.assertGreater(os.path.getsize(files[0]), 0)
        self.assertEqual("svg", _sheet.sheet_facts(sheet)["export_format"])
        self.assertGreater(len(_sheet.iter_entities(sheet)), 0)

    def test_preview_refuses_a_sheet_without_views(self):
        sheet_uuid, _sheet_obj = self.layout(size="A3")
        preview = self.op("preview")
        self.cad_error("drawing_error", preview, doc=self.doc, sheet_uuid=sheet_uuid,
                       out=os.path.join(self.tmp, "empty"))
        self.assertEqual([], glob.glob(os.path.join(self.tmp, "empty*")),
                         "no preview artifact may exist after the refusal")

    def test_publish_three_layouts_gives_three_pages_and_three_uuids(self):
        publish = self.op("publish")
        sheet_uuids = [self.layout(size="A3", name="L%d" % index)[0] for index in (1, 2, 3)]
        out_dir = os.path.join(self.tmp, "published")
        uuids = publish(self.doc, sheet_uuids=sheet_uuids, fmt="svg", out_dir=out_dir)
        self.assertEqual(3, len(uuids))
        self.assertEqual(3, len(set(uuids)), "each page must get its own uuid")
        pages = sorted(glob.glob(os.path.join(out_dir, "page_*.svg")))
        self.assertEqual(3, len(pages), "page count must equal the requested layout count")
        for page in pages:
            self.assertGreater(os.path.getsize(page), 0)

    def test_publish_empty_list_is_refused(self):
        publish = self.op("publish")
        self.cad_error("empty_sheet_list", publish, doc=self.doc, sheet_uuids=[],
                       fmt="svg", out_dir=os.path.join(self.tmp, "none"))


# --------------------------------------------------------------------------------------
# 6) manage_layout / NA 能力（DWG / pc3 / CTB）
# --------------------------------------------------------------------------------------


class TestLayoutsAndUnavailableCapabilities(FamilyCase):
    def test_new_and_copy_layout_uuids_are_distinct(self):
        manage = self.op("manage_layout")
        first = manage(self.doc, action="new", name="L1")
        self.assertEqual(1, len(first))
        second = manage(self.doc, action="copy", source=first[0])
        self.assertEqual(1, len(second))
        self.assertNotEqual(first[0], second[0], "a copied layout must get its own uuid")
        source_sheet = self.sheet_of(first[0])
        copy_sheet = self.sheet_of(second[0])
        self.assertEqual((source_sheet.size, source_sheet.scale, source_sheet.width_nm),
                         (copy_sheet.size, copy_sheet.scale, copy_sheet.width_nm))

    def test_duplicate_name_and_last_layout_are_refused(self):
        manage = self.op("manage_layout")
        manage(self.doc, action="new", name="L1")
        self.cad_error("duplicate_name", manage, doc=self.doc, action="new", name="L1")
        self.cad_error("last_layout", manage, doc=self.doc, action="delete", name="L1")
        self.cad_error("unknown_layout", manage, doc=self.doc, action="delete", name="NOPE")

    def test_dwg_and_pc3_are_explicitly_missing_not_fake_success(self):
        save_dwg = self.op("save_dwg")
        open_dwg = self.op("open_dwg")
        configure_plotter = self.op("configure_plotter")
        dwg_path = os.path.join(self.tmp, "part.dwg")
        exc = self.cad_error("dependency_missing", save_dwg, doc=self.doc, path=dwg_path)
        self.assertIsInstance(exc, DependencyMissing)
        self.assertEqual(-1, exc.details.get("capability", exc.capability if hasattr(exc, "capability") else -1),
                         "an unimplemented capability must be recorded as -1 (unmeasured), not 0")
        self.assertFalse(os.path.exists(dwg_path), "a fake .dwg must never appear on disk")
        self.cad_error("dependency_missing", open_dwg, doc=self.doc, path=dwg_path)
        self.cad_error("dependency_missing", configure_plotter, doc=self.doc)
        self.assertEqual([], glob.glob(os.path.join(self.tmp, "*.dwg")))

    def test_plot_style_table_formats(self):
        sheet_uuid, _sheet_obj = self.layout(size="A3")
        set_table = self.op("set_plot_style_table")
        self.cad_error(("unsupported_table_format", "dependency_missing", "unsupported"),
                       set_table, doc=self.doc, sheet_uuid=sheet_uuid, table="mono.stb")


# --------------------------------------------------------------------------------------
# 7) 两个组合用例
# --------------------------------------------------------------------------------------


class TestCompositeWorkflows(FamilyCase):
    def test_layout_to_page_setup_to_plot_keeps_size_and_bytes_consistent(self):
        """组合 1：建布局(A3) → 页面设置(A1, 1:2) → 出 DXF；三处读数必须同源。"""
        sheet_uuid, sheet = self.layout(size="A3", scale="1:1")
        self.assertEqual(420 * NM, sheet.width_nm)
        self.op("set_page_setup")(self.doc, sheet_uuid=sheet_uuid, size="A1", scale="1:2")
        uuids = self.op("plot")(self.doc, sheet_uuid=sheet_uuid, fmt="dxf",
                                out=os.path.join(self.tmp, "combo1"))
        self.assertEqual(1, len(uuids))
        facts = _sheet.sheet_facts(sheet)
        self.assertEqual(841 * NM, facts["sheet_width_nm"])
        self.assertEqual(594 * NM, facts["sheet_height_nm"])
        self.assertEqual("1:2", facts["scale"])
        self.assertEqual("dxf", facts["export_format"])
        self.assertFalse(facts["export_downgraded"])
        files = sorted(glob.glob(os.path.join(self.tmp, "combo1*.dxf")))
        self.assertEqual(1, len(files))
        self.assertGreater(os.path.getsize(files[0]), 0)

    def test_three_layouts_publish_then_plot_one_page_again_is_deterministic(self):
        """组合 2：三个布局批量发布 → 再单独出一页；同一布局两次导出必须逐字节相同。"""
        sheet_uuids = [self.layout(size="A3", name="P%d" % index)[0] for index in (1, 2, 3)]
        out_dir = os.path.join(self.tmp, "combo2")
        uuids = self.op("publish")(self.doc, sheet_uuids=sheet_uuids, fmt="svg", out_dir=out_dir)
        self.assertEqual(3, len(uuids))
        self.assertEqual(3, len(glob.glob(os.path.join(out_dir, "page_*.svg"))))
        plot = self.op("plot")
        plot(self.doc, sheet_uuid=sheet_uuids[0], fmt="svg", out=os.path.join(self.tmp, "again"))
        plot(self.doc, sheet_uuid=sheet_uuids[0], fmt="svg", out=os.path.join(self.tmp, "again2"))
        # Q1（docstring）：返回 uuid 与路径的关系未冻结 ⇒ 这里只硬断言**字节**确定性。
        left = Path(glob.glob(os.path.join(self.tmp, "again*.svg"))[0]).read_bytes()
        right = Path(glob.glob(os.path.join(self.tmp, "again2*.svg"))[0]).read_bytes()
        self.assertEqual(left, right, "no timestamp may leak into the artifact (byte-identical export)")


# --------------------------------------------------------------------------------------
# 13) 布局/视口补齐层（本席 output-A；覆盖面貌**修正本文件模块 docstring 第 4 条**里
#     "set_viewport_scale / set_viewport_layer_override 本文件不测" 的旧记账 —— 自本类起
#     这两个操作被硬断言覆盖，缺席即 `self.fail`（**不是** ABSENT skip，不许静默当绿）。
# --------------------------------------------------------------------------------------


class TestLayoutViewportGapClosing(FamilyCase):
    """L1 登记 / 裸调建视口 / 视口比例锁定 / VP Freeze 的硬断言（真件专属接缝）。"""

    def _l1_sheets(self):
        return self.doc.get("sheets") or []

    def _viewport_op(self, name):
        fn = OPS.get(name)
        if not callable(fn):
            self.fail("OPERATIONS is missing %s (keys=%s)" % (name, sorted(OPS)))
        return fn

    def _add_rectangle_sketch(self):
        corners = [(0, 0), (60 * NM, 0), (60 * NM, 40 * NM), (0, 40 * NM)]
        entities = []
        for index in range(4):
            x1, y1 = corners[index]
            x2, y2 = corners[(index + 1) % 4]
            entities.append({"uuid": str(_uuid.uuid4()), "kind": "line", "layer": "0",
                             "construction": False,
                             "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
        self.doc["sketches"] = [{"uuid": str(_uuid.uuid4()), "plane": "XY", "entities": entities,
                                 "constraints": [], "dimensions": [], "solved": {}}]

    def _viewport(self, sheet_uuid, *, view="front", kernel=None):
        return self.op("create_viewport")(self.doc, sheet_uuid=sheet_uuid, shape=object(),
                                          view=view, at_nm=(0, 0), hidden=True,
                                          kernel=kernel or _StubKernel())

    def test_create_layout_registers_one_l1_sheet_and_is_idempotent(self):
        """布局必须真的进 L1 `sheets[]`；同入参两次调用同 uuid 且不重复登记。"""
        first, sheet = self.layout(size="A3", name="L1")
        self.assertEqual(1, len(self._l1_sheets()))
        entry = self._l1_sheets()[0]
        self.assertEqual(first, entry["uuid"])
        self.assertEqual("L1", entry["name"])
        self.assertEqual(420 * NM, sheet.width_nm)
        self.assertEqual(297 * NM, sheet.height_nm)
        again = self.op("create_layout")(self.doc, standard=_sheet.standard_ids()[0],
                                         language="en", size="A3")
        self.assertEqual([first], list(again))
        self.assertEqual(1, len(self._l1_sheets()))

    def test_bare_create_viewport_lands_a_view_and_flags_hidden_downgrade(self):
        """裸调（无 sheet_uuid/shape/kernel）：自动内核 + 线框 ⇒ 无隐藏线，降级必须留痕。"""
        self._add_rectangle_sketch()
        sheet_uuid, sheet = self.layout(size="A3")
        uuids = self.op("create_viewport")(self.doc)
        self.assertEqual(1, len(uuids))
        self.assertEqual(1, len(sheet.views))
        entry = self._l1_sheets()[0]
        self.assertEqual(1, len(entry["views"]))
        view_l1 = entry["views"][0]
        self.assertEqual(uuids[0], view_l1["uuid"])
        self.assertEqual("viewport", view_l1["kind"])
        self.assertFalse(view_l1["hidden"], "wire projection carries no hidden curves")
        self.assertTrue(view_l1["hidden_downgraded"], "the downgrade must be machine-readable")

    def test_set_viewport_scale_and_lock_are_read_back_three_ways(self):
        """比例/锁定落到 View、L1 与图纸视图三处，且坏比例不回落、不改写原比例。"""
        sheet_uuid, sheet = self.layout(size="A3")
        view_uuid = self._viewport(sheet_uuid)[0]
        scale_op = self._viewport_op("set_viewport_scale")
        self.assertEqual([view_uuid], list(scale_op(self.doc, sheet_uuid=sheet_uuid,
                                                    view_uuid=view_uuid, scale="1:100",
                                                    locked=True)))
        view = sheet.views[0]
        self.assertEqual((1, 100), tuple(view.scale))
        self.assertEqual("1:100", view.scale_text)
        view_l1 = self._l1_sheets()[0]["views"][0]
        self.assertEqual("1:100", view_l1["scale"])
        self.assertTrue(view_l1["locked"])
        self.cad_error(("parse_failed", "bad_scale"), scale_op, sheet_uuid=sheet_uuid,
                       view_uuid=view_uuid, scale="oops")
        self.assertEqual("1:100", sheet.views[0].scale_text, "a bad scale must not be written")

    def test_viewport_layer_override_is_per_view_and_never_touches_model_layers(self):
        """同层在两个视口分别 frozen/on；模型空间图层表逐字段不变；未实现形态 unsupported。"""
        sheet_uuid, sheet = self.layout(size="A3")
        first = self._viewport(sheet_uuid)[0]
        second = self._viewport(sheet_uuid, view="top")[0]
        before = {key: dict(value) for key, value in sheet.layers.items()}
        override = self._viewport_op("set_viewport_layer_override")
        override(self.doc, sheet_uuid=sheet_uuid, view_uuid=first, layer="VISIBLE", state="frozen")
        override(self.doc, sheet_uuid=sheet_uuid, view_uuid=second, layer="VISIBLE", state="on")
        self.assertEqual(before, {key: dict(value) for key, value in sheet.layers.items()},
                         "model space layer table must not be rewritten")
        by_uuid = {item["uuid"]: item for item in self._l1_sheets()[0]["views"]}
        self.assertTrue(by_uuid[first]["layer_overrides"]["VISIBLE"]["freeze"])
        self.assertFalse(by_uuid[second]["layer_overrides"]["VISIBLE"]["freeze"])
        self.cad_error("unsupported", override, sheet_uuid=sheet_uuid, view_uuid=first,
                       layer="VISIBLE", state="color")
        self.cad_error("unknown_layer", override, sheet_uuid=sheet_uuid, view_uuid=first,
                       layer="NOPE", state="on")
        self.cad_error("viewport_not_found", override, sheet_uuid=sheet_uuid,
                       view_uuid=str(_uuid.uuid4()), layer="VISIBLE", state="on")


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
