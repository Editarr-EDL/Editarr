import type { SqlStatement } from '../src/runtime/contracts';
import { beforeAll, expect, it, vi } from 'vitest';
import { publishArtifact, publicationOperation } from '../src/contributions/publication';
import { readReservation } from '../src/contributions/repository';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { publicationState } from './helpers/publication-state';
import { syntheticPublication } from './helpers/publication-signatures';

beforeAll(setupPublication);

it('publishes once under concurrent first attempts and returns the same committed result', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const results = await Promise.all([0, 1].map(() => publishArtifact(db,
    { ...identity, nonce: crypto.randomUUID() }, input, limits, now)));
  expect(results[0].result).toEqual(results[1].result);
  expect(await db.prepare('SELECT count(*) AS total FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toEqual({ total: 1 });
  const retries = await Promise.all([0, 1].map(() => publishArtifact(db,
    { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1)));
  expect(retries[1].result).toEqual(retries[0].result);
});

it('rejects a cooperative validation wall budget before reserving', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const clock = vi.spyOn(Date, 'now').mockReturnValueOnce(0).mockReturnValue(11);
  try { await expect(publishArtifact(db, identity, input, { ...limits, validationWallMs: 10 }, now)).rejects.toThrow('validation_budget_exceeded'); }
  finally { clock.mockRestore(); }
  expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toBeNull();
});

it('checks key activity inside the transaction and leaves no publication when retired', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const before = await publicationState();
  let batches = 0;
  const retiring = new Proxy(db, { get(target, property) {
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      if (++batches === 1) await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = 'key-1'").bind(now).run();
      return target.batch(statements);
    };
    const value = Reflect.get(target, property); return typeof value === 'function' ? value.bind(target) : value;
  } });
  try {
    await expect(publishArtifact(retiring, identity, input, limits, now)).rejects.toThrow();
    expect(await publicationState()).toEqual(before);
    expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toBeNull();
  } finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run(); }
});

it('concurrent retries of a rolled-back transaction converge without duplicate byte charges', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  await db.exec(`CREATE TRIGGER fail_payload BEFORE INSERT ON artifact_payloads
    BEGIN SELECT RAISE(ABORT, 'offline'); END`.replace(/\s*\n\s*/g, ' '));
  try { await expect(publishArtifact(db, identity, input, limits, now)).rejects.toThrow('offline'); }
  finally { await db.exec('DROP TRIGGER fail_payload'); }
  const before = await db.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'").first<{ bytes: number }>();
  const retries = await Promise.all([0, 1].map(() => publishArtifact(db,
    { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1)));
  expect(retries[1].result).toEqual(retries[0].result);
  const after = await db.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'").first<{ bytes: number }>();
  expect(after!.bytes - (before?.bytes ?? 0)).toBe(input.artifactBytes.length + input.manifestBytes.length + input.envelopeBytes.length);
});

it('returns a committed result to an active sibling after the original signing key retires', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const first = await publishArtifact(db, identity, input, limits, now);
  await db.batch([
    db.prepare("INSERT INTO installations VALUES ('sibling', 'contributor-1', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('sibling-key', 'sibling', 'Ed25519', ?, 'active', 1, NULL)").bind(crypto.getRandomValues(new Uint8Array(32))),
    db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = 'key-1'").bind(now),
  ]);
  try {
    const retried = await publishArtifact(db, { ...identity, keyId: 'sibling-key', nonce: crypto.randomUUID() }, input, limits, now + 1);
    expect(retried.result).toEqual(first.result);
    expect((await readReservation(db, identity.contributorId, { kind: 'publication', id: identity.operationId! }))?.key_id).toBe('key-1');
  } finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run(); }
});

it('rejects conflicting concurrent operation content without publishing a second artifact', async () => {
  const { identity } = await publicationCase('ai');
  const inputs = await Promise.all([syntheticPublication(), syntheticPublication()]);
  const results = await Promise.allSettled(inputs.map((input, index) => publishArtifact(db,
    { ...identity, nonce: crypto.randomUUID(), contentDigest: `conflict-${index}` }, input, limits, now)));
  expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  const rejected = results.find(result => result.status === 'rejected') as PromiseRejectedResult;
  expect(String(rejected.reason)).toContain('operation_conflict');
  expect(await db.prepare('SELECT count(*) AS total FROM contribution_revisions WHERE operation_id = ?')
    .bind(identity.operationId).first()).toEqual({ total: 1 });
});
