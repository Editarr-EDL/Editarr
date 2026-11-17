import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityFilterResults, type CommunityResultsProps } from "./community-filter-results";
import { communityResponse, mixedCandidate } from "../test/community-lookup-fixtures";
import { remoteFilterDisplay } from "./use-community-lookup";

vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(cleanup);
const downloadController = () => ({ allowed: true, entries: [], running: false, statusError: false, checking: false,
  start: vi.fn(), cancel: vi.fn(), reconcile: vi.fn(), retry: vi.fn() });

function lookup(candidates = [mixedCandidate]): CommunityResultsProps["lookup"] {
  return { key: "target", status: "ready", response: communityResponse(candidates), candidates,
    displayCandidates: candidates.map(remoteFilterDisplay), error: null, stale: false,
    start: vi.fn(), refresh: vi.fn(), more: vi.fn(), cancel: vi.fn() };
}

it("finds sortable scoped matches with a checkbox menu and expandable category counts", () => {
  const second = { ...mixedCandidate, revisionId: "a", summary: { ...mixedCandidate.summary, name: "Another scan", channels: { video: 1 } } };
  render(<CommunityFilterResults lookup={lookup([mixedCandidate, second])} />);
  const table = () => within(screen.getByRole("table", { name: "Community filter matches" }));
  expect(table().getAllByRole("row")[1]).toHaveTextContent("Another scan");
  fireEvent.click(table().getByRole("button", { name: "Scan" }));
  fireEvent.click(table().getByRole("button", { name: "Scan" }));
  expect(table().getAllByRole("row")[1]).toHaveTextContent("Mixed review");
  fireEvent.click(screen.getByRole("button", { name: "Match All" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "All grades" }));
  expect(screen.getByRole("checkbox", { name: "Strong fingerprint" })).toHaveAccessibleDescription("Loaded results: 1");
  expect(screen.getByRole("checkbox", { name: "Possible cut" })).toHaveAccessibleDescription("Loaded results: 1");
  fireEvent.click(screen.getByRole("checkbox", { name: "Possible cut" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Strong fingerprint" }));
  expect(table().getAllByRole("row")).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("Channel"), { target: { value: "audio" } });
  expect(table().getAllByRole("row")).toHaveLength(2);
  expect(screen.getByRole("checkbox", { name: "Strong fingerprint" })).toHaveAccessibleDescription("Loaded results: 0");
  expect(screen.getByRole("checkbox", { name: "Possible cut" })).toHaveAccessibleDescription("Loaded results: 1");
  fireEvent.click(table().getByRole("button", { name: /Mixed review/ }));
  expect(table().getByText("All filters by category")).toBeVisible();
  expect(table().getByText("Language (1)")).toBeVisible();
  expect(table().getByText("Swearing 1")).toBeVisible();
});

it("opens details with compared timing, genuine model and manual metadata, and restores focus", () => {
  const stream = { algorithm: "pcm", digest: "b".repeat(64), playback_start: "1/4", duration: "120",
    provenance: "analyzed" as const, description: null };
  const candidate = { ...mixedCandidate, summary: { ...mixedCandidate.summary,
    models: [{ capability: "audio_analysis", model_id: "Whisper" }], manual_filter_counts: { added: 1, edited: 2 } },
    evidence: { ...mixedCandidate.evidence, audio_stream: stream } };
  const state = lookup([candidate]);
  state.response!.targetEvidence = { ...state.response!.targetEvidence, audio_stream: { ...stream, playback_start: "0" } };
  render(<CommunityFilterResults lookup={state} />);
  fireEvent.click(screen.getByRole("button", { name: /Mixed review/ }));
  const trigger = screen.getByRole("button", { name: "Details for Mixed review" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Mixed review" });
  expect(within(dialog).getByText("Whisper")).toBeVisible();
  expect(within(dialog).getByText("Manually edited filters").parentElement).toHaveTextContent("2");
  expect(within(dialog).getByText("Manually added filters").parentElement).toHaveTextContent("1");
  expect(within(dialog).getByRole("table", { name: "Audio timing" })).toHaveTextContent("Playback start0.25 s0.000 s");
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("distinguishes legacy metadata from zero edits and treats remote labels as text", () => {
  const candidate = { ...mixedCandidate, catalogCreatedAt: Number.MAX_SAFE_INTEGER,
    attribution: { ...mixedCandidate.attribution, displayName: "<img src=x onerror=alert(1)>" } };
  const { rerender } = render(<CommunityFilterResults lookup={lookup([candidate])} />);
  fireEvent.click(screen.getByRole("button", { name: /Mixed review/ }));
  fireEvent.click(screen.getByRole("button", { name: "Details for Mixed review" }));
  expect(screen.getAllByText("Not recorded")).toHaveLength(2);
  expect(screen.getByText("No models recorded.")).toBeVisible();
  expect(screen.getByText(/Unknown date/)).toBeVisible();
  expect(document.querySelector("img")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  rerender(<CommunityFilterResults lookup={lookup([{ ...candidate, summary: { ...candidate.summary, manual_filter_counts: { added: 0, edited: 0 } } }])} />);
  fireEvent.click(screen.getByRole("button", { name: "Details for Mixed review" }));
  expect(screen.queryByText("Manually edited filters")).toBeNull();
  expect(screen.queryByText("Manually added filters")).toBeNull();
});

it("downloads only selected channels and invalidates uncertain consent when the target changes", () => {
  const downloads = downloadController();
  const state = lookup();
  const { rerender } = render(<CommunityFilterResults lookup={state} downloads={downloads} />);
  fireEvent.click(screen.getByRole("button", { name: "Download Mixed review" }));
  const dialog = screen.getByRole("dialog", { name: "Download filters" });
  expect(within(dialog).getByRole("checkbox", { name: /Video/ })).toBeChecked();
  const audio = within(dialog).getByRole("checkbox", { name: /Audio/ });
  expect(audio).not.toBeChecked();
  fireEvent.click(audio);
  const approve = within(dialog).getByRole("checkbox", { name: /I reviewed the timing/ });
  expect(within(dialog).getByRole("button", { name: "Download filters" })).toBeDisabled();
  fireEvent.click(approve);
  fireEvent.click(within(dialog).getByRole("button", { name: "Download filters" }));
  expect(downloads.start).toHaveBeenCalledWith([mixedCandidate], [expect.objectContaining({
    channels: ["video", "audio"], targetKey: "a".repeat(64), reviewRequired: ["audio"],
  })], true);
  fireEvent.click(screen.getByRole("button", { name: "Download Mixed review" }));
  rerender(<CommunityFilterResults lookup={{ ...state, key: "another", response: { ...communityResponse(), targetKey: "b".repeat(64) } }} downloads={downloads} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(downloads.start).toHaveBeenCalledOnce();
});

it("marks compatible scoped channels downloaded without treating an incompatible-only scope as complete", () => {
  const candidate = { ...mixedCandidate, compatibility: { ...mixedCandidate.compatibility,
    audio: { ...mixedCandidate.compatibility.audio, band: "incompatible" as const } } };
  const downloads: NonNullable<CommunityResultsProps["downloads"]> = { ...downloadController(), entries: [{
    id: "imported", input: { target: { source: "jellyfin", item_id: "video" } }, targetKey: "a".repeat(64),
    choice: { revisionId: candidate.revisionId, contributionId: candidate.contributionId, artifactDigest: candidate.artifactDigest, channels: ["video"] },
    label: "Mixed review", status: "imported", confirmed: false,
  }] };
  render(<CommunityFilterResults lookup={lookup([candidate])} downloads={downloads} />);
  expect(screen.getByRole("button", { name: "Downloaded Mixed review" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Channel"), { target: { value: "audio" } });
  expect(screen.getByRole("button", { name: "Download Mixed review" })).toBeDisabled();
  expect(downloads.start).not.toHaveBeenCalled();
});

it("keeps stale actions inactive and offers refresh, retry, paging, and honest empty states", () => {
  const state = lookup();
  const downloads = downloadController();
  const { rerender } = render(<CommunityFilterResults lookup={{ ...state, status: "error", stale: true, error: { code: "service_unavailable", retryable: true } }} downloads={downloads} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Community lookup is unavailable.");
  expect(screen.getByText("Previous results are stale.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Download Mixed review" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /^Thumbs up/ })).toBeDisabled();
  expect(screen.queryByText("No community filters were found.")).not.toBeInTheDocument();
  rerender(<CommunityFilterResults lookup={{ ...state, status: "cancelled" }} />);
  expect(screen.getByText("Community lookup cancelled.")).toBeVisible();
  const page = { ...communityResponse().page!, nextCursor: "more" };
  rerender(<CommunityFilterResults lookup={{ ...state, response: { ...communityResponse(), page } }} />);
  fireEvent.click(screen.getByRole("button", { name: "More community results" }));
  expect(state.more).toHaveBeenCalledOnce();
  rerender(<CommunityFilterResults lookup={lookup([])} />);
  expect(screen.getByText("No community filters were found.")).toBeVisible();
});
