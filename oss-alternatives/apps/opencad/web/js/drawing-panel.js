/* Explicitly mounted associative drawing panel; project-session owns CAS/auth. */
(function () {
  "use strict";

  function mount(host, options) {
    if (!host || host.nodeType !== 1) { throw new Error("工程图面板需要挂载节点"); }
    options = options || {};
    const request = options.fetch || window.fetch.bind(window);
    const panel = document.createElement("section");
    panel.className = "drawing-panel";
    panel.setAttribute("aria-label", "关联工程图");
    panel.innerHTML =
      '<header><p class="drawing-panel-kicker">模型与图纸 · 同源版本</p><h2>关联工程图</h2>' +
      '<p>真实实体投影与剖切。完整 GD&amp;T / MBD 和 GB / ISO 符合性未验收。</p></header>' +
      '<form class="drawing-panel-form"><label>工程图定义 JSON' +
      '<textarea class="drawing-definition" spellcheck="false" rows="8" required></textarea></label>' +
      '<div class="drawing-panel-actions"><button type="submit">保存定义</button>' +
      '<button type="button" data-drawing-refresh>刷新关联图</button></div></form>' +
      '<output class="drawing-panel-status" role="status" aria-live="polite">尚未读取</output>' +
      '<div class="drawing-panel-binding"></div>' +
      '<div class="drawing-panel-preview" aria-label="真实关联投影视图"></div>' +
      '<div class="drawing-panel-table" tabindex="0" aria-label="关联尺寸，可横向滚动">' +
      '<table aria-label="关联实测尺寸"><thead><tr><th scope="col">尺寸</th>' +
      '<th scope="col">实测值</th><th scope="col">模型引用</th></tr></thead><tbody></tbody></table></div>' +
      '<div class="drawing-panel-actions drawing-panel-exports">' +
      '<button type="button" data-drawing-export="svg">下载 SVG</button>' +
      '<button type="button" data-drawing-export="pdf">下载 PDF</button>' +
      '<button type="button" data-drawing-export="dxf">下载 DXF</button></div>';
    host.appendChild(panel);
    const editor = panel.querySelector("textarea");
    const status = panel.querySelector("output");
    const binding = panel.querySelector(".drawing-panel-binding");
    const preview = panel.querySelector(".drawing-panel-preview");
    const body = panel.querySelector("tbody");
    let generation = 0;
    let pending = false;
    let dirty = false;
    let current = null;
    let disposed = false;
    let refreshQueued = false;
    editor.value = JSON.stringify(options.definition || {
      title: "Associative drawing", views: ["top", "front", "right"],
      sections: [], dimensions: [], bom_refs: []
    }, null, 2);

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
      binding.textContent = reason || "";
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
        throw new Error(value.detail || value.error || "工程图请求未被确认");
      }
      return value;
    }

    function checkedSvg(text) {
      const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
      if (parsed.querySelector("parsererror") || parsed.documentElement.localName !== "svg") {
        throw new Error("工程图 SVG 无法解析");
      }
      const allowed = new Set(["svg", "title", "rect", "g", "line", "circle", "text"]);
      parsed.querySelectorAll("*").forEach(function (node) {
        if (!allowed.has(node.localName) || node.namespaceURI !== "http://www.w3.org/2000/svg") {
          throw new Error("工程图含不支持的 SVG 节点");
        }
        Array.from(node.attributes).forEach(function (attribute) {
          if (/^on/i.test(attribute.name) || /href|style/i.test(attribute.name)
              || /url\s*\(/i.test(attribute.value)) {
            throw new Error("工程图含不安全的 SVG 属性");
          }
        });
      });
      return document.importNode(parsed.documentElement, true);
    }

    function render(value) {
      if (!value.defined) {
        clear("尚未保存工程图定义");
        status.textContent = "先保存投影、剖面与尺寸引用定义";
        return;
      }
      if (!Number.isSafeInteger(value.model_version)
          || !/^[a-f0-9]{64}$/.test(value.source_model_sha256 || "")
          || !/^[a-f0-9]{64}$/.test(value.source_document_sha256 || "")
          || !/^[a-f0-9]{64}$/.test(value.drawing_sha256 || "")
          || value.revision !== value.model_version
          || typeof value.svg !== "string" || !Array.isArray(value.dimensions)) {
        throw new Error("工程图版本或模型绑定不完整");
      }
      const image = checkedSvg(value.svg);
      current = value;
      binding.textContent = "项目版本 " + value.revision + " · 模型 SHA " + value.source_model_sha256;
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
      status.textContent = "关联图已重算，视图、尺寸与模型版本一致";
    }

    async function refresh() {
      if (disposed) { return; }
      refreshQueued = false;
      const own = ++generation;
      clear("正在核对当前模型");
      controls(true);
      status.textContent = "正在重算真实投影与剖面";
      try {
        const value = await json("/api/drawing");
        if (disposed || own !== generation || refreshQueued) { return; }
        render(value);
      } catch (error) {
        if (!disposed && own === generation) {
          clear("当前工程图不可用");
          status.textContent = error.message;
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
      clear("保存结果尚未确认");
      controls(true);
      try {
        const definition = JSON.parse(editor.value);
        await json("/api/drawing", {definition: definition});
        if (disposed) { return; }
        dirty = false;
        controls(false);
        await refresh();
      } catch (error) {
        if (!disposed) {
          status.textContent = "定义未确认保存：" + error.message;
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
          throw new Error(value.detail || value.error || "导出失败");
        }
        if (response.headers.get("X-OpenCAD-Model-SHA256") !== expected.source_model_sha256
            || response.headers.get("X-OpenCAD-Model-Version") !== String(expected.model_version)
            || response.headers.get("X-OpenCAD-Document-SHA256") !== expected.source_document_sha256
            || response.headers.get("X-OpenCAD-Drawing-SHA256") !== expected.drawing_sha256
            || disposed || current !== expected || refreshQueued) {
          clear("模型已变化，请刷新后导出");
          throw new Error("导出与当前预览不同版本，已拒绝下载");
        }
        const blob = await response.blob();
        if (disposed || current !== expected || refreshQueued) {
          throw new Error("读取导出期间模型已变化，已拒绝下载");
        }
        if (!blob.size) { throw new Error("导出文件为空"); }
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "drawing." + fmt;
        anchor.click();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        status.textContent = "已下载同版本 " + fmt.toUpperCase();
      } catch (error) {
        status.textContent = error.message;
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
    function changed() {
      if (pending) {
        refreshQueued = true;
        clear("模型已变化，等待重算当前版本");
      } else {
        refresh();
      }
    }
    window.addEventListener("opencad:state", changed);
    controls(false);
    refresh();
    return {
      refresh: refresh,
      destroy: function () {
        disposed = true;
        ++generation;
        window.removeEventListener("opencad:state", changed);
        panel.remove();
      }
    };
  }

  window.opencadDrawingPanel = {mount: mount};
}());
