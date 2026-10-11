#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cadcore.sketch.rebuild —— 草图重建与**传播链**：重解约束 → 几何诊断 → 依赖特征重建。

一句话用途
----------
"改完草图以后，谁需要跟着重建、重建结果对不对"这件事的唯一入口：`rebuild()` 对一张草图
（或整份文档）做三件事，并把每一步的结论做成**机读报告**（不是只回一个 True/False）：

1. **重解**：有约束/尺寸就走 `constraints.solve()`，把 `dof / converged / over_constrained` 带出来；
2. **几何体检**：空草图 / 非整数纳米 / 未知实体类型 / 引用不存在的约束目标 / 轮廓不闭合 —— 逐条给码；
3. **传播**：列出**依赖这张草图**的特征（`features[].inputs` 含该 sketch uuid），
   有内核就把它们真的重建（走 `docmodel.recompute`），没内核就记 `pending_kernel`
   （**未采**，绝不把"没重建"写成"重建成功"）。

三态口径
--------
- `converged`：`True/False` 真解过；没有方程（空草图/无约束）⇒ `None`（不适用）；
- `rebuilt_features` / `pending_features`：真的重建过 / 因为没内核而**未采**，两者不许同形；
- 诊断一律带 `code`（`err.*` / `warn.*`），`severity=error` 的条目就是"这次重建不算通过"。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/sketch/rebuild.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

if __package__ in (None, ""):  # 直跑引导
    import os as _os

    _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    __package__ = "cadcore.sketch"

from ..errors import CadError  # noqa: E402
from . import constraints as _constraints  # noqa: E402

__all__ = ["rebuild", "rebuild_sketch", "dependents_of", "selftest"]

#: 不适用哨兵。
NA = "NA"

#: 已知实体类型（几何体检里"未知类型"判据的唯一真源）。
KNOWN_KINDS: tuple[str, ...] = ("line", "circle", "arc", "polyline", "point", "spline")

#: 几何里必须是**整数纳米**的长度字段。
_LENGTH_FIELDS: tuple[str, ...] = ("x1", "y1", "x2", "y2", "cx", "cy", "r")


def _diag(severity: str, code: str, **details: object) -> dict[str, object]:
    out: dict[str, object] = {"severity": severity, "code": code}
    out.update(details)
    return out


def _is_doc(target: object) -> bool:
    if isinstance(target, Mapping):
        return "sketches" in target
    return hasattr(target, "sketches")


def _sketches_of(doc: object) -> list[object]:
    value = doc.get("sketches") if isinstance(doc, Mapping) else getattr(doc, "sketches", None)
    if isinstance(value, list):
        return value
    raise CadError("document has no sketches list", reason="bad_document")


def _features_of(doc: object) -> list[object]:
    value = doc.get("features") if isinstance(doc, Mapping) else getattr(doc, "features", None)
    return list(value) if isinstance(value, list) else []


def _uuid_of(node: object) -> str:
    if isinstance(node, Mapping):
        return str(node.get("uuid") or "")
    return str(getattr(node, "uuid", "") or "")


def dependents_of(doc: object, sketch_uuid: str) -> list[str]:
    """依赖这张草图的**未抑制特征** uuid（按文档顺序）—— 传播链的唯一定义。"""
    wanted = str(sketch_uuid)
    out: list[str] = []
    for feature in _features_of(doc):
        if isinstance(feature, Mapping) and bool(feature.get("suppressed")):
            continue
        inputs = feature.get("inputs") if isinstance(feature, Mapping) else getattr(feature, "inputs", None)
        refs = [str(item) for item in (inputs or []) if isinstance(item, str)]
        if wanted in refs:
            out.append(_uuid_of(feature))
    return out


def _sketch_diagnostics(sketch: Mapping[str, object], *, tol_nm: float) -> list[dict[str, object]]:
    """单张草图的几何体检（每条都带 code；不自证、不猜测）。"""
    problems: list[dict[str, object]] = []
    entities = [item for item in (sketch.get("entities") or []) if isinstance(item, Mapping)]
    if not entities:
        problems.append(_diag("error", "err.sketch.empty", detail="sketch has no entities"))
        return problems
    for entity in entities:
        kind = str(entity.get("kind") or "").strip().lower()
        if kind not in KNOWN_KINDS:
            problems.append(_diag("error", "err.sketch.geometry_error", entity=_uuid_of(entity),
                                  detail="unknown entity kind", kind=kind))
            continue
        geom = entity.get("geom")
        if not isinstance(geom, Mapping):
            problems.append(_diag("error", "err.sketch.geometry_error", entity=_uuid_of(entity),
                                  detail="entity has no geom mapping"))
            continue
        for field in _LENGTH_FIELDS:
            value = geom.get(field)
            if isinstance(value, bool) or (value is not None and not isinstance(value, int)):
                problems.append(_diag("error", "err.sketch.geometry_error", entity=_uuid_of(entity),
                                      field=field, detail="length must be integer nanometres",
                                      value_type=type(value).__name__))
    known = {_uuid_of(item) for item in entities}
    for constraint in (sketch.get("constraints") or []):
        if not isinstance(constraint, Mapping):
            continue
        for ref in (constraint.get("refs") or []):
            if isinstance(ref, Sequence) and not isinstance(ref, (str, bytes)) and ref:
                target = str(ref[0])
                if target and target not in known:
                    problems.append(_diag("error", "err.sketch.unknown_reference",
                                          constraint=_uuid_of(constraint), ref=target,
                                          detail="constraint points at a missing entity"))
    _polygon_kinds = {"line", "polyline"}
    if entities and all(str(item.get("kind") or "") in _polygon_kinds for item in entities):
        loops = _constraints.closed_profiles(sketch, tol_nm=tol_nm)
        if not loops:
            problems.append(_diag("error", "err.sketch.no_closed_contour",
                                  detail="line/polyline sketch closes no loop"))
        else:
            problems.append(_diag("info", "info.sketch.closed_contours", loops=len(loops)))
    return problems


def rebuild_sketch(sketch: object, *, tol_nm: float = 0.0) -> dict[str, object]:
    """单张草图重建：重解 + 几何体检（返回报告的一部分，不含传播）。"""
    if not isinstance(sketch, Mapping):
        raise CadError("sketch must be a mapping", reason="invalid_argument")
    report: dict[str, object] = {"sketch": _uuid_of(sketch)}
    diagnostics = _sketch_diagnostics(sketch, tol_nm=tol_nm)
    constraints = [item for item in (sketch.get("constraints") or []) if isinstance(item, Mapping)]
    dimensions = [item for item in (sketch.get("dimensions") or []) if isinstance(item, Mapping)]
    if constraints or dimensions:
        result = _constraints.solve(sketch)
        report.update({"converged": bool(result.converged), "dof": int(result.dof),
                       "variables": int(result.variables), "equations": int(result.equations),
                       "redundant": int(getattr(result, "redundant", 0) or 0)})
        if bool(getattr(result, "over_constrained", False)):
            diagnostics.append(_diag("error", "err.sketch.over_defined",
                                     redundant=int(getattr(result, "redundant", 0) or 0)))
        conflicting = list(getattr(result, "conflicting", []) or [])
        if conflicting:
            diagnostics.append(_diag("error", "err.sketch.conflicting", worst=str(conflicting[0])))
        if not result.converged:
            diagnostics.append(_diag("error", "err.sketch.not_converged",
                                     verdict=str(result.verdict_line())))
    else:
        report.update({"converged": None, "dof": None, "variables": None, "equations": None,
                       "redundant": 0})
    report["diagnostics"] = diagnostics
    report["errors"] = [item["code"] for item in diagnostics if item["severity"] == "error"]
    report["state"] = "ok" if not report["errors"] else "error"
    return report


def rebuild(target: object, *, kernel: object = None, tol_nm: float = 0.0) -> dict[str, object]:
    """**重建入口**：草图（映射）⇒ 只做单张重建；文档 ⇒ 逐张重建 + **依赖特征传播**。

    文档形态的传播：`dependents` 给出每张草图的依赖特征；有 `kernel` 就把重建交给
    `docmodel.recompute()`（它才是"文档级重建"的唯一真源），把它的 diagnostic code 原样带回来；
    没给内核 ⇒ 依赖特征进 `pending_features` 且 `state="partial"`（**未采**，不冒充重建成功）。
    """
    if not _is_doc(target):
        report = rebuild_sketch(target, tol_nm=tol_nm)
        report.update({"kind": "rebuild", "scope": "sketch", "dependents": {},
                       "rebuilt_features": [], "pending_features": [], "kernel": NA})
        return report
    sketches = _sketches_of(target)
    per_sketch = [rebuild_sketch(item, tol_nm=tol_nm) for item in sketches]
    dependents: dict[str, list[str]] = {}
    for item in sketches:
        uuid_ = _uuid_of(item)
        dependents[uuid_] = dependents_of(target, uuid_)
    all_dependents = sorted({fid for ids in dependents.values() for fid in ids})
    diagnostics: list[dict[str, object]] = []
    for item in per_sketch:
        for entry in item["diagnostics"]:
            diagnostics.append(dict(entry, sketch=item["sketch"]))
    report: dict[str, object] = {"kind": "rebuild", "scope": "document",
                                 "sketches": [item["sketch"] for item in per_sketch],
                                 "per_sketch": per_sketch, "dependents": dependents,
                                 "kernel": NA, "rebuilt_features": [], "pending_features": [],
                                 "diagnostics": diagnostics}
    if not sketches:
        report["state"] = "error"
        report["errors"] = ["err.document.no_sketch"]
        report["diagnostics"] = diagnostics + [_diag("error", "err.document.no_sketch")]
        return report
    if kernel is None:
        report["pending_features"] = all_dependents
        report["state"] = "partial" if all_dependents else "ok"
        report["errors"] = sorted({str(item["code"]) for item in diagnostics
                                   if item["severity"] == "error"})
        return report
    try:
        from .. import docmodel  # 局部导入：避免 cadcore 包初始化期的环
    except Exception as exc:  # noqa: BLE001 - 拿不到文档层就如实记未采
        report["kernel"] = str(getattr(kernel, "name", NA))
        report["pending_features"] = all_dependents
        report["state"] = "partial"
        report["errors"] = ["err.rebuild.docmodel_unavailable"]
        report["diagnostics"] = diagnostics + [_diag("error", "err.rebuild.docmodel_unavailable",
                                                     detail="%s: %s" % (type(exc).__name__, exc))]
        return report
    result = docmodel.recompute(target, kernel)
    rebuilt = [str(item.get("feature_uuid") or "") for item in result.shape_refs]
    for entry in result.diagnostics:
        diagnostics.append(dict(entry, severity=str(entry.get("severity") or "info"),
                                code=str(entry.get("code") or "err.rebuild.unknown")))
    report.update({"kernel": str(getattr(kernel, "name", NA)), "ok": bool(result.ok),
                   "rebuilt_features": rebuilt,
                   "pending_features": [fid for fid in all_dependents if fid not in rebuilt],
                   "diagnostics": diagnostics,
                   "errors": sorted({str(item["code"]) for item in diagnostics
                                     if item["severity"] == "error"})})
    report["state"] = "ok" if (result.ok and not report["errors"]) else "error"
    return report


def selftest() -> int:
    """自检：健康草图重建绿 / 破坏几何被抓 / 未闭合被抓 / 传播链点名依赖特征 / 无内核=未采。"""
    from . import commands as _commands
    from . import primitives as _primitives

    def new_sketch() -> dict[str, object]:
        return {"uuid": "11111111-1111-4111-8111-111111111111", "plane": "XY", "entities": [],
                "constraints": [], "dimensions": []}

    good = new_sketch()
    entities = _primitives.add_rectangle(good, p1_nm=(0, 0), p2_nm=(10_000_000, 5_000_000))
    report = rebuild(good)
    assert report["state"] == "ok", report
    assert report["converged"] is True, report
    assert any(item["code"] == "info.sketch.closed_contours" for item in report["diagnostics"]), report
    broken = new_sketch()
    _commands.add_line(broken, p1=(0, 0), p2=(5_000_000, 0))
    report = rebuild(broken)
    assert "err.sketch.no_closed_contour" in report["errors"], report
    dirty = new_sketch()
    _commands.add_circle(dirty, center=(0, 0), radius_nm=1_000_000)
    dirty["entities"][0]["geom"]["r"] = 1.5
    report = rebuild(dirty)
    assert "err.sketch.geometry_error" in report["errors"], report
    doc = {"meta": {}, "sketches": [good], "features": [{"uuid": "f-1", "kind": "extrude",
                                                         "inputs": [good["uuid"]], "params": {}}]}
    report = rebuild(doc)
    assert report["dependents"][good["uuid"]] == ["f-1"], report["dependents"]
    assert report["pending_features"] == ["f-1"] and report["state"] == "partial", report
    assert len(entities) == 4 and len(good["entities"]) == 4, "rebuild must not mutate geometry"
    print("SW_SKETCH_REBUILD verdict=PASS checks=5")
    return 0


def main() -> int:
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败
        print("SW_SKETCH_REBUILD verdict=FAIL error=%s: %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
