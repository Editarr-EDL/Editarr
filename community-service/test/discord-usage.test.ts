import { beforeAll, describe, expect, it } from 'vitest';
import { applyBaseline } from './helpers/schema';
import { serviceUsage, pruneOAuthUsage, recordOAuthUsage } from '../src/integrations/discord/usage';
import { db, initializeOAuthDb, now } from './helpers/discord-oauth';

beforeAll(async () => {
  await initializeOAuthDb();
});

describe('OAuth observed usage', () => {
  it('preserves identity and labels missing observations unknown', async () => {
    expect(await db.prepare('SELECT display_name FROM contributors WHERE contributor_id = ?')
      .bind('pre-oauth').first()).toEqual({ display_name: 'Preserved' });
    const status = await serviceUsage(db, now);
    expect(status.resources.worker_requests.observed).toBeNull();
    expect(status.restoreProvenance).toBe('unknown');
    expect(status.providerVerified).toBe(false);
    expect(status).not.toHaveProperty('paused');
  });

  it('records concurrent attempts without imposing a daily ceiling', async () => {
    await Promise.all(Array.from({ length: 10 }, () => recordOAuthUsage(db, 'worker_requests', 1, now)));
    await recordOAuthUsage(db, 'worker_requests', 1_000_000, now);
    const status = await serviceUsage(db, now);
    expect(status.resources.worker_requests.observed).toBe(1_000_010);
    expect(status.resources.worker_requests).not.toHaveProperty('pauseAt');
    expect((await serviceUsage(db, now + 86_400_000)).resources.worker_requests.observed).toBeNull();
  });

  it('combines anonymous activity and contributor observations', async () => {
    await db.prepare(`INSERT INTO usage_observations
      (contributor_id, resource, period, outcome, occurrences, units, updated_at)
      VALUES ('pre-oauth', 'd1_writes', '2026-09-30', 'denied', 1, 79999, ?)`)
      .bind(now).run();
    const accounting = await recordOAuthUsage(db, 'd1_writes', 0, now);
    expect(accounting.meta.rows_written).toBe(1);
    const status = await serviceUsage(db, now);
    expect(status.resources.d1_writes.observed).toBe(80_000);
    expect(Object.keys(status.resources).sort()).toEqual(['d1_writes', 'worker_requests']);
    expect(status).not.toHaveProperty('paused');
  });

  it('prunes only bounded old anonymous counters, preserving existing grants and identity', async () => {
    await recordOAuthUsage(db, 'worker_requests', 1, Date.parse('2026-08-01T00:00:00Z'));
    await recordOAuthUsage(db, 'd1_writes', 1, Date.parse('2026-08-01T00:00:00Z'));
    expect(await pruneOAuthUsage(db, now, 1)).toBe(1);
    expect(await pruneOAuthUsage(db, now, 1)).toBe(1);
    expect(await pruneOAuthUsage(db, now, 1)).toBe(0);
    expect((await serviceUsage(db, now)).resources.worker_requests.observed).toBe(1_000_010);
  });
});