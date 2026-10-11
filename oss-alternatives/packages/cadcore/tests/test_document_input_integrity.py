"""Independent input-integrity regressions, with external red/green evidence.

Direct execution binds --source-package explicitly and writes only --out.
Discovery uses the containing cadcore package. No production HTTP is used.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import unittest

LOCAL_PACKAGE = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = LOCAL_PACKAGE
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=LOCAL_PACKAGE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--regressions", action="store_true")
    ARGS = parser.parse_args()
    SOURCE_PACKAGE = ARGS.source_package.resolve()
    sys.path.insert(0, str(SOURCE_PACKAGE))

from cadcore import docmodel
from cadcore.ops import draw_extra
from cadcore.sketch import commands


def ident(number):
    return f"a11ce000-0000-4000-8000-{number:012x}"


def line(number=3, **geom):
    return {"uuid": ident(number), "kind": "line", "layer": "0",
            "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 100, "y2": 0, **geom}}


def payload():
    result = docmodel.new_document(standard_profile="gbt").to_payload()
    result["meta"]["doc_uuid"] = ident(1)
    result["sketches"] = [{"uuid": ident(2), "entities": [line()],
                           "constraints": [], "dimensions": []}]
    return result


class DocumentInputIntegrityTests(unittest.TestCase):
    def rejected(self, candidate, prefix=None):
        before = copy.deepcopy(candidate)
        problems = docmodel.load_blocking_problems(candidate)
        self.assertTrue(problems, "invalid candidate silently passed the load gate")
        if prefix:
            self.assertTrue(any(item.startswith(prefix) for item in problems), problems)
        self.assertEqual(candidate, before, "validation changed its input")
        decoded = docmodel.document_from_l1(docmodel.document_to_l1(candidate))
        self.assertTrue(docmodel.load_blocking_problems(decoded))

    def test_actual_module_and_nonempty_fixture(self):
        self.assertEqual(Path(docmodel.__file__).resolve(),
                         SOURCE_PACKAGE / "cadcore/docmodel.py")
        clean = payload()
        self.assertEqual(len(clean["sketches"][0]["entities"]), 1)
        self.assertEqual(docmodel.load_blocking_problems(clean), [])

    def test_coordinate_scalar_types_strict_integer(self):
        for value in (True, False, None, "1", "", [], {}, 1.0, 0.25):
            with self.subTest(value=repr(value)):
                candidate = payload()
                candidate["sketches"][0]["entities"][0]["geom"]["x1"] = value
                self.rejected(candidate)

    def test_known_coordinate_keys_and_radius_types(self):
        for key in ("x1", "y1", "x2", "y2", "cx", "cy", "r", "x", "y",
                    "px", "py", "rx", "ry", "radius_nm"):
            with self.subTest(key=key):
                candidate = payload()
                candidate["sketches"][0]["entities"][0]["geom"][key] = True
                self.rejected(candidate)

    def test_coordinate_range_rejected(self):
        candidate = payload()
        candidate["sketches"][0]["entities"][0]["geom"]["x1"] = 10**100
        self.rejected(candidate, "nm_out_of_range:")

    def test_missing_geometry_never_defaults_to_zero(self):
        for geom in (None, {}, [], "bad"):
            with self.subTest(geom=repr(geom)):
                candidate = payload()
                candidate["sketches"][0]["entities"][0]["geom"] = geom
                self.rejected(candidate)
        candidate = payload()
        del candidate["sketches"][0]["entities"][0]["geom"]
        self.rejected(candidate)

    def test_incomplete_known_geometry_rejected(self):
        for kind, geom in (
            ("line", {"x1": 0, "y1": 0, "x2": 100}),
            ("circle", {"cx": 0, "cy": 0}),
            ("arc", {"cx": 0, "cy": 0, "r": 100, "start_deg": 0}),
            ("point", {"x": 0}), ("ellipse", {"cx": 0, "cy": 0, "rx": 10}),
            ("polyline", {"closed": False}), ("spline", {"scheme": "vendor"}),
            ("ray", {"px": 0, "py": 0, "dx": 1}),
        ):
            with self.subTest(kind=kind):
                candidate = payload()
                candidate["sketches"][0]["entities"][0].update(kind=kind, geom=geom)
                self.rejected(candidate, "geom_missing_field:")

    def test_zero_length_and_nonpositive_radii_rejected(self):
        for kind, geom in (
            ("line", {"x1": 0, "y1": 0, "x2": 0, "y2": 0}),
            ("circle", {"cx": 0, "cy": 0, "r": 0}),
            ("circle", {"cx": 0, "cy": 0, "r": -1}),
            ("ellipse", {"cx": 0, "cy": 0, "rx": 0, "ry": 10}),
            ("ray", {"px": 0, "py": 0, "dx": 0.0, "dy": 0.0}),
        ):
            with self.subTest(kind=kind, geom=geom):
                candidate = payload()
                candidate["sketches"][0]["entities"][0].update(kind=kind, geom=geom)
                self.rejected(candidate, "geom_degenerate:")

    def test_nonpositive_radius_alias_rejected(self):
        for radius in (0, -1):
            candidate = payload()
            candidate["sketches"][0]["entities"][0].update(
                kind="circle", geom={"center": [0, 0], "radius": radius})
            self.rejected(candidate, "geom_degenerate:")

    def test_point_sequence_shape_and_types_rejected(self):
        for points in ([], [[0, 0]], [0, 1], [[0], [1, 1]], [[0, 0, 0], [1, 1]],
                       [[True, 0], [1, 1]], [[None, 0], [1, 1]],
                       [[0, 0], ["1", 1]], [[0, 0], [0, 0]]):
            with self.subTest(points=points):
                candidate = payload()
                candidate["sketches"][0]["entities"][0].update(
                    kind="polyline", geom={"points": points})
                self.rejected(candidate)

    def test_closed_polyline_boolean_and_noncollapsed(self):
        for geom in ({"points": [[0, 0], [100, 0]], "closed": True},
                     {"points": [[0, 0], [100, 0], [100, 100]], "closed": "false"}):
            candidate = payload()
            candidate["sketches"][0]["entities"][0].update(kind="polyline", geom=geom)
            self.rejected(candidate)

    def test_pair_geometry_legacy_aliases(self):
        for kind, geom in (
            ("line", {"p1": [0, 0], "p2": [100, 0]}),
            ("line", {"start_nm": [0, 0], "end_nm": [100, 0]}),
            ("line", {"start": [0, 0], "end": [100, 0]}),
            ("circle", {"center": [0, 0], "radius_nm": 100}),
            ("circle", {"cx": 0, "cy": 0, "radius_nm": 100}),
            ("arc", {"center_nm": [0, 0], "radius_nm": 100, "a1_deg": 0, "a2_deg": 90}),
            ("polyline", {"points_nm": [[0, 0], [100, 0], [0, 100]], "closed": True}),
        ):
            with self.subTest(kind=kind):
                candidate = payload()
                candidate["sketches"][0]["entities"][0].update(kind=kind, geom=geom)
                self.assertEqual(docmodel.load_blocking_problems(candidate), [])
                bad = copy.deepcopy(candidate)
                if kind == "line":
                    bad["sketches"][0]["entities"][0]["geom"][next(iter(geom))][0] = None
                elif kind == "circle":
                    bad["sketches"][0]["entities"][0]["geom"]["radius_nm"] = "100"
                elif kind == "arc":
                    bad["sketches"][0]["entities"][0]["geom"]["center_nm"][0] = "0"
                else:
                    bad["sketches"][0]["entities"][0]["geom"]["points_nm"][0][0] = False
                self.rejected(bad)

    def test_conflicting_alias_geometry_rejected(self):
        for kind, geom in (
            ("line", {"x1": 0, "y1": 0, "x2": 100, "y2": 0,
                      "p1": [1, 0], "p2": [100, 0]}),
            ("circle", {"cx": 0, "cy": 0, "r": 100, "radius_nm": 200}),
            ("circle", {"cx": 0, "cy": 0, "r": 100, "center": [1, 0]}),
            ("polyline", {"points": [[0, 0], [100, 0]], "points_nm": [[0, 0], [0, 100]]}),
        ):
            with self.subTest(kind=kind):
                candidate = payload()
                candidate["sketches"][0]["entities"][0].update(kind=kind, geom=geom)
                self.rejected(candidate, "geom_conflicting_fields:")

    def test_repeated_polyline_segment_rejected(self):
        candidate = payload()
        candidate["sketches"][0]["entities"][0].update(
            kind="polyline", geom={"points": [[0, 0], [100, 0], [100, 0], [0, 100]]})
        self.rejected(candidate, "geom_degenerate:")

    def test_dimensional_point_cannot_contain_nested_lists(self):
        candidate = payload()
        candidate["sketches"][0]["entities"][0].update(
            geom={"p1": [[0], 0], "p2": [100, 0]})
        self.rejected(candidate)

    def test_duplicate_entity_in_same_sketch(self):
        candidate = payload()
        candidate["sketches"][0]["entities"].append(line())
        self.rejected(candidate, "uuid_duplicate:")

    def test_duplicate_entity_across_sketches(self):
        candidate = payload()
        candidate["sketches"].append(
            {"uuid": ident(4), "entities": [line()], "constraints": [], "dimensions": []})
        self.rejected(candidate, "uuid_duplicate:")

    def test_duplicate_sketch_uuid(self):
        candidate = payload()
        candidate["sketches"].append(
            {"uuid": ident(2), "entities": [line(4)], "constraints": [], "dimensions": []})
        self.rejected(candidate, "uuid_duplicate:")

    def test_cross_kind_and_document_identity_collisions(self):
        for same in (ident(1), ident(2), ident(3)):
            with self.subTest(uuid=same):
                candidate = payload()
                candidate["features"] = [{"uuid": same, "kind": "extrude",
                    "inputs": [ident(2)], "params": {"distance_nm": 100}, "suppressed": False}]
                self.rejected(candidate, "uuid_duplicate:")

    def test_equivalent_uuid_spellings_collide(self):
        candidate = payload()
        candidate["sketches"][0]["entities"].append(
            {**line(4), "uuid": ident(3).replace("-", "").upper()})
        self.rejected(candidate, "uuid_duplicate:")

    def test_views_across_sheets_identity_collision(self):
        candidate = payload()
        candidate["sheets"] = [
            {"uuid": ident(10 + index), "views": [{"uuid": ident(20), "at_nm": [0, 0]}],
             "dimensions": []} for index in range(2)]
        self.rejected(candidate, "uuid_duplicate:")

    def test_duplicate_constraint_dimension_and_annotation(self):
        for container in ("constraints", "dimensions"):
            with self.subTest(container=container):
                candidate = payload()
                candidate["sketches"][0][container] = [
                    {"uuid": ident(4), "kind": "horizontal"},
                    {"uuid": ident(4), "kind": "horizontal"}]
                self.rejected(candidate, "uuid_duplicate:")
        candidate = payload()
        candidate["annotations"] = [{"uuid": ident(4), "kind": "note"}] * 2
        self.rejected(candidate, "uuid_duplicate:")

    def test_missing_uuid_is_blocking_without_changing_advisory_api(self):
        candidate = payload()
        del candidate["sketches"][0]["entities"][0]["uuid"]
        self.rejected(candidate, "uuid_missing:")

    def test_invalid_root_uuid_blocked(self):
        candidate = payload()
        candidate["meta"]["doc_uuid"] = "not-a-uuid"
        self.rejected(candidate, "uuid_bad:")

    def test_missing_or_malformed_meta_is_not_a_valid_raw_document(self):
        for meta in (None, [], "bad"):
            candidate = payload()
            candidate["meta"] = meta
            self.assertTrue(docmodel.load_blocking_problems(candidate))
            with self.assertRaises(docmodel.DocumentError):
                docmodel.document_from_l1(json.dumps(candidate))
        candidate = payload()
        del candidate["meta"]
        self.assertTrue(docmodel.load_blocking_problems(candidate))

    def test_explicit_null_schema_is_not_legacy_missing_version(self):
        candidate = payload()
        candidate["schema_version"] = None
        with self.assertRaises(docmodel.DocumentError) as caught:
            docmodel.document_from_l1(json.dumps(candidate))
        self.assertEqual(caught.exception.reason, "bad_schema_version")
        del candidate["schema_version"]
        decoded = docmodel.document_from_l1(json.dumps(candidate))
        self.assertEqual(decoded.schema_version, docmodel.SCHEMA_VERSION)
        self.assertEqual(docmodel.load_blocking_problems(decoded), [])

    def test_malformed_nodes_and_nested_containers_rejected(self):
        for key in ("entities", "constraints", "dimensions"):
            candidate = payload()
            candidate["sketches"][0][key] = "not-list"
            self.rejected(candidate, "bad_container:")
        for node in (None, [], "not-node", 7):
            candidate = payload()
            candidate["sketches"][0]["entities"] = [node]
            self.rejected(candidate, "bad_node:")

    def test_dimension_annotation_legacy_mirror_preserved(self):
        candidate = payload()
        candidate["sketches"][0]["dimensions"] = [{
            "uuid": ident(5), "kind": "linear", "refs": [[ident(3), "edge"]],
            "value_nm": 100, "text_key": "cmd.dimension.linear"}]
        candidate["annotations"] = [{
            "uuid": ident(5), "kind": "dimension", "target": [ident(3), "edge"],
            "value_nm": 100, "text_key": "cmd.dimension.linear"}]
        self.assertEqual(docmodel.load_blocking_problems(candidate), [])
        candidate["annotations"][0]["value_nm"] = 101
        self.rejected(candidate, "uuid_duplicate:")

    def test_third_identity_not_excused_by_dimension_mirror(self):
        candidate = json.loads((SOURCE_PACKAGE / "tests/data/plate.json").read_text())
        mirrored = candidate["sketches"][0]["dimensions"][0]["uuid"]
        candidate["features"].append({"uuid": mirrored, "kind": "unknown"})
        self.rejected(candidate, "uuid_duplicate:")

    def test_sheet_dimension_not_a_legacy_sketch_mirror(self):
        candidate = payload()
        candidate["sheets"] = [{"uuid": ident(6), "views": [], "dimensions": [
            {"uuid": ident(5), "kind": "linear", "value_nm": 100, "text_key": "cmd.dimension.linear"}]}]
        candidate["annotations"] = [{
            "uuid": ident(5), "kind": "dimension", "value_nm": 100, "text_key": "cmd.dimension.linear"}]
        self.rejected(candidate, "uuid_duplicate:")

    def test_direction_and_angle_metadata_remain_finite_only(self):
        candidate = payload()
        candidate["sketches"][0]["entities"][0].update(
            kind="arc", geom={"cx": 0, "cy": 0, "r": 100, "start_deg": False, "end_deg": 90})
        self.rejected(candidate, "angle_not_a_number:")
        candidate["sketches"][0]["entities"][0].update(
            kind="ray", geom={"px": 0, "py": 0, "dx": "1", "dy": 0})
        self.rejected(candidate, "geom_direction_invalid:")

    def test_plate_legacy_dimension_mirrors_remain_clean(self):
        fixture = SOURCE_PACKAGE / "tests/data/plate.json"
        raw = fixture.read_text()
        decoded = docmodel.document_from_l1(raw)
        self.assertEqual(len(decoded.annotations), 12)
        self.assertEqual(docmodel.load_blocking_problems(decoded), [])
        text = docmodel.document_to_l1(decoded)
        self.assertEqual(docmodel.document_to_l1(docmodel.document_from_l1(text)), text)

    def test_actual_producer_geometries_remain_loadable(self):
        candidate = docmodel.new_document(standard_profile="gbt")
        sketch = {"uuid": ident(2), "entities": [], "constraints": [], "dimensions": []}
        candidate.sketches.append(sketch)
        commands.add_line(sketch, p1=(0, 0), p2=(100, 0))
        commands.add_circle(sketch, center=(100, 100), radius_nm=20)
        commands.add_arc(sketch, center=(200, 200), radius_nm=20, start_deg=10, end_deg=70)
        draw_extra.add_point(sketch, position=(5, 7))
        draw_extra.add_ellipse(sketch, center=(0, 0), major_radius=30, minor_radius=20,
                               rotation_deg=22.5)
        draw_extra.add_spline(sketch, points_nm=[(0, 0), (100, 200), (300, 100)])
        draw_extra.add_construction_line(sketch, point=(0, 0), direction=(1, 0))
        draw_extra.add_ray(sketch, start=(0, 0), direction=(0, 1))
        self.assertEqual(len(sketch["entities"]), 8)
        self.assertEqual(docmodel.load_blocking_problems(candidate), [])
        text = docmodel.document_to_l1(candidate)
        self.assertEqual(docmodel.load_blocking_problems(docmodel.document_from_l1(text)), [])

    def test_legacy_scaffold_and_vendor_fields_preserved(self):
        candidate = payload()
        for key in ("schema_version", "features", "annotations", "sheets"):
            del candidate[key]
        candidate["x_vendor"] = {"kept": {"enabled": True, "label": None}}
        doc = docmodel.document_from_l1(json.dumps(candidate))
        self.assertEqual(docmodel.load_blocking_problems(doc), [])
        self.assertEqual(doc.extra["x_vendor"], candidate["x_vendor"])
        self.assertTrue(doc.diagnostics)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    out = ARGS.out.resolve()
    if not out.is_relative_to(Path("/3fs/research3fs/data/tmp")):
        raise ValueError("--out must be external 3FS tmp")
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    os.environ["TMPDIR"] = str(out / "tmp")
    bindings = {str(Path(docmodel.__file__).resolve()): digest(Path(docmodel.__file__)),
                str(Path(__file__).resolve()): digest(Path(__file__))}
    report = {"verdict": "FAIL", "started_at": datetime.now(timezone.utc).isoformat(),
              "source_package": str(SOURCE_PACKAGE), "source_sha256": bindings,
              "production": "NOT_TOUCHED"}
    start = time.monotonic()
    with (out / "tests.log").open("x", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(DocumentInputIntegrityTests)
            if ARGS.regressions:
                for module in ("tests.test_docmodel", "tests.test_fixtures"):
                    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(module)))
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            failed = {getattr(test, "test_case", test).id()
                      for test, _ in [*result.failures, *result.errors]}
            report.update(tests=result.testsRun, failed_tests=len(failed),
                          failures=[{"test": str(test), "traceback": detail}
                                    for test, detail in result.failures],
                          errors=[{"test": str(test), "traceback": detail}
                                  for test, detail in result.errors],
                          skip=[{"test": str(test), "reason": reason}
                                for test, reason in result.skipped])
            drift = [path for path, value in bindings.items() if digest(Path(path)) != value]
            report["source_drift"] = drift
            if result.testsRun > 0 and result.wasSuccessful() and not result.skipped and not drift:
                report["verdict"] = "PASS"
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            report["wall_s"] = time.monotonic() - start
            line_result = (f"DOCUMENT_INPUT_INTEGRITY verdict={report['verdict']} "
                           f"tests={result.testsRun} failed_tests={len(failed)} "
                           f"collected_at={report['finished_at']}")
            log.write(line_result + "\n")
    report["log_sha256"] = digest(out / "tests.log")
    with (out / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(line_result)
    print(f"evidence={out}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(run())
