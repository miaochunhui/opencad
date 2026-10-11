/* Independent server-image controls; no WebGL/WebGPU or remote resource input. */
(function () {
  "use strict";
  const labels = {
    title: "\u670d\u52a1\u7aef\u79bb\u5c4f\u6e32\u67d3",
    camera: "\u89c6\u89d2",
    isometric: "\u7b49\u8f74\u6d4b",
    front: "\u524d\u89c6",
    top: "\u4fef\u89c6",
    right: "\u53f3\u89c6",
    backend: "\u540e\u7aef",
    fallback: "\u5141\u8bb8\u660e\u786e CPU \u56de\u9000",
    render: "\u6e32\u67d3\u5f53\u524d\u6301\u4e45\u7248\u672c",
    wait: "\u6b63\u5728\u8bfb\u53d6\u6301\u4e45\u7248\u672c\u5e76\u6e32\u67d3",
    image: "\u670d\u52a1\u7aef\u6e32\u67d3 PNG",
    idle: "\u5c1a\u672a\u8c03\u7528\u670d\u52a1\u7aef\u6e32\u67d3",
    failed: "\u6e32\u67d3\u5931\u8d25"
  };
  function mount(root) {
    if (!root || root.querySelector(".oc-render-panel")) { return; }
    const panel = document.createElement("section");
    panel.className = "oc-render-panel";
    const title = document.createElement("h2");
    title.textContent = labels.title;
    function select(label, choices) {
      const wrap = document.createElement("label");
      wrap.append(document.createTextNode(label));
      const node = document.createElement("select");
      choices.forEach(function (value) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = labels[value] || value.toUpperCase();
        node.append(option);
      });
      wrap.append(node);
      panel.append(wrap);
      return node;
    }
    panel.append(title);
    const camera = select(labels.camera, ["isometric", "front", "top", "right"]);
    const backend = select(labels.backend, ["gpu", "cpu"]);
    const fallbackWrap = document.createElement("label");
    const fallback = document.createElement("input");
    fallback.type = "checkbox";
    fallbackWrap.append(fallback, document.createTextNode(labels.fallback));
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = labels.render;
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.textContent = labels.idle;
    const image = document.createElement("img");
    image.alt = labels.image;
    image.hidden = true;
    panel.append(fallbackWrap, button, status, image);
    root.append(panel);
    let active = false;
    async function render() {
      if (active) { return; }
      active = true;
      button.disabled = camera.disabled = backend.disabled = fallback.disabled = true;
      image.hidden = true;
      image.removeAttribute("src");
      status.textContent = labels.wait;
      try {
        const stateResponse = await window.fetch("/api/state", {credentials: "same-origin"});
        const state = await stateResponse.json();
        if (!stateResponse.ok || !Number.isSafeInteger(state.revision)
            || !/^[0-9a-f]{64}$/.test(state.sha256 || "")) {
          throw new Error(state.detail || state.error || "DB snapshot unavailable");
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
          throw new Error(failure.detail || failure.error || labels.failed);
        }
        if ((response.headers.get("Content-Type") || "").split(";")[0] !== "image/png"
            || response.headers.get("X-OpenCAD-Revision") !== String(state.revision)
            || response.headers.get("X-OpenCAD-Snapshot-SHA256") !== state.sha256) {
          throw new Error("Render response revision/media mismatch");
        }
        const blob = await response.blob();
        if (!blob.size || blob.size > 2 * 1024 * 1024) {
          throw new Error("Render PNG byte budget invalid");
        }
        const bytes = new Uint8Array(await blob.arrayBuffer());
        if ([137, 80, 78, 71, 13, 10, 26, 10].some(function (v, i) { return bytes[i] !== v; })) {
          throw new Error("Render PNG magic invalid");
        }
        // CSP permits same-origin/data images, not blob URLs. No renderer canvas.
        const reader = new FileReader();
        await new Promise(function (resolve, reject) {
          reader.onload = resolve;
          reader.onerror = function () { reject(new Error("PNG read failed")); };
          reader.readAsDataURL(blob);
        });
        image.src = reader.result;
        image.hidden = false;
        const reason = response.headers.get("X-OpenCAD-Fallback-Reason");
        status.textContent = response.headers.get("X-OpenCAD-Render-Status") + " | "
          + response.headers.get("X-OpenCAD-Render-Backend") + " | "
          + response.headers.get("X-OpenCAD-Renderer") + " | revision " + state.revision
          + (reason ? " | CPU fallback: " + reason : "");
      } catch (error) {
        status.textContent = labels.failed + ": " + String(error.message || error);
      } finally {
        active = false;
        button.disabled = camera.disabled = backend.disabled = fallback.disabled = false;
      }
    }
    button.addEventListener("click", render);
    camera.addEventListener("change", render);
    backend.addEventListener("change", render);
    fallback.addEventListener("change", render);
  }
  window.OpenCADRenderPanel = {mount: mount};
  function boot() {
    const root = document.querySelector("[data-opencad-render-panel]");
    if (root) { mount(root); }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }
})();
