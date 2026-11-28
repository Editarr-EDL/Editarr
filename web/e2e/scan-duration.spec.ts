import { expect, test } from "@playwright/test";
import { expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";

test("scan queue duration follows the worker across an error and a retry", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 320, height: 900 });
  const start = Date.parse("2026-11-17T12:00:00Z");
  const serverOffset = 3_600_000;
  await page.clock.install({ time: start - 60_000 });
  await page.clock.pauseAt(start);
  let status = "scanning";
  let elapsed = 0;
  let attemptStartedAt = start - 5_000;
  await page.route("**/api/scans/queue", async (route) => {
    const now = await page.evaluate(() => Date.now());
    const sample = new Date(now + serverOffset).toISOString();
    await json(route, [{
      id: "scan-1", scan_id: 1, queue_id: 1, is_own: true,
      jellyfin_item_id: "movie-1", item_name: "Example Movie", status,
      scan_audio: true, audio_model: "whisper", created_at: sample, updated_at: sample,
      scan_duration_seconds: elapsed + (status === "scanning" ? (now - attemptStartedAt) / 1_000 : 0),
      scan_duration_as_of: status === "scanning" ? sample : null,
    }]);
  });
  await page.route("**/api/scans/queue/1/resume", async (route) => {
    status = "queued";
    await json(route, { resumed: true });
  });
  await page.goto("/#/settings");
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
  const row = page.getByRole("region", { name: "Scan queue" })
    .getByRole("row").filter({ hasText: "Example Movie" });
  const duration = row.locator("time");
  await expect(duration).toHaveText("00:00:05");
  await page.clock.runFor(1_000);
  await expect(duration).toHaveText("00:00:06");

  elapsed = 6;
  status = "orphaned";
  await page.clock.runFor(1_000);
  await expect(row.getByRole("button", { name: "Resume" })).toBeVisible();
  await page.clock.fastForward(60_000);
  await expect(duration).toHaveText("00:00:06");

  await row.getByRole("button", { name: "Resume" }).click();
  await expect(row.getByText("queued", { exact: true })).toBeVisible();
  await expect(duration).toHaveText("00:00:06");
  attemptStartedAt = await page.evaluate(() => Date.now());
  status = "scanning";
  await page.clock.runFor(2_000);
  await expect(duration).toHaveText("00:00:08");
});

test("pauses a running scan, reloads its saved progress, and resumes through the queue", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 320, height: 900 });
  const start = Date.parse("2026-11-17T12:00:00Z");
  await page.clock.install({ time: start - 60_000 });
  await page.clock.pauseAt(start);
  const queue = {
    id: "scan-2", scan_id: 2, queue_id: 2, is_own: true,
    jellyfin_item_id: "movie-1", item_name: "Example Movie", status: "scanning",
    scan_audio: true, audio_model: "whisper", created_at: new Date(start).toISOString(),
    updated_at: new Date(start).toISOString(), progress_percent: 65,
    scan_duration_seconds: 3_723, scan_duration_as_of: new Date(start).toISOString() as string | null,
  };
  await page.route("**/api/scans/queue", async (route) => json(route, [queue]));
  await page.route("**/api/scans/queue/2/pause", async (route) => {
    expect(route.request().method()).toBe("POST");
    queue.status = "pausing";
    await json(route, { paused: false, pausing: true });
  });
  await page.route("**/api/scans/queue/2/resume", async (route) => {
    expect(route.request().method()).toBe("POST");
    queue.status = "queued";
    await json(route, { resumed: true });
  });
  await page.goto("/#/settings");
  const openQueue = () => page.getByRole("button", { name: "Open scans queue and history" }).click();
  await openQueue();
  const row = page.getByRole("region", { name: "Scan queue" }).getByRole("row").filter({ hasText: "Example Movie" });
  await expect(row.locator("time")).toHaveText("01:02:03");
  await row.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(row.getByText("Pausing…", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: "Resume", exact: true })).toHaveCount(0);
  queue.status = "paused";
  queue.scan_duration_as_of = null;
  await page.clock.runFor(1_600);
  await expect(row.getByText("Paused", { exact: true })).toBeVisible();
  await expectNoHorizontalPageOverflow(page);

  await page.reload();
  await openQueue();
  await expect(row.getByText("Paused", { exact: true })).toBeVisible();
  await expect(row.locator("time")).toHaveText("01:02:03");
  await expect(row.getByRole("progressbar")).toHaveAttribute("value", "65");
  await page.setViewportSize({ width: 1440, height: 900 });
  await expectNoHorizontalPageOverflow(page);
  await row.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(row.getByText("queued", { exact: true })).toBeVisible();
  await expect(row.locator("time")).toHaveText("01:02:03");
});
