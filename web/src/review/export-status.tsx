import type {
  ExportFormat,
  ExportScanReference,
  ExportStatus,
} from "../api/api-mcf-types";

export function exportProvenance(scans: ExportScanReference[] = []): string {
  if (!scans.length) return "provenance unavailable";
  return scans.map((scan) => `${scan.name ? `${scan.name} · ` : ""}Scan #${scan.scan_id} · ${scan.origin || "generated"} (${scan.role})`).join(", ");
}

export function emptyExportStatus(): ExportStatus {
  const summary = (format: ExportFormat) => ({ format, present: false, exported_at: null, review_id: null, exported_format_revision: null, current: false, outdated: false, exported_scans: [], exported_review: null });
  return { exports: { edl: summary("edl"), mcf: summary("mcf") } };
}

export function normalizedExportStatus(value?: { exports?: ExportStatus }): ExportStatus {
  return value?.exports || emptyExportStatus();
}

/** The adjacent pre-migration sidecars, which `Manage exports` deletes like an item default. */
export function legacyExports(status: ExportStatus): ExportStatus["exports"] {
  return status.legacy_exports ?? status.exports;
}

/** The status after one adjacent sidecar was deleted; a format showing the caller's own file keeps it. */
export function withoutLegacyExport(status: ExportStatus, format: ExportFormat): ExportStatus {
  const legacy = legacyExports(status);
  const empty = emptyExportStatus().exports[format];
  const shownLegacy = JSON.stringify(status.exports[format]) === JSON.stringify(legacy[format]);
  return {
    ...status,
    exports: shownLegacy ? { ...status.exports, [format]: empty } : status.exports,
    legacy_exports: { ...legacy, [format]: empty },
  };
}

/** Keep only exports attributed to one saved review.
 * @param status Media version export status.
 * @param reviewId Saved review whose badges are being rendered.
 */
export function exportStatusForReview(status: ExportStatus, reviewId: string): ExportStatus {
  const empty = emptyExportStatus().exports;
  return { exports: {
    edl: status.exports.edl.review_id === reviewId ? status.exports.edl : empty.edl,
    mcf: status.exports.mcf.review_id === reviewId ? status.exports.mcf : empty.mcf,
  } };
}

export function ExportBadges({ status }: { status: ExportStatus }) {
  const active = (["edl", "mcf"] as const).map((format) => status.exports[format]).filter((item) => item.present);
  if (!active.length) return null;
  return <span className="export-badges">{active.map((item) => <span className={`export-badge ${item.outdated ? "outdated" : "current"}`} key={item.format} title={`${item.format.toUpperCase()} ${item.outdated ? "outdated" : "current"}${item.exported_at ? ` · ${new Date(item.exported_at).toLocaleString()}` : ""} · ${exportProvenance(item.exported_scans)}`}>{item.format.toUpperCase()} · {item.outdated ? "Outdated" : "Current"}</span>)}</span>;
}
