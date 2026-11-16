import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  disableBackupSchedule, fetchBackupSchedule, saveBackupSchedule,
  type BackupScheduleConfig, type BackupScheduleStatus,
} from "../api/api-backup-schedule";
import { useCan } from "../auth/auth-gate";
import "../styles/backup-schedule.css";

const errors: Record<string, string> = {
  destination_unavailable: "Backup destination is missing, changed, or not writable.",
  credential_unavailable: "The protected passphrase file is unavailable. No unencrypted fallback was created.",
  export_failed: "Backup failed. Check disk space and backup prerequisites. Previous backups were retained.",
  retention_requires_review: "Backup retention needs review. Changed files have not been deleted.",
  previous_run_interrupted: "The previous backup was interrupted. Any unconfirmed output has been retained.",
};
const timestamp = (value: number | null) => value === null ? "Never" : new Date(value * 1000).toLocaleString();

export function BackupScheduleSettings() {
  const mayEdit = useCan("backups.schedule.edit");
  const mayView = useCan("backups.status.view");
  if (!mayEdit && !mayView) return null;
  return <section className="backup-schedule" aria-labelledby="backup-schedule-heading">
    <h3 id="backup-schedule-heading">Scheduled backups</h3>
    <ScheduleForm mayEdit={mayEdit} mayView={mayView} />
  </section>;
}

function ScheduleForm({ mayEdit, mayView }: { mayEdit: boolean; mayView: boolean }) {
  const [status, setStatus] = useState<BackupScheduleStatus | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [hours, setHours] = useState("");
  const [keep, setKeep] = useState("");
  const [destination, setDestination] = useState("");
  const [encrypted, setEncrypted] = useState(false);
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const initialized = useRef(false);
  const generation = useRef(0);
  const saving = useRef(false);

  function acceptDraft(config: BackupScheduleConfig | null) {
    setEnabled(config?.enabled ?? false);
    setHours(config ? String(config.interval_hours) : "");
    setKeep(config ? String(config.keep_count) : "");
    setDestination(config?.destination ?? "");
    setEncrypted(config?.protection === "age-scrypt");
    setCredential(config?.passphrase_file ?? "");
    initialized.current = true;
  }

  useEffect(() => {
    if (!mayView) return;
    let disposed = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    async function load() {
      window.clearTimeout(timer);
      if (document.hidden || saving.current) return;
      controller?.abort();
      controller = new AbortController();
      const requestGeneration = ++generation.current;
      try {
        const next = await fetchBackupSchedule(controller.signal);
        if (disposed || requestGeneration !== generation.current) return;
        setStatus(next); setError(null);
        if (!initialized.current) acceptDraft(next.config);
      } catch (reason) {
        if (!disposed && requestGeneration === generation.current && !controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Backup schedule is unavailable.");
        }
      } finally {
        if (!disposed && !document.hidden) timer = window.setTimeout(() => void load(), 30000);
      }
    }
    const visibility = () => {
      window.clearTimeout(timer);
      if (document.hidden) controller?.abort();
      else void load();
    };
    void load();
    document.addEventListener("visibilitychange", visibility);
    return () => { disposed = true; controller?.abort(); window.clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [refresh, mayView]);

  const intervalHours = Number(hours), keepCount = Number(keep);
  const valid = Number.isInteger(intervalHours) && intervalHours >= 1 && intervalHours <= 8760
    && Number.isInteger(keepCount) && keepCount >= 1 && keepCount <= 1000
    && destination.trim().length > 0 && (!encrypted || credential.trim().length > 0);
  const running = status?.outcome === "running";

  async function mutate(disable: boolean) {
    if (saving.current || (!disable && !valid)) return;
    saving.current = true; generation.current += 1;
    setBusy(true); setError(null); setNotice(null);
    try {
      const next = disable ? await disableBackupSchedule() : await saveBackupSchedule({
        enabled, interval_hours: intervalHours, keep_count: keepCount, destination,
        kind: "full", protection: encrypted ? "age-scrypt" : "plain",
        passphrase_file: encrypted ? credential : null,
      });
      setStatus(next); acceptDraft(next.config);
      setNotice(next.config?.enabled ? "Backup schedule saved." : "Scheduled backups disabled.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save the backup schedule.");
    } finally {
      saving.current = false; setBusy(false); setRefresh((current) => current + 1);
    }
  }

  function submit(event: FormEvent) { event.preventDefault(); void mutate(false); }
  const stateLabel = status?.restore_hold ? "Paused for restore reconciliation" : !status?.config?.enabled ? "Disabled"
    : running ? "Running" : status?.outcome === "failed" ? "Failed"
    : status?.outcome === "interrupted" ? "Interrupted" : "Scheduled";

  return <>
    {mayView && <div className="backup-schedule-status">
      <p role="status">{status ? stateLabel : "Loading schedule..."}</p>
      <button className="btn-secondary" type="button" disabled={busy} onClick={() => setRefresh((current) => current + 1)}>Refresh status</button>
    </div>}
    {status && <dl className="backup-schedule-summary">
      <div><dt>Next attempt</dt><dd>{status.next_run === null ? "Not scheduled" : timestamp(status.next_run)}</dd></div>
      <div><dt>Last attempt</dt><dd>{timestamp(status.last_attempt)}</dd></div>
      <div><dt>Last successful backup</dt><dd>{timestamp(status.last_success)}</dd></div>
      <div><dt>Verified backups retained</dt><dd>{status.retained_count}</dd></div>
    </dl>}
    {status?.notice === "settings_schedule_now_full" && <p role="status" className="form-status">This schedule made settings-only backups, which no longer exist. It now makes full backups. Save the schedule to dismiss this notice.</p>}
    {status?.error && <p role="status" className="form-status">{errors[status.error] ?? "Scheduled backups need review."}</p>}
    {mayEdit && <form onSubmit={submit}>
      <fieldset className="backup-schedule-fields" aria-label="Backup schedule configuration" disabled={(mayView && !status) || busy || running}>
        <label className="backup-schedule-toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>Enable scheduled backups</span></label>
        <div className="backup-schedule-grid">
          <label className="field"><span>Interval (hours)</span><input type="number" min="1" max="8760" step="1" required value={hours} onChange={(event) => setHours(event.target.value)} /></label>
          <label className="field"><span>Backups to keep</span><input type="number" min="1" max="1000" step="1" required value={keep} onChange={(event) => setKeep(event.target.value)} /></label>
          <label className="field backup-schedule-wide"><span>Backup destination</span><input type="text" required autoComplete="off" spellCheck={false} value={destination} onChange={(event) => setDestination(event.target.value)} /></label>
        </div>
        <label className="backup-schedule-toggle"><input type="checkbox" checked={encrypted} onChange={(event) => { setEncrypted(event.target.checked); setCredential(""); }} /><span>Encrypt scheduled backups</span></label>
        {encrypted && <label className="field"><span>Protected passphrase file</span><input type="text" required autoComplete="off" spellCheck={false} value={credential} onChange={(event) => setCredential(event.target.value)} /></label>}
        <p className="muted-copy">{encrypted ? "Encryption requires an external owner-only passphrase file. Missing credentials fail the run." : "Unencrypted backups contain included credentials and signing keys."}</p>
        <p className="muted-copy">Oldest scheduled backups beyond the retained count are deleted only after a verified backup succeeds. Manual backups are not deleted.</p>
        <div className="backup-schedule-actions">
          <button className="btn-primary" type="submit" disabled={!valid}>{busy ? "Saving..." : "Save schedule"}</button>
          {(status?.config?.enabled || !mayView) && <button className="btn-secondary" type="button" onClick={() => void mutate(true)}>Disable schedule</button>}
        </div>
      </fieldset>
    </form>}
    {notice && <p role="status" className="form-status">{notice}</p>}
    {error && <p role="alert" className="form-status">{error}</p>}
  </>;
}