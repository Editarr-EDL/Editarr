import { z } from 'zod';

export const seedSource = z.enum(['VideoSkip', 'Filmiway']);
export type SeedSource = z.infer<typeof seedSource>;
const providerId = z.string().regex(/^[1-9][0-9]{0,11}$/).nullable();
const index = z.number().int().min(0).max(9999);
const timestamp = z.number().int().min(0).max(86_400_000);

const record = z.strictObject({
  sourceRecordId: z.string().regex(/^[a-z0-9:_.-]{1,128}$/),
  work: z.strictObject({
    kind: z.enum(['movie', 'episode']), tmdb: providerId, tvdb: providerId,
    season: index.nullable(), episode: index.nullable(), title: z.string().min(1).max(256),
    year: z.number().int().min(1870).max(2200).nullable(),
  }),
  sourceRuntimeMs: timestamp.min(1).nullable(),
  provenance: z.strictObject({
    attribution: z.record(z.string(), z.unknown()),
    sync: z.record(z.string(), z.unknown()).nullable(),
    match: z.record(z.string(), z.unknown()),
  }),
  filters: z.array(z.strictObject({
    startMs: timestamp, endMs: timestamp, category: z.string(), subcategory: z.string(),
    severity: z.enum(['low', 'medium', 'high']), action: z.enum(['mute', 'skip']),
    sourceCueIndex: z.number().int().nonnegative(),
  })).min(1).max(2000),
});
export type SeedRecord = z.infer<typeof record>;
export const seedBundle = z.strictObject({
  schemaVersion: z.literal('editarr-community-seed-1'),
  source: seedSource,
  records: z.array(record).min(1).max(100_000),
}).superRefine(({ source, records }, context) => {
  const ids = new Set<string>();
  for (const [i, record] of records.entries()) {
    if (!record.sourceRecordId.startsWith(source.toLowerCase() + ':') || ids.has(record.sourceRecordId)) {
      context.addIssue({ code: 'custom', path: ['records', i, 'sourceRecordId'], message: 'invalid_or_duplicate_seed_record_id' });
    }
    ids.add(record.sourceRecordId);
  }
});
