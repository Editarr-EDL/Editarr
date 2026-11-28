/* Which items count as sensitive (nudity/sex); the views blur their media and hide their text. */
import { S } from "./data.js";
import { familyOf } from "./scoring.js";

const SENSITIVE_FAMILIES = ["nudity", "sex"];

export const isSensitiveKey = (k) => SENSITIVE_FAMILIES.includes(familyOf(k));

/**
 * True when effective (corrected) expected labels, film source tags (e.g. immodesty/nudity_without_sex) or any
 * loaded model's flags involve nudity/sex. Callers must wait for every model (data.allLoaded) before trusting false.
 */
export function isSensitive(item) {
  if ((item.expected || []).some(isSensitiveKey)) return true;
  if ((item.source_tags || []).some((t) => /nud|sex/i.test(t))) return true;
  return S.modelIds.some((id) => {
    const r = S.results[id]?.get(item.id);
    return !!r && (r.flagged.some(isSensitiveKey) || !!r.sample?.flagged.some(isSensitiveKey));
  });
}
