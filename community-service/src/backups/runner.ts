import { rm, stat, rename, lstat } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import type { Env } from '../config';
import type { SqlDatabase } from '../runtime/contracts';
import { fileSha256 } from '../runtime/restore.mjs';
import { backupRoot, confinedDirectory, fsyncPath, normalizeDestination } from './destination';
import { liveConnection, snapshotDatabase, verifySnapshotFile } from './engine';
import { BackupError, runErrorCode } from './errors';
import { pruneRunHistory, sweepPartials } from './housekeeping';
import { pruneBackups } from './prune';
import { stageInProgress } from './staging';
import { backupDue } from './schedule';
import { backupAudit, completeBackupRun, failBackupRun, insertBackupRun, interruptStaleRuns, lastScheduledStart,
  readBackupConfig, type BackupConfigRow } from './store';

let active: { runId: string; done: Promise<void> } | null = null;

/** Whether this process is running a backup (the single-flight lock). */
export const backupRunning = () => active !== null && active.runId !== 'housekeeping';
/** Wait for an in-flight backup; shutdown calls this before closing SQLite. */
export async function backupDrain(): Promise<void> { await active?.done; }

let closed = false;
/**
 * Shutdown: refuse every later backup start (`503 backups_shutting_down`) and housekeeping, then
 * wait for an in-flight run. Call before draining requests; drain again just before closing SQLite.
 */
export function closeBackups(): Promise<void> {
  closed = true;
  return backupDrain();
}

function stamp(nowMs: number): string {
  return new Date(nowMs).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

async function execute(env: Env, db: SqlDatabase, runId: string, config: BackupConfigRow, startedAt: number): Promise<void> {
  let partial: string | null = null;
  try {
    const root = await backupRoot(env.COMMUNITY_BACKUP_ROOT);
    if (!root) throw new BackupError('backups_not_configured', 503);
    const directory = await confinedDirectory(root, normalizeDestination(config.destination, root, env.COMMUNITY_BACKUP_ROOT), true);
    const name = `community-${stamp(startedAt)}-${runId.slice(0, 8)}.sqlite`, final = join(directory, name);
    partial = join(directory, `.${name}.partial`);
    await snapshotDatabase(liveConnection(env.CATALOG_DB), partial);
    const verified = await verifySnapshotFile(partial, config.integrity_check === 1, env.BACKUP_VERIFIER);
    const sha256 = await fileSha256(partial), bytes = (await stat(partial)).size;
    if (await lstat(final).then(() => true, () => false)) throw new BackupError('backup_destination_invalid');
    await rename(partial, final);
    partial = null;
    await fsyncPath(final);
    await fsyncPath(directory);
    await completeBackupRun(db, runId, { fileName: relative(root, final).split(sep).join('/'), bytes,
      durationMs: Date.now() - startedAt, integrity: config.integrity_check === 1 ? 'ok' : 'skipped', sha256,
      note: `schema version ${verified.version}` });
    try { await pruneBackups(db, root, config.keep_last, config.keep_weekly); }
    catch { console.error('community_backup_prune_failed'); }
  } catch (error) {
    if (partial) await rm(partial, { force: true }).catch(() => undefined);
    await failBackupRun(db, runId, runErrorCode(error), Date.now() - startedAt);
  }
  try { await pruneRunHistory(db, Date.now()); }
  catch { console.error('community_backup_history_failed'); }
}

/**
 * Recovery and bounded cleanup while no run is active: mark runs left `running` by a stopped
 * process as interrupted, remove their partial files (staging partials only when no stage is in
 * progress) and prune old failed/pruned history. The maintenance loop calls this every minute.
 */
export async function backupHousekeeping(env: Env, nowMs: number): Promise<void> {
  if (closed || active || !env.CATALOG_DB) return;
  const db = env.CATALOG_DB;
  let release!: () => void;
  active = { runId: 'housekeeping', done: new Promise<void>(resolve => { release = resolve; }) };
  try {
    await interruptStaleRuns(db).run();
    const root = await backupRoot(env.COMMUNITY_BACKUP_ROOT).catch(() => null);
    if (root) {
      const config = await readBackupConfig(db);
      await sweepPartials(root, config.destination, !stageInProgress()).catch(() => undefined);
    }
    await pruneRunHistory(db, nowMs);
  } finally { active = null; release(); }
}

/**
 * Start a backup run under the single-flight lock and return its run ID once the `running` row
 * (and, for manual runs, the audit row) is committed; the snapshot continues in the background.
 * Throws `backup_running` (409) when a run is active, `backups_not_configured` (503) for a manual
 * run without `COMMUNITY_BACKUP_ROOT`, and `backups_shutting_down` (503) once shutdown began.
 */
export async function startBackup(env: Env, trigger: 'scheduled' | 'manual', actor: string | null, nowMs: number): Promise<string> {
  if (closed) throw new BackupError('backups_shutting_down', 503);
  if (active) throw new BackupError('backup_running', 409);
  const runId = crypto.randomUUID();
  let release!: () => void;
  active = { runId, done: new Promise<void>(resolve => { release = resolve; }) };
  try {
    const db = env.CATALOG_DB;
    if (!db) throw new BackupError('backups_not_configured', 503);
    if (trigger === 'manual' && !env.COMMUNITY_BACKUP_ROOT) throw new BackupError('backups_not_configured', 503);
    const config = await readBackupConfig(db);
    const statements = [interruptStaleRuns(db), insertBackupRun(db, runId, nowMs, trigger, actor)];
    if (actor) statements.push(backupAudit(db, actor, 'backup_run', runId, 'Started a manual backup', { runId }, nowMs));
    await db.batch(statements);
    void execute(env, db, runId, config, nowMs).catch(() => console.error('community_backup_failed'))
      .finally(() => { active = null; release(); });
    return runId;
  } catch (error) {
    active = null;
    release();
    throw error;
  }
}

/**
 * Maintenance-loop hook: start and await a scheduled backup when one is due. Backups are disabled
 * without `COMMUNITY_BACKUP_ROOT`; a configured but unusable root is recorded as a failed run.
 */
export async function scheduledBackup(env: Env, nowMs: number): Promise<void> {
  if (closed || active || !env.COMMUNITY_BACKUP_ROOT || !env.CATALOG_DB) return;
  await backupHousekeeping(env, nowMs);
  if (closed) return;
  const config = await readBackupConfig(env.CATALOG_DB);
  if (!backupDue(config, await lastScheduledStart(env.CATALOG_DB), nowMs)) return;
  await startBackup(env, 'scheduled', null, nowMs);
  await backupDrain();
}
