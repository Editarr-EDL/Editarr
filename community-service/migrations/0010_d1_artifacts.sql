/* Preserve prior schema and accounting history; all new artifact bytes live in D1. */
CREATE TABLE artifact_storage_policy (
  policy_id INTEGER PRIMARY KEY CHECK (policy_id = 1),
  global_bytes INTEGER NOT NULL CHECK (global_bytes BETWEEN 1 AND 350000000),
  contributor_bytes INTEGER NOT NULL CHECK (contributor_bytes > 0 AND contributor_bytes <= global_bytes)
);
/* Payload budget leaves 150 MB of the Free database limit for indexes and other rows. */
INSERT INTO artifact_storage_policy VALUES (1, 350000000, 7000000);
CREATE TABLE artifact_storage_usage (
  scope_id TEXT PRIMARY KEY NOT NULL,
  bytes INTEGER NOT NULL CHECK (bytes >= 0)
);
CREATE TABLE artifact_payloads (
  object_key TEXT PRIMARY KEY NOT NULL REFERENCES contribution_revisions(object_key),
  artifact BLOB NOT NULL CHECK (typeof(artifact) = 'blob' AND length(artifact) > 0),
  manifest BLOB NOT NULL CHECK (typeof(manifest) = 'blob' AND length(manifest) > 0),
  envelope BLOB NOT NULL CHECK (typeof(envelope) = 'blob' AND length(envelope) BETWEEN 1 AND 256),
  artifact_digest TEXT NOT NULL,
  manifest_digest TEXT NOT NULL,
  envelope_digest TEXT NOT NULL,
  CHECK (length(artifact) + length(manifest) + length(envelope) <= 1900000)
);
CREATE TRIGGER guard_payload_insert BEFORE INSERT ON artifact_payloads
WHEN NOT EXISTS (SELECT 1 FROM contribution_revisions r WHERE r.object_key = NEW.object_key
  AND r.artifact_digest = NEW.artifact_digest AND r.manifest_digest = NEW.manifest_digest
  AND r.envelope_digest = NEW.envelope_digest AND r.artifact_bytes = length(NEW.artifact)
  AND r.manifest_bytes = length(NEW.manifest) AND r.envelope_bytes = length(NEW.envelope))
BEGIN SELECT RAISE(ABORT, 'artifact_metadata_mismatch'); END;
CREATE TRIGGER guard_payload_update BEFORE UPDATE ON artifact_payloads
WHEN NEW.object_key IS NOT OLD.object_key OR NEW.artifact IS NOT OLD.artifact
  OR NEW.manifest IS NOT OLD.manifest OR NEW.envelope IS NOT OLD.envelope
  OR NEW.artifact_digest IS NOT OLD.artifact_digest OR NEW.manifest_digest IS NOT OLD.manifest_digest
  OR NEW.envelope_digest IS NOT OLD.envelope_digest
BEGIN SELECT RAISE(ABORT, 'object_conflict'); END;
CREATE TRIGGER guard_payload_delete BEFORE DELETE ON artifact_payloads
BEGIN SELECT RAISE(ABORT, 'immutable_artifact'); END;
CREATE TRIGGER charge_payload_storage AFTER INSERT ON artifact_payloads
BEGIN
  INSERT INTO artifact_storage_usage VALUES ('global', length(NEW.artifact) + length(NEW.manifest) + length(NEW.envelope))
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
  INSERT INTO artifact_storage_usage SELECT 'contributor:' || contributor_id,
    length(NEW.artifact) + length(NEW.manifest) + length(NEW.envelope)
    FROM contribution_revisions WHERE object_key = NEW.object_key
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
  SELECT RAISE(ABORT, 'artifact_storage_exhausted') WHERE NOT EXISTS (
    SELECT 1 FROM artifact_storage_policy WHERE policy_id = 1
      AND (SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global') <= global_bytes
      AND (SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'contributor:' ||
        (SELECT contributor_id FROM contribution_revisions WHERE object_key = NEW.object_key)) <= contributor_bytes);
END;
CREATE TRIGGER guard_publication_payload BEFORE UPDATE ON contribution_publications
WHEN NEW.state IN ('object_stored', 'published') AND NOT EXISTS (
  SELECT 1 FROM artifact_payloads p JOIN contribution_revisions r USING (object_key)
  WHERE r.revision_id = NEW.revision_id)
BEGIN SELECT RAISE(ABORT, 'artifact_payload_missing'); END;
/* Retain obsolete counters for audit/backup, but never charge retired resources. */
CREATE TRIGGER guard_retired_quota BEFORE INSERT ON quota_charges
WHEN NEW.resource NOT IN ('worker_requests', 'd1_writes')
BEGIN SELECT RAISE(ABORT, 'retired_resource'); END;
CREATE TRIGGER guard_retired_budget BEFORE INSERT ON budget_reservations
WHEN NEW.resource NOT IN ('worker_requests', 'd1_writes')
BEGIN SELECT RAISE(ABORT, 'retired_resource'); END;
CREATE TRIGGER guard_retired_usage BEFORE INSERT ON usage_observations
WHEN NEW.resource NOT IN ('worker_requests', 'd1_writes')
BEGIN SELECT RAISE(ABORT, 'retired_resource'); END;
