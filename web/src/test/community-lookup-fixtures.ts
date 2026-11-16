import type { LookupResponse, RemoteFilterCandidate } from "../api/api-community-lookup";

export const mixedCandidate: RemoteFilterCandidate = {
  contributionId: "mixed-root",
  revisionId: "mixed", artifactDigest: "a".repeat(64), artifactSchema: "community-artifact-2", releaseId: "release", catalogCreatedAt: Date.parse("2026-10-04T00:00:00Z"),
  attribution: { contributorId: "author", displayName: "Public Author" },
  summary: { schema_version: "community-summary-2", registry_version: "mcf-1.1.0", submission_id: "mixed", kind: "derived",
    name: "Mixed review", work_ids: [], release: null, duration_seconds: 120, release_version: null, scanner_behavior_version: null,
    total: 3, channels: { video: 2, audio: 1 }, sources: { derived: 3 }, taxonomy: { "language/swearing": 1 }, coverage: [] },
  evidence: { media: { entity_type: "movie", identifiers: [], series_identifiers: [], episode_numbers: [], episode_span: [], input_requirement: null,
    release: { scene_name: null, group: null, edition: null, quality: null, size_bytes: null, duration: null, audio: null } },
    fingerprints: [], video_stream: null, audio_stream: null },
  compatibility: { policy_version: "community-compatibility-stream-1", selected: false,
    video: { band: "strong_fingerprint", strong_eligible: true, reasons: ["video_stream_digest_match"], conflicts: [], missing: [] },
    audio: { band: "possible_cut", strong_eligible: false, reasons: [], conflicts: [], missing: ["analyzed_audio_stream"] } },
  statistics: { imports: 0, likes: 0, dislikes: 0 }, ranking: null,
};

export function communityResponse(candidates = [mixedCandidate]): LookupResponse {
  return { version: 1, policyVersion: "community-compatibility-stream-1", targetKey: "a".repeat(64), targetEvidence: mixedCandidate.evidence,
    warnings: [], availability: null, availabilityError: null, pageError: null,
    page: { version: 1, policyVersion: "community-compatibility-stream-1", candidates, nextCursor: null } };
}