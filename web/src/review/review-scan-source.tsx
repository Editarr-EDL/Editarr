import type { Scan } from "../api/api-scan-types";
import { scanLabel, scanOriginLabel } from "../shared/display";
import { InfoButton } from "../ui/InfoButton";

export function reviewScanName(scan: Scan) {
  return scan.name?.trim() || "Unnamed scan";
}

export function reviewScanSourceLabel(scans: Scan[]) {
  if (!scans.length) return "Source unavailable";
  return scans.length === 1 ? reviewScanName(scans[0]) : `${scans.length} scans`;
}

/** Describe the contributing scans while keeping names and counts compact in Review. */
export function ReviewScanInfo({ scans }: { scans: Scan[] }) {
  if (!scans.length) return null;
  return <InfoButton label={`Show scan details for ${reviewScanSourceLabel(scans)}`}>
    {scans.map((scan) => {
      const origin = scanOriginLabel(scan, { includeTime: false });
      const timestamp = (scan.origin === "imported" ? scan.imported_at : scan.scan_started_at) || scan.created_at;
      return <span className="review-scan-info-entry" key={scan.id}>
        <strong>{scanLabel(scan)}</strong>
        <span>{origin} · <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time></span>
        {scan.community_provenance && <span>Source author: {scan.community_provenance.author_id}</span>}
      </span>;
    })}
  </InfoButton>;
}
