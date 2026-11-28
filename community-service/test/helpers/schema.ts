import baseline from '../../migrations/0001_master_baseline.sql?raw';
import expressions from '../../migrations/0002_filter_expressions.sql?raw';
import receiptVersions from '../../migrations/0003_download_receipt_version_index.sql?raw';
import retentionScans from '../../migrations/0004_retention_scan_indexes.sql?raw';
import externalSeeds from '../../migrations/0005_external_seed_submissions.sql?raw';
import type { FixtureDatabase } from './environment';

/** Apply the current `community-master-1` schema to a disposable test database. */
export async function applyBaseline(db: FixtureDatabase) {
  await db.exec(baseline);
  await db.exec(expressions);
  await db.exec(receiptVersions);
  await db.exec(retentionScans);
  await db.exec(externalSeeds);
}
