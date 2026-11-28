import { describe, expect, it } from 'vitest';
import { wilson } from '../src/versions/acceptance';
import { DEFAULT_MASTER_POLICY } from '../src/filters/policy';
import { recomputeVersion } from '../src/master/recompute';
import { loadVersionState } from '../src/master/state';
import { SqliteDatabase } from '../src/runtime/sqlite.mjs';
import { importExternalSeed } from '../src/seeds/service';
import { labelsEqual } from '../src/versions/labels';
import { readFileSync, readdirSync } from 'node:fs';

const migrations = new URL('../migrations/', import.meta.url);
const options = { service: 'https://example.test', policy: DEFAULT_MASTER_POLICY, now: 1795553648 };
const cue = { startMs: 1000, endMs: 3000, category: 'language', subcategory: 'swearing',
  severity: 'medium', action: 'mute', sourceCueIndex: 1 };

function bundle(source: 'VideoSkip' | 'Filmiway') {
  return { schemaVersion: 'editarr-community-seed-1', source, records: [{
    sourceRecordId: source.toLowerCase() + ':123', work: { kind: 'movie', tmdb: '603', tvdb: null,
      title: 'The Matrix', year: 1999, season: null, episode: null }, sourceRuntimeMs: source === 'Filmiway' ? 8_160_000 : null,
    provenance: { attribution: { source_url: 'https://example.test/archive' }, sync: null, match: { imdb: 'tt0133093' } },
    filters: [cue, { ...cue, sourceCueIndex: 2 }],
  }] };
}

function database() {
  const db = new SqliteDatabase(':memory:');
  for (const name of readdirSync(migrations).filter(name => name.endsWith('.sql')).sort()) {
    db.connection.exec(readFileSync(new URL(name, migrations), 'utf8'));
  }
  return db;
}

describe('operator external seed import', () => {
  it('uses the source label and one unit of support with no match keys or fabricated local verification', async () => {
    const db = database();
    try {
      for (const source of ['VideoSkip', 'Filmiway'] as const) {
        const payload = bundle(source);
        expect(await importExternalSeed(db, payload, options)).toEqual({ source, inserted: 1, unchanged: 0, filters: 1, duplicates: 1 });
        expect(await importExternalSeed(db, payload, options)).toMatchObject({ inserted: 0, unchanged: 1 });
      }
      const versions = db.connection.prepare('SELECT * FROM versions ORDER BY label_text').all();
      expect(versions.map(row => [row.label_text, row.status, row.runtime_ms]))
        .toEqual([['Filmiway', 'candidate', 8_160_000], ['VideoSkip', 'candidate', null]]);
      expect(new Set(versions.map(row => row.work_id)).size).toBe(1);
      for (const { version_id } of versions) {
        const state = (await loadVersionState(db, version_id as string))!;
        expect(labelsEqual(state, { aliasId: null, labelText: state.labelText!.toLowerCase() })).toBe(true);
        expect(state.referenceKeys).toEqual([]);
        expect(state.submissions[0]).toMatchObject({ externalSeed: true, entries: [{ locallyVerified: false }, { locallyVerified: false }] });
        const plan = await recomputeVersion(db, version_id as string, options);
        expect(plan.changed).toBe(false);
        expect(plan.result.filters[0]).toMatchObject({ support: { attestations: 1, upvotes: 0 }, confidence: wilson(1, 0) });
      }
      const keys = db.connection.prepare('SELECT state FROM installation_keys').all();
      expect(keys.map(row => row.state)).toEqual(['revoked', 'revoked']);
      const vote = db.connection.prepare('SELECT count(*) AS count FROM filter_votes').get();
      expect(vote?.count).toBe(0);
    } finally { db.close(); }
  });

  it('rejects changed records and uses the normal artifact taxonomy validator', async () => {
    const db = database();
    try {
      const payload = bundle('VideoSkip');
      const invalid = structuredClone(payload);
      invalid.records[0].filters[0].category = 'unknown';
      await expect(importExternalSeed(db, invalid, options)).rejects.toThrow('invalid_taxonomy');
      await importExternalSeed(db, payload, options);
      payload.records[0].filters[0].severity = 'low';
      await expect(importExternalSeed(db, payload, options)).rejects.toThrow('changed_external_seed_record');
    } finally { db.close(); }
  });
});
