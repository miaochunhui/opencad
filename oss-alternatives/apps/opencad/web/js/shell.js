/* SPDX-License-Identifier: Apache-2.0 */
/*
  infra 服务外壳 · 统一顶栏（shell.js）—— 纯静态，零外部依赖，零网络请求
  ===========================================================================
  三件事，都是本文件自带，页面侧只需要一行 <script>（见「接线」）：
    ① 统一顶栏  ：在本页最上方插一条 infra 级导航条（本页自己的 #topbar 原样保留，成为其下第二条）。
    ② 服务互链  ：OpenMath / OpenCAD 两个线上 Web 服务互跳。
    ③ 回主页    ：回 infra 文档中心主页，并直达《面向用户的服务与入口》那一节。

  接线（必须由持有 index.html 写面的席次执行；本文件**不会**自己被加载）
  ---------------------------------------------------------------------------
      <script src="/web/js/shell.js"></script>          <!-- 放在 </body> 之前 -->
  两个 app 各有一份逐字同构的本文件，唯一差异是下面「身份块」三行。

  硬约束（照着写，别改；每条都是本仓实测事实，不是风格偏好）
  ---------------------------------------------------------------------------
  1. CSP 是 script-src 'self'; style-src 'self'（server.py 的 _CSP，无 'unsafe-inline'）
     ⇒ 本文件**不许**建 <style>、**不许** setAttribute('style', …)、**不许**内联事件属性。
     样式一律走 CSSOM 属性赋值（el.style.foo = …）——这是 CSP 不拦的那条路，
     真浏览器实测：样式生效且零 SecurityPolicyViolation（见 /hf3fs-jd/data/tmp/web-i-d6/REPORT.md）。
  2. body 是 display:grid; grid-template-rows:auto minmax(0,1fr) auto（app.css），
     三行按位置对应 #topbar / #workspace / #status。直接把导航条插成第一个子元素会让三行整体错位
     （顶栏掉进 1fr 行、工作区被挤成 auto）⇒ 命中该布局时把行模板改成四行 auto auto minmax(0,1fr) auto。
     布局不认识就退回普通流，并在 data-svc-grid 上**自曝**是哪种（adopted / foreign / flow）。
  3. 不写任何 data-i18n 节点：app.js 的 i18n 重刷按 key 覆盖它们，缺 key 还会被标 data-i18n-missing
     （r7 的 UX-01 就是这个坑）。条上的字由本文件按 <html lang> 自己选，三包 en / zh-CN / ja 内置。
  4. **不编 URL**：对端服务的线上地址没定就是空串，渲染成 unset 态（不是链接），并在 data-svc-state 自曝。
     线上地址定下来后，改本文件的 PEER_URL 一行，或页面加载前给
     window.__INFRA_SVC_SHELL_CONFIG__ = { peerUrls: { opencad: "<对端 https 地址>" } }  —— 两条路都实测过。

  三态口径（0 / -1 / NA 不许同形；机器判据看 data-svc-* 属性，不看颜色）
  ---------------------------------------------------------------------------
    peer 的 data-svc-state:
      live  = 对端地址已定        ⇒ <a href="…"> 可点
      self  = 就是本页自己        ⇒ <span aria-current="page">，不留空链接
      unset = 对端地址还没定      ⇒ <span title="…">，**不给 href**（不猜地址）
    data-svc-grid:
      adopted = 三行网格已改四行（本仓两个 app 的形态） / foreign = 是网格但不是三行，未改 / flow = 不是网格
    data-svc-lang:
      本文件当前按哪门语言渲染；没命中语言包时 data-svc-lang-fallback="1"（en 兜底，不是"已翻译"）
    window.__INFRA_SVC_SHELL__.state() 给出上面全部字段的机读快照（供探针/哨兵取数）
*/
(function () {
  "use strict";

  /* ── 身份块（两个 app 的 shell.js 唯一差异就在这三行） ───────────────── */
  var SERVICE_ID = "opencad";
  var SERVICE_NAME = "OpenCAD";
  var SERVICE_TAGLINE = "在线制图";

  /* ── 常量 ─────────────────────────────────────────────────────────── */
  var VERSION = "d6-1";
  var BAR_ID = "infra-svc-shell";
  var GLOBAL_KEY = "__INFRA_SVC_SHELL__";
  var CFG_KEY = "__INFRA_SVC_SHELL_CONFIG__";
  var HOME_URL = "https://docs.ai4everyone.top/projects/infra/";
  var ENTRY_URL = "https://docs.ai4everyone.top/projects/infra/#sec-user";
  var PEERS = ["openmath", "opencad"];
  var PEER_LABEL = { openmath: "OpenMath", opencad: "OpenCAD" };
  var PEER_URL = { openmath: "", opencad: "" };        // "" = 线上地址未定（不许猜）
  var GRID_4 = "auto auto minmax(0, 1fr) auto";
  var MIN_HIT = 44;                                     // 触达目标下限（design system 口径）

  /* 顶栏三个词条的三个语言包（本文件只对"自己这一条"负责，不碰页面文案） */
  var TEXT = {
    "zh-CN": {
      nav: "infra 服务导航", home: "← infra 主页", entry: "服务与入口", group: "服务互链",
      self: "当前", unset: "未上线", live: "打开",
      homeTitle: "回 infra 文档中心主页（docs.ai4everyone.top）",
      entryTitle: "直达主页《面向用户的服务与入口》一节",
      unsetTitle: "对端服务的线上地址还没定，先不给链接（不猜地址）"
    },
    en: {
      nav: "infra service navigation", home: "← infra home", entry: "Services & entry points",
      group: "Services", self: "current", unset: "not live", live: "open",
      homeTitle: "Back to the infra doc center home (docs.ai4everyone.top)",
      entryTitle: "Jump to the home page section: services and entry points",
      unsetTitle: "The peer service URL is not decided yet; no link is invented."
    },
    ja: {
      nav: "infra サービスナビ", home: "← infra ホーム", entry: "サービスと入口", group: "サービス連携",
      self: "現在", unset: "未公開", live: "開く",
      homeTitle: "infra ドキュメントセンターのホームへ（docs.ai4everyone.top）",
      entryTitle: "ホームの「サービスと入口」へ",
      unsetTitle: "相手サービスの公開 URL は未定です（推測しません）。"
    }
  };

  /* 配色沿用文档中心 B 族令牌（r6 §1.1），不新造颜色 */
  var PALETTE = {
    light: { bg: "#f7f8fa", card: "#ffffff", ink: "#1f2430", ink2: "#5b6472", line: "#dfe3e9", accent: "#1d4ed8", code: "#f2f4f7" },
    dark: { bg: "#14171c", card: "#1c2027", ink: "#e6e9ee", ink2: "#a4adba", line: "#2c323b", accent: "#9db9ff", code: "#22272f" }
  };
  var UI_FONT = "system-ui, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif";
  var MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

  var PAL = PALETTE.light;

  function warn(msg) { try { console.warn("[infra-svc-shell] " + msg); } catch (e) { /* console 不在也不许挂 */ } }

  function css(el, props) {
    for (var k in props) {
      if (Object.prototype.hasOwnProperty.call(props, k)) {
        try { el.style[k] = props[k]; } catch (e) { /* 单条样式写不进不影响其余 */ }
      }
    }
  }

  function el(tag, cls, attrs) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (attrs) {
      for (var k in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] !== null && attrs[k] !== undefined) {
          node.setAttribute(k, String(attrs[k]));
        }
      }
    }
    return node;
  }

  function config() {
    try {
      var c = window[CFG_KEY];
      return (c && typeof c === "object") ? c : {};
    } catch (e) { return {}; }
  }

  /* 只认 https:// 绝对地址；本机自证另放 http://127.0.0.1|localhost。
     别的形状一律拒绝并自曝 —— 「拒绝」与「未定」都渲染成 unset，但属性不同形。 */
  function acceptUrl(raw, what) {
    var v = String(raw === null || raw === undefined ? "" : raw).trim();
    if (!v) return "";
    var ok = v.indexOf("https://") === 0 ||
             /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?(\/|$)/.test(v);
    if (!ok) { warn(what + " 只接受 https:// 绝对地址（本机自证可 http://127.0.0.1），已拒绝并退回 unset：" + v); return ""; }
    return v;
  }

  function homeUrl() { return acceptUrl(config().home, "home"); }
  function entryUrl() { return acceptUrl(config().entry, "entry"); }
  function homeHref() { return homeUrl() || HOME_URL; }
  function entryHref() { return entryUrl() || ENTRY_URL; }

  function peerUrl(id) {
    var c = config().peerUrls || {};
    var chosen = c[id] !== undefined ? c[id] : PEER_URL[id];
    return acceptUrl(chosen, "peerUrls." + id);
  }

  function peerState(id) {
    if (id === SERVICE_ID) return "self";
    return peerUrl(id) ? "live" : "unset";
  }

  function normalizeLang(raw) {
    var v = String(raw || "").trim().toLowerCase();
    if (!v) return "";
    if (TEXT[v]) return v;
    if (v.indexOf("zh") === 0) return "zh-CN";
    if (v.indexOf("ja") === 0) return "ja";
    if (v.indexOf("en") === 0) return "en";
    return "";
  }

  function resolveLang() {
    var seen = [];
    try { seen.push(document.documentElement.getAttribute("lang")); } catch (e) { /* ignore */ }
    try { seen.push(navigator.language); } catch (e) { /* ignore */ }
    try { seen.push((navigator.languages || [])[0]); } catch (e) { /* ignore */ }
    for (var i = 0; i < seen.length; i += 1) {
      var hit = normalizeLang(seen[i]);
      if (hit) return { tag: hit, fallback: false };
    }
    return { tag: "en", fallback: true };
  }

  function prefersDark() {
    try { return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches); }
    catch (e) { return false; }
  }

  /* 焦点可见：:focus-visible 写不进 CSSOM（没有选择器），用 focus/blur 事件补上同一条轮廓 */
  function armFocus(node) {
    node.addEventListener("focus", function () { css(node, { outline: "3px solid " + PAL.accent, outlineOffset: "2px" }); });
    node.addEventListener("blur", function () { css(node, { outline: "none" }); });
  }

  function pill(node, P, kind) {
    var bg = P.card, fg = P.accent, bd = P.line;
    if (kind === "self") { bg = P.code; fg = P.ink; }
    if (kind === "unset") { bg = "transparent"; fg = P.ink2; }
    if (kind === "home") { bg = P.code; fg = P.ink; }
    css(node, {
      display: "inline-flex", alignItems: "center", minHeight: MIN_HIT + "px",
      padding: "0 12px", borderRadius: "999px", border: "1px solid " + bd,
      background: bg, color: fg, fontFamily: UI_FONT, fontSize: "13px", lineHeight: "1.4",
      textDecoration: "none", boxSizing: "border-box", whiteSpace: "nowrap"
    });
  }

  function paint(bar, lang) {
    PAL = prefersDark() ? PALETTE.dark : PALETTE.light;
    var P = PAL;
    css(bar, {
      display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px",
      boxSizing: "border-box", width: "100%", minHeight: MIN_HIT + "px", padding: "6px 12px",
      background: P.card, color: P.ink, borderBottom: "1px solid " + P.line,
      fontFamily: UI_FONT, fontSize: "13px", lineHeight: "1.5"
    });
    var kids = bar.children;
    for (var i = 0; i < kids.length; i += 1) {
      var node = kids[i];
      var role = node.getAttribute("data-svc-role");
      if (role === "home") { pill(node, P, "home"); armFocus(node); }
      else if (role === "entry") { pill(node, P, "entry"); armFocus(node); }
      else if (role === "peer") {
        var st = node.getAttribute("data-svc-state");
        if (st === "live") { pill(node, P, "live"); armFocus(node); }
        else if (st === "self") { pill(node, P, "self"); }
        else { pill(node, P, "unset"); }
      }
      else if (role === "group") {
        css(node, { color: P.ink2, fontSize: "12px", fontFamily: UI_FONT, padding: "0 2px" });
      }
      else if (role === "sep") {
        css(node, { color: P.line, fontFamily: MONO_FONT, fontSize: "12px" });
      }
      else if (role === "brand") {
        css(node, { color: P.ink2, fontFamily: UI_FONT, fontSize: "12px", padding: "0 2px" });
      }
    }
  }

  function render() {
    var bar = document.getElementById(BAR_ID);
    if (!bar) return false;
    var L = resolveLang();
    var T = TEXT[L.tag];
    bar.setAttribute("data-svc-lang", L.tag);
    if (L.fallback) bar.setAttribute("data-svc-lang-fallback", "1");
    else bar.removeAttribute("data-svc-lang-fallback");
    bar.setAttribute("aria-label", T.nav);

    while (bar.firstChild) bar.removeChild(bar.firstChild);

    /* ① 回主页：一处是站点根，一处是主页里那一节 */
    var home = el("a", "infra-svc-shell__home", { "data-svc-role": "home", "data-svc-link": "home", href: homeHref(), title: T.homeTitle });
    home.textContent = T.home;
    bar.appendChild(home);

    var entry = el("a", "infra-svc-shell__entry", { "data-svc-role": "entry", "data-svc-link": "entry", href: entryHref(), title: T.entryTitle });
    entry.textContent = T.entry;
    bar.appendChild(entry);

    bar.appendChild(el("span", "infra-svc-shell__sep", { "data-svc-role": "sep", "aria-hidden": "true" })).textContent = "·";

    /* ② 服务互链：本页自己=self、对端未定=unset、对端已定=live —— 三种形状互不相同 */
    bar.appendChild(el("span", "infra-svc-shell__group", { "data-svc-role": "group" })).textContent = T.group;
    for (var i = 0; i < PEERS.length; i += 1) {
      var id = PEERS[i];
      var st = peerState(id);
      var url = peerUrl(id);
      var node;
      if (st === "live") {
        node = el("a", "infra-svc-shell__peer", { "data-svc-role": "peer", "data-svc-peer": id, "data-svc-state": "live", href: url, title: T.live + " " + PEER_LABEL[id] + "：" + url });
        node.textContent = PEER_LABEL[id];
      } else if (st === "self") {
        node = el("span", "infra-svc-shell__peer", { "data-svc-role": "peer", "data-svc-peer": id, "data-svc-state": "self", "aria-current": "page" });
        node.textContent = PEER_LABEL[id] + "（" + T.self + "）";
      } else {
        node = el("span", "infra-svc-shell__peer", { "data-svc-role": "peer", "data-svc-peer": id, "data-svc-state": "unset", title: T.unsetTitle });
        node.textContent = PEER_LABEL[id] + "（" + T.unset + "）";
      }
      bar.appendChild(node);
    }

    bar.appendChild(el("span", "infra-svc-shell__sep", { "data-svc-role": "sep", "aria-hidden": "true" })).textContent = "·";
    bar.appendChild(el("span", "infra-svc-shell__brand", { "data-svc-role": "brand" })).textContent = "infra · " + SERVICE_NAME + " " + SERVICE_TAGLINE;

    paint(bar, L.tag);
    return true;
  }

  /* body 是三行网格（本仓两个 app 的形态）⇒ 补一行，别让既有三行整体错位 */
  function adoptGrid(body) {
    var cs;
    try { cs = window.getComputedStyle(body); } catch (e) { return "flow"; }
    if (!cs || String(cs.display).indexOf("grid") < 0) return "flow";
    var tracks = String(cs.gridTemplateRows || "").trim().split(/\s+/).filter(function (s) { return !!s; });
    if (tracks.length !== 3) {
      warn("body 是 " + tracks.length + " 行网格（预期 3 行），未改行模板，data-svc-grid=foreign 请人工看一眼");
      return "foreign";
    }
    body.style.gridTemplateRows = GRID_4;
    return "adopted";
  }

  function mount() {
    if (!document.body) return false;
    var grid = "flow";
    var bar = document.getElementById(BAR_ID);
    if (!bar) {
      grid = adoptGrid(document.body);
      bar = el("nav", "infra-svc-shell", { id: BAR_ID, "data-svc-shell": "1", "data-svc-grid": grid });
      document.body.insertBefore(bar, document.body.firstChild);
    } else {
      grid = bar.getAttribute("data-svc-grid") || "flow";
    }
    render();
    return true;
  }

  function watch() {
    if (watch.done) return;
    watch.done = true;
    try {
      new MutationObserver(function () { render(); })
        .observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    } catch (e) { warn("MutationObserver 不可用，切语言需刷新页面才更新顶栏"); }
    try {
      document.addEventListener("change", function (ev) {
        var t = ev.target;
        if (t && t.getAttribute && (t.id === "lang-select" || t.getAttribute("data-locale"))) render();
      }, true);
    } catch (e) { /* 没有下拉也不影响 */ }
    try {
      var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
      if (mq && mq.addEventListener) mq.addEventListener("change", function () { render(); });
    } catch (e) { /* 老浏览器无 addEventListener 也不影响 */ }
  }

  function state() {
    var L = resolveLang();
    var bar = document.getElementById(BAR_ID);
    var peers = [];
    for (var i = 0; i < PEERS.length; i += 1) {
      peers.push({ id: PEERS[i], state: peerState(PEERS[i]), url: peerUrl(PEERS[i]) });
    }
    return {
      version: VERSION, service: SERVICE_ID, service_name: SERVICE_NAME,
      mounted: !!bar, bars: document.querySelectorAll("#" + BAR_ID).length,
      grid: bar ? bar.getAttribute("data-svc-grid") : "unmounted",
      lang: L.tag, lang_fallback: L.fallback,
      home: homeHref(), entry: entryHref(),
      peers: peers
    };
  }

  function boot() {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", boot, { once: true });
      return;
    }
    mount();
    watch();
  }

  window[GLOBAL_KEY] = { version: VERSION, service: SERVICE_ID, mount: mount, render: render, state: state };
  boot();
})();
