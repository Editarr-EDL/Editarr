import { expect, test } from "@playwright/test";
import { json, mockApi } from "./app-fixtures";

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
  await expect(duration).toHaveText("0:05");
  await page.clock.runFor(1_000);
  await expect(duration).toHaveText("0:06");

  elapsed = 6;
  status = "orphaned";
  await page.clock.runFor(1_000);
  await expect(row.getByRole("button", { name: "Resume" })).toBeVisible();
  await page.clock.fastForward(60_000);
  await expect(duration).toHaveText("0:06");

  await row.getByRole("button", { name: "Resume" }).click();
  await expect(row.getByText("queued", { exact: true })).toBeVisible();
  await expect(duration).toHaveText("0:06");
  attemptStartedAt = await page.evaluate(() => Date.now());
  status = "scanning";
  await page.clock.runFor(2_000);
  await expect(duration).toHaveText("0:08");
});
