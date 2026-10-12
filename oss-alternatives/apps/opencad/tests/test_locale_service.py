"""Execute the shipped locale service offline; retain exact source/log/child closure.

No HTTP server or browser is started. Node executes the real service in a strict
DOM fixture. CLI evidence is external and immutable; --verify reads it offline.
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
WEB = "oss-alternatives/apps/opencad/web"
BASE = "eb19211ca7ca4b9304678fe747026009234d1515"
OWNED = (
    f"{WEB}/js/locale-service.js", f"{WEB}/panel-i18n.json",
    f"{WEB}/index.html", f"{WEB}/js/app.js",
    "oss-alternatives/apps/opencad/tests/test_locale_service.py",
    "docs/design/locale-service.md",
)
MARKER = "OPENCAD_LOCALE_SERVICE"

NODE_FIXTURE = r"""
"use strict";
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const rootPath = process.argv[2], mode = process.argv[3];
const path = require("node:path");
const source = fs.readFileSync(path.join(rootPath,
  "oss-alternatives/apps/opencad/web/js/locale-service.js"), "utf8");
const shipped = JSON.parse(fs.readFileSync(path.join(rootPath,
  "oss-alternatives/apps/opencad/web/panel-i18n.json"), "utf8"));
const catalog = JSON.parse(fs.readFileSync(path.join(rootPath,
  "oss-alternatives/packages/cadcore/cadcore/data/i18n/languages.json"), "utf8"));
let assertions = 0;
function check(value, message) { assertions++; assert.ok(value, message); }
function equal(a, b) { assertions++; assert.deepEqual(a, b); }
function rejects(fn, code) {
  assertions++;
  assert.throws(fn, error => error.code === code, code);
}
class Bus {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, callback, options) {
    const list = this.listeners.get(name) || [];
    list.push({callback, once: Boolean(options && options.once)});
    this.listeners.set(name, list);
  }
  dispatchEvent(event) {
    const list = [...(this.listeners.get(event.type) || [])];
    for (const item of list) {
      if (item.once) this.listeners.set(event.type,
        this.listeners.get(event.type).filter(other => other !== item));
      item.callback(event);
    }
    return true;
  }
}
class Element extends Bus {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.children = [];
    this.attrs = new Map(); this.parentElement = null;
    this._text = ""; this.value = ""; this.disabled = false;
    this.hidden = false; this.inert = false; this.dataset = {};
  }
  append(node) { this.children.push(node); node.parentElement = this; }
  setAttribute(key, value) { this.attrs.set(key, String(value)); }
  getAttribute(key) { return this.attrs.has(key) ? this.attrs.get(key) : null; }
  hasAttribute(key) { return this.attrs.has(key); }
  get textContent() { return this._text; }
  set textContent(text) {
    check(this.children.length === 0, "text setter must not destroy children");
    this._text = text;
  }
  matches(selector) {
    if (!/^\[data-panel-i18n(?:-(?:aria|placeholder|title|alt))?\]$/.test(selector)) {
      throw new Error("fixture_unknown_selector:" + selector);
    }
    return this.hasAttribute(selector.slice(1, -1));
  }
  querySelectorAll(selector) {
    const result = [];
    function visit(node) {
      node.children.forEach(child => {
        if (child.matches(selector)) result.push(child);
        visit(child);
      });
    }
    visit(this);
    return result;
  }
}
function fixture(payload = shipped, options = {}) {
  const document = new Bus(), window = new Bus(), events = [], requests = [];
  const body = new Element("body"), selectors = [];
  document.body = body; document.readyState = "loading";
  document.currentScript = {src: "http://localhost/web/js/locale-service.js"};
  document.createElement = tag => new Element(tag);
  document.querySelectorAll = selector => {
    if (selector !== "#lang-select option, #locale option") {
      throw new Error("fixture_unknown_document_selector:" + selector);
    }
    return selectors;
  };
  for (const entry of catalog) {
    const item = new Element("option"); item.value = entry.code; selectors.push(item);
  }
  let resolveFetch;
  const fetched = new Promise(resolve => { resolveFetch = resolve; });
  const response = {ok: options.http !== false,
    text: async () => options.text === undefined ? JSON.stringify(payload) : options.text};
  window.fetch = async (url, init) => {
    requests.push({url, init});
    if (options.defer) await fetched;
    if (options.fetchError) throw new Error("fixture_fetch_failure");
    return response;
  };
  window.addEventListener("opencad:locale", event => events.push(event.detail));
  const context = vm.createContext({window, document, URL, CustomEvent: class {
    constructor(type, init) { this.type = type; this.detail = init.detail; }
  }, console});
  vm.runInContext(source, context, {filename: "locale-service.js"});
  const service = window.OpenCADLocale;
  check(Object.isFrozen(service), "frozen public API");
  return {window, document, body, service, events, requests, selectors,
    release: resolveFetch};
}
function strings() { return {"app.title": "Private core", "lang.entry": "{native_name} ({code})"}; }
function update(f, locale) { return f.service.update({locale, strings: strings()}); }
function leaf(root, attribute, key, tag = "span") {
  const element = new Element(tag); element.setAttribute(attribute, key); root.append(element);
  return element;
}
function publicAlert(f, expected, visible = true) {
  const nodes = f.body.children.filter(node => node.tagName === "OUTPUT" &&
    node.getAttribute("role") === "alert");
  equal(nodes.length, 1);
  const node = nodes[0];
  equal(node.parentElement, f.body);
  equal(node.getAttribute("role"), "alert");
  equal(node.getAttribute("lang"), "en");
  equal(node.getAttribute("data-opencad-public-locale-error"), "foundation");
  equal(Array.from(node.attrs.keys()).sort(),
    ["data-opencad-public-locale-error", "lang", "role"]);
  equal(node.children.length, 0);
  equal(node.hidden, !visible);
  if (visible) {
    check(!node.hasAttribute("hidden"), "public alert has hidden attribute");
    equal(node.textContent, expected);
  } else {
    equal(node.textContent, "");
  }
  return node;
}
async function readyFailure(options, expected, publicCode, payload = shipped) {
  const f = fixture(payload, options);
  await assert.rejects(f.service.ready, error => error.code === expected); assertions++;
  equal(f.service.snapshot().status, "error");
  equal(f.service.snapshot().error.code, expected);
  publicAlert(f, publicCode);
  return f;
}
async function main() {
  const f = fixture();
  equal(Object.keys(f.service).sort(), ["bind", "ready", "snapshot", "t", "update"]);
  equal(f.service.snapshot().locale, null);
  equal(f.service.snapshot().bound, false);
  rejects(() => f.service.t("panel.workbench.heading"), "locale_not_ready");
  await f.service.ready;
  equal(f.service.snapshot().locale, "en");
  equal(f.service.snapshot().bound, false);
  equal(f.service.snapshot().available_locales.join(","), catalog.map(x => x.code).sort().join(","));
  equal(f.service.snapshot().supported_locales.join(","), Object.keys(shipped.locales).sort().join(","));
  equal(f.requests.length, 1);
  equal(f.requests[0].url, "http://localhost/web/panel-i18n.json");
  equal(f.requests[0].init.cache, "no-store");
  if (mode === "missing-key") f.service.t("panel.not_present");
  if (mode === "missing-args") f.service.t("panel.session.logout_unconfirmed");
  if (mode === "invalid-args") f.service.t("panel.session.logout_unconfirmed", {detail: {}});
  if (mode === "unaccepted-state") {
    f.window.dispatchEvent({type: "opencad:state", detail: {ok: false, locale: "ja", strings: strings()}});
  }
  if (mode === "invalid-state") {
    f.window.dispatchEvent({type: "opencad:state", detail: {ok: true, locale: "ja", strings: {}}});
  }
  if (mode === "invalid-root") f.service.bind({});
  if (mode === "nonleaf") {
    const root = new Element("label");
    root.setAttribute("data-panel-i18n", "panel.session.account");
    root.append(new Element("input")); f.service.bind(root);
  }
  if (mode === "bad-binding-args") {
    const root = new Element("span");
    root.setAttribute("data-panel-i18n", "panel.session.account");
    root.setAttribute("data-panel-i18n-args", "{"); f.service.bind(root);
  }
  if (mode !== "positive") throw new Error("fixture_unknown_mode:" + mode);
  const root = new Element("section"); f.body.append(root);
  const title = leaf(root, "data-panel-i18n", "panel.workbench.heading");
  const input = leaf(root, "data-panel-i18n-aria", "panel.session.account", "input");
  input.setAttribute("data-panel-i18n-placeholder", "panel.session.account");
  input.setAttribute("data-panel-i18n-title", "panel.session.account");
  input.value = "draft-value"; input.disabled = true; input.hidden = true; input.inert = true;
  const image = leaf(root, "data-panel-i18n-alt", "panel.session.account", "img");
  const named = leaf(root, "data-panel-i18n", "panel.parametric.expression_aria");
  named.setAttribute("data-panel-i18n-args", JSON.stringify({name: "<img src=x onerror=bad>"}));
  f.document.activeElement = input;
  const off = f.service.bind(root);
  equal(title.textContent, shipped.locales.en["panel.workbench.heading"]);
  const eventsBeforeRebind = f.events.length;
  const offRebind = f.service.bind(root); offRebind();
  equal(f.events.length, eventsBeforeRebind);
  for (const code of Object.keys(shipped.locales)) {
    const state = update(f, code);
    equal(state.status, "ready"); equal(state.bound, true);
    equal(title.textContent, shipped.locales[code]["panel.workbench.heading"]);
    for (const attr of ["aria-label", "placeholder", "title"]) {
      equal(input.getAttribute(attr), shipped.locales[code]["panel.session.account"]);
    }
    equal(image.getAttribute("alt"), shipped.locales[code]["panel.session.account"]);
    equal(input.value, "draft-value"); equal(input.disabled, true);
    equal(input.hidden, true); equal(input.inert, true);
    equal(f.document.activeElement, input);
    check(named.textContent.includes("<img src=x onerror=bad>"), "literal safe interpolation");
    equal(named.children.length, 0);
    for (const key of Object.keys(shipped.locales[code])) {
      const args = {};
      for (const match of shipped.locales[code][key].matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)) {
        args[match[1]] = match[1];
      }
      check(typeof f.service.t(key, args) === "string", "actual dictionary key:" + code + ":" + key);
    }
  }
  const previous = title.textContent;
  off(); off(); update(f, "en"); equal(title.textContent, previous);
  f.service.bind(root); equal(title.textContent, shipped.locales.en["panel.workbench.heading"]);
  const off2 = f.service.bind(root); off2(); update(f, "ja");
  equal(title.textContent, shipped.locales.ja["panel.workbench.heading"]);
  const late = new Element("span"); late.setAttribute("data-panel-i18n", "panel.workbench.parametric");
  f.service.bind(late); equal(late.textContent, shipped.locales.ja["panel.workbench.parametric"]);
  const copied = strings();
  f.service.update({locale: "en", strings: copied}); copied["app.title"] = "mutated";
  equal(f.service.t("app.title"), "Private core");
  rejects(() => f.service.t("panel.missing"), "locale_missing_key");
  rejects(() => f.service.t("panel.session.logout_unconfirmed"), "locale_argument_set_mismatch");
  rejects(() => f.service.t("panel.session.account", {extra: 1}), "locale_argument_set_mismatch");
  rejects(() => f.service.t("panel.session.logout_unconfirmed", {detail: NaN}), "locale_invalid_arg_value");
  rejects(() => f.service.t("panel.session.account", []), "locale_invalid_args");
  rejects(() => f.service.update({locale: "", strings: strings()}), "locale_invalid_code");
  rejects(() => f.service.update({locale: "en", strings: {}}), "locale_empty_dictionary");
  rejects(() => f.service.update({locale: "en", strings: {"app.title": 1}}), "locale_empty_translation");
  rejects(() => f.service.update({locale: "en", strings: strings(), doc: "private"}), "locale_invalid_update");
  const bad = new Element("span"); bad.setAttribute("data-panel-i18n", "panel.missing");
  root.append(bad);
  const before = f.service.snapshot().generation, oldText = title.textContent;
  rejects(() => update(f, "ja"), "locale_missing_key");
  equal(f.service.snapshot().generation, before); equal(title.textContent, oldText);
  root.children.pop();
  const other = new Element("option"); other.value = "fr"; f.selectors.push(other);
  update(f, "fr");
  equal(f.service.snapshot().status, "unsupported");
  check(f.service.snapshot().unsupported.includes("fr"), "no allowlist silent fallback");
  rejects(() => f.service.t("panel.session.account"), "locale_unsupported");
  const unsupportedAlert = publicAlert(f, "locale_unsupported");
  f.selectors.splice(f.selectors.indexOf(other), 1);
  update(f, "en");
  equal(f.service.snapshot().status, "ready");
  equal(unsupportedAlert.textContent, "");
  equal(unsupportedAlert.hidden, true);
  const data = {ok: true, locale: "ja", strings: strings(),
    doc: "private-document", project_id: "private-project", user: {name: "private-user"}};
  f.window.dispatchEvent({type: "opencad:state", detail: data});
  equal(f.service.snapshot().locale, "ja");
  for (const event of f.events) {
    equal(Object.keys(event).sort(), ["available_locales", "bound", "error", "generation",
      "locale", "schema", "status", "supported_locales", "unsupported"]);
    check(Object.isFrozen(event), "event frozen");
    check(!JSON.stringify(event).includes("private-"), "event has no private payload");
    check(Object.isFrozen(event.supported_locales), "nested snapshot frozen");
  }
  const deferred = fixture(shipped, {defer: true});
  update(deferred, "ja"); update(deferred, "zh-CN");
  equal(deferred.service.snapshot().unsupported.length, 0);
  equal(deferred.body.children.length, 0);
  deferred.release(); await deferred.service.ready;
  equal(deferred.service.snapshot().locale, "zh-CN");
  equal(deferred.service.snapshot().bound, true);
  const node = new Element("span"); node.setAttribute("data-panel-i18n", "panel.session.account");
  deferred.service.bind(node); equal(node.textContent, shipped.locales["zh-CN"]["panel.session.account"]);
  const app = fs.readFileSync(path.join(rootPath,
    "oss-alternatives/apps/opencad/web/js/app.js"), "utf8");
  const body = app.match(/function notifyLocale\(body\) \{([\s\S]*?)\n  \}/);
  check(Boolean(body), "actual app bridge");
  const bridge = vm.runInContext("(function(body) {" + body[1] + "})",
    vm.createContext({window: f.window}));
  bridge(data); equal(f.service.snapshot().locale, "ja");
  assertions++; assert.throws(() => bridge({...data, ok: false}), /locale_unaccepted_response/);
  check(app.includes("applyState(body);\n    renderAll();\n    notifyLocale(body);"), "accepted state ordering");
  check(app.includes("await loadCatalogs();\n      renderAll();\n      notifyLocale(body);"), "locale POST ordering");
  check(!body[1].includes("dispatchEvent"), "no incomplete shared state event");
  const html = fs.readFileSync(path.join(rootPath, "oss-alternatives/apps/opencad/web/index.html"), "utf8");
  const serviceTag = '<script src="web/js/locale-service.js"></script>';
  equal(html.split(serviceTag).length - 1, 1);
  check(html.indexOf(serviceTag) < html.indexOf('src="web/js/app.js"'),
    "service before app");
  await readyFailure({http: false}, "locale_package_http_error", "locale_package_http_error");
  await readyFailure({fetchError: true}, "locale_package_load_failed", "locale_package_load_failed");
  await readyFailure({text: "{"}, "locale_package_load_failed", "locale_package_load_failed");
  await readyFailure({}, "locale_invalid_package", "locale_package_load_failed", {});
  const missing = structuredClone(shipped); delete missing.locales.ja["panel.session.account"];
  await readyFailure({}, "locale_key_set_mismatch", "locale_package_load_failed", missing);
  const placeholder = structuredClone(shipped);
  placeholder.locales.ja["panel.session.account"] = "{who}";
  await readyFailure({}, "locale_placeholder_set_mismatch", "locale_package_load_failed", placeholder);
  const empty = structuredClone(shipped); empty.locales.en = {};
  await readyFailure({}, "locale_empty_dictionary", "locale_package_load_failed", empty);
  const duplicate = JSON.stringify(shipped).replace('"schema":', '"schema":"wrong","schema":');
  await readyFailure({text: duplicate}, "locale_duplicate_json_key", "locale_package_load_failed");
  const nestedDuplicate = JSON.stringify(shipped).replace('"panel.session.account":',
    '"panel.session.account":"duplicated","panel.session.account":');
  await readyFailure({text: nestedDuplicate}, "locale_duplicate_json_key", "locale_package_load_failed");
  const invalidPlaceholder = structuredClone(shipped);
  invalidPlaceholder.locales.en["panel.session.account"] = "{0}";
  await readyFailure({}, "locale_invalid_placeholder", "locale_package_load_failed", invalidPlaceholder);
  const appended = structuredClone(shipped); appended.locales.fr = {...appended.locales.en};
  const extended = fixture(appended); await extended.service.ready; update(extended, "fr");
  equal(extended.service.snapshot().status, "ready");
  equal(extended.service.t("panel.session.account"), appended.locales.fr["panel.session.account"]);
  console.log(JSON.stringify({verdict: "PASS", mode, assertions}));
}
main().catch(error => {
  console.error(JSON.stringify({verdict: "FAIL", mode, assertions,
    code: error.code || null, error: String(error.stack || error)}));
  process.exitCode = 1;
});
"""

NEGATIVES = {
    "missing-key": "locale_missing_key",
    "missing-args": "locale_argument_set_mismatch",
    "invalid-args": "locale_invalid_arg_value",
    "unaccepted-state": "locale_unaccepted_state",
    "invalid-state": "locale_empty_dictionary",
    "invalid-root": "locale_invalid_root",
    "nonleaf": "locale_text_requires_leaf",
    "bad-binding-args": "locale_invalid_binding_args",
}


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def git(root, *args):
    child = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    require(child.returncode == 0, "git_failed:" + " ".join(args))
    return child.stdout


def source(root, expected=None, development=False):
    require(root.is_dir() and not root.is_symlink(), "invalid_source_root")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    if expected:
        require(head == expected, "source_head_mismatch")
    git(root, "merge-base", "--is-ancestor", BASE, head)
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    if not development:
        require(not status, "source_not_clean")
    else:
        require(all(line[3:] in OWNED for line in status), "development_scope_mismatch")
    if development:
        changed = git(root, "diff", "--name-only", BASE, head).decode().splitlines()
        require(set(changed) <= set(OWNED), "development_commit_scope_mismatch")
    entries = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        mode, blob, stage = meta.decode().split()
        name = os.fsdecode(name)
        require(stage == "0" and mode in ("100644", "100755"), "source_stage_or_mode")
        path = root / name
        require(path.is_file() and not path.is_symlink(), "source_file_type:" + name)
        data = path.read_bytes()
        actual_mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
        committed = git(root, "cat-file", "blob", f"{head}:{name}")
        if not development or name not in OWNED:
            require(data == committed and actual_mode == mode, "source_byte_mode_drift:" + name)
        entries[name] = {"mode": actual_mode, "bytes": len(data), "sha256": digest(data)}
    physical = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if ".git" in dirs:
            require(relative == Path(".") and not (Path(directory) / ".git").is_symlink(),
                    "nested_git_metadata")
            dirs.remove(".git")
        for folder in dirs:
            require(not (Path(directory) / folder).is_symlink(), "source_directory_symlink")
        for name in files:
            path = Path(directory) / name
            if name == ".git":
                require(relative == Path(".") and not path.is_symlink(), "nested_git_metadata")
                continue
            require(not path.is_symlink() and path.is_file(), "source_file_type")
            physical.add(path.relative_to(root).as_posix())
    if development:
        extra = physical - entries.keys()
        require(extra <= set(OWNED), "source_physical_set_mismatch")
        for name in extra:
            path = root / name
            data = path.read_bytes()
            entries[name] = {"mode": "100755" if path.stat().st_mode & stat.S_IXUSR else "100644",
                             "bytes": len(data), "sha256": digest(data)}
    require(physical == entries.keys(), "source_physical_set_mismatch")
    require(all(name in entries for name in OWNED), "owned_source_missing")
    return {"head": head, "status": status, "development": development,
            "files": entries, "sha256": digest(json.dumps(entries, sort_keys=True).encode())}


def strict_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def child(root, script, mode, out):
    command = [shutil.which("node") or "node", str(script), str(root), mode]
    result = subprocess.run(command, capture_output=True, check=False)
    (out / f"{mode}.stdout.log").write_bytes(result.stdout)
    (out / f"{mode}.stderr.log").write_bytes(result.stderr)
    stream = result.stdout if result.returncode == 0 else result.stderr
    payload = json.loads(stream.decode().splitlines()[-1])
    require(payload["mode"] == mode and payload["assertions"] > 0, "child_terminal_missing")
    expected = 0 if mode == "positive" else 1
    require(result.returncode == expected, "child_unexpected_rc:" + mode)
    require(payload["verdict"] == ("PASS" if expected == 0 else "FAIL"), "child_verdict_mismatch")
    if expected:
        require(payload["code"] == NEGATIVES[mode], "child_wrong_negative:" + mode)
    return {"mode": mode, "argv": command, "actual_rc": result.returncode,
            "finished_at": stamp(), "terminal": payload}


class LocaleServiceTests(unittest.TestCase):
    def test_real_service_and_negative_children(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        out = Path(evidence) / "locale-service-unittest"
        out.mkdir(parents=True, exist_ok=False)
        script = out / "locale-fixture.cjs"
        script.write_text(NODE_FIXTURE)
        records = [child(ROOT, script, mode, out) for mode in ("positive", *NEGATIVES)]
        (out / "children.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        self.assertEqual(len(records), 1 + len(NEGATIVES))


def artifacts(out):
    result = {}
    for path in sorted(out.rglob("*")):
        require(not path.is_symlink(), "artifact_symlink")
        if path.is_file() and path != out / "result.json":
            result[path.relative_to(out).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
    return result


def verify(report, root, evidence):
    require(report["verdict"] == "PASS" and report["actual_rc"] == 0 and
            report["first_cause"] is None, "producer_not_pass")
    require(report["source_before"] == report["source_after"], "producer_source_drift")
    require(report["source_root"] == str(root), "source_root_mismatch")
    require(report["source_before"] == source(root, report["source_before"]["head"],
                                             report["source_before"]["development"]),
            "current_source_drift")
    require(report["artifacts"] == artifacts(evidence), "artifact_closure_mismatch")
    require((evidence / "locale-fixture.cjs").read_text() == NODE_FIXTURE, "fixture_source_drift")
    records = report["children"]
    require([item["mode"] for item in records] == ["positive", *NEGATIVES],
            "child_set_mismatch")
    for item in records:
        mode = item["mode"]
        require(item["argv"] == [shutil.which("node") or "node",
                                str(evidence / "locale-fixture.cjs"), str(root), mode],
                "child_argv_mismatch")
        expected = 0 if mode == "positive" else 1
        require(item["actual_rc"] == expected, "child_rc_mismatch")
        log = evidence / f"{mode}.{'stdout' if expected == 0 else 'stderr'}.log"
        payload = json.loads(log.read_text().splitlines()[-1])
        require(payload == item["terminal"] and payload["assertions"] > 0,
                "child_log_terminal_mismatch")
        require(payload["verdict"] == ("PASS" if expected == 0 else "FAIL"),
                "child_verdict_mismatch")
        if expected:
            require(payload["code"] == NEGATIVES[mode], "child_negative_cause_mismatch")
    require(report["positive_checks"] == records[0]["terminal"]["assertions"] and
            report["negative_controls"] == len(NEGATIVES), "count_mismatch")
    expected_marker = (f"{MARKER} verdict=PASS checks={report['positive_checks']} "
                       f"negatives={len(NEGATIVES)} rc=0\n")
    require((evidence / "completion.log").read_text() == expected_marker,
            "completion_marker_mismatch")
    return len(records)


def controls(prior_path, root, out):
    """Retain actual failing reader CLIs; original artifacts are never changed."""
    prior = strict_json(prior_path)
    require(not prior["source_before"]["development"], "controls_require_committed_producer")
    verify(prior, root, prior_path.parent)
    records = []
    mutations = {
        "missing-child": "child_set_mismatch",
        "fake-child-rc": "child_rc_mismatch",
        "fake-count": "count_mismatch",
        "fake-source-sha": "current_source_drift",
        "fake-mode": "current_source_drift",
        "foreign-root": "source_root_mismatch",
        "corrupt-log": "artifact_closure_mismatch",
        "extra-artifact": "artifact_closure_mismatch",
        "missing-artifact": "artifact_closure_mismatch",
        "rehashed-terminal": "child_log_terminal_mismatch",
        "rehashed-completion": "completion_marker_mismatch",
    }
    for name, cause in mutations.items():
        case = out / name
        case.mkdir()
        evidence = case / "evidence"
        shutil.copytree(prior_path.parent, evidence)
        changed = copy.deepcopy(prior)
        if name == "missing-child":
            changed["children"].pop()
        elif name == "fake-child-rc":
            changed["children"][1]["actual_rc"] = 0
        elif name == "fake-count":
            changed["positive_checks"] += 1
        elif name in ("fake-source-sha", "fake-mode"):
            entry = changed["source_before"]
            if name == "fake-source-sha":
                entry["sha256"] = "0" * 64
            else:
                entry["files"][OWNED[0]]["mode"] = "100755"
            changed["source_after"] = copy.deepcopy(entry)
        elif name == "foreign-root":
            changed["source_root"] = "/data/tmp/foreign"
        elif name == "corrupt-log":
            (evidence / "positive.stdout.log").write_text("corrupted\n")
        elif name == "extra-artifact":
            (evidence / "extra.log").write_text("unbound\n")
        elif name == "missing-artifact":
            changed["artifacts"].pop("positive.stdout.log")
        elif name == "rehashed-terminal":
            payload = copy.deepcopy(changed["children"][0]["terminal"])
            payload["assertions"] += 1
            (evidence / "positive.stdout.log").write_text(json.dumps(payload) + "\n")
        elif name == "rehashed-completion":
            (evidence / "completion.log").write_text("PASS\n")
        for item in changed["children"]:
            item["argv"][1] = str(evidence / "locale-fixture.cjs")
        if name in ("rehashed-terminal", "rehashed-completion"):
            changed["artifacts"] = artifacts(evidence)
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        destination = case / "reader"
        command = [sys.executable, "-B", str(root / OWNED[4]), "--verify",
                   str(evidence / "result.json"), "--source-root", str(root),
                   "--expected-head", prior["source_before"]["head"], "--out", str(destination)]
        child_rc = subprocess.run(command, capture_output=True, check=False)
        (case / "reader.stdout.log").write_bytes(child_rc.stdout)
        (case / "reader.stderr.log").write_bytes(child_rc.stderr)
        result = strict_json(destination / "result.json")
        records.append({"name": name, "argv": command, "actual_rc": child_rc.returncode,
                        "first_cause": result["first_cause"], "expected": cause,
                        "result_sha256": digest((destination / "result.json").read_bytes())})
        require(child_rc.returncode == 1 and result["first_cause"] == cause,
                "reader_control_failed:" + name + ":" + str(result["first_cause"]))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--selftest", action="store_true")
    choice.add_argument("--verify", type=Path)
    choice.add_argument("--controls", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-head")
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    root = args.source_root.absolute()
    out = args.out.absolute()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp")) and not out.exists() and
            not out.is_relative_to(root), "fresh_external_out_required")
    out.mkdir(parents=True, exist_ok=False)
    report = {"started_at": stamp(), "source_root": str(root), "actual_rc": 1,
              "verdict": "FAIL", "first_cause": None, "children": [],
              "positive_checks": 0, "negative_controls": 0}
    rc = 1
    try:
        before = source(root, args.expected_head, args.development)
        report["source_before"] = before
        strict_json(root / WEB / "panel-i18n.json")
        if args.controls:
            report["reader_controls"] = controls(args.controls, root, out)
            report["negative_controls"] = len(report["reader_controls"])
        elif args.verify:
            prior = strict_json(args.verify)
            report["input"] = {"path": str(args.verify),
                               "sha256": digest(args.verify.read_bytes())}
            report["verified_children"] = verify(prior, root, args.verify.parent)
            report["positive_checks"] = prior["positive_checks"]
            report["negative_controls"] = prior["negative_controls"]
        else:
            script = out / "locale-fixture.cjs"
            script.write_text(NODE_FIXTURE)
            for mode in ("positive", *NEGATIVES):
                report["children"].append(child(root, script, mode, out))
            report["positive_checks"] = report["children"][0]["terminal"]["assertions"]
            report["negative_controls"] = len(NEGATIVES)
        report["source_after"] = source(root, args.expected_head, args.development)
        require(before == report["source_after"], "source_drift")
        report["verdict"], rc = "PASS", 0
    except Exception as error:
        report["first_cause"] = str(error)
    report["actual_rc"] = rc
    report["finished_at"] = stamp()
    marker = f"{MARKER} verdict={report['verdict']} checks={report['positive_checks']} negatives={report['negative_controls']} rc={rc}"
    (out / "completion.log").write_text(marker + "\n")
    report["artifacts"] = artifacts(out)
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(marker, flush=True)
    if rc:
        print("first_cause=" + str(report["first_cause"]), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
