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
  let viewRecovery = false, checkingView = false;
  let notice;
  let retry;
  let refresh;
  let login;
  let recovery;
  let unlock;
  const ready = new Promise(function (resolve) { unlock = resolve; });
  const storageKey = "opencad-project-id";

  async function raw(path, body) {
    const options = body === undefined ? {} : {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-OpenCAD-Request": "1"},
      body: JSON.stringify(body)
    };
    const response = await rawFetch(path, options);
    const value = await response.json();
    if (!response.ok || value.ok !== true) {
      const failure = new Error(value.detail || value.error || "请求失败");
      failure.status = response.status;
      throw failure;
    }
    return value;
  }

  function showError(node, error) {
    node.textContent = String(error.message || error);
  }

  async function activate(identifier) {
    const response = await rawFetch("/api/state", {
      headers: {"X-OpenCAD-Project": identifier}
    });
    const value = await response.json();
    if (!response.ok || value.ok !== true) {
      throw new Error(value.detail || value.error || "Project unavailable");
    }
    project = identifier;
    base = {revision: value.revision, sha256: value.sha256};
    sessionStorage.setItem(storageKey, identifier);
  }

  function remember(value) {
    if (value && value.project_id === project && Number.isSafeInteger(value.revision)
        && typeof value.sha256 === "string") {
      if (!base || value.revision >= base.revision) {
        base = {revision: value.revision, sha256: value.sha256};
      }
    }
  }

  function inform(message) {
    if (notice) { notice.textContent = message; }
    if (retry) { retry.hidden = !pending; }
    if (refresh) { refresh.hidden = !conflicted; }
    if (login) { login.hidden = !needsLogin; }
  }

  function expired() {
    needsLogin = true;
    inform(pending ? "登录已失效，请以原账号重新登录，再确认上一笔编辑。原请求仍保留在此页面。" :
      "登录已失效，请重新登录。");
  }

  async function recoverView() {
    if (checkingView) return;
    checkingView = true;
    conflicted = true;
    viewRecovery = true;
    inform("编辑已确认保存，正在核对最新视图。未提交的表单保留在此页。");
    try {
      const known = base;
      const workspace = window.opencad3d || window.OpenCADWorkspace;
      if (!workspace || typeof workspace.refresh !== "function") {
        throw new Error("workspace_refresh_unavailable");
      }
      const state = await workspace.refresh();
      if (state.project_id !== project || !Number.isSafeInteger(state.revision) ||
          state.revision < known.revision ||
          !/^[a-f0-9]{64}$/.test(state.sha256 || "") ||
          (state.revision === known.revision && state.sha256 !== known.sha256)) {
        throw new Error("recovered_view_version_conflict");
      }
      const response = await window.fetch("/api/state");
      const latest = await response.json();
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
      inform("编辑已确认保存，最新视图已核对。未提交表单仍保留，请检查后再保存。");
    } catch (error) {
      inform("编辑已确认保存，但视图未验证：" + String(error.message || error) +
        "。请核对最新视图，不会再次提交原请求。");
    } finally { checkingView = false; }
  }

  async function send(url, options, mutation) {
    if (mutation && needsLogin) {
      throw new Error("请先以原账号重新登录，未确认的编辑不会自动重放。");
    }
    if (mutation && (pending || conflicted)) {
      throw new Error(pending ? "上一笔编辑尚未确认，请先确认保存结果。" :
        "项目已有新版本，请刷新后检查图纸，再重新编辑。");
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
      if (mutation) {
        inform("连接中断，编辑是否保存尚未确认。请确认上一笔编辑，勿重复操作。");
      }
      throw failure;
    }
    if (response.status === 401) { expired(); }
    let value;
    try {
      if ((response.headers.get("Content-Type") || "").startsWith("application/json")) {
        value = await response.clone().json();
      }
    } catch (failure) {
      if (mutation) { inform("响应损坏，保存结果尚未确认，请确认上一笔编辑。"); }
      throw failure;
    }
    if (response.ok && value && value.ok === true) {
      remember(value);
      if (mutation) {
        pending = null;
        inform("编辑已保存。");
        window.dispatchEvent(new CustomEvent("opencad:project-commit", {
          detail: {path: url.split("?")[0], base: submittedBase, receipt: value}
        }));
      }
    } else if (mutation) {
      // A server-side failure may happen after COMMIT. Only the receipt replay
      // can disambiguate it; do not manufacture a fresh request ID.
      if (response.status === 401) {
        expired();
      } else if (response.status >= 500 || !value) {
        inform("服务未确认保存结果，请确认上一笔编辑。");
      } else {
        pending = null;
        conflicted = response.status === 409;
        inform(response.status === 401 ? "登录已失效，请重新登录。" :
          conflicted ? "项目版本冲突，请刷新并检查最新图纸。" :
          (value.detail || value.error || "编辑被拒绝，图纸未修改。"));
      }
    }
    return response;
  }

  async function download(path, extension) {
    const response = await rawFetch(path, {headers: {"X-OpenCAD-Project": project}});
    if (!response.ok) {
      if (response.status === 401) { expired(); }
      else { inform("导出失败，请检查项目状态。"); }
      return response;
    }
    const blob = await response.blob();
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
    const original = new Request(input, init);
    const method = original.method.toUpperCase();
    const originalBody = method === "POST" ? await original.text() : "";
    if (method === "GET" && url.pathname === "/api/open") {
      return new Response(JSON.stringify({ok: false, error: "local_file_required",
        detail: "请通过文件导入打开图纸，私有项目不接受服务器文件路径。"}), {
        status: 405, headers: {"Content-Type": "application/json"}
      });
    }
    async function execute() {
      const options = {method: method, headers: new Headers(original.headers),
        credentials: "same-origin", signal: original.signal};
      options.headers.set("X-OpenCAD-Project", project);
      let mutates = false;
      if (method === "POST") {
        options.headers.set("X-OpenCAD-Request", "1");
        const body = JSON.parse(originalBody || "{}");
        if (url.pathname === "/api/export/dxf") { return download("/api/dxf", "dxf"); }
        if (url.pathname === "/api/export/step") { return download("/api/step", "step"); }
        if (url.pathname === "/api/command" && body.command === "export_pdf") {
          return download("/api/pdf", "pdf");
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
      return send(url.pathname + url.search, options, mutates);
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
    const bar = document.createElement("section");
    bar.className = "project-status";
    notice = document.createElement("output");
    notice.setAttribute("role", "status");
    retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "确认上一笔编辑";
    retry.hidden = true;
    retry.addEventListener("click", async function () {
      if (!pending) { return; }
      if (needsLogin) { recovery(); return; }
      retry.disabled = true;
      const operation = pending;
      try {
        const response = await rawFetch(operation.url, Object.assign({}, operation.options,
          {signal: undefined}));
        const value = await response.json();
        if (response.ok && value.ok === true) {
          conflicted = true;
          remember(value);
          pending = null;
          await recoverView();
        } else if (response.status < 500 && response.status !== 401) {
          pending = null;
          conflicted = true;
          inform("上一笔编辑未提交，请刷新并检查最新图纸。");
        } else {
          if (response.status === 401) { expired(); }
          else { inform("保存结果仍未确认，请保留此页面。"); }
        }
      } catch (failure) { inform("无法确认保存结果，请保留此页面，待连接恢复后再次确认。"); }
      finally { retry.disabled = false; }
    });
    refresh = document.createElement("button");
    refresh.type = "button";
    refresh.textContent = "刷新最新版本";
    refresh.hidden = true;
    refresh.addEventListener("click", function () {
      if (viewRecovery) { recoverView(); }
      else { location.reload(); }
    });
    login = document.createElement("button");
    login.type = "button";
    login.textContent = "重新登录";
    login.hidden = true;
    recovery = function () {
      if (document.querySelector("dialog[data-session-recovery]")) { return; }
      const dialog = document.createElement("dialog");
      dialog.dataset.sessionRecovery = "true";
      dialog.addEventListener("cancel", function (event) {
        event.preventDefault();
        dialog.close();
        dialog.remove();
      });
      const heading = document.createElement("h1");
      heading.textContent = "恢复原账号登录";
      const explanation = document.createElement("p");
      explanation.textContent = "未确认的编辑保留原请求编号。登录成功后，请手动确认上一笔编辑，不会自动重复保存。";
      const error = document.createElement("p");
      error.setAttribute("role", "alert");
      const form = document.createElement("form");
      const username = document.createElement("input");
      username.name = "username";
      username.autocomplete = "username";
      username.setAttribute("aria-label", "原账号");
      username.required = true;
      const password = document.createElement("input");
      password.name = "password";
      password.type = "password";
      password.autocomplete = "current-password";
      password.setAttribute("aria-label", "密码");
      password.required = true;
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.textContent = "恢复登录";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.textContent = "保留页面，稍后登录";
      cancel.addEventListener("click", function () { dialog.close(); dialog.remove(); });
      form.append(username, password, submit, cancel);
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        submit.disabled = true;
        try {
          const ticket = await raw("/api/auth/login",
            {username: username.value, password: password.value});
          password.value = "";
          if (!ticket.user || ticket.user.user_id !== identity) {
            await raw("/api/auth/logout", {});
            throw new Error("账号与当前项目不一致。请以原账号登录，原编辑仍保留。");
          }
          needsLogin = false;
          dialog.close();
          dialog.remove();
          inform(pending ? "原账号登录已恢复。请手动确认上一笔编辑，尚未自动重放。" :
            "原账号登录已恢复，可继续访问当前项目。");
        } catch (failure) {
          password.value = "";
          showError(error, failure);
        } finally { submit.disabled = false; }
      });
      dialog.append(heading, explanation, error, form);
      document.body.append(dialog);
      dialog.showModal();
    };
    login.addEventListener("click", recovery);
    const exit = document.createElement("button");
    exit.type = "button";
    exit.textContent = "退出登录";
    exit.addEventListener("click", async function () {
      if (pending) { inform("请先确认上一笔编辑，再退出登录。"); return; }
      try { await raw("/api/auth/logout", {}); location.reload(); }
      catch (failure) { inform(failure.message); }
    });
    bar.append(notice, retry, refresh, login, exit);
    document.body.prepend(bar);
    window.addEventListener("beforeunload", function (event) {
      if (pending) { event.preventDefault(); event.returnValue = ""; }
    });
    const overlay = document.createElement("dialog");
    const title = document.createElement("h1");
    title.textContent = "OpenCAD 私有项目";
    const explanation = document.createElement("p");
    explanation.textContent = "登录后选择项目。每次编辑会保存为持久版本，其他用户无法访问你的图纸。";
    const error = document.createElement("p");
    error.setAttribute("role", "alert");
    const form = document.createElement("form");
    const username = document.createElement("input");
    username.autocomplete = "username";
    username.name = "username";
    username.placeholder = "账号";
    username.setAttribute("aria-label", "账号");
    username.required = true;
    const password = document.createElement("input");
    password.type = "password";
    password.name = "password";
    password.autocomplete = "current-password";
    password.placeholder = "密码";
    password.setAttribute("aria-label", "密码");
    password.required = true;
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = "登录";
    form.append(username, password, submit);
    overlay.append(title, explanation, error, form);
    document.body.append(overlay);
    overlay.addEventListener("cancel", function (event) { event.preventDefault(); });
    overlay.showModal();

    async function choose() {
      form.replaceChildren();
      const value = await raw("/api/projects");
      const select = document.createElement("select");
      select.setAttribute("aria-label", "项目");
      value.projects.forEach(function (item) {
        const option = document.createElement("option");
        option.value = item.project_id;
        option.textContent = item.title;
        select.append(option);
      });
      const stored = sessionStorage.getItem(storageKey);
      if (value.projects.some(function (item) { return item.project_id === stored; })) {
        select.value = stored;
      }
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "打开项目";
      open.disabled = value.projects.length === 0;
      open.addEventListener("click", async function () {
        try {
          await activate(select.value);
          overlay.close();
          overlay.remove();
          unlock();
        } catch (failure) { showError(error, failure); }
      });
      const name = document.createElement("input");
      name.placeholder = "新项目名称";
      name.setAttribute("aria-label", "新项目名称");
      const create = document.createElement("button");
      create.type = "button";
      create.textContent = "创建项目";
      let creation = null;
      create.addEventListener("click", async function () {
        create.disabled = true;
        try {
          if (!name.value.trim()) { throw new Error("请输入项目名称"); }
          if (!creation) {
            creation = {title: name.value.trim(), request_id: crypto.randomUUID()};
          }
          const created = await raw("/api/projects", creation);
          await activate(created.project_id);
          overlay.close();
          overlay.remove();
          unlock();
        } catch (failure) {
          showError(error, failure);
          if (failure.status && failure.status < 500) { creation = null; }
          create.disabled = false;
        }
      });
      const logout = document.createElement("button");
      logout.type = "button";
      logout.textContent = "退出登录";
      logout.addEventListener("click", async function () {
        try { await raw("/api/auth/logout", {}); location.reload(); }
        catch (failure) { showError(error, failure); }
      });
      form.append(select, open, name, create, logout);
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
