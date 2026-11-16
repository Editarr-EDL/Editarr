import { DatabaseSync } from 'node:sqlite';
import { performance } from 'node:perf_hooks';

function parameter(value) {
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  if (value === null || typeof value === 'string' || typeof value === 'bigint') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  throw new Error('invalid_sqlite_parameter');
}

function record(row) {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => {
    if (value instanceof Uint8Array) return [key, value.slice().buffer];
    if (typeof value === 'bigint') {
      const number = Number(value);
      if (!Number.isSafeInteger(number)) throw new Error('unsafe_sqlite_integer');
      return [key, number];
    }
    return [key, value];
  }));
}

export class SqliteDatabase {
  constructor(path, options = {}) {
    this.connection = new DatabaseSync(path, options);
    this.connection.exec('PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;');
  }

  prepare(sql) { return new SqliteStatement(this, sql); }

  lockWriter() {
    this.connection.exec('PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL; PRAGMA locking_mode = EXCLUSIVE; BEGIN EXCLUSIVE; COMMIT;');
  }

  async batch(statements) {
    if (!statements.every(statement => statement instanceof SqliteStatement && statement.database === this)) {
      throw new Error('invalid_sqlite_batch');
    }
    this.connection.exec('BEGIN IMMEDIATE');
    try {
      const results = statements.map(statement => statement.execute());
      this.connection.exec('COMMIT');
      return results;
    } catch (error) {
      this.connection.exec('ROLLBACK');
      throw error;
    }
  }

  close() { this.connection.close(); }
}

class SqliteStatement {
  constructor(database, sql, values = []) {
    this.database = database;
    this.sql = sql;
    this.values = values;
  }

  bind(...values) { return new SqliteStatement(this.database, this.sql, values.map(parameter)); }

  execute() {
    const connection = this.database.connection;
    const started = performance.now();
    const before = connection.prepare('SELECT total_changes() AS count').get().count;
    const statement = connection.prepare(this.sql);
    statement.setReadBigInts(true);
    const results = statement.all(...this.values).map(record);
    const after = connection.prepare('SELECT total_changes() AS count').get().count;
    const changed = after - before;
    const changes = changed === 0 ? 0 : connection.prepare('SELECT changes() AS count').get().count;
    return { success: true, results, meta: {
      changes, duration: performance.now() - started,
      rows_read: null, rows_written: changed,
    } };
  }

  async all() { return this.execute(); }
  async run() { return this.execute(); }
  async first(column) {
    const row = this.execute().results[0];
    if (!row) return null;
    if (column === undefined) return row;
    if (!Object.hasOwn(row, column)) throw new Error('unknown_sqlite_column');
    return row[column];
  }
}