"""Offline full assembly JS + real locale-service contract regression.

The dictionary is read directly from --catalog-root, owned by the chair. No
runtime dictionary is generated; retained copies are byte-for-byte evidence.
Node uses a strict, self-tested DOM/WebGL fixture, not a browser or CAD oracle.
All CLI logs, source snapshots, runtime and negative evidence remain external.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
BASE = "9c4abdac2906111d49f0c39a437ca4cef7abb9b3"
FOUNDATION = "97b80e675f39e23faffcb8c0f6bb8706a8238f0b"
WEB = "oss-alternatives/apps/opencad/web"
OWNED = (f"{WEB}/js/assembly-panel.js",
         "oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py",
         "docs/design/assembly-panel-locale.md")
LOCALE = f"{WEB}/js/locale-service.js"
NEGATIVES = {
    "unknown-key": "locale_missing_key",
    "missing-args": "locale_argument_set_mismatch",
    "extra-args": "locale_argument_set_mismatch",
    "unknown-collision": "locale_missing_key",
    "selector-negative": "fixture_unknown_selector",
    "missing-service": "assembly_locale_service_required",
    "package-failure": "locale_package_http_error",
    "lost-listener": "locale_repaint_missing",
    "rerender": "locale_tree_rebuilt",
}

NODE = r"""
"use strict";
const fs = require("node:fs"), vm = require("node:vm");
const assert = require("node:assert/strict"), path = require("node:path");
const crypto = require("node:crypto");
const [rootPath, localePath, dictionaryPath, mode] = process.argv.slice(2);
const source = fs.readFileSync(path.join(rootPath,
  "oss-alternatives/apps/opencad/web/js/assembly-panel.js"), "utf8");
const localeSource = fs.readFileSync(localePath, "utf8");
const dictionary = JSON.parse(fs.readFileSync(dictionaryPath, "utf8"));
let assertions = 0, sourceRuns = 0;
let fixtureVerified = false;
function check(v, m) { assertions++; assert.ok(v, m); }
function equal(a, b, m) { assertions++; assert.deepEqual(a, b, m); }
function reject(fn, pattern) { assertions++; assert.throws(fn, pattern); }
const normalize = value => JSON.parse(JSON.stringify(value));
class Bus {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, callback, options) {
    const entries = this.listeners.get(name) || [];
    entries.push({callback, once: Boolean(options && options.once)});
    this.listeners.set(name, entries);
  }
  removeEventListener(name, callback) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter(x => x.callback !== callback));
  }
  dispatchEvent(event) {
    event.currentTarget = this; event.target = event.target || this;
    event.preventDefault = event.preventDefault || (() => {});
    for (const item of [...(this.listeners.get(event.type) || [])]) {
      if (item.once) this.removeEventListener(event.type, item.callback);
      item.callback(event);
    }
    return true;
  }
}
const selectors = new Set([
  "[data-label]", "[data-role=\"canvas\"]", "[data-role=\"status\"]",
  "[data-role=\"tree\"]", "[data-role=\"bom\"]", "[data-role=\"collisions\"]",
  "[data-role=\"faces\"]", "[data-role=\"solver\"]", "[data-role=\"metrics\"]",
  "[data-role=\"reopen\"]", "[data-form=pose]", "[data-form=mate]",
  "[data-form=import]", "[data-action]", "input,textarea,button",
  "[data-panel-i18n]", "[data-panel-i18n-aria]", "[data-panel-i18n-placeholder]",
  "[data-panel-i18n-title]", "[data-panel-i18n-alt]"
]);
let replacements = 0, markupWrites = 0, leafWrites = 0;
class Element extends Bus {
  constructor(tag) {
    super(); this.tagName = tag.toUpperCase(); this.attrs = new Map();
    this.children = []; this.parentElement = null; this._text = "";
    this.value = ""; this.disabled = false; this.files = [];
    this.selectionStart = 0; this.selectionEnd = 0; this.scrollTop = 0;
    this.classList = {add: () => {}};
    this.gl = null;
  }
  get dataset() {
    const result = {};
    for (const [name, value] of this.attrs) {
      if (name.startsWith("data-")) result[name.slice(5).replace(/-([a-z])/g,
        (_, c) => c.toUpperCase())] = value;
    }
    return result;
  }
  get elements() {
    const entries = {};
    for (const element of this.querySelectorAll("input,textarea,button")) {
      const name = element.getAttribute("name");
      if (name) entries[name] = element;
    }
    return entries;
  }
  append(...children) {
    for (const child of children) {
      check(child instanceof Element, "fixture_append_element");
      child.parentElement = this; this.children.push(child);
    }
  }
  replaceChildren(...children) {
    replacements++;
    for (const child of this.children) child.parentElement = null;
    this.children = []; this._text = ""; this.append(...children);
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
    if (name === "value") this.value = String(value);
    if (name === "type") this.type = value;
  }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  removeAttribute(name) { this.attrs.delete(name); }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
  set textContent(value) {
    if (this.children.length) throw new Error("fixture_text_destroyed_children");
    leafWrites++; this._text = String(value);
  }
  set innerHTML(html) {
    markupWrites++; this.replaceChildren();
    const stack = [this], tokens = html.match(/<[^>]+>|[^<]+/g) || [];
    for (const token of tokens) {
      if (token.startsWith("</")) {
        const expected = token.slice(2, -1).toUpperCase();
        check(stack.length > 1 && stack.at(-1).tagName === expected, "fixture_html_closing");
        stack.pop();
      } else if (token.startsWith("<")) {
        const parsed = token.match(/^<([a-z][a-z0-9-]*)([\s\S]*?)>$/i);
        check(Boolean(parsed), "fixture_html_tag");
        const element = new Element(parsed[1]);
        for (const match of parsed[2].matchAll(/([a-z][a-z0-9-]*)(?:="([^"]*)")?/gi)) {
          element.setAttribute(match[1], match[2] === undefined ? "" : match[2]);
        }
        stack.at(-1).append(element);
        if (!["INPUT", "IMG", "BR", "HR"].includes(element.tagName)) stack.push(element);
      } else if (token.trim()) {
        stack.at(-1).textContent = token;
      }
    }
    equal(stack.length, 1, "fixture_html_closed");
  }
  matches(selector) {
    if (!selectors.has(selector)) throw new Error("fixture_unknown_selector:" + selector);
    if (selector === "input,textarea,button") {
      return ["INPUT", "TEXTAREA", "BUTTON"].includes(this.tagName);
    }
    const parsed = selector.match(/^\[([a-z0-9-]+)(?:=(?:"([^"]*)"|([a-z]+)))?\]$/);
    check(Boolean(parsed), "fixture_selector_parser");
    return this.hasAttribute(parsed[1]) &&
      (parsed[2] === undefined && parsed[3] === undefined ||
       this.getAttribute(parsed[1]) === (parsed[2] || parsed[3]));
  }
  querySelectorAll(selector) {
    if (!selectors.has(selector)) throw new Error("fixture_unknown_selector:" + selector);
    const result = [];
    function walk(element) {
      for (const child of element.children) {
        if (child.matches(selector)) result.push(child);
        walk(child);
      }
    }
    walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getContext(name) {
    equal(this.tagName, "CANVAS"); equal(name, "webgl"); return this.gl || activeGL;
  }
  getBoundingClientRect() { return {width: 640, height: 320}; }
  setPointerCapture(id) { this.pointer = id; }
  click() { this.dispatchEvent({type: "click"}); }
}
function fixtureSelftest() {
  const root = new Element("section");
  root.innerHTML = '<form data-form="pose"><label><span data-panel-i18n="x"></span>' +
    '<input name="translation_mm" value="[1,2,3]"></label><textarea name="component"></textarea></form>';
  const form = root.querySelector("[data-form=pose]");
  equal(form.elements.translation_mm.value, "[1,2,3]");
  equal(root.querySelectorAll("input,textarea,button").length, 2);
  equal(root.querySelectorAll("[data-panel-i18n]").length, 1);
  equal(root.querySelectorAll("[data-panel-i18n-aria]").length, 0);
  for (const selector of selectors) root.querySelectorAll(selector);
  reject(() => root.querySelectorAll("[data-made-up]"), /fixture_unknown_selector/);
  reject(() => { form.textContent = "bad"; }, /fixture_text_destroyed_children/);
  const element = form.elements.component; element.value = "draft";
  let calls = 0; const cb = () => { calls++; };
  element.addEventListener("submit", cb); element.dispatchEvent({type: "submit"});
  element.removeEventListener("submit", cb); element.dispatchEvent({type: "submit"});
  equal(calls, 1); equal(element.value, "draft");
}
function webgl() {
  const counts = {draw: 0, buffer: 0, clear: 0, deleted: 0};
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4,
    ARRAY_BUFFER: 5, FLOAT: 6, DEPTH_TEST: 7, COLOR_BUFFER_BIT: 8,
    DEPTH_BUFFER_BIT: 16, STATIC_DRAW: 17, TRIANGLES: 18,
    createShader: () => ({}), shaderSource: () => {}, compileShader: () => {},
    getShaderParameter: () => true, getShaderInfoLog: () => "shader_raw",
    createProgram: () => ({}), attachShader: () => {}, linkProgram: () => {},
    getProgramParameter: () => true, getProgramInfoLog: () => "link_raw",
    useProgram: () => {}, createBuffer: () => ({}), bindBuffer: () => {},
    getAttribLocation: () => 0, enableVertexAttribArray: () => {},
    vertexAttribPointer: () => {}, enable: () => {}, viewport: () => {},
    clearColor: () => {}, clear: () => { counts.clear++; },
    uniform1f: () => {}, getUniformLocation: () => 0, uniform3fv: () => {},
    bufferData: () => { counts.buffer++; }, drawArrays: () => { counts.draw++; },
    deleteBuffer: () => { counts.deleted++; }, deleteProgram: () => { counts.deleted++; }
  };
  return {gl, counts};
}
let activeGL;
function fixture(options = {}) {
  const window = new Bus(), document = new Bus(), body = new Element("body");
  const requests = [], localeRequests = [], observers = [], graphics = webgl();
  activeGL = options.noGL ? null : graphics.gl;
  document.body = body;
  document.currentScript = {src: "http://fixture/web/js/locale-service.js"};
  document.createElement = tag => new Element(tag);
  document.querySelectorAll = selector => {
    if (selector !== "#lang-select option, #locale option") {
      throw new Error("fixture_unknown_document_selector:" + selector);
    }
    return ["zh-CN", "en", "ja"].map(code => ({value: code}));
  };
  let releaseLocale;
  const held = new Promise(resolve => { releaseLocale = resolve; });
  window.fetch = async (url, init) => {
    localeRequests.push({url, init});
    if (options.defer) await held;
    return {ok: !options.packageFailure, text: async () => JSON.stringify(dictionary)};
  };
  const context = vm.createContext({window, document, HTMLElement: Element,
    URL, Blob, TextEncoder, crypto: crypto.webcrypto, devicePixelRatio: 1,
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
      observe(element) { this.element = element; }
      disconnect() { this.disconnected = true; }
    },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    console});
  if (!options.noService) vm.runInContext(localeSource, context, {filename: localePath});
  vm.runInContext(source, context, {filename: path.join(rootPath, "assembly-panel.js")});
  sourceRuns++;
  check(typeof window.opencadAssembly.mount === "function", "full_real_js_export");
  const root = new Element("section"); body.append(root);
  return {window, document, root, service: window.OpenCADLocale, requests,
    localeRequests, observers, graphics, releaseLocale};
}
const hash = text => crypto.createHash("sha256").update(text).digest("hex");
function snapshot(revision = 1, present = true) {
  const rotation = [[1,0,0],[0,1,0],[0,0,1]];
  const component = (id, part) => ({
    component_id: id, part_id: part, configuration: "default", parent_id: null,
    fixed: true, transform: {rotation, translation_nm: [0,0,0]}
  });
  const assembly = present ? {
    parts: [{part_id: "part-原文", configuration: "default", document_sha256: "a".repeat(64)}],
    components: [component("group-raw", null), component("part-instance", "part-原文")],
    mates: [{mate_id: "raw-mate", kind: "axis"}]
  } : null;
  const assembly_json = present ? JSON.stringify(assembly) : null;
  const doc = JSON.stringify(present ? {industrial_assembly_v1: assembly} : {});
  const document_sha256 = hash(doc), assembly_sha256 = present ? hash(assembly_json) : null;
  return {ok: true, present, revision, project_id: "private-project",
    sha256: hash("revision:" + revision), doc, document_sha256, assembly, assembly_json,
    assembly_sha256, bom: [{part_id: "part-原文", label: "BOM raw 原文", count: 1}],
    solver: {status: "underconstrained", dof: 3, iterations: 1, jacobian_rank: 2,
      residuals: [{mate_id: "raw-mate", distance_nm: 123}]},
    evaluation: present ? {assembly_sha256, document_sha256, backend: "FreeCAD-BRep",
      solid_count: 1, properties: {volume_nm3: 123456, bbox_nm: [0,0,0,1000000,1000000,1000000]},
      collisions: ["separated", "touching", "penetrating"].map(status =>
        ({a: "a-raw", b: "b-原文", status, distance_nm: 7, interference_volume_nm3: 8})),
      instances: [{component_id: "part-instance", part_id: "part-原文", configuration: "default",
        document_sha256: "a".repeat(64), faces: [{kind: "plane", normal: [0,0,1]}],
        mesh: {vertices_mm: [[0,0,0],[1,0,0],[0,1,0]], triangles: [[0,1,2]]}}]
    } : null};
}
const role = (f, name) => f.root.querySelector('[data-role="' + name + '"]');
function transport(f, options = {}) {
  let revision = 1;
  return async request => {
    f.requests.push(normalize(request));
    if (options.hold) return options.hold;
    if (options.error) throw new Error(options.error);
    if (request.path.endsWith("action=export")) {
      const value = snapshot(revision); return value;
    }
    if (request.method === "POST") revision++;
    return snapshot(revision, options.present !== false);
  };
}
async function mounted(options = {}) {
  const f = fixture(options);
  await f.service.ready;
  f.service.update({locale: "en", strings: {"app.title": "Core"}});
  f.panel = f.window.opencadAssembly.mount(f.root,
    {transport: transport(f, options), labels: {title: "FORBIDDEN OVERRIDE"}});
  await f.panel.ready;
  return f;
}
function translated(f, suffix, args) { return f.service.t("panel.assembly." + suffix, args); }
function durable(f) {
  return {state: f.panel.state, json: JSON.stringify(f.panel.state), error: f.panel.error,
    triangles: f.panel.triangles, busy: f.panel.busy,
    children: [...role(f, "tree").children], buttons: role(f, "tree").children.map(x => x.children[0]),
    inputs: f.root.querySelectorAll("input,textarea,button").map(x => ({
      node: x, value: x.value, disabled: x.disabled, start: x.selectionStart,
      end: x.selectionEnd, scroll: x.scrollTop, files: x.files})),
    focus: f.document.activeElement, replacements, markupWrites,
    graphics: {...f.graphics.counts}, requests: JSON.stringify(f.requests),
    faces: role(f, "faces").textContent, bom: role(f, "bom").textContent,
    solver: role(f, "solver").textContent};
}
function unchanged(before, f) { equal(durable(f), before, "locale_must_only_paint"); }
function update(f, code) { f.service.update({locale: code, strings: {"app.title": "Core"}}); }
async function settle(f) {
  for (let i = 0; i < 100; i++) {
    await new Promise(resolve => setImmediate(resolve));
    if (!f.panel.busy) return;
  }
  throw new Error("fixture_queue_did_not_settle");
}
async function main() {
  fixtureSelftest();
  fixtureVerified = true;
  if (mode === "selector-negative") new Element("div").querySelectorAll("[data-invented]");
  if (mode === "missing-service") {
    const f = fixture({noService: true});
    f.window.opencadAssembly.mount(f.root, {transport: transport(f)});
    throw new Error("missing_service_accepted");
  }
  if (mode === "package-failure") {
    const f = fixture({packageFailure: true});
    f.service.ready.catch(() => {});
    const api = f.window.opencadAssembly.mount(f.root, {transport: transport(f)});
    await api.ready;
    throw new Error("package_failure_accepted");
  }
  const f = await mounted();
  if (mode === "unknown-key") f.service.t("panel.assembly.not_registered");
  if (mode === "missing-args") f.service.t("panel.assembly.current_revision");
  if (mode === "extra-args") f.service.t("panel.assembly.ready", {extra: 1});
  if (mode === "unknown-collision") {
    const bad = snapshot(2); bad.evaluation.collisions[0].status = "unknown_engine_status";
    f.panel = f.window.opencadAssembly.mount(new Element("section"), {
      transport: async () => bad});
    await f.panel.ready;
    throw new Error("unknown_collision_accepted");
  }
  if (mode === "lost-listener") {
    f.window.listeners.set("opencad:locale", []);
    update(f, "ja");
    check(role(f, "collisions").textContent.includes(translated(f, "touching")), "locale_repaint_missing");
    throw new Error("listener_control_not_detected");
  }
  if (mode === "rerender") {
    f.window.addEventListener("opencad:locale", () => { role(f, "tree").replaceChildren(); });
    const before = replacements; update(f, "ja");
    equal(replacements, before, "locale_tree_rebuilt");
    throw new Error("rerender_control_not_detected");
  }
  check(mode === "positive", "fixture_unknown_mode:" + mode);
  const title = f.root.children[0];
  equal(title.textContent, translated(f, "title"), "before_red_title_shared_locale");
  check(!source.includes("const defaults") && !source.includes("options.labels"), "no_local_defaults");
  equal(role(f, "canvas").getAttribute("aria-label"), translated(f, "preview"));
  equal(f.panel.triangles, 1, "mesh_path_executed");
  equal(role(f, "tree").children.length, 2);
  f.panel.select("part-instance");
  const pose = f.root.querySelector("[data-form=pose]");
  const mate = f.root.querySelector("[data-form=mate]");
  pose.elements.component.value = '{"draft":"pose kept"}';
  pose.elements.translation_mm.value = "[9,8,7]";
  mate.elements.mate.value = '{"draft":"mate kept"}';
  pose.elements.component.selectionStart = 2; pose.elements.component.selectionEnd = 8;
  pose.elements.component.scrollTop = 31; f.document.activeElement = pose.elements.component;
  const before = durable(f);
  for (const code of ["zh-CN", "ja", "en", "zh-CN"]) {
    update(f, code); unchanged(before, f);
    equal(title.textContent, translated(f, "title"));
    equal(role(f, "canvas").getAttribute("aria-label"), translated(f, "preview"));
    for (const control of f.root.querySelectorAll("[data-panel-i18n-aria]")) {
      equal(control.getAttribute("aria-label"), f.service.t(
        control.getAttribute("data-panel-i18n-aria")), "all_aria_bindings");
    }
    equal(role(f, "status").textContent, translated(f, "current_revision", {revision: 1}));
    equal(role(f, "metrics").textContent, translated(f, "metrics",
      {backend: "FreeCAD-BRep", solids: 1, volume: 123456}));
    check(role(f, "tree").textContent.includes(translated(f, "group")), "group_translated");
    check(role(f, "tree").textContent.includes("part-原文"), "part_data_not_translated");
    for (const status of ["separated", "touching", "penetrating"]) {
      check(role(f, "collisions").textContent.includes(translated(f, status)), "collision:" + status);
    }
    check(role(f, "collisions").textContent.includes("distance_nm=7 interference_nm3=8"), "raw_units");
    for (const leaf of f.root.querySelectorAll("[data-panel-i18n]")) {
      const raw = leaf.getAttribute("data-panel-i18n-args");
      equal(leaf.textContent, f.service.t(leaf.getAttribute("data-panel-i18n"),
        raw === null ? undefined : JSON.parse(raw)), "all_bound_labels");
    }
  }
  // Selection is proved by refresh preserving the selected part, not a private test getter.
  await f.panel.refresh();
  equal(JSON.parse(pose.elements.component.value).component_id, "part-instance");
  equal(f.requests.length, 2);
  const command = {action: "put_mate", mate: {mate_id: "immutable-request", kind: "axis"}};
  const committing = f.panel.command(command); command.mate.mate_id = "late-mutation";
  await committing;
  equal(f.requests.at(-1).body.mate.mate_id, "immutable-request");
  equal(f.panel.state.revision, 2);
  update(f, "ja"); equal(role(f, "status").textContent, translated(f, "current_revision", {revision: 2}));
  const countBeforePose = f.requests.length;
  pose.elements.translation_mm.value = "[1.125,-2,0]";
  pose.dispatchEvent({type: "submit"}); await settle(f);
  equal(f.requests.length, countBeforePose + 1, "real_pose_submit");
  equal(f.requests.at(-1).body.component.transform.translation_nm, [1125000,-2000000,0]);
  mate.elements.mate.value = '{"mate_id":"raw-mate","kind":"axis","offset_nm":7}';
  mate.dispatchEvent({type: "submit"}); await settle(f);
  equal(f.requests.at(-1).body, {
    action: "put_mate", mate: {mate_id: "raw-mate", kind: "axis", offset_nm: 7}});
  const actions = f.root.querySelectorAll("[data-action]");
  actions.find(button => button.dataset.action === "solve").click(); await settle(f);
  equal(f.requests.at(-1).body, {action: "solve"});
  actions.find(button => button.dataset.action === "checkpoint").click(); await settle(f);
  equal(f.requests.at(-1).body, {action: "checkpoint"});
  const stateBeforeDownload = f.panel.state;
  actions.find(button => button.dataset.action === "download").click(); await settle(f);
  equal(f.requests.at(-1), {method: "GET", path: "/api/assembly?action=export"});
  equal(f.panel.state, stateBeforeDownload, "download_does_not_commit");
  equal(role(f, "status").textContent, translated(f, "ready"));
  update(f, "en"); equal(role(f, "status").textContent, translated(f, "ready"));
  const dragBefore = durable(f);
  role(f, "canvas").dispatchEvent({type: "pointerdown", clientX: 1, clientY: 2, pointerId: 3});
  update(f, "ja"); unchanged(dragBefore, f);
  role(f, "canvas").dispatchEvent({type: "pointermove", clientX: 31, clientY: 42});
  check(f.graphics.counts.draw > dragBefore.graphics.draw, "drag_still_draws_after_locale");
  role(f, "canvas").dispatchEvent({type: "pointerup"});
  // In-flight transport and disabled controls are unaffected by language events.
  let release; const held = new Promise(resolve => { release = resolve; });
  const busyF = await mounted();
  const queuedF = fixture(); await queuedF.service.ready;
  queuedF.panel = queuedF.window.opencadAssembly.mount(queuedF.root, {
    transport: transport(queuedF, {hold: held})});
  await new Promise(resolve => setImmediate(resolve));
  const busyBefore = durable(queuedF); update(queuedF, "ja"); unchanged(busyBefore, queuedF);
  equal(role(queuedF, "status").textContent, translated(queuedF, "busy"));
  release(snapshot()); await queuedF.panel.ready;
  const rawError = fixture(); await rawError.service.ready;
  rawError.panel = rawError.window.opencadAssembly.mount(rawError.root, {
    transport: transport(rawError, {error: "assembly_snapshot_conflict raw SHA=abc"})});
  await assert.rejects(rawError.panel.ready, /assembly_snapshot_conflict/); assertions++;
  const errorBefore = durable(rawError);
  update(rawError, "zh-CN"); unchanged(errorBefore, rawError);
  equal(role(rawError, "status").textContent,
    translated(rawError, "error_detail", {detail: rawError.panel.error}));
  equal(rawError.panel.error, "assembly_snapshot_conflict raw SHA=abc");
  const importForm = f.root.querySelector("[data-form=import]");
  importForm.dispatchEvent({type: "submit"}); await settle(f);
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "part_file_required", {limit: 2})}));
  const diagnostic = f.panel.error; update(f, "en");
  equal(f.panel.error, diagnostic, "diagnostic_not_rewritten");
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "part_file_required", {limit: 2})}));
  const validPartText = '{"part":"L1 原文"}';
  const validPart = {size: validPartText.length, text: async () => validPartText};
  importForm.elements.file.files = [validPart];
  importForm.elements.part_id.value = "part-not-translated";
  importForm.elements.component_id.value = "component-not-translated";
  importForm.dispatchEvent({type: "submit"}); await settle(f);
  equal(f.requests.at(-1).body.part.l1_text, validPartText);
  equal(f.requests.at(-1).body.part.document_sha256, hash(validPartText));
  equal(f.requests.at(-1).body.component.component_id, "component-not-translated");
  importForm.elements.file.files = [{size: 2 * 1024 * 1024 + 1}];
  importForm.dispatchEvent({type: "submit"}); await settle(f);
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "part_file_required", {limit: 2})}));
  role(f, "reopen").dispatchEvent({type: "change"}); await settle(f);
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "assembly_file_required", {limit: 8})}));
  const reopenText = snapshot().assembly_json;
  role(f, "reopen").files = [{size: reopenText.length, text: async () => reopenText}];
  role(f, "reopen").dispatchEvent({type: "change"}); await settle(f);
  equal(f.requests.at(-1).body, {action: "reopen", assembly_json: reopenText});
  role(f, "reopen").files = [{size: 8 * 1024 * 1024 + 1}];
  role(f, "reopen").dispatchEvent({type: "change"}); await settle(f);
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "assembly_file_required", {limit: 8})}));
  pose.elements.component.value = "{bad-json";
  pose.dispatchEvent({type: "submit"}); await settle(f);
  check(role(f, "status").textContent.includes(translated(f, "json_invalid",
    {detail: f.panel.error})), "json_localized_raw_cause");
  pose.elements.component.value = JSON.stringify(snapshot().assembly.components[1]);
  pose.elements.translation_mm.value = "[1,2]";
  pose.dispatchEvent({type: "submit"}); await settle(f);
  equal(role(f, "status").textContent, translated(f, "error_detail", {
    detail: translated(f, "translation_invalid")}));
  reject(() => f.panel.select("unknown-<img>"), /unknown-<img>/);
  equal(normalize(f.window.opencadAssembly.nmVector("[1.125,-2,0]")), [1125000,-2000000,0]);
  for (const bad of ["null", "[]", "[1,2]", "[1,2,3,4]", "[1e30,0,0]", '["1",0,0]']) {
    reject(() => f.window.opencadAssembly.nmVector(bad), /translation requires/);
  }
  const empty = await mounted({present: false});
  update(empty, "zh-CN"); equal(role(empty, "metrics").textContent, translated(empty, "empty"));
  equal(empty.panel.state.evaluation, null);
  const lazy = fixture({defer: true});
  lazy.panel = lazy.window.opencadAssembly.mount(lazy.root, {transport: transport(lazy)});
  await new Promise(resolve => setImmediate(resolve));
  equal(lazy.requests.length, 0, "wait_locale_before_transport");
  update(lazy, "ja"); lazy.releaseLocale(); await lazy.panel.ready;
  equal(lazy.root.children[0].textContent, translated(lazy, "title"));
  const destroyed = fixture({defer: true});
  destroyed.panel = destroyed.window.opencadAssembly.mount(destroyed.root,
    {transport: transport(destroyed)});
  destroyed.panel.destroy(); destroyed.releaseLocale();
  await assert.rejects(destroyed.panel.ready, /assembly_panel_destroyed/); assertions++;
  equal(destroyed.requests.length, 0); equal(destroyed.root.children.length, 0);
  const noGL = fixture({noGL: true}); await noGL.service.ready;
  reject(() => noGL.window.opencadAssembly.mount(noGL.root, {transport: transport(noGL)}),
    /WebGL/);
  // Existing engineering digest/CAS gates still reject unchanged, regardless of locale.
  for (const [field, cause] of [
    ["document_sha256", "assembly_document_digest_mismatch"],
    ["assembly_sha256", "assembly_digest_mismatch"]]) {
    const mismatch = fixture(); await mismatch.service.ready;
    const bad = snapshot(); bad[field] = "0".repeat(64);
    mismatch.panel = mismatch.window.opencadAssembly.mount(mismatch.root, {transport: async () => bad});
    await assert.rejects(mismatch.panel.ready, new RegExp(cause)); assertions++;
    equal(mismatch.panel.state, null);
  }
  const rootCount = f.window.listeners.get("opencad:locale").length;
  f.panel.destroy(); check(f.observers[0].disconnected, "observer_disconnected");
  equal(f.window.listeners.get("opencad:locale").length, rootCount - 1);
  equal(f.root.children.length, 0); update(f, "ja"); equal(f.root.children.length, 0);
  busyF.panel.destroy();
  console.log(JSON.stringify({verdict: "PASS", mode, assertions, sourceRuns,
    sourcePaths: {assembly: path.join(rootPath,
      "oss-alternatives/apps/opencad/web/js/assembly-panel.js"), locale: localePath,
      dictionary: dictionaryPath},
    sourceHashes: {assembly: hash(source), locale: hash(localeSource),
      dictionary: hash(fs.readFileSync(dictionaryPath))},
    fixtureVerified, selectors: selectors.size, boundKeys: Object.keys(dictionary.locales.en)
      .filter(key => key.startsWith("panel.assembly.")).length}));
}
main().catch(error => {
  console.error(JSON.stringify({verdict: "FAIL", mode, assertions, sourceRuns,
    fixtureVerified, code: error.code || null, error: String(error.stack || error)}));
  process.exitCode = fixtureVerified ? 1 : 2;
});
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(value, cause):
    if not value:
        raise AssertionError(cause)


def command(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    require(proc.returncode == 0, f"git_failed:{args}:{proc.returncode}:{proc.stderr.decode()}")
    return proc.stdout


def manifest(root):
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name != ".git"]
        for name in dirs:
            require(name != ".git" and not (Path(directory) / name).is_symlink(),
                    "source_directory_not_regular")
        for name in files:
            path = Path(directory) / name
            if path == root / ".git":
                continue
            require(path.is_file() and not path.is_symlink(), "source_not_regular")
            entries[path.relative_to(root).as_posix()] = {
                "sha256": sha(path.read_bytes()), "bytes": path.stat().st_size,
                "mode": path.stat().st_mode & 0o777}
    require(entries and all(name in entries for name in OWNED), "source_empty_or_owned_missing")
    return dict(sorted(entries.items()))


def source(root):
    head = command(root, "rev-parse", "HEAD").decode().strip()
    command(root, "merge-base", "--is-ancestor", BASE, head)
    status = command(root, "status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    require(all(line[3:] in OWNED for line in status), "foreign_worktree_changes")
    files = manifest(root)
    tracked = command(root, "ls-files").decode().splitlines()
    require(set(files) - set(tracked) <= set(OWNED), "foreign_physical_source")
    for name in tracked:
        if name not in OWNED:
            require(sha(command(root, "show", f"{head}:{name}")) == files[name]["sha256"],
                    "nonowned_source_drift:" + name)
    return {"head": head, "status": status, "files": files,
            "sha256": sha(json.dumps(files, sort_keys=True).encode())}


def scan_owned(root):
    spec = importlib.util.spec_from_file_location("assembly_source_scan",
                                                  root / "tools/bootstrap_from_infra.py")
    require(spec and spec.loader, "source_scan_loader_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in OWNED:
        module.scan_bytes(name, (root / name).read_bytes())
    return {"paths": list(OWNED), "credential_matches": 0}


def keymap_check(root, payload):
    rows = []
    for line in (root / OWNED[2]).read_text().splitlines():
        if not line.startswith("| ") or line.startswith("| Key") or line.startswith("| ---"):
            continue
        cells = [cell.strip().replace(r"\|", "|")
                 for cell in re.split(r"(?<!\\)\|", line.strip("|"))]
        require(len(cells) == 4, "keymap_columns")
        suffix, zh, en, ja = cells
        args = sorted(set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", en)))
        for text in (zh, en, ja):
            require(sorted(set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", text))) == args,
                    "keymap_arg_set:" + suffix)
        key = f"panel.assembly.{suffix}"
        for code in ("zh-CN", "en", "ja"):
            require(key in payload["locales"][code], "catalog_key_missing:" + code + ":" + key)
            require(sorted(set(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}",
                                          payload["locales"][code][key]))) == args,
                    "catalog_key_args_mismatch:" + code + ":" + key)
        rows.append((key, zh, en, ja))
    require(rows and len({row[0] for row in rows}) == len(rows), "keymap_empty_or_duplicate")
    return len(rows)


def bindings(root, locale_root, catalog_root):
    locale = locale_root / LOCALE
    head = command(locale_root, "rev-parse", "HEAD").decode().strip()
    command(locale_root, "merge-base", "--is-ancestor", FOUNDATION, head)
    require(locale.read_bytes() == command(locale_root, "show", f"{head}:{LOCALE}"),
            "foundation_source_drift")
    result = {"locale_path": str(locale), "locale_sha256": sha(locale.read_bytes()),
              "foundation_commit": FOUNDATION, "foundation_head": head,
              "dictionary_kind": "chair-working-catalog"}
    catalog = catalog_root / WEB / "panel-i18n.json"
    result.update(catalog_path=str(catalog), catalog_sha256=sha(catalog.read_bytes()))
    return result


def inventory(out):
    return {path.relative_to(out).as_posix(): {"sha256": sha(path.read_bytes()),
                                             "bytes": path.stat().st_size}
            for path in sorted(out.rglob("*")) if path.is_file() and path != out / "result.json"}


def write_json(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_node(root, bound, out, mode):
    argv = [shutil.which("node") or "node", str(out / "fixture.cjs"), str(root),
            bound["locale_path"], bound["catalog_path"], mode]
    start = now()
    proc = subprocess.run(argv, capture_output=True)
    for label, data in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        (out / f"{mode}.{label}.log").write_bytes(data)
    stream = proc.stdout if proc.returncode == 0 else proc.stderr
    terminal = json.loads(stream.decode().splitlines()[-1])
    return {"mode": mode, "argv": argv, "started_at": start, "finished_at": now(),
            "actual_rc": proc.returncode, "terminal": terminal}


def validate_child(record):
    mode = record["mode"]
    require(record["terminal"]["mode"] == mode and record["terminal"]["assertions"] > 0,
            "child_missing_assertions")
    require(record["terminal"]["fixtureVerified"], "fixture_not_verified")
    if mode == "positive":
        require(record["actual_rc"] == 0 and record["terminal"]["verdict"] == "PASS",
                "positive_failed:" + record["terminal"].get("error", ""))
        require(record["terminal"]["sourceRuns"] >= 8 and record["terminal"]["selectors"] > 10,
                "full_js_or_fixture_not_exercised")
    else:
        require(record["actual_rc"] == 1 and record["terminal"]["verdict"] == "FAIL",
                "negative_not_failed:" + mode)
        text = str(record["terminal"].get("code")) + ":" + record["terminal"].get("error", "")
        require(NEGATIVES[mode] in text, "wrong_negative_cause:" + mode + ":" + text)


def readback(path, root, bound):
    prior = json.loads(path.read_text())
    require(prior["verdict"] == "PASS" and prior["actual_rc"] == 0, "prior_not_pass")
    require(prior["source_before"] == prior["source_after"] == source(root), "source_readback_drift")
    require(prior["bindings"] == bound, "dependency_readback_drift")
    require(prior["artifacts"] == inventory(path.parent), "artifact_readback_drift")
    require((path.parent / "fixture.cjs").read_text() == NODE, "fixture_readback_drift")
    require((path.parent / "assembly-panel.source.js").read_bytes() ==
            (root / OWNED[0]).read_bytes(), "assembly_snapshot_drift")
    require((path.parent / "locale-service.source.js").read_bytes() ==
            Path(bound["locale_path"]).read_bytes(), "locale_snapshot_drift")
    require(sha((path.parent / "dictionary.json").read_bytes()) ==
            bound["catalog_sha256"], "shipped_dictionary_drift")
    require([record["mode"] for record in prior["children"]] == ["positive", *NEGATIVES],
            "child_set_readback_mismatch")
    for record in prior["children"]:
        mode = record["mode"]
        require(record["argv"] == [shutil.which("node") or "node", str(path.parent / "fixture.cjs"),
                str(root), bound["locale_path"], bound["catalog_path"], mode],
                "child_argv_readback_mismatch")
        validate_child(record)
        if mode == "positive":
            require(record["terminal"]["sourcePaths"] == {
                "assembly": str(root / OWNED[0]), "locale": bound["locale_path"],
                "dictionary": bound["catalog_path"]}, "node_read_path_mismatch")
            require(record["terminal"]["sourceHashes"] == {
                "assembly": prior["source_before"]["files"][OWNED[0]]["sha256"],
                "locale": bound["locale_sha256"], "dictionary": bound["catalog_sha256"]},
                "node_read_hash_mismatch")
        stream = "stdout" if record["actual_rc"] == 0 else "stderr"
        terminal = json.loads((path.parent / f"{mode}.{stream}.log").read_text().splitlines()[-1])
        require(terminal == record["terminal"], "terminal_readback_mismatch")
    require((path.parent / "completion.log").read_text() ==
            f"ASSEMBLY_LOCALE verdict=PASS children={1 + len(NEGATIVES)} "
            "verified=0 reader_negatives=0 rc=0\n",
            "completion_readback_mismatch")
    return prior


def reader_controls(path, root, locale_root, catalog_root, bound, out):
    """Run actual failing reader CLIs against copies, never mutate original evidence."""
    prior = readback(path, root, bound)
    mutations = {
        "missing-child": "child_set_readback_mismatch",
        "fake-rc": "negative_not_failed",
        "source-sha": "source_readback_drift",
        "corrupt-log": "artifact_readback_drift",
        "extra-file": "artifact_readback_drift",
        "rehashed-terminal": "terminal_readback_mismatch",
        "rehashed-completion": "completion_readback_mismatch",
    }
    records = []
    for name, expected in mutations.items():
        case = out / name
        case.mkdir()
        evidence = case / "evidence"
        shutil.copytree(path.parent, evidence)
        changed = copy.deepcopy(prior)
        for record in changed["children"]:
            record["argv"][1] = str(evidence / "fixture.cjs")
        if name == "missing-child":
            changed["children"].pop()
        elif name == "fake-rc":
            changed["children"][1]["actual_rc"] = 0
        elif name == "source-sha":
            changed["source_before"]["sha256"] = "0" * 64
            changed["source_after"] = copy.deepcopy(changed["source_before"])
        elif name == "corrupt-log":
            (evidence / "positive.stdout.log").write_text("corrupt\n")
        elif name == "extra-file":
            (evidence / "extra.log").write_text("unbound\n")
        elif name == "rehashed-terminal":
            terminal = copy.deepcopy(changed["children"][0]["terminal"])
            terminal["assertions"] += 1
            (evidence / "positive.stdout.log").write_text(json.dumps(terminal) + "\n")
        elif name == "rehashed-completion":
            (evidence / "completion.log").write_text("PASS\n")
        if name.startswith("rehashed"):
            changed["artifacts"] = inventory(evidence)
        (evidence / "result.json").write_text(json.dumps(changed, indent=2) + "\n")
        argv = [sys.executable, "-B", str(root / OWNED[1]), "--verify",
                str(evidence / "result.json"), "--source-root", str(root),
                "--locale-root", str(locale_root), "--out", str(case / "reader")]
        if catalog_root:
            argv.extend(["--catalog-root", str(catalog_root)])
        start = now()
        proc = subprocess.run(argv, capture_output=True)
        (case / "reader.stdout.log").write_bytes(proc.stdout)
        (case / "reader.stderr.log").write_bytes(proc.stderr)
        result = json.loads((case / "reader/result.json").read_text())
        records.append({"name": name, "actual_rc": proc.returncode, "argv": argv,
                        "started_at": start, "finished_at": now(),
                        "first_cause": result["first_cause"], "expected": expected})
        require(proc.returncode == 1 and expected in result["first_cause"],
                "reader_negative_failed:" + name + ":" + str(result["first_cause"]))
    return records


class AssemblyLocaleTest(unittest.TestCase):
    def test_full_js_locale_contract(self):
        evidence = os.environ.get("OPENCAD_EVIDENCE_ROOT")
        require(evidence, "OPENCAD_EVIDENCE_ROOT_required")
        out = Path(evidence) / "assembly-locale-unittest"
        argv = [sys.executable, "-B", str(ROOT / OWNED[1]), "--selftest",
                "--source-root", str(ROOT), "--out", str(out),
                "--locale-root", os.environ.get("OPENCAD_ASSEMBLY_LOCALE_ROOT",
                    "/data/dev/wt-opencad-locale-foundation-20261012"),
                "--catalog-root", os.environ.get("OPENCAD_ASSEMBLY_CATALOG_ROOT",
                    "/data/dev/wt-opencad-panel-catalog-r2-20261012")]
        proc = subprocess.run(argv, capture_output=True)
        if proc.returncode:
            self.fail(proc.stdout.decode() + proc.stderr.decode())
        report = json.loads((out / "result.json").read_text())
        self.assertEqual(report["actual_rc"], 0)
        self.assertEqual(len(report["children"]), 1 + len(NEGATIVES))


def regression(root, out, locale_root, catalog_root):
    """Invoke the repository's independent acceptance entry on this exact test."""
    argv = [sys.executable, "-B", str(root / "tools/acceptance.py"),
            "--suite", str(root / "oss-alternatives/apps/opencad/tests"),
            "--top", str(root / "oss-alternatives/apps/opencad"),
            "--pattern", "test_assembly_panel_locale.py"]
    runtime = Path("/data/tmp") / ("oc-al-" + datetime.now(timezone.utc).strftime("%H%M%S%f"))
    runtime.mkdir(exist_ok=False)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENCAD_EVIDENCE_ROOT=str(out),
               TMPDIR=str(runtime), TMP=str(runtime), TEMP=str(runtime),
               OPENCAD_TEST_RUNTIME_ROOT=str(runtime),
               OPENCAD_ASSEMBLY_LOCALE_ROOT=str(locale_root),
               OPENCAD_ASSEMBLY_CATALOG_ROOT=str(catalog_root))
    start = now()
    proc = subprocess.run(argv, env=env, capture_output=True)
    (out / "unittest.stdout.log").write_bytes(proc.stdout)
    (out / "unittest.stderr.log").write_bytes(proc.stderr)
    record = {"argv": argv, "actual_rc": proc.returncode, "runtime": str(runtime),
              "started_at": start, "finished_at": now()}
    require(proc.returncode == 0, "regression_failed:" + proc.stdout.decode() + proc.stderr.decode())
    lines = [line.removeprefix("INDEPENDENT_TEST_COUNTS ")
             for line in proc.stdout.decode().splitlines()
             if line.startswith("INDEPENDENT_TEST_COUNTS ")]
    require(len(lines) == 1, "regression_count_missing")
    counts = json.loads(lines[0])
    require(counts["tests"] == counts["passed"] == 1 and not counts["skipped"] and
            counts["errors"] == counts["failures"] == 0, "regression_count_mismatch")
    record["counts"] = counts
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--selftest", action="store_true")
    group.add_argument("--verify", type=Path)
    group.add_argument("--controls", type=Path)
    group.add_argument("--regression", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--locale-root", type=Path,
                        default=Path("/data/dev/wt-opencad-locale-foundation-20261012"))
    parser.add_argument("--catalog-root", type=Path,
                        default=Path("/data/dev/wt-opencad-panel-catalog-r2-20261012"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root, out = args.source_root.absolute(), args.out.absolute()
    require(out.is_relative_to(Path("/hf3fs-jd/data/tmp")) and not out.exists() and
            not out.is_relative_to(root), "fresh_external_evidence_required")
    out.mkdir(parents=True, exist_ok=False)
    report = {"verdict": "FAIL", "actual_rc": 1, "started_at": now(),
              "first_cause": None, "children": [], "source_root": str(root),
              "browser": "NOT_RUN", "industrial_parity": "NOT_ASSESSED"}
    rc = 1
    try:
        report["source_before"] = source(root)
        report["source_scan"] = scan_owned(root)
        bound = bindings(root, args.locale_root, args.catalog_root)
        report["bindings"] = bound
        report["keymap_count"] = keymap_check(root, json.loads(
            Path(bound["catalog_path"]).read_text()))
        if args.regression:
            report["regression"] = regression(root, out, args.locale_root, args.catalog_root)
        elif args.controls:
            report["reader_controls"] = reader_controls(
                args.controls, root, args.locale_root, args.catalog_root, bound, out)
        elif args.verify:
            prior = readback(args.verify, root, bound)
            report["input"] = {"path": str(args.verify), "sha256": sha(args.verify.read_bytes())}
            report["verified_children"] = len(prior["children"])
        else:
            (out / "fixture.cjs").write_text(NODE)
            (out / "assembly-panel.source.js").write_bytes((root / OWNED[0]).read_bytes())
            (out / "locale-service.source.js").write_bytes(Path(bound["locale_path"]).read_bytes())
            (out / "dictionary.json").write_bytes(Path(bound["catalog_path"]).read_bytes())
            (out / "runtime").mkdir()
            for mode in ("positive", *NEGATIVES):
                record = run_node(root, bound, out, mode)
                report["children"].append(record)
                validate_child(record)
        report["source_after"] = source(root)
        require(report["source_before"] == report["source_after"], "source_drift_during_test")
        report["bindings_after"] = bindings(root, args.locale_root, args.catalog_root)
        require(report["bindings"] == report["bindings_after"], "dependency_drift_during_test")
        report["verdict"], rc = "PASS", 0
    except Exception as error:
        report["first_cause"] = str(error)
    report["actual_rc"] = rc
    report["finished_at"] = now()
    count = len(report["children"])
    verified = report.get("verified_children", 0)
    reader_negatives = len(report.get("reader_controls", []))
    marker = (f"ASSEMBLY_LOCALE verdict={report['verdict']} children={count} "
              f"verified={verified} reader_negatives={reader_negatives} rc={rc}")
    (out / "completion.log").write_text(marker + "\n")
    report["artifacts"] = inventory(out)
    write_json(out / "result.json", report)
    print(marker + f" at={report['finished_at']} evidence={out}", flush=True)
    if rc:
        print("first_cause=" + str(report["first_cause"]), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
