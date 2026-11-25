/**
 * Community master filter sets in the browser against mocked local routes (docs/community-local-api.md).
 * The real-backend path (find → apply → master view through the local HTTP routes, against the real
 * community service) runs in the community-service multi-instance journey (`tests/community_e2e.py`),
 * because this Playwright suite has no real Python app or community service to launch.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import { mockCommunityMasters } from "./community-masters-mock";
import { chooseWorkflowStep } from "./workflow-actions";

const completedScan = {
  id: "scan-previous", scan_id: 7, media_item_id: "media-1", library_id: "movies", jellyfin_item_id: "movie-1",
  item_name: "Example Movie", item_type: "Movie", media_source_id: "source-1", media_source_name: "4K HDR",
  name: "Audio test scan", origin: "generated", status: "complete", scan_audio: true, audio_model: "whisper",
  scan_video: false, video_model: null, frame_interval: 1, speed_up: false, error_message: null,
  created_at: "2026-08-19T00:00:00Z", updated_at: "2026-08-19T00:00:00Z",
};

async function mockItemPage(page: Page, scans: unknown[] = []) {
  await mockApi(page);
  await page.route("**/api/scans/history", (route) => json(route, scans));
  await page.route("**/api/filter-candidates", (route) => json(route, { media_item_id: "media-1", matches: [] }));
  await page.route("**/api/media/audio-tracks?*", (route) => json(route, {
    file_version: "a".repeat(64), tracks: [], selection: null, selection_stale: false }));
}

/** Reach `control` with the keyboard and check that it shows a visible focus indicator. */
async function expectKeyboardFocusVisible(page: Page, control: Locator) {
  await control.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(control).toBeFocused();
  const style = await control.evaluate((element) => {
    const computed = getComputedStyle(element);
    return { outline: computed.outlineStyle, width: computed.outlineWidth, shadow: computed.boxShadow };
  });
  expect(style.outline !== "none" && style.width !== "0px" || style.shadow !== "none", JSON.stringify(style)).toBe(true);
}

/** The tooltip opens on hover and on keyboard focus. */
async function expectTooltip(page: Page, trigger: Locator, text: RegExp) {
  await trigger.hover();
  await expect(page.getByRole("tooltip")).toContainText(text);
  await page.mouse.move(0, 0);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expectKeyboardFocusVisible(page, trigger);
  await expect(page.getByRole("tooltip")).toContainText(text);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
}

async function checkLayout(page: Page, name: string, width: number) {
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  await page.screenshot({ path: `test-results/community-masters-${name}-${width}.png`, fullPage: true });
}

for (const width of [320, 1440]) {
  test(`${width}px find, apply with alignment and the master view`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockItemPage(page);
    const community = await mockCommunityMasters(page);
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Scans");
    await expect(page.getByRole("button", { name: "Find Filters" })).toHaveCount(0);
    await chooseWorkflowStep(page, "Community");
    const panel = page.getByRole("region", { name: "Community filters", exact: true });
    await expect(panel.getByText(/No community filters for this version yet/)).toBeVisible();
    await checkLayout(page, "community-tab-empty", width);
    await panel.getByRole("button", { name: "Find Filters" }).click();
    // Find Filters in the Community tab searches community versions at once, and nothing local.
    const dialog = page.getByRole("dialog", { name: "Find Filters" });
    await expect(dialog.getByText("80% match")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Associate Selected" })).toHaveCount(0);
    await expect(dialog.getByText("30% match")).toBeVisible();
    await expect(dialog.getByRole("combobox", { name: "Your version" })).toHaveValue("theatrical");
    await expectTooltip(page, dialog.getByRole("button", { name: "Unconfirmed" }), /hasn't been verified yet/);
    await checkLayout(page, "find", width);
    await dialog.getByRole("button", { name: "Use Extended Edition filters" }).click();
    await expect(dialog.getByRole("status").filter({ hasText: "Align them on the item page" })).toBeVisible();
    expect(community.requests.find((request) => request.path === "masters")?.body).toEqual({ target:
      { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" }, find_key: "fk-1", community_version_id: "v_extended", confirmed: true });
    await dialog.getByRole("button", { name: "Use Theatrical filters" }).click();
    const replace = dialog.getByRole("group", { name: "Replace community filters" });
    await expect(replace).toContainText("Replace Extended Edition with Theatrical?");
    await replace.getByRole("button", { name: "Cancel" }).click();
    await expect(replace).toHaveCount(0);
    await dialog.getByRole("button", { name: "Close" }).click();

    await expect(panel.getByRole("heading", { name: "Extended Edition" })).toBeVisible();
    await expect(panel.getByRole("heading", { name: "Align before applying" })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Approve alignment" })).toBeDisabled();
    await panel.getByRole("button", { name: "Preview alignment" }).click();
    await expect(panel.getByText("Offset 0.000 s")).toBeVisible();
    await expect(panel.getByRole("list", { name: "Aligned filter times" }).getByRole("listitem")).toHaveCount(3);
    await expectKeyboardFocusVisible(page, panel.getByRole("button", { name: "Play clip at 20:00.000" }));
    await checkLayout(page, "alignment", width);
    await panel.getByRole("button", { name: "Approve alignment" }).click();
    await expect(panel.getByRole("heading", { name: "Align before applying" })).toHaveCount(0);
    expect(community.requests.find((request) => request.path.endsWith("alignment/approve"))?.body)
      .toEqual({ token: "align-1", for_revision: 12, confirmed: true });

    await panel.getByRole("button", { name: "Show filters" }).click();
    await expect(panel.getByText("2 filters shown · 1 hidden by your filter settings")).toBeVisible();
    await expectTooltip(page, panel.getByRole("button", { name: "Locally verified" }), /Locally verified/);
    const thumbsUp = panel.getByRole("button", { name: /^Thumbs up for Violence at/ });
    await expect(thumbsUp).toHaveAttribute("aria-pressed", "true");
    const thumbsDown = panel.getByRole("button", { name: /^Thumbs down for Violence at/ });
    await thumbsDown.click();
    await expect(thumbsDown).toHaveAttribute("aria-pressed", "true");
    await expect(thumbsUp).toHaveAttribute("aria-pressed", "false");
    expect(community.requests.find((request) => request.path.endsWith("/vote"))?.body).toMatchObject({ vote: "down", confirmed: true });
    await expectKeyboardFocusVisible(page, thumbsDown);

    const threshold = panel.getByRole("slider", { name: "Minimum confidence" });
    await threshold.fill("0.5");
    await expect(panel.getByText("1 filter shown · 2 hidden by your filter settings")).toBeVisible();
    await expect.poll(() => community.requests.filter((request) => request.path === "preferences").at(-1)?.body)
      .toEqual({ confidence_threshold: 0.5, show_low_confidence: false, hide_rejected_downvoted: false });
    await threshold.fill("0");
    const lowConfidence = panel.getByRole("checkbox", { name: "Show Low Confidence filters" });
    await expect(lowConfidence).not.toBeChecked();
    await lowConfidence.check();
    await expect(panel.getByText("Low Confidence · 20%")).toBeVisible();
    await expect(panel.getByText("3 filters shown")).toBeVisible();
    await expect.poll(() => community.requests.filter((request) => request.path === "preferences").at(-1)?.body)
      .toEqual({ confidence_threshold: 0, show_low_confidence: true, hide_rejected_downvoted: false });
    await checkLayout(page, "master-view", width);
    const hide = panel.getByRole("checkbox", { name: "Hide downvoted filters" });
    await expect(hide).not.toBeChecked();
    await hide.check();
    await expect(panel.getByText("2 filters shown · 1 hidden by your filter settings")).toBeVisible();
    await expect(thumbsDown).toHaveCount(0);
    await expect.poll(() => community.requests.filter((request) => request.path === "preferences").at(-1)?.body)
      .toEqual({ confidence_threshold: 0, show_low_confidence: true, hide_rejected_downvoted: true });
    await checkLayout(page, "hide-toggle", width);
  });

  test(`${width}px the Community tab is read-only and the update check downloads newer filters`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockItemPage(page);
    const community = await mockCommunityMasters(page, { linked: true });
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Community");
    const panel = page.getByRole("region", { name: "Community filters", exact: true });
    await panel.getByRole("button", { name: "Show filters" }).click();
    await expect(panel.getByText("Revision 12")).toBeVisible();
    for (const name of [/^Confirm/, /^Reject/, /^Reset/]) await expect(panel.getByRole("button", { name })).toHaveCount(0);
    await panel.getByRole("button", { name: "Check for filter updates" }).click();
    await expect(panel.getByText(/Downloaded the latest community filters/)).toBeVisible();
    await expect(panel.getByText("Revision 13")).toBeVisible();
    await expect(panel.getByText(/Community filters updated/)).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Review" })).toHaveCount(0);
    expect(community.requests.some((request) => request.path.includes("/update"))).toBe(false);
    await panel.getByRole("button", { name: "Check for filter updates" }).click();
    await expect(panel.getByText("These community filters are up to date.")).toBeVisible();
    await checkLayout(page, "update-check", width);
  });

  test(`${width}px notification entry links to the updated version`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockItemPage(page);
    const community = await mockCommunityMasters(page, { linked: true });
    await page.goto("/#/library/movies");
    await expect(page.getByRole("heading", { level: 1, name: "Movies" })).toBeVisible();
    if (width < 800) await page.getByRole("button", { name: "Open navigation" }).click();
    const entry = page.getByRole("button", { name: /^Filter updates/ });
    await expect(entry.getByLabel("1 unread")).toBeVisible();
    await expectKeyboardFocusVisible(page, entry);
    await entry.click();
    const notification = page.getByRole("link", { name: /Example Movie\s*filters updated/ });
    await expect(notification).toHaveAttribute("href", "#/library/movies/item/movie-1/source/source-2/community");
    await checkLayout(page, "notifications", width);
    await notification.click();
    await expect(page).toHaveURL(/#\/?library\/movies\/item\/movie-1\/source\/source-2\/community$/);
    await expect(page.getByRole("tab", { name: "Community", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("region", { name: "Community filters", exact: true })).toBeVisible();
    await expect.poll(() => community.requests.find((request) => request.path === "sync/notifications/read")?.body)
      .toEqual({ notification_ids: ["n1"] });
  });

  test(`${width}px sharing version picker and approximate estimates`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockItemPage(page, [completedScan]);
    const community = await mockCommunityMasters(page);
    await page.goto("/#/library/movies/item/movie-1");
    await chooseWorkflowStep(page, "Scans");
    await page.getByRole("button", { name: /^Share Audio test scan/ }).click();
    const dialog = page.getByRole("dialog", { name: "Share scan" });
    const picker = dialog.getByRole("combobox", { name: "Which version is this?" });
    await expect(picker.getByRole("option")).toHaveText(["Choose a version", "Theatrical", "Director's Cut", "Other"]);
    await expect(dialog.getByRole("region", { name: "Filter summary" })).toHaveCount(0);
    expect(community.requests.some((request) => request.path.endsWith("/preview"))).toBe(false);
    await picker.selectOption({ label: "Other" });
    const versionName = dialog.getByLabel("Version name (optional)");
    await expect(versionName).toHaveValue("");
    await versionName.fill("IMAX");
    await expect(dialog.getByText("≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute")).toBeVisible();
    await expect.poll(() => community.requests.filter((request) => request.path.endsWith("/preview")).at(-1)?.body)
      .toMatchObject({ version_choice: { choice: "other", text: "IMAX" } });
    await expectKeyboardFocusVisible(page, versionName);
    await checkLayout(page, "sharing", width);
  });
}
