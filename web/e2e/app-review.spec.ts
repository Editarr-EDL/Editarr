import { chooseExportFormat, chooseWorkflowStep } from "./workflow-actions";
import { expect, test } from "@playwright/test";
import { MockReviewFilter, expectNoAccessibilityViolations, mockApi } from "./app-fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  await page.goto("/#/settings");
});

test("keeps saved reviews isolated, versioned, exportable, and importable", async ({ page }) => {
  test.setTimeout(60_000);
  await page.getByRole("button", { name: "Movies" }).click();
  await page.getByRole("button", { name: "Open Example Movie" }).click();
  const panel = page.locator(".saved-review-panel");
  const expandReviewFilterGroups = async () => {
    await panel.getByLabel("Sort", { exact: true }).selectOption("category");
    const subcategories = panel.locator(".hierarchy-subcategory button");
    await expect(subcategories.first()).toBeVisible();
    for (let index = 0; index < await subcategories.count(); index += 1) {
      const subcategory = subcategories.nth(index);
      if (await subcategory.getAttribute("aria-expanded") === "false") await subcategory.click();
    }
    const groups = panel.locator(".hierarchy-group button");
    await expect(groups.first()).toBeVisible();
    for (let index = 0; index < await groups.count(); index += 1) {
      const group = groups.nth(index);
      if (await group.getAttribute("aria-expanded") === "false") await group.click();
    }
  };
  const openFirstReviewFilterEditor = async () => {
    if (await panel.getByLabel(/Filter .* label/).count()) return;
    await expandReviewFilterGroups();
    const editButton = panel.getByRole("button", { name: /^Edit filter / }).first();
    if (await editButton.getAttribute("aria-expanded") !== "true") await editButton.click();
  };
  const version = page.getByLabel("Media version");
  await expect(version).toHaveValue("source-1");
  await expect(page.getByLabel(/Include scan .* in review/)).toHaveCount(0);

  await version.selectOption("source-2");
  await expect(panel.getByRole("table", { name: "Saved reviews" })).toBeVisible();
  await expect(panel.getByRole("button", { name: /^Open review / })).toHaveCount(0);
  await version.selectOption("source-1");

  await panel.getByRole("button", { name: "New Review" }).click();
  let dialog = page.getByRole("dialog", { name: "New Review" });
  await dialog.getByLabel("New review name").fill("Review A");
  await dialog.getByRole("button", { name: "Start new review" }).click();
  await panel.getByLabel("Scan #2").check();
  await openFirstReviewFilterEditor();
  await expect(panel.getByLabel(/Filter .* label/)).toHaveValue("Visual content");
  await expect(panel.getByRole("heading", { name: "Review A", level: 3 })).toBeVisible();
  const [membershipSave] = await Promise.all([
    page.waitForResponse((response) => response.url().includes("/api/library-items/movie-1/reviews") && response.request().method() === "POST"),
    panel.getByRole("button", { name: "Save Review" }).click(),
  ]);
  const membershipPayload = membershipSave.request().postDataJSON() as { filters: MockReviewFilter[] };
  expect(membershipPayload.filters).toEqual([expect.objectContaining({
    source_scan_id: "scan-history", source_finding_id: 10, label: "Visual content",
  })]);
  await panel.getByLabel(/Filter .* label/).fill("A isolated");
  await chooseWorkflowStep(page, "Scans");
  await expect(page.getByRole("button", { name: "Import", exact: true })).toBeDisabled();
  await chooseWorkflowStep(page, "Review");
  page.once("dialog", async (confirmation) => {
    expect(confirmation.message()).toContain("switch versions");
    await confirmation.dismiss();
  });
  await version.selectOption("source-2");
  await expect(version).toHaveValue("source-1");
  page.once("dialog", async (confirmation) => {
    expect(confirmation.message()).toContain("leave this page");
    await confirmation.dismiss();
  });
  await page.getByRole("button", { name: "General", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Example Movie" })).toBeVisible();
  page.once("dialog", async (confirmation) => {
    expect(confirmation.message()).toContain("leave this page");
    await confirmation.dismiss();
  });
  await page.evaluate(() => { window.location.hash = "#/settings/general"; });
  await expect(page.getByRole("heading", { name: "Example Movie" })).toBeVisible();
  await Promise.all([
    page.waitForResponse((response) => response.url().includes("/api/reviews/") && response.request().method() === "PUT"),
    panel.getByRole("button", { name: "Save Changes" }).click(),
  ]);

  await panel.getByRole("button", { name: "New Review" }).click();
  dialog = page.getByRole("dialog", { name: "New Review" });
  await dialog.getByLabel("New review name").fill("Review B");
  await dialog.getByRole("button", { name: "Start new review" }).click();
  await panel.getByRole("button", { name: "Add Manual Filter" }).click();
  dialog = page.getByRole("dialog", { name: "Add Manual Filter" });
  await dialog.getByLabel("Manual filter label").fill("B manual");
  await dialog.getByRole("button", { name: "Add Filter" }).click();
  await panel.getByRole("button", { name: "Save Review" }).click();
  const reviewA = panel.getByRole("row").filter({ hasText: "Review A" });
  const reviewB = panel.getByRole("row").filter({ hasText: "Review B" });
  await reviewA.getByRole("button", { name: "Open review Review A" }).click();
  await expect(panel.getByRole("heading", { name: "Review A", level: 3 })).toBeVisible();
  await openFirstReviewFilterEditor();
  await expect(panel.getByLabel(/Filter .* label/)).toHaveValue("A isolated");
  await reviewB.getByRole("button", { name: "Open review Review B" }).click();
  await expect(panel.getByRole("heading", { name: "Review B", level: 3 })).toBeVisible();
  await openFirstReviewFilterEditor();
  await expect(panel.getByLabel(/Filter .* label/)).toHaveValue("B manual");

  await panel.getByRole("button", { name: "Rename review" }).click();
  await panel.getByLabel("Review name", { exact: true }).fill("Review B renamed");
  await panel.locator(".saved-review-name-editor").getByRole("button", { name: "Save" }).click();
  await expect(panel.getByRole("row").filter({ hasText: "Review B renamed" })).toBeVisible();
  await panel.getByRole("button", { name: "Duplicate" }).click();
  dialog = page.getByRole("dialog", { name: "Duplicate Review" });
  await dialog.getByLabel("Duplicate review name").fill("Review B copy");
  await dialog.getByRole("button", { name: "Duplicate" }).click();
  await expect(panel.getByRole("heading", { name: "Review B copy", level: 3 })).toBeVisible();
  await panel.getByRole("button", { name: "Delete review" }).click();
  await page.getByRole("dialog", { name: "Delete review" }).getByRole("button", { name: "Delete review" }).click();
  await expect(panel.getByRole("row").filter({ hasText: "Review B copy" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Open review Review B renamed" }).click();
  await panel.getByLabel("Scan #2").check();
  await panel.getByRole("button", { name: "Save Changes" }).click();

  await (await chooseExportFormat(page, "EDL")).click();
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Publish EDL" }).click();
  await expect(page.getByRole("dialog", { name: "Export" }).getByRole("status")).toContainText("Published EDL");
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Close" }).click();
  await chooseWorkflowStep(page, "Review");
  await expect(reviewB).toContainText("EDL · Current");
  await reviewA.getByRole("button", { name: "Open review Review A" }).click();
  await (await chooseExportFormat(page, "EDL")).click();
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Publish EDL" }).click();
  await expect(page.getByRole("dialog", { name: "Export" }).getByRole("status")).toContainText("Published EDL");
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Close" }).click();
  await chooseWorkflowStep(page, "Review");
  await expect(reviewA).toContainText("EDL · Current");
  await expect(reviewB).not.toContainText("EDL · Current");

  await chooseWorkflowStep(page, "Scans");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Import" });
  await expect(dialog.getByRole("button", { name: "EDL", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Import EDL" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "EDL", exact: true })).toBeFocused();
  await dialog.getByLabel("Import review name").fill("Imported review");
  await dialog.getByLabel("EDL file").setInputFiles({ name: "mixed.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") });
  await dialog.getByRole("button", { name: "Import EDL" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Unsupported EDL actions were ignored and not imported" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Imported review", level: 3 })).toBeVisible();
  await openFirstReviewFilterEditor();
  await expect(panel.getByLabel(/Filter .* label/)).toHaveValue("Imported mute");
  await panel.getByLabel(/Filter .* family/).selectOption("language");
  await expect(panel.getByLabel(/Filter .* subcategory/)).toHaveCount(0);
  await openFirstReviewFilterEditor();
  await panel.getByLabel(/Filter .* subcategory/).fill("swearing");
  await expect(panel.getByLabel(/Filter .* description/)).toHaveCount(0);
  await openFirstReviewFilterEditor();
  await panel.getByLabel(/Filter .* description/).fill("Imported details");
  await panel.getByLabel(/Filter .* start seconds/).fill("1.25");
  await panel.getByLabel(/Filter .* end seconds/).fill("2.75");
  await panel.getByLabel(/Filter .* action/).selectOption("skip");
  await panel.getByLabel(/Filter .* label/).fill("Imported edited");
  await panel.getByRole("button", { name: "Add Manual Filter" }).click();
  dialog = page.getByRole("dialog", { name: "Add Manual Filter" });
  await dialog.getByLabel("Manual filter label").fill("Review-local mute");
  await dialog.getByRole("button", { name: "Add Filter" }).click();
  await expect(panel.getByRole("status")).toContainText("1 Manual filter staged");
  const [importedSave] = await Promise.all([
    page.waitForResponse((response) => response.url().includes("/api/reviews/") && response.request().method() === "PUT"),
    panel.getByRole("button", { name: "Save Changes" }).click(),
  ]);
  expect(importedSave.request().postDataJSON().filters[0]).toEqual(expect.objectContaining({
    category: "language", subcategory: "swearing", label: "Imported edited", details: "Imported details",
    start_seconds: 1.25, end_seconds: 2.75, edit_action: "skip",
  }));
  await expandReviewFilterGroups();
  await expect(panel.getByRole("button", { name: /^Edit filter / })).toHaveCount(2);
  await (await chooseExportFormat(page, "EDL")).click();
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Publish EDL" }).click();
  await expect(page.getByRole("dialog", { name: "Export" }).getByRole("status")).toContainText("Published EDL");
  await page.getByRole("dialog", { name: "Export" }).getByRole("button", { name: "Close" }).click();
  await chooseWorkflowStep(page, "Review");
  await expect(panel.getByRole("row").filter({ hasText: "Imported review" })).toContainText("EDL · Current");

  const reviewCount = await panel.getByRole("button", { name: /^Open review / }).count();
  await chooseWorkflowStep(page, "Scans");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Import" });
  await dialog.getByLabel("Import review name").fill("Invalid import");
  await dialog.getByLabel("EDL file").setInputFiles({ name: "invalid.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") });
  await dialog.getByRole("button", { name: "Import EDL" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Invalid EDL (line 1)");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await chooseWorkflowStep(page, "Review");
  await expect(panel.getByRole("button", { name: /^Open review / })).toHaveCount(reviewCount);
  await expectNoAccessibilityViolations(page);
});

for (const width of [320, 1440]) {
  test(`names the Jellyfin item an import attaches to and refuses a file without one at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/#/library/movies/item/movie-1");
    const panel = page.locator(".saved-review-panel");
    await expect(panel.getByRole("heading", { name: "Saved reviews" })).toBeVisible();
    const reviewCount = await panel.getByRole("button", { name: /^Open review / }).count();
    await chooseWorkflowStep(page, "Scans");
    await page.getByRole("button", { name: "Import", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Import" });
    await expect(dialog.getByText(/^Imports into Jellyfin item Example Movie, version /)).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Scanner path" })).toHaveCount(0);
    await dialog.getByLabel("Import review name").fill("Orphan import");
    await dialog.getByLabel("EDL file").setInputFiles({ name: "orphan.edl", mimeType: "text/plain", buffer: Buffer.from("1 2 1\n") });
    await dialog.getByRole("button", { name: "Import EDL" }).click();
    await expect(dialog.getByRole("alert")).toContainText("This file must belong to a Jellyfin item. Open its Jellyfin item and import the file there.");
    await expect(panel.getByRole("button", { name: /^Open review / })).toHaveCount(reviewCount);
    await expectNoAccessibilityViolations(page);
  });
}

