"""Real FreeCAD/Poppler/DXF acceptance; no mocked geometry and no skips.

Direct entry supports --out for external evidence and optional test names.
Discovery initializes the same real fixtures, not an unpopulated harness.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[4]
PACKAGES = ROOT / "oss-alternatives/packages"
for package in ("cadcore", "dxf", "vault"):
    sys.path.insert(0, str(PACKAGES / package))

from cadcore import docmodel
from cadcore.draw.associative import (
    AssociativeDrawingError, DrawingSnapshot, export_drawing, readback_export,
    rebuild_drawing, source_document_sha256, synchronize_drawing, scene,
)
from cadcore.kernel.subproc import SubprocessKernel

MM = 1_000_000
OUT = None


def plate(width=120, hole=10, thickness=6):
    doc = docmodel.new_document()
    doc.meta["doc_uuid"] = "e53a6a58-8a8c-44d7-9d20-9dcf9928f5bd"
    doc.sketches = [
        {"uuid": "outline", "plane": "XY", "entities": [
            {"uuid": "bottom", "kind": "line", "geom": {
                "x1": 0, "y1": 0, "x2": width * MM, "y2": 0}},
            {"uuid": "right", "kind": "line", "geom": {
                "x1": width * MM, "y1": 0, "x2": width * MM, "y2": 80 * MM}},
            {"uuid": "upper", "kind": "line", "geom": {
                "x1": width * MM, "y1": 80 * MM, "x2": 0, "y2": 80 * MM}},
            {"uuid": "left", "kind": "line", "geom": {
                "x1": 0, "y1": 80 * MM, "x2": 0, "y2": 0}}]},
        {"uuid": "hole-sketch", "plane": "XY", "entities": [
            {"uuid": "hole-circle", "kind": "circle",
             "geom": {"cx": 60 * MM, "cy": 40 * MM, "r": hole * MM // 2}}]},
    ]
    doc.features = [
        {"uuid": "base", "kind": "extrude", "sketch_uuid": "outline",
         "params": {"distance_nm": thickness * MM}},
        {"uuid": "hole-cut", "kind": "extrude", "sketch_uuid": "hole-sketch",
         "params": {"distance_nm": thickness * MM, "op": "cut"}},
    ]
    return doc.to_payload()


def specification():
    return {"views": ["top", "front", "right", "iso"],
            "sections": [{"name": "section-A", "axis": "y", "offset_nm": 40 * MM,
                          "keep": "positive"}],
            "dimensions": [
                {"id": "width", "ref": ["hole-cut", "extent:x"], "kind": "extent",
                 "axis": "x", "view": "top"},
                {"id": "thickness", "ref": ["hole-cut", "extent:z"], "kind": "extent",
                 "axis": "z", "view": "front"},
                {"id": "hole", "ref": ["hole-circle", "diameter"], "kind": "diameter",
                 "view": "top"}],
            "bom_refs": [["e53a6a58-8a8c-44d7-9d20-9dcf9928f5bd", "model"]],
            "title": "Hole plate"}


class AssociativeDrawingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global OUT
        if OUT is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            OUT = Path("/hf3fs-jd/data/tmp/opencad-associative-drawings-20261011") / stamp
        OUT = OUT.resolve()
        OUT.mkdir(parents=True, exist_ok=True)
        cls.kernel = SubprocessKernel(work_dir=OUT / "kernel", keep_work_dir=True)
        cls.addClassCleanup(cls.kernel.close)
        report = cls.kernel.probe_report()
        if not report.available:
            raise RuntimeError("real FreeCAD required: " + str(report))
        cls.doc = plate()
        cls.spec = specification()
        cls.before = rebuild_drawing(cls.doc, model_version=1, spec=cls.spec,
                                     kernel=cls.kernel, work_dir=OUT / "rebuild")
        cls.new_doc = plate(width=140, hole=16, thickness=8)
        cls.after = synchronize_drawing(cls.before, cls.new_doc, model_version=2,
                                        kernel=cls.kernel, work_dir=OUT / "rebuild")

    def view(self, snapshot, name):
        return next(item for item in snapshot.to_dict()["views"] if item["name"] == name)

    def export(self, snapshot, directory):
        doc = self.doc if snapshot is self.before else self.new_doc
        return export_drawing(snapshot, document=doc, model_version=snapshot.model_version,
                              out_dir=directory)

    def test_real_worker_source_and_step_bindings(self):
        data = self.before.to_dict()
        evidence = Path(data["evidence_dir"])
        self.assertEqual(data["worker_rc"], 0)
        self.assertEqual(data["solid_count"], 1)
        self.assertEqual(data["source_doc_sha256"], source_document_sha256(self.doc))
        self.assertEqual(data["step_sha256"], hashlib.sha256((evidence / "model.step").read_bytes()).hexdigest())
        self.assertIn("DRAWING_WORKER verdict=PASS", (evidence / "worker.log").read_text())
        self.assertGreater((evidence / "model.step").stat().st_size, 1000)
        self.assertAlmostEqual(data["volume_nm3"] / MM ** 3,
                               (120 * 80 - math.pi * 5 ** 2) * 6, places=5)

    def test_principal_projection_extents_and_true_hlr(self):
        expected = {"top": (120, 80), "front": (120, 6), "right": (80, 6)}
        for name, bounds in expected.items():
            with self.subTest(view=name):
                view = self.view(self.before, name)
                self.assertEqual(view["source"], "Drawing.projectEx")
                self.assertTrue(view["hidden_measured"])
                visible = [point for curve in view["curves"] if curve["kind"] == "visible"
                           for point in curve["points_nm"]]
                spans = tuple((max(p[i] for p in visible) - min(p[i] for p in visible)) / MM
                              for i in range(2))
                self.assertEqual(spans, bounds)
                self.assertTrue(any(curve["kind"] == "hidden" for curve in view["curves"]))

    def test_section_material_gap_not_silhouette_hatch(self):
        view = self.view(self.before, "section-A")
        self.assertAlmostEqual(view["material_area_nm2"] / MM ** 2, (120 - 10) * 6, places=5)
        self.assertEqual(view["material_faces"], 2)
        self.assertGreaterEqual(len(view["profile"]), 8)
        self.assertAlmostEqual(view["kept_volume_nm3"] / MM ** 3,
                               self.before.to_dict()["volume_nm3"] / MM ** 3 / 2, places=5)

    def test_model_change_rebuilds_section_hlr_dimensions(self):
        self.assertNotEqual(self.before.source_doc_sha256, self.after.source_doc_sha256)
        self.assertEqual(self.after.model_version, 2)
        dimensions = {item["id"]: item["value_nm"] for item in self.after.to_dict()["dimensions"]}
        self.assertEqual(dimensions, {"width": 140 * MM, "hole": 16 * MM, "thickness": 8 * MM})
        self.assertAlmostEqual(self.view(self.after, "section-A")["material_area_nm2"] / MM ** 2,
                               (140 - 16) * 8, places=5)
        old_edges = self.view(self.before, "front")["curves"]
        new_edges = self.view(self.after, "front")["curves"]
        self.assertNotEqual(old_edges, new_edges)
        hidden_x = {p[0] for c in new_edges if c["kind"] == "hidden" for p in c["points_nm"]}
        self.assertIn(52 * MM, hidden_x)
        self.assertIn(68 * MM, hidden_x)

    def test_same_source_reuses_immutable_snapshot(self):
        result = synchronize_drawing(self.before, self.doc, model_version=1,
                                     kernel=self.kernel, work_dir=OUT / "reuse")
        self.assertIs(result, self.before)
        altered = result.to_dict()
        altered["dimensions"].clear()
        self.assertEqual(len(self.before.to_dict()["dimensions"]), 3)

    def test_invalid_model_does_not_change_previous_drawing(self):
        bad = copy.deepcopy(self.doc)
        bad["features"][0]["params"]["distance_nm"] = 0
        before = self.before.text
        with self.assertRaises(Exception):
            synchronize_drawing(self.before, bad, model_version=2,
                                kernel=self.kernel, work_dir=OUT / "bad-model")
        self.assertEqual(self.before.text, before)

    def test_missing_reference_is_loud_and_nonmutating(self):
        bad = copy.deepcopy(self.spec)
        bad["dimensions"][2]["ref"][0] = "missing-circle"
        with self.assertRaises(AssociativeDrawingError) as caught:
            synchronize_drawing(self.before, self.doc, model_version=1, spec=bad,
                                kernel=self.kernel, work_dir=OUT / "missing-ref")
        self.assertEqual(caught.exception.reason, "bad_drawing_reference")
        self.assertEqual(len(self.before.to_dict()["dimensions"]), 3)

    def test_real_brep_mismatch_rejects_stale_circle_reference(self):
        bad = copy.deepcopy(self.doc)
        bad["features"][1]["params"]["distance_nm"] = 1 * MM
        # Place cut beyond the model through a sketch plane unsupported by the ref contract.
        bad["sketches"][1]["entities"][0]["geom"]["cx"] = 500 * MM
        before = self.before.text
        with self.assertRaises(AssociativeDrawingError):
            synchronize_drawing(self.before, bad, model_version=3,
                                kernel=self.kernel, work_dir=OUT / "brep-mismatch")
        self.assertEqual(before, self.before.text)

    def test_boundary_section_rejected_preserving_previous(self):
        bad = copy.deepcopy(self.spec)
        bad["sections"][0]["offset_nm"] = 0
        with self.assertRaises(AssociativeDrawingError):
            synchronize_drawing(self.before, self.doc, model_version=2, spec=bad,
                                kernel=self.kernel, work_dir=OUT / "boundary")
        self.assertEqual(self.before.model_version, 1)

    def test_path_in_request_rejected(self):
        for field in ("path", "step_path", "out_dir"):
            bad = {**self.spec, field: "/etc/passwd"}
            with self.subTest(field=field), self.assertRaises(AssociativeDrawingError):
                rebuild_drawing(self.doc, model_version=1, spec=bad, kernel=self.kernel,
                                work_dir=OUT / "paths")

    def test_bom_real_component_refs_and_missing_ref(self):
        doc = copy.deepcopy(self.doc)
        doc["sw_assembly"] = {"components": [{"uuid": "component-plate", "part_key": "plate",
                                              "suppressed": False}]}
        spec = copy.deepcopy(self.spec)
        spec["bom_refs"] = [["component-plate", "component"]]
        drawing = rebuild_drawing(doc, model_version=4, spec=spec, kernel=self.kernel,
                                  work_dir=OUT / "bom")
        self.assertEqual(drawing.to_dict()["bom"][0]["quantity"], 1)
        self.assertEqual(drawing.to_dict()["bom"][0]["geometry"], "UNMEASURED")
        doc["sw_assembly"]["components"].clear()
        with self.assertRaises(AssociativeDrawingError):
            synchronize_drawing(drawing, doc, model_version=5, kernel=self.kernel,
                                work_dir=OUT / "bom")

    def test_pdf_dxf_real_readback_before_after(self):
        for name, drawing in (("before", self.before), ("after", self.after)):
            exported = self.export(drawing, OUT / ("export-" + name))
            facts = exported["readback"]
            self.assertEqual(facts["verdict"], "PASS")
            self.assertTrue(facts["dxf_roundtrip_identical"])
            self.assertGreater(facts["pdf_ink_pixels"], 100)
            self.assertGreater(facts["dxf_layers"]["HIDDEN"], 0)
            self.assertGreater(facts["dxf_layers"]["SECTION"], 0)
            self.assertIn("CIRCLE", facts["dxf_types"])

    def test_bad_pdf_body_rejected_not_magic_only(self):
        exported = self.export(self.before, OUT / "pdf-negative")
        from reportlab.pdfgen import canvas
        pdf = canvas.Canvas(exported["pdf"])
        pdf.drawString(10, 10, "Not the associated drawing")
        pdf.save()
        with self.assertRaises(AssociativeDrawingError) as caught:
            readback_export(self.before, export_dir=exported["export_dir"])
        if caught.exception.reason != "drawing_pdf_readback":
            self.fail(f"negative must reject PDF content, not unrelated failure: {caught.exception}")

    def test_bad_dxf_geometry_rejected(self):
        exported = self.export(self.before, OUT / "dxf-negative")
        path = Path(exported["dxf"])
        text = path.read_text()
        marker = "\n0\nCIRCLE\n"
        position = text.index(marker)
        prefix, tail = text[:position], text[position:]
        radius_start = tail.index("\n40\n") + 4
        radius_end = tail.index("\n", radius_start)
        tail = tail[:radius_start] + "9999" + tail[radius_end:]
        path.write_text(prefix + tail)
        with self.assertRaises(AssociativeDrawingError) as caught:
            readback_export(self.before, export_dir=exported["export_dir"])
        self.assertEqual(caught.exception.reason, "drawing_dxf_readback")

    def test_tampered_snapshot_rejected(self):
        data = self.before.to_dict()
        data["model_version"] = 999
        with self.assertRaises(AssociativeDrawingError):
            self.export(DrawingSnapshot(json.dumps(data)), OUT / "tampered")

    def test_old_snapshot_export_rejected_with_current_model(self):
        destination = OUT / "stale-export"
        with self.assertRaises(AssociativeDrawingError) as caught:
            export_drawing(self.before, document=self.new_doc, model_version=2,
                           out_dir=destination)
        self.assertEqual(caught.exception.reason, "stale_drawing")
        self.assertFalse(destination.exists())

    def test_changed_model_requires_newer_version(self):
        for version in (0, 1):
            with self.subTest(version=version), self.assertRaises(AssociativeDrawingError) as caught:
                synchronize_drawing(self.before, self.new_doc, model_version=version,
                                    kernel=self.kernel, work_dir=OUT / "version")
            self.assertEqual(caught.exception.reason, "drawing_version_conflict")

    def test_pdf_text_only_cannot_fake_geometry_readback(self):
        exported = self.export(self.before, OUT / "text-only-negative")
        from reportlab.pdfgen import canvas
        primitives, _ = scene(self.before.to_dict())
        pdf = canvas.Canvas(exported["pdf"], pagesize=(297 * 72 / 25.4, 210 * 72 / 25.4))
        pdf.setFont("Helvetica", 7)
        for item in primitives:
            if item["type"] == "TEXT":
                pdf.drawString(*(value * 72 / 25.4 for value in item["at"]), item["text"])
        pdf.save()
        with self.assertRaises(AssociativeDrawingError) as caught:
            readback_export(self.before, export_dir=exported["export_dir"])
        self.assertEqual(caught.exception.reason, "drawing_pdf_readback")
        self.assertIn("geometry primitive", str(caught.exception))

    def worker_negative(self, name, *, step=None, linked=False, changed_digest=False):
        source = Path(self.before.to_dict()["evidence_dir"])
        target = OUT / name
        (target / "scripts").mkdir(parents=True)
        shutil.copyfile(source / "scripts/drawing_worker.py", target / "scripts/drawing_worker.py")
        job = json.loads((source / "job.json").read_text())
        if linked:
            (target / "model.step").symlink_to(source / "model.step")
        else:
            (target / "model.step").write_bytes(step if step is not None else
                                                (source / "model.step").read_bytes())
        if step is not None and not changed_digest:
            job["step_sha256"] = hashlib.sha256(step).hexdigest()
        (target / "job.json").write_text(json.dumps(job))
        env = dict(os.environ, OPENCAD_DRAWING_JOB=str(target / "job.json"),
                   PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen")
        with (target / "worker.log").open("xb") as handle:
            process = subprocess.run(
                [self.kernel.probe_report().executable, str(target / "scripts/drawing_worker.py")],
                cwd=target, env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=30)
        result = json.loads((target / "worker-result.json").read_text())
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(result["ok"])
        self.assertIn("DRAWING_WORKER verdict=FAIL", (target / "worker.log").read_text())
        return result

    def test_worker_bad_step_parse_is_loud(self):
        result = self.worker_negative("worker-bad-step",
                                     step=b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
        self.assertTrue(result["error"])
        self.assertIn("traceback", result)

    def test_worker_external_link_rejected(self):
        result = self.worker_negative("worker-linked-step", linked=True)
        self.assertIn("regular local file", result["error"])

    def test_worker_changed_step_digest_rejected(self):
        result = self.worker_negative("worker-changed-step", step=b"changed STEP", changed_digest=True)
        self.assertIn("bytes changed", result["error"])

    def test_standard_and_industrial_claims_explicitly_unassessed(self):
        self.assertEqual(self.before.to_dict()["standards"], "NOT_ASSESSED")
        self.assertEqual(self.before.to_dict()["gdt_mbd"], "NOT_IMPLEMENTED")


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    OUT = args.out.resolve()
    OUT.mkdir(parents=True, exist_ok=False)
    sources = (Path(__file__), PACKAGES / "cadcore/cadcore/draw/associative.py",
               PACKAGES / "cadcore/cadcore/kernel/drawing_worker.py",
               ROOT / "docs/design/associative-drawings.md")
    before = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sources}
    suite = (unittest.defaultTestLoader.loadTestsFromNames(args.tests, module=sys.modules[__name__])
             if args.tests else unittest.defaultTestLoader.loadTestsFromTestCase(AssociativeDrawingsTests))
    with (OUT / "tests.log").open("x", encoding="utf-8") as handle:
        result = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
    files = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sources}
    drift = sorted(name for name in before if before[name] != files[name])
    facts = {"verdict": "PASS" if result.wasSuccessful() and result.testsRun and not drift else "FAIL",
             "time": datetime.now(timezone.utc).isoformat(), "tests": result.testsRun,
             "failures": len(result.failures), "errors": len(result.errors),
             "skipped": len(result.skipped), "source": files, "source_drift": drift}
    (OUT / "result.json").write_text(json.dumps(facts, indent=2) + "\n")
    print("ASSOCIATIVE_DRAWINGS " + json.dumps(facts, sort_keys=True), flush=True)
    if facts["verdict"] != "PASS":
        print((OUT / "tests.log").read_text()[-7000:])
    return 0 if facts["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
