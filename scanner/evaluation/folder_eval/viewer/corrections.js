/* Ground-truth corrections: effective expected labels and server sync (serialized POSTs, optimistic display). */
import { S, fetchJSON } from "./data.js";
import { $ } from "./ui.js";
import { familyOf } from "./scoring.js";

const API = "../_api/";
const POLL_MS = 3000;
const MAX_POLLS = 40;

/** mode: "edit" (API writable), "readonly" (corrections.json only) or "none". */
export const C = {
  mode: "none", status: null, lastSummaryAt: null, docUpdatedAt: null,
  confirmed: {}, // item id → corrections map last confirmed by the server
  edits: {}, // item id → [{key, value}] not yet confirmed, applied on top of `confirmed` for display
  note: "", openFamilies: new Set(),
};
let pollTimer = null, pollsLeft = 0, toastTimer = null;

export const canEdit = () => C.mode === "edit";
export const isCorrected = (item, key) => !!item.corr && key in item.corr;
export const correctionCount = () => Object.values(S.corrections).reduce((n, m) => n + Object.keys(m).length, 0);

/** Recompute item.expected/item.scored from the inventory values plus this item's corrections (contract rules). */
export function applyCorrections(item) {
  if (!("inv_expected" in item)) { item.inv_expected = item.expected || []; item.inv_scored = item.scored; }
  const corr = S.corrections[item.id];
  item.corr = corr && Object.keys(corr).length ? corr : null;
  if (!item.corr) { item.expected = item.inv_expected; item.scored = item.inv_scored; return; }
  const exp = new Set(item.inv_expected);
  for (const [k, v] of Object.entries(item.corr)) {
    if (v) { exp.add(k); exp.add(familyOf(k)); continue; }
    exp.delete(k);
    if (!k.includes("/")) for (const e of [...exp]) if (e.startsWith(k + "/")) exp.delete(e);
  }
  item.expected = [...exp];
  item.scored = true;
}

/** Same normalisation as the server: family false drops token trues; token true drops a family false. */
function normalise(map, key, value) {
  const next = { ...map };
  if (value === null) delete next[key];
  else next[key] = value;
  if (value === false && !key.includes("/")) for (const k of Object.keys(next)) if (k.startsWith(key + "/") && next[k] === true) delete next[k];
  if (value === true && key.includes("/") && next[familyOf(key)] === false) delete next[familyOf(key)];
  return next;
}

const emit = () => window.dispatchEvent(new Event("evaldata"));
const busy = () => Object.keys(C.edits).length > 0;
let chain = Promise.resolve();

/** Display map for an item: last server-confirmed corrections with still-pending edits applied in order. */
function displayMap(id) {
  let m = C.confirmed[id] || {};
  for (const e of C.edits[id] || []) m = normalise(m, e.key, e.value);
  return m;
}

function refreshItem(id) {
  const item = S.byId.get(id);
  if (!item) return;
  const m = displayMap(id);
  if (Object.keys(m).length) S.corrections[id] = m;
  else delete S.corrections[id];
  applyCorrections(item);
}

/** True while summary.json was computed from an older corrections document than the current one. */
export function summaryStale() {
  if (C.mode === "none") return false;
  return (S.summary?.corrections?.updated_at ?? null) !== (C.docUpdatedAt ?? null) || busy();
}

function toast(text) {
  const el = $("#toast");
  const dlg = $("#detail");
  (dlg.open ? dlg : document.body).append(el); // a modal dialog's top layer would hide a body-level toast
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 8000);
}

export function updateHeader() {
  const n = correctionCount();
  const el = $("#corr-status");
  el.hidden = C.mode === "none" && !n;
  el.textContent = `${n} correction${n === 1 ? "" : "s"}${C.mode === "readonly" ? " (read-only)" : ""}`;
  const st = $("#summary-status");
  st.hidden = !(canEdit() && (C.status?.summarizing || summaryStale()));
  st.textContent = busy() ? "Saving…" : "Summary updating…";
}

/** Detect the API (edit mode) or fall back to a static corrections.json (read-only), then apply corrections. */
export async function loadCorrections() {
  let status = null;
  try {
    const res = await fetch(API + "status", { cache: "no-cache" });
    if (res.ok && (res.headers.get("content-type") || "").includes("json")) status = await res.json();
  } catch { /* plain static server */ }
  let doc;
  if (status && status.writable) {
    C.mode = "edit";
    C.status = status;
    C.lastSummaryAt = status.summary_updated_at ?? null;
    doc = await fetchJSON(API + "corrections", true);
  } else {
    doc = await fetchJSON("corrections.json", true);
    C.mode = doc ? "readonly" : "none";
  }
  C.confirmed = (doc && doc.items) || {};
  C.docUpdatedAt = (doc && doc.updated_at) ?? null;
  S.corrections = {};
  for (const id of Object.keys(C.confirmed)) {
    const m = displayMap(id);
    if (Object.keys(m).length) S.corrections[id] = m;
  }
  for (const item of S.items) applyCorrections(item);
  if (canEdit() && (status.summarizing || summaryStale())) startPolling();
  updateHeader();
}

/** Re-read the authoritative corrections document and re-derive every item's display map. */
async function resync() {
  const doc = await fetchJSON(API + "corrections", true);
  if (!doc) return;
  const ids = new Set([...Object.keys(C.confirmed), ...Object.keys(doc.items || {})]);
  C.confirmed = doc.items || {};
  C.docUpdatedAt = doc.updated_at ?? null;
  for (const id of ids) refreshItem(id);
}

function startPolling() {
  pollsLeft = MAX_POLLS;
  if (!pollTimer) pollTimer = setTimeout(poll, POLL_MS);
  updateHeader();
}

/** Poll status until summary.json reflects the current corrections; re-fetch it whenever summary_updated_at changes. */
async function poll() {
  pollTimer = null;
  let st = null;
  try { st = await (await fetch(API + "status", { cache: "no-cache" })).json(); } catch { /* retry next tick */ }
  if (st) {
    C.status = st;
    if ((st.summary_updated_at ?? null) !== C.lastSummaryAt) {
      C.lastSummaryAt = st.summary_updated_at ?? null;
      S.summary = (await fetchJSON("summary.json", true)) ?? S.summary;
      emit();
    }
  }
  pollsLeft--;
  if ((st?.summarizing || summaryStale()) && pollsLeft > 0) pollTimer = setTimeout(poll, POLL_MS);
  updateHeader();
}

async function send(id, edit) {
  let error = null;
  try {
    const body = { item_id: id, key: edit.key, value: edit.value };
    if (edit.note) body.note = edit.note;
    const res = await fetch(API + "corrections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let json = null;
    try { json = await res.json(); } catch { /* non-JSON error */ }
    if (!res.ok || !json || !json.ok) throw new Error((json && json.error) || `HTTP ${res.status}`);
    C.confirmed[id] = json.corrections || {};
    if (json.updated_at) C.docUpdatedAt = json.updated_at;
  } catch (err) {
    error = err;
  }
  const drop = () => {
    const list = C.edits[id];
    list.splice(list.indexOf(edit), 1);
    if (!list.length) delete C.edits[id];
  };
  // On failure, or once nothing else is queued, re-read the server document. A successful edit stays pending
  // until then so the summary never looks current while the new corrections updated_at is still unknown.
  if (error) {
    drop();
    await resync();
  } else {
    if (Object.values(C.edits).reduce((n, l) => n + l.length, 0) === 1) await resync();
    drop();
  }
  refreshItem(id);
  if (error) toast(`Could not save the correction for ${edit.key}: ${error.message}. The change was discarded.`);
  startPolling();
  emit();
}

/**
 * Set (true/false) or revert (null) the ground truth for item/key. The edit shows immediately; POSTs are
 * serialized so responses apply in order, and a failed edit is dropped and state re-read from the server.
 */
export function setCorrection(item, key, value, note) {
  const edit = { key, value, note };
  (C.edits[item.id] ||= []).push(edit);
  refreshItem(item.id);
  updateHeader();
  emit();
  chain = chain.then(() => send(item.id, edit));
  return chain;
}
