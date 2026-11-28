import { z } from 'zod';

/** A rejected insight query; the router answers 400 with `code` (for example `invalid_items_query`). */
export class InsightQueryError extends Error {
  constructor(public code: string) { super(code); }
}
/** A missing insight resource; the router answers 404 `resource_not_found`. */
export class InsightNotFound extends Error {
  constructor() { super('resource_not_found'); }
}

export const PAGE_SIZE = 20;
/** Catalog tables store Unix seconds; responses use epoch milliseconds. */
export const ms = (seconds: number | null | undefined) => seconds === null || seconds === undefined ? null : Number(seconds) * 1000;
export const idSchema = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);

/**
 * Parse `url`'s query with a strict object schema: unknown or repeated keys and invalid values
 * throw `InsightQueryError(code)`. Absent keys are `undefined`.
 */
export function parseQuery<T extends z.ZodRawShape>(url: URL, code: string, shape: T) {
  const keys = [...url.searchParams.keys()];
  if (new Set(keys).size !== keys.length) throw new InsightQueryError(code);
  const parsed = z.strictObject(shape).safeParse(Object.fromEntries(url.searchParams));
  if (!parsed.success) throw new InsightQueryError(code);
  return parsed.data;
}

/**
 * Cursor positions hold IDs (≤ 128 characters) or sort keys such as a lower-cased work title
 * (≤ 512 bytes, so ≤ 512 characters); the parameter bound covers JSON escaping of the worst case.
 */
const CURSOR_STRING_MAX = 512;
/** An opaque keyset cursor parameter: base64 of the UTF-8 JSON array of strings or integers. */
export const cursorParam = z.string().min(1).max(8192).regex(/^[A-Za-z0-9+/=]+$/);

export function encodeCursor(values: (string | number)[]) {
  return btoa(Array.from(new TextEncoder().encode(JSON.stringify(values)), byte => String.fromCharCode(byte)).join(''));
}

/** Decode a cursor whose positions are strings (`'s'`) or integers (`'n'`); malformed cursors throw `code`. */
export function decodeCursor(value: string | undefined, kinds: ('s' | 'n')[], code: string): (string | number)[] | null {
  if (value === undefined) return null;
  try {
    const bytes = Uint8Array.from(atob(value), char => char.charCodeAt(0));
    const decoded = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown;
    if (!Array.isArray(decoded) || decoded.length !== kinds.length) throw new Error();
    kinds.forEach((kind, index) => {
      const item = decoded[index];
      if (kind === 's' ? typeof item !== 'string' || item.length > CURSOR_STRING_MAX : !Number.isSafeInteger(item)) throw new Error();
    });
    return decoded as (string | number)[];
  } catch { throw new InsightQueryError(code); }
}

/** Trim a `LIMIT size + 1` result to a page and derive the next cursor from the last kept row. */
export function keysetPage<T>(rows: T[], size: number, key: (row: T) => (string | number)[]) {
  const items = rows.slice(0, size);
  return { items, nextCursor: rows.length > size ? encodeCursor(key(items[size - 1])) : null };
}

export const PERIOD_SECONDS = { '24h': 86_400, '7d': 7 * 86_400, '30d': 30 * 86_400 } as const;
export type Period = keyof typeof PERIOD_SECONDS;
