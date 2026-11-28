import { useState } from "react";
import { backupDownloadHref, writeOperatorBackupStage, type BackupRun, type StagedBackup } from "../../../api/api-community-operator-system";
import { bytes, dayTime, duration } from "../shared/format";
import { mutationMessage, useMutation } from "./backup-mutation";

const integrity = (run: BackupRun) => run.status === "running" ? <span className="chip blue">running</span>
  : run.status === "failed" ? <span className="chip bad">✕ failed</span> : run.integrity === "ok" ? <span className="chip ok">✓ passed</span>
  : run.integrity === "skipped" ? <span className="chip">not checked</span> : run.status === "pruned" ? <span className="chip dim">pruned</span>
  : <span className="chip bad">✕ check failed</span>;

/** Recent backup runs with size, duration, integrity and a download link for verified files. */
export function RunsTable({ runs, now }: { runs: BackupRun[]; now: number }) {
  return <div className="tw" role="region" aria-label="Backup runs table" tabIndex={0}><table className="t"><caption className="sr">Recent backup runs</caption>
    <thead><tr><th scope="col">Started</th><th scope="col">Trigger</th><th scope="col" className="r">Size</th><th scope="col" className="r">Duration</th>
      <th scope="col">Integrity</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
    <tbody>{runs.map(run => { const href = run.status === "verified" ? backupDownloadHref(run.runId) : null;
      return <tr key={run.runId}><td>{dayTime(run.startedAt, now)}</td>
        <td className="wrap">{run.trigger === "manual" ? `Manual${run.actor ? ` · ${run.actor}` : ""}` : "Scheduled"}
          {run.note && <div className="faint small">“{run.note}”</div>}</td>
        <td className="r num">{bytes(run.bytes)}</td><td className="r num">{duration(run.durationMs)}</td>
        <td className="wrap">{integrity(run)}{run.errorCode && <div className="faint small">{mutationMessage(run.errorCode)}</div>}</td>
        <td className="r">{href && <a className="btn sm" href={href} download>Download<span className="sr"> backup from {dayTime(run.startedAt, now)}</span></a>}</td></tr>; })}
      {!runs.length && <tr><td colSpan={6} className="muted">No backups yet. Run one now or enable the schedule.</td></tr>}</tbody></table></div>;
}

/** Offline restore guidance; staging a verified backup re-verifies it and fills in the exact host commands. */
export function RestoreSteps({ runs, now }: { runs: BackupRun[]; now: number }) {
  const verified = runs.filter(run => run.status === "verified");
  const [selected, setSelected] = useState(verified[0]?.runId ?? "");
  const [staged, setStaged] = useState<StagedBackup | null>(null);
  const mutation = useMutation();
  const run = verified.find(entry => entry.runId === selected) ?? null;
  const stage = () => mutation.run(signal => writeOperatorBackupStage(selected, signal), result => {
    setStaged(result); return `Staged and re-verified · sha256 ${result.sha256.slice(0, 12)}…`; });
  return <section className="card restore" aria-labelledby="restore-title">
    <div className="panel-head"><div><h2 id="restore-title">Restore a backup</h2><p className="muted small">Restores are staged here and applied offline on the
      host, never from this page.</p></div>
      {verified.length > 0 && <div className="actions"><label htmlFor="restore-run" className="sr">Backup to restore</label>
        <select id="restore-run" className="btn" value={selected} onChange={event => { setSelected(event.target.value); setStaged(null); }}>
          {verified.map(entry => <option key={entry.runId} value={entry.runId}>{dayTime(entry.startedAt, now)} ({bytes(entry.bytes)})</option>)}</select>
        <button type="button" className="btn primary" disabled={!run || mutation.busy} onClick={stage}>{mutation.busy ? "Staging…" : "Stage for restore"}</button></div>}
    </div>
    {mutation.message && <p role={mutation.failed ? "alert" : "status"} className={`sec ${mutation.failed ? "error" : "muted"}`}>{mutation.message}</p>}
    <RestoreGuide staged={staged} run={run} hasVerified={verified.length > 0} now={now} />
  </section>;
}

/**
 * The four offline restore steps from docs/community-service-docker.md. The restore CLI refuses an existing database,
 * so the backup is restored into a new named volume (`restoreVolume`) and verified as a disabled deployment before cutover.
 */
function RestoreGuide({ staged, run, hasVerified, now }: { staged: StagedBackup | null; run: BackupRun | null; hasVerified: boolean; now: number }) {
  const volume = staged?.restoreVolume ?? "RESTORE_VOLUME";
  return <ol className="steps">
    <li><div><b>Stage the backup</b><p>{!hasVerified ? "No verified backup is available yet." : staged
      ? `Staged at ${staged.stagedPath} and re-verified (schema version ${staged.schemaVersion}, ${bytes(staged.bytes)}).`
      : "Stage for restore copies the verified backup to /online-backups/staged/ and re-checks its SHA-256, schema and integrity."}</p>
      {(staged?.sha256 ?? run?.sha256) && <code tabIndex={0}>sha256 {staged?.sha256 ?? run?.sha256}</code>}</div></li>
    <li><div><b>Stop the service and restore into a new volume</b><p>Run these on the host. The restore refuses an existing database: it verifies the
      checksum and schema, copies the backup into the new volume {staged ? <code className="inline-code">{volume}</code> : "named in the commands"} and
      prints <code className="inline-code">community_restore_complete</code> (with <code className="inline-code">upgrade_required</code> if the backup-first
      upgrade must run in that volume first). The current data volume is left untouched.</p>
      {staged ? staged.commands.map((command, index) => <code key={index} tabIndex={0}>{command}</code>) : <p>The exact commands appear after staging.</p>}</div></li>
    <li><div><b>Start a disabled recovery deployment</b><p>With the protected configuration&apos;s <code className="inline-code">enabled</code> and{" "}
      <code className="inline-code">trustedTunnel</code> set to false, point a Compose override outside the checkout at the new volume and start it;
      readiness must report 503 while disabled.</p>
      <code tabIndex={0}>{`volumes:\n  community-data:\n    external: true\n    name: ${volume}`}</code>
      <code tabIndex={0}>docker compose -f community-service/compose.yaml -f /private/recovery/restore-volume.yaml up -d --no-build community</code></div></li>
    <li><div><b>Verify, then cut over</b><p>Review the restored data (see Isolated snapshot restore in the service documentation) before a separately approved
      activation. Keep both volumes. Uploads made after {run ? dayTime(run.startedAt, now) : "the backup"} must be re-sent by clients.</p></div></li>
  </ol>;
}
