import { expect, test } from "@playwright/test";

import type { Scan } from "../src/api/api-scan-types";
import type { EditarrItem, VerificationPreview } from "../src/api/api-stable-media";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, items, json, mockApi } from "./app-fixtures";
import { chooseWorkflowStep } from "./workflow-actions";

const movie = { ...items[0], name: "The Life of Chuck", season_number: null, media_streams: [], media_provider_id: null };
const oldScan: Scan = {
  id: "old-blu-ray", scan_id: 2, name: "Original video scan", origin: "generated", is_own: true,
  jellyfin_item_id: "old-chuck", media_item_id: "media-chuck", library_id: "movies", media_source_id: "original-blu-ray",
  media_source_name: "Blu-ray", item_name: movie.name, item_type: "Movie", scan_mode: "content", status: "complete",
  scan_audio: false, audio_model: null, audio_filter: null, scan_video: true, video_model: "nudenet",
  coarse_scan_interval: null, coarse_scan_enabled: false, qwen_model: "qwen3-vl-8b-instruct-4bit",
  qwen_summaries_per_segment: 3, qwen_segment_join_gap_seconds: 0, duration_seconds: 5700,
  video_hash: null, checkpoint_stage: "complete", error_message: null, finding_count: 2, manual: false, edited: false,
  scan_started_at: null, scan_duration_seconds: null, created_at: "2026-08-19T00:00:00Z",
  updated_at: "2026-08-19T00:00:00Z", import_source_name: null, imported_at: null, mcf_document: null, credits_detection: null,
};
const title: EditarrItem = {
  title_id: "chuck-e2e", name: movie.name, active_item: movie, active_items: [movie],
  selected_version_id: null, choose_version: false, versions: [{
    id: "original-version", jellyfin_item_id: "old-chuck", media_source_id: "original-blu-ray",
    label: "Original Blu-ray", available: false, scans: [oldScan], reviews: [],
  }],
};

for (const width of [320, 1440]) {
  test(`${width}px guided verification previews a cut and approves without a checkbox`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route("**/api/editarr-items/chuck-e2e", (route) => json(route, title));
    await page.route("**/api/library-items/movie-1/scans?associated_only=true", (route) => json(route, []));
    const token = "c".repeat(64);
    const targetGeneration = "d".repeat(64);
    await page.route("**/api/media-versions/current/reuse/preview", (route) => {
      const request = route.request().postDataJSON();
      expect(request.target).toEqual({ source: "jellyfin", item_id: "movie-1", media_source_id: "source-2" });
      return json(route, {
        source_scan_id: oldScan.id, version_id: "current", token, target_generation: targetGeneration, exact: false,
        verified_channels: ["video"], issues: [], anchors: request.anchors,
        cues: [
          { source_finding_id: 1, channel: "video", source_start: 3672, source_end: 3673, target_start: 3674, target_end: 3675 },
          { source_finding_id: 2, channel: "video", source_start: 3740, source_end: 3741, target_start: null, target_end: null },
        ],
      } satisfies VerificationPreview);
    });
    const clipRequests: unknown[] = [];
    await page.route("**/api/library-items/movie-1/reviews/preview", (route) => {
      expect(route.request().headers()["x-csrf-token"]).toBe("test-csrf-token");
      clipRequests.push(route.request().postDataJSON());
      return route.fulfill({ contentType: "video/mp4", body: Buffer.alloc(0) });
    });
    const approvals: unknown[] = [];
    let finishApproval!: () => void;
    const holdApproval = new Promise<void>((resolve) => { finishApproval = resolve; });
    await page.route("**/api/media-fingerprints/queue", (route) => json(route, approvals.length ? [{
      id: "chuck-fingerprint", item_name: movie.name, version_name: "1080p SDR",
      item_id: "movie-1", media_source_id: "source-2", source_scan_id: oldScan.id,
      bytes_read: 10 * 1024 * 1024, bytes_total: 20 * 1024 * 1024, reading: true,
    }] : []));
    await page.route("**/api/media-versions/current/reuse/approve", async (route) => {
      approvals.push(route.request().postDataJSON());
      await holdApproval;
      return json(route, { scan: { ...oldScan, id: "verified-copy" }, reused: false });
    });

    await page.goto("/#title/chuck-e2e");
    await page.getByLabel("Media version").selectOption("source-2");
    await chooseWorkflowStep(page, "Scans");
    const trigger = page.getByRole("listitem").filter({ hasText: "Original video scan" })
      .getByRole("button", { name: "Verify for this version" });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Verify for this version" });
    await expect(dialog.getByRole("button", { name: "Align timing" })).toHaveAttribute("aria-current", "step");
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();

    await trigger.press("Enter");
    for (const [label, value] of [["Original time 1", "0"], ["Target time 1", "2"], ["Original time 2", "3700"], ["Target time 2", "3702"]]) {
      await dialog.getByLabel(label).fill(value);
    }
    await dialog.getByRole("button", { name: "Preview alignment" }).click();
    await expect(dialog.getByRole("heading", { name: "Proposed cue times" })).toBeVisible();
    await expect(dialog.getByText("1 pending", { exact: true })).toBeVisible();
    const cueTimes = dialog.getByRole("region", { name: "Proposed cue times" });
    const tableWidth = await cueTimes.evaluate((region) => ({ content: region.scrollWidth, container: region.clientWidth }));
    expect(tableWidth.content).toBeLessThanOrEqual(tableWidth.container + 1);
    const playClip = dialog.getByRole("button", { name: /^Preview at .*14\.000$/ });
    await playClip.click();
    const video = dialog.getByLabel("Target cue preview");
    await expect(video).toBeVisible();
    await playClip.focus();
    await page.keyboard.press("Tab");
    await expect(video).toBeFocused();
    expect(clipRequests).toEqual([expect.objectContaining({
      media_source_id: "source-2", target_generation: targetGeneration, source: "visual", edit_action: "skip",
      start_seconds: 3674, end_seconds: 3675,
    })]);
    await expectNoHorizontalPageOverflow(page);
    await dialog.getByRole("button", { name: "Continue to approval" }).click();
    await expect(dialog.getByText(/For large files, it reads sampled blocks/)).toBeVisible();
    const approve = dialog.getByRole("button", { name: "Approve for this version" });
    await expect(approve).toBeEnabled();
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    expect(approvals).toHaveLength(0);
    await approve.press("Enter");
    try {
      await expect(dialog.getByText("Checking the target file’s fingerprint before saving…")).toBeVisible();
      const queueButton = dialog.getByRole("button", { name: "Queue", exact: true });
      if (await queueButton.getAttribute("aria-expanded") !== "true") await queueButton.click();
      await expect(dialog.getByRole("progressbar", { name: "File fingerprint progress" })).toHaveAttribute("value", "50");
      await expectNoHorizontalPageOverflow(page);
    } finally {
      finishApproval();
    }
    await expect(dialog).toHaveCount(0);
    expect(approvals).toEqual([expect.objectContaining({ source_scan_id: oldScan.id, token, confirmed: true,
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-2" },
    })]);
  });
}
