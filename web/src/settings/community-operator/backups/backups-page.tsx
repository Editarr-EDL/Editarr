import { useEffect } from "react";
import { readOperatorBackups, writeOperatorBackupRun } from "../../../api/api-community-operator-system";
import { ago, bytes, until } from "../shared/format";
import { DataState, useOperatorData } from "../shared/operator-data";
import { PageHead } from "../shared/ui";
import { mutationMessage, useMutation } from "./backup-mutation";
import { RestoreSteps, RunsTable } from "./backup-runs";
import { ScheduleForm } from "./schedule-form";

const schedule = { hourly: "Hourly", every_6h: "Every 6 hours", daily: "Daily", weekly: "Weekly" } as const;

/** Online backups: status, manual run, schedule configuration, recent runs and staged offline restore. */
export function BackupsPage() {
  const { data, status, refresh } = useOperatorData("backups", readOperatorBackups);
  const mutation = useMutation();
  // Poll while a run is in progress; re-arms after every settled read (fresh or stale) and never overlaps a pending one.
  useEffect(() => {
    if (!data?.running || status === "loading") return;
    const timer = window.setTimeout(refresh, 3000);
    return () => window.clearTimeout(timer);
  }, [data, status]);
  const last = data?.runs.find(run => run.status !== "running") ?? null;
  const kept = data?.runs.filter(run => run.status === "verified") ?? [];
  const runNow = () => mutation.run(writeOperatorBackupRun, () => { refresh(); return "Backup started. This page refreshes until it finishes."; });
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="System" title="Backups" intro="Scheduled online snapshots of the community database, verified after each run.">
      {last && <span className={`chip big ${last.status === "verified" ? "ok" : "bad"}`}>{last.status === "verified" ? "✓ Last backup verified" : "✕ Last backup failed"}</span>}
    </PageHead>
    <DataState status={status} onRetry={refresh} />
    {data && <>
      <div className="bk-top">
        <div className="stat"><span>Last backup</span><b>{last ? ago(last.startedAt, data.observedAt) : "None yet"}</b>
          <span className={last?.status === "verified" ? "up" : last ? "down" : undefined}>{!last ? "Run one to start" : last.status === "verified"
            ? last.integrity === "ok" ? "✓ integrity check passed" : "✓ verified" : `✕ ${mutationMessage(last.errorCode ?? "failed")}`}</span></div>
        <div className="stat"><span>Next scheduled</span><b>{data.next ? until(data.next.at, data.observedAt) : "Off"}</b>
          <span>{data.config.enabled ? `${schedule[data.config.frequency]}${data.config.frequency === "hourly" ? "" : ` at ${data.config.atUtc} UTC`}` : "Schedule disabled"}</span></div>
        <div className="stat"><span>Kept</span><b>{kept.length} verified</b>
          <span>{bytes(kept.reduce((sum, run) => sum + (run.bytes ?? 0), 0))} in the last {data.runs.length} runs</span></div>
        <div className="stat run-now"><button type="button" className="btn primary" disabled={!data.destination.root || data.running || mutation.busy}
          onClick={runNow}>{data.running ? "Backup running…" : "Run backup now"}</button>
          <span className="faint small">{data.destination.root ? "Online snapshot; service stays up" : "Set COMMUNITY_BACKUP_ROOT to enable"}</span></div>
      </div>
      {mutation.message && <p role={mutation.failed ? "alert" : "status"} className={`banner ${mutation.failed ? "bad" : ""}`}>{mutation.message}</p>}
      <div className="bk-cols">
        <ScheduleForm data={data} onSaved={refresh} />
        <div><RunsTable runs={data.runs} now={data.observedAt} />
          <p className="foot">Showing the {data.runs.length} most recent runs. Downloads are the verified SQLite file.</p></div>
      </div>
      <RestoreSteps key={kept[0]?.runId ?? "none"} runs={data.runs} now={data.observedAt} />
    </>}
  </section>;
}
