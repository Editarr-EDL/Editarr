import type { Scan } from "../api/api-scan-types";

function recordedVersion(value: string | null | undefined): string | null {
  const version = value?.trim();
  return version && version.toLowerCase() !== "unknown" ? version : null;
}

/** Present noteworthy coverage and use a completed stage's version when the recorded versions agree. */
export function scanProvenanceSummary(scan: Pick<Scan, "scanner_behavior_version" | "stage_coverage">) {
  const coverage = scan.stage_coverage ?? [];
  const versions = new Set(coverage
    .filter((stage) => stage.status === "complete")
    .map((stage) => recordedVersion(stage.scanner_behavior_version))
    .filter((version) => version !== null));
  return {
    scannerVersion: recordedVersion(scan.scanner_behavior_version) ?? (versions.size === 1 ? [...versions][0] : null),
    stages: coverage.filter((stage) => stage.status !== "complete" && stage.status !== "unknown"),
  };
}
