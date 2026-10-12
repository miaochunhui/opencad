#!/usr/bin/env python3
"""Execute the complete parametric panel with a strict DOM/locale contract double.

Run --selftest with fresh external --out and local --runtime-root. --before
executes the frozen eb19211 product and deliberately returns the real red.
No browser, HTTP server, production data or shared dictionary is invoked.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = "oss-alternatives/apps/opencad/web/js/parametric-panel.js"
TEST = "oss-alternatives/apps/opencad/tests/test_parametric_panel_locale.py"
DOC = "oss-alternatives/apps/opencad/docs/design/parametric-panel-locale.md"
BASE = "eb19211ca7ca4b9304678fe747026009234d1515"
PREFIX = "panel.parametric."
RUNTIME = None
BEFORE = False
MARKER = "OPENCAD_PARAMETRIC_PANEL_LOCALE"


def record_error(report, error):
    if report["first_cause"] is None:
        report["first_cause"] = error
    else:
        report["secondary_errors"].append(error)
    report.update(verdict="FAIL", actual_rc=1)


class OrderedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_cause = None

    def addFailure(self, test, err):
        if self.first_cause is None:
            self.first_cause = self._exc_info_to_string(err, test)
        super().addFailure(test, err)

    def addError(self, test, err):
        if self.first_cause is None:
            self.first_cause = self._exc_info_to_string(err, test)
        super().addError(test, err)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def dictionary():
    """Only a test double: consume the documented request, not product strings."""
    packs = {locale: {} for locale in ("zh-CN", "en", "ja")}
    for line in (ROOT / DOC).read_text().splitlines():
        if not line.startswith("| ") or " --- " in line or "Key Suffix" in line:
            continue
        fields = [part.strip().replace(r"\|", "|")
                  for part in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(fields) != 4:
            raise ValueError("locale_request_row_not_exact")
        key = PREFIX + fields[0]
        if key in packs["en"]:
            raise ValueError("locale_request_key_duplicate:" + key)
        placeholders = [set(re.findall(r"\{(\w+)\}", value)) for value in fields[1:]]
        if placeholders[1:] != placeholders[:1] * 2:
            raise ValueError("locale_request_placeholders_differ:" + key)
        for locale, value in zip(packs, fields[1:]):
            packs[locale][key] = value
    if len(packs["en"]) < 70:
        raise ValueError("locale_request_empty_or_incomplete")
    return packs


EXPOSURE = r"""
  window.__localePanel = {
    render: render, shield: shield, report: report, markBusy: markBusy, json: json,
    state: function () {
      return {current, specification, owner, blocked, expired, busy, dirty, reads, epoch,
        clearOverrides, definitionDirty, modelStale, definitionStale, definitionMode,
        modelDraft: fields ? modelDraft() : null,
        definitionDraft: definitionForm ? definitionDraft() : null,
        bindingError: bindingError && bindingError.message};
    },
    references: function () { return {current, specification, bindingUpdate}; },
    elements: function () { return {panel, form, fields, dimensions, configurations, features,
      result, notice, discardConfirmation, definitionForm, definitionTarget, definitionName,
      definitionParameters, definitionFeatures, selectionPolicy, deleteReplacement,
      deleteConfirmation, definitionNotice}; },
    result: function () {
      const args = {volume: "123.456000", bytes: 15434, sha: "0123456789ab"};
      if (typeof localText === "function") localText(result, "rebuild_result", args);
      else result.textContent = "重建已保存 | 体积 123.456000 mm³ | STEP 15434 bytes | 0123456789ab";
      result.dataset.documentSha = current.document_sha256;
      result.dataset.volumeMm3 = "123.456";
      result.dataset.revision = String(current.revision);
    },
    suspend: function (permanent) {
      if (typeof shieldKey === "function") shieldKey(permanent ? "session_expired" :
        "read_unverified", permanent, permanent ? undefined : {message: "raw_stale: private-detail"});
      else shield(permanent ? "登录已失效，已清除本页私有参数。请重新登录。" :
        "参数未能验证，草稿保留但暂停编辑。请核对保存结果后重读：raw_stale: private-detail", permanent);
    }
  };
"""

RUNNER = r"""
"use strict";
const fs = require("fs"), vm = require("vm"), assert = require("assert"), crypto = require("crypto");
const packs = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const scenario = process.argv[4], prefix = "panel.parametric.";
let mutations = 0, requests = 0, binds = 0, unbinds = 0, paints = 0, peakTokens = 0;
let updates = 0, translations = 0, readyResolve, readyReject;
const tokens = new Map(), tokenEvents = [], unhandled = [];
const originalRejection = Object.assign(new Error("private-ready-detail: 数据"), {
  code: "private-code", detail: "private-detail", status: 403
});
process.on("unhandledRejection", reason => { unhandled.push(reason); });
const listeners = {}, queries = [], calls = [];
const allowed = new Set(["button, input, select", "button, select", "input, select", "button, input",
  "[data-definition-v2]", "[data-parametric-create]", "[data-parametric-discard]",
  "[data-parametric-cancel-discard]", "[data-definition-create]", "[data-definition-replace]",
  "[data-definition-suppressions]", "[data-definition-delete]", "[data-definition-new]",
  "[data-definition-explicit]", "[data-definition-parameter]", "[data-definition-suppression]",
  "[data-parametric-rebuild]", "[data-parametric-locale]", "[data-panel-i18n]",
  "[data-panel-i18n-aria]", "[data-panel-i18n-placeholder]", "[data-panel-i18n-title]",
  "[data-panel-i18n-alt]", "[data-parameter]", "[data-dimension]"]);
function attribute(node, key) {
  if (key.startsWith("data-")) {
    const name = key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return Object.hasOwn(node.dataset, name) ? String(node.dataset[name]) : null;
  }
  return Object.hasOwn(node.attributes, key) ? node.attributes[key] : null;
}
function select(root, selector) {
  queries.push(selector);
  assert(allowed.has(selector), "fixture_unknown_selector:" + selector);
  const targets = selector.split(/,\s*/);
  return descendants(root).filter(node => targets.some(part => {
    if (part.startsWith("[")) return attribute(node, part.slice(1, -1)) !== null;
    return node.tagName === part.toUpperCase();
  }));
}
function descendants(root) {
  return root.children.flatMap(node => [node, ...descendants(node)]);
}
let serial = 0;
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.uid = ++serial; this.children = [];
    this.dataset = {}; this.attributes = {}; this.events = {}; this.parentElement = null;
    this.hidden = false; this.disabled = false; this.checked = false; this._text = "";
    this._value = ""; this.className = ""; this.id = "";
  }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(value) {
    for (const child of this.children) child.parentElement = null;
    this._text = String(value); this.children = [];
  }
  get options() { assert(this.tagName === "SELECT", "fixture_options_not_select"); return this.children; }
  get value() {
    if (this.tagName !== "SELECT") return this._value;
    const selected = this.children.find(node => node.value === this._value);
    return selected ? selected.value : this.children[0]?.value || "";
  }
  set value(value) { this._value = String(value); }
  append(...nodes) {
    ++mutations;
    for (const node of nodes) {
      assert(node instanceof Element, "fixture_append_not_element");
      if (node.parentElement) {
        node.parentElement.children = node.parentElement.children.filter(item => item !== node);
      }
      node.parentElement = this; this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    ++mutations;
    for (const child of this.children) child.parentElement = null;
    this.children = []; this._text = ""; this._value = ""; this.append(...nodes);
  }
  setAttribute(key, value) {
    if (key.startsWith("data-")) {
      this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    } else this.attributes[key] = String(value);
  }
  getAttribute(key) { return attribute(this, key); }
  removeAttribute(key) {
    if (key.startsWith("data-")) {
      delete this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())];
    } else delete this.attributes[key];
  }
  hasAttribute(key) { return this.getAttribute(key) !== null; }
  querySelectorAll(selector) { return select(this, selector); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(target) { return target === this || descendants(this).includes(target); }
  addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }
  dispatchEvent(event) {
    event.target ||= this; event.preventDefault ||= function () {};
    for (const fn of this.events[event.type] || []) fn(event);
    if (this.parentElement && event.bubbles !== false) this.parentElement.dispatchEvent(event);
    return true;
  }
  focus() { document.activeElement = this; this.dispatchEvent({type: "focusin"}); }
}
const host = new Element("aside"), body = new Element("body");
body.append(host);
const document = {readyState: "loading", activeElement: null, body,
  createElement(tag) {
    assert(["section", "form", "fieldset", "label", "input", "select", "option", "button",
      "p", "legend", "span", "small", "strong", "div", "h2", "h3", "output"].includes(tag),
      "fixture_unknown_tag:" + tag);
    return new Element(tag);
  },
  addEventListener(type, fn) {
    assert(type === "DOMContentLoaded", "fixture_unknown_document_event:" + type);
    (listeners["dom:" + type] ||= []).push(fn);
  },
  querySelector(selector) {
    assert(["[data-parametric-host]", "main > aside"].includes(selector),
      "fixture_unknown_document_selector:" + selector);
    return host;
  }};
let locale = "zh-CN", missing = null;
function t(key, args) {
  ++translations; calls.push(key);
  assert(Object.hasOwn(packs[locale], key) && key !== missing, "missing_panel_translation:" + key);
  const template = packs[locale][key], wanted = [...template.matchAll(/\{(\w+)\}/g)].map(item => item[1]);
  for (const name of wanted) {
    assert(args && Object.hasOwn(args, name), "missing_panel_argument:" + name);
  }
  assert(Object.keys(args || {}).every(name => wanted.includes(name)),
    "unknown_panel_argument");
  return template.replace(/\{(\w+)\}/g, (_, name) => String(args[name]));
}
const service = {
  ready: scenario === "ready" || scenario.startsWith("ready_")
    ? new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; })
    : Promise.resolve(),
  snapshot() { return {locale, strings: packs[locale]}; },
  t,
  update() { ++updates; throw Error("panel_must_not_update_locale"); },
  bind(root) {
    ++binds;
    const selected = [];
    for (const [attr, target] of [["data-panel-i18n", null], ["data-panel-i18n-aria", "aria-label"],
        ["data-panel-i18n-placeholder", "placeholder"], ["data-panel-i18n-title", "title"],
        ["data-panel-i18n-alt", "alt"]]) {
      for (const node of [root, ...root.querySelectorAll("[" + attr + "]")]) {
        const key = node.getAttribute(attr);
        if (key === null) continue;
        assert(target || !node.children.length, "locale_text_binding_must_be_leaf");
        selected.push({node, target, value: t(key)});
      }
    }
    const token = {};
    tokens.set(token, root); tokenEvents.push("bind");
    peakTokens = Math.max(peakTokens, tokens.size);
    for (const {node, target, value} of selected) {
      if (target) node.setAttribute(target, value); else node.textContent = value;
    }
    ++paints;
    return function () {
      assert(tokens.has(token), "fixture_duplicate_or_unknown_unbind");
      tokens.delete(token); ++unbinds; tokenEvents.push("unbind");
    };
  }
};
const spec = {parameters: {width: {expression: "60 * mm", unit: "length"}},
  configurations: {Lean: {width: "40 * mm"}, Wide: {width: "80 * mm"}}};
const doc = JSON.stringify({parametric: spec}), sha = crypto.createHash("sha256").update(doc).digest("hex");
const value = {ok: true, enabled: true, parametric_version: 2, can_create_plate: false,
  project_id: "private-project", revision: 9, sha256: "a".repeat(64), document_sha256: sha,
  configurations: ["Lean", "Wide"], configuration: "Lean",
  parameters: [{name: "width", expression: "40 * mm", unit: "length", value: {value: 40000000}}],
  driving_dimensions: [{key: "plate_width", editable: true, value_nm: 40000000}],
  configuration_definitions: {Lean: {width: "40 * mm"}, Wide: {width: "80 * mm"}},
  configuration_suppressions: {Lean: {hole_cut: true}, Wide: {}},
  feature_bindings: {hole_cut: {target_uuid: "private-uuid", base_suppressed: false},
    plate_boss: {target_uuid: "private-plate-uuid", base_suppressed: false}}};
const state = {...value, doc};
let rejectedResponse = false;
const window = {OpenCADLocale: service,
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
  dispatchEvent(event) { for (const fn of listeners[event.type] || []) fn(event); return true; },
  fetch(path) {
    ++requests;
    if (rejectedResponse) return Promise.resolve({ok: false, status: 409,
      json: async () => ({ok: false, error: "raw_cas_conflict", detail: "original-detail: 数据"})});
    assert(["/api/auth/me", "/api/parametric", "/api/state"].includes(path),
      "fixture_unexpected_request:" + path);
    return Promise.resolve({ok: true, status: 200, json: async () => path === "/api/auth/me"
      ? {ok: true, user: {user_id: "private-owner"}} : path === "/api/state" ? state : value});
  }};
const context = {window, document, console, TextEncoder, crypto: crypto.webcrypto,
  CustomEvent: class { constructor(type, args) { this.type = type; this.detail = args?.detail; } }};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context, {filename: "parametric-panel.js"});
const api = window.__localePanel;
function switchTo(next) { locale = next; window.dispatchEvent({type: "opencad:locale",
  detail: service.snapshot()}); }
function snapshot(elements) {
  return {state: JSON.parse(JSON.stringify(api.state())), mutations, requests,
    tree: [elements.panel, ...descendants(elements.panel)].map(node => ({
      uid: node.uid, children: node.children.map(child => child.uid), value: node.value,
      checked: node.checked, disabled: node.disabled, hidden: node.hidden,
      dataset: Object.fromEntries(Object.entries(node.dataset).filter(([key]) =>
        !key.startsWith("panelI18n") && key !== "parametricLocale"))
    }))};
}
function assertInvariant(before, elements) {
  assert.deepStrictEqual(snapshot(elements), before, "locale_mutated_private_state_or_dom_identity");
}
function editableDrafts(elements) {
  const model = elements.fields.querySelector("[data-parameter]");
  model.value = "73 * mm"; model.dispatchEvent({type: "input"});
  const explicit = elements.definitionParameters.querySelector("[data-definition-explicit]");
  explicit.checked = true; explicit.dispatchEvent({type: "change"});
  const expression = elements.definitionParameters.querySelector("[data-definition-parameter]");
  expression.value = "81 * mm"; expression.dispatchEvent({type: "input"});
  elements.selectionPolicy.value = "target"; elements.selectionPolicy.dispatchEvent({type: "change"});
  elements.deleteReplacement.value = "Wide";
  const suppression = elements.definitionFeatures.querySelector("[data-definition-suppression]");
  suppression.value = "live"; suppression.dispatchEvent({type: "change"});
  elements.panel.dataset.receipt = "private-receipt";
  api.result();
}
(async function () {
  if (scenario === "ready_rejection" || scenario === "ready_recovery" ||
      scenario === "mount_rejection" || scenario === "service_rejection") {
    if (scenario === "mount_rejection") {
      const createElement = document.createElement;
      document.createElement = function (tag) {
        if (tag === "section") throw originalRejection;
        return createElement(tag);
      };
    }
    if (scenario === "service_rejection") {
      Object.defineProperty(window, "OpenCADLocale", {get() { throw originalRejection; }});
    }
    for (const fn of listeners["dom:DOMContentLoaded"] || []) fn();
    if (scenario.startsWith("ready_")) readyReject(originalRejection);
    await new Promise(resolve => setImmediate(resolve));
    assert(window.opencadParametric.ready &&
      typeof window.opencadParametric.ready.then === "function", "boot_rejection_not_observable");
    await assert.rejects(window.opencadParametric.ready, error => error === originalRejection,
      "boot_original_rejection_lost");
    const alerts = body.children.filter(node =>
      node.getAttribute("data-opencad-public-locale-error") === "parametric");
    assert(alerts.length === 1, "boot_public_alert_missing_or_duplicate");
    const alert = alerts[0];
    assert(alert.tagName === "OUTPUT" && alert.getAttribute("role") === "alert" &&
      alert.getAttribute("lang") === "en" && !alert.hidden && !alert.hasAttribute("hidden") &&
      !host.contains(alert) && !alert.children.length, "boot_alert_not_independent_or_visible");
    assert(Object.keys(alert.attributes).sort().join(",") === "lang,role" &&
      Object.keys(alert.dataset).sort().join(",") === "opencadPublicLocaleError" &&
      alert.getAttribute("data-opencad-public-locale-error") === "parametric",
      "boot_alert_contract_shape");
    assert(alert.textContent === "parametric_locale_initialization_failed" &&
      JSON.stringify({text: alert.textContent, attributes: alert.attributes,
        dataset: alert.dataset}).includes("private") === false, "boot_alert_private_detail_leak");
    assert(requests === 0 && api.state().current === null && api.state().owner === null,
      "boot_failure_loaded_private_state");
    assert(unhandled.length === 0, "boot_rejection_not_explicitly_received");
    if (scenario === "ready_recovery") {
      service.ready = Promise.resolve();
      for (const fn of listeners["dom:DOMContentLoaded"] || []) fn();
      await window.opencadParametric.ready;
      await new Promise(resolve => setImmediate(resolve));
      assert(alert.hidden && alert.textContent === "" && host.children.length === 1,
        "boot_recovery_did_not_clear_public_alert");
    }
    console.log(JSON.stringify({scenario, requests, real_js: true, original_rejection: true,
      unhandled: unhandled.length, public_alert: true, updates})); return;
  }
  if (scenario === "strict_unbind") {
    const root = new Element("section"), leaf = new Element("p");
    leaf.setAttribute("data-panel-i18n", prefix + "title"); root.append(leaf);
    const release = service.bind(root);
    assert(tokens.size === 1 && binds === 1 && paints === 1 &&
      leaf.textContent === t(prefix + "title"), "fixture_bind_did_not_immediately_paint");
    release();
    assert(tokens.size === 0 && unbinds === 1, "fixture_unbind_did_not_release_token");
    assert.throws(release, /fixture_duplicate_or_unknown_unbind/);
    console.log(JSON.stringify({scenario, real_js: true, binds, unbinds, paints})); return;
  }
  if (scenario === "ready") {
    for (const fn of listeners["dom:DOMContentLoaded"] || []) fn();
    await Promise.resolve();
    assert(host.children.length === 0 && requests === 0, "locale_ready_not_awaited");
    readyResolve(); await new Promise(resolve => setImmediate(resolve));
    assert(host.children.length === 1 && requests > 0, "locale_ready_did_not_mount");
    console.log(JSON.stringify({scenario, requests, real_js: true, updates})); return;
  }
  window.opencadParametric.mount(host); await window.opencadParametric.refresh(true);
  const elements = api.elements();
  assert(elements.fields.querySelector("[data-parameter]").tagName === "INPUT" &&
    elements.configurations.tagName === "SELECT", "fixture_types_not_real");
  assert(elements.panel.querySelectorAll("button, select").every(node => node.tagName !== "INPUT"),
    "fixture_selector_subset_not_exact");
  assert.throws(() => elements.panel.querySelectorAll("unknown, input"), /fixture_unknown_selector/);
  if (scenario === "tokens" || scenario === "token_failure") {
    editableDrafts(elements);
    const before = snapshot(elements), initialBinds = binds, initialUnbinds = unbinds;
    if (scenario === "token_failure") {
      missing = prefix + "title";
      assert.throws(() => switchTo("en"), /missing_panel_translation/);
      assert(tokens.size === 0, "failed_rebind_retained_old_token");
      missing = null;
    }
    for (let index = 0; index < 60; ++index) {
      const beforeBinds = binds, beforeUnbinds = unbinds, beforePaints = paints;
      const hadToken = tokens.size;
      switchTo(["en", "ja", "zh-CN"][index % 3]);
      assert(tokens.size === 1 && peakTokens === 1, "locale_bind_token_leak");
      assert(binds === beforeBinds + 1 && unbinds === beforeUnbinds + hadToken &&
        paints === beforePaints + 1, "locale_bind_not_released_or_immediately_painted");
      const title = elements.panel.children.find(node => node.tagName === "H2");
      assert(title.textContent === t(prefix + "title"), "locale_rebind_did_not_paint");
      assertInvariant(before, elements);
    }
    const tokenBinds = tokenEvents.filter(event => event === "bind").length;
    const tokenUnbinds = tokenEvents.filter(event => event === "unbind").length;
    assert(binds > initialBinds && unbinds > initialUnbinds && tokens.size === 1 &&
      tokenBinds - tokenUnbinds === 1,
      "locale_live_token_accounting_incorrect binds=" + binds +
      " unbinds=" + unbinds + " initial=" + initialBinds + "/" + initialUnbinds +
      " live=" + tokens.size + " token_delta=" + (tokenBinds - tokenUnbinds));
  } else if (scenario === "shield_missing_key") {
    editableDrafts(elements);
    missing = prefix + "session_expired";
    assert.throws(() => api.suspend(true), /missing_panel_translation/);
    const cleared = api.state();
    assert(cleared.expired && !cleared.current && !cleared.owner &&
      elements.deleteReplacement.options.length === 0 && !elements.definitionForm.dataset.editingTarget &&
      elements.fields.children.length === 0 && elements.result.textContent === "" &&
      elements.panel.querySelectorAll("button, input, select").every(node => node.disabled),
      "translation_failure_prevented_private_clear");
    const before = snapshot(elements); missing = null; switchTo("en");
    assertInvariant(before, elements);
    assert(elements.result.textContent === "" && elements.definitionNotice.textContent === "",
      "missing_key_recovery_resurrected_private_data");
  } else if (scenario === "local_error") {
    const remove = elements.definitionForm.querySelector("[data-definition-delete]");
    elements.definitionTarget.replaceChildren();
    remove.dispatchEvent({type: "click"});
    const before = snapshot(elements);
    for (const lang of ["en", "ja", "zh-CN"]) {
      switchTo(lang); assertInvariant(before, elements);
      assert(elements.notice.textContent === t(prefix + "no_configuration_error",
        {message: "没有可删除的配置"}), "local_error_hint_untranslated");
    }
  } else if (scenario === "unknown_argument") {
    assert.throws(() => t(prefix + "expression_aria"), /missing_panel_argument/);
    assert.throws(() => t(prefix + "expression_aria", {name: "width", bad: "value"}),
      /unknown_panel_argument/);
  } else if (scenario === "unknown_key") {
    missing = prefix + "title";
    assert.throws(() => switchTo("en"), /missing_panel_translation/);
  } else if (scenario === "unknown_binding") {
    elements.panel.setAttribute("data-panel-i18n-title", prefix + "does_not_exist");
    assert.throws(() => switchTo("en"), /missing_panel_translation/);
  } else if (scenario === "unknown_selector") {
    elements.panel.querySelectorAll("button, textarea");
  } else if (scenario === "error") {
    rejectedResponse = true;
    let error;
    try { await api.json("/api/parametric"); } catch (caught) { error = caught; }
    assert(error && error.code === "raw_cas_conflict" &&
      error.detail === "original-detail: 数据" && error.status === 409, "original_error_fields_lost");
    api.report(error); const before = snapshot(elements);
    for (const lang of ["en", "ja", "zh-CN"]) {
      switchTo(lang); assertInvariant(before, elements);
      assert(elements.notice.textContent === t(prefix + "error", {message: error.message}),
        "error_user_hint_not_translated");
      assert(elements.notice.textContent.includes("raw_cas_conflict: original-detail: 数据"),
        "original_error_detail_translated_or_lost");
    }
  } else if (scenario === "v1") {
    api.render({value: {...value, parametric_version: 1}, spec, owner: "private-owner"}, false, false);
    const before = snapshot(elements); switchTo("ja"); assertInvariant(before, elements);
    assert(elements.features.textContent.includes(t(prefix + "v1_preview")) &&
      elements.definitionFeatures.textContent.includes(t(prefix + "v1_definition")), "v1_text_untranslated");
  } else {
    editableDrafts(elements);
    if (scenario === "attributes") {
      elements.definitionName.setAttribute("data-panel-i18n-placeholder", prefix + "definition_name_aria");
      elements.definitionName.setAttribute("data-panel-i18n-title", prefix + "definition_name_aria");
      elements.definitionName.setAttribute("data-panel-i18n-alt", prefix + "definition_name_aria");
    }
    if (scenario === "busy") api.markBusy(true);
    if (scenario === "focused") elements.fields.querySelector("[data-parameter]").focus();
    if (scenario === "create") {
      api.render({value, spec, owner: "private-owner"}, false, false);
      elements.definitionForm.querySelector("[data-definition-new]").dispatchEvent({type: "click"});
      assert(api.state().definitionMode === "create" && api.state().definitionDirty &&
        !elements.definitionName.hidden && elements.definitionTarget.hidden,
        "fixture_did_not_enter_create_mode");
      elements.definitionName.value = "NewDraft";
      const explicit = elements.definitionParameters.querySelector("[data-definition-explicit]");
      explicit.checked = true; explicit.dispatchEvent({type: "change"});
      elements.definitionParameters.querySelector("[data-definition-parameter]").value = "81 * mm";
    }
    if (scenario === "temporary" || scenario === "permanent") api.suspend(scenario === "permanent");
    const originalReferences = api.references();
    const before = snapshot(elements);
    const order = scenario === "zh" ? ["ja", "en", "zh-CN"] : scenario === "ja" ? ["ja", "zh-CN", "en"] :
      ["en", "ja", "zh-CN"];
    for (const lang of order) {
      switchTo(lang); await new Promise(resolve => setImmediate(resolve)); assertInvariant(before, elements);
      const title = elements.panel.children.find(node => node.tagName === "H2");
      assert(title.textContent === t(prefix + "title"), "locale_title_untranslated");
      assert(elements.panel.getAttribute("aria-label") === t(prefix + "panel_aria"),
        "locale_panel_aria_untranslated");
      if (scenario === "permanent") {
        assert(elements.result.textContent === "" && elements.definitionNotice.textContent === "" &&
          elements.deleteReplacement.options.length === 0 && !elements.definitionForm.dataset.editingTarget,
          "locale_resurrected_private_shield_data");
        assert(elements.notice.textContent === t(prefix + "session_expired"), "shield_hint_untranslated");
        assert(elements.panel.querySelectorAll("button, input, select").every(node => node.disabled),
          "private_clear_action_gate");
      } else if (scenario === "temporary") {
        assert(elements.notice.textContent === t(prefix + "read_unverified",
          {message: "raw_stale: private-detail"}), "temporary_shield_hint_untranslated");
      } else {
        const expression = elements.definitionParameters.querySelector("[data-definition-parameter]");
        assert(expression.getAttribute("aria-label") === t(prefix + "definition_expression_aria",
          {name: "width"}), "dynamic_aria_untranslated");
        assert(elements.result.textContent === t(prefix + "rebuild_result",
          {volume: "123.456000", bytes: 15434, sha: "0123456789ab"}), "result_untranslated");
        assert(elements.deleteReplacement.children.find(node => node.value === "Wide").textContent ===
          t(prefix + "replacement_named", {name: "Wide"}), "replacement_name_untranslated");
        if (scenario === "attributes") {
          for (const attr of ["placeholder", "title", "alt"]) {
            assert(elements.definitionName.getAttribute(attr) === t(prefix + "definition_name_aria"),
              "locale_attribute_untranslated:" + attr);
          }
        }
      }
    }
    const references = api.references();
    assert(references.current === originalReferences.current &&
      references.specification === originalReferences.specification &&
      references.bindingUpdate === originalReferences.bindingUpdate ||
      scenario === "permanent", "locale_replaced_document_or_receipt_binding");
    if (scenario === "permanent") {
      const notice = elements.notice.textContent; api.report(new Error("late-private-error"));
      assert(elements.notice.textContent === notice, "late_error_overrode_permanent_shield");
      await assert.rejects(window.opencadParametric.submit("rebuild"), /parametric_operation_unavailable/);
    }
  }
  assert(updates === 0, "panel_updated_shared_locale");
  console.log(JSON.stringify({scenario, requests, binds, unbinds, paints, peakTokens,
    live_tokens: tokens.size, token_events: tokenEvents, translations, updates, mutations,
    real_js: true, keys: [...new Set(calls)], queries, state: api.state()}));
}()).catch(error => { console.error(error.stack); process.exitCode = 1; });
"""


def probe(scenario, *, mutation=None):
    runtime = RUNTIME / (scenario + ("-" + mutation[0] if mutation else ""))
    runtime.mkdir(exist_ok=False)
    product = (subprocess.check_output(["git", "--no-replace-objects", "-C", str(ROOT),
                                      "show", BASE + ":" + PRODUCT]) if BEFORE else (ROOT / PRODUCT).read_bytes())
    original = product
    if mutation:
        _, old, new = mutation
        if product.count(old.encode()) != 1:
            raise ValueError("locale_mutation_anchor_not_exact:" + mutation[0])
        product = product.replace(old.encode(), new.encode())
    anchor = b"window.opencadParametric = {mount: mount, refresh: refresh, submit: submit};"
    if product.count(anchor) != 1:
        raise ValueError("locale_exposure_anchor_not_exact")
    instrumented = product.replace(anchor, anchor + EXPOSURE.encode())
    (runtime / "product.js").write_bytes(instrumented)
    (runtime / "runner.cjs").write_text(RUNNER)
    (runtime / "packs.json").write_text(json.dumps(dictionary(), ensure_ascii=False))
    node = shutil.which("node")
    if node is None:
        raise ValueError("locale_node_required")
    argv = [node, str(runtime / "runner.cjs"), str(runtime / "product.js"),
            str(runtime / "packs.json"), scenario]
    child = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (runtime / "stdout.log").write_bytes(child.stdout)
    (runtime / "stderr.log").write_bytes(child.stderr)
    binding = {"argv": argv, "actual_rc": child.returncode, "base": BASE if BEFORE else None,
               "product_sha256": digest(original), "tested_sha256": digest(product),
               "instrumented_sha256": digest(instrumented), "runner_sha256": digest(RUNNER.encode()),
               "packs_sha256": digest((runtime / "packs.json").read_bytes()),
               "stdout_sha256": digest(child.stdout), "stderr_sha256": digest(child.stderr),
               "mutation": mutation[0] if mutation else None}
    (runtime / "binding.json").write_text(json.dumps(binding, indent=2) + "\n")
    return child


class Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUNTIME
        if RUNTIME is None:
            RUNTIME = Path("/data/tmp") / ("oc-ppl-discovery-" + uuid4().hex)
            RUNTIME.mkdir(exist_ok=False)

    def success(self, scenario):
        child = probe(scenario)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        self.assertTrue(json.loads(child.stdout)["real_js"])

    def test_en_switch_preserves_drafts_selection_and_receipt(self):
        self.success("en")

    def test_ja_switch_preserves_drafts_selection_and_receipt(self):
        self.success("ja")

    def test_zh_switch_preserves_drafts_selection_and_receipt(self):
        self.success("zh")

    def test_busy_gate_preserved(self):
        self.success("busy")

    def test_focused_inputs_preserved(self):
        self.success("focused")

    def test_create_draft_preserved(self):
        self.success("create")

    def test_temporary_shield_preserves_drafts_and_translates(self):
        self.success("temporary")

    def test_permanent_shield_never_resurrects_private_state(self):
        self.success("permanent")

    def test_error_code_detail_status_preserved_and_hint_translated(self):
        self.success("error")

    def test_v1_does_not_upgrade(self):
        self.success("v1")

    def test_ready_is_awaited(self):
        self.success("ready")

    def test_ready_rejection_retains_original_and_public_safe_alert(self):
        self.success("ready_rejection")

    def test_ready_recovery_clears_only_public_alert(self):
        self.success("ready_recovery")

    def test_mount_rejection_retains_original_and_public_safe_alert(self):
        self.success("mount_rejection")

    def test_service_access_rejection_retains_original_and_public_safe_alert(self):
        self.success("service_rejection")

    def test_bind_tokens_are_released_before_each_immediate_paint(self):
        self.success("tokens")

    def test_failed_rebind_releases_old_token_and_can_recover(self):
        self.success("token_failure")

    def test_locale_double_strict_unbind_and_immediate_paint(self):
        self.success("strict_unbind")

    def test_unknown_key_fails_loud(self):
        self.success("unknown_key")

    def test_unknown_binding_fails_loud(self):
        self.success("unknown_binding")

    def test_missing_shield_translation_still_clears_private_state(self):
        self.success("shield_missing_key")

    def test_unknown_and_missing_placeholder_fixture_fails_loud(self):
        self.success("unknown_argument")

    def test_placeholder_title_alt_bindings(self):
        self.success("attributes")

    def test_local_error_preserves_diagnostic_and_translates_guidance(self):
        self.success("local_error")

    def test_owned_three_file_source_hygiene(self):
        spec = importlib.util.spec_from_file_location("panel_locale_scan", ROOT / "tools/bootstrap_from_infra.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in (PRODUCT, TEST, DOC):
            module.scan_bytes(name, (ROOT / name).read_bytes())

    def test_unknown_selector_fixture_fails_loud(self):
        child = probe("unknown_selector")
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"fixture_unknown_selector:button, textarea", child.stderr)

    def test_dictionary_has_exact_three_complete_placeholder_sets(self):
        packs = dictionary()
        self.assertEqual(set(packs), {"zh-CN", "en", "ja"})
        self.assertEqual(set(packs["en"]), set(packs["ja"]))
        self.assertEqual(set(packs["en"]), set(packs["zh-CN"]))

    def test_missed_locale_event_mutation_is_rejected(self):
        child = probe("en", mutation=("missed-event",
            'window.addEventListener("opencad:locale"', 'window.addEventListener("opencad:wrong-locale"'))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"locale_title_untranslated", child.stderr)

    def test_shield_action_missing_input_mutation_is_rejected(self):
        child = probe("permanent", mutation=("missing-input",
            'panel.querySelectorAll("button, input, select")', 'panel.querySelectorAll("button, select")'))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"private_clear_action_gate", child.stderr)

    def test_locale_state_mutation_is_rejected(self):
        child = probe("en", mutation=("dirty-reset",
            'window.addEventListener("opencad:locale", translatePanel);',
            'window.addEventListener("opencad:locale", function () { dirty = false; translatePanel(); });'))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"locale_mutated_private_state_or_dom_identity", child.stderr)

    def test_locale_fetch_mutation_is_rejected(self):
        child = probe("en", mutation=("locale-fetch",
            'window.addEventListener("opencad:locale", translatePanel);',
            'window.addEventListener("opencad:locale", function () { window.fetch("/api/state"); translatePanel(); });'))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"locale_mutated_private_state_or_dom_identity", child.stderr)

    def test_cleared_result_binding_mutation_is_rejected(self):
        child = probe("permanent", mutation=("resurrect-result",
            'configurations.replaceChildren(); clearLocalText(result);',
            'configurations.replaceChildren(); result.textContent = "";'))
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(b"locale_resurrected_private_shield_data", child.stderr)

    def terminal_report(self):
        return {"verdict": "PASS", "actual_rc": 0, "tests": 23, "passed": 23,
                "first_cause": None, "secondary_errors": [], "source_after": None,
                "finished_at": now()}

    def test_terminal_result_write_failure_preserves_first_and_null_hash(self):
        out = RUNTIME / "terminal-result-failure"
        out.mkdir(exist_ok=False)
        report = self.terminal_report()
        report["first_cause"] = "original-js-failure"
        report.update(verdict="FAIL", actual_rc=1)
        original = Path.write_bytes

        def fail_result(path, data):
            if path.name == "result.json":
                raise OSError("injected-result-write-failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", fail_result):
            publish(report, io.StringIO(), out)
        marker = json.loads((out / "completion.json").read_bytes())
        self.assertEqual(marker["first_cause"], "original-js-failure")
        self.assertIn("injected-result-write-failure", marker["secondary_errors"][0])
        self.assertIsNone(marker["result_sha256"])
        self.assertEqual(marker["actual_rc"], 1)

    def test_terminal_log_write_failure_has_null_log_hash(self):
        out = RUNTIME / "terminal-log-failure"
        out.mkdir(exist_ok=False)
        report = self.terminal_report()
        original = Path.write_bytes

        def fail_log(path, data):
            if path.name == "tests.log":
                raise OSError("injected-log-write-failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", fail_log):
            publish(report, io.StringIO(), out)
        marker = json.loads((out / "completion.json").read_bytes())
        self.assertIn("injected-log-write-failure", marker["first_cause"])
        self.assertIsNone(marker["log_sha256"])
        self.assertEqual(marker["verdict"], "FAIL")

    def test_terminal_marker_failure_is_not_pass(self):
        out = RUNTIME / "terminal-marker-failure"
        out.mkdir(exist_ok=False)
        report = self.terminal_report()
        original = Path.write_bytes

        def fail_marker(path, data):
            if path.name == "completion.json":
                raise OSError("injected-marker-write-failure")
            return original(path, data)

        with patch.object(Path, "write_bytes", fail_marker):
            publish(report, io.StringIO(), out)
        self.assertEqual(report["actual_rc"], 1)
        self.assertIn("injected-marker-write-failure", report["first_cause"])
        fallback = json.loads((out / "terminal-failure.json").read_bytes())
        self.assertEqual(fallback["verdict"], "FAIL")

    def test_test_error_order_precedes_later_failure(self):
        class Actors(unittest.TestCase):
            def test_first(self):
                raise RuntimeError("chronological-first-error")

            def test_second(self):
                self.fail("later-failure")

        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=OrderedResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Actors))
        self.assertIn("chronological-first-error", result.first_cause)
        self.assertNotIn("later-failure", result.first_cause)


def source():
    git = ["git", "--no-replace-objects", "--no-optional-locks", "-C", str(ROOT)]
    paths = subprocess.check_output(git + ["ls-files", "-z"]).split(b"\0")
    paths += subprocess.check_output(git + ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0")
    files = {}
    for raw in sorted(set(paths) - {b""}):
        name = raw.decode()
        path = ROOT / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("locale_source_not_regular:" + name)
        files[name] = {"sha256": digest(path.read_bytes()), "mode": stat.S_IMODE(info.st_mode)}
    head = subprocess.check_output(git + ["rev-parse", "HEAD"]).decode().strip()
    status = subprocess.check_output(git + ["status", "--porcelain=v1", "-z"]).decode()
    return {"root": str(ROOT), "head": head, "status": status, "files": files,
            "binding_sha256": digest(json.dumps(files, sort_keys=True).encode())}


def publish(report, stream, out):
    """Never promote a passed suite when its log/result/marker failed to publish."""
    report["log_sha256"] = None
    result_sha = None
    stream.write(f"\n{MARKER} verdict={report['verdict']} tests={report['tests']} "
                 f"passed={report['passed']} actual_rc={report['actual_rc']} "
                 f"finished_at={report['finished_at']}\n")
    try:
        log = stream.getvalue().encode()
        (out / "tests.log").write_bytes(log)
        report["log_sha256"] = digest(log)
    except Exception:
        record_error(report, traceback.format_exc())
    try:
        payload = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode()
        (out / "result.json").write_bytes(payload)
        result_sha = digest(payload)
    except Exception:
        record_error(report, traceback.format_exc())
    terminal = {"marker": MARKER, "verdict": report["verdict"], "actual_rc": report["actual_rc"],
                "tests": report["tests"], "passed": report["passed"], "result_sha256": result_sha,
                "log_sha256": report["log_sha256"], "first_cause": report["first_cause"],
                "secondary_errors": report["secondary_errors"],
                "source_binding_sha256": report["source_after"]["binding_sha256"]
                if report["source_after"] else None, "finished_at": report["finished_at"]}
    try:
        (out / "completion.json").write_bytes((json.dumps(terminal, indent=2) + "\n").encode())
    except Exception:
        record_error(report, traceback.format_exc())
        terminal.update(verdict="FAIL", actual_rc=1, first_cause=report["first_cause"],
                        secondary_errors=report["secondary_errors"])
        try:
            (out / "terminal-failure.json").write_bytes((json.dumps(terminal, indent=2) + "\n").encode())
        except Exception:
            record_error(report, traceback.format_exc())
    return report["actual_rc"]


def main():
    global RUNTIME, BEFORE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--discovery", action="store_true")
    parser.add_argument("--before", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    runtime = args.runtime_root.resolve()
    if not out.is_relative_to(Path("/hf3fs-jd/data/tmp").resolve()) or not runtime.is_relative_to(Path("/data/tmp").resolve()):
        parser.error("fresh 3FS evidence and local /data/tmp runtime required")
    out.mkdir(exist_ok=False)
    runtime.mkdir(exist_ok=False)
    RUNTIME, BEFORE = runtime, args.before
    report = {"schema": "opencad.parametric-panel-locale/1", "started_at": now(), "mode": "before" if BEFORE
              else "selftest", "runtime": str(runtime), "source_before": None, "source_after": None,
              "source_drift": None, "tests": None, "passed": None, "failures": None, "errors": None,
              "first_cause": None, "secondary_errors": [], "verdict": "FAIL", "actual_rc": 1}
    started = time.monotonic()
    stream = io.StringIO()
    try:
        report["source_before"] = source()
        if args.require_clean and report["source_before"]["status"]:
            raise ValueError("locale_source_not_clean")
        if BEFORE:
            suite = unittest.TestSuite([Controls("test_en_switch_preserves_drafts_selection_and_receipt")])
        elif args.discovery:
            module = sys.modules[__name__]
            sys.modules["test_parametric_panel_locale"] = module
            suite = unittest.defaultTestLoader.discover(str(ROOT / "oss-alternatives/apps/opencad/tests"),
                                                       pattern="test_parametric_panel_locale.py")
        else:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(Controls)
        with redirect_stdout(stream), redirect_stderr(stream):
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=OrderedResult).run(suite)
        report.update(tests=result.testsRun, passed=result.testsRun - len(result.failures) - len(result.errors),
                      failures=len(result.failures), errors=len(result.errors))
        if result.failures or result.errors:
            report["first_cause"] = result.first_cause
        else:
            report.update(verdict="PASS", actual_rc=0)
    except Exception:
        record_error(report, traceback.format_exc())
        stream.write(report["first_cause"])
    try:
        report["source_after"] = source()
        before, after = report["source_before"], report["source_after"]
        report["source_drift"] = [] if before == after else ["source_or_head_or_status_changed"]
        if report["source_drift"]:
            raise ValueError("locale_source_drift")
    except Exception:
        error = traceback.format_exc()
        record_error(report, error)
    report.update(finished_at=now(), elapsed_seconds=time.monotonic() - started)
    report["children"] = []
    try:
        for path in sorted(runtime.glob("*/binding.json")):
            report["children"].append(json.loads(path.read_bytes()))
        artifact_root = out / "actors"
        artifact_root.mkdir()
        for child in runtime.iterdir():
            if not child.is_dir() or not (child / "binding.json").exists():
                continue
            target = artifact_root / child.name
            target.mkdir()
            for name in ("binding.json", "stdout.log", "stderr.log"):
                (target / name).write_bytes((child / name).read_bytes())
    except Exception:
        record_error(report, traceback.format_exc())
    publish(report, stream, out)
    if report["actual_rc"]:
        print(report["first_cause"], file=sys.stderr)
        for error in report["secondary_errors"]:
            print(error, file=sys.stderr)
    print(f"{MARKER} verdict={report['verdict']} tests={report['tests']} passed={report['passed']} "
          f"actual_rc={report['actual_rc']} out={out}")
    return report["actual_rc"]


if __name__ == "__main__":
    sys.exit(main())
