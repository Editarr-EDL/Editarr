/* Hash routes: #/model/<id>?cat&outcome&folder&q&sort&page&item and #/folder/<path>?q&sub&page&item. */
import { encPath } from "./ui.js";

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const qi = raw.indexOf("?");
  const pathPart = qi >= 0 ? raw.slice(0, qi) : raw;
  const params = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : "");
  const [view, ...rest] = pathPart.split("/");
  const arg = rest.filter((s) => s !== "").map(decodeURIComponent).join("/");
  return { view: view || "model", arg, params };
}

/** Build a route hash; the current ?scope is carried over unless params sets it. */
export function buildHash(view, arg, params) {
  const qs = new URLSearchParams();
  const scope = parseHash().params.get("scope");
  if (scope && !("scope" in params)) qs.set("scope", scope);
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "") qs.set(k, v);
  const s = qs.toString();
  return `#/${view}/${arg ? encPath(arg) : ""}${s ? "?" + s : ""}`;
}

/** Current hash with some params changed. */
export function hashWith(changes) {
  const r = parseHash();
  return buildHash(r.view, r.arg, Object.assign(Object.fromEntries(r.params), changes));
}

/** Navigate to the current route with changed params; replace=true avoids a history entry (typing in filters). */
export function withParams(changes, replace) {
  const hash = hashWith(changes);
  if (!replace) { location.hash = hash; return; }
  history.replaceState(null, "", hash);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

/** Short name of the routed page, e.g. "Model siglip2" or "Folder video/ucf_crime". */
export function routeName(r) {
  return r.view === "folder" ? `Folder ${r.arg || "(all)"}` : `Model ${r.arg}`.trim();
}

let lastPage = null;

/**
 * After a route renders: set document.title and, when the page itself changed (view, model or folder — not just
 * filters, paging or the detail dialog), announce it and move focus to #main. The first render keeps the
 * browser's initial focus. Returns whether focus moved.
 */
export function announceRoute(r) {
  document.title = `${routeName(r)} · Folder evaluation`;
  const page = `${r.view}:${r.arg}`;
  const changed = lastPage !== null && page !== lastPage;
  lastPage = page;
  if (!changed) return false;
  const status = document.querySelector("#route-status");
  if (status) status.textContent = routeName(r);
  if (r.params.get("item")) return false;
  document.querySelector("#main")?.focus();
  return true;
}
