import { env } from './helpers/environment';
import { beforeAll, expect, it } from 'vitest';
import { recoverInstallation, pruneRecoveryAuthorizations } from '../src/identity/recovery';
import { completeEnrollmentProof } from '../src/identity/enrollment-proof';
import { confirmationHash } from '../src/identity/confirmation';
import { signedEnrollment } from './helpers/enrollment-signatures';
import migration1 from '../migrations/0001_identity_operations.sql?raw';
import migration2 from '../migrations/0002_budget_ledger.sql?raw';
import migration3 from '../migrations/0003_quotas.sql?raw';
import migration4 from '../migrations/0004_request_admission.sql?raw';
import migration5 from '../migrations/0005_discord_oauth.sql?raw';
import migration6 from '../migrations/0006_oauth_usage.sql?raw';
import migration7 from '../migrations/0007_membership.sql?raw';
import migration8 from '../migrations/0008_enrollment_profiles.sql?raw';

const db = env.CATALOG_DB;
const now = 1_700_000_030_000;
const authority = 'community.example';
const migrations = [migration1, migration2, migration3, migration4, migration5, migration6, migration7, migration8];
beforeAll(async () => {
  for (const migration of migrations) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  await db.prepare('UPDATE quota_policy SET enabled = 1').run();
});

async function fixture() {
  const contributorId = crypto.randomUUID();
  const installationId = crypto.randomUUID();
  const siblingId = crypto.randomUUID();
  const oldKeyId = `sha256:${'a'.repeat(64)}`;
  const siblingKeyId = `sha256:${crypto.randomUUID().replaceAll('-', '').repeat(2)}`;
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const keyId = `sha256:${Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey)),
    (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  const uniqueOldKey = oldKeyId.slice(0, 39) + crypto.randomUUID().replaceAll('-', '');
  const stateId = crypto.randomUUID().replaceAll('-', '').repeat(2);
  const operationId = crypto.randomUUID();
  const secret = 'b'.repeat(64);
  const hash = await confirmationHash(secret);
  const intent = { version: 1, operationId, installationId, oldKeyId: uniqueOldKey,
    newKeyId: keyId, publicKey: btoa(String.fromCharCode(...publicKey)), confirmationHash: hash, expiresAt: now + 600_000 };
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES (?, ?, 'Stable Name', ?, 1)`).bind(contributorId, contributorId, contributorId),
    ...[installationId, siblingId].map((id) => db.prepare('INSERT INTO installations VALUES (?, ?, 1)').bind(id, contributorId)),
    ...[[uniqueOldKey, installationId], [siblingKeyId, siblingId]].map(([id, installation]) =>
      db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
        VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(id, installation, publicKey)),
    db.prepare(`INSERT INTO enrollment_states (state_id, installation_id, key_id, public_key, provider_subject, expires_at, confirmation_hash)
      VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(stateId, installationId, keyId, publicKey, contributorId, now + 600_000, hash),
    db.prepare(`INSERT INTO operations VALUES (?, 'key_recovery_authorization', ?, 'bound', 'completed', ?, ?, ?)`)
      .bind(contributorId, stateId, JSON.stringify(intent), now, now),
  ]);
  const body = JSON.stringify({ version: 1, stateId, operationId, installationId,
    oldKeyId: uniqueOldKey, confirmationSecret: secret });
  const request = (status = false, override = {}, signedAt = now) => signedEnrollment({ stateId, installationId, keyId,
    privateKey: pair.privateKey }, signedAt, `https://${authority}/v1/keys/${status ? 'recovery-status' : 'recover'}`,
  { body, operationId, ...override });
  return { contributorId, installationId, siblingKeyId, oldKeyId: uniqueOldKey, keyId, stateId, body, request };
}

it('recovers all-key loss without an old proof, preserving sibling keys and contributor state', async () => {
  const fixtureData = await fixture();
  const outcome = await recoverInstallation(await fixtureData.request(), db, authority, now, async () => {});
  expect(outcome.result).toMatchObject({ kind: 'key_recovery', contributorId: fixtureData.contributorId });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(fixtureData.oldKeyId).first()).toEqual({ state: 'retired' });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(fixtureData.siblingKeyId).first()).toEqual({ state: 'active' });
  expect(await db.prepare('SELECT display_name, state FROM contributors WHERE contributor_id = ?')
    .bind(fixtureData.contributorId).first()).toEqual({ display_name: 'Stable Name', state: 'active' });
  await db.prepare('DELETE FROM enrollment_states WHERE state_id = ?').bind(fixtureData.stateId).run();
  expect(await recoverInstallation(await fixtureData.request(true, {}, now + 700_000), db, authority, now + 700_000, async () => {})).toEqual(outcome);
});

it('reconciles concurrent identical completions without recharging', async () => {
  const fixtureData = await fixture();
  const results = await Promise.all([fixtureData.request(), fixtureData.request()].map(async (request) =>
    recoverInstallation(await request, db, authority, now, async () => {})));
  expect(results[1]).toEqual(results[0]);
  expect((await db.prepare('SELECT * FROM quota_charges WHERE contributor_id = ?').bind(fixtureData.contributorId).all()).results).toHaveLength(0);
});

it.each(['foreign', 'suspended', 'secret', 'expired', 'membership', 'signature'])('rejects %s recovery without consuming authority', async (kind) => {
  const fixtureData = await fixture();
  if (kind === 'foreign') await db.prepare('UPDATE enrollment_states SET provider_subject = ? WHERE state_id = ?').bind('foreign', fixtureData.stateId).run();
  if (kind === 'suspended') await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = ?").bind(fixtureData.contributorId).run();
  if (kind === 'expired') await db.prepare('UPDATE enrollment_states SET expires_at = ? WHERE state_id = ?').bind(now, fixtureData.stateId).run();
  const request = await fixtureData.request(false, kind === 'secret' ? {
    body: JSON.stringify({ ...JSON.parse(fixtureData.body), confirmationSecret: 'd'.repeat(64) }),
  } : {});
  if (kind === 'signature') request.headers.delete('signature');
  await expect(recoverInstallation(request, db, authority, now, async () => {
    if (kind === 'membership') throw new Error('membership_unavailable');
  })).rejects.toThrow();
  expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?').bind(fixtureData.stateId).first()).toEqual({ consumed_at: null });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(fixtureData.oldKeyId).first()).toEqual({ state: 'active' });
});

it('recovers a revoked key but never reactivates a revoked replacement through reconciliation', async () => {
  const fixtureData = await fixture();
  await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = ?").bind(now, fixtureData.oldKeyId).run();
  await recoverInstallation(await fixtureData.request(), db, authority, now, async () => {});
  await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = ?").bind(now, fixtureData.keyId).run();
  await expect(recoverInstallation(await fixtureData.request(true), db, authority, now, async () => {})).rejects.toThrow('key_inactive');
});

it('status is read-only and historical caps cannot block replacement', async () => {
  const fixtureData = await fixture();
  expect(await recoverInstallation(await fixtureData.request(true), db, authority, now, async () => {})).toEqual({ version: 1, state: 'not_committed', result: null });
  await db.prepare("UPDATE quota_caps SET cap = 2 WHERE resource = 'worker_requests'").run();
  await db.prepare("INSERT INTO quota_charges VALUES (?, ?, 'worker_requests', ?, 1, ?)").bind(crypto.randomUUID(), fixtureData.contributorId,
    new Date(now).toISOString().slice(0, 10), now).run();
  try {
    await expect(recoverInstallation(await fixtureData.request(), db, authority, now, async () => {})).resolves.toMatchObject({ state: 'completed' });
    expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(fixtureData.oldKeyId).first()).toEqual({ state: 'retired' });
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?').bind(fixtureData.stateId).first()).toEqual({ consumed_at: now });
  } finally { await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run(); }
});

it('cannot consume recovery authority through ordinary enrollment', async () => {
  const fixtureData = await fixture();
  const recoveryRequest = await fixtureData.request();
  const payload = JSON.parse(await recoveryRequest.text());
  const request = new Request(`https://${authority}/v1/enrollments/complete`, { method: 'POST',
    headers: { 'content-type': 'application/json', 'idempotency-key': fixtureData.stateId },
    body: JSON.stringify({ version: 1, stateId: fixtureData.stateId,
      installationId: fixtureData.installationId, confirmationSecret: payload.confirmationSecret }) });
  await expect(completeEnrollmentProof(request, db, { authority, now, maxLifetimeSeconds: 120,
    maxClockSkewSeconds: 5, displayName: 'Unused', requireConfirmation: true })).rejects.toThrow('recovery_required');
});

it('expiry cleanup is bounded and retains committed recovery evidence', async () => {
  const committed = await fixture();
  const unused = await fixture();
  await recoverInstallation(await committed.request(), db, authority, now, async () => {});
  await pruneRecoveryAuthorizations(db, now + 700_000, 1000);
  expect(await db.prepare("SELECT 1 FROM operations WHERE kind = 'key_recovery_authorization' AND operation_id = ?")
    .bind(unused.stateId).first()).toBeNull();
  expect(await db.prepare("SELECT 1 FROM operations WHERE kind = 'key_recovery_authorization' AND operation_id = ?")
    .bind(committed.stateId).first()).not.toBeNull();
  await expect(pruneRecoveryAuthorizations(db, now, 0)).rejects.toThrow('invalid_limit');
});

it('a stale authorization cannot retire a later replacement, even with fresh candidate proof', async () => {
  const fixtureData = await fixture();
  const laterKey = crypto.randomUUID();
  await db.batch([
    db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = ?").bind(now, fixtureData.oldKeyId),
    db.prepare("INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at) VALUES (?, ?, 'Ed25519', ?, 'active', ?)")
      .bind(laterKey, fixtureData.installationId, crypto.getRandomValues(new Uint8Array(32)), now),
  ]);
  for (const status of [true, false]) await expect(recoverInstallation(await fixtureData.request(status), db,
    authority, now, async () => {})).rejects.toThrow('recovery_conflict');
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(laterKey).first()).toEqual({ state: 'active' });
  expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?').bind(fixtureData.stateId).first()).toEqual({ consumed_at: null });
});

it('candidate activation failure rolls back retirement, grant consumption and operation publication', async () => {
  const fixtureData = await fixture();
  const otherInstallation = crypto.randomUUID();
  await db.prepare('INSERT INTO installations VALUES (?, ?, 1)').bind(otherInstallation, fixtureData.contributorId).run();
  await db.prepare("INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at) VALUES (?, ?, 'Ed25519', ?, 'active', 1)")
    .bind(fixtureData.keyId, otherInstallation, crypto.getRandomValues(new Uint8Array(32))).run();
  await expect(recoverInstallation(await fixtureData.request(), db, authority, now, async () => {})).rejects.toThrow('recovery_conflict');
  expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?').bind(fixtureData.stateId).first()).toEqual({ consumed_at: null });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(fixtureData.oldKeyId).first()).toEqual({ state: 'active' });
  expect(await db.prepare("SELECT 1 FROM operations WHERE contributor_id = ? AND kind = 'key_recovery'").bind(fixtureData.contributorId).first()).toBeNull();
});