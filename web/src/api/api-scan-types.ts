/** Scan records, findings, and options. */

import type { CreditsDetection } from "./api-credits-types";
import type { AudioTrackSelection } from "./api-audio-tracks";
import type { McfCategory, McfChannel, Severity } from "./api-mcf-types";
import type { ReviewEditAction, ReviewFilter, ReviewFilterSource, ScanMcfDocument, ScanMode } from "./api-review-types";

export interface ScanQueueItem extends Scan {
  queue_id: number;
  sort_order: number;
  progress_percent: number | null;
  progress_stage: string | null;
  progress_message: string | null;
}

export interface Scan {
  id: string;
  scan_id: number;
  is_own?: boolean | null;
  scan_mode: ScanMode;
  release_version?: string | null;
  scanner_behavior_version?: string | null;
  build_revision?: string | null;
  audio_model_revision?: string | null;
  video_model_revision?: string | null;
  stage_coverage?: ScanStageCoverage[];
  media_item_id: string;
  jellyfin_item_id: string;
  library_id: string | null;
  item_name: string;
  item_type: string;
  media_source_id: string | null;
  media_source_name: string | null;
  status: string;
  scan_audio: boolean;
  audio_model: string | null;
  audio_filter: string | null;
  requested_audio_selection?: AudioTrackSelection | null;
  analyzed_audio_selection?: AudioTrackSelection | null;
  scan_video: boolean;
  video_model: string | null;
  coarse_scan_interval: number | null;
  coarse_scan_enabled: boolean;
  qwen_model: string;
  qwen_summaries_per_segment: number;
  qwen_segment_join_gap_seconds: number;
  duration_seconds: number | null;
  video_hash: string | null;
  checkpoint_stage: string;
  error_message: string | null;
  finding_count: number;
  manual: boolean;
  edited: boolean;
  scan_started_at: string | null;
  scan_duration_seconds: number | null;
  created_at: string;
  updated_at: string;
  name: string | null;
  origin: "generated" | "manual" | "imported";
  import_source_name: string | null;
  imported_at: string | null;
  mcf_document: ScanMcfDocument | null;
  credits_detection: CreditsDetection | null;
  community_provenance?: { author_id: string; contribution_id: string; revision_id: string; digest: string; [key: string]: unknown } | null;
  community_approvals?: { source: "jellyfin"; target_item_id: string;
    media_source_id: string | null; channels: ("audio" | "video")[] }[];
}

export interface ScanFinding {
  id: number;
  scan_id: string;
  category: ReviewFilter["category"];
  subcategory: McfCategory | null;
  severity: Severity | null;
  mcf_channel: McfChannel | null;
  mcf_comment: string | null;
  start_seconds: number;
  end_seconds: number | null;
  confidence: number | null;
  label: string | null;
  details: string | null;
  group_key: string | null;
  source: ReviewFilterSource;
  selected: boolean;
  edit_action: ReviewEditAction;
}

export interface ScanFindings {
  revision: number;
  findings: ScanFinding[];
}

export interface ScanStageCoverage {
  stage: string;
  execution_id: string;
  status: "complete" | "partial" | "failed" | "disabled" | "unsupported" | "unknown";
  source: Scan["origin"];
  release_version: string | null;
  scanner_behavior_version: string | null;
  build_revision: string | null;
  model_id: string | null;
  model_revision: string | null;
  model_digest: string | null;
  quantization: string | null;
  effective_settings: Record<string, unknown> | null;
  intervals: { start_seconds: number; end_seconds: number }[] | null;
  sampling_seconds: number | null;
}

export interface ScanMatch {
  scan: Scan;
  match_reason: "scene_release" | "quick_hash" | "file_name" | "format";
  already_associated: boolean;
}

export interface FilterCandidateSearchResult {
  media_item_id: string;
  matches: ScanMatch[];
}

export interface ScanOptions {
  audio_selection?: AudioTrackSelection | null;
  name?: string | null;
  coarse_scan_interval?: number;
  coarse_scan_enabled: boolean;
  qwen_model: string;
  qwen_summaries_per_segment: number;
  qwen_segment_join_gap_seconds: number;
  scan_audio: boolean;
  audio_model: string | null;
  audio_filter: string | null;
  scan_video: boolean;
  video_model: string | null;
}
