import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReviewCommunitySource, SavedReview } from "../api/api-review-types";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import { masterLink } from "../scans/community-master-test-fixtures";
import { completeScan } from "../test/app-test-fixtures";
import { communityEntries } from "./review-community";
import { createReviewDraft, type ReviewDraft } from "./review-draft";
import { ReviewSourcesMenu } from "./review-sources-menu";

afterEach(cleanup);

const snapshot = (overrides: Partial<ReviewCommunitySource> = {}): ReviewCommunitySource => ({
  source_id: "src-1", link_id: "link-1", community_version_id: "v_12ab", label: "Theatrical", tier: "2", match_percent: 80,
  master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current", link_state: "active",
  filter_count: 34, ...overrides,
});
const audio = completeScan({ id: "scan-a", scan_id: 1, name: "Audio test scan", scan_audio: true, scan_video: false,
  audio_model: "whisper", finding_count: 18, scan_started_at: new Date(new Date().getFullYear(), 7, 19, 12).toISOString() });
const combined = completeScan({ id: "scan-c", scan_id: 2, name: "Combined scan", scan_audio: true, scan_video: true,
  video_model: "Shieldstral", finding_count: 1 });

function Harness({ sources = [snapshot()], links = [masterLink()], memberships = [{ scan_id: audio.id, role: "audio" as const }],
  onToggleScan = vi.fn(), onRetryVotes, votesFailed }: {
  sources?: ReviewCommunitySource[]; links?: MasterLinkSummary[]; memberships?: SavedReview["memberships"];
  onToggleScan?: (scan: typeof audio, included: boolean) => void; onRetryVotes?: () => void; votesFailed?: () => boolean;
}) {
  const [draft, setDraft] = useState<ReviewDraft>(() => createReviewDraft({
    id: "review-1", name: "Review", revision: 1, memberships, filters: [], community_sources: sources,
    synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "file_end" }, mcf_document: null, accepted_credits: null,
  } as unknown as SavedReview));
  return <><ReviewSourcesMenu scans={[audio, combined]} draft={draft} entries={communityEntries(draft, sources, links)} maySearch
    disabled={false} onToggleScan={onToggleScan} onChangeRole={vi.fn()} onChange={setDraft}
    votesFailed={votesFailed} onRetryVotes={onRetryVotes} /><button type="button">Elsewhere</button></>;
}
const sourcesButton = () => screen.getByRole("button", { name: /^Sources · / });
const popover = () => screen.getByRole("dialog", { name: "Included scans" });

it("counts included sources and opens a dialog listing each with its details, moving focus in", () => {
  render(<Harness />);
  expect(sourcesButton()).toHaveAccessibleName("Sources · 2 of 3");
  expect(sourcesButton()).toHaveAttribute("aria-haspopup", "dialog");
  expect(sourcesButton()).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(sourcesButton());
  expect(sourcesButton()).toHaveAttribute("aria-expanded", "true");
  const scan = within(popover()).getByRole("checkbox", { name: "Audio test scan" });
  expect(scan).toBeChecked();
  expect(scan).toHaveFocus();
  expect(scan).toHaveAccessibleDescription("Audio · whisper · 18 filters · Aug 19");
  expect(within(popover()).getByRole("checkbox", { name: "Combined scan" })).not.toBeChecked();
  expect(within(popover()).getByRole("checkbox", { name: "Community · Theatrical" }))
    .toHaveAccessibleDescription("Community · 34 filters · revision 12 · aligned");
  expect(popover()).toHaveTextContent("Changes apply when you save the review.");
});

it("includes scans and community filters with today's semantics, offering a combined scan's role once included", () => {
  const onToggleScan = vi.fn();
  render(<Harness memberships={[{ scan_id: combined.id, role: "both" }]} sources={[]} onToggleScan={onToggleScan} />);
  fireEvent.click(sourcesButton());
  expect(within(popover()).getByLabelText("Review role for Combined scan · Scan #2")).toHaveValue("both");
  fireEvent.click(within(popover()).getByRole("checkbox", { name: "Audio test scan" }));
  expect(onToggleScan).toHaveBeenCalledWith(audio, true);
  fireEvent.click(within(popover()).getByRole("checkbox", { name: "Community · Theatrical" }));
  expect(within(popover()).getByRole("checkbox", { name: "Community · Theatrical" })).toBeChecked();
  expect(within(popover()).getByText(/Saving the review adds these community filters to the results/)).toBeInTheDocument();
  expect(sourcesButton()).toHaveAccessibleName("Sources · 2 of 3");
});

it("marks changed community filters on the button and stages their refresh in context", () => {
  render(<Harness sources={[snapshot({ state: "changed" })]} />);
  expect(sourcesButton()).toHaveAccessibleName("Sources · 2 of 3, community filters changed");
  fireEvent.click(sourcesButton());
  expect(within(popover()).getByRole("checkbox", { name: "Community · Theatrical" }))
    .toHaveAccessibleDescription(/Community filters changed since included\./);
  fireEvent.click(within(popover()).getByRole("button", { name: "Refresh community filters" }));
  expect(within(popover()).getByText(/Refresh staged\. Save the review to bring in the community changes/)).toBeInTheDocument();
  expect(sourcesButton()).toHaveAccessibleName("Sources · 2 of 3");
});

it("shows alignment, link and vote-loading states with their actions", () => {
  const onRetryVotes = vi.fn();
  render(<Harness sources={[snapshot({ link_id: "link-1" })]} onRetryVotes={onRetryVotes} votesFailed={() => true}
    links={[masterLink({ linkState: "merged" }), masterLink({ linkId: "link-2", label: "Extended", alignmentState: "required" })]} />);
  fireEvent.click(sourcesButton());
  expect(within(popover()).getByText("Merged")).toBeInTheDocument();
  const extended = within(popover()).getByRole("checkbox", { name: "Community · Extended" });
  expect(extended).toBeDisabled();
  expect(extended).toHaveAccessibleDescription(/needs alignment.*Align these filters in the Community tab before including them\./);
  fireEvent.click(within(popover()).getByRole("button", { name: "Retry loading votes" }));
  expect(onRetryVotes).toHaveBeenCalledTimes(1);
});

it("closes on Done, Escape and outside presses, returning focus to the button", () => {
  render(<Harness />);
  fireEvent.click(sourcesButton());
  fireEvent.click(within(popover()).getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(sourcesButton()).toHaveFocus();
  fireEvent.click(sourcesButton());
  fireEvent.keyDown(within(popover()).getByRole("checkbox", { name: "Audio test scan" }), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(sourcesButton()).toHaveFocus();
  fireEvent.click(sourcesButton());
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(sourcesButton()).toHaveFocus();
  fireEvent.click(sourcesButton());
  // Tabbing out of the non-modal dialog closes it, leaving focus where it went.
  fireEvent.blur(within(popover()).getByRole("checkbox", { name: "Audio test scan" }), { relatedTarget: screen.getByRole("button", { name: "Elsewhere" }) });
  expect(screen.queryByRole("dialog")).toBeNull();
});
