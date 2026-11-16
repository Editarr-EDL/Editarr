import { beforeAll, expect, it } from 'vitest';
import { sha256 } from '../src/catalog/records';
import { publicationStorageStatement, readPublication } from '../src/contributions/storage';
import { publishArtifact } from '../src/contributions/publication';
import { publicationLimits } from '../src/contributions/publication-input';
import { readReservation } from '../src/contributions/repository';
import { publicationState } from './helpers/publication-state';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';

beforeAll(setupPublication);

it('stores exact signed parts in one immutable row and charges retries only once', async () => {
  const { identity, input } = await publicationCase('ai');
  await publishArtifact(db, identity, input, limits, now);
  const row = await readReservation(db, identity.contributorId, { kind: 'publication', id: identity.operationId! });
  const parts = (await readPublication(db, row!.object_key))!;
  expect(parts.artifact.bytes).toEqual(input.artifactBytes);
  expect(parts.manifest.bytes).toEqual(input.manifestBytes);
  expect(parts.envelope.bytes).toEqual(input.envelopeBytes);
  const expected = Object.fromEntries(Object.entries(parts).map(([name, part]) =>
    [name, { byteLength: part.bytes.length, digest: part.digest }])) as import('../src/contributions/storage').PublicationMetadata;
  expect(await readPublication(db, row!.object_key, expected)).toEqual(parts);
  await expect(readPublication(db, row!.object_key, { ...expected,
    artifact: { ...expected.artifact, byteLength: expected.artifact.byteLength + 1 } })).rejects.toThrow('artifact_metadata_mismatch');
  await expect(readPublication(db, row!.object_key, { ...expected,
    manifest: { ...expected.manifest, digest: '0'.repeat(64) } })).rejects.toThrow('artifact_metadata_mismatch');
  expect(await readPublication(db, 'artifacts/missing')).toBeNull();
  await (await publicationStorageStatement(db, row!.object_key, parts)).run();
  expect(await db.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'").first())
    .toEqual({ bytes: input.artifactBytes.length + input.manifestBytes.length + input.envelopeBytes.length });
  await expect(db.prepare('UPDATE artifact_payloads SET artifact = ? WHERE object_key = ?')
    .bind(new Uint8Array([1]), row!.object_key).run()).rejects.toThrow('object_conflict');
  await expect(db.prepare('DELETE FROM artifact_payloads WHERE object_key = ?').bind(row!.object_key).run())
    .rejects.toThrow('immutable_artifact');
  const bad = { ...parts, artifact: { bytes: new Uint8Array([1]), digest: parts.artifact.digest } };
  await expect(publicationStorageStatement(db, row!.object_key, bad)).rejects.toThrow('object_digest_mismatch');
  bad.artifact.digest = await sha256(bad.artifact.bytes);
  await expect((await publicationStorageStatement(db, row!.object_key, bad)).run()).rejects.toThrow();
  expect((await readPublication(db, row!.object_key))?.artifact.bytes).toEqual(input.artifactBytes);
});

it.each(['global', 'contributor'])('ignores obsolete %s storage caps while observing payload bytes', async scope => {
  const { identity, input } = await publicationCase(scope === 'global' ? 'manual' : 'zero');
  await db.prepare(`UPDATE artifact_storage_policy SET ${scope === 'global' ? 'global_bytes = 1, contributor_bytes = 1' : 'contributor_bytes = 1'}`).run();
  try {
    expect((await publishArtifact(db, identity, input, limits, now)).result.revisionId).toBe(input.submissionId);
    expect((await db.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'")
      .first<{ bytes: number }>())!.bytes).toBeGreaterThan(1);
  } finally { await db.prepare('UPDATE artifact_storage_policy SET global_bytes = 350000000, contributor_bytes = 7000000').run(); }
  await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
});

it('rejects configuration that could exceed the SQLite row budget', () => {
  expect(() => publicationLimits({ ...limits, artifact: { ...limits.artifact, compressedBytes: 1_900_000 } }))
    .toThrow('publication_unavailable');
});
