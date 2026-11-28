import type { OperatorHome } from "../../../api/api-community-operator-community";
import { age, ago, bytes, count, delta, plural, until } from "../shared/format";
import { Sparkline } from "../shared/charts";

function Delta({ value, previous, absolute }: { value: number; previous: number | null; absolute?: boolean }) {
  const change = delta(value, previous, absolute);
  return change ? <span className={change.down ? "down" : "up"}>{change.text}</span> : null;
}

/** The five headline numbers for the selected period, each linking to its detail page. */
export function HomeKpis({ kpis }: { kpis: OperatorHome["kpis"] }) {
  const { activeContributors: active, uploads, votes, openReports, lastBackup } = kpis;
  const backupOk = lastBackup?.status === "verified";
  return <div className="kpis">
    <a className="kpi" href="#/contributors"><span className="l">Active contributors</span><span className="v">{count(active.value)}</span>
      <span className="d"><Delta value={active.value} previous={active.previous} absolute /> vs prior period</span>
      <Sparkline values={active.series.map(point => point.v)} /></a>
    <a className="kpi" href="#/activity?kind=uploads"><span className="l">Uploads</span><span className="v">{count(uploads.value)}</span>
      <span className="d"><Delta value={uploads.value} previous={uploads.previous} /> · {plural(uploads.items, "item")}</span>
      <Sparkline values={uploads.series.map(point => point.v)} /></a>
    <a className="kpi" href="#/activity?kind=votes"><span className="l">Votes</span><span className="v">{count(votes.total.value)}</span>
      <span className="d"><Delta value={votes.total.value} previous={votes.total.previous} />
        {votes.upShare !== null && ` · ${Math.round(votes.upShare * 100)}% up`}</span>
      <Sparkline values={votes.total.series.map(point => point.v)} /></a>
    <a className={`kpi${openReports.count ? " alert" : ""}`} href="#/queue/reports"><span className="l">Open reports</span>
      <span className="v">{count(openReports.count)}</span>
      <span className="d">{openReports.oldestAt ? `oldest ${age(openReports.oldestAt)}` : "none waiting"}
        {openReports.versionLevel > 0 && ` · ${openReports.versionLevel} version-level`}</span></a>
    <a className={`kpi${lastBackup?.status === "failed" ? " alert" : ""}`} href="#/backups"><span className="l">Last backup</span>
      <span className={`v ${backupOk ? "ok" : "warn"}`}>{!lastBackup ? "Off" : backupOk ? "✓ Verified" : lastBackup.status === "running" ? "Running"
        : lastBackup.status === "failed" ? "✕ Failed" : lastBackup.status === "pruned" ? "Pruned" : "Not run yet"}</span>
      <span className="d">{lastBackup ? [lastBackup.at ? ago(lastBackup.at) : null, lastBackup.bytes !== null ? bytes(lastBackup.bytes) : null,
        lastBackup.nextAt ? `next ${until(lastBackup.nextAt)}` : null].filter(Boolean).join(" · ") || "Scheduled" : "Scheduled backups are off"}</span></a>
  </div>;
}

/** One-sentence state of the service for the greeting. */
export function homeSummary(data: OperatorHome) {
  const decisions = data.queueCounts.all, oldest = data.queueCounts.oldestAt;
  const backup = data.kpis.lastBackup;
  return [decisions ? `${plural(decisions, "decision is", "decisions are")} waiting${oldest ? `, the oldest for ${age(oldest)}` : ""}.`
    : "No decisions are waiting.",
  data.health.ready ? "The service is healthy" : "The service is not ready",
  backup?.status === "verified" && backup.at ? `and the last backup verified ${ago(backup.at)}.` : backup?.status === "failed" ? "and the last backup failed."
    : "and no backup has run yet."].join(" ");
}
