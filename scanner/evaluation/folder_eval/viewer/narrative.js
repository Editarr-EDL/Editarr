/* Model narrative output: descriptions, severity chips (vs expected severity) and transcripts.
   Text for sensitive (nudity/sex) items stays hidden behind the same reveal controls as images. */
import { allLoaded, loadAllModels } from "./data.js";
import { h, chip } from "./ui.js";
import { isSensitive } from "./media.js";

const CARD_TEXT = 300;
let refreshQueued = false;

/** Severity for a key; a family falls back to the max over its tokens. */
function keySeverity(sev, key) {
  if (!sev) return null;
  if (key in sev) return sev[key];
  if (key.includes("/")) return null;
  const toks = Object.entries(sev).filter(([k]) => k.startsWith(key + "/")).map(([, v]) => v);
  return toks.length ? Math.max(...toks) : null;
}

/** "violence 2/3" chip; with item.expected_severity for the key it adds ✓ (equal), ≈ (within 1) or ✗. */
function severityChip(key, value, item) {
  const exp = item?.expected_severity?.[key];
  let cls = `sev sev-${value ?? "na"}`, text = `${key} ${value ?? "–"}/3`, title = `Model severity for ${key}`;
  if (Number.isFinite(exp)) {
    const diff = value == null ? Infinity : Math.abs(value - exp);
    const [mark, kind, word] = diff === 0 ? ["✓", "match", "matches"] : diff === 1 ? ["≈", "close", "is within 1 of"] : ["✗", "miss", "differs from"];
    cls += ` sev-${kind}`;
    text += ` · exp ${exp} ${mark}`;
    title = `Model severity ${value ?? "not rated"} ${word} expected ${exp}`;
  }
  return chip(text, cls, title);
}

/** Clip-level chips: every key rated above 0 plus every key with an expected severity (only for models that rate severity). */
export function severityChips(r, item) {
  if (!r?.severity) return null;
  const keys = new Set([
    ...Object.keys(r?.severity || {}).filter((k) => r.severity[k] > 0),
    ...Object.keys(item.expected_severity || {}),
  ]);
  return keys.size ? h("span", { class: "chips" }, [...keys].sort().map((k) => severityChip(k, keySeverity(r?.severity, k), item))) : null;
}

/** The most severe key as one chip (ties prefer a key with an expected severity, to show the comparison), or null. */
export function maxSeverityChip(r, item) {
  let best = null;
  const hasExp = (k) => Number.isFinite(item.expected_severity?.[k]);
  for (const [k, v] of Object.entries(r?.severity || {})) {
    if (v > 0 && (!best || v > best[1] || (v === best[1] && hasExp(k) && !hasExp(best[0])))) best = [k, v];
  }
  return best ? severityChip(best[0], best[1], item) : null;
}

/**
 * Return `content` as is for non-sensitive items; otherwise wrap it so it stays hidden until revealed (per element
 * or via the global "Show sensitive images" toggle). Until every model's flags are loaded, text is hidden too and
 * the view re-renders once they are.
 */
function sensitiveText(item, content, what, block) {
  const known = allLoaded();
  if (known && !isSensitive(item)) return content;
  if (!known && !refreshQueued) {
    refreshQueued = true;
    loadAllModels().then(() => window.dispatchEvent(new Event("evaldata")));
  }
  const label = known ? `Show ${what} (sensitive)` : `Show ${what}`;
  const wrap = h("span", { class: `sens-text${block ? " block" : ""}` });
  const btn = h("button", { type: "button", class: "btn reveal-text", "aria-expanded": "false", text: label });
  btn.addEventListener("click", () => {
    const on = wrap.classList.toggle("revealed");
    btn.setAttribute("aria-expanded", String(on));
    btn.textContent = on ? `Hide ${what}` : label;
  });
  wrap.append(btn, h("span", { class: "sens-content" }, content));
  return wrap;
}

/** One-line description for a model-view card (escaped text; full text as a tooltip only if not sensitive). */
export function descriptionLine(item, r) {
  if (!r?.description) return null;
  const full = r.description;
  const text = h("span", { class: "desc", text: full.length > CARD_TEXT ? full.slice(0, CARD_TEXT) + "…" : full });
  const out = sensitiveText(item, text, "description", false);
  if (out === text) text.title = full;
  return h("p", { class: "desc-line" }, out);
}

/** Per-frame narrative for the detail panel: description, severity chips and a collapsible transcript. */
export function frameNarrative(item, f) {
  if (!f || (!f.description && !f.severity && !f.transcript)) return null;
  const transcript = f.transcript
    ? h("details", { class: "transcript" }, h("summary", { text: `Transcript (${f.transcript.length} line${f.transcript.length === 1 ? "" : "s"})` }),
      h("ol", {}, f.transcript.map((line) => h("li", { text: line }))))
    : null;
  return h("div", { class: "narrative" },
    f.description ? sensitiveText(item, h("p", { class: "desc", text: f.description }), "description", true) : null,
    f.severity ? h("span", { class: "chips" }, Object.entries(f.severity).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => severityChip(k, v, null))) : null,
    transcript ? sensitiveText(item, transcript, "transcript", true) : null);
}
