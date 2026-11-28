import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { signatureBase, verifySubmission } from '../artifact/signatures';
import type { ArtifactV3, ManifestV3 } from '../artifact/v3';
import type { SqlDatabase } from '../runtime/contracts';
import type { StoredSubmission } from '../submissions/store';
import type { SeedRecord, SeedSource } from './input';

export const sha256 = (input: string | Uint8Array) => createHash('sha256').update(input).digest('hex');

export async function sourceSigner(source: SeedSource) {
  const contributorId = 'external-seed-' + source.toLowerCase();
  const installationId = contributorId;
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const keyId = 'sha256:' + sha256(publicKey);
  return { contributorId, installationId, publicKey, keyId, privateKey: pair.privateKey };
}

type Signer = Awaited<ReturnType<typeof sourceSigner>>;

/** Seed principals exist for attribution and moderation. They have no Discord identity or active installation key. */
export async function registerSource(db: SqlDatabase, source: SeedSource, signer: Signer, now: number) {
  const { contributorId, installationId, keyId, publicKey } = signer;
  const displayName = source + ' external archive';
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, state, created_at)
      VALUES (?1, ?1, ?, ?, 'suspended', ?) ON CONFLICT(contributor_id) DO NOTHING`)
      .bind(contributorId, displayName, displayName.toLowerCase(), now),
    db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
      SELECT ?1, contributor_id, ?2 FROM contributors WHERE contributor_id = ?3 AND discord_user_id = ?3 AND state = 'suspended'
      ON CONFLICT(installation_id) DO NOTHING`).bind(installationId, now, contributorId),
    db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at, retired_at)
      SELECT ?, installation_id, 'Ed25519', ?, 'revoked', ?3, ?3 FROM installations
      WHERE installation_id = ? AND contributor_id = ?`).bind(keyId, publicKey, now, installationId, contributorId),
  ]);
  if (!await db.prepare('SELECT 1 AS found FROM installation_keys WHERE key_id = ?').bind(keyId).first())
    throw new Error('external_seed_source_identity_conflict');
}

/** Encode and validate an attributed archive using the normal artifact validator. Its match keys and coverage are empty. */
export async function archiveSeed(record: SeedRecord, source: SeedSource, signer: Signer,
  submissionId: string, versionId: string, now: number): Promise<StoredSubmission> {
  // The archive format requires a positive bound. An unknown runtime stays NULL in the seed metadata and derived version.
  const durationMs = record.sourceRuntimeMs ?? Math.max(...record.filters.map(filter => filter.endMs));
  const artifact: ArtifactV3 = {
    schemaVersion: 'community-artifact-3', submissionId, createdAt: now,
    author: { contributorId: signer.contributorId, keyId: signer.keyId },
    work: { ...record.work, lastEpisode: null }, version: { choice: 'other', text: source },
    media: { durationMs, matchKeys: [] }, alignmentHint: null, coverage: [],
    entries: record.filters.map(({ sourceCueIndex: _sourceCueIndex, ...filter }, i) => ({
      ...filter, entryId: String(i + 1), proposedFilterId: null, kind: 'support',
      evidence: { origin: 'downloaded', locallyVerified: false, manualReview: false, modelVersion: null },
    })),
  };
  const artifactBytes = gzipSync(JSON.stringify(artifact));
  const digest = sha256(artifactBytes);
  const manifest: ManifestV3 = {
    schema_version: 'community-manifest-1', artifact_format: 'gzip-json', artifact_schema_version: 'community-artifact-3',
    artifact_digest: digest, submission_id: submissionId, contributor_id: signer.contributorId, key_id: signer.keyId, summary: {},
  };
  const manifestBytes = Buffer.from(JSON.stringify(manifest));
  const message = await signatureBase(manifestBytes, manifest);
  const signature = await crypto.subtle.sign('Ed25519', signer.privateKey, message as BufferSource);
  const envelopeBytes = Buffer.from(JSON.stringify({
    schema_version: 'community-artifact-signature-1', signature: Buffer.from(signature).toString('base64'),
  }));
  await verifySubmission(artifactBytes, manifestBytes, envelopeBytes, signer);
  return {
    submissionId, digest, contributorId: signer.contributorId, installationId: signer.installationId, keyId: signer.keyId,
    versionId, tier: null, artifact, artifactBytes, manifestBytes, envelopeBytes,
  };
}
