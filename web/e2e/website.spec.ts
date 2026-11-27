import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function expectNoOverflowOrViolations(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
}

test("landing page navigation, responsive layout, and accessibility", async ({ page }, info) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Editarr — Your media. Your edit.");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Your media. Your edit.");
  const hero = page.locator("#overview img");
  await expect(hero).toHaveAttribute("fetchpriority", "high");
  await expect(page.locator("#workflow li")).toHaveText([/Community/, /Scans/, /Review/, /Subtitles/, /Publish/]);
  const tour = page.locator("#features .tour-row");
  expect(await tour.count()).toBe(8);
  for (const shot of await page.locator("#features a.shot").all()) {
    const href = await shot.getAttribute("href");
    expect(href).toMatch(/^\/assets\/screens\/[a-z-]+\.png$/);
    await expect(shot.locator("img")).toHaveAttribute("src", href!);
    await expect(shot.locator("img")).toHaveAttribute("loading", "lazy");
    expect(await shot.locator("img").getAttribute("alt")).toBeTruthy();
  }
  await expect(page.locator("#features .chips, #more, [href*='models.png']")).toHaveCount(0);
  if (info.project.name === "mobile") {
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "true");
  }
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Features" }).click();
  await expect(page).toHaveURL(/#features$/);
  if (info.project.name === "mobile") await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  await expectNoOverflowOrViolations(page);
  const contribution = page.locator("#contributing");
  await expect(contribution.getByRole("heading", { name: "Contribute on GitHub" })).toBeVisible();
  await expect(contribution.getByRole("link", { name: "Explore the repository" })).toHaveAttribute("href", "https://github.com/Editarr-EDL/Editarr");
  for (const install of await page.getByRole("link", { name: /Install/ }).all()) {
    await expect(install).toHaveAttribute("href", "/docs/#install");
  }
  expect(await page.locator("body").evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim())).toBe("#111318");
  await page.goto("/");
  await page.evaluate(async () => {
    for (const image of Array.from(document.images)) image.loading = "eager";
    await Promise.all(Array.from(document.images).map(image => image.decode().catch(() => undefined)));
  });
  await page.screenshot({ path: `/tmp/editarr-site-${info.project.name}.png`, fullPage: true });
});

test("screenshots open in an accessible in-page viewer", async ({ page }, info) => {
  await page.goto("/");
  const shot = page.locator("#features a.shot").first();
  const href = await shot.getAttribute("href");
  await shot.scrollIntoViewIfNeeded();
  await shot.click();
  const viewer = page.getByRole("dialog", { name: "Your Jellyfin library" });
  await expect(viewer).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  const image = viewer.getByRole("img");
  await expect(image).toHaveAttribute("src", href!);
  await expect(image).toHaveAttribute("alt", (await shot.locator("img").getAttribute("alt"))!);
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
  const box = (await image.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.width).toBeLessThanOrEqual(viewport.width * 0.95 + 1);
  expect(box.height).toBeLessThanOrEqual(viewport.height * 0.9 + 1);
  const close = viewer.getByRole("button", { name: "Close screenshot" });
  await expect(close).toBeFocused();
  expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).toBe("hidden");
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished)));
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `/tmp/editarr-lightbox-${info.project.name}.png` });
  await close.click();
  await expect(viewer).toBeHidden();
  await expect(shot).toBeFocused();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe("hidden");

  const publish = page.locator("#features a.shot").nth(5);
  await publish.click();
  await expect(page.getByRole("dialog", { name: "Publish for every viewer" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(publish).toBeFocused();
  await expect(page.locator("#features").getByRole("link", { name: "Jellyfin EDL plugin", exact: true })).toHaveAttribute("href", "https://github.com/Editarr-EDL/jellyfin-edl");
});

test("documentation handbook navigation and API reference link", async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/docs/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Make it your own.");
  await expect(page.locator('#navigation a[aria-current="page"]')).toHaveText("Docs");
  const handbook = page.getByRole("navigation", { name: "Handbook sections" });
  for (const anchor of ["get-started", "workflow", "subtitles", "accounts", "api", "hosting"]) {
    await handbook.locator(`a[href="#${anchor}"]`).click();
    await expect(page).toHaveURL(new RegExp(`#${anchor}$`));
    await expect(page.locator(`#${anchor} h2`)).toBeInViewport();
  }
  await expect(page.locator("#install")).toHaveCount(1);
  await expect(page.locator("#api").getByRole("link", { name: "API reference" })).toHaveAttribute("href", "/api/");
  await expect(page.locator('script[src="/assets/api-reference.js"]')).toHaveCount(0);
  await expectNoOverflowOrViolations(page);
  await page.goto("/docs/");
  await page.screenshot({ path: `/tmp/editarr-docs-${info.project.name}.png`, fullPage: false });
});
