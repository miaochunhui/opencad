#!/usr/bin/env python3
"""Owned configuration CRUD browser gate; source-bound live CLI/SQLite/FreeCAD.

--selftest runs only this file's receipt/source/terminal negative controls.
--verify-evidence is offline. Real runs require fresh 3FS evidence and a short
fresh /data/tmp runtime. Existing browser/geometry helpers are read-only imports;
no previous screenshots, results, product suites or production data are used.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
import copy
import http.client
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
APP = ROOT / "oss-alternatives/apps/opencad"
OWNED = (
    "oss-alternatives/apps/opencad/web/js/parametric-panel.js",
    "oss-alternatives/apps/opencad/web/css/parametric-panel.css",
    "oss-alternatives/apps/opencad/tests/test_parametric_config_crud_browser.py",
    "oss-alternatives/apps/opencad/docs/design/parametric-config-crud-web-ui.md",
)
READERS = (
    "oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py",
    "oss-alternatives/tools/cad3d/viewer_controls_check.py",
    "oss-alternatives/apps/opencad/tests/legacy_browser_compat_gate.py",
    "tools/bootstrap_from_infra.py",
)
MARKER = "OPENCAD_PARAMETRIC_CONFIG_CRUD"
WORKFLOWS = (
    "create_inactive_two_drafts", "replace_selection_overrides", "suppression_three_states",
    "delete_explicit_replacement", "invalid_definitions_atomic", "draft_save_and_conflict",
    "save_undo_redo_restart", "lossresponse_and_late_read", "expired_identity_isolation",
)
GEOMETRY = (
    "created", "inactive", "active", "keep-overrides", "clear-overrides", "unsuppress",
    "inherit", "suppress", "delete", "saved", "undo", "redo", "restart", "loss",
)
spec = importlib.util.spec_from_file_location("crud_browser_helpers", APP / "tests/test_parametric_config_browser.py")
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)
require, write, digest, canonical, now = (
    legacy.require, legacy.write, legacy.digest, legacy.canonical, legacy.now)
CONTROL_RUNTIME = None


def source():
    captured = legacy.source(ROOT)
    require(set(OWNED + READERS) <= set(captured["files"]), "crud_source_missing")
    require(Path(legacy.__file__).resolve() == ROOT / READERS[0], "foreign_reader_module")
    captured["owned"] = {name: captured["files"][name] for name in OWNED}
    captured["readers"] = {name: captured["files"][name] for name in READERS}
    scanner = legacy.load("crud_source_scan", ROOT / "tools/bootstrap_from_infra.py")
    for name in OWNED:
        scanner.scan_bytes(name, (ROOT / name).read_bytes())
    return captured


def expected_checks():
    return {f"{width}-{label}" for width in (1440, 390) for label in WORKFLOWS}


def terminal(report):
    require(report["verdict"] == "PASS" and report["intended_rc"] == 0 and
            report["first_cause"] is None and not report["cleanup_errors"], "producer_failed")
    require(report["source_before"] == report["source_after"] and report["source_drift"] == [],
            "source_drift")
    checks = report["checks"]
    require(len(checks) == len(expected_checks()) and
            {row["name"] for row in checks} == expected_checks() and
            all(row["pass"] is True for row in checks), "workflow_ledger_mismatch")
    require(len(report["cli"]) == 4 and all(
        row["ready"] is True and row["exit_rc"] == 0 and
        row["argv"] == row["observed_argv"] and row["startticks"] == row["stop_startticks"] and
        type(row["startticks"]) is int and row["startticks"] > 0 for row in report["cli"]),
        "cli_terminal_mismatch")
    return len(checks)


def validate_requests(report):
    for rows in [*report["requests"].values(), report["actor_requests"]]:
        require(rows, "response_ledger_empty")
        for row in rows:
            validate_response(row)
    for width in (1440, 390):
        requests = report["requests"][str(width)]
        mutations = [row for row in requests if row["route"] == "/api/parametric" and row["body"]]
        require(mutations and not any(row["route"] == "/api/open" for row in requests),
                "open_rebuild_forbidden")
        require({"create_configuration", "replace_configuration", "delete_configuration",
                 "set_suppressions"} <= {row["body"]["action"] for row in mutations},
                "four_actions_not_measured")
        require(any(row["status"] == 409 for row in mutations) and
                any(row["status"] == 422 for row in mutations), "http_negative_missing")
        for row in mutations:
            body = row["body"]
            require(type(body["expected_revision"]) is int and body["expected_revision"] > 0 and
                    len(body["expected_sha"]) == len(body["expected_document_sha256"]) == 64 and
                    body["request_id"], "request_cas_missing")
            if body["action"] in {"create_configuration", "replace_configuration"}:
                require(set(body["definition"]) == {"parameters", "suppressions"},
                        "definition_schema_mismatch")
            flags = body.get("suppressions", body.get("definition", {}).get("suppressions", {}))
            require(all(type(flag) is bool for flag in flags.values()), "suppression_nonboolean")
            if body["action"] in {"create_configuration", "replace_configuration",
                                   "delete_configuration", "set_suppressions"}:
                require(not set(body) & {"overrides", "dimensions"}, "definition_override_mix")
        operations = report["operations"][str(width)]
        require(operations and all(
            row["after"]["revision"] == row["before"]["revision"] + 1 and
            row["after"]["receipts"] == row["before"]["receipts"] + 1 and
            row["receipt"]["request_id"] == row["request"]["request_id"] and
            row["receipt"]["revision"] == row["after"]["revision"] and
            row["receipt"]["sha256"] == row["after"]["sha256"] for row in operations),
            "single_receipt_mismatch")
        ids = [row["request"]["request_id"] for row in operations]
        require(len(ids) == len(set(ids)), "duplicate_operation_id")
        lost = report["loss"][str(width)]
        same = [row for row in mutations if row["body"]["request_id"] == lost["request_id"]]
        require(len(same) == 2 and same[0]["body"] == same[1]["body"] and
                lost["before_replay"] == lost["after_replay"] and
                all(row["status"] == 200 for row in same), "loss_replay_not_exact")
        require(same[0]["response"]["body_base64"] == same[1]["response"]["body_base64"],
                "loss_response_not_exact")
        for operation in operations:
            matches = [row for row in mutations if
                       row["body"]["request_id"] == operation["request"]["request_id"]]
            require(len(matches) == 1 and matches[0]["body"] == operation["request"] and
                    matches[0]["status"] == 200, "operation_response_missing")
            payload = json.loads(validate_response(matches[0]))
            require(all(payload[key] == operation["after"][key] for key in ("revision", "sha256")),
                    "operation_response_receipt_mismatch")


def validate_response(row):
    require(type(row["status"]) is int and 100 <= row["status"] <= 599 and
            row["method"] in {"GET", "POST"}, "http_response_status_invalid")
    item = row["response"]
    data = base64.b64decode(item["body_base64"], validate=True)
    require(type(item["bytes"]) is int and len(data) == item["bytes"] and
            digest(data) == item["sha256"], "http_response_bytes_mismatch")
    require(type(item["headers"]) is dict and all(
        key == "content-type" or key.startswith("x-opencad-") for key in item["headers"]),
        "http_response_headers_outside_scope")
    if item["headers"].get("content-type", "").startswith("application/json"):
        value = json.loads(data)
        require(type(value) is dict and (value.get("ok") is True if row["status"] < 400 else
                value.get("ok") is False), "http_response_payload_mismatch")
    return data


def verify_bound(report, out, *, current=True):
    count = terminal(report)
    if current:
        require(source() == report["source_after"], "current_source_drift")
    captured = report["source_after"]
    require(captured["root"] == str(ROOT.resolve()) and len(captured["owned"]) == 4 and
            captured["owned"] == {name: captured["files"][name] for name in OWNED} and
            captured["readers"] == {name: captured["files"][name] for name in READERS} and
            digest(canonical(captured["files"])) == captured["sha256"], "source_binding_mismatch")
    actual = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
              and path.relative_to(out).as_posix() not in {
                  "result.json", "terminal-candidate.json", "completion.log"}}
    require(actual == set(report["artifacts"]) and
            {"checks.log", "http-ledger.json"} <= actual, "artifact_set_mismatch")
    for name, item in report["artifacts"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "artifact_escape")
        legacy.validate_artifact(out / name, item)
    require([json.loads(line) for line in (out / "checks.log").read_text().splitlines()] ==
            report["checks"], "check_log_mismatch")
    require(json.loads((out / "http-ledger.json").read_bytes()) ==
            {key: report[key] for key in ("requests", "actor_requests", "operations", "loss", "measurements")},
            "http_ledger_mismatch")
    validate_requests(report)
    for number, cli in enumerate(report["cli"], 1):
        require(cli["log"] == f"cli-{number}.log" and
                cli["argv"] == [sys.executable, "-B", str(APP / "secure_web.py"), "--data-root",
                    report["runtime_root"], "--host", "127.0.0.1", "--port", str(cli["port"]),
                    "--public-origin", cli["origin"]] and
                cli["origin"] == f"http://127.0.0.1:{cli['port']}" and
                f"OPENCAD_PRIVATE_WEB verdict=READY port={cli['port']}\n" in
                (out / cli["log"]).read_text(), "cli_scope_mismatch")
    require(len(report["measurements"]) == len(GEOMETRY) * 2 and
            {row["label"] for row in report["measurements"]} ==
            {f"{width}-{label}" for width in (1440, 390) for label in GEOMETRY},
            "geometry_ledger_mismatch")
    for record in report["measurements"]:
        witness = json.loads((out / record["witness"]).read_bytes())
        legacy.validate_geometry_witness(witness)
        require(record["document_sha256"] == witness["mesh"]["document_sha256"] and
                record["revision"] == witness["state"]["revision"] and
                abs(record["step_mm3"] - witness["expected_mm3"]) <= 1e-5, "step_geometry_mismatch")
        step = (out / record["step"]).read_bytes()
        require(step.startswith(b"ISO-10303-21;") and digest(step) == record["step_sha256"] and
                len(step) == record["step_bytes"], "step_hash_mismatch")
    for width in (1440, 390):
        prefix = str(width) + "/"
        for workflow in WORKFLOWS:
            png = (out / prefix / (workflow + ".png")).read_bytes()
            require(png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000, "screenshot_missing")
        download = report["downloads"][str(width)]
        legacy.validate_download((out / download["file"]).read_bytes(), download["witness"],
                                 download["progress"])
    return count


class Controls(unittest.TestCase):
    @staticmethod
    def response_row():
        data = b'{"ok":true,"revision":2,"sha256":"synthetic"}'
        return {"route": "/api/parametric", "method": "POST", "status": 200, "body": {},
                "response": {"body_base64": base64.b64encode(data).decode(),
                             "bytes": len(data), "sha256": digest(data),
                             "headers": {"content-type": "application/json"}}}

    def test_response_bytes_positive_and_each_field_negative(self):
        row = self.response_row()
        self.assertEqual(json.loads(validate_response(row))["revision"], 2)
        for key, value in (("body_base64", base64.b64encode(b'{"ok":true}').decode()),
                           ("bytes", 0), ("sha256", "0" * 64)):
            changed = copy.deepcopy(row)
            changed["response"][key] = value
            with self.assertRaisesRegex(AssertionError, "http_response_bytes_mismatch"):
                validate_response(changed)
        for key in ("body_base64", "bytes", "sha256", "headers"):
            changed = copy.deepcopy(row)
            del changed["response"][key]
            with self.assertRaises(KeyError):
                validate_response(changed)

    def test_response_headers_and_failed_payload_negative(self):
        row = self.response_row()
        row["response"]["headers"]["set-cookie"] = "synthetic"
        with self.assertRaisesRegex(AssertionError, "headers_outside_scope"):
            validate_response(row)
        del row["response"]["headers"]["set-cookie"]
        row["status"] = 422
        with self.assertRaisesRegex(AssertionError, "payload_mismatch"):
            validate_response(row)

    def request_report(self):
        def row(action, identifier, status=200):
            item = self.response_row()
            item["body"] = {"action": action, "request_id": identifier,
                "expected_revision": 1, "expected_sha": "b" * 64,
                "expected_document_sha256": "c" * 64}
            if action in {"create_configuration", "replace_configuration"}:
                item["body"]["definition"] = {"parameters": {}, "suppressions": {"hole_cut": True}}
            if action == "set_suppressions": item["body"]["suppressions"] = {"hole_cut": False}
            data = canonical({"ok": status == 200, "revision": 2, "sha256": "a" * 64})
            item.update(status=status, response={**item["response"],
                "body_base64": base64.b64encode(data).decode(), "bytes": len(data), "sha256": digest(data)})
            return item
        rows = [row(action, str(index)) for index, action in enumerate((
            "create_configuration", "replace_configuration", "delete_configuration", "set_suppressions"))]
        rows += [row("replace_configuration", "conflict", 409),
                 row("create_configuration", "invalid", 422)]
        rows += [row("replace_configuration", "lost"), row("replace_configuration", "lost")]
        before = {"revision": 1, "sha256": "b" * 64, "receipts": 0}
        after = {"revision": 2, "sha256": "a" * 64, "receipts": 1}
        operations = [{"before": before, "after": after, "request": item["body"],
            "receipt": {"request_id": item["body"]["request_id"], "revision": 2, "sha256": "a" * 64}}
            for item in rows[:4]]
        return {"requests": {str(width): copy.deepcopy(rows) for width in (1440, 390)},
            "actor_requests": [copy.deepcopy(rows[0])],
            "operations": {str(width): copy.deepcopy(operations) for width in (1440, 390)},
            "loss": {str(width): {"request_id": "lost", "before_replay": after, "after_replay": after}
                     for width in (1440, 390)}}

    def test_real_ledger_validator_synthetic_positive(self):
        validate_requests(self.request_report())

    def test_single_receipt_and_response_binding_negative(self):
        for kind in ("revision", "receipts", "receipt", "response_missing"):
            changed = self.request_report()
            operation = changed["operations"]["1440"][0]
            if kind in {"revision", "receipts"}: operation["after"][kind] += 1
            if kind == "receipt": operation["receipt"]["sha256"] = "0" * 64
            if kind == "response_missing": operation["request"]["request_id"] = "unmeasured"
            with self.assertRaises(AssertionError): validate_requests(changed)

    def test_boolean_open_and_exact_replay_negative(self):
        for kind in ("boolean", "open", "replay_body", "replay_receipt", "response_bytes"):
            changed = self.request_report()
            rows = changed["requests"]["1440"]
            if kind == "boolean": rows[0]["body"]["definition"]["suppressions"]["hole_cut"] = 1
            if kind == "open": rows.append({**copy.deepcopy(rows[0]), "route": "/api/open"})
            if kind == "replay_body": rows[-1]["body"]["name"] = "changed"
            if kind == "replay_receipt": changed["loss"]["1440"]["after_replay"] = {}
            if kind == "response_bytes": rows[-1]["response"]["sha256"] = "0" * 64
            with self.assertRaises(AssertionError): validate_requests(changed)

    def valid(self):
        return {"verdict": "PASS", "intended_rc": 0, "first_cause": None, "cleanup_errors": [],
                "source_before": {"head": "a", "files": {}}, "source_after": {"head": "a", "files": {}},
                "source_drift": [], "checks": [{"name": name, "pass": True} for name in expected_checks()],
                "cli": [{"ready": True, "exit_rc": 0, "argv": ["synthetic"], "observed_argv": ["synthetic"],
                         "startticks": 1, "stop_startticks": 1} for _ in range(4)]}

    def test_terminal_positive(self):
        self.assertEqual(terminal(self.valid()), 18)

    def test_empty_missing_duplicate_renamed_and_false_ledger(self):
        for mode in ("empty", "missing", "duplicate", "renamed", "false"):
            report = self.valid()
            if mode == "empty": report["checks"] = []
            if mode == "missing": report["checks"].pop()
            if mode == "duplicate": report["checks"].append(report["checks"][0])
            if mode == "renamed": report["checks"][0]["name"] = "unmeasured"
            if mode == "false": report["checks"][0]["pass"] = False
            with self.assertRaisesRegex(AssertionError, "workflow_ledger_mismatch"):
                terminal(report)

    def test_head_only_drift(self):
        report = self.valid()
        report["source_after"]["head"] = "b"
        with self.assertRaisesRegex(AssertionError, "source_drift"): terminal(report)

    def test_bytes_mode_status_drift(self):
        for key in ("files", "status"):
            report = self.valid()
            report["source_after"][key] = {"mode": 0, "sha256": "changed"}
            with self.assertRaisesRegex(AssertionError, "source_drift"): terminal(report)

    def test_firstcause_and_forced_cleanup_cannot_pass(self):
        for key, value in (("first_cause", "original failure"), ("cleanup_errors", ["forced kill"])):
            report = self.valid()
            report[key] = value
            with self.assertRaisesRegex(AssertionError, "producer_failed"): terminal(report)

    def test_cli_argv_startticks_exit_negative(self):
        for key, value in (("exit_rc", -9), ("observed_argv", ["wrong"]), ("stop_startticks", 2)):
            report = self.valid()
            report["cli"][0][key] = value
            with self.assertRaisesRegex(AssertionError, "cli_terminal_mismatch"): terminal(report)

    def test_actual_owned_source_and_reader_binding(self):
        captured = source()
        self.assertEqual(set(captured["owned"]), set(OWNED))
        self.assertEqual(set(captured["readers"]), set(READERS))
        self.assertEqual(len(captured["head"]), 40)
        self.assertEqual(len(captured["sha256"]), 64)
        for name in OWNED + READERS:
            self.assertEqual(captured["files"][name]["sha256"], digest((ROOT / name).read_bytes()))

    def test_download_incomplete_negative(self):
        with self.assertRaisesRegex(AssertionError, "download_not_complete_or_bound"):
            legacy.validate_download(b"ISO-10303-21;", {"bytes": 14, "sha256": "0"*64},
                                     {"state": "inProgress", "receivedBytes": 14, "totalBytes": 14})

    def test_artifact_hash_and_missing_negative(self):
        root = CONTROL_RUNTIME or Path("/data/tmp")
        child = root / ("crud-control-" + uuid4().hex)
        child.mkdir()
        path = child / "synthetic.txt"
        write(path, b"synthetic control")
        item = {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
        legacy.validate_artifact(path, item)
        with self.assertRaisesRegex(AssertionError, "artifact_hash_mismatch"):
            legacy.validate_artifact(path, {**item, "sha256": "0"*64})
        with self.assertRaisesRegex(AssertionError, "artifact_not_regular"):
            legacy.validate_artifact(child / "missing", item)

    def test_first_error_precedes_later_failure(self):
        class First(unittest.TestCase):
            def runTest(self): raise RuntimeError("first-original-control")
        class Later(unittest.TestCase):
            def runTest(self): self.fail("later-control")
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=legacy.EvidenceResult).run(
            unittest.TestSuite([First(), Later()]))
        self.assertIn("first-original-control", result.first_cause)
        self.assertNotIn("later-control", result.first_cause)


class Review(legacy.Review):
    def __init__(self, *args):
        super().__init__(*args)
        self.requests, self.operations, self.loss, self.downloads = {}, {}, {}, {}
        self.actor_requests = []
        self.measurements = []
        sys.path.insert(0, str(ROOT / "oss-alternatives/packages/cadcore"))
        from cadcore import docmodel
        from cadcore.kernel.subproc import SubprocessKernel
        self.docmodel = docmodel
        work = self.runtime / "step-reader"
        work.mkdir()
        try:
            self.kernel = SubprocessKernel(work_dir=str(work), keep_work_dir=True,
                                           extra_env={"TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1"})
        except Exception:
            self.log.close()
            raise

    @staticmethod
    def choose(browser, selector, value):
        index = browser.evaluate("Array.from(document.querySelector(" + json.dumps(selector) +
            ").options).findIndex(o=>o.value===" + json.dumps(value) + ")")
        require(type(index) is int and index >= 0, "select_value_missing:" + value)
        browser.click(selector)
        for key in ["Home", *(["ArrowDown"] * index), "Enter"]:
            for kind in ("keyDown", "keyUp"):
                browser.call("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": key,
                    "windowsVirtualKeyCode": {"Home": 36, "ArrowDown": 40, "Enter": 13}[key]})

    def login(self, browser, name, project=None):
        browser.wait("window.__crudAuth===200 || window.__crudAuth===401")
        if browser.evaluate("window.__crudAuth===401"):
            browser.wait("!!document.querySelector('dialog[open] input[name=username]')")
            self.type(browser, "dialog[open] input[name=username]", name)
            self.type(browser, "dialog[open] input[name=password]", self.accounts[name]["password"])
            browser.click("dialog[open] button[type=submit]")
        browser.wait("!!document.querySelector('dialog[open] select')")
        if project:
            self.choose(browser, "dialog[open] select", project)
            browser.click("dialog[open] button")
        else:
            self.type(browser, "[aria-label='新项目名称']", "配置 CRUD " + str(browser.width))
            browser.click("dialog[open] button:nth-of-type(2)")
        browser.wait("!document.querySelector('dialog')")
        browser.evaluate("document.querySelector('[data-parametric-host]').closest('details').open=true")
        browser.wait("!!document.querySelector('#parametric-panel').dataset.projectId && "
                     "document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'")

    @staticmethod
    def reload(browser):
        page = browser.evaluate("window.__crudPage")
        browser.call("Page.reload", {})
        browser.wait("!!window.__crudPage && window.__crudPage!==" + json.dumps(page))

    def record_response(self, method, route, body, response, data):
        self.actor_requests.append({"method": method, "route": route, "body": body,
            "status": response.status, "response": {
                "body_base64": base64.b64encode(data).decode(), "bytes": len(data), "sha256": digest(data),
                "headers": {key.lower(): value for key, value in response.getheaders()
                            if key.lower() == "content-type" or key.lower().startswith("x-opencad-")}}})

    def http(self, method, path, cookie=None, project=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.cli[-1]["port"], timeout=60)
        headers = {"Origin": self.origin, "X-OpenCAD-Request": "1"}
        if cookie: headers["Cookie"] = "opencad_session=" + cookie
        if project: headers["X-OpenCAD-Project"] = project
        if body is not None: headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            data = response.read()
            if not path.startswith("/api/auth/"):
                self.record_response(method, path, body, response, data)
            return response.status, (json.loads(data) if response.getheader(
                "Content-Type", "").startswith("application/json") else data)
        finally:
            conn.close()

    def anchor(self, cookie, project):
        state = self.state(cookie, project)
        dbpath = self.runtime / "projects" / "projects.sqlite3"
        with sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True) as db:
            require(db.execute("PRAGMA foreign_key_check").fetchall() == [], "sqlite_fk_failure")
            count = db.execute("SELECT count(*) FROM receipts WHERE project_id=?", (project,)).fetchone()[0]
        return {"revision": state["revision"], "sha256": state["sha256"], "receipts": count}

    def confirm(self, browser, cookie, project, before):
        self.rebuilt(browser, before["revision"])
        after = self.anchor(cookie, project)
        rows = browser.evaluate("window.__crudRequests.filter(r=>r.route==='/api/parametric'"
                                "&&r.body&&r.status===200)")
        row = rows[-1]
        sys.path.insert(0, str(APP))
        from project_store import ProjectStore
        store = ProjectStore(self.runtime / "projects")
        try:
            receipt = store.read_receipt(project, row["body"]["request_id"],
                                        request_payload={**row["body"], "route": "/api/parametric"})
        finally:
            store.close()
        require(receipt is not None and after["revision"] == before["revision"] + 1 and
                after["receipts"] == before["receipts"] + 1 and receipt["revision"] == after["revision"] and
                receipt["sha256"] == after["sha256"], "single_operation_receipt_failed")
        self.operations[str(browser.width)].append({"before": before, "after": after,
            "request": row["body"], "receipt": {key: receipt[key] for key in (
                "request_id", "revision", "sha256")}})
        return after

    def edit_width(self, browser, expression):
        if not browser.evaluate("document.querySelector('[data-definition-explicit=width]').checked"):
            browser.click("[data-definition-explicit=width]")
        self.type(browser, "[data-definition-parameter=width]", expression)

    def discard_definition(self, browser):
        browser.click("[data-definition-discard]")
        browser.wait("!document.querySelector('[data-definition-new]').disabled")

    def geometry(self, browser, cookie, project, expected, label):
        state = super().geometry(browser, cookie, project, expected, label)
        conn = http.client.HTTPConnection("127.0.0.1", self.cli[-1]["port"], timeout=60)
        try:
            conn.request("GET", "/api/step", headers={"Cookie": "opencad_session=" + cookie,
                         "X-OpenCAD-Project": project})
            response = conn.getresponse()
            step = response.read()
            self.record_response("GET", "/api/step", None, response, step)
            require(response.status == 200 and response.getheader("X-OpenCAD-Document-SHA256") ==
                    digest(state["doc"].encode()) and int(response.getheader("X-OpenCAD-Revision")) ==
                    state["revision"], "step_version_mismatch")
        finally:
            conn.close()
        require(self.anchor(cookie, project)["revision"] == state["revision"], "geometry_head_changed")
        imported = self.docmodel.new_document()
        imported.features.append({"uuid": str(uuid4()), "kind": "import_step", "suppressed": False,
                                  "params": {"step_text": step.decode("ascii")}})
        shape = self.kernel.shape_from_document(json.loads(self.docmodel.document_to_l1(imported)))
        volume = self.kernel.mass_properties(shape).volume_nm3 / 1e18
        require(abs(volume-expected) <= 1e-5, "step_volume_mismatch")
        write(browser.out / (label + ".step"), step)
        self.measurements.append({"label": str(browser.width) + "-" + label,
            "witness": f"{browser.width}/{label}.json", "step": f"{browser.width}/{label}.step",
            "document_sha256": digest(state["doc"].encode()), "revision": state["revision"],
            "step_mm3": volume, "step_sha256": digest(step), "step_bytes": len(step)})
        return state

    def workflow(self, browser, label, condition, **facts):
        self.check(str(browser.width) + "-" + label, condition, **facts)
        browser.screenshot(label)

    def scenario(self, width, height):
        self.operations[str(width)] = []
        Browser = self.browser_class()
        with Browser(self.binary, self.out / str(width), width=width, height=height,
                     source_root=self.root) as browser:
            browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
                "const raw=window.fetch.bind(window);window.__crudRequests=[];"
                "window.__crudPage=crypto.randomUUID();window.__crudAuth=null;"
                "window.fetch=async function(i,o){const p=new URL(typeof i==='string'?i:i.url,"
                "location.href).pathname;const r=await raw(i,o);"
                "if(p==='/api/auth/me'){window.__crudAuth=r.status;}"
                "if(['/api/parametric','/api/state','/api/mesh','/api/step','/api/save','/api/undo',"
                "'/api/redo','/api/open'].includes(p)){const b=await r.clone().arrayBuffer();"
                "const h=await crypto.subtle.digest('SHA-256',b);const bytes=new Uint8Array(b);"
                "let binary='';for(let n=0;n<bytes.length;n+=8192){binary+="
                "String.fromCharCode(...bytes.subarray(n,n+8192));}const headers={};"
                "r.headers.forEach((v,k)=>{if(k==='content-type'||k.startsWith('x-opencad-'))headers[k]=v;});"
                "window.__crudRequests.push({route:p,method:o?.method||'GET',status:r.status,"
                "body:o?.method==='POST'?JSON.parse(o.body):null,response:{body_base64:btoa(binary),"
                "bytes:b.byteLength,sha256:Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join(''),"
                "headers:headers}});}"
                "if(p==='/api/parametric'){"
                "if(o?.method==='POST'&&window.__lose){window.__lose=false;"
                "const v=await r.clone().json();if(!r.ok||!v.ok)throw Error('loss_fixture_rejected');"
                "throw Error('Owned body-loss boundary');}"
                "if(o?.method==='POST'&&window.__holdPost){window.__holdPost=false;"
                "window.__postHeld=true;await new Promise(resolve=>window.__releasePost=resolve);}"
                "if((!o?.method||o.method==='GET')&&window.__hold){window.__hold=false;"
                "window.__held=true;await new Promise(resolve=>window.__release=resolve);}}"
                "if(p==='/api/step'&&r.ok){const b=await r.clone().arrayBuffer();"
                "const h=await crypto.subtle.digest('SHA-256',b);window.__stepWitness={bytes:b.byteLength,"
                "sha256:Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join('')};}"
                "return r;};"})
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            self.login(browser, "config-alice")
            cookie = self.cookie(browser)
            project = browser.evaluate("document.querySelector('#parametric-panel').dataset.projectId")
            self.choose(browser, "#locale", "zh-CN")
            browser.wait("document.documentElement.lang==='zh-CN'")
            before = self.anchor(cookie, project)
            browser.click('[data-parametric-create="create_configurable_plate"]')
            self.confirm(browser, cookie, project, before)
            base_volume = (800-math.pi*16)*8
            self.geometry(browser, cookie, project, base_volume, "created")
            self.type(browser, "[data-parameter=width]", "55 * mm")
            browser.click("[data-definition-new]")
            self.type(browser, "[data-definition-name]", "custom")
            self.edit_width(browser, "50 * mm")
            self.choose(browser, "[data-definition-suppression=hole_cut]", "suppressed")
            before = self.anchor(cookie, project)
            browser.evaluate("window.__holdPost=true;window.__postHeld=false")
            browser.click("[data-definition-create]")
            browser.wait("window.__postHeld===true")
            committed = self.anchor(cookie, project)
            require(committed["revision"] == before["revision"] + 1 and
                    committed["receipts"] == before["receipts"] + 1 and browser.evaluate(
                        "document.querySelector('[data-parameter=width]').disabled && "
                        "document.querySelector('[data-definition-parameter=width]').disabled && "
                        "document.querySelector('[data-parameter=width]').value==='55 * mm' && "
                        "document.querySelector('[data-definition-parameter=width]').value==='50 * mm'"),
                    "held_post_draft_or_lock_failed")
            browser.evaluate("window.__releasePost();void 0")
            self.confirm(browser, cookie, project, before)
            state = self.geometry(browser, cookie, project, base_volume, "inactive")
            self.workflow(browser, WORKFLOWS[0],
                json.loads(state["doc"])["parametric"]["configurations"]["custom"] == {"width": "50 * mm"} and
                browser.evaluate("document.querySelector('[data-parameter=width]').value==='55 * mm'") and
                not json.loads(state["doc"])["parametric"]["active_configuration"])
            self.discard(browser, accept=True)
            browser.wait("!document.querySelector('[data-definition-replace]').disabled")
            self.choose(browser, "[data-definition-target]", "custom")
            self.choose(browser, "[data-definition-selection]", "target")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-replace]")
            self.confirm(browser, cookie, project, before)
            self.geometry(browser, cookie, project, 10000, "active")
            self.type(browser, "[data-parameter=width]", "55 * mm")
            before = self.anchor(cookie, project)
            browser.click("[data-parametric-rebuild]")
            self.confirm(browser, cookie, project, before)
            self.edit_width(browser, "60 * mm")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-replace]")
            self.confirm(browser, cookie, project, before)
            kept = self.geometry(browser, cookie, project, 12100, "keep-overrides")
            self.choose(browser, "[data-definition-selection]", "target")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-replace]")
            self.confirm(browser, cookie, project, before)
            cleared = self.geometry(browser, cookie, project, 14400, "clear-overrides")
            self.workflow(browser, WORKFLOWS[1],
                json.loads(kept["doc"])["parametric"]["active_overrides"] == {"width": "55 * mm"} and
                json.loads(cleared["doc"])["parametric"]["active_overrides"] == {})
            for value, expected, label in (
                ("live", (1200-math.pi*36)*12, "unsuppress"),
                ("inherit", (1200-math.pi*36)*12, "inherit"), ("suppressed", 14400, "suppress")):
                self.choose(browser, "[data-definition-suppression=hole_cut]", value)
                before = self.anchor(cookie, project)
                browser.click("[data-definition-suppressions]")
                self.confirm(browser, cookie, project, before)
                state = self.geometry(browser, cookie, project, expected, label)
                flags = json.loads(state["doc"])["parametric"]["configuration_suppressions"]["custom"]
                require(flags == ({} if value == "inherit" else {"hole_cut": value == "suppressed"}),
                        "suppression_three_state_encoding")
            self.workflow(browser, WORKFLOWS[2], True)
            before = self.anchor(cookie, project)
            browser.click("[data-definition-delete]")
            browser.click("[data-definition-confirm-delete]")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('明确选择')")
            require(self.anchor(cookie, project) == before, "active_delete_without_selection_mutated")
            browser.click("[data-definition-cancel-delete]")
            require(self.anchor(cookie, project) == before, "delete_cancel_mutated")
            browser.click("[data-definition-delete]")
            self.choose(browser, "[data-definition-replacement]", "wide")
            browser.click("[data-definition-confirm-delete]")
            self.confirm(browser, cookie, project, before)
            deleted = self.geometry(browser, cookie, project, (1200-math.pi*36)*12, "delete")
            self.workflow(browser, WORKFLOWS[3],
                "custom" not in json.loads(deleted["doc"])["parametric"]["configurations"] and
                json.loads(deleted["doc"])["parametric"]["active_configuration"] == "wide")
            for index, expression in enumerate(("50", "hole_x * 2", "-1 * mm")):
                browser.click("[data-definition-new]")
                self.type(browser, "[data-definition-name]", "bad"+str(index))
                self.edit_width(browser, expression)
                before = self.anchor(cookie, project)
                browser.click("[data-definition-create]")
                browser.wait("document.querySelector('#parametric-panel').getAttribute('aria-busy')==='false'"
                             " && document.querySelector('.parametric-notice').textContent.includes('草稿')")
                require(self.anchor(cookie, project) == before, "invalid_definition_mutated")
                require(browser.evaluate("document.querySelector('[data-definition-parameter=width]').value===" +
                                         json.dumps(expression)), "rejected_draft_lost")
                self.discard_definition(browser)
            browser.click("[data-definition-new]")
            self.type(browser, "[data-definition-name]", "all_hidden")
            self.choose(browser, "[data-definition-suppression=plate_boss]", "suppressed")
            self.choose(browser, "[data-definition-suppression=hole_cut]", "suppressed")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-create]")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('configuration_dependency')")
            require(self.anchor(cookie, project) == before, "all_suppressed_mutated")
            self.discard_definition(browser)
            self.workflow(browser, WORKFLOWS[4], True)
            self.type(browser, "[data-parameter=width]", "55 * mm")
            browser.click("[data-definition-new]")
            self.type(browser, "[data-definition-name]", "unsaved")
            self.edit_width(browser, "50 * mm")
            before = self.anchor(cookie, project)
            browser.click("[data-parametric-save]")
            browser.wait("Number(document.querySelector('#parametric-panel').dataset.revision)===" +
                         str(before["revision"] + 1))
            require(browser.evaluate("document.querySelector('[data-parameter=width]').value==='55 * mm' && "
                    "document.querySelector('[data-definition-name]').value==='unsaved'"), "save_erased_drafts")
            actor_state = self.state(cookie, project)
            actor = {"action": "create_configuration", "name": "actor",
                     "definition": {"parameters": {}, "suppressions": {}},
                     "expected_document_sha256": digest(actor_state["doc"].encode()),
                     "expected_revision": actor_state["revision"], "expected_sha": actor_state["sha256"],
                     "request_id": uuid4().hex}
            status, reply = self.http("POST", "/api/parametric", cookie, project, actor)
            require(status == 200, "independent_actor_failed")
            browser.evaluate("window.opencadParametric.refresh(true).catch(e=>window.__draftError=e.message);void 0")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('版本已变化')")
            self.workflow(browser, WORKFLOWS[5],
                browser.evaluate("document.querySelector('[data-parameter=width]').value==='55 * mm' && "
                "document.querySelector('[data-definition-name]').value==='unsaved' && "
                "document.querySelector('[data-definition-create]').disabled"))
            self.discard(browser, accept=True)
            self.discard_definition(browser)
            browser.click("[data-definition-new]")
            self.type(browser, "[data-definition-name]", "resume")
            self.edit_width(browser, "50 * mm")
            self.choose(browser, "[data-definition-suppression=hole_cut]", "suppressed")
            self.choose(browser, "[data-definition-selection]", "target")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-create]")
            self.confirm(browser, cookie, project, before)
            saved = self.geometry(browser, cookie, project, 10000, "saved")
            browser.click("[data-parametric-save]")
            browser.wait("Number(document.querySelector('#parametric-panel').dataset.revision)===" +
                         str(saved["revision"] + 1))
            browser.click("#undo")
            browser.wait("!window.opencad3d.busy")
            undone = self.geometry(browser, cookie, project, (1200-math.pi*36)*12, "undo")
            require("resume" not in json.loads(undone["doc"])["parametric"]["configurations"], "undo_definition_lost")
            browser.click("#redo")
            browser.wait("!window.opencad3d.busy")
            redone = self.geometry(browser, cookie, project, 10000, "redo")
            port = self.cli[-1]["port"]
            self.stop()
            self.start(port)
            browser.evaluate("window.opencad3d.refresh()")
            restarted = self.geometry(browser, cookie, project, 10000, "restart")
            require(redone["doc"] == restarted["doc"] and redone["revision"] == restarted["revision"],
                    "cli_restart_definition_mismatch")
            downloads = browser.out / "downloads"
            downloads.mkdir()
            browser.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads),
                                                          "eventsEnabled": True})
            browser.click("#step")
            deadline = time.monotonic() + 30
            while browser.download_progress is None or browser.download_progress["state"] != "completed":
                require(time.monotonic() < deadline and (browser.download_progress is None or
                        browser.download_progress["state"] != "canceled"), "download_not_completed")
                browser.evaluate("void 0")
                time.sleep(.05)
            files = [path for path in downloads.iterdir() if path.is_file()]
            require(len(files) == 1, "download_file_count")
            witness = browser.evaluate("window.__stepWitness")
            legacy.validate_download(files[0].read_bytes(), witness, browser.download_progress)
            self.downloads[str(width)] = {"file": files[0].relative_to(self.out).as_posix(),
                                         "witness": witness, "progress": browser.download_progress}
            self.workflow(browser, WORKFLOWS[6], True)
            browser.wait("document.querySelector('#parametric-panel').dataset.revision===" +
                         json.dumps(str(restarted["revision"])))
            self.choose(browser, "[data-definition-target]", "resume")
            self.edit_width(browser, "60 * mm")
            self.choose(browser, "[data-definition-selection]", "target")
            before = self.anchor(cookie, project)
            browser.evaluate("window.__lose=true")
            browser.click("[data-definition-replace]")
            browser.wait("document.querySelector('.project-status output').textContent.includes('连接中断')")
            lost_before = self.anchor(cookie, project)
            require(lost_before["revision"] == before["revision"] + 1 and
                    browser.evaluate("document.querySelector('[data-definition-parameter=width]').value==='60 * mm'"),
                    "loss_draft_missing")
            request_id = browser.evaluate("window.__crudRequests.filter(r=>r.body).at(-1).body.request_id")
            browser.click(".project-status button")
            browser.wait("document.querySelector('.project-status output').textContent.includes('最新视图已核对')",
                         timeout=60)
            lost_after = self.anchor(cookie, project)
            require(lost_before == lost_after, "loss_receipt_recommitted")
            self.loss[str(width)] = {"request_id": request_id, "before_replay": lost_before,
                                    "after_replay": lost_after}
            self.discard_definition(browser)
            self.geometry(browser, cookie, project, 14400, "loss")
            browser.evaluate("window.__hold=true;window.__held=false;window.__done=false;"
                "window.opencadParametric.refresh(true).then(()=>window.__done=true);void 0")
            browser.wait("window.__held===true")
            browser.evaluate("window.__release();void 0")
            browser.wait("window.__done===true")
            browser.evaluate("window.__hold=true;window.__held=false;window.__done=false;"
                "window.opencadParametric.refresh(true).then(()=>window.__done=true);void 0")
            browser.wait("window.__held===true")
            self.edit_width(browser, "65 * mm")
            browser.evaluate("window.__release();void 0")
            browser.wait("window.__done===true")
            require(browser.evaluate("document.querySelector('[data-definition-parameter=width]').value==='65 * mm'"),
                    "late_read_erased_draft")
            self.discard_definition(browser)
            # Real outside-browser writer wins; the stale UI must fail CAS with no new receipt.
            actor_state = self.state(cookie, project)
            actor.update(name="race", expected_revision=actor_state["revision"],
                         expected_sha=actor_state["sha256"],
                         expected_document_sha256=digest(actor_state["doc"].encode()), request_id=uuid4().hex)
            status, _ = self.http("POST", "/api/parametric", cookie, project, actor)
            require(status == 200, "cas_actor_failed")
            self.edit_width(browser, "66 * mm")
            anchor = self.anchor(cookie, project)
            browser.click("[data-definition-replace]")
            browser.wait("document.querySelector('.project-status output').textContent.includes('版本冲突')")
            require(self.anchor(cookie, project) == anchor and browser.evaluate(
                "document.querySelector('[data-definition-parameter=width]').value==='66 * mm'"), "stale_cas_changed")
            self.workflow(browser, WORKFLOWS[7], True)
            self.requests[str(width)] = browser.evaluate("window.__crudRequests")
            self.reload(browser)
            self.login(browser, "config-alice", project)
            browser.evaluate("window.__hold=true;window.__held=false;"
                "window.opencadParametric.refresh(true).catch(e=>window.__expired=e.message);void 0")
            browser.wait("window.__held===true")
            self.edit_width(browser, "67 * mm")
            code, reply = self.http("POST", "/api/auth/logout", cookie, body={})
            require(code == 200 and reply["ok"], "logout_actor_failed")
            browser.evaluate("window.__release();void 0")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('登录已失效')")
            require(browser.evaluate("document.querySelectorAll('[data-definition-parameter]').length===0 && "
                    "document.querySelectorAll('[data-parameter]').length===0 && "
                    "!document.querySelector('.parametric-result').dataset.documentSha"), "expired_private_dom_left")
            self.reload(browser)
            self.login(browser, "config-alice", project)
            cookie = self.cookie(browser)
            self.choose(browser, "[data-definition-target]", "resume")
            self.edit_width(browser, "68 * mm")
            before = self.anchor(cookie, project)
            browser.evaluate("window.__holdPost=true;window.__postHeld=false")
            browser.click("[data-definition-replace]")
            browser.wait("window.__postHeld===true")
            committed = self.anchor(cookie, project)
            require(committed["revision"] == before["revision"] + 1 and
                    committed["receipts"] == before["receipts"] + 1, "late_post_not_single_receipt")
            code, reply = self.http("POST", "/api/auth/logout", cookie, body={})
            require(code == 200 and reply["ok"], "late_post_logout_actor_failed")
            browser.evaluate("window.__releasePost();void 0")
            browser.wait("document.querySelector('.parametric-notice').textContent.includes('登录已失效')")
            require(browser.evaluate("document.querySelectorAll('[data-definition-parameter]').length===0 && "
                    "document.querySelectorAll('[data-parameter]').length===0 && "
                    "!document.querySelector('.parametric-result').dataset.documentSha"),
                    "late_post_restored_revoked_private_dom")
            self.requests[str(width)] += browser.evaluate("window.__crudRequests")
            self.reload(browser)
            self.login(browser, "config-bob")
            second_cookie = self.cookie(browser)
            second_project = browser.evaluate("document.querySelector('#parametric-panel').dataset.projectId")
            state = self.state(second_cookie, second_project)
            require(second_project != project and not json.loads(state["doc"])["features"] and
                    browser.evaluate("document.querySelectorAll('[data-definition-parameter]').length===0"),
                    "second_identity_leaked")
            self.workflow(browser, WORKFLOWS[8], True)


def artifact_manifest(out):
    return {path.relative_to(out).as_posix(): {"sha256": digest(path.read_bytes()),
            "bytes": path.stat().st_size} for path in out.rglob("*") if path.is_file() and
            path.relative_to(out).as_posix() not in {
                "result.json", "terminal-candidate.json", "completion.log"}}


def preflight_failure(out):
    cause = traceback.format_exc()
    write(out / "failure.log", cause.encode())
    write(out / "result.json", {"verdict": "FAIL", "intended_rc": 1, "first_cause": cause,
        "phase": "preflight", "finished_at": now(), "artifacts": artifact_manifest(out)})
    write(out / "completion.log", f"{MARKER} verdict=FAIL rc=1\n".encode())
    print(f"{MARKER} verdict=FAIL phase=preflight rc=1 evidence={out}")
    return 1


def main(argv=None):
    global CONTROL_RUNTIME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--browser", default=legacy.DEFAULT_BROWSER)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--verify-evidence", type=Path)
    args = parser.parse_args(argv)
    require(args.source_root.resolve() == ROOT.resolve(), "runner_source_mismatch")
    out = args.evidence_dir.resolve()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()), "external_evidence_required")
    out.mkdir(parents=True, exist_ok=False)
    try:
        before = source()
    except Exception:
        return preflight_failure(out)
    if args.verify_evidence:
        try:
            original = args.verify_evidence.resolve()
            report = json.loads((original / "result.json").read_bytes())
            count = verify_bound(report, original)
            require(json.loads((original / "terminal-candidate.json").read_bytes()) == report,
                    "terminal_candidate_mismatch")
            require((original / "completion.log").read_text() ==
                    f"{MARKER} verdict=PASS checks={count} rc=0\n", "completion_marker_mismatch")
            result = {"verdict": "EVIDENCE_VERIFIED", "intended_rc": 0, "checks": count,
                      "first_cause": None, "source": source(), "finished_at": now(),
                      "original_result_sha256": digest((original / "result.json").read_bytes())}
        except Exception:
            result = {"verdict": "FAIL", "intended_rc": 1, "first_cause": traceback.format_exc()}
            write(out / "failure.log", result["first_cause"].encode())
        write(out / "result.json", result)
        write(out / "completion.log", f"{MARKER} verdict={result['verdict']} rc={result['intended_rc']}\n".encode())
        print(f"{MARKER} verdict={result['verdict']} rc={result['intended_rc']} evidence={out}")
        return result["intended_rc"]
    try:
        base = args.runtime_root
        require(base is not None and base.absolute().is_relative_to(Path("/data/tmp")) and
                all(not path.is_symlink() for path in (base, *base.parents)), "local_runtime_required")
        base.mkdir(parents=True, exist_ok=False)
        runtime = base.absolute() / "run"
        runtime.mkdir()
        filesystem = subprocess.check_output(["stat", "-f", "-c", "%T", str(runtime)], text=True).strip()
        require(not filesystem.startswith("fuse") and filesystem not in {"nfs", "nfs4", "tmpfs", "cifs"},
                "persistent_local_sqlite_required")
    except Exception:
        return preflight_failure(out)
    if args.selftest:
        CONTROL_RUNTIME = runtime
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Controls)
        names = sorted(unittest.defaultTestLoader.getTestCaseNames(Controls))
        with (out / "tests.log").open("x") as log:
            with redirect_stdout(log), redirect_stderr(log):
                tested = unittest.TextTestRunner(stream=log, verbosity=2,
                    resultclass=legacy.EvidenceResult, failfast=True).run(suite)
        after = source()
        ok = tested.wasSuccessful() and tested.testsRun == len(names) > 0 and not tested.skipped and before == after
        result = {"verdict": "PASS" if ok else "FAIL", "intended_rc": 0 if ok else 1,
                  "tests": tested.testsRun, "expected_tests": len(names), "test_names": names,
                  "failures": len(tested.failures), "errors": len(tested.errors), "skipped": len(tested.skipped),
                  "first_cause": tested.first_cause or (None if ok else "incomplete_or_source_drift"),
                  "source_before": before, "source_after": after,
                  "source_drift": [] if before == after else ["source"], "finished_at": now(),
                  "artifacts": artifact_manifest(out)}
        if not ok: write(out / "failure.log", str(result["first_cause"]).encode())
        write(out / "result.json", result)
        write(out / "completion.log", f"{MARKER}_SELFTEST verdict={result['verdict']} tests={tested.testsRun} rc={result['intended_rc']}\n".encode())
        print(f"{MARKER}_SELFTEST verdict={result['verdict']} tests={tested.testsRun} rc={result['intended_rc']} evidence={out}")
        return result["intended_rc"]
    report = {"verdict": "FAIL", "intended_rc": 1, "first_cause": None, "started_at": now(),
              "runtime_root": str(runtime), "runtime_filesystem": filesystem, "production_mutated": False,
              "gpu_used": False, "source_before": before}
    review = None
    try:
        review = Review(ROOT, out, runtime, args.browser)
        review.register()
        for width, height in ((1440, 900), (390, 844)):
            review.start()
            review.scenario(width, height)
            review.stop()
        report.update(verdict="PASS", intended_rc=0)
    except Exception:
        report["first_cause"] = traceback.format_exc()
    finally:
        if review:
            try: review.stop()
            except Exception:
                review.cleanup_errors.append("cli_stop")
                report["first_cause"] = report["first_cause"] or traceback.format_exc()
            try: review.kernel.close()
            except Exception:
                review.cleanup_errors.append("kernel_close")
                report["first_cause"] = report["first_cause"] or traceback.format_exc()
            review.log.close()
        try:
            after = source()
            report.update(source_after=after, source_drift=[] if before == after else ["source"])
        except Exception:
            report.update(source_after=None, source_drift=["source_read_failed"])
            report["first_cause"] = report["first_cause"] or traceback.format_exc()
        report.update(checks=review.checks if review else [], cli=review.cli if review else [],
            cleanup_errors=review.cleanup_errors if review else [], requests=review.requests if review else {},
            actor_requests=review.actor_requests if review else [],
            operations=review.operations if review else {}, loss=review.loss if review else {},
            downloads=review.downloads if review else {}, measurements=review.measurements if review else [],
            finished_at=now())
        if report["first_cause"] or report["source_drift"] or report["cleanup_errors"]:
            report.update(verdict="FAIL", intended_rc=1)
            report["first_cause"] = report["first_cause"] or "source_drift_or_cleanup_failed"
        write(out / "http-ledger.json", {key: report[key] for key in (
            "requests", "actor_requests", "operations", "loss", "measurements")})
        if report["first_cause"]: write(out / "failure.log", report["first_cause"].encode())
        report["artifacts"] = artifact_manifest(out)
        try:
            if review:
                for name in report["artifacts"]:
                    require(not any(secret in (out / name).read_bytes() for secret in review.secrets),
                            "secret_in_evidence:" + name)
                for row in [*review.actor_requests, *(item for rows in review.requests.values() for item in rows)]:
                    data = validate_response(row)
                    require(not any(secret in data for secret in review.secrets), "secret_in_response_evidence")
        except Exception:
            report.update(verdict="FAIL", intended_rc=1,
                          first_cause=report["first_cause"] or traceback.format_exc())
            write(out / "failure.log", report["first_cause"].encode())
            report["artifacts"] = artifact_manifest(out)
        if report["verdict"] == "PASS":
            try:
                write(out / "terminal-candidate.json", report)
                saved = json.loads((out / "terminal-candidate.json").read_bytes())
                require(saved == report, "terminal_roundtrip_mismatch")
                verify_bound(saved, out)
            except Exception:
                report.update(verdict="FAIL", intended_rc=1, first_cause=traceback.format_exc())
                write(out / "failure.log", report["first_cause"].encode())
                report["artifacts"] = artifact_manifest(out)
        write(out / "result.json", report)
        require(json.loads((out / "result.json").read_bytes()) == report, "result_roundtrip_mismatch")
        marker = f"{MARKER} verdict={report['verdict']} checks={len(report['checks'])} rc={report['intended_rc']}\n"
        write(out / "completion.log", marker.encode())
        print(marker.strip() + " evidence=" + str(out))
    return report["intended_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
