CREATE TABLE contributors (
  contributor_id TEXT PRIMARY KEY NOT NULL,
  discord_user_id TEXT NOT NULL CONSTRAINT uq_contributors_discord_user_id UNIQUE,
  display_name TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'suspended')),
  created_at INTEGER NOT NULL
);

CREATE TABLE installations (
  installation_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  created_at INTEGER NOT NULL,
  CONSTRAINT uq_installations_contributor_id UNIQUE (contributor_id, installation_id)
);
CREATE INDEX idx_installations_contributor ON installations(contributor_id);

CREATE TABLE installation_keys (
  key_id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL REFERENCES installations(installation_id),
  algorithm TEXT NOT NULL CHECK (algorithm = 'Ed25519'),
  public_key BLOB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'retired', 'revoked')),
  created_at INTEGER NOT NULL,
  retired_at INTEGER,
  CHECK ((state = 'active' AND retired_at IS NULL) OR
         (state != 'active' AND retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_installation_keys_active ON installation_keys(installation_id) WHERE state = 'active';
CREATE INDEX idx_installation_keys_history ON installation_keys(installation_id, created_at);

CREATE TABLE enrollment_states (
  state_id TEXT PRIMARY KEY NOT NULL,
  installation_id TEXT NOT NULL,
  key_id TEXT NOT NULL,
  public_key BLOB NOT NULL,
  provider_subject TEXT,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  CHECK (consumed_at IS NULL OR consumed_at <= expires_at)
);
CREATE INDEX idx_enrollment_states_expiry ON enrollment_states(expires_at) WHERE consumed_at IS NULL;

CREATE TRIGGER guard_enrollment_consumption BEFORE UPDATE OF consumed_at ON enrollment_states
WHEN OLD.consumed_at IS NOT NULL OR OLD.expires_at <= NEW.consumed_at OR
     (OLD.provider_subject IS NOT NULL AND OLD.provider_subject != NEW.provider_subject)
BEGIN
  SELECT RAISE(ABORT, 'enrollment_state_invalid');
END;

CREATE TABLE request_nonces (
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  nonce TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY (key_id, nonce)
);
CREATE INDEX idx_request_nonces_expiry ON request_nonces(expires_at);

CREATE TABLE operations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  kind TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'failed')),
  result TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, kind, operation_id),
  CHECK ((state = 'completed' AND result IS NOT NULL) OR
         (state != 'completed' AND result IS NULL))
);
CREATE INDEX idx_operations_state ON operations(state, updated_at);