CREATE TABLE revision_votes (
  contributor_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  value TEXT NOT NULL CHECK (value IN ('like', 'dislike', 'neutral')),
  key_id TEXT NOT NULL REFERENCES installation_keys(key_id),
  updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
  PRIMARY KEY (contributor_id, revision_id),
  FOREIGN KEY (contributor_id, revision_id) REFERENCES import_receipts(contributor_id, revision_id)
) WITHOUT ROWID;
CREATE INDEX idx_revision_votes_totals ON revision_votes(revision_id, value);
CREATE TRIGGER guard_vote_insert BEFORE INSERT ON revision_votes
WHEN NOT EXISTS (SELECT 1 FROM import_receipts i
  JOIN contribution_revisions r USING (revision_id)
  JOIN contribution_publications p USING (revision_id)
  JOIN contribution_statistics s USING (revision_id)
  JOIN contributors c ON c.contributor_id = i.contributor_id
  JOIN installations install ON install.contributor_id = c.contributor_id
  JOIN installation_keys k USING (installation_id)
  WHERE i.contributor_id = NEW.contributor_id AND i.revision_id = NEW.revision_id
    AND i.counted = 1 AND r.contributor_id != NEW.contributor_id AND p.state = 'published'
    AND c.state = 'active' AND k.key_id = NEW.key_id AND k.state = 'active')
BEGIN SELECT RAISE(ABORT, 'vote_ineligible'); END;
CREATE TRIGGER guard_vote_update BEFORE UPDATE ON revision_votes
WHEN NEW.contributor_id != OLD.contributor_id OR NEW.revision_id != OLD.revision_id
  OR NOT EXISTS (SELECT 1 FROM contributors c
    JOIN installations install USING (contributor_id)
    JOIN installation_keys k USING (installation_id)
    JOIN contribution_publications p ON p.revision_id = NEW.revision_id
    WHERE c.contributor_id = NEW.contributor_id AND c.state = 'active'
      AND k.key_id = NEW.key_id AND k.state = 'active' AND p.state = 'published')
BEGIN SELECT RAISE(ABORT, 'vote_ineligible'); END;
CREATE TRIGGER count_vote_insert AFTER INSERT ON revision_votes
BEGIN UPDATE contribution_statistics SET likes = likes + (NEW.value = 'like'),
  dislikes = dislikes + (NEW.value = 'dislike') WHERE revision_id = NEW.revision_id; END;
CREATE TRIGGER count_vote_update AFTER UPDATE ON revision_votes
BEGIN UPDATE contribution_statistics SET likes = likes - (OLD.value = 'like') + (NEW.value = 'like'),
  dislikes = dislikes - (OLD.value = 'dislike') + (NEW.value = 'dislike')
  WHERE revision_id = NEW.revision_id; END;
CREATE TRIGGER count_vote_delete AFTER DELETE ON revision_votes
BEGIN UPDATE contribution_statistics SET likes = likes - (OLD.value = 'like'),
  dislikes = dislikes - (OLD.value = 'dislike') WHERE revision_id = OLD.revision_id; END;

CREATE TABLE vote_usage_observations (
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