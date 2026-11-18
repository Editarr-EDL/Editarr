import { expect, test } from "@playwright/test";
import type { CommunityConnection, CommunityRecoveryStatus } from "../src/api/api-community";
import { expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi } from "./app-fixtures";

for (const width of [320, 1440]) {
  test(`${width}px lost-key confirmation, pending reload and replacement reconciliation`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    const identity = { installationId: "00000000-0000-4000-8000-000000000001", keyId: "sha256:" + "a".repeat(64), contributorId: "contributor-001" };
    const candidate = "sha256:" + "c".repeat(64);
    let recovery: CommunityRecoveryStatus = { configured: true, serviceUrl: "https://community.test", identity, pending: null, completed: null };
    let starts = 0; let sends = 0; let checks = 0; let retries = 0;
    await page.route(/\/api\/community\/.*/, async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "POST") expect(request.headers()["x-csrf-token"]).toBe("test-csrf-token");
      if (path.endsWith("/keys/recovery/start")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true, old_key_id: identity.keyId }); starts++;
        recovery = { ...recovery, pending: { operationId: "saved-recovery", stateId: null, oldKeyId: identity.keyId, newKeyId: candidate },
          authorizationUrl: "https://community.test/v1/discord/recovery-start?confirmationHash=public-hash" };
      }
      if (path.endsWith("/keys/recovery/complete")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true, state_id: "b".repeat(64) }); sends++;
        recovery = { ...recovery, pending: { ...recovery.pending!, stateId: "b".repeat(64) }, authorizationUrl: undefined };
        await json(route, { code: "network_unavailable", retryable: true }, 503); return;
      }
      if (path.endsWith("/keys/recovery/check")) { checks++; recovery = { ...recovery, recoveryStatus: "not_committed" }; }
      if (path.endsWith("/keys/recovery/retry")) {
        expect(request.postDataJSON()).toEqual({ confirmed: true }); retries++;
        recovery = { ...recovery, identity: { ...identity, keyId: candidate }, completed: recovery.pending,
          pending: null, recoveryStatus: undefined };
      }
      if (path.includes("/keys/recovery")) { await json(route, recovery); return; }
      if (!recovery.completed && path.endsWith("/connection/status")) {
        await json(route, {
          configured: true, connected: true, pending: null, profile: null, serviceUrl: recovery.serviceUrl,
          identity: { installationId: identity.installationId, keyId: identity.keyId, lastKnownKeyState: "active" },
          pendingRecovery: Boolean(recovery.pending),
        } satisfies CommunityConnection);
        return;
      }
      if (!recovery.completed) { await json(route, { code: "identity_incomplete", retryable: false }, 409); return; }
      const connection: CommunityConnection = { configured: true, connected: true, pending: null, serviceUrl: "https://community.test",
        identity: { installationId: identity.installationId, keyId: candidate, lastKnownKeyState: "active" },
        profile: { contributorId: identity.contributorId, shortId: "contribu", installationId: identity.installationId,
          keyId: candidate, keyState: "active", displayName: "Cedar Frost Grove", profileRevision: 1,
          policyVersion: "test", protocolVersion: 1, allowedOperations: ["identity:read", "identity:rename"]} };
      await json(route, connection);
    });
    await page.goto("/#/settings/connections");
    const panel = page.getByRole("region", { name: "Lost-key recovery" });
    const trigger = panel.getByRole("button", { name: "Recover with Discord" });
    await expect(trigger).toBeEnabled();
    await trigger.click();
    const prepare = page.getByRole("dialog", { name: "Authorize identity recovery?" });
    await expect(prepare.getByRole("button", { name: "Cancel" })).toBeFocused();
    await prepare.getByRole("button", { name: "Cancel" }).press("Escape");
    await expect(trigger).toBeFocused(); expect(starts).toBe(0);
    await trigger.click(); await page.getByRole("button", { name: "Prepare recovery" }).click();
    await expect(panel.getByRole("link", { name: "Authorize recovery with Discord" })).toBeVisible();
    await panel.getByLabel("Discord recovery URL or reference").fill("https://community.test/v1/discord/callback?state=" + "b".repeat(64) + "&code=private-provider-code");
    await expect(panel.getByLabel("Discord recovery URL or reference")).toHaveValue("b".repeat(64));
    await panel.getByRole("button", { name: "Review recovery" }).click();
    const confirm = page.getByRole("dialog", { name: "Replace this installation's key?" });
    await expect(confirm).toContainText("Other installations retain access");
    await expectNoAccessibilityViolations(page); await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css33-confirm-${width}.png`, fullPage: true });
    await confirm.getByRole("button", { name: "Cancel" }).click(); expect(sends).toBe(0);
    await panel.getByRole("button", { name: "Review recovery" }).click();
    await confirm.getByRole("button", { name: "Confirm recovery" }).click();
    await expect(panel.getByRole("alert")).toContainText("service is unreachable");
    await page.reload(); await expect(panel.getByRole("button", { name: "Check recovery" })).toBeEnabled();
    expect(starts).toBe(1); expect(sends).toBe(1); expect(checks).toBe(0); expect(retries).toBe(0);
    await expectNoAccessibilityViolations(page); await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css33-pending-${width}.png`, fullPage: true });
    await panel.getByRole("button", { name: "Check recovery" }).click();
    await expect(panel.getByText(/service has not committed/)).toBeVisible();
    await panel.getByRole("button", { name: "Retry saved recovery" }).click();
    await page.getByRole("button", { name: "Confirm recovery" }).click();
    await expect(panel.getByText(/Identity recovery activated/)).toBeVisible();
    await expect(panel.getByText(/no account-transfer bypass/)).toBeVisible();
    const exposed = await page.evaluate(() => document.body.innerHTML + JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
    for (const secret of ["candidatePrivateKey", "confirmationSecret", "private-provider-code", "private_key"]) expect(exposed).not.toContain(secret);
    await expectNoAccessibilityViolations(page); await expectNoHorizontalPageOverflow(page);
    await page.screenshot({ path: `test-results/css33-completed-${width}.png`, fullPage: true });
    await panel.getByRole("link", { name: "Create identity backup" }).click();
    await page.getByRole("button", { name: "Create backup", exact: true }).click();
    await expect(page.getByLabel("Encrypt backup")).not.toBeChecked();
  });
}
