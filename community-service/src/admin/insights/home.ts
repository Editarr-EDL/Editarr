import type { SqlDatabase } from '../../runtime/contracts';
import type { Env } from '../../config';
import { z } from 'zod';
import { nextBackupAt, type BackupFrequency } from '../../backups/schedule';
import { serviceHealth } from '../system/health';
import { resolveLabels } from '../labels';
import { parseQuery, PERIOD_SECONDS, type Period } from './query';
import { queueCounts, queueCountsStatement, readQueue } from './queue';
import { readActivity } from './activity';
import { readLeaderboard } from './leaderboard';
import { summariseRevisions } from './revisions';

/** A numeric KPI: the period's value, the previous period's, and per-bucket points (`t` is the bucket start, ms). */
export interface Kpi { value: number; previous: number; series: { t: number; v: number }[] }

/** Contributors active (uploaded, voted or reported) at second `t` since `?1`. */
const activity = `SELECT contributor_id AS c, created_at AS t FROM submissions WHERE created_at >= ?1
  UNION ALL SELECT contributor_id, created_at FROM vote_events WHERE created_at >= ?1
  UNION ALL SELECT contributor_id, created_at FROM reports WHERE created_at >= ?1`;
const votes = `kind IN ('cast', 'changed')`;

/**
 * KPI statements: `?1` previous-period start, `?2` current-period start, `?3` bucket seconds, `?4`
 * bucket count (Unix seconds throughout). Votes count casts and changes from `vote_events`.
 */
function kpiStatements(db: SqlDatabase, previous: number, start: number, bucket: number, buckets: number) {
  const index = (column: string) => `min((${column} - ?2) / ?3, ?4 - 1)`;
  return [
    db.prepare(`WITH act AS (${activity}) SELECT
      (SELECT count(DISTINCT c) FROM act WHERE t >= ?2) AS activeNow, (SELECT count(DISTINCT c) FROM act WHERE t < ?2) AS activePrev,
      (SELECT count(*) FROM submissions WHERE created_at >= ?2) AS uploadsNow,
      (SELECT count(*) FROM submissions WHERE created_at >= ?1 AND created_at < ?2) AS uploadsPrev,
      (SELECT count(DISTINCT v.work_id) FROM submissions s JOIN versions v ON v.version_id = s.version_id WHERE s.created_at >= ?2) AS uploadItems,
      (SELECT count(*) FROM vote_events WHERE created_at >= ?2 AND ${votes}) AS votesNow,
      (SELECT count(*) FROM vote_events WHERE created_at >= ?1 AND created_at < ?2 AND ${votes}) AS votesPrev,
      (SELECT count(*) FROM vote_events WHERE created_at >= ?2 AND ${votes} AND vote = 'up') AS votesUp,
      (SELECT count(*) FROM reports WHERE state = 'open') AS openReports,
      (SELECT count(*) FROM reports WHERE state = 'open' AND target_type = 'version') AS openVersionReports,
      (SELECT min(created_at) FROM reports WHERE state = 'open') AS oldestReport`).bind(previous, start),
    db.prepare(`WITH act AS (${activity})
      SELECT 'active' AS m, ${index('t')} AS b, count(DISTINCT c) AS v FROM act WHERE t >= ?2 GROUP BY b
      UNION ALL SELECT 'uploads', ${index('created_at')} AS b, count(*) FROM submissions WHERE created_at >= ?2 GROUP BY b
      UNION ALL SELECT 'votes', ${index('created_at')} AS b, count(*) FROM vote_events WHERE created_at >= ?2 AND ${votes} GROUP BY b`)
      .bind(previous, start, bucket, buckets),
    db.prepare(`SELECT c.enabled, c.frequency, c.at_utc AS atUtc, r.status, r.started_at AS at, r.bytes FROM backup_config c
      LEFT JOIN (SELECT status, started_at, bytes FROM backup_runs WHERE status != 'running' ORDER BY started_at DESC LIMIT 1) r
      WHERE c.singleton = 1`),
  ];
}

/**
 * `GET /admin/v1/home?period=24h|7d|30d` (default 7d): KPIs against the previous period, the top
 * five queue items, latest master revisions, top contributors, recent activity and health.
 * Hourly buckets for 24h, daily otherwise.
 */
export async function home(db: SqlDatabase, env: Env, url: URL, now: number) {
  const query = parseQuery(url, 'invalid_home_query', { period: z.enum(['24h', '7d', '30d']).optional() });
  const period: Period = query.period ?? '7d', length = PERIOD_SECONDS[period];
  const bucket = period === '24h' ? 3600 : 86_400, buckets = length / bucket, start = now - length, previous = start - length;
  const [totals, series, backup, counts, revisions] = await db.batch<Record<string, any>>([
    ...kpiStatements(db, previous, start, bucket, buckets), queueCountsStatement(db),
    db.prepare(`SELECT version_id AS versionId, revision, generated_at AS generatedAt FROM master_revisions
      ORDER BY generated_at DESC, rowid DESC LIMIT 5`)]);
  const points = (metric: string) => Array.from({ length: buckets }, (_, index) => ({ t: (start + index * bucket) * 1000,
    v: Number(series.results.find(row => row.m === metric && row.b === index)?.v ?? 0) }));
  const kpi = (metric: string, value: number, before: number): Kpi => ({ value, previous: before, series: points(metric) });
  const row = totals.results[0], config = backup.results[0];
  const nextAt = config && env.COMMUNITY_BACKUP_ROOT ? nextBackupAt({ enabled: config.enabled, frequency: config.frequency as BackupFrequency,
    at_utc: config.atUtc }, now * 1000) : null;
  const updates = await summariseRevisions(db, revisions.results as { versionId: string; revision: number; generatedAt: number }[]);
  const labels = await resolveLabels(db, { versions: updates.map(update => update.versionId) });
  const [attention, feed, leaders, health] = [await readQueue(db, 'all', null, 5), await readActivity(db, 'all', null, 8),
    await readLeaderboard(db, start, 'accepted', 0, 5), await serviceHealth(db, env, now * 1000)];
  const { version: _, observedAt: __, ...healthSummary } = health;
  return {
    version: 1, observedAt: now * 1000, period,
    kpis: {
      activeContributors: kpi('active', row.activeNow, row.activePrev),
      uploads: { ...kpi('uploads', row.uploadsNow, row.uploadsPrev), items: row.uploadItems },
      votes: { total: kpi('votes', row.votesNow, row.votesPrev), upShare: row.votesNow ? row.votesUp / row.votesNow : null },
      openReports: { count: row.openReports, oldestAt: row.oldestReport === null ? null : row.oldestReport * 1000,
        versionLevel: row.openVersionReports },
      lastBackup: !config || (config.status === null && !nextAt) ? null
        : { status: config.status ?? null, at: config.at ?? null, bytes: config.bytes ?? null, nextAt },
    },
    queueCounts: queueCounts(counts.results[0]),
    attention: attention.items,
    latestUpdates: updates.map(({ versionId, ...update }) => ({ at: update.generatedAt, ...labels.target('version', versionId), ...update })),
    topContributors: leaders.items,
    activity: feed.items,
    health: healthSummary,
  };
}
