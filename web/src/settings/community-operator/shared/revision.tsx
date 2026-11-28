import type { RevisionCause, RevisionDelta } from "../../../api/api-community-operator-labels";
import { contributorLink } from "../routes";

/** "+3 filters, −1, 2 retimed" coloured by change kind; "no served change" when nothing moved. */
export function RevisionChanges({ delta }: { delta: RevisionDelta }) {
  if ([delta.added, delta.removed, delta.retimed, delta.recategorised].every(value => value === null))
    return <span className="muted">{delta.filters === null ? "changes unavailable" : `${delta.filters} served filters`}</span>;
  const parts = [
    delta.added ? <span key="a" className="add">+{delta.added} {delta.added === 1 ? "filter" : "filters"}</span> : null,
    delta.removed ? <span key="r" className="rm">−{delta.removed}{delta.added ? "" : delta.removed === 1 ? " filter" : " filters"}</span> : null,
    delta.retimed ? <span key="t" className="chg">{delta.retimed} retimed</span> : null,
    delta.recategorised ? <span key="c" className="chg">{delta.recategorised} recategorised</span> : null,
  ].filter(Boolean);
  if (!parts.length) return <span className="muted">no served change</span>;
  return <span>{parts.flatMap((part, index) => index ? [", ", part] : [part])}</span>;
}

/** Why a revision happened: an upload (linked contributor), an operator action or vote movement. */
export function RevisionCauseText({ cause }: { cause: RevisionCause | null }) {
  if (!cause) return <span className="muted">cause unknown</span>;
  if (cause.kind === "upload") return <span>upload by {cause.contributor ? <a href={contributorLink(cause.contributor.contributorId)}>
    {cause.contributor.displayName}</a> : "a contributor"}</span>;
  if (cause.kind === "moderation") return <span>{cause.actor} · {cause.command.replace(/_/g, " ")}</span>;
  return <span>votes changed{cause.contributor ? <> (latest by <a href={contributorLink(cause.contributor.contributorId)}>{cause.contributor.displayName}</a>)</> : ""}</span>;
}
