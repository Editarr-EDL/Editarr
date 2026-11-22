import type { HistoricalVersion } from "../api/api-stable-media";
import type { Scan } from "../api/api-scan-types";
import { scanLabel } from "../shared/display";

export function PreviousVersionScans({ versions, imports = [], linkedScans = [], verifiedCopies, canVerify, onVerify }: {
  versions: HistoricalVersion[];
  imports?: Scan[];
  linkedScans?: Scan[];
  verifiedCopies: Scan[];
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
  const verifiedAudio = new Set(verifiedCopies.filter((scan) => scan.scan_audio).map((scan) => scan.verified_from_scan_id));
  const verifiedVideo = new Set(verifiedCopies.filter((scan) => scan.scan_video).map((scan) => scan.verified_from_scan_id));
  const needsVerification = [...entries, ...pending, ...linked].filter(({ scan }) =>
    (scan.scan_audio && !verifiedAudio.has(scan.id)) || (scan.scan_video && !verifiedVideo.has(scan.id)));
  if (!needsVerification.length) return null;
  return <section className="previous-version-scans" aria-label="Previous version scans">
    <h3>Scans that need verification</h3>
    <ul>{needsVerification.map(({ scan, label }) => <li key={scan.id}>
      <div><strong>{scanLabel(scan)}</strong><div>{label} · {verifiedAudio.has(scan.id)
        ? "Video needs verification for this version" : verifiedVideo.has(scan.id)
          ? "Audio needs verification for this version" : "Needs verification for this version"}</div></div>
      {canVerify && <button type="button" className="btn-secondary compact-button"
        onClick={() => onVerify(scan)}>Verify for this version</button>}
    </li>)}</ul>
  </section>;
}
