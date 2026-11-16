import type { ScanCapabilities } from "../api/api-system-types";
import { formatBytes } from "../shared/display";

/** Explain a selected scan backend's availability and weight requirement.
 * @param props Capability data, backend, scan type, and label option.
 */
export function ScanCapabilityDisclosure({
  capabilities,
  backendId,
  scanType,
  includeScanName = true,
}: {
  capabilities: ScanCapabilities | null;
  backendId: string;
  scanType: "audio" | "video";
  includeScanName?: boolean;
}) {
  const capability = capabilities?.model_capabilities?.find((item) => item.id === backendId);
  const download = capability?.download;
  const qwenGap = scanType === "video" && capabilities?.model_capabilities?.some(
    (item) => item.enrichment_behavior === "detector_findings_only",
  );
  const languageQwenUnavailable = scanType === "audio" && capabilities?.model_capabilities?.some(
    (item) => item.id === "qwen3_vl" && item.availability === "unavailable",
  );
  return <>
    {includeScanName && <label className="field"><span>Scan name (optional)</span><input aria-label="Scan name (optional)" defaultValue="" /></label>}
    {(!download && !qwenGap && !languageQwenUnavailable) ? null : <div className="scan-capability-disclosure" role="status">
    {download?.state === "not_downloaded" && <p>This scan will download {capability?.display_name || backendId} ({formatBytes(download.bytes_total)}) before inference starts.</p>}
    {download?.state === "downloading" && <p>{capability?.display_name || backendId} is downloading. The scan starts after the model is ready; download progress is separate from inference progress.</p>}
    {download?.state === "failed" && <p className="form-error">Model download needs attention: {download.error_detail || download.error_code || "retry from Model Storage"}.</p>}
    {download?.state === "cancelled" && <p className="form-error">Model download was cancelled. Restart it from Model Storage before scanning.</p>}
    {qwenGap && <p>Windows and Linux preserve detector findings without Qwen enrichment. Supported models may use CUDA when shown by the capability response; CPU remains available.</p>}
    {languageQwenUnavailable && <p>Qwen language detection and categorization are unavailable here. Multilingual profanity and blasphemy coverage depends on the selected filter and your Deny words. Detected words remain Swearing / Medium.</p>}
    </div>}
  </>;
}
