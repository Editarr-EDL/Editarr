import type { AccountDeletionPreview, AccountDeletionResult, DeletionBlocker, DeletionMode, Disposition } from "../../api/api-account-deletion";
import type { ExportPublication } from "../../api/api-mcf-types";

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

export const MODES: Record<DeletionMode, { label: string; summary: string }> = {
  retain: { label: "Keep data", summary: "The account stops signing in. Its reviews, scans, and personal export files stay exactly as they are." },
  transfer: { label: "Transfer data", summary: "Reviews and personal export files move to another account you choose." },
  delete: { label: "Delete permanently", summary: "Reviews are deleted, and scans that nothing else needs. You choose what happens to each published file." },
};

/** Plain-language explanations of MU-38 blockers. Each names only a count. */
const BLOCKERS: Record<string, (count: number) => string> = {
  link_in_progress: count => `${plural(count, "Jellyfin link")} of this account or the destination ${count === 1 ? "is" : "are"} still being finished. Try again shortly.`,
  account_transfer_unsettled: count => `${plural(count, "file move")} from an earlier transfer ${count === 1 ? "is" : "are"} not settled. Editarr retries at startup and every hour.`,
  account_jobs_running: count => `${plural(count, "scan")} by this account ${count === 1 ? "is" : "are"} running. Wait for ${count === 1 ? "it" : "them"} to finish or cancel ${count === 1 ? "it" : "them"}.`,
  scans_outside_access: count => `${plural(count, "scan")} by this account ${count === 1 ? "is" : "are"} on media you can't access, so you can't delete ${count === 1 ? "it" : "them"}. Keep the data instead, or ask the owner.`,
  reviews_outside_access: count => `${plural(count, "review")} by this account ${count === 1 ? "is" : "are"} hidden from you, so you can't delete or transfer ${count === 1 ? "it" : "them"}. Keep the data instead, or ask the owner.`,
  transfer_media_inaccessible: count => `${plural(count, "review")} ${count === 1 ? "is" : "are"} on media the destination can't access. Nothing is transferred partially. Give the destination access or choose another destination.`,
  export_filename_collision: count => `${plural(count, "moved file")} would take a file name the destination already publishes. Remove one or choose another destination.`,
  export_regeneration_pending: count => `${plural(count, "personal export")} ${count === 1 ? "is" : "are"} being regenerated. Wait for it to finish.`,
  export_recovery_required: count => `${plural(count, "personal export")} ${count === 1 ? "needs" : "need"} recovery first. Editarr recovers exports at startup and every hour.`,
  export_file_collision: count => `${plural(count, "file")} already ${count === 1 ? "exists" : "exist"} where a moved export would be written. An operator must remove ${count === 1 ? "it" : "them"} first.`,
  export_destination_changed: count => `${plural(count, "export destination")} changed. Review the deletion again.`,
  export_destination_unavailable: count => `${plural(count, "export media folder")} can't be reached.`,
};

export const blockerText = ({ code, count }: DeletionBlocker) => BLOCKERS[code]?.(count) ?? `Blocked: ${code} (${count}).`;

/**
 * What a transfer does to one visible publication, from the preview alone. A file targeting
 * the account moves to the destination's export ID; its policy follows its review's owner, so
 * only files of the account's own reviews switch to the destination. A detached file's policy
 * owner is not in the preview, so `detached` makes no policy claim (`transfer.policy_files`
 * counts those that switch).
 */
export function transferEffect(preview: AccountDeletionPreview, publication: ExportPublication) {
  return {
    moves: publication.target_user_id === preview.user_id,
    policy: publication.review_id === null ? "detached" as const : preview.reviews.ids.includes(publication.review_id) ? "destination" as const : "review owner" as const,
  };
}

/** One visible publication's transfer effect as a short phrase. */
export function transferEffectText(preview: AccountDeletionPreview, publication: ExportPublication, destination: string): string {
  const { moves, policy } = transferEffect(preview, publication);
  return [
    moves ? `moves to ${destination}'s export ID` : "stays with its recipient",
    { destination: `follows ${destination}'s filter policy`, "review owner": "keeps its review owner's filter policy", detached: "kept from a deleted review" }[policy],
  ].join("; ");
}

function transferFiles(preview: AccountDeletionPreview, transfer: { files_moved: number; policy_files: number }, destination: string): string {
  const policies = preview.publications.items.map(publication => transferEffect(preview, publication).policy);
  const follow = policies.filter(policy => policy === "destination").length;
  const keep = policies.filter(policy => policy === "review owner").length;
  return [
    `${plural(transfer.files_moved, "personal export file")} ${transfer.files_moved === 1 ? "moves" : "move"} to ${destination}'s export ID.`,
    follow > 0 && `${plural(follow, "listed file")} ${follow === 1 ? "follows" : "follow"} ${destination}'s filter policy as the new owner of ${follow === 1 ? "its review" : "their reviews"}.`,
    keep > 0 && `${plural(keep, "listed file")} ${keep === 1 ? "comes" : "come"} from another account's review and ${keep === 1 ? "keeps" : "keep"} that review owner's filter policy.`,
    transfer.policy_files > 0 && `${plural(transfer.policy_files, "kept file")} from deleted reviews now ${transfer.policy_files === 1 ? "follows" : "follow"} ${destination}'s filter policy.`,
    "Every affected file is regenerated.",
  ].filter(Boolean).join(" ");
}

/** Why an unneeded scan may still survive: a file kept from its review references it. */
export const scanStays = (one: boolean) => `a published file that stays still uses ${one ? "it" : "them"}`;

const reviews = (count: number, hidden: number) => `${plural(count, "saved review")}${hidden ? `, ${hidden} of them hidden from you` : ""}`;

/** One line per consequence of the previewed disposition, in reading order. */
export function consequences(preview: AccountDeletionPreview, disposition: Disposition, name: string): { label: string; text: string }[] {
  const { credentials, community, jobs } = preview;
  const access = [
    plural(credentials.sessions, "browser session") + (credentials.sessions === 1 ? " ends" : " end"),
    credentials.api_key && "its API key is deleted",
    credentials.linked && "its Jellyfin link is released",
  ].filter(Boolean).join(", ");
  const destination = disposition.mode === "transfer" ? disposition.destination.name : "";
  const one = preview.reviews.count === 1;
  const reviewText = {
    retain: `${reviews(preview.reviews.count, preview.reviews.hidden)} ${one ? "stays" : "stay"} unchanged, with ${one ? "its" : "their"} scans and filter preferences.`,
    transfer: `${reviews(preview.reviews.count, preview.reviews.hidden)} ${one ? "moves" : "move"} to ${destination}. ${destination} becomes the owner; ${name} stays recorded as the original author.`,
    delete: `${reviews(preview.reviews.count, preview.reviews.hidden)} ${one ? "is" : "are"} deleted.`,
  }[preview.mode];
  const fileText = preview.transfer
    ? transferFiles(preview, preview.transfer, destination)
    : preview.mode === "retain"
      ? `${plural(preview.personal_files, "personal export file")} ${preview.personal_files === 1 ? "stays" : "stay"} published exactly as ${preview.personal_files === 1 ? "it is" : "they are"}. Media players keep using ${preview.personal_files === 1 ? "it" : "them"}.`
      : "Each published file you can see is kept or removed as you choose below. Files you can't see are kept.";
  const stopped = jobs.waiting.length + jobs.running.length + jobs.hidden;
  const lines = [
    { label: "Account", text: `${name} can no longer sign in: ${access}. A disabled record stays for history, and the account leaves the Users list.` },
    { label: "Reviews", text: reviewText },
    { label: "Personal export files", text: fileText },
  ];
  if (stopped) lines.push({ label: "Scan jobs", text: `${plural(stopped, "waiting or running scan job")} ${stopped === 1 ? "stops" : "stop"}.` });
  if (preview.mode === "delete" && preview.scans.items.length > 0) {
    const kept = preview.scans.items.filter(scan => scan.required).length;
    const rest = preview.scans.items.length - kept;
    lines.push({ label: "Scans", text: [
      kept > 0 && `${plural(kept, "scan")} ${kept === 1 ? "is" : "are"} kept because another account's review, a file outside this account's reviews, or pending community work needs ${kept === 1 ? "it" : "them"}.`,
      rest > 0 && `${plural(rest, "scan")} ${rest === 1 ? "is" : "are"} deleted unless ${scanStays(rest === 1)}.`,
    ].filter(Boolean).join(" ") });
  }
  lines.push({ label: "Community", text: community.identity || community.operations
    ? `The community identity${community.operations ? ` and ${plural(community.operations, "community record")} (${community.pending} pending)` : ""} stay in place. Anything already shared with the community stays published; nothing is retracted, and Editarr does not contact the community service.`
    : "This account has no community identity or community history." });
  return lines;
}

/** What a completed deletion changed, naming only what the caller may see. */
export function resultLines(result: AccountDeletionResult, destination: string | null): string[] {
  const hidden = result.reviews_hidden ? ` (${result.reviews_hidden} hidden from you)` : "";
  const access = [
    plural(result.sessions_revoked, "browser session") + " ended",
    result.api_key_revoked && "API key deleted",
    result.identity_released && "Jellyfin link released",
    result.jellyfin_blocked && "Jellyfin user blocked until the owner unblocks them in Users",
    result.profile_removed && "profile removed",
  ].filter(Boolean).join(", ");
  return [
    result.mode === "retain" && `${plural(result.reviews_retained.length + result.reviews_hidden, "review")} kept unchanged${hidden}.`,
    result.mode === "transfer" && `${plural(result.reviews_transferred.length + result.reviews_hidden, "review")} transferred to ${destination ?? `account ${result.destination_id}`}${hidden}. ${plural(result.files_moved, "personal export file")} moved.`,
    result.mode === "delete" && `${plural(result.reviews_deleted.length + result.reviews_hidden, "review")} deleted${hidden}. ${plural(result.files_retained.length, "published file")} kept, ${result.files_removed.length} removed.`,
    result.mode === "delete" && `${plural(result.scans_deleted.length, "scan")} deleted, ${result.scans_kept.length} kept${result.scans_hidden ? `, ${result.scans_hidden} hidden from you` : ""}.`,
    result.jobs_stopped.length + result.jobs_hidden > 0 && `${plural(result.jobs_stopped.length + result.jobs_hidden, "scan job")} stopped.`,
    `${plural(result.personal_files_kept, "personal export file")} still published for this account.`,
    `${access}.`,
    (result.community_identity_kept || result.community_operations_kept > 0) && `Community identity and ${plural(result.community_operations_kept, "community record")} kept; nothing was retracted.`,
    result.replayed && "This deletion had already been applied; nothing changed this time.",
  ].filter((line): line is string => Boolean(line));
}
