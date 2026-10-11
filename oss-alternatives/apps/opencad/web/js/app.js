/*
  OpenCAD web shell (U2-web seat).
  Native browser APIs only: no framework, no bundler, no external asset, no CDN.
  Responsibilities:
    - boot from GET /api/state, fill every [data-i18n] node from strings
    - language switch via POST /api/locale (re-fills the document in place, no reload)
    - drawing standard switch via POST /api/standard (status line shows the projection angle)
    - draw L1 document entities (line / circle / arc / dimension) on #canvas with
      wheel zoom + drag pan; tool buttons post /api/command
    - status line: language, standard, projection, entity count, cursor position
    - #cmd-line accepts the minimal "line x1,y1 x2,y2" style commands
  No human-language literal is written here: all user-facing words come from the server
  language packs (strings), and a missing key falls back exactly like cadcore's own
  i18n.catalog._derive_source_string does (tail of the key, de-underscored, capitalised).
*/
"use strict";

(function () {
  const API = {
    state: "/api/state",
    catalogs: "/api/catalogs",
    locale: "/api/locale",
    standard: "/api/standard",
    command: "/api/command",
    save: "/api/save",
    open: "/api/open",
    exportDxf: "/api/export/dxf"
  };
  const NM_PER_MM = 1e6;
  const SAVE_PATH = "build/opencad.json";
  const DXF_PATH = "build/opencad.dxf";
  const DEFAULT_LAYER = "0";

  // DOM keys the shipped cdacore catalogs spell with another key name (data/i18n/*.json).
  // Resolution order is: strings[key] -> strings[KEY_ALIAS[key]] -> key-derived fallback.
  const KEY_ALIAS = {
    "tool.line": "cmd.line",
    "tool.circle": "cmd.circle",
    "tool.arc": "cmd.arc",
    "tool.dimension": "cmd.dimension",
    "tool.delete": "cmd.delete"
  };

  // web/web-i18n.json 里 d4 席产出的 web 语言包（schema opencad.web-i18n/1，262 键 × en/zh-CN/ja）。
  // 它**只在服务端 strings 没有这条键时**兜底 —— 服务端给了真译文就一律以服务端为准，这一层
  // 永不覆盖已有键（所以既有的可见文案一个字都不会变）。补的是服务端语言包
  // （cadcore/cadcore/data/i18n/*.json）里根本没有的词条：help.* / hint.*，2026-10-06
  // x2-v4 F5 与 x2-v5 F3 实测三语全缺（中文界面里蹦 "Shortcuts"/"Cancel"/"Undo empty"）。
  // 三态：WEB_STRINGS_STATE = unloaded（还没取）/ ready（已合并）/ absent（404 或解析失败，
  // 缺件不是"没加载"，两者不许同形；判定脚本读 window.__opencadV3.webI18n()）。
  let WEB_STRINGS = {};
  let WEB_FALLBACK_CHAIN = {};
  let WEB_STRINGS_STATE = "unloaded";

  const SUB = { start: "start", end: "end", center: "center" };
  const TOOLS = ["select", "line", "circle", "arc", "dimension", "delete"];

  // -1 means "not measured" and must never be displayed as 0 (measurement contract).
  const NOT_MEASURED = -1;

  const S = {
    strings: {},
    locale: "",
    standard: "",
    projection: "",
    catalogs: [],
    standards: [],
    doc: null,
    entities: NOT_MEASURED,
    cursor: null,
    tool: "select",
    drafts: [],
    selected: null
  };

  const view = { scale: 0, ox: 0, oy: 0 };
  const colors = {};

  let el = {};

  /* ------------------------------------------------------------------ i18n */

  function deriveFromKey(key) {
    const src = String(key || "");
    const cut = src.lastIndexOf(".");
    const tail = cut >= 0 ? src.slice(cut + 1) : src;
    const text = tail.replace(/_/g, " ").replace(/-/g, " ").replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function nonEmpty(value) {
    if (value === null || value === undefined) return null;
    const s = String(value);
    return s.trim() ? s : null;
  }

  function lookup(key) {
    if (!key) return null;
    if (Object.prototype.hasOwnProperty.call(S.strings, key)) {
      const direct = nonEmpty(S.strings[key]);
      if (direct) return direct;
    }
    const alias = KEY_ALIAS[key];
    if (alias && Object.prototype.hasOwnProperty.call(S.strings, alias)) {
      const viaAlias = nonEmpty(S.strings[alias]);
      if (viaAlias) return viaAlias;
    }
    // 服务端没有 ⇒ 才轮到 web 语言包：先活动语言，再 bundle 自己声明的回退语言
    // （resource.resolution_order 的第 3 步；这里把"活动语言优先"放在回退语言之前，
    // 否则 zh-CN 会拿到 en 的值，等于没修 "中文界面蹦英文"）。
    const chain = WEB_FALLBACK_CHAIN[S.locale]
      ? [S.locale, WEB_FALLBACK_CHAIN[S.locale]] : [S.locale];
    for (let i = 0; i < chain.length; i += 1) {
      const web = WEB_STRINGS[chain[i]];
      if (web && Object.prototype.hasOwnProperty.call(web, key)) {
        const viaWeb = nonEmpty(web[key]);
        if (viaWeb) return viaWeb;
      }
    }
    return null;
  }

  function interpolate(template, args) {
    if (!args) return template;
    return template.replace(/\{([a-zA-Z0-9_]+)\}/g, function (whole, name) {
      if (!Object.prototype.hasOwnProperty.call(args, name)) return whole;
      const v = args[name];
      return v === null || v === undefined ? whole : String(v);
    });
  }

  function text(key, args) {
    const tpl = lookup(key);
    if (tpl === null) {
      const derived = deriveFromKey(key);
      return derived ? { text: derived, missing: true } : null;
    }
    return { text: interpolate(tpl, args), missing: false };
  }

  function label(key, args) {
    const r = text(key, args);
    return r ? r.text : "";
  }

  function nativeName(code) {
    for (let i = 0; i < S.catalogs.length; i += 1) {
      const item = S.catalogs[i];
      if (item && item.code === code) return nonEmpty(item.native_name) || code;
    }
    return code || "";
  }

  function i18nArgs() {
    return { native_name: nativeName(S.locale), code: S.locale };
  }

  function fillNode(node, attr) {
    const key = node.getAttribute(attr);
    if (!key) return;
    const r = text(key, i18nArgs());
    const value = r ? r.text : "";
    if (attr === "data-i18n") node.textContent = value;
    else if (attr === "data-i18n-placeholder") node.setAttribute("placeholder", value);
    else if (attr === "data-i18n-title") node.setAttribute("title", value);
    if (r && r.missing) node.setAttribute("data-i18n-missing", "1");
    else node.removeAttribute("data-i18n-missing");
  }

  function fillI18n() {
    ["data-i18n", "data-i18n-placeholder", "data-i18n-title"].forEach(function (attr) {
      const nodes = document.querySelectorAll("[" + attr + "]");
      for (let i = 0; i < nodes.length; i += 1) fillNode(nodes[i], attr);
    });
    const commandInput = byId("cmd-line");
    if (commandInput) commandInput.setAttribute("aria-label", label("cmd.label"));
    document.title = label("app.title");
    document.documentElement.lang = S.locale || "en";
  }

  /* ------------------------------------------------------------- transport */

  async function api(path, init) {
    const res = await fetch(path, init);
    let body = null;
    try {
      body = await res.json();
    } catch (err) {
      throw new Error(path + " -> " + res.status + " (body is not JSON)");
    }
    if (!res.ok) throw new Error(path + " -> " + res.status);
    if (body && body.ok === false) {
      throw new Error(nonEmpty(body.error) || nonEmpty(body.detail) || path + " -> ok=false");
    }
    return body || {};
  }

  function postJSON(path, payload) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  }

  /* --------------------------------------------------------------- message */

  function setMessage(kind, value) {
    if (el.status) el.status.setAttribute("data-message-kind", kind || "");
    if (el.message) el.message.textContent = value === null || value === undefined ? "" : String(value);
  }

  function showError(err) {
    const detail = err && err.message ? err.message : String(err);
    setMessage("error", label("err.generic", { message: detail }));
  }

  /* ------------------------------------------------------------- document */

  function parseDoc(raw) {
    if (typeof raw !== "string" || !raw.trim()) {
      S.doc = null;
      return;
    }
    try {
      S.doc = JSON.parse(raw);
    } catch (err) {
      S.doc = null;
      setMessage("error", label("err.parse_failed", { source: "L1", detail: err.message }));
    }
  }

  function sketchList() {
    const doc = S.doc;
    if (!doc || !Array.isArray(doc.sketches)) return [];
    return doc.sketches;
  }

  function entityList() {
    const out = [];
    sketchList().forEach(function (sk) {
      if (sk && Array.isArray(sk.entities)) {
        sk.entities.forEach(function (e) {
          if (e && typeof e === "object") out.push(e);
        });
      }
    });
    return out;
  }

  function dimensionList() {
    const out = [];
    sketchList().forEach(function (sk) {
      if (sk && Array.isArray(sk.dimensions)) {
        sk.dimensions.forEach(function (d) {
          if (d && typeof d === "object") out.push(d);
        });
      }
    });
    return out;
  }

  function num(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function arcSweep(startDeg, endDeg) {
    const sweep = ((endDeg - startDeg) % 360 + 360) % 360;
    return sweep === 0 ? 360 : sweep;
  }

  function pointOnCircle(geom, deg) {
    const rad = (deg * Math.PI) / 180;
    return { x: geom.cx + geom.r * Math.cos(rad), y: geom.cy + geom.r * Math.sin(rad) };
  }

  function entityPoints(entity) {
    const g = (entity && entity.geom) || {};
    const kind = entity && entity.kind;
    if (kind === "line") {
      const x1 = num(g.x1), y1 = num(g.y1), x2 = num(g.x2), y2 = num(g.y2);
      if (x1 === null || y1 === null || x2 === null || y2 === null) return [];
      return [{ sub: SUB.start, x: x1, y: y1 }, { sub: SUB.end, x: x2, y: y2 }];
    }
    if (kind === "circle") {
      const cx = num(g.cx), cy = num(g.cy), r = num(g.r);
      if (cx === null || cy === null || r === null) return [];
      return [{ sub: SUB.center, x: cx, y: cy }];
    }
    if (kind === "arc") {
      const cx = num(g.cx), cy = num(g.cy), r = num(g.r);
      const a0 = num(g.start_deg), a1 = num(g.end_deg);
      if (cx === null || cy === null || r === null || a0 === null || a1 === null) return [];
      const geom = { cx: cx, cy: cy, r: r };
      return [
        { sub: SUB.center, x: cx, y: cy },
        { sub: SUB.start, x: pointOnCircle(geom, a0).x, y: pointOnCircle(geom, a0).y },
        { sub: SUB.end, x: pointOnCircle(geom, a1).x, y: pointOnCircle(geom, a1).y }
      ];
    }
    return [];
  }

  function entitySegments(entity) {
    const g = (entity && entity.geom) || {};
    const kind = entity && entity.kind;
    if (kind === "line") {
      const p = entityPoints(entity);
      return p.length === 2 ? [[p[0], p[1]]] : [];
    }
    if (kind === "circle" || kind === "arc") {
      const cx = num(g.cx), cy = num(g.cy), r = num(g.r);
      if (cx === null || cy === null || r === null || r <= 0) return [];
      const full = kind === "circle";
      const a0 = full ? 0 : num(g.start_deg);
      const a1 = full ? 360 : num(g.end_deg);
      if (a0 === null || a1 === null) return [];
      const sweep = full ? 360 : arcSweep(a0, a1);
      const steps = Math.max(8, Math.min(180, Math.round(sweep / 4)));
      const segs = [];
      let prev = pointOnCircle(g, a0);
      for (let i = 1; i <= steps; i += 1) {
        const cur = pointOnCircle(g, a0 + (sweep * i) / steps);
        segs.push([prev, cur]);
        prev = cur;
      }
      return segs;
    }
    return [];
  }

  function bbox() {
    let minX = null, minY = null, maxX = null, maxY = null;
    entityList().forEach(function (e) {
      entityPoints(e).forEach(function (p) {
        if (minX === null || p.x < minX) minX = p.x;
        if (maxX === null || p.x > maxX) maxX = p.x;
        if (minY === null || p.y < minY) minY = p.y;
        if (maxY === null || p.y > maxY) maxY = p.y;
      });
      entitySegments(e).forEach(function (s) {
        s.forEach(function (p) {
          if (minX === null || p.x < minX) minX = p.x;
          if (maxX === null || p.x > maxX) maxX = p.x;
          if (minY === null || p.y < minY) minY = p.y;
          if (maxY === null || p.y > maxY) maxY = p.y;
        });
      });
    });
    if (minX === null) return null;
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  function findEntity(uuid) {
    const list = entityList();
    for (let i = 0; i < list.length; i += 1) {
      if (list[i].uuid === uuid) return list[i];
    }
    return null;
  }

  function resolveRef(ref) {
    if (!Array.isArray(ref) || ref.length < 2) return null;
    const entity = findEntity(ref[0]);
    if (!entity) return null;
    const points = entityPoints(entity);
    for (let i = 0; i < points.length; i += 1) {
      if (points[i].sub === ref[1]) return points[i];
    }
    return null;
  }

  /* --------------------------------------------------------------- geometry */

  function dist(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function pointSegmentDistance(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) return dist(p, a);
    let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }

  function entityDistance(entity, p) {
    let best = null;
    entitySegments(entity).forEach(function (s) {
      const d = pointSegmentDistance(p, s[0], s[1]);
      if (best === null || d < best) best = d;
    });
    return best;
  }

  function hitEntity(p) {
    const tol = 8 / view.scale;
    let best = null, bestD = null;
    entityList().forEach(function (e) {
      const d = entityDistance(e, p);
      if (d === null || d > tol) return;
      if (bestD === null || d < bestD) { bestD = d; best = e; }
    });
    return best;
  }

  function nearestRef(p) {
    const tol = 14 / view.scale;
    let best = null, bestD = null;
    entityList().forEach(function (e) {
      entityPoints(e).forEach(function (q) {
        const d = dist(p, q);
        if (d > tol) return;
        if (bestD === null || d < bestD) {
          bestD = d;
          best = [e.uuid, q.sub];
        }
      });
    });
    return best;
  }

  function circumcircle(a, b, c) {
    const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
    if (!isFinite(d) || Math.abs(d) < 1e-6) return null;
    const a2 = a.x * a.x + a.y * a.y;
    const b2 = b.x * b.x + b.y * b.y;
    const c2 = c.x * c.x + c.y * c.y;
    const ux = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d;
    const uy = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d;
    const center = { x: ux, y: uy };
    return { center: center, radius: dist(center, a) };
  }

  function angleDeg(center, p) {
    return ((Math.atan2(p.y - center.y, p.x - center.x) * 180) / Math.PI + 360) % 360;
  }

  /* ------------------------------------------------------------- rendering */

  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    function pick(name, fallback) {
      const v = cs.getPropertyValue(name).trim();
      return v || fallback;
    }
    colors.canvas = pick("--fc-bg-canvas", "#FFFFFF");
    colors.grid = pick("--fc-line-subtle", "#D1D1D1");
    colors.axis = pick("--fc-line-strong", "#868686");
    colors.geometry = pick("--fc-text-primary", "#303030");
    colors.selected = pick("--fc-brand-solid", "#5F5EF0");
    colors.dimension = pick("--fc-warning", "#9A6815");
    colors.preview = pick("--fc-text-tertiary", "#6D6D6D");
  }

  function resize() {
    if (!el.canvas || !el.pane) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, el.pane.clientWidth);
    const h = Math.max(1, el.pane.clientHeight);
    el.canvas.width = Math.round(w * dpr);
    el.canvas.height = Math.round(h * dpr);
    el.canvas.style.width = w + "px";
    el.canvas.style.height = h + "px";
    if (!view.scale) fitView();
    render();
  }

  function fitView() {
    const w = el.canvas ? el.canvas.clientWidth : 800;
    const h = el.canvas ? el.canvas.clientHeight : 600;
    const box = bbox();
    let minX, minY, maxX, maxY;
    if (!box) {
      const half = 50 * NM_PER_MM;
      minX = -half; minY = -half; maxX = half; maxY = half;
    } else {
      const padX = Math.max((box.maxX - box.minX) * 0.12, 5 * NM_PER_MM);
      const padY = Math.max((box.maxY - box.minY) * 0.12, 5 * NM_PER_MM);
      minX = box.minX - padX; maxX = box.maxX + padX;
      minY = box.minY - padY; maxY = box.maxY + padY;
    }
    const sx = w / Math.max(1, maxX - minX);
    const sy = h / Math.max(1, maxY - minY);
    view.scale = Math.min(sx, sy);
    view.ox = w / 2 - ((minX + maxX) / 2) * view.scale;
    view.oy = h / 2 + ((minY + maxY) / 2) * view.scale;
  }

  function toScreen(p) {
    return { x: view.ox + p.x * view.scale, y: view.oy - p.y * view.scale };
  }

  function toWorld(sx, sy) {
    return { x: (sx - view.ox) / view.scale, y: (view.oy - sy) / view.scale };
  }

  function drawGrid(ctx, w, h) {
    const targets = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];
    let stepMm = targets[targets.length - 1];
    for (let i = 0; i < targets.length; i += 1) {
      if (targets[i] * NM_PER_MM * view.scale >= 24) { stepMm = targets[i]; break; }
    }
    const step = stepMm * NM_PER_MM;
    const left = toWorld(0, 0).x, right = toWorld(w, 0).x;
    const bottom = toWorld(0, h).y, top = toWorld(0, 0).y;
    ctx.save();
    ctx.strokeStyle = colors.grid;
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(left / step) * step; x <= right; x += step) {
      const s = toScreen({ x: x, y: 0 });
      ctx.moveTo(Math.round(s.x) + 0.5, 0);
      ctx.lineTo(Math.round(s.x) + 0.5, h);
    }
    for (let y = Math.ceil(bottom / step) * step; y <= top; y += step) {
      const s = toScreen({ x: 0, y: y });
      ctx.moveTo(0, Math.round(s.y) + 0.5);
      ctx.lineTo(w, Math.round(s.y) + 0.5);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = colors.axis;
    ctx.beginPath();
    const origin = toScreen({ x: 0, y: 0 });
    ctx.moveTo(0, Math.round(origin.y) + 0.5);
    ctx.lineTo(w, Math.round(origin.y) + 0.5);
    ctx.moveTo(Math.round(origin.x) + 0.5, 0);
    ctx.lineTo(Math.round(origin.x) + 0.5, h);
    ctx.stroke();
    ctx.restore();
  }

  function drawEntity(ctx, entity) {
    const kind = entity.kind;
    const selected = S.selected && S.selected.uuid === entity.uuid;
    ctx.save();
    ctx.strokeStyle = selected ? colors.selected : colors.geometry;
    ctx.lineWidth = selected ? 2.5 : 1.5;
    if (entity.construction) {
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = selected ? colors.selected : colors.preview;
    }
    if (kind === "circle") {
      const c = toScreen({ x: num(entity.geom.cx), y: num(entity.geom.cy) });
      ctx.beginPath();
      ctx.arc(c.x, c.y, num(entity.geom.r) * view.scale, 0, Math.PI * 2, false);
      ctx.stroke();
    } else if (kind === "arc") {
      const g = entity.geom;
      const c = toScreen({ x: num(g.cx), y: num(g.cy) });
      const a0 = num(g.start_deg), a1 = num(g.end_deg);
      const sweep = arcSweep(a0, a1);
      ctx.beginPath();
      ctx.arc(c.x, c.y, num(g.r) * view.scale, (-a0 * Math.PI) / 180, (-(a0 + sweep) * Math.PI) / 180, true);
      ctx.stroke();
    } else {
      entitySegments(entity).forEach(function (s) {
        const a = toScreen(s[0]), b = toScreen(s[1]);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      });
    }
    ctx.restore();
    entityPoints(entity).forEach(function (p) {
      const s = toScreen(p);
      ctx.save();
      ctx.fillStyle = selected ? colors.selected : colors.geometry;
      ctx.fillRect(s.x - 1.5, s.y - 1.5, 3, 3);
      ctx.restore();
    });
  }

  function dimensionOffset() {
    return 8 * NM_PER_MM;
  }

  function drawDimension(ctx, dim) {
    const refs = Array.isArray(dim.refs) ? dim.refs : [];
    const resolved = refs.map(resolveRef);
    if (resolved.length < 2 || !resolved[0] || !resolved[1]) return;
    const a = resolved[0], b = resolved[1];
    const length = dist(a, b);
    const dx = b.x - a.x, dy = b.y - a.y;
    const scaleLen = length === 0 ? 1 : length;
    const nx = -dy / scaleLen, ny = dx / scaleLen;
    const off = dimensionOffset();
    const a2 = { x: a.x + nx * off, y: a.y + ny * off };
    const b2 = { x: b.x + nx * off, y: b.y + ny * off };
    const sa = toScreen(a), sb = toScreen(b), sa2 = toScreen(a2), sb2 = toScreen(b2);
    ctx.save();
    ctx.strokeStyle = colors.dimension;
    ctx.fillStyle = colors.dimension;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sa.x, sa.y); ctx.lineTo(sa2.x, sa2.y);
    ctx.moveTo(sb.x, sb.y); ctx.lineTo(sb2.x, sb2.y);
    ctx.moveTo(sa2.x, sa2.y); ctx.lineTo(sb2.x, sb2.y);
    ctx.stroke();
    const ang = Math.atan2(sb2.y - sa2.y, sb2.x - sa2.x);
    const arrow = 7;
    [[sa2, ang], [sb2, ang + Math.PI]].forEach(function (pair) {
      const tip = pair[0], dir = pair[1];
      ctx.beginPath();
      ctx.moveTo(tip.x, tip.y);
      ctx.lineTo(tip.x + arrow * Math.cos(dir + 0.4), tip.y + arrow * Math.sin(dir + 0.4));
      ctx.lineTo(tip.x + arrow * Math.cos(dir - 0.4), tip.y + arrow * Math.sin(dir - 0.4));
      ctx.closePath();
      ctx.fill();
    });
    const value = num(dim.value_nm) === null ? length : num(dim.value_nm);
    const unit = unitSymbol();
    const caption = (value / NM_PER_MM).toFixed(3) + (unit ? " " + unit : "");
    const mid = { x: (sa2.x + sb2.x) / 2, y: (sa2.y + sb2.y) / 2 };
    ctx.font = "12px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(caption, mid.x, mid.y - 3);
    ctx.restore();
  }

  function drawPreview(ctx) {
    if (!S.drafts.length || !S.cursor) return;
    ctx.save();
    ctx.strokeStyle = colors.preview;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    const first = toScreen(S.drafts[0]);
    ctx.moveTo(first.x, first.y);
    S.drafts.slice(1).forEach(function (p) {
      const s = toScreen(p);
      ctx.lineTo(s.x, s.y);
    });
    const cur = toScreen(S.cursor);
    ctx.lineTo(cur.x, cur.y);
    ctx.stroke();
    ctx.restore();
    S.drafts.forEach(function (p) {
      const s = toScreen(p);
      ctx.save();
      ctx.strokeStyle = colors.selected;
      ctx.strokeRect(s.x - 3, s.y - 3, 6, 6);
      ctx.restore();
    });
  }

  function render() {
    if (!el.canvas) return;
    const ctx = el.canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = el.canvas.clientWidth, h = el.canvas.clientHeight;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = colors.canvas;
    ctx.fillRect(0, 0, w, h);
    if (view.scale > 0) {
      drawGrid(ctx, w, h);
      entityList().forEach(function (e) { drawEntity(ctx, e); });
      dimensionList().forEach(function (d) { drawDimension(ctx, d); });
      drawPreview(ctx);
    }
  }

  /* ----------------------------------------------------------- panel/status */

  function renderLayers() {
    if (!el.layers) return;
    el.layers.textContent = "";
    const counts = new Map();
    entityList().forEach(function (e) {
      const name = nonEmpty(e.layer) || DEFAULT_LAYER;
      counts.set(name, (counts.get(name) || 0) + 1);
    });
    const names = Array.from(counts.keys()).sort();
    names.forEach(function (name) {
      const li = document.createElement("li");
      const nameNode = document.createElement("span");
      nameNode.className = "layer__name";
      nameNode.textContent = name;
      const countNode = document.createElement("span");
      countNode.className = "layer__count";
      countNode.textContent = String(counts.get(name));
      li.appendChild(nameNode);
      li.appendChild(countNode);
      el.layers.appendChild(li);
    });
  }

  function unitSymbol() {
    const doc = S.doc || {};
    const unit = nonEmpty(doc.unit);
    if (!unit) return "";
    return label("unit." + unit + ".symbol");
  }

  function standardLabel(code) {
    if (!code) return label("status.unmeasured");
    return label("std." + code) || code;
  }

  function projectionLabel(projection) {
    if (projection === undefined || projection === null || projection === "") {
      return label("status.unmeasured");
    }
    const raw = String(projection);
    if (raw === "NA" || raw.toLowerCase() === "na") return label("status.na");
    const key = "std.projection." + raw;
    return lookup(key) ? label(key) : raw;
  }

  function entitiesLabel(count) {
    if (typeof count !== "number" || count < 0) return label("status.unmeasured");
    if (count === 0) return label("status.zero");
    return String(count);
  }

  function cursorLabel(cursor) {
    if (!cursor) return label("status.unmeasured");
    const unit = unitSymbol();
    const x = (cursor.x / NM_PER_MM).toFixed(3);
    const y = (cursor.y / NM_PER_MM).toFixed(3);
    return x + ", " + y + (unit ? " " + unit : "");
  }

  function setText(node, value) {
    if (node) node.textContent = value;
  }

  function renderStatus() {
    setText(el.statusLanguage, nativeName(S.locale) || label("status.unmeasured"));
    setText(el.statusStandard, standardLabel(S.standard));
    setText(el.statusProjection, projectionLabel(S.projection));
    setText(el.statusEntities, entitiesLabel(S.entities));
    setText(el.statusCursor, cursorLabel(S.cursor));
    setText(el.profileStandard, standardLabel(S.standard));
    setText(el.profileProjection, projectionLabel(S.projection));
  }

  function renderSelectors() {
    if (el.lang) {
      const codes = S.catalogs.map(function (c) { return c.code; });
      el.lang.textContent = "";
      codes.forEach(function (code) {
        const option = document.createElement("option");
        option.value = code;
        // strings 还没到（或该键缺）时用语言包自己的 native_name（English/日本語/简体中文），
        // 不要露出 deriveFromKey 的占位词 "Entry"。
        const entry = text("lang.entry", { native_name: nativeName(code), code: code });
        option.textContent = entry && !entry.missing ? entry.text : (nativeName(code) || code);
        el.lang.appendChild(option);
      });
      if (S.locale) el.lang.value = S.locale;
    }
    if (el.standard) {
      el.standard.textContent = "";
      S.standards.forEach(function (code) {
        const option = document.createElement("option");
        option.value = code;
        option.textContent = standardLabel(code);
        el.standard.appendChild(option);
      });
      if (S.standard) el.standard.value = S.standard;
    }
  }

  function renderAll() {
    fillI18n();
    renderSelectors();
    renderLayers();
    renderStatus();
    render();
  }

  /* -------------------------------------------------------------- commands */

  function pointArg(p) {
    return [Math.round(p.x), Math.round(p.y)];
  }

  async function runCommand(command, args) {
    try {
      const body = await postJSON(API.command, { command: command, args: args });
      if (typeof body.entities === "number") S.entities = body.entities;
      S.drafts = [];
      setMessage("ok", nonEmpty(body.uuid) || "");
      await refreshState();
    } catch (err) {
      showError(err);
      render();
    }
  }

  async function refreshState() {
    const body = await api(API.state);
    applyState(body);
    renderAll();
    return body;
  }
  window.OpenCADWorkspace = Object.freeze({refresh: refreshState});

  function applyState(body) {
    S.strings = body.strings && typeof body.strings === "object" ? body.strings : {};
    S.locale = nonEmpty(body.locale) || S.locale;
    S.standard = nonEmpty(body.standard) || "";
    S.projection = body.projection === undefined ? "" : body.projection;
    S.standards = Array.isArray(body.standards) ? body.standards : [];
    S.entities = typeof body.entities === "number" ? body.entities : NOT_MEASURED;
    parseDoc(body.doc);
    const codes = Array.isArray(body.catalogs) ? body.catalogs : [];
    if (codes.length) {
      const known = new Map(S.catalogs.map(function (c) { return [c.code, c]; }));
      S.catalogs = codes.map(function (code) {
        return known.get(code) || { code: code, native_name: code, keys: null };
      });
    }
  }

  async function loadCatalogs() {
    try {
      const body = await api(API.catalogs);
      if (Array.isArray(body.catalogs)) S.catalogs = body.catalogs;
      // 语言下拉在第一趟往返（/api/catalogs）就填好，不等第二趟 /api/state（它要带整包
      // strings，8~11 KB）。2026-10-01 实锚：Windows VM 的就绪门在 Edge 86 上静默失效，
      // 页面 bootstrap 还没跑完就取样，量到 `#lang-select options=0` 的假红；把下拉的可见
      // 时刻提前到第一趟往返，任何"早取样"的尺子都不会再把它读成"页面起不来"。
      // 语义不变：/api/state 回来后 renderAll() 仍会按最终 strings 重填一次。
      renderSelectors();
    } catch (err) {
      showError(err);
    }
  }

  // `resolved` = 取点管线（对象捕捉/栅格步进/正交/极轴/追踪）算好的点，见文末"用户面接线 v2"；
  // 不传时按本段自己的局部管线解析一次。两条路都不改下面的绘图/命令流程。
  function clickWorld(world, resolved) {
    const p = resolved ? { x: resolved.x, y: resolved.y } : resolvePick(world);
    if (!view.scale) return;
    if (S.tool === "select") {
      S.selected = hitEntity(p);
      render();
      return;
    }
    if (S.tool === "delete") {
      const hit = hitEntity(p);
      if (!hit) {
        setMessage("error", label("err.unknown_reference", { reference: pointArg(p).join(",") }));
        return;
      }
      runCommand("delete", { uuid: hit.uuid });
      return;
    }
    S.drafts.push(p);
    if (S.tool === "line" && S.drafts.length === 2) {
      runCommand("add_line", { p1: pointArg(S.drafts[0]), p2: pointArg(S.drafts[1]), layer: DEFAULT_LAYER });
      return;
    }
    if (S.tool === "circle" && S.drafts.length === 2) {
      const radius = Math.round(dist(S.drafts[0], S.drafts[1]));
      if (radius <= 0) {
        setMessage("error", label("err.invalid_length", { text: "0" }));
        S.drafts = [];
        render();
        return;
      }
      runCommand("add_circle", { center: pointArg(S.drafts[0]), radius_nm: radius, layer: DEFAULT_LAYER });
      return;
    }
    if (S.tool === "arc" && S.drafts.length === 3) {
      const circle = circumcircle(S.drafts[0], S.drafts[1], S.drafts[2]);
      if (!circle) {
        setMessage("error", label("err.geometry_invalid", { detail: "determinant=0" }));
        S.drafts = [];
        render();
        return;
      }
      const a0 = angleDeg(circle.center, S.drafts[0]);
      const a1 = angleDeg(circle.center, S.drafts[2]);
      const mid = angleDeg(circle.center, S.drafts[1]);
      const ccw = (a1 - a0 + 360) % 360;
      const midCcw = (mid - a0 + 360) % 360;
      const startDeg = midCcw <= ccw ? a0 : a1;
      const endDeg = midCcw <= ccw ? a1 : a0;
      runCommand("add_arc", {
        center: pointArg(circle.center),
        radius_nm: Math.round(circle.radius),
        start_deg: startDeg,
        end_deg: endDeg,
        layer: DEFAULT_LAYER
      });
      return;
    }
    if (S.tool === "dimension" && S.drafts.length === 2) {
      const r1 = nearestRef(S.drafts[0]);
      const r2 = nearestRef(S.drafts[1]);
      if (!r1 || !r2) {
        setMessage("error", label("err.unknown_reference", { reference: pointArg(S.drafts[1]).join(",") }));
        S.drafts = [];
        render();
        return;
      }
      runCommand("add_dimension", {
        kind: "linear",
        refs: [r1, r2],
        value_nm: Math.round(dist(S.drafts[0], S.drafts[1]))
      });
      return;
    }
    render();
  }

  // 精确坐标输入（AutoCAD 口径）：绝对 `x,y`（毫米）、相对 `@dx,dy`、极坐标 `@距离<角度`。
  // `base` = 同一命令里的上一个点（`line @0,0 @10,0` 的第二点相对第一点）；没给就用 S.lastPoint。
  function parsePoint(token, base) {
    const raw = String(token === undefined || token === null ? "" : token).trim();
    if (!raw) return null;
    if (raw.charAt(0) === "@") {
      const rest = raw.slice(1);
      const origin = base || S.lastPoint || { x: 0, y: 0 };
      const polar = rest.split("<");
      if (polar.length === 2) {
        const lengthMm = Number(polar[0]), angle = Number(polar[1]);
        if (!isFinite(lengthMm) || !isFinite(angle)) return null;
        const rad = (angle * Math.PI) / 180;
        return { x: Math.round(origin.x + lengthMm * NM_PER_MM * Math.cos(rad)),
                 y: Math.round(origin.y + lengthMm * NM_PER_MM * Math.sin(rad)) };
      }
      const rel = rest.split(",");
      if (rel.length !== 2) return null;
      const dx = Number(rel[0]), dy = Number(rel[1]);
      if (!isFinite(dx) || !isFinite(dy)) return null;
      return { x: Math.round(origin.x + dx * NM_PER_MM),
               y: Math.round(origin.y + dy * NM_PER_MM) };
    }
    const parts = raw.split(",");
    if (parts.length !== 2) return null;
    const x = Number(parts[0]), y = Number(parts[1]);
    if (!isFinite(x) || !isFinite(y)) return null;
    return { x: Math.round(x * NM_PER_MM), y: Math.round(y * NM_PER_MM) };
  }

  function runCmdLine(raw) {
    const parts = String(raw || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return;
    const verb = parts[0].toLowerCase();
    const argv = parts.slice(1);
    const bad = function (token) {
      setMessage("error", label("err.invalid_length", { text: String(token) }));
    };
    if (verb === "line") {
      const a = parsePoint(argv[0]), b = parsePoint(argv[1], a);
      if (!a || !b) return bad(argv[0] === undefined ? verb : argv[0]);
      S.lastPoint = b;
      runCommand("add_line", { p1: pointArg(a), p2: pointArg(b), layer: DEFAULT_LAYER });
      return;
    }
    if (verb === "circle") {
      const c = parsePoint(argv[0]);
      const r = Number(argv[1]);
      if (!c || !isFinite(r)) return bad(argv[0] === undefined ? verb : argv[0]);
      runCommand("add_circle", { center: pointArg(c), radius_nm: Math.round(r * NM_PER_MM), layer: DEFAULT_LAYER });
      return;
    }
    if (verb === "arc") {
      const c = parsePoint(argv[0]);
      const r = Number(argv[1]), a0 = Number(argv[2]), a1 = Number(argv[3]);
      if (!c || !isFinite(r) || !isFinite(a0) || !isFinite(a1)) return bad(argv[0] === undefined ? verb : argv[0]);
      runCommand("add_arc", {
        center: pointArg(c),
        radius_nm: Math.round(r * NM_PER_MM),
        start_deg: a0,
        end_deg: a1,
        layer: DEFAULT_LAYER
      });
      return;
    }
    if (verb === "zoom" || verb === "fit") {
      fitView();
      render();
      return;
    }
    // 通用直通：`<verb> {json}` —— 服务端只接线，校验/几何/边界全在 cadcore（未知 verb 由
    // 服务端如实回 unknown_command，不在前端假装"没实现"）。图层面（layer_off / layer_freeze /
    // layer_state_set / layer_assign / current_layer / layer_state）与出图面（plot_area / plot_scale /
    // plot_flag / lineweight / model_space_plot / manage_layout / publish / preview /
    // set_viewport_layer_override）都从这里进。
    const rest = String(raw || "").trim().slice(verb.length).trim();
    if (!rest) {
      setMessage("error", label("err.missing_argument", { argument: "args" }));
      return;
    }
    let payload = null;
    try {
      payload = JSON.parse(rest);
    } catch (err) {
      setMessage("error", label("err.geometry_invalid", { detail: "args-json" }));
      return;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      setMessage("error", label("err.geometry_invalid", { detail: "args-object" }));
      return;
    }
    runCommand(verb, payload);
  }

  /* ------------------------------------------------------------- lifecycle */

  async function onLocaleChange(code) {
    try {
      const body = await postJSON(API.locale, { locale: code });
      S.locale = nonEmpty(body.locale) || code;
      if (body.strings && typeof body.strings === "object") S.strings = body.strings;
      await loadCatalogs();
      renderAll();
      if (typeof body.changed === "number") {
        setMessage("ok", label("lang.switched", { native_name: nativeName(S.locale) }));
      }
    } catch (err) {
      showError(err);
    }
  }

  async function onStandardChange(code) {
    try {
      const body = await postJSON(API.standard, { standard: code });
      S.standard = nonEmpty(body.standard) || code;
      S.projection = body.projection === undefined ? "NA" : body.projection;
      renderAll();
    } catch (err) {
      showError(err);
    }
  }

  async function onAction(action) {
    try {
      if (action === "zoom-fit") {
        fitView();
        render();
        return;
      }
      if (action === "save") {
        const body = await postJSON(API.save, { path: SAVE_PATH });
        setMessage("ok", [nonEmpty(body.path) || SAVE_PATH, String(body.bytes)].join(" "));
        return;
      }
      if (action === "export-dxf") {
        const body = await postJSON(API.exportDxf, { path: DXF_PATH });
        setMessage("ok", [nonEmpty(body.path) || DXF_PATH, String(body.bytes), String(body.dimension_entities)].join(" "));
        return;
      }
      if (action === "export-pdf") {
        const pdfPath = "build/opencad.pdf";
        const body = await postJSON(API.command, {
          command: "export_pdf", args: { out: pdfPath } });
        setMessage("ok", [nonEmpty(body.path) || pdfPath, String(body.bytes),
          nonEmpty(body.magic)].join(" "));
        return;
      }
      if (action === "open") {
        const body = await api(API.open + "?path=" + encodeURIComponent(SAVE_PATH));
        parseDoc(body.doc);
        S.entities = typeof body.entities === "number" ? body.entities : NOT_MEASURED;
        S.drafts = [];
        renderAll();
        setMessage("ok", nonEmpty(body.path) || SAVE_PATH);
      }
    } catch (err) {
      showError(err);
    }
  }

  function setTool(name) {
    if (TOOLS.indexOf(name) < 0) return;
    S.tool = name;
    S.drafts = [];
    if (el.canvas) el.canvas.setAttribute("data-tool", name);
    const buttons = document.querySelectorAll("#toolbar .tool");
    for (let i = 0; i < buttons.length; i += 1) {
      const on = buttons[i].getAttribute("data-tool") === name;
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
    render();
  }

  function bindCanvas() {
    const canvas = el.canvas;
    if (!canvas) return;
    let panning = false, moved = 0, last = null;

    canvas.addEventListener("pointerdown", function (ev) {
      canvas.setPointerCapture(ev.pointerId);
      // 左键在"选择"工具下是**窗选/交叉窗选**（见文末 v2 段的橡皮筋），平移改用中键/右键；
      // 其它工具左键照旧取点。这是 v2 的唯一一处行为改动，理由是 AutoCAD 口径的窗选必须有左键拖拽。
      const wantPan = ev.button === 1 || ev.button === 2;
      panning = wantPan;
      moved = 0;
      last = { x: ev.offsetX, y: ev.offsetY };
    });

    canvas.addEventListener("pointermove", function (ev) {
      const world = toWorld(ev.offsetX, ev.offsetY);
      S.cursor = world;
      if (panning && last) {
        moved += Math.abs(ev.offsetX - last.x) + Math.abs(ev.offsetY - last.y);
        view.ox += ev.offsetX - last.x;
        view.oy += ev.offsetY - last.y;
        last = { x: ev.offsetX, y: ev.offsetY };
      } else {
        last = { x: ev.offsetX, y: ev.offsetY };
      }
      renderStatus();
      render();
    });

    canvas.addEventListener("pointerup", function (ev) {
      const wasPanning = panning;
      panning = false;
      if (wasPanning && moved > 3) return;
      if (ev.button === 2) return;
      S.cursor = toWorld(ev.offsetX, ev.offsetY);
      clickWorld(S.cursor);
    });

    canvas.addEventListener("pointerleave", function () {
      S.cursor = null;
      renderStatus();
    });

    canvas.addEventListener("contextmenu", function (ev) { ev.preventDefault(); });

    canvas.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      const before = toWorld(ev.offsetX, ev.offsetY);
      const factor = Math.pow(1.0015, -ev.deltaY);
      view.scale = Math.min(0.2, Math.max(1e-9, view.scale * factor));
      const after = toWorld(ev.offsetX, ev.offsetY);
      view.ox += (after.x - before.x) * view.scale;
      view.oy -= (after.y - before.y) * view.scale;
      S.cursor = toWorld(ev.offsetX, ev.offsetY);
      renderStatus();
      render();
    }, { passive: false });
  }

  // x2-v5 F2：文本录入控件里的按键一个字都不许拦。v1/v2 两个 keydown 处理器共用这一个谓词，
  // 否则在 #osnap-tolerance 这类数值框里按 Delete 会真的删掉选中的几何（实测数据丢失）。
  function isTextEntryTarget(node) {
    if (!node || !node.tagName) return false;
    const tag = String(node.tagName).toUpperCase();
    if (tag === "TEXTAREA" || tag === "SELECT") return true;
    if (tag === "INPUT") {
      const t = String(node.type || "text").toLowerCase();
      return ["text", "search", "url", "tel", "email", "password", "number"].indexOf(t) >= 0;
    }
    return node.isContentEditable === true;
  }

  function bind() {
    if (el.lang) {
      el.lang.addEventListener("change", function (ev) { onLocaleChange(ev.target.value); });
    }
    if (el.standard) {
      el.standard.addEventListener("change", function (ev) { onStandardChange(ev.target.value); });
    }
    if (el.toolbar) {
      el.toolbar.addEventListener("click", function (ev) {
        const button = ev.target.closest ? ev.target.closest(".tool") : null;
        if (button) setTool(button.getAttribute("data-tool"));
      });
    }
    const actions = document.getElementById("actions");
    if (actions) {
      actions.addEventListener("click", function (ev) {
        const button = ev.target.closest ? ev.target.closest("[data-action]") : null;
        if (button) onAction(button.getAttribute("data-action"));
      });
    }
    if (el.cmd) {
      el.cmd.addEventListener("keydown", function (ev) {
        if (ev.key !== "Enter") return;
        runCmdLine(el.cmd.value);
        el.cmd.value = "";
      });
    }
    // x2-v5 F2：这里原来还有一条 Delete 分支（与 v2 段重复接线）⇒ 一次按键发出两条删除命令
    // （本条的 uuid 版 + v2 段的 refs 版）。删除统一交给 v2 那一条，本段只留 Esc。
    document.addEventListener("keydown", function (ev) {
      if (ev.target === el.cmd || isTextEntryTarget(ev.target)) return;
      if (ev.key === "Escape") {
        S.drafts = [];
        S.selected = null;
        render();
      }
    });
    window.addEventListener("resize", resize);
    if (window.ResizeObserver && el.pane) {
      new ResizeObserver(function () { resize(); }).observe(el.pane);
    }
    const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    if (media && media.addEventListener) {
      media.addEventListener("change", function () {
        readColors();
        render();
      });
    }
  }

  /* ------------------------------------------- 新增能力接线（additive only） */

  // 本段是**加法**：扩工具集、补状态行段位、画捕捉指示、给正交/极轴开关接线，并让
  // #cmd-line 认得较新的 /api/command 动词。上面的绘图/命令流程一行未改，几何规则全在
  // cadcore；这里只做词法、单位换算与预览。面向用户的词一律来自服务端语言包。

  const WIRE_DRAW = ["rectangle", "polygon", "slot"];
  const WIRE_XFORM = ["move", "copy", "rotate", "scale", "mirror"];
  const WIRE_TOOLS = WIRE_DRAW.concat(WIRE_XFORM,
    ["offset", "trim", "extend", "fillet", "chamfer", "array_linear",
     "delete_constraint", "snap_query"]);
  WIRE_TOOLS.forEach(function (name) { if (TOOLS.indexOf(name) < 0) TOOLS.push(name); });

  //: #cmd-line 直通 /api/command 的动词（这里只做词法/单位换算，几何规则在 cadcore）。
  const WIRE_VERBS = ["add_rectangle", "add_polyline", "add_polygon", "add_slot", "move", "copy",
    "rotate", "scale", "mirror", "offset", "trim", "extend", "fillet", "chamfer",
    "array_linear", "delete_constraint", "snap_query"];

  const MODE_BUTTONS = ["ortho", "polar"];
  const POLAR_STEP_DEG = 15;      // 极轴增量（度）
  const MODE_TOLERANCE_DEG = 3;   // 与 cadcore.precision.osnap 的缺省容差同口径
  const SNAP_TOL_PX = 12;         // 捕捉命中半径（屏幕像素）→ 按当前比例换算成纳米再问服务端
  const POLYGON_SIDES = 6;        // 正多边形缺省边数（工具栏点击流没有边数输入）

  S.modes = { ortho: false, polar: false };
  S.snap = null;

  function byId(id) { return document.getElementById(id); }

  function modeStateLabel(name) {
    return label(S.modes[name] ? "status.toggle.on" : "status.toggle.off",
                 { name: label("cmd." + name) });
  }

  function toolStatusLabel() {
    const key = "tool." + S.tool;
    if (lookup(key)) return label(key);
    const alias = KEY_ALIAS[key];
    if (alias && lookup(alias)) return label(alias);
    return label("cmd." + S.tool);
  }

  function snapKindLabel(kind) {
    const key = "status.snap." + String(kind || "");
    return lookup(key) ? label(key) : String(kind || "");
  }

  function renderWiringSegments() {
    setText(byId("status-tool"), toolStatusLabel());
    setText(byId("status-snap"),
            S.snap ? label("status.snap.indicator", { kind: snapKindLabel(S.snap.kind) })
                   : label("status.snap.none"));
    setText(byId("status-ortho"), modeStateLabel("ortho"));
    setText(byId("status-polar"), modeStateLabel("polar"));
  }

  function paintModeButtons() {
    MODE_BUTTONS.forEach(function (name) {
      const node = document.querySelector('#toolbar [data-tool="' + name + '"]');
      if (node) node.setAttribute("aria-pressed", S.modes[name] ? "true" : "false");
    });
  }

  function paintSnapIndicator() {
    const node = byId("snap-indicator");
    if (!node) return;
    const point = S.snap && Array.isArray(S.snap.point_nm) ? S.snap.point_nm : null;
    if (!point || point.length < 2) { node.hidden = true; return; }
    node.hidden = false;
    node.setAttribute("data-snap-kind", String(S.snap.kind || ""));
    node.textContent = snapKindLabel(S.snap.kind);
    const on = toScreen({ x: point[0], y: point[1] });
    node.style.left = on.x + "px";
    node.style.top = on.y + "px";
  }

  let snapToken = 0;
  let snapBusy = false;

  async function querySnap(world) {
    if (!world || !view.scale || S.tool === "select") {
      S.snap = null;
      paintSnapIndicator();
      return;
    }
    if (snapBusy) return;
    snapBusy = true;
    const token = ++snapToken;
    const tolerance = Math.max(1, Math.round(SNAP_TOL_PX / view.scale));
    try {
      const body = await postJSON(API.command, {
        command: "snap_query",
        args: { point_nm: [Math.round(world.x), Math.round(world.y)], tolerance_nm: tolerance }
      });
      if (token === snapToken) S.snap = body && body.snap ? body.snap : null;
    } catch (err) {
      if (token === snapToken) S.snap = null;
    } finally {
      snapBusy = false;
      if (token === snapToken) { renderWiringSegments(); paintSnapIndicator(); }
    }
  }

  function lockToGrid(deg, grid) {
    let best = null, bestDiff = null;
    grid.forEach(function (target) {
      const diff = Math.abs(((deg - target + 540) % 360) - 180);
      if (bestDiff === null || diff < bestDiff) { bestDiff = diff; best = target; }
    });
    return bestDiff !== null && bestDiff <= MODE_TOLERANCE_DEG ? best : null;
  }

  function polarGrid() {
    const out = [];
    for (let deg = 0; deg < 360; deg += POLAR_STEP_DEG) out.push(deg);
    return out;
  }

  // 正交/极轴只约束**交互预览与新增工具的取点**。权威实现是 cadcore.precision.osnap 的
  // ortho_lock / polar_track（服务端 snap_query 走的那一份）；这里是同容差的预览孪生。
  function applyDraftModes(world) {
    const base = S.drafts.length ? S.drafts[0] : null;
    if (!base) return world;
    const dx = world.x - base.x, dy = world.y - base.y;
    const radius = Math.hypot(dx, dy);
    if (!(radius > 0)) return world;
    const deg = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
    let target = S.modes.ortho ? lockToGrid(deg, [0, 90, 180, 270]) : null;
    if (target === null && S.modes.polar) target = lockToGrid(deg, polarGrid());
    if (target === null) return world;
    const rad = (target * Math.PI) / 180;
    return { x: base.x + radius * Math.cos(rad), y: base.y + radius * Math.sin(rad) };
  }

  function selectionRefs() {
    return S.selected && S.selected.uuid ? [S.selected.uuid] : [];
  }

  function needsSelection() {
    setMessage("error", label("err.unknown_reference", { reference: label("cmd." + S.tool) }));
    S.drafts = [];
    render();
  }

  function handleNewToolClick(world) {
    const pts = S.drafts;
    const layer = DEFAULT_LAYER;
    if (S.tool === "rectangle" && pts.length === 2) {
      runCommand("add_rectangle", { p1_nm: pointArg(pts[0]), p2_nm: pointArg(pts[1]),
                                    layer: layer });
      return true;
    }
    if (S.tool === "polygon" && pts.length === 2) {
      const radius = Math.round(dist(pts[0], pts[1]));
      if (radius <= 0) {
        setMessage("error", label("err.invalid_length", { text: "0" }));
        S.drafts = [];
        render();
      } else {
        runCommand("add_polygon", { center_nm: pointArg(pts[0]), radius_nm: radius,
                                    sides: POLYGON_SIDES, layer: layer });
      }
      return true;
    }
    if (S.tool === "slot" && pts.length === 3) {
      const width = Math.round(2 * pointSegmentDistance(pts[2], pts[0], pts[1]));
      const even = Math.max(2, width - (width % 2));
      runCommand("add_slot", { p1_nm: pointArg(pts[0]), p2_nm: pointArg(pts[1]),
                               width_nm: even, layer: layer });
      return true;
    }
    if (S.tool === "snap_query") {
      querySnap(world);
      S.drafts = [];
      render();
      return true;
    }
    if (WIRE_XFORM.indexOf(S.tool) < 0) return false;
    const refs = selectionRefs();
    if (!refs.length) { needsSelection(); return true; }
    if (S.tool === "move" || S.tool === "copy") {
      if (pts.length !== 2) return false;
      runCommand(S.tool, { refs: refs, dx_nm: Math.round(pts[1].x - pts[0].x),
                           dy_nm: Math.round(pts[1].y - pts[0].y) });
      return true;
    }
    if (S.tool === "mirror" && pts.length === 2) {
      runCommand("mirror", { refs: refs, p1_nm: pointArg(pts[0]), p2_nm: pointArg(pts[1]),
                             keep_original: true });
      return true;
    }
    if (S.tool === "rotate" && pts.length === 2) {
      const angle = (((Math.atan2(pts[1].y - pts[0].y, pts[1].x - pts[0].x) * 180) / Math.PI)
                     + 360) % 360;
      runCommand("rotate", { refs: refs, center_nm: pointArg(pts[0]), angle_deg: angle });
      return true;
    }
    if (S.tool === "scale" && pts.length === 3) {
      const reference = dist(pts[0], pts[1]);
      if (reference <= 0) {
        setMessage("error", label("err.invalid_length", { text: "0" }));
        S.drafts = [];
        render();
      } else {
        runCommand("scale", { refs: refs, center_nm: pointArg(pts[0]),
                              factor: dist(pts[0], pts[2]) / reference });
      }
      return true;
    }
    return false;
  }

  // #cmd-line 的通用形式：`<verb> key=value …`。`*_nm` 的值按**毫米**给（换算成纳米），
  // `*_deg` 按度；点写 `x,y`，多点用 `;` 分隔，refs 写 `uuid:sub,uuid:sub`。
  // 不是本段认识的动词就返回 false，交回既有的那个解析器（既有流程一行未改）。
  function parseWireValue(key, token) {
    const raw = String(token);
    if (key === "refs") return raw.split(",").map(function (item) { return item.split(":"); });
    if (key === "kinds") return raw.split(",");
    if (key === "layer") return raw;
    if (raw === "true" || raw === "false") return raw === "true";
    if (raw.indexOf(";") >= 0) {
      return raw.split(";").map(function (pair) {
        return pair.split(",").map(function (part) {
          return Math.round(Number(part) * NM_PER_MM);
        });
      });
    }
    if (raw.indexOf(",") >= 0) {
      const convert = /_nm$/.test(key);
      return raw.replace(/^\[|\]$/g, "").split(",").map(function (part) {
        const value = Number(part);
        return convert && isFinite(value) ? Math.round(value * NM_PER_MM) : part;
      });
    }
    const value = Number(raw);
    if (!isFinite(value)) return raw;
    return /_nm$/.test(key) ? Math.round(value * NM_PER_MM) : value;
  }

  function wiringCommandLine(raw) {
    const parts = String(raw || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return false;
    const verb = parts[0].toLowerCase();
    if (WIRE_VERBS.indexOf(verb) < 0) return false;
    const args = {};
    for (let i = 1; i < parts.length; i += 1) {
      const cut = parts[i].indexOf("=");
      if (cut <= 0) {
        setMessage("error", label("err.invalid_length", { text: parts[i] }));
        return true;
      }
      const key = parts[i].slice(0, cut);
      args[key] = parseWireValue(key, parts[i].slice(cut + 1));
    }
    runCommand(verb, args);
    return true;
  }

  // 捕获相：本段先拿到 Enter，认领得下就截断，既有的解析器不会再看到这一行。
  document.addEventListener("keydown", function (ev) {
    const cmd = byId("cmd-line");
    if (!cmd || ev.target !== cmd || ev.key !== "Enter") return;
    if (!wiringCommandLine(cmd.value)) return;
    ev.preventDefault();
    ev.stopPropagation();
    cmd.value = "";
  }, true);

  const WIRE_CLICK_TOOLS = WIRE_TOOLS.slice();
  let wiringBound = false;

  function bindWiring() {
    if (wiringBound) return;
    wiringBound = true;
    const pane = byId("canvas-pane");
    const canvas = byId("canvas");
    const indicator = byId("snap-indicator");
    if (pane && getComputedStyle(pane).position === "static") pane.style.position = "relative";
    if (indicator) {
      indicator.style.position = "absolute";
      indicator.style.zIndex = "3";
      indicator.style.pointerEvents = "none";
      indicator.style.whiteSpace = "nowrap";
      indicator.style.padding = "1px 4px";
      indicator.style.border = "1px solid " + colors.selected;
      indicator.style.borderRadius = "3px";
      indicator.style.background = colors.canvas;
      indicator.style.color = colors.selected;
      indicator.style.font = "11px ui-monospace, monospace";
      indicator.style.transform = "translate(10px, -50%)";
    }
    if (canvas) {
      canvas.addEventListener("pointermove", function (ev) {
        const world = applyDraftModes(toWorld(ev.offsetX, ev.offsetY));
        S.cursor = world;
        renderStatus();
        render();
        querySnap(world);
      });
      canvas.addEventListener("pointerleave", function () {
        S.snap = null;
        paintSnapIndicator();
        renderWiringSegments();
      });
    }
    // 取点在**捕获相**处理（document 上注册 ⇒ 早于画布自身的目标相监听器）：先让服务端的
    // 权威解析链（precision_pick：捕捉 → 栅格 → 正交/极轴 → 追踪）把原始光标点解析掉，
    // 再分派给新工具或既有 clickWorld —— 既有绘图流程一行未改，只是拿到的点已吸附。
    // "选择"工具不在这里拦（窗选/夹点由 v2 段自己的指针处理）。
    document.addEventListener("pointerup", async function (ev) {
      const node = byId("canvas");
      if (!node || ev.target !== node) return;
      if (ev.button !== 0) return;
      if (S.tool === "select") return;
      ev.stopPropagation();
      const raw = toWorld(ev.offsetX, ev.offsetY);
      const world = await pickPoint(raw);
      S.cursor = world;
      if (WIRE_CLICK_TOOLS.indexOf(S.tool) >= 0) {
        S.drafts.push(world);
        renderStatus();
        if (!handleNewToolClick(world)) render();
      } else {
        clickWorld(world, world);
      }
    }, true);
    const toolbar = byId("toolbar");
    if (toolbar) {
      toolbar.addEventListener("click", function (ev) {
        const button = ev.target.closest ? ev.target.closest("[data-tool]") : null;
        if (!button) return;
        const name = button.getAttribute("data-tool");
        if (MODE_BUTTONS.indexOf(name) >= 0) {
          S.modes[name] = !S.modes[name];
          paintModeButtons();
        }
        renderWiringSegments();
        paintSnapIndicator();
      });
    }
    paintModeButtons();
  }

  // 唯一的一处"包一层"：既有 renderAll 是刷新总入口，包它才能让语言切换/状态刷新同时
  // 刷到本段新增的段位（既有函数体一行未改）。
  const renderAllBase = renderAll;
  renderAll = function () {
    renderAllBase();
    bindWiring();
    renderWiringSegments();
    paintSnapIndicator();
  };


  /* ==================================================================================
     用户面接线 v2：取点管线（捕捉/栅格/正交/极轴/追踪）+ 精度设置面 + 命令面补全
     ----------------------------------------------------------------------------------
     这一段的定位与上一段相同：**加法**。几何规则、捕捉算法、测量算法全在 cadcore；
     本段只做三件事 —— ①把指针点交给服务端的权威解析链（precision_pick）；
     ②把可设的精度开关真正接到设置面（改了就影响取点，不是装饰）；
     ③把族里已实现、只差入口的命令接上工具栏/命令行。
     面向用户的词一律来自服务端语言包，本文件不写任何人类语言字面量。
     ================================================================================== */

  const SNAP_KINDS = ["endpoint", "point", "midpoint", "center", "quadrant", "intersection",
                      "perpendicular", "nearest"];
  const SNAP_EXTRA_KINDS = ["tangent", "apparent", "extension", "parallel"];
  const PRECISION_MODES = ["osnap", "ortho", "polar", "otrack", "dyninput", "grid"];
  //: 追加捕捉类型在"可达优先级"里的插入位置（与服务端 server.py 的 _merged_priority 同口径）。
  const EXTRA_AFTER = { apparent: "intersection", tangent: "perpendicular",
                        extension: "nearest", parallel: "nearest" };
  const GRIP_TOL_PX = 9;
  const WINDOW_MIN_PX = 4;
  const HATCH_PATTERNS = ["lines", "ansi31", "ansi32", "cross", "grid"];

  S.precision = {
    store: -1,                       // -1 = 未采（服务端没这张表），0/1 = 真读数
    kinds: SNAP_KINDS.slice(),       // 已启用（顺序 = 优先级）
    priority: SNAP_KINDS.slice(),
    extras: [],                      // 追加类型（切点/外观交点/延长线/平行）的启用集合
    tolerance_nm: 5 * NM_PER_MM,
    spacing_nm: 5 * NM_PER_MM,
    polar_increments_deg: [15, 30, 45, 90],
    ignore_hatch: false,
    pattern: "lines",
    hatch_spacing_nm: 4 * NM_PER_MM
  };
  S.modes = { ortho: false, polar: false, osnap: true, snap: false, dyn: true, otrack: false };
  S.snap = null;
  S.pick = null;
  S.selection = [];
  S.window = null;
  S.grips = [];
  S.gripDrag = null;
  S.measure = null;

  function anyModeOn() {
    return S.modes.osnap || S.modes.snap || S.modes.ortho || S.modes.polar || S.modes.otrack;
  }

  function activeSnapKinds() {
    return S.precision.kinds.concat(S.precision.extras);
  }

  function mergedPriority() {
    const out = [];
    S.precision.priority.forEach(function (kind) {
      out.push(kind);
      SNAP_EXTRA_KINDS.forEach(function (extra) {
        if (EXTRA_AFTER[extra] === kind) out.push(extra);
      });
    });
    return out;
  }

  function modeStateLabel(name) {
    return label(S.modes[name] ? "status.toggle.on" : "status.toggle.off",
                 { name: label("cmd." + name) });
  }

  function toolStatusLabel() {
    const key = "tool." + S.tool;
    if (lookup(key)) return label(key);
    const alias = KEY_ALIAS[key];
    if (alias && lookup(alias)) return label(alias);
    return label("cmd." + S.tool);
  }

  function snapKindLabel(kind) {
    const key = "status.snap." + String(kind || "");
    return lookup(key) ? label(key) : String(kind || "");
  }

  function fmtMm(nm) {
    return (Number(nm) / NM_PER_MM).toFixed(3);
  }

  function readoutLabel() {
    const pick = S.pick;
    if (!pick) return label("status.unmeasured");
    const parts = [];
    if (typeof pick.distance_nm === "number") {
      parts.push(label("msr.distance") + " " + fmtMm(pick.distance_nm) + unitSymbol());
    }
    if (typeof pick.angle_udeg === "number") {
      parts.push((pick.angle_udeg / 1e6).toFixed(2) + "\u00b0");
    }
    if (!parts.length && Array.isArray(pick.point_nm) && pick.point_nm.length === 2) {
      parts.push(fmtMm(pick.point_nm[0]) + ", " + fmtMm(pick.point_nm[1]));
    }
    return parts.length ? parts.join("  ") : label("status.unmeasured");
  }

  function renderWiringSegments() {
    setText(byId("status-tool"), toolStatusLabel());
    setText(byId("status-snap"),
            S.snap ? label("status.snap.indicator", { kind: snapKindLabel(S.snap.kind) })
                   : label("status.snap.none"));
    setText(byId("status-ortho"), modeStateLabel("ortho"));
    setText(byId("status-polar"), modeStateLabel("polar"));
    setText(byId("status-osnap"), modeStateLabel("osnap"));
    setText(byId("status-dyn"), modeStateLabel("dyn"));
    setText(byId("status-grid"),
            label(S.modes.snap ? "status.toggle.on" : "status.toggle.off",
                  { name: label("cmd.snap") + " " + fmtMm(S.precision.spacing_nm) + unitSymbol() }));
    setText(byId("status-readout"), readoutLabel());
    setText(byId("status-measure"), measureStatusLabel());
  }

  function measureStatusLabel() {
    if (!S.measure || !S.measure.text) return label("status.unmeasured");
    return S.measure.text;
  }

  function paintModeButtons() {
    ["ortho", "polar", "osnap", "snap", "dyn", "otrack"].forEach(function (name) {
      const node = document.querySelector('#toolbar [data-tool="' + name + '"]');
      if (node) node.setAttribute("aria-pressed", S.modes[name] ? "true" : "false");
    });
  }

  function paintSnapIndicator() {
    const node = byId("snap-indicator");
    if (!node) return;
    const point = S.snap && Array.isArray(S.snap.point_nm) ? S.snap.point_nm : null;
    if (!point || point.length < 2) { node.hidden = true; return; }
    node.hidden = false;
    node.setAttribute("data-snap-kind", String(S.snap.kind || ""));
    node.textContent = snapKindLabel(S.snap.kind);
    const on = toScreen({ x: point[0], y: point[1] });
    node.style.left = on.x + "px";
    node.style.top = on.y + "px";
  }

  let snapTokenV2 = 0;
  let snapBusyV2 = false;

  // 悬停捕捉：把**当刻启用的类型**（含追加类型）与优先级一起问服务端；命中就画指示器。
  // （本函数在 v2 段重定义，覆盖 v1 的同名声明 —— 变量名另起，避免重复声明。）
  async function querySnap(world) {
    if (!world || !view.scale || S.tool === "select" || !S.modes.osnap) {
      S.snap = null;
      paintSnapIndicator();
      return;
    }
    if (snapBusyV2) return;
    snapBusyV2 = true;
    const token = ++snapTokenV2;
    const tolerance = Math.max(1, Math.round(SNAP_TOL_PX / view.scale));
    try {
      const args = { point_nm: [Math.round(world.x), Math.round(world.y)], tolerance_nm: tolerance,
                     kinds: activeSnapKinds() };
      if (S.drafts.length) args.base_nm = [Math.round(S.drafts[0].x), Math.round(S.drafts[0].y)];
      if (S.precision.ignore_hatch) args.ignore_kinds = ["hatch"];
      const body = await postJSON(API.command, { command: "snap_query", args: args });
      if (token === snapTokenV2) {
        S.snap = body && body.snap ? body.snap : null;
        if (S.snap && Array.isArray(S.snap.point_nm)) acquireTrackPoint(S.snap.point_nm);
      }
    } catch (err) {
      if (token === snapTokenV2) S.snap = null;
    } finally {
      snapBusyV2 = false;
      if (token === snapTokenV2) { renderWiringSegments(); paintSnapIndicator(); }
    }
  }

  function lockToGrid(deg, grid) {
    let best = null, bestDiff = null;
    grid.forEach(function (target) {
      const diff = Math.abs(((deg - target + 540) % 360) - 180);
      if (bestDiff === null || diff < bestDiff) { bestDiff = diff; best = target; }
    });
    return bestDiff !== null && bestDiff <= MODE_TOLERANCE_DEG ? best : null;
  }

  function polarGrid() {
    const out = [];
    (S.precision.polar_increments_deg.length ? S.precision.polar_increments_deg : [15])
      .forEach(function (step) {
        for (let deg = 0; deg < 360; deg += step) if (out.indexOf(deg) < 0) out.push(deg);
      });
    return out;
  }

  // 预览用的局部管线（与服务端 precision_apply 同序：捕捉 → 栅格 → 正交/极轴）。
  // 权威解析在服务端（cadcore 的 precision_ops.precision_apply），这里只在没有基点/离线时兜底。
  function resolvePickLocal(world) {
    let point = { x: world.x, y: world.y };
    if (S.modes.osnap && S.snap && Array.isArray(S.snap.point_nm) && S.snap.point_nm.length === 2) {
      const candidate = { x: S.snap.point_nm[0], y: S.snap.point_nm[1] };
      if (dist(candidate, world) <= Math.max(1, SNAP_TOL_PX / Math.max(view.scale, 1e-9))) {
        point = candidate;
      }
    }
    if (S.modes.snap && S.precision.spacing_nm > 0) {
      point = { x: Math.round(point.x / S.precision.spacing_nm) * S.precision.spacing_nm,
                y: Math.round(point.y / S.precision.spacing_nm) * S.precision.spacing_nm };
    }
    const base = S.drafts.length ? S.drafts[0] : null;
    if (!base) return point;
    const dx = point.x - base.x, dy = point.y - base.y;
    const radius = Math.hypot(dx, dy);
    if (!(radius > 0)) return point;
    const deg = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
    let target = S.modes.ortho ? lockToGrid(deg, [0, 90, 180, 270]) : null;
    if (target === null && S.modes.polar) target = lockToGrid(deg, polarGrid());
    if (target === null) return point;
    const rad = (target * Math.PI) / 180;
    return { x: base.x + radius * Math.cos(rad), y: base.y + radius * Math.sin(rad) };
  }

  // 取点（点击时）：走服务端 precision_pick —— 对象捕捉 → 栅格步进 → 正交/极轴 → 捕捉追踪。
  // 服务端只在 L1 副本上跑（响应里带 doc_unchanged），真源一个字节都不动。
  async function pickPoint(raw) {
    const base = S.drafts.length ? S.drafts[0] : null;
    if (!anyModeOn()) { S.pick = null; return resolvePickLocal(raw); }
    try {
      const args = { point_nm: [Math.round(raw.x), Math.round(raw.y)] };
      if (base) args.base_nm = [Math.round(base.x), Math.round(base.y)];
      const body = await postJSON(API.command, { command: "precision_pick", args: args });
      S.pick = body || null;
      if (body && Array.isArray(body.point_nm) && body.point_nm.length === 2) {
        return { x: body.point_nm[0], y: body.point_nm[1] };
      }
    } catch (err) {
      S.pick = null;
    }
    return resolvePickLocal(raw);
  }

  /* ------------------------------------------------------------- 精度设置面（会真的生效） */

  async function savePrecision(verb, args) {
    try {
      await postJSON(API.command, { command: verb, args: args });
    } catch (err) {
      showError(err);
    }
    await loadPrecisionStatus();
    paintModeButtons();
    renderWiringSegments();
  }

  async function loadPrecisionStatus() {
    try {
      const body = await postJSON(API.command, { command: "precision_status", args: {} });
      S.precision.store = typeof body.store === "number" ? body.store : -1;
      if (Array.isArray(body.kinds) && body.kinds.length) S.precision.kinds = body.kinds.slice();
      if (Array.isArray(body.priority) && body.priority.length) S.precision.priority = body.priority.slice();
      if (typeof body.spacing_nm === "number" && body.spacing_nm > 0) S.precision.spacing_nm = body.spacing_nm;
      if (typeof body.tolerance_nm === "number" && body.tolerance_nm > 0) S.precision.tolerance_nm = body.tolerance_nm;
      if (Array.isArray(body.polar_increments_deg) && body.polar_increments_deg.length) {
        S.precision.polar_increments_deg = body.polar_increments_deg.slice();
      }
      const modes = body.modes || {};
      if (modes.osnap === 0 || modes.osnap === 1) S.modes.osnap = modes.osnap === 1;
      if (modes.ortho === 0 || modes.ortho === 1) S.modes.ortho = modes.ortho === 1;
      if (modes.polar === 0 || modes.polar === 1) S.modes.polar = modes.polar === 1;
      if (modes.otrack === 0 || modes.otrack === 1) S.modes.otrack = modes.otrack === 1;
      if (modes.grid === 0 || modes.grid === 1) S.modes.snap = modes.grid === 1;
    } catch (err) {
      S.precision.store = -1;
    }
    paintPrecisionPanel();
  }

  function panelNodes() {
    return {
      root: byId("precision-panel"),
      kinds: byId("osnap-kinds"),
      priority: byId("osnap-priority"),
      tolerance: byId("osnap-tolerance"),
      spacing: byId("grid-spacing"),
      increments: byId("polar-increments"),
      ignore: byId("ignore-hatch"),
      pattern: byId("hatch-pattern"),
      hatchSpacing: byId("hatch-spacing")
    };
  }

  function kindToggleRow(kind) {
    const row = document.createElement("li");
    row.className = "precision__row";
    row.setAttribute("data-kind", kind);
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = S.precision.kinds.indexOf(kind) >= 0 || S.precision.extras.indexOf(kind) >= 0;
    box.setAttribute("data-kind-toggle", kind);
    const name = document.createElement("span");
    name.className = "precision__name";
    name.textContent = snapKindLabel(kind);
    row.appendChild(box);
    row.appendChild(name);
    return row;
  }

  function paintPrecisionPanel() {
    const el2 = panelNodes();
    if (!el2.root) return;
    if (el2.kinds) {
      el2.kinds.textContent = "";
      SNAP_KINDS.forEach(function (kind) { el2.kinds.appendChild(kindToggleRow(kind)); });
      SNAP_EXTRA_KINDS.forEach(function (kind) { el2.kinds.appendChild(kindToggleRow(kind)); });
    }
    if (el2.priority) {
      el2.priority.textContent = "";
      S.precision.priority.forEach(function (kind, index) {
        const row = document.createElement("li");
        row.className = "precision__row";
        row.setAttribute("data-priority-kind", kind);
        const name = document.createElement("span");
        name.className = "precision__name";
        name.textContent = String(index + 1) + ". " + snapKindLabel(kind);
        const up = document.createElement("button");
        up.type = "button"; up.className = "precision__move";
        up.setAttribute("data-priority-move", "up"); up.setAttribute("data-priority-target", kind);
        up.textContent = label("panel.priority_up");
        const down = document.createElement("button");
        down.type = "button"; down.className = "precision__move";
        down.setAttribute("data-priority-move", "down"); down.setAttribute("data-priority-target", kind);
        down.textContent = label("panel.priority_down");
        row.appendChild(name); row.appendChild(up); row.appendChild(down);
        el2.priority.appendChild(row);
      });
    }
    if (el2.tolerance) el2.tolerance.value = fmtMm(S.precision.tolerance_nm);
    if (el2.spacing) el2.spacing.value = fmtMm(S.precision.spacing_nm);
    if (el2.increments) el2.increments.value = S.precision.polar_increments_deg.join(",");
    if (el2.ignore) el2.ignore.checked = !!S.precision.ignore_hatch;
    if (el2.pattern) {
      // 图案名是 cadcore 的**枚举 token**（不是人类文案），选项从 HATCH_PATTERNS 现填；
      // 这样 HTML 里不留任何裸文本（ui_gate 的 human_literals 腿）。
      if (el2.pattern.options.length !== HATCH_PATTERNS.length) {
        el2.pattern.textContent = "";
        HATCH_PATTERNS.forEach(function (name) {
          const option = document.createElement("option");
          option.value = name;
          option.textContent = name;
          el2.pattern.appendChild(option);
        });
      }
      el2.pattern.value = S.precision.pattern;
    }
    if (el2.hatchSpacing) el2.hatchSpacing.value = fmtMm(S.precision.hatch_spacing_nm);
    const badge = byId("precision-store");
    if (badge) badge.setAttribute("data-store", String(S.precision.store));
  }

  function movePriority(kind, delta) {
    const order = S.precision.priority.slice();
    const from = order.indexOf(kind);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= order.length) return;
    order.splice(from, 1);
    order.splice(to, 0, kind);
    S.precision.priority = order;
    // 优先级必须把每个基础类型恰好列一次（cadcore 的 snap_priority_set 会校验），变了就落盘。
    savePrecision("snap_priority_set", { priority: order });
  }

  function bindPrecisionPanel() {
    const el2 = panelNodes();
    if (!el2.root) return;
    el2.root.addEventListener("change", function (ev) {
      const box = ev.target.closest ? ev.target.closest("[data-kind-toggle]") : null;
      if (box) {
        const kind = box.getAttribute("data-kind-toggle");
        const base = SNAP_KINDS.slice();
        const extras = S.precision.extras.slice();
        if (SNAP_EXTRA_KINDS.indexOf(kind) >= 0) {
          const at = extras.indexOf(kind);
          if (box.checked && at < 0) extras.push(kind);
          if (!box.checked && at >= 0) extras.splice(at, 1);
          S.precision.extras = extras;
          paintPrecisionPanel();
          return;
        }
        const index = box.checked ? base.indexOf(kind) : base.indexOf(kind) + 1;
        const next = base.slice();
        if (box.checked) next.splice(index, 0, kind);
        else if (index - 1 >= 0) next.splice(index - 1, 1);
        savePrecision("snap_kinds_set", { kinds: next });
        return;
      }
      if (ev.target === el2.ignore) {
        S.precision.ignore_hatch = !!el2.ignore.checked;
        return;
      }
      if (ev.target === el2.pattern) {
        S.precision.pattern = String(el2.pattern.value || "lines");
        return;
      }
    });
    el2.root.addEventListener("click", function (ev) {
      const button = ev.target.closest ? ev.target.closest("[data-priority-move]") : null;
      if (button) {
        movePriority(button.getAttribute("data-priority-target"),
                     button.getAttribute("data-priority-move") === "up" ? -1 : 1);
        return;
      }
      const apply = ev.target.closest ? ev.target.closest("[data-precision-apply]") : null;
      if (apply) {
        const what = apply.getAttribute("data-precision-apply");
        if (what === "tolerance") {
          const mm = Number(el2.tolerance.value);
          if (isFinite(mm) && mm > 0) savePrecision("snap_tolerance_set", { tolerance_nm: Math.round(mm * NM_PER_MM) });
        } else if (what === "spacing") {
          const mm = Number(el2.spacing.value);
          if (isFinite(mm) && mm > 0) {
            S.precision.spacing_nm = Math.round(mm * NM_PER_MM);
            savePrecision("grid_set", { enabled: S.modes.snap, spacing_nm: S.precision.spacing_nm,
                                        snap_enabled: true });
          }
        } else if (what === "increments") {
          const values = String(el2.increments.value || "").split(",")
            .map(function (item) { return Number(String(item).trim()); })
            .filter(function (value) { return isFinite(value) && value > 0 && value <= 360; });
          if (values.length) savePrecision("polar_set", { enabled: S.modes.polar, increments_deg: values });
        } else if (what === "hatch") {
          const mm = Number(el2.hatchSpacing.value);
          if (isFinite(mm) && mm > 0) S.precision.hatch_spacing_nm = Math.round(mm * NM_PER_MM);
          S.precision.pattern = String(el2.pattern.value || "lines");
        }
      }
    });
  }

  /* ------------------------------------------------------------- 选择集 / 窗选 / 夹点 */

  function selectionRefs() {
    if (S.selection.length) return S.selection.slice();
    return S.selected && S.selected.uuid ? [S.selected.uuid] : [];
  }

  function needsSelection() {
    setMessage("error", label("err.unknown_reference", { reference: label("cmd." + S.tool) }));
    S.drafts = [];
    render();
  }

  function pointInBox(point, box) {
    return point.x >= box.minX && point.x <= box.maxX && point.y >= box.minY && point.y <= box.maxY;
  }

  function entityCrossesBox(entity, box) {
    const points = entityPoints(entity);
    if (points.some(function (p) { return pointInBox(p, box); })) return true;
    let hit = false;
    entitySegments(entity).forEach(function (segment) {
      if (hit) return;
      if (pointInBox(segment[0], box) || pointInBox(segment[1], box)) { hit = true; return; }
      const corners = [{ x: box.minX, y: box.minY }, { x: box.maxX, y: box.minY },
                       { x: box.maxX, y: box.maxY }, { x: box.minX, y: box.maxY }];
      for (let i = 0; i < corners.length && !hit; i += 1) {
        if (segmentsIntersect(segment[0], segment[1], corners[i], corners[(i + 1) % 4])) hit = true;
      }
    });
    return hit;
  }

  function segmentsIntersect(a, b, c, d) {
    const cross = function (p, q, r) { return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x); };
    const d1 = cross(c, d, a), d2 = cross(c, d, b), d3 = cross(a, b, c), d4 = cross(a, b, d);
    return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
  }

  function applySelection(hits, mode) {
    const known = {};
    S.selection.forEach(function (uuid) { known[uuid] = true; });
    hits.forEach(function (uuid) {
      if (mode === "add") known[uuid] = true;
      else if (mode === "remove") delete known[uuid];
      else known[uuid] = true;
    });
    S.selection = Object.keys(known);
    const first = S.selection.length ? findEntity(S.selection[0]) : null;
    S.selected = first;
    refreshGrips();
    render();
  }

  async function refreshGrips() {
    S.grips = [];
    const refs = selectionRefs();
    if (refs.length !== 1) { render(); return; }
    try {
      const body = await postJSON(API.command, {
        command: "grip_positions", args: { ref: refs[0] } });
      const grips = body && body.grips ? body.grips : {};
      S.grips = Object.keys(grips).map(function (name) {
        return { ref: refs[0], grip: name, point: { x: grips[name][0], y: grips[name][1] } };
      });
    } catch (err) {
      S.grips = [];
    }
    render();
  }

  function gripAt(screen) {
    let best = null, bestD = null;
    S.grips.forEach(function (item) {
      const on = toScreen(item.point);
      const d = Math.hypot(on.x - screen.x, on.y - screen.y);
      if (d <= GRIP_TOL_PX && (bestD === null || d < bestD)) { bestD = d; best = item; }
    });
    return best;
  }

  /* ------------------------------------------------------------- 动态输入 / 测量读数 */

  function paintDynInput() {
    const box = byId("dyn-input");
    if (!box) return;
    const base = S.drafts.length ? S.drafts[0] : null;
    const show = !!(base && S.modes.dyn && S.cursor && S.tool !== "select" && S.tool !== "measure");
    box.hidden = !show;
    if (!show) return;
    const on = toScreen(S.cursor);
    box.style.left = (on.x + 14) + "px";
    box.style.top = (on.y + 14) + "px";
    const length = byId("dyn-length");
    const angle = byId("dyn-angle");
    if (length && document.activeElement !== length) length.value = fmtMm(dist(base, S.cursor));
    if (angle && document.activeElement !== angle) {
      const deg = ((Math.atan2(S.cursor.y - base.y, S.cursor.x - base.x) * 180) / Math.PI + 360) % 360;
      angle.value = deg.toFixed(2);
    }
  }

  function commitDynInput() {
    const base = S.drafts.length ? S.drafts[0] : null;
    if (!base) return;
    const length = Number((byId("dyn-length") || {}).value);
    const angle = Number((byId("dyn-angle") || {}).value);
    if (!isFinite(length) || !isFinite(angle)) return;
    const rad = (angle * Math.PI) / 180;
    const point = { x: Math.round(base.x + length * NM_PER_MM * Math.cos(rad)),
                    y: Math.round(base.y + length * NM_PER_MM * Math.sin(rad)) };
    S.cursor = point;
    if (WIRE_CLICK_TOOLS.indexOf(S.tool) >= 0) {
      S.drafts.push(point);
      if (!handleNewToolClick(point)) render();
    } else {
      clickWorld(point, point);
    }
    paintDynInput();
  }

  // 通用测量（MEASUREGEOM 口径）：第一次点拾基点；第二次点若落在某个闭合环里 ⇒ 面积 + 周长，
  // 否则两点测距。闭合环是**只读探测**（boundary_probe），测量不会顺手改图。
  async function measureAt(point) {
    if (!S.measure || !S.measure.base) {
      S.measure = { base: point, text: label("msr.pick_second") };
      S.drafts = [point];
      renderStatus();
      render();
      return;
    }
    const base = S.measure.base;
    S.drafts = [];
    try {
      const probe = await postJSON(API.command, { command: "boundary_probe", args: {
        point_nm: [Math.round(point.x), Math.round(point.y)] } });
      if (probe && probe.found === 1) {
        const args = { boundary: probe.members };
        const area = await postJSON(API.command, { command: "measure_area", args: args });
        const perimeter = await postJSON(API.command, { command: "measure_perimeter", args: args });
        S.measure = { base: null, text: label("msr.area") + " " + fmtMm(area.area_nm2) + unitSymbol() + "2  "
                      + label("msr.perimeter") + " " + fmtMm(perimeter.perimeter_nm) + unitSymbol() };
      } else {
        const body = await postJSON(API.command, { command: "measure_distance", args: {
          p1_nm: [Math.round(base.x), Math.round(base.y)],
          p2_nm: [Math.round(point.x), Math.round(point.y)] } });
        S.measure = { base: null, text: label("msr.distance") + " " + fmtMm(body.distance_nm) + unitSymbol()
                      + "  dx " + fmtMm(body.dx_nm) + "  dy " + fmtMm(body.dy_nm) };
      }
    } catch (err) {
      showError(err);
      S.measure = { base: null, text: "" };
    }
    renderStatus();
    render();
  }

  /* ------------------------------------------------------------- 命令面补全（工具栏/命令行） */

  function defaultArgs(verb) {
    return { layer: DEFAULT_LAYER, verb: verb };
  }

  function handleNewToolClickV2(world) {
    const pts = S.drafts;
    const layer = DEFAULT_LAYER;
    if (S.tool === "polyline") {
      // 逐点拾取：双击/Enter/点回起点 ⇒ 提交（闭合由"点回起点"或 C 命令给）。
      if (pts.length >= 2) {
        const first = toScreen(pts[0]);
        const now = toScreen(world);
        if (Math.hypot(first.x - now.x, first.y - now.y) <= GRIP_TOL_PX) {
          runCommand("add_polyline", { points_nm: pts.slice(0, pts.length - 1).map(pointArg),
                                       closed: true, layer: layer });
          S.drafts = [];
          return true;
        }
      }
      setMessage("ok", label("status.command_done", { command: label("cmd.polyline") }));
      render();
      return true;
    }
    if (S.tool === "point" && pts.length === 1) {
      runCommand("add_point", { position: pointArg(pts[0]), layer: layer });
      return true;
    }
    if (S.tool === "hatch" && pts.length === 1) {
      runCommand("hatch", { point_nm: pointArg(pts[0]), pattern: S.precision.pattern,
                            spacing_nm: S.precision.hatch_spacing_nm, layer: layer });
      return true;
    }
    if (S.tool === "boundary" && pts.length === 1) {
      runCommand("boundary", { point_nm: pointArg(pts[0]), layer: layer });
      return true;
    }
    if (S.tool === "revcloud" && pts.length === 2) {
      const width = Math.abs(pts[1].x - pts[0].x), height = Math.abs(pts[1].y - pts[0].y);
      if (width < 1 || height < 1) {
        setMessage("error", label("err.invalid_length", { text: "0" }));
        S.drafts = [];
        render();
        return true;
      }
      runCommand("revcloud_rect", {
        center_nm: pointArg({ x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 }),
        width_nm: Math.round(width), height_nm: Math.round(height),
        arc_length_nm: Math.round(Math.min(width, height) / 4) || 1, layer: layer });
      return true;
    }
    if (S.tool === "measure" && pts.length === 1) {
      measureAt(pts[0]);
      return true;
    }
    if (S.tool === "explode" && pts.length === 1) {
      const refs = selectionRefs();
      if (!refs.length) { needsSelection(); return true; }
      runCommand("explode", { refs: refs });
      return true;
    }
    if (S.tool === "matchprop" && pts.length === 1) {
      const hit = hitEntity(pts[0]);
      if (!hit) { setMessage("error", label("err.unknown_reference", { reference: pointArg(pts[0]).join(",") })); S.drafts = []; render(); return true; }
      const refs = selectionRefs().filter(function (uuid) { return uuid !== hit.uuid; });
      if (!refs.length) { needsSelection(); return true; }
      runCommand("matchprop", { source: hit.uuid, targets: refs });
      return true;
    }
    if (S.tool === "stretch") {
      const refs = selectionRefs();
      if (!refs.length) { needsSelection(); return true; }
      if (pts.length !== 2) return false;
      runCommand("stretch", {
        refs: refs,
        window_nm: [Math.round(Math.min(pts[0].x, pts[1].x)), Math.round(Math.min(pts[0].y, pts[1].y)),
                    Math.round(Math.max(pts[0].x, pts[1].x)), Math.round(Math.max(pts[0].y, pts[1].y))],
        dx_nm: Math.round(pts[1].x - pts[0].x), dy_nm: Math.round(pts[1].y - pts[0].y) });
      return true;
    }
    return false;
  }

  // 既有段（v1）的 handleNewToolClick 继续管矩形/多边形/腰形槽/变换命令；本段先认领新工具。
  const handleNewToolClickV1 = handleNewToolClick;
  handleNewToolClick = function (world) {
    if (handleNewToolClickV2(world)) return true;
    return handleNewToolClickV1(world);
  };

  //: 命令行直通动词（v2 追加；词法/单位换算在 parseWireValue，几何规则在 cadcore）。
  const WIRE_VERBS_V2 = ["add_point", "boundary", "hatch", "gradient", "revcloud", "revcloud_rect",
                         "stretch", "explode", "matchprop", "grip_edit", "delete",
                         "measure_area", "measure_perimeter", "measure_distance",
                         "precision_pick", "precision_status", "grip_positions",
                         "snap_enable", "snap_kinds_set", "snap_priority_set", "snap_tolerance_set",
                         "ortho_set", "polar_set", "otrack_set", "dyninput_set", "grid_set"];
  WIRE_VERBS_V2.forEach(function (verb) { if (WIRE_VERBS.indexOf(verb) < 0) WIRE_VERBS.push(verb); });

  const TOOL_NAMES_V2 = ["polyline", "point", "hatch", "boundary", "revcloud", "measure",
                         "explode", "matchprop", "stretch", "osnap", "snap", "dyn", "otrack"];
  TOOL_NAMES_V2.forEach(function (name) {
    if (TOOLS.indexOf(name) < 0) TOOLS.push(name);
    if (WIRE_CLICK_TOOLS.indexOf(name) < 0) WIRE_CLICK_TOOLS.push(name);
  });

  /* ------------------------------------------------------------- 指针/键盘（v2） */

  let v2Bound = false;
  let selectDrag = null;

  //: 工具栏模式按钮 → 落盘动词（按下即改设置；改了的设置**真的会影响下一次取点**）。
  const MODE_VERBS = { ortho: "ortho_set", polar: "polar_set", osnap: "snap_enable",
                       snap: "grid_set", dyn: "dyninput_set", otrack: "otrack_set" };
  //: AutoCAD 习惯的功能键（F3 捕捉 / F8 正交 / F9 栅格捕捉 / F10 极轴 / F11 追踪 / F12 动态输入）。
  const MODE_KEYS = { F3: "osnap", F8: "ortho", F9: "snap", F10: "polar", F11: "otrack", F12: "dyn" };
  S.otrackPoints = [];

  function persistMode(name) {
    const on = !!S.modes[name];
    if (name === "ortho") {
      savePrecision("ortho_set", { enabled: on });
    } else if (name === "polar") {
      savePrecision("polar_set", { enabled: on, increments_deg: S.precision.polar_increments_deg });
    } else if (name === "osnap") {
      savePrecision("snap_enable", { enabled: on });
    } else if (name === "snap") {
      savePrecision("grid_set", { enabled: false, spacing_nm: S.precision.spacing_nm, snap_enabled: on });
    } else if (name === "dyn") {
      savePrecision("dyninput_set", { enabled: on });
    } else if (name === "otrack") {
      savePrecision("otrack_set", { enabled: on, points_nm: S.otrackPoints });
    }
  }

  function toggleMode(name) {
    if (!MODE_VERBS[name]) return false;
    S.modes[name] = !S.modes[name];
    paintModeButtons();
    persistMode(name);
    renderWiringSegments();
    render();
    return true;
  }

  function acquireTrackPoint(point) {
    if (!S.modes.otrack || !Array.isArray(point) || point.length !== 2) return;
    const key = point[0] + "," + point[1];
    const known = S.otrackPoints.some(function (item) { return item[0] + "," + item[1] === key; });
    if (known) return;
    S.otrackPoints = S.otrackPoints.concat([[point[0], point[1]]]).slice(-8);
    persistMode("otrack");     // 采集点随即落盘 ⇒ 下一次取点的对齐路径用它（不是画着玩的）
  }

  function bindWiringV2() {
    if (v2Bound) return;
    v2Bound = true;
    bindPrecisionPanel();
    const toolbar = byId("toolbar");
    if (toolbar) {
      // 捕获相先认领模式按钮（v1 的处理器只认 ortho/polar）：认领后 stopPropagation，
      // 避免同一击被处理两次。
      toolbar.addEventListener("click", function (ev) {
        const button = ev.target.closest ? ev.target.closest("[data-tool]") : null;
        if (!button) return;
        if (toggleMode(button.getAttribute("data-tool"))) ev.stopPropagation();
      }, true);
    }
    document.addEventListener("keydown", function (ev) {
      const name = MODE_KEYS[ev.key];
      if (!name) return;
      const target = ev.target || {};
      if (target.id === "cmd-line" || target.id === "dyn-length" || target.id === "dyn-angle") return;
      if (toggleMode(name)) ev.preventDefault();
    });
    paintPrecisionPanel();
    loadPrecisionStatus();     // 落盘设置回读（表缺席 ⇒ store=-1，不冒充"全关"）
    const canvas = byId("canvas");
    if (canvas) {
      // 夹点拖拽：命中夹点后按下即进入拖拽（本地预览），抬起时把解析后的点交给 grip_edit。
      canvas.addEventListener("pointerdown", async function (ev) {
        if (ev.button !== 0 || S.tool !== "select") return;
        const grip = gripAt({ x: ev.offsetX, y: ev.offsetY });
        if (!grip) return;
        ev.stopPropagation();
        canvas.setPointerCapture(ev.pointerId);
        S.gripDrag = grip;
        render();
      }, true);
      canvas.addEventListener("pointermove", function (ev) {
        if (!S.gripDrag) return;
        S.gripDrag.preview = toWorld(ev.offsetX, ev.offsetY);
        render();
      }, true);
      canvas.addEventListener("pointerup", async function (ev) {
        if (!S.gripDrag) return;
        const grip = S.gripDrag;
        S.gripDrag = null;
        ev.stopPropagation();
        const raw = toWorld(ev.offsetX, ev.offsetY);
        const world = await pickPoint(raw);
        try {
          await postJSON(API.command, { command: "grip_edit", args: {
            ref: grip.ref, grip: grip.grip,
            to_nm: [Math.round(world.x), Math.round(world.y)] } });
        } catch (err) {
          showError(err);
        }
        await refreshState();
        await refreshGrips();
      }, true);
      // 指针移动：本地管线先把光标吸到捕捉/栅格/正交后的点（预览），悬停捕捉查询由 v1 段的
      // 监听器承担（它已注册在同一个 canvas 上，本段不重复发请求）。
      canvas.addEventListener("pointermove", function (ev) {
        const world = resolvePickLocal(toWorld(ev.offsetX, ev.offsetY));
        S.cursor = world;
        renderStatus();
        render();
        paintDynInput();
      });
    }
    // 选择：窗选（左→右 = 全包含）/ 交叉窗选（右→左 = 相交）+ Shift 加选 / Alt 减选。
    document.addEventListener("pointerdown", function (ev) {
      const node = byId("canvas");
      if (!node || ev.target !== node || ev.button !== 0 || S.tool !== "select") return;
      selectDrag = { x: ev.offsetX, y: ev.offsetY, moved: 0, shift: ev.shiftKey, alt: ev.altKey };
    }, true);
    document.addEventListener("pointermove", function (ev) {
      if (!selectDrag) return;
      const node = byId("canvas");
      if (!node || ev.target !== node) return;
      selectDrag.moved += Math.abs(ev.offsetX - selectDrag.x) + Math.abs(ev.offsetY - selectDrag.y);
      selectDrag.to = { x: ev.offsetX, y: ev.offsetY };
      if (selectDrag.moved > WINDOW_MIN_PX) {
        S.window = { from: { x: selectDrag.x, y: selectDrag.y }, to: selectDrag.to };
        render();
      }
    }, true);
    document.addEventListener("pointerup", function (ev) {
      if (!selectDrag) return;
      const drag = selectDrag;
      selectDrag = null;
      const node = byId("canvas");
      if (!node || ev.target !== node || ev.button !== 0) return;
      if (drag.moved > 3) { S.window = null; render(); return; }   // 拖拽 = 平移视图，不是选择
      ev.stopPropagation();
      const world = toWorld(ev.offsetX, ev.offsetY);
      const hit = hitEntity(world);
      const mode = drag.shift ? "add" : (drag.alt ? "remove" : "replace");
      if (!hit) {
        if (mode === "replace") applySelection([], "replace");
        return;
      }
      applySelection([hit.uuid], mode);
    }, true);
    document.addEventListener("keydown", function (ev) {
      // 守卫换成共用谓词：原来只列了 cmd-line/dyn-length/dyn-angle，漏掉精度面板的
      // #osnap-tolerance / #grid-spacing / #polar-increments / #hatch-spacing（x2-v5 F2）。
      if (isTextEntryTarget(ev.target)) return;
      if (ev.key === "Delete") {
        const refs = selectionRefs();
        if (refs.length) {
          ev.preventDefault();
          postJSON(API.command, { command: "delete", args: { refs: refs } })
            .then(function () { S.selection = []; S.selected = null; return refreshState(); })
            .catch(showError);
        }
        return;
      }
      if (ev.key === "Enter" && S.tool === "polyline" && S.drafts.length >= 2) {
        ev.preventDefault();
        runCommand("add_polyline", { points_nm: S.drafts.map(pointArg), closed: false,
                                     layer: DEFAULT_LAYER });
        return;
      }
      if (ev.key === "Enter" && S.tool === "measure" && S.drafts.length === 1) {
        ev.preventDefault();
        measureAt(S.drafts[0]);
        return;
      }
    });
    ["dyn-length", "dyn-angle"].forEach(function (id) {
      const node = byId(id);
      if (!node) return;
      node.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); commitDynInput(); }
        if (ev.key === "Tab") {
          ev.preventDefault();
          const other = byId(id === "dyn-length" ? "dyn-angle" : "dyn-length");
          if (other) other.focus();
        }
      });
    });
  }

  function overlayLayer(ctx) {
    if (S.window) {
      const a = toScreen(S.window.from), b = toScreen(S.window.to);
      const left = Math.min(a.x, b.x), top = Math.min(a.y, b.y);
      const width = Math.abs(a.x - b.x), height = Math.abs(a.y - b.y);
      ctx.save();
      ctx.setLineDash(b.x >= a.x ? [] : [5, 4]);
      ctx.strokeStyle = colors.selected;
      ctx.lineWidth = 1;
      ctx.strokeRect(left, top, width, height);
      ctx.globalAlpha = 0.08;
      ctx.fillStyle = colors.selected;
      ctx.fillRect(left, top, width, height);
      ctx.restore();
    }
    if (S.selection.length) {
      ctx.save();
      ctx.strokeStyle = colors.selected;
      ctx.lineWidth = 2;
      entityList().forEach(function (entity) {
        if (S.selection.indexOf(entity.uuid) < 0) return;
        entitySegments(entity).forEach(function (segment) {
          const a = toScreen(segment[0]), b = toScreen(segment[1]);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        });
      });
      ctx.restore();
    }
    S.grips.forEach(function (item) {
      const on = toScreen(item.point);
      ctx.save();
      ctx.fillStyle = colors.canvas;
      ctx.strokeStyle = colors.selected;
      ctx.lineWidth = 1.5;
      ctx.fillRect(on.x - 3.5, on.y - 3.5, 7, 7);
      ctx.strokeRect(on.x - 3.5, on.y - 3.5, 7, 7);
      ctx.restore();
    });
    if (S.gripDrag && S.gripDrag.preview) {
      const on = toScreen(S.gripDrag.preview);
      ctx.save();
      ctx.strokeStyle = colors.selected;
      ctx.setLineDash([4, 4]);
      ctx.strokeRect(on.x - 5, on.y - 5, 10, 10);
      ctx.restore();
    }
    if (S.modes.osnap && S.snap && Array.isArray(S.snap.point_nm)) {
      const on = toScreen({ x: S.snap.point_nm[0], y: S.snap.point_nm[1] });
      ctx.save();
      ctx.strokeStyle = colors.selected;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(on.x - 4.5, on.y - 4.5, 9, 9);
      ctx.restore();
    }
    // OTRACK 对齐路径：从每个已采集追踪点画到光标的水平/垂直虚线（与 cadcore 的
    // "只做水平/垂直"同口径 —— 本段不另立一套几何）。
    if (S.modes.otrack && S.cursor && S.otrackPoints.length) {
      const cur = toScreen(S.cursor);
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = colors.dimension;
      ctx.lineWidth = 1;
      S.otrackPoints.forEach(function (point) {
        const on = toScreen({ x: point[0], y: point[1] });
        ctx.beginPath();
        ctx.moveTo(on.x, on.y);
        ctx.lineTo(cur.x, on.y);
        ctx.moveTo(on.x, on.y);
        ctx.lineTo(on.x, cur.y);
        ctx.stroke();
        ctx.strokeRect(on.x - 3, on.y - 3, 6, 6);
      });
      ctx.restore();
    }
  }

  // 唯一的两处"包一层"：render（画叠加层）与 renderAll（初始化面板 + 刷状态段位）。
  const renderBaseV2 = render;
  render = function () {
    renderBaseV2();
    if (!el.canvas || !view.scale) return;
    const ctx = el.canvas.getContext("2d");
    if (ctx) overlayLayer(ctx);
  };

  const renderStatusBaseV2 = renderStatus;
  renderStatus = function () {
    renderStatusBaseV2();
    renderWiringSegments();
    paintDynInput();
  };

  const renderAllBaseV2 = renderAll;
  renderAll = function () {
    renderAllBaseV2();
    bindWiringV2();
    paintModeButtons();
    paintPrecisionPanel();
    renderWiringSegments();
    paintSnapIndicator();
    paintDynInput();
  };

  async function boot() {
    el = {
      canvas: document.getElementById("canvas"),
      pane: document.getElementById("canvas-pane"),
      toolbar: document.getElementById("toolbar"),
      lang: document.getElementById("lang-select"),
      standard: document.getElementById("standard-select"),
      layers: document.getElementById("layers"),
      status: document.getElementById("status"),
      message: document.getElementById("status-message"),
      statusLanguage: document.getElementById("status-language"),
      statusStandard: document.getElementById("status-standard"),
      statusProjection: document.getElementById("status-projection"),
      statusEntities: document.getElementById("status-entities"),
      statusCursor: document.getElementById("status-cursor"),
      profileStandard: document.getElementById("profile-standard"),
      profileProjection: document.getElementById("profile-projection"),
      cmd: document.getElementById("cmd-line")
    };
    const viewerLink = document.createElement("a");
    viewerLink.id = "open-3d";
    viewerLink.href = "/web/3d.html";
    viewerLink.textContent = "3D";
    el.toolbar.appendChild(viewerLink);
    readColors();
    setTool(S.tool);
    bind();
    bindCanvas();
    resize();
    fitView();
    renderAll();
    try {
      await loadCatalogs();
      await refreshState();
      setMessage("", "");
    } catch (err) {
      showError(err);
      renderStatus();
    }
  }

  /* ==== opencad-web-v3:BEGIN ==== */
  /* ============================================================ 用户面接线 v3（additive only）
     本段只做加法，全部落在本文件（opencad/web/js/app.js）内：
       - DXF 导出：**浏览器侧**真字节（Blob + <a download> 真下载）。线上服务的静态面只有 /web/**，
         服务端 /api/export/dxf 的产物在浏览器里没有出口（r8 4.9 实测 anchors=0/downloadAnchors=0），
         所以导出必须由浏览器自己出字节，用户才真拿得到文件；
       - DXF 导入：本地 .dxf → 逐条 /api/command 建成草图实体（几何规则全在 cadcore，本段只做词法、
         单位换算与计数），"跳过/失败/不支持的类型"如实上报，不静默丢；
       - 错误提示：保留既有 setMessage(kind, ·) 契约（状态行 + data-message-kind），另加一条独立的
         role="alert" 通道与按错误码给出的可操作提示；
       - 快捷键：Ctrl+S / Ctrl+O / Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y / F / F1 / Esc，工具条方向键导航
         （ARIA APG roving tabindex），F1 快捷键面板；
       - 进度：确定/不确定/空闲三态进度条（role="progressbar"），确定态带真分子分母，未采不写 0。
     约束（逐条可在仓里的尺子上复现）：
       1. 不写人类语言字面量：面向用户的词一律 label(key)（缺键沿用 deriveFromKey 兜底，与本文件既有
          契约一致），键值来自服务端语言包；本段把用到的缺键记进 V3.derivedKeys 供体检。
       2. index.html / css/app.css 不在本席写面内 ⇒ 新增节点一律 JS 创建 + 内联样式，显隐直写
          element.style.display（**不用 [hidden] 属性**：app.css 的作者样式会压过 UA 的
          [hidden]{display:none}，同一个坑见 r8 4.5 的 #dyn-input）。
       3. 计数三态：0 = 真为零，-1 = 未采（NOT_MEASURED），不适用就不出现在串里；未采时
          **不写 aria-valuenow**（写了就是伪造一个总数）。
       4. 不改既有流程：本段只包 api / showError / onAction / boot 四处，绘图、命令、选择、精度面板
          一行未动。
  */

  const V3 = {
    ready: false,
    derivedKeys: {},
    maxImportEntities: 5000,
    maxFileBytes: 32 * 1024 * 1024,
    importing: false,
    cancel: false,
    alert: null
  };

  // 缺键只记一次，便于体检（返回值仍是 deriveFromKey 的可读兜底，与服务端口径一致）。
  function v3Text(key, args) {
    const r = text(key, args);
    if (!r) return "";
    if (r.missing) V3.derivedKeys[key] = 1;
    return r.text;
  }

  function v3Yield() {
    return new Promise(function (resolve) { setTimeout(resolve, 0); });
  }

  function v3Stamp() {
    const d = new Date();
    const pad = function (n) { return (n < 10 ? "0" : "") + n; };
    return String(d.getFullYear()) + pad(d.getMonth() + 1) + pad(d.getDate()) + "-"
      + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
  }

  function v3UniqPush(list, value) {
    if (list.indexOf(value) < 0) list.push(value);
  }

  /* ---------------------------------------------------------------- 进度（三态） */

  const v3Progress = (function () {
    let box = null, bar = null, cap = null, lab = null, cancelBtn = null, wrap = null;
    let total = NOT_MEASURED, done = 0, hideTimer = null, cancelHook = null;

    function ensure() {
      if (box && box.parentNode) return box;
      // x2-v10 F3：role="progressbar" 的子节点在无障碍树里是 presentational
      // （WAI-ARIA 1.2 Children Presentational: true）⇒ 取消按钮不能是它的子节点。
      // 这里加一个**无 role** 的定位容器 wrap，把 box(progressbar) 与 cancelBtn 放成兄弟。
      wrap = document.createElement("div");
      wrap.id = "opencad-progress-wrap";
      wrap.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:2147483000;display:none;";
      box = document.createElement("div");
      box.id = "opencad-progress";
      box.setAttribute("role", "progressbar");
      box.setAttribute("aria-valuemin", "0");
      box.setAttribute("data-progress-state", "idle");
      box.style.cssText = "min-width:260px;max-width:48vw;padding:8px 10px;"
        + "border:1px solid rgba(127,127,127,.45);"
        + "border-radius:6px;background:rgba(24,24,28,.9);color:#fff;"
        + "font:12px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;";
      const head = document.createElement("div");
      head.style.cssText = "display:flex;justify-content:space-between;gap:10px;align-items:baseline;";
      lab = document.createElement("span");
      lab.id = "opencad-progress-label";
      cap = document.createElement("span");
      cap.id = "opencad-progress-caption";
      cap.style.cssText = "opacity:.85;font-variant-numeric:tabular-nums;";
      head.appendChild(lab);
      head.appendChild(cap);
      const track = document.createElement("div");
      track.style.cssText = "margin-top:6px;height:4px;border-radius:2px;"
        + "background:rgba(255,255,255,.22);overflow:hidden;";
      bar = document.createElement("div");
      bar.id = "opencad-progress-bar";
      bar.style.cssText = "height:100%;width:0%;background:#8ab4ff;transition:width .12s linear;";
      track.appendChild(bar);
      cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.id = "opencad-progress-cancel";
      cancelBtn.style.cssText = "margin-top:6px;padding:2px 8px;font:inherit;cursor:pointer;"
        + "background:transparent;color:inherit;border:1px solid rgba(255,255,255,.4);"
        + "border-radius:4px;display:none;";
      cancelBtn.addEventListener("click", function () {
        V3.cancel = true;
        if (cancelHook) cancelHook();
      });
      wrap.appendChild(box);
      wrap.appendChild(cancelBtn);        // 兄弟节点，不在 progressbar 里
      document.body.appendChild(wrap);
      return box;
    }

    function paint(text) {
      if (!box) return;
      if (wrap) wrap.style.display = "block";
      lab.textContent = text || lab.textContent;
      if (total === NOT_MEASURED) {
        // 未采：不写 aria-valuenow/aria-valuemax（写了就是把"不知道"伪装成一个数）
        box.setAttribute("data-progress-state", "indeterminate");
        box.removeAttribute("aria-valuenow");
        box.removeAttribute("aria-valuemax");
        box.removeAttribute("aria-valuetext");
        bar.style.width = "100%";
        bar.style.opacity = ".35";
        cap.textContent = "";
      } else {
        const pct = total > 0 ? Math.round((done / total) * 100) : 100;
        box.setAttribute("data-progress-state", "determinate");
        box.setAttribute("aria-valuemax", String(total));
        box.setAttribute("aria-valuenow", String(done));
        box.setAttribute("aria-valuetext", String(done) + "/" + String(total));
        bar.style.opacity = "1";
        bar.style.width = String(Math.max(0, Math.min(100, pct))) + "%";
        cap.textContent = String(done) + "/" + String(total);
      }
    }

    function begin(text, count, cancelable, onCancel) {
      ensure();
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      total = (typeof count === "number" && isFinite(count) && count >= 0) ? Math.round(count) : NOT_MEASURED;
      done = 0;
      V3.cancel = false;
      cancelHook = typeof onCancel === "function" ? onCancel : null;
      cancelBtn.style.display = cancelable ? "inline-block" : "none";
      cancelBtn.disabled = false;                 // 上一次取消留下的灰按钮必须复位（x2-v10 F4）
      cancelBtn.textContent = v3Text("help.cancel");
      paint(text);
    }

    function step(n, text) {
      if (!box) return;
      done = typeof n === "number" && isFinite(n) ? Math.round(n) : done + 1;
      if (total !== NOT_MEASURED && done > total) done = total;
      paint(text);
    }

    function finish(text) {
      if (!box) return;
      if (text) cap.textContent = text;
      box.setAttribute("data-progress-state", total === NOT_MEASURED ? "indeterminate" : "determinate");
      cancelBtn.style.display = "none";
      cancelHook = null;
      hideTimer = setTimeout(function () {
        if (wrap) wrap.style.display = "none";
        box.setAttribute("data-progress-state", "idle");
        hideTimer = null;
      }, 900);
    }

    function node() { return box; }

    return { begin: begin, step: step, finish: finish, ensure: ensure, node: node };
  }());

  /* ---------------------------------------------------------------- 错误提示（独立 alert 通道 + 可操作提示） */

  function v3Hint(err) {
    const code = err && err.code ? String(err.code) : "";
    const extra = (err && err.extra) || {};
    if (code === "unknown_command") {
      return v3Text("err.not_implemented", { feature: String(extra.command || "") });
    }
    if (code === "dxf_version_unsupported") {
      return v3Text("err.dxf_version_unsupported", { version: String(extra.version || "") });
    }
    if (code === "path_rejected" || code === "permission_denied") {
      return v3Text("err.permission_denied", { path: String(extra.path || "") });
    }
    if (code === "file_not_found") {
      return v3Text("err.file_not_found", { path: String(extra.path || "") });
    }
    if (code === "nothing_to_undo") return v3Text("hint.undo_empty");
    if (code === "nothing_to_redo") return v3Text("hint.redo_empty");
    if (code === "missing_argument") {
      return v3Text("err.missing_argument", { argument: String(extra.argument || "") });
    }
    if (code === "import_failed") {
      return v3Text("err.import_failed", { format: "DXF", detail: String(extra.detail || "") });
    }
    if (code === "export_failed") {
      return v3Text("err.export_failed", { format: "DXF", detail: String(extra.detail || "") });
    }
    if (code === "timeout") return v3Text("hint.timeout");
    return "";
  }

  function v3AlertEnsure() {
    if (V3.alert && V3.alert.box && V3.alert.box.parentNode) return V3.alert;
    const box = document.createElement("div");
    box.id = "opencad-alert";
    box.setAttribute("role", "alert");
    box.setAttribute("aria-live", "assertive");
    box.setAttribute("data-message-kind", "error");
    box.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);"
      + "z-index:2147483001;max-width:min(720px,92vw);padding:8px 34px 8px 10px;"
      + "border:1px solid #c0392b;border-left-width:4px;border-radius:6px;"
      + "background:#fdecea;color:#611a15;font:12px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;"
      + "box-shadow:0 2px 10px rgba(0,0,0,.18);display:none;white-space:pre-wrap;";
    const main = document.createElement("div");
    main.id = "opencad-alert-text";
    const hint = document.createElement("div");
    hint.id = "opencad-alert-hint";
    hint.style.cssText = "margin-top:2px;opacity:.9;";
    const close = document.createElement("button");
    close.type = "button";
    close.id = "opencad-alert-close";
    close.style.cssText = "position:absolute;top:2px;right:4px;border:0;background:transparent;"
      + "color:inherit;font:14px/1 system-ui,sans-serif;cursor:pointer;padding:4px;";
    close.addEventListener("click", function () { box.style.display = "none"; });
    box.appendChild(main);
    box.appendChild(hint);
    box.appendChild(close);
    document.body.appendChild(box);
    V3.alert = { box: box, main: main, hint: hint, close: close };
    return V3.alert;
  }

  function v3AlertShow(message, hint) {
    const node = v3AlertEnsure();
    node.close.textContent = v3Text("help.close");
    node.close.setAttribute("aria-label", v3Text("help.close"));
    node.main.textContent = message || "";
    node.hint.textContent = hint || "";
    node.hint.style.display = hint ? "block" : "none";
    node.box.style.display = "block";
    return node;
  }

  // 导入/导出这类"整体失败"也走同一通道：状态行保既有契约，alert 补一条带提示的通道。
  function v3ReportError(key, args, err) {
    const message = v3Text(key, args);
    setMessage("error", message);
    v3AlertShow(message, v3Hint(err));
    return message;
  }

  /* ---------------------------------------------------------------- 文件输入 / 拖放 */

  function v3FileInput() {
    if (V3.fileInput && V3.fileInput.parentNode) return V3.fileInput;
    const input = document.createElement("input");
    input.type = "file";
    input.id = "opencad-file";
    input.accept = ".dxf,.DXF,application/dxf,text/plain,application/octet-stream";
    input.tabIndex = -1;
    input.setAttribute("aria-hidden", "true");
    // 不用 hidden 属性：见本段题注约束 2；display:none 的文件输入仍可被脚本点击。
    input.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:-9999px;";
    input.addEventListener("change", function () {
      const file = input.files && input.files[0] ? input.files[0] : null;
      input.value = "";
      if (file) v3ImportFile(file);
    });
    document.body.appendChild(input);
    V3.fileInput = input;
    return input;
  }

  function v3PickFile() {
    const input = v3FileInput();
    input.click();
    return true;
  }

  function v3ReadFile(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(new Error("file_read_failed")); };
      try {
        reader.readAsText(file, "utf-8");
      } catch (err) {
        reject(err);
      }
    });
  }

  async function v3ImportFile(file) {
    if (!file) return;
    const name = String(file.name || "");
    if (!/\.dxf$/i.test(name)) {
      v3ReportError("err.import_failed", { format: "DXF", detail: "unsupported_file_type" });
      return;
    }
    if (typeof file.size === "number" && file.size > V3.maxFileBytes) {
      v3ReportError("err.import_failed", { format: "DXF", detail: "file_too_large" });
      return;
    }
    let contentText = "";
    try {
      contentText = await v3ReadFile(file);
    } catch (err) {
      v3ReportError("err.import_failed", { format: "DXF", detail: "file_read_failed" }, err);
      return;
    }
    await v3ImportDxfText(name, contentText);
  }

  function v3BindDropZone() {
    const stop = function (ev) { ev.preventDefault(); };
    window.addEventListener("dragover", stop, false);
    window.addEventListener("drop", function (ev) {
      ev.preventDefault();
      const files = ev.dataTransfer && ev.dataTransfer.files ? ev.dataTransfer.files : null;
      if (files && files.length) v3ImportFile(files[0]);
    }, false);
  }

  /* ---------------------------------------------------------------- DXF 写侧（L1 → DXF 真字节） */

  // $INSUNITS 码 → 每单位多少纳米（写侧按文档 unit 反查，读侧按 DXF 头换算）。
  const V3_INSUNITS_NM = {
    0: NM_PER_MM, 1: 25.4e6, 2: 304.8e6, 3: 1609344e6, 4: 1e6, 5: 1e7, 6: 1e9, 7: 1e12,
    8: 25.4, 9: 25400, 10: 914.4e6, 11: 0.1, 12: 1, 13: 1e3, 14: 1e8, 15: 1e10, 16: 1e11, 17: 1e18
  };
  const V3_UNIT_INSUNITS = { mm: 4, cm: 5, m: 6, km: 7, in: 1, ft: 2, yd: 10, nm: 12, um: 13 };

  function v3DocUnit() {
    const doc = S.doc && typeof S.doc === "object" ? S.doc : null;
    return (doc && nonEmpty(doc.unit)) || "mm";
  }

  function v3Num(value) {
    // DXF 实数文本：固定小数、不用指数（部分读侧不认 E 记法），末尾零去掉。
    if (typeof value !== "number" || !isFinite(value)) return "0";
    let s = value.toFixed(6);
    if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    if (s === "" || s === "-0") s = "0";
    return s;
  }

  function v3Pair(out, code, value) {
    out.push(String(code));
    out.push(String(value));
  }

  function v3DxfComment(out, lineText) {
    v3Pair(out, 999, lineText);
  }

  async function v3BuildDxf() {
    const list = entityList();
    const dims = dimensionList();
    const doc = S.doc && typeof S.doc === "object" ? S.doc : {};
    const annotations = Array.isArray(doc.annotations) ? doc.annotations : [];
    const unit = v3DocUnit();
    const insunits = Object.prototype.hasOwnProperty.call(V3_UNIT_INSUNITS, unit)
      ? V3_UNIT_INSUNITS[unit] : 0;
    const perUnit = V3_INSUNITS_NM[insunits] || NM_PER_MM;
    const conv = function (nm) { return v3Num(Number(nm) / perUnit); };
    const out = [];
    const layers = [DEFAULT_LAYER];
    const unsupported = [];
    let emitted = 0;

    v3Pair(out, 0, "SECTION");
    v3Pair(out, 2, "HEADER");
    v3Pair(out, 9, "$ACADVER");
    v3Pair(out, 1, "AC1015");
    v3Pair(out, 9, "$INSUNITS");
    v3Pair(out, 70, String(insunits));
    v3DxfComment(out, "CADCORE: written by the opencad web shell (browser-side export); "
      + "L1 sketch entities map to LINE/CIRCLE/ARC/LWPOLYLINE/POINT");
    const box = bbox();
    if (box) {
      v3Pair(out, 9, "$EXTMIN");
      v3Pair(out, 10, conv(box.minX));
      v3Pair(out, 20, conv(box.minY));
      v3Pair(out, 30, "0");
      v3Pair(out, 9, "$EXTMAX");
      v3Pair(out, 10, conv(box.maxX));
      v3Pair(out, 20, conv(box.maxY));
      v3Pair(out, 30, "0");
    }
    v3Pair(out, 0, "ENDSEC");

    const body = [];
    for (let i = 0; i < list.length; i += 1) {
      const entity = list[i];
      const kind = entity && entity.kind ? String(entity.kind).toLowerCase() : "";
      const layer = nonEmpty(entity && entity.layer) || DEFAULT_LAYER;
      const g = (entity && entity.geom) || {};
      v3UniqPush(layers, layer);
      if (kind === "line") {
        v3Pair(body, 0, "LINE");
        v3Pair(body, 8, layer);
        v3Pair(body, 10, conv(g.x1)); v3Pair(body, 20, conv(g.y1)); v3Pair(body, 30, "0");
        v3Pair(body, 11, conv(g.x2)); v3Pair(body, 21, conv(g.y2)); v3Pair(body, 31, "0");
        emitted += 1;
      } else if (kind === "circle") {
        v3Pair(body, 0, "CIRCLE");
        v3Pair(body, 8, layer);
        v3Pair(body, 10, conv(g.cx)); v3Pair(body, 20, conv(g.cy)); v3Pair(body, 30, "0");
        v3Pair(body, 40, conv(g.r));
        emitted += 1;
      } else if (kind === "arc") {
        v3Pair(body, 0, "ARC");
        v3Pair(body, 8, layer);
        v3Pair(body, 10, conv(g.cx)); v3Pair(body, 20, conv(g.cy)); v3Pair(body, 30, "0");
        v3Pair(body, 40, conv(g.r));
        v3Pair(body, 50, v3Num(num(g.start_deg) === null ? 0 : num(g.start_deg)));
        v3Pair(body, 51, v3Num(num(g.end_deg) === null ? 0 : num(g.end_deg)));
        emitted += 1;
      } else if (kind === "polyline") {
        const raw = Array.isArray(g.points) ? g.points : [];
        // L1 的"闭合"是**几何闭合**：cadcore.sketch.primitives.add_polyline 在 closed=True 时把首点
        // 原样补在末点（没有额外字段）。DXF 的规范形相反 —— 70 置 1 且不重复顶点，所以这里去重
        // 末点 + 置标志位；导回时 cadcore 会再把首点补回去，往返几何一致（见 61 往返判据）。
        const first = raw.length ? raw[0] : null;
        const last = raw.length ? raw[raw.length - 1] : null;
        const dup = raw.length >= 4 && first && last && first[0] === last[0] && first[1] === last[1];
        const closed = g.closed === true || dup;
        const pts = dup ? raw.slice(0, raw.length - 1) : raw;
        v3Pair(body, 0, "LWPOLYLINE");
        v3Pair(body, 8, layer);
        v3Pair(body, 90, String(pts.length));
        v3Pair(body, 70, closed ? "1" : "0");
        for (let k = 0; k < pts.length; k += 1) {
          const p = pts[k] || [];
          v3Pair(body, 10, conv(p[0]));
          v3Pair(body, 20, conv(p[1]));
        }
        emitted += 1;
      } else if (kind === "point") {
        v3Pair(body, 0, "POINT");
        v3Pair(body, 8, layer);
        v3Pair(body, 10, conv(num(g.x) === null ? g.x_nm : g.x));
        v3Pair(body, 20, conv(num(g.y) === null ? g.y_nm : g.y));
        v3Pair(body, 30, "0");
        emitted += 1;
      } else {
        // 不静默丢：数出来、在产物里留痕、在状态行上报（与 dxf_bridge 对未知 kind 不静默同一口径）。
        v3UniqPush(unsupported, kind || "unknown");
      }
      if (i % 200 === 199) {
        // 让步点同时是取消检查点：导出是本地构建，只有这里能中断（x2-v10 F9）
        if (V3.cancel) return null;
        v3Progress.step(i + 1);
        await v3Yield();
      }
    }

    v3Pair(out, 0, "SECTION");
    v3Pair(out, 2, "TABLES");
    v3Pair(out, 0, "TABLE");
    v3Pair(out, 2, "LTYPE");
    v3Pair(out, 70, "1");
    v3Pair(out, 0, "LTYPE");
    v3Pair(out, 2, "CONTINUOUS");
    v3Pair(out, 70, "0");
    v3Pair(out, 3, "Solid line");
    v3Pair(out, 72, "65");
    v3Pair(out, 73, "0");
    v3Pair(out, 40, "0");
    v3Pair(out, 0, "ENDTAB");
    v3Pair(out, 0, "TABLE");
    v3Pair(out, 2, "LAYER");
    v3Pair(out, 70, String(layers.length));
    for (let i = 0; i < layers.length; i += 1) {
      v3Pair(out, 0, "LAYER");
      v3Pair(out, 2, layers[i]);
      v3Pair(out, 70, "0");
      v3Pair(out, 62, "7");
      v3Pair(out, 6, "CONTINUOUS");
    }
    v3Pair(out, 0, "ENDTAB");
    v3Pair(out, 0, "ENDSEC");

    v3Pair(out, 0, "SECTION");
    v3Pair(out, 2, "ENTITIES");
    for (let i = 0; i < body.length; i += 1) out.push(body[i]);
    v3Pair(out, 0, "ENDSEC");

    // 草图尺寸与文档标注：草图没有图纸布局 ⇒ 不编坐标，按 999 注释留痕（与 cadcore
    // dxf_bridge.sketch_to_dxf 同一条口径：编一个坐标就是伪造几何）。
    if (dims.length) {
      v3DxfComment(out, "CADCORE: skipped " + dims.length + " sketch dimension(s) -- they need a "
        + "sheet layout (use cadcore.io.dxf_bridge.sheet_to_dxf)");
    }
    let annotationTexts = 0;
    for (let i = 0; i < annotations.length; i += 1) {
      const item = annotations[i] || {};
      const args = item.args && typeof item.args === "object" ? item.args : {};
      const literal = nonEmpty(item.text) || nonEmpty(args.text);
      const value = literal || nonEmpty(item.text_key);
      if (!value) continue;
      const uuidText = nonEmpty(item.uuid) || "";
      v3DxfComment(out, "CADCORE: annotation " + (uuidText ? uuidText + " " : "") + "text: " + value);
      annotationTexts += 1;
    }
    for (let i = 0; i < unsupported.length; i += 1) {
      v3DxfComment(out, "CADCORE: unsupported sketch entity kind in web export: " + unsupported[i]);
    }
    v3Pair(out, 0, "EOF");

    const text = out.join("\n") + "\n";
    return {
      text: text,
      bytes: text.length,
      entities: emitted,
      layers: layers.length,
      unsupported: unsupported.length,
      unsupportedKinds: unsupported,
      dimensionsAsComment: dims.length,
      annotationsAsComment: annotationTexts,
      unit: unit,
      insunits: insunits,
      name: "opencad-" + v3Stamp() + ".dxf"
    };
  }

  function v3DownloadText(name, text, mime) {
    const blob = new Blob([text], { type: mime || "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.rel = "noopener";
    anchor.setAttribute("data-opencad-download", "1");
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    setTimeout(function () {
      try { URL.revokeObjectURL(url); } catch (err) { /* 已释放 */ }
      if (anchor.parentNode) anchor.parentNode.removeChild(anchor);
    }, 4000);
    return blob.size;
  }

  async function v3ExportDxfDownload() {
    const total = entityList().length;
    // x2-v10 F9：导出原来没给 cancelable ⇒ 永远没有取消按钮（大图只能干等）
    v3Progress.begin(v3Text("cmd.export.dxf"), total, true, function () {
      const btn = document.getElementById("opencad-progress-cancel");
      if (btn) { btn.disabled = true; btn.textContent = v3Text("help.cancelling"); }
    });
    try {
      const built = await v3BuildDxf();
      if (!built) {
        // 用户在让步点按了取消（见 v3BuildDxf 的 V3.cancel 检查）：取消 ≠ 失败，也不留假成功
        v3Progress.finish("");
        setMessage("", "");
        return null;
      }
      v3Progress.step(total);
      const written = v3DownloadText(built.name, built.text, "application/dxf");
      v3Progress.finish(String(total) + "/" + String(total));
      const parts = [v3Text("cmd.export.dxf"), built.name, String(written) + "B",
        "entities=" + String(built.entities), "layers=" + String(built.layers)];
      if (built.unsupported) parts.push("unsupported=" + String(built.unsupported));
      setMessage("ok", parts.join(" "));
      return built;
    } catch (err) {
      v3Progress.finish("");
      v3ReportError("err.export_failed", { format: "DXF", detail: (err && err.message) || String(err) }, err);
      return null;
    }
  }

  /* ---------------------------------------------------------------- DXF 读侧（真字节 → 实体计划） */

  function v3DxfTags(rawText) {
    const raw = String(rawText || "").replace(/^\uFEFF/, "");
    if (/^AutoCAD Binary DXF/i.test(raw.slice(0, 64))) return { ok: false, error: "binary_dxf" };
    const lines = raw.split(/\r\n|\r|\n/);
    while (lines.length && String(lines[lines.length - 1]).trim() === "") lines.pop();
    if (lines.length % 2 !== 0) return { ok: false, error: "odd_line_count", line: lines.length };
    const tags = [];
    for (let i = 0; i < lines.length; i += 2) {
      const codeText = String(lines[i]).trim();
      const code = Number(codeText);
      if (!codeText || !isFinite(code)) {
        return { ok: false, error: "bad_group_code", line: i + 1, value: codeText.slice(0, 24) };
      }
      tags.push({ code: code, value: String(lines[i + 1] === undefined ? "" : lines[i + 1]).trim() });
    }
    return { ok: true, tags: tags };
  }

  function v3DxfScan(tags) {
    const scan = { version: "", insunits: NOT_MEASURED, records: [] };
    let section = "";
    let headerVar = "";
    let current = null;
    for (let i = 0; i < tags.length; i += 1) {
      const tag = tags[i];
      if (tag.code === 0) {
        const name = String(tag.value || "").toUpperCase();
        if (name === "SECTION") { section = ""; current = null; continue; }
        if (name === "ENDSEC") { section = ""; current = null; continue; }
        if (section === "ENTITIES") { current = { type: name, tags: [] }; scan.records.push(current); continue; }
        current = null;
        continue;
      }
      if (!section) {
        if (tag.code === 2) section = String(tag.value || "").toUpperCase();
        continue;
      }
      if (section === "HEADER") {
        if (tag.code === 9) headerVar = String(tag.value || "").toUpperCase();
        else if (headerVar === "$ACADVER" && tag.code === 1) scan.version = String(tag.value || "");
        else if (headerVar === "$INSUNITS" && tag.code === 70) {
          const code = Number(tag.value);
          if (isFinite(code)) scan.insunits = code;
        }
        continue;
      }
      if (section === "ENTITIES" && current) current.tags.push(tag);
    }
    return scan;
  }

  function v3TagValue(tags, code) {
    for (let i = 0; i < tags.length; i += 1) if (tags[i].code === code) return tags[i].value;
    return null;
  }

  function v3TagNumber(tags, code) {
    const raw = v3TagValue(tags, code);
    if (raw === null) return null;
    const value = Number(raw);
    return isFinite(value) ? value : null;
  }

  function v3LwPoints(tags) {
    const points = [];
    let pendingX = null;
    for (let i = 0; i < tags.length; i += 1) {
      if (tags[i].code === 10) {
        const x = Number(tags[i].value);
        pendingX = isFinite(x) ? x : null;
      } else if (tags[i].code === 20 && pendingX !== null) {
        const y = Number(tags[i].value);
        if (isFinite(y)) points.push([pendingX, y]);
        pendingX = null;
      }
    }
    return points;
  }

  function v3DxfPlan(scan) {
    const records = scan.records;
    const plan = { commands: [], skipped: {}, layers: [], imported: 0 };
    const skip = function (type, reason) {
      const key = String(type || "unknown") + "/" + String(reason || "unsupported");
      plan.skipped[key] = (plan.skipped[key] || 0) + 1;
    };
    const insunits = scan.insunits === NOT_MEASURED ? 0 : scan.insunits;
    const perUnit = V3_INSUNITS_NM[insunits] || NM_PER_MM;
    const toNmb = function (value) { return Math.round(value * perUnit); };
    for (let i = 0; i < records.length; i += 1) {
      const rec = records[i];
      const type = String(rec.type || "").toUpperCase();
      if (type === "SEQEND" || type === "VERTEX") continue;   // 跟随 POLYLINE 的子记录
      const layer = nonEmpty(v3TagValue(rec.tags, 8)) || DEFAULT_LAYER;
      v3UniqPush(plan.layers, layer);
      if (type === "LINE") {
        const x1 = v3TagNumber(rec.tags, 10), y1 = v3TagNumber(rec.tags, 20);
        const x2 = v3TagNumber(rec.tags, 11), y2 = v3TagNumber(rec.tags, 21);
        if (x1 === null || y1 === null || x2 === null || y2 === null) { skip(type, "incomplete_geometry"); continue; }
        plan.commands.push({ command: "add_line", type: type,
          args: { p1: [toNmb(x1), toNmb(y1)], p2: [toNmb(x2), toNmb(y2)], layer: layer } });
      } else if (type === "CIRCLE") {
        const cx = v3TagNumber(rec.tags, 10), cy = v3TagNumber(rec.tags, 20), r = v3TagNumber(rec.tags, 40);
        if (cx === null || cy === null || r === null || r <= 0) { skip(type, "incomplete_geometry"); continue; }
        plan.commands.push({ command: "add_circle", type: type,
          args: { center: [toNmb(cx), toNmb(cy)], radius_nm: toNmb(r), layer: layer } });
      } else if (type === "ARC") {
        const cx = v3TagNumber(rec.tags, 10), cy = v3TagNumber(rec.tags, 20), r = v3TagNumber(rec.tags, 40);
        const a0 = v3TagNumber(rec.tags, 50), a1 = v3TagNumber(rec.tags, 51);
        if (cx === null || cy === null || r === null || r <= 0 || a0 === null || a1 === null) {
          skip(type, "incomplete_geometry");
          continue;
        }
        plan.commands.push({ command: "add_arc", type: type,
          args: { center: [toNmb(cx), toNmb(cy)], radius_nm: toNmb(r),
                  start_deg: a0, end_deg: a1, layer: layer } });
      } else if (type === "LWPOLYLINE") {
        const points = v3LwPoints(rec.tags);
        if (points.length < 2) { skip(type, "incomplete_geometry"); continue; }
        const flags = v3TagNumber(rec.tags, 70);
        plan.commands.push({ command: "add_polyline", type: type,
          args: { points_nm: points.map(function (p) { return [toNmb(p[0]), toNmb(p[1])]; }),
                  closed: !!(flags && (flags & 1)), layer: layer } });
      } else if (type === "POLYLINE") {
        const points = [];
        let closed = false;
        const flags = v3TagNumber(rec.tags, 70);
        if (flags && (flags & 1)) closed = true;
        let j = i + 1;
        for (; j < records.length; j += 1) {
          const sub = String(records[j].type || "").toUpperCase();
          if (sub === "SEQEND") break;
          if (sub !== "VERTEX") break;
          const vx = v3TagNumber(records[j].tags, 10), vy = v3TagNumber(records[j].tags, 20);
          if (vx !== null && vy !== null) points.push([vx, vy]);
        }
        if (points.length < 2) { skip(type, "incomplete_geometry"); continue; }
        plan.commands.push({ command: "add_polyline", type: type,
          args: { points_nm: points.map(function (p) { return [toNmb(p[0]), toNmb(p[1])]; }),
                  closed: closed, layer: layer } });
      } else if (type === "POINT") {
        const x = v3TagNumber(rec.tags, 10), y = v3TagNumber(rec.tags, 20);
        if (x === null || y === null) { skip(type, "incomplete_geometry"); continue; }
        plan.commands.push({ command: "add_point", type: type,
          args: { position: [toNmb(x), toNmb(y)], layer: layer } });
      } else {
        skip(type, "unsupported_type");
      }
    }
    plan.imported = plan.commands.length;
    plan.unitAssumed = scan.insunits === NOT_MEASURED;
    plan.nmPerUnit = perUnit;
    return plan;
  }

  function v3SkipSummary(skipped) {
    const keys = Object.keys(skipped);
    if (!keys.length) return "";
    keys.sort(function (a, b) { return skipped[b] - skipped[a]; });
    return keys.map(function (k) { return k + "=" + String(skipped[k]); }).join(" ");
  }

  async function v3ImportDxfText(fileName, rawText) {
    if (V3.importing) {
      v3ReportError("err.import_failed", { format: "DXF", detail: "import_already_running" });
      return null;
    }
    const tags = v3DxfTags(rawText);
    if (!tags.ok) {
      v3ReportError("err.import_failed", { format: "DXF", detail: tags.error });
      return null;
    }
    const scan = v3DxfScan(tags.tags);
    const versionText = nonEmpty(scan.version);
    if (versionText && !/^AC10(09|12|14|15|18|21|24|27|32)$/.test(versionText)) {
      const err = new Error("dxf_version_unsupported");
      err.code = "dxf_version_unsupported";
      err.extra = { version: versionText };
      v3ReportError("err.dxf_version_unsupported", { version: versionText }, err);
      return null;
    }
    const plan = v3DxfPlan(scan);
    let commands = plan.commands;
    let truncated = 0;
    if (commands.length > V3.maxImportEntities) {
      truncated = commands.length - V3.maxImportEntities;
      commands = commands.slice(0, V3.maxImportEntities);
    }
    const total = commands.length;
    V3.importing = true;
    let cancelled = false;
    // x2-v10 F4：这个第 4 参原来没给 ⇒ cancelHook 恒为 null，点了取消界面毫无变化、
    // 也断不掉在飞的那个请求。这里给回调：立刻反馈 + abort（x2-v10 F5）。
    v3Progress.begin(v3Text("cmd.import.dxf"), total, true, function () {
      const btn = document.getElementById("opencad-progress-cancel");
      if (btn) { btn.disabled = true; btn.textContent = v3Text("help.cancelling"); }
      v3AbortInflight("cancel");
    });
    let ok = 0, failed = 0, firstError = null, timedOut = false;
    try {
      for (let i = 0; i < total; i += 1) {
        if (V3.cancel) { cancelled = true; truncated += total - i; break; }
        try {
          await postJSON(API.command, { command: commands[i].command, args: commands[i].args });
          ok += 1;
        } catch (err) {
          // 用户按了取消 ⇒ 这一条不是"失败"（否则取消会弹一条报错横幅）
          if (V3.cancel) { cancelled = true; break; }
          failed += 1;
          if (!firstError) firstError = err;
          // 超时 = 链路已经不通：再逐条打下去只是在等第二个 15 s
          if (err && err.code === "timeout") { timedOut = true; break; }
        }
        v3Progress.step(i + 1);
        if (i % 100 === 99) await v3Yield();
      }
      if (!timedOut) await refreshState();
    } catch (err) {
      v3Progress.finish("");
      v3ReportError("err.import_failed", { format: "DXF", detail: (err && err.message) || String(err) }, err);
      V3.importing = false;
      return null;
    }
    V3.importing = false;
    if (timedOut) {
      // 超时不是"取消"也不是"成功"：显式走失败通道，不静默算进 ok=（三态不许同形）
      v3Progress.finish("");
      v3ReportError("err.import_failed", { format: "DXF", detail: "timeout" });
      return null;
    }
    v3Progress.finish(String(ok) + "/" + String(total));
    const skippedCount = Object.keys(plan.skipped).reduce(function (sum, key) {
      return sum + plan.skipped[key];
    }, 0);
    const parts = [v3Text("cmd.import.dxf"), nonEmpty(fileName) || "dxf",
      "ok=" + String(ok), "skipped=" + String(skippedCount), "failed=" + String(failed),
      "layers=" + String(plan.layers.length),
      "version=" + (versionText || "NA"),
      // 没有 $INSUNITS 的 DXF 是"无单位"：本段按 mm 解读（与 L1 的 unit 缺省一致），但**必须写明这是假设**,
      // 不能只写个 NA 让人以为"单位已按 1:1 处理"。三态：0=真为零 / -1=未采 / NA=不适用+假设要写出来。
      "units=" + (scan.insunits === NOT_MEASURED ? "NA(assumed_mm)" : String(scan.insunits))];
    if (truncated) parts.push("truncated=" + String(truncated));
    if (cancelled) parts.push("cancelled=1");
    const skipText = v3SkipSummary(plan.skipped);
    if (skipText) parts.push("skip:" + skipText);
    const summary = parts.join(" ");
    if (failed > 0) {
      setMessage("error", v3Text("err.import_failed", {
        format: "DXF", detail: (firstError && (firstError.detail || firstError.message)) || String(failed)
      }));
      v3AlertShow(summary, v3Hint(firstError));
    } else {
      setMessage("ok", summary);
    }
    return { plan: plan, ok: ok, failed: failed, skipped: skippedCount, truncated: truncated,
             cancelled: cancelled, version: versionText || "NA", summary: summary };
  }

  /* ---------------------------------------------------------------- 快捷键 / 帮助面板 */

  const V3_SHORTCUTS = [
    { keys: "Ctrl+S", key: "cmd.export.dxf" },
    { keys: "Ctrl+O", key: "cmd.import.dxf" },
    { keys: "Ctrl+Z", key: "cmd.undo" },
    { keys: "Ctrl+Shift+Z", key: "cmd.redo" },
    { keys: "F", key: "cmd.zoom_fit" },
    { keys: "F1", key: "help.shortcuts" },
    { keys: "Del", key: "cmd.delete" },
    { keys: "Esc", key: "help.cancel" },
    { keys: "ArrowLeft/ArrowRight", key: "help.toolbar" }
  ];

  function v3IsEditable(node) {
    if (!node || !node.tagName) return false;
    const tag = String(node.tagName).toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return node.isContentEditable === true;
  }

  function v3ToolbarButtons() {
    const list = [];
    const nodes = document.querySelectorAll("#toolbar [data-tool]");
    for (let i = 0; i < nodes.length; i += 1) {
      if (!nodes[i].disabled) list.push(nodes[i]);
    }
    return list;
  }

  function v3ToolbarRoving() {
    const list = v3ToolbarButtons();
    for (let i = 0; i < list.length; i += 1) {
      list[i].setAttribute("tabindex", i === 0 ? "0" : "-1");
    }
    return list.length;
  }

  function v3ToolbarMove(delta, absolute) {
    const list = v3ToolbarButtons();
    if (!list.length) return false;
    const active = document.activeElement;
    let index = list.indexOf(active);
    if (index < 0) return false;
    let next = index;
    if (absolute === "home") next = 0;
    else if (absolute === "end") next = list.length - 1;
    else next = (index + delta + list.length) % list.length;
    list[index].setAttribute("tabindex", "-1");
    list[next].setAttribute("tabindex", "0");
    list[next].focus();
    return true;
  }

  function v3HelpEnsure() {
    if (V3.help && V3.help.box && V3.help.box.parentNode) return V3.help;
    const box = document.createElement("div");
    box.id = "opencad-help";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "opencad-help-title");
    box.style.cssText = "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);"
      + "z-index:2147483002;min-width:320px;max-width:min(560px,92vw);max-height:80vh;overflow:auto;"
      + "padding:14px 16px;border:1px solid rgba(127,127,127,.5);border-radius:8px;"
      + "background:#1d1d22;color:#f2f2f4;font:13px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;"
      + "box-shadow:0 8px 30px rgba(0,0,0,.4);";
    const title = document.createElement("h2");
    title.id = "opencad-help-title";
    title.style.cssText = "margin:0 0 8px;font-size:14px;";
    title.textContent = v3Text("help.shortcuts");
    const list = document.createElement("dl");
    list.style.cssText = "display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0 0 10px;";
    for (let i = 0; i < V3_SHORTCUTS.length; i += 1) {
      const dt = document.createElement("dt");
      dt.style.cssText = "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;opacity:.95;";
      dt.textContent = V3_SHORTCUTS[i].keys;
      const dd = document.createElement("dd");
      dd.style.cssText = "margin:0;";
      dd.textContent = v3Text(V3_SHORTCUTS[i].key);
      list.appendChild(dt);
      list.appendChild(dd);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.id = "opencad-help-close";
    close.style.cssText = "padding:4px 12px;font:inherit;cursor:pointer;border-radius:4px;"
      + "border:1px solid rgba(255,255,255,.4);background:transparent;color:inherit;";
    close.textContent = v3Text("help.close");
    close.addEventListener("click", function () { v3HelpClose(); });
    box.appendChild(title);
    box.appendChild(list);
    box.appendChild(close);
    document.body.appendChild(box);
    box.addEventListener("keydown", function (ev) {
      if (ev.key !== "Tab") return;
      const focusables = [close];
      const idx = focusables.indexOf(document.activeElement);
      ev.preventDefault();
      const next = ev.shiftKey ? (idx <= 0 ? focusables.length - 1 : idx - 1) : (idx + 1) % focusables.length;
      focusables[next].focus();
    });
    box.style.display = "none";
    V3.help = { box: box, close: close };
    return V3.help;
  }

  function v3HelpOpen() {
    const help = v3HelpEnsure();
    V3.lastFocus = document.activeElement;
    help.box.style.display = "block";
    V3.helpOpen = true;
    help.close.focus();
  }

  function v3HelpClose() {
    const help = v3HelpEnsure();
    help.box.style.display = "none";
    V3.helpOpen = false;
    const back = V3.lastFocus;
    V3.lastFocus = null;
    if (back && back.focus) back.focus();
  }

  function v3ZoomFit() {
    fitView();
    render();
  }

  async function v3UndoRedo(which) {
    try {
      const body = await postJSON(API.command, { command: which });
      await refreshState();
      const parts = [v3Text(which === "undo" ? "cmd.undo" : "cmd.redo")];
      if (typeof body.undo_depth === "number") parts.push("undo_depth=" + String(body.undo_depth));
      if (typeof body.redo_depth === "number") parts.push("redo_depth=" + String(body.redo_depth));
      setMessage("ok", parts.join(" "));
    } catch (err) {
      showError(err);
    }
  }

  function v3OnKeydown(ev) {
    const key = ev.key;
    const mod = ev.ctrlKey || ev.metaKey;
    if (key === "Escape" && V3.helpOpen) {
      ev.preventDefault();
      ev.stopPropagation();
      v3HelpClose();
      return;
    }
    if (key === "F1") {
      ev.preventDefault();
      ev.stopPropagation();
      if (V3.helpOpen) v3HelpClose(); else v3HelpOpen();
      return;
    }
    if (v3IsEditable(ev.target)) return;
    if (mod && (key === "s" || key === "S")) {
      ev.preventDefault();
      ev.stopPropagation();
      v3ExportDxfDownload();
      return;
    }
    if (mod && (key === "o" || key === "O")) {
      ev.preventDefault();
      ev.stopPropagation();
      v3PickFile();
      return;
    }
    if (mod && (key === "z" || key === "Z")) {
      ev.preventDefault();
      ev.stopPropagation();
      v3UndoRedo(ev.shiftKey ? "redo" : "undo");
      return;
    }
    if (mod && (key === "y" || key === "Y")) {
      ev.preventDefault();
      ev.stopPropagation();
      v3UndoRedo("redo");
      return;
    }
    if (!mod && !ev.altKey && (key === "f" || key === "F") && !ev.shiftKey) {
      ev.preventDefault();
      ev.stopPropagation();
      v3ZoomFit();
      return;
    }
    const inToolbar = !!(ev.target && ev.target.closest && ev.target.closest("#toolbar"));
    if (inToolbar && (key === "ArrowRight" || key === "ArrowLeft")) {
      ev.preventDefault();
      ev.stopPropagation();
      v3ToolbarMove(key === "ArrowRight" ? 1 : -1, null);
      return;
    }
    if (inToolbar && key === "Home") {
      ev.preventDefault();
      ev.stopPropagation();
      v3ToolbarMove(0, "home");
      return;
    }
    if (inToolbar && key === "End") {
      ev.preventDefault();
      ev.stopPropagation();
      v3ToolbarMove(0, "end");
    }
  }

  function v3InstallShortcuts() {
    const count = v3ToolbarRoving();
    const pairs = [["[data-action=\"export-dxf\"]", "Control+S"], ["[data-action=\"open\"]", "Control+O"]];
    for (let i = 0; i < pairs.length; i += 1) {
      const node = document.querySelector(pairs[i][0]);
      if (node) node.setAttribute("aria-keyshortcuts", pairs[i][1]);
    }
    return count;
  }

  /* ---------------------------------------------------------------- 取消 / 超时（AbortController） */

  // x2-v10 F5：改前全文件零 AbortController、零超时 ⇒ 请求挂住时 await 永远不返回，
  // 进度条永久停在原地，V3.cancel 也穿不透在飞的那个 fetch。
  const V3_TIMEOUT_MS = 15000;
  let v3AbortCtl = null;
  let v3AbortReason = "";

  function v3AbortInflight(reason) {
    const ctl = v3AbortCtl;
    if (!ctl) return false;
    v3AbortReason = reason || "cancel";
    try { ctl.abort(); } catch (err) { /* 已经结束 */ }
    return true;
  }

  /* ---------------------------------------------------------------- web 语言包兜底层 */

  // 取 web/web-i18n.json。相对路径：本机直跑解析成 /web/web-i18n.json，挂反代前缀时随文档
  // URL 解析（与 index.html 的相对引用同一口径）。best-effort：404/坏 JSON 只记成 "absent"，
  // 绝不拖垮 boot —— 服务端 strings 仍是主真源（resource.consumers[role=page] 声明的消费者）。
  const WEB_I18N_URLS = ["web/web-i18n.json", "web-i18n.json"];

  async function v3LoadWebI18n() {
    for (let i = 0; i < WEB_I18N_URLS.length; i += 1) {
      let body = null;
      try {
        const res = await fetch(WEB_I18N_URLS[i], { headers: { Accept: "application/json" } });
        if (!res.ok) continue;
        body = await res.json();
      } catch (err) {
        continue;
      }
      const cats = body && body.catalogs;
      if (!cats || typeof cats !== "object") continue;
      const merged = {};
      Object.keys(cats).forEach(function (code) {
        const strings = cats[code] && cats[code].strings;
        if (!strings || typeof strings !== "object") return;
        const keep = {};
        Object.keys(strings).forEach(function (key) {
          if (typeof strings[key] === "string" && strings[key]) keep[key] = strings[key];
        });
        merged[code] = keep;
      });
      if (!Object.keys(merged).length) continue;
      const chain = body && body.fallback && body.fallback.chain;
      WEB_FALLBACK_CHAIN = chain && typeof chain === "object" ? chain : {};
      WEB_STRINGS = merged;
      WEB_STRINGS_STATE = "ready";
      return { state: WEB_STRINGS_STATE, url: WEB_I18N_URLS[i], locales: Object.keys(merged) };
    }
    WEB_STRINGS_STATE = "absent";
    return { state: WEB_STRINGS_STATE, url: "", locales: [] };
  }

  /* ---------------------------------------------------------------- 包既有入口（四处，语义不变） */

  // 报错文案与 base api 逐字相同（调用方与既有测试依赖它）；这里只**补充**结构化字段
  // （status / code / detail / extra）供提示用 —— 服务端 ok:false 回执里的 error/detail/argument
  // 字段在 base api 里被丢掉了，没有它们就写不出可操作提示。
  api = async function (path, init) {
    // 每个请求挂一个 signal + 15 s 超时；取消或超时都翻成带 code 的错误交给 v3Hint()。
    const opts = {};
    if (init) Object.keys(init).forEach(function (k) { opts[k] = init[k]; });
    let ownCtl = null, timer = null;
    if (!opts.signal && typeof AbortController === "function") {
      ownCtl = new AbortController();
      opts.signal = ownCtl.signal;
      v3AbortCtl = ownCtl;
      v3AbortReason = "";
      timer = setTimeout(function () {
        if (v3AbortCtl === ownCtl) v3AbortReason = "timeout";
        try { ownCtl.abort(); } catch (err) { /* 已结束 */ }
      }, V3_TIMEOUT_MS);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      const reason = v3AbortReason || (err && err.name === "AbortError" ? "timeout" : "");
      if (reason) {
        const aborted = new Error(path + " -> " + reason);
        aborted.status = 0;
        aborted.path = path;
        aborted.code = reason;
        aborted.detail = "";
        aborted.extra = {};
        throw aborted;
      }
      throw err;
    } finally {
      if (timer) clearTimeout(timer);
      if (ownCtl && v3AbortCtl === ownCtl) { v3AbortCtl = null; v3AbortReason = ""; }
    }
    let body = null;
    try {
      body = await res.json();
    } catch (err) {
      const bad = new Error(path + " -> " + res.status + " (body is not JSON)");
      bad.status = res.status;
      bad.path = path;
      bad.code = "bad_json";
      throw bad;
    }
    if (!res.ok) {
      const http = new Error(path + " -> " + res.status);
      http.status = res.status;
      http.path = path;
      http.code = nonEmpty(body && body.error) || "http_" + res.status;
      http.detail = nonEmpty(body && body.detail) || "";
      http.extra = body || {};
      throw http;
    }
    if (body && body.ok === false) {
      const notOk = new Error(nonEmpty(body.error) || nonEmpty(body.detail) || path + " -> ok=false");
      notOk.status = res.status;
      notOk.path = path;
      notOk.code = nonEmpty(body.error) || "not_ok";
      notOk.detail = nonEmpty(body.detail) || "";
      notOk.extra = body;
      throw notOk;
    }
    return body || {};
  };

  const v3ShowErrorBase = showError;
  showError = function (err) {
    v3ShowErrorBase(err);                                  // 状态行 + data-message-kind=error（既有契约）
    v3AlertShow(label("err.generic", { message: (err && err.message) || String(err) }), v3Hint(err));
  };

  const v3OnActionBase = onAction;
  onAction = async function (action) {
    if (action === "export-dxf") {
      await v3ExportDxfDownload();
      return;
    }
    if (action === "open") {
      // 线上服务的用户拿不到服务端 build/ 目录 ⇒ "打开"改成打开**用户自己的** DXF 文件，
      // 由浏览器读文本、逐条建成草图实体（几何规则仍在 cadcore）。
      v3PickFile();
      return;
    }
    return v3OnActionBase(action);
  };

  const v3BootBase = boot;
  boot = async function () {
    // 与首屏并行取 web 语言包；不阻塞 boot（缺件时服务端 strings 照旧工作）
    const webI18n = v3LoadWebI18n();
    try {
      await v3BootBase();
    } finally {
      try {
        v3InstallShortcuts();
        v3BindDropZone();
        document.addEventListener("keydown", v3OnKeydown, true);
        V3.ready = true;
        document.documentElement.setAttribute("data-opencad-v3", "ready");
      } catch (err) {
        V3.ready = false;
      }
    }
    try {
      V3.webI18n = await webI18n;
      if (V3.ready) renderAll();     // 兜底层到齐后重刷一次（缺键哨兵要重算）
    } catch (err) {
      V3.webI18n = { state: "absent", url: "", locales: [] };
    }
  };

  // 体检/自检入口：只读句柄，不给 UI 加任何可见物（判定脚本读它，别去猜 DOM 时序）。
  window.__opencadV3 = {
    version: "v3",
    ready: function () { return V3.ready === true; },
    // 忙信号：判定脚本用它等一次导入真结束（不许靠 sleep 猜 —— 猜出来的绿是假绿）
    importing: function () { return V3.importing === true; },
    exportDxf: v3ExportDxfDownload,
    importDxf: function (name, contentText) { return v3ImportDxfText(name, contentText); },
    importFile: v3ImportFile,
    pickFile: v3PickFile,
    help: { open: v3HelpOpen, close: v3HelpClose, isOpen: function () { return V3.helpOpen === true; } },
    progress: { begin: v3Progress.begin, step: v3Progress.step, finish: v3Progress.finish, node: v3Progress.node },
    shortcuts: V3_SHORTCUTS,
    derivedKeys: function () { return Object.keys(V3.derivedKeys); },
    // 兜底层的三态读数（unloaded/ready/absent 不许同形）：判定脚本用它区分"缺件"与"没加载"
    webI18n: function () {
      return { state: WEB_STRINGS_STATE, locales: Object.keys(WEB_STRINGS),
               url: (V3.webI18n && V3.webI18n.url) || "" };
    },
    alert: function () { return V3.alert ? V3.alert.box : null; }
  };
  /* ==== opencad-web-v3:END ==== */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
