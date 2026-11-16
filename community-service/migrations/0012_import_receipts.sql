CREATE TABLE import_receipts (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  revision_id TEXT NOT NULL REFERENCES contribution_revisions(revision_id),
  receipt_id TEXT NOT NULL UNIQUE CHECK (length(receipt_id) = 64 AND receipt_id NOT GLOB '*[^a-f0-9]*'),
  artifact_digest TEXT NOT NULL CHECK (length(artifact_digest) = 64 AND artifact_digest NOT GLOB '*[^a-f0-9]*'),
  counted INTEGER NOT NULL CHECK (counted IN (0, 1)),
  acknowledged_at INTEGER NOT NULL CHECK (acknowledged_at >= 0),
  PRIMARY KEY (contributor_id, revision_id)
) WITHOUT ROWID;
CREATE INDEX idx_import_receipts_revision ON import_receipts(revision_id, counted);
CREATE TRIGGER guard_import_receipt BEFORE INSERT ON import_receipts
WHEN NOT EXISTS (SELECT 1 FROM contribution_revisions r JOIN contribution_statistics s USING (revision_id)
  WHERE r.revision_id = NEW.revision_id AND r.artifact_digest = NEW.artifact_digest
    AND NEW.counted = (r.contributor_id != NEW.contributor_id))
BEGIN SELECT RAISE(ABORT, 'invalid_import_receipt'); END;
CREATE TRIGGER count_import_receipt AFTER INSERT ON import_receipts
WHEN NEW.counted = 1
BEGIN UPDATE contribution_statistics SET imports = imports + 1 WHERE revision_id = NEW.revision_id; END;
CREATE TRIGGER guard_import_receipt_update BEFORE UPDATE ON import_receipts
BEGIN SELECT RAISE(ABORT, 'immutable_import_receipt'); END;
CREATE TRIGGER guard_import_receipt_delete BEFORE DELETE ON import_receipts
BEGIN SELECT RAISE(ABORT, 'immutable_import_receipt'); END;

CREATE TABLE import_usage_observations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  period TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied')),
  occurrences INTEGER NOT NULL CHECK (occurrences > 0),
  read_units INTEGER NOT NULL CHECK (read_units >= 0),
  unknown_reads INTEGER NOT NULL CHECK (unknown_reads >= 0),
  write_units INTEGER NOT NULL CHECK (write_units >= 0),
  unknown_writes INTEGER NOT NULL CHECK (unknown_writes >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, period, outcome)
) WITHOUT ROWID;