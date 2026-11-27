/* Shared state plus loading of inventory, summary, model metadata and streamed JSONL results. */
import { setStatus, toast } from "./ui.js";
import { compactResult, mergeResult } from "./results.js";

const KNOWN_MODELS = ["nudenet", "siglip2", "d1-omni-2b", "clef-flash", "d1-3b"];

export const S = {
  inv: null, items: [], byId: new Map(), families: {}, familyOrder: [],
  modelIds: [], metas: {}, results: {}, pending: {}, loadErrors: {}, summary: null,
  subset: null, askedModels: new Set(), corrections: {},
  trees: {}, folderStats: {}, shell: "", expanded: new Set([""]),
};

/**
 * Canonical MCF key, as in the inventory: a general-token alias that some backends emit (e.g. violence/violence,
 * which is not one of the family's inventory tokens) becomes its family; other keys are unchanged.
 */
export function canonicalKey(key) {
  const i = key.indexOf("/");
  if (i < 0) return key;
  const fam = key.slice(0, i), toks = S.families[fam];
  return toks && !toks.includes(key.slice(i + 1)) ? fam : key;
}

const canonicalList = (keys) => [...new Set(keys.map(canonicalKey))];
const canonicalScores = (scores) => {
  const out = {};
  for (const [k, v] of Object.entries(scores || {})) { const c = canonicalKey(k); out[c] = Math.max(out[c] ?? -Infinity, v); }
  return out;
};

/** A JSONL row with canonical keys in its clip and frame scores, flags and asked keys. */
export function canonicalRow(row) {
  const frames = (row.frames || []).map((fr) => ({
    ...fr, scores: canonicalScores(fr.scores), ...(Array.isArray(fr.asked) ? { asked: canonicalList(fr.asked) } : {}),
  }));
  return { ...row, frames, ...(row.scores ? { scores: canonicalScores(row.scores) } : {}), flagged: canonicalList(row.flagged || []) };
}

/** Model meta with canonical `supports`, so aliases are never scored or offered as corrections. */
export function canonicalMeta(meta) {
  return Array.isArray(meta.supports) ? { ...meta, supports: canonicalList(meta.supports) } : meta;
}

export async function fetchJSON(url, optional) {
  try {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    if (optional) return null;
    throw err;
  }
}

/**
 * Stream a JSONL file line by line, calling onRow per parsed object and onProgress(bytes, total). A missing file
 * (404) reads as empty; any other HTTP error or an interrupted stream rejects.
 */
async function streamJsonl(url, onRow, onProgress) {
  const res = await fetch(url, { cache: "no-cache" });
  if (res.status === 404) return;
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length")) || 0;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "", bytes = 0;
  const flush = (line) => {
    line = line.trim();
    if (!line) return;
    try { onRow(JSON.parse(line)); } catch (err) { console.warn("Bad JSONL line in", url, err.message); }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.length;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) { flush(buf.slice(0, nl)); buf = buf.slice(nl + 1); }
    onProgress?.(bytes, total);
  }
  flush(buf + decoder.decode());
}

export async function loadBase() {
  setStatus("Loading inventory…");
  const [inv, summary, index] = await Promise.all([
    fetchJSON("inventory.json"),
    fetchJSON("summary.json", true),
    fetchJSON("models/index.json", true),
  ]);
  S.inv = inv;
  S.items = inv.items || [];
  S.families = inv.families || {};
  S.familyOrder = Object.keys(S.families);
  for (const it of S.items) S.byId.set(it.id, it);
  S.summary = summary;
  S.subset = await loadSubset(summary);
  S.modelIds = await discoverModels(index, summary);
  const metas = await Promise.all(S.modelIds.map((id) => fetchJSON(`models/${encodeURIComponent(id)}.meta.json`, true)));
  S.modelIds.forEach((id, i) => { S.metas[id] = canonicalMeta(metas[i] || { model_id: id }); });
  setStatus("");
}

/** Decision sample from summary.subsets.decision_sample {subset_ids, frames}, else subsets/decision_sample.json {items: [{id, frames}]}. */
async function loadSubset(summary) {
  let ds = summary?.subsets?.decision_sample;
  if (!ds?.subset_ids) {
    const file = await fetchJSON("subsets/decision_sample.json", true);
    if (!Array.isArray(file?.items)) return null;
    ds = { subset_ids: file.items.map((i) => i.id), frames: Object.fromEntries(file.items.map((i) => [i.id, i.frames || []])) };
  }
  const frames = new Map(Object.entries(ds.frames || {}).map(([id, ns]) => [id, new Set(ns)]));
  return { ids: new Set(ds.subset_ids), frames };
}

/** models/index.json may be ["id", …], [{id|model_id}, …] or {"models": […]}; falls back to summary keys, then probing. */
async function discoverModels(index, summary) {
  const list = Array.isArray(index) ? index : index && Array.isArray(index.models) ? index.models : null;
  if (list) return list.map((m) => (typeof m === "string" ? m : m.id || m.model_id)).filter(Boolean);
  const fromSummary = summaryModelIds(summary);
  if (fromSummary.length) return fromSummary;
  const probes = await Promise.all(KNOWN_MODELS.map((id) => fetchJSON(`models/${id}.meta.json`, true)));
  return KNOWN_MODELS.filter((_, i) => probes[i]);
}

function summaryModelIds(summary) {
  if (!summary) return [];
  const m = summary.models || summary;
  if (Array.isArray(m)) return m.map((x) => x.model_id || x.model || x.id).filter(Boolean);
  return Object.keys(m).filter((k) => m[k] && typeof m[k] === "object" && !Array.isArray(m[k]));
}

/**
 * Load (once) a model's results into S.results[id] as Map(item id → compact result). Results are committed only
 * after a complete read; a failure is shown, recorded in S.loadErrors and rethrown, and the next call retries.
 * Until every model has loaded, allLoaded() stays false, so sensitive media and text stay protected.
 */
export function loadModel(id) {
  if (S.results[id]) return Promise.resolve(S.results[id]);
  if (S.pending[id]) return S.pending[id];
  const map = new Map();
  S.pending[id] = streamJsonl(
    `models/${encodeURIComponent(id)}.jsonl`,
    (row) => {
      if (!row || !row.id) return;
      const meta = S.metas[id], wanted = S.subset?.frames.get(row.id);
      const next = compactResult(canonicalRow(row), meta, wanted);
      if (next.asked) S.askedModels.add(id);
      map.set(row.id, mergeResult(map.get(row.id), next, meta, wanted));
    },
    (b, t) => setStatus(`Loading ${id}: ${(b / 1048576).toFixed(1)}${t ? ` / ${(t / 1048576).toFixed(1)}` : ""} MB`),
  ).then(
    () => { S.results[id] = map; delete S.loadErrors[id]; return map; },
    (err) => {
      S.loadErrors[id] = String(err?.message || err);
      toast(`Could not load results for ${id} (${S.loadErrors[id]}). Sensitive media and text stay hidden; reload to retry.`, 15000);
      throw err;
    },
  ).finally(() => { delete S.pending[id]; setStatus(""); });
  return S.pending[id];
}

export const loadAllModels = () => Promise.all(S.modelIds.map(loadModel));
export const allLoaded = () => S.modelIds.every((id) => S.results[id]);
