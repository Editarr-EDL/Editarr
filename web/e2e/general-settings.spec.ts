import { expect, test, type Page } from "@playwright/test";
import manifest from "../package.json" with { type: "json" };
import type { DeploymentConfiguration } from "../src/api/api-system-types";
import {
  authenticationStatus, expectNoAccessibilityViolations, expectNoHorizontalPageOverflow, json, mockApi,
} from "./app-fixtures";

const release = manifest.version;
const titles = ["Deployment", "macOS", "Backup and restore", "Base path mappings", "Diagnostics", "About Editarr"];
const summary = (page: Page, title: string) => page.locator("summary").filter({ hasText: title });

async function mockGeneralSettings(page: Page) {
  await mockApi(page);
  const active = {
    base_url: "http://editarr.example.test/", public_origin: "http://editarr.example.test",
    prefix: "", listening_port: 3351, base_url_source: "local_default", port_source: "default", cli_port_override: null,
  } as const;
  let deployment: DeploymentConfiguration = {
    active, next_start: active, saved: { base_url: null, port: null }, restart_required: false,
    restart: {
      supported: true, mode: "standalone_controller", generation: 1, in_progress: false,
      outcome: "idle", target_generation: null, reason: null, detail: null,
    }, macos_dock_supported: true,
  };
  const deploymentWrites: unknown[] = [];
  const dockWrites: unknown[] = [];
  const logTails: string[] = [];
  let showDock = true;
  let debugLogging = false;
  await page.route("**/api/settings/deployment", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      deploymentWrites.push(body);
      deployment = { ...deployment, saved: { base_url: null, port: body.port }, restart_required: true,
        next_start: { ...active, listening_port: body.port, port_source: "saved" } };
    }
    return json(route, deployment);
  });
  await page.route("**/api/settings/macos/dock", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      showDock = body.show_in_dock;
      dockWrites.push(body);
    }
    return json(route, { show_in_dock: showDock });
  });
  await page.route("**/api/backups/schedule", (route) => json(route, {
    config: null, next_run: null, last_attempt: null, last_success: null, outcome: "idle", error: null,
    retained_count: 0, restore_hold: false, notice: null,
  }));
  await page.route("**/api/system/logs/settings", (route) => {
    if (route.request().method() === "PUT") debugLogging = route.request().postDataJSON().debug_enabled;
    return json(route, { debug_enabled: debugLogging });
  });
  await page.route(/\/api\/system\/logs\/(?:application|http|worker|controller)\?/, (route) => {
    const channel = new URL(route.request().url()).pathname.split("/").at(-1)!;
    logTails.push(channel);
    return json(route, { channel, lines: [`Synthetic ${channel} log line`], truncated: false });
  });
  await page.route("**/api/system/version", (route) => json(route, {
    release, build_revision: "1234567", build_dirty: false, scanner_behavior: "1", database_schema: "2",
    local_api: "3", community_api: null, artifact_schema: null,
  }));
  return { deploymentWrites, dockWrites, logTails };
}

test("compact General shows saved summaries and keeps deployment drafts when navigating with the keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const { deploymentWrites, logTails } = await mockGeneralSettings(page);
  await page.goto("/#/settings/general");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "General", exact: true })).toBeVisible();
  const rows = main.locator("details[name='general-settings'] > summary");
  await expect(rows).toHaveCount(titles.length);
  for (const [index, title] of titles.entries()) await expect(rows.nth(index)).toContainText(title);
  const deployment = summary(page, "Deployment");
  const paths = summary(page, "Base path mappings");
  await expect(deployment).toContainText("Port 3351");
  await expect(deployment).toHaveAccessibleName(/Deployment.*Port 3351/);
  await expect(summary(page, "macOS")).toContainText("Dock icon shown");
  await expect(summary(page, "Backup and restore")).toContainText(/schedule.*(?:off|disabled)/i);
  await expect(paths).toContainText(/(?:0 mappings|no mappings)/i);
  await expect(summary(page, "Diagnostics")).toContainText(/debug logging (?:off|disabled)/i);
  await expect(summary(page, "About Editarr")).toContainText(release);
  const port = main.getByRole("textbox", { name: /^Listening Port/ });
  const connectionDetails = summary(page, "Connection details");
  const active = main.getByRole("region", { name: "Active now" });
  const next = main.getByRole("region", { name: "Next start" });
  const activeConnection = main.getByRole("region", { name: "Active connection" });
  const remotePath = main.getByRole("textbox", { name: "Remote path", exact: true });
  await expect(port).toHaveValue("3351");
  for (const snapshot of [active, next]) {
    await expect(snapshot.getByText("http://editarr.example.test/", { exact: true })).toBeVisible();
    await expect(snapshot).toContainText("3351");
  }
  await expect(activeConnection).toBeHidden();
  await connectionDetails.press("Enter");
  await expect(activeConnection).toContainText("Public origin");
  await expect(activeConnection).toContainText("http://editarr.example.test");
  await expect(remotePath).toBeHidden();
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  await connectionDetails.press("Space");
  await expect(activeConnection).toBeHidden();
  expect(logTails).toEqual([]);

  await port.fill("4100");
  await paths.focus();
  await paths.press("Enter");
  await expect(paths).toBeFocused();
  await expect(port).toBeHidden();
  const addMapping = main.getByRole("button", { name: "Add mapping", exact: true });
  await expect(addMapping).toHaveAttribute("aria-expanded", "false");
  await expect(remotePath).toBeHidden();
  await addMapping.press("Enter");
  await expect(addMapping).toHaveAttribute("aria-expanded", "true");
  await expect(remotePath).toBeVisible();
  await expect(main.getByText(/You have unsaved changes/)).toBeVisible();
  await expect(deployment).toContainText("Port 3351");
  expect(deploymentWrites).toEqual([]);
  await remotePath.fill("/unsaved/remote");
  await addMapping.press("Space");
  await expect(remotePath).toBeHidden();
  await addMapping.press("Enter");
  await expect(remotePath).toHaveValue("/unsaved/remote");
  await deployment.focus();
  await deployment.press("Space");
  await expect(deployment).toBeFocused();
  await expect(remotePath).toBeHidden();
  await expect(port).toHaveValue("4100");
  await main.getByRole("button", { name: "Save deployment settings" }).click();
  await expect(next).toContainText("4100");
  await expect(active).toContainText("3351");
  await paths.press("Enter");
  await expect(remotePath).toHaveValue("/unsaved/remote");
  await expect(deployment).toContainText(/restart required/i);
  await expect(deployment).toHaveAccessibleName(/Deployment.*restart required/i);
  await expect(deployment).toContainText("Port 3351");
  await expect(main.getByRole("button", { name: "Restart Editarr" })).toBeVisible();
  await expectNoHorizontalPageOverflow(page);
  expect(deploymentWrites).toEqual([{ port: 4100 }]);
});

test("updates the Dock and diagnostic summaries and loads log lines on demand", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { dockWrites, logTails } = await mockGeneralSettings(page);
  await page.goto("/#/settings/general");
  const macos = summary(page, "macOS");
  await expect(macos).toContainText("Dock icon shown");
  const dock = page.getByRole("checkbox", { name: "Show app icon in the Dock" });
  await expect(dock).toBeHidden();
  await macos.click();
  await expect(dock).toBeChecked();
  await dock.click();
  await expect(dock).not.toBeChecked();
  await expect(macos).toContainText("Dock icon hidden");
  await macos.click();
  await expect(dock).toBeHidden();
  await expect(macos).toContainText("Dock icon hidden");
  expect(dockWrites).toEqual([{ show_in_dock: false }]);

  const diagnostics = summary(page, "Diagnostics");
  await expect(diagnostics).toContainText(/debug logging (?:off|disabled)/i);
  expect(logTails).toEqual([]);
  await diagnostics.click();
  const viewLogs = page.getByRole("button", { name: "View logs" });
  await expect(viewLogs).toBeVisible();
  await expect(viewLogs).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("combobox", { name: "Log source" })).toBeHidden();
  expect(logTails).toEqual([]);
  await viewLogs.click();
  await expect(page.getByText("Synthetic application log line")).toBeVisible();
  await page.getByRole("combobox", { name: "Log source" }).selectOption("worker");
  await expect(page.getByText("Synthetic worker log line")).toBeVisible();
  const debugLogging = page.getByRole("checkbox", { name: "Enable debug logging" });
  await debugLogging.click();
  await expect(debugLogging).toBeChecked();
  await expect(diagnostics).toContainText(/debug logging (?:on|enabled)/i);
  await expectNoHorizontalPageOverflow(page);
  await expectNoAccessibilityViolations(page);
  await page.getByRole("button", { name: "Hide logs" }).press("Space");
  await expect(viewLogs).toBeVisible();
  await expect(page.getByText("Synthetic worker log line")).toBeHidden();
  await diagnostics.click();
  await expect(page.getByText("Synthetic worker log line")).toBeHidden();
  await expect(diagnostics).toContainText(/debug logging (?:on|enabled)/i);
  expect(logTails).toContain("application");
  expect(logTails).toContain("worker");
  await expectNoAccessibilityViolations(page);
});

test("does not expose closed operational summaries to an ordinary account", async ({ page }) => {
  await mockGeneralSettings(page);
  const account = authenticationStatus("authenticated");
  account.access!.principal.role = "user";
  account.access!.capabilities = ["profile.view.own", "system.version.view"];
  await page.route("**/api/auth/status", (route) => json(route, account));
  const operations: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (/^\/api\/(?:settings\/(?:deployment|macos|path-mappings)|system\/(?:logs|restart)|backups)/.test(pathname)) {
      operations.push(pathname);
    }
  });
  await page.goto("/#/settings/general");
  const rows = page.getByRole("main").locator("details[name='general-settings'] > summary");
  await expect(rows).toHaveCount(1);
  const about = summary(page, "About Editarr");
  await expect(about).toContainText(release);
  await expect(page.getByRole("button", { name: "Restart Editarr" })).toHaveCount(0);
  const scannerBehavior = page.getByText("Scanner behavior", { exact: true });
  await expect(page.getByText("1234567", { exact: true })).toBeVisible();
  await expect(scannerBehavior).toBeHidden();
  const buildDetails = summary(page, "Build details");
  await buildDetails.press("Enter");
  await expect(scannerBehavior).toBeVisible();
  await about.press("Space");
  await expect(scannerBehavior).toBeHidden();
  await expect(about).toContainText(release);
  await about.press("Enter");
  await expect(scannerBehavior).toBeVisible();
  expect(operations).toEqual([]);
});
