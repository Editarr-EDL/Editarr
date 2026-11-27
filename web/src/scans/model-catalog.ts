import type { ModelCapability, ScanCapabilities, ScanModelCapability } from "../api/api-system-types";
import { fallbackCopy, MODEL_COPY } from "./model-catalog-copy";
import { defaultModelId } from "./model-options";

export type ModelRole = "transcriber" | "filter" | "video" | "severity";
export type ModelStatus =
  | { kind: "built_in" }
  | { kind: "ready"; bytesTotal: number | null }
  | { kind: "download"; bytesTotal: number | null }
  | { kind: "downloading"; bytesDownloaded: number | null; bytesTotal: number | null }
  | { kind: "failed"; detail: string | null }
  | { kind: "unavailable"; reason: string };

export interface ModelOption {
  id: string;
  name: string;
  variant?: string;
  description: string;
  detects: string[];
  reports: string[];
  bestFor?: string;
  badges: string[];
  recommended: boolean;
  isDefault: boolean;
  available: boolean;
  status: ModelStatus;
}

/** Each role's selectable list and the backend ids it may hold, in the backend's selection order. */
const ROLES: Record<ModelRole, { list: keyof Pick<ScanCapabilities, "audio_models" | "audio_filters" | "video_models" | "qwen_models">; ids: string[] }> = {
  transcriber: { list: "audio_models", ids: ["whisper", "mlx_whisper", "apple_speech"] },
  filter: { list: "audio_filters", ids: ["better_profanity", "glin_profanity", "alt_profanity_check", "qwen"] },
  video: {
    list: "video_models",
    ids: ["qwen3-vl-8b", "qwen3-vl-4b", "nudenet", "image_safety_gate", "siglip2_moderation_nsfw",
      "siglip2_moderation_violence", "shieldstral", "d1-3b"],
  },
  severity: { list: "qwen_models", ids: ["qwen3-vl-8b-instruct-4bit", "qwen3-vl-4b-instruct-4bit"] },
};

/** Description models share artifacts with the matching video detector, which carries their download state. */
const SEVERITY_ARTIFACT: Record<string, string> = {
  "qwen3-vl-8b-instruct-4bit": "qwen3-vl-8b",
  "qwen3-vl-4b-instruct-4bit": "qwen3-vl-4b",
};
const SEVERITY_ENRICHMENT = "qwen3_vl";

const REASONS: Record<string, string> = {
  unsupported_platform: "Not supported on this server’s operating system",
  unsupported_architecture: "Not supported on this server’s processor architecture",
  unsupported_os_version: "Needs a newer operating system version",
  missing_package: "A required package isn’t installed on this server",
  missing_provider: "ONNX Runtime’s CPU provider isn’t available on this server",
  deferred_gap: "Not available on Windows or Linux yet",
  unsupported_dependency_version: "An installed dependency on this server is too old",
};

const MODEL_REASONS: Record<string, Record<string, string>> = {
  apple_speech: {
    unsupported_os_version: "Needs macOS 26 or later",
    missing_package: "Needs the Editarr macOS app or Command Line Tools with a macOS 26 SDK",
  },
  "d1-3b": { unsupported_dependency_version: "Needs transformers 5.14 or newer on this server" },
};

/**
 * Readable text for a capability `reason` code. Pass `modelId` for model-specific wording
 * (Apple Speech's macOS requirements, d1-3B's transformers version); unknown codes stay readable.
 */
export function unavailableReasonText(reason: string | null, modelId?: string): string {
  if (!reason) return "Unavailable on this server";
  const specific = modelId ? MODEL_REASONS[modelId]?.[reason] : undefined;
  return specific ?? REASONS[reason] ?? `Unavailable on this server (${reason.replace(/_/g, " ")})`;
}

function downloadStatus(capability: ModelCapability | undefined): ModelStatus {
  const download = capability?.download;
  if (!download) return { kind: "ready", bytesTotal: null };
  if (capability.weight_requirement === "none" || download.state === "not_required") return { kind: "built_in" };
  switch (download.state) {
    case "ready": return { kind: "ready", bytesTotal: download.bytes_total };
    case "not_downloaded": return { kind: "download", bytesTotal: download.bytes_total };
    case "downloading": return { kind: "downloading", bytesDownloaded: download.bytes_downloaded, bytesTotal: download.bytes_total };
    case "cancelled": return { kind: "failed", detail: "Download was cancelled. Restart it in Settings › Models." };
    default: return { kind: "failed", detail: download.error_detail || download.error_code };
  }
}

/** The capability records deciding one option's availability and download state. */
function joinCapabilities(role: ModelRole, id: string, byId: Map<string, ModelCapability>) {
  if (role !== "severity") return { availability: byId.get(id), download: byId.get(id) };
  return {
    availability: byId.get(SEVERITY_ENRICHMENT),
    download: byId.get(SEVERITY_ARTIFACT[id] ?? SEVERITY_ENRICHMENT) ?? byId.get(SEVERITY_ENRICHMENT),
  };
}

/**
 * Build picker options for one role from ScanCapabilities. Offered models come from the role's list;
 * `model_capabilities` adds availability, download state and size, plus the role's unavailable models
 * with readable reasons. `defaultId` overrides which option is marked Default.
 */
export function buildModelOptions(role: ModelRole, capabilities: ScanCapabilities | null | undefined,
  options: { defaultId?: string } = {}): ModelOption[] {
  if (!capabilities) return [];
  const { list, ids } = ROLES[role];
  const offered: ScanModelCapability[] = capabilities[list] ?? [];
  const byId = new Map((capabilities.model_capabilities ?? []).map((item) => [item.id, item]));
  const offeredIds = new Set(offered.map((model) => model.id));
  const defaultId = options.defaultId ?? defaultModelId(offered);
  const missing: ScanModelCapability[] = role === "severity" ? [] : ids
    .filter((id) => !offeredIds.has(id) && byId.get(id)?.availability === "unavailable")
    .map((id) => byId.get(id)!);

  return [...offered, ...missing].map((model) => {
    const joined = joinCapabilities(role, model.id, byId);
    const available = offeredIds.has(model.id) && joined.availability?.availability !== "unavailable";
    const copy = MODEL_COPY[model.id] ?? fallbackCopy(model.display_name);
    const reasonId = role === "severity" ? SEVERITY_ENRICHMENT : model.id;
    return {
      id: model.id,
      ...copy,
      recommended: available && Boolean(model.recommended),
      isDefault: model.id === defaultId,
      available,
      status: available ? downloadStatus(joined.download)
        : { kind: "unavailable", reason: unavailableReasonText(joined.availability?.reason ?? null, reasonId) },
    };
  });
}
