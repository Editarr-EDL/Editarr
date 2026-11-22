import type { HistoricalVersion } from "../api/api-stable-media";
import type { Scan } from "../api/api-scan-types";
import { scanLabel } from "../shared/display";

export function PreviousVersionScans({ versions, imports = [], linkedScans = [], verifiedSources, canVerify, onVerify }: {
  versions: HistoricalVersion[];
  imports?: Scan[];
  linkedScans?: Scan[];
  verifiedSources: ReadonlySet<string>;
  canVerify: boolean;
  onVerify: (scan: Scan) => void;
}) {
  const entries = versions.flatMap((version) => version.scans
    .filter((scan) => scan.status === "complete" && (scan.scan_audio || scan.scan_video) && scan.scan_mode !== "credits_detection")
    .map((scan) => ({ scan, label: version.label })));
  const existingIds = new Set(entries.map(({ scan }) => scan.id));
  const pending = imports.filter((scan) => !existingIds.has(scan.id))
    .map((scan) => ({ scan, label: "External import" }));
  const displayedIds = new Set([...existingIds, ...pending.map(({ scan }) => scan.id)]);
  const linked = linkedScans.filter((scan) => !displayedIds.has(scan.id) && scan.status === "complete"
    && scan.scan_mode !== "credits_detection" && (scan.scan_audio || scan.scan_video))
    .map((scan) => ({ scan, label: scan.media_source_name || "Linked scan" }));
  if (!entries.length && !pending.length && !linked.length) return null;
  return <section className="previous-version-scans" aria-label="Previous version scans">
    <h3>Scans that need verification</h3>
    <ul>{[...entries, ...pending, ...linked].map(({ scan, label }) => <li key={scan.id}>
      <div><strong>{scanLabel(scan)}</strong><div>{label} · {verifiedSources.has(scan.id)
        ? "Has verified cues for this version" : "Needs verification for this version"}</div></div>
      {canVerify && <button type="button" className="btn-secondary compact-button"
        onClick={() => onVerify(scan)}>Verify for this version</button>}
    </li>)}</ul>
  </section>;
}
