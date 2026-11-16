import type { SqlStatement } from '../src/runtime/contracts';
import { beforeAll, expect, it } from 'vitest';
import { publishArtifact, publicationOperation } from '../src/contributions/publication';
import { readReservation } from '../src/contributions/repository';
import { publicationState } from './helpers/publication-state';
import { syntheticPublication } from './helpers/publication-signatures';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';

beforeAll(setupPublication);

it.each(['ai', 'manual', 'zero'] as const)('rolls back all publication records on a transaction failure for %s', async name => {
  const { identity, input } = await publicationCase(name);
  const before = await publicationState();
  await db.exec(`CREATE TRIGGER injected_abort BEFORE UPDATE OF state ON operations
    WHEN NEW.state = 'completed' BEGIN SELECT RAISE(ABORT, 'injected_failure'); END`.replace(/\s*\n\s*/g, ' '));
  try {
    await expect(publishArtifact(db, identity, input, limits, now)).rejects.toThrow('injected_failure');
    expect(await publicationState()).toEqual(before);
    expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toBeNull();
  } finally { await db.exec('DROP TRIGGER injected_abort'); }
  await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
  expect(await readReservation(db, identity.contributorId, { kind: 'publication', id: identity.operationId! }))
    .toMatchObject({ state: 'published' });
});

it('keeps committed success when telemetry fails', async () => {
  const { identity, input } = await publicationCase('partial');
  await db.exec(`CREATE TRIGGER injected_telemetry BEFORE INSERT ON usage_observations
    BEGIN SELECT RAISE(ABORT, 'injected_failure'); END`.replace(/\s*\n\s*/g, ' '));
  try {
    const response = await publishArtifact(db, identity, input, limits, now);
    expect(response.usageAvailable).toBe(false);
    expect(await publicationOperation(db, identity.contributorId, identity.operationId!)).toMatchObject({ state: 'completed' });
  } finally { await db.exec('DROP TRIGGER injected_telemetry'); }
});

it.each(['payload', 'seal'] as const)('leaves no publication records when the %s step fails', async boundary => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const before = await publicationState();
  const trigger = boundary === 'payload' ? 'BEFORE INSERT ON artifact_payloads' :
    "BEFORE UPDATE ON contribution_publications WHEN NEW.state = 'object_stored'";
  await db.exec(`CREATE TRIGGER injected_step ${trigger} BEGIN SELECT RAISE(ABORT, 'injected_step'); END`);
  try {
    await expect(publishArtifact(db, identity, input, limits, now)).rejects.toThrow('injected_step');
    expect(await publicationState()).toEqual(before);
  } finally { await db.exec('DROP TRIGGER injected_step'); }
  await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
});

it('returns the saved result on retry after commit succeeds but its response is lost', async () => {
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  let batches = 0, committed = false;
  const disconnected = new Proxy(db, { get(target, property) {
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      const result = await target.batch(statements);
      if (++batches === 1) committed = true;
      return result;
    };
    if (property === 'prepare') return (sql: string) => {
      if (committed && sql.includes('SELECT request_digest, state, result')) throw new Error('response_lost');
      return target.prepare(sql);
    };
    const value = Reflect.get(target, property); return typeof value === 'function' ? value.bind(target) : value;
  } });
  await expect(publishArtifact(disconnected, identity, input, limits, now)).rejects.toThrow('response_lost');
  const saved = await publicationOperation(db, identity.contributorId, identity.operationId!);
  expect(saved?.state).toBe('completed');
  const beforeRetry = await publicationState();
  const retried = await publishArtifact(db, { ...identity, nonce: crypto.randomUUID() }, input, limits, now + 1);
  expect(retried.result).toEqual(JSON.parse(saved!.result!));
  expect(await publicationState()).toEqual(beforeRetry);
});
