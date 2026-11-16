import { beforeAll, expect, it } from 'vitest';
import { episodeQuery, fingerprintQuery, identifierQuery, lookupIdentifiers, lookupEpisodes, lookupFingerprints } from '../src/catalog/lookup';
import { matchEvidenceSchema } from '../src/catalog/compatibility-inputs';
import { releaseStatements } from '../src/catalog/releases';
import { reserveRevision, transitionPublication } from '../src/contributions/repository';
import { db, fixtureOperation, limits, rawRevision, setupCatalog, verifiedCase } from './helpers/catalog';

const identifier = { provider: 'tmdb', entity_type: 'movie', value: '123', provenance: 'signed_claim' } as const;
const lookupLimits = { maxKeys: 100, maxResults: 5, maxQueries: 4 };
beforeAll(setupCatalog);

it('returns published claims only and chunks/deduplicates a 100-parameter boundary', async () => {
  for (const name of ['ai', 'zero'] as const) {
    const result = await reserveRevision(db, { verified: await verifiedCase(name), contributionId: `family-${name}`, releaseId: `release-${name}`, now: 2, limits, operation: await fixtureOperation(`op-${name}`) });
    if (name === 'ai') {
      await transitionPublication(db, { revisionId: result.revisionId, from: 'reserved', to: 'object_stored', objectEtag: 'fixture', now: 3 });
      await transitionPublication(db, { revisionId: result.revisionId, from: 'object_stored', to: 'published', now: 4 });
    }
  }
  const keys = Array.from({ length: 70 }, () => identifier);
  expect((await lookupIdentifiers(db, keys, lookupLimits)).map(item => item.revision_id)).toEqual(['submission-ai']);
  expect(identifierQuery(keys.slice(0, 32), 5).bindings).toHaveLength(98);
  expect(() => identifierQuery(keys.slice(0, 33), 5)).toThrow('invalid_lookup_batch');
  const page = await lookupIdentifiers(db, [identifier], lookupLimits);
  expect(await lookupIdentifiers(db, [identifier], { ...lookupLimits, beforeSequence: page[0].revision_sequence })).toEqual([]);
  await expect(lookupIdentifiers(db, keys, { ...lookupLimits, maxQueries: 1 })).rejects.toThrow('catalog_lookup_limit');
  expect(await lookupIdentifiers(db, [], lookupLimits)).toEqual([]);
  const plan = identifierQuery([identifier], 5);
  const explain = await db.prepare(`EXPLAIN QUERY PLAN ${plan.sql}`).bind(...plan.bindings).all<{ detail: string }>();
  expect(explain.results.some(item => item.detail.includes('idx_release_work_associations_work'))).toBe(true);
  await transitionPublication(db, { revisionId: 'submission-ai', from: 'published', to: 'quarantined', now: 5 });
  expect(await lookupIdentifiers(db, [identifier], lookupLimits)).toEqual([]);
});

it('indexes known fingerprints and does not collapse different episode numbering schemes', async () => {
  const series = { ...identifier, entity_type: 'series', value: '45' } as const;
  const number = { scheme: 'aired', season: 0, number: 2 } as const;
  const evidence = matchEvidenceSchema.parse({ media: { entity_type: 'episode', identifiers: [],
    series_identifiers: [series], episode_numbers: [number], episode_span: [number, { ...number, number: 3 }], release: {} },
    fingerprints: [{ algorithm: 'sha256-full-1', digest: 'a'.repeat(64), size_bytes: 100 }] });
  const release = await releaseStatements(db, 'release-episode', evidence, 6, limits);
  await db.batch(release.statements);
  const fingerprint = { channel: 'file', algorithm: 'sha256-full-1', digest: 'a'.repeat(64) } as const;
  for (const [plan, index] of [[fingerprintQuery([fingerprint], 5), 'idx_release_fingerprints_lookup'],
    [episodeQuery([{ series, number }], 5), 'idx_release_episodes_lookup']] as const) {
    const explain = await db.prepare(`EXPLAIN QUERY PLAN ${plan.sql}`).bind(...plan.bindings).all<{ detail: string }>();
    expect(explain.results.some(item => item.detail.includes(index))).toBe(true);
  }
  expect(await lookupFingerprints(db, [fingerprint], lookupLimits)).toEqual([]);
  expect(await lookupEpisodes(db, [{ series, number }], lookupLimits)).toEqual([]);
  expect(release.workId).not.toBe('tmdb:series:45');
  await rawRevision('episode-revision', release.releaseId);
  await transitionPublication(db, { revisionId: 'episode-revision', from: 'reserved', to: 'object_stored', objectEtag: 'episode-fixture', now: 7 });
  await transitionPublication(db, { revisionId: 'episode-revision', from: 'object_stored', to: 'published', now: 8 });
  expect((await lookupFingerprints(db, [fingerprint], lookupLimits)).map(item => item.revision_id)).toEqual(['episode-revision']);
  expect((await lookupEpisodes(db, [{ series, number: { ...number, number: 3 } }], lookupLimits)).map(item => item.revision_id)).toEqual(['episode-revision']);
  expect(await lookupEpisodes(db, [{ series, number: { scheme: 'absolute', season: null, number: 2 } }], lookupLimits)).toEqual([]);
  await expect(db.prepare(`INSERT INTO release_fingerprints VALUES (?, 'video', 'new', 'a', NULL)`).bind(release.releaseId).run()).rejects.toThrow('sealed_release');
});