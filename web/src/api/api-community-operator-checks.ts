/** Strict structural checks for operator API responses; every object must match its exact field set. */
export type Check = (value: unknown) => boolean;
export const text: Check = value => typeof value === "string" && value.length <= 1000;
export const identifier: Check = value => typeof value === "string" && /^[A-Za-z0-9_.-]{1,128}$/.test(value);
/** An attestation target: `submissionId:entryId`. */
export const attestationIdentifier: Check = value => typeof value === "string" && /^[A-Za-z0-9_.-]{1,128}:[A-Za-z0-9_.-]{1,32}$/.test(value);
export const aliasIdentifier: Check = value => typeof value === "string" && /^[a-z0-9_]{1,64}$/.test(value);
export const number: Check = value => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export const integer: Check = value => typeof value === "number" && Number.isSafeInteger(value);
export const fraction: Check = value => typeof value === "number" && value >= 0 && value <= 1;
export const boolean: Check = value => typeof value === "boolean";
export const flag: Check = value => value === 0 || value === 1;
export const nullable = (check: Check): Check => value => value === null || check(value);
export const literal = (expected: unknown): Check => value => value === expected;
export const oneOf = (...values: string[]): Check => value => typeof value === "string" && values.includes(value);
/** An object with exactly the `shape` keys, plus any present `optional` keys. */
export const fields = (shape: Record<string, Check>, optional: Record<string, Check> = {}): Check => value => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Object.keys(record).every(key => key in shape || key in optional) &&
    Object.entries(shape).every(([key, check]) => key in record && check(record[key])) &&
    Object.entries(optional).every(([key, check]) => !(key in record) || check(record[key]));
};
export const array = (check: Check, max = 20): Check => value => Array.isArray(value) && value.length <= max && value.every(check);
export const reasonText = (value: string) => !!value.trim() && value.length <= 1000 && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
/** A plain object of at most `max` scalar (string, number, boolean or null) values, such as run details. */
export const scalars = (max = 50): Check => value => !!value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).length <= max && Object.values(value).every(entry => entry === null || typeof entry === "boolean" ||
    (typeof entry === "number" && Number.isFinite(entry)) || (typeof entry === "string" && entry.length <= 1000));
export const finite: Check = value => typeof value === "number" && Number.isFinite(value);
/** A boolean, also accepted as a SQLite 0/1 flag; read it with `!!value`. */
export const bool: Check = value => typeof value === "boolean" || value === 0 || value === 1;
const scalar = (entry: unknown) => entry === null || typeof entry === "boolean" ||
  (typeof entry === "number" && Number.isFinite(entry)) || (typeof entry === "string" && entry.length <= 1000);
/** Run details: a plain object of at most 50 scalars or short scalar arrays. */
export const details: Check = value => !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length <= 50 &&
  Object.values(value).every(entry => scalar(entry) || (Array.isArray(entry) && entry.length <= 50 && entry.every(scalar)));
