import { chooseExportFormat, chooseWorkflowStep, openReviewSources } from "./workflow-actions";
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { authenticationStatus, expectNoHorizontalPageOverflow as expectNoOverflow, json } from "./app-fixtures";

const emptyExport = (format: "edl" | "mcf") => ({
  format, present: false, exported_at: null, review_id: null,
  exported_format_revision: null, current: false, outdated: false,
  exported_scans: [], exported_review: null as { name: string } | null,
});

const scan = {
  id: "scan-1", scan_id: 1, media_item_id: "media-1", jellyfin_item_id: "movie-1", owner_id: 1, is_own: true,
  scan_mode: "content",
  library_id: "movies", item_name: "Example Movie", item_type: "Movie",
  media_source_id: "source-1", media_source_name: "4K", status: "complete",
  scan_audio: true, audio_model: "whisper", audio_filter: "better_profanity",
  scan_video: true, video_model: "nudenet", coarse_scan_interval: null,
  coarse_scan_enabled: false, qwen_summaries_per_segment: 3,
  qwen_segment_join_gap_seconds: 0, duration_seconds: 120, video_hash: null, checkpoint_stage: "complete",
  error_message: null, finding_count: 5, manual: false, edited: false,
  scan_started_at: "2026-09-18T10:00:00Z", scan_duration_seconds: 5,
  created_at: "2026-09-18T10:00:00Z", updated_at: "2026-09-18T10:00:05Z",
  name: "Imported scan", origin: "imported", import_source_name: null,
  imported_at: "2026-09-18T10:00:00Z", mcf_document: null, credits_detection: null,
};
const creditsCandidate = {
  id: "candidate-1", boundary_seconds: 90, range_start_seconds: 84, range_end_seconds: 96,
  confidence: 0.42, confidence_band: "low", method: "chapters",
  evidence: [{ kind: "chapter_marker", timestamp_seconds: 90, score: 0.7, detail_code: "credits_chapter", details: "End Credits" }],
};
const creditsScan = {
  ...scan,
  id: "credits-scan", scan_id: 2, scan_mode: "credits_detection", name: "Credits detection",
  scan_audio: false, audio_model: null, audio_filter: null, video_model: null,
  credits_detection: {
    configuration: { detector_version: "credits-detector-v1", tail_window_seconds: 45, frame_sample_interval_seconds: 2, qwen_model: null },
    detector_version: "credits-detector-v1", cache_key: "a".repeat(64),
    result: { outcome: "candidate", candidates: [creditsCandidate], best_candidate_id: creditsCandidate.id },
    completed_at: "2026-09-18T10:00:05Z",
  },
};
const creditsPreviewMp4 = Buffer.from((
  "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAPUbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAggAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAA" +
  "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAv90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAggAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAA" +
  "ABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAIIAAAEAAABAAAAAAJ3bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAyAAAAGgBVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRl" +
  "b0hhbmRsZXIAAAACIm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAeJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAA" +
  "AAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFExhdmM2My4xLjEwMSBsaWJ4MjY0AAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAMg8SJZYAQAGaOvjyyLA/fj4AAAAABBw" +
  "YXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAADSVAAAAAAAAABhzdHRzAAAAAAAAAAEAAAANAAACAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAeGN0dHMAAAAAAAAADQAAAAEAAAQAAAAAAQAACgAAAAABAAAEAAAAAAEA" +
  "AAAAAAAAAQAAAgAAAAABAAAKAAAAAAEAAAQAAAAAAQAAAAAAAAABAAACAAAAAAEAAAoAAAAAAQAABAAAAAABAAAAAAAAAAEAAAIAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAANAAAAAQAAAEhzdHN6AAAAAAAAAAAA" +
  "AAANAAACywAAAAwAAAAMAAAADAAAAAwAAAASAAAADgAAAAwAAAAMAAAAEgAAAA4AAAAMAAAADAAAABRzdGNvAAAAAAAAAAEAAAQEAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwA" +
  "AAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2My4xLjEwMQAAAAhmcmVlAAADc21kYXQAAAKuBgX//6rcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIyIGIzNTYw" +
  "NWEgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6" +
  "MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0w" +
  "IGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFj" +
  "ZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9" +
  "MiBrZXlpbnQ9MjUwIGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4" +
  "PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAVZYiEADv//uOr+BTRr6HPd6RRN7BBAAAACEGaJGxDf/7gAAAACEGeQniF/8GBAAAACAGeYXRCv8SAAAAACAGeY2pCv8SBAAAADkGaaEmo" +
  "QWiZTAhn//7hAAAACkGehkURLC//wYEAAAAIAZ6ldEK/xIEAAAAIAZ6nakK/xIAAAAAOQZqsSahBbJlMCFf//sAAAAAKQZ7KRRUsL//BgQAAAAgBnul0Qr/EgAAAAAgBnutqQr/EgA=="
), "base64");

function review(itemId: string, id: string) {
  const exports = { exports: { edl: emptyExport("edl"), mcf: emptyExport("mcf") } };
  const value = {
    id, media_item_id: "media-1", target: { source: "jellyfin", item_id: itemId, media_source_id: "source-1" },
    name: "Catalog review",
    revision: 1, content_revision: 1, edl_content_revision: 1, mcf_content_revision: 2,
    created_at: "2026-09-18T10:00:00Z", updated_at: "2026-09-18T10:00:00Z",
    exports,
    synchronization: { start_seconds: 0, end_seconds: 120, end_mode: "before_credits" }, accepted_credits: null,
    memberships: [{ scan_id: scan.id, role: "audio" }], scans: [{ scan, role: "audio" }],
    filters: [
      {
        id: "audio-1", source_scan_id: scan.id, source_finding_id: 1, source: "audio",
        category: "language", subcategory: "swearing", severity: "medium",
        mcf_channel: "audio", mcf_comment: null, label: "Word", details: "First dialogue",
        group_key: null, start_seconds: 15, end_seconds: 16, confidence: 1,
        edit_action: "mute", selected: true,
      },
      {
        id: "audio-2", source_scan_id: scan.id, source_finding_id: 2, source: "audio",
        category: "language", subcategory: "swearing", severity: "low",
        mcf_channel: "audio", mcf_comment: null, label: "Words", details: "Earlier dialogue",
        group_key: null, start_seconds: 5, end_seconds: 6, confidence: 1,
        edit_action: "mute", selected: true,
      },
      {
        id: "video-linked-1", source_scan_id: scan.id, source_finding_id: 3, source: "visual",
        category: "violence", subcategory: "violence", severity: "medium",
        mcf_channel: "video", mcf_comment: null, label: "Fight", details: "Linked fight",
        group_key: "fight", start_seconds: 30, end_seconds: 40, confidence: 1,
        edit_action: "skip", selected: false,
      },
      {
        id: "video-linked-2", source_scan_id: scan.id, source_finding_id: 4, source: "visual",
        category: "violence", subcategory: "weapons", severity: "high",
        mcf_channel: "video", mcf_comment: null, label: "Weapon", details: "Linked weapon",
        group_key: "weapons", start_seconds: 35, end_seconds: 45, confidence: 1,
        edit_action: "skip", selected: false,
      },
      {
        id: "video-legacy", source_scan_id: scan.id, source_finding_id: 5, source: "visual",
        category: null, subcategory: null, severity: null,
        mcf_channel: null, mcf_comment: null, label: "Legacy", details: "Legacy finding",
        group_key: "legacy", start_seconds: 55, end_seconds: 56, confidence: 1,
        edit_action: "skip", selected: false,
      },
    ],
    mcf_document: {
      title: "Example Movie", year: 2025, work_type: "movie", season: null,
      episode: null, source_url: null, imdb_url: null, release: null, comment: null,
      material_start_seconds: 0, material_end_seconds: 120,
    },
  };
  const reference = { id: scan.id, scan_id: scan.scan_id, name: scan.name, origin: scan.origin, role: "audio" };
  const exportedReview = { name: value.name };
  value.exports.exports.edl = { ...emptyExport("edl"), present: true, exported_at: "2026-09-18T11:00:00Z", review_id: id, exported_format_revision: 1, current: true, exported_scans: [reference], exported_review: exportedReview };
  value.exports.exports.mcf = { ...emptyExport("mcf"), present: true, exported_at: "2026-09-18T11:05:00Z", review_id: id, exported_format_revision: 1, outdated: true, exported_scans: [reference], exported_review: exportedReview };
  return value;
}

const catalogReview = review("movie-1", "review-catalog");
const item = {
  id: "movie-1", library_id: "movies", parent_id: null, item_type: "Movie",
  name: "Example Movie", sort_name: "Example Movie", year: 2025,
  overview: "A test movie.", path: "/media/example.mkv", official_rating: "PG-13",
  index_number: null, runtime_ticks: null, duration_seconds: 120, resolution: "4K",
  media_format: "MKV", media_provider_id: null, media_source_count: 1,
  versions: [{
    id: "source-1", name: "4K", scene_name: "Example.Movie", release_group: "GROUP",
    edition: null, path: "/media/example.mkv", container: "mkv", resolution: "4K",
    media_format: "MKV", duration_seconds: 120, size_bytes: 1000,
    exports: catalogReview.exports,
  }],
  media_streams: [], image_tags: {}, image_url: null,
  exports: catalogReview.exports,
};

async function mockApi(page: Page) {
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/auth/status") return json(route, authenticationStatus("authenticated"));
    if (path === "/api/mcf-registry") return json(route, { families: [
      { token: "language", label: "Language", categories: [{ token: "swearing", label: "Swearing" }] },
      { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }, { token: "weapons", label: "Weapons" }] },
    ] });
    if (path === "/api/settings/jellyfin") return json(route, { server_url: "http://jellyfin.test", api_key_configured: true, last_checked_at: null, last_error: null });
    if (path === "/api/libraries") return json(route, [{ id: "movies", name: "Movies", collection_type: "movies", locations: [] }]);
    if (path === "/api/library-items/movie-1") return json(route, item);
    if (path === "/api/library-items/movie-1/scans") return json(route, [scan, creditsScan]);
    if (path === "/api/library-items/movie-1/reviews") return json(route, [catalogReview]);
    if (path === "/api/reviews/review-catalog") return json(route, catalogReview);
    if (path === "/api/reviews/review-catalog/subtitles/sources") return json(route, {
      sources: [], audio_sources: [], audio_source_id: null, fallback: "scan_required", published_subtitles: [],
    });
    if (path === "/api/media/subtitles/publications") return json(route, { published_subtitles: [] });
    if (path === "/api/reviews/review-catalog/mcf/preflight") return json(route, {
      review_id: catalogReview.id, revision: 1, eligible: false,
      issues: [{ code: "invalid_mcf_metadata", field: "title", filter_id: null, filter_position: null, message: "Review title needs attention." }],
    });
    if (path === "/api/credits-detections" && route.request().method() === "POST") {
      return json(route, { scan: creditsScan, queue_id: null, model_consent: null }, 202);
    }
    if (path === "/api/credits-detections/credits-scan/clip") {
      return route.fulfill({ status: 200, contentType: "video/mp4", body: creditsPreviewMp4 });
    }
    if (path === "/api/credits-detections/credits-scan/accept" && route.request().method() === "POST") {
      return json(route, {
        accepted_credits: { scan_id: creditsScan.id, source_finding_id: 6, candidate_id: creditsCandidate.id, detected_boundary_seconds: 90, accepted_boundary_seconds: 91, confidence: 0.42, method: "chapters", accepted_at: "2026-09-18T10:01:00Z" },
        membership: { scan_id: creditsScan.id, role: "video" },
        filter: { id: "credits-filter", source_scan_id: creditsScan.id, source_finding_id: 6, source: "visual", category: "dispensable_scenes", subcategory: "dispensable", severity: "medium", mcf_channel: "video", mcf_comment: "End Credits", label: "End Credits", details: "End Credits", group_key: null, start_seconds: 91, end_seconds: 120, confidence: 0.42, edit_action: "skip", selected: true },
      });
    }
    if (path === "/api/capabilities") return json(route, { audio_models: [], audio_filters: [], video_models: [], qwen_models: [{ id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true }, { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B", recommended: false }], model_capabilities: [] });
    if (path === "/api/scans/queue") return json(route, []);
    if (path === "/api/scans/history") return json(route, [scan, creditsScan]);
    return json(route, {});
  });
}

async function expectAxeClean(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(result.violations, result.violations.map((violation) => `${violation.id}: ${violation.help}`).join("\n")).toEqual([]);
}

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 320, height: 900 }]) {
  test(`MTS-07 credits result clip and edited acceptance work at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto("/#/library/movies/item/movie-1");
    const panel = page.locator(".saved-review-panel");
    const trigger = panel.getByRole("button", { name: "Review Credits Result" });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Review Credits Result" });
    await expect(dialog.getByRole("status")).toContainText("Low confidence");

    const video = dialog.getByLabel("Credits boundary preview");
    await expect(video).toBeVisible();
    const playback = await video.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      if (element.readyState < 2) await new Promise<void>((resolve) => element.addEventListener("loadeddata", () => resolve(), { once: true }));
      await element.play();
      await new Promise<void>((resolve) => element.addEventListener("timeupdate", () => resolve(), { once: true }));
      const canvas = document.createElement("canvas");
      canvas.width = 16; canvas.height = 16;
      const context = canvas.getContext("2d")!;
      context.drawImage(element, 0, 0, 16, 16);
      return { currentTime: element.currentTime, pixel: [...context.getImageData(8, 8, 1, 1).data] };
    });
    expect(playback.currentTime).toBeGreaterThan(0);
    expect(playback.pixel[0]).toBeGreaterThan(playback.pixel[1]);

    await dialog.getByLabel("Accepted credits boundary").fill("00:01:31.000");
    const acceptedRequest = page.waitForRequest((request) => request.url().includes("/credits-scan/accept"));
    await dialog.getByRole("button", { name: "Accept boundary" }).click();
    expect((await acceptedRequest).postDataJSON().accepted_boundary_seconds).toBe(91);
    await expect(dialog).not.toBeAttached();
    await expect(trigger).toBeFocused();
    await expectNoOverflow(page);
    await expectAxeClean(page);
    await page.screenshot({ path: `test-results/mts07-credits-${viewport.name}.png`, fullPage: true });
  });

  test(`MCF-10 catalog controls are accessible at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto("/#/library/movies/item/movie-1");
    await expect(page.getByRole("heading", { name: "Example Movie", level: 1 })).toBeVisible();
    const published = page.getByRole("region", { name: "Published files" });
    await expect(published.getByText("EDL · Current")).toBeVisible();
    await expect(published.getByText("MCF · Outdated")).toBeVisible();
    await chooseWorkflowStep(page, "Scans");
    const scanRows = await page.locator('.bounded-scan-list[aria-label="Scan history"] .scan-history-row').evaluateAll((rows) => rows.map((row) => {
      const box = row.getBoundingClientRect();
      const content = row.querySelector(":scope > div")!.getBoundingClientRect();
      const action = row.querySelector(":scope > button")!.getBoundingClientRect();
      return { top: box.top, bottom: box.bottom, scrollHeight: row.scrollHeight, clientHeight: row.clientHeight, contentRight: content.right, actionLeft: action.left };
    }));
    expect(scanRows).toHaveLength(2);
    expect(scanRows[0].bottom).toBeLessThanOrEqual(scanRows[1].top);
    for (const row of scanRows) {
      expect(row.scrollHeight).toBeLessThanOrEqual(row.clientHeight);
      expect(row.contentRight).toBeLessThanOrEqual(row.actionLeft);
    }

    const metadata = page.locator(".detail-meta");
    const showDetails = metadata.getByRole("button", { name: "Show Details", exact: true });
    const hideDetails = metadata.getByRole("button", { name: "Hide Details", exact: true });
    const files = page.getByRole("region", { name: "Media files" });
    await expect(page.getByRole("combobox", { name: "Item history" })).toHaveCount(0);
    await expect(showDetails).toHaveAttribute("aria-expanded", "false");
    await showDetails.focus();
    await showDetails.press("Enter");
    await expect(hideDetails).toHaveAttribute("aria-expanded", "true");
    await expect(files).toBeVisible();
    await hideDetails.press("Space");
    await expect(showDetails).toHaveAttribute("aria-expanded", "false");
    await expect(files).toBeHidden();
    await showDetails.click();
    await expect(hideDetails).toBeVisible();
    const manage = page.getByRole("button", { name: "Manage legacy exports" });
    await manage.click();
    const manageDialog = page.getByRole("dialog", { name: "Manage exports" });
    await expect(manageDialog.getByRole("region", { name: "EDL export" })).toContainText("Catalog review");
    await expect(manageDialog.getByRole("region", { name: "MCF export" })).toContainText("Outdated");
    await manageDialog.getByRole("button", { name: "Close" }).click();

    const importButton = page.getByRole("button", { name: "Import", exact: true });
    await importButton.click();
    const importDialog = page.getByRole("dialog", { name: "Import" });
    await importDialog.getByRole("button", { name: "MCF" }).click();
    await expect(importDialog.getByText(/Movie Content Filter 1\.0\.0 or 1\.1\.0/)).toBeVisible();
    await expect(importDialog.getByText("Imports into Jellyfin item Example Movie, version 4K")).toBeVisible();
    await expect(importDialog.getByLabel("MCF file")).toHaveAttribute("accept", ".mcf,text/plain,text/mcf+vtt");
    await expect(importDialog.getByRole("button", { name: "Review Credits Result" })).toBeVisible();
    await expect(importDialog.getByLabel("Target START")).toHaveValue("00:00:00.000");
    await expect(importDialog.getByLabel("Before-credits END")).toHaveValue("");
    await importDialog.getByRole("button", { name: "File end" }).click();
    await expect(importDialog.getByLabel("File END")).toHaveValue("00:02:00.000");
    await expect(importDialog.getByRole("button", { name: "Scanner path" })).toHaveCount(0);
    await importDialog.getByRole("button", { name: "Cancel" }).click();
    await expect(importButton).toBeFocused();

    const exportButton = await chooseExportFormat(page, "EDL");
    await exportButton.focus();
    await exportButton.press("Enter");
    const edlDialog = page.getByRole("dialog", { name: "Export" });
    await expect(edlDialog.getByRole("button", { name: "Download EDL" })).toBeVisible();
    await edlDialog.press("Escape");
    await expect(exportButton).toBeFocused();
    await (await chooseExportFormat(page, "MCF")).click();
    await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Publish MCF" }).click();
    const issue = page.getByRole("alert", { name: "MCF export needs attention" });
    await issue.getByRole("button", { name: "Review title needs attention." }).click();
    await expect(page.getByLabel("MCF title")).toBeFocused();

    await expectNoOverflow(page);
    await expectAxeClean(page);
    await page.screenshot({ path: `test-results/mcf10-catalog-${viewport.name}.png`, fullPage: true });
  });

  test(`MCF-13 tiered Review tables are accessible at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApi(page);
    await page.goto("/#/library/movies/item/movie-1");

    const membership = await openReviewSources(page);
    const choice = membership.getByRole("checkbox", { name: "Imported scan", exact: true });
    const info = membership.getByRole("button", { name: "Show scan details for Imported scan" });
    await expect(choice).toBeChecked();
    await info.hover();
    await expect(page.getByRole("tooltip")).toContainText("Scan #1");
    await expect(page.getByRole("tooltip")).toContainText(/imported/i);
    await info.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    // Escape closes the details first and leaves the Sources popover open.
    await expect(membership).toBeVisible();
    await expect(choice).toBeChecked();
    await membership.getByRole("button", { name: "Done" }).click();

    await page.getByLabel("Sort", { exact: true }).selectOption("category");
    const video = page.getByRole("region", { name: "Video review filters" });
    const audio = page.getByRole("region", { name: "Audio review filters" });
    await expect(video.getByRole("button", { name: /Violence family/ })).toHaveAttribute("aria-expanded", "true");
    await expect(video.getByRole("button", { name: /Unclassified family/ })).toHaveAttribute("aria-expanded", "true");
    await expect(video.getByRole("button", { name: /Unclassified subcategory/ })).toHaveAttribute("aria-expanded", "false");
    await expect(video.getByRole("button", { name: /Violence family/ })).not.toContainText("Medium");
    await expect(video.getByRole("button", { name: /Violence family/ })).not.toContainText("Video");

    const violenceGeneral = video.getByRole("button", { name: /Violence \(General\) subcategory/ });
    await violenceGeneral.focus();
    await violenceGeneral.press("Enter");
    const fight = video.getByRole("button", { name: /fight finding group/ });
    await expect(fight).toHaveAttribute("aria-expanded", "false");
    await fight.focus();
    await fight.press("Enter");
    await video.getByRole("button", { name: /Weapons subcategory/ }).click();
    await video.getByRole("button", { name: /weapons finding group/ }).click();

    const linkedRows = video.getByRole("checkbox", { name: /^Include Linked fight; Linked weapon at 0:30/ });
    await expect(linkedRows).toHaveCount(2);
    await expect(video.getByRole("columnheader", { name: "Channel" })).toHaveCount(0);
    const sourceInfo = video.getByRole("button", { name: "Show scan details for Imported scan" }).first();
    await sourceInfo.focus();
    await expect(page.getByRole("tooltip")).toContainText("Scan #1");
    await sourceInfo.press("Escape");
    await expect(video.getByRole("img", { name: "Linked result repeated in another category" })).toHaveCount(4);
    await linkedRows.first().click();
    await expect(linkedRows.first()).toBeChecked();
    await expect(linkedRows.last()).toBeChecked();

    const sort = (section: typeof video, label: string) => section.getByRole("group", { name: /^Sort / }).getByRole("button", { name: new RegExp(`^${label}`) });
    await expect(sort(video, "Time")).toHaveAccessibleName("Time, ascending");
    await expect(sort(video, "Time")).toHaveAttribute("aria-pressed", "true");
    await expect(sort(audio, "Time")).toHaveAccessibleName("Time, ascending");
    await sort(video, "Severity").click();
    await expect(sort(video, "Severity")).toHaveAccessibleName("Severity, ascending");
    await expect(sort(video, "Time")).toHaveAttribute("aria-pressed", "false");
    await expect(sort(audio, "Time")).toHaveAccessibleName("Time, ascending");
    await sort(video, "Severity").click();
    await expect(sort(video, "Severity")).toHaveAccessibleName("Severity, descending");
    await expect(sort(video, "Severity")).toContainText("Severity ▼");
    await expect(sort(video, "Severity")).toHaveCSS("display", /flex/);
    await expect(sort(video, "Severity")).toHaveCSS("white-space", "nowrap");
    // The tables fit their width: nothing scrolls sideways.
    for (const table of [video.getByRole("table", { name: "Video review filters table" }), audio.getByRole("table", { name: "Audio review filters table" })]) {
      expect(await table.evaluate((element) => element.parentElement!.scrollWidth - element.parentElement!.clientWidth)).toBe(0);
    }
    await expectNoOverflow(page);
    await expectAxeClean(page);
    await page.screenshot({ path: `test-results/mcf13-review-${viewport.name}.png`, fullPage: true });
  });
}
