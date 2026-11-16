import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

const root = fileURLToPath(new URL('../', import.meta.url));
const rowCount = Number(process.argv[2]);
assert(Number.isSafeInteger(rowCount) && rowCount > 0 && rowCount <= 10000,
  'Provide an explicit synthetic revision count from 1 to 10000');
const vectors = JSON.parse(readFileSync(join(root, '../tests/fixtures/community/donation-artifacts-v2.json'), 'utf8')).cases;
const names = Object.keys(vectors);
const directory = mkdtempSync(join(tmpdir(), 'samwise-catalog-measure-'));
const db = new DatabaseSync(join(directory, 'synthetic.sqlite'));
try {
  db.exec('PRAGMA foreign_keys=ON');
  for (const name of readdirSync(join(root, 'migrations')).filter(name => /^000[1-8]_.*\.sql$/.test(name)).sort())
    db.exec(readFileSync(join(root, 'migrations', name), 'utf8'));
  db.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, state, created_at)
    VALUES ('measure-author', 'synthetic-discord', 'Synthetic Name', 'synthetic name', 'active', 1);
    INSERT INTO installations VALUES ('measure-installation', 'measure-author', 1);`);
  db.prepare(`INSERT INTO installation_keys VALUES ('measure-key', 'measure-installation', 'Ed25519', ?, 'active', 1, NULL)`).run(Buffer.alloc(32));
  const backup = join(directory, 'recoverable-pre-0009.sqlite');
  db.prepare('VACUUM INTO ?').run(backup);
  const recovered = new DatabaseSync(backup, { readOnly: true });
  assert.equal(Object.values(recovered.prepare('PRAGMA integrity_check').get())[0], 'ok');
  for (const { name } of db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'`).all())
    assert.deepEqual(recovered.prepare(`SELECT * FROM "${name}"`).all(), db.prepare(`SELECT * FROM "${name}"`).all());
  assert.equal(recovered.prepare('PRAGMA foreign_key_check').all().length, 0);
  recovered.close();
  db.exec('BEGIN');
  db.exec(readFileSync(join(root, 'migrations/0009_catalog.sql'), 'utf8'));
  db.exec('COMMIT');
  const before = Number(db.prepare('PRAGMA page_count').get().page_count) * Number(db.prepare('PRAGMA page_size').get().page_size);
  const work = db.prepare(`INSERT INTO works VALUES (?, 'movie', 1) ON CONFLICT DO NOTHING`);
  const identity = db.prepare(`INSERT INTO work_identifiers VALUES ('tmdb', 'movie', ?, ?, 'user_asserted', 'initial') ON CONFLICT DO NOTHING`);
  const release = db.prepare(`INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, 1)`);
  const association = db.prepare(`INSERT INTO release_work_associations VALUES (?, ?)`);
  const fingerprint = db.prepare(`INSERT INTO release_fingerprints VALUES (?, 'file', 'sha256-full-1', ?, 1000)`);
  const contribution = db.prepare(`INSERT INTO contributions VALUES (?, 'measure-author', 1)`);
  const operation = db.prepare(`INSERT INTO operations VALUES ('measure-author', 'synthetic-publication', ?, 'synthetic-digest', 'pending', NULL, 1, 1)`);
  const revision = db.prepare(`INSERT INTO contribution_revisions (revision_id, contribution_id, contributor_id, key_id, release_id,
    kind, artifact_schema, artifact_digest, manifest_digest, envelope_digest, object_key, summary_json,
    total_events, audio_events, video_events, lineage_depth, parent_limit, created_at, artifact_bytes, manifest_bytes, envelope_bytes, operation_kind, operation_id)
    VALUES (?, ?, 'measure-author', 'measure-key', ?, ?, 'community-artifact-2', ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 1, ?, ?, ?, 'synthetic-publication', ?)`);
  const publication = db.prepare(`INSERT INTO contribution_publications VALUES (?, 'reserved', NULL, 1)`);
  const stats = db.prepare(`INSERT INTO contribution_statistics (revision_id) VALUES (?)`);
  let jsonBytes = 0;
  db.exec('BEGIN');
  let parentEdges = 0;
  for (let index = -1; index < rowCount; index++) {
    const name = index === -1 ? 'zero' : names[index % names.length];
    const item = vectors[name];
    const artifact = JSON.parse(gunzipSync(Buffer.from(item.artifact, 'base64')));
    const summary = JSON.parse(Buffer.from(item.manifest, 'base64')).summary;
    const value = String(1000 + Math.floor(index / 10));
    const workId = `tmdb:movie:${value}`, id = `synthetic-${index}`;
    work.run(workId); identity.run(value, workId);
    const evidence = JSON.stringify(artifact.matching), summaryJson = JSON.stringify(summary);
    jsonBytes += Buffer.byteLength(evidence) + Buffer.byteLength(summaryJson);
    release.run(id, workId, evidence, 'same-nonunique-scene', null, artifact.media.duration_seconds);
    association.run(id, workId); fingerprint.run(id, 'a'.repeat(64)); contribution.run(id); operation.run(id);
    revision.run(id, id, id, artifact.kind, 'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64),
      `artifacts/${id}`, summaryJson, summary.total, summary.channels.audio ?? 0, summary.channels.video ?? 0, name === 'imported' ? 1 : 0,
      Buffer.from(item.artifact, 'base64').byteLength, Buffer.from(item.manifest, 'base64').byteLength, Buffer.from(item.envelope, 'base64').byteLength, id);
    publication.run(id); stats.run(id);
    if (index === -1) db.prepare(`UPDATE contribution_publications SET state = 'object_stored', object_etag = 'synthetic-root', updated_at = 2 WHERE revision_id = ?`).run(id);
    if (index === -1) db.prepare(`UPDATE contribution_publications SET state = 'published', updated_at = 3 WHERE revision_id = ?`).run(id);
    if (name === 'imported') {
      db.prepare(`INSERT INTO contribution_parents VALUES (?, 'synthetic--1', ?)`).run(id, 'a'.repeat(64));
      parentEdges++;
    }
  }
  db.exec('COMMIT');
  assert.equal(db.prepare('PRAGMA foreign_key_check').all().length, 0);
  const pageSize = Number(db.prepare('PRAGMA page_size').get().page_size);
  const totalBytes = Number(db.prepare('PRAGMA page_count').get().page_count) * pageSize;
  const objects = db.prepare(`SELECT s.name, s.type, sum(d.pgsize) AS bytes FROM sqlite_schema s
    JOIN dbstat d ON d.name = s.name WHERE s.name NOT LIKE 'sqlite_%' GROUP BY s.name, s.type`).all();
  const catalogTables = ['operations', 'works', 'work_identifiers', 'releases', 'release_work_associations', 'release_episodes',
    'release_fingerprints', 'contributions', 'contribution_revisions', 'contribution_publications', 'contribution_statistics', 'contribution_parents'];
  const catalogObjects = db.prepare(`SELECT name, type FROM sqlite_schema WHERE tbl_name IN (${catalogTables.map(() => '?').join(',')}) AND type IN ('table', 'index')`).all(...catalogTables);
  const sizes = db.prepare('SELECT name, sum(pgsize) AS bytes FROM dbstat GROUP BY name').all();
  const namesByType = type => new Set(catalogObjects.filter(item => item.type === type).map(item => item.name));
  const sum = type => sizes.filter(item => namesByType(type).has(item.name)).reduce((total, item) => total + Number(item.bytes), 0);
  const plans = {
    identity: db.prepare(`EXPLAIN QUERY PLAN SELECT work_id FROM work_identifiers WHERE provider = ? AND entity_type = ? AND value = ?`).all('tmdb', 'movie', '1000'),
    fingerprint: db.prepare(`EXPLAIN QUERY PLAN SELECT release_id FROM release_fingerprints WHERE channel = ? AND algorithm = ? AND digest = ?`).all('file', 'sha256-full-1', 'a'.repeat(64)),
  };
  const binds = {};
  for (const count of [100, 101]) {
    try { db.prepare(`SELECT ${Array(count).fill('?').join('+')} AS total`).get(...Array(count).fill(1)); binds[count] = 'accepted_by_local_sqlite'; }
    catch (error) { binds[count] = error.message; }
  }
  console.log(JSON.stringify({ scope: 'temporary synthetic Node SQLite; not hosted D1', revisions: rowCount,
    mixedCases: names, syntheticRootRevisions: 1, parentEdges, backupVerifiedBefore0009: true, databaseBytes: totalBytes, growthBytes: totalBytes - before,
    catalogTableBytes: sum('table'), catalogIndexBytes: sum('index'), indexToTableRatio: sum('index') / sum('table'),
    publicJsonBytes: jsonBytes, bytesPerRevisionIncludingIndexes: (sum('table') + sum('index')) / rowCount,
    objects, plans, bindProbe: binds, hostedParameterLimit: 100 }, null, 2));
} finally {
  db.close();
  rmSync(directory, { recursive: true, force: true });
}