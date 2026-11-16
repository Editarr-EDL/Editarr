CREATE TABLE quota_policy (
  policy_id INTEGER PRIMARY KEY CHECK (policy_id = 1),
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1))
);
INSERT INTO quota_policy (policy_id, enabled) VALUES (1, 0);

CREATE TABLE quota_caps (
  resource TEXT PRIMARY KEY CHECK (resource IN ('worker_requests', 'd1_writes', 'r2_a', 'r2_b')),
  cap INTEGER NOT NULL CHECK (cap > 0)
);
INSERT INTO quota_caps (resource, cap) VALUES
  ('worker_requests', 1800), ('d1_writes', 1800),
  ('r2_a', 18000), ('r2_b', 180000);

CREATE TABLE quota_counters (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  resource TEXT NOT NULL REFERENCES quota_caps(resource),
  period TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, resource, period)
);

CREATE TABLE quota_charges (
  charge_id TEXT PRIMARY KEY,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  resource TEXT NOT NULL REFERENCES quota_caps(resource),
  period TEXT NOT NULL,
  units INTEGER NOT NULL CHECK (units > 0),
  created_at INTEGER NOT NULL
);

CREATE TRIGGER quota_charge AFTER INSERT ON quota_charges
BEGIN
  SELECT RAISE(ABORT, 'quota_unavailable') WHERE
    NOT EXISTS (SELECT 1 FROM quota_policy WHERE policy_id = 1 AND enabled = 1) OR
    NOT EXISTS (SELECT 1 FROM contributors
      WHERE contributor_id = NEW.contributor_id AND state = 'active');
  INSERT INTO quota_counters (contributor_id, resource, period, used, updated_at)
    VALUES (NEW.contributor_id, NEW.resource, NEW.period, 0, NEW.created_at)
    ON CONFLICT DO NOTHING;
  UPDATE quota_counters SET used = used + NEW.units, updated_at = NEW.created_at
    WHERE contributor_id = NEW.contributor_id AND resource = NEW.resource
      AND period = NEW.period AND used + NEW.units <=
        (SELECT cap FROM quota_caps WHERE resource = NEW.resource);
  SELECT RAISE(ABORT, 'quota_exhausted') WHERE changes() != 1;
END;

CREATE TABLE usage_observations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  resource TEXT NOT NULL CHECK (resource IN
    ('worker_requests', 'd1_writes', 'r2_a', 'r2_b', 'r2_bytes_estimate')),
  period TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied', 'retried')),
  occurrences INTEGER NOT NULL DEFAULT 0 CHECK (occurrences >= 0),
  units INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, resource, period, outcome)
);