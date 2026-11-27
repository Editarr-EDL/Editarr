/* Generic DOM and formatting helpers shared by every view. */

export const PAGE_SIZE = 100;

/** Create an element: attrs support class, text, on<event> handlers; children may be nested arrays. */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

export const $ = (sel) => document.querySelector(sel);
/** Replace an element's children, flattening arrays and skipping null/false. */
export const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
export const encPath = (p) => p.split("/").map(encodeURIComponent).join("/");
export const fmt = (v, d = 2) => (typeof v === "number" && isFinite(v) ? v.toFixed(d) : "–");
export const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "–");

export function setStatus(text) { $("#status").textContent = text || ""; }

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function chip(text, cls, title) { return h("span", { class: `chip ${cls || ""}`, title }, text); }

/** Expected-label chips; reviewer corrections show as ✎ (marked present) or struck through (marked absent). */
export function expectedChips(item) {
  const exp = item.expected || [], corr = item.corr || {};
  const removed = Object.keys(corr).filter((k) => corr[k] === false);
  return h("span", { class: "chips" },
    exp.map((k) => (corr[k] === true ? chip(`${k} ✎`, "exp corrected", "Marked present by a reviewer (applies to all models)") : chip(k, "exp"))),
    removed.map((k) => chip(k, "exp removed", "Marked absent by a reviewer (applies to all models)")),
    exp.length ? null : chip("clean", "exp clean"),
    item.scored ? null : chip("unscored", "unscored", "Excluded from metrics"));
}

/** Seconds as m:ss (or h:mm:ss). */
export function fmtTime(t) {
  const s = Math.max(0, Math.floor(t || 0));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

export const fmtWindow = (w) => (Array.isArray(w) && w.length === 2 ? `${fmtTime(w[0])}–${fmtTime(w[1])}` : "");

/** Film-window time and source tags (e.g. violence/graphic) for an item, if present. */
export function itemExtras(item) {
  const win = fmtWindow(item.window);
  const tags = item.source_tags || [];
  if (!win && !tags.length) return null;
  return h("span", { class: "chips extras" },
    win ? h("span", { class: "window", title: "Film window", text: `⏱ ${win}` }) : null,
    tags.map((t) => chip(t, "src", "Source tag")));
}

export function field(label, control) { return h("label", { class: "field" }, h("span", { text: label }), control); }

export function select(options, value, onchange, extra) {
  return h("select", { onchange, ...extra }, options.map(([v, l]) => {
    const o = h("option", { value: v, text: l });
    if (v === value) o.selected = true;
    return o;
  }));
}

/** Update a filter control from the URL unless the user is typing in it. */
export function syncControl(id, value) {
  const el = document.getElementById(id);
  if (el && document.activeElement !== el && el.value !== value) el.value = value;
}

/** Page navigation links; onHref(page) returns the hash for that page. */
export function pager(total, page, onHref) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pages <= 1) return h("nav", { class: "pager", "aria-label": "Pages" }, h("span", { text: `${total} items` }));
  const link = (p, label) => p < 1 || p > pages || p === page
    ? h("span", { class: "btn disabled", "aria-disabled": "true", text: label })
    : h("a", { class: "btn", href: onHref(p), text: label });
  return h("nav", { class: "pager", "aria-label": "Pages" },
    link(1, "« First"), link(page - 1, "‹ Prev"),
    h("span", { text: `Page ${page} of ${pages} · ${total} items` }),
    link(page + 1, "Next ›"), link(pages, "Last »"));
}
