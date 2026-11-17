/** Credit detection and acceptance payloads. */

import type { Scan } from "./api-scan-types";
import type { AcceptedCreditsReference, CreditsConfidenceBand, CreditsDetectionMethod, CreditsEvidenceKind, ReviewFilterInput, ReviewPolicyToken, ReviewScanMembership, ReviewTargetRequest } from "./api-review-types";

export interface CreditsDetectionConfiguration {
  detector_version: string;
  tail_window_seconds: number;
  frame_sample_interval_seconds: number;
  qwen_model: string | null;
}

export interface CreditsEvidence {
  kind: CreditsEvidenceKind;
  timestamp_seconds: number;
  score: number;
  detail_code: string;
  details: string | null;
}

export interface CreditsCandidate {
  id: string;
  boundary_seconds: number;
  range_start_seconds: number;
  range_end_seconds: number;
  confidence: number;
  confidence_band: CreditsConfidenceBand;
  method: CreditsDetectionMethod;
  evidence: CreditsEvidence[];
}

export interface CreditsDetectionResult {
  outcome: "candidate" | "no_candidate";
  candidates: CreditsCandidate[];
  best_candidate_id: string | null;
}

export interface CreditsDetection {
  configuration: CreditsDetectionConfiguration;
  detector_version: string;
  cache_key: string | null;
  result: CreditsDetectionResult | null;
  completed_at: string | null;
}

export interface CreditsModelConsent {
  backend_id: string;
  artifact_ids: string[];
  expected_size: number;
  availability: "available" | "unavailable";
  device: "cpu" | "cuda" | "coreml" | "metal" | "mlx" | "none";
  license_spdx: string | null;
  license_status: "declared" | "undeclared" | "license-review-required";
}

export interface CreditsDetectionStartRequest {
  target: ReviewTargetRequest;
  qwen_model?: string | null;
}

export interface CreditsDetectionStartResult {
  scan: Scan;
  queue_id: number;
  model_consent: CreditsModelConsent | null;
}

export type CreditsAcceptanceContext =
  | { kind: "import"; target: ReviewTargetRequest }
  | ({ kind: "review"; review_id: string; revision: number } & ReviewPolicyToken);

export interface CreditsAcceptanceRequest {
  context: CreditsAcceptanceContext;
  accepted_boundary_seconds: number;
}

export interface CreditsAcceptanceResult extends ReviewPolicyToken {
  accepted_credits: AcceptedCreditsReference;
  membership: ReviewScanMembership;
  filter: ReviewFilterInput;
  base_filter?: ReviewFilterInput;
}
