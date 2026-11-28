import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { BackupFrequency } from './schedule';

export interface BackupConfigRow {
  enabled: number; frequency: BackupFrequency; at_utc: string; keep_last: number; keep_weekly: number;
  destination: string; integrity_check: number; updated_at: number; updated_by: string | null;
}
export interface BackupRunRow {
  run_id: string; started_at: number; trigger: 'scheduled' | 'manual'; actor: string | null; file_name: string | null;
  bytes: number | null; duration_ms: number | null; integrity: 'ok' | 'failed' | 'skipped' | null; sha256: string | null;
  status: 'running' | 'verified' | 'failed' | 'pruned'; error_code: string | null; note: string | null;
}
export type BackupCommand = 'backup_configure' | 'backup_run' | 'backup_stage';

export async function readBackupConfig(db: SqlDatabase): Promise<BackupConfigRow> {
  const row = await db.prepare(`SELECT enabled, frequency, at_utc, keep_last, keep_weekly, destination, integrity_check,
    updated_at, updated_by FROM backup_config WHERE singleton = 1`).first<BackupConfigRow>();
  if (!row) throw new Error('backup_config_missing');
  return row;
}

export function saveBackupConfig(db: SqlDatabase, config: Omit<BackupConfigRow, 'updated_by'>, actor: string): SqlStatement {
  return db.prepare(`UPDATE backup_config SET enabled = ?, frequency = ?, at_utc = ?, keep_last = ?, keep_weekly = ?,
    destination = ?, integrity_check = ?, updated_at = ?, updated_by = ? WHERE singleton = 1`)
    .bind(config.enabled, config.frequency, config.at_utc, config.keep_last, config.keep_weekly, config.destination,
      config.integrity_check, config.updated_at, actor);
}

/** One `moderation_actions` audit row (resource `backup`) for an operator backup mutation. */
export function backupAudit(db: SqlDatabase, actor: string, command: BackupCommand, target: string, reason: string,
  request: unknown, nowMs: number): SqlStatement {
  return db.prepare(`INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision,
    reason, request_json, outcome, error_code, created_at) VALUES (?, ?, 'backup', ?, ?, 0, ?, ?, 'applied', NULL, ?)`)
    .bind(crypto.randomUUID(), actor, target, command, reason, JSON.stringify(request), nowMs);
}

export async function listBackupRuns(db: SqlDatabase, limit: number): Promise<BackupRunRow[]> {
  return (await db.prepare('SELECT * FROM backup_runs ORDER BY started_at DESC, run_id DESC LIMIT ?').bind(limit).all<BackupRunRow>()).results;
}

export function readBackupRun(db: SqlDatabase, runId: string): Promise<BackupRunRow | null> {
  return db.prepare('SELECT * FROM backup_runs WHERE run_id = ?').bind(runId).first<BackupRunRow>();
}

export async function lastScheduledStart(db: SqlDatabase): Promise<number | null> {
  const row = await db.prepare("SELECT max(started_at) AS at FROM backup_runs WHERE trigger = 'scheduled'").first<{ at: number | null }>();
  return row?.at ?? null;
}

export function insertBackupRun(db: SqlDatabase, runId: string, startedAt: number, trigger: 'scheduled' | 'manual', actor: string | null): SqlStatement {
  return db.prepare(`INSERT INTO backup_runs (run_id, started_at, trigger, actor, status) VALUES (?, ?, ?, ?, 'running')`)
    .bind(runId, startedAt, trigger, actor);
}

/** Runs left `running` by a stopped process; only called while this process holds the run lock. */
export function interruptStaleRuns(db: SqlDatabase): SqlStatement {
  return db.prepare("UPDATE backup_runs SET status = 'failed', error_code = 'interrupted' WHERE status = 'running'");
}

export async function completeBackupRun(db: SqlDatabase, runId: string, result: { fileName: string; bytes: number;
  durationMs: number; integrity: 'ok' | 'skipped'; sha256: string; note: string | null }): Promise<void> {
  await db.prepare(`UPDATE backup_runs SET status = 'verified', file_name = ?, bytes = ?, duration_ms = ?, integrity = ?,
    sha256 = ?, note = ?, error_code = NULL WHERE run_id = ?`).bind(result.fileName, result.bytes, result.durationMs,
    result.integrity, result.sha256, result.note, runId).run();
}

export async function failBackupRun(db: SqlDatabase, runId: string, code: string, durationMs: number): Promise<void> {
  await db.prepare(`UPDATE backup_runs SET status = 'failed', error_code = ?, duration_ms = ?,
    integrity = CASE WHEN ? = 'integrity_failed' THEN 'failed' ELSE integrity END WHERE run_id = ?`)
    .bind(code, durationMs, code, runId).run();
}

export async function verifiedBackupRuns(db: SqlDatabase): Promise<Pick<BackupRunRow, 'run_id' | 'started_at' | 'file_name'>[]> {
  return (await db.prepare(`SELECT run_id, started_at, file_name FROM backup_runs WHERE status = 'verified'
    ORDER BY started_at DESC, run_id DESC`).all<Pick<BackupRunRow, 'run_id' | 'started_at' | 'file_name'>>()).results;
}

export async function markBackupPruned(db: SqlDatabase, runId: string): Promise<void> {
  await db.prepare("UPDATE backup_runs SET status = 'pruned' WHERE run_id = ? AND status = 'verified'").bind(runId).run();
}

export function apiConfig(row: BackupConfigRow) {
  return { enabled: row.enabled === 1, frequency: row.frequency, atUtc: row.at_utc, keepLast: row.keep_last,
    keepWeekly: row.keep_weekly, destination: row.destination, integrityCheck: row.integrity_check === 1,
    updatedAt: row.updated_at, updatedBy: row.updated_by };
}

export function apiRun(row: BackupRunRow) {
  return { runId: row.run_id, startedAt: row.started_at, trigger: row.trigger, actor: row.actor, fileName: row.file_name,
    bytes: row.bytes, durationMs: row.duration_ms, integrity: row.integrity, sha256: row.sha256, status: row.status,
    errorCode: row.error_code, note: row.note };
}
