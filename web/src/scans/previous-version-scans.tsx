import type { HistoricalVersion } from "../api/api-stable-media";
import type { Scan } from "../api/api-scan-types";
import { scanLabel } from "../shared/display";

function coverageBySource(verifiedCopies: Scan[]) {
  const coverage = new Map<string, {
    totals: { audio: number; video: number };
    findings: { audio: Set<number>; video: Set<number> };
  }>();
  for (const { verified_from_scan_id: sourceId, verification_coverage: copy } of verifiedCopies) {
    if (!sourceId || !copy) continue;
    const source = coverage.get(sourceId) ?? {
      totals: { audio: 0, video: 0 },
      findings: { audio: new Set<number>(), video: new Set<number>() },
    };
    for (const channel of ["audio", "video"] as const) {
      source.totals[channel] = Math.max(source.totals[channel], copy.source_counts[channel]);
      for (const id of copy.covered_finding_ids[channel]) source.findings[channel].add(id);
    }
    coverage.set(sourceId, source);
  }
  return coverage;
}

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
  const coverage = coverageBySource(verifiedCopies);
  const needsVerification = [...entries, ...pending, ...linked].flatMap((entry) => {
    const { scan } = entry;
    const source = coverage.get(scan.id);
    const audio = scan.scan_audio && (!source || source.findings.audio.size < source.totals.audio);
    const video = scan.scan_video && (!source || source.findings.video.size < source.totals.video);
    return audio || video ? [{ ...entry, audio, video }] : [];
  });
  if (!needsVerification.length) return null;
  return <section className="previous-version-scans" aria-label="Previous version scans">
    <h3>Scans that need verification</h3>
    <ul>{needsVerification.map(({ scan, label, audio, video }) => <li key={scan.id}>
      <div><strong>{scanLabel(scan)}</strong><div>{label} · {scan.scan_audio && !audio
        ? "Video needs verification for this version" : scan.scan_video && !video
          ? "Audio needs verification for this version" : "Needs verification for this version"}</div></div>
      {canVerify && <button type="button" className="btn-secondary compact-button"
        onClick={() => onVerify(scan)}>Verify for this version</button>}
    </li>)}</ul>
  </section>;
}
