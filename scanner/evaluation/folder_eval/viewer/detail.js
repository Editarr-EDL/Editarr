/* Item detail dialog: original media, per-model clip scores and every frame's top scores. */
import { S, allLoaded, loadAllModels } from "./data.js";
import { h, $, fill, fmt, fmtTime, fmtWindow, encPath, expectedChips, itemExtras } from "./ui.js";
import { parseHash, withParams } from "./router.js";
import { frameBlock, protect } from "./media.js";
import { modelCell } from "./folder-view.js";
import { outcomeFor, supportsKey, wasAsked } from "./scoring.js";
import { correctable, truthEditor } from "./correction-controls.js";
import { frameNarrative, severityChips } from "./narrative.js";
import { getScope, inSampleFrame, resultFor } from "./scope.js";

const PLAYABLE = /\.(mp4|webm|m4v|mov)$/i;
const IMAGE = /\.(jpe?g|png|webp|gif)$/i;
/* Media nodes are reused across re-renders (e.g. after a correction) so playback isn't reset; their protection is
   reapplied on every render because a correction can change whether the item is sensitive. */
let mediaCache = { id: null, nodes: null, media: null };

/** Best clip score for a key (a family falls back to its best token). */
function keyScore(r, key) {
  if (key in r.scores) return r.scores[key];
  const toks = Object.entries(r.scores).filter(([k]) => k.startsWith(key + "/")).map(([, v]) => v);
  return toks.length ? Math.max(...toks) : null;
}

/** Score (or "not asked"/"unsupported") per expected key for one model; each chip offers a ground-truth correction. */
function expectedScores(item, r, id) {
  const meta = S.metas[id];
  if (!r || r.status !== "ok" || !(item.expected || []).length) return h("td", { class: "muted", text: "—" });
  return h("td", {}, h("span", { class: "chips" }, item.expected.map((k) => {
    const o = outcomeFor(item, r, k, meta);
    const [text, cls, title] = !supportsKey(meta, k) ? [`${k}: unsupported`, "notasked", "The model cannot emit this key"]
      : wasAsked(r, k) ? [`${k} ${fmt(keyScore(r, k))}`, "exp", `${o} for ${k}`]
      : [`${k}: not asked`, "notasked", "The model was not asked about this key"];
    return correctable(item, k, o, { text, cls: `chip ${cls}`, title }, `exp-${id}`);
  })));
}

/** Original media ({nodes, media}): video player (mp4/webm, seeking to a film window via #t=), image, or a link with the window time. */
function mediaSection(item) {
  const base = "../" + encPath(item.path);
  const win = Array.isArray(item.window) ? item.window : null;
  const playable = PLAYABLE.test(item.path);
  const href = playable && win ? `${base}#t=${win[0]}` : base;
  const media = playable ? h("video", { controls: true, preload: "metadata", src: win ? `${base}#t=${win[0]},${win[1]}` : base, class: "media" })
    : IMAGE.test(item.path) ? h("img", { src: base, alt: item.path, class: "media" }) : null;
  const wrap = media ? h("div", { class: "media-wrap media-box" }, media) : null;
  const nodes = [
    h("p", {}, h("a", { href, target: "_blank", rel: "noopener", text: "Open original media ↗" }),
      win ? h("span", { text: ` at ${fmtWindow(win)}${playable ? "" : " (seek manually)"}` }) : null,
      item.duration_s ? h("span", { class: "muted", text: ` · ${fmt(item.duration_s, 1)} s` }) : null,
      item.imdb_id ? [" · ", h("a", { href: `https://www.imdb.com/title/${encodeURIComponent(item.imdb_id)}/`, target: "_blank", rel: "noopener", text: item.imdb_id })] : null),
    wrap,
  ];
  return { nodes, media };
}

/** Open the dialog for itemId (from the route's item param) or close it when absent. */
export async function syncDetail(itemId) {
  const dlg = $("#detail");
  const item = itemId && S.byId.get(itemId);
  if (!item) { if (dlg.open) dlg.close(); return; }
  $("#detail-title").textContent = item.path.split("/").pop() + (item.window ? ` @ ${fmtWindow(item.window)}` : "");
  if (!allLoaded()) fill($("#detail-body"), h("p", { class: "loading", text: "Loading model results…" }));
  if (!dlg.open) dlg.showModal();
  // Sensitivity depends on every model's flags: create (or reuse) media only once they are all loaded.
  await loadAllModels();
  if (mediaCache.id !== item.id) mediaCache = { id: item.id, ...mediaSection(item) };
  if (mediaCache.media) protect(mediaCache.media, item);
  const scope = getScope();
  fill($("#detail-body"),
    h("p", {}, h("code", { text: item.path })),
    h("p", {}, expectedChips(item), " ", itemExtras(item)),
    mediaCache.nodes,
    h("p", { class: "muted", text: scope === "sample" ? "Scores below use only the decision-sample frames (outlined)." : "Scores below use all frames." }),
    h("div", { class: "table-wrap" }, h("table", { class: "compact" },
      h("thead", {}, h("tr", {}, ["Model", "Flagged", "Expected keys", "Severity", "Top clip scores", "ms"].map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", {}, S.modelIds.map((id) => {
        const r = resultFor(id, item.id, scope);
        const top = r ? Object.entries(r.scores).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => `${k} ${fmt(v)}`).join(", ") : "";
        return h("tr", {}, h("th", { scope: "row", text: S.metas[id].display_name || id }), modelCell(item, id, scope, "detail"),
          expectedScores(item, r, id), h("td", {}, severityChips(r, item) || "—"), h("td", { text: top || "—" }), h("td", { text: r?.elapsed_ms ?? "–" }));
      })))),
    truthEditor(item),
    h("h3", { text: `Frames (${(item.frames || []).length})` }),
    h("ul", { class: "frames" }, (item.frames || []).map((fr) => h("li", { class: `frame${inSampleFrame(item, fr.n) ? " in-sample" : ""}` },
      frameBlock(item, fr.n, encPath(fr.file), { cls: "", alt: `Frame ${fr.n} at ${fmt(fr.t, 1)} s`, linkAttrs: { target: "_blank", rel: "noopener" } }),
      h("div", { class: "muted", text: `#${fr.n} · ${item.window ? `${fmtTime(fr.t)} in film` : `${fmt(fr.t, 1)} s`}${inSampleFrame(item, fr.n) ? " · in sample" : ""}` }),
      h("dl", {}, S.modelIds.map((id) => {
        const f = S.results[id].get(item.id)?.frames.find((x) => x.n === fr.n);
        const txt = f ? f.top.slice(0, 3).map(([k, v]) => `${k} ${fmt(v)}`).join(", ") || "no scores" : "not run";
        const extra = (f && f.labels.length ? ` [${f.labels.join(", ")}]` : "") + (f && f.asked != null ? ` · asked ${f.asked} keys` : "");
        return [h("dt", { text: S.metas[id].display_name || id }), h("dd", { class: f ? null : "muted" }, txt + extra, frameNarrative(item, f))];
      }))))));
}

function closeDetail() { if (parseHash().params.get("item")) withParams({ item: "" }); }

export function initDetail() {
  const dlg = $("#detail");
  $("#detail-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", closeDetail);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}
