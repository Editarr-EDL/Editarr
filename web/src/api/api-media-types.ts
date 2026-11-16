/** Connected media catalogs, sources, and path mappings. */

import type { ExportStatus } from "./api-mcf-types";
import type { MediaIdentity } from "./api-media-identity";

export interface JellyfinSettings {
  server_url: string | null;
  api_key_configured: boolean;
  last_checked_at: string | null;
  last_error: string | null;
}

export interface JellyfinSettingsUpdate {
  server_url?: string;
  api_key?: string;
  clear_api_key?: boolean;
}

export interface JellyfinLibrary {
  id: string;
  name: string;
  collection_type: string | null;
  locations: string[];
}

export interface JellyfinMediaItem {
  id: string;
  library_id: string | null;
  parent_id: string | null;
  item_type: string;
  name: string;
  sort_name: string | null;
  year: number | null;
  official_rating: string | null;
  season_number: number | null;
  index_number: number | null;
  overview: string | null;
  path: string | null;
  runtime_ticks: number | null;
  duration_seconds: number | null;
  resolution: string | null;
  media_format: string | null;
  media_provider_id: string | null;
  media_source_count: number | null;
  versions: JellyfinMediaVersion[];
  media_streams: Array<Record<string, unknown>>;
  image_tags: Record<string, unknown>;
  image_url: string | null;
  exports: ExportStatus;
  media_identity?: MediaIdentity | null;
}

export interface JellyfinMediaVersion {
  id: string | null;
  name: string | null;
  scene_name: string | null;
  release_group: string | null;
  edition: string | null;
  path: string | null;
  container: string | null;
  resolution: string | null;
  media_format: string | null;
  duration_seconds: number | null;
  size_bytes: number | null;
  exports: ExportStatus;
  media_identity?: MediaIdentity | null;
}

export interface JellyfinMediaPage {
  items: JellyfinMediaItem[];
  total_count: number;
  rating_values: string[];
  resolution_values: string[];
}

export interface LibraryItemQuery {
  startIndex?: number;
  limit?: number;
  sortBy?: "title" | "year" | "rating" | "duration" | "type";
  sortDirection?: "asc" | "desc";
  edlFilter?: "all" | "exported" | "not_exported";
  ratingFilter?: string;
  resolutionFilter?: string;
  refresh?: boolean;
  search?: string;
}

export interface PathMapping {
  id: number;
  remote_path: string;
  local_path: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface PathResolution {
  remote_path: string;
  local_path: string;
  mapping_id: number | null;
}
