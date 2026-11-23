import { runtimeApiBasePath } from "../shared/runtime-urls";
import type { ReviewExportRequest, ReviewTargetRequest } from "./api-review-types";
import { jsonApi } from "./api-transport";

export interface SubtitleSource {
  id: string;
  label: string;
  language: string | null;
  format: string;
  kind: "external" | "embedded" | "transcript";
  forced: boolean;
  sdh: boolean;
  supported: boolean;
  recommended: boolean;
  published_filename?: string | null;
}

export interface PublishedSubtitle {
  filename: string;
  label: string;
  format: string;
  forced: boolean;
}

export interface ReviewSubtitleSources {
  sources: SubtitleSource[];
  audio_sources: { id: string; label: string; language: string | null }[];
  audio_source_id: string | null;
  fallback: "available" | "scan_required" | "choose_audio" | null;
  published_subtitles: PublishedSubtitle[];
}

export interface SubtitleCuePreview {
  start_seconds: number;
  end_seconds: number;
  original: string;
  filtered: string;
}

export interface ReviewSubtitlePreview {
  source: SubtitleSource;
  cues: SubtitleCuePreview[];
  changed_cues: number;
  unchanged_cues: number;
  preview_token: string;
}

export interface ReviewSubtitlePublication {
  source: SubtitleSource;
  filename: string;
  replaced: boolean;
  refresh_pending?: boolean;
}

export type ReviewSubtitleAudioRequest = ReviewExportRequest & { audio_source_id?: string };
export type ReviewSubtitleSourceRequest = ReviewSubtitleAudioRequest & { source_id: string };
export type ReviewSubtitlePublicationRequest = ReviewSubtitleSourceRequest & { preview_token: string };

function subtitleRequest<T>(
  reviewId: string,
  operation: "sources" | "preview" | "publish",
  request: ReviewSubtitleAudioRequest,
  fallback: string,
  signal?: AbortSignal,
) {
  const path = `${runtimeApiBasePath()}/reviews/${encodeURIComponent(reviewId)}/subtitles/${operation}`;
  return jsonApi<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  }, fallback);
}

export function fetchReviewSubtitleSources(id: string, request: ReviewSubtitleAudioRequest, signal?: AbortSignal) {
  return subtitleRequest<ReviewSubtitleSources>(id, "sources", request, "Unable to find subtitle tracks", signal);
}

export function previewReviewSubtitles(id: string, request: ReviewSubtitleSourceRequest, signal?: AbortSignal) {
  return subtitleRequest<ReviewSubtitlePreview>(id, "preview", request, "Unable to preview filtered subtitles", signal);
}

export function publishReviewSubtitles(id: string, request: ReviewSubtitlePublicationRequest) {
  return subtitleRequest<ReviewSubtitlePublication>(id, "publish", request, "Unable to publish filtered subtitles");
}

export async function fetchPublishedSubtitles(target: ReviewTargetRequest, signal?: AbortSignal) {
  const query = new URLSearchParams({ source: target.source, item_id: target.item_id });
  if (target.media_source_id) query.set("media_source_id", target.media_source_id);
  const result = await jsonApi<{ published_subtitles: PublishedSubtitle[] }>(
    `${runtimeApiBasePath()}/media/subtitles/publications?${query}`,
    { signal },
    "Unable to load published subtitles",
  );
  return result.published_subtitles;
}
