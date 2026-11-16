DROP TRIGGER quota_charge;
DROP TRIGGER guard_retired_quota;
DROP TRIGGER guard_retired_budget;
DROP TRIGGER charge_payload_storage;
DROP TRIGGER IF EXISTS guard_profile_name_rate;

CREATE TRIGGER observe_payload_storage AFTER INSERT ON artifact_payloads
BEGIN
  INSERT INTO artifact_storage_usage VALUES ('global', length(NEW.artifact) + length(NEW.manifest) + length(NEW.envelope))
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
  INSERT INTO artifact_storage_usage SELECT 'contributor:' || contributor_id,
    length(NEW.artifact) + length(NEW.manifest) + length(NEW.envelope)
    FROM contribution_revisions WHERE object_key = NEW.object_key
    ON CONFLICT(scope_id) DO UPDATE SET bytes = bytes + excluded.bytes;
END;