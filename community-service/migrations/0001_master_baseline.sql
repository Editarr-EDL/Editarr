/* community-master-1 baseline: identity, operations, moderation, catalog and master filter sets. */
CREATE TABLE schema_lineage (
  lineage TEXT PRIMARY KEY NOT NULL CHECK (lineage = 'community-master-1')
) WITHOUT ROWID;
INSERT INTO schema_lineage (lineage) VALUES ('community-master-1');
CREATE TRIGGER guard_schema_lineage_update BEFORE UPDATE ON schema_lineage
BEGIN SELECT RAISE(ABORT, 'immutable_schema_lineage'); END;
CREATE TRIGGER guard_schema_lineage_delete BEFORE DELETE ON schema_lineage
BEGIN SELECT RAISE(ABORT, 'immutable_schema_lineage'); END;

/* Identity */
CREATE TABLE contributors (
  contributor_id TEXT PRIMARY KEY NOT NULL,
  discord_user_id TEXT NOT NULL CONSTRAINT uq_contributors_discord_user_id UNIQUE,
  display_name TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'suspended')),
  created_at INTEGER NOT NULL,
  profile_updated_at INTEGER NOT NULL DEFAULT 0,
  display_name_key TEXT,
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1))
);
CREATE UNIQUE INDEX uq_contributors_display_name ON contributors(display_name_key);
CREATE TRIGGER guard_profile_name_insert BEFORE INSERT ON contributors
WHEN NEW.display_name_key IS NULL OR NEW.display_name_key = ''
BEGIN SELECT RAISE(ABORT, 'display_name_key_required'); END;
CREATE TRIGGER guard_profile_name_update BEFORE UPDATE OF display_name, display_name_key ON contributors
WHEN NEW.display_name_key IS NULL OR NEW.display_name_key = ''
BEGIN SELECT RAISE(ABORT, 'display_name_key_required'); END;

CREATE TABLE installations (
  installation_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  created_at INTEGER NOT NULL,
  CONSTRAINT uq_installations_contributor_id UNIQUE (contributor_id, installation_id)
);
CREATE INDEX idx_installations_contributor ON installations(contributor_id);

CREATE TABLE installation_keys (
  key_id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL REFERENCES installations(installation_id),
  algorithm TEXT NOT NULL CHECK (algorithm = 'Ed25519'),
  public_key BLOB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'revoked')),
  created_at INTEGER NOT NULL,
  retired_at INTEGER,
  CHECK ((state = 'active' AND retired_at IS NULL) OR (state != 'active' AND retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_installation_keys_active ON installation_keys(installation_id) WHERE state = 'active';
CREATE INDEX idx_installation_keys_history ON installation_keys(installation_id, created_at);

/* Enrollment */
CREATE TABLE enrollment_states (
  state_id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  public_key BLOB NOT NULL,
  provider_subject TEXT,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  confirmation_hash TEXT,
  CHECK (consumed_at IS NULL OR consumed_at <= expires_at)
);
CREATE INDEX idx_enrollment_states_expiry ON enrollment_states(expires_at) WHERE consumed_at IS NULL;
CREATE TRIGGER guard_enrollment_consumption BEFORE UPDATE OF consumed_at ON enrollment_states
WHEN OLD.consumed_at IS NOT NULL OR OLD.expires_at <= NEW.consumed_at OR
     (OLD.provider_subject IS NOT NULL AND OLD.provider_subject != NEW.provider_subject)
BEGIN SELECT RAISE(ABORT, 'enrollment_state_invalid'); END;

CREATE TABLE discord_oauth_states (
  state_id TEXT PRIMARY KEY NOT NULL,
  browser_hash TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  public_key BLOB NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  confirmation_hash TEXT
);
CREATE INDEX idx_discord_oauth_states_expiry ON discord_oauth_states(expires_at);

CREATE TABLE oauth_usage_counters (
  resource TEXT NOT NULL CHECK (resource IN ('worker_requests', 'd1_writes')),
  period TEXT NOT NULL,
  units INTEGER NOT NULL CHECK (units >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (resource, period)
) WITHOUT ROWID;

CREATE TABLE discord_membership (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  authority TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('eligible', 'ineligible')),
  checked_at INTEGER NOT NULL CHECK (checked_at >= 0),
  PRIMARY KEY (contributor_id, authority)
) WITHOUT ROWID;
CREATE TABLE discord_membership_refresh (
  authority TEXT PRIMARY KEY,
  lease_id TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0 CHECK (lease_until >= 0),
  retry_at INTEGER NOT NULL DEFAULT 0 CHECK (retry_at >= 0)
) WITHOUT ROWID;

CREATE TABLE profile_name_changes (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  operation_id TEXT NOT NULL,
  changed_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, operation_id)
) WITHOUT ROWID;
CREATE INDEX idx_profile_name_changes_expiry ON profile_name_changes(changed_at);

/* Replay and idempotency */
CREATE TABLE request_nonces (
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  nonce TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  contributor_id TEXT REFERENCES contributors(contributor_id),
  PRIMARY KEY (key_id, nonce)
);
CREATE INDEX idx_request_nonces_expiry ON request_nonces(expires_at);
CREATE TRIGGER request_nonce_active_key BEFORE INSERT ON request_nonces
BEGIN
  SELECT RAISE(ABORT, 'key_inactive') WHERE NOT EXISTS (
    SELECT 1 FROM installation_keys AS key
    JOIN installations AS installation ON installation.installation_id = key.installation_id
    JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
    WHERE key.key_id = NEW.key_id AND key.state = 'active' AND contributor.state = 'active'
      AND contributor.contributor_id = NEW.contributor_id);
END;

CREATE TABLE operations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  kind TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'failed')),
  result TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, kind, operation_id),
  CHECK ((state = 'completed' AND result IS NOT NULL) OR (state != 'completed' AND result IS NULL))
);
CREATE INDEX idx_operations_state ON operations(state, updated_at);
CREATE TRIGGER operation_digest_immutable BEFORE INSERT ON operations
WHEN EXISTS (SELECT 1 FROM operations WHERE contributor_id = NEW.contributor_id
  AND kind = NEW.kind AND operation_id = NEW.operation_id AND request_digest != NEW.request_digest)
BEGIN SELECT RAISE(ABORT, 'operation_conflict'); END;

/* Observations and per-window rate counters */
CREATE TABLE usage_observations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  resource TEXT NOT NULL CHECK (resource IN ('worker_requests', 'd1_writes')),
  period TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied', 'retried')),
  occurrences INTEGER NOT NULL DEFAULT 0 CHECK (occurrences >= 0),
  units INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, resource, period, outcome)
);
CREATE INDEX idx_usage_observations_resource_period ON usage_observations (resource, period);
CREATE TABLE rate_counters (
  scope TEXT NOT NULL CHECK (length(scope) BETWEEN 1 AND 256),
  window_start INTEGER NOT NULL CHECK (window_start >= 0),
  requests INTEGER NOT NULL CHECK (requests > 0),
  PRIMARY KEY (scope, window_start)
) WITHOUT ROWID;
CREATE INDEX idx_rate_counters_expiry ON rate_counters(window_start);

/* Stored submission bytes are charged against the operator storage policy. */
CREATE TABLE artifact_storage_policy (
  policy_id INTEGER PRIMARY KEY CHECK (policy_id = 1),
  global_bytes INTEGER NOT NULL CHECK (global_bytes BETWEEN 1 AND 350000000),
  contributor_bytes INTEGER NOT NULL CHECK (contributor_bytes > 0 AND contributor_bytes <= global_bytes)
);
INSERT INTO artifact_storage_policy VALUES (1, 350000000, 7000000);
CREATE TABLE artifact_storage_usage (
  scope_id TEXT PRIMARY KEY NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0)
);

/* Moderation framework */
CREATE TABLE moderation_versions (
  resource TEXT PRIMARY KEY NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0)
) WITHOUT ROWID;
CREATE TABLE moderation_epoch (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), epoch INTEGER NOT NULL CHECK (epoch >= 0));
INSERT INTO moderation_epoch VALUES (1, 0);
CREATE TABLE operator_request_counts (
  subject TEXT NOT NULL,
  minute INTEGER NOT NULL,
  requests INTEGER NOT NULL CHECK (requests > 0),
  PRIMARY KEY (subject, minute)
) WITHOUT ROWID;
CREATE TABLE moderation_actions (
  action_id TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  resource TEXT NOT NULL,
  target_id TEXT NOT NULL,
  command TEXT NOT NULL CHECK (command IN ('quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote',
    'restore_vote', 'resolve_report', 'recompute')),
  expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
  request_json TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'conflict', 'denied')),
  error_code TEXT CHECK ((outcome = 'applied') = (error_code IS NULL)),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE INDEX idx_moderation_audit ON moderation_actions(resource, created_at, action_id);
CREATE TRIGGER guard_audit_update BEFORE UPDATE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_audit_delete BEFORE DELETE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_moderation BEFORE INSERT ON moderation_actions
WHEN NEW.outcome = 'applied' AND NEW.command != 'recompute'
  AND coalesce((SELECT revision FROM moderation_versions WHERE resource = NEW.resource), 0) != NEW.expected_revision
BEGIN SELECT RAISE(ABORT, 'moderation_conflict'); END;
CREATE TRIGGER version_moderation AFTER INSERT ON moderation_actions
WHEN NEW.outcome = 'applied' AND NEW.command != 'recompute'
BEGIN INSERT INTO moderation_versions VALUES (NEW.resource, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;

/* Works, aliases and versions (contract §3) */
CREATE TABLE works (
  work_id TEXT PRIMARY KEY NOT NULL CHECK (length(work_id) BETWEEN 1 AND 128 AND work_id NOT GLOB '*[^A-Za-z0-9_.-]*'),
  kind TEXT NOT NULL CHECK (kind IN ('movie', 'episode')),
  catalog_seq INTEGER NOT NULL DEFAULT 0 CHECK (catalog_seq >= 0),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TRIGGER guard_work_catalog_seq BEFORE UPDATE OF catalog_seq ON works
WHEN NEW.catalog_seq != OLD.catalog_seq + 1
BEGIN SELECT RAISE(ABORT, 'catalog_write_conflict'); END;
CREATE TABLE work_identifiers (
  provider TEXT NOT NULL CHECK (provider IN ('tmdb', 'tvdb')),
  kind TEXT NOT NULL CHECK (kind IN ('movie', 'episode')),
  value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 12 AND value NOT GLOB '*[^0-9]*'),
  season INTEGER NOT NULL CHECK (season BETWEEN -1 AND 9999),
  episode INTEGER NOT NULL CHECK (episode BETWEEN -1 AND 9999),
  last_episode INTEGER NOT NULL CHECK (last_episode BETWEEN -1 AND 10014),
  work_id TEXT NOT NULL REFERENCES works(work_id),
  PRIMARY KEY (provider, kind, value, season, episode, last_episode),
  CHECK ((kind = 'movie') = (season = -1 AND episode = -1 AND last_episode = -1)),
  CHECK (kind = 'movie' OR (season >= 0 AND episode >= 0))
) WITHOUT ROWID;
CREATE INDEX idx_work_identifiers_work ON work_identifiers(work_id);
CREATE TRIGGER guard_work_identifier_update BEFORE UPDATE ON work_identifiers
BEGIN SELECT RAISE(ABORT, 'immutable_work_identifier'); END;
CREATE TABLE work_identifier_conflicts (
  conflict_id TEXT PRIMARY KEY NOT NULL,
  submission_id TEXT NOT NULL CHECK (length(submission_id) BETWEEN 1 AND 128),
  provider TEXT NOT NULL CHECK (provider IN ('tmdb', 'tvdb')),
  value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 12 AND value NOT GLOB '*[^0-9]*'),
  existing_work_id TEXT NOT NULL REFERENCES works(work_id),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TABLE work_titles (
  work_id TEXT NOT NULL REFERENCES works(work_id),
  normalized TEXT NOT NULL CHECK (length(normalized) BETWEEN 1 AND 256),
  year INTEGER NOT NULL CHECK (year = -1 OR year BETWEEN 1870 AND 2200),
  title TEXT NOT NULL CHECK (length(CAST(title AS BLOB)) BETWEEN 1 AND 512),
  observations INTEGER NOT NULL CHECK (observations >= 1),
  PRIMARY KEY (work_id, normalized, year)
) WITHOUT ROWID;
CREATE INDEX idx_work_titles_key ON work_titles(normalized, year);

CREATE TABLE aliases (
  alias_id TEXT PRIMARY KEY NOT NULL CHECK (length(alias_id) BETWEEN 1 AND 64 AND alias_id NOT GLOB '*[^a-z0-9_]*'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 64),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TABLE alias_spellings (
  normalized TEXT PRIMARY KEY NOT NULL CHECK (length(normalized) <= 64),
  alias_id TEXT NOT NULL REFERENCES aliases(alias_id),
  source TEXT NOT NULL CHECK (source IN ('seed', 'moderator'))
) WITHOUT ROWID;
CREATE TABLE alias_suggestions (
  suggestion_id TEXT PRIMARY KEY NOT NULL CHECK (length(suggestion_id) BETWEEN 1 AND 128),
  normalized TEXT NOT NULL CHECK (length(normalized) BETWEEN 1 AND 64),
  text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 64),
  submission_id TEXT NOT NULL CHECK (length(submission_id) BETWEEN 1 AND 128),
  state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'rejected')),
  alias_id TEXT REFERENCES aliases(alias_id),
  created_at INTEGER NOT NULL CHECK (created_at >= 0),
  CHECK ((state = 'approved') = (alias_id IS NOT NULL))
);
CREATE INDEX idx_alias_suggestions_queue ON alias_suggestions(state, suggestion_id);
INSERT INTO aliases (alias_id, label, created_at) VALUES
  ('theatrical', 'Theatrical', 0), ('directors_cut', 'Director''s Cut', 0),
  ('extended', 'Extended Edition', 0), ('unrated', 'Unrated', 0);
INSERT INTO alias_spellings (normalized, alias_id, source) VALUES
  ('', 'theatrical', 'seed'), ('theatrical', 'theatrical', 'seed'), ('theatrical cut', 'theatrical', 'seed'),
  ('theatrical version', 'theatrical', 'seed'), ('theatrical edition', 'theatrical', 'seed'),
  ('theatrical release', 'theatrical', 'seed'), ('original theatrical', 'theatrical', 'seed'),
  ('original theatrical cut', 'theatrical', 'seed'),
  ('directors cut', 'directors_cut', 'seed'), ('directors edition', 'directors_cut', 'seed'),
  ('directors version', 'directors_cut', 'seed'),
  ('extended edition', 'extended', 'seed'), ('extended', 'extended', 'seed'), ('extended cut', 'extended', 'seed'),
  ('extended version', 'extended', 'seed'),
  ('unrated', 'unrated', 'seed'), ('unrated cut', 'unrated', 'seed'), ('unrated edition', 'unrated', 'seed'),
  ('unrated version', 'unrated', 'seed');

CREATE TABLE versions (
  version_id TEXT PRIMARY KEY NOT NULL CHECK (length(version_id) BETWEEN 1 AND 128 AND version_id NOT GLOB '*[^A-Za-z0-9_.-]*'),
  work_id TEXT NOT NULL REFERENCES works(work_id),
  alias_id TEXT REFERENCES aliases(alias_id),
  label_text TEXT CHECK (label_text IS NULL OR length(label_text) BETWEEN 1 AND 64),
  status TEXT NOT NULL CHECK (status IN ('confirmed', 'candidate', 'merged_into')),
  merged_into TEXT REFERENCES versions(version_id),
  runtime_ms INTEGER CHECK (runtime_ms IS NULL OR runtime_ms BETWEEN 1 AND 86400000),
  trust REAL NOT NULL CHECK (trust BETWEEN 0 AND 1),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  reference_submission_id TEXT,
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
  created_seq INTEGER NOT NULL UNIQUE CHECK (created_seq >= 1),
  created_at INTEGER NOT NULL CHECK (created_at >= 0),
  CHECK ((status = 'merged_into') = (merged_into IS NOT NULL)),
  CHECK (merged_into IS NULL OR merged_into != version_id)
);
CREATE INDEX idx_versions_work_status ON versions(work_id, status);
CREATE INDEX idx_versions_work_label ON versions(work_id, alias_id, label_text);
CREATE INDEX idx_versions_status ON versions(status, version_id);
CREATE INDEX idx_versions_merged ON versions(merged_into);
CREATE TRIGGER guard_version_identity BEFORE UPDATE ON versions
WHEN NEW.version_id != OLD.version_id OR NEW.work_id != OLD.work_id OR NEW.created_seq != OLD.created_seq
  OR NEW.created_at != OLD.created_at OR NEW.revision < OLD.revision OR OLD.status = 'merged_into' AND NEW.status != 'merged_into'
BEGIN SELECT RAISE(ABORT, 'immutable_version'); END;
CREATE TRIGGER guard_version_confirmed_label BEFORE INSERT ON versions
WHEN NEW.status = 'confirmed' AND EXISTS (SELECT 1 FROM versions v WHERE v.work_id = NEW.work_id
  AND v.status != 'merged_into' AND (
    coalesce(v.alias_id, CASE WHEN v.label_text IS NULL THEN 'theatrical' END)
      = coalesce(NEW.alias_id, CASE WHEN NEW.label_text IS NULL THEN 'theatrical' END)
    OR (coalesce(v.alias_id, CASE WHEN v.label_text IS NULL THEN 'theatrical' END) IS NULL
      AND coalesce(NEW.alias_id, CASE WHEN NEW.label_text IS NULL THEN 'theatrical' END) IS NULL
      AND v.label_text = NEW.label_text)))
BEGIN SELECT RAISE(ABORT, 'version_label_conflict'); END;
CREATE TRIGGER guard_version_merge_target BEFORE UPDATE OF merged_into ON versions
WHEN NEW.merged_into IS NOT NULL AND NOT EXISTS (SELECT 1 FROM versions t WHERE t.version_id = NEW.merged_into
  AND t.status != 'merged_into' AND t.work_id = NEW.work_id)
BEGIN SELECT RAISE(ABORT, 'merge_invalid'); END;
CREATE TABLE version_match_keys (
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  type TEXT NOT NULL CHECK (type IN ('sha256_full', 'sha256_sampled', 'md5_quick', 'audio_stream', 'video_stream')),
  algorithm TEXT NOT NULL CHECK (length(algorithm) <= 128),
  value TEXT NOT NULL CHECK (length(value) IN (32, 64) AND value NOT GLOB '*[^0-9a-f]*'),
  size_bytes INTEGER NOT NULL CHECK (size_bytes = -1 OR size_bytes >= 0),
  is_reference INTEGER NOT NULL CHECK (is_reference IN (0, 1)),
  observations INTEGER NOT NULL CHECK (observations >= 1),
  PRIMARY KEY (version_id, type, algorithm, value, size_bytes)
) WITHOUT ROWID;
CREATE INDEX idx_version_match_keys_lookup ON version_match_keys(type, algorithm, value, size_bytes);

CREATE TABLE version_acceptances (
  submission_id TEXT PRIMARY KEY NOT NULL CHECK (length(submission_id) BETWEEN 1 AND 128),
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  accepted_seq INTEGER NOT NULL UNIQUE CHECK (accepted_seq >= 1),
  tier INTEGER CHECK (tier IS NULL OR tier IN (1, 2, 3)),
  founder INTEGER NOT NULL CHECK (founder IN (0, 1)),
  duration_ms INTEGER NOT NULL CHECK (duration_ms BETWEEN 1 AND 86400000),
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
  merged_from TEXT REFERENCES versions(version_id),
  accepted_at INTEGER NOT NULL CHECK (accepted_at >= 0)
);
CREATE INDEX idx_version_acceptances_latest ON version_acceptances(version_id, contributor_id, accepted_seq);
CREATE INDEX idx_version_acceptances_contributor ON version_acceptances(contributor_id, accepted_at);
CREATE VIEW version_counted_acceptances AS
SELECT a.submission_id, a.version_id, a.contributor_id, a.accepted_seq, a.tier, a.founder, a.duration_ms
FROM version_acceptances a WHERE a.quarantined = 0 AND a.accepted_seq = (SELECT max(b.accepted_seq)
  FROM version_acceptances b WHERE b.version_id = a.version_id AND b.contributor_id = a.contributor_id AND b.quarantined = 0);

CREATE TABLE catalog_moderation_actions (
  action_id TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  resource TEXT NOT NULL,
  target_id TEXT NOT NULL,
  command TEXT NOT NULL CHECK (command IN ('confirm_version', 'merge_version', 'approve_alias', 'reject_alias')),
  expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
  request_json TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'conflict', 'denied')),
  error_code TEXT CHECK ((outcome = 'applied') = (error_code IS NULL)),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE INDEX idx_catalog_moderation_audit ON catalog_moderation_actions(resource, created_at, action_id);
CREATE TRIGGER guard_catalog_audit_update BEFORE UPDATE ON catalog_moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_catalog_audit_delete BEFORE DELETE ON catalog_moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_catalog_moderation BEFORE INSERT ON catalog_moderation_actions
WHEN NEW.outcome = 'applied'
  AND coalesce((SELECT revision FROM moderation_versions WHERE resource = NEW.resource), 0) != NEW.expected_revision
BEGIN SELECT RAISE(ABORT, 'moderation_conflict'); END;
CREATE TRIGGER version_catalog_moderation AFTER INSERT ON catalog_moderation_actions
WHEN NEW.outcome = 'applied'
BEGIN INSERT INTO moderation_versions VALUES (NEW.resource, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;

/* Signed submissions (contract §5, §6.1). Raw bytes are stored once and never rewritten. */
CREATE TABLE submissions (
  submission_id TEXT PRIMARY KEY NOT NULL REFERENCES version_acceptances(submission_id),
  digest TEXT NOT NULL UNIQUE CHECK (length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'),
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  installation_id TEXT NOT NULL REFERENCES installations(installation_id),
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  accepted_seq INTEGER NOT NULL UNIQUE CHECK (accepted_seq >= 1),
  tier INTEGER CHECK (tier IS NULL OR tier IN (1, 2, 3)),
  artifact_bytes BLOB NOT NULL CHECK (typeof(artifact_bytes) = 'blob' AND length(artifact_bytes) BETWEEN 1 AND 1000000),
  manifest_bytes BLOB NOT NULL CHECK (typeof(manifest_bytes) = 'blob' AND length(manifest_bytes) BETWEEN 1 AND 4096),
  envelope_bytes BLOB NOT NULL CHECK (typeof(envelope_bytes) = 'blob' AND length(envelope_bytes) BETWEEN 1 AND 256),
  match_keys_json TEXT NOT NULL CHECK (json_valid(match_keys_json)),
  alignment_hint_json TEXT CHECK (alignment_hint_json IS NULL OR json_valid(alignment_hint_json)),
  fit_json TEXT CHECK (fit_json IS NULL OR json_valid(fit_json)),
  state TEXT NOT NULL CHECK (state IN ('counted', 'superseded', 'quarantined')),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE INDEX idx_submissions_version ON submissions(version_id, contributor_id, accepted_seq);
CREATE INDEX idx_submissions_version_page ON submissions(version_id, submission_id);
CREATE INDEX idx_submissions_contributor ON submissions(contributor_id, created_at);
CREATE TRIGGER guard_submission_update BEFORE UPDATE OF submission_id, digest, contributor_id, installation_id, key_id,
  accepted_seq, artifact_bytes, manifest_bytes, envelope_bytes, match_keys_json, alignment_hint_json, created_at ON submissions
BEGIN SELECT RAISE(ABORT, 'immutable_submission'); END;
CREATE TRIGGER guard_submission_delete BEFORE DELETE ON submissions
BEGIN SELECT RAISE(ABORT, 'immutable_submission'); END;
CREATE TRIGGER charge_submission_storage AFTER INSERT ON submissions
BEGIN
  INSERT INTO artifact_storage_usage VALUES ('global',
    length(NEW.artifact_bytes) + length(NEW.manifest_bytes) + length(NEW.envelope_bytes))
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
  INSERT INTO artifact_storage_usage VALUES ('contributor:' || NEW.contributor_id,
    length(NEW.artifact_bytes) + length(NEW.manifest_bytes) + length(NEW.envelope_bytes))
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
  SELECT RAISE(ABORT, 'artifact_storage_exhausted') WHERE NOT EXISTS (
    SELECT 1 FROM artifact_storage_policy WHERE policy_id = 1
      AND (SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global') <= global_bytes
      AND (SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'contributor:' || NEW.contributor_id) <= contributor_bytes);
END;

CREATE TABLE submission_entries (
  submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
  entry_id TEXT NOT NULL CHECK (length(entry_id) BETWEEN 1 AND 32),
  kind TEXT NOT NULL CHECK (kind IN ('support', 'dispute', 'coverage_negative')),
  proposed_filter_id TEXT CHECK (proposed_filter_id IS NULL OR length(proposed_filter_id) BETWEEN 1 AND 128),
  start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
  end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
  category TEXT NOT NULL,
  subcategory TEXT,
  severity TEXT CHECK (severity IS NULL OR severity IN ('low', 'medium', 'high')),
  action TEXT CHECK (action IS NULL OR action IN ('skip', 'mute')),
  origin TEXT NOT NULL CHECK (origin IN ('model', 'manual', 'downloaded')),
  manual_review INTEGER NOT NULL CHECK (manual_review IN (0, 1)),
  locally_verified INTEGER NOT NULL CHECK (locally_verified IN (0, 1)),
  model_version TEXT,
  aligned_start_ms INTEGER,
  aligned_end_ms INTEGER,
  weight INTEGER NOT NULL DEFAULT 0 CHECK (weight IN (0, 1)),
  ignored_reason TEXT CHECK (ignored_reason IS NULL OR ignored_reason IN ('unverified_reshare', 'duplicate')),
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
  PRIMARY KEY (submission_id, entry_id)
) WITHOUT ROWID;
CREATE TABLE submission_coverage (
  submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
  category TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
  end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
  PRIMARY KEY (submission_id, category, ordinal)
) WITHOUT ROWID;

/* Master filter sets (contract §6) */
CREATE TABLE filter_registry (
  filter_id TEXT PRIMARY KEY NOT NULL CHECK (length(filter_id) BETWEEN 1 AND 128),
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  created_seq INTEGER NOT NULL CHECK (created_seq >= 1),
  incumbent_subcategory TEXT,
  incumbent_action TEXT CHECK (incumbent_action IS NULL OR incumbent_action IN ('skip', 'mute')),
  state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'quarantined')),
  merged_into TEXT,
  quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1))
);
CREATE INDEX idx_filter_registry_version ON filter_registry(version_id);
CREATE INDEX idx_filter_registry_version_page ON filter_registry(version_id, filter_id);
CREATE TABLE master_filters (
  filter_id TEXT PRIMARY KEY NOT NULL REFERENCES filter_registry(filter_id),
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  category TEXT NOT NULL,
  subcategory TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
  action TEXT NOT NULL CHECK (action IN ('skip', 'mute')),
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  spread_start_ms INTEGER NOT NULL,
  spread_end_ms INTEGER NOT NULL,
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  contributor_count INTEGER NOT NULL,
  attestations INTEGER NOT NULL,
  upvotes INTEGER NOT NULL,
  downvotes INTEGER NOT NULL,
  disputes INTEGER NOT NULL,
  coverage_negatives INTEGER NOT NULL,
  low_confidence INTEGER NOT NULL CHECK (low_confidence IN (0, 1)),
  served INTEGER NOT NULL CHECK (served IN (0, 1)),
  subcategory_tally_json TEXT NOT NULL,
  severity_tally_json TEXT NOT NULL,
  action_tally_json TEXT NOT NULL
);
CREATE INDEX idx_master_filters_version ON master_filters(version_id, served);
CREATE TABLE filter_attestations (
  submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
  entry_id TEXT NOT NULL,
  filter_id TEXT,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  kind TEXT NOT NULL CHECK (kind IN ('support', 'dispute', 'coverage_negative')),
  implicit INTEGER NOT NULL CHECK (implicit IN (0, 1)),
  counted INTEGER NOT NULL CHECK (counted IN (0, 1)),
  prior_filter_id TEXT,
  PRIMARY KEY (submission_id, entry_id)
) WITHOUT ROWID;
CREATE INDEX idx_filter_attestations_filter ON filter_attestations(filter_id, contributor_id);

CREATE TABLE master_revisions (
  version_id TEXT NOT NULL REFERENCES versions(version_id),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  snapshot_digest TEXT NOT NULL CHECK (length(snapshot_digest) = 64 AND snapshot_digest NOT GLOB '*[^0-9a-f]*'),
  generated_at INTEGER NOT NULL CHECK (generated_at >= 0),
  snapshot_bytes BLOB CHECK (snapshot_bytes IS NULL OR (typeof(snapshot_bytes) = 'blob' AND length(snapshot_bytes) <= 2000000)),
  PRIMARY KEY (version_id, revision)
);
CREATE TRIGGER guard_master_revision_update BEFORE UPDATE ON master_revisions
WHEN NEW.version_id != OLD.version_id OR NEW.revision != OLD.revision OR NEW.snapshot_digest != OLD.snapshot_digest
  OR NEW.generated_at != OLD.generated_at OR (NEW.snapshot_bytes IS NOT NULL AND NEW.snapshot_bytes IS NOT OLD.snapshot_bytes)
BEGIN SELECT RAISE(ABORT, 'immutable_master_revision'); END;
CREATE TABLE master_revision_filters (
  version_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  filter_id TEXT NOT NULL,
  PRIMARY KEY (version_id, revision, filter_id),
  FOREIGN KEY (version_id, revision) REFERENCES master_revisions(version_id, revision)
) WITHOUT ROWID;
CREATE INDEX idx_master_revision_filters_filter ON master_revision_filters(filter_id);
CREATE TABLE download_receipts (
  receipt_id TEXT PRIMARY KEY NOT NULL CHECK (length(receipt_id) = 64 AND receipt_id NOT GLOB '*[^0-9a-f]*'),
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  version_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  snapshot_digest TEXT NOT NULL,
  service_key_id TEXT NOT NULL,
  issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
  FOREIGN KEY (version_id, revision) REFERENCES master_revisions(version_id, revision)
);
CREATE INDEX idx_download_receipts_contributor ON download_receipts(contributor_id, version_id);
CREATE TRIGGER guard_download_receipt_update BEFORE UPDATE ON download_receipts
BEGIN SELECT RAISE(ABORT, 'immutable_download_receipt'); END;
CREATE TABLE filter_votes (
  filter_id TEXT NOT NULL REFERENCES filter_registry(filter_id),
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
  receipt_id TEXT NOT NULL REFERENCES download_receipts(receipt_id),
  excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
  updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
  PRIMARY KEY (filter_id, contributor_id)
) WITHOUT ROWID;
CREATE INDEX idx_filter_votes_contributor ON filter_votes(contributor_id);
CREATE TABLE sync_runs (
  installation_id TEXT NOT NULL REFERENCES installations(installation_id),
  run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128 AND run_id NOT GLOB '*[^A-Za-z0-9_.-]*'),
  started_at INTEGER NOT NULL CHECK (started_at >= 0),
  batches INTEGER NOT NULL CHECK (batches >= 1),
  PRIMARY KEY (installation_id, run_id)
) WITHOUT ROWID;
CREATE INDEX idx_sync_runs_started ON sync_runs(installation_id, started_at);

/* Reports (contract §7.6) */
CREATE TABLE reports (
  report_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  target_type TEXT NOT NULL CHECK (target_type IN ('filter', 'version')),
  target_id TEXT NOT NULL CHECK (length(target_id) BETWEEN 1 AND 128),
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  reason TEXT NOT NULL CHECK (reason IN ('spam', 'inaccurate', 'privacy', 'rights', 'other')),
  note TEXT NOT NULL CHECK (length(note) <= 1000),
  state TEXT NOT NULL CHECK (state IN ('open', 'resolved')),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE UNIQUE INDEX uq_open_report ON reports(contributor_id, target_type, target_id) WHERE state = 'open';
CREATE INDEX idx_report_daily ON reports(contributor_id, created_at);
CREATE INDEX idx_report_queue ON reports(state, report_id);
CREATE TRIGGER guard_report_update BEFORE UPDATE ON reports
WHEN NEW.report_id != OLD.report_id OR NEW.contributor_id != OLD.contributor_id
  OR NEW.target_type != OLD.target_type OR NEW.target_id != OLD.target_id OR NEW.key_id != OLD.key_id
  OR NEW.reason != OLD.reason OR NEW.note != OLD.note OR NEW.created_at != OLD.created_at
  OR OLD.state != 'open' OR NEW.state != 'resolved'
BEGIN SELECT RAISE(ABORT, 'immutable_report'); END;
CREATE TRIGGER guard_report_delete BEFORE DELETE ON reports
BEGIN SELECT RAISE(ABORT, 'immutable_report'); END;
