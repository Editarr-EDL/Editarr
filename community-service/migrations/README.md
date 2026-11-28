# Community SQLite migrations

## Current schema

The current schema is **`community-master-1` version 5**: the baseline
`0001_master_baseline.sql`, `0002_filter_expressions.sql`,
`0003_download_receipt_version_index.sql`, `0004_retention_scan_indexes.sql` and
`0005_operator_insights.sql`. It is the first migration of a new lineage, recorded in
the immutable single-row `schema_lineage` table. See
[contract §8](../../docs/community-scan-service-contracts.md#8-storage-schema-service)
and [ADR 0002](../../docs/adr/0002-community-master-filter-sets.md).

The master-filter-set refactor rebased the earlier revision-era migrations
(0001–0018). The owner explicitly authorized discarding that store because the
community service has no users (ADR 0002, D1). The runner never converts it:

- `initializeDatabase` creates a new store only at an unused path.
- `openDatabase`, `upgradeDatabase`, `backup` and `retention` refuse any store whose
  `schema_lineage` is missing or different with `unsupported_database_lineage`.
  They never stamp, upgrade or back up such a store, and leave its bytes unchanged.
- An operator moves from the old lineage by stopping the service, archiving the old
  data volume and initializing a new one; see the
  [Docker guide](../../docs/community-service-docker.md#persistence-and-recovery).

## Baseline contents

- **Identity and enrollment:** `contributors` (with `quarantined` for moderation),
  `installations`, `installation_keys`, `enrollment_states`, `discord_oauth_states`,
  `oauth_usage_counters`, `discord_membership`, `discord_membership_refresh` and
  `profile_name_changes`, folded from the former 0001–0008.
- **Replay and idempotency:** `request_nonces` (bound to the active key's
  contributor) and `operations` (digest-immutable).
- **Observations and windows:** `usage_observations` (`worker_requests` and
  `d1_writes` only) and `rate_counters`, the fixed-window counters behind the
  `MASTER_POLICY` request limits.
- **Stored bytes:** `artifact_storage_policy` and `artifact_storage_usage`. Inserting a
  submission charges its raw bytes and is refused beyond the global or contributor cap.
- **Moderation:** `moderation_versions`, `moderation_epoch`, `operator_request_counts`,
  `moderation_actions` (visibility, account, vote, report and recompute commands),
  `catalog_moderation_actions` (version and alias commands) and `reports`
  retargeted to `target_type`/`target_id`.
- **Catalog:** `works`, `work_identifiers`, `work_identifier_conflicts`,
  `work_titles`, `aliases` and `alias_spellings` (seeded), `alias_suggestions`,
  `versions`, `version_match_keys`, `version_acceptances` and the
  `version_counted_acceptances` view. These were `master_works` and
  `master_work_identifiers` in the interim 0018.
- **Master filter sets:** `submissions` (raw artifact, manifest and envelope BLOBs,
  immutable), `submission_entries`, `submission_coverage`, `filter_registry`,
  `master_filters`, `filter_attestations`, `filter_votes`, `master_revisions`,
  `master_revision_filters`, `download_receipts` and `sync_runs`.

The budget ledger, quota, release, contribution, artifact payload, import receipt,
revision vote and per-handler usage tables were not carried into the baseline.

## Later migrations

- **0002 filter expressions:** nullable `submission_entries.expression` (language
  supports only), `filter_registry.incumbent_expression` and `master_filters.expression`
  for the word or phrase of a language filter (contract §5.2, §6.7). Existing rows keep
  their values; the upgrade runs after the verified backup.
- **0003 download receipt version index:** `idx_download_receipts_version` on
  `download_receipts(version_id, revision, issued_at)`, so snapshot-byte retention
  checks each superseded revision's receipts without a table scan (contract §8).
  It adds no columns and changes no rows.
- **0004 retention scan indexes:** the partial index
  `idx_master_revisions_retained_bytes` on `master_revisions(version_id, revision)`
  `WHERE snapshot_bytes IS NOT NULL`, so snapshot-byte retention visits only
  revisions that still hold bytes and never rescans pruned history, and
  `idx_sync_runs_expiry` on `sync_runs(started_at)`, so expired sync runs are found
  without a full scan and sort (contract §8). It adds no columns and changes no rows.
- **0005 operator insights:** the append-only `vote_events` history (backfilled with
  one `cast` event per existing `filter_votes` row at its `updated_at`),
  `service_metrics_hourly`, `usage_daily`, `maintenance_runs`, the single-row
  `backup_config` (inserted disabled) and `backup_runs`. It rebuilds
  `moderation_actions` with its rows, index and triggers so the audit also accepts
  `backup_configure`, `backup_run` and `backup_stage`, which never advance a moderation
  revision, and adds read-path indexes for the operator console (contract §8). Upgrade
  tests cover every earlier version (1–4).

Add a new numbered file for every later schema change; never edit an applied
migration. Later migrations return to verified-backup-first upgrades: the offline
`upgrade` command writes and verifies a backup at a new destination, then applies
the pending migrations in one transaction that rolls back on failure. Include
upgrade tests from every supported `community-master-1` version, and never reset
a store or stamp an unknown schema.
