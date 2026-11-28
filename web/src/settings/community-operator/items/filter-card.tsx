import type { FilterContext } from "../../../api/api-community-operator-catalog";
import { useModeration } from "../moderation-dialog";
import { TokenActions } from "../token-actions";
import { categoryClass, dayTime, filterName, filterSpan, shortDate, timecode, titleCase } from "../shared/format";
import { useOperator } from "../shared/operator-data";
import { Avatar } from "../shared/ui";
import { contributorLink, operatorLink } from "../routes";

const attestationOrigin = (entry: FilterContext["attestations"][number]) => [entry.origin ?? "unknown origin",
  entry.manualReview ? "manual review" : null, entry.locallyVerified ? "locally verified" : null,
  entry.modelVersion ? `model ${entry.modelVersion}` : null].filter(Boolean).join(" · ");

/**
 * The selected master filter: timing, vote tally with per-voter exclude/restore, attesting uploads, reports and
 * moderation history with quarantine or restore. Actions use the listed moderation revisions and re-inspect first.
 */
export function FilterCard({ context, fresh, onApplied }: { context: FilterContext; fresh: boolean; onApplied: () => void }) {
  const { onAccessFailure } = useOperator();
  const moderation = useModeration({ onAccessFailure, onApplied });
  const { filter, votes, attestations, reports } = context;
  const counted = votes.filter(vote => !vote.excluded);
  const up = counted.filter(vote => vote.vote === "up").length, down = counted.length - up, excluded = votes.length - counted.length;
  const open = reports.filter(report => report.state === "open");
  const length = filter.startMs !== null && filter.endMs !== null ? ((filter.endMs - filter.startMs) / 1000).toFixed(1) : null;
  const locked = moderation.locked || !fresh;
  return <aside className="card fcard" aria-labelledby="filter-card-title">
    <div className={`top ${categoryClass(filter.category)}`}>
      <p className="eyebrow">Selected filter · {context.versionLabel?.label ?? "unknown version"}</p>
      <h2 id="filter-card-title"><i className={`dot ${categoryClass(filter.category)}`} aria-hidden="true" />{filterName(filter)}</h2>
      <div className="when num">{titleCase(filter.action ?? "filter")} · {filterSpan(filter)}{length && <span className="faint"> ({length} s)</span>}</div>
      <dl className="kv"><div><dt>Severity</dt><dd>{filter.severity ? titleCase(filter.severity) : "—"}</dd></div>
        <div><dt>Confidence</dt><dd className="num">{filter.confidence === null ? "—" : filter.confidence.toFixed(2)}</dd></div>
        {filter.spread && !filter.quarantined ? <div><dt>Spread</dt><dd className="num">±{(Math.max(Math.abs(filter.spread.startMs), Math.abs(filter.spread.endMs)) / 1000).toFixed(1)} s</dd></div>
          : <div><dt>State</dt><dd>{filter.quarantined ? "Quarantined" : filter.served ? "Served" : "Not served"}</dd></div>}</dl>
    </div>
    {moderation.view}
    <section className="sec" aria-labelledby="filter-votes"><h3 id="filter-votes">Votes <small>from download receipts</small></h3>
      <div className="tally"><div className="up"><b>{up}</b>up</div><div className="dn"><b>{down}</b>down{excluded ? <span className="faint"> +{excluded} excluded</span> : null}</div>
        <div className="di"><b>{filter.disputes ?? 0}</b>{filter.disputes === 1 ? "dispute" : "disputes"}</div></div>
      {votes.length ? <ul className="voters">{votes.map(vote => {
        const command = vote.excluded ? "restore_vote" : "exclude_vote";
        return <li key={vote.contributor.contributorId}><button type="button" className={`voter ${vote.vote === "up" ? "up" : "dn"}${vote.excluded ? " ex" : ""}`}
          disabled={locked} title={`${vote.contributor.displayName}${vote.at ? ` · ${shortDate(vote.at)}` : ""}${vote.revision !== null ? ` · rev ${vote.revision}` : ""}${vote.excluded ? " · excluded" : ""}`}
          aria-label={`${vote.excluded ? "Restore" : "Exclude"} ${vote.contributor.displayName}'s ${vote.vote} vote`}
          onClick={() => void moderation.begin({ kind: "vote", targetId: filter.filterId, contributorId: vote.contributor.contributorId }, command,
            { revision: vote.moderation.revision })}>
          <Avatar person={vote.contributor} /><i aria-hidden="true">{vote.vote === "up" ? "▲" : "▼"}</i>{vote.contributor.displayName.split(" ")[0]}</button></li>;
      })}</ul> : <p className="muted small">No votes yet.</p>}
      {votes.length > 0 && <p className="faint small">Select a voter to exclude or restore their vote.</p>}
    </section>
    <section className="sec" aria-labelledby="filter-attest"><h3 id="filter-attest">Attesting uploads
      <small>{attestations.filter(entry => entry.counted).length} of {attestations.length} counted</small></h3>
      <ul className="rows">{attestations.map(entry => <li className="row" key={`${entry.submissionId}:${entry.entryId ?? ""}`}>
        <Avatar person={entry.contributor} /><div className="grow"><a className="actor" href={contributorLink(entry.contributor.contributorId)}>
          {entry.contributor.displayName}</a> · {attestationOrigin(entry)}
          {entry.kind !== "support" && !entry.implicit && <span className="warn-text"> · {entry.kind.replace(/_/g, " ")}</span>}
          {!entry.counted && <span className="warn-text"> · not counted</span>}{entry.quarantined && <span className="chip bad mini">quarantined</span>}
          <div>{[entry.at ? shortDate(entry.at) : null, entry.tier !== null ? `tier ${entry.tier}` : null,
            entry.implicit ? `implicit ${entry.kind === "coverage_negative" ? "coverage negative" : "support"}`
              : `${timecode(entry.startMs)}–${timecode(entry.endMs)}`].filter(Boolean).join(" · ")}
            {/* Implicit attestations have no submission entry to inspect. */}
            {!entry.implicit && <>{" · "}<a href={operatorLink("inspect", { kind: "attestation", selected: `${entry.submissionId}:${entry.entryId}` })}>
              Inspect<span className="sr"> {entry.contributor.displayName}'s attestation</span></a></>}</div></div></li>)}
        {!attestations.length && <li className="muted small">No attesting uploads.</li>}</ul>
    </section>
    <section className="sec" aria-labelledby="filter-reports"><h3 id="filter-reports">Reports <small>{open.length} open</small></h3>
      {reports.length ? <div className="rows">{reports.map(report => <div className={report.state === "open" ? "report" : "report done"} key={report.reportId}>
        <div className="row"><Avatar person={report.contributor} /><div className="grow"><b>{report.contributor.displayName}</b> · {titleCase(report.reason)}
          {report.state === "resolved" && <span className="chip ok mini">resolved</span>}{report.at && <div>{dayTime(report.at)}</div>}</div></div>
        {report.note && <q>{report.note}</q>}
        {report.state === "open" && <div className="actions"><button className="btn sm primary" disabled={locked}
          onClick={() => void moderation.begin({ kind: "report", targetId: report.reportId }, "resolve_report", { revision: report.moderation.revision })}>
          Resolve report</button></div>}
      </div>)}</div> : <p className="muted small">No reports.</p>}
    </section>
    <section className="sec" aria-labelledby="filter-moderation"><h3 id="filter-moderation">Moderation</h3>
      <ul className="rows">{context.moderation.map(action => <li className="row" key={action.actionId}><span className="me sm" aria-hidden="true">
        {action.actor.slice(0, 1).toUpperCase()}</span><div className="grow"><b>{action.actor}</b> {action.command.replace(/_/g, " ")}{action.voter && ` ${action.voter.displayName}'s vote`}
        {action.outcome !== "applied" && <span className="chip bad mini">{action.outcome}</span>}<div>{shortDate(action.at)} · “{action.reason}”</div></div></li>)}
        {!context.moderation.length && <li className="muted small">No moderation on this filter.</li>}</ul>
      <TokenActions token={filter.moderation} state={{ quarantined: filter.quarantined ? 1 : 0 }} enabled={fresh} onApplied={onApplied}
        extra={<button type="button" className="btn sm" onClick={() => void navigator.clipboard?.writeText(filter.filterId)}>Copy filter ID</button>} />
    </section>
  </aside>;
}
