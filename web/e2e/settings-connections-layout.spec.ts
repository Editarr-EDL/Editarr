import { expect, test } from "@playwright/test";
import { mockApi, expectNoAccessibilityViolations } from "./app-fixtures";

for (const width of [320, 760, 1440]) {
  test(`${width}px connection controls fit inside their cards`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.goto("/#/settings/connections");
    const card = page.getByRole("region", { name: "Jellyfin", exact: true });
    await expect(card.getByRole("button", { name: "Disconnect", exact: true })).toBeVisible();
    const bounds = await card.evaluate((element) => {
      const cardBounds = element.getBoundingClientRect();
      return Array.from(element.querySelectorAll("input, button, .configured-badge")).map((control) => {
        const rect = control.getBoundingClientRect();
        return { left: rect.left - cardBounds.left, right: rect.right - cardBounds.right };
      });
    });
    for (const bound of bounds) {
      expect(bound.left).toBeGreaterThanOrEqual(0);
      expect(bound.right).toBeLessThanOrEqual(0);
    }
    await card.getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Disconnect Jellyfin?" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/connections-layout-${width}.png`, fullPage: true });
  });
}
