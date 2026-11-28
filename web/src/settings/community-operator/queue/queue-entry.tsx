import type { QueueItem } from "../../../api/api-community-operator-community";
import type { ModerationStart, ModerationTarget } from "../moderation-dialog";
import type { OperatorActionKind } from "../moderation-commands";
import { age, ago, itemTitle } from "../shared/format";
import { itemLink } from "../routes";
import { queueActions, queueContext, queueIcon, queueImpact, queueLink, queueTitle } from "./queue-model";

export type Begin = (target: ModerationTarget, command: OperatorActionKind, start?: ModerationStart) => void;

function Actions({ entry, begin, locked, limit }: { entry: QueueItem; begin: Begin; locked: boolean; limit?: number }) {
  const actions = queueActions(entry).slice(0, limit), href = queueLink(entry);
  return <>{actions.map(action => <button key={action.label} type="button" disabled={locked}
    className={`btn sm${action.tone ? ` ${action.tone}` : ""}`} aria-label={`${action.label.replace("…", "")}: ${queueTitle(entry)}`}
    onClick={() => begin(action.target, action.command, { revision: action.revision, extras: action.extras })}>{action.label}</button>)}
    {href && <a className={`btn sm${limit ? "" : " ghost"}`} href={href}>{entry.filter ? "Open filter" : "Review"}<span className="sr">: {queueTitle(entry)}</span></a>}</>;
}

/** A full queue card with quote, impact and actions. */
export function QueueCard({ entry, begin, locked }: { entry: QueueItem; begin: Begin; locked: boolean }) {
  const [icon, tone] = queueIcon(entry);
  return <article className="qi" aria-labelledby={`queue-${entry.kind}-${entry.id}`}>
    <span className={`kind ic ${tone}`} aria-hidden="true">{icon}</span>
    <div className="qi-main">
      <h3 id={`queue-${entry.kind}-${entry.id}`}>{queueTitle(entry)}</h3>
      <div className="ctx">{entry.item && <a href={itemLink(entry.item.workId, entry.version?.versionId)}>{itemTitle(entry.item)}</a>}
        {entry.item && queueContext(entry) ? " · " : ""}{queueContext(entry)}</div>
      {entry.quote && <div className="body report"><q>{entry.quote}</q></div>}
      {entry.suggestion && <div className="alias-map"><span className="from">“{entry.suggestion.text}”</span><span className="muted" aria-hidden="true">→</span>
        <span className="chip blue">{entry.suggestion.normalized} (new alias)</span>
        <span className="faint">Approve to create the alias, or enter an existing alias ID in the dialog</span></div>}
      {queueImpact(entry) && <div className="ctx small">{queueImpact(entry)}</div>}
      {entry.latestAction && <div className="ctx small">“{entry.latestAction.reason}” · {ago(entry.latestAction.at)}</div>}
    </div>
    <div className="side"><span className="age">{ago(entry.at)}</span><Actions entry={entry} begin={begin} locked={locked} /></div>
  </article>;
}

/** A compact home-page "Needs attention" row with the first two actions. */
export function AttentionRow({ entry, begin, locked }: { entry: QueueItem; begin: Begin; locked: boolean }) {
  const [icon, tone] = queueIcon(entry);
  const context = [entry.item ? itemTitle(entry.item) : null, queueContext(entry),
    entry.quote ? `“${entry.quote}”` : queueImpact(entry)].filter(Boolean).join(" · ");
  return <div className="arow">
    <span className={`ic ${tone}`} aria-hidden="true">{icon}</span>
    <div className="grow"><div className="tt">{queueTitle(entry)}</div><div className="sb">{context}</div></div>
    <div className="acts"><span className="age">{age(entry.at)}</span><Actions entry={entry} begin={begin} locked={locked} limit={1} /></div>
  </div>;
}
