/* Private service adapter. Credentials stay in HttpOnly cookies, never storage. */
(function () {
  "use strict";
  const rawFetch = window.fetch.bind(window);
  let project = null;
  let base = null;
  let queue = Promise.resolve();
  let pending = null;
  let conflicted = false;
  let identity = null;
  let needsLogin = false;
  let epoch = 0, locked = false;
  const shielded = new Map();
  let viewRecovery = false, checkingView = false;
  let notice;
  let retry;
  let refresh;
  let login;
  let recovery;
  let unlock;
  const ready = new Promise(function (resolve) { unlock = resolve; });
  const storageKey = "opencad-project-id";
  const copies = new Map();
  let localeFault, localeFailure = null, localeTicket = 0;
  const PUBLIC_LOCALE_CODES = Object.freeze({
    foundation: Object.freeze(["locale_package_http_error", "locale_package_load_failed", "locale_unsupported"]),
    parametric: Object.freeze(["parametric_locale_initialization_failed"]),
    private: Object.freeze(["private_locale_initialization_failed"])
  });

  function publicLocaleDiagnostic(node) {
    if (!node || node.tagName !== "OUTPUT" || node.parentNode !== document.body ||
        typeof node.getAttributeNames !== "function") { return false; }
    const names = node.getAttributeNames().slice().sort();
    if (names.length !== 3 || names[0] !== "data-opencad-public-locale-error" ||
        names[1] !== "lang" || names[2] !== "role") { return false; }
    const owner = node.getAttribute("data-opencad-public-locale-error");
    if (!Object.hasOwn(PUBLIC_LOCALE_CODES, owner)) { return false; }
    return !node.children.length && !node.hasAttribute("hidden") && node.hidden !== true &&
      node.getAttribute("role") === "alert" && node.getAttribute("lang") === "en" &&
      PUBLIC_LOCALE_CODES[owner].includes(node.textContent);
  }

  function localeError(failure, key) {
    const candidate = String(failure && (failure.code || failure.message) || failure || "");
    const code = /^[a-z][a-z0-9_]{2,80}$/.test(candidate) ? candidate : "session_locale_failure";
    const coordinate = typeof key === "string" && /^panel\.session\.[a-z0-9_]+$/.test(key) ? key : "";
    if (!localeFailure) { localeFailure = {code: code, key: coordinate}; }
    else if (!localeFailure.key && coordinate && localeFailure.code === code) { localeFailure.key = coordinate; }
    document.body.dataset.opencadSessionLocaleError = localeFailure.code;
    if (localeFault) {
      const coordinate = localeFailure.code + (localeFailure.key ? " @ " + localeFailure.key : "");
      let text = "Session translations unavailable: " + coordinate;
      try { text = window.OpenCADLocale.t("panel.session.locale_unavailable", {code: coordinate}); }
      catch (_) { /* The original translation failure remains visible below. */ }
      localeFault.textContent = text;
      localeFault.hidden = false;
    }
  }

  function localeService(loading) {
    const service = window.OpenCADLocale;
    if (!service || typeof service.snapshot !== "function" ||
        typeof service.update !== "function" || typeof service.t !== "function" ||
        typeof service.bind !== "function" || !service.ready ||
        typeof service.ready.then !== "function") {
      throw new Error("session_locale_api_unavailable");
    }
    const state = service.snapshot();
    if (!state) { throw new Error("session_locale_snapshot_invalid"); }
    if (state.status === "error" && state.error && typeof state.error.code === "string" &&
        state.error.code) {
      const failure = new Error(state.error.code);
      failure.code = state.error.code;
      throw failure;
    }
    if (!loading && (state.status === "loading" || state.status === "error" ||
        typeof state.locale !== "string" || !state.locale)) {
      throw new Error("session_locale_snapshot_invalid");
    }
    return service;
  }

  function translation(suffix, fallback, args) {
    try {
      const service = localeService(true), snapshot = service.snapshot();
      if (snapshot.status === "loading") { return fallback; }
      const text = localeService().t("panel.session." + suffix, args || {});
      if (typeof text !== "string" || !text.trim() || text === "panel.session." + suffix) {
        throw new Error("locale_missing_key");
      }
      return text;
    } catch (failure) {
      localeError(failure, "panel.session." + suffix);
      return fallback.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, function (_, name) {
        return args && Object.hasOwn(args, name) ? String(args[name]) : "{" + name + "}";
      });
    }
  }

  function renderCopy(node, entry) {
    if (entry.release) { entry.release(); entry.release = null; }
    const args = entry.args || {};
    node.setAttribute("data-panel-i18n-args", JSON.stringify(args));
    const values = [];
    entry.items.forEach(function (item) {
      node.setAttribute(item.binding, "panel.session." + item.suffix);
      values.push({item: item, text: translation(item.suffix, item.fallback, args)});
    });
    try {
      const service = localeService(true);
      if (service.snapshot().status !== "loading" && node.isConnected) {
        const release = localeService().bind(node);
        if (typeof release !== "function") { throw new Error("session_locale_bind_invalid"); }
        entry.release = release;
      }
    } catch (failure) { localeError(failure, entry.items[0].suffix); }
    values.forEach(function (value) {
      if (value.item.target) { node.setAttribute(value.item.target, value.text); }
      else { node.textContent = value.text + (entry.detail ? "\n" + entry.detail : ""); }
    });
  }

  function copy(node, suffix, fallback, target, args, detail) {
    const attribute = {aria: "aria-label", placeholder: "placeholder", title: "title", alt: "alt"}[target];
    const binding = "data-panel-i18n" + (target ? "-" + target : "");
    const entry = copies.get(node) || {items: [], release: null};
    const item = {suffix: suffix, fallback: fallback, binding: binding, target: attribute};
    const index = entry.items.findIndex(function (value) { return value.binding === binding; });
    if (index < 0) { entry.items.push(item); } else { entry.items[index] = item; }
    entry.args = args || {};
    entry.detail = detail || "";
    copies.set(node, entry);
    renderCopy(node, entry);
  }

  function refreshCopy() {
    copies.forEach(function (entry, node) {
      if (!node.isConnected) {
        if (entry.release) { entry.release(); }
        copies.delete(node);
      } else { renderCopy(node, entry); }
    });
    if (localeFailure) { localeError(localeFailure.code, localeFailure.key); }
  }

  function localeNotification(value, generation) {
    current(generation);
    if (!identity || needsLogin || (locked && !checkingView) || !value ||
        value.ok !== true || value.project_id !== project) { return; }
    const ticket = ++localeTicket, owner = identity, identifier = project;
    let service;
    try {
      service = localeService(true);
      if (typeof value.locale !== "string" || !value.locale ||
          !value.strings || typeof value.strings !== "object" || Array.isArray(value.strings)) {
        throw new Error("session_locale_response_invalid");
      }
    } catch (failure) { localeError(failure); return; }
    Promise.resolve(service.ready).then(function () {
      current(generation);
      if (ticket !== localeTicket || identity !== owner || project !== identifier ||
          needsLogin || (locked && !checkingView)) { return; }
      service.update({locale: value.locale, strings: value.strings});
      current(generation);
    }).catch(function (failure) {
      if (generation === epoch && failure.message !== "session_boundary_changed") {
        localeError(failure);
      }
    });
  }

  function originalDetail(error) {
    return String(error.sessionDetail || error.message || error);
  }

  function sessionError(suffix, fallback, detail) {
    const failure = new Error(translation(suffix, fallback));
    failure.sessionKey = suffix;
    failure.sessionFallback = fallback;
    failure.sessionDetail = detail || "";
    return failure;
  }

  window.addEventListener("opencad:locale", refreshCopy);
  try {
    Promise.resolve(localeService(true).ready).then(refreshCopy).catch(localeError);
  } catch (failure) { localeError(failure); }

  function boundaryError() {
    return new Error("session_boundary_changed");
  }

  function current(generation) {
    if (generation !== epoch) { throw boundaryError(); }
  }

  function shield() {
    epoch += 1;
    locked = true;
    document.body.dataset.opencadSessionLocked = "true";
    if (document.activeElement && document.activeElement.blur) {
      document.activeElement.blur();
    }
    shieldWorkspace();
  }

  function shieldWorkspace() {
    Array.from(document.body.children).forEach(function (node) {
      if (node.matches(".project-status,[data-session-entry],[data-session-recovery],script,style,link")) {
        return;
      }
      if (publicLocaleDiagnostic(node)) { return; }
      if (!shielded.has(node)) {
        shielded.set(node, {visibility: node.style.visibility,
          priority: node.style.getPropertyPriority("visibility"), inert: node.inert});
      }
      node.style.setProperty("visibility", "hidden", "important");
      node.inert = true;
    });
  }

  function unshield() {
    epoch += 1;
    shielded.forEach(function (saved, node) {
      node.style.setProperty("visibility", saved.visibility, saved.priority);
      node.inert = saved.inert;
    });
    shielded.clear();
    locked = false;
    delete document.body.dataset.opencadSessionLocked;
  }

  // Body consumption can finish after a logout even when fetch already returned.
  function guarded(response, generation) {
    ["json", "text", "blob", "arrayBuffer", "formData"].forEach(function (method) {
      const consume = response[method].bind(response);
      response[method] = async function () {
        current(generation);
        const value = await consume();
        current(generation);
        return value;
      };
    });
    const clone = response.clone.bind(response);
    response.clone = function () {
      current(generation);
      return guarded(clone(), generation);
    };
    return response;
  }

  async function raw(path, body) {
    const generation = epoch;
    const options = body === undefined ? {} : {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-OpenCAD-Request": "1"},
      body: JSON.stringify(body)
    };
    const response = await rawFetch(path, options);
    current(generation);
    const value = await response.json();
    current(generation);
    if (!response.ok || value.ok !== true) {
      const failure = sessionError("request_failed", "Request failed. Check your connection and account.",
        value.detail || value.error);
      failure.status = response.status;
      throw failure;
    }
    return value;
  }

  function showError(node, error) {
    if (error.message === "session_boundary_changed") { return; }
    copy(node, error.sessionKey || "request_failed",
      error.sessionFallback || "Request failed. Check your connection and account.",
      null, {}, error.sessionKey ? error.sessionDetail : originalDetail(error));
  }

  async function activate(identifier) {
    const generation = epoch;
    const response = await rawFetch("/api/state", {
      headers: {"X-OpenCAD-Project": identifier}
    });
    current(generation);
    const value = await response.json();
    current(generation);
    if (!response.ok || value.ok !== true) {
      throw sessionError("project_unavailable", "Project unavailable. Check the current account and project.",
        value.detail || value.error);
    }
    if (value.project_id !== identifier || !Number.isSafeInteger(value.revision) ||
        !/^[a-f0-9]{64}$/.test(value.sha256 || "")) {
      throw sessionError("project_unavailable", "Project unavailable. Check the current account and project.",
        "project_state_identity_or_version_invalid");
    }
    project = identifier;
    base = {revision: value.revision, sha256: value.sha256};
    sessionStorage.setItem(storageKey, JSON.stringify({
      owner_id: identity, project_id: identifier
    }));
    localeNotification(value, generation);
  }

  function remember(value) {
    if (value && value.project_id === project && Number.isSafeInteger(value.revision)
        && typeof value.sha256 === "string") {
      if (!base || value.revision >= base.revision) {
        base = {revision: value.revision, sha256: value.sha256};
      }
    }
  }

  function inform(suffix, fallback, args, detail) {
    if (notice) { copy(notice, suffix, fallback, null, args, detail); }
    if (retry) { retry.hidden = !pending; }
    if (refresh) { refresh.hidden = !(conflicted || (locked && !needsLogin)); }
    if (login) { login.hidden = !needsLogin; }
  }

  function expired() {
    needsLogin = true;
    shield();
    if (pending) {
      inform("expired_pending", "Your session expired. Sign in with the original account, then confirm the previous edit. Its request is retained on this page.");
    } else { inform("expired", "Your session expired. Sign in again."); }
  }

  async function recoverView() {
    if (checkingView) return;
    checkingView = true;
    conflicted = true;
    viewRecovery = true;
    inform("view_checking", "The edit is confirmed saved. Checking the latest view. Unsubmitted forms remain on this page.");
    const generation = epoch;
    try {
      const session = await raw("/api/auth/me");
      current(generation);
      if (!session.user || session.user.user_id !== identity) {
        expired();
        throw boundaryError();
      }
      const known = base;
      const workspace = window.opencad3d || window.OpenCADWorkspace;
      if (!workspace || typeof workspace.refresh !== "function") {
        throw new Error("workspace_refresh_unavailable");
      }
      const state = await workspace.refresh();
      current(generation);
      if (state.project_id !== project || !Number.isSafeInteger(state.revision) ||
          state.revision < known.revision ||
          !/^[a-f0-9]{64}$/.test(state.sha256 || "") ||
          (state.revision === known.revision && state.sha256 !== known.sha256)) {
        throw new Error("recovered_view_version_conflict");
      }
      const response = await window.fetch("/api/state");
      const latest = await response.json();
      current(generation);
      if (!response.ok || latest.ok !== true || latest.project_id !== state.project_id ||
          latest.revision !== state.revision || latest.sha256 !== state.sha256) {
        throw new Error("recovered_view_changed");
      }
      remember(latest);
      if (base.revision !== state.revision || base.sha256 !== state.sha256) {
        throw new Error("recovered_view_behind_known_head");
      }
      conflicted = false;
      viewRecovery = false;
      if (locked) { unshield(); }
      localeNotification(latest, epoch);
      inform("view_verified", "The edit is confirmed saved and the latest view is verified. Unsubmitted forms remain; review them before saving.");
    } catch (error) {
      if (generation !== epoch || error.message === "session_boundary_changed") { return; }
      if (error.status === 401) { expired(); }
      inform("view_unverified", "The edit is confirmed saved, but the view is not verified: {detail}. Check the latest view. The original request will not be submitted again.",
        {detail: originalDetail(error)});
    } finally { checkingView = false; }
  }

  async function send(url, options, mutation, generation) {
    current(generation);
    if (locked && !checkingView) { throw boundaryError(); }
    if (mutation && needsLogin) {
      throw sessionError("original_login_required", "Sign in with the original account first. Unconfirmed edits will not replay automatically.");
    }
    if (mutation && (pending || conflicted)) {
      throw pending ? sessionError("previous_pending", "The previous edit is unconfirmed. Confirm its save result first.") :
        sessionError("project_changed", "The project has a newer version. Refresh and review the drawing before editing again.");
    }
    const operation = {url: url, options: options};
    const sent = mutation ? JSON.parse(options.body) : null;
    const submittedBase = sent ? Object.freeze({
      project_id: options.headers.get("X-OpenCAD-Project"),
      expected_revision: sent.expected_revision, expected_sha: sent.expected_sha,
      request_id: sent.request_id
    }) : null;
    if (mutation) { pending = operation; }
    let response;
    try {
      response = await rawFetch(url, options);
    } catch (failure) {
      current(generation);
      if (mutation) {
        inform("connection_pending", "Connection lost; the save result is unconfirmed. Confirm the previous edit. Do not repeat it.", {}, originalDetail(failure));
      } else if (!options.signal || !options.signal.aborted) {
        shield();
        inform("connection_shielded", "Connection lost. The old workspace is hidden. Verify the latest version before restoring it. Unsubmitted forms remain.", {}, originalDetail(failure));
      }
      throw failure;
    }
    current(generation);
    if (response.status === 401) { expired(); }
    const responseGeneration = response.status === 401 ? epoch : generation;
    let value;
    try {
      if ((response.headers.get("Content-Type") || "").startsWith("application/json")) {
        value = await response.clone().json();
      }
    } catch (failure) {
      current(responseGeneration);
      if (mutation) { inform("response_pending", "The response is invalid and the save result is unconfirmed. Confirm the previous edit.", {}, originalDetail(failure)); }
      else if (response.status !== 401) {
        shield();
        inform("response_shielded", "The response is invalid. The old workspace is hidden. Verify the latest version before restoring it.", {}, originalDetail(failure));
      }
      throw failure;
    }
    current(responseGeneration);
    if (response.ok && value && value.ok === true) {
      if (url.split("?")[0] === "/api/state" || url.split("?")[0] === "/api/locale") {
        localeNotification(value, generation);
      }
      remember(value);
      if (mutation) {
        pending = null;
        inform("edit_saved", "Edit saved.");
        window.dispatchEvent(new CustomEvent("opencad:project-commit", {
          detail: {path: url.split("?")[0], base: submittedBase, receipt: value}
        }));
      }
    } else if (!mutation && response.status !== 401 && !response.ok) {
      shield();
      inform("read_refused", "Read refused. The old workspace is hidden. Check the current account and project.",
        {}, value && (value.detail || value.error));
      return response;
    } else if (mutation) {
      // A server-side failure may happen after COMMIT. Only the receipt replay
      // can disambiguate it; do not manufacture a fresh request ID.
      if (response.status === 401) {
        return guarded(response, responseGeneration);
      } else if (response.status >= 500 || !value) {
        inform("save_unconfirmed", "The service did not confirm the save result. Confirm the previous edit.",
          {}, value && (value.detail || value.error));
      } else {
        pending = null;
        conflicted = response.status === 409;
        if (conflicted) {
          inform("version_conflict", "Project version conflict. Refresh and review the latest drawing.", {}, value.detail || value.error);
        } else {
          inform("edit_refused", "Edit refused. The drawing was not changed.", {}, value.detail || value.error);
        }
      }
    }
    // A 401 carries the original server refusal, never the previous document.
    return guarded(response, responseGeneration);
  }

  async function download(path, extension, generation) {
    current(generation);
    if (locked) { throw boundaryError(); }
    let response;
    try {
      response = await rawFetch(path, {headers: {"X-OpenCAD-Project": project}});
    } catch (failure) {
      current(generation);
      shield();
      inform("connection_shielded", "Connection lost. The old workspace is hidden. Verify the latest version before restoring it. Unsubmitted forms remain.", {}, originalDetail(failure));
      throw failure;
    }
    current(generation);
    if (!response.ok) {
      if (response.status === 401) { expired(); }
      else { inform("export_failed", "Export failed. Check the project state."); }
      return response;
    }
    const blob = await response.blob();
    current(generation);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "drawing." + extension;
    anchor.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    return new Response(JSON.stringify({ok: true, bytes: blob.size,
      download: "drawing." + extension}), {
      status: 200, headers: {"Content-Type": "application/json"}
    });
  }

  window.fetch = async function (input, init) {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      return rawFetch(input, init);
    }
    await ready;
    const generation = epoch;
    const original = new Request(input, init);
    const method = original.method.toUpperCase();
    const originalBody = method === "POST" ? await original.text() : "";
    current(generation);
    if (method === "GET" && url.pathname === "/api/open") {
      return new Response(JSON.stringify({ok: false, error: "local_file_required",
        detail: translation("local_file_required", "Import a local file to open a drawing. Private projects do not accept server file paths.")}), {
        status: 405, headers: {"Content-Type": "application/json"}
      });
    }
    async function execute() {
      current(generation);
      const options = {method: method, headers: new Headers(original.headers),
        credentials: "same-origin", signal: original.signal};
      options.headers.set("X-OpenCAD-Project", project);
      let mutates = false;
      if (method === "POST") {
        options.headers.set("X-OpenCAD-Request", "1");
        const body = JSON.parse(originalBody || "{}");
        if (url.pathname === "/api/auth/login" || url.pathname === "/api/auth/logout") {
          if (pending) { throw sessionError("confirm_before_switch", "Confirm the previous edit before switching accounts."); }
          shield();
          options.body = JSON.stringify(body);
          const response = await rawFetch(url.pathname, options);
          if (!response.ok && response.status !== 401) {
            inform("switch_unconfirmed", "The account switch is unconfirmed. The old workspace remains hidden. Restore the original account or try signing out again.");
            return response;
          }
          sessionStorage.removeItem(storageKey);
          // Never return a new identity into the old panels' private closures.
          setTimeout(function () { location.reload(); }, 0);
          throw boundaryError();
        }
        if (url.pathname === "/api/export/dxf") { return download("/api/dxf", "dxf", generation); }
        if (url.pathname === "/api/export/step") { return download("/api/step", "step", generation); }
        if (url.pathname === "/api/command" && body.command === "export_pdf") {
          return download("/api/pdf", "pdf", generation);
        }
        const queries = new Set(["snap_query", "precision_pick", "precision_status",
          "grip_positions", "boundary_probe", "layer_state", "layer_visible_entities",
          "measure_area", "measure_perimeter", "measure_distance"]);
        mutates = url.pathname !== "/api/locale"
          && url.pathname !== "/api/clearance"
          && url.pathname !== "/api/render"
          && !(url.pathname === "/api/surfaces" && body.action === "check_seam")
          && !(url.pathname === "/api/command" && queries.has(body.command));
        if (mutates) {
          if (url.pathname === "/api/save") { delete body.path; }
          body.expected_revision = base.revision;
          body.expected_sha = base.sha256;
          body.request_id = crypto.randomUUID();
        }
        options.body = JSON.stringify(body);
      }
      return send(url.pathname + url.search, options, mutates, generation);
    }
    if (method === "POST") {
      const result = queue.then(execute);
      queue = result.then(function () {}, function () {});
      return result;
    }
    return execute();
  };
  window.OpenCADProjectSession = Object.freeze({ready: ready});

  async function boot() {
    new MutationObserver(function () {
      if (locked) { shieldWorkspace(); }
    }).observe(document.body, {childList: true});
    const bar = document.createElement("section");
    bar.className = "project-status";
    notice = document.createElement("output");
    notice.setAttribute("role", "status");
    localeFault = document.createElement("output");
    localeFault.setAttribute("role", "alert");
    localeFault.hidden = true;
    if (localeFailure) { localeError(localeFailure.code, localeFailure.key); }
    retry = document.createElement("button");
    retry.type = "button";
    copy(retry, "confirm_edit", "Confirm previous edit");
    retry.hidden = true;
    retry.addEventListener("click", async function () {
      if (!pending) { return; }
      if (needsLogin) { recovery(); return; }
      retry.disabled = true;
      const operation = pending;
      const generation = epoch;
      try {
        const response = await rawFetch(operation.url, Object.assign({}, operation.options,
          {signal: undefined}));
        current(generation);
        const value = await response.json();
        current(generation);
        if (response.ok && value.ok === true) {
          conflicted = true;
          remember(value);
          pending = null;
          await recoverView();
        } else if (response.status < 500 && response.status !== 401) {
          pending = null;
          conflicted = true;
          inform("previous_not_submitted", "The previous edit was not submitted. Refresh and review the latest drawing.", {}, value.detail || value.error);
        } else {
          if (response.status === 401) { expired(); }
          else { inform("still_unconfirmed", "The save result is still unconfirmed. Keep this page open.", {}, value.detail || value.error); }
        }
      } catch (failure) {
        if (generation === epoch && failure.message !== "session_boundary_changed") {
          inform("confirm_failed", "Cannot confirm the save result. Keep this page open and confirm again when the connection returns.", {}, originalDetail(failure));
        }
      }
      finally { retry.disabled = false; }
    });
    refresh = document.createElement("button");
    refresh.type = "button";
    copy(refresh, "refresh", "Refresh latest version");
    refresh.hidden = true;
    refresh.addEventListener("click", function () {
      if (viewRecovery || locked) { recoverView(); }
      else { location.reload(); }
    });
    login = document.createElement("button");
    login.type = "button";
    copy(login, "login_again", "Sign in again");
    login.hidden = true;
    recovery = function () {
      if (document.querySelector("dialog[data-session-recovery]")) { return; }
      const dialog = document.createElement("dialog");
      dialog.dataset.sessionRecovery = "true";
      dialog.addEventListener("cancel", function (event) {
        event.preventDefault();
        dialog.close();
        dialog.remove();
        refreshCopy();
      });
      const heading = document.createElement("h1");
      copy(heading, "recovery_heading", "Restore the original account");
      const explanation = document.createElement("p");
      copy(explanation, "recovery_explanation", "The unconfirmed edit retains its original request ID. After signing in, confirm it manually. It will not be saved again automatically.");
      const error = document.createElement("p");
      error.setAttribute("role", "alert");
      const form = document.createElement("form");
      const username = document.createElement("input");
      username.name = "username";
      username.autocomplete = "username";
      copy(username, "original_account", "Original account", "aria");
      copy(username, "original_account", "Original account", "placeholder");
      username.required = true;
      const password = document.createElement("input");
      password.name = "password";
      password.type = "password";
      password.autocomplete = "current-password";
    copy(password, "password_label", "Password", "aria");
    copy(password, "password_label", "Password", "placeholder");
      password.required = true;
      const submit = document.createElement("button");
      submit.type = "submit";
      copy(submit, "restore_login", "Restore sign-in");
      const cancel = document.createElement("button");
      cancel.type = "button";
      copy(cancel, "later_login", "Keep this page; sign in later");
      cancel.addEventListener("click", function () { dialog.close(); dialog.remove(); refreshCopy(); });
      form.append(username, password, submit, cancel);
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        submit.disabled = true;
        const generation = epoch;
        try {
          const ticket = await raw("/api/auth/login",
            {username: username.value, password: password.value});
          password.value = "";
          if (!ticket.user || ticket.user.user_id !== identity) {
            await raw("/api/auth/logout", {});
            throw sessionError("wrong_account", "This account does not match the current project. Sign in with the original account. The edit is retained.");
          }
          current(generation);
          needsLogin = false;
          dialog.close();
          dialog.remove();
          refreshCopy();
          if (pending) {
            inform("login_restored", "Sign-in with the original account is restored. Confirm the previous edit manually. It has not replayed automatically.");
          } else {
            await recoverView();
          }
        } catch (failure) {
          password.value = "";
          if (generation === epoch && failure.message !== "session_boundary_changed") {
            showError(error, failure);
          }
        } finally { submit.disabled = false; }
      });
      dialog.append(heading, explanation, error, form);
      document.body.append(dialog);
      refreshCopy();
      dialog.showModal();
    };
    login.addEventListener("click", recovery);
    const exit = document.createElement("button");
    exit.type = "button";
    copy(exit, "logout", "Sign out");
    exit.addEventListener("click", async function () {
      if (pending) { inform("confirm_before_logout", "Confirm the previous edit before signing out."); return; }
      shield();
      try {
        await raw("/api/auth/logout", {});
        sessionStorage.removeItem(storageKey);
        location.reload();
      } catch (failure) {
        if (failure.status === 401) {
          sessionStorage.removeItem(storageKey);
          location.reload();
        } else { inform("logout_unconfirmed", "Sign-out is unconfirmed. The old workspace remains hidden: {detail}", {detail: originalDetail(failure)}); }
      }
    });
    bar.append(notice, localeFault, retry, refresh, login, exit);
    document.body.prepend(bar);
    window.addEventListener("beforeunload", function (event) {
      if (pending) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("pageshow", function (event) {
      if (event.persisted) { shield(); location.reload(); }
    });
    const overlay = document.createElement("dialog");
    overlay.dataset.sessionEntry = "true";
    const title = document.createElement("h1");
    copy(title, "heading", "OpenCAD private projects");
    const explanation = document.createElement("p");
    copy(explanation, "explanation", "Sign in and choose a project. Each edit is saved as a persistent version. Other users cannot access your drawings.");
    const error = document.createElement("p");
    error.setAttribute("role", "alert");
    const form = document.createElement("form");
    const username = document.createElement("input");
    username.autocomplete = "username";
    username.name = "username";
    copy(username, "account", "Account", "placeholder");
    copy(username, "account", "Account", "aria");
    username.required = true;
    const password = document.createElement("input");
    password.type = "password";
    password.name = "password";
    password.autocomplete = "current-password";
    copy(password, "password_label", "Password", "placeholder");
    copy(password, "password_label", "Password", "aria");
    password.required = true;
    const submit = document.createElement("button");
    submit.type = "submit";
    copy(submit, "login", "Sign in");
    form.append(username, password, submit);
    overlay.append(title, explanation, error, form);
    document.body.append(overlay);
    overlay.addEventListener("cancel", function (event) { event.preventDefault(); });
    overlay.showModal();
    refreshCopy();

    async function choose() {
      form.replaceChildren();
      const value = await raw("/api/projects");
      const select = document.createElement("select");
      copy(select, "project", "Project", "aria");
      value.projects.forEach(function (item) {
        const option = document.createElement("option");
        option.value = item.project_id;
        option.textContent = item.title;
        select.append(option);
      });
      let stored = null;
      try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey));
        if (saved && saved.owner_id === identity) { stored = saved.project_id; }
      } catch (_) {
        // Legacy unbound selection is not an identity authority.
      }
      if (value.projects.some(function (item) { return item.project_id === stored; })) {
        select.value = stored;
      }
      const open = document.createElement("button");
      open.type = "button";
      copy(open, "open_project", "Open project");
      open.disabled = value.projects.length === 0;
      open.addEventListener("click", async function () {
        try {
          await activate(select.value);
          overlay.close();
          overlay.remove();
          refreshCopy();
          unlock();
        } catch (failure) { showError(error, failure); }
      });
      const name = document.createElement("input");
      copy(name, "new_project_name", "New project name", "placeholder");
      copy(name, "new_project_name", "New project name", "aria");
      const create = document.createElement("button");
      create.type = "button";
      copy(create, "create_project", "Create project");
      let creation = null;
      create.addEventListener("click", async function () {
        create.disabled = true;
        try {
          if (!name.value.trim()) { throw sessionError("project_name_required", "Enter a project name."); }
          if (!creation) {
            creation = {title: name.value.trim(), request_id: crypto.randomUUID()};
          }
          const created = await raw("/api/projects", creation);
          await activate(created.project_id);
          overlay.close();
          overlay.remove();
          refreshCopy();
          unlock();
        } catch (failure) {
          showError(error, failure);
          if (failure.status && failure.status < 500) { creation = null; }
          create.disabled = false;
        }
      });
      const logout = document.createElement("button");
      logout.type = "button";
      copy(logout, "logout", "Sign out");
      logout.addEventListener("click", async function () {
        try {
          await raw("/api/auth/logout", {});
          sessionStorage.removeItem(storageKey);
          location.reload();
        }
        catch (failure) { showError(error, failure); }
      });
      form.append(select, open, name, create, logout);
      refreshCopy();
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      submit.disabled = true;
      try {
        const ticket = await raw("/api/auth/login",
          {username: username.value, password: password.value});
        identity = ticket.user.user_id;
        password.value = "";
        await choose();
      } catch (failure) {
        showError(error, failure);
        submit.disabled = false;
      }
    });
    try {
      const session = await raw("/api/auth/me");
      identity = session.user.user_id;
      await choose();
    } catch (failure) {
      if (failure.status !== 401) { showError(error, failure); }
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }
})();
