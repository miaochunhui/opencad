#!/usr/bin/env python3
"""Real local secure CLI, SQLite, Chromium and FreeCAD configuration UI gate.

Run with --evidence-dir /hf3fs-jd/data/tmp/<fresh> --runtime-root /data/tmp/<short>.
--selftest exercises receipt/source/artifact negative controls without a browser.
--verify-evidence performs only local readback; it never starts a service.
All account secrets are generated in memory and omitted from CDP/HTTP records.
"""

from __future__ import annotations

import argparse
import base64
import copy
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "oss-alternatives/apps/opencad"
OWNED = (
    "oss-alternatives/apps/opencad/web/js/parametric-panel.js",
    "oss-alternatives/apps/opencad/web/css/parametric-panel.css",
    "oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py",
    "oss-alternatives/apps/opencad/docs/design/parametric-config-web-ui.md",
)
MARKER = "OPENCAD_PARAMETRIC_CONFIG_BROWSER"
DEFAULT_BROWSER = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"
WORKFLOWS = {
    "loaded_owned_ui", "create_v2_real_geometry", "draft_background_preserved",
    "draft_discard_cancel_preserved",
    "simplified_panel_locale",
    "suppression_preview_not_commit", "solid_complete_rebuild", "explicit_override",
    "clear_override", "base_restore", "save_receipt", "undo_geometry",
    "redo_geometry", "cli_restart_persistence", "real_step_download",
    "rejected_expression_atomic", "lost_response_draft_preserved",
    "receipt_replay_single_commit", "late_read_positive",
    "late_read_after_logout_shielded", "second_account_isolated",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, cause):
    if not condition:
        raise AssertionError(cause)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write(path, value):
    data = value if isinstance(value, bytes) else (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "module_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source(root):
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {
            ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"})
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "linked_source_directory")
        for name in sorted(names):
            if name == ".git":
                continue
            path = Path(directory) / name
            require(path.is_file() and not path.is_symlink(), "nonregular_source")
            data = path.read_bytes()
            files[path.relative_to(root).as_posix()] = {
                "sha256": digest(data), "bytes": len(data), "mode": path.stat().st_mode & 0o777}
    require(set(OWNED) <= set(files), "owned_source_missing")
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, check=True).stdout.decode().strip()
    status = subprocess.run(["git", "--no-optional-locks", "-C", str(root),
                             "status", "--porcelain=v1"], capture_output=True,
                            check=True).stdout.decode().splitlines()
    return {"root": str(root), "head": head, "files": files,
            "sha256": digest(canonical(files)), "status": status,
            "owned": {name: files[name] for name in OWNED}}


def validate_receipt(state, mesh):
    require(state["project_id"] == mesh["project_id"] and
            state["revision"] == mesh["revision"] and state["sha256"] == mesh["sha256"],
            "geometry_receipt_mismatch")
    require(digest(state["doc"].encode()) == mesh["document_sha256"],
            "geometry_document_mismatch")


def validate_download(data, witness, progress):
    require(progress is not None and progress["state"] == "completed"
            and progress["receivedBytes"] == progress["totalBytes"] == len(data)
            and len(data) == witness["bytes"] and len(data) > 0
            and digest(data) == witness["sha256"] and data.startswith(b"ISO-10303-21;"),
            "download_not_complete_or_bound")


def mesh_volume(solid):
    vertices = solid["vertices_mm"]
    require(vertices and solid["triangles"], "empty_mesh")
    volume, edges = 0.0, {}
    for triangle in solid["triangles"]:
        require(len(triangle) == 3 and all(type(i) is int and 0 <= i < len(vertices)
                                         for i in triangle), "invalid_triangle")
        a, b, c = triangle
        x, y, z = vertices[a], vertices[b], vertices[c]
        require(all(len(v) == 3 and all(type(n) in (float, int) and math.isfinite(n)
                                       for n in v) for v in (x, y, z)), "invalid_vertex")
        volume += (x[0] * (y[1]*z[2]-y[2]*z[1]) +
                   x[1] * (y[2]*z[0]-y[0]*z[2]) +
                   x[2] * (y[0]*z[1]-y[1]*z[0])) / 6
        for i, j in ((a, b), (b, c), (c, a)):
            edge = tuple(sorted((i, j)))
            edges[edge] = edges.get(edge, 0) + 1
    require(all(value == 2 for value in edges.values()), "open_mesh")
    return abs(volume)


def validate_geometry_witness(witness):
    validate_receipt(witness["state"], witness["mesh"])
    expected = witness["expected_mm3"]
    require(type(expected) in (int, float) and math.isfinite(expected) and expected > 0,
            "invalid_expected_volume")
    require(len(witness["mesh"]["solids"]) == 1, "unexpected_solid_count")
    solid = witness["mesh"]["solids"][0]
    actual = solid["properties"]["volume_nm3"] / 1e18
    integrated = mesh_volume(solid)
    require(abs(actual-expected) <= 1e-5 and abs(integrated-expected) <= expected*.005
            and actual == witness["brep_mm3"] and integrated == witness["mesh_mm3"],
            "real_geometry_volume_mismatch")
    displayed = witness["display"]
    require(displayed["doc"] == witness["mesh"]["document_sha256"] and
            displayed["triangles"] == len(solid["triangles"]) and
            abs(displayed["volume"]-expected) <= 1e-5, "display_version_mismatch")


def artifact_names():
    names = {"checks.log", *(f"cli-{number}.log" for number in range(1, 5))}
    for width in (1440, 390):
        prefix = str(width) + "/"
        names.update(prefix + name for name in (
            "browser.log", "preflight.json", "cdp-events.json",
            "pierced.png", "solid.png", "restored.png", "expired.png",
            "pierced.json", "solid.json", "override.json", "clear.json",
            "base.json", "undo.json", "redo.json", "restart.json",
            "download-witness.json", "downloads/opencad.step"))
    return names


def validate_artifact(path, record):
    require(not path.is_symlink() and path.is_file(), "artifact_not_regular")
    data = path.read_bytes()
    require(digest(data) == record["sha256"] and len(data) == record["bytes"],
            "artifact_hash_mismatch")
    return data


def terminal(report):
    require(report["verdict"] == "PASS" and report["intended_rc"] == 0
            and report["first_cause"] is None and not report["cleanup_errors"],
            "producer_failure")
    require(report["source_before"] == report["source_after"] and not report["source_drift"],
            "source_drift")
    expected = {f"{width}-{name}" for width in (1440, 390) for name in WORKFLOWS}
    checks = report["checks"]
    require({item["name"] for item in checks} == expected and len(checks) == len(expected)
            and all(item["pass"] is True for item in checks), "workflow_ledger_mismatch")
    require(len(report["cli"]) == 4 and all(
        item["ready"] is True and item["argv"] == item["observed_argv"]
        and item["exit_rc"] == 0 and item["startticks"] == item["stop_startticks"]
        and type(item["startticks"]) is int and item["startticks"] > 0
        for item in report["cli"]), "original_cli_mismatch")
    return len(checks)


def validate_artifact_set(actual, manifest):
    require(actual == set(manifest) and artifact_names() <= actual, "artifact_set_mismatch")


def validate_bound_report(report, root, out, *, check_current=True):
    count = terminal(report)
    if check_current:
        require(source(root) == report["source_after"], "current_source_drift")
    require(report["source_after"]["root"] == str(root), "source_root_mismatch")
    require(set(OWNED) <= set(report["source_after"]["files"])
            and report["source_after"]["owned"] == {
                name: report["source_after"]["files"][name] for name in OWNED}, "owned_source_mismatch")
    require(digest(canonical(report["source_after"]["files"])) ==
            report["source_after"]["sha256"], "source_manifest_hash_mismatch")
    actual = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
              and p.relative_to(out).as_posix() not in {
                  "result.json", "completion.log", "terminal-candidate.json"}}
    validate_artifact_set(actual, report["artifacts"])
    for name, record in report["artifacts"].items():
        path = out / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts
                and not path.is_symlink(), "artifact_escape")
        validate_artifact(path, record)
    require([json.loads(line) for line in (out / "checks.log").read_text().splitlines()]
            == report["checks"], "check_log_mismatch")
    for number, cli in enumerate(report["cli"], 1):
        argv = [sys.executable, "-B", str(root / "oss-alternatives/apps/opencad/secure_web.py"),
                "--data-root", report["runtime_root"], "--host", "127.0.0.1",
                "--port", str(cli["port"]), "--public-origin", cli["origin"]]
        require(cli["argv"] == argv and cli["origin"] == f"http://127.0.0.1:{cli['port']}"
                and cli["log"] == f"cli-{number}.log",
                "cli_scope_mismatch")
        require(f"OPENCAD_PRIVATE_WEB verdict=READY port={cli['port']}\n".encode()
                in (out / cli["log"]).read_bytes(), "cli_ready_marker_missing")
    for width in (1440, 390):
        prefix = str(width) + "/"
        for name in ("pierced", "solid", "restored", "expired"):
            data = (out / prefix / (name + ".png")).read_bytes()
            require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 1000,
                    "invalid_screenshot")
        for name in ("pierced", "solid", "override", "clear", "base", "undo", "redo", "restart"):
            witness = json.loads((out / prefix / (name + ".json")).read_bytes())
            validate_geometry_witness(witness)
        witness = json.loads((out / prefix / "download-witness.json").read_text())
        step = (out / prefix / "downloads/opencad.step").read_bytes()
        validate_download(step, witness, witness["completion"])
    return count


def validate_marker(marker, count):
    require(marker == f"{MARKER} verdict=PASS checks={count} rc=0\n",
            "completion_marker_mismatch")


def verify(report, root, out):
    count = validate_bound_report(report, root, out)
    candidate = json.loads((out / "terminal-candidate.json").read_bytes())
    require(candidate == report, "terminal_candidate_mismatch")
    validate_marker((out / "completion.log").read_text(), count)
    return count


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_cause = None

    def remember(self, test, error):
        if self.first_cause is None:
            self.first_cause = self._exc_info_to_string(error, test)

    def addError(self, test, error):
        self.remember(test, error)
        super().addError(test, error)

    def addFailure(self, test, error):
        self.remember(test, error)
        super().addFailure(test, error)

    def addSubTest(self, test, subtest, error):
        if error is not None:
            self.remember(subtest, error)
        super().addSubTest(test, subtest, error)


def bind_control_runtime(suite, runtime):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            bind_control_runtime(test, runtime)
        else:
            test.runtime_root = runtime


class Controls(unittest.TestCase):
    def setUp(self):
        self.valid = {"verdict": "PASS", "intended_rc": 0, "first_cause": None,
                      "cleanup_errors": [], "source_before": {}, "source_after": {},
                      "source_drift": [], "checks": [
                          {"name": f"{width}-{name}", "pass": True}
                          for width in (1440, 390) for name in sorted(WORKFLOWS)],
                      "cli": [{"ready": True, "argv": ["python", "-B"],
                               "observed_argv": ["python", "-B"], "exit_rc": 0,
                               "startticks": 42, "stop_startticks": 42} for _ in range(4)]}

    def test_terminal_positive(self):
        self.assertEqual(terminal(self.valid), 2 * len(WORKFLOWS))

    def test_receipt_positive(self):
        text = '{"features":[]}'
        validate_receipt({"project_id": "a", "revision": 2, "sha256": "b", "doc": text},
                         {"project_id": "a", "revision": 2, "sha256": "b",
                          "document_sha256": digest(text.encode())})

    def test_geometry_project_revision_sha_and_document_negative(self):
        for key, changed in (("project_id", "other"), ("revision", 3),
                             ("sha256", "changed"), ("document_sha256", "changed")):
            with self.subTest(key=key):
                mesh = {"project_id": "a", "revision": 2, "sha256": "b",
                        "document_sha256": digest(b"{}")}
                mesh[key] = changed
                with self.assertRaises(AssertionError):
                    validate_receipt({"project_id": "a", "revision": 2,
                                      "sha256": "b", "doc": "{}"}, mesh)

    def test_empty_ledger_rejected(self):
        report = {"verdict": "PASS", "intended_rc": 0, "first_cause": None,
                  "cleanup_errors": [], "source_before": {}, "source_after": {},
                  "source_drift": [], "checks": []}
        with self.assertRaisesRegex(AssertionError, "workflow_ledger"):
            terminal(report)

    def test_head_drift_rejected(self):
        report = {"verdict": "PASS", "intended_rc": 0, "first_cause": None,
                  "cleanup_errors": [], "source_before": {"head": "a"},
                  "source_after": {"head": "b"}, "source_drift": []}
        with self.assertRaisesRegex(AssertionError, "source_drift"):
            terminal(report)

    def test_first_cause_cannot_be_pass(self):
        report = {"verdict": "PASS", "intended_rc": 0, "first_cause": "original failure",
                  "cleanup_errors": []}
        with self.assertRaisesRegex(AssertionError, "producer_failure"):
            terminal(report)

    def test_missing_duplicate_failed_renamed_checks_rejected(self):
        for kind in ("missing", "duplicate", "failed", "renamed"):
            with self.subTest(kind=kind):
                report = copy.deepcopy(self.valid)
                if kind == "missing":
                    report["checks"].pop()
                elif kind == "duplicate":
                    report["checks"][-1] = report["checks"][0]
                elif kind == "failed":
                    report["checks"][0]["pass"] = False
                else:
                    report["checks"][0]["name"] = "unrelated"
                with self.assertRaisesRegex(AssertionError, "workflow_ledger"):
                    terminal(report)

    def test_cli_argv_pid_generation_exit_negative(self):
        for key, changed in (("observed_argv", ["other"]), ("exit_rc", -9),
                             ("stop_startticks", 43), ("startticks", 0), ("ready", False)):
            with self.subTest(key=key):
                report = copy.deepcopy(self.valid)
                report["cli"][0][key] = changed
                with self.assertRaisesRegex(AssertionError, "original_cli"):
                    terminal(report)

    def test_source_bytes_modes_status_negative(self):
        for key in ("files", "status"):
            with self.subTest(key=key):
                report = copy.deepcopy(self.valid)
                report["source_after"][key] = {"mode": 0} if key == "files" else ["changed"]
                with self.assertRaisesRegex(AssertionError, "source_drift"):
                    terminal(report)

    def test_missing_required_artifact_negative(self):
        expected = artifact_names()
        validate_artifact_set(expected, {name: {} for name in expected})
        for name in sorted(expected):
            with self.subTest(name=name):
                incomplete = expected - {name}
                with self.assertRaisesRegex(AssertionError, "artifact_set"):
                    validate_artifact_set(incomplete, {key: {} for key in incomplete})

    def test_artifact_hash_size_positive_negative(self):
        root = getattr(self, "runtime_root", None)
        if root is None:
            declared = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
            require(declared is not None, "discovery_local_runtime_required")
            root = Path(declared)
            require(root.absolute().is_relative_to(Path("/data/tmp")) and root.is_dir(),
                    "discovery_local_runtime_invalid")
        with tempfile.TemporaryDirectory(prefix="config-ui-control-", dir=root) as directory:
            path = Path(directory) / "synthetic"
            write(path, b"fixture")
            self.assertEqual(validate_artifact(path, {"sha256": digest(b"fixture"), "bytes": 7}),
                             b"fixture")
            for record in ({"sha256": digest(b"changed"), "bytes": 7},
                           {"sha256": digest(b"fixture"), "bytes": 6}):
                with self.subTest(record=record):
                    with self.assertRaisesRegex(AssertionError, "artifact_hash"):
                        validate_artifact(path, record)

    def test_completion_marker_negative(self):
        count = 2 * len(WORKFLOWS)
        validate_marker(f"{MARKER} verdict=PASS checks={count} rc=0\n", count)
        for marker in ("", f"{MARKER} verdict=PASS checks=0 rc=0\n",
                       f"{MARKER} verdict=PASS checks={count} rc=1\n",
                       f"{MARKER} verdict=RUNNING checks={count} rc=0\n"):
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(AssertionError, "completion_marker"):
                    validate_marker(marker, count)

    def test_selftest_discovery_collects_exact_suite(self):
        discovered = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(discovered.countTestCases(),
                         unittest.defaultTestLoader.loadTestsFromTestCase(Controls).countTestCases())

    def test_first_error_precedes_later_failure(self):
        from io import StringIO

        class FirstError(unittest.TestCase):
            def runTest(self):
                raise RuntimeError("first-error-witness")

        class LaterFailure(unittest.TestCase):
            def runTest(self):
                self.fail("later-failure-witness")

        result = unittest.TextTestRunner(stream=StringIO(), resultclass=EvidenceResult).run(
            unittest.TestSuite([FirstError(), LaterFailure()]))
        self.assertEqual((len(result.errors), len(result.failures)), (1, 1))
        self.assertIn("first-error-witness", result.first_cause)
        self.assertNotIn("later-failure-witness", result.first_cause)

    def test_incomplete_download_rejected(self):
        data = b"ISO-10303-21;\nfixture"
        witness = {"bytes": len(data), "sha256": digest(data)}
        complete = {"state": "completed", "receivedBytes": len(data), "totalBytes": len(data)}
        validate_download(data, witness, complete)
        for downloaded, progress in ((b"", complete), (data, None),
                (data, {**complete, "state": "inProgress"}),
                (data, {**complete, "receivedBytes": 0}), (data + b"changed", complete)):
            with self.subTest(progress=progress):
                with self.assertRaisesRegex(AssertionError, "download_not_complete"):
                    validate_download(downloaded, witness, progress)


class Review:
    def __init__(self, root, out, runtime, binary):
        self.root, self.out, self.runtime, self.binary = root, out, runtime, binary
        self.app = root / "oss-alternatives/apps/opencad"
        self.checks, self.cli, self.cleanup_errors, self.secrets = [], [], [], []
        self.process = None
        self.log = (out / "checks.log").open("x", encoding="utf-8")
        sys.path[:0] = [str(self.app), str(root / "oss-alternatives/packages/cadcore")]
        from access_control import AuthController
        self.Auth = AuthController
        self.accounts = {}

    def check(self, name, condition, **facts):
        item = {"name": name, "pass": bool(condition), "at": now(), **facts}
        self.checks.append(item)
        self.log.write(json.dumps(item, sort_keys=True) + "\n")
        self.log.flush()
        os.fsync(self.log.fileno())
        print(f"CONFIG_UI_CHECK name={name} pass={bool(condition)}", flush=True)
        require(condition, name)

    def register(self):
        with closing(self.Auth(self.runtime / "accounts", allow_registration=True)) as auth:
            for name in ("config-alice", "config-bob"):
                password = uuid.uuid4().hex
                self.accounts[name] = {**auth.register(name, password), "password": password}
                self.secrets.append(password.encode())

    def start(self, port=None):
        if port is None:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        argv = [sys.executable, "-B", str(self.app / "secure_web.py"), "--data-root",
                str(self.runtime), "--host", "127.0.0.1", "--port", str(port),
                "--public-origin", self.origin]
        log_name = f"cli-{len(self.cli) + 1}.log"
        self.cli_log = (self.out / log_name).open("xb")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   OPENCAD_TEST_RUNTIME_ROOT=str(self.runtime), TMPDIR=str(self.runtime))
        for key in ("PYTHONPATH", "OPENCAD_CADCORE_ROOT", "OPENCAD_I18N_DIRS",
                    "CADCORE_PDF_BACKEND", "CADCORE_SUBPROC_EXECUTABLE"):
            env.pop(key, None)
        self.process = subprocess.Popen(argv, cwd=self.root, env=env,
                                        stdout=subprocess.PIPE, stderr=self.cli_log)
        ready, _, _ = select.select([self.process.stdout], [], [], 20)
        require(ready, "cli_readiness_timeout")
        line = self.process.stdout.readline()
        self.cli_log.write(line)
        require(line == f"OPENCAD_PRIVATE_WEB verdict=READY port={port}\n".encode(),
                "cli_readiness_marker")
        proc = Path("/proc") / str(self.process.pid)
        fields = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()
        observed = [p.decode() for p in proc.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0")]
        record = {"argv": argv, "observed_argv": observed, "pid": self.process.pid,
                  "startticks": int(fields[19]), "ready": True, "ready_at": now(),
                  "exit_rc": None, "port": port, "origin": self.origin, "log": log_name}
        self.cli.append(record)
        require(argv == observed, "cli_argv_mismatch_after_ready")

    def stop(self):
        if self.process is None:
            return
        fields = (Path("/proc") / str(self.process.pid) / "stat").read_text().rsplit(")", 1)[1].split()
        self.cli[-1]["stop_startticks"] = int(fields[19])
        self.cli[-1]["shutdown_started_at"] = now()
        self.cli[-1]["shutdown_bound_seconds"] = 18
        self.process.terminate()
        # HTTP/1.1 handlers have the source-defined 15 second idle timeout.
        # Observe that drain, not a shorter harness deadline or an unbounded wait.
        deadline = time.monotonic() + 18
        observations = []
        while self.process.poll() is None and time.monotonic() < deadline:
            observations.append({"at": now(), "threads": len(list(
                (Path("/proc") / str(self.process.pid) / "task").iterdir()))})
            time.sleep(.1)
        if self.process.poll() is None:
            self.process.kill()
            self.cleanup_errors.append("cli_required_sigkill")
        code = self.process.wait(timeout=5)
        self.cli[-1]["shutdown_observations"] = observations
        self.cli_log.write(self.process.stdout.read())
        self.cli_log.close()
        self.process.stdout.close()
        self.process = None
        self.cli[-1].update(exit_rc=code, finished_at=now())
        require(code == 0, "original_cli_exit_nonzero")

    def http(self, method, path, cookie=None, project=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.cli[-1]["port"], timeout=60)
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if cookie:
            headers["Cookie"] = "opencad_session=" + cookie
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            data = response.read()
            return response.status, (json.loads(data) if response.getheader(
                "Content-Type", "").startswith("application/json") else data)
        finally:
            conn.close()

    def browser_class(self):
        module = load("config_ui_controls", self.root /
                      "oss-alternatives/tools/cad3d/viewer_controls_check.py")
        review = self

        class Browser(module.BrowserSession):
            def __enter__(self):
                self.download_progress = None
                self.out.mkdir()
                self.directory = review.runtime / ("b-" + uuid.uuid4().hex[:8])
                self.directory.mkdir()
                socket_path = self.directory / "org.chromium.Chromium.XXXXXX/SingletonSocket"
                write(self.out / "preflight.json", {"path": str(socket_path),
                      "bytes": len(os.fsencode(socket_path)), "limit": 107})
                require(len(os.fsencode(socket_path)) <= 107, "owned_browser_socket_path_too_long")
                self.log = (self.out / "browser.log").open("x")
                try:
                    self.proc = subprocess.Popen([
                        self.browser, "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                        "--remote-debugging-port=0", "--user-data-dir=" + str(self.directory),
                        "--no-first-run", "--no-default-browser-check",
                        "--disable-background-networking", "--disable-component-update",
                        "--disable-sync", "--disable-breakpad", "--disable-crash-reporter",
                        "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank",
                    ], env=dict(os.environ, HOME=str(self.directory), TMPDIR=str(self.directory),
                                XDG_CONFIG_HOME=str(self.directory / "config"),
                                XDG_CACHE_HOME=str(self.directory / "cache")),
                        stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
                    active = self.directory / "DevToolsActivePort"
                    deadline = time.monotonic() + 20
                    while not active.is_file():
                        require(self.proc.poll() is None and time.monotonic() < deadline,
                                "owned_browser_startup_failed")
                        time.sleep(.1)
                    with urllib.request.urlopen("http://127.0.0.1:" +
                            active.read_text().splitlines()[0] + "/json/list", timeout=10) as response:
                        tabs = json.load(response)
                    self.helper = load("config_ui_cdp", self.app / "tests/legacy_browser_compat_gate.py")
                    self.ws = self.helper._WSClient(next(tab for tab in tabs if tab["type"] == "page")[
                        "webSocketDebuggerUrl"])
                    self.call("Runtime.enable", {})
                    self.call("Page.enable", {})
                    self.call("Emulation.setDeviceMetricsOverride", {
                        "width": self.width, "height": self.height,
                        "deviceScaleFactor": 1, "mobile": self.width < 650})
                    return self
                except BaseException:
                    self.__exit__(*sys.exc_info())
                    raise

            def call(self, method, params):
                self.sequence["id"] += 1
                identifier = self.sequence["id"]
                self.ws.send_text(json.dumps({"id": identifier, "method": method, "params": params}))
                deadline = time.monotonic() + 60
                while True:
                    require(time.monotonic() < deadline, "cdp_response_timeout:" + method)
                    response = json.loads(self.ws.recv_text())
                    if response.get("method") == "Browser.downloadProgress":
                        self.download_progress = response["params"]
                    if response.get("id") == identifier:
                        break
                self.events.append({"method": method, "ok": "error" not in response})
                if "error" in response or "exceptionDetails" in response.get("result", {}):
                    raise RuntimeError("browser_operation_failed:" + method)
                return response["result"]

            def screenshot(self, name):
                layout = self.call("Page.getLayoutMetrics", {})["cssContentSize"]
                require(0 < layout["width"] <= 20000 and 0 < layout["height"] <= 20000,
                        "screenshot_layout_invalid")
                image = self.call("Page.captureScreenshot", {
                    "format": "png", "captureBeyondViewport": True,
                    "clip": {**layout, "scale": 1}})
                path = self.out / (name + ".png")
                data = base64.b64decode(image["data"])
                require(data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 1000,
                        "empty_screenshot")
                write(path, data)
                return path

            def __exit__(self, kind, exc, tb):
                if kind is not None and self.ws is not None:
                    try:
                        self.screenshot("first-failure")
                        write(self.out / "failure-dom.json", self.evaluate(
                            "({notice:document.querySelector('#parametric-panel .parametric-notice')"
                            "?.textContent,session:document.querySelector('.project-status output')"
                            "?.textContent,panel:document.querySelector('#parametric-panel')?.innerText,"
                            "triangles:window.opencad3d?.triangles})"))
                    except Exception as error:
                        review.cleanup_errors.append("failure_capture:" + type(error).__name__)
                if self.ws is not None:
                    self.ws.sock.close()
                    self.ws = None
                if self.proc is not None:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                        self.proc.wait(timeout=5)
                    self.proc = None
                if self.log is not None:
                    self.log.close()
                    self.log = None
                write(self.out / "cdp-events.json", self.events)

        return Browser

    @staticmethod
    def type(browser, selector, value):
        browser.click(selector)
        browser.key("a", modifiers=2)
        browser.call("Input.insertText", {"text": value})

    def login(self, browser, name, project=None):
        browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
        self.type(browser, "dialog[open] input[name=username]", name)
        self.type(browser, "dialog[open] input[name=password]", self.accounts[name]["password"])
        browser.click("dialog[open] button[type=submit]")
        browser.wait("!!document.querySelector('dialog[open] select')")
        if project:
            browser.click("dialog[open] button")
        else:
            self.type(browser, "[aria-label='新项目名称']", "配置验收 " + str(browser.width))
            browser.click("dialog[open] button:nth-of-type(2)")
        browser.wait("!document.querySelector('dialog')")
        browser.evaluate("document.querySelector('[data-parametric-host]').closest('details').open=true")
        browser.wait("!!document.querySelector('[data-parametric-create="
                     "\"create_configurable_plate\"]:not([hidden])') && "
                     "!document.querySelector('[data-parametric-create="
                     "\"create_configurable_plate\"]').disabled && "
                     "!!document.querySelector('#parametric-panel').dataset.projectId")

    def cookie(self, browser):
        cookie = next(item for item in browser.call("Network.getCookies", {"urls": [self.origin]})[
            "cookies"] if item["name"] == "opencad_session")
        self.secrets.append(cookie["value"].encode())
        return cookie["value"]

    @staticmethod
    def choose_configuration(browser, index):
        browser.click('[aria-label="参数配置"]')
        for key in ["Home", *(["ArrowDown"] * index), "Enter"]:
            for kind in ("keyDown", "keyUp"):
                browser.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": key,
                             "windowsVirtualKeyCode": {"Home": 36, "ArrowDown": 40, "Enter": 13}[key]})

    @staticmethod
    def discard(browser, *, accept):
        browser.click("[data-parametric-discard]")
        browser.wait("!document.querySelector('.parametric-discard-confirmation').hidden")
        browser.click("[data-parametric-confirm-discard]" if accept else
                      "[data-parametric-cancel-discard]")

    def state(self, cookie, project):
        code, value = self.http("GET", "/api/state", cookie, project)
        require(code == 200 and value["ok"] is True, "actor_state_failed")
        return value

    def geometry(self, browser, cookie, project, expected, label):
        state = self.state(cookie, project)
        code, mesh = self.http("GET", "/api/mesh", cookie, project)
        require(code == 200, "actor_mesh_failed")
        validate_receipt(state, mesh)
        solid = mesh["solids"][0]
        volume = solid["properties"]["volume_nm3"] / 1e18
        integrated = mesh_volume(solid)
        require(abs(volume-expected) <= 1e-5 and abs(integrated-expected) <= expected*.005,
                "real_geometry_volume_mismatch")
        browser.wait("!window.opencad3d.busy && window.opencad3d.triangles>0")
        displayed = browser.evaluate("({doc:window.opencad3d.document_sha256,"
                                     "volume:window.opencad3d.volume_mm3,triangles:window.opencad3d.triangles})")
        require(displayed["doc"] == mesh["document_sha256"] and displayed["triangles"] > 0
                and abs(displayed["volume"]-expected) <= 1e-5, "display_version_mismatch")
        witness = {"state": state, "mesh": mesh, "brep_mm3": volume,
                   "mesh_mm3": integrated, "expected_mm3": expected, "display": displayed}
        validate_geometry_witness(witness)
        write(browser.out / (label + ".json"), witness)
        return state

    def rebuilt(self, browser, after_revision):
        browser.wait("document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'"
                     " && Number(document.querySelector('.parametric-result').dataset.revision)==="
                     + str(after_revision + 1), timeout=60)
        browser.wait("document.querySelector('.parametric-result').dataset.documentSha === "
                     "window.opencad3d.document_sha256 && !window.opencad3d.busy", timeout=60)

    def scenario(self, width, height):
        Browser = self.browser_class()
        with Browser(self.binary, self.out / str(width), width=width, height=height,
                     source_root=self.root) as browser:
            prefix = str(width) + "-"
            browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                "const transport=window.fetch.bind(window);window.fetch=async function(input,opt){"
                "const r=await transport(input,opt);const p=new URL(typeof input==='string'?input:"
                "input.url,location.href).pathname;"
                "if(p==='/api/parametric'&&(!opt||!opt.method||opt.method==='GET')&&window.__hold){"
                "window.__hold=false;window.__held=true;await new Promise(resolve=>window.__release=resolve);}"
                "if(p==='/api/parametric'&&opt?.method==='POST'&&window.__lose){"
                "const v=await r.clone().json();if(!r.ok||v.ok!==true)throw Error('loss_fixture_rejected');"
                "window.__lose=false;throw Error('Injected lost response');}"
                "if(p==='/api/step'&&r.ok){const b=await r.clone().arrayBuffer();"
                "const h=await crypto.subtle.digest('SHA-256',b);window.__stepWitness={bytes:b.byteLength,"
                "sha256:Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join('')};}"
                "return r;};"})
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            self.login(browser, "config-alice")
            cookie = self.cookie(browser)
            state = browser.evaluate("fetch('/api/state').then(r=>r.json())")
            project = state["project_id"]
            browser.wait("!!document.querySelector('#locale option[value=\"zh-CN\"]')")
            locale_index = browser.evaluate("Array.from(document.querySelector('#locale').options)"
                                            ".findIndex(o=>o.value==='zh-CN')")
            require(type(locale_index) is int and locale_index >= 0, "simplified_locale_missing")
            browser.click("#locale")
            for key in ["Home", *(["ArrowDown"] * locale_index), "Enter"]:
                for kind in ("keyDown", "keyUp"):
                    browser.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": key,
                                 "windowsVirtualKeyCode": {"Home": 36, "ArrowDown": 40, "Enter": 13}[key]})
            browser.wait("document.documentElement.lang==='zh-CN'", timeout=30)
            self.check(prefix+"simplified_panel_locale", browser.evaluate(
                "document.querySelector('#parametric-panel h2').textContent==='参数化配置' && "
                "Array.from(document.querySelectorAll('#parametric-panel button')).some("
                "b=>b.textContent==='确认保存当前模型') && "
                "!/[參數創儲圖體積\\u3040-\\u30ff]/.test("
                "document.querySelector('#parametric-panel').innerText)"))
            code, loaded = self.http("GET", "/web/js/parametric-panel.js")
            self.check(prefix+"loaded_owned_ui", code == 200 and digest(loaded) ==
                       digest((self.app / "web/js/parametric-panel.js").read_bytes()))
            browser.click('[data-parametric-create="create_configurable_plate"]')
            self.rebuilt(browser, state["revision"])
            pierced_volume = (800-math.pi*16)*8
            pierced = self.geometry(browser, cookie, project, pierced_volume, "pierced")
            self.check(prefix+"create_v2_real_geometry", json.loads(pierced["doc"])["parametric"]["version"] == 2)
            browser.screenshot("pierced")
            self.type(browser, "[data-parameter=width]", "55 * mm")
            browser.evaluate("window.opencad3d.refresh()")
            self.check(prefix+"draft_background_preserved", browser.evaluate(
                "document.querySelector('[data-parameter=width]').value==='55 * mm'"))
            self.discard(browser, accept=False)
            self.check(prefix+"draft_discard_cancel_preserved", browser.evaluate(
                "document.querySelector('[data-parameter=width]').value==='55 * mm'") and
                       self.state(cookie, project)["revision"] == pierced["revision"])
            self.choose_configuration(browser, 3)
            self.check(prefix+"suppression_preview_not_commit", browser.evaluate(
                "document.querySelector('[data-feature=hole_cut]').dataset.suppressed==='true'") and
                       self.state(cookie, project)["revision"] == pierced["revision"])
            # Reset the draft explicitly before selecting the named 60 mm configuration.
            self.type(browser, "[data-parameter=width]", "40 * mm")
            browser.click("[data-parametric-rebuild]")
            self.rebuilt(browser, pierced["revision"])
            solid = self.geometry(browser, cookie, project, 14400, "solid")
            self.check(prefix+"solid_complete_rebuild", json.loads(solid["doc"])["features"][1]["suppressed"])
            browser.screenshot("solid")
            self.type(browser, "[data-parameter=width]", "50 * mm")
            browser.click("[data-parametric-rebuild]")
            self.rebuilt(browser, solid["revision"])
            overridden = self.geometry(browser, cookie, project, 10000, "override")
            self.check(prefix+"explicit_override", json.loads(overridden["doc"])["parametric"][
                "active_overrides"]["width"] == "50 * mm")
            browser.click("[data-parametric-reset-overrides]")
            browser.click("[data-parametric-rebuild]")
            self.rebuilt(browser, overridden["revision"])
            cleared = self.geometry(browser, cookie, project, 14400, "clear")
            self.check(prefix+"clear_override", json.loads(cleared["doc"])["parametric"]["active_overrides"] == {})
            self.choose_configuration(browser, 0)
            browser.click("[data-parametric-rebuild]")
            self.rebuilt(browser, cleared["revision"])
            restored = self.geometry(browser, cookie, project, pierced_volume, "base")
            self.check(prefix+"base_restore", restored["doc"] == pierced["doc"])
            browser.click("[data-parametric-save]")
            browser.wait("document.querySelector('#parametric-panel').dataset.revision==="
                         + json.dumps(str(restored["revision"] + 1)) +
                         " && document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'",
                         timeout=60)
            saved = self.state(cookie, project)
            self.check(prefix+"save_receipt", saved["revision"] == restored["revision"]+1 and
                       saved["doc"] == restored["doc"])
            browser.click("#undo")
            browser.wait("!window.opencad3d.busy")
            undo = self.geometry(browser, cookie, project, 14400, "undo")
            self.check(prefix+"undo_geometry", undo["doc"] == cleared["doc"])
            browser.click("#redo")
            browser.wait("!window.opencad3d.busy")
            redo = self.geometry(browser, cookie, project, pierced_volume, "redo")
            self.check(prefix+"redo_geometry", redo["doc"] == pierced["doc"])
            before_restart = self.state(cookie, project)
            port = self.cli[-1]["port"]
            self.stop()
            self.start(port)
            browser.evaluate("window.opencad3d.refresh()")
            after_restart = self.geometry(browser, cookie, project, pierced_volume, "restart")
            self.check(prefix+"cli_restart_persistence", all(before_restart[k] == after_restart[k]
                       for k in ("doc", "revision", "sha256", "undo_depth", "redo_depth")))
            browser.screenshot("restored")
            downloads = browser.out / "downloads"
            downloads.mkdir()
            browser.call("Browser.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(downloads), "eventsEnabled": True})
            browser.click("#step")
            deadline = time.monotonic()+60
            while browser.download_progress is None or browser.download_progress["state"] != "completed":
                require(browser.download_progress is None or
                        browser.download_progress["state"] != "canceled", "real_download_canceled")
                require(time.monotonic() < deadline, "real_download_timeout")
                browser.evaluate("void 0")
                time.sleep(.1)
            downloaded = (downloads / "opencad.step").read_bytes()
            witness = browser.evaluate("window.__stepWitness")
            validate_download(downloaded, witness, browser.download_progress)
            witness["completion"] = browser.download_progress
            write(browser.out / "download-witness.json", witness)
            from cadcore.docmodel import new_document, document_to_l1
            from cadcore.kernel.subproc import SubprocessKernel
            imported = new_document()
            imported.features.append({"uuid": str(uuid.uuid4()), "kind": "import_step",
                "params": {"step_text": downloaded.decode("ascii")}, "suppressed": False})
            kernel = SubprocessKernel(work_dir=str(self.runtime / ("step-"+str(width))), keep_work_dir=True)
            try:
                shape = kernel.shape_from_document(json.loads(document_to_l1(imported)))
                step_volume = kernel.mass_properties(shape).volume_nm3/1e18
            finally:
                kernel.close()
            self.check(prefix+"real_step_download", witness["sha256"] == digest(downloaded) and
                       witness["bytes"] == len(downloaded) and abs(step_volume-pierced_volume) < 1e-5,
                       step_volume_mm3=step_volume)
            browser.wait("document.querySelector('#parametric-panel').dataset.revision==="
                         + json.dumps(str(redo["revision"])))
            self.type(browser, "[data-parameter=width]", "-1 * mm")
            anchor = self.state(cookie, project)
            browser.click("[data-parametric-rebuild]")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('invalid_parameter')",
                         timeout=60)
            self.check(prefix+"rejected_expression_atomic", self.state(cookie, project)["sha256"] ==
                       anchor["sha256"] and browser.evaluate(
                       "document.querySelector('[data-parameter=width]').value==='-1 * mm'"))
            self.discard(browser, accept=True)
            browser.wait("!document.querySelector('[data-parametric-rebuild]').disabled")
            self.type(browser, "[data-parameter=width]", "50 * mm")
            browser.evaluate("window.__lose=true")
            browser.click("[data-parametric-rebuild]")
            browser.wait("document.querySelector('.project-status output').textContent.includes('连接中断')",
                         timeout=60)
            lost = self.state(cookie, project)
            self.check(prefix+"lost_response_draft_preserved", lost["revision"] == anchor["revision"]+1 and
                       browser.evaluate("document.querySelector('[data-parameter=width]').value==='50 * mm'"))
            browser.click(".project-status button")
            browser.wait("document.querySelector('.project-status output').textContent.includes('最新视图已核对')",
                         timeout=60)
            self.check(prefix+"receipt_replay_single_commit", self.state(cookie, project)["revision"] ==
                       lost["revision"] and browser.evaluate(
                       "document.querySelector('[data-parameter=width]').value==='50 * mm'"))
            self.discard(browser, accept=True)
            browser.wait("!document.querySelector('[data-parametric-rebuild]').disabled")
            browser.evaluate("window.__hold=true;window.__held=false;"
                             "window.__readDone=false;window.opencadParametric.refresh(true)"
                             ".then(()=>window.__readDone=true,e=>window.__readError=e.message);"
                             "void 0")
            browser.wait("window.__held===true")
            browser.evaluate("window.__release()")
            browser.wait("window.__readDone===true")
            self.check(prefix+"late_read_positive", browser.evaluate(
                "!document.querySelector('#parametric-panel form').hidden"))
            browser.evaluate("window.__hold=true;window.__held=false;"
                             "window.opencadParametric.refresh(true).catch(e=>window.__readError=e.message);"
                             "void 0")
            browser.wait("window.__held===true")
            code, value = self.http("POST", "/api/auth/logout", cookie, body={})
            require(code == 200 and value["ok"], "actor_logout_failed")
            browser.evaluate("window.__release()")
            browser.wait("document.querySelector('#parametric-panel form').hidden && "
                         "document.querySelector('#parametric-panel .parametric-notice').textContent.includes('登录已失效')")
            self.check(prefix+"late_read_after_logout_shielded", browser.evaluate(
                "document.querySelectorAll('#parametric-panel [data-parameter]').length===0 && "
                "!document.querySelector('.parametric-result').dataset.documentSha"))
            browser.screenshot("expired")
            browser.call("Page.reload", {})
            self.login(browser, "config-bob")
            second = browser.evaluate("fetch('/api/state').then(r=>r.json())")
            self.check(prefix+"second_account_isolated", second["project_id"] != project and
                       second["revision"] == 1 and json.loads(second["doc"])["features"] == [] and
                       browser.evaluate("document.querySelectorAll('#parametric-panel [data-parameter]').length===0"))
        self.stop()


def acceptance(args):
    root, out, base = args.source_root.resolve(), args.evidence_dir.resolve(), args.runtime_root.absolute()
    require(root == ROOT.resolve(), "runner_source_root_mismatch")
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) and
            not out.is_relative_to(root), "evidence_not_external_3fs")
    require(base.is_relative_to(Path("/data/tmp")) and not base.is_relative_to(root)
            and all(not part.is_symlink() for part in (base, *base.parents)), "runtime_scope_invalid")
    out.mkdir(parents=True, exist_ok=False)
    base.mkdir(parents=True, exist_ok=False)
    runtime = base / "run"
    runtime.mkdir()
    report = {"verdict": "FAIL", "intended_rc": 1, "first_cause": None,
              "started_at": now(), "runtime_root": str(runtime),
              "production_mutated": False, "gpu_speed": "NOT_ASSESSED"}
    before = source(root)
    review = None
    try:
        review = Review(root, out, runtime, args.browser)
        review.register()
        for width, height in ((1440, 900), (390, 844)):
            review.start()
            review.scenario(width, height)
        report.update(verdict="PASS", intended_rc=0)
    except Exception:
        report["first_cause"] = traceback.format_exc()
        write(out / "failure.log", report["first_cause"].encode())
    finally:
        if review:
            try:
                review.stop()
            except Exception as error:
                review.cleanup_errors.append("cli_stop:"+str(error))
                if report["first_cause"] is None:
                    report["first_cause"] = traceback.format_exc()
                    write(out / "failure.log", report["first_cause"].encode())
            review.log.close()
        report.update(checks=review.checks if review else [], cli=review.cli if review else [],
                      cleanup_errors=review.cleanup_errors if review else [],
                      source_before=before, source_after=source(root), finished_at=now())
        report["source_drift"] = [] if report["source_before"] == report["source_after"] else ["source"]
        if report["source_drift"] or report["cleanup_errors"]:
            report.update(verdict="FAIL", intended_rc=1)
            if report["first_cause"] is None:
                report["first_cause"] = "source_drift_or_owned_cleanup_failed"
        artifacts = {}
        for path in out.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                if review and any(secret in data for secret in review.secrets):
                    report.update(verdict="FAIL", intended_rc=1)
                    if report["first_cause"] is None:
                        report["first_cause"] = "secret_in_external_evidence"
                        write(out / "failure.log", b"secret_in_external_evidence\n")
                artifacts[path.relative_to(out).as_posix()] = {"sha256": digest(data), "bytes": len(data)}
        report["artifacts"] = artifacts
        if report["verdict"] == "PASS":
            try:
                write(out / "terminal-candidate.json", report)
                saved_candidate = json.loads((out / "terminal-candidate.json").read_bytes())
                require(saved_candidate == report, "terminal_save_readback_mismatch")
                validate_bound_report(saved_candidate, root, out)
            except Exception:
                report.update(verdict="FAIL", intended_rc=1, first_cause=traceback.format_exc())
                write(out / "failure.log", report["first_cause"].encode())
                report["artifacts"]["failure.log"] = {
                    "sha256": digest((out / "failure.log").read_bytes()),
                    "bytes": (out / "failure.log").stat().st_size}
        write(out / "result.json", report)
        saved = json.loads((out / "result.json").read_bytes())
        require(saved == report, "terminal_save_readback_mismatch")
        line = f"{MARKER} verdict={report['verdict']} checks={len(report['checks'])} rc={report['intended_rc']}\n"
        write(out / "completion.log", line.encode())
        print(line.strip(), flush=True)
        print("evidence="+str(out), flush=True)
    return report["intended_rc"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--browser", default=DEFAULT_BROWSER)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--discovery-check", action="store_true",
                        help="collect selftests through unittest discovery, never run product suites")
    parser.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args(argv)
    if args.selftest:
        require(args.source_root.resolve() == ROOT.resolve(), "runner_source_root_mismatch")
        require(args.evidence_dir.resolve().is_relative_to(
                Path("/hf3fs-jd/data/tmp").resolve()), "evidence_not_external_3fs")
        args.evidence_dir.mkdir(parents=True, exist_ok=False)
        require(args.runtime_root is not None and args.runtime_root.absolute().is_relative_to(
                Path("/data/tmp")), "selftest_runtime_required")
        args.runtime_root.mkdir(parents=True, exist_ok=False)
        before = source(args.source_root.resolve())
        suite = (unittest.defaultTestLoader.discover(str(APP / "tests"),
                 pattern="test_parametric_config_browser.py", top_level_dir=str(APP))
                 if args.discovery_check else unittest.defaultTestLoader.loadTestsFromTestCase(Controls))
        bind_control_runtime(suite, args.runtime_root)
        with (args.evidence_dir / "tests.log").open("x") as log:
            with redirect_stdout(log), redirect_stderr(log):
                result = unittest.TextTestRunner(
                    stream=log, verbosity=2, resultclass=EvidenceResult).run(suite)
        after = source(args.source_root.resolve())
        passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped and before == after
        first_cause = result.first_cause
        if not passed and first_cause is None:
            first_cause = "source_drift" if before != after else "empty_or_skipped_control_suite"
        if first_cause:
            write(args.evidence_dir / "failure.log", first_cause.encode())
        write(args.evidence_dir / "result.json", {
            "verdict": "PASS" if passed else "FAIL", "tests": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
            "source_before": before, "source_after": after, "finished_at": now(),
            "source_drift": [] if before == after else ["source"],
            "intended_rc": 0 if passed else 1,
            "discovery": args.discovery_check,
            "first_cause": first_cause})
        marker = (f"{MARKER}_SELFTEST verdict={'PASS' if passed else 'FAIL'} "
                  f"tests={result.testsRun} rc={0 if passed else 1}\n")
        write(args.evidence_dir / "completion.log", marker.encode())
        print(marker.strip())
        return 0 if passed else 1
    if args.verify_evidence:
        original = args.verify_evidence.resolve()
        args.evidence_dir.mkdir(parents=True, exist_ok=False)
        try:
            count = verify(json.loads((original / "result.json").read_bytes()),
                           args.source_root.resolve(), original)
            report = {"verdict": "EVIDENCE_VERIFIED", "checks": count, "intended_rc": 0,
                      "first_cause": None, "original_result_sha256": digest(
                          (original / "result.json").read_bytes()),
                      "source": source(args.source_root.resolve()), "finished_at": now()}
        except Exception:
            report = {"verdict": "FAIL", "intended_rc": 1, "first_cause": traceback.format_exc(),
                      "finished_at": now()}
            write(args.evidence_dir / "failure.log", report["first_cause"].encode())
        write(args.evidence_dir / "result.json", report)
        write(args.evidence_dir / "completion.log", (
            f"{MARKER}_READBACK verdict={report['verdict']} rc={report['intended_rc']}\n").encode())
        print(f"{MARKER} verdict={report['verdict']} checks={report.get('checks', 0)}")
        return report["intended_rc"]
    require(args.runtime_root is not None, "runtime_root_required")
    return acceptance(args)


if __name__ == "__main__":
    raise SystemExit(main())
