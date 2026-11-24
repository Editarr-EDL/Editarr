import { beforeAll, describe, expect, it, vi } from 'vitest';
import { applyBaseline } from './helpers/schema';
import { env } from './helpers/environment';
import { confirmationHash } from '../src/identity/confirmation';
import { completeEnrollmentProof } from '../src/identity/enrollment-proof';
import { contributorEnrollment } from '../src/http/enrollment';
import { contributorIdentity } from '../src/http/identity';
import { contributorProfile } from '../src/identity/profile';
import { pruneProfileNames, renameContributor } from '../src/identity/rename';
import { signedEnrollment } from './helpers/enrollment-signatures';
import service from '../src/index';
import contract from '../../tests/fixtures/community/enrollment-profile-v1.json';
import { discordOAuth } from '../src/integrations/discord/oauth';

const db = env.CATALOG_DB;
const now = Date.parse('2026-10-01T12:00:00Z');
const origin = 'https://community.example';
const bindings = { ...env, PUBLIC_ORIGIN: origin, POLICY_VERSION: 'fixture-policy-1',
  DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'fixture-token'};
const policy = { authority: 'community.example', now, maxLifetimeSeconds: 120,
  maxClockSkewSeconds: 5, displayName: 'Fixture Initial Name', requireConfirmation: true };

beforeAll(async () => {
  await applyBaseline(db);
});

async function grant(subject = '1554889314549243999') {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey));
  const keyId = `sha256:${Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
  const stateId = crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
  const confirmationSecret = crypto.randomUUID().replaceAll('-', '') + crypto.randomUUID().replaceAll('-', '');
  const installationId = crypto.randomUUID();
  await db.prepare(`INSERT INTO enrollment_states (state_id, installation_id, key_id, public_key,
    provider_subject, expires_at, confirmation_hash) VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .bind(stateId, installationId, keyId, publicKey, subject, now + 600_000,
      await confirmationHash(confirmationSecret)).run();
  return { stateId, installationId, keyId, publicKey, confirmationSecret, privateKey: pair.privateKey, subject };
}

async function proof(value: Awaited<ReturnType<typeof grant>>, secret = value.confirmationSecret,
  nonce?: string) {
  return signedEnrollment(value, now, `${origin}/v1/enrollments/complete`, {
    nonce,
    body: JSON.stringify({ version: 1, stateId: value.stateId,
      installationId: value.installationId, confirmationSecret: secret }),
  });
}

function edge(request: Request) {
  const headers = new Headers(request.headers);
  headers.set('CF-Connecting-IP', '192.0.2.10');
  return new Request(request, { headers, });
}

describe('confirmed enrollment and current profiles', () => {
  it('binds browser OAuth to backend confirmation and completes through actual Node service dispatch', async () => {
    const value = await grant('1554889314549243910');
    const app = { ...bindings, DISCORD_CLIENT_ID: '1554924079486607490', DISCORD_CLIENT_SECRET: 'fixture-client-secret' };
    const startUrl = new URL(`${origin}/v1/discord/start`);
    startUrl.search = new URLSearchParams({ installationId: value.installationId, keyId: value.keyId,
      publicKey: btoa(String.fromCharCode(...value.publicKey)),
      confirmationHash: await confirmationHash(value.confirmationSecret) }).toString();
    const started = await discordOAuth(new Request(startUrl), app, now, fetch, async () => 'allowed', true);
    expect(started.status).toBe(303);
    const confirmationUrl = started.headers.get('location')!;
    const cookie = started.headers.get('set-cookie')!.split(';')[0];
    const stateId = new URL(confirmationUrl).searchParams.get('state')!;
    expect((await discordOAuth(new Request(confirmationUrl, { method: 'POST',
      headers: { Cookie: cookie, Origin: origin } }), app, now, fetch, async () => 'allowed', true)).status).toBe(303);
    const client = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/oauth2/token')) return Response.json({ access_token: 'fixture-access-token',
        token_type: 'Bearer', scope: 'identify guilds.members.read' });
      if (url.endsWith('/users/@me')) return Response.json({ id: value.subject });
      return Response.json({ user: { id: value.subject } });
    });
    expect((await discordOAuth(new Request(`${origin}/v1/discord/callback?state=${stateId}&code=fixture`,
      { headers: { Cookie: cookie } }), app, now, client, async () => 'allowed', true)).status).toBe(200);
    expect(await db.prepare('SELECT confirmation_hash FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ confirmation_hash: await confirmationHash(value.confirmationSecret) });
    const time = vi.spyOn(Date, 'now').mockReturnValue(now);
    const provider = vi.spyOn(globalThis, 'fetch').mockImplementation(client);
    try {
      const response = await service.fetch(edge(await proof({ ...value, stateId })), app);
      expect(response.status).toBe(200);
      expect(await response.json()).toMatchObject({ installationId: value.installationId, keyId: value.keyId });
      const missing = new URL(startUrl);
      missing.searchParams.delete('confirmationHash');
      expect((await service.fetch(edge(new Request(missing)), app)).status).toBe(400);
    } finally { time.mockRestore(); provider.mockRestore(); }
  });
  it('rejects a signed proof without the backend-held secret and preserves the grant', async () => {
    const value = await grant();
    await expect(completeEnrollmentProof(await proof(value, 'a'.repeat(64)), db, policy))
      .rejects.toThrow('invalid_enrollment_proof');
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(value.stateId).first()).toEqual({ consumed_at: null });
  });

  it('reconciles concurrent same-key completion and rejects subsequent retired-key retries', async () => {
    const value = await grant();
    const results = await Promise.all([proof(value), proof(value)]);
    const enrolled = await Promise.all(results.map((request) => completeEnrollmentProof(request, db, policy)));
    expect(enrolled[0]).toEqual(enrolled[1]);
    expect((await db.prepare('SELECT 1 FROM installations WHERE installation_id = ?')
      .bind(value.installationId).all()).results).toHaveLength(1);
    await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = ?")
      .bind(now, value.keyId).run();
    await expect(completeEnrollmentProof(await proof(value), db, policy)).rejects.toThrow('enrollment_conflict');
  });

  it('returns a private-link-free generated profile and reconciles lost-response retries', async () => {
    const value = await grant('1554889314549243988');
    const client = vi.fn(async () => Response.json({ user: { id: value.subject } }));
    const response = await contributorEnrollment(edge(await proof(value)), bindings, now, client);
    expect(response.status).toBe(200);
    const profile = await response.json() as { contributorId: string; displayName: string };
    expect(profile.displayName.split(' ')).toHaveLength(3);
    expect(Object.keys(profile).sort()).toEqual([...contract.profileRequiredFields].sort());
    expect(profile).toMatchObject({ installationId: value.installationId, keyId: value.keyId,
      keyState: 'active', policyVersion: 'fixture-policy-1', protocolVersion: 1 });
    expect(JSON.stringify(profile)).not.toContain(value.subject);
    expect(JSON.stringify(profile)).not.toContain(value.confirmationSecret);
    expect((await contributorEnrollment(edge(await proof(value)), bindings, now, client)).status).toBe(200);
    expect((await service.fetch(edge(await proof(value)), { ...bindings, APP_ENV: 'production' })).status).toBe(503);
  });

  it('fails closed before enrollment when membership or trusted edge admission fails', async () => {
    for (const state of ['ineligible', 'unavailable']) {
      const value = await grant(`15548893145492439${state === 'ineligible' ? '70' : '71'}`);
      const client = vi.fn(async () => Response.json({ code: state === 'ineligible' ? 10007 : 10004 }, { status: 404 }));
      const response = await contributorEnrollment(edge(await proof(value)),
        { ...bindings, DISCORD_BOT_TOKEN: `token-${state}` }, now, client);
      expect(response.status).toBe(state === 'ineligible' ? 403 : 503);
      expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
        .bind(value.stateId).first()).toEqual({ consumed_at: null });
    }
    const value = await grant();
    const client = vi.fn();
    expect((await contributorEnrollment(await proof(value), bindings, now, client)).status).toBe(503);
    expect(client).not.toHaveBeenCalled();
  });

  it('allows repeated unique changes, releases old names and reflects the latest profile on retry', async () => {
    const value = await grant('1554889314549243960');
    const registered = await completeEnrollmentProof(await proof(value), db, { ...policy, displayName: 'First Current Name' });
    const identity = { ...registered, displayName: 'First Current Name', nonce: 'initial-nonce',
      expiresAt: now / 1000 + 120, operationId: 'initial-operation', contentDigest: 'initial-digest' };
    await Promise.all(Array.from({ length: 5 }, (_, index) => renameContributor(db,
      { ...identity, nonce: `name-nonce-${index}`, operationId: `name-op-${index}`, contentDigest: `digest-${index}` },
      `Updated Current ${index}`, now)));
    await expect(renameContributor(db, identity, 'Sixth Current Name', now)).resolves.toBe('changed');
    expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ? AND nonce = ?')
      .bind(value.keyId, identity.nonce).first()).not.toBeNull();
    expect(await renameContributor(db, { ...identity, nonce: 'name-nonce-0', operationId: 'name-op-0',
      contentDigest: 'digest-0' }, 'Updated Current 0', now)).toBe('existing');
    const current = await contributorProfile(db, registered.contributorId, value.keyId, 'fixture-policy-1', now);
    expect(current.profileRevision).toBeGreaterThanOrEqual(now);
    const same = { ...identity, nonce: 'same-name', operationId: 'same-name', contentDigest: 'same-name' };
    await renameContributor(db, same, current.displayName, now);
    expect(await db.prepare('SELECT COUNT(*) AS count FROM profile_name_changes WHERE contributor_id = ?')
      .bind(registered.contributorId).first()).toEqual({ count: 6 });
    await expect(renameContributor(db, { ...identity, nonce: 'before-hour', operationId: 'before-hour',
      contentDigest: 'before-hour' }, 'Before Hour Name', now + 3_599_999)).resolves.toBe('changed');
    await renameContributor(db, identity, 'Next Hour Name', now + 3_600_000);
    const other = await grant('1554889314549243961');
    await completeEnrollmentProof(await proof(other), db, { ...policy, displayName: 'First Current Name' });
    await expect(renameContributor(db, { ...identity, nonce: 'taken-name', operationId: 'taken-name',
      contentDigest: 'taken-name' }, 'First Current Name', now + 3_600_000)).rejects.toThrow('display_name_taken');
    await expect(renameContributor(db, { ...identity, nonce: 'case-name', operationId: 'case-name',
      contentDigest: 'case-name' }, 'first current name', now + 3_600_000)).rejects.toThrow('display_name_taken');
    await pruneProfileNames(db, now + 3_600_000, 1);
    expect(await db.prepare('SELECT COUNT(*) AS count FROM profile_name_changes WHERE contributor_id = ?')
      .bind(registered.contributorId).first()).toEqual({ count: 6 });
  });

  it('does not revert a newer name when another request commits before the retry transaction', async () => {
    const value = await grant('1554889314549243941');
    const registered = await completeEnrollmentProof(await proof(value), db,
      { ...policy, displayName: 'Initial Race Name' });
    const identity = { ...registered, displayName: 'Initial Race Name', nonce: 'race-nonce',
      expiresAt: now / 1000 + 120, operationId: 'race-operation', contentDigest: 'race-digest' };
    const racing = new Proxy(db, { get(target, property) {
      if (property === 'batch') return async (statements: Parameters<typeof db.batch>[0]) => {
        await renameContributor(db, { ...identity, nonce: 'winning-nonce' }, 'First Race Name', now);
        await renameContributor(db, { ...identity, nonce: 'newer-nonce', operationId: 'newer-operation',
          contentDigest: 'newer-digest' }, 'Latest Race Name', now + 1);
        return target.batch(statements);
      };
      const member = Reflect.get(target, property);
      return typeof member === 'function' ? member.bind(target) : member;
    } });
    expect(await renameContributor(racing, identity, 'First Race Name', now)).toBe('existing');
    expect((await contributorProfile(db, registered.contributorId, value.keyId, 'fixture-policy-1', now)).displayName)
      .toBe('Latest Race Name');
  });

  it('enrolls another installation regardless of historical counters', async () => {
    const subject = '1554889314549243940';
    const first = await grant(subject);
    const registered = await completeEnrollmentProof(await proof(first), db,
      { ...policy, displayName: 'Stable Current Name' });
    const next = await grant(subject);
    await expect(completeEnrollmentProof(await proof(next), db, policy)).resolves.toMatchObject({ contributorId: registered.contributorId });
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(next.stateId).first()).toEqual({ consumed_at: now });
    expect(await db.prepare('SELECT 1 FROM installation_keys WHERE key_id = ?')
      .bind(next.keyId).first()).not.toBeNull();
  });

  it('reconnects the same active installation without another contributor or key', async () => {
    const value = await grant('1554889314549243930');
    const registered = await completeEnrollmentProof(await proof(value), db,
      { ...policy, displayName: 'Reconnect Current Name' });
    const next = await grant(value.subject);
    await db.prepare('UPDATE enrollment_states SET installation_id = ?, key_id = ?, public_key = ? WHERE state_id = ?')
      .bind(value.installationId, value.keyId, value.publicKey, next.stateId).run();
    const reconnect = { ...next, installationId: value.installationId, keyId: value.keyId, privateKey: value.privateKey };
    await expect(completeEnrollmentProof(await proof(reconnect), db, policy)).rejects.toThrow('enrollment_conflict');
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(next.stateId).first()).toEqual({ consumed_at: null });
    expect(await completeEnrollmentProof(await proof(reconnect, reconnect.confirmationSecret,
      'DQIDBAUGBwgJCgsMDQ4PEA'), db, policy)).toEqual(registered);
    expect(await db.prepare('SELECT 1 AS present FROM request_nonces WHERE key_id = ? AND nonce = ?')
      .bind(value.keyId, 'DQIDBAUGBwgJCgsMDQ4PEA').first()).toEqual({ present: 1 });
    expect((await db.prepare('SELECT 1 FROM installation_keys WHERE installation_id = ?')
      .bind(value.installationId).all()).results).toHaveLength(1);
  });

  it('requires explicit recovery instead of replacing an existing installation key', async () => {
    const value = await grant('1554889314549243920');
    await completeEnrollmentProof(await proof(value), db, { ...policy, displayName: 'Recovery Current Name' });
    const next = await grant(value.subject);
    await db.prepare('UPDATE enrollment_states SET installation_id = ? WHERE state_id = ?')
      .bind(value.installationId, next.stateId).run();
    await expect(completeEnrollmentProof(await proof({ ...next, installationId: value.installationId }), db, policy))
      .rejects.toThrow('recovery_required');
    expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?')
      .bind(value.keyId).first()).toEqual({ state: 'active' });
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(next.stateId).first()).toEqual({ consumed_at: null });
  });

  it('serves signed rename responses using current profile data and rejects replay body conflicts', async () => {
    const value = await grant('1554889314549243950');
    const registered = await completeEnrollmentProof(await proof(value), db, { ...policy, displayName: 'Route Initial Name' });
    const options = { method: 'PATCH' as const, operationId: 'rename-route', nonce: 'BQIDBAUGBwgJCgsMDQ4PEA',
      body: JSON.stringify({ displayName: 'Route Updated Name' }) };
    const request = () => signedEnrollment(value, now, `${origin}/v1/identity`, options);
    const first = await contributorIdentity(await request(), db, 'community.example', now / 1000,
      async () => 'eligible', 'fixture-policy-1');
    expect(first.status).toBe(200);
    expect(await first.json()).toMatchObject({ contributorId: registered.contributorId, displayName: 'Route Updated Name' });
    expect((await contributorIdentity(await request(), db, 'community.example', now / 1000,
      async () => 'eligible', 'fixture-policy-1')).status).toBe(200);
    const conflict = await signedEnrollment(value, now, `${origin}/v1/identity`, { ...options,
      body: JSON.stringify({ displayName: 'Route Conflict Name' }), nonce: 'CQIDBAUGBwgJCgsMDQ4PEA' });
    expect((await contributorIdentity(conflict, db, 'community.example', now / 1000,
      async () => 'eligible', 'fixture-policy-1')).status).toBe(409);
    await db.prepare("UPDATE contributors SET display_name = 'Changed Elsewhere Name', display_name_key = 'changed elsewhere name', profile_updated_at = ? WHERE contributor_id = ?")
      .bind(now + 1, registered.contributorId).run();
    const retry = await contributorIdentity(await request(), db, 'community.example', now / 1000,
      async () => 'eligible', 'fixture-policy-1');
    expect(await retry.json()).toMatchObject({ displayName: 'Changed Elsewhere Name', profileRevision: now + 1 });
  });
});