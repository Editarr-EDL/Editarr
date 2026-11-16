import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import { communityResponse, mixedCandidate } from "../src/test/community-lookup-fixtures";

const tracks = [1, 2].map((stream_index) => ({ stream_index, language: "eng", title: stream_index === 1 ? "Main" : "Commentary",
  codec: "aac", channels: 2, disposition: stream_index === 1 ? 1 : 8, start_seconds: 0, duration_seconds: 120, time_base: "1/48000" }));
const selection = { file_version: "f".repeat(64), track: tracks[0] };

for (const width of [320, 1440]) {
  test(`${width}px community bands, filters, explicit channels, track invalidation and keyboard focus`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    const requests: Record<string, unknown>[] = [];
    await page.route("**/api/filter-candidates", (route) => json(route, { media_item_id: "local-target", matches: [] }));
    await page.route("**/api/media/audio-tracks?*", (route) => json(route, { file_version: selection.file_version, tracks, selection, selection_stale: false }));
    await page.route("**/api/media/audio-tracks/selection", (route) => json(route, route.request().postDataJSON().selection));
    const conflict = { ...mixedCandidate, summary: { ...mixedCandidate.summary, name: `Mixed-${"Evidence".repeat(50)}` },
      compatibility: { ...mixedCandidate.compatibility, audio: { ...mixedCandidate.compatibility.audio, band: "incompatible" as const, conflicts: ["audio_duration_conflict"] } } };
    const strong = { ...mixedCandidate, revisionId: "strong", summary: { ...mixedCandidate.summary, name: "Video-only scan", kind: "scan" as const,
      channels: { video: 2 }, coverage: [{ channel: "video" as const, capability: "visual", state: "complete", intervals: [] }] } };
    await page.route("**/api/community/lookup", (route) => {
      requests.push(route.request().postDataJSON());
      expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
      return json(route, communityResponse([conflict, strong]));
    });
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open Example Movie" }).click();
    await page.getByLabel("Review version").selectOption("source-2");
    const trigger = page.getByRole("button", { name: "Find Filters", exact: true });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Found Filters" });
    await expect(dialog.getByLabel("Audio track")).toHaveValue("1");
    expect(requests).toEqual([]);
    await dialog.getByRole("button", { name: "Find community filters" }).click();
    await expect(dialog.getByRole("region", { name: "Review required results" })).toBeVisible();
    await expect(dialog.getByRole("region", { name: "Strong results" })).toBeVisible();
    await expect(dialog.getByText("Audio: Incompatible").first()).toBeVisible();
    await expect(dialog.getByText("audio duration conflict").first()).toBeVisible();
    const video = dialog.getByRole("checkbox", { name: `Select video from ${conflict.summary.name}` });
    const audio = dialog.getByRole("checkbox", { name: `Select audio from ${conflict.summary.name}` });
    await expect(video).not.toBeChecked();
    await expect(audio).not.toBeChecked();
    await dialog.getByRole("button", { name: "Select strong channels" }).click();
    await expect(video).toBeChecked();
    await expect(audio).not.toBeChecked();
    expect(requests[0]).toMatchObject({ target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-2" }, audio_selection: selection });
    expect(requests[0]).not.toHaveProperty("tracks");
    expect(requests[0]).not.toHaveProperty("evidence");
    await dialog.getByLabel("Type", { exact: true }).selectOption("scan");
    await expect(dialog.getByRole("article", { name: conflict.summary.name })).toHaveCount(0);
    await dialog.getByLabel("Type", { exact: true }).selectOption("all");
    await dialog.getByLabel("Language", { exact: true }).selectOption("unknown");
    await dialog.getByLabel("Coverage", { exact: true }).selectOption("unknown");
    await expect(dialog.getByRole("article")).toHaveCount(1);
    await dialog.getByLabel("Audio band", { exact: true }).selectOption("incompatible");
    await expect(dialog.getByRole("article", { name: conflict.summary.name })).toBeVisible();
    await dialog.getByLabel("Coverage", { exact: true }).selectOption("all");
    await dialog.getByLabel("Audio band", { exact: true }).selectOption("all");
    await dialog.getByLabel("Sort within bands").selectOption("uploader");
    await dialog.getByText("Evidence and provenance", { exact: true }).first().click();
    await expect(dialog.getByText("Author reputation", { exact: true }).first()).toBeVisible();
    await expect(dialog.getByText(/Revision quality unavailable/).first()).toBeVisible();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await dialog.evaluate((element) => { element.scrollTop = 0; });
    const bounds = await dialog.boundingBox();
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(900);
    await page.screenshot({ path: `test-results/css49-overview-${width}.png` });
    await video.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/css49-results-${width}.png` });
    await dialog.getByLabel("Audio track").selectOption("2");
    await expect(dialog.getByRole("article")).toHaveCount(0);
    await expect(dialog.getByText("Community lookup not started.")).toBeVisible();
    await dialog.getByRole("button", { name: "Find community filters" }).click();
    await expect(dialog.getByRole("article")).toHaveCount(2);
    await expect(video).not.toBeChecked();
    expect(requests[1]).toMatchObject({ audio_selection: { ...selection, track: tracks[1] } });
    await video.focus();
    await page.keyboard.press("Space");
    await expect(video).toBeChecked();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test(`${width}px remote outage is not empty success and stale rows cannot be selected`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/filter-candidates", (route) => json(route, { media_item_id: "local-target", matches: [] }));
    await page.route("**/api/media/audio-tracks?*", (route) => json(route, { file_version: selection.file_version, tracks, selection, selection_stale: false }));
    let calls = 0;
    await page.route("**/api/community/lookup", (route) => {
      calls++;
      return calls === 1 ? json(route, communityResponse()) : json(route, { code: "service_unavailable", retryable: true }, 503);
    });
    await page.goto("/#/library/movies");
    await page.getByRole("button", { name: "Open Example Movie" }).click();
    await page.getByRole("button", { name: "Find Filters", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Found Filters" });
    await expect(dialog.getByLabel("Audio track")).toHaveValue("1");
    await dialog.getByRole("button", { name: "Find community filters" }).click();
    await expect(dialog.getByRole("article")).toHaveCount(1);
    await dialog.getByRole("button", { name: "Refresh community filters" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Community lookup is unavailable.");
    await expect(dialog.getByText(/Previous results are stale/)).toBeVisible();
    await expect(dialog.getByRole("checkbox", { name: "Select video from Mixed review" })).toBeDisabled();
    await expect(dialog.getByText("No community filters were found.")).toHaveCount(0);
    await expect(dialog.getByText("No matching filter scans were found.")).toBeVisible();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}