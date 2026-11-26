import { env } from './helpers/environment';
import { applyBaseline } from './helpers/schema';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { createBotMembership } from '../src/integrations/discord/membership';
import { authenticateContributor } from '../src/security/contributor-auth';
import { signedEnrollment } from './helpers/enrollment-signatures';
import { contributorIdentity } from '../src/http/identity';
import service from '../src/index';

const db = env.CATALOG_DB;
const now = 1_700_000_030;
const origin = 'https://community.example';
const emptyDigest = 'sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:';

beforeAll(async () => {
  await applyBaseline(db);
});

async function register() {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const contributorId = crypto.randomUUID();
  const installationId = crypto.randomUUID();
  const keyId = crypto.randomUUID();
  const discordId = String(1554889314549243900n + BigInt(Math.floor(Math.random() * 1_000_000_000)));
  const displayName = `My name ${contributorId}`;
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES (?, ?, ?, ?, 1)`).bind(contributorId, discordId, displayName, displayName.toLowerCase()),
    db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
      VALUES (?, ?, 1)`).bind(installationId, contributorId),
    db.prepare(`INSERT INTO installation_keys
      (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(keyId, installationId, publicKey),
  ]);
  return { contributorId, keyId, privateKey: pair.privateKey, discordId, installationId, displayName };
}

async function signedGet(identity: Awaited<ReturnType<typeof register>>,
  url = `${origin}/v1/identity`, timestamp = now) {
  const target = new URL(url);
  const signatureInput = `editarr=("@method" "@scheme" "@authority" "@path" "@query" ` +
    `"content-digest");created=${timestamp - 10};expires=${timestamp + 50};` +
    `nonce="AQIDBAUGBwgJCgsMDQ4PEA";keyid="${identity.keyId}";alg="ed25519";` +
    'tag="editarr-community-request-v1"';
  const base = `"@method": GET\n"@scheme": https\n"@authority": ${target.host}\n` +
    `"@path": ${target.pathname}\n"@query": ${target.search || '?'}\n` +
    `"content-digest": ${emptyDigest}\n` +
    `"@signature-params": ${signatureInput.slice('editarr='.length)}`;
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', identity.privateKey,
    new TextEncoder().encode(base)));
  return new Request(url, { headers: { 'signature-input': signatureInput,
    signature: `editarr=:${btoa(String.fromCharCode(...signature))}:`,
    'content-digest': emptyDigest } });
}

describe('own-identity contributor HTTP route', () => {
  it('wires bot eligibility into Node service identity and never exposes provider linkage or secrets', async () => {
    const identity = await register();
    const timestamp = Math.floor(Date.now() / 1000);
    const client = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ user: { id: identity.discordId } }));
    const bindings = { ...env, PUBLIC_ORIGIN: origin, POLICY_VERSION: 'fixture-policy-1', DISCORD_GUILD_ID: '1554889314549243986',
      DISCORD_BOT_TOKEN: 'synthetic-route-bot-secret' };
    try {
      const response = await service.fetch(await signedGet(identity, `${origin}/v1/identity`, timestamp), bindings);
      expect(response.status).toBe(200);
      const text = await response.text();
      expect(text).not.toContain(identity.discordId);
      expect(text).not.toContain(bindings.DISCORD_BOT_TOKEN);
      expect(client).toHaveBeenCalledOnce();
      expect((await service.fetch(await signedGet(identity, `${origin}/v1/identity`, timestamp), bindings)).status).toBe(200);
      expect(client).toHaveBeenCalledOnce();
      expect((await service.fetch(await signedGet(identity, `${origin}/v1/identity`, timestamp),
        { ...bindings, APP_ENV: 'production' })).status).toBe(503);
    } finally { client.mockRestore(); }
  });

  it('blocks valid signed reads and mutations before protected work when membership is absent or unavailable', async () => {
    for (const outcome of ['ineligible', 'unavailable'] as const) {
      const identity = await register();
      const client = vi.fn(async () => Response.json({ code: outcome === 'ineligible' ? 10007 : 10004 }, { status: 404 }));
      const check = createBotMembership({ ...env, DISCORD_GUILD_ID: '1554889314549243986',
        DISCORD_BOT_TOKEN: `synthetic-${crypto.randomUUID()}` }, client);
      const expected = outcome === 'ineligible' ? 'membership_ineligible' : 'membership_unavailable';
      for (const pathname of ['/v1/identity', '/v1/catalog', '/v1/artifacts/example']) {
        await expect(authenticateContributor(await signedGet(identity, `${origin}${pathname}`), db,
          'community.example', now, check)).rejects.toMatchObject({ code: expected });
      }
      const mutation = await signedEnrollment({ ...identity, stateId: 'a'.repeat(64) }, now * 1000,
        `${origin}/v1/contributions`, { body: '{"version":1}' });
      await expect(authenticateContributor(mutation, db, 'community.example', now, check))
        .rejects.toMatchObject({ code: expected });
      const response = await contributorIdentity(await signedGet(identity), db, 'community.example', now, check);
      expect(response.status).toBe(outcome === 'ineligible' ? 403 : 503);
      expect(await db.prepare('SELECT 1 FROM operations WHERE contributor_id = ?')
        .bind(identity.contributorId).first()).toBeNull();
      expect(client).toHaveBeenCalledOnce();
    }
  });
  it('denies by default until membership is wired and never exposes own data anonymously', async () => {
    const identity = await register();
    expect((await service.fetch(await signedGet(identity), { ...env,
      PUBLIC_ORIGIN: origin })).status).toBe(503);
    expect((await contributorIdentity(await signedGet(identity), db, 'community.example', now))
      .status).toBe(503);
    expect((await contributorIdentity(new Request(`${origin}/v1/identity`), db,
      'community.example', now, async () => 'eligible')).status).toBe(401);
  });

  it('verifies current key and membership before charging and returning only own status', async () => {
    const identity = await register();
    const deniedMembership = await contributorIdentity(await signedGet(identity), db,
      'community.example', now, async () => 'ineligible');
    expect(deniedMembership.status).toBe(403);
    const response = await contributorIdentity(await signedGet(identity), db,
      'community.example', now, async () => 'eligible');
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ contributorId: identity.contributorId,
      displayName: identity.displayName });
    const tampered = await signedGet(identity);
    tampered.headers.set('content-digest', 'sha-256=:wrong:');
    expect((await contributorIdentity(tampered, db, 'community.example', now,
      async () => 'eligible')).status).toBe(401);
    await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = ?")
      .bind(now, identity.keyId).run();
    expect((await contributorIdentity(await signedGet(identity), db,
      'community.example', now, async () => 'eligible')).status).toBe(401);
  });

  it('ignores legacy caps but rejects key retirement, suspension and wrong authority', async () => {
    const identity = await register();
    try {
      expect((await contributorIdentity(await signedGet(identity), db,
        'community.example', now, async () => 'eligible')).status).toBe(200);
      const exhausted = await contributorIdentity(await signedGet(identity), db,
        'community.example', now, async () => 'eligible');
      expect(exhausted.status).toBe(200);
      expect(await exhausted.json()).not.toHaveProperty('quotas');
      expect((await contributorIdentity(await signedGet(identity), db,
        'other.example', now, async () => 'eligible')).status).toBe(401);
      await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = ?")
        .bind(identity.contributorId).run();
      expect((await contributorIdentity(await signedGet(identity), db,
        'community.example', now, async () => 'eligible')).status).toBe(401);
      await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = ?")
        .bind(identity.contributorId).run();
      const another = await register();
      const retired = await contributorIdentity(await signedGet(another), db,
        'community.example', now, async () => {
          await db.prepare(`UPDATE installation_keys SET state = 'retired', retired_at = ?
            WHERE key_id = ?`).bind(now, another.keyId).run();
          return 'eligible';
        });
      expect(retired.status).toBe(401);
    } finally {
    }
  });
});