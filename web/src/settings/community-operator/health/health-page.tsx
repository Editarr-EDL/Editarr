import { readOperatorHealth, readOperatorMaintenanceRuns, trendMetrics, type TrendPeriod } from "../../../api/api-community-operator-system";
import { ago, dayTime, duration, titleCase } from "../shared/format";
import { DataState, useOperatorData } from "../shared/operator-data";
import { PageHead, Segmented } from "../shared/ui";
import { navigate, operatorLink } from "../routes";
import { ServiceRecords } from "./service-records";
import { Tools } from "./tools";
import { TrendCard } from "./trend-card";

const periods = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"]] as const;
const outcomes = { ok: ["ok", "✓ Complete"], batch_limit: ["cand", "Batch limit"], skipped: ["", "Skipped"], failed: ["bad", "✕ Failed"] } as const;

/** Readiness checks, storage and usage trends, retention sweeps and maintenance tools. */
export function HealthPage({ params }: { params: Record<string, string | null> }) {
  const period = (periods.some(([value]) => value === params.period) ? params.period : "30d") as TrendPeriod;
  const health = useOperatorData("health", readOperatorHealth);
  const runs = useOperatorData("runs:retention", signal => readOperatorMaintenanceRuns("retention", null, signal));
  const data = health.data;
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="System" title="Health & tools" intro="Readiness, storage and maintenance over time. Figures marked ¹ are counted by the service and not provider-verified.">
      <Segmented label="Period" value={period} options={periods} onChange={value => navigate(operatorLink("health", { period: value }))} />
    </PageHead>
    <DataState status={health.status} onRetry={health.refresh} />
    {data && <div className={`status-card${data.ready ? "" : " bad"}`}>
      <span className="big" aria-hidden="true">{data.ready ? "✓" : "!"}</span>
      <div><h2>{data.ready ? "Ready · all checks passing" : `Not ready · ${data.checks.filter(check => !check.ok).length} checks failing`}</h2>
        <p>Checked {ago(data.observedAt)}{data.database.pageSize ? ` · page size ${data.database.pageSize} B` : ""}</p>
        <ul className="checks">{data.checks.map(check => <li key={check.id} className={`chip ${check.ok ? "ok" : "bad"}`} title={check.detail}>
          <span aria-hidden="true">{check.ok ? "✓" : "✕"}</span> {check.label}<span className="sr">: {check.ok ? "passing" : "failing"}.</span> {check.detail}</li>)}</ul></div>
      <button type="button" className="btn" onClick={() => { health.refresh(); runs.refresh(); }}>Re-check</button>
    </div>}
    <div className="trends">{trendMetrics.map(metric => <TrendCard key={`${metric}:${period}`} metric={metric} period={period} />)}</div>
    <div className="section-title"><h2>Retention sweeps</h2><span className="muted small">Durable records and payloads are never deleted; each sweep prunes
      bounded batches of expired rows.</span></div>
    <div className="tw" role="region" aria-label="Retention sweeps table" tabIndex={0}><table className="t ret"><caption className="sr">Retention sweeps</caption>
      <thead><tr><th scope="col">Ran</th><th scope="col" className="r">Duration</th><th scope="col" className="r">Steps</th><th scope="col">Result</th></tr></thead>
      <tbody>{runs.data?.items.map(run => { const [tone, label] = outcomes[run.outcome];
        const failed = Array.isArray(run.detail.failedSteps) ? run.detail.failedSteps.length : 0;
        return <tr key={run.runId}><td>{dayTime(run.startedAt)}</td><td className="r num">{duration(run.durationMs)}</td>
          <td className="r num">{typeof run.detail.steps === "number" ? run.detail.steps : "—"}</td>
          <td className="wrap"><span className={`chip ${tone}`}>{label}</span>{failed > 0 && <span className="faint small"> {failed} steps failed</span>}</td></tr>; })}
        {runs.data && !runs.data.items.length && <tr><td colSpan={4} className="muted">No retention sweep recorded yet.</td></tr>}
        {runs.status === "unavailable" && <tr><td colSpan={4} className="muted">Retention history unavailable.</td></tr>}</tbody></table></div>
    <ServiceRecords params={params} />
    <Tools lastRecomputeAll={data?.lastRecomputeAll ?? null} onApplied={health.refresh} />
    <div className="footnotes">¹ Application-observed: counted by the service from <span className="mono">usage_observations</span> and{" "}
      <span className="mono">artifact_storage_usage</span>. Requests rejected before the application (edge, Access) are not included, so these may
      undercount; they are not verified against provider billing.<br />² Sampled hourly from SQLite page count × page size and the WAL file size.
      {data && <> Last retention sweep: {data.lastRetention ? `${titleCase(data.lastRetention.outcome.replace("_", " "))}, ${ago(data.lastRetention.startedAt)}` : "none yet"}.</>}</div>
  </section>;
}
