/* Compact model results, mirroring the harness: merge_record for repeated lines and restrict_results for subsets. */

const DEFAULT_THRESHOLD = 0.5;

/** Same lookup order as the harness's threshold_for: key, family, "*", default. */
function threshold(meta, key) {
  const t = (meta && meta.thresholds) || {};
  return t[key] ?? t[key.split("/")[0]] ?? t["*"] ?? DEFAULT_THRESHOLD;
}

const withFamilies = (keys) => {
  const out = new Set(keys);
  for (const k of keys) if (k.includes("/")) out.add(k.split("/")[0]);
  return [...out];
};

/** Clip-level scores (max over frames), best frame per key, asked keys and thresholded flags. */
function aggregate(frames, meta) {
  const scores = {}, best = {};
  let asked = null;
  for (const fr of frames) {
    for (const [k, v] of Object.entries(fr.scores || {})) if (!(k in scores) || v > scores[k]) { scores[k] = v; best[k] = fr.n; }
    if (Array.isArray(fr.asked)) { asked ||= new Set(); fr.asked.forEach((k) => asked.add(k)); }
  }
  const flagged = withFamilies(Object.keys(scores).filter((k) => scores[k] >= threshold(meta, k)));
  return { scores, best, asked, flagged };
}

const MAX_TEXT = 2000, MAX_LINES = 400;
const clip = (t) => (typeof t === "string" ? (t.length > MAX_TEXT ? t.slice(0, MAX_TEXT) + "…" : t) : null);

/** Optional narrative fields of a frame: description, severity {key: 0–3}, transcript (seq runs). */
function frameNarrative(fr) {
  const out = {};
  const d = clip(fr.description);
  if (d) out.description = d;
  if (fr.severity && typeof fr.severity === "object") {
    const sev = {};
    for (const [k, v] of Object.entries(fr.severity)) if (Number.isFinite(v)) sev[k] = Math.max(0, Math.min(3, Math.round(v)));
    if (Object.keys(sev).length) out.severity = sev;
  }
  if (Array.isArray(fr.transcript) && fr.transcript.length) out.transcript = fr.transcript.slice(0, MAX_LINES).map((l) => clip(String(l)));
  return out;
}

/** Clip-level narrative over compact frames: per-key max severity and the description of the most severe frame. */
function clipNarrative(frames) {
  const severity = {};
  let description = null, bestSev = -1;
  for (const f of frames) {
    const top = Math.max(-1, ...Object.values(f.severity || {}));
    for (const [k, v] of Object.entries(f.severity || {})) severity[k] = Math.max(severity[k] ?? 0, v);
    if (f.description && top > bestSev) { description = f.description; bestSev = top; }
  }
  return { severity: Object.keys(severity).length ? severity : null, description };
}

/*
 * Compact per-frame scores: each model (keyed by its meta object) has one shared key table, and a frame stores its
 * scores as a Float32Array over that table's columns (NaN = key absent from the frame; frames made before the table
 * grew are shorter, and missing columns are absent too). Asked-key lists are interned per model.
 */
const TABLES = new WeakMap();
const NO_META = {};
const EMPTY = Object.freeze([]);

function tableFor(meta) {
  const owner = meta && typeof meta === "object" ? meta : NO_META;
  let table = TABLES.get(owner);
  if (!table) TABLES.set(owner, (table = { keys: [], index: new Map(), asked: new Map() }));
  return table;
}

function packScores(table, scores) {
  const entries = Object.entries(scores || {});
  for (const [k] of entries) if (!table.index.has(k)) { table.index.set(k, table.keys.length); table.keys.push(k); }
  const values = new Float32Array(table.keys.length).fill(NaN);
  for (const [k, v] of entries) values[table.index.get(k)] = v;
  return values;
}

function internAsked(table, asked) {
  if (!Array.isArray(asked)) return undefined;
  const id = asked.join("\u0000");
  let shared = table.asked.get(id);
  if (!shared) table.asked.set(id, (shared = Object.freeze([...asked])));
  return shared;
}

/** A compact frame's scores as [key, value] pairs (value as stored, i.e. float32 precision). */
export function frameScores(frame, meta) {
  const { keys } = tableFor(meta), out = [];
  for (let i = 0; i < frame.v.length; i++) if (!Number.isNaN(frame.v[i])) out.push([keys[i], frame.v[i]]);
  return out;
}

/** A compact frame's n highest scores, computed on demand for rendering. */
export const frameTop = (frame, meta, n = 5) => frameScores(frame, meta).sort((a, b) => b[1] - a[1]).slice(0, n);

/**
 * Aggregate compact frames like the harness's aggregate. Scores are float32, so thresholds are compared at float32
 * precision too; only a score within float32 rounding (~1e-7 relative) of its threshold can flag differently.
 */
function aggregateCompact(frames, meta) {
  const { keys } = tableFor(meta);
  const scores = {}, best = {};
  let asked = null;
  for (const fr of frames) {
    for (let i = 0; i < fr.v.length; i++) {
      const v = fr.v[i];
      if (Number.isNaN(v)) continue;
      const k = keys[i];
      if (!(k in scores) || v > scores[k]) { scores[k] = v; best[k] = fr.n; }
    }
    if (fr.askedKeys) { asked ||= new Set(); fr.askedKeys.forEach((k) => asked.add(k)); }
  }
  const flagged = withFamilies(Object.keys(scores).filter((k) => scores[k] >= Math.fround(threshold(meta, k))));
  return { scores, best, asked, flagged };
}

/** Sample view: re-aggregated over exactly the subset frames (null if any is missing), like restrict_results. */
function withSample(base, meta, wanted) {
  if (!wanted) return base;
  if (base.status !== "ok") { base.sample = base; return base; }
  const byN = new Map(base.frames.map((f) => [f.n, f]));
  const frames = [...wanted].map((n) => byN.get(n));
  if (frames.some((f) => !f)) { base.sample = null; return base; }
  const a = aggregateCompact(frames, meta);
  const narrative = clipNarrative(base.frames.filter((f) => wanted.has(f.n)));
  base.sample = { ...base, scores: a.scores, best: a.best, asked: a.asked, flagged: a.flagged, ...narrative };
  return base;
}

/**
 * Compact one JSONL row: clip scores/flags and best frame per key (from the row's exact values), and per frame its
 * packed scores (see frameScores/frameTop), interned asked keys, labels and narrative. Frames keep every score so
 * merged rows and the decision sample (`wanted` = Set of subset frame numbers) can be re-aggregated.
 */
export function compactResult(row, meta, wanted) {
  const raw = row.frames || [];
  const agg = aggregate(raw, meta);
  const table = tableFor(meta);
  const frames = raw.map((fr) => {
    const frame = { n: fr.n, v: packScores(table, fr.scores), labels: fr.labels?.length ? fr.labels.slice(0, 8) : EMPTY };
    const askedKeys = internAsked(table, fr.asked);
    if (askedKeys) frame.askedKeys = askedKeys;
    return Object.assign(frame, frameNarrative(fr));
  });
  const base = {
    status: row.status || "ok", error: row.error, elapsed_ms: row.elapsed_ms, scores: row.scores || agg.scores,
    flagged: withFamilies(row.flagged || []), best: agg.best, asked: agg.asked, frames, ...clipNarrative(frames),
  };
  return withSample(base, meta, wanted);
}

/**
 * Merge a repeated line into the previous one like the harness's merge_record: errors never replace an ok
 * result; a line covering all earlier frames replaces it; otherwise frames merge by n (newer wins) and clip
 * scores, best frames, asked keys and flags are recomputed from the merged frames with the model's thresholds.
 */
export function mergeResult(prev, next, meta, wanted) {
  if (!prev) return next;
  if (next.status !== "ok") return prev.status === "ok" ? prev : next;
  if (prev.status !== "ok") return next;
  const nextNs = new Set(next.frames.map((f) => f.n));
  if (prev.frames.every((f) => nextNs.has(f.n))) return next;
  const byN = new Map(prev.frames.map((f) => [f.n, f]));
  next.frames.forEach((f) => byN.set(f.n, f));
  const frames = [...byN.keys()].sort((a, b) => a - b).map((n) => byN.get(n));
  const a = aggregateCompact(frames, meta);
  const merged = { ...next, frames, scores: a.scores, best: a.best, asked: a.asked, flagged: a.flagged, ...clipNarrative(frames) };
  return withSample(merged, meta, wanted);
}
