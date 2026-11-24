CREATE TABLE master_works (
  work_id TEXT PRIMARY KEY NOT NULL CHECK (length(work_id) BETWEEN 1 AND 128 AND work_id NOT GLOB '*[^A-Za-z0-9_.-]*'),
  kind TEXT NOT NULL CHECK (kind IN ('movie', 'episode')),
  catalog_seq INTEGER NOT NULL DEFAULT 0 CHECK (catalog_seq >= 0),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TRIGGER guard_master_work_catalog_seq BEFORE UPDATE OF catalog_seq ON master_works
WHEN NEW.catalog_seq != OLD.catalog_seq + 1
BEGIN SELECT RAISE(ABORT, 'catalog_write_conflict'); END;
CREATE TABLE master_work_identifiers (
  provider TEXT NOT NULL CHECK (provider IN ('tmdb', 'tvdb')),
  kind TEXT NOT NULL CHECK (kind IN ('movie', 'episode')),
  value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 12 AND value NOT GLOB '*[^0-9]*'),
  season INTEGER NOT NULL CHECK (season BETWEEN -1 AND 9999),
  episode INTEGER NOT NULL CHECK (episode BETWEEN -1 AND 9999),
  last_episode INTEGER NOT NULL CHECK (last_episode BETWEEN -1 AND 10014),
  work_id TEXT NOT NULL REFERENCES master_works(work_id),
  PRIMARY KEY (provider, kind, value, season, episode, last_episode),
  CHECK ((kind = 'movie') = (season = -1 AND episode = -1 AND last_episode = -1)),
  CHECK (kind = 'movie' OR (season >= 0 AND episode >= 0))
) WITHOUT ROWID;
CREATE INDEX idx_master_work_identifiers_work ON master_work_identifiers(work_id);
CREATE TRIGGER guard_master_work_identifier_update BEFORE UPDATE ON master_work_identifiers
BEGIN SELECT RAISE(ABORT, 'immutable_work_identifier'); END;
CREATE TABLE work_identifier_conflicts (
  conflict_id TEXT PRIMARY KEY NOT NULL,
  submission_id TEXT NOT NULL CHECK (length(submission_id) BETWEEN 1 AND 128),
  provider TEXT NOT NULL CHECK (provider IN ('tmdb', 'tvdb')),
  value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 12 AND value NOT GLOB '*[^0-9]*'),
  existing_work_id TEXT NOT NULL REFERENCES master_works(work_id),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TABLE work_titles (
  work_id TEXT NOT NULL REFERENCES master_works(work_id),
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
  work_id TEXT NOT NULL REFERENCES master_works(work_id),
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
