import { backup, DatabaseSync } from 'node:sqlite';
import { Worker } from 'node:worker_threads';
import { chmod, open } from 'node:fs/promises';
import { BackupError } from './errors';

/** Pages copied per backup step; between steps the event loop keeps serving requests. */
export const SNAPSHOT_PAGES_PER_STEP = 256;

/** The serving `node:sqlite` connection behind the runtime adapter (duck-typed: the adapter may be bundled twice). */
export function liveConnection(db: unknown): DatabaseSync {
  const connection = (db as { connection?: unknown } | null | undefined)?.connection;
  if (!(connection instanceof DatabaseSync)) throw new BackupError('backup_engine_unavailable', 503);
  return connection;
}

/**
 * Write a consistent online snapshot of the serving connection to a new 0600 file using the SQLite
 * online backup API. The runtime holds the WAL database with an exclusive lock, so the snapshot
 * must use the serving connection; writes made through it during the copy are applied to the
 * destination by SQLite, so the result is one transaction-consistent state without blocking the
 * event loop. The copy is switched to rollback journaling so read-only verification and restore
 * staging never need -wal/-shm side files.
 */
export async function snapshotDatabase(connection: DatabaseSync, path: string): Promise<void> {
  const handle = await open(path, 'wx', 0o600);
  await handle.close();
  // Node settles backup() from a thread-pool completion without draining microtasks, so on an idle
  // loop the await would stall until unrelated I/O arrives; a short timer keeps it progressing.
  const pump = setInterval(() => undefined, 10);
  try { await backup(connection, path, { rate: SNAPSHOT_PAGES_PER_STEP }); }
  finally { clearInterval(pump); }
  await chmod(path, 0o600);
  const copy = new DatabaseSync(path);
  try { copy.exec('PRAGMA journal_mode = DELETE'); }
  finally { copy.close(); }
}

/**
 * Verify a snapshot in a worker thread with the offline store checks (lineage, exact known schema,
 * and optionally integrity_check plus foreign_key_check). Resolves the snapshot's schema version.
 */
export function verifySnapshotFile(path: string, integrity: boolean,
  verifier = new URL('../runtime/backup-verify.mjs', import.meta.url)): Promise<{ version: number; current: number }> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(verifier, { workerData: { path, integrity }, execArgv: [] });
    let settled = false;
    worker.once('message', (message: { ok: boolean; code?: string; version?: number; current?: number }) => {
      settled = true;
      if (message.ok) resolve({ version: message.version!, current: message.current! });
      else reject(new BackupError(message.code === 'database_integrity_failed' ? 'integrity_failed' : 'verification_failed', 409));
    });
    worker.once('error', () => { if (!settled) { settled = true; reject(new BackupError('verification_failed', 409)); } });
    worker.once('exit', () => { if (!settled) { settled = true; reject(new BackupError('verification_failed', 409)); } });
  });
}
