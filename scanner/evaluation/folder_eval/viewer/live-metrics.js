/* Browser-side metrics with the same rules as the harness's summary.py, from effective (corrected) labels. */
import { S } from "./data.js";
import { expectedPositive } from "./scoring.js";
import { resultFor, scopedItems } from "./scope.js";

/** Confusion counts and P/R/F1 like summary.py's metric_row. */
function metricRow(pairs, key) {
  const c = { tp: 0, fp: 0, fn: 0, tn: 0 };
  for (const [item, r] of pairs) {
    const e = expectedPositive(item, key), p = r.flagged.includes(key);
    c[e ? (p ? "tp" : "fn") : (p ? "fp" : "tn")]++;
  }
  c.n = pairs.length;
  c.precision = c.tp + c.fp ? c.tp / (c.tp + c.fp) : null;
  c.recall = c.tp + c.fn ? c.tp / (c.tp + c.fn) : null;
  c.f1 = c.tp + c.fp + c.fn ? (2 * c.tp) / (2 * c.tp + c.fp + c.fn) : null;
  return c;
}

/**
 * Family rows for the families the model explicitly supports (a token-only model gets none, as in summary.py) plus
 * token rows for supported tokens with ground truth here. Empty/missing `supports` (e.g. no meta.json yet) means
 * every family and token, as in scoring.supportsKey.
 */
function group(pairs, supports) {
  const any = supports.size === 0;
  const families = any ? [...S.familyOrder].sort() : [...supports].filter((k) => !k.includes("/")).sort();
  const tokens = new Set();
  for (const [item] of pairs) for (const k of item.expected || []) if (k.includes("/") && (any || supports.has(k))) tokens.add(k);
  const rows = (keys) => Object.fromEntries(keys.map((k) => [k, metricRow(pairs, k)]));
  return { families: rows(families), tokens: rows([...tokens].sort()) };
}

/**
 * Summary-shaped block ({families, tokens, films}) computed live for a model and scope. Like summary.py it uses
 * scored items with an ok result; film windows (items with `window`) are reported only under `films`.
 */
export function liveBlock(id, scope) {
  const supports = new Set(S.metas[id]?.supports || []);
  const pairs = [];
  for (const item of scopedItems(scope)) {
    if (!item.scored) continue;
    const r = resultFor(id, item.id, scope);
    if (r && r.status === "ok") pairs.push([item, r]);
  }
  const block = group(pairs.filter(([item]) => !("window" in item)), supports);
  const byFilm = {};
  for (const pair of pairs) if ("window" in pair[0]) (byFilm[pair[0].folder] ||= []).push(pair);
  const films = Object.keys(byFilm).sort();
  if (films.length) {
    const all = films.flatMap((f) => byFilm[f]);
    block.films = { all: { windows: all.length, ...group(all, supports) } };
    for (const f of films) block.films[f] = { imdb_id: byFilm[f][0][0].imdb_id ?? null, windows: byFilm[f].length, ...group(byFilm[f], supports) };
  }
  return block;
}
