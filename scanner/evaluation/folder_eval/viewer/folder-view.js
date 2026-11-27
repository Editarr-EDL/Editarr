/* Folder view: collapsible tree with per-model stats and an item table with one column per model. */
import { S, allLoaded, loadAllModels } from "./data.js";
import { h, $, fill, fmt, fmtWindow, pct, chip, expectedChips, itemExtras, field, pager, debounce, syncControl, PAGE_SIZE } from "./ui.js";
import { buildHash, hashWith, parseHash, withParams } from "./router.js";
import { itemCorrect, maxScore, outcomeClass, outcomeFor, predicted, wasAsked } from "./scoring.js";
import { defaultFrame, getScope, resultFor, scopedItems } from "./scope.js";

let curScope = "all";
import { frameBlock } from "./media.js";
import { correctable } from "./correction-controls.js";

function buildTree(scope) {
  const root = { name: "", path: "", children: new Map(), count: 0 };
  for (const item of scopedItems(scope)) {
    let node = root;
    root.count++;
    for (const seg of item.folder.split("/").filter(Boolean)) {
      const path = node.path ? `${node.path}/${seg}` : seg;
      if (!node.children.has(seg)) node.children.set(seg, { name: seg, path, children: new Map(), count: 0 });
      node = node.children.get(seg);
      node.count++;
    }
  }
  return root;
}

/** Per-folder, per-model aggregates (recursive): ran, flagged, scorable, correct. */
function computeFolderStats(scope) {
  const stats = new Map();
  const get = (path) => {
    if (!stats.has(path)) stats.set(path, S.modelIds.map(() => ({ ran: 0, flagged: 0, scorable: 0, correct: 0 })));
    return stats.get(path);
  };
  for (const item of scopedItems(scope)) {
    const segs = item.folder.split("/").filter(Boolean);
    const paths = [""].concat(segs.map((_, i) => segs.slice(0, i + 1).join("/")));
    S.modelIds.forEach((id, mi) => {
      const r = resultFor(id, item.id, scope);
      if (!r || r.status !== "ok") return;
      const ok = itemCorrect(item, r, S.metas[id]);
      for (const p of paths) {
        const s = get(p)[mi];
        s.ran++;
        if (r.flagged.length) s.flagged++;
        if (ok != null) { s.scorable++; if (ok) s.correct++; }
      }
    });
  }
  return stats;
}

function treeNode(node, selected) {
  const kids = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name));
  const open = S.expanded.has(node.path);
  const stats = S.folderStats[curScope].get(node.path) || [];
  const bars = h("span", { class: "accbars", "aria-hidden": "true" }, S.modelIds.map((id, mi) => {
    const s = stats[mi];
    const acc = s && s.scorable ? s.correct / s.scorable : null;
    return h("span", { class: "accbar", title: `${id}: flagged ${pct(s?.flagged, s?.ran)}, accuracy ${acc == null ? "–" : pct(s.correct, s.scorable)}`,
      style: acc == null ? null : `background:linear-gradient(to top,var(--tp) ${Math.round(acc * 100)}%,var(--fp) 0)` });
  }));
  const row = h("div", { class: "tree-row" },
    kids.length ? h("button", { type: "button", class: "toggle", "aria-expanded": String(open), "aria-label": `${open ? "Collapse" : "Expand"} ${node.name || "root"}`,
      onclick: () => { open ? S.expanded.delete(node.path) : S.expanded.add(node.path); renderTree(); } }, open ? "▾" : "▸")
      : h("span", { class: "toggle spacer" }),
    h("a", { href: buildHash("folder", node.path, {}), "aria-current": node.path === selected ? "page" : null, text: node.name || "(all)" }),
    h("span", { class: "count", text: node.count }), bars);
  return h("li", {}, row, open && kids.length ? h("ul", {}, kids.map((k) => treeNode(k, selected))) : null);
}

function renderTree() {
  const tree = $("#tree");
  if (tree) fill(tree, h("ul", { class: "tree" }, treeNode(S.trees[curScope], parseHash().arg)));
}

function folderStatsTable(path) {
  const stats = S.folderStats[curScope].get(path) || [];
  return h("div", { class: "table-wrap" }, h("table", { class: "compact" },
    h("thead", {}, h("tr", {}, ["Model", "Results", "Flag rate", "Accuracy"].map((c) => h("th", { scope: "col", text: c })))),
    h("tbody", {}, S.modelIds.map((id, mi) => {
      const s = stats[mi] || { ran: 0, flagged: 0, scorable: 0, correct: 0 };
      return h("tr", {}, h("th", { scope: "row" }, h("a", { href: buildHash("model", id, { folder: path }), text: S.metas[id].display_name || id })),
        h("td", { text: s.ran }), h("td", { text: pct(s.flagged, s.ran) }), h("td", { text: `${pct(s.correct, s.scorable)} (${s.scorable})` }));
    }))));
}

/** Table cell with a model's flagged keys (✓ TP, ✗ FP), missed expected keys (FN) and "not asked" keys. */
export function modelCell(item, id, scope, ctx = "cell") {
  const r = resultFor(id, item.id, scope), meta = S.metas[id];
  const fix = (k, o, text, cls, title) => correctable(item, k, o, { text, cls: `chip ${cls}`, title }, `${ctx}-${id}`);
  if (!r) return h("td", { class: "muted", text: "—" });
  if (r.status !== "ok") return h("td", {}, chip("error", "fp", r.error));
  const chips = [...r.flagged].sort().map((k) => {
    const o = outcomeFor(item, r, k, meta);
    return fix(k, o, `${o === "TP" ? "✓" : o === "FP" ? "✗" : "•"} ${k}`, outcomeClass(o), o);
  });
  for (const k of item.expected || []) {
    if (predicted(r, k)) continue;
    if (outcomeFor(item, r, k, meta) !== "FN") continue;
    chips.push(wasAsked(r, k) ? fix(k, "FN", `✗ ${k}`, "fn", "missed (FN)")
      : fix(k, "FN", `✗ ${k}: not asked`, "fn notasked", "Not asked; counted as a missed positive (FN)"));
  }
  return h("td", {}, h("span", { class: "chips" }, chips.length ? chips : chip("none", "tn")), h("div", { class: "score", text: fmt(maxScore(r)) }));
}

export async function showFolder(r) {
  const path = r.arg;
  const scope = curScope = getScope(r);
  if (!S.shell.startsWith(`folder:${scope}:`)) {
    if (!allLoaded()) fill($("#main"), h("p", { class: "loading", text: "Loading all model results…" }));
    await loadAllModels();
    S.trees[scope] ||= buildTree(scope);
    S.folderStats[scope] ||= computeFolderStats(scope);
  }
  const segs = path.split("/").filter(Boolean);
  segs.forEach((_, i) => S.expanded.add(segs.slice(0, i + 1).join("/")));
  const p = Object.fromEntries(r.params);
  const key = `folder:${scope}:${path}`;
  if (S.shell !== key || !$("#folder-items")) {
    S.shell = key;
    fill($("#main"), h("div", { class: "folder-layout" },
      h("nav", { id: "tree", class: "tree-pane", "aria-label": "Folders" }),
      h("section", { class: "folder-main", "aria-label": "Folder contents" },
        h("h2", { class: "crumbs" }, h("a", { href: buildHash("folder", "", {}), text: "(all)" }),
          segs.map((s, i) => [" / ", h("a", { href: buildHash("folder", segs.slice(0, i + 1).join("/"), {}), text: s })])),
        folderStatsTable(path),
        h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
          field("Search", h("input", { type: "search", id: "f-fq", value: p.q || "", placeholder: "path contains…", oninput: debounce((e) => withParams({ q: e.target.value, page: "" }, true), 250) })),
          h("label", { class: "check" }, h("input", { type: "checkbox", id: "f-sub", checked: p.sub !== "0", onchange: (e) => withParams({ sub: e.target.checked ? "" : "0", page: "" }, true) }), " Include subfolders"),
          h("label", { class: "check" }, h("input", { type: "checkbox", id: "f-fcorr", checked: p.corr === "1", onchange: (e) => withParams({ corr: e.target.checked ? "1" : "", page: "" }, true) }), " Corrected only")),
        h("div", { id: "folder-items" }))));
  }
  renderTree();
  syncControl("f-fq", p.q || "");
  $("#f-fcorr").checked = p.corr === "1";
  const sub = p.sub !== "0", q = (p.q || "").toLowerCase();
  const items = scopedItems(scope).filter((i) => (sub ? !path || i.folder === path || i.folder.startsWith(path + "/") : i.folder === path)
    && (!q || i.path.toLowerCase().includes(q)) && (p.corr !== "1" || i.corr));
  const page = Math.max(1, Number(p.page) || 1);
  const slice = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pg = (where) => pager(items.length, page, (n) => hashWith({ page: n }), `Pages (${where})`);
  fill($("#folder-items"), pg("top"), h("div", { class: "table-wrap" }, h("table", { class: "items" },
    h("thead", {}, h("tr", {}, h("th", { scope: "col", text: "Item" }), h("th", { scope: "col", text: "Expected" }),
      S.modelIds.map((id) => h("th", { scope: "col", text: S.metas[id].display_name || id })))),
    h("tbody", {}, slice.map((item) => h("tr", {},
      h("th", { scope: "row" }, h("div", { class: "item-cell" },
        frameBlock(item, defaultFrame(item, scope), hashWith({ item: item.id }), { linkAttrs: { tabindex: "-1", "aria-hidden": "true" } }),
        h("a", { href: hashWith({ item: item.id }), class: "path", title: item.path,
          text: item.path.split("/").pop() + (item.window ? ` @ ${fmtWindow(item.window)}` : "") }))),
      h("td", {}, expectedChips(item), item.source_tags?.length ? itemExtras({ source_tags: item.source_tags }) : null),
      S.modelIds.map((id) => modelCell(item, id, scope))))))), pg("bottom"));
}
