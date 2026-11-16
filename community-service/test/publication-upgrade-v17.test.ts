import { readFileSync, readdirSync } from 'node:fs';
import { beforeAll, expect, it } from 'vitest';
import genericProvenance from '../migrations/0017_generic_provenance.sql?raw';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { sha256 } from '../src/catalog/records';
import { publicationOperation, publishArtifact, type PublicationResult } from '../src/contributions/publication';
import { publicationStatement, readReservation, reserveRevision } from '../src/contributions/repository';
import { publicationStorageStatement } from '../src/contributions/storage';
import type { AuthenticatedContributor } from '../src/security/contributor-auth';
import { decode, verifiedCase } from './helpers/catalog';
import { db, limits, now } from './helpers/publication';
import { signedArtifact } from './helpers/publication-signatures';
import fixture from '../../tests/fixtures/community/donation-artifacts-v2.json';

type State = 'reserved' | 'object_stored' | 'completed';

async function radarrPublication() {
  const artifact = structuredClone((await verifiedCase('ai')).artifact.artifact);
  if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2');
  artifact.submission_id = crypto.randomUUID();
  artifact.sharing_terms = 'community-reuse-1';
  const matching = artifact.matching as Record<string, any>;
  matching.media.release.duration = { seconds: 10, source: 'radarr_catalog_minutes', precision_seconds: 60 };
  matching.media.identifiers = matching.media.identifiers.map((item: object) => ({ ...item, provenance: 'radarr.movie' }));
  return { input: await signedArtifact(artifact, true), matching };
}

function contributor(): AuthenticatedContributor {
  return { contributorId: 'contributor-1', keyId: 'key-1', displayName: 'Public Name', nonce: crypto.randomUUID(),
    operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 };
}

async function schema16Publication(state: State) {
  const { input, matching } = await radarrPublication(), identity = contributor();
  const operation = { kind: 'publication', id: identity.operationId! };
  await db.prepare(`INSERT INTO operations VALUES (?, 'publication', ?, ?, 'pending', NULL, ?, ?)`)
    .bind(identity.contributorId, operation.id, identity.contentDigest, now * 1000, now * 1000).run();
  const verified = await verifyArtifactSignature(input.artifactBytes, input.manifestBytes, input.envelopeBytes,
    { contributorId: identity.contributorId, keyId: identity.keyId, publicKey: decode(fixture.public_key), status: 'active' },
    input.submissionId, limits.artifact, true);
  const reservation = await reserveRevision(db, { verified, contributionId: input.contributionId,
    releaseId: crypto.randomUUID(), now, limits: limits.catalog, operation });
  await db.exec('DROP TRIGGER guard_release_update; DROP TRIGGER guard_revision_update;');
  await db.prepare(`UPDATE contribution_revisions SET summary_json = json_set(summary_json, '$.matching', json(?))
    WHERE revision_id = ?`).bind(JSON.stringify(matching), reservation.revisionId).run();
  await db.prepare(`UPDATE releases SET evidence_json = ? WHERE release_id =
    (SELECT release_id FROM contribution_revisions WHERE revision_id = ?)`).bind(JSON.stringify(matching), reservation.revisionId).run();
  await db.exec(`CREATE TRIGGER guard_release_update BEFORE UPDATE ON releases BEGIN SELECT RAISE(ABORT, 'immutable_release'); END;
    CREATE TRIGGER guard_revision_update BEFORE UPDATE ON contribution_revisions BEGIN SELECT RAISE(ABORT, 'immutable_revision'); END;`);
  let saved: PublicationResult | null = null;
  if (state !== 'reserved') {
    const [artifact, manifest, envelope] = await Promise.all(
      [input.artifactBytes, input.manifestBytes, input.envelopeBytes].map(async bytes => ({ bytes, digest: await sha256(bytes) })));
    await db.batch([
      await publicationStorageStatement(db, reservation.objectKey, { artifact, manifest, envelope }),
      publicationStatement(db, { revisionId: reservation.revisionId, from: 'reserved', to: 'object_stored', objectEtag: artifact.digest, now }),
    ]);
    if (state === 'completed') {
      saved = { version: 1, operationId: operation.id, contributionId: input.contributionId,
        revisionId: reservation.revisionId, artifactDigest: artifact.digest, committedAt: now };
      await db.batch([
        publicationStatement(db, { revisionId: reservation.revisionId, from: 'object_stored', to: 'published', now }),
        db.prepare(`UPDATE operations SET state = 'completed', result = ? WHERE operation_id = ?`)
          .bind(JSON.stringify(saved), operation.id),
      ]);
    }
  }
  return { identity, input, operation, revisionId: reservation.revisionId, saved };
}

const rows: Partial<Record<State, Awaited<ReturnType<typeof schema16Publication>>>> = {};
const retried = async () => (await db.prepare(`SELECT coalesce(sum(occurrences), 0) AS total FROM usage_observations
  WHERE resource = 'worker_requests' AND outcome = 'retried'`).first<{ total: number }>())!.total;

beforeAll(async () => {
  const directory = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(directory).filter(file => /^00(0\d|1[0-6])_.*\.sql$/.test(file)).sort())
    await db.exec(readFileSync(new URL(file, directory), 'utf8'));
  await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
    VALUES ('contributor-1', 'discord-1', 'Public Name', 'public name', 1)`).run();
  await db.prepare(`INSERT INTO installations VALUES ('installation-1', 'contributor-1', 1)`).run();
  await db.prepare(`INSERT INTO installation_keys VALUES ('key-1', 'installation-1', 'Ed25519', ?, 'active', 1, NULL)`)
    .bind(decode(fixture.public_key)).run();
  for (const state of ['reserved', 'object_stored', 'completed'] as const) rows[state] = await schema16Publication(state);
  expect(await db.prepare(`SELECT count(*) AS total FROM releases WHERE evidence_json LIKE '%radarr%'`).first())
    .toEqual({ total: 3 });
  await db.exec(genericProvenance);
  expect(await db.prepare(`SELECT count(*) AS total FROM releases WHERE evidence_json LIKE '%radarr%'`).first())
    .toEqual({ total: 0 });
});

it.each(['reserved', 'object_stored'] as const)('completes a %s Radarr publication left at schema 16 on explicit retry', async state => {
  const { identity, input, operation, revisionId } = rows[state]!;
  const before = await retried();
  const response = await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
  expect(response.result).toMatchObject({ operationId: operation.id, revisionId, artifactDigest: await sha256(input.artifactBytes) });
  expect(await readReservation(db, identity.contributorId, operation)).toMatchObject({ state: 'published' });
  expect(await publicationOperation(db, identity.contributorId, operation.id)).toMatchObject({ state: 'completed' });
  expect(await retried()).toBe(before + 1);
});

it('returns the saved result marked retried for a Radarr publication completed at schema 16', async () => {
  const { identity, input, saved } = rows.completed!;
  const before = await retried();
  const response = await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
  expect(response.result).toEqual(saved);
  expect(await retried()).toBe(before + 1);
});

it('refuses different Radarr bytes under a schema 16 operation ID', async () => {
  const { identity } = rows.reserved!, { input } = await radarrPublication();
  await expect(publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1))
    .rejects.toThrow('operation_conflict');
});

it('refuses a new upload carrying Radarr provenance after the upgrade', async () => {
  const { input } = await radarrPublication(), identity = contributor();
  await expect(publishArtifact(db, identity, input, limits, now + 1)).rejects.toThrow();
  expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toBeNull();
  expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toBeNull();
});
