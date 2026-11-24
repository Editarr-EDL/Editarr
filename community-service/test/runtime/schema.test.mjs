import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { initializeDatabase, openDatabase, upgradeDatabase, verifiedBackup, migrationsDirectory } from '../../src/runtime/schema.mjs';

test('initialization is explicit, refuses overwrite and holds a single-writer lock', () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-schema-'));
  const path = join(directory, 'database.sqlite');
  try {
    assert.throws(() => openDatabase(path), /database_missing/);
    initializeDatabase(path);
    assert.throws(() => initializeDatabase(path), /EEXIST/);
    const { database, version } = openDatabase(path);
    try {
      assert.equal(version, 18);
      const other = new DatabaseSync(path);
      try { assert.throws(() => other.exec('BEGIN IMMEDIATE'), /locked/); }
      finally { other.close(); }
    } finally { database.close(); }
    openDatabase(path).database.close();
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('read-only inspection refuses mutations and leaves database bytes unchanged', () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-readonly-'));
  const path = join(directory, 'database.sqlite');
  try {
    initializeDatabase(path);
    const before = readFileSync(path);
    const { database, version } = openDatabase(path, undefined, false, { readOnly: true });
    try {
      assert.equal(version, 18);
      assert.equal(database.connection.prepare('SELECT COUNT(*) AS count FROM operations').get().count, 0);
      assert.throws(() => database.connection.exec('CREATE TABLE forbidden (value TEXT)'), /readonly/);
      assert.equal(database.connection.prepare('SELECT total_changes() AS count').get().count, 0);
    } finally { database.close(); }
    assert.deepEqual(readFileSync(path), before);
    const writer = openDatabase(path).database;
    try { assert.throws(() => openDatabase(path, undefined, false, { readOnly: true }), /locked/); }
    finally { writer.close(); }
    const previousPath = join(directory, 'previous.sqlite');
    const previous = new DatabaseSync(previousPath);
    for (const name of readdirSync(migrationsDirectory).filter(name => name.endsWith('.sql')).sort().slice(0, 16)) {
      previous.exec(readFileSync(new URL(name, migrationsDirectory), 'utf8'));
    }
    previous.exec('PRAGMA user_version = 16');
    previous.close();
    assert.throws(() => openDatabase(previousPath, undefined, false, { readOnly: true }), /database_upgrade_required/);
    const unknown = new DatabaseSync(path);
    unknown.exec('CREATE TABLE unknown (value TEXT)');
    unknown.close();
    assert.throws(() => openDatabase(path, undefined, false, { readOnly: true }), /unknown_database_schema/);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

for (let version = 1; version < 18; version++) {
  test(`upgrades version ${version} with a verified backup and preserved identity BLOBs`, async () => {
    const directory = mkdtempSync(join(tmpdir(), 'community-upgrade-'));
    const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
    try {
      const source = new DatabaseSync(path);
      for (const name of readdirSync(migrationsDirectory).filter(name => name.endsWith('.sql')).sort().slice(0, version)) {
        source.exec(readFileSync(new URL(name, migrationsDirectory), 'utf8'));
      }
      source.exec(`PRAGMA user_version = ${version}`);
      const columns = version >= 8 ? ', display_name_key' : '';
      const values = version >= 8 ? ", 'one two three'" : '';
      source.exec(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at${columns})
        VALUES ('contributor', '12345678901234567', 'One Two Three', 1${values});
        INSERT INTO installations VALUES ('installation', 'contributor', 1);
        INSERT INTO installation_keys VALUES ('key', 'installation', 'Ed25519', X'00FF0180', 'active', 1, NULL);`);
      source.close();
      assert.throws(() => openDatabase(path), /database_upgrade_required/);
      await upgradeDatabase(path, snapshot);
      const { database } = openDatabase(path);
      try {
        assert.deepEqual(database.connection.prepare('SELECT public_key FROM installation_keys').get().public_key, new Uint8Array([0, 255, 1, 128]));
        assert.equal(database.connection.prepare('SELECT display_name FROM contributors').get().display_name, 'One Two Three');
        assert.equal(database.connection.prepare("SELECT count(*) AS count FROM alias_spellings WHERE source = 'seed'").get().count, 19);
      } finally { database.close(); }
      const restored = new DatabaseSync(snapshot, { readOnly: true });
      try { assert.equal(restored.prepare('PRAGMA user_version').get().user_version, version); }
      finally { restored.close(); }
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });
}

test('refuses unknown schemas and occupied backup destinations without changing data', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-unknown-'));
  const path = join(directory, 'database.sqlite');
  try {
    initializeDatabase(path);
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

test('failed version-eight backfill rolls back the schema and preserves its recoverable backup', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-rollback-'));
  const path = join(directory, 'database.sqlite'), snapshot = join(directory, 'backup.sqlite');
  try {
    const source = new DatabaseSync(path);
    for (const name of readdirSync(migrationsDirectory).filter(name => name.endsWith('.sql')).sort().slice(0, 7)) {
      source.exec(readFileSync(new URL(name, migrationsDirectory), 'utf8'));
    }
    source.exec(`PRAGMA user_version = 7;
      INSERT INTO contributors VALUES ('one', 'one-discord', 'One Two Three', 'active', 1);
      INSERT INTO contributors VALUES ('two', 'two-discord', 'one two three', 'active', 1);`);
    source.close();
    await assert.rejects(upgradeDatabase(path, snapshot));
    for (const candidate of [path, snapshot]) {
      const restored = new DatabaseSync(candidate, { readOnly: true });
      try {
        assert.equal(restored.prepare('PRAGMA user_version').get().user_version, 7);
        assert.equal(restored.prepare('SELECT COUNT(*) AS count FROM contributors').get().count, 2);
        assert.equal(restored.prepare('PRAGMA table_info(contributors)').all().some(column => column.name === 'display_name_key'), false);
      } finally { restored.close(); }
    }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});