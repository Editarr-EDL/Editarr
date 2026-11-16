import { expect, test } from "@playwright/test";
import { CAPABILITIES } from "../src/auth/capabilities";
import { libraries, items, series, season, episode, json, mockApi, expectNoHorizontalPageOverflow, authenticationStatus } from "./app-fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/settings");
});

for (const width of [320, 1440]) {
  test(`limits idle requests and refreshes the queue on demand at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.clock.install();
    await page.clock.pauseAt(new Date(Date.now() + 1_000));
    const requests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url());
    });
    for (const [route, heading] of [
      ["settings/general", "General"],
      ["library/movies/item/movie-1", "Example Movie"],
      ["archive", "Archive"],
    ]) {
      await page.goto(`/?idle-route=${encodeURIComponent(route)}#/${route}`);
      await expect(page.locator(".app-main").getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      const initialRequests = requests.length;
      await page.clock.fastForward(29_000);
      expect(requests).toHaveLength(initialRequests);
      await page.clock.fastForward(1_100);
      await expect.poll(() => requests.length).toBeGreaterThan(initialRequests);
      await page.waitForLoadState("networkidle");
      const beforeOpening = requests.length;
      await page.getByRole("button", { name: "Open scans queue and history" }).click();
      await expect(page.getByRole("region", { name: "Scans queue and history" })).toBeVisible();
      await expect.poll(() => requests.length).toBeGreaterThan(beforeOpening);
      await expectNoHorizontalPageOverflow(page);
    }
  });
}

for (const width of [320, 375, 760, 761, 768, 1024, 1280, 1920]) {
  test(`keeps primary pages within a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const routes = [
      ["settings/connections", "Connections"], ["settings/filters", "Filters"], ["settings/filters/server", "Filters"],
      ["settings/models", "Model Storage"], ["settings/general", "General"],
      ["library/movies", "Movies"], ["library/movies/item/movie-1", "Example Movie"],
      ["archive", "Archive"],
    ];
    for (const [route, heading] of routes) {
      await page.goto(`/#/${route}`);
      await expect(page.locator(".app-main").getByRole("heading", { name: heading, exact: true }).first()).toBeVisible();
      await expectNoHorizontalPageOverflow(page);
      const layout = await page.evaluate(() => {
        const main = document.querySelector(".app-main")!.getBoundingClientRect();
        const control = document.querySelector(".scans-floating-control")!.getBoundingClientRect();
        return { mainLeft: main.left, mainRight: main.right, controlLeft: control.left, controlRight: control.right };
      });
      expect(layout.mainLeft, route).toBeGreaterThanOrEqual(0);
      expect(layout.mainRight, route).toBeLessThanOrEqual(width + 1);
      expect(layout.controlLeft, route).toBeGreaterThanOrEqual(0);
      expect(layout.controlRight, route).toBeLessThanOrEqual(width + 1);
    }
    await page.getByRole("button", { name: "Open scans queue and history" }).click();
    const popover = page.locator(".scans-floating-popover");
    await expect(popover).toBeVisible();
    const bounds = await popover.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(901);
  });
}

test("wraps the library toolbar without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.getByRole("button", { name: "Movies" }).click();
  await expect(page.getByRole("heading", { name: "Movies" })).toBeVisible();

  const toolbar = await page.locator(".library-toolbar").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    rowCount: new Set(Array.from(element.children, (child) => child.offsetTop)).size,
  }));
  expect(toolbar.scrollWidth).toBeLessThanOrEqual(toolbar.clientWidth);
  expect(toolbar.rowCount).toBeGreaterThan(1);
});

test("creates a Editarr scan from the item detail without entering a path", async ({ page }) => {
  const track = { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2,
    disposition: 1, start_seconds: 0, duration_seconds: 120, time_base: "1/48000" };
  await page.route("**/api/media/audio-tracks?*", (route) => {
    const version = (new URL(route.request().url()).searchParams.get("media_source_id") === "source-2" ? "b" : "a").repeat(64);
    return json(route, { file_version: version, tracks: [track],
      selection: { file_version: version, track }, selection_stale: false });
  });
  await page.route("**/api/media/audio-tracks/selection", (route) => json(route, route.request().postDataJSON().selection));
  await page.getByRole("button", { name: "Movies" }).click();
  await page.getByRole("button", { name: "Open Example Movie" }).click();
  await page.getByRole("button", { name: "New Scan" }).click();
  await expect(page.getByRole("dialog", { name: "New Scan" })).toBeVisible();
  await expect(page.getByLabel("Version", { exact: true })).toHaveValue("source-1");
  await page.getByLabel("Version", { exact: true }).selectOption("source-2");
  await expect(page.getByLabel("Version", { exact: true })).toHaveValue("source-2");
  await page.getByRole("button", { name: "Add to Queue" }).click();
  await expect(page.getByText("Scan 1 added to the queue.")).toBeVisible();
});

test("keeps a long scan selector bounded and keyboard-scrollable", async ({ page }) => {
  const scan = (index: number) => ({
    id: index === 1 ? "scan-previous" : `scan-${index}`,
    scan_id: index, media_item_id: "media-1", jellyfin_item_id: "movie-1",
    item_name: "Example Movie", item_type: "Movie", status: "complete", scan_audio: true,
    audio_model: "whisper", scan_video: true, video_model: "nudenet", frame_interval: 1,
    speed_up: false, error_message: null, created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
  });
  let scanHistoryRequests = 0;
  await page.route(/\/api\/library-items\/movie-1\/scans(?:\?.*)?$/, async (route) => {
    scanHistoryRequests += 1;
    await json(route, [1, 2, 3, 4, 5].map(scan));
  });
  await page.getByRole("button", { name: "Movies" }).click();
  await page.getByRole("button", { name: "Open Example Movie" }).click();
  const scanHistory = page.getByRole("list", { name: "Scan history" });
  await expect(scanHistory.getByRole("listitem")).toHaveCount(5);
  expect(scanHistoryRequests).toBeGreaterThan(0);
  await expect(scanHistory).toHaveAttribute("tabindex", "0");
  await scanHistory.focus();
  await page.keyboard.press("PageDown");
  await expect.poll(() => scanHistory.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
});

test("drills through a series season and episode", async ({ page }) => {
  await page.route("**/api/libraries/movies/items?*", async (route) => {
    await json(route, { items: [series], total_count: 1 });
  });
  await page.getByRole("button", { name: "Movies" }).click();
  await page.getByRole("button", { name: "Open Example Series" }).click();
  await expect(page.getByRole("heading", { name: "Example Series" })).toBeVisible();
  await page.getByRole("button", { name: "Open Season 1" }).click();
  await expect(page.getByRole("heading", { name: "Season 1" })).toBeVisible();
  await page.getByRole("button", { name: "Episode 1" }).click();
  await expect(page.getByRole("heading", { name: "Episode 1" })).toBeVisible();
});

test("tests a path mapping resolution from Settings", async ({ page }) => {
  await page.getByRole("button", { name: "General" }).click();
  await page.getByLabel("Test a remote path").fill("/media/example.mkv");
  await page.getByRole("button", { name: "Test resolution" }).click();
  await expect(page.getByText("Maps to /mnt/media/example.mkv")).toBeVisible();
});

test("manages path mappings from Settings", async ({ page }) => {
  await page.getByRole("button", { name: "General" }).click();
  const mappingForm = page.locator(".mapping-form");
  await mappingForm.getByLabel("Remote path").fill("/remote/movies");
  await mappingForm.getByLabel("Local path").fill("/local/movies");
  await page.getByRole("button", { name: "Add mapping" }).click();
  await expect(page.getByText("/remote/movies")).toBeVisible();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Local path for mapping 1").fill("/scanner/movies");
  await page.getByRole("button", { name: "Save mapping" }).click();
  await expect(page.getByText("/scanner/movies")).toBeVisible();
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("dialog", { name: "Delete path mapping?" })).toBeVisible();
  await page.getByRole("button", { name: "Delete mapping" }).click();
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByText("Path mapping restored.")).toBeVisible();
});

test("shows active Editarr work in the scans panel", async ({ page }) => {
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
  await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
  const queue = page.getByRole("region", { name: "Scan queue" });
  const queuedScan = queue.getByRole("row").filter({ hasText: "Example Movie" });
  await expect(queuedScan).toBeVisible();
  await expect(queuedScan).toContainText("Scan #1");
});

test("confirms removal of queued Editarr work", async ({ page }) => {
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("dialog", { name: "Remove scan #1?" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("No queued scans.")).toBeVisible();
});

test("shows completed Editarr work in the scans panel", async ({ page }) => {
  await page.getByRole("button", { name: "Open scans queue and history" }).click();
  await expect(page.getByRole("heading", { name: "History", exact: true })).toBeVisible();
  await expect(page.getByText("#2")).toBeVisible();
  await expect(page.getByRole("cell", { name: "complete" })).toBeVisible();
});

for (const width of [320, 1440]) {
  test(`shows the read-only archive only with archive.view at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.getByRole("button", { name: "Archive", exact: true }).click();
    const main = page.locator(".app-main");
    await expect(main.getByRole("heading", { name: /Archived Movie/ })).toBeVisible();
    await expect(main.getByText("Radarr · Movie")).toBeVisible();
    await expect(main.getByRole("button")).toHaveCount(0);
    await expect(main.getByRole("link")).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);

    const status = authenticationStatus("authenticated");
    status.access!.capabilities = CAPABILITIES.filter((capability) => capability !== "archive.view");
    await page.route("**/api/auth/status", (route) => json(route, status));
    await page.goto("/#/archive");
    await page.reload();
    await expect(main.getByRole("heading", { name: "Page unavailable" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Archive", exact: true })).toHaveCount(0);
  });
}
