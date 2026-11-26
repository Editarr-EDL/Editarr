import type { SqlDatabase, SqlStatement, SqlResult } from '../runtime/contracts';
/** Bound handler queries and retain null usage when SQLite cannot supply trustworthy metadata. */
export function observeQueries(database: SqlDatabase, maxQueries: number) {
  let queries = 0, read: number | null = 0, written: number | null = 0;
  const originals = new WeakMap<SqlStatement, SqlStatement>();
  function admitted(count: number) {
    if (queries + count > maxQueries) throw new Error('query_limit');
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
