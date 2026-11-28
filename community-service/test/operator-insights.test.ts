import { beforeAll, describe, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { audit, seedInsights } from './helpers/insights';
import { db, now } from './helpers/versions';

const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }) };
let jwt: string, resolver: JWTVerifyGetKey, seed: Awaited<ReturnType<typeof seedInsights>>;

beforeAll(async () => {
  seed = await seedInsights();
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});

const handle = (path: string, token = jwt) => operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/${path}`,
  { headers: { 'Cf-Access-Jwt-Assertion': token } }), settings, resolver, new Date(now * 1000));
const read = async (path: string) => {
  const response = await handle(path);
  const body = await response.json() as Record<string, any>;
  if (response.status !== 200) throw new Error(`${path}: ${response.status} ${JSON.stringify(body)}`);
  return body;
};
const status = async (path: string) => {
  const response = await handle(path);
  return [response.status, (await response.json() as { error?: { code: string } }).error?.code];
};
const paths = () => ['home', 'items', `items/${seed.workId}`, `items/${seed.workId}/versions/${seed.versionId}`, `filters/${seed.gore}/context`,
  'activity', 'contributors/leaderboard', `contributors/${seed.ana.contributorId}/profile`, 'queue', 'search?q=arrival', 'audit'];

describe('access and validation', () => {
  it('requires operator access, answers within the query budget and never exposes secrets', async () => {
    for (const path of paths()) {
      expect((await handle(path, '')).status).toBe(401);
      const response = await handle(path);
      expect(response.status).toBe(200);
      expect(response.headers.get('Cache-Control')).toBe('private, no-store');
      const body = await response.text();
      expect(JSON.parse(body)).toMatchObject({ version: 1, observedAt: now * 1000 });
      for (const forbidden of ['discord', 'public_key', 'publicKey', 'request_digest', 'request_json', 'artifact_bytes', 'digest', 'match_keys_json'])
        expect(body).not.toContain(`"${forbidden}`);
    }
    const post = await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/home`, { method: 'POST',
      headers: { 'Cf-Access-Jwt-Assertion': jwt, Origin: settings.OPERATOR_ORIGIN, 'Sec-Fetch-Site': 'same-origin',
        'X-Editarr-Operator': '1' } }), settings, resolver, new Date(now * 1000));
    expect(post.status).not.toBe(200);
  });

  it('rejects unknown, repeated or invalid query parameters with endpoint-specific codes', async () => {
    const cases: [string, string][] = [['home?period=1y', 'invalid_home_query'], ['home?x=1', 'invalid_home_query'],
      ['items?sort=rating', 'invalid_items_query'], ['items?sort=title&sort=title', 'invalid_items_query'], ['items?after=!', 'invalid_items_query'],
      ['items?q=%20%20', 'invalid_items_query'], ['items?after=WyJ4Il0=', 'invalid_items_query'],
      [`items/${seed.workId}?x=1`, 'invalid_item_query'], [`items/${seed.workId}/versions/${seed.versionId}?before=WyJ4Il0=`, 'invalid_item_query'],
      [`filters/${seed.gore}/context?full=1`, 'invalid_filter_query'], ['activity?kind=likes', 'invalid_activity_query'],
      ['activity?before=WzEsIngiXQ==', 'invalid_activity_query'], ['contributors/leaderboard?metric=karma', 'invalid_leaderboard_query'],
      ['contributors/leaderboard?period=90d', 'invalid_leaderboard_query'], [`contributors/${seed.ana.contributorId}/profile?x=1`, 'invalid_profile_query'],
      ['queue?kind=spam', 'invalid_queue_query'], ['search', 'invalid_search_query'], [`search?q=${'a'.repeat(101)}`, 'invalid_search_query'],
      ['audit?after=e30=', 'invalid_audit_query']];
    for (const [path, code] of cases) expect([path, ...await status(path)]).toEqual([path, 400, code]);
    for (const path of ['items/w_missing', `items/w_untitled/versions/${seed.versionId}`, 'filters/f_missing/context', 'contributors/c_missing/profile'])
      expect(await status(path)).toEqual([404, 'resource_not_found']);
    expect(await status('items/a/b/c')).toEqual([404, 'not_found']);
  });
});

describe('home', () => {
  it('summarises KPIs against the previous period with bucketed series', async () => {
    const day = await read('home?period=24h');
    expect(day.kpis.uploads).toMatchObject({ value: 3, previous: 0, items: 2 });
    expect(day.kpis.uploads.series).toHaveLength(24);
    expect(day.kpis.uploads.series[0].t).toBe((now - 86_400) * 1000);
    expect(day.kpis.uploads.series.at(-1).v).toBe(3);
    expect(day.kpis.activeContributors).toMatchObject({ value: 4, previous: 0 });
    expect(day.kpis.votes).toMatchObject({ total: { value: 2, previous: 0 }, upShare: 0.5 });
    expect(day.kpis.openReports).toEqual({ count: 3, oldestAt: (now - 10_800) * 1000, versionLevel: 1 });
    expect(day.kpis.lastBackup).toBeNull();
    expect((await read('home?period=30d')).kpis.uploads.series).toHaveLength(30);
  });

  it('includes the top queue items, latest revisions, leaders, activity and health', async () => {
    const body = await read('home');
    expect(body.period).toBe('7d');
    expect(body.queueCounts).toEqual({ all: 7, reports: 3, aliases: 1, versions: 1, quarantined: 1, disputed: 1 });
    expect(body.attention).toHaveLength(5);
    expect(body.latestUpdates.length).toBeGreaterThan(0);
    expect(body.latestUpdates[0]).toMatchObject({ at: now * 1000, item: { title: expect.any(String) }, version: { label: expect.any(String) },
      cause: { kind: 'upload' } });
    expect(body.topContributors[0]).toMatchObject({ rank: 1, accepted: 1, contributor: { displayName: expect.any(String) } });
    expect(body.activity.length).toBeLessThanOrEqual(8);
    expect(body.health).toMatchObject({ ready: expect.any(Boolean), checks: expect.any(Array), storage: { quotaBytes: 350_000_000 } });
  });

  it('reports the latest finished backup', async () => {
    await db.prepare(`INSERT INTO backup_runs (run_id, started_at, trigger, bytes, status) VALUES ('b1', ?, 'scheduled', 4096, 'verified'),
      ('b2', ?, 'manual', NULL, 'running')`).bind((now - 600) * 1000, (now - 10) * 1000).run();
    expect((await read('home')).kpis.lastBackup).toEqual({ status: 'verified', at: (now - 600) * 1000, bytes: 4096, nextAt: null });
  });
});

describe('items', () => {
  it('lists works with labelled versions, aggregates and top contributors, paging by keyset', async () => {
    const first = await read('items');
    expect(first.items).toHaveLength(20);
    const arrival = first.items.find((item: any) => item.workId === seed.workId);
    expect(arrival).toMatchObject({ title: 'Arrival Road', year: 1977, kind: 'movie', filters: 2, netVotes: 0, openReports: 1, uploads: 2,
      lastChangeAt: now * 1000, versions: [{ versionId: seed.versionId, label: "Director's Cut", status: 'confirmed', quarantined: false }] });
    expect(arrival.contributors.map((c: any) => c.displayName).sort()).toEqual(['Jonas Lind', 'Mira Okafor']);
    expect(first.items.find((item: any) => item.workId === 'w_untitled')).toMatchObject({ title: 'w_untitled', year: null, versions: [] });
    const rest = await read(`items?after=${encodeURIComponent(first.nextCursor)}`);
    expect(rest.nextCursor).toBeNull();
    expect([...first.items, ...rest.items].map((item: any) => item.workId)).toHaveLength(25);
    expect(new Set([...first.items, ...rest.items].map((item: any) => item.workId)).size).toBe(25);
  });

  it('sorts by title ascending and by reports, and filters by normalized title', async () => {
    // A >256-character, non-ASCII title ends the first page, so the cursor carries it.
    const long = `Paged Film 17 ${'x'.repeat(290)} ü日本`;
    await db.prepare(`INSERT INTO works (work_id, kind, catalog_seq, created_at) VALUES ('w_long', 'movie', 0, 1)`).run();
    await db.prepare(`INSERT INTO work_titles (work_id, normalized, year, title, observations) VALUES ('w_long', 'long', 2017, ?, 1)`)
      .bind(long).run();
    const titles = await read('items?sort=title');
    expect(titles.items.slice(0, 2).map((item: any) => item.title)).toEqual(['Arrival Road', 'Blue Comet']);
    expect(titles.items.at(-1).title).toBe(long);
    const next = await read(`items?sort=title&after=${encodeURIComponent(titles.nextCursor)}`);
    expect(next.items.map((item: any) => item.workId)).toEqual(['w_page_18', 'w_page_19', 'w_page_20', 'w_page_21', 'w_page_22', 'w_untitled']);
    expect((await read('items?sort=reports')).items.slice(0, 2).map((item: any) => item.openReports)).toEqual([1, 1]);
    expect((await read('items?q=Blue%20COMET!')).items.map((item: any) => item.title)).toEqual(['Blue Comet']);
  });

  it('details a work with versions, evidence, downloads and moderation tokens', async () => {
    const body = await read(`items/${seed.workId}`);
    expect(body.work).toMatchObject({ workId: seed.workId, title: 'Arrival Road', year: 1977, aliases: [],
      identifiers: [{ provider: 'tmdb', kind: 'movie', value: '700', season: null }] });
    expect(body.versions).toEqual([expect.objectContaining({ versionId: seed.versionId, label: "Director's Cut", status: 'confirmed',
      downloads: 2, uploads: 2, filters: 2, servedFilters: 2, openReports: 1, quarantined: false,
      matchKeys: [{ type: 'md5_quick', algorithm: '', sizeBytes: 1000, isReference: true, observations: 2 }],
      moderation: { resource: `version:${seed.versionId}`, revision: 0 } })]);
    expect(body.versions[0].contributors.map((c: any) => c.displayName)).toEqual(['Mira Okafor', 'Jonas Lind']);
  });

  it('details a version with filter stats and summarised revisions', async () => {
    const body = await read(`items/${seed.workId}/versions/${seed.versionId}`);
    expect(body.item.title).toBe('Arrival Road');
    expect(body.versionInfo).toMatchObject({ versionId: seed.versionId, label: "Director's Cut" });
    expect(body.filters.map((filter: any) => [filter.filterId, filter.quarantined, filter.openReports])).toEqual([[seed.gentle, true, 0],
      [seed.gore, false, 1]]);
    expect(body.filters[1]).toMatchObject({ category: 'violence', subcategory: 'stabbing', severity: 'high', downvotes: 1, served: true });
    expect(body.revisions.map((revision: any) => [revision.revision, revision.current])).toEqual([[4, true], [3, false], [2, false], [1, false]]);
    // Revision 3's snapshot was pruned and never downloaded, so the change counts are unknown rather than guessed.
    expect(body.revisions[0]).toMatchObject({ generatedAt: now * 1000, filters: 2, added: null, cause: { kind: 'upload' } });
    expect(body.revisionsNextCursor).toBeNull();
  });

  it('diffs revisions from retained snapshots or filter IDs and pages them', async () => {
    const snapshot = (filters: [string, number, string][]) => new TextEncoder().encode(JSON.stringify({ filters: filters.map(([filterId,
      startMs, subcategory]) => ({ filterId, startMs, endMs: startMs + 1000, category: 'violence', subcategory })) }));
    await db.prepare(`INSERT INTO versions (version_id, work_id, status, trust, revision, created_seq, created_at, label_text)
      VALUES ('v_rev', 'w_untitled', 'confirmed', 0.5, 13, 900, ?, 'rev cut')`).bind(now - 9000).run();
    const revision = (number: number, bytes: Uint8Array | null) => db.prepare(`INSERT INTO master_revisions (version_id, revision,
      snapshot_digest, generated_at, snapshot_bytes) VALUES ('v_rev', ?, ?, ?, ?)`).bind(number, 'a'.repeat(64), now - 9000 + number, bytes).run();
    await revision(1, snapshot([['fa', 0, 'punching'], ['fb', 5000, 'punching']]));
    await revision(2, snapshot([['fa', 100, 'kicking'], ['fc', 9000, 'punching']]));
    await revision(3, null);
    for (const filterId of ['fa', 'fc', 'fd']) await db.prepare(`INSERT INTO master_revision_filters (version_id, revision, filter_id)
      VALUES ('v_rev', 3, ?)`).bind(filterId).run();
    for (let number = 4; number <= 13; number++) await revision(number, null);
    await db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, state) VALUES ('fa', 'v_rev', 1, 'active')`).run();
    await audit('rev-2-action', 'filter:fa', 'quarantine', (now - 9000 + 2) * 1000 + 500);
    // Same-second actions on other versions, backups and other recomputes are not blamed.
    await db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, state) VALUES ('fz', ?, 1, 'active')`)
      .bind(seed.cometVersionId).run();
    await audit('rev-1-other-filter', 'filter:fz', 'quarantine', (now - 9000 + 1) * 1000);
    await audit('rev-1-backup', 'backup', 'backup_run', (now - 9000 + 1) * 1000 + 10);
    const recompute = (id: string, at: number, versionIds: string[]) => db.prepare(`INSERT INTO moderation_actions (action_id, actor,
      resource, target_id, command, expected_revision, reason, request_json, outcome, error_code, created_at)
      VALUES (?, 'operator-subject', 'recompute', 'versions', 'recompute', 0, 'r', ?, 'applied', NULL, ?)`)
      .bind(id, JSON.stringify({ versionIds }), at).run();
    await recompute('rev-1-recompute-other', (now - 9000 + 1) * 1000 + 20, [seed.versionId]);
    await recompute('rev-3-recompute', (now - 9000 + 3) * 1000 + 20, ['v_rev']);
    const first = await read(`items/w_untitled/versions/v_rev`);
    expect(first.revisions).toHaveLength(10);
    const rest = await read(`items/w_untitled/versions/v_rev?before=${encodeURIComponent(first.revisionsNextCursor)}`);
    expect(rest.revisionsNextCursor).toBeNull();
    expect(rest.revisions.map(({ revision, filters, added, removed, retimed, recategorised, cause }: any) =>
      ({ revision, filters, added, removed, retimed, recategorised, cause }))).toEqual([
      { revision: 3, filters: 3, added: 1, removed: 0, retimed: null, recategorised: null,
        cause: { kind: 'moderation', actor: 'operator-subject', command: 'recompute' } },
      { revision: 2, filters: 2, added: 1, removed: 1, retimed: 1, recategorised: 1,
        cause: { kind: 'moderation', actor: 'operator-subject', command: 'quarantine' } },
      { revision: 1, filters: 2, added: 2, removed: 0, retimed: 0, recategorised: 0, cause: null }]);
    expect(first.revisions[0]).toMatchObject({ revision: 13, current: true, filters: null, added: null });
  });

  it('gives a filter its votes, attestations, reports and moderation with labels', async () => {
    const body = await read(`filters/${seed.gore}/context`);
    expect(body.filter).toMatchObject({ filterId: seed.gore, subcategory: 'stabbing', openReports: 1 });
    expect(body.item.title).toBe('Arrival Road');
    expect(body.versionLabel).toEqual({ versionId: seed.versionId, label: "Director's Cut" });
    expect(body.votes).toEqual([expect.objectContaining({ contributor: { contributorId: seed.ana.contributorId, displayName: 'Ana Ruiz' },
      vote: 'down', excluded: false, at: now * 1000 })]);
    expect(body.attestations.map((a: any) => [a.contributor.displayName, a.counted, a.origin]).sort())
      .toEqual([['Jonas Lind', true, 'manual'], ['Mira Okafor', true, 'manual']]);
    expect(body.reports).toEqual([expect.objectContaining({ reportId: 'r_gore', note: 'Ends about four seconds early', state: 'open' })]);
    expect(body.moderation).toEqual([expect.objectContaining({ actionId: 'a4-vote', command: 'exclude_vote',
      voter: { contributorId: seed.ana.contributorId, displayName: 'Ana Ruiz' } })]);
  });
});

describe('community', () => {
  it('renders activity as labelled sentence parts and pages by time', async () => {
    const votes = await read('activity?kind=votes');
    expect(votes.items.map((event: any) => [event.verb, event.actor.displayName, event.filter.filterId])).toEqual([
      ['voted_down', 'Ana Ruiz', seed.gore], ['voted_up', 'Priya Raman', seed.gentle]]);
    const moderation = await read('activity?kind=moderation');
    expect(moderation.items.find((event: any) => event.id === 'm:a1-quarantine')).toEqual(expect.objectContaining({ verb: 'quarantine', actor: { type: 'operator', subject: 'operator-subject' },
      filter: expect.objectContaining({ filterId: seed.gentle }), item: expect.objectContaining({ title: 'Arrival Road' }) }));
    const reports = await read('activity?kind=reports');
    expect(reports.items.at(-1)).toMatchObject({ verb: 'reported', item: null, version: null,
      filter: { filterId: 'f_deleted', category: null }, detail: { reason: 'spam' } });
    for (let index = 0; index < 15; index++) await audit(`recompute-${index}`, 'recompute', 'recompute', (now - 1000 - index) * 1000);
    const first = await read('activity');
    expect(first.items).toHaveLength(20);
    const rest = await read(`activity?before=${encodeURIComponent(first.nextCursor)}`);
    const all = [...first.items, ...rest.items];
    expect(all).toHaveLength(29);
    expect(new Set(all.map((event: any) => event.id)).size).toBe(29);
    expect(all.map((event: any) => event.at)).toEqual([...all.map((event: any) => event.at)].sort((a, b) => b - a));
  });

  it('ranks contributors by accepted served filters with secondary metrics', async () => {
    const board = await read('contributors/leaderboard?period=all');
    expect(board).toMatchObject({ period: 'all', metric: 'accepted', total: 4, nextCursor: null });
    const row = (name: string) => board.items.find((item: any) => item.contributor.displayName === name);
    // The quarantined filter no longer counts; the remaining filter carries one down vote and one report.
    expect(row('Mira Okafor')).toMatchObject({ accepted: 1, uploads: 1, votesReceived: -1, votesCast: 0, reportsAgainst: 1 });
    expect(row('Ana Ruiz')).toMatchObject({ accepted: 0, uploads: 0, votesCast: 1, rank: 4 });
    expect((await read('contributors/leaderboard?metric=votes_cast')).items.slice(0, 2).map((item: any) => item.votesCast)).toEqual([1, 1]);
    const week = await read('contributors/leaderboard?period=7d&metric=uploads');
    expect(week.since).toBe((now - 7 * 86_400) * 1000);
  });

  it('profiles a contributor with installations, history, reports and usage', async () => {
    const ana = await read(`contributors/${seed.ana.contributorId}/profile`);
    expect(ana.identity).toMatchObject({ displayName: 'Ana Ruiz', state: 'active', quarantined: false,
      moderation: { resource: `contributor:${seed.ana.contributorId}`, revision: 0 } });
    expect(ana.installations).toEqual([expect.objectContaining({ installationId: seed.ana.installationId, keys: { active: 1, retired: 0, revoked: 0 },
      newestKey: expect.objectContaining({ keyId: seed.ana.keyId, state: 'active' }) })]);
    expect(ana.votes).toEqual([expect.objectContaining({ kind: 'cast', vote: 'down', filter: expect.objectContaining({ filterId: seed.gore }),
      item: expect.objectContaining({ title: 'Arrival Road' }) })]);
    expect(ana.reportsFiled.map((report: any) => [report.reportId, report.item?.title ?? null])).toEqual([['r_gore', 'Arrival Road'], ['r_missing', null]]);
    expect(ana.totals).toMatchObject({ votesCast: 1, votesUpShare: 0, reportsFiled: 2, uploads: 0 });
    expect(ana.usage).toMatchObject({ source: 'application_observed', storage: { bytes: 0, quotaBytes: 7_000_000 } });
    const mira = await read(`contributors/${seed.mira.contributorId}/profile`);
    expect(mira.uploads).toEqual([expect.objectContaining({ entries: 2, version: { versionId: seed.versionId, label: "Director's Cut" } })]);
    expect(mira.reportsAgainst).toEqual([expect.objectContaining({ reportId: 'r_gore', reporter: { contributorId: seed.ana.contributorId,
      displayName: 'Ana Ruiz' } })]);
    expect(mira.totals).toMatchObject({ uploads: 1, items: 1, accepted: 1 });
  });

  it('queues reports, aliases, candidates, quarantines and disputes with impact', async () => {
    const queue = await read('queue');
    const kinds = queue.items.map((item: any) => item.kind);
    expect(kinds.sort()).toEqual(['alias', 'disputed', 'quarantined', 'report', 'report', 'report', 'version']);
    const report = queue.items.find((item: any) => item.id === 'r_gore');
    expect(report).toMatchObject({ resource: 'report:r_gore', revision: 0, reason: 'inaccurate', quote: 'Ends about four seconds early',
      reporter: { displayName: 'Ana Ruiz' }, filter: { filterId: seed.gore }, item: { title: 'Arrival Road' },
      impact: { downvotes: 1, upvotes: 0, served: 1 } });
    expect(queue.items.find((item: any) => item.id === 'r_missing')).toMatchObject({ item: null, filter: { filterId: 'f_deleted' },
      title: { text: 'f_deleted' } });
    expect(queue.items.find((item: any) => item.kind === 'alias')).toMatchObject({ suggestion: { text: 'Blue Comet: Remastered' },
      reporter: { displayName: 'Priya Raman' }, item: { title: 'Blue Comet' }, impact: { versionsWithLabel: 1 } });
    expect(queue.items.find((item: any) => item.kind === 'version')).toMatchObject({ id: seed.cometVersionId,
      impact: { uploads: 1, contributors: 1 } });
    expect(queue.items.find((item: any) => item.kind === 'quarantined')).toMatchObject({ resource: `filter:${seed.gentle}`,
      at: (now - 60) * 1000, latestAction: { command: 'quarantine', reason: 'Seeded reason' } });
    expect((await read('queue?kind=disputed')).items).toEqual([expect.objectContaining({ id: seed.gore })]);
    await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 20)
      INSERT INTO versions (version_id, work_id, status, trust, revision, created_seq, created_at, label_text)
      SELECT printf('v_cand_%02d', i), 'w_untitled', 'candidate', 0, 1, 1000 + i, ? - i, printf('cut %02d', i) FROM n`).bind(now - 50_000).run();
    const versions = await read('queue?kind=versions');
    expect(versions.counts.versions).toBe(21);
    const tail = await read(`queue?kind=versions&after=${encodeURIComponent(versions.nextCursor)}`);
    expect(tail).toMatchObject({ nextCursor: null, items: [{ id: 'v_cand_20', item: { title: 'w_untitled' }, version: { label: 'cut 20' } }] });
  });

  it('searches titles, display names and exact IDs', async () => {
    expect((await read('search?q=Arrival')).items.map((item: any) => item.title)).toEqual(['Arrival Road']);
    expect((await read('search?q=ruiz')).contributors).toEqual([{ contributorId: seed.ana.contributorId, displayName: 'Ana Ruiz' }]);
    expect((await read('search?q=%25')).contributors).toEqual([]);
    expect((await read(`search?q=${seed.gore}`)).ids).toEqual([{ kind: 'filter', id: seed.gore,
      label: "violence › stabbing 00:20:00–00:20:10 · Arrival Road (1977) · Director's Cut" }]);
    expect((await read('search?q=r_gore')).ids).toEqual([{ kind: 'report', id: 'r_gore', label: expect.stringContaining('Report · violence') }]);
    expect((await read('search?q=paged')).items).toHaveLength(10);
  });

  it('pages the audit log newest first with readable resource labels and ID fallbacks', async () => {
    for (let index = 0; index < 3; index++) await audit(`old-${index}`, `version:v_gone_${index}`, 'confirm_version', 1000 + index,
      'denied', 'catalog_moderation_actions');
    const page = await read('audit');
    expect(page.items).toHaveLength(20);
    const label = (id: string) => [...page.items].find((item: any) => item.actionId === id)?.resourceLabel;
    expect(label('a2-missing-filter')).toMatchObject({ type: 'filter', id: 'f_deleted', text: 'f_deleted', item: null });
    expect(label('a3-missing-contributor')).toMatchObject({ contributor: { contributorId: 'c_gone', displayName: 'c_gone' } });
    expect(label('a4-vote').text).toBe("Vote by Ana Ruiz · violence › stabbing 00:20:00–00:20:10 · Arrival Road (1977) · Director's Cut");
    expect(label('a5-catalog')).toMatchObject({ type: 'version', item: { title: 'Blue Comet' } });
    const rest = await read(`audit?after=${encodeURIComponent(page.nextCursor)}`);
    const all = [...page.items, ...rest.items];
    expect(new Set(all.map((item: any) => item.actionId)).size).toBe(all.length);
    expect(all.at(-1)).toMatchObject({ actionId: 'old-0', resourceLabel: { id: 'v_gone_0', text: 'v_gone_0', version: { label: 'v_gone_0' } } });
    expect(all.map((item: any) => item.createdAt)).toEqual(all.map((item: any) => item.createdAt).sort((a: number, b: number) => b - a));
    expect(all.find((item: any) => item.command === 'recompute').resourceLabel).toMatchObject({ type: 'recompute', id: null, text: 'recompute' });
  });
});
