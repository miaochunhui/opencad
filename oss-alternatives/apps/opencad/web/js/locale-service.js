/* Public UI language only. Identity and accepted-response gates belong to callers. */
(function () {
  "use strict";
  if (window.OpenCADLocale) throw new Error("locale_service_already_loaded");
  const schema = "opencad.panel-i18n.v1";
  const scriptURL = document.currentScript && document.currentScript.src;
  const roots = new Map();
  const attributes = Object.freeze({
    "data-panel-i18n": null,
    "data-panel-i18n-aria": "aria-label",
    "data-panel-i18n-placeholder": "placeholder",
    "data-panel-i18n-title": "title",
    "data-panel-i18n-alt": "alt"
  });
  let packages = null, locale = null, strings = Object.freeze({});
  let generation = 0, bound = false, loadError = null, alert = null;

  function failure(code) {
    const error = new Error(code);
    error.code = code;
    return error;
  }

  function record(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function names(text) {
    if (typeof text !== "string" || !text.trim()) throw failure("locale_empty_translation");
    const result = Array.from(text.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g), m => m[1]);
    if (/[{}]/.test(text.replace(/\{[A-Za-z][A-Za-z0-9_]*\}/g, ""))) {
      throw failure("locale_invalid_placeholder");
    }
    return Array.from(new Set(result)).sort();
  }

  function dictionary(value, panel) {
    if (!record(value) || !Object.keys(value).length) throw failure("locale_empty_dictionary");
    const copy = Object.create(null);
    Object.keys(value).forEach(function (key) {
      if (!/^[A-Za-z][A-Za-z0-9_.-]*$/.test(key) ||
          (panel && !/^panel\.[a-z][a-z0-9_.]*$/.test(key))) {
        throw failure("locale_invalid_key");
      }
      names(value[key]);
      copy[key] = value[key];
    });
    return Object.freeze(copy);
  }

  function localeCode(value) {
    if (typeof value !== "string" || !/^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$/.test(value)) {
      throw failure("locale_invalid_code");
    }
    return value;
  }

  function validate(payload) {
    if (!record(payload) || Object.keys(payload).sort().join(",") !== "default_locale,locales,schema" ||
        payload.schema !== schema || payload.default_locale !== "en" ||
        !record(payload.locales) || !Object.keys(payload.locales).length) {
      throw failure("locale_invalid_package");
    }
    const result = Object.create(null);
    Object.keys(payload.locales).forEach(function (code) {
      result[localeCode(code)] = dictionary(payload.locales[code], true);
    });
    if (!Object.hasOwn(result, payload.default_locale)) throw failure("locale_default_missing");
    const keys = Object.keys(result[payload.default_locale]).sort();
    Object.values(result).forEach(function (pack) {
      if (Object.keys(pack).sort().join("\n") !== keys.join("\n")) {
        throw failure("locale_key_set_mismatch");
      }
      keys.forEach(function (key) {
        if (names(pack[key]).join(",") !== names(result[payload.default_locale][key]).join(",")) {
          throw failure("locale_placeholder_set_mismatch");
        }
      });
    });
    return Object.freeze(result);
  }

  function uniqueJSON(text) {
    const value = JSON.parse(text);
    const tokens = text.match(/"(?:[^"\\]|\\.)*"|[{}[\]:,]|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null/g);
    let index = 0;
    function scan() {
      const token = tokens[index++];
      if (token === "{") {
        const seen = new Set();
        if (tokens[index] === "}") { index += 1; return; }
        do {
          const key = JSON.parse(tokens[index++]);
          if (seen.has(key)) throw failure("locale_duplicate_json_key");
          seen.add(key);
          index += 1;
          scan();
        } while (tokens[index++] === ",");
      } else if (token === "[") {
        if (tokens[index] === "]") { index += 1; return; }
        do { scan(); } while (tokens[index++] === ",");
      }
    }
    scan();
    return value;
  }

  function available() {
    const values = [];
    document.querySelectorAll("#lang-select option, #locale option").forEach(function (option) {
      const code = localeCode(option.value);
      if (!values.includes(code)) values.push(code);
    });
    return values.sort();
  }

  function snapshot() {
    const supported = packages ? Object.keys(packages).sort() : [];
    const selectable = available();
    const unsupported = packages ? selectable.filter(code => !supported.includes(code)) : [];
    if (packages && locale && !supported.includes(locale) && !unsupported.includes(locale)) {
      unsupported.push(locale);
      unsupported.sort();
    }
    return Object.freeze({
      schema: schema, status: loadError ? "error" : !packages ? "loading" :
        Object.hasOwn(packages, locale) ? "ready" : "unsupported",
      locale: locale, generation: generation, bound: bound,
      available_locales: Object.freeze(selectable),
      supported_locales: Object.freeze(supported),
      unsupported: Object.freeze(unsupported),
      error: loadError ? Object.freeze({code: loadError.code}) : null
    });
  }

  function publicFailureCode(state) {
    if (loadError) {
      return loadError.code === "locale_package_http_error" ?
        "locale_package_http_error" : "locale_package_load_failed";
    }
    return state.unsupported.length ? "locale_unsupported" : null;
  }

  function publish() {
    const state = snapshot();
    const code = publicFailureCode(state);
    if (code) {
      if (!alert && document.body) {
        alert = document.createElement("output");
        alert.setAttribute("role", "alert");
        alert.setAttribute("lang", "en");
        alert.setAttribute("data-opencad-public-locale-error", "foundation");
        document.body.append(alert);
      }
      if (alert) {
        alert.hidden = false;
        alert.textContent = code;
      }
    } else if (alert) {
      alert.textContent = "";
      alert.hidden = true;
    }
    window.dispatchEvent(new CustomEvent("opencad:locale", {detail: state}));
    return state;
  }

  function translate(code, core, key, args) {
    if (!packages) throw failure(loadError ? loadError.code : "locale_not_ready");
    if (!Object.hasOwn(packages, code)) throw failure("locale_unsupported");
    if (typeof key !== "string" || !key) throw failure("locale_missing_key");
    const pool = key.startsWith("panel.") ? packages[code] : core;
    if (!Object.hasOwn(pool, key)) throw failure("locale_missing_key");
    const template = pool[key], required = names(template);
    const values = args === undefined ? {} : args;
    if (!record(values)) throw failure("locale_invalid_args");
    if (Object.keys(values).sort().join(",") !== required.join(",")) {
      throw failure("locale_argument_set_mismatch");
    }
    required.forEach(function (name) {
      if (!(typeof values[name] === "string" ||
          (typeof values[name] === "number" && Number.isFinite(values[name])) ||
          typeof values[name] === "boolean")) throw failure("locale_invalid_arg_value");
    });
    return template.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, function (_, name) {
      return String(values[name]);
    });
  }

  function t(key, args) {
    return translate(locale, strings, key, args);
  }

  function changes(root, code, core) {
    if (!root || typeof root.querySelectorAll !== "function") throw failure("locale_invalid_root");
    const selected = [];
    Object.keys(attributes).forEach(function (attribute) {
      const nodes = Array.from(root.querySelectorAll("[" + attribute + "]"));
      if (root.hasAttribute && root.hasAttribute(attribute)) nodes.unshift(root);
      nodes.forEach(function (node) {
        const target = attributes[attribute];
        if (target === null && (node.children.length ||
            /^(INPUT|SELECT|TEXTAREA|SCRIPT|STYLE|CANVAS)$/.test(node.tagName))) {
          throw failure("locale_text_requires_leaf");
        }
        const raw = node.getAttribute("data-panel-i18n-args");
        let args;
        try { args = raw === null ? undefined : JSON.parse(raw); }
        catch (_) { throw failure("locale_invalid_binding_args"); }
        selected.push({node: node, target: target,
          text: translate(code, core, node.getAttribute(attribute), args)});
      });
    });
    return selected;
  }

  function paint(selected) {
    selected.forEach(function (change) {
      if (change.target === null) change.node.textContent = change.text;
      else change.node.setAttribute(change.target, change.text);
    });
  }

  function update(value) {
    if (!record(value) || Object.keys(value).sort().join(",") !== "locale,strings") {
      throw failure("locale_invalid_update");
    }
    if (loadError) throw failure(loadError.code);
    const next = localeCode(value.locale), core = dictionary(value.strings, false);
    let selected = [];
    if (packages && Object.hasOwn(packages, next)) {
      roots.forEach(function (_, root) { selected = selected.concat(changes(root, next, core)); });
    }
    locale = next; strings = core; bound = true; generation += 1;
    paint(selected);
    return publish();
  }

  function bind(root) {
    const selected = changes(root, locale, strings);
    const token = {};
    if (!roots.has(root)) roots.set(root, new Set());
    roots.get(root).add(token);
    paint(selected);
    return function () {
      const tokens = roots.get(root);
      if (tokens) {
        tokens.delete(token);
        if (!tokens.size) roots.delete(root);
      }
    };
  }

  window.addEventListener("opencad:state", function (event) {
    if (!event.detail || event.detail.ok !== true) throw failure("locale_unaccepted_state");
    update({locale: event.detail.locale, strings: event.detail.strings});
  });
  document.addEventListener("DOMContentLoaded", publish, {once: true});
  const ready = (async function () {
    try {
      if (!scriptURL) throw failure("locale_script_url_missing");
      // Capture fetch before the private session adapter; dictionary loading is public.
      const response = await window.fetch(new URL("../panel-i18n.json", scriptURL).href,
        {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw failure("locale_package_http_error");
      packages = validate(uniqueJSON(await response.text()));
      if (locale === null) locale = "en";
      publish();
      return snapshot();
    } catch (error) {
      loadError = failure(error.code || "locale_package_load_failed");
      publish();
      throw loadError;
    }
  }());
  window.OpenCADLocale = Object.freeze({ready: ready, snapshot: snapshot,
    update: update, t: t, bind: bind});
}());
