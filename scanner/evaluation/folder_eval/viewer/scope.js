/* Scope handling: "all" items vs the decision sample (subset items, scored over subset frames only). */
import { S } from "./data.js";
import { parseHash } from "./router.js";

const DECISION_MODELS = ["d1-omni-2b", "clef-flash", "d1-3b"];
const scopedCache = {};

export const hasSample = () => !!S.subset;

/** Decision models only ran on the sample: known ids, meta.kind === "decision", or frames carrying `asked`. */
export function isDecisionModel(id) {
  const meta = S.metas[id] || {};
  return DECISION_MODELS.includes(id) || meta.kind === "decision" || meta.decision === true || S.askedModels.has(id);
}

/** Scope from the hash (?scope=all|sample); defaults to the sample when a decision model is being viewed or compared. */
export function getScope(route = parseHash()) {
  if (!hasSample()) return "all";
  const explicit = route.params.get("scope");
  if (explicit === "all" || explicit === "sample") return explicit;
  if (route.view === "folder") return S.modelIds.some(isDecisionModel) ? "sample" : "all";
  const id = S.modelIds.includes(route.arg) ? route.arg : S.modelIds[0];
  return id && isDecisionModel(id) ? "sample" : "all";
}

/** Inventory items in the scope (cached). */
export function scopedItems(scope) {
  if (scope !== "sample") return S.items;
  return (scopedCache.sample ||= S.items.filter((i) => S.subset.ids.has(i.id)));
}

/** A model's result for an item in the scope; sample results are recomputed over subset frames at load time. */
export function resultFor(id, itemId, scope) {
  const r = S.results[id]?.get(itemId);
  if (!r) return undefined;
  return scope === "sample" ? r.sample ?? undefined : r;
}

/** Thumbnail frame: the first subset frame in sample scope, otherwise the first frame. */
export function defaultFrame(item, scope) {
  if (scope !== "sample") return undefined;
  const f = S.subset.frames.get(item.id);
  return f && f.size ? Math.min(...f) : undefined;
}

export const inSampleFrame = (item, n) => !!S.subset?.frames.get(item.id)?.has(n);
