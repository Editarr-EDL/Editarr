import { lstat, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import type { SqlDatabase } from '../runtime/contracts';
import { confinedDirectory, normalizeDestination, STAGING_DIRECTORY } from './destination';

/** Names the runner and staging write; only these are ever removed by housekeeping. */
export const BACKUP_FILE = /^community-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite$/;
export const PARTIAL_FILE = /^\.community-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite\.partial$/;
/** Failed and pruned run rows are kept this long, and at most `HISTORY_ROWS` of them. */
export const HISTORY_MS = 90 * 86_400_000;
export const HISTORY_ROWS = 200;

/** Remove regular files whose names match `pattern` in `directory` (symlinks are left alone), except `keep`. */
export async function removeMatching(directory: string, pattern: RegExp, keep?: string): Promise<string[]> {
  const removed: string[] = [];
  for (const name of await readdir(directory)) {
    if (!pattern.test(name) || name === keep) continue;
    const path = join(directory, name);
    const stat = await lstat(path).catch(() => null);
    if (!stat?.isFile() || stat.isSymbolicLink()) continue;
    await rm(path, { force: true });
    removed.push(name);
  }
  return removed;
}

/**
 * Remove `.partial` files left by interrupted runs or stages from the root, the configured
 * destination and the staging directory. Callers must hold the run lock with no stage in progress.
 */
export async function sweepPartials(root: string, destination: string, includeStaging: boolean): Promise<string[]> {
  const directories = new Set([root]);
  const candidates = [() => confinedDirectory(root, normalizeDestination(destination, root), false)];
  if (includeStaging) candidates.push(() => confinedDirectory(root, STAGING_DIRECTORY, false));
  for (const candidate of candidates) {
    try { directories.add(await candidate()); } catch { /* missing or refused directories hold nothing to sweep */ }
  }
  const removed: string[] = [];
  for (const directory of directories) removed.push(...await removeMatching(directory, PARTIAL_FILE));
  return removed;
}

/**
 * Drop failed and pruned run rows older than `HISTORY_MS`, then all but the newest `HISTORY_ROWS`
 * of them. Verified (file on disk) and running rows are never deleted.
 */
export async function pruneRunHistory(db: SqlDatabase, nowMs: number): Promise<void> {
  await db.batch([
    db.prepare("DELETE FROM backup_runs WHERE status IN ('failed', 'pruned') AND started_at < ?").bind(nowMs - HISTORY_MS),
    db.prepare(`DELETE FROM backup_runs WHERE run_id IN (SELECT run_id FROM backup_runs WHERE status IN ('failed', 'pruned')
      ORDER BY started_at DESC, run_id DESC LIMIT -1 OFFSET ?)`).bind(HISTORY_ROWS),
  ]);
}
