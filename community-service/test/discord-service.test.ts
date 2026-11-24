import type { SqlDatabase } from '../src/runtime/contracts';
import { applyBaseline } from './helpers/schema';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import service from '../src/index';
import { discordService } from '../src/integrations/discord/service';
import { serviceUsage } from '../src/integrations/discord/usage';
import { config, db, initializeOAuthDb, now, origin, provider, start } from './helpers/discord-oauth';

beforeAll(async () => {
  await initializeOAuthDb();
});

function fixture(url: string) {
  const source = { limit: vi.fn(async () => ({ success: true })) };
  const shared = { limit: vi.fn(async () => ({ success: true })) };
  return { request: new Request(url, { headers: { 'CF-Connecting-IP': '192.0.2.1' }, }),
    env: { ...config}, source, shared };
}

describe('Node service OAuth service wiring', () => {
  it('admits trusted starts without a quota policy but still refuses untrusted ingress', async () => {
    const flow = await start();
    const input = fixture(`${flow.startUrl}&confirmationHash=${'a'.repeat(64)}`);
    expect((await discordService(input.request, input.env, now)).status).toBe(303);
    expect((await discordService(input.request, { ...input.env, TRUSTED_ACTOR_KEY: undefined }, now)).status)
      .toBe(503);
    expect((await serviceUsage(db, now)).resources.worker_requests.observed).toBe(1);
  });

  it('completes browser OAuth through real Node service dispatch with synthetic provider and edge metadata', async () => {
    const flow = await start();
    const input = fixture(`${flow.startUrl}&confirmationHash=${'a'.repeat(64)}`);
    const response = await service.fetch(input.request, input.env);
    expect(response.status).toBe(303);
    const cookie = response.headers.get('set-cookie')!.split(';')[0];
    const confirmationUrl = response.headers.get('location')!;
    const stateId = new URL(confirmationUrl).searchParams.get('state')!;
    const confirmRequest = new Request(confirmationUrl, { method: 'POST',
      headers: { Cookie: cookie, Origin: origin, 'CF-Connecting-IP': '192.0.2.1' }, });
    expect((await service.fetch(confirmRequest, input.env)).status).toBe(303);
    const client = provider('member');
    const callback = new Request(`${origin}/v1/discord/callback?state=${stateId}&code=synthetic`, {
      headers: { Cookie: cookie, 'CF-Connecting-IP': '192.0.2.1' },
    });
    expect((await discordService(callback, input.env, Date.now(), client)).status).toBe(200);
    expect(client).toHaveBeenCalledTimes(3);
    expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
      .bind(stateId).first()).toEqual({ consumed_at: null });
    const status = await serviceUsage(db, Date.now());
    expect(status.resources.worker_requests.observed).toBeGreaterThanOrEqual(3);
    expect(status.resources.d1_writes.observed).toBeGreaterThanOrEqual(7);
  });

  it('denies untrusted requests before SQLite or provider work', async () => {
    const input = fixture(`${origin}/v1/discord/start`);
    const prepare = vi.fn(() => { throw new Error('unexpected SQLite'); });
    const client = provider('member');
    expect((await discordService(input.request, { ...input.env,
      TRUSTED_ACTOR_KEY: () => null, CATALOG_DB: { prepare } as unknown as SqlDatabase }, now, client)).status).toBe(503);
    expect(prepare).not.toHaveBeenCalled();
    expect(client).not.toHaveBeenCalled();
  });

  it('does not deny high activity but still handles database outages without calling Discord', async () => {
    const flow = await start();
    const input = fixture(`${flow.startUrl}&confirmationHash=${'a'.repeat(64)}`);
    await db.prepare(`INSERT INTO oauth_usage_counters (resource, period, units, updated_at)
      VALUES ('worker_requests', '2026-09-30', 80000, ?)
      ON CONFLICT(resource, period) DO UPDATE SET units = 80000, updated_at = excluded.updated_at`)
      .bind(now).run();
    const client = provider('member');
    expect((await discordService(input.request, input.env, now, client)).status).toBe(303);
    expect(client).not.toHaveBeenCalled();
    expect((await serviceUsage(db, now)).resources.worker_requests.observed).toBe(80001);
    expect((await discordService(input.request, { ...input.env, CATALOG_DB: {
      prepare: () => { throw new Error('sensitive database detail'); },
    } as unknown as SqlDatabase }, now, client)).status).toBe(503);
  });

  it('keeps non-local routing and scheduled work disabled', async () => {
    const input = fixture(`${origin}/v1/discord/start`);
    expect((await service.fetch(input.request, { ...input.env, APP_ENV: 'production' })).status).toBe(503);
    expect(input.source.limit).not.toHaveBeenCalled();
    const prepare = vi.fn();
    await service.scheduled({}, {
      APP_ENV: 'production', CATALOG_DB: { prepare } as unknown as SqlDatabase,
    });
    expect(prepare).not.toHaveBeenCalled();
  });
});