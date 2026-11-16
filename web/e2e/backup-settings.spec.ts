import { expect, test } from "@playwright/test";
import {
  expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi,
} from "./app-fixtures";
import type { BackupScheduleConfig, BackupScheduleStatus } from "../src/api/api-backup-schedule";

for (const width of [320, 1440]) {
  for (const encrypted of [false, true]) {
    test(`${width}px scheduled ${encrypted ? "encrypted" : "plain"} backups`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await mockApi(page);
      let status: BackupScheduleStatus = {
        config: null, next_run: null, last_attempt: null, last_success: null,
        outcome: "idle", error: null, retained_count: 0, restore_hold: false,
      };
      const writes: Record<string, unknown>[] = [];
      await page.route("**/api/backups/schedule", async (route) => {
        if (route.request().method() === "PUT") {
          const body = route.request().postDataJSON();
          writes.push(body);
          expect(body.confirmed).toBe(true);
          const { confirmed: _confirmed, ...config } = body;
          status = { ...status, config: config as BackupScheduleConfig, next_run: 1900003600 };
        } else if (route.request().method() === "DELETE") {
          status = { ...status, config: { ...status.config!, enabled: false }, next_run: null };
        }
        await json(route, status);
      });
      await page.goto("/#/settings/general");
      const schedule = page.getByRole("region", { name: "Scheduled backups" });
      await expect(schedule.getByText("Disabled", { exact: true })).toBeVisible();
      expect(writes).toHaveLength(0);
      await expect(schedule.getByLabel("Enable scheduled backups")).not.toBeChecked();
      await expect(schedule.getByRole("button", { name: "Save schedule" })).toBeDisabled();
      await schedule.getByLabel("Enable scheduled backups").focus();
      await schedule.getByLabel("Enable scheduled backups").press("Space");
      await schedule.getByLabel("Interval (hours)").fill("24");
      await schedule.getByLabel("Backups to keep").fill("7");
      await schedule.getByLabel("Backup destination").fill("/private/independent-backups/long-directory-name-for-responsive-verification");
      await expect(schedule.getByLabel("Scheduled backup contents")).toHaveCount(0);
      if (encrypted) {
        await schedule.getByLabel("Encrypt scheduled backups").check();
        await expect(schedule.getByRole("button", { name: "Save schedule" })).toBeDisabled();
        await schedule.getByLabel("Protected passphrase file").fill("/private/operator-credentials/scheduled-backup.passphrase");
      } else {
        await expect(schedule.getByLabel("Protected passphrase file")).toHaveCount(0);
      }
      await expect(schedule.locator('input[type="password"]')).toHaveCount(0);
      for (const label of ["Enable scheduled backups", "Encrypt scheduled backups"]) {
        await expect(schedule.getByText(label, { exact: true })).toBeVisible();
        const bounds = await schedule.getByLabel(label).boundingBox();
        expect(bounds?.width).toBe(16);
        expect(bounds?.height).toBe(16);
      }
      await expectNoHorizontalPageOverflow(page);
      await expectNoAccessibilityViolations(page);
      const save = schedule.getByRole("button", { name: "Save schedule" });
      await save.focus();
      await expect(save).toBeFocused();
      await save.press("Enter");
      await expect(schedule.getByText("Backup schedule saved.")).toBeVisible();
      expect(writes).toHaveLength(1);
      expect(writes[0]).toMatchObject({ enabled: true, interval_hours: 24, keep_count: 7,
        kind: "full", protection: encrypted ? "age-scrypt" : "plain",
        passphrase_file: encrypted ? "/private/operator-credentials/scheduled-backup.passphrase" : null });
      expect(writes[0]).not.toHaveProperty("passphrase");
      await page.reload();
      await expect(schedule.getByLabel("Enable scheduled backups")).toBeChecked();
      expect(writes).toHaveLength(1);
      status = { ...status, outcome: "failed", error: encrypted ? "credential_unavailable" : "export_failed",
        last_attempt: 1900000000, last_success: 1899990000, retained_count: 7 };
      await schedule.getByRole("button", { name: "Refresh status" }).click();
      await expect(schedule.getByText("Failed", { exact: true })).toBeVisible();
      await expect(schedule.getByText(encrypted ? /No unencrypted fallback/ : /Previous backups were retained/)).toBeVisible();
      await expectNoHorizontalPageOverflow(page);
      await expectNoAccessibilityViolations(page);
      await schedule.screenshot({ path: testInfo.outputPath("scheduled-backups.png") });
      await schedule.getByRole("button", { name: "Disable schedule" }).click();
      await expect(schedule.getByText("Scheduled backups disabled.")).toBeVisible();
      expect(status.config?.enabled).toBe(false);
      expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain("operator-credentials");
    });
  }
  for (const encrypted of [false, true]) {
    test(`${width}px ${encrypted ? "encrypted" : "plain"} full backup export`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await mockApi(page);
      await page.route("**/api/backups/schedule", (route) => json(route, {
        config: null, next_run: null, last_attempt: null, last_success: null,
        outcome: "idle", error: null, retained_count: 0, restore_hold: false, notice: null,
      }));
      const bodies: Record<string, unknown>[] = [];
      await page.route(/\/api\/backups\/jobs(?:\/.*)?$/, async (route) => {
        const request = route.request();
        if (request.method() === "POST") bodies.push(request.postDataJSON() as Record<string, unknown>);
        if (request.method() === "HEAD") return route.fulfill({ status: 200 });
        await json(route, {
          id: "job-1", action: "export", kind: "full", state: request.method() === "DELETE" ? "cancelled" : "ready",
          error: null, reason: null, plan: null, report: null,
          inventory: { protection: encrypted ? "age-scrypt" : "plain", included_secret_types: ["community-signing-key", "database-local-auth"], omitted_environment: [], identity_included: true },
        });
      });
      await page.goto("/#/settings/general");
      const backup = page.locator(".backup-settings").first();
      await expect(backup.getByRole("heading", { name: "Backup and restore" })).toBeVisible();
      await expect(backup.getByLabel("Backup contents", { exact: true })).toHaveCount(0);
      await expect(backup.getByLabel("Backup file")).toHaveCount(0);
      await expect(backup.getByRole("button", { name: "Inspect backup" })).toHaveCount(0);
      if (encrypted) {
        await backup.getByLabel("Encrypt backup").check();
        await backup.getByLabel("Passphrase", { exact: true }).fill("test-passphrase");
        await backup.getByLabel("Confirm passphrase").fill("test-passphrase");
      } else {
        await expect(backup.getByLabel("Passphrase", { exact: true })).toHaveCount(0);
      }
      await backup.getByRole("button", { name: "Create backup" }).click();
      await expect(backup.getByRole("button", { name: "Download backup" })).toBeVisible();
      await expect(backup.getByText(/Community identity included/)).toBeVisible();
      expect(bodies).toEqual([encrypted ? { action: "export", encrypt: true, passphrase: "test-passphrase" } : { action: "export" }]);
      await expectNoHorizontalPageOverflow(page);
      await expectNoAccessibilityViolations(page);
      expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain("test-passphrase");
    });
  }
}
