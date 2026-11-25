import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchCommunityMaster, MasterApiError, voteMasterFilter } from "../api/api-community-masters";
import type { ReviewCommunitySource, ReviewFilter } from "../api/api-review-types";
import { masterFilter, masterView } from "../scans/community-master-test-fixtures";
import { SavedReviewOverview } from "./review-overview";
import { useReviewCommunityVotes, voteUnavailable } from "./use-review-community-votes";
import { communityEntries } from "./review-community";
import { ReviewCommunityEntries } from "./review-community-sources";
import { createReviewDraft } from "./review-draft";
import type { SavedReview } from "../api/api-review-types";

const capabilities = vi.hoisted(() => ({ granted: new Set(["community.search", "community.feedback.submit"]) }));
vi.mock("../auth/auth-gate", async (original) => ({
  ...await original<typeof import("../auth/auth-gate")>(), useCan: (capability: string) => capabilities.granted.has(capability),
}));
vi.mock("../shared/mcf-registry", async (original) => ({
  ...await original<typeof import("../shared/mcf-registry")>(), useMcfRegistry: () => ({ registry: null, error: null }),
}));
vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchCommunityMaster: vi.fn(), voteMasterFilter: vi.fn(),
}));

beforeEach(() => { capabilities.granted = new Set(["community.search", "community.feedback.submit"]); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const source = (overrides: Partial<ReviewCommunitySource> = {}): ReviewCommunitySource => ({
  source_id: "src-1", link_id: "link-1", community_version_id: "v_12ab", label: "Theatrical", tier: "2", match_percent: 80,
  master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current", link_state: "active",
  filter_count: 3, ...overrides,
});
const reviewFilter = (id: string, start: number, overrides: Partial<ReviewFilter> = {}): ReviewFilter => ({
  id: `row-${id}`, source_scan_id: null, source_finding_id: null, community_source_id: "src-1", community_filter_id: id,
  source: "visual", category: "violence", subcategory: "punching", severity: "high", mcf_channel: "both", mcf_comment: null,
  label: null, details: `Fight ${id}`, group_key: null, start_seconds: start, end_seconds: start + 5, confidence: 0.5,
  edit_action: "skip", selected: true,
  community: { source_id: "src-1", filter_id: id, label: "Theatrical", master_revision: 12, confidence: 0.5,
    contributor_count: 3, low_confidence: false, removed: false, locally_verified: false },
  ...overrides,
});
const scanFilter = reviewFilter("scan", 300, { id: "row-scan", community_source_id: null, community_filter_id: null,
  community: null, source_scan_id: "scan-1", details: "Scan fight" });

function Review({ filters, sources, onChangeFilter = vi.fn() }: {
  filters: ReviewFilter[]; sources: ReviewCommunitySource[]; onChangeFilter?: () => void;
}) {
  const votes = useReviewCommunityVotes(sources);
  const draft = createReviewDraft({ id: "review-1", name: "Review", revision: 1, memberships: [], filters,
    community_sources: sources } as unknown as SavedReview);
  return <><ReviewCommunityEntries entries={communityEntries(draft, sources, [])} draft={draft} disabled={false} maySearch
    onChange={vi.fn()} votesFailed={votes.loadFailed} onRetryVotes={votes.retry} /><SavedReviewOverview filters={filters} memberships={[]} scans={[]} busy={false} defaultSort="time"
    onToggleFilters={vi.fn()} onChangeFilter={onChangeFilter} onDeleteFilter={vi.fn()} onApplyDefaults={vi.fn()}
    onPreviewFilter={vi.fn()} preview={null} previewDisabled={false} renderFilterVote={votes.renderVote} /></>;
}
const row = (text: string) => screen.getAllByRole("row").find((item) => item.textContent?.includes(text))!;
const pillIn = (text: string) => within(row(text)).queryByRole("button", { name: /^Your vote/ });

it("shows each community row's vote, leaves scan rows without one and follows merged filters", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue(masterView([
    masterFilter({ filterId: "f_up", myVote: "up" }),
    masterFilter({ filterId: "f_none" }),
    masterFilter({ filterId: "f_merged", myVote: "down", mergedIds: ["f_old"] }),
  ]));
  vi.mocked(voteMasterFilter).mockResolvedValue({ version: 1, state: "completed", result: {} });
  const onChangeFilter = vi.fn();
  render(<Review sources={[source()]} onChangeFilter={onChangeFilter} filters={[
    reviewFilter("f_up", 10), reviewFilter("f_none", 100), reviewFilter("f_old", 200), scanFilter,
  ]} />);
  await waitFor(() => expect(pillIn("Fight f_up")).toHaveAttribute("data-vote", "up"));
  expect(fetchCommunityMaster).toHaveBeenCalledTimes(1);
  expect(pillIn("Fight f_none")).toHaveAttribute("data-vote", "none");
  expect(pillIn("Fight f_none")).toHaveAccessibleName("Your vote: none. Change vote for Fight f_none at 1:40");
  expect(pillIn("Fight f_old")).toHaveAttribute("data-vote", "down");
  expect(pillIn("Scan fight")).toBeNull();
  expect(within(row("Scan fight")).getByRole("button", { name: "Preview filter row-scan" })).toBeInTheDocument();

  // A row still keyed by an ID its served filter merged votes on that served filter, immediately.
  fireEvent.click(pillIn("Fight f_old")!);
  fireEvent.click(screen.getByRole("menuitemradio", { name: "No vote" }));
  await waitFor(() => expect(pillIn("Fight f_old")).toHaveAttribute("data-vote", "none"));
  expect(voteMasterFilter).toHaveBeenLastCalledWith("link-1", "f_merged", expect.objectContaining({ vote: "none" }), false);
  fireEvent.click(pillIn("Fight f_none")!);
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Thumbs up" }));
  await waitFor(() => expect(pillIn("Fight f_none")).toHaveAttribute("data-vote", "up"));
  expect(onChangeFilter).not.toHaveBeenCalled();
});

it("explains why a detached snapshot, an unserved filter or an account without permission can't vote", async () => {
  vi.mocked(fetchCommunityMaster).mockResolvedValue({ ...masterView([masterFilter({ filterId: "f_up", myVote: "up" })]),
    retiredVotes: { f_gone: "down" } });
  const filters = [reviewFilter("f_up", 10), reviewFilter("f_gone", 100),
    reviewFilter("f_x", 200, { community_source_id: "src-2" }), reviewFilter("f_y", 250, { community_source_id: "src-3" })];
  const sources = [source(), source({ source_id: "src-2", link_id: null }),
    source({ source_id: "src-3", link_id: "link-3", state: "link_unavailable", link_state: "replaced" })];
  const { unmount } = render(<Review sources={sources} filters={filters} />);
  await waitFor(() => expect(pillIn("Fight f_up")).toHaveAttribute("data-vote", "up"));
  expect(pillIn("Fight f_up")).not.toHaveAttribute("aria-disabled");
  expect(pillIn("Fight f_gone")).toHaveAttribute("aria-disabled", "true");
  // A filter no longer served keeps showing the last vote on it.
  expect(pillIn("Fight f_gone")).toHaveAttribute("data-vote", "down");
  expect(pillIn("Fight f_y")).toHaveAttribute("data-vote", "none");
  expect(pillIn("Fight f_gone")).toHaveAccessibleDescription(voteUnavailable.notServed);
  expect(pillIn("Fight f_x")).toHaveAccessibleDescription(voteUnavailable.detached);
  expect(pillIn("Fight f_y")).toHaveAccessibleDescription(voteUnavailable.detached);
  expect(fetchCommunityMaster).toHaveBeenCalledTimes(1);
  fireEvent.click(pillIn("Fight f_gone")!);
  expect(screen.queryByRole("menu")).toBeNull();
  unmount();

  capabilities.granted.delete("community.feedback.submit");
  render(<Review sources={sources} filters={filters} />);
  await waitFor(() => expect(pillIn("Fight f_up")).toHaveAttribute("data-vote", "up"));
  expect(pillIn("Fight f_up")).toHaveAccessibleDescription(voteUnavailable.permission);
});

it("explains a link the reviewer can't read and a refused vote", async () => {
  vi.mocked(fetchCommunityMaster).mockImplementation(async (linkId) => {
    if (linkId === "link-2") throw new MasterApiError("not_found", false, 404);
    return masterView([masterFilter({ filterId: "f_up" })]);
  });
  vi.mocked(voteMasterFilter).mockRejectedValue(new MasterApiError("vote_ineligible", false, 409, "self_vote"));
  render(<Review sources={[source(), source({ source_id: "src-2", link_id: "link-2" })]}
    filters={[reviewFilter("f_up", 10), reviewFilter("f_other", 100, { community_source_id: "src-2" })]} />);
  await waitFor(() => expect(pillIn("Fight f_other")).toHaveAccessibleDescription(/no longer available to you/));
  await waitFor(() => expect(pillIn("Fight f_up")).not.toHaveAttribute("aria-disabled"));
  fireEvent.click(pillIn("Fight f_up")!);
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Thumbs up" }));
  expect(await within(row("Fight f_up")).findByRole("alert")).toHaveTextContent("You can’t vote on your own filter.");
  expect(pillIn("Fight f_up")).toHaveAttribute("data-vote", "none");
});

it("offers Retry loading votes after a failed load and shows the votes once it succeeds", async () => {
  vi.mocked(fetchCommunityMaster).mockRejectedValueOnce(new MasterApiError("network_unavailable", true, 0))
    .mockResolvedValue(masterView([masterFilter({ filterId: "f_up", myVote: "up" })]));
  render(<Review sources={[source()]} filters={[reviewFilter("f_up", 10)]} />);
  const retry = await screen.findByRole("button", { name: "Retry loading votes" });
  expect(screen.getByText("Couldn’t load your votes on these filters.")).toBeInTheDocument();
  expect(pillIn("Fight f_up")).toHaveAccessibleDescription(voteUnavailable.loadFailed);
  fireEvent.click(retry);
  await waitFor(() => expect(pillIn("Fight f_up")).toHaveAttribute("data-vote", "up"));
  expect(pillIn("Fight f_up")).not.toHaveAttribute("aria-disabled");
  expect(screen.queryByRole("button", { name: "Retry loading votes" })).toBeNull();
});
