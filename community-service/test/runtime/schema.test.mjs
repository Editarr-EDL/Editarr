import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { initializeDatabase, openDatabase, upgradeDatabase, verifiedBackup, migrationsDirectory, SCHEMA_LINEAGE } from '../../src/runtime/schema.mjs';

const baseline = readFileSync(new URL('0001_master_baseline.sql', migrationsDirectory), 'utf8');
const MIGRATIONS = readdirSync(migrationsDirectory).filter(name => /^\d{4}_.+\.sql$/.test(name)).sort();
const CURRENT = MIGRATIONS.length;
const workspace = prefix => mkdtempSync(join(tmpdir(), prefix));

/** A migrations directory holding the first `count` real migrations plus an optional synthetic later one. */
function migrationsRoot(directory, count, sql = null) {
  const root = join(directory, `migrations-${count}${sql === null ? '' : '-later'}`);
  mkdirSync(root);
  for (const name of MIGRATIONS.slice(0, count)) copyFileSync(new URL(name, migrationsDirectory), join(root, name));
  if (sql !== null) writeFileSync(join(root, `${String(count + 1).padStart(4, '0')}_later.sql`), sql);
  return pathToFileURL(`${root}/`);
}

/** A migrations directory holding every real migration plus a synthetic later migration. */
const laterMigrations = (directory, sql) => migrationsRoot(directory, CURRENT, sql);

test('initialization is explicit, refuses overwrite and holds a single-writer lock', () => {
  const directory = workspace('community-schema-');
  const path = join(directory, 'database.sqlite');
  try {
    assert.throws(() => openDatabase(path), /database_missing/);
    initializeDatabase(path);
    assert.throws(() => initializeDatabase(path), /EEXIST/);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, CURRENT);
      assert.equal(database.connection.prepare('SELECT lineage FROM schema_lineage').get().lineage, SCHEMA_LINEAGE);
      assert.equal(database.connection.prepare("SELECT count(*) AS count FROM alias_spellings WHERE source = 'seed'").get().count, 19);
      const other = new DatabaseSync(path);
      try { assert.throws(() => other.exec('BEGIN IMMEDIATE'), /locked/); }
      finally { other.close(); }
    } finally { database.close(); }
    openDatabase(path).database.close();
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('the baseline contains only the kept and master-set tables', () => {
  const database = new DatabaseSync(':memory:');
  try {
    database.exec(baseline);
    const tables = database.prepare("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name").all().map(row => row.name);
    for (const removed of ['releases', 'contributions', 'contribution_revisions', 'artifact_payloads', 'import_receipts',
      'revision_votes', 'master_works', 'budget_policy', 'quota_charges']) assert.ok(!tables.includes(removed), removed);
    for (const kept of ['contributors', 'installation_keys', 'operations', 'request_nonces', 'moderation_actions', 'reports',
      'works', 'work_identifiers', 'versions', 'submissions', 'submission_entries', 'filter_registry', 'master_filters',
      'filter_attestations', 'filter_votes', 'master_revisions', 'master_revision_filters', 'download_receipts', 'sync_runs'])
      assert.ok(tables.includes(kept), kept);
    assert.throws(() => database.exec("UPDATE schema_lineage SET lineage = 'other'"), /CHECK|immutable_schema_lineage/);
    const columns = name => database.prepare(`SELECT name FROM pragma_index_info('${name}') ORDER BY seqno`).all().map(row => row.name);
    assert.deepEqual(columns('idx_filter_registry_version_page'), ['version_id', 'filter_id']);
    assert.deepEqual(columns('idx_submissions_version_page'), ['version_id', 'submission_id']);
  } finally { database.close(); }
});

test('read-only inspection refuses mutations and leaves database bytes unchanged', () => {
  const directory = workspace('community-readonly-');
  const path = join(directory, 'database.sqlite');
  try {
    initializeDatabase(path);
    const before = readFileSync(path);
    const { database, version } = openDatabase(path, undefined, false, { readOnly: true });
    try {
      assert.equal(version, CURRENT);
      assert.equal(database.connection.prepare('SELECT COUNT(*) AS count FROM operations').get().count, 0);
      assert.throws(() => database.connection.exec('CREATE TABLE forbidden (value TEXT)'), /readonly/);
    } finally { database.close(); }
    assert.deepEqual(readFileSync(path), before);
    const writer = openDatabase(path).database;
    try { assert.throws(() => openDatabase(path, undefined, false, { readOnly: true }), /locked/); }
    finally { writer.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('refuses an old-lineage store without stamping, upgrading or backing it up', async () => {
  const directory = workspace('community-lineage-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    const old = new DatabaseSync(path);
    old.exec(`CREATE TABLE contributors (contributor_id TEXT PRIMARY KEY, discord_user_id TEXT, display_name TEXT,
        state TEXT, created_at INTEGER);
      CREATE TABLE contribution_revisions (revision_id TEXT PRIMARY KEY);
      INSERT INTO contributors VALUES ('one', '12345678901234567', 'One', 'active', 1);
      PRAGMA user_version = 18;`);
    old.close();
    const before = readFileSync(path);
    for (const open of [() => openDatabase(path), () => openDatabase(path, undefined, false, { readOnly: true }),
      () => openDatabase(path, undefined, true)]) assert.throws(open, /unsupported_database_lineage/);
    await assert.rejects(upgradeDatabase(path, snapshot), /unsupported_database_lineage/);
    assert.equal(existsSync(snapshot), false);
    assert.throws(() => initializeDatabase(path), /EEXIST/);
    assert.deepEqual(readFileSync(path), before);
    const forged = new DatabaseSync(path);
    forged.exec("CREATE TABLE schema_lineage (lineage TEXT); INSERT INTO schema_lineage VALUES ('community-revisions-0')");
    forged.close();
    assert.throws(() => openDatabase(path), /unsupported_database_lineage/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('a current baseline is not upgraded, and unknown schemas and occupied backups are refused', async () => {
  const directory = workspace('community-unknown-');
  const path = join(directory, 'database.sqlite');
  try {
    initializeDatabase(path);
    await assert.rejects(upgradeDatabase(path, join(directory, 'backup.sqlite')), /database_already_current/);
    const { database, inventory } = openDatabase(path);
    try { await assert.rejects(verifiedBackup(database, path, inventory), /EEXIST/); }
    finally { database.close(); }
    const changed = new DatabaseSync(path);
    changed.exec('CREATE TABLE unknown (secret BLOB); INSERT INTO unknown VALUES (X\'FF\')');
    changed.close();
    assert.throws(() => openDatabase(path), /unknown_database_schema/);
    const preserved = new DatabaseSync(path, { readOnly: true });
    try { assert.deepEqual(preserved.prepare('SELECT secret FROM unknown').get().secret, new Uint8Array([255])); }
    finally { preserved.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('a later migration upgrades the baseline only after a verified backup and preserves data', async () => {
  const directory = workspace('community-upgrade-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path);
    const source = new DatabaseSync(path);
    source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('contributor', '12345678901234567', 'One Two Three', 'one two three', 1);
      INSERT INTO installations VALUES ('installation', 'contributor', 1);
      INSERT INTO installation_keys VALUES ('key', 'installation', 'Ed25519', X'00FF0180', 'active', 1, NULL);`);
    source.close();
    const root = laterMigrations(directory, 'CREATE TABLE later_feature (value TEXT);');
    assert.throws(() => openDatabase(path, root), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot, root);
    const { database, version } = openDatabase(path, root);
    try {
      assert.equal(version, CURRENT + 1);
      assert.deepEqual(database.connection.prepare('SELECT public_key FROM installation_keys').get().public_key, new Uint8Array([0, 255, 1, 128]));
    } finally { database.close(); }
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try { assert.equal(restored.prepare('PRAGMA user_version').get().user_version, CURRENT); }
    finally { restored.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('a failed later migration rolls back and keeps its recoverable backup', async () => {
  const directory = workspace('community-rollback-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path);
    const root = laterMigrations(directory, "CREATE TABLE partial (value TEXT); SELECT RAISE(ABORT, 'synthetic_failure');");
    await assert.rejects(upgradeDatabase(path, snapshot, root));
    for (const candidate of [path, snapshot]) {
      const restored = new DatabaseSync(candidate, { readOnly: true });
      try {
        assert.equal(restored.prepare('PRAGMA user_version').get().user_version, CURRENT);
        assert.equal(restored.prepare("SELECT count(*) AS count FROM sqlite_schema WHERE name = 'partial'").get().count, 0);
      } finally { restored.close(); }
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('0002 upgrades a baseline store to filter expressions after a verified backup and keeps its rows', async () => {
  const directory = workspace('community-expressions-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path, migrationsRoot(directory, 1));
    const source = new DatabaseSync(path);
    source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('contributor', '12345678901234567', 'One Two Three', 'one two three', 1);`);
    source.close();
    assert.throws(() => openDatabase(path), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, CURRENT);
      assert.equal(database.connection.prepare('SELECT display_name FROM contributors').get().display_name, 'One Two Three');
      const columns = table => database.connection.prepare(`SELECT name FROM pragma_table_info('${table}')`).all().map(row => row.name);
      assert.ok(columns('submission_entries').includes('expression'));
      assert.ok(columns('filter_registry').includes('incumbent_expression'));
      assert.ok(columns('master_filters').includes('expression'));
    } finally { database.close(); }
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try {
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 1);
      assert.equal(restored.prepare('SELECT count(*) AS count FROM contributors').get().count, 1);
    } finally { restored.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('0003 upgrades a version 2 store to index receipts by version after a verified backup', async () => {
  const directory = workspace('community-receipt-index-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path, migrationsRoot(directory, 2));
    const source = new DatabaseSync(path);
    source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('contributor', '12345678901234567', 'One Two Three', 'one two three', 1);`);
    source.close();
    assert.throws(() => openDatabase(path), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, CURRENT);
      assert.equal(database.connection.prepare('SELECT display_name FROM contributors').get().display_name, 'One Two Three');
      const columns = database.connection.prepare("SELECT name FROM pragma_index_info('idx_download_receipts_version') ORDER BY seqno")
        .all().map(row => row.name);
      assert.deepEqual(columns, ['version_id', 'revision', 'issued_at']);
    } finally { database.close(); }
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try {
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 2);
      assert.equal(restored.prepare('SELECT count(*) AS count FROM contributors').get().count, 1);
    } finally { restored.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('0004 upgrades a version 3 store to index retention scans after a verified backup', async () => {
  const directory = workspace('community-retention-index-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path, migrationsRoot(directory, 3));
    const source = new DatabaseSync(path);
    source.exec(`INSERT INTO works (work_id, kind, created_at) VALUES ('work', 'movie', 1);
      INSERT INTO versions (version_id, work_id, status, trust, revision, created_seq, created_at)
        VALUES ('version', 'work', 'confirmed', 0.5, 2, 1, 1);`);
    const insert = source.prepare(`INSERT INTO master_revisions (version_id, revision, snapshot_digest, generated_at, snapshot_bytes)
      VALUES ('version', ?, ?, 1, ?)`);
    insert.run(1, 'a'.repeat(64), null);
    insert.run(2, 'b'.repeat(64), new Uint8Array([1, 2]));
    source.close();
    assert.throws(() => openDatabase(path), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, CURRENT);
      const db = database.connection;
      const columns = name => db.prepare(`SELECT name FROM pragma_index_info('${name}') ORDER BY seqno`).all().map(row => row.name);
      assert.deepEqual(columns('idx_master_revisions_retained_bytes'), ['version_id', 'revision']);
      assert.deepEqual(columns('idx_sync_runs_expiry'), ['started_at']);
      assert.deepEqual(db.prepare(`SELECT revision, snapshot_digest, length(snapshot_bytes) AS size FROM master_revisions
        ORDER BY revision`).all().map(row => ({ ...row })), [
        { revision: 1, snapshot_digest: 'a'.repeat(64), size: null }, { revision: 2, snapshot_digest: 'b'.repeat(64), size: 2 }]);
      // Only revisions that still hold bytes are in the partial index the retention scan reads.
      assert.deepEqual(db.prepare(`SELECT revision FROM master_revisions INDEXED BY idx_master_revisions_retained_bytes
        WHERE snapshot_bytes IS NOT NULL`).all().map(row => row.revision), [2]);
    } finally { database.close(); }
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try {
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 3);
      assert.equal(restored.prepare('SELECT count(*) AS count FROM master_revisions').get().count, 2);
    } finally { restored.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

for (const from of [1, 2, 3, 4, 5]) test(`0006 upgrades a version ${from} store to operator insights after a verified backup`, async () => {
  const directory = workspace(`community-insights-${from}-`);
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path, migrationsRoot(directory, from));
    const source = new DatabaseSync(path);
    source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('voter', '12345678901234567', 'Voter', 'voter', 1), ('other', '12345678901234568', 'Other', 'other', 1);
      INSERT INTO works (work_id, kind, created_at) VALUES ('work', 'movie', 1);
      INSERT INTO versions (version_id, work_id, status, trust, revision, created_seq, created_at)
        VALUES ('version', 'work', 'confirmed', 0.5, 1, 1, 1);
      INSERT INTO master_revisions (version_id, revision, snapshot_digest, generated_at) VALUES ('version', 1, '${'a'.repeat(64)}', 1);
      INSERT INTO download_receipts VALUES ('${'b'.repeat(64)}', 'voter', 'version', 1, '${'a'.repeat(64)}', 'key', 5),
        ('${'c'.repeat(64)}', 'other', 'version', 1, '${'a'.repeat(64)}', 'key', 5);
      INSERT INTO filter_registry (filter_id, version_id, created_seq, state) VALUES ('f1', 'version', 1, 'active'), ('f2', 'version', 2, 'active');
      INSERT INTO filter_votes VALUES ('f1', 'voter', 'up', '${'b'.repeat(64)}', 0, 40), ('f2', 'other', 'down', '${'c'.repeat(64)}', 1, 30);
      INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision, reason, request_json,
        outcome, error_code, created_at) VALUES ('a1', 'operator', 'vote:f2:other', 'f2', 'exclude_vote', 0, 'Spam', '{}', 'applied', NULL, 9);`);
    if (from >= 5) source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, state, created_at)
        VALUES ('external-seed-videoskip', 'external-seed-videoskip', 'VideoSkip external archive', 'videoskip external archive', 'suspended', 1);
      INSERT INTO installations VALUES ('external-seed-videoskip', 'external-seed-videoskip', 1);
      INSERT INTO installation_keys VALUES ('sha256:seed', 'external-seed-videoskip', 'Ed25519', x'00', 'revoked', 1, 1);
      INSERT INTO version_acceptances (submission_id, version_id, contributor_id, accepted_seq, founder, duration_ms, accepted_at)
        VALUES ('seed_1', 'version', 'external-seed-videoskip', 1, 1, 1000, 1);
      INSERT INTO submissions (submission_id, digest, contributor_id, installation_id, key_id, version_id, accepted_seq,
        artifact_bytes, manifest_bytes, envelope_bytes, match_keys_json, state, created_at)
        VALUES ('seed_1', '${'d'.repeat(64)}', 'external-seed-videoskip', 'external-seed-videoskip', 'sha256:seed', 'version', 1,
        x'01', x'02', x'03', '[]', 'counted', 1);
      INSERT INTO external_seed_submissions VALUES ('seed_1', 'VideoSkip', 'videoskip:1', '${'e'.repeat(64)}', NULL, '{"a":1}');`);
    source.close();
    assert.throws(() => openDatabase(path), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, CURRENT);
      const db = database.connection, rows = sql => db.prepare(sql).all().map(row => ({ ...row }));
      assert.deepEqual(rows('SELECT filter_id, contributor_id, vote, kind, receipt_id, created_at FROM vote_events ORDER BY event_id'), [
        { filter_id: 'f2', contributor_id: 'other', vote: 'down', kind: 'cast', receipt_id: 'c'.repeat(64), created_at: 30 },
        { filter_id: 'f1', contributor_id: 'voter', vote: 'up', kind: 'cast', receipt_id: 'b'.repeat(64), created_at: 40 }]);
      assert.equal(db.prepare('SELECT count(*) AS n FROM filter_votes').get().n, 2);
      assert.deepEqual(rows('SELECT action_id, command, created_at FROM moderation_actions'), [{ action_id: 'a1', command: 'exclude_vote', created_at: 9 }]);
      assert.equal(db.prepare("SELECT revision FROM moderation_versions WHERE resource = 'vote:f2:other'").get().revision, 1);
      assert.throws(() => db.exec("DELETE FROM moderation_actions"), /immutable_audit/);
      assert.throws(() => db.exec("UPDATE vote_events SET vote = 'down'"), /immutable_vote_event/);
      const backupAudit = id => db.exec(`INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision,
        reason, request_json, outcome, error_code, created_at) VALUES ('${id}', 'operator', 'backup', 'config', 'backup_configure', 0, 'r', '{}', 'applied', NULL, 10)`);
      backupAudit('b1'); backupAudit('b2');
      assert.equal(db.prepare("SELECT count(*) AS n FROM moderation_versions WHERE resource = 'backup'").get().n, 0);
      assert.deepEqual({ ...db.prepare('SELECT enabled, frequency, at_utc, keep_last, keep_weekly FROM backup_config').get() },
        { enabled: 0, frequency: 'daily', at_utc: '03:30', keep_last: 14, keep_weekly: 8 });
      for (const table of ['service_metrics_hourly', 'usage_daily', 'maintenance_runs', 'backup_runs'])
        assert.equal(db.prepare(`SELECT count(*) AS n FROM ${table}`).get().n, 0);
      assert.deepEqual(rows('SELECT submission_id, source, source_record_id, source_runtime_ms, provenance_json FROM external_seed_submissions'),
        from >= 5 ? [{ submission_id: 'seed_1', source: 'VideoSkip', source_record_id: 'videoskip:1', source_runtime_ms: null,
          provenance_json: '{"a":1}' }] : []);
      if (from >= 5) assert.throws(() => db.exec('DELETE FROM external_seed_submissions'), /immutable_external_seed/);
    } finally { database.close(); }
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try {
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, from);
      assert.equal(restored.prepare('SELECT count(*) AS n FROM filter_votes').get().n, 2);
      assert.equal(restored.prepare("SELECT count(*) AS n FROM sqlite_schema WHERE name = 'vote_events'").get().n, 0);
    } finally { restored.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('expressions are stored only on language supports and hold 1–64 characters', () => {
  const database = new DatabaseSync(':memory:', { enableForeignKeyConstraints: false });
  try {
    for (const name of MIGRATIONS) database.exec(readFileSync(new URL(name, migrationsDirectory), 'utf8'));
    const insert = (entryId, kind, category, expression) => database.prepare(`INSERT INTO submission_entries (submission_id,
        entry_id, kind, start_ms, end_ms, category, origin, manual_review, locally_verified, expression)
      VALUES ('s', ?, ?, 0, 1, ?, 'manual', 1, 1, ?)`).run(entryId, kind, category, expression);
    insert('e1', 'support', 'language', 'damn');
    insert('e2', 'support', 'violence', null);
    assert.throws(() => insert('e3', 'support', 'violence', 'damn'), /CHECK/);
    assert.throws(() => insert('e4', 'dispute', 'language', 'damn'), /CHECK/);
    assert.throws(() => insert('e5', 'support', 'language', 'x'.repeat(65)), /CHECK/);
  } finally { database.close(); }
});

test('0005 upgrades a version 4 store with a verified backup and preserves its catalog', async () => {
  const directory = workspace('community-seed-upgrade-');
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    initializeDatabase(path, migrationsRoot(directory, 4));
    const previous = new DatabaseSync(path);
    previous.exec("INSERT INTO works (work_id, kind, catalog_seq, created_at) VALUES ('original', 'movie', 0, 1)");
    previous.close();
    assert.throws(() => openDatabase(path), /database_upgrade_required/);
    await upgradeDatabase(path, snapshot);
    const { database } = openDatabase(path);
    const restored = new DatabaseSync(snapshot, { readOnly: true });
    try {
      assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 4);
      assert.equal(database.connection.prepare('PRAGMA user_version').get().user_version, CURRENT);
      assert.deepEqual(restored.prepare('SELECT * FROM works').all(), database.connection.prepare('SELECT * FROM works').all());
      assert.equal(database.connection.prepare('SELECT count(*) AS count FROM external_seed_submissions').get().count, 0);
    } finally {
      restored.close();
      database.close();
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
