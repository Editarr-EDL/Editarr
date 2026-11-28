import { useState } from "react";
import { readOperatorAudit, type AuditRow } from "../../../api/api-community-operator-community";
import { dayTime } from "../shared/format";
import { DataState, useOperatorData } from "../shared/operator-data";
import { PageHead, Segmented } from "../shared/ui";
import { itemLink, operatorLink } from "../routes";

const filters = [["all", "All"], ["quarantine", "Quarantine"], ["versions", "Versions"], ["aliases", "Aliases"], ["votes", "Votes"], ["failed", "Failed"]] as const;
type AuditFilter = typeof filters[number][0];
const matches: Record<AuditFilter, (row: AuditRow) => boolean> = {
  all: () => true, quarantine: row => row.command === "quarantine" || row.command === "restore",
  versions: row => row.command.endsWith("_version") || row.command.startsWith("recompute"), aliases: row => row.command.endsWith("_alias"),
  votes: row => row.command.endsWith("_vote"), failed: row => row.outcome !== "applied" };
const verbs: Record<string, string> = { quarantine: "quarantined", restore: "restored", suspend: "suspended", unsuspend: "restored access for",
  exclude_vote: "excluded the vote", restore_vote: "restored the vote", resolve_report: "resolved", confirm_version: "confirmed",
  merge_version: "merged", approve_alias: "approved", reject_alias: "rejected" };

/** Every operator action, immutable; conflicts and denied attempts are kept too. */
export function AuditPage({ params }: { params: Record<string, string | null> }) {
  const after = params.after ?? null;
  const [filter, setFilter] = useState<AuditFilter>("all");
  const { data, status, refresh } = useOperatorData(`audit:${after}`, signal => readOperatorAudit(after, signal));
  const rows = data?.items.filter(matches[filter]) ?? [];
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="System" title="Audit log" intro="Every operator action, immutable. Conflicts and denied attempts are kept too.">
      <Segmented label="Audit filter" value={filter} options={filters} onChange={setFilter} className="actions pills" />
    </PageHead>
    <DataState status={status} onRetry={refresh} />
    {data && <div className="card"><ol className="audit-list">{rows.map(row => <li className="audit-row" key={row.actionId}>
      <span className={`me sm${row.outcome === "applied" ? "" : " dim"}`} aria-hidden="true">{row.actor.slice(0, 1).toUpperCase()}</span>
      <div><b>{row.actor}</b> {row.outcome === "applied" ? verbs[row.command] ?? row.command.replace(/_/g, " ") : `tried to ${row.command.replace(/_/g, " ")}`}{" "}
        {row.resourceLabel.item ? <a href={itemLink(row.resourceLabel.item.workId, row.resourceLabel.version?.versionId, row.resourceLabel.filter?.filterId)}>
          {row.resourceLabel.text}</a> : <b>{row.resourceLabel.text}</b>}{row.outcome !== "applied" && <span className="chip bad mini">{row.outcome}</span>}
        <div className="reason">“{row.reason}”{row.errorCode ? ` · ${row.errorCode}` : ""} · expected revision {row.expectedRevision}
          <span className="faint mono"> · {row.resource} · {row.actionId}</span></div></div>
      <span className="faint">{dayTime(row.createdAt, data.observedAt)}</span></li>)}</ol>
      {!rows.length && <p className="empty">No audit entries{filter === "all" ? "" : " match this filter on this page"}.</p>}</div>}
    <p className="foot">{after && <a href={operatorLink("audit")}>Newest entries</a>}{after && data?.nextCursor && " · "}
      {data?.nextCursor && <a href={operatorLink("audit", { after: data.nextCursor })}>Older entries</a>}</p>
  </section>;
}
