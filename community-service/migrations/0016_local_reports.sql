CREATE TABLE legacy_report_allowances AS
  SELECT report_id, per_day_limit, max_open_limit FROM reports;
DROP TRIGGER guard_report_limits;
DROP TRIGGER guard_report_update;
ALTER TABLE reports DROP COLUMN per_day_limit;
ALTER TABLE reports DROP COLUMN max_open_limit;
CREATE TRIGGER guard_report_update BEFORE UPDATE ON reports
WHEN NEW.report_id != OLD.report_id OR NEW.contributor_id != OLD.contributor_id
  OR NEW.revision_id != OLD.revision_id OR NEW.key_id != OLD.key_id
  OR NEW.reason != OLD.reason OR NEW.note != OLD.note OR NEW.created_at != OLD.created_at
  OR OLD.state != 'open' OR NEW.state != 'resolved'
BEGIN SELECT RAISE(ABORT, 'immutable_report'); END;