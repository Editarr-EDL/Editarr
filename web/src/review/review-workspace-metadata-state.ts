import type { McfPreflightIssue, ReviewMcfDocument } from "../api/api-review-types";
import type { SavedReviewPanelProps } from "./review-workspace-state";

export function catalogReviewMcfDocument(
  item: SavedReviewPanelProps["item"],
  seasonNumber: number | null | undefined,
  mediaSourceId: string | null,
): ReviewMcfDocument {
  const selectedVersion = item.versions?.find((version) => version.id === mediaSourceId);
  const itemType = item.item_type?.toLowerCase();
  return {
    title: item.name ?? null,
    year: item.year ?? null,
    work_type: itemType === "movie" || itemType === "series" || itemType === "episode" ? itemType : null,
    season: itemType === "episode" && (seasonNumber ?? item.season_number ?? 0) > 0 ? (seasonNumber ?? item.season_number ?? null) : null,
    episode: itemType === "episode" && item.index_number && item.index_number > 0 ? item.index_number : null,
    source_url: null,
    imdb_url: null,
    release: selectedVersion ? selectedVersion.edition || selectedVersion.name : null,
    comment: null,
    material_start_seconds: 0,
    material_end_seconds: selectedVersion?.duration_seconds ?? item.duration_seconds ?? null,
  };
}

export function focusMcfPreflightIssue(issue: McfPreflightIssue): void {
  const editor = document.querySelector<HTMLDetailsElement>(".mcf-document-editor");
  if (editor) editor.open = true;
  const fieldLabels: Record<string, string> = {
    title: "MCF title", year: "MCF year", type: "MCF type", work_type: "MCF type",
    season: "MCF season", episode: "MCF episode", source: "MCF source URL",
    source_url: "MCF source URL", imdb: "MCF IMDb URL", imdb_url: "MCF IMDb URL",
    release: "MCF release", comment: "MCF document comment", start: "MCF material start",
    material_start_seconds: "MCF material start", end: "MCF material end",
    material_end_seconds: "MCF material end",
  };
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`[aria-label="${fieldLabels[issue.field] || "MCF title"}"]`)?.focus());
}