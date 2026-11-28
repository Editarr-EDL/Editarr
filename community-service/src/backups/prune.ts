import { rm } from 'node:fs/promises';
import type { SqlDatabase } from '../runtime/contracts';
import { confinedFile } from './destination';
import { markBackupPruned, verifiedBackupRuns } from './store';

const WEEK = 7 * 86_400_000;
/** 1970-01-05 was the first Monday after the epoch; weekly retention uses Monday-start UTC weeks. */
const FIRST_MONDAY = 4 * 86_400_000;

/**
 * Verified runs to prune: keep the newest `keepLast`, plus the newest backup of each of the newest
 * `keepWeekly` UTC weeks that have one. The newest verified backup is always kept.
 */
export function backupsToPrune(runs: { run_id: string; started_at: number }[], keepLast: number, keepWeekly: number): string[] {
  const ordered = [...runs].sort((left, right) => right.started_at - left.started_at || (left.run_id < right.run_id ? 1 : -1));
  const keep = new Set(ordered.slice(0, Math.max(1, keepLast)).map(run => run.run_id));
  const weeks = new Set<number>();
  for (const run of ordered) {
    const week = Math.floor((run.started_at - FIRST_MONDAY) / WEEK);
    if (weeks.has(week)) continue;
    if (weeks.size >= keepWeekly) break;
    weeks.add(week);
    keep.add(run.run_id);
  }
  return ordered.filter(run => !keep.has(run.run_id)).map(run => run.run_id);
}

/**
 * Delete pruned backup files (only recorded, root-confined regular files) and mark their runs
 * `pruned`. A file that is already missing is still marked; other deletion errors keep the run.
 */
export async function pruneBackups(db: SqlDatabase, root: string, keepLast: number, keepWeekly: number): Promise<string[]> {
  const runs = await verifiedBackupRuns(db);
  const pruned: string[] = [];
  for (const runId of backupsToPrune(runs, keepLast, keepWeekly)) {
    const run = runs.find(item => item.run_id === runId)!;
    try {
      if (run.file_name) await rm(await confinedFile(root, run.file_name));
    } catch (error) {
      if ((error as { code?: string }).code !== 'ENOENT' && (error as { code?: string }).code !== 'backup_destination_missing') continue;
    }
    await markBackupPruned(db, runId);
    pruned.push(runId);
  }
  return pruned;
}
