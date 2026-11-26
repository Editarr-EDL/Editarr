import { expect, test, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, mockApi } from "./app-fixtures";
import { generatedLibrary, mockLibraryCatalog } from "./library-catalog-mock";

const library = generatedLibrary(250);
let requests: string[] = [];

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  requests = await mockLibraryCatalog(page, library);
});

async function openLibrary(page: Page, width: number) {
  await page.setViewportSize({ width, height: width < 640 ? 844 : 900 });
  await page.goto("/#/library/movies");
  await expect(page.getByRole("heading", { name: "Movies", level: 1 })).toBeVisible();
  await expect(page.getByText("250 titles")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Showing 100 of 250" })).toBeVisible();
}

for (const width of [390, 1440]) {
  test(`browses posters continuously and jumps with the A–Z rail at ${width}px`, async ({ page }) => {
    await openLibrary(page, width);
    const posters = page.getByRole("list", { name: "Movies" }).getByRole("listitem");
    await expect(posters).toHaveCount(100);
    await expect(page.getByRole("button", { name: "Refresh library" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Posters" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("Poster size")).toBeVisible({ visible: width >= 640 });
    const firstBadge = page.locator(".poster-edl").first();
    const badgeWidth = (await firstBadge.boundingBox())?.width ?? 0;
    if (width < 640) expect(badgeWidth).toBeLessThanOrEqual(12);
    else expect(badgeWidth).toBeGreaterThan(40);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);

    const rail = page.getByRole("toolbar", { name: "Jump to letter" });
    await expect(rail.getByRole("button", { name: "Jump to numbers and symbols" })).toHaveAttribute("aria-current", "true");
    await rail.getByRole("button", { name: "Jump to T" }).click();
    await expect(page.getByRole("button", { name: "Open Tide Light 1" })).toBeFocused();
    await expect(posters).not.toHaveCount(100);
    await expect(rail.getByRole("button", { name: "Jump to T" })).toHaveAttribute("aria-current", "true");
    await rail.getByRole("button", { name: "Jump to T" }).focus();
    await page.keyboard.press("ArrowUp");
    await expect(rail.getByRole("button", { name: "Jump to S" })).toBeFocused();

    await page.locator(".load-more-footer").scrollIntoViewIfNeeded();
    await expect(page.getByRole("status").filter({ hasText: "Showing 250 of 250" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Load more" })).toHaveCount(0);
    await expect(posters).toHaveCount(250);
    expect(requests).toEqual(["0:100", "100:100", "200:100"]);
    await expectNoHorizontalPageOverflow(page);
  });

  test(`filters with search tokens and sorts from one menu at ${width}px`, async ({ page }) => {
    await openLibrary(page, width);
    const search = page.getByRole("combobox", { name: "Search titles or filters" });
    await search.fill("pg");
    const suggestions = page.getByRole("listbox", { name: "Search suggestions" });
    await expect(suggestions.getByRole("option", { name: "Rating: PG", exact: true })).toBeVisible();
    await expect(suggestions.getByRole("option", { name: "Rating: PG-13" })).toBeVisible();
    await expect(search).toHaveAttribute("aria-expanded", "true");
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await search.press("ArrowDown");
    await search.press("Enter");
    await expect(page.getByRole("button", { name: "Remove PG filter" })).toBeVisible();
    await expect(page.getByText("63 titles")).toBeVisible();
    await search.press("Backspace");
    await expect(page.getByRole("button", { name: "Remove PG filter" })).toHaveCount(0);
    await expect(page.getByText("250 titles")).toBeVisible();

    await page.getByRole("button", { name: "Sort by Title, ascending" }).click();
    const menu = page.getByRole("menu", { name: "Sort by Title, ascending" });
    await expect(menu.getByRole("menuitemradio", { name: "Title" })).toBeFocused();
    await expectNoAccessibilityViolations(page);
    await menu.getByRole("menuitemradio", { name: "Year" }).click();
    await expect(page.getByRole("button", { name: "Sort by Year, ascending" })).toBeFocused();
    await expect(page.getByRole("toolbar", { name: "Jump to letter" })).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Showing 100 of 250" })).toBeVisible();

    await page.getByRole("button", { name: "List" }).click();
    await expect(page.getByRole("columnheader", { name: "Year" })).toHaveAttribute("aria-sort", "ascending");
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}
