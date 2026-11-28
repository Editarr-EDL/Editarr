import type { ActivityEvent } from "../../../api/api-community-operator-community";
import { ago, filterSummary, itemTitle, titleCase, versionName } from "../shared/format";
import { contributorLink, itemLink } from "../routes";

const icons = { uploads: ["U", "up"], votes: ["▲", "vo"], reports: ["!", "re"], moderation: ["✓", "mo"] } as const;
const commands: Record<string, string> = { quarantine: "quarantined", restore: "restored", suspend: "suspended", unsuspend: "restored access for",
  exclude_vote: "excluded a vote on", restore_vote: "restored a vote on", resolve_report: "resolved a report on", confirm_version: "confirmed",
  merge_version: "merged", approve_alias: "approved an alias for", reject_alias: "rejected an alias for", recompute: "recomputed" };
const str = (value: unknown) => typeof value === "string" ? value : null;

/** The verb phrase for an event from the server's verb code and details. */
export function verbPhrase(event: ActivityEvent) {
  const { verb, detail } = event;
  if (verb === "uploaded") return typeof detail.entries === "number" ? `uploaded ${detail.entries} ${detail.entries === 1 ? "entry" : "entries"} to` : "uploaded to";
  if (verb === "voted_up" || verb === "voted_down") return `voted ${verb.slice(6)}`;
  if (verb.startsWith("changed_vote_")) return `changed their vote to ${verb.slice(13)} on`;
  if (verb === "removed_vote") return "removed their vote on";
  if (verb === "vote_excluded") return "had a vote excluded on";
  if (verb === "vote_restored") return "had a vote restored on";
  if (verb === "reported") return `reported${str(detail.targetType) === "version" ? " a version" : ""}${str(detail.reason) ? ` as ${str(detail.reason)}` : ""}`;
  return commands[verb] ?? verb.replace(/_/g, " ");
}

/** One activity sentence built from server parts (never server HTML): actor, verb, filter, item and version. */
export function EventRow({ event, now }: { event: ActivityEvent; now?: number }) {
  const down = event.kind === "votes" && /down/.test(event.verb);
  const [icon, tone] = down ? ["▼", "re"] as const : event.kind === "moderation" && event.verb === "quarantine" ? ["⊘", "mo"] as const : icons[event.kind];
  const item = event.item ? <a href={itemLink(event.item.workId, event.version?.versionId, event.filter?.filterId)}>{itemTitle(event.item)}</a> : null;
  const version = event.version ? ` · ${versionName(event.version)}` : "";
  const filter = event.filter?.category ? filterSummary(event.filter) : null;
  const actor = event.actor?.type === "contributor" ? <a className="actor" href={contributorLink(event.actor.contributorId)}>{event.actor.displayName}</a>
    : <b>{event.actor?.type === "operator" ? event.actor.subject : "Service"}</b>;
  const note = [event.kind === "uploads" && str(event.detail.state) && event.detail.state !== "counted" ? titleCase(str(event.detail.state)!) : null,
    str(event.detail.reason) && event.kind === "moderation" ? `“${str(event.detail.reason)}”` : null,
    !item && !filter ? str(event.detail.resourceText) : null].filter(Boolean).join(" · ");
  return <div className="ev">
    <span className={`ic ${tone}`} aria-hidden="true">{icon}</span>
    <div className="grow">
      <div className="txt">{actor} {verbPhrase(event)}{event.subject && <> <b>{event.subject.displayName}</b></>}
        {filter && <> <b>{filter}</b></>}{!filter && item && <> {item}{version}</>}</div>
      {((filter && item) || note) && <div className="sub">{filter && item && <>{item}{version}</>}{filter && item && note ? " · " : ""}{note}</div>}
    </div>
    <span className="t">{ago(event.at, now)}</span>
  </div>;
}
