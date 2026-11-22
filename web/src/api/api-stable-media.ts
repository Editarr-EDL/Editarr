import type { Scan } from "./api-scan-types";
import type { JellyfinMediaItem } from "./api-media-types";
import { notifyScansChanged } from "../shared/scan-events";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { jsonApi } from "./api-transport";

export interface HistoricalVersion {
  id: string;
  jellyfin_item_id: string;
  media_source_id: string | null;
  label: string;
  available: boolean;
  active_item_id?: string | null;
  active_media_source_id?: string | null;
  scans: Scan[];
  reviews: { id: string; name: string }[];
}

export interface EditarrItem {
  title_id: string;
  name: string;
  active_item: JellyfinMediaItem | null;
  active_items: JellyfinMediaItem[];
  versions: HistoricalVersion[];
  current_versions?: { item_id: string; media_source_id: string | null; version_id: string }[];
  selected_version_id: string | null;
  choose_version: boolean;
}

type Channel = "audio" | "video";

export interface AlignmentAnchor {
  source_seconds: number;
  target_seconds: number;
  segment?: number;
}

export interface VerificationRequest {
  source_scan_id: string;
  target: { source: "jellyfin"; item_id: string; media_source_id: string | null };
  channels: Channel[];
  audio_track_index?: number;
  anchors?: AlignmentAnchor[];
  method: "original" | "manual" | "transcript";
}

export interface VerificationCue {
  source_finding_id: number;
  channel: Channel | "both";
  source_start: number;
  source_end: number | null;
  target_start: number | null;
  target_end: number | null;
}

export interface VerificationPreview {
  source_scan_id: string;
  version_id: string;
  token: string | null;
  target_generation?: string | null;
  verified_channels: Channel[];
  exact: boolean;
  issues: string[];
  cues: VerificationCue[];
  anchors: AlignmentAnchor[];
}

function stableItemPath(titleId: string) {
  return `${runtimeApiBasePath()}/editarr-items/${encodeURIComponent(titleId)}`;
}

export function fetchEditarrItem(titleId: string, versionId?: string) {
  const query = versionId ? `?${new URLSearchParams({ version_id: versionId })}` : "";
  return jsonApi<EditarrItem>(`${stableItemPath(titleId)}${query}`, {}, "Unable to open this item");
}

export function resolveLegacyItem(itemId: string, libraryId: string) {
  const query = new URLSearchParams({ library_id: libraryId });
  const path = `${runtimeApiBasePath()}/editarr-items/legacy/${encodeURIComponent(itemId)}?${query}`;
  return jsonApi<EditarrItem>(path, {}, "Unable to resolve this bookmark");
}

function post<T>(path: string, value: unknown, failure: string, signal?: AbortSignal) {
  return jsonApi<T>(path, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  }, failure);
}

export function identifyEditarrVersion(titleId: string, target: VerificationRequest["target"]) {
  return post<{ version_id: string }>(`${stableItemPath(titleId)}/versions`, target, "Unable to verify the selected source file");
}

export function previewScanReuse(versionId: string, request: VerificationRequest, signal?: AbortSignal) {
  const path = `${runtimeApiBasePath()}/media-versions/${encodeURIComponent(versionId)}/reuse/preview`;
  return post<VerificationPreview>(path, request, "Unable to align this scan", signal);
}

export function fetchVerificationTranscriptStatus(request: VerificationRequest, signal: AbortSignal) {
  const path = `${runtimeApiBasePath()}/media-versions/current/reuse/transcript-status`;
  return post<{ ready: boolean }>(path, request, "Unable to check the target transcript", signal);
}

export interface FingerprintQueueItem {
  id: string;
  item_name: string;
  version_name: string | null;
  item_id: string;
  media_source_id: string | null;
  source_scan_id: string;
  bytes_read: number;
  bytes_total: number;
  reading: boolean;
}

const pendingApprovals = new Map<string, Promise<{ scan: Scan; reused: boolean }>>();

export function hasPendingFingerprintApproval() {
  return pendingApprovals.size > 0;
}

export function fetchFingerprintQueue() {
  return jsonApi<FingerprintQueueItem[]>(`${runtimeApiBasePath()}/media-fingerprints/queue`, {},
    "Unable to load fingerprint activity");
}

export function approveScanReuse(versionId: string, request: VerificationRequest, token: string) {
  const pending = pendingApprovals.get(token);
  if (pending) return pending;
  const path = `${runtimeApiBasePath()}/media-versions/${encodeURIComponent(versionId)}/reuse/approve`;
  const approval = post<{ scan: Scan; reused: boolean }>(
    path, { ...request, token, confirmed: true }, "Unable to approve these cues",
  ).finally(() => {
    pendingApprovals.delete(token);
    notifyScansChanged();
  });
  pendingApprovals.set(token, approval);
  notifyScansChanged();
  return approval;
}
