-- Operator insights (contract §8): append-only vote history, service metrics, usage rollups,
-- maintenance and backup runs, the backup configuration, backup audit commands and read-path
-- indexes. Data-preserving: existing rows are kept, and vote history is backfilled from filter_votes.

-- Append-only vote history; created_at is Unix seconds, like filter_votes.updated_at.
CREATE TABLE vote_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  filter_id TEXT NOT NULL REFERENCES filter_registry(filter_id),
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
  kind TEXT NOT NULL CHECK (kind IN ('cast', 'changed', 'removed', 'excluded', 'restored')),
  receipt_id TEXT,
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE INDEX idx_vote_events_time ON vote_events(created_at);
CREATE INDEX idx_vote_events_filter ON vote_events(filter_id, created_at);
CREATE INDEX idx_vote_events_contributor ON vote_events(contributor_id, created_at);
CREATE TRIGGER guard_vote_event_update BEFORE UPDATE ON vote_events
BEGIN SELECT RAISE(ABORT, 'immutable_vote_event'); END;
INSERT INTO vote_events (filter_id, contributor_id, vote, kind, receipt_id, created_at)
  SELECT filter_id, contributor_id, vote, 'cast', receipt_id, updated_at FROM filter_votes ORDER BY updated_at, filter_id, contributor_id;

-- Hourly service samples; hour is epoch milliseconds floored to the hour.
CREATE TABLE service_metrics_hourly (
  hour INTEGER PRIMARY KEY CHECK (hour >= 0),
  db_bytes INTEGER,
  wal_bytes INTEGER,
  payload_bytes INTEGER,
  payload_quota_bytes INTEGER,
  source TEXT NOT NULL
) WITHOUT ROWID;
-- Daily totals of usage_observations across contributors; day is YYYY-MM-DD (UTC).
CREATE TABLE usage_daily (
  day TEXT NOT NULL,
  resource TEXT NOT NULL,
  outcome TEXT NOT NULL,
  occurrences INTEGER NOT NULL CHECK (occurrences >= 0),
  units INTEGER NOT NULL CHECK (units >= 0),
  contributors INTEGER NOT NULL CHECK (contributors >= 0),
  PRIMARY KEY (day, resource, outcome)
) WITHOUT ROWID;
-- started_at is epoch milliseconds.
CREATE TABLE maintenance_runs (
  run_id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('retention', 'recompute_all', 'metrics')),
  started_at INTEGER NOT NULL CHECK (started_at >= 0),
  duration_ms INTEGER CHECK (duration_ms >= 0),
  outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'batch_limit', 'skipped', 'failed')),
  detail_json TEXT NOT NULL DEFAULT '{}'
) WITHOUT ROWID;
CREATE INDEX idx_maintenance_runs_kind ON maintenance_runs(kind, started_at);

CREATE TABLE backup_config (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  frequency TEXT NOT NULL DEFAULT 'daily' CHECK (frequency IN ('hourly', 'every_6h', 'daily', 'weekly')),
  at_utc TEXT NOT NULL DEFAULT '03:30' CHECK (at_utc GLOB '[0-2][0-9]:[0-5][0-9]'),
  keep_last INTEGER NOT NULL DEFAULT 14 CHECK (keep_last BETWEEN 1 AND 365),
  keep_weekly INTEGER NOT NULL DEFAULT 8 CHECK (keep_weekly BETWEEN 0 AND 104),
  destination TEXT NOT NULL DEFAULT '',
  integrity_check INTEGER NOT NULL DEFAULT 1 CHECK (integrity_check IN (0, 1)),
  updated_at INTEGER NOT NULL DEFAULT 0,
  updated_by TEXT
);
INSERT INTO backup_config (singleton) VALUES (1);
CREATE TABLE backup_runs (
  run_id TEXT PRIMARY KEY NOT NULL,
  started_at INTEGER NOT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual')),
  actor TEXT,
  file_name TEXT,
  bytes INTEGER,
  duration_ms INTEGER,
  integrity TEXT CHECK (integrity IN ('ok', 'failed', 'skipped')),
  sha256 TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'verified', 'failed', 'pruned')),
  error_code TEXT,
  note TEXT
) WITHOUT ROWID;
CREATE INDEX idx_backup_runs_started ON backup_runs(started_at);

-- Rebuild moderation_actions so its audit accepts the backup commands. Columns, rows, the index
-- and the guard triggers are kept; backup commands, like recompute, never advance a revision.
ALTER TABLE moderation_actions RENAME TO moderation_actions_v4;
CREATE TABLE moderation_actions (
  action_id TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  resource TEXT NOT NULL,
  target_id TEXT NOT NULL,
  command TEXT NOT NULL CHECK (command IN ('quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote',
    'restore_vote', 'resolve_report', 'recompute', 'backup_configure', 'backup_run', 'backup_stage')),
  expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
  request_json TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'conflict', 'denied')),
  error_code TEXT CHECK ((outcome = 'applied') = (error_code IS NULL)),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
INSERT INTO moderation_actions SELECT action_id, actor, resource, target_id, command, expected_revision, reason,
  request_json, outcome, error_code, created_at FROM moderation_actions_v4;
DROP TABLE moderation_actions_v4;
CREATE INDEX idx_moderation_audit ON moderation_actions(resource, created_at, action_id);
CREATE TRIGGER guard_audit_update BEFORE UPDATE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_audit_delete BEFORE DELETE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_moderation BEFORE INSERT ON moderation_actions
WHEN NEW.outcome = 'applied' AND NEW.command NOT IN ('recompute', 'backup_configure', 'backup_run', 'backup_stage')
  AND coalesce((SELECT revision FROM moderation_versions WHERE resource = NEW.resource), 0) != NEW.expected_revision
BEGIN SELECT RAISE(ABORT, 'moderation_conflict'); END;
CREATE TRIGGER version_moderation AFTER INSERT ON moderation_actions
WHEN NEW.outcome = 'applied' AND NEW.command NOT IN ('recompute', 'backup_configure', 'backup_run', 'backup_stage')
BEGIN INSERT INTO moderation_versions VALUES (NEW.resource, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;

-- Read-path indexes for the operator activity feed, home KPIs, queue and report counts.
CREATE INDEX idx_moderation_actions_created ON moderation_actions(created_at, action_id);
CREATE INDEX idx_catalog_moderation_actions_created ON catalog_moderation_actions(created_at, action_id);
CREATE INDEX idx_submissions_created ON submissions(created_at, submission_id);
CREATE INDEX idx_reports_created ON reports(created_at, report_id);
CREATE INDEX idx_reports_target ON reports(target_type, target_id, state);
CREATE INDEX idx_master_revisions_generated ON master_revisions(generated_at);
CREATE INDEX idx_filter_attestations_contributor ON filter_attestations(contributor_id, filter_id);
CREATE INDEX idx_filter_registry_quarantined ON filter_registry(filter_id) WHERE quarantined = 1;
CREATE INDEX idx_versions_quarantined ON versions(version_id) WHERE quarantined = 1;
CREATE INDEX idx_version_acceptances_quarantined ON version_acceptances(submission_id) WHERE quarantined = 1;
CREATE INDEX idx_master_filters_disputed ON master_filters(filter_id) WHERE disputes > 0 OR downvotes > upvotes;
CREATE INDEX idx_download_receipts_issued ON download_receipts(issued_at);
