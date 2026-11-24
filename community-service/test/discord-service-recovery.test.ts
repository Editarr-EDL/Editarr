import type { SqlDatabase, SqlStatement } from '../src/runtime/contracts';
import { applyBaseline } from './helpers/schema';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import service from '../src/index';
import { discordService } from '../src/integrations/discord/service';
import { observeOAuthDatabase, serviceUsage } from '../src/integrations/discord/usage';
import { config, confirm, db, initializeOAuthDb, now, origin, provider, start } from './helpers/discord-oauth';

beforeAll(async () => {
  await initializeOAuthDb();
});

function bindings(database: SqlDatabase = db) {
  return { ...config, CATALOG_DB: database};
}

function edge(url: string, cookie?: string, method = 'GET') {
  return new Request(url, { method,
    headers: { 'CF-Connecting-IP': '192.0.2.10', ...(cookie ? { Cookie: cookie } : {}),
      ...(method === 'POST' ? { Origin: origin } : {}) } });
}

function faultyDatabase(mode: 'accounting' | 'missing_metadata'): SqlDatabase {
  function prepared(query: string, target: SqlStatement, values: unknown[] = []): SqlStatement {
    return new Proxy(target, { get(statement, property) {
      if (property === 'bind') return (...input: unknown[]) => prepared(query, statement.bind(...input), input);
      if (property === 'run') return async () => {
        if (mode === 'accounting' && query.includes('INSERT INTO oauth_usage_counters') &&
            values[0] === 'd1_writes') throw new Error('sensitive telemetry detail');
        const result = await statement.run();
        return mode === 'missing_metadata' && !query.includes('oauth_usage_counters') ?
          { ...result, meta: { ...result.meta, rows_written: undefined } } : result;
      };
      const value = Reflect.get(statement, property);
      return typeof value === 'function' ? value.bind(statement) : value;
    } });
  }
  return new Proxy(db, { get(target, property) {
    if (property === 'prepare') return (query: string) => prepared(query, target.prepare(query));
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
}

describe('OAuth accounting failure and cleanup recovery', () => {
  it.each(['confirm', 'callback'] as const)('preserves committed %s outcomes during telemetry failures', async (stage) => {
    for (const mode of ['accounting', 'missing_metadata'] as const) {
      const flow = await start();
      if (stage === 'callback') await confirm(flow);
      const input = stage === 'confirm' ? edge(flow.confirmationUrl, flow.cookie, 'POST') :
        edge(`${origin}/v1/discord/callback?state=${flow.stateId}&code=synthetic`, flow.cookie);
      const client = provider('member');
      const response = await discordService(input, bindings(faultyDatabase(mode)), now, client);
      expect(response.status).toBe(stage === 'confirm' ? 303 : 200);
      expect(response.headers.get('Community-Usage-Status')).toBe('unavailable');
      expect(await response.clone().text()).not.toContain('sensitive telemetry detail');
      if (stage === 'confirm') {
        expect(new URL(response.headers.get('location')!).origin).toBe('https://discord.com');
        expect(await db.prepare('SELECT confirmed FROM discord_oauth_states WHERE state_id = ?')
          .bind(flow.stateId).first()).toEqual({ confirmed: 1 });
      } else {
        expect(response.headers.get('set-cookie')).toContain('Max-Age=0');
        expect(await db.prepare('SELECT consumed_at FROM enrollment_states WHERE state_id = ?')
          .bind(flow.stateId).first()).toEqual({ consumed_at: null });
      }
    }
  });

  it('accounts exact known writes, including the accounting row itself', async () => {
    const before = (await serviceUsage(db, now)).resources.d1_writes.observed ?? 0;
    const observer = observeOAuthDatabase(db);
    expect((await discordService(edge(`${origin}/v1/discord/start`), bindings(observer.db), now)).status).toBe(400);
    const after = (await serviceUsage(db, now)).resources.d1_writes.observed!;
    expect(observer.rowsWritten()).toBe(2);
    expect(after - before).toBe(observer.rowsWritten());
  });

  it('accepts observations from overlapping later requests without a false outage', async () => {
    const response = await discordService(edge(`${origin}/v1/discord/start`), bindings(), now + 1);
    expect(response.status).toBe(400);
    expect((await serviceUsage(db, now)).resources.worker_requests.observedAt).toBe(now + 1);
    expect((await discordService(edge(`${origin}/v1/discord/start`), bindings(), now)).status).toBe(400);
  });

  it('runs later scheduled prunes and records usage after an earlier prune fails', async () => {
    const queries: string[] = [];
    const failing = new Proxy(db, { get(target, property) {
      if (property === 'prepare') return (query: string) => {
        queries.push(query);
        if (queries.length === 1) throw new Error('first_prune_failed');
        return target.prepare(query);
      };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    await expect(service.scheduled({}, bindings(failing))).rejects.toThrow('first_prune_failed');
    expect(queries.length).toBeGreaterThan(5);
    expect(queries.some(query => query.includes('INSERT INTO oauth_usage_counters'))).toBe(true);
  });

  it('performs bounded scheduled cleanup, measures its writes and preserves live state', async () => {
    const at = Date.now();
    const expired = await start(at - 700_000);
    const live = await start(at);
    const before = (await serviceUsage(db, at)).resources.d1_writes.observed ?? 0;
    const observer = observeOAuthDatabase(db);
    await service.scheduled({}, bindings(observer.db));
    expect(await db.prepare('SELECT state_id FROM discord_oauth_states WHERE state_id = ?')
      .bind(expired.stateId).first()).toBeNull();
    expect(await db.prepare('SELECT state_id FROM discord_oauth_states WHERE state_id = ?')
      .bind(live.stateId).first()).toEqual({ state_id: live.stateId });
    const after = (await serviceUsage(db, Date.now())).resources.d1_writes.observed!;
    expect(observer.rowsWritten()).toBeGreaterThan(1);
    expect(after - before).toBe(observer.rowsWritten());
    expect(await db.prepare('SELECT display_name FROM contributors WHERE contributor_id = ?')
      .bind('pre-oauth').first()).toEqual({ display_name: 'Preserved' });
  });
});