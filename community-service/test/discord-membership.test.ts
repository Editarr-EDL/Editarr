import type { SqlDatabase } from '../src/runtime/contracts';
import { applyBaseline } from './helpers/schema';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { createBotMembership, pruneBotMembership } from '../src/integrations/discord/membership';
import { config, db, initializeOAuthDb, now, userId } from './helpers/discord-oauth';

const at = Math.floor(now / 1000);
beforeAll(async () => {
  await initializeOAuthDb();
  await db.prepare('UPDATE contributors SET discord_user_id = ? WHERE contributor_id = ?')
    .bind(userId, 'pre-oauth').run();
});

function fixture() {
  const client = vi.fn(async () => Response.json({ user: { id: userId }, pending: true, roles: [] }));
  const env = { ...config, DISCORD_BOT_TOKEN: `synthetic-${crypto.randomUUID()}` };
  return { client, env, check: createBotMembership(env, client) };
}

describe('persisted bot membership eligibility', () => {
  it('does not start a provider call after a lease expires during a cache read', async () => {
    const { env, client } = fixture();
    const clock = vi.fn().mockReturnValueOnce(at).mockReturnValue(at + 11);
    expect(await createBotMembership(env, client, clock)('pre-oauth', at)).toBe('unavailable');
    expect(client).not.toHaveBeenCalled();
  });

  it('does not publish eligibility when completion precedes the last cache read', async () => {
    const { env, client } = fixture();
    const clock = vi.fn().mockReturnValueOnce(at).mockReturnValueOnce(at + 4).mockReturnValue(at + 2);
    expect(await createBotMembership(env, client, clock)('pre-oauth', at)).toBe('unavailable');
    expect(client).toHaveBeenCalledTimes(1);
    expect(await db.prepare("SELECT 1 FROM discord_membership WHERE contributor_id = 'pre-oauth'").first()).toBeNull();
  });
  it('does not return a cache that expires while awaiting SQLite', async () => {
    const { env, client, check } = fixture();
    expect(await check('pre-oauth', at)).toBe('eligible');
    client.mockRejectedValue(new Error('outage'));
    expect(await createBotMembership(env, client, () => at + 901)('pre-oauth', at + 899))
      .toBe('unavailable');
    expect(client).toHaveBeenCalledTimes(2);
  });
  it('bounds cleanup and preserves fresh evidence, in-flight leases and cooldowns', async () => {
    await db.prepare(`INSERT INTO discord_membership (contributor_id, authority, state, checked_at)
      VALUES ('pre-oauth', 'cleanup-old', 'eligible', ?), ('pre-oauth', 'cleanup-fresh', 'eligible', ?)`)
      .bind(at - 900, at).run();
    await db.prepare(`INSERT INTO discord_membership_refresh (authority, lease_id, lease_until, retry_at)
      VALUES ('cleanup-lease', 'lease', ?, 0), ('cleanup-cooldown', NULL, 0, ?),
        ('cleanup-idle', NULL, 0, 0)`)
      .bind(at + 10, at + 120).run();
    await pruneBotMembership(db, at, 1);
    expect(await db.prepare("SELECT 1 FROM discord_membership WHERE authority = 'cleanup-old'").first()).toBeNull();
    expect(await db.prepare("SELECT 1 FROM discord_membership WHERE authority = 'cleanup-fresh'").first()).not.toBeNull();
    expect(await db.prepare("SELECT 1 FROM discord_membership_refresh WHERE authority = 'cleanup-lease'").first()).not.toBeNull();
    expect(await db.prepare("SELECT 1 FROM discord_membership_refresh WHERE authority = 'cleanup-cooldown'").first()).not.toBeNull();
    expect(await db.prepare("SELECT 1 FROM discord_membership_refresh WHERE authority = 'cleanup-idle'").first()).toBeNull();
  });
  it('expires exactly at 15 minutes, denies stale outage and recovers without retained tokens', async () => {
    const { check, client } = fixture();
    expect(await check('pre-oauth', at)).toBe('eligible');
    expect(await check('pre-oauth', at + 899)).toBe('eligible');
    expect(client).toHaveBeenCalledTimes(1);
    client.mockRejectedValueOnce(new Error('sensitive provider detail'));
    expect(await check('pre-oauth', at + 900)).toBe('unavailable');
    expect(await check('pre-oauth', at + 904)).toBe('unavailable');
    expect(client).toHaveBeenCalledTimes(2);
    expect(await check('pre-oauth', at + 905)).toBe('eligible');
  });

  it('persists negative results and automatically restores access after verified rejoin', async () => {
    const { check, client } = fixture();
    client.mockResolvedValueOnce(Response.json({ code: 10007 }, { status: 404 }));
    expect(await check('pre-oauth', at)).toBe('ineligible');
    expect(await check('pre-oauth', at + 899)).toBe('ineligible');
    expect(await check('pre-oauth', at + 900)).toBe('eligible');
    expect(client).toHaveBeenCalledTimes(2);
  });

  it('shares refresh leases across independent check instances and installations', async () => {
    const { env, client, check } = fixture();
    let complete!: (response: Response) => void;
    let entered!: () => void;
    const started = new Promise<void>((resolve) => { entered = resolve; });
    client.mockImplementationOnce(() => { entered(); return new Promise((resolve) => { complete = resolve; }); });
    const first = check('pre-oauth', at);
    await started;
    expect(await createBotMembership(env, client)('pre-oauth', at)).toBe('unavailable');
    complete(Response.json({ user: { id: userId } }));
    expect(await first).toBe('eligible');
    expect(await createBotMembership(env, client)('pre-oauth', at)).toBe('eligible');
    expect(client).toHaveBeenCalledOnce();
  });

  it('honors shared/global rate cooldown without extending expired membership', async () => {
    const { check, client } = fixture();
    client.mockResolvedValueOnce(Response.json({ retry_after: 120.1, global: true }, { status: 429 }));
    expect(await check('pre-oauth', at)).toBe('unavailable');
    expect(await check('pre-oauth', at + 120)).toBe('unavailable');
    expect(client).toHaveBeenCalledOnce();
    expect(await check('pre-oauth', at + 121)).toBe('eligible');
  });

  it('invalidates old positive results on token or guild changes and clock rollback', async () => {
    const { check, env, client } = fixture();
    expect(await check('pre-oauth', at)).toBe('eligible');
    expect(await check('pre-oauth', at - 1)).toBe('unavailable');
    client.mockRejectedValue(new Error('outage'));
    expect(await createBotMembership({ ...env, DISCORD_BOT_TOKEN: 'rotated-synthetic' }, client)
      ('pre-oauth', at)).toBe('unavailable');
    expect(await createBotMembership({ ...env, DISCORD_GUILD_ID: '1554889314549243987' }, client)
      ('pre-oauth', at)).toBe('unavailable');
    expect(await createBotMembership({ ...env, DISCORD_BOT_TOKEN: undefined }, client)
      ('pre-oauth', at)).toBe('unavailable');
  });

  it('never clears operator suspension, including suspension during a provider call', async () => {
    const { check, client } = fixture();
    client.mockImplementationOnce(async () => {
      await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'pre-oauth'").run();
      return Response.json({ user: { id: userId } });
    });
    expect(await check('pre-oauth', at)).toBe('ineligible');
    expect(await check('pre-oauth', at + 901)).toBe('ineligible');
    expect(client).toHaveBeenCalledOnce();
    await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'pre-oauth'").run();
  });

  it('denies missing contributors, malformed time and database outages without provider access', async () => {
    const { check, env, client } = fixture();
    expect(await check('unknown', at)).toBe('ineligible');
    expect(await check('pre-oauth', NaN)).toBe('unavailable');
    expect(await createBotMembership({ ...env, CATALOG_DB: {
      prepare: () => { throw new Error('sensitive db error'); },
    } as unknown as SqlDatabase }, client)('pre-oauth', at)).toBe('unavailable');
    expect(client).not.toHaveBeenCalled();
  });

  it('anchors cooldown to provider completion, not request start', async () => {
    const { env, client } = fixture();
    let time = at;
    const check = createBotMembership(env, client, () => time);
    client.mockImplementationOnce(async () => {
      time = at + 4;
      return Response.json({ retry_after: 120 }, { status: 429 });
    });
    expect(await check('pre-oauth', at)).toBe('unavailable');
    time = at + 120;
    expect(await check('pre-oauth', time)).toBe('unavailable');
    expect(client).toHaveBeenCalledOnce();
    time = at + 124;
    expect(await check('pre-oauth', time)).toBe('eligible');
  });

  it('does not publish a result after lease expiry even without another owner', async () => {
    const { env, client } = fixture();
    let time = at;
    client.mockImplementationOnce(async () => {
      time = at + 10;
      return Response.json({ user: { id: userId } });
    });
    const check = createBotMembership(env, client, () => time);
    expect(await check('pre-oauth', at)).toBe('unavailable');
    expect(await check('pre-oauth', time)).toBe('eligible');
    expect(client).toHaveBeenCalledTimes(2);
  });

  it('prevents an old lease owner from overwriting a newer negative result', async () => {
    const { env, client } = fixture();
    let time = at;
    let complete!: (response: Response) => void;
    let entered!: () => void;
    const started = new Promise<void>((resolve) => { entered = resolve; });
    client.mockImplementationOnce(() => { entered(); return new Promise((resolve) => { complete = resolve; }); });
    const first = createBotMembership(env, client, () => time)('pre-oauth', at);
    await started;
    time = at + 10;
    client.mockResolvedValueOnce(Response.json({ code: 10007 }, { status: 404 }));
    const newer = createBotMembership(env, client, () => time);
    expect(await newer('pre-oauth', time)).toBe('ineligible');
    complete(Response.json({ user: { id: userId } }));
    expect(await first).toBe('unavailable');
    expect(await newer('pre-oauth', time)).toBe('ineligible');
  });
});