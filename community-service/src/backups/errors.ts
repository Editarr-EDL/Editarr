/** A backup failure with a stable code; `status` is the HTTP status the operator API answers with. */
export class BackupError extends Error {
  constructor(public readonly code: string, public readonly status = 400) { super(code); }
}

/** Map a run failure to the `backup_runs.error_code` recorded for operators (never raw messages). */
export function runErrorCode(error: unknown): string {
  if (error instanceof BackupError) {
    if (['integrity_failed', 'verification_failed', 'destination_full', 'destination_unwritable'].includes(error.code)) return error.code;
    if (error.code.startsWith('backup_destination_') || error.code === 'backup_root_invalid') return 'destination_invalid';
    return 'snapshot_failed';
  }
  const errno = (error as { code?: unknown } | null)?.code;
  if (errno === 'ENOSPC' || errno === 'EDQUOT') return 'destination_full';
  if (errno === 'EACCES' || errno === 'EPERM' || errno === 'EROFS') return 'destination_unwritable';
  return 'snapshot_failed';
}
