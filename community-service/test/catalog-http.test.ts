import { tunnelActor } from '../src/runtime/environment.mjs';
import type { SqlStatement, SqlResult } from '../src/runtime/contracts';
import { beforeAll, expect, it, vi } from 'vitest';
import membershipMigration from '../migrations/0007_membership.sql?raw';
import importMigration from '../migrations/0012_import_receipts.sql?raw';
import voteMigration from '../migrations/0013_votes.sql?raw';
import moderationMigration from '../migrations/0014_moderation.sql?raw';
import contract from '../../tests/fixtures/community/catalog-lookup-v1.json';
import service from '../src/index';
import { contributorCatalog } from '../src/http/catalog';
import { availability, matches, type Candidate } from '../src/catalog/search';
import { searchLimits } from '../src/catalog/search-input';
import { matchEvidenceSchema } from '../src/catalog/compatibility-inputs';
import { publishArtifact } from '../src/contributions/publication';
import { transitionPublication } from '../src/contributions/repository';
import { fixtureKey, signedArtifact, syntheticPublication } from './helpers/publication-signatures';
import { signedPost, signingKey } from './helpers/key-lifecycle';
import { verifiedCase } from './helpers/catalog';
import { db, now, limits as publicationCaps, setupPublication } from './helpers/publication';
import { rankRevision } from '../src/reputation/ranking';
import { setVote, reconcileVoteCounts } from '../src/contributions/votes';
import { observeDownloadDatabase } from '../src/budgets/download-usage';
import { inspectModeration, moderate } from '../src/admin/moderation';

const caps = searchLimits({ requestBytes: 131072, responseBytes: 1048576, maxItems: 50,
  maxKeys: 300, maxQueries: 12, maxCandidates: 96, maxRows: 500, maxPageSize: 20,
  cacheEntries: 8, cacheBytes: 1048576, positiveTtlSeconds: 60, negativeTtlSeconds: 5 });
const eligible = async () => 'eligible' as const;
let evidence: ReturnType<typeof matchEvidenceSchema.parse>;
const handle = (request: Request, membership = eligible, limits = caps, timestamp = now) =>
  contributorCatalog(request, db, 'community.example', timestamp, membership, limits, 'policy-1');
const request = async (path = '/v1/matches', extra: Record<string, unknown> = {}) =>
  signedPost(await fixtureKey(), path, JSON.stringify(path === '/v1/matches' ?
    { version: 1, evidence, pageSize: 1, ...extra } : { version: 1, items: [{ id: 'item', evidence }], ...extra }), crypto.randomUUID());
async function publish() {
  const input = await syntheticPublication();
  await publishArtifact(db, { contributorId: 'contributor-1', keyId: 'key-1', displayName: 'Public Name',
    nonce: crypto.randomUUID(), operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 },
  input, publicationCaps, now);
  return input.submissionId;
}
beforeAll(async () => {
  await setupPublication();
  await db.exec(membershipMigration.replace(/\s*\n\s*/g, ' '));
  for (const migration of [importMigration, voteMigration, moderationMigration]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  const verified = await verifiedCase('ai');
  evidence = matchEvidenceSchema.parse(verified.artifact.summary.matching);
});

it('returns true empty availability, refreshes negative caches, and keeps failures distinct', async () => {
  const frozen = await handle(await signedPost(await fixtureKey(), '/v1/availability/batch', JSON.stringify(contract.availabilityRequest), crypto.randomUUID()));
  expect(await frozen.json()).toEqual({ version: 1, policyVersion: contract.policyVersion, items: [contract.emptyItem] });
  expect(await (await handle(await request('/v1/availability/batch'))).json()).toMatchObject({ items: [{ status: 'ok', candidateCount: 0, truncated: false }] });
  const revision = await publish();
  expect(await (await handle(await request('/v1/availability/batch'))).json()).toMatchObject({ items: [{ candidateCount: 1 }] });
  const missing = matchEvidenceSchema.parse({ media: { entity_type: 'movie', identifiers: [], release: {} } });
  const mixed = await handle(await request('/v1/availability/batch', { items: [{ id: 'known', evidence }, { id: 'missing', evidence: missing }] }));
  expect(await mixed.json()).toMatchObject({ items: [{ id: 'known', status: 'ok', candidateCount: 1 },
    { id: 'missing', status: 'missing_identity', candidateCount: null, channels: null }] });
  const output = await handle(await request());
  expect(output.headers.get('cache-control')).toBe('no-store');
  const result = await output.json() as { candidates: Candidate[] };
  expect(result.candidates[0].revisionId).toBe(revision);
  expect(result.candidates[0].compatibility.selected).toBe(false);
  expect(result.candidates[0].ranking).toEqual(rankRevision(0, 0));
  expect(JSON.stringify(result)).not.toMatch(/discord|object_key|privateKey|findings|request_nonces|quota|receipt/);
  for (const [membership, expected] of [['unavailable', contract.errors.membershipUnavailable], ['ineligible', contract.errors.nonmember]] as const) {
    const denied = await handle(await request(), async () => membership as never);
    expect(denied.status).toBe(expected.status);
    expect(await denied.json()).toEqual(expected.body);
  }
});

it('paginates despite newer inserts and rejects changed moderation snapshots', async () => {
  await publish(); await publish();
  const first = await (await handle(await request())).json() as { candidates: Candidate[]; nextCursor: string };
  expect(first.nextCursor).toBeTruthy();
  const inserted = await publish();
  const second = await (await handle(await request('/v1/matches', { cursor: first.nextCursor, pageSize: 20 }))).json() as { candidates: Candidate[] };
  expect(second.candidates).toHaveLength(2);
  expect(second.candidates.map(item => item.revisionId)).not.toContain(inserted);
  expect(second.candidates.map(item => item.revisionId)).not.toContain(first.candidates[0].revisionId);
  await transitionPublication(db, { revisionId: first.candidates[0].revisionId, from: 'published', to: 'withdrawn', now: now + 1 });
  expect((await handle(await request('/v1/matches', { cursor: first.nextCursor }))).status).toBe(400);
  const restarted = await (await handle(await request('/v1/matches', { pageSize: 20, refresh: true }))).json() as { candidates: Candidate[] };
  expect(restarted.candidates.map(item => item.revisionId)).not.toContain(first.candidates[0].revisionId);
  expect((await handle(await request('/v1/matches', { cursor: 'bad' }))).status).toBe(400);
  const changed = structuredClone(evidence); changed.media.release.edition = 'different';
  expect((await handle(await request('/v1/matches', { cursor: first.nextCursor, evidence: changed }))).status).toBe(400);
});

it('rechecks cache authorization, moderation, current attribution and aggregate statistics', async () => {
  const initial = await (await handle(await request('/v1/matches', { pageSize: 20 }))).json() as { candidates: Candidate[] };
  const revision = initial.candidates[0].revisionId;
  await db.prepare("UPDATE contributors SET display_name = 'Renamed Public Name' WHERE contributor_id = 'contributor-1'").run();
  await db.prepare('UPDATE contribution_statistics SET imports = 7, likes = 3 WHERE revision_id = ?').bind(revision).run();
  const renamed = await (await handle(await request('/v1/matches', { pageSize: 20 }))).json() as { candidates: Candidate[] };
  expect(renamed.candidates.find(item => item.revisionId === revision)).toMatchObject({ attribution: { displayName: 'Renamed Public Name' }, statistics: { imports: 7, likes: 3 } });
  await transitionPublication(db, { revisionId: revision, from: 'published', to: 'quarantined', now: now + 2 });
  const moderated = await (await handle(await request('/v1/matches', { pageSize: 20 }))).json() as { candidates: Candidate[] };
  expect(moderated.candidates.map(item => item.revisionId)).not.toContain(revision);
  await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = 'key-1'").bind(now).run();
  try { expect((await handle(await request())).status).toBe(401); }
  finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run(); }
  const other = await signingKey();
  expect((await handle(await signedPost(other, '/v1/matches', '{}', crypto.randomUUID()))).status).toBe(401);
});

it('hides suspended authors on warm catalog caches and rejects their prior page cursors', async () => {
  const initial = await matches(db, { id: 'suspension', evidence }, 1, null, 'policy-1', now, true, caps);
  expect(initial.candidates.length).toBe(1);
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'contributor-1'").run();
  try {
    const hidden = await matches(db, { id: 'suspension', evidence }, 20, null, 'policy-1', now, false, caps);
    expect(hidden.candidates).toEqual([]);
    if (initial.nextCursor) await expect(matches(db, { id: 'suspension', evidence }, 1, initial.nextCursor, 'policy-1', now, false, caps)).rejects.toThrow('invalid_cursor');
  } finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'contributor-1'").run(); }
  expect((await matches(db, { id: 'suspension', evidence }, 20, null, 'policy-1', now, false, caps)).candidates.length).toBeGreaterThan(0);
});

it('recalculates from current votes on cache hits, invalidates stale cursors and preserves volume neutrality', async () => {
  const initial = await matches(db, { id: 'target', evidence }, 20, null, 'policy-1', now, true, caps);
  const ids = initial.candidates.map(item => item.revisionId);
  await reconcileVoteCounts(db, ids);
  const voted = initial.candidates[0];
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('ranking-reader', 'ranking-discord', 'Ranking Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('ranking-install', 'ranking-reader', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('ranking-key', 'ranking-install', 'Ed25519', ?, 'active', 1, NULL)").bind(new Uint8Array(32)),
    db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
      .bind('ranking-reader', voted.revisionId, 'e'.repeat(64), voted.artifactDigest, now),
  ]);
  const identity = () => ({ contributorId: 'ranking-reader', keyId: 'ranking-key', displayName: 'Ranking Reader',
    nonce: crypto.randomUUID(), operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 });
  await setVote(db, identity(), voted.revisionId, { version: 1, vote: 'like' }, now);
  const first = await matches(db, { id: 'target', evidence }, 1, null, 'policy-1', now, false, caps);
  expect(first.candidates[0].revisionId).toBe(voted.revisionId);
  expect(first.candidates[0].ranking).toEqual(rankRevision(1, 0));
  expect(first.nextCursor).toBeTruthy();
  await db.prepare('UPDATE contribution_statistics SET imports = imports + 1000000 WHERE revision_id = ?').bind(voted.revisionId).run();
  expect((await matches(db, { id: 'target', evidence }, 1, null, 'policy-1', now, false, caps)).candidates[0].ranking).toEqual(rankRevision(1, 0));
  await matches(db, { id: 'target', evidence }, 20, first.nextCursor, 'policy-1', now, false, caps);
  await expect(matches(db, { id: 'target', evidence }, 20, first.nextCursor, 'policy-2', now, false, caps)).rejects.toThrow('invalid_cursor');
  const legacy = JSON.parse(atob(first.nextCursor!)); legacy.version = 'community-search-1';
  await expect(matches(db, { id: 'target', evidence }, 20, btoa(JSON.stringify(legacy)), 'policy-1', now, false, caps)).rejects.toThrow('invalid_cursor');
  await setVote(db, identity(), voted.revisionId, { version: 1, vote: 'neutral' }, now);
  await expect(matches(db, { id: 'target', evidence }, 20, first.nextCursor, 'policy-1', now, false, caps)).rejects.toThrow('invalid_cursor');
  const reversed = await matches(db, { id: 'target', evidence }, 20, null, 'policy-1', now, false, caps);
  expect(reversed.candidates.every(item => item.ranking.score === rankRevision(0, 0).score)).toBe(true);
  expect(reversed.candidates.map(item => item.revisionId)).toEqual([...ids].sort());
  await setVote(db, identity(), voted.revisionId, { version: 1, vote: 'like' }, now);
  const rated = await matches(db, { id: 'target', evidence }, 1, null, 'policy-1', now, false, caps);
  const vote = await inspectModeration(db, 'vote', 'ranking-reader', voted.revisionId);
  await moderate(db, 'operator', { version: 1, actionId: crypto.randomUUID(), command: 'exclude_vote', targetId: 'ranking-reader',
    revisionId: voted.revisionId, expectedRevision: vote.revision, reason: 'Confirmed abusive rating' }, now);
  await expect(matches(db, { id: 'target', evidence }, 20, rated.nextCursor, 'policy-1', now, false, caps)).rejects.toThrow('invalid_cursor');
  const excluded = await matches(db, { id: 'target', evidence }, 20, null, 'policy-1', now, false, caps);
  expect(excluded.candidates.find(item => item.revisionId === voted.revisionId)?.ranking).toEqual(rankRevision(0, 0));
  const excludedVote = await inspectModeration(db, 'vote', 'ranking-reader', voted.revisionId);
  await moderate(db, 'operator', { version: 1, actionId: crypto.randomUUID(), command: 'restore_vote', targetId: 'ranking-reader',
    revisionId: voted.revisionId, expectedRevision: excludedVote.revision, reason: 'Review corrected exclusion' }, now);
  const restored = await matches(db, { id: 'target', evidence }, 20, null, 'policy-1', now, false, caps);
  expect(restored.candidates.find(item => item.revisionId === voted.revisionId)?.ranking).toEqual(rankRevision(1, 0));
});

it('refuses a cached response when author suspension races the final authority fence', async () => {
  const read = () => request('/v1/matches', { pageSize: 20 });
  expect((await handle(await read())).status).toBe(200);
  const race = new Proxy(db, { get(target, property) {
    if (property === 'prepare') return (sql: string) => {
      const statement = target.prepare(sql);
      if (!sql.includes('SELECT 1 FROM installation_keys k')) return statement;
      return new Proxy(statement, { get(original, method) {
        if (method === 'bind') return (...values: unknown[]) => {
          const bound = original.bind(...values);
          return { first: async () => {
            await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'contributor-1'").run();
            return bound.first();
          } };
        };
        const value = Reflect.get(original, method); return typeof value === 'function' ? value.bind(original) : value;
      } });
    };
    const value = Reflect.get(target, property); return typeof value === 'function' ? value.bind(target) : value;
  } });
  try {
    const response = await contributorCatalog(await read(), race, 'community.example', now, eligible, caps, 'policy-1', db);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: { code: 'service_unavailable' } });
  } finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'contributor-1'").run(); }
});

it('measures ranked cold/warm lookup cost within configured bounds', async context => {
  await publish(); await publish();
  const costs = [];
  for (const refresh of [true, false]) {
    const observer = observeDownloadDatabase(db, 64);
    const started = performance.now();
    const output = await contributorCatalog(await request('/v1/matches', { pageSize: 20, refresh }), observer.db,
      'community.example', now, eligible, caps, 'policy-1', db);
    expect(output.status).toBe(200);
    const bytes = await output.arrayBuffer();
    const usage = observer.snapshot();
    expect(usage.queries).toBeLessThanOrEqual(64);
    expect(bytes.byteLength).toBeLessThanOrEqual(caps.responseBytes);
    costs.push({ refresh, ...usage, responseBytes: bytes.byteLength, wallMs: Number((performance.now() - started).toFixed(3)) });
  }
  Object.assign(context.task.meta, { measurement: { scope: 'local signed ranked lookup; excludes native/bot work', costs } });
});

it('keeps uncertain channels separate, bounded results honest and response sizes enforced', async () => {
  const uncertain = structuredClone(evidence);
  uncertain.fingerprints = []; uncertain.video_stream = null; uncertain.audio_stream = null;
  const output = await (await handle(await request('/v1/matches', { evidence: uncertain, pageSize: 20 }))).json() as { candidates: Candidate[] };
  expect(output.candidates.every(item => item.compatibility.audio.band === 'possible_cut')).toBe(true);
  expect(output.candidates.every(item => !item.compatibility.audio.strong_eligible)).toBe(true);
  const small = { ...caps, maxCandidates: 1, maxPageSize: 1 };
  expect((await handle(await request(), eligible, small)).status).toBe(413);
  expect(await (await handle(await request('/v1/availability/batch'), eligible, small)).json()).toMatchObject({ items: [{ candidateCount: 1, truncated: true }] });
  expect((await handle(await request(), eligible, { ...caps, responseBytes: 10 })).status).toBe(413);
  expect((await handle(await request(), eligible, { ...caps, requestBytes: 10 })).status).toBe(413);
});

it('shares public metadata across contributors without caching private accounting headers', async () => {
  const other = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('reader-2', 'reader-discord', 'Second Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('reader-installation', 'reader-2', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'reader-installation', 'Ed25519', ?, 'active', 1, NULL)").bind(other.keyId, other.publicKey),
  ]);
  const prepare = db.prepare.bind(db);
  const spy = vi.spyOn(db, 'prepare').mockImplementation(sql => {
    if (sql.includes('INSERT INTO usage_observations')) throw new Error('synthetic telemetry outage');
    return prepare(sql);
  });
  let first: unknown;
  try {
    const output = await handle(await request('/v1/matches', { pageSize: 20 }));
    expect(output.status).toBe(200);
    expect(output.headers.get('Community-Usage-Status')).toBe('unavailable');
    first = await output.json();
  } finally { spy.mockRestore(); }
  const signed = await signedPost(other, '/v1/matches', JSON.stringify({ version: 1, evidence, pageSize: 20 }), crypto.randomUUID());
  const second = await handle(signed);
  expect(second.status).toBe(200);
  expect(second.headers.has('Community-Usage-Status')).toBe(false);
  expect(await second.json()).toEqual(first);
  expect(JSON.stringify(first)).not.toContain('reader-2');
  expect(await db.prepare("SELECT occurrences FROM usage_observations WHERE contributor_id = 'reader-2' AND resource = 'worker_requests' AND outcome = 'allowed'").first()).toEqual({ occurrences: 1 });
});

it('invalidates unchanged-sequence negative caches immediately after moderation restoration', async () => {
  const target = { id: 'negative', evidence: structuredClone(evidence) };
  const all = await matches(db, { id: 'target', evidence }, 20, null, 'policy-1', now, true, caps);
  const identity = await db.prepare('SELECT contribution_id, release_id FROM contribution_revisions WHERE revision_id = ?')
    .bind(all.candidates[0].revisionId).first<{ contribution_id: string; release_id: string }>();
  expect(all.candidates[0].contributionId).toBe(identity!.contribution_id);
  expect(all.candidates[0].releaseId).toBe(identity!.release_id);
  for (const item of all.candidates) await transitionPublication(db, { revisionId: item.revisionId, from: 'published', to: 'withdrawn', now: now + 3 });
  expect((await availability(db, [target], 'policy-1', now, true, caps)).items[0].candidateCount).toBe(0);
  const revision = all.candidates[0].revisionId;
  await transitionPublication(db, { revisionId: revision, from: 'withdrawn', to: 'published', now: now + 4 });
  expect((await availability(db, [target], 'policy-1', now + 1, false, caps)).items[0].candidateCount).toBe(1);
  expect((await availability(db, [target], 'policy-1', now + 5, false, caps)).items[0].candidateCount).toBe(1);
  await transitionPublication(db, { revisionId: revision, from: 'published', to: 'withdrawn', now: now + 5 });
  expect((await availability(db, [target], 'policy-1', now + 6, true, caps)).items[0].candidateCount).toBe(0);
  await transitionPublication(db, { revisionId: revision, from: 'withdrawn', to: 'published', now: now + 6 });
  expect((await availability(db, [target], 'policy-1', now + 6, true, caps)).items[0].candidateCount).toBe(1);
});

it('refuses Radarr and Sonarr provenance in match and availability requests', async () => {
  const arrDuration = structuredClone(evidence);
  arrDuration.media.release.duration = { seconds: 5400, source: 'radarr_catalog_minutes' as never, precision_seconds: 60 };
  const arrIdentifier = structuredClone(evidence);
  arrIdentifier.media.identifiers = arrIdentifier.media.identifiers.map(item => ({ ...item, provenance: 'sonarr.series' }));
  for (const [changed, status] of [[evidence, 200], [arrDuration, 400], [arrIdentifier, 400]] as const) {
    for (const path of ['/v1/matches', '/v1/availability/batch']) {
      const response = await handle(await request(path, path === '/v1/matches' ? { evidence: changed } : { items: [{ id: 'item', evidence: changed }] }));
      expect(response.status, path).toBe(status);
    }
  }
});

it('observes malformed signed reads and distinguishes database failures from empty results', async () => {
  const key = await fixtureKey();
  const before = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  expect((await handle(await signedPost(key, '/v1/matches', '{', crypto.randomUUID()))).status).toBe(400);
  const after = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  expect(after!.used).toBe((before?.used ?? 0) + 1);
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
  try {
    const output = await handle(await request());
    expect(output.status).toBe(200);
  } finally { await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run(); }
  const prepare = db.prepare.bind(db);
  const spy = vi.spyOn(db, 'prepare').mockImplementation(sql => {
    if (sql.includes('MAX(revision_sequence)')) throw new Error('synthetic db outage');
    return prepare(sql);
  });
  try {
    const denied = await handle(await request());
    expect(denied.status).toBe(contract.errors.unavailable.status);
    expect(await denied.json()).toEqual(contract.errors.unavailable.body);
  }
  finally { spy.mockRestore(); }
});

it('measures local 50-item cold/warm read cost without claiming hosted CPU or billing', async context => {
  const originals = new WeakMap<SqlStatement, SqlStatement>();
  let statements = 0, rowsRead: number | null = 0, rowsWritten: number | null = 0;
  function observe(result: SqlResult) {
    statements += 1;
    rowsRead = rowsRead === null || result.meta.rows_read === null ? null : rowsRead + result.meta.rows_read;
    rowsWritten = rowsWritten === null || result.meta.rows_written === null ? null : rowsWritten + result.meta.rows_written;
  }
  function tracked(statement: SqlStatement): SqlStatement {
    const proxy = new Proxy(statement, { get(target, property) {
      if (property === 'bind') return (...values: unknown[]) => tracked(target.bind(...values));
      if (['all', 'run', 'first'].includes(String(property))) return async () => {
        const result = await target.all(); observe(result);
        return property === 'first' ? result.results[0] ?? null : result;
      };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    originals.set(proxy, statement); return proxy;
  }
  const measured = new Proxy(db, { get(target, property) {
    if (property === 'prepare') return (sql: string) => tracked(target.prepare(sql));
    if (property === 'batch') return async (items: SqlStatement[]) => {
      const results = await target.batch(items.map(item => originals.get(item) ?? item));
      results.forEach(observe); return results;
    };
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
  const measurements = [];
  for (const refresh of [true, false]) {
    for (let sample = 0; sample < 5; sample += 1) {
      statements = 0; rowsRead = 0; rowsWritten = 0;
      const body = JSON.stringify({ version: 1, refresh,
        items: Array.from({ length: 50 }, (_, index) => ({ id: `item-${index}`, evidence })) });
      const signed = await signedPost(await fixtureKey(), '/v1/availability/batch', body, crypto.randomUUID());
      const started = performance.now();
      const output = await contributorCatalog(signed, measured, 'community.example', now, eligible, caps, 'policy-1', db);
      const wallMs = performance.now() - started;
      expect(output.status).toBe(200);
      expect(statements).toBeLessThanOrEqual(50);
      measurements.push({ mode: refresh ? 'cold' : 'warm', wallMs: Number(wallMs.toFixed(3)), statements, rowsRead, rowsWritten,
        responseBytes: (await output.arrayBuffer()).byteLength });
    }
  }
  Object.assign(context.task.meta, { measurement: { scope: 'isolated local Node, fake eligible membership; excludes native/bot work', measurements } });
  console.info('CSS-41 local baseline', JSON.stringify(measurements));
});

it('uses native admission and real bot membership on Node service dispatch without cache bypass', async () => {
  await db.prepare("UPDATE contributors SET discord_user_id = '1554889314549243999' WHERE contributor_id = 'contributor-1'").run();
  const timestamp = Math.floor(Date.now() / 1000);
  const provider = vi.fn(async () => Response.json({ user: { id: '1554889314549243999' } }));
  vi.stubGlobal('fetch', provider);
  const limiter = { limit: vi.fn(async () => ({ success: true })) };
  const config = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
    LOOKUP_LIMITS: JSON.stringify(caps), DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-test-token'};
  async function edgeRequest() {
    const signed = await signedPost(await fixtureKey(), '/v1/matches', JSON.stringify({ version: 1, evidence, pageSize: 1 }), crypto.randomUUID(), timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  const prepare = db.prepare.bind(db);
  const spy = vi.spyOn(db, 'prepare').mockImplementation(prepare);
  try {
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('WITH requested'))).toBe(true);
    spy.mockClear();
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('WITH requested'))).toBe(false);
    expect(provider).toHaveBeenCalledOnce();
    await db.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp - 900).run();
    provider.mockImplementation(async () => { throw new Error('synthetic offline provider'); });
    const denied = await service.fetch(await edgeRequest(), config);
    expect(denied.status).toBe(503);
    expect(await denied.json()).toEqual(contract.errors.membershipUnavailable.body);
    spy.mockClear();
    expect((await service.fetch(await edgeRequest(), { ...config, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect(spy).not.toHaveBeenCalled();
  } finally { spy.mockRestore(); vi.unstubAllGlobals(); }
});

it('rejects tampering, wrong methods, query strings and unconfigured/production activation', async () => {
  const signed = await request();
  expect((await handle(new Request(signed.url, { method: 'POST', headers: signed.headers, body: '{}' }))).status).toBe(401);
  expect((await handle(new Request(signed.url))).status).toBe(405);
  expect((await handle(await signedPost(await fixtureKey(), '/v1/matches?extra=1', '{}', crypto.randomUUID()))).status).toBe(400);
  expect((await service.fetch(signed, { APP_ENV: 'production' })).status).toBe(503);
  expect((await service.fetch(signed, { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor })).status).toBe(503);
});

it('returns extended filter details only to opted-in match clients', async () => {
  const { artifact } = (await verifiedCase('ai')).artifact;
  if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2');
  const input = await signedArtifact({
    ...artifact,
    submission_id: crypto.randomUUID(),
    sharing_terms: 'community-reuse-1',
    manual_filter_tracking: 'manual-1',
    findings: artifact.findings.map(finding => ({ ...finding, manual: { added: false, edited: false } })),
  });
  await publishArtifact(db, { contributorId: 'contributor-1', keyId: 'key-1', displayName: 'Public Name',
    nonce: crypto.randomUUID(), operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 },
  input, publicationCaps, now);
  for (const include of [false, true, false]) {
    const signed = await request('/v1/matches', { pageSize: 20, refresh: true });
    if (include) signed.headers.set('Editarr-Filter-Details', '1');
    const output = await handle(signed);
    expect(output.status).toBe(200);
    const result = await output.json() as { candidates: Candidate[] };
    const summary = result.candidates.find(candidate => candidate.revisionId === input.submissionId)!.summary;
    if (include) {
      expect(summary).toMatchObject({ manual_filter_counts: { added: 0, edited: 0 },
        models: [{ capability: 'audio_scan', model_id: 'test-model', model_revision: 'test-revision' }] });
    } else {
      expect(summary).not.toHaveProperty('manual_filter_counts');
      expect(summary).not.toHaveProperty('models');
    }
  }
});
