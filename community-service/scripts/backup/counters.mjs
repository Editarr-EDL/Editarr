import { reconcileImportCounts, reconcileVoteCounts } from './artifact-runtime.mjs';
import { validateDatabase } from './validate.mjs';
import { isDeepStrictEqual } from 'node:util';
import { CliError } from '../operator/transport.mjs';

export async function reconcileCounters(db, bounds, original) {
  if (db.prepare(`SELECT 1 FROM contribution_revisions r LEFT JOIN contribution_statistics s USING(revision_id)
      WHERE s.revision_id IS NULL`).get() ||
      db.prepare(`SELECT 1 FROM import_receipts i JOIN contribution_revisions r USING(revision_id)
      WHERE i.artifact_digest != r.artifact_digest OR i.counted != (i.contributor_id != r.contributor_id)`).get() ||
      db.prepare(`SELECT 1 FROM revision_votes v JOIN import_receipts i USING(contributor_id,revision_id)
      JOIN installation_keys k USING(key_id) JOIN installations install USING(installation_id)
      WHERE i.counted != 1 OR install.contributor_id != v.contributor_id`).get())
    throw new CliError('restore_reputation_facts_invalid');
  const adapter = {
    prepare: sql => ({ bind: (...values) => ({ sql, values }) }),
    batch: async statements => {
      db.exec('BEGIN IMMEDIATE');
      try {
        const results = statements.map(statement => db.prepare(statement.sql).run(...statement.values));
        db.exec('COMMIT'); return results;
      } catch (error) { db.exec('ROLLBACK'); throw error; }
    },
  };
  let after = null;
  while (true) {
    const ids = db.prepare('SELECT revision_id FROM contribution_revisions WHERE (? IS NULL OR revision_id > ?) ORDER BY revision_id LIMIT 100')
      .all(after, after).map(row => row.revision_id);
    if (!ids.length) break;
    await reconcileImportCounts(adapter, ids);
    await reconcileVoteCounts(adapter, ids);
    after = ids.at(-1);
  }
  const verified = await validateDatabase(db, bounds);
  const unchanged = inventory => inventory.filter(row => row.table !== 'contribution_statistics');
  if (!isDeepStrictEqual(unchanged(original.inventory), unchanged(verified.inventory)))
    throw new CliError('restore_source_facts_changed');
  const beforeStatistics = original.inventory.find(row => row.table === 'contribution_statistics');
  const afterStatistics = verified.inventory.find(row => row.table === 'contribution_statistics');
  return { validation: verified, changed: !isDeepStrictEqual(beforeStatistics, afterStatistics),
    source: 'durable_counted_imports_and_votes_with_active_exclusions', batchSize: 100,
    suspensionPolicy: 'preserve_votes_suspension_is_not_exclusion', storagePolicy: 'verified_exact_blob_lengths_no_reservation_reconciliation' };
}