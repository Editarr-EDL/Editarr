import type { OperatorResource } from "../../../api/api-community-operator";
import type { QueueItem } from "../../../api/api-community-operator-community";
import type { ExtrasDraft } from "../action-extras";
import type { ModerationTarget } from "../moderation-dialog";
import { parseResource, type OperatorActionKind } from "../moderation-commands";
import { filterSummary, itemTitle, titleCase, versionName } from "../shared/format";
import { itemLink } from "../routes";

export interface QueueAction { label: string; command: OperatorActionKind; target: ModerationTarget; revision?: number; extras?: ExtrasDraft;
  tone?: "primary" | "danger" }
export const queueLabels = { all: "Queue", reports: "Reports", aliases: "Alias suggestions", versions: "Candidate versions",
  quarantined: "Quarantined", disputed: "Disputed filters" } as const;
const aliasSlug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "alias";
const resources = new Set(["filter", "attestation", "submission", "contributor", "version", "vote", "report", "alias_suggestion"]);
/** The entry's own moderation target, from its `resource` name. */
export function queueTarget(entry: QueueItem): ModerationTarget | null {
  const parsed = parseResource(entry.resource);
  return resources.has(parsed.kind) ? parsed : null;
}

/** Icon letter and tone class for a queue entry. */
export const queueIcon = (entry: QueueItem) => ({ report: ["!", "re"], alias: ["A", "mo"], version: ["V", "up"], quarantined: ["⊘", "mo"],
  disputed: ["≠", "re"] } as const)[entry.kind];

/** What the entry is about: the labelled filter, or the item and version. */
function subject(entry: QueueItem) {
  if (entry.filter?.category) return filterSummary(entry.filter);
  if (entry.item && entry.version) return `${itemTitle(entry.item)} · ${versionName(entry.version)}`;
  return entry.title.text;
}
/** Headline for a queue entry, e.g. "Report · Inaccurate — Violence › Stabbing · skip · 00:20:00–00:20:10". */
export function queueTitle(entry: QueueItem) {
  switch (entry.kind) {
    case "report": return `Report · ${titleCase(entry.reason ?? "other")} — ${entry.title.targetType === "version" ? "version " : ""}${subject(entry)}`;
    case "alias": return `Alias suggestion · “${entry.suggestion?.text ?? subject(entry)}”`;
    case "version": return `Candidate version · ${subject(entry)}`;
    case "disputed": return `Disputed filter · ${subject(entry)}`;
    default: return `${titleCase(entry.target.type === "submission" ? "upload" : entry.target.type.replace("_", " "))} · ${subject(entry)}`;
  }
}
/** Context after the item link: the version (for filter entries), the reporter and any latest quarantine. */
export function queueContext(entry: QueueItem) {
  return [entry.filter && entry.version ? versionName(entry.version) : null, entry.reporter ? `reported by ${entry.reporter.displayName}` : null,
    entry.latestAction ? `${entry.latestAction.command} by ${entry.latestAction.actor}` : null].filter(Boolean).join(" · ");
}
/** Facts from `impact`, e.g. "1 upload · 1 filter · trust 0.21" or "▲0 ▼1 · 2 attestations". */
export function queueImpact(entry: QueueItem) {
  const impact = entry.impact;
  if (!impact) return null;
  const parts: string[] = [];
  const n = (key: string) => typeof impact[key] === "number" ? impact[key] as number : null;
  if (n("versionsWithLabel") !== null) parts.push(`${n("versionsWithLabel")} ${n("versionsWithLabel") === 1 ? "version uses" : "versions use"} this exact label`);
  if (n("uploads") !== null) parts.push(`${n("uploads")} ${n("uploads") === 1 ? "upload" : "uploads"}`);
  if (n("filters") !== null) parts.push(`${n("filters")} filters`);
  if (n("trust") !== null) parts.push(`trust ${n("trust")!.toFixed(2)}`);
  if (n("upvotes") !== null) parts.push(`votes ▲${n("upvotes")} ▼${n("downvotes") ?? 0}`);
  if (n("disputes")) parts.push(`${n("disputes")} disputes`);
  if (n("attestations") !== null) parts.push(`${n("attestations")} attestations`);
  if (n("confidence") !== null) parts.push(`confidence ${n("confidence")!.toFixed(2)}`);
  return parts.join(" · ") || null;
}
/** Where to review the entry in context. */
export function queueLink(entry: QueueItem) {
  if (!entry.item) return null;
  return itemLink(entry.item.workId, entry.version?.versionId, entry.filter?.filterId);
}

/** The reasoned actions offered for an entry; each re-checks fresh state before its confirmation dialog opens. */
export function queueActions(entry: QueueItem): QueueAction[] {
  const own = queueTarget(entry);
  if (!own) return [];
  const mine = { target: own, revision: entry.revision };
  const filter = entry.filter && entry.target.type === "filter" ? { kind: "filter" as OperatorResource, targetId: entry.filter.filterId } : null;
  const version = entry.target.type === "version" ? { kind: "version" as OperatorResource, targetId: entry.target.id } : null;
  switch (entry.kind) {
    case "report": return [{ label: "Resolve…", command: "resolve_report", ...mine, tone: "primary" },
      ...(filter ? [{ label: "Quarantine filter", command: "quarantine" as const, target: filter }] :
        version ? [{ label: "Merge…", command: "merge_version" as const, target: version }] : [])];
    case "alias": {
      const suggestion = entry.suggestion;
      const extras: ExtrasDraft = { create: true, aliasId: aliasSlug(suggestion?.normalized ?? ""), label: suggestion?.text.trim().slice(0, 64) };
      return [{ label: "Approve", command: "approve_alias", ...mine, extras, tone: "primary" }, { label: "Reject", command: "reject_alias", ...mine }];
    }
    case "version": return [{ label: "Confirm version", command: "confirm_version", ...mine, tone: "primary" },
      { label: "Merge…", command: "merge_version", ...mine }, { label: "Quarantine version", command: "quarantine", ...mine }];
    case "disputed": return [{ label: "Quarantine filter", command: "quarantine", ...mine, tone: "danger" }];
    default: return [{ label: `Restore ${own.kind === "submission" ? "upload" : own.kind.replace("_", " ")}`,
      command: own.kind === "vote" ? "restore_vote" : "restore", ...mine }];
  }
}
