import { mkdirSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { initializeDatabase, openDatabase } from '../../src/runtime/schema.mjs';
import type { SqliteDatabase } from '../../src/runtime/sqlite.mjs';
import { backupDrain } from '../../src/backups/runner';

/** A current-schema store opened like the runtime (WAL, exclusive lock) plus a backup root. */
export function backupFixture() {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'community-backups-')));
  const path = join(directory, 'community.sqlite'), root = join(directory, 'backups');
  mkdirSync(root, { mode: 0o700 });
  initializeDatabase(path);
  const { database } = openDatabase(path);
  return { directory, root, database, env: { CATALOG_DB: database, COMMUNITY_BACKUP_ROOT: root },
    async close() { await backupDrain(); database.close(); rmSync(directory, { recursive: true, force: true }); } };
}

/** Insert `pairs` transactions of two `usage_daily` rows each through the serving connection. */
export function writePairs(database: SqliteDatabase, from: number, pairs: number, padding = 0) {
  const insert = database.connection.prepare('INSERT INTO usage_daily VALUES (?, ?, ?, 1, 1, 1)');
  for (let index = from; index < from + pairs; index++) {
    database.connection.exec('BEGIN IMMEDIATE');
    const day = String(index).padStart(8, '0') + 'x'.repeat(padding);
    insert.run(day, 'r', 'a');
    insert.run(day, 'r', 'b');
    database.connection.exec('COMMIT');
  }
}
