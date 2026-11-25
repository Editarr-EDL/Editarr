import { chooseWorkflowStep, openReviewSources } from "./workflow-actions";
import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, items, json, mockApi } from "./app-fixtures";

const scan = {
  id: "found-local", scan_id: 7, name: `Local-${"Evidence".repeat(8)}`, origin: "generated",
  media_item_id: "original-media", jellyfin_item_id: "movie-1", library_id: "movies",
  item_name: "Example Movie", item_type: "Movie", media_source_id: "source-2", media_source_name: "1080p SDR",
  scan_mode: "content", status: "complete", scan_audio: true, audio_model: "whisper", audio_filter: "better_profanity",
  scan_video: false, video_model: null, coarse_scan_enabled: false, coarse_scan_interval: null,
  qwen_model: "qwen3-vl-8b-instruct-4bit", qwen_summaries_per_segment: 3, qwen_segment_join_gap_seconds: 0,
  duration_seconds: 5700, finding_count: 2, error_message: null, mcf_document: null,
  created_at: "2026-10-04T00:00:00Z", updated_at: "2026-10-04T00:00:00Z",
};
const existing = { ...scan, id: "existing-local", scan_id: 2, name: "Already linked" };
const unchecked = { ...scan, id: "unchecked-local", scan_id: 8, name: "Leave unassociated" };

for (const width of [320, 1440]) {
  test(`${width}px Recover Scans: local discovery, keyboard cancellation, selection and review reuse`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    let associated = false;
    let finishAssociation!: () => void;
    const associationGate = new Promise<void>((resolve) => { finishAssociation = resolve; });
    const searches: unknown[] = [];
    const associations: unknown[] = [];
    const communityRequests: string[] = [];
    page.on("request", (request) => {
      // The daily sync and the item page's own master-set list are background reads, not filter lookups.
      if (request.url().includes("/api/community/") && !/\/api\/community\/(sync\/run|masters\?)/.test(request.url())) {
        communityRequests.push(request.url());
      }
    });
    await page.route("**/api/scans/history", (route) => json(route, associated ? [existing, scan] : [existing]));
    await page.route("**/api/filter-candidates/available", (route) => json(route, { available: true }));
    await page.route("**/api/media/audio-tracks?*", (route) => json(route, {
      file_version: "a".repeat(64), tracks: [], selection: null, selection_stale: false,
    }));
    await page.route("**/api/filter-candidates", (route) => {
      searches.push(route.request().postDataJSON());
      return json(route, { media_item_id: "resolved-target", matches: [
        { scan: existing, match_reason: "quick_hash", already_associated: true },
        { scan, match_reason: "scene_release", already_associated: false },
        { scan: unchecked, match_reason: "file_name", already_associated: false },
      ] });
    });
    await page.route("**/api/filter-candidates/associations", async (route) => {
      expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
      associations.push(route.request().postDataJSON());
      await associationGate;
      associated = true;
      await route.fulfill({ status: 204 });
    });
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open Example Movie" }).click();
    await page.getByLabel("Media version").selectOption("source-2");
    await chooseWorkflowStep(page, "Scans");
    const trigger = page.getByRole("button", { name: "Recover Scans" });
    await expect(trigger).toBeEnabled();
    await expect(page.getByRole("button", { name: "Find Filters" })).toHaveCount(0);
    await page.screenshot({ path: `test-results/recover-scans-enabled-${width}.png`, fullPage: true });
    expect(searches).toEqual([]);
    await trigger.click();
    let dialog = page.getByRole("dialog", { name: "Recover Scans" });
    const selected = dialog.getByRole("checkbox", { name: new RegExp(`^Associate ${scan.name}`) });
    await expect(selected).toBeFocused();
    await expect(selected).toBeChecked();
    await expect(dialog.getByRole("checkbox", { name: /Associate Already linked/ })).toBeDisabled();
    await expect(dialog.getByRole("checkbox", { name: /Associate Already linked/ })).toBeChecked();
    expect(searches[0]).toMatchObject({ source: "jellyfin", item_id: "movie-1", media_source_id: "source-2",
      duration_seconds: 5700, scene_name: items[0].versions[1].scene_name, release_group: "OTHER" });
    expect(searches[0]).not.toHaveProperty("source_path");
    // Recovery is local only: community versions are found from the Community tab.
    await expect(dialog.getByRole("button", { name: "Find community versions" })).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    const contentFits = await dialog.locator(".candidate-scan > div").evaluateAll((elements) =>
      elements.every((element) => element.scrollWidth <= element.clientWidth));
    expect(contentFits).toBe(true);
    await page.screenshot({ path: `test-results/css47-found-${width}.png`, fullPage: true });
    await page.screenshot({ path: `test-results/recover-scans-dialog-${width}.png`, fullPage: true });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(associations).toEqual([]);
    await trigger.click();
    dialog = page.getByRole("dialog", { name: "Recover Scans" });
    await dialog.getByRole("checkbox", { name: /Associate Leave unassociated/ }).uncheck();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(trigger).toBeFocused();
    expect(associations).toEqual([]);
    await trigger.click();
    dialog = page.getByRole("dialog", { name: "Recover Scans" });
    await expect(dialog.getByRole("checkbox", { name: /Associate Leave unassociated/ })).toBeChecked();
    await dialog.getByRole("checkbox", { name: /Associate Leave unassociated/ }).uncheck();
    await dialog.getByRole("button", { name: "Associate Selected" }).click();
    await expect(dialog.getByRole("button", { name: "Associating…" })).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    finishAssociation();
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expect(page.getByRole("status")).toHaveText("Associated 1 scan.");
    expect(associations).toEqual([{ media_item_id: "resolved-target", scan_ids: [scan.id] }]);
    await expect(page.getByRole("list", { name: "Scan history" }).getByText(new RegExp(scan.name)).first()).toBeVisible();
    await chooseWorkflowStep(page, "Review");
    await page.getByRole("button", { name: "New Review" }).click();
    await page.getByLabel("New review name").fill("Reuse local filters");
    await page.getByRole("button", { name: "Start new review" }).click();
    const sources = await openReviewSources(page);
    await expect(sources.getByRole("checkbox", { name: new RegExp(scan.name) })).toBeVisible();
    // A long scan name wraps inside the Sources popover rather than widening the page.
    await expectNoHorizontalPageOverflow(page);
    expect(communityRequests).toEqual([]);
  });

  test(`${width}px local empty and failed lookups and association retry`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    let searchCount = 0;
    let associationCount = 0;
    await page.route("**/api/filter-candidates/available", (route) => json(route, { available: true }));
    await page.route("**/api/filter-candidates", (route) => {
      searchCount++;
      if (searchCount === 1) return json(route, { detail: "Local lookup unavailable" }, 503);
      return json(route, { media_item_id: "resolved-target", matches: searchCount === 2 ? [] : [
        { scan, match_reason: "format", already_associated: false },
      ] });
    });
    await page.route("**/api/filter-candidates/associations", async (route) => {
      associationCount++;
      if (associationCount === 1) return json(route, { detail: "Local association unavailable" }, 503);
      await route.fulfill({ status: 204 });
    });
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open Example Movie" }).click();
    await chooseWorkflowStep(page, "Scans");
    const trigger = page.getByRole("button", { name: "Recover Scans" });
    await trigger.click();
    await expect(page.getByRole("status")).toContainText("Local lookup unavailable");
    await expect(trigger).toBeEnabled();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Recover Scans" });
    await expect(dialog.getByText("No recoverable scans matched this file.")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Associate Selected" })).toBeDisabled();
    await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css47-empty-${width}.png`, fullPage: true });
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await trigger.click();
    await dialog.getByRole("button", { name: "Associate Selected" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Local association unavailable" })).toContainText("Local association unavailable");
    await expect(dialog.getByRole("checkbox")).toBeChecked();
    await expect(dialog.getByRole("button", { name: "Associate Selected" })).toBeEnabled();
    await dialog.getByRole("button", { name: "Associate Selected" }).click();
    await expect(dialog).toHaveCount(0);
    expect(associationCount).toBe(2);
  });

  test(`${width}px Recover Scans is disabled and explained when nothing can be recovered`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    for (const path of ["**/api/scans/history", "**/api/scans/queue", "**/api/library-items/movie-1/scans"]) {
      await page.route(path, (route) => json(route, []));
    }
    const probes: unknown[] = [];
    await page.route("**/api/filter-candidates/available", (route) => {
      probes.push(route.request().postDataJSON());
      return json(route, { available: false });
    });
    await page.route("**/api/media/audio-tracks?*", (route) => json(route, {
      file_version: "a".repeat(64), tracks: [], selection: null, selection_stale: false,
    }));
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open Example Movie" }).click();
    await chooseWorkflowStep(page, "Scans");
    const trigger = page.getByRole("button", { name: "Recover Scans" });
    await expect(trigger).toBeDisabled();
    await expect(trigger).toHaveAccessibleDescription(/no local scans of this item's other versions or files/);
    await expect(page.getByText(/no local scans of this item's other versions/)).toBeVisible();
    expect(probes).toContainEqual(expect.objectContaining({ item_id: "movie-1" }));
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/recover-scans-disabled-${width}.png`, fullPage: true });
  });
}
