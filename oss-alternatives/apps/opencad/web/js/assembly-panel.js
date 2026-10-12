/* Bounded Web-only assembly panel. Gateway transport owns authentication/CAS. */
(function () {
  "use strict";
  const mm = 1000000;
  const sha = /^[a-f0-9]{64}$/;

  function inputError(message, key, args) {
    const error = new Error(message);
    error.localeKey = key;
    error.localeArgs = args;
    return error;
  }

  function parseInput(value) {
    try { return JSON.parse(value); }
    catch (error) {
      error.localeKey = "json_invalid";
      error.localeArgs = {detail: error.message};
      throw error;
    }
  }

  async function digest(text) {
    const value = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(value), x => x.toString(16).padStart(2, "0")).join("");
  }

  function nmVector(value) {
    const vector = parseInput(value);
    if (!Array.isArray(vector) || vector.length !== 3 ||
        vector.some(x => typeof x !== "number" || !Number.isFinite(x) ||
          !Number.isSafeInteger(Math.round(x * mm)) || Math.abs(x * mm) > 1e12)) {
      throw inputError("translation requires three bounded numbers in mm", "translation_invalid");
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
    const locale = window.OpenCADLocale;
    if (!locale || !locale.ready || typeof locale.t !== "function" ||
        typeof locale.bind !== "function" || typeof locale.snapshot !== "function") {
      throw new Error("assembly_locale_service_required");
    }
    const text = (key, args) => locale.t("panel.assembly." + key, args);
    root.classList.add("assembly-panel");
    root.innerHTML = `
      <h2 data-panel-i18n="panel.assembly.title"></h2>
      <p data-role="status" role="status" aria-live="polite"></p>
      <div class="assembly-layout">
        <section class="assembly-controls">
          <form data-form="import">
            <label><span data-panel-i18n="panel.assembly.part"></span><input name="file" type="file" accept=".json,.l1" required data-panel-i18n-aria="panel.assembly.part"></label>
            <label><span data-panel-i18n="panel.assembly.part_id"></span><input name="part_id" required maxlength="128" data-panel-i18n-aria="panel.assembly.part_id"></label>
            <label><span data-panel-i18n="panel.assembly.component_id"></span><input name="component_id" required maxlength="128" data-panel-i18n-aria="panel.assembly.component_id"></label>
            <button data-panel-i18n="panel.assembly.load"></button>
          </form>
          <h3 data-panel-i18n="panel.assembly.components"></h3><ul data-role="tree"></ul>
          <form data-form="pose">
            <h3 data-panel-i18n="panel.assembly.pose"></h3>
            <label><span data-panel-i18n="panel.assembly.component_json"></span><textarea name="component" rows="9" required data-panel-i18n-aria="panel.assembly.component_json"></textarea></label>
            <label><span data-panel-i18n="panel.assembly.translation"></span><input name="translation_mm" value="[0,0,0]" required data-panel-i18n-aria="panel.assembly.translation"></label>
            <button data-panel-i18n="panel.assembly.apply"></button>
          </form>
          <form data-form="mate">
            <h3 data-panel-i18n="panel.assembly.mate"></h3>
            <label><span data-panel-i18n="panel.assembly.mate_json"></span><textarea name="mate" rows="5" required data-panel-i18n-aria="panel.assembly.mate_json"></textarea></label>
            <button data-panel-i18n="panel.assembly.add_mate"></button>
          </form>
          <div class="assembly-actions">
            <button type="button" data-action="solve" data-panel-i18n="panel.assembly.solve"></button>
            <button type="button" data-action="checkpoint" data-panel-i18n="panel.assembly.save"></button>
            <button type="button" data-action="download" data-panel-i18n="panel.assembly.download"></button>
            <button type="button" data-action="refresh" data-panel-i18n="panel.assembly.refresh"></button>
          </div>
          <label><span data-panel-i18n="panel.assembly.reopen"></span><input data-role="reopen" type="file" accept=".json" data-panel-i18n-aria="panel.assembly.reopen"></label>
        </section>
        <section class="assembly-view">
          <canvas data-role="canvas" data-panel-i18n-aria="panel.assembly.preview"></canvas>
          <p data-role="metrics"></p><h3 data-panel-i18n="panel.assembly.bom"></h3><pre data-role="bom"></pre>
          <h3 data-panel-i18n="panel.assembly.collision"></h3><pre data-role="collisions"></pre>
          <h3 data-panel-i18n="panel.assembly.faces"></h3><pre data-role="faces"></pre>
          <h3 data-panel-i18n="panel.assembly.solver"></h3><pre data-role="solver"></pre>
        </section>
      </div>`;
    const node = role => root.querySelector('[data-role="' + role + '"]');
    const poseForm = root.querySelector("[data-form=pose]");
    const mateForm = root.querySelector("[data-form=mate]");
    mateForm.elements.mate.value = JSON.stringify({
      mate_id: "mate-1", kind: "axis", a: "", a_face: 0, b: "", b_face: 0,
      offset_nm: 0, aligned: true
    }, null, 2);
    const canvas = node("canvas");
    const gl = canvas.getContext("webgl");
    if (!gl) { throw new Error(text("webgl_required")); }
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
    let notice = {key: "busy"}, lastError = null, unbind = null, destroyed = false;

    function paintStatus() {
      const detail = lastError && (lastError.localeKey ?
        text(lastError.localeKey, lastError.localeArgs) : api.error);
      node("status").textContent = lastError ? text("error_detail", {detail: detail}) :
        text(notice.key, notice.args);
    }

    function paintLocale() {
      if (destroyed) { return; }
      paintStatus();
      const evaluation = api.state && api.state.evaluation;
      node("collisions").textContent = evaluation ? evaluation.collisions.map(pair =>
        pair.a + " / " + pair.b + ": " + text(pair.status) +
        " distance_nm=" + pair.distance_nm + " interference_nm3=" + pair.interference_volume_nm3
      ).join("\n") : "";
      node("metrics").textContent = evaluation ? text("metrics", {
        backend: evaluation.backend, solids: evaluation.solid_count,
        volume: evaluation.properties.volume_nm3
      }) : api.state ? text("empty") : "";
    }

    function status(key, args) {
      notice = {key: key, args: args};
      lastError = null;
      paintStatus();
    }

    function bindLabels() {
      const next = locale.bind(root);
      if (unbind) { unbind(); }
      unbind = next;
    }

    function fail(error) {
      api.error = String(error.message || error);
      lastError = error;
      if (destroyed) { return; }
      if (locale.snapshot().status === "ready") { paintStatus(); }
      else { node("status").textContent = api.error; }
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
        try {
          await locale.ready;
          if (destroyed) { throw new Error("assembly_panel_destroyed"); }
          busy(true); api.error = null; status("busy");
          return await action();
        }
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
          const identifier = document.createElement("span");
          identifier.textContent = component.component_id + " / ";
          const part = document.createElement("span");
          if (component.part_id) { part.textContent = component.part_id; }
          else { part.setAttribute("data-panel-i18n", "panel.assembly.group"); }
          const parent = document.createElement("span");
          parent.textContent = component.parent_id ? " ← " + component.parent_id : "";
          button.append(identifier, part, parent);
          button.addEventListener("click", function () { select(component); });
          item.append(button); node("tree").append(item);
        });
        select(assembly.components.find(c => c.component_id === selected) || assembly.components[0]);
      } else { poseForm.elements.component.value = ""; }
      node("bom").textContent = JSON.stringify(value.bom, null, 2);
      const evaluation = value.evaluation;
      node("faces").textContent = evaluation ? JSON.stringify(evaluation.instances.map(item => ({
        component_id: item.component_id, document_sha256: item.document_sha256, faces: item.faces
      })), null, 2) : "";
      node("solver").textContent = value.solver ? JSON.stringify({
        status: value.solver.status, dof: value.solver.dof, iterations: value.solver.iterations,
        jacobian_rank: value.solver.jacobian_rank, residuals: value.solver.residuals
      }, null, 2) : "";
      status("current_revision", {revision: value.revision});
      bindLabels();
      paintLocale();
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
      if (!component) {
        throw inputError(text("component_unknown", {identifier: identifier}),
          "component_unknown", {identifier: identifier});
      }
      select(component);
    };
    root.querySelector("[data-form=import]").addEventListener("submit", event => {
      event.preventDefault();
      const form = event.currentTarget;
      const file = form.elements.file.files[0];
      const partId = form.elements.part_id.value, componentId = form.elements.component_id.value;
      queued(async function () {
        if (!file || file.size > 2 * 1024 * 1024) {
          throw inputError(text("part_file_required", {limit: 2}), "part_file_required", {limit: 2});
        }
        if (!api.state) { throw inputError(text("sync_required"), "sync_required"); }
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
        const component = parseInput(componentText);
        component.transform.translation_nm = nmVector(translationText);
        return write({action: "put_component", component: component});
      }).catch(fail);
    });
    mateForm.addEventListener("submit", event => {
      event.preventDefault();
      const mateText = mateForm.elements.mate.value;
      queued(() => write({action: "put_mate", mate: parseInput(mateText)})).catch(fail);
    });
    node("reopen").addEventListener("change", event => {
      const file = event.target.files[0];
      queued(async function () {
        if (!file || file.size > 8 * 1024 * 1024) {
          throw inputError(text("assembly_file_required", {limit: 8}),
            "assembly_file_required", {limit: 8});
        }
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
          status("ready");
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
      destroyed = true;
      window.removeEventListener("opencad:locale", paintLocale);
      if (unbind) { unbind(); }
      observer.disconnect(); gl.deleteBuffer(buffer); gl.deleteProgram(program); root.replaceChildren();
    };
    busy(true);
    api.ready = locale.ready.then(function () {
      if (destroyed) { throw new Error("assembly_panel_destroyed"); }
      bindLabels();
      window.addEventListener("opencad:locale", paintLocale);
      return api.refresh();
    });
    api.ready.catch(fail);
    return api;
  }
  window.opencadAssembly = {mount: mount, nmVector: nmVector};
})();
