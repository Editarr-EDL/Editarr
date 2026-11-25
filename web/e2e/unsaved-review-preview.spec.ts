import { chooseFilterAction, setReviewSource, workflowStep } from "./workflow-actions";
import { expect, test, type Page } from "@playwright/test";
import { authenticationStatus, json, mockApi } from "./app-fixtures";

const previewPath = "/api/library-items/movie-1/reviews/preview";

/** Open one evidence filter on a new, unsaved review. */
async function openDraftFilter(page: Page) {
  await page.route("**/api/scans/scan-history/findings", (route) => json(route, {
    revision: 0,
    findings: [{
      id: 10, scan_id: "scan-history", source: "visual", category: "nudity",
      subcategory: "nudity", severity: "medium", mcf_channel: "video", mcf_comment: null,
      label: "Visual content", details: null, group_key: null, start_seconds: 10,
      end_seconds: 18, confidence: 0.9, edit_action: "skip", selected: true,
    }],
  }));
  await page.goto("/#/library/movies/item/movie-1");
  const panel = page.locator(".saved-review-panel");
  await panel.getByRole("button", { name: "New Review" }).click();
  const dialog = page.getByRole("dialog", { name: "New Review" });
  await dialog.getByLabel("New review name").fill("Draft for preview");
  await dialog.getByRole("button", { name: "Start new review" }).click();
  await setReviewSource(page, "Unnamed scan");
  await workflowStep(page, "Review").click();
  await panel.getByLabel("Sort", { exact: true }).selectOption("category");
  const video = panel.getByRole("region", { name: "Video review filters", exact: true });
  await video.locator(".hierarchy-subcategory button").click();
  await video.locator(".hierarchy-group button").click();
  return { panel, video, preview: video.getByRole("button", { name: /^Preview / }) };
}

test("previews an unselected evidence filter with its unsaved timing", async ({ page }) => {
  await mockApi(page);
  const mutations: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (request.method() !== "GET" && pathname.includes("/reviews") && pathname !== previewPath) {
      mutations.push(pathname);
    }
  });
  await page.route(`**${previewPath}`, (route) => route.fulfill({
    contentType: "video/mp4",
    body: "mock video preview",
  }));
  const { panel, video, preview } = await openDraftFilter(page);
  await expect(preview).toBeEnabled();
  await video.getByRole("checkbox", { name: /^Include / }).uncheck();
  await chooseFilterAction(page, video, /^Edit /);
  await video.getByLabel(/^Filter .* start seconds$/).fill("11.5");
  await video.getByLabel(/^Filter .* end seconds$/).fill("16.5");
  const [request] = await Promise.all([
    page.waitForRequest((candidate) => new URL(candidate.url()).pathname === previewPath),
    preview.click(),
  ]);
  expect(request.method()).toBe("POST");
  expect(request.postDataJSON()).toMatchObject({
    media_source_id: "source-1",
    source_scan_id: "scan-history",
    source_finding_id: 10,
    start_seconds: 11.5,
    end_seconds: 16.5,
    source: "visual",
    edit_action: "skip",
  });
  await expect(video.locator("video")).toHaveAttribute("src", /^blob:/);
  await expect(panel.getByRole("button", { name: "Save Review" })).toBeEnabled();
  expect(mutations).toEqual([]);
});

test("requires preview permission for unsaved evidence filters", async ({ page }) => {
  await mockApi(page);
  const signedIn = authenticationStatus("authenticated");
  signedIn.access!.capabilities = signedIn.access!.capabilities.filter((capability) => capability !== "reviews.preview.own");
  await page.route("**/api/auth/status", (route) => json(route, signedIn));
  const { preview } = await openDraftFilter(page);
  await expect(preview).toBeDisabled();
});

test("previews manual audio timing before adding the filter or saving the review", async ({ page }) => {
  await mockApi(page);
  const failedMessage = "Unable to render the filter preview";
  await page.route(`**${previewPath}`, (route) => json(route, {
    detail: { code: "review_preview_failed", message: failedMessage },
  }, 422));
  const { panel } = await openDraftFilter(page);
  await panel.getByRole("button", { name: "Add Manual Filter" }).click();
  const dialog = page.getByRole("dialog", { name: "Add Manual Filter" });
  const preview = dialog.getByRole("button", { name: "Preview", exact: true });
  const addFilter = dialog.getByRole("button", { name: "Add Filter", exact: true });
  await dialog.getByLabel("Manual filter start seconds").fill("8");
  await expect(preview).toBeDisabled();
  await dialog.getByLabel("Manual filter end seconds").fill("9.5");
  await expect(addFilter).toBeDisabled();
  await preview.click();
  await expect(dialog.getByRole("alert")).toHaveText(failedMessage);

  await page.route(`**${previewPath}`, (route) => route.fulfill({
    contentType: "audio/mpeg",
    body: "mock audio preview",
  }));
  const [request] = await Promise.all([
    page.waitForRequest((candidate) => new URL(candidate.url()).pathname === previewPath),
    preview.click(),
  ]);
  expect(request.method()).toBe("POST");
  expect(request.postDataJSON()).toEqual({
    media_source_id: "source-1",
    start_seconds: 8,
    end_seconds: 9.5,
    source: "audio",
    edit_action: "mute",
  });
  await expect(dialog.locator("audio")).toHaveAttribute("src", /^blob:/);
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(panel.getByRole("button", { name: "Save Review" })).toBeEnabled();
  await expect(panel.getByText(/Manual .* staged/)).toHaveCount(0);
});
