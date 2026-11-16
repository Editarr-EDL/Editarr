CREATE TABLE moderation_versions (
  resource TEXT PRIMARY KEY NOT NULL,
  revision INTEGER NOT NULL CHECK (revision > 0)
) WITHOUT ROWID;

CREATE TABLE moderation_epoch (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), epoch INTEGER NOT NULL CHECK (epoch >= 0));
INSERT INTO moderation_epoch VALUES (1, 0);
CREATE TABLE operator_request_counts (
  subject TEXT NOT NULL,
  minute INTEGER NOT NULL,
  requests INTEGER NOT NULL CHECK (requests > 0),
  PRIMARY KEY (subject, minute)
) WITHOUT ROWID;

CREATE TABLE reports (
  report_id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  reason TEXT NOT NULL CHECK (reason IN ('spam', 'inaccurate', 'privacy', 'rights', 'other')),
  note TEXT NOT NULL CHECK (length(note) <= 1000),
  state TEXT NOT NULL CHECK (state IN ('open', 'resolved')),
  created_at INTEGER NOT NULL CHECK (created_at >= 0),
  per_day_limit INTEGER NOT NULL CHECK (per_day_limit BETWEEN 1 AND 100),
  max_open_limit INTEGER NOT NULL CHECK (max_open_limit BETWEEN 1 AND 100),
  FOREIGN KEY (contributor_id, revision_id) REFERENCES import_receipts(contributor_id, revision_id)
);
CREATE UNIQUE INDEX uq_open_report ON reports(contributor_id, revision_id) WHERE state = 'open';
CREATE INDEX idx_report_daily ON reports(contributor_id, created_at);
CREATE INDEX idx_report_queue ON reports(state, report_id);
CREATE TRIGGER guard_report_limits BEFORE INSERT ON reports
WHEN (SELECT count(*) FROM reports WHERE contributor_id = NEW.contributor_id
    AND created_at >= NEW.created_at - (NEW.created_at % 86400)
    AND created_at < NEW.created_at - (NEW.created_at % 86400) + 86400) >= NEW.per_day_limit
  OR (SELECT count(*) FROM reports WHERE contributor_id = NEW.contributor_id AND state = 'open') >= NEW.max_open_limit
BEGIN SELECT RAISE(ABORT, 'report_rate_limited'); END;
CREATE TRIGGER guard_report_insert BEFORE INSERT ON reports
WHEN NEW.state != 'open' OR NOT EXISTS (SELECT 1 FROM import_receipts i
  JOIN contribution_revisions r USING (revision_id)
  JOIN contribution_publications p USING (revision_id)
  JOIN contributors author ON author.contributor_id = r.contributor_id
  JOIN contributors reporter ON reporter.contributor_id = i.contributor_id
  JOIN installations install ON install.contributor_id = reporter.contributor_id
  JOIN installation_keys k USING (installation_id)
  WHERE i.contributor_id = NEW.contributor_id AND i.revision_id = NEW.revision_id
    AND i.counted = 1 AND r.contributor_id != NEW.contributor_id
    AND p.state = 'published' AND author.state = 'active' AND reporter.state = 'active'
    AND k.key_id = NEW.key_id AND k.state = 'active')
BEGIN SELECT RAISE(ABORT, 'report_ineligible'); END;
CREATE TRIGGER guard_report_update BEFORE UPDATE ON reports
WHEN NEW.report_id != OLD.report_id OR NEW.contributor_id != OLD.contributor_id
  OR NEW.revision_id != OLD.revision_id OR NEW.key_id != OLD.key_id
  OR NEW.reason != OLD.reason OR NEW.note != OLD.note OR NEW.created_at != OLD.created_at
  OR NEW.per_day_limit != OLD.per_day_limit OR NEW.max_open_limit != OLD.max_open_limit
  OR OLD.state != 'open' OR NEW.state != 'resolved'
BEGIN SELECT RAISE(ABORT, 'immutable_report'); END;
CREATE TRIGGER guard_report_delete BEFORE DELETE ON reports
BEGIN SELECT RAISE(ABORT, 'immutable_report'); END;

CREATE TABLE moderation_actions (
  action_id TEXT PRIMARY KEY NOT NULL,
  actor TEXT NOT NULL,
  resource TEXT NOT NULL,
  target_id TEXT NOT NULL,
  revision_id TEXT,
  command TEXT NOT NULL CHECK (command IN ('quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report')),
  expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
  request_json TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('applied', 'conflict', 'denied')),
  created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE INDEX idx_moderation_audit ON moderation_actions(resource, created_at, action_id);
CREATE TRIGGER guard_audit_update BEFORE UPDATE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;
CREATE TRIGGER guard_audit_delete BEFORE DELETE ON moderation_actions
BEGIN SELECT RAISE(ABORT, 'immutable_audit'); END;

CREATE TABLE vote_exclusions (
  contributor_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  excluded INTEGER NOT NULL CHECK (excluded IN (0, 1)),
  action_id TEXT NOT NULL REFERENCES moderation_actions(action_id),
  PRIMARY KEY (contributor_id, revision_id),
  FOREIGN KEY (contributor_id, revision_id) REFERENCES revision_votes(contributor_id, revision_id)
) WITHOUT ROWID;

CREATE TRIGGER version_publication AFTER UPDATE OF state ON contribution_publications
WHEN NEW.state != OLD.state
BEGIN INSERT INTO moderation_versions VALUES ('revision:' || NEW.revision_id, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1;
  UPDATE moderation_epoch SET epoch = epoch + 1 WHERE singleton = 1 AND OLD.state IN ('published', 'quarantined', 'withdrawn'); END;
CREATE TRIGGER version_contributor AFTER UPDATE OF state ON contributors
WHEN NEW.state != OLD.state
BEGIN INSERT INTO moderation_versions VALUES ('contributor:' || NEW.contributor_id, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1;
  UPDATE moderation_epoch SET epoch = epoch + 1 WHERE singleton = 1; END;
CREATE TRIGGER version_report AFTER UPDATE OF state ON reports
BEGIN INSERT INTO moderation_versions VALUES ('report:' || NEW.report_id, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;
CREATE TRIGGER version_vote_insert AFTER INSERT ON revision_votes
BEGIN INSERT INTO moderation_versions VALUES ('vote:' || NEW.contributor_id || ':' || NEW.revision_id, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;
CREATE TRIGGER version_vote_update AFTER UPDATE ON revision_votes
BEGIN INSERT INTO moderation_versions VALUES ('vote:' || NEW.contributor_id || ':' || NEW.revision_id, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1; END;

DROP TRIGGER count_vote_insert;
DROP TRIGGER count_vote_update;
DROP TRIGGER count_vote_delete;
CREATE TRIGGER count_vote_insert AFTER INSERT ON revision_votes
BEGIN UPDATE contribution_statistics SET likes = likes + (NEW.value = 'like'),
  dislikes = dislikes + (NEW.value = 'dislike') WHERE revision_id = NEW.revision_id; END;
CREATE TRIGGER count_vote_update AFTER UPDATE ON revision_votes
WHEN NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = NEW.contributor_id
  AND e.revision_id = NEW.revision_id AND e.excluded = 1)
BEGIN UPDATE contribution_statistics SET likes = likes - (OLD.value = 'like') + (NEW.value = 'like'),
  dislikes = dislikes - (OLD.value = 'dislike') + (NEW.value = 'dislike')
  WHERE revision_id = NEW.revision_id; END;
CREATE TRIGGER count_vote_delete AFTER DELETE ON revision_votes
WHEN NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = OLD.contributor_id
  AND e.revision_id = OLD.revision_id AND e.excluded = 1)
BEGIN UPDATE contribution_statistics SET likes = likes - (OLD.value = 'like'),
  dislikes = dislikes - (OLD.value = 'dislike') WHERE revision_id = OLD.revision_id; END;
CREATE TRIGGER exclusion_insert AFTER INSERT ON vote_exclusions
BEGIN
  UPDATE contribution_statistics SET
    likes = (SELECT count(*) FROM revision_votes v WHERE v.revision_id = NEW.revision_id AND v.value = 'like'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1)),
    dislikes = (SELECT count(*) FROM revision_votes v WHERE v.revision_id = NEW.revision_id AND v.value = 'dislike'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1))
    WHERE revision_id = NEW.revision_id;
  INSERT INTO moderation_versions VALUES ('vote:' || NEW.contributor_id || ':' || NEW.revision_id, 1)
    ON CONFLICT(resource) DO UPDATE SET revision = revision + 1;
END;
CREATE TRIGGER exclusion_update AFTER UPDATE ON vote_exclusions
BEGIN
  UPDATE contribution_statistics SET
    likes = (SELECT count(*) FROM revision_votes v WHERE v.revision_id = NEW.revision_id AND v.value = 'like'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1)),
    dislikes = (SELECT count(*) FROM revision_votes v WHERE v.revision_id = NEW.revision_id AND v.value = 'dislike'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1))
    WHERE revision_id = NEW.revision_id;
  INSERT INTO moderation_versions VALUES ('vote:' || NEW.contributor_id || ':' || NEW.revision_id, 1)
    ON CONFLICT(resource) DO UPDATE SET revision = revision + 1;
END;

CREATE TRIGGER guard_moderation BEFORE INSERT ON moderation_actions
WHEN NEW.outcome = 'applied'
BEGIN
  SELECT CASE WHEN coalesce((SELECT revision FROM moderation_versions WHERE resource = NEW.resource), 0) != NEW.expected_revision
    THEN RAISE(ABORT, 'moderation_conflict') END;
  SELECT CASE WHEN NOT (
    (NEW.command = 'quarantine' AND NEW.resource = 'revision:' || NEW.target_id
      AND EXISTS (SELECT 1 FROM contribution_publications WHERE revision_id = NEW.target_id AND state = 'published'))
    OR (NEW.command = 'restore' AND NEW.resource = 'revision:' || NEW.target_id
      AND EXISTS (SELECT 1 FROM contribution_publications p JOIN contribution_revisions r USING (revision_id)
        JOIN contributors c ON c.contributor_id = r.contributor_id JOIN installation_keys k ON k.key_id = r.key_id
        WHERE p.revision_id = NEW.target_id AND p.state = 'quarantined' AND c.state = 'active' AND k.state IN ('active', 'retired')))
    OR (NEW.command = 'suspend' AND NEW.resource = 'contributor:' || NEW.target_id
      AND EXISTS (SELECT 1 FROM contributors WHERE contributor_id = NEW.target_id AND state = 'active'))
    OR (NEW.command = 'unsuspend' AND NEW.resource = 'contributor:' || NEW.target_id
      AND EXISTS (SELECT 1 FROM contributors WHERE contributor_id = NEW.target_id AND state = 'suspended'))
    OR (NEW.command = 'resolve_report' AND NEW.resource = 'report:' || NEW.target_id
      AND EXISTS (SELECT 1 FROM reports WHERE report_id = NEW.target_id AND state = 'open'))
    OR (NEW.command = 'exclude_vote' AND NEW.resource = 'vote:' || NEW.target_id || ':' || NEW.revision_id
      AND EXISTS (SELECT 1 FROM revision_votes v WHERE v.contributor_id = NEW.target_id AND v.revision_id = NEW.revision_id)
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions WHERE contributor_id = NEW.target_id AND revision_id = NEW.revision_id AND excluded = 1))
    OR (NEW.command = 'restore_vote' AND NEW.resource = 'vote:' || NEW.target_id || ':' || NEW.revision_id
      AND EXISTS (SELECT 1 FROM vote_exclusions e JOIN import_receipts i USING (contributor_id, revision_id)
        JOIN contributors voter ON voter.contributor_id = e.contributor_id
        JOIN contribution_revisions r USING (revision_id) JOIN contribution_publications p USING (revision_id)
        JOIN contributors author ON author.contributor_id = r.contributor_id
        WHERE e.contributor_id = NEW.target_id AND e.revision_id = NEW.revision_id AND e.excluded = 1
          AND i.counted = 1 AND r.contributor_id != e.contributor_id AND voter.state = 'active'
          AND author.state = 'active' AND p.state = 'published'))
  ) THEN RAISE(ABORT, 'moderation_ineligible') END;
END;
CREATE TRIGGER apply_moderation AFTER INSERT ON moderation_actions
WHEN NEW.outcome = 'applied'
BEGIN
  UPDATE contribution_publications SET state = CASE NEW.command WHEN 'quarantine' THEN 'quarantined' ELSE 'published' END,
    updated_at = max(updated_at, NEW.created_at)
    WHERE revision_id = NEW.target_id AND NEW.command IN ('quarantine', 'restore');
  UPDATE contributors SET state = CASE NEW.command WHEN 'suspend' THEN 'suspended' ELSE 'active' END
    WHERE contributor_id = NEW.target_id AND NEW.command IN ('suspend', 'unsuspend');
  UPDATE reports SET state = 'resolved' WHERE report_id = NEW.target_id AND NEW.command = 'resolve_report';
  INSERT INTO vote_exclusions (contributor_id, revision_id, excluded, action_id)
    SELECT NEW.target_id, NEW.revision_id, (NEW.command = 'exclude_vote'), NEW.action_id
    WHERE NEW.command IN ('exclude_vote', 'restore_vote')
    ON CONFLICT(contributor_id, revision_id) DO UPDATE SET excluded = excluded.excluded, action_id = excluded.action_id;
END;

CREATE TRIGGER guard_vote_author_insert BEFORE INSERT ON revision_votes
WHEN NOT EXISTS (SELECT 1 FROM contribution_revisions r JOIN contributors c ON c.contributor_id = r.contributor_id
  WHERE r.revision_id = NEW.revision_id AND c.state = 'active')
BEGIN SELECT RAISE(ABORT, 'vote_ineligible'); END;

CREATE TABLE report_usage_observations (
  contributor_id TEXT NOT NULL REFERENCES contributors(contributor_id),
  period TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied')),
  occurrences INTEGER NOT NULL CHECK (occurrences > 0),
  read_units INTEGER NOT NULL CHECK (read_units >= 0),
  unknown_reads INTEGER NOT NULL CHECK (unknown_reads >= 0),
  write_units INTEGER NOT NULL CHECK (write_units >= 0),
  unknown_writes INTEGER NOT NULL CHECK (unknown_writes >= 0),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (contributor_id, period, outcome)
) WITHOUT ROWID;
CREATE TRIGGER guard_vote_author_update BEFORE UPDATE ON revision_votes
WHEN NOT EXISTS (SELECT 1 FROM contribution_revisions r JOIN contributors c ON c.contributor_id = r.contributor_id
  WHERE r.revision_id = NEW.revision_id AND c.state = 'active')
BEGIN SELECT RAISE(ABORT, 'vote_ineligible'); END;