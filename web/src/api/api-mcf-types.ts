/** MCF categories, filter defaults, and export status. */

import type { ReviewFilterSource, SavedReviewSummary } from "./api-review-types";

export type McfCategoryFamily = "commercial_content" | "discrimination" | "dispensable_scenes" | "drugs" | "fear" | "language" | "nudity" | "sex" | "violence";
export type McfCategory = string;
export type Severity = "low" | "medium" | "high";
export type McfChannel = "both" | "video" | "audio";
export type McfSourceVersion = "1.0.0" | "1.1.0";
export type McfWorkType = "movie" | "series" | "episode";
export type ExportFormat = "edl" | "mcf";

export interface McfCategoryOption { token: McfCategory; label: string; }
export interface McfCategoryFamilyOption { token: McfCategoryFamily; label: string; categories: McfCategoryOption[]; }
export interface McfRegistry { families: McfCategoryFamilyOption[]; }
export interface FilterSelectionRule {
  id: string;
  disposition: "allow" | "deny";
  evidence_source: ReviewFilterSource | null;
  category: McfCategoryFamily | null;
  subcategory: McfCategory | null;
  label_contains: string | null;
  severity: Severity | null;
  mcf_channel: McfChannel | null;
}
export interface McfSeverityDefault { subcategory: McfCategory; severity: Severity; }
export interface FilterDefaultsSettings { revision: number; selection_rules: FilterSelectionRule[]; severity_defaults: McfSeverityDefault[]; }

export interface ExportScanReference {
  id: string;
  scan_id: number;
  role: "audio" | "video" | "both";
  name: string | null;
  origin: "generated" | "manual" | "imported";
}

export interface ExportSummary {
  format: ExportFormat;
  present: boolean;
  exported_at: string | null;
  review_id: string | null;
  exported_format_revision: number | null;
  current: boolean;
  outdated: boolean;
  exported_scans: ExportScanReference[];
  exported_review: SavedReviewSummary | null;
}
export type ExportTarget = "self" | "review_owner" | "item_default";
export type RegenerationState = "idle" | "queued" | "running" | "succeeded" | "failed";

/** One canonical personal or item-default file as discovery reports it; never a path. */
export interface ExportPublication {
  id: number;
  publication_id: string;
  publication_revision: number;
  media_item_id: string;
  media_source_id: string;
  format: ExportFormat;
  scope: "personal" | "default";
  target: ExportTarget;
  target_user_id: number | null;
  target_export_id: string | null;
  actor_id: number | null;
  policy_owner_id: number | null;
  original_author_id: number | null;
  policy_revision: number;
  review_id: string | null;
  exported_format_revision: number | null;
  content_sha256: string | null;
  current: boolean;
  outdated: boolean;
  regeneration_state: RegenerationState;
  regenerating: boolean;
  failed: boolean;
  failure_reason: "write_failed" | null;
  retryable: boolean;
  serving: boolean;
  desired_policy_revision: number | null;
}
export interface ExportStatus {
  exports: Record<ExportFormat, ExportSummary>;
  legacy_exports?: Record<ExportFormat, ExportSummary>;
  publications?: ExportPublication[];
}
