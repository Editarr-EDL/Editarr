import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { signOutThroughMenu } from "./app-fixtures";
import net from "node:net";

const baseURL = process.env.EDITARR_PRODUCTION_BASE_URL!;
const initialPrefix = process.env.EDITARR_PRODUCTION_PREFIX ?? "";
const secondaryBaseURL = process.env.EDITARR_PRODUCTION_SECONDARY_BASE_URL!;
const controlPort = Number(process.env.EDITARR_PRODUCTION_CONTROL_PORT);
const initialUpstreamPort = Number(process.env.EDITARR_PRODUCTION_UPSTREAM_PORT);
const jellyfin = {
  url: process.env.EDITARR_PRODUCTION_JELLYFIN_URL!,
  username: process.env.EDITARR_PRODUCTION_JELLYFIN_USERNAME!,
  password: process.env.EDITARR_PRODUCTION_JELLYFIN_PASSWORD!,
};

function fixtureControl(action: Record<string, unknown>): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const connection = net.createConnection({ host: "127.0.0.1", port: controlPort });
    let response = "";
    connection.setTimeout(15_000);
    connection.on("connect", () => connection.end(`${JSON.stringify(action)}\n`));
    connection.on("data", (chunk) => { response += chunk; });
    connection.on("timeout", () => reject(new Error("Fixture control timed out.")));
    connection.on("error", reject);
    connection.on("end", () => {
      try {
        const result = JSON.parse(response) as Record<string, unknown>;
        if (result.ok !== true) throw new Error(String(result.error));
        resolve(result);
      } catch (error) { reject(error); }
    });
  });
}

async function setup(page: Page) {
  await page.goto(`${initialPrefix}/`);
  await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
  await page.getByLabel("Jellyfin server URL", { exact: true }).fill(jellyfin.url);
  await page.getByLabel("Jellyfin username", { exact: true }).fill(jellyfin.username);
  await page.getByLabel("Jellyfin password", { exact: true }).fill(jellyfin.password);
  await page.getByRole("button", { name: "Connect and sign in" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
}

async function signIn(page: Page) {
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  await page.locator("#administrator-login-username").fill(jellyfin.username);
  await page.locator("#administrator-login-password").fill(jellyfin.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
}

async function openGeneralSettings(page: Page, prefix = "") {
  await page.goto(`${prefix}/#/settings/general`);
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.locator(".general-settings-page-header").getByRole("button", { name: "Restart Editarr" })).toBeVisible();
}

test("installed artifact serves real HTTPS Jellyfin setup, settings, prefix restart, and logout", async ({ page, context }) => {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().startsWith(baseURL)) requests.push(new URL(request.url()).pathname);
  });

  await setup(page);
  await openGeneralSettings(page, initialPrefix);
  await expect(new AxeBuilder({ page }).include(".general-settings-page-header").analyze()).resolves.toMatchObject({ violations: [] });

  const initialBaseUrl = `${baseURL}${initialPrefix}`;
  const cookie = (await context.cookies(initialBaseUrl)).find(({ name }) => name === "editarr_session");
  expect(cookie).toMatchObject({ secure: true, httpOnly: true, path: initialPrefix || "/", sameSite: "Lax" });
  await expect(page.locator("input[name=deployment_base_url]")).toHaveValue(initialBaseUrl);

  // Browser-originated requests prove the protected endpoint still enforces both
  // source and CSRF checks; the production app itself receives every request.
  const csrfAndOrigin = await page.evaluate(async (prefix) => {
    const api = `${prefix}/api/settings/deployment`;
    const missingCsrf = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: "{}" });
    const badOrigin = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json", Origin: "https://evil.example", "X-CSRF-Token": "invalid" }, body: "{}" });
    return [missingCsrf.status, badOrigin.status];
  }, initialPrefix);
  expect(csrfAndOrigin).toEqual([403, 403]);

  const fixtureMedia = await fixtureControl({ action: "fixture_media" });
  const mediaResponses = await page.evaluate(async ({ prefix, reviewId, filterId }) => {
    const artwork = await fetch(`${prefix}/api/library-items/fixture-item/image`);
    const preview = await fetch(`${prefix}/api/reviews/${reviewId}/filters/${filterId}/mute-preview`);
    return [artwork.status, artwork.headers.get("content-type"), preview.status, preview.headers.get("content-type")];
  }, { prefix: initialPrefix, reviewId: fixtureMedia.review_id, filterId: fixtureMedia.filter_id });
  const backendLog = await fixtureControl({ action: "backend_log" });
  expect(mediaResponses, String(backendLog.log)).toEqual([200, "image/png", 200, "audio/mpeg"]);

  const newUpstreamPort = initialUpstreamPort + 1;
  const nextPrefix = initialPrefix ? `${initialPrefix}/nested` : "/editarr";
  await page.locator("input[name=deployment_base_url]").fill(`${baseURL}${nextPrefix}`);
  await page.locator("input[name=deployment_port]").fill(String(newUpstreamPort));
  await page.getByRole("button", { name: "Save deployment settings" }).click();
  await expect(page.getByText("Restart required to apply the next-start settings.")).toBeVisible();
  // Save is staged: the live root listener and mount still serve this authenticated page.
  await expect(page).toHaveURL(new RegExp(`${baseURL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}${initialPrefix}/#`));

  const restart = page.locator(".general-settings-page-header").getByRole("button", { name: "Restart Editarr" });
  await restart.click();
  await expect(restart).toBeDisabled();
  await expect(page.getByText("Editarr is restarting")).toBeVisible();
  await fixtureControl({ action: "reconfigure", prefix: nextPrefix, upstream_port: newUpstreamPort });
  await expect(page).toHaveURL(new RegExp(`${baseURL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}${nextPrefix}/#/settings/general`));
  const prefixRequestStart = requests.length;

  await signIn(page);
  await openGeneralSettings(page, nextPrefix);
  const afterPrefixCookie = (await context.cookies(`${baseURL}${nextPrefix}`)).find(({ name }) => name === "editarr_session");
  expect(afterPrefixCookie).toMatchObject({ secure: true, httpOnly: true, path: nextPrefix, sameSite: "Lax" });

  const settledRestart = page.getByRole("button", { name: "Restart Editarr" });
  // The no-pending restart targets the current URL. Reconnecting must still load a
  // fresh General Settings document rather than leave the pre-restart page showing.
  const reconnected = page.waitForEvent("load", { timeout: 15_000 });
  await settledRestart.click();
  await reconnected;
  await expect(page).toHaveURL(new RegExp(`${nextPrefix}/#/settings/general`));
  await expect(page.getByText("Restart complete. Reconnecting to General Settings")).toBeHidden();
  await expect(settledRestart).toBeEnabled();

  // Cross-origin transition: the UI must not credential-poll the second origin.
  // It exposes the exact server target; the test-owned second proxy is configured
  // only after the 202 acknowledgement, then the browser follows that real link.
  await page.locator("input[name=deployment_base_url]").fill(`${secondaryBaseURL}/cross`);
  await page.getByRole("button", { name: "Save deployment settings" }).click();
  await expect(page.getByText("Restart required to apply the next-start settings.")).toBeVisible();
  await page.getByRole("button", { name: "Restart Editarr" }).click();
  await expect(page.getByText("Restart accepted at a different public address")).toBeVisible();
  await fixtureControl({ action: "reconfigure_secondary", prefix: "/cross", upstream_port: newUpstreamPort });
  await page.getByRole("link", { name: "Open restart target" }).click();
  await expect(page).toHaveURL(new RegExp(`${secondaryBaseURL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/cross/#/settings/general`));
  await signIn(page);
  await signOutThroughMenu(page);
  await expect(page.getByRole("heading", { name: "SIGN IN TO CONTINUE" })).toBeVisible();
  // Requests made after activation must retain the configured mount.  Root traffic
  // above is intentionally permitted while the old root configuration is active.
  expect(requests.slice(prefixRequestStart).every((path) => path.startsWith(`${nextPrefix}/`))).toBeTruthy();
});
