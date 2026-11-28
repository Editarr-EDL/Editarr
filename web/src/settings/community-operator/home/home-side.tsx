import type { LeaderRow, OperatorHome } from "../../../api/api-community-operator-community";
import { readOperatorTrend, type Trend } from "../../../api/api-community-operator-system";
import { ago, bytes, count, plural, titleCase } from "../shared/format";
import { optionalRead, useOperatorData } from "../shared/operator-data";
import { Meter, Sparkline } from "../shared/charts";
import { Avatar, PanelHead } from "../shared/ui";
import { contributorLink } from "../routes";

/** Top five contributors by accepted filters still served, with proportional bars. */
export function TopContributors({ rows, period }: { rows: LeaderRow[]; period: string }) {
  const top = Math.max(1, ...rows.map(row => row.accepted));
  return <section className="card" aria-labelledby="home-top">
    <PanelHead title={<span id="home-top">Top contributors</span>} note={period.replace("h", " h").replace("d", " d")}><a href="#/contributors">Leaderboard →</a></PanelHead>
    {rows.length ? <ol className="mlb">{rows.map(row => <li key={row.contributor.contributorId}><a href={contributorLink(row.contributor.contributorId)}>
      <span className="rk">{row.rank}</span><Avatar person={row.contributor} /><span className="nm">{row.contributor.displayName}</span>
      <Meter value={row.accepted / top} className="bar" /><span className="val">{count(row.accepted)}</span></a></li>)}</ol> :
      <p className="empty">No contributions in this period.</p>}
  </section>;
}

/** Readiness, database, payload storage and denials (7-day sparklines) and the last retention sweep. */
export function ServiceHealth({ health, now }: { health: OperatorHome["health"]; now: number }) {
  const { database, storage, lastRetention } = health;
  const trends = useOperatorData("home:trends", signal => Promise.all((["db", "storage", "denials"] as const)
    .map(metric => optionalRead(readOperatorTrend(metric, "7d", signal)))));
  const [db, store, denied] = trends.data ?? [null, null, null];
  const used = storage.quotaBytes ? storage.payloadBytes / storage.quotaBytes : null;
  const failing = health.checks.filter(check => !check.ok);
  const values = (trend: Trend | null) => (trend?.points ?? []).map(point => point.v);
  return <section className="card" aria-labelledby="home-health">
    <PanelHead title={<span id="home-health">Service health</span>}><a href="#/health">Details →</a></PanelHead>
    <div className="hrow"><div><div className="k">Readiness</div><div className={`v ${health.ready ? "up" : "warn-text"}`}>
      {health.ready ? "Ready · all checks pass" : `Not ready · ${plural(failing.length, "check")} failing`}</div>
      {failing.length > 0 && <div className="k">{failing.map(check => check.label).join(", ")}</div>}</div><span /><span className="age" /></div>
    <div className="hrow"><div><div className="k">Database + WAL</div><div className="v">{bytes(database.bytes)}
      {database.walBytes !== null && <small> + {bytes(database.walBytes)} WAL</small>}</div></div>
      <Sparkline values={values(db)} tone="teal" /><span className="age">7 d</span></div>
    <div className="hrow"><div><div className="k">Upload storage <abbr className="prov" title="Counted by the service (artifact_storage_usage); not verified by the storage provider.">ⓘ</abbr></div>
      <div className="v">{bytes(storage.payloadBytes)} {storage.quotaBytes !== null && <small>of {bytes(storage.quotaBytes)}</small>}</div>
      {used !== null && <Meter value={used} tone="teal" />}</div>
      <Sparkline values={values(store)} tone="teal" /><span className="age">{used !== null ? `${(used * 100).toFixed(used < 0.1 ? 1 : 0)}%` : ""}</span></div>
    <div className="hrow"><div><div className="k">Rate-limit denials <abbr className="prov" title="Application-observed (usage_observations); partial, not provider-verified.">ⓘ</abbr></div>
      <div className="v">{denied ? count(denied.total ?? 0) : "—"} <small>in 7 days</small></div></div>
      <Sparkline values={values(denied)} tone="warn" /><span className="age" /></div>
    <div className="hrow"><div><div className="k">Retention sweep</div><div className="v">{lastRetention ?
      (lastRetention.outcome === "ok" ? "OK" : titleCase(lastRetention.outcome.replace("_", " "))) : "Not run yet"}</div></div>
      <span /><span className="age">{lastRetention ? ago(lastRetention.startedAt, now) : ""}</span></div>
  </section>;
}

export function QuickTools() {
  return <section className="card" aria-labelledby="home-tools">
    <PanelHead title={<span id="home-tools">Quick tools</span>} />
    <div className="qtools"><a className="btn" href="#/inspect">Look up an ID</a><a className="btn" href="#/health">Recompute a version</a>
      <a className="btn" href="#/backups">Run backup now</a></div>
  </section>;
}
