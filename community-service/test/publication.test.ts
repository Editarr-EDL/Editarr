import { beforeAll, expect, it } from 'vitest';
import { publishArtifact, publicationOperation } from '../src/contributions/publication';
import { readPublication } from '../src/contributions/storage';
import { readReservation, revisionStatements, publicationStatement } from '../src/contributions/repository';
import { publicationStorageStatement } from '../src/contributions/storage';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { parseArtifact } from '../src/artifact/codec';
import { sha256 } from '../src/catalog/records';
import { validateParentPermission } from '../src/contributions/permission';
import { verifiedCase } from './helpers/catalog';
import { fixtureKey, signedArtifact } from './helpers/publication-signatures';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';

beforeAll(setupPublication);

it('publishes exact original bytes and returns the same committed result without duplicating stored bytes', async () => {
  const { identity, input } = await publicationCase('ai');
  const first = await publishArtifact(db, identity, input, limits, now);
  const row = await readReservation(db, identity.contributorId, { kind: 'publication', id: identity.operationId! });
  expect(row?.state).toBe('published');
  const stored = await readPublication(db, row!.object_key);
  expect(stored?.artifact.bytes).toEqual(input.artifactBytes);
  expect(stored?.manifest.bytes).toEqual(input.manifestBytes);
  expect(stored?.envelope.bytes).toEqual(input.envelopeBytes);
  const repeated = await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
  expect(repeated.result).toEqual(first.result);
  expect(await db.prepare('SELECT count(*) AS total FROM artifact_payloads WHERE object_key = ?').bind(row!.object_key).first()).toEqual({ total: 1 });
  expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toMatchObject({ state: 'completed' });
});

it('rejects changed operation content before creating another reservation', async () => {
  const { identity, input } = await publicationCase('manual');
  await publishArtifact(db, identity, input, limits, now);
  await expect(publishArtifact(db, { ...identity, contentDigest: 'changed' }, input, limits, now)).rejects.toThrow('operation_conflict');
});

it('ignores disabled legacy policy and tiny legacy write caps when publishing', async () => {
  const { identity, input } = await publicationCase('zero');
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'd1_writes'").run();
  try {
    expect((await publishArtifact(db, identity, input, limits, now)).result.revisionId).toBe(input.submissionId);
    expect((await db.prepare('SELECT * FROM quota_charges').all()).results).toEqual([]);
  } finally { await db.prepare("UPDATE quota_caps SET cap = 100000 WHERE resource = 'd1_writes'").run(); }
});

it('rejects author substitution and expanded-byte exhaustion before reservation', async () => {
  const { identity, input } = await publicationCase('partial');
  await expect(publishArtifact(db, { ...identity, contributorId: 'other' }, input, limits, now)).rejects.toThrow('key_inactive');
  await expect(publishArtifact(db, identity, input, { ...limits, artifact: { ...limits.artifact, expandedBytes: 1 } }, now)).rejects.toThrow();
  expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toBeNull();
});

async function derivative(parent: { revisionId: string; artifactDigest: string }, authorId = 'contributor-1') {
  const artifact = structuredClone((await verifiedCase('manual')).artifact.artifact);
  if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2 fixture');
  artifact.submission_id = crypto.randomUUID();
  artifact.sharing_terms = 'community-reuse-1';
  artifact.kind = 'derived';
  artifact.media.timeline = 'derived_target';
  const reference = { submission_id: parent.revisionId, digest: parent.artifactDigest };
  artifact.source_references = [reference];
  artifact.derivation = { parents: [reference], transform: 'Explicit saved review changes' };
  artifact.sources[0].origin = 'imported';
  artifact.sources[0].reference = reference;
  artifact.sources[0].author_id = authorId;
  artifact.retiming = [{ source_id: artifact.sources[0].source_id, source_bounds: null,
    target_bounds: { start_seconds: 0, end_seconds: 10 }, mapping: 'unknown', applied: true }];
  return signedArtifact(artifact);
}

it('preserves exact parent permission, attribution and derivative history; rejects substituted authors', async () => {
  const parent = await publicationCase('partial');
  const published = await publishArtifact(db, parent.identity, parent.input, limits, now);
  const parentRow = await readReservation(db, parent.identity.contributorId, { kind: 'publication', id: parent.identity.operationId! });
  const before = await readPublication(db, parentRow!.object_key);
  const input = await derivative(published.result);
  const identity = { ...parent.identity, operationId: crypto.randomUUID(), nonce: crypto.randomUUID(), contentDigest: crypto.randomUUID() };
  await publishArtifact(db, identity, input, limits, now);
  const row = await readReservation(db, identity.contributorId, { kind: 'publication', id: identity.operationId });
  const stored = await readPublication(db, row!.object_key);
  expect(stored!.artifact.bytes).toEqual(input.artifactBytes);
  expect(stored!.manifest.bytes).toEqual(input.manifestBytes);
  expect(stored!.envelope.bytes).toEqual(input.envelopeBytes);
  const parsed = await parseArtifact(stored!.artifact.bytes, limits.artifact, true);
  expect(parsed.artifact).toMatchObject({ sharing_terms: 'community-reuse-1',
    derivation: { parents: [{ submission_id: published.result.revisionId, digest: published.result.artifactDigest }] },
    sources: [{ author_id: 'contributor-1', origin: 'imported' }] });
  expect(await readPublication(db, parentRow!.object_key)).toEqual(before);
  const substituted = await derivative(published.result, 'invented-original-author');
  const invalidIdentity = { ...identity, operationId: crypto.randomUUID(), nonce: crypto.randomUUID(), contentDigest: crypto.randomUUID() };
  await expect(publishArtifact(db, invalidIdentity, substituted, limits, now)).rejects.toThrow('source_attribution_mismatch');
  expect(await publicationOperation(db, invalidIdentity.contributorId, invalidIdentity.operationId)).toBeNull();
});

it('retains completed historical claims without allowing derivatives to relicense them', async () => {
  const { identity } = await publicationCase('legacy');
  const artifact = structuredClone((await verifiedCase('manual')).artifact.artifact);
  artifact.submission_id = crypto.randomUUID();
  artifact.sharing_terms = 'historical-other-permission';
  const input = await signedArtifact(artifact), key = await fixtureKey();
  const verified = await verifyArtifactSignature(input.artifactBytes, input.manifestBytes, input.envelopeBytes,
    { contributorId: identity.contributorId, keyId: identity.keyId, publicKey: key.publicKey, status: 'active' }, input.submissionId, limits.artifact);
  const plan = await revisionStatements(db, { verified, contributionId: input.contributionId, releaseId: crypto.randomUUID(),
    now, limits: limits.catalog, operation: { kind: 'publication', id: identity.operationId! } });
  const artifactDigest = await sha256(input.artifactBytes);
  const result = { version: 1, operationId: identity.operationId, contributionId: input.contributionId,
    revisionId: input.submissionId, artifactDigest, committedAt: now };
  const storage = await publicationStorageStatement(db, plan.objectKey, {
    artifact: { bytes: input.artifactBytes, digest: artifactDigest },
    manifest: { bytes: input.manifestBytes, digest: await sha256(input.manifestBytes) },
    envelope: { bytes: input.envelopeBytes, digest: await sha256(input.envelopeBytes) },
  });
  await db.batch([
    db.prepare("INSERT INTO operations VALUES (?, 'publication', ?, ?, 'pending', NULL, ?, ?)")
      .bind(identity.contributorId, identity.operationId, identity.contentDigest, now, now),
    ...plan.statements, storage,
    publicationStatement(db, { revisionId: input.submissionId, from: 'reserved', to: 'object_stored', objectEtag: artifactDigest, now }),
    publicationStatement(db, { revisionId: input.submissionId, from: 'object_stored', to: 'published', now }),
    db.prepare("UPDATE operations SET state = 'completed', result = ? WHERE operation_id = ?").bind(JSON.stringify(result), identity.operationId),
  ]);
  const before = await readPublication(db, plan.objectKey);
  expect((await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now)).result).toEqual(result);
  const child = await derivative(result);
  await expect(publishArtifact(db, { ...identity, operationId: crypto.randomUUID(), nonce: crypto.randomUUID(), contentDigest: crypto.randomUUID() },
    child, limits, now)).rejects.toThrow('source_permission_incompatible');
  expect(await readPublication(db, plan.objectKey)).toEqual(before);
  expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(child.submissionId).first()).toBeNull();
  const pendingIdentity = { ...identity, operationId: crypto.randomUUID(), nonce: crypto.randomUUID(), contentDigest: crypto.randomUUID() };
  const pendingVerified = await verifyArtifactSignature(child.artifactBytes, child.manifestBytes, child.envelopeBytes,
    { contributorId: identity.contributorId, keyId: identity.keyId, publicKey: key.publicKey, status: 'active' }, child.submissionId, limits.artifact);
  const pendingPlan = await revisionStatements(db, { verified: pendingVerified, contributionId: child.contributionId,
    releaseId: crypto.randomUUID(), now, limits: limits.catalog, operation: { kind: 'publication', id: pendingIdentity.operationId } });
  await db.batch([
    db.prepare("INSERT INTO operations VALUES (?, 'publication', ?, ?, 'pending', NULL, ?, ?)")
      .bind(identity.contributorId, pendingIdentity.operationId, pendingIdentity.contentDigest, now, now),
    ...pendingPlan.statements,
  ]);
  await expect(publishArtifact(db, pendingIdentity, child, limits, now)).rejects.toThrow('source_permission_incompatible');
  expect(await readReservation(db, identity.contributorId, { kind: 'publication', id: pendingIdentity.operationId }))
    .toMatchObject({ state: 'reserved' });
  expect(await readPublication(db, pendingPlan.objectKey)).toBeNull();
});

it('fails closed for missing or corrupt original permission evidence', async () => {
  const artifact = (await verifiedCase('manual')).artifact.artifact;
  const parent = { revision_id: 'missing', artifact_digest: 'a'.repeat(64), contributor_id: 'contributor-1', artifact: null };
  await expect(validateParentPermission(artifact, parent, limits.artifact)).rejects.toThrow('source_permission_unknown');
  await expect(validateParentPermission(artifact, { ...parent, artifact: new Uint8Array([1, 2]).buffer }, limits.artifact))
    .rejects.toThrow('source_permission_unknown');
});