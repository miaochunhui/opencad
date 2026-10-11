#!/usr/bin/env python3
"""Independent private surface GET export consumer; see the paired design doc.

Only fresh local fixtures and external evidence are written. No production,
existing account, product edit, dependency installation, GPU or remote operation.
Selftest and preflight are not HTTP or geometry acceptance.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import sys
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest import mock

import trim_loop_private_review as common

ROOT = Path(__file__).resolve().parents[1]
TOOL = "tools/surface_export_private_review.py"
HELPER = "tools/trim_loop_private_review.py"
OWNED = frozenset((TOOL, "docs/design/surface-export-private-review.md"))
SCHEMA = "surface-export-private-ledger-v2"
REAL = "REAL_PRIVATE_SURFACE_GET_DOWNLOAD"
RESERVED = frozenset(("result.json", "producer-terminal-candidate.json"))
FORMATS = ("step", "brep")
STAGES = ("polygon", "saved-polygon", "thickened", "undo-polygon",
          "redo-thickened", "cli-reopened-thickened", "http-opened-thickened")
PROTOCOL = {
    "schema": "surface-export-get-contract-v2",
    "routes": {fmt: "/api/surfaces/" + fmt for fmt in FORMATS},
    "project_header": "X-OpenCAD-Project", "query": "none",
    "get_csrf": "cookie/owner authorization; GET needs no write CSRF marker",
    "empty": {"status": 404, "error": "surface_not_defined"},
    "query_denial": {"status": 400, "error": "bad_arguments"},
    "headers": ["X-OpenCAD-Revision", "X-OpenCAD-Model-Version",
                "X-OpenCAD-Document-SHA256", "X-OpenCAD-Snapshot-SHA256",
                "X-OpenCAD-Artifact-SHA256", "X-OpenCAD-Surface-Recipe-SHA256"],
    "media": {"step": "application/step", "brep": "application/octet-stream"},
    "cache_control": "no-store",
}
stamp, canonical, sha = common.stamp, common.canonical, common.sha
read_json, write_json, valid_sha = common.read_json, common.write_json, common.valid_sha


def check_that(condition, message):
    if not condition:
        raise AssertionError(message)


def source_contract(value):
    check_that(isinstance(value, dict) and set(value) == {
        "root", "head", "status", "manifest", "sha256"}, "Incomplete source binding")
    check_that(type(value["root"]) is str and Path(value["root"]).is_absolute()
               and type(value["head"]) is str and len(value["head"]) == 40
               and not set(value["head"]) - set("0123456789abcdef")
               and type(value["status"]) is str, "Invalid source root/HEAD/status")
    manifest = value["manifest"]
    check_that(isinstance(manifest, dict) and bool(manifest)
               and set(common.REQUIRED) <= set(manifest), "Empty/incomplete source manifest")
    for name, item in manifest.items():
        check_that(type(name) is str and bool(name) and not Path(name).is_absolute()
                   and ".." not in Path(name).parts and isinstance(item, dict)
                   and set(item) == {"sha256", "bytes", "executable"}
                   and valid_sha(item["sha256"]) and type(item["bytes"]) is int
                   and item["bytes"] >= 0 and type(item["executable"]) is bool,
                   "Invalid source path/byte/mode entry")
    check_that(valid_sha(value["sha256"]) and sha(canonical(manifest)) == value["sha256"],
               "Source manifest digest mismatch")


def source_equal(before, after, reference):
    for value in (before, after, reference):
        source_contract(value)
    check_that(canonical(before) == canonical(reference)
               and canonical(after) == canonical(reference),
               "FULL_SOURCE_HEAD_BYTE_MODE_SET_MISMATCH")


def executor_scope(binding):
    check_that(not set(common.dirty_paths(binding)) - OWNED,
               "Executor has non-owned dirty paths")


def selected_binding(source):
    result = common.source_binding(source)
    source_contract(result)
    return result


def sql_cell(value):
    return {"type": type(value).__name__,
            "value": value.hex() if isinstance(value, bytes) else value}


def database_anchor(store):
    """All SQLite tables, schema and typed rows, not a frozen table count."""
    with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                            "ORDER BY type,name,tbl_name,sql").fetchall()
        names = [row[1] for row in schema if row[0] == "table"]
        check_that(set(common.TABLES) <= set(names), "Empty/incomplete ProjectStore schema")
        tables = {}
        for name in names:
            quoted = chr(34) + name.replace(chr(34), chr(34) * 2) + chr(34)
            rows = [[sql_cell(cell) for cell in row]
                    for row in db.execute("SELECT * FROM " + quoted)]
            tables[name] = sorted(rows, key=canonical)
    return {"sha256": sha(canonical({"schema": schema, "tables": tables})),
            "tables": sorted(tables), "counts": {name: len(rows) for name, rows in tables.items()}}


def database_contract(anchor):
    check_that(isinstance(anchor, dict) and set(anchor) == {"sha256", "tables", "counts"}
               and valid_sha(anchor["sha256"]) and type(anchor["tables"]) is list
               and all(type(name) is str for name in anchor["tables"])
               and anchor["tables"] == sorted(set(anchor["tables"]))
               and set(common.TABLES) <= set(anchor["tables"])
               and isinstance(anchor["counts"], dict)
               and set(anchor["counts"]) == set(anchor["tables"])
               and all(type(value) is int and value >= 0 for value in anchor["counts"].values()),
               "Empty/incomplete/invalid complete database anchor")


def fixture(stage):
    solid = "thickened" in stage
    spec = common.fixture_specs()[1 if solid else 0]
    return {"name": stage, "area": None if solid else 50,
            "volume": 100 if solid else None, "signature": spec["signature"],
            "witnesses": json.loads(canonical(spec["witnesses"])),
            "recipe": ({"op": "thicken", "surface": common.polygon_recipe(),
                        "thickness_nm": 2_000_000, "inward": False}
                       if solid else common.polygon_recipe())}


def denials():
    for fmt in FORMATS:
        route = PROTOCOL["routes"][fmt]
        for label, user, overrides, status, error in (
            ("anonymous", None, {}, 401, "missing_token"),
            ("forged", None, {"Cookie": "opencad_session=forged"}, 401, "invalid_token"),
            ("other-owner", "other", {}, 403, "project_forbidden"),
            ("project-missing", "owner", {}, 400, "project_required"),
        ):
            yield {"name": fmt + "-" + label, "route": route, "user": user,
                   "overrides": overrides, "status": status, "error": error,
                   "no_project": label == "project-missing"}
        for label, query in (("query-format", "format=step"), ("empty-format", "format="),
                             ("malicious-format", "format=..%2F..%2Fetc%2Fpasswd"),
                             ("query-path", "path=%2Fetc%2Fpasswd"),
                             ("query-version", "model_version=0"),
                             ("duplicate-query", "format=step&format=brep")):
            yield {"name": fmt + "-" + label, "route": route + "?" + query,
                   "user": "owner", "overrides": {}, **PROTOCOL["query_denial"]}
        yield {"name": fmt + "-empty", "route": route, "user": "owner",
               "overrides": {}, "empty_project": True, **PROTOCOL["empty"]}


def ledger():
    """Exact workflow identities come from one declaration, never old counts."""
    checks, requests, exports, geometry = [], [], [], []

    def request(name, route, method="GET", status=200, envelope=True):
        requests.append({"name": name, "route": route, "method": method, "status": status})
        checks.append(name + "/response-bounded")
        if envelope:
            checks.append(name + "/http-contract")

    def stage(name):
        request(name + "-state", "/api/state")
        checks.append(name + "/canonical-durable-state")
        for fmt in FORMATS:
            label = name + "/" + fmt
            request(label, PROTOCOL["routes"][fmt], envelope=False)
            checks.extend((label + "/download-contract", label + "/all-tables-unchanged"))
            exports.append(label)
            geometry.extend(label + "/" + suffix for suffix in (
                "bytes-sha", "valid", "holes-wires", "analytic-shape",
                "witness-0", "witness-1", "witness-2", "witness-3"))

    checks.extend(("fixture/analytic-area", "fixture/all-project-tables-empty",
                   "initial/original-cli-ready"))
    for user in ("owner", "other"):
        request("login-" + user, "/api/auth/login", "POST")
        checks.append("login-" + user + "/private-cookie-identity")
    for name in ("create", "create-empty"):
        request(name, "/api/projects", "POST", 201)
    checks.append("fixture/project-owner-and-isolation")
    for item in denials():
        request(item["name"], item["route"], status=item["status"], envelope=False)
        checks.extend((item["name"] + "/safe-exact-denial", item["name"] + "/all-tables-unchanged"))
    for name, overrides in (("csrf-missing", {"X-OpenCAD-Request": None}),
                            ("csrf-origin", {"Origin": "https://example.invalid"})):
        request(name, "/api/surfaces", "POST", 403, envelope=False)
        checks.extend((name + "/safe-exact-denial", name + "/all-tables-unchanged"))
    request("polygon-post", "/api/surfaces", "POST")
    checks.append("polygon-post/durable-receipt")
    stage("polygon")
    request("save", "/api/save", "POST")
    checks.append("save/durable-receipt")
    stage("saved-polygon")
    checks.append("save/document-history-identical")
    request("thicken-post", "/api/surfaces", "POST")
    checks.append("thicken-post/durable-receipt")
    stage("thickened")
    for action, name in (("undo", "undo-polygon"), ("redo", "redo-thickened")):
        request(action, "/api/command", "POST")
        checks.append(action + "/durable-receipt")
        stage(name)
        checks.append(action + "/document-history-identical")
    request("download-document", "/api/document", envelope=False)
    checks.extend(("download/exact-durable-document", "initial/original-cli-rc",
                   "reopen/original-cli-ready"))
    stage("cli-reopened-thickened")
    checks.append("restart/all-project-tables-and-history-identical")
    request("open-document", "/api/open", "POST")
    checks.append("open-document/durable-receipt")
    stage("http-opened-thickened")
    checks.extend(("open/document-history-reset", "reopen/original-cli-rc",
                   "readback/measured-cli-terminal", "readback/exact-geometry-ledger",
                   "source/exact-head-byte-mode-set", "executor/exact-head-byte-mode-set"))
    geometry.append("negative-control/rectangle-fills-hole-and-notch")
    return {"schema": SCHEMA, "protocol": PROTOCOL, "checks": checks,
            "http_requests": requests, "exports": exports,
            "states": list(STAGES), "fixtures": [fixture(stage) for stage in STAGES],
            "geometry_checks": geometry}


def counts(report):
    checks = report.get("checks", [])
    geometry = report.get("readback", {}).get("checks", [])
    return {"checks": len(checks), "passed": sum(item.get("passed") is True for item in checks),
            "http": len(report.get("http_requests", [])),
            "exports": len(report.get("exports", [])), "geometry": len(geometry),
            "geometry_passed": sum(item.get("passed") is True for item in geometry)}


def headers_contract(headers, version, raw, fmt, recipe):
    public = {key.lower(): value for key, value in headers.items()}
    expected = {"x-opencad-revision": str(version["revision"]),
                "x-opencad-model-version": str(version["revision"]),
                "x-opencad-document-sha256": version["document_sha256"],
                "x-opencad-snapshot-sha256": version["sha256"],
                "x-opencad-artifact-sha256": sha(raw),
                "x-opencad-surface-recipe-sha256": sha(canonical(recipe)),
                "cache-control": PROTOCOL["cache_control"]}
    check_that(type(raw) is bytes and bool(raw)
               and all(public.get(key) == value for key, value in expected.items()),
               "Download empty or version/document/snapshot/artifact header mismatch")
    media = public.get("content-type", "").split(";", 1)[0]
    check_that(media == PROTOCOL["media"][fmt],
               "Download content type is not a declared CAD format")
    check_that(public.get("content-length") == str(len(raw))
               and public.get("content-disposition") == f"attachment; filename=\"surface.{fmt}\"",
               "Download length/disposition mismatch")
    check_that(not any(marker in canonical(public) for marker in (
        "/data/", "/hf3fs", "/3fs/", "/tmp/", "Traceback", "surface-jobs")),
        "Private path or traceback leaked in response headers")


def safe_denial(code, data, raw, headers, expected):
    check_that(code == expected["status"] and isinstance(data, dict)
               and data.get("ok") is False and data.get("error") == expected["error"],
               "HTTP exact denial mismatch")
    check_that(not any(marker in raw.decode("utf-8", errors="replace") for marker in (
        "/data/", "/hf3fs", "/3fs/", "/tmp/", "Traceback", "surface-jobs",
        "root:x:", "BEGIN PRIVATE KEY")), "Private path/content leaked by error response")
    check_that(not any(key.lower() == "content-disposition" for key in headers),
               "Error response has a success attachment header")


def identity_sequence(items, names, label):
    actual = [item["name"] for item in items]
    check_that(actual == names and len(set(actual)) == len(actual),
               "Incomplete/duplicate/renamed/out-of-order ledger " + label)


def terminal_contract(report, selected, executor):
    plan = ledger()
    check_that(report.get("schema") == SCHEMA and report.get("verdict") == "PASS"
               and type(report.get("rc")) is int and report["rc"] == 0
               and report.get("acceptance") == REAL and not report.get("first_cause")
               and report.get("finished_at") and report.get("started_at")
               and canonical(report.get("ledger")) == canonical(plan)
               and report.get("synthetic") is False,
               "Not a complete real terminal suite/declaration")
    source_equal(report["source_before"], report["source_after"], selected)
    source_equal(report["executor_before"], report["executor_after"], executor)
    executor_scope(executor)
    check_that(selected["status"] == "" and executor["status"] == "",
               "Real terminal source/executor must be clean")
    check_that(report.get("source_drift") is False and report.get("executor_drift") is False,
               "Source or executor drift")
    check_that(executor["manifest"][TOOL]["sha256"] == report["tool_sha256"]
               and executor["manifest"][HELPER]["sha256"] == report["helper_sha256"],
               "Executor dependency/source binding mismatch")
    identity_sequence(report["checks"], plan["checks"], "checks")
    check_that(all(item["passed"] is True and item.get("at") for item in report["checks"]),
               "Failed or unmeasured checks")
    identity_sequence(report["http_requests"], [item["name"] for item in plan["http_requests"]], "HTTP")
    for actual, expected in zip(report["http_requests"], plan["http_requests"]):
        check_that(all(type(actual[key]) is type(value) and actual[key] == value
                       for key, value in expected.items()), "HTTP route/method/status ledger mismatch")
    identity_sequence(report["states"], plan["states"], "states")
    states = {item["name"]: item for item in report["states"]}
    for name, item in states.items():
        version = item["version"]
        check_that(type(version.get("project_id")) is str and bool(version["project_id"])
                   and type(version.get("revision")) is int and version["revision"] > 0
                   and valid_sha(version.get("sha256")) and valid_sha(version.get("document_sha256")),
                   "Invalid project/version/document binding")
    identity_sequence(report["exports"], plan["exports"], "exports")
    for item in report["exports"]:
        database_contract(item["db_before"])
        database_contract(item["db_after"])
        name, fmt = item["name"].split("/")
        check_that(item["format"] == fmt and canonical(item["fixture"]) == canonical(fixture(name))
                   and item["version"] == states[name]["version"]
                   and item["db_before"] == item["db_after"]
                   and valid_sha(item["artifact_sha256"]) and type(item["bytes"]) is int
                   and item["bytes"] > 0, "Export artifact/fixture/version/database mismatch")
    processes = report["cli_processes"]
    check_that([item["label"] for item in processes] == ["initial", "reopen"]
               and all(type(item.get("rc")) is int and item["rc"] == 0
                       and item.get("ready_at") and item.get("finished_at")
                       and not item.get("shutdown_error") for item in processes),
               "Missing/failed/unmeasured original CLI lifecycle")
    execution = report["readback_execution"]
    check_that(type(execution.get("rc")) is int and execution["rc"] == 0
               and execution.get("rc_measured") is True and execution.get("timed_out") is False
               and not execution.get("launch_error") and execution.get("finished_at"),
               "Missing/failed/unmeasured FreeCAD readback CLI")
    readback = report["readback"]
    check_that(readback.get("ok") is True and readback.get("backend"), "Missing real readback")
    identity_sequence(readback["checks"], plan["geometry_checks"], "geometry checks")
    identity_sequence(readback["shapes"], plan["exports"], "shapes")
    check_that(all(item["passed"] is True for item in readback["checks"]), "Failed geometry check")
    for shape, exported in zip(readback["shapes"], report["exports"]):
        spec = exported["fixture"]
        measurement = shape["volume_mm3"] if spec["volume"] else shape["area_mm2"]
        check_that(type(measurement) in (int, float) and math.isfinite(measurement)
                   and abs(measurement - (spec["volume"] or spec["area"])) < 1e-6
                   and shape["hole_signature"] == spec["signature"]
                   and shape["version"] == exported["version"], "Readback analytic/version mismatch")
        check_that(len(shape["witnesses"]) == len(spec["witnesses"]), "Missing world witnesses")
        for witness, (point, expected) in zip(shape["witnesses"], spec["witnesses"]):
            check_that(witness["point_mm"] == point and witness["expected"] is expected
                       and witness["actual"] is expected
                       and type(witness["distance_mm"]) in (int, float)
                       and math.isfinite(witness["distance_mm"]) and witness["distance_mm"] >= 0,
                       "World hole/notch witness mismatch")
    check_that(report["counts"] == counts(report), "Measured counts do not reconcile")
    return counts(report)


def evidence_file(root, relative):
    check_that(type(relative) is str and relative and not Path(relative).is_absolute()
               and ".." not in Path(relative).parts, "Foreign evidence path")
    path = root / relative
    check_that(not any(part.is_symlink() for part in (path, *path.parents)
                       if part.is_relative_to(root)), "Linked evidence path")
    check_that(path.is_file() and stat.S_ISREG(path.stat().st_mode),
               "Missing/nonregular evidence file: " + relative)
    return path


def inventory(out):
    result = {}
    for path in sorted(out.rglob("*")):
        check_that(not path.is_symlink(), "Linked evidence inventory entry")
        if path.is_file() and path.relative_to(out).as_posix() not in RESERVED:
            check_that(stat.S_ISREG(path.stat().st_mode), "Nonregular evidence inventory entry")
            raw = path.read_bytes()
            result[path.relative_to(out).as_posix()] = {"bytes": len(raw), "sha256": sha(raw)}
    return result


def saved_candidate(out, report, validate):
    path = out / "producer-terminal-candidate.json"
    write_json(path, report)
    loaded = read_json(path)
    check_that(canonical(loaded) == canonical(report), "Producer serialized content changed")
    validate(loaded)
    return {"schema": SCHEMA, "verified": True, "candidate": path.name,
            "sha256": sha(path.read_bytes())}


def publish(out, report, selected, executor, redact):
    """Save, read and validate the actual complete candidate before final PASS."""
    try:
        if report.get("synthetic") is True:
            check_that(report["acceptance"] != REAL, "Synthetic fixture cannot claim real acceptance")
        if report["verdict"] == "PASS" and report["acceptance"] == REAL:
            check_that("producer_save_load" not in report, "Prepopulated producer gate")
            candidate = json.loads(redact(canonical(report)))

            def validate(loaded):
                terminal_contract(loaded, selected, executor)
                evidence_contract(Path(selected["root"]), out, loaded)

            report["producer_save_load"] = saved_candidate(out, candidate, validate)
    except Exception as exc:
        report.update(verdict="FAIL", rc=1, failure_type=type(exc).__name__)
        report.setdefault("first_cause", redact(traceback.format_exc()))
        write_json(out / "publication-failure.json", {"rc": 1, "first_cause": report["first_cause"]})
    write_json(out / "result.json", json.loads(redact(canonical(report))))


READBACK = """import FreeCAD as App
import Part
import hashlib
import json
import os
import traceback
from pathlib import Path
result = {"ok": False, "checks": [], "shapes": []}
def check(name, condition):
    result["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        raise AssertionError(name)
try:
    result["backend"] = {"freecad": list(App.Version()), "occt": Part.OCC_VERSION}
    document = json.loads(Path(os.environ["SURFACE_EXPORT_INPUT"]).read_text())
    root = Path(document["evidence_root"])
    for item in document["exports"]:
        name, spec = item["name"], item["fixture"]
        path = root / item["artifact"]
        check(name + "/bytes-sha", path.stat().st_size == item["bytes"] > 0
              and hashlib.sha256(path.read_bytes()).hexdigest() == item["artifact_sha256"])
        shape = Part.Shape()
        if item["format"] == "brep":
            shape.importBrep(str(path))
        else:
            shape = Part.read(str(path))
        check(name + "/valid", shape.isValid() and bool(shape.Faces))
        signature = sorted(len(face.Wires) - 1 for face in shape.Faces)
        check(name + "/holes-wires", signature == spec["signature"]
              and all(wire.isClosed() for face in shape.Faces for wire in face.Wires))
        solid = spec["volume"] is not None
        check(name + "/analytic-shape", (len(shape.Solids) == 1 and shape.isClosed()
              and abs(shape.Volume - spec["volume"]) < 1e-6) if solid
              else (not shape.Solids and abs(shape.Area - spec["area"]) < 1e-6))
        witnesses = []
        for index, (point, expected) in enumerate(spec["witnesses"]):
            p = App.Vector(*point)
            distance = shape.distToShape(Part.Vertex(p))[0]
            actual = shape.isInside(p, 1e-7, False) if solid else distance < 1e-7
            witnesses.append({"point_mm": point, "actual": actual,
                              "expected": expected, "distance_mm": distance})
            check(name + "/witness-" + str(index), actual == expected)
        result["shapes"].append({"name": name, "area_mm2": shape.Area,
            "volume_mm3": shape.Volume if solid else None, "hole_signature": signature,
            "witnesses": witnesses, "version": item["version"]})
    rectangle = Part.makePlane(8, 8, App.Vector(1, 1, 0))
    check("negative-control/rectangle-fills-hole-and-notch", all(
        rectangle.distToShape(Part.Vertex(App.Vector(*point)))[0] < 1e-7
        for point in ([2.5, 2.5, 0], [4.3, 2.5, 0], [7, 7, 0])))
    plan = document["ledger"]
    if ([item["name"] for item in result["checks"]] != plan["geometry_checks"]
            or [item["name"] for item in result["shapes"]] != plan["exports"]):
        raise AssertionError("Incomplete or reordered geometry ledger")
    result["ok"] = True
except Exception:
    result["first_cause"] = traceback.format_exc()
    print(result["first_cause"], flush=True)
Path(os.environ["SURFACE_EXPORT_RESULT"]).write_text(json.dumps(result, indent=2))
print("SURFACE_EXPORT_READBACK verdict=" + ("PASS" if result["ok"] else "FAIL"), flush=True)
raise SystemExit(0 if result["ok"] else 3)
"""


def readback_terminal(outcome):
    return (type(outcome.rc) is int and outcome.rc == 0 and outcome.rc_measured is True
            and outcome.timed_out is False and not outcome.launch_error
            and "SURFACE_EXPORT_READBACK verdict=PASS" in outcome.stdout.splitlines())


def run_readback(source, runtime, review):
    out, report = review.out, review.report
    write_json(out / "readback-input.json", {"evidence_root": str(out), "exports": report["exports"],
                                            "ledger": ledger()})
    script = out / "download-readback.py"
    with script.open("x", encoding="utf-8") as handle:
        handle.write(READBACK)
    _, _, _, _, subproc = common.import_product(source)
    env = common.product_env(source, runtime)
    env.update(SURFACE_EXPORT_INPUT=str(out / "readback-input.json"),
               SURFACE_EXPORT_RESULT=str(out / "readback.json"))
    executable = subproc.find_executable()
    argv = [executable, str(script)]
    execution = {"argv": argv, "started_at": stamp(), "executable_sha256": sha(Path(executable).read_bytes())}
    report["readback_execution"] = execution
    outcome = subproc._run(argv, cwd=str(runtime), env=env, timeout_s=90)
    for name, text in (("readback-stdout.log", outcome.stdout), ("readback-stderr.log", outcome.stderr)):
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(text)
    execution.update(rc=outcome.rc, rc_measured=outcome.rc_measured,
                     timed_out=outcome.timed_out, launch_error=outcome.launch_error, finished_at=stamp())
    if (out / "readback.json").is_file():
        report["readback"] = read_json(out / "readback.json")
    review.check("readback/measured-cli-terminal", readback_terminal(outcome), execution=execution)
    report["readback"] = read_json(out / "readback.json")
    review.check("readback/exact-geometry-ledger", report["readback"]["ok"] is True
                 and [item["name"] for item in report["readback"]["checks"]] == ledger()["geometry_checks"]
                 and all(item["passed"] is True for item in report["readback"]["checks"]))


def diagnose_readback(evidence, runtime, review):
    """Paired real FreeCAD replay of retained downloads, never HTTP or publication."""
    original = read_json(evidence_file(evidence, "result.json"))
    check_that(original["verdict"] == "FAIL" and original["rc"] == 1
               and "readback/measured-cli-terminal" in original["first_cause"],
               "Diagnosis requires the retained original terminal-marker failure")
    check_that(inventory(evidence) == original["files"], "Original evidence closure changed")
    original_script = evidence_file(evidence, "download-readback.py").read_text()
    unflushed = READBACK.replace('print(result["first_cause"], flush=True)',
                                'print(result["first_cause"])').replace(
        'else "FAIL"), flush=True)', 'else "FAIL"))')
    check_that(original_script == unflushed, "Original script differs beyond the explicit flush fix")
    inputs = read_json(evidence_file(evidence, "readback-input.json"))
    check_that(inputs == {"evidence_root": str(evidence), "exports": original["exports"],
                          "ledger": ledger()}, "Original readback input detached")
    for item in inputs["exports"]:
        raw = evidence_file(evidence, item["artifact"]).read_bytes()
        check_that(len(raw) == item["bytes"] and sha(raw) == item["artifact_sha256"],
                   "Original download bytes changed")
    baseline = inventory(evidence)
    _, _, _, _, subproc = common.import_product(ROOT)
    executable = subproc.find_executable()
    check_that(executable and Path(executable).is_file(), "No existing diagnostic FreeCAD executable")
    executions = []
    review.report["diagnosis"] = {"original_result": str(evidence / "result.json"),
        "original_result_sha256": sha((evidence / "result.json").read_bytes()),
        "referenced_downloads": len(inputs["exports"]), "executions": executions}
    for label, script_text in (("original-unflushed", original_script), ("fixed-flushed", READBACK)):
        out = review.out / label
        out.mkdir(exist_ok=False)
        script = out / "download-readback.py"
        with script.open("x") as handle:
            handle.write(script_text)
        write_json(out / "readback-input.json", inputs)
        env = common.product_env(ROOT, runtime)
        env.update(SURFACE_EXPORT_INPUT=str(out / "readback-input.json"),
                   SURFACE_EXPORT_RESULT=str(out / "readback.json"))
        argv = [executable, str(script)]
        execution = {"label": label, "argv": argv, "started_at": stamp(),
            "executable_sha256": sha(Path(executable).read_bytes()),
            "script_sha256": sha(script.read_bytes())}
        executions.append(execution)
        outcome = subproc._run(argv, cwd=str(runtime), env=env, timeout_s=90)
        for name, text in (("readback-stdout.log", outcome.stdout), ("readback-stderr.log", outcome.stderr)):
            with (out / name).open("x") as handle:
                handle.write(text)
        execution.update(rc=outcome.rc, rc_measured=outcome.rc_measured,
                         timed_out=outcome.timed_out, launch_error=outcome.launch_error,
                         finished_at=stamp(), terminal_gate=readback_terminal(outcome))
        payload = read_json(evidence_file(out, "readback.json"))
        execution["readback"] = payload
        check_that(payload["ok"] is True and not payload.get("first_cause")
                   and [item["name"] for item in payload["checks"]] == ledger()["geometry_checks"]
                   and all(item["passed"] is True for item in payload["checks"])
                   and [item["name"] for item in payload["shapes"]] == ledger()["exports"],
                   "Diagnostic full geometry/readback failed")
        expected_gate = label == "fixed-flushed"
        review.check("diagnosis/" + label, outcome.rc == 0 and outcome.rc_measured
                     and not outcome.timed_out and not outcome.launch_error
                     and readback_terminal(outcome) is expected_gate,
                     measured_rc=outcome.rc, terminal_gate=readback_terminal(outcome),
                     geometry=len(payload["checks"]), shapes=len(payload["shapes"]))
    review.check("diagnosis/original-evidence-unchanged", inventory(evidence) == baseline)


def workflow(source, runtime, review):
    report = review.report
    report.update(ledger=ledger(), exports=[], states=[])
    access, stores, docmodel, _, _ = common.import_product(source)
    credentials = {name: secrets.token_hex(24) for name in ("owner", "other")}
    review.private_values.extend(credentials.values())
    auth = access.AuthController(runtime / "accounts", allow_registration=True)
    principals = {}
    try:
        for name, credential in credentials.items():
            principals[name] = auth.register(name, credential)
    finally:
        auth.close()
    store = stores.ProjectStore(runtime / "projects")
    cookies = {}
    review.check("fixture/analytic-area", abs(common.analytic_area(common.polygon_recipe()) - 50) < 1e-10)
    review.check("fixture/all-project-tables-empty", all(count == 0 for count in database_anchor(store)["counts"].values()))

    def reject(http, item, head, body=None):
        before = database_anchor(store)
        code, headers, data, raw = http.call(item["name"], item["route"], body,
            user=item["user"], project=None if item.get("no_project") else head["project_id"],
            overrides=item["overrides"])
        safe_denial(code, data, raw, headers, item)
        review.check(item["name"] + "/safe-exact-denial", True)
        after = database_anchor(store)
        review.check(item["name"] + "/all-tables-unchanged", before == after, before=before, after=after)

    def write(http, name, route, head, **fields):
        body = common.edit(head, **fields)
        result = http.json(name, route, body, project=head["project_id"])[0]
        receipt = store.read_receipt(head["project_id"], body["request_id"],
                                     request_payload={**body, "route": route})
        review.check(name + "/durable-receipt", receipt is not None
                     and all(receipt[key] == result[key] for key in ("project_id", "revision", "sha256"))
                     and {**receipt["response"], **{key: receipt[key] for key in (
                         "project_id", "revision", "sha256")}} == result)
        return result

    def capture(http, stage, head):
        before = database_anchor(store)
        state = http.json(stage + "-state", "/api/state", project=head["project_id"])[0]
        durable = store.read_head(head["project_id"])
        document = docmodel.document_from_l1(state["doc"])
        spec = fixture(stage)
        review.check(stage + "/canonical-durable-state", state["doc"] == durable["snapshot"]["doc"]
                     and all(state[key] == durable[key] == head[key]
                             for key in ("project_id", "revision", "sha256"))
                     and docmodel.document_to_l1(document) == state["doc"]
                     and document.extra["x_industrial_surfaces"] == {"schema": 1, "recipe": spec["recipe"]}
                     and database_anchor(store) == before)
        version = {key: head[key] for key in ("project_id", "revision", "sha256")}
        version["document_sha256"] = sha(state["doc"])
        report["states"].append({"name": stage, "version": version, "doc": state["doc"],
                                 "undo_depth": state["undo_depth"], "redo_depth": state["redo_depth"]})
        for fmt in FORMATS:
            label = stage + "/" + fmt
            anchor = database_anchor(store)
            code, headers, _, raw = http.call(label, PROTOCOL["routes"][fmt], project=head["project_id"],
                overrides={"Origin": None, "X-OpenCAD-Request": None})
            check_that(code == 200, "Surface GET download failed: " + label + " status=" + str(code))
            headers_contract(headers, version, raw, fmt, spec["recipe"])
            review.check(label + "/download-contract", True, bytes=len(raw), sha256=sha(raw), version=version)
            after = database_anchor(store)
            review.check(label + "/all-tables-unchanged", anchor == after
                         and store.read_head(head["project_id"]) == durable, before=anchor, after=after)
            path = "download-" + stage + "." + fmt
            with (review.out / path).open("xb") as handle:
                handle.write(raw)
            report["exports"].append({"name": label, "format": fmt, "artifact": path,
                "bytes": len(raw), "artifact_sha256": sha(raw), "version": version, "fixture": spec,
                "http_index": len(report["http_requests"]), "db_before": anchor, "db_after": after})
        return state

    try:
        with common.secure_cli(source, runtime, review, "initial") as (port, origin):
            http = common.HTTP(port, origin, review, cookies)
            for name in credentials:
                login, headers, _ = http.json("login-" + name, "/api/auth/login",
                    {"username": name, "password": credentials[name]}, user=None)
                cookie = headers.get("Set-Cookie", "")
                review.private_values.extend((cookie, cookie.split(";", 1)[0]))
                review.check("login-" + name + "/private-cookie-identity", login["user"] == principals[name]
                             and cookie.startswith("opencad_session=") and "HttpOnly" in cookie
                             and "SameSite=Strict" in cookie)
                cookies[name] = cookie.split(";", 1)[0]
            head = http.json("create", "/api/projects", {"title": "Surface export private consumer",
                "request_id": secrets.token_hex(16), "standard": "gbt"}, status=201)[0]
            empty = http.json("create-empty", "/api/projects", {"title": "Empty surface negative control",
                "request_id": secrets.token_hex(16), "standard": "gbt"}, status=201)[0]
            review.check("fixture/project-owner-and-isolation", head["project_id"] != empty["project_id"]
                         and store.read_head(head["project_id"])["owner"] == principals["owner"]["user_id"])
            for item in denials():
                reject(http, item, empty if item.get("empty_project") else head)
            for name, overrides in (("csrf-missing", {"X-OpenCAD-Request": None}),
                                    ("csrf-origin", {"Origin": "https://example.invalid"})):
                reject(http, {"name": name, "route": "/api/surfaces", "user": "owner",
                    "overrides": overrides, "status": 403, "error": "cross_site_request"},
                    head, common.edit(head, recipe=common.polygon_recipe()))
            head = write(http, "polygon-post", "/api/surfaces", head, recipe=common.polygon_recipe())
            polygon = capture(http, "polygon", head)
            head = write(http, "save", "/api/save", head)
            saved = capture(http, "saved-polygon", head)
            review.check("save/document-history-identical", saved["doc"] == polygon["doc"]
                         and (saved["undo_depth"], saved["redo_depth"]) == (1, 0))
            head = write(http, "thicken-post", "/api/surfaces", head, recipe=fixture("thickened")["recipe"])
            thick = capture(http, "thickened", head)
            head = write(http, "undo", "/api/command", head, command="undo", args={})
            undone = capture(http, "undo-polygon", head)
            review.check("undo/document-history-identical", undone["doc"] == saved["doc"]
                         and (undone["undo_depth"], undone["redo_depth"]) == (1, 1))
            head = write(http, "redo", "/api/command", head, command="redo", args={})
            redone = capture(http, "redo-thickened", head)
            review.check("redo/document-history-identical", redone["doc"] == thick["doc"]
                         and (redone["undo_depth"], redone["redo_depth"]) == (2, 0))
            code, headers, _, raw = http.call("download-document", "/api/document", project=head["project_id"])
            review.check("download/exact-durable-document", code == 200 and raw.decode() == redone["doc"]
                         and headers.get("X-OpenCAD-Document-SHA256") == sha(raw)
                         and headers.get("X-OpenCAD-Revision") == str(head["revision"]))
            with (review.out / "saved-document.l1").open("xb") as handle:
                handle.write(raw)
            restart_db = database_anchor(store)
        review.check("initial/original-cli-rc", report["cli_processes"][-1]["rc"] == 0)
        with common.secure_cli(source, runtime, review, "reopen") as (port, origin):
            http = common.HTTP(port, origin, review, cookies)
            reopened = capture(http, "cli-reopened-thickened", head)
            review.check("restart/all-project-tables-and-history-identical", reopened == redone
                         and database_anchor(store) == restart_db)
            head = write(http, "open-document", "/api/open", head, doc=raw.decode())
            opened = capture(http, "http-opened-thickened", head)
            review.check("open/document-history-reset", opened["doc"] == redone["doc"]
                         and (opened["undo_depth"], opened["redo_depth"]) == (0, 0))
            report["final_db"] = database_anchor(store)
        review.check("reopen/original-cli-rc", report["cli_processes"][-1]["rc"] == 0)
        report["runtime_origins"] = common.runtime_origins(source)
        run_readback(source, runtime, review)
    finally:
        store.close()


def evidence_contract(source, evidence, report):
    """One physical-evidence oracle for both real publication and offline reading."""
    check_that(inventory(evidence) == report["files"] and bool(report["files"]),
               "Evidence full file set/byte/hash closure mismatch")
    database_contract(report["final_db"])
    for item in report["exports"]:
        database_contract(item["db_before"])
        database_contract(item["db_after"])
    checks = [json.loads(line) for line in evidence_file(evidence, "checks.jsonl").read_text().splitlines()]
    check_that(canonical(checks) == canonical(report["checks"]), "Checks log/report mismatch")
    for index, request in enumerate(report["http_requests"], 1):
        if request["route"] == "/api/auth/login":
            check_that("body" not in request and "response_sha256" not in request and "headers" not in request,
                       "Login secret-bearing response retained")
            continue
        raw = evidence_file(evidence, "http-%03d.json" % index).read_bytes()
        check_that(len(raw) == request["bytes"] and sha(raw) == request["response_sha256"],
                   "HTTP raw response binding mismatch")
    for item in report["exports"]:
        raw = evidence_file(evidence, item["artifact"]).read_bytes()
        check_that(type(item["http_index"]) is int
                   and 1 <= item["http_index"] <= len(report["http_requests"]),
                   "Export HTTP index is absent/invalid")
        request = report["http_requests"][item["http_index"] - 1]
        check_that(request["name"] == item["name"] and request["method"] == "GET"
                   and request["status"] == 200 and len(raw) == item["bytes"]
                   and sha(raw) == item["artifact_sha256"] == request["response_sha256"],
                   "Export is detached from actual HTTP download bytes")
        headers_contract(request["headers"], item["version"], raw, item["format"], item["fixture"]["recipe"])
    for process in report["cli_processes"]:
        log = evidence_file(evidence, Path(process["log"]).name)
        port = process["argv"][process["argv"].index("--port") + 1]
        check_that(sha(log.read_bytes()) == process["log_sha256"]
                   and "OPENCAD_PRIVATE_WEB verdict=READY port=" + port in log.read_text()
                   and str(source / common.APP_REL / "secure_web.py") in process["argv"],
                   "Original CLI source/log/ready marker mismatch")
    check_that(canonical(read_json(evidence_file(evidence, "readback.json"))) == canonical(report["readback"])
               and evidence_file(evidence, "download-readback.py").read_text() == READBACK
               and "SURFACE_EXPORT_READBACK verdict=PASS" in
               evidence_file(evidence, "readback-stdout.log").read_text().splitlines(),
               "Readback script/result/terminal mismatch")
    inputs = read_json(evidence_file(evidence, "readback-input.json"))
    check_that(inputs == {"evidence_root": str(evidence), "exports": report["exports"], "ledger": ledger()},
               "Readback input detached from actual exported HTTP artifacts")
    argv = report["readback_execution"]["argv"]
    check_that(type(argv) is list and len(argv) == 2
               and argv[1] == str(evidence / "download-readback.py")
               and valid_sha(report["readback_execution"]["executable_sha256"]),
               "Readback CLI argv/executable binding missing")
    document = evidence_file(evidence, "saved-document.l1").read_bytes()
    check_that(document.decode() == next(item["doc"] for item in report["states"]
                                         if item["name"] == "redo-thickened"),
               "Saved/reopened document bytes mismatch")
    runtime = common.external(Path(report["runtime_root"]), source, local=True).resolve(strict=True)
    database = runtime / "projects/projects.sqlite3"
    check_that(not any(part.is_symlink() for part in (database, *database.parents)
                       if part.is_relative_to(runtime)) and database.is_file(),
               "Missing/linked retained ProjectStore database")
    check_that(database_anchor(SimpleNamespace(db_path=database)) == report["final_db"],
               "Final complete ProjectStore changed")


def verify_saved(source, evidence, expected_sha, review, selected, executor):
    evidence = common.external(evidence, source)
    check_that(evidence.is_dir(), "Original evidence directory missing")
    path = evidence_file(evidence, "result.json")
    check_that(valid_sha(expected_sha) and sha(path.read_bytes()) == expected_sha,
               "Independent original result SHA required/mismatched")
    report = read_json(path)
    terminal_contract(report, selected, executor)
    gate = report.get("producer_save_load")
    check_that(isinstance(gate, dict) and canonical(gate) == canonical({"schema": SCHEMA, "verified": True,
        "candidate": "producer-terminal-candidate.json", "sha256": gate.get("sha256")})
        and valid_sha(gate["sha256"]), "Missing/invalid producer save/load gate")
    candidate = evidence_file(evidence, gate["candidate"])
    check_that(sha(candidate.read_bytes()) == gate["sha256"] and canonical(read_json(candidate)) ==
               canonical({key: value for key, value in report.items() if key != "producer_save_load"}),
               "Producer candidate/final saved content mismatch")
    evidence_contract(source, evidence, report)
    review.report.update(original_result=str(path), original_sha256=expected_sha,
                         original_finished_at=report["finished_at"], original_counts=counts(report))
    review.check("offline/exact-source-producer-http-artifact-database-closure", True)


def control_report(selected, executor):
    """In-memory mutation fixture. Disk output must stay synthetic or FAIL."""
    plan = ledger()
    version = {"project_id": "control", "revision": 1, "sha256": "a" * 64,
               "document_sha256": "b" * 64}
    report = {"schema": SCHEMA, "verdict": "PASS", "rc": 0, "acceptance": REAL,
        "started_at": stamp(), "finished_at": stamp(), "ledger": plan, "synthetic": False,
        "source_before": copy.deepcopy(selected), "source_after": copy.deepcopy(selected),
        "executor_before": copy.deepcopy(executor), "executor_after": copy.deepcopy(executor),
        "source_drift": False, "executor_drift": False,
        "tool_sha256": executor["manifest"][TOOL]["sha256"],
        "helper_sha256": executor["manifest"][HELPER]["sha256"],
        "checks": [{"name": name, "passed": True, "at": stamp()} for name in plan["checks"]],
        "http_requests": copy.deepcopy(plan["http_requests"]),
        "states": [{"name": stage, "version": version} for stage in STAGES],
        "exports": [], "cli_processes": [{"label": name, "rc": 0,
            "ready_at": stamp(), "finished_at": stamp()} for name in ("initial", "reopen")],
        "readback_execution": {"rc": 0, "rc_measured": True, "timed_out": False, "finished_at": stamp()},
        "readback": {"ok": True, "backend": {"control": "synthetic"},
            "checks": [{"name": name, "passed": True} for name in plan["geometry_checks"]], "shapes": []}}
    for name in plan["exports"]:
        stage, fmt = name.split("/")
        spec = fixture(stage)
        anchor = {"sha256": "d" * 64, "tables": sorted(common.TABLES),
                  "counts": {table: 1 for table in common.TABLES}}
        report["exports"].append({"name": name, "format": fmt, "artifact_sha256": "c" * 64,
            "bytes": 1, "version": version, "fixture": spec, "db_before": anchor, "db_after": anchor})
        report["readback"]["shapes"].append({"name": name, "area_mm2": spec["area"],
            "volume_mm3": spec["volume"], "hole_signature": spec["signature"], "version": version,
            "witnesses": [{"point_mm": point, "actual": expected, "expected": expected,
                           "distance_mm": 0} for point, expected in spec["witnesses"]]})
    report["counts"] = counts(report)
    return report


class Controls(unittest.TestCase):
    out = None
    selected = None
    executor = None
    diagnostics = 0
    original_counterexample = None
    original_readback_failure = None

    def sample(self):
        return control_report(self.selected, self.executor)

    def rejected(self, report):
        with self.assertRaises((AssertionError, KeyError, TypeError, ValueError)):
            terminal_contract(report, self.selected, self.executor)
        type(self).diagnostics += 1

    def test_complete_json_roundtrip(self):
        report = self.sample()
        self.assertEqual(terminal_contract(json.loads(canonical(report)), self.selected, self.executor),
                         report["counts"])

    def test_full_source_dual_mutations(self):
        for group in ("source", "executor"):
            for operation in ("empty", "delete", "extra", "byte", "mode", "head", "status", "root"):
                report = self.sample()
                changed = copy.deepcopy(report[group + "_before"])
                manifest = changed["manifest"]
                name = next(name for name in manifest if name not in common.REQUIRED and name not in OWNED)
                if operation == "empty":
                    manifest.clear()
                elif operation == "delete":
                    manifest.pop(name)
                elif operation == "extra":
                    manifest["extra.py"] = {"sha256": "e" * 64, "bytes": 0, "executable": False}
                elif operation == "byte":
                    manifest[name]["sha256"] = "e" * 64
                elif operation == "mode":
                    manifest[name]["executable"] = not manifest[name]["executable"]
                elif operation == "head":
                    changed["head"] = "e" * 40
                elif operation == "status":
                    changed["status"] = " M " + TOOL + "\n"
                else:
                    changed["root"] = "/data/dev/foreign"
                changed["sha256"] = sha(canonical(manifest))
                report[group + "_before"] = changed
                report[group + "_after"] = copy.deepcopy(changed)
                self.rejected(report)

    def test_every_ledger_identity_mutation(self):
        for group in ("checks", "http_requests", "states", "exports", "geometry", "shapes"):
            sample = self.sample()
            items = (sample["readback"]["checks"] if group == "geometry" else
                     sample["readback"]["shapes"] if group == "shapes" else sample[group])
            for index in range(len(items)):
                for operation in ("delete", "duplicate", "rename", "reorder"):
                    report = self.sample()
                    target = (report["readback"]["checks"] if group == "geometry" else
                              report["readback"]["shapes"] if group == "shapes" else report[group])
                    if operation == "delete":
                        target.pop(index)
                    elif operation == "duplicate":
                        target.insert(index, copy.deepcopy(target[index]))
                    elif operation == "rename":
                        target[index]["name"] += "-mutated"
                    else:
                        other = (index + 1) % len(target)
                        target[index], target[other] = target[other], target[index]
                    report["counts"] = counts(report)
                    self.rejected(report)

    def test_http_route_method_status_strict(self):
        for index in range(len(self.sample()["http_requests"])):
            for key, value in (("route", "/changed"), ("method", "POST-mutant"), ("status", True)):
                report = self.sample()
                report["http_requests"][index][key] = value
                self.rejected(report)

    def test_empty_artifact_and_version_and_witness(self):
        for key, value in (("bytes", 0), ("bytes", True), ("artifact_sha256", ""),
                           ("version", {}), ("db_after", {"changed": True}), ("fixture", {})):
            report = self.sample()
            report["exports"][0][key] = value
            self.rejected(report)
        for key, value in (("area_mm2", True), ("area_mm2", math.nan), ("area_mm2", 64),
                           ("witnesses", []), ("hole_signature", [0]), ("version", {})):
            report = self.sample()
            report["readback"]["shapes"][0][key] = value
            self.rejected(report)

    def test_cli_and_partial_terminal(self):
        for key, value in (("verdict", "FAIL"), ("rc", True), ("acceptance", "PREFLIGHT_ONLY"),
                           ("first_cause", "original failure"), ("finished_at", None),
                           ("cli_processes", []), ("source_drift", True)):
            self.rejected({**self.sample(), key: value})
        for key, value in (("rc", True), ("rc", 3), ("rc_measured", False), ("timed_out", True),
                           ("launch_error", "missing FreeCAD"), ("finished_at", None)):
            report = self.sample()
            report["readback_execution"][key] = value
            self.rejected(report)

    def test_headers_all_bindings_and_empty_bytes(self):
        version = self.sample()["exports"][0]["version"]
        recipe = fixture("polygon")["recipe"]
        raw = b"ISO-10303-21;"
        headers = {"Content-Type": "application/step", "Content-Length": str(len(raw)),
            "Content-Disposition": "attachment; filename=\"surface.step\"",
            "X-OpenCAD-Revision": "1", "X-OpenCAD-Model-Version": "1",
            "X-OpenCAD-Document-SHA256": version["document_sha256"],
            "X-OpenCAD-Snapshot-SHA256": version["sha256"], "X-OpenCAD-Artifact-SHA256": sha(raw),
            "X-OpenCAD-Surface-Recipe-SHA256": sha(canonical(recipe)), "Cache-Control": "no-store"}
        headers_contract(headers, version, raw, "step", recipe)
        for key in headers:
            for operation in ("missing", "changed"):
                bad = dict(headers)
                if operation == "missing":
                    bad.pop(key)
                else:
                    bad[key] += "mutated"
                with self.assertRaises(AssertionError):
                    headers_contract(bad, version, raw, "step", recipe)
                type(self).diagnostics += 1
        with self.assertRaises(AssertionError):
            headers_contract(headers, version, b"", "step", recipe)

    def test_producer_actual_save_load_negative_controls(self):
        original_writer = write_json
        for operation in ("empty-physical-evidence", "serialized-corrupt", "incomplete", "dual-source-delete"):
            out = self.out / operation
            out.mkdir(exist_ok=False)
            report = self.sample()
            report["files"] = inventory(out)
            if operation == "incomplete":
                report["checks"].pop()
                report["counts"] = counts(report)
            elif operation == "dual-source-delete":
                name = next(name for name in report["source_before"]["manifest"] if name not in common.REQUIRED)
                for key in ("source_before", "source_after"):
                    report[key]["manifest"].pop(name)
                    report[key]["sha256"] = sha(canonical(report[key]["manifest"]))

            def writer(path, value):
                changed = copy.deepcopy(value)
                if operation == "serialized-corrupt" and path.name == "producer-terminal-candidate.json":
                    changed["readback"]["checks"].pop()
                original_writer(path, changed)

            with mock.patch.object(sys.modules[__name__], "write_json", writer):
                publish(out, report, self.selected, self.executor, str)
            saved = read_json(out / "result.json")
            self.assertEqual((saved["verdict"], saved["rc"]), ("FAIL", 1))
            self.assertNotIn("producer_save_load", saved)
            self.assertTrue(saved["first_cause"])
            if operation == "empty-physical-evidence":
                self.assertIn("Evidence full file set/byte/hash closure mismatch", saved["first_cause"])
            type(self).diagnostics += 1
            before = (out / "result.json").read_bytes()
            with self.assertRaises(FileExistsError):
                original_writer(out / "result.json", {})
            self.assertEqual((out / "result.json").read_bytes(), before)

    def test_synthetic_saved_roundtrip_never_real(self):
        out = self.out / "synthetic-save-load"
        out.mkdir(exist_ok=False)
        report = {"schema": SCHEMA, "synthetic": True, "acceptance": "SYNTHETIC_SAVE_LOAD",
                  "verdict": "PASS", "rc": 0, "payload": {"complete": True}}

        def validate(loaded):
            self.assertEqual(loaded, report)

        gate = saved_candidate(out, report, validate)
        self.assertEqual(gate["sha256"], sha((out / gate["candidate"]).read_bytes()))
        publish(out, report, self.selected, self.executor, str)
        self.assertEqual(read_json(out / "result.json")["acceptance"], "SYNTHETIC_SAVE_LOAD")
        forged = self.sample()
        forged["synthetic"] = True
        self.rejected(forged)

    def test_original_counterexample_immutable_and_physically_rejected(self):
        if self.original_counterexample is None:
            self.skipTest("Supply --selftest-counterexample to pin the retained original false PASS")
        path = self.original_counterexample
        before = path.read_bytes()
        report = read_json(path)
        self.assertEqual((report["verdict"], report["acceptance"]), ("PASS", REAL))
        with self.assertRaisesRegex(AssertionError, "Evidence full file set/byte/hash closure mismatch"):
            evidence_contract(Path(report["source_before"]["root"]), path.parent, report)
        self.assertEqual(path.read_bytes(), before)
        write_json(self.out / "original-counterexample-control.json", {
            "synthetic": True, "original_result": str(path), "original_sha256": sha(before),
            "original_verdict": "PASS", "verifier_rc": 1,
            "first_cause": "Evidence full file set/byte/hash closure mismatch", "at": stamp()})
        type(self).diagnostics += 1

    def test_physical_evidence_positive_and_missing_actual_files(self):
        out = self.out / "physical-structure-positive"
        out.mkdir(exist_ok=False)
        report = self.sample()
        runtime = common.external(Path("/data/tmp") / (self.out.name + "-physical-controls"), ROOT, local=True)
        runtime.mkdir(exist_ok=False)
        projects = runtime / "projects"
        projects.mkdir()
        with sqlite3.connect(projects / "projects.sqlite3") as db:
            for table in common.TABLES:
                db.execute("CREATE TABLE " + table + " (value TEXT)")
                db.execute("INSERT INTO " + table + " VALUES (?)", ("synthetic-evidence-structure",))
        anchor = database_anchor(SimpleNamespace(db_path=projects / "projects.sqlite3"))
        report.update(runtime_root=str(runtime), final_db=anchor)
        for item in report["states"]:
            item["doc"] = canonical({"synthetic": True, "stage": item["name"]})
        for item in report["exports"]:
            stage, fmt = item["name"].split("/")
            raw = ("synthetic-" + item["name"]).encode()
            item.update(artifact="download-" + stage + "." + fmt, bytes=len(raw),
                        artifact_sha256=sha(raw), db_before=anchor, db_after=anchor)
            item["http_index"] = next(index for index, request in enumerate(report["http_requests"], 1)
                                      if request["name"] == item["name"])
            with (out / item["artifact"]).open("xb") as handle:
                handle.write(raw)
        for index, request in enumerate(report["http_requests"], 1):
            if request["route"] == "/api/auth/login":
                continue
            exported = next((item for item in report["exports"] if item["name"] == request["name"]), None)
            raw = ((out / exported["artifact"]).read_bytes() if exported else b"{\"synthetic\":true}")
            request.update(bytes=len(raw), response_sha256=sha(raw))
            if exported:
                version, fmt = exported["version"], exported["format"]
                request["headers"] = {"Content-Type": PROTOCOL["media"][fmt],
                    "Content-Length": str(len(raw)), "Content-Disposition": f"attachment; filename=\"surface.{fmt}\"",
                    "X-OpenCAD-Revision": str(version["revision"]),
                    "X-OpenCAD-Model-Version": str(version["revision"]),
                    "X-OpenCAD-Document-SHA256": version["document_sha256"],
                    "X-OpenCAD-Snapshot-SHA256": version["sha256"],
                    "X-OpenCAD-Artifact-SHA256": sha(raw), "Cache-Control": "no-store",
                    "X-OpenCAD-Surface-Recipe-SHA256": sha(canonical(exported["fixture"]["recipe"]))}
            with (out / ("http-%03d.json" % index)).open("xb") as handle:
                handle.write(raw)
        for index, process in enumerate(report["cli_processes"], 1):
            path = out / (process["label"] + "-secure-cli.log")
            with path.open("x") as handle:
                handle.write("SYNTHETIC CLI LOG\nOPENCAD_PRIVATE_WEB verdict=READY port=" + str(index))
            process.update(log=str(path), log_sha256=sha(path.read_bytes()),
                           argv=[str(ROOT / common.APP_REL / "secure_web.py"), "--port", str(index)])
        with (out / "checks.jsonl").open("x") as handle:
            handle.write("\n".join(canonical(item) for item in report["checks"]) + "\n")
        write_json(out / "readback.json", report["readback"])
        write_json(out / "readback-input.json", {"evidence_root": str(out), "exports": report["exports"], "ledger": ledger()})
        with (out / "download-readback.py").open("x") as handle:
            handle.write(READBACK)
        with (out / "readback-stdout.log").open("x") as handle:
            handle.write("SYNTHETIC LOG\nSURFACE_EXPORT_READBACK verdict=PASS")
        report["readback_execution"].update(argv=["synthetic-not-executed", str(out / "download-readback.py")],
                                           executable_sha256="a" * 64)
        with (out / "saved-document.l1").open("x") as handle:
            handle.write(next(item["doc"] for item in report["states"] if item["name"] == "redo-thickened"))
        report["files"] = inventory(out)
        evidence_contract(ROOT, out, report)
        outcomes = []
        for label, omitted in (("artifact", report["exports"][0]["artifact"]),
                                ("HTTP", "http-001.json" if "http-001.json" in report["files"] else "http-003.json"),
                                ("CLI", "initial-secure-cli.log"), ("geometry", "readback.json"),
                                ("document", "saved-document.l1")):
            destination = self.out / ("physical-missing-" + label)
            shutil.copytree(out, destination, ignore=lambda directory, names:
                            [omitted, "readback-input.json"] if directory == str(out) else [])
            bad = copy.deepcopy(report)
            bad["readback_execution"]["argv"][1] = str(destination / "download-readback.py")
            write_json(destination / "readback-input.json",
                       {"evidence_root": str(destination), "exports": bad["exports"], "ledger": ledger()})
            bad["files"] = inventory(destination)
            with self.assertRaises(AssertionError) as failure:
                evidence_contract(ROOT, destination, bad)
            first_cause = str(failure.exception)
            self.assertEqual(first_cause, "Missing/nonregular evidence file: " + omitted)
            outcomes.append({"name": label, "omitted_actual_file": omitted, "verifier_rc": 1,
                             "first_cause": first_cause})
            type(self).diagnostics += 1
        bad = copy.deepcopy(report)
        absent = runtime / "absent-database-control"
        absent.mkdir()
        bad["runtime_root"] = str(absent)
        with self.assertRaisesRegex(AssertionError, "Missing/linked retained ProjectStore database") as failure:
            evidence_contract(ROOT, out, bad)
        outcomes.append({"name": "database", "verifier_rc": 1, "first_cause": str(failure.exception)})
        type(self).diagnostics += 1
        report.update(synthetic=True, acceptance="SYNTHETIC_PHYSICAL_STRUCTURE", outcomes=outcomes)
        write_json(self.out / "physical-evidence-controls.json", report)

    def test_evidence_bidirectional_set_and_hash(self):
        out = self.out / "file-closure"
        out.mkdir(exist_ok=False)
        write_json(out / "artifact.json", {"present": True})
        original = inventory(out)
        write_json(out / "extra.json", {})
        self.assertNotEqual(inventory(out), original)
        with self.assertRaises(AssertionError):
            evidence_file(out, "../foreign")
        with self.assertRaises(AssertionError):
            evidence_file(out, "missing")

    def test_cli_contract_and_readback_parse(self):
        compile(READBACK, "download-readback.py", "exec")
        for arguments in (("--out", str(self.out), "--verify-result", str(self.out)),
                          ("--out", str(self.out), "--preflight", "--selftest")):
            with self.assertRaises(SystemExit) as failure:
                parse_args(arguments)
            self.assertEqual(failure.exception.code, 2)

    def test_readback_rc_json_cannot_replace_stdout_terminal(self):
        good = SimpleNamespace(rc=0, rc_measured=True, timed_out=False, launch_error="",
                               stdout="SURFACE_EXPORT_READBACK verdict=PASS\n")
        self.assertTrue(readback_terminal(good))
        for field, value in (("stdout", "FreeCAD banner only\n"), ("rc", 3), ("rc", True),
                             ("rc_measured", False), ("timed_out", True), ("launch_error", "failed"),
                             ("stdout", "SURFACE_EXPORT_READBACK verdict=PASS-forged\n")):
            bad = copy.copy(good)
            setattr(bad, field, value)
            self.assertFalse(readback_terminal(bad))
            type(self).diagnostics += 1
        self.assertIn('else "FAIL"), flush=True)', READBACK)

    def test_retained_real_readback_failure(self):
        if self.original_readback_failure is None:
            self.skipTest("Supply --selftest-readback-failure to pin the retained real marker failure")
        evidence = self.original_readback_failure
        paths = [evidence_file(evidence, name) for name in
                 ("result.json", "readback.json", "readback-stdout.log", "readback-stderr.log")]
        before = {path.name: sha(path.read_bytes()) for path in paths}
        original = read_json(paths[0])
        payload = read_json(paths[1])
        self.assertEqual((original["verdict"], original["rc"]), ("FAIL", 1))
        self.assertIn("readback/measured-cli-terminal", original["first_cause"])
        self.assertNotIn("producer_save_load", original)
        execution = original["readback_execution"]
        outcome = SimpleNamespace(**{key: execution[key] for key in
                                     ("rc", "rc_measured", "timed_out", "launch_error")},
                                  stdout=paths[2].read_text())
        self.assertEqual(outcome.rc, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual([item["name"] for item in payload["checks"]], ledger()["geometry_checks"])
        self.assertTrue(all(item["passed"] is True for item in payload["checks"]))
        self.assertEqual([item["name"] for item in payload["shapes"]], ledger()["exports"])
        self.assertFalse(readback_terminal(outcome))
        inputs = read_json(evidence_file(evidence, "readback-input.json"))
        self.assertEqual(str(common.external(evidence, ROOT)), inputs["evidence_root"])
        self.assertEqual(canonical(inputs), canonical(
            {"evidence_root": str(evidence), "exports": original["exports"], "ledger": ledger()}))
        self.assertEqual({path.name: sha(path.read_bytes()) for path in paths}, before)
        write_json(self.out / "retained-readback-failure-control.json",
                   {"original_evidence": str(evidence), "original_hashes": before,
                    "original_rc": 1, "child_rc": outcome.rc, "verifier_rc": 1,
                    "first_cause": "readback/measured-cli-terminal",
                    "geometry": len(payload["checks"]), "shapes": len(payload["shapes"])})
        type(self).diagnostics += 1


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True, help="fresh exact evidence directory; never reused")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--expect-head")
    parser.add_argument("--expect-source-sha")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--selftest", action="store_true")
    modes.add_argument("--verify-result", type=Path)
    modes.add_argument("--diagnose-readback", type=Path,
                       help="paired FreeCAD-only replay of original failed evidence, no HTTP")
    parser.add_argument("--reference-sha", help="independently supplied original result SHA, not self-derived")
    parser.add_argument("--selftest-counterexample", type=Path,
                        help="read-only original false PASS result.json, only with selftest")
    parser.add_argument("--selftest-readback-failure", type=Path,
                        help="read-only retained real terminal-marker failure, only with selftest")
    args = parser.parse_args(arguments)
    if bool(args.verify_result) != bool(args.reference_sha):
        parser.error("--verify-result and --reference-sha must be supplied together")
    if args.selftest_counterexample and not args.selftest:
        parser.error("--selftest-counterexample requires --selftest")
    if args.selftest_readback_failure and not args.selftest:
        parser.error("--selftest-readback-failure requires --selftest")
    if args.diagnose_readback and not args.runtime_root:
        parser.error("--diagnose-readback requires a fresh runtime-root")
    if not (args.preflight or args.selftest or args.verify_result or args.diagnose_readback):
        if not args.runtime_root or not args.expect_head or not args.expect_source_sha:
            parser.error("real execution requires runtime-root, expect-head and expect-source-sha")
    return args


def main(arguments=None):
    args = parse_args(arguments)
    source = args.source_root.resolve(strict=True)
    out = common.external(args.out, source)
    check_that(not out.exists(), "Evidence already exists; no reuse or cleanup")
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": SCHEMA, "verdict": "FAIL", "rc": 1, "acceptance": "NOT_RUN",
        "started_at": stamp(), "evidence_dir": str(out), "checks": [], "http_requests": [],
        "synthetic": False,
        "cli_processes": [], "tool_sha256": sha(Path(__file__).read_bytes()),
        "helper_sha256": sha(Path(common.__file__).read_bytes()),
        "boundaries": ["consumer-only; no product/panel/browser/production acceptance",
            "real GET bytes, not independent recipe reconstruction",
            "exact selected-source and executor closures; no ancestor or product-equivalence promotion",
            "local cookie/auth/CSRF only; no proxy/TLS/revocation concurrency proof",
            "small analytic fixture only; no large assembly or industrial parity",
            "reference SHA is caller-supplied evidence pin, not an approved-record trust gate"]}
    review = common.Review(out, report)
    selected = executor = None
    started = time.monotonic()
    try:
        selected, executor = selected_binding(source), selected_binding(ROOT)
        report.update(source_before=copy.deepcopy(selected), executor_before=copy.deepcopy(executor))
        source_equal(selected, selected, selected)
        source_equal(executor, executor, executor)
        executor_scope(executor)
        if args.preflight:
            candidate = os.environ.get("CADCORE_SUBPROC_EXECUTABLE") or shutil.which("freecadcmd")
            check_that(candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK),
                       "No existing executable FreeCAD path")
            report["freecad_precheck"] = {"path": str(Path(candidate).resolve()),
                "sha256": sha(Path(candidate).read_bytes()), "executed": False}
            report.update(verdict="PASS", acceptance="PREFLIGHT_ONLY")
        elif args.selftest:
            # These clean snapshots are in-memory validator controls, not a measured clean product run.
            Controls.out = out
            Controls.selected, Controls.executor = copy.deepcopy(selected), copy.deepcopy(executor)
            Controls.selected["status"] = Controls.executor["status"] = ""
            Controls.diagnostics = 0
            Controls.original_counterexample = (common.external(args.selftest_counterexample, source)
                if args.selftest_counterexample else None)
            Controls.original_readback_failure = (common.external(args.selftest_readback_failure, source)
                if args.selftest_readback_failure else None)
            with (out / "selftest.log").open("x") as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(Controls))
            report["selftest"] = {"tests": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skips": len(result.skipped),
                "rejected_mutations": Controls.diagnostics, "synthetic": True}
            check_that(result.testsRun > 0 and result.wasSuccessful(), "Consumer selftest failed")
            report.update(verdict="PASS", acceptance="SELFTEST_ONLY")
        elif args.verify_result:
            verify_saved(source, args.verify_result, args.reference_sha, review, selected, executor)
            report.update(verdict="PASS", acceptance="OFFLINE_READBACK_ONLY")
        elif args.diagnose_readback:
            evidence = common.external(args.diagnose_readback, source)
            check_that(evidence.is_dir(), "Original diagnostic evidence directory missing")
            runtime = common.external(args.runtime_root, source, local=True)
            check_that(not runtime.exists(), "Diagnostic runtime exists; no reuse or cleanup")
            runtime.mkdir(parents=True, exist_ok=False)
            report["runtime_root"] = str(runtime)
            diagnose_readback(evidence, runtime, review)
            report.update(verdict="PASS", acceptance="READBACK_DIAGNOSTIC_ONLY")
        else:
            check_that(selected["status"] == "" and executor["status"] == "",
                       "Real execution requires clean frozen source and executor")
            check_that(selected["head"] == args.expect_head, "HEAD_NOT_PINNED")
            common.frozen(selected, args.expect_source_sha)
            runtime = common.external(args.runtime_root, source, local=True)
            check_that(not runtime.exists(), "Runtime already exists; no reuse or cleanup")
            runtime.mkdir(parents=True, exist_ok=False)
            report["runtime_root"] = str(runtime)
            os.environ.update(common.product_env(source, runtime))
            workflow(source, runtime, review)
            report.update(verdict="PASS", acceptance=REAL)
        after, executor_after = selected_binding(source), selected_binding(ROOT)
        source_equal(selected, after, selected)
        source_equal(executor, executor_after, executor)
        report.update(source_after=after, executor_after=executor_after, source_drift=False, executor_drift=False)
        review.check("source/exact-head-byte-mode-set", True)
        review.check("executor/exact-head-byte-mode-set", True)
        for path in out.rglob("*"):
            if path.is_file():
                check_that(not any(value.encode() in path.read_bytes() for value in review.private_values if value),
                           "Generated credential leaked into evidence: " + path.name)
        report["rc"] = 0
    except Exception as exc:
        report.update(verdict="FAIL", rc=1, first_cause=review.redact(traceback.format_exc()),
                      failure_type=type(exc).__name__)
        with (out / "failure.log").open("x") as handle:
            handle.write(report["first_cause"])
        print(report["first_cause"], file=sys.stderr)
        if selected is not None:
            try:
                report["source_after"], report["executor_after"] = selected_binding(source), selected_binding(ROOT)
                report["source_drift"] = canonical(selected) != canonical(report["source_after"])
                report["executor_drift"] = canonical(executor) != canonical(report["executor_after"])
            except Exception as secondary:
                report["secondary_failure"] = review.redact(str(secondary))
    report.update(finished_at=stamp(), elapsed_s=time.monotonic() - started, counts=counts(report))
    report["files"] = inventory(out)
    publish(out, report, selected, executor, review.redact)
    print("SURFACE_EXPORT_PRIVATE_REVIEW verdict=%s acceptance=%s rc=%s counts=%s evidence=%s" %
          (report["verdict"], report["acceptance"], report["rc"], canonical(report["counts"]), out), flush=True)
    return report["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
