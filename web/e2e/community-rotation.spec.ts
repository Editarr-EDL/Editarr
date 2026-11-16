import { expect, test } from "@playwright/test";
import type { CommunityConnection } from "../src/api/api-community";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";

for (const width of [320, 1440]) {
  test(`${width}px explicit rotation, cancellation, response loss and saved recovery`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    const identity = { installationId: "00000000-0000-4000-8000-000000000001", keyId: "sha256:" + "a".repeat(64), lastKnownKeyState: "active" };
    const replacement = "sha256:" + "b".repeat(64);
    let connection: CommunityConnection = { configured: true, connected: true, serviceUrl: "https://community.test", identity, pending: null,
      profile: { contributorId: "contributor-001", shortId: "contribu", installationId: identity.installationId,
        keyId: identity.keyId, keyState: "active", displayName: "Cedar Frost Grove", profileRevision: 1,
        policyVersion: "test", protocolVersion: 1, allowedOperations: ["identity:read", "identity:rename"]} };
    let sends = 0;
    let probes = 0;
    let retries = 0;
    await page.route(/\/api\/community\/.*/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST") expect(request.headers()["x-csrf-token"]).toBe("test-csrf-token");
      if (path.endsWith("/keys/rotate")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true });
        sends++;
        connection = { ...connection, pendingRotation: { operationId: "saved-rotation-1", newKeyId: replacement, state: "pending" } };
        await json(route, { code: "network_unavailable", retryable: true }, 503); return;
      }
      if (path.endsWith("/keys/rotation-status")) {
        probes++;
        connection = { ...connection, rotationStatus: "not_committed" };
      }
      if (path.endsWith("/keys/rotation-retry")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true });
        retries++;
        connection = { ...connection, identity: { ...identity, keyId: replacement }, profile: { ...connection.profile!, keyId: replacement },
          pendingRotation: null, rotationCompleted: { operationId: "saved-rotation-1", keyId: replacement, committedAt: 1700000030000 }, rotationStatus: undefined };
      }
      await json(route, connection);
    });
    await page.goto("/#/settings/connections");
    const panel = page.getByRole("region", { name: "Community scans" });
    const trigger = panel.getByRole("button", { name: "Rotate key" });
    await expect(trigger).toBeEnabled();
    expect(sends).toBe(0);
    await trigger.click();
    const modal = page.getByRole("dialog", { name: "Rotate this installation's key?" });
    await expect(modal.getByRole("button", { name: "Cancel" })).toBeFocused();
    await expect(modal).toContainText("Other separately registered installations retain access");
    await expectNoHorizontalPageOverflow(page); await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css32-confirm-${width}.png`, fullPage: true });
    await modal.getByRole("button", { name: "Cancel" }).press("Escape");
    await expect(trigger).toBeFocused();
    expect(sends).toBe(0);
    await trigger.click();
    await modal.getByRole("button", { name: "Confirm rotation" }).click();
    await expect(panel.getByRole("alert")).toContainText("service is unreachable");
    await page.reload();
    await expect(panel.getByRole("button", { name: "Check rotation" })).toBeEnabled();
    expect(sends).toBe(1); expect(probes).toBe(0); expect(retries).toBe(0);
    await expect(panel.getByLabel("Contributor name")).toBeDisabled();
    await expect(panel.getByText("saved-rotation-1")).toBeVisible();
    await expectNoHorizontalPageOverflow(page); await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css32-pending-${width}.png`, fullPage: true });
    await panel.getByRole("button", { name: "Check rotation" }).click();
    await expect(panel.getByText(/service has not committed/)).toBeVisible();
    await panel.getByRole("button", { name: "Retry saved rotation" }).click();
    await page.getByRole("button", { name: "Confirm retry" }).click();
    await expect(panel.getByText(/Key rotation activated/)).toBeVisible();
    expect(sends).toBe(1); expect(retries).toBe(1); expect(probes).toBe(1);
    await page.reload();
    await expect(panel.getByText(/Backup freshness has not been verified/)).toBeVisible();
    const exposed = await page.evaluate(() => document.body.innerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
    for (const secret of ["candidatePrivateKey", "oldProof", "newProof", "private_key"]) expect(exposed).not.toContain(secret);
    await expectNoHorizontalPageOverflow(page); await expectNoAccessibilityViolations(page);
    await page.screenshot({ path: `test-results/css32-completed-${width}.png`, fullPage: true });
    await panel.getByRole("link", { name: "Backup and restore" }).click();
    await expect(page.getByLabel("Encrypt backup")).not.toBeChecked();
  });
}