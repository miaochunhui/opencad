(function () {
  "use strict";
  var canvas = document.getElementById("model"), status = document.getElementById("status");
  var gl = canvas.getContext("webgl", {antialias: true, preserveDrawingBuffer: true});
  var model = {triangles: 0, frames: 0, yaw: -Math.PI/4, pitch: -Math.atan(Math.sqrt(2)),
    view: "iso", zoom: 1, pan: [0, 0], error: ""};
  window.opencad3d = model;
  var program, buffer, count = 0, state, labels = {}, busy = false, refreshSequence = 0;
  function fail(error) {
    if (error && error.code === "refresh_superseded") return;
    model.error = String(error); status.textContent = model.error;
  }
  function superseded() {
    var error = new Error("refresh_superseded");
    error.code = "refresh_superseded";
    return error;
  }
  function report(error) { fail(error); }
  function setBusy(value) {
    busy = value; model.busy = value;
    document.querySelectorAll("#advanced button, #apply, #undo, #redo, #locale").forEach(function (node) {
      node.disabled = value;
    });
  }
  function request(path, body) {
    return fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})
      .then(function (r) { return r.json(); }).then(function (r) {
        if (!r.ok) throw new Error((r.error || "request_failed") + ": " + (r.detail || r.reason || ""));
        return r;
      });
  }
  function translate(s) {
    state = s; labels = s.strings;
    var features = JSON.parse(s.doc).features.filter(function (feature) {
      return !feature.suppressed;
    });
    var latest = features[features.length - 1];
    if (latest && latest.kind === "extrude" && latest.params &&
        document.getElementById("depth").dataset.dirty !== "true" &&
        Number.isFinite(latest.params.distance_nm)) {
      document.getElementById("depth").value = latest.params.distance_nm / 1000000;
    }
    document.documentElement.lang = s.locale;
    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      var key = node.getAttribute("data-i18n");
      if (!labels[key]) throw new Error("missing_translation:" + key);
      node.textContent = labels[key];
    });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (node) {
      var key = node.getAttribute("data-i18n-aria");
      if (!labels[key]) throw new Error("missing_translation:" + key);
      node.setAttribute("aria-label", labels[key]);
    });
    var select = document.getElementById("locale");
    select.value = s.locale;
  }
  document.getElementById("depth").addEventListener("input", function (event) {
    event.target.dataset.dirty = "true";
  });
  function shader(type, source) {
    var sh = gl.createShader(type); gl.shaderSource(sh, source); gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
    return sh;
  }
  function init() {
    if (!gl) throw new Error("webgl_unavailable");
    program = gl.createProgram();
    gl.attachShader(program, shader(gl.VERTEX_SHADER,
      "attribute vec3 p; attribute vec3 n; uniform vec2 rotation; uniform vec2 scale; uniform vec2 pan; varying float light;" +
      "vec3 rotate(vec3 v){ float cy=cos(rotation.x),sy=sin(rotation.x),cp=cos(rotation.y),sp=sin(rotation.y);" +
      "vec3 a=vec3(cy*v.x-sy*v.y,sy*v.x+cy*v.y,v.z); return vec3(a.x,cp*a.y-sp*a.z,sp*a.y+cp*a.z);}" +
      "void main(){vec3 v=rotate(p); vec3 normal=rotate(n); gl_Position=vec4(v.xy*scale+pan,-v.z*0.25,1.0);" +
      "light=0.35+0.65*abs(dot(normalize(normal),normalize(vec3(-0.4,0.6,1.0))));}"));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER,
      "precision mediump float; varying float light; void main(){gl_FragColor=vec4(vec3(0.48,0.58,0.76)*light,1.0);}"));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    buffer = gl.createBuffer();
    var info = gl.getExtension("WEBGL_debug_renderer_info");
    model.renderer = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    document.getElementById("renderer").textContent = model.renderer;
    gl.enable(gl.DEPTH_TEST);
    canvas.addEventListener("webglcontextlost", function (event) { event.preventDefault(); fail("webgl_context_lost"); });
    canvas.addEventListener("webglcontextrestored", function () {
      try { init(); refresh(); } catch (error) { fail(error); }
    }, {once: true});
  }
  function upload(mesh) {
    var vertices = mesh.vertices_mm, triangles = mesh.triangles, box = mesh.properties.bbox_nm;
    if (!vertices.length || !triangles.length || !box || box.length !== 6) throw new Error("invalid_mesh");
    var center = [0, 1, 2].map(function (i) { return (box[i] + box[i + 3]) / 2000000; });
    var radius = Math.max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) / 1000000;
    if (!(radius > 0)) throw new Error("invalid_model_extent");
    var data = [];
    triangles.forEach(function (tri) {
      var a = vertices[tri[0]], b = vertices[tri[1]], c = vertices[tri[2]];
      if (!a || !b || !c) throw new Error("invalid_triangle_index");
      var u = b.map(function (v, i) { return v - a[i]; }), v = c.map(function (x, i) { return x - a[i]; });
      var n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
      var norm = Math.hypot.apply(null, n);
      if (!(norm > 0)) throw new Error("degenerate_triangle");
      [a, b, c].forEach(function (p) {
        p.forEach(function (value, i) { data.push((value - center[i]) / radius); });
        n.forEach(function (value) { data.push(value / norm); });
      });
    });
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    count = triangles.length * 3; model.triangles = triangles.length;
    model.volume_mm3 = mesh.properties.volume_nm3 / 1e18; model.bbox_nm = box;
    var metrics = document.getElementById("metrics"); metrics.textContent = "";
    [["mm\u00b3", model.volume_mm3.toFixed(3)], ["mm", [0, 1, 2].map(function (i) { return ((box[i+3]-box[i])/1000000).toFixed(2); }).join(" x ")]]
      .forEach(function (pair) { var dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = pair[0]; dd.textContent = pair[1]; metrics.appendChild(dt); metrics.appendChild(dd); });
  }
  function draw() {
    if (!gl || !program) return;
    var ratio = Math.min(window.devicePixelRatio || 1, 2), w = Math.round(canvas.clientWidth * ratio), h = Math.round(canvas.clientHeight * ratio);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h); gl.clearColor(0.9, 0.92, 0.96, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(program); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    ["p", "n"].forEach(function (key, i) { var loc = gl.getAttribLocation(program, key);
      gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 24, i * 12); });
    gl.uniform2f(gl.getUniformLocation(program, "rotation"), model.yaw, model.pitch);
    gl.uniform2f(gl.getUniformLocation(program, "scale"), 1.6 * model.zoom * Math.min(1, h/w), 1.6 * model.zoom * Math.min(1, w/h));
    gl.uniform2f(gl.getUniformLocation(program, "pan"), model.pan[0], model.pan[1]);
    gl.drawArrays(gl.TRIANGLES, 0, count);
    if (gl.getError() !== gl.NO_ERROR) throw new Error("webgl_render_error");
    model.frames += 1;
  }
  model.fit = function () {
    endDrag();
    model.pan = [0, 0]; model.zoom = 1; draw();
  };
  model.setView = function (name) {
    var views = {front: [0, -Math.PI/2], top: [0, 0],
      right: [-Math.PI/2, -Math.PI/2], iso: [-Math.PI/4, -Math.atan(Math.sqrt(2))]};
    if (!Object.prototype.hasOwnProperty.call(views, name)) throw new Error("unsupported_view:" + name);
    model.yaw = views[name][0]; model.pitch = views[name][1]; model.view = name;
    model.fit();
  };
  model.draw = draw;
  model.benchmark = function (frames) {
    var start = performance.now();
    for (var frame = 0; frame < frames; frame += 1) {
      model.yaw += 0.001; draw(); gl.finish();
    }
    return {frames: frames, wall_ms: performance.now() - start, renderer: model.renderer};
  };
  model.pixelCount = function () {
    draw(); gl.finish();
    var pixels = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    var found = 0;
    for (var i = 0; i < pixels.length; i += 4) if (pixels[i] < 210) found += 1;
    return found;
  };
  function refresh() {
    var sequence = ++refreshSequence;
    model.error = "";
    return request("/api/state").then(function (s) {
      return request("/api/mesh").then(function (r) {
        if (s.project_id !== undefined &&
            (s.project_id !== r.project_id || s.revision !== r.revision ||
             s.sha256 !== r.sha256)) throw new Error("project_snapshot_conflict");
        return crypto.subtle.digest("SHA-256", new TextEncoder().encode(s.doc)).then(function (digest) {
          var expected = Array.from(new Uint8Array(digest)).map(function (value) {
            return value.toString(16).padStart(2, "0");
          }).join("");
          if (expected !== r.document_sha256) throw new Error("document_snapshot_conflict");
          return {state: s, mesh: r};
        });
      });
    }).then(function (snapshot) {
      if (sequence !== refreshSequence) throw superseded();
      var r = snapshot.mesh;
      translate(snapshot.state);
      model.document_sha256 = r.document_sha256;
      if (r.solids.length) { upload(r.solids[0]); status.textContent = labels["status.ready"]; }
      else {
        count = 0; model.triangles = 0;
        model.volume_mm3 = null; model.bbox_nm = null;
        document.getElementById("metrics").textContent = "";
        var key = "viewer3d." + r.reason;
        if (!labels[key]) throw new Error("missing_translation:" + key);
        status.textContent = labels[key];
      }
      draw();
      window.dispatchEvent(new CustomEvent("opencad:state", {detail: snapshot.state}));
      return snapshot.state;
    }).catch(function (error) {
      if (sequence !== refreshSequence) throw superseded();
      if (sequence === refreshSequence) fail(error);
      throw error;
    });
  }
  model.refresh = refresh;
  function command(name, args) {
    if (busy) return Promise.reject(new Error("operation_in_progress"));
    setBusy(true);
    return request("/api/command", {command: name, args: args || {}}).then(refresh)
      .catch(function (error) { fail(error); throw error; })
      .finally(function () { setBusy(false); });
  }
  model.command = command;
  document.getElementById("extrusion").addEventListener("submit", function (event) {
    event.preventDefault(); var depth = Number(document.getElementById("depth").value);
    if (!Number.isFinite(depth) || depth <= 0 || depth > 10000) { fail("invalid_extrusion_depth"); return; }
    if (!state) { fail("workspace_not_loaded"); return; }
    var features = JSON.parse(state.doc).features.filter(function (f) { return !f.suppressed; });
    command(features.length ? "edit_solid" : "extrude", features.length ?
      {feature_uuid: features[features.length - 1].uuid, distance_nm: Math.round(depth * 1000000)} :
      {distance_nm: Math.round(depth * 1000000)}).catch(report);
  });
  ["undo", "redo"].forEach(function (name) { document.getElementById(name).addEventListener("click", function () { command(name).catch(report); }); });
  document.getElementById("locale").addEventListener("change", function (event) {
    if (busy) { report("operation_in_progress"); return; }
    setBusy(true);
    request("/api/locale", {locale: event.target.value}).then(refresh).catch(report)
      .finally(function () { setBusy(false); });
  });
  document.getElementById("step").addEventListener("click", function () {
    fetch("/api/step").then(function (r) {
      if (!r.ok || r.headers.get("Content-Type") !== "application/step") {
        return r.json().then(function (body) { throw new Error(body.detail || body.error || "step_export_failed"); });
      }
      return r.blob();
    })
      .then(function (blob) { var url = URL.createObjectURL(blob), a = document.createElement("a");
        a.href = url; a.download = "opencad.step"; a.click(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000); }).catch(fail);
  });
  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      model.setView(button.getAttribute("data-view"));
    });
  });
  var drag = null;
  function endDrag(event) {
    if (!drag || (event && event.pointerId !== drag.id)) return;
    var id = drag.id;
    drag = null;
    if (canvas.hasPointerCapture(id)) canvas.releasePointerCapture(id);
  }
  canvas.addEventListener("pointerdown", function (e) {
    if (e.button !== 0 || drag) return;
    e.preventDefault(); canvas.focus(); canvas.setPointerCapture(e.pointerId);
    drag = {id: e.pointerId, x: e.clientX, y: e.clientY};
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var bounds = canvas.getBoundingClientRect();
    if (!(e.buttons & 1) || e.clientX < bounds.left || e.clientX >= bounds.right ||
        e.clientY < bounds.top || e.clientY >= bounds.bottom) { endDrag(e); return; }
    var dx = e.clientX-drag.x, dy = e.clientY-drag.y;
    if (e.shiftKey) { model.pan[0] += dx/canvas.clientWidth*2; model.pan[1] -= dy/canvas.clientHeight*2; }
    else { model.yaw += dx*0.01; model.pitch += dy*0.01; model.view = "custom"; }
    drag.x = e.clientX; drag.y = e.clientY; draw();
  });
  ["pointerup", "pointercancel", "pointerleave", "lostpointercapture"].forEach(function (name) {
    canvas.addEventListener(name, endDrag);
  });
  window.addEventListener("blur", function () { endDrag(); });
  document.addEventListener("keydown", function (event) {
    var target = event.target, active = document.activeElement;
    function editing(node) {
      return node && ((node.closest && node.closest("input,select,textarea")) || node.isContentEditable);
    }
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey ||
        editing(target) || editing(active)) return;
    var key = event.key.toLowerCase(), views = {f: "front", t: "top", r: "right", i: "iso"};
    if (key === "home") { event.preventDefault(); model.fit(); }
    else if (Object.prototype.hasOwnProperty.call(views, key)) {
      event.preventDefault(); model.setView(views[key]);
    }
  });
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault(); model.zoom = Math.max(0.1, Math.min(8, model.zoom*Math.exp(-e.deltaY*0.001))); draw();
  }, {passive: false});
  window.addEventListener("resize", function () { try { draw(); } catch (e) { fail(e); } });
  try {
    init();
    request("/api/catalogs").then(function (r) {
      var select = document.getElementById("locale");
      r.catalogs.filter(function (catalog) { return catalog.valid; }).forEach(function (catalog) {
        var option = document.createElement("option");
        option.value = catalog.code; option.textContent = catalog.native_name; select.add(option);
      });
      return refresh();
    }).catch(fail);
  } catch (error) { fail(error); }
}());
