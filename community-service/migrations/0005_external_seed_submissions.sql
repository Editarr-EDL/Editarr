CREATE TABLE external_seed_submissions (
  submission_id TEXT PRIMARY KEY NOT NULL REFERENCES submissions(submission_id),
  source TEXT NOT NULL CHECK (source IN ('VideoSkip', 'Filmiway')),
  source_record_id TEXT NOT NULL CHECK (length(source_record_id) BETWEEN 1 AND 128),
  input_digest TEXT NOT NULL CHECK (length(input_digest) = 64 AND input_digest NOT GLOB '*[^0-9a-f]*'),
  source_runtime_ms INTEGER CHECK (source_runtime_ms BETWEEN 1 AND 86400000),
  provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json)),
  UNIQUE (source, source_record_id)
) WITHOUT ROWID;
CREATE TRIGGER guard_external_seed_update BEFORE UPDATE ON external_seed_submissions
BEGIN SELECT RAISE(ABORT, 'immutable_external_seed'); END;
CREATE TRIGGER guard_external_seed_delete BEFORE DELETE ON external_seed_submissions
BEGIN SELECT RAISE(ABORT, 'immutable_external_seed'); END;
