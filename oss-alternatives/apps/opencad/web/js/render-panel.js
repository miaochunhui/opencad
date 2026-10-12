/* Independent server-image controls; no WebGL/WebGPU or remote resource input. */
(function () {
  "use strict";
  const prefix = "panel.render.";
  function mount(root) {
    if (!root || root.querySelector(".oc-render-panel")) { return; }
    const panel = document.createElement("section");
    panel.className = "oc-render-panel";
    const title = document.createElement("h2");
    title.setAttribute("data-panel-i18n", prefix + "title");
    function select(label, choices) {
      const wrap = document.createElement("label");
      const text = document.createElement("span");
      text.setAttribute("data-panel-i18n", prefix + label);
      wrap.append(text);
      const node = document.createElement("select");
      node.setAttribute("data-panel-i18n-aria", prefix + label);
      node.setAttribute("data-panel-i18n-title", prefix + label);
      choices.forEach(function (value) {
        const option = document.createElement("option");
        option.value = value;
        option.setAttribute("data-panel-i18n", prefix + label + "." + value);
        node.append(option);
      });
      wrap.append(node);
      panel.append(wrap);
      return node;
    }
    panel.append(title);
    const camera = select("camera", ["isometric", "front", "top", "right"]);
    const backend = select("backend", ["gpu", "cpu"]);
    const fallbackWrap = document.createElement("label");
    const fallback = document.createElement("input");
    fallback.type = "checkbox";
    fallback.setAttribute("data-panel-i18n-aria", prefix + "fallback");
    fallback.setAttribute("data-panel-i18n-title", prefix + "fallback");
    const fallbackText = document.createElement("span");
    fallbackText.setAttribute("data-panel-i18n", prefix + "fallback");
    fallbackWrap.append(fallback, fallbackText);
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("data-panel-i18n", prefix + "submit");
    button.setAttribute("data-panel-i18n-aria", prefix + "submit");
    button.setAttribute("data-panel-i18n-title", prefix + "submit");
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const image = document.createElement("img");
    image.setAttribute("data-panel-i18n-alt", prefix + "image.alt");
    image.hidden = true;
    const localeAlert = document.createElement("output");
    localeAlert.setAttribute("role", "alert");
    localeAlert.setAttribute("data-opencad-render-locale-alert", "");
    localeAlert.hidden = true;
    panel.append(fallbackWrap, button, status, image, localeAlert);
    root.append(panel);
    let active = false;
    let languageReady = false;
    let currentStatus = {key: "status.idle"};
    const locale = window.OpenCADLocale;
    function localeFailure(error) {
      localeAlert.textContent = "render_locale_error: " + String(error.code || error.message || error);
      localeAlert.hidden = false;
      throw error;
    }
    function paintStatus() {
      try {
        const args = currentStatus.detailKey ?
          {detail: locale.t(prefix + currentStatus.detailKey)} : currentStatus.args;
        let text = locale.t(prefix + currentStatus.key, args);
        if (currentStatus.reason) {
          text += " | " + locale.t(prefix + "status.fallback", {reason: currentStatus.reason});
        }
        status.textContent = text;
        localeAlert.textContent = "";
        localeAlert.hidden = true;
      } catch (error) {
        localeFailure(error);
      }
    }
    function setStatus(value) {
      currentStatus = value;
      paintStatus();
    }
    function localError(key) {
      const error = new Error("render_" + key);
      error.renderLocaleKey = "error." + key;
      return error;
    }
    if (!locale) { localeFailure(new Error("locale_service_missing")); }
    const ready = locale.ready.then(function () {
      locale.bind(panel);
      languageReady = true;
      paintStatus();
    }).catch(localeFailure);
    window.addEventListener("opencad:locale", function () {
      if (languageReady && locale.snapshot().status !== "loading") { paintStatus(); }
    });
    async function render() {
      if (active) { return; }
      await ready;
      if (active) { return; }
      active = true;
      button.disabled = camera.disabled = backend.disabled = fallback.disabled = true;
      image.hidden = true;
      image.removeAttribute("src");
      try {
        setStatus({key: "status.wait"});
        const stateResponse = await window.fetch("/api/state", {credentials: "same-origin"});
        const state = await stateResponse.json();
        if (!stateResponse.ok || !Number.isSafeInteger(state.revision)
            || !/^[0-9a-f]{64}$/.test(state.sha256 || "")) {
          if (state.detail || state.error) { throw new Error(state.detail || state.error); }
          throw localError("snapshot");
        }
        const response = await window.fetch("/api/render", {
          method: "POST", credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-OpenCAD-Request": "1"},
          body: JSON.stringify({camera: camera.value, backend: backend.value,
            width: 256, height: 256, allow_cpu_fallback: fallback.checked,
            expected_revision: state.revision, expected_sha: state.sha256})
        });
        if (!response.ok) {
          const failure = await response.json();
          if (failure.detail || failure.error) { throw new Error(failure.detail || failure.error); }
          throw localError("request");
        }
        if ((response.headers.get("Content-Type") || "").split(";")[0] !== "image/png"
            || response.headers.get("X-OpenCAD-Revision") !== String(state.revision)
            || response.headers.get("X-OpenCAD-Snapshot-SHA256") !== state.sha256) {
          throw localError("response");
        }
        const blob = await response.blob();
        if (!blob.size || blob.size > 2 * 1024 * 1024) {
          throw localError("budget");
        }
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if ([137, 80, 78, 71, 13, 10, 26, 10].some(function (v, i) { return bytes[i] !== v; })) {
          throw localError("magic");
        }
        // CSP permits same-origin/data images, not blob URLs. No renderer canvas.
        const reader = new FileReader();
        await new Promise(function (resolve, reject) {
          reader.onload = resolve;
          reader.onerror = function () { reject(localError("read")); };
          reader.readAsDataURL(blob);
        });
        image.src = reader.result;
        image.hidden = false;
        const reason = response.headers.get("X-OpenCAD-Fallback-Reason");
        setStatus({key: "status.result", args: {
          status: String(response.headers.get("X-OpenCAD-Render-Status")),
          backend: String(response.headers.get("X-OpenCAD-Render-Backend")),
          renderer: String(response.headers.get("X-OpenCAD-Renderer")),
          revision: state.revision
        }, reason: reason});
      } catch (error) {
        if (typeof error.code === "string" && error.code.startsWith("locale_")) { throw error; }
        setStatus(error.renderLocaleKey ?
          {key: "status.failed", detailKey: error.renderLocaleKey} :
          {key: "status.failed", args: {detail: String(error.message || error)}});
      } finally {
        active = false;
        button.disabled = camera.disabled = backend.disabled = fallback.disabled = false;
      }
    }
    button.addEventListener("click", render);
    camera.addEventListener("change", render);
    backend.addEventListener("change", render);
    fallback.addEventListener("change", render);
    return ready;
  }
  window.OpenCADRenderPanel = {mount: mount};
  function boot() {
    const root = document.querySelector("[data-opencad-render-panel]");
    if (root) { return mount(root); }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }
})();
