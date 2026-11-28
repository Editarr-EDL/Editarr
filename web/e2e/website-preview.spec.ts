import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./app-fixtures";
import { mockCommunityMasters } from "./community-masters-mock";
import { mockWebsiteData, movieNight } from "./website-capture-mock";
import {
  mockWebsiteCommunityVersions, mockWebsiteLibrary, mockWebsitePublications, mockWebsiteSubtitles, mockWebsiteUsers,
} from "./website-capture-screens";
import { chooseWorkflowStep, setReviewSource } from "./workflow-actions";
import { chooseModel, chooseScanType, modelPicker } from "./new-scan-actions";

test.skip(!process.env.EDITARR_CAPTURE_WEBSITE, "Opt-in website asset capture");

// Run explicitly to refresh the public website screenshots in website/public/assets/screens/ from synthetic
// "Example Movie" fixtures only: EDITARR_CAPTURE_WEBSITE=1 pnpm exec playwright test website-preview.spec.ts
// review.png is the full-app hero; each feature tour shot is a 1200x800 crop of one region at 2x.
const screens = "../website/public/assets/screens";
const tourSize = [1200, 800] as const;
test.use({ viewport: { width: 1440, height: 900 }, locale: "en-US", timezoneId: "America/New_York" });

async function expectSettled(page: Page) {
  await expect(page.locator(".toast, [role=alert]").filter({ visible: true })).toHaveCount(0);
  await page.mouse.move(0, 0);
}

async function openItem(page: Page, step: Parameters<typeof chooseWorkflowStep>[1]) {
  await page.goto("/#/library/movies/item/movie-1");
  await expect(page.getByRole("heading", { name: "Example Movie", exact: true })).toBeVisible();
  await chooseWorkflowStep(page, step);
}

/** Scroll the window at once so `target` sits `gap` pixels below the top of the viewport. */
async function scrollTo(page: Page, target: Locator, gap = 24) {
  await target.evaluate((element, offset) => window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY - offset, behavior: "instant" }), gap);
}

/**
 * Save a 1200x800 CSS-pixel tour shot of the viewport that frames `regions` (the union of their boxes): centered
 * horizontally on them and starting `padding` above them, kept inside the viewport.
 */
async function captureRegion(page: Page, name: string, regions: Locator[], padding = 16, [width, height]: readonly [number, number] = tourSize) {
  await expectSettled(page);
  const boxes = await Promise.all(regions.map(async (region) => (await region.boundingBox())!));
  const left = Math.min(...boxes.map((box) => box.x));
  const top = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.width));
  expect(right - left, `${name} region width`).toBeLessThanOrEqual(width);
  const viewport = page.viewportSize()!;
  const x = Math.min(Math.max(0, (left + right - width) / 2), viewport.width - width);
  const y = Math.min(Math.max(0, top - padding), viewport.height - height);
  await page.screenshot({ path: `${screens}/${name}.png`, clip: { x: Math.round(x), y: Math.round(y), width, height } });
}

/**
 * Build and save the "Movie night" review grouped by category the way a user would: include the Sex cue, leave out
 * the bar fight, and open Sex › Kissing and Violence › Punching to their cues.
 */
async function openMovieNightReview(page: Page) {
  await mockApi(page);
  await mockWebsiteData(page);
  await openItem(page, "Review");
  const panel = page.locator(".saved-review-panel");
  await panel.getByRole("button", { name: "New Review", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New Review", exact: true });
  await dialog.getByLabel("New review name").fill("Movie night");
  await dialog.getByRole("button", { name: "Start new review" }).click();
  await setReviewSource(page, "Dialogue");
  await setReviewSource(page, "Picture");
  await panel.getByLabel("Group by", { exact: true }).selectOption("category");
  await expect(panel.getByRole("heading", { name: "Movie night", exact: true })).toBeVisible();
  await panel.getByRole("button", { name: /^Nudity family/ }).click();
  for (const group of [/^Kissing subcategory/, /^A couple kisses on a balcony at night finding group/, /^Punching subcategory/,
    /^Two characters trade punches in a parking garage finding group/, /^A bar fight breaks out after an argument finding group/]) {
    await panel.getByRole("button", { name: group }).click();
  }
  const sex = panel.getByRole("checkbox", { name: "Use all video filters in Sex family" });
  await sex.check();
  const barFight = panel.getByRole("checkbox", { name: /^Include A bar fight breaks out after an argument at / });
  await barFight.uncheck();
  await panel.getByRole("button", { name: "Save Review", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Save Changes", exact: true })).toBeVisible();
  await expect(sex).toBeChecked();
  await expect(barFight).not.toBeChecked();
  // Checking boxes scrolls the filter list; start it at the Sex family so both edited groups are in view.
  await panel.locator("tr.hierarchy-family").filter({ has: page.getByRole("checkbox", { name: "Use all video filters in Sex family" }) }).evaluate((row) => {
    let scroller = row.parentElement;
    while (scroller && scroller.scrollHeight <= scroller.clientHeight) scroller = scroller.parentElement;
    if (scroller) scroller.scrollTop += row.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  });
  return panel;
}

test("review", async ({ page }) => {
  const panel = await openMovieNightReview(page);
  await scrollTo(page, panel.getByRole("heading", { name: "Movie night", exact: true }));
  await expectSettled(page);
  await page.screenshot({ path: `${screens}/review.png` });
});

test.describe("feature tour", () => {
  test.use({ deviceScaleFactor: 2 });

  test("review detail", async ({ page }) => {
    const panel = await openMovieNightReview(page);
    await page.setViewportSize({ width: 1440, height: 800 });
    const heading = panel.getByRole("heading", { name: "Movie night", exact: true });
    await scrollTo(page, heading, 20);
    await captureRegion(page, "review-detail", [heading, panel.getByRole("table", { name: "Video review filters table" })], 20);
  });

  test.describe("new scan", () => {
    // Header through the Video model card runs taller than 800 CSS px, so frame 1320x880 at 2400/1320 scale:
    // still a 2400x1600, 3:2 file.
    const frame = [1320, 880] as const;
    test.use({ deviceScaleFactor: 2400 / 1320 });

    test("new scan", async ({ page }) => {
      test.skip(!process.env.EDITARR_CAPTURE_NEW_SCAN, "Set EDITARR_CAPTURE_NEW_SCAN=1 to refresh new-scan.png");
      // A window taller than the shot lets the dialog show its header through the Video model card without scrolling.
      await page.setViewportSize({ width: 1440, height: 1040 });
      await mockApi(page);
      await mockWebsiteData(page);
      await openItem(page, "Scans");
      await page.getByRole("button", { name: "New Scan", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "New Scan" });
      await chooseScanType(dialog, "Audio and video");
      await expect(dialog.getByText("Loading audio tracks")).toHaveCount(0);
      await expect(dialog.getByLabel("Audio track", { exact: true })).not.toHaveValue("");
      await chooseModel(dialog, "Filter", "Qwen");
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
      await captureRegion(page, "new-scan", [dialog], 16, frame);
    });
  });

  test("library", async ({ page }) => {
    await page.setViewportSize({ width: 1540, height: 900 });
    await mockApi(page);
    await mockWebsiteData(page);
    await mockWebsiteLibrary(page);
    await page.goto("/#/library/movies");
    await expect(page.getByRole("heading", { name: "Movies", level: 1 })).toBeVisible();
    await page.waitForLoadState("networkidle");
    const posters = page.getByRole("list", { name: "Movies" }).getByRole("listitem");
    await captureRegion(page, "library", [page.getByRole("heading", { name: "Movies", level: 1 }), page.locator(".library-toolbar"), posters.nth(6)]);
  });

  test("scan queue", async ({ page }) => {
    await mockApi(page);
    await mockWebsiteData(page);
    await mockWebsiteLibrary(page);
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open scans queue and history" }).click();
    const panel = page.getByRole("region", { name: "Scans queue and history" });
    await expect(panel).toBeVisible();
    await page.waitForLoadState("networkidle");
    await captureRegion(page, "scan-queue", [page.getByRole("heading", { name: "Movies", level: 1 }), panel], 24);
  });

  test("subtitles", async ({ page }) => {
    const review = movieNight();
    await mockApi(page);
    await mockWebsiteData(page, { reviews: [review] });
    await mockWebsiteSubtitles(page, review.id);
    await openItem(page, "Subtitles");
    const cuePreview = page.getByRole("region", { name: "Cue preview" });
    await cuePreview.getByRole("button", { name: "Preview cue changes" }).click();
    await expect(cuePreview.getByText("Oh my ***, look at the bridge!")).toBeVisible();
    await scrollTo(page, page.locator(".workflow-published-files"), 40);
    await captureRegion(page, "subtitles", [page.locator(".workflow-published-files"), cuePreview]);
  });

  test("publish", async ({ page }) => {
    const review = movieNight();
    await mockApi(page);
    await mockWebsiteData(page, { reviews: [review] });
    await mockWebsitePublications(page, review.id);
    await openItem(page, "Publish");
    await page.getByRole("button", { name: "EDL", exact: true }).click();
    await expect(page.getByRole("listitem", { name: "MCF · Item default" })).toBeVisible();
    // A viewport as tall as the shot lets the short page scroll the Published files bar to the top.
    await page.setViewportSize({ width: 1440, height: 800 });
    await scrollTo(page, page.locator(".workflow-published-files"), 40);
    await captureRegion(page, "publish", [page.locator(".workflow-published-files"), page.locator(".workflow-release")]);
  });

  test("community", async ({ page }) => {
    await mockApi(page);
    await mockWebsiteData(page);
    await mockCommunityMasters(page);
    await mockWebsiteCommunityVersions(page);
    await openItem(page, "Community");
    await page.getByRole("region", { name: "Community filters", exact: true }).getByRole("button", { name: "Find Filters" }).click();
    const dialog = page.getByRole("dialog", { name: "Find Filters" });
    await expect(dialog.getByText("80% match")).toBeVisible();
    await captureRegion(page, "community", [dialog], 12);
  });

  test.describe("users", () => {
    // The Users table needs more than the 1440px layout's width to keep its Name column readable.
    test.use({ viewport: { width: 1560, height: 900 } });

    test("users", async ({ page }) => {
      await mockApi(page);
      await mockWebsiteData(page);
      await mockWebsiteUsers(page);
      await page.goto("/#/settings/users");
      const directory = page.getByRole("region", { name: "User directory" });
      await expect(directory.getByRole("rowheader", { name: /Mia/ })).toBeVisible();
      await page.waitForLoadState("networkidle");
      await captureRegion(page, "users", [page.getByRole("heading", { name: "Users", level: 1 }), directory]);
    });
  });
});
