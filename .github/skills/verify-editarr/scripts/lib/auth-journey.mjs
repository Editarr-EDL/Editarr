import assert from "node:assert/strict";
import { accountMenuButton, signOut } from "./account-menu.mjs";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { capture as captureMasked, MASK_COLOR } from "./capture.mjs";
import { launchBrowser } from "./instance.mjs";
import { FIXED_TIME, installFixedClock } from "./fixtures.mjs";
import { fillSignIn, startRunJellyfin, submitJellyfinSetup } from "./run-jellyfin.mjs";

export const INJECTED_FAILURE = "Injected failure after credential entry";

/** Open the run's origin in a fresh browser context and require the Jellyfin setup screen. */
export async function expectSetupScreen(run) {
  const { expect } = createRequire(path.join(run.root, "web/package.json"))("@playwright/test");
  const context = await (await launchBrowser(run)).newContext({ viewport: { width: 1440, height: 900 } });
  try {
    const page = await context.newPage();
    await page.goto(`${run.manifest.origin}/`);
    await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER", exact: true })).toBeVisible({ timeout: 20000 });
  } finally {
    await context.close();
  }
}

/**
 * Drive first-run setup through the run's Jellyfin 12 administrator, then sign-out
 * and fresh-context Jellyfin sign-in through the real UI. The owner it returns
 * signs in with Jellyfin (`jellyfin: true`). `fixedClock` pins browser Date in the
 * sign-in context. `injectFailure` stops after credentials are typed, for cleanup
 * and redaction drills. `libraries` starts Jellyfin with the synthetic libraries
 * and the restricted users the identity journey reads.
 */
export async function authenticate(run, { fixedClock = false, injectFailure = false, libraries = false } = {}) {
  const { root, evidence, manifest } = run;
  const { expect } = createRequire(path.join(root, "web/package.json"))("@playwright/test");
  const jellyfin = await startRunJellyfin(run, { libraries });
  const { name: username, password } = jellyfin.admin;
  const actions = [];
  const requests = [];
  const externalRequests = [];
  const failures = [];
  const contexts = [];
  let context;
  let page;
  await launchBrowser(run);
  const action = value => { actions.push({ at: new Date().toISOString(), action: value }); run.write("actions.json", actions); };
  async function open(width, { clock = false } = {}) {
    context = await run.browser.newContext({ viewport: { width, height: 900 } });
    contexts.push({ viewport: width, initialCookies: (await context.cookies()).length, fixedClock: clock ? FIXED_TIME : null });
    if (clock) await installFixedClock(context);
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== manifest.origin) {
        externalRequests.push({ origin: url.origin, method: route.request().method() });
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on("pageerror", error => failures.push(error.message));
    page.on("response", response => requests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() }));
    await page.goto(`${manifest.origin}/#/settings/general`);
  }
  async function close() {
    const cookies = await context.cookies();
    for (const cookie of cookies) run.secrets.add(cookie.value);
    contexts.at(-1).cookieNames = cookies.map(cookie => cookie.name);
    await context.close();
  }
  async function authenticatedStatus() {
    const body = await (await context.request.get(`${manifest.origin}/api/auth/status`)).json();
    if (body.csrf_token) run.secrets.add(body.csrf_token);
    return body.state;
  }
  const capture = name => captureMasked(page, run, name);
  try {
    await open(1440);
    const seeded = Boolean(run.legacyMembers);
    let before;
    if (seeded) {
      await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
      await capture("auth-seeded-sign-in-1440");
      before = await context.request.get(`${manifest.origin}/api/libraries`);
      assert.equal(before.status(), 401);
      action("Verified the seeded installation's sign-in screen and protected libraries HTTP 401 at 1440px.");
      action("Sign in as the seeded owner, linked to the run's Jellyfin administrator (credentials not recorded).");
      await fillSignIn(page, username, password);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
    } else {
      await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Restore from backup", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Use Quick Connect", exact: true })).toHaveCount(0);
      assert.equal((await context.request.get(`${manifest.origin}/api/auth/jellyfin/quick-connect`)).status(), 423, "setup offers no Quick Connect");
      await capture("auth-setup-1440");
      before = await context.request.get(`${manifest.origin}/api/libraries`);
      assert.equal(before.status(), 423);
      action("Verified the Jellyfin setup screen and protected libraries HTTP 423 at 1440px.");
      if (injectFailure) {
        await page.getByLabel("Jellyfin server URL", { exact: true }).fill(jellyfin.base);
        await page.getByLabel("Jellyfin username", { exact: true }).fill(username);
        await page.getByLabel("Jellyfin password", { exact: true }).fill(password);
        throw new Error(INJECTED_FAILURE);
      }
      action("Submit Jellyfin setup with the run's disposable Jellyfin administrator (credentials not recorded).");
      await submitJellyfinSetup(page, jellyfin);
    }
    await expect(page.getByRole("heading", { name: "General", exact: true })).toBeVisible();
    await expect(accountMenuButton(page)).toBeVisible();
    assert.equal(await authenticatedStatus(), "authenticated");
    const owner = await (await context.request.get(`${manifest.origin}/api/auth/status`)).json();
    assert.equal(owner.access.principal.role, "owner");
    assert.equal(owner.jellyfin_user_id.replaceAll("-", ""), jellyfin.admin.id.replaceAll("-", "").toLowerCase());
    const keys = await jellyfin.editarrKeys();
    assert.equal(keys.length, 1, "Jellyfin lists exactly one Editarr key after setup");
    const versionResponse = await context.request.get(`${manifest.origin}/api/system/version`);
    assert.equal(versionResponse.status(), 200);
    const version = await versionResponse.json();
    const release = JSON.parse(fs.readFileSync(path.join(root, "web/package.json"), "utf8")).version;
    assert.equal(version.release, release);
    run.write("version.json", version);
    await capture("auth-created-1440");
    action("Verified the owner is linked to the Jellyfin administrator, one Editarr key exists, and General settings and version load.");
    await signOut(page);
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    const signedOut = await context.request.get(`${manifest.origin}/api/libraries`);
    assert.equal(signedOut.status(), 401);
    await capture("auth-signed-out-1440");
    action("Sign out and verify protected libraries HTTP 401.");
    await close();
    await open(320, { clock: fixedClock });
    if (fixedClock) {
      assert.equal(await page.evaluate(() => new Date().toISOString()), FIXED_TIME, "Browser clock is not fixed");
      action(`Fixed the sign-in context's browser clock at ${FIXED_TIME}.`);
    }
    await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE", exact: true })).toBeVisible();
    await capture("auth-login-320");
    await fillSignIn(page, username, password);
    action("Sign in with Jellyfin at 320px in a fresh browser context as the persisted owner.");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "General", exact: true })).toBeVisible();
    await expect(page).toHaveURL(`${manifest.origin}/#/settings/general`);
    assert.equal(await authenticatedStatus(), "authenticated");
    const libraries = await context.request.get(`${manifest.origin}/api/libraries`);
    assert.equal(libraries.status(), 200, "the catalog loads through Editarr's minted key");
    const folders = (await jellyfin.call("GET", "/Library/VirtualFolders")).filter(folder => folder.ItemId && folder.Name);
    assert.equal((await libraries.json()).length, folders.length, "Editarr lists every Jellyfin library");
    const persistedVersion = await context.request.get(`${manifest.origin}/api/system/version`);
    assert.equal(persistedVersion.status(), 200);
    assert.equal((await persistedVersion.json()).release, release);
    await capture("auth-persisted-320");
    await close();
    assert.equal(failures.length, 0, "Browser raised an uncaught error");
    assert.equal(externalRequests.length, 0, "Unexpected browser requests outside the owned origin");
    assert.ok(contexts.every(record => record.initialCookies === 0), "A browser context started with cookies");
    const databaseBytes = fs.statSync(path.join(run.data, "scanner.db")).size;
    assert.ok(databaseBytes > 0);
    run.write("result.json", {
      feature: "auth", passed: true, viewports: [1440, 320], persistedAdministrator: true,
      seededInstallation: seeded, protectedLibraries: { [seeded ? "beforeSignIn" : "beforeSetup"]: before.status(), afterSignOut: 401, afterFreshLogin: 200 },
      jellyfin: { image: jellyfin.image, editarrKeys: keys.length, ownerLinked: true },
      protectedVersionAfterFreshLogin: persistedVersion.status(),
      release: version.release, databaseBytes, contexts, fixedBrowserClock: fixedClock ? FIXED_TIME : null,
      externalBrowserRequests: externalRequests, backendOutboundTraffic: "not measured",
      scope: "Real source-development app and database; no API mocks, models, external providers, TLS, installed release, or native shell certification.",
    });
    action("Verified persisted administrator in a new context, protected API access, and nonempty scratch database.");
    return { username, password, jellyfin: true };
  } catch (error) {
    if (page && !page.isClosed()) {
      await page.screenshot({ path: path.join(evidence, "failure.png"), fullPage: true, mask: [page.locator("input")], maskColor: MASK_COLOR }).catch(() => {});
    }
    throw error;
  } finally {
    run.write("http-statuses.json", requests);
    run.write("browser-errors.json", failures);
    run.write("contexts.json", contexts);
    for (const cookie of await context?.cookies().catch(() => []) ?? []) run.secrets.add(cookie.value);
    await context?.close();
  }
}
