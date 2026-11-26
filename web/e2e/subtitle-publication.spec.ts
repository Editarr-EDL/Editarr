import { expect, test, type Page } from "@playwright/test";
import {
  expectNoHorizontalPageOverflow, json, mockApi, mockReview,
} from "./app-fixtures";
import { chooseWorkflowStep } from "./workflow-actions";
import type { PublishedSubtitle, SubtitleSource } from "../src/api/api-review-subtitles";

const review = { ...mockReview("family-review", "Family Review", "source-1"), owner_id: 1, policy_owner_id: 1, policy_revision: 0 };
const track: SubtitleSource = {
  id: "english-forced", label: "English for signs", format: "srt", kind: "external",
  language: "en", supported: true,
  recommended: true, forced: true, sdh: false,
};
const title = "ryan - Family Review";
const filename = `example-4k.${title}.en.forced.srt`;
const previewToken = "a".repeat(64);

async function openSubtitleWorkflow(page: Page, publicationBlockReason?: string) {
  const publications: PublishedSubtitle[] = [
    { filename: "shared.srt", label: "Original shared copy", format: "srt", forced: false },
    { filename: "other.srt", label: "alex - Family Review", format: "srt", forced: true, owned_by_viewer: false },
  ];
  await mockApi(page);
  await page.route("**/api/library-items/movie-1/reviews**", (route) => json(route, [review]));
  await page.route(`**/api/reviews/${review.id}`, (route) => json(route, review));
  await page.route("**/api/media/subtitles/publications?**", (route) => json(route, { published_subtitles: publications }));
  await page.route(`**/api/reviews/${review.id}/subtitles/sources`, (route) => json(route, {
    sources: [track], audio_sources: [], audio_source_id: null, fallback: null, published_subtitles: publications,
    track_title: publicationBlockReason ? null : title, publication_block_reason: publicationBlockReason ?? null,
  }));
  await page.route(`**/api/reviews/${review.id}/subtitles/preview`, (route) => json(route, {
    source: track, cues: [{ start_seconds: 2, end_seconds: 3, original: "Original cue", filtered: "Updated cue" }],
    changed_cues: 1, unchanged_cues: 3,
    preview_token: publicationBlockReason ? null : previewToken,
    track_title: publicationBlockReason ? null : title,
    filename: publicationBlockReason ? null : filename,
    publication_block_reason: publicationBlockReason ?? null,
  }));
  await page.route(`**/api/reviews/${review.id}/subtitles/publish`, (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ source_id: track.id, preview_token: previewToken });
    publications.push({ filename, label: title, format: "srt", forced: true, owned_by_viewer: true });
    return json(route, { source: track, filename, replaced: false });
  });
  await page.goto("/#/library/movies/item/movie-1");
  await chooseWorkflowStep(page, "Subtitles");
}

for (const width of [320, 1440]) {
  test(`shows the exact named sidecar with shared forced playback at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    await openSubtitleWorkflow(page);
    const cuePreview = page.getByRole("region", { name: "Cue preview" });
    await expect(cuePreview.getByText(title, { exact: true })).toBeVisible();
    await expect(cuePreview.getByText(/automatically select this forced track for other viewers/)).toBeVisible();
    await expectNoHorizontalPageOverflow(page);

    await cuePreview.getByRole("button", { name: "Preview cue changes" }).click();
    await expect(cuePreview.getByText("Updated cue")).toBeVisible();
    await cuePreview.getByRole("button", { name: "Continue to Publish" }).click();

    const publish = page.getByRole("region", { name: "Publish to Jellyfin" });
    await expect(publish.getByText(title, { exact: true })).toBeVisible();
    await expect(publish.getByText(/everyone who can play this version/i)).toBeVisible();
    await expect(publish.getByText(/automatically select this forced track for other viewers/)).toBeVisible();
    await publish.getByRole("button", { name: "Publish filtered subtitles" }).click();
    await expect(publish.getByRole("status")).toContainText("Saved the filtered subtitle track");
    const published = page.getByRole("list", { name: "Published subtitle files" });
    await expect(published.getByRole("listitem").filter({ hasText: title })).toContainText("Forced · Your track · Shared");
    await expect(published.getByRole("listitem").filter({ hasText: "alex - Family Review" })).not.toContainText("Your track");
    await expect(published.getByRole("listitem").filter({ hasText: "Original shared copy" })).toContainText("Shared");
    await expectNoHorizontalPageOverflow(page);
  });

  test(`keeps cue preview available when a naming issue blocks publishing at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 });
    const message = "Rename this saved review to remove the word Forced from its Jellyfin track title.";
    await openSubtitleWorkflow(page, message);
    const cuePreview = page.getByRole("region", { name: "Cue preview" });
    await expect(cuePreview.getByRole("alert")).toHaveText(message);
    await cuePreview.getByRole("button", { name: "Preview cue changes" }).click();
    await expect(cuePreview.getByText("Updated cue")).toBeVisible();
    await expect(cuePreview.getByRole("alert")).toHaveText(message);
    await expect(cuePreview.getByRole("button", { name: "Continue to Publish" })).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);

    await chooseWorkflowStep(page, "Publish");
    const publish = page.getByRole("region", { name: "Publish to Jellyfin" });
    await expect(publish.getByRole("alert")).toHaveText(message);
    await expect(publish.getByRole("button", { name: "Publish filtered subtitles" })).toHaveCount(0);
  });
}
