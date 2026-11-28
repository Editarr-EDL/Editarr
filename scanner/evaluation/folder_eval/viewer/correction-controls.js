/* Ground-truth correction controls: outcome menus and the detail-panel ground-truth editor. */
import { S } from "./data.js";
import { h } from "./ui.js";
import { expectedPositive } from "./scoring.js";
import { C, canEdit, isCorrected, setCorrection } from "./corrections.js";

function actionsFor(item, key, outcome) {
  const acts = expectedPositive(item, key)
    ? [[false, outcome === "FN" ? "Mark absent → TN" : outcome === "TP" ? "Actually absent → FP" : "Mark absent"]]
    : [[true, outcome === "FP" ? "Mark correct (actually present) → TP" : outcome === "TN" ? "Actually present → FN" : "Mark present"]];
  if (isCorrected(item, key)) acts.push([null, "Revert to inventory label"]);
  return acts;
}

/**
 * An outcome badge/chip for (item, key). In edit mode it is a disclosure menu offering the ground-truth
 * correction for that outcome; otherwise a plain label. Corrected keys get a ✎ marker.
 * trigger = {text, cls, title}; ctx makes the focus key unique per view/column.
 */
export function correctable(item, key, outcome, trigger, ctx) {
  const corrected = isCorrected(item, key);
  const cls = `${trigger.cls}${corrected ? " corrected" : ""}`;
  const mark = corrected ? h("span", { class: "corr-mark", "aria-label": "corrected", text: " ✎" }) : null;
  const title = corrected ? `${trigger.title || trigger.text} · ground truth corrected (${item.corr[key] ? "present" : "absent"})` : trigger.title;
  // data-outcome is the stable, markup-independent outcome (TP/FP/FN/TN/unscored) for tests and tooling.
  if (!canEdit()) return h("span", { class: cls, title, "data-outcome": outcome }, trigger.text, mark);
  const d = h("details", { class: "fix" },
    h("summary", { class: cls, title, "data-outcome": outcome, "data-fkey": `${ctx}|${item.id}|${key}`, "aria-label": `${trigger.text}${corrected ? " (corrected)" : ""} — correct ground truth for ${key}` },
      trigger.text, mark, h("span", { class: "caret", "aria-hidden": "true", text: " ▾" })),
    h("div", { class: "fix-menu", role: "group", "aria-label": `Ground truth for ${key}` },
      h("p", { class: "fix-note", text: `Ground truth for ${key} — applies to all models.` }),
      actionsFor(item, key, outcome).map(([v, text]) => h("button", { type: "button", class: "btn", onclick: () => {
        d.querySelector("summary").focus(); // focus is restored to this trigger after the view re-renders
        setCorrection(item, key, v, C.note || undefined);
      } }, text))));
  d.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !d.open) return;
    e.preventDefault();
    e.stopPropagation();
    d.open = false;
    d.querySelector("summary").focus();
  });
  return d;
}

function truthRow(item, key) {
  const corr = item.corr?.[key];
  const current = corr === true ? "present" : corr === false ? "absent" : "default";
  const name = `gt-${item.id}-${key}`;
  const inv = { ...item, expected: item.inv_expected };
  return h("fieldset", { class: `gt-row${corr !== undefined ? " corrected" : ""}` },
    h("legend", {}, key, h("span", { class: "muted", text: ` · now ${expectedPositive(item, key) ? "present" : "absent"}` })),
    [["default", `Default (${expectedPositive(inv, key) ? "present" : "absent"})`], ["present", "Present"], ["absent", "Absent"]].map(([v, label]) =>
      h("label", { class: "gt-opt" },
        h("input", { type: "radio", name, value: v, checked: v === current, "data-fkey": `gt|${item.id}|${key}|${v}`,
          onchange: () => setCorrection(item, key, v === "default" ? null : v === "present", C.note || undefined) }),
        ` ${label}`)));
}

/** Detail-panel ground-truth editor (edit mode) or a list of the item's corrections (read-only). */
export function truthEditor(item) {
  if (!canEdit()) {
    if (!item.corr) return null;
    return h("section", { class: "gt" }, h("h3", { text: "Ground-truth corrections" }),
      h("p", { class: "muted", text: "Read-only: corrections apply to all models." }),
      h("ul", {}, Object.entries(item.corr).map(([k, v]) => h("li", { text: `${k}: ${v ? "present" : "absent"}` }))));
  }
  return h("section", { class: "gt", "aria-label": "Ground truth editor" },
    h("h3", { text: "Ground truth" }),
    h("p", { class: "muted", text: "Corrections change the expected labels for this item and apply to every model's metrics." }),
    h("label", { class: "field gt-note" }, h("span", { text: "Note (optional, saved with each change)" }),
      h("input", { type: "text", value: C.note, maxlength: "500", "data-fkey": `gt-note|${item.id}`, oninput: (e) => { C.note = e.target.value; } })),
    S.familyOrder.map((f) => {
      const toks = (S.families[f] || []).map((t) => `${f}/${t}`);
      const open = C.openFamilies.has(f) || toks.some((k) => isCorrected(item, k));
      const d = h("details", { class: "gt-family", open },
        h("summary", { "data-fkey": `gt-fam|${item.id}|${f}`, text: `${f} tokens (${toks.length})` }),
        toks.map((k) => truthRow(item, k)));
      d.addEventListener("toggle", () => { d.open ? C.openFamilies.add(f) : C.openFamilies.delete(f); });
      return h("div", { class: "gt-group" }, truthRow(item, f), toks.length ? d : null);
    }));
}
