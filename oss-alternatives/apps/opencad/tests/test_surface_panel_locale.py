"""Real source Node/strict DOM surface locale checks and external evidence reader.

No browser, HTTP service, credentials or production is used. The actual public
foundation and chair dictionary are read-only dependencies. All executable
fixtures, child logs and receipts live in fresh external runtime/data/tmp roots.
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
TEST = "oss-alternatives/apps/opencad/tests/test_surface_panel_locale.py"
BASE = "9c4abdac2906111d49f0c39a437ca4cef7abb9b3"
FOUNDATION = Path("/data/dev/wt-opencad-locale-foundation-20261012")
OWNED = (
    f"{WEB}/js/surfaces-panel.js", f"{WEB}/js/industrial-panels.js",
    TEST, "docs/design/surface-panel-locale.md",
)
MARKER = "OPENCAD_SURFACE_PANEL_LOCALE"
NEGATIVES = {
    "missing-service": "surface_locale_required",
    "loading-service": "locale_not_ready",
    "missing-mount-key": "locale_missing_key",
    "missing-key": "locale_missing_key",
    "missing-args": "locale_argument_set_mismatch",
    "nonleaf": "locale_text_requires_leaf",
    "unknown-selector": "fixture_unknown_selector",
    "lost-focus": "locale_control_state_changed",
    "refetch": "locale_transport_changed",
    "redraw": "locale_draw_changed",
    "remount": "locale_node_set_changed",
}

NODE = r"""
"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const assert = require("node:assert/strict"), crypto = require("node:crypto");
const [sourceRoot, foundationRoot, catalogPath, mode] = process.argv.slice(2);
const web = "oss-alternatives/apps/opencad/web";
let source = fs.readFileSync(path.join(sourceRoot, web, "js/surfaces-panel.js"), "utf8");
const industrial = fs.readFileSync(path.join(sourceRoot, web, "js/industrial-panels.js"), "utf8");
const serviceSource = fs.readFileSync(path.join(foundationRoot, web, "js/locale-service.js"), "utf8");
const dictionary = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
let checks = 0;
function check(value, code) {
  checks++;
  if (!value) { const error = new Error(code); error.code = code; throw error; }
}
function equal(actual, expected, code) {
  checks++;
  try { assert.deepEqual(actual, expected); }
  catch (error) { error.code = code; error.message = code + ": " + error.message; throw error; }
}
class Bus {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback, options) {
    const list = this.listeners.get(type) || [];
    list.push({callback, once: Boolean(options && options.once)});
    this.listeners.set(type, list);
  }
  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x.callback !== callback));
  }
  dispatchEvent(event) {
    for (const item of [...(this.listeners.get(event.type) || [])]) {
      if (item.once) this.removeEventListener(event.type, item.callback);
      item.callback(event);
    }
    return true;
  }
}
let document, canvasCalls = 0, draws = 0, created = 0;
class Element extends Bus {
  constructor(tag) {
    super(); created++; this.nodeType = 1; this.tagName = tag.toUpperCase();
    this.children = []; this.parentElement = null; this.attrs = new Map();
    this.dataset = {}; this._text = ""; this.value = ""; this.checked = false;
    this.hidden = false; this.disabled = false; this.inert = false; this.open = false;
    this.className = ""; this.selectionStart = 0; this.selectionEnd = 0;
    this.selectionDirection = "none"; this.clientWidth = 640; this.clientHeight = 360;
  }
  appendChild(node) { this.children.push(node); node.parentElement = this; return node; }
  append(node) { return this.appendChild(node); }
  remove() {
    if (this.parentElement) {
      const parent = this.parentElement;
      parent.children = parent.children.filter(x => x !== this);
      this.parentElement = null;
    }
  }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  get textContent() {
    return this._text + this.children.map(child => child.textContent).join("");
  }
  set textContent(value) {
    check(this.children.length === 0, "fixture_destructive_text_write");
    this._text = String(value);
  }
  focus() { document.activeElement = this; }
  matches(selector) {
    if (/^\[data-panel-i18n(?:-(?:aria|title|placeholder|alt))?\]$/.test(selector)) {
      return this.hasAttribute(selector.slice(1, -1));
    }
    if (/^\[data-surface-(?:field|action)="[a-z-]+"\]$/.test(selector)) {
      const match = selector.match(/^\[data-surface-(field|action)="([a-z-]+)"\]$/);
      return this.dataset[match[1] === "field" ? "surfaceField" : "surfaceAction"] === match[2];
    }
    if (["button", "details", "div", "output", "canvas", "pre", "h2", "label", "span", "input", "textarea"].includes(selector)) {
      return this.tagName === selector.toUpperCase();
    }
    if ([".surfaces-panel", ".surfaces-status", ".surfaces-recipe",
         ".surfaces-metrics", ".surfaces-preview"].includes(selector)) {
      return this.className.split(" ").includes(selector.slice(1));
    }
    const error = new Error("fixture_unknown_selector:" + selector);
    error.code = "fixture_unknown_selector"; throw error;
  }
  querySelectorAll(selector) {
    // Validate even on empty roots; unknown selectors must never become [].
    this.matches(selector);
    const result = [];
    function visit(node) {
      for (const child of node.children) {
        if (child.matches(selector)) result.push(child);
        visit(child);
      }
    }
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  click() {
    if (this.disabled) return;
    if (this.tagName === "A") downloads.push(this.download);
    this.dispatchEvent({type: "click"});
  }
  getContext(type) {
    equal(type, "webgl", "fixture_canvas_type"); canvasCalls++;
    return gl;
  }
}
const gl = {
  COMPILE_STATUS: 1, LINK_STATUS: 2, DEPTH_TEST: 3, ARRAY_BUFFER: 4, STATIC_DRAW: 5,
  FLOAT: 6, TRIANGLES: 7, COLOR_BUFFER_BIT: 8, DEPTH_BUFFER_BIT: 16, NO_ERROR: 0,
  VERTEX_SHADER: 17, FRAGMENT_SHADER: 18,
  createShader() { return {}; }, shaderSource() {}, compileShader() {},
  getShaderParameter() { return true; }, attachShader() {}, createProgram() { return {}; },
  linkProgram() {}, getProgramParameter() { return true; }, createBuffer() { return {}; },
  enable() {}, viewport() {}, useProgram() {}, clearColor() {}, clear() {},
  bindBuffer() {}, bufferData() {}, getAttribLocation() { return 1; },
  enableVertexAttribArray() {}, vertexAttribPointer() {}, uniform2f() {},
  getUniformLocation() { return {}; }, drawArrays() { draws++; },
  getError() { return 0; }, deleteBuffer() {}, deleteProgram() {}
};
const downloads = [], requests = [], timers = [], observers = [], cryptoCalls = [], core = {"app.title": "CAD"};
let nextError = null, saved = null, delayed = null;
const responseMesh = {
  mesh: {properties: {bbox_mm: [0, 0, 0, 10, 10, 0]},
    vertices_mm: [[0, 0, 0], [10, 0, 0], [0, 10, 0]], triangles: [[0, 1, 2]]},
  properties: {area_mm2: 50, volume_mm3: 0}, seams: []
};
const bytes = Buffer.from("offline exact export bytes"), sha = crypto.createHash("sha256").update(bytes).digest("hex");
const window = new Bus();
document = new Bus(); document.body = new Element("body"); document.readyState = "complete";
document.activeElement = null;
document.currentScript = {src: "http://localhost/web/js/locale-service.js"};
document.createElement = tag => new Element(tag);
const localeOptions = ["zh-CN", "en", "ja"].map(code => ({value: code}));
document.querySelectorAll = selector => {
  equal(selector, "#lang-select option, #locale option", "fixture_document_selector");
  return localeOptions;
};
let workbench = null;
document.querySelector = selector => {
  equal(selector, ".industrial-workbench", "fixture_document_selector");
  return workbench;
};
window.fetch = async (url, init) => {
  requests.push({url, init});
  if (url === "http://localhost/web/panel-i18n.json") {
    return {ok: true, text: async () => JSON.stringify(dictionary)};
  }
  if (nextError) { const error = nextError; nextError = null; throw error; }
  if (url === "/api/surfaces") {
    if (delayed) await delayed;
    if (init && init.body) saved = JSON.parse(init.body).recipe;
    const body = saved ? {ok: true, surface: {recipe: saved}, result: responseMesh} : {ok: true, empty: true};
    return {ok: true, json: async () => body};
  }
  if (["/api/surfaces/step", "/api/surfaces/brep"].includes(url)) {
    const fmt = url.split("/").pop();
    return {ok: true, blob: async () => new Blob([bytes]), headers: new Map([
      ["X-OpenCAD-Artifact-SHA256", sha], ["X-OpenCAD-Document-SHA256", "a".repeat(64)],
      ["Content-Disposition", 'attachment; filename="surface.' + fmt + '"']
    ])};
  }
  throw new Error("fixture_unexpected_fetch:" + url);
};
class Observer {
  constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
  observe(node) { this.node = node; }
  disconnect() { this.disconnected = true; }
}
const URLClass = class extends URL {
  static createObjectURL() { return "blob:offline"; }
  static revokeObjectURL(url) { equal(url, "blob:offline", "fixture_blob_url"); }
};
const context = vm.createContext({window, document, URL: URLClass, Blob,
  crypto: {subtle: {digest(...args) {
    const pending = crypto.webcrypto.subtle.digest(...args);
    cryptoCalls.push(pending); return pending;
  }}}, ResizeObserver: Observer, Float32Array, Uint8Array,
  setTimeout(callback) { timers.push(callback); }, console, CustomEvent: class {
    constructor(type, init) { this.type = type; this.detail = init.detail; }
  }});
const prefix = "panel.surfaces.";
function translate(key, args = {}) {
  const template = dictionary.locales[window.OpenCADLocale.snapshot().locale][prefix + key];
  check(typeof template === "string", "catalog_surface_key_missing:" + key);
  return template.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (_, name) => String(args[name]));
}
function node(root, selector) {
  const selected = root.querySelectorAll(selector);
  equal(selected.length, 1, "surface_selector_cardinality:" + selector); return selected[0];
}
function fields(root) {
  return ["recipe", "bounds", "thickness", "inward"].map(name => node(root, '[data-surface-field="' + name + '"]'));
}
function controlState(items) {
  return items.map(x => [x.value, x.checked, x.disabled, x.hidden, x.inert,
    x.selectionStart, x.selectionEnd, x.selectionDirection]);
}
function nodes(root) { return [root, ...root.children.flatMap(child => nodes(child))]; }
function update(locale) { window.OpenCADLocale.update({locale, strings: core}); }
async function settle() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function annotated(root) {
  const captions = root.querySelectorAll("[data-panel-i18n]");
  equal(captions.length, 15, "surface_caption_count");
  for (const item of captions) {
    const key = item.getAttribute("data-panel-i18n");
    equal(item.children.length, 0, "surface_caption_leaf");
    equal(item.textContent, translate(key.slice(prefix.length)), "surface_caption_translation");
  }
  const aria = root.querySelectorAll("[data-panel-i18n-aria]");
  equal(aria.length, 16, "surface_aria_count");
  for (const item of aria) {
    equal(item.getAttribute("aria-label"),
      translate(item.getAttribute("data-panel-i18n-aria").slice(prefix.length)), "surface_aria_translation");
  }
  equal(root.querySelectorAll("[data-panel-i18n-placeholder]").length, 0, "no_recipe_placeholder");
  equal(root.querySelectorAll("button").length, 9, "surface_button_count");
}
function mutate(anchor, replacement) {
  equal(source.split(anchor).length, 2, "mutation_anchor_count");
  source = source.replace(anchor, replacement);
}
async function main() {
  check(source.includes('data-panel-i18n'), "surface_locale_bindings_missing");
  if (mode === "lost-focus") mutate("function paintNotice() {",
    "function paintNotice() { document.activeElement = null;");
  if (mode === "refetch") mutate("function paintNotice() {",
    'function paintNotice() { window.fetch("/api/surfaces");');
  if (mode === "redraw") mutate("function paintNotice() {",
    "function paintNotice() { draw();");
  if (mode === "remount") mutate("function paintNotice() {",
    'function paintNotice() { root.appendChild(document.createElement("span"));');
  vm.runInContext(serviceSource, context, {filename: "locale-service.js"});
  await window.OpenCADLocale.ready;
  update("en");
  vm.runInContext(source, context, {filename: "surfaces-panel.js"});
  const host = new Element("div"); document.body.append(host);
  if (["missing-service", "loading-service", "missing-mount-key"].includes(mode)) {
    const service = window.OpenCADLocale;
    if (mode === "missing-service") window.OpenCADLocale = null;
    if (mode === "loading-service") window.OpenCADLocale = {
      ...service, snapshot: () => ({status: "loading"})
    };
    if (mode === "missing-mount-key") window.OpenCADLocale = {
      ...service, t(key, args) {
        if (key === prefix + "error_export") {
          const error = new Error("locale_missing_key"); error.code = error.message; throw error;
        }
        return service.t(key, args);
      }
    };
    let failed;
    try { window.OpenCADSurfaces.mount(host); }
    catch (error) { failed = error; }
    check(Boolean(failed), "mount_negative_missing");
    equal(host.children.length, 0, "mount_failure_no_partial_dom");
    equal(observers.length, 0, "mount_failure_no_observer");
    equal(requests.length, 1, "mount_failure_no_fetch");
    throw failed;
  }
  const panel = window.OpenCADSurfaces.mount(host);
  const root = panel.root;
  annotated(root);
  const items = fields(root), [editor, bounds, thickness, inward] = items;
  equal(editor.tagName, "TEXTAREA", "actual_recipe_control");
  check(editor.value.includes('"poles_nm"'), "raw_recipe");
  bounds.value = "[0.11,0.88,0.22,0.77]"; thickness.value = "2.125"; inward.checked = true;
  editor.value += "\n "; editor.selectionStart = 3; editor.selectionEnd = 8;
  editor.selectionDirection = "backward"; editor.focus();
  const initial = controlState(items), initialNodes = nodes(host);
  if (mode === "unknown-selector") root.querySelectorAll(".not-registered");
  if (mode === "missing-key") {
    editor.setAttribute("data-panel-i18n-aria", "panel.surfaces.not_present");
    update("ja");
  }
  if (mode === "nonleaf") {
    root.querySelectorAll("label")[0].setAttribute("data-panel-i18n", prefix + "recipe");
    update("ja");
  }
  if (mode === "missing-args") window.OpenCADLocale.t(prefix + "error");
  const beforeRequest = requests.length, beforeDraws = draws, beforeCanvas = canvasCalls;
  for (const locale of ["zh-CN", "ja", "en", "ja"]) {
    update(locale); annotated(root);
    equal(controlState(items), initial, "locale_control_state_changed");
    equal(document.activeElement, editor, "locale_control_state_changed");
    equal(requests.length, beforeRequest, "locale_transport_changed");
    equal(draws, beforeDraws, "locale_draw_changed");
    equal(canvasCalls, beforeCanvas, "locale_draw_changed");
    equal(nodes(host), initialNodes, "locale_node_set_changed");
  }
  await panel.reload();
  equal(node(root, "output").textContent, translate("empty"), "empty_notice");
  update("zh-CN");
  equal(node(root, "output").textContent, translate("empty"), "empty_notice_switch");
  const original = JSON.parse(editor.value);
  node(root, '[data-surface-action="trim"]').click(); await settle();
  const trimmed = JSON.parse(editor.value);
  equal(trimmed.bounds, JSON.parse(bounds.value), "raw_uv_preserved");
  equal(trimmed.surface, original, "raw_trim_recipe_preserved");
  editor.value = JSON.stringify(original);
  node(root, '[data-surface-action="thicken"]').click(); await settle();
  equal(JSON.parse(editor.value).thickness_nm, 2125000, "raw_thickness_nm");
  equal(JSON.parse(editor.value).inward, true, "raw_inward");
  await panel.apply();
  equal(panel.model.triangles, 1, "actual_mesh_path");
  check(draws > 0, "actual_draw_path");
  equal(node(root, "output").textContent, translate("ready"), "ready_notice");
  const metrics = node(root, "pre").textContent, response = panel.model.response;
  check(metrics.includes("area_mm2") && metrics.includes("volume_mm3"), "raw_metric_units");
  const meshRequests = requests.length, meshDraws = draws, meshNodes = nodes(host);
  update("ja");
  equal(draws, meshDraws, "locale_draw_changed");
  equal(requests.length, meshRequests, "locale_transport_changed");
  equal(panel.model.response, response, "locale_response_unchanged");
  equal(node(root, "pre").textContent, metrics, "locale_metrics_unchanged");
  equal(nodes(host), meshNodes, "locale_node_set_changed");
  if (mode === "redraw") throw new Error("negative_control_not_triggered");
  const dirty = editor.value + "\n\t"; editor.value = dirty;
  node(root, '[data-surface-action="download-step"]').click(); await settle();
  await Promise.all(cryptoCalls); await settle();
  equal(downloads, ["surface.step"], "actual_download_path");
  equal(editor.value, dirty, "download_dirty_retained");
  equal(node(root, "output").textContent, translate("downloaded"), "download_notice");
  update("en");
  equal(node(root, "output").textContent, translate("downloaded"), "download_notice_switch");
  node(root, '[data-surface-action="download-brep"]').click(); await settle();
  await Promise.all(cryptoCalls); await settle();
  equal(downloads, ["surface.step", "surface.brep"], "brep_download_path");
  equal(editor.value, dirty, "brep_dirty_retained");
  const causes = [
    ["thickness_requires_positive_integer_nm", "error_thickness"],
    ["webgl_unavailable", "error_webgl"], ["surface_webgl_error", "error_webgl"],
    ["invalid_surface_extent", "error_mesh"], ["invalid_surface_triangle", "error_mesh"],
    ["degenerate_surface_triangle", "error_mesh"], ["surface_request_in_progress", "error_busy"],
    ["surface_export_failed", "error_export"], ["surface_export_receipt_invalid", "error_export"],
    ["surface_export_bytes_changed", "error_export"], ["backend detail <b>raw</b> nm", "error"]
  ];
  for (const [detail, key] of causes) {
    nextError = new Error(detail);
    node(root, '[data-surface-action="reload"]').click(); await settle();
    equal(panel.model.error, detail, "raw_error_detail");
    equal(node(root, "output").textContent, translate(key, {detail}), "localized_error");
    const failureRequests = requests.length, failureDraws = draws, failureNodes = nodes(host);
    update("zh-CN");
    equal(node(root, "output").textContent, translate(key, {detail}), "error_notice_switch");
    equal(panel.model.error, detail, "error_raw_after_switch");
    equal(requests.length, failureRequests, "locale_transport_changed");
    equal(draws, failureDraws, "locale_draw_changed");
    equal(nodes(host), failureNodes, "locale_node_set_changed");
    update("ja");
  }
  thickness.value = "0";
  const recipeBeforeInvalid = editor.value;
  node(root, '[data-surface-action="thicken"]').click(); await settle();
  equal(panel.model.error, "thickness_requires_positive_integer_nm", "local_validation_detail");
  equal(editor.value, recipeBeforeInvalid, "validation_recipe_unchanged");
  let release; delayed = new Promise(resolve => { release = resolve; });
  const pending = panel.reload(); await settle();
  check(panel.model.busy, "busy_path");
  const busyState = controlState(items), busyButtons = root.querySelectorAll("button").map(x => x.disabled);
  update("en");
  equal(controlState(items), busyState, "busy_controls_preserved");
  equal(root.querySelectorAll("button").map(x => x.disabled), busyButtons, "busy_buttons_preserved");
  equal(root.getAttribute("aria-busy"), "true", "busy_aria_preserved");
  release(); delayed = null; await pending;
  check(!panel.model.busy, "busy_finished");
  const status = node(root, "output"), finalStatus = status.textContent;
  panel.dispose(); const afterDisposeRequests = requests.length, afterDisposeDraws = draws;
  update("ja");
  equal(status.textContent, finalStatus, "disposed_notice_not_repainted");
  equal(host.children.length, 0, "dispose_removed_root");
  equal(requests.length, afterDisposeRequests, "disposed_transport_unchanged");
  equal(draws, afterDisposeDraws, "disposed_draw_unchanged");
  check(observers[0].disconnected, "dispose_observer");
  for (const callback of timers) callback();
  // Execute the actual authenticated industrial surface branch too.
  workbench = new Element("section");
  const details = new Element("details"), lazyHost = new Element("div");
  lazyHost.setAttribute("data-surfaces-host", ""); details.append(lazyHost); workbench.append(details);
  window.OpenCADProjectSession = {ready: Promise.resolve()};
  vm.runInContext(industrial, context, {filename: "industrial-panels.js"}); await settle();
  equal(lazyHost.children.length, 0, "lazy_mount_before_open");
  details.open = true; details.dispatchEvent({type: "toggle"}); await settle();
  check(Boolean(window.opencadSurfacesPanel), "industrial_surface_mounted");
  annotated(window.opencadSurfacesPanel.root);
  const lazyNodes = nodes(lazyHost), lazyRequests = requests.length, lazyDraws = draws;
  details.open = false; details.dispatchEvent({type: "toggle"});
  update("zh-CN");
  equal(details.open, false, "details_open_preserved");
  details.open = true; details.dispatchEvent({type: "toggle"}); await settle();
  equal(nodes(lazyHost), lazyNodes, "industrial_no_remount");
  equal(requests.length, lazyRequests, "industrial_no_refetch");
  equal(draws, lazyDraws, "industrial_no_redraw");
  window.opencadSurfacesPanel.dispose();
  equal(requests.filter(x => x.url === "http://localhost/web/panel-i18n.json").length, 1,
    "dictionary_loaded_once");
  equal(mode, "positive", "negative_control_not_triggered");
  console.log(JSON.stringify({verdict: "PASS", mode, checks, sourceNode: true}));
}
main().catch(error => {
  console.error(JSON.stringify({verdict: "FAIL", mode, checks, sourceNode: true,
    code: error.code || error.message, error: String(error.stack || error)}));
  process.exitCode = 1;
});
"""


def require(condition, code):
    if not condition:
        raise AssertionError(code)


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def git(root, *args):
    run = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    require(run.returncode == 0, f"git_rc={run.returncode}:{' '.join(args)}:{run.stderr.decode()}")
    return run.stdout


def entry(path):
    require(path.is_file() and not path.is_symlink(), "source_file_type:" + str(path))
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": sha(data),
            "mode": "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"}


def industrial_guard(root):
    old = git(root, "show", BASE + ":" + OWNED[1]).decode()
    new = (root / OWNED[1]).read_text()
    def outside(text):
        text, count = re.subn(r"  const surfaceLabels = \{[\s\S]*?\n  \};",
                             "  SURFACE_LABEL_SCOPE", text)
        require(count == 1, "industrial_surface_label_boundary")
        text, count = re.subn(
            r'          if \(host\.hasAttribute\("data-surfaces-host"\)\) \{[\s\S]*?'
            r'          \} else if \(host\.hasAttribute\("data-assembly-host"\)\)',
            "          SURFACE_MOUNT_SCOPE else if ASSEMBLY", text)
        require(count == 1, "industrial_surface_mount_boundary")
        return text
    require(outside(old) == outside(new), "industrial_non_surface_changed")


def surface_guard(root):
    """Freeze geometry, transport, recipes and receipts, not human-facing paint."""
    old = git(root, "show", BASE + ":" + OWNED[0]).decode()
    new = (root / OWNED[0]).read_text()
    boundaries = (
        ("  function plane() {", "  function mount(host, options) {"),
        ("    function setRecipe(value)", '    button("plane"'),
        ("      const nm = Number(thickness.value)", '    button("sew"'),
        ("      const left = plane(), right = plane();", "    const actions ="),
        ("    function busy(value)", "    function show(response)"),
        ("    function show(response)", "    async function perform(body)"),
        ("    async function perform(body)", '    button("apply"'),
        ("    async function download(fmt)", '    button("download-step"'),
    )
    for start, end in boundaries:
        require(old.count(start) == new.count(start) == 1 and
                old.count(end) == new.count(end) == 1, "surface_guard_anchor")
        left, right = old.split(start)[1].split(end)[0], new.split(start)[1].split(end)[0]
        if start.startswith("    async function download"):
            left, count = re.subn(
                r'        status\.textContent = text\("downloaded", "[^"]+"\);',
                '        notify("downloaded");', left)
            require(count == 1, "download_notice_boundary")
        elif start.startswith("    function show"):
            left, count = re.subn(
                r'      status\.textContent = response\.empty \? text\("empty", "[^"]+"\) :\n'
                r'        text\("ready", "[^"]+"\);',
                '      notify(response.empty ? "empty" : "ready");', left)
            require(count == 1, "show_notice_boundary")
        elif start.startswith("      const nm"):
            left = left.replace('button("sew", "Two-plane seam",', 'button("sew",')
        require(left == right, "surface_behavior_changed:" + start)


def keymap_guard(root, catalog):
    text = (root / OWNED[3]).read_text()
    require("| Key Suffix | zh-CN | en | ja |" in text, "keymap_header")
    rows = []
    table = text.split("| Key Suffix | zh-CN | en | ja |", 1)[1].split("\n\n", 1)[0]
    for line in table.splitlines():
        if line.startswith("| ") and not line.startswith("| --- "):
            cells = re.split(r"(?<!\\)\|", line)[1:-1]
            require(len(cells) == 4, "keymap_column_count")
            rows.append([cell.strip().replace(r"\|", "|") for cell in cells])
    require(len(rows) == 27 and len({row[0] for row in rows}) == 27, "keymap_set")
    payload = read_json(catalog)
    for locale, column in (("zh-CN", 1), ("en", 2), ("ja", 3)):
        for row in rows:
            key = "panel.surfaces." + row[0]
            require(key in payload["locales"][locale], "catalog_surface_key_missing:" + locale + ":" + key)
            require(payload["locales"][locale][key] == row[column],
                    "catalog_keymap_mismatch:" + locale + ":" + key)


def source(root, foundation, catalog):
    head = git(root, "rev-parse", "HEAD").decode().strip()
    git(root, "merge-base", "--is-ancestor", BASE, head)
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    require(all(line[3:] in OWNED for line in status), "source_dirty_outside_owned")
    names = [os.fsdecode(name) for name in git(root, "ls-files", "-z").split(b"\0") if name]
    names = sorted(set(names) | {name for name in OWNED if (root / name).exists()})
    changed = git(root, "diff", "--name-only", BASE, head).decode().splitlines()
    if status:
        require(set(changed) <= set(OWNED), "committed_write_scope_mismatch")
    files = {name: entry(root / name) for name in names}
    physical = set()
    for directory, dirs, leaves in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if ".git" in dirs:
            require(relative == Path("."), "nested_git_metadata")
            dirs.remove(".git")
        require(all(not (Path(directory) / name).is_symlink() for name in dirs),
                "source_directory_symlink")
        for name in leaves:
            if relative == Path(".") and name == ".git":
                continue
            physical.add((relative / name).as_posix())
    require(physical == set(files), "source_physical_set_mismatch")
    dependencies = {str(foundation / WEB / "js/locale-service.js"):
                    entry(foundation / WEB / "js/locale-service.js"), str(catalog): entry(catalog)}
    return {"head": head, "status": status, "development": bool(status),
            "files": files, "dependencies": dependencies,
            "sha256": sha(json.dumps([files, dependencies], sort_keys=True).encode())}


def artifacts(out):
    result = {}
    for path in sorted(out.rglob("*")):
        require(not path.is_symlink(), "artifact_symlink")
        if path.is_file() and path != out / "result.json":
            result[path.relative_to(out).as_posix()] = entry(path)
    return result


def child(root, foundation, catalog, out, mode):
    script = out / "runtime/surface-fixture.cjs"
    command = [shutil.which("node") or "node", str(script), str(root),
               str(foundation), str(catalog), mode]
    started = stamp()
    run = subprocess.run(command, capture_output=True, check=False)
    for name, data in (("stdout", run.stdout), ("stderr", run.stderr)):
        (out / f"data/tmp/{mode}.{name}.log").write_bytes(data)
    stream = run.stdout if run.returncode == 0 else run.stderr
    terminal = json.loads(stream.decode().splitlines()[-1])
    return {"mode": mode, "argv": command, "actual_rc": run.returncode,
            "started_at": started, "finished_at": stamp(), "terminal": terminal}


def validate_child(record):
    mode = record["mode"]
    expected = 0 if mode == "positive" else 1
    terminal = record["terminal"]
    require(record["actual_rc"] == expected,
            f"child_rc:{mode}:{record['actual_rc']}:{terminal.get('code')}")
    require(terminal["mode"] == mode and terminal["checks"] > 0 and
            terminal["sourceNode"] is True, "child_terminal_missing")
    require(terminal["verdict"] == ("PASS" if expected == 0 else "FAIL"),
            "child_verdict_mismatch")
    if expected:
        require(terminal["code"] == NEGATIVES[mode], "negative_first_cause:" + mode)


def marker(report):
    return (f"{MARKER} verdict={report['verdict']} checks={report['checks']} "
            f"negatives={report['negatives']} rc={report['actual_rc']}\n")


def verify(prior, root, foundation, catalog, evidence, parent_rc):
    require(parent_rc == 0, "parent_rc_not_zero")
    require(prior["verdict"] == "PASS" and prior["actual_rc"] == 0 and
            prior["first_cause"] is None, "producer_not_pass")
    require(prior["source_root"] == str(root) and prior["foundation_root"] == str(foundation)
            and prior["catalog"] == str(catalog), "source_coordinate_mismatch")
    require(prior["source_before"] == prior["source_after"], "producer_source_drift")
    require(prior["source_before"] == source(root, foundation, catalog), "current_source_drift")
    require(prior["artifacts"] == artifacts(evidence), "artifact_closure_mismatch")
    require((evidence / "runtime/surface-fixture.cjs").read_text() == NODE,
            "sourceNode_fixture_mismatch")
    records = prior["children"]
    require([record["mode"] for record in records] == ["positive", *NEGATIVES],
            "child_set_mismatch")
    for record in records:
        mode = record["mode"]
        require(record["argv"] == [shutil.which("node") or "node",
                str(evidence / "runtime/surface-fixture.cjs"), str(root),
                str(foundation), str(catalog), mode], "child_argv_mismatch")
        validate_child(record)
        stream = "stdout" if mode == "positive" else "stderr"
        terminal = read_json_line(evidence / f"data/tmp/{mode}.{stream}.log")
        require(terminal == record["terminal"], "child_log_terminal_mismatch")
        start = datetime.datetime.fromisoformat(record["started_at"])
        end = datetime.datetime.fromisoformat(record["finished_at"])
        require(start.tzinfo and end.tzinfo and
                datetime.datetime.fromisoformat(prior["started_at"]) <= start <= end <=
                datetime.datetime.fromisoformat(prior["finished_at"]), "child_time_mismatch")
    require(prior["checks"] == records[0]["terminal"]["checks"] and
            prior["negatives"] == len(NEGATIVES), "count_mismatch")
    require((evidence / "data/tmp/completion.log").read_text() == marker(prior),
            "completion_mismatch")
    industrial_guard(root)
    surface_guard(root)
    keymap_guard(root, catalog)
    return len(records)


def read_json_line(path):
    return json.loads(path.read_text().splitlines()[-1])


def controls(prior_path, root, foundation, catalog, out, parent_rc):
    prior = read_json(prior_path)
    verify(prior, root, foundation, catalog, prior_path.parent, parent_rc)
    cases = {
        "parent-rc": "parent_rc_not_zero", "missing-child": "child_set_mismatch",
        "false-rc": "child_rc:positive:1:None", "false-count": "count_mismatch",
        "changed-artifact": "artifact_closure_mismatch",
        "rehashed-completion": "completion_mismatch",
        "rehashed-terminal": "child_log_terminal_mismatch",
        "false-time": "child_time_mismatch", "false-source": "current_source_drift",
    }
    results = []
    for name, cause in cases.items():
        case = out / "data/tmp" / name
        evidence = case / "evidence"
        shutil.copytree(prior_path.parent, evidence)
        changed = copy.deepcopy(prior)
        if name == "missing-child":
            changed["children"].pop()
        elif name == "false-rc":
            changed["children"][0]["actual_rc"] = 1
        elif name == "false-count":
            changed["checks"] += 1
        elif name == "changed-artifact":
            (evidence / "data/tmp/positive.stdout.log").write_text("corrupt\n")
        elif name == "rehashed-completion":
            (evidence / "data/tmp/completion.log").write_text("PASS\n")
        elif name == "rehashed-terminal":
            terminal = copy.deepcopy(changed["children"][0]["terminal"])
            terminal["checks"] += 1
            (evidence / "data/tmp/positive.stdout.log").write_text(json.dumps(terminal) + "\n")
        elif name == "false-time":
            changed["children"][0]["finished_at"] = "1970-01-01T00:00:00+00:00"
        elif name == "false-source":
            changed["source_before"]["sha256"] = "0" * 64
            changed["source_after"] = copy.deepcopy(changed["source_before"])
        for record in changed["children"]:
            record["argv"][1] = str(evidence / "runtime/surface-fixture.cjs")
        if name.startswith("rehashed-"):
            changed["artifacts"] = artifacts(evidence)
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        command = [sys.executable, "-B", str(root / TEST), "--verify",
                   str(evidence / "result.json"), "--parent-rc",
                   "1" if name == "parent-rc" else "0", "--source-root", str(root),
                   "--foundation-root", str(foundation), "--catalog", str(catalog),
                   "--out", str(case / "reader")]
        run = subprocess.run(command, capture_output=True, check=False)
        (case / "reader.stdout.log").write_bytes(run.stdout)
        (case / "reader.stderr.log").write_bytes(run.stderr)
        receipt = read_json(case / "reader/result.json")
        results.append({"name": name, "argv": command, "actual_rc": run.returncode,
                        "first_cause": receipt["first_cause"], "finished_at": stamp()})
        require(run.returncode == 1 and receipt["first_cause"] == cause,
                "reader_negative_failed:" + name + ":" + str(receipt["first_cause"]))
    return results


class SurfacePanelLocaleTests(unittest.TestCase):
    def test_source_node(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        foundation = Path(os.environ.get("OPENCAD_LOCALE_FOUNDATION_ROOT", FOUNDATION))
        catalog = Path(os.environ.get("OPENCAD_PANEL_CATALOG", foundation / WEB / "panel-i18n.json"))
        out = Path(evidence) / "surface-panel-locale"
        create_out(out, ROOT)
        (out / "runtime/surface-fixture.cjs").write_text(NODE)
        records = []
        for mode in ("positive", *NEGATIVES):
            record = child(ROOT, foundation, catalog, out, mode)
            records.append(record)
            (out / "data/tmp/children.json").write_text(json.dumps(records, indent=2) + "\n")
            validate_child(record)
        industrial_guard(ROOT)
        surface_guard(ROOT)
        keymap_guard(ROOT, catalog)
        self.assertEqual(len(records), 1 + len(NEGATIVES))


def create_out(out, root):
    require(out.is_absolute() and out.is_relative_to(Path("/hf3fs-jd/data/tmp"))
            and not out.exists() and not out.resolve().is_relative_to(root.resolve()),
            "fresh_external_out_required")
    out.mkdir(parents=True, exist_ok=False)
    (out / "runtime").mkdir()
    (out / "data/tmp").mkdir(parents=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--selftest", action="store_true")
    choice.add_argument("--verify", type=Path)
    choice.add_argument("--controls", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--foundation-root", type=Path, default=FOUNDATION)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--parent-rc", type=int)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root, foundation, out = args.source_root.absolute(), args.foundation_root.absolute(), args.out.absolute()
    catalog = (args.catalog or foundation / WEB / "panel-i18n.json").absolute()
    create_out(out, root)
    report = {"started_at": stamp(), "source_root": str(root), "foundation_root": str(foundation),
              "catalog": str(catalog), "verdict": "FAIL", "actual_rc": 1,
              "first_cause": None, "checks": 0, "negatives": 0, "children": []}
    try:
        before = source(root, foundation, catalog)
        report["source_before"] = before
        if args.verify or args.controls:
            prior_path = args.verify or args.controls
            report["input"] = {"path": str(prior_path), "sha256": sha(prior_path.read_bytes()),
                               "parent_rc_received": args.parent_rc}
            if args.controls:
                report["reader_controls"] = controls(prior_path, root, foundation, catalog,
                                                     out, args.parent_rc)
                report["negatives"] = len(report["reader_controls"])
            else:
                prior = read_json(prior_path)
                report["verified_children"] = verify(prior, root, foundation, catalog,
                                                    prior_path.parent, args.parent_rc)
                report["checks"], report["negatives"] = prior["checks"], prior["negatives"]
        else:
            (out / "runtime/surface-fixture.cjs").write_text(NODE)
            for mode in ("positive", *NEGATIVES):
                record = child(root, foundation, catalog, out, mode)
                report["children"].append(record)
                validate_child(record)
            report["checks"] = report["children"][0]["terminal"]["checks"]
            report["negatives"] = len(NEGATIVES)
            industrial_guard(root)
            surface_guard(root)
            keymap_guard(root, catalog)
        report["source_after"] = source(root, foundation, catalog)
        require(before == report["source_after"], "source_drift")
        report["verdict"], report["actual_rc"] = "PASS", 0
    except Exception as error:
        report["first_cause"] = str(error)
    report["finished_at"] = stamp()
    (out / "data/tmp/completion.log").write_text(marker(report))
    report["artifacts"] = artifacts(out)
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(marker(report).strip(), flush=True)
    if report["first_cause"]:
        print("first_cause=" + report["first_cause"], flush=True)
    return report["actual_rc"]


if __name__ == "__main__":
    sys.exit(main())
