import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { SavedReview } from "../api";
import { SavedReviewOverview } from "./review-overview";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each([false, true])("masks audio editor text unless metadata is editable (%s)", async (metadataEditable) => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const filter = {
    id: "audio-word", source_scan_id: "scan-1", source_finding_id: 1,
    source: "audio", category: "language", subcategory: "swearing", severity: "medium",
    mcf_channel: "audio", mcf_comment: null, label: "fudge", details: "oh fudge it", group_key: "fudge",
    start_seconds: 1, end_seconds: 2, confidence: null, edit_action: "mute", selected: true,
  } satisfies SavedReview["filters"][number];
  render(<SavedReviewOverview filters={[filter]} memberships={[]} scans={[]} busy={false}
    metadataEditable={metadataEditable} focusFilterId="audio-word"
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);
  await screen.findByRole("button", { name: "Edit filter audio-word" });
  if (metadataEditable) {
    expect(screen.getByLabelText("Filter audio-word label")).toHaveValue("fudge");
    expect(screen.getByLabelText("Filter audio-word description")).toHaveValue("oh fudge it");
  } else {
    expect(screen.queryByLabelText("Filter audio-word label")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Filter audio-word description")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("fudge")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("oh fudge it")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Audio filter label")).toHaveTextContent("f*dg*");
    expect(screen.getByLabelText("Audio filter description")).toHaveTextContent("*h f*dg* *t");
  }
});

it("keeps an expanded review tier open when category labels load", async () => {
  let resolveRegistry!: (response: Response) => void;
  const registryResponse = new Promise<Response>((resolve) => { resolveRegistry = resolve; });
  vi.stubGlobal("fetch", vi.fn(() => registryResponse));
  const filters: SavedReview["filters"] = [{
    id: "finding-1", source_scan_id: "scan-1", source_finding_id: 1,
    source: "visual", category: "violence", subcategory: "violence",
    severity: "medium", mcf_channel: "video", mcf_comment: null,
    label: "Fight", details: "Fight", group_key: "fight",
    start_seconds: 10, end_seconds: 12, confidence: 0.9,
    edit_action: "skip", selected: true,
  }];
  render(<SavedReviewOverview
    filters={filters} memberships={[]} scans={[]} busy={false}
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()}
    preview={null} previewDisabled={true}
  />);

  const section = screen.getByRole("region", { name: "Video review filters" });
  const subcategory = within(section).getByRole("button", { name: /subcategory, 1 result/ });
  fireEvent.click(subcategory);
  expect(within(section).getByRole("button", { name: /fight finding group/ })).toBeVisible();

  await act(async () => {
    resolveRegistry(new Response(JSON.stringify({ families: [{
      token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }],
    }] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await registryResponse;
  });

  expect(within(section).getByRole("button", { name: /Violence \(General\) subcategory/ })).toHaveAttribute("aria-expanded", "true");
  expect(within(section).getByRole("button", { name: /fight finding group/ })).toBeVisible();
});

it("renders server-decided selection and severity locks without disabling permitted metadata", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [{ token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence" }] }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const filter: SavedReview["filters"][number] = {
    id: "required", source_scan_id: "scan-1", source_finding_id: 1,
    source: "visual", category: "violence", subcategory: "violence", severity: "high",
    mcf_channel: "video", mcf_comment: null, label: "Fight", details: "Fight", group_key: "fight",
    start_seconds: 1, end_seconds: 2, confidence: null, edit_action: "skip", selected: true,
  };
  render(<SavedReviewOverview filters={[filter]} memberships={[]} scans={[]} busy={false}
    requiredFilterIds={["required"]} lockedSeverities={{ required: "high" }}
    focusFilterId="required" onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);
  expect(await screen.findByRole("button", { name: "Edit filter required" })).toBeVisible();
  expect(screen.getByRole("checkbox", { name: /Selected.*required by instance policy/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Delete filter required" })).toBeDisabled();
  expect(await screen.findByRole("radio", { name: "High" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Low" })).toBeDisabled();
  expect(screen.getByLabelText("Filter required label")).toBeEnabled();
  expect(screen.getByRole("region", { name: "Video review filters table" })).toHaveAttribute("tabindex", "0");
});

it("shows a registry load failure in the filter editor instead of loading forever", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Registry offline" }), { status: 500, headers: { "Content-Type": "application/json" } })));
  const filter: SavedReview["filters"][number] = {
    id: "editing", source_scan_id: "scan-1", source_finding_id: 1,
    source: "visual", category: "violence", subcategory: "violence", severity: "high",
    mcf_channel: "video", mcf_comment: null, label: "Fight", details: "Fight", group_key: "fight",
    start_seconds: 1, end_seconds: 2, confidence: null, edit_action: "skip", selected: true,
  };
  render(<SavedReviewOverview filters={[filter]} memberships={[]} scans={[]} busy={false} focusFilterId="editing"
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("Loading MCF categories…")).toBeNull();
});
