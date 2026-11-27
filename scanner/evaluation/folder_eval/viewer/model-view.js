/* Model view: metrics, flagged items grouped by category key, filters and paging. */
import { S, loadModel } from "./data.js";
import { h, $, fill, fmt, chip, expectedChips, itemExtras, field, select, pager, debounce, syncControl, PAGE_SIZE } from "./ui.js";
import { buildHash, hashWith, parseHash, withParams } from "./router.js";
import { OUTCOMES, maxScore, outcomeClass, outcomeFor, predicted, supportsKey, wasAsked } from "./scoring.js";
import { defaultFrame, getScope, resultFor, scopedItems } from "./scope.js";
import { frameBlock } from "./media.js";
import { correctable } from "./correction-controls.js";
import { descriptionLine, maxSeverityChip } from "./narrative.js";
import { metricsTable } from "./metrics.js";

const GROUP_PREVIEW = 12;

function categoryKeys(id, scope) {
  const meta = S.metas[id];
  const flaggedCount = {};
  let none = 0;
  for (const item of scopedItems(scope)) {
    const r = resultFor(id, item.id, scope);
    if (!r) continue;
    if (r.status === "ok" && !r.flagged.length) none++;
    for (const k of r.flagged) flaggedCount[k] = (flaggedCount[k] || 0) + 1;
  }
  const keys = [];
  const seen = new Set();
  const add = (k, depth) => { if (!seen.has(k)) { seen.add(k); keys.push({ key: k, depth, count: flaggedCount[k] || 0 }); } };
  for (const f of S.familyOrder) {
    const toks = (S.families[f] || []).map((t) => `${f}/${t}`)
      .filter((k) => flaggedCount[k] || (meta.supports || []).includes(k));
    if (!flaggedCount[f] && !supportsKey(meta, f) && !toks.length) continue;
    add(f, 0);
    toks.forEach((k) => add(k, 1));
  }
  for (const k of Object.keys(flaggedCount).sort()) add(k, k.includes("/") ? 1 : 0);
  return { keys, none };
}

function modelRows(id, p, scope) {
  const meta = S.metas[id];
  const cat = p.cat || "", outcome = p.outcome || "flagged", folder = p.folder || "", q = (p.q || "").toLowerCase();
  const rows = [];
  for (const item of scopedItems(scope)) {
    const r = resultFor(id, item.id, scope);
    if (!r) continue;
    if (folder && !(item.path.startsWith(folder) || item.folder.startsWith(folder))) continue;
    if (q && !item.path.toLowerCase().includes(q)) continue;
    if (p.corr === "1" && !item.corr) continue;
    let o, score, frame;
    if (cat === "__none") {
      if (r.status !== "ok" || r.flagged.length) continue;
      const fams = S.familyOrder.filter((f) => supportsKey(meta, f));
      o = !item.scored ? "unscored" : fams.some((f) => outcomeFor(item, r, f, meta) === "FN") ? "FN" : "TN";
      score = maxScore(r);
    } else {
      o = outcomeFor(item, r, cat, meta);
      if (outcome === "flagged" ? !predicted(r, cat) : outcome !== "all" && o !== outcome) continue;
      score = r.scores[cat] || 0;
      frame = r.best[cat];
    }
    if (cat === "__none" && outcome !== "flagged" && outcome !== "all" && o !== outcome) continue;
    rows.push({ item, r, o, score, frame });
  }
  const sort = p.sort || "score_desc";
  if (sort === "path") rows.sort((a, b) => a.item.path.localeCompare(b.item.path));
  else rows.sort((a, b) => (sort === "score_asc" ? a.score - b.score : b.score - a.score));
  return rows;
}

function card(row, key, scope) {
  const { item, r, o, score } = row;
  return h("li", { class: "card" },
    frameBlock(item, row.frame ?? (key ? r.best[key] : undefined) ?? defaultFrame(item, scope), hashWith({ item: item.id }),
      { linkAttrs: { class: "card-link", "aria-label": `Open ${item.path}` } }),
    h("div", { class: "card-body" },
      h("div", { class: "path", title: item.path, text: item.path }),
      expectedChips(item),
      itemExtras(item),
      descriptionLine(item, r),
      h("div", { class: "card-foot" },
        key ? correctable(item, key, o, { text: o, cls: `badge ${outcomeClass(o)}`, title: `${o} for ${key}` }, "card")
          : h("span", { class: `badge ${outcomeClass(o)}`, "data-outcome": o, text: o }),
        key && r.status === "ok" && !wasAsked(r, key)
          ? h("span", { class: "badge notasked", title: "Not asked; counted as a negative prediction", text: "not asked" })
          : h("span", { class: "score", text: `score ${fmt(score)}` }),
        maxSeverityChip(r, item),
        r.status !== "ok" ? chip("error", "fp", r.error) : null)));
}

export async function showModel(r) {
  if (!S.modelIds.length) {
    fill($("#main"), h("div", { class: "empty" }, h("h2", { text: "No models found" }),
      h("p", { text: "Expected _results/models/index.json listing model ids (or summary.json models)." })));
    return;
  }
  const id = S.modelIds.includes(r.arg) ? r.arg : S.modelIds[0];
  if (r.arg !== id) { history.replaceState(null, "", buildHash("model", id, Object.fromEntries(r.params))); }
  const scope = getScope();
  if (S.shell !== `model:${id}:${scope}`) {
    if (!S.results[id]) fill($("#main"), h("p", { class: "loading", text: `Loading ${id}…` }));
    await loadModel(id);
    buildModelShell(id, scope);
  }
  updateModelList(id, Object.fromEntries(parseHash().params), scope);
}

function buildModelShell(id, scope) {
  S.shell = `model:${id}:${scope}`;
  const meta = S.metas[id];
  const p = Object.fromEntries(parseHash().params);
  const { keys, none } = categoryKeys(id, scope);
  const upd = (k) => (e) => withParams({ [k]: e.target.value, page: "" }, true);
  const catOptions = [["", "All flagged (grouped)"], ...keys.map((k) => [k.key, `${k.depth ? "  └ " : ""}${k.key} (${k.count})`]), ["__none", `Not flagged (${none})`]];
  const folders = [...new Set(scopedItems(scope).map((i) => i.folder.split("/").slice(0, 2).join("/")))].sort();
  const searchInput = h("input", { type: "search", id: "f-q", value: p.q || "", placeholder: "path contains…", oninput: debounce(upd("q"), 250) });
  const folderInput = h("input", { type: "text", id: "f-folder", list: "folder-list", value: p.folder || "", placeholder: "e.g. video/ucf_crime", oninput: debounce(upd("folder"), 250) });
  fill($("#main"), 
    h("section", { class: "model-head" },
      h("div", { class: "row" },
        field("Model", select(S.modelIds.map((m) => [m, S.metas[m].display_name || m]), id, (e) => { location.hash = buildHash("model", e.target.value, {}); }, { id: "f-model" })),
        h("div", { class: "meta" },
          h("strong", { text: meta.display_name || id }),
          meta.runtime ? chip(meta.runtime, "") : null,
          meta.artifact ? h("span", { class: "muted", text: meta.artifact }) : null,
          meta.thresholds ? h("span", { class: "muted", text: "thresholds: " + Object.entries(meta.thresholds).map(([k, v]) => `${k}=${v}`).join(", ") }) : null,
          meta.notes ? h("p", { class: "muted", text: meta.notes }) : null)),
      metricsTable(id, scope)),
    h("div", { class: "model-layout" },
      h("nav", { class: "groups", "aria-label": "Categories" },
        h("h2", { text: "Flagged by category" }),
        h("ul", {},
          h("li", {}, h("a", { href: buildHash("model", id, { cat: "" }), "data-cat": "", text: "All flagged" })),
          keys.map((k) => h("li", { class: k.depth ? "tok" : "fam" },
            h("a", { href: buildHash("model", id, { cat: k.key }), "data-cat": k.key }, k.key, h("span", { class: "count", text: k.count })))),
          h("li", {}, h("a", { href: buildHash("model", id, { cat: "__none" }), "data-cat": "__none" }, "Not flagged", h("span", { class: "count", text: none }))))),
      h("section", { class: "results", "aria-label": "Results" },
        h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
          field("Category", select(catOptions, p.cat || "", upd("cat"), { id: "f-cat" })),
          field("Outcome", select([["flagged", "Flagged"], ["all", "All"], ...OUTCOMES.map((o) => [o, o])], p.outcome || "flagged", upd("outcome"), { id: "f-outcome" })),
          field("Folder prefix", folderInput),
          h("datalist", { id: "folder-list" }, folders.map((f) => h("option", { value: f }))),
          field("Search", searchInput),
          field("Sort", select([["score_desc", "Score ↓"], ["score_asc", "Score ↑"], ["path", "Path"]], p.sort || "score_desc", upd("sort"), { id: "f-sort" })),
          h("label", { class: "check" }, h("input", { type: "checkbox", id: "f-corr", checked: p.corr === "1",
            onchange: (e) => withParams({ corr: e.target.checked ? "1" : "", page: "" }, true) }), " Corrected only")),
        h("div", { id: "model-list" }))));
}

function updateModelList(id, p, scope) {
  syncControl("f-cat", p.cat || ""); syncControl("f-outcome", p.outcome || "flagged");
  $("#f-corr").checked = p.corr === "1";
  syncControl("f-folder", p.folder || ""); syncControl("f-q", p.q || ""); syncControl("f-sort", p.sort || "score_desc");
  document.querySelectorAll(".groups a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.cat === (p.cat || "")));
  const box = $("#model-list");
  const items = scopedItems(scope);
  const missing = items.filter((i) => !resultFor(id, i.id, scope)).length;
  const note = missing > 0 ? h("p", { class: "muted", text: `${missing} of ${items.length} items in this scope have no result for this model.` }) : null;
  if (!p.cat) {
    const { keys } = categoryKeys(id, scope);
    const groups = [...keys.filter((k) => k.count).map((k) => k.key), "__none"];
    fill(box, note, groups.map((key) => {
      const rows = modelRows(id, { ...p, cat: key }, scope);
      const label = key === "__none" ? `Not flagged` : key;
      return h("section", { class: "group" },
        h("h3", {}, label, h("span", { class: "count", text: rows.length }),
          h("a", { class: "more", href: buildHash("model", id, { ...p, cat: key, page: "" }), text: `Show all →` })),
        rows.length ? h("ul", { class: "cards" }, rows.slice(0, GROUP_PREVIEW).map((row) => card(row, key === "__none" ? null : key, scope)))
          : h("p", { class: "muted", text: "No matching items." }));
    }));
    return;
  }
  const rows = modelRows(id, p, scope);
  const page = Math.max(1, Number(p.page) || 1);
  const slice = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pg = () => pager(rows.length, page, (n) => hashWith({ page: n }));
  fill(box, note, pg(),
    slice.length ? h("ul", { class: "cards" }, slice.map((row) => card(row, p.cat === "__none" ? null : p.cat, scope)))
      : h("p", { class: "muted", text: "No matching items." }), pg());
}
