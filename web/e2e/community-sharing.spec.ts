import { expect, test } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi, mockReview } from "./app-fixtures";
import { pendingPublication, sharingContextFixture, sharingPreviewFixture } from "../src/scans/sharing-test-fixtures";
import type { PublicationJob } from "../src/api/api-sharing";

const completedScan = {
  id: "scan-previous", scan_id: 7, media_item_id: "media-1", library_id: "movies", jellyfin_item_id: "movie-1",
  item_name: "Example Movie", item_type: "Movie", media_source_id: "source-1", media_source_name: "4K HDR",
  name: "Audio test scan", origin: "generated", status: "complete", scan_audio: true, audio_model: "whisper",
  scan_video: false, video_model: null, frame_interval: 1, speed_up: false, error_message: null,
  created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
};

for (const width of [320, 1440]) {
  test(`${width}px exact preview, focus, stale revision, saved retry and cancellation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/scans/history", (route) => json(route, [completedScan]));
    let job: PublicationJob | null = null;
    let approvalCalls = 0;
    const posts: { path: string; body: unknown }[] = [];
    await page.route(/\/api\/community\/.*/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST") {
        expect(request.headers()["x-csrf-token"]).toBe("test-csrf-token");
        posts.push({ path, body: request.postDataJSON() });
      }
      if (path.endsWith("/preview")) {
        const fixture = sharingPreviewFixture();
        return json(route, { ...fixture, preview: { ...fixture.preview, warnings: ["audio_fingerprint_unsupported"] } });
      }
      if (path.endsWith("/approve")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true });
        if (++approvalCalls === 1) return json(route, { code: "preview_stale", retryable: false }, 409);
        job = { ...pendingPublication };
        return json(route, job);
      }
      if (path.includes("/publications/")) {
        expect(path).toContain(pendingPublication.operationId);
        job = request.method() === "DELETE" ? { ...pendingPublication, state: "cancel_pending", cancelRequested: true }
          : { ...pendingPublication, state: "paused", phase: "outcome_unknown", errorCode: "network_unavailable" };
        return json(route, job);
      }
      if (request.method() === "DELETE") return json(route, { discarded: true });
      return json(route, { ...sharingContextFixture(), jobs: job ? [job] : [] });
    });
    await page.goto("/#/library/movies/item/movie-1");
    const share = page.getByRole("button", { name: /^Share Audio test scan/ });
    await share.click();
    let dialog = page.getByRole("dialog", { name: "Share scan" });
    await expect(dialog.getByLabel("I confirm this TMDB/TVDB association.")).toBeVisible();
    expect(posts).toEqual([]);
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await expect(dialog.getByRole("region", { name: "Outgoing contribution" })).toBeVisible();
    await expect(dialog.getByRole("list", { name: "Preview warnings" })).toContainText("Audio fingerprint unavailable for this track.");
    await expect(dialog.getByText(/View detected words in the local scan/)).toBeVisible();
    expect(posts.every((request) => request.path.endsWith("/preview"))).toBe(true);
    await dialog.getByLabel("I may share this data and its notes, and I approve this exact preview.").check();
    await dialog.getByRole("button", { name: "Close", exact: true }).focus();
    await page.keyboard.press("Shift+Tab");
    await expect(dialog.getByRole("button", { name: "Share contribution" })).toBeFocused();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css46-preview-${width}.png`, fullPage: true });
    await page.keyboard.press("Escape");
    await expect(share).toBeFocused();
    expect(approvalCalls).toBe(0);
    await share.click();
    dialog = page.getByRole("dialog", { name: "Share scan" });
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await dialog.getByLabel("I may share this data and its notes, and I approve this exact preview.").check();
    await dialog.getByRole("button", { name: "Share contribution" }).click();
    await expect(dialog.getByRole("alert")).toContainText("The source changed");
    await expect(dialog.getByRole("region", { name: "Outgoing contribution" })).toHaveCount(0);
    expect(posts.some((request) => request.path.includes("/publications/"))).toBe(false);
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await dialog.getByLabel("I may share this data and its notes, and I approve this exact preview.").check();
    await dialog.getByRole("button", { name: "Share contribution" }).click();
    await expect(dialog.getByRole("button", { name: "Retry saved contribution" })).toBeEnabled();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const beforeReopen = posts.length;
    await share.click();
    dialog = page.getByRole("dialog", { name: "Share scan" });
    await dialog.getByRole("region", { name: "Saved publications" }).getByRole("button").click();
    expect(posts.length).toBe(beforeReopen);
    await dialog.getByRole("button", { name: "Retry saved contribution" }).click();
    await expect(dialog.getByRole("button", { name: "Cancel publication" })).toBeEnabled();
    await dialog.getByRole("button", { name: "Cancel publication" }).click();
    await expect(dialog.getByText(/Cancellation does not delete/)).toBeVisible();
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    for (const canary of ["PRIVATE FULL TRANSCRIPT", "/private/media.mp4", "confirmationSecret", "private_key"]) {
      expect(JSON.stringify(posts)).not.toContain(canary);
      expect(await page.locator("body").textContent()).not.toContain(canary);
    }
  });

  test(`${width}px derived mixed audio preserves public notes, video and immutable review`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    const review = mockReview("saved-derived", "Mixed community review", "source-1");
    await page.route(/\/api\/library-items\/movie-1\/reviews(?:\?.*)?$/, (route) => json(route, [review]));
    await page.route("**/api/reviews/saved-derived", (route) => json(route, review));
    const context = sharingContextFixture("derived");
    context.audioSources.push({ scanId: "scan-b", track: { ...context.audioSources[0].track!, stream_index: 2, language: "fr", title: "French actually analyzed" } });
    const writes: string[] = [];
    await page.route(/\/api\/community\/.*/, async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === "DELETE") return json(route, { discarded: true });
      if (path.endsWith("/preview")) {
        writes.push(path);
        expect(route.request().postDataJSON().audio_source_scan_id).toBe("scan-a");
        return json(route, sharingPreviewFixture("derived"));
      }
      return json(route, context);
    });
    page.on("request", (request) => {
      if (request.method() === "PUT" || request.url().includes("/approve") || request.url().includes("/publications/")) writes.push(request.url());
    });
    await page.goto("/#/library/movies/item/movie-1");
    await page.getByRole("button", { name: "Open review Mixed community review" }).click();
    await page.getByRole("button", { name: "Share Mixed community review" }).click();
    const dialog = page.getByRole("dialog", { name: "Share review" });
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await expect(dialog.getByRole("button", { name: "Generate preview" })).toBeDisabled();
    await dialog.getByLabel("Contribution audio source").selectOption("scan-a");
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await expect(dialog.getByText(/event-2.*other audio track/)).toBeVisible();
    await expect(dialog.getByRole("region", { name: "Outgoing filters" }).getByText("video", { exact: true })).toBeVisible();
    for (const finding of sharingPreviewFixture("derived").preview.artifact.findings) {
      if (finding.note) await expect(dialog.getByRole("region", { name: "Outgoing filters" }).getByText(finding.note, { exact: true })).toBeVisible();
    }
    await dialog.getByLabel("I may share this data and its notes, and I approve this exact preview.").check();
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css46-derived-${width}.png`, fullPage: true });
    await dialog.getByLabel("Contribution audio source").selectOption("scan-b");
    await expect(dialog.getByRole("region", { name: "Outgoing contribution" })).toHaveCount(0);
    expect(writes).toEqual(["/api/community/sharing/sources/review/saved-derived/preview"]);
  });

  test(`${width}px typed association and unknown public preview for a Jellyfin item without file identity`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    const privatePath = "/media/movies/example.mkv";
    await page.route("**/api/scans/history", (route) => json(route, [{ ...completedScan, name: "Manual evidence", origin: "manual" }]));
    const requests: unknown[] = [];
    await page.route(/\/api\/community\/.*/, async (route) => {
      if (route.request().method() === "DELETE") return json(route, { discarded: true });
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        requests.push(body);
        if (body.media.reference.includes("imdb")) return json(route, { code: "invalid_media_reference", retryable: false }, 409);
        return json(route, sharingPreviewFixture("manual"));
      }
      return json(route, { ...sharingContextFixture("manual"), mediaIdentity: null, audioSources: [{ scanId: "scan-previous", track: null }] });
    });
    await page.goto("/#/library/movies/item/movie-1");
    await page.getByRole("button", { name: /^Share Manual evidence/ }).click();
    const dialog = page.getByRole("dialog", { name: "Share scan" });
    await dialog.getByLabel("TMDB/TVDB ID or URL").fill("https://www.imdb.com/title/tt123");
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await expect(dialog.getByRole("alert")).toContainText("supported TMDB/TVDB");
    await dialog.getByLabel("TMDB/TVDB ID or URL").fill("https://www.themoviedb.org/movie/123");
    await expect(dialog.getByLabel("I confirm this TMDB/TVDB association.")).not.toBeChecked();
    await dialog.getByLabel("I confirm this TMDB/TVDB association.").check();
    await dialog.getByRole("button", { name: "Generate preview" }).click();
    await expect(dialog.getByText("Audio track unknown. Review required.")).toBeVisible();
    await dialog.getByText("Exact outgoing metadata and lineage", { exact: true }).click();
    await expect(dialog.getByRole("region", { name: "Exact outgoing metadata and lineage" })).toBeVisible();
    expect(JSON.stringify(requests)).not.toContain(privatePath);
    expect(JSON.stringify(requests)).not.toContain("video_path");
    expect(await dialog.textContent()).not.toContain(privatePath);
    await expectNoAccessibilityViolations(page);
    await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css46-typed-${width}.png` });
  });
}
