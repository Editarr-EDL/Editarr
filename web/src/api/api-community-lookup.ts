import type { AudioTrackSelection } from "./api-audio-tracks";
import type { MediaIdentity } from "./api-media-identity";
import { CommunityApiError } from "./api-community";
import { apiRequest } from "./api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

export const LOOKUP_POLICY = "community-compatibility-stream-1";
export const RANKING_POLICY = "community-ranking-wilson-1";
export type CommunityRanking = {
  policyVersion: typeof RANKING_POLICY; qualityScore: number; voteCount: number; uploaderBoost: 0; score: number;
};
export type MatchBand = "exact_file" | "strong_fingerprint" | "release_match" | "possible_cut" | "title_only" | "incompatible";
export type LookupError = { code: string; retryable: boolean };
export type LookupTarget = {
  source: "jellyfin";
  item_id: string;
  media_source_id?: string | null;
};
export type LookupInput = { target: LookupTarget; audio_selection?: AudioTrackSelection | null };
export type StreamEvidence = {
  algorithm: string; digest: string; playback_start: string | null; duration: string | null;
  provenance: "selected" | "analyzed" | "unknown"; description: string | null;
};
export type MatchEvidence = {
  media: MediaIdentity;
  fingerprints: { algorithm: string; digest: string; size_bytes: number }[];
  video_stream: StreamEvidence | null;
  audio_stream: StreamEvidence | null;
};
export type ChannelCompatibility = {
  band: MatchBand; reasons: string[]; conflicts: string[]; missing: string[]; strong_eligible: boolean;
};
export type RemoteFilterCandidate = {
  contributionId: string;
  revisionId: string; artifactDigest: string; artifactSchema: "community-artifact-1" | "community-artifact-2";
  releaseId: string; catalogCreatedAt: number;
  attribution: { contributorId: string; displayName: string };
  summary: {
    schema_version: "community-summary-1" | "community-summary-2"; submission_id: string;
    registry_version: "mcf-1.1.0"; work_ids: { provider: string; entity_type: string; value: string }[];
    release: string | null; duration_seconds: number;
    kind: "scan" | "manual" | "derived"; name?: string | null;
    release_version: string | null; scanner_behavior_version: string | null;
    total: number; channels: Partial<Record<"audio" | "video", number>>;
    sources: Record<string, number>; taxonomy: Record<string, number>;
    coverage: { channel: "audio" | "video" | "both"; capability: string; state: string;
      intervals: { start_seconds: number; end_seconds: number }[] }[];
    matching?: MatchEvidence | null; audio_association?: "none" | "unknown" | "identified" | null;
    source_revision?: number | null;
    analyzed_audio?: { provenance: "analyzed"; language: string | null; description: string | null;
      role: "main" | "commentary" | "descriptive" | "unknown"; codec: string | null; channels: number | null;
      stream_start_seconds: number | null; stream_duration_seconds: number | null; time_base: string | null } | null;
    source_states?: { source_id: string; origin: "generated" | "manual" | "imported";
      status: "complete" | "failed" | "cancelled" }[] | null;
  };
  evidence: MatchEvidence;
  compatibility: { policy_version: string; video: ChannelCompatibility; audio: ChannelCompatibility; selected: false };
  statistics: { imports: number; likes: number; dislikes: number }; ranking: CommunityRanking | null;
};
export type LookupAvailability = {
  version: 1; policyVersion: string; items: [{ id: "target"; status: "ok" | "missing_identity";
    candidateCount: number | null; truncated: boolean;
    channels: Record<"audio" | "video", Record<MatchBand, number>> | null }];
};
export type LookupResponse = {
  version: 1; policyVersion: string; targetKey: string; targetEvidence: MatchEvidence; warnings: string[];
  availability: LookupAvailability | null; availabilityError: LookupError | null;
  page: { version: 1; policyVersion: string; candidates: RemoteFilterCandidate[]; nextCursor: string | null } | null;
  pageError: LookupError | null;
};

function validRanking(candidate: RemoteFilterCandidate): boolean {
  const value = candidate?.ranking;
  if (value === null) return true;
  return !!value && Object.keys(value).sort().join(",") === "policyVersion,qualityScore,score,uploaderBoost,voteCount"
    && value.policyVersion === RANKING_POLICY && Number.isFinite(value.qualityScore)
    && value.qualityScore >= 0 && value.qualityScore <= 1 && value.score === value.qualityScore
    && value.uploaderBoost === 0 && Number.isSafeInteger(value.voteCount) && value.voteCount >= 0
    && Number.isSafeInteger(candidate.statistics?.likes) && candidate.statistics.likes >= 0
    && Number.isSafeInteger(candidate.statistics?.dislikes) && candidate.statistics.dislikes >= 0
    && value.voteCount === candidate.statistics.likes + candidate.statistics.dislikes;
}

export async function lookupCommunityFilters(input: LookupInput, options: {
  signal: AbortSignal; refresh?: boolean; cursor?: string | null; targetKey?: string | null;
}): Promise<LookupResponse> {
  let response: Response;
  try {
    response = await apiRequest(`${runtimeApiBasePath()}/community/lookup`, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: options.signal,
      body: JSON.stringify({ ...input, refresh: options.refresh || false, cursor: options.cursor || null,
        target_key: options.targetKey || null }),
    });
  } catch (error) {
    if (options.signal.aborted) throw error;
    throw new CommunityApiError("network_unavailable", true, 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new CommunityApiError(typeof body?.code === "string" ? body.code : "remote_error",
    body?.retryable === true, response.status);
  if (body?.policyVersion && body.policyVersion !== LOOKUP_POLICY) throw new CommunityApiError("unsupported_policy", false, response.status);
  const validError = (value: unknown) => value === null || (typeof value === "object" && value !== null
    && "code" in value && typeof value.code === "string" && "retryable" in value && typeof value.retryable === "boolean");
  if (body?.version !== 1 || body?.policyVersion !== LOOKUP_POLICY || !/^[a-f0-9]{64}$/.test(body?.targetKey)
    || !Array.isArray(body?.warnings) || body.warnings.some((warning: unknown) => typeof warning !== "string")
    || !body?.targetEvidence?.media || !Array.isArray(body.targetEvidence.fingerprints)
    || !validError(body.availabilityError) || !validError(body.pageError)
    || !(body.availability === null || (body.availability?.version === 1 && body.availability.policyVersion === LOOKUP_POLICY
      && body.availability.items?.length === 1 && body.availability.items[0].id === "target"))
    || (body.page === null && !body.availabilityError && !body.pageError)
    || (body.page !== null && (body.page?.version !== 1 || body.page.policyVersion !== LOOKUP_POLICY
      || !(body.page.nextCursor === null || typeof body.page.nextCursor === "string")
      || !Array.isArray(body.page.candidates) || body.page.candidates.some((candidate: RemoteFilterCandidate) =>
        !/^[A-Za-z0-9_.-]{1,128}$/.test(candidate?.contributionId || "")
        || !validRanking(candidate)
        || candidate?.compatibility?.selected !== false || candidate.compatibility.policy_version !== LOOKUP_POLICY)))) {
    throw new CommunityApiError("invalid_response", false, response.status);
  }
  return body as LookupResponse;
}