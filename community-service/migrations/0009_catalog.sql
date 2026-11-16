CREATE TABLE works (
  work_id TEXT PRIMARY KEY NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('movie', 'series', 'episode')),
  created_at INTEGER NOT NULL,
  UNIQUE (work_id, entity_type)
);
CREATE TABLE work_identifiers (
  provider TEXT NOT NULL CHECK (provider IN ('tmdb', 'tvdb')),
  entity_type TEXT NOT NULL CHECK (entity_type IN ('movie', 'series', 'episode')),
  value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 64 AND value NOT GLOB '*[^A-Za-z0-9]*'),
  work_id TEXT NOT NULL,
  provenance TEXT NOT NULL CHECK (provenance IN ('user_asserted', 'verified_source', 'operator')),
  evidence_ref TEXT NOT NULL CHECK (length(evidence_ref) BETWEEN 1 AND 128),
  PRIMARY KEY (provider, entity_type, value),
  FOREIGN KEY (work_id, entity_type) REFERENCES works(work_id, entity_type)
);
CREATE INDEX idx_work_identifiers_work ON work_identifiers(work_id);
CREATE TRIGGER guard_unverified_alias BEFORE INSERT ON work_identifiers
WHEN NEW.provenance = 'user_asserted' AND EXISTS (
  SELECT 1 FROM work_identifiers WHERE work_id = NEW.work_id
) AND NOT EXISTS (SELECT 1 FROM work_identifiers WHERE provider = NEW.provider
  AND entity_type = NEW.entity_type AND value = NEW.value AND work_id = NEW.work_id)
BEGIN SELECT RAISE(ABORT, 'unverified_work_alias'); END;
CREATE TRIGGER guard_identifier_conflict BEFORE INSERT ON work_identifiers
WHEN EXISTS (SELECT 1 FROM work_identifiers WHERE provider = NEW.provider
  AND entity_type = NEW.entity_type AND value = NEW.value AND work_id != NEW.work_id)
BEGIN SELECT RAISE(ABORT, 'work_identifier_conflict'); END;
CREATE TRIGGER guard_work_identifier_update BEFORE UPDATE ON work_identifiers
BEGIN SELECT RAISE(ABORT, 'immutable_work_identifier'); END;
CREATE TRIGGER guard_work_identifier_delete BEFORE DELETE ON work_identifiers
BEGIN SELECT RAISE(ABORT, 'immutable_work_identifier'); END;
CREATE TRIGGER guard_work_update BEFORE UPDATE ON works
BEGIN SELECT RAISE(ABORT, 'immutable_work'); END;

CREATE TABLE releases (
  release_id TEXT PRIMARY KEY NOT NULL,
  work_id TEXT NOT NULL REFERENCES works(work_id),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json) AND json_type(evidence_json) = 'object'),
  scene_name TEXT,
  edition TEXT,
  duration_seconds REAL CHECK (duration_seconds > 0),
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_releases_work ON releases(work_id, release_id);
CREATE INDEX idx_releases_name ON releases(work_id, scene_name, release_id);
CREATE TABLE release_work_associations (
  release_id TEXT NOT NULL REFERENCES releases(release_id),
  work_id TEXT NOT NULL REFERENCES works(work_id),
  PRIMARY KEY (release_id, work_id)
);
CREATE INDEX idx_release_work_associations_work ON release_work_associations(work_id, release_id);
CREATE TRIGGER guard_release_association_update BEFORE UPDATE ON release_work_associations
BEGIN SELECT RAISE(ABORT, 'immutable_release_association'); END;
CREATE TRIGGER guard_release_association_delete BEFORE DELETE ON release_work_associations
BEGIN SELECT RAISE(ABORT, 'immutable_release_association'); END;
CREATE TABLE release_episodes (
  release_id TEXT NOT NULL REFERENCES releases(release_id),
  series_work_id TEXT NOT NULL REFERENCES works(work_id),
  scheme TEXT NOT NULL CHECK (scheme IN ('aired', 'absolute', 'dvd', 'anime')),
  season INTEGER NOT NULL CHECK (season >= -1 AND (scheme NOT IN ('aired', 'dvd') OR season >= 0)),
  episode_number INTEGER NOT NULL CHECK (episode_number >= 0),
  role TEXT NOT NULL CHECK (role IN ('number', 'span')),
  position INTEGER NOT NULL CHECK (position >= 0),
  PRIMARY KEY (release_id, series_work_id, role, position)
);
CREATE INDEX idx_release_episodes_lookup ON release_episodes(series_work_id, scheme, season, episode_number, release_id);
CREATE TRIGGER guard_release_episode_series BEFORE INSERT ON release_episodes
WHEN NOT EXISTS (SELECT 1 FROM works WHERE work_id = NEW.series_work_id AND entity_type = 'series')
BEGIN SELECT RAISE(ABORT, 'invalid_series_work'); END;
CREATE TRIGGER guard_release_episode_update BEFORE UPDATE ON release_episodes
BEGIN SELECT RAISE(ABORT, 'immutable_release_episode'); END;
CREATE TRIGGER guard_release_episode_delete BEFORE DELETE ON release_episodes
BEGIN SELECT RAISE(ABORT, 'immutable_release_episode'); END;
CREATE TABLE release_fingerprints (
  release_id TEXT NOT NULL REFERENCES releases(release_id),
  channel TEXT NOT NULL CHECK (channel IN ('file', 'audio', 'video')),
  algorithm TEXT NOT NULL CHECK (length(algorithm) BETWEEN 1 AND 128),
  digest TEXT NOT NULL CHECK (length(digest) BETWEEN 1 AND 128 AND digest NOT GLOB '*[^a-f0-9]*'),
  size_bytes INTEGER CHECK (size_bytes >= 0),
  PRIMARY KEY (release_id, channel, algorithm)
);
CREATE INDEX idx_release_fingerprints_lookup ON release_fingerprints(channel, algorithm, digest, release_id);
CREATE TRIGGER guard_release_update BEFORE UPDATE ON releases
BEGIN SELECT RAISE(ABORT, 'immutable_release'); END;
CREATE TRIGGER guard_release_fingerprint_update BEFORE UPDATE ON release_fingerprints
BEGIN SELECT RAISE(ABORT, 'immutable_release_fingerprint'); END;

CREATE TABLE contributions (
  contribution_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  created_at INTEGER NOT NULL,
  UNIQUE (contribution_id, contributor_id)
);
CREATE TABLE contribution_revisions (
  revision_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id TEXT NOT NULL UNIQUE,
  contribution_id TEXT NOT NULL,
  contributor_id TEXT NOT NULL,
  operation_kind TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  release_id TEXT NOT NULL REFERENCES releases(release_id),
  kind TEXT NOT NULL CHECK (kind IN ('scan', 'manual', 'derived')),
  artifact_schema TEXT NOT NULL CHECK (artifact_schema IN ('community-artifact-1', 'community-artifact-2')),
  artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64 AND artifact_digest NOT GLOB '*[^a-f0-9]*'),
  manifest_digest TEXT NOT NULL CHECK (length(manifest_digest) = 64 AND manifest_digest NOT GLOB '*[^a-f0-9]*'),
  envelope_digest TEXT NOT NULL CHECK (length(envelope_digest) = 64 AND envelope_digest NOT GLOB '*[^a-f0-9]*'),
  artifact_bytes INTEGER NOT NULL CHECK (artifact_bytes > 0),
  manifest_bytes INTEGER NOT NULL CHECK (manifest_bytes > 0),
  envelope_bytes INTEGER NOT NULL CHECK (envelope_bytes > 0),
  object_key TEXT NOT NULL UNIQUE,
  summary_json TEXT NOT NULL CHECK (json_valid(summary_json) AND json_type(summary_json) = 'object' AND json_type(summary_json, '$.findings') IS NULL),
  total_events INTEGER NOT NULL CHECK (total_events >= 0),
  audio_events INTEGER NOT NULL CHECK (audio_events BETWEEN 0 AND total_events),
  video_events INTEGER NOT NULL CHECK (video_events BETWEEN 0 AND total_events),
  lineage_depth INTEGER NOT NULL CHECK (lineage_depth >= 0),
  parent_limit INTEGER NOT NULL CHECK (parent_limit >= 0),
  supersedes TEXT REFERENCES contribution_revisions(revision_id),
  created_at INTEGER NOT NULL,
  FOREIGN KEY (contribution_id, contributor_id) REFERENCES contributions(contribution_id, contributor_id),
  FOREIGN KEY (contributor_id, operation_kind, operation_id) REFERENCES operations(contributor_id, kind, operation_id),
  UNIQUE (contributor_id, operation_kind, operation_id)
);
CREATE INDEX idx_contribution_revisions_release ON contribution_revisions(release_id, revision_sequence);
CREATE INDEX idx_contribution_revisions_author ON contribution_revisions(contributor_id, revision_sequence);
CREATE INDEX idx_contribution_revisions_family ON contribution_revisions(contribution_id, revision_sequence);
CREATE TRIGGER guard_sealed_fingerprint BEFORE INSERT ON release_fingerprints
WHEN EXISTS (SELECT 1 FROM contribution_revisions WHERE release_id = NEW.release_id)
BEGIN SELECT RAISE(ABORT, 'sealed_release'); END;
CREATE TRIGGER guard_sealed_episode BEFORE INSERT ON release_episodes
WHEN EXISTS (SELECT 1 FROM contribution_revisions WHERE release_id = NEW.release_id)
BEGIN SELECT RAISE(ABORT, 'sealed_release'); END;
CREATE TRIGGER guard_sealed_association BEFORE INSERT ON release_work_associations
WHEN EXISTS (SELECT 1 FROM contribution_revisions WHERE release_id = NEW.release_id)
BEGIN SELECT RAISE(ABORT, 'sealed_release'); END;
CREATE TRIGGER guard_fingerprint_delete BEFORE DELETE ON release_fingerprints
BEGIN SELECT RAISE(ABORT, 'immutable_release_fingerprint'); END;
CREATE TABLE contribution_publications (
  revision_id TEXT PRIMARY KEY NOT NULL REFERENCES contribution_revisions(revision_id),
  state TEXT NOT NULL CHECK (state IN ('reserved', 'object_stored', 'published', 'failed', 'expired', 'withdrawn', 'quarantined')),
  object_etag TEXT,
  updated_at INTEGER NOT NULL,
  CHECK (state NOT IN ('object_stored', 'published', 'withdrawn', 'quarantined') OR object_etag IS NOT NULL)
);
CREATE INDEX idx_contribution_publications_state ON contribution_publications(state, updated_at, revision_id);
CREATE TRIGGER guard_publication_delete BEFORE DELETE ON contribution_publications
BEGIN SELECT RAISE(ABORT, 'immutable_publication_identity'); END;
CREATE TABLE contribution_statistics (
  revision_id TEXT PRIMARY KEY NOT NULL REFERENCES contribution_revisions(revision_id),
  imports INTEGER NOT NULL DEFAULT 0 CHECK (imports >= 0),
  likes INTEGER NOT NULL DEFAULT 0 CHECK (likes >= 0),
  dislikes INTEGER NOT NULL DEFAULT 0 CHECK (dislikes >= 0)
);
CREATE TABLE contribution_parents (
  revision_id TEXT NOT NULL REFERENCES contribution_revisions(revision_id),
  parent_revision_id TEXT NOT NULL REFERENCES contribution_revisions(revision_id),
  parent_digest TEXT NOT NULL,
  PRIMARY KEY (revision_id, parent_revision_id)
);
CREATE TRIGGER guard_statistics_identity BEFORE UPDATE OF revision_id ON contribution_statistics
WHEN NEW.revision_id != OLD.revision_id
BEGIN SELECT RAISE(ABORT, 'immutable_statistics_identity'); END;
CREATE INDEX idx_contribution_parents_parent ON contribution_parents(parent_revision_id, revision_id);
CREATE TRIGGER guard_revision_owner BEFORE INSERT ON contribution_revisions
WHEN NOT EXISTS (
  SELECT 1 FROM installation_keys k JOIN installations i USING (installation_id)
  WHERE k.key_id = NEW.key_id AND i.contributor_id = NEW.contributor_id
)
BEGIN SELECT RAISE(ABORT, 'revision_key_owner'); END;
CREATE TRIGGER guard_revision_supersedes AFTER INSERT ON contribution_revisions
WHEN NEW.supersedes IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM contribution_revisions p JOIN contribution_publications s USING (revision_id)
  WHERE p.revision_id = NEW.supersedes AND p.contribution_id = NEW.contribution_id
    AND p.revision_sequence < NEW.revision_sequence AND s.state = 'published'
)
BEGIN SELECT RAISE(ABORT, 'invalid_supersedes'); END;
CREATE TRIGGER guard_revision_update BEFORE UPDATE ON contribution_revisions
BEGIN SELECT RAISE(ABORT, 'immutable_revision'); END;
CREATE TRIGGER guard_revision_delete BEFORE DELETE ON contribution_revisions
BEGIN SELECT RAISE(ABORT, 'immutable_revision'); END;
CREATE TRIGGER guard_contribution_update BEFORE UPDATE ON contributions
BEGIN SELECT RAISE(ABORT, 'immutable_contribution'); END;
CREATE TRIGGER guard_parent_insert BEFORE INSERT ON contribution_parents
WHEN NOT EXISTS (
  SELECT 1 FROM contribution_revisions child
  JOIN contribution_publications cs ON cs.revision_id = child.revision_id
  JOIN contribution_revisions parent ON parent.revision_id = NEW.parent_revision_id
  JOIN contribution_publications ps ON ps.revision_id = parent.revision_id
  WHERE child.revision_id = NEW.revision_id AND cs.state = 'reserved' AND ps.state = 'published'
    AND parent.artifact_digest = NEW.parent_digest
    AND parent.revision_sequence < child.revision_sequence
    AND parent.lineage_depth < child.lineage_depth
    AND (SELECT count(*) FROM contribution_parents WHERE revision_id = NEW.revision_id) < child.parent_limit
)
BEGIN SELECT RAISE(ABORT, 'invalid_parent'); END;
CREATE TRIGGER guard_parent_update BEFORE UPDATE ON contribution_parents
BEGIN SELECT RAISE(ABORT, 'immutable_parent'); END;
CREATE TRIGGER guard_parent_delete BEFORE DELETE ON contribution_parents
BEGIN SELECT RAISE(ABORT, 'immutable_parent'); END;
CREATE TRIGGER guard_publication_initial BEFORE INSERT ON contribution_publications
WHEN NEW.state != 'reserved'
BEGIN SELECT RAISE(ABORT, 'invalid_publication_state'); END;
CREATE TRIGGER guard_publication_transition BEFORE UPDATE ON contribution_publications
WHEN NEW.revision_id != OLD.revision_id OR NEW.updated_at < OLD.updated_at
  OR NOT ((OLD.state = 'reserved' AND NEW.state IN ('object_stored', 'failed', 'expired'))
    OR (OLD.state = 'object_stored' AND NEW.state IN ('published', 'failed', 'expired'))
    OR (OLD.state IN ('published', 'withdrawn', 'quarantined')
      AND NEW.state IN ('published', 'withdrawn', 'quarantined') AND NEW.state != OLD.state))
  OR (OLD.object_etag IS NOT NULL AND NEW.object_etag IS NOT OLD.object_etag)
BEGIN SELECT RAISE(ABORT, 'invalid_publication_state'); END;
CREATE TRIGGER guard_publication_lineage BEFORE UPDATE ON contribution_publications
WHEN NEW.state = 'object_stored' AND (
  SELECT lineage_depth FROM contribution_revisions WHERE revision_id = NEW.revision_id
) != (SELECT coalesce(max(p.lineage_depth) + 1, 0) FROM contribution_parents e
  JOIN contribution_revisions p ON p.revision_id = e.parent_revision_id WHERE e.revision_id = NEW.revision_id)
BEGIN SELECT RAISE(ABORT, 'invalid_lineage_depth'); END;