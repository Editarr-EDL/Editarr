-- Bound retention scans by live rows instead of history (contract §8). Data-preserving.
-- Snapshot-byte retention visits only revisions that still hold bytes: pruned revisions leave
-- this partial index, so later runs never rescan them.
CREATE INDEX IF NOT EXISTS idx_master_revisions_retained_bytes ON master_revisions(version_id, revision)
  WHERE snapshot_bytes IS NOT NULL;
-- Expired sync runs are found by start time across installations, without a full scan and sort.
CREATE INDEX IF NOT EXISTS idx_sync_runs_expiry ON sync_runs(started_at);
