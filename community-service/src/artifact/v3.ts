import { z } from 'zod';
import taxonomy from './taxonomy.json';
import { evidenceKeySchema } from '../versions/keys';
import { normalizedTitle } from '../versions/normalize';
import { gunzipSingleMember, parseStrictJson, StrictJsonError } from './strict-json';

export const ARTIFACT_V3_SCHEMA = 'community-artifact-3';
export const MANIFEST_SCHEMA = 'community-manifest-1';

/** A refused submission artifact; `code` is the §7.2 error (`400 invalid_submission` or a `422` code). */
export class ArtifactV3Error extends Error {
  constructor(public code: 'invalid_submission' | 'work_identity_required' | 'invalid_taxonomy' | 'invalid_bounds' |
    'key_mismatch' | 'unknown_alias', message: string = code) { super(message); }
}

/** Resource bounds checked before the artifact JSON is parsed (§5.2). */
export interface ArtifactBounds { compressedBytes: number; expandedBytes: number; depth: number; stringBytes: number; manifestBytes: number }
export const DEFAULT_ARTIFACT_BOUNDS: Readonly<ArtifactBounds> = Object.freeze({
  compressedBytes: 1_000_000, expandedBytes: 4_000_000, depth: 8, stringBytes: 512, manifestBytes: 4096,
});

const families = taxonomy as Record<string, string[]>;
const familyOf = new Map(Object.entries(families).flatMap(([family, tokens]) => tokens.map(token => [token, family] as const)));
const MAX_DURATION_MS = 86_400_000;
const token = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
const keyId = z.string().regex(/^sha256:[0-9a-f]{64}$/);
const providerId = z.string().regex(/^[0-9]{1,12}$/).nullable();
const safe = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const interval = z.strictObject({ startMs: safe, endMs: safe });

export const artifactV3Schema = z.strictObject({
  schemaVersion: z.literal(ARTIFACT_V3_SCHEMA),
  submissionId: token,
  createdAt: safe,
  author: z.strictObject({ contributorId: token, keyId }),
  work: z.strictObject({
    kind: z.enum(['movie', 'episode']), tmdb: providerId, tvdb: providerId,
    season: z.number().int().min(0).max(9999).nullable(), episode: z.number().int().min(0).max(9999).nullable(),
    lastEpisode: z.number().int().min(0).max(9999 + 15).nullable(), title: z.string().min(1),
    year: z.number().int().min(1870).max(2200).nullable(),
  }),
  version: z.discriminatedUnion('choice', [
    z.strictObject({ choice: z.literal('alias'), aliasId: z.string().regex(/^[a-z0-9_]{1,64}$/) }),
    z.strictObject({ choice: z.literal('other'), text: z.string().refine(value => [...value].length <= 64).nullable() }),
  ]),
  media: z.strictObject({ durationMs: z.number().int().min(1).max(MAX_DURATION_MS), matchKeys: z.array(evidenceKeySchema).max(5) }),
  alignmentHint: z.strictObject({
    versionId: token, revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    offsetMs: z.number().int().min(-MAX_DURATION_MS).max(MAX_DURATION_MS), scale: z.number().finite().min(0.9).max(1.1),
    anchors: z.number().int().min(0).max(2000),
  }).nullable(),
  coverage: z.array(z.strictObject({ category: z.string(), intervals: z.array(interval).min(1).max(512) })).max(9),
  entries: z.array(z.strictObject({
    entryId: z.string().regex(/^[A-Za-z0-9_.-]{1,32}$/),
    kind: z.enum(['support', 'dispute', 'coverage_negative']),
    proposedFilterId: token.nullable(),
    startMs: safe, endMs: safe, category: z.string(), subcategory: z.string().nullable(),
    severity: z.enum(['low', 'medium', 'high']).nullable(), action: z.enum(['skip', 'mute']).nullable(),
    evidence: z.strictObject({ origin: z.enum(['model', 'manual', 'downloaded']), manualReview: z.boolean(),
      locallyVerified: z.boolean(), modelVersion: z.string().nullable() }),
  })).max(2000),
});
export type ArtifactV3 = z.infer<typeof artifactV3Schema>;

export const manifestV3Schema = z.strictObject({
  schema_version: z.literal(MANIFEST_SCHEMA), artifact_format: z.literal('gzip-json'),
  artifact_schema_version: z.literal(ARTIFACT_V3_SCHEMA), artifact_digest: z.string().regex(/^[0-9a-f]{64}$/),
  submission_id: token, contributor_id: token, key_id: keyId, summary: z.record(z.string(), z.unknown()),
});
export type ManifestV3 = z.infer<typeof manifestV3Schema>;

function checkWork(work: ArtifactV3['work']) {
  if (work.tmdb === null && work.tvdb === null) throw new ArtifactV3Error('work_identity_required');
  const numbering = [work.season, work.episode, work.lastEpisode];
  if (work.kind === 'movie' && numbering.some(value => value !== null)) throw new ArtifactV3Error('invalid_submission');
  if (work.kind === 'episode' && (work.season === null || work.episode === null ||
      (work.lastEpisode !== null && (work.lastEpisode < work.episode || work.lastEpisode > work.episode + 15))))
    throw new ArtifactV3Error('invalid_submission');
  if (new TextEncoder().encode(work.title).length > 512 || normalizedTitle(work.title) === null)
    throw new ArtifactV3Error('invalid_submission');
}

function checkBounds(start: number, end: number, duration: number) {
  if (!(start >= 0 && start < end && end <= duration)) throw new ArtifactV3Error('invalid_bounds');
}

/**
 * Apply the §5.2 rules a schema cannot express. Structural failures are `invalid_submission`;
 * taxonomy and time failures are their `422` codes.
 */
export function validateArtifactV3(artifact: ArtifactV3): ArtifactV3 {
  checkWork(artifact.work);
  const keys = artifact.media.matchKeys;
  if (new Set(keys.map(key => key.type)).size !== keys.length ||
      new Set(keys.flatMap(key => 'sizeBytes' in key ? [key.sizeBytes] : [])).size > 1) throw new ArtifactV3Error('invalid_submission');
  const duration = artifact.media.durationMs;
  if (new Set(artifact.coverage.map(item => item.category)).size !== artifact.coverage.length) throw new ArtifactV3Error('invalid_submission');
  for (const coverage of artifact.coverage) {
    if (!Object.hasOwn(families, coverage.category)) throw new ArtifactV3Error('invalid_taxonomy');
    let previousEnd = 0;
    for (const item of coverage.intervals) {
      checkBounds(item.startMs, item.endMs, duration);
      if (item.startMs < previousEnd) throw new ArtifactV3Error('invalid_bounds');
      previousEnd = item.endMs;
    }
  }
  const entryIds = new Set<string>(), proposed = new Set<string>();
  for (const entry of artifact.entries) {
    if (entryIds.has(entry.entryId)) throw new ArtifactV3Error('invalid_submission');
    entryIds.add(entry.entryId);
    if (entry.proposedFilterId !== null) {
      if (proposed.has(entry.proposedFilterId)) throw new ArtifactV3Error('invalid_submission');
      proposed.add(entry.proposedFilterId);
    } else if (entry.kind !== 'support') throw new ArtifactV3Error('invalid_submission');
    const classification = [entry.subcategory, entry.severity, entry.action];
    if (entry.kind === 'support' ? classification.includes(null) : classification.some(value => value !== null))
      throw new ArtifactV3Error('invalid_submission');
    const { modelVersion, origin } = entry.evidence;
    if ((modelVersion !== null && new TextEncoder().encode(modelVersion).length > 128) || (origin === 'model' && modelVersion === null))
      throw new ArtifactV3Error('invalid_submission');
    if (!Object.hasOwn(families, entry.category) || (entry.subcategory !== null && familyOf.get(entry.subcategory) !== entry.category))
      throw new ArtifactV3Error('invalid_taxonomy');
    checkBounds(entry.startMs, entry.endMs, duration);
  }
  return artifact;
}

export interface DecodedArtifactV3 { artifact: ArtifactV3; gzipBytes: Uint8Array; digest: string }

async function sha256Hex(bytes: Uint8Array) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
    byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Strictly decode exact gzip bytes, checking resource bounds before parsing the JSON (§5.1, §5.2). */
export async function decodeArtifactV3(gzipBytes: Uint8Array, bounds: ArtifactBounds = DEFAULT_ARTIFACT_BOUNDS): Promise<DecodedArtifactV3> {
  let value: unknown;
  try {
    const expanded = await gunzipSingleMember(gzipBytes, bounds.compressedBytes, bounds.expandedBytes);
    value = parseStrictJson(expanded, { maxBytes: bounds.expandedBytes, maxDepth: bounds.depth,
      maxStringBytes: bounds.stringBytes, fractionPaths: ['alignmentHint.scale'] });
  } catch (error) {
    if (error instanceof StrictJsonError) throw new ArtifactV3Error('invalid_submission', error.message);
    throw error;
  }
  const parsed = artifactV3Schema.safeParse(value);
  if (!parsed.success) throw new ArtifactV3Error('invalid_submission', parsed.error.issues[0]?.path.join('.'));
  return { artifact: validateArtifactV3(parsed.data), gzipBytes, digest: await sha256Hex(gzipBytes) };
}

/** Strictly parse manifest bytes and bind them to the decoded artifact's exact bytes and identity (§5.1). */
export function parseManifestV3(bytes: Uint8Array, decoded: DecodedArtifactV3, bounds: ArtifactBounds = DEFAULT_ARTIFACT_BOUNDS): ManifestV3 {
  let value: unknown;
  try { value = parseStrictJson(bytes, { maxBytes: bounds.manifestBytes, maxStringBytes: bounds.stringBytes }); }
  catch (error) {
    if (error instanceof StrictJsonError) throw new ArtifactV3Error('invalid_submission', error.message);
    throw error;
  }
  const parsed = manifestV3Schema.safeParse(value);
  if (!parsed.success) throw new ArtifactV3Error('invalid_submission', 'manifest');
  const manifest = parsed.data, artifact = decoded.artifact;
  if (Object.keys(manifest.summary).length || manifest.artifact_digest !== decoded.digest ||
      manifest.submission_id !== artifact.submissionId || manifest.contributor_id !== artifact.author.contributorId ||
      manifest.key_id !== artifact.author.keyId) throw new ArtifactV3Error('invalid_submission', 'manifest binding');
  return manifest;
}
