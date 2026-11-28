import { expect, test, type Locator } from "@playwright/test";
import { json, mockApi } from "./app-fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  const now = new Date().toISOString();
  const scan = {
    is_own: true, name: null, origin: "generated", created_at: now, updated_at: now,
    scan_started_at: now, scan_duration_seconds: 65, item_type: "Movie",
  };
  await page.route("**/api/scans/queue", (route) => json(route, [{
    ...scan, id: "active-scan", scan_id: 1, queue_id: 1, sort_order: 0,
    item_name: "Active film", status: "scanning",
    scan_audio: true, audio_model: "whisper", audio_filter: "better_profanity",
    scan_video: true, video_model: "nudenet", coarse_scan_enabled: true, coarse_scan_interval: 2.5,
    qwen_summaries_per_segment: 4, qwen_segment_join_gap_seconds: 2,
    progress_percent: 35, progress_message: "Analyzing audio",
  }]));
  await page.route("**/api/scans/history", (route) => json(route, [{
    ...scan, id: "complete-scan", scan_id: 2, item_name: "Completed film",
    status: "complete", finding_count: 3, scan_video: true, video_model: "nudenet",
    qwen_summaries_per_segment: 4, qwen_segment_join_gap_seconds: 2,
    stage_coverage: [
      { stage: "visual", status: "unknown" },
      { stage: "visual_qwen", status: "unknown" },
      { stage: "visual_qwen:scene_0005", status: "complete", scanner_behavior_version: "scanner-1" },
    ],
  }]));
  await page.goto("/#/settings");
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
});

/** Check the painted tooltip at its corners and center, including past a scroll boundary. */
async function expectUnclipped(tooltip: Locator, scrollContainer?: Locator) {
  await expect(tooltip).toBeVisible();
  const clip = await scrollContainer?.boundingBox();
  await expect.poll(() => tooltip.evaluate((element, boundary) => {
    const { left, top, right, bottom } = element.getBoundingClientRect();
    const xs = [left + 7, (left + right) / 2, right - 7];
    const ys = [top + 7, (top + bottom) / 2, bottom - 7];
    const points = xs.flatMap((x) => ys.map((y) => [x, y]));
    const insideViewport = left >= 0 && top >= 0
      && right <= document.documentElement.clientWidth && bottom <= window.innerHeight;
    const allPainted = points.every(([x, y]) => element.contains(document.elementFromPoint(x, y)));
    const paintsOutsideScrollContainer = !boundary || points.some(([x, y]) =>
      x < boundary.x || x > boundary.x + boundary.width || y < boundary.y || y > boundary.y + boundary.height
    );
    return { insideViewport, allPainted, paintsOutsideScrollContainer };
  }, clip ?? null)).toEqual({ insideViewport: true, allPainted: true, paintsOutsideScrollContainer: true });
}

test("queue and history show hover options outside their scrolling containers", async ({ page }) => {
  const panel = page.getByRole("region", { name: "Scans queue and history" });
  const queue = panel.getByRole("region", { name: "Scan queue" });
  const history = panel.getByRole("region", { name: "Scan history" });
  const tooltip = page.getByRole("tooltip");
  const activeOptions = queue.getByRole("button", { name: "Show options for Scan #1" });
  await expect(queue.getByRole("progressbar", { name: "Scan #1 progress" })).toHaveJSProperty("value", 35);
  await activeOptions.hover();
  await expect(tooltip).toContainText("whisper transcriber");
  await expect(tooltip).toContainText("Coarse scan every 2.5 s");
  await expect(tooltip).toContainText("Fine scan for flagged scenes or scenes without samples");
  await expect(tooltip).not.toContainText("Fine scan every frame");
  await expectUnclipped(tooltip, queue.getByLabel("Queued scans table"));
  await tooltip.hover();
  await expect(tooltip).toBeVisible();
  await history.getByRole("heading", { name: "History" }).hover();
  await expect(tooltip).toBeHidden();

  await expect(history.getByRole("columnheader", { name: "Options" })).toHaveCount(0);
  await expect(history.getByRole("columnheader", { name: "Duration" })).toBeVisible();
  const row = history.getByRole("row", { name: /Completed film/ });
  await expect(row.getByText("nudenet video")).toHaveCount(0);
  await expect(row.getByRole("cell", { name: "complete", exact: true })).toBeVisible();
  await expect(row.getByRole("cell", { name: "3", exact: true })).toBeVisible();
  await expect(row.getByRole("cell", { name: "1:05", exact: true })).toBeVisible();
  await activeOptions.click();
  await expect(tooltip).toContainText("whisper transcriber");
  await row.getByRole("button", { name: "Show options for Scan #2" }).hover();
  await expect(tooltip).toHaveCount(1);
  await expect(tooltip).toContainText("nudenet video");
  await expect(tooltip).toContainText("Fine scan every frame");
  await expect(tooltip).toContainText("Scanner scanner-1");
  await expect(tooltip).not.toContainText(/visual: unknown|visual_qwen|Scanner unknown/i);
  await expect(activeOptions).toHaveAttribute("aria-expanded", "false");
  await expectUnclipped(tooltip, panel);
});

test("keyboard focus opens options and Escape dismisses only the tooltip", async ({ page }) => {
  const panel = page.getByRole("region", { name: "Scans queue and history" });
  const tooltip = page.getByRole("tooltip");
  const options = panel.getByRole("button", { name: "Show options for Scan #1" });
  await options.focus();
  await expect(tooltip).toContainText("whisper transcriber");
  await page.keyboard.press("Escape");
  await expect(tooltip).toBeHidden();
  await expect(options).toBeFocused();
  await expect(panel).toBeVisible();
});

test.describe("narrow touch viewport", () => {
  test.use({ hasTouch: true, viewport: { width: 320, height: 740 } });

  test("tapping the info icon opens and closes options within the viewport", async ({ page }) => {
    const options = page.getByRole("button", { name: "Show options for Scan #1" });
    const tooltip = page.getByRole("tooltip");
    await options.tap();
    await expect(tooltip).toContainText("whisper transcriber");
    await expectUnclipped(tooltip);
    await options.tap();
    await expect(tooltip).toBeHidden();
  });
});
