import { expect, test } from "@playwright/test";
import { authenticationStatus, expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json } from "./app-fixtures";

for (const width of [320, 1440]) {
  for (const encrypted of [false, true]) {
    for (const included of [false, true]) {
      test(`${width}px ${encrypted ? "encrypted" : "plain"} setup restore ${included ? "sign-in" : "create-admin"}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        let restored = false;
        let job: Record<string, unknown> = { id: "setup-job", action: "restore", kind: "full", state: "waiting", plan: null, error: null, reason: null, inventory: null, report: null };
        await page.route("**/api/auth/status", async (route) => {
          await json(route, authenticationStatus(restored ? (included ? "unauthenticated" : "setup_required") : "setup_required"));
        });
        await page.route(/\/api\/setup-restore\/(?:flow|jobs(?:\/.*)?)$/, async (route) => {
          const request = route.request();
          const path = new URL(request.url()).pathname;
          if (path.endsWith("/flow")) return json(route, { token: "setup-flow-token" });
          if (request.method() === "POST" && path.endsWith("/jobs")) {
            expect(request.headers()["x-setup-restore-token"]).toBe("setup-flow-token");
            return json(route, job);
          }
          if (request.method() === "PUT") job = { ...job, state: "uploaded" };
          if (path.endsWith("/preflight")) {
            expect((request.postDataJSON() as { passphrase: string | null }).passphrase).toBe(encrypted ? "archive-secret" : null);
            job = { ...job, state: "ready", plan: {
              kind: "full", protection: encrypted ? "age-scrypt" : "plain", administrator_included: included,
              source_identity: null, account_identities: 1, included_secret_types: [], omitted_environment: [],
              backup_deployment: { base_url: included ? "https://source.example/editarr" : null, port: included ? 9443 : null },
            } };
          }
          if (path.endsWith("/activate")) {
            expect((request.postDataJSON() as { confirmed: boolean }).confirmed).toBe(true);
            job = { ...job, state: "activating", plan: null };
            restored = true;
          }
          if (request.method() === "GET" && job.state === "activating") job = { ...job, state: "activated", report: { move_disagreements: 0 } };
          await json(route, job);
        });
        await page.goto("/#/settings/general");
        await expect(page.getByRole("heading", { name: "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
        await page.getByRole("button", { name: "Restore from backup" }).click();
        await expect(page.getByRole("heading", { name: "RESTORE FROM BACKUP" })).toBeVisible();
        const restore = page.locator(".backup-settings");
        await restore.getByLabel("Backup file").setInputFiles({
          name: "archive.backup", mimeType: "application/octet-stream",
          buffer: Buffer.from(encrypted ? "age-encryption.org/v1\n" : "manifest.json\n"),
        });
        if (encrypted) await restore.getByLabel("Backup passphrase").fill("archive-secret");
        else await expect(restore.getByLabel("Backup passphrase")).toHaveCount(0);
        await restore.getByRole("button", { name: "Inspect backup" }).click();
        await expect(restore.getByRole("heading", { name: "Restore preview" })).toBeVisible();
        await expect(restore.getByText(included ? /Sign in as the restored owner/ : /Connect a Jellyfin server after restore/)).toBeVisible();
        await expect(restore.getByLabel(/Replace/)).toHaveCount(0);
        await restore.getByLabel(/Restore this backup into this new installation/).check();
        await expectNoHorizontalPageOverflow(page);
        await expectNoAccessibilityViolations(page);
        const activate = restore.getByRole("button", { name: "Restore backup" });
        await activate.focus();
        await expect(activate).toBeFocused();
        await activate.press("Enter");
        await expect(page.getByRole("heading", { name: "Restore complete" })).toBeVisible();
        await expect(page.getByText(/Review media paths and source connections/)).toBeVisible();
        await expect(page.getByText(included ? /backup used base URL https:\/\/source\.example\/editarr and port 9443/ : /backup used base URL default and port default/)).toBeVisible();
        await expectNoAccessibilityViolations(page);
        await page.getByRole("button", { name: "Continue" }).click();
        await expect(page.getByRole("heading", { name: included ? "SIGN IN TO CONTINUE" : "CONNECT YOUR JELLYFIN SERVER" })).toBeVisible();
        expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain("archive-secret");
      });
    }
  }
  test(`${width}px setup restore names why a non-empty installation is refused`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/auth/status", (route) => json(route, authenticationStatus("setup_required")));
    await page.route("**/api/setup-restore/flow", (route) => route.fulfill({
      status: 409, contentType: "application/json",
      body: JSON.stringify({ detail: { code: "restore_target_has_work", message: "This installation already has data in scans" } }),
    }));
    await page.goto("/#/settings/general");
    await page.getByRole("button", { name: "Restore from backup" }).click();
    await expect(page.getByRole("alert")).toContainText("already has scans, reviews, or settings");
    await expect(page.getByLabel("Backup file")).toBeDisabled();
    await expectNoHorizontalPageOverflow(page);
    await expectNoAccessibilityViolations(page);
  });
}
