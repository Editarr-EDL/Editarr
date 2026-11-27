import type { NewScanForm } from "./use-new-scan";

/** One-line summary of the advanced video settings, shown while the disclosure is collapsed. */
export function advancedVideoSummary(video: NewScanForm["video"]): string {
  if (video.invalid) return "Check these settings";
  const pass = video.coarseScanEnabled ? `Coarse every ${video.coarseScanInterval} s` : "Fine scan";
  return `${pass} · ${video.qwenSummariesPerSegment} frames · join ${video.qwenSegmentJoinGapSeconds} s`;
}

/** Collapsed "Advanced video settings" disclosure: coarse scan, frames per segment, and join gap. */
export function NewScanAdvanced({ video }: { video: NewScanForm["video"] }) {
  return <details className="new-scan-advanced">
    <summary>
      <svg className="new-scan-chevron" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 6l6 6-6 6" /></svg>
      Advanced video settings
      <span className={video.invalid ? "new-scan-advanced-summary invalid" : "new-scan-advanced-summary"}>{advancedVideoSummary(video)}</span>
    </summary>
    <div className="new-scan-advanced-body">
      <div className="new-scan-field">
        <label className="new-scan-toggle"><input type="checkbox" checked={video.coarseScanEnabled}
          onChange={(event) => video.setCoarseScanEnabled(event.target.checked)} /> Enable coarse scan</label>
        <span className="new-scan-unit"><input type="number" aria-label="Coarse scan interval (seconds)" min="0.1" step="0.1"
          disabled={!video.coarseScanEnabled} value={video.coarseScanInterval}
          onChange={(event) => video.setCoarseScanInterval(Number(event.target.value))} /><span aria-hidden="true">s</span></span>
      </div>
      <label className="new-scan-field"><span>Frames per segment</span>
        <span className="new-scan-unit"><input aria-label="Representative frames per flagged segment" type="number" min="1" step="1"
          value={video.qwenSummariesPerSegment}
          onChange={(event) => video.setQwenSummariesPerSegment(Number(event.target.value))} /><span aria-hidden="true">fr</span></span>
      </label>
      <label className="new-scan-field"><span>Join segments within</span>
        <span className="new-scan-unit"><input aria-label="Join flagged segments within (seconds)" type="number" min="0" step="0.1"
          value={video.qwenSegmentJoinGapSeconds}
          onChange={(event) => video.setQwenSegmentJoinGapSeconds(Number(event.target.value))} /><span aria-hidden="true">s</span></span>
      </label>
      <p className="new-scan-hint">Fine scans analyze every source frame. Qwen receives evenly spaced representative flagged frames together after the scene scan; short clean gaps can be joined into one segment.</p>
    </div>
  </details>;
}
