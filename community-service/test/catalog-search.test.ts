import { beforeAll, expect, it } from 'vitest';
import { searchPlans, searchRows } from '../src/catalog/search-repository';
import { searchLimits, type SearchTarget } from '../src/catalog/search-input';
import { matchEvidenceSchema } from '../src/catalog/compatibility-inputs';
import { reserveRevision, transitionPublication } from '../src/contributions/repository';
import { db, fixtureOperation, limits, setupCatalog, verifiedCase } from './helpers/catalog';

export const caps = searchLimits({ requestBytes: 131072, responseBytes: 1048576, maxItems: 50,
  maxKeys: 300, maxQueries: 12, maxCandidates: 96, maxRows: 500, maxPageSize: 20,
  cacheEntries: 8, cacheBytes: 1048576, positiveTtlSeconds: 60, negativeTtlSeconds: 5 });
const evidence = matchEvidenceSchema.parse({ media: { entity_type: 'movie',
  identifiers: [{ provider: 'tmdb', entity_type: 'movie', value: '123', provenance: 'user_asserted' }], release: {} } });
beforeAll(setupCatalog);

it('batches 50 targets without N+1 and bounds every SQL statement', async () => {
  const targets = Array.from({ length: 50 }, (_, index) => ({ id: `target-${index}`, evidence }));
  const plans = searchPlans(targets, Number.MAX_SAFE_INTEGER, caps);
  expect(plans).toHaveLength(3);
  for (const plan of plans) {
    expect(plan.bindings.length).toBeLessThanOrEqual(100);
    const explanation = await db.prepare(`EXPLAIN QUERY PLAN ${plan.sql}`).bind(...plan.bindings).all<{ detail: string }>();
    expect(explanation.results.some(row => row.detail.includes('idx_release_work_associations_work'))).toBe(true);
  }
  expect(() => searchPlans(targets, 100, { ...caps, maxQueries: 2 })).toThrow('lookup_limit');
  expect(() => searchPlans(targets, 100, { ...caps, maxKeys: 49 })).toThrow('lookup_limit');
  expect(() => searchPlans([{ id: 'empty', evidence: matchEvidenceSchema.parse({ media: { entity_type: 'movie', identifiers: [], release: {} } }) }], 100, caps))
    .toThrow('missing_identity');
});

it('returns separate per-target published candidates and honors a snapshot ceiling', async () => {
  const verified = await verifiedCase('ai');
  const result = await reserveRevision(db, { verified, contributionId: 'search-family', releaseId: 'search-release', now: 2,
    limits, operation: await fixtureOperation('search-op') });
  const targets: SearchTarget[] = [{ id: 'first', evidence }, { id: 'second', evidence }];
  expect((await searchRows(db, targets, 100, caps)).map(item => item.rows.length)).toEqual([0, 0]);
  await transitionPublication(db, { revisionId: result.revisionId, from: 'reserved', to: 'object_stored', objectEtag: 'fixture', now: 3 });
  await transitionPublication(db, { revisionId: result.revisionId, from: 'object_stored', to: 'published', now: 4 });
  const rows = await searchRows(db, targets, 100, caps);
  expect(rows[0].rows[0].contribution_id).toBe('search-family');
  expect(rows[0].rows[0].release_id).toBe('search-release');
  expect(rows.map(item => item.rows.map(row => row.revision_id))).toEqual([[result.revisionId], [result.revisionId]]);
  expect((await searchRows(db, targets, 0, caps)).map(item => item.rows.length)).toEqual([0, 0]);
  await expect(searchRows(db, [...targets, { id: 'third', evidence }], 100, { ...caps, maxRows: 2 })).rejects.toThrow('lookup_limit');
});

it('keeps typed namespaces and channel/episode key plans indexed', async () => {
  const episode = matchEvidenceSchema.parse({ media: { entity_type: 'episode', identifiers: [],
    series_identifiers: [{ provider: 'tvdb', entity_type: 'series', value: '123', provenance: 'user_asserted' }],
    episode_numbers: [{ scheme: 'aired', season: 1, number: 2 }], release: {} },
    fingerprints: [{ algorithm: 'sha256-full-1', digest: 'a'.repeat(64), size_bytes: 100 }] });
  const plans = searchPlans([{ id: 'episode', evidence: episode }], 100, caps);
  expect(plans).toHaveLength(2);
  for (const [plan, index] of [[plans[0], 'idx_release_episodes_lookup'], [plans[1], 'idx_release_fingerprints_lookup']] as const) {
    const result = await db.prepare(`EXPLAIN QUERY PLAN ${plan.sql}`).bind(...plan.bindings).all<{ detail: string }>();
    expect(result.results.some(row => row.detail.includes(index))).toBe(true);
  }
  expect((await searchRows(db, [{ id: 'episode', evidence: episode }], 100, caps))[0].rows).toEqual([]);
});