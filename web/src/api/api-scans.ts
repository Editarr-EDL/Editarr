import type {
  ScanQueueItem,
  Scan,
  ScanFindings,
  FilterCandidateSearchResult,
  ScanReferences,
  ScanOptions,
} from "./api-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { notifyScansChanged } from "../shared/scan-events";
import { apiRequest, responseError } from "./api-transport";
const fetch = apiRequest;

export async function enqueueScan(
  jellyfinItemId: string,
  options: ScanOptions,
  mediaSourceId?: string | null,
): Promise<ScanQueueItem> {
  const response = await fetch(`${runtimeApiBasePath()}/scans`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jellyfin_item_id: jellyfinItemId,
      ...(mediaSourceId ? { media_source_id: mediaSourceId } : {}),
      ...options,
    }),
  });
  if (!response.ok) throw await responseError(response, "Unable to add scan to queue");
  notifyScansChanged();
  return response.json();
}

export async function renameScan(scanId: string, name: string | null): Promise<Scan> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/${encodeURIComponent(scanId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw await responseError(response, "Unable to rename scan");
  notifyScansChanged();
  return response.json();
}

export async function fetchScanQueue(): Promise<ScanQueueItem[]> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/queue`);
  if (!response.ok) throw await responseError(response, "Unable to load Editarr queue");
  return response.json();
}

export async function reorderScanQueue(queueIds: number[]): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/queue/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ queue_ids: queueIds }),
  });
  if (!response.ok) throw await responseError(response, "Unable to reorder Editarr queue");
  notifyScansChanged();
}

export async function cancelScanQueueItem(queueId: number): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/queue/${queueId}`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Unable to cancel Editarr scan");
  notifyScansChanged();
}

export async function resumeOrphanedScan(queueId: number): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/queue/${queueId}/resume`, { method: "POST" });
  if (!response.ok) throw await responseError(response, "Unable to resume orphaned Editarr scan");
  notifyScansChanged();
}

export async function fetchScanHistory(): Promise<Scan[]> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/history`);
  if (!response.ok) throw await responseError(response, "Unable to load Editarr history");
  return response.json();
}

export async function fetchAssociatedScans(itemId: string): Promise<Scan[]> {
  const path = `${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/scans?associated_only=true`;
  const response = await fetch(path);
  if (!response.ok) throw await responseError(response, "Unable to load linked scans");
  return response.json();
}

export async function fetchScanFindings(scanId: string): Promise<ScanFindings> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/${encodeURIComponent(scanId)}/findings`);
  if (!response.ok) throw await responseError(response, "Unable to load scan findings");
  return response.json();
}

export async function deleteScan(scanId: string): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/${encodeURIComponent(scanId)}`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Unable to delete scan");
  notifyScansChanged();
}

export async function fetchScanReferences(scanId: string): Promise<ScanReferences & { has_hidden_references?: boolean }> {
  const response = await fetch(`${runtimeApiBasePath()}/scans/${encodeURIComponent(scanId)}/references`);
  if (!response.ok) throw await responseError(response, "Unable to load scan references");
  return response.json();
}

/** Find completed scans with filters that can be associated with a media item.
 * @param search Media identity and optional source path.
 */
export async function findFilterCandidates(search: {
  source: "jellyfin";
  item_id: string;
  media_source_id?: string | null;
  library_id: string | null;
  source_path?: string | null;
  item_name: string;
  item_type: string;
  duration_seconds: number | null;
  scene_name: string | null;
  release_group: string | null;
  media_format: string | null;
  media_provider_id: string | null;
}, signal?: AbortSignal): Promise<FilterCandidateSearchResult> {
  const response = await fetch(`${runtimeApiBasePath()}/filter-candidates`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(search),
  });
  if (!response.ok) throw await responseError(response, "Unable to find matching filters");
  return response.json();
}

export async function associateFilterCandidates(mediaItemId: string, scanIds: string[]): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/filter-candidates/associations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_item_id: mediaItemId, scan_ids: scanIds }),
  });
  if (!response.ok) throw await responseError(response, "Unable to associate filters");
}
