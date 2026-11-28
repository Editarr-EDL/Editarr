import { describe, expect, it } from "vitest";

import type { Scan, ScanStageCoverage } from "../api/api-scan-types";
import { scanOptionsSummary } from "./scan-components";

const scan = {
  scan_mode: "content", manual: false, scan_audio: false, scan_video: true,
  video_model: "qwen3-vl-8b", coarse_scan_enabled: true, coarse_scan_interval: 5,
  qwen_summaries_per_segment: 4, qwen_segment_join_gap_seconds: 3,
  media_source_name: "Remux-2160p", mcf_document: null,
} as Scan;

function coverage(
  stage: string,
  status: ScanStageCoverage["status"],
  scannerVersion: string | null = null,
): ScanStageCoverage {
  return {
    stage, execution_id: "attempt-1", status, source: "generated",
    release_version: null, scanner_behavior_version: scannerVersion, build_revision: null,
    model_id: null, model_revision: null, model_digest: null, quantization: null,
    effective_settings: null, intervals: null, sampling_seconds: null,
  };
}

describe("scan provenance summary", () => {
  it("omits missing historical coverage and scanner version", () => {
    expect(scanOptionsSummary(scan)).toMatch(/Join gaps 3s · Remux-2160p$/);
  });

  it("omits completed and unknown steps and shows a recorded scanner version", () => {
    const summary = scanOptionsSummary({
      ...scan, scanner_behavior_version: null,
      stage_coverage: [
        coverage("visual", "unknown", "requested-version"),
        coverage("visual_qwen", "unknown"),
        ...Array.from({ length: 17 }, (_, i) => coverage(`visual_qwen:scene_${i}`, "complete", "scanner-1")),
      ],
    });
    expect(summary).toMatch(/Remux-2160p · Scanner scanner-1$/);
    expect(summary).not.toMatch(/visual[_:]|unknown|complete/i);
  });

  it("keeps actionable coverage and prefers the recorded scan-level version", () => {
    expect(scanOptionsSummary({
      ...scan, scanner_behavior_version: "scanner-2",
      stage_coverage: [
        coverage("audio.transcription", "partial"),
        coverage("audio_qwen", "failed"),
        coverage("visual", "complete", "scanner-1"),
      ],
    })).toMatch(/audio\.transcription: partial, audio_qwen: failed · Scanner scanner-2$/);
  });

  it("omits the scanner label when completed stages give conflicting versions", () => {
    expect(scanOptionsSummary({
      ...scan, scanner_behavior_version: "unknown",
      stage_coverage: [
        coverage("visual", "complete", "scanner-1"),
        coverage("visual_qwen:scene_0005", "complete", "scanner-2"),
      ],
    })).toMatch(/Join gaps 3s · Remux-2160p$/);
  });
});
