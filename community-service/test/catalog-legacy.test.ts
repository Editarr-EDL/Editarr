import { beforeAll, expect, it } from 'vitest';
import { parseArtifact } from '../src/artifact/codec';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { reserveRevision } from '../src/contributions/repository';
import gzip from './fixtures/artifact-v1-gzip.json';
import signatures from './fixtures/artifact-signatures-v1.json';
import { db, decode, fixtureOperation, limits, setupCatalog } from './helpers/catalog';

beforeAll(async () => {
  await setupCatalog();
  await db.prepare(`UPDATE installation_keys SET public_key = ? WHERE key_id = 'key-1'`)
    .bind(decode(signatures.original.public_key_base64)).run();
});
it('indexes the unchanged original v1 signature and summary without guessed audio proof', async () => {
  const bounds = { compressedBytes: 4096, expandedBytes: 16384, events: 10, text: 256, depth: 12 };
  const bytes = decode(gzip.base64), parsed = await parseArtifact(bytes, bounds, true);
  const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));
  const manifest = encode({ schema_version: 'community-manifest-1', artifact_format: 'gzip-json',
    artifact_schema_version: 'community-artifact-1', artifact_digest: signatures.original.artifact_sha256,
    submission_id: parsed.artifact.submission_id, contributor_id: parsed.artifact.author.contributor_id,
    key_id: parsed.artifact.author.key_id, summary: parsed.summary });
  const verified = await verifyArtifactSignature(bytes, manifest,
    encode({ schema_version: 'community-artifact-signature-1', signature: signatures.original.signature_base64 }),
    { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(signatures.original.public_key_base64), status: 'active' },
    parsed.artifact.submission_id, bounds);
  const result = await reserveRevision(db, { verified, contributionId: 'legacy-family', releaseId: 'legacy-release', now: 2, limits, operation: await fixtureOperation('op-legacy') });
  const row = await db.prepare(`SELECT artifact_schema, total_events, audio_events, video_events, summary_json FROM contribution_revisions WHERE revision_id = ?`).bind(result.revisionId).first<{ artifact_schema: string; total_events: number; audio_events: number; video_events: number; summary_json: string }>();
  expect(row).toMatchObject({ artifact_schema: 'community-artifact-1', total_events: 2, audio_events: 1, video_events: 2 });
  expect(JSON.parse(row!.summary_json)).toEqual(parsed.summary);
  expect(await db.prepare(`SELECT 1 FROM release_fingerprints WHERE release_id = 'legacy-release'`).first()).toBeNull();
});