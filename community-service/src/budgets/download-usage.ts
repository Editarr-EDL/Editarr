import type { SqlDatabase, SqlStatement, SqlResult } from '../runtime/contracts';
/** Bound handler queries and retain null usage when SQLite cannot supply trustworthy metadata. */
export function observeDownloadDatabase(database: SqlDatabase, maxQueries: number) {
  let queries = 0, read: number | null = 0, written: number | null = 0;
  const originals = new WeakMap<SqlStatement, SqlStatement>();
  function admitted(count: number) {
    if (queries + count > maxQueries) throw new Error('download_query_limit');
    queries += count;
  }
  function observe(result: SqlResult) {
    const meta = result.meta;
    read = read !== null && typeof meta?.rows_read === 'number' && Number.isSafeInteger(meta.rows_read) && meta.rows_read >= 0 ? read + meta.rows_read : null;
    written = written !== null && typeof meta?.rows_written === 'number' && Number.isSafeInteger(meta.rows_written) && meta.rows_written >= 0 ? written + meta.rows_written : null;
  }
  function tracked(statement: SqlStatement): SqlStatement {
    const proxy = new Proxy(statement, { get(target, property) {
      if (property === 'bind') return (...values: unknown[]) => tracked(target.bind(...values));
      if (['first', 'all', 'run'].includes(String(property))) return async (column?: string) => {
        admitted(1);
        let result: SqlResult<Record<string, unknown>>;
        try { result = property === 'run' ? await target.run() : await target.all<Record<string, unknown>>(); }
        catch (error) { read = null; written = null; throw error; }
        observe(result);
        return property === 'first' ? column === undefined ? result.results[0] ?? null : result.results[0]?.[column] ?? null : result;
      };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    originals.set(proxy, statement);
    return proxy;
  }
  const db = new Proxy(database, { get(target, property) {
    if (property === 'prepare') return (sql: string) => tracked(target.prepare(sql));
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      admitted(statements.length);
      let results: SqlResult[];
      try { results = await target.batch(statements.map(statement => originals.get(statement) ?? statement)); }
      catch (error) { read = null; written = null; throw error; }
      results.forEach(observe);
      return results;
    };
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
  return { db, snapshot: () => ({ queries, rowsRead: read, rowsWritten: written }) };
}

export async function recordDownloadUsage(db: SqlDatabase, contributorId: string,
  outcome: 'allowed' | 'denied', now: number, usage: { rowsRead: number | null; rowsWritten: number | null }) {
  return recordTransferUsage(db, contributorId, outcome, now, usage, 'download_usage_observations');
}

export async function recordImportUsage(db: SqlDatabase, contributorId: string,
  outcome: 'allowed' | 'denied', now: number, usage: { rowsRead: number | null; rowsWritten: number | null }) {
  return recordTransferUsage(db, contributorId, outcome, now, usage, 'import_usage_observations');
}

export async function recordVoteUsage(db: SqlDatabase, contributorId: string,
  outcome: 'allowed' | 'denied', now: number, usage: { rowsRead: number | null; rowsWritten: number | null }) {
  return recordTransferUsage(db, contributorId, outcome, now, usage, 'vote_usage_observations');
}

export async function recordReportUsage(db: SqlDatabase, contributorId: string,
  outcome: 'allowed' | 'denied', now: number, usage: { rowsRead: number | null; rowsWritten: number | null }) {
  return recordTransferUsage(db, contributorId, outcome, now, usage, 'report_usage_observations');
}

async function recordTransferUsage(db: SqlDatabase, contributorId: string, outcome: 'allowed' | 'denied',
  now: number, usage: { rowsRead: number | null; rowsWritten: number | null },
  table: 'download_usage_observations' | 'import_usage_observations' | 'vote_usage_observations' | 'report_usage_observations') {
  const period = new Date(now * 1000).toISOString().slice(0, 10);
  await db.prepare(`INSERT INTO ${table}
    (contributor_id, period, outcome, occurrences, read_units, unknown_reads, write_units, unknown_writes, updated_at)
    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?) ON CONFLICT(contributor_id, period, outcome) DO UPDATE SET
      occurrences = occurrences + 1, read_units = read_units + excluded.read_units,
      unknown_reads = unknown_reads + excluded.unknown_reads,
      write_units = write_units + excluded.write_units, unknown_writes = unknown_writes + excluded.unknown_writes,
      updated_at = MAX(updated_at, excluded.updated_at)`)
    .bind(contributorId, period, outcome, usage.rowsRead ?? 0, usage.rowsRead === null ? 1 : 0,
      usage.rowsWritten ?? 0, usage.rowsWritten === null ? 1 : 0, now * 1000).run();
}