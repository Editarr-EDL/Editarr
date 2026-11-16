import { env } from './helpers/environment';
import { beforeAll, describe, expect, it } from 'vitest';
import identityMigration from '../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../migrations/0003_quotas.sql?raw';
import admissionMigration from '../migrations/0004_request_admission.sql?raw';
import oauthMigration from '../migrations/0005_discord_oauth.sql?raw';
import usageMigration from '../migrations/0006_oauth_usage.sql?raw';
import membershipMigration from '../migrations/0007_membership.sql?raw';
import profileMigration from '../migrations/0008_enrollment_profiles.sql?raw';
import vector from '../../tests/fixtures/community/local-enrollment-request-v1.json';
import contract from '../../tests/fixtures/community/enrollment-profile-v1.json';
import { verifyRequestSignature } from '../src/security/request-signatures';
import { completeEnrollmentProof } from '../src/identity/enrollment-proof';
import { confirmationHash } from '../src/identity/confirmation';
import { contributorProfile } from '../src/identity/profile';

const publicKey = Uint8Array.from(atob(vector.publicKey), (character) => character.charCodeAt(0));
const keyId = 'sha256:56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c';
const policy = { now: 1700000030, maxLifetimeSeconds: 120, maxClockSkewSeconds: 5,
  expectedAuthority: 'community.example', expectedKeyId: keyId };

beforeAll(async () => {
  for (const sql of [identityMigration, budgetMigration, quotaMigration, admissionMigration,
    oauthMigration, usageMigration, membershipMigration, profileMigration]) {
    await env.CATALOG_DB.exec(sql.replace(/\s*\n\s*/g, ' '));
  }
  await env.CATALOG_DB.prepare('UPDATE quota_policy SET enabled = 1').run();
});

function request() {
  return { method: 'POST', url: vector.url, headers: new Headers(vector.headers),
    body: new TextEncoder().encode(vector.body) };
}

describe('Python local enrollment contract', () => {
  it('consumes a bound grant and reconciles a lost response without another identity', async () => {
    const payload = JSON.parse(vector.body);
    const db = env.CATALOG_DB;
    const now = policy.now * 1000;
    await db.prepare(`INSERT INTO enrollment_states (state_id, installation_id, key_id,
      public_key, provider_subject, expires_at, confirmation_hash) VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(payload.stateId, payload.installationId, keyId, publicKey, '1554889314549243999',
        now + 600_000, await confirmationHash(payload.confirmationSecret)).run();
    const enrollmentPolicy = { authority: policy.expectedAuthority, now,
      maxLifetimeSeconds: 120, maxClockSkewSeconds: 5, displayName: 'Cedar Frost Grove',
      requireConfirmation: true };
    const proof = () => new Request(vector.url, { method: 'POST', headers: vector.headers,
      body: vector.body });
    const enrolled = await completeEnrollmentProof(proof(), db, enrollmentPolicy);
    expect(await completeEnrollmentProof(proof(), db, enrollmentPolicy)).toEqual(enrolled);
    expect(await db.prepare('SELECT COUNT(*) AS count FROM contributors').first()).toEqual({ count: 1 });
    expect(await db.prepare('SELECT COUNT(*) AS count FROM installation_keys').first()).toEqual({ count: 1 });
    const profile = await contributorProfile(db, enrolled.contributorId, keyId, 'fixture-v1', now);
    expect(Object.keys(profile).sort()).toEqual([...contract.profileRequiredFields].sort());
    expect(JSON.stringify(profile)).not.toContain('1554889314549243999');
  });

  it('accepts exact Python completion bytes and frozen operation binding', async () => {
    expect(Object.keys(JSON.parse(vector.body))).toEqual(contract.completionRequiredFields);
    expect(new URL(vector.url).pathname).toBe(contract.completionPath);
    expect(await verifyRequestSignature(request(), publicKey, policy)).toMatchObject({
      keyId, operationId: JSON.parse(vector.body).stateId,
      nonce: 'AQIDBAUGBwgJCgsMDQ4PEA', created: 1700000030, expires: 1700000090,
    });
  });

  it('rejects altered secret, wrong audience and clock skew', async () => {
    const changed = request();
    changed.body = new TextEncoder().encode(vector.body.replace('aaaaaaaa', 'cccccccc'));
    await expect(verifyRequestSignature(changed, publicKey, policy)).rejects.toThrow();
    await expect(verifyRequestSignature(request(), publicKey,
      { ...policy, expectedAuthority: 'attacker.example' })).rejects.toThrow();
    await expect(verifyRequestSignature(request(), publicKey,
      { ...policy, now: 1700000096 })).rejects.toThrow();
  });
});