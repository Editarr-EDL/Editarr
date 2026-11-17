import { expect, test } from "@playwright/test";
import { mockApi, expectNoAccessibilityViolations, expectNoHorizontalPageOverflow } from "./app-fixtures";

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

test("opens narrow Settings navigation from the left and returns focus when dismissed", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await mockApi(page);
  await page.goto("/#/settings/connections");
  const toggle = page.getByRole("button", { name: "Open navigation" });
  const drawer = page.getByRole("dialog", { name: "Navigation menu" });
  const panel = page.locator(".sidebar-drawer-panel");
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  const close = drawer.getByRole("button", { name: "Close navigation" });
  const [logo, menuButton, profile] = await Promise.all([
    page.locator(".brand-mark").boundingBox(),
    toggle.boundingBox(),
    page.getByRole("button", { name: /^Account menu for / }).boundingBox(),
  ]);
  expect(menuButton!.x + menuButton!.width).toBeLessThan(logo!.x);
  expect(logo!.x + logo!.width).toBeLessThan(profile!.x);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(navigation).toBeHidden();
  await panel.evaluate((element) => {
    let slideCount = 0;
    element.addEventListener("transitionrun", (event) => {
      if ((event as TransitionEvent).propertyName === "transform") {
        element.setAttribute("data-slide-count", String(++slideCount));
      }
    });
  });

  await toggle.press("Enter");
  await expect(close).toBeFocused();
  await expect(navigation.getByRole("button", { name: "Connections", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(panel).toHaveAttribute("data-slide-count", "1");
  await expect.poll(async () => (await panel.boundingBox())?.x).toBe(0);
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(panel).toHaveAttribute("data-slide-count", "2");
  await expect(toggle).toBeFocused();

  await toggle.click();
  await close.click();
  await expect(drawer).toBeHidden();
  await toggle.click();
  await page.mouse.click(310, 220);
  await expect(drawer).toBeHidden();
  await toggle.click();
  await navigation.getByRole("button", { name: "Filters", exact: true }).click();
  await expect(drawer).toBeHidden();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("heading", { name: "Filters", exact: true, level: 1 })).toBeVisible();
});

test("returns to the desktop sidebar when resizing an open drawer", async ({ page }) => {
  await page.setViewportSize({ width: 760, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockApi(page);
  await page.goto("/#/settings/connections");
  const toggle = page.getByRole("button", { name: "Open navigation" });
  const drawer = page.getByRole("dialog", { name: "Navigation menu" });
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  await toggle.click();
  await expect(drawer).toBeVisible();
  await expect(drawer.locator(".sidebar-drawer-panel")).toHaveCSS("transition-duration", "0s");
  await page.setViewportSize({ width: 761, height: 900 });
  await expect(toggle).toBeHidden();
  await expect(drawer).toBeHidden();
  await expect(navigation).toBeVisible();
  await navigation.getByRole("button", { name: "Filters", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Filters", exact: true, level: 1 })).toBeVisible();

  await page.setViewportSize({ width: 760, height: 900 });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(navigation).toBeHidden();
});
