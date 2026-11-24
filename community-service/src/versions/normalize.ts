/** Normalize a title, alias spelling or Other text exactly as contract §3.3 specifies. */
export function normalize(value: string): string {
  return value.normalize('NFKD')
    .replace(/\p{Mn}/gu, '')
    .toLowerCase()
    .replace(/['\u2019\u02BC`]/g, '')
    .replace(/[^\p{L}\p{N}]/gu, ' ')
    .replace(/ {2,}/g, ' ')
    .trim()
    .normalize('NFC');
}

/** Return the normalized title, or null when it is not 1–256 code points (§3.3). */
export function normalizedTitle(title: string): string | null {
  const value = normalize(title), length = [...value].length;
  return length >= 1 && length <= 256 ? value : null;
}
