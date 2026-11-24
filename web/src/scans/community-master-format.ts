import type {
  MasterFilter, MasterPreferences, MasterSharingSummary, UpdateCounts, UpdateGroup, UpdateRow, VersionChoice,
} from "../api/api-community-master-types";
import { categoryLabel } from "./community-result-state";

export const UNCONFIRMED_TOOLTIP = "This version hasn't been verified yet. Its filters come from contributors whose files "
  + "could only be loosely matched (by ID, title, or runtime). It becomes confirmed once several contributors with "
  + "matching files submit for it, or after moderator review.";

export const updateGroups: { id: UpdateGroup; label: string }[] = [
  { id: "new", label: "New" }, { id: "changed", label: "Changed" },
  { id: "removed", label: "Removed" }, { id: "nowLowConfidence", label: "Now Low Confidence" },
];

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
  if (!["pending", "verified", "adjusted"].includes(filter.state)) return false;
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
  capability_denied: "Your account can’t do this.",
  find_expired: "These results expired. Find community filters again.",
  target_changed: "This file changed. Find community filters again.",
  version_merged: "This version was merged into another one. Find community filters again.",
  snapshot_invalid: "The downloaded filter set couldn’t be verified.",
  alignment_preview_changed: "The alignment preview changed. Preview it again.",
  filter_state_changed: "This filter changed elsewhere. Refresh and try again.",
  vote_ineligible: "You can’t vote on this filter.",
  decision_not_allowed: "That choice isn’t available for this filter.",
  update_changed: "A newer update arrived. Review the latest changes.",
  work_identity_required: "Add a TMDB or TVDB ID (with season and episode for TV) before sharing.",
  invalid_response: "Community response couldn’t be read.",
};
const voteReasons: Record<string, string> = {
  self_vote: "You can’t vote on your own filter.",
  receipt_required: "Download this filter set again before voting.",
};

/** User-facing text for a community master route failure. */
export function masterErrorText(reason: unknown) {
  if (typeof reason !== "object" || reason === null || !("code" in reason)) return errorMessages.network_unavailable;
  const { code, reason: detail } = reason as { code: string; reason?: string | null };
  if (code === "vote_ineligible" && detail && voteReasons[detail]) return voteReasons[detail];
  return errorMessages[code] ?? "Community filters are unavailable right now.";
}
