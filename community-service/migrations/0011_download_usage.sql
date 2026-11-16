CREATE TABLE download_usage_observations (
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