import type { SqlDatabase, SqlStatement } from '../src/runtime/contracts';
import { applyBaseline } from './helpers/schema';
import { env } from './helpers/environment';
import { beforeAll, describe, expect, it } from 'vitest';
import { enroll, replaceInstallationKey } from '../src/identity/repository';
import { completeOperation, pruneExpiredAuthenticationState, 
  reserveOperation } from '../src/operations/repository';

const db = env.CATALOG_DB;
const key = new Uint8Array([1, 2, 3]);

beforeAll(async () => {
  await applyBaseline(db);
  // These cases exercise the unconfirmed registration path, which stores no profile name key.
  await db.exec('DROP TRIGGER guard_profile_name_insert; DROP INDEX uq_contributors_display_name;');
});

async function stage(installationId: string, keyId: string, subject: string, now = 100) {
  const stateId = crypto.randomUUID();
  await db.prepare(`INSERT INTO enrollment_states
    (state_id, installation_id, key_id, public_key, provider_subject, expires_at)
    VALUES (?, ?, ?, ?, ?, ?)`).bind(stateId, installationId, keyId, key, subject, now + 60).run();
  return stateId;
}

describe('SQLite identity and operation persistence', () => {
  it('resolves concurrent Discord enrollment to one contributor and preserves separate keys', async () => {
    const discordUserId = crypto.randomUUID();
    const firstId = crypto.randomUUID();
    const secondId = crypto.randomUUID();
    const firstKey = crypto.randomUUID();
    const secondKey = crypto.randomUUID();
    const [first, second] = await Promise.all([
      enroll(db, { stateId: await stage(firstId, firstKey, discordUserId),
        discordUserId, contributorId: crypto.randomUUID(), installationId: firstId,
        keyId: firstKey, publicKey: key, displayName: 'First', now: 100 }),
      enroll(db, { stateId: await stage(secondId, secondKey, discordUserId),
        discordUserId, contributorId: crypto.randomUUID(), installationId: secondId,
        keyId: secondKey, publicKey: key, displayName: 'Second', now: 100 }),
    ]);
    expect(first.contributor_id).toBe(second.contributor_id);
    const rows = await db.prepare(`SELECT key_id FROM installation_keys
      WHERE state = 'active' AND installation_id IN (?, ?)`).bind(firstId, secondId).all();
    expect(rows.results).toHaveLength(2);
  });

  it('allows only one concurrent consumption of the same grant', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    const stateId = await stage(installationId, keyId, discordUserId);
    const input = { stateId, discordUserId, contributorId: crypto.randomUUID(),
      installationId, keyId, publicKey: key, displayName: 'Fixture', now: 100 };
    const outcomes = await Promise.allSettled([enroll(db, input), enroll(db, input)]);
    expect(outcomes.filter((outcome) => outcome.status === 'fulfilled')).toHaveLength(1);
    expect(outcomes.filter((outcome) => outcome.status === 'rejected')).toHaveLength(1);
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ consumed_at: 100 });
  });

  it.each([
    ['installation_id', crypto.randomUUID()],
    ['key_id', crypto.randomUUID()],
    ['public_key', new Uint8Array([4, 5, 6])],
    ['provider_subject', crypto.randomUUID()],
    ['expires_at', 100],
    ['consumed_at', 99],
  ])('rejects a grant whose %s changes before the batch', async (column, replacement) => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    const stateId = await stage(installationId, keyId, discordUserId);
    const racingDb = {
      prepare: db.prepare.bind(db),
      batch: async (statements: SqlStatement[]) => {
        await db.prepare(`UPDATE enrollment_states SET ${column} = ? WHERE state_id = ?`)
          .bind(replacement, stateId).run();
        return db.batch(statements);
      },
    } as SqlDatabase;
    await expect(enroll(racingDb, { stateId, discordUserId, contributorId: crypto.randomUUID(),
      installationId, keyId, publicKey: key, displayName: 'Fixture', now: 100 })).rejects.toThrow();
    expect(await db.prepare('SELECT 1 FROM contributors WHERE discord_user_id = ?')
      .bind(discordUserId).first()).toBeNull();
    expect(await db.prepare('SELECT 1 FROM installations WHERE installation_id = ?')
      .bind(installationId).first()).toBeNull();
    expect(await db.prepare('SELECT 1 FROM installation_keys WHERE key_id = ?')
      .bind(keyId).first()).toBeNull();
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ consumed_at: column === 'consumed_at' ? 99 : null });
  });

  it('rolls back invalid enrollment without leaving a contributor or installation', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    const stateId = await stage(installationId, keyId, discordUserId, 100);
    const enrollment = { stateId, discordUserId, contributorId: crypto.randomUUID(),
      installationId, keyId, publicKey: key, displayName: 'Fixture', now: 100 };
    await db.prepare('UPDATE enrollment_states SET expires_at = 99 WHERE state_id = ?')
      .bind(stateId).run();
    await expect(enroll(db, enrollment)).rejects.toThrow('enrollment_state_invalid');
    expect(await db.prepare('SELECT * FROM contributors WHERE discord_user_id = ?')
      .bind(discordUserId).first()).toBeNull();
    expect(await db.prepare('SELECT * FROM installations WHERE installation_id = ?')
      .bind(installationId).first()).toBeNull();
  });

  it('rolls back the new contributor when an installation belongs to another account', async () => {
    const installationId = crypto.randomUUID();
    const firstUser = crypto.randomUUID();
    const firstKey = crypto.randomUUID();
    await enroll(db, { stateId: await stage(installationId, firstKey, firstUser),
      discordUserId: firstUser, contributorId: crypto.randomUUID(), installationId,
      keyId: firstKey, publicKey: key, displayName: 'First', now: 100 });
    const secondUser = crypto.randomUUID();
    const secondKey = crypto.randomUUID();
    const stateId = await stage(installationId, secondKey, secondUser);
    await expect(enroll(db, { stateId,
      discordUserId: secondUser, contributorId: crypto.randomUUID(), installationId,
      keyId: secondKey, publicKey: key, displayName: 'Second', now: 100 })).rejects.toThrow();
    expect(await db.prepare('SELECT * FROM contributors WHERE discord_user_id = ?')
      .bind(secondUser).first()).toBeNull();
    expect(await db.prepare('SELECT * FROM installation_keys WHERE key_id = ?')
      .bind(secondKey).first()).toBeNull();
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ consumed_at: null });
  });

  it('retires only the targeted key, and a stale replacement cannot retire it again', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    await enroll(db, { stateId: await stage(installationId, keyId, discordUserId),
      discordUserId, contributorId: crypto.randomUUID(), installationId, keyId,
      publicKey: key, displayName: 'Fixture', now: 100 });
    const replacement = crypto.randomUUID();
    await replaceInstallationKey(db, installationId, keyId, replacement, key, 110);
    await expect(replaceInstallationKey(db, installationId, keyId, crypto.randomUUID(), key, 120))
      .rejects.toThrow();
    const rows = await db.prepare('SELECT key_id, state FROM installation_keys WHERE installation_id = ?')
      .bind(installationId).all<{ key_id: string; state: string }>();
    expect(rows.results).toEqual(expect.arrayContaining([
      { key_id: keyId, state: 'retired' }, { key_id: replacement, state: 'active' },
    ]));
    expect(rows.results).toHaveLength(2);
  });

  it('never turns fresh Discord enrollment into replacement of an existing installation', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const originalKey = crypto.randomUUID();
    const contributor = await enroll(db, {
      stateId: await stage(installationId, originalKey, discordUserId),
      discordUserId, contributorId: crypto.randomUUID(), installationId,
      keyId: originalKey, publicKey: key, displayName: 'Preserved', now: 100,
    });
    const replacementKey = crypto.randomUUID();
    const stateId = await stage(installationId, replacementKey, discordUserId);
    await expect(enroll(db, { stateId, discordUserId, contributorId: crypto.randomUUID(),
      installationId, keyId: replacementKey, publicKey: key,
      displayName: 'Must not replace', now: 110 })).rejects.toThrow();
    expect(await db.prepare('SELECT contributor_id, display_name, state FROM contributors WHERE discord_user_id = ?')
      .bind(discordUserId).first()).toEqual({ contributor_id: contributor.contributor_id,
      display_name: 'Preserved', state: 'active' });
    expect((await db.prepare('SELECT key_id, state FROM installation_keys WHERE installation_id = ?')
      .bind(installationId).all()).results).toEqual([{ key_id: originalKey, state: 'active' }]);
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ consumed_at: null });
  });

  it('deduplicates equal operation IDs but rejects different digests and terminal rewrites', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    const contributor = await enroll(db, { stateId: await stage(installationId, keyId, discordUserId),
      discordUserId, contributorId: crypto.randomUUID(), installationId, keyId,
      publicKey: key, displayName: 'Fixture', now: 100 });
    const operation = { contributorId: contributor.contributor_id, kind: 'receipt',
      operationId: crypto.randomUUID(), requestDigest: 'sha256:fixture', now: 110 };
    expect(await reserveOperation(db, operation)).toBe('created');
    expect(await reserveOperation(db, operation)).toBe('existing');
    await expect(reserveOperation(db, { ...operation, requestDigest: 'other' }))
      .rejects.toThrow('operation_conflict');
    await completeOperation(db, operation, '{}');
    await expect(completeOperation(db, operation, '{}')).rejects.toThrow('operation_conflict');
  });

  it('rejects unbound enrollment and replays of a nonce, with bounded expiry cleanup', async () => {
    const discordUserId = crypto.randomUUID();
    const installationId = crypto.randomUUID();
    const keyId = crypto.randomUUID();
    const stateId = await stage(installationId, keyId, discordUserId);
    await db.prepare('UPDATE enrollment_states SET provider_subject = NULL WHERE state_id = ?')
      .bind(stateId).run();
    const input = { stateId, discordUserId, contributorId: crypto.randomUUID(),
      installationId, keyId, publicKey: key, displayName: 'Fixture', now: 100 };
    await expect(enroll(db, input)).rejects.toThrow('enrollment_state_invalid');
    await db.prepare('UPDATE enrollment_states SET provider_subject = ? WHERE state_id = ?')
      .bind(discordUserId, stateId).run();
    const enrolled = await enroll(db, input);
    const nonce = crypto.randomUUID();
    const reserve = () => db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
      .bind(keyId, nonce, 120, enrolled.contributor_id).run();
    await reserve();
    await expect(reserve()).rejects.toThrow();
    await pruneExpiredAuthenticationState(db, 121, 1);
    expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ? AND nonce = ?')
      .bind(keyId, nonce).first()).toBeNull();
  });

  it('uses indexed account, active-key, nonce and operation lookups', async () => {
    const queries = [
      ['SELECT contributor_id FROM contributors WHERE discord_user_id = ?', 'sqlite_autoindex_contributors'],
      ["SELECT key_id FROM installation_keys WHERE installation_id = ? AND state = 'active'", 'uq_installation_keys_active'],
      ['SELECT nonce FROM request_nonces WHERE key_id = ? AND nonce = ?', 'sqlite_autoindex_request_nonces'],
      ['SELECT state FROM operations WHERE contributor_id = ? AND kind = ? AND operation_id = ?', 'sqlite_autoindex_operations'],
    ];
    for (const [sql, index] of queries) {
      const plan = await db.prepare(`EXPLAIN QUERY PLAN ${sql}`)
        .bind(...Array((sql.match(/\?/g) ?? []).length).fill('fixture'))
        .all<{ detail: string }>();
      expect(plan.results.some((row) => row.detail.includes(index)), sql).toBe(true);
    }
  });
});