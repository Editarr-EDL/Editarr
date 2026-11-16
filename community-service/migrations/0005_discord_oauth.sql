CREATE TABLE discord_oauth_states (
  state_id TEXT PRIMARY KEY NOT NULL,
  browser_hash TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  public_key BLOB NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER
);
CREATE INDEX idx_discord_oauth_states_expiry ON discord_oauth_states(expires_at);