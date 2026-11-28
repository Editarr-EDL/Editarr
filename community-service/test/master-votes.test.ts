import { beforeAll, describe, expect, it, vi } from 'vitest';
import { tunnelActor } from '../src/runtime/environment.mjs';
import service from '../src/index';
import { contributorVotes } from '../src/http/votes';
import { recomputeVersion } from '../src/master/recompute';
import { contributor, nonce, service as serviceOrigin, support, type Contributor } from './helpers/submissions';
import { download, eligible, post, reply, submitWork } from './helpers/master';
import { signedRead } from './helpers/key-lifecycle';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

const limits = { requestBytes: 8192, maxQueries: 64, votesPerHour: 1000 };
const handle = async (request: Request, votesPerHour = limits.votesPerHour) =>
  reply(await contributorVotes(request, db, 'community.example', now, eligible, { policy, limits: { ...limits, votesPerHour } }));
const vote = async (who: Contributor, filterId: string, value: unknown, operationId = `vote-${crypto.randomUUID()}`) =>
  handle(await post(who, `/v1/filters/${filterId}/votes`, value, operationId));
const filter = async (filterId: string) => db.prepare('SELECT upvotes, downvotes, confidence FROM master_filters WHERE filter_id = ?')
  .bind(filterId).first<{ upvotes: number; downvotes: number; confidence: number }>();
let alice: Contributor, bob: Contributor, carol: Contributor, versionId: string, filterId: string, receiptId: string;

beforeAll(async () => {
  await setupVersionCatalog([]);
  [alice, bob, carol] = await Promise.all([contributor(), contributor(), contributor()]);
  versionId = (await submitWork(alice, '300')).versionId;
  const downloaded = await download(bob, versionId);
  filterId = downloaded.snapshot.filters[0].filterId;
  receiptId = downloaded.receiptId;
});

describe('POST /v1/filters/{filterId}/votes', () => {
  it('records one receipt-bound vote per contributor and filter, recomputes and replays retries', async () => {
    const operationId = `vote-${crypto.randomUUID()}`;
    const up = await vote(bob, filterId, { version: 1, vote: 'up', receiptId }, operationId);
    expect(up.status).toBe(200);
    expect(up.body).toEqual({ version: 1, state: 'completed', result: { version: 1, operationId, filterId, versionId, vote: 'up', revision: 2 } });
    expect(await filter(filterId)).toMatchObject({ upvotes: 1, downvotes: 0 });
    expect((await vote(bob, filterId, { version: 1, vote: 'up', receiptId }, operationId)).body).toEqual(up.body);
    expect(await vote(bob, filterId, { version: 1, vote: 'down', receiptId }, operationId)).toMatchObject({ status: 409,
      body: { error: { code: 'operation_conflict' } } });
    const down = await vote(bob, filterId, { version: 1, vote: 'down', receiptId });
    expect(down.body.result).toMatchObject({ vote: 'down', revision: 3 });
    expect(await filter(filterId)).toMatchObject({ upvotes: 0, downvotes: 1 });
    expect((await db.prepare('SELECT count(*) AS n FROM filter_votes WHERE filter_id = ?').bind(filterId).first())).toEqual({ n: 1 });
    const own = await handle(await signedRead(bob, `/v1/versions/${versionId}/votes`, now, nonce()));
    expect(own.body).toEqual({ version: 1, versionId, votes: [{ filterId, vote: 'down' }] });
    const none = await vote(bob, filterId, { version: 1, vote: 'none', receiptId });
    expect(none.body.result).toMatchObject({ vote: 'none', revision: 4 });
    expect(await filter(filterId)).toMatchObject({ upvotes: 0, downvotes: 0 });
    expect((await handle(await signedRead(bob, `/v1/versions/${versionId}/votes`, now, nonce()))).body.votes).toEqual([]);
    // Append-only history: the replayed retry adds nothing; a removal records the withdrawn vote.
    const events = await db.prepare(`SELECT vote, kind, receipt_id AS receiptId, created_at AS createdAt FROM vote_events
      WHERE filter_id = ? AND contributor_id = ? ORDER BY event_id`).bind(filterId, bob.contributorId).all();
    expect(events.results).toEqual([{ vote: 'up', kind: 'cast', receiptId, createdAt: now },
      { vote: 'down', kind: 'changed', receiptId, createdAt: now }, { vote: 'down', kind: 'removed', receiptId, createdAt: now }]);
    await vote(bob, filterId, { version: 1, vote: 'none', receiptId });
    expect(await db.prepare('SELECT count(*) AS n FROM vote_events WHERE contributor_id = ?').bind(bob.contributorId).first()).toEqual({ n: 3 });
  });

  it('refuses self votes, missing or foreign receipts and filters outside the receipt snapshot', async () => {
    const own = await download(alice, versionId);
    expect(await vote(alice, filterId, { version: 1, vote: 'up', receiptId: own.receiptId })).toMatchObject({ status: 403,
      body: { error: { code: 'vote_ineligible', reason: 'self_vote' } } });
    for (const receipt of ['0'.repeat(64), receiptId])
      expect(await vote(carol, filterId, { version: 1, vote: 'up', receiptId: receipt })).toMatchObject({ status: 403,
        body: { error: { code: 'vote_ineligible', reason: 'receipt_required' } } });
    await submitWork(carol, '300', { entries: [support('c1', 600_000, 605_000), support('c2', 2_000_000, 2_005_000)] });
    const later = (await download(alice, versionId)).snapshot.filters.find(item => item.startMs === 2_000_000)!.filterId;
    expect(await vote(bob, later, { version: 1, vote: 'up', receiptId })).toMatchObject({ status: 403,
      body: { error: { code: 'vote_ineligible', reason: 'receipt_required' } } });
    expect(await vote(bob, 'f_missing', { version: 1, vote: 'up', receiptId })).toMatchObject({ status: 404,
      body: { error: { code: 'filter_not_found' } } });
  });

  it.each([{ version: 1, vote: 'maybe', receiptId: 'a'.repeat(64) }, { version: 1, vote: 'up', receiptId: 'short' },
    { version: 1, vote: 'up', receiptId: 'a'.repeat(64), extra: 1 }, '{'])('rejects %j as invalid_vote', async value => {
    expect(await vote(bob, filterId, value)).toMatchObject({ status: 400, body: { error: { code: 'invalid_vote' } } });
  });

  it('accepts a retired filter ID listed in the receipt snapshot and resolves its merge chain for self votes', async () => {
    const retired = 'f_retired_example', older = 'f_retired_older';
    await db.batch([
      db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, state, merged_into, quarantined)
        VALUES (?, ?, 90, 'retired', ?, 0)`).bind(retired, versionId, filterId),
      db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, state, merged_into, quarantined)
        VALUES (?, ?, 91, 'retired', ?, 0)`).bind(older, versionId, retired),
    ]);
    await recomputeVersion(db, versionId, { service: serviceOrigin, policy, now });
    const erin = await contributor();
    const { snapshot, receiptId: erinReceipt } = await download(erin, versionId);
    expect(snapshot.filters.find(item => item.filterId === filterId)!.mergedIds).toEqual([retired, older].sort());
    const before = (await filter(filterId))!.upvotes;
    expect((await vote(erin, older, { version: 1, vote: 'up', receiptId: erinReceipt })).body.result).toMatchObject({ filterId: older, vote: 'up' });
    expect((await filter(filterId))!.upvotes).toBe(before + 1);
    const own = await download(alice, versionId);
    expect(await vote(alice, older, { version: 1, vote: 'up', receiptId: own.receiptId })).toMatchObject({ status: 403,
      body: { error: { code: 'vote_ineligible', reason: 'self_vote' } } });
  });

  it('replaces a vote left on a filter ID that was merged into the voted filter', async () => {
    const frank = await contributor();
    const { receiptId: frankReceipt } = await download(frank, versionId);
    const merged = 'f_merged_voted';
    const counts = async () => (await db.prepare(`SELECT upvotes, downvotes FROM master_filters WHERE filter_id = ?`)
      .bind(filterId).first<{ upvotes: number; downvotes: number }>())!;
    // Frank voted on `merged` before the service merged it into `filterId`.
    await db.batch([
      db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, state, merged_into, quarantined)
        VALUES (?, ?, 92, 'retired', ?, 0)`).bind(merged, versionId, filterId),
      db.prepare(`INSERT INTO filter_votes (filter_id, contributor_id, vote, receipt_id, excluded, updated_at)
        VALUES (?, ?, 'up', ?, 0, ?)`).bind(merged, frank.contributorId, frankReceipt, now),
    ]);
    const before = await counts();
    await recomputeVersion(db, versionId, { service: serviceOrigin, policy, now });
    expect((await counts()).upvotes).toBe(before.upvotes + 1);
    expect((await vote(frank, filterId, { version: 1, vote: 'none', receiptId: frankReceipt })).status).toBe(200);
    expect(await counts()).toEqual(before);
    const rows = () => db.prepare('SELECT filter_id AS filterId, vote FROM filter_votes WHERE contributor_id = ?')
      .bind(frank.contributorId).all();
    expect((await rows()).results).toEqual([]);
    await db.prepare(`INSERT INTO filter_votes (filter_id, contributor_id, vote, receipt_id, excluded, updated_at)
      VALUES (?, ?, 'up', ?, 0, ?)`).bind(merged, frank.contributorId, frankReceipt, now).run();
    expect((await vote(frank, filterId, { version: 1, vote: 'down', receiptId: frankReceipt })).status).toBe(200);
    expect(await counts()).toEqual({ upvotes: before.upvotes, downvotes: before.downvotes + 1 });
    expect((await rows()).results).toEqual([{ filterId, vote: 'down' }]);
  });

  it('limits votes per contributor per hour and requires a vote- operation key', async () => {
    const hal = await contributor();
    const { receiptId: halReceipt } = await download(hal, versionId);
    const cast = async (value: string, votesPerHour: number, operationId = `vote-${crypto.randomUUID()}`) =>
      handle(await post(hal, `/v1/filters/${filterId}/votes`, { version: 1, vote: value, receiptId: halReceipt }, operationId), votesPerHour);
    expect((await cast('up', 2)).status).toBe(200);
    expect((await cast('down', 2)).status).toBe(200);
    const limited = await cast('none', 2);
    expect([limited.status, limited.body]).toEqual([429, { error: { code: 'rate_limited' } }]);
    expect(Number(limited.headers.get('retry-after'))).toBeGreaterThan(0);
    expect(await cast('up', 1000, crypto.randomUUID())).toMatchObject({ status: 400, body: { error: { code: 'invalid_vote' } } });
  });

  it('keeps a moderator exclusion when the voter changes or removes the vote', async () => {
    const dave = await contributor();
    const { receiptId: daveReceipt } = await download(dave, versionId);
    const upvotes = (await filter(filterId))!.upvotes;
    await vote(dave, filterId, { version: 1, vote: 'down', receiptId: daveReceipt });
    await db.prepare('UPDATE filter_votes SET excluded = 1 WHERE filter_id = ? AND contributor_id = ?').bind(filterId, dave.contributorId).run();
    await vote(dave, filterId, { version: 1, vote: 'up', receiptId: daveReceipt });
    expect(await filter(filterId)).toMatchObject({ upvotes });
    await vote(dave, filterId, { version: 1, vote: 'none', receiptId: daveReceipt });
    expect(await db.prepare('SELECT excluded FROM filter_votes WHERE filter_id = ? AND contributor_id = ?')
      .bind(filterId, dave.contributorId).first()).toEqual({ excluded: 1 });
  });

  it('is routed behind admission and fails closed without VOTE_LIMITS', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ user: { id: bob.discordId } })));
    const env = { APP_ENV: 'local', CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
      TRUSTED_ACTOR_KEY: tunnelActor, DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token',
      MASTER_POLICY: JSON.stringify(policy), VOTE_LIMITS: JSON.stringify(limits) };
    const edge = async () => {
      const signed = await signedRead(bob, `/v1/versions/${versionId}/votes`, Math.floor(Date.now() / 1000), nonce());
      const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
      return new Request(signed, { headers });
    };
    try {
      expect((await service.fetch(await edge(), env)).status).toBe(200);
      expect((await service.fetch(await edge(), { ...env, VOTE_LIMITS: undefined })).status).toBe(503);
    } finally { vi.unstubAllGlobals(); }
  });
});
