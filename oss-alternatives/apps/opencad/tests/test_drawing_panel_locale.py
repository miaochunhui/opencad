"""Strict offline Node execution of drawing source plus the real locale service.

Evidence, child scripts and runtime data stay in a new external directory.
--contract uses the documented keymap in memory only, never writes a dictionary,
and is explicitly not acceptance of the dictionary owner's integrated package.
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
BASE = "9c4abdac2906111d49f0c39a437ca4cef7abb9b3"
FOUNDATION = "97b80e675f39e23faffcb8c0f6bb8706a8238f0b"
WEB = "oss-alternatives/apps/opencad/web"
OWNED = (
    f"{WEB}/js/drawing-panel.js",
    "oss-alternatives/apps/opencad/tests/test_drawing_panel_locale.py",
    "docs/design/drawing-panel-locale.md",
)
MARKER = "OPENCAD_DRAWING_LOCALE"
NEGATIVES = {
    "missing-key": "locale_missing_key",
    "missing-args": "locale_argument_set_mismatch",
    "extra-args": "locale_argument_set_mismatch",
    "no-service": "drawing_locale_service_required",
}

NODE = r"""
"use strict";
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const assert = require("node:assert/strict");
const [root, foundation, dictionaryRoot, mode, contract] = process.argv.slice(2);
const web = "oss-alternatives/apps/opencad/web";
const source = fs.readFileSync(path.join(root, web, "js/drawing-panel.js"), "utf8");
const localeSource = fs.readFileSync(path.join(foundation, web, "js/locale-service.js"), "utf8");
const shipped = JSON.parse(fs.readFileSync(path.join(dictionaryRoot, web, "panel-i18n.json"), "utf8"));
const rows = fs.readFileSync(path.join(root, "docs/design/drawing-panel-locale.md"), "utf8")
  .split("\n").filter(line => /^\| [a-z][a-z0-9_.]* \|/.test(line)).map(line => {
    const [key, zh, en, ja] = line.split("|").slice(1, -1).map(x => x.trim());
    return {key: "panel.drawing." + key, en, "zh-CN": zh, ja};
  });
assert.equal(rows.length, 38, "frozen handoff key count");
if (contract === "contract") {
  // Protocol-only in-memory fixture: the unique production dictionary stays read-only.
  for (const code of ["en", "zh-CN", "ja"]) {
    for (const row of rows) shipped.locales[code][row.key] = row[code];
  }
}
let checks = 0, scenarios = 0;
function ok(value, name) { checks++; assert.ok(value, name); }
function eq(value, expected, name) { checks++; assert.deepEqual(value, expected, name); }
function same(value, expected, name) { checks++; assert.strictEqual(value, expected, name); }
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}
class Bus {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, callback) {
    const entries = this.listeners.get(type) || [];
    entries.push(callback); this.listeners.set(type, entries);
  }
  removeEventListener(type, callback) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x !== callback));
  }
  dispatchEvent(event) {
    for (const callback of [...(this.listeners.get(event.type) || [])]) callback(event);
    return true;
  }
}
function decode(text) {
  return text.replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&#39;/g, "'");
}
class Element extends Bus {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.localName = tag.toLowerCase();
    this.nodeType = 1; this.children = []; this.attrs = new Map(); this.parentNode = null;
    this._text = ""; this.value = ""; this.disabled = false; this.hidden = false;
    this.inert = false; this.checked = false; this.selectionStart = 0;
    this.selectionEnd = 0; this.selectionDirection = "none"; this.scrollTop = 0;
    this.scrollLeft = 0; this.namespaceURI = null;
  }
  set className(value) { this.setAttribute("class", value); }
  get className() { return this.getAttribute("class") || ""; }
  get attributes() { return [...this.attrs].map(([name, value]) => ({name, value})); }
  get dataset() {
    const values = {};
    for (const [name, value] of this.attrs) {
      if (name.startsWith("data-")) values[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    return values;
  }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  removeAttribute(name) { this.attrs.delete(name); }
  appendChild(node) { this.children.push(node); node.parentNode = this; return node; }
  append(node) { return this.appendChild(node); }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(x => x !== this);
      this.parentNode = null;
    }
  }
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentNode = null;
    this.children = []; this._text = "";
    for (const node of nodes) this.appendChild(node);
  }
  set textContent(value) {
    assert.equal(this.children.length, 0, "text update must not destroy child elements");
    this._text = String(value);
  }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
  set innerHTML(value) {
    assert.equal(this.children.length, 0, "panel may be constructed exactly once");
    parse(value, this);
  }
  querySelectorAll(selector) {
    assert.ok(/^(?:\*|[a-z][a-z0-9-]*|\.[a-z][a-z0-9-]*|\[[a-z][a-z0-9-]*(?:="[^"]*")?\])$/.test(selector),
      "unsupported selector:" + selector);
    const result = [];
    function matches(node) {
      if (selector === "*") return true;
      if (selector.startsWith(".")) return node.className.split(/\s+/).includes(selector.slice(1));
      if (selector.startsWith("[")) {
        const found = selector.match(/^\[([^=]+)(?:="([^"]*)")?\]$/);
        return node.hasAttribute(found[1]) && (found[2] === undefined || node.getAttribute(found[1]) === found[2]);
      }
      return node.localName === selector;
    }
    function visit(node) {
      for (const child of node.children) {
        if (matches(child)) result.push(child);
        visit(child);
      }
    }
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  click() {
    if (this.localName === "a") downloads.push({name: this.download, href: this.href});
    else this.dispatchEvent({type: "click", preventDefault() {}});
  }
}
function parse(html, parent, svg = false) {
  const stack = [parent], tokens = html.match(/<[^>]*>|[^<]+/g) || [];
  for (const token of tokens) {
    if (token.startsWith("</")) { assert.ok(stack.length > 1); stack.pop(); }
    else if (token.startsWith("<")) {
      const match = token.match(/^<([a-zA-Z][a-zA-Z0-9-]*)([\s\S]*?)(\/?)>$/);
      assert.ok(match, "fixture refuses malformed markup");
      const node = new Element(match[1]);
      if (svg) node.namespaceURI = "http://www.w3.org/2000/svg";
      for (const attribute of match[2].matchAll(/([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:="([^"]*)")?/g)) {
        node.setAttribute(attribute[1], decode(attribute[2] || ""));
      }
      stack[stack.length - 1].appendChild(node);
      if (!match[3] && !["input", "img", "br", "hr", "meta", "link"].includes(node.localName)) stack.push(node);
    } else {
      stack[stack.length - 1]._text += decode(token);
    }
  }
  assert.equal(stack.length, 1, "balanced markup");
}
const downloads = [], urls = [];
function response(value) { return {ok: true, json: async () => clone(value)}; }
const original = {
  ok: true, defined: true, revision: 7, model_version: 7,
  project_id: "raw-project-UUID", sha256: "1".repeat(64),
  source_model_sha256: "a".repeat(64), source_document_sha256: "b".repeat(64),
  drawing_sha256: "c".repeat(64),
  svg: '<svg xmlns="http://www.w3.org/2000/svg"><title>RAW MODEL UUID GB ISO mm</title>' +
    '<g><text>RAW top front section</text><line x1="0" y1="1" x2="2" y2="3"/></g></svg>',
  definition: {title: "RAW MODEL UUID", views: ["top", "front"], sections: [],
    dimensions: [], bom_refs: ["uuid-unchanged"]},
  dimensions: [{id: "raw_dimension_uuid", value_nm: 123456789, ref: ["RAW UUID", "edge:8"]}]
};
async function fixture(options = {}) {
  const window = new Bus(), document = new Bus(), calls = [], queue = [];
  document.body = new Element("body");
  document.currentScript = {src: "http://localhost/web/js/locale-service.js"};
  document.createElement = tag => new Element(tag);
  document.importNode = node => node;
  document.activeElement = null;
  document.querySelectorAll = selector => {
    assert.equal(selector, "#lang-select option, #locale option");
    return ["en", "zh-CN", "ja"].map(code => ({value: code}));
  };
  const load = deferred();
  window.fetch = async (url, init) => {
    assert.equal(url, "http://localhost/web/panel-i18n.json");
    if (options.late) await load.promise;
    return {ok: true, text: async () => JSON.stringify(shipped)};
  };
  const context = vm.createContext({window, document, console, URL: class extends URL {
    static createObjectURL(blob) { urls.push(blob); return "blob:fixture:" + urls.length; }
    static revokeObjectURL() {}
  }, setTimeout: fn => { fn(); return 1; }, CustomEvent: class {
    constructor(type, value) { this.type = type; this.detail = value.detail; }
  }, DOMParser: class {
    parseFromString(text, type) {
      assert.equal(type, "image/svg+xml");
      const doc = new Element("document"); parse(text, doc, true);
      doc.documentElement = doc.children[0]; return doc;
    }
  }});
  vm.runInContext(localeSource, context, {filename: path.join(foundation, web, "js/locale-service.js")});
  vm.runInContext(source, context, {filename: path.join(root, web, "js/drawing-panel.js")});
  const host = new Element("main"); document.body.appendChild(host);
  const request = async (url, init) => {
    calls.push({url, init: clone(init)});
    assert.equal(init.credentials, "same-origin");
    if (queue.length) return await queue.shift()(url, init);
    return response(original);
  };
  if (!options.late) await window.OpenCADLocale.ready;
  if (options.noService) delete window.OpenCADLocale;
  const controller = window.opencadDrawingPanel.mount(host, {fetch: request});
  return {window, document, host, controller, calls, queue, load,
    locale: window.OpenCADLocale, panel: host.children[0]};
}
function nodes(f) {
  return {editor: f.panel.querySelector("textarea"), output: f.panel.querySelector("output"),
    binding: f.panel.querySelector(".drawing-panel-binding"),
    preview: f.panel.querySelector(".drawing-panel-preview"), body: f.panel.querySelector("tbody"),
    refresh: f.panel.querySelector("[data-drawing-refresh]"),
    exports: f.panel.querySelectorAll("[data-drawing-export]"),
    all: f.panel.querySelectorAll("*")};
}
function snapshot(f) {
  const n = nodes(f);
  return {identities: n.all, editor: n.editor, value: n.editor.value,
    selection: [n.editor.selectionStart, n.editor.selectionEnd, n.editor.selectionDirection],
    focus: f.document.activeElement, scroll: [n.editor.scrollTop, n.preview.scrollLeft],
    busy: f.panel.getAttribute("aria-busy"),
    flags: n.all.map(x => [x.disabled, x.hidden, x.inert, x.checked]),
    svg: n.preview.children[0], rows: [...n.body.children],
    rawSVG: n.preview.textContent, rawDimensions: n.body.textContent,
    calls: clone(f.calls), downloads: clone(downloads), urls: [...urls]};
}
function switchLanguage(f, code) {
  const before = snapshot(f);
  f.locale.update({locale: code, strings: {"app.title": "RAW core"}});
  const after = snapshot(f);
  eq(after.identities.length, before.identities.length, "same node count");
  after.identities.forEach((node, index) => same(node, before.identities[index], "same node identity"));
  same(after.editor, before.editor, "same editor");
  same(after.focus, before.focus, "same focus");
  same(after.svg, before.svg, "same SVG identity");
  after.rows.forEach((node, index) => same(node, before.rows[index], "same dimension row identity"));
  for (const key of ["value", "selection", "scroll", "busy", "flags", "rows",
    "rawSVG", "rawDimensions", "calls", "downloads", "urls"]) eq(after[key], before[key], key);
  eq(f.panel.querySelector("h2").textContent, shipped.locales[code]["panel.drawing.heading"]);
  eq(f.panel.getAttribute("aria-label"), shipped.locales[code]["panel.drawing.heading"]);
  eq(f.panel.querySelector("textarea").getAttribute("aria-label"),
    shipped.locales[code]["panel.drawing.definition"]);
}
async function settled() { await new Promise(resolve => setImmediate(resolve)); }
async function main() {
  if (mode === "no-service") { await fixture({noService: true}); throw new Error("negative_not_seen"); }
  const f = await fixture(); const n = nodes(f);
  if (mode === "missing-key") {
    n.editor.setAttribute("data-panel-i18n-title", "panel.drawing.not_present");
    await f.controller.ready; f.locale.update({locale: "ja", strings: {"app.title": "core"}});
    throw new Error("negative_not_seen");
  }
  await f.controller.ready; await settled();
  if (mode === "missing-args" || mode === "extra-args") {
    n.binding.setAttribute("data-panel-i18n", "panel.drawing.binding.version");
    n.binding.setAttribute("data-panel-i18n-args", JSON.stringify(mode === "missing-args" ?
      {revision: 7} : {revision: 7, sha: "a".repeat(64), extra: 1}));
    f.locale.update({locale: "ja", strings: {"app.title": "core"}});
    throw new Error("negative_not_seen");
  }
  if (mode !== "positive") throw new Error("fixture_unknown_mode");
  for (const code of ["en", "zh-CN", "ja"]) {
    const expected = rows.map(row => row.key).sort();
    eq(Object.keys(shipped.locales[code]).filter(key => key.startsWith("panel.drawing.")).sort(),
      expected, "complete documented drawing key collection");
    f.locale.update({locale: code, strings: {"app.title": "raw core"}});
    for (const row of rows) {
      const args = {};
      for (const match of row[code].matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)) args[match[1]] = match[1];
      eq(f.locale.t(row.key, args), row[code].replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (_, key) => args[key]),
        "actual translation and args:" + code + ":" + row.key);
    }
  }
  scenarios++;
  ok(f.controller.ready && typeof f.controller.ready.then === "function", "mount exposes locale readiness");
  eq(f.calls.length, 1, "one initial drawing fetch");
  ok(n.preview.children[0], "real panel render executes checkedSvg");
  eq(n.body.textContent, "raw_dimension_uuid123.456789 mmRAW UUID / edge:8", "raw dimensions");
  n.editor.value = '{ "title": "UNSAVED UUID", "views": ["right"], "bom_refs": ["raw-ref"] }';
  n.editor.selectionStart = 9; n.editor.selectionEnd = 18; n.editor.selectionDirection = "backward";
  n.editor.scrollTop = 12; n.preview.scrollLeft = 23;
  f.document.activeElement = n.editor; n.editor.dispatchEvent({type: "input"});
  n.editor.hidden = false; n.editor.inert = false;
  for (const code of ["zh-CN", "ja", "en", "zh-CN"]) switchLanguage(f, code);
  ok(n.binding.textContent.includes("7") && n.binding.textContent.includes("a".repeat(64)), "raw binding");
  scenarios++;
  for (const code of ["ja", "en", "zh-CN"]) {
    const before = snapshot(f);
    f.window.dispatchEvent({type: "opencad:state", detail: {
      ok: true, project_id: original.project_id, revision: 7, sha256: original.sha256,
      locale: code, strings: {"app.title": "raw core"}
    }});
    const after = snapshot(f);
    eq(after.calls, before.calls, "same-model language state must not fetch");
    same(after.svg, before.svg, "same-model language state preserves SVG");
    eq(after.value, before.value, "same-model language state preserves draft");
    eq(after.busy, before.busy, "same-model language state preserves pending");
  }
  scenarios++;
  const held = deferred(); f.queue.push(async () => held.promise);
  const refresh = f.controller.refresh();
  await settled(); const pendingCalls = f.calls.length;
  for (const code of ["ja", "en", "zh-CN"]) switchLanguage(f, code);
  f.window.dispatchEvent({type: "opencad:state", detail: {
    ok: true, project_id: original.project_id, revision: 7, sha256: original.sha256,
    locale: "ja", strings: {"app.title": "raw core"}
  }});
  eq(f.calls.length, pendingCalls, "locale never queues refresh");
  held.resolve(response({...original, revision: 8, model_version: 8,
    source_model_sha256: "d".repeat(64)})); await refresh;
  eq(f.calls.length, pendingCalls, "no locale refresh race after release");
  ok(n.binding.textContent.includes("8") && n.binding.textContent.includes("d".repeat(64)), "fresh revision");
  ok(n.editor.value.includes("UNSAVED UUID"), "dirty draft survives asynchronous refresh");
  scenarios++;
  const old = deferred(), fresh = deferred();
  f.queue.push(async () => old.promise, async () => fresh.promise);
  const oldRefresh = f.controller.refresh(); await settled();
  f.window.dispatchEvent({type: "opencad:parametric"}); switchLanguage(f, "ja");
  old.resolve(response({...original, revision: 9, model_version: 9})); await oldRefresh;
  await settled(); switchLanguage(f, "en");
  fresh.resolve(response({...original, revision: 10, model_version: 10,
    source_model_sha256: "e".repeat(64)})); await settled();
  ok(n.binding.textContent.includes("10") && n.binding.textContent.includes("e".repeat(64)),
    "queued newer model wins");
  scenarios++;
  const saved = deferred();
  f.queue.push(async (url, init) => {
    eq(url, "/api/drawing"); eq(init.method, "POST");
    eq(init.headers, {"Content-Type": "application/json", "X-OpenCAD-Request": "1"});
    eq(JSON.parse(init.body).definition.title, "submitted raw title");
    return saved.promise;
  });
  n.editor.value = JSON.stringify({...original.definition, title: "submitted raw title"});
  const submit = n.editor.parentNode.parentNode.querySelector("button");
  eq(submit.getAttribute("type"), "submit");
  f.panel.querySelector("form").dispatchEvent({type: "submit", preventDefault() {}});
  await settled();
  n.editor.value = '{"title":"later RAW draft","views":["right"]}';
  n.editor.dispatchEvent({type: "input"});
  switchLanguage(f, "zh-CN"); saved.resolve(response({ok: true})); await settled(); await settled();
  eq(n.editor.value, '{"title":"later RAW draft","views":["right"]}', "later draft survives save");
  scenarios++;
  const backend = "revision_conflict: RAW detail UUID mm GB ISO";
  f.queue.push(async () => ({ok: false, json: async () => ({error: "revision_conflict", detail: backend})}));
  await f.controller.refresh();
  for (const code of ["en", "ja", "zh-CN"]) {
    switchLanguage(f, code);
    ok(n.output.textContent.startsWith(shipped.locales[code]["panel.drawing.error.prefix"]),
      "localized error prefix");
    ok(n.output.textContent.endsWith(backend), "raw backend detail preserved");
  }
  scenarios++;
  n.editor.value = "{bad raw JSON";
  f.panel.querySelector("form").dispatchEvent({type: "submit", preventDefault() {}});
  await settled();
  const rawParse = n.output.children[1].textContent;
  ok(rawParse.length > 0, "raw JSON parser cause");
  switchLanguage(f, "ja");
  eq(n.output.children[1].textContent, rawParse, "parser error stays literal");
  ok(n.output.textContent.startsWith(shipped.locales.ja["panel.drawing.error.save_prefix"]),
    "save prefix translated");
  scenarios++;
  f.queue.push(async () => response({...original, svg:
    '<svg xmlns="http://www.w3.org/2000/svg"><script>raw unsafe</script></svg>'}));
  await f.controller.refresh(); switchLanguage(f, "en");
  ok(n.output.textContent.includes(shipped.locales.en["panel.drawing.error.svg_node"]), "SVG node refused");
  eq(n.preview.children.length, 0, "unsafe SVG never retained");
  f.queue.push(async () => response({...original, svg:
    '<svg xmlns="http://www.w3.org/2000/svg"><g style="bad"/></svg>'}));
  await f.controller.refresh(); switchLanguage(f, "ja");
  ok(n.output.textContent.includes(shipped.locales.ja["panel.drawing.error.svg_attribute"]), "SVG attribute refused");
  f.queue.push(async () => response({...original, revision: 999}));
  await f.controller.refresh(); switchLanguage(f, "en");
  ok(n.output.textContent.includes(shipped.locales.en["panel.drawing.error.binding"]), "binding refused");
  scenarios++;
  await f.controller.refresh();
  const blob = deferred(), exportHeaders = {
    "X-OpenCAD-Model-SHA256": original.source_model_sha256,
    "X-OpenCAD-Model-Version": "7",
    "X-OpenCAD-Document-SHA256": original.source_document_sha256,
    "X-OpenCAD-Drawing-SHA256": original.drawing_sha256
  };
  f.queue.push(async () => ({ok: true, headers: {get: key => exportHeaders[key]}, blob: async () => blob.promise}));
  n.exports[0].click(); await settled();
  for (const code of ["ja", "en", "zh-CN"]) switchLanguage(f, code);
  const exportBefore = snapshot(f);
  f.window.dispatchEvent({type: "opencad:state", detail: {
    ok: true, project_id: original.project_id, revision: 7, sha256: original.sha256,
    locale: "ja", strings: {"app.title": "raw core"}
  }});
  same(snapshot(f).svg, exportBefore.svg, "state locale keeps download preview identity");
  eq(f.calls, exportBefore.calls, "state locale never queues download refresh");
  const priorDownloads = downloads.length;
  blob.resolve({size: 12}); await settled();
  eq(downloads.length, priorDownloads + 1, "locale preserves export epoch and current identity");
  eq(downloads.at(-1).name, "drawing.svg", "format literal");
  ok(n.output.textContent.includes("SVG"), "localized download status with raw format");
  switchLanguage(f, "en");
  scenarios++;
  f.queue.push(async () => ({ok: true, headers: {get: key =>
    key === "X-OpenCAD-Model-SHA256" ? "f".repeat(64) : exportHeaders[key]},
    blob: async () => { throw new Error("must_not_read_mismatched_blob"); }}));
  const rejectedDownloads = downloads.length;
  n.exports[0].click(); await settled(); switchLanguage(f, "ja");
  eq(downloads.length, rejectedDownloads, "wrong export version still refused");
  ok(n.output.textContent.includes(shipped.locales.ja["panel.drawing.error.export_version"]), "version error localized");
  scenarios++;
  await f.controller.refresh();
  const changingBlob = deferred();
  f.queue.push(async () => ({ok: true, headers: {get: key => exportHeaders[key]},
    blob: async () => changingBlob.promise}));
  n.exports[0].click(); await settled();
  f.window.dispatchEvent({type: "opencad:state",
    detail: {ok: true, locale: "en", strings: {"app.title": "raw core"}}}); switchLanguage(f, "en");
  changingBlob.resolve({size: 12}); await settled(); await settled();
  eq(downloads.length, rejectedDownloads, "model change during blob read refused");
  scenarios++;
  const late = await fixture({late: true});
  eq(late.calls.length, 0, "no drawing request before locale readiness");
  late.locale.update({locale: "ja", strings: {"app.title": "core"}});
  late.load.resolve(); await late.controller.ready; await settled();
  eq(late.calls.length, 1, "one request after readiness");
  eq(late.panel.querySelector("h2").textContent, shipped.locales.ja["panel.drawing.heading"],
    "early accepted locale not overwritten");
  scenarios++;
  const priorCalls = late.calls.length, priorText = late.panel.querySelector("h2").textContent;
  late.controller.destroy(); late.controller.destroy();
  late.locale.update({locale: "en", strings: {"app.title": "core"}});
  late.window.dispatchEvent({type: "opencad:parametric"});
  eq(late.calls.length, priorCalls, "destroy removes CAD listeners");
  eq(late.panel.querySelector("h2").textContent, priorText, "destroy unbinds locale");
  const disposed = await fixture({late: true}); disposed.controller.destroy();
  disposed.load.resolve(); await disposed.controller.ready; await settled();
  eq(disposed.calls.length, 0, "destroy before ready has no fetch or binding");
  scenarios++;
  console.log(JSON.stringify({mode, verdict: "PASS", checks, scenarios,
    contract: contract === "contract", finished_at: new Date().toISOString()}));
}
main().catch(error => {
  console.error(JSON.stringify({mode, verdict: "FAIL", checks, scenarios,
    code: error.code || error.message, detail: error.stack, finished_at: new Date().toISOString()}));
  process.exitCode = 1;
});
"""


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def require(condition, cause):
    if not condition:
        raise RuntimeError(cause)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    require(result.returncode == 0, "git_failed:" + result.stderr.decode(errors="replace"))
    return result.stdout


def source(root, base, owned, development, required=(), restrict_commits=True):
    require(root.is_dir() and not root.is_symlink(), "invalid_source_root")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    git(root, "merge-base", "--is-ancestor", base, head)
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    require(not status or development, "source_not_clean")
    if development:
        require(all(line[3:] in owned for line in status), "development_scope_mismatch")
        if restrict_commits:
            changed = git(root, "diff", "--name-only", base, head).decode().splitlines()
            require(set(changed) <= set(owned), "development_commit_scope_mismatch")
    committed = {}
    for raw in git(root, "ls-tree", "-r", "-z", head).split(b"\0"):
        if raw:
            meta, name = raw.split(b"\t", 1)
            mode, kind, blob = meta.decode().split()
            require(kind == "blob" and mode in ("100644", "100755"), "source_git_type")
            committed[os.fsdecode(name)] = (mode, blob)
    entries = {}
    for name, (mode, blob) in committed.items():
        target = root / name
        require(target.is_file() and not target.is_symlink(), "source_file_type:" + name)
        data = target.read_bytes()
        actual_mode = "100755" if target.stat().st_mode & stat.S_IXUSR else "100644"
        if not development or name not in owned:
            actual_blob = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            require(actual_blob == blob and actual_mode == mode, "source_byte_mode_drift:" + name)
        entries[name] = {"bytes": len(data), "sha256": digest(data), "mode": actual_mode}
    physical = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        if ".git" in dirs:
            require(relative == Path(".") and not (Path(directory) / ".git").is_symlink(),
                    "nested_git_metadata")
            dirs.remove(".git")
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "source_directory_symlink")
        for name in files:
            target = Path(directory) / name
            if name == ".git":
                require(relative == Path(".") and not target.is_symlink(), "nested_git_metadata")
                continue
            require(target.is_file() and not target.is_symlink(), "source_file_type")
            physical.add(target.relative_to(root).as_posix())
    extra = physical - entries.keys()
    require(not extra or (development and extra <= set(owned)), "source_physical_set_mismatch")
    for name in extra:
        target = root / name
        data = target.read_bytes()
        entries[name] = {"bytes": len(data), "sha256": digest(data),
                         "mode": "100755" if target.stat().st_mode & stat.S_IXUSR else "100644"}
    require(physical == entries.keys(), "source_physical_set_mismatch")
    require(set(required) <= entries.keys(), "required_source_missing")
    return {"head": head, "status": status, "development": development, "files": entries,
            "sha256": digest(json.dumps(entries, sort_keys=True).encode())}


def closure(args):
    foundation = args.foundation_root.absolute()
    dictionary = args.dictionary_root.absolute() if args.dictionary_root else foundation
    service = source(foundation, FOUNDATION, (), False, (f"{WEB}/js/locale-service.js",))
    package = service if foundation == dictionary else source(dictionary, FOUNDATION, (
        f"{WEB}/panel-i18n.json", "docs/design/panel-catalog.md",
        "oss-alternatives/apps/opencad/tests/test_panel_catalog.py",
    ), args.dictionary_development, (f"{WEB}/panel-i18n.json",), restrict_commits=False)
    return {"root": source(args.source_root, BASE, OWNED, args.development, OWNED),
            "foundation_root": str(foundation), "foundation": service,
            "dictionary_root": str(dictionary), "dictionary": package,
            "contract": args.contract}


def artifacts(out):
    values = {}
    for target in sorted(out.rglob("*")):
        require(not target.is_symlink(), "artifact_symlink")
        if target.is_file() and target != out / "result.json":
            values[target.relative_to(out).as_posix()] = {
                "bytes": target.stat().st_size, "sha256": digest(target.read_bytes())}
    return values


def strict_json(path):
    def unique(pairs):
        values = {}
        for key, value in pairs:
            require(key not in values, "duplicate_json_key")
            values[key] = value
        return values
    return json.loads(path.read_text(), object_pairs_hook=unique)


def child(args, mode, out):
    command = [shutil.which("node") or "node", str(out / "drawing-fixture.cjs"),
               str(args.source_root), str(args.foundation_root),
               str(args.dictionary_root or args.foundation_root), mode,
               "contract" if args.contract else "shipped"]
    started = stamp()
    result = subprocess.run(command, capture_output=True, check=False)
    (out / f"{mode}.stdout.log").write_bytes(result.stdout)
    (out / f"{mode}.stderr.log").write_bytes(result.stderr)
    raw = result.stdout if result.returncode == 0 else result.stderr
    try:
        terminal = json.loads(raw.decode().splitlines()[-1])
    except (ValueError, IndexError) as error:
        terminal = {"verdict": "FAIL", "code": "child_terminal_missing", "detail": str(error)}
    return {"mode": mode, "argv": command, "actual_rc": result.returncode,
            "started_at": started, "finished_at": stamp(), "terminal": terminal}


def verify(prior, args, evidence, current):
    require(prior["verdict"] == "PASS" and prior["actual_rc"] == 0 and
            prior["first_cause"] is None, "producer_not_pass")
    require(prior["source_root"] == str(args.source_root), "source_root_mismatch")
    require(prior["source_before"] == prior["source_after"] == current, "current_source_drift")
    require(prior["artifacts"] == artifacts(evidence), "artifact_closure_mismatch")
    require((evidence / "drawing-fixture.cjs").read_text() == NODE, "fixture_source_drift")
    records = prior["children"]
    require([item["mode"] for item in records] == ["positive", *NEGATIVES], "child_set_mismatch")
    for item in records:
        mode = item["mode"]
        command = [shutil.which("node") or "node", str(evidence / "drawing-fixture.cjs"),
                   str(args.source_root), str(args.foundation_root),
                   str(args.dictionary_root or args.foundation_root), mode,
                   "contract" if args.contract else "shipped"]
        require(item["argv"] == command, "child_argv_mismatch")
        expected = 0 if mode == "positive" else 1
        require(item["actual_rc"] == expected, "child_rc_mismatch")
        target = evidence / f"{mode}.{'stdout' if expected == 0 else 'stderr'}.log"
        terminal = strict_json_lines(target)
        require(terminal == item["terminal"], "child_log_terminal_mismatch")
        require(terminal["verdict"] == ("PASS" if expected == 0 else "FAIL"), "child_verdict_mismatch")
        if mode == "positive":
            require(terminal["checks"] > 0 and terminal["scenarios"] == 14, "positive_count_missing")
        else:
            require(terminal["code"] == NEGATIVES[mode], "negative_cause_mismatch")
        require(datetime.datetime.fromisoformat(item["started_at"]) <=
                datetime.datetime.fromisoformat(terminal["finished_at"].replace("Z", "+00:00")) <=
                datetime.datetime.fromisoformat(item["finished_at"]),
                "child_timestamp_mismatch")
    require(prior["checks"] == records[0]["terminal"]["checks"] and
            prior["scenarios"] == 14 and prior["negative_controls"] == len(NEGATIVES), "count_mismatch")
    require((evidence / "completion.log").read_text() == marker(prior) + "\n", "completion_marker_mismatch")
    return len(records)


def controls(prior_path, args, out, current):
    """Run actual negative reader CLIs against new copied evidence, never originals."""
    prior = strict_json(prior_path)
    verify(prior, args, prior_path.parent, current)
    mutations = {
        "missing-child": "child_set_mismatch",
        "fake-child-rc": "child_rc_mismatch",
        "fake-count": "count_mismatch",
        "fake-source": "current_source_drift",
        "foreign-root": "source_root_mismatch",
        "extra-artifact": "artifact_closure_mismatch",
        "corrupt-log": "artifact_closure_mismatch",
        "rehashed-terminal": "child_log_terminal_mismatch",
        "rehashed-completion": "completion_marker_mismatch",
    }
    records = []
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
            changed["checks"] += 1
        elif name == "fake-source":
            changed["source_before"]["root"]["sha256"] = "0" * 64
            changed["source_after"] = copy.deepcopy(changed["source_before"])
        elif name == "foreign-root":
            changed["source_root"] = "/foreign/root"
        elif name == "extra-artifact":
            (evidence / "unbound.log").write_text("unbound\n")
        elif name == "corrupt-log":
            (evidence / "positive.stdout.log").write_text("corrupt\n")
        elif name == "rehashed-terminal":
            terminal = copy.deepcopy(changed["children"][0]["terminal"])
            terminal["checks"] += 1
            (evidence / "positive.stdout.log").write_text(json.dumps(terminal) + "\n")
        elif name == "rehashed-completion":
            (evidence / "completion.log").write_text("PASS\n")
        for item in changed["children"]:
            item["argv"][1] = str(evidence / "drawing-fixture.cjs")
        if name in ("rehashed-terminal", "rehashed-completion"):
            changed["artifacts"] = artifacts(evidence)
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        command = [sys.executable, "-B", str(args.source_root / OWNED[1]),
                   "--verify", str(evidence / "result.json"),
                   "--source-root", str(args.source_root),
                   "--foundation-root", str(args.foundation_root),
                   "--dictionary-root", str(args.dictionary_root or args.foundation_root),
                   "--out", str(case / "reader")]
        for flag, value in (("--development", args.development),
                            ("--dictionary-development", args.dictionary_development),
                            ("--contract", args.contract)):
            if value:
                command.append(flag)
        started = stamp()
        result = subprocess.run(command, capture_output=True, check=False)
        (case / "reader.stdout.log").write_bytes(result.stdout)
        (case / "reader.stderr.log").write_bytes(result.stderr)
        terminal = strict_json(case / "reader/result.json")
        records.append({"name": name, "argv": command, "actual_rc": result.returncode,
                        "started_at": started, "finished_at": stamp(),
                        "first_cause": terminal["first_cause"], "expected": cause})
        require(result.returncode == 1 and terminal["first_cause"] == cause,
                "reader_control_failed:" + name + ":" + str(terminal["first_cause"]))
    return records


def strict_json_lines(path):
    return json.loads(path.read_text().splitlines()[-1])


def marker(report):
    return (f"{MARKER} verdict={report['verdict']} checks={report['checks']} "
            f"scenarios={report['scenarios']} negatives={report['negative_controls']} "
            f"contract={report['contract']} rc={report['actual_rc']}")


class DrawingLocaleTests(unittest.TestCase):
    def test_actual_node_source(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        command = [sys.executable, "-B", str(Path(__file__)), "--selftest",
                   "--source-root", str(ROOT), "--out", str(Path(evidence) / "drawing-locale-unittest")]
        foundation = os.environ.get("OPENCAD_LOCALE_FOUNDATION_ROOT")
        dictionary = os.environ.get("OPENCAD_LOCALE_DICTIONARY_ROOT")
        if foundation:
            command += ["--foundation-root", foundation]
        if dictionary:
            command += ["--dictionary-root", dictionary]
        result = subprocess.run(command, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--selftest", action="store_true")
    choice.add_argument("--verify", type=Path)
    choice.add_argument("--controls", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--foundation-root", type=Path,
                        default=Path("/data/dev/wt-opencad-locale-foundation-20261012"))
    parser.add_argument("--dictionary-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--dictionary-development", action="store_true")
    parser.add_argument("--contract", action="store_true")
    args = parser.parse_args()
    args.source_root = args.source_root.absolute()
    args.foundation_root = args.foundation_root.absolute()
    if args.dictionary_root:
        args.dictionary_root = args.dictionary_root.absolute()
    out = args.out.absolute()
    require(out.resolve().is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) and
            not out.exists() and not out.resolve().is_relative_to(args.source_root.resolve()),
            "fresh_external_out_required")
    out.mkdir(parents=True, exist_ok=False)
    report = {"started_at": stamp(), "source_root": str(args.source_root),
              "verdict": "FAIL", "actual_rc": 1, "first_cause": None,
              "contract": args.contract, "children": [], "checks": 0, "scenarios": 0,
              "negative_controls": 0}
    try:
        before = closure(args)
        report["source_before"] = before
        if args.controls:
            report["reader_controls"] = controls(args.controls, args, out, before)
            report["negative_controls"] = len(report["reader_controls"])
        elif args.verify:
            prior = strict_json(args.verify)
            report["verified_children"] = verify(prior, args, args.verify.parent, before)
            report["input"] = {"path": str(args.verify), "sha256": digest(args.verify.read_bytes())}
            report["checks"], report["scenarios"] = prior["checks"], prior["scenarios"]
            report["negative_controls"] = prior["negative_controls"]
        else:
            (out / "drawing-fixture.cjs").write_text(NODE)
            for mode in ("positive", *NEGATIVES):
                item = child(args, mode, out)
                report["children"].append(item)
                expected = 0 if mode == "positive" else 1
                require(item["actual_rc"] == expected, "child_unexpected_rc:" + mode + ":" +
                        str(item["terminal"].get("code")))
                if mode != "positive":
                    require(item["terminal"]["code"] == NEGATIVES[mode], "child_wrong_negative:" + mode)
            report["checks"] = report["children"][0]["terminal"]["checks"]
            report["scenarios"] = report["children"][0]["terminal"]["scenarios"]
            require(report["checks"] > 0 and report["scenarios"] == 14, "positive_count_missing")
            report["negative_controls"] = len(NEGATIVES)
        report["source_after"] = closure(args)
        require(before == report["source_after"], "source_drift")
        report["verdict"], report["actual_rc"] = "PASS", 0
    except Exception as error:
        report["first_cause"] = str(error)
    report["finished_at"] = stamp()
    (out / "completion.log").write_text(marker(report) + "\n")
    report["artifacts"] = artifacts(out)
    (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(marker(report), flush=True)
    if report["first_cause"]:
        print("first_cause=" + report["first_cause"], flush=True)
    return report["actual_rc"]


if __name__ == "__main__":
    sys.exit(main())
