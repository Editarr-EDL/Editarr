import assert from "node:assert/strict";
import { accountMenuButton, signOut } from "./account-menu.mjs";
import { randomBytes } from "node:crypto";
import { createRequire } from "node:module";
import path from "node:path";
import { capture } from "./capture.mjs";
import { SIGN_IN_PATH, addJellyfinUser, admitJellyfinUser, fillSignIn } from "./run-jellyfin.mjs";

const HIDDEN_FROM_USERS = ["Connections", "Models"];

/** Every heading, button, and link text the page has shown since the recorder last reset. */
function installRecorder() {
  window.__editarrSeen = new Set();
  const record = () => {
    for (const element of document.querySelectorAll("h1, h2, h3, button, a")) {
      const text = (element.textContent || "").trim();
      if (text) window.__editarrSeen.add(text);
    }
  };
  new MutationObserver(record).observe(document, { subtree: true, childList: true, characterData: true });
}

/**
 * Drive MU-19 through the real UI with Jellyfin users of the run's Jellyfin: another
 * tab's identity change, permission change, sign-out, and disablement, sign-in and
 * sign-out, account switching in one browser, permission refresh, revoked sessions,
 * first Jellyfin sign-in, and two concurrent contexts. Needs the owner created by
 * `authenticate`.
 */
export async function verifyBrowserSessions(run, owner) {
  const { root, manifest } = run;
  const load = createRequire(path.join(root, "web/package.json"));
  const { chromium, expect, request } = load("@playwright/test");
  const { default: AxeBuilder } = load("@axe-core/playwright");
  const origin = manifest.origin;
  const steps = [];
  const accessibility = [];
  const contexts = [];
  const apiPaths = [];
  const step = (name, detail = {}) => { steps.push({ name, ...detail }); run.write("sessions-steps.json", steps); };
  const secret = () => { const value = randomBytes(24).toString("base64url"); run.secrets.add(value); return value; };

  const admin = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin } });
  let csrf = null;
  const send = async (method, url, data) => {
    const response = await admin.fetch(url, { method, data, headers: csrf && method !== "GET" ? { "X-CSRF-Token": csrf } : {} });
    return { status: response.status(), body: await response.json().catch(() => null) };
  };
  const signedIn = await send("POST", SIGN_IN_PATH, { username: owner.username, password: owner.password });
  assert.equal(signedIn.status, 200);
  csrf = signedIn.body.csrf_token;
  run.secrets.add(csrf);
  const fresh = { reauthentication: { password: owner.password } };
  const alpha = await admitJellyfinUser(run, "journey-alpha");
  const beta = await admitJellyfinUser(run, "journey-beta");
  step("journey-alpha and journey-beta were created on the run's Jellyfin and admitted by their first Jellyfin sign-in");

  async function open(width) {
    const pageId = contexts.length + 1;
    const context = await run.browser.newContext({ viewport: { width, height: 900 } });
    await context.addInitScript(installRecorder);
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort("blockedbyclient"));
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const failures = [];
    page.on("pageerror", error => failures.push(error.message));
    page.on("request", item => {
      const url = new URL(item.url());
      if (url.pathname.startsWith("/api/")) apiPaths.push({ width, page: pageId, method: item.method(), path: url.pathname });
    });
    contexts.push({ context, width, failures });
    return { context, page, pageId };
  }
  const seen = page => page.evaluate(() => [...window.__editarrSeen]);
  const resetSeen = page => page.evaluate(() => window.__editarrSeen.clear());
  const storageKeys = page => page.evaluate(() => Object.keys(window.localStorage).sort());
  async function axe(page, name) {
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    accessibility.push({ name, violations: result.violations.map(item => ({ id: item.id, nodes: item.nodes.length })) });
    run.write("sessions-axe.json", accessibility);
    assert.deepEqual(result.violations.map(item => item.id), [], `${name} has axe violations`);
  }
  async function signIn(page, username, password) {
    await fillSignIn(page, username, password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
  }
  const refreshPermissions = page => page.evaluate(() => window.dispatchEvent(new Event("focus")));
  const signedInAs = (page, name) => expect(accountMenuButton(page, name)).toBeVisible();
  const cookies = async context => { for (const cookie of await context.cookies()) run.secrets.add(cookie.value); };

  try {
    for (const width of [1440, 320]) {
      const first = await admitJellyfinUser(run, `tabs-first-${width}`);
      const second = await admitJellyfinUser(run, `tabs-second-${width}`);
      const { page: original, context } = await open(width);
      await original.goto(origin);
      await signIn(original, first.username, first.password);
      await signedInAs(original, first.username);
      await capture(original, run, `sessions-tabs-before-switch-${width}`);
      const other = await context.newPage();
      await other.goto(origin);
      await signedInAs(other, first.username);
      await signOut(other);
      await signIn(other, second.username, second.password);
      await signedInAs(other, second.username);
      await refreshPermissions(original);
      await signedInAs(original, second.username);
      await axe(original, `tabs-identity-change-${width}`);
      await capture(original, run, `sessions-tabs-after-switch-${width}`);
      await resetSeen(original);
      assert.equal((await send("PUT", `/api/users/${second.id}/role`, { role: "admin", ...fresh })).status, 200);
      await refreshPermissions(original);
      await expect(original.getByRole("button", { name: "Connections", exact: true })).toBeVisible();
      await capture(original, run, `sessions-tabs-revision-${width}`);
      await signOut(other);
      await expect(other.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
      await refreshPermissions(original);
      await expect(original.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
      assert.equal(await original.locator('input[type="password"]').inputValue(), "");
      await signIn(original, first.username, first.password);
      await signedInAs(original, first.username);
      assert.equal((await send("PUT", `/api/users/${first.id}/status`, { status: "disabled" })).status, 200);
      await refreshPermissions(original);
      await expect(original.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
      assert.equal(await original.locator('input[type="password"]').inputValue(), "");
      await capture(original, run, `sessions-tabs-disabled-${width}`);
      await other.close();
      step("an open tab followed another tab's identity change, permission change, sign-out, and the account's disablement", { width });
    }

    const { page, pageId } = await open(1440);
    const pathsFromPage = start => apiPaths.slice(start).filter(item => item.page === pageId).map(item => `${item.method} ${item.path}`);
    await page.goto(`${origin}/#/settings/connections`);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    await signIn(page, alpha.username, "not-the-password-for-this-account");
    await expect(page.getByRole("alert")).toHaveText("Unable to sign in with those credentials. Please try again.");
    step("failed sign-in shows the generic refusal and stays on sign-in");

    const beforeSignIn = apiPaths.length;
    await signIn(page, alpha.username, alpha.password);
    await signedInAs(page, alpha.username);
    const signInPaths = pathsFromPage(beforeSignIn);
    assert.equal(signInPaths[0], `POST ${SIGN_IN_PATH}`, `sign-in made ${signInPaths.join(", ")}`);
    step("journey-alpha signed in with its Jellyfin password and returned to the requested deep link", { signInPaths });

    await expect(page.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
    const hidden = (await seen(page)).filter(text => HIDDEN_FROM_USERS.includes(text));
    assert.deepEqual(hidden, [], "an ordinary user saw admin-only navigation or content");
    await axe(page, "denied-deep-link-1440");
    await capture(page, run, "sessions-denied-link-1440");
    step("ordinary user's deep link to Connections shows Page unavailable and never rendered admin-only controls");

    await page.goto(`${origin}/#/library/journey-library`);
    await expect.poll(() => storageKeys(page)).toContain(`editarr.user.${alpha.id}.library-last-route.journey-library`);
    await page.goto(`${origin}/#/settings/general`);
    await page.reload();
    await signedInAs(page, alpha.username);
    assert.ok((await storageKeys(page)).includes(`editarr.user.${alpha.id}.library-last-route.journey-library`), "reload kept the same account's storage");
    step("reload kept journey-alpha signed in with its own keyed storage");

    await resetSeen(page);
    assert.equal((await send("PUT", `/api/users/${alpha.id}/role`, { role: "admin", ...fresh })).status, 200);
    await refreshPermissions(page);
    await expect(page.getByRole("button", { name: "Connections", exact: true })).toBeVisible();
    assert.ok(!(await storageKeys(page)).some(key => key.startsWith(`editarr.user.${alpha.id}.`)), "a permission revision cleared stored values");
    assert.equal((await send("PUT", `/api/users/${alpha.id}/role`, { role: "user", ...fresh })).status, 200);
    await refreshPermissions(page);
    await expect(page.getByRole("button", { name: "Connections", exact: true })).toHaveCount(0);
    await page.goto(`${origin}/#/settings/connections`);
    await expect(page.getByRole("heading", { name: "Page unavailable", exact: true })).toBeVisible();
    step("promotion showed Connections and demotion hid it again and refused the deep link");

    await page.goto(`${origin}/#/settings/general`);
    assert.equal((await send("DELETE", `/api/users/${alpha.id}/sessions`)).status, 200);
    await refreshPermissions(page);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    assert.deepEqual((await storageKeys(page)).filter(key => key.startsWith("editarr.user")), [], "revocation cleared stored values");
    step("revoking journey-alpha's sessions returned the open page to sign-in and cleared storage");

    await signIn(page, beta.username, beta.password);
    await signedInAs(page, beta.username);
    assert.equal(new URL(page.url()).hash, "#/settings/general");
    const body = await page.locator("body").innerText();
    assert.ok(!body.includes(alpha.username), "journey-beta saw journey-alpha's name");
    assert.ok(!(await storageKeys(page)).some(key => key.startsWith(`editarr.user.${alpha.id}.`)), "journey-beta's browser holds journey-alpha's values");
    await capture(page, run, "sessions-switched-1440");
    step("journey-beta signed in on the same browser without journey-alpha's route, name, or storage");

    const { page: small } = await open(320);
    await small.goto(`${origin}/`);
    await expect(small.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    assert.equal(await small.getByRole("button", { name: "Create an account", exact: true }).count(), 0, "sign-in offers no registration");
    const gamma = await addJellyfinUser(run, "journey-gamma");
    await signIn(small, gamma.username, gamma.password);
    await signedInAs(small, gamma.username);
    await axe(small, "signed-in-320");
    await capture(small, run, "sessions-first-jellyfin-sign-in-320");
    step("a Jellyfin user with no Editarr account signed in at 320px and got one; sign-in offers no registration");

    await page.goto(`${origin}/#/library/beta-library`);
    await small.goto(`${origin}/#/library/gamma-library`);
    await expect.poll(() => storageKeys(page)).toContain(`editarr.user.${beta.id}.library-last-route.beta-library`);
    await expect.poll(async () => (await storageKeys(small)).some(key => /^editarr\.user\.\d+\.library-last-route\.gamma-library$/.test(key))).toBe(true);
    assert.ok(!(await storageKeys(page)).some(key => key.includes("gamma-library")), "contexts share storage");
    assert.ok(!(await storageKeys(small)).some(key => key.includes("beta-library")), "contexts share storage");
    await page.reload();
    await small.reload();
    await signedInAs(page, beta.username);
    await signedInAs(small, gamma.username);
    step("two concurrent contexts kept journey-beta and journey-gamma and their storage apart across reloads");

    const throttles = [];
    for (const [targetPage, width] of [[page, 1440], [small, 320]]) {
      await targetPage.goto(`${origin}/#/settings/general`);
      await targetPage.getByRole("button", { name: "Generate API key", exact: true }).click();
      const dialog = targetPage.getByRole("dialog");
      const wrong = secret();
      await capture(targetPage, run, `sessions-fresh-before-throttle-${width}`);
      const attempts = [];
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await dialog.getByLabel("Jellyfin password", { exact: true }).fill(wrong);
        const responsePromise = targetPage.waitForResponse(response => new URL(response.url()).pathname === "/api/auth/api-key" && response.request().method() === "POST");
        await dialog.getByRole("button", { name: "Generate key", exact: true }).click();
        const response = await responsePromise;
        const body = await response.json();
        attempts.push({ status: response.status(), code: body.code, retryAfter: response.headers()["retry-after"] ?? null });
        await expect(dialog.getByLabel("Jellyfin password", { exact: true })).toHaveValue("");
        if (body.code === "reauthentication_throttled") break;
        assert.equal(body.code, "reauthentication_failed");
      }
      const last = attempts.at(-1);
      assert.equal(last.status, 429);
      assert.equal(last.code, "reauthentication_throttled");
      assert.match(last.retryAfter ?? "", /^[1-9]\d*$/);
      await expect(dialog.getByRole("alert")).toContainText(/Try again in \d+ (?:seconds?|minutes?)\./);
      await expect(dialog.getByRole("button", { name: "Generate key", exact: true })).toBeDisabled();
      await axe(targetPage, `fresh-throttle-${width}`);
      await capture(targetPage, run, `sessions-fresh-throttled-${width}`);
      throttles.push({ width, attempts, submitDisabled: true, passwordCleared: true });
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    }
    run.write("sessions-throttles.json", throttles);
    step("real reauthentication throttles carry Retry-After, clear passwords, and disable submit with the remaining wait", { viewports: [1440, 320] });

    await page.goto(`${origin}/#/settings/general`);
    await signOut(page);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    assert.deepEqual((await storageKeys(page)).filter(key => key.startsWith("editarr.user")), []);
    await signedInAs(small, gamma.username);
    step("signing journey-beta out cleared its storage and left journey-gamma's context signed in");

    for (const { context, failures } of contexts) {
      await cookies(context);
      assert.deepEqual(failures, [], "browser raised an uncaught error");
    }
    run.write("sessions-result.json", {
      feature: "mu-web-sessions", passed: true, viewports: [1440, 320], accounts: [alpha.id, beta.id, gamma.username],
      signInRequests: signInPaths, accessibility,
      scope: "Real verification-mode source app and browser; every account is a user of the run's Jellyfin 12 admitted by Jellyfin sign-in; no mocks.",
    });
  } finally {
    run.write("sessions-api-requests.json", apiPaths);
    for (const { context } of contexts) {
      await cookies(context).catch(() => {});
      await context.close();
    }
    await admin.dispose();
  }
}
