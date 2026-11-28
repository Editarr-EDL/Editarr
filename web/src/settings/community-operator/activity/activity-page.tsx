import { readOperatorActivity, type ActivityEvent, type ActivityKind } from "../../../api/api-community-operator-community";
import { DataState, useOperatorData } from "../shared/operator-data";
import { PageHead, Segmented } from "../shared/ui";
import { navigate, operatorLink } from "../routes";
import { EventRow } from "./event-row";

const kinds = [["all", "All"], ["uploads", "Uploads"], ["votes", "Votes"], ["reports", "Reports"], ["moderation", "Moderation"]] as const;
const day = 86400000;
/** Group events into Today, Yesterday, This week and Earlier (UTC days). */
export function groupByDay(events: ActivityEvent[], now: number) {
  const today = Math.floor(now / day), groups = new Map<string, ActivityEvent[]>();
  for (const event of events) {
    const days = today - Math.floor(event.at / day);
    const label = days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "This week" : "Earlier";
    groups.set(label, [...groups.get(label) ?? [], event]);
  }
  return [...groups];
}

/** Uploads, votes, reports and moderation across every item, newest first. */
export function ActivityPage({ params }: { params: Record<string, string | null> }) {
  const kind = (kinds.some(([value]) => value === params.kind) ? params.kind : "all") as ActivityKind;
  const before = params.before ?? null;
  const { data, status, refresh } = useOperatorData(`activity:${kind}:${before}`, signal => readOperatorActivity(kind, before, signal));
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="Community" title="Activity" intro="Uploads, votes, reports and moderation across every item.">
      <Segmented label="Activity kind" value={kind} options={kinds} className="actions pills"
        onChange={value => navigate(operatorLink("activity", { kind: value === "all" ? null : value }))} />
    </PageHead>
    <DataState status={status} onRetry={refresh} />
    {data && (data.items.length ? groupByDay(data.items, data.observedAt).map(([label, events]) => <section className="feed-group" key={label}
      aria-label={label}><h2>{label}</h2><div className="card feed">{events.map((event, index) =>
        <EventRow key={`${event.at}:${index}`} event={event} now={data.observedAt} />)}</div></section>) :
      <div className="card empty"><p>No activity of this kind yet.</p></div>)}
    <p className="foot">{before && <a href={operatorLink("activity", { kind: kind === "all" ? null : kind })}>Newest activity</a>}
      {before && data?.nextCursor && " · "}
      {data?.nextCursor && <a href={operatorLink("activity", { kind: kind === "all" ? null : kind, before: data.nextCursor })}>Older activity</a>}</p>
  </section>;
}
