CREATE TABLE oauth_usage_counters (
  resource TEXT NOT NULL CHECK (resource IN ('worker_requests', 'd1_writes')),
  period TEXT NOT NULL,
  units INTEGER NOT NULL CHECK (units >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (resource, period)
) WITHOUT ROWID;
CREATE INDEX idx_usage_observations_resource_period ON usage_observations (resource, period);