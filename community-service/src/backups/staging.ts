import { chmod, copyFile, lstat, rename, rm, stat, constants } from 'node:fs/promises';
import { basename, join } from 'node:path';
import type { Env } from '../config';
import type { SqlDatabase } from '../runtime/contracts';
import { fileSha256 } from '../runtime/restore.mjs';
import { backupRoot, confinedDirectory, confinedFile, fsyncPath, STAGING_DIRECTORY } from './destination';
import { verifySnapshotFile } from './engine';
import { BackupError } from './errors';
import { BACKUP_FILE, removeMatching } from './housekeeping';
import { backupAudit, readBackupRun, type BackupRunRow } from './store';

const COMPOSE = 'docker compose -f community-service/compose.yaml';
let staging = false;

/** Whether a stage is copying or verifying; partial-file cleanup leaves the staging directory alone meanwhile. */
export const stageInProgress = () => staging;

/** Look up a verified run and its root-confined file; 404 for unknown runs, 409 when not verified. */
export async function verifiedBackupFile(env: Env, db: SqlDatabase, runId: string): Promise<{ run: BackupRunRow; root: string; path: string }> {
  const run = await readBackupRun(db, runId);
  if (!run) throw new BackupError('backup_not_found', 404);
  if (run.status !== 'verified' || !run.file_name || !run.sha256) throw new BackupError('backup_not_verified', 409);
  const root = await backupRoot(env.COMMUNITY_BACKUP_ROOT);
  if (!root) throw new BackupError('backups_not_configured', 503);
  let path: string;
  try { path = await confinedFile(root, run.file_name); }
  catch { throw new BackupError('backup_file_unavailable', 409); }
  if ((await stat(path)).size !== run.bytes) throw new BackupError('backup_file_unavailable', 409);
  return { run, root, path };
}

/**
 * The offline restore for a staged copy: stop the API, then restore into a new named volume with
 * the networkless `restore` maintenance service, which re-checks the SHA-256 and the store schema.
 */
export function restoreCommands(stagedPath: string, sha256: string, volume: string): string[] {
  return [
    `${COMPOSE} stop community`,
    `COMMUNITY_RESTORE_VOLUME=${volume} ${COMPOSE} run --rm --no-deps restore --confirm --source ${stagedPath} --sha256 ${sha256}`,
  ];
}

/**
 * Copy a verified backup into `<root>/staged/` (0600, never overwriting a different file), then
 * re-verify the copy's SHA-256, schema and integrity before returning the offline restore commands.
 * Only the newest staged copy is kept: a successful stage removes older staged backups. One stage
 * runs at a time (`409 backup_stage_running`).
 */
export async function stageBackup(env: Env, db: SqlDatabase, runId: string, actor: string, nowMs: number) {
  if (staging) throw new BackupError('backup_stage_running', 409);
  staging = true;
  try { return await stageExclusive(env, db, runId, actor, nowMs); }
  finally { staging = false; }
}

async function stageExclusive(env: Env, db: SqlDatabase, runId: string, actor: string, nowMs: number) {
  const { run, root, path } = await verifiedBackupFile(env, db, runId);
  const directory = await confinedDirectory(root, STAGING_DIRECTORY, true);
  const name = basename(path), target = join(directory, name);
  const existing = await lstat(target).catch(() => null);
  if (existing) {
    if (existing.isSymbolicLink() || !existing.isFile() || await fileSha256(target) !== run.sha256)
      throw new BackupError('backup_stage_conflict', 409);
  } else {
    const partial = join(directory, `.${name}.partial`);
    await rm(partial, { force: true });
    try {
      await copyFile(path, partial, constants.COPYFILE_EXCL);
      await chmod(partial, 0o600);
      if (await fileSha256(partial) !== run.sha256) throw new BackupError('backup_checksum_mismatch', 409);
      await fsyncPath(partial);
      await rename(partial, target);
      await fsyncPath(directory);
    } catch (error) { await rm(partial, { force: true }); throw error; }
  }
  if (await fileSha256(target) !== run.sha256) throw new BackupError('backup_checksum_mismatch', 409);
  const { version } = await verifySnapshotFile(target, true, env.BACKUP_VERIFIER);
  await removeMatching(directory, BACKUP_FILE, name);
  const volume = `editarr-community-restore-${run.run_id.slice(0, 8)}`;
  await db.batch([backupAudit(db, actor, 'backup_stage', run.run_id, 'Staged a backup for offline restore',
    { runId: run.run_id, stagedPath: target }, nowMs)]);
  return { version: 1, observedAt: nowMs, runId: run.run_id, stagedPath: target, sha256: run.sha256, bytes: run.bytes,
    schemaVersion: version, restoreVolume: volume, commands: restoreCommands(target, run.sha256, volume) };
}
