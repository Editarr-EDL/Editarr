import type { SqlDatabase } from '../../runtime/contracts';

export const TREND_METRICS = ['db', 'storage', 'denials', 'requests', 'uploads_votes', 'downloads'] as const;
export const TREND_PERIODS = ['24h', '7d', '30d', '90d'] as const;
export type TrendMetric = typeof TREND_METRICS[number];
export type TrendPeriod = typeof TREND_PERIODS[number];

const HOUR_MS = 3_600_000, DAY_MS = 86_400_000;
const SPAN_MS: Record<TrendPeriod, number> = { '24h': DAY_MS, '7d': 7 * DAY_MS, '30d': 30 * DAY_MS, '90d': 90 * DAY_MS };
/** Integer bucket start in milliseconds; parameters may bind as REAL, so both sides are cast. */
const bucketOf = (ms: string) => `(CAST(${ms} AS INTEGER) / CAST(?1 AS INTEGER)) * CAST(?1 AS INTEGER)`;
interface Point { t: number; v: number; v2?: number }
interface Row { b: number; v: number | null; v2: number | null }

/** Gauges report their latest sample as the total; counters sum their buckets and fill empty ones with 0. */
const definitions: Record<TrendMetric, { unit: string; gauge: boolean; daily: boolean; hasV2: boolean;
  provenance: 'service_database' | 'application_observed'; sql: string }> = {
  db: { unit: 'bytes', gauge: true, daily: false, hasV2: true, provenance: 'service_database',
    sql: `SELECT ${bucketOf('hour')} AS b, max(db_bytes) AS v, max(wal_bytes) AS v2 FROM service_metrics_hourly
      WHERE hour >= ?2 AND hour < ?3 GROUP BY b` },
  storage: { unit: 'bytes', gauge: true, daily: false, hasV2: true, provenance: 'service_database',
    sql: `SELECT ${bucketOf('hour')} AS b, max(payload_bytes) AS v, max(payload_quota_bytes) AS v2 FROM service_metrics_hourly
      WHERE hour >= ?2 AND hour < ?3 GROUP BY b` },
  denials: { unit: 'requests', gauge: false, daily: true, hasV2: false, provenance: 'application_observed',
    sql: `SELECT CAST(unixepoch(day) AS INTEGER) * 1000 AS b, sum(occurrences) AS v, NULL AS v2 FROM usage_daily
      WHERE outcome = 'denied' AND resource = 'worker_requests' AND day >= date(?2 / 1000, 'unixepoch')
        AND day < date(?3 / 1000, 'unixepoch') AND ?1 > 0 GROUP BY day` },
  requests: { unit: 'requests', gauge: false, daily: true, hasV2: true, provenance: 'application_observed',
    sql: `SELECT CAST(unixepoch(day) AS INTEGER) * 1000 AS b, sum(units) AS v,
        sum(CASE WHEN outcome = 'denied' THEN occurrences ELSE 0 END) AS v2 FROM usage_daily
      WHERE resource = 'worker_requests' AND day >= date(?2 / 1000, 'unixepoch')
        AND day < date(?3 / 1000, 'unixepoch') AND ?1 > 0 GROUP BY day` },
  uploads_votes: { unit: 'events', gauge: false, daily: false, hasV2: true, provenance: 'service_database',
    sql: `SELECT b, sum(u) AS v, sum(w) AS v2 FROM (
        SELECT ${bucketOf('created_at * 1000')} AS b, 1 AS u, 0 AS w FROM submissions
          WHERE created_at >= ?2 / 1000 AND created_at < ?3 / 1000
        UNION ALL SELECT ${bucketOf('created_at * 1000')}, 0, 1 FROM vote_events
          WHERE created_at >= ?2 / 1000 AND created_at < ?3 / 1000 AND kind IN ('cast', 'changed')) GROUP BY b` },
  downloads: { unit: 'downloads', gauge: false, daily: false, hasV2: false, provenance: 'service_database',
    sql: `SELECT ${bucketOf('issued_at * 1000')} AS b, count(*) AS v, NULL AS v2 FROM download_receipts
      WHERE issued_at >= ?2 / 1000 AND issued_at < ?3 / 1000 GROUP BY b` },
};

/**
 * `GET /admin/v1/trends`: one metric over `period`, bucketed hourly for 24h and 7d (daily for usage
 * rollups) and daily for 30d and 90d. `total`/`previousTotal` compare this period with the one before;
 * `total2`/`previousTotal2` do the same for `v2` on counters. `now` is milliseconds.
 */
export async function serviceTrend(db: SqlDatabase, metric: TrendMetric, period: TrendPeriod, now: number) {
  const definition = definitions[metric], span = SPAN_MS[period];
  const bucket = definition.daily || span > 7 * DAY_MS ? DAY_MS : HOUR_MS;
  const end = Math.floor(now / bucket) * bucket + bucket, start = end - span, previousStart = start - span;
  const rows = (await db.prepare(definition.sql).bind(bucket, previousStart, end).all<Row>()).results;
  const point = (row: Row): Point => ({ t: row.b, v: Number(row.v ?? 0), ...(definition.hasV2 ? { v2: Number(row.v2 ?? 0) } : {}) });
  const current = rows.filter(row => row.b >= start).map(point).sort((a, b) => a.t - b.t);
  const previous = rows.filter(row => row.b < start).map(point).sort((a, b) => a.t - b.t);
  const sum = (points: Point[], key: 'v' | 'v2') => points.reduce((total, item) => total + (item[key] ?? 0), 0);
  let points = current;
  if (!definition.gauge) {
    const byTime = new Map(current.map(item => [item.t, item]));
    points = [];
    for (let t = start; t < end; t += bucket) points.push(byTime.get(t) ?? { t, v: 0, ...(definition.hasV2 ? { v2: 0 } : {}) });
  }
  const totals = definition.gauge
    ? { total: current.at(-1)?.v ?? null, previousTotal: previous.at(-1)?.v ?? null, total2: current.at(-1)?.v2 ?? null,
      previousTotal2: previous.at(-1)?.v2 ?? null }
    : { total: sum(current, 'v'), previousTotal: sum(previous, 'v'), total2: definition.hasV2 ? sum(current, 'v2') : null,
      previousTotal2: definition.hasV2 ? sum(previous, 'v2') : null };
  return { version: 1, observedAt: now, metric, period, unit: definition.unit, bucketMs: bucket, points, ...totals,
    provenance: definition.provenance };
}
