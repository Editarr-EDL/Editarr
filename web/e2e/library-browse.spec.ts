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

    await page.mouse.wheel(0, 2500);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
    await search.fill("edl exp");
    await page.getByRole("option", { name: "EDL: Exported" }).click();
    await expect(search).toBeFocused();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(400);
    await expect(page.getByRole("status").filter({ hasText: /^Showing \d+ of \d+$/ })).toBeVisible();
    await page.getByRole("button", { name: "Remove EDL exported filter" }).click();
    await expect(page.getByText("250 titles")).toBeVisible();

    await page.getByRole("button", { name: "Sort by Title, ascending" }).click();
    const menu = page.getByRole("menu", { name: "Sort by Title, ascending" });
    await expect(menu.getByRole("menuitemradio", { name: "Title" })).toBeFocused();
    await expectNoAccessibilityViolations(page);
    await menu.getByRole("menuitemradio", { name: "Year" }).click();
    await expect(page.getByRole("button", { name: "Sort by Year, ascending" })).toBeFocused();
    await expect(page.getByRole("toolbar", { name: "Jump to letter" })).toHaveCount(0);
    await expect(page.getByRole("toolbar", { name: "Jump to decade" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "Showing 100 of 250" })).toBeVisible();

    await page.getByRole("button", { name: "List" }).click();
    await expect(page.getByRole("columnheader", { name: "Year" })).toHaveAttribute("aria-sort", "ascending");
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}

/** Choose a sort and direction from the sort menu. */
async function sortBy(page: Page, label: string, direction: "Ascending" | "Descending" = "Ascending") {
  await page.getByRole("button", { name: /^Sort by / }).click();
  await page.getByRole("menuitemradio", { name: label, exact: true }).click();
  await page.getByRole("button", { name: /^Sort by / }).click();
  await page.getByRole("menuitemradio", { name: direction }).click();
}

/** Every rail button keeps its label inside it, and on phones is at least 24px tall. */
async function expectRailFits(page: Page, width: number) {
  const sizes = await page.locator(".jump-rail button").evaluateAll((buttons) => buttons.map((button) => ({
    overflows: button.scrollWidth > button.clientWidth, height: button.getBoundingClientRect().height,
  })));
  expect(sizes.length).toBeGreaterThan(1);
  expect(sizes.filter((size) => size.overflows)).toEqual([]);
  if (width < 640) expect(Math.min(...sizes.map((size) => size.height))).toBeGreaterThanOrEqual(24);
}

const normalized = (value: string) => value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

for (const width of [320, 390, 1440]) {
  test(`changes the rail with the sort and jumps within it at ${width}px`, async ({ page }) => {
    await openLibrary(page, width);
    const rails = {
      Year: ["Jump to decade", ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s", "—"]],
      Rating: ["Jump to rating", ["G", "PG", "PG-13", "R"]],
      Length: ["Jump to length", ["<90m", "90m", "2h"]],
      Type: ["Jump to type", ["Movie", "Series"]],
    } as const;
    for (const [label, [name, entries]] of Object.entries(rails)) {
      await sortBy(page, label);
      const rail = page.getByRole("toolbar", { name });
      await expect(rail.getByRole("button")).toHaveText([...entries]);
      await expect(page.getByRole("toolbar", { name: "Jump to letter" })).toHaveCount(0);
      await expectRailFits(page, width);
      await expectNoHorizontalPageOverflow(page);
      if (label === "Length") await expect(rail.getByRole("button", { name: "Jump to 2 hours" })).toBeVisible();
    }

    await sortBy(page, "Year", "Descending");
    const decades = page.getByRole("toolbar", { name: "Jump to decade" });
    await expect(decades.getByRole("button")).toHaveText(["—", "2020s", "2010s", "2000s", "1990s", "1980s", "1970s"]);
    await expect(decades.getByRole("button", { name: "Jump to No year" })).toHaveAttribute("aria-current", "true");
    await expectNoAccessibilityViolations(page);

    await sortBy(page, "Year");
    const firstOf1990s = library.filter((item) => item.year !== null && item.year >= 1990 && item.year < 2000)
      .sort((left, right) => left.year! - right.year! || normalized(left.name).localeCompare(normalized(right.name)))[0];
    const before = requests.length;
    await decades.getByRole("button", { name: "Jump to 1990s" }).click();
    await expect(page.getByRole("button", { name: `Open ${firstOf1990s.name}`, exact: true })).toBeFocused();
    await expect(decades.getByRole("button", { name: "Jump to 1990s" })).toHaveAttribute("aria-current", "true");
    expect(requests.slice(before).length).toBeLessThanOrEqual(1);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}

async function openLargeLibrary(page: Page, width: number) {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockApi(page);
  const largeRequests = await mockLibraryCatalog(page, generatedLibrary(10_000));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/#/library/movies");
  await expect(page.getByText("10000 titles")).toBeVisible();
  return largeRequests;
}

/** The accessible name and top of the first poster in view below the sticky toolbar. */
async function visiblePoster(page: Page) {
  return page.evaluate(() => {
    const toolbar = document.querySelector(".catalog-toolbar")!.getBoundingClientRect().bottom;
    const card = [...document.querySelectorAll<HTMLElement>(".media-card")].find((element) => element.getBoundingClientRect().top >= toolbar);
    return { name: card?.getAttribute("aria-label") ?? "", top: card?.getBoundingClientRect().top ?? -1 };
  });
}

test("jumps across a 10,000-title library with few requests and a bounded window", async ({ page }) => {
  const largeRequests = await openLargeLibrary(page, 1440);
  const posters = page.getByRole("list", { name: "Movies" }).getByRole("listitem");
  await expect(posters.first()).toHaveCSS("content-visibility", "auto");
  const rail = page.getByRole("toolbar", { name: "Jump to letter" });
  await rail.getByRole("button", { name: "Jump to M" }).click();
  await expect(page.locator(":focus")).toHaveAccessibleName(/^Open Meadow/);
  await expect(rail.getByRole("button", { name: "Jump to M" })).toHaveAttribute("aria-current", "true");
  expect(largeRequests).toEqual(["0:100", "4600:100"]);
  await expect(posters).toHaveCount(100);
  await expect(page.getByRole("status")).toHaveText("Showing 4601–4700 of 10000");
  await expectNoAccessibilityViolations(page);
});

for (const [width, columns] of [[1100, 3], [390, 2]]) {
  test(`keeps titles in place while earlier pages load at ${width}px (${columns} columns)`, async ({ page }) => {
    const largeRequests = await openLargeLibrary(page, width);
    const grid = page.getByRole("list", { name: "Movies" });
    expect(await grid.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(columns);
    await page.getByRole("toolbar", { name: "Jump to letter" }).getByRole("button", { name: "Jump to M" }).click();
    await expect(page.locator(":focus")).toHaveAccessibleName(/^Open Meadow/);
    await page.route(/start_index=4500/, async (route) => { await new Promise((resolve) => setTimeout(resolve, 500)); await route.fallback(); });
    await page.getByRole("button", { name: "Load earlier" }).scrollIntoViewIfNeeded();
    const before = await visiblePoster(page);
    expect(before.top).toBeGreaterThan(0);
    await expect(page.getByRole("status")).toHaveText("Showing 4501–4700 of 10000");
    const after = await page.getByRole("button", { name: before.name, exact: true }).boundingBox();
    expect(Math.abs((after?.y ?? 0) - before.top)).toBeLessThan(4);
    expect(largeRequests).toEqual(["0:100", "4600:100", "4500:100"]);
    await expectNoHorizontalPageOverflow(page);
  });
}

test("scrolls back to the first title after a jump at 1440px", async ({ page }) => {
  const largeRequests = await openLargeLibrary(page, 1440);
  await page.getByRole("toolbar", { name: "Jump to letter" }).getByRole("button", { name: "Jump to D" }).click();
  await expect(page.locator(":focus")).toHaveAccessibleName(/^Open Dune/);
  const earlier = page.getByRole("button", { name: "Load earlier" });
  for (let step = 0; step < 20 && await earlier.count(); step += 1) {
    const shown = await page.getByRole("status").textContent();
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.getByRole("status")).not.toHaveText(shown ?? "");
  }
  await expect(earlier).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText(/^Showing \d+ of 10000$/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByRole("button", { name: "Open 2001 Odyssey 1" })).toBeInViewport();
  expect(new Set(largeRequests).size).toBe(largeRequests.length);
});
