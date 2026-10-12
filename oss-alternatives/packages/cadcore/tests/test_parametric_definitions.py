#!/usr/bin/env python3
"""Independent definition gate; real FreeCAD, no acceptance.py or old suites.

Standalone: --evidence-dir <fresh 3FS> --runtime-root <fresh /data/tmp child>.
--controls-only checks the receipt/source/terminal ruler without geometry jobs.
--verify-evidence <producer root> is offline and starts no kernel or service.
The app definition suite reuses only this gate's helpers, not these test classes.
"""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import traceback
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
CORE = ROOT / "oss-alternatives/packages/cadcore"
APP = ROOT / "oss-alternatives/apps/opencad"
sys.path[:0] = [str(CORE), str(APP), str(ROOT / "tools")]
from cadcore import docmodel, parametric
from cadcore.kernel.subproc import SubprocessKernel
from industrial_parametric import make_configurable_plate_document
import bootstrap_from_infra as bootstrap

OWNED = (
    "oss-alternatives/packages/cadcore/cadcore/parametric.py",
    "oss-alternatives/apps/opencad/industrial_parametric.py",
    "oss-alternatives/apps/opencad/project_gateway.py",
    "oss-alternatives/packages/cadcore/tests/test_parametric_definitions.py",
    "oss-alternatives/apps/opencad/tests/test_parametric_definition_api.py",
    "docs/design/parametric-definition-api.md",
)
MARKER = "OPENCAD_DEFINITION_ACCEPTANCE"
CONTEXT = None


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def require(condition, cause):
    if not condition:
        raise AssertionError(cause)


def write(path, value):
    data = value if isinstance(value, bytes) else (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def ensure_context():
    global CONTEXT
    if CONTEXT is not None:
        return
    # Normal discovery has an external parent gate; keep its owned fixtures local.
    runtime = Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT", "/data/tmp/opencad-definition-discovery"))
    require(runtime.absolute().is_relative_to(Path("/data/tmp")) and
            all(not path.is_symlink() for path in (runtime, *runtime.parents)), "local_runtime_required")
    runtime.mkdir(parents=True, exist_ok=True)
    child = runtime / ("definition-" + uuid4().hex)
    child.mkdir()
    out = Path("/hf3fs-jd/data/tmp") / ("opencad-definition-discovery-" + uuid4().hex)
    out.mkdir()
    CONTEXT = Context(out, child)


def source():
    files = {}
    for directory, dirs, names in os.walk(ROOT, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {
            ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"})
        require(all(not (Path(directory) / name).is_symlink() for name in dirs),
                "linked_source_directory")
        for name in sorted(names):
            if name == ".git":
                continue
            path = Path(directory) / name
            require(stat.S_ISREG(path.lstat().st_mode), "nonregular_source")
            data = path.read_bytes()
            bootstrap.scan_bytes(path.relative_to(ROOT).as_posix(), data)
            files[path.relative_to(ROOT).as_posix()] = {
                "sha256": digest(data), "bytes": len(data), "mode": path.stat().st_mode & 0o777}
    require(set(OWNED) <= set(files), "owned_source_missing")
    for module, expected in ((parametric, CORE / "cadcore/parametric.py"),
                             (sys.modules["industrial_parametric"], APP / "industrial_parametric.py"),
                             (sys.modules["cadcore.kernel.subproc"], CORE / "cadcore/kernel/subproc.py")):
        require(Path(module.__file__).resolve() == expected, "foreign_runtime_source")
    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "--no-replace-objects", "-C", str(ROOT), *args],
            text=True).strip()
    return {"root": str(ROOT), "head": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"), "status": git("status", "--porcelain=v1"),
            "files": files, "owned": {name: files[name] for name in OWNED},
            "sha256": digest(canonical(files))}


def mesh_volume(mesh):
    terms, incidence = [], {}
    vertices = mesh["vertices_mm"]
    require(vertices and mesh["triangles"], "empty_mesh")
    for face in mesh["triangles"]:
        require(len(face) == 3 and all(type(i) is int and 0 <= i < len(vertices) for i in face),
                "invalid_mesh_face")
        a, b, c = (vertices[i] for i in face)
        require(all(len(v) == 3 and all(type(n) in (int, float) and math.isfinite(n)
                                       for n in v) for v in (a, b, c)), "invalid_mesh_vertex")
        terms.append((a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2])
                      + a[2]*(b[0]*c[1]-b[1]*c[0]))/6)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted(tuple(round(n, 7) for n in vertices[face[k]]) for k in (i, j)))
            incidence[edge] = incidence.get(edge, 0) + 1
    require(incidence and all(n == 2 for n in incidence.values()), "open_mesh")
    return abs(math.fsum(terms))


def definition(width="50 * mm", suppressed=True):
    return {"parameters": {"width": width}, "suppressions": {"hole_cut": suppressed}}


class Context:
    def __init__(self, out, runtime):
        self.out, self.runtime = out, runtime
        self.measurements, self.http, self.cli, self.actors = [], [], [], []
        self.secrets = []
        self.lock = threading.Lock()

    def child(self, label):
        path = self.runtime / (label + "-" + uuid4().hex[:8])
        path.mkdir()
        return path

    def measure(self, document, mesh, step, expected, label, kernel, *, receipt=None,
                mesh_envelope=None):
        text = docmodel.document_to_l1(document)
        sha = digest(text)
        binding = mesh if mesh_envelope is None else mesh_envelope
        require(binding["document_sha256"] == sha and
                (mesh_envelope is None or mesh_envelope["solids"] == [mesh]), "mesh_document_mismatch")
        actual = mesh["properties"]["volume_nm3"] / 1e18
        integrated = mesh_volume(mesh)
        require(abs(actual - expected) < 1e-5 and abs(integrated - expected) < expected * .005,
                "geometry_volume_mismatch")
        require(step.startswith(b"ISO-10303-21;"), "invalid_step")
        imported = docmodel.new_document()
        imported.features.append({"uuid": str(uuid4()), "kind": "import_step", "suppressed": False,
                                  "params": {"step_text": step.decode("ascii")}})
        shape = kernel.shape_from_document(json.loads(docmodel.document_to_l1(imported)))
        props = kernel.mass_properties(shape)
        require(abs(props.volume_nm3 / 1e18 - expected) < 1e-5, "step_volume_mismatch")
        number = len(self.measurements)
        filename = f"{number:03d}-{label}.step"
        write(self.out / filename, step)
        record = {"label": label, "doc": text, "document_sha256": sha, "mesh": mesh,
                  "mesh_envelope": mesh_envelope,
                  "step_file": filename, "step_sha256": digest(step), "step_bytes": len(step),
                  "expected_mm3": expected, "brep_mm3": actual, "mesh_mm3": integrated,
                  "step_mm3": props.volume_nm3 / 1e18, "step_bbox_nm": list(props.bbox_nm),
                  "receipt": receipt}
        write(self.out / f"{number:03d}-{label}.json", record)
        self.measurements.append(record)
        return record


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_cause, self.events = None, []

    def remember(self, test, error):
        if self.first_cause is None:
            self.first_cause = {"test": str(test), "traceback": self._exc_info_to_string(error, test)}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.events.append({"test": test.id(), "pass": True})

    def addFailure(self, test, error):
        self.remember(test, error)
        super().addFailure(test, error)
        self.events.append({"test": test.id(), "pass": False})

    def addError(self, test, error):
        self.remember(test, error)
        super().addError(test, error)
        self.events.append({"test": test.id(), "pass": False})

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.remember(subtest, error)
        super().addSubTest(test, subtest, error)


def terminal(report):
    require(report["verdict"] == "PASS" and report["intended_rc"] == 0 and
            report["first_cause"] is None, "producer_failed")
    require(report["source_before"] == report["source_after"] and report["source_drift"] == [],
            "source_drift")
    require(report["tests"] == report["passed"] == report["expected_tests"] > 0 and
            report["failures"] == report["errors"] == report["skipped"] == 0,
            "test_count_mismatch")
    require(len(report["events"]) == report["tests"] and
            len({row["test"] for row in report["events"]}) == report["tests"] and
            all(row["pass"] is True for row in report["events"]), "test_ledger_mismatch")
    require(sorted(".".join(row["test"].split(".")[-2:]) for row in report["events"]) ==
            report["test_names"] and len(set(report["test_names"])) == report["tests"],
            "test_name_mismatch")
    return report["tests"]


def validate_measurement(record):
    binding = record["mesh"] if record["mesh_envelope"] is None else record["mesh_envelope"]
    require(digest(record["doc"]) == record["document_sha256"] ==
            binding["document_sha256"] and (record["mesh_envelope"] is None or
            binding["solids"] == [record["mesh"]]), "measurement_document_mismatch")
    mesh = mesh_volume(record["mesh"])
    expected = record["expected_mm3"]
    require(type(expected) in (int, float) and math.isfinite(expected) and expected > 0,
            "invalid_expected_volume")
    require(abs(mesh-expected) < expected*.005 and mesh == record["mesh_mm3"] and
            abs(record["step_mm3"]-expected) < 1e-5 and
            record["brep_mm3"] == record["mesh"]["properties"]["volume_nm3"]/1e18 and
            abs(record["brep_mm3"]-expected) < 1e-5, "measurement_volume_mismatch")
    if record["receipt"] is not None:
        require(digest(record["receipt"]["doc"]) == record["document_sha256"],
                "measurement_receipt_mismatch")
        require(type(record["receipt"]["revision"]) is int and record["receipt"]["revision"] > 0 and
                len(record["receipt"]["sha256"]) == 64 and record["receipt"]["project_id"] and
                all(binding[key] == record["receipt"][key] for key in (
                    "project_id", "revision", "sha256")),
                "measurement_receipt_identity_missing")


def verify_report(report, out, *, current=True, contract=None):
    count = terminal(report)
    if contract is not None:
        require(report["label"] == contract["label"] and
                report["test_names"] == contract[report["mode"]] and
                report["minimum_measurements"] == contract["minimum_measurements"],
                "source_test_contract_mismatch")
    if current:
        require(source() == report["source_after"], "current_source_drift")
    actual = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
              and path.relative_to(out).as_posix() not in {
                  "result.json", "terminal-candidate.json", "completion.log"}}
    require(actual == set(report["artifacts"]) and {"tests.log", "ledger.json"} <= actual,
            "artifact_set_mismatch")
    for name, item in report["artifacts"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "artifact_escape")
        path = out / name
        require(path.is_file() and not path.is_symlink(), "artifact_not_regular")
        data = path.read_bytes()
        require(digest(data) == item["sha256"] and len(data) == item["bytes"],
                "artifact_hash_mismatch")
    ledger = json.loads((out / "ledger.json").read_bytes())
    require(ledger == {key: report[key] for key in ("events", "measurements", "http", "cli", "actors")},
            "ledger_mismatch")
    require(report["mode"] == "controls" or len(report["measurements"]) >= report["minimum_measurements"],
            "geometry_not_measured")
    for record in report["measurements"]:
        validate_measurement(record)
        step = (out / record["step_file"]).read_bytes()
        require(digest(step) == record["step_sha256"] and len(step) == record["step_bytes"],
                "step_hash_mismatch")
    if report["mode"] == "real":
        labels = {
            "core-definition": {"create", "replace", "unsuppress", "suppress", "delete-base",
                                "delete-to-wide", "v1-create", "retain-override", "clear-override",
                                "downstream"},
            "http-definition": {"http-base", "http-create-restart", "http-replace", "http-unsuppress",
                                "http-undo", "http-redo", "http-save-restart", "http-delete"},
        }
        require(report["label"] in labels and
                {row["label"] for row in report["measurements"]} == labels[report["label"]] and
                len(report["measurements"]) == len(labels[report["label"]]),
                "measurement_label_mismatch")
    verify_execution(report, out)
    return count


def verify_execution(report, out):
    if report["label"] == "http-definition" and report["mode"] == "real":
        require(len(report["http"]) >= 80 and all(row["method"] in {"GET", "POST"} and
                row["route"].startswith("/api/") and row["fixture"].startswith(report["runtime_root"] + "/")
                for row in report["http"]), "http_ledger_incomplete")
        require(not any(row["route"] == "/api/open" for row in report["http"]), "open_rebuild_forbidden")
        lost = [row for row in report["http"] if row["lost_body"]]
        require(len(lost) == 1 and lost[0]["status"] == 200 and
                lost[0]["route"] == "/api/parametric" and lost[0]["request_id"] and
                any(row["request_id"] == lost[0]["request_id"] and row["status"] == 200 and
                    not row["lost_body"] for row in report["http"]), "lossresponse_not_measured")
        require({row["status"] for row in report["http"]} >= {200, 201, 400, 401, 403, 409, 422, 503},
                "http_negative_controls_missing")
        require(len(report["cli"]) == 3 and all(row["rc"] == 0 and
                row["normal_signal"] == "SIGTERM" and row["identity"]["observed_after_ready"] is True and
                row["identity"]["argv"] == row["argv"] and row["identity"]["pid"] > 0 and
                row["identity"]["startticks"].isdigit() and row["ready_marker"] in
                (out / row["log_file"]).read_text() for row in report["cli"]), "cli_terminal_incomplete")
        require(len(report["actors"]) == 3 and all(row["rc"] == 0 and not row["stderr"] and
                json.loads(row["stdout"]) == row["outcome"] for row in report["actors"]) and
                sorted(row["outcome"]["state"] for row in report["actors"]) ==
                ["BUSY", "REVOKED", "REVOKED"], "actor_terminal_incomplete")


def run_gate(classes, *, label, argv=None, minimum_measurements=1):
    global CONTEXT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--controls-only", action="store_true")
    parser.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args(argv)
    def names(items):
        return sorted(cls.__name__ + "." + name for cls in items
                      for name in unittest.defaultTestLoader.getTestCaseNames(cls))
    contract = {"label": label, "controls": names([GateControls]), "real": names(classes),
                "minimum_measurements": minimum_measurements}
    out = args.evidence_dir.resolve()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()), "external_evidence_required")
    out.mkdir(parents=True, exist_ok=False)
    if args.verify_evidence:
        try:
            original = args.verify_evidence.resolve()
            report = json.loads((original / "result.json").read_bytes())
            count = verify_report(report, original, contract=contract)
            require(json.loads((original / "terminal-candidate.json").read_bytes()) == report,
                    "terminal_candidate_mismatch")
            require((original / "completion.log").read_text() ==
                    f"{MARKER} verdict=PASS tests={count} rc=0\n", "completion_marker_mismatch")
            result = {"verdict": "EVIDENCE_VERIFIED", "checks": count, "intended_rc": 0,
                      "original_result_sha256": digest((original / "result.json").read_bytes()),
                      "source": source(), "finished_at": now(), "first_cause": None}
        except Exception:
            result = {"verdict": "FAIL", "intended_rc": 1, "finished_at": now(),
                      "first_cause": traceback.format_exc()}
            write(out / "failure.log", result["first_cause"].encode())
        write(out / "result.json", result)
        write(out / "completion.log", (f"{MARKER} verdict={result['verdict']} "
                                      f"rc={result['intended_rc']}\n").encode())
        print(f"{MARKER} verdict={result['verdict']} rc={result['intended_rc']} evidence={out}")
        return result["intended_rc"]
    runtime = args.runtime_root
    require(runtime is not None and runtime.absolute().is_relative_to(Path("/data/tmp")) and
            all(not p.is_symlink() for p in (runtime, *runtime.parents)), "local_runtime_required")
    runtime = runtime.absolute()
    runtime.mkdir(parents=True, exist_ok=False)
    filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(runtime)], text=True).strip()
    require(not filesystem.startswith("fuse") and filesystem not in {
        "nfs", "nfs4", "tmpfs", "ramfs", "cifs"}, "local_persistent_runtime_required")
    CONTEXT = Context(out, runtime)
    before = source()
    started = now()
    suite = unittest.TestSuite()
    for cls in ([GateControls] if args.controls_only else classes):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    expected = suite.countTestCases()
    with (out / "tests.log").open("x") as log:
        with redirect_stdout(log), redirect_stderr(log):
            result = unittest.TextTestRunner(
                stream=log, verbosity=2, resultclass=EvidenceResult, failfast=True).run(suite)
    after = source()
    passed = result.wasSuccessful() and expected == result.testsRun > 0 and not result.skipped
    first = result.first_cause
    if before != after:
        passed = False
        first = first or {"reason": "source_drift"}
    if not passed:
        first = first or {"reason": "empty_or_incomplete_or_skipped"}
        write(out / "failure.log", first)
    ledger = {"events": result.events, "measurements": CONTEXT.measurements,
              "http": CONTEXT.http, "cli": CONTEXT.cli, "actors": CONTEXT.actors}
    write(out / "ledger.json", ledger)
    report = {"verdict": "PASS" if passed else "FAIL", "intended_rc": 0 if passed else 1,
              "label": label, "mode": "controls" if args.controls_only else "real",
              "test_names": contract["controls" if args.controls_only else "real"],
              "expected_tests": expected, "tests": result.testsRun,
              "passed": sum(row["pass"] for row in result.events),
              "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
              "first_cause": first, "source_before": before, "source_after": after,
              "source_drift": [] if before == after else ["source"],
              "started_at": started, "finished_at": now(), "minimum_measurements": minimum_measurements,
              "runtime_root": str(runtime), "runtime_filesystem": filesystem,
              "production_operations": False, **ledger}
    report["artifacts"] = {}
    for path in out.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if any(secret in data for secret in CONTEXT.secrets):
                report.update(verdict="FAIL", intended_rc=1,
                              first_cause=report["first_cause"] or {"reason": "secret_in_evidence"})
                passed = False
            report["artifacts"][path.relative_to(out).as_posix()] = {
                "sha256": digest(data), "bytes": len(data)}
    if passed:
        try:
            write(out / "terminal-candidate.json", report)
            saved = json.loads((out / "terminal-candidate.json").read_bytes())
            require(saved == report, "terminal_roundtrip_mismatch")
            verify_report(saved, out, contract=contract)
        except Exception:
            report.update(verdict="FAIL", intended_rc=1, first_cause=traceback.format_exc())
            write(out / "failure.log", report["first_cause"].encode())
            data = (out / "failure.log").read_bytes()
            report["artifacts"]["failure.log"] = {"sha256": digest(data), "bytes": len(data)}
    write(out / "result.json", report)
    require(json.loads((out / "result.json").read_bytes()) == report, "result_roundtrip_mismatch")
    marker = f"{MARKER} verdict={report['verdict']} tests={report['tests']} rc={report['intended_rc']}\n"
    write(out / "completion.log", marker.encode())
    print(marker.strip() + " evidence=" + str(out))
    return report["intended_rc"]


class GateControls(unittest.TestCase):
    def valid(self):
        return {"verdict": "PASS", "intended_rc": 0, "first_cause": None,
                "source_before": {"head": "a"}, "source_after": {"head": "a"}, "source_drift": [],
                "tests": 1, "passed": 1, "expected_tests": 1, "failures": 0, "errors": 0, "skipped": 0,
                "test_names": ["Real.test_measured"],
                "events": [{"test": "Real.test_measured", "pass": True}]}

    def test_terminal_positive(self):
        self.assertEqual(terminal(self.valid()), 1)

    def test_head_only_and_source_drift_negative(self):
        report = self.valid()
        report["source_after"]["head"] = "b"
        with self.assertRaisesRegex(AssertionError, "source_drift"):
            terminal(report)

    def test_empty_incomplete_duplicate_false_and_firstcause_negative(self):
        for key, value in (("tests", 0), ("expected_tests", 2), ("first_cause", "old failure"),
                           ("events", []), ("events", [{"test": "Real.test_measured", "pass": False}]),
                           ("events", [{"test": "Real.test_measured", "pass": True}] * 2),
                           ("test_names", ["Other.test_missing"])):
            with self.subTest(key=key):
                report = self.valid()
                report[key] = value
                with self.assertRaises(AssertionError):
                    terminal(report)

    def test_first_error_precedes_failure(self):
        class First(unittest.TestCase):
            def runTest(self):
                raise RuntimeError("first-control-error")
        class Later(unittest.TestCase):
            def runTest(self):
                self.fail("later-control-failure")
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=EvidenceResult).run(
            unittest.TestSuite([First(), Later()]))
        self.assertIn("first-control-error", result.first_cause["traceback"])
        self.assertNotIn("later-control-failure", result.first_cause["traceback"])

    def test_owned_source_count_hash_head_and_status(self):
        ensure_context()
        captured = source()
        self.assertEqual(set(captured["owned"]), set(OWNED))
        self.assertEqual(len(captured["owned"]), 6)
        self.assertEqual(len(captured["head"]), 40)
        self.assertEqual(len(captured["sha256"]), 64)
        for name in OWNED:
            self.assertEqual(captured["owned"][name]["sha256"], digest((ROOT / name).read_bytes()))

    def test_artifact_positive_missing_and_hash_negative(self):
        ensure_context()
        out = CONTEXT.out / ("artifact-controls-" + uuid4().hex)
        out.mkdir()
        report = self.valid()
        report.update(label="ruler-fixture", mode="controls", minimum_measurements=0,
                      measurements=[], http=[], cli=[], actors=[])
        write(out / "tests.log", b"synthetic receipt-ruler control, not CAD measurement\n")
        ledger = {key: report[key] for key in ("events", "measurements", "http", "cli", "actors")}
        write(out / "ledger.json", ledger)
        report["artifacts"] = {path.name: {"sha256": digest(path.read_bytes()),
                                         "bytes": path.stat().st_size} for path in out.iterdir()}
        self.assertEqual(verify_report(report, out, current=False), 1)
        missing = copy.deepcopy(report)
        del missing["artifacts"]["tests.log"]
        with self.assertRaisesRegex(AssertionError, "artifact_set_mismatch"):
            verify_report(missing, out, current=False)
        corrupt = copy.deepcopy(report)
        corrupt["artifacts"]["tests.log"]["sha256"] = "0"*64
        with self.assertRaisesRegex(AssertionError, "artifact_hash_mismatch"):
            verify_report(corrupt, out, current=False)
        contract = {"label": "another-suite", "controls": report["test_names"],
                    "minimum_measurements": 0}
        with self.assertRaisesRegex(AssertionError, "source_test_contract_mismatch"):
            verify_report(report, out, current=False, contract=contract)

    def test_http_cli_actor_and_open_boundary_positive_negative(self):
        ensure_context()
        filename = "synthetic-cli-" + uuid4().hex + ".log"
        write(CONTEXT.out / filename, b"SYNTHETIC_READY not a real CLI receipt\n")
        report = {"label": "http-definition", "mode": "real", "runtime_root": "/data/tmp/x",
                  "http": [], "cli": [], "actors": []}
        for index in range(80):
            report["http"].append({
                "method": "POST", "route": "/api/parametric", "fixture": "/data/tmp/x/fixture",
                "status": [200, 201, 400, 401, 403, 409, 422, 503][index % 8],
                "lost_body": index == 0, "request_id": "lost" if index in (0, 8) else str(index)})
        for _ in range(3):
            report["cli"].append({
                "rc": 0, "normal_signal": "SIGTERM", "argv": ["synthetic-only"],
                "identity": {"observed_after_ready": True, "argv": ["synthetic-only"],
                             "pid": 1, "startticks": "1"},
                "log_file": filename, "ready_marker": "SYNTHETIC_READY"})
        for state in ("BUSY", "REVOKED", "REVOKED"):
            outcome = {"state": state, "changed": 0 if state == "BUSY" else 1}
            report["actors"].append({"rc": 0, "stderr": "", "stdout": json.dumps(outcome),
                                     "outcome": outcome})
        verify_execution(report, CONTEXT.out)
        cases = [
            ("http", [], "http_ledger_incomplete"),
            ("cli", [], "cli_terminal_incomplete"),
            ("actors", [], "actor_terminal_incomplete"),
        ]
        for key, value, reason in cases:
            changed = copy.deepcopy(report)
            changed[key] = value
            with self.assertRaisesRegex(AssertionError, reason):
                verify_execution(changed, CONTEXT.out)
        for field, value, reason in (
                ("route", "/api/open", "open_rebuild_forbidden"),
                ("status", 500, "lossresponse_not_measured"),
                ("lost_body", False, "lossresponse_not_measured")):
            changed = copy.deepcopy(report)
            changed["http"][0][field] = value
            with self.assertRaisesRegex(AssertionError, reason):
                verify_execution(changed, CONTEXT.out)
        changed = copy.deepcopy(report)
        changed["cli"][0]["identity"]["argv"] = ["wrong"]
        with self.assertRaisesRegex(AssertionError, "cli_terminal_incomplete"):
            verify_execution(changed, CONTEXT.out)

    def test_measurement_document_and_receipt_negative(self):
        tetra = {"vertices_mm": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                 "triangles": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
                 "properties": {"volume_nm3": 1e18/6}, "document_sha256": digest("synthetic")}
        record = {"doc": "synthetic", "document_sha256": digest("synthetic"),
                  "mesh": tetra, "mesh_envelope": None, "expected_mm3": 1/6,
                  "mesh_mm3": mesh_volume(tetra), "step_mm3": 1/6,
                  "brep_mm3": tetra["properties"]["volume_nm3"]/1e18, "receipt": None}
        validate_measurement(record)
        changed = copy.deepcopy(record)
        changed["doc"] = "different"
        with self.assertRaisesRegex(AssertionError, "measurement_document_mismatch"):
            validate_measurement(changed)
        changed = copy.deepcopy(record)
        changed["receipt"] = {"doc": "different"}
        with self.assertRaisesRegex(AssertionError, "measurement_receipt_mismatch"):
            validate_measurement(changed)


class DefinitionCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_context()
        cls.work = CONTEXT.child("core")
        cls.kernel = SubprocessKernel(work_dir=str(cls.work), keep_work_dir=True,
                                     extra_env={"TMPDIR": str(cls.work), "PYTHONDONTWRITEBYTECODE": "1"})
        cls.addClassCleanup(cls.kernel.close)
        probe = cls.kernel.probe_report()
        require(probe.available, "real_FreeCAD_required")

    def setUp(self):
        self.doc = make_configurable_plate_document(docmodel.new_document())
        self.base = docmodel.document_to_l1(self.doc)

    def prepare(self, **fields):
        return parametric.prepare_definition_rebuild(self.doc, kernel=self.kernel, **fields)

    def rejects(self, reason, **fields):
        with self.assertRaises(parametric.ParametricError) as error:
            self.prepare(**fields)
        self.assertEqual(error.exception.reason, reason)
        self.assertEqual(docmodel.document_to_l1(self.doc), self.base)

    def witness(self, prepared, expected, label):
        prepared.verify_integrity()
        self.assertEqual(prepared.base_document_sha256, digest(self.base))
        path = self.work / (uuid4().hex + ".step")
        self.kernel.export(prepared.shape, fmt="step", path=str(path))
        return CONTEXT.measure(prepared.document, prepared.mesh, path.read_bytes(), expected,
                               label, self.kernel)

    def test_create_replace_delete_and_boolean_suppression_real(self):
        operations = [
            (dict(action="create_configuration", name="custom", definition=definition(),
                  configuration="custom"), 10000, "create"),
            (dict(action="replace_configuration", name="custom", definition=definition("60 * mm"),
                  configuration="custom"), 14400, "replace"),
            (dict(action="set_suppressions", name="custom", suppressions={"hole_cut": False}),
             (1200-math.pi*36)*12, "unsuppress"),
            (dict(action="set_suppressions", name="custom", suppressions={"hole_cut": True}),
             14400, "suppress"),
            (dict(action="delete_configuration", name="custom", configuration=""),
             (800-math.pi*16)*8, "delete-base"),
        ]
        ids = [row["uuid"] for row in self.doc.features]
        for fields, expected, label in operations:
            self.base = docmodel.document_to_l1(self.doc)
            before = self.base
            prepared = self.prepare(**fields)
            self.witness(prepared, expected, label)
            self.assertEqual(docmodel.document_to_l1(self.doc), before)
            self.assertEqual([row["uuid"] for row in prepared.document.features], ids)
            self.doc = prepared.document
        self.assertNotIn("custom", self.doc.extra["parametric"]["configurations"])

    def test_saved_consistency_is_not_repaired(self):
        self.doc.features[1]["suppressed"] = True
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("configuration_state_mismatch", action="create_configuration",
                     name="new", definition=definition())

    def test_strict_boolean_negative(self):
        for flag in (0, 1, "true", None, [], {}):
            with self.subTest(flag=flag):
                self.rejects("bad_feature_configuration", action="set_suppressions",
                             name="solid", suppressions={"hole_cut": flag})

    def test_multi_configuration_dependency_and_all_suppressed_negative(self):
        self.rejects("configuration_dependency_invalid", action="create_configuration",
                     name="hidden", definition={
                         "parameters": {}, "suppressions": {"plate_boss": True, "hole_cut": True}})
        self.doc.features[1]["inputs"].append(self.doc.features[0]["uuid"])
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("configuration_dependency_invalid", action="create_configuration", name="hidden",
                     definition={"parameters": {}, "suppressions": {"plate_boss": True}})

    def test_uuid_duplicate_and_unknown_target_negative(self):
        self.rejects("unknown_feature_binding", action="set_suppressions", name="solid",
                     suppressions={"other": True})
        self.doc.extra["parametric"]["feature_bindings"]["hole_cut"]["target_uuid"] = "missing"
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("unknown_binding_target", action="delete_configuration", name="solid")
        self.doc.extra["parametric"]["feature_bindings"]["hole_cut"]["target_uuid"] = self.doc.features[0]["uuid"]
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("duplicate_binding", action="delete_configuration", name="solid")

    def test_hidden_topology_consumer_negative(self):
        self.doc.features.append({"uuid": str(uuid4()), "kind": "fillet",
                                  "inputs": [self.doc.features[0]["uuid"]], "suppressed": False,
                                  "params": {"edge_indices": [0], "radius_nm": 1000000}})
        # Start with a consistent model with no suppression variants.
        self.doc.extra["parametric"]["configuration_suppressions"] = {
            name: {} for name in self.doc.extra["parametric"]["configurations"]}
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("configuration_topology_unsupported", action="set_suppressions",
                     name="solid", suppressions={"hole_cut": True})

    def test_inactive_unit_cycle_guard_and_expression_negative(self):
        for width, reason in (("50", "unit_mismatch"), ("hole_x * 2", "parameter_cycle"),
                              ("120 * mm", "design_check_failed"),
                              ("__import__('os')", "unsafe_expression"), ("other * mm", "unknown_parameter")):
            with self.subTest(reason=reason):
                self.rejects(reason, action="create_configuration", name="inactive",
                             definition=definition(width), configuration="")

    def test_active_delete_requires_explicit_surviving_selection(self):
        active = parametric.prepare_rebuild(self.doc, kernel=self.kernel, configuration="solid")
        self.doc, self.base = active.document, active.l1
        self.rejects("active_configuration_requires_selection", action="delete_configuration", name="solid")
        self.rejects("unknown_configuration", action="delete_configuration", name="solid",
                     configuration="solid")
        changed = self.prepare(action="delete_configuration", name="solid", configuration="wide")
        self.witness(changed, (1200-math.pi*36)*12, "delete-to-wide")

    def test_create_replace_delete_contract_and_capacity_negative(self):
        self.rejects("configuration_exists", action="create_configuration", name="solid", definition=definition())
        self.rejects("unknown_configuration", action="replace_configuration", name="none", definition=definition())
        self.rejects("unknown_configuration", action="delete_configuration", name="none")
        self.rejects("bad_configuration_definition", action="replace_configuration",
                     name="solid", definition={"parameters": {}})
        spec = self.doc.extra["parametric"]
        spec["configurations"] = {f"c{i}": {} for i in range(32)}
        spec["configuration_suppressions"] = {name: {} for name in spec["configurations"]}
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("configuration_limit", action="create_configuration", name="extra", definition=definition())

    def test_v1_no_implicit_upgrade(self):
        spec = self.doc.extra["parametric"]
        spec["version"] = 1
        del spec["feature_bindings"], spec["configuration_suppressions"]
        self.base = docmodel.document_to_l1(self.doc)
        result = self.prepare(action="create_configuration", name="custom",
                              definition={"parameters": {"width": "50 * mm"}}, configuration="custom")
        self.witness(result, (1000-math.pi*25)*10, "v1-create")
        self.assertEqual(result.document.extra["parametric"]["version"], 1)
        self.rejects("bad_configuration_definition", action="set_suppressions",
                     name="solid", suppressions={})

    def test_overrides_retained_on_omission_and_cleared_on_explicit_selection(self):
        active = parametric.prepare_rebuild(self.doc, kernel=self.kernel, configuration="solid",
                                           overrides={"width": "55 * mm"})
        self.doc, self.base = active.document, active.l1
        result = self.prepare(action="replace_configuration", name="solid", definition=definition())
        self.assertEqual(result.parameters.overrides, {"width": "55 * mm"})
        self.witness(result, 12100, "retain-override")
        result = self.prepare(action="replace_configuration", name="solid",
                              definition=definition(), configuration="solid")
        self.assertEqual(result.parameters.overrides, {})
        self.witness(result, 10000, "clear-override")

    def test_solver_brep_and_tamper_fail_loud_atomic(self):
        from cadcore.errors import CadError
        from cadcore.sketch import commands
        fields = dict(action="create_configuration", name="custom", definition=definition(),
                      configuration="custom")
        original = copy.deepcopy(self.doc)
        dimension = self.doc.sketches[0]["dimensions"][0]
        commands.add_dimension(self.doc.sketches[0], kind=dimension["kind"],
                               refs=dimension["refs"], value_nm=1000000)
        self.base = docmodel.document_to_l1(self.doc)
        self.rejects("sketch_not_converged", **fields)
        self.doc, self.base = original, docmodel.document_to_l1(original)
        with patch.object(self.kernel, "shape_from_document",
                          side_effect=CadError("injected real boundary", reason="kernel_failure")):
            self.rejects("brep_rebuild_failed", **fields)
        prepared = self.prepare(**fields)
        prepared.document.extra["parametric"]["configuration_suppressions"]["custom"]["hole_cut"] = False
        with self.assertRaisesRegex(parametric.ParametricError, "changed"):
            prepared.verify_integrity()
        self.assertEqual(docmodel.document_to_l1(self.doc), self.base)

    def test_supported_downstream_modifier_is_rebuilt(self):
        identifier = str(uuid4())
        self.doc.features.append({"uuid": identifier, "kind": "mirror",
                                  "inputs": [self.doc.features[0]["uuid"]], "suppressed": False,
                                  "params": {"normal_axis": "x", "offset_nm": 80000000,
                                             "keep_original": False}})
        self.doc.extra["parametric"]["bindings"]["mirror_offset"] = {
            "kind": "feature", "target_uuid": identifier, "field": "offset_nm", "expression": "width * 2"}
        self.base = docmodel.document_to_l1(self.doc)
        result = self.prepare(action="create_configuration", name="custom",
                              definition=definition(), configuration="custom")
        record = self.witness(result, 10000, "downstream")
        self.assertEqual(result.document.features[-1]["uuid"], identifier)
        self.assertEqual(result.document.features[-1]["params"]["offset_nm"], 100000000)
        self.assertEqual(record["step_bbox_nm"], [150000000, 0, 0, 200000000, 20000000, 10000000])


if __name__ == "__main__":
    raise SystemExit(run_gate([GateControls, DefinitionCoreTests], label="core-definition",
                             minimum_measurements=10))
