import type { OperatorInspection, OperatorResource } from "../../api/api-community-operator";
import type { OperatorCommand } from "../../api/api-community-operator-actions";

/** A moderation command, or a recompute of the inspected version. */
export type OperatorActionKind = OperatorCommand | "recompute";
const actionLabels: Record<OperatorActionKind, string> = { quarantine: "Quarantine", restore: "Restore",
  suspend: "Suspend contributor", unsuspend: "Restore contributor access", exclude_vote: "Exclude vote",
  restore_vote: "Restore vote", resolve_report: "Resolve report", confirm_version: "Confirm version",
  merge_version: "Merge into another version", approve_alias: "Approve alias", reject_alias: "Reject alias",
  recompute: "Recompute version" };
/** Button and dialog label; visibility actions name their resource, for example "Quarantine filter". */
export function actionLabel(action: OperatorActionKind, kind: OperatorResource) {
  return action === "quarantine" || action === "restore" ? `${actionLabels[action]} ${kind}` : actionLabels[action];
}
const recomputes = "Every affected version is recomputed and its master revision advances only if served content changes.";
export const actionImpacts: Record<OperatorActionKind, string> = {
  quarantine: `Exclude this resource from master sets; stored evidence and audit are retained. ${recomputes}`,
  restore: `Include this resource in master sets again if nothing else excludes it. ${recomputes}`,
  suspend: "Block all community access for this contributor and all their installations. Keys remain unchanged; submissions and votes are preserved. Existing local downloads cannot be recalled.",
  unsuspend: "Remove this contributor's suspension. Membership and key checks still apply; quarantines remain. No key is reactivated or rotated.",
  exclude_vote: `Exclude this contributor's vote on this filter from its counts; the original vote and audit are retained. ${recomputes}`,
  restore_vote: `Count this vote again. ${recomputes}`,
  resolve_report: "Close this private report and retain its text and audit. This does not quarantine anything, suspend a contributor or change votes.",
  confirm_version: "Change this candidate version to confirmed and recompute its master set.",
  merge_version: "Mark this version merged into the target version of the same work. Its submissions, filters, votes and receipt eligibility move to the target, moved submissions lose their evidence tier, and the target is recomputed. This cannot be undone.",
  approve_alias: "Add this spelling to an existing alias or create a new alias. Versions labeled with this exact text are relabeled and their master revision advances.",
  reject_alias: "Reject this alias suggestion. No version label changes.",
  recompute: "Recompute this version's master set under the current policy. Nothing changes when no input changed.",
};

/** Split a moderation resource name into its kind, target ID and, for votes, contributor ID. */
export function parseResource(resource: string) {
  const index = resource.indexOf(":");
  const kind = resource.slice(0, index) as OperatorResource, rest = resource.slice(index + 1);
  if (kind !== "vote") return { kind, targetId: rest };
  const split = rest.lastIndexOf(":");
  return { kind, targetId: rest.slice(0, split), contributorId: rest.slice(split + 1) };
}

/** Actions the current state of an inspected resource permits. */
export function commandsFor(data: OperatorInspection): OperatorActionKind[] {
  const { kind } = parseResource(data.resource), state = data.state;
  const visibility: OperatorActionKind[] = [state.quarantined === 1 ? "restore" : "quarantine"];
  if (kind === "filter" || kind === "attestation" || kind === "submission") return visibility;
  if (kind === "contributor") return [state.state === "active" ? "suspend" : "unsuspend", ...visibility];
  if (kind === "version") return state.status === "merged_into" ? [] :
    [...(state.status === "candidate" ? ["confirm_version" as const] : []), "merge_version", ...visibility, "recompute"];
  if (kind === "alias_suggestion") return state.state === "pending" ? ["approve_alias", "reject_alias"] : [];
  if (kind === "report") return state.state === "open" ? ["resolve_report"] : [];
  if (kind === "vote") return [state.excluded === 1 ? "restore_vote" : "exclude_vote"];
  return [];
}
