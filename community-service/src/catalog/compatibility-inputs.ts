import { z } from 'zod';
import { AUDIO_ALGORITHM, PCM_AUDIO_ALGORITHM, VIDEO_ALGORITHM, streamEvidenceSchema } from './stream-compatibility';

export const POLICY_VERSION = 'community-compatibility-stream-1';
export const DURATION_TOLERANCE_SECONDS = 2;
export const FULL_ALGORITHM = 'sha256-full-1';
export const SAMPLED_ALGORITHM = 'editarr-community-file-sha256-sampled-1';
export const BANDS = ['exact_file', 'strong_fingerprint', 'release_match', 'possible_cut', 'title_only', 'incompatible'] as const;
export type Band = typeof BANDS[number];
const integer = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const ARR_PROVENANCE = /^(radarr|sonarr)\./i;
const ARR_DURATION_SOURCES = new Set(['radarr_catalog_minutes', 'sonarr_catalog_minutes']);
const label = z.string().max(256).refine(value => !/[\\/]/.test(value));
const identifier = z.strictObject({
  provider: z.enum(['tmdb', 'tvdb']), entity_type: z.enum(['movie', 'series', 'episode']),
  value: z.string().regex(/^[a-zA-Z0-9]{1,64}$/),
  provenance: z.string().min(1).max(64).refine(value => !ARR_PROVENANCE.test(value)),
});
const number = z.strictObject({
  scheme: z.enum(['aired', 'absolute', 'dvd', 'anime']), season: integer.nullable(), number: integer,
}).refine(item => !['aired', 'dvd'].includes(item.scheme) || item.season !== null);
const fingerprint = z.strictObject({
  algorithm: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/),
  digest: z.string().regex(/^[a-f0-9]{1,128}$/), size_bytes: integer,
}).refine(item => ![FULL_ALGORITHM, SAMPLED_ALGORITHM].includes(item.algorithm) || item.digest.length === 64);
const media = z.strictObject({
  entity_type: z.enum(['movie', 'series', 'episode']), identifiers: z.array(identifier).max(16),
  series_identifiers: z.array(identifier).max(16).default([]),
  episode_numbers: z.array(number).max(32).default([]), episode_span: z.array(number).max(32).default([]),
  release: z.strictObject({
    scene_name: label.nullable().default(null), group: label.nullable().default(null),
    edition: label.nullable().default(null), quality: label.nullable().default(null),
    size_bytes: integer.nullable().default(null),
    duration: z.strictObject({
      seconds: z.number().positive().finite(),
      source: z.enum(['file_probe', 'jellyfin_ticks']),
      precision_seconds: z.number().nonnegative().finite().nullable().default(null),
    }).nullable().default(null),
    audio: z.strictObject({
      language: z.string().regex(/^[a-zA-Z0-9-]{1,64}$/).nullable(),
      role: z.enum(['main', 'commentary', 'descriptive', 'unknown']),
      codec: z.string().nullable().default(null), channels: integer.nullable().default(null),
    }).nullable().default(null),
  }),
  input_requirement: z.string().nullable().default(null),
});

export const matchEvidenceSchema = z.strictObject({
  media, fingerprints: z.array(fingerprint).max(8).default([]),
  video_stream: streamEvidenceSchema.nullable().default(null),
  audio_stream: streamEvidenceSchema.nullable().default(null),
})
  .superRefine((input, context) => {
    const fail = (message: string) => context.addIssue({ code: 'custom', message });
    if (input.video_stream?.algorithm === AUDIO_ALGORITHM || input.video_stream?.algorithm === PCM_AUDIO_ALGORITHM ||
      input.audio_stream?.algorithm === VIDEO_ALGORITHM)
      fail('Stream fingerprint channel mismatch');
    for (const [identifiers, kind] of [[input.media.identifiers, input.media.entity_type],
      [input.media.series_identifiers, 'series']] as const) {
      if (identifiers.some(item => item.entity_type !== kind)) fail('Identifier namespace mismatch');
      if (new Set(identifiers.map(item => `${item.provider}:${item.entity_type}`)).size !== identifiers.length)
        fail('Duplicate identifier namespace');
    }
    for (const numbers of [input.media.episode_numbers, input.media.episode_span]) {
      if (new Set(numbers.map(item => `${item.scheme}:${item.season}:${item.number}`)).size !== numbers.length)
        fail('Duplicate episode number');
    }
    if (new Set(input.fingerprints.map(item => item.algorithm)).size !== input.fingerprints.length)
      fail('Duplicate fingerprint algorithm');
    const sizes = new Set(input.fingerprints.filter(item => [FULL_ALGORITHM, SAMPLED_ALGORITHM].includes(item.algorithm))
      .map(item => item.size_bytes));
    if (input.media.release.size_bytes !== null) sizes.add(input.media.release.size_bytes);
    if (sizes.size > 1) fail('Contradictory file size evidence');
  });

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const withoutArrIdentifiers = (items: unknown) => !Array.isArray(items) ? items : items.map(item =>
  isRecord(item) && typeof item.provenance === 'string' && ARR_PROVENANCE.test(item.provenance)
    ? { ...item, provenance: 'source_metadata' } : item);

/** Reads `matching` from signed bytes stored before migration 0017 the way 0017 rewrote the catalog copies. */
export function withoutArrProvenance<T>(record: T): T {
  if (!isRecord(record) || !isRecord(record.matching) || !isRecord(record.matching.media)) return record;
  const media = { ...record.matching.media };
  for (const key of ['identifiers', 'series_identifiers'])
    if (Object.hasOwn(media, key)) media[key] = withoutArrIdentifiers(media[key]);
  const release = media.release;
  if (isRecord(release) && isRecord(release.duration) && ARR_DURATION_SOURCES.has(String(release.duration.source)))
    media.release = { ...release, duration: { ...release.duration, source: 'jellyfin_ticks' } };
  return { ...record, matching: { ...record.matching, media } };
}
export type MatchEvidence = z.infer<typeof matchEvidenceSchema>;
export type MatchEvidenceInput = z.input<typeof matchEvidenceSchema>;
export type Identifier = MatchEvidence['media']['identifiers'][number];
export type EpisodeNumber = MatchEvidence['media']['episode_numbers'][number];
export interface ChannelCompatibility {
  band: Band; reasons: string[]; conflicts: string[]; missing: string[]; strong_eligible: boolean;
}
export interface Compatibility {
  policy_version: string; video: ChannelCompatibility; audio: ChannelCompatibility; selected: false;
}
