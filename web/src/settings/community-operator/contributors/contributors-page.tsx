import { readOperatorLeaderboard, type LeaderMetric, type LeaderPeriod, type LeaderRow } from "../../../api/api-community-operator-community";
import { count } from "../shared/format";
import { Meter } from "../shared/charts";
import { DataState, useOperatorData } from "../shared/operator-data";
import { Avatar, PageHead, Segmented } from "../shared/ui";
import { navigate, operatorLink } from "../routes";
import { AllContributors } from "./all-contributors";
import { ProfileCard } from "./profile-card";

const periods = [["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]] as const;
const metrics = [["uploads", "Uploads"], ["accepted", "Accepted filters"], ["votes_cast", "Votes cast"], ["votes_received", "Votes received"]] as const;
const value = (row: LeaderRow, metric: LeaderMetric) =>
  ({ accepted: row.accepted, uploads: row.uploads, votes_cast: row.votesCast, votes_received: row.votesReceived })[metric];

/** Contributor leaderboard (ranked by accepted filters still served by default) with a profile of the selected contributor. */
export function ContributorsPage({ params }: { params: Record<string, string | null> }) {
  const period = (periods.some(([option]) => option === params.period) ? params.period : "30d") as LeaderPeriod;
  const metric = (metrics.some(([option]) => option === params.metric) ? params.metric : "accepted") as LeaderMetric;
  const all = params.tab === "all";
  const { data, status, refresh } = useOperatorData(`leaders:${period}:${metric}`, signal => readOperatorLeaderboard(period, metric, signal), !all);
  const go = (values: Record<string, string>) => navigate(operatorLink("contributors", { period, metric, selected: params.selected, ...values }));
  const rows = data?.items ?? [];
  const selected = params.selected ?? (all ? null : rows[0]?.contributor.contributorId ?? null);
  const top = Math.max(1, ...rows.map(row => value(row, metric)));
  const metricLabel = metrics.find(([option]) => option === metric)![1];
  const link = (id: string) => operatorLink("contributors", { period, metric, selected: id });
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="Community" title="Contributors" intro="Who builds the master sets, ranked by filters still served from their uploads.">
      {!all && <Segmented label="Period" value={period} options={periods} onChange={option => go({ period: option })} />}
    </PageHead>
    <nav className="subnav" aria-label="Contributor views">
      <a href={operatorLink("contributors", { period, metric })} aria-current={all ? undefined : "page"}>Leaderboard</a>
      <a href={operatorLink("contributors", { tab: "all" })} aria-current={all ? "page" : undefined}>All contributors</a></nav>
    <div className="qcols ccols">
      {all ? <AllContributors params={params} /> : <div>
        <div className="toolbar"><span className="lbl" aria-hidden="true">Rank by</span>
          <Segmented label="Rank by" value={metric} options={metrics} onChange={option => go({ metric: option })} /></div>
        <DataState status={status} onRetry={refresh} />
        {rows.length >= 3 && <ol className="podium" aria-label="Top three">{[rows[1], rows[0], rows[2]].map(row => <li key={row.contributor.contributorId}
          className={`pod${row === rows[0] ? " first" : ""}`}><span className="place">{["1st", "2nd", "3rd"][row.rank - 1] ?? `#${row.rank}`}</span>
          <Avatar person={row.contributor} size={row === rows[0] ? "xl" : "lg"} /><a href={link(row.contributor.contributorId)}><b>{row.contributor.displayName}</b></a>
          <span className="big num">{count(value(row, metric))}</span><span className="muted small">{metricLabel.toLowerCase()}</span>
          <span className="mini"><span>{count(row.uploads)} uploads</span><span>{count(row.votesReceived)} votes rec.</span></span></li>)}</ol>}
        {data && <div className="tw" role="region" aria-label="Leaderboard table" tabIndex={0}><table className="t lb"><caption className="sr">Leaderboard by {metricLabel}</caption>
          <thead><tr><th scope="col">#</th><th scope="col">Contributor</th>{metrics.map(([option, label]) => <th scope="col" key={option} className="r"
            aria-sort={option === metric ? "descending" : undefined}><span className={option === metric ? "sorted" : undefined}>{label}{option === metric ? " ▾" : ""}</span></th>)}
            <th scope="col" className="r">Reports</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.contributor.contributorId} className={row.contributor.contributorId === selected ? "sel" : undefined}>
            <td className="rank">{row.rank}</td>
            <td><span className="who"><Avatar person={row.contributor} /><a href={link(row.contributor.contributorId)}
              aria-current={row.contributor.contributorId === selected ? "true" : undefined}><b>{row.contributor.displayName}</b></a></span></td>
            {metrics.map(([option]) => <td key={option} className="r num">{option === metric ? <span className="barcell">{count(value(row, option))}
              <Meter value={value(row, option) / top} className="minibar" /></span> : count(value(row, option))}</td>)}
            <td className="r num">{row.reportsAgainst || <span className="rep0">0</span>}</td></tr>)}
            {!rows.length && <tr><td colSpan={7} className="muted">No contributions in this period.</td></tr>}</tbody></table></div>}
        <p className="foot">Accepted filters are master filters this contributor attests to that are still served at the current revision.
          Uploads and net votes received are secondary measures.</p>
      </div>}
      {selected ? <ProfileCard key={selected} contributorId={selected} /> : <aside className="card cdetail empty-card"><p className="muted">Select a contributor.</p></aside>}
    </div>
  </section>;
}
