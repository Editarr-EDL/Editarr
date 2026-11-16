import type { DatabaseSync, DatabaseSyncOptions } from 'node:sqlite';
import type { SqlDatabase, SqlResult, SqlStatement } from './contracts';

export class SqliteDatabase implements SqlDatabase {
  constructor(path: string, options?: DatabaseSyncOptions);
  connection: DatabaseSync;
  prepare(sql: string): SqlStatement;
  batch<Row = Record<string, unknown>>(statements: SqlStatement[]): Promise<SqlResult<Row>[]>;
  lockWriter(): void;
  close(): void;
}