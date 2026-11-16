import { readFileSync, readdirSync } from 'node:fs';
import { expect, it } from 'vitest';
import genericProvenance from '../migrations/0017_generic_provenance.sql?raw';
import { matchEvidenceSchema, withoutArrProvenance } from '../src/catalog/compatibility-inputs';
import { lookupEpisodes, lookupIdentifiers } from '../src/catalog/lookup';
import { env } from './helpers/environment';

const media = (entity: 'movie' | 'episode', identifiers: object[], series: object[], source: string) => ({
  media: { entity_type: entity, identifiers, series_identifiers: series,
    episode_numbers: entity === 'episode' ? [{ scheme: 'aired', season: 1, number: 2 }] : [], episode_span: [],
    release: { scene_name: null, group: null, edition: null, quality: null, size_bytes: null,
      duration: { seconds: 5400, source, precision_seconds: 60 }, audio: null },
    input_requirement: null },
  fingerprints: [], video_stream: null, audio_stream: null,
});
const releases = {
  movie: media('movie', [{ provider: 'tmdb', entity_type: 'movie', value: '603', provenance: 'radarr.movie' }], [],
    'radarr_catalog_minutes'),
  episode: media('episode', [], [{ provider: 'tvdb', entity_type: 'series', value: '81189', provenance: 'sonarr.series' }],
    'sonarr_catalog_minutes'),
  jellyfin: media('movie', [{ provider: 'tmdb', entity_type: 'movie', value: '604', provenance: 'user_confirmed' }], [],
    'jellyfin_ticks'),
};
const limits = { maxKeys: 8, maxResults: 8, maxQueries: 8 };
const tables = async () => {
  const db = env.CATALOG_DB;
  const names = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' ORDER BY name`).all<{ name: string }>()).results.map(item => item.name);
  return new Map(await Promise.all(names.map(async name =>
    [name, (await db.prepare(`SELECT * FROM "${name}"`).all()).results] as const)));
};

it('rewrites Radarr and Sonarr provenance in populated 0016 catalog rows and keeps every row and ID', async () => {
  const db = env.CATALOG_DB;
  const directory = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(directory).filter(file => /^00(0\d|1[0-6])_.*\.sql$/.test(file)).sort())
    await db.exec(readFileSync(new URL(file, directory), 'utf8'));
  await db.exec(`
    INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES ('author', 'author-discord', 'Author', 'author', 1);
    INSERT INTO installations VALUES ('install', 'author', 1);
    INSERT INTO installation_keys VALUES ('key', 'install', 'Ed25519', X'01', 'active', 1, NULL);
    INSERT INTO works VALUES ('movie-603', 'movie', 1), ('movie-604', 'movie', 1), ('series-81189', 'series', 1);
    INSERT INTO work_identifiers VALUES ('tmdb', 'movie', '603', 'movie-603', 'user_asserted', 'initial'),
      ('tmdb', 'movie', '604', 'movie-604', 'user_asserted', 'initial'),
      ('tvdb', 'series', '81189', 'series-81189', 'user_asserted', 'initial');
    INSERT INTO contributions VALUES ('contribution', 'author', 1);
  `);
  const works = { movie: 'movie-603', episode: 'series-81189', jellyfin: 'movie-604' };
  for (const [index, [name, evidence]] of Object.entries(releases).entries()) {
    const work = works[name as keyof typeof works], digest = String(index).repeat(64);
    await db.prepare('INSERT INTO releases VALUES (?, ?, ?, NULL, NULL, 5400, 1)').bind(name, work, JSON.stringify(evidence)).run();
    await db.prepare('INSERT INTO release_work_associations VALUES (?, ?)').bind(name, work).run();
    if (name === 'episode') await db.prepare(`INSERT INTO release_episodes VALUES (?, ?, 'aired', 1, 2, 'number', 0)`).bind(name, work).run();
    await db.prepare("INSERT INTO operations VALUES ('author', 'publication', ?, 'digest', 'completed', '{}', 1, 1)").bind(name).run();
    await db.prepare(`INSERT INTO contribution_revisions
      (revision_id, contribution_id, contributor_id, operation_kind, operation_id, key_id,
        release_id, kind, artifact_schema, artifact_digest, manifest_digest, envelope_digest,
        artifact_bytes, manifest_bytes, envelope_bytes, object_key, summary_json, total_events,
        audio_events, video_events, lineage_depth, parent_limit, created_at)
      VALUES (?, 'contribution', 'author', 'publication', ?, 'key', ?, 'scan', 'community-artifact-2', ?, ?, ?,
        1, 1, 1, ?, ?, 0, 0, 0, 0, 0, 1)`)
      .bind(`revision-${name}`, name, name, digest, digest, digest, `object-${name}`,
        JSON.stringify({ schema_version: 'community-summary-2', matching: evidence })).run();
    await db.prepare("INSERT INTO contribution_publications VALUES (?, 'reserved', NULL, 1)").bind(`revision-${name}`).run();
    await db.prepare('INSERT INTO contribution_statistics VALUES (?, 0, 0, 0)').bind(`revision-${name}`).run();
    await db.prepare("INSERT INTO artifact_payloads VALUES (?, X'01', X'02', X'03', ?, ?, ?)")
      .bind(`object-${name}`, digest, digest, digest).run();
    await db.prepare("UPDATE contribution_publications SET state = 'object_stored', object_etag = 'etag' WHERE revision_id = ?").bind(`revision-${name}`).run();
    await db.prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = ?").bind(`revision-${name}`).run();
  }
  const movieKeys = [{ provider: 'tmdb', entity_type: 'movie', value: '603', provenance: 'user_confirmed' } as const];
  const episodeKeys = [{ series: { provider: 'tvdb', entity_type: 'series', value: '81189', provenance: 'user_confirmed' } as const,
    number: { scheme: 'aired', season: 1, number: 2 } as const }];
  const revisions = async () => [...(await lookupIdentifiers(db, movieKeys, limits)), ...(await lookupEpisodes(db, episodeKeys, limits))]
    .map(row => row.revision_id);
  const lookedUp = await revisions();
  expect(lookedUp).toEqual(['revision-movie', 'revision-episode']);
  const before = await tables();

  await db.exec(genericProvenance);

  const after = await tables();
  expect([...after.keys()]).toEqual([...before.keys()]);
  for (const [name, rows] of before) {
    if (name === 'releases' || name === 'contribution_revisions') continue;
    expect(after.get(name), name).toEqual(rows);
  }
  const rewritten = (record: Record<string, unknown>) => withoutArrProvenance({ matching: record }).matching;
  expect(after.get('releases')).toEqual(before.get('releases')!.map(row => {
    const item = row as { evidence_json: string };
    return { ...item, evidence_json: JSON.stringify(rewritten(JSON.parse(item.evidence_json))) };
  }));
  expect(after.get('contribution_revisions')).toEqual(before.get('contribution_revisions')!.map(row => {
    const item = row as { summary_json: string };
    return { ...item, summary_json: JSON.stringify(withoutArrProvenance(JSON.parse(item.summary_json))) };
  }));
  const jellyfin = before.get('releases')!.find(row => (row as { release_id: string }).release_id === 'jellyfin');
  expect(after.get('releases')).toContainEqual(jellyfin);
  expect(JSON.stringify([...after.entries()])).not.toMatch(/radarr|sonarr/i);
  for (const row of after.get('releases')! as { evidence_json: string }[])
    expect(() => matchEvidenceSchema.parse(JSON.parse(row.evidence_json))).not.toThrow();
  expect(JSON.parse((after.get('releases')![0] as { evidence_json: string }).evidence_json).media).toMatchObject({
    identifiers: [{ provenance: 'source_metadata' }],
    release: { duration: { seconds: 5400, source: 'jellyfin_ticks', precision_seconds: 60 } },
  });
  expect(await revisions()).toEqual(lookedUp);
  await expect(db.prepare("UPDATE releases SET evidence_json = '{}'").run()).rejects.toThrow('immutable_release');
  await expect(db.prepare('UPDATE contribution_revisions SET total_events = 1').run()).rejects.toThrow('immutable_revision');
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
});
