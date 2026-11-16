import { z } from 'zod';
import { matchEvidenceSchema } from './compatibility-inputs';

const positive = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
export const searchLimitsSchema = z.strictObject({
  requestBytes: positive, responseBytes: positive, maxItems: positive.max(50),
  maxKeys: positive, maxQueries: positive.max(12), maxCandidates: positive.max(96),
  maxRows: positive.max(500),
  maxPageSize: positive.max(96), cacheEntries: positive.max(128), cacheBytes: positive,
  positiveTtlSeconds: positive, negativeTtlSeconds: positive,
}).refine(value => value.maxPageSize <= value.maxCandidates && value.maxRows > value.maxCandidates &&
  value.negativeTtlSeconds < value.positiveTtlSeconds);
export type SearchLimits = z.infer<typeof searchLimitsSchema>;
export const searchLimits = (value: unknown) => searchLimitsSchema.parse(value);
const target = z.strictObject({ id: z.string().regex(/^[A-Za-z0-9_-]{1,64}$/), evidence: matchEvidenceSchema });
export const availabilitySchema = z.strictObject({
  version: z.literal(1), items: z.array(target).min(1).max(50), refresh: z.boolean().default(false),
}).refine(value => new Set(value.items.map(item => item.id)).size === value.items.length);
export const matchesSchema = z.strictObject({
  version: z.literal(1), evidence: matchEvidenceSchema, pageSize: positive.max(96),
  cursor: z.string().max(2048).nullable().default(null), refresh: z.boolean().default(false),
});
export type SearchTarget = z.infer<typeof target>;

export class SearchError extends Error {
  constructor(public code: 'lookup_limit' | 'invalid_cursor' | 'missing_identity' | 'invalid_lookup') { super(code); }
}