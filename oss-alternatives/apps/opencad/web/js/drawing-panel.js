/* Explicitly mounted associative drawing panel; project-session owns CAS/auth. */
(function () {
  "use strict";

  function mount(host, options) {
    if (!host || host.nodeType !== 1) { throw new Error("drawing_mount_node_required"); }
    const locale = window.OpenCADLocale;
    if (!locale || !locale.ready || typeof locale.ready.then !== "function"
        || typeof locale.t !== "function" || typeof locale.bind !== "function"
        || typeof locale.snapshot !== "function") {
      throw new Error("drawing_locale_service_required");
    }
    options = options || {};
    const request = options.fetch || window.fetch.bind(window);
    const panel = document.createElement("section");
    panel.className = "drawing-panel";
    panel.setAttribute("data-panel-i18n-aria", "panel.drawing.heading");
    panel.innerHTML =
      '<header><p class="drawing-panel-kicker" data-panel-i18n="panel.drawing.kicker"></p>' +
      '<h2 data-panel-i18n="panel.drawing.heading"></h2>' +
      '<p data-panel-i18n="panel.drawing.scope"></p></header>' +
      '<form class="drawing-panel-form"><label><span data-panel-i18n="panel.drawing.definition"></span>' +
      '<textarea class="drawing-definition" data-panel-i18n-aria="panel.drawing.definition" ' +
      'spellcheck="false" rows="8" required></textarea></label>' +
      '<div class="drawing-panel-actions"><button type="submit" data-panel-i18n="panel.drawing.save"></button>' +
      '<button type="button" data-drawing-refresh data-panel-i18n="panel.drawing.refresh"></button></div></form>' +
      '<output class="drawing-panel-status" role="status" aria-live="polite">' +
      '<span></span><span data-panel-i18n="panel.drawing.status.unread"></span></output>' +
      '<div class="drawing-panel-binding"></div>' +
      '<div class="drawing-panel-preview" data-panel-i18n-aria="panel.drawing.preview_aria"></div>' +
      '<div class="drawing-panel-table" tabindex="0" data-panel-i18n-aria="panel.drawing.table_scroll_aria">' +
      '<table data-panel-i18n-aria="panel.drawing.table_aria"><thead><tr>' +
      '<th scope="col" data-panel-i18n="panel.drawing.dimension"></th>' +
      '<th scope="col" data-panel-i18n="panel.drawing.measured_value"></th>' +
      '<th scope="col" data-panel-i18n="panel.drawing.model_reference"></th></tr></thead><tbody></tbody></table></div>' +
      '<div class="drawing-panel-actions drawing-panel-exports">' +
      '<button type="button" data-drawing-export="svg" data-panel-i18n="panel.drawing.download_svg"></button>' +
      '<button type="button" data-drawing-export="pdf" data-panel-i18n="panel.drawing.download_pdf"></button>' +
      '<button type="button" data-drawing-export="dxf" data-panel-i18n="panel.drawing.download_dxf"></button></div>';
    host.appendChild(panel);
    const editor = panel.querySelector("textarea");
    const status = panel.querySelector("output");
    const prefix = status.children[0];
    const message = status.children[1];
    const binding = panel.querySelector(".drawing-panel-binding");
    const preview = panel.querySelector(".drawing-panel-preview");
    const body = panel.querySelector("tbody");
    let generation = 0;
    let pending = false;
    let dirty = false;
    let current = null;
    let disposed = false;
    let refreshQueued = false;
    let localeBound = false;
    let unbind = null;
    let modelBinding = null;
    editor.value = JSON.stringify(options.definition || {
      title: "Associative drawing", views: ["top", "front", "right"],
      sections: [], dimensions: [], bom_refs: []
    }, null, 2);

    function text(node, key, args) {
      const full = "panel.drawing." + key;
      const translated = locale.t(full, args);
      node.setAttribute("data-panel-i18n", full);
      if (args === undefined) { node.removeAttribute("data-panel-i18n-args"); }
      else { node.setAttribute("data-panel-i18n-args", JSON.stringify(args)); }
      node.textContent = translated;
    }

    function literal(node, value) {
      node.removeAttribute("data-panel-i18n");
      node.removeAttribute("data-panel-i18n-args");
      node.textContent = value;
    }

    function notice(key, args) {
      literal(prefix, "");
      text(message, key, args);
    }

    function localError(key) {
      const error = new Error("drawing_" + key.replace(/\./g, "_"));
      error.localeKey = key;
      return error;
    }

    function requestError(value, fallback) {
      if (!value.detail && !value.error) { return localError(fallback); }
      const detail = value.detail || "";
      const code = value.error || "";
      return new Error(code && detail && code !== detail ? code + ": " + detail : detail || code);
    }

    function showError(error, key) {
      // Locale failures are infrastructure errors, never disguised as CAD errors.
      if (/^locale_/.test(error.code || "")) { throw error; }
      text(prefix, key || "error.prefix");
      if (error.localeKey) { text(message, error.localeKey); }
      else { literal(message, error.message); }
    }

    function controls(busy) {
      pending = busy;
      panel.setAttribute("aria-busy", String(busy));
      panel.querySelectorAll("button").forEach(function (button) {
        button.disabled = busy || (button.hasAttribute("data-drawing-export") && !current);
      });
    }

    function clear(reason) {
      current = null;
      preview.replaceChildren();
      body.replaceChildren();
      if (reason) { text(binding, reason); }
      else { literal(binding, ""); }
      controls(pending);
    }

    async function json(path, payload) {
      const response = await request(path, payload === undefined ? {
        credentials: "same-origin"
      } : {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-OpenCAD-Request": "1"},
        body: JSON.stringify(payload)
      });
      const value = await response.json();
      if (!response.ok || value.ok !== true) {
        throw requestError(value, "error.request_unconfirmed");
      }
      return value;
    }

    function checkedSvg(text) {
      const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
      if (parsed.querySelector("parsererror") || parsed.documentElement.localName !== "svg") {
        throw localError("error.svg_parse");
      }
      const allowed = new Set(["svg", "title", "rect", "g", "line", "circle", "text"]);
      parsed.querySelectorAll("*").forEach(function (node) {
        if (!allowed.has(node.localName) || node.namespaceURI !== "http://www.w3.org/2000/svg") {
          throw localError("error.svg_node");
        }
        Array.from(node.attributes).forEach(function (attribute) {
          if (/^on/i.test(attribute.name) || /href|style/i.test(attribute.name)
              || /url\s*\(/i.test(attribute.value)) {
            throw localError("error.svg_attribute");
          }
        });
      });
      return document.importNode(parsed.documentElement, true);
    }

    function render(value) {
      if (!value.defined) {
        modelBinding = stateBinding(value);
        clear("binding.undefined");
        notice("status.define_first");
        return;
      }
      if (!Number.isSafeInteger(value.model_version)
          || !/^[a-f0-9]{64}$/.test(value.source_model_sha256 || "")
          || !/^[a-f0-9]{64}$/.test(value.source_document_sha256 || "")
          || !/^[a-f0-9]{64}$/.test(value.drawing_sha256 || "")
          || value.revision !== value.model_version
          || typeof value.svg !== "string" || !Array.isArray(value.dimensions)) {
        throw localError("error.binding");
      }
      const image = checkedSvg(value.svg);
      current = value;
      modelBinding = stateBinding(value);
      text(binding, "binding.version", {revision: value.revision, sha: value.source_model_sha256});
      preview.replaceChildren(image);
      body.replaceChildren();
      value.dimensions.forEach(function (dimension) {
        const row = document.createElement("tr");
        [dimension.id, String(dimension.value_nm / 1000000) + " mm",
          dimension.ref.join(" / ")].forEach(function (text) {
          const cell = document.createElement("td");
          cell.textContent = text;
          row.appendChild(cell);
        });
        body.appendChild(row);
      });
      if (!dirty) { editor.value = JSON.stringify(value.definition, null, 2); }
      notice("status.recomputed");
    }

    async function refresh() {
      if (disposed) { return; }
      if (!localeBound) { await ready; return; }
      refreshQueued = false;
      const own = ++generation;
      clear("binding.checking");
      controls(true);
      notice("status.recomputing");
      try {
        const value = await json("/api/drawing");
        if (disposed || own !== generation || refreshQueued) { return; }
        render(value);
      } catch (error) {
        if (!disposed && own === generation) {
          clear("binding.unavailable");
          showError(error);
        }
      } finally {
        if (!disposed && own === generation) {
          controls(false);
          if (refreshQueued) { refresh(); }
        }
      }
    }

    async function save(event) {
      event.preventDefault();
      if (pending) { return; }
      ++generation;
      clear("binding.save_unconfirmed");
      controls(true);
      const submittedText = editor.value;
      try {
        const definition = JSON.parse(submittedText);
        await json("/api/drawing", {definition: definition});
        if (disposed) { return; }
        dirty = editor.value !== submittedText;
        controls(false);
        await refresh();
      } catch (error) {
        if (!disposed) {
          showError(error, "error.save_prefix");
          controls(false);
          if (refreshQueued) { refresh(); }
        }
      }
    }

    async function download(fmt) {
      if (pending || !current) { return; }
      const expected = current;
      controls(true);
      try {
        const response = await request("/api/drawing/" + fmt, {credentials: "same-origin"});
        if (!response.ok) {
          const value = await response.json();
          throw requestError(value, "error.export_failed");
        }
        if (response.headers.get("X-OpenCAD-Model-SHA256") !== expected.source_model_sha256
            || response.headers.get("X-OpenCAD-Model-Version") !== String(expected.model_version)
            || response.headers.get("X-OpenCAD-Document-SHA256") !== expected.source_document_sha256
            || response.headers.get("X-OpenCAD-Drawing-SHA256") !== expected.drawing_sha256
            || disposed || current !== expected || refreshQueued) {
          clear("binding.export_changed");
          throw localError("error.export_version");
        }
        const blob = await response.blob();
        if (disposed || current !== expected || refreshQueued) {
          throw localError("error.export_read_changed");
        }
        if (!blob.size) { throw localError("error.export_empty"); }
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "drawing." + fmt;
        anchor.click();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        notice("status.downloaded", {format: fmt.toUpperCase()});
      } catch (error) {
        showError(error);
      } finally {
        if (!disposed) {
          controls(false);
          if (refreshQueued) { refresh(); }
        }
      }
    }

    editor.addEventListener("input", function () { dirty = true; });
    panel.querySelector("form").addEventListener("submit", save);
    panel.querySelector("[data-drawing-refresh]").addEventListener("click", refresh);
    panel.querySelectorAll("[data-drawing-export]").forEach(function (button) {
      button.addEventListener("click", function () { download(button.dataset.drawingExport); });
    });
    function stateBinding(value) {
      if (!value || value.ok !== true || typeof value.project_id !== "string"
          || !value.project_id || !Number.isSafeInteger(value.revision)
          || !/^[a-f0-9]{64}$/.test(value.sha256 || "")) { return null; }
      return JSON.stringify([value.project_id, value.revision, value.sha256]);
    }

    function changed(event) {
      if (!localeBound || disposed) { return; }
      const next = event && event.type === "opencad:state" ? stateBinding(event.detail) : null;
      // Viewer locale refreshes publish state too; the identical private head is not a CAD edit.
      if (next && next === modelBinding && typeof event.detail.locale === "string"
          && event.detail.strings && typeof event.detail.strings === "object") { return; }
      if (next) { modelBinding = next; }
      if (pending) {
        refreshQueued = true;
        clear("binding.model_changed");
      } else {
        refresh();
      }
    }
    window.addEventListener("opencad:state", changed);
    window.addEventListener("opencad:parametric", changed);
    controls(true);
    // bind re-reads dynamic leaf keys on locale updates; it never calls render/refresh.
    const ready = locale.ready.then(async function () {
      if (disposed) { return; }
      unbind = locale.bind(panel);
      localeBound = true;
      controls(false);
      await refresh();
    }).catch(function (error) {
      if (!disposed) {
        const alert = document.createElement("output");
        alert.setAttribute("role", "alert");
        alert.setAttribute("data-drawing-locale-alert", "");
        alert.textContent = error.code || error.message;
        panel.appendChild(alert);
      }
      throw error;
    });
    return {
      ready: ready,
      refresh: refresh,
      destroy: function () {
        disposed = true;
        ++generation;
        window.removeEventListener("opencad:state", changed);
        window.removeEventListener("opencad:parametric", changed);
        if (unbind) { unbind(); }
        panel.remove();
      }
    };
  }

  window.opencadDrawingPanel = {mount: mount};
}());
