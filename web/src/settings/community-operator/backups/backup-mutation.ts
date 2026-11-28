import { useEffect, useRef, useState } from "react";
import { accessFailureStatus, OperatorMutationError } from "../../../api/api-community-operator";
import { OperatorAccessError } from "../runtime";
import { useOperator } from "../shared/operator-data";

const messages: Record<string, string> = {
  invalid_backup_config: "The service rejected the schedule", backup_destination_invalid: "the destination path is invalid",
  backup_destination_outside_root: "the destination is outside the backup root", backup_destination_symlink: "the destination resolves through a symlink",
  backup_destination_unwritable: "the destination is not writable", backups_not_configured: "backups are not configured on the host",
  backup_running: "a backup is already running", backup_not_found: "that backup no longer exists", backup_not_verified: "that backup is not verified",
  snapshot_failed: "snapshot failed", verification_failed: "verification failed", integrity_failed: "integrity check failed",
  destination_unwritable: "destination not writable", destination_full: "destination full", destination_invalid: "destination invalid",
  interrupted: "interrupted", backup_destination_missing: "the destination folder does not exist",
  backup_root_invalid: "the backup root is invalid", backup_destination_full: "the destination is full",
  backup_checksum_mismatch: "the checksum does not match", backup_config_missing: "the backup schedule is missing",
  backup_engine_unavailable: "the backup engine is unavailable", backup_file_unavailable: "the backup file is unavailable",
  backup_stage_conflict: "a restore is already staged", invalid_backup_request: "the request was invalid",
};
/** A readable phrase for a backup error code (unknown codes are shown as-is). */
export const mutationMessage = (code: string) => messages[code] ?? code.replace(/_/g, " ");

/**
 * One backup mutation at a time. `run` sends `send`, reports `success(result)` as a status, maps service error codes to
 * alerts and ends the session on 401/403. Unconfirmed outcomes say so rather than claiming success or failure.
 */
export function useMutation() {
  const { onAccessFailure } = useOperator();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const run = <T,>(send: (signal: AbortSignal) => Promise<T>, success: (result: T) => string) => {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller; setBusy(true); setMessage("");
    // Wrappers validate synchronously; run `send` in a continuation so a throw still settles the form.
    Promise.resolve().then(() => send(controller.signal)).then(result => { if (!controller.signal.aborted) { setFailed(false); setMessage(success(result)); } })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const denied = accessFailureStatus(error);
        if (denied) { onAccessFailure(denied); return; }
        setFailed(true);
        setMessage(error instanceof OperatorMutationError ? `Refused: ${mutationMessage(error.code)}.` : error instanceof OperatorAccessError && error.status === 400
          ? "Refused: check the values and try again." : "Outcome unconfirmed. Refresh to see the current state before retrying.");
      })
      .finally(() => { if (pending.current === controller) { pending.current = null; setBusy(false); } });
  };
  return { run, busy, message, failed };
}
