/* Per-model metrics table: summary.json breakdowns (scope-aware, plus films), or live browser-computed ones. */
import { S } from "./data.js";
import { h, fmt } from "./ui.js";
import { buildHash } from "./router.js";
import { familyOf } from "./scoring.js";
import { liveBlock } from "./live-metrics.js";
import { canEdit, correctionCount, summaryStale } from "./corrections.js";

const COLS = ["n", "tp", "fp", "fn", "tn", "precision", "recall", "f1"];
const isRow = (o) => o && typeof o === "object" && ["tp", "fp", "fn", "tn"].some((k) => typeof o[k] === "number");

/** summary.json block for a model and scope: models.<id> or models.<id>.subsets.decision_sample. */
function summaryBlock(id, scope) {
  const m = S.summary?.models;
  if (!m) return null;
  const obj = Array.isArray(m) ? m.find((x) => (x.model_id || x.model || x.id) === id) : m[id];
  if (!obj) return null;
  return scope === "sample" ? obj.subsets?.decision_sample ?? null : obj;
}

/** Rows from a {families: {fam: row}, tokens: {"fam/tok": row} | {fam: {tok: row}}} breakdown. */
function breakdownRows(b) {
  const rows = [];
  const tokens = { ...(b?.tokens || {}) };
  for (const [k, v] of Object.entries(b?.families || {})) {
    if (!isRow(v)) continue;
    rows.push({ ...v, key: k });
    if (v.tokens) tokens[k] = v.tokens;
  }
  for (const [k, v] of Object.entries(tokens)) {
    if (isRow(v)) rows.push({ ...v, key: k });
    else if (v && typeof v === "object") for (const [t, r] of Object.entries(v)) if (isRow(r)) rows.push({ ...r, key: `${k}/${t}` });
  }
  const order = (k) => { const f = S.familyOrder.indexOf(familyOf(k)); return (f < 0 ? 999 : f) * 1000 + (k.includes("/") ? 1 : 0); };
  return rows.sort((a, b) => order(a.key) - order(b.key) || a.key.localeCompare(b.key));
}

const cells = (r) => COLS.map((c) => h("td", { text: ["precision", "recall", "f1"].includes(c) ? fmt(r[c]) : (r[c] ?? "–") }));
const head = (...first) => h("thead", {}, h("tr", {}, first.map((t) => h("th", { scope: "col", text: t })), COLS.map((c) => h("th", { scope: "col", text: c }))));

/** Overall plus per-film rows from block.films ({all: {windows, families, tokens}, "films/<Title>": {...}}). */
function filmsTable(films, note) {
  const entries = Object.entries(films).sort(([a], [b]) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b)));
  return h("details", { class: "films", open: true },
    h("summary", {}, `Films (${entries.length - (films.all ? 1 : 0)})`, note ? h("span", { class: "muted live", text: ` · ${note}` }) : null),
    h("div", { class: "table-wrap" }, h("table", {}, head("Film", "Windows", "Key"),
      // One row group per film: its name and window count span the group, and each key heads its own row.
      entries.map(([name, f]) => {
        const rows = breakdownRows(f);
        return h("tbody", {}, rows.map((r, i) => h("tr", { class: i ? "" : "film-first" },
          i ? null : [
            h("th", { scope: "rowgroup", rowspan: rows.length }, name === "all" ? "All films"
              : h("a", { href: buildHash("folder", name, {}), text: name.replace(/^films\//, ""), title: f.imdb_id || null })),
            h("td", { rowspan: rows.length, text: f.windows ?? "–" }),
          ],
          h("th", { scope: "row", text: r.key }), cells(r))));
      }))));
}

/**
 * Metrics for a model/scope. Uses summary.json when it reflects the current corrections; while it is stale
 * (summary.corrections.updated_at ≠ corrections updated_at) or absent, shows live browser-computed metrics.
 */
export function metricsTable(id, scope) {
  const block = summaryBlock(id, scope);
  const useSummary = breakdownRows(block).length > 0 && !summaryStale();
  const data = useSummary ? block : liveBlock(id, scope);
  const rows = breakdownRows(data);
  const liveNote = block && !useSummary && summaryStale() ? `live (${canEdit() ? "pending summary refresh" : "summary not yet regenerated"})` : null;
  const extras = block ? Object.entries(block).filter(([, v]) => typeof v === "number") : [];
  const source = useSummary ? `(summary.json${scope === "sample" ? ", decision sample" : ""})`
    : liveNote || "(computed in browser from loaded results)";
  const n = correctionCount();
  const corrNote = n ? ` · includes ${n} correction${n === 1 ? "" : "s"}` : "";
  return h("details", { class: "metrics", open: true },
    h("summary", {}, "Metrics ", h("span", { class: `muted${liveNote ? " live" : ""}`, text: source + corrNote })),
    extras.length ? h("p", { class: "muted" }, extras.map(([k, v]) => `${k}: ${Number.isInteger(v) ? v : +v.toFixed(3)}`).join(" · ")) : null,
    h("div", { class: "table-wrap" }, h("table", {}, head("Key"),
      h("tbody", {}, rows.map((r) => h("tr", { class: r.key.includes("/") ? "tok" : "fam" },
        h("th", { scope: "row" }, h("a", { href: buildHash("model", id, { cat: r.key, outcome: "all" }), text: r.key })),
        cells(r)))))),
    data?.films && Object.keys(data.films).length ? filmsTable(data.films, liveNote) : null);
}
