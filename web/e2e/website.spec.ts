import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("landing page navigation, responsive layout, and accessibility", async ({ page }, info) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Editarr — Your media. Your edit.");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Your media. Your edit.");
  if (info.project.name === "mobile") {
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "true");
  }
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Features" }).click();
  await expect(page).toHaveURL(/#features$/);
  if (info.project.name === "mobile") await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  const contribution = page.locator("#contributing");
  await expect(contribution.getByRole("heading", { name: "Contribute on GitHub" })).toBeVisible();
  await expect(contribution.getByRole("link", { name: "Explore the repository" })).toHaveAttribute("href", "https://github.com/Editarr-EDL/Editarr");
  for (const install of await page.getByRole("link", { name: /Install/ }).all()) {
    await expect(install).toHaveAttribute("href", "/docs/#install");
  }
  expect(await page.locator("body").evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg").trim())).toBe("#111318");
  await page.goto("/");
  await page.screenshot({ path: `/tmp/editarr-site-${info.project.name}.png`, fullPage: true });
});

test("documentation search exposes request and response schemas", async ({ page }, info) => {
  await page.goto("/docs/");
  await expect(page.locator("#api-count")).toContainText("endpoints");
  await page.getByLabel("Find an endpoint").fill("api/auth/api-key");
  const operations = page.locator(".api-operation:visible");
  await expect(operations.filter({ hasText: /POST\s*\/api\/auth\/api-key\/reveal$/ })).toBeVisible();
  await operations.filter({ hasText: /POST\s*\/api\/auth\/api-key$/ }).locator("summary").click();
  const schemas = page.locator(".api-operation[open] pre");
  await expect(schemas).toContainText('"FreshKeyRequest"');
  await expect(schemas).toContainText('"ApiKeySecret"');
  await page.getByLabel("Find an endpoint").fill("not-an-endpoint");
  await expect(page.locator("#api-count")).toContainText("0 of");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  await page.goto("/docs/");
  await page.screenshot({ path: `/tmp/editarr-docs-${info.project.name}.png`, fullPage: false });
});
