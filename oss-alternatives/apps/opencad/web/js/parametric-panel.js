/* Isolated parameter UI. Project session owns authentication/version receipts. */
(function () {
  "use strict";
  let panel, form, fields, dimensions, configurations, result, notice, current = null;
  let busy = false, reads = 0, dirty = false;
  const inputs = new Map(), dimensionInputs = new Map();

  function report(error) {
    if (notice) notice.textContent = String(error.message || error);
  }

  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  async function request(body) {
    const response = await window.fetch("/api/parametric", body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok || value.ok !== true) {
      throw new Error((value.error || "parametric_request_failed") + ": " + (value.detail || ""));
    }
    if (!/^[0-9a-f]{64}$/.test(value.document_sha256 || "")) {
      throw new Error("parametric_response_without_document_sha");
    }
    if (!Number.isSafeInteger(value.revision) || value.revision < 1 ||
        !/^[0-9a-f]{64}$/.test(value.sha256 || "") ||
        typeof value.project_id !== "string" || !value.project_id) {
      throw new Error("parametric_response_without_project_receipt");
    }
    return value;
  }

  function markBusy(value) {
    busy = value;
    panel.setAttribute("aria-busy", String(value));
    panel.querySelectorAll("button, input, select").forEach(function (item) {
      item.disabled = value || item.dataset.readonly === "true";
    });
  }

  function render(value) {
    current = value;
    dirty = false;
    fields.replaceChildren(node("legend", "参数表达式"));
    dimensions.replaceChildren(node("legend", "驱动尺寸")); configurations.replaceChildren();
    inputs.clear(); dimensionInputs.clear();
    const base = node("option", "基础配置");
    base.value = ""; configurations.append(base);
    (value.configurations || []).forEach(function (name) {
      const option = node("option", name); option.value = name; configurations.append(option);
    });
    configurations.value = value.configuration || "";
    (value.parameters || []).forEach(function (parameter) {
      const row = node("label", undefined, "parametric-row");
      const input = node("input");
      input.type = "text"; input.value = parameter.expression; input.required = true;
      input.maxLength = 2048; input.dataset.parameter = parameter.name;
      input.setAttribute("aria-label", parameter.name + " 表达式");
      row.append(node("span", parameter.name), input,
        node("small", parameter.value.value + (parameter.unit === "length" ? " nm" : "")));
      fields.append(row);
      inputs.set(parameter.name, {input: input, original: parameter.expression});
    });
    (value.driving_dimensions || []).forEach(function (dimension) {
      const row = node("label", undefined, "parametric-row");
      const input = node("input");
      input.type = "text"; input.value = dimension.value_nm + " * nm";
      input.maxLength = 2048; input.required = true; input.dataset.dimension = dimension.key;
      input.dataset.readonly = String(!dimension.editable);
      input.disabled = !dimension.editable;
      input.setAttribute("aria-label", dimension.key + " 驱动尺寸");
      row.append(node("span", dimension.key), input); dimensions.append(row);
      dimensionInputs.set(dimension.key, {input: input, original: input.value});
    });
    form.hidden = !value.enabled;
    panel.querySelector("[data-parametric-create]").hidden = value.enabled || !value.can_create_plate;
    notice.textContent = value.enabled
      ? "长度表达式需显式单位，如 60 * mm。依赖尺寸会在一次完整重建中同步更新。"
      : "当前项目尚无参数模型。仅真空白项目可以创建带孔板，不会替换已有图纸。";
    panel.dataset.documentSha = value.document_sha256;
  }

  async function refresh(force) {
    if (busy || (!force && (dirty || form.contains(document.activeElement)))) return;
    const sequence = ++reads;
    try {
      const value = await request();
      if (sequence === reads && !busy) render(value);
      return value;
    } catch (error) {
      if (sequence === reads) notice.textContent = String(error.message || error);
      throw error;
    }
  }

  async function submit(action) {
    if (!current || busy) {
      const error = new Error("parametric_operation_in_progress"); report(error); throw error;
    }
    if (window.opencad3d && window.opencad3d.busy) {
      const error = new Error("workspace_operation_in_progress"); report(error); throw error;
    }
    const before = current;
    const body = {action: action, expected_document_sha256: current.document_sha256};
    if (action === "rebuild") {
      if (configurations.value !== (current.configuration || "")) body.configuration = configurations.value;
      body.overrides = {};
      inputs.forEach(function (item, name) {
        if (item.input.value !== item.original) body.overrides[name] = item.input.value;
      });
      body.dimensions = {};
      dimensionInputs.forEach(function (item, name) {
        if (item.input.value !== item.original) body.dimensions[name] = item.input.value;
      });
    }
    ++reads; markBusy(true);
    try {
      const value = await request(body);
      if (value.project_id !== before.project_id || value.revision < before.revision ||
          value.base_document_sha256 !== before.document_sha256) {
        throw new Error("parametric_commit_receipt_conflict");
      }
      if (!value.mesh || value.mesh.document_sha256 !== value.document_sha256 ||
          !value.step || value.step.document_sha256 !== value.document_sha256 ||
          !Array.isArray(value.mesh.solids) || !value.mesh.solids.length) {
        throw new Error("parametric_artifact_version_mismatch");
      }
      const model = window.opencad3d;
      if (model && typeof model.refresh === "function") {
        await model.refresh();
        if (model.document_sha256 !== value.document_sha256) throw new Error("parametric_display_version_conflict");
      }
      render(value);
      const volume = value.mesh.solids[0].properties.volume_nm3 / 1e18;
      result.textContent = "重建已保存 | 体积 " + volume.toFixed(6) + " mm³ | STEP " +
        value.step.bytes + " bytes | " + value.document_sha256.slice(0, 12);
      result.dataset.documentSha = value.document_sha256;
      result.dataset.volumeMm3 = String(volume);
      notice.textContent = "真实 BRep、网格和 STEP 已验证为同一文档版本。范围为单件草图/实体工作流。";
      window.dispatchEvent(new CustomEvent("opencad:parametric", {detail: value}));
      return value;
    } catch (error) {
      notice.textContent = String(error.message || error);
      throw error;
    } finally { markBusy(false); }
  }

  function mount(target) {
    if (panel) return panel;
    panel = node("section", undefined, "parametric-panel");
    panel.id = "parametric-panel";
    panel.setAttribute("aria-label", "参数化重建");
    panel.append(node("h2", "参数化重建"));
    notice = node("p", "正在读取项目参数…", "parametric-notice");
    notice.setAttribute("role", "status"); notice.setAttribute("aria-live", "polite");
    form = node("form");
    form.addEventListener("focusin", function () { ++reads; });
    form.addEventListener("input", function () { dirty = true; ++reads; });
    form.addEventListener("change", function () { dirty = true; ++reads; });
    const configurationLabel = node("label", "配置");
    configurations = node("select"); configurations.setAttribute("aria-label", "参数配置");
    configurationLabel.append(configurations);
    fields = node("fieldset");
    dimensions = node("fieldset");
    const rebuild = node("button", "完整重建"); rebuild.type = "submit";
    rebuild.dataset.parametricRebuild = "true";
    form.append(configurationLabel, fields, dimensions, rebuild);
    form.addEventListener("submit", function (event) {
      event.preventDefault(); submit("rebuild").catch(report);
    });
    const create = node("button", "创建真实带孔板"); create.type = "button";
    create.dataset.parametricCreate = "true";
    create.addEventListener("click", function () { submit("create_plate").catch(report); });
    const reload = node("button", "重读已保存参数"); reload.type = "button";
    reload.dataset.parametricReload = "true";
    reload.addEventListener("click", function () { refresh(true).catch(report); });
    result = node("output", "", "parametric-result");
    result.setAttribute("aria-live", "polite");
    panel.append(notice, create, form, reload, result); target.append(panel);
    refresh().catch(report);
    return panel;
  }

  window.opencadParametric = {mount: mount, refresh: refresh, submit: submit};
  window.addEventListener("opencad:state", function () { if (panel && !busy) refresh().catch(report); });
  function boot() {
    const target = document.querySelector("[data-parametric-host]") || document.querySelector("main > aside");
    if (target) mount(target);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, {once: true});
  else boot();
}());
