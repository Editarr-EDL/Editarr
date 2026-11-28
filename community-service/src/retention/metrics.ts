import type { SqlDatabase } from '../runtime/contracts';

/** Hourly service samples are kept for 90 days. */
export const METRICS_RETENTION_MS = 90 * 86_400_000;
const HOUR_MS = 3_600_000;

/**
 * Upsert this hour's `service_metrics_hourly` sample: database pages, WAL file size (null when the
 * runtime cannot stat it) and stored submission bytes against the global quota. `now` is milliseconds.
 */
export async function recordServiceMetrics(db: SqlDatabase, now: number, walBytes: number | null) {
  await db.prepare(`INSERT INTO service_metrics_hourly (hour, db_bytes, wal_bytes, payload_bytes, payload_quota_bytes, source)
    SELECT ?, (SELECT page_count FROM pragma_page_count()) * (SELECT page_size FROM pragma_page_size()), ?,
      coalesce((SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'), 0),
      (SELECT global_bytes FROM artifact_storage_policy WHERE policy_id = 1), 'service_database'
    ON CONFLICT(hour) DO UPDATE SET db_bytes = excluded.db_bytes, wal_bytes = excluded.wal_bytes,
      payload_bytes = excluded.payload_bytes, payload_quota_bytes = excluded.payload_quota_bytes, source = excluded.source`)
    .bind(now - now % HOUR_MS, walBytes).run();
}

/**
 * Roll `usage_observations` into `usage_daily` totals across contributors. It refreshes the day
 * before the latest rolled-up day onward (every day on the first run), so later pruning of
 * per-contributor observations keeps the daily history.
 */
export async function rollupUsageDaily(db: SqlDatabase) {
  await db.prepare(`INSERT INTO usage_daily (day, resource, outcome, occurrences, units, contributors)
    SELECT period, resource, outcome, sum(occurrences), sum(units), count(DISTINCT contributor_id) FROM usage_observations
    WHERE resource IN ('worker_requests', 'd1_writes')
      AND period >= coalesce((SELECT date(max(day), '-1 day') FROM usage_daily), '')
    GROUP BY period, resource, outcome
    ON CONFLICT(day, resource, outcome) DO UPDATE SET occurrences = excluded.occurrences, units = excluded.units,
      contributors = excluded.contributors`).run();
}

/** Delete at most `limit` hourly samples older than `METRICS_RETENTION_MS`. */
export async function pruneServiceMetrics(db: SqlDatabase, now: number, limit: number) {
  await db.prepare(`DELETE FROM service_metrics_hourly WHERE hour IN
    (SELECT hour FROM service_metrics_hourly WHERE hour < ? ORDER BY hour LIMIT ?)`).bind(now - METRICS_RETENTION_MS, limit).run();
}
