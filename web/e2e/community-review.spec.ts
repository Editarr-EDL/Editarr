/**
 * Saved reviews include community filters as stored snapshots (docs/community-local-api.md §3): the
 * Review tab's Included scans offers each master set, results list the stored filters, and Community
 * tab changes only show a "changed" notice until the user refreshes and saves.
 */
import { expect, test, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi, mockReview } from "./app-fixtures";
import { mockCommunityMasters, type CommunityMastersMock } from "./community-masters-mock";
import { chooseExportFormat, chooseWorkflowStep } from "./workflow-actions";
import type { ReviewCommunityMembership } from "../src/api/api-review-types";

/** One saved review whose PUT stores community snapshots the way the backend does. */
async function mockCommunityReview(page: Page, community: CommunityMastersMock) {
  let review = { ...mockReview("review-c", "Community review", "source-1"), community_sources: [] as unknown[] };
  const saves: { community_memberships?: ReviewCommunityMembership[] }[] = [];
  let snapshots = 0;
  const snapshot = (sourceId: string) => ({ source_id: sourceId, link_id: community.state.link!.linkId,
    community_version_id: community.state.link!.communityVersionId, label: community.state.link!.label, tier: "4",
    match_percent: 30, master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current",
    link_state: "active", filters: community.snapshotFilters() });
  await page.route("**/api/library-items/movie-1/reviews*", (route) => route.request().method() === "GET"
    ? json(route, [review]) : route.fallback());
  await page.route("**/api/reviews/review-c", async (route) => {
    if (route.request().method() === "GET") return json(route, review);
    const body = route.request().postDataJSON();
    saves.push(body);
    const sources = (body.community_memberships ?? []).map((value: ReviewCommunityMembership) => value.link_id
      ? snapshot(`snap-${++snapshots}`)
      : value.refresh ? snapshot(value.source_id!) : review.community_sources.find((source) =>
        (source as { source_id: string }).source_id === value.source_id));
    review = { ...review, revision: review.revision + 1, content_revision: review.content_revision + 1,
      edl_content_revision: review.edl_content_revision + 1, community_sources: sources };
    return json(route, review);
  });
  return saves;
}

for (const width of [320, 1440]) {
  test(`${width}px include, flag and refresh community filters in a saved review`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, []));
    const community = await mockCommunityMasters(page, { linked: true });
    const saves = await mockCommunityReview(page, community);
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Review");
    const open = page.getByRole("button", { name: "Open review Community review" });
    if (await open.isVisible()) await open.click();

    const included = page.getByRole("complementary", { name: "Review scan evidence" });
    const entry = included.getByRole("checkbox", { name: "Community filters · Extended Edition" });
    await expect(entry).not.toBeChecked();
    await entry.check();
    const results = page.getByRole("region", { name: "Community review filters table" });
    await expect(results).toContainText("(not saved yet)");
    await expect(results).toContainText("Community · Extended Edition");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect.poll(() => saves[0]?.community_memberships).toEqual([{ link_id: "link-v_extended" }]);
    await expect(results).not.toContainText("(not saved yet)");
    await expect(results.getByRole("row")).toHaveCount(3);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/community-review-included-${width}.png`, fullPage: true });

    await chooseWorkflowStep(page, "Community");
    const panel = page.getByRole("region", { name: "Community filters", exact: true });
    await panel.getByRole("button", { name: "Show filters" }).click();
    await panel.getByRole("button", { name: /^Reject Language/ }).click();
    await expect(panel.getByText("Not applied")).toBeVisible();

    await chooseWorkflowStep(page, "Review");
    await expect(included.getByText("Community filters changed since included.")).toBeVisible();
    // The review keeps its stored snapshot until the user refreshes it.
    await expect(results.getByRole("row")).toHaveCount(3);
    expect(saves).toHaveLength(1);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/community-review-changed-${width}.png`, fullPage: true });
    await included.getByRole("button", { name: "Refresh community filters" }).click();
    await expect(included.getByText("Refresh staged. Save the review to store the current filters.")).toBeVisible();
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect.poll(() => saves[1]?.community_memberships).toEqual([{ source_id: "snap-1", refresh: true }]);
    await expect(included.getByText("Community filters changed since included.")).toHaveCount(0);
    await expect(results.getByRole("row")).toHaveCount(2);
  });
}

for (const width of [320, 1440]) {
  test(`${width}px a review with only community filters can be exported`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, []));
    const community = await mockCommunityMasters(page, { linked: true });
    const review = { ...mockReview("review-c", "Community only", "source-1"), community_sources: [{
      source_id: "snap-1", link_id: community.state.link!.linkId, community_version_id: "v_extended", label: "Extended Edition",
      tier: "4", match_percent: 30, master_revision: 12, alignment: null, captured_at: "2026-11-24T00:00:00", state: "current",
      link_state: "active", filters: community.snapshotFilters() }] };
    await page.route("**/api/library-items/movie-1/reviews*", (route) => route.request().method() === "GET"
      ? json(route, [review]) : route.fallback());
    await page.route("**/api/reviews/review-c", (route) => json(route, review));
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Review");
    const open = page.getByRole("button", { name: "Open review Community only" });
    if (await open.isVisible()) await open.click();
    await expect(page.getByRole("region", { name: "Community review filters table" })).toContainText("Community · Extended Edition");
    const exportButton = await chooseExportFormat(page, "EDL");
    await exportButton.click();
    const dialog = page.getByRole("dialog", { name: "Export" });
    await expect(dialog.getByRole("button", { name: "Download EDL" })).toBeEnabled();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/community-only-export-${width}.png`, fullPage: true });
  });
}
