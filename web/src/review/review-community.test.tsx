import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import type { ReviewCommunitySource, ReviewFilter, SavedReview } from "../api/api-review-types";
import { masterLink } from "../scans/community-master-test-fixtures";
import { communityEntries, hasExportEvidence, setCommunityIncluded, stageCommunityRefresh } from "./review-community";
import { ReviewCommunityEntries } from "./review-community-sources";
import { CommunityFilterSource } from "./review-community-badges";
import { createReviewDraft, isReviewDraftDirty, type ReviewDraft } from "./review-draft";

afterEach(cleanup);

const source = (overrides: Partial<ReviewCommunitySource> = {}): ReviewCommunitySource => ({
  source_id: "src-1", link_id: "link-1", community_version_id: "v_12ab", label: "Theatrical", tier: "2", match_percent: 80,
  master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current", link_state: "active",
  filter_count: 2, ...overrides,
});
const communityFilter = (id: string, overrides: Partial<ReviewFilter> = {}): ReviewFilter => ({
  id: `row-${id}`, source_scan_id: null, source_finding_id: null, community_source_id: "src-1", community_filter_id: id,
  source: "visual", category: "violence", subcategory: "punching", severity: "high", mcf_channel: "both", mcf_comment: null,
  label: null, details: null, group_key: null, start_seconds: 10, end_seconds: 15, confidence: 0.5, edit_action: "skip",
  selected: true,
  community: { source_id: "src-1", filter_id: id, label: "Theatrical", master_revision: 12, confidence: 0.5,
    contributor_count: 3, low_confidence: false, removed: false, edited: false, locally_verified: false },
  ...overrides,
});
const review = (sources: ReviewCommunitySource[], filters: ReviewFilter[] = []) => ({
  id: "review-1", name: "Review", revision: 1, content_revision: 1, edl_content_revision: 1, mcf_content_revision: 1,
  memberships: [], filters, synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "file_end" },
  mcf_document: null, accepted_credits: null, community_sources: sources,
}) as unknown as SavedReview;

it("folds the saved snapshot's link into one entry and reads the server's changed state", () => {
  const draft = createReviewDraft(review([source()]));
  const links = [masterLink(), masterLink({ linkId: "link-2", label: "Extended" })];
  expect(communityEntries(draft, [source()], links).map((entry) => [entry.key, entry.included, entry.changed]))
    .toEqual([["src-1", true, false], ["link-2", false, false]]);
  expect(communityEntries(draft, [source({ state: "changed" })], links)[0].changed).toBe(true);
});

it("includes, refreshes and removes snapshots with their filters through the draft", () => {
  const filters = [communityFilter("f1"), communityFilter("f2", { selected: false })];
  const draft = createReviewDraft(review([source({ state: "changed" })], filters));
  const [stored, extended] = communityEntries(draft, [source({ state: "changed" })],
    [masterLink(), masterLink({ linkId: "link-2", label: "Extended" })]);
  const refreshed = stageCommunityRefresh(draft, stored);
  expect(refreshed.communityMemberships).toEqual([{ source_id: "src-1", refresh: true }]);
  expect(isReviewDraftDirty(refreshed)).toBe(true);
  const added = setCommunityIncluded(refreshed, extended, true);
  expect(added.communityMemberships).toEqual([{ source_id: "src-1", refresh: true }, { link_id: "link-2" }]);
  const removed = setCommunityIncluded(added, stored, false);
  expect(removed.communityMemberships).toEqual([{ link_id: "link-2" }]);
  expect(removed.filters).toEqual([]);
  const restored = setCommunityIncluded(removed, stored, true);
  expect(restored.communityMemberships).toContainEqual({ source_id: "src-1" });
  expect(restored.filters.map((value) => [value.community_filter_id, value.selected])).toEqual([["f1", true], ["f2", false]]);
});

it("needs a scan or a community filter to export", () => {
  expect(hasExportEvidence(review([source()]))).toBe(false);
  expect(hasExportEvidence(review([source()], [communityFilter("f1")]))).toBe(true);
});

function Entries({ sources, links, filters = [] }: { sources: ReviewCommunitySource[]; links: MasterLinkSummary[]; filters?: ReviewFilter[] }) {
  const [draft, setDraft] = useState<ReviewDraft>(() => createReviewDraft(review(sources, filters)));
  return <ReviewCommunityEntries entries={communityEntries(draft, sources, links)} draft={draft} disabled={false} maySearch onChange={setDraft} />;
}

it("offers Refresh only for a changed, refreshable snapshot and explains the staged refresh", () => {
  render(<Entries sources={[source({ state: "changed" })]} links={[masterLink()]} />);
  expect(screen.getByText("Community filters changed since included.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh community filters" }));
  expect(screen.getByText(/Refresh staged\. Save the review to bring in the community changes; your edits are kept\./)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Refresh community filters" })).toBeNull();
});

it("blocks including a link that awaits alignment and explains a new inclusion", () => {
  render(<Entries sources={[]} links={[masterLink({ alignmentState: "required" }), masterLink({ linkId: "link-2", label: "Extended" })]} />);
  expect(screen.getByRole("checkbox", { name: "Community filters · Theatrical" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Community filters · Extended" }));
  expect(screen.getByText(/Saving the review adds these community filters to the results/)).toBeInTheDocument();
});

it("labels a community filter's source with its version, confidence, contributors and flags", () => {
  render(<CommunityFilterSource filters={[communityFilter("f1", { community: { ...communityFilter("f1").community!,
    edited: true, removed: true, locally_verified: true } })]} />);
  expect(screen.getByText("Community · Theatrical")).toBeInTheDocument();
  expect(screen.getByText("50% confidence")).toBeInTheDocument();
  expect(screen.getByText("3 contributors")).toBeInTheDocument();
  expect(screen.getByText("Edited")).toBeInTheDocument();
  expect(screen.getByText("Removed from community")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Locally verified" })).toBeInTheDocument();
});
