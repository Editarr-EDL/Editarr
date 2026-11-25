import baseline from '../../migrations/0001_master_baseline.sql?raw';
import expressions from '../../migrations/0002_filter_expressions.sql?raw';
import type { FixtureDatabase } from './environment';

/** Apply the current `community-master-1` schema to a disposable test database. */
export async function applyBaseline(db: FixtureDatabase) {
  await db.exec(baseline);
  await db.exec(expressions);
}
