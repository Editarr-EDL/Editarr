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

/** Sample view: re-aggregated over exactly the subset frames (null if any is missing), like restrict_results. */
function withSample(base, meta, wanted) {
  if (!wanted) return base;
  if (base.status !== "ok") { base.sample = base; return base; }
  const frames = [...wanted].map((n) => base.subRaw.get(n));
  if (frames.some((f) => !f)) { base.sample = null; return base; }
  const a = aggregate(frames, meta);
  const narrative = clipNarrative(base.frames.filter((f) => wanted.has(f.n)));
  base.sample = { ...base, scores: a.scores, best: a.best, asked: a.asked, flagged: a.flagged, ...narrative };
  return base;
}

/**
 * Compact one JSONL row: clip scores/flags, per-frame top-5 and best frame per key. For decision-sample items
 * (`wanted` = Set of subset frame numbers) raw subset frames are kept so `sample` can be (re)computed.
 */
export function compactResult(row, meta, wanted) {
  const raw = row.frames || [];
  const agg = aggregate(raw, meta);
  const frames = raw.map((fr) => ({
    n: fr.n,
    top: Object.entries(fr.scores || {}).sort((a, b) => b[1] - a[1]).slice(0, 5),
    labels: (fr.labels || []).slice(0, 8),
    asked: Array.isArray(fr.asked) ? fr.asked.length : null,
    ...frameNarrative(fr),
  }));
  const base = {
    status: row.status || "ok", error: row.error, elapsed_ms: row.elapsed_ms, scores: row.scores || agg.scores,
    flagged: withFamilies(row.flagged || []), best: agg.best, asked: agg.asked, frames, ...clipNarrative(frames),
    subRaw: wanted ? new Map(raw.filter((fr) => wanted.has(fr.n)).map((fr) => [fr.n, { n: fr.n, scores: fr.scores, asked: fr.asked }])) : null,
  };
  return withSample(base, meta, wanted);
}

/**
 * Merge a repeated line into the previous one like the harness's merge_record: errors never replace an ok
 * result; a line covering all earlier frames replaces it; otherwise frames merge by n (newer wins) and flags union.
 * Clip scores are approximated as the per-key max of both lines (display only; metrics use flags).
 */
export function mergeResult(prev, next, meta, wanted) {
  if (!prev) return next;
  if (next.status !== "ok") return prev.status === "ok" ? prev : next;
  if (prev.status !== "ok") return next;
  const nextNs = new Set(next.frames.map((f) => f.n));
  if (prev.frames.every((f) => nextNs.has(f.n))) return next;
  const byN = new Map(prev.frames.map((f) => [f.n, f]));
  next.frames.forEach((f) => byN.set(f.n, f));
  const scores = { ...prev.scores }, best = { ...prev.best };
  for (const [k, v] of Object.entries(next.scores)) if (!(k in scores) || v >= scores[k]) { scores[k] = v; best[k] = next.best[k] ?? best[k]; }
  const asked = prev.asked || next.asked ? new Set([...(prev.asked || []), ...(next.asked || [])]) : null;
  const subRaw = prev.subRaw || next.subRaw ? new Map([...(prev.subRaw || []), ...(next.subRaw || [])]) : null;
  const frames = [...byN.keys()].sort((a, b) => a - b).map((n) => byN.get(n));
  const merged = {
    ...next, frames, scores, best, asked, subRaw, ...clipNarrative(frames),
    flagged: [...new Set([...prev.flagged, ...next.flagged])],
  };
  return withSample(merged, meta, wanted);
}
