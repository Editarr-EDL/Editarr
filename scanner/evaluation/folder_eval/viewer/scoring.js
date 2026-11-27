/* Clip-level ground-truth and outcome rules shared by the views. */
import { S } from "./data.js";

export const OUTCOMES = ["TP", "FP", "FN", "TN", "unscored"];
export const outcomeClass = (o) => o.replace(/\s+/g, "").toLowerCase();
export const familyOf = (key) => key.split("/")[0];

/** Expected positive: exact token, or for a family key the family or any of its tokens. */
export function expectedPositive(item, key) {
  const exp = item.expected || [];
  if (key.includes("/")) return exp.includes(key);
  return exp.some((k) => k === key || k.startsWith(key + "/"));
}

export function predicted(res, key) {
  if (!res || res.status !== "ok") return false;
  if (key.includes("/")) return res.flagged.includes(key);
  return res.flagged.some((k) => k === key || k.startsWith(key + "/"));
}

/**
 * Whether a model scores a key, as in summary.py: a family or token must be listed in meta.supports, so a model
 * supporting only fear/hemophobia scores that token but not the fear family. Empty/missing supports means every key.
 */
export function supportsKey(meta, key) {
  const sup = meta && meta.supports;
  if (!Array.isArray(sup) || !sup.length) return true;
  return sup.includes(key);
}

/** Keys a model is judged on: its supported families and tokens (every family when supports is unknown). */
export function scorableKeys(meta) {
  const sup = meta && meta.supports;
  return Array.isArray(sup) && sup.length ? sup : S.familyOrder;
}

/** Decision models record the keys they were asked; a family counts as asked if it or any of its tokens was. */
export function wasAsked(res, key) {
  if (!res || !res.asked) return true;
  if (res.asked.has(key)) return true;
  if (key.includes("/")) return false;
  for (const k of res.asked) if (k.startsWith(key + "/")) return true;
  return false;
}

/**
 * TP/FP/FN/TN for one key, or "unscored" for unscored items, unsupported keys and errors.
 * Like summary.json, a key a decision model was not asked counts as a negative prediction (FN or TN);
 * views show that via wasAsked() as a "not asked" label.
 */
export function outcomeFor(item, res, key, meta) {
  if (!item.scored || !res || res.status !== "ok" || !supportsKey(meta, key)) return "unscored";
  const p = predicted(res, key), e = expectedPositive(item, key);
  return p ? (e ? "TP" : "FP") : (e ? "FN" : "TN");
}

/** Item-level correctness: every scorable key agrees with ground truth (null when not scorable). */
export function itemCorrect(item, res, meta) {
  if (!item.scored || !res || res.status !== "ok") return null;
  const keys = scorableKeys(meta);
  if (!keys.length) return null;
  return keys.every((k) => predicted(res, k) === expectedPositive(item, k));
}

export function maxScore(res) {
  if (!res || !res.scores) return 0;
  let m = 0;
  for (const v of Object.values(res.scores)) if (v > m) m = v;
  return m;
}
