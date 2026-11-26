import { z } from 'zod';
import { DEFAULT_ARTIFACT_BOUNDS, SUBMISSION_ID, type ArtifactBounds } from '../artifact/v3';

/** Protocol maxima (§5.2); `SUBMISSION_LIMITS` may lower but never raise them. */
const PROTOCOL = { requestBytes: 1_500_000, ...DEFAULT_ARTIFACT_BOUNDS };
const bounded = (max: number) => z.number().int().min(1).max(max);

/** `SUBMISSION_LIMITS` (§8.1): the request, manifest and artifact bounds of §5.2. */
export const submissionLimitsSchema = z.strictObject({
  requestBytes: bounded(PROTOCOL.requestBytes), manifestBytes: bounded(PROTOCOL.manifestBytes),
  artifact: z.strictObject({ compressedBytes: bounded(PROTOCOL.compressedBytes), expandedBytes: bounded(PROTOCOL.expandedBytes),
    depth: bounded(PROTOCOL.depth), stringBytes: bounded(PROTOCOL.stringBytes) }),
});
export type SubmissionLimits = z.infer<typeof submissionLimitsSchema>;
export const DEFAULT_SUBMISSION_LIMITS: SubmissionLimits = {
  requestBytes: PROTOCOL.requestBytes, manifestBytes: PROTOCOL.manifestBytes,
  artifact: { compressedBytes: PROTOCOL.compressedBytes, expandedBytes: PROTOCOL.expandedBytes,
    depth: PROTOCOL.depth, stringBytes: PROTOCOL.stringBytes },
};

export function artifactBounds(limits: SubmissionLimits): ArtifactBounds {
  return { ...limits.artifact, manifestBytes: limits.manifestBytes };
}

/** The `POST /v1/submissions` body after canonical base64 decoding. */
export interface SubmissionRequest {
  submissionId: string; artifactBytes: Uint8Array; manifestBytes: Uint8Array; envelopeBytes: Uint8Array;
}

/** Decode canonical, padded standard base64 of 1..`limit` bytes, or throw `invalid_submission`. */
function decode(value: unknown, limit: number): Uint8Array {
  if (typeof value !== 'string' || !value || value.length > Math.ceil(limit / 3) * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new Error('invalid_submission');
  const decoded = atob(value);
  if (!decoded.length || decoded.length > limit || btoa(decoded) !== value) throw new Error('invalid_submission');
  return Uint8Array.from(decoded, character => character.charCodeAt(0));
}

/** Parse the strict §7.2 body `{version, submissionId, artifact, manifest, envelope}`. */
export function parseSubmissionRequest(value: unknown, limits: SubmissionLimits): SubmissionRequest {
  const input = value as Record<string, unknown> | null;
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      Object.keys(input).sort().join(',') !== 'artifact,envelope,manifest,submissionId,version' || input.version !== 1 ||
      typeof input.submissionId !== 'string' || !SUBMISSION_ID.test(input.submissionId)) throw new Error('invalid_submission');
  return { submissionId: input.submissionId, artifactBytes: decode(input.artifact, limits.artifact.compressedBytes),
    manifestBytes: decode(input.manifest, limits.manifestBytes), envelopeBytes: decode(input.envelope, 256) };
}
