import { env } from './helpers/environment';
import { beforeAll, expect, it } from 'vitest';
import identityMigration from '../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../migrations/0003_quotas.sql?raw';
import admissionMigration from '../migrations/0004_request_admission.sql?raw';
import { changeInstallationKey, keyOperation } from '../src/identity/key-lifecycle';
import type { AuthenticatedContributor } from '../src/security/contributor-auth';

const db = env.CATALOG_DB;
const now = 1_700_000_030_000;
beforeAll(async () => {
  for (const migration of [identityMigration, budgetMigration, quotaMigration, admissionMigration]) {
    await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  }
  await db.prepare('UPDATE quota_policy SET enabled = 1').run();
});

async function fixture() {
  const contributorId = crypto.randomUUID();
  const installationId = crypto.randomUUID();
  const oldKeyId = crypto.randomUUID();
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at)
      VALUES (?, ?, 'Historical Name', 1)`).bind(contributorId, contributorId),
    db.prepare('INSERT INTO installations (installation_id, contributor_id, created_at) VALUES (?, ?, 1)')
      .bind(installationId, contributorId),
    db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(oldKeyId, installationId, publicKey),
  ]);
  const identity: AuthenticatedContributor = { contributorId, keyId: oldKeyId,
    displayName: 'Historical Name', nonce: crypto.randomUUID(), operationId: crypto.randomUUID(),
    contentDigest: 'exact-body', expiresAt: now / 1000 + 60 };
  const input = { installationId, oldKeyId,
    newKey: { keyId: crypto.randomUUID(), publicKey: crypto.getRandomValues(new Uint8Array(32)) } };
  return { identity, input, pair, publicKey };
}

it('atomically rotates one installation and preserves historic public signatures', async () => {
  const { identity, input, pair, publicKey } = await fixture();
  const bytes = new TextEncoder().encode('previously signed artifact');
  const signature = await crypto.subtle.sign('Ed25519', pair.privateKey, bytes);
  const result = await changeInstallationKey(db, identity, input, now);
  expect(result).toMatchObject({ contributorId: identity.contributorId, installationId: input.installationId });
  const old = await db.prepare('SELECT state, public_key FROM installation_keys WHERE key_id = ?')
    .bind(input.oldKeyId).first<{ state: string; public_key: ArrayBuffer }>();
  expect(old!.state).toBe('retired');
  expect(new Uint8Array(old!.public_key)).toEqual(publicKey);
  const historic = await crypto.subtle.importKey('raw', new Uint8Array(old!.public_key), 'Ed25519', false, ['verify']);
  expect(await crypto.subtle.verify('Ed25519', historic, signature, bytes)).toBe(true);
  expect(await keyOperation(db, identity.contributorId, 'key_rotation', identity.operationId!)).toMatchObject({ state: 'completed' });
});

it('concurrent different replacements activate exactly one key', async () => {
  const { identity, input } = await fixture();
  const outcomes = await Promise.allSettled([
    changeInstallationKey(db, identity, input, now),
    changeInstallationKey(db, { ...identity, nonce: crypto.randomUUID(), operationId: crypto.randomUUID() },
      { ...input, newKey: { ...input.newKey, keyId: crypto.randomUUID() } }, now),
  ]);
  expect(outcomes.filter((result) => result.status === 'fulfilled')).toHaveLength(1);
  expect((await db.prepare("SELECT key_id FROM installation_keys WHERE installation_id = ? AND state = 'active'")
    .bind(input.installationId).all()).results).toHaveLength(1);
});

it('concurrent identical operations reconcile and changed bytes conflict', async () => {
  const { identity, input } = await fixture();
  const outcomes = await Promise.all([
    changeInstallationKey(db, identity, input, now),
    changeInstallationKey(db, { ...identity, nonce: crypto.randomUUID() }, input, now),
  ]);
  expect(outcomes[1]).toEqual(outcomes[0]);
  await expect(changeInstallationKey(db, { ...identity, contentDigest: 'changed' }, input, now))
    .rejects.toThrow('operation_conflict');
  expect((await db.prepare('SELECT * FROM quota_charges WHERE contributor_id = ?')
    .bind(identity.contributorId).all()).results).toHaveLength(0);
});

it('rolls back nonce, retirement and operation when activation fails', async () => {
  const { identity, input } = await fixture();
  await expect(changeInstallationKey(db, identity, { ...input, newKey: { ...input.newKey, keyId: input.oldKeyId } }, now))
    .rejects.toThrow('key_operation_conflict');
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(input.oldKeyId).first())
    .toEqual({ state: 'active' });
  expect(await keyOperation(db, identity.contributorId, 'key_rotation', identity.operationId!)).toBeNull();
  expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ?').bind(input.oldKeyId).first()).toBeNull();
});

it('rejects foreign targets and retirement between proof and transaction', async () => {
  const own = await fixture();
  const foreign = await fixture();
  await expect(changeInstallationKey(db, own.identity, foreign.input, now)).rejects.toThrow('key_operation_conflict');
  await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = ?")
    .bind(now, own.input.oldKeyId).run();
  await expect(changeInstallationKey(db, own.identity, own.input, now)).rejects.toThrow('key_inactive');
});

it('historical exhausted counters cannot block key rotation', async () => {
  const { identity, input } = await fixture();
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
  await db.prepare(`INSERT INTO quota_charges (charge_id, contributor_id, resource, period, units, created_at)
    VALUES (?, ?, 'worker_requests', ?, 1, ?)`).bind(crypto.randomUUID(), identity.contributorId,
      new Date(now).toISOString().slice(0, 10), now).run();
  try {
    await expect(changeInstallationKey(db, identity, input, now)).resolves.toMatchObject({ newKeyId: input.newKey.keyId });
    expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(input.oldKeyId).first())
      .toEqual({ state: 'retired' });
    expect(await keyOperation(db, identity.contributorId, 'key_rotation', identity.operationId!)).not.toBeNull();
    expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ?').bind(input.oldKeyId).first()).not.toBeNull();
  } finally {
    await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run();
  }
});

it('revokes only the target installation under the same contributor', async () => {
  const own = await fixture();
  const siblingId = crypto.randomUUID();
  const siblingKey = crypto.randomUUID();
  await db.batch([
    db.prepare('INSERT INTO installations VALUES (?, ?, 1)').bind(siblingId, own.identity.contributorId),
    db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(siblingKey, siblingId, own.publicKey),
  ]);
  await changeInstallationKey(db, own.identity, { installationId: siblingId, oldKeyId: siblingKey }, now);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(siblingKey).first()).toEqual({ state: 'revoked' });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(own.input.oldKeyId).first()).toEqual({ state: 'active' });
});