"""Read-only review of the main cadcore replay/schema boundary.

Discovery defaults to the package containing this file; standalone use may
point --source-package at a different read-only package. Failures are regression
counterexamples, not expectedFailure
or skipped cases. Fixtures are in-memory L1 documents built with public sketch
commands; real FreeCAD jobs and all observations stay in external evidence.

Accept both suites against the package containing this file::

    sudo -n -u infra python3 -B tests/test_feature_replay_review.py --suite both
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import unittest
import uuid
from datetime import datetime, timezone

sys.dont_write_bytecode = True
DEFAULT_PACKAGE = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-replay-review")
ARGS = argparse.Namespace(source_package=DEFAULT_PACKAGE, evidence_dir=DEFAULT_EVIDENCE,
                          suite="replay", test_name=[])
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--suite", choices=("replay", "namespace", "both"), default="replay")
    parser.add_argument("--test-name", action="append", default=[],
                        help="Run only the named replay test method; repeat for multiple tests.")
    ARGS = parser.parse_args()
    if ARGS.test_name and ARGS.suite != "replay":
        parser.error("--test-name requires --suite replay")
    SOURCE_PACKAGE = ARGS.source_package.resolve()
else:
    SOURCE_PACKAGE = DEFAULT_PACKAGE
sys.path.insert(0, str(SOURCE_PACKAGE))


def load_core():
    """Resolve only the selected package; never silently fall back to main."""
    global docmodel, CadError, features_registry, subproc, commands
    docmodel = importlib.import_module("cadcore.docmodel")
    CadError = importlib.import_module("cadcore.errors").CadError
    features_registry = importlib.import_module("cadcore.kernel.features_registry")
    subproc = importlib.import_module("cadcore.kernel.subproc")
    commands = importlib.import_module("cadcore.sketch.commands")
    for module in (docmodel, features_registry, subproc, commands):
        if not Path(module.__file__).resolve().is_relative_to(SOURCE_PACKAGE):
            raise AssertionError("review imported the wrong package: " + module.__file__)

MM = 1_000_000
IDS = {key: str(uuid.uuid5(uuid.NAMESPACE_URL, "cadcore-replay-review/" + key))
       for key in ("rect", "wide", "circle", "radial", "copy", "base", "later",
                   "modifier", "missing", "future")}


def sketch(key, corners=None, radius=None, plane="XY"):
    result = {"uuid": IDS[key], "plane": plane, "entities": [],
              "constraints": [], "dimensions": [],
              "solved": {"dof": -1, "converged": False, "cache": True}}
    if radius is not None:
        commands.add_circle(result, center=(0, 0), radius_nm=radius, layer="0")
    else:
        for index, point in enumerate(corners):
            commands.add_line(result, p1=point, p2=corners[(index + 1) % len(corners)], layer="0")
    return result


def fixture():
    model = docmodel.new_document(unit="mm", standard_profile="iso")
    small = [(0, 0), (10 * MM, 0), (10 * MM, 8 * MM), (0, 8 * MM)]
    model.sketches.extend([
        sketch("rect", small),
        sketch("wide", [(0, 0), (20 * MM, 0), (20 * MM, 10 * MM), (0, 10 * MM)]),
        sketch("circle", radius=2 * MM),
        sketch("radial", [(3 * MM, 0), (5 * MM, 0), (5 * MM, 6 * MM), (3 * MM, 6 * MM)], plane="XZ"),
        sketch("copy", small),
    ])
    model.features.append({"uuid": IDS["base"], "kind": "extrude",
                           "inputs": [IDS["rect"]], "suppressed": False,
                           "params": {"distance_nm": 6 * MM, "symmetric": False, "op": "new"}})
    return model.to_payload()


def features(step_text):
    return {
        "sweep": {"sketch_uuid": IDS["circle"], "path_points_nm": [[0, 0, 0], [0, 0, 6 * MM]]},
        "loft": {"profile_uuids": [IDS["rect"], IDS["copy"]],
                 "offsets_nm": [[0, 0, 0], [0, 0, 6 * MM]], "ruled": True},
        "fillet": {"size_nm": MM // 2, "edge_indices": [1]},
        "chamfer": {"size_nm": MM // 2, "edge_indices": [1]},
        "shell": {"thickness_nm": MM, "face_indices": [6], "inward": True},
        "pattern": {"translations_nm": [[0, 0, 0], [20 * MM, 0, 0]], "fuse": False},
        "mirror": {"normal_axis": "x", "offset_nm": 0, "keep_original": False},
        "import_step": {"step_text": step_text},
    }


def feature_document(kind, params):
    document = fixture()
    item = {"uuid": IDS["modifier"], "kind": kind, "inputs": [IDS["base"]],
            "params": copy.deepcopy(params), "suppressed": False}
    if kind in ("sweep", "loft", "import_step"):
        item["inputs"] = ([params["sketch_uuid"]] if kind == "sweep" else
                          list(params["profile_uuids"]) if kind == "loft" else [])
        document["features"] = [item]
    else:
        document["features"].append(item)
    return document


def source_hashes():
    return {str(path.relative_to(SOURCE_PACKAGE)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((SOURCE_PACKAGE / "cadcore").rglob("*.py"))}


class FeatureReplayReview(unittest.TestCase):
    evidence_root = None
    observations = []
    kernel = None

    @classmethod
    def setUpClass(cls):
        cls.observations = []
        if cls.evidence_root is None:
            cls.evidence_root = DEFAULT_EVIDENCE / datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ-discover")
        cls.evidence_root.mkdir(parents=True, exist_ok=True)
        try:
            load_core()
        except Exception:
            detail = traceback.format_exc()
            (cls.evidence_root / "setup-error.txt").write_text(detail, encoding="utf-8")
            (cls.evidence_root / "result.json").write_text(json.dumps({
                "verdict": "FAIL", "phase": "package_import", "tests": 0,
                "errors": 1, "skipped": 0, "exit_code": 1,
                "source_package": str(SOURCE_PACKAGE), "review_file": str(Path(__file__).resolve()),
                "review_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "finished_at": datetime.now(timezone.utc).isoformat(), "traceback": detail,
            }, indent=2, sort_keys=True), encoding="utf-8")
            raise
        executable = shutil.which("freecadcmd")
        if executable is None:
            raise AssertionError("real FreeCAD required: freecadcmd is not on PATH")
        cls.kernel = subproc.SubprocessKernel(
            executable=executable, probe_timeout_s=15,
            work_dir=cls.evidence_root / "kernel", keep_work_dir=True,
            extra_env={"QT_QPA_PLATFORM": "offscreen"})
        cls.addClassCleanup(cls.kernel.close)
        report = cls.kernel.probe_report(force=True)
        (cls.evidence_root / "probe.json").write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        if not report.available:
            raise AssertionError("real FreeCAD is required: " + str(report.as_dict()))
        cls.model = fixture()
        problems = docmodel.check_invariants(cls.model) + docmodel.range_problems(cls.model)
        if problems:
            raise AssertionError("invalid review fixture: " + str(problems))
        cls.base = cls.kernel.shape_from_document(cls.model)
        props = cls.kernel.mass_properties(cls.base)
        if abs(props.volume_nm3 / 1e18 - 480) > 1e-6:
            raise AssertionError("fixture did not build the analytic 480 mm3 base solid")
        cls.kernel.export(cls.base, fmt="step", path=str(cls.evidence_root / "base.step"))
        cls.specs = features((cls.evidence_root / "base.step").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        (cls.evidence_root / "observations.json").write_text(
            json.dumps(cls.observations, indent=2, sort_keys=True), encoding="utf-8")
        (cls.evidence_root / "kernel-stats.json").write_text(
            json.dumps(cls.kernel.stats(), indent=2, sort_keys=True), encoding="utf-8")

    def reject(self, label, value, operation, measure=False):
        before = copy.deepcopy(value)
        record = {"test": self.id(), "case": label, "input": before, "expected": "reject"}
        try:
            result = operation()
        except (CadError, ValueError) as exc:
            if getattr(exc, "reason", None) == "no_solid_feature":
                record.update(outcome="accepted", error_type=type(exc).__name__,
                              error=str(exc), reason=exc.reason)
                self.observations.append(record)
                self.fail(label + " was only hidden by all-suppressed empty geometry")
            record.update(outcome="rejected", error_type=type(exc).__name__,
                          error=str(exc), reason=getattr(exc, "reason", None))
            self.observations.append(record)
            self.assertEqual(value, before, "replay mutated the caller's input")
            return
        except Exception as exc:
            record.update(outcome="unexpected-error", error_type=type(exc).__name__,
                          error=str(exc), traceback=traceback.format_exc())
            self.observations.append(record)
            raise
        record["outcome"] = "accepted"
        if isinstance(result, subproc.SubprocessShape):
            record["recipe"] = result.to_dict()
            if measure:
                try:
                    record["properties"] = self.kernel.mass_properties(result).as_dict()
                except CadError as exc:
                    record["geometry_error"] = exc.as_dict()
        else:
            record["recipe"] = result
        self.observations.append(record)
        self.assertEqual(value, before, "replay mutated the caller's input")
        self.fail("%s was silently accepted; see observations.json" % label)

    def measure(self, label, document, expected):
        before = copy.deepcopy(document)
        shape = self.kernel.shape_from_document(document)
        props = self.kernel.mass_properties(shape)
        self.assertAlmostEqual(props.volume_nm3 / 1e18, expected, delta=1e-5)
        self.assertEqual(document, before)
        self.observations.append({"test": self.id(), "case": label, "outcome": "measured",
                                  "input": before, "recipe": shape.to_dict(),
                                  "properties": props.as_dict()})
        return props

    def test_fixture_is_l1_valid_and_real_geometry(self):
        self.assertEqual(docmodel.check_invariants(self.model), [])
        self.assertEqual(docmodel.range_problems(self.model), [])
        props = self.measure("analytic base", self.model, 480)
        self.assertEqual(props.bbox_nm, (0, 0, 0, 10 * MM, 8 * MM, 6 * MM))

    def test_empty_document_without_step_preserves_dependency_missing_reason(self):
        dangling = fixture()
        dangling["sketches"] = []
        absent = copy.deepcopy(dangling)
        absent.pop("sketches")
        for label, document in (("empty mapping", {}), ("empty tables", {
                "sketches": [], "features": []}), ("dangling extrude", dangling),
                ("absent sketches", absent)):
            before = copy.deepcopy(document)
            with self.subTest(case=label):
                with self.assertRaises(CadError) as caught:
                    self.kernel.shape_from_document(document)
                exc = caught.exception
                self.observations.append({
                    "test": self.id(), "case": label, "input": before,
                    "outcome": "rejected", "error_type": type(exc).__name__,
                    "reason": exc.reason, "error": str(exc),
                })
                self.assertEqual(document, before)
                self.assertIsInstance(exc, subproc.DependencyMissing)
                self.assertEqual(exc.reason, "empty_document")

    def test_malformed_tables_are_rejected_before_empty_document(self):
        for field in ("sketches", "features"):
            for invalid in (None, {}, (), "not-a-list", 0):
                document = {"sketches": [], "features": [], field: invalid}
                before = copy.deepcopy(document)
                with self.subTest(field=field, value=invalid):
                    with self.assertRaises(subproc.SubprocError) as caught:
                        self.kernel.shape_from_document(document)
                    exc = caught.exception
                    self.observations.append({
                        "test": self.id(), "case": "malformed " + field,
                        "input": before, "outcome": "rejected",
                        "error_type": type(exc).__name__, "reason": exc.reason,
                        "error": str(exc),
                    })
                    self.assertEqual(document, before)
                    self.assertEqual(exc.reason, "bad_features_table")

    def test_standalone_step_without_sketch_really_builds_solid(self):
        for explicit_table in (False, True):
            document = feature_document("import_step", self.specs["import_step"])
            if explicit_table:
                document["sketches"] = []
            else:
                document.pop("sketches")
            with self.subTest(explicit_sketches=explicit_table):
                self.assertEqual(len(document["features"]), 1)
                self.assertEqual(document["features"][0]["inputs"], [])
                props = self.measure("standalone STEP empty sketch table=%s" % explicit_table,
                                     document, 480)
                self.assertEqual(props.source, "external")
                self.assertEqual(props.bbox_nm, (0, 0, 0, 10 * MM, 8 * MM, 6 * MM))

    def test_valid_registry_recipes_really_replay_all_kinds(self):
        import math

        expected = {"sweep": math.pi * 4 * 6, "loft": 480, "pattern": 960,
                    "mirror": 480, "import_step": 480}
        self.assertEqual(set(self.specs), features_registry.KINDS)
        for kind, params in self.specs.items():
            with self.subTest(kind=kind):
                step = features_registry.feature_step(kind, params, self.base, self.model, self.kernel)
                recipe = (step,) if kind in ("sweep", "loft", "import_step") else self.base.recipe + (step,)
                props = self.kernel.mass_properties(subproc.SubprocessShape(recipe=recipe))
                self.assertEqual(props.source, "external")
                self.assertGreater(props.volume_nm3, 0)
                if kind in expected:
                    self.assertAlmostEqual(props.volume_nm3 / 1e18, expected[kind], delta=1e-5)
                else:
                    self.assertLess(props.volume_nm3 / 1e18, 480)
                self.observations.append({"test": self.id(), "case": kind, "outcome": "measured",
                                          "recipe": step, "properties": props.as_dict()})

    def test_valid_registry_documents_really_replay_all_kinds(self):
        import math

        expected = {"sweep": math.pi * 24, "loft": 480, "pattern": 960,
                    "mirror": 480, "import_step": 480}
        for kind, params in self.specs.items():
            with self.subTest(kind=kind):
                document = feature_document(kind, params)
                shape = self.kernel.shape_from_document(document)
                props = self.kernel.mass_properties(shape)
                self.assertEqual(props.source, "external")
                self.assertGreater(props.volume_nm3, 0)
                if kind in expected:
                    self.assertAlmostEqual(props.volume_nm3 / 1e18, expected[kind], delta=1e-5)
                else:
                    self.assertLess(props.volume_nm3 / 1e18, 480)
                self.observations.append({"test": self.id(), "case": "document " + kind,
                                          "outcome": "measured", "recipe": shape.to_dict(),
                                          "properties": props.as_dict()})

    def test_loft_repeated_profile_uuid_at_distinct_offsets_is_valid(self):
        """Reusing a section is legal; duplicating sketch-table UUIDs is not."""
        for ruled in (False, True):
            params = {"profile_uuids": [IDS["rect"], IDS["rect"]],
                      "offsets_nm": [[MM, 2 * MM, 3 * MM], [MM, 2 * MM, 9 * MM]],
                      "ruled": ruled}
            document = feature_document("loft", params)
            document["sketches"] = [document["sketches"][0]]
            feature = document["features"][0]
            with self.subTest(ruled=ruled):
                self.assertEqual(feature["inputs"], params["profile_uuids"])
                self.assertEqual(len(document["sketches"]), 1)
                self.assertEqual(docmodel.check_invariants(document), [])
                self.assertEqual(docmodel.range_problems(document), [])
                # The 10 x 8 mm section spans 6 mm, regardless of its translation.
                props = self.measure("loft repeated profile ruled=%s" % ruled, document, 480)
                self.assertEqual(props.source, "external")
                self.assertEqual(props.bbox_nm, (MM, 2 * MM, 3 * MM, 11 * MM, 10 * MM, 9 * MM))

    def test_unknown_registry_parameters_are_not_discarded(self):
        for kind, valid in self.specs.items():
            params = dict(valid, unexpected_geometry_flag=True)
            with self.subTest(kind=kind):
                self.reject("registry unknown params " + kind, params, lambda:
                            features_registry.feature_step(kind, params, self.base, self.model, self.kernel))

    def test_document_registry_unknown_parameters_are_rejected(self):
        for kind, valid in self.specs.items():
            document = feature_document(kind, dict(valid, unexpected_geometry_flag=True))
            with self.subTest(kind=kind):
                self.reject("document unknown params " + kind, document,
                            lambda: self.kernel.shape_from_document(document), measure=kind == "loft")

    def test_builtin_feature_unknown_parameters_are_rejected(self):
        for kind in ("extrude", "revolve"):
            document = fixture()
            params = document["features"][0]["params"]
            if kind == "revolve":
                document["features"][0].update(kind=kind, inputs=[IDS["radial"]])
                document["features"][0]["params"] = params = {"axis": "z", "angle_deg": 360}
            params["unexpected_geometry_flag"] = True
            with self.subTest(kind=kind):
                self.reject("builtin unknown params " + kind, document,
                            lambda: self.kernel.shape_from_document(document), measure=True)

    def test_unknown_document_params_raise_subproc_error_not_base_cad_error(self):
        for kind in ("extrude", "revolve", *self.specs):
            document = (fixture() if kind in ("extrude", "revolve") else
                        feature_document(kind, self.specs[kind]))
            if kind == "revolve":
                document["features"][0].update(
                    kind=kind, inputs=[IDS["radial"]], params={"axis": "z", "angle_deg": 360})
            document["features"][-1]["params"]["unexpected_geometry_flag"] = True
            with self.subTest(kind=kind):
                with self.assertRaises(subproc.SubprocError):
                    self.kernel.shape_from_document(document)

    def test_modifier_operation_is_not_silently_ignored(self):
        for kind in ("pattern", "mirror", "fillet", "chamfer", "shell"):
            document = feature_document(kind, dict(self.specs[kind], op="cut"))
            with self.subTest(kind=kind):
                self.reject("unsupported modifier op " + kind, document,
                            lambda: self.kernel.shape_from_document(document), measure=kind == "pattern")

    def test_suppressed_requires_actual_boolean_at_replay_boundary(self):
        for value in ("false", "true", 0, 1, None, [], {}):
            document = feature_document("pattern", self.specs["pattern"])
            document["features"][1]["suppressed"] = value
            with self.subTest(value=value):
                self.reject("nonbool suppressed %r" % value, document,
                            lambda: self.kernel.shape_from_document(document), measure=True)

    def test_actual_boolean_suppression_has_expected_geometry(self):
        for value, volume in ((False, 960), (True, 480)):
            document = feature_document("pattern", self.specs["pattern"])
            document["features"][1]["suppressed"] = value
            with self.subTest(value=value):
                self.measure("boolean suppressed %s" % value, document, volume)

    def test_valid_all_suppressed_reports_no_solid(self):
        for document in (fixture(), feature_document("pattern", self.specs["pattern"])):
            for feature in document["features"]:
                feature["suppressed"] = True
            with self.subTest(features=len(document["features"])):
                with self.assertRaises(CadError) as caught:
                    self.kernel.shape_from_document(document)
                self.assertEqual(caught.exception.reason, "no_solid_feature")

    def test_all_suppressed_invalid_params_are_not_hidden(self):
        for kind in ("extrude", *self.specs):
            document = (fixture() if kind == "extrude" else
                        feature_document(kind, self.specs[kind]))
            for feature in document["features"]:
                feature["suppressed"] = True
            document["features"][-1]["params"]["unexpected_geometry_flag"] = True
            with self.subTest(kind=kind):
                self.reject("all suppressed unknown params " + kind, document,
                            lambda: self.kernel.shape_from_document(document))
        document = fixture()
        document["features"][0].update(suppressed=True)
        document["features"][0]["params"]["distance_nm"] = 0.5
        self.reject("all suppressed invalid nm", document,
                    lambda: self.kernel.shape_from_document(document))

    def test_missing_or_wrong_kind_inputs_do_not_fallback_to_first_sketch(self):
        for inputs in ([], [IDS["missing"]], [IDS["base"]], ["not-a-uuid"], "not-a-list", {},
                       [IDS["rect"], IDS["missing"]], [True], [None]):
            document = fixture()
            document["features"][0]["inputs"] = inputs
            with self.subTest(inputs=inputs):
                self.reject("invalid extrude inputs", document,
                            lambda: self.kernel.shape_from_document(document), measure=True)

    def test_explicit_sketch_reference_is_resolved_not_defaulted(self):
        document = fixture()
        document["features"][0]["inputs"] = [IDS["wide"]]
        self.measure("explicit wide profile", document, 1200)
        document["features"][0]["sketch_uuid"] = IDS["missing"]
        with self.assertRaises(CadError) as caught:
            self.kernel.shape_from_document(document)
        self.assertEqual(caught.exception.reason, "unknown_sketch")

    def test_legacy_missing_inputs_and_short_ids_remain_valid(self):
        document = fixture()
        document["features"][0].pop("inputs")
        self.measure("legacy missing inputs", document, 480)
        document = fixture()
        document["sketches"][0]["uuid"] = "legacy-sketch"
        document["features"][0].update(uuid="legacy-feature", inputs=["legacy-sketch"])
        self.measure("legacy nonempty short IDs", document, 480)

    def test_legacy_zero_draft_values_are_preserved(self):
        for value in (0, "0"):
            document = fixture()
            document["features"][0]["params"]["angle_deg"] = value
            with self.subTest(value=value):
                self.measure("legacy zero draft %r" % value, document, 480)

    def test_nonzero_extrusion_draft_is_not_ignored(self):
        for value in (1, -1, "1"):
            document = fixture()
            document["features"][0]["params"]["angle_deg"] = value
            with self.subTest(value=value):
                self.reject("unsupported nonzero draft", document,
                            lambda: self.kernel.shape_from_document(document))

    def test_modifier_missing_self_forward_or_suppressed_feature_inputs_are_rejected(self):
        for reference in (IDS["missing"], IDS["modifier"], IDS["future"], IDS["later"]):
            document = feature_document("pattern", self.specs["pattern"])
            later = copy.deepcopy(document["features"][0])
            later.update(uuid=IDS["later"], suppressed=True)
            document["features"].insert(1, later)
            document["features"][2]["inputs"] = [reference]
            future = copy.deepcopy(document["features"][0])
            future["uuid"] = IDS["future"]
            document["features"].append(future)
            with self.subTest(reference=reference):
                self.reject("invalid modifier base reference " + reference, document,
                            lambda: self.kernel.shape_from_document(document), measure=True)

    def test_registry_missing_or_malformed_profile_refs_fail_with_schema_errors(self):
        for reference in (IDS["missing"], IDS["base"], None, [], {}, 3, True):
            params = copy.deepcopy(self.specs["loft"])
            params["profile_uuids"][1] = reference
            with self.subTest(reference=reference):
                self.reject("invalid loft profile ref", params, lambda:
                            features_registry.feature_step("loft", params, self.base, self.model, self.kernel))

    def test_feature_input_domain_must_match_profile_parameters(self):
        for kind in ("sweep", "loft"):
            mismatch = ([IDS["rect"]] if kind == "sweep" else [IDS["copy"], IDS["rect"]])
            for refs in ([IDS["base"]], [IDS["missing"]], [True], "not-a-list", mismatch):
                document = feature_document(kind, self.specs[kind])
                document["features"][0]["inputs"] = refs
                with self.subTest(kind=kind, refs=refs):
                    self.reject("profile input domain " + kind, document,
                                lambda: self.kernel.shape_from_document(document))

    def test_duplicate_input_refs_are_rejected(self):
        for kind in ("extrude", "pattern", "loft"):
            document = fixture() if kind == "extrude" else feature_document(kind, self.specs[kind])
            feature = document["features"][-1]
            feature["inputs"] = [feature["inputs"][0], feature["inputs"][0]]
            with self.subTest(kind=kind):
                self.reject("duplicate inputs " + kind, document,
                            lambda: self.kernel.shape_from_document(document))

    def test_duplicate_sketch_uuids_are_rejected_instead_of_ambiguous_geometry(self):
        for kind in ("extrude", "loft"):
            document = fixture()
            document["sketches"][1]["uuid"] = IDS["rect"]
            if kind == "loft":
                document["features"][0].update(kind="loft", params={
                    "profile_uuids": [IDS["rect"], IDS["rect"]],
                    "offsets_nm": [[0, 0, 0], [0, 0, 6 * MM]], "ruled": True})
            self.assertEqual(document["sketches"][0]["uuid"], document["sketches"][1]["uuid"])
            with self.subTest(kind=kind):
                self.reject("duplicate sketch UUID " + kind, document,
                            lambda: self.kernel.shape_from_document(document), measure=True)

    def test_duplicate_feature_uuids_are_rejected(self):
        document = feature_document("pattern", self.specs["pattern"])
        document["features"][1]["uuid"] = IDS["base"]
        self.assertTrue(any("duplicate" in problem for problem in docmodel.check_invariants(document)))
        self.reject("duplicate feature UUID", document,
                    lambda: self.kernel.shape_from_document(document), measure=True)

    def test_sketch_length_coordinates_are_not_coerced(self):
        for field, value in (("x1", 0.5), ("x1", False), ("x1", "0"),
                             ("x1", 2 ** 53 + 1)):
            document = fixture()
            document["sketches"][0]["entities"][0]["geom"][field] = value
            with self.subTest(field=field, value=value):
                self.reject("invalid sketch coordinate", document,
                            lambda: self.kernel.shape_from_document(document), measure=type(value) is not int)

    def test_radius_is_not_coerced_from_float_bool_or_string(self):
        for value in (2 * MM + 0.5, True, str(2 * MM)):
            document = fixture()
            document["features"][0]["inputs"] = [IDS["circle"]]
            document["sketches"][2]["entities"][0]["geom"]["r"] = value
            with self.subTest(value=value):
                self.reject("invalid sketch radius", document,
                            lambda: self.kernel.shape_from_document(document), measure=type(value) is not bool)

    def test_registry_length_range_matches_l1_boundary(self):
        invalid = 2 ** 53 + 1
        cases = [
            ("sweep", "path_points_nm", [[0, 0, 0], [0, 0, invalid]]),
            ("loft", "offsets_nm", [[0, 0, 0], [0, 0, invalid]]),
            ("fillet", "size_nm", invalid), ("chamfer", "size_nm", invalid),
            ("shell", "thickness_nm", invalid),
            ("pattern", "translations_nm", [[0, 0, 0], [invalid, 0, 0]]),
            ("mirror", "offset_nm", invalid),
        ]
        for kind, key, value in cases:
            params = copy.deepcopy(self.specs[kind])
            params[key] = value
            document = feature_document(kind, params)
            self.assertTrue(docmodel.range_problems(document), "L1 range ruler must see the invalid input")
            with self.subTest(kind=kind):
                self.reject("registry out of L1 range " + kind, params, lambda:
                            features_registry.feature_step(kind, params, self.base, self.model, self.kernel))

    def test_extrusion_length_range_matches_l1_boundary(self):
        document = fixture()
        document["features"][0]["params"]["distance_nm"] = 2 ** 53 + 1
        self.assertTrue(docmodel.range_problems(document))
        self.reject("extrusion out of L1 range", document,
                    lambda: self.kernel.shape_from_document(document))

    def test_document_length_range_matches_l1_boundary_even_when_suppressed(self):
        cases = [
            ("sweep", "path_points_nm", [[0, 0, 0], [0, 0, 2 ** 53 + 1]]),
            ("loft", "offsets_nm", [[0, 0, 0], [0, 0, 2 ** 53 + 1]]),
            ("fillet", "size_nm", 2 ** 53 + 1),
            ("chamfer", "size_nm", 2 ** 53 + 1),
            ("shell", "thickness_nm", 2 ** 53 + 1),
            ("pattern", "translations_nm", [[0, 0, 0], [2 ** 53 + 1, 0, 0]]),
            ("mirror", "offset_nm", 2 ** 53 + 1),
        ]
        for kind, key, value in cases:
            for suppressed in (False, True):
                document = feature_document(kind, dict(self.specs[kind], **{key: value}))
                for feature in document["features"]:
                    feature["suppressed"] = suppressed
                with self.subTest(kind=kind, suppressed=suppressed):
                    self.reject("document L1 range " + kind, document,
                                lambda: self.kernel.shape_from_document(document))

    def test_sketch_table_invalid_entries_are_not_silently_filtered(self):
        document = fixture()
        document["sketches"].insert(0, None)
        self.reject("nonmapping sketch row", document,
                    lambda: self.kernel.shape_from_document(document), measure=True)


def main():
    started = datetime.now(timezone.utc)
    run_dir = ARGS.evidence_dir / started.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "write-probe").touch(exist_ok=False)
    FeatureReplayReview.evidence_root = run_dir
    os.environ["CADCORE_NAMESPACE_EVIDENCE"] = str(run_dir / "namespace-kernel")
    before = source_hashes()
    (run_dir / "sources-before.json").write_text(json.dumps(before, indent=2, sort_keys=True), encoding="utf-8")
    wall = time.monotonic()
    suite = unittest.TestSuite()
    review_files = [Path(__file__).resolve()]
    if ARGS.suite in ("replay", "both"):
        if ARGS.test_name:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromNames(
                ARGS.test_name, FeatureReplayReview))
        else:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(FeatureReplayReview))
    if ARGS.suite in ("namespace", "both"):
        path = Path(__file__).with_name("test_roundtrip_extension_namespaces.py")
        review_files.append(path.resolve())
        spec = importlib.util.spec_from_file_location("review_namespaces", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        producer_rows = [{"kind": item.kind, "key": item.key, "note": item.note}
                         for item in module.PRODUCERS]
        (run_dir / "namespace-producers.json").write_text(
            json.dumps(producer_rows, indent=2, sort_keys=True), encoding="utf-8")
        main_path = SOURCE_PACKAGE / "tests" / path.name
        patch = subprocess.run(
            ["git", "diff", "--no-index", "--", str(main_path), str(path)],
            text=True, capture_output=True)
        if patch.returncode not in (0, 1):
            raise RuntimeError("cannot capture producer diff: " + patch.stderr)
        (run_dir / "namespace-main-to-seat.patch").write_text(patch.stdout, encoding="utf-8")
        (run_dir / "namespace-file.json").write_text(json.dumps({
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "main_path": str(main_path),
            "main_sha256": hashlib.sha256(main_path.read_bytes()).hexdigest(),
        }, indent=2, sort_keys=True), encoding="utf-8")
    with (run_dir / "tests.log").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = source_hashes()
    drift = [path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)]
    (run_dir / "sources-after.json").write_text(json.dumps(after, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "verdict": "PASS" if result.wasSuccessful() and result.testsRun and not result.skipped and not drift else "FAIL",
        "suite": ARGS.suite,
        "selected_tests": ARGS.test_name,
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "elapsed_s": time.monotonic() - wall,
        "source_package": str(SOURCE_PACKAGE), "source_files": len(before), "source_drift": drift,
        "review_file": str(Path(__file__).resolve()),
        "review_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "test_files": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in review_files},
        "evidence_dir": str(run_dir), "uid": os.getuid(),
        "exit_code": 0 if result.wasSuccessful() and result.testsRun and not result.skipped and not drift else 1,
        "counterexamples": [item for item in FeatureReplayReview.observations
                            if item["outcome"] in ("accepted", "unexpected-error")],
        "failure_details": [{"test": str(test), "traceback": detail}
                            for test, detail in result.failures + result.errors],
    }
    (run_dir / "result.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    status = subprocess.run(["git", "status", "--short", "--branch"], check=True,
                            text=True, capture_output=True)
    (run_dir / "worktree-status.txt").write_text(status.stdout, encoding="utf-8")
    verdict = ("FEATURE_REPLAY_REVIEW verdict=%s tests=%s failures=%s errors=%s skipped=%s "
          "counterexamples=%s source_drift=%s elapsed_s=%.3f at=%s evidence=%s" %
          (summary["verdict"], result.testsRun, len(result.failures), len(result.errors),
           len(result.skipped), len(summary["counterexamples"]), len(drift), summary["elapsed_s"],
           summary["finished_at"], run_dir))
    with (run_dir / "tests.log").open("a", encoding="utf-8") as stream:
        stream.write(verdict + "\nRC=%s\n" % summary["exit_code"])
    print(verdict)
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
