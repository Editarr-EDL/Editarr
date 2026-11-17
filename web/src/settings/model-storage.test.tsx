import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ManagedModelArtifact, ModelCapability, ModelDownloadState, ModelStorageStatus } from "../api/api-system-types";
import { AuthenticationGate } from "../auth/auth-gate";
import { installProductionApi, json } from "../test/app-test-fixtures";
import { ModelStorage } from "./model-storage";

const noDownload: ModelDownloadState = {
  state: "not_downloaded", bytes_downloaded: null, bytes_total: null, error_code: null, error_detail: null,
};

function artifact(id: string, backend: string, state: ModelDownloadState["state"]): ManagedModelArtifact {
  return {
    artifact_id: id, backend_ids: [backend], state: { ...noDownload, state }, expected_size: 100,
    source_url: "https://example.com/models", license_spdx: "Apache-2.0",
    license_status: "declared", removable: true,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderInventory(storage: ModelStorageStatus) {
  const fallback = installProductionApi();
  const fetch = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    if (new URL(String(input), "http://editarr.test").pathname === "/api/models") {
      return Promise.resolve(json(storage));
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetch);
  render(<AuthenticationGate><ModelStorage /></AuthenticationGate>);
  return fetch;
}

const detector: ModelCapability = {
  id: "image_safety_gate", display_name: "Image Safety Gate", stage: "detector",
  availability: "available", reason: null, device: "cpu",
  weight_requirement: "on_demand", download: noDownload, enrichment_behavior: "none",
};

it("offers downloads with undeclared licenses and distinguishes unavailable and cached weights", async () => {
  const blocked: ModelCapability = {
    ...detector,
    id: "siglip2_moderation_nsfw", display_name: "SigLIP2 Moderation — NSFW", stage: "detector",
    availability: "unavailable", reason: "missing_package", device: "none",
  };
  renderInventory({
    artifacts: [
      artifact("siglip2-base", blocked.id, "ready"),
      artifact("siglip2-head", blocked.id, "not_downloaded"),
      {
        ...artifact("stable-diffusion-safety-checker", detector.id, "not_downloaded"),
        license_spdx: null,
        license_status: "undeclared",
      },
    ],
    model_capabilities: [blocked, detector],
  });
  fireEvent.click(await screen.findByText("Video Detection"));

  const cached = within(await screen.findByRole("article", { name: "siglip2-base model" }));
  expect(cached.getByText("cached", { exact: true })).toBeVisible();
  expect(cached.getByText("This build is missing a required runtime package.")).toBeVisible();
  expect(cached.getByRole("button", { name: "Remove" })).toBeEnabled();

  const unavailable = within(screen.getByRole("article", { name: "siglip2-head model" }));
  expect(unavailable.getByText("unavailable", { exact: true })).toBeVisible();
  expect(unavailable.queryByRole("button", { name: "Download" })).toBeNull();

  const supported = within(screen.getByRole("article", { name: "stable-diffusion-safety-checker model" }));
  expect(supported.getByText("CPU", { exact: true })).toBeVisible();
  expect(supported.getByRole("link", { name: "Not declared" })).toBeVisible();
  expect(supported.getByRole("button", { name: "Download" })).toBeEnabled();
  expect(screen.queryByText(/Windows|Linux|CUDA|deferred gap/)).toBeNull();
});

it("groups artifacts by stage and preserves open groups through refresh", async () => {
  const audio: ModelCapability = { ...detector, stage: "audio", id: "mlx_whisper", device: "mlx" };
  const enrichment: ModelCapability = { ...detector, stage: "enrichment", id: "qwen3_vl", device: "mlx" };
  const otherDetector: ModelCapability = { ...detector, id: "siglip2_moderation_nsfw" };
  const request = renderInventory({
    artifacts: [
      artifact("qwen-weights", enrichment.id, "not_downloaded"),
      { ...artifact("shared-weights", detector.id, "downloading"), backend_ids: [detector.id, otherDetector.id] },
      artifact("mlx-weights", audio.id, "ready"),
      { ...artifact("support-package", "", "not_required"), backend_ids: [], removable: false },
    ],
    model_capabilities: [audio, enrichment, detector, otherDetector],
  });

  const video = await screen.findByText("Video Detection");
  const sections = ["Audio", "Video Detection", "Enrichment", "Supporting Packages"];
  expect(Array.from(document.querySelectorAll("summary .model-stage-title"), (node) => node.textContent)).toEqual(sections);
  for (const article of screen.getAllByRole("article")) expect(article).not.toBeVisible();
  expect(within(video.closest("summary")!).getByText("1 item")).toBeVisible();
  expect(within(video.closest("summary")!).getByText("1 downloading")).toBeVisible();

  fireEvent.click(video);
  fireEvent.click(screen.getByText("Audio"));
  expect(screen.getByRole("article", { name: "shared-weights model" })).toBeVisible();
  expect(within(screen.getByRole("article", { name: "mlx-weights model" })).getByText("GPU (MLX)")).toBeVisible();
  expect(screen.getByRole("article", { name: "qwen-weights model" })).not.toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(request.mock.calls.filter(([url]) => String(url).endsWith("/api/models"))).toHaveLength(2));
  expect(screen.getByRole("article", { name: "shared-weights model" })).toBeVisible();
  fireEvent.click(video);
  expect(screen.getByRole("article", { name: "shared-weights model" })).not.toBeVisible();
  expect(screen.getByRole("article", { name: "mlx-weights model" })).toBeVisible();
});

it("sums cached and in-progress downloads once across all stages", async () => {
  const audio: ModelCapability = { ...detector, stage: "audio", id: "mlx_whisper", device: "mlx" };
  const storage: ModelStorageStatus = {
    artifacts: [
      {
        ...artifact("shared-weights", detector.id, "ready"),
        backend_ids: [detector.id, audio.id],
        state: { ...noDownload, state: "ready", bytes_downloaded: 1024 },
      },
      { ...artifact("cached", audio.id, "ready"), expected_size: 1024 },
      {
        ...artifact("partial", audio.id, "downloading"),
        expected_size: 8192,
        state: { ...noDownload, state: "downloading", bytes_downloaded: 512 },
      },
      {
        ...artifact("interrupted", detector.id, "failed"),
        state: { ...noDownload, state: "failed", bytes_downloaded: 999 },
      },
      artifact("never-downloaded", detector.id, "not_downloaded"),
      { ...artifact("bundled", detector.id, "not_required"), removable: false },
    ],
    model_capabilities: [detector, audio],
  };
  renderInventory(storage);
  const total = await screen.findByText("Estimated Total Storage Used");
  expect(total.parentElement).toHaveTextContent("2.5 KB");

  storage.artifacts = [];
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(total.parentElement).toHaveTextContent("0 B"));
});

it("shows the artifact's preferred device for a mixed-device backend", async () => {
  renderInventory({
    artifacts: [
      { ...artifact("wd-tagger", detector.id, "ready"), device: "cpu" },
      artifact("safety-checker", detector.id, "ready"),
    ],
    model_capabilities: [{ ...detector, device: "metal" }],
  });
  fireEvent.click(await screen.findByText("Video Detection"));
  const wd = within(screen.getByRole("article", { name: "wd-tagger model" }));
  expect(wd.getByText("CPU")).toBeVisible();
  const checker = within(screen.getByRole("article", { name: "safety-checker model" }));
  expect(checker.getByText("GPU (Metal)")).toBeVisible();
});
