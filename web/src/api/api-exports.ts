import type { ExportFormat, ExportPublication, ExportTarget } from "./api-mcf-types";
import type { ReviewExportRequest } from "./api-review-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { apiRequest, jsonApi, responseError } from "./api-transport";

export interface PublicationRequest extends ReviewExportRequest {
  target: ExportTarget;
  confirmation?: string;
}

/** A publish either lands, or the server names the replacement it needs confirmed. */
export type PublishOutcome =
  | { kind: "published"; publication: ExportPublication }
  | { kind: "confirmation_required"; confirmation: string };

const reviewPath = (reviewId: string) => `${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}`;

/** List every canonical publication the caller may read for one physical media version. */
export async function fetchVersionPublications(mediaItemId: string, mediaSourceId: string | null): Promise<ExportPublication[]> {
  const query = new URLSearchParams({ media_item_id: mediaItemId, media_source_id: mediaSourceId ?? "" });
  const result = await jsonApi<{ publications?: unknown }>(`${runtimeApiBasePath()}/exports?${query}`, {}, "Unable to load published exports");
  return Array.isArray(result?.publications) ? result.publications as ExportPublication[] : [];
}

/**
 * Publish one format of a saved review to a destination. A 409
 * `export_confirmation_required` becomes an outcome carrying the server's
 * confirmation, which binds the destination, review revision, content, and the
 * file being replaced. Every other refusal, including a stale confirmation, throws.
 */
export async function publishReviewExport(reviewId: string, format: ExportFormat, request: PublicationRequest): Promise<PublishOutcome> {
  const response = await apiRequest(`${reviewPath(reviewId)}/exports/${format}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
  });
  if (response.status === 409) {
    const body = await response.clone().json().catch(() => null) as { detail?: { code?: unknown; confirmation?: unknown } } | null;
    const confirmation = body?.detail?.confirmation;
    if (body?.detail?.code === "export_confirmation_required" && typeof confirmation === "string") {
      return { kind: "confirmation_required", confirmation };
    }
  }
  if (!response.ok) throw await responseError(response, `Unable to publish ${format.toUpperCase()}`);
  return { kind: "published", publication: await response.json() };
}

function attachmentName(header: string | null, fallback: string): string {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (!encoded) return fallback;
  try { return decodeURIComponent(encoded); } catch { return fallback; }
}

/** Fetch the caller's personal rendering of a review without publishing it. */
export async function downloadReviewExport(reviewId: string, format: ExportFormat, request: ReviewExportRequest): Promise<{ blob: Blob; filename: string }> {
  const response = await apiRequest(`${reviewPath(reviewId)}/exports/${format}/download`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
  });
  if (!response.ok) throw await responseError(response, `Unable to download ${format.toUpperCase()}`);
  return { blob: await response.blob(), filename: attachmentName(response.headers.get("content-disposition"), `review.${format}`) };
}

/** Delete one published file, bound to the UUID and revision the caller saw. */
export function deletePublication(publication: Pick<ExportPublication, "id" | "publication_id" | "publication_revision" | "format">): Promise<void> {
  const query = new URLSearchParams({ revision: String(publication.publication_revision), publication_id: publication.publication_id });
  return jsonApi(`${runtimeApiBasePath()}/exports/${publication.id}?${query}`, { method: "DELETE" }, `Unable to delete ${publication.format.toUpperCase()}`);
}
