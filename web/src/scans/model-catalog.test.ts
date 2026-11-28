import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { ModelCapability, ScanCapabilities } from "../api/api-system-types";
import { buildModelOptions, unavailableReasonText } from "./model-catalog";
import { MODEL_COPY } from "./model-catalog-copy";

const download = (state: ModelCapability["download"]["state"], total: number | null = null, done: number | null = null) =>
  ({ state, bytes_downloaded: done, bytes_total: total, error_code: null, error_detail: null });

function capability(id: string, overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    id, display_name: id, stage: "detector", availability: "available", reason: null, device: "cpu",
    weight_requirement: "on_demand", download: download("ready", 100), enrichment_behavior: "none", ...overrides,
  };
}

/** A Windows-like host: MLX models and Qwen enrichment can't run. */
const windows: ScanCapabilities = {
  audio_models: [{ id: "whisper", display_name: "Whisper (faster-whisper)" }],
  audio_filters: [{ id: "better_profanity", display_name: "better-profanity" }, { id: "glin_profanity", display_name: "glin-profanity" }],
  video_models: [{ id: "nudenet", display_name: "NudeNet" }, { id: "d1-3b", display_name: "d1-3B" }],
  qwen_models: [
    { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true },
    { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B" },
  ],
  model_capabilities: [
    capability("whisper", { download: download("not_downloaded", 147_882_941) }),
    capability("mlx_whisper", { availability: "unavailable", reason: "unsupported_platform" }),
    capability("apple_speech", { availability: "unavailable", reason: "unsupported_platform", weight_requirement: "none", download: download("not_required") }),
    capability("better_profanity", { weight_requirement: "none", download: download("not_required") }),
    capability("glin_profanity", { weight_requirement: "none", download: download("not_required") }),
    capability("qwen", { availability: "unavailable", reason: "deferred_gap" }),
    capability("nudenet", { weight_requirement: "none", download: download("not_required") }),
    capability("d1-3b", { download: download("downloading", 200, 50) }),
    capability("qwen3-vl-8b", { availability: "unavailable", reason: "unsupported_platform", recommended: true, download: download("not_downloaded", 5_776_630_909) }),
    capability("qwen3_vl", { stage: "enrichment", availability: "unavailable", reason: "deferred_gap" }),
  ],
};

describe("buildModelOptions", () => {
  it("joins offered models with their download state and adds the role's unavailable models", () => {
    const options = buildModelOptions("transcriber", windows);
    expect(options.map((option) => [option.id, option.available, option.status])).toEqual([
      ["whisper", true, { kind: "download", bytesTotal: 147_882_941 }],
      ["mlx_whisper", false, { kind: "unavailable", reason: "Not supported on this server’s operating system" }],
      ["apple_speech", false, { kind: "unavailable", reason: "Not supported on this server’s operating system" }],
    ]);
    expect(options[0]).toMatchObject({ name: "Whisper", variant: "faster-whisper", isDefault: true, recommended: false });
  });

  it("maps built-in, downloading and failed states", () => {
    const filters = buildModelOptions("filter", windows);
    expect(filters.map((option) => option.id)).toEqual(["better_profanity", "glin_profanity", "qwen"]);
    expect(filters[0].status).toEqual({ kind: "built_in" });
    expect(filters.at(-1)?.status).toEqual({ kind: "unavailable", reason: "Not available on Windows or Linux yet" });
    const video = buildModelOptions("video", windows);
    expect(video.find((option) => option.id === "d1-3b")?.status).toEqual({ kind: "downloading", bytesDownloaded: 50, bytesTotal: 200 });
    const failed = buildModelOptions("video", { ...windows, model_capabilities: [capability("nudenet", { download: { ...download("failed"), error_code: "checksum" } })] });
    expect(failed[0].status).toEqual({ kind: "failed", detail: "checksum" });
  });

  it("never recommends a model this host can't run", () => {
    const video = buildModelOptions("video", windows);
    const qwen = video.find((option) => option.id === "qwen3-vl-8b");
    expect(qwen).toMatchObject({ available: false, recommended: false });
    expect(video.find((option) => option.id === "nudenet")).toMatchObject({ available: true, isDefault: true });
  });

  it("takes description-model availability from Qwen enrichment and size from the shared video artifact", () => {
    const severity = buildModelOptions("severity", windows);
    expect(severity.map((option) => [option.id, option.available, option.isDefault])).toEqual([
      ["qwen3-vl-8b-instruct-4bit", false, true], ["qwen3-vl-4b-instruct-4bit", false, false],
    ]);
    const mac = buildModelOptions("severity", {
      ...windows,
      model_capabilities: [capability("qwen3_vl", { stage: "enrichment" }), capability("qwen3-vl-8b", { download: download("ready", 5_776_630_909) })],
    });
    expect(mac[0]).toMatchObject({ available: true, recommended: true, status: { kind: "ready", bytesTotal: 5_776_630_909 } });
  });

  it("sizes the Qwen filter by the selected description model, which it loads", () => {
    const caps: ScanCapabilities = {
      ...windows,
      audio_filters: [{ id: "qwen", display_name: "Qwen (multilingual)" }],
      model_capabilities: [
        capability("qwen", { stage: "audio", download: download("ready", 5_776_630_909) }),
        capability("qwen3-vl-8b", { download: download("ready", 5_776_630_909) }),
        capability("qwen3-vl-4b", { download: download("not_downloaded", 3_109_729_929) }),
        capability("qwen38-27b", { download: download("not_downloaded", 16_074_308_726) }),
      ],
    };
    const qwenFilter = (qwenModelId?: string) => buildModelOptions("filter", caps, { qwenModelId })[0];
    expect(qwenFilter("qwen3-vl-4b-instruct-4bit").status).toEqual({ kind: "download", bytesTotal: 3_109_729_929 });
    expect(qwenFilter("qwen3-vl-8b-instruct-4bit").status).toEqual({ kind: "ready", bytesTotal: 5_776_630_909 });
    expect(qwenFilter("qwen38-27b-instruct-4bit").status).toEqual({ kind: "download", bytesTotal: 16_074_308_726 });
    expect(qwenFilter().status).toEqual({ kind: "ready", bytesTotal: 5_776_630_909 });
    expect(qwenFilter("future-qwen").status).toEqual({ kind: "ready", bytesTotal: 5_776_630_909 });
  });

  it("works without model_capabilities, honors defaultId, and falls back for unknown models", () => {
    const options = buildModelOptions("video", {
      audio_models: [], audio_filters: [], qwen_models: [],
      video_models: [{ id: "nudenet", display_name: "NudeNet" }, { id: "future_model", display_name: "Future Model" }],
    }, { defaultId: "future_model" });
    expect(options.map((option) => [option.id, option.available, option.isDefault, option.name])).toEqual([
      ["nudenet", true, false, "NudeNet"], ["future_model", true, true, "Future Model"],
    ]);
    expect(options[1]).toMatchObject({ detects: [], reports: [], badges: [] });
    expect(buildModelOptions("video", null)).toEqual([]);
  });
});

describe("unavailableReasonText", () => {
  it("uses model-specific wording where the backend does", () => {
    expect(unavailableReasonText("unsupported_os_version", "apple_speech")).toBe("Needs macOS 26 or later");
    expect(unavailableReasonText("unsupported_dependency_version", "d1-3b")).toBe("Needs transformers 5.14 or newer on this server");
    expect(unavailableReasonText(null)).toBe("Unavailable on this server");
    expect(unavailableReasonText("new_reason")).toBe("Unavailable on this server (new reason)");
  });
});

describe("catalog coverage", () => {
  const source = readFileSync(resolve(process.cwd(), "../scanner/settings/capabilities.py"), "utf8");

  it("has copy for every model the backend can offer", () => {
    const names = source.match(/DISPLAY_NAMES = \{([\s\S]*?)\n\}/)?.[1] ?? "";
    const qwen = source.match(/QWEN_MODEL_DISPLAY_NAMES = \{([\s\S]*?)\n\}/)?.[1] ?? "";
    // Keys may be string literals or module constants such as APPLE_SPEECH_MODEL.
    const constant = (name: string) => source.match(new RegExp(`^${name} = "([^"]+)"`, "m"))?.[1] ?? name;
    const ids = [...`${names}\n${qwen}`.matchAll(/^\s+(?:"([^"]+)"|([A-Z][A-Z0-9_]*)):/gm)]
      .map((match) => match[1] ?? constant(match[2]));
    expect(ids).toContain("apple_speech");
    expect(ids.length).toBeGreaterThan(15);
    expect(ids.filter((id) => id !== "qwen3_vl" && !MODEL_COPY[id])).toEqual([]);
  });

  it("has readable text for every availability reason", () => {
    const reasons = [...(source.match(/AvailabilityReason = Literal\[([\s\S]*?)\]/)?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((match) => match[1]);
    expect(reasons.length).toBeGreaterThan(5);
    expect(reasons.filter((reason) => unavailableReasonText(reason).includes("("))).toEqual([]);
  });
});
