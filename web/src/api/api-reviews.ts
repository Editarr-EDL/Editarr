import type {
  ExportFormat,
  Scan,
  ExportStatus,
  SavedReviewSummary,
  SavedReview,
  ReviewCreate,
  ReviewSave,
  ReviewRetimeRequest,
  ReviewRetimeResult,
  ReviewRename,
  ReviewDuplicate,
  ReviewPolicyToken,
  ReviewFilterInput,
  ReviewDeletionPreview,
  ReviewDeletionRequest,
  ReviewDeletionResult,
  McfImportResult,
  McfPreflight,
  EdlImportResult,
  ReviewTargetRequest,
  EdlImportOptions,
  McfImportOptions,
  CreditsDetectionStartRequest,
  CreditsDetectionStartResult,
  CreditsAcceptanceRequest,
  CreditsAcceptanceResult,
} from "./api-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { notifyScansChanged } from "../shared/scan-events";
import { apiRequest, responseError, jsonApi } from "./api-transport";
const fetch = apiRequest;

export function reviewPolicyToken(review: ReviewPolicyToken): ReviewPolicyToken {
  return { policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision };
}

export function applyReviewFilterDefaults(reviewId: string, filters: ReviewFilterInput[], token: ReviewPolicyToken): Promise<{
  filters: ReviewFilterInput[]; base_filters?: ReviewFilterInput[]; selection_changes: number; severity_changes: number; channel_changes: number;
} & ReviewPolicyToken> {
  return jsonApi(`${runtimeApiBasePath()}/settings/filters/apply`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_id: reviewId, filters, ...reviewPolicyToken(token) }),
  }, "Unable to apply filter defaults");
}

function reviewQuery(params: { media_source_id?: string | null; sort?: "name" | "updated_at"; direction?: "asc" | "desc" }): string {
  const query = new URLSearchParams();
  if (params.media_source_id !== undefined && params.media_source_id !== null) {
    query.set("media_source_id", params.media_source_id);
  }
  if (params.sort) query.set("sort", params.sort);
  if (params.direction) query.set("direction", params.direction);
  const value = query.toString();
  return value ? `?${value}` : "";
}

export function fetchSavedReviews(
  itemId: string,
  params: { media_source_id?: string | null; sort?: "name" | "updated_at"; direction?: "asc" | "desc" } = {},
): Promise<SavedReviewSummary[]> {
  return jsonApi(`${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/reviews${reviewQuery(params)}`, {}, "Unable to load saved reviews");
}

export function createSavedReview(target: ReviewTargetRequest, data: ReviewCreate): Promise<SavedReview> {
  const path = `${runtimeApiBasePath()}/library-items/${encodeURIComponent(target.item_id)}/reviews`;
  const body = { ...data, media_source_id: target.media_source_id ?? null };
  return jsonApi(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, "Unable to create saved review");
}

export function fetchSavedReview(reviewId: string): Promise<SavedReview> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}`, {}, "Unable to load saved review");
}

export function recoverSavedReview(reviewId: string): Promise<SavedReviewSummary> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/repair/recover`, { method: "POST" }, "Unable to recover saved review");
}

export function redownloadReviewCommunityScan(reviewId: string, confirmed = false): Promise<SavedReviewSummary> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/repair/community`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmed }),
  }, "Unable to retrieve the missing community scan");
}

export function saveSavedReview(reviewId: string, data: ReviewSave): Promise<SavedReview> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to save saved review");
}

export function retimeSavedReview(reviewId: string, data: ReviewRetimeRequest): Promise<ReviewRetimeResult> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/retime`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to adjust cue times");
}

export function renameSavedReview(reviewId: string, data: ReviewRename): Promise<SavedReview> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to rename saved review");
}

export function duplicateSavedReview(reviewId: string, data: ReviewDuplicate): Promise<SavedReview> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/duplicate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to duplicate saved review");
}

/** Preview a review deletion: the published files the caller may see and their permitted choices. */
export function fetchReviewDeletion(reviewId: string): Promise<ReviewDeletionPreview> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/deletion`, {}, "Unable to preview review deletion");
}

/** Delete a review with one retain/remove choice per previewed file. */
export function deleteReviewWithChoices(reviewId: string, data: ReviewDeletionRequest): Promise<ReviewDeletionResult> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/deletion`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
  }, "Unable to delete saved review");
}

export function fetchSavedReviewMutePreview(reviewId: string, filterId: string): Promise<Blob> {
  return fetch(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/filters/${encodeURIComponent(filterId)}/mute-preview`).then(async (response) => {
    if (!response.ok) throw await responseError(response, "Unable to render review mute preview");
    return response.blob();
  });
}

export function fetchSavedReviewVideoPreview(reviewId: string, filterId: string): Promise<Blob> {
  return fetch(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/filters/${encodeURIComponent(filterId)}/video-preview`).then(async (response) => {
    if (!response.ok) throw await responseError(response, "Unable to render review video preview");
    return response.blob();
  });
}

export function fetchMcfPreflight(reviewId: string): Promise<McfPreflight> {
  return jsonApi(`${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/mcf/preflight`, {}, "Unable to validate MCF export");
}

export function importEdlUpload(file: File, options: EdlImportOptions): Promise<EdlImportResult> {
  const body = new FormData();
  body.append("file", file);
  body.append("options", JSON.stringify(options));
  return jsonApi(`${runtimeApiBasePath()}/edl-imports/upload`, { method: "POST", body }, "Unable to import EDL");
}

export function importMcfUpload(file: File, options: McfImportOptions): Promise<McfImportResult> {
  const body = new FormData();
  body.append("file", file, file.name);
  body.append("options", JSON.stringify(options));
  return jsonApi(`${runtimeApiBasePath()}/mcf-imports/upload`, { method: "POST", body }, "Unable to import MCF");
}

export async function startCreditsDetection(data: CreditsDetectionStartRequest): Promise<CreditsDetectionStartResult> {
  const result = await jsonApi<CreditsDetectionStartResult>(`${runtimeApiBasePath()}/credits-detections`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to start credits detection");
  notifyScansChanged();
  return result;
}

export function fetchCreditsDetection(scanId: string): Promise<Scan> {
  return jsonApi(`${runtimeApiBasePath()}/credits-detections/${encodeURIComponent(scanId)}`, {}, "Unable to load credits detection");
}

export async function submitCreditsModelConsent(scanId: string, decision: "acquire" | "continue_without_qwen"): Promise<CreditsDetectionStartResult> {
  const result = await jsonApi<CreditsDetectionStartResult>(`${runtimeApiBasePath()}/credits-detections/${encodeURIComponent(scanId)}/model-consent`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }, "Unable to continue credits detection");
  notifyScansChanged();
  return result;
}

export function acceptCreditsDetection(scanId: string, data: CreditsAcceptanceRequest): Promise<CreditsAcceptanceResult> {
  return jsonApi(`${runtimeApiBasePath()}/credits-detections/${encodeURIComponent(scanId)}/accept`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }, "Unable to accept credits detection");
}

export function creditsDetectionClipUrl(scanId: string, boundarySeconds: number): string {
  const query = new URLSearchParams({ boundary_seconds: String(boundarySeconds) });
  return `${runtimeApiBasePath()}/credits-detections/${encodeURIComponent(scanId)}/clip?${query}`;
}

function exportQuery(mediaSourceId: string | null): string {
  return mediaSourceId ? `?${new URLSearchParams({ media_source_id: mediaSourceId })}` : "";
}

export function fetchExportStatus(itemId: string, mediaSourceId: string | null): Promise<ExportStatus> {
  const path = `${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/exports${exportQuery(mediaSourceId)}`;
  return jsonApi(path, {}, "Unable to load export status");
}

export function deleteExport(itemId: string, mediaSourceId: string | null, format: ExportFormat): Promise<void> {
  const path = `${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/exports/${format}${exportQuery(mediaSourceId)}`;
  return jsonApi(path, { method: "DELETE" }, `Unable to delete ${format.toUpperCase()}`);
}
