import { expect, test } from "@playwright/test";
import { mockApi } from "./app-fixtures";

test.skip(!process.env.EDITARR_CAPTURE_WEBSITE, "Opt-in website asset capture");

// Run explicitly to refresh the public, fixture-only product screenshot.
test("capture Editarr for the website with example media", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  await mockApi(page);
  await page.goto("/#/library/movies/item/movie-1");
  await expect(page.getByRole("heading", { name: "Example Movie", exact: true })).toBeVisible();
  const panel = page.locator(".saved-review-panel");
  await panel.getByRole("button", { name: "New Review", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New Review", exact: true });
  await dialog.getByLabel("New review name").fill("Movie night");
  await dialog.getByRole("button", { name: "Start new review" }).click();
  await panel.getByRole("checkbox", { name: "Unnamed scan", exact: true }).check();
  await expect(panel.getByRole("heading", { name: "Movie night", exact: true })).toBeVisible();
  await expect(panel.locator(".hierarchy-subcategory button").first()).toBeVisible();
  await expect(panel.getByRole("button", { name: "Save Review", exact: true })).toBeEnabled();
  await panel.getByRole("button", { name: "Save Review", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Save Changes", exact: true })).toBeVisible();
  await panel.evaluate((element) => window.scrollTo(0, element.getBoundingClientRect().top + window.scrollY - 24));
  await page.screenshot({ path: "../website/public/assets/editarr-review.png" });
});
