import { z } from 'zod';
import { evidenceKeySchema, providerKeySchema, titleYearKeySchema } from '../versions/keys';
import { normalizedTitle } from '../versions/normalize';

/** Maximum `POST /v1/match` body; 12 keys of at most ~600 bytes each fit comfortably. */
export const MATCH_REQUEST_BYTES = 16_384;

export class MatchError extends Error {
  constructor(public code: 'invalid_match') { super(code); }
}

const queryKey = z.union([evidenceKeySchema, providerKeySchema,
  titleYearKeySchema.refine(key => normalizedTitle(key.title) !== null)]);
const label = z.union([
  z.strictObject({ aliasId: z.string().regex(/^[a-z0-9_]{1,64}$/) }),
  z.strictObject({ text: z.string().refine(value => [...value].length <= 64) }),
  z.null(),
]);

/** `POST /v1/match` request (§7.1). */
export const matchRequestSchema = z.strictObject({
  version: z.literal(1),
  keys: z.array(queryKey).min(1).max(12).refine(keys => {
    const counts = new Map<string, number>();
    for (const key of keys) counts.set(key.type, (counts.get(key.type) ?? 0) + 1);
    return [...counts.values()].every(count => count <= 2);
  }),
  label,
  runtimeMs: z.number().int().min(1).max(86_400_000).nullable(),
  limit: z.number().int().min(1).max(50).default(20),
});
export type MatchRequest = z.infer<typeof matchRequestSchema>;

export function parseMatchRequest(body: Uint8Array): MatchRequest {
  try { return matchRequestSchema.parse(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body))); }
  catch { throw new MatchError('invalid_match'); }
}
