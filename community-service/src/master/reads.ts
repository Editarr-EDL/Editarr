import type { SqlDatabase } from '../runtime/contracts';

/** Served filter counts at each version's current revision (§7.1 `filterCount`). */
export async function servedFilterCounts(db: SqlDatabase, versionIds: readonly string[]): Promise<Map<string, number>> {
  const rows = await db.prepare(`SELECT version_id, count(*) AS count FROM master_filters
    WHERE served = 1 AND version_id IN (SELECT value FROM json_each(?)) GROUP BY version_id`)
    .bind(JSON.stringify(versionIds)).all<{ version_id: string; count: number }>();
  return new Map(rows.results.map(row => [row.version_id, row.count]));
}
