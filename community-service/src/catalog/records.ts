import { matchEvidenceSchema, type Identifier } from './compatibility-inputs';

export const SQL_BIND_LIMIT = 100;
export function positive(value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('invalid_catalog_limit');
  return value;
}
export function timestamp(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error('invalid_catalog_timestamp');
  return value;
}
export function token(value: string): string {
  if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(value)) throw new Error('invalid_catalog_id');
  return value;
}
export function identifier(value: Identifier): Identifier {
  return matchEvidenceSchema.parse({ media: { entity_type: value.entity_type,
    identifiers: [value], release: {} } }).media.identifiers[0];
}
export async function sha256(bytes: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
    byte => byte.toString(16).padStart(2, '0')).join('');
}
export function boundedJson(value: unknown, maxBytes: number): string {
  const text = JSON.stringify(value);
  if (new TextEncoder().encode(text).length > positive(maxBytes)) throw new Error('catalog_json_limit');
  return text;
}