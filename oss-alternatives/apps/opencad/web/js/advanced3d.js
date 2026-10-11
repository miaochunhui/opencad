(function () {
  "use strict";
  var panel = document.getElementById("advanced"), tree = document.getElementById("feature-tree");
  var status = document.getElementById("status"), current = null, measurement = null, measuring = false;
  function fail(error) {
    // A superseded refresh must not replace the newer accepted model's status.
    if (error && error.code === "refresh_superseded") return;
    status.textContent = String(error);
  }
  function label(key) {
    if (!current || !current.strings[key]) throw new Error("missing_translation:" + key);
    return current.strings[key];
  }
  function renderMeasurement() {
    var output = document.getElementById("clearance-result");
    if (!measurement) { output.textContent = ""; return; }
    var data = measurement.measurement;
    output.textContent = label("solid.clearance") + ": " + label("solid." + data.status) +
      " / " + (data.distance_nm / 1000000) + " mm / " +
      (data.interference_volume_nm3 / 1e18) + " mm\u00b3 / " +
      JSON.stringify(data.nearest_points_nm.map(function (pair) {
        return pair.map(function (point) { return point.map(function (nm) { return nm / 1000000; }); });
      })) + " mm";
  }
  function render(state) {
    current = state;
    if (measurement && measurement.document_sha256 !== window.opencad3d.document_sha256) {
      measurement = null;
    }
    renderMeasurement();
    var documentValue = JSON.parse(state.doc), sketches = document.getElementById("profile");
    var previous = sketches.value; sketches.textContent = "";
    documentValue.sketches.forEach(function (sketch, index) {
      var option = document.createElement("option"); option.value = sketch.uuid;
      option.textContent = (index + 1) + " / " + sketch.uuid; sketches.appendChild(option);
    });
    if (Array.from(sketches.options).some(function (option) { return option.value === previous; })) {
      sketches.value = previous;
    }
    tree.textContent = "";
    documentValue.features.forEach(function (feature) {
      var row = document.createElement("li"), name = document.createElement("span");
      name.textContent = label("solid." + feature.kind) + " / " + feature.uuid.slice(0, 8);
      var toggle = document.createElement("button"); toggle.type = "button";
      toggle.textContent = label(feature.suppressed ? "solid.resume" : "solid.suppress");
      // The shared command stays busy until mesh and state rendering both finish.
      toggle.disabled = Boolean(window.opencad3d.busy);
      toggle.addEventListener("click", function () {
        execute("suppress_solid", {feature_uuid: feature.uuid, suppressed: !feature.suppressed}).catch(fail);
      });
      var edit = document.createElement("button"); edit.type = "button";
      edit.textContent = label("solid.edit");
      edit.disabled = Boolean(window.opencad3d.busy);
      edit.addEventListener("click", function () {
        document.getElementById("feature-edit-id").value = feature.uuid;
        document.getElementById("feature-parameters").value = JSON.stringify(feature.params, null, 2);
        document.getElementById("feature-parameters").focus();
      });
      row.appendChild(name); row.appendChild(toggle); row.appendChild(edit); tree.appendChild(row);
    });
    return state;
  }
  function execute(command, args) {
    var model = window.opencad3d;
    if (!model || typeof model.command !== "function") {
      return Promise.reject(new Error("workspace_not_loaded"));
    }
    return model.command(command, args);
  }
  function nanometres(value) {
    if ((typeof value !== "number" && typeof value !== "string") ||
        (typeof value === "string" && !value.trim())) throw new Error("invalid_length_mm");
    var mm = Number(value);
    if (!Number.isFinite(mm)) throw new Error("invalid_length_mm");
    var nm = Math.round(mm * 1000000);
    if (!Number.isSafeInteger(nm)) throw new Error("length_out_of_range");
    return nm;
  }
  function integers(value) {
    var values = value.split(",").map(function (item) {
      if (!/^\s*\d+\s*$/.test(item)) throw new Error("invalid_edge_or_face_index");
      return Number(item);
    });
    if (!values.length || values.some(function (item) { return !Number.isSafeInteger(item) || item < 1; })) {
      throw new Error("invalid_edge_or_face_index");
    }
    return values;
  }
  function points(value) {
    var data = JSON.parse(value);
    if (!Array.isArray(data) || !data.length || data.some(function (point) {
      return !Array.isArray(point) || point.length !== 3 || point.some(function (item) {
        return typeof item !== "number" || !Number.isFinite(item);
      });
    })) throw new Error("invalid_points_mm");
    return data.map(function (point) { return point.map(nanometres); });
  }
  function measureClearance(translation, button) {
    var model = window.opencad3d;
    if (!current || !model || !model.document_sha256) {
      return Promise.reject(new Error("workspace_not_loaded"));
    }
    if (model.busy) return Promise.reject(new Error("operation_in_progress"));
    if (measuring) return Promise.reject(new Error("measurement_in_progress"));
    var digest = model.document_sha256;
    measuring = true; measurement = null; renderMeasurement(); button.disabled = true;
    return fetch("/api/clearance", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({translation_nm: translation})}).then(function (response) {
      return response.json().then(function (value) {
        if (!response.ok || !value.ok) {
          throw new Error((value.error || "request_failed") + ": " + (value.detail || value.reason || ""));
        }
        return value;
      });
    }).then(function (value) {
      if (model.busy || model.document_sha256 !== digest ||
          value.document_sha256 !== digest) throw new Error("stale_clearance_measurement");
      var data = value.measurement;
      if (!data || ["separated", "touching", "penetrating"].indexOf(data.status) === -1 ||
          !Number.isSafeInteger(data.distance_nm) || data.distance_nm < 0 ||
          !Number.isInteger(data.interference_volume_nm3) || data.interference_volume_nm3 < 0 ||
          !Array.isArray(data.nearest_points_nm) || !data.nearest_points_nm.length ||
          data.nearest_points_nm.some(function (pair) {
            return !Array.isArray(pair) || pair.length !== 2 || pair.some(function (point) {
              return !Array.isArray(point) || point.length !== 3 || point.some(function (nm) {
                return !Number.isSafeInteger(nm);
              });
            });
          })) throw new Error("invalid_clearance_measurement");
      measurement = value; renderMeasurement();
      status.textContent = label("status.ready");
      return value;
    }).finally(function () {
      measuring = false; button.disabled = Boolean(model.busy);
    });
  }
  panel.addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.target, operation = form.getAttribute("data-solid");
      var data = new FormData(form), args = {};
      if (operation === "sweep_solid") {
        args = {sketch_uuid: document.getElementById("profile").value,
          path_points_nm: points(data.get("path"))};
      } else if (operation === "loft_solid") {
        args = {profile_uuids: String(data.get("profiles")).split(",").map(function (v) { return v.trim(); }),
          offsets_nm: points(data.get("offsets")), ruled: data.get("ruled") === "on"};
      } else if (operation === "fillet_solid" || operation === "chamfer_solid") {
        args = {size_nm: nanometres(data.get("size")), edge_indices: integers(data.get("edges"))};
      } else if (operation === "shell_solid") {
        args = {thickness_nm: nanometres(data.get("thickness")),
          face_indices: integers(data.get("faces")), inward: true};
      } else if (operation === "pattern_solid") {
        args = {translations_nm: points(data.get("translations")), fuse: data.get("fuse") === "on"};
        if (args.translations_nm.length > 256) throw new Error("too_many_pattern_instances");
      } else if (operation === "mirror_solid") {
        args = {normal_axis: data.get("axis"), offset_nm: nanometres(data.get("offset")),
          keep_original: data.get("keep") === "on"};
      } else if (operation === "measure_clearance") {
        var vector = JSON.parse(data.get("translation"));
        if (!Array.isArray(vector) || vector.length !== 3 || vector.some(function (item) {
          return typeof item !== "number" || !Number.isFinite(item);
        })) throw new Error("invalid_translation_mm");
        measureClearance(vector.map(nanometres), form.querySelector("button")).catch(fail);
        return;
      } else if (operation === "edit_feature_params") {
        args = {feature_uuid: document.getElementById("feature-edit-id").value,
          params: JSON.parse(document.getElementById("feature-parameters").value)};
      } else if (operation === "import_step") {
        var file = form.querySelector("input[type=file]").files[0];
        if (!file || file.size > 8 * 1024 * 1024) throw new Error("invalid_step_file_size");
        file.text().then(function (text) { return execute(operation, {step_text: text}); }).catch(fail);
        return;
      } else throw new Error("unknown_solid_operation");
      execute(operation, args).catch(fail);
    } catch (error) { fail(error); }
  });
  window.addEventListener("opencad:state", function (event) {
    try { render(event.detail); } catch (error) { fail(error); }
  });
  window.opencadAdvanced = {render: render, execute: execute};
}());
