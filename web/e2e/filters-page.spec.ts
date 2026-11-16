import { expect, test } from "@playwright/test";
import { expectNoHorizontalPageOverflow, mockApi } from "./app-fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

for (const width of [320, 1440]) {
  test(`saves My filters and the new-account mode from their tabs at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const writes: { path: string; body: unknown }[] = [];
    page.on("request", request => {
      if (request.method() === "PUT") writes.push({ path: new URL(request.url()).pathname, body: request.postDataJSON() });
    });
    await page.goto("/#/settings/filters");
    await expect(page.getByRole("tab", { name: "My filters" })).toHaveAttribute("aria-selected", "true");
    await page.getByRole("button", { name: "Swearing: Medium" }).click();
    await expect(page.getByRole("dialog", { name: "Swearing" })).toBeVisible();
    await expectNoHorizontalPageOverflow(page);
    await page.getByRole("radio", { name: "High" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Swearing: High, changed by you" })).toBeFocused();
    await expect(page.getByText("1 unsaved change")).toBeVisible();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Your filters are saved.")).toBeVisible();
    expect(writes.at(-1)).toEqual({ path: "/api/settings/filters/personal", body: expect.objectContaining({ mode: "live",
      severity_overrides: [{ subcategory: "swearing", severity: "high" }] }) });

    await page.getByRole("tab", { name: "Server defaults" }).click();
    await expect(page).toHaveURL(/#settings\/filters\/server$/);
    await expect(page.getByRole("heading", { name: "Default rules" })).toBeVisible();
    await page.getByText("Give them their own copy").click();
    await expect(page.getByText("New accounts will get their own copy.")).toBeVisible();
    expect(writes.at(-1)).toEqual({ path: "/api/settings/filters/default-mode", body: { mode: "copy" } });
    await expectNoHorizontalPageOverflow(page);
  });
}
