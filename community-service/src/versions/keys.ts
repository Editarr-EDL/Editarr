import { z } from 'zod';

export const AUDIO_ALGORITHMS = ['editarr-community-audio-pcm-s16le-16000-mono-sha256-1',
  'editarr-community-audio-sha256-sampled-1'] as const;
export const VIDEO_ALGORITHMS = ['editarr-community-video-sha256-sampled-1'] as const;
export const EVIDENCE_KEY_TYPES = ['sha256_full', 'sha256_sampled', 'md5_quick', 'audio_stream', 'video_stream'] as const;
export type EvidenceKeyType = typeof EVIDENCE_KEY_TYPES[number];
export type EvidenceTier = 1 | 2 | 3;
export const KEY_TIER: Record<EvidenceKeyType, EvidenceTier> = {
  sha256_full: 1, sha256_sampled: 2, md5_quick: 2, audio_stream: 3, video_stream: 3,
};

const hex64 = z.string().regex(/^[0-9a-f]{64}$/);
const sizeBytes = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const providerId = z.string().regex(/^[0-9]{1,12}$/);
const numbering = z.number().int().min(0).max(9999);

/** Tier 1–3 key shapes from §3.2, shared by artifacts and match queries. */
export const evidenceKeySchema = z.discriminatedUnion('type', [
  z.strictObject({ type: z.literal('sha256_full'), value: hex64, sizeBytes }),
  z.strictObject({ type: z.literal('sha256_sampled'), value: hex64, sizeBytes }),
  z.strictObject({ type: z.literal('md5_quick'), value: z.string().regex(/^[0-9a-f]{32}$/), sizeBytes }),
  z.strictObject({ type: z.literal('audio_stream'), algorithm: z.enum(AUDIO_ALGORITHMS), value: hex64 }),
  z.strictObject({ type: z.literal('video_stream'), algorithm: z.enum(VIDEO_ALGORITHMS), value: hex64 }),
]);
export type EvidenceKey = z.infer<typeof evidenceKeySchema>;

function providerKey<T extends 'tmdb' | 'tvdb'>(type: T) {
  return z.union([
    z.strictObject({ type: z.literal(type), kind: z.literal('movie'), id: providerId }),
    z.strictObject({ type: z.literal(type), kind: z.literal('episode'), id: providerId, season: numbering,
      episode: numbering, lastEpisode: numbering.nullable().optional() })
      .refine(key => key.lastEpisode == null || (key.lastEpisode >= key.episode && key.lastEpisode <= key.episode + 15)),
  ]);
}
export const providerKeySchema = z.union([providerKey('tmdb'), providerKey('tvdb')]);
export type ProviderKey = z.infer<typeof providerKeySchema>;
export const titleYearKeySchema = z.strictObject({ type: z.literal('title_year'),
  title: z.string().refine(value => { const bytes = new TextEncoder().encode(value).length; return bytes >= 1 && bytes <= 512; }),
  year: z.number().int().min(1870).max(2200).nullable() });
export type TitleYearKey = z.infer<typeof titleYearKeySchema>;

/** The `version_match_keys` tuple; absent algorithm is `''` and absent size is `-1`. */
export interface StoredKey { type: EvidenceKeyType; algorithm: string; value: string; sizeBytes: number }

export function storedKey(key: EvidenceKey): StoredKey {
  return 'algorithm' in key ? { type: key.type, algorithm: key.algorithm, value: key.value, sizeBytes: -1 }
    : { type: key.type, algorithm: '', value: key.value, sizeBytes: key.sizeBytes };
}

export function sameKey(left: StoredKey, right: StoredKey): boolean {
  return left.type === right.type && left.algorithm === right.algorithm && left.value === right.value &&
    left.sizeBytes === right.sizeBytes;
}

/** Best (lowest) tier at which any key of `left` equals a key of `right`, or null. */
export function bestEvidenceTier(left: readonly StoredKey[], right: readonly StoredKey[]): EvidenceTier | null {
  let best: EvidenceTier | null = null;
  for (const key of left) {
    if (right.some(other => sameKey(key, other)) && (best === null || KEY_TIER[key.type] < best)) best = KEY_TIER[key.type];
  }
  return best;
}

/** Work identifier columns; absent numbering is stored as -1 (§8). */
export interface IdentifierRow { provider: 'tmdb' | 'tvdb'; kind: 'movie' | 'episode'; value: string;
  season: number; episode: number; lastEpisode: number }

export function identifierRow(provider: 'tmdb' | 'tvdb', kind: 'movie' | 'episode', value: string,
  season: number | null = null, episode: number | null = null, lastEpisode: number | null = null): IdentifierRow {
  return kind === 'movie' ? { provider, kind, value, season: -1, episode: -1, lastEpisode: -1 }
    : { provider, kind, value, season: season ?? -1, episode: episode ?? -1, lastEpisode: lastEpisode ?? -1 };
}
