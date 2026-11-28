/** Review editing, synchronization, and import requests. */

import type { ExportFormat, ExportPublication, ExportStatus, McfCategory, McfCategoryFamily, McfChannel, McfSourceVersion, McfWorkType, Severity } from "./api-mcf-types";
import type { Scan } from "./api-scan-types";

export type ReviewSource = "jellyfin";
export type ReviewScanRole = "audio" | "video" | "both";
export type ReviewFilterSource = "audio" | "visual";
export type ReviewEditAction = "mute" | "skip";
export type ScanMode = "content" | "credits_detection";
export type SynchronizationEndMode = "before_credits" | "file_end";
export type CreditsDetectionMethod = "chapters" | "visual_text" | "hybrid" | "hybrid_qwen";
export type CreditsEvidenceKind = "chapter_marker" | "scene_boundary" | "text_density" | "credit_motion" | "black_frame" | "qwen";
export type CreditsConfidenceBand = "low" | "medium" | "high";

export interface ReviewSynchronization {
  start_seconds: number;
  end_seconds: number;
  end_mode: SynchronizationEndMode;
}

export interface AcceptedCreditsReference {
  scan_id: string;
  candidate_id: string;
  accepted_boundary_seconds: number;
}

export interface AcceptedCreditsProvenance extends AcceptedCreditsReference {
  source_finding_id: number;
  detected_boundary_seconds: number;
  confidence: number;
  method: CreditsDetectionMethod;
  accepted_at: string;
}

export interface ReviewTarget {
  source: ReviewSource;
  item_id: string;
  media_source_id: string | null;
}

export interface ReviewScanMembership {
  scan_id: string;
  role: ReviewScanRole;
}

/** How a stored snapshot compares with its master link's served filters now. */
export type ReviewCommunitySourceState = "current" | "changed" | "alignment_required" | "link_unavailable";

/** A snapshot of one master link's served filters; its filters are normal review filters. */
export interface ReviewCommunitySource {
  source_id: string;
  link_id: string | null;
  community_version_id: string;
  label: string;
  tier: string | null;
  match_percent: number | null;
  master_revision: number;
  alignment: Record<string, unknown> | null;
  captured_at: string;
  state: ReviewCommunitySourceState;
  link_state: string | null;
  /** The filters the master served when the snapshot was taken. */
  filter_count: number;
}

/** Read-only provenance of a review filter from a community snapshot. */
export interface ReviewFilterCommunity {
  source_id: string;
  filter_id: string;
  /** The community version's label. */
  label: string;
  master_revision: number;
  confidence: number | null;
  contributor_count: number | null;
  low_confidence: boolean;
  /** The master no longer served it at the last refresh. */
  removed: boolean;
  /** Its own bounds overlap a finding of the same category from a local model scan. */
  locally_verified: boolean;
}

/** Keep a stored snapshot (`source_id`), take a new one from it (`refresh`), or include a link (`link_id`). */
export type ReviewCommunityMembership =
  | { source_id: string; refresh?: boolean; link_id?: never }
  | { link_id: string; source_id?: never; refresh?: never };

export interface ReviewScanReference {
  scan: Scan;
  role: ReviewScanRole;
}

export interface ReviewFilter {
  id: string;
  /** The scan a scan filter came from; null for a community filter. */
  source_scan_id: string | null;
  source_finding_id: number | null;
  /** The included community snapshot and master filter a community filter came from. */
  community_source_id?: string | null;
  community_filter_id?: string | null;
  /** A community filter the user edited directly; bulk changes such as Apply Filter Defaults leave it unset. */
  community_edited?: boolean | null;
  /** Read-only community provenance; the server ignores it on save. */
  community?: ReviewFilterCommunity | null;
  source: ReviewFilterSource;
  category: McfCategoryFamily | null;
  subcategory: McfCategory | null;
  severity: Severity | null;
  mcf_channel: McfChannel | null;
  mcf_comment: string | null;
  label: string | null;
  details: string | null;
  group_key: string | null;
  start_seconds: number;
  end_seconds: number | null;
  confidence: number | null;
  edit_action: ReviewEditAction;
  selected: boolean;
  /** Read-only: the label differs from the source scan finding's. Absent in older responses; saves may echo it. */
  label_edited?: boolean;
}

export interface McfMetadata {
  title: string | null;
  year: number | null;
  work_type: McfWorkType | null;
  season: number | null;
  episode: number | null;
  source_url: string | null;
  imdb_url: string | null;
  release: string | null;
  comment: string | null;
}
export interface ReviewMcfDocument extends McfMetadata { material_start_seconds: number | null; material_end_seconds: number | null; }
export interface ScanMcfDocument extends McfMetadata { source_version: McfSourceVersion; material_start_seconds: number; material_end_seconds: number; }
export type McfMetadataSeedRequest = { kind: "scan"; scan_id: string } | { kind: "catalog" };
export interface McfMetadataSeedCandidate { kind: "scan" | "catalog"; scan_id: string | null; label: string; mcf_document: ReviewMcfDocument; }

export interface ReviewRepairSummary {
  message: string;
  missing_scan_ids: string[];
  detected_at: string;
  recover: { kept_cues: number; removed_memberships: number; removed_accepted_credits: number };
}

export interface SavedReviewSummary {
  id: string;
  media_item_id: string;
  target: ReviewTarget;
  name: string;
  revision: number;
  content_revision: number;
  edl_content_revision: number;
  mcf_content_revision: number;
  created_at: string;
  updated_at: string;
  exports: ExportStatus;
  repair: ReviewRepairSummary | null;
}

export interface ReviewPolicyToken {
  policy_owner_id?: number | null;
  policy_revision?: number;
}

export interface SavedReview extends SavedReviewSummary, ReviewPolicyToken {
  owner_id?: number | null;
  original_author_id?: number | null;
  policy_required_filter_ids?: string[];
  policy_locked_severities?: Record<string, Severity>;
  memberships: ReviewScanMembership[];
  scans: ReviewScanReference[];
  filters: ReviewFilter[];
  base_filters?: ReviewFilter[];
  policy_missing_evidence?: ReviewFilter[];
  mcf_document: ReviewMcfDocument;
  synchronization: ReviewSynchronization;
  accepted_credits: AcceptedCreditsProvenance | null;
  community_sources?: ReviewCommunitySource[];
}

export interface ReviewCreate {
  name: string;
  memberships?: ReviewScanMembership[];
  community_memberships?: ReviewCommunityMembership[];
  community_drafts?: ReviewCommunityDraft[];
  filters?: ReviewFilterInput[] | null;
  new_manual_filters?: NewManualReviewFilter[];
  mcf_metadata_seed?: McfMetadataSeedRequest | null;
}

export interface ReviewFilterInput extends ReviewFilter {}

export interface ReviewCommunityDraft {
  link_id: string;
  token: string;
  filters: ReviewFilterInput[];
}

export interface NewManualReviewFilter {
  id: string;
  source: ReviewFilterSource;
  category: McfCategoryFamily;
  subcategory: McfCategory;
  severity: Severity | null;
  mcf_channel: McfChannel | null;
  mcf_comment: string | null;
  label: string | null;
  details: string | null;
  group_key: string | null;
  start_seconds: number;
  end_seconds: number;
  confidence?: number | null;
  edit_action: ReviewEditAction;
  selected?: boolean;
}

export interface ReviewSave extends ReviewPolicyToken {
  filter_basis?: "base" | "effective";
  revision: number;
  name: string;
  memberships: ReviewScanMembership[];
  filters: ReviewFilterInput[];
  mcf_document: ReviewMcfDocument;
  synchronization: ReviewSynchronization;
  accepted_credits?: AcceptedCreditsReference | null;
  new_manual_filters?: NewManualReviewFilter[];
  community_memberships?: ReviewCommunityMembership[];
  community_drafts?: ReviewCommunityDraft[];
}

export interface ReviewRetimeRequest extends ReviewPolicyToken {
  filter_basis?: "base" | "effective";
  revision: number;
  memberships: ReviewScanMembership[];
  current_synchronization: ReviewSynchronization;
  proposed_synchronization: ReviewSynchronization;
  mcf_document: ReviewMcfDocument;
  filters: ReviewFilterInput[];
  accepted_credits: AcceptedCreditsReference | null;
  new_manual_filters: NewManualReviewFilter[];
}

export interface ReviewRetimeResult extends ReviewPolicyToken {
  synchronization: ReviewSynchronization;
  mcf_document: ReviewMcfDocument;
  filters: ReviewFilterInput[];
  base_filters?: ReviewFilterInput[];
  accepted_credits: AcceptedCreditsReference | null;
  new_manual_filters: NewManualReviewFilter[];
}

export interface ReviewRename { revision: number; name: string; }
export interface ReviewDuplicate extends ReviewPolicyToken { revision: number; name: string; }
export interface ReviewExportRequest extends ReviewPolicyToken { revision: number; }
export type ReviewFileChoice = "retain" | "remove";
export interface ReviewDeletionFile { publication: ExportPublication; choices: ReviewFileChoice[]; }
/** Files the caller may see for a review deletion; `confirmation` binds the whole preview. */
export interface ReviewDeletionPreview {
  review_id: string;
  revision: number;
  confirmation: string;
  files: ReviewDeletionFile[];
  legacy_formats: ExportFormat[];
  scan_ids: string[];
}
export interface ReviewDeletionRequest { revision: number; confirmation: string; files: { export_id: number; choice: ReviewFileChoice }[]; }
export interface ReviewDeletionResult { review_id: string; retained: number[]; removed: number[]; }
export interface McfMetadataNotice { code: "metadata_mismatch"; fields: string[]; mcf_metadata: ReviewMcfDocument; catalog_metadata: ReviewMcfDocument; }
export interface McfImportResult { review: SavedReview | null; scan: Scan; source_version: McfSourceVersion; notices: McfMetadataNotice[]; }
export interface McfImportIssue { code: string; message: string; line_number: number; line_text: string; }
export type McfPreflightIssueCode = "missing_category" | "missing_subcategory" | "missing_severity" | "invalid_category" | "invalid_subcategory" | "invalid_category_pair" | "invalid_severity" | "invalid_mcf_channel" | "invalid_mcf_comment" | "invalid_mcf_metadata" | "invalid_material_bounds";
export interface McfPreflightIssue { code: McfPreflightIssueCode; field: string; filter_id: string | null; filter_position: number | null; message: string; }
export interface McfPreflight { review_id: string; revision: number; eligible: boolean; issues: McfPreflightIssue[]; }
export interface EdlImportWarning {
  code: "unsupported_actions_ignored";
  message: string;
  ignored_action_count: number;
}
export interface EdlImportResult { review: SavedReview | null; scan: Scan; warning: EdlImportWarning | null; }
export interface VideoSkipImportResult {
  review: null;
  scan: Scan;
  issue_count: number;
  issues: { code: string; message: string; line_number: number }[];
}
export interface ReviewReference { id: string; name: string; updated_at: string; }
export interface ScanReferences { scan_id: string; active_exports: ExportFormat[]; reviews: ReviewReference[]; community_in_progress: boolean; }
export interface ReviewErrorDetail {
  code: string;
  message: string;
  reason?: string | null;
  repair: ReviewRepairSummary | null;
  line_number: number | null;
  line_text: string | null;
  references: unknown | null;
  filter_ids: string[];
  document_fields: string[];
  metadata_candidates: McfMetadataSeedCandidate[];
  import_issues: McfImportIssue[];
}
export interface ReviewTargetRequest { source: ReviewSource; item_id: string; media_source_id?: string | null; stable_version_id?: string; }
export interface EdlImportOptions { target: ReviewTargetRequest; name: string; stage_for_verification?: boolean }
export interface VideoSkipImportOptions {
  target: ReviewTargetRequest;
  name: string;
  attribution: { source_url: string | null; uploader: string | null };
}
export interface McfImportOptions {
  target: ReviewTargetRequest;
  name: string;
  ignore_errors?: boolean;
  stage_for_verification?: boolean;
  synchronization: ReviewSynchronization;
  mcf_material_end_seconds: number | null;
  accepted_credits?: AcceptedCreditsReference | null;
}
