import type { SqlDatabase } from '../src/runtime/contracts';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { discordOAuth as broker, pruneDiscordOAuth } from '../src/integrations/discord/oauth';
import service from '../src/index';
import { config, confirm, db, discordOAuth, initializeOAuthDb, json, key, now, origin,
  provider, request, start, userId } from './helpers/discord-oauth';

beforeAll(initializeOAuthDb);

describe('Discord OAuth broker', () => {
  it.each(['missing', 'unavailable', 'failure'] as const)(
    'rejects %s admission before any SQLite or Discord work', async (mode) => {
      const prepare = vi.fn(() => { throw new Error('SQLite must not be called'); });
      const client = provider('member');
      const admit = mode === 'missing' ? undefined : vi.fn(async () => {
        if (mode === 'failure') throw new Error('sensitive admission detail');
        return mode;
      });
      const response = await broker(new Request(`${origin}/v1/discord/start`),
        { ...config, CATALOG_DB: { prepare } as unknown as SqlDatabase }, now, client, admit);
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: { code: 'service_unavailable' } });
      expect(prepare).not.toHaveBeenCalled();
      expect(client).not.toHaveBeenCalled();
    },
  );
  it('preserves existing schema and requires explicit same-browser confirmation', async () => {
    expect(await db.prepare("SELECT display_name FROM contributors WHERE contributor_id = 'pre-oauth'")
      .first()).toMatchObject({ display_name: 'Preserved' });
    const flow = await start();
    expect(flow.confirmationUrl).toBe(`${origin}/v1/discord/confirm?state=${flow.stateId}`);
    expect((await discordOAuth(request(flow.confirmationUrl, 'wrong=token'), config, now))
      .status).toBe(400);
    const page = await discordOAuth(request(flow.confirmationUrl, flow.cookie), config, now);
    expect(page.status).toBe(200);
    expect(page.headers.get('referrer-policy')).toBe('same-origin');
    const markup = await page.text();
    const text = markup.replaceAll('<wbr>', '');
    expect(text).toContain('Continue with Discord');
    expect(text).toContain(flow.keyId);
    expect(text).toContain(flow.installationId);
    expect((await discordOAuth(request(flow.confirmationUrl, flow.cookie, 'POST',
      { Origin: 'https://attacker.example' }), config, now)).status).toBe(403);
    const redirect = await confirm(flow);
    expect(redirect.status).toBe(303);
    expect(redirect.headers.get('referrer-policy')).toBe('no-referrer');
    const discord = new URL(redirect.headers.get('location')!);
    expect(discord.origin).toBe('https://discord.com');
    expect(discord.searchParams.get('redirect_uri')).toBe(`${origin}/v1/discord/callback`);
    expect(discord.searchParams.get('scope')).toBe('identify guilds.members.read');
    expect(discord.searchParams.get('state')).toBe(flow.stateId);
    expect((await confirm(flow)).status).toBe(400);
  });

  it('verifies identity and membership before issuing one key-bound handoff', async () => {
    const flow = await start();
    await confirm(flow);
    const callback = `${origin}/v1/discord/callback?state=${flow.stateId}&code=synthetic-code`;
    const client = provider('member');
    for (const issuer of ['iss=https%3A%2F%2Fother.example', 'iss=https%3A%2F%2Fdiscord.com&iss=https%3A%2F%2Fdiscord.com']) {
      expect((await discordOAuth(request(`${callback}&${issuer}`, flow.cookie), config, now, client)).status)
        .toBe(400);
    }
    expect((await discordOAuth(request(callback, 'wrong=token'), config, now, client)).status)
      .toBe(400);
    expect(client).not.toHaveBeenCalled();
    const [first, second] = await Promise.all([
      discordOAuth(request(`${callback}&iss=https%3A%2F%2Fdiscord.com`, flow.cookie), config, now, client),
      discordOAuth(request(callback, flow.cookie), config, now, client),
    ]);
    expect([first.status, second.status].sort()).toEqual([200, 400]);
    expect(client).toHaveBeenCalledTimes(3);
    expect((await first.clone().text()) + (await second.clone().text()))
      .not.toContain('synthetic-access-token');
    const grant = await db.prepare(`SELECT installation_id, key_id, public_key,
      provider_subject, consumed_at, expires_at FROM enrollment_states WHERE state_id = ?`)
      .bind(flow.stateId).first<{ installation_id: string; key_id: string;
        public_key: ArrayBuffer; provider_subject: string; consumed_at: number | null;
        expires_at: number }>();
    expect(grant).toMatchObject({ installation_id: flow.installationId, key_id: flow.keyId,
      provider_subject: userId, consumed_at: null, expires_at: now + 600_000 });
    expect(new Uint8Array(grant!.public_key)).toEqual(key);
  });

  it.each(['nonmember', 'outage', 'wrong-user', 'bad-scope'] as const)(
    'fails closed on %s without creating a handoff', async (result) => {
      const flow = await start();
      await confirm(flow);
      const response = await discordOAuth(request(`${origin}/v1/discord/callback?state=${flow.stateId}&code=code`,
        flow.cookie), config, now, provider(result));
      expect(response.status).toBe(result === 'nonmember' ? 403 : 503);
      expect(await response.text()).not.toContain('synthetic-secret');
      expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
        .bind(flow.stateId).first()).toBeNull();
      expect((await discordOAuth(request(`${origin}/v1/discord/callback?state=${flow.stateId}&code=code`,
        flow.cookie), config, now, provider('member'))).status).toBe(400);
    },
  );

  it('rejects expiration, denied consent, substituted keys and unconfigured Node service requests', async () => {
    const flow = await start();
    await confirm(flow);
    expect((await discordOAuth(request(`${origin}/v1/discord/callback?state=${flow.stateId}&error=access_denied`,
      flow.cookie), config, now, provider('member'))).status).toBe(403);
    expect((await confirm(flow, now + 600_001)).status).toBe(400);
    expect((await service.fetch(new Request(`${origin}/v1/discord/callback`), config)).status).toBe(503);
    const digest = `sha256:${'0'.repeat(64)}`;
    const substituted = new URL(`${origin}/v1/discord/start`);
    substituted.search = new URLSearchParams({ installationId: crypto.randomUUID(),
      keyId: digest, publicKey: btoa(String.fromCharCode(...key)) }).toString();
    expect((await discordOAuth(new Request(substituted), config, now)).status).toBe(400);
    expect(await pruneDiscordOAuth(db, now + 600_001, 1000)).toBeGreaterThan(0);
  });

  it('consumes only valid same-browser denied consent and cannot later authorize it', async () => {
    const flow = await start();
    await confirm(flow);
    const client = provider('member');
    const denial = `${origin}/v1/discord/callback?state=${flow.stateId}&error=access_denied`;
    expect((await discordOAuth(request(denial, 'wrong=token'), config, now, client)).status).toBe(400);
    expect(await db.prepare('SELECT consumed_at FROM discord_oauth_states WHERE state_id = ?')
      .bind(flow.stateId).first()).toEqual({ consumed_at: null });
    expect((await discordOAuth(request(`${denial}&code=code`, flow.cookie), config, now, client))
      .status).toBe(400);
    expect((await discordOAuth(request(denial, flow.cookie), config, now, client)).status).toBe(403);
    expect((await discordOAuth(request(denial, flow.cookie), config, now, client)).status).toBe(400);
    expect((await discordOAuth(request(`${origin}/v1/discord/callback?state=${flow.stateId}&code=code`,
      flow.cookie), config, now, client)).status).toBe(400);
    expect(client).not.toHaveBeenCalled();
    expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
      .bind(flow.stateId).first()).toBeNull();
  });

  it('binds state to the configured origin without allowing arbitrary client redirects', async () => {
    const other = 'https://community-test.example';
    const flow = await start(now, other);
    expect(flow.confirmationUrl).toBe(`${other}/v1/discord/confirm?state=${flow.stateId}`);
    const moved = flow.confirmationUrl.replace(other, origin);
    expect((await discordOAuth(request(moved, flow.cookie), config, now)).status).toBe(400);
    expect((await discordOAuth(request(flow.confirmationUrl, flow.cookie), {
      ...config, PUBLIC_ORIGIN: other,
    }, now)).status).toBe(200);
    expect((await confirm(flow, now, other)).status).toBe(303);
    const client = provider('member');
    expect((await discordOAuth(request(`${origin}/v1/discord/callback?state=${flow.stateId}&code=code`,
      flow.cookie), config, now, client)).status).toBe(400);
    expect(client).not.toHaveBeenCalled();
  });

  it.each(['rate_limit', 'timeout'] as const)('does not issue a grant on Discord %s',
    async (failure) => {
      const flow = await start();
      await confirm(flow);
      const fetchProvider = vi.fn(async () => {
        if (failure === 'timeout') throw new DOMException('timed out', 'TimeoutError');
        return json({ message: 'rate limited' }, 429);
      }) as typeof fetch;
      const response = await discordOAuth(request(
        `${origin}/v1/discord/callback?state=${flow.stateId}&code=secret-code`,
        flow.cookie), config, now, fetchProvider);
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: { code: 'discord_unavailable' } });
      expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
        .bind(flow.stateId).first()).toBeNull();
    },
  );
});
