import { expect, test } from "@playwright/test";
import { discordPage } from "../../community-service/src/integrations/discord/pages";
import { authenticationStatus, expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";
import type { CommunityConnection } from "../src/api/api-community";

const stateId = "b".repeat(64);
const identity = { installationId: "00000000-0000-4000-8000-000000000001", keyId: "sha256:" + "a".repeat(64), lastKnownKeyState: "unenrolled" };
const profile = {
  contributorId: "contributor-001", shortId: "contribu", installationId: identity.installationId,
  keyId: identity.keyId, keyState: "active" as const, displayName: "Cedar Frost Grove",
  profileRevision: 1, policyVersion: "test-v1", protocolVersion: 1 as const,
  allowedOperations: ["identity:read", "identity:rename"] as ("identity:read" | "identity:rename")[]};

for (const width of [320, 1440]) {
  test(`${width}px hosted handoff, durable retry, profile and backup access`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    let connection: CommunityConnection = { configured: true, serviceUrl: "https://community.test", identity, connected: false, profile: null, pending: null };
    let completionAttempts = 0;
    let renameAttempts = 0;
    let renameOperation = "";
    let authorized = false;
    await page.route(/\/api\/community\/.*/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() !== "GET") expect(request.headers()["x-csrf-token"]).toBe("test-csrf-token");
      if (path.endsWith("/enrollment/start")) {
        connection = { ...connection, pending: { stateId: null, expiresAt: 1900000000, awaitingConfirmation: true },
          authorizationUrl: `https://community.test/v1/discord/start?installationId=${identity.installationId}&keyId=${identity.keyId}&publicKey=public-key&confirmationHash=public-hash` };
      } else if (path.endsWith("/enrollment/complete")) {
        expect(authorized).toBe(true);
        expect(request.postDataJSON()).toEqual({ state_id: stateId, confirmed: true });
        completionAttempts++;
        connection = { ...connection, pending: { stateId, expiresAt: 1900000000, awaitingConfirmation: true } };
        if (completionAttempts === 1) {
          await json(route, { code: "network_unavailable", retryable: true }, 503);
          return;
        }
        connection = { ...connection, identity: { ...identity, lastKnownKeyState: "active" }, connected: true, profile, pending: null, authorizationUrl: undefined };
      } else if (path.endsWith("/profile") && request.method() === "PATCH") {
        const body = request.postDataJSON() as { display_name: string; operation_id: string };
        renameAttempts++;
        if (renameAttempts === 1) renameOperation = body.operation_id;
        expect(body.operation_id).toBe(renameOperation);
        connection = { ...connection, pendingRename: { operation_id: body.operation_id, display_name: body.display_name } };
        if (renameAttempts === 1) {
          await json(route, { code: "service_unavailable", retryable: true }, 503);
          return;
        }
        connection = { ...connection, profile: { ...profile, displayName: body.display_name, profileRevision: 2 }, pendingRename: null };
      }
      await json(route, connection);
    });

    await context.route("https://community.test/**", async (route) => {
      const url = new URL(route.request().url());
      const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
      if (url.pathname.endsWith("/start")) {
        expect(url.searchParams.get("confirmationHash")).toBe("public-hash");
        expect(url.searchParams.has("confirmationSecret")).toBe(false);
        const response = await discordPage({ stateId, installationId: identity.installationId, keyId: identity.keyId }, headers);
        await route.fulfill({ headers: Object.fromEntries(response.headers), body: await response.text() });
      } else if (url.pathname.endsWith("/confirm") && route.request().method() === "POST") {
        authorized = true;
        const response = await discordPage(null, headers, stateId);
        await route.fulfill({ headers: Object.fromEntries(response.headers), body: await response.text() });
      } else {
        const response = await discordPage(url.pathname.endsWith("/callback") ? null : { stateId, installationId: identity.installationId, keyId: identity.keyId }, headers, authorized ? stateId : undefined);
        await route.fulfill({ status: response.status, headers: Object.fromEntries(response.headers), body: await response.text() });
      }
    });

    await page.goto("/#/settings/connections");
    const panel = page.getByRole("region", { name: "Community scans" });
    const backup = panel.getByRole("link", { name: "Backup and restore" });
    await expect(backup).toBeVisible();
    await panel.getByRole("button", { name: "Connect with Discord" }).click();
    const popupPromise = page.waitForEvent("popup");
    await panel.getByRole("link", { name: "Continue with Discord" }).click();
    const hosted = await popupPromise;
    await hosted.getByRole("button", { name: "Continue with Discord" }).click();
    await expect(hosted.getByRole("heading", { name: "Discord verified" })).toBeVisible();
    const reference = await hosted.locator("dd code").textContent();
    expect(reference).toBe(stateId);
    expect(completionAttempts).toBe(0);
    await hosted.close();
    await panel.getByLabel("Discord confirmation URL or enrollment reference").fill(reference!);
    await panel.getByRole("button", { name: "Review connection" }).click();
    const modal = page.getByRole("dialog", { name: "Connect this installation?" });
    await expect(modal.getByRole("button", { name: "Back" })).toBeFocused();
    await modal.getByRole("button", { name: "Back" }).press("Tab");
    await expect(modal.getByRole("button", { name: "Confirm connection" })).toBeFocused();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css30-confirm-${width}.png`, fullPage: true });
    await modal.getByRole("button", { name: "Confirm connection" }).press("Escape");
    await expect(panel.getByRole("button", { name: "Review connection" })).toBeFocused();
    expect(completionAttempts).toBe(0);
    await panel.getByRole("button", { name: "Review connection" }).click();
    await page.getByRole("button", { name: "Confirm connection" }).click();
    await expect(panel.getByRole("alert")).toContainText("service is unreachable");
    await page.reload();
    await panel.getByRole("button", { name: "Retry confirmation" }).click();
    await page.getByRole("button", { name: "Confirm connection" }).click();
    await expect(panel.getByRole("status")).toHaveText("Connected");
    expect(completionAttempts).toBe(2);
    await expect(panel.getByText("contributor-001", { exact: true })).toBeVisible();
    await expect(panel.getByRole("heading", { name: "Contributor profile" })).toBeVisible();
    await panel.getByLabel("Contributor name").fill("<b>Public Name</b>");
    await panel.getByRole("button", { name: "Save name" }).click();
    await expect(panel.getByRole("alert")).toContainText("temporarily unavailable");
    await page.reload();
    const retryName = panel.getByRole("button", { name: "Retry name change" });
    await expect(retryName).toBeEnabled();
    await retryName.click();
    await expect(panel.getByLabel("Contributor name")).toHaveValue("<b>Public Name</b>");
    await expect(panel.locator("b")).toHaveCount(0);
    expect(renameAttempts).toBe(2);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css30-connected-${width}.png`, fullPage: true });
    const stored = await page.evaluate(() => document.body.innerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
    for (const forbidden of ["confirmationSecret", "private_key", "access_token", "discordId"]) expect(stored).not.toContain(forbidden);
    await backup.click();
    await expect(page.getByRole("heading", { name: "Backup and restore" })).toBeVisible();
    await expect(page.getByLabel("Encrypt backup")).toBeHidden();
    await page.getByRole("button", { name: "Create backup", exact: true }).click();
    await expect(page.getByLabel("Encrypt backup")).not.toBeChecked();
  });

  test(`${width}px disabled community leaves local workflows available`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    await page.route(/\/api\/community\/.*/, (route) => json(route, { configured: false, connected: false, profile: null, pending: null }));
    await page.goto("/#/settings/connections");
    const panel = page.getByRole("region", { name: "Community scans" });
    await expect(panel.getByText(/Local scans and reviews remain available/)).toBeVisible();
    await expect(panel.getByRole("button", { name: "Connect with Discord" })).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
    await page.goto("/#/library/movies");
    await expect(page.getByRole("heading", { name: "Movies", exact: true })).toBeVisible();
  });
}
