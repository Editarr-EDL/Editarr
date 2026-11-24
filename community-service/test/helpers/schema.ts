import baseline from '../../migrations/0001_master_baseline.sql?raw';
import type { FixtureDatabase } from './environment';

/** Apply the `community-master-1` baseline schema to a disposable test database. */
export async function applyBaseline(db: FixtureDatabase) {
  await db.exec(baseline);
}
