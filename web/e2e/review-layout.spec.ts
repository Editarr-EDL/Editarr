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

for (const width of [320, 390, 1100]) {
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
    // Stacked rows scroll with the page; wide screens keep the list's own capped scroll area.
    const results = page.getByRole("region", { name: "Filter results" });
    expect(await results.evaluate((element) => getComputedStyle(element).maxHeight)).toBe(width <= 900 ? "none" : "420px");
    const row = page.getByRole("table", { name: "Review filters by time" }).getByRole("row").filter({ hasText: "10:00" });
    const content = await row.getByRole("cell").nth(1).boundingBox();
    const actions = await row.getByRole("button", { name: /^Preview / }).boundingBox();
    // Actions sit beside the row's details on wide screens and under them, indented past the checkbox, on phones.
    if (width <= 900) {
      expect(actions!.y).toBeGreaterThanOrEqual(content!.y + content!.height - 1);
      // Below 380px they use the row's full width.
      if (width > 380) expect(actions!.x).toBeGreaterThanOrEqual(content!.x - 1);
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

test("the item workflow tabs fit their labels from phone to desktop widths", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/library/movies/item/movie-1");
  await expect(page.getByRole("tab", { name: "Review" })).toBeVisible();
  const clipped: string[] = [];
  for (let width = 320; width <= 1100; width += 20) {
    await page.setViewportSize({ width, height: 900 });
    const tabs = await page.getByRole("tablist", { name: "Library item workflow" }).getByRole("tab")
      .evaluateAll((elements) => elements.filter((element) => element.scrollWidth > element.clientWidth).map((element) => element.textContent));
    if (tabs.length) clipped.push(`${width}: ${tabs.join(", ")}`);
  }
  expect(clipped).toEqual([]);
});

test("a filter row's menus close once the row scrolls out of the filter list", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await mockApi(page);
  await page.route("**/api/scans/history", (route) => json(route, []));
  const community = await mockCommunityMasters(page, { linked: true });
  await mockCommunityReview(page, community);
  await page.goto("/#/library/movies/item/movie-1");
  await chooseWorkflowStep(page, "Review");
  await setReviewSource(page, "Community · Extended Edition");
  await page.getByRole("button", { name: "Save Changes" }).click();
  await page.getByRole("combobox", { name: "Sort" }).selectOption("time");
  // A short list so its first row can scroll out of view.
  await page.addStyleTag({ content: ".saved-review-filter-results { max-height: 90px !important; }" });
  const results = page.getByRole("region", { name: "Filter results" });
  const first = page.getByRole("table", { name: "Review filters by time" }).getByRole("row").filter({ hasText: "10:00" });
  await first.getByRole("button", { name: /^More actions for / }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await results.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(page.getByRole("menu")).toHaveCount(0);
  await results.evaluate((element) => { element.scrollTop = 0; });
  await first.getByRole("button", { name: /^Your vote: / }).click();
  await expect(page.getByRole("menu")).toBeVisible();
  await results.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(page.getByRole("menu")).toHaveCount(0);
});
