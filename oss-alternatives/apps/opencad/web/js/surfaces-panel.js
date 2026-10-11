/* SPDX-License-Identifier: Apache-2.0
 * Explicit mount only. The project-session fetch adapter owns durable CAS.
 * onMesh receives actual BRep tessellation; no approximate geometry producer.
 */
(function () {
  "use strict";
  function plane() {
    return {op: "nurbs", poles_nm: [
      [[0, 0, 0], [0, 10000000, 0]], [[10000000, 0, 0], [10000000, 10000000, 0]]
    ], u_degree: 1, v_degree: 1, u_knots: [0, 1], v_knots: [0, 1],
    u_mults: [2, 2], v_mults: [2, 2], weights: null};
  }
  function cylinder() {
    const poles = [[5, 0], [5, 5], [0, 5]].map(function (xy) {
      return [0, 10].map(function (z) { return [xy[0] * 1000000, xy[1] * 1000000, z * 1000000]; });
    });
    return {op: "nurbs", poles_nm: poles, u_degree: 2, v_degree: 1,
      u_knots: [0, 1], v_knots: [0, 1], u_mults: [3, 3], v_mults: [2, 2],
      weights: [[1, 1], [Math.SQRT1_2, Math.SQRT1_2], [1, 1]]};
  }
  function seam() {
    return {left: 1, right: 2, left_edge: "u_max", right_edge: "u_min", reverse: false,
      continuity: "G2", samples: 17, position_tolerance_nm: 100,
      angle_tolerance_rad: 0.000001, curvature_tolerance_per_mm: 0.000001};
  }
  function mount(host, options) {
    options = options || {};
    if (!host || host.nodeType !== 1) throw new Error("surface_panel_host_required");
    const labels = options.labels || {};
    const text = function (key, fallback) { return labels[key] || fallback; };
    const root = document.createElement("section");
    root.className = "surfaces-panel";
    host.appendChild(root);
    const heading = document.createElement("h2");
    heading.textContent = text("heading", "Exact surfaces");
    root.appendChild(heading);
    const note = document.createElement("p");
    note.className = "surfaces-note";
    note.textContent = text("limits", "NURBS / rectangular trim / sew / thicken. Sampled G0/G1/G2, not a whole-domain proof.");
    root.appendChild(note);
    const status = document.createElement("output");
    status.className = "surfaces-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    root.appendChild(status);
    const tools = document.createElement("div");
    tools.className = "surfaces-tools";
    root.appendChild(tools);
    function button(key, fallback, callback, parent) {
      const node = document.createElement("button");
      node.type = "button"; node.dataset.surfaceAction = key;
      node.textContent = text(key, fallback);
      node.addEventListener("click", function () {
        Promise.resolve().then(callback).catch(fail);
      });
      (parent || tools).appendChild(node);
      return node;
    }
    const editorLabel = document.createElement("label");
    editorLabel.textContent = text("recipe", "Exact recipe (coordinates in integer nm)");
    const editor = document.createElement("textarea");
    editor.className = "surfaces-recipe"; editor.rows = 12; editor.spellcheck = false;
    editor.dataset.surfaceField = "recipe";
    editorLabel.appendChild(editor); root.appendChild(editorLabel);
    function setRecipe(value) { editor.value = JSON.stringify(value, null, 2); }
    function recipe() { return JSON.parse(editor.value); }
    button("plane", "10 mm plane", function () { setRecipe(plane()); });
    button("cylinder", "R5 quarter cylinder", function () { setRecipe(cylinder()); });
    const parameters = document.createElement("div");
    parameters.className = "surfaces-parameters"; root.appendChild(parameters);
    function input(name, caption, value, type) {
      const label = document.createElement("label"); label.textContent = text(name, caption);
      const node = document.createElement("input"); node.type = type || "text";
      node.dataset.surfaceField = name; node.value = value;
      label.appendChild(node); parameters.appendChild(label); return node;
    }
    const bounds = input("bounds", "Trim UV [u0,u1,v0,v1]", "[0.2,0.8,0.25,0.75]");
    const thickness = input("thickness", "Thickness (mm)", "1", "number");
    thickness.step = "0.001"; thickness.min = "0.000001";
    const inward = input("inward", "Inward", "", "checkbox");
    button("trim", "Wrap trim", function () {
      setRecipe({op: "trim", surface: recipe(), bounds: JSON.parse(bounds.value)});
    });
    button("thicken", "Wrap thicken", function () {
      const nm = Number(thickness.value) * 1000000;
      if (!Number.isSafeInteger(nm) || nm <= 0) throw new Error("thickness_requires_positive_integer_nm");
      setRecipe({op: "thicken", surface: recipe(), thickness_nm: nm, inward: inward.checked});
    });
    button("sew", "Two-plane seam", function () {
      const left = plane(), right = plane();
      right.poles_nm.forEach(function (row) { row.forEach(function (p) { p[0] += 10000000; }); });
      setRecipe({op: "sew", surfaces: [left, right], solid: false, seams: [seam()]});
    });
    const actions = document.createElement("div");
    actions.className = "surfaces-actions"; root.appendChild(actions);
    const canvas = document.createElement("canvas");
    canvas.className = "surfaces-preview"; canvas.setAttribute("aria-label", text("preview", "Exact surface mesh preview"));
    root.appendChild(canvas);
    const metrics = document.createElement("pre");
    metrics.className = "surfaces-metrics"; metrics.setAttribute("aria-live", "polite");
    root.appendChild(metrics);
    const model = {busy: false, triangles: 0, draws: 0, response: null, error: ""};
    let disposed = false, sequence = 0, gl, program, buffer, mesh;
    function fail(error) {
      model.error = String(error.message || error);
      status.textContent = model.error; root.dataset.surfaceState = "error";
    }
    function busy(value) {
      model.busy = value; root.setAttribute("aria-busy", String(value));
      root.querySelectorAll("button").forEach(function (node) { node.disabled = value; });
    }
    const request = options.request || async function (path, body) {
      const response = await window.fetch(path, body === undefined ? {} : {
        method: "POST", headers: {"Content-Type": "application/json", "X-OpenCAD-Request": "1"},
        credentials: "same-origin", body: JSON.stringify(body)
      });
      const value = await response.json();
      if (!response.ok || value.ok !== true) throw new Error(value.first_cause || value.detail || value.error);
      return value;
    };
    function shader(type, source) {
      const item = gl.createShader(type); gl.shaderSource(item, source); gl.compileShader(item);
      if (!gl.getShaderParameter(item, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(item));
      gl.attachShader(program, item);
    }
    function draw() {
      if (!mesh || disposed) return;
      if (!gl) {
        gl = canvas.getContext("webgl", {antialias: true, preserveDrawingBuffer: true});
        if (!gl) throw new Error("webgl_unavailable");
        program = gl.createProgram();
        shader(gl.VERTEX_SHADER, "attribute vec3 p;attribute vec3 n;uniform vec2 aspect;varying float l;" +
          "void main(){vec3 v=vec3(0.7071*(p.x-p.y),0.40825*(p.x+p.y)+0.8165*p.z,0.57735*(p.x+p.y-p.z));" +
          "gl_Position=vec4(v.xy*aspect,v.z*0.2,1.0);l=0.3+0.7*abs(dot(n,normalize(vec3(-0.4,0.6,1.0))));}");
        shader(gl.FRAGMENT_SHADER, "precision mediump float;varying float l;" +
          "void main(){gl_FragColor=vec4(vec3(0.23,0.48,0.53)*l,1.0);}");
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
        buffer = gl.createBuffer(); gl.enable(gl.DEPTH_TEST);
      }
      const box = mesh.properties.bbox_mm, points = mesh.vertices_mm;
      const center = [0, 1, 2].map(function (i) { return (box[i] + box[i + 3]) / 2; });
      const span = Math.max(box[3] - box[0], box[4] - box[1], box[5] - box[2]);
      if (!(span > 0)) throw new Error("invalid_surface_extent");
      const data = [];
      mesh.triangles.forEach(function (tri) {
        const p = tri.map(function (i) { return points[i]; });
        if (p.some(function (v) { return !v || v.length !== 3; })) throw new Error("invalid_surface_triangle");
        const u = p[1].map(function (v, i) { return v - p[0][i]; });
        const v = p[2].map(function (w, i) { return w - p[0][i]; });
        const n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
        const length = Math.hypot.apply(null, n);
        if (!(length > 0)) throw new Error("degenerate_surface_triangle");
        p.forEach(function (point) {
          point.forEach(function (x, i) { data.push((x - center[i]) / span); });
          n.forEach(function (x) { data.push(x / length); });
        });
      });
      const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
      canvas.width = width * Math.min(window.devicePixelRatio || 1, 2);
      canvas.height = height * Math.min(window.devicePixelRatio || 1, 2);
      gl.viewport(0, 0, canvas.width, canvas.height); gl.useProgram(program);
      gl.clearColor(0.92, 0.94, 0.94, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      ["p", "n"].forEach(function (key, i) {
        const location = gl.getAttribLocation(program, key); gl.enableVertexAttribArray(location);
        gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 24, i * 12);
      });
      gl.uniform2f(gl.getUniformLocation(program, "aspect"), 1.4 * Math.min(1, height / width),
        1.4 * Math.min(1, width / height));
      gl.drawArrays(gl.TRIANGLES, 0, mesh.triangles.length * 3);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("surface_webgl_error");
      model.triangles = mesh.triangles.length; model.draws += 1;
    }
    function show(response) {
      model.response = response; model.error = "";
      if (response.surface && response.surface.recipe) setRecipe(response.surface.recipe);
      if (response.result) {
        mesh = response.result.mesh; draw();
        metrics.textContent = JSON.stringify({
          area_mm2: response.result.properties.area_mm2,
          volume_mm3: response.result.properties.volume_mm3,
          seams: response.result.seams.map(function (s) {
            return {requested: s.requested, passed: s.passed, gap_nm: s.max_gap_nm,
              angle_rad: s.max_angle_rad, curvature_per_mm: s.max_curvature_difference_per_mm,
              whole_domain_proof: s.whole_domain_proof};
          })
        }, null, 2);
        if (options.onMesh) options.onMesh(mesh, response);
      } else {
        mesh = null; model.triangles = 0; metrics.textContent = "";
        if (gl) { gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); }
        if (options.onMesh) options.onMesh(null, response);
      }
      status.textContent = response.empty ? text("empty", "No saved surface.") :
        text("ready", "Exact kernel result ready.");
      root.dataset.surfaceState = "ready";
    }
    async function perform(body) {
      if (model.busy) throw new Error("surface_request_in_progress");
      const current = ++sequence; busy(true);
      try {
        const response = await request("/api/surfaces", body);
        if (!disposed && current === sequence) show(response);
        return response;
      } finally { if (!disposed && current === sequence) busy(false); }
    }
    button("apply", "Validate & save candidate", function () { return perform({recipe: recipe()}); }, actions);
    button("reload", "Read saved surfaces", function () { return perform(); }, actions);
    async function download(fmt) {
      if (model.busy) throw new Error("surface_request_in_progress");
      const current = ++sequence;
      busy(true);
      try {
        const response = await window.fetch("/api/surfaces/" + fmt, {credentials: "same-origin"});
        if (!response.ok) {
          const value = await response.json();
          throw new Error(value.first_cause || value.detail || value.error || "surface_export_failed");
        }
        const blob = await response.blob();
        const digest = response.headers.get("X-OpenCAD-Artifact-SHA256");
        const documentSHA = response.headers.get("X-OpenCAD-Document-SHA256");
        if (!blob.size || !/^[a-f0-9]{64}$/.test(digest || "") ||
            !/^[a-f0-9]{64}$/.test(documentSHA || "") ||
            response.headers.get("Content-Disposition") !== 'attachment; filename="surface.' + fmt + '"') {
          throw new Error("surface_export_receipt_invalid");
        }
        const actual = Array.from(new Uint8Array(await crypto.subtle.digest(
          "SHA-256", await blob.arrayBuffer()))).map(function (byte) {
          return byte.toString(16).padStart(2, "0");
        }).join("");
        if (actual !== digest) throw new Error("surface_export_bytes_changed");
        if (disposed || current !== sequence) return;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url; anchor.download = "surface." + fmt;
        try { anchor.click(); } finally {
          setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        }
        model.error = "";
        status.textContent = text("downloaded", "Saved exact surface downloaded; unsaved recipe retained.");
        root.dataset.surfaceState = "ready";
      } finally { if (!disposed && current === sequence) busy(false); }
    }
    button("download-step", "Download saved STEP", function () { return download("step"); }, actions);
    button("download-brep", "Download saved BRep", function () { return download("brep"); }, actions);
    setRecipe(plane());
    const observer = new ResizeObserver(function () { try { draw(); } catch (error) { fail(error); } });
    observer.observe(canvas);
    return {root: root, model: model, reload: function () { return perform(); },
      apply: function () { return perform({recipe: recipe()}); }, setRecipe: setRecipe,
      dispose: function () {
        disposed = true; sequence += 1; observer.disconnect();
        if (gl) { gl.deleteBuffer(buffer); gl.deleteProgram(program); }
        root.remove();
      }};
  }
  window.OpenCADSurfaces = Object.freeze({mount: mount});
}());
