import { beforeAll, expect, it } from 'vitest';
import { readReservation, reserveRevision, revisionStatements, transitionPublication } from '../src/contributions/repository';
import { db, fixtureOperation, limits, rawRevision, setupCatalog, verifiedCase } from './helpers/catalog';

beforeAll(setupCatalog);
it('reserves immutable verified claims with separate lifecycle and counts', async () => {
  const verified = await verifiedCase('ai');
  const result = await reserveRevision(db, { verified, contributionId: 'family-ai', releaseId: 'release-ai', now: 2, limits, operation: await fixtureOperation('op-ai') });
  expect(result.objectKey).toMatch(/^artifacts\/[0-9a-f-]{36}$/);
  expect(await db.prepare(`SELECT artifact_bytes, manifest_bytes, envelope_bytes FROM contribution_revisions WHERE revision_id = ?`).bind(result.revisionId).first())
    .toEqual({ artifact_bytes: verified.artifactBytes.byteLength, manifest_bytes: verified.manifestBytes.byteLength, envelope_bytes: verified.envelopeBytes.byteLength });
  await expect(db.prepare(`UPDATE contribution_revisions SET total_events = 2 WHERE revision_id = ?`).bind(result.revisionId).run()).rejects.toThrow('immutable_revision');
  await expect(transitionPublication(db, { revisionId: result.revisionId, from: 'reserved', to: 'published', now: 3 })).rejects.toThrow('invalid_publication_state');
  await transitionPublication(db, { revisionId: result.revisionId, from: 'reserved', to: 'object_stored', objectEtag: 'stored-fixture', now: 3 });
  await transitionPublication(db, { revisionId: result.revisionId, from: 'object_stored', to: 'published', now: 4 });
  await db.prepare(`UPDATE contribution_statistics SET imports = 1 WHERE revision_id = ?`).bind(result.revisionId).run();
  const claim = await db.prepare(`SELECT total_events, summary_json FROM contribution_revisions WHERE revision_id = ?`).bind(result.revisionId).first<{ total_events: number; summary_json: string }>();
  expect(claim?.total_events).toBe(1);
  expect(JSON.parse(claim!.summary_json)).toEqual(verified.artifact.summary);
  await expect(db.prepare(`DELETE FROM contribution_publications WHERE revision_id = ?`).bind(result.revisionId).run()).rejects.toThrow('immutable_publication_identity');
  await transitionPublication(db, { revisionId: result.revisionId, from: 'published', to: 'quarantined', now: 5 });
  await transitionPublication(db, { revisionId: result.revisionId, from: 'quarantined', to: 'published', now: 6 });
  await expect(transitionPublication(db, { revisionId: result.revisionId, from: 'published', to: 'reserved' as 'published', now: 7 })).rejects.toThrow('invalid_publication_state');
  await expect(db.prepare(`UPDATE contribution_publications SET object_etag = 'changed' WHERE revision_id = ?`).bind(result.revisionId).run()).rejects.toThrow('invalid_publication_state');
});

it('allows one concurrent immutable revision and rolls back the loser completely', async () => {
  const verified = await verifiedCase('zero');
  const operation = await fixtureOperation('op-zero');
  const results = await Promise.allSettled(['release-zero-a', 'release-zero-b'].map(releaseId =>
    reserveRevision(db, { verified, contributionId: 'family-zero', releaseId, now: 5, limits, operation })));
  expect(results.filter(item => item.status === 'fulfilled')).toHaveLength(1);
  expect(results.filter(item => item.status === 'rejected')).toHaveLength(1);
  expect((await db.prepare(`SELECT release_id FROM releases WHERE release_id LIKE 'release-zero-%'`).all()).results).toHaveLength(1);
});

it('rejects missing parents and bounded-summary overflow before writing rows', async () => {
  const operation = await fixtureOperation('op-invalid');
  await expect(reserveRevision(db, { verified: await verifiedCase('imported'), contributionId: 'family-import', releaseId: 'release-import', now: 6, limits, operation })).rejects.toThrow('missing_parent');
  await expect(reserveRevision(db, { verified: await verifiedCase('partial'), contributionId: 'family-small', releaseId: 'release-small', now: 6, limits: { ...limits, maxJsonBytes: 1 }, operation })).rejects.toThrow('catalog_json_limit');
  expect(await db.prepare(`SELECT count(*) AS total FROM releases WHERE release_id IN ('release-import', 'release-small')`).first()).toEqual({ total: 0 });
});

it.each(['maxReadQueries', 'maxStatements'] as const)('enforces explicit %s before writes', async field => {
  await expect(reserveRevision(db, { verified: await verifiedCase('retimed'), contributionId: 'family-capped', releaseId: 'release-capped', now: 6, limits: { ...limits, [field]: field === 'maxStatements' ? 1 : 0 }, operation: await fixtureOperation(`op-${field}`) })).rejects.toThrow();
  expect(await db.prepare(`SELECT 1 FROM releases WHERE release_id = 'release-capped'`).first()).toBeNull();
});

it('rechecks a parent status inside the final atomic batch', async () => {
  await rawRevision('original-revision', 'release-ai');
  await transitionPublication(db, { revisionId: 'original-revision', from: 'reserved', to: 'object_stored', objectEtag: 'parent', now: 7 });
  await transitionPublication(db, { revisionId: 'original-revision', from: 'object_stored', to: 'published', now: 8 });
  const input = { verified: await verifiedCase('imported'), contributionId: 'family-race', releaseId: 'release-race', now: 9, limits, operation: await fixtureOperation('op-parent-race') };
  const plan = await revisionStatements(db, input);
  await transitionPublication(db, { revisionId: 'original-revision', from: 'published', to: 'quarantined', now: 9 });
  await expect(db.batch(plan.statements)).rejects.toThrow('invalid_parent');
  expect(await db.prepare(`SELECT 1 FROM releases WHERE release_id = 'release-race'`).first()).toBeNull();
});

it.each(['failed', 'expired'] as const)('does not expose or reopen terminal %s uploads', async state => {
  const id = `terminal-${state}`;
  await rawRevision(id, 'release-ai');
  await transitionPublication(db, { revisionId: id, from: 'reserved', to: state, now: 10 });
  await expect(transitionPublication(db, { revisionId: id, from: 'reserved', to: 'object_stored', objectEtag: 'late-object', now: 11 })).rejects.toThrow('publication_conflict');
  await expect(db.prepare(`UPDATE contribution_publications SET state = 'object_stored', object_etag = 'late-object' WHERE revision_id = ?`).bind(id).run()).rejects.toThrow('invalid_publication_state');
});

it('binds one revision to an owned operation and provides a scoped recovery lookup', async () => {
  const operation = { kind: 'fixture-publication', id: 'op-ai' };
  expect(await readReservation(db, 'contributor-1', operation)).toMatchObject({ revision_id: 'submission-ai', state: 'published' });
  expect(await readReservation(db, 'other-operation-owner', operation)).toBeNull();
  const verified = await verifiedCase('legacy');
  await expect(reserveRevision(db, { verified, contributionId: 'reused-family', releaseId: 'reused-release', now: 12, limits, operation })).rejects.toThrow();
  await expect(reserveRevision(db, { verified, contributionId: 'missing-op-family', releaseId: 'missing-op-release', now: 12, limits, operation: { ...operation, id: 'missing' } })).rejects.toThrow();
  await db.prepare(`INSERT INTO contributors VALUES ('foreign-operation-owner', 'foreign-discord', 'Foreign', 'active', 1)`).run();
  await db.prepare(`INSERT INTO operations VALUES ('foreign-operation-owner', 'fixture-publication', 'foreign-op', 'fixture', 'pending', NULL, 1, 1)`).run();
  await expect(reserveRevision(db, { verified, contributionId: 'foreign-op-family', releaseId: 'foreign-op-release', now: 12, limits, operation: { ...operation, id: 'foreign-op' } })).rejects.toThrow();
  expect(await db.prepare(`SELECT count(*) AS total FROM releases WHERE release_id IN ('reused-release', 'missing-op-release', 'foreign-op-release')`).first()).toEqual({ total: 0 });
  const explain = await db.prepare(`EXPLAIN QUERY PLAN SELECT revision_id FROM contribution_revisions
    WHERE contributor_id = ? AND operation_kind = ? AND operation_id = ?`).bind('contributor-1', operation.kind, operation.id).all<{ detail: string }>();
  expect(explain.results.some(item => item.detail.includes('operation_kind=?') && item.detail.includes('operation_id=?'))).toBe(true);
});