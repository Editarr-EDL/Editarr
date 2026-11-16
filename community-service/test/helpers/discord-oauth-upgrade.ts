import { env } from './environment';
import { expect } from 'vitest';
import identityMigration from '../../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../../migrations/0003_quotas.sql?raw';
import admissionMigration from '../../migrations/0004_request_admission.sql?raw';
import oauthMigration from '../../migrations/0005_discord_oauth.sql?raw';
import usageMigration from '../../migrations/0006_oauth_usage.sql?raw';
import membershipMigration from '../../migrations/0007_membership.sql?raw';
import profileMigration from '../../migrations/0008_enrollment_profiles.sql?raw';

const db = env.CATALOG_DB;
const previous = [identityMigration, budgetMigration, quotaMigration, admissionMigration, oauthMigration, usageMigration, membershipMigration];
const publicKey = new Uint8Array(32);

async function apply(sql: string) {
  await db.exec(sql.replace(/\s*\n\s*/g, ' '));
}

async function seedIdentity() {
  await db.batch([
    db.prepare(`INSERT INTO contributors
      (contributor_id, discord_user_id, display_name, state, created_at)
      VALUES ('upgrade-contributor', 'upgrade-subject', 'Preserved', 'suspended', 1)`),
    db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
      VALUES ('upgrade-installation', 'upgrade-contributor', 1)`),
    db.prepare(`INSERT INTO installation_keys
      (key_id, installation_id, algorithm, public_key, state, created_at, retired_at)
      VALUES ('upgrade-key', 'upgrade-installation', 'Ed25519', ?, 'retired', 1, 2)`).bind(publicKey),
    db.prepare(`INSERT INTO enrollment_states
      (state_id, installation_id, key_id, public_key, provider_subject, expires_at)
      VALUES ('upgrade-grant', 'new-installation', 'new-key', ?, 'upgrade-subject', 1000)`).bind(publicKey),
    db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at)
      VALUES ('upgrade-key', 'upgrade-nonce', 1000)`),
    db.prepare(`INSERT INTO operations
      (contributor_id, kind, operation_id, request_digest, state, created_at, updated_at)
      VALUES ('upgrade-contributor', 'receipt', 'upgrade-operation', 'preserved-digest', 'pending', 1, 1)`),
  ]);
}

export async function verifyOAuthUpgrade(version: number) {
  for (let index = 0; index < version; index += 1) {
    await apply(previous[index]);
    if (index === 0) await seedIdentity();
  }
  if (version >= 3) {
    await db.prepare(`INSERT INTO quota_counters
      (contributor_id, resource, period, used, updated_at)
      VALUES ('upgrade-contributor', 'worker_requests', '2026-09-30', 42, 1)`).run();
  }
  if (version >= 5) {
    await db.prepare(`INSERT INTO discord_oauth_states
      (state_id, browser_hash, installation_id, key_id, public_key, confirmed, expires_at)
      VALUES ('pending-oauth-state', 'private-browser-hash', 'pending-installation', 'pending-key', ?, 1, 1000)`)
      .bind(publicKey).run();
  }
  for (let index = version; index < previous.length; index += 1) await apply(previous[index]);
  if (version >= 6) {
    await db.prepare(`INSERT INTO oauth_usage_counters (resource, period, units, updated_at)
      VALUES ('worker_requests', '2026-09-30', 73, 1)`).run();
  }
  if (version >= 7) {
    await db.prepare(`INSERT INTO discord_membership_refresh (authority, lease_id, lease_until, retry_at)
      VALUES ('upgrade-authority', 'preserved-lease', 100, 200)`).run();
  }
  await apply(profileMigration);
  if (version >= 7) {
    expect(await db.prepare('SELECT lease_id, lease_until, retry_at FROM discord_membership_refresh').first())
      .toEqual({ lease_id: 'preserved-lease', lease_until: 100, retry_at: 200 });
  }
  if (version > 0) {
    expect(await db.prepare('SELECT display_name_key, profile_updated_at FROM contributors WHERE contributor_id = ?')
      .bind('upgrade-contributor').first()).toEqual({ display_name_key: 'preserved', profile_updated_at: 0 });
  }
  expect(await db.prepare('SELECT COUNT(*) AS count FROM profile_name_changes').first()).toEqual({ count: 0 });
  if (version >= 3) {
    expect(await db.prepare('SELECT used FROM quota_counters WHERE contributor_id = ?')
      .bind('upgrade-contributor').first()).toEqual({ used: 42 });
  }
  if (version >= 5) {
    expect(await db.prepare('SELECT browser_hash, confirmed, consumed_at FROM discord_oauth_states WHERE state_id = ?')
      .bind('pending-oauth-state').first())
      .toEqual({ browser_hash: 'private-browser-hash', confirmed: 1, consumed_at: null });
  }
  expect(await db.prepare('SELECT COUNT(*) AS count FROM oauth_usage_counters').first())
    .toEqual({ count: version >= 6 ? 1 : 0 });
  if (version >= 6) {
    expect(await db.prepare('SELECT units FROM oauth_usage_counters').first()).toEqual({ units: 73 });
  }
  expect(await db.prepare('SELECT COUNT(*) AS count FROM discord_membership').first()).toEqual({ count: 0 });
  if (version > 0) {
    expect(await db.prepare('SELECT display_name, state FROM contributors WHERE contributor_id = ?')
      .bind('upgrade-contributor').first()).toEqual({ display_name: 'Preserved', state: 'suspended' });
    expect(await db.prepare('SELECT state, retired_at, public_key FROM installation_keys WHERE key_id = ?')
      .bind('upgrade-key').first()).toEqual({ state: 'retired', retired_at: 2, public_key: publicKey.buffer });
    expect(await db.prepare('SELECT provider_subject, consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind('upgrade-grant').first()).toEqual({ provider_subject: 'upgrade-subject', consumed_at: null });
    expect(await db.prepare('SELECT nonce, expires_at FROM request_nonces WHERE key_id = ?')
      .bind('upgrade-key').first()).toEqual({ nonce: 'upgrade-nonce', expires_at: 1000 });
    expect(await db.prepare('SELECT request_digest, state FROM operations WHERE operation_id = ?')
      .bind('upgrade-operation').first()).toEqual({ request_digest: 'preserved-digest', state: 'pending' });
  }
  await db.prepare(`INSERT INTO discord_oauth_states
    (state_id, browser_hash, installation_id, key_id, public_key, expires_at)
    VALUES ('new-oauth-state', 'browser-hash', 'installation', 'key', ?, 1000)`).bind(publicKey).run();
  expect(await db.prepare('SELECT confirmed, consumed_at FROM discord_oauth_states WHERE state_id = ?')
    .bind('new-oauth-state').first()).toEqual({ confirmed: 0, consumed_at: null });
}