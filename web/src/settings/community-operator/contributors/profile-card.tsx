import { readOperatorProfile } from "../../../api/api-community-operator-community";
import { ago, bytes, count, filterSummary, itemTitle, shortDate, titleCase, versionName } from "../shared/format";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { Avatar } from "../shared/ui";
import { TokenActions } from "../token-actions";
import { itemLink, operatorLink } from "../routes";

/** How a vote history event reads, e.g. "withdrew their vote" for a removed vote. */
const voteEvents = { cast: "voted", changed: "changed their vote", removed: "withdrew their vote", excluded: "vote excluded by an operator",
  restored: "vote restored by an operator" } as const;
const target = (entry: { item: { workId: string } | null; version: { versionId: string } | null; filter: { filterId: string } | null }) =>
  entry.item ? itemLink(entry.item.workId, entry.version?.versionId, entry.filter?.filterId) : null;

/** A contributor's identity, totals, recent uploads and votes, reports, installations, usage and account moderation. */
export function ProfileCard({ contributorId }: { contributorId: string }) {
  const { refreshCounts } = useOperator();
  const { data, status, refresh } = useOperatorData(`profile:${contributorId}`, signal => readOperatorProfile(contributorId, signal));
  if (!data) return <aside className="card cdetail" aria-label="Contributor profile"><div className="sec"><DataState status={status} onRetry={refresh} /></div></aside>;
  const { identity: person, totals, usage } = data;
  const requests = Object.values(usage.requests).reduce((sum, row) => sum + (row?.occurrences ?? 0), 0);
  const denials = usage.requests.denied?.occurrences ?? 0;
  return <aside className="card cdetail" aria-labelledby="profile-title">
    <DataState status={status} onRetry={refresh} />
    <div className="top"><Avatar person={person} size="xl" /><div><h2 id="profile-title">{person.displayName}</h2>
      <div className="muted small">{person.joinedAt ? `Joined ${shortDate(person.joinedAt)}` : "Join date unknown"}
        {person.membership.length > 0 && ` · ${person.membership.map(row => `${titleCase(row.authority)} ${row.state}`).join(", ")}`}
        {" · "}<span className={`chip mini ${person.state === "active" ? "ok" : "bad"}`}>{person.state}</span>
        {person.quarantined && <span className="chip mini bad">quarantined</span>}</div></div></div>
    <div className="sec"><div className="cgrid">
      <div><b>{count(totals.uploads)}</b><span>uploads · {count(totals.items)} items</span></div>
      <div><b>{count(totals.accepted)}</b><span>accepted filters</span></div>
      <div><b>{count(totals.votesCast)}</b><span>votes cast{totals.votesUpShare !== null ? ` · ${Math.round(totals.votesUpShare * 100)}% up` : ""}</span></div>
      <div><b>{count(totals.reportsFiled)}</b><span>reports filed</span></div>
      <div><b>{data.reportsAgainst.length}</b><span>recent reports against</span></div>
      <div><b>{data.installations.length}</b><span>installations</span></div></div></div>
    <section className="sec" aria-labelledby="profile-uploads"><h3 id="profile-uploads">Recent uploads</h3><ul className="rows">
      {data.uploads.map(upload => <li className="row" key={upload.submissionId}>
        <span className={`dot ${upload.quarantined ? "bad" : upload.state === "counted" ? "ok" : "dim"}`} aria-hidden="true" />
        <div className="grow">{target(upload) ? <a href={target(upload)!}><b>{itemTitle(upload.item)}</b></a> : <b>{itemTitle(upload.item)}</b>}
          {upload.version && ` · ${versionName(upload.version)}`}<div>{[upload.at ? shortDate(upload.at) : null, `${count(upload.entries)} entries`,
            upload.tier !== null ? `tier ${upload.tier}` : null, upload.quarantined ? "quarantined" : upload.state].filter(Boolean).join(" · ")} ·{" "}
            <a href={operatorLink("inspect", { kind: "submission", selected: upload.submissionId })}>Inspect<span className="sr"> upload {upload.submissionId}</span></a></div></div></li>)}
      {!data.uploads.length && <li className="muted small">No uploads.</li>}</ul></section>
    <section className="sec" aria-labelledby="profile-votes"><h3 id="profile-votes">Recent votes</h3><ul className="rows">
      {data.votes.map(vote => <li className="row" key={vote.eventId}>
        <b className={vote.kind === "removed" ? "faint" : vote.vote === "up" ? "up" : "down"}><span aria-hidden="true">{vote.kind === "removed" ? "–" : vote.vote === "up" ? "▲" : "▼"}</span>
          <span className="sr">{vote.kind === "removed" ? "Withdrawn vote" : vote.vote === "up" ? "Up vote" : "Down vote"}</span></b>
        <div className="grow">{vote.filter ? <a href={operatorLink(`filters/${vote.filter.filterId}`)}>{filterSummary(vote.filter)}</a> : "Unknown filter"}
          <div>{[vote.item ? itemTitle(vote.item) : null, vote.version ? versionName(vote.version) : null, voteEvents[vote.kind],
            vote.at ? ago(vote.at) : null].filter(Boolean).join(" · ")}</div></div></li>)}
      {!data.votes.length && <li className="muted small">No votes.</li>}</ul></section>
    {(data.reportsFiled.length > 0 || data.reportsAgainst.length > 0) && <section className="sec" aria-labelledby="profile-reports">
      <h3 id="profile-reports">Reports</h3><ul className="rows">
        {data.reportsFiled.map(report => <li className="row" key={`f:${report.reportId}`}><span className="chip mini">filed</span>
          <div className="grow">{titleCase(report.reason)} on {target(report) ? <a href={target(report)!}>{report.filter?.category ? filterSummary(report.filter)
            : itemTitle(report.item)}</a> : report.targetType}<div>{report.state}{report.at ? ` · ${ago(report.at)}` : ""}</div></div></li>)}
        {data.reportsAgainst.map(report => <li className="row" key={`a:${report.reportId}`}><span className="chip mini bad">against</span>
          <div className="grow">{titleCase(report.reason)} by {report.reporter?.displayName ?? "unknown"} on {report.filter ? filterSummary(report.filter) : "a filter"}
            <div>{report.state}{report.at ? ` · ${ago(report.at)}` : ""}</div></div></li>)}</ul></section>}
    <section className="sec" aria-labelledby="profile-keys"><h3 id="profile-keys">Installations &amp; keys</h3><ul className="rows">
      {data.installations.map(installation => <li className="keyrow" key={installation.installationId}>
        <span><b className="mono">{installation.installationId.slice(0, 8)}</b><br /><span className="muted">{installation.keys.active} active ·{" "}
          {installation.keys.retired} retired{installation.keys.revoked ? ` · ${installation.keys.revoked} revoked` : ""}
          {installation.newestKey?.createdAt ? ` · newest ${shortDate(installation.newestKey.createdAt)}` : ""}</span></span>
        <span className="faint">{installation.lastSyncAt ? `last sync ${ago(installation.lastSyncAt)}` : "never synced"}</span></li>)}
      {!data.installations.length && <li className="muted small">No installations.</li>}</ul>
      <a className="small" href={operatorLink("inspect", { kind: "contributor", selected: person.contributorId })}>Key history and full inspection →</a></section>
    <section className="sec" aria-labelledby="profile-usage"><h3 id="profile-usage">Usage <small>{usage.period} UTC</small></h3>
      <p className="muted small">{count(requests)} requests · {count(denials)} rate-limit denials · {bytes(usage.storage.bytes)}
        {usage.storage.quotaBytes !== null && ` of ${bytes(usage.storage.quotaBytes)}`} payload
        <abbr className="prov" title="Application-observed counts; not verified against provider billing."> ⓘ</abbr></p>
      <TokenActions token={person.moderation} state={{ state: person.state, quarantined: person.quarantined ? 1 : 0 }} enabled={status === "fresh"}
        onApplied={() => { refresh(); refreshCounts(); }} />
    </section>
  </aside>;
}
