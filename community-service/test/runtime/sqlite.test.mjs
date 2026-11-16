import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SqliteDatabase } from '../../src/runtime/sqlite.mjs';

test('batches roll back writes and trigger side effects together', async () => {
  const database = new SqliteDatabase(':memory:');
  try {
    database.connection.exec(`CREATE TABLE facts (id INTEGER PRIMARY KEY);
      CREATE TABLE audit (id INTEGER);
      CREATE TRIGGER added AFTER INSERT ON facts BEGIN INSERT INTO audit VALUES (new.id); END;`);
    await assert.rejects(database.batch([
      database.prepare('INSERT INTO facts VALUES (1)'), database.prepare('INSERT INTO facts VALUES (1)'),
    ]));
    assert.equal(await database.prepare('SELECT COUNT(*) AS count FROM facts').first('count'), 0);
    assert.equal(await database.prepare('SELECT COUNT(*) AS count FROM audit').first('count'), 0);
    const [result] = await database.batch([database.prepare('INSERT INTO facts VALUES (1) RETURNING id')]);
    assert.deepEqual(result.results, [{ id: 1 }]);
    assert.equal(result.meta.changes, 1);
    assert.equal(result.meta.rows_written, 2);
    assert.equal(result.meta.rows_read, null);
  } finally { database.close(); }
});

test('preserves exact ArrayBuffer BLOBs and immutable bound statements', async () => {
  const database = new SqliteDatabase(':memory:');
  try {
    database.connection.exec('CREATE TABLE payloads (id INTEGER PRIMARY KEY, bytes BLOB NOT NULL)');
    const bytes = new Uint8Array([0, 255, 1, 128]);
    const insert = database.prepare('INSERT INTO payloads VALUES (?, ?)');
    await database.batch([insert.bind(1, bytes.buffer), insert.bind(2, bytes)]);
    const stored = await database.prepare('SELECT bytes FROM payloads WHERE id = ?').bind(1).first('bytes');
    assert.ok(stored instanceof ArrayBuffer);
    assert.deepEqual(new Uint8Array(stored), bytes);
    assert.equal(await database.prepare('SELECT id FROM payloads WHERE id = 99').first(), null);
    await assert.rejects(database.prepare('SELECT id FROM payloads').first('missing'));
  } finally { database.close(); }
});

test('enforces foreign keys, safe integer results and connection ownership', async () => {
  const database = new SqliteDatabase(':memory:');
  const other = new SqliteDatabase(':memory:');
  try {
    database.connection.exec('CREATE TABLE parents (id INTEGER PRIMARY KEY); CREATE TABLE children (id INTEGER REFERENCES parents)');
    await assert.rejects(database.prepare('INSERT INTO children VALUES (1)').run());
    await assert.rejects(database.prepare('SELECT 9223372036854775807 AS id').first(), /unsafe_sqlite_integer/);
    await assert.rejects(database.batch([other.prepare('SELECT 1')]), /invalid_sqlite_batch/);
    assert.throws(() => database.prepare('SELECT ?').bind(undefined), /invalid_sqlite_parameter/);
  } finally { database.close(); other.close(); }
});