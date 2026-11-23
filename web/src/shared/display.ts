import { useEffect } from "react";
import type { Scan, ScanQueueItem } from "../api/api-scan-types";

const documentTitlePrefix = "Editarr — Media Editing";

export function formatTime(seconds: number): string {
  const safeSeconds = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

export function formatBytes(
  value: number | null | undefined,
  options: { unit?: "auto" | "gb"; unavailable?: string } = {},
): string {
  if (value === null || value === undefined) return options.unavailable ?? "Size unavailable";
  if (options.unit === "gb") {
    return `${(value / 1_073_741_824).toFixed(value >= 10_737_418_240 ? 0 : 1)} GB`;
  }
  if (value === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function scanLabel(scan: Scan | ScanQueueItem): string {
  return `${scan.name ? `${scan.name} · ` : ""}Scan #${scan.scan_id}`;
}

export function scanOriginLabel(scan: Scan | ScanQueueItem, { includeTime = true } = {}): string {
  if (scan.origin === "manual") return "Manual";
  if (scan.origin === "imported") {
    const source = scan.import_source_name || scan.media_source_name || "Imported EDL";
    if (!includeTime) return `Imported from ${source}`;
    const timestamp = scan.imported_at ? new Date(scan.imported_at).toLocaleString() : "time unavailable";
    return `Imported from ${source} on ${timestamp}`;
  }
  return "Generated";
}


export function resetPageTitle() {
  document.title = documentTitlePrefix;
}

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${documentTitlePrefix} — ${title}`;
  }, [title]);
}
