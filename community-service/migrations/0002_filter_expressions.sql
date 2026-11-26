-- Words and phrases of language filters (contract §5.2, §6.7, §7.3).
-- Every column is nullable, so existing rows keep their values: stored submissions carry
-- no expression and their filters merge to a null expression until a recompute sees one.
ALTER TABLE submission_entries ADD COLUMN expression TEXT
  CHECK (expression IS NULL OR (length(expression) BETWEEN 1 AND 64 AND kind = 'support' AND category = 'language'));
ALTER TABLE filter_registry ADD COLUMN incumbent_expression TEXT
  CHECK (incumbent_expression IS NULL OR length(incumbent_expression) BETWEEN 1 AND 64);
ALTER TABLE master_filters ADD COLUMN expression TEXT
  CHECK (expression IS NULL OR length(expression) BETWEEN 1 AND 64);
