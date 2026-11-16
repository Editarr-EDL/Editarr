import { useEffect, useState } from "react";
import {
  activateSetupRestore, beginSetupRestore, cancelSetupRestore, preflightSetupRestore,
  setupRestoreStatus, startSetupRestore, uploadSetupRestore,
} from "../api/api-backups";
import type { BackupJob, RestorePlan } from "../api/api-backups";
import { ApiError } from "../api/api-transport";

const refusals: Record<string, string> = {
  restore_target_has_accounts: "This installation already has accounts. A backup can only be restored into a new, empty installation.",
  restore_target_has_work: "This installation already has scans, reviews, or settings. Connect a Jellyfin server to keep them, or start Editarr with a new, empty data directory to restore.",
  restore_target_has_identity: "This installation already has a community identity. Start Editarr with a new, empty data directory to restore.",
  restore_target_unsupported_schema: "This installation's database is not a version this Editarr can restore over. Start Editarr with a new, empty data directory to restore.",
  restore_settings_backup: "This is a settings-only backup, which can no longer be restored. Choose a full backup.",
  restore_owner_count: "This backup does not have exactly one owner account, so it cannot be restored.",
  restore_owner_credential: "This backup's owner has neither a usable password nor a Jellyfin link, so it cannot be restored.",
  restore_unknown_account_reference: "This backup refers to an account it does not contain, so it cannot be restored.",
  restore_unknown_identity_home: "This backup holds a community identity for an account it does not contain, so it cannot be restored.",
};

function failure(job: BackupJob): string {
  return (job.reason && refusals[job.reason]) || job.error || "Restore failed. Check the backup file and try again.";
}

// The browser holds one flow cookie and each flow response replaces it, so two flow requests
// in flight could leave a panel's token paired with the other flow's cookie. Every panel that
// mounts while a request is pending, including after a remount, shares that request.
let pendingFlow: Promise<string> | null = null;

function beginFlow(): Promise<string> {
  pendingFlow ??= beginSetupRestore().finally(() => { pendingFlow = null; });
  return pendingFlow;
}

/** First-run restore of a full backup into this empty installation. `onRestored` fires once it commits. */
export function SetupRestore({ onComplete, onRestored }: { onComplete: () => void; onRestored?: () => void }) {
  const [token, setToken] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [protection, setProtection] = useState<"plain" | "age-scrypt" | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [job, setJob] = useState<BackupJob | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [restored, setRestored] = useState(false);
  // Activation clears the job's plan, so keep the backup's binding for the notice.
  const [binding, setBinding] = useState<RestorePlan["backup_deployment"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void beginFlow().then(setToken).catch((reason: unknown) => {
      const code = reason instanceof ApiError ? reason.code : undefined;
      setError((code && refusals[code]) || "Unable to start restore. Try again.");
    });
  }, []);

  useEffect(() => { if (restored) onRestored?.(); }, [restored, onRestored]);

  useEffect(() => {
    if (!job || (job.state !== "working" && job.state !== "activating")) return;
    const activating = job.state === "activating";
    const timer = window.setInterval(() => {
      void setupRestoreStatus(job.id).then((next) => {
        setJob(next);
        if (next.state === "activated") setRestored(true);
        if (next.state === "failed") setError(failure(next));
      }).catch((reason: unknown) => {
        // Restoring an owner completes setup, which closes this flow.
        if (activating && reason instanceof ApiError && reason.code === "setup_already_complete") setRestored(true);
        else setError(reason instanceof Error ? reason.message : "Unable to check restore status.");
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state]);

  async function selectArchive(selected: File | null) {
    if (job && token && job.state !== "activating" && job.state !== "activated") void cancelSetupRestore(job.id, token).catch(() => {});
    setFile(selected); setJob(null); setConfirmed(false); setError(null); setPassphrase("");
    setProtection(selected && await selected.slice(0, 22).text() === "age-encryption.org/v1\n" ? "age-scrypt" : selected ? "plain" : null);
  }

  async function inspect() {
    if (!file || !token) return;
    setBusy(true); setError(null);
    let created: BackupJob | null = null;
    try {
      created = await startSetupRestore(token);
      setJob(created);
      await uploadSetupRestore(created.id, file, token);
      setJob(await preflightSetupRestore(created.id, token, protection === "age-scrypt" ? passphrase : undefined));
      setPassphrase("");
    } catch (reason) {
      const code = reason instanceof ApiError ? reason.code : undefined;
      setError((code && refusals[code]) || (reason instanceof Error ? reason.message : "Unable to inspect backup."));
      if (created) void cancelSetupRestore(created.id, token).catch(() => {});
    } finally { setBusy(false); }
  }

  async function activate() {
    if (!job || !token) return;
    setBusy(true); setError(null); setBinding(job.plan?.backup_deployment ?? null);
    try {
      const next = await activateSetupRestore(job.id, token);
      setJob(next); setConfirmed(false);
      if (next.state === "activated") setRestored(true);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to restore backup."); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (!job || !token) return;
    try { setJob(await cancelSetupRestore(job.id, token)); setConfirmed(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to cancel restore."); }
  }

  if (restored) {
    return <div className="backup-settings" role="status">
      <h3>Restore complete</h3>
      <p>Review media paths and source connections in Settings. Scans stay paused until you remove restore-reconciliation.json from the data directory after that review.</p>
      {binding && <p>This installation keeps its own base URL and listening port. The backup used base URL {binding.base_url ?? "default"} and port {binding.port ?? "default"}. To adopt them, change them under Deployment in Settings.</p>}
      {(job?.report?.move_disagreements ?? 0) > 0 && <p>{job?.report?.move_disagreements} account transfer or link records disagree with the files on disk. Every file was kept; see restore-report.json in the data directory.</p>}
      <button className="btn-primary" type="button" onClick={onComplete}>Continue</button>
    </div>;
  }

  const plan = job?.state === "ready" ? job.plan : null;
  const working = busy || job?.state === "working" || job?.state === "activating";
  return <div className="backup-settings">
    <label className="field"><span>Backup file</span><input type="file" disabled={!token} onChange={(event) => void selectArchive(event.target.files?.[0] || null)} /></label>
    {protection && <p className="muted-copy">Protection: {protection === "plain" ? "Unencrypted" : "age passphrase"}</p>}
    {protection === "age-scrypt" && <label className="field"><span>Backup passphrase</span><input type="password" autoComplete="off" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} /></label>}
    <button className="btn-secondary" onClick={() => void inspect()} disabled={!file || !token || working || (protection === "age-scrypt" && !passphrase)}>Inspect backup</button>
    {plan && <div className="backup-preview"><h3>Restore preview</h3>
      <p>Full backup · {plan.protection === "plain" ? "Unencrypted" : "Encrypted"}</p>
      <p>Owner community identity: {plan.source_identity?.key_id || "None"}. Other accounts' community identities: {plan.account_identities}.</p>
      <p>Included secret types: {plan.included_secret_types.join(", ") || "None"}. External credentials to reconfigure: {plan.omitted_environment.join(", ") || "None"}.</p>
      <p role="status">{plan.administrator_included ? "Sign in as the restored owner after restore." : "This backup has no accounts. Connect a Jellyfin server after restore."} The backup passphrase only unlocks the archive.</p>
      <p className="muted-copy">Everything in the backup is restored: accounts, API keys, community identities, settings, and local work. This installation keeps its own base URL and listening port. Browser sessions are signed out. Afterwards, review media paths and source connections.</p>
      <label className="field"><span><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> Restore this backup into this new installation</span></label>
      <button className="danger-button" onClick={() => void activate()} disabled={!confirmed || working}>Restore backup</button>
    </div>}
    {job && <div role="status" className="form-status"><p>{job.state === "activating" ? "Restoring backup…" : job.state === "working" ? "Checking backup…" : job.state === "cancelled" ? "Restore cancelled." : ""}</p>{job.state !== "cancelled" && job.state !== "activating" && <button className="btn-secondary" onClick={() => void cancel()}>Cancel restore</button>}</div>}
    {error && <p role="alert" className="form-status">{error}</p>}
  </div>;
}
