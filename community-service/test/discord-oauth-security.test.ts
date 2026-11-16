import type { SqlDatabase } from '../src/runtime/contracts';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { completeEnrollmentProof } from '../src/identity/enrollment-proof';
import { createOAuthAdmission } from '../src/integrations/discord/admission';
import { discordOAuth as broker } from '../src/integrations/discord/oauth';
import { pruneExpiredAuthenticationState } from '../src/operations/repository';
import service from '../src/index';
import { config, confirm, db, discordOAuth, initializeOAuthDb, json, now, origin,
  provider, request, start } from './helpers/discord-oauth';
import { signedEnrollment } from './helpers/enrollment-signatures';

beforeAll(initializeOAuthDb);

describe('OAuth broker security boundaries', () => {
  it('admits browser start only after trusted ingress and service approval', async () => {
    const flow = await start();
    const policy = vi.fn(async (): Promise<'allowed'> => 'allowed');
    const response = await broker(new Request(flow.startUrl), config, now, provider('member'),
      createOAuthAdmission({ trustedActorKey: () => 'trusted-fixture', policy }));
    expect(response.status).toBe(303);
    expect(policy).toHaveBeenCalledOnce();
    expect(new URL(response.headers.get('location')!).origin).toBe(origin);
  });

  it.each(['token', 'user', 'member'] as const)('never follows credential-bearing %s redirects',
    async (stage) => {
      const flow = await start();
      await confirm(flow);
      const healthy = provider('member');
      const client = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        expect(init?.redirect).toBe('error');
        const url = String(input);
        expect(new URL(url).origin).toBe('https://discord.com');
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        const current = url.endsWith('/oauth2/token') ? 'token' :
          url.endsWith('/users/@me') ? 'user' : 'member';
        if (current === stage) return new Response(null, {
          status: 302, headers: { Location: 'https://attacker.example/collect' },
        });
        return healthy(input);
      }) as typeof fetch;
      const response = await discordOAuth(request(
        `${origin}/v1/discord/callback?state=${flow.stateId}&code=sensitive-code`, flow.cookie),
      config, now, client);
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: { code: 'discord_unavailable' } });
      expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
        .bind(flow.stateId).first()).toBeNull();
      expect((await discordOAuth(request(
        `${origin}/v1/discord/callback?state=${flow.stateId}&code=sensitive-code`, flow.cookie),
      config, now, provider('member'))).status).toBe(400);
    },
  );
  it('keeps configured non-local OAuth and enrollment requests unavailable before SQLite', async () => {
    const prepare = vi.fn(() => { throw new Error('SQLite must not be called'); });
    for (const path of ['/v1/discord/start', '/v1/discord/confirm', '/v1/discord/callback',
      '/v1/enrollments/complete']) {
      for (const method of ['GET', 'POST']) {
        const response = await service.fetch(new Request(`${origin}${path}`, { method }), {
          ...config, APP_ENV: 'production', CATALOG_DB: { prepare } as unknown as SqlDatabase });
        expect(response.status).toBe(503);
        expect(await response.json()).toEqual({ error: { code: 'service_unavailable' } });
      }
    }
    expect(prepare).not.toHaveBeenCalled();
  });
  it('connects arbitrary self-hosted browser origins through the fixed callback and key proof', async () => {
    const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const rawKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    const flow = await start(now, origin, rawKey);
    const opened = await discordOAuth(new Request(flow.startUrl, {
      headers: { Origin: 'https://editarr.self-hosted.example:8443' } }), config, now);
    expect(opened.status).toBe(303);
    const redirect = await confirm(flow);
    expect(new URL(redirect.headers.get('location')!).searchParams.get('redirect_uri'))
      .toBe(`${origin}/v1/discord/callback`);
    const callback = await discordOAuth(request(
      `${origin}/v1/discord/callback?state=${flow.stateId}&code=sensitive-code`, flow.cookie),
    config, now, provider('member'));
    expect(callback.status).toBe(200);
    expect(callback.headers.get('set-cookie')).toContain('Max-Age=0');
    expect(callback.headers.get('cache-control')).toBe('no-store');
    expect(callback.headers.get('referrer-policy')).toBe('no-referrer');
    expect(await callback.text()).not.toMatch(/sensitive-code|synthetic-access-token|synthetic-secret/);
    const grant = { ...flow, privateKey: pair.privateKey };
    const attacker = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const policy = { authority: new URL(origin).host, now, maxLifetimeSeconds: 120,
      maxClockSkewSeconds: 5, displayName: 'Fixture' };
    const target = `${origin}/v1/enrollments/complete`;
    await expect(completeEnrollmentProof(await signedEnrollment(grant, now, target,
      { privateKey: attacker.privateKey }), db, policy)).rejects.toThrow('invalid_enrollment_proof');
    await expect(completeEnrollmentProof(await signedEnrollment(grant, now, target), db, policy))
      .resolves.toMatchObject({ installationId: flow.installationId, keyId: flow.keyId });
    await expect(completeEnrollmentProof(await signedEnrollment(grant, now, target), db, policy))
      .rejects.toThrow();
    await pruneExpiredAuthenticationState(db, now / 1000 + 1, 1000);
    expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ?')
      .bind(flow.keyId).first()).not.toBeNull();
    await pruneExpiredAuthenticationState(db, now / 1000 + 56, 1000);
    expect(await db.prepare('SELECT 1 FROM request_nonces WHERE key_id = ?')
      .bind(flow.keyId).first()).toBeNull();
    expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
      .bind(flow.stateId).first()).not.toBeNull();
    await pruneExpiredAuthenticationState(db, now / 1000 + 601, 1000);
    expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
      .bind(flow.stateId).first()).toBeNull();
  });

  it.each(['null', '[]', '"text"', '{}', '{"installationId":1}', '{"returnUrl":"https://attacker.example"}'])(
    'rejects malformed start body %s without touching SQLite', async (body) => {
      const prepare = vi.fn(() => { throw new Error('SQLite must not be called'); });
      const response = await discordOAuth(new Request(`${origin}/v1/discord/start`, {
        method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body,
      }), { ...config, CATALOG_DB: { prepare } as unknown as SqlDatabase }, now);
      expect(response.status).toBe(400);
      expect(prepare).not.toHaveBeenCalled();
    },
  );

  it('cancels oversized streamed start bodies without trusting Content-Length', async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new Uint8Array(1024));
      controller.enqueue(new Uint8Array(1025));
    }, cancel });
    const options = {
      method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body,
    };
    const response = await discordOAuth(new Request(`${origin}/v1/discord/start`, { ...options, duplex: 'half' } as RequestInit), config, now);
    expect(response.status).toBe(413);
    expect(cancel).toHaveBeenCalled();
  });

  it.each(['token', 'user', 'member'] as const)('fails closed on malformed or oversized %s replies',
    async (stage) => {
      for (const failure of ['null', 'numeric_id', 'invalid_json', 'oversized', 'rate_limit', 'timeout']) {
        const flow = await start();
        await confirm(flow);
        const healthy = provider('member');
        const client = vi.fn(async (input: RequestInfo | URL) => {
          const url = String(input);
          const current = url.endsWith('/oauth2/token') ? 'token' : url.endsWith('/users/@me') ? 'user' : 'member';
          if (stage !== current) return healthy(input);
          if (failure === 'timeout') throw new DOMException('sensitive provider detail', 'TimeoutError');
          if (failure === 'rate_limit') return json({ detail: 'sensitive provider detail' }, 429);
          if (failure === 'invalid_json') return new Response('sensitive provider detail');
          if (failure === 'oversized') return new Response(' '.repeat(16_385));
          return json(failure === 'null' ? null : stage === 'token' ? { access_token: 123 } :
            stage === 'user' ? { id: 1554889314549243999 } : { user: { id: 1554889314549243999 } });
        }) as typeof fetch;
        const response = await discordOAuth(request(
          `${origin}/v1/discord/callback?state=${flow.stateId}&code=sensitive-code`, flow.cookie), config, now, client);
        expect(response.status, `${stage}/${failure}`).toBe(503);
        expect(await response.json()).toEqual({ error: { code: 'discord_unavailable' } });
        expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
          .bind(flow.stateId).first()).toBeNull();
      }
    },
  );

  it.each(['installation_id', 'key_id', 'public_key', 'expires_at'])(
    'rejects %s changed between state read and callback claim', async (column) => {
      const flow = await start();
      await confirm(flow);
      const replacement = column === 'public_key' ? new Uint8Array(32) :
        column === 'expires_at' ? now : crypto.randomUUID();
      const racingDb = { prepare: (sql: string) => {
        if (!sql.includes('UPDATE discord_oauth_states SET consumed_at')) return db.prepare(sql);
        return { bind: (...values: unknown[]) => ({ run: async () => {
          await db.prepare(`UPDATE discord_oauth_states SET ${column} = ? WHERE state_id = ?`)
            .bind(replacement, flow.stateId).run();
          return db.prepare(sql).bind(...values).run();
        } }) };
      } } as unknown as SqlDatabase;
      const client = provider('member');
      expect((await discordOAuth(request(
        `${origin}/v1/discord/callback?state=${flow.stateId}&code=code`, flow.cookie),
      { ...config, CATALOG_DB: racingDb }, now, client)).status).toBe(400);
      expect(client).not.toHaveBeenCalled();
      expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?')
        .bind(flow.stateId).first()).toBeNull();
    },
  );

  it('rejects duplicate browser cookies and extra callback parameters without claiming state', async () => {
    const flow = await start();
    await confirm(flow);
    const client = provider('member');
    const callback = `${origin}/v1/discord/callback?state=${flow.stateId}&code=code`;
    for (const [url, cookie] of [[callback, `${flow.cookie}; ${flow.cookie}`],
      [callback, `${flow.cookie}; __Host-community_oauth=malformed`],
      [callback, `${flow.cookie}; __Host-community_oauth=`],
      [`${callback}&state=${flow.stateId}`, flow.cookie], [`${callback}&returnUrl=https://attacker.example`, flow.cookie]]) {
      expect((await discordOAuth(request(url, cookie), config, now, client)).status).toBe(400);
    }
    expect(client).not.toHaveBeenCalled();
    expect(await db.prepare('SELECT consumed_at FROM discord_oauth_states WHERE state_id = ?')
      .bind(flow.stateId).first()).toEqual({ consumed_at: null });
  });
});