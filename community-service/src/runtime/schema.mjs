import { DatabaseSync, backup } from 'node:sqlite';
import { readFileSync, readdirSync, openSync, closeSync, chmodSync, existsSync, lstatSync, fsyncSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { SqliteDatabase } from './sqlite.mjs';

export const migrationsDirectory = new URL('../../migrations/', import.meta.url);
/** Schema lineage of the rebased master-set baseline; stores from any other lineage are refused. */
export const SCHEMA_LINEAGE = 'community-master-1';
/** Refusal codes that are safe to print, so operators see why a store was not opened. */
export const REPORTED_REFUSALS = new Set(['unsupported_database_lineage', 'database_upgrade_required',
  'unknown_database_schema', 'unknown_database_version', 'database_missing_or_symlink', 'database_integrity_failed']);

function migrations(root) {
  const names = readdirSync(root).filter(name => /^\d{4}_.+\.sql$/.test(name)).sort();
  if (!names.length || names.some((name, index) => Number(name.slice(0, 4)) !== index + 1)) throw new Error('invalid_migration_inventory');
  return names.map(name => ({ name, sql: readFileSync(new URL(name, root), 'utf8') }));
}

function schema(connection) {
  return connection.prepare("SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name").all();
}

function verifyIntegrity(connection) {
  if (connection.prepare('PRAGMA integrity_check').get().integrity_check !== 'ok' ||
      connection.prepare('PRAGMA foreign_key_check').all().length) throw new Error('database_integrity_failed');
}

/**
 * Refuse a store from another schema lineage (for example the pre-rebase revision schema)
 * before comparing versions. Such a store is never stamped or upgraded; archive it instead.
 */
function verifyLineage(connection) {
  let rows = [];
  try { rows = connection.prepare('SELECT lineage FROM schema_lineage').all(); }
  catch (error) {
    if (!/no such (table|column)/.test(String(error?.message))) throw error;
  }
  if (rows.length !== 1 || rows[0].lineage !== SCHEMA_LINEAGE) throw new Error('unsupported_database_lineage');
}

function verifySchema(connection, inventory) {
  verifyLineage(connection);
  const version = connection.prepare('PRAGMA user_version').get().user_version;
  if (!Number.isSafeInteger(version) || version < 1 || version > inventory.length) throw new Error('unknown_database_version');
  const expected = new DatabaseSync(':memory:');
  try {
    for (const item of inventory.slice(0, version)) expected.exec(item.sql);
    if (!isDeepStrictEqual(schema(connection), schema(expected))) throw new Error('unknown_database_schema');
  } finally { expected.close(); }
  verifyIntegrity(connection);
  return version;
}

export function initializeDatabase(path, root = migrationsDirectory) {
  const inventory = migrations(root);
  const descriptor = openSync(path, 'wx', 0o600);
  closeSync(descriptor);
  const database = new SqliteDatabase(path);
  try {
    database.connection.exec('BEGIN IMMEDIATE');
    for (const item of inventory) database.connection.exec(item.sql);
    database.connection.exec(`PRAGMA user_version = ${inventory.length}`);
    verifySchema(database.connection, inventory);
    database.connection.exec('COMMIT');
  } catch (error) {
    database.connection.exec('ROLLBACK');
    throw error;
  } finally { database.close(); }
}

export function openDatabase(path, root = migrationsDirectory, allowUpgrade = false, options = {}) {
  if (!existsSync(path) || !lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) throw new Error('database_missing_or_symlink');
  const readOnly = options.readOnly === true;
  const database = new SqliteDatabase(path, { readOnly });
  try {
    if (readOnly) database.connection.exec('BEGIN');
    const inventory = migrations(root);
    const version = verifySchema(database.connection, inventory);
    if (!allowUpgrade && version !== inventory.length) throw new Error('database_upgrade_required');
    if (!readOnly) database.lockWriter();
    verifySchema(database.connection, inventory);
    return { database, inventory, version };
  } catch (error) { database.close(); throw error; }
}

function fingerprint(connection) {
  return connection.prepare("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name").all().map(({ name }) => {
    const quoted = `"${name.replaceAll('"', '""')}"`;
    const columns = connection.prepare(`PRAGMA table_info(${quoted})`).all();
    const primary = columns.filter(column => column.pk).sort((left, right) => left.pk - right.pk);
    const order = primary.length ? primary.map(column => `"${column.name.replaceAll('"', '""')}"`).join(',') : 'rowid';
    const rows = connection.prepare(`SELECT * FROM ${quoted} ORDER BY ${order}`);
    rows.setReadBigInts(true);
    const hash = createHash('sha256');
    let count = 0;
    for (const row of rows.iterate()) {
      const values = Object.values(row).map(value => value instanceof Uint8Array ? ['blob', Buffer.from(value).toString('base64')]
        : typeof value === 'bigint' ? ['integer', value.toString()] : [typeof value, value]);
      const bytes = Buffer.from(JSON.stringify(values));
      const length = Buffer.alloc(4);
      length.writeUInt32BE(bytes.length);
      hash.update(length).update(bytes);
      count++;
    }
    return { name, count, digest: hash.digest('hex') };
  });
}

export async function verifiedBackup(database, destination, inventory) {
  const descriptor = openSync(destination, 'wx', 0o600);
  closeSync(descriptor);
  await backup(database.connection, destination, { rate: 1000 });
  chmodSync(destination, 0o600);
  const restored = new DatabaseSync(destination, { readOnly: true });
  try {
    verifySchema(restored, inventory);
    if (!isDeepStrictEqual(fingerprint(database.connection), fingerprint(restored))) throw new Error('backup_inventory_mismatch');
  } finally { restored.close(); }
  for (const path of [destination, dirname(destination)]) {
    const descriptor = openSync(path, 'r');
    try { fsyncSync(descriptor); }
    finally { closeSync(descriptor); }
  }
}

export async function upgradeDatabase(path, destination, root = migrationsDirectory) {
  const { database, version, inventory } = openDatabase(path, root, true);
  try {
    if (version === inventory.length) throw new Error('database_already_current');
    await verifiedBackup(database, destination, inventory);
    database.connection.exec('BEGIN IMMEDIATE');
    try {
      for (const item of inventory.slice(version)) database.connection.exec(item.sql);
      database.connection.exec(`PRAGMA user_version = ${inventory.length}`);
      verifySchema(database.connection, inventory);
      database.connection.exec('COMMIT');
    } catch (error) { database.connection.exec('ROLLBACK'); throw error; }
  } finally { database.close(); }
}