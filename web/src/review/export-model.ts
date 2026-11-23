import type { Capability } from "../auth/capabilities";
import type { ExportFormat, ExportPublication, ExportTarget } from "../api/api-mcf-types";

export const EXPORT_FORMATS = ["edl", "mcf"] as const satisfies readonly ExportFormat[];

export type ExportCondition = "current" | "outdated" | "regenerating" | "regeneration_failed" | "withdrawn" | "deleted";

/** Label and plain-language consequence of each publication condition. */
export const EXPORT_CONDITIONS: Record<ExportCondition, { label: string; explanation: string }> = {
  current: { label: "Current", explanation: "Matches the saved review and its owner's current policy." },
  outdated: { label: "Outdated", explanation: "Publish again to apply the current saved review and enforced filters." },
  regenerating: { label: "Regenerating", explanation: "Updating for a policy change. The previous file stays available until regeneration finishes." },
  regeneration_failed: { label: "Regeneration failed", explanation: "Could not be updated for the current policy. The previous file is still in place until it is withdrawn." },
  withdrawn: { label: "Withdrawn", explanation: "Removed because it could not be regenerated under the current policy. Publish again to restore it." },
  deleted: { label: "Deleted", explanation: "The file was already gone when it was deleted. Editarr keeps the record so a returning copy is recognized." },
};

/** Collapse the discovery flags into the one condition a reader acts on. */
export function exportCondition(publication: ExportPublication): ExportCondition {
  if (publication.regenerating) return "regenerating";
  if (publication.failed) return publication.serving ? "regeneration_failed" : "withdrawn";
  if (!publication.serving) return "deleted";
  return publication.current ? "current" : "outdated";
}

export type Allowed = (capability: Capability) => boolean;

export interface ExportDestination {
  target: ExportTarget;
  /** Whose personal file this is; null for the item default. */
  userId: number | null;
}

/**
 * Destinations the caller may publish a review to. Another user's review needs
 * `reviews.export.other`; a personal file for yourself also needs
 * `exports.personal.publish.own`, and the item default `exports.default.publish`.
 */
export function publishDestinations(reviewOwnerId: number | null | undefined, me: number | null, allowed: Allowed): ExportDestination[] {
  const other = reviewOwnerId != null && me != null && reviewOwnerId !== me;
  if (other && !allowed("reviews.export.other")) return [];
  const destinations: ExportDestination[] = [];
  if (allowed("exports.personal.publish.own")) destinations.push({ target: "self", userId: me });
  if (other) destinations.push({ target: "review_owner", userId: reviewOwnerId });
  if (allowed("exports.default.publish")) destinations.push({ target: "item_default", userId: null });
  return destinations;
}

export function canDownload(reviewOwnerId: number | null | undefined, me: number | null, allowed: Allowed): boolean {
  const other = reviewOwnerId != null && me != null && reviewOwnerId !== me;
  return allowed("exports.personal.download.own") && (!other || allowed("reviews.export.other"));
}

/** A saved review can be exported when any existing download or publication action is available. */
export function canExportSavedReview(reviewOwnerId: number | null | undefined, me: number | null, allowed: Allowed): boolean {
  return canDownload(reviewOwnerId, me, allowed) || publishDestinations(reviewOwnerId, me, allowed).length > 0;
}

/** Whether the caller holds the capability that deleting this file exercises. */
export function canDeletePublication(publication: ExportPublication, me: number | null, allowed: Allowed): boolean {
  if (publication.scope === "default") return allowed("exports.default.delete");
  return allowed(publication.target_user_id === me ? "exports.personal.delete.own" : "exports.personal.delete.other");
}

/** The publication currently occupying a destination for one format, if the caller can see it. */
export function occupant(publications: readonly ExportPublication[], destination: ExportDestination, format: ExportFormat): ExportPublication | undefined {
  return publications.find((publication) => publication.format === format && (destination.target === "item_default"
    ? publication.scope === "default"
    : publication.scope === "personal" && publication.target_user_id === destination.userId));
}
