import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ManagedModelArtifact, ModelCapability, ModelDownloadState } from "../api/api-system-types";
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

it("explains the macOS license restriction and distinguishes unavailable and cached weights", async () => {
  const blocked: ModelCapability = {
    id: "image_safety_gate", display_name: "Image Safety Gate", stage: "detector",
    availability: "unavailable", reason: "license_review_required", device: "none",
    weight_requirement: "none", download: noDownload, enrichment_behavior: "none",
  };
  const available: ModelCapability = {
    ...blocked, id: "siglip2_moderation_nsfw", display_name: "SigLIP2 Moderation — NSFW",
    availability: "available", reason: null, device: "cpu", weight_requirement: "on_demand",
  };
  const fallback = installProductionApi();
  vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
    if (new URL(String(input), "http://editarr.test").pathname === "/api/models") {
      return Promise.resolve(json({
        artifacts: [
          artifact("wd-default", blocked.id, "ready"),
          artifact("wd-alternate", blocked.id, "not_downloaded"),
          artifact("siglip2-head", available.id, "not_downloaded"),
        ],
        model_capabilities: [blocked, available],
      }));
    }
    return fallback(input, init);
  }));

  render(<AuthenticationGate><ModelStorage /></AuthenticationGate>);

  const cached = within(await screen.findByRole("article", { name: "wd-default model" }));
  expect(cached.getByText("cached", { exact: true })).toBeVisible();
  expect(cached.getByText("A required model is pending license review for this build.")).toBeVisible();
  expect(cached.getByRole("button", { name: "Remove" })).toBeEnabled();

  const unavailable = within(screen.getByRole("article", { name: "wd-alternate model" }));
  expect(unavailable.getByText("unavailable", { exact: true })).toBeVisible();
  expect(unavailable.queryByRole("button", { name: "Download" })).toBeNull();

  const supported = within(screen.getByRole("article", { name: "siglip2-head model" }));
  expect(supported.getByText("cpu", { exact: true })).toBeVisible();
  expect(supported.getByRole("button", { name: "Download" })).toBeEnabled();
  expect(screen.queryByText(/Windows|Linux|CUDA|deferred gap/)).toBeNull();
});
