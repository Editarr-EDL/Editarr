import { env } from './helpers/environment';
import { beforeAll, describe, expect, it } from 'vitest';
import migration from '../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../migrations/0003_quotas.sql?raw';
import admissionMigration from '../migrations/0004_request_admission.sql?raw';
import { completeEnrollmentProof } from '../src/identity/enrollment-proof';
import { RequestSignatureTimeError } from '../src/security/request-signatures';
import { signedEnrollment, type SigningOptions } from './helpers/enrollment-signatures';

const db = env.CATALOG_DB;
const now = 1_700_000_030_000;
const authority = 'community.example';
const target = `https://${authority}/v1/enrollments/complete`;
const policy = { authority, now, maxLifetimeSeconds: 120, maxClockSkewSeconds: 5,
  displayName: 'Fixture name' };
const nonce = 'AQIDBAUGBwgJCgsMDQ4PEA';

beforeAll(async () => {
  for (const sql of [migration, budgetMigration, quotaMigration, admissionMigration]) {
    await db.exec(sql.replace(/\s*\n\s*/g, ' '));
  }
});

async function stage(subject = crypto.randomUUID()) {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey));
  const keyId = `sha256:${Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  const stateId = Array.from(crypto.getRandomValues(new Uint8Array(32)),
    (byte) => byte.toString(16).padStart(2, '0')).join('');
  const installationId = crypto.randomUUID();
  await db.prepare(`INSERT INTO enrollment_states
    (state_id, installation_id, key_id, public_key, provider_subject, expires_at)
    VALUES (?, ?, ?, ?, ?, ?)`).bind(stateId, installationId, keyId, publicKey, subject, now + 600_000).run();
  return { stateId, installationId, keyId, publicKey, subject, privateKey: pair.privateKey };
}

function signed(grant: Awaited<ReturnType<typeof stage>>, options: SigningOptions = {}) {
  return signedEnrollment(grant, now, target, options);
}

async function expectUntouched(grant: Awaited<ReturnType<typeof stage>>) {
  expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
    .bind(grant.stateId).first()).toEqual({ consumed_at: null });
  expect(await db.prepare('SELECT 1 FROM installation_keys WHERE key_id = ?')
    .bind(grant.keyId).first()).toBeNull();
  expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ?')
    .bind(grant.keyId).first()).toBeNull();
}

describe('internal signed enrollment handoff', () => {
  it('registers only after key proof and stores the nonce in the same transaction', async () => {
    const grant = await stage();
    const result = await completeEnrollmentProof(await signed(grant), db, policy);
    expect(result).toEqual({ contributorId: expect.any(String),
      installationId: grant.installationId, keyId: grant.keyId });
    expect(JSON.stringify(result)).not.toContain(grant.subject);
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(grant.stateId).first()).toEqual({ consumed_at: now });
    expect(await db.prepare('SELECT nonce, expires_at, contributor_id FROM request_nonces WHERE key_id = ?')
      .bind(grant.keyId).first()).toEqual({ nonce, expires_at: now / 1000 + 55,
        contributor_id: result.contributorId });
    await expect(completeEnrollmentProof(await signed(grant), db, policy)).rejects.toThrow();
  });

  it('rejects a stolen public grant ID signed by another key', async () => {
    const grant = await stage();
    const attacker = await stage();
    await expect(completeEnrollmentProof(await signed(grant, { privateKey: attacker.privateKey }),
      db, policy)).rejects.toThrow('invalid_enrollment_proof');
    await expectUntouched(grant);
  });

  it('permits only one concurrent signed completion', async () => {
    const grant = await stage();
    const requests = await Promise.all([signed(grant), signed(grant)]);
    const results = await Promise.allSettled(requests.map((request) =>
      completeEnrollmentProof(request, db, policy)));
    expect(results.filter((result) => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter((result) => result.status === 'rejected')).toHaveLength(1);
  });

  it.each(['installation', 'version', 'extra', 'oversize', 'operation', 'authority', 'path',
    'query', 'expiry', 'signature', 'digest', 'unsigned'])('rejects %s proof mismatch', async (kind) => {
    const grant = await stage();
    const payload = { version: 1, stateId: grant.stateId, installationId: grant.installationId };
    const body = kind === 'installation' ? JSON.stringify({ ...payload, installationId: crypto.randomUUID() }) :
      kind === 'version' ? JSON.stringify({ ...payload, version: 2 }) :
      kind === 'extra' ? JSON.stringify({ ...payload, discordUserId: grant.subject }) :
      kind === 'oversize' ? ' '.repeat(1025) : JSON.stringify(payload);
    const request = await signed(grant, { body,
      operationId: kind === 'operation' ? 'other' : undefined,
      url: kind === 'authority' ? target.replace(authority, 'other.example') :
        kind === 'path' ? target.replace('complete', 'other') : kind === 'query' ? `${target}?extra=1` : undefined,
      expires: kind === 'expiry' ? now / 1000 - 6 : undefined });
    if (kind === 'signature') request.headers.set('signature', 'editarr=:AAAA:');
    if (kind === 'digest') request.headers.set('content-digest', 'sha-256=:AAAA:');
    if (kind === 'unsigned') request.headers.delete('signature-input');
    await expect(completeEnrollmentProof(request, db, policy)).rejects.toThrow(
      kind === 'expiry' ? RequestSignatureTimeError : 'invalid_enrollment_proof');
    await expectUntouched(grant);
  });

  it.each(['expired', 'unbound', 'consumed'])('rejects an %s grant', async (kind) => {
    const grant = await stage();
    if (kind === 'expired') await db.prepare('UPDATE enrollment_states SET expires_at = ? WHERE state_id = ?')
      .bind(now, grant.stateId).run();
    if (kind === 'unbound') await db.prepare('UPDATE enrollment_states SET provider_subject = NULL WHERE state_id = ?')
      .bind(grant.stateId).run();
    if (kind === 'consumed') await db.prepare('UPDATE enrollment_states SET consumed_at = ? WHERE state_id = ?')
      .bind(now, grant.stateId).run();
    await expect(completeEnrollmentProof(await signed(grant), db, policy)).rejects.toThrow('invalid_enrollment_proof');
  });

  it('rolls back grant and nonce on conflicting registration', async () => {
    const grant = await stage();
    await db.batch([
      db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at)
        VALUES ('existing-fixture', 'existing-subject', 'Existing', ?)`).bind(now),
      db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
        VALUES (?, 'existing-fixture', ?)`).bind(grant.installationId, now),
    ]);
    await expect(completeEnrollmentProof(await signed(grant), db, policy)).rejects.toThrow('enrollment_conflict');
    await expectUntouched(grant);
    expect(await db.prepare('SELECT 1 FROM contributors WHERE discord_user_id = ?')
      .bind(grant.subject).first()).toBeNull();
  });

  it('rolls back all registration writes if the nonce insert fails', async () => {
    const grant = await stage();
    await db.exec(`CREATE TRIGGER reject_fixture_nonce BEFORE INSERT ON request_nonces WHEN NEW.key_id = '${grant.keyId}' BEGIN SELECT RAISE(ABORT, 'fixture_failure'); END;`);
    try {
      await expect(completeEnrollmentProof(await signed(grant), db, policy))
        .rejects.toThrow('enrollment_conflict');
      await expectUntouched(grant);
      expect(await db.prepare('SELECT 1 FROM contributors WHERE discord_user_id = ?')
        .bind(grant.subject).first()).toBeNull();
      expect(await db.prepare('SELECT 1 FROM installations WHERE installation_id = ?')
        .bind(grant.installationId).first()).toBeNull();
    } finally {
      await db.exec('DROP TRIGGER reject_fixture_nonce');
    }
    await expect(completeEnrollmentProof(await signed(grant), db, policy)).resolves.toMatchObject({
      installationId: grant.installationId });
  });

  it('keeps one contributor and the original name across new installations', async () => {
    const first = await stage();
    const registered = await completeEnrollmentProof(await signed(first), db, policy);
    const second = await stage(first.subject);
    expect(await completeEnrollmentProof(await signed(second), db,
      { ...policy, displayName: 'Replacement name' })).toMatchObject({
      contributorId: registered.contributorId, installationId: second.installationId });
    expect(await db.prepare('SELECT display_name, state FROM contributors WHERE contributor_id = ?')
      .bind(registered.contributorId).first()).toEqual({ display_name: policy.displayName, state: 'active' });
    expect((await db.prepare('SELECT 1 FROM installations WHERE contributor_id = ?')
      .bind(registered.contributorId).all()).results).toHaveLength(2);
  });

  it('rejects suspended rejoin without consuming its grant or changing moderation', async () => {
    const first = await stage();
    const registered = await completeEnrollmentProof(await signed(first), db, policy);
    await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = ?")
      .bind(registered.contributorId).run();
    const second = await stage(first.subject);
    await expect(completeEnrollmentProof(await signed(second), db, policy))
      .rejects.toThrow('enrollment_conflict');
    await expectUntouched(second);
    expect(await db.prepare('SELECT state FROM contributors WHERE contributor_id = ?')
      .bind(registered.contributorId).first()).toEqual({ state: 'suspended' });
    expect(await db.prepare('SELECT 1 FROM installations WHERE installation_id = ?')
      .bind(second.installationId).first()).toBeNull();
  });

  it.each(['retired', 'revoked'])('never re-registers a %s key as a new installation', async (state) => {
    const original = await stage();
    await completeEnrollmentProof(await signed(original), db, policy);
    await db.prepare('UPDATE installation_keys SET state = ?, retired_at = ? WHERE key_id = ?')
      .bind(state, now, original.keyId).run();
    const next = await stage(original.subject);
    await db.prepare('UPDATE enrollment_states SET key_id = ?, public_key = ? WHERE state_id = ?')
      .bind(original.keyId, original.publicKey, next.stateId).run();
    const replayedKeyGrant = { ...next, keyId: original.keyId, privateKey: original.privateKey };
    await expect(completeEnrollmentProof(await signed(replayedKeyGrant), db, policy))
      .rejects.toThrow('enrollment_conflict');
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(next.stateId).first()).toEqual({ consumed_at: null });
    expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?')
      .bind(original.keyId).first()).toEqual({ state });
    expect(await db.prepare('SELECT 1 FROM installations WHERE installation_id = ?')
      .bind(next.installationId).first()).toBeNull();
  });
});