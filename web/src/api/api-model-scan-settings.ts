import { runtimeApiBasePath } from "../shared/runtime-urls";
import { jsonApi } from "./api-transport";

interface ModelScanSettings {
  audio_padding_seconds: number;
}

export function fetchModelScanSettings(signal: AbortSignal): Promise<ModelScanSettings> {
  return jsonApi(`${runtimeApiBasePath()}/settings/models/scans`, { signal }, "Unable to load scan settings.");
}

export function saveModelScanSettings(audioPaddingSeconds: number): Promise<ModelScanSettings> {
  return jsonApi(`${runtimeApiBasePath()}/settings/models/scans`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_padding_seconds: audioPaddingSeconds }),
  }, "Unable to save scan settings.");
}
