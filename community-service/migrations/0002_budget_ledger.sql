CREATE TABLE budget_policy (
  policy_id INTEGER PRIMARY KEY CHECK (policy_id = 1),
  version TEXT NOT NULL,
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  provider_checked_at INTEGER NOT NULL,
  provider_epoch TEXT NOT NULL
);

CREATE TABLE budget_caps (
  resource TEXT NOT NULL CHECK (resource IN ('worker_requests', 'd1_writes', 'r2_a', 'r2_b', 'r2_bytes_peak')),
  scope TEXT NOT NULL CHECK (scope IN ('global', 'contributor')),
  cap INTEGER NOT NULL CHECK (cap >= 0),
  pressure_cap INTEGER CHECK (pressure_cap IS NULL OR
    (scope = 'global' AND pressure_cap >= 0 AND pressure_cap <= cap)),
  PRIMARY KEY (resource, scope)
);

CREATE TABLE budget_counters (
  resource TEXT NOT NULL,
  period TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  provider_floor INTEGER NOT NULL DEFAULT 0 CHECK (provider_floor >= 0),
  PRIMARY KEY (resource, period, scope_id)
);

CREATE TABLE budget_reservations (
  reservation_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  resource TEXT NOT NULL,
  period TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('upload', 'download', 'cleanup', 'backup', 'search')),
  provider_epoch TEXT NOT NULL,
  provider_observed_at INTEGER NOT NULL,
  units INTEGER NOT NULL CHECK (units > 0),
  state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved', 'settled', 'uncertain')),
  actual_units INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  CHECK ((state = 'settled' AND actual_units IS NOT NULL AND actual_units >= 0 AND actual_units <= units)
    OR (state != 'settled' AND actual_units IS NULL))
);
CREATE INDEX idx_budget_reservations_pending ON budget_reservations(state, updated_at);

CREATE TABLE budget_reservation_keys (
  reservation_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL,
  resource TEXT NOT NULL,
  period TEXT NOT NULL,
  purpose TEXT NOT NULL,
  units INTEGER NOT NULL
);

CREATE TRIGGER budget_reserve AFTER INSERT ON budget_reservations
BEGIN
  SELECT RAISE(ABORT, 'budget_unavailable') WHERE NOT EXISTS (
    SELECT 1 FROM budget_policy WHERE policy_id = 1 AND enabled = 1
      AND provider_epoch = NEW.provider_epoch AND provider_checked_at <= NEW.provider_observed_at
  ) OR NOT EXISTS (
    SELECT 1 FROM contributors WHERE contributor_id = NEW.contributor_id AND state = 'active'
  );
  UPDATE budget_counters SET reserved = reserved + NEW.units
    WHERE resource = NEW.resource AND period = NEW.period AND scope_id = 'global'
    AND MAX(used, provider_floor) + reserved + NEW.units <= (
      SELECT CASE WHEN NEW.purpose = 'upload' THEN pressure_cap ELSE cap END
      FROM budget_caps WHERE resource = NEW.resource AND scope = 'global');
  SELECT RAISE(ABORT, 'global_budget_exhausted') WHERE changes() != 1;
  UPDATE budget_counters SET reserved = reserved + NEW.units
    WHERE resource = NEW.resource AND period = NEW.period AND scope_id = NEW.contributor_id
    AND used + reserved + NEW.units <= (
      SELECT cap FROM budget_caps WHERE resource = NEW.resource AND scope = 'contributor');
  SELECT RAISE(ABORT, 'contributor_budget_exhausted') WHERE changes() != 1;
END;

CREATE TRIGGER budget_settle AFTER UPDATE OF state ON budget_reservations
WHEN NEW.state = 'settled' AND OLD.state = 'reserved'
BEGIN
  UPDATE budget_counters SET reserved = reserved - OLD.units, used = used + NEW.actual_units
    WHERE resource = OLD.resource AND period = OLD.period AND scope_id = 'global';
  UPDATE budget_counters SET reserved = reserved - OLD.units, used = used + NEW.actual_units
    WHERE resource = OLD.resource AND period = OLD.period AND scope_id = OLD.contributor_id;
  INSERT INTO budget_reservation_keys
    (reservation_id, contributor_id, resource, period, purpose, units)
    VALUES (NEW.reservation_id, NEW.contributor_id, NEW.resource, NEW.period, NEW.purpose, NEW.units);
END;

CREATE TRIGGER budget_guard_state BEFORE UPDATE ON budget_reservations
WHEN OLD.state != 'reserved' OR NEW.reservation_id != OLD.reservation_id OR
  NEW.contributor_id != OLD.contributor_id OR NEW.resource != OLD.resource OR
  NEW.period != OLD.period OR NEW.purpose != OLD.purpose OR
  NEW.provider_epoch != OLD.provider_epoch OR NEW.provider_observed_at != OLD.provider_observed_at OR
  NEW.units != OLD.units OR NEW.state != 'settled'
BEGIN
  SELECT RAISE(ABORT, 'reservation_immutable');
END;