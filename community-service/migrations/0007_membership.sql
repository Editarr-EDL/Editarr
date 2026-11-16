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