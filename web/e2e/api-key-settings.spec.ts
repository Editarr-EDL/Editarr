import { expect, test } from "@playwright/test";
import { mockApi } from "./app-fixtures";

for (const width of [375, 1280]) {
  test(`${width}px API key generation, password-confirmed reveal, and rotation warning`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page);
    let configured = false;
    const confirmations: unknown[] = [];
    const secret = { api_key: "a".repeat(64), created_at: "2026-10-13T12:00:00Z" };
    await page.route("**/api/auth/api-key**", async (route) => {
      const request = route.request();
      if (request.method() === "GET") {
        await route.fulfill({ json: { configured, created_at: configured ? secret.created_at : null, revealable: configured } });
        return;
      }
      confirmations.push(request.postDataJSON());
      configured = true;
      await route.fulfill({ json: secret });
    });
    await page.route("**/api/users/1", (route) => route.fulfill({ json: {
      id: 1, username: null, display_name: "Owner", source: "jellyfin", role: "owner", status: "active", disable_cause: null,
      avatar: null, last_login_at: null, created_at: "2026-10-24T12:00:00Z",
      overrides: { permissions: false, media_access: false, filter_policy: false },
      language: "en",
      read_only_fields: ["username", "display_name", "avatar"], available_languages: ["en"], link_cleanup: null, successor_refusal: null,
    } }));
    await page.goto("/#/settings/profile");
    const profile = page.getByRole("region", { name: "Profile details" });
    await expect(profile.getByText("Jellyfin account", { exact: true })).toBeVisible();
    await expect(profile.getByText("Jellyfin username", { exact: true })).toHaveCount(0);
    await expect(profile.getByText("Avatar from Jellyfin", { exact: true })).toHaveCount(0);
    await expect(profile.getByRole("button", { name: "Save profile" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Jellyfin link" })).toHaveCount(0);
    const panel = page.getByRole("region", { name: "API Key", exact: true });
    await panel.getByRole("button", { name: "Generate API key", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Generate API key?" });
    await dialog.getByLabel("Jellyfin password").fill("synthetic password");
    await dialog.getByRole("button", { name: "Generate key" }).click();
    const key = panel.getByLabel("Current API key");
    await expect(key).toHaveValue("a".repeat(64));
    await page.reload();
    await expect(key).toHaveCount(0);
    await panel.getByRole("button", { name: "Show API key" }).click();
    const reveal = page.getByRole("dialog", { name: "Show API key?" });
    await reveal.getByLabel("Jellyfin password").fill("synthetic password");
    await reveal.getByRole("button", { name: "Show key" }).click();
    await expect(key).toHaveValue("a".repeat(64));
    expect(confirmations).toEqual([
      { reauthentication: { password: "synthetic password" } },
      { reauthentication: { password: "synthetic password" } },
    ]);
    const overflow = await panel.evaluate((element) => {
      const parent = element.getBoundingClientRect();
      return Array.from(element.querySelectorAll("button, input")).some((control) => {
        const bounds = control.getBoundingClientRect();
        return bounds.left < parent.left || bounds.right > parent.right;
      });
    });
    expect(overflow).toBe(false);
    await panel.getByRole("button", { name: "Generate new API key" }).click();
    await expect(page.getByRole("dialog", { name: "Replace API key?" })).toContainText("stop working immediately");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await panel.screenshot({ path: `/tmp/editarr-api-key-${width}.png` });
  });
}
