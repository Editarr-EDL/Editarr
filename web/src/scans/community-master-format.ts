import type {
  MasterFilter, MasterPreferences, MasterSharingSummary, UpdateCounts, UpdateGroup, UpdateRow, VersionChoice,
} from "../api/api-community-master-types";

export const UNCONFIRMED_TOOLTIP = "This version hasn't been verified yet. Its filters come from contributors whose files "
  + "could only be loosely matched (by ID, title, or runtime). It becomes confirmed once several contributors with "
  + "matching files submit for it, or after moderator review.";

export const updateGroups: { id: UpdateGroup; label: string }[] = [
  { id: "new", label: "New" }, { id: "changed", label: "Changed" },
  { id: "removed", label: "Removed" }, { id: "nowLowConfidence", label: "Now Low Confidence" },
];

/** Turn a taxonomy token such as `strong_language` or `sexContent` into a sentence-case label. */
export function categoryLabel(value: string): string {
  const spaced = value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Format milliseconds as `h:mm:ss.mmm` (hours only when present). */
export function formatMs(ms: number) {
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor(ms / 60_000) % 60;
  const clock = `${String(minutes).padStart(2, "0")}:${(ms % 60_000 / 1000).toFixed(3).padStart(6, "0")}`;
  return hours > 0 ? `${hours}:${clock}` : clock;
}

export const formatRange = (value: { startMs: number; endMs: number }) => `${formatMs(value.startMs)}–${formatMs(value.endMs)}`;
export const percent = (value: number) => `${Math.round(value * 100)}%`;
export const filterName = (filter: { category: string; subcategory?: string | null }) =>
  [categoryLabel(filter.category), filter.subcategory && filter.subcategory !== filter.category
    ? categoryLabel(filter.subcategory) : null].filter(Boolean).join(" · ");

/**
 * Whether a row is shown under the user's preferences. Touched rows ignore the threshold and the Low
 * Confidence toggle; untouched rows need confidence ≥ threshold and, when Low Confidence, the toggle.
 */
export function filterVisible(filter: MasterFilter, preferences: MasterPreferences) {
  if (filter.touched) return true;
  if (filter.master.lowConfidence && !preferences.showLowConfidence) return false;
  return filter.master.confidence >= preferences.confidenceThreshold;
}

/** Mirror the contract's `active` rule so preference changes apply before the server round trip. */
export function filterActive(filter: MasterFilter, preferences: MasterPreferences, alignmentRequired: boolean) {
  if (alignmentRequired) return false;
  const states = preferences.showLowConfidence ? ["pending", "verified", "adjusted", "low_confidence"] : ["pending", "verified", "adjusted"];
  if (!states.includes(filter.state)) return false;
  return filter.touched || filterVisible(filter, preferences);
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** Render sharing estimates as "≈N new, ≈M confirmations, K adjustments, D disputes". */
export function estimateText({ estimates }: Pick<MasterSharingSummary, "estimates">) {
  const approx = estimates.approximate ? "≈" : "";
  return [`${approx}${estimates.newFilters} new`, `${approx}${plural(estimates.confirmations, "confirmation")}`,
    plural(estimates.adjustments, "adjustment"), plural(estimates.disputes, "dispute")].join(", ");
}

export const updateTotal = (counts: UpdateCounts | null | undefined) =>
  counts ? counts.new + counts.changed + counts.removed + counts.nowLowConfidence : 0;

/** Banner copy: "Community filters updated: X new, Y changed, Z removed." plus Low Confidence when present. */
export function bannerText(counts: UpdateCounts) {
  const parts = [`${counts.new} new`, `${counts.changed} changed`, `${counts.removed} removed`];
  if (counts.nowLowConfidence) parts.push(`${counts.nowLowConfidence} now Low Confidence`);
  return `Community filters updated: ${parts.join(", ")}.`;
}

/** Rows that group-level Accept all covers: untouched rows whose Accept is enabled. */
export const acceptAllRows = (rows: UpdateRow[]) =>
  rows.filter((row) => !row.touched && row.canAccept && row.allowed.includes("accept"));

export type PickerValue = { selection: string; otherText: string };
export const OTHER = "__other__";

/** Convert the picker state into the contract's `version_choice`, or null until a version is chosen. */
export function versionChoice({ selection, otherText }: PickerValue): VersionChoice | null {
  if (!selection) return null;
  if (selection === OTHER) return { choice: "other", text: otherText.trim() || null };
  return { choice: "alias", alias_id: selection };
}

const errorMessages: Record<string, string> = {
  network_unavailable: "Community service is offline. Try again.",
  service_unavailable: "Community service is unavailable. Try again later.",
  not_configured: "Community sharing isn’t set up on this server.",
  not_connected: "Connect to the community service in Settings first.",
  rotation_pending: "Your community key is being rotated. Try again once it finishes.",
  capability_denied: "Your account can’t do this.",
  not_found: "This item or community filter set is no longer available to you.",
  validation_failed: "Some of the values aren’t valid. Check them and try again.",
  confirmation_required: "Confirm this action and try again.",
  find_expired: "These results expired. Find community filters again.",
  target_changed: "This file changed. Find community filters again.",
  replace_required: "This file already uses another community version. Confirm to replace it.",
  version_merged: "This version was merged into another one. Find community filters again.",
  version_unavailable: "The community no longer offers this version. Find community filters again.",
  version_not_found: "The community service no longer knows this version. Find community filters again.",
  link_unavailable: "That community filter set is no longer in use for this file. Refresh and try again.",
  snapshot_invalid: "The downloaded filter set couldn’t be verified.",
  alignment_not_required: "This version already matches your file, so it doesn’t need alignment.",
  alignment_out_of_range: "That alignment moves filters off your file or drifts too far. Check the anchor times.",
  alignment_preview_changed: "The alignment preview changed. Preview it again.",
  filter_state_changed: "This filter changed elsewhere. Refresh and try again.",
  vote_ineligible: "You can’t vote on this filter.",
  vote_pending: "Another vote on this filter is still being sent. Try again in a moment.",
  operation_conflict: "This vote conflicts with one already sent. Refresh and vote again.",
  receipt_owner_mismatch: "Download this filter set again with your account before voting or reporting.",
  report_ineligible: "You can only report the version this file uses.",
  decision_not_allowed: "That choice isn’t available for this filter.",
  update_changed: "A newer update arrived. Review the latest changes.",
  work_identity_required: "Add a TMDB or TVDB ID (with season and episode for TV) before sharing.",
  invalid_response: "Community response couldn’t be read.",
};
const voteReasons: Record<string, string> = {
  self_vote: "You can’t vote on your own filter.",
  receipt_required: "Download this filter set again before voting.",
};

type ErrorDetails = { code: string; reason?: string | null; retryAfter?: number | null; validation?: string[] };

/** User-facing text for a community master route failure, including the rate-limit wait and 422 details. */
export function masterErrorText(reason: unknown) {
  if (typeof reason !== "object" || reason === null || !("code" in reason)) return errorMessages.network_unavailable;
  const { code, reason: detail, retryAfter, validation } = reason as ErrorDetails;
  if (code === "vote_ineligible" && detail && voteReasons[detail]) return voteReasons[detail];
  if (code === "rate_limited") {
    return retryAfter ? `Please wait ${plural(retryAfter, "second")} before trying again.` : "Please wait a moment before trying again.";
  }
  if (code === "validation_failed" && validation?.length) return `Some of the values aren’t valid: ${validation[0].replace(/\.$/, "")}.`;
  return errorMessages[code] ?? "Community filters are unavailable right now.";
}
