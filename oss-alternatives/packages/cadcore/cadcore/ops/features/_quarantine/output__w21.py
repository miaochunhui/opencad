#!/usr/bin/env python3
"""output 族实现片 w21 —— 第 12 号条目：批量发布多布局出图（PUBLISH）。

编号口径（本族仍 missing/partial 的 must_have，按 features/autocad-output.json 清单顺序 0..14）：
  0 页面设置 / 1 PLOT / 2 打印到 PDF / 3 绘图仪配置 .pc3 / 4 打印样式表 CTB-STB /
  5 LWEIGHT / 6 打印区域选择 / 7 打印比例与布满图纸 / 8 打印预览 / 9 Plot-No Plot /
  10 模型空间直接出图 / 11 布局管理 / 12 **批量发布多布局出图（PUBLISH）** ← 本片 /
  13 DWG 读写 / 14 DXF 导入导出与精度。

已实现（真几何 / 真数据后果，全部落在 L1 文档模型上）
- 按 L1 `document.sheets[]` **原有顺序**逐张出图，每页落一个真文件 `page_NNN.<fmt>`；
  返回**每页一个 uuid**（顺序 == 页序 == 图纸序）。
- 页面几何复用唯一真源 `cadcore.draw.sheet`：优先取本族 `create_layout` 登记过的**真 Sheet 对象**
  （含视图/图层/图框曲线），L1 里未登记的条目按 `size / scale / standard_profile / language`
  现场重建真 Sheet（`make_sheet`），**不是桩**。
- 每页 uuid 写回该图纸 L1 条目的 `exports[]` 并同步记进文档级 `publish_exports` 桶
  ⇒ 返回的 uuid 能在载荷里解引用（对得上页序与所属图纸）。

未实现（显式 `unsupported`，绝不静默降级）
- `single_pdf=True` 的多页合并单文件 PDF；图纸集（Sheet Set）；发布对话框 / UI / HTTP 入口；
  后台发布队列与打印任务状态机。

错误（`cadcore.errors.CadError`，带机器可读 `reason`）
- 空清单 `empty_sheet_list`；未知图纸 `unknown_layout`；缺 `out_dir` / 坏文档 `bad_reference`；
  格式不支持 `unsupported`；写盘失败 `write_failed`（原异常进 details）；图纸模块缺席 `dependency_missing`。
"""

from __future__ import annotations

import os as _os
import sys as _sys
import uuid as _uuid
from typing import Any, Callable, Mapping

# 直接按 `python3 cadcore/ops/features/output__w21.py` 执行时，sys.path[0] 是本文件目录，
# 包根（packages/cadcore）不在路径上；显式补进去，使包内/脚本两种跑法结果一致。
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__)))))
if _PKG_ROOT not in _sys.path:
    _sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError  # noqa: E402

try:  # 唯一真源：图纸对象与导出写出器都在 draw.sheet
    from cadcore.draw import sheet as _sheet

    _SHEET_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - 只在包不完整时走到
    _sheet = None
    _SHEET_IMPORT_ERROR = "%s: %s" % (type(_exc).__name__, _exc)

FAMILY = "output"
_FORMATS = ("dxf", "svg", "pdf")
_ENTITY_MARKERS = (b"LINE", b"LWPOLYLINE", b"POLYLINE", b"ARC", b"CIRCLE", b"TEXT", b"MTEXT")

__all__ = ["FAMILY", "OPERATIONS", "publish"]


def _bad(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _entry_field(entry: Any, name: str, default: Any = None) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _sheets_of(doc: Any) -> list[Any]:
    """L1 布局表：`document.sheets[]`（dict 载荷或 Document 对象都认）。"""
    sheets = doc.get("sheets") if isinstance(doc, Mapping) else getattr(doc, "sheets", None)
    return list(sheets) if isinstance(sheets, (list, tuple)) else []


def _uuid_of(entry: Any) -> str:
    raw = _entry_field(entry, "uuid", None)
    return str(raw) if raw else ""


def _size_token(size: Any) -> str:
    """L1 `sheet.size` 可能是 token 或幅面字典；字典取名字，取不到退回公称 A3。"""
    if isinstance(size, Mapping):
        for key in ("name", "token", "size"):
            if size.get(key):
                return str(size[key])
        return "A3"
    text = str(size or "").strip()
    return text or "A3"


def _real_sheet(doc: Any, entry: Any, sheet_uuid: str) -> Any:
    """拿到该 L1 条目的真 Sheet：族注册表优先，否则按 L1 元数据现场重建。"""
    if _sheet is None:
        raise _bad("dependency_missing", "cadcore.draw.sheet is unavailable",
                   detail=_SHEET_IMPORT_ERROR)
    try:
        from cadcore.ops import output as _family
    except Exception:  # 族模块缺席不阻断出图，走重建路径
        _family = None
    if _family is not None:
        for name in ("sheet_by_uuid", "_sheet_by_uuid"):
            finder = getattr(_family, name, None)
            if callable(finder):
                try:
                    found = finder(sheet_uuid)
                except Exception:
                    found = None
                if found is not None:
                    return found
    size = _size_token(_entry_field(entry, "size", None))
    scale = str(_entry_field(entry, "scale", None) or "1:100")
    standard = str(_entry_field(entry, "standard_profile", None) or "gbt")
    language = str(_entry_field(entry, "language", None) or "zh")
    try:
        sheet = _sheet.make_sheet(doc, standard=standard, language=language, size=size, scale=scale)
    except CadError:
        raise
    except Exception as exc:
        raise _bad("bad_reference", "cannot rebuild a sheet from the L1 entry",
                   sheet_uuid=sheet_uuid, detail="%s: %s" % (type(exc).__name__, exc)) from exc
    try:
        sheet.uuid = sheet_uuid          # L1 uuid 是权威，重建对象不能另起一个身份
    except Exception:
        pass
    return sheet


def _register_page(doc: Any, entry: Any, record: dict[str, Any]) -> None:
    """页 uuid 真进 L1：写进该图纸条目的 `exports[]`，并同步文档级记账桶。"""
    if isinstance(entry, dict):
        bucket = entry.setdefault("exports", [])
        if isinstance(bucket, list):
            bucket.append(dict(record))
    if isinstance(doc, dict):
        bucket = doc.setdefault("publish_exports", [])
        if isinstance(bucket, list):
            bucket.append(dict(record))


def _records_in(doc: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for entry in _sheets_of(doc):
        exports = _entry_field(entry, "exports", None)
        if isinstance(exports, list):
            found.extend([r for r in exports if isinstance(r, Mapping)])
    if isinstance(doc, Mapping):
        bucket = doc.get("publish_exports")
        if isinstance(bucket, list):
            found.extend([r for r in bucket if isinstance(r, Mapping)])
    return found


def publish(sk_or_doc: Any, *, sheet_uuids: Any = None, out_dir: str = "", fmt: str = "pdf",
            single_pdf: bool = False, dxf_version: str = "R2018", dry_run: bool = False,
            page_prefix: str = "page") -> list[str]:
    """PUBLISH：按布局顺序批量出图，每页一个文件、返回每页一个 uuid。

    - `sheet_uuids=None` ⇒ 取 `document.sheets[]` 全部、按原顺序；显式给列表 ⇒ 按给定顺序发布
      （重复 uuid 会被逐页重复发布，顺序即页序）。空清单**显式拒绝**，不把"没页"当成功。
    - `fmt` ∈ dxf|svg|pdf（pdf 后端缺席时 `draw.sheet.export_sheet` 自己降级并在返回值里写明实际格式）。
    - `dry_run=True` ⇒ 只算页路径与 uuid 并记账（`planned=True`），**不落盘**（口径写进 L1 记录）。
    - 返回：`list[str]`，长度 == 页数，逐页 uuid（可在 L1 `exports[]` 里解引用）。
    """
    doc = sk_or_doc
    entries = _sheets_of(doc)
    by_uuid: dict[str, Any] = {}
    for entry in entries:
        key = _uuid_of(entry)
        if key and key not in by_uuid:
            by_uuid[key] = entry

    if sheet_uuids is None:
        order = [_uuid_of(e) for e in entries]
        order = [key for key in order if key]
    elif isinstance(sheet_uuids, str):
        order = [sheet_uuids.strip()] if sheet_uuids.strip() else []
    else:
        try:
            order = [str(key).strip() for key in sheet_uuids if str(key or "").strip()]
        except TypeError as exc:
            raise _bad("bad_reference", "sheet_uuids must be a sequence of layout uuids",
                       detail="%s: %s" % (type(exc).__name__, exc)) from exc

    if not order:
        raise _bad("empty_sheet_list", "publish needs at least one layout to plot",
                   sheets=len(entries))
    if single_pdf:
        raise _bad("unsupported", "single multi-page PDF publish is not implemented",
                   what="single_pdf", requested=True)
    target_fmt = str(fmt or "").strip().lower()
    if target_fmt not in _FORMATS:
        raise _bad("unsupported", "publish format must be dxf|svg|pdf", fmt=str(fmt),
                   implemented=list(_FORMATS))
    if not str(out_dir or "").strip():
        raise _bad("bad_reference", "publish needs out_dir", out_dir=str(out_dir))

    missing = [key for key in order if key not in by_uuid]
    if missing:
        raise _bad("unknown_layout", "layout uuid is not in document.sheets[]",
                   missing=missing, known=sorted(by_uuid))

    if not dry_run:
        try:
            _os.makedirs(str(out_dir), exist_ok=True)
        except OSError as exc:
            raise _bad("write_failed", "publish could not create out_dir",
                       path=str(out_dir), detail="%s: %s" % (type(exc).__name__, exc)) from exc

    prefix = str(page_prefix or "page")
    pages: list[str] = []
    for index, sheet_uuid in enumerate(order):
        entry = by_uuid[sheet_uuid]
        sheet = _real_sheet(doc, entry, sheet_uuid)
        path = _os.path.join(str(out_dir), "%s_%03d.%s" % (prefix, index, target_fmt))
        if not dry_run:
            try:
                path = _sheet.export_sheet(sheet, fmt=target_fmt, out=str(path),
                                           dxf_version=str(dxf_version))
            except CadError:
                raise
            except Exception as exc:
                raise _bad("write_failed", "publish could not write the page",
                           path=str(path), sheet_uuid=sheet_uuid,
                           detail="%s: %s" % (type(exc).__name__, exc)) from exc
        page_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                                    "cadcore|output|publish|%s|%s|%s" % (sheet_uuid, path,
                                                                         target_fmt)))
        _register_page(doc, entry, {"uuid": page_uuid, "export_uuid": page_uuid,
                                    "kind": "output page", "op": "publish", "page": index,
                                    "sheet_uuid": sheet_uuid, "fmt": target_fmt,
                                    "path": str(path), "planned": bool(dry_run)})
        pages.append(page_uuid)
    return pages


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "publish": publish,
}


def _selftest() -> int:
    import tempfile
    from cadcore.ops import output as family

    doc: dict[str, Any] = {}
    created: list[str] = []
    how = "family.create_layout"
    for size, scale in (("A3", "1:100"), ("A4", "1:50")):
        try:
            created.append(str(family.OPERATIONS["create_layout"](doc, size=size, scale=scale)[0]))
        except Exception:
            created = []
            break
    if not created:                      # 族入口不可用时走本片自己的真 Sheet 重建路径
        how = "own-make_sheet"
        for size, scale in (("A3", "1:100"), ("A4", "1:50")):
            built = _sheet.make_sheet(doc, standard="gbt", language="zh", size=size, scale=scale)
            created.append(str(built.uuid))
            doc.setdefault("sheets", []).append({"uuid": str(built.uuid), "size": size,
                                                 "scale": scale, "standard_profile": "gbt",
                                                 "views": []})
    fallback_uuid = "sheet-fallback-l1"  # L1 有、族注册表没有 ⇒ 验证重建路径
    doc.setdefault("sheets", []).append({"uuid": fallback_uuid, "size": "A4", "scale": "1:200",
                                         "standard_profile": "gbt"})
    order = [_uuid_of(e) for e in _sheets_of(doc)]

    # H1：页数 == 布局数、页序 == sheets[] 序、每页真文件且带 ENTITIES 段
    assert len(order) == 3 and order[-1] == fallback_uuid, order
    blobs: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        pages = OPERATIONS["publish"](doc, out_dir=tmp, fmt="dxf")
        assert isinstance(pages, list) and len(pages) == 3 and len(set(pages)) == 3, pages
        for index, _page in enumerate(pages):
            name = "page_%03d.dxf" % index
            path = _os.path.join(tmp, name)
            assert _os.path.isfile(path), path
            with open(path, "rb") as handle:
                blob = handle.read()
            assert len(blob) > 200 and b"ENTITIES" in blob, (name, len(blob))
            blobs.append(blob)

        # H2：真几何（不是空壳文件）—— 至少一页带实体记录
        assert any(any(mark in blob for mark in _ENTITY_MARKERS) for blob in blobs), \
            "published pages carry no entity records"

        # H3：每页 uuid 在 L1 载荷里可解引用，且归属图纸与页序一一对应
        records = _records_in(doc)
        for index, page_uuid in enumerate(pages):
            hit = [r for r in records if str(r.get("uuid")) == page_uuid]
            assert len(hit) == 1, (page_uuid, len(hit))
            assert str(hit[0].get("sheet_uuid")) == order[index], (hit[0], order[index])

        # H4 负例：空清单必须抛 CadError(empty_sheet_list)，不许静默出 0 页
        try:
            OPERATIONS["publish"]({"sheets": []}, out_dir=tmp, fmt="dxf")
        except CadError as exc:
            assert exc.reason == "empty_sheet_list", exc.as_dict()
        else:
            raise AssertionError("empty sheet list did not raise")

        # H5 负例：多页合并单 PDF 显式 unsupported（不是悄悄只出第一页）
        try:
            OPERATIONS["publish"](doc, out_dir=tmp, fmt="pdf", single_pdf=True)
        except CadError as exc:
            assert exc.reason == "unsupported", exc.as_dict()
        else:
            raise AssertionError("single_pdf did not raise")

    print("FEATURE_OK family=output op=publish verdict=PASS layouts=%d pages=%d "
          "geom_kept=True l1_resolvable=%d via=%s" % (len(order), len(pages), len(records), how))
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
