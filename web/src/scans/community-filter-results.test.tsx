import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityFilterResults, type CommunityResultsProps } from "./community-filter-results";
import { communityResponse, mixedCandidate } from "../test/community-lookup-fixtures";
import { remoteFilterDisplay } from "./use-community-lookup";
import { defaultResultFilters, filterResults } from "./community-result-state";

vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(cleanup);
it("keeps quality inside both bands, sorts ties by revision and leaves legacy quality unknown", () => {
  const ranked = { ...mixedCandidate, revisionId: "b", ranking: { policyVersion: "community-ranking-wilson-1" as const,
    qualityScore: 0.207654955, voteCount: 1, uploaderBoost: 0 as const, score: 0.207654955 } };
  const tied = { ...ranked, revisionId: "a" };
  const wrongCut = { ...ranked, revisionId: "c", ranking: { ...ranked.ranking, score: 0.946540780 },
    compatibility: { ...ranked.compatibility, video: { ...ranked.compatibility.video, band: "possible_cut" as const } } };
  expect(filterResults([wrongCut, ranked, { ...tied, revisionId: "legacy", ranking: null }, tied],
    { ...defaultResultFilters, sort: "quality" }).map((candidate) => candidate.revisionId)).toEqual(["a", "b", "legacy", "c"]);
});
function lookup(): CommunityResultsProps["lookup"] {
  return { key: "target", status: "ready", response: communityResponse(), candidates: [mixedCandidate],
    displayCandidates: [remoteFilterDisplay(mixedCandidate)], error: null, stale: false,
    start: vi.fn(), refresh: vi.fn(), more: vi.fn(), cancel: vi.fn() };
}

it("starts all remote channels unchecked, separates mixed uncertainty and exposes provenance honestly", () => {
  const onSelectionChange = vi.fn();
  render(<CommunityFilterResults lookup={lookup()} onSelectionChange={onSelectionChange} />);
  const review = screen.getByRole("region", { name: "Review required results" });
  expect(within(review).getByText("Video: Strong fingerprint")).toBeVisible();
  expect(within(review).getByText("Audio: Possible cut")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "Select audio from Mixed review" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Select strong channels" }));
  expect(screen.getByRole("checkbox", { name: "Select video from Mixed review" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Select audio from Mixed review" })).not.toBeChecked();
  expect(onSelectionChange).toHaveBeenLastCalledWith([expect.objectContaining({ targetKey: "a".repeat(64), channels: ["video"], reviewRequired: [] })]);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select audio from Mixed review" }));
  expect(onSelectionChange).toHaveBeenLastCalledWith([expect.objectContaining({ reviewRequired: ["audio"] })]);
  fireEvent.click(screen.getByText("Evidence and provenance"));
  expect(screen.getByText("Models / settings")).toBeVisible();
  expect(screen.getByText("Unavailable")).toBeVisible();
  expect(screen.getByText("Analyzed audio: Unknown analyzed track")).toBeVisible();
});

it("distinguishes failed, stale, cancelled, empty and filtered results and disables stale selection", () => {
  const state = lookup();
  const { rerender } = render(<CommunityFilterResults lookup={{ ...state, status: "error", stale: true, error: { code: "service_unavailable", retryable: true } }} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Community lookup is unavailable.");
  expect(screen.getByText(/Previous results are stale/)).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "Select video from Mixed review" })).toBeDisabled();
  expect(screen.queryByText("No community filters were found.")).not.toBeInTheDocument();
  rerender(<CommunityFilterResults lookup={{ ...state, status: "cancelled" }} />);
  expect(screen.getByText("Community lookup cancelled.")).toBeVisible();
  rerender(<CommunityFilterResults lookup={{ ...state, candidates: [], response: communityResponse([]) }} />);
  expect(screen.getByText("No community filters were found.")).toBeVisible();
  rerender(<CommunityFilterResults lookup={state} />);
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "manual" } });
  expect(screen.getByText("No results match these filters.")).toBeVisible();
});

it("renders legacy/partial/zero findings and long untrusted labels without inventing metadata", () => {
  const state = lookup();
  const candidate = { ...mixedCandidate, catalogCreatedAt: Number.MAX_SAFE_INTEGER,
    attribution: { ...mixedCandidate.attribution, displayName: "<img src=x onerror=alert(1)>" },
    summary: { ...mixedCandidate.summary, schema_version: "community-summary-1" as const, name: "Long".repeat(120), kind: "scan" as const,
      total: 0, coverage: [{ channel: "video" as const, capability: "visual", state: "partial", intervals: [] }] } };
  render(<CommunityFilterResults lookup={{ ...state, candidates: [candidate] }} />);
  expect(screen.getByText(/Unknown date/)).toBeVisible();
  expect(screen.getByText(/visual \(partial\)/)).toBeVisible();
  expect(document.querySelector("img")).toBeNull();
  expect(screen.getByRole("heading", { name: candidate.summary.name })).toBeVisible();
});

it("filters type, language, channel, coverage, category and both independent bands", () => {
  render(<CommunityFilterResults lookup={lookup()} />);
  for (const [label, value] of [["Type", "derived"], ["Language", "unknown"], ["Channel", "audio"],
    ["Coverage", "unknown"], ["Category", "language/swearing"], ["Video band", "strong_fingerprint"], ["Audio band", "possible_cut"]]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(screen.getByRole("article", { name: "Mixed review" })).toBeVisible();
  }
  fireEvent.change(screen.getByLabelText("Audio band"), { target: { value: "incompatible" } });
  expect(screen.getByText("No results match these filters.")).toBeVisible();
  expect(screen.getByRole("option", { name: "Revision quality" })).toBeEnabled();
  expect(screen.getByRole("option", { name: "Most downloaded unavailable" })).toBeDisabled();
});