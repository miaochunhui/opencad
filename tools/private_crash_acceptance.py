#!/usr/bin/env python3
"""Accept actual private CLI SIGKILL/reopen against new local SQLite fixtures.

Only newly generated accounts/data and owned child processes are touched.
An unread HTTP response is resolved through its durable receipt after a crash.
SIGKILL is not physical power loss or a storage/controller flush guarantee.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid

import acceptance

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "oss-alternatives/apps/opencad"
sys.path.insert(0, str(APP))
from access_control import AuthController
from project_store import ProjectStore, validate_root


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


class CrashReview:
    def __init__(self, root, out):
        self.root, self.out = root, out
        self.process = None
        self.cookie = None
        self.logs = []
        self.checks = []
        self.secrets = []

    def check(self, name, **fields):
        entry = {"name": name, "verdict": "PASS", "at": now(), **fields}
        self.checks.append(entry)
        print(f"PRIVATE_CRASH_CHECK name={name} verdict=PASS", flush=True)

    def start(self):
        require(self.process is None, "A fixture CLI is already running")
        log = (self.out / f"cli-{len(self.logs) + 1}.log").open("xb")
        self.logs.append(log)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   OPENCAD_TEST_RUNTIME_ROOT=str(self.root))
        for name in ("PYTHONPATH", "OPENCAD_CADCORE_ROOT", "OPENCAD_I18N_DIRS",
                     "CADCORE_PDF_BACKEND", "CADCORE_SUBPROC_EXECUTABLE"):
            env.pop(name, None)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([
            sys.executable, "-B", str(APP / "secure_web.py"), "--data-root", str(self.root),
            "--port", str(port), "--public-origin", origin],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=log)
        self.process = process
        readable, _, _ = select.select([process.stdout], [], [], 15)
        require(readable, "Original private CLI did not emit readiness")
        line = process.stdout.readline()
        match = re.fullmatch(rb"OPENCAD_PRIVATE_WEB verdict=READY port=(\d+)\n", line)
        require(match is not None, "Original private CLI readiness marker invalid")
        self.port = int(match.group(1))
        require(self.port == port, "CLI listener differs from its exact configured origin")
        self.origin = origin
        self.check("original_cli_ready", generation=len(self.logs), port=self.port)

    def stop(self, *, crash):
        process = self.process
        require(process is not None, "No owned process to stop")
        if crash:
            process.kill()
        else:
            process.terminate()
        code = process.wait(timeout=15)
        process.stdout.close()
        self.process = None
        self.logs[-1].flush()
        os.fsync(self.logs[-1].fileno())
        self.logs[-1].close()
        require(code == (-9 if crash else 0), "Owned CLI exit status did not match stop mode")
        self.check("original_cli_sigkill" if crash else "original_cli_drained", process_rc=code)

    def call(self, method, route, body=None, project=None):
        headers = {"X-OpenCAD-Request": "1", "Origin": self.origin}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if project:
            headers["X-OpenCAD-Project"] = project
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, route, json.dumps(body) if body is not None else None,
                               headers=headers)
            response = connection.getresponse()
            raw = response.read()
            data = (json.loads(raw) if response.getheader("Content-Type", "").startswith(
                "application/json") else raw)
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def send_unread(self, body, project, *, partial=False):
        raw = json.dumps(body).encode()
        header = (
            f"POST /api/save HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            f"Origin: {self.origin}\r\nX-OpenCAD-Request: 1\r\n"
            f"Cookie: {self.cookie}\r\nX-OpenCAD-Project: {project}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n"
        ).encode()
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        connection.sendall(header + (raw[:len(raw) // 2] if partial else raw))
        return connection

    def head(self, project):
        with ProjectStore(self.root / "projects") as store:
            return store.read_head(project)

    def receipt(self, project, body):
        with ProjectStore(self.root / "projects") as store:
            return store.read_receipt(
                project, body["request_id"], request_payload={**body, "route": "/api/save"})

    @staticmethod
    def body(head, **fields):
        return {"expected_revision": head["revision"], "expected_sha": head["sha256"],
                "request_id": uuid.uuid4().hex, **fields}

    def run(self):
        accounts = AuthController(self.root / "accounts", allow_registration=True)
        password = uuid.uuid4().hex
        self.secrets.append(password.encode())
        try:
            accounts.register("crash-review", password)
        finally:
            accounts.close()
        self.start()
        status, headers, response = self.call(
            "POST", "/api/auth/login", {"username": "crash-review", "password": password})
        require(status == 200 and response.get("ok") is True, "Actual HTTP login failed")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.secrets.append(self.cookie.split("=", 1)[1].encode())
        status, _, head = self.call("POST", "/api/projects", {
            "title": "Crash recovery hole plate", "request_id": uuid.uuid4().hex})
        require(status == 201, "Actual HTTP project creation failed")
        project = head["project_id"]
        status, _, state = self.call("GET", "/api/state", project=project)
        require(status == 200, "Actual initial state failed")
        status, _, plate = self.call("POST", "/api/parametric", self.body(
            head, action="create_plate",
            expected_document_sha256=hashlib.sha256(state["doc"].encode()).hexdigest()), project)
        require(status == 200 and plate["ok"] is True, "Real hole-plate creation failed")
        before = self.head(project)
        status, _, original_mesh = self.call("GET", "/api/mesh", project=project)
        require(status == 200 and original_mesh["document_sha256"] == plate["document_sha256"],
                "Baseline HTTP mesh does not bind to the created plate")
        volume = original_mesh["solids"][0]["properties"]["volume_nm3"] / 1e18
        require(abs(volume - (40 * 20 - math.pi * 4**2) * 8) < 1e-5,
                "Baseline plate does not match independent analytic hole volume")
        write(self.out / "original-mesh.json", json.dumps(
            original_mesh, indent=2, sort_keys=True).encode())
        # Keep another connection open so uncheckpointed WAL survives the CLI kill.
        keeper = sqlite3.connect(self.root / "projects/projects.sqlite3", isolation_level=None)
        keeper.execute("SELECT count(*) FROM projects").fetchone()
        try:
            body = self.body(plate)
            unread = self.send_unread(body, project)
            try:
                deadline = time.monotonic() + 15
                receipt = None
                while receipt is None:
                    require(time.monotonic() < deadline, "Unread request never committed a receipt")
                    receipt = self.receipt(project, body)
                    if receipt is None:
                        time.sleep(.01)
                committed = self.head(project)
                require(committed["revision"] == before["revision"] + 1
                        and committed["snapshot"] == before["snapshot"],
                        "Save did not persist the exact hole-plate snapshot")
                self.check("unread_response_confirmed_in_database",
                           revision=committed["revision"], request_id=body["request_id"])
                self.stop(crash=True)
            finally:
                unread.close()
            wal = self.root / "projects/projects.sqlite3-wal"
            require(wal.is_file() and wal.stat().st_size > 32, "No retained WAL for crash recovery")
            require(self.head(project) == committed, "Committed head lost after SIGKILL")
            self.check("wal_recovered_exact_head", wal_bytes=wal.stat().st_size)
            self.start()
            status, _, recovered = self.call("GET", "/api/state", project=project)
            require(status == 200 and recovered["revision"] == committed["revision"]
                    and recovered["sha256"] == committed["sha256"],
                    "Actual CLI reopen did not recover the acknowledged head")
            status, _, replay = self.call("POST", "/api/save", body, project)
            require(status == 200 and replay == {
                **receipt["response"], "project_id": project,
                "revision": receipt["revision"], "sha256": receipt["sha256"]},
                "Lost-response replay did not return the original receipt")
            require(self.head(project) == committed, "Receipt replay added another revision")
            self.check("old_base_replay_after_sigkill", revision=replay["revision"])
            incomplete_body = self.body(committed)
            partial = self.send_unread(incomplete_body, project, partial=True)
            try:
                self.stop(crash=True)
            finally:
                partial.close()
            require(self.head(project) == committed, "Partial HTTP body changed the database")
            require(self.receipt(project, incomplete_body) is None,
                    "Partial HTTP body created a request receipt")
            self.check("incomplete_http_body_no_confirmation")
            self.start()
            status, _, mesh = self.call("GET", "/api/mesh", project=project)
            require(status == 200 and mesh["revision"] == committed["revision"]
                    and mesh["sha256"] == committed["sha256"], "Recovered mesh version mismatch")
            write(self.out / "recovered-mesh.json", json.dumps(
                mesh, indent=2, sort_keys=True).encode())
            require(mesh["document_sha256"] == plate["document_sha256"]
                    and mesh["solids"] == original_mesh["solids"],
                    "Recovered exact BRep mesh/volume differs from the original hole plate")
            status, headers, step = self.call("GET", "/api/step", project=project)
            require(status == 200 and step.startswith(b"ISO-10303-21;")
                    and headers["X-OpenCAD-Document-SHA256"] == plate["document_sha256"]
                    and headers["X-OpenCAD-Revision"] == str(committed["revision"]),
                    "Recovered STEP does not bind to the same model/version")
            write(self.out / "recovered.step", step)
            write(self.out / "recovered-head.json", json.dumps(
                committed, indent=2, sort_keys=True).encode())
            self.check("hole_plate_mesh_and_step_after_two_crashes",
                       revision=committed["revision"], solids=len(mesh["solids"]))
            require(self.head(project) == committed, "Read/export changed recovered history")
            self.stop(crash=False)
        finally:
            keeper.close()

    def cleanup(self):
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=15)
            self.process.stdout.close()
            self.process = None
        for log in self.logs:
            if not log.closed:
                log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    root = validate_root(args.runtime_root)
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()),
            "Evidence must use external 3FS tmp")
    require(not out.is_relative_to(ROOT) and not root.resolve().is_relative_to(out)
            and not out.is_relative_to(root.resolve()), "Source/runtime/evidence must be disjoint")
    require(not root.exists(), "Crash test runtime must be new")
    out.mkdir(parents=True, exist_ok=False)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {"verdict": "FAIL", "started_at": now(), "runtime_root": str(root),
              "production": "NOT_TOUCHED", "power_loss": "NOT_ASSESSED",
              "physical_storage_durability": "NOT_ASSESSED"}
    review, before = CrashReview(root, out), acceptance.source_manifest(ROOT)
    try:
        review.run()
        report["verdict"] = "PASS"
    except Exception as exc:
        report["cause"] = {"type": type(exc).__name__, "detail": str(exc),
                           "traceback": traceback.format_exc()}
    finally:
        review.cleanup()
        after = acceptance.source_manifest(ROOT)
        report["source_drift"] = sorted(name for name in before.keys() | after.keys()
                                       if before.get(name) != after.get(name))
        report["source_sha256"] = before
        report["checks"] = review.checks
        report["passed"] = len(review.checks)
        report["finished_at"] = now()
        if report["source_drift"]:
            report["verdict"] = "FAIL"
        encoded = json.dumps(report, indent=2, sort_keys=True).encode()
        for secret in review.secrets:
            if secret in encoded or any(secret in path.read_bytes()
                                        for path in out.iterdir() if path.is_file()):
                report.pop("cause", None)
                report["verdict"] = "FAIL"
                report["secret_redaction"] = "FAILED_NO_VALUE_REPORTED"
                break
        write(out / "result.json", json.dumps(report, indent=2, sort_keys=True).encode())
        line = (f"PRIVATE_CRASH_ACCEPTANCE verdict={report['verdict']} "
                f"checks={len(review.checks)} collected_at={report['finished_at']}")
        write(out / "verdict.log", (line + "\n").encode())
        print(line, flush=True)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
