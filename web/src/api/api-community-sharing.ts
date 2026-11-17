import { apiRequest } from "./api-transport";
import { CommunityApiError } from "./api-community";
import type { MediaIdentity, EntityType, IdentityProvider, EpisodeNumber } from "./api-media-identity";
import type { AudioTrack } from "./api-audio-tracks";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export type SharingSource = { kind: "scan" | "review"; id: string };
export type PublicationJob = {
  ownerId: number | null; actorId: number | null; contributorId: string;
  operationId: string; contributionId: string; revisionId: string; artifactDigest: string;
  state: "pending" | "paused" | "published" | "rejected" | "cancelled" | "cancel_pending";
  phase: string; cancelRequested: boolean; errorCode: string | null;
  result: { committedAt: number; revisionId: string } | null;
};
export type SharingContext = {
  sourceOwnerId: number | null;
  name: string; mediaIdentity: MediaIdentity | null; durationSeconds: number | null;
  audioSources: { scanId: string; track: AudioTrack | null }[];
  sourceTerms: { authorId: string; revisionId: string; termsVersion: string }[];
  jobs: PublicationJob[]; terms: { version: string; text: string };
  accessVerified: boolean; accessError: string | null;
};
export type SharingFinding = {
  event_id: string; start_seconds: number; end_seconds: number;
  source?: "audio" | "visual" | "manual" | "derived";
  channel: string; category: string | null; subcategory: string | null; note: string | null;
  manual?: { added: boolean | null; edited: boolean | null } | null;
};
export type SharingPreview = {
  previewId: string; expiresAt: number; terms: { version: string; text: string };
  preview: {
    artifact: {
      name: string; kind: string; submission_id: string; source_revision: number;
      author: { contributor_id: string; key_id: string };
      audio_association: string;
      analyzed_audio: { language: string | null; description: string | null } | null;
      matching?: { media: MediaIdentity };
      findings: SharingFinding[];
      coverage: { channel: string; state: string }[];
    };
    manifest: { summary: { total: number } }; artifact_digest: string; manifest_digest: string;
    exclusions: { event_id: string; reason: string; channel?: string }[]; warnings: string[];
  };
};
export type PublicMediaReference = {
  provider: IdentityProvider; entity_type: EntityType; reference: string;
  series_reference?: string; episode_numbers?: EpisodeNumber[];
};
export type SharingDraft = {
  name: string; identity_confirmed: boolean; media: PublicMediaReference | null;
  audio_source_scan_id: string | null;
};

async function request<Result>(path: string, init?: RequestInit): Promise<Result> {
  let response: Response;
  try { response = await apiRequest(`${runtimeApiBasePath()}/community/${path}`, init); }
  catch (reason) {
    if (reason instanceof Error && reason.name === "AbortError") throw reason;
    throw new CommunityApiError("network_unavailable", true, 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new CommunityApiError(typeof body?.code === "string" ? body.code : "remote_error",
    body?.retryable === true, response.status);
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new CommunityApiError("invalid_response", false, response.status);
  return body as Result;
}
const post = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const sourcePath = (source: SharingSource) => `sharing/sources/${source.kind}/${encodeURIComponent(source.id)}`;
export const fetchSharingContext = (source: SharingSource, signal?: AbortSignal) => request<SharingContext>(sourcePath(source), { signal });
export const prepareSharingPreview = (source: SharingSource, draft: SharingDraft, signal?: AbortSignal) => request<SharingPreview>(`${sourcePath(source)}/preview`, { ...post(draft), signal });
export const approveSharingPreview = (previewId: string, signal?: AbortSignal) => request<PublicationJob>(`sharing/previews/${encodeURIComponent(previewId)}/approve`, { ...post({ confirmed: true }), signal });
export const discardSharingPreview = (previewId: string, signal?: AbortSignal) => request<{ discarded: boolean }>(`sharing/previews/${encodeURIComponent(previewId)}`, { method: "DELETE", signal });
export const checkPublication = (operationId: string, signal?: AbortSignal) => request<PublicationJob>(`publications/${encodeURIComponent(operationId)}/check`, { ...post({}), signal });
export const retryPublication = (operationId: string, signal?: AbortSignal) => request<PublicationJob>(`publications/${encodeURIComponent(operationId)}/retry`, { ...post({ confirmed: true }), signal });
export const cancelPublication = (operationId: string, signal?: AbortSignal) => request<PublicationJob>(`publications/${encodeURIComponent(operationId)}`, { method: "DELETE", signal });
