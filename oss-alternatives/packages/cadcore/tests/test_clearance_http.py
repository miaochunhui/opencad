"""Real HTTP acceptance for read-only, composed FreeCAD clearance.

The default source is always this test's own repository root. Reading another
tree requires an explicit CLI override, without copying or editing that tree:
sudo -n -u infra python3 -B <this-file> --source-root /data/dev/infra

Only private localhost workspaces are created. No production requests, mocks,
adapter replacements or dependency skips are used. Logs, raw subprocess jobs,
source fingerprints and timed results remain in the external evidence root.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


MM = 1_000_000
EVIDENCE_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-clearance")
LOCAL_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOT = LOCAL_ROOT
_RUN_ROOT = None
_OBSERVATIONS = []
_SOURCE_BEFORE = None


def source_fingerprints():
    paths = [
        SOURCE_ROOT / "oss-alternatives/apps/opencad/server.py",
        SOURCE_ROOT / "oss-alternatives/apps/opencad/release_state.py",
        Path(__file__).resolve(),
    ]
    core = SOURCE_ROOT / "oss-alternatives/packages/cadcore/cadcore"
    # Include every feature snippet, not an assumption about snippet count.
    paths.extend(sorted((core / "kernel").glob("*.py")))
    paths.extend(core / name for name in (
        "docmodel.py", "errors.py", "ops/sw_drawings.py", "sketch/primitives.py",
    ))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def run_root():
    global _RUN_ROOT
    if _RUN_ROOT is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        _RUN_ROOT = EVIDENCE_ROOT / ("http-" + stamp)
        _RUN_ROOT.mkdir(parents=True, exist_ok=False)
        (_RUN_ROOT / "write-probe").touch(exist_ok=False)
    return _RUN_ROOT


class RealClearanceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global _SOURCE_BEFORE
        _SOURCE_BEFORE = source_fingerprints()
        root = run_root()
        app = SOURCE_ROOT / "oss-alternatives/apps/opencad"
        package = SOURCE_ROOT / "oss-alternatives/packages/cadcore"
        cls.previous_path = sys.path[:]
        cls.previous_tempdir = tempfile.tempdir
        cls.previous_env = {key: os.environ.get(key) for key in (
            "OPENCAD_CADCORE_ROOT", "CADCORE_SUBPROC_KEEP_WORK",
        )}
        cls.addClassCleanup(cls.restore_environment)
        sys.path.insert(0, str(app))
        sys.path.insert(0, str(package))
        temp = root / "kernel-jobs"
        temp.mkdir(exist_ok=False)
        tempfile.tempdir = str(temp)
        os.environ["OPENCAD_CADCORE_ROOT"] = str(package)
        os.environ["CADCORE_SUBPROC_KEEP_WORK"] = "1"

        import cadcore
        from cadcore.kernel.subproc import SubprocessKernel
        from cadcore.ops.sw_drawings import _sample_model

        if not Path(cadcore.__file__).resolve().is_relative_to(package.resolve()):
            raise AssertionError("cadcore already imported from a different source tree")
        spec = importlib.util.spec_from_file_location(
            "_opencad_clearance_http_server", app / "server.py")
        if spec is None or spec.loader is None:
            raise AssertionError("real server source is unavailable")
        cls.server_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server_module)
        cls.sample_model = staticmethod(_sample_model)
        cls.core = cls.server_module._load_cadcore()

        kernel = SubprocessKernel(work_dir=str(root / "fixture-kernel"), keep_work_dir=True)
        try:
            probe = kernel.probe_report()
            if not probe.available:
                raise AssertionError("real FreeCAD required: %s" % probe.as_dict())
            if kernel.name != "freecad":
                raise AssertionError("fixture must use FreeCAD, not another backend")
            doc = cls.sample_model()
            shape = kernel.shape_from_document(json.loads(cls.core["docmodel"].document_to_l1(doc)))
            properties = kernel.mass_properties(shape)
            if not math.isclose(properties.volume_nm3 / 1e18, 60 * 40 * 20,
                                rel_tol=1e-9):
                raise AssertionError("sample model is not the analytic 60x40x20mm solid")
            _OBSERVATIONS.append({
                "kind": "real-fixture", "probe": probe.as_dict(),
                "volume_mm3": properties.volume_nm3 / 1e18,
                "cadcore": str(Path(cadcore.__file__).resolve()),
                "server": str(app / "server.py"),
            })
        finally:
            kernel.close()

    @classmethod
    def restore_environment(cls):
        sys.path[:] = cls.previous_path
        tempfile.tempdir = cls.previous_tempdir
        for key, value in cls.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        self.output = run_root() / self._testMethodName
        self.output.mkdir(exist_ok=False)
        self.httpd = self.server_module.build_server(
            self.core, host="127.0.0.1", port=0, locale="en", standard="iso",
            out_root=str(self.output))
        self.workspace = self.httpd.workspace
        self.workspace.doc = self.sample_model()
        text = self.workspace.l1_text()
        # Nonempty histories make clearing a stack observable as well as identity.
        self.workspace.undo_stack.extend([text, text])
        self.workspace.redo_stack.append(text)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_private_server)

    def stop_private_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive(), "private HTTP server did not stop")

    def request(self, method, route, body=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=90)
        try:
            encoded = None if body is None else json.dumps(body).encode("utf-8")
            connection.request(method, route, body=encoded,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIn("application/json", response.getheader("Content-Type", ""))
            payload = json.loads(raw)
            _OBSERVATIONS.append({
                "kind": "http", "test": self._testMethodName, "method": method,
                "route": route, "request_body": (
                    None if encoded is None else encoded.decode("utf-8")),
                "http_status": response.status,
                "response": payload,
            })
            return response.status, payload
        finally:
            connection.close()

    def snapshot(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIs(state["ok"], True)
        with self.workspace._lock:
            cache_exists = hasattr(self.workspace, "_mesh_cache")
            cache = getattr(self.workspace, "_mesh_cache", None)
            return {
                "state": state, "doc_object": self.workspace.doc,
                "doc_value": copy.deepcopy(self.workspace.doc),
                "text": self.workspace.l1_text(),
                "undo_object": self.workspace.undo_stack,
                "undo_value": self.workspace.undo_stack[:],
                "redo_object": self.workspace.redo_stack,
                "redo_value": self.workspace.redo_stack[:],
                "cache_exists": cache_exists, "cache_object": cache,
                "cache_value": copy.deepcopy(cache),
                "files": {str(path.relative_to(self.output)): path.read_bytes()
                          for path in self.output.rglob("*") if path.is_file()},
            }

    def assert_unchanged(self, before):
        after = self.snapshot()
        for field in ("doc_object", "undo_object", "redo_object", "cache_object"):
            self.assertIs(after[field], before[field], field + " replaced")
        for field in ("state", "doc_value", "text", "undo_value", "redo_value",
                      "cache_exists", "cache_value", "files"):
            self.assertEqual(after[field], before[field], field + " mutated")
        _OBSERVATIONS.append({
            "kind": "read-only-verified", "test": self._testMethodName,
            "document_sha256": hashlib.sha256(before["text"].encode()).hexdigest(),
            "document_identity": True, "history_identity_and_content": True,
            "mesh_cache_identity_and_content": True, "output_files_unchanged": True,
        })

    def warm_mesh(self):
        status, result = self.request("GET", "/api/mesh")
        self.assertEqual(status, 200)
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["reason"], "ok")
        self.assertTrue(result["solids"])
        self.assertEqual(result, self.workspace._mesh_cache)

    def reading(self, vector, *, distance_nm, volume_mm3, state, warm=True):
        if warm:
            self.warm_mesh()
        before = self.snapshot()
        status, result = self.request("POST", "/api/clearance", {"translation_nm": vector})
        self.assert_unchanged(before)
        self.assertEqual(status, 200)
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["translation_nm"], vector)
        self.assertEqual(result["document_sha256"],
                         hashlib.sha256(before["text"].encode()).hexdigest())
        measurement = result["measurement"]
        self.assertIs(type(measurement["distance_nm"]), int)
        self.assertIs(type(measurement["interference_volume_nm3"]), int)
        self.assertEqual(measurement["distance_nm"], distance_nm)
        self.assertEqual(measurement["status"], state)
        self.assertAlmostEqual(measurement["interference_volume_nm3"] / 1e18,
                               volume_mm3, places=6)
        pairs = measurement["nearest_points_nm"]
        self.assertIsInstance(pairs, list)
        self.assertTrue(pairs)
        for pair in pairs:
            self.assertEqual(len(pair), 2)
            for point in pair:
                self.assertEqual(len(point), 3)
                self.assertTrue(all(type(value) is int for value in point))
            self.assertLessEqual(abs(math.dist(*pair) - distance_nm), 2)
        return measurement

    def test_separation_is_five_mm_brep_gap_not_sixty_five_mm_origin_distance(self):
        result = self.reading([65 * MM, 0, 0], distance_nm=5 * MM,
                              volume_mm3=0, state="separated")
        for left, right in result["nearest_points_nm"]:
            self.assertEqual(left[0], 60 * MM)
            self.assertEqual(right[0], 65 * MM)

    def test_contact_has_zero_distance_and_zero_interference_volume(self):
        self.reading([60 * MM, 0, 0], distance_nm=0,
                     volume_mm3=0, state="touching")

    def test_interference_has_zero_distance_and_1600_mm3_shared_volume(self):
        self.reading([58 * MM, 0, 0], distance_nm=0,
                     volume_mm3=1600, state="penetrating")

    def test_ui_circle_extrusion_axial_contact_and_interference(self):
        self.workspace.doc.features.clear()
        self.workspace.doc.sketches[0]["entities"].clear()
        for command, args in (
                ("add_circle", {"center": [0, 0], "radius_nm": 2 * MM}),
                ("extrude", {"distance_nm": 10 * MM})):
            status, result = self.request("POST", "/api/command",
                                          {"command": command, "args": args})
            self.assertEqual(status, 200)
            self.assertIs(result["ok"], True, result)
        for offset, distance, volume, state in (
                (15, 5 * MM, 0, "separated"), (10, 0, 0, "touching"),
                (5, 0, math.pi * 4 * 5, "penetrating")):
            with self.subTest(offset=offset):
                self.reading([0, 0, offset * MM], distance_nm=distance,
                             volume_mm3=volume, state=state)

    def test_negative_translation_measures_the_other_side(self):
        result = self.reading([-65 * MM, 0, 0], distance_nm=5 * MM,
                              volume_mm3=0, state="separated")
        for left, right in result["nearest_points_nm"]:
            self.assertEqual(left[0], 0)
            self.assertEqual(right[0], -5 * MM)

    def test_diagonal_3_4_5_gap_is_euclidean_surface_distance(self):
        self.reading([63 * MM, 44 * MM, 0], distance_nm=5 * MM,
                     volume_mm3=0, state="separated")

    def test_measurement_does_not_create_a_cold_mesh_cache(self):
        self.assertFalse(hasattr(self.workspace, "_mesh_cache"))
        self.reading([65 * MM, 0, 0], distance_nm=5 * MM,
                     volume_mm3=0, state="separated", warm=False)
        self.assertFalse(hasattr(self.workspace, "_mesh_cache"))

    def test_offset_is_applied_after_the_complete_pattern_feature_tree(self):
        self.workspace.doc.features.append({
            "uuid": "clearance-http-tail-pattern", "kind": "pattern", "inputs": [],
            "params": {"translations_nm": [[0, 0, 0], [70 * MM, 0, 0]], "fuse": False},
            "suppressed": False,
        })
        result = self.reading([135 * MM, 0, 0], distance_nm=5 * MM,
                              volume_mm3=0, state="separated")
        for left, right in result["nearest_points_nm"]:
            self.assertEqual(left[0], 130 * MM)
            self.assertEqual(right[0], 135 * MM)

    def test_repeated_offsets_are_fresh_and_do_not_accumulate_transforms(self):
        for offset, distance, volume, state in (
                (65, 5 * MM, 0, "separated"), (60, 0, 0, "touching"),
                (58, 0, 1600, "penetrating"), (65, 5 * MM, 0, "separated")):
            with self.subTest(offset=offset):
                self.reading([offset * MM, 0, 0], distance_nm=distance,
                             volume_mm3=volume, state=state)

    def test_bad_translation_contract_returns_400_without_state_changes(self):
        self.warm_mesh()
        for body in (
                {}, {"translations_nm": [[65 * MM, 0, 0]]},
                {"translation_nm": [65 * MM, 0, 0], "unknown": True},
                {"translation_nm": None}, {"translation_nm": "65,0,0"},
                {"translation_nm": []}, {"translation_nm": [0, 0]},
                {"translation_nm": [0, 0, 0, 0]}, {"translation_nm": [True, 0, 0]},
                {"translation_nm": [1.0, 0, 0]}, {"translation_nm": ["1", 0, 0]},
                {"translation_nm": [None, 0, 0]},
                {"translation_nm": [2**53 + 1, 0, 0]},
                {"translation_nm": [-2**53 - 1, 0, 0]},
                {"translation_nm": [float("nan"), 0, 0]},
                {"translation_nm": [float("inf"), 0, 0]}):
            with self.subTest(body=body):
                before = self.snapshot()
                status, result = self.request("POST", "/api/clearance", body)
                self.assert_unchanged(before)
                self.assertEqual(status, 400, result)
                self.assertIs(result["ok"], False)
                self.assertEqual(result["error"], "bad_arguments")
                self.assertNotIn("measurement", result)

    def test_missing_solid_is_fail_loud_not_a_zero_measurement(self):
        self.workspace.doc.features.clear()
        before = self.snapshot()
        status, result = self.request("POST", "/api/clearance", {"translation_nm": [MM, 0, 0]})
        self.assert_unchanged(before)
        self.assertEqual(status, 200)
        self.assertIs(result["ok"], False)
        self.assertIn("detail", result)
        self.assertIn("DependencyMissing", result["detail"])
        self.assertIn("document has no extrude feature", result["detail"])
        self.assertNotIn("measurement", result)

    def test_invalid_real_profile_keeps_brep_root_cause_and_existing_cache(self):
        self.warm_mesh()
        self.workspace.doc.sketches[0]["entities"].pop()
        before = self.snapshot()
        status, result = self.request(
            "POST", "/api/clearance", {"translation_nm": [65 * MM, 0, 0]})
        self.assert_unchanged(before)
        self.assertEqual(status, 200)
        self.assertIs(result["ok"], False)
        self.assertIn("rc=3", result["detail"])
        self.assertIn("valid positive-volume solid", result["detail"])
        self.assertIn("_clearance_solid", result["detail"])
        self.assertNotIn("measurement", result)


def main():
    global SOURCE_ROOT, EVIDENCE_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--evidence-root", type=Path, default=EVIDENCE_ROOT)
    args = parser.parse_args()
    SOURCE_ROOT = args.source_root.resolve()
    args.evidence_root.resolve().relative_to(Path("/hf3fs-jd/data/tmp").resolve())
    EVIDENCE_ROOT = args.evidence_root.absolute()
    root = run_root()
    logs = root / "logs"
    logs.mkdir()
    started = datetime.now(timezone.utc).isoformat()
    wall = time.monotonic()
    log_path = logs / "tests.log"
    with log_path.open("x", encoding="utf-8") as log:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealClearanceHTTPTests)
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        after = source_fingerprints()
        drift = sorted(path for path in set(_SOURCE_BEFORE or {}) | set(after)
                       if (_SOURCE_BEFORE or {}).get(path) != after.get(path))
        verdict = ("PASS" if result.wasSuccessful() and result.testsRun > 0
                   and not result.skipped and not drift else "FAIL")
        ended = datetime.now(timezone.utc).isoformat()
        line = ("CLEARANCE_HTTP verdict=%s time=%s tests=%d failed=%d errors=%d "
                "skipped=%d source_drift=%d" % (
                    verdict, ended, result.testsRun, len(result.failures),
                    len(result.errors), len(result.skipped), len(drift)))
        log.write("\n" + line + "\n")
    report = {
        "verdict": verdict, "time": ended, "started_at": started,
        "wall_s": time.monotonic() - wall, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "uid": os.getuid(), "log": str(log_path),
        "failure_details": [{"test": str(test), "traceback": detail}
                            for test, detail in result.failures + result.errors],
        "source_root": str(SOURCE_ROOT), "sources_before": _SOURCE_BEFORE,
        "sources_after": after, "source_drift": drift,
        "source_unchanged": not drift, "observations": _OBSERVATIONS,
        "command": ["sudo", "-n", "-u", "infra", "python3", "-B",
                    str(Path(__file__).resolve()), "--source-root", str(SOURCE_ROOT),
                    "--evidence-root", str(EVIDENCE_ROOT)],
    }
    (root / "result.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(line)
    print("evidence=" + str(root))
    if verdict != "PASS":
        print(log_path.read_text(encoding="utf-8"))
        print("source_drift=" + repr(drift))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
