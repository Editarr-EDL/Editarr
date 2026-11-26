import type { SqlDatabase } from '../runtime/contracts';
import { transientStatement } from './transient';
import { SNAPSHOT_RETENTION } from '../master/recompute';

/**
 * Prune expired fixed-window request counters and sync runs by `idx_rate_counters_expiry` and
 * `idx_sync_runs_expiry` (migration 0004); `now` is milliseconds.
 */
export async function pruneRateWindows(db: SqlDatabase, now: number, limit: number) {
  await db.batch([transientStatement(db, 'rateCounters', now, limit, 'prune'),
    transientStatement(db, 'syncRuns', now, limit, 'prune')]);
}

/**
 * Clear the bytes of superseded snapshots whose receipts all predate the cutoff (seconds), at most
 * `limit` rows. The scan reads `idx_master_revisions_retained_bytes` (migration 0004), so it visits
 * only revisions that still hold bytes and never rescans pruned history; the receipt check uses
 * `idx_download_receipts_version` (migration 0003).
 */
export const PRUNE_SNAPSHOT_BYTES_SQL = `UPDATE master_revisions SET snapshot_bytes = NULL WHERE rowid IN (SELECT m.rowid FROM master_revisions m
    WHERE m.snapshot_bytes IS NOT NULL
      AND m.revision < (SELECT max(revision) FROM master_revisions l WHERE l.version_id = m.version_id)
      AND NOT EXISTS (SELECT 1 FROM download_receipts r WHERE r.version_id = m.version_id AND r.revision = m.revision
        AND r.issued_at > ?) LIMIT ?)`;

/**
 * Drop superseded snapshot bytes whose receipts left the retention window (§8); a bounded batch
 * per run. The latest stored revision of each version always keeps its bytes. `now` is milliseconds.
 */
export async function pruneSnapshotBytes(db: SqlDatabase, now: number, limit: number) {
  await db.prepare(PRUNE_SNAPSHOT_BYTES_SQL)
    .bind(Math.floor(now / 1000) - SNAPSHOT_RETENTION.receiptSeconds, limit).run();
}
