#!/usr/bin/env python3
"""tools/cad3d/numeric-gate-kernel.py —— 真内核数值门（B 席交付；与 A 席的 gate-3d-capability.py 互补）。

一句话用途：拿**闭式解**当尺子，量真 CAD 内核（进程外 FreeCADCmd / OCCT）经 `cadcore.kernel.subproc`
跑出来的**数**对不对 —— 体积、表面积、重心、包围盒、投影曲线数、STEP 字节数与**往返一致**，
外加"缺特征/非法视图必须硬失败、且失败算子的退出码非 0"的负控。

中文关键词：数值门 / 真内核数值门 / 闭式解对拍 / 体积对拍 / STEP 往返 / 往返一致 / 投影曲线数 /
退出码非零负控 / numeric gate / closed-form oracle / STEP roundtrip / mass properties parity。
English keywords: CAD numeric gate, closed-form oracle, mass properties parity, STEP roundtrip,
bbox, projection curve counts, nonzero exit code negative control.

为什么写它（背景）：A 席的能力门回答"**有没有**这个能力"，回答不了"**算得对不对**"。2026-10-07 B 席
第一次把真 FreeCADCmd 0.19.2 接上时就撞到："available=true、caps 7 项"全绿，而 100×80×6 的长方体
报出 **−32000 mm³**（`Wire.extrude()` 给的是 Shell 不是 Solid）。**能力绿 ≠ 数对**，所以本门只用
能闭式验算的件：几何对了才算过，且每个数都同时给"实测 / 期望 / 相对误差"。

覆盖（四组数 + 负控；全部真跑，不许内存补丁）：
  ① 100×80×6 长方体：体积 48000 mm³、表面积 18160 mm²、重心 (0,0,3)，三视图投影，STEP 往返；
  ② `tests/data/plate.json`（120×80、R10 四角、4×⌀8 孔、拉伸 6mm，走 `shape_from_document`）：
     体积 55878.5840131754 mm³；
  ③ 同件的"只外形"变体（去掉 4 个圆）：57084.9555921539 mm³；以及"外形 + 4 次 boolean cut"的
     **另一条构造路线**，必须落到同一个数（两条独立路线互证，防"某一条路蒙对"）；
  ④ 每次 STEP 导出后**另起进程重新导入**，报字节数 / solids / 体积 / 包围盒，并断言往返一致。
负控（不做负控就不算门）：缺 extrude 特征 ⇒ `DependencyMissing(reason='no_solid_feature')`；
非法视图 ⇒ 硬失败 **且退出码 ≠ 0**（后者是 `_run_probe`/`_run_job` 把脚本放进 `scripts/` 子目录、
让脚本所在目录 ≠ cwd 之后才成立的契约；若退回"脚本就在 cwd 里"，FreeCAD 会当模块 import，
rc 会恒为 0 —— 本门会当场报红）。

用法（从仓库根跑）：
    python3 oss-alternatives/tools/cad3d/numeric-gate-kernel.py            # 人读
    python3 oss-alternatives/tools/cad3d/numeric-gate-kernel.py --json     # 机读 JSON
    python3 oss-alternatives/tools/cad3d/numeric-gate-kernel.py --kernel nano   # 换内核（nano 会因缺 step 报红，属预期）
环境变量：`CADCORE_SUBPROC_EXECUTABLE` 指向外部 CLI（缺省按 PATH 找 `freecadcmd`/`FreeCADCmd`）。
本脚本只往临时目录写产物，**不往仓库里写任何东西**。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_ROOT = REPO_ROOT / "oss-alternatives" / "packages" / "cadcore"
sys.path.insert(0, str(PKG_ROOT))

from cadcore.kernel import base, subproc  # noqa: E402

MM = 1_000_000  # nm per mm
MASS_TOL = 1e-9  # 质量属性允许的相对误差（闭式解应当逐位吻合）
ROUNDTRIP_TOL = 1e-9  # STEP 往返允许的相对误差

REIMPORT_HELPER = '''

import json
import os

import Part

path = os.environ["NUMERIC_GATE_STEP"]
shape = Part.Shape()
shape.read(path)
box = shape.BoundBox
payload = {
    "step_bytes": os.path.getsize(path),
    "solids": len(shape.Solids),
    "shells": len(shape.Shells),
    "faces": len(shape.Faces),
    "edges": len(shape.Edges),
    "volume_mm3": float(shape.Volume),
    "area_mm2": float(shape.Area),
    "bbox_min_mm": [float(box.XMin), float(box.YMin), float(box.ZMin)],
    "bbox_max_mm": [float(box.XMax), float(box.YMax), float(box.ZMax)],
    "bbox_size_mm": [float(box.XLength), float(box.YLength), float(box.ZLength)],
}
try:
    payload["center_mm"] = [float(v) for v in tuple(shape.Solids[0].CenterOfMass)[:3]]
except Exception as exc:
    payload["center_mm"] = None
    payload["center_error"] = "%s: %s" % (type(exc).__name__, exc)
print("NUMERIC_GATE_STEP_JSON " + json.dumps(payload, sort_keys=True))
'''


class Gate:
    """一条可复跑的判定行：checks 全绿才 PASS；任何一条红了带原文进 JSON。"""

    def __init__(self, kernel_name: str) -> None:
        self.kernel_name = kernel_name
        self.checks: list[dict] = []
        self.steps: list[dict] = []
        self.metrics: dict = {}
        self.notes: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def close(self, measured, expected, rel, name: str, tol: float = MASS_TOL) -> bool:
        if measured is None:
            return self.check(name, False, "unmeasured (None)")
        return self.check(name, abs(rel) <= tol,
                          "measured=%r expected=%r rel=%.3e tol=%.0e" % (measured, expected, rel, tol))

    def timed(self, label: str, fn):
        started = time.perf_counter()
        try:
            value, ok, err = fn(), True, ""
        except Exception as exc:  # 记录真错误，绝不吞
            value, ok, err = None, False, "%s: %s" % (type(exc).__name__, exc)
        elapsed = round((time.perf_counter() - started) * 1000.0, 1)
        self.steps.append({"step": label, "ok": ok, "elapsed_ms": elapsed, "error": err})
        return value, ok, err

    def verdict(self) -> tuple[bool, str]:
        bad = [item["name"] for item in self.checks if not item["ok"]]
        ok = not bad
        line = "CAD3D_NUMERIC verdict=%s kernel=%s checks=%d failed=%d" % (
            "PASS" if ok else "FAIL", self.kernel_name, len(self.checks), len(bad))
        if bad:
            line += " failed_names=" + ",".join(bad)
        return ok, line

    def as_dict(self) -> dict:
        return {"kernel": self.kernel_name, "checks": self.checks, "steps": self.steps,
                "metrics": self.metrics, "notes": self.notes,
                "verdict_line": self.verdict()[1]}


# ── 夹具构造（全部 L1 文档形态；与 `shape_from_document` 同口径）────────────────────────
def rect_document(width_mm: float, height_mm: float, depth_nm: int) -> dict:
    hx, hy = int(width_mm * MM / 2), int(height_mm * MM / 2)
    corners = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    entities = []
    for index in range(4):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % 4]
        entities.append({"kind": "line", "geom": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return {
        "unit": "mm",
        "sketches": [{"uuid": "gate-sketch", "plane": "XY", "entities": entities}],
        "features": [{"uuid": "gate-extrude", "kind": "extrude", "suppressed": False,
                      "inputs": ["gate-sketch"], "params": {"distance_nm": depth_nm, "symmetric": False}}],
    }


def doc_without_extrude(document: dict) -> dict:
    clone = json.loads(json.dumps(document))
    clone["features"] = []
    return clone


def doc_without_sketch(document: dict) -> dict:
    clone = json.loads(json.dumps(document))
    clone["sketches"] = []
    return clone


def plate_document() -> dict:
    with open(PKG_ROOT / "tests" / "data" / "plate.json", encoding="utf-8") as handle:
        return json.load(handle)


def plate_outer_only(document: dict) -> dict:
    """同件但去掉 4 个圆（孔）：只留外形轮廓（4 直线 + 4 圆角弧）。"""
    clone = json.loads(json.dumps(document))
    for sketch in clone.get("sketches", []):
        sketch["entities"] = [item for item in sketch.get("entities", []) if item.get("kind") != "circle"]
    return clone


def mm3(volume_nm3):
    return None if volume_nm3 is None else volume_nm3 / 1e18


def mm2(area_nm2):
    return None if area_nm2 is None else area_nm2 / 1e12


def rel(measured, expected):
    return None if measured is None or not expected else (measured - expected) / expected


def bbox_close(measured, expected, tol: float = 1e-6) -> bool:
    if not isinstance(measured, (list, tuple)) or len(measured) != len(expected):
        return False
    return all(abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b))) for a, b in zip(measured, expected))


def main() -> int:
    parser = argparse.ArgumentParser(description="真内核数值门（闭式解对拍 + STEP 往返 + 负控）。")
    parser.add_argument("--kernel", default="", help="内核名（缺省：取第一个可用且有 shape_from_document 的）")
    parser.add_argument("--json", action="store_true", help="只打印机读 JSON")
    parser.add_argument("--keep", action="store_true", help="保留临时产物目录（取证用）")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "cad3d-numeric-gate.json"),
                        help="机读结果落盘位置（缺省临时目录，绝不写进仓库）")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="cad3d-numeric-gate-"))
    report = base.kernel_report()
    available = base.available_kernels()
    name = args.kernel or ""
    if not name:
        for candidate in report["registered"]:
            if not available.get(candidate):
                continue
            try:
                probe_kernel = base.get_kernel(candidate)
            except Exception:
                continue
            if hasattr(probe_kernel, "shape_from_document"):
                name = candidate
                break
    if not name:
        print("CAD3D_NUMERIC verdict=INVALID reason=no available kernel with shape_from_document "
              "available_kernels=%s" % json.dumps(available, sort_keys=True))
        return 2
    try:
        kernel = base.get_kernel(name)
    except Exception as exc:
        print("CAD3D_NUMERIC verdict=INVALID kernel=%s reason=%s: %s" % (name, type(exc).__name__, exc))
        return 2
    gate = Gate(name)
    gate.notes.append("kernel_report.freecad=%s" % json.dumps(report["kernels"].get("freecad"), ensure_ascii=False))
    gate.notes.append("work_dir=%s" % work)
    caps = sorted(kernel.capabilities())
    gate.metrics["capabilities"] = caps
    gate.metrics["version"] = kernel.version
    gate.check("kernel_available", bool(kernel.available()), "available=%s version=%r" % (kernel.available(), kernel.version))
    gate.check("kernel_has_shape_from_document", hasattr(kernel, "shape_from_document"))
    for needed in ("sketch", "extrude", "mass", "project2d", "step"):
        gate.check("capability:%s" % needed, needed in caps, "caps=%s" % (caps,))

    exe = kernel.probe_report().executable or subproc.find_executable()
    gate.metrics["executable"] = exe

    def reimport(step_path: str):
        helper = work / "reimport_helper.py"
        helper.write_text(REIMPORT_HELPER, encoding="utf-8")
        env = dict(os.environ, NUMERIC_GATE_STEP=step_path)
        proc = subprocess.run([exe, str(helper)], capture_output=True, text=True, timeout=300, env=env)
        payload = None
        for line in proc.stdout.splitlines():
            if line.startswith("NUMERIC_GATE_STEP_JSON "):
                payload = json.loads(line[len("NUMERIC_GATE_STEP_JSON "):])
        return {"rc": proc.returncode, "payload": payload, "stderr_tail": proc.stderr[-300:]}

    def measure(tag: str, document: dict, expected_volume: float, expected_area: float,
                expected_bbox: list, views=("front", "top", "iso")):
        group: dict = {"expected_volume_mm3": expected_volume, "expected_area_mm2": expected_area}
        shape, ok, _err = gate.timed(tag + ".shape_from_document", lambda: kernel.shape_from_document(document))
        gate.check(tag + ".shape_from_document", ok)
        if not ok:
            return group
        mass, ok, _err = gate.timed(tag + ".mass_properties", lambda: kernel.mass_properties(shape))
        gate.check(tag + ".mass_properties", ok)
        if ok:
            volume, area = mm3(mass.volume_nm3), mm2(mass.area_nm2)
            center = [item / MM for item in (mass.center_nm or ())]
            group.update({"volume_mm3": volume, "area_mm2": area, "center_mm": center,
                          "unmeasured": list(mass.unmeasured),
                          "volume_rel_err": rel(volume, expected_volume),
                          "area_rel_err": rel(area, expected_area)})
            gate.close(volume, expected_volume, rel(volume, expected_volume), tag + ".volume")
            gate.close(area, expected_area, rel(area, expected_area), tag + ".surface_area")
        group["projections"] = {}
        for view in views:
            projection, ok, _err = gate.timed("%s.project_to_2d(%s)" % (tag, view),
                                        lambda view=view: kernel.project_to_2d(shape, view=view, hidden=True))
            gate.check(tag + ".project2d:" + view, ok)
            if ok:
                group["projections"][view] = {
                    "curves": len(projection.curves), "visible": projection.visible_curves,
                    "hidden": projection.hidden_curves, "hidden_measured": projection.hidden_measured,
                    "source": projection.source}
                gate.check(tag + ".project2d:%s.has_curves" % view, len(projection.curves) > 0,
                           "curves=%d" % len(projection.curves))
        step_path = str(work / (tag + ".step"))
        exported, ok, _err = gate.timed(tag + ".export(step)", lambda: kernel.export(shape, fmt="step", path=step_path))
        gate.check(tag + ".export(step)", ok)
        if ok:
            size = os.path.getsize(step_path)
            group["step"] = {"path": step_path, "bytes": size}
            gate.check(tag + ".step_bytes", size > 0, "bytes=%d" % size)
            back, ok, _err = gate.timed(tag + ".reimport(step)", lambda: reimport(step_path))
            gate.check(tag + ".reimport_ran", ok and back["rc"] == 0, "rc=%s" % (back["rc"] if back else None))
            payload = back["payload"] if back else None
            gate.check(tag + ".reimport_payload", payload is not None,
                       "stderr=%s" % (back["stderr_tail"] if back else ""))
            if payload:
                volume = payload["volume_mm3"]
                group["roundtrip"] = {"solids": payload["solids"], "volume_mm3": volume,
                                      "bbox_size_mm": payload["bbox_size_mm"],
                                      "faces": payload["faces"], "edges": payload["edges"],
                                      "volume_rel_err": rel(volume, expected_volume)}
                gate.check(tag + ".roundtrip.solids", payload["solids"] == 1,
                           "solids=%d" % payload["solids"])
                gate.close(volume, expected_volume, rel(volume, expected_volume),
                           tag + ".roundtrip.volume", ROUNDTRIP_TOL)
                gate.check(tag + ".roundtrip.bbox", bbox_close(payload["bbox_size_mm"], expected_bbox),
                           "bbox=%s expected=%s" % (payload["bbox_size_mm"], expected_bbox))
        return group

    # ── ① 100×80×6：闭式 48000 mm³ / 18160 mm² / 重心 z=3 ────────────────────────────
    gate.metrics["gate_a_box100x80x6"] = measure(
        "box100x80x6", rect_document(100, 80, 6 * MM), 48000.0,
        2 * 100 * 80 + 2 * 100 * 6 + 2 * 80 * 6, [100.0, 80.0, 6.0])
    box_center = gate.metrics["gate_a_box100x80x6"].get("center_mm")
    gate.check("box100x80x6.center", bbox_close(box_center, [0.0, 0.0, 3.0]),
               "center=%s expected=[0,0,3]" % (box_center,))

    # ── ② plate.json（120×80 R10 四角 + 4×⌀8 孔，拉伸 6mm）────────────────────────────
    document = plate_document()
    depth = int(document["features"][0]["params"]["distance_nm"])
    outer_area = 120 * 80 - 4 * (10 * 10 - math.pi * 100 / 4)
    hole_area = 4 * math.pi * 4.0 ** 2
    perimeter = 320.0 + 20.0 * math.pi
    gate.metrics["gate_b_plate120x80x6"] = measure(
        "plate120x80x6", document, (outer_area - hole_area) * 6,
        2 * (outer_area - hole_area) + (perimeter + 4 * 2 * math.pi * 4) * 6, [120.0, 80.0, 6.0])

    # ── ③ 两条独立路线互证：只外形（57084.9555921539）与"外形 + 4 次 boolean cut"──────
    gate.metrics["gate_c_outer120x80x6"] = measure(
        "outer120x80x6", plate_outer_only(document), outer_area * 6,
        2 * outer_area + perimeter * 6, [120.0, 80.0, 6.0])
    if "boolean" in caps:
        outer_shape, ok, _err = gate.timed("boolcut.outer_shape", lambda: kernel.shape_from_document(plate_outer_only(document)))
        plate = outer_shape
        if ok:
            for index, entity in enumerate([item for item in document["sketches"][0]["entities"]
                                            if item.get("kind") == "circle"]):
                geom = entity["geom"]
                hole_doc = {
                    "unit": "mm",
                    "sketches": [{"uuid": "hole%d" % index, "plane": "XY", "entities": [
                        {"kind": "circle", "geom": {"cx": geom["cx"], "cy": geom["cy"], "r": geom["r"]}}]}],
                    "features": [{"uuid": "hole-extrude%d" % index, "kind": "extrude", "suppressed": False,
                                  "params": {"distance_nm": depth, "symmetric": False}}],
                }
                hole, ok, _err = gate.timed("boolcut.hole%d.shape" % (index + 1),
                                      lambda hole_doc=hole_doc: kernel.shape_from_document(hole_doc))
                if not ok:
                    break
                plate, ok, _err = gate.timed("boolcut.hole%d.cut" % (index + 1),
                                       lambda plate=plate, hole=hole: kernel.boolean("cut", plate, hole))
                if not ok:
                    break
            else:
                mass, ok, _err = gate.timed("boolcut.mass", lambda: kernel.mass_properties(plate))
                gate.check("boolcut.mass", ok)
                if ok:
                    volume = mm3(mass.volume_nm3)
                    gate.metrics["gate_d_boolcut"] = {"volume_mm3": volume,
                                                      "volume_rel_err": rel(volume, (outer_area - hole_area) * 6)}
                    gate.close(volume, (outer_area - hole_area) * 6, rel(volume, (outer_area - hole_area) * 6),
                               "boolcut.volume_matches_plate")

    # ── 负控：缺特征 / 缺草图 / 非法视图（含 rc≠0）────────────────────────────────────
    negative: dict = {}
    for label, bad_document, expected_reason in (
        ("no_solid_feature", doc_without_extrude(document), "no_solid_feature"),
        ("empty_document", doc_without_sketch(document), "empty_document"),
    ):
        try:
            kernel.shape_from_document(bad_document)
            negative[label] = {"ok": False, "detail": "no exception raised"}
        except subproc.DependencyMissing as exc:
            negative[label] = {"ok": getattr(exc, "reason", "") == expected_reason,
                               "reason": getattr(exc, "reason", ""), "detail": str(exc)}
        except Exception as exc:
            negative[label] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
        gate.check("negative:" + label, negative[label]["ok"],
                   "reason=%r detail=%s" % (negative[label].get("reason"), negative[label]["detail"]))
        gate.check("negative:%s.reason" % label, negative[label].get("reason") == expected_reason,
                   "reason=%r expected=%r" % (negative[label].get("reason"), expected_reason))
    if "project2d" in caps:
        shape, ok, _err = gate.timed("negative.bad_view.shape", lambda: kernel.shape_from_document(rect_document(10, 10, MM)))
        try:
            kernel.project_to_2d(shape, view="bogus-view", hidden=True)
            negative["bad_view"] = {"ok": False, "detail": "no exception raised"}
        except subproc.KernelExecutionError as exc:
            rc = (exc.details or {}).get("rc")
            negative["bad_view"] = {"ok": rc not in (None, 0), "rc": rc, "reason": getattr(exc, "reason", ""),
                                    "detail": str(exc)[:200]}
        except Exception as exc:
            negative["bad_view"] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
        gate.check("negative:bad_view.raises", negative["bad_view"].get("rc") is not None,
                   "detail=%s" % negative["bad_view"].get("detail"))
        gate.check("negative:bad_view.nonzero_rc", bool(negative["bad_view"].get("ok")),
                   "rc=%r (0 or missing means the exit-code contract is dead again)" % negative["bad_view"].get("rc"))
    gate.metrics["negatives"] = negative
    gate.metrics["kernel_stats"] = kernel.stats()

    payload = gate.as_dict()
    try:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        gate.notes.append("could not write %s: %s" % (args.out, exc))
    if not args.keep:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    ok, line = gate.verdict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for group in ("gate_a_box100x80x6", "gate_b_plate120x80x6", "gate_c_outer120x80x6", "gate_d_boolcut"):
            item = gate.metrics.get(group)
            if not item:
                continue
            print("[%s] volume_mm3=%s (expected %s, rel=%s) area_mm2=%s" % (
                group, item.get("volume_mm3"), item.get("expected_volume_mm3", "-"),
                item.get("volume_rel_err"), item.get("area_mm2")))
            if item.get("step"):
                back = item.get("roundtrip") or {}
                print("    step_bytes=%s roundtrip solids=%s volume_mm3=%s rel=%s bbox=%s" % (
                    item["step"].get("bytes"), back.get("solids"), back.get("volume_mm3"),
                    back.get("volume_rel_err"), back.get("bbox_size_mm")))
            for view, curve in (item.get("projections") or {}).items():
                print("    project(%s): curves=%s visible=%s hidden=%s hidden_measured=%s source=%s" % (
                    view, curve.get("curves"), curve.get("visible"), curve.get("hidden"),
                    curve.get("hidden_measured"), curve.get("source")))
        for key, item in negative.items():
            print("[negative:%s] ok=%s reason=%r rc=%r" % (key, item.get("ok"), item.get("reason"), item.get("rc")))
        for note in gate.notes:
            print("[note] " + note)
        print("result -> " + args.out)
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
