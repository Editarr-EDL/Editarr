import assert from "node:assert/strict";
import { accountMenuButton } from "./account-menu.mjs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { fillSignIn } from "./run-jellyfin.mjs";

/**
 * Browser helpers for the account-link and account-deletion journeys: isolated contexts,
 * sign-in, same-session API calls, and the MU-39 link dialog driven through its prove and
 * confirm requests. `prefix` names this journey's evidence files.
 */
export function linkUI(run, prefix = "link-ui") {
  const require = createRequire(path.join(run.root, "web/package.json"));
  const { expect } = require("@playwright/test");
  const AxeBuilder = require("@axe-core/playwright").default;
  const origin = run.manifest.origin;
  const contexts = [], errors = [], requests = [], axe = [], steps = [], screenshots = [], keyboard = [];

  async function open(width) {
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push(context);
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => errors.push(error.message));
    page.on("response", response => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/")) requests.push({ method: response.request().method(), path: url.pathname, status: response.status() });
    });
    return { context, page };
  }

  async function signIn({ page, context }, username, password, route = "profile") {
    await page.goto(`${origin}/#/settings/${route}`);
    await fillSignIn(page, username, password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(accountMenuButton(page)).toBeVisible();
    for (const cookie of await context.cookies()) run.secrets.add(cookie.value);
  }

  /** Call the API inside a browser context's own session, with its CSRF token. */
  async function api(context, method, pathname, data, expected) {
    const status = await (await context.request.get(`${origin}/api/auth/status`)).json();
    if (status.csrf_token) run.secrets.add(status.csrf_token);
    const response = await context.request.fetch(origin + pathname, { method, data, headers: { Origin: origin, "X-CSRF-Token": status.csrf_token } });
    requests.push({ method, path: pathname, status: response.status() });
    const body = await response.json().catch(() => null);
    assert.equal(response.status(), expected, `${method} ${pathname}: ${JSON.stringify(body)}`);
    return body;
  }

  async function answer(page, suffix, action) {
    const pending = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith(suffix));
    await action();
    const response = await pending;
    const body = await response.json();
    if (body?.proof) run.secrets.add(body.proof);
    return { status: response.status(), body };
  }

  /** Fill the proof form, confirm with the actor's Jellyfin password, and return the proof response. */
  async function prove(page, dialog, { username, password, actorPassword }) {
    await dialog.getByLabel("Jellyfin username", { exact: true }).fill(username);
    await dialog.getByLabel("Jellyfin password", { exact: true }).fill(password);
    await dialog.getByRole("button", { name: "Review impact", exact: true }).click();
    const fresh = page.getByRole("dialog", { name: "Confirm your Jellyfin password", exact: true });
    await checkTrap(page, fresh);
    await fresh.getByLabel("Jellyfin password", { exact: true }).fill(actorPassword);
    const result = await answer(page, "/link/proof", () => fresh.getByRole("button", { name: "Prove identities", exact: true }).click());
    await expect(fresh).toHaveCount(0);
    assert.deepEqual(await page.locator("input[type=password]").evaluateAll(inputs => inputs.map(input => input.value).filter(Boolean)), [], "no password stays in the page after the proof request");
    return result;
  }

  async function confirm(page, dialog, actorPassword, kind = "link") {
    const title = kind === "link" ? "Confirm link" : "Confirm relink";
    await dialog.getByRole("button", { name: title, exact: true }).click();
    const fresh = page.getByRole("dialog", { name: title, exact: true });
    await checkTrap(page, fresh);
    await fresh.getByLabel("Jellyfin password", { exact: true }).fill(actorPassword);
    const result = await answer(page, "/link", () => fresh.getByRole("button", { name: kind === "link" ? "Link account" : "Relink account", exact: true }).click());
    await expect(fresh).toHaveCount(0);
    return result;
  }

  /** Screenshot (inputs masked), ARIA snapshot, overflow check, and axe for one state. */
  async function evidence(page, name) {
    await capture(page, run, `${prefix}-${name}`);
    screenshots.push(`${prefix}-${name}.png`);
    const report = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    axe.push({ name, violations: report.violations.map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map(node => node.target) })) });
    run.write(`${prefix}-axe.json`, axe);
    assert.equal(report.violations.length, 0, `axe ${name}`);
  }

  /** Tab and Shift+Tab visit every control of the topmost `dialog` once per cycle, in order, and never leave it. */
  async function checkTrap(page, dialog) {
    await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
    const position = () => dialog.evaluate(element => {
      const controls = [...element.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
      return { index: controls.indexOf(document.activeElement), count: controls.length };
    });
    const visits = [];
    for (const [key, step] of [["Tab", 1], ["Shift+Tab", -1]]) {
      let { index, count } = await position();
      for (let press = 0; press < count * 2; press++) {
        await page.keyboard.press(key);
        const next = await position();
        assert.equal(next.index, (index + step + count) % count, `${key} moves to the adjacent control of the topmost dialog`);
        index = next.index;
        visits.push(index);
      }
    }
    keyboard.push({ dialog: await dialog.getAttribute("aria-labelledby"), open: await page.getByRole("dialog").count(), visits });
    run.write(`${prefix}-keyboard.json`, keyboard);
  }

  async function noUnlinkOrMerge(page) {
    assert.equal(await page.getByRole("button", { name: /unlink|merge/i }).count(), 0, "no unlink or merge action");
  }

  const record = name => { steps.push(name); run.write(`${prefix}-steps.json`, steps); console.log(`PASS ${name}`); };

  async function close() {
    for (const context of contexts) {
      for (const cookie of await context.cookies().catch(() => [])) run.secrets.add(cookie.value);
      await context.close().catch(() => {});
    }
    run.write(`${prefix}-http-statuses.json`, requests);
    run.write(`${prefix}-browser-errors.json`, errors);
  }

  return { expect, origin, open, signIn, api, prove, confirm, evidence, checkTrap, noUnlinkOrMerge, record, close, errors, steps, screenshots, requests };
}
