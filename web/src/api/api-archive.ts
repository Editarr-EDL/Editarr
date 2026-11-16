import { runtimeApiBasePath } from "../shared/runtime-urls";
import { jsonApi } from "./api-transport";

export type ArchiveSource = "radarr" | "sonarr" | "manual_files";

export interface ArchivedScan { id: string; scan_number: number; name: string | null; status: string; created_at: string }
export interface ArchivedReview { id: string; name: string; owner: string | null; updated_at: string }
export interface ArchivedExport { id: number; format: "edl" | "mcf"; scope: "legacy" | "default" | "personal"; exported_at: string }

export interface ArchivedItem {
  id: string;
  source: ArchiveSource;
  title: string;
  item_type: string;
  series: string | null;
  season: number | null;
  episode: number | null;
  last_episode: number | null;
  path: string | null;
  scans: ArchivedScan[];
  reviews: ArchivedReview[];
  exports: ArchivedExport[];
}

export interface ArchivePage { total: number; offset: number; limit: number; items: ArchivedItem[] }

export function fetchArchive(offset: number, limit: number, signal?: AbortSignal): Promise<ArchivePage> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return jsonApi(`${runtimeApiBasePath()}/archive?${query}`, { signal }, "Unable to load the archive");
}
