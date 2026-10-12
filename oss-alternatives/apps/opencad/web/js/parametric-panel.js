/* Parameter UI owns drafts; the private session alone owns mutation receipts. */
(function () {
  "use strict";
  let panel, form, fields, dimensions, configurations, features, result, notice, discardConfirmation;
  let current = null, specification = null, owner = null, blocked = false, expired = false;
  let busy = false, dirty = false, reads = 0, epoch = 0;
  let bindingUpdate = Promise.resolve(), bindingError = null, clearOverrides = false;
  let definitionForm, definitionTarget, definitionName, definitionParameters, definitionFeatures;
  let selectionPolicy, deleteReplacement, deleteConfirmation, definitionNotice;
  let definitionDirty = false, modelStale = false, definitionStale = false;
  let definitionMode = "edit";
  const inputs = new Map(), dimensionInputs = new Map();
  const definitionInputs = new Map(), suppressionInputs = new Map();
  const digestPattern = /^[0-9a-f]{64}$/;
  const noReplacement = ":unselected:";
  const localePrefix = "panel.parametric.";
  const localeBindings = new WeakMap();

  function localeService() {
    const service = window.OpenCADLocale;
    if (!service || typeof service.t !== "function" || typeof service.bind !== "function" ||
        typeof service.snapshot !== "function") throw new Error("parametric_locale_service_unavailable");
    return service;
  }

  function localBinding(item, key, args, target) {
    const attribute = target ? "data-panel-i18n-" + target : "data-panel-i18n";
    const fullKey = localePrefix + key;
    const translated = localeService().t(fullKey, args);
    if (args) {
      item.removeAttribute(attribute);
      let bindings = localeBindings.get(item);
      if (!bindings) { bindings = new Map(); localeBindings.set(item, bindings); }
      bindings.set(target || "text", {key: fullKey, args: Object.assign({}, args)});
      item.setAttribute("data-parametric-locale", "true");
    } else {
      const bindings = localeBindings.get(item);
      if (bindings) {
        bindings.delete(target || "text");
        if (!bindings.size) {
          localeBindings.delete(item); item.removeAttribute("data-parametric-locale");
        }
      }
      item.setAttribute(attribute, fullKey);
    }
    if (target) item.setAttribute(target === "aria" ? "aria-label" : target, translated);
    else item.textContent = translated;
    return item;
  }

  function localText(item, key, args) { return localBinding(item, key, args); }
  function localAria(item, key, args) { return localBinding(item, key, args, "aria"); }
  function localNode(tag, key, className, args) {
    return localText(node(tag, undefined, className), key, args);
  }
  function clearLocalText(item) {
    item.removeAttribute("data-panel-i18n");
    const bindings = localeBindings.get(item);
    if (bindings) {
      bindings.delete("text");
      if (!bindings.size) { localeBindings.delete(item); item.removeAttribute("data-parametric-locale"); }
    }
    item.textContent = "";
  }
  function translatePanel() {
    if (!panel) return;
    const service = localeService();
    service.bind(panel);
    panel.querySelectorAll("[data-parametric-locale]").forEach(function (item) {
      const bindings = localeBindings.get(item);
      if (!bindings) throw new Error("parametric_locale_binding_missing");
      bindings.forEach(function (binding, target) {
        const text = service.t(binding.key, binding.args);
        if (target === "text") item.textContent = text;
        else item.setAttribute(target === "aria" ? "aria-label" : target, text);
      });
    });
  }
  function shieldKey(key, permanent, args) {
    shield("", permanent);
    localText(notice, key, args);
  }
  function localError(message, key) {
    const error = new Error(message);
    error.localeKey = key;
    return error;
  }

  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function report(error) {
    if (expired) return;
    const message = String(error.message || error);
    if (notice && !(blocked && notice.textContent.includes(message))) {
      localText(notice, error.localeKey || "error", {message: message});
    }
  }

  function markBusy(value) {
    busy = value;
    panel.setAttribute("aria-busy", String(value));
    panel.querySelectorAll("button, input, select").forEach(function (item) {
      item.disabled = value || expired || !current || item.dataset.readonly === "true" ||
        (blocked && item.dataset.parametricReload !== "true" &&
          item.dataset.parametricDiscard !== "true" &&
          item.dataset.parametricConfirmDiscard !== "true" &&
          item.dataset.parametricCancelDiscard !== "true" &&
          item.dataset.definitionDiscard !== "true");
    });
    if (definitionForm && current) {
      definitionForm.querySelectorAll("[data-definition-v2]").forEach(function (item) {
        item.disabled = item.disabled || current.parametric_version !== 2;
      });
      definitionInputs.forEach(function (item) {
        item.expression.disabled = item.expression.disabled || !item.explicit.checked;
      });
    }
  }

  function shield(message, permanent) {
    ++epoch; ++reads;
    blocked = true;
    if (permanent) {
      expired = true;
      owner = null; current = null; specification = null;
      inputs.clear(); dimensionInputs.clear();
      fields.replaceChildren(); dimensions.replaceChildren(); features.replaceChildren();
      configurations.replaceChildren(); clearLocalText(result);
      definitionInputs.clear(); suppressionInputs.clear();
      definitionTarget.replaceChildren(); definitionName.value = "";
      deleteReplacement.replaceChildren();
      delete definitionForm.dataset.editingTarget;
      definitionMode = "edit"; selectionPolicy.value = "keep"; bindingError = null;
      definitionParameters.replaceChildren(); definitionFeatures.replaceChildren();
      clearLocalText(definitionNotice);
      definitionDirty = false; modelStale = false; definitionStale = false;
      delete panel.dataset.documentSha;
      delete panel.dataset.projectId; delete panel.dataset.revision;
      delete result.dataset.documentSha; delete result.dataset.volumeMm3; delete result.dataset.revision;
      dirty = false; clearOverrides = false;
    }
    form.hidden = true;
    definitionForm.hidden = true; deleteConfirmation.hidden = true;
    discardConfirmation.hidden = true;
    panel.querySelectorAll("[data-parametric-create]").forEach(function (button) {
      button.hidden = true;
    });
    clearLocalText(notice); notice.textContent = message;
    markBusy(false);
  }

  async function json(path, body) {
    const response = await window.fetch(path, body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok || value.ok !== true) {
      const error = new Error((value.error || "parametric_request_failed") + ": " + (value.detail || ""));
      error.code = value.error || "parametric_request_failed";
      error.detail = value.detail || "";
      error.status = response.status;
      throw error;
    }
    return value;
  }

  async function liveOwner() {
    const session = await json("/api/auth/me");
    const identifier = session.user && session.user.user_id;
    if (typeof identifier !== "string" || !identifier || (owner && owner !== identifier)) {
      shieldKey("identity_changed", true);
      throw new Error("parametric_identity_changed");
    }
    return identifier;
  }

  function receipt(value) {
    if (!digestPattern.test(value.document_sha256 || "") ||
        !Number.isSafeInteger(value.revision) || value.revision < 1 ||
        !digestPattern.test(value.sha256 || "") ||
        typeof value.project_id !== "string" || !value.project_id) {
      throw new Error("parametric_response_without_project_receipt");
    }
  }

  function sameHead(a, b) {
    return a.project_id === b.project_id && a.revision === b.revision && a.sha256 === b.sha256;
  }

  async function readBound() {
    const identifier = await liveOwner();
    const value = await json("/api/parametric");
    receipt(value);
    const state = await json("/api/state");
    const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(state.doc));
    const digest = Array.from(new Uint8Array(hash), function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
    if (!sameHead(value, state) || digest !== value.document_sha256) {
      throw new Error("parametric_read_version_conflict");
    }
    if (await liveOwner() !== identifier) throw new Error("parametric_identity_changed");
    return {value: value, spec: JSON.parse(state.doc).parametric || null, owner: identifier};
  }

  function featurePreview() {
    features.replaceChildren(localNode("legend", "feature_suppression"));
    if (!current || current.parametric_version !== 2) {
      features.append(localNode("p", "v1_preview"));
      return;
    }
    const selected = configurations.value;
    const flags = (current.configuration_suppressions || {})[selected] || {};
    Object.entries(current.feature_bindings || {}).forEach(function (entry) {
      const key = entry[0], feature = entry[1];
      const suppressed = Object.prototype.hasOwnProperty.call(flags, key)
        ? flags[key] : feature.base_suppressed;
      const row = node("p", undefined, "parametric-feature");
      row.dataset.feature = key; row.dataset.suppressed = String(suppressed);
      row.append(key === "hole_cut" || key === "plate_boss" ? localNode("strong", key) : node("strong", key),
      localNode("span", suppressed ? "suppressed" : "live"),
      node("small", "UUID " + feature.target_uuid));
      features.append(row);
    });
    features.append(localNode("small", "preview_help"));
  }

  function render(bound, preserve, preserveDefinition) {
    const value = bound.value;
    owner = bound.owner; current = value; specification = bound.spec;
    blocked = false; bindingError = null;
    if (!preserve) {
      dirty = false; clearOverrides = false;
      fields.replaceChildren(localNode("legend", "expressions"));
      dimensions.replaceChildren(localNode("legend", "dimensions"));
      configurations.replaceChildren();
      inputs.clear(); dimensionInputs.clear();
      const base = localNode("option", "base"); base.value = ""; configurations.append(base);
      (value.configurations || []).forEach(function (name) {
        const option = node("option", name); option.value = name; configurations.append(option);
      });
      configurations.value = value.configuration || "";
      (value.parameters || []).forEach(function (parameter) {
        const row = node("label", undefined, "parametric-row"), input = node("input");
        input.type = "text"; input.value = parameter.expression; input.required = true;
        input.maxLength = 2048; input.dataset.parameter = parameter.name;
        localAria(input, "expression_aria", {name: parameter.name});
        row.append(node("span", parameter.name), input,
          node("small", parameter.value.value + (parameter.unit === "length" ? " nm" : "")));
        fields.append(row);
        inputs.set(parameter.name, {input: input, original: parameter.expression});
      });
      (value.driving_dimensions || []).forEach(function (dimension) {
        const row = node("label", undefined, "parametric-row"), input = node("input");
        input.type = "text"; input.value = dimension.value_nm + " * nm";
        input.maxLength = 2048; input.required = true; input.dataset.dimension = dimension.key;
        input.dataset.readonly = String(!dimension.editable);
        localAria(input, "dimension_aria", {name: dimension.key});
        row.append(node("span", dimension.key), input); dimensions.append(row);
        dimensionInputs.set(dimension.key, {input: input, original: input.value});
      });
    }
    featurePreview();
    if (!preserveDefinition) renderDefinition();
    definitionForm.hidden = !value.enabled;
    form.hidden = !value.enabled;
    panel.querySelectorAll("[data-parametric-create]").forEach(function (button) {
      button.hidden = value.enabled || !value.can_create_plate;
    });
    localText(notice, preserve ? "preserved" : value.enabled ? "enabled_help" : "blank_help");
    panel.dataset.documentSha = value.document_sha256;
    panel.dataset.projectId = value.project_id;
    panel.dataset.revision = String(value.revision);
    blocked = modelStale || definitionStale;
    if (blocked) localText(notice, "other_draft_stale");
    markBusy(busy);
  }

  async function refresh(force) {
    if (expired || busy || (!force && (dirty || definitionDirty ||
        form.contains(document.activeElement) || definitionForm.contains(document.activeElement)))) return;
    const sequence = ++reads, generation = epoch, before = current;
    try {
      const bound = await readBound();
      if (generation !== epoch || sequence !== reads || busy || expired) return;
      if (before && bound.value.project_id !== before.project_id) {
        shieldKey("project_changed", true);
        throw new Error("parametric_project_changed");
      }
      if ((dirty || definitionDirty) && before && !sameHead(before, bound.value)) {
        blocked = true; bindingError = new Error("parametric_draft_base_changed");
        localText(notice, "draft_stale");
        form.hidden = false; markBusy(false);
        definitionForm.hidden = false;
        panel.querySelector("[data-parametric-discard]").disabled = false;
        return;
      }
      render(bound, dirty, definitionDirty);
      return bound.value;
    } catch (error) {
      if (generation === epoch && !expired && (sequence === reads ||
          error.status === 401 || error.status === 403)) {
        if (error.status === 401 || error.status === 403) {
          shieldKey("session_expired", true);
        } else {
          shieldKey("read_unverified", false, {message: error.message});
        }
      }
      throw error;
    }
  }

  function modelDraft() {
    return JSON.stringify({configuration: configurations.value, clearOverrides: clearOverrides,
      parameters: Array.from(inputs, function (entry) { return [entry[0], entry[1].input.value]; }),
      dimensions: Array.from(dimensionInputs, function (entry) { return [entry[0], entry[1].input.value]; })});
  }

  function definitionDraft() {
    return JSON.stringify({mode: definitionMode, target: definitionTarget.value, name: definitionName.value,
      selection: selectionPolicy.value, replacement: deleteReplacement.value,
      parameters: Array.from(definitionInputs, function (entry) {
        return [entry[0], entry[1].explicit.checked, entry[1].expression.value];
      }), suppressions: Array.from(suppressionInputs, function (entry) {
        return [entry[0], entry[1].value];
      })});
  }

  function definitionState(value) {
    return JSON.stringify({version: value.parametric_version,
      definitions: value.configuration_definitions, suppressions: value.configuration_suppressions,
      bindings: Object.entries(value.feature_bindings || {}).map(function (entry) {
        return [entry[0], entry[1].target_uuid, entry[1].base_suppressed];
      })});
  }

  function effectiveState(value) {
    return JSON.stringify({parameters: value.parameters, configuration: value.configuration,
      dimensions: value.driving_dimensions, features: value.feature_bindings});
  }

  function definitionBody(action) {
    const name = definitionMode === "create" ? definitionName.value : definitionTarget.value;
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name)) throw new Error("configuration_name_invalid");
    if (action === "create_configuration" && definitionMode !== "create") {
      throw new Error("configuration_create_mode_required");
    }
    if (action !== "create_configuration" && !Object.prototype.hasOwnProperty.call(
        current.configuration_definitions || {}, name)) throw new Error("configuration_target_missing");
    const parameters = {}, suppressions = {};
    definitionInputs.forEach(function (item, key) {
      if (item.explicit.checked) {
        if (!item.expression.value.trim()) throw new Error("configuration_expression_required");
        parameters[key] = item.expression.value;
      }
    });
    suppressionInputs.forEach(function (item, key) {
      if (item.value !== "inherit") {
        if (item.value !== "live" && item.value !== "suppressed") throw new Error("suppression_boolean_required");
        suppressions[key] = item.value === "suppressed";
      }
    });
    const body = {name: name};
    if (action === "create_configuration" || action === "replace_configuration") {
      body.definition = {parameters: parameters};
      if (current.parametric_version === 2) body.definition.suppressions = suppressions;
    } else if (action === "set_suppressions") {
      if (current.parametric_version !== 2) throw new Error("suppression_requires_v2");
      const stored = (current.configuration_definitions || {})[name] || {};
      if (Object.keys(parameters).length !== Object.keys(stored).length ||
          Object.keys(parameters).some(function (key) { return parameters[key] !== stored[key]; })) {
        throw localError("参数定义草稿未保存，请使用完整替换，不能仅保存抑制。", "definition_unsaved_error");
      }
      body.suppressions = suppressions;
    } else if (action !== "delete_configuration") {
      throw new Error("definition_action_invalid");
    }
    if (action === "delete_configuration") {
      if (current.configuration === name && deleteReplacement.value === noReplacement) {
        throw localError("删除活动配置必须明确选择替代配置或基础。", "replacement_required_error");
      }
      if (deleteReplacement.value !== noReplacement) {
        if (deleteReplacement.value === name) throw new Error("configuration_delete_target_selection");
        body.configuration = deleteReplacement.value;
      }
    } else if (selectionPolicy.value === "target") {
      body.configuration = name;
    } else if (selectionPolicy.value === "base") {
      body.configuration = "";
    } else if (selectionPolicy.value !== "keep") {
      throw new Error("configuration_selection_required");
    }
    return body;
  }

  async function submit(action) {
    await bindingUpdate;
    if (bindingError) { report(bindingError); throw bindingError; }
    if (!current || busy || blocked || expired) throw new Error("parametric_operation_unavailable");
    if (window.opencad3d && window.opencad3d.busy) throw new Error("workspace_operation_in_progress");
    const before = current, generation = epoch;
    const body = {action: action, expected_document_sha256: before.document_sha256};
    const editingDefinition = ["create_configuration", "replace_configuration",
      "delete_configuration", "set_suppressions"].includes(action);
    const submittedModel = modelDraft(), submittedDefinition = definitionDraft();
    const modelWasDirty = dirty, definitionWasDirty = definitionDirty;
    if (editingDefinition) Object.assign(body, definitionBody(action));
    if (action === "rebuild") {
      const switching = configurations.value !== (before.configuration || "");
      if (switching || clearOverrides) body.configuration = configurations.value;
      body.overrides = {}; body.dimensions = {};
      if (clearOverrides && !switching) {
        inputs.forEach(function (item, name) {
          const baseExpression = (specification.configurations[configurations.value] || {})[name] ||
            specification.parameters[name].expression;
          if (item.input.value !== baseExpression) body.overrides[name] = item.input.value;
        });
      } else {
        inputs.forEach(function (item, name) {
          if (item.input.value !== item.original) body.overrides[name] = item.input.value;
        });
      }
      dimensionInputs.forEach(function (item, name) {
        if (item.input.value !== item.original) body.dimensions[name] = item.input.value;
      });
    }
    ++reads; markBusy(true);
    let accepted = false;
    try {
      await liveOwner();
      if (generation !== epoch || expired) throw new Error("parametric_context_changed");
      const value = await json("/api/parametric", body);
      accepted = true; receipt(value);
      if (generation !== epoch || expired || value.project_id !== before.project_id ||
          value.revision !== before.revision + 1 ||
          value.base_document_sha256 !== before.document_sha256) {
        throw new Error("parametric_commit_receipt_conflict");
      }
      if (!value.mesh || value.mesh.document_sha256 !== value.document_sha256 ||
          !value.step || value.step.document_sha256 !== value.document_sha256 ||
          !Array.isArray(value.mesh.solids) || !value.mesh.solids.length) {
        throw new Error("parametric_artifact_version_mismatch");
      }
      await liveOwner();
      const model = window.opencad3d;
      if (!model || typeof model.refresh !== "function") throw new Error("parametric_view_unavailable");
      const displayed = await model.refresh();
      if (!sameHead(value, displayed) || model.document_sha256 !== value.document_sha256) {
        throw new Error("parametric_display_version_conflict");
      }
      const bound = await readBound();
      if (!sameHead(bound.value, value) || generation !== epoch || expired) {
        throw new Error("parametric_display_version_conflict");
      }
      const keepModel = editingDefinition ? modelWasDirty || modelDraft() !== submittedModel :
        modelDraft() !== submittedModel;
      const keepDefinition = editingDefinition ? definitionDraft() !== submittedDefinition :
        definitionWasDirty || definitionDraft() !== submittedDefinition;
      dirty = keepModel; definitionDirty = keepDefinition;
      modelStale = keepModel && effectiveState(bound.value) !== effectiveState(before);
      definitionStale = keepDefinition && definitionState(bound.value) !== definitionState(before);
      render(bound, keepModel, keepDefinition);
      deleteConfirmation.hidden = true;
      const volume = value.mesh.solids[0].properties.volume_nm3 / 1e18;
      if (!Number.isFinite(volume) || volume <= 0) throw new Error("parametric_volume_invalid");
      localText(result, "rebuild_result", {volume: volume.toFixed(6),
        bytes: value.step.bytes, sha: value.document_sha256.slice(0, 12)});
      result.dataset.documentSha = value.document_sha256;
      result.dataset.volumeMm3 = String(volume);
      result.dataset.revision = String(value.revision);
      localText(notice, blocked ? "saved_other_draft" : "artifacts_verified");
      window.dispatchEvent(new CustomEvent("opencad:parametric", {detail: value}));
      return value;
    } catch (error) {
      if (generation === epoch && !expired) {
        if (error.status === 401 || error.status === 403) {
          shieldKey("session_expired", true);
        } else {
          blocked = true;
          localText(notice, accepted ? "saved_unverified" : "edit_unverified", {message: error.message});
        }
      }
      throw error;
    } finally {
      if (generation === epoch) {
        markBusy(false);
        if (blocked && !expired) panel.querySelector("[data-parametric-discard]").disabled = false;
      }
    }
  }

  async function save() {
    await bindingUpdate;
    if (!current || busy || blocked || expired || bindingError) {
      throw new Error("parametric_operation_unavailable");
    }
    const before = current, generation = epoch;
    ++reads; markBusy(true);
    try {
      await liveOwner();
      if (generation !== epoch || expired) throw new Error("parametric_context_changed");
      const saved = await json("/api/save", {});
      if (saved.project_id !== before.project_id || saved.revision !== before.revision + 1) {
        throw new Error("parametric_commit_receipt_conflict");
      }
      await window.opencad3d.refresh();
      const bound = await readBound();
      if (generation !== epoch || expired || !sameHead(bound.value, saved) ||
          parameterState(bound.value) !== parameterState(before)) {
        throw new Error("parametric_save_version_conflict");
      }
      render(bound, dirty, definitionDirty);
      localText(notice, "model_saved");
      return saved;
    } catch (error) {
      if (generation === epoch && !expired) {
        if (error.status === 401 || error.status === 403) {
          shieldKey("session_expired", true);
        } else {
          blocked = true; report(error);
        }
      }
      throw error;
    } finally {
      if (generation === epoch) {
        markBusy(false);
        if (blocked && !expired) panel.querySelector("[data-parametric-discard]").disabled = false;
      }
    }
  }

  function renderDefinition() {
    definitionDirty = false; definitionStale = false;
    definitionInputs.clear(); suppressionInputs.clear();
    definitionTarget.replaceChildren();
    Object.keys(current.configuration_definitions || {}).sort().forEach(function (name) {
      const option = node("option", name); option.value = name; definitionTarget.append(option);
    });
    definitionTarget.value = current.configuration || definitionTarget.options[0]?.value || "";
    definitionForm.dataset.editingTarget = definitionTarget.value;
    definitionMode = "edit"; definitionName.value = "";
    selectionPolicy.value = "keep";
    if (!current.enabled || !specification) {
      definitionParameters.replaceChildren(); definitionFeatures.replaceChildren();
      clearLocalText(definitionNotice);
      deleteConfirmation.hidden = true;
      return;
    }
    loadDefinitionFields();
  }

  function loadDefinitionFields() {
    const name = definitionMode === "edit" ? definitionTarget.value : "";
    const stored = (current.configuration_definitions || {})[name] || {};
    const flags = (current.configuration_suppressions || {})[name] || {};
    definitionInputs.clear(); suppressionInputs.clear();
    definitionParameters.replaceChildren(localNode("legend", "definition_parameters"));
    Object.entries(specification.parameters || {}).forEach(function (entry) {
      const key = entry[0], row = node("div", undefined, "parametric-definition-row");
      const label = node("label", key), explicit = node("input"), expression = node("input");
      explicit.type = "checkbox"; explicit.checked = Object.prototype.hasOwnProperty.call(stored, key);
      explicit.dataset.definitionExplicit = key; localAria(explicit, "explicit_aria", {name: key});
      expression.type = "text"; expression.maxLength = 2048;
      expression.value = explicit.checked ? stored[key] : entry[1].expression;
      expression.dataset.definitionParameter = key; localAria(expression, "definition_expression_aria", {name: key});
      label.append(explicit, localNode("span", "explicit_help"));
      row.append(label, expression);
      definitionParameters.append(row); definitionInputs.set(key, {explicit: explicit, expression: expression});
    });
    definitionFeatures.replaceChildren(localNode("legend", "definition_suppressions"));
    if (current.parametric_version === 2) {
      Object.entries(current.feature_bindings || {}).forEach(function (entry) {
        const key = entry[0], feature = entry[1], row = node("label", undefined, "parametric-row");
        const select = node("select"); select.dataset.definitionSuppression = key;
        select.dataset.definitionV2 = "true"; localAria(select, "suppression_aria", {name: key});
        [["inherit", feature.base_suppressed ? "inherit_suppressed" : "inherit_live"],
          ["live", "live"], ["suppressed", "suppress"]].forEach(function (item) {
          const option = localNode("option", item[1]); option.value = item[0]; select.append(option);
        });
        select.value = Object.prototype.hasOwnProperty.call(flags, key)
          ? flags[key] ? "suppressed" : "live" : "inherit";
        row.append(node("span", key), select, node("small", "UUID " + feature.target_uuid));
        definitionFeatures.append(row); suppressionInputs.set(key, select);
      });
    } else definitionFeatures.append(localNode("p", "v1_definition"));
    deleteReplacement.replaceChildren();
    const unselected = localNode("option", "replacement_unselected");
    unselected.value = noReplacement; deleteReplacement.append(unselected);
    const base = localNode("option", "replacement_base"); base.value = ""; deleteReplacement.append(base);
    Object.keys(current.configuration_definitions || {}).sort().filter(function (key) {
      return key !== name;
    }).forEach(function (key) {
      const option = localNode("option", "replacement_named", undefined, {name: key});
      option.value = key; deleteReplacement.append(option);
    });
    deleteReplacement.value = noReplacement; deleteConfirmation.hidden = true;
    localText(definitionNotice, definitionMode === "create" ? "definition_create_help" : "definition_replace_help");
    definitionForm.querySelector("[data-definition-create]").hidden = definitionMode !== "create";
    definitionForm.querySelector("[data-definition-replace]").hidden = definitionMode !== "edit";
    definitionForm.querySelector("[data-definition-suppressions]").hidden = definitionMode !== "edit";
    definitionForm.querySelector("[data-definition-delete]").hidden = definitionMode !== "edit";
    definitionTarget.hidden = definitionMode !== "edit"; definitionName.hidden = definitionMode !== "create";
    markBusy(busy);
  }

  function mountDefinition() {
    definitionForm = node("form", undefined, "parametric-definition-editor");
    localAria(definitionForm, "definition_aria");
    definitionForm.addEventListener("submit", function (event) { event.preventDefault(); });
    definitionForm.append(localNode("h3", "definition_title"));
    definitionTarget = node("select"); definitionTarget.dataset.definitionTarget = "true";
    localAria(definitionTarget, "definition_target_aria");
    definitionName = node("input"); definitionName.type = "text"; definitionName.maxLength = 64;
    definitionName.dataset.definitionName = "true"; localAria(definitionName, "definition_name_aria");
    definitionTarget.addEventListener("change", function () {
      if (definitionDirty) {
        definitionTarget.value = definitionForm.dataset.editingTarget || "";
        localText(definitionNotice, "definition_switch_blocked");
        return;
      }
      definitionForm.dataset.editingTarget = definitionTarget.value; loadDefinitionFields();
    });
    const createMode = localNode("button", "definition_new"); createMode.type = "button";
    createMode.dataset.definitionNew = "true";
    createMode.addEventListener("click", function () {
      if (definitionDirty) { localText(definitionNotice, "definition_dirty"); return; }
      definitionMode = "create"; definitionDirty = true; definitionName.value = "";
      loadDefinitionFields();
    });
    selectionPolicy = node("select"); selectionPolicy.dataset.definitionSelection = "true";
    localAria(selectionPolicy, "selection_aria");
    [["keep", "selection_keep"], ["target", "selection_target"],
      ["base", "selection_base"]].forEach(function (item) {
      const option = localNode("option", item[1]); option.value = item[0]; selectionPolicy.append(option);
    });
    definitionParameters = node("fieldset"); definitionFeatures = node("fieldset");
    definitionNotice = node("p", undefined, "parametric-definition-notice");
    definitionNotice.setAttribute("role", "status");
    const buttons = node("div", undefined, "parametric-actions");
    [["create_configuration", "create", "definitionCreate"],
      ["replace_configuration", "replace", "definitionReplace"],
      ["set_suppressions", "save_suppressions", "definitionSuppressions"]].forEach(function (item) {
      const button = localNode("button", item[1]); button.type = "button"; button.dataset[item[2]] = "true";
      if (item[0] === "set_suppressions") button.dataset.definitionV2 = "true";
      button.addEventListener("click", function () { submit(item[0]).catch(report); });
      buttons.append(button);
    });
    const remove = localNode("button", "delete"); remove.type = "button";
    remove.dataset.definitionDelete = "true";
    remove.addEventListener("click", function () {
      if (!definitionTarget.value) { report(localError("没有可删除的配置", "no_configuration_error")); return; }
      if (definitionDirty) {
        localText(definitionNotice, "definition_delete_dirty"); return;
      }
      deleteConfirmation.hidden = false;
    });
    deleteConfirmation = node("div", undefined, "parametric-delete-confirmation");
    deleteConfirmation.hidden = true; deleteConfirmation.setAttribute("role", "group");
    localAria(deleteConfirmation, "delete_aria");
    deleteReplacement = node("select"); deleteReplacement.dataset.definitionReplacement = "true";
    localAria(deleteReplacement, "replacement_aria");
    const confirm = localNode("button", "confirm_delete"); confirm.type = "button";
    confirm.dataset.definitionConfirmDelete = "true";
    confirm.addEventListener("click", function () { submit("delete_configuration").catch(report); });
    const cancel = localNode("button", "cancel_delete"); cancel.type = "button";
    cancel.dataset.definitionCancelDelete = "true";
    cancel.addEventListener("click", function () { deleteConfirmation.hidden = true; });
    deleteConfirmation.append(localNode("p", "delete_help"),
      deleteReplacement, confirm, cancel);
    const discard = localNode("button", "discard_definition"); discard.type = "button";
    discard.dataset.definitionDiscard = "true";
    discard.addEventListener("click", function () {
      definitionDirty = false; definitionStale = false; bindingError = null;
      renderDefinition(); refresh(true).catch(report);
    });
    ["input", "change"].forEach(function (event) {
      definitionForm.addEventListener(event, function (e) {
        if (e.target === definitionTarget || e.target === deleteReplacement) return;
        definitionDirty = true; ++reads; deleteConfirmation.hidden = true; markBusy(busy);
      });
    });
    definitionForm.addEventListener("focusin", function () { ++reads; });
    definitionForm.append(definitionTarget, definitionName, createMode, definitionNotice,
      definitionParameters, definitionFeatures, selectionPolicy, buttons, remove, deleteConfirmation, discard);
  }

  function mount(target) {
    if (panel) return panel;
    panel = node("section", undefined, "parametric-panel"); panel.id = "parametric-panel";
    localAria(panel, "panel_aria");
    panel.append(localNode("h2", "title"));
    notice = localNode("p", "loading", "parametric-notice");
    notice.setAttribute("role", "status"); notice.setAttribute("aria-live", "polite");
    form = node("form");
    form.addEventListener("focusin", function () { ++reads; });
    ["input", "change"].forEach(function (name) {
      form.addEventListener(name, function () { dirty = true; ++reads; featurePreview(); });
    });
    const label = node("label");
    configurations = node("select"); localAria(configurations, "configuration_aria");
    label.append(localNode("span", "configuration"), configurations);
    fields = node("fieldset"); dimensions = node("fieldset"); features = node("fieldset");
    const rebuild = localNode("button", "rebuild"); rebuild.type = "submit";
    rebuild.dataset.parametricRebuild = "true";
    const reset = localNode("button", "reset_overrides"); reset.type = "button";
    reset.dataset.parametricResetOverrides = "true";
    reset.addEventListener("click", function () {
      if (!specification) return;
      clearOverrides = true; dirty = true;
      inputs.forEach(function (item, name) {
        item.input.value = (specification.configurations[configurations.value] || {})[name] ||
          specification.parameters[name].expression;
      });
      dimensionInputs.forEach(function (item) { item.input.value = item.original; });
      localText(notice, "reset_ready");
    });
    const discard = localNode("button", "discard_reload"); discard.type = "button";
    discard.dataset.parametricDiscard = "true";
    discard.addEventListener("click", function () {
      discardConfirmation.hidden = false;
      discardConfirmation.querySelector("[data-parametric-cancel-discard]").focus();
    });
    discardConfirmation = node("div", undefined, "parametric-discard-confirmation");
    discardConfirmation.hidden = true;
    discardConfirmation.setAttribute("role", "group");
    localAria(discardConfirmation, "discard_aria");
    const confirmDiscard = localNode("button", "confirm_discard"); confirmDiscard.type = "button";
    confirmDiscard.dataset.parametricConfirmDiscard = "true";
    confirmDiscard.addEventListener("click", function () {
      discardConfirmation.hidden = true;
      dirty = false; clearOverrides = false; bindingError = null;
      modelStale = false;
      refresh(true).catch(report);
    });
    const cancelDiscard = localNode("button", "keep_drafts"); cancelDiscard.type = "button";
    cancelDiscard.dataset.parametricCancelDiscard = "true";
    cancelDiscard.addEventListener("click", function () {
      discardConfirmation.hidden = true;
      discard.focus();
    });
    discardConfirmation.append(localNode("p", "discard_help"),
      confirmDiscard, cancelDiscard);
    form.append(label, features, fields, dimensions, reset, rebuild, discard, discardConfirmation);
    form.addEventListener("submit", function (event) {
      event.preventDefault(); submit("rebuild").catch(report);
    });
    const actions = node("div", undefined, "parametric-actions");
    [["create_configurable_plate", "create_v2"], ["create_plate", "create_v1"]]
      .forEach(function (item) {
        const create = localNode("button", item[1]); create.type = "button";
        create.dataset.parametricCreate = item[0];
        create.addEventListener("click", function () { submit(item[0]).catch(report); });
        actions.append(create);
      });
    const reload = localNode("button", "reload"); reload.type = "button";
    reload.dataset.parametricReload = "true";
    reload.addEventListener("click", function () { refresh(true).catch(report); });
    const saveButton = localNode("button", "save"); saveButton.type = "button";
    saveButton.dataset.parametricSave = "true";
    saveButton.addEventListener("click", function () { save().catch(report); });
    result = node("output", "", "parametric-result"); result.setAttribute("aria-live", "polite");
    mountDefinition();
    panel.append(notice, actions, form, definitionForm, saveButton, reload, result); target.append(panel);
    markBusy(false);
    refresh(true).catch(report);
    return panel;
  }

  window.opencadParametric = {mount: mount, refresh: refresh, submit: submit};
  function parameterState(value) {
    return JSON.stringify({
      enabled: value.enabled, can_create_plate: value.can_create_plate,
      parameters: value.parameters, configuration: value.configuration,
      configurations: value.configurations, driving_dimensions: value.driving_dimensions,
      feature_bindings: value.feature_bindings,
      configuration_suppressions: value.configuration_suppressions,
      configuration_definitions: value.configuration_definitions
    });
  }
  window.addEventListener("opencad:project-commit", function (event) {
    const detail = event.detail;
    if (!panel || !current || !detail || busy || expired) return;
    ++reads;
    const generation = epoch;
    bindingUpdate = bindingUpdate.then(async function () {
      const before = current, receipt = detail.receipt, base = detail.base;
      try {
        const bound = await readBound(), value = bound.value;
        if (generation !== epoch || expired || current !== before) return;
        if (!base || !receipt || base.project_id !== before.project_id ||
            base.expected_revision !== before.revision || base.expected_sha !== before.sha256 ||
            receipt.revision !== base.expected_revision + 1 || !sameHead(value, receipt)) {
          throw new Error("parametric_commit_receipt_conflict");
        }
        if (dirty && parameterState(value) !== parameterState(before)) {
          throw new Error("parametric_draft_base_changed");
        }
        if (definitionDirty && definitionState(value) !== definitionState(before)) {
          throw new Error("parametric_definition_draft_base_changed");
        }
        render(bound, dirty, definitionDirty);
      } catch (error) {
        if (generation === epoch && current === before && !expired) {
          if (error.status === 401 || error.status === 403) {
            shieldKey("session_expired", true);
            return;
          }
          bindingError = error; blocked = true; report(error); markBusy(false);
          panel.querySelector("[data-parametric-discard]").disabled = false;
        }
      }
    });
  });
  window.addEventListener("opencad:state", function (event) {
    if (!panel || expired) return;
    if (current && event.detail && event.detail.project_id !== current.project_id) {
      shieldKey("project_changed", true);
    } else if (!busy) refresh(false).catch(report);
  });
  window.addEventListener("opencad:locale", translatePanel);
  function boot() {
    const service = localeService();
    Promise.resolve(service.ready).then(function () {
      const target = document.querySelector("[data-parametric-host]") || document.querySelector("main > aside");
      if (target) mount(target);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
}());
