/* Bounded Web-only assembly panel. Gateway transport owns authentication/CAS. */
(function () {
  "use strict";
  const defaults = {
    title: "真实装配", part: "独立零件 L1 文件", partId: "零件 ID",
    componentId: "实例 ID", load: "加载零件", components: "层级实例",
    pose: "局部位姿与父级", apply: "应用位姿", mate: "平面 / 轴配合",
    addMate: "应用配合", solve: "求解配合", save: "保存项目版本",
    download: "下载装配 JSON", reopen: "重开装配 JSON", refresh: "刷新",
    empty: "尚未加载独立零件", ready: "已同步", busy: "处理中",
    bom: "物料清单", collision: "真实 BRep 碰撞与间隙",
    faces: "绑定零件 SHA 的面索引", separated: "分离", touching: "接触",
    penetrating: "相交", error: "错误", preview: "装配三角网格视图",
    committed: "项目版本已提交", current: "当前项目版本",
    componentJson: "层级实例 JSON", mateJson: "配合 JSON", translation: "平移（mm）",
    solver: "配合求解残差与自由度"
  };
  const mm = 1000000;
  const sha = /^[a-f0-9]{64}$/;

  async function digest(text) {
    const value = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(value), x => x.toString(16).padStart(2, "0")).join("");
  }

  function nmVector(value) {
    const vector = JSON.parse(value);
    if (!Array.isArray(vector) || vector.length !== 3 ||
        vector.some(x => typeof x !== "number" || !Number.isFinite(x) ||
          !Number.isSafeInteger(Math.round(x * mm)) || Math.abs(x * mm) > 1e12)) {
      throw new Error("translation requires three bounded numbers in mm");
    }
    return vector.map(x => Math.round(x * mm));
  }

  function cloneJson(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function mount(root, options) {
    if (!(root instanceof HTMLElement) || !options ||
        typeof options.transport !== "function") {
      throw new Error("assembly mount requires a root and gateway transport");
    }
    const labels = Object.assign({}, defaults, options.labels || {});
    const text = key => labels[key] || key;
    root.classList.add("assembly-panel");
    root.innerHTML = `
      <h2 data-label="title"></h2>
      <p data-role="status" role="status" aria-live="polite"></p>
      <div class="assembly-layout">
        <section class="assembly-controls">
          <form data-form="import">
            <label><span data-label="part"></span><input name="file" type="file" accept=".json,.l1" required></label>
            <label><span data-label="partId"></span><input name="part_id" required maxlength="128"></label>
            <label><span data-label="componentId"></span><input name="component_id" required maxlength="128"></label>
            <button data-label="load"></button>
          </form>
          <h3 data-label="components"></h3><ul data-role="tree"></ul>
          <form data-form="pose">
            <h3 data-label="pose"></h3>
            <label><span data-label="componentJson"></span><textarea name="component" rows="9" required></textarea></label>
            <label><span data-label="translation"></span><input name="translation_mm" value="[0,0,0]" required></label>
            <button data-label="apply"></button>
          </form>
          <form data-form="mate">
            <h3 data-label="mate"></h3>
            <label><span data-label="mateJson"></span><textarea name="mate" rows="5" required></textarea></label>
            <button data-label="addMate"></button>
          </form>
          <div class="assembly-actions">
            <button type="button" data-action="solve" data-label="solve"></button>
            <button type="button" data-action="checkpoint" data-label="save"></button>
            <button type="button" data-action="download" data-label="download"></button>
            <button type="button" data-action="refresh" data-label="refresh"></button>
          </div>
          <label><span data-label="reopen"></span><input data-role="reopen" type="file" accept=".json"></label>
        </section>
        <section class="assembly-view">
          <canvas data-role="canvas"></canvas>
          <p data-role="metrics"></p><h3 data-label="bom"></h3><pre data-role="bom"></pre>
          <h3 data-label="collision"></h3><pre data-role="collisions"></pre>
          <h3 data-label="faces"></h3><pre data-role="faces"></pre>
          <h3 data-label="solver"></h3><pre data-role="solver"></pre>
        </section>
      </div>`;
    root.querySelectorAll("[data-label]").forEach(node => {
      node.textContent = text(node.dataset.label);
    });
    const node = role => root.querySelector('[data-role="' + role + '"]');
    const poseForm = root.querySelector("[data-form=pose]");
    const mateForm = root.querySelector("[data-form=mate]");
    mateForm.elements.mate.value = JSON.stringify({
      mate_id: "mate-1", kind: "axis", a: "", a_face: 0, b: "", b_face: 0,
      offset_nm: 0, aligned: true
    }, null, 2);
    const canvas = node("canvas");
    canvas.setAttribute("aria-label", text("preview"));
    const gl = canvas.getContext("webgl");
    if (!gl) { throw new Error("WebGL is required for the assembly mesh preview"); }
    function shader(kind, source) {
      const item = gl.createShader(kind);
      gl.shaderSource(item, source); gl.compileShader(item);
      if (!gl.getShaderParameter(item, gl.COMPILE_STATUS)) {
        throw new Error(gl.getShaderInfoLog(item));
      }
      return item;
    }
    const program = gl.createProgram();
    gl.attachShader(program, shader(gl.VERTEX_SHADER,
      "attribute vec3 p;uniform float aspect;void main(){gl_Position=vec4(p.x/aspect,p.y,p.z,1.);}"));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER,
      "precision mediump float;uniform vec3 color;void main(){gl_FragColor=vec4(color,1.);}"));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program));
    }
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    const position = gl.getAttribLocation(program, "p");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 3, gl.FLOAT, false, 0, 0);
    gl.enable(gl.DEPTH_TEST);
    const api = {busy: false, state: null, error: null, triangles: 0};
    let chain = Promise.resolve(), selected = null, angle = -0.4, tilt = 0.7;

    function fail(error) {
      api.error = String(error.message || error);
      node("status").textContent = text("error") + ": " + api.error;
    }

    function busy(value) {
      api.busy = value;
      root.setAttribute("aria-busy", String(value));
      root.querySelectorAll("input,textarea,button").forEach(item => { item.disabled = value; });
    }

    let pending = 0;
    function queued(action) {
      pending += 1;
      busy(true);
      const task = chain.then(async function () {
        busy(true); api.error = null; node("status").textContent = text("busy");
        try { return await action(); }
        catch (error) { fail(error); throw error; }
        finally { pending -= 1; busy(pending > 0); }
      });
      // Preserve rejection for the caller while allowing an explicit recovery.
      chain = task.catch(function () {});
      return task;
    }

    function draw() {
      const bounds = canvas.getBoundingClientRect();
      canvas.width = Math.round(Math.max(1, bounds.width) * devicePixelRatio);
      canvas.height = Math.round(Math.max(1, bounds.height) * devicePixelRatio);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.06, 0.10, 0.13, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.uniform1f(gl.getUniformLocation(program, "aspect"), canvas.width / canvas.height);
      const measured = api.state && api.state.evaluation;
      api.triangles = 0;
      if (!measured) { return; }
      const bbox = measured.properties.bbox_nm.map(x => x / mm);
      const center = [0, 1, 2].map(i => (bbox[i] + bbox[i + 3]) / 2);
      const aspect = canvas.width / canvas.height;
      const scale = 1.1 * Math.min(1, aspect) /
        Math.max(1e-9, ...[0, 1, 2].map(i => bbox[i + 3] - bbox[i]));
      measured.instances.forEach(function (instance, index) {
        const mesh = instance.mesh;
        if (!mesh) { return; }
        const vertices = [];
        mesh.triangles.forEach(function (triangle) {
          triangle.forEach(function (offset) {
            const point = mesh.vertices_mm[offset].map((x, i) => (x - center[i]) * scale);
            const x = point[0] * Math.cos(angle) - point[1] * Math.sin(angle);
            const y = point[0] * Math.sin(angle) + point[1] * Math.cos(angle);
            vertices.push(x, y * Math.cos(tilt) - point[2] * Math.sin(tilt),
              y * Math.sin(tilt) + point[2] * Math.cos(tilt));
          });
        });
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
        const colors = [[0.25, 0.76, 0.79], [1, 0.65, 0.25], [0.63, 0.8, 0.35]];
        gl.uniform3fv(gl.getUniformLocation(program, "color"), colors[index % colors.length]);
        gl.drawArrays(gl.TRIANGLES, 0, vertices.length / 3);
        api.triangles += mesh.triangles.length;
      });
    }

    function select(component) {
      selected = component.component_id;
      poseForm.elements.component.value = JSON.stringify(component, null, 2);
      poseForm.elements.translation_mm.value = JSON.stringify(
        component.transform.translation_nm.map(x => x / mm));
    }

    function render(value) {
      api.state = value;
      node("tree").replaceChildren();
      const assembly = value.assembly;
      if (assembly) {
        assembly.components.forEach(function (component) {
          const item = document.createElement("li"), button = document.createElement("button");
          button.type = "button";
          button.disabled = api.busy;
          button.textContent = component.component_id + " / " + (component.part_id || "group") +
            (component.parent_id ? " ← " + component.parent_id : "");
          button.addEventListener("click", function () { select(component); });
          item.append(button); node("tree").append(item);
        });
        select(assembly.components.find(c => c.component_id === selected) || assembly.components[0]);
      } else { poseForm.elements.component.value = ""; }
      node("bom").textContent = JSON.stringify(value.bom, null, 2);
      const evaluation = value.evaluation;
      node("collisions").textContent = evaluation ? evaluation.collisions.map(pair =>
        pair.a + " / " + pair.b + ": " + text(pair.status) +
        " distance_nm=" + pair.distance_nm + " interference_nm3=" + pair.interference_volume_nm3
      ).join("\n") : "";
      node("faces").textContent = evaluation ? JSON.stringify(evaluation.instances.map(item => ({
        component_id: item.component_id, document_sha256: item.document_sha256, faces: item.faces
      })), null, 2) : "";
      node("solver").textContent = value.solver ? JSON.stringify({
        status: value.solver.status, dof: value.solver.dof, iterations: value.solver.iterations,
        jacobian_rank: value.solver.jacobian_rank, residuals: value.solver.residuals
      }, null, 2) : "";
      node("metrics").textContent = evaluation ?
        "FreeCAD-BRep · solids=" + evaluation.solid_count + " · volume_nm3=" +
          evaluation.properties.volume_nm3 : text("empty");
      node("status").textContent = text("ready") + " · " + text("current") + " " + value.revision;
      draw();
    }

    async function accept(value) {
      if (!value || value.ok !== true || typeof value.present !== "boolean" ||
          !Number.isSafeInteger(value.revision) || value.revision < 1 ||
          typeof value.project_id !== "string" || !sha.test(value.sha256) ||
          !sha.test(value.document_sha256)) { throw new Error("invalid assembly commit/snapshot metadata"); }
      if (api.state && (value.project_id !== api.state.project_id ||
          value.revision < api.state.revision ||
          (value.revision === api.state.revision && value.sha256 !== api.state.sha256))) {
        throw new Error("assembly_snapshot_conflict");
      }
      if (typeof value.doc !== "string" || await digest(value.doc) !== value.document_sha256) {
        throw new Error("assembly_document_digest_mismatch");
      }
      if (value.present && (!value.assembly || !sha.test(value.assembly_sha256) ||
          typeof value.assembly_json !== "string")) {
        throw new Error("invalid immutable assembly binding");
      }
      if (value.present) {
        function canonical(item) {
          if (Array.isArray(item)) { return item.map(canonical); }
          if (item && typeof item === "object") {
            const result = {}; Object.keys(item).sort().forEach(k => { result[k] = canonical(item[k]); });
            return result;
          }
          return item;
        }
        if (await digest(value.assembly_json) !== value.assembly_sha256 ||
            JSON.stringify(canonical(JSON.parse(value.assembly_json))) !==
              JSON.stringify(canonical(value.assembly)) ||
            JSON.stringify(canonical(JSON.parse(value.doc).industrial_assembly_v1)) !==
              JSON.stringify(canonical(value.assembly))) {
          throw new Error("assembly_digest_mismatch");
        }
        if (!value.evaluation || value.evaluation.assembly_sha256 !== value.assembly_sha256 ||
            value.evaluation.document_sha256 !== value.document_sha256 ||
            value.evaluation.backend !== "FreeCAD-BRep") {
          throw new Error("assembly_evaluation_snapshot_mismatch");
        }
        if (value.evaluation) {
          const parts = new Map(value.assembly.parts.map(p => [
            p.part_id + "\0" + p.configuration, p.document_sha256]));
          const leaves = new Map(value.assembly.components.filter(c => c.part_id !== null)
            .map(c => [c.component_id, c]));
          if (leaves.size !== value.evaluation.instances.length) {
            throw new Error("assembly_instance_mesh_mismatch");
          }
          const seen = new Set();
          for (const item of value.evaluation.instances) {
            const component = leaves.get(item.component_id);
            if (!component || seen.has(item.component_id) || component.part_id !== item.part_id ||
                component.configuration !== item.configuration ||
                parts.get(item.part_id + "\0" + item.configuration) !== item.document_sha256) {
              throw new Error("assembly_part_mesh_mismatch");
            }
            seen.add(item.component_id);
          }
        }
      } else if (value.assembly !== null || value.assembly_sha256 !== null ||
          value.assembly_json !== null || value.evaluation !== null ||
          JSON.parse(value.doc).industrial_assembly_v1 !== undefined) {
        throw new Error("assembly_empty_snapshot_mismatch");
      }
      render(value); return value;
    }

    async function read() {
      return accept(await options.transport({method: "GET", path: "/api/assembly?action=state"}));
    }
    async function write(body) {
      const value = await options.transport({method: "POST", path: "/api/assembly", body: body});
      await accept(value);
      if (options.onCommitted) { await options.onCommitted(value); }
      return value;
    }
    api.refresh = () => queued(read);
    api.command = body => {
      const captured = cloneJson(body);
      return queued(() => write(captured));
    };
    api.select = identifier => {
      const component = api.state && api.state.assembly &&
        api.state.assembly.components.find(c => c.component_id === identifier);
      if (!component) { throw new Error("unknown component"); }
      select(component);
    };
    root.querySelector("[data-form=import]").addEventListener("submit", event => {
      event.preventDefault();
      const form = event.currentTarget;
      const file = form.elements.file.files[0];
      const partId = form.elements.part_id.value, componentId = form.elements.component_id.value;
      queued(async function () {
        if (!file || file.size > 2 * 1024 * 1024) { throw new Error("需要不超过 2 MiB 的独立零件 L1 文件"); }
        if (!api.state) { throw new Error("请先同步当前项目"); }
        const l1 = await file.text();
        const part = {part_id: partId, configuration: "default",
          l1_text: l1, document_sha256: await digest(l1)};
        return write({action: "import_part", part: part, component: {
          component_id: componentId, part_id: part.part_id,
          configuration: "default", parent_id: null, fixed: !api.state.present,
          transform: {rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], translation_nm: [0, 0, 0]}
        }});
      }).catch(fail);
    });
    poseForm.addEventListener("submit", event => {
      event.preventDefault();
      const componentText = poseForm.elements.component.value;
      const translationText = poseForm.elements.translation_mm.value;
      queued(function () {
        const component = JSON.parse(componentText);
        component.transform.translation_nm = nmVector(translationText);
        return write({action: "put_component", component: component});
      }).catch(fail);
    });
    mateForm.addEventListener("submit", event => {
      event.preventDefault();
      const mateText = mateForm.elements.mate.value;
      queued(() => write({action: "put_mate", mate: JSON.parse(mateText)})).catch(fail);
    });
    node("reopen").addEventListener("change", event => {
      const file = event.target.files[0];
      queued(async function () {
        if (!file || file.size > 8 * 1024 * 1024) { throw new Error("需要不超过 8 MiB 的装配 JSON 文件"); }
        return write({action: "reopen", assembly_json: await file.text()});
      }).catch(fail);
    });
    root.querySelectorAll("[data-action]").forEach(button => {
      button.addEventListener("click", function () {
        const action = button.dataset.action;
        queued(async function () {
          if (action === "refresh") { return read(); }
          if (action !== "download") { return write({action: action}); }
          const value = await options.transport({method: "GET", path: "/api/assembly?action=export"});
          if (await digest(value.assembly_json) !== value.assembly_sha256) {
            throw new Error("assembly_export_digest_mismatch");
          }
          const url = URL.createObjectURL(new Blob([value.assembly_json], {type: "application/json"}));
          const anchor = document.createElement("a");
          anchor.href = url; anchor.download = "assembly.json"; anchor.click();
          URL.revokeObjectURL(url);
          node("status").textContent = text("ready");
          return value;
        }).catch(fail);
      });
    });
    let drag = null;
    canvas.addEventListener("pointerdown", event => {
      drag = [event.clientX, event.clientY]; canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", event => {
      if (!drag) { return; }
      angle += (event.clientX - drag[0]) / 150; tilt += (event.clientY - drag[1]) / 150;
      drag = [event.clientX, event.clientY]; draw();
    });
    canvas.addEventListener("pointerup", () => { drag = null; });
    canvas.addEventListener("pointercancel", () => { drag = null; });
    const observer = new ResizeObserver(draw); observer.observe(canvas);
    api.destroy = () => {
      observer.disconnect(); gl.deleteBuffer(buffer); gl.deleteProgram(program); root.replaceChildren();
    };
    api.ready = api.refresh();
    api.ready.catch(fail);
    return api;
  }
  window.opencadAssembly = {mount: mount, nmVector: nmVector};
})();
