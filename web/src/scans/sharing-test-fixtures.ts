import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import type { PublicationJob, SharingContext, SharingPreview } from "../api/api-community-sharing";

export function sharingPreviewFixture(kind = "ai"): SharingPreview {
  const file = kind === "tracked" ? "donation-artifact-filter-details.json" : "donation-artifacts-v2.json";
  const vectors = JSON.parse(readFileSync("../tests/fixtures/community/" + file, "utf8"));
  const encoded = vectors.cases[kind];
  const artifact = JSON.parse(gunzipSync(Buffer.from(encoded.artifact, "base64")).toString("utf8"));
  const manifest = JSON.parse(Buffer.from(encoded.manifest, "base64").toString("utf8"));
  return {
    previewId: `preview-${kind}`, expiresAt: 1900000000,
    terms: { version: artifact.sharing_terms, text: "Contributions and public notes may be copied, shared and modified with original attribution. Authors cannot withdraw contributions; operators may moderate availability." },
    preview: { artifact, manifest, artifact_digest: manifest.artifact_digest, manifest_digest: "b".repeat(64),
      exclusions: ["derived", "retimed"].includes(kind) ? [{ event_id: "event-2", reason: "other_audio_track", channel: "audio" }] : [],
      warnings: artifact.audio_association === "unknown" ? ["audio_track_unknown"] : [] },
  };
}
export function sharingContextFixture(kind = "ai"): SharingContext {
  const preview = sharingPreviewFixture(kind);
  const identity = preview.preview.artifact.matching?.media ?? null;
  return { sourceOwnerId: 1, name: preview.preview.artifact.name, mediaIdentity: identity, durationSeconds: 10,
    audioSources: [{ scanId: "scan-a", track: { stream_index: 1, language: "en", title: "English actually analyzed", codec: "aac", channels: 2,
      disposition: 1, time_base: "1/48000", start_seconds: 0, duration_seconds: 10 } }],
    sourceTerms: [], jobs: [], terms: preview.terms, accessVerified: true, accessError: null };
}
export const pendingPublication: PublicationJob = {
  ownerId: 1, actorId: 1, contributorId: "original-contributor",
  operationId: "publish-original",
  artifactDigest: "a".repeat(64), state: "pending", phase: "awaiting_send", cancelRequested: false,
  errorCode: null, result: null,
};
