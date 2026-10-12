"""Real full-JS render locale checks, with external evidence and offline readback.

--selftest runs Node, --verify requires the independently received --parent-rc,
and --controls runs actual failing reader subprocesses on fresh evidence copies.
The real locale service and the chair's unique catalog are read-only dependencies.
No browser, HTTP server, GPU, production state or credentials are used.
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
BASE = "9c4abdac2906111d49f0c39a437ca4cef7abb9b3"
FOUNDATION = "97b80e675f39e23faffcb8c0f6bb8706a8238f0b"
WEB = "oss-alternatives/apps/opencad/web"
OWNED = (f"{WEB}/js/render-panel.js",
         "oss-alternatives/apps/opencad/tests/test_render_panel_locale.py",
         "docs/design/render-panel-locale.md")
DEFAULT_LOCALE = Path("/data/dev/wt-opencad-locale-foundation-20261012")
DEFAULT_CATALOG = Path("/data/dev/wt-opencad-panel-catalog-r2-20261012")
MARKER = "OPENCAD_RENDER_LOCALE"
CASES = ("bindings", "idle", "late-mount", "busy-state", "busy-response",
         "success", "fallback", "opaque-error", "snapshot-error", "request-error",
         "response-error", "budget-error", "magic-error", "read-error",
         "unsupported-recovery", "readiness", "missing-catalog")
NEGATIVES = {"missing-key": "locale_missing_key",
             "missing-args": "locale_argument_set_mismatch",
             "extra-args": "locale_argument_set_mismatch",
             "unsafe-binding": "locale_text_requires_leaf",
             "request-on-locale": "locale_changed_render_state",
             "early-exit": "child_terminal_missing"}

NODE = r"""
"use strict";
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const assert = require("node:assert/strict");
const [rootPath, localePath, catalogPath, mode] = process.argv.slice(2);
const web = "oss-alternatives/apps/opencad/web";
let panelSource = fs.readFileSync(path.join(rootPath, web, "js/render-panel.js"), "utf8");
const localeSource = fs.readFileSync(path.join(localePath, web, "js/locale-service.js"), "utf8");
const pack = JSON.parse(fs.readFileSync(path.join(catalogPath, web, "panel-i18n.json"), "utf8"));
const crypto = require("node:crypto");
let checks = 0;
const cases = [], signals = [];
function eq(a, b, message) { checks++; assert.deepEqual(a, b, message); }
function ok(a, message) { checks++; assert.ok(a, message); }
function fail(code) { const error = new Error(code); error.code = code; throw error; }
class Bus {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback, options) {
    const entries = this.listeners.get(type) || [];
    entries.push({callback, once: Boolean(options && options.once)});
    this.listeners.set(type, entries);
  }
  dispatchEvent(event) {
    const result = [];
    for (const entry of [...(this.listeners.get(event.type) || [])]) {
      if (entry.once) this.listeners.set(event.type,
        this.listeners.get(event.type).filter(other => other !== entry));
      result.push(entry.callback(event));
    }
    return result;
  }
  async emit(type, detail) { await Promise.all(this.dispatchEvent({type, detail})); }
}
class Text {
  constructor(text) { this.nodeType = 3; this.textContent = String(text); this.parentElement = null; }
}
class Element extends Bus {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.nodeType = 1; this.childNodes = [];
    this.attrs = new Map(); this.parentElement = null; this._text = ""; this._value = undefined;
    this.className = ""; this.disabled = false; this.checked = false;
    this.hidden = false; this.inert = false; this.type = "";
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  append(...nodes) {
    for (const node of nodes) {
      ok(node.nodeType === 1 || node.nodeType === 3, "fixture_append_node_required");
      node.parentElement = this; this.childNodes.push(node);
    }
  }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  removeAttribute(name) { this.attrs.delete(name); }
  get src() { return this.getAttribute("src") || ""; }
  set src(value) { this.setAttribute("src", value); }
  get alt() { return this.getAttribute("alt") || ""; }
  set alt(value) { this.setAttribute("alt", value); }
  get value() {
    return this._value === undefined ? (this.tagName === "SELECT" ?
      (this.children[0] ? this.children[0].value : "") : "") : this._value;
  }
  set value(value) { this._value = String(value); }
  get textContent() { return this._text + this.childNodes.map(node => node.textContent).join(""); }
  set textContent(value) {
    if (this.children.length) fail("fixture_text_destroyed_controls");
    this.childNodes = []; this._text = String(value);
  }
  set innerHTML(_) { fail("fixture_innerHTML_forbidden"); }
  matches(selector) {
    if (selector === ".oc-render-panel") return this.className === "oc-render-panel";
    if (/^\[data-panel-i18n(?:-(?:aria|placeholder|title|alt))?\]$/.test(selector) ||
        selector === "[data-opencad-render-panel]") return this.hasAttribute(selector.slice(1, -1));
    fail("fixture_unknown_selector:" + selector);
  }
  querySelectorAll(selector) {
    const nodes = [];
    const visit = node => node.children.forEach(child => {
      if (child.matches(selector)) nodes.push(child);
      visit(child);
    });
    visit(this); return nodes;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function deferred() {
  let release; const promise = new Promise(resolve => { release = resolve; });
  return {promise, release};
}
async function tick() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
if (mode === "request-on-locale") {
  const target = 'if (languageReady && locale.snapshot().status !== "loading") { paintStatus(); }';
  ok(panelSource.split(target).length === 2, "negative_mutation_coordinate_missing");
  panelSource = panelSource.replace(target,
    'if (languageReady && locale.snapshot().status !== "loading") {' +
    ' window.fetch("/api/state", {credentials: "same-origin"}); paintStatus(); }');
}
function setup(options = {}) {
  const document = new Bus(), window = new Bus(), body = new Element("body");
  const root = new Element("main"); root.setAttribute("data-opencad-render-panel", "");
  body.append(root);
  document.body = body; document.readyState = "loading";
  document.currentScript = {src: "http://fixture.local/web/js/locale-service.js"};
  document.createElement = tag => new Element(tag);
  document.createTextNode = text => new Text(text);
  document.querySelector = selector => body.querySelector(selector);
  document.querySelectorAll = selector => {
    if (selector !== "#lang-select option, #locale option") fail("fixture_unknown_document_selector");
    return ["en", "zh-CN", "ja"].map(value => ({value}));
  };
  document.activeElement = root;
  const requests = [], urlOps = [], notices = [];
  const gate = deferred();
  const f = {document, window, body, root, requests, urlOps, notices, gate,
    responseMode: "success", stateGate: null, renderGate: null, reads: 0,
    privateState: Object.freeze({epoch: 7, revision: 23, request: "opaque-request",
      project: "fixture-project", receipt: "fixture-receipt", busy: false})};
  const sha = "ab".repeat(32);
  const headers = () => new Map([
    ["Content-Type", f.responseMode === "response" ? "text/html" : "image/png"],
    ["X-OpenCAD-Revision", "23"], ["X-OpenCAD-Snapshot-SHA256", sha],
    ["X-OpenCAD-Render-Status", f.responseMode === "fallback" ? "CPU_FALLBACK" : "OK"],
    ["X-OpenCAD-Render-Backend", f.responseMode === "fallback" ? "cpu" : "gpu"],
    ["X-OpenCAD-Renderer", "NVIDIA RTX 3080 | SHA abcd | 0.012345 s"],
    ["X-OpenCAD-Fallback-Reason", f.responseMode === "fallback" ?
      "GPU_DISABLED raw <img> 0.008 s" : null]]);
  window.fetch = async (url, init) => {
    if (String(url).endsWith("/panel-i18n.json")) {
      if (options.deferLocale) await gate.promise;
      return {ok: true, text: async () => JSON.stringify(options.pack || pack)};
    }
    requests.push({url, init});
    if (url === "/api/state") {
      if (f.stateGate) await f.stateGate.promise;
      return {ok: f.responseMode !== "snapshot", json: async () =>
        f.responseMode === "snapshot" ? {} : {revision: 23, sha256: sha}};
    }
    if (url !== "/api/render") fail("fixture_unexpected_request");
    if (f.renderGate) await f.renderGate.promise;
    return {ok: !["opaque", "request"].includes(f.responseMode),
      json: async () => f.responseMode === "opaque" ?
        {detail: "original <img onerror=raw> 日本語 SHA abc 0.123 s"} : {},
      headers: {get: key => headers().get(key) || null},
      blob: async () => ({size: f.responseMode === "budget" ? 0 : 9,
        arrayBuffer: async () => new Uint8Array(f.responseMode === "magic" ?
          [0, 80, 78, 71, 13, 10, 26, 10, 0] : [137, 80, 78, 71, 13, 10, 26, 10, 0]).buffer})};
  };
  class Reader {
    readAsDataURL() {
      f.reads++;
      if (f.responseMode === "read") this.onerror();
      else { this.result = "data:image/png;base64,iVBORw0KGgoA"; this.onload(); }
    }
  }
  class FixtureURL extends URL {}
  FixtureURL.createObjectURL = () => { urlOps.push("create"); return "blob:fixture"; };
  FixtureURL.revokeObjectURL = () => { urlOps.push("revoke"); };
  const context = vm.createContext({window, document, URL: FixtureURL, FileReader: Reader,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    console});
  vm.runInContext(localeSource, context, {filename: path.join(localePath, web, "js/locale-service.js")});
  const service = window.OpenCADLocale;
  f.service = service;
  vm.runInContext(panelSource, context, {filename: path.join(rootPath, web, "js/render-panel.js")});
  f.mounted = async () => {
    const mounting = document.emit("DOMContentLoaded");
    if (options.deferLocale) gate.release();
    await mounting; await service.ready; await tick();
    f.panel = root.querySelector(".oc-render-panel");
    ok(Boolean(f.panel), "real_panel_not_mounted");
    f.title = f.panel.children.find(node => node.tagName === "H2");
    const selects = f.panel.children.flatMap(node => node.children).filter(node => node.tagName === "SELECT");
    [f.camera, f.backend] = selects;
    f.fallback = f.panel.children.flatMap(node => node.children).find(node => node.tagName === "INPUT");
    f.button = f.panel.children.find(node => node.tagName === "BUTTON");
    f.status = f.panel.children.find(node => node.getAttribute("role") === "status");
    f.image = f.panel.children.find(node => node.tagName === "IMG");
    ok(f.camera && f.backend && f.fallback && f.button && f.status && f.image, "fixture_controls_present");
    return f;
  };
  f.update = code => service.update({locale: code, strings: {"app.title": "fixture-core"}});
  f.translate = (key, args) => service.t("panel.render." + key, args);
  return f;
}
function state(f) {
  return {camera: f.camera.value, backend: f.backend.value, fallback: f.fallback.checked,
    disabled: [f.camera, f.backend, f.fallback, f.button].map(node => node.disabled),
    image: [f.image.src, f.image.hidden], focus: f.document.activeElement === f.camera,
    requests: JSON.stringify(f.requests), reads: f.reads, urlOps: [...f.urlOps],
    privateState: f.privateState};
}
function switchInvariant(f, code) {
  const before = state(f), controls = [f.camera, f.backend, f.fallback, f.button, f.image];
  f.update(code);
  const after = state(f);
  if (JSON.stringify(before) !== JSON.stringify(after)) fail("locale_changed_render_state");
  eq(state(f), before);
  eq([f.camera, f.backend, f.fallback, f.button, f.image], controls);
  eq(f.title.textContent, f.translate("title"));
  eq(f.image.alt, f.translate("image.alt"));
  eq(f.camera.getAttribute("aria-label"), f.translate("camera"));
  eq(f.backend.getAttribute("aria-label"), f.translate("backend"));
  eq(f.camera.getAttribute("title"), f.translate("camera"));
  eq(f.backend.getAttribute("title"), f.translate("backend"));
  eq(f.fallback.getAttribute("aria-label"), f.translate("fallback"));
  eq(f.fallback.getAttribute("title"), f.translate("fallback"));
  eq(f.button.getAttribute("aria-label"), f.translate("submit"));
  eq(f.button.getAttribute("title"), f.translate("submit"));
}
async function main() {
  if (mode === "early-exit") return;
  const f = await setup().mounted();
  eq(f.title.getAttribute("data-panel-i18n"), "panel.render.title",
    "render_title_not_locale_bound");
  if (mode === "missing-key") f.service.t("panel.render.not_present");
  if (mode === "missing-args") f.service.t("panel.render.status.failed");
  if (mode === "extra-args") f.service.t("panel.render.title", {extra: 1});
  if (mode === "unsafe-binding") {
    f.camera.parentElement.setAttribute("data-panel-i18n", "panel.render.camera");
    f.service.bind(f.panel);
  }
  if (mode === "request-on-locale") {
    switchInvariant(f, "ja");
  }
  if (mode !== "positive") fail("fixture_unknown_mode");
  eq(f.camera.children.map(node => node.value), ["isometric", "front", "top", "right"]);
  eq(f.backend.children.map(node => node.value), ["gpu", "cpu"]);
  eq(f.title.textContent, f.translate("title"));
  ok(f.panel.children.filter(node => node.tagName === "LABEL")
    .every(node => !node.hasAttribute("data-panel-i18n")), "no_nonleaf_binding");
  eq(f.requests.length, 0); cases.push("bindings");
  f.camera.value = "right"; f.backend.value = "cpu"; f.fallback.checked = true;
  f.document.activeElement = f.camera;
  for (const code of ["ja", "zh-CN", "en"]) {
    switchInvariant(f, code);
    eq(f.status.textContent, f.translate("status.idle"));
    eq(f.camera.children.map(node => node.textContent),
      ["isometric", "front", "top", "right"].map(view => f.translate("camera." + view)));
  }
  cases.push("idle");
  const late = new Element("aside"); f.body.append(late);
  await f.window.OpenCADRenderPanel.mount(late); await tick();
  eq(late.querySelector(".oc-render-panel").children[0].textContent, f.translate("title"));
  f.window.OpenCADRenderPanel.mount(late);
  eq(late.children.length, 1); cases.push("late-mount");
  f.stateGate = deferred();
  const running = f.button.emit("click"); await tick();
  eq(f.requests.length, 1);
  eq([f.button, f.camera, f.backend, f.fallback].map(node => node.disabled), [true, true, true, true]);
  switchInvariant(f, "ja"); eq(f.status.textContent, f.translate("status.wait"));
  await f.button.emit("click"); eq(f.requests.length, 1); cases.push("busy-state");
  f.renderGate = deferred(); f.stateGate.release(); await tick();
  eq(f.requests.length, 2);
  const request = JSON.parse(f.requests[1].init.body);
  eq(request, {camera: "right", backend: "cpu", width: 256, height: 256,
    allow_cpu_fallback: true, expected_revision: 23, expected_sha: "ab".repeat(32)});
  eq(f.requests[0].init.credentials, "same-origin");
  eq(f.requests[1].init.credentials, "same-origin");
  eq(f.requests[1].init.headers["X-OpenCAD-Request"], "1");
  switchInvariant(f, "zh-CN"); cases.push("busy-response");
  f.renderGate.release(); await running; f.stateGate = f.renderGate = null;
  const args = {status: "OK", backend: "gpu",
    renderer: "NVIDIA RTX 3080 | SHA abcd | 0.012345 s", revision: 23};
  eq(f.status.textContent, f.translate("status.result", args));
  eq(f.image.hidden, false); ok(f.image.src.startsWith("data:image/png"), "same_png_displayed");
  for (const code of ["en", "ja", "zh-CN"]) {
    switchInvariant(f, code); eq(f.status.textContent, f.translate("status.result", args));
  }
  cases.push("success");
  f.responseMode = "fallback"; await f.button.emit("click");
  for (const code of ["en", "zh-CN", "ja"]) {
    switchInvariant(f, code);
    eq(f.status.textContent, f.translate("status.result", {...args, status: "CPU_FALLBACK", backend: "cpu"}) +
      " | " + f.translate("status.fallback", {reason: "GPU_DISABLED raw <img> 0.008 s"}));
  }
  cases.push("fallback");
  f.responseMode = "opaque"; await f.button.emit("click");
  for (const code of ["en", "ja", "zh-CN"]) {
    switchInvariant(f, code);
    eq(f.status.textContent, f.translate("status.failed",
      {detail: "original <img onerror=raw> 日本語 SHA abc 0.123 s"}));
    eq(f.status.children.length, 0);
  }
  cases.push("opaque-error");
  for (const kind of ["snapshot", "request", "response", "budget", "magic", "read"]) {
    f.responseMode = kind; await f.button.emit("click");
    for (const code of ["en", "zh-CN", "ja"]) {
      switchInvariant(f, code);
      eq(f.status.textContent, f.translate("status.failed", {detail: f.translate("error." + kind)}));
      eq(f.button.disabled, false);
    }
    cases.push(kind + "-error");
  }
  const beforeUnsupported = state(f);
  let unsupported;
  try { f.update("fr"); } catch (error) { unsupported = error; }
  ok(unsupported && unsupported.code === "locale_unsupported", "unsupported_must_throw");
  eq(state(f), beforeUnsupported);
  ok(f.panel.children.some(node => node.getAttribute("role") === "alert" &&
    !node.hidden && node.textContent.includes("locale_unsupported")), "panel_locale_error_visible");
  switchInvariant(f, "ja"); cases.push("unsupported-recovery");
  const pending = setup({loading: true, deferLocale: true});
  pending.service.update({locale: "zh-CN", strings: {"app.title": "fixture-core"}});
  await pending.mounted();
  eq(pending.title.textContent, pending.translate("title"));
  eq(pending.requests.length, 0); cases.push("readiness");
  const incomplete = structuredClone(pack);
  Object.values(incomplete.locales).forEach(dictionary => { delete dictionary["panel.render.title"]; });
  const missing = setup({pack: incomplete});
  let missingError;
  try { await missing.mounted(); } catch (error) { missingError = error; }
  ok(missingError && missingError.code === "locale_missing_key", "missing_catalog_must_reject");
  ok(missing.root.querySelector(".oc-render-panel").children.some(node =>
    node.getAttribute("role") === "alert" && !node.hidden &&
    node.textContent.includes("locale_missing_key")), "missing_catalog_alert_visible");
  eq(missing.requests.length, 0); cases.push("missing-catalog");
  eq(cases, ["bindings", "idle", "late-mount", "busy-state", "busy-response",
    "success", "fallback", "opaque-error", "snapshot-error", "request-error",
    "response-error", "budget-error", "magic-error", "read-error",
    "unsupported-recovery", "readiness", "missing-catalog"]);
  eq(f.urlOps, []);
  signals.push({requests: f.requests.length, png_reads: f.reads,
    locale_generation: f.service.snapshot().generation,
    state_sha256: crypto.createHash("sha256").update(JSON.stringify(state(f))).digest("hex")});
  console.log(JSON.stringify({verdict: "PASS", mode, checks, cases, signals}));
}
main().catch(error => {
  console.error(JSON.stringify({verdict: "FAIL", mode, checks, cases, signals,
    code: error.code || null, error: String(error.stack || error)}));
  process.exitCode = 1;
});
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def git(root, *args):
    process = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    require(process.returncode == 0, "git_failed:" + " ".join(args) +
            ":rc=" + str(process.returncode) + ":" + process.stderr.decode(errors="replace"))
    return process.stdout


def snapshot(root, development=False, allowed=()):
    require(root.is_dir() and not root.is_symlink(), "invalid_source_root")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    require(not status or (development and all(line[3:] in allowed for line in status)),
            "source_not_clean_or_owned:" + str(root))
    tracked = {}
    for raw in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if raw:
            meta, name = raw.split(b"\t", 1)
            mode, _, stage = meta.decode().split()
            require(stage == "0" and mode in ("100644", "100755"), "source_stage_mode")
            tracked[os.fsdecode(name)] = mode
    changed = set(git(root, "diff", "HEAD", "--name-only").decode().splitlines())
    require(changed <= set(allowed) if development else not changed, "source_byte_drift")
    files = {}
    for directory, folders, names in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if ".git" in folders:
            require(relative == Path("."), "nested_git")
            folders.remove(".git")
        require(all(not (Path(directory) / folder).is_symlink() for folder in folders),
                "source_directory_symlink")
        for name in names:
            path = Path(directory) / name
            if name == ".git":
                require(relative == Path(".") and not path.is_symlink(), "nested_git")
                continue
            key = path.relative_to(root).as_posix()
            require(path.is_file() and not path.is_symlink(), "source_file_type:" + key)
            require(key in tracked or (development and key in allowed), "unknown_source:" + key)
            data = path.read_bytes()
            mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            require(key not in tracked or mode == tracked[key], "source_mode_drift:" + key)
            files[key] = {"bytes": len(data), "mode": mode, "sha256": sha(data)}
    require(set(tracked) <= files.keys() and files, "source_set_mismatch")
    return {"root": str(root), "head": head, "status": status,
            "development": development, "files": files,
            "sha256": sha(json.dumps(files, sort_keys=True).encode())}


def sources(root, locale, catalog, development):
    git(root, "merge-base", "--is-ancestor", BASE, "HEAD")
    if development:
        require(set(git(root, "diff", BASE, "HEAD", "--name-only").decode().splitlines()) <= set(OWNED),
                "development_commit_write_scope")
    locale_head = git(locale, "rev-parse", "HEAD").decode().strip()
    git(locale, "merge-base", "--is-ancestor", FOUNDATION, locale_head)
    git(catalog, "merge-base", "--is-ancestor", FOUNDATION, "HEAD")
    original = git(locale, "cat-file", "blob", locale_head + ":" + WEB + "/js/locale-service.js")
    require(original == (locale / WEB / "js/locale-service.js").read_bytes(),
            "foundation_service_drift")
    catalog_owned = (f"{WEB}/panel-i18n.json",
                     "oss-alternatives/apps/opencad/tests/test_panel_catalog.py",
                     "docs/design/panel-catalog.md",
                     "oss-alternatives/apps/opencad/docs/design/panel-catalog.md")
    packages = strict_json(catalog / WEB / "panel-i18n.json")["locales"]
    render = {code: {key: value for key, value in pack.items() if key.startswith("panel.render.")}
              for code, pack in packages.items()}
    require(render and all(len(pack) == 23 for pack in render.values()), "render_namespace_incomplete")
    return {"panel": snapshot(root, development, OWNED),
            "locale": snapshot(locale, development and locale == catalog, catalog_owned),
            "catalog": snapshot(catalog, development, catalog_owned),
            "render_namespace": {"keys_per_locale": {code: len(pack) for code, pack in render.items()},
                                 "sha256": sha(json.dumps(render, sort_keys=True,
                                                         ensure_ascii=False).encode())}}


def strict_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def artifact_map(out):
    entries = {}
    for path in sorted(out.rglob("*")):
        require(not path.is_symlink(), "artifact_symlink")
        if path.is_file() and path != out / "result.json":
            data = path.read_bytes()
            entries[path.relative_to(out).as_posix()] = {"bytes": len(data), "sha256": sha(data)}
    return entries


def syntax_children(root, locale, out, runtime):
    files = sorted((root / WEB / "js").glob("*.js"))
    files.append(locale / WEB / "js/locale-service.js")
    require(files, "empty_js_collection")
    records = []
    for index, path in enumerate(files):
        argv = [shutil.which("node") or "node", "--check", str(path)]
        process = subprocess.run(argv, cwd=runtime, capture_output=True, check=False)
        label = "syntax-" + str(index)
        (out / (label + ".stdout.log")).write_bytes(process.stdout)
        (out / (label + ".stderr.log")).write_bytes(process.stderr)
        records.append({"label": label, "argv": argv, "cwd": str(runtime),
                        "actual_rc": process.returncode, "at": now()})
        require(process.returncode == 0, "js_syntax_failed:" + str(path))
    return records


def keymap(root, catalog):
    rows = []
    for line in (root / OWNED[2]).read_text().splitlines():
        if not line.startswith("| ") or line.startswith("| Key Suffix") or line.startswith("| ---"):
            continue
        fields = re.split(r"(?<!\\)\|", line)[1:-1]
        require(len(fields) == 4, "keymap_four_columns_required")
        rows.append([field.strip().replace(r"\|", "|") for field in fields])
    require(len(rows) == 23 and len({row[0] for row in rows}) == len(rows),
            "keymap_count_or_duplicates")
    packages = strict_json(catalog / WEB / "panel-i18n.json")["locales"]
    expected = {"status.result": {"status", "backend", "renderer", "revision"},
                "status.fallback": {"reason"}, "status.failed": {"detail"}}
    for suffix, zh, en, ja in rows:
        for code, text in (("zh-CN", zh), ("en", en), ("ja", ja)):
            key = "panel.render." + suffix
            require(packages[code].get(key) == text, "catalog_keymap_missing_or_mismatch:" + code + ":" + key)
            require(set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", text)) ==
                    expected.get(suffix, set()), "keymap_placeholder_mismatch:" + key)
    return {"keys": len(rows), "translations": len(rows) * 3, "args": {
        key: sorted(value) for key, value in expected.items()}}


def run_child(root, locale, catalog, script, mode, out, runtime):
    argv = [shutil.which("node") or "node", str(script), str(root), str(locale), str(catalog), mode]
    env = dict(os.environ, TMPDIR=str(runtime), PYTHONDONTWRITEBYTECODE="1")
    started = now()
    result = subprocess.run(argv, cwd=runtime, env=env, capture_output=True, check=False)
    (out / (mode + ".stdout.log")).write_bytes(result.stdout)
    (out / (mode + ".stderr.log")).write_bytes(result.stderr)
    lines = (result.stdout if result.returncode == 0 else result.stderr).decode().splitlines()
    terminal = json.loads(lines[-1]) if lines else None
    return {"mode": mode, "argv": argv, "cwd": str(runtime), "started_at": started,
            "finished_at": now(), "actual_rc": result.returncode, "terminal": terminal}


def validate_child(record):
    mode, terminal = record["mode"], record["terminal"]
    if mode == "early-exit":
        require(record["actual_rc"] == 0 and terminal is None, "early_exit_control_changed")
        try:
            require(terminal and terminal["checks"] > 0, "child_terminal_missing")
        except AssertionError as error:
            require(str(error) == NEGATIVES[mode], "early_exit_wrong_cause")
        else:
            raise AssertionError("early_exit_was_accepted")
        return
    require(terminal and terminal["mode"] == mode and terminal["checks"] > 0,
            "child_terminal_missing")
    expected = 0 if mode == "positive" else 1
    require(record["actual_rc"] == expected, "child_unexpected_rc:" + mode +
            ":first_cause=" + str(terminal.get("code") or terminal.get("error")))
    require(terminal["verdict"] == ("PASS" if expected == 0 else "FAIL"),
            "child_verdict_mismatch")
    if expected:
        require(terminal["code"] == NEGATIVES[mode], "child_negative_cause:" + mode)
    else:
        require(terminal["cases"] == list(CASES) and terminal["checks"] >= 200,
                "child_case_count_mismatch")
        require(len(terminal["signals"]) == 1 and terminal["signals"][0]["requests"] == 17 and
                terminal["signals"][0]["png_reads"] == 3, "child_signal_mismatch")
        require(re.fullmatch(r"[0-9a-f]{64}", terminal["signals"][0]["state_sha256"]) and
                terminal["signals"][0]["locale_generation"] > 20, "child_state_binding_missing")


def readback(prior, root, locale, catalog, evidence, parent_rc):
    require(parent_rc == 0, "original_parent_rc_not_zero")
    require(prior["kind"] == "selftest" and prior["verdict"] == "PASS" and
            prior["actual_rc"] == 0 and prior["first_cause"] is None, "producer_not_pass")
    require(prior["started_at"] <= prior["finished_at"], "producer_time_mismatch")
    require(prior["source_before"] == prior["source_after"], "producer_source_drift")
    require(prior["source_before"] == sources(root, locale, catalog, prior["development"]),
            "current_source_drift")
    require(prior["artifacts"] == artifact_map(evidence), "artifact_closure_mismatch")
    require((evidence / "render-fixture.cjs").read_text() == NODE, "fixture_source_drift")
    expected_js = sorted((root / WEB / "js").glob("*.js")) + [locale / WEB / "js/locale-service.js"]
    require(len(prior["syntax_children"]) == len(expected_js), "syntax_child_set_mismatch")
    for index, (record, path) in enumerate(zip(prior["syntax_children"], expected_js)):
        require(record["label"] == "syntax-" + str(index) and record["actual_rc"] == 0 and
                record["argv"] == [shutil.which("node") or "node", "--check", str(path)],
                "syntax_child_mismatch")
        require((evidence / (record["label"] + ".stdout.log")).read_bytes() == b"" and
                (evidence / (record["label"] + ".stderr.log")).read_bytes() == b"",
                "syntax_log_mismatch")
    require(prior["keymap"] == keymap(root, catalog), "keymap_readback_mismatch")
    require([record["mode"] for record in prior["children"]] == ["positive", *NEGATIVES],
            "child_set_mismatch")
    for record in prior["children"]:
        mode = record["mode"]
        require(record["argv"] == [shutil.which("node") or "node",
                                  str(evidence / "render-fixture.cjs"), str(root),
                                  str(locale), str(catalog), mode], "child_argv_mismatch")
        require(Path(record["cwd"]).is_relative_to(Path("/data/tmp")), "child_cwd_mismatch")
        require(prior["started_at"] <= record["started_at"] <= record["finished_at"] <=
                prior["finished_at"], "child_time_mismatch")
        log = evidence / (mode + (".stdout.log" if record["actual_rc"] == 0 else ".stderr.log"))
        lines = log.read_text().splitlines()
        terminal = json.loads(lines[-1]) if lines else None
        require(terminal == record["terminal"], "child_log_terminal_mismatch")
        validate_child(record)
    require(prior["checks"] == prior["children"][0]["terminal"]["checks"] and
            prior["cases"] == len(CASES) and prior["negative_controls"] == len(NEGATIVES) and
            prior["skips"] == 0, "count_mismatch")
    marker = (f"{MARKER} verdict=PASS cases={len(CASES)} checks={prior['checks']} "
              f"negatives={len(NEGATIVES)} skips=0 rc=0")
    require((evidence / "completion.log").read_text() == marker + "\n",
            "completion_marker_mismatch")
    return len(prior["children"])


def reader_controls(prior_path, root, locale, catalog, out, runtime, parent_rc):
    prior = strict_json(prior_path)
    readback(prior, root, locale, catalog, prior_path.parent, parent_rc)
    controls = {"missing-child": "child_set_mismatch",
                "fake-rc": "child_unexpected_rc:missing-key:first_cause=locale_missing_key",
                "fake-count": "count_mismatch",
                "source-sha": "current_source_drift",
                "log-corrupt": "artifact_closure_mismatch",
                "extra-artifact": "artifact_closure_mismatch",
                "rehash-terminal": "child_log_terminal_mismatch",
                "rehash-marker": "completion_marker_mismatch",
                "parent-rc": "original_parent_rc_not_zero"}
    records = []
    for name, expected in controls.items():
        case = out / name
        case.mkdir()
        evidence = case / "evidence"
        shutil.copytree(prior_path.parent, evidence)
        changed = copy.deepcopy(prior)
        if name == "missing-child":
            changed["children"].pop()
        elif name == "fake-rc":
            changed["children"][1]["actual_rc"] = 0
            (evidence / "missing-key.stdout.log").write_text(
                json.dumps(changed["children"][1]["terminal"]) + "\n")
            changed["artifacts"] = artifact_map(evidence)
        elif name == "fake-count":
            changed["checks"] += 1
        elif name == "source-sha":
            changed["source_before"]["panel"]["sha256"] = "0" * 64
            changed["source_after"] = copy.deepcopy(changed["source_before"])
        elif name == "log-corrupt":
            (evidence / "positive.stdout.log").write_text("corrupted\n")
        elif name == "extra-artifact":
            (evidence / "extra.log").write_text("not bound\n")
        elif name == "rehash-terminal":
            terminal = copy.deepcopy(changed["children"][0]["terminal"])
            terminal["checks"] += 1
            (evidence / "positive.stdout.log").write_text(json.dumps(terminal) + "\n")
            changed["artifacts"] = artifact_map(evidence)
        elif name == "rehash-marker":
            (evidence / "completion.log").write_text("PASS\n")
            changed["artifacts"] = artifact_map(evidence)
        for record in changed["children"]:
            record["argv"][1] = str(evidence / "render-fixture.cjs")
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        argv = [sys.executable, "-B", str(root / OWNED[1]), "--verify",
                str(evidence / "result.json"), "--parent-rc", "1" if name == "parent-rc" else "0",
                "--source-root", str(root), "--locale-root", str(locale),
                "--catalog-root", str(catalog), "--out", str(case / "reader"),
                "--runtime", str(runtime / name)]
        if prior["development"]:
            argv.append("--development")
        process = subprocess.run(argv, capture_output=True, check=False)
        (case / "reader.stdout.log").write_bytes(process.stdout)
        (case / "reader.stderr.log").write_bytes(process.stderr)
        report = strict_json(case / "reader/result.json")
        record = {"name": name, "actual_rc": process.returncode, "argv": argv,
                  "first_cause": report["first_cause"], "expected": expected, "at": now()}
        records.append(record)
        require(process.returncode == 1 and record["first_cause"] == expected,
                "reader_control_failed:" + name + ":" + str(record["first_cause"]))
    return records


class RenderLocaleTests(unittest.TestCase):
    def test_real_panel_locale(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        runtime = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
        require(evidence and runtime, "external_evidence_and_runtime_required")
        out = Path(evidence) / "render-locale-unittest"
        out.mkdir(parents=True, exist_ok=False)
        work = Path(runtime) / "render-locale-unittest"
        work.mkdir(parents=True, exist_ok=False)
        script = out / "render-fixture.cjs"
        script.write_text(NODE)
        records = []
        locale = Path(os.environ.get("OPENCAD_RENDER_LOCALE_ROOT", str(DEFAULT_LOCALE)))
        catalog = Path(os.environ.get("OPENCAD_RENDER_CATALOG_ROOT", str(DEFAULT_CATALOG)))
        for mode in ("positive", *NEGATIVES):
            record = run_child(ROOT, locale, catalog, script, mode, out, work)
            records.append(record)
            (out / "children.json").write_text(json.dumps(records, indent=2) + "\n")
            validate_child(record)
        self.assertEqual(len(records), 1 + len(NEGATIVES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--selftest", action="store_true")
    action.add_argument("--verify", type=Path)
    action.add_argument("--controls", type=Path)
    parser.add_argument("--parent-rc", type=int)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--foundation-root", type=Path,
                        help="real foundation service and unique chair catalog in one tree")
    parser.add_argument("--locale-root", type=Path, default=DEFAULT_LOCALE)
    parser.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    root = args.source_root.absolute()
    locale = (args.foundation_root or args.locale_root).absolute()
    catalog = (args.foundation_root or args.catalog_root).absolute()
    out, runtime = args.out.absolute(), args.runtime.absolute()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp")) and not out.exists(),
            "fresh_external_evidence_required")
    require(runtime.is_relative_to(Path("/data/tmp")) and not runtime.exists(),
            "fresh_local_runtime_required")
    out.mkdir(parents=True, exist_ok=False)
    runtime.mkdir(parents=True, exist_ok=False)
    report = {"kind": "controls" if args.controls else "verify" if args.verify else "selftest",
              "started_at": now(), "development": args.development, "verdict": "FAIL",
              "actual_rc": 1, "first_cause": None, "children": [], "checks": 0,
              "cases": 0, "negative_controls": 0, "skips": 0,
              "gpu_measurement": "NOT_ASSESSED", "browser": "NOT_ASSESSED",
              "production": "NOT_ASSESSED"}
    try:
        before = sources(root, locale, catalog, args.development)
        report["source_before"] = before
        if args.verify or args.controls:
            input_path = args.verify or args.controls
            report["input"] = {"path": str(input_path), "sha256": sha(input_path.read_bytes()),
                               "original_parent_rc": args.parent_rc}
            if args.controls:
                report["reader_controls"] = reader_controls(input_path, root, locale, catalog,
                                                           out, runtime, args.parent_rc)
                report["negative_controls"] = len(report["reader_controls"])
            else:
                prior = strict_json(input_path)
                report["verified_children"] = readback(prior, root, locale, catalog,
                                                      input_path.parent, args.parent_rc)
                report["checks"], report["cases"] = prior["checks"], prior["cases"]
                report["negative_controls"] = prior["negative_controls"]
        else:
            report["syntax_children"] = syntax_children(root, locale, out, runtime)
            compile((root / OWNED[1]).read_text(), str(root / OWNED[1]), "exec")
            script = out / "render-fixture.cjs"
            script.write_text(NODE)
            for mode in ("positive", *NEGATIVES):
                record = run_child(root, locale, catalog, script, mode, out, runtime)
                report["children"].append(record)
                validate_child(record)
            report["checks"] = report["children"][0]["terminal"]["checks"]
            report["cases"], report["negative_controls"] = len(CASES), len(NEGATIVES)
            report["keymap"] = keymap(root, catalog)
        report["source_after"] = sources(root, locale, catalog, args.development)
        require(before == report["source_after"], "source_drift")
        report["verdict"], report["actual_rc"] = "PASS", 0
    except Exception as error:
        report["first_cause"] = str(error)
    report["finished_at"] = now()
    marker = (f"{MARKER} verdict={report['verdict']} cases={report['cases']} checks={report['checks']} "
              f"negatives={report['negative_controls']} skips=0 rc={report['actual_rc']}")
    (out / "completion.log").write_text(marker + "\n")
    report["artifacts"] = artifact_map(out)
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(marker, flush=True)
    if "source_after" in report:
        print("RENDER_NAMESPACE at=" + report["finished_at"] +
              " before=" + report["source_before"]["render_namespace"]["sha256"] +
              " after=" + report["source_after"]["render_namespace"]["sha256"], flush=True)
    if report["first_cause"]:
        print("first_cause=" + report["first_cause"], flush=True)
    return report["actual_rc"]


if __name__ == "__main__":
    sys.exit(main())
