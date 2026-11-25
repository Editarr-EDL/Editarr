/**
 * The Review tab lays out without sideways scrolling: saved reviews, the Filters list in both sorts (its rows
 * wrap and their actions move under the row on phones) and the Sources popover all fit, and pass axe.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import { mockCommunityMasters } from "./community-masters-mock";
import { mockCommunityReview } from "./community-review-mock";
import { chooseWorkflowStep, openReviewSources, setReviewSource } from "./workflow-actions";

const overflow = (locator: Locator) => locator.evaluate((element) => element.scrollWidth - element.clientWidth);

async function expectFits(page: Page) {
  await expect.poll(() => overflow(page.locator(".saved-review-table-wrap"))).toBe(0);
  await expect.poll(() => overflow(page.getByRole("region", { name: "Filter results" }))).toBe(0);
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
}

for (const width of [390, 1100]) {
  test(`${width}px the Review tab fits without sideways scrolling`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, []));
    const community = await mockCommunityMasters(page, { linked: true });
    await mockCommunityReview(page, community);
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Review");
    await setReviewSource(page, "Community · Extended Edition");
    await page.getByRole("button", { name: "Save Changes" }).click();
    await expect(page.getByText("Community · Extended Edition").first()).toBeVisible();

    await page.getByRole("combobox", { name: "Sort" }).selectOption("time");
    await expectFits(page);
    const row = page.getByRole("table", { name: "Review filters by time" }).getByRole("row").filter({ hasText: "10:00" });
    const content = await row.getByRole("cell").nth(1).boundingBox();
    const actions = await row.getByRole("button", { name: /^Preview filter/ }).boundingBox();
    // Actions sit beside the row's details on wide screens and under them, indented past the checkbox, on phones.
    if (width <= 900) {
      expect(actions!.y).toBeGreaterThanOrEqual(content!.y + content!.height - 1);
      expect(actions!.x).toBeGreaterThanOrEqual(content!.x - 1);
    } else expect(actions!.x).toBeGreaterThanOrEqual(content!.x + content!.width - 1);

    await page.getByRole("combobox", { name: "Sort" }).selectOption("category");
    const video = page.getByRole("region", { name: "Video review filters" });
    for (const level of ["subcategory", "finding group"]) {
      const toggles = video.getByRole("button", { name: new RegExp(` ${level}, `) });
      for (let index = 0; index < await toggles.count(); index += 1) {
        if (await toggles.nth(index).getAttribute("aria-expanded") === "false") await toggles.nth(index).click();
      }
    }
    await expect(video.getByRole("checkbox", { name: /^Include / }).first()).toBeVisible();
    await expectFits(page);

    const sources = await openReviewSources(page);
    await expect(sources.getByRole("checkbox", { name: "Community · Extended Edition" })).toBeFocused();
    const box = await sources.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    if (width <= 900) {
      // On phones the popover spans the Filters toolbar.
      const toolbar = await page.locator(".review-filter-toolbar").boundingBox();
      expect(Math.abs(box!.x - toolbar!.x)).toBeLessThan(2);
      expect(Math.abs(box!.width - toolbar!.width)).toBeLessThan(2);
    }
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}
