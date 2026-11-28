import { readOperatorHome, type HomePeriod, type Update } from "../../../api/api-community-operator-community";
import { EventRow } from "../activity/event-row";
import { useModeration } from "../moderation-dialog";
import { AttentionRow } from "../queue/queue-entry";
import { ago, itemTitle, versionName } from "../shared/format";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { RevisionCauseText, RevisionChanges } from "../shared/revision";
import { PageHead, PanelHead, Poster, Segmented } from "../shared/ui";
import { itemLink, navigate, operatorLink } from "../routes";
import { homeSummary, HomeKpis } from "./home-kpis";
import { QuickTools, ServiceHealth, TopContributors } from "./home-side";

const periods = [["24h", "24 hours"], ["7d", "7 days"], ["30d", "30 days"]] as const;
const greeting = (hour: number) => hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
const firstName = (subject: string) => { const name = subject.split("@")[0].split(/[._\s-]/)[0]; return name.charAt(0).toUpperCase() + name.slice(1); };

function UpdateRow({ update }: { update: Update }) {
  return <div className="urow">
    {update.item ? <Poster item={update.item} /> : <span className="poster" aria-hidden="true">?</span>}
    <div className="grow"><div className="tt">{update.item ? <a href={itemLink(update.item.workId, update.version?.versionId)}>{itemTitle(update.item)}</a>
      : itemTitle(null)}<span> · {versionName(update.version)}</span></div>
      <div className="what"><span className="chip blue">rev {update.revision}</span><RevisionChanges delta={update} /><RevisionCauseText cause={update.cause} /></div></div>
    <span className="age">{ago(update.at)}</span></div>;
}

/** The operator's landing page: KPIs, decisions waiting, latest master changes, activity and service health. */
export function HomePage({ params }: { params: Record<string, string | null> }) {
  const { subject, onAccessFailure, refreshCounts } = useOperator();
  const period = (periods.some(([value]) => value === params.period) ? params.period : "7d") as HomePeriod;
  const { data, status, refresh } = useOperatorData(`home:${period}`, signal => readOperatorHome(period, signal));
  const moderation = useModeration({ onAccessFailure, onApplied: () => { refresh(); refreshCounts(); } });
  const now = new Date();
  return <section aria-labelledby="page-title">
    <PageHead eyebrow={now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}
      title={`${greeting(now.getHours())}, ${firstName(subject)}`} intro={data ? homeSummary(data) : " "}>
      <Segmented label="Period" value={period} options={periods} onChange={value => navigate(operatorLink("home", { period: value }))} />
    </PageHead>
    <DataState status={status} onRetry={refresh} />
    {moderation.view}
    {data && <>
      <HomeKpis kpis={data.kpis} />
      <div className="home">
        <div className="col">
          <section className="card" aria-labelledby="home-attention">
            <PanelHead title={<span id="home-attention">Needs attention</span>}
              note={`top ${data.attention.length} of ${data.queueCounts.all}`}><a href="#/queue">Open queue →</a></PanelHead>
            {data.attention.length ? data.attention.map(entry => <AttentionRow key={`${entry.kind}:${entry.id}`} entry={entry}
              begin={(...args) => void moderation.begin(...args)} locked={moderation.locked || status !== "fresh"} />) :
              <p className="empty">Nothing needs a decision right now.</p>}
          </section>
          <section className="card" aria-labelledby="home-updates">
            <PanelHead title={<span id="home-updates">Latest updates</span>} note="uploads and master revisions"><a href="#/items">All items →</a></PanelHead>
            {data.latestUpdates.length ? data.latestUpdates.map(update => <UpdateRow key={`${update.version?.versionId}:${update.revision}`} update={update} />) :
              <p className="empty">No master revisions yet.</p>}
          </section>
          <section className="card feed compact" aria-labelledby="home-activity">
            <PanelHead title={<span id="home-activity">Activity</span>}><a href="#/activity">All activity →</a></PanelHead>
            {data.activity.length ? data.activity.map((event, index) => <EventRow key={`${event.at}:${index}`} event={event} />) :
              <p className="empty">No activity in this period.</p>}
          </section>
        </div>
        <div className="col">
          <TopContributors rows={data.topContributors} period={data.period} />
          <ServiceHealth health={data.health} now={data.observedAt} />
          <QuickTools />
        </div>
      </div>
      <p className="foot">Counts come from the community database. Storage and denials are application-observed (ⓘ) and may undercount;
        trends and provenance are on <a href="#/health">Health &amp; tools</a>.</p>
    </>}
  </section>;
}
