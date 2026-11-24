import type { SqlDatabase } from '../runtime/contracts';
import { transientStatement } from './transient';

/** Prune expired fixed-window request counters and sync runs; `now` is milliseconds. */
export async function pruneRateWindows(db: SqlDatabase, now: number, limit: number) {
  await db.batch([transientStatement(db, 'rateCounters', now, limit, 'prune'),
    transientStatement(db, 'syncRuns', now, limit, 'prune')]);
}
