import { tunnelActor } from '../src/runtime/environment.mjs';
import type { SqlStatement } from '../src/runtime/contracts';
import { beforeAll, expect, it, vi } from 'vitest';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import membershipMigration from '../migrations/0007_membership.sql?raw';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { readVote, setVote, reconcileVoteCounts } from '../src/contributions/votes';
import { contributorVote } from '../src/http/votes';
import { signedPost, signedRead, signingKey, type SigningKey } from './helpers/key-lifecycle';
import { fixtureKey } from './helpers/publication-signatures';
import { observeDownloadDatabase, recordVoteUsage } from '../src/budgets/download-usage';
import service from '../src/index';
import { rawRevision } from './helpers/catalog';
import contract from '../../tests/fixtures/community/vote-v1.json';

let revisionId: string;
let key: SigningKey, sibling: SigningKey, other: SigningKey;
beforeAll(async () => {
  await setupPublication();
  for (const migration of [imports, votes, moderation, membershipMigration]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  key = await signingKey(); sibling = await signingKey(); other = await signingKey();
  const item = await publicationCase('ai');
  const published = await publishArtifact(db, item.identity, item.input, limits, now);
  revisionId = published.result.revisionId;
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('voter', 'discord-voter', 'Voter', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('voter-install', 'voter', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('voter-key', 'voter-install', 'Ed25519', ?, 'active', 1, NULL)")
      .bind(new Uint8Array(32)),
    db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
      .bind('voter', revisionId, 'a'.repeat(64), published.result.artifactDigest, now),
    db.prepare('UPDATE installation_keys SET public_key = ? WHERE key_id = ?').bind(key.publicKey, 'voter-key'),
    db.prepare("INSERT INTO installations VALUES ('sibling-install', 'voter', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'sibling-install', 'Ed25519', ?, 'active', 1, NULL)").bind(sibling.keyId, sibling.publicKey),
    db.prepare("INSERT INTO contributors VALUES ('other', 'discord-other', 'Other', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('other-install', 'other', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'other-install', 'Ed25519', ?, 'active', 1, NULL)").bind(other.keyId, other.publicKey),
  ]);
  key = { ...key, keyId: 'voter-key' };
});

async function store(value: string) {
  await db.prepare(`INSERT INTO revision_votes VALUES ('voter', ?, ?, 'voter-key', ?)
    ON CONFLICT(contributor_id, revision_id) DO UPDATE SET value = excluded.value,
      key_id = excluded.key_id, updated_at = excluded.updated_at`).bind(revisionId, value, now).run();
}

it('keeps exact counters across every vote transition, including no-ops and neutral', async () => {
  for (const before of ['neutral', 'like', 'dislike']) {
    for (const after of ['neutral', 'like', 'dislike']) {
      await store(before); await store(after);
      expect(await db.prepare('SELECT imports, likes, dislikes FROM contribution_statistics WHERE revision_id = ?')
        .bind(revisionId).first()).toEqual({ imports: 1, likes: Number(after === 'like'), dislikes: Number(after === 'dislike') });
      expect(await db.prepare('SELECT count(*) AS total FROM revision_votes').first()).toEqual({ total: 1 });
    }
  }
});

it('rejects missing imports, self-votes, invalid states and changed vote identity', async () => {
  await expect(db.prepare("INSERT INTO revision_votes VALUES ('contributor-1', ?, 'like', 'key-1', ?)")
    .bind(revisionId, now).run()).rejects.toThrow('vote_ineligible');
  await expect(store('report')).rejects.toThrow();
  await expect(db.prepare("UPDATE revision_votes SET contributor_id = 'contributor-1'").run()).rejects.toThrow('vote_ineligible');
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'voter'").run();
  try { await expect(store('neutral')).rejects.toThrow('vote_ineligible'); }
  finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'voter'").run(); }
});

function identity(operationId = crypto.randomUUID(), vote = 'like') {
  return { contributorId: 'voter', keyId: 'voter-key', displayName: 'Voter', nonce: crypto.randomUUID(),
    operationId, contentDigest: `fixture-${vote}`, expiresAt: now + 60 };
}

it('retries concurrent identical operations once and never reapplies an old vote', async () => {
  const principal = identity();
  const results = await Promise.all(Array.from({ length: 5 }, () => setVote(db,
    { ...principal, nonce: crypto.randomUUID() }, revisionId, { version: 1, vote: 'like' }, now)));
  expect(results.every(result => result.vote === 'like')).toBe(true);
  await setVote(db, identity(undefined, 'dislike'), revisionId, { version: 1, vote: 'dislike' }, now);
  expect(await setVote(db, { ...principal, nonce: crypto.randomUUID() }, revisionId, { version: 1, vote: 'like' }, now)).toEqual(results[0]);
  expect(await readVote(db, identity(), revisionId, true, now)).toMatchObject({ vote: 'dislike', eligible: true });
  await expect(setVote(db, { ...principal, contentDigest: 'changed' }, revisionId,
    { version: 1, vote: 'neutral' }, now)).rejects.toThrow('operation_conflict');
});

it('concurrent independent operations leave totals equal to the single current row', async () => {
  await Promise.all(Array.from({ length: 12 }, (_, index) => {
    const vote = ['like', 'dislike', 'neutral'][index % 3];
    return setVote(db, identity(undefined, vote), revisionId, { version: 1, vote }, now);
  }));
  const current = await readVote(db, identity(), revisionId, true, now);
  expect(await readVote(db, identity(), revisionId, false, now)).toEqual({ version: 1, revisionId,
    likes: Number(current.vote === 'like'), dislikes: Number(current.vote === 'dislike') });
  await db.prepare('UPDATE contribution_statistics SET likes = 77, dislikes = 88 WHERE revision_id = ?').bind(revisionId).run();
  await reconcileVoteCounts(db, [revisionId, revisionId]);
  expect(await readVote(db, identity(), revisionId, false, now)).toMatchObject({
    likes: Number(current.vote === 'like'), dislikes: Number(current.vote === 'dislike') });
  expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(revisionId).first()).toEqual({ imports: 1 });
});

const path = () => `/v1/contributions/${revisionId}/vote`;
const handle = (request: Request, membership = async () => 'eligible' as const, requestBytes = 8192, maxQueries = 64) =>
  contributorVote(request, db, 'community.example', now, membership, { requestBytes, maxQueries });
async function request(vote = 'like', operationId = crypto.randomUUID(), signing = key, timestamp = now) {
  return signedPost(signing, path(), JSON.stringify({ version: 1, vote }), operationId, timestamp);
}

it('keeps private current votes isolated and public aggregates free of voter history', async () => {
  expect((await handle(await request('like'))).status).toBe(200);
  const own = await handle(await signedRead(key, path()));
  expect(own.headers.get('cache-control')).toBe('private, no-store');
  expect(await own.json()).toEqual({ version: 1, revisionId, vote: 'like', eligible: true });
  const foreign = await handle(await signedRead(other, path()));
  expect(foreign.headers.get('cache-control')).toBe('private, no-store');
  expect(await foreign.json()).toEqual({ version: 1, revisionId, vote: 'neutral', eligible: false });
  expect(await (await handle(await signedRead(other, path() + 's'))).json()).toEqual({ version: 1, revisionId, likes: 1, dislikes: 0 });
  expect((await handle(await request('like', undefined, other))).status).toBe(403);
  expect((await handle(await request('like', undefined, await fixtureKey()))).status).toBe(403);
});

it('uses one voter across sibling installations, rotation, and restored operation retries', async () => {
  const operationId = crypto.randomUUID();
  const result = await (await handle(await request('like', operationId))).json();
  expect((await handle(await request('dislike', undefined, sibling))).status).toBe(200);
  await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = 'voter-key'").bind(now).run();
  try {
    expect((await handle(await request('neutral'))).status).toBe(401);
    expect(await (await handle(await request('like', operationId, sibling))).json()).toEqual(result);
    expect(await (await handle(await signedRead(sibling, path()))).json()).toMatchObject({ vote: 'dislike' });
    expect(await db.prepare('SELECT count(*) AS total FROM revision_votes WHERE contributor_id = ?').bind('voter').first()).toEqual({ total: 1 });
  } finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'voter-key'").run(); }
});

it('denies access loss without removing existing votes, and permits verified return', async () => {
  for (const method of ['read', 'write']) {
    const fresh = async () => method === 'read' ? signedRead(key, path()) : request();
    expect((await handle(await fresh(), async () => 'ineligible' as never)).status).toBe(403);
    expect((await handle(await fresh(), async () => 'unavailable' as never)).status).toBe(503);
    await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'voter'").run();
    try { expect((await handle(await fresh())).status).toBe(401); }
    finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'voter'").run(); }
  }
  expect(await (await handle(await signedRead(key, path()))).json()).toMatchObject({ vote: 'dislike', eligible: true });
});

it('rolls back replayed nonces and prevents stale principals from committing after key retirement', async () => {
  const principal = identity();
  await setVote(db, principal, revisionId, { version: 1, vote: 'like' }, now);
  const replay = { ...identity(), nonce: principal.nonce };
  await expect(setVote(db, replay, revisionId, { version: 1, vote: 'neutral' }, now)).rejects.toThrow('request_nonces');
  expect(await db.prepare("SELECT result FROM operations WHERE kind = 'vote' AND operation_id = ?").bind(replay.operationId).first()).toBeNull();
  const retiring = new Proxy(db, { get(target, property) {
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      if (statements.length === 3) await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = 'voter-key'").bind(now).run();
      return target.batch(statements);
    };
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
  const late = identity();
  try {
    await expect(setVote(retiring, late, revisionId, { version: 1, vote: 'neutral' }, now)).rejects.toThrow('key_inactive');
    expect(await db.prepare("SELECT result FROM operations WHERE kind = 'vote' AND operation_id = ?").bind(late.operationId).first()).toBeNull();
  } finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'voter-key'").run(); }
  expect(await readVote(db, identity(), revisionId, true, now)).toMatchObject({ vote: 'like' });
});

it('rejects malformed, altered, oversized and unsupported requests and observes invalid votes', async () => {
  const malformed = await signedPost(key, path(), '{', crypto.randomUUID());
  const before = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'voter' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  expect((await handle(malformed)).status).toBe(400);
  expect((await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'voter' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>())!.used).toBe((before?.used ?? 0) + 1);
  expect((await handle(await request('report'))).status).toBe(400);
  expect((await handle(await signedPost(key, path(), JSON.stringify({ version: 1, vote: 'like', reason: 'accurate' }), crypto.randomUUID()))).status).toBe(400);
  const original = await request();
  expect((await handle(new Request(original.url, { method: 'POST', headers: original.headers, body: '{"version":1,"vote":"neutral"}' }))).status).toBe(401);
  expect((await handle(await request(), undefined, 10)).status).toBe(413);
  expect((await handle(await request(), undefined, 8192, 2)).status).toBe(413);
  expect((await handle(new Request(original.url, { method: 'DELETE' }))).status).toBe(405);
  expect((await handle(new Request(original.url))).status).toBe(401);
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
  try { expect((await handle(await request('like'))).status).toBe(200); }
  finally { await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run(); }
  expect(await readVote(db, identity(), revisionId, true, now)).toMatchObject({ vote: 'like' });
});

it('new revisions and derivatives start without votes and require their own import', async () => {
  for (const name of ['manual', 'zero'] as const) {
    const item = await publicationCase(name);
    const published = await publishArtifact(db, item.identity, item.input, limits, now);
    const next = published.result.revisionId;
    expect(await readVote(db, identity(), next, false, now)).toEqual({ version: 1, revisionId: next, likes: 0, dislikes: 0 });
    await expect(setVote(db, identity(), next, { version: 1, vote: 'like' }, now)).rejects.toThrow('vote_ineligible');
    await db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
      .bind('voter', next, crypto.randomUUID().replace(/-/g, '').repeat(2), published.result.artifactDigest, now).run();
    await setVote(db, identity(), next, { version: 1, vote: 'like' }, now);
    expect(await readVote(db, identity(), next, false, now)).toMatchObject({ likes: 1, dislikes: 0 });
  }
  const parent = await db.prepare('SELECT contribution_id, release_id, artifact_digest FROM contribution_revisions WHERE revision_id = ?')
    .bind(revisionId).first<{ contribution_id: string; release_id: string; artifact_digest: string }>();
  await rawRevision('new-revision', parent!.release_id, { family: parent!.contribution_id, supersedes: revisionId });
  await rawRevision('derivative', parent!.release_id, { family: 'derived-family', depth: 1 });
  await db.prepare('INSERT INTO contribution_parents VALUES (?, ?, ?)').bind('derivative', revisionId, parent!.artifact_digest).run();
  for (const next of ['new-revision', 'derivative']) {
    expect(await db.prepare('SELECT likes, dislikes FROM contribution_statistics WHERE revision_id = ?').bind(next).first()).toEqual({ likes: 0, dislikes: 0 });
    expect(await db.prepare('SELECT count(*) AS total FROM revision_votes WHERE revision_id = ?').bind(next).first()).toEqual({ total: 0 });
  }
});

it('measures local mutation/retry writes and preserves unknown observation metadata', async context => {
  const revision = await db.prepare('SELECT artifact_digest FROM contribution_revisions WHERE revision_id = ?')
    .bind(revisionId).first<{ artifact_digest: string }>();
  await db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
    .bind('other', revisionId, 'c'.repeat(64), revision!.artifact_digest, now).run();
  const operationId = crypto.randomUUID(), costs = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    const observer = observeDownloadDatabase(db, 64);
    const output = await contributorVote(await request('like', operationId, other), observer.db, 'community.example', now,
      async () => 'eligible', { requestBytes: 8192, maxQueries: 64 });
    expect(output.status).toBe(200); costs.push(observer.snapshot());
  }
  Object.assign(context.task.meta, { measurement: { scope: 'local SQLite vote handler only', first: costs[0], retry: costs[1] } });
  expect(costs[0].rowsWritten).toBeLessThanOrEqual(32);
  expect(costs[1].rowsWritten).toBeLessThan(costs[0].rowsWritten!);
  const before = await db.prepare("SELECT unknown_reads FROM vote_usage_observations WHERE contributor_id = 'voter' AND outcome = 'allowed'").first<{ unknown_reads: number }>();
  await recordVoteUsage(db, 'voter', 'allowed', now, { rowsRead: null, rowsWritten: null });
  expect(await db.prepare("SELECT unknown_reads, unknown_writes FROM vote_usage_observations WHERE contributor_id = 'voter' AND outcome = 'allowed'").first())
    .toEqual({ unknown_reads: before!.unknown_reads + 1, unknown_writes: 1 });
});

it('wires native admission and continuing membership while refusing missing limits and production', async () => {
  const timestamp = Math.floor(Date.now() / 1000), limiter = { limit: vi.fn(async () => ({ success: true })) };
  await db.prepare("UPDATE contributors SET discord_user_id = '1554889314549243999' WHERE contributor_id = 'voter'").run();
  const provider = vi.fn(async () => Response.json({ user: { id: '1554889314549243999' } }));
  vi.stubGlobal('fetch', provider);
  const config = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
    VOTE_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }), DISCORD_GUILD_ID: '1554889314549243986',
    DISCORD_BOT_TOKEN: 'synthetic-token'};
  async function edgeRequest() {
    const signed = await request('like', undefined, key, timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  try {
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(200);
    expect(provider).toHaveBeenCalledOnce();
    await db.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp - 900).run();
    provider.mockImplementation(async () => { throw new Error('synthetic offline'); });
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(503);
    expect((await service.fetch(await edgeRequest(), { ...config, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect((await service.fetch(await edgeRequest(), { ...config, VOTE_LIMITS: undefined })).status).toBe(503);
    expect((await service.fetch(await edgeRequest(), { ...config, APP_ENV: 'production' })).status).toBe(503);
  } finally { vi.unstubAllGlobals(); }
});

it('refuses hidden revisions, including quarantine at the atomic mutation boundary', async () => {
  const before = await readVote(db, identity(), revisionId, false, now);
  const quarantining = new Proxy(db, { get(target, property) {
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      if (statements.length === 3) await db.prepare("UPDATE contribution_publications SET state = 'quarantined' WHERE revision_id = ?").bind(revisionId).run();
      return target.batch(statements);
    };
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
  const principal = identity();
  try {
    await expect(setVote(quarantining, principal, revisionId, { version: 1, vote: 'neutral' }, now)).rejects.toThrow('vote_ineligible');
    expect(await db.prepare("SELECT result FROM operations WHERE kind = 'vote' AND operation_id = ?").bind(principal.operationId).first()).toBeNull();
    expect((await handle(await request())).status).toBe(404);
    expect((await handle(await signedRead(key, path()))).status).toBe(404);
    expect((await handle(await signedRead(key, path() + 's'))).status).toBe(404);
  } finally { await db.prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = ?").bind(revisionId).run(); }
  expect(await readVote(db, identity(), revisionId, false, now)).toEqual(before);
});

it('hides a suspended author revision without erasing votes or import evidence', async () => {
  const before = await readVote(db, identity(), revisionId, false, now);
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'contributor-1'").run();
  try {
    expect((await handle(await signedRead(key, path()))).status).toBe(404);
    expect((await handle(await request())).status).toBe(404);
  } finally {
    await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'contributor-1'").run();
  }
  expect(await readVote(db, identity(), revisionId, false, now)).toEqual(before);
});

it('matches the frozen shared DTOs under signed mutation and read requests', async () => {
  expect(revisionId).toBe(contract.revisionId);
  await db.prepare("UPDATE revision_votes SET value = 'neutral' WHERE contributor_id = 'other'").run();
  const output = await handle(await signedPost(key, path(), JSON.stringify(contract.mutation), contract.operationId));
  expect(await output.json()).toEqual(contract.completed);
  expect(await (await handle(await signedRead(key, path()))).json()).toEqual(contract.own);
  expect(await (await handle(await signedRead(key, path() + 's'))).json()).toEqual(contract.aggregates);
});