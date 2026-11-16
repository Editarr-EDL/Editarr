import { describe, expect, it } from "vitest";

import type { Scan } from "../api/api-scan-types";
import { scanOptionsSummary } from "./scan-components";

describe("scan provenance summary", () => {
  const scan = {
    scan_mode: "content", manual: false, scan_audio: false, scan_video: false,
    media_source_name: null, mcf_document: null,
  } as Scan;

  it("labels absent historical coverage and scanner version unknown", () => {
    expect(scanOptionsSummary(scan)).toContain("Unknown coverage · Scanner unknown");
  });

  it("shows recorded stage status without implying other stages ran", () => {
    expect(scanOptionsSummary({
      ...scan, scanner_behavior_version: "scanner-1",
      stage_coverage: [{
        stage: "audio.transcription", execution_id: "attempt-1", status: "partial", source: "generated",
        release_version: null, scanner_behavior_version: null, build_revision: null,
        model_id: null, model_revision: null, model_digest: null, quantization: null,
        effective_settings: null, intervals: null, sampling_seconds: null,
      }],
    })).toContain("audio.transcription: partial · Scanner scanner-1");
  });
});