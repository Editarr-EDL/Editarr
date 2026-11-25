/** Word or phrase rules of a `language` support entry (contract §5.2). */

/** Maximum expression length in Unicode code points after normalization. */
export const EXPRESSION_MAX_CODE_POINTS = 64;
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u;

/**
 * Normalize an entry expression: reject control (Cc), format (Cf) and lone surrogate (Cs)
 * characters, then apply NFC and trim surrounding white space. Returns null when the
 * result is not 1–64 code points or contained a forbidden character.
 */
export function normalizeExpression(value: string): string | null {
  if (FORBIDDEN.test(value)) return null;
  const normalized = value.normalize('NFC').trim();
  const length = [...normalized].length;
  return length >= 1 && length <= EXPRESSION_MAX_CODE_POINTS ? normalized : null;
}
