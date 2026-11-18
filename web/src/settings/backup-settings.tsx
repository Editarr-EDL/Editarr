import { useEffect, useId, useState } from "react";
import { backupDownloadUrl, backupStatus, cancelBackup, checkBackupDownload, startBackupExport } from "../api/api-backups";
import type { BackupJob } from "../api/api-backups";
import { BackupScheduleSettings } from "./backup-schedule-settings";
import { GeneralSettingsSection } from "./general-settings-section";
import { useCan } from "../auth/auth-gate";

/** Create, download, and cancel full backups. Restore is offered only during first-run setup. */
export function BackupSettings({ initiallyOpen }: { initiallyOpen?: boolean }) {
  const mayCreate = useCan("backups.create");
  const mayDownload = useCan("backups.download");
  const mayDelete = useCan("backups.delete");
  const mayViewStatus = useCan("backups.status.view");
  const exportId = useId();
  const [exportOpen, setExportOpen] = useState(false);
  const [encrypt, setEncrypt] = useState(false);
  const [exportPassphrase, setExportPassphrase] = useState("");
  const [confirmPassphrase, setConfirmPassphrase] = useState("");
  const [job, setJob] = useState<BackupJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduleSummary, setScheduleSummary] = useState<string | null>(null);

  useEffect(() => {
    if (!job || !mayViewStatus || job.state !== "working") return;
    const timer = window.setInterval(() => {
      void backupStatus(job.id).then((next) => {
        setJob(next);
        if (next.state === "failed") setError(next.error || "Backup failed.");
      }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Unable to check backup status."));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.state, mayViewStatus]);

  async function exportArchive() {
    setError(null);
    if (encrypt && (!exportPassphrase || exportPassphrase !== confirmPassphrase)) {
      setError("Enter matching backup passphrases.");
      return;
    }
    setBusy(true);
    try {
      setJob(await startBackupExport(encrypt ? { encrypt, passphrase: exportPassphrase } : {}));
      setExportPassphrase(""); setConfirmPassphrase("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create backup."); }
    finally { setBusy(false); }
  }

  async function cancel() {
    if (!job) return;
    try { setJob(await cancelBackup(job.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to cancel backup."); }
  }

  async function download() {
    if (!job) return;
    setError(null);
    try {
      await checkBackupDownload(job.id);
      const link = document.createElement("a");
      link.href = backupDownloadUrl(job.id);
      link.download = `editarr-full.${job.inventory?.protection === "age-scrypt" ? "age" : "backup"}`;
      document.body.append(link);
      link.click();
      window.setTimeout(() => link.remove(), 0);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Backup download failed."); }
  }

  return <GeneralSettingsSection id="backups" title="Backup and restore" initiallyOpen={initiallyOpen}
    value={busy || job?.state === "working" ? "Backup in progress" : mayViewStatus ? scheduleSummary : null}
    bodyClassName="backup-settings">
    <BackupScheduleSettings onSummaryChange={setScheduleSummary} />
    <div className="general-settings-actions">
      {mayCreate && <button className="btn-secondary compact-button" type="button" aria-expanded={exportOpen}
        aria-controls={exportId} onClick={() => setExportOpen((value) => !value)}>
        {exportOpen ? "Hide backup options" : "Create backup"}
      </button>}
      <p className="muted-copy general-settings-note">Restore a backup from the first-run setup screen of a new, empty installation.</p>
    </div>
    {mayCreate && <div id={exportId} hidden={!exportOpen}>
    {exportOpen && <form className="general-settings-inline backup-export-options" onSubmit={(event) => { event.preventDefault(); void exportArchive(); }}>
      <label className="general-setting-toggle">
        <span>Encrypt backup</span>
        <input type="checkbox" checked={encrypt} onChange={(event) => {
          setEncrypt(event.target.checked);
          setExportPassphrase("");
          setConfirmPassphrase("");
        }} />
      </label>
      {encrypt && <div className="backup-schedule-grid">
        <label className="field"><span>Passphrase</span><input type="password" autoComplete="new-password" value={exportPassphrase} onChange={(event) => setExportPassphrase(event.target.value)} /></label>
        <label className="field"><span>Confirm passphrase</span><input type="password" autoComplete="new-password" value={confirmPassphrase} onChange={(event) => setConfirmPassphrase(event.target.value)} /></label>
      </div>}
      <p className="muted-copy general-settings-note">{encrypt ? "Protected with age passphrase encryption." : "Unencrypted: included credentials and signing keys are readable to anyone with the file."} Existing environment credentials, media, caches and sidecars are excluded. Create a new backup after changing signing keys or saved credentials.</p>
      <div className="general-settings-actions">
        <button className="btn-primary compact-button" type="submit" disabled={busy || job?.state === "working"}>Create backup</button>
      </div>
    </form>}
    </div>}
    {mayCreate && mayDownload && job?.state === "ready" && <div className="general-settings-inline backup-export-result">
      <p>Included: {job.inventory?.included_secret_types.join(", ") || "No saved credentials"}. Community identity {job.inventory?.identity_included ? "included" : "not present"}. Reconfigure external credentials: {job.inventory?.omitted_environment.join(", ") || "None"}.</p>
      <div className="general-settings-actions">
        <button className="btn-secondary compact-button" onClick={() => void download()}>Download backup</button>
        <p className="muted-copy general-settings-note">Keep this page open until the download completes. Retry the download if it fails.</p>
      </div>
    </div>}
    {job && <div role="status" className="form-status"><p>{job.state === "ready" ? mayDownload ? "Backup ready for download." : "Backup created." : job.state === "working" ? "Processing backup…" : job.state === "failed" ? "Backup failed." : job.state === "cancelled" ? "Backup cancelled." : ""}</p>{mayDelete && job.state !== "cancelled" && <button className="btn-secondary" onClick={() => void cancel()}>Cancel job</button>}</div>}
    {error && <p role="alert" className="form-status">{error}</p>}
  </GeneralSettingsSection>;
}
