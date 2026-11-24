import { gzipSync } from 'node:zlib';
import type { ArtifactV3 } from '../../src/artifact/v3';
import { signatureBase } from '../../src/artifact/signatures';
import { manifestV3Schema } from '../../src/artifact/v3';
import { registerKey, signedPost, signedRead, type SigningKey } from './key-lifecycle';
import { db } from './versions';

export type Contributor = SigningKey & { contributorId: string; installationId: string; discordId: string };
export const service = 'https://community.example';
const encoder = new TextEncoder();
export const base64 = (bytes: Uint8Array) => Buffer.from(bytes).toString('base64');

export function hex(seed: string, length = 64) { return seed.repeat(length).slice(0, length); }

let sequence = 0;
/** A valid movie artifact for TMDB 603 with one violence support cue, overridable per field. */
export function artifact(author: Contributor, overrides: Partial<ArtifactV3> = {}): ArtifactV3 {
  sequence++;
  return {
    schemaVersion: 'community-artifact-3', submissionId: `sub_${sequence}_${crypto.randomUUID().slice(0, 8)}`, createdAt: 1_700_000_000,
    author: { contributorId: author.contributorId, keyId: author.keyId },
    work: { kind: 'movie', tmdb: '603', tvdb: null, season: null, episode: null, lastEpisode: null, title: 'The Matrix', year: 1999 },
    version: { choice: 'alias', aliasId: 'theatrical' },
    media: { durationMs: 8_160_000, matchKeys: [{ type: 'md5_quick', value: hex('a', 32), sizeBytes: 1000 }] },
    alignmentHint: null,
    coverage: [{ category: 'violence', intervals: [{ startMs: 0, endMs: 8_160_000 }] }],
    entries: [support('e1', 600_000, 605_000)],
    ...overrides,
  };
}

export function support(entryId: string, startMs: number, endMs: number, fields: Record<string, unknown> = {}): ArtifactV3['entries'][number] {
  return { entryId, kind: 'support', proposedFilterId: null, startMs, endMs, category: 'violence', subcategory: 'punching',
    severity: 'medium', action: 'skip', evidence: { origin: 'manual', manualReview: true, locallyVerified: true, modelVersion: null },
    ...fields } as ArtifactV3['entries'][number];
}

export interface EncodedSubmission { artifact: ArtifactV3; artifactBytes: Uint8Array; manifestBytes: Uint8Array; envelopeBytes: Uint8Array }

/** Gzip, manifest and sign an artifact with the author's key (§5.1). */
export async function encode(value: ArtifactV3, author: Pick<Contributor, 'privateKey'>, json = JSON.stringify(value)): Promise<EncodedSubmission> {
  const artifactBytes = new Uint8Array(gzipSync(encoder.encode(json)));
  const digest = Buffer.from(await crypto.subtle.digest('SHA-256', artifactBytes)).toString('hex');
  const manifest = { schema_version: 'community-manifest-1', artifact_format: 'gzip-json', artifact_schema_version: 'community-artifact-3',
    artifact_digest: digest, submission_id: value.submissionId, contributor_id: value.author.contributorId, key_id: value.author.keyId, summary: {} };
  const manifestBytes = encoder.encode(JSON.stringify(manifest));
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', author.privateKey,
    await signatureBase(manifestBytes, manifestV3Schema.parse(manifest)) as BufferSource));
  const envelopeBytes = encoder.encode(JSON.stringify({ schema_version: 'community-artifact-signature-1', signature: base64(signature) }));
  return { artifact: value, artifactBytes, manifestBytes, envelopeBytes };
}

export function body(encoded: EncodedSubmission, submissionId = encoded.artifact.submissionId) {
  return JSON.stringify({ version: 1, submissionId, artifact: base64(encoded.artifactBytes),
    manifest: base64(encoded.manifestBytes), envelope: base64(encoded.envelopeBytes) });
}

/** A signed `POST /v1/submissions` request with the contract idempotency key. */
export async function submitRequest(author: Contributor, encoded: EncodedSubmission, options: { operationId?: string; text?: string } = {}) {
  return signedPost(author, '/v1/submissions', options.text ?? body(encoded),
    options.operationId ?? `submit-${encoded.artifact.submissionId}`, undefined, nonce());
}

export function nonce() {
  return Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64url');
}

export async function contributor(id?: string): Promise<Contributor> {
  return registerKey(db, id as ReturnType<typeof crypto.randomUUID> | undefined) as Promise<Contributor>;
}

export { signedPost, signedRead };
