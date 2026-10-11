"""Real FreeCAD STEP acceptance, without mocks/skips.

Run this file with --evidence-dir to retain the summary, log, job recipes,
embedded STEP inputs, meshes and genuine FreeCAD exports outside the worktree.
The profile injection mirrors the parent integration without editing subproc.
Standalone --source-package selects an explicit read-only product dependency;
discovery always uses the package containing this test.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    source_parser = argparse.ArgumentParser(add_help=False)
    source_parser.add_argument("--source-package", type=Path, default=_ROOT)
    source_args, _ = source_parser.parse_known_args()
    _ROOT = source_args.source_package.resolve()
sys.path.insert(0, str(_ROOT))

from cadcore.kernel import features_step, subproc

MM = 1_000_000
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-step")
_RUN_DIR: Path | None = None

_POINT_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('point; not a solid'),'2;1');
FILE_NAME('point.step','2026-10-10T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
#1=CARTESIAN_POINT('',(0.,0.,0.));
ENDSEC;
END-ISO-10303-21;
"""

_READ_AUDIT_SCRIPT = '''
import json
import os
import sys
import FreeCAD as App
import Part

shape = Part.Shape()
shape.read(os.environ["SEAT_STEP_AUDIT_INPUT"])
payload = {
    "version": App.Version()[:3],
    "read_doc": Part.Shape.read.__doc__,
    "shape_type": shape.ShapeType,
    "valid": shape.isValid(),
    "null": shape.isNull(),
    "volume_mm3": shape.Volume,
    "has_center_of_mass": hasattr(shape, "CenterOfMass"),
    "solids": [{"type": solid.ShapeType, "valid": solid.isValid(),
                "volume_mm3": solid.Volume,
                "center_mm": list(tuple(solid.CenterOfMass))}
               for solid in shape.Solids],
}
with open(os.environ["SEAT_STEP_AUDIT_RESULT"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
print("STEP_READ_AUDIT", json.dumps(payload, sort_keys=True))
'''


def _run_dir() -> Path:
    global _RUN_DIR
    if _RUN_DIR is None:
        EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        _RUN_DIR = Path(tempfile.mkdtemp(prefix="run-", dir=EVIDENCE_ROOT))
    return _RUN_DIR


def _inject(source: str) -> str:
    anchor = "def _build_recipe(recipe):"
    dispatch_anchor = '    raise ValueError("unsupported build op: %s" % op)'
    if source.count(anchor) != 1 or source.count(dispatch_anchor) != 1:
        raise AssertionError("adapter integration anchors missing or ambiguous")
    source = source.replace(anchor, "_EXTENDED_BUILDERS = {}\n"
                            + features_step.ADAPTER_SOURCE + "\n" + anchor, 1)
    return source.replace(dispatch_anchor,
                          "    if op in _EXTENDED_BUILDERS:\n"
                          "        return _EXTENDED_BUILDERS[op](step, previous)\n"
                          + dispatch_anchor, 1)


def _rect(kernel, x=0):
    return kernel.sketch_to_wire({"entities": [{
        "kind": "polyline",
        "geom": {"points": [[x, 0], [x + 12 * MM, 0],
                            [x + 12 * MM, 8 * MM], [x, 8 * MM], [x, 0]]},
    }]})


class TextBoundaryTests(unittest.TestCase):
    def test_exact_recipe_and_original_text_preserved(self):
        self.assertEqual(features_step.make_step(step_text=_POINT_STEP),
                         {"op": "import_step", "step_text": _POINT_STEP})

    def test_keyword_only(self):
        with self.assertRaises(TypeError):
            features_step.make_step(_POINT_STEP)

    def test_non_string_inputs(self):
        for text in (None, b"ISO-10303-21;", 1, {}, []):
            with self.subTest(type=type(text).__name__), self.assertRaises(TypeError):
                features_step.make_step(step_text=text)

    def test_empty_inputs(self):
        for text in ("", " \n\t\r"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                features_step.make_step(step_text=text)

    def test_non_ascii_and_invalid_utf8_surrogates(self):
        for char in ("\u4e2d", "\ufeff", "\u00e9", "\ud800", "\udfff"):
            with self.subTest(char=repr(char)), self.assertRaisesRegex(ValueError, "ASCII"):
                features_step.make_step(step_text=_POINT_STEP.replace("point.step", char))

    def test_controls(self):
        for char in ("\x00", "\x01", "\x1f", "\x7f"):
            with self.subTest(char=repr(char)), self.assertRaisesRegex(ValueError, "control"):
                features_step.make_step(step_text=_POINT_STEP.replace("point.step", char))

    def test_inclusive_8_mib_boundary(self):
        text = _POINT_STEP + " " * (features_step.MAX_STEP_BYTES - len(_POINT_STEP))
        self.assertEqual(len(features_step.make_step(step_text=text)["step_text"]),
                         8 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "8 MiB"):
            features_step.make_step(step_text=text + " ")

    def test_header_alone_and_truncated_files(self):
        for text in ("ISO-10303-21;", _POINT_STEP.split("DATA;")[0],
                     _POINT_STEP.replace("END-ISO-10303-21;", ""),
                     _POINT_STEP.replace("ENDSEC;", "", 1)):
            with self.subTest(text=text[:30]), self.assertRaises(ValueError):
                features_step.make_step(step_text=text)

    def test_required_header_records(self):
        for name in ("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "header"):
                features_step.make_step(step_text=_POINT_STEP.replace(name, "WRONG"))

    def test_empty_or_non_entity_data(self):
        for data in ("", "garbage;", "#1=garbage;", "#1=CARTESIAN_POINT()"):
            text = re.sub(r"#1=CARTESIAN_POINT.*?;\n", data + "\n", _POINT_STEP)
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "DATA"):
                features_step.make_step(step_text=text)

    def test_quoted_fake_entities_and_comments_are_not_data(self):
        for data in ("/* #1=CARTESIAN_POINT(); */", "'#1=CARTESIAN_POINT();'"):
            text = _POINT_STEP.replace("#1=CARTESIAN_POINT('',(0.,0.,0.));", data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                features_step.make_step(step_text=text)

    def test_quotes_comments_crlf_and_escaped_unicode_are_preserved(self):
        text = ("/* source */\r\n" + _POINT_STEP.replace(
            "point.step", r"quoted''name\X2\4E2D\X0\.step")).replace("\n", "\r\n")
        self.assertEqual(features_step.make_step(step_text=text)["step_text"], text)

    def test_unterminated_lexemes_and_trailing_junk(self):
        for text in (_POINT_STEP + "junk", _POINT_STEP + "/* unterminated",
                     _POINT_STEP.replace("'point.step'", "'point.step"),
                     _POINT_STEP + "'ENDSEC;'"):
            with self.subTest(text=text[-30:]), self.assertRaises(ValueError):
                features_step.make_step(step_text=text)

    def test_input_is_not_a_path(self):
        path = _run_dir() / "text-boundary-source.step"
        original = _POINT_STEP.encode("ascii")
        path.write_bytes(original)
        self.assertTrue(path.is_file())
        with self.assertRaises(ValueError):
            features_step.make_step(step_text=str(path))
        with self.assertRaises(TypeError):
            features_step.make_step(step_text=_POINT_STEP, path=str(path))
        self.assertEqual(path.read_bytes(), original)

    def test_real_module_and_snippet_contract(self):
        self.assertEqual(Path(features_step.__file__).resolve(),
                         _ROOT / "cadcore/kernel/features_step.py")
        self.assertTrue(features_step.ADAPTER_SOURCE.isascii())
        self.assertIn('_EXTENDED_BUILDERS["import_step"] = _build_import_step',
                      features_step.ADAPTER_SOURCE)
        compile(_inject(subproc.get_profile().adapter_script), "<step-adapter>", "exec")

    def test_adapter_uses_the_single_validation_source(self):
        self.assertIn(inspect.getsource(features_step._validate_text),
                      features_step.ADAPTER_SOURCE)
        self.assertEqual(features_step.ADAPTER_SOURCE.count("def _validate_text("), 1)
        self.assertIn("_validate_text(text)", features_step.ADAPTER_SOURCE)
        self.assertIn("MAX_STEP_BYTES = %d" % features_step.MAX_STEP_BYTES,
                      features_step.ADAPTER_SOURCE)


class RealStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = _run_dir() / "real"
        cls.directory.mkdir()
        cls.executable = shutil.which("freecadcmd")
        if cls.executable is None:
            raise AssertionError("real FreeCAD required: freecadcmd is not on PATH")
        cls.kernel = subproc.SubprocessKernel(
            executable=cls.executable, work_dir=str(cls.directory / "jobs"),
            keep_work_dir=True, probe_timeout_s=15, op_timeout_s=60,
            extra_env={"QT_QPA_PLATFORM": "offscreen"},
        )
        report = cls.kernel.probe_report()
        (cls.directory / "probe.json").write_text(
            json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        if not report.available:
            raise AssertionError("real FreeCAD required: " + json.dumps(report.as_dict()))
        cls.kernel._profile = replace(
            cls.kernel._profile, adapter_script=_inject(cls.kernel._profile.adapter_script))
        base = cls.kernel.extrude(_rect(cls.kernel), distance_nm=5 * MM)
        circle = cls.kernel.sketch_to_wire({"entities": [{
            "kind": "circle", "geom": {"cx": 6 * MM, "cy": 4 * MM, "r": 2 * MM},
        }]})
        bore = cls.kernel.extrude(circle, distance_nm=5 * MM)
        cls.source = cls.kernel.boolean("cut", base, bore)
        cls.original = cls.directory / "source.step"
        cls.kernel.export(cls.source, fmt="step", path=str(cls.original))
        cls.step_text = cls.original.read_text(encoding="ascii")
        cls.imported = subproc.SubprocessShape(
            recipe=(features_step.make_step(step_text=cls.step_text),))
        # The original name no longer exists before any import operation.
        cls.original.rename(cls.directory / "source-archived.step")
        cls.expected_volume = 480 - 20 * math.pi

    def _record(self, data):
        self._record_count = getattr(self, "_record_count", 0) + 1
        suffix = "" if self._record_count == 1 else "-%d" % self._record_count
        (_run_dir() / (self._testMethodName + suffix + ".json")).write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _assert_loud(self, step, message):
        before = set((self.directory / "jobs").glob("job-*"))
        shape = subproc.SubprocessShape(recipe=(step,))
        with self.assertRaises(subproc.KernelExecutionError) as caught:
            self.kernel.mass_properties(shape)
        self.assertEqual(caught.exception.reason, subproc.REASON_OP_FAILED)
        self.assertEqual(caught.exception.details["rc"], 3)
        after = set((self.directory / "jobs").glob("job-*")) - before
        self.assertEqual(len(after), 1)
        job_dir = after.pop()
        payload = json.loads((job_dir / "result.json").read_text())
        self.assertIs(payload["ok"], False)
        self.assertIn(message, payload["error"])
        self.assertTrue(payload["traceback_tail"])
        self._record({"job_dir": str(job_dir), "payload": payload,
                      "error": str(caught.exception), "details": caught.exception.details})
        return job_dir

    def test_volume_bbox_and_real_hole(self):
        self.assertFalse(self.original.exists())
        props = self.kernel.mass_properties(self.imported)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, self.expected_volume, places=6)
        self.assertNotAlmostEqual(props.volume_nm3 / 1e18, 480, places=3)
        self.assertEqual(props.bbox_nm, (0, 0, 0, 12 * MM, 8 * MM, 5 * MM))
        self.assertEqual(props.center_nm, (6 * MM, 4 * MM, 2500000))
        self.assertEqual(props.unmeasured, ())
        self._record(props.as_dict())

    def test_direct_part_read_api_topology_and_solid_center(self):
        scripts = self.directory / "audit-scripts"
        scripts.mkdir()
        script = scripts / "audit.py"
        script.write_text(_READ_AUDIT_SCRIPT, encoding="ascii")
        target = self.directory / "read-audit.json"
        env = dict(os.environ, SEAT_STEP_AUDIT_INPUT=str(
            self.directory / "source-archived.step"), SEAT_STEP_AUDIT_RESULT=str(target))
        log_path = self.directory / "read-audit.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run(
                [self.executable, str(script)], cwd=self.directory,
                env=env, stdout=log, stderr=subprocess.STDOUT, check=False,
            )
        self.assertEqual(process.returncode, 0, log_path.read_text())
        payload = json.loads(target.read_text())
        self.assertTrue(payload["read_doc"])
        self.assertTrue(payload["valid"])
        self.assertFalse(payload["null"])
        self.assertEqual(len(payload["solids"]), 1)
        self.assertEqual(payload["solids"][0]["type"], "Solid")
        self.assertTrue(payload["solids"][0]["valid"])
        self.assertAlmostEqual(payload["volume_mm3"], self.expected_volume, places=6)
        for actual, expected in zip(payload["solids"][0]["center_mm"], (6, 4, 2.5)):
            self.assertAlmostEqual(actual, expected, places=6)
        self._record(payload)

    def test_real_mesh_indices_and_properties(self):
        mesh = self.kernel.tessellate(self.imported)
        self.assertEqual(mesh["solid_count"], 1)
        self.assertGreater(len(mesh["triangles"]), 12)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18,
                               self.expected_volume, places=6)
        vertices = mesh["vertices_mm"]
        for triangle in mesh["triangles"]:
            self.assertEqual(len(triangle), 3)
            for index in triangle:
                self.assertTrue(0 <= index < len(vertices))
        self.assertTrue(all(math.isfinite(v) for point in vertices for v in point))
        self._record(mesh)

    def test_inclusive_8_mib_real_import(self):
        text = self.step_text + " " * (features_step.MAX_STEP_BYTES - len(self.step_text))
        shape = {"recipe": [features_step.make_step(step_text=text)]}
        props = self.kernel.mass_properties(shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, self.expected_volume, places=6)
        self.assertEqual(props.center_nm, (6 * MM, 4 * MM, 2500000))
        self._record({"input_bytes": len(text.encode("ascii")),
                      "properties": props.as_dict()})

    def test_recipe_json_replay_without_original_path(self):
        path = self.directory / "l1-recipe.json"
        feature = {"kind": "import_step", "params": {"step_text": self.step_text}}
        document = {"features": [feature], "shape": self.imported.to_dict()}
        path.write_text(json.dumps(document, ensure_ascii=True),
                        encoding="utf-8")
        restored = json.loads(path.read_text(encoding="utf-8"))
        payload = restored["shape"]
        self.assertEqual(restored["features"][0]["params"]["step_text"], self.step_text)
        self.assertEqual(payload["recipe"][0]["step_text"], self.step_text)
        self.assertEqual(set(payload["recipe"][0]), {"op", "step_text"})
        replay = {"recipe": [features_step.make_step(**restored["features"][0]["params"])]}
        props = self.kernel.mass_properties(replay)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, self.expected_volume, places=6)
        self._record({"recipe": str(path), "properties": props.as_dict()})

    def test_step_export_import_second_roundtrip(self):
        target = self.directory / "roundtrip.step"
        self.kernel.export(self.imported, fmt="step", path=str(target))
        text = target.read_text(encoding="ascii")
        target.rename(self.directory / "roundtrip-archived.step")
        replay = {"recipe": [features_step.make_step(step_text=text)]}
        props = self.kernel.mass_properties(replay)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, self.expected_volume, places=6)
        self.assertEqual(props.bbox_nm, (0, 0, 0, 12 * MM, 8 * MM, 5 * MM))
        self._record({"bytes": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "properties": props.as_dict()})

    def test_import_file_is_new_job_local_and_exact(self):
        before = set((self.directory / "jobs").glob("job-*"))
        self.kernel.mass_properties(self.imported)
        job_dirs = set((self.directory / "jobs").glob("job-*")) - before
        self.assertEqual(len(job_dirs), 1)
        job_dir = job_dirs.pop()
        inputs = list(job_dir.glob("import-step-*.step"))
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].read_bytes(), self.step_text.encode("ascii"))
        self.assertEqual(inputs[0].parent, job_dir)
        job = json.loads((job_dir / "job.json").read_text())
        self.assertEqual(job["shape"]["recipe"][0]["step_text"], self.step_text)
        self._record({"job_dir": str(job_dir), "input": str(inputs[0]),
                      "bytes": inputs[0].stat().st_size})

    def test_two_imports_in_boolean_create_distinct_owned_inputs(self):
        before = set((self.directory / "jobs").glob("job-*"))
        common = self.kernel.boolean("intersect", self.imported, self.imported)
        props = self.kernel.mass_properties(common)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, self.expected_volume, places=6)
        job_dir = (set((self.directory / "jobs").glob("job-*")) - before).pop()
        inputs = list(job_dir.glob("import-step-*.step"))
        self.assertEqual(len(inputs), 2)
        self.assertTrue(all(item.read_bytes() == self.step_text.encode("ascii")
                            for item in inputs))
        self._record({"inputs": [str(item) for item in inputs],
                      "properties": props.as_dict()})

    def test_multiple_real_solids(self):
        one = self.kernel.extrude(_rect(self.kernel), distance_nm=5 * MM)
        two = self.kernel.extrude(_rect(self.kernel, 20 * MM), distance_nm=5 * MM)
        target = self.directory / "two-solids.step"
        self.kernel.export(self.kernel.boolean("union", one, two),
                           fmt="step", path=str(target))
        imported = {"recipe": [features_step.make_step(
            step_text=target.read_text(encoding="ascii"))]}
        mesh = self.kernel.tessellate(imported)
        self.assertEqual(mesh["solid_count"], 2)
        self.assertAlmostEqual(mesh["properties"]["volume_nm3"] / 1e18, 960, places=6)
        self.assertEqual(mesh["properties"]["bbox_nm"],
                         [0, 0, 0, 32 * MM, 8 * MM, 5 * MM])
        if "center_nm" in mesh["properties"]:
            self.assertEqual(mesh["properties"]["center_nm"], [16 * MM, 4 * MM, 2500000])
        else:
            self.assertIn("center_nm", mesh["properties"]["unmeasured"])
        self._record(mesh)

    def test_genuine_wire_step_is_not_a_solid(self):
        target = self.directory / "wire.step"
        self.kernel.export(_rect(self.kernel), fmt="step", path=str(target))
        step = features_step.make_step(step_text=target.read_text(encoding="ascii"))
        self._assert_loud(step, "STEP import failed")

    def test_point_entities_cannot_fake_real_import(self):
        self._assert_loud(features_step.make_step(step_text=_POINT_STEP),
                          "STEP import failed")

    def test_unknown_entity_real_parser_failure_is_loud(self):
        text = _POINT_STEP.replace("CARTESIAN_POINT", "NOT_A_REAL_STEP_ENTITY")
        job_dir = self._assert_loud(features_step.make_step(step_text=text),
                                   "STEP import failed")
        self.assertEqual(len(list(job_dir.glob("import-step-*.step"))), 1)

    def test_bypassed_recipe_boundary_is_revalidated_before_writing(self):
        for text, message in (
            ("ISO-10303-21;", "complete Part 21"),
            (_POINT_STEP + "\u4e2d", "ASCII"),
            (_POINT_STEP + "\ud800", "ASCII"),
            (_POINT_STEP + "\x00", "control"),
            (" " * (features_step.MAX_STEP_BYTES + 1) + _POINT_STEP, "8 MiB"),
            ("", "empty"),
            (None, "string"),
        ):
            with self.subTest(message=message):
                job_dir = self._assert_loud({"op": "import_step", "step_text": text},
                                           message)
                self.assertEqual(list(job_dir.glob("import-step-*.step")), [])

    def test_arbitrary_path_fields_are_rejected(self):
        path = self.directory / "unrelated-source.step"
        original = self.step_text.encode("ascii")
        path.write_bytes(original)
        self.assertTrue(path.is_file())
        step = features_step.make_step(step_text=self.step_text)
        step["path"] = str(path)
        job_dir = self._assert_loud(step, "only op and embedded")
        self.assertEqual(list(job_dir.glob("import-step-*.step")), [])
        self.assertEqual(path.read_bytes(), original)


def main() -> int:
    global EVIDENCE_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=_ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_ROOT)
    parser.add_argument("-k", default="")
    args = parser.parse_args()
    for module in (features_step, subproc):
        if not Path(module.__file__).resolve().is_relative_to(_ROOT):
            raise AssertionError("STEP tests imported the wrong package: " + module.__file__)
    EVIDENCE_ROOT = args.evidence_dir
    run_dir = _run_dir()
    scratch = run_dir / "scratch"
    scratch.mkdir()
    tempfile.tempdir = str(scratch)
    os.environ["TMPDIR"] = str(scratch)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    loader = unittest.TestLoader()
    if args.k:
        loader.testNamePatterns = ["*" + args.k + "*"]
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    with (run_dir / "tests.log").open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    ok = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
    summary = {"verdict": "PASS" if ok else "FAIL", "tests": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors),
               "skips": len(result.skipped), "collected_at": datetime.now(timezone.utc).isoformat(),
               "evidence": str(run_dir), "source_package": str(_ROOT),
               "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "source_sha256": hashlib.sha256(Path(features_step.__file__).read_bytes()).hexdigest()}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("STEP_FEATURE_TESTS " + json.dumps(summary, sort_keys=True))
    if not ok:
        print((run_dir / "tests.log").read_text(encoding="utf-8"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
