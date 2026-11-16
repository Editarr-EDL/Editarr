import type { SqlDatabase, SqlStatement, SqlResult } from '../../runtime/contracts';
import { transientStatement } from '../../retention/transient';

function periods(now: number) {
  if (!Number.isSafeInteger(now) || now < 0) throw new Error('usage_unavailable');
  const day = new Date(now).toISOString().slice(0, 10);
  return { day, month: day.slice(0, 7) };
}

/** Read passive application activity, never a provider billing or restore baseline. */
export async function serviceUsage(db: SqlDatabase, now: number) {
  const { day } = periods(now);
  const rows = await db.prepare(`SELECT resource, SUM(units) AS units, MAX(updated_at) AS updated_at
    FROM (SELECT resource, units, updated_at FROM usage_observations
      WHERE (resource IN ('worker_requests', 'd1_writes') AND period = ?)
      UNION ALL SELECT resource, units, updated_at FROM oauth_usage_counters WHERE period = ?)
    GROUP BY resource`).bind(day, day)
    .all<{ resource: string; units: number; updated_at: number }>();
  const resources = Object.fromEntries(['worker_requests', 'd1_writes'].map(resource => {
    const row = rows.results.find((entry) => entry.resource === resource);
    if (row && (!Number.isSafeInteger(row.units) || row.units < 0 ||
        !Number.isSafeInteger(row.updated_at) || row.updated_at < 0)) throw new Error('usage_unavailable');
    return [resource, { observed: row?.units ?? null, observedAt: row?.updated_at ?? null }];
  }));
  return { source: 'partial_application_observed' as const, providerVerified: false as const,
    restoreProvenance: 'unknown' as const, resources };
}

/** Record bounded daily broker activity without retaining IP, OAuth state or account identity. */
export async function recordOAuthUsage(db: SqlDatabase, resource: 'worker_requests' | 'd1_writes',
  units: number, now: number): Promise<SqlResult> {
  const { day } = periods(now);
  if (!Number.isSafeInteger(units) || units < 0) throw new Error('usage_unavailable');
  return db.prepare(`INSERT INTO oauth_usage_counters (resource, period, units, updated_at)
    VALUES (?, ?, ?, ?) ON CONFLICT(resource, period) DO UPDATE SET
      units = units + excluded.units, updated_at = MAX(updated_at, excluded.updated_at)`)
    .bind(resource, day, units + (resource === 'd1_writes' ? 1 : 0), now).run();
}

/** Observe mutation metadata without throwing after a committed transition. */
export function observeOAuthDatabase(db: SqlDatabase) {
  let written: number | null = 0;
  const originals = new WeakMap<SqlStatement, SqlStatement>();
  function observe(result: SqlResult) {
    const units = result.meta.rows_written;
    written = written !== null && typeof units === 'number' && Number.isSafeInteger(units) && units >= 0 ? written + units : null;
  }
  function tracked(statement: SqlStatement): SqlStatement {
    const proxy = new Proxy(statement, { get(target, property) {
      if (property === 'bind') return (...values: unknown[]) => tracked(target.bind(...values));
      if (property === 'run') return async () => {
        const result = await target.run();
        observe(result);
        return result;
      };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    originals.set(proxy, statement);
    return proxy;
  }
  const observedDb = new Proxy(db, { get(target, property) {
    if (property === 'prepare') return (query: string) => tracked(target.prepare(query));
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      const results = await target.batch(statements.map((statement) => originals.get(statement) ?? statement));
      results.forEach(observe);
      return results;
    };
    const value = Reflect.get(target, property);
    return typeof value === 'function' ? value.bind(target) : value;
  } });
  return { db: observedDb, rowsWritten: () => written };
}

/** Bound historical daily detail; current observations and contributor data remain untouched. */
export async function pruneOAuthUsage(db: SqlDatabase, now: number, limit: number): Promise<number> {
  periods(now);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new Error('invalid_limit');
  const result = await transientStatement(db, 'brokerObservations', now, limit, 'prune').run();
  return result.meta.changes;
}