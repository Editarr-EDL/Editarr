import type { SqlDatabase } from '../../runtime/contracts';
import type { Env } from '../../config';
import { latestRun } from './runs';

const HOUR_MS = 3_600_000;
const FREQUENCY_MS: Record<string, number> = { hourly: HOUR_MS, every_6h: 6 * HOUR_MS, daily: 24 * HOUR_MS, weekly: 168 * HOUR_MS };
/** Thresholds behind the health checks. */
export const HEALTH_LIMITS = Object.freeze({ retentionStaleMs: 2 * HOUR_MS, storageShare: 0.9, walBytes: 64 * 1024 * 1024 });

interface Check { id: string; label: string; ok: boolean; detail: string }
const mib = (bytes: number) => `${(bytes / 1048576).toFixed(1)} MiB`;

/**
 * `GET /admin/v1/health`: live database and storage sizes, the latest maintenance runs and pass/fail
 * checks. `ready` is true only when every check passes. `now` is milliseconds.
 */
export async function serviceHealth(db: SqlDatabase, env: Env, now: number) {
  const facts = await db.prepare(`SELECT (SELECT page_count FROM pragma_page_count()) AS pages,
      (SELECT page_size FROM pragma_page_size()) AS pageSize, (SELECT lineage FROM schema_lineage) AS lineage,
      (SELECT user_version FROM pragma_user_version()) AS schemaVersion,
      coalesce((SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'), 0) AS payloadBytes,
      (SELECT global_bytes FROM artifact_storage_policy WHERE policy_id = 1) AS quotaBytes,
      (SELECT enabled FROM backup_config WHERE singleton = 1) AS backupsEnabled,
      (SELECT frequency FROM backup_config WHERE singleton = 1) AS backupFrequency,
      (SELECT max(started_at) FROM backup_runs WHERE status = 'verified') AS lastBackupAt`)
    .first<{ pages: number; pageSize: number; lineage: string | null; schemaVersion: number; payloadBytes: number;
      quotaBytes: number; backupsEnabled: number | null; backupFrequency: string | null; lastBackupAt: number | null }>();
  if (!facts) throw new Error('health_unavailable');
  const walBytes = env.DATABASE_WAL_BYTES?.() ?? null;
  const [lastRetention, lastRecomputeAll] = [await latestRun(db, 'retention'), await latestRun(db, 'recompute_all')];
  const retentionFresh = lastRetention !== null && now - lastRetention.startedAt <= HEALTH_LIMITS.retentionStaleMs;
  const backupWindow = FREQUENCY_MS[facts.backupFrequency ?? ''] ?? FREQUENCY_MS.daily;
  const checks: Check[] = [
    { id: 'schema', label: 'Database schema', ok: facts.lineage === 'community-master-1',
      detail: `${facts.lineage ?? 'unknown lineage'} version ${facts.schemaVersion}` },
    { id: 'retention', label: 'Hourly maintenance', ok: retentionFresh && lastRetention?.outcome === 'ok',
      detail: lastRetention === null ? 'No maintenance run recorded yet'
        : !retentionFresh ? 'The last maintenance run is more than 2 hours old' : `Last run ${lastRetention.outcome}` },
    { id: 'storage', label: 'Submission storage', ok: facts.payloadBytes < facts.quotaBytes * HEALTH_LIMITS.storageShare,
      detail: `${mib(facts.payloadBytes)} of ${mib(facts.quotaBytes)} used` },
    { id: 'wal', label: 'Write-ahead log', ok: walBytes === null || walBytes < HEALTH_LIMITS.walBytes,
      detail: walBytes === null ? 'Size unavailable in this runtime' : `${mib(walBytes)}` },
    { id: 'backups', label: 'Scheduled backups', ok: facts.backupsEnabled === 1 && facts.lastBackupAt !== null &&
        now - facts.lastBackupAt <= 2 * backupWindow,
      detail: facts.backupsEnabled !== 1 ? 'Scheduled backups are off' : facts.lastBackupAt === null ? 'No verified backup yet'
        : now - facts.lastBackupAt <= 2 * backupWindow ? 'A recent verified backup exists' : 'The newest verified backup is overdue' },
  ];
  return { version: 1, observedAt: now, ready: checks.every(check => check.ok), checks,
    database: { bytes: facts.pages * facts.pageSize, walBytes, pageSize: facts.pageSize },
    storage: { payloadBytes: facts.payloadBytes, quotaBytes: facts.quotaBytes }, lastRetention, lastRecomputeAll };
}
