import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { base64, issueReceipt, type ReceiptSigner } from './receipts';
import { MAX_STORED_BYTES, readPublication, type PublicationMetadata, type PublicationParts } from './storage';

const positive = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
export const downloadLimitsSchema = z.strictObject({
  requestBytes: positive.max(8192), artifactBytes: positive.max(MAX_STORED_BYTES),
  manifestBytes: positive.max(MAX_STORED_BYTES), payloadBytes: positive.max(MAX_STORED_BYTES),
  responseBytes: positive.max(3_000_000), maxQueries: positive.max(24),
  cacheEntries: positive.max(64), cacheBytes: positive.max(16_000_000), cacheTtlSeconds: positive,
}).refine(value => value.artifactBytes + value.manifestBytes + 256 <= value.payloadBytes);
export type DownloadLimits = z.infer<typeof downloadLimitsSchema>;
export const downloadLimits = (value: unknown) => downloadLimitsSchema.parse(value);
export class DownloadError extends Error {
  constructor(public code: 'contribution_not_found' | 'contribution_unavailable' | 'historical_key_untrusted' |
    'download_limit' | 'artifact_unavailable' | 'key_inactive') { super(code); }
}
interface Revision {
  revision_id: string; contributor_id: string; key_id: string; artifact_schema: string; object_key: string;
  artifact_digest: string; manifest_digest: string; envelope_digest: string;
  artifact_bytes: number; manifest_bytes: number; envelope_bytes: number;
  state: string; contributor_state: string; public_key: number[]; key_state: 'active' | 'retired' | 'revoked';
}
type Entry = { createdAt: number; expiresAt: number; size: number; parts: PublicationParts };
const caches = new WeakMap<object, Map<string, Entry>>();

async function revision(db: SqlDatabase, revisionId: string) {
  const row = await db.prepare(`SELECT r.revision_id, r.contributor_id, r.key_id, r.artifact_schema, r.object_key,
    r.artifact_digest, r.manifest_digest, r.envelope_digest, r.artifact_bytes, r.manifest_bytes, r.envelope_bytes,
    p.state, c.state AS contributor_state, k.public_key, k.state AS key_state FROM contribution_revisions r
    JOIN contribution_publications p USING (revision_id) JOIN installation_keys k ON k.key_id = r.key_id
    JOIN contributors c ON c.contributor_id = r.contributor_id
    JOIN installations i ON i.installation_id = k.installation_id AND i.contributor_id = r.contributor_id
    WHERE r.revision_id = ?`).bind(revisionId).first<Revision>();
  if (!row) throw new DownloadError('contribution_not_found');
  if (row.state !== 'published' || row.contributor_state !== 'active') throw new DownloadError('contribution_unavailable');
  if (!['active', 'retired'].includes(row.key_state)) throw new DownloadError('historical_key_untrusted');
  if (new Uint8Array(row.public_key).length !== 32) throw new DownloadError('artifact_unavailable');
  return row;
}

async function payload(db: SqlDatabase, row: Revision, now: number, limits: DownloadLimits, scope: object) {
  const expected: PublicationMetadata = { artifact: { byteLength: row.artifact_bytes, digest: row.artifact_digest },
    manifest: { byteLength: row.manifest_bytes, digest: row.manifest_digest },
    envelope: { byteLength: row.envelope_bytes, digest: row.envelope_digest } };
  const size = row.artifact_bytes + row.manifest_bytes + row.envelope_bytes;
  if (row.artifact_bytes > limits.artifactBytes || row.manifest_bytes > limits.manifestBytes || row.envelope_bytes > 256 ||
      size > limits.payloadBytes) throw new DownloadError('download_limit');
  let cache = caches.get(scope);
  if (!cache) { cache = new Map(); caches.set(scope, cache); }
  for (const [key, entry] of cache)
    if (Math.min(entry.expiresAt, entry.createdAt + limits.cacheTtlSeconds) <= now) cache.delete(key);
  while (cache.size && (cache.size > limits.cacheEntries ||
      [...cache.values()].reduce((total, entry) => total + entry.size, 0) > limits.cacheBytes))
    cache.delete(cache.keys().next().value!);
  const cacheKey = JSON.stringify([row.revision_id, row.artifact_schema, expected]);
  const cached = cache.get(cacheKey);
  if (cached) return cached.parts;
  let parts: PublicationParts | null;
  try { parts = await readPublication(db, row.object_key, expected); }
  catch { throw new DownloadError('artifact_unavailable'); }
  if (!parts) throw new DownloadError('artifact_unavailable');
  while (cache.size && (cache.size >= limits.cacheEntries ||
      [...cache.values()].reduce((total, entry) => total + entry.size, 0) + size > limits.cacheBytes))
    cache.delete(cache.keys().next().value!);
  if (size <= limits.cacheBytes) cache.set(cacheKey, { parts, size, createdAt: now, expiresAt: now + limits.cacheTtlSeconds });
  return parts;
}

export async function downloadArtifact(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string,
  service: string, now: number, limits: DownloadLimits, signer: ReceiptSigner, cacheScope: object = db) {
  const row = await revision(db, revisionId);
  const parts = await payload(db, row, now, limits, cacheScope);
  const receipt = await issueReceipt(signer, { service, contributorId: identity.contributorId,
    revisionId: row.revision_id, artifactDigest: row.artifact_digest }, now);
  const current = await db.prepare(`SELECT p.state, c.state AS contributor_state, author.state AS author_state, author.public_key,
    EXISTS(SELECT 1 FROM installation_keys requester JOIN installations i USING (installation_id)
      JOIN contributors c USING (contributor_id) WHERE requester.key_id = ? AND requester.state = 'active'
      AND c.state = 'active' AND c.contributor_id = ?) AS authorized
    FROM contribution_publications p JOIN contribution_revisions r USING (revision_id)
    JOIN contributors c ON c.contributor_id = r.contributor_id
    JOIN installation_keys author ON author.key_id = r.key_id WHERE p.revision_id = ?`)
    .bind(identity.keyId, identity.contributorId, revisionId)
    .first<{ state: string; contributor_state: string; author_state: string; public_key: number[]; authorized: number }>();
  if (!current?.authorized) throw new DownloadError('key_inactive');
  if (current.state !== 'published' || current.contributor_state !== 'active') throw new DownloadError('contribution_unavailable');
  if (!['active', 'retired'].includes(current.author_state)) throw new DownloadError('historical_key_untrusted');
  if (base64(new Uint8Array(current.public_key)) !== base64(new Uint8Array(row.public_key))) throw new DownloadError('artifact_unavailable');
  return { version: 1, revisionId, artifactSchema: row.artifact_schema, artifactFormat: 'gzip-json',
    artifactDigest: row.artifact_digest, manifestDigest: row.manifest_digest, envelopeDigest: row.envelope_digest,
    artifact: base64(parts.artifact.bytes), manifest: base64(parts.manifest.bytes), envelope: base64(parts.envelope.bytes),
    historicalKey: { contributorId: row.contributor_id, keyId: row.key_id,
      publicKey: base64(new Uint8Array(row.public_key)), status: current.author_state }, receipt };
}