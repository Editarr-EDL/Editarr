import { readFileSync, readdirSync } from 'node:fs';
import { beforeAll, expect, it, vi } from 'vitest';
import type { SqlDatabase } from '../src/runtime/contracts';
import { previewRetention, RETENTION_POLICY } from '../src/retention/policy';
import { transientStatement } from '../src/retention/transient';
import { pruneExpiredAuthenticationState } from '../src/operations/repository';
import { pruneRecoveryAuthorizations } from '../src/identity/recovery';
import { pruneBotMembership } from '../src/integrations/discord/membership';
import { env } from './helpers/environment';

const db = env.CATALOG_DB;
const now = Date.parse('2026-10-08T12:00:00Z');
const seconds = now / 1000;

beforeAll(async () => {
  const directory = new URL('../migrations/', import.meta.url);
  for (const name of readdirSync(directory).filter(name => name.endsWith('.sql')).sort()) {
    await db.exec(readFileSync(new URL(name, directory), 'utf8'));
  }
  await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
    VALUES ('retention-person', 'private-discord-id', 'Retained Person', 'retained person', 1)`).run();
  await db.prepare("INSERT INTO installations VALUES ('retention-install', 'retention-person', 1)").run();
  await db.prepare("INSERT INTO installation_keys VALUES ('retention-key', 'retention-install', 'Ed25519', ?, 'active', 1, NULL)")
    .bind(new Uint8Array(32)).run();
  for (const offset of [-1, 0, 1]) {
    await db.prepare(`INSERT INTO discord_oauth_states
      (state_id, browser_hash, installation_id, key_id, public_key, expires_at) VALUES (?, ?, ?, ?, ?, ?)`)
      .bind(`state-${offset}`, 'private-browser-hash', 'retention-install', 'retention-key', new Uint8Array(32), now + offset).run();
    await db.prepare('INSERT INTO request_nonces VALUES (?, ?, ?, ?)')
      .bind('retention-key', `nonce-${offset}`, seconds + offset, 'retention-person').run();
  }
  for (const [state, kind, operation, result] of [
    ['completed', 'key_recovery_authorization', 'unused-authority', { expiresAt: now - 1, operationId: 'unused-recovery' }],
    ['completed', 'key_recovery_authorization', 'used-authority', { expiresAt: now - 1, operationId: 'finished-recovery' }],
    ['completed', 'key_recovery', 'finished-recovery', { stateId: 'used-authority' }],
    ['pending', 'key_recovery_authorization', 'pending-authority', { expiresAt: now - 1, operationId: 'pending-recovery' }],
    ['completed', 'profile_name', 'durable-rename', { displayName: 'Retained Person' }],
  ] as const) {
    await db.prepare(`INSERT INTO operations
      (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, 1, ?)`)
      .bind('retention-person', kind, operation, 'retained-digest', state,
        state === 'completed' ? JSON.stringify(result) : null, now - 600_001).run();
  }
});

it('previews only bounded counts without executing mutations or exposing private values', async () => {
  const prepare = vi.fn((sql: string) => {
    expect(sql).toMatch(/^SELECT COUNT\(\*\)/);
    return db.prepare(sql);
  });
  const batch = vi.fn(() => { throw new Error('preview_must_not_write'); });
  const before = (await db.prepare('SELECT * FROM operations ORDER BY operation_id').all()).results;
  const plan = await previewRetention({ prepare, batch }, now);
  expect(plan.policy).toEqual(RETENTION_POLICY);
  expect(plan).toMatchObject({ mode: 'read_only', payloadBytesReclaimed: 0,
    scope: 'currently_eligible_not_reserved' });
  expect(plan.categories.filter(item => item.eligible > 0)).toEqual([
    { category: 'oauthStates', eligible: 1, moreEligible: false },
    { category: 'recoveryAuthorizations', eligible: 1, moreEligible: false },
    { category: 'requestNonces', eligible: 1, moreEligible: false },
  ]);
  expect(prepare).toHaveBeenCalledTimes(10);
  expect(batch).not.toHaveBeenCalled();
  expect(JSON.stringify(plan)).not.toMatch(/private-|retention-person|retention-key|used-authority|retained-digest/);
  expect((await db.prepare('SELECT * FROM operations ORDER BY operation_id').all()).results).toEqual(before);
});

it('retains completed recovery authority, pending operations and exact expiry-boundary nonces', async () => {
  await pruneRecoveryAuthorizations(db, now, 1000);
  await pruneExpiredAuthenticationState(db, seconds, 1000);
  expect((await db.prepare('SELECT operation_id FROM operations ORDER BY operation_id').all()).results)
    .toEqual(['durable-rename', 'finished-recovery', 'pending-authority', 'used-authority']
      .map(operation_id => ({ operation_id })));
  expect((await db.prepare('SELECT nonce FROM request_nonces ORDER BY nonce').all()).results)
    .toEqual([{ nonce: 'nonce-0' }, { nonce: 'nonce-1' }]);
  expect(await db.prepare("SELECT state FROM installation_keys WHERE key_id = 'retention-key'").first('state')).toBe('active');
});

it('caps preview results and each prune batch without changing current/future replay state', async () => {
  await db.batch(Array.from({ length: 1002 }, (_, index) => db.prepare('INSERT INTO request_nonces VALUES (?, ?, ?, ?)')
    .bind('retention-key', `old-${index}`, seconds - 1, 'retention-person')));
  expect((await previewRetention(db, now)).categories.find(item => item.category === 'requestNonces'))
    .toEqual({ category: 'requestNonces', eligible: 1000, moreEligible: true });
  await pruneExpiredAuthenticationState(db, seconds, 1000);
  expect(await db.prepare('SELECT COUNT(*) AS count FROM request_nonces WHERE expires_at < ?').bind(seconds).first('count')).toBe(2);
  await pruneExpiredAuthenticationState(db, seconds, 1000);
  expect((await previewRetention(db, now)).categories.find(item => item.category === 'requestNonces'))
    .toEqual({ category: 'requestNonces', eligible: 0, moreEligible: false });
  expect(await db.prepare('SELECT COUNT(*) AS count FROM request_nonces').first('count')).toBe(2);
});

it('preserves live membership evidence, active leases and provider cooldowns', async () => {
  for (const [authority, checked] of [['expired-member', seconds - 900], ['fresh-member', seconds - 899]] as const) {
    await db.prepare("INSERT INTO discord_membership VALUES ('retention-person', ?, 'eligible', ?)").bind(authority, checked).run();
  }
  for (const [authority, lease, retry] of [
    ['expired-member', 0, 0], ['fresh-member', 0, 0], ['idle', 0, 0],
    ['active-lease', seconds + 1, 0], ['provider-cooldown', 0, seconds + 1],
  ] as const) {
    await db.prepare('INSERT INTO discord_membership_refresh (authority, lease_until, retry_at) VALUES (?, ?, ?)')
      .bind(authority, lease, retry).run();
  }
  const before = await previewRetention(db, now);
  expect(before.categories.find(item => item.category === 'membershipEvidence')?.eligible).toBe(1);
  expect(before.categories.find(item => item.category === 'membershipCoordination')?.eligible).toBe(1);
  await pruneBotMembership(db, seconds, 1000);
  expect((await db.prepare('SELECT authority FROM discord_membership').all()).results).toEqual([{ authority: 'fresh-member' }]);
  expect((await db.prepare('SELECT authority FROM discord_membership_refresh ORDER BY authority').all()).results)
    .toEqual(['active-lease', 'fresh-member', 'provider-cooldown'].map(authority => ({ authority })));
});

it('rejects invalid bounds/time and unavailable query metadata instead of fabricating an empty preview', async () => {
  for (const limit of [0, 1001, 1.5]) expect(() => transientStatement(db, 'oauthStates', now, limit, 'prune')).toThrow('invalid_limit');
  for (const time of [-1, NaN, 8_640_000_000_000_001]) await expect(previewRetention(db, time)).rejects.toThrow('invalid_time');
  const unavailable = { prepare: () => ({ bind() { return this; }, first: async () => null }) } as unknown as SqlDatabase;
  await expect(previewRetention(unavailable, now)).rejects.toThrow('retention_preview_unavailable');
});
it('finds expired rate windows and sync runs by their expiry index without a scan or sort', async () => {
  const explain = { prepare: (sql: string) => db.prepare(`EXPLAIN QUERY PLAN ${sql}`), batch: db.batch.bind(db) };
  for (const [category, index] of [['rateCounters', 'idx_rate_counters_expiry'], ['syncRuns', 'idx_sync_runs_expiry']] as const) {
    const plan = (await transientStatement(explain, category, now, 1, 'prune').all<{ detail: string }>()).results
      .map(row => row.detail);
    expect(plan.some(detail => detail.includes(`USING COVERING INDEX ${index} (`))).toBe(true);
    expect(plan.some(detail => /^SCAN |TEMP B-TREE/.test(detail))).toBe(false);
  }
});
