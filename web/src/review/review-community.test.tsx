import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import type { ReviewCommunityFilter, ReviewCommunitySource, SavedReview } from "../api/api-review-types";
import { masterLink } from "../scans/community-master-test-fixtures";
import { communityEntries, entryFilters, filtersDiffer, setCommunityIncluded, stageCommunityRefresh } from "./review-community";
import { ReviewCommunityEntries, ReviewCommunityResults } from "./review-community-sources";
import { createReviewDraft, isReviewDraftDirty, type ReviewDraft } from "./review-draft";

afterEach(cleanup);

const filter = (id: string, start: number, overrides: Partial<ReviewCommunityFilter> = {}): ReviewCommunityFilter => ({
  filter_id: id, category: "violence", subcategory: "punching", severity: "high", edit_action: "skip",
  start_seconds: start, end_seconds: start + 5, confidence: 0.5, ...overrides,
});
const source = (overrides: Partial<ReviewCommunitySource> = {}): ReviewCommunitySource => ({
  source_id: "src-1", link_id: "link-1", community_version_id: "v_12ab", label: "Theatrical", tier: "2", match_percent: 80,
  master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current", link_state: "active",
  filters: [filter("f1", 10), filter("f2", 40)], ...overrides,
});
const review = (sources: ReviewCommunitySource[]) => ({
  id: "review-1", name: "Review", revision: 1, content_revision: 1, edl_content_revision: 1, mcf_content_revision: 1,
  memberships: [], filters: [], synchronization: { start_seconds: 0, end_seconds: 100, end_mode: "file_end" },
  mcf_document: null, accepted_credits: null, community_sources: sources,
}) as unknown as SavedReview;

it("folds the saved snapshot's link into one entry and compares filters without confidence", () => {
  const draft = createReviewDraft(review([source()]));
  const live = { "link-1": [filter("f1", 10, { confidence: 0.9 }), filter("f2", 40)], "link-2": [filter("f9", 1)] };
  const entries = communityEntries(draft, [source()], [masterLink(), masterLink({ linkId: "link-2", label: "Extended" })], live);
  expect(entries.map((entry) => [entry.key, entry.included, entry.changed])).toEqual([["src-1", true, false], ["link-2", false, false]]);
  expect(filtersDiffer(source().filters, [filter("f1", 10)])).toBe(true);
});

it("includes, refreshes and removes snapshots through the draft", () => {
  const draft = createReviewDraft(review([source()]));
  const [stored, extended] = communityEntries(draft, [source()], [masterLink(), masterLink({ linkId: "link-2", label: "Extended" })],
    { "link-1": [filter("f1", 10)], "link-2": [filter("f9", 1)] });
  expect(stored.changed).toBe(true);
  const refreshed = stageCommunityRefresh(draft, stored);
  expect(refreshed.communityMemberships).toEqual([{ source_id: "src-1", refresh: true }]);
  expect(isReviewDraftDirty(refreshed)).toBe(true);
  const added = setCommunityIncluded(refreshed, extended, true);
  expect(added.communityMemberships).toEqual([{ source_id: "src-1", refresh: true }, { link_id: "link-2" }]);
  const removed = setCommunityIncluded(added, stored, false);
  expect(removed.communityMemberships).toEqual([{ link_id: "link-2" }]);
  const restored = setCommunityIncluded(removed, stored, true);
  expect(restored.communityMemberships).toContainEqual({ source_id: "src-1" });
  expect(entryFilters({ ...stored, refreshStaged: true }).preview).toBe(true);
  expect(entryFilters(stored).filters.map((value) => value.filter_id)).toEqual(["f1", "f2"]);
});

function Entries({ sources, links, live }: {
  sources: ReviewCommunitySource[]; links: MasterLinkSummary[]; live: Record<string, ReviewCommunityFilter[]>;
}) {
  const [draft, setDraft] = useState<ReviewDraft>(() => createReviewDraft(review(sources)));
  const entries = communityEntries(draft, sources, links, live);
  return <><ReviewCommunityEntries entries={entries} draft={draft} disabled={false} maySearch onChange={setDraft} />
    <ReviewCommunityResults entries={entries} /></>;
}

it("shows the changed notice and stages Refresh community filters", () => {
  render(<Entries sources={[source()]} links={[masterLink()]} live={{ "link-1": [filter("f1", 10)] }} />);
  expect(screen.getByRole("checkbox", { name: "Community filters · Theatrical" })).toBeChecked();
  expect(screen.getByText("Community filters changed since included.")).toBeVisible();
  const table = screen.getByRole("region", { name: "Community review filters table" });
  expect(table).toHaveTextContent("Community · Theatrical");
  expect(table).toHaveTextContent("50%");
  fireEvent.click(screen.getByRole("button", { name: "Refresh community filters" }));
  expect(screen.getByText("Refresh staged. Save the review to store the current filters.")).toBeVisible();
  expect(screen.getByRole("region", { name: "Community review filters table" })).toHaveTextContent("(not saved yet)");
});

it("blocks links that need alignment and explains unavailable or merged sets", () => {
  render(<Entries sources={[source({ link_id: null, state: "link_unavailable", label: "Director's Cut" })]}
    links={[masterLink({ alignmentState: "required" }), masterLink({ linkId: "link-3", label: "IMAX", linkState: "merged" })]} live={{}} />);
  const fuzzy = screen.getByRole("checkbox", { name: "Community filters · Theatrical" });
  expect(fuzzy).toBeDisabled();
  expect(fuzzy).toHaveAccessibleDescription("Align these filters in the Community tab before including them.");
  expect(screen.getByRole("checkbox", { name: "Community filters · Director's Cut" })).toHaveAccessibleDescription(/no longer available/);
  expect(screen.getByRole("checkbox", { name: "Community filters · IMAX" })).toBeEnabled();
  expect(screen.getByText("Merged")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Refresh community filters" })).not.toBeInTheDocument();
});
