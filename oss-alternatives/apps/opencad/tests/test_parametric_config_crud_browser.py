#!/usr/bin/env python3
"""Owned configuration CRUD browser gate; source-bound live CLI/SQLite/FreeCAD.

--selftest runs only this file's receipt/source/terminal negative controls.
--verify-evidence is offline. Real runs require fresh 3FS evidence and a short
fresh /data/tmp runtime. Existing browser/geometry helpers are read-only imports;
no previous screenshots, results, product suites or production data are used.
"""

from __future__ import annotations

import argparse
import ast
import base64
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
import copy
import http.client
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import sqlite3
import subprocess
import sys
import tarfile
import time
import traceback
import unittest
from unittest.mock import patch
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
SENTINEL_CHECKS = {"1440-legal_unselected_replacement"}
HTTP_ROUTES = {"/api/parametric", "/api/state", "/api/mesh", "/api/step",
               "/api/save", "/api/command", "/api/open"}
LEDGER_KEYS = ("requests", "actor_requests", "operations", "loss", "measurements", "history", "identities")
spec = importlib.util.spec_from_file_location("crud_browser_helpers", APP / "tests/test_parametric_config_browser.py")
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)
require, write, digest, canonical, now = (
    legacy.require, legacy.write, legacy.digest, legacy.canonical, legacy.now)
CONTROL_RUNTIME = None
REVISION_BASE = "c1121623c8b538cdb67f5158d3ed7eabef4186de"
HISTORICAL_RESULT_SHA = "addfe08de7e20383b6d20b971c57033b2cf32f94f409b6f1bdfe4ef134ac1d4a"
READER_REPAIR_PATHS = {OWNED[2], OWNED[3]}
HISTORICAL_FIXTURE_READER = "d00f3fd8977f3b43345c06f115ba500da3c179e9"
INTEGRATION_FIXTURE_BASE = "7c63a289a4c6965d24254ba7115bac85aa17b68f"
PRIVATE_CLEAR_BASE = "380596370d614659964b15e5e33a3b5d312cb894"
PRIVATE_CLEAR_BEFORE = False
SELECTOR_FIXTURE_BASE = "22de80c1059c9b18518f1b4b4e7804a6e441489a"
SELECTOR_FIXTURE_BEFORE = False


def selector_fixture_probe(runtime, data, *, temporary=False):
    if not SELECTOR_FIXTURE_BEFORE:
        return private_clear_probe(runtime, data=data, temporary=temporary)
    old = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
        "show", SELECTOR_FIXTURE_BASE + ":" + OWNED[2]])
    functions = [item for item in ast.parse(old).body if isinstance(item, ast.FunctionDef) and
                 item.name == "private_clear_probe"]
    require(len(functions) == 1, "selector_baseline_probe_not_exact")
    namespace = dict(globals())
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 SELECTOR_FIXTURE_BASE + ":" + OWNED[2], "exec"), namespace)
    tested = namespace["private_clear_probe"](runtime, data=data, temporary=temporary)
    binding = json.loads((runtime / "binding.json").read_bytes())
    binding.update(probe_commit=SELECTOR_FIXTURE_BASE, probe_file_sha256=digest(old))
    (runtime / "binding.json").unlink()
    write(runtime / "binding.json", binding)
    return tested


def private_clear_probe(runtime, *, temporary=False, data=None):
    """Execute the complete owned JS; only the fixture exposes closed-over state."""
    node = shutil.which("node")
    require(node is not None, "private_clear_node_required")
    if data is None:
        data = (subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                "show", PRIVATE_CLEAR_BASE + ":" + OWNED[0]]) if PRIVATE_CLEAR_BEFORE else
                (ROOT / OWNED[0]).read_bytes())
    anchor = b"window.opencadParametric = {mount: mount, refresh: refresh, submit: submit};"
    require(data.count(anchor) == 1, "private_clear_export_anchor_not_exact")
    exposure = b"""
  window.__privateClear = {
    shield: shield, report: report, submit: submit, refresh: refresh,
    seed: function (nodes) {
      panel = nodes.panel; form = nodes.form; fields = nodes.fields;
      dimensions = nodes.dimensions; configurations = nodes.configurations;
      features = nodes.features; result = nodes.result; notice = nodes.notice;
      discardConfirmation = nodes.discardConfirmation; definitionForm = nodes.definitionForm;
      definitionTarget = nodes.definitionTarget; definitionName = nodes.definitionName;
      definitionParameters = nodes.definitionParameters; definitionFeatures = nodes.definitionFeatures;
      selectionPolicy = nodes.selectionPolicy; deleteReplacement = nodes.deleteReplacement;
      deleteConfirmation = nodes.deleteConfirmation; definitionNotice = nodes.definitionNotice;
      owner = "private-owner"; current = {project_id: "private-project", parametric_version: 2};
      specification = {private: "definition"}; bindingError = new Error("private-config-error");
      inputs.set("private-width", {input: nodes.fields});
      dimensionInputs.set("private-dimension", nodes.dimensions);
      definitionInputs.set("private-expression", {expression: nodes.definitionParameters,
        explicit: {checked: true}});
      suppressionInputs.set("private-feature", nodes.definitionFeatures);
      blocked = false; expired = false; busy = true; dirty = true; definitionDirty = true;
      modelStale = true; definitionStale = true; clearOverrides = true; definitionMode = "create";
    },
    state: function () {
      return {owner, current, specification, blocked, expired, busy, dirty, definitionDirty,
        modelStale, definitionStale, clearOverrides, definitionMode, epoch, reads,
        bindingError: bindingError && bindingError.message,
        maps: [inputs.size, dimensionInputs.size, definitionInputs.size, suppressionInputs.size]};
    }
  };
"""
    instrumented = data.replace(anchor, anchor + exposure)
    write(runtime / "product.js", instrumented)
    runner = r"""
"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert");
function element(tagName = "DIV") {
  return {tagName, value: "private-config", textContent: "private-text", hidden: false, disabled: false,
    dataset: {}, children: ["private-option"], attributes: {},
    replaceChildren() { this.children = []; this.value = ""; this.textContent = ""; },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    querySelectorAll(selector) { throw Error("private_clear_unknown_selector:" + selector); }};
}
const names = ["panel", "form", "fields", "dimensions", "configurations", "features", "result",
  "notice", "discardConfirmation", "definitionForm", "definitionTarget", "definitionName",
  "definitionParameters", "definitionFeatures", "selectionPolicy", "deleteReplacement",
  "deleteConfirmation", "definitionNotice"];
const nodes = Object.fromEntries(names.map(name => [name, element()]));
nodes.definitionName.tagName = "INPUT";
for (const name of ["configurations", "definitionTarget", "deleteReplacement", "selectionPolicy"]) {
  nodes[name].tagName = "SELECT";
}
nodes.definitionForm.dataset.editingTarget = "private-config";
nodes.panel.dataset = {documentSha: "private-doc", projectId: "private-project", revision: "9"};
nodes.result.dataset = {documentSha: "private-doc", volumeMm3: "42", revision: "9"};
nodes.selectionPolicy.value = "target";
const buttons = [element("BUTTON"), element("BUTTON")];
buttons[0].dataset.parametricCreate = "create_configurable_plate";
buttons[1].dataset.parametricReload = "true";
const permitted = [buttons[1]];
for (const key of ["parametricDiscard", "parametricConfirmDiscard", "parametricCancelDiscard",
    "definitionDiscard"]) {
  const button = element("BUTTON"); button.dataset[key] = "true";
  buttons.push(button); permitted.push(button);
}
const readonlyInput = element("INPUT"); readonlyInput.dataset.readonly = "true";
const actions = [...buttons, nodes.definitionName, readonlyInput, nodes.configurations,
  nodes.definitionTarget, nodes.deleteReplacement, nodes.selectionPolicy];
nodes.selectionPolicy.dataset.definitionV2 = "true";
const queries = [];
function selectKnown(selector, known) {
  queries.push(selector);
  assert(Object.hasOwn(known, selector), "private_clear_unknown_selector:" + selector);
  return known[selector];
}
const tagSubsets = Object.fromEntries(["button, input, select", "button, select",
  "input, select", "button, input"].map(selector => {
  const tags = selector.split(", ").map(tag => tag.toUpperCase());
  return [selector, actions.filter(item => tags.includes(item.tagName))];
}));
nodes.panel.querySelectorAll = selector => selectKnown(selector, {
  ...tagSubsets, "[data-parametric-create]": actions.filter(item =>
    Object.hasOwn(item.dataset, "parametricCreate"))
});
nodes.definitionForm.querySelectorAll = selector => selectKnown(selector, {
  "[data-definition-v2]": [nodes.selectionPolicy]
});
assert.deepStrictEqual(tagSubsets["button, input, select"], actions, "private_clear_fixture_full_set");
for (const tag of ["BUTTON", "INPUT", "SELECT"]) {
  assert(actions.some(item => item.tagName === tag && !item.disabled),
    "private_clear_fixture_missing_enabled_type:" + tag);
}
assert(tagSubsets["button, select"].every(item => item.tagName !== "INPUT") &&
  !tagSubsets["button, select"].includes(nodes.definitionName),
  "private_clear_fixture_subset_not_exact");
let requests = 0;
const window = {
  OpenCADLocale: {
    ready: Promise.resolve(),
    snapshot: () => ({status: "ready", locale: "zh-CN"}),
    t: (key, args) => args && Object.hasOwn(args, "message") ? String(args.message) : key,
    bind: () => () => {}
  },
  addEventListener() {}, fetch() { ++requests; throw Error("unexpected_private_fetch"); }
};
const document = {readyState: "loading", addEventListener() {}};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {window, document, console});
const api = window.__privateClear;
assert(api && typeof api.shield === "function", "private_clear_real_shield_missing");
api.seed(nodes);
const before = JSON.stringify(api.state()), permanent = process.argv[3] === "permanent";
api.shield("private-session-expired", permanent);
const after = JSON.parse(JSON.stringify(api.state()));
assert(after.epoch === 1 && after.reads === 1 && after.blocked && !after.busy,
  "private_clear_generation_or_busy_gate");
assert(nodes.form.hidden && nodes.definitionForm.hidden && nodes.deleteConfirmation.hidden &&
  nodes.discardConfirmation.hidden && buttons[0].hidden, "private_clear_visibility_gate");
if (permanent) {
  assert(nodes.deleteReplacement.children.length === 0 && nodes.deleteReplacement.value === "",
    "private_clear_replacement_options_left");
  assert(!Object.hasOwn(nodes.definitionForm.dataset, "editingTarget"),
    "private_clear_editing_target_left");
  assert(nodes.definitionTarget.children.length === 0 && nodes.definitionName.value === "" &&
    nodes.selectionPolicy.value === "keep" && after.definitionMode === "edit",
    "private_clear_definition_selection_left");
  assert(after.expired && after.owner === null && after.current === null &&
    after.specification === null && after.bindingError === null,
    "private_clear_owner_or_binding_left");
  assert(after.maps.every(size => size === 0) && !after.dirty && !after.definitionDirty &&
    !after.modelStale && !after.definitionStale && !after.clearOverrides,
    "private_clear_private_draft_left");
  for (const name of ["fields", "dimensions", "features", "configurations",
      "definitionParameters", "definitionFeatures"]) {
    assert(nodes[name].children.length === 0, "private_clear_rows_left:" + name);
  }
  assert(nodes.result.textContent === "" && nodes.definitionNotice.textContent === "" &&
    Object.keys(nodes.panel.dataset).length === 0 && Object.keys(nodes.result.dataset).length === 0,
    "private_clear_receipt_metadata_left");
  assert(actions.every(item => item.disabled), "private_clear_action_gate:" +
    JSON.stringify(actions.map(item => ({tag: item.tagName, disabled: item.disabled}))));
  api.report(new Error("late-private-error"));
  assert(nodes.notice.textContent === "private-session-expired", "private_clear_notice_overwritten");
} else {
  const original = JSON.parse(before);
  for (const key of ["owner", "current", "specification", "dirty", "definitionDirty",
      "modelStale", "definitionStale", "clearOverrides", "definitionMode", "bindingError", "maps"]) {
    assert.deepStrictEqual(after[key], original[key], "private_clear_temporary_draft_erased:" + key);
  }
  assert(!after.expired && nodes.deleteReplacement.children[0] === "private-option" &&
    nodes.definitionForm.dataset.editingTarget === "private-config" &&
    nodes.definitionName.value === "private-config" && nodes.selectionPolicy.value === "target" &&
    nodes.panel.dataset.projectId === "private-project", "private_clear_temporary_dom_erased");
  assert(permitted.every(item => !item.disabled) &&
    actions.filter(item => !permitted.includes(item)).every(item => item.disabled),
    "private_clear_temporary_action_gate:" +
      JSON.stringify(actions.map(item => ({tag: item.tagName, disabled: item.disabled}))));
}
(async function () {
  await assert.rejects(api.submit("replace_configuration"), permanent ?
    /parametric_operation_unavailable/ : /private-config-error/);
  if (permanent) await api.refresh(true);
  assert(requests === 0, "private_clear_post_shield_request");
  console.log(JSON.stringify({permanent, state: after, requests, real_js_executed: true, queries,
    actions: actions.map(item => ({tag: item.tagName, disabled: item.disabled}))}));
}()).catch(error => { console.error(error.stack); process.exitCode = 1; });
"""
    write(runtime / "probe.cjs", runner.encode())
    argv = [node, str(runtime / "probe.cjs"), str(runtime / "product.js"),
            "temporary" if temporary else "permanent"]
    tested = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write(runtime / "stdout.log", tested.stdout)
    write(runtime / "stderr.log", tested.stderr)
    write(runtime / "binding.json", {"argv": argv, "actual_rc": tested.returncode,
        "product_sha256": digest(data), "instrumented_sha256": digest(instrumented),
        "runner_sha256": digest(runner.encode()), "baseline_commit": PRIVATE_CLEAR_BASE
        if PRIVATE_CLEAR_BEFORE else None})
    return tested


def historical_diff_command():
    return ["git", "--no-optional-locks", "--no-replace-objects", "-C", str(ROOT),
            "diff", "--name-only", REVISION_BASE, "--"]


@contextmanager
def control_git_diff(commit):
    """Only controls substitute this one working-tree query with an exact Git pair."""
    require(type(commit) is str and len(commit) == 40 and
            all(char in "0123456789abcdef" for char in commit), "fixture_commit_required")
    original = subprocess.check_output
    actual = original(["git", "--no-replace-objects", "-C", str(ROOT),
                       "rev-parse", "--verify", commit + "^{commit}"], text=True).strip()
    require(actual == commit, "fixture_commit_not_exact")
    command = historical_diff_command()
    exact = [*command[:-1], commit, "--"]
    paths = original(exact, text=True).splitlines()

    def read(argv, *args, **kwargs):
        return original(exact if argv == command else argv, *args, **kwargs)

    with patch.object(subprocess, "check_output", side_effect=read):
        yield {"base": REVISION_BASE, "commit": commit, "command": exact, "paths": paths}


@contextmanager
def control_historical_browser_source():
    """Historical positive controls read their exact old scenario, never current UI."""
    historical = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
        "show", PRIVATE_CLEAR_BASE + ":" + OWNED[2]])
    require(browser_semantics(historical) == browser_semantics(baseline_reader_bytes()),
            "historical_control_browser_not_exact")
    original = Path.read_bytes
    target = Path(__file__).resolve()

    def read(path):
        return historical if path.resolve() == target else original(path)

    with patch.object(Path, "read_bytes", read):
        yield


def previous_historical_controls():
    data = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                   "show", HISTORICAL_FIXTURE_READER + ":" + OWNED[2]])
    names = {"historical_fixture", "test_historical_product_source_set_mode_and_head_negative",
             "test_historical_browser_semantic_change_negative"}
    classes = [node for node in ast.parse(data).body if isinstance(node, ast.ClassDef) and
               node.name == "Controls"]
    require(len(classes) == 1, "fixture_baseline_controls_missing")
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and
               node.name in names]
    require({node.name for node in methods} == names, "fixture_baseline_methods_missing")
    node = ast.ClassDef(name="PreviousHistoricalControls",
        bases=[ast.Attribute(value=ast.Name(id="unittest", ctx=ast.Load()),
                             attr="TestCase", ctx=ast.Load())],
        keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = dict(globals())
    exec(compile(module, HISTORICAL_FIXTURE_READER + ":" + OWNED[2], "exec"), namespace)
    return namespace[node.name], digest(data)


def historical_fixture_case(record, kind):
    try:
        original, current = Controls().historical_fixture()
        commit = HISTORICAL_FIXTURE_READER if kind == "compatible" else INTEGRATION_FIXTURE_BASE
        with control_git_diff(commit) as binding, control_historical_browser_source():
            record.report["fixture_git_binding"] = binding
            require((set(binding["paths"]) <= READER_REPAIR_PATHS) == (kind == "compatible"),
                    "fixture_git_boundary_not_exercised")
            compatible = verify_historical_source(original, current)
        record.report.update(compatibility=compatible, verdict="FIXTURE_COMPATIBLE", intended_rc=0,
                             completed=True, expected_checks=1, completed_checks=1)
    except Exception:
        record.failure("historical_fixture_case", traceback.format_exc())
    record.report["browser_executed"] = False
    record.capture("source_after")
    if record.report["source_drift"]:
        record.failure("source_drift", "source_drift")
    return record.publish()


def historical_fixture_acceptance(record, *, before):
    record.report.update(browser_executed=False, cases=[])
    try:
        if before:
            previous, baseline_sha = previous_historical_controls()
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(previous)
            record.report.update(baseline_commit=HISTORICAL_FIXTURE_READER,
                                 baseline_reader_sha256=baseline_sha, expected_checks=2)
            with control_git_diff(INTEGRATION_FIXTURE_BASE) as binding:
                record.report["fixture_git_binding"] = binding
                require(set(binding["paths"]) - READER_REPAIR_PATHS,
                        "fixture_integration_difference_missing")
                with (record.out / "tests.log").open("x") as log:
                    with redirect_stdout(log), redirect_stderr(log):
                        tested = unittest.TextTestRunner(stream=log, verbosity=2,
                            resultclass=legacy.EvidenceResult).run(suite)
            record.report.update(tests=tested.testsRun, failures=len(tested.failures),
                errors=len(tested.errors), skipped=len(tested.skipped),
                completed_checks=tested.testsRun - len(tested.failures) - len(tested.errors))
            if tested.first_cause:
                record.failure("baseline_fixture_selftest", tested.first_cause)
            require(tested.testsRun == 2 and len(tested.failures) == 2 and not tested.errors and
                    "historical_git_product_changed" in (tested.first_cause or ""),
                    "baseline_fixture_counterexample_not_observed")
        else:
            record.report["expected_checks"] = 3
            for name in ("compatible", "integration", "ordinary-old-evidence"):
                child = record.out / name
                child.mkdir()
                argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                        "--evidence-dir", str(child / "reader")]
                if name == "ordinary-old-evidence":
                    original = Path("/hf3fs-jd/data/tmp/opencad-config-crud-hardening-browser-20261012-v1")
                    require(digest((original / "result.json").read_bytes()) == HISTORICAL_RESULT_SHA,
                            "historical_result_anchor_mismatch")
                    argv += ["--verify-evidence", str(original)]
                    expected_rc, expected_cause = 1, "current_source_drift"
                else:
                    argv += ["--historical-fixture-case", name]
                    expected_rc = 0 if name == "compatible" else 1
                    expected_cause = None if name == "compatible" else "historical_git_product_changed"
                tested = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                write(child / "stdout.log", tested.stdout)
                write(child / "stderr.log", tested.stderr)
                actual = json.loads((child / "reader/result.json").read_bytes())
                record.report["cases"].append({"name": name, "argv": argv, "actual_rc": tested.returncode,
                    "first_cause": actual["first_cause"], "verdict": actual["verdict"],
                    "result_sha256": digest((child / "reader/result.json").read_bytes())})
                require(tested.returncode == expected_rc and
                        (actual["first_cause"] is None if expected_cause is None else
                         expected_cause in (actual["first_cause"] or "")),
                        "fixture_cli_boundary_mismatch:" + name)
                require(actual["source_drift"] == [] and actual["secondary_errors"] == [],
                        "fixture_cli_source_or_secondary_failure")
                record.report["completed_checks"] += 1
            record.report.update(verdict="PASS", intended_rc=0, completed=True)
    except Exception:
        record.failure("historical_fixture_acceptance", traceback.format_exc())
    record.capture("source_after")
    if record.report["source_drift"]:
        record.failure("source_drift", "source_drift")
    return record.publish()


def baseline_reader_bytes():
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                    "show", REVISION_BASE + ":" + OWNED[2]])


def baseline_history():
    data = baseline_reader_bytes()
    tree = ast.parse(data)
    functions = [item for item in tree.body if isinstance(item, ast.FunctionDef) and
                 item.name == "validate_history"]
    require(len(functions) == 1, "baseline_history_function_missing")
    namespace = {"require": require, "validate_response": validate_response, "json": json}
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 REVISION_BASE + ":" + OWNED[2], "exec"), namespace)
    return namespace["validate_history"]


def browser_semantics(data):
    stable = {"Review", "source", "terminal", "validate_requests", "validate_response",
              "artifact_manifest", "finalize_browser", "WORKFLOWS", "GEOMETRY",
              "SENTINEL_CHECKS", "HTTP_ROUTES", "LEDGER_KEYS", "OWNED", "READERS"}
    selected = {}
    for node in ast.parse(data).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in stable:
            selected[node.name] = ast.dump(node, include_attributes=False)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in stable:
                    selected[target.id] = ast.dump(node, include_attributes=False)
    require(set(selected) == stable, "historical_semantic_definitions_missing")
    return selected


def verify_historical_source(report, current):
    captured = report["source_after"]
    require(report["scope"] == "crud" and captured["head"] ==
            "bcadfdeb4ce7689df30cec168de8dbba16577435", "historical_producer_scope_mismatch")
    require(set(current["files"]) == set(captured["files"]), "historical_source_set_mismatch")
    changes = [name for name in current["files"] if current["files"][name] != captured["files"][name]]
    require(set(changes) <= READER_REPAIR_PATHS, "historical_product_source_changed")
    for name in READER_REPAIR_PATHS:
        data = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                       "show", REVISION_BASE + ":" + name])
        require(captured["files"][name]["sha256"] == digest(data) and
                captured["files"][name]["bytes"] == len(data) and
                current["files"][name]["mode"] == captured["files"][name]["mode"],
                "historical_repair_base_mismatch")
    changed_git = subprocess.check_output(
        ["git", "--no-optional-locks", "--no-replace-objects", "-C", str(ROOT),
         "diff", "--name-only", REVISION_BASE, "--"], text=True).splitlines()
    require(set(changed_git) <= READER_REPAIR_PATHS, "historical_git_product_changed")
    old_semantics = browser_semantics(baseline_reader_bytes())
    new_semantics = browser_semantics(Path(__file__).read_bytes())
    require(new_semantics == old_semantics, "historical_browser_semantics_changed")
    return {"baseline_commit": REVISION_BASE, "producer_source": captured,
            "reader_source": current, "changed_paths": changes,
            "product_bytes_modes_equal": True, "browser_semantics_equal": True,
            "semantic_sha256": digest(canonical(old_semantics)), "browser_executed": False}


def verify_historical(report, original):
    data = (original / "result.json").read_bytes()
    require(digest(data) == HISTORICAL_RESULT_SHA, "historical_result_anchor_mismatch")
    require(json.loads(data) == report, "historical_result_not_original")
    compatibility = verify_historical_source(report, source())
    count = verify_bound(report, original, current=False,
                         producer_root=Path(report["source_after"]["root"]))
    require(json.loads((original / "terminal-candidate.json").read_bytes()) == report,
            "terminal_candidate_mismatch")
    require((original / "completion.log").read_text() ==
            f"{MARKER} verdict=PASS checks={count} rc=0\n", "completion_marker_mismatch")
    return count, compatibility


def revision_case(record, path, use_baseline):
    try:
        require(path.resolve().is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()),
                "external_revision_case_required")
        data = path.read_bytes()
        report = json.loads(data)
        checker = baseline_history() if use_baseline else validate_history
        for width in ("1440", "390"):
            checker(report, width)
        record.report.update(verdict="HISTORY_CASE_ACCEPTED", intended_rc=0, completed=True,
                             completed_checks=4, expected_checks=4)
    except Exception:
        record.failure("history_revision_case", traceback.format_exc())
    record.report.update(case_path=str(path), baseline=use_baseline, browser_executed=False)
    record.capture("source_after")
    if record.report["source_drift"]:
        record.failure("source_drift", "source_drift")
    return record.publish()


def revision_controls(record, original, *, before):
    try:
        data = (original / "result.json").read_bytes()
        require(digest(data) == HISTORICAL_RESULT_SHA, "historical_result_anchor_mismatch")
        report = json.loads(data)
        baseline = baseline_history()
        for width in ("1440", "390"):
            baseline(report, width)
        record.report.update(original_result_sha256=digest(data),
            original_source=report["source_after"], baseline_commit=REVISION_BASE,
            baseline_reader_sha256=digest(baseline_reader_bytes()), browser_executed=False,
            cases=[], expected_checks=8)
        if not before:
            _, compatibility = verify_historical(report, original)
            record.report["compatibility"] = compatibility
        selected = {key: copy.deepcopy(report[key]) for key in ("history", "identities", "requests")}
        selected["requests"] = {width: [row for row in rows if row["route"] == "/api/command"]
                                for width, rows in selected["requests"].items()}
        for width in ("1440", "390"):
            for index in range(2):
                for mutation in ("wrong", "missing"):
                    name = width + "-" + selected["history"][width][index]["command"] + "-" + mutation
                    child = record.out / name
                    child.mkdir()
                    changed = copy.deepcopy(selected)
                    receipt = changed["history"][width][index]["receipt"]
                    if mutation == "wrong":
                        receipt["revision"] = 999
                    else:
                        del receipt["revision"]
                    case = child / "input.json"
                    write(case, changed)
                    argv = [sys.executable, "-B", str(Path(__file__).resolve()),
                            "--receipt-revision-case", str(case), "--evidence-dir", str(child / "reader")]
                    if before:
                        argv.append("--baseline-history")
                    tested = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    write(child / "stdout.log", tested.stdout)
                    write(child / "stderr.log", tested.stderr)
                    actual = json.loads((child / "reader/result.json").read_bytes())
                    record.report["cases"].append({"name": name, "argv": argv,
                        "actual_rc": tested.returncode, "first_cause": actual["first_cause"],
                        "verdict": actual["verdict"], "input_sha256": digest(case.read_bytes())})
                    require(actual["source_drift"] == [] and actual["secondary_errors"] == [],
                            "revision_case_source_or_secondary_failure")
                    if not before:
                        require(tested.returncode == 1 and
                                "command_receipt_revision_mismatch" in (actual["first_cause"] or ""),
                                "receipt_revision_mutant_not_rejected:" + name)
                    record.report["completed_checks"] += 1
        if before:
            require(not any(row["actual_rc"] == 0 for row in record.report["cases"]),
                    "baseline_receipt_revision_mutants_accepted")
        record.report.update(verdict="PASS", intended_rc=0, completed=True)
    except Exception:
        record.failure("receipt_revision_controls", traceback.format_exc())
    record.capture("source_after")
    if record.report["source_drift"]:
        record.failure("source_drift", "source_drift")
    return record.publish()


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
            report["first_cause"] is None and not report["cleanup_errors"] and
            report["secondary_errors"] == [] and report["completed"] is True, "producer_failed")
    require(type(report["source_before"]) is dict and type(report["source_after"]) is dict and
            report["source_before"] == report["source_after"] and report["source_drift"] == [],
            "source_drift")
    checks = report["checks"]
    expected = SENTINEL_CHECKS if report["scope"] == "sentinel" else expected_checks()
    require(report["scope"] in {"sentinel", "crud"} and len(checks) == len(expected) and
            report["expected_checks"] == len(expected) and report["completed_checks"] == len(expected) and
            {row["name"] for row in checks} == expected and
            all(row["pass"] is True for row in checks), "workflow_ledger_mismatch")
    require(len(report["cli"]) == (1 if report["scope"] == "sentinel" else 4) and all(
        row["ready"] is True and row["exit_rc"] == 0 and
        row["argv"] == row["observed_argv"] and row["startticks"] == row["stop_startticks"] and
        type(row["startticks"]) is int and row["startticks"] > 0 and
        type(row["pid"]) is int and row["pid"] > 0 for row in report["cli"]),
        "cli_terminal_mismatch")
    return len(checks)


def validate_requests(report):
    for rows in [*report["requests"].values(), report["actor_requests"]]:
        require(rows, "response_ledger_empty")
        for row in rows:
            validate_response(row)
    for width in (1440, 390):
        requests = report["requests"][str(width)]
        identities = list(report["identities"][str(width)].values())
        require(all(row["identity"] in identities for row in requests), "response_identity_outside_workflow")
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
        validate_history(report, str(width))
    allowed = [identity for groups in report["identities"].values() for identity in groups.values()]
    require(all(row["identity"] in allowed for row in report["actor_requests"]), "actor_identity_outside_workflow")


def validate_history(report, width):
    rows = report["requests"][width]
    history = report["history"][width]
    require(len(history) == 2 and [item["command"] for item in history] == ["undo", "redo"],
            "history_ledger_incomplete")
    identity = report["identities"][width]
    require(identity["first"]["owner_id"] != identity["second"]["owner_id"] and
            identity["first"]["project_id"] != identity["second"]["project_id"], "identity_not_isolated")
    for item in history:
        matches = [row for row in rows if row["route"] == "/api/command" and
                   row["body"] and row["body"].get("request_id") == item["request"]["request_id"]]
        require(len(matches) == 1 and matches[0]["method"] == "POST" and matches[0]["status"] == 200 and
                matches[0]["body"] == item["request"] and
                item["request"]["command"] == item["command"] and item["request"]["args"] == {} and
                matches[0]["request_headers"].get("content-type") == "application/json" and
                matches[0]["request_headers"].get("x-opencad-request") == "1", "command_response_missing")
        row = matches[0]
        payload = json.loads(validate_response(row))
        require(type(item["receipt"].get("revision")) is int and
                item["receipt"]["revision"] == item["after"]["revision"],
                "command_receipt_revision_mismatch")
        require(row["identity"] == identity["first"] and
                row["request_headers"]["x-opencad-project"] == identity["first"]["project_id"] and
                payload["project_id"] == identity["first"]["project_id"] and
                payload["revision"] == item["after"]["revision"] == item["before"]["revision"] + 1 and
                payload["sha256"] == item["after"]["sha256"] == item["receipt"]["sha256"] and
                item["request"]["expected_revision"] == item["before"]["revision"] and
                item["request"]["expected_sha"] == item["before"]["sha256"] and
                item["after"]["receipts"] == item["before"]["receipts"] + 1 and
                item["receipt"]["request_id"] == item["request"]["request_id"],
                "command_identity_receipt_mismatch")
        for key, value in row["response"]["headers"].items():
            if key == "x-opencad-revision":
                require(value == str(payload["revision"]), "command_header_revision_mismatch")
            if key == "x-opencad-snapshot-sha256":
                require(value == payload["sha256"], "command_header_sha_mismatch")


def validate_response(row):
    require(type(row["status"]) is int and 100 <= row["status"] <= 599 and
            row["method"] in {"GET", "POST"}, "http_response_status_invalid")
    item = row["response"]
    data = base64.b64decode(item["body_base64"], validate=True)
    require(type(item["bytes"]) is int and len(data) == item["bytes"] and
            digest(data) == item["sha256"], "http_response_bytes_mismatch")
    require(type(item["headers"]) is dict and all(
        (key == "content-type" or key.startswith("x-opencad-")) and type(value) is str
        for key, value in item["headers"].items()) and "content-type" in item["headers"],
        "http_response_headers_outside_scope")
    require(row["route"] in HTTP_ROUTES and type(row["request_headers"]) is dict and
            all(key in {"content-type", "x-opencad-project", "x-opencad-request"} and
                type(value) is str for key, value in row["request_headers"].items()),
            "http_request_headers_outside_scope")
    require(set(row["identity"]) == {"owner_id", "project_id"} and
            all(type(value) is str and value for value in row["identity"].values()) and
            row["request_headers"].get("x-opencad-project") == row["identity"]["project_id"],
            "http_identity_binding_missing")
    if item["headers"].get("content-type", "").startswith("application/json"):
        value = json.loads(data)
        require(type(value) is dict and (value.get("ok") is True if row["status"] < 400 else
                value.get("ok") is False), "http_response_payload_mismatch")
    return data


def verify_bound(report, out, *, current=True, producer_root=None):
    count = terminal(report)
    if current:
        require(source() == report["source_after"], "current_source_drift")
    captured = report["source_after"]
    producer_root = ROOT.resolve() if producer_root is None else producer_root
    require(captured["root"] == str(producer_root) and len(captured["owned"]) == 4 and
            captured["owned"] == {name: captured["files"][name] for name in OWNED} and
            captured["readers"] == {name: captured["files"][name] for name in READERS} and
            digest(canonical(captured["files"])) == captured["sha256"], "source_binding_mismatch")
    actual = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
              and path.relative_to(out).as_posix() not in {
                  "result.json", "terminal-candidate.json", "completion.log",
                  "result-failure.pending", "completion-failure.pending"}}
    require(actual == set(report["artifacts"]) and
            {"checks.log", "http-ledger.json"} <= actual, "artifact_set_mismatch")
    for name, item in report["artifacts"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "artifact_escape")
        legacy.validate_artifact(out / name, item)
    require([json.loads(line) for line in (out / "checks.log").read_text().splitlines()] ==
            report["checks"], "check_log_mismatch")
    require(json.loads((out / "http-ledger.json").read_bytes()) ==
            {key: report[key] for key in LEDGER_KEYS},
            "http_ledger_mismatch")
    if report["scope"] == "crud":
        validate_requests(report)
    else:
        rows = report["requests"]["1440"]
        for row in [*rows, *report["actor_requests"]]:
            validate_response(row)
        operation = report["operations"]["1440"][-1]
        require(operation["request"]["action"] == "delete_configuration" and
                operation["request"]["configuration"] == "unselected" and
                operation["after"]["revision"] == operation["before"]["revision"] + 1 and
                operation["after"]["receipts"] == operation["before"]["receipts"] + 1,
                "sentinel_receipt_missing")
    for number, cli in enumerate(report["cli"], 1):
        require(cli["log"] == f"cli-{number}.log" and
                cli["argv"] == [sys.executable, "-B", str(producer_root /
                    "oss-alternatives/apps/opencad/secure_web.py"), "--data-root",
                    report["runtime_root"], "--host", "127.0.0.1", "--port", str(cli["port"]),
                    "--public-origin", cli["origin"]] and
                cli["origin"] == f"http://127.0.0.1:{cli['port']}" and
                f"OPENCAD_PRIVATE_WEB verdict=READY port={cli['port']}\n" in
                (out / cli["log"]).read_text(), "cli_scope_mismatch")
    if report["scope"] == "sentinel":
        png = (out / "sentinel/legal_unselected_replacement.png").read_bytes()
        require(png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000, "screenshot_missing")
        return count
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
    for width in ("1440", "390"):
        for row in report["history"][width]:
            witness = json.loads((out / f"{width}/{row['command']}.json").read_bytes())
            require(all(witness["state"][key] == row["after"][key] for key in ("revision", "sha256")) and
                    witness["state"]["project_id"] == report["identities"][width]["first"]["project_id"],
                    "command_geometry_identity_mismatch")
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
    def test_private_clear_selector_omission_negative(self):
        data = (ROOT / OWNED[0]).read_bytes()
        selector = b'panel.querySelectorAll("button, input, select")'
        self.assertEqual(data.count(selector), 1, "selector_mutation_anchor_not_exact")
        for replacement in (b'panel.querySelectorAll("button, select")',
                            b'panel.querySelectorAll("input, select")',
                            b'panel.querySelectorAll("button, input")'):
            for temporary in (False, True):
                tested = selector_fixture_probe(self.control_child(),
                    data.replace(selector, replacement), temporary=temporary)
                self.assertEqual(tested.returncode, 1,
                    "selector_fixture_accepted_omitted_control:" + replacement.decode() +
                    "\n" + tested.stdout.decode() + tested.stderr.decode())
                self.assertIn("private_clear_" + ("temporary_" if temporary else "") +
                              "action_gate", tested.stderr.decode())

    def test_private_clear_unknown_selector_fail_loud(self):
        data = (ROOT / OWNED[0]).read_bytes()
        for original, changed, temporary in (
            (b'panel.querySelectorAll("button, input, select")',
             b'panel.querySelectorAll("button, input, select, textarea")', False),
            (b'panel.querySelectorAll("[data-parametric-create]").forEach(function (button) {\n'
             b'      button.hidden = true;',
             b'panel.querySelectorAll("[data-parametric-create-other]").forEach(function (button) {\n'
             b'      button.hidden = true;', False),
            (b'definitionForm.querySelectorAll("[data-definition-v2]")',
             b'definitionForm.querySelectorAll("[data-definition-unknown]")', True),
        ):
            self.assertEqual(data.count(original), 1, "unknown_selector_anchor_not_exact")
            tested = private_clear_probe(self.control_child(),
                data=data.replace(original, changed), temporary=temporary)
            self.assertEqual(tested.returncode, 1)
            self.assertIn("private_clear_unknown_selector:", tested.stderr.decode())

    def test_private_clear_permanent_private_state(self):
        tested = private_clear_probe(self.control_child())
        self.assertEqual(tested.returncode, 0, tested.stderr.decode())
        self.assertTrue(json.loads(tested.stdout)["real_js_executed"])

    def test_private_clear_temporary_preserves_both_drafts(self):
        tested = private_clear_probe(self.control_child(), temporary=True)
        self.assertEqual(tested.returncode, 0, tested.stderr.decode())
        self.assertFalse(json.loads(tested.stdout)["state"]["expired"])

    def test_private_clear_missing_cleanup_negative(self):
        data = (ROOT / OWNED[0]).read_bytes()
        for line, cause in (
            (b"      deleteReplacement.replaceChildren();\n",
             "private_clear_replacement_options_left"),
            (b"      delete definitionForm.dataset.editingTarget;\n",
             "private_clear_editing_target_left"),
            (b'      definitionMode = "edit"; selectionPolicy.value = "keep"; bindingError = null;\n',
             "private_clear_definition_selection_left"),
        ):
            self.assertEqual(data.count(line), 1, "cleanup_mutation_anchor_not_exact")
            tested = private_clear_probe(self.control_child(), data=data.replace(line, b""))
            self.assertEqual(tested.returncode, 1)
            self.assertIn(cause, tested.stderr.decode())

    @staticmethod
    def response_row():
        data = b'{"ok":true,"revision":2,"sha256":"synthetic"}'
        return {"route": "/api/parametric", "method": "POST", "status": 200, "body": {},
                "identity": {"owner_id": "alice", "project_id": "project"},
                "request_headers": {"x-opencad-project": "project", "content-type": "application/json",
                                    "x-opencad-request": "1"},
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
                     for width in (1440, 390)}, "history": {}, "identities": {}}

    def test_real_ledger_validator_synthetic_positive(self):
        report = self.request_report()
        self.add_history(report)
        validate_requests(report)

    def add_history(self, report):
        for width in ("1440", "390"):
            records = []
            for index, command in enumerate(("undo", "redo"), 1):
                row = self.response_row()
                row.update(route="/api/command", body={"command": command, "args": {},
                    "expected_revision": index, "expected_sha": "b" * 64, "request_id": command})
                data = canonical({"ok": True, "project_id": "project", "revision": index + 1,
                                  "sha256": "a" * 64})
                row["response"].update(body_base64=base64.b64encode(data).decode(),
                                       bytes=len(data), sha256=digest(data))
                report["requests"][width].append(row)
                records.append({"command": command, "request": row["body"],
                    "before": {"revision": index, "sha256": "b" * 64, "receipts": index},
                    "after": {"revision": index + 1, "sha256": "a" * 64, "receipts": index + 1},
                    "receipt": {"request_id": command, "revision": index + 1, "sha256": "a" * 64}})
            report["history"][width] = records
            report["identities"][width] = {"first": {"owner_id": "alice", "project_id": "project"},
                                         "second": {"owner_id": "bob", "project_id": "other"}}

    def test_command_body_header_identity_and_missing_negative(self):
        for kind in ("missing", "identity", "header", "body", "receipts"):
            report = self.request_report()
            self.add_history(report)
            row = report["requests"]["1440"][-2]
            if kind == "missing": report["requests"]["1440"].remove(row)
            if kind == "identity": row["identity"]["owner_id"] = "bob"
            if kind == "header": row["request_headers"]["x-opencad-project"] = "other"
            if kind == "body": row["body"]["command"] = "different"
            if kind == "receipts": report["history"]["1440"][0]["after"]["receipts"] += 1
            with self.assertRaises(AssertionError): validate_history(report, "1440")

    def test_command_receipt_revision_wrong_missing_and_type_negative(self):
        for width in ("1440", "390"):
            for index in (0, 1):
                for value in (999, None, True, "2", "missing"):
                    report = self.request_report()
                    self.add_history(report)
                    validate_history(report, width)
                    receipt = report["history"][width][index]["receipt"]
                    if value == "missing":
                        del receipt["revision"]
                    else:
                        receipt["revision"] = value
                    with self.assertRaisesRegex(AssertionError, "command_receipt_revision_mismatch"):
                        validate_history(report, width)

    def historical_fixture(self):
        archive = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                           "archive", "--format=tar", REVISION_BASE])
        files = {}
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tree:
            for item in tree:
                if item.isdir():
                    continue
                require(item.isfile() and not Path(item.name).is_absolute() and
                        ".." not in Path(item.name).parts, "historical_fixture_nonregular")
                data = tree.extractfile(item).read()
                files[item.name] = {"bytes": len(data), "sha256": digest(data),
                                    "mode": item.mode & 0o777}
        require(set(OWNED + READERS) <= set(files), "historical_fixture_source_missing")
        captured = {"head": "bcadfdeb4ce7689df30cec168de8dbba16577435", "files": files,
                    "fixture_commit": REVISION_BASE, "sha256": digest(canonical(files))}
        current = copy.deepcopy(captured)
        for name in READER_REPAIR_PATHS:
            data = (ROOT / name).read_bytes()
            current["files"][name].update(bytes=len(data), sha256=digest(data))
        current["sha256"] = digest(canonical(current["files"]))
        original = {"scope": "crud", "source_after": captured}
        return original, current

    def test_historical_product_source_set_mode_and_head_negative(self):
        original, current = self.historical_fixture()
        with control_git_diff(HISTORICAL_FIXTURE_READER) as binding, control_historical_browser_source():
            self.assertEqual(set(binding["paths"]), READER_REPAIR_PATHS)
            self.assertTrue(verify_historical_source(original, current)["product_bytes_modes_equal"])
            for kind in ("product", "set", "mode", "head"):
                report, changed = copy.deepcopy(original), copy.deepcopy(current)
                if kind == "product":
                    changed["files"][OWNED[0]]["sha256"] = "0" * 64
                if kind == "set":
                    del changed["files"][OWNED[0]]
                if kind == "mode":
                    changed["files"][OWNED[2]]["mode"] = 0
                if kind == "head":
                    report["source_after"]["head"] = "0" * 40
                with self.assertRaises(AssertionError):
                    verify_historical_source(report, changed)

    def test_historical_browser_semantic_change_negative(self):
        data = baseline_reader_bytes()
        original = browser_semantics(data)
        changed = data.replace(b"class Review(legacy.Review):",
                               b"class Review(object):")
        self.assertNotEqual(browser_semantics(changed), original)
        report, current = self.historical_fixture()
        with control_git_diff(HISTORICAL_FIXTURE_READER), control_historical_browser_source():
            with patch.object(sys.modules[__name__], "baseline_reader_bytes", return_value=changed):
                with self.assertRaisesRegex(AssertionError, "historical_browser_semantics_changed"):
                    verify_historical_source(report, current)

    def test_current_private_clear_source_is_not_historical_browser(self):
        original, current = self.historical_fixture()
        with control_git_diff(HISTORICAL_FIXTURE_READER):
            with self.assertRaisesRegex(AssertionError, "historical_browser_semantics_changed"):
                verify_historical_source(original, current)
            data = (ROOT / OWNED[0]).read_bytes()
            current["files"][OWNED[0]].update(bytes=len(data), sha256=digest(data))
            with self.assertRaisesRegex(AssertionError, "historical_product_source_changed"):
                verify_historical_source(original, current)

    def test_actual_integration_git_difference_remains_rejected(self):
        original, current = self.historical_fixture()
        with control_git_diff(INTEGRATION_FIXTURE_BASE) as binding:
            self.assertGreater(len(set(binding["paths"]) - READER_REPAIR_PATHS), 0)
            with self.assertRaisesRegex(AssertionError, "historical_git_product_changed"):
                verify_historical_source(original, current)
        actual = subprocess.check_output(historical_diff_command(), text=True).splitlines()
        if set(actual) - READER_REPAIR_PATHS:
            with self.assertRaisesRegex(AssertionError, "historical_git_product_changed"):
                verify_historical_source(original, current)
        else:
            self.assertTrue(verify_historical_source(original, current)["product_bytes_modes_equal"])

    def test_production_reader_functions_and_allowlist_unchanged(self):
        baseline = subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
            "show", HISTORICAL_FIXTURE_READER + ":" + OWNED[2]])
        names = {"verify_historical_source", "verify_historical", "verify_bound", "source",
                 "terminal", "validate_history", "browser_semantics"}

        def definitions(data):
            tree = ast.parse(data)
            functions = {node.name: ast.dump(node, include_attributes=False) for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name in names}
            self.assertEqual(set(functions), names)
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and
                        target.id == "READER_REPAIR_PATHS" for target in node.targets):
                    functions["READER_REPAIR_PATHS"] = ast.dump(node, include_attributes=False)
            self.assertIn("READER_REPAIR_PATHS", functions)
            return functions

        self.assertEqual(definitions(baseline), definitions(Path(__file__).read_bytes()))

    def test_single_receipt_and_response_binding_negative(self):
        for kind in ("revision", "receipts", "receipt", "response_missing"):
            changed = self.request_report()
            self.add_history(changed)
            operation = changed["operations"]["1440"][0]
            if kind in {"revision", "receipts"}: operation["after"][kind] += 1
            if kind == "receipt": operation["receipt"]["sha256"] = "0" * 64
            if kind == "response_missing": operation["request"]["request_id"] = "unmeasured"
            with self.assertRaises(AssertionError): validate_requests(changed)

    def test_boolean_open_and_exact_replay_negative(self):
        for kind in ("boolean", "open", "replay_body", "replay_receipt", "response_bytes"):
            changed = self.request_report()
            self.add_history(changed)
            rows = changed["requests"]["1440"]
            if kind == "boolean": rows[0]["body"]["definition"]["suppressions"]["hole_cut"] = 1
            if kind == "open": rows.append({**copy.deepcopy(rows[0]), "route": "/api/open"})
            if kind == "replay_body": rows[-3]["body"]["name"] = "changed"
            if kind == "replay_receipt": changed["loss"]["1440"]["after_replay"] = {}
            if kind == "response_bytes": rows[-3]["response"]["sha256"] = "0" * 64
            with self.assertRaises(AssertionError): validate_requests(changed)

    def valid(self):
        return {"verdict": "PASS", "intended_rc": 0, "first_cause": None, "cleanup_errors": [],
                "secondary_errors": [], "completed": True, "scope": "crud",
                "expected_checks": 18, "completed_checks": 18,
                "source_before": {"head": "a", "files": {}}, "source_after": {"head": "a", "files": {}},
                "source_drift": [], "checks": [{"name": name, "pass": True} for name in expected_checks()],
                "cli": [{"ready": True, "exit_rc": 0, "argv": ["synthetic"], "observed_argv": ["synthetic"],
                         "startticks": 1, "stop_startticks": 1, "pid": 1} for _ in range(4)]}

    def test_null_source_secondary_and_completion_negative(self):
        for key, value in (("source_after", None), ("source_before", None),
                           ("secondary_errors", [{"phase": "close", "cause": "synthetic"}]),
                           ("completed", False), ("completed_checks", 17)):
            report = self.valid()
            report[key] = value
            with self.assertRaises(AssertionError): terminal(report)

    def control_child(self):
        root = CONTROL_RUNTIME or Path(os.environ.get("OPENCAD_TEST_RUNTIME_ROOT", "/data/tmp"))
        child = root / ("crud-terminal-control-" + uuid4().hex)
        child.mkdir()
        return child

    def test_source_read_failure_preserves_firstcause_and_null(self):
        record = TerminalRecord(self.control_child(), "controls")
        record.failure("scenario", "original-first")
        with patch.object(sys.modules[__name__], "source", side_effect=RuntimeError("source-secondary")):
            self.assertIsNone(record.capture("source_after"))
        self.assertEqual(record.report["first_cause"], "original-first")
        self.assertIsNone(record.report["source_after"])
        self.assertIn("source-secondary", record.report["secondary_errors"][0]["cause"])
        self.assertEqual(record.publish(), 1)
        saved = json.loads((record.out / "result.json").read_bytes())
        self.assertFalse(saved["completed"])

    def test_finalize_logclose_failure_is_secondary(self):
        record = TerminalRecord(self.control_child(), "sentinel")
        record.report.update(source_before=source(), expected_checks=1)
        record.failure("scenario", "first-original")

        def broken_close():
            raise OSError("synthetic-log-close")

        review = SimpleNamespace(stop=lambda: None, kernel=SimpleNamespace(close=lambda: None),
            log=SimpleNamespace(close=broken_close), checks=[], cli=[], cleanup_errors=[],
            requests={}, actor_requests=[], operations={}, loss={}, downloads={},
            measurements=[], history={}, identities={}, secrets=[])
        self.assertEqual(finalize_browser(record, review), 1)
        saved = json.loads((record.out / "result.json").read_bytes())
        self.assertEqual(saved["first_cause"], "first-original")
        self.assertTrue(any(row["phase"] == "log_close" and "synthetic-log-close" in row["cause"]
                            for row in saved["secondary_errors"]))

    def test_terminal_each_output_fault_fail_loud(self):
        for target in ("failure.log", "result.json", "completion.log"):
            record = TerminalRecord(self.control_child(), "controls")
            record.failure("scenario", "first-original")
            original_write, invoked = write, []

            def broken_write(path, value):
                if Path(path).name == target:
                    invoked.append(target)
                    raise OSError("synthetic-write-" + target)
                return original_write(path, value)

            with patch.object(sys.modules[__name__], "write", side_effect=broken_write):
                self.assertEqual(record.publish(), 1)
            self.assertEqual(invoked, [target])
            saved = json.loads((record.out / "result.json").read_bytes())
            self.assertEqual(saved["first_cause"], "first-original")
            self.assertFalse(saved["completed"])
            self.assertIn("verdict=FAIL", (record.out / "completion.log").read_text())
            self.assertTrue(any("synthetic-write-" in row["cause"] for row in saved["secondary_errors"]))

    def test_terminal_artifact_fault_cannot_publish_pass(self):
        record = TerminalRecord(self.control_child(), "controls")
        record.report.update(verdict="PASS", intended_rc=0, completed=True)
        with patch.object(sys.modules[__name__], "artifact_manifest", side_effect=OSError("artifact-first")):
            self.assertEqual(record.publish(), 1)
        self.assertIn("artifact-first", record.report["first_cause"])
        self.assertIsNone(record.report["artifacts"])
        self.assertFalse(json.loads((record.out / "result.json").read_bytes())["completed"])

    def test_http_ledger_write_failure_preserves_firstcause(self):
        record = TerminalRecord(self.control_child(), "sentinel")
        record.report.update(source_before=source(), expected_checks=1)
        record.failure("scenario", "first-original")
        original_write = write

        def broken_write(path, value):
            if Path(path).name == "http-ledger.json":
                raise OSError("synthetic-ledger-write")
            return original_write(path, value)

        with patch.object(sys.modules[__name__], "write", side_effect=broken_write):
            self.assertEqual(finalize_browser(record, None), 1)
        saved = json.loads((record.out / "result.json").read_bytes())
        self.assertEqual(saved["first_cause"], "first-original")
        self.assertTrue(any(row["phase"] == "http_ledger_write" for row in saved["secondary_errors"]))
        self.assertFalse(saved["completed"])

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
    def __init__(self, *args, record=None):
        super().__init__(*args)
        self.requests, self.operations, self.loss, self.downloads = {}, {}, {}, {}
        self.actor_requests = []
        self.measurements = []
        self.history, self.identities, self.cookie_owners = {}, {}, {}
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
            if record is not None:
                record.failure("kernel_init", traceback.format_exc())
                record.guard("init_log_close", self.log.close)
            else:
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

    def cookie(self, browser):
        cookie = super().cookie(browser)
        status, payload = self.http("GET", "/api/auth/me", cookie)
        require(status == 200 and payload["user"]["user_id"] == browser.evaluate("window.__crudOwner") and
                payload["user"]["user_id"] in {account["user_id"] for account in self.accounts.values()},
                "browser_owner_probe_mismatch")
        self.cookie_owners[cookie] = payload["user"]["user_id"]
        return cookie

    def reload(self, browser):
        self.requests[str(browser.width)].extend(browser.evaluate("window.__crudRequests"))
        page = browser.evaluate("window.__crudPage")
        browser.call("Page.reload", {})
        browser.wait("!!window.__crudPage && window.__crudPage!==" + json.dumps(page))

    def record_response(self, method, route, body, response, data, headers, cookie, project):
        self.actor_requests.append({"method": method, "route": route, "body": body,
            "identity": {"owner_id": self.cookie_owners[cookie], "project_id": project},
            "request_headers": {key.lower(): value for key, value in headers.items()
                                if key.lower() in {"content-type", "x-opencad-request", "x-opencad-project"}},
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
                self.record_response(method, path, body, response, data, headers, cookie, project)
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

    def confirm_history(self, browser, cookie, project, before, command):
        after = self.anchor(cookie, project)
        rows = browser.evaluate("window.__crudRequests.filter(r=>r.route==='/api/command'"
                                "&&r.body&&r.status===200)")
        row = rows[-1]
        require(row["body"]["command"] == command, "history_actual_command_mismatch")
        from project_store import ProjectStore
        store = ProjectStore(self.runtime / "projects")
        try:
            receipt = store.read_receipt(project, row["body"]["request_id"],
                                        request_payload={**row["body"], "route": "/api/command"})
        finally:
            store.close()
        require(receipt is not None and after["revision"] == before["revision"] + 1 and
                after["receipts"] == before["receipts"] + 1, "history_single_receipt_failed")
        self.history[str(browser.width)].append({"command": command, "before": before, "after": after,
            "request": row["body"], "receipt": {key: receipt[key] for key in ("request_id", "revision", "sha256")}})
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
            headers = {"Cookie": "opencad_session=" + cookie, "X-OpenCAD-Project": project}
            conn.request("GET", "/api/step", headers=headers)
            response = conn.getresponse()
            step = response.read()
            self.record_response("GET", "/api/step", None, response, step, headers, cookie, project)
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

    @staticmethod
    def capture_script(browser):
        browser.call("Page.addScriptToEvaluateOnNewDocument", {"source":
            "const raw=window.fetch.bind(window);window.__crudRequests=[];"
            "window.__crudPage=crypto.randomUUID();window.__crudAuth=null;window.__crudOwner=null;"
            "window.fetch=async function(i,o){const p=new URL(typeof i==='string'?i:i.url,"
            "location.href).pathname;const requestHeaders={};const hs=new Headers(o?.headers||{});"
            "hs.forEach((v,k)=>{if(['content-type','x-opencad-project','x-opencad-request'].includes(k))"
            "requestHeaders[k]=v;});const identity={owner_id:window.__crudOwner,"
            "project_id:hs.get('x-opencad-project')};const r=await raw(i,o);"
            "if(p==='/api/auth/me'){if(r.ok){const v=await r.clone().json();"
            "window.__crudOwner=v.user.user_id;}window.__crudAuth=r.status;}"
            "if(p==='/api/auth/login'&&r.ok){const v=await r.clone().json();window.__crudOwner=v.user.user_id;}"
            "if(['/api/parametric','/api/state','/api/mesh','/api/step','/api/save','/api/command',"
            "'/api/open'].includes(p)){const b=await r.clone().arrayBuffer();"
            "const h=await crypto.subtle.digest('SHA-256',b);const bytes=new Uint8Array(b);"
            "let binary='';for(let n=0;n<bytes.length;n+=8192){binary+="
            "String.fromCharCode(...bytes.subarray(n,n+8192));}const headers={};"
            "r.headers.forEach((v,k)=>{if(k==='content-type'||k.startsWith('x-opencad-'))headers[k]=v;});"
            "window.__crudRequests.push({route:p,method:o?.method||'GET',status:r.status,"
            "identity:identity,request_headers:requestHeaders,"
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

    def sentinel_scenario(self):
        self.operations["1440"] = []
        Browser = self.browser_class()
        with Browser(self.binary, self.out / "sentinel", width=1440, height=900,
                     source_root=self.root) as browser:
            self.capture_script(browser)
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            self.login(browser, "config-alice")
            cookie = self.cookie(browser)
            project = browser.evaluate("document.querySelector('#parametric-panel').dataset.projectId")
            before = self.anchor(cookie, project)
            browser.click('[data-parametric-create="create_configurable_plate"]')
            self.confirm(browser, cookie, project, before)
            browser.click("[data-definition-new]")
            self.type(browser, "[data-definition-name]", "unselected")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-create]")
            self.confirm(browser, cookie, project, before)
            self.choose(browser, "[data-definition-target]", "wide")
            self.choose(browser, "[data-definition-selection]", "target")
            before = self.anchor(cookie, project)
            browser.click("[data-definition-replace]")
            self.confirm(browser, cookie, project, before)
            before = self.anchor(cookie, project)
            browser.click("[data-definition-delete]")
            self.choose(browser, "[data-definition-replacement]", "unselected")
            browser.click("[data-definition-confirm-delete]")
            deadline = time.monotonic() + 20
            while True:
                if browser.evaluate("document.querySelector('.parametric-notice').textContent.includes('明确选择')"):
                    require(self.anchor(cookie, project) == before, "sentinel_rejection_mutated")
                    raise AssertionError("legal_unselected_replacement_rejected")
                if self.anchor(cookie, project)["revision"] == before["revision"] + 1:
                    break
                require(time.monotonic() < deadline, "sentinel_delete_not_completed")
                time.sleep(.1)
            self.confirm(browser, cookie, project, before)
            state = self.state(cookie, project)
            require(json.loads(state["doc"])["parametric"]["active_configuration"] == "unselected" and
                    "wide" not in json.loads(state["doc"])["parametric"]["configurations"],
                    "sentinel_configuration_not_applied")
            self.workflow(browser, "legal_unselected_replacement", True)
            self.requests["1440"] = browser.evaluate("window.__crudRequests")

    def scenario(self, width, height):
        self.operations[str(width)] = []
        self.history[str(width)] = []
        self.requests[str(width)] = []
        Browser = self.browser_class()
        with Browser(self.binary, self.out / str(width), width=width, height=height,
                     source_root=self.root) as browser:
            self.capture_script(browser)
            browser.call("Page.navigate", {"url": self.origin + "/web/3d.html"})
            self.login(browser, "config-alice")
            cookie = self.cookie(browser)
            project = browser.evaluate("document.querySelector('#parametric-panel').dataset.projectId")
            self.identities[str(width)] = {"first": {
                "owner_id": self.cookie_owners[cookie], "project_id": project}}
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
            before_history = self.anchor(cookie, project)
            browser.click("#undo")
            browser.wait("!window.opencad3d.busy")
            self.confirm_history(browser, cookie, project, before_history, "undo")
            undone = self.geometry(browser, cookie, project, (1200-math.pi*36)*12, "undo")
            require("resume" not in json.loads(undone["doc"])["parametric"]["configurations"], "undo_definition_lost")
            before_history = self.anchor(cookie, project)
            browser.click("#redo")
            browser.wait("!window.opencad3d.busy")
            self.confirm_history(browser, cookie, project, before_history, "redo")
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
                    "document.querySelectorAll('[data-definition-replacement] option').length===0 && "
                    "document.querySelectorAll('[data-definition-target] option').length===0 && "
                    "!Object.prototype.hasOwnProperty.call(document.querySelector('.parametric-definition-editor').dataset,'editingTarget') && "
                    "document.querySelector('[data-definition-name]').value==='' && "
                    "document.querySelector('[data-definition-selection]').value==='keep' && "
                    "document.querySelector('.parametric-definition-editor').hidden && "
                    "Array.from(document.querySelectorAll('#parametric-panel button, #parametric-panel input, #parametric-panel select')).every(e=>e.disabled) && "
                    "!document.querySelector('#parametric-panel').dataset.projectId && "
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
                    "document.querySelectorAll('[data-definition-replacement] option').length===0 && "
                    "document.querySelectorAll('[data-definition-target] option').length===0 && "
                    "!Object.prototype.hasOwnProperty.call(document.querySelector('.parametric-definition-editor').dataset,'editingTarget') && "
                    "document.querySelector('[data-definition-name]').value==='' && "
                    "document.querySelector('[data-definition-selection]').value==='keep' && "
                    "document.querySelector('.parametric-definition-editor').hidden && "
                    "Array.from(document.querySelectorAll('#parametric-panel button, #parametric-panel input, #parametric-panel select')).every(e=>e.disabled) && "
                    "!document.querySelector('#parametric-panel').dataset.projectId && "
                    "!document.querySelector('.parametric-result').dataset.documentSha"),
                    "late_post_restored_revoked_private_dom")
            self.reload(browser)
            self.login(browser, "config-bob")
            second_cookie = self.cookie(browser)
            second_project = browser.evaluate("document.querySelector('#parametric-panel').dataset.projectId")
            self.identities[str(width)]["second"] = {
                "owner_id": self.cookie_owners[second_cookie], "project_id": second_project}
            state = self.state(second_cookie, second_project)
            require(second_project != project and not json.loads(state["doc"])["features"] and
                    browser.evaluate("document.querySelectorAll('[data-definition-parameter]').length===0"),
                    "second_identity_leaked")
            self.workflow(browser, WORKFLOWS[8], True)
            self.requests[str(width)].extend(browser.evaluate("window.__crudRequests"))


def artifact_manifest(out):
    return {path.relative_to(out).as_posix(): {"sha256": digest(path.read_bytes()),
            "bytes": path.stat().st_size} for path in out.rglob("*") if path.is_file() and
            path.relative_to(out).as_posix() not in {
                "result.json", "terminal-candidate.json", "completion.log", "result-failure.pending",
                "completion-failure.pending"}}


class TerminalRecord:
    """One producer owns this fresh directory; finalizer faults cannot erase first cause."""

    def __init__(self, out, scope):
        self.out = out
        self.report = {"verdict": "FAIL", "intended_rc": 1, "scope": scope, "completed": False,
            "first_cause": None, "first_phase": None, "secondary_errors": [], "source_before": None,
            "source_after": None, "source_drift": ["source_not_captured"], "started_at": now(),
            "finished_at": None, "completed_checks": 0, "expected_checks": None,
            "checks": [], "cleanup_errors": [], "cli": [], "artifacts": None}

    def failure(self, phase, cause):
        if self.report["first_cause"] is None:
            self.report.update(first_cause=cause, first_phase=phase)
        else:
            self.report["secondary_errors"].append({"phase": phase, "cause": cause})
        self.report.update(verdict="FAIL", intended_rc=1, completed=False)

    def guard(self, phase, operation):
        try:
            return operation()
        except Exception:
            self.failure(phase, traceback.format_exc())
            return None

    def capture(self, key):
        captured = self.guard(key, source)
        self.report[key] = captured
        before, after = self.report["source_before"], self.report["source_after"]
        self.report["source_drift"] = ([] if type(before) is dict and before == after else
                                       ["source_read_failed"] if captured is None else ["source"])
        return captured

    def publish(self):
        report = self.report
        failure_log_attempted = report["first_cause"] is not None
        if report["finished_at"] is None:
            report["finished_at"] = now()
        if report["first_cause"] is not None:
            self.guard("failure_log", lambda: write(self.out / "failure.log", report["first_cause"].encode()))
        report["artifacts"] = self.guard("artifact_manifest", lambda: artifact_manifest(self.out))
        self.guard("result_write", lambda: write(self.out / "result.json", report))
        self.guard("result_readback", lambda: require(
            json.loads((self.out / "result.json").read_bytes()) == report, "result_roundtrip_mismatch"))
        marker = self.marker()
        self.guard("completion_write", lambda: write(self.out / "completion.log", marker.encode()))
        self.guard("completion_readback", lambda: require(
            (self.out / "completion.log").read_text() == marker, "completion_marker_mismatch"))
        if report["first_cause"] is not None:
            # This is failure publication, not a retry of a product/test operation.
            report["artifacts"] = self.guard("failure_manifest", lambda: artifact_manifest(self.out))
            if not failure_log_attempted:
                self.guard("late_failure_log", lambda: write(
                    self.out / "failure.log", report["first_cause"].encode()))
                report["artifacts"] = self.guard("failure_manifest_refresh", lambda: artifact_manifest(self.out))
            self.guard("failure_result_publish", lambda: self.replace_failure("result.json", report))
            self.guard("failure_completion_publish", lambda: self.replace_failure(
                "completion.log", self.marker().encode()))
        print(self.marker().strip() + " evidence=" + str(self.out), flush=True)
        return report["intended_rc"]

    def replace_failure(self, name, value):
        pending = self.out / (name.split(".")[0] + "-failure.pending")
        write(pending, value)
        os.replace(pending, self.out / name)

    def marker(self):
        return (f"{MARKER} verdict={self.report['verdict']} "
                f"checks={self.report['completed_checks']} rc={self.report['intended_rc']}\n")


def finalize_browser(record, review):
    report = record.report
    if review is not None:
        for phase, action in (("cli_stop", review.stop), ("kernel_close", review.kernel.close),
                              ("log_close", review.log.close)):
            record.guard(phase, action)
        report.update({key: getattr(review, key) for key in (
            "checks", "cli", "cleanup_errors", "requests", "actor_requests", "operations", "loss",
            "downloads", "measurements", "history", "identities")})
    else:
        report.update({key: {} for key in ("requests", "operations", "loss", "downloads", "history", "identities")})
        report.update(actor_requests=[], measurements=[])
    record.capture("source_after")
    report["completed_checks"] = sum(row["pass"] is True for row in report["checks"])
    if report["source_drift"] or report["cleanup_errors"]:
        record.failure("source_or_cleanup", "source_drift_or_cleanup_failed")
    record.guard("http_ledger_write", lambda: write(
        record.out / "http-ledger.json", {key: report[key] for key in LEDGER_KEYS}))
    report["artifacts"] = record.guard("artifact_manifest", lambda: artifact_manifest(record.out))

    def secret_gate():
        require(report["artifacts"] is not None, "artifact_manifest_missing")
        if review is not None:
            for name in report["artifacts"]:
                require(not any(secret in (record.out / name).read_bytes() for secret in review.secrets),
                        "secret_in_evidence:" + name)
            for row in [*review.actor_requests, *(item for rows in review.requests.values() for item in rows)]:
                require(not any(secret in validate_response(row) for secret in review.secrets),
                        "secret_in_response_evidence")

    record.guard("secret_response_gate", secret_gate)
    if report["first_cause"] is None:
        report.update(verdict="PASS", intended_rc=0, completed=True)
        report["finished_at"] = now()
        record.guard("terminal_candidate_write", lambda: write(record.out / "terminal-candidate.json", report))
        saved = record.guard("terminal_candidate_read", lambda: json.loads(
            (record.out / "terminal-candidate.json").read_bytes()))
        record.guard("terminal_roundtrip", lambda: require(saved == report, "terminal_roundtrip_mismatch"))
        if report["first_cause"] is None:
            record.guard("terminal_gate", lambda: verify_bound(saved, record.out))
    return record.publish()


def main(argv=None):
    global CONTROL_RUNTIME, PRIVATE_CLEAR_BEFORE, SELECTOR_FIXTURE_BEFORE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--browser", default=legacy.DEFAULT_BROWSER)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--private-clear-before", action="store_true",
                        help="Exact 380 Git JS counterexample, no browser; requires --selftest.")
    parser.add_argument("--private-clear-only", action="store_true",
                        help="Only permanent/temporary shield execution and mutation controls.")
    parser.add_argument("--selector-fixture-before", action="store_true",
                        help="Exact 22de probe on omitted-input full JS; requires --selftest.")
    parser.add_argument("--verify-evidence", type=Path)
    parser.add_argument("--verify-historical-evidence", type=Path,
                        help="Pinned c112-compatible historical producer; never rebinding old evidence.")
    parser.add_argument("--sentinel-only", action="store_true",
                        help="Real legal-name collision regression, no full workflow verdict.")
    parser.add_argument("--receipt-revision-before", type=Path)
    parser.add_argument("--receipt-revision-controls", type=Path)
    parser.add_argument("--receipt-revision-case", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-history", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--historical-fixture-before", action="store_true",
                        help="Offline original d00 controls with the exact 7c Git difference.")
    parser.add_argument("--historical-fixture-controls", action="store_true",
                        help="Offline actual CLI compatible/integration/ordinary-reader boundaries.")
    parser.add_argument("--historical-fixture-case", choices=("compatible", "integration"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--integration-fixture-selftest", action="store_true",
                        help="Run the controls with the actual pinned 7c historical Git difference.")
    parser.add_argument("--discovery-check", action="store_true",
                        help="Use unittest discovery for this controls file only; requires --selftest.")
    args = parser.parse_args(argv)
    out = args.evidence_dir.resolve()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()), "external_evidence_required")
    out.mkdir(parents=True, exist_ok=False)
    scope = "receipt-revision" if (args.receipt_revision_before or args.receipt_revision_controls or
                                  args.receipt_revision_case) else "readback" if (
                                      args.verify_evidence or args.verify_historical_evidence) else "controls" if args.selftest else (
        "sentinel" if args.sentinel_only else "crud")
    record = TerminalRecord(out, scope)
    record.guard("source_root", lambda: require(args.source_root.resolve() == ROOT.resolve(),
                                                "runner_source_mismatch"))
    record.guard("fixture_selftest_scope", lambda: require(
        (not args.integration_fixture_selftest and not args.discovery_check and
         not args.private_clear_before and not args.private_clear_only and
         not args.selector_fixture_before) or args.selftest,
        "fixture_modes_require_selftest"))
    record.guard("private_clear_scope", lambda: require(
        not ((args.private_clear_before or args.private_clear_only) and (
            args.discovery_check or args.integration_fixture_selftest)) and
        not (args.private_clear_before and args.private_clear_only) and
        not (args.selector_fixture_before and (args.private_clear_before or args.private_clear_only or
             args.discovery_check or args.integration_fixture_selftest)),
        "private_clear_modes_conflict"))
    before = record.capture("source_before")
    if record.report["first_cause"] is not None:
        return record.publish()
    if args.historical_fixture_case:
        return historical_fixture_case(record, args.historical_fixture_case)
    if args.historical_fixture_before or args.historical_fixture_controls:
        return historical_fixture_acceptance(record, before=args.historical_fixture_before)
    if args.receipt_revision_case:
        return revision_case(record, args.receipt_revision_case, args.baseline_history)
    if args.receipt_revision_before or args.receipt_revision_controls:
        return revision_controls(record, (args.receipt_revision_before or
                                          args.receipt_revision_controls).resolve(),
                                 before=bool(args.receipt_revision_before))
    if args.verify_evidence or args.verify_historical_evidence:
        try:
            original = (args.verify_evidence or args.verify_historical_evidence).resolve()
            report = json.loads((original / "result.json").read_bytes())
            if args.verify_historical_evidence:
                count, compatibility = verify_historical(report, original)
                record.report.update(compatibility=compatibility, browser_executed=False,
                                     original_source=report["source_after"])
            else:
                count = verify_bound(report, original)
                require(json.loads((original / "terminal-candidate.json").read_bytes()) == report,
                        "terminal_candidate_mismatch")
                require((original / "completion.log").read_text() ==
                        f"{MARKER} verdict=PASS checks={count} rc=0\n", "completion_marker_mismatch")
            record.report.update(verdict="HISTORICAL_EVIDENCE_VERIFIED" if args.verify_historical_evidence
                                 else "EVIDENCE_VERIFIED", intended_rc=0, completed=True,
                completed_checks=count, expected_checks=count,
                original_result_sha256=digest((original / "result.json").read_bytes()))
        except Exception:
            record.failure("verify_evidence", traceback.format_exc())
        record.capture("source_after")
        if record.report["source_drift"]:
            record.failure("source_drift", "source_drift")
        return record.publish()
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
        record.failure("runtime_preflight", traceback.format_exc())
        record.capture("source_after")
        return record.publish()
    if args.selftest:
        CONTROL_RUNTIME = runtime
        PRIVATE_CLEAR_BEFORE = args.private_clear_before
        SELECTOR_FIXTURE_BEFORE = args.selector_fixture_before
        names = sorted(unittest.defaultTestLoader.getTestCaseNames(Controls))
        if args.selector_fixture_before:
            names = ["test_private_clear_selector_omission_negative"]
        elif args.private_clear_before:
            names = ["test_private_clear_permanent_private_state"]
        elif args.private_clear_only:
            names = [name for name in names if name.startswith("test_private_clear_")]
        record.report.update(browser_executed=False, runtime_root=str(runtime),
                             private_clear_baseline=PRIVATE_CLEAR_BASE if args.private_clear_before else None,
                             selector_fixture_baseline=SELECTOR_FIXTURE_BASE
                             if args.selector_fixture_before else None)
        tested, log = None, None
        try:
            if args.discovery_check:
                suite = unittest.defaultTestLoader.discover(str(APP / "tests"),
                    pattern=Path(__file__).name, top_level_dir=str(APP))
                discovered = sys.modules["tests." + Path(__file__).stem]
                require(Path(discovered.__file__).resolve() == Path(__file__).resolve(),
                        "foreign_discovery_module")
                discovered.CONTROL_RUNTIME = runtime
                require(suite.countTestCases() == len(names), "discovery_controls_count_mismatch")
                record.report["discovery_verified"] = True
            else:
                suite = unittest.TestSuite(Controls(name) for name in names)
            log = (out / "tests.log").open("x")
            fixture = control_git_diff(INTEGRATION_FIXTURE_BASE) if (
                args.integration_fixture_selftest) else nullcontext(None)
            with fixture as binding:
                if binding is not None:
                    record.report["fixture_git_binding"] = binding
                    require(set(binding["paths"]) - READER_REPAIR_PATHS,
                            "fixture_integration_difference_missing")
                with redirect_stdout(log), redirect_stderr(log):
                    tested = unittest.TextTestRunner(stream=log, verbosity=2,
                        resultclass=legacy.EvidenceResult, failfast=True).run(suite)
            if tested.first_cause: record.failure("selftest", tested.first_cause)
        except Exception:
            record.failure("selftest", traceback.format_exc())
        finally:
            if log is not None: record.guard("tests_log_close", log.close)
        def retain_private_probes():
            for binding in sorted(runtime.rglob("binding.json")):
                if not (binding.parent / "probe.cjs").is_file():
                    continue
                child = out / "private-clear-fixtures" / binding.parent.relative_to(runtime)
                child.mkdir(parents=True, exist_ok=False)
                for name in ("binding.json", "probe.cjs", "product.js", "stdout.log", "stderr.log"):
                    write(child / name, (binding.parent / name).read_bytes())
        record.guard("private_clear_fixture_evidence", retain_private_probes)
        after = record.capture("source_after")
        record.report.update(tests=tested.testsRun if tested else None, expected_tests=len(names),
            expected_checks=len(names), test_names=names,
            failures=len(tested.failures) if tested else None, errors=len(tested.errors) if tested else None,
            skipped=len(tested.skipped) if tested else None,
            completed_checks=tested.testsRun-len(tested.errors)-len(tested.failures)-len(tested.skipped) if tested else 0)
        ok = tested is not None and tested.wasSuccessful() and tested.testsRun == len(names) > 0 and (
            not tested.skipped and before == after and record.report["first_cause"] is None)
        if ok: record.report.update(verdict="PASS", intended_rc=0, completed=True)
        else: record.failure("selftest_gate", "incomplete_or_source_drift")
        return record.publish()
    report = record.report
    report.update(runtime_root=str(runtime), runtime_filesystem=filesystem, production_mutated=False,
                  gpu_used=False, expected_checks=1 if args.sentinel_only else 18)
    review = None
    try:
        review = Review(ROOT, out, runtime, args.browser, record=record)
        review.register()
        if args.sentinel_only:
            review.start()
            review.sentinel_scenario()
            review.stop()
        else:
            for width, height in ((1440, 900), (390, 844)):
                review.start()
                review.scenario(width, height)
                review.stop()
    except Exception:
        record.failure("browser_scenario", traceback.format_exc())
    return finalize_browser(record, review)


if __name__ == "__main__":
    raise SystemExit(main())
