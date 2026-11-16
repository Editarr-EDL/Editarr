ALTER TABLE discord_oauth_states ADD COLUMN confirmation_hash TEXT;
ALTER TABLE enrollment_states ADD COLUMN confirmation_hash TEXT;
ALTER TABLE contributors ADD COLUMN profile_updated_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE contributors ADD COLUMN display_name_key TEXT;
CREATE TABLE profile_upgrade_guard (valid INTEGER NOT NULL CHECK (valid = 1));
INSERT INTO profile_upgrade_guard (valid)
	SELECT CASE WHEN EXISTS (SELECT 1 FROM contributors WHERE display_name GLOB '*[^ -~]*')
		THEN 0 ELSE 1 END;
DROP TABLE profile_upgrade_guard;
UPDATE contributors SET display_name_key = lower(trim(display_name));
CREATE UNIQUE INDEX uq_contributors_display_name ON contributors(display_name_key);
CREATE TRIGGER guard_profile_name_insert BEFORE INSERT ON contributors
WHEN NEW.display_name_key IS NULL OR NEW.display_name_key = ''
BEGIN
	SELECT RAISE(ABORT, 'display_name_key_required');
END;
CREATE TRIGGER guard_profile_name_update BEFORE UPDATE OF display_name, display_name_key ON contributors
WHEN NEW.display_name_key IS NULL OR NEW.display_name_key = ''
BEGIN
	SELECT RAISE(ABORT, 'display_name_key_required');
END;
CREATE TABLE profile_name_changes (
	contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
	operation_id TEXT NOT NULL,
	changed_at INTEGER NOT NULL,
	PRIMARY KEY (contributor_id, operation_id)
) WITHOUT ROWID;
CREATE INDEX idx_profile_name_changes_expiry ON profile_name_changes(changed_at);
CREATE TRIGGER guard_profile_name_rate BEFORE INSERT ON profile_name_changes
BEGIN
	SELECT RAISE(ABORT, 'rename_rate_limited') WHERE
		(SELECT COUNT(*) FROM profile_name_changes WHERE contributor_id = NEW.contributor_id
			AND changed_at > NEW.changed_at - 3600000) >= 5;
END;