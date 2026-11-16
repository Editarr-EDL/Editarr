import { beforeAll, expect, it } from 'vitest';
import { publishArtifact } from '../src/contributions/publication';
import { publicationStatement, readReservation, reserveRevision } from '../src/contributions/repository';
import { publicationStorageStatement } from '../src/contributions/storage';
import { sha256 } from '../src/catalog/records';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { fixtureKey } from './helpers/publication-signatures';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';

beforeAll(setupPublication);

it.each(['reserved', 'object_stored'] as const)('preserves and completes an older %s publication on explicit retry', async state => {
  const name = state === 'reserved' ? 'ai' : 'manual';
  const { identity, input } = await publicationCase(name);
  const operation = { kind: 'publication', id: identity.operationId! };
  await db.prepare(`INSERT INTO operations VALUES (?, 'publication', ?, ?, 'pending', NULL, ?, ?)`)
    .bind(identity.contributorId, operation.id, identity.contentDigest, now * 1000, now * 1000).run();
  const key = await fixtureKey();
  const verified = await verifyArtifactSignature(input.artifactBytes, input.manifestBytes, input.envelopeBytes,
    { contributorId: identity.contributorId, keyId: identity.keyId, publicKey: key.publicKey, status: 'active' }, input.submissionId, limits.artifact);
  const reservation = await reserveRevision(db, { verified,
    contributionId: input.contributionId, releaseId: crypto.randomUUID(), now, limits: limits.catalog, operation });
  if (state === 'object_stored') {
    const [artifact, manifest, envelope] = await Promise.all(
      [input.artifactBytes, input.manifestBytes, input.envelopeBytes].map(async bytes => ({ bytes, digest: await sha256(bytes) })));
    await db.batch([
      await publicationStorageStatement(db, reservation.objectKey, { artifact, manifest, envelope }),
      publicationStatement(db, { revisionId: reservation.revisionId, from: 'reserved', to: 'object_stored', objectEtag: artifact.digest, now }),
    ]);
  }
  const result = await publishArtifact(db, identity, input, limits, now + 1);
  expect(result.result.revisionId).toBe(reservation.revisionId);
  expect(await readReservation(db, identity.contributorId, operation))
    .toMatchObject({ state: 'published', object_key: reservation.objectKey });
  expect((await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 2)).result)
    .toEqual(result.result);
});
