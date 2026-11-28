import { readOperatorQueue, queueKinds, type QueueKind } from "../../../api/api-community-operator-community";
import { useModeration } from "../moderation-dialog";
import { age, ago, plural } from "../shared/format";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { PageHead } from "../shared/ui";
import { operatorLink } from "../routes";
import { QueueCard } from "./queue-entry";
import { queueActions, queueLabels, queueTitle } from "./queue-model";

const pills: QueueKind[] = ["all", "reports", "aliases", "versions", "quarantined", "disputed"];

/** Open moderation decisions, newest first, filtered by kind; every action asks for a reason and is audited. */
export function QueuePage({ kind: requested, params }: { kind: string; params: Record<string, string | null> }) {
  const kind: QueueKind = queueKinds.includes(requested as QueueKind) ? requested as QueueKind : "all";
  const { onAccessFailure, refreshCounts } = useOperator();
  const after = params.after ?? null;
  const page = useOperatorData(`queue:${kind}:${after}`, signal => readOperatorQueue(kind, after, signal));
  const quarantined = useOperatorData("queue:quarantined:side", signal => readOperatorQueue("quarantined", null, signal), kind !== "quarantined");
  const applied = () => { page.refresh(); quarantined.refresh(); refreshCounts(); };
  const moderation = useModeration({ onAccessFailure, onApplied: applied });
  const begin = (...args: Parameters<typeof moderation.begin>) => void moderation.begin(...args);
  const data = page.data, counts = data?.counts;
  const oldest = data?.oldestAt ?? null;
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="Moderate" title={queueLabels[kind]} intro="Newest first. Every action asks for a reason and is written to the audit log.">
      <nav className="actions" aria-label="Queue filter">{pills.filter(pill => pill !== "disputed" || (counts?.disputed ?? 0) > 0 || kind === "disputed")
        .map(pill => <a key={pill} className="pill-filter" href={pill === "all" ? "#/queue" : `#/queue/${pill}`}
          aria-current={pill === kind ? "page" : undefined}>{pill === "all" ? "All" : queueLabels[pill]}
          {counts && <span className="n">{counts[pill]}</span>}</a>)}</nav>
    </PageHead>
    <DataState status={page.status} onRetry={page.refresh} />
    {moderation.view}
    <div className="qcols">
      <div className="queue" aria-busy={page.status === "loading"}>
        {data && (data.items.length ? data.items.map(entry => <QueueCard key={`${entry.kind}:${entry.id}`} entry={entry} begin={begin}
          locked={moderation.locked || page.status !== "fresh"} />) :
          <div className="card empty"><p>Nothing waiting. New reports, alias suggestions and candidate versions appear here.</p></div>)}
        <p className="foot">{data ? `Showing ${plural(data.items.length, kind === "all" ? "open decision" : "entry", kind === "all" ? "open decisions" : "entries")}` : ""}
          {after && <> · <a href={operatorLink(kind === "all" ? "queue" : `queue/${kind}`)}>First page</a></>}
          {data?.nextCursor && <> · <a href={operatorLink(kind === "all" ? "queue" : `queue/${kind}`, { after: data.nextCursor })}>Next page</a></>}</p>
      </div>
      <aside className="card side-card" aria-label="Queue summary">
        <div className="sec"><h2 className="h3">Queue health</h2><div className="cgrid">
          <div><b>{counts?.all ?? "—"}</b><span>open decisions</span></div>
          <div><b>{oldest ? age(oldest) : "—"}</b><span>oldest waiting</span></div>
          <div><b>{counts?.reports ?? "—"}</b><span>open reports</span></div>
          <div><b>{counts?.disputed ?? "—"}</b><span>disputed filters</span></div></div></div>
        {kind !== "quarantined" && <div className="sec"><h2 className="h3">Currently quarantined <small>{counts?.quarantined ?? ""}</small></h2>
          <div className="rows">{quarantined.data?.items.slice(0, 5).map(entry => { const action = queueActions(entry)[0];
            return <div className="row" key={`${entry.kind}:${entry.id}`}><span className="dot cat-other" aria-hidden="true" />
              <div className="grow"><b>{queueTitle(entry).replace(/^[^·]+· /, "")}</b><div>{entry.item ? `${entry.item.title} · ` : ""}{ago(entry.at)}</div></div>
              {action && <button className="btn sm" disabled={moderation.locked} aria-label={`Restore ${queueTitle(entry)}`}
                onClick={() => begin(action.target, action.command, { revision: action.revision })}>Restore</button>}</div>; })}
            {quarantined.data && !quarantined.data.items.length && <p className="muted">Nothing is quarantined.</p>}</div></div>}
        <div className="sec"><a href="#/audit">Full audit log →</a></div>
      </aside>
    </div>
  </section>;
}
