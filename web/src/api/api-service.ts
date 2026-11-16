import type {
  VersionInformation,
  RestartCapability,
  RestartAcknowledgement,
  DeploymentConfiguration,
  DeploymentUpdate,
  DiagnosticLogChannel,
  DiagnosticLogSettings,
  DiagnosticLogTail,
  ScanCapabilities,
  ModelStorageStatus,
  ModelActionResult,
  JellyfinSettings,
  JellyfinSettingsUpdate,
  McfRegistry,
  FilterDefaultsSettings,
  JellyfinLibrary,
  JellyfinMediaItem,
  JellyfinMediaPage,
  LibraryItemQuery,
  PathMapping,
  PathResolution,
  ReviewFilterInput,
} from "./api-types";
import { runtimeApiBasePath } from "../shared/runtime-urls";
import { apiRequest, responseError, jsonApi } from "./api-transport";
const fetch = apiRequest;

export async function fetchVersionInformation(): Promise<VersionInformation> {
  const response = await fetch(`${runtimeApiBasePath()}/system/version`);
  if (!response.ok) throw await responseError(response, "Unable to load version information");
  return response.json();
}

export async function fetchCapabilities(): Promise<ScanCapabilities> {
  const res = await fetch(`${runtimeApiBasePath()}/capabilities`);
  if (!res.ok) throw new Error("Unable to load scanning capabilities");
  return res.json();
}

export async function fetchModelStorage(): Promise<ModelStorageStatus> {
  const response = await fetch(`${runtimeApiBasePath()}/models`);
  if (!response.ok) throw await responseError(response, "Unable to load model storage");
  return response.json();
}

export async function prefetchModel(backend_id: string): Promise<ModelActionResult> {
  const response = await fetch(`${runtimeApiBasePath()}/models/prefetch`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backend_id }),
  });
  if (!response.ok) throw await responseError(response, "Unable to download model");
  return response.json();
}

export async function cancelModelDownload(artifactId: string): Promise<ModelActionResult> {
  const response = await fetch(`${runtimeApiBasePath()}/models/${encodeURIComponent(artifactId)}/cancel`, { method: "POST" });
  if (!response.ok) throw await responseError(response, "Unable to cancel model download");
  return response.json();
}

export async function removeModel(artifactId: string): Promise<ModelActionResult> {
  const response = await fetch(`${runtimeApiBasePath()}/models/${encodeURIComponent(artifactId)}`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Unable to remove model");
  return response.json();
}

export async function fetchDeploymentConfiguration(): Promise<DeploymentConfiguration> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/deployment`);
  if (!response.ok) throw await responseError(response, "Unable to load deployment settings");
  return response.json();
}

export async function saveDeploymentConfiguration(
  update: DeploymentUpdate,
): Promise<DeploymentConfiguration> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/deployment`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!response.ok) throw await responseError(response, "Unable to save deployment settings");
  return response.json();
}

/** Read restart state from the active runtime API or a same-origin reconnect API. */
export async function fetchRestartCapability(
  apiBasePath: string = runtimeApiBasePath(),
  options: { deferAuthenticationTransition?: boolean; signal?: AbortSignal } = {},
): Promise<RestartCapability> {
  // During a prefix transition, the new endpoint can reject the old path-scoped
  // session. Let the restart owner navigate first so the auth gate remounts at the
  // server-provided target instead of tearing down the old shell prematurely.
  const response = options.deferAuthenticationTransition
    ? await globalThis.fetch(`${apiBasePath}/system/restart`, { credentials: "same-origin", signal: options.signal })
    : await fetch(`${apiBasePath}/system/restart`, { signal: options.signal });
  if (!response.ok) throw await responseError(response, "Unable to read restart status");
  return response.json();
}

/** Ask the standalone controller to restart after this protected response completes. */
export async function requestEditarrRestart(signal?: AbortSignal): Promise<RestartAcknowledgement> {
  const response = await fetch(`${runtimeApiBasePath()}/system/restart`, { method: "POST", signal });
  if (!response.ok) throw await responseError(response, "Unable to restart Editarr");
  return response.json();
}

export async function fetchJellyfinSettings(): Promise<JellyfinSettings> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/jellyfin`);
  if (!response.ok) throw await responseError(response, "Unable to load Jellyfin settings");
  return response.json();
}

export async function testJellyfinSettings(
  settings: JellyfinSettingsUpdate,
): Promise<JellyfinSettings> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/jellyfin/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw await responseError(response, "Unable to connect to Jellyfin");
  return response.json();
}

export async function saveJellyfinSettings(
  settings: JellyfinSettingsUpdate,
): Promise<JellyfinSettings> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/jellyfin`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw await responseError(response, "Unable to save Jellyfin settings");
  return response.json();
}

export async function disconnectJellyfin(): Promise<JellyfinSettings> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/jellyfin`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Unable to disconnect Jellyfin");
  return response.json();
}

export function fetchMcfRegistry(): Promise<McfRegistry> {
  return jsonApi(`${runtimeApiBasePath()}/mcf-registry`, {}, "Unable to load MCF categories");
}

export function fetchFilterDefaultsSettings(): Promise<FilterDefaultsSettings> {
  return jsonApi(`${runtimeApiBasePath()}/settings/filters`, {}, "Unable to load filter settings");
}

export function updateFilterDefaultsSettings(settings: FilterDefaultsSettings, reauthentication?: { password: string }): Promise<FilterDefaultsSettings> {
  return jsonApi(`${runtimeApiBasePath()}/settings/filters`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...settings, ...(reauthentication ? { reauthentication } : {}) }) }, "Unable to save filter settings");
}

export function applyFilterDefaults(filters: ReviewFilterInput[]): Promise<{ filters: ReviewFilterInput[]; base_filters?: ReviewFilterInput[]; selection_changes: number; severity_changes: number; channel_changes: number }> {
  return jsonApi(`${runtimeApiBasePath()}/settings/filters/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filters }) }, "Unable to apply filter defaults");
}

export async function fetchDiagnosticLogSettings(): Promise<DiagnosticLogSettings> {
  const response = await apiRequest(`${runtimeApiBasePath()}/system/logs/settings`);
  if (!response.ok) throw await responseError(response, "Unable to load diagnostics settings");
  return response.json();
}

export async function saveDiagnosticLogSettings(
  settings: DiagnosticLogSettings,
): Promise<DiagnosticLogSettings> {
  const response = await apiRequest(`${runtimeApiBasePath()}/system/logs/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw await responseError(response, "Unable to save diagnostics settings");
  return response.json();
}

export async function fetchDiagnosticLogTail(
  channel: DiagnosticLogChannel,
  limit = 300,
): Promise<DiagnosticLogTail> {
  const response = await apiRequest(`${runtimeApiBasePath()}/system/logs/${channel}?limit=${limit}`);
  if (!response.ok) throw await responseError(response, "Unable to load logs");
  return response.json();
}

export async function fetchLibraries(): Promise<JellyfinLibrary[]> {
  const response = await fetch(`${runtimeApiBasePath()}/libraries`);
  if (!response.ok) throw await responseError(response, "Unable to load Jellyfin libraries");
  return response.json();
}

/** Fetch one filtered and paginated library page.
 * @param libraryId Jellyfin library identifier.
 * @param query Search, sort, and filter options.
 */
export async function fetchLibraryItems(
  libraryId: string,
  query: LibraryItemQuery = {},
): Promise<JellyfinMediaPage> {
  const params = new URLSearchParams({
    start_index: String(query.startIndex ?? 0),
    limit: String(query.limit ?? 100),
    sort_by: query.sortBy ?? "title",
    sort_direction: query.sortDirection ?? "asc",
    edl_filter: query.edlFilter ?? "all",
    rating_filter: query.ratingFilter ?? "all",
    resolution_filter: query.resolutionFilter ?? "all",
  });
  if (query.search?.trim()) params.set("search", query.search.trim());
  if (query.refresh) params.set("refresh", "true");
  const response = await fetch(`${runtimeApiBasePath()}/libraries/${encodeURIComponent(libraryId)}/items?${params}`);
  if (!response.ok) throw await responseError(response, "Unable to load library items");
  return response.json();
}

export async function fetchLibraryItem(itemId: string): Promise<JellyfinMediaItem> {
  const response = await fetch(`${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}`);
  if (!response.ok) throw await responseError(response, "Unable to load library item");
  return response.json();
}

export async function fetchLibraryItemChildren(itemId: string): Promise<JellyfinMediaItem[]> {
  const response = await fetch(`${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/children`);
  if (!response.ok) throw await responseError(response, "Unable to load library item children");
  return response.json();
}

export async function fetchLibraryItemSeasons(itemId: string): Promise<JellyfinMediaItem[]> {
  const response = await fetch(`${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/seasons`);
  if (!response.ok) throw await responseError(response, "Unable to load seasons");
  return response.json();
}

export function primaryImageUrl(itemId: string, imageTag?: string): string {
  const query = imageTag ? `?${new URLSearchParams({ tag: imageTag })}` : "";
  return `${runtimeApiBasePath()}/library-items/${encodeURIComponent(itemId)}/image${query}`;
}

export async function fetchPathMappings(): Promise<PathMapping[]> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings`);
  if (!response.ok) throw await responseError(response, "Unable to load path mappings");
  return response.json();
}

export async function createPathMapping(
  remotePath: string,
  localPath: string,
): Promise<PathMapping> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remote_path: remotePath, local_path: localPath }),
  });
  if (!response.ok) throw await responseError(response, "Unable to create path mapping");
  return response.json();
}

export async function updatePathMapping(
  id: number,
  remotePath: string,
  localPath: string,
): Promise<PathMapping> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remote_path: remotePath, local_path: localPath }),
  });
  if (!response.ok) throw await responseError(response, "Unable to update path mapping");
  return response.json();
}

export async function deletePathMapping(id: number): Promise<void> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings/${id}`, { method: "DELETE" });
  if (!response.ok) throw await responseError(response, "Unable to delete path mapping");
}

export async function reorderPathMappings(mappingIds: number[]): Promise<PathMapping[]> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mapping_ids: mappingIds }),
  });
  if (!response.ok) throw await responseError(response, "Unable to reorder path mappings");
  return response.json();
}

export async function resolvePathMapping(remotePath: string): Promise<PathResolution> {
  const response = await fetch(`${runtimeApiBasePath()}/settings/path-mappings/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remote_path: remotePath }),
  });
  if (!response.ok) throw await responseError(response, "Unable to resolve path mapping");
  return response.json();
}
