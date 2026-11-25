/**
 * Community filters are normal review filters (docs/community-local-api.md §3, ADR 0002 U1–U5): the
 * Review tab's Sources popover (Included scans) offers each master set, its filters join the normal results where they
 * are enabled, disabled and adjusted like scan filters, the Community tab only browses, votes and
 * downloads updates, and Refresh brings community changes in while keeping the user's edits.
 */
import { expect, test, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import { mockCommunityMasters } from "./community-masters-mock";
import { mockCommunityReview } from "./community-review-mock";
import { chooseExportFormat, chooseFilterAction, chooseWorkflowStep, openReviewSources, setReviewSource } from "./workflow-actions";

async function openReview(page: Page, name: string) {
  await page.goto("/#/library/movies/item/movie-1");
  await chooseWorkflowStep(page, "Review");
  const open = page.getByRole("button", { name: `Open review ${name}` });
  if (await open.isVisible()) await open.click();
}

async function check(page: Page, name: string, width: number) {
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  // Show each scrolling table from its first column.
  await page.evaluate(() => document.querySelectorAll("*").forEach((element) => { if (element.scrollLeft) element.scrollLeft = 0; }));
  await page.screenshot({ path: `test-results/cm-review-uniform/${name}-${width}.png`, fullPage: true });
}

const byTime = (page: Page) => page.getByRole("table", { name: "Review filters by time" });
const row = (page: Page, time: string) => byTime(page).getByRole("row").filter({ hasText: time });

for (const width of [320, 1440]) {
  test(`${width}px include, edit and refresh community filters as normal review filters`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, []));
    const community = await mockCommunityMasters(page, { linked: true });
    const saves = await mockCommunityReview(page, community);
    await openReview(page, "Community review");

    const sourcesButton = page.getByRole("button", { name: /^Sources · / });
    await expect(sourcesButton).toHaveAccessibleName("Sources · 0 of 1");
    const included = await openReviewSources(page);
    const entry = included.getByRole("checkbox", { name: "Community · Extended Edition" });
    await expect(entry).toBeFocused();
    await expect(entry).not.toBeChecked();
    await entry.check();
    await expect(included.getByText(/Saving the review adds these community filters to the results/)).toBeVisible();
    await expect(sourcesButton).toHaveAccessibleName("Sources · 1 of 1");
    // The popover fits the window and passes axe; Escape closes it and returns focus to the button.
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    const box = await included.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `test-results/cm-review-uniform/review-sources-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(included).toHaveCount(0);
    await expect(sourcesButton).toBeFocused();
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect.poll(() => saves[0]?.community_memberships).toEqual([{ link_id: "link-v_extended" }]);

    await page.getByRole("combobox", { name: "Sort" }).selectOption("time");
    await expect(byTime(page).getByText("Community · Extended Edition")).toHaveCount(3);
    await expect(row(page, "10:00").getByRole("checkbox")).toBeChecked();
    await expect(row(page, "15:00").getByRole("checkbox")).toBeChecked();
    // Low Confidence filters arrive disabled.
    await expect(row(page, "20:00").getByRole("checkbox")).not.toBeChecked();
    await expect(page.getByRole("region", { name: "Timeline summary" })).toContainText("0 selected scans and 3 community filters");
    await check(page, "review-community-included", width);

    // Each community row votes right beside its Preview, immediately and outside the review's save.
    const vote = (time: string) => row(page, time).getByRole("button", { name: /^Your vote: / });
    await expect(vote("10:00")).toHaveAttribute("data-vote", "up");
    await expect(vote("15:00")).toHaveAttribute("data-vote", "none");
    await expect(vote("20:00")).toHaveAttribute("data-vote", "none");
    await vote("20:00").click();
    const menu = page.getByRole("menu", { name: /^Vote for / });
    await expect(menu.getByRole("menuitemradio", { name: "No vote" })).toBeFocused();
    await menu.getByRole("menuitemradio", { name: "Thumbs down" }).click();
    await expect(vote("20:00")).toHaveAttribute("data-vote", "down");
    // Preview and the vote sit side by side, and action labels never break mid-word.
    const preview = row(page, "20:00").getByRole("button", { name: /^Preview filter/ });
    const [previewBox, voteBox] = [await preview.boundingBox(), await vote("20:00").boundingBox()];
    expect(Math.abs(previewBox!.y + previewBox!.height / 2 - (voteBox!.y + voteBox!.height / 2))).toBeLessThan(4);
    expect(voteBox!.x).toBeGreaterThan(previewBox!.x + previewBox!.width);
    const more = row(page, "20:00").getByRole("button", { name: /^More actions for .* at 20:00$/ });
    const moreBox = await more.boundingBox();
    expect(Math.abs(moreBox!.y + moreBox!.height / 2 - (voteBox!.y + voteBox!.height / 2))).toBeLessThan(4);
    expect(moreBox!.x).toBeGreaterThan(voteBox!.x + voteBox!.width);
    for (const button of [preview, more]) {
      expect(await button.evaluate((element) => getComputedStyle(element).whiteSpace === "nowrap" && element.scrollWidth <= element.clientWidth)).toBe(true);
    }
    // Edit and Delete sit in the row's "⋯" menu, a keyboard-operable menu button.
    await more.focus();
    await page.keyboard.press("Enter");
    const rowMenu = page.getByRole("menu", { name: /^More actions for .* at 20:00$/ });
    await expect(rowMenu.getByRole("menuitem", { name: /^Edit filter/ })).toBeFocused();
    await expect(rowMenu.getByRole("menuitem", { name: /^Delete filter/ })).toBeEnabled();
    await expectNoAccessibilityViolations(page);
    await page.keyboard.press("Escape");
    await expect(rowMenu).toHaveCount(0);
    await expect(more).toBeFocused();
    expect(community.requests.filter((request) => request.path.endsWith("/vote")).map((request) => [request.path, request.body]))
      .toEqual([["masters/link-v_extended/filters/f_gone/vote", expect.objectContaining({ vote: "down", confirmed: true })]]);
    await expect(page.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    await vote("15:00").focus();
    await page.keyboard.press("ArrowDown");
    await expect(menu).toBeVisible();
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/cm-review-votes/review-vote-menu-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(vote("15:00")).toBeFocused();
    await check(page, "review-votes", width);

    // Disable one and adjust another exactly like a scan filter.
    await row(page, "10:00").getByRole("checkbox").uncheck();
    await chooseFilterAction(page, row(page, "15:00"), /^Edit filter/);
    await page.getByRole("spinbutton", { name: /start seconds/ }).fill("905");
    await page.getByRole("button", { name: "Done" }).click();
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect.poll(() => saves[1]?.filters.find((filter) => filter.community_filter_id === "f_lang")?.start_seconds).toBe(905);
    // The language filter carries its word as its label; adjusting it is an edit, turning one off isn't.
    expect(saves[1].filters.find((filter) => filter.community_filter_id === "f_lang")).toMatchObject({ label: "damn", community_edited: true });
    expect(saves[1].filters.find((filter) => filter.community_filter_id === "f_fight"))
      .toMatchObject({ selected: false, community_edited: false });
    await expect(byTime(page).getByText("Edited")).toHaveCount(1);
    await check(page, "review-community-edited", width);

    // The Community tab is read-only for decisions and only downloads the newer filters.
    await chooseWorkflowStep(page, "Community");
    const panel = page.getByRole("region", { name: "Community filters", exact: true });
    await panel.getByRole("button", { name: "Show filters" }).click();
    for (const name of [/^Confirm/, /^Reject/, /^Reset/]) await expect(panel.getByRole("button", { name })).toHaveCount(0);
    await panel.getByRole("button", { name: "Check for filter updates" }).click();
    await expect(panel.getByText(/Downloaded the latest community filters/)).toBeVisible();
    await expect(panel.getByRole("button", { name: "Review" })).toHaveCount(0);
    await check(page, "community-tab", width);

    await chooseWorkflowStep(page, "Review");
    // The Sources button marks the change; its popover explains it beside the set.
    await expect(sourcesButton).toHaveAccessibleName("Sources · 1 of 1, community filters changed");
    await openReviewSources(page);
    await expect(included.getByText("Community filters changed since included.")).toBeVisible();
    // The review keeps its filters until the user refreshes them.
    await expect(byTime(page).getByText("Community · Extended Edition")).toHaveCount(3);
    expect(saves).toHaveLength(2);
    await check(page, "review-refresh-available", width);
    await included.getByRole("button", { name: "Refresh community filters" }).click();
    await expect(included.getByText(/Refresh staged\. Save the review to bring in the community changes/)).toBeVisible();
    await expect(included.getByRole("checkbox", { name: "Community · Extended Edition" })).toBeFocused();
    await included.getByRole("button", { name: "Done" }).click();
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect.poll(() => saves[2]?.community_memberships).toEqual([{ source_id: "snap-1", refresh: true }]);
    await expect(sourcesButton).toHaveAccessibleName("Sources · 1 of 1");
    await openReviewSources(page);
    await expect(included.getByText("Community filters changed since included.")).toHaveCount(0);
    await included.getByRole("button", { name: "Done" }).click();
    await expect(byTime(page).getByText("Community · Extended Edition")).toHaveCount(4);
    // Edits stay, and a filter turned off stays off; the filter no longer served is flagged and stays
    // disabled; the new one arrives enabled.
    await expect(row(page, "10:00").getByRole("checkbox")).not.toBeChecked();
    await expect(row(page, "15:05")).toContainText("Edited");
    await expect(row(page, "20:00")).toContainText("Removed from community");
    await expect(row(page, "20:00").getByRole("checkbox")).not.toBeChecked();
    await expect(row(page, "30:00").getByRole("checkbox")).toBeChecked();
    // A filter no longer served can't be voted on but still shows the last vote; the new one can.
    await expect(vote("20:00")).toHaveAttribute("aria-disabled", "true");
    await expect(vote("20:00")).toHaveAttribute("data-vote", "down");
    await expect(vote("20:00")).toHaveAccessibleDescription(/no longer serves this filter/);
    await expect(vote("30:00")).toHaveAttribute("data-vote", "none");
    await expect(vote("30:00")).not.toHaveAttribute("aria-disabled");
    // The filter table, actions included, fits without sideways scrolling at every width.
    expect(await byTime(page).evaluate((table) => table.parentElement!.scrollWidth - table.parentElement!.clientWidth)).toBe(0);
    await check(page, "review-refreshed-removed", width);
    // The flags show in the row's details line, visible without scrolling the table sideways.
    const flag = row(page, "20:00").getByText("Removed from community");
    await flag.evaluate((element) => element.scrollIntoView({ block: "center", inline: "nearest" }));
    await expect(flag).toBeInViewport({ ratio: 1 });
    const edited = row(page, "15:05").getByText("Edited");
    await edited.evaluate((element) => element.scrollIntoView({ block: "nearest", inline: "nearest" }));
    await expect(edited).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: `test-results/cm-review-uniform/review-removed-flag-${width}.png` });
  });
}

for (const width of [320, 1440]) {
  test(`${width}px a review with only community filters can be exported`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, []));
    const community = await mockCommunityMasters(page, { linked: true });
    await mockCommunityReview(page, community, "Community only");
    await openReview(page, "Community only");
    await setReviewSource(page, "Community · Extended Edition");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect(page.getByText("Community · Extended Edition").first()).toBeVisible();
    await chooseWorkflowStep(page, "Subtitles");
    await expect(page.getByText(/community mute/)).toHaveCount(0);
    const exportButton = await chooseExportFormat(page, "EDL");
    await exportButton.click();
    const dialog = page.getByRole("dialog", { name: "Export" });
    await expect(dialog.getByRole("button", { name: "Download EDL" })).toBeEnabled();
    await check(page, "community-only-export", width);
  });
}
