import { beforeAll, describe, expect, it, vi } from 'vitest';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { contributorVersionCatalog } from '../src/http/match';
import type { VersionAcceptance } from '../src/versions/acceptance-service';
import service from '../src/index';
import { registerKey, signedPost, signedRead, type SigningKey } from './helpers/key-lifecycle';
import { acceptPlanned, db, keys, newId, now, policy, setupVersionCatalog, submission } from './helpers/versions';

let key: SigningKey & { discordId: string }, theatrical: VersionAcceptance, extended: VersionAcceptance, candidate: VersionAcceptance;
const eligible = async () => 'eligible' as const;
const handle = (request: Request, membership: () => Promise<'eligible' | 'ineligible' | 'unavailable'> = eligible) =>
  contributorVersionCatalog(request, db, 'community.example', now, membership);
const match = async (body: unknown) => handle(await signedPost(key, '/v1/match', JSON.stringify(body), crypto.randomUUID()));
const query = (overrides: Record<string, unknown> = {}) => ({ version: 1, keys: [keys.md5('a'),
  { type: 'tmdb', kind: 'movie', id: '603' }, { type: 'title_year', title: 'THE MATRIX', year: 1999 }],
  label: { aliasId: 'theatrical' }, runtimeMs: 8_160_512, ...overrides });
type Results = { results: { versionId: string; tier: string; matchPercent: number; matchedKeys: string[] }[] };

beforeAll(async () => {
  await setupVersionCatalog();
  key = await registerKey(db);
  const accept = (overrides: Parameters<typeof submission>[0]) => acceptPlanned(submission(overrides), { policy, now, newId });
  theatrical = await accept({ matchKeys: [keys.md5('a')] });
  extended = await accept({ contributorId: 'c2', version: { choice: 'alias', aliasId: 'extended' }, durationMs: 8_400_000,
    matchKeys: [keys.md5('b')] });
  candidate = await accept({ contributorId: 'c3', durationMs: 8_161_000, matchKeys: [keys.md5('c')] });
});

describe('POST /v1/match', () => {
  it('scores, orders and describes every version that shares a key', async () => {
    const response = await match(query());
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
    const body = await response.json() as Results;
    expect(body.results.map(item => [item.versionId, item.tier, item.matchPercent, item.matchedKeys])).toEqual([
      [theatrical.versionId, '2', 80, ['md5_quick']],
      [candidate.versionId, '4', 30, ['tmdb']],
      [extended.versionId, 'other_version', 0, ['tmdb', 'title_year']],
    ]);
    expect(body.results[0]).toEqual({ versionId: theatrical.versionId, status: 'confirmed', aliasId: 'theatrical',
      label: 'Theatrical', labelText: null, work: { workId: theatrical.workId, kind: 'movie', tmdb: '603', tvdb: null,
        season: null, episode: null, lastEpisode: null, title: 'The Matrix', year: 1999 },
      tier: '2', matchPercent: 80, matchedKeys: ['md5_quick'], runtimeAgreement: 'not_applicable', runtimeMs: 8_160_000,
      trust: 0.207654955, revision: 1, filterCount: 0, contributorCount: 1 });
  });

  it('keeps the best tier despite mismatching keys and drops outside runtimes to 5%', async () => {
    const mixed = await (await match(query({ keys: [keys.full('f'), keys.md5('a')] }))).json() as Results;
    expect(mixed.results.map(item => [item.versionId, item.tier])).toEqual([[theatrical.versionId, '2']]);
    const outside = await (await match(query({ keys: [{ type: 'tmdb', kind: 'movie', id: '603' }], runtimeMs: 9_000_000 }))).json() as Results;
    expect(outside.results.map(item => [item.versionId, item.tier, item.matchPercent])).toEqual([
      [theatrical.versionId, 'runtime_mismatch', 5], [candidate.versionId, 'runtime_mismatch', 5],
      [extended.versionId, 'other_version', 0]]);
    const text = await (await match(query({ keys: [{ type: 'tmdb', kind: 'movie', id: '603' }],
      label: { text: 'Extended Cut' }, runtimeMs: 8_401_000, limit: 1 }))).json() as Results;
    expect(text.results.map(item => [item.versionId, item.tier])).toEqual([[extended.versionId, '4']]);
  });

  it('ignores null-year titles, merged and quarantined versions', async () => {
    const titleOnly = await (await match(query({ keys: [{ type: 'title_year', title: 'The Matrix', year: null }] }))).json();
    expect(titleOnly).toEqual({ version: 1, results: [] });
    await db.prepare('UPDATE versions SET quarantined = 1 WHERE version_id = ?').bind(candidate.versionId).run();
    try {
      const body = await (await match(query({ keys: [{ type: 'title_year', title: 'the matrix', year: 1999 }] }))).json() as Results;
      expect(body.results.map(item => [item.versionId, item.tier])).toEqual([[theatrical.versionId, '5'],
        [extended.versionId, 'other_version']]);
    } finally { await db.prepare('UPDATE versions SET quarantined = 0 WHERE version_id = ?').bind(candidate.versionId).run(); }
  });

  it('rejects invalid requests and reports common failures', async () => {
    for (const body of [query({ keys: [keys.md5('a'), keys.md5('b'), keys.md5('c')] }), query({ keys: [] }), query({ extra: 1 }),
      query({ limit: 51 }), query({ runtimeMs: 0 }), query({ label: { aliasId: 'Bad' } }),
      query({ keys: [{ type: 'title_year', title: '!!', year: 1999 }] }), { ...query(), runtimeMs: undefined }]) {
      const response = await match(body);
      expect([response.status, await response.json()]).toEqual([400, { error: { code: 'invalid_match' } }]);
    }
    const denied = await handle(await signedPost(key, '/v1/match', JSON.stringify(query()), crypto.randomUUID()), async () => 'ineligible');
    expect([denied.status, await denied.json()]).toEqual([403, { error: { code: 'membership_ineligible' } }]);
    expect((await handle(await signedRead(key, '/v1/match'))).status).toBe(405);
  });
});

describe('GET /v1/aliases', () => {
  it('lists approved aliases with theatrical first, then by label', async () => {
    const response = await handle(await signedRead(key, '/v1/aliases'));
    const body = await response.json() as { aliases: { aliasId: string; label: string; spellings: string[] }[] };
    expect(body.aliases.map(alias => [alias.aliasId, alias.label])).toEqual([['theatrical', 'Theatrical'],
      ['directors_cut', "Director's Cut"], ['extended', 'Extended Edition'], ['unrated', 'Unrated']]);
    expect(body.aliases[1].spellings).toEqual(['directors cut', 'directors edition', 'directors version']);
    expect(body.aliases[0].spellings).not.toContain('');
  });

  it('is routed by the service entry point behind contributor admission', async () => {
    const timestamp = Math.floor(Date.now() / 1000);
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ user: { id: key.discordId } })));
    const env = { APP_ENV: 'local', CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
      TRUSTED_ACTOR_KEY: tunnelActor, DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token' };
    const edge = async (path: string) => {
      const signed = await signedRead(key, path, timestamp), headers = new Headers(signed.headers);
      headers.set('CF-Connecting-IP', '192.0.2.1');
      return new Request(signed, { headers });
    };
    try {
      const routed = await service.fetch(await edge('/v1/aliases'), env);
      expect(routed.status).toBe(200);
      expect(await routed.json()).toMatchObject({ version: 1, aliases: [{ aliasId: 'theatrical' }, {}, {}, {}] });
      const misconfigured = await service.fetch(await edge('/v1/aliases'), { ...env, PUBLIC_ORIGIN: undefined });
      expect([misconfigured.status, await misconfigured.json()]).toEqual([503, { error: { code: 'service_unavailable' } }]);
    } finally { vi.unstubAllGlobals(); }
  });
});
