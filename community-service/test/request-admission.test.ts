import { env } from './helpers/environment';
import { applyBaseline } from './helpers/schema';
import { beforeAll, describe, expect, it } from 'vitest';
import { admitSignedMutation } from '../src/operations/repository';

const db = env.CATALOG_DB;
beforeAll(async () => {
  await applyBaseline(db);
});

async function identity() {
  const contributorId = crypto.randomUUID();
  const installationId = crypto.randomUUID();
  const keyId = crypto.randomUUID();
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at, display_name_key)
      VALUES (?, ?, 'Admission fixture', 1, lower(hex(randomblob(8))))`).bind(contributorId, crypto.randomUUID()),
    db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
      VALUES (?, ?, 1)`).bind(installationId, contributorId),
    db.prepare(`INSERT INTO installation_keys
      (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(keyId, installationId, new Uint8Array(32)),
  ]);
  return { contributorId, keyId };
}

describe('signed mutation admission', () => {
  it('admits one concurrent nonce, accepts a fresh-nonce retry, and rejects changed payload', async () => {
    const { contributorId, keyId } = await identity();
    const input = { contributorId, keyId, nonce: crypto.randomUUID(), expiresAt: 200,
      kind: 'fixture', operationId: crypto.randomUUID(), requestDigest: 'digest', now: 100 };
    const results = await Promise.allSettled(Array.from({ length: 8 }, () =>
      admitSignedMutation(db, input)));
    expect(results.filter((result) => result.status === 'fulfilled')).toHaveLength(1);
    expect(await admitSignedMutation(db, { ...input, nonce: crypto.randomUUID() })).toBe('existing');
    await expect(admitSignedMutation(db, { ...input, nonce: crypto.randomUUID(),
      requestDigest: 'different' })).rejects.toThrow('operation_conflict');
    expect((await db.prepare('SELECT COUNT(*) AS total FROM request_nonces WHERE key_id = ?')
      .bind(keyId).first<{ total: number }>())?.total).toBe(2);
  });

  it('rejects suspended contributors, retired keys and cross-contributor admission', async () => {
    const { contributorId, keyId } = await identity();
    const other = await identity();
    const input = { contributorId, keyId, nonce: crypto.randomUUID(), expiresAt: 200,
      kind: 'fixture', operationId: crypto.randomUUID(), requestDigest: 'digest', now: 100 };
    await expect(admitSignedMutation(db, { ...input, contributorId: other.contributorId }))
      .rejects.toThrow('key_inactive');
    await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = ?")
      .bind(contributorId).run();
    await expect(admitSignedMutation(db, input)).rejects.toThrow('key_inactive');
    await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = ?")
      .bind(contributorId).run();
    await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = 110 WHERE key_id = ?")
      .bind(keyId).run();
    await expect(admitSignedMutation(db, input)).rejects.toThrow('key_inactive');
  });
});