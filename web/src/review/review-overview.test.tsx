import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { SavedReview, Scan } from "../api";
import { useState } from "react";

import { SavedReviewOverview } from "./review-overview";
import { updateReviewFilter, type ReviewDraft } from "./review-draft";

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

it("renders a visual finding without a description by its category only", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const filters: SavedReview["filters"] = [{
    id: "finding-1", source_scan_id: "scan-1", source_finding_id: 1,
    source: "visual", category: "violence", subcategory: "violence",
    severity: "medium", mcf_channel: "video", mcf_comment: null,
    label: "d1_violence", details: null, group_key: "violence",
    start_seconds: 10, end_seconds: 12, confidence: 0.9,
    edit_action: "skip", selected: true,
  }];
  render(<SavedReviewOverview filters={filters} memberships={[]} scans={[]} busy={false} focusFilterId="finding-1"
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);

  const section = screen.getByRole("region", { name: "Video review filters" });
  const include = await within(section).findByRole("checkbox", { name: "Include violence at 0:10" });
  const row = include.closest("tr") as HTMLElement;
  expect(row.querySelector(".merged-descriptions")).toBeNull();
  expect(row).not.toHaveTextContent(/d1_violence|Unclassified/);
});

it("names manual visual filters by their label beside undescribed model findings", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const base = {
    source_finding_id: 1, source: "visual", category: "violence", subcategory: "violence",
    severity: "medium", mcf_channel: "video", mcf_comment: null, details: null, group_key: "violence",
    confidence: 0.9, edit_action: "skip", selected: true,
  } as const;
  const filters: SavedReview["filters"] = [
    { ...base, id: "model", source_scan_id: "scan-generated", label: "d1_violence", start_seconds: 10, end_seconds: 12 },
    { ...base, id: "manual-overlap", source_scan_id: "scan-manual", label: "Bar fight", start_seconds: 11, end_seconds: 13 },
    { ...base, id: "manual-alone", source_scan_id: "scan-manual", label: "Car chase", start_seconds: 30, end_seconds: 32 },
    { ...base, id: "model-alone", source_scan_id: "scan-generated", label: "d1_violence", start_seconds: 50, end_seconds: 52 },
  ];
  const scans = [
    { id: "scan-generated", scan_id: 1, origin: "generated", name: "Scan" },
    { id: "scan-manual", scan_id: 2, origin: "manual", name: "Manual" },
  ] as Scan[];
  render(<SavedReviewOverview filters={filters} memberships={[]} scans={scans} busy={false} focusFilterId="model-alone"
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);

  const section = screen.getByRole("region", { name: "Video review filters" });
  const mixed = await within(section).findByRole("checkbox", { name: "Include Bar fight at 0:10" });
  expect(mixed.closest("tr")).toHaveTextContent("Bar fight");
  const manual = within(section).getByRole("checkbox", { name: "Include Car chase at 0:30" });
  expect(manual.closest("tr")).toHaveTextContent("Car chase");
  const model = within(section).getByRole("checkbox", { name: "Include violence at 0:50" });
  expect((model.closest("tr") as HTMLElement).querySelector(".merged-descriptions")).toBeNull();
  for (const row of [mixed, manual, model]) expect(row.closest("tr")).not.toHaveTextContent("d1_violence");
});

it("shows a relabelled generated finding's label and follows a relabel in the editor", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const base = {
    source_scan_id: "scan-generated", source_finding_id: 1, source: "visual", category: "violence", subcategory: "violence",
    severity: "medium", mcf_channel: "video", mcf_comment: null, details: null, group_key: "violence",
    confidence: 0.9, edit_action: "skip", selected: true, label: "d1_violence", label_edited: false,
  } as const;
  const initial: SavedReview["filters"] = [
    { ...base, id: "relabelled", label: "Bar fight", label_edited: true, start_seconds: 10, end_seconds: 12 },
    { ...base, id: "untouched", start_seconds: 30, end_seconds: 32 },
  ];
  const scans = [{ id: "scan-generated", scan_id: 1, origin: "generated", name: "Scan" }] as Scan[];
  function Editable() {
    const [draft, setDraft] = useState({ filters: initial, filterBases: {} } as unknown as ReviewDraft);
    return <SavedReviewOverview filters={draft.filters} memberships={[]} scans={scans} busy={false} metadataEditable focusFilterId="untouched"
      onToggleFilters={vi.fn()} onChangeFilter={(id, patch) => setDraft((current) => updateReviewFilter(current, id, patch))} onDeleteFilter={vi.fn()}
      onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />;
  }
  render(<Editable />);

  const section = screen.getByRole("region", { name: "Video review filters" });
  const relabelled = await within(section).findByRole("checkbox", { name: "Include Bar fight at 0:10" });
  expect(relabelled.closest("tr")).toHaveTextContent("Bar fight");
  const untouched = within(section).getByRole("checkbox", { name: "Include violence at 0:30" });
  expect((untouched.closest("tr") as HTMLElement).querySelector(".merged-descriptions")).toBeNull();
  expect(untouched.closest("tr")).not.toHaveTextContent("d1_violence");

  fireEvent.change(await screen.findByLabelText("Filter untouched label"), { target: { value: "Car chase" } });
  const renamed = within(section).getByRole("checkbox", { name: "Include Car chase at 0:30" });
  expect(renamed.closest("tr")).toHaveTextContent("Car chase");
});

it("sorts mixed review filters by time and restores the collapsible categories", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [{
    token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }],
  }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const video: SavedReview["filters"][number] = {
    id: "late-video", source_scan_id: "scan-1", source_finding_id: 1, source: "visual",
    category: "violence", subcategory: "violence", severity: "medium", mcf_channel: "video", mcf_comment: null,
    label: "Fight", details: "Fight", group_key: "fight", start_seconds: 12, end_seconds: 14,
    confidence: null, edit_action: "skip", selected: true,
  };
  const audio: SavedReview["filters"][number] = {
    ...video, id: "early-audio", source_finding_id: 2, source: "audio",
    category: "language", subcategory: "swearing", mcf_channel: "audio", group_key: "fudge",
    label: "fudge", details: "oh fudge it", start_seconds: 1, end_seconds: 2, edit_action: "mute",
  };
  render(<SavedReviewOverview filters={[video, audio]} memberships={[]} scans={[]} busy={false}
    defaultSort="time" onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);

  const list = screen.getByRole("region", { name: "Filter results" });
  const timeline = screen.getByRole("region", { name: "Timeline summary" });
  expect(timeline).not.toContainElement(list);
  expect(within(list).getAllByRole("checkbox").map((checkbox) => checkbox.getAttribute("aria-label")))
    .toEqual(["Include fudge at 0:01", "Include Fight at 0:12"]);
  const show = screen.getByRole("combobox", { name: "Show" });
  fireEvent.change(show, { target: { value: "audio" } });
  expect(within(list).getByText("f*dg*")).toBeVisible();
  expect(within(list).queryByText("Fight")).not.toBeInTheDocument();
  expect(timeline).toHaveTextContent("2/2 results");

  fireEvent.change(show, { target: { value: "visual" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "category" } });
  const category = await within(list).findByRole("button", { name: /Violence \(General\) subcategory/ });
  expect(category).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(category);
  expect(await within(list).findByRole("button", { name: /fight finding group/ })).toBeVisible();
  expect(within(list).queryByRole("region", { name: "Audio review filters" })).not.toBeInTheDocument();
});

it("counts included community filters in the timeline summary and skipped time", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ families: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  const community = (id: string, start: number, edit_action: "skip" | "mute") => ({
    filter_id: id, category: "violence", subcategory: "punching", severity: "high", edit_action,
    start_seconds: start, end_seconds: start + 10, confidence: 0.5,
  });
  render(<SavedReviewOverview filters={[]} memberships={[]} scans={[]} busy={false}
    communityFilters={[community("c1", 10, "skip"), community("c2", 15, "skip"), community("c3", 60, "mute")]}
    onToggleFilters={vi.fn()} onChangeFilter={vi.fn()} onDeleteFilter={vi.fn()}
    onApplyDefaults={vi.fn()} onPreviewFilter={vi.fn()} preview={null} previewDisabled />);
  const summary = screen.getByRole("region", { name: "Timeline summary" });
  expect(summary).toHaveTextContent("Combined results from 0 selected scans and 3 community filters.");
  expect(summary).toHaveTextContent("3/3 results");
  expect(summary).toHaveTextContent("Skipped Time 0:15");
  expect(within(summary).getAllByRole("img", { name: /^selected (skip|mute) from/ })).toHaveLength(2);
});
