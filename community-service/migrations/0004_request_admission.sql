ALTER TABLE request_nonces ADD COLUMN contributor_id TEXT REFERENCES contributors(contributor_id);

CREATE TRIGGER request_nonce_active_key BEFORE INSERT ON request_nonces
BEGIN
  SELECT RAISE(ABORT, 'key_inactive') WHERE NOT EXISTS (
    SELECT 1 FROM installation_keys AS key
    JOIN installations AS installation ON installation.installation_id = key.installation_id
    JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
    WHERE key.key_id = NEW.key_id AND key.state = 'active' AND contributor.state = 'active'
      AND contributor.contributor_id = NEW.contributor_id
  );
END;

CREATE TRIGGER operation_digest_immutable BEFORE INSERT ON operations
WHEN EXISTS (SELECT 1 FROM operations WHERE contributor_id = NEW.contributor_id
  AND kind = NEW.kind AND operation_id = NEW.operation_id
  AND request_digest != NEW.request_digest)
BEGIN
  SELECT RAISE(ABORT, 'operation_conflict');
END;