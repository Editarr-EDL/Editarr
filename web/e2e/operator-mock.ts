import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";
import { operatorFixture } from "../src/settings/community-operator/test-fixtures";

export const operatorCsp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'";

/** Serve the operator page with the production CSP and pin the clock to the fixture time. */
export async function strictOperatorPage(page: Page) {
  await page.clock.setFixedTime(new Date("2026-11-27T18:00:00Z"));
  await page.route("**/admin/", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "Content-Security-Policy": operatorCsp } });
  });
}
/**
 * Answer operator API reads from the synthetic fixtures; `handle` may answer first (return a value to send as JSON,
 * or a full fulfil options object with `status`), and returns undefined to fall through.
 */
export async function mockOperatorApi(page: Page, handle?: (path: string, method: string, body: unknown) => unknown) {
  await page.route("**/admin/v1/**", route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname + url.search;
    const custom = handle?.(path, request.method(), request.method() === "GET" ? null : request.postDataJSON());
    if (custom && typeof custom === "object" && "status" in custom) return route.fulfill(custom as { status: number; json: unknown });
    if (custom !== undefined) return route.fulfill({ json: custom });
    try { return route.fulfill({ json: operatorFixture(path) }); }
    catch { return route.fulfill({ status: 404, json: { error: { code: "resource_not_found" } } }); }
  });
}
export async function expectAccessible(page: Page) {
  const violations = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze().then(result => result.violations);
  expect(violations.map(violation => `${violation.id}: ${violation.nodes.slice(0, 3).map(node => node.target.join(" ")).join(", ")}`)).toEqual([]);
}
export async function expectNoOverflow(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
}
/** Collect CSP violations and inline style attributes written into the DOM. */
export async function watchCsp(page: Page) {
  const violations: string[] = [];
  page.on("console", message => { if (/Content Security Policy|Refused to apply inline style/i.test(message.text())) violations.push(message.text()); });
  return { violations, inlineStyles: () => page.evaluate(() => document.querySelectorAll("[style]").length) };
}
